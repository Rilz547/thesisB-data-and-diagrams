# Datasets used in the final campaign

**Compiled:** 2026-10-22 on the Jetson. Read and sample counts were measured with a small slow5lib program (decodes every record; `count_reads.c` is in the Jetson scratchpad and `~/data/count_reads`). Source of truth for provenance: upstream `BonsonW/slorado` docs and `test/extensive.sh`, and the Slorado/Openfish preprint (Table 1).

| id | type | reads | samples | file | size | on the Jetson | source / provenance |
|---|---|---|---|---|---|---|---|
| `dna_1k` | DNA R10.4.1, 5 kHz, HG002 | 1,000 | 55,715,189 | `test/PGXXXX230339/reads_1k.blow5` | 49.5 MB | repo `test/` | upstream test data (`PGXXXX230339` = HG002 PromethION run); README gives expected FAST/HAC/SUP medians for this file |
| `dna_20k` | same run | 20,000 | 1,121,019,457 | `test/PGXXXX230339/reads_20k.blow5` | 998 MB | repo `test/` | md5 `d4bb9a40eb89647c2bb74b724d63cef4`, **identical** to `PGXXXX230339_reads_20k.blow5` in upstream's `slorado_test_ext_dat`; documented download `https://slow5.bioinf.science/hg2_prom_5khz_subsubsample`. Sample count agrees with the paper's HG002-dna-20k (1.1 B) |
| `dna_500k` | same run | **500,000** | **28,107,529,368** | `~/data/PGXXXX230339_reads_500k.blow5` | 25.02 GB | `~/data/` (downloaded 2026-10-22) | `https://slow5.bioinf.science/hg2_prom_5khz_subsample` → public S3 (`gtgseq.s3.amazonaws.com/misc/sub/ont-r10-5khz-dna/`). **Read and sample counts match the paper's Table 1 HG002-dna-500k (500,000 reads, 28.1 B samples).** md5 `4cfe7a3ab4fbb45f87fd4ddc3c0b6eca`. File dated 2024-05-01 at source |
| `dna_chr22` | same run, chr22 reads | 189,928 | 12,176,764,640 | `slorado_test_ext_dat/PGXXXX230339_reads_chr22.blow5` | 10.77 GB | **Trash** (`~/.local/share/Trash/files/slorado_test_ext_dat/`) | upstream test data for the 5mC methylation test (`bisulphite_chr22.tsv`); documented download `https://slow5.bioinf.science/hg2_prom_5khz_chr22`. Not a dataset in the paper. Optional middle-size tier |
| `rna_20k` | RNA004, UHRR, 4 kHz | 20,000 | 772,479,808 | `~/data/PNXRXX240011_reads_20k.blow5` | 719 MB | `~/data/` (copied from Trash, md5 verified) | upstream's RNA test subset (`PNXRXX240011` = ENA run ERR12997170, UHRR RNA004). md5 `3b2d0908088bf4c7a7be6dc2f2cf7d81` |
| `rna_1k` | same | 1,000 | 36,686,101 | `~/data/rna_1k.blow5` | 34 MB | `~/data/` (cut 2026-10-22) | first 1,000 reads of `rna_20k`, cut with `slow5tools split -r 1000 -c zstd -s svb-zd` (slow5tools built from source at `~/tools/slow5tools`, zstd on, HDF5 off). md5 `dc402c5745773aed8f544d209d54e628` |

**References**
- DNA: `hg38noAlt.fa` (3.14 GB; copy at `~/slorado/hg38noAlt.fa`; also in Trash). Upstream download: `seq.bioinf.science/hg38noAlt`.
- RNA: `gencode.v40.transcripts.fa` (GENCODE release 40 human transcript sequences, 246,624 transcripts, 454 MB) copied to `~/data/ref/` (md5 `a7c65af1c6e56197e22b70836590bbba`).

**Accuracy commands (paper's method, Supplementary Note 1)**
- DNA: `minimap2 -cx map-ont -t N --secondary=no hg38noAlt.fa reads.fastq | awk '{print $10/$11}' | datamash median 1`
- RNA: `minimap2 -cx splice -uf -k14 -t N --secondary=no gencode.v40.transcripts.fa reads.fastq | awk '{print $10/$11}' | datamash median 1`

**Paper comparison anchors (Table S7, Jetson AGX Xavier, FAST, HG002-dna-500k)**
144.4 min, median identity 0.939837, `-t 8 -B 500M -K 2000 -c 10000 -C 200`, Slorado commit `406ab8c` (an ancestor of our base `754a74d`). 28.1 B samples / 144.4 min ≈ **3.24 M samples/s**.

**FASTQ output size** (measured, decimal MB): DNA FAST 1k ≈ 8.0 MB, DNA HAC 1k ≈ 8.1 MB, RNA FAST 1k ≈ 2.1 MB; so a 20k DNA FASTQ is ≈ 160 MB (the August power runs recorded ≈ 157–159 MB).

**Throughput units.** `samples/s = samples / Real time` is hardware-neutral and lets runs on different datasets be compared. Example: speculative FAST 20k at 158.8 s ≈ 7.06 M samples/s; upstream FAST 20k (June, `-C 256`) at 247.9 s ≈ 4.52 M samples/s.

**Harness cell ids** (`docs-riley/bench/stages.json`): `<dtype>_<model>_<size>` — `dna_fast_1k`, `dna_hac_20k`, `rna_fast_1k`, `rna_hac_20k`, `dna_fast_500k`, `dna_fast_chr22` (a two-part id such as `fast_1k` means DNA).
Default repeats and output modes per dataset: 1k → 5 repeats, modes A and B; 20k → 3, A and B; 500k and chr22 → 1, mode B only (FASTQ written; also what the paper's Xavier run did); FAST only for the two large datasets. One uncounted warm-up per cell except the two large datasets.

**Kept FASTQ names** (one per cell, mode B repeat 1): `docs-riley/bench-runs/kept/output_<fast|hac>_<size>-<dna|rna>-<stage>.fastq`, e.g. `output_fast_20k-rna-spec.fastq` (size token for chr22 is `190k`). The existing PC script's glob (`--glob "docs-riley/bench-runs/kept/output_*.fastq"`) accepts these names; its APPEND suffix becomes `-<dtype>-<stage>`.
