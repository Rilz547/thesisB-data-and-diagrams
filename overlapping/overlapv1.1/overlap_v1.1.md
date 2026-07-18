# Infer∥Decode Overlap v1.1 — Persistent Pinned Host Decode Buffers

Hardware: Jetson Orin · slorado 0.5.0-beta · openfish (stream-aware + persistent hosts)
Models: FAST / HAC `dna_r10.4.1_e8.2_400bps_*@v5.0.0` · `-C 128` · `reads_{1k,20k}.blow5`
Commits: slorado `9f8db24` *(Overlap v1.1)* · openfish `cf182a7` *(Overlap v1.1)*
Relationship to v1: builds directly on the depth-1 infer∥decode overlap (v1). This change (v1.1) addresses the bottleneck v1 profiling exposed.

---

# Part A — Simple notes

## What the problem was

v1 overlap worked (FAST 20k ≈+11.5% wall, matched accuracy), but Nsight on the overlap runs showed a new top cost: **`cudaFreeHost` ≈ 50% of CUDA API time** on FAST 20k overlap — 2,400 calls, median ≈28 µs but mean ≈34 ms with a heavy tail (max ≈138 ms).

Cause: openfish `cudaHostAlloc`'d the pinned host decode outputs (`moves`/`sequence`/`qstring`) **every** decode and freed them right after. Freeing page-locked memory forces real OS work (unlock/unmap, page-table/TLB updates), and on Jetson those pages share the CPU's physical RAM. Under overlap the free also lands while the next batch's inference is running, so it partly serialized against in-flight GPU work.

## What we changed (v1.1)

Allocate the pinned host buffers **once per runner** (on the openfish gpubuf, same lifetime as the device scratch that was already persistent), **reuse** them every decode, **free once** at teardown. Per-batch alloc/free → zero in steady state.

## Headline result

`cudaFreeHost` on the hot path is **gone**, and it stayed gone across every profile:

| Profile (v1.1) | cudaFreeHost calls | cudaFreeHost % of API |
|---|---:|---:|
| FAST 20k overlap | **3** (was 2,400) | **0.0%** (was 50.2%) |
| FAST 20k base | 3 | 0.0% |
| FAST 1k base / overlap | 3 / 3 | 0.0% / 0.0% |
| HAC 20k base / overlap | 3 / 3 | 0.0% / 0.0% |
| HAC 1k base / overlap | 3 / 3 | 0.0% / 0.0% |

The 3 remaining frees are the three persistent buffers released once at gpubuf teardown — exactly as designed.

## Wall clock (this dataset, under `nsys`, `-C 128`)

| Config | Baseline (s) | Overlap + v1.1 (s) | Overlap gain |
|---|---:|---:|---:|
| FAST 1k | 11.879 | 10.379 | 12.6% |
| FAST 20k | 210.780 | 177.662 | **15.7%** |
| HAC 1k | 48.499 | 46.176 | 4.8% |
| HAC 20k | 953.282 | 930.629 | 2.4% |

## v1 vs v1.1

v1 delivered the overlap schedule; v1.1 removes the per-batch pinned host alloc/free that v1's own profiling exposed as the next bottleneck. The overlap schedule and the basecalls are unchanged — v1.1 only changes how the decode-output buffers are managed.

| Overlap path | v1 (overlap only) | v1.1 (+ persistent host buffers) |
|---|---|---|
| FAST 20k overlap wall (under `nsys`) | ≈186.2 s | **177.7 s** (≈4.6% faster) |
| Baseline wall (FAST 20k) | ≈210.4 s | 210.8 s (unchanged) |
| `cudaFreeHost` (FAST 20k overlap) | 50.2% · 2,400 calls · 82 s · max 138 ms | 0.0% · 3 calls · 37 µs |
| Host buffer alloc/free | every decode | once per runner |
| Run-to-run timing | jittery (free tail on critical path) | tight (see below) |
| Accuracy | identical | identical |

The gain is overlap-specific: v1.1 leaves the serial baseline unchanged (the frees were cheap without concurrent work) and only speeds the overlap path, where the free had been colliding with in-flight inference. Both v1 and v1.1 wall figures here are under `nsys`; expect a small profiler overhead vs off-profiler runs.

## Steadier timing (less run-to-run jitter)

Alongside the mean, the bigger practical win is variance. In v1, every batch did a pinned free whose cost was wildly variable: across the 2,400 frees in a FAST 20k overlap run, median ≈28 µs but mean ≈34 ms, max ≈138 ms, stddev ≈49 ms — roughly a 5,000× spread between the typical and worst free inside a single run. Because that free sat on the host critical path (and under overlap collided with the next batch's inference), its variance leaked straight into per-batch wall time and therefore into total run time.

v1.1 removes it — 2,400 per-batch frees → 3 at teardown — so the high-variance operation is gone from steady state. Per-batch timing is now uniform and repeated runs land in a narrow band: off-profiler FAST 1k ≈ 10.10–10.15 s (≈50 ms spread, ≈0.5%) and HAC 1k ≈ 45.12–45.18 s (≈60 ms, ≈0.13%). For the writeup this matters as much as the speedup: it makes the overlap benchmark reproducible and the reported gains trustworthy.

## Nsight in one paragraph

`cudaFreeHost` drops from 50.2% to 0.0% on FAST 20k overlap. The mandatory once-per-batch host↔GPU rendezvous that depth-1 overlap requires does **not** disappear — it re-attributes onto `cudaMemcpyAsync` (0.8% → 49.9%) while `cudaStreamSynchronize` is basically unchanged (46.9% → 47.9%). Crucially, the GPU-side transfer those memcpys represent is tiny (H2D 466 ms + D2H 47 ms over the whole 20k run), so that 49.9% is overwhelmingly *wait*, not copy work — and unlike the old free, it carries no OS page-fault cost. Net: wall time down, timing much steadier, basecalls unchanged.

## Host-timer caveat (unchanged from v1)

Under overlap, slorado's printed `inference:` / `decode:` times are misleading — the decode host clock includes waiting while infer runs (e.g. FAST 20k overlap prints decode ≈ 164 s, which is not real decode work). Use **wall time + Nsight** for anything quantitative; use the **baseline** host timers for serial phase context.

---

# Part B — Report draft

## 1. Motivation

v1 introduced depth-1 infer∥decode overlap: while decoding batch *N*, infer batch *N+1* on a second CUDA stream. It removed device-wide synchronisation from the critical path and delivered a wall-clock win at matched accuracy. Profiling the overlap runs then surfaced the next bottleneck — per-batch pinned-host allocation/free of the decode outputs, dominated by `cudaFreeHost`. This work (v1.1) removes that churn.

## 2. Implementation

The device-side decode outputs (`gpubuf->moves/sequence/qstring`) were already allocated once in `openfish_gpubuf_init` and freed in `openfish_gpubuf_free`. Only the **pinned host** copies were per-call. v1.1 mirrors the device pattern for the host buffers.

### 2.1 Openfish (`decode-profiling`, `cf182a7`)
- `openfish_gpubuf_t` gains `moves_host`, `sequence_host`, `qstring_host`.
- `openfish_gpubuf_init`: `cudaHostAlloc` the three, sized `batch_size × n_timesteps` (same as the device counterparts).
- `openfish_gpubuf_free`: `cudaFreeHost` the three.
- `openfish_decode_gpu`: no per-call alloc; assigns `*moves = gpubuf->moves_host` etc. The existing async D2H (`cudaMemcpyAsync` into `*moves`) is unchanged and now targets the persistent buffer.
- `openfish_decode_free_host`: **no-op** on CUDA (symbol kept for API stability).
- `openfish_gpubuf_size` accounts for the three host buffers; HIP/Metal backends NULL-init the new fields (Jetson/CUDA is the focus; those backends keep per-call malloc/free).

### 2.2 Slorado (`develop`, `9f8db24`)
- Remove the GPU-path `openfish_decode_free_host` calls in both the serial `decode_scores_to_chunks` and `overlap_finalize_decode`.
- CPU path still `free(...)` (CPU decode allocates with `malloc`, not pinned).
- Stashed output pointers are cleared after `write_decode_results` (not freed).
- gpubuf is created once per runner at `torchbox.cpp:120` as `openfish_gpubuf_init(chunk_size/model_stride, gpu_batch_size, state_len)`, so the persistent host buffers are sized to the max batch and cover every decode.

### 2.3 Correctness invariant
Safe under depth-1 overlap because at most one decode's outputs are live at a time: `overlap_finalize_decode` runs `write_decode_results` (copying bytes into each chunk's `std::string`/`vector`) **before** the next batch's `overlap_launch_decode` issues the next D2H into the same buffer. The serial path is trivially safe. The change applies to **both** GPU paths (not overlap-only). If depth ever exceeds 1 (depth-2, or a second runner sharing a gpubuf), promote the three `*_host` buffers to a 2-slot ring indexed like `input_tensor`/`input_tensor_alt`.

Because v1.1 alters only buffer lifetime — not decode kernels or the copied bytes — basecalls are bit-identical to v1 by construction.

## 3. Methods

- **Timing:** `[main] Real time`; profiles via `nsys` (`nsight_runner.sh`). All wall numbers here are under `nsys` unless noted; expect a small profiler overhead vs off-profiler runs.
- **Accuracy:** `minimap2 -cx map-ont --secondary=no` vs hg38; median PAF identity. Reference (v1, unchanged expectation): FAST ≈ 0.9408 (1k) / 0.9394 (20k), HAC ≈ 0.9769 (1k) / 0.9773 (20k).
- **Data/config:** `reads_{1k,20k}.blow5`, `-C 128`, chunk 12288, overlap 150.

## 4. Results

### 4.1 Accuracy

Confirmed. Base and overlap produce identical basecalls on every pair — identical read counts, alignment counts, map%, and median PAF identity — and match the v1 / expected references. Since v1.1 changes only buffer lifetime, this is the expected result.

| Config | Reads | Alns | Map% | Median ID (base = overlap) | vs expected |
|--------|------:|-----:|-----:|:--------------------------:|:------------|
| FAST 1k  | 1000  | 1031  | 103.1% | 0.940763 | +0.000067 |
| FAST 20k | 20000 | 20727 | 103.6% | 0.939375 | — |
| HAC 1k   | 1000  | 1105  | 110.5% | 0.976852 | −0.000000 |
| HAC 20k  | 20000 | 22274 | 111.4% | 0.977333 | — |

Base and overlap are line-for-line identical in every column above (the profile pair only differs in the `.nsys-rep` it was measured under), so the buffer-lifetime change is verified output-preserving — not merely expected to be.

### 4.2 Wall-clock throughput

| Config | Baseline (s) | Overlap + v1.1 (s) | Δ (s) | Improvement |
|--------|-------------:|-----------------:|------:|------------:|
| FAST 1k  | 11.879  | 10.379  | −1.50  | 12.6% |
| FAST 20k | 210.780 | 177.662 | −33.12 | **15.7%** |
| HAC 1k   | 48.499  | 46.176  | −2.32  | 4.8% |
| HAC 20k  | 953.282 | 930.629 | −22.65 | 2.4% |

Baseline phase context (serial host timers, meaningful only without overlap):
- **FAST 20k base:** inference 129.8 s (rnns 92.0 s, conv 29.4 s), decode 66.9 s (beam_search 46.8 s) → decode ≈ 34% of basecall → large share to hide.
- **HAC 20k base:** inference 851.3 s (rnns 752.6 s), decode 84.4 s → decode ≈ 9% of basecall → little to hide (infer-bound), hence the small HAC gain.

### 4.3 Nsight — CUDA API, v1 → v1.1 (FAST 20k overlap)

The `cudaFreeHost` cost is eliminated and the residual overlap wait relocates to `cudaMemcpyAsync`:

| CUDA API call | v1 | v1.1 |
|---|---|---|
| `cudaFreeHost` | 50.2% · 2,400 calls · 82.07 s · mean 34 ms · max 138 ms | **0.0% · 3 calls · 37 µs** |
| `cudaStreamSynchronize` | 46.9% · 1,627 calls · mean 47.1 ms | 47.9% · 1,627 calls · mean 45.7 ms |
| `cudaMemcpyAsync` | 0.8% · 3,247 calls | 49.9% · 3,247 calls · mean 23.9 ms |
| `cudaDeviceSynchronize` | absent (removed at v1) | absent |

**Reading it:** the once-per-batch decode-stream sync (`cudaStreamSynchronize`, 1,627 = one per GPU batch) was always the intended rendezvous and is unchanged. The *second* large cost swapped identity: pinned-free → async-memcpy. That the swap is a re-attribution and not new work is proven by the GPU MemOps below.

### 4.4 Nsight — GPU MemOps (FAST 20k overlap, v1.1): memcpy API time ≠ transfer

| Operation | Total time | Count |
|---|---:|---:|
| CUDA memcpy Host-to-Device | 466 ms | 827 |
| CUDA memcpy Device-to-Host | 47 ms | 2,400 |
| CUDA memset | 25 ms | 2,402 |

Actual on-GPU transfer totals ≈0.5 s, versus 77.5 s of `cudaMemcpyAsync` *API* time — i.e. the API time is ≈99% host-side wait (the async issue blocking on the busy decode stream), not copying. For comparison, the same 3,247 `cudaMemcpyAsync` calls cost only 0.55 s (0.3%) in the serial baseline, confirming the extra time is overlap-attribution, not work.

### 4.5 Nsight — GPU kernels unchanged (FAST 20k overlap, v1.1)

Same hotspots and shares as baseline, confirming v1.1 is a host-side memory-management change with no effect on compute or output:

| Kernel | Share |
|---|---:|
| `beam_search<__half>` | 25.0% |
| `RNN_blockPersist…LSTM` | 23.2% |
| `ampere_fp16 s1688gemm` | 10.7% |
| `fwd_post_scan<__half>` | 6.6% |
| `flip_kernel_impl` | 5.6% |

(The `flip_kernel_impl` at 5.6% is the alternating-direction LSTM's `flip(1)` copies — a known, separate future lever, not affected here.)

### 4.6 HAC 20k overlap (v1.1) — launch-bound, as expected

`cudaLaunchKernel` 90.7% (16.7 M launches), `cudaMemcpyAsync` 7.6%, `cudaStreamSynchronize` 0.1%, `cudaFreeHost` 0.0%. HAC's cost is kernel launch + LSTM/GEMM compute; the pinned-free fix removes the small residual host cost but cannot move an infer-bound model much.

## 5. Discussion

1. **v1.1 did exactly what it targeted:** `cudaFreeHost` fell from 50.2% to 0.0% of CUDA API time on FAST 20k overlap (2,400 → 3 calls), consistently across all eight profiles. The heavy per-call tail (up to 138 ms) is gone.
2. **Modest, real wall-clock win + stability:** ≈4.6% off the FAST 20k overlap wall vs v1, baseline unchanged, and much tighter run-to-run spread — the expected profile for removing allocator jitter rather than compute.
3. **The residual is now a scheduling wait, not memory churn.** The mandatory depth-1 rendezvous re-attributes to `cudaMemcpyAsync`/`cudaStreamSynchronize`, backed by only ≈0.5 s of real transfer. This is the correct next target: a depth-2 pipeline or asynchronous result collection would attack the rendezvous itself. That work depends on turning the (now persistent) host buffers into a small ring — the v1.1 design was chosen to make that straightforward.
4. **Memory cost of overlap:** peak RAM is higher with overlap (e.g. FAST 20k 4.69 → 5.68 GB; HAC 20k 6.04 → 6.25 GB) from the dual input buffers, persistent pinned buffers, and concurrent activations — comfortable on the 16 GB Orin but worth tracking if `-C` grows.
5. **HAC remains infer-bound;** its levers are LSTM/GEMM (e.g. production sync gating, quantisation, or eliminating the flip copies), not decode-side host work.

## 6. Conclusions

Persistent pinned host decode buffers (allocate-once, reuse, free-at-teardown), on top of v1 depth-1 overlap:
- **`cudaFreeHost` off the hot path:** 50.2% → 0.0% on FAST 20k overlap; 3 teardown frees everywhere.
- **Basecalls unchanged** (buffer-lifetime change only) — confirmed: base and overlap give identical median identity on all four pairs (FAST 0.940763 / 0.939375, HAC 0.976852 / 0.977333), matching expected.
- **FAST 20k:** overlap+v1.1 vs baseline ≈ **15.7%** wall; ≈4.6% of that is v1.1 over v1 overlap, plus markedly steadier timing.
- **HAC:** small (2–5%), infer-bound as before.
- Nsight confirms the FreeHost stall is replaced by a pure scheduling wait with no OS page-fault cost.

**Next:** production sync gating; depth-2 / async result collection (needs a host-buffer ring); LSTM-side work (flip-copy elimination, quantisation) for HAC.

## 7. Reproducibility

```bash
# baseline
./slorado basecaller -C 128 -o output_fast_20k.fastq \
  models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 test/PGXXXX230339/reads_20k.blow5

# overlap + v1.1
./slorado basecaller --overlap-decode=yes -C 128 -o output_fast_20k_overlap.fastq \
  models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 test/PGXXXX230339/reads_20k.blow5

# profiles via nsight_runner.sh; inspect:
#   nsys stats --report cuda_api_sum <file>.nsys-rep      # cudaFreeHost -> 0.0%
#   nsys stats --report cuda_gpu_mem_time_sum <file>.nsys-rep  # ~0.5 s real transfer
```
Commits: slorado `9f8db24`, openfish `cf182a7` (both tagged *Overlap v1.1*).