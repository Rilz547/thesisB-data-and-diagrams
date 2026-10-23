# Reply 2 from the PC agent: harness history, script reuse, charts, hygiene, RNA/500k accuracy

**Written:** 2026-10-22 by the Claude Code agent on Riley's main PC, in reply to `AGENT-CONTEXT-final-test-campaign.md` (§5) and the files beside it in `jetson-handoff2/`.
**Scope:** read-only. No existing file was changed, nothing was committed, and no Jetson access was used (SSH needs Riley's password).
One short job was run, to answer §5 item 5: minimap2 on one existing 20k FASTQ (12 s), with its output in a scratch folder outside the repo. Paths are relative to `~/Desktop/thesisB-data-and-diagrams/` unless they start with `~`.
Note: the first reply is saved on the PC as `REPLY-from-PC-agent.md` (no date in the name); the Jetson files call it `REPLY-from-PC-agent-2026-10-22.md`.

**Short version**
- **June 23 baselines:** they ran in a separate Jetson checkout, `~/slorado`, not the fork `~/slorado-riley-v2` (`output_path` in every run JSON). The Desktop copy of that upstream repo is at `754a74d`. Their accuracy is valid (0.940763 / 0.939375 / 0.976852 / 0.977333), so they did not use the broken TNC layout.
- **`baseline_results_v2/`:** these were **not** plain upstream. Their console has per-layer timers (`conv_stack`, `rnns`, `flip`, `lstm`, `crf_1`) that the June 23 upstream console lacks, and the same timers appear in July 1 fork runs. They are hand-saved console copies from an instrumented fork build, dated 2026-06-30.
- **Early v0 history:** the GUI history starts before June 23. There is a June 10 shell pipeline and June 17 GUI runs with different flags. There are also two GUI versions on the PC.
- **Scripts:** `fetch-and-minimap.py` already accepts the new FASTQ names. But it would write new results into the **legacy** `overlapping/overlapv1.0/` folders, map RNA with the DNA preset and reference, and give RNA 1k files the DNA expected value. A patch is proposed in §5. Nothing was edited.
- **500k mapping on this PC:** about 4 min and ~16–18 GB RAM per FASTQ (from a measured 20k run: 12.4 s, 16.4 GB peak). The hg38 `.mmi` is cached. Disk is the constraint: **30 GB free**, and each 500k FASTQ is about 4.1 GB.
- **`pre_overlap` on the PC:** only `power-runs/baseline/20260811T124004Z/` and my first reply depend on it (§1b).

---

## 1. Harness-history gaps (answers to `harness-evolution.md` "Open items")

### 1.1 Which commit the June 23 baselines used
- No file records a commit. The run JSON (`baseline-results/*/*.json`) records `basecaller_version 0.5.0-beta`, the flags, and `output_path = /home/riley/slorado/benchmark_runs/<group>/<stamp>/…`.
- The output path shows they ran in **`~/slorado` on the Jetson, not `~/slorado-riley-v2`**. The fork existed from 06-23, and the GUI config was only pointed at the fork later: `~/Desktop/slorado_analysis/jetson_runner/config.json` has `slorado_root: ~/slorado-riley-v2`, last modified 2026-07-11.
- Supporting evidence (circumstantial):
  - `~/Desktop/slorado_analysis/slorado/` is an upstream clone at `754a74d`, its `.git/HEAD` dated 2026-06-10.
  - The June 10 pipeline (`~/Desktop/slorado_analysis/sync_and_analyse.sh`) pulled from `JETSON_PATH="~/slorado"`.
  - Upstream's next commit after `754a74d` is unknown here, but any clone made between 2026-05-07 and the next upstream push would be `754a74d`.
- The June 23 accuracy is valid (FAST 1k 0.940763, the fixed-C value), so those runs did not have the TNC/NTC bug.
- **To confirm on the Jetson:** `git -C ~/slorado rev-parse HEAD`, `git -C ~/slorado status --short` (uncommitted edits?) and `git -C ~/slorado submodule status`.

### 1.2 How `baseline_results_v2/` was produced
- **Files:** four console captures (`baseline_results_v2/{1k,20k}_{FAST,HAC}.txt`), all written 2026-06-30 between 16:35:35 and 16:40:17 +1000, and committed 2026-07-14 (`49bf637`).
- **Hand-saved, not live:** the four runs take ≥ 20 min, but the files were written within 5 min of each other. So they are pasted or saved console output, not files a tool wrote as it ran. There is no GUI JSON, telemetry, timestamps or accuracy.
- **Commands:** `./slorado basecaller -C 256` (FAST) / `-C 128` (HAC) `-o output_<model>_<size>.fastq …`, with no `-t`, `-B` or `-K`, so defaults apply (console shows `no. threads 8`, `read batch size 4096`).
- **Not plain upstream:** the console contains a per-layer timing tree (`conv_stack`, `conv[i]` with shapes, `rnns`, `rnn[i] (reverse|forward)`, `flip`, `lstm`, `crf_1`, `clamp`). The June 23 upstream console (`baseline-results/baselines-all-console-raw.txt`) has only `inference` and `decode`. The same tree appears in the July 1 fork runs (`~/Desktop/slorado_analysis/slorado_benchmarking/runs/fast_20k/2026070*/console.log`, `output_path` under `~/slorado-riley-v2`). So v2 came from an **instrumented fork build** around 2026-06-30. That fits the v1 "manual shell notes" era better than the GUI.
- **Validity (guess, needs a Jetson check):** v2 has no accuracy. The July 1 fork run at `-C 128 -c 12288 -K 2048` gave FAST 20k median **0.939375**, the valid value, in 212.598 s (`…/20260701_124216/run.json`). So the fork decoded correctly on July 1, and v2 (one day earlier) was probably valid too.
- **When the TNC bug appeared (hypothesis):** it may have arrived when openfish was repointed (2026-07-08, `6977ef3`) while slorado still transposed to TNC. **To confirm on the Jetson:** the dates of `8873209` and of the openfish bump to `3d95491`, and whether any fork commit between `d5a8737` and `e00bada` was used for other PC data (v2 and the July 1 runs are the only candidates I found).

### 1.3 Earlier versions, charts and scripts before June 23 (not in `harness-evolution.md`)
| date | what | where (PC) | notes |
|---|---|---|---|
| 2026-06-10 | **Shell pipeline v−1**: `sync_and_analyse.sh` rsyncs `~/slorado/output_*.fastq` from the Jetson (then `192.168.4.26`), maps with minimap2, `awk $10/$11 | datamash` | `~/Desktop/slorado_analysis/sync_and_analyse.sh`, results in `~/Desktop/slorado_analysis/results/`, console in `~/Desktop/first run` | The first pass used `--gpu-chain -t 1` **without `--secondary=no`**: 1318 alignments / median 0.934771 on FAST 1k. It was re-run the same evening with `--secondary=no`: 1031 / 0.940909. A methodology lesson worth one sentence. Basecall flags not recorded |
| 2026-06-17 | **GUI version 1** (`slorado_benchmarking`: `app.py`, `backend/`, `gui/`, `scripts/{fetch_runs.sh,analyse_runs.sh}`) | `~/Desktop/slorado_analysis/slorado_benchmarking/` | Older layout than `jetson_runner` (config holds the Jetson IP, PC paths and host details). Its `runs/` contains only the two July 1 runs |
| 2026-06-17 | GUI runs, FAST 20k and HAC 20k: `-C 250 -K 1000`, threads 8 / 4, FASTQ out, in `~/slorado` | `~/Desktop/fast_20k_20260617_233553.pdf`, `~/Desktop/hac_20k_20260617_235237.pdf` | `Real time` 458.514 s (FAST 20k) and 1071.86 s (HAC 20k). Exploratory flags, not a baseline |
| 2026-06-17 → 07-11 | **GUI version 2** (`jetson_runner`: `app.py`, `backend/{runner,telemetry,slorado_parser,run_store,system_info,config}.py`, `gui/main_window.py`) | `~/Desktop/slorado_analysis/jetson_runner/` | The PC copy of the Jetson GUI (files dated 06-17 → 07-11). `runner.py` never records a git commit |
| 2026-06-23 | GUI runs used as the June baseline | `baseline-results/` | JSON, `telemetry.csv`, PDF report, 9 PNG charts per run (CPU frequency, CPU %, GPU %, memory, power, temperature, 3 overview pages) |
| 2026-06-23 | `accuracy-analysis.drawio` (control-flow diagram, commit `61b167d`; its `.bkp` copy is also tracked) | repo root | |
| 2026-07-01 | GUI v1 runs on the fork: FAST 20k at `-c 12288` (212.598 s, 0.939375) and `-c 8192` (209.412 s, 0.93936) | `~/Desktop/slorado_analysis/slorado_benchmarking/runs/fast_20k/` | Fork build with per-layer timers; valid decode |

**What the GUI logged (for the v0 row):**
- Telemetry comes from `backend/telemetry.py`. For every sample it starts `tegrastats --interval 500`, reads one line, then sleeps `telemetry_interval_sec` (1 s). That is why the period is ≈ 1.52 s (median, all four June 23 runs) rather than 1 s.
- Columns: `timestamp, ram_used/total_mb, swap_used/total_mb, cpu0–5_util_pct, cpu0–5_freq_mhz, gr3d_freq_util_pct, temp_{cpu,soc0,soc1,soc2,gpu,tj}_c, power_vdd_in_mw, power_vdd_cpu_gpu_cv_mw, power_vdd_soc_mw, elapsed_sec`.
- `slorado_parser.py` extracts `model_initialization, data_loading, data_processing, parse, preprocess, runners, postprocess, inference, decode, data_output, data_free` (s), plus `Real time`, CPU time and peak RAM.
- The JSON also stores PC host details and the minimap2 version (`2.24-mm2-gb-biosys`).
- **Prototype only (never computed before):** trapezoid `VDD_IN` energy over the whole June telemetry log is FAST 1k 168 J, HAC 1k 807 J, FAST 20k 3211 J, HAC 20k 16087 J. These are FASTQ-writing runs, have ~1.5 s sampling and use the whole-log window, so the same caveats apply as for August.

### 1.4 PC analysis scripts: dates and purpose
First and last commit dates in this repo; `git log --follow` merges copies, so "first" can predate the file's own folder.

| script | first → last commit | purpose |
|---|---|---|
| `fetch-and-minimap.py` | 2026-07-14 → 2026-10-21 (9 commits) | Main accuracy pass: pull `output_*.fastq` (+ nsys, runner summaries), minimap2 vs hg38, `minimap_accuracy{APPEND}.csv`, auto `nsys stats` |
| `load-imbalance/jetson-artifacts/fetch-and-minimap.py` | 2026-07-14 → 2026-08-02 | An older copy, glob `output_*_overlap-li-*.fastq` |
| `nsys-profile-basecall.sh` | 2026-07-14 | v1 Nsight script (base + overlap callers, `-K 2048`, FASTQ out). It predates the Jetson's `nsight_runner.sh` (07-15) |
| `cpu-beam-search/cpu_beam_test.py` | 2026-07-22 → 07-23 | CPU-beam driver. The 07-22 results (2.52–3.34× slower, n=5/1) are in git at `ee4859e:cpu-beam-search/cpu-beam-results.txt`; the 07-23 re-run (1.09–1.15×) replaced them |
| `load-imbalance/jetson-artifacts/{flush_sweep,batchwidth_sweep,li_spotcheck}_test.py` | 2026-07-28 → 08-02 | Load-imbalance drivers (Jetson-side copies) |
| `cascade/casecade-final/*.py`, `cascade_20k_missing_rerun.sh` | 2026-08-02 | Cascade sweeps, accuracy, router calibration (copied in one commit) |
| `fetch-and-plot-power.py` | 2026-08-11 (4 commits) | Pull `docs-riley/power-runs/`, per-run time-series PNGs, summary bars |
| `spec-decode/fetch-and-minimap-spec.py` | 2026-08-11 | Speculative sweep accuracy (time + keep passes, manifest join) |

**Also:** the runner existed before 07-21. Commit `f67e495` (2026-07-19) says "redoing the runner and fetch/minimap files so that i can get variance calculations", and the first `riley-runner-*` summaries were committed 2026-07-20 (`def7ad4`, `9a68a57`). `harness-evolution.md` dates v2 to 2026-07-21.

## 1b. PC data that depends on `pre_overlap` (`8873209`)
- `power-runs/baseline/20260811T124004Z/`: `all_summaries.csv`, `raw/all_summaries.csv`, four `raw/*_pre_overlap_*/` folders (meta, samples, summary, tegrastats, logs), four per-run chart folders (4 PNGs each), and `charts/summary_{wall_s,peak_power_W,energy_J}.png`. That is 19 charts, all built only from `pre_overlap` rows. The folder name `baseline` is misleading now.
- `REPLY-from-PC-agent.md`: §2 (§4.3 row), §3 item 3 ("pre_overlap 3.4–5.1 % slower than ov_off"; the FAST 1k −30.5 % / FAST 20k −26.9 % / HAC 1k −8.0 % "original vs now" figures), §4 gaps rows 1 and 4, and §5 bullet 1. **Withdrawn:** that bullet recommended a same-harness re-run of the pre-overlap commit; use `upstream` / `ov_off` instead.
- Nothing else references it: no other chart, canvas (`*.canvas.tsx`) or report on the PC (searched for `pre_overlap`, `pre-overlap`, `8873209`, the stamp). `baseline-results/` (June 23) is upstream, not `pre_overlap`. No accuracy data for `pre_overlap` exists on the PC.

---

## 2. Reuse assessment of the PC scripts (no edits made)

| script | assumes | needed for the §4 schema |
|---|---|---|
| `fetch-and-minimap.py` | **Inputs:** remote glob relative to `~/slorado-riley-v2` (`--glob "docs-riley/bench-runs/kept/output_*.fastq"` works). Name regex `output_(fast\|hac)_(\d+k)(_overlap)?(.*)\.fastq`: new names parse, with size `1k`/`20k`/`190k`/`500k` and APPEND `-dna-spec`. From APPEND it looks for `riley-runner-*{APPEND}.*` and `nsys_*` files in the repo root (missing → warnings only). **Mapping:** one preset (`map-ont`) and one reference (hg38, cached `.mmi`) for every file. `expected_for()` checks the substring `"1k"` and model only, so an RNA 1k file gets the DNA value. **Outputs:** `ACCURACY_DIR`, `RUNNER_SUMMARY_DIR`, `NSIGHT_STATS_DIR` are all under `overlapping/overlapv1.0/`; scratch is `slorado-accuracy-tmp/` (4 legacy FASTQs present) with an append-only `minimap_results.csv`; pulled FASTQs are deleted after mapping. **CSV:** `timestamp, fastq, n_reads, n_alignments, map_rate` (string with `%`), `accuracy_median, expected_median, delta_vs_expected, nsys_pulled, reference`. **Median:** all PAF lines with `col11 > 0`, primaries + supplementaries. This is the preprint's `awk $10/$11 \| datamash median` (checked: identical result, 0.934853 / 20720, on the Oct FAST 20k FASTQ) | Per-file dtype → preset + reference (§5); new output dirs and scratch dir (§4); run with `--no-runner-summaries --no-nsys`, or default them off; replace `expected_for()` with parsed fields; add `dtype, model, size, stage, cell, preset, reference, minimap2_version` columns and a numeric `map_rate` so the CSV joins to `runs.csv` on `(stage, cell)` |
| `fetch-and-plot-power.py` | Remote `docs-riley/power-runs/<run>/` folders + `all_summaries.csv`. `samples.csv` columns `power_total_mW, gr3d_pct, cpu_mean_pct, ram_used_mb, swap_used_mb` (and the `VDD_*` columns, unused). Summary columns `label, model, size, wall_s, peak_power_mW, energy_J`. One bar per run, no grouping or error bars. Output root hard-coded to `power-runs/cpu-decode/`, with a new UTC stamp folder per pull (`exist_ok=False`, so it can't overwrite). Host `192.168.4.48` (stale) | Read `runs.csv`; group by `stage × cell × mode`, mean ± sd over `rep`; read `power/<run-id>.csv` (column names to be fixed in `HARNESS-FROZEN.md`); stacked per-rail plots; shade the timed window; new output root. Easier to write a new `analyze`-side plotter than to patch it |
| `spec-decode/fetch-and-minimap-spec.py` | `docs-riley/spec-decode-sweep/<stamp>/{fastq,logs,manifest.csv}`. Manifest columns `tag, model, size, flush, phase (time\|keep), speculative, q_thr, margin_thr, brave, fastq, real_s, cpu_s, peak_ram_gb, drafted, repaired, alpha, draft_s, repair_s, log`. Writes `overlapping/spec-decode/<stamp>/` (that folder was later moved to `spec-decode/`, so a re-run would recreate the old path); scratch `slorado-accuracy-tmp-spec/` | Not needed for the campaign. But its manifest has α, drafted, repaired, draft_s and repair_s, which the proposed `runs.csv` **lacks**. Suggest adding `spec_drafted, spec_repaired, spec_alpha, spec_draft_s, spec_repair_s` (blank for non-spec stages) |
| `cascade/casecade-final/fetch-and-minimap-cascade{,-frac}.py`, `cascade_*_minimap.py`, `cascade_router_calibrate.py` | `docs-riley/cascade-*-sweep/<stamp>/…`, `output_cascade_*`; write `overlapping/cascade/…` (now `cascade/`); host `192.168.4.48` | Not reusable (cascade isn't in the campaign); reference only |
| `load-imbalance/jetson-artifacts/fetch-and-minimap.py` | Older copy of the main script, glob `output_*_overlap-li-*.fastq`, scratch `slorado-accuracy-tmp/` | Superseded; don't use |
| Drivers (`*_test.py`, `cpu_beam_test.py`, `nsys-profile-basecall.sh`) and canvases (`*.canvas.tsx`, hard-coded numbers) | Jetson-side or presentation-only | None |

---

## 3. Chart and table templates (from `runs.csv`; P = can be prototyped from legacy data today)

**Figures**
1. **H1 time by stage, per cell**: bars, mean ± sd, a line at the named baseline. *P*: overlap v1.0–1.2 only (runner, n=10/3); other stages n=1, no error bars.
2. **H2 time by stage**: same layout. *P*: June upstream, plus Aug spec `keep` passes (n=1).
3. **H2 − H1 per cell** (output-mode cost; validation V6). *P*: 60 legacy back-to-back pairs (Aug spec time/keep, load-imbalance no-output/with-output). FASTQ-out minus `/dev/null` has a median of **+0.04 %**, range −1.0 to +2.3 % (single runs). This supports "H2 ≈ H1" but sits inside single-run noise.
4. **Cumulative speed-up S vs upstream**, plus an **incremental-factor waterfall** (factors multiply). *P*: power chain `ov_off → ov_on → lb_best → spec` (single runs).
5. **Energy by rail, stacked** (`CPU_GPU_CV`, `SOC`, residual), gross and net. *P*: gross only, from August `samples.csv` (whole-log window). Residual is 29.4–37.1 % across all 36 August runs and 30.1–38.0 % in the June telemetry. Net is impossible without idle baselines.
6. **Net energy vs time scatter** per cell, stage markers, iso-mean-power lines (separates "finished sooner" from "drew less"). *P*: gross version.
7. **Mean net power by stage**, testing the "energy tracks time, peak power flat" claim. *P*: gross.
8. **Accuracy Δpp vs speed-up trade-off**, one point per stage and config, DNA and RNA panels. *P*: Aug spec grid, cascade force-frac, RNA threshold grid (Jetson `explore.jsonl`).
9. **Per-stage profile shares** (infer, decode, other; infer∥decode overlap), stacked. *P*: partial, from console `inference` / `decode` lines and load-imbalance `infer_s` / `decode_s`.
10. **Run-to-run variability**: CV % per cell and stage. *P*: overlap runner data.
11. **Throughput (samples/s)** by dataset (DNA/RNA × 1k/20k/500k) with the preprint Xavier line (3.24 M samples/s). *P*: DNA 1k/20k only.
12. **Harness-evolution timeline** (v−1 … v5, dates, what each measured). *P*: yes, from §1 and `harness-evolution.md`.
13. **Window-alignment illustration**: power trace with process lifetime vs the timed window shaded. *P*: roughly, with an August 1 Hz trace; better from a new high-rate trace.
14. **Drift panel**: canary time, idle power and temperature before and after each stage block. *P*: no.
15. **Streaming flush sweep** (load imbalance; not in the final campaign, kept as a legacy figure). *P*: yes (`flush-sweep-timings-no-output.csv`).

**Tables**
1. Stage, commit, branch, flags, validity (V7).
2. Per cell × stage × mode: n, mean ± sd, min, CV; `d` and `S` vs the named baseline.
3. Incremental vs cumulative chain.
4. Energy gross / net per rail, mean power, J per 1k reads, J per Mbase.
5. Accuracy: median, map rate, Δpp, relative error change, V7 status.
6. Spec α / drafted / repaired per cell, DNA and RNA.
7. 500k vs the preprint (`Real time`, `wall_process_s`, samples/s, identity, with the hardware/CUDA/power-mode differences stated).
8. Validation V1–V7 results.
9. Datasets.

---

## 4. Legacy-data hygiene

**Risks of overwriting or confusing legacy and new data**
1. **Accuracy into the legacy folder:** `fetch-and-minimap.py` writes into `overlapping/overlapv1.0/{accuracy,runner-summaries,nsight-stats}/` by default. New `minimap_accuracy-dna-spec.csv` etc. would land beside legacy v1.0 data (as the Oct re-run already did).
2. **Mixed scratch folder:** `slorado-accuracy-tmp/` holds 4 legacy FASTQs (`output_*_overlap-spec-q10m2brave.fastq`) and an append-only `minimap_results.csv`. `--accuracy-crunch` maps every `output_*.fastq` there, so legacy and new files would be mapped together into the same CSV names. `confirm_dest_writes` prompts only on exact name collisions.
3. **Default remote glob:** `REMOTE_FASTQ_GLOB = "output_*.fastq"` matches the Jetson repo root, where legacy FASTQs may still be. Always pass `--glob "docs-riley/bench-runs/kept/output_*.fastq"`, or change the default.
4. **Power folder:** `power-runs/` already has stage folders `baseline/` (= invalid `pre_overlap`), `overlap/`, `load-balance/`, `cpu-decode/`, `spec-decode/`, and `fetch-and-plot-power.py` adds new stamp folders under `power-runs/cpu-decode/`. A new `upstream` or "baseline" folder there would be easy to confuse with `power-runs/baseline`.
5. **Summary file name:** `all_summaries.csv` exists in five folders (and a duplicate under `raw/`). The new `runs.csv` doesn't clash, but don't reuse `all_summaries.csv`.
6. **Stage names:** legacy names differ from the new labels: `load-balance` / `load-imbalance` / `lb_best`, `cpu-decode` / `cpu-beam` / `cb_*`, `best_fast` used for HAC too. Map them explicitly in any cross-era table.
7. **Ignore rules:** `.gitignore` covers only `/slorado-accuracy-tmp`, `__pycache__/` and `.venv-power/`. New FASTQs (500k ≈ 4.1 GB each), PAFs and high-rate power traces need an ignore rule before anything is committed.

**Suggested scheme (Riley decides; nothing created)**
```text
final-campaign/                      # everything from the frozen harness, nothing else
  HARNESS-FROZEN.md                  # copy from the Jetson, with script hashes
  jetson/bench-runs/                 # verbatim mirror: runs.csv, runs.jsonl, power/, profile_by_stage.csv, smoke.jsonl, explore*.json
  accuracy/minimap_accuracy-final.csv   # one row per kept FASTQ, joins runs.csv on (stage, cell)
  charts/  tables/                   # generated only from the two folders above
  work/                              # gitignored: pulled FASTQs, PAFs
```
- Leave every legacy folder where it is.
- Optionally add a root `LEGACY.md` that lists the legacy folders, their harness version (v−1 … v4) and validity. Mark `power-runs/baseline/20260811T124004Z/` as **INVALID (`pre_overlap`, garbled decode)** there, rather than renaming it, because renaming breaks paths quoted in both replies.
- Use a separate scratch folder (e.g. `final-campaign/work/` or `slorado-accuracy-tmp-final/`). The existing 7.3 GB index can be referenced in place rather than copied.

---

## 5. RNA and large-dataset accuracy plan

### 5.1 Patch proposal for `fetch-and-minimap.py` (description; not applied)
```text
CONFIG
  REFERENCES = {
    "dna": {"fasta": ~/Desktop/slorado_analysis/ref/hg38noAlt.fa,
            "preset": ["-x", "map-ont"],  "mmi": slorado-accuracy-tmp/hg38noAlt.mmi},   # existing cache, reused in place
    "rna": {"fasta": ~/Desktop/slorado_analysis/ref/gencode.v40.transcripts.fa,         # copy from Jetson, check md5 a7c65af1…
            "preset": ["-x", "splice", "-uf", "-k14"], "mmi": None},                    # map from FASTA: 454 MB indexes in seconds, avoids a stale k
  }
  ACCURACY_DIR = final-campaign/accuracy ; LOCAL_WORK_DIR = final-campaign/work
  FETCH_NSYS = False ; FETCH_RUNNER_SUMMARIES = False ; REMOTE_FASTQ_GLOB = "docs-riley/bench-runs/kept/output_*.fastq"

parse_name(fastq)                       # new, used everywhere instead of the substring checks
  new:    output_(fast|hac)_(\d+k)-(dna|rna)-(.+)\.fastq   → model, size, dtype, stage
  legacy: output_(fast|hac)_(\d+k)(_overlap)?(.*)\.fastq   → dtype = "dna", stage = suffix
  cell = f"{dtype}_{model}_{size}"      # matches stages.json ids; 190k = chr22

run_minimap2(fastq, dtype)
  ref = REFERENCES[dtype]; target = ref["mmi"] or ref["fasta"]
  cmd = [MINIMAP2, "-c", *ref["preset"], "-t", N, "--secondary=no", target, fastq]   # identity unchanged: col10/col11, all lines

plausibility (replaces expected_for / EXPECTED_MEDIAN; flag only, never a delta baseline)
  (dna, fast, 1k): 0.940772 narrow stages (lb_best, spec) | 0.940763 fixed-C stages (upstream, ov_off, ov_on)
  (dna, fast, 20k): 0.939375   (dna, hac, 1k): 0.976852   (dna, hac, 20k): 0.977333
  (rna, fast, 1k): 0.934396 and (rna, hac, 1k): 0.971673   ← Jetson smoke test, not PC-verified
  rna any size: band 0.931–0.934 FAST / 0.967–0.972 HAC (preprint full UHRR, A100)
  (dna, fast, 500k): 0.939837 (preprint Xavier, -c 10000; approximate for -c 12288)
  Δ for the report = vs the reference stage of the same cell (from runs.csv), computed in analysis, not here

output CSV columns
  timestamp, fastq, dtype, model, size, stage, cell, n_reads, n_alignments, map_rate (float),
  median_identity, plausible (yes/no), preset, reference, minimap2_version
map-rate colouring: keep the 50 % warning for both types (RNA ≈ 97–98 %)
```

### 5.2 Capacity on this PC (measured on 2026-10-22, plus an extrapolation)
- **Hardware:** Ryzen 7 9800X3D (16 threads), 60 GiB RAM, minimap2 `2.24-mm2-gb-biosys`. `-x splice -uf -k14` are standard options in 2.24, but RNA mapping with this build is **untested here**; cross-check one RNA 1k file against the Jetson's `accuracy.py`.
- **hg38 index:** cached at `slorado-accuracy-tmp/hg38noAlt.mmi` (7.26 GB, built 2026-10-05, `map-ont`). The script reuses it while its mtime is ≥ the FASTA's.
- **Measured (DNA FAST 20k, Oct spec FASTQ, `-t 16`):** 12.35 s wall (3.4 s index load + 8.7 s mapping), peak RSS 16.4 GB, PAF 18.9 MB. The result matches the stored CSV exactly.
- **500k (extrapolated, a guess):** ~25 × the mapping work, so about **4 min** per FASTQ. Peak RAM about 16–18 GB (index-dominated). FASTQ ≈ 4.1 GB (164 MB × 25), PAF ≈ 0.5 GB. The 3–5 planned 500k runs take ~12–20 min of mapping in total.
- **Transfer:** the June 10 rsync ran at ≈ 22–26 MB/s, so ≈ 3 min per 500k FASTQ. chr22 (190k) is ≈ 1.6 GB, ≈ 1.5 min to map.
- **Disk:** only **30 GB free** on `/`. Pull, map and delete one 500k FASTQ at a time (the script's default deletes FASTQs). Don't keep 500k FASTQs on the PC.
- **RNA:** no RNA FASTQ or GENCODE file is on the PC, so there is no measurement. Expect seconds for 1k and well under a minute for 20k (guess).

### 5.3 Needed from the Jetson
- `~/data/ref/gencode.v40.transcripts.fa` (454 MB).
- One small RNA FASTQ already scored on the Jetson (e.g. `rna_fast_1k` upstream, 0.934396) to cross-check the PC result.
- The final kept FASTQ list.
- `HARNESS-FROZEN.md` with the `power/<run-id>.csv` column names.

---

## 6. Sanity questions and inconsistencies

1. **Inconsistent kept-FASTQ names.** `AGENT-CONTEXT…` §4 gives `output_<fast|hac>_<1k|20k>-<stage>.fastq` (no dtype), while §4b and `datasets.md` give `output_<fast|hac>_<size>-<dna|rna>-<stage>.fastq`. The parser in §5.1 needs the dtype form. Please fix §4.
2. **Worked examples still use the withdrawn baseline.** `measurement-methodology.md` §2 uses `pre_overlap` 12.974 → 9.023 s (the arithmetic is right: −30.5 %, 1.44×, +43.8 %), and its chain starts at `pre_overlap` 217.121. Both should move to a valid baseline. For example, the FAST 20k chain from `ov_off`: 206.102 → 160.951 s is factor 0.7809, −21.9 %, while the increments −15.48 / −0.21 / −7.40 sum to −23.09 %.
3. **The original-vs-overlap-off gap isn't explained yet.** With `pre_overlap` retracted, the open question moves to upstream vs `ov_off`. Smoke DNA FAST 1k mode B: upstream 14.94 s vs `ov_off` 12.81 s (single cold runs). PC: June upstream 14.212 s (`-C 256`, FASTQ, telemetry on), v2 fork 12.206 s (`-C 256`), runner `ov_off` 11.404–11.500 s (`/dev/null`). The pinned-buffer / async-copy hypothesis may still explain upstream → `ov_off`. I'd keep it open as a hypothesis for the campaign to test, rather than retracting it.
4. **Accuracy method text covers DNA only.** `measurement-methodology.md` §Accuracy and `report-results-outline.md` A.5 say "minimap2 `map-ont`". Add the RNA method (`splice -uf -k14`, GENCODE v40), and note that the identity definition counts all PAF lines including supplementaries.
5. **A cell marked "not on Jetson" is on the PC.** `2026-10-21-optimisation-summary.md` "Original versus now" table, HAC 20k, overlap-v1.0 runner baseline: the PC has 902.986 s (n=3, `overlapping/overlap-report.md` §5), giving 865.431 → **−4.2 % (1.04×)**.
6. **The proposed `runs.csv` has no speculative-decode columns** (α, drafted, repaired, draft/repair seconds). They are needed for D.3/D.4b tables (see §2).
7. **v0 start date.** `harness-evolution.md` dates v0 to June 23; PC data starts June 10 (shell pipeline) and June 17 (GUI, different flags). The June 10 `--gpu-chain`, no-`--secondary=no` pass is a pitfall worth a line in B.2.
8. **v2 date.** `rileys-runner.py` outputs exist from 2026-07-20 (`def7ad4`), not 07-21 (§1.4).
9. **Audit figures reproduce.** The `harness-audit.md` table 2 per-rail energies all reproduce from the PC `samples.csv` files. The residual range across all 36 August runs is 29.4–37.1 % (the audit's 29.8–37.1 % is the eight CPU-beam runs). One nuance: the first sample is ≈ 5.1 W in all eight, but GR3D is 0 % in only six (`cb_on_fast_1k` 53 %, `cb_off_hac_20k` 32 %).
10. **Units.** `datasets.md` "20k FASTQ ≈ 157–159 MB" is MiB; the PC files are 164.2 MB / 166.2 MB (FAST / HAC, decimal).
11. **Smoke results vs PC data.** `lb_best` "99–100 % identical reads" fits the PC medians (narrow 0.940772 vs fixed-C 0.940763 on DNA FAST 1k). `ov_off` "identical to upstream" fits too: the overlap v1.0–v1.2 baselines have exactly June upstream's medians and alignment counts on all four DNA cells.
12. **Checked and consistent:** the throughput examples (7.06 M and 4.52 M samples/s, 3.24 M for Xavier), the methodology pp and error-rate example (−0.550 pp, +9.3 % relative), the CPU-beam rail example (+19.8 / +23.0 / +19.1 / +16.3 %), the CPU-beam ranges (+2–19 % power-run wall, 2.5–3.3× July report), and the Aug tegrastats ≈ 139 J.

**Jetson-side items that would settle open points:** `git -C ~/slorado rev-parse HEAD` and `status`; the commit date of `8873209` and of the openfish move to `3d95491`; whether any other PC dataset came from a fork commit between `d5a8737` and `e00bada`.
