# Report outline — methodology and results sections

**Draft layout for the thesis report** (2026-10-22). Each heading lists its source document and the caveat to state. Numbers marked *(final)* will come from the frozen-harness campaign; everything else exists today.
Speculative decode was **not** renamed in the code (`--speculative-decode`); this outline calls it "speculative (draft-and-repair) decoding".

---

## A. Measurement methodology  *(new)*
Source: `measurement-methodology.md`, `harness-audit.md`
- A.1 Platform, models, shared flags (Jetson Orin Nano, 25W, `jetson_clocks` off; DNA FAST/HAC v5.0.0 and RNA FAST/HAC v5.1.0).
- A.1b **Datasets** (`datasets.md`): DNA 1k (1,000 reads) and 20k (20,000 reads, 1.12 B samples; byte-identical to upstream's test file) and the 500k subset (500,000 reads, 28.1 B samples, matches the preprint's HG002-dna-500k); RNA UHRR RNA004 1k and 20k (772 M samples). State provenance, read and sample counts, and that the optional chr22 tier (189,928 reads) is upstream test data, not a preprint dataset.
- A.1c **Code lineage**: upstream `754a74d` (0.5.0-beta, 2026-05-07); the preprint used v0.4.0-beta (`499b1cb`), and its Xavier run used `406ab8c`.
- A.2 **Two headline speed statistics:** H1 raw speed (`-o /dev/null`) and H2 end-to-end speed (FASTQ written to NVMe), both `Real time`.
- A.3 How differences are computed: time change, speed-up, throughput gain (not interchangeable); incremental versus cumulative (factors multiply); named baselines only.
- A.4 Energy: timed-window integration, per-rail (`VDD_IN`, CPU+GPU+CV, SoC, residual), idle baseline and net energy, energy per read / per Mbase; what cannot be separated (GPU vs CPU vs DRAM).
- A.5 Accuracy: median identity (DNA: minimap2 `map-ont` vs hg38noAlt; RNA: minimap2 `splice -uf -k14` vs GENCODE v40 transcripts; both `--secondary=no`, PAF col10/col11), absolute pp and relative error; batch-mode-specific reference values.
- A.6 Statistics: n, mean ± sd, CV; what counts as a real difference.

## B. Evolution of the testing harness  *(new)*
Source: `harness-evolution.md`, `harness-audit.md`
- B.1 Timeline v0–v5 (Jetson-Runner GUI → shell notes and Nsight script → `rileys-runner.py` → tegrastats power scripts → reconfigured runner → unified harness).
- B.2 What each version could and could not show; the audit findings that forced the redesign (window mismatch, 1 Hz vs a ≈ 3 Hz sensor, single runs, no idle baseline, one total rail).
- B.3 Validation of the final harness and its frozen state (`HARNESS-FROZEN.md`) *(final)*.

## C. Where the time goes (profiling)
Source: `2026-10-05-hac-lstm-profile.md`, `bench/profile_stage.py` output *(final, per stage)*
- C.1 FAST is decode-bound; HAC is inference-bound (≈ 83 % LSTM).
- C.2 HAC LSTM kernel breakdown (`nsys`, N = 128): recurrent GEMM, cell/gate kernel (≈ 23 µs × 409 600 launches), input GEMM; batch/sequence scaling benchmark.
- C.3 Per-stage profile: how GPU busy share, decode share and infer–decode overlap change from stage to stage *(final)*.
- Caveat: the Nsight Compute passes ran at N = 2; only the N = 128 `nsys` and scaling results are cited.

## D. Methods that improved performance
Source: `2026-10-21-optimisation-summary.md` (§1, §2), `REPLY-from-PC-agent-2026-10-22.md`
- D.1 **Overlap decode** (v1.0 → v1.2): FAST −11.5 % (1k) / −13.9 % (20k) in the multi-run data; HAC ≈ −3 %. Pseudocode in the appendix.
- D.2 **Load imbalance** (narrow batches + flush threshold): offline FAST 1k −3.4 to −5.9 %; streaming case −45 % (flush 64) to −77 % (flush 4) against padded batches.
- D.3 **Speculative (draft-and-repair) decoding:** greedy draft, quality/margin gate, selective beam repair, "brave" overlapped repair; FAST ≈ −7 to −10 % at ≈ −0.5 pp identity; HAC −0.2 to −1.6 % at ≈ −0.15 pp.
- D.4 Combined effect, stage by stage, with named baselines *(final)*.
- D.4b **RNA**: the same stages on UHRR RNA004 (FAST and HAC, 1k and 20k); accuracy against the GENCODE v40 transcriptome; whether speculative decode's thresholds (tuned on DNA) transfer (accept rate α and identity change).
- D.4c **Large dataset (500k, FAST)** *(final)*: your fastest configuration versus upstream, and the Xavier-matched run (`406ab8c` with the preprint's flags) against the preprint's Jetson AGX Xavier result (144.4 min, median identity 0.939837, ≈ 3.24 M samples/s). State the differences: hardware, CUDA/torch versions, power mode; compare both slorado `Real time` and whole-process wall time (GNU `time` analogue).
- D.5 Accuracy impact, one table of median identity per stage and config *(final)*.
- D.6 Power and energy: gross/net, per-rail, per-read, both headline modes *(final)*; peak power flat, energy tracks time (preliminary, August data).
- Caveat: pre-final figures are single runs from different harnesses. The August `pre_overlap` baseline is **withdrawn** (invalid decode); use `upstream`/`ov_off` as pre-overlap baselines.

## E. Methods attempted that did not improve performance
Source: `2026-10-21-optimisation-summary.md`
- E.1 **CPU beam search hybrid** (GPU scan + CPU beam): slower in every cell (+2 % to +19 % wall in the power runs; baseline had no overlap).
- E.2 **Overlap depth-2** (dual decode): documented failed route.
- E.3 **FAST→HAC cascade:** accuracy/speed knob, not a speed win (1.56× faster than HAC on 1k with better-than-FAST accuracy from `force_frac` 0.30; always slower than FAST). No power data.
- E.4 **Speculative variants:** `m = 3` margin (slower than baseline on FAST; HAC 20k), speculative decode on HAC (inference-bound, < 2 %), and **RNA FAST** (accept rate 0.91 but −4.2 pp identity; a 13-setting threshold grid found no setting that is both faster and accurate; `spec-threshold-exploration-rna.md`).
- E.5 **Larger batches:** out of memory at `-C 256/512` (HAC, overlap on); FAST evidence from the PC data.
- E.6 **Int8 quantisation:** not available for the v5 LSTM models in this codebase (int8 path only for v6 factored LSTM / transformer, fused kernel removed); would need new kernels.
- (Personal test branches `cuda-lstm-gemm`, `flip-spike`: omit.)

## F. Discussion
- F.1 Why gains differ between FAST and HAC (decode-bound vs inference-bound).
- F.2 Remaining bottleneck: the near-idle LSTM cell kernel (estimated ≈ 20 % of HAC wall; **unbuilt and unmeasured**).
- F.3 Threats to validity: single runs in earlier data, harness differences between stages, baseline ambiguity, the re-created speculative code (reproduced August α and identity to within noise), swap/thermal drift.

## G. Future work
Fused LSTM cell kernel; mini-beam draft and CPU repair for speculative decode; int8 LSTM for v5; same-harness replication on other hardware modes.

## H. Appendix
- Pseudocode for the three methods (`2026-10-21-optimisation-summary.md` §2); the greedy draft kernel.
- Flags and commit hashes per stage (slorado `49ac641`, openfish `5109673`, stage commits `754a74d`, `8873209`, `7cec6c5`, `65246f2`, `664bb1f`).
- Reproduction commands: harness, analysis scripts, accuracy pipeline.
- Note on the lost-and-re-created speculative kernel and how it was validated.
