# Speculative decode on RNA: threshold exploration (2026-10-22)

**Question.** The smoke test showed speculative decode (`q10_m2_brave`, thresholds tuned on DNA) loses 4.16 pp of median identity on RNA FAST while accepting 90 % of drafts. Can different gate thresholds recover accuracy and still give a speed-up?
**Method.** `bench.py explore`: spec stage (`49ac641`) on `rna_fast_1k` (1,000 reads, UHRR RNA004) over a grid of `--spec-repair-threshold` (mean draft Phred Q below which a chunk is repaired) × `--spec-margin-threshold` (mean decision margin below which it is repaired), each against a matched non-speculative reference (`lb_best`, same flush threshold). Identity = median PAF col10/col11 against GENCODE v40 (`minimap2 -cx splice -uf -k14 --secondary=no`), computed on the Jetson. Flags: `-C 128 -c 12288 -K 4096 -p 150 --overlap-decode=yes --fixed-c-batch=no --flush-threshold=64 --spec-overlap-repair=yes`.
**Caveats.** Single runs, 1k reads (a few seconds each), mode B; timing differences of a few percent are within noise. The grid is exploratory, not part of the measured campaign. Records: `bench-runs/explore.jsonl`, summaries `bench-runs/explore-rna*.json`.

## RNA FAST 1k (reference `lb_best`: 8.13 s, median identity 0.934396)
| Q threshold | margin threshold | accept rate α | `Real time` s | vs reference | median identity | Δ pp | bases vs reference |
|---|---|---|---|---|---|---|---|
| 10 | 2 | 0.905 | 7.74 | −4.8 % | 0.892756 | **−4.164** | 0.916 |
| 10 | 2.5 | 0.905 | 7.71 | −5.2 % | 0.892756 | −4.164 | 0.916 |
| 10 | 3 | 0.867 | 7.78 | −4.4 % | 0.895579 | −3.882 | 0.923 |
| 10 | 4 | 0.027 | 9.15 | +12.5 % | 0.933912 | −0.048 | 0.998 |
| 15 | 2 | 0.534 | 8.42 | +3.5 % | 0.912571 | −2.182 | 0.962 |
| 15 | 2.5 | 0.534 | 8.37 | +3.0 % | 0.912571 | −2.182 | 0.962 |
| 15 | 3 | 0.532 | 8.36 | +2.7 % | 0.912791 | −2.161 | 0.962 |
| 15 | 4 | 0.009 | 9.25 | +13.7 % | 0.934084 | −0.031 | 1.000 |
| 20 | 2–4 | 0.000 | 9.12–9.16 | +12.1 to +12.7 % | 0.934396 | 0.000 | 1.000 |

## RNA HAC 1k (reference `lb_best`: 38.12 s, median identity 0.971673), Q = 10
| margin threshold | α | `Real time` s | vs reference | median identity | Δ pp |
|---|---|---|---|---|---|
| 2 (smoke run) | 0.930 | 37.80 | −0.8 % (vs 37.95 s) | 0.968209 | −0.346 |
| 2.5 | 0.930 | 37.52 | −1.6 % | 0.968209 | −0.346 |
| 3 | 0.930 | 37.45 | −1.7 % | 0.968209 | −0.346 |

## Findings
1. **No setting is both faster and accurate on RNA FAST.** The only settings that are faster than the non-speculative reference (Q = 10, margin ≤ 3) lose 3.9–4.2 pp of identity for a 4–5 % speed-up. Settings that keep accuracy (α ≈ 0) are 12–14 % *slower* than the reference, because every chunk pays for the draft *and* the repair. Q = 15 is in between and worse than the reference on both axes (−2.2 pp *and* +3 % time).
2. **On RNA the quality gate dominates, not the margin.** Raising the margin from 2 to 3 barely changes α at Q = 10 or Q = 15; α only collapses at margin 4 (α 0.027). On DNA the margin was the dominant knob. The draft's own mean Phred score overestimates quality on RNA FAST (the model's q-score bias is +2.0 vs −2.0 for DNA FAST), so a quality gate that is selective on DNA is nearly always satisfied on RNA.
3. **RNA HAC behaves like DNA HAC:** α ≈ 0.93, a ≈ 1–2 % speed-up and −0.35 pp identity (DNA HAC: ≈ 1 % and −0.14 pp). Margin 2–3 changes nothing at Q = 10.
4. **Interpretation.** With the width-1 greedy draft, RNA FAST drafts that look confident are often wrong, so no gate on draft Q or margin separates good from bad drafts well. This supports the report's earlier future-work item (a mini-beam draft of width 4–8 to raise draft quality) and suggests the RNA gate may need a different statistic, not just different thresholds. These are hypotheses; neither has been tested.

## Consequence for the campaign
Keep the thresholds fixed at Q = 10, margin = 2 for every dataset and report the RNA FAST result as it is (speed-up of a few percent at a 4 pp accuracy cost; method not transferable without re-tuning or a better draft). This table is supplementary evidence that re-tuning the two thresholds does not rescue it.
