# Infer/Decode Overlap: Notes and Draft

---

# Simple notes

## What the idea is

Each GPU batch does two jobs:

1. **Infer:** neural net to scores  
2. **Decode:** openfish to DNA letters and qualities  

**Baseline:** finish infer, then decode, then start the next batch.  
**Overlap (`--overlap-decode=yes`):** while decoding batch *N*, I start inferring batch *N+1* on a second CUDA stream.

Same answers; better schedule.

## What I changed (short)

1. **Two GPU lanes:** infer stream and decode stream, using ping-pong input buffers.  
2. **Fewer “wait for the whole GPU” calls:** I skipped per-layer and device-wide syncs when overlapping; I only sync the decode stream when I need the bases.  
3. **Pinned host memory for decode outputs:** so GPU-to-CPU copies do not secretly stall.  
4. **Openfish takes a stream:** decode kernels run on the decode lane.  
5. **NTC layout fix:** I stopped flipping scores to TNC, which broke accuracy for `-C > 1`. This was not a speedup, but rather a required fix for correct batched results.

## Headline numbers

| Run | Wall baseline to overlap | Gain | Accuracy base vs overlap |
|-----|-------------------------|------|---------------------------|
| FAST 1k | 11.9 to 10.7 s | **10.4%** | identical (0.940763) |
| FAST 20k | 210.4 to 186.2 s | **11.5%** | identical (0.939375) |
| HAC 1k | 48.4 to 46.6 s | **3.8%** | identical (0.976852) |
| HAC 20k | 950.7 to 932.8 s | **1.9%** | identical (0.977333) |

**Why FAST is much greater than HAC:** FAST has a large decode share to hide; HAC is almost entirely LSTM infer, meaning there is very little decode left for me to overlap.

## Nsight

Baseline CUDA API time is mostly spent **waiting** (`cudaStreamSynchronize` and `cudaDeviceSynchronize`). My overlap implementation **removes device-wide sync** from the dominant list and waits more narrowly on the decode stream. GPU kernels stay the same hotspots (FAST: LSTM and beam search; HAC: GEMM/RNN cell and beam search). Overlap does not delete that work; it simply runs infer and decode together. On my overlap runs, `cudaFreeHost` often looks expensive on the host API timeline, which represents a possible next cleanup target for me.

## Host timers

Under overlap, slorado’s printed `inference:` and `decode:` times are **misleading** because decode’s host clock includes waiting while infer runs. I prefer to use **wall time**, **Nsight**, and event-based decode phases.

---

# Part B: Report draft stuff

## 1. Motivation

Slorado’s GPU basecall path is inference (conv + bidirectional LSTMs + CRF head) followed by openfish CRF decode (scan, beam search, quals, sequence, D2H). Serially, these phases do not overlap. On FAST, decode is a large fraction of time, so overlapping decode of batch *N* with inference of batch *N+1* can reduce wall clock without changing basecalls.

## 2. Implementation

### 2.1 Openfish
- `openfish_decode_gpu(..., stream)`: kernels and D2H on caller stream (`NULL` for legacy behavior).  
- Pinned host buffers (`cudaHostAlloc`) and `openfish_decode_free_host`.  
- No mid-phase `cudaDeviceSynchronize` and no final sync when `stream != NULL` (the caller syncs instead).  
- CUDA-event phase timing and `openfish_decode_stats_finish` after caller sync.

### 2.2 Slorado
- CLI `--overlap-decode=yes|no` (defaulting to no).  
- Depth-1 ping-pong: dual input tensors, infer/decode streams, and events.  
- Order: **queue decode(N−1) -> infer(N) -> sync decode stream -> write results**.  
- I set `sync_layers = 0` when overlapping to skip per-layer stream sync in CRFModel.

### 2.3 Correctness
Openfish expects scores in **NTC** layout `[N,T,C]`. Removing the TNC transpose restored my batched accuracy (`-C 128`).

## 3. Methods

- **Timing:** `[main] Real time`; profiles via `nsys` (`nsight_runner.sh`).  
- **Accuracy:** `minimap2 -cx map-ont --secondary=no` vs hg38; median PAF identity. Expected on `reads_1k`: FAST ≈ 0.940696, HAC ≈ 0.976852.

## 4. Results

### 4.1 Accuracy

| Output | Reads | Median ID | vs expected | vs pair |
|--------|------:|----------:|------------:|---------|
| FAST 1k / overlap | 1000 | 0.940763 | +0.000067 | identical |
| FAST 20k / overlap | 20000 | 0.939375 | — | identical |
| HAC 1k / overlap | 1000 | 0.976852 | 0 | identical |
| HAC 20k / overlap | 20000 | 0.977333 | — | identical |

### 4.2 Wall-clock throughput

| Config | Baseline (s) | Overlap (s) | Δ (s) | Improvement |
|--------|-------------:|------------:|------:|------------:|
| FAST 1k | 11.895 | 10.656 | −1.24 | **10.4%** |
| FAST 20k | 210.434 | 186.164 | −24.27 | **11.5%** |
| HAC 1k | 48.445 | 46.619 | −1.83 | **3.8%** |
| HAC 20k | 950.664 | 932.815 | −17.85 | **1.9%** |

Baseline phase context (serial host timers, meaningful only without overlap):

- **FAST 20k:** infer ≈ 130 s, decode ≈ 67 s, meaning decode is large enough to hide.  
- **HAC 20k:** infer ≈ 849 s (RNNs ≈ 751 s), decode ≈ 85 s. Decode takes about 9% of basecall, leading to a small relative gain.

### 4.3 Nsight Systems evidence

Profiles: `nsys_{fast,hac}_{1k,20k}_{base,overlap}.nsys-rep`. Open with `nsys-ui <file>.nsys-rep`.  
Reports below from `nsys stats` CUDA API and GPU kernel summaries.

#### 4.3.1 CUDA API: waiting pattern

**FAST (API time dominated by syncs, followed by stream waits and frees):**

| Profile | Top API signals |
|---------|-----------------|
| FAST 1k base | `cudaStreamSynchronize` ~60%, `cudaDeviceSynchronize` ~33% |
| FAST 1k overlap | `cudaDeviceSynchronize` gone from top; `cudaFreeHost` ~46%, `cudaStreamSynchronize` ~40% (far fewer sync calls) |
| FAST 20k base | `cudaStreamSynchronize` ~63%, `cudaDeviceSynchronize` ~35% |
| FAST 20k overlap | `cudaDeviceSynchronize` gone; `cudaFreeHost` ~50%, `cudaStreamSynchronize` ~47% (≈1.6k vs ≈17.6k stream syncs) |

**HAC (infer-heavy; launch volume dominates API % under overlap):**

| Profile | Top API signals |
|---------|-----------------|
| HAC 1k base | `cudaLaunchKernel` ~58%, `cudaStreamSynchronize` ~30%, `cudaDeviceSynchronize` ~10% |
| HAC 1k overlap | `cudaLaunchKernel` ~88%; `cudaDeviceSynchronize` gone; `cudaFreeHost` ~8%; stream sync small |
| HAC 20k base | `cudaLaunchKernel` ~58%, `cudaStreamSynchronize` ~29%, `cudaDeviceSynchronize` ~10% |
| HAC 20k overlap | `cudaLaunchKernel` ~90%; `cudaFreeHost` ~8%; stream sync much less than 1% of API time |

**Interpretation:** Overlap removes **device-wide** synchronisation from the critical path. Remaining waits are narrower, dealing with the decode stream or pinned-buffer freeing. The huge `cudaLaunchKernel` percentage on HAC reflects many small LSTM/GEMM launches rather than a failure of my overlap path.

#### 4.3.2 GPU kernels: where time goes

| Model | Dominant kernels (both base and overlap) |
|-------|----------------------------------------|
| **FAST** | LSTM (`RNN_blockPersist…`) ≈ 23–26%; `beam_search` ≈ 25%; plus GEMM / `fwd_post_scan` |
| **HAC** | cuDNN GEMM + `elemWiseRNNcell` ≈ 50%+ combined; `beam_search` ≈ 5%; decode is a minority |

Under overlap, decode kernels (such as beam search and `fwd_post_scan`) are often slightly slower per call due to GPU contention. Absolute LSTM work stays similar. Kernel-time percentages can reshuffle because concurrent streams make the sum of kernel durations exceed exclusive wall time.

#### 4.3.3 Memory ops

H2D and D2H memcpy totals are small compared to compute. Overlap may show a higher D2H *time share* under contention, though this is not the main bottleneck.

## 5. Discussion

1. **FAST:** my overlap implementation is an effective systems win (around 10 to 12% wall-clock reduction) at matched accuracy. Nsight confirms a sync-bound baseline and a concurrent-capable overlap path.  
2. **HAC:** infer-bound with a minor 2 to 4% wall gain. Nsight shows LSTM and GEMM dominate the run, meaning my next levers are production sync gating and quantisation, rather than more decode overlap.  
3. **`cudaFreeHost` on overlap:** large host API cost that is a candidate for my follow-up work, specifically buffer reuse to avoid freeing on the critical path.  
4. I will report **wall time and Nsight**, rather than skewed host `decode:` timers under overlap conditions.

## 6. Conclusions

My depth-1 infer/decode overlap (CUDA streams, pinned D2H, sync policy) plus the NTC score-layout fix yielded the following results:

- Accuracy remained unchanged versus the baseline on all tested FAST/HAC 1k/20k sets.  
- FAST 20k achieved **+11.5%** throughput, while HAC 20k saw a **+1.9%** improvement.  
- Nsight reports show the baseline is wait-heavy (`DeviceSynchronize`), whereas my overlap eliminates device-wide sync dominance while preserving LSTM and beam-search as the real GPU work.

**Next steps:** I need to gate profiling syncs for production; address pinned free costs; and implement PTQ and LSTM optimisations for HAC.

## 7. Reproducibility

```bash
./slorado basecaller -C 128 -o output_fast_20k.fastq \
  models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 test/PGXXXX230339/reads_20k.blow5

./slorado basecaller --overlap-decode=yes -C 128 -o output_fast_20k_overlap.fastq \
  models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 test/PGXXXX230339/reads_20k.blow5

# see also nsight_runner.sh
nsys-ui nsys_fast_20k_base.nsys-rep
