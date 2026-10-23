# Context for the PC agent: the final test campaign (read this before the next task)

**Owner:** Riley (thesis work, slorado on a Jetson Orin)
**Written:** 2026-10-22 by the Claude Code agent on the Jetson
**Follows:** your reply `REPLY-from-PC-agent-2026-10-22.md` (thank you — it was accurate and changed several of our headline numbers).
**Status of this file:** briefing only. No new analysis is required until the Jetson-side harness is finished and validated. Section 5 lists what is useful to do *now*.

---

## 1. Where we are

**Code lineage (baseline):** derived from upstream `BonsonW/slorado` commit **`754a74d`** ("Merge pull request #31 from BonsonW/dev", 2026-05-07 18:49 +1000), which reports version **0.5.0-beta** and pins openfish `2530c2a` (`warp9seq/openfish`, "comment out gfx1150 custom flag for release"). The repository was cloned 2026-06-23 and the first own commit is 2026-06-24 (`d5a8737`); every branch (`develop`, `speculative-decode`, `cpu-beam`, `temp`) shares merge-base `754a74d`. The Slorado/Openfish preprint used v0.4.0-beta (commit `499b1cb`, 2025-10-30), which is **140 commits earlier**; upstream has no git tags. Openfish was later pointed at the fork `Rilz547/openfish-riley` (2026-07-08, `6977ef3`, tracking upstream `dev` `b9569c6`) and then `speculative-greedy` (`5109673`).

- Your cross-check confirmed every number in the first handoff (all 36 power-campaign cells within 0.29 %) and corrected several claims. Jetson-side documents are being revised accordingly (overlap FAST −11.5 / −13.9 %; load imbalance FAST 1k −3.4 to −5.9 % offline; HAC speculative −0.2 to −1.6 %; cascade framed as an accuracy/speed knob; `-C 256` OOM qualified to overlap-on; `0.940696` identified as the upstream README value).
- A new finding to be aware of: the pre-overlap openfish used pageable `malloc` host buffers, synchronous copies and several `cudaDeviceSynchronize` calls per batch; the overlap commits replaced them with pinned persistent buffers and async copies, which also apply with `--overlap-decode=no`. This is a **code-reading hypothesis**, not measured, offered as a possible reason `pre_overlap` is 3.4–5.1 % slower than `ov_off`.
- Riley will **redo all tests under one frozen, validated harness** so that every stage is compared on the same footing. The existing data stays untouched as the legacy record.

## 2. What the Jetson agent audited and why the tests must be redone

Full detail: `harness-audit.md`. In short:
1. Energy was integrated over the whole tegrastats log, which includes start-up and idle samples, while time uses slorado `Real time` (≈ 10 % error on 1k runs).
2. The power sensor (INA3221) refreshes ≈ 3 times a second as a 512-sample moving average, so 1 Hz tegrastats samples see about a third of the time.
3. Only total `VDD_IN` energy was summarised; per-rail data existed but was never used. CPU and GPU share one rail (`VDD_CPU_GPU_CV`), and there is no DRAM rail, so GPU/CPU/RAM energy cannot be separated by measurement. About 30–37 % of energy is an unmeasured residual.
4. No idle baseline; one run per cell; timing and power from different harnesses; power only ever measured with `-o /dev/null`; environment drift (swap, temperature) not controlled.

## 3. What the final campaign will be

**Harness (being built and validated on the Jetson; nothing for you to run):** a unified driver that, per cell, does a warm-up and then repeated timed runs while a high-rate power logger records `VDD_IN`, `VDD_CPU_GPU_CV` and `VDD_SOC` (plus GPU clock, CPU utilisation, RAM/swap, temperatures), integrating energy over exactly slorado's `Real time` window, with an idle baseline subtracted. Validation (sensor cross-check, sampling-error quantification, repeatability, window alignment, drift, output-mode check) must pass before it is frozen.

**Two headline speed statistics (must stay separate everywhere):**
- **H1 raw speed:** `-o /dev/null`, slorado `Real time`.
- **H2 end-to-end speed:** `-o <FASTQ on NVMe>`, `Real time` (+ a separately timed `sync`).

**Campaign matrix (updated 2026-10-22; shared flags `-C 128 -c 12288 -K 4096 -p 150`, i.e. slorado's own defaults; `-c 10000` is used only by the paper-matched stage):**
- **Stages/labels:** `upstream` (`754a74d`, true original), `ov_off`, `ov_on` (`7cec6c5`), `lb_best` (`65246f2`), `spec` (`49ac641`, `q10_m2_brave`) — **`pre_overlap` (`8873209`) is dropped: the 2026-10-22 smoke test showed it decodes garbage (score layout bug), so it is not a valid baseline;** optional/last: `lb_baseline`, `cb_off`, `cb_on` (`664bb1f`); plus `paper_xavier` (commit `406ab8c`, the preprint's Jetson Xavier commit, with the preprint's flags `-t 8 -B 500M -K 2000 -c 10000 -C 200`).
- **Datasets** (details and checksums: `datasets.md`): DNA 1k (1,000 reads) and 20k (20,000 reads, 1.12 B samples), RNA 1k and 20k (UHRR RNA004; 20k = 772 M samples), and the large DNA tier `dna_500k` (500,000 reads, 28.1 B samples; verified identical in size to the preprint's HG002-dna-500k; downloaded from upstream's public S3). `dna_chr22` (189,928 reads) is registered as an optional middle tier.
- **Cells:** `<dtype>_<model>_<size>` (e.g. `rna_hac_20k`). DNA and RNA both FAST and HAC at 1k/20k; both output modes; 5 repeats for 1k, 3 for 20k; one uncounted warm-up per cell. RNA is run on `upstream`, `ov_on`, `lb_best`, `spec` only. The 500k tier is FAST only, mode B, 1 repeat, on `paper_xavier`, `upstream`, `spec` (and optionally `ov_on`, `lb_best`).
- **Extra per stage:** idle baseline and a fixed GPU micro-benchmark ("canary") before and after the stage block; an Nsight Systems profile of 1k FAST and 1k HAC (separate pass).
- **Accuracy:** the FASTQ kept from one mode-B repeat per cell, mapped on the PC. **RNA needs a different mapping step** (section 4b).
- **Cost:** about 29–31 h of Jetson time for core DNA + RNA, plus about 4.7 h for the three 500k runs; run in priority order (1k cells first), started by Riley.

**Definitions (full text: `measurement-methodology.md`):**
- Time change `d = (t_new − t_base)/t_base`; time reduction `r = −d`; speed-up `S = t_base/t_new`; throughput gain `S − 1`. They are **not interchangeable** (30 % less time = 1.43× = +43 % throughput). The report quotes `d`/`r` and `S`.
- Stage chains: incremental and cumulative; **factors multiply, percentages do not add** (FAST 20k example: increments sum to −28.2 %, cumulative is −25.9 %).
- Energy: gross and net (idle-subtracted), per rail plus residual, per 1000 reads and per Mbase.
- Accuracy: absolute percentage points of median identity, plus relative error-rate change; baseline reference depends on batch mode (0.940772 narrow, 0.940763 fixed-C).
- Statistics: n, mean ± sd, min, CV; "real" only if > 2× pooled sd and > 1 %.

## 4. Planned data outputs (so you can prepare analysis early)

Proposed locations (to be fixed in `HARNESS-FROZEN.md`):
- `docs-riley/bench-runs/runs.csv` — one row per run; `runs.jsonl` — the same plus raw detail; `power/<run-id>.csv` — the high-rate power trace for each run; `profile_by_stage.csv`.
- Kept FASTQs: `docs-riley/bench-runs/kept/output_<fast|hac>_<size>-<dna|rna>-<stage>.fastq` (e.g. `output_fast_1k-dna-spec.fastq`; named so the existing PC script accepts it: `--glob "docs-riley/bench-runs/kept/output_*.fastq"`) (the Jetson agent will list them and Riley will copy them to the PC).

`runs.csv` columns (proposed): `stage, commit, config, size, mode (A=/dev/null, B=FASTQ), rep, t_real_s, cpu_s, peak_ram_gb, t_infer_s, t_decode_s, window_start_epoch, E_total_J, E_cpu_gpu_cv_J, E_soc_J, E_resid_J, Pmean_total_W, Pmean_cpu_gpu_cv_W, Pmean_soc_W, Ppeak_total_W (≈3 Hz moving average), E_net_total_J, E_net_cpu_gpu_cv_J, E_net_soc_J, P_idle_total_W, gpu_mhz_mean, gpu_util_mean, cpu_util_mean, swap_mb, tj_max_C, canary_s, reads, bases, J_per_1k_reads, J_per_Mbase, median_identity, map_rate, flags`.

## 3b. New findings from the smoke test (details: `smoke-test-2026-10-22.md`)

1. **`pre_overlap` is invalid.** Its output is unrelated to the other stages (0/1000 reads identical to upstream on DNA and RNA). Cause: score layout mismatch (TNC vs NTC), fixed from v1.0. **Please check whether any PC data used it** (the August power campaign did: `power-runs/baseline/20260811T124004Z/`, `label=pre_overlap`; and any `fast`/`hac` accuracy or plots that treat it as "the original") and list every figure that depends on it; the earlier cross-check found no accuracy data for it.
2. **Speculative decode on RNA:** accept rate 0.91–0.93 but RNA FAST identity falls 4.2 pp (0.9344 → 0.8928) versus 0.55 pp on DNA; RNA HAC −0.35 pp. The DNA-tuned thresholds look too permissive for RNA FAST (hypothesis); a 13-setting threshold grid (`spec-threshold-exploration-rna.md`) found no setting that is both faster and accurate on RNA FAST, so the campaign keeps Q = 10 / margin = 2 for all datasets and reports the result as is.
3. **A new validity check V7** compares every stage's kept FASTQ with the reference stage read by read; it will run on the final data.

## 4b. RNA (and large-dataset) accuracy: what the PC side must change

The preprint's method (Supplementary Note 1; also implemented on the Jetson in `docs-riley/bench/accuracy.py`):
- **DNA:** `minimap2 -cx map-ont -t N --secondary=no hg38noAlt.fa reads.fastq`, then median of PAF column 10 / column 11 over all output lines.
- **RNA:** `minimap2 -cx splice -uf -k14 -t N --secondary=no gencode.v40.transcripts.fa reads.fastq`, same identity definition. The reference is the GENCODE release 40 human **transcriptome** (246,624 transcripts, 454 MB; copy it from the Jetson: `scp riley@192.168.8.211:~/data/ref/gencode.v40.transcripts.fa .`).

What the existing `fetch-and-minimap.py` assumes and will therefore get wrong for RNA (do not edit it; propose a patch in your reply):
- `MINIMAP2_PRESET = "map-ont"` and `REFERENCE_FASTA = hg38noAlt.fa`, applied to every FASTQ. RNA FASTQs need the splice preset and the gencode reference, chosen per file.
- Filenames: kept FASTQs are now `output_<fast|hac>_<size>-<dna|rna>-<stage>.fastq` (e.g. `output_hac_20k-rna-spec.fastq`); the script's APPEND suffix becomes `-<dna|rna>-<stage>`, so the data type can be read from the suffix. The 190k chr22 tier uses size token `190k`; the 500k tier uses `500k`.
- `EXPECTED_MEDIAN` is DNA-1k only. Plausibility ranges from the preprint for the *full* UHRR dataset on A100 (not subset-specific): Slorado FAST ≈ 0.931–0.934, HAC ≈ 0.967–0.972 (Supplementary Table S6); DNA 500k Xavier FAST 0.939837 (Table S7).
- `expected_for()`, the map-rate colouring and the identity delta logic should be per data type.
- Sanity check available on the Jetson: RNA accuracy for any FASTQ takes seconds there (`python3 docs-riley/bench/accuracy.py FASTQ --dtype rna`), so the PC result can be cross-checked on a small file.

New columns in `runs.csv` (see section 4): `dtype, cell, dataset, samples, samples_per_s, wall_process_s` (process wall time, comparable to GNU `time` elapsed used by the preprint).

## 5. What is useful for you to do now (read-only; reply in one file)

Please produce `REPLY2-from-PC-agent.md` containing:

1. **Harness-history gaps** — answer the "Open items [PC to confirm]" at the end of `harness-evolution.md`: which slorado commit the June 23 baselines used (if any record exists), how `baseline_results_v2/` was produced, any earlier script versions or charts predating June 23, and the dates and purpose of each PC-side analysis script (`fetch-and-minimap.py`, `fetch-and-plot-power.py`, `fetch-and-minimap-spec.py`, the cascade scripts). Add anything about the early Jetson-Runner GUI that is visible from PC data (fields in the JSON/telemetry, charts produced).
2. **Reuse assessment** — for each existing PC script, say what it assumes about filenames and CSV schemas (e.g. `output_*.fastq` globs, `riley-runner-*` summaries, `all_summaries.csv` columns) and what would need to change to read the schema in §4. Do not edit the scripts.
3. **Chart and table templates** — propose (list, with one line each, no need to build yet) the figures and tables that should come from `runs.csv`: H1 and H2 time by stage (with error bars), speed-up bars, energy by rail (stacked, gross and net), net energy vs time, accuracy (pp) vs speed trade-off, per-stage profile shares, and a methodology figure for the harness evolution. Flag which can be prototyped today from legacy data.
4. **Legacy-data hygiene** — anything in `thesisB-data-and-diagrams` that could be overwritten or confused with the new data (same filenames, same folder names), and a suggested naming/location scheme to keep legacy and final data separate.
5. **RNA and large-dataset accuracy plan** — a concrete patch proposal (diff or description) for the mapping step covering RNA (splice preset, gencode), per-file reference selection, the new filenames, and the 500k tier (500,000 reads: expected mapping time and memory on your PC); say whether an hg38 `.mmi` index is already cached.
6. **Sanity questions** — anything in this file or the linked documents that looks wrong or inconsistent with the PC data.

## 6. Rules
- Read-only. Do not commit, push, delete or modify existing files; write only your reply file.
- Do not re-run minimap2 or other long jobs unless needed to answer a question above.
- Report both values and their source file on any disagreement; label guesses as guesses.
- Do not ask Riley for credentials. If you need a Jetson-side file, list it in the reply.

## 7. Files to read (copy from the Jetson `~/slorado-riley-v2/docs-riley/`)
`AGENT-CONTEXT-final-test-campaign.md` (this), `measurement-methodology.md`, `harness-audit.md`, `harness-evolution.md`, `2026-10-21-optimisation-summary.md`, `report-results-outline.md`, and your own earlier reply `REPLY-from-PC-agent-2026-10-22.md` for reference.
```bash
mkdir -p ~/Desktop/thesisB-data-and-diagrams/jetson-handoff2 && cd ~/Desktop/thesisB-data-and-diagrams/jetson-handoff2
scp riley@192.168.8.211:'~/slorado-riley-v2/docs-riley/{AGENT-CONTEXT-final-test-campaign,measurement-methodology,harness-audit,harness-evolution,2026-10-21-optimisation-summary,report-results-outline}.md' .
```

---

## Paste-ready first message for the PC agent

```text
Read jetson-handoff2/AGENT-CONTEXT-final-test-campaign.md first, then the other files in jetson-handoff2/ that it lists.
Follow section 5 of that file using the data in this folder. This is read-only: do not commit, push, delete or modify anything
except creating one new file, REPLY2-from-PC-agent.md, in this folder. Report disagreements with both values and file paths.
Do not run long jobs and do not ask me for credentials; if you need a Jetson-side file, list it in the reply.
```
