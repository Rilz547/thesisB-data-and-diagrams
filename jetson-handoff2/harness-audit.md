# Audit of the existing timing and power harness (2026-10-22)

**Purpose:** decide whether the harness used for the August power campaign and the October runner is good enough to produce the final report numbers, and what a final harness must fix.
**Method:** read the scripts (`docs-riley/power-record`, `power-sweep`, `parse_tegrastats.py`, `rileys-runner.py`), recompute from the saved August `samples.csv` files, and probe the Jetson's power sensor read-only.
**Verdict:** the *total-energy* figures are usable as rough, single-run indications at 20k, but **not** as final 1k numbers, and they cannot answer "did the GPU/CPU/RAM use less energy". The timing numbers are sound for what they measure. A new unified harness is needed (plan: `bench/`), validated before any re-testing.

---

## 1. Findings

| # | Finding | Evidence | Consequence | Fix in the final harness |
|---|---|---|---|---|
| 1 | **Energy window ≠ timed window** | 1k FAST: 14–15 tegrastats samples for a 11.9–12.2 s `Real time` (table 2). The log includes `sleep 1.2`, process start-up and an idle tail; the first sample of each log is idle (≈ 5.1 W, GR3D 0 %) | 1k energy includes ≈ 2–3 s of non-run power (≈ 10 %); wall time uses slorado `Real time` while energy uses the whole log | Recover slorado's start epoch from the first progress line (`[basecaller_main::<elapsed>*…]`) and integrate exactly over `[start, start + Real time]`. Works on every commit without changing slorado |
| 2 | **1 Hz sampling of a slowly updating sensor** | INA3221 `hwmon1`: `samples` = 512 (hardware averaging), shunts 5 mΩ. Reading `in1_input`/`curr1_input` 736 times in 1 s returned only **3 distinct** (V, I) pairs | The sensor refreshes ≈ 3 times a second as a moving average; a 1 Hz tegrastats sample sees roughly one third of the time. 1k runs have ≈ 10–15 samples | Read hwmon at 20 Hz, keep each distinct update per rail with a real timestamp, integrate over the exact window (**measured update, see §1b: this matters less than first thought**) |
| 3 | **Constant `dt = 1.0 s` assumed** | `parse_tegrastats.py` uses `energy = Σ P × 1.0`. HAC 20k: 891 samples for 893.7 s `Real time` plus start-up, so the real interval is ≈ 1.009 s | up to ≈ 1 % error | Use timestamps, not a constant |
| 4 | **Only total `VDD_IN` is summarised** | `samples.csv` already contains `VDD_CPU_GPU_CV` and `VDD_SOC`, but `summary.csv` has only the total | No per-rail comparison was ever made | Per-rail energy: `VDD_IN`, `VDD_CPU_GPU_CV`, `VDD_SOC` and the residual |
| 5 | **Large unmeasured residual** | residual = `VDD_IN − CPU_GPU_CV − SOC` is **29.8–37.1 %** of energy in all eight August runs (table 2) | "RAM/board used less energy" cannot be read from the rails | Report the residual explicitly; state the limitation |
| 6 | **CPU and GPU share one rail; no DRAM rail** | `hwmon1` channels: `in1` = `VDD_IN`, `in2` = `VDD_CPU_GPU_CV`, `in3` = `VDD_SOC` (only three) | GPU vs CPU vs RAM energy cannot be *measured* separately on this module | Optional calibration (CPU-only, GPU-only, memory-stress runs) to fit `P ≈ P_idle + a·cpu + b·gpu + c·mem`; label as **estimate**, report R² |
| 7 | **No idle baseline** | No script measures idle | Cannot separate "finished sooner" from "drew less power while running"; static power (≈ 5.1–5.6 W idle) is a large share, especially at 1k | 60 s idle before and after each stage block; report gross **and** net (idle-subtracted) energy and mean power |
| 8 | **One run per cell; timing and power from different runs** | `power-record` = 1 run; `rileys-runner.py` = timing only | No variance for power; time and power not from the same run | One driver captures timing, power and slorado's own stage timings in the same runs, n = 5 (1k) / 3 (20k) |
| 9 | **Only `/dev/null` is power-measured** | `power-record` always uses `-o /dev/null`; the only FASTQ-writing runs are the June upstream ones | The "end-to-end" headline (FASTQ written) has no matching data | Both modes in every cell |
| 10 | **Environment drift not controlled** | PC cross-check: the August speculative campaign ran with ≈ 0 swap versus 1.4–1.6 GB in other stages; two HAC 20k attempts hit NvMap OOM; scripts use only `sleep 8` between cells | Possible stage-to-stage confounders | Temperature gate, per-run swap/RAM/GPU-clock log, a fixed GPU micro-benchmark ("canary") and idle baseline before and after each stage block |
| 11 | **tegrastats `avg` adds no information** | The final `avg` equals `mean_power_mW` exactly (11021 mW for `cb_off_fast_1k`); it is the arithmetic mean of the same samples | None (a consistency check passed) | Keep tegrastats only as a cross-check during validation |

| 12 | **No check that a stage's output is correct** | The August `pre_overlap` baseline was never accuracy-checked (PC cross-check: no accuracy data). The 2026-10-22 smoke test found its output is garbage on DNA and RNA (0/1000 reads match `upstream`; slorado transposed scores to TNC while the pinned openfish reads NTC) | A broken decode was used as the "original" in every earlier comparison | New check **V7** (`analyze.py equiv`): each stage's kept FASTQ is compared read by read with the reference stage; unrelated output marks the stage INVALID, and `bench.py run` refuses invalid stages |

Storage is NVMe (`/dev/nvme0n1p1`), not SD; a 20k FASTQ is ≈ 157–159 MB.


### 1b. Measured update after building the new logger (2026-10-22)
- **Window mismatch is the dominant old-method error.** On FAST 1k (speculative, 4 runs) the energy over the whole process lifetime is **+9.2 %** above the energy over the exact timed window (137.0 vs 125.4 J), and the August tegrastats log (whole log incl. `sleep`, start-up, idle tail) gave ≈ 139 J for the same configuration (**≈ +11 %**). Process shutdown alone takes ≈ 0.9–1.2 s after slorado prints `Real time`.
- **1 Hz sampling is a small error for steady loads.** Emulating a 1 Hz sample-and-hold sampler on the same high-rate traces and comparing it with full-resolution integration over the same window gives **−0.5 % to +0.4 %** (FAST 1k). Finding 2 therefore matters mainly for bursty workloads, peak power and short windows; it is *not* the main reason to replace tegrastats. The validation phase (V2) re-measures this on HAC and 20k.
- **Rail update rates under load** (20 polls/s): `VDD_IN` ≈ 15 updates/s, `VDD_CPU_GPU_CV` ≈ 13/s, `VDD_SOC` ≈ 10/s while a job runs; at idle the SoC rail is constant for seconds (a held value, which integrates correctly under the backward-rectangle convention).
- **Window recovery works:** the start epoch reconstructed from progress lines agrees across the first six lines to 0.1–0.8 ms.
- **Rail shares in the new window:** `VDD_CPU_GPU_CV` 5.6 W, `VDD_SOC` 3.5 W, residual 4.7 W (≈ 34 % of 13.9 W).

## 2. Evidence tables

### Samples versus timed window, and rail shares (August CPU-beam power runs)
`extra` = number of samples minus `Real time` seconds. Energies in joules from the saved `samples.csv`, 1 Hz × instantaneous mW. `resid` = `VDD_IN − CPU_GPU_CV − SOC`.

| run | `Real time` s | samples | extra s | E total | E CPU_GPU_CV | E SOC | E resid | resid % |
|---|---|---|---|---|---|---|---|---|
| cb_off FAST 1k | 11.9 | 15 | 3.1 | 165 | 62 | 42 | 61 | 37.1 |
| cb_on FAST 1k | 12.2 | 14 | 1.8 | 181 | 72 | 46 | 62 | 34.5 |
| cb_off HAC 1k | 46.1 | 48 | 1.9 | 776 | 359 | 180 | 237 | 30.5 |
| cb_on HAC 1k | 54.5 | 56 | 1.5 | 882 | 395 | 209 | 277 | 31.4 |
| cb_off FAST 20k | 198.3 | 199 | 0.7 | 2765 | 1147 | 708 | 910 | 32.9 |
| cb_on FAST 20k | 219.2 | 220 | 0.8 | 3313 | 1411 | 843 | 1058 | 31.9 |
| cb_off HAC 20k | 893.7 | 891 | −2.7 | 15223 | 7183 | 3505 | 4534 | 29.8 |
| cb_on HAC 20k | 1059.5 | 1056 | −3.5 | 17513 | 7957 | 4193 | 5362 | 30.6 |

Reading: at 1k the window carries 2–3 s of extra samples (≈ 10–25 % of a 12 s run). At 20k the effective sampling interval is slightly over 1 s (negative `extra`).

### Sensor probe (read-only, idle)
- `hwmon1` = `ina3221`; labels `in1 VDD_IN`, `in2 VDD_CPU_GPU_CV`, `in3 VDD_SOC`; currents `curr1..3_input` in mA, voltages `in1..3_input` in mV; files readable without root.
- `samples` = 512 (averaging count); 736 reads/s returned 3 distinct (V, I) pairs.
- tegrastats `--interval` accepts a millisecond value, but the sensor's own update rate is the limit.
- GPU clock: `/sys/class/devfreq/17000000.gpu/cur_freq` (306 MHz idle; 918 MHz cap in 25W mode).

## 3. What the final harness can and cannot claim about energy

| question | answerable? | how |
|---|---|---|
| Did total energy go down? | yes | gross and net `VDD_IN` energy over the exact timed window, n ≥ 3, with variance |
| Is that from shorter time or lower power? | yes | compare mean net power and time separately; idle baseline subtracted |
| Did the CPU+GPU rail use less energy? | yes (combined) | `VDD_CPU_GPU_CV` energy |
| Did the SoC rail use less? | yes | `VDD_SOC` energy |
| Did "the rest" (DRAM, board) use less? | only as the residual | `VDD_IN − the two rails`; DRAM is inside it and cannot be isolated |
| Did the GPU alone / CPU alone use less? | **estimate only** | utilisation-based model calibrated with stress runs, reported with R² |

## 4. What is already fine
- `Real time` as the timing metric (excludes start-up; stable, CV < 0.6 % in the multi-run overlap data).
- Using `VDD_IN` as the module total (the parser deliberately avoids summing rails).
- Fairness settings: `nvpmodel` 25W, `jetson_clocks` off, one job at a time.
- The runner's warm-up, accuracy pass, hang recovery and resume logic (to be reused).
