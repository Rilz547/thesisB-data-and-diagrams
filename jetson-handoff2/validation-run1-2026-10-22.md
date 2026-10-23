# Harness validation, run 1 (2026-10-22 23:16 – 2026-10-23 00:05)

Harness version `d5704e0ec3e6`. Data: `bench-runs/validation.jsonl` (49 timed runs: spec/upstream DNA FAST 1k in ABAB blocks, spec DNA HAC 1k, spec DNA FAST 20k with tegrastats). Criteria were written before the run and are unchanged below.

## Result: 3 criteria failed, all coinciding with desktop activity on the Jetson
| check | result | detail |
|---|---|---|
| V1 logger vs tegrastats | PASS | worst mean-power difference 0.38 % (limit 3 %), 3 runs ≥ 60 s |
| V2 1 Hz emulation | info | mean −0.56 %, worst 3.65 % over 49 runs: the old 1 Hz sampler's error |
| V3 time CV | **FAIL** | worst 2.92 % (spec FAST 1k A, first block); also 2.97 % (upstream FAST 1k B, last block). HAC 1k CV 0.25–0.59 %, FAST 20k 0.30 % |
| V3 energy CV | PASS | worst 1.81 % |
| V4 start-epoch spread / anchor / exit gap / coverage | PASS | 2.3 ms; −0.8…0.9 ms; 0.54–1.46 s; 1.0000 |
| V4 idle shoulders | **FAIL** | 1 of 49 runs above 0.5 W (0.65 W); median pre/post difference 0.06 W |
| V5 idle power per block | PASS (6/6) | ≤ 0.12 W drift |
| V5 canary per block | **FAIL** (1 of 6) | `upstream-20261022T233545`: 168.9 → 162.5 ms (−3.77 %); a *permanent* step: every later canary is ≈ 162 ms |
| V5 order/drift (ABAB block means) | PASS (4/4) | spreads 0.23–1.79 % |
| V6 mode B vs A | info | −0.45 to −1.46 % (B not slower; `sync` ≈ 0.017 s) |
| V7 output equivalence | PASS | every valid stage matches `upstream` (or differs by design: spec, cb_on, paper_xavier); only the four `pre_overlap` files fail, as expected |

## What happened at the failures
Four runs were slow by 1.6–7 % (23:18:43, 23:32:02, 23:39:29, 23:40:15). The slow runs used the same process CPU time as their neighbours but higher system-wide CPU (18.6 % vs ≈ 13 % on the first one), i.e. something else was using the machine. The process list and journal show GUI activity on the Jetson during the run:
- 23:17:40 `gnome-terminal` started, `tail` at 23:18:26 → first slow run 23:18:43;
- 23:40:09 `gnome-calendar` and `seahorse`, 23:40:49 the NVIDIA display was re-probed (DisplayPort/HDMI) → the two upstream-B slow runs, the 0.65 W idle-shoulder run and the permanent canary step (the display compositor shares the GPU).
I issued no commands within ±20 s of three of the four slow runs (the fourth coincided with one light `head`). The GUI session shares the GPU and CPU with the benchmark, so desktop use during a run is a confound.

Runs that had no such activity were stable: HAC 1k (CV 0.25–0.59 %), FAST 20k (0.30 %), and the other eight FAST 1k groups (0.20–1.04 %).

## Consequence and next step
The criteria are **not** relaxed. The harness is not frozen on this run. Re-run the four ABAB FAST 1k blocks on an undisturbed Jetson (`bench/validate_rerun.sh`, writes `bench-runs/validation-rerun.jsonl`) and evaluate with the same `validate` command. If they pass, the failures are attributed to the disturbance; if they do not, the 1k cells need a different statistic or a longer run and that goes in the harness-evolution record.
Rule for the campaign (to be written into `HARNESS-FROZEN.md`): do not use the Jetson's desktop during runs; watch progress over SSH. Optional: boot to multi-user (no desktop) for the campaign; that needs `sudo` and is Riley's call.

---

# Run 2: quiet re-run (2026-10-23 00:21 – 00:47)
Data: `bench-runs/validation-rerun.jsonl` (40 timed runs, four ABAB FAST 1k blocks, no desktop use). Same criteria.

| check | result |
|---|---|
| V3 time CV | **PASS**: worst 0.45 % (limit 1 %) |
| V3 energy CV | **PASS**: worst 0.28 % |
| V4 start-epoch spread / anchor / exit gap / coverage | PASS: 0.8 ms; −0.7…0.9 ms; 0.55–0.95 s; 1.0000 |
| V4 idle shoulders | **FAIL as written**: max \|pre−post\| 0.58 W (limit 0.5 W), 1 of 40 runs (upstream A rep 5); median 0.06 W, p90 0.32 W |
| V5 canary / idle per block | **PASS** (4/4 each): canary drift ≤ 0.66 %, idle drift ≤ 0.04 W |
| V5 order/drift (block means) | **PASS**: spreads 0.01–0.45 % (first run: 0.23–1.79 %) |
| V6 mode B vs A | info: +0.08 % (spec), +0.34 % (upstream); `sync` 0.016 s |
| V2 1 Hz emulation error | info: mean −0.12 %, worst 0.86 % (n = 40) |

**The disturbance explanation is confirmed:** with the desktop untouched, time CV fell from 2.9 % to ≤ 0.45 %, the canary no longer stepped, and block means agree within 0.45 %.

**The shoulder criterion fails once, and not because of the disturbance.** Sensor noise alone cannot produce a 0.58 W difference: two 3.5 s means from the 60 s idle traces differ by median 0.06 W, p90 0.10 W, max 0.32 W (112 pairs). The four largest differences (0.32–0.58 W) all have the *post*-run shoulder **below** the pre-run one (e.g. 5.23 W pre, 4.66 W post; idle baseline ≈ 5.1 W), i.e. a real transient after the process exits (clocks and memory winding down), not leftover load. The shoulders are not used in the energy integral (that uses the exact timed window), and the block-level idle drift (V5) is ≤ 0.04 W.
Amendment (decided by Riley, 2026-10-23, after seeing the data): make the shoulder check one-sided, failing only if the post-run shoulder is **above** the pre-run one by more than 0.5 W (leftover load), and report the two-sided difference as information.
