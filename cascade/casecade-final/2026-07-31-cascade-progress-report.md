# Two-Model FAST→HAC Cascade — Report

**Date:** 2026-07-31 (updated 2026-08-02)  
**Platform:** Jetson Orin (`riley-jetson`) · slorado 0.5.0-beta  
**Models:** `dna_r10.4.1_e8.2_400bps_fast@v5.0.0`, `…_hac@v5.0.0`  
**Data:** `test/PGXXXX230339/reads_1k.blow5`, `reads_20k.blow5`  
**Accuracy:** minimap2 `map-ont` vs hg38noAlt  
**Compose:** overlap-decode depth=1, chunk 12288, flush-threshold 64 (unless noted)

---

## 1. Motivation / idea

FAST is cheap but leaves a hard tail of poorly basecalled / poorly mapping reads. HAC is much more accurate on that tail, but roughly **5× slower** on 1k (≈10 s FAST vs ≈49 s HAC).

**Idea:** run a **two-pass cascade**:

1. **Scout** every read with FAST.  
2. **Promote** only the “hard” reads to HAC.  
3. Emit a mixed FASTQ: easy reads keep FAST calls; hard reads get HAC calls.

Success looks like: **global median identity > FAST**, **wall time ≪ full HAC**, without needing HAC for every read.

Early hypothesis used an absolute FAST mean-Phred threshold. After calibration this became a **force-frac** budget: always promote the worst X% of the batch by mean Q.

---

## 2. Implementation (high level)

### 2.1 Pipeline

```text
reads → FAST basecall (all)
      → score each read (mean Phred Q of FAST quality string)
      → select worst force_frac of the batch
      → unload FAST, load HAC (lazy — dual-resident OOM’d on 20k)
      → HAC basecall promoted reads only
      → free HAC, reload FAST
      → write mixed FASTQ + optional TSV log (read_id, mean_q, model)
```

### 2.2 User-facing controls

| Flag | Role |
|------|------|
| `--cascade=yes` | enable cascade |
| `--cascade-hac=PATH` | HAC model directory |
| `--cascade-force-frac=X` | promote worst fraction X ∈ (0, 1] by mean Q (**required**) |
| `--cascade-log=FILE` | per-read TSV |

`--cascade-threshold` existed in v1 and was **removed** once force-frac became the only router.

### 2.3 VRAM note

Keeping FAST and HAC resident together OOM’d on 20k. The fix is **lazy HAC**: park/unload FAST for the promote pass, then restore FAST. High promote fractions on 20k still needed **`-C 64`** (not smaller chunks).

### 2.4 Typical run flags

```text
-C 128 (1k / low-frac 20k)  or  -C 64 (high-frac 20k)
-c 12288 -K 4096 -p 150
--overlap-decode=yes --overlap-depth=1 --fixed-c-batch=no
--flush-threshold=64
--cascade=yes --cascade-hac=… --cascade-force-frac=X
```

---

## 3. Baselines (overlap v1.2 measured)

These are the anchors used throughout. Do **not** use older “expected” constants.

| Mode | Set | Median identity | map% | real_s (this campaign) |
|------|-----|----------------:|-----:|-----------------------:|
| FAST | 1k | **0.940763** | ~103% | **9.801** |
| HAC  | 1k | **0.976852** | ~110.5% | **49.187** |
| FAST | 20k | **0.939375** | ~103.6% | (prior overlap campaign) |
| HAC  | 20k | **0.977333** | ~110+% | (prior overlap campaign) |

FAST→HAC median gap: **+0.036** (1k), **+0.038** (20k).

---

## 4. Initial results (absolute mean-Q threshold)

### 4.1 Early shower (thr = 10)

| Mode | 1k real_s | % HAC |
|------|----------:|------:|
| FAST-only | ~10–11 | 0 |
| Cascade thr=10 | ~18 | 16.8% |
| HAC-only | ~49 | 100% |

≈ **2.7× faster than HAC** at thr=10 on 1k.  
Manual 20k thr=10: **348.9 s**, `kept_fast=16544`, `promoted_hac=3456` (**17.3% HAC**).

### 4.2 Threshold sweep `20260731T040555Z`

Thresholds 8–14; 1k × 3 reps; 20k × 1. Script: `cascade_threshold_sweep.py`.

**1k speed (mean of 3 reps):**

| thr | promoted / 1000 | % HAC | mean real_s |
|----:|----------------:|------:|------------:|
| 8 | 58 | 5.8% | 13.37 |
| 9 | 113 | 11.3% | 15.97 |
| 10 | 168 | 16.8% | 18.44 |
| 11 | 189 | 18.9% | 19.14 |
| 12 | 203 | 20.3% | 19.94 |
| 13 | 217 | 21.7% | 20.55 |
| 14 | 230 | 23.0% | 21.12 |

**20k speed:**

| thr | % HAC | real_s | status |
|----:|------:|-------:|--------|
| 8 | — | — | OOM (`exit=-6`), incomplete FASTQ |
| 9 | — | — | OOM, incomplete |
| 10 | 17.3% | 348.9 | OK |
| 11 | 19.6% | 370.1 | OK |
| 12 | 21.0% | 381.2 | OK |
| 13 | 22.2% | 392.6 | OK |
| 14 | — | — | OOM, incomplete |

OOM signature: `NvMapMemAlloc… error 12` → CUDA caching allocator / cuDNN LSTM under unload/reload.

**1k global median identity (all reads):**

| thr | median | vs FAST 0.940763 | vs HAC 0.976852 |
|----:|-------:|-----------------:|----------------:|
| 8 | 0.940000 | ≈0 / slightly down | −0.037 |
| 9 | 0.939002 | −0.0018 | −0.038 |
| 10 | 0.938462 | −0.0023 | −0.038 |
| 11–14 | ~0.9382–0.9388 | ~−0.002–0.003 | ~−0.038 |

Reps identical within thr → not noise.

**20k global median (complete thr 10–13 only):**

| Mode | median |
|------|-------:|
| FAST 20k | 0.939375 |
| Cascade thr 10–13 | **0.9364–0.9366** (still **below** FAST) |

Map rate rose with thr (1k ~105% → ~110%), approaching HAC, even while median stayed FAST-like.

### 4.3 Mean-Q distribution (why thr 8–11 matter)

Approximate promote rate on 20k by absolute thr:

| thr | ≈ % HAC (20k) |
|----:|--------------:|
| 7 | 3.6% |
| 8 | 6.9% |
| 9 | 12.5% |
| 10 | 17.3% |
| 11 | 19.6% |
| 12–14 | 21–23.5% (plateau) |

Hard hump ≈ Q 7–10; median FAST Q ≈ 22. Absolute thr 12–14 buys little extra promote rate for extra time.

### 4.4 Initial takeaway

Plumbing worked and was faster than HAC, but **global median never beat FAST** in the thr 8–14 band. Either the router was wrong, the metric was wrong, or promote rates were too low for the median KPI.

---

## 5. Tuning / improvements

### 5.1 Correctness: 100% promote sanity

Cascade 1k with always-promote (thr=50 era / 100% HAC path):

| Metric | Result |
|--------|--------|
| Promoted | 1000 / 1000 |
| Median identity | **0.976852** (= HAC exactly) |
| map% | 110.5% |

**Pass-B (including lazy unload/reload) is correct.** Global-median stagnation was not a HAC-pass bug.

### 5.2 Promoted-only check (thr=10 hard tail)

Same **168** read IDs marked `hac` in the thr10 TSV, scored under FAST-only vs cascade:

| On 168 promoted IDs | FAST-only | Cascade (HAC) | Δ |
|---------------------|----------:|--------------:|--:|
| Median identity | 0.755507 | **0.810000** | **+0.054493** |
| Alignments | 96 / 168 | **155 / 168** | +59 |

**Mean-Q selects a real hard / poorly mapping tail**, and HAC clearly helps that subset. Global median was the wrong sole success metric for a ~17% tail router — but the product goal still wanted median > FAST, so more promote budget was needed.

### 5.3 Router calibration

Script: `cascade_router_calibrate.py` on FAST-only vs thr50(=HAC) 1k. Rank scores by correlation with per-read identity gain Δ.

| Score | Spearman vs Δ | % of positive-Δ mass @ 17% budget |
|-------|--------------:|----------------------------------:|
| **mean_q** | **+0.530** | **52.2%** (best) |
| frac_lt15 | +0.525 | 51% |
| frac_lt10 | +0.512 | 51% |
| oracle ceiling | — | 66.5% |

Absolute thr=10 (168 reads) ≈ worst 17% by mean_q.  
**Decision:** keep mean_q; **do not** switch features. Replace absolute thr with **`--cascade-force-frac`** so the promote budget is portable across datasets.

### 5.4 Force-frac implementation + VRAM hardening

- Removed absolute threshold; cascade requires `--cascade-force-frac ∈ (0,1]`.  
- Swept X ∈ {0.05, 0.10, 0.15, 0.17, 0.20, 0.25, 0.30, 0.40, 0.50}.  
- First 20k high-frac runs OOM’d / truncated; re-ran 0.15–0.50 with **`-C 64`** (`cascade_20k_missing_rerun.sh`).

---

## 6. Final results (force-frac)

### 6.1 1k speed (mean of 3 reps) — stamp `20260731T053635Z`

| frac | % HAC | mean real_s | vs HAC 49.2 s |
|-----:|------:|------------:|--------------:|
| FAST | 0 | 9.80 | — |
| 0.05 | 5% | 12.80 | 3.8× faster |
| 0.10 | 10% | 15.37 | 3.2× |
| 0.15 | 15% | 17.44 | 2.8× |
| 0.17 | 17% | 18.55 | 2.7× |
| 0.20 | 20% | 19.72 | 2.5× |
| 0.25 | 25% | 21.67 | 2.3× |
| 0.30 | 30% | 23.50 | 2.1× |
| 0.40 | 40% | 29.00 | 1.7× |
| 0.50 | 50% | 32.85 | 1.5× |
| HAC | 100% | 49.19 | — |

### 6.2 1k accuracy (reps identical within frac)

Baselines: FAST **0.940763**, HAC **0.976852**.

| frac | median | Δfast | Δhac | map% | mean real_s |
|-----:|-------:|------:|-----:|-----:|------------:|
| 0.05 | 0.940043 | −0.000729 | −0.03681 | 104.9% | 12.80 |
| 0.10 | 0.939056 | −0.001716 | −0.03780 | 106.9% | 15.37 |
| 0.15 | 0.938679 | −0.002093 | −0.03817 | 108.3% | 17.44 |
| 0.17 | 0.938376 | −0.002396 | −0.03848 | 109.0% | 18.55 |
| 0.20 | 0.938246 | −0.002526 | −0.03861 | 109.6% | 19.72 |
| 0.25 | 0.939231 | −0.001541 | −0.03762 | 110.0% | 21.67 |
| **0.30** | **0.942532** | **+0.001760** | −0.03432 | **110.2%** | **23.50** |
| **0.40** | **0.947368** | **+0.006596** | −0.02948 | **110.3%** | **29.00** |
| **0.50** | **0.950625** | **+0.009853** | −0.02623 | **110.5%** | **32.85** |

First clear beat of FAST median: **force_frac = 0.30**.  
At 50% still **−0.026** vs HAC.

### 6.3 20k — first sweep (complete points only, `-C` higher)

Incomplete / OOM fracs (0.15, 0.20, 0.30, 0.40, 0.50) ignored for accuracy claims.

| frac | median | Δfast (vs 0.939375) | Δhac | real_s | map% | status |
|-----:|-------:|--------------------:|-----:|-------:|-----:|--------|
| 0.05 | 0.938622 | −0.000753 | −0.03871 | 232.3 | 105.3% | OK |
| 0.10 | 0.937716 | −0.001659 | −0.03962 | 277.7 | 107.5% | OK |
| 0.17 | 0.936623 | −0.002752 | −0.04071 | 345.2 | 109.7% | OK |
| 0.25 | 0.937453 | −0.001922 | −0.03988 | 415.9 | 110.9% | OK |
| 0.15 / 0.20 / 0.30–0.50 | — | — | — | — | — | OOM / truncated |

### 6.4 20k — final high-frac re-runs (`-C 64`, 20000 reads)

Outputs: `docs-riley/cascade-20k-missing-rerun/`.  
Timings: 0.30–0.50 from `[main] Real time`; 0.15–0.20 from file birth→mtime ≈ wall.

Baselines: FAST **0.939375**, HAC **0.977333**.

| frac | median | Δfast | Δhac | real_s | %HAC | map% | Δmed vs prev |
|-----:|-------:|------:|-----:|------:|-----:|-----:|-------------:|
| 0.15 | 0.936925 | −0.002450 | −0.040408 | 332.0 | 15% | 109.1% | — |
| 0.20 | 0.936441 | −0.002934 | −0.040892 | 382.0 | 20% | 110.4% | −0.0005 |
| **0.30** | **0.940090** | **+0.000715** | −0.037243 | **468.0** | 30% | 111.0% | **+0.0036** |
| **0.40** | **0.945472** | **+0.006097** | −0.031861 | **545.1** | 40% | 111.1% | **+0.0054** |
| 0.50 | 0.950063 | +0.010688 | −0.027270 | 619.9 | 50% | 111.1% | +0.0046 |

**20k first beat of FAST median: force_frac = 0.30** (same knee as 1k).  
Curve **not flattened** through 0.50 — median still rises ~0.005 per +0.10 frac.  
map% saturates ≈ HAC by **0.30**.  
At 0.50 still **−0.027** vs HAC.

### 6.5 Combined speed / accuracy picture (headline)

| Question | 1k answer | 20k answer |
|----------|-----------|------------|
| Beat FAST median? | yes from **0.30** (0.9425) | yes from **0.30** (0.9401) |
| Clear FAST win? | **0.40** (+0.0066) | **0.40** (+0.0061) |
| Near HAC? | no — 0.50 → 0.9506 vs 0.9769 | no — 0.50 → 0.9501 vs 0.9773 |
| Flatten by 0.50? | no (still climbing) | no (still climbing) |
| Practical park | **0.30–0.40** | **0.30–0.40** |

Optional extra point **0.60** only if a figure needs the right edge; skip 0.70 unless 0.60 still looks linear. Extrapolation ≈ 0.955 / ~700 s and 0.960 / ~780 s — still well below HAC.

---

## 7. Conclusion / evaluation

### What worked

1. **Cascade is correct** — 100% promote matches HAC median exactly; lazy HAC load is sound.  
2. **Router finds a real hard tail** — thr10 promoted-only: **+0.054** identity, alignments 96→155 / 168.  
3. **mean_q is the right simple score** among tested features; force-frac is the right budget knob.  
4. **Product goal is achievable on median** — both 1k and 20k first exceed FAST at **~30% HAC**, with a clearer win at **~40%**, still well under full-HAC time on 1k (~29 s vs 49 s at 0.40).

### What did not work / limits

1. **Low promote rates (≤ ~25%) never beat FAST global median** — expected once the median is dominated by easy reads (median FAST Q ≈ 22).  
2. **Not a HAC replacement** — even 50% promote recovers only part of the FAST→HAC gap (~0.011 of ~0.038 on 20k median).  
3. **No accuracy flatten through 0.50** while time keeps climbing (~+75–80 s per +0.10 frac on 20k @ `-C 64`).  
4. **20k VRAM remains the operational constraint** — dual-resident failed; high frac needs `-C 64`; some configs still OOM’d before that fix.

### Thesis-facing claim

> Selective FAST→HAC promotion by mean-Q force-frac **rescues a hard tail** and can push **global median above FAST from ~30% HAC**, with a practical operating region around **0.30–0.40**. It recovers **part** of the FAST→HAC gap at moderate cost — it is **not** “near-HAC for cheap.”

### Optional follow-ups

- One 20k point at force_frac **0.60** for the figure’s right edge.  
- Better per-read scores if the goal is more accuracy per HAC-second (force-frac alone is blunt).  
- Commit cascade + docs when ready.

---

## Appendix A — Scripts & artifacts

| Path | Role |
|------|------|
| `docs-riley/cascade_threshold_sweep.py` | Early absolute-thr Jetson sweep |
| `docs-riley/cascade_force_frac_sweep.py` | Force-frac Jetson sweep |
| `docs-riley/cascade_20k_missing_rerun.sh` | 20k frac 0.15–0.50 @ `-C 64` |
| `docs-riley/cascade-20k-missing-rerun/cascade_missing_rerun_results.csv` | Re-run timings |
| `docs-riley/fetch-and-minimap-cascade.py` | Main-PC thr-era accuracy |
| `docs-riley/fetch-and-minimap-cascade-frac.py` | Main-PC force-frac accuracy + timing join |
| `docs-riley/cascade_sanity_minimap.py` | 100% promote sanity |
| `docs-riley/cascade_promoted_minimap.py` | Promoted-tail FAST vs cascade |
| `docs-riley/cascade_router_calibrate.py` | Router feature ranking |
| `docs-riley/2026-07-31-two-model-cascade-design.md` | Design notes |
| `docs-riley/2026-07-31-two-model-cascade-plan.md` | Implementation plan |

Sweep roots:  
`docs-riley/cascade-threshold-sweep/20260731T040555Z/`  
`docs-riley/cascade-force-frac-sweep/20260731T053635Z/`  
`docs-riley/cascade-20k-missing-rerun/`
