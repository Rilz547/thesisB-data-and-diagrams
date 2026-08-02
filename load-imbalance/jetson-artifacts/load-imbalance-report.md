# Load Imbalance: GPU Chunk Padding in Partial Batches

When the GPU batch is set to size `C` but only `N < C` real chunks are ready, the old code still launched a full `C`-wide batch. The empty slots were filled with zeros (or stale data). The GPU then spent time inferring and decoding those dummy slots, and the results were thrown away. That is load imbalance.

This report covers a small fix ("narrow") that launches only the `N` real slots, plus experiments that simulate early flushing the way a streaming basecaller would.

- **Host:** Jetson (Tegra, aarch64), 6 cores
- **Defaults:** `-C 128 -c 12288 -K 4096 -p 150`, `--overlap-decode=yes`
- **Modes:** `narrow` (`--fixed-c-batch=no`) vs `fixedc` (`--fixed-c-batch=yes`, the old padded behaviour)
- **Timing:** use the `*-no-output` runs (`-o /dev/null`) for performance numbers. The later `*-with-output` runs wrote FASTQs for accuracy and match those times within noise.
- **Accuracy:** PC-side minimap2 vs hg38, **pass** (section 7).
- **% faster** means `(baseline - new) / baseline x 100`.

---

## Motivation

After overlap-decode v1.2, the suggestion from my supervisor was to look at load imbalance: does the GPU waste work when a batch is not full?

In the normal offline path the answer is "barely". You pack chunks until you hit `C`, so only the last leftover batch is partial (about 0.4% padding on FAST 1k). That is not worth worrying about on its own.

It matters a lot more once you care about **latency**, not just throughput. In a streaming setup (reads arriving from a live sequencer, or a pipeline that wants results out ASAP), you often cannot wait to fill a full batch of 128 chunks. You flush early: as soon as you have 64, or 32, or even 4 chunks, you send them to the GPU. Under the old fixed-`C` launch, that means every batch is mostly padding. At flush=4 and C=128, about **97%** of launched GPU slots are wasted.

So the motivation is really: make early flush cheap enough that low-latency / streaming modes are practical, without giving up the large `C` you still want for full-batch occupancy when plenty of work is queued.

---

## Why a flush speedup is useful (streaming and beyond)

The flush sweep is not "make offline FAST 1k a bit faster" as the main story. Offline already packs full batches. The interesting case is when flush is forced below `C`.

**1. Streaming / real-time basecalling.**  
Live nanopore runs produce reads continuously. If the caller waits for 128 chunks every time, end-to-end latency of the first bases grows. Flushing earlier cuts that wait. Without narrow, early flush is brutal (fixedc at flush=4 is about 251s vs about 10s at full batch on FAST 1k). With narrow, the same flush is about 57s: still slower than full batch because of launch overhead (Layer 2), but **77% faster** than the padded path. That is the difference between "streaming mode is unusable" and "streaming mode is expensive but workable".

**2. Keeping a large `C` without punishing partial batches.**  
On devices with more GPU memory (desktop/server GPUs, bigger Jetsons), you often want a large `-C` for peak throughput when the queue is full. With fixed-C padding, a large `C` makes every early flush *worse*, because padded% = `(C-N)/C` grows with `C`. Narrow breaks that tradeoff: you can keep a large `C` for busy periods, and partial flushes only pay for the real `N` slots. The 2D sweep shows this directly: at flush=4, narrow's win grows from **54.6%** at C=64 to **77.3%** at C=128, while narrow's absolute time stays flat.

**3. Edge devices and bursty queues.**  
On a Jetson (this work), memory is tight and the queue of ready chunks can be bursty: preprocess threads, I/O, or a small read batch can leave the runner short of a full `C`. Even offline, the true tail batch is partial. Narrow makes those cases free. On smaller or shared GPUs, you may also choose a smaller effective flush to reduce peak memory or to interleave with other work; again, you need partial batches not to burn the whole `C`.

**4. The modest offline win is a bonus, not the point.**  
On FAST 1k, flush=64 with narrow is about **5% faster** than packing full C=128. That is nice and accuracy-safe, but the thesis value is the streaming story: early flush becomes affordable.

---

## Changes (what we implemented)

### Code (slorado)

| Piece | Change |
|---|---|
| [`src/slorado.h`](../src/slorado.h) | `SLORADO_FIXED_C_BATCH` flag; `opt_t.flush_threshold`; counters `total_batches`, `tail_batches`, `padded_slots`, `total_chunks_processed` |
| [`src/basecall.cpp`](../src/basecall.cpp) | Before `forward`, narrow the input tensor to `N` real chunks (unless `--fixed-c-batch=yes`). Same in overlap and non-overlap paths. `pthread_single_basecall` tiles at `flush_threshold` and updates the counters. |
| [`src/basecaller_main.cpp`](../src/basecaller_main.cpp) | CLI: `--flush-threshold`, `--fixed-c-batch`; startup print; end-of-run `load-imbalance:` summary line |

How it behaves:

- Default: **narrow on**. Offline full batches look like before (only the real leftover tail is partial).
- `--fixed-c-batch=yes`: always launch `C` wide again (old padding), for A/B comparison.
- `--flush-threshold=N`: send a batch to the GPU once `N` chunks are queued (0 or unset means pack full `C`). This is how we simulate streaming.

### Experiments

| Script | Purpose |
|---|---|
| `flush_sweep_test.py` | FAST 1k, C=128, flush 128 down to 4, narrow vs fixedc |
| `batchwidth_sweep_test.py` | FAST 1k, C in {64, 128} times flush in {full, 32, 8, 4} |
| `li_spotcheck_test.py` | FAST 20k (3 cells) and HAC 1k (4 cells) at flush 128/64 |
| `docs-riley/fetch-and-minimap.py` | On the PC: pull the `*-li-*.fastq` files and score with minimap2 |

We kept the `/dev/null` timings as `*-no-output` (best numbers for the writeup). We re-ran with FASTQ output as `*-with-output` only so we could check accuracy.

---

## Headline numbers

| Comparison | Improvement |
|---|---|
| FAST 1k best (flush=64 narrow) vs overlap v1.2 full-batch | **about 4.8% faster** (3.7% to 5.9% depending on reference) |
| FAST 1k flush=64: narrow vs padded fixedc | **45.6% faster** (1.84x) |
| FAST 1k flush=4: narrow vs padded fixedc | **77.3% faster** (4.40x) |
| FAST 20k flush=64 narrow vs full-batch narrow | **0.6% faster** (within noise) |
| FAST 20k flush=64: narrow vs padded fixedc | **47.4% faster** (1.90x) |
| HAC 1k flush=64 narrow vs full-batch | **8.4% slower** (Layer 2; not an offline win) |
| HAC 1k flush=64: narrow vs padded fixedc | **43.9% faster** (1.78x) |

## Verdict

Narrow removes the padding tax. Against normal overlap v1.2 full-batch packing, FAST 1k picks up about **5%** at flush=64. FAST 20k is flat. HAC 1k is actually a bit slower than full-batch at flush=64 (about **8%**), because HAC is inference-heavy and Layer 2 shows up sooner.

The important result is the streaming comparison: if you flush early under the old padded path, you pay a huge tax. Narrow cuts that by about **45%** at flush=64 and up to about **77%** at flush=4 on FAST 1k. Accuracy stays the same.

---

## 1. Flush sweep (C=128, FAST 1k)

Source: `flush-sweep-results-no-output.txt`.

**% vs fixedc:** how much faster narrow is than padded fixed-C at the same flush (the padding tax you remove).  
**% vs v1.2 full-batch:** how that narrow cell compares to offline overlap v1.2 (average of flush=128 fixedc 10.123s and flush=128 narrow 10.365s = **10.244s**). Negative means slower than packing full batches.

| flush | padded % | fixed-C (s) | narrow (s) | speedup | % faster vs fixedc | % vs v1.2 full-batch | narrow infer (s) | narrow decode (s) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 128 | 0.4 | 10.123 | 10.365 | 0.98x | -2.4% | -1.2% | 5.163 | 8.597 |
| 96 | 26.2 | 12.884 | 10.973 | 1.17x | **14.8%** | -7.1% | 6.017 | 9.314 |
| 64 | 50.2 | 17.922 | 9.752 | 1.84x | **45.6%** | **+4.8%** | 4.337 | 8.254 |
| 48 | 62.8 | 23.159 | 10.427 | 2.22x | **55.0%** | -1.8% | 3.993 | 8.994 |
| 32 | 75.1 | 33.528 | 11.914 | 2.81x | **64.5%** | -16.3% | 2.694 | 10.414 |
| 24 | 81.3 | 43.773 | 14.216 | 3.08x | **67.5%** | -38.8% | 2.434 | 12.758 |
| 16 | 87.5 | 64.469 | 17.580 | 3.67x | **72.7%** | -71.6% | 2.651 | 16.103 |
| 12 | 90.6 | 85.088 | 22.534 | 3.78x | **73.5%** | -120.0% | 3.260 | 21.021 |
| 8 | 93.8 | 126.696 | 30.866 | 4.10x | **75.6%** | -201.3% | 4.701 | 29.357 |
| 4 | 96.9 | 250.788 | 56.965 | 4.40x | **77.3%** | -456.1% | 8.641 | 55.405 |

**Sweet spot:** flush=64 narrow is the only point that beats offline packing (**4.8%** faster than the 10.244s full-batch average; **3.7%** vs full fixedc, **5.9%** vs full narrow). Half the slots would have been padding under fixed-C. Below flush=32, Layer 2 (many small launches) makes narrow slower than full-batch, even though it still beats padded fixed-C by a wide margin.

For streaming, read the **% faster vs fixedc** column: that is what you save whenever the queue forces an early flush.

---

## 2. Batch-width sweep

Source: `batchwidth-sweep-results-no-output.txt`.

| C | flush | fixed-C (s) | narrow (s) | speedup | % faster vs fixedc |
|---:|---|---:|---:|---:|---:|
| 64 | full | 9.752 | 9.844 | 0.99x | -0.9% |
| 64 | 32 | 17.426 | 11.865 | 1.47x | **31.9%** |
| 64 | 8 | 63.846 | 30.874 | 2.07x | **51.6%** |
| 64 | 4 | 125.420 | 56.959 | 2.20x | **54.6%** |
| 128 | full | 10.117 | 10.253 | 0.99x | -1.3% |
| 128 | 32 | 33.531 | 11.887 | 2.82x | **64.5%** |
| 128 | 8 | 126.681 | 30.872 | 4.10x | **75.6%** |
| 128 | 4 | 250.857 | 56.964 | 4.40x | **77.3%** |

Two takeaways:

1. **Larger `C` makes padding worse, so narrow helps more.** At flush=4 you go from **54.6%** faster (C=64) to **77.3%** faster (C=128).
2. **Narrow time does not grow with `C`.** At flush=4 both C=64 and C=128 take about 57s. Fixed-C roughly doubles (125s to 251s). So on a bigger GPU you can raise `-C` for peak throughput without making every partial flush more expensive.

C=256 with overlap-decode ran out of memory on this Jetson (about 1.08 GB contiguous alloc failed). The grid stops at C=128; the trend was already clear.

---

## 3. Spot-checks: FAST 20k and HAC 1k

Source: `li-spotcheck-results-with-output.txt`. Same flags (`-C 128`, overlap on).

### FAST 20k

| flush | mode | real (s) | padded % | % vs f128 narrow | % vs f64 fixedc |
|---:|---|---:|---:|---:|---:|
| 128 | narrow | 176.410 | 0.3 | baseline | n/a |
| 64 | narrow | 175.291 | 50.1 | **+0.6%** (essentially flat) | **47.4%** |
| 64 | fixedc | 332.967 | 50.1 | n/a | baseline |

At scale, flush=64 is not a clear offline win (0.6% is noise). The streaming comparison still holds: narrow is **47.4%** faster than padded fixedc at the same flush.

### HAC 1k

| flush | mode | real (s) | padded % | % vs f128 avg | % vs f64 fixedc |
|---:|---|---:|---:|---:|---:|
| 128 | narrow | 45.582 | 0.4 | about 0 (matches fixedc) | n/a |
| 128 | fixedc | 45.350 | 0.4 | baseline avg 45.466s | n/a |
| 64 | narrow | 49.275 | 50.2 | **-8.4%** (slower) | **43.9%** |
| 64 | fixedc | 87.791 | 50.2 | n/a | baseline |

HAC does not get an offline boost from flush=64 (about **8.4% slower** than full-batch). Inference dominates, so Layer 2 shows earlier than on FAST. If streaming forces an early flush anyway, narrow is still **43.9%** faster than the padded path. Same mechanism, different offline tradeoff.

---

## 4. Two layers of cost

**Layer 1: padding tax (what narrow removes).**  
Fixed-C always runs `C` slots. A partial batch with `N` real chunks wastes `(C-N)/C` of the GPU work. On FAST 1k, removing that saves **14.8%** wall time at flush=96 and up to **77.3%** at flush=4.

**Layer 2: launch overhead and low occupancy (what is left).**  
Even with narrow, FAST 1k goes from about 10.4s at flush=128 to about 57s at flush=4 (about **5.5x** slower than full-batch). You are firing many more kernels on smaller batches. Fixing that means coalescing work on the host (fewer, fuller launches), not more FLOPs per slot.

---

## 5. How to talk about this later

1. **Problem:** fixed `-C` batches pad partial flushes. Fine offline; bad for streaming latency.
2. **Fix:** narrow the tensor to `N` before forward. Tiny change, gated with `--fixed-c-batch` for fair A/B. `--flush-threshold` simulates streaming.
3. **Why flush speedup matters:** live reads, low-latency pipelines, and any device that wants a large `C` when busy but still flushes early when the queue is short.
4. **Offline vs v1.2:** FAST 1k about **5%** at flush=64; FAST 20k flat; HAC 1k not improved offline.
5. **Streaming vs padded:** up to about **77%** on FAST 1k (flush=4); about **45%** at flush=64 on FAST and HAC.
6. **Accuracy:** unchanged.
7. **Next step:** Layer 2 (host-side coalescing).

---

## 6. Accuracy (PC minimap2 vs hg38)

Source: `overlapping/load-imbalance/accuracy/minimap_accuracy-load-imbalance.csv` (43 FASTQs).

**Pass.** Every 1k run is within **0.0003** of the published expected median (we used a 0.01 pass bar). Map rates look healthy (about 103% FAST, 110% HAC).

| Set | n | median identity | vs expected | notes |
|---|---:|---|---|---|
| FAST 1k (all flush / C / mode) | 36 | 0.940763 to 0.940909 | +0.000067 to +0.000213 | expected 0.940696 |
| HAC 1k (f128/f64 x narrow/fixedc) | 4 | **0.976852** | **0.000000** | exact on all four |
| FAST 20k (three cells) | 3 | **0.939375** (identical) | n/a (no published 20k expected) | same across modes |

FAST 1k lands on three discrete medians (0.940763, 0.940772, 0.940909). That is normal floating-point / cuDNN batch-size variation, not a quality drop. Worst gap versus expected is still tiny.

---

## Findings

1. **vs overlap v1.2 full-batch:** FAST 1k about **4.8% faster** at flush=64; FAST 20k about **0.6%** (flat); HAC 1k about **8.4% slower** at flush=64.
2. **vs padded early flush:** the big wins. FAST 1k up to **77.3%**, FAST 20k **47.4%**, HAC 1k **43.9%** at flush=64.
3. **Narrow is free at full batch** (within noise).
4. **Narrow's win grows with `C`**, while absolute narrow time stays flat. That is what you want on larger GPUs.
5. **Layer 2 remains** after narrow. Next lever is fewer, fuller launches on the host.
6. **Accuracy is preserved.**

---

## Reproducing

| Artifact | Role |
|---|---|
| `flush-sweep-*-no-output.*` | Primary FAST 1k flush timing |
| `batchwidth-sweep-*-no-output.*` | Primary C x flush timing |
| `*-with-output.*` and `output_*_overlap-li-*.fastq` | Accuracy re-run |
| `li-spotcheck-*-with-output.*` | FAST 20k and HAC 1k spot-check |
| `flush_sweep_test.py`, `batchwidth_sweep_test.py`, `li_spotcheck_test.py` | Drivers |

### Code change summary
- `src/slorado.h`: `SLORADO_FIXED_C_BATCH`, `flush_threshold`, load-imbalance counters.
- `src/basecall.cpp`: narrow input to `N` before `forward` unless fixed-C; flush-threshold tiling and counters.
- `src/basecaller_main.cpp`: `--flush-threshold`, `--fixed-c-batch`, summary line.
