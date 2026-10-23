# Evolution of the testing harness (for the report's methodology section)

**Compiled:** 2026-10-22 from git history, file contents and run artefacts on the Jetson. Items marked **[PC to confirm]** are things only the PC data can settle.
Each version lists what it measured, what was limiting, and why the next version replaced it. The audit behind the "limiting" column is `harness-audit.md`.

---

## Timeline

| version | when | tool | what it measured | main limitation | led to |
|---|---|---|---|---|---|
| **v0** | June 2026 (analysis scripts from 2026-06-10 and 2026-06-17; Jetson runs 2026-06-23) | **Jetson Runner GUI** (`~/Desktop/jetson_runner`: `app.py`, `backend/{runner,telemetry,slorado_parser,run_store,system_info}.py`, `gui/main_window.py`, `config.json`) | One run per config, FASTQ written. Parsed slorado's console into `run.json` (model init, load, processing, inference, decode seconds, `Real time`, CPU time, peak RAM) and logged telemetry (`telemetry.csv`: RAM/swap, per-core CPU %/MHz, GR3D %, temperatures, `VDD_IN`/`VDD_CPU_GPU_CV`/`VDD_SOC` mW) roughly every 1–1.5 s (median 1.52 s between rows in the FAST 1k run) | Single runs; no warm-up; FASTQ-writing runs only; power logged but not turned into energy; FAST ran at `-C 256` and HAC at `-C 128`; commit not recorded | Need repeated, warmed-up timing runs → v2 |
| **v1** | July 2026 | **Manual shell notes** (`docs-riley/testing`, 2026-07-08) and **Nsight runner** (`nsight_runner.sh`, added 2026-07-15 in `e00bada`, deleted 2026-07-28 in `574db27`) | Ad-hoc commands for 1k/20k with different batch sizes; `nsys` profiles of the base and overlap callers on 1k/20k FAST (and HAC) with the same flags | Manual, not repeatable, no statistics | Scripted variance runner → v2 |
| **v2** | 2026-07-20 (`rileys-runner.py`, first committed with overlap v1.2) | **`rileys-runner.py`** | Per config: one uncounted **warm-up**, an optional **accuracy / Nsight pass** that keeps a FASTQ and is excluded from the statistics, then *n* timed `/dev/null` runs (n = 10 for 1k, 3 for 20k) reporting mean, sd, min, max, range, CV. Console capture, **hang detection** (stall and wall-time limits, kill, GPU snapshot, one retry), lock file against two runs sharing the GPU, JSON state with `--resume`, report + CSV after every run | Timing and accuracy only: **no power**; overlap-era flags and one shared `EXTRA_ARGS` for FAST and HAC | Power needed for the thesis → v3 |
| **v3** | August 2026 (power runs 2026-08-11; sweep manifests 2026-08-02) | **`power-record`, `power-sweep`, `parse_tegrastats.py`** and **`spec-fastq-sweep` / `spec-sweep`** (PC side: `fetch-and-plot-power.py`, `fetch-and-minimap-spec.py`) | `power-record`: tegrastats at 1 Hz around one `/dev/null` run; total power = `VDD_IN`; energy = Σ P × 1 s; wall = slorado `Real time`; appends `all_summaries.csv`. `power-sweep`: matrix driver per stage (`--pre`, `--overlap`, `--loadimb`, `--cpu-beam`). `spec-fastq-sweep`: a timing pass (`/dev/null`) then a keep pass (FASTQ) per config for PC-side minimap2 | n = 1 per cell; tegrastats window ≠ timed window; 1 Hz sampling of a ≈ 3 Hz sensor; total rail only; no idle baseline; time and power in separate harnesses | Audit (`harness-audit.md`) → v5 |
| **v4** | 2026-10-05 | **`rileys-runner.py`, reconfigured** for the re-created speculative code | Two runs per config: FASTQ run (doubling as warm-up) then one timed `/dev/null` run; per-model flush threshold (`FAMILY_ARGS`); campaign flags | n = 1; timing only | Need a single validated harness for the final numbers |
| **v5** | planned (this plan, `docs-riley/bench/`) | **Unified harness: `bench.py` + `powerlog.py` + `analyze.py` + `profile_stage.py`** | See below | — | — |

## What changed along the way, in one line each
1. **Repeats.** v0 single runs → v2 warm-up + 10/3 runs → v5 5/3 runs for every cell, both output modes.
2. **Output mode.** v0 wrote FASTQ → v2/v3 `/dev/null` only → v5 measures both ("raw speed" and "end-to-end speed").
3. **Power.** v0 logged rails but never integrated → v3 total-rail energy from 1 Hz tegrastats → v5 a high-rate sensor log with per-rail, idle-subtracted energy over the exact timed window.
4. **Accuracy.** v0 minimap2 on the PC → v3/v4 keep-pass FASTQ → PC `fetch-and-minimap.py`, with the expected-median reference corrected (`0.940696` is the published upstream value, not a measurement).
5. **Robustness.** v2 introduced hang detection, retries, lock file and resume; v5 reuses that logic and adds a temperature gate, swap/RAM/clock logging, a fixed GPU micro-benchmark and idle baselines to expose drift between stages.
6. **One harness.** v2 (time) and v3 (power) were separate scripts that never measured the same run; v5 captures timing, power and slorado's own stage timings together.

## Findings from earlier versions that shaped v5
- The `pre_overlap` stage used as "the original" in v3 decoded garbage (score layout bug) and was never accuracy-checked; v5 adds a read-by-read output-equivalence check (V7) and refuses stages marked invalid.
- The single-run overlap and load-imbalance power numbers disagreed with the multi-run timings by up to 8 % (PC cross-check, items 1–2): single runs are not enough.
- Energy at 1k was dominated by start-up/idle samples and a 1 Hz sampler (`harness-audit.md` findings 1–3).
- Total energy alone could not answer which component saved energy (findings 4–6).
- No harness ever measured the same FASTQ-writing mode that the June baselines used, so the original-versus-now comparison mixed output modes.

## v5 design summary (details: the plan and `measurement-methodology.md`)
- **Power logger:** INA3221 (`hwmon1`) read at ≈ 10 Hz, de-duplicated to the ≈ 3 Hz real updates, timestamped; also GPU clock, CPU utilisation, RAM/swap and temperatures.
- **Window:** recovered from slorado's progress lines so no slorado change is needed on any commit.
- **Per cell:** warm-up, then n timed runs in mode A (`/dev/null`) and mode B (FASTQ on NVMe); one B run per cell kept for accuracy.
- **Per stage:** worktree + build, idle baseline and canary at start and end, an Nsight profile of 1k FAST and HAC in a separate pass.
- **Validation before use:** sensor cross-check against tegrastats, quantification of the old 1 Hz error, repeatability, window alignment, order/drift, mode A vs B; then frozen with script hashes in `HARNESS-FROZEN.md`.

## Open items **[PC to confirm]**
- Which exact `slorado` commit the June Jetson-Runner baselines used (the run JSON records `0.5.0-beta` and the command, not the commit).
- How `baseline_results_v2/` was produced (script, date, thread/batch defaults) and by which tool.
- Whether PC-side copies of `jetson_runner` exist in other versions, and any earlier charts/scripts that predate June 23.
- Dates and content of the PC analysis scripts (`fetch-and-minimap.py`, `fetch-and-plot-power.py`, `fetch-and-minimap-spec.py`) for the version table.
