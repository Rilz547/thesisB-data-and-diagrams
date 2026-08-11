# Speculative Decode — Progress Report

**Date:** 2026-08-02 (HAC results 2026-08-03)  
**Branch:** `speculative-decode` (from `load-imbalance`)  
**Platform:** Jetson Orin · DNA R10.4.1 e8.2 400bps @ v5.0.0  
**FAST baseline flags:** `-C 128 -c 12288 -K 4096 -p 150 --overlap-decode=yes --fixed-c-batch=no --flush-threshold=64`  
**HAC baseline flags:** same with `--flush-threshold=128`  
**HAC sweep stamp:** `20260802T121510Z` (`--hac --quick`)

---

## 1. Motivation

On the “best” load-imbalance FAST path, wall time is dominated by **CRF beam decode** (width 32), not the LSTM. The scores tensor is already on device after infer; the expensive step is turning scores into bases.

If a cheap draft is good enough for most chunks, we can skip full beam on the easy majority and only “repair” the hard minority — same idea as speculative execution: guess fast, fix when unsure.

**Goal (speed-first):** lower wall time vs best baseline; small accuracy loss is acceptable. Identity checked with minimap2 on the main PC.

---

## 2. Design

Two-tier decode on the **same** NTC score tensor:

```text
infer → greedy draft (all chunks) → confidence gate → full beam (hard only) → stitch FASTQ
```

Optional **brave** schedule overlaps repair of batch _k_ with infer of batch _k+1_ (scores kept alive until repair finishes).

| Stage      | What                                                                                                             |
| ---------- | ---------------------------------------------------------------------------------------------------------------- |
| **Draft**  | `openfish_decode_gpu_greedy` — width-1 stay/step path (Dorado-style scoring + back-guides)                       |
| **Gate**   | Repair if mean draft Phred Q < `--spec-repair-threshold` **or** mean decision margin < `--spec-margin-threshold` |
| **Repair** | Full `openfish_decode_gpu` beam on the hard subset only                                                          |
| **Brave**  | `--spec-overlap-repair=yes`: `repair(k) ‖ infer(k+1)`                                                            |

### Flags

| Flag                      | Default | Role                            |
| ------------------------- | ------- | ------------------------------- |
| `--speculative-decode`    | no      | enable draft + selective repair |
| `--spec-repair-threshold` | 10.0    | Q gate                          |
| `--spec-margin-threshold` | 2.0     | margin gate (dominates on FAST) |
| `--spec-overlap-repair`   | no      | brave overlap                   |
| `--spec-agreement`        | no      | Phase-0: greedy vs beam compare |

### Phase 0 (go / no-go)

Exact chunk identity greedy == beam was ≈ **0%** (width-1 vs width-32 paths diverge). Drafts were still plausible (similar lengths / related prefixes). We proceeded with **approximate** accept (Q + margin), not bit-identical speculation.

### Harness

- Jetson: `./docs-riley/spec-fastq-sweep --fast` / `--hac [--quick]` — for each config, **time** pass (`-o /dev/null`) then **keep** pass (FASTQ).
- PC: `docs-riley/fetch-and-minimap-spec.py` — pull stamp, minimap2, write `overlapping/spec-decode/<stamp>/`.

---

## 3. FAST results

Wall times from the **time** (`/dev/null`) phase. Accuracy from keep FASTQs vs same-size beam baseline (minimap2, map-ont).

### 3.1 FAST 1k

| tag              | real_s    | α (accept) | map%       | median_id    | Δ vs baseline |
| ---------------- | --------- | ---------- | ---------- | ------------ | ------------- |
| baseline         | 9.827     | —          | 103.00     | 0.940772     | —             |
| q5_m2            | 9.592     | 0.784      | 102.50     | 0.935700     | −0.005072     |
| q8_m2            | 9.568     | 0.770      | 102.80     | 0.935484     | −0.005288     |
| q10_m2           | 9.794     | 0.761      | 103.10     | 0.935347     | −0.005425     |
| q12_m2           | 9.732     | 0.752      | 103.10     | 0.935347     | −0.005425     |
| **q10_m2_brave** | **9.033** | **0.759**  | **103.20** | **0.935274** | **−0.005498** |
| q10_m3           | 11.032    | 0.093      | 103.00     | 0.940565     | −0.000208     |
| q10_m3_brave     | 10.530    | 0.093      | 103.40     | 0.940565     | −0.000208     |

### 3.2 FAST 20k

| tag              | real_s      | α (accept) | map%       | median_id    | Δ vs baseline |
| ---------------- | ----------- | ---------- | ---------- | ------------ | ------------- |
| baseline         | 174.065     | —          | 103.64     | 0.939375     | —             |
| q5_m2            | 168.828     | 0.759      | 103.28     | 0.934974     | −0.004401     |
| q8_m2            | 169.550     | 0.741      | 103.43     | 0.934914     | −0.004461     |
| q10_m2           | 169.719     | 0.730      | 103.56     | 0.934853     | −0.004522     |
| q12_m2           | 168.809     | 0.722      | 103.56     | 0.934843     | −0.004532     |
| **q10_m2_brave** | **157.768** | **0.730**  | **103.59** | **0.934857** | **−0.004518** |
| q10_m3           | 197.333     | 0.069      | 103.58     | 0.939068     | −0.000307     |
| q10_m3_brave     | 188.323     | 0.069      | 103.61     | 0.939051     | −0.000324     |

### 3.3 FAST headline

|           | baseline | **q10_m2_brave** | change                  |
| --------- | -------- | ---------------- | ----------------------- |
| 1k wall   | 9.83 s   | **9.03 s**       | **≈8% faster**          |
| 20k wall  | 174.1 s  | **157.8 s**      | **≈9% faster**          |
| median id | —        | —                | **≈ −0.45 to −0.55 pp** |
| map%      | —        | —                | unchanged (~103%)       |

**Headline FAST config:** `--speculative-decode=yes --spec-repair-threshold=10 --spec-margin-threshold=2 --spec-overlap-repair=yes`

### 3.4 FAST takeaways

1. **Margin gate dominates** — Q sweeps at m=2 barely move α or identity; m=2 vs m=3 flips the tradeoff.
2. **Brave pays when α is high** — clear win at m=2; at m=3 (α≈0.07) runs are _slower_ than baseline.
3. **Speed-first trade is good** — ~10% wall for ~0.5 pp identity.
4. **Near-beam identity (m=3)** is available but not a speed win with width-1 draft.

---

## 4. HAC results (quick sweep)

Same machinery; `--hac --quick` = baseline + q10_m2 + q10_m2_brave + q10_m3_brave. Stamp `20260802T121510Z`.

### 4.1 HAC 1k

| tag              | real_s     | α         | map%       | median_id    | Δ vs baseline |
| ---------------- | ---------- | --------- | ---------- | ------------ | ------------- |
| baseline         | 45.305     | —         | 110.50     | 0.976852     | —             |
| q10_m2           | 44.639     | 0.829     | 110.20     | 0.975572     | −0.001279     |
| **q10_m2_brave** | **44.593** | **0.827** | **110.50** | **0.975490** | **−0.001362** |
| q10_m3_brave     | 44.982     | 0.709     | 111.20     | 0.975285     | −0.001566     |

### 4.2 HAC 20k

| tag              | real_s      | α         | map%       | median_id    | Δ vs baseline |
| ---------------- | ----------- | --------- | ---------- | ------------ | ------------- |
| baseline         | 871.297     | —         | 111.37     | 0.977333     | —             |
| q10_m2           | 867.465     | 0.809     | 111.22     | 0.975527     | −0.001806     |
| **q10_m2_brave** | **866.079** | **0.809** | **111.28** | **0.975516** | **−0.001817** |
| q10_m3_brave     | 880.197     | 0.674     | 111.39     | 0.975606     | −0.001727     |

### 4.3 HAC headline

|           | baseline | q10_m2_brave | change                  |
| --------- | -------- | ------------ | ----------------------- |
| 1k wall   | 45.3 s   | 44.6 s       | **≈1.6% faster**        |
| 20k wall  | 871.3 s  | 866.1 s      | **≈0.6% faster**        |
| median id | —        | —            | **≈ −0.13 to −0.18 pp** |
| map%      | —        | —            | flat (~110–111%)        |

### 4.4 HAC takeaways

1. **Speculative decode works on HAC** — higher α than FAST at m=2 (~0.81–0.83), and **much smaller** identity cost (~0.15 pp vs ~0.5 pp).
2. **Speedup is tiny** — HAC is **infer-bound**; saving decode barely moves wall time. Brave barely beats serial q10_m2.
3. **m=3 is not useful for HAC speed** — 20k q10_m3_brave is _slower_ than baseline despite still accepting ~67% of drafts.
4. **Product implication:** sell speculative decode as a **FAST** win; on HAC treat it as optional / “small gain, tiny accuracy cost,” not a headline.

---

## 5. FAST vs HAC (side by side)

|                    | FAST q10_m2_brave | HAC q10_m2_brave        |
| ------------------ | ----------------- | ----------------------- |
| Wall speedup (20k) | **≈9%**           | **≈0.6%**               |
| α                  | ~0.73             | ~0.81                   |
| Δ median id        | ~−0.45 pp         | ~−0.18 pp               |
| Bound              | decode-bound      | infer-bound             |
| Role in story      | **headline**      | confirmation / footnote |

---

## 6. Making it faster (next ideas)

Ordered by expected payoff vs effort — mostly aimed at **FAST** (where decode still matters):

1. **Better draft (highest leverage)**  
   Mini-beam (width 4–8) should raise draft quality → higher α at the same identity, or same α with less id loss → less repair → more brave headroom.

2. **CPU repair of the hard set**  
   Host beam while GPU stays on infer/draft. Only pays while α is high (m=2). Unlikely to move HAC wall much (infer-bound).

3. **Tune brave + gpubuf**  
   Depth-1 repair hold is conservative; packing/overlap tweaks for FAST.

4. **Cheaper draft path**  
   Emissions-only draft (skip some scans) if quality stays usable.

5. **Don’t chase m=3 for speed** (either model)  
   Accuracy knob until the draft improves.

---

## 7. Files

| Path                                   | Role                         |
| -------------------------------------- | ---------------------------- |
| `openfish/src/greedy_search_cuda.h`    | greedy kernel + margins      |
| `openfish/src/decode_cuda.c`           | `openfish_decode_gpu_greedy` |
| `src/basecall.cpp`                     | gate, repair, overlap, brave |
| `docs-riley/spec-fastq-sweep`          | Jetson time+keep sweep       |
| `docs-riley/fetch-and-minimap-spec.py` | PC pull + minimap            |
| `docs-riley/best`                      | non-spec baseline runner     |

---

## 8. Bottom line

- **FAST:** real win — **~9–10% faster** (q10_m2_brave) for ~**0.5 pp** identity; brave matters.
- **HAC:** machinery works, α is high, id cost is tiny (~**0.15 pp**), but wall gain is **&lt;1%** because infer dominates — not the headline.
- **Next session:** better draft (mini-beam) for FAST; optional CPU repair. HAC needs no further grid unless we change the draft.
