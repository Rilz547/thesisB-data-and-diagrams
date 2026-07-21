# Overlap v1.1 — Persistent pinned host decode buffers

Commits: slorado `9f8db24` · openfish `cf182a7`
Scope: **what changed** in v1.1. Full cross-version measurements are in the overall report.

## Motivation

v1.0 profiling exposed the next bottleneck: the decode output buffers were `cudaHostAlloc`'d and `cudaFreeHost`'d **every** decode call. On FAST 20k overlap that free was ~51% of CUDA API time (2,400 calls) with a heavy tail — freeing page-locked memory forces OS work (unmap / page-table / TLB updates), and on Jetson those pages share the CPU's RAM. Under overlap the free also collided with the next batch's in-flight inference, so its cost (and variance) leaked onto the host critical path.

## What changed — openfish

The device-side output buffers already lived on `openfish_gpubuf_t` (allocated once, reused). v1.1 mirrors that for the host buffers:

- Add persistent pinned buffers `moves_host` / `sequence_host` / `qstring_host` to `openfish_gpubuf_t`.
- Allocate them once in `openfish_gpubuf_init`, free once in `openfish_gpubuf_free`.
- `openfish_decode_gpu` returns pointers into these persistent buffers instead of allocating per call.
- `openfish_decode_free_host` becomes a **no-op** on CUDA (kept for API stability).

## What changed — slorado

- Remove the per-batch `openfish_decode_free_host` calls on the GPU path — in **both** the serial `decode_scores_to_chunks` and the overlap `overlap_finalize_decode`.
- CPU decode path is unchanged (it allocates with `malloc`/`free`).
- Stashed output pointers are cleared after `write_decode_results` (not freed).

## Correctness

Buffer-lifetime change only — the decode kernels and the copied bytes are unchanged, so basecalls are identical. Safe under depth-1 overlap because `write_decode_results` copies the bytes out into each chunk before the next batch's decode reuses the buffer.

## Measured effect (headline; full tables in the overall report)

- **`cudaFreeHost` 51% → 0.0%** on FAST 20k overlap (2,400 → 3 teardown calls). The residual once-per-batch host wait relocates onto `cudaMemcpyAsync` / `cudaStreamSynchronize` — pure waits with no OS page-fault tail.
- **Clear second wall-time gain** on the overlap path: FAST 20k overlap 179.8 → 175.6 s (overlap-vs-baseline rises from +11.7% to +13.8%); HAC 20k +2.5% → +2.7%.
- **Large jitter reduction:** FAST 1k baseline CV falls from 2.69% (with the 12.37 s outlier) to 0.19%; every config is now sub-0.6% CV. The baseline path is stabilised too, since the change applies to both paths.