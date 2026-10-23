# Measurement methodology — what we measure and how comparisons are made

**Status:** definitions agreed 2026-10-22; to be frozen together with the final harness (`harness-audit.md`, `bench/`).
**Applies to:** every timing, energy, profiling and accuracy figure in the report from the final campaign onward. Older figures keep their original harness and must be labelled as such.

---

## 1. The two headline speed statistics

| | **H1 — raw speed** | **H2 — end-to-end speed** |
|---|---|---|
| What it answers | How fast is the basecaller itself, without storage effects? | How fast is it when it also has to produce the output file a user would keep? |
| Command | `-o /dev/null` | `-o <FASTQ on the NVMe disk>` |
| Metric | slorado `[main] Real time` | slorado `[main] Real time`, plus the time of a `sync` after exit recorded separately |
| Includes | model initialisation, loading, preprocessing, inference, decode, stitching, FASTQ *formatting* | all of H1 plus the cost of writing the FASTQ through the page cache |
| Excludes | process and Torch/CUDA start-up | same, and background writeback to disk after the `sync` window |
| Prior data | all runner and power-campaign timings | only the June 2026 upstream runs (FASTQ written) |

Notes:
- Storage is NVMe (`/dev/nvme0n1p1`); the 20k FASTQ is ≈ 157–159 MB, so H2 should differ little from H1. This is an expectation to be **measured**, not assumed.
- The two numbers must never be mixed in one comparison. Every table states which headline it reports.
- Accuracy (median identity) is measured on the FASTQ produced in H2 mode.

## 2. How differences are expressed

Let `t_base` be the baseline time and `t_new` the new time (same headline, same config).

| name | formula | sign / reading |
|---|---|---|
| **Time change** `d` | `(t_new − t_base) / t_base × 100` | negative = faster. This is the convention used in the existing documents ("−30 %") |
| **Time reduction** `r` | `−d` | positive = faster |
| **Speed-up** `S` | `t_base / t_new` | `×`; > 1 = faster |
| **Throughput gain** `G` | `S − 1` (as %) | reads or bases per second increase |

**These are not interchangeable.** `r`, `S` and `G` describe the same change differently:

- 30 % less time (`r = 30 %`) is `S = 1.43×` and `G = +43 %` throughput.
- Real example, FAST 1k, overlap-v1.0 runner with `--overlap-decode=no` (11.500 s, same runner harness) → current speculative result (9.023 s): `d = −21.5 %`, `S = 1.27×`, `G = +27.5 %`. (The earlier pre-overlap example was withdrawn: `8873209` is an invalid baseline.)

**Convention for the report:** quote the time change `d` (or equivalently reduction `r`) *and* the speed-up `S`; do not quote `G` unless it is needed for a throughput statement, and never call a time reduction a "speed increase".

### Chains of stages
- **Incremental** change: each stage versus the *previous* stage.
- **Cumulative** change: each stage versus a *named baseline*.
- Percent changes **do not add; factors multiply.** Real example, FAST 20k power-campaign chain (single runs, valid stages only):
  `ov_off 206.102 → ov_on 174.192 (×0.8452, −15.48 %) → lb_best 173.820 (×0.9979, −0.21 %) → spec 160.951 (×0.9260, −7.40 %)`.
  The increments sum to −23.10 %, but the cumulative change is `0.7809 − 1 = −21.91 %` (`S = 1.28×`).
- Every table must name its baseline (e.g. "vs upstream `754a74d`", "vs overlap-off `7cec6c5`"; `8873209` is invalid and must not be used). There is no unnamed "original".

### Energy
- **Gross energy** `E = ∫ P dt` over the *timed window* (see §3), per rail and total.
- **Net energy** `E_net = E − P_idle × T`, where `P_idle` is the idle power measured immediately before and after each stage block and `T` is the window length. This separates "ran for less time" from "drew less power while running".
- Also reported: mean power, peak power (a 3 Hz moving average, so it under-states true instantaneous peaks), and **energy per 1000 reads** and **per megabase** so configs with different output sizes are comparable.
- Differences use the same `d`, `r`, `S` conventions as time.
- Rails on this module: `VDD_IN` (total), `VDD_CPU_GPU_CV` (CPU, GPU and CV share one rail), `VDD_SOC`, and the **residual** `VDD_IN − VDD_CPU_GPU_CV − VDD_SOC` (≈ 30–37 % of energy; board, DRAM, regulator losses, NVMe, fan). GPU and CPU energy cannot be separated by measurement; any split is a **calibrated estimate** and is labelled as such.
  Real example (FAST 20k, CPU-beam off → on, August data): total +19.8 %, CPU_GPU_CV +23.0 %, SOC +19.1 %, residual +16.3 %.

### Accuracy
- **Median identity** = median over all PAF lines of column 10 (matches) / column 11 (alignment block length), `--secondary=no`, following the Slorado/Openfish preprint (Supplementary Note 1). **DNA:** `minimap2 -cx map-ont --secondary=no hg38noAlt.fa reads.fastq`. **RNA:** `minimap2 -cx splice -uf -k14 --secondary=no gencode.v40.transcripts.fa reads.fastq` (GENCODE v40 *transcripts*, 246,624 sequences, not the genome). RNA is mapped on the Jetson (small reference); DNA is mapped on the PC (the hg38 index does not fit comfortably in 8 GB). Pitfall found in June 2026 analysis: omitting `--secondary=no` adds secondary alignments to the median and shifts it, so every accuracy table must state the flag.
- Differences are **absolute percentage points** (pp), not relative: 0.940772 → 0.935276 is **−0.550 pp**.
- For context also give the change in error rate `1 − identity`: 5.923 % → 6.472 % is **+9.3 % relative** (the same change looks very different depending on the convention).
- The baseline must match the batch mode: **0.940772** for narrow batches, **0.940763** for fixed-C (padded) batches. `0.940696` is the *published* value in the upstream README, not a measurement from these runs.

### Statistics
- Each cell reports `n`, mean, sample standard deviation, min and CV (sd/mean).
- A difference is called **real** only if it is larger than 2× the pooled standard deviation *and* larger than 1 %; otherwise it is "within noise".
- With n = 3 (20k) the standard deviation is itself uncertain; say so rather than quoting p-values.

## 2b. Metrics added for comparison with the preprint and across datasets

- **Whole-process wall time** `wall_process_s` = process exit minus process launch, recorded for every run. It includes start-up (Torch/CUDA init, model load) and shutdown, so it is the closest analogue of GNU `time`'s "elapsed" that the preprint used for its Jetson result. It sits next to slorado's own `Real time` (which excludes start-up); report both and name which one a comparison uses.
- **Throughput** `samples_per_s` = dataset sample count / `Real time`. It is hardware- and dataset-neutral, so runs on different datasets or devices can be compared. Sample counts are measured per dataset (`datasets.md`). Reference point: the preprint's Jetson AGX Xavier FAST run on HG002-dna-500k is 28,107,529,368 samples in 144.4 min ≈ **3.24 M samples/s**.
- **Paper-matched stage** `paper_xavier`: slorado commit `406ab8c` (the commit the preprint used on the Xavier), run with the preprint's flags `-t 8 -B 500M -K 2000 -c 10000 -C 200`, mode B, on the 500k dataset. It differs from the preprint run by hardware (Orin Nano vs Xavier), CUDA/torch versions (12.6 / 2.11 vs JetPack 5.1 / torch 2.0) and power mode; those must be stated with any comparison.

## 3. The timed window (energy alignment)
- Energy is integrated over exactly the interval slorado reports as `Real time`, not over the whole process or a tegrastats log.
- slorado prints progress lines as `[basecaller_main::<elapsed>*…]` where `<elapsed>` is seconds since its internal start (`realtime0`). The harness timestamps the first such line when it arrives and subtracts `<elapsed>` to recover the start epoch; the end is start + `Real time`. This needs no change to slorado, so it works on every old commit.
- Idle shoulders (a few seconds of logging before the process starts and after it exits) and a separate 60 s idle baseline are recorded for each stage block.

## 4. What is held constant
`nvpmodel` 25W; `jetson_clocks` off (GPU clock follows DVFS up to 918 MHz); one job at a time; temperature gate before each run; shared flags `-C 128 -c 12288 -K 4096 -p 150` plus per-stage flags; one uncounted warm-up per cell and mode; the same `models/` and `test/` data for all stages. GPU clock, temperature, RAM and swap are logged for every run so confounders can be checked afterwards.

## 5. Baselines available (all named, none "the original")

**Code lineage (baseline):** derived from upstream `BonsonW/slorado` commit **`754a74d`** ("Merge pull request #31 from BonsonW/dev", 2026-05-07 18:49 +1000), which reports version **0.5.0-beta** and pins openfish `2530c2a` (`warp9seq/openfish`, "comment out gfx1150 custom flag for release"). The repository was cloned 2026-06-23 and the first own commit is 2026-06-24 (`d5a8737`); every branch (`develop`, `speculative-decode`, `cpu-beam`, `temp`) shares merge-base `754a74d`. The Slorado/Openfish preprint used v0.4.0-beta (commit `499b1cb`, 2025-10-30), which is **140 commits earlier**; upstream has no git tags. Openfish was later pointed at the fork `Rilz547/openfish-riley` (2026-07-08, `6977ef3`, tracking upstream `dev` `b9569c6`) and then `speculative-greedy` (`5109673`).
| baseline | harness | note |
|---|---|---|
| June upstream `754a74d` | Jetson-runner GUI (June 23), FASTQ out, no warm-up | FAST ran at `-C 256` |
| `baseline_results_v2` | console runs, FASTQ out | commit not recorded |
| ~~`pre_overlap` `8873209`~~ | power-record (tegrastats 1 Hz) | **INVALID**: decodes garbage (score-layout bug, fixed in v1.0); withdrawn as a baseline |
| overlap off `7cec6c5` | runner and power-record | valid; output identical to upstream |
The final campaign re-measures these under one harness so they can be compared directly.

## 6. Output validity (V7)
A speed or energy comparison is only meaningful if the compared stages decode the same signal. Every stage's kept FASTQ is compared read by read with the reference stage (`analyze.py equiv`): identical or near-identical output passes; documented approximate stages (`spec`) and different chunk sizes (`paper_xavier`) are expected to differ slightly; unrelated output (similarity < 0.9 or length ratio outside 0.8–1.2) marks the stage INVALID. This caught `pre_overlap` (2026-10-22).
