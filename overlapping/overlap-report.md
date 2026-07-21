# Infer∥Decode Overlap — Overall Report (v1.0 → v1.2)

Hardware: Jetson Orin · slorado `0.5.0-beta` · openfish (stream-aware)
Models: `dna_r10.4.1_e8.2_400bps_{fast,hac}@v5.0.0` · batch `-C 128` · data `reads_{1k,20k}.blow5`
Versions: v1.0 `e00bada`/`59e12e9` · v1.1 `9f8db24`/`cf182a7` · v1.2 `7cec6c5`/`223f26c`

## 1. Overview & methodology

Three incremental versions of the infer∥decode overlap were benchmarked against their own serial baselines and against the original (v1.0) baseline. All changes are performance-only and output-preserving.

Test harness (`rileys-runner.py`): for each config a cache warmup runs first, then the **timed runs write to `-o /dev/null`** — **10 runs for 1k, 3 runs for 20k** — and a **separate accuracy + Nsight pass** (saves FASTQ, profiled under `nsys --trace=cuda,nvtx,osrt`) runs once. The profiled/accuracy pass is **excluded** from all timing statistics, so wall-clock numbers are free of profiler overhead.

Statistics are computed over the timed `/dev/null` runs: **min, mean, max, sample stdev (n−1), and CV% = stdev/mean × 100**. "baseline" is `--overlap-decode=no` for that version's build; "overlap" is `--overlap-decode=yes`. Because the v1.1/v1.2 buffer changes apply to both code paths, **each version has its own baseline**; the "vs v1.0 base" columns use the original v1.0 baseline as a common anchor for cumulative comparison. Accuracy is `minimap2 -cx map-ont --secondary=no` vs hg38, median PAF identity.

## 2. Version summaries

- **v1.0 — stream-aware overlap.** Runs decode(N−1) concurrently with infer(N) on a second CUDA stream (dual streams, double-buffered input, per-slot events, pinned D2H, decode-stream sync). Shipped the NTC score-layout fix required for correct batched decode. Delivers the primary win; also introduces per-batch pinned host alloc/free.
- **v1.1 — persistent pinned host buffers.** Allocates the decode-output host buffers once on the openfish gpubuf and reuses them, eliminating the per-batch `cudaHostAlloc`/`cudaFreeHost`. Removes the `cudaFreeHost` hot-path cost and its jitter; yields a clear second wall-time gain.
- **v1.2 — 2-slot host ring + CPU-write overlap ("P5-lite").** Turns the host buffer into a 2-slot ring so the CPU copy-out of the previous batch runs concurrently with the next GPU decode. Wall time is level with v1.1 at depth-1; the ring is groundwork for a future depth-2 pipeline.

## 3. Accuracy (validation)

Identical across **every version and both modes** — as expected for output-preserving changes.

| Config | Reads | Alns | Map% | Median identity (all versions, base = overlap) | vs expected |
|---|--:|--:|--:|:--:|:--|
| FAST 1k  | 1000  | 1031  | 103.1% | 0.940763 | +0.000067 |
| FAST 20k | 20000 | 20727 | 103.6% | 0.939375 | — |
| HAC 1k   | 1000  | 1105  | 110.5% | 0.976852 | −0.000000 |
| HAC 20k  | 20000 | 22274 | 111.4% | 0.977333 | — |

## 4. Headline — overlap speedup vs same-version baseline (mean real time)

| Config | v1.0 | v1.1 | v1.2 |
|---|--:|--:|--:|
| FAST 1k | +9.84% | +11.71% | +11.51% |
| FAST 20k | +11.73% | +13.80% | +13.94% |
| HAC 1k | +1.96% | +3.40% | +2.99% |
| HAC 20k | +2.49% | +2.72% | +3.00% |

The gain grows from v1.0 to v1.1 (v1.1 removes the pinned-free stall from the overlap path) and then holds flat at v1.2. FAST benefits far more than HAC because FAST has a large decode share to hide behind inference, whereas HAC is infer-bound (little decode to overlap).

## 5. Real-time detail (per config)

Real time in seconds over the timed `/dev/null` runs. **vs own base (avg)** is the overlap speedup on the mean versus that version’s own baseline. The three **vs v1.0 base** columns give the improvement against the **v1.0 baseline mean** at three points of the row’s own run distribution: **min** (from the row’s slowest run), **avg** (from its mean) and **max** (from its fastest run). Positive = faster; min ≤ avg ≤ max.

### FAST 1k — real time (s), n=10

| Version | Mode | min | mean | max | CV% | vs own base (avg) | vs v1.0 base — min | vs v1.0 base — avg | vs v1.0 base — max |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| v1.0 | baseline | 11.351 | 11.500 | 12.367 | 2.688 | — | -7.54% | +0.00% | +1.29% |
| v1.0 | overlap | 10.303 | 10.368 | 10.428 | 0.310 | +9.84% | +9.32% | +9.84% | +10.41% |
| v1.1 | baseline | 11.362 | 11.401 | 11.438 | 0.187 | — | +0.54% | +0.86% | +1.20% |
| v1.1 | overlap | 10.039 | 10.066 | 10.092 | 0.175 | +11.71% | +12.24% | +12.47% | +12.70% |
| v1.2 | baseline | 11.368 | 11.404 | 11.461 | 0.267 | — | +0.34% | +0.84% | +1.15% |
| v1.2 | overlap | 10.064 | 10.091 | 10.144 | 0.292 | +11.51% | +11.79% | +12.25% | +12.49% |

Note the v1.0 baseline **max of 12.367 s** (CV 2.69%) — a pinned-free jitter outlier; it also shows as the −7.54% “min” cell (that slow run was 7.5% *slower* than the v1.0 baseline mean). It disappears from v1.1 on.

### FAST 20k — real time (s), n=3

| Version | Mode | min | mean | max | CV% | vs own base (avg) | vs v1.0 base — min | vs v1.0 base — avg | vs v1.0 base — max |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| v1.0 | baseline | 203.496 | 203.685 | 204.053 | 0.156 | — | -0.18% | +0.00% | +0.09% |
| v1.0 | overlap | 179.344 | 179.791 | 180.511 | 0.350 | +11.73% | +11.38% | +11.73% | +11.95% |
| v1.1 | baseline | 203.406 | 203.711 | 204.080 | 0.168 | — | -0.19% | -0.01% | +0.14% |
| v1.1 | overlap | 174.754 | 175.591 | 176.757 | 0.593 | +13.80% | +13.22% | +13.79% | +14.20% |
| v1.2 | baseline | 203.810 | 203.966 | 204.160 | 0.087 | — | -0.23% | -0.14% | -0.06% |
| v1.2 | overlap | 175.358 | 175.534 | 175.746 | 0.112 | +13.94% | +13.72% | +13.82% | +13.91% |

### HAC 1k — real time (s), n=10

| Version | Mode | min | mean | max | CV% | vs own base (avg) | vs v1.0 base — min | vs v1.0 base — avg | vs v1.0 base — max |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| v1.0 | baseline | 46.359 | 46.519 | 46.707 | 0.230 | — | -0.41% | +0.00% | +0.34% |
| v1.0 | overlap | 45.267 | 45.608 | 45.906 | 0.518 | +1.96% | +1.32% | +1.96% | +2.69% |
| v1.1 | baseline | 46.342 | 46.555 | 46.793 | 0.293 | — | -0.59% | -0.08% | +0.38% |
| v1.1 | overlap | 44.847 | 44.970 | 45.084 | 0.174 | +3.40% | +3.08% | +3.33% | +3.59% |
| v1.2 | baseline | 46.356 | 46.580 | 46.782 | 0.282 | — | -0.57% | -0.13% | +0.35% |
| v1.2 | overlap | 45.071 | 45.188 | 45.302 | 0.164 | +2.99% | +2.62% | +2.86% | +3.11% |

### HAC 20k — real time (s), n=3

| Version | Mode | min | mean | max | CV% | vs own base (avg) | vs v1.0 base — min | vs v1.0 base — avg | vs v1.0 base — max |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| v1.0 | baseline | 900.684 | 902.986 | 904.467 | 0.224 | — | -0.16% | +0.00% | +0.25% |
| v1.0 | overlap | 879.164 | 880.505 | 881.614 | 0.141 | +2.49% | +2.37% | +2.49% | +2.64% |
| v1.1 | baseline | 898.258 | 899.878 | 902.285 | 0.236 | — | +0.08% | +0.34% | +0.52% |
| v1.1 | overlap | 875.083 | 875.421 | 875.999 | 0.057 | +2.72% | +2.99% | +3.05% | +3.09% |
| v1.2 | baseline | 898.913 | 900.972 | 903.026 | 0.228 | — | -0.00% | +0.22% | +0.45% |
| v1.2 | overlap | 871.883 | 873.971 | 876.746 | 0.286 | +3.00% | +2.91% | +3.21% | +3.44% |

Baselines are stable across versions (mean drift ≤ 0.34%), confirming the improvements come from the overlap path, not baseline movement.

## 6. Peak RAM (GB, max over timed runs)

| Config | v1.0 base | v1.0 ovl | v1.1 base | v1.1 ovl | v1.2 base | v1.2 ovl | overlap Δ (v1.2) |
|---|--:|--:|--:|--:|--:|--:|--:|
| FAST 1k | 2.62 | 3.65 | 2.62 | 3.69 | 2.62 | 3.68 | +1.06 |
| FAST 20k | 4.55 | 5.61 | 4.48 | 5.56 | 4.51 | 5.56 | +1.04 |
| HAC 1k | 3.77 | 4.27 | 3.75 | 4.31 | 3.77 | 4.29 | +0.51 |
| HAC 20k | 5.67 | 6.18 | 5.62 | 6.07 | 5.63 | 6.07 | +0.44 |

Overlap costs roughly **+1.0 GB on FAST and +0.5 GB on HAC**, driven by the double-buffered input and concurrent activations. The overhead is **stable across versions** — the v1.2 2-slot host ring adds negligible RAM (the buffers are ~MB). All configs stay comfortably within the 16 GB Orin.

## 7. CPU time (mean, s)

| Config | v1.0 base | v1.0 ovl | v1.1 base | v1.1 ovl | v1.2 base | v1.2 ovl |
|---|--:|--:|--:|--:|--:|--:|
| FAST 1k | 4.0 | 4.2 | 4.0 | 4.2 | 4.0 | 4.2 |
| FAST 20k | 41.5 | 39.3 | 41.5 | 39.7 | 41.4 | 39.7 |
| HAC 1k | 31.8 | 43.8 | 31.8 | 43.4 | 31.8 | 43.5 |
| HAC 20k | 591.1 | 828.1 | 588.3 | 826.3 | 589.4 | 824.7 |

The tradeoff worth noting for the thesis: overlap buys wall-clock time by keeping a host thread **active** (waiting on / servicing the decode stream) while the GPU works. For **FAST** the CPU cost is roughly flat (even slightly lower). For **HAC** it rises sharply — **+37% (1k) and +40% (20k)** — because HAC's GPU phases are long, so the host spends far longer waiting during each overlapped batch. On Jetson's shared CPU/GPU power and thermal budget this is a real cost, not free throughput; it is stable across versions.

## 8. Run-to-run jitter (CV%)

The most visible jitter effect is in v1.0: the FAST 1k baseline has **CV 2.69%** with a 12.37 s outlier (versus ~11.4 s typical) — the signature of the per-batch pinned free occasionally hitting a slow OS path. From **v1.1 onward every config is sub-0.6% CV** (most well under 0.3%), on both baseline and overlap paths, because the persistent buffers remove that high-variance operation. v1.2 keeps timing equally tight. Steadier timing makes the reported speedups reproducible.

## 9. Nsight — CUDA API progression (FAST 20k overlap)

| Version | `cudaFreeHost` | `cudaMemcpyAsync` | `cudaStreamSynchronize` |
|---|--:|--:|--:|
| v1.0 | 51.1% (2,400 calls) | 0.8% | 46.0% |
| v1.1 | 0.0% (3 calls) | 49.8% | 48.1% |
| v1.2 | 0.0% (6 calls) | 49.9% | 48.0% |

v1.1 eliminates `cudaFreeHost` from the hot path; the mandatory once-per-batch host wait then re-attributes onto `cudaMemcpyAsync` / `cudaStreamSynchronize` — pure waits with no OS page-fault tail (the ~50% `cudaMemcpyAsync` API time corresponds to well under 1 s of real transfer). v1.2's profile is unchanged from v1.1 (6 teardown frees for the 2-slot ring), consistent with its flat wall time. On HAC, `cudaFreeHost` similarly drops from ~8% (v1.0) to ~0% (v1.1/v1.2); HAC's API time is otherwise dominated by `cudaLaunchKernel` (launch-bound).

## 10. Conclusions

- **v1.0** delivers the overlap win: FAST +9.8%/+11.7% (1k/20k), HAC +2.0%/+2.5%, at identical accuracy — but adds per-batch pinned alloc/free (51% of CUDA API time) and a jitter outlier.
- **v1.1** is the second clear step: it removes `cudaFreeHost` (51% → 0%), lifts FAST 20k overlap to +13.8% and HAC 20k to +2.7% vs baseline, and collapses run-to-run jitter (FAST 1k baseline CV 2.69% → 0.19%).
- **v1.2** holds wall time level with v1.1 (within noise) and keeps timing tight; its contribution is the 2-slot host ring that makes the CPU copy-out concurrent and unlocks a future **depth-2** pipeline. At depth-1 the host critical path is the decode-stream sync / D2H wait, which the CPU-write overlap does not remove.
- Across all versions: **accuracy is unchanged**, **baselines are stable**, overlap costs **~0.5–1.0 GB extra RAM** and (for HAC) **~40% more CPU time** in exchange for the wall-clock gain.
- **Next lever:** depth-2 pipelining (now unblocked by the v1.2 ring) to attack the decode-stream rendezvous itself; separately, LSTM-side work (e.g. flip-copy elimination, quantisation) is the path for the infer-bound HAC model.