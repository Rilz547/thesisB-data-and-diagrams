# Smoke test, 2026-10-22 — functional check of every stage and of RNA support

**What it is:** one run per (stage, cell) on the 1k datasets, output mode B, harness `97f48888f36c`-family code; **not a measurement** (single cold runs, records in `bench-runs/smoke.jsonl`, never in `runs.jsonl`). Purpose: does each stage binary run, does it support RNA, does its output look right, and are the harness's window anchors sound.
**Accuracy:** RNA identity computed on the Jetson with the preprint's method (`minimap2 -cx splice -uf -k14 --secondary=no`, gencode v40). DNA mapping needs a ~7 GB hg38 index, so DNA accuracy is left to the PC; DNA output was instead compared read-by-read with `upstream` (`analyze.py equiv`).

## 1. Result summary
- **24 of 25 first-pass runs completed**; the one failure (`cb_on`, stream pipeline: no progress lines) was a harness limitation, fixed by anchoring on the `Real time` line (cross-check on `ov_on`: the two anchors agree to 0.8 ms); `cb_on` then ran fine.
- **RNA works on every valid stage** (`upstream`, `ov_on`, `lb_best`, `spec`) for FAST and HAC.
- **`pre_overlap` (`8873209`) is INVALID**: its output is unrelated to the other stages on DNA and RNA (below).
- **Speculative decode transfers to RNA for speed but not for FAST accuracy** (below).
- Harness quality in these 25 runs: progress-line start-epoch spread ≤ 0.9 ms, process exit 0.57–1.23 s after the timed window ends, power-rail coverage 100 % of every window.

## 2. Single-run `Real time` (s), mode B, 1k — indicative only
| stage | DNA FAST | DNA HAC | RNA FAST | RNA HAC |
|---|---|---|---|---|
| upstream | 14.94 | 49.58 | 10.76 | 41.63 |
| pre_overlap *(invalid)* | 14.66 | 51.65 | 10.23 | 40.65 |
| ov_off | 12.81 | — | — | — |
| ov_on | 12.09 | 47.97 | 8.85 | 37.90 |
| lb_baseline | 10.68 | — | — | — |
| lb_best | 12.20 | 48.03 | 8.14 | 37.95 |
| spec | 10.97 | 47.21 | 7.53 | 37.80 |
| cb_off | 12.63 | — | — | — |
| cb_on | 12.30 | — | — | — |
| paper_xavier (`-t 8 -B 500M -K 2000 -c 10000 -C 200`) | 13.53 | — | — | — |
First runs include cold-start effects and some ordering effects; do not draw stage ordering conclusions (e.g. `lb_baseline` 10.68 s vs `ov_on` 12.09 s) from single runs. The campaign's repeats will settle those.

## 3. `pre_overlap` is invalid
Read-by-read comparison with `upstream`, 1,000 reads each:
| data | identical reads | median length ratio | median similarity (first 400 bases) |
|---|---|---|---|
| DNA FAST | 0 | 0.753 | 0.309 |
| DNA HAC | 0 | 0.728 | 0.282 |
| RNA FAST | 0 | 1.123 | 0.305 |
| RNA HAC | 0 | 0.764 | 0.291 |
Every other stage reproduces `upstream` (identical, or 99–100 % identical reads for `lb_best`; `spec`/`cb_on`/`paper_xavier` differ by design).
**Cause (from the code):** at `8873209` `call_chunks` transposes the model output to TNC (`scores_TNC = scores.transpose(0, 1)`) before calling openfish, but the pinned openfish (`3d95491`) reads scores as NTC (`scan_cuda.h`: "scores are NTC: each batch element's [T,C] block is contiguous"). The layouts coincide for a single chunk and scramble every batch of more than one. The fix, "scores stay NTC for openfish", is in v1.0 (`e00bada`), and a later code comment records the same lesson ("Transposing to TNC made N=1 look fine … but destroyed accuracy for N>1").
**Consequences:** the August `pre_overlap` timing and energy rows, and the single-chain "original → now" figures built on them, came from a garbled decode and must not be used as a baseline. `pre_overlap` was never accuracy-checked (the PC cross-check found no accuracy data for it). The valid pre-overlap baselines are `upstream` (`754a74d`) and `ov_off` (`7cec6c5`, overlap off); both reproduce the same reads. The earlier open question "why is `pre_overlap` 3–5 % slower than `ov_off`?" is therefore most likely an artefact of the broken decode, not of host-buffer handling.

## 4. Speculative decode on RNA (`q10_m2_brave`, thresholds tuned on DNA)
| cell | accept rate α | median identity | Δ vs `upstream` | `Real time` vs `lb_best` |
|---|---|---|---|---|
| DNA FAST 1k | 0.759 | (PC) | −0.55 pp (earlier PC result) | 10.97 vs 12.20 s |
| DNA HAC 1k | 0.827 | (PC) | −0.14 pp (earlier PC result) | 47.21 vs 48.03 s |
| **RNA FAST 1k** | **0.905** | **0.892756** | **−4.16 pp** (0.934396 → 0.892756) | 7.53 vs 8.14 s |
| RNA HAC 1k | 0.930 | 0.968209 | −0.35 pp (0.971673 → 0.968209) | 37.80 vs 37.95 s |
On RNA the gate accepts far more drafts (α 0.91–0.93 vs 0.76–0.83) and, for FAST, the accepted drafts are much worse: RNA FAST output is also 8 % shorter (median length ratio 0.916) with 46/1000 reads identical to `upstream`. The thresholds (mean Phred Q ≥ 10 and margin ≥ 2) were tuned on DNA and the RNA quality scale differs (FAST RNA q-score bias +2.0 vs −2.0 for DNA), so they are probably too permissive for RNA FAST (a hypothesis; not yet tested). This is a result in itself (the method needs per-model thresholds) and motivated a threshold exploration on RNA 1k (run the same day: `spec-threshold-exploration-rna.md`; no setting gives both a speed-up and acceptable accuracy on RNA FAST).

## 5. RNA plausibility (preprint, full UHRR dataset on A100, Supplementary Table S6)
`upstream`/`ov_on`/`lb_best` on the 1,000-read subset: **FAST 0.934396**, **HAC 0.971673** (map rate 97.4 % / 98.0 %). The preprint's full-dataset values are ≈ 0.931–0.934 (FAST) and ≈ 0.967–0.972 (HAC): consistent.
