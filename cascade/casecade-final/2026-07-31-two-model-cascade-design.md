# Design: FAST→HAC two-model cascade

**Date:** 2026-07-31  
**Status:** implemented on branch `cascade-fast-hac` (v1 mean-Q router)  
**Goal:** Cut wall time vs full-HAC by running **FAST on every read**, then **HAC only on hard reads**, while keeping accuracy close to full-HAC (tunable).

## First shower run (Orin, 2026-07-31)

Flags: `-C 128`, overlap, narrow, `--flush-threshold=64`, `--cascade-threshold=10.0`

| Mode | Real time | Peak RAM | Notes |
|------|----------:|---------:|-------|
| FAST-only | 10.7 s | 2.56 GB | |
| **Cascade** | **18.1 s** | 4.44 GB | kept_fast=832, promoted_hac=168 (**16.8%**) |
| HAC-only | 49.1 s | 3.08 GB | |

Cascade ≈ **2.7× faster than HAC-only** at thr=10 on this 1k set. Accuracy vs HAC not measured yet (minimap next). Log: `docs-riley/cascade_1k_thr10.tsv`, full console `docs-riley/cascade_shower_run_*.txt`.

## One-sentence pitch

Use FAST as a cheap scout; promote only the suspicious fraction to HAC — same models you already ship, new routing policy.

## Why this can win (your numbers)

Rough Orin costs (depth-1, flush=64, C=128, from depth2 A/B suite):

| Config | ~Real time |
|--------|------------|
| FAST 1k | ~9.8 s |
| HAC 1k | ~49.3 s |
| FAST 20k | ~173 s |

HAC ≈ **5×** FAST on 1k. If fraction `f` of reads need HAC:

```text
T_cascade ≈ T_FAST_all + f × T_HAC_all
         ≈ T_FAST × (1 + 5f)     (order-of-magnitude)
```

Examples vs full HAC (`≈ 5 × T_FAST`):

| hard fraction f | cascade vs full HAC |
|----------------:|---------------------|
| 20% | ~0.4× (≈ **2.5× faster**) |
| 40% | ~0.6× (≈ **1.7× faster**) |
| 80% | ~0.96× (almost no win) |

Win depends entirely on **how often FAST is “good enough.”** That is an empirical knob, not a hope.

## Non-goals (v1)

- Not TensorRT / custom CUDA LSTM (separate track B)
- Not training a new router network
- Not changing openfish beam maths
- Not dual-GPU
- Not replacing FAST-only or HAC-only modes (cascade is an *extra* mode)

## Architecture

```text
                    ┌─────────────────────┐
   reads.blow5 ───► │ Stage A: FAST path  │  (existing runners, overlap ok)
                    │  preprocess → infer │
                    │  → decode → stitch  │
                    └─────────┬───────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │ Router (per read)   │
                    │  score = mean Q, …  │
                    │  if score >= thr    │──► keep FAST seq/q  → output
                    │  else               │──► mark HARD
                    └─────────┬───────────┘
                              │ HARD reads only
                              ▼
                    ┌─────────────────────┐
                    │ Stage B: HAC path   │  (second model / runners)
                    │  same chunks/signal │
                    │  overwrite seq/q    │
                    └─────────┬───────────┘
                              ▼
                           FASTQ out
                      (+ cascade stats)
```

### Recommended v1 integration shape

**Offline two-pass inside one `slorado` invocation** (simplest correctness story):

1. Load **both** models (FAST + HAC) → two runner sets (or reload between passes if VRAM tight).
2. Pass A: basecall the databatch with FAST (current `process_db` path).
3. After `postprocess_signal` (stitched read-level seq/q available), run **router** per read.
4. Collect HARD read indices; Pass B: basecall **only those reads** with HAC (reuse already-preprocessed signal / chunks where possible).
5. Emit FASTQ; tag or log which reads were upgraded.

**Alternative (later):** dual resident runners and route at batch boundaries — more overlap, more memory. Defer until two-pass proves the accuracy/speed curve.

### CLI sketch

```text
--cascade=yes|no              # default no
--cascade-fast MODEL_DIR      # required if cascade
--cascade-hac  MODEL_DIR      # required if cascade
--cascade-metric mean_q       # v1: only mean_q
--cascade-threshold FLOAT     # promote to HAC if mean_q < threshold
--cascade-force-frac FLOAT    # optional: always promote worst X% (calibration aid)
```

Positional `model` can remain the FAST model when cascade is on, or require explicit flags only — pick one in implementation and document it.

**Suggested:** positional model = FAST; `--cascade-hac=` required when `--cascade=yes`.

## Router (v1): mean Q-score

After stitch, for read `i`:

```text
mean_q = average over qstring chars of (ord(q) - 33)   # Phred
hard   = (mean_q < cascade_threshold)
```

Why start here:

- Already available after decode/stitch (no new GPU work)
- Correlates with “this call looks shaky”
- One scalar threshold → easy Pareto sweep

**v1.1 extras** (only if mean_q under-promotes):

- move density / bases-per-signal outlier  
- fraction of q below Q10  
- read length bucket  

Keep the interface: `bool is_hard(read)`; swap metric without changing the pipeline.

## Memory / Jetson notes

- Holding FAST + HAC runners ≈ more VRAM than one model. Orin 8GB-class: **measure**. Fallback: unload FAST module before HAC pass B (slower, safer).
- Reuse `scaled_signal` / chunk metadata; do **not** re-parse blow5 for HARD reads.
- Keep overlap-decode + narrow + flush settings on both passes (same as your sub‑10s FAST recipe unless HAC needs its own `-C`).

## Correctness & evaluation

### Must-have checks

1. **`--cascade=no`** behaviour unchanged vs current binary.  
2. **`--cascade-threshold=-inf`** (or very low): never promote → byte-comparable to FAST-only.  
3. **`--cascade-threshold=+inf`** (or very high): always promote → accuracy ≈ HAC-only (seq may differ only by stitch/RNG-free decode; expect near-identical identity).  
4. Mid threshold: identity vs hg38 between FAST-only and HAC-only; report `% promoted`.

### Bench matrix (when you run it)

Same FAST flags you trust (`-C 128`, overlap, narrow, `--flush-threshold=64`) unless retuned for HAC:

| Mode | What |
|------|------|
| FAST-only | upper speed / lower accuracy bound |
| HAC-only | lower speed / upper accuracy bound |
| Cascade @ thr ∈ {…} | Pareto: wall vs identity vs %HAC |

Primary metrics: `[main] Real time`, median minimap identity, `% reads HAC-upgraded`.

## Failure modes

| Risk | Mitigation |
|------|------------|
| Almost all reads look hard → no speedup | Threshold sweep; report f; abort claim if f>0.7 |
| VRAM OOM with two models | Sequential load; document |
| Router false negatives (keep bad FAST) | Bias threshold toward more HAC; add Q10 fraction |
| Stitch mismatch FAST vs HAC chunking | Same `-c/-p/stride`; identical chunking code path |
| Thesis overclaim | Always quote accuracy + % promoted beside wall |

## Success criteria (experiment)

Cascade is a **win** if on a chosen threshold:

- Wall time **clearly below HAC-only** (target ≥15–20% on 1k or 20k), **and**
- Median identity within **~0.002–0.005** of HAC-only (tune with supervisor), **and**
- Implementation stays flag-gated off by default.

If no threshold meets both speed and accuracy: document as constrained (dataset too hard for FAST scout) — still a valid result.

## Relation to other tracks

| Track | Relation |
|-------|----------|
| Overlap v1.2 / load-imbalance | Keep on; cascade composes with them |
| Depth-2 | Dead; ignore |
| TensorRT / compile (B) | Orthogonal — speeds Stage A and B later |
| Beam turbo | Can apply inside either stage later |

## Open decisions (Riley)

1. VRAM strategy: dual-resident vs unload/reload between passes?  
2. Output: single FASTQ, or also emit `*.cascade.tsv` (read_id, mean_q, model_used)? (recommend yes — tiny, huge for demos)  
3. First accuracy target: match HAC, or “within X of HAC while beating HAC wall”?
