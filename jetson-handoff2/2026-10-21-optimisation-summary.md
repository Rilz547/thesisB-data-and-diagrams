# Slorado on Jetson Orin — optimisations tried: what worked, what didn't

**Date:** 2026-10-21 (work done Jun–Oct 2026)
**Platform:** Jetson Orin (Nano-class, ~7.6 GB shared), `nvpmodel` 25W, `jetson_clocks` OFF
**Models / data:** DNA R10.4.1 e8.2 400bps `fast@v5.0.0` and `hac@v5.0.0`; `reads_1k` and `reads_20k`
**Code:** slorado `speculative-decode` (`49ac641`); openfish `speculative-greedy` (`5109673`)
**Shared flags:** `-C 128 -c 12288 -K 4096 -p 150`
**Code lineage (baseline):** derived from upstream `BonsonW/slorado` commit **`754a74d`** ("Merge pull request #31 from BonsonW/dev", 2026-05-07 18:49 +1000), which reports version **0.5.0-beta** and pins openfish `2530c2a` (`warp9seq/openfish`, "comment out gfx1150 custom flag for release"). The repository was cloned 2026-06-23 and the first own commit is 2026-06-24 (`d5a8737`); every branch (`develop`, `speculative-decode`, `cpu-beam`, `temp`) shares merge-base `754a74d`. The Slorado/Openfish preprint used v0.4.0-beta (commit `499b1cb`, 2025-10-30), which is **140 commits earlier**; upstream has no git tags. Openfish was later pointed at the fork `Rilz547/openfish-riley` (2026-07-08, `6977ef3`, tracking upstream `dev` `b9569c6`) and then `speculative-greedy` (`5109673`).

Wall times are slorado's `[main] Real time` (excludes process/Torch startup). Unless stated, runs wrote to `/dev/null` ("raw speed").
A second headline, **end-to-end speed with FASTQ output**, is defined in `measurement-methodology.md` and has not yet been measured under a frozen harness.
Percentages are *time changes versus a named baseline*, `(t_new − t_base) / t_base` (negative = faster); see `measurement-methodology.md` for speed-up and throughput conventions.
**Unless stated, every figure below is from a single run**, so differences of a percent or two are noise.
*Revised 2026-10-22 after a cross-check against the PC data (`REPLY-from-PC-agent-2026-10-22.md`): several figures that originally came from single power-campaign runs were replaced by multi-run values.*

---

## 1. Summary

### What worked

| change | idea | measured effect |
|---|---|---|
| **Overlap decode** (v1.0 → v1.2) | run GPU decode of batch *N−1* concurrently with inference of batch *N* | FAST **−11.5 % (1k) / −13.9 % (20k)** (runner, n = 10/3, v1.2; single power runs showed −17.2 / −15.5 %, which are too high); HAC ≈ −3 % in both sources |
| **Load imbalance** (narrow batches + flush threshold) | stop padding partial batches to a full `-C`-wide GPU launch | Offline (full input) FAST 1k **−3.4 to −5.9 %** depending on the reference (report headline ≈ 4.8 %; the earlier "≈ −12 %" was a single power-run artefact); ≈ 0 on FAST 20k and HAC. The large effect is in the streaming / partial-batch case: **−45 % at flush 64, −77 % at flush 4** versus padded fixed-C batches |
| **Speculative decode** | cheap greedy draft for every chunk; full beam search only for chunks the draft is unsure about | FAST ≈ −7 to −10 % for ≈ −0.5 pp identity; HAC **−1.0 to −1.6 % (1k), −0.2 to −0.7 % (20k)** for ≈ −0.15 pp identity |

### Dead ends

| idea | outcome |
|---|---|
| **CPU beam hybrid** (`cpu-beam`) | slower in every cell: +2 % to +19 % wall in the Aug power runs (and 2.5–3.3× in the July timing report, which measured a different implementation) |
| **Overlap depth-2** (dual decode) | documented as a failed route |
| **`cuda-lstm-gemm`, `flip-spike` branches** | personal tests, no commits of their own |
| **Speculative `m = 3` margin** | FAST: near-beam identity but slower than baseline (accept rate ≈ 7–9 %). HAC: still accepts ≈ 67–71 % but 20k is slower than baseline |

### Qualified result: FAST→HAC cascade (accuracy/speed knob)

Reads are first basecalled with FAST and the least confident are promoted to HAC. From the PC cascade report (`cascade/casecade-final/2026-07-31-cascade-progress-report.md` §6–7;
**taken from the PC cross-check, not re-verified on the Jetson**): from `force_frac` 0.30 the median identity beats FAST (+0.0018 on 1k, +0.0007 on 20k); at 0.40 the gain is +0.0066 / +0.0061.
On 1k it is **1.56× faster than HAC** measured against the flush = 128 HAC anchor (1.7× against the report's flush = 64 anchor, which is 8.6 % slower). It is always slower than FAST (2.4–3.1× at 0.30–0.40).
No power or nsys data exist for it, and the 20k results are single runs. It was not pursued further; it is a trade-off knob between the two models, not a speed optimisation of either.

### Investigated and ruled out

| idea | why |
|---|---|
| **Larger batches** (`-C 256`, `-C 512`) | HAC 1k crashed with GPU OOM (`NvMap … error 12`) at both sizes in a 5 Oct test **run with overlap decode and speculative flags on**; `-C 128` already peaks at ≈ 5.0 GB. The PC data shows the same OOM for FAST 1k at `-C 256` with overlap on (~1.08 GB contiguous allocation), whereas June upstream ran FAST at `-C 256` *without* overlap. HAC at `-C 256` with overlap off is untested |
| **Int8 quantisation** | the codebase's int8 path covers only v6 factored-LSTM and transformer models; the fused int8 FLSTM kernel was removed, and `--quant` is never passed to the plain-LSTM loader used by v5.0.0 FAST/HAC. A real int8 LSTM would need new kernels |

### Original versus now — named baselines (no single headline yet)

There is no single, same-harness "original". The Oct runner result (`q10_m2_brave`, 9.023 / 44.679 / 158.803 / 865.431 s) compared with each available baseline
(time change; speed-up in brackets; all single runs unless noted):

| baseline (harness) | FAST 1k | HAC 1k | FAST 20k | HAC 20k |
|---|---|---|---|---|
| overlap-v1.0 runner, `--overlap-decode=no` (same runner harness, warm-up, n = 10/3) | 11.500 s → −21.5 % (1.27×) | −4.0 % | −22.0 % | 902.986 s → −4.2 % (1.04×) |
| `baseline_results_v2` (upstream-style, FAST `-C 256`, FASTQ out, no warm-up) | 12.206 → −26.1 % (1.35×) | 48.375 → −7.6 % (1.08×) | 212.114 → −25.1 % (1.34×) | 927.336 → −6.7 % (1.07×) |
| `pre_overlap` `8873209` (power-record harness, tegrastats on) — **INVALID baseline, see below** | ~~12.974~~ | ~~48.559~~ | ~~217.121~~ | ~~934.324~~ |
| June upstream `754a74d` (FAST `-C 256`, FASTQ out, telemetry on, no warm-up) | 14.212 → −36.5 % (1.58×) | 49.805 → −10.3 % (1.11×) | 247.872 → −35.9 % (1.56×) | 955.152 → −9.4 % (1.10×) |

So the end-to-end gain depends on the baseline: FAST 1k ranges from −21.5 % to −36.5 % and HAC 1k from −4.0 % to −10.3 %. Different harnesses, flags and output modes are mixed, so **no single percentage should be quoted until a same-harness run** (the final harness; see `harness-audit.md`).
**`pre_overlap` (`8873209`) is not a valid baseline (found 2026-10-22, `smoke-test-2026-10-22.md`):** its output is unrelated to every other stage (0/1000 reads identical to `upstream`, ~30 % sequence similarity, on DNA and RNA). At that commit slorado transposes the model output to TNC but the pinned openfish reads NTC, which scrambles every batch of more than one chunk; the fix arrived in v1.0 (`e00bada`). (Dates: `8873209` is 2026-07-11; its pinned openfish `3d95491` is dated 2026-07-08, bumped into the fork by `f70a90d` the same day, so the mismatch existed from the first overlap-era commit that used it.) So the August `pre_overlap` timing and energy rows, and the earlier "original → now" chain built on them, came from a garbled decode and are withdrawn. This also explains why `pre_overlap` looked 3.4–5.1 % slower than `ov_off` in the power harness: that gap is most likely an artefact, not a real effect of host-buffer handling (the earlier code-reading hypothesis is retracted). The valid pre-overlap baselines are `upstream` (`754a74d`) and `ov_off` (`7cec6c5`, overlap off), whose outputs reproduce each other read for read.

**Open question (not resolved):** in the smoke test `upstream` was slower than `ov_off` even though overlap is off in both (FAST 1k, single runs: 14.94 s vs 12.81 s). A code-reading hypothesis is that the fork's pinned, persistent host buffers and asynchronous copies also apply with overlap off; this is **unmeasured** and the final campaign (`upstream` vs `ov_off`, n = 5) will show whether the gap is real.

Stage-by-stage chain from the power campaign (single runs, FAST 20k): ~~217.1 (`pre_overlap`, invalid)~~ → 206.1 (overlap off) → 174.2 (overlap on) → 173.8 (narrow batches) → 161.0 s (speculative). The first step is withdrawn; the chain from overlap-off onward is valid. These are not additive percentages; cumulative factors multiply.

### Final timings (rileys-runner.py, `q10_m2_brave`)

| config | time | median identity | Δ vs baseline |
|---|---|---|---|
| FAST 1k | 9.02 s | 0.935276 | −0.0055 |
| HAC 1k | 44.68 s | 0.975490 | −0.0014 |
| FAST 20k | 158.8 s | 0.934853 | −0.0045 |
| HAC 20k | 865.4 s | 0.975521 | −0.0018 |

---

## 2. Pseudocode of the three changes that worked

Notation: `N` = real chunks in a batch, `C` = `-C` (GPU batch size, 128), `T` = timesteps per chunk (2048 for HAC/FAST at `-c 12288`),
`NTC` = scores laid out `[batch, time, channels]`.

### 2.1 Overlap decode  (`basecall_chunks_overlap`, `src/basecall.cpp`; `--overlap-decode=yes`)

**Problem.** The serial path does `infer(batch)` then `decode(batch)`; the GPU decode and the CPU work to turn its output into
sequences leave inference idle.

**Idea.** A depth-1 software pipeline. Each runner owns two CUDA streams (inference, decode), two input buffers, two completion
events, and a 2-slot pinned host output ring in openfish. Decode of batch *k* runs while the CPU stages batch *k+1* and the GPU infers it.

```text
per-runner state:
    input[0..1]            # two CPU->GPU staging buffers, ping-ponged by `slot`
    infer_stream, decode_stream
    infer_event[0..1]      # "inference for this slot is finished"
    host_ring[0..1]        # persistent pinned host buffers for decode output (v1.1: never freed per batch;
                           #   v1.2: two slots so batch k's output survives while k-1 is still being written)
    pending                # the previous batch: its scores (on GPU) + chunk list + which slot it used

basecall_chunks_overlap(chunks):
    slot = runner.slot                              # 0 or 1, flips every batch
    copy each chunk's signal into input[slot]       # CPU, "accept"

    if pending exists and its decode is not yet launched:
        launch_decode(pending)                      # see below; asynchronous, no sync

    # --- inference of the CURRENT batch, concurrent with the decode of the PREVIOUS one ---
    with stream = infer_stream:
        x      = input[slot][0:N]                   # narrowed to real chunks (see 2.2)
        scores = model.forward(x)                   # NTC
        record(infer_event[slot])

    if pending exists:
        synchronize(decode_stream)                  # previous decode has finished
        write_decode_results(pending)               # CPU: moves/sequence/qstring -> chunk objects
        pending = none

    pending = { chunks, scores, slot, host_slot = slot & 1 }
    launch_decode(pending)                          # starts as soon as infer_event[slot] fires
    runner.slot = slot ^ 1                          # ping-pong

launch_decode(p):
    with stream = decode_stream:
        wait(infer_event[p.slot])                   # decode only starts after THIS batch's inference is done
        openfish_decode_gpu(p.scores, ..., host_slot = p.host_slot, stream = decode_stream)
            # bwd_scan -> beam_search -> fwd_post_scan -> qual -> gen_sequence -> async D2H into host_ring[host_slot]
            # no cudaStreamSynchronize inside: stream != NULL skips the per-phase device syncs

flush at end of input:
    launch (if needed) + synchronize + write the last pending batch serially (nothing left to overlap with)
```

Version history: **v1.0** infer∥decode with correct NTC layout for `C > 1`; **v1.1** persistent pinned host buffers
(removes per-batch `cudaHostAlloc/FreeHost`, which was an nsys hotspot); **v1.2** two-slot ring so one batch's CPU write-back
can overlap the next batch's decode.

### 2.2 Load imbalance — narrow batches + flush threshold  (`pthread_single_basecall`, `call_chunks`; `--fixed-c-batch`, `--flush-threshold`)

**Problem.** Every GPU launch used the full `C`-wide input tensor even when only `N < C` chunks were real (tail of the input, or a
partial flush), so inference and decode were spent on padding.

**Idea.** (a) Run the forward pass and decode only on the `N` real chunks; (b) let the batcher flush a batch earlier than `C`.

```text
flush_thr = flush_threshold  if 0 < flush_threshold <= C  else C

queue = []
for each read assigned to this runner:
    for each chunk of the read:
        queue.append(chunk)
        if len(queue) >= flush_thr:
            dispatch(queue); queue = []
if queue not empty:
    dispatch(queue)                                  # leftover tail batch (N < flush_thr)

dispatch(batch):
    N = len(batch)
    stats: total_batches++, total_chunks += N; if N < C: tail_batches++, padded_slots += C - N
    if overlap_decode: basecall_chunks_overlap(batch)      # 2.1
    else:              basecall_chunks(batch)

# inside inference (both paths):
input  = input_tensor                    if --fixed-c-batch=yes        # old behaviour: full C rows, padding included
       = input_tensor.narrow(0, 0, N)    otherwise                     # only the N real rows
scores = model.forward(input)            # N rows, so the CRF scores and the openfish decode are N-wide too
```

Notes: the "best" settings found were `--fixed-c-batch=no` with `--flush-threshold=64` for FAST and `128` for HAC. Smaller thresholds
flush sooner but run narrower (less efficient) batches. With a full input, the offline gain is small (FAST 1k −3.4 to −5.9 %, ≈ 0 on 20k and HAC); the large gains
appear when batches are flushed early or partially (−45 % at flush 64, −77 % at flush 4, against padded fixed-C batches), i.e. the streaming case. The earlier
single power-run figure of ≈ −12 % on FAST 1k is not supported by the multi-run sweep. slorado's "padded slots" statistic counts `C − N` per tail batch and is an accounting figure, not the
launch width when narrowing is on.

### 2.3 Speculative decode  (`overlap_*` and `decode_scores_speculative` in `src/basecall.cpp`; kernels in `openfish/src/greedy_search_cuda.h`, `decode_cuda.c`)

**Problem.** After the LSTM, FAST is dominated by the width-32 CRF beam search. Most chunks are easy.

**Idea.** Decode every chunk with a cheap width-1 *greedy* path; accept it when it looks confident; run the full beam only on the rest ("repair").

```text
# thresholds (flags): Q_thr = --spec-repair-threshold (10), M_thr = --spec-margin-threshold (2)

decode_speculative(scores):                       # scores: NTC for one batch
    (moves, seq, qstr, margin[]) = openfish_decode_gpu_greedy(scores)       # draft, ALL chunks
    write drafts into the chunk objects
    hard = []
    for i in 0 .. N-1:
        q = mean_phred(chunk[i].qstring)
        if q < Q_thr  or  margin[i] < M_thr:     # not confident enough
            hard.append(i)
    if hard is empty: return                      # every draft accepted; scores can be dropped
    hard_scores = scores.index_select(dim=0, index=hard)   # copy only the hard rows
    (moves2, seq2, qstr2) = openfish_decode_gpu(hard_scores)              # full beam, hard subset only
    for j, i in enumerate(hard):
        chunk[i] = result j of the repair                                 # overwrites the draft
```

The greedy draft itself (openfish, replaces `beam_search` inside the same decode pipeline):

```text
openfish_decode_gpu_greedy(scores):               # same pipeline as the beam decode, one kernel swapped
    bwd_scan(scores)           -> bwd[chunk][t][state]        # backward guide
    greedy_search(scores, bwd) -> states[], moves[], margin[chunk]     # <-- replaces beam_search
    fwd_post_scan(scores, bwd) -> posteriors
    compute_qual_data(posteriors, states)
    generate_sequence(...)     -> sequence, qstring
    async D2H of moves / sequence / qstring / margin into the pinned host ring slot

greedy_search (one GPU thread per chunk):
    state      = argmax over s of bwd[0][s]       # start where the backward guide is best
    margin_sum = 0
    for t in 0 .. T-1:
        candidates:
            for base in 0..3:                      # "step": append one base to the k-mer
                new_state = ((state << 2) & mask) | base
                score[base] = emission[t][move_index(state, new_state)] * scale + bwd[t+1][new_state]
            stay_score      = fixed_stay_score + bwd[t+1][state]        # "stay": repeat the same k-mer
        best, second = largest and second-largest of the five scores
        state = best's destination state
        states[t] = state;  moves[t] = 1 if best was a step else 0
        margin_sum += best - second
    moves[0] = 1                                   # always a step at the first event
    margin[chunk] = margin_sum / T                 # mean decision margin; low = unreliable chunk
```

The "brave" variant (`--spec-overlap-repair=yes`) also overlaps the repair with the next batch's inference, using the same
two-stream structure as 2.1:

```text
basecall_chunks_overlap(chunks):                    # speculative + brave
    accept chunks into input[slot]
    if repair_hold exists:   launch full-beam repair of repair_hold's hard subset on decode_stream   # repair(k-1) ‖ infer(k)
    elif pending exists:     launch greedy draft of pending on decode_stream
    infer(current) on infer_stream
    if repair_hold exists:   sync decode_stream; write repaired chunks; repair_hold = none
    if pending exists:
        if draft not launched and no repair queued: launch draft(pending)
        sync decode_stream; gate(pending)            # write draft, build `hard`
        if pending has hard chunks:
            repair_hold = pending                    # defer: repaired during the NEXT iteration's inference
        pending = none
    pending = current batch (scores kept alive)
    if repair_hold is empty: launch draft(pending) now      # otherwise delay, so the GPU buffers stay exclusive to the repair
```

Cost/benefit knobs: the margin gate dominates the accept rate (m = 2 → ≈ 73–76 % of chunks accepted on FAST, ≈ 81–83 % on HAC; m = 3 → ≈ 7–9 % on FAST and ≈ 67–71 % on HAC, and slower than baseline on FAST and on HAC 20k).
HAC gains little because its wall time is inference-bound (≈ 83 % LSTM), so saving decode time hardly shows.

---

## 2b. Reliability of the numbers

- **Single runs:** load imbalance, speculative decode (August and October), CPU beam, cascade-20k, and every power/energy figure are n = 1. Multi-run data (n = 10/3, CV < 0.6 %) exists only for the overlap versions.
- **Energy resolution:** FAST 1k power runs have only 11–16 tegrastats samples, so 1k energy differences under ~10 % cannot be resolved; quote energy at 20k until the final harness. Energy windows also included startup/idle samples (see `harness-audit.md`).
- **Possible confounder:** the August speculative power campaign ran with almost no swap in use (`peak_swap_mb` 0–214 on its first six runs) whereas other stages peaked at ~1.4–1.6 GB. Two HAC 20k speculative attempts failed with an NvMap OOM before succeeding.
- **Expected-median note:** `0.940696` (FAST 1k) is the *published* value in the upstream README (`slorado/README.md:80`), not one of these runs. The correct references are `0.940772` for narrow batches and `0.940763` for fixed-C (padded) batches; HAC 1k (`0.976852`) is identical everywhere.
- **Re-created speculative code:** repaired-chunk counts are identical to August on 1k (1230, 881) and differ by 3 and 1 on 20k (27541 vs 27538, 19518 vs 19517); medians differ by ≤ 0.000005.
- **Invalid stage found:** `pre_overlap` (`8873209`) decodes garbage (see above); every table or chain that used it is withdrawn.
- **Pending:** a same-harness, multi-run, both-output-mode re-measurement of every stage under a validated harness (`measurement-methodology.md`, `harness-audit.md`).

---

## 3. What the profiling added (see `2026-10-05-hac-lstm-profile.md`)

- HAC wall time is ≈ 83 % LSTM, which none of the three changes above touch.
- About 30 % of each LSTM layer is a near-idle gate/cell elementwise kernel (`elemWiseRNNcell`, ≈ 23 µs/step, launched 409 600 times per 1k run).
- The best untested lead is replacing that kernel (estimate: ≈ −20 % of HAC wall; unbuilt and unmeasured).
- The ncu per-kernel numbers in that document were taken at N = 2, not N = 128, and are provisional; the nsys and micro-benchmark results are N = 128.

## 4. Open items

- Same-harness baseline: re-run the pre-overlap commit through `rileys-runner.py` so both ends of the comparison use the same harness.
- Baseline accuracy for the load-imbalance config on HAC/20k in the same session as the speculative runs.
- Optional: proof-of-concept fused LSTM cell kernel; re-run ncu at N = 128 (`--fixed-c-batch=yes`).
- Merge `speculative-decode` into `develop` (deferred while work continues).
