# After the campaign: every step, with commands

Machines: **Jetson** = `riley@192.168.8.211`, repo `~/slorado-riley-v2`; **PC** = `~/Desktop/thesisB-data-and-diagrams`. Jetson commands run from `~/slorado-riley-v2/docs-riley/bench` unless stated. Do nothing on the Jetson that uses the GPU while the campaign is still running.

## 0. Watch it while it runs

```bash
python3 progress.py                 # runs done / expected, per phase, elapsed time, rough ETA (add --no-extras to ignore phase 4)
python3 bench.py status             # per stage/cell/mode counts
tail -f ../bench-runs/campaign.log  # live log (Ctrl-C only stops the tail, not the campaign)
grep -c '!!!' ../bench-runs/campaign.log   # number of failed bench.py calls (0 is what you want)
```

The campaign is finished when the log ends with `=== ... campaign complete` and `pgrep -af "campaign.sh|bench.py run"` prints nothing. If any `!!!` lines appeared, re-run the same `campaign.sh` (it uses `--resume` and only repeats what is missing).

## 1. Check the campaign data (Jetson)

```bash
python3 analyze.py validate         # drift/order/canary/idle checks on the campaign data (V1 reports "no qualifying runs": no tegrastats in the campaign, that is expected)
python3 analyze.py equiv            # V7: kept FASTQs reproduce `upstream` (spec, cb_on, paper_xavier differ by design)
python3 analyze.py csv              # writes ../bench-runs/runs.csv (56 columns)
python3 analyze.py tables --md ../bench-runs/tables.md     # timing + energy tables, no accuracy yet
```

If `validate` flags a canary or idle drift for a stage, look at that block before trusting its numbers, and tell Claude.

## 2. RNA accuracy (Jetson)

Done here because the PC's `fetch-and-minimap.py` is DNA-only (`map-ont` against hg38; RNA needs `splice -uf -k14` against the GENCODE v40 transcripts) and the 454 MB transcriptome maps easily on the Jetson, whereas the 7 GB hg38 index does not fit in 8 GB. It could move to the PC if that script gained an RNA mode, but then it would need re-checking against the values below.

```bash
python3 rna_accuracy_csv.py         # -> ../bench-runs/accuracy-rna/minimap_accuracy-rna-<stage>.csv
```

Expected sanity values on 1k: `upstream` RNA FAST 0.934396, RNA HAC 0.971673.

## 3. Back up the Jetson run data to the PC (run on the PC)

```bash
mkdir -p ~/Desktop/thesisB-data-and-diagrams/final-campaign/jetson-campaign-runs
rsync -avP --exclude='kept*/' riley@192.168.8.211:/home/riley/slorado-riley-v2/docs-riley/bench-runs/ \
  ~/Desktop/thesisB-data-and-diagrams/final-campaign/jetson-campaign-runs/
```

(`--exclude='kept*/'` leaves the FASTQs out; the accuracy step below pulls those. Make sure `.gitignore` still ignores `final-campaign/jetson-*-runs/kept*/`, `*.fastq`, `.bench.lock`.)

## 4. DNA accuracy (PC)

In `~/Desktop/thesisB-data-and-diagrams`; `final-campaign/accuracy/` should be empty (the pre-freeze CSVs are in `accuracy-prefreeze/`).

```bash
# 1k and 20k, all stages, one CSV per stage
python3 fetch-and-minimap.py --no-nsys --no-runner-summaries \
  --glob 'docs-riley/bench-runs/kept/output_*-dna-*.fastq'
```

The 500k FASTQs match that glob too. If you would rather run them separately (they are large), use a tighter glob for the first pass, e.g. `'docs-riley/bench-runs/kept/output_*_[12]*k-dna-*.fastq'`, then:

```bash
for s in upstream spec paper_xavier; do
  python3 fetch-and-minimap.py --no-nsys --no-runner-summaries -f docs-riley/bench-runs/kept/output_fast_500k-dna-$s.fastq
done
```

Never pass `-rna-` FASTQs to this script (it maps with `map-ont` against hg38). Expected sanity values (1k, vs the published): `upstream` FAST ≈ 0.9408, HAC 0.976852; `spec` FAST ≈ −0.55 pp, HAC ≈ −0.14 pp.

## 5. Bring the accuracy CSVs to the Jetson (run on the PC)

```bash
ssh riley@192.168.8.211 'mkdir -p ~/slorado-riley-v2/docs-riley/bench-runs/accuracy'
scp final-campaign/accuracy/minimap_accuracy-dna-*.csv riley@192.168.8.211:/home/riley/slorado-riley-v2/docs-riley/bench-runs/accuracy/
```

## 6. Final tables with accuracy (Jetson)

`--accuracy` is an option of the `tables` subcommand, so it comes **after** `tables`:

```bash
python3 analyze.py tables \
  --accuracy ../bench-runs/accuracy/*.csv ../bench-runs/accuracy-rna/*.csv \
  --md ../bench-runs/tables-with-accuracy.md
python3 analyze.py csv
```

Optional: `--baseline ov_off` (or another stage) changes the comparison baseline in the accuracy table; every table names its baseline.

## 7. Commit and push (you do the commits)

- Jetson: `git add docs-riley/...` the new docs (`tables*.md` if you copy them out of the gitignored `bench-runs/`), then commit and push from the PC as before. No Claude co-author line.
- PC: `git add final-campaign/accuracy final-campaign/jetson-campaign-runs`, commit "Add final campaign accuracy CSVs and Jetson run data", push.
- Copy `runs.csv`, `tables-with-accuracy.md` and the accuracy CSVs somewhere tracked (e.g. `final-campaign/results/` on the PC) so the report numbers are backed up.

## 8. Tell Claude, then write the report

Open a new Claude session in `~/slorado-riley-v2` and say: "The campaign is done. Read `docs-riley/SESSION-STATE.md`, `HARNESS-FROZEN.md` and `POST-CAMPAIGN-STEPS.md`, then check the results." Claude will go through `validate`, `equiv` and the tables with you, write the post-campaign handoff for the PC agent (charts from `runs.csv`), and fill in the report sections from `report-results-outline.md` (`campaign-flow.md` describes what each result feeds).

## 9. Still open after this

- **Nsight profile per stage** (outline section C.3): `profile_stage.py` is not written yet; it runs in a separate GPU pass after the timing campaign.
- `dna_chr22` tier (deferred; file still in the Trash), the single isolated `-c 10000` test, the unbuilt fused LSTM cell kernel (future work).
