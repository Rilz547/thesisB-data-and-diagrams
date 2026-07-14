# Infer∥Decode Overlap — Notes + Report Draft

Hardware: Jetson Orin · slorado 0.5.0-beta · openfish (stream-aware)  
Models: FAST / HAC `dna_r10.4.1_e8.2_400bps_*@v5.0.0` · `-C 128` · `reads_{1k,20k}.blow5`

---

# Part A — Simple notes (for you / demonstrator chat)

## What the idea is

Each GPU batch does two jobs:

1. **Infer** — neural net → scores  
2. **Decode** — openfish → DNA letters + qualities  

**Baseline:** finish infer, then decode, then next batch.  
**Overlap (`--overlap-decode=yes`):** while decoding batch *N*, start inferring batch *N+1* on a second CUDA stream.

Same answers; better schedule.

## What we changed (short)

1. **Two GPU lanes** — infer stream + decode stream, ping-pong input buffers.  
2. **Fewer “wait for the whole GPU” calls** — skip per-layer / device-wide syncs when overlapping; only sync the decode stream when we need the bases.  
3. **Pinned host memory for decode outputs** — so GPU→CPU copies don’t secretly stall.  
4. **Openfish takes a stream** — decode kernels run on the decode lane.  
5. **NTC layout fix** — stop flipping scores to TNC (broke accuracy for `-C > 1`). Not a speedup; required for correct batched results.

## Headline numbers

| Run | Wall baseline → overlap | Gain | Accuracy base vs overlap |
|-----|-------------------------|------|---------------------------|
| FAST 1k | 11.9 → 10.7 s | **10.4%** | identical (0.940763) |
| FAST 20k | 210.4 → 186.2 s | **11.5%** | identical (0.939375) |
| HAC 1k | 48.4 → 46.6 s | **3.8%** | identical (0.976852) |
| HAC 20k | 950.7 → 932.8 s | **1.9%** | identical (0.977333) |

**Why FAST ≫ HAC:** FAST has a large decode share to hide; HAC is almost all LSTM infer, so little decode left to overlap.

## Nsight in one paragraph

Baseline CUDA API time is mostly **waiting** (`cudaStreamSynchronize` + `cudaDeviceSynchronize`). Overlap **removes device-wide sync** from the dominant list and waits more narrowly on the decode stream. GPU kernels stay the same hotspots (FAST: LSTM + beam search; HAC: GEMM/RNN cell + beam search). Overlap does not delete that work — it runs infer and decode together. On overlap runs, `cudaFreeHost` often looks expensive on the host API timeline (possible next cleanup).

## How to talk about host timers

Under overlap, slorado’s printed `inference:` / `decode:` times are **misleading** (decode’s host clock includes waiting while infer runs). Prefer **wall time**, **Nsight**, and event-based decode phases.

---

# Part B — Report draft

## 1. Motivation

Slorado’s GPU basecall path is inference (conv + bidirectional LSTMs + CRF head) followed by openfish CRF decode (scan, beam search, quals, sequence, D2H). Serially, these phases do not overlap. On FAST, decode is a large fraction of time, so overlapping decode of batch *N* with inference of batch *N+1* can reduce wall clock without changing basecalls.

## 2. Implementation

### 2.1 Openfish
- `openfish_decode_gpu(..., stream)` — kernels + D2H on caller stream (`NULL` = legacy).  
- Pinned host buffers (`cudaHostAlloc`); `openfish_decode_free_host`.  
- No mid-phase `cudaDeviceSynchronize` / no final sync when `stream != NULL` (caller syncs).  
- CUDA-event phase timing + `openfish_decode_stats_finish` after caller sync.

### 2.2 Slorado
- CLI `--overlap-decode=yes|no` (default no).  
- Depth-1 ping-pong: dual input tensors, infer/decode streams, events.  
- Order: **queue decode(N−1) → infer(N) → sync decode stream → write results**.  
- `sync_layers = 0` when overlapping (skip per-layer stream sync in CRFModel).

### 2.3 Correctness
Openfish expects scores **NTC** `[N,T,C]`. Removing the TNC transpose restored batched accuracy (`-C 128`).

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

- **FAST 20k:** infer ≈ 130 s, decode ≈ 67 s → decode large enough to hide.  
- **HAC 20k:** infer ≈ 849 s (RNNs ≈ 751 s), decode ≈ 85 s → decode ~9% of basecall → small relative gain.

### 4.3 Nsight Systems evidence

Profiles: `nsys_{fast,hac}_{1k,20k}_{base,overlap}.nsys-rep`. Open with `nsys-ui <file>.nsys-rep`.  
Reports below from `nsys stats` CUDA API + GPU kernel summaries.

#### 4.3.1 CUDA API — waiting pattern

**FAST (API time dominated by syncs → then stream waits / frees):**

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
| HAC 20k overlap | `cudaLaunchKernel` ~90%; `cudaFreeHost` ~8%; stream sync ≪1% of API time |

**Interpretation:** Overlap removes **device-wide** synchronisation from the critical path. Remaining waits are narrower (decode stream and/or pinned-buffer free). Huge `cudaLaunchKernel` % on HAC reflects many small LSTM/GEMM launches, not “overlap failed.”

#### 4.3.2 GPU kernels — where time goes

| Model | Dominant kernels (both base & overlap) |
|-------|----------------------------------------|
| **FAST** | LSTM (`RNN_blockPersist…`) ≈ 23–26%; `beam_search` ≈ 25%; plus GEMM / `fwd_post_scan` |
| **HAC** | cuDNN GEMM + `elemWiseRNNcell` ≈ 50%+ combined; `beam_search` ≈ 5%; decode is a minority |

Under overlap, decode kernels (e.g. beam search, `fwd_post_scan`) are often slightly slower per call (GPU contention). Absolute LSTM work stays similar. Kernel-time percentages can reshuffle because concurrent streams make “sum of kernel durations” exceed exclusive wall time.

#### 4.3.3 Memory ops

H2D / D2H memcpy totals are small vs compute. Overlap may show higher D2H *time share* under contention; not the main bottleneck.

## 5. Discussion

1. **FAST:** overlap is an effective systems win (~10–12% wall) at matched accuracy; Nsight confirms sync-bound baseline and concurrent-capable overlap path.  
2. **HAC:** infer-bound; ~2–4% wall gain; Nsight shows LSTM/GEMM dominate — next levers are production sync gating and quantisation, not more decode overlap.  
3. **`cudaFreeHost` on overlap:** large host API cost; candidate follow-up (buffer reuse / avoid free on critical path).  
4. Report **wall time + Nsight**, not skewed host `decode:` timers under overlap.

## 6. Conclusions

Depth-1 infer∥decode overlap (CUDA streams, pinned D2H, sync policy) plus NTC score-layout fix:

- Accuracy unchanged vs baseline on all tested FAST/HAC 1k/20k sets.  
- FAST 20k **+11.5%** throughput; HAC 20k **+1.9%**.  
- Nsight: baseline wait-heavy (`DeviceSynchronize`); overlap eliminates device-wide sync dominance while preserving LSTM + beam-search as the real GPU work.

**Next:** gate profiling syncs for production; address pinned free cost; PTQ / LSTM optimisations for HAC.

## 7. Reproducibility

```bash
./slorado basecaller -C 128 -o output_fast_20k.fastq \
  models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 test/PGXXXX230339/reads_20k.blow5

./slorado basecaller --overlap-decode=yes -C 128 -o output_fast_20k_overlap.fastq \
  models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 test/PGXXXX230339/reads_20k.blow5

# see also nsight_runner.sh
nsys-ui nsys_fast_20k_base.nsys-rep
```
