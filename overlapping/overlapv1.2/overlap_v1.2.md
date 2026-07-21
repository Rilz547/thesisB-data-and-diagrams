# Overlap v1.2 — 2-slot pinned host ring + CPU write under next decode ("P5-lite")

Commits: slorado `7cec6c5` · openfish `223f26c`
Scope: **what changed** in v1.2. Full cross-version measurements are in the overall report.

## Motivation

After v1.1, the residual host cost under overlap is the once-per-batch **rendezvous**: for each batch the CPU synchronises the decode stream, copies the results out (`write_decode_results`), and only then launches the next decode. v1.2 lets that CPU copy-out run **concurrently with the next batch's GPU decode**, and introduces the buffer ring needed to make that safe (and to enable deeper pipelining later).

## What changed — openfish

- Replace the single persistent host buffer with a **2-slot ring**: `OPENFISH_HOST_RING = 2`, so `moves_host[2]` / `sequence_host[2]` / `qstring_host[2]` on `openfish_gpubuf_t`.
- `openfish_decode_gpu` gains a `host_slot` argument (serial path uses slot 0); both slots are allocated once in `openfish_gpubuf_init` and freed in `openfish_gpubuf_free`.
- HIP/Metal decode stubs updated for the new signature (serial-path slot 0).

## What changed — slorado

- `overlap_pending_t` gains a `host_slot` (separate from the input/`infer_event` ping-pong `slot`).
- Overlap loop restructured — the old `overlap_finalize_decode` is split into `overlap_sync_decode` and `overlap_write_decode`, and the per-batch order becomes:
  1. **sync** the previous batch's decode,
  2. **launch** the current batch's decode into the *other* host slot,
  3. run `write_decode_results(prev)` on the **CPU while the current decode runs on the GPU**.
- Added the `rileys-runner.py` benchmark harness used for this round of testing (cache warmup + N timed `/dev/null` runs + a separate nsys/accuracy pass).

## Correctness

The 2-slot ring is what makes the concurrent CPU write safe: the previous batch's results sit in slot A while the current decode writes slot B, so the copy-out can proceed without racing the next D2H. Basecalls are identical.

## Measured effect (honest; full tables in the overall report)

- **Wall time is essentially level with v1.1**, within run-to-run noise: FAST 20k overlap 175.6 → 175.5 s; HAC 20k 875.4 → 874.0 s; FAST 1k / HAC 1k within ±0.4%. The Nsight API profile on FAST 20k overlap is unchanged from v1.1 (`cudaMemcpyAsync` ~50% + `cudaStreamSynchronize` ~48%; `cudaFreeHost` still ~0%).
- **Interpretation:** at depth-1 the host critical path is dominated by the decode-stream sync / D2H wait, not by the CPU copy-out — so overlapping the copy-out doesn't move wall time much. The real value of v1.2 is the **ring**, which is the prerequisite for a **depth-2 pipeline** (the logical next optimisation). No regression, and the infrastructure is now in place.