# Reply from the PC agent: cross-check against thesisB-data-and-diagrams

**Written:** 2026-10-22 by the Claude Code agent on Riley's main PC, in reply to `AGENT-HANDOFF-thesisB-comparison.md` (2026-10-21).
**Scope:** read-only. Nothing under `thesisB-data-and-diagrams` was changed, nothing was re-run (minimap2 included), nothing was committed.
The Jetson could not be reached from here (SSH needs Riley's password), so none of the §2 rsync pulls were done. Everything below comes from PC files only.
Paths are relative to `~/Desktop/thesisB-data-and-diagrams/` unless they start with `~`.

**Short version**
- Every number in §4.1, §4.2, §4.3 and §4.4 matches the PC files. In §4.3 all 36 wall/energy cells match (largest gap 0.29 %, which is rounding).
- `0.940696` is upstream's published expected median, not one of Riley's runs. Riley's own FAST 1k median depends on batch mode: `0.940763` with fixed-C (padded) batches and `0.940772` with narrow batches.
- Two §6 figures come from single power runs and are too high: overlap "FAST −15 to −17 %" (multi-run data says −11.5 / −13.9 %) and load imbalance "FAST 1k ≈ −12 %" (sweep data says −3.4 to −5.9 % depending on the reference; report headline 4.8 %).
- No same-harness "original" timing exists. Depending on which baseline you pick, the FAST 1k "original vs now" result ranges from −21.5 % to −36.5 %.

---

## 1. Inventory

| # | Stage | Date (data / commit) | Code (as recorded) | Flags and repeats (as recorded) | Data types | Path |
|---|---|---|---|---|---|---|
| 1 | Early accuracy check | 2026-06-10 | not recorded | not recorded | minimap2 mean / median / count only | `~/Desktop/slorado_analysis/results/output_{fast,hac}_{1k,20k}_accuracy.txt` (outside the repo) |
| 2 | June upstream baseline | 2026-06-23 | commit not recorded in files (handoff says `754a74d`) | `-t 8 -K 4096 -C 256` (FAST) / `-C 128` (HAC) `-B 512M -c 12288 -p 150`, writes FASTQ, 1 run, no warm-up | JSON (wall, CPU, RAM, minimap2 accuracy), `telemetry.csv`, PDF + PNG charts (CPU, GPU, memory, power, temperature), console | `baseline-results/{fast1k,fast20k,hac1k,hac20k}/`, `baseline-results/baselines-all-console-raw.txt` |
| 3 | Upstream-style baseline "v2" (not in the handoff) | files 2026-06-30, committed 2026-07-14 | not recorded | `-C 256` (FAST) / `-C 128` (HAC), other flags default, writes FASTQ, 1 run | console only (wall, CPU, RAM, per-stage timing) | `baseline_results_v2/{1k,20k}_{FAST,HAC}.txt` |
| 4 | Overlap v1.0 / v1.1 / v1.2 | 2026-07-18 → 07-22 | v1.0 `e00bada`/`59e12e9`, v1.1 `9f8db24`/`cf182a7`, v1.2 `7cec6c5`/`223f26c` | `-C 128 -c 12288 -K 4096 -p 150 --overlap-decode=no\|yes`; `rileys-runner.py`: warm-up, then n=10 (1k) / 3 (20k) timed `/dev/null` runs, plus 1 accuracy + nsys pass | runner summaries (txt/csv/json/console), minimap2 CSVs (baseline + overlap), `nsys stats` (8 per version), per-version notes + overall report | `overlapping/overlapv1.{0,1,2}/{accuracy,nsight-stats,runner-summaries}/`, `overlapping/overlap-report.md` |
| 5 | CPU beam hybrid | 2026-07-22/23 | "Bonson managed hostvis" (commit not recorded) | `-C 128 -c 12288 -K 4096 -p 150 --cpu-beam=no\|yes`, **no `--overlap-decode`**, 1 warm-up + 1 timed run | timings CSV, results txt, console logs, gdb backtraces, canvas chart. **No accuracy** | `cpu-beam-search/` |
| 6 | Load imbalance (narrow batches) | 2026-07-28 | branch `load-imbalance` (power runs record `65246f2`) | `-C 64\|128 -c 12288 -K 4096 -p 150 --overlap-decode=yes --fixed-c-batch=yes\|no --flush-threshold=N`, **1 run per cell** | flush, batch-width and spot-check timings (`-no-output` and `-with-output`), report, canvas chart, drivers; minimap2 CSV (43 FASTQs) | `load-imbalance/jetson-artifacts/`, `load-imbalance/accuracy/minimap_accuracy-load-imbalance.csv` |
| 7 | FAST→HAC cascade | 2026-07-31 → 08-02 | branch `cascade-fast-hac` (commit not recorded) | overlap depth 1, narrow, `--flush-threshold=64` for **both** FAST and HAC; `-C 128`, or `-C 64` for high-frac 20k; 1k × 3 reps, 20k × 1 | threshold-sweep + force-frac-sweep CSVs, minimap2 CSVs, report / design / plan, scripts. **No power, no nsys** | `cascade/` (`casecade-final/`, `sweep-artifacts/`, `cascade-force-frac/`, `accuracy/`) |
| 8 | Speculative decode, original code | 2026-08-02/03 | `speculative-decode`, uncommitted on top of `65246f2` (see §3 item 11) | overlap on, narrow, FAST `--flush-threshold=64`, HAC `128`; 1 time pass (`/dev/null`) + 1 keep pass (FASTQ) per tag | manifest, minimap2 CSV, RESULTS / SUMMARY, logs, report | `spec-decode/20260802T110631Z/` (FAST, full grid), `spec-decode/20260802T121510Z/` (HAC `--quick`), `spec-decode/report.md` |
| 9 | Power campaign (all stages in §4.3) | 2026-08-11 | per row of §4.3 | nvpmodel 25W, `jetson_clocks` OFF, 1 run each, tegrastats at 1 Hz, `/dev/null` | `all_summaries.csv` (wall, peak/mean power, energy, RAM, swap, GR3D, temperatures); per-run `meta.txt`, `samples.csv`, `tegrastats.log`, `slorado.log`; PNG charts. **No accuracy** | `power-runs/baseline/20260811T124004Z/`, `power-runs/overlap/20260811T113545Z/`, `power-runs/load-balance/20260811T082445Z/`, `power-runs/cpu-decode/20260811T135455Z/`, `power-runs/spec-decode/` |
| 10 | Speculative decode, re-created code | run 2026-10-05, committed here 2026-10-21 (`350ebf6`) | `49ac641` / `5109673` per the handoff (not recorded in files) | as §4.1; n=1, FASTQ pass doubles as warm-up | runner output / timings / state / console, minimap2 CSV. **No power, no nsys** | `overlapping/overlapv1.0/runner-summaries/riley-runner-*-spec-q10m2brave.*`, `overlapping/overlapv1.0/accuracy/minimap_accuracy-spec-q10m2brave.csv`; FASTQs in `slorado-accuracy-tmp/` (gitignored) |
| 11 | Diagrams | 2026-06-23 | n/a | n/a | `accuracy-analysis.drawio` (its `.bkp` copy is also tracked), `fixing-script.png` | repo root |

**Not on the PC:** overlap depth-2, the HAC LSTM profile (§5), `cuda-lstm-gemm` / `flip-spike`, the Oct `q10_m2` serial and `q10_m3_brave` 1k tests, the 2026-07-22 cpu-beam report, and `AGENT-HANDOFF-power-campaign.md`.

**Layout vs the older handoff:** `overlapping/power-runs/` and `overlapping/spec-decode/` do not exist. They are top-level `power-runs/` and `spec-decode/`.
`power-runs/spec-decode/` has no stamp subfolder, and its `all_summaries.csv` is byte-identical to `raw/all_summaries.csv`.
The Oct re-run is filed under `overlapping/overlapv1.0/` because `fetch-and-minimap.py` points `ACCURACY_DIR` and `RUNNER_SUMMARY_DIR` there.

---

## 2. Cross-check

### §4.1 Oct timings and accuracy (re-created code)
Timings file: `overlapping/overlapv1.0/runner-summaries/riley-runner-timings-spec-q10m2brave.csv`. Accuracy file: `overlapping/overlapv1.0/accuracy/minimap_accuracy-spec-q10m2brave.csv`.

| Claim | Jetson value | PC value | Verdict |
|---|---|---|---|
| FAST 1k timed / median / map / reads | 9.023 / 0.935276 / 103.2 % / 1000 | 9.023 / 0.935276 / 103.2 % / 1000 | match |
| HAC 1k | 44.679 / 0.975490 / 110.5 % / 1000 | 44.679 / 0.975490 / 110.5 % / 1000 | match |
| FAST 20k | 158.803 / 0.934853 / 103.6 % / 20000 | 158.803 / 0.934853 / 103.6 % / 20000 | match |
| HAC 20k | 865.431 / 0.975521 / 111.3 % / 20000 | 865.431 / 0.975521 / 111.3 % / 20000 | match |
| Run order: FASTQ pass, then 1 timed run | as stated | CSV rows are `accuracy` then `timed`; the runner header says `warmup=no` | match |
| Oct α (from `riley-runner-{fast,hac}-console-spec-q10m2brave.txt`) | 0.759 FAST 1k | FAST 1k 0.759 (1230/5100 repaired), FAST 20k 0.730 (27541/102089), HAC 1k 0.827 (881/5100), HAC 20k 0.809 (19518/102089) | match |

### §4.2 Aug original-code results
Files: `spec-decode/20260802T110631Z/minimap_accuracy.csv` + `manifest.csv` (FAST), `spec-decode/20260802T121510Z/minimap_accuracy.csv` + `manifest.csv` (HAC).

| Claim | Jetson value | PC value | Verdict |
|---|---|---|---|
| FAST 1k `q10_m2_brave` time / α / median | 9.033 / 0.759 / 0.935274 | 9.033 / 0.759 / 0.935274 | match |
| FAST 20k | 157.768 / 0.730 / 0.934857 | 157.768 / 0.730 / 0.934857 | match |
| HAC 1k | 44.593 / 0.827 / 0.975490 | 44.593 / 0.827 / 0.975490 | match |
| HAC 20k | 866.079 / 0.809 / 0.975516 | 866.079 / 0.809 / 0.975516 | match |
| Baselines FAST 1k / FAST 20k / HAC 1k / HAC 20k (time, median) | 9.827, 0.940772 / 174.065, 0.939375 / 45.305, 0.976852 / 871.297, 0.977333 | identical | match |
| Re-created 1k α: `q10_m2` 0.761, `q10_m3_brave` 0.093 | 0.761 / 0.093 | Oct runs for these tags are not on the PC. The Aug values are 0.761 / 0.093 | can't verify Oct; the claim equals the Aug value |
| Oct FAST 1k median 0.935276 vs baseline 0.940772 | as stated | both present (in the two files above) | match |

### §4.3 Power-campaign chain
Files: `power-runs/baseline/20260811T124004Z/all_summaries.csv` (`pre_overlap`), `power-runs/overlap/20260811T113545Z/all_summaries.csv` (`ov_off`, `ov_on`), `power-runs/load-balance/20260811T082445Z/all_summaries.csv` (`lb_*`), `power-runs/spec-decode/all_summaries.csv` (`best_fast`, `spec_q10_m2_brave`), `power-runs/cpu-decode/20260811T135455Z/all_summaries.csv` (`cb_*`).

| Stage | Commit in `meta.txt` | Wall (4 configs) | Energy (4 configs) | Verdict |
|---|---|---|---|---|
| `pre_overlap` | `8873209` (HEAD) | 12.974 / 48.559 / 217.121 / 934.324 | 176.8 / 802.1 / 2981.1 / 15845.0 J | match |
| `ov_off`, `ov_on` | `7cec6c5` (HEAD) | 12.356 / 46.587 / 206.102 / 902.478; 10.234 / 45.110 / 174.192 / 875.174 | 167.5 / 774.4 / 2829.2 / 15345.0; 151.6 / 778.5 / 2648.6 / 15327.9 | match |
| `lb_baseline`, `lb_best` | `65246f2` (`load-imbalance`) | 11.064 / 45.033 / 175.073 / 867.595; 9.782 / 44.687 / 173.820 / 870.539 | 161.7 / 779.1 / 2667.5 / 15256.9; 143.1 / 767.4 / 2661.5 / 15261.8 | match |
| `best` (CSV label `best_fast`, used for HAC too), `spec_q10_m2_brave` | `65246f2` (`speculative-decode`) | 9.778 / 45.817 / 174.218 / 873.884; 9.154 / 45.372 / 160.951 / 872.306 | 141.7 / 779.6 / 2650.7 / 15306.7; 139.2 / 783.9 / 2501.4 / 15366.3 | match |
| `cb_off`, `cb_on` | `664bb1f` (`cpu-beam`) | 11.928 / 46.064 / 198.269 / 893.652; 12.207 / 54.498 / 219.168 / 1059.460 | 165.3 / 775.6 / 2764.9 / 15222.5; 181.0 / 882.0 / 3313.0 / 17512.6 | match |

Order is FAST 1k / HAC 1k / FAST 20k / HAC 20k. All 36 cells match within rounding (largest gap 0.29 %, `ov_on` FAST 1k energy 151.557 → "152").
"Oct agrees with Aug within ~1–2 %": the Oct runner vs the Aug power runs differ by −1.4 / −1.5 / −1.3 / −0.8 % (match). The Oct runner vs the Aug runner differ by −0.1 to +0.7 %.

### §4.4 June 23 upstream baseline
Files: `baseline-results/*/*.json` (`basecallResults.real_time_sec`) and `baseline-results/baselines-all-console-raw.txt`.

| Claim | Jetson value | PC value | Verdict |
|---|---|---|---|
| FAST 1k / HAC 1k / FAST 20k / HAC 20k wall | 14.212 / 49.805 / 247.872 / 955.152 | 14.212 / 49.805 / 247.872 / 955.152 | match |
| Flags: FAST `-C 256`, HAC `-C 128`, `-t 8 -B 512M`, FASTQ out | as stated | identical `[main] CMD` lines | match |
| Commit `754a74d` | as stated | not recorded in these files (`~/Desktop/slorado_analysis/slorado` is an upstream checkout at `754a74d`, but nothing links it to these runs) | can't verify |
| June accuracy (not in the handoff) | n/a | 0.940763 / 0.976852 / 0.939375 / 0.977333; map 1031 / 1105 / 20727 / 22274 alignments | extra data |

### §4.5 Where `0.940696` comes from
- `0.940696` is the **published expected median** in the upstream README, `~/Desktop/slorado_analysis/slorado/README.md:80` (BonsonW/slorado `754a74d`): "expected median identity scores for test/PGXXXX230339/reads_1k.blow5: FAST v5.0.0: 0.940696, HAC v5.0.0: 0.976852". It does not come from any of Riley's runs. HAC matches Riley's measurement exactly; FAST does not.
- Riley's measured FAST 1k medians depend on batch mode (from `load-imbalance/accuracy/minimap_accuracy-load-imbalance.csv`, where the same runs differ only in mode):
  - **0.940763**: fixed-C (padded) batches. Covers June upstream, overlap v1.0–v1.2 baseline and overlap, and every `fixedc` cell.
  - **0.940772**: narrow batches at flush ≥ 16 (full, 128, 64, 32, 16). Covers the Aug spec baseline and the cascade FAST baseline.
  - **0.940909**: narrow batches at very small flush (4, 12), and the June 10 run (`~/Desktop/slorado_analysis/results/output_fast_1k_accuracy.txt`).
- So the right reference for the speculative runs (narrow) is **0.940772**. The Oct FAST 1k delta is −0.005420 vs 0.940696 (as printed in the CSV), −0.005496 vs 0.940772, and −0.005487 vs 0.940763.
- FAST 20k (0.939375), HAC 1k (0.976852) and HAC 20k (0.977333) are identical in every stage that has accuracy. Only the June 10 FAST 20k differs (0.939295).

### Baseline accuracy held per stage
| Stage | FAST 1k | FAST 20k | HAC 1k | HAC 20k | File |
|---|---|---|---|---|---|
| June upstream | 0.940763 | 0.939375 | 0.976852 | 0.977333 | `baseline-results/*/*.json` |
| Overlap v1.0–1.2 (base and overlap) | 0.940763 | 0.939375 | 0.976852 | 0.977333 | `overlapping/overlapv1.*/accuracy/*.csv` |
| Load imbalance | 0.940763 fixed-C / 0.940772 narrow | 0.939375 (all modes) | 0.976852 (all modes) | none | `load-imbalance/accuracy/minimap_accuracy-load-imbalance.csv` |
| Cascade (FAST / HAC baselines) | 0.940772 | 0.939375 (constant, not re-measured) | 0.976852 | 0.977333 (constant) | `cascade/cascade-force-frac/accuracy/minimap_accuracy-cascade-frac.csv` |
| Spec, Aug | 0.940772 | 0.939375 | 0.976852 | 0.977333 | `spec-decode/2026080*/minimap_accuracy.csv` |
| `pre_overlap` `8873209`, CPU beam, all power runs | none | none | none | none | n/a |

---

## 3. Mismatches and surprises (most important first)

1. **§6 overlap "FAST −15 to −17 %" uses single power runs.** In the power runs, `ov_off`→`ov_on` is −17.2 % (1k) and −15.5 % (20k). In the multi-run runner data (n=10/3, CV < 0.6 %) the same step is **−11.5 % / −13.9 %** for v1.2 and −9.8 % / −11.7 % for v1.0 (`overlapping/overlap-report.md` §4). The power-run `ov_off` FAST 1k (12.356 s) is 8.3 % slower than the runner's v1.2 baseline mean (11.404 s), and also above its max (11.461 s). HAC −3 % agrees in both sources.
2. **§6 load imbalance "FAST 1k ≈ −12 %" is also a power-run artefact.** In the power runs `lb_baseline` (11.064 s) is 8.1 % slower than `ov_on` (10.234 s), even though the two configs are effectively the same (overlap on, fixed-C). In the sweep (`load-imbalance/jetson-artifacts/flush-sweep-results-no-output.txt`), flush=64 narrow (9.752 s) is −3.7 % vs flush=128 fixed-C (10.123 s) and −3.4 % vs the runner's v1.2 overlap mean (10.091 s). The report quotes about 4.8 %. The real load-imbalance result is the streaming one (−45 % at flush 64, −77 % at flush 4, against padded fixed-C).
3. **There is no single "original".** The "original" FAST 1k wall varies by source: 11.500 s (overlap-v1.0 runner baseline), 12.206 s (`baseline_results_v2`), 12.974 s (`pre_overlap` power run), 14.212 s (June upstream, `-C 256`, FASTQ out). The Oct speculative run vs each of these is −21.5 / −26.1 / −30.5 / −36.5 % (FAST 1k), −22.0 / −25.1 / −26.9 / −35.9 % (FAST 20k), and −4.0 / −7.6 / −8.0 / −10.3 % (HAC 1k). Also, inside the power harness `pre_overlap` (`8873209`) is 3.4–5.1 % slower than `ov_off` (`7cec6c5`, overlap off) on all four configs. So the overlap report's own-version baselines are already faster than the original, and nothing on the PC explains why.
4. **Cascade framing disagrees.** §6 calls it "negligible or worse". `cascade/casecade-final/2026-07-31-cascade-progress-report.md` §6–7 calls it a qualified positive: from force_frac 0.30 the median beats FAST (+0.0018 on 1k / +0.0007 on 20k), with a clearer win at 0.40 (+0.0066 / +0.0061), at 1.7× faster than HAC on 1k. It is always slower than FAST (2.4–3.1× at 0.30–0.40 across 1k and 20k). Its HAC anchor (49.187 s) was measured at flush=64, which is 8.6 % slower than HAC at flush=128 (45.305 s, Aug spec baseline). Against the faster HAC anchor, 0.40 is 1.56× faster than HAC, not 1.70×. The report text gives the FAST 1k baseline as 0.940763, but its CSV deltas are computed against 0.940772 (its own narrow baseline run).
5. **§6 HAC speculative "< 1 %" holds only for 20k.** HAC 1k is −1.6 % (Aug report), −1.0 % (power), −1.4 % (Oct vs Aug baseline). HAC 20k is −0.6 / −0.2 / −0.7 %.
6. **The stale-host note is out of date.** `fetch-and-minimap.py:100` already says `192.168.8.211` (changed in `350ebf6` on 2026-10-21), and no file contains `192.168.4.36`. These still say `192.168.4.48`: `fetch-and-plot-power.py:33`, `spec-decode/fetch-and-minimap-spec.py:36`, `load-imbalance/jetson-artifacts/fetch-and-minimap.py:40`, and every `JETSON_HOST` in `cascade/casecade-final/*.py`. Not edited.
7. **"16 GB Orin" in `overlapping/overlap-report.md:104` is wrong.** tegrastats shows `RAM …/7607MB`, `SWAP …/16384MB`, and the June JSON says "Jetson Orin Nano Engineering Reference Developer Kit Super", 7 GB. The handoff's 7.6 GB is correct.
8. **The CPU-beam baseline has no overlap.** `cpu-beam-search/cpu-beam-results.txt` and the `cb_off` power runs use `--cpu-beam=no` without `--overlap-decode`. So CPU beam is slower (+9.2 to +14.7 % runner; +2.3 / +18.3 / +10.5 / +18.6 % power) even against a weaker baseline. "Dead end" stands.
9. **`-C 256` OOM evidence is FAST, not HAC.** The PC data shows OOM at `-C 256` with overlap-decode on FAST 1k (`load-imbalance-report.md` §2, ~1.08 GB contiguous alloc). June upstream ran FAST at `-C 256` without overlap and finished. There is no HAC `-C 256` run on the PC, so "OOM on HAC" can't be checked here.
10. **Power-run resolution and state.** FAST 1k power runs have only 11–16 tegrastats samples (1 Hz), so 1k energy differences under about 10 % can't be resolved. The spec campaign ran with almost no swap in use (`peak_swap_mb` 0–214 on its first six runs), while every other stage peaked at ~1.4–1.6 GB of swap. Two HAC 20k spec-campaign attempts failed with `NvMapMemAllocInternalTagged … error 12` (OOM) before succeeding (`power-runs/spec-decode/raw/_failed_*`).
11. **Confirms §3 (lost code).** The Aug spec power runs record `git_commit=65246f2`, the same as the load-imbalance runs, on branch `speculative-decode`. So the spec code was uncommitted at the time. The Oct re-created code matches Aug almost exactly: repaired chunk counts are identical on 1k (1230, 881) and differ by 3 and 1 on 20k (27541 vs 27538, 19518 vs 19517). Medians differ by ≤ 0.000005, and alignment counts are equal except FAST 20k (20720 vs 20719).
12. **`baseline_results_v2/` is not in the handoff.** It is a second upstream-style baseline set: 12.206 / 48.375 / 212.114 / 927.336 s (FAST 1k / HAC 1k / FAST 20k / HAC 20k), default threads and `-B`, FAST at `-C 256`, FASTQ out. Its commit is not recorded.

---

## 4. Gaps for a fair original-vs-now comparison

| Needed | On the PC? | Cheapest way to fill |
|---|---|---|
| Same-harness "original" timing (warm-up, n=10/3, `-C 128`, `/dev/null`) | **No.** The nearest is the overlap-v1.0 `--overlap-decode=no` runner baseline, but that code is already 3.4–5.1 % faster than `pre_overlap` (§3 item 3). June / v2 differ in `-C`, threads and output | `rileys-runner.py` on `8873209` (or upstream `754a74d`) with `BASE_ARGS='-C 128 -c 12288 -K 4096 -p 150'`, n=10/3 + accuracy pass. About 1.4 h, mostly HAC 20k |
| Multi-run "now" timing | **No.** The Oct spec run is n=1. Non-spec best (overlap + narrow + flush) is n=1 everywhere (LB sweep, Aug spec baseline, power) | Same runner session as the row above, two more configs (best, spec). About 1.4 h each |
| Accuracy for non-spec best on HAC and 20k, same session as Oct spec | **Partly.** Aug-session values exist (`spec-decode/2026080*/minimap_accuracy.csv`), and these medians have been identical in every session so far, but they are not same-session | One keep pass per config with best args (about 18 min on the Jetson) + `fetch-and-minimap.py` on the PC |
| Accuracy for the original | June upstream: yes (equals fixed-C medians). `8873209`: no | Comes free with the accuracy pass in row 1 |
| Energy for the Oct re-created code | **No** | Power-record wrapper on `49ac641` for spec + best (8 runs, about 40 min) |
| Usable energy resolution at 1k | Weak (11–16 samples) | Repeat 1k power runs 3–5×, or quote energy at 20k only |
| Cascade vs best HAC | HAC anchor is at flush=64 | No re-run needed to re-quote against 45.305 / 871.297 s; a fully fair version would run the cascade's HAC pass at flush=128 |
| CPU beam vs an overlap baseline | Baseline lacks overlap | Probably not worth filling (dead end either way); state the caveat |
| §5 LSTM profile, depth-2, `cuda-lstm-gemm` / `flip-spike` | **No PC data** | Can't be checked from the PC |

**Claims the PC data cannot support**
- Overlap "−15 to −17 %" on FAST. Only single power runs show it; multi-run data says −11.5 / −13.9 %.
- Load imbalance "−12 %" on FAST 1k offline. Only a single power run shows it; the sweep says −3.4 to −5.9 % depending on the reference.
- A single "original vs now" percentage that doesn't name its baseline (FAST 1k range −21.5 to −36.5 %; HAC 1k −4.0 to −10.3 %).
- Any variance or confidence claim for speculative, load-imbalance, CPU-beam, cascade-20k or power numbers (all n=1).

---

## 5. Recommended next steps (Riley decides; none implemented)

- Pick one named "original" anchor. The cleanest is a same-harness runner pass on the pre-overlap commit. Until then, label the overlap-v1.0 baseline "same harness, overlap off" and June upstream "upstream defaults, different harness".
- Do one final `rileys-runner.py` session with three configs (original, best non-spec, spec), n=10/3 + accuracy pass, so timing and accuracy are same-session.
- Use runner multi-run numbers for per-stage percentages, and power runs only for energy and power (preferably at 20k).
- Correct §6: overlap FAST −11.5 / −13.9 %; load imbalance FAST 1k −3.4 to −5.9 % offline (depending on the reference), with the streaming numbers as the headline; speculative HAC −0.6 to −1.6 %.
- Decide how to frame the cascade (accuracy/speed knob between FAST and HAC, or dead end with a stated reason), and re-quote its HAC ratio against the flush=128 HAC time.
- Small fixes, if Riley wants them: "16 GB Orin" in `overlapping/overlap-report.md`; the stale `192.168.4.48` hosts; using 0.940772 (narrow) or 0.940763 (fixed-C) instead of 0.940696 for FAST 1k deltas.
