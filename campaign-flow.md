# Final campaign: how it runs, what it tests, what it records

Started 2026-10-23 by Riley on the frozen harness (`HARNESS-FROZEN.md`, stamp `d5704e0ec3e6`) with `bench/campaign.sh`. Everything below describes what the code does; exact flags are in `bench/stages.json` and in the `flags` column of `runs.csv`.

## 1. The shape of the campaign

One driver (`bench.py run`) measures **timing, power and slorado's own stage timings in the same run**. A call to `bench.py run --stage S --cells ...` is a **block**; `campaign.sh` makes one block per (phase, stage). Every call uses `--resume`, so a stop/restart skips runs already recorded OK.

### Phases (priority order, so a partial campaign is still usable)

| phase | what                                    | stages                                           | cells                                                                                           |
| ----- | --------------------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| 1     | 1k reads                                | `upstream`, `ov_off`, `ov_on`, `lb_best`, `spec` | DNA FAST 1k, DNA HAC 1k; plus RNA FAST 1k, RNA HAC 1k on `upstream`, `ov_on`, `lb_best`, `spec` |
| 2     | 20k reads                               | same five stages                                 | same cells at 20k                                                                               |
| 3     | 500k reads, FAST only                   | `paper_xavier`, `upstream`, `spec`               | `dna_fast_500k`, mode B, 1 rep, no warm-up                                                      |
| 4     | optional extras (dead ends / baselines) | `lb_baseline`, `cb_off`, `cb_on`                 | DNA FAST/HAC, 1k then 20k                                                                       |

Per cell: **1 uncounted warm-up** (mode A), then **mode A reps, then mode B reps**. Repeats: 1k cells 5 reps per mode, 20k cells 3, 500k 1 (mode B only). Expected timed runs: 387 (core 288, 500k 3, extras 96) plus 48 warm-ups. Time (from `bench.py plan`): core stages ≈ 22 h (mostly the 20k cells), 500k three runs ≈ 5–7 h, extras ≈ 8 h; **≈ 35 h in total**.

### Per-block sequence

1. Pre-flight (`check`): power mode 25 W, `jetson_clocks` off, no stray slorado/nsys, binary present, datasets present.
2. **Idle baseline (before)**: 60 s of the power logger with nothing running.
3. **Canary (before)**: a fixed cuDNN LSTM micro-benchmark (N = 128, T = 2048; 8 s warm-up + 7 repeats) under the power logger. It detects GPU-state drift between blocks.
4. For each cell: warm-up, mode A reps, mode B reps (details below).
5. **Canary (after)** and **idle baseline (after)**. Drift gates used in validation (canary ±3 %, idle ±0.3 W) can be re-applied to the campaign with `analyze.py validate`.

### Per-run sequence (every timed run)

- **Thermal gate:** wait until junction temperature ≤ 55 °C (up to 300 s) so every run starts from a similar thermal state.
- Record a system snapshot (clocks, RAM, swap, temperature, nvpmodel).
- Start the **power logger** (polls the INA3221 sensor at 20 Hz; the sensor itself updates about 3 Hz, so only distinct updates are kept). A 3 s idle **pre-shoulder**, then launch slorado.
- slorado runs with stderr piped so the harness can read its progress lines and the final `Real time` line.
- The **timed window** is `[realtime0, realtime0 + Real time]`: slorado's progress lines carry seconds since its internal start, and the first one, timestamped on arrival, gives that start in wall-clock time. This needs no change to slorado, so every historical commit works. Validation: start-epoch spread ≤ 2.3 ms across progress lines, `Real time` line agrees with the progress-line end within ±1 ms.
- Stop after a 4 s **post-shoulder**. Integrate power **over exactly the window** (not the whole process lifetime).
- **Mode B** (end-to-end): slorado writes a FASTQ on the NVMe; the FASTQ is counted (reads, bases), a `sync` is timed, and the **first mode B run of each cell is kept** for the accuracy step; the others are deleted. **Mode A** (raw speed): `-o /dev/null`.
- Safety: a stall limit (900 s without progress) and a wall-time cap per run (2.5× expected); a failed run is killed and retried once.

## 2. What the stages are (all at `-C 128 -c 12288 -K 4096 -p 150` unless stated)

| stage              | commit    | what it is                                                                               | extra flags                                                                                                               |
| ------------------ | --------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `upstream`         | `754a74d` | unmodified upstream BonsonW/slorado (0.5.0-beta, 2026-05-07), the true original          | none                                                                                                                      |
| `ov_off`           | `7cec6c5` | overlap-decode code, switched off (valid pre-overlap baseline)                           | `--overlap-decode=no`                                                                                                     |
| `ov_on`            | `7cec6c5` | overlap decode v1.2: GPU decode of batch N−1 runs concurrently with inference of batch N | `--overlap-decode=yes`                                                                                                    |
| `lb_baseline`      | `65246f2` | overlap on, padded fixed-width batches                                                   | `--overlap-decode=yes --fixed-c-batch=yes`                                                                                |
| `lb_best`          | `65246f2` | load imbalance: narrow batches + flush threshold                                         | `--overlap-decode=yes --fixed-c-batch=no`, `--flush-threshold=64` (FAST) / `128` (HAC)                                    |
| `spec`             | `49ac641` | speculative decode: greedy draft, quality/margin gate, beam repair, overlapped repair    | lb_best flags + `--speculative-decode=yes --spec-repair-threshold=10 --spec-margin-threshold=2 --spec-overlap-repair=yes` |
| `cb_off` / `cb_on` | `664bb1f` | CPU beam hybrid (dead end)                                                               | `--cpu-beam=no` / `yes`                                                                                                   |
| `paper_xavier`     | `406ab8c` | the commit and command line the preprint used on the Jetson AGX Xavier                   | `-t 8 -B 500M -K 2000 -c 10000 -C 200`                                                                                    |
| ~~`pre_overlap`~~  | `8873209` | **INVALID** (score-layout bug); refused by the harness                                   | —                                                                                                                         |

Models: DNA R10.4.1 e8.2 400 bps FAST and HAC v5.0.0; RNA004 130 bps FAST and HAC v5.1.0. Datasets: DNA 1k / 20k / 500k (HG002), RNA 1k / 20k (UHRR RNA004); see `datasets.md`.

## 3. What gets recorded

One JSON line per run in `bench-runs/runs.jsonl` (plus warm-ups, idle and canary records), a 20 Hz power trace (`power/*.csv.gz`) and the slorado console (`console/*.log`) per run. `analyze.py csv` writes `bench-runs/runs.csv` (56 columns, listed in `HARNESS-FROZEN.md`):

- **Timing:** `t_real_s` (slorado `Real time`, headline), `wall_process_s` (whole-process wall time, the `time` analogue), `cpu_s`, `peak_ram_gb`, `t_infer_s`, `t_decode_s`, `samples`, `samples_per_s`, `sync_s`.
- **Power and energy, per rail:** `VDD_IN` total (`in`), `VDD_CPU_GPU_CV` (`cg`), `VDD_SOC` (`soc`), and the residual (`resid` = in − cg − soc: board, DRAM, regulator loss, NVMe, fan). For each: gross energy `E_*_J` over the window, mean power, peak power (of ≈ 3 Hz moving averages), the block's idle power `P_idle_*_W`, and **net energy** `E_net_*_J = E − P_idle × T`. Also `J_per_1k_reads` and `J_per_Mbase`.
- **System state:** mean GPU clock and load, CPU utilisation, RAM and swap peaks, max junction temperature.
- **Speculative decode:** `spec_drafted`, `spec_repaired`, `spec_alpha` (accept rate), `spec_draft_s`, `spec_repair_s`.
- **Accuracy:** not measured on the Jetson for DNA. Median identity is computed afterwards from the kept mode-B FASTQ (DNA on the PC with `fetch-and-minimap.py`; RNA on the Jetson with `accuracy.py --dtype rna`) and joined with `analyze.py --accuracy`.

## 4. How the numbers will be reported (see `measurement-methodology.md`)

- **Two headline speeds:** H1 raw speed (mode A, `/dev/null`) and H2 end-to-end speed (mode B, FASTQ written), both `Real time`.
- **Differences:** time change `d = (t_new − t_base) / t_base`, speed-up `S = t_base / t_new`; chains multiply; every table names its baseline (`upstream`, `ov_off`, ...).
- **Statistics:** n, mean ± sd, min, CV per cell; a difference is "real" only if > 2× pooled sd and > 1 %.
- **Accuracy:** absolute percentage points of median identity, with the reference values for the batch mode.
- **Energy:** gross and net, per rail, plus residual; GPU, CPU and DRAM energy cannot be separated (one shared rail, no DRAM rail on the Orin Nano).

## 5. What each result feeds in the report (`report-results-outline.md`)

| result                                                                           | section                              |
| -------------------------------------------------------------------------------- | ------------------------------------ |
| mean ± sd timing and energy, `upstream` vs everything                            | D.4 combined effect; A.6 statistics  |
| `ov_off` → `ov_on`                                                               | D.1 overlap decode                   |
| `lb_baseline` → `lb_best`                                                        | D.2 load imbalance                   |
| `lb_best` → `spec`, accept rate, accuracy                                        | D.3 speculative decode; E.4 variants |
| RNA cells                                                                        | D.4b RNA                             |
| `paper_xavier` 500k vs preprint (144.4 min, 0.939837) and `upstream`/`spec` 500k | D.4c large dataset                   |
| `cb_off`/`cb_on`                                                                 | E.1 CPU beam (dead end)              |
| per-rail energy, net energy, J per read                                          | D.6 power and energy                 |
| idle/canary/validation records                                                   | B.3 validated harness                |

## 6. Not part of this run

- **Nsight profile per stage** (`profile_stage.py` is not written yet; nsys inflates wall time, so it runs in a separate pass after the timing campaign).
- Accuracy (after the campaign), `dna_chr22` (deferred), the isolated `-c 10000` test (later), cascade (accuracy/speed knob, no new data).
- The `pre_overlap` stage and any claim built on it are withdrawn.

## 7. Rules while it runs

No desktop use on the Jetson (a terminal or GNOME apps caused +2–7 % outliers and a canary step in validation run 1); monitor over SSH (`python3 bench.py status`, `tail -f bench-runs/campaign.log`); nothing else on the GPU; do not edit the five stamped harness files.
