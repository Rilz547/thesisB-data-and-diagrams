# HARNESS FROZEN — pending Riley's sign-off

**Frozen:** 2026-10-23 · **Harness version stamp:** `d5704e0ec3e6` (first 12 hex of SHA-256 over `bench.py`, `powerlog.py`, `canary.py`, `accuracy.py`, `stages.json`, in that order; printed by `bench.py check` and stored in every record). Any change to those five files changes the stamp; do not edit them during the campaign. `analyze.py` is outside the stamp (analysis can still be refined after data exists).
**Status:** all validation criteria pass on the quiet re-run, one criterion was amended (below). The campaign has **not** been started: `bench-runs/runs.jsonl` does not exist yet.

## File hashes (SHA-256, first 16 hex)
| file | hash | in stamp |
|---|---|---|
| `bench.py` | `b771fc0683e5865f` | yes |
| `powerlog.py` | `b8e63de324844db0` | yes |
| `canary.py` | `133246a3a2263a3a` | yes |
| `stages.json` | `571cfbc91681093d` | yes |
| `accuracy.py` | `4230677b4a5cecd9` | yes |
| `analyze.py` | `bf350aebc37bfeb3` | no |
| `setup_stages.sh` | `86daf0512cb5b74a` | no |
| `validate_runs.sh` / `validate_rerun.sh` | `5a3b9b69b51c25a6` / `1f893610e4424a27` | no |

## Validation record (details: `validation-run1-2026-10-22.md`)
Run 1 (`bench-runs/validation.jsonl`, 49 timed runs) was disturbed by desktop activity on the Jetson and failed V3 time CV (2.9 %), V4 shoulders (0.65 W) and one V5 canary (−3.77 %). Run 2 (`bench-runs/validation-rerun.jsonl`, 40 timed runs, desktop untouched) gives:
| check | criterion | result |
|---|---|---|
| V1 logger vs tegrastats (run 1, 3 runs ≥ 60 s) | mean power within 3 % | **PASS** worst 0.38 % |
| V2 1 Hz sampler error | informational | run 1: mean −0.56 %, worst 3.65 %; run 2: mean −0.12 %, worst 0.86 % |
| V3 time CV (n = 5 per group) | < 1 % | **PASS** worst 0.45 % |
| V3 energy CV | < 3 % | **PASS** worst 0.28 % |
| V4 start-epoch spread / anchor cross-check / exit gap / coverage | ≤ 5 ms / ≤ 25 ms / 0–3 s / ≥ 0.99 | **PASS** 0.8 ms / −0.7…0.9 ms / 0.55–0.95 s / 1.0000 |
| V4 idle shoulders (amended, below) | post − pre ≤ +0.5 W | **PASS** max +0.10 W (two-sided max \|pre−post\| 0.58 W, median 0.06 W, mean signed −0.05 W) |
| V5 canary drift per block | ±3 % | **PASS** 4/4, worst −0.66 % |
| V5 idle power drift per block | ±0.3 W | **PASS** 4/4, worst 0.04 W |
| V5 order/drift (ABAB block means) | within 2 % | **PASS** spreads 0.01–0.45 % |
| V6 mode B vs A | informational | +0.08 % (spec), +0.34 % (upstream); `sync` 0.016 s |
| V7 output equivalence | valid stages reproduce upstream | **PASS** except the four `pre_overlap` files (stage already INVALID and refused by the harness) |
| PC accuracy pipeline cross-check (2026-10-23, `fetch-and-minimap.py`, kept 1k FASTQs from the smoke/validation runs) | DNA median identity matches the published reference | **PASS** `upstream` FAST 0.940763 (published 0.940696; 0.940763 is the fixed-C value in the earlier PC data), HAC 0.976852 (exact). `ov_off`/`ov_on`/`lb_baseline`/`cb_off` identical to `upstream`; `lb_best` 0.940772; `spec` 0.935276 FAST (−0.55 pp) and 0.975490 HAC (−0.14 pp); `cb_on` 0.940528; `paper_xavier` 0.941212; `pre_overlap` 0.2–0.3 % of reads map (invalid, confirmed independently) |

**Amended criterion (decision by Riley, 2026-10-23, made after seeing run 2):** V4 idle shoulders was written as `max |pre − post| ≤ 0.5 W`. In the quiet run one of 40 runs gave 0.58 W, with the post-run shoulder *below* the pre-run one (a post-exit transient; sensor noise alone gives ≤ 0.32 W). The check exists to catch leftover load after the run, so it is now one-sided: fail if post exceeds pre by more than 0.5 W. The two-sided difference is still printed as information. Shoulders are not used in any energy figure. Under the original two-sided wording the run 2 result would be a documented 0.08 W exception.

## Known limitations (state in the report)
- **Desktop activity is a confound.** The Jetson runs a GNOME desktop that shares CPU and GPU; opening terminals/apps or changing the display during a run produced +2 to +7 % outliers and a permanent −3.8 % canary step. For the campaign: **do not use the Jetson's desktop; monitor over SSH only.** Optional (needs `sudo`, Riley's call): boot to `multi-user.target` without the desktop.
- `VDD_CPU_GPU_CV` is one rail; GPU, CPU and DRAM energy cannot be separated. The residual (`VDD_IN` minus the two measured rails) is ≈ 30–37 % of energy and is reported explicitly.
- 1k cells (≈ 10–15 s) can still show an occasional single-run outlier; analysis reports mean ± sd, min, CV and median, and flags runs > 3 % above the cell median.
- `dna_chr22` is deferred (file still in the Trash); the plan excludes it.
- The canary is a cuDNN LSTM (N = 128, T = 2048); it detects GPU-state drift, not DRAM or decode drift.
- Validation covered FAST 1k (both stages, modes A/B), HAC 1k and FAST 20k (spec) and V1 with tegrastats on one 20k block; RNA cells and `dna_500k` were smoke-tested only.

## Before the campaign
1. `bench-runs/kept/` holds 41 smoke / exploration / validation FASTQs (162 MB). Archive it so campaign FASTQs are unambiguous: `mv bench-runs/kept bench-runs/kept-prefreeze`.
2. `python3 bench.py check` must print `problems: none` and `harness version: d5704e0ec3e6`.
3. Nothing else on the GPU; nobody at the desktop; swap is not reset between stages (record only).

## Commands (Riley starts these; `--resume` skips runs already recorded OK; `bench.py status` shows progress)
`python3 bench.py plan` estimates ≈ 31.9 h for the full chain (`upstream` 5.3 h, `ov_off` 2.7, `ov_on` 4.6, `lb_baseline` 2.7, `lb_best` 4.6, `spec` 4.6, `cb_off` 2.7, `cb_on` 3.1, `paper_xavier` 1.7). Suggested priority so partial results are usable: run the 1k cells of every stage first, then 20k, then 500k.
```
cd ~/slorado-riley-v2/docs-riley/bench
# example, one stage at a time (default cells: DNA FAST/HAC 1k and 20k; RNA stages also RNA 1k/20k)
python3 bench.py run --stage upstream --resume
# 500k FAST (mode B, 1 rep): paper_xavier, upstream, spec
python3 bench.py run --stage paper_xavier --cells dna_fast_500k --resume
```
Records append to `bench-runs/runs.jsonl`; `analyze.py csv` writes `bench-runs/runs.csv`, `analyze.py tables` the markdown tables, `analyze.py validate` re-checks drift/order on the campaign data, `analyze.py equiv` is the V7 equivalence check.

## `runs.csv` columns (56)
`stage, commit, dtype, cell, config, size, dataset, mode, rep, t_real_s, wall_process_s, samples, samples_per_s, cpu_s, peak_ram_gb, t_infer_s, t_decode_s, window_start_epoch, window_s, idle_source,`
`E_in_J, Pmean_in_W, P_idle_in_W, E_net_in_J, E_cg_J, Pmean_cg_W, P_idle_cg_W, E_net_cg_J, E_soc_J, Pmean_soc_W, P_idle_soc_W, E_net_soc_J, E_resid_J, Pmean_resid_W, P_idle_resid_W, E_net_resid_J, Ppeak_in_W, Ppeak_cg_W, Ppeak_soc_W,`
`reads, bases, J_per_1k_reads, J_per_Mbase, gpu_mhz_mean, gpu_load_mean_permille, cpu_util_mean_pct, ram_used_mb_max, swap_used_mb_max, tj_max_C, sync_s,`
`spec_drafted, spec_repaired, spec_alpha, spec_draft_s, spec_repair_s, flags`
(`in` = `VDD_IN` total, `cg` = `VDD_CPU_GPU_CV`, `soc` = `VDD_SOC`, `resid` = in − cg − soc; `E_net_*` subtracts the stage block's idle power × window; `Ppeak_*` is a peak of ≈ 3 Hz moving-average updates.)

## Sign-off
- [ ] Riley has read this file and accepts the amended V4 criterion and the limitations.
- Then: archive `kept/`, start the campaign, and tell the Jetson agent (or the PC agent via the next handoff) when data exists.
