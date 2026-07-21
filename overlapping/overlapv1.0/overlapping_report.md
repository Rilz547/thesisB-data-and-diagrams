# Overlap v1.0 — Stream-aware infer∥decode overlap

Commits: slorado `e00bada` · openfish `59e12e9`
Scope: this report covers **what changed** in v1.0. Full cross-version measurements are in the overall report.

## Motivation

The serial GPU basecall path runs inference then decode for each batch on a single CUDA stream. While inference runs the decode engine is idle and vice-versa, and the next batch's inference cannot begin until the current batch's decode has finished. v1.0 breaks that serialisation: it runs **decode of batch N−1 concurrently with inference of batch N** on a second CUDA stream, without changing any basecalls.

## What changed — openfish

- `openfish_decode_gpu` gains a `stream` argument; decode kernels and the device→host copy run on the caller's stream (`NULL` = legacy behaviour).
- When a stream is passed, openfish skips its own device-wide `cudaDeviceSynchronize` — the caller is responsible for synchronising.
- Decode outputs (`moves`/`sequence`/`qstring`) are copied into **pinned host** buffers (`cudaHostAlloc`) so the D2H is genuinely asynchronous; freed via `openfish_decode_free_host`. *(At this stage the buffers are allocated and freed per decode call — this is what v1.1 later fixes.)*
- CUDA-event-based decode phase timers, resolved after the caller's sync.

## What changed — slorado

- CLI flag `--overlap-decode=yes|no` (default `no`); the banner prints the mode.
- Per runner: an `infer_stream` and a `decode_stream`, **double-buffered input** (`input_tensor` / `input_tensor_alt`) selected by a ping-pong `slot`, and a per-slot `infer_event`.
- Depth-1 pipeline in `basecall_chunks_overlap`: queue decode(N−1) on the decode stream (which first waits on that batch's `infer_event`) → run `forward(N)` on the infer stream and record its event → synchronise the decode stream → write the previous batch's results.
- `sync_layers = 0` when overlapping, i.e. skip the per-layer `torch::cuda::synchronize` in `CRFModel` (those exist only for profiling and would serialise the stream).

## Correctness

- **Score-layout fix (shipped with v1.0).** openfish consumes scores in **NTC** `[N, T, C]`; slorado had been transposing to TNC. That looked fine at `N = 1` (the layouts coincide) but produced garbage DNA for `-C > 1`. Removing the transpose and passing contiguous NTC restored batched accuracy.
- The overlap changes the *schedule*, not the arithmetic — basecalls are identical to the serial baseline (verified by median PAF identity).

## Measured effect (headline; full tables in the overall report)

- Overlap vs the v1.0 baseline (mean real time): **FAST +9.8% (1k) / +11.7% (20k)**, **HAC +2.0% (1k) / +2.5% (20k)**. FAST has a large decode share to hide behind inference; HAC is infer-bound, so there is little decode to overlap.
- v1.0 also introduces the cost that v1.1 targets: the **per-batch pinned host alloc/free**. Nsight on FAST 20k overlap shows `cudaFreeHost` at ~51% of CUDA API time (2,400 calls, heavy tail), and a run-to-run **jitter outlier** — one FAST 1k baseline run hit 12.37 s versus ~11.4 s typical (CV 2.69%).