#!/usr/bin/env python3
"""
Re-map every kept FASTQ behind the current accuracy results with minimap2 2.26 (the version in the slorado
preprint, Supplementary Note 1), side by side with the minimap2 2.24 used so far. Run on the PC.

Safety
------
* Read-only on all existing results: final-campaign/accuracy/, jetson-campaign-runs/accuracy-rna/ and the
  backups are only read. Everything this script writes goes to OUT_DIR (a new folder) and WORK_DIR (outside the repo).
* Refuses to start if OUT_DIR already has files, unless --resume is given (then rows already done are skipped).
* Each FASTQ is mapped with BOTH versions. If the 2.24 re-run reproduces the stored median, alignment count and
  read count, the row is SAME and its 2.26 value is a fair comparison. Otherwise it is MISMATCH: for DNA (stored
  values came from this same PC build) that means the file changed since the CSV was written; for RNA it can
  also mean the PC's 2.24 (mm2-gb build) differs from the Jetson's stock 2.24 that made the stored values.
  MISMATCH rows are kept in comparison.csv but left out of the drop-in accuracy CSVs.

Method (identical to Supplementary Note 1 apart from the version)
------------------------------------------------------------------
  DNA: minimap2 -cx map-ont --secondary=no hg38noAlt.fa reads.fastq
  RNA: minimap2 -cx splice -uf -k14 --secondary=no gencode.v40.transcripts.fa reads.fastq
  identity = PAF column 10 / column 11 for every output line; median over lines.

Outputs (OUT_DIR)
-----------------
  comparison.csv                         one row per FASTQ: stored vs 2.24 re-run vs 2.26
  minimap_accuracy-<dtype>-<stage>.csv   2.26 values in the original accuracy-CSV layout (rows with
                                         file_check = SAME only), ready to swap in for the tables

Usage
-----
  python3 remap-minimap2-2.26.py --dry-run          # list what would run; no copies, no mapping, no writes
  python3 remap-minimap2-2.26.py --only fast_1k     # small test: only FASTQs whose name contains "fast_1k"
  python3 remap-minimap2-2.26.py                    # everything (about 12 GB pulled one file at a time)
  python3 remap-minimap2-2.26.py --resume           # continue after an interruption
"""

from __future__ import annotations

import argparse
import atexit
import csv
import hashlib
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent
JETSON_HOST = "192.168.8.211"
JETSON_USER = "riley"
REMOTE_KEPT = "/home/riley/slorado-riley-v2/docs-riley/bench-runs/kept"
REMOTE_GENCODE = "/home/riley/data/ref/gencode.v40.transcripts.fa"
GENCODE_MD5 = "a7c65af1c6e56197e22b70836590bbba"  # datasets.md

STORED_CSVS = sorted((REPO / "final-campaign" / "accuracy").glob("minimap_accuracy-dna-*.csv")) + sorted(
    (REPO / "final-campaign" / "jetson-campaign-runs" / "accuracy-rna").glob("minimap_accuracy-rna-*.csv"))

OUT_DIR = REPO / "final-campaign" / "accuracy-mm2-2.26"
WORK_DIR = Path.home() / "mm226-work"          # pulled FASTQs, PAFs, the 2.26 hg38 index; outside the repo

MM_OLD = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"   # 2.24 (mm2-gb build) used so far
MM_NEW = "/usr/bin/minimap2"                                       # 2.26 from Ubuntu 24.04
THREADS = 16

HG38_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"
HG38_MMI_OLD = REPO / "slorado-accuracy-tmp" / "hg38noAlt.mmi"     # existing 2.24 index (read only)
HG38_MMI_NEW = WORK_DIR / "hg38noAlt.mm226.mmi"                    # built here once with 2.26
GENCODE_FASTA = WORK_DIR / "gencode.v40.transcripts.fa"           # copied from the Jetson once

PRESETS = {"dna": ["-cx", "map-ont"], "rna": ["-cx", "splice", "-uf", "-k14"]}
NAME_RE = re.compile(r"output_(fast|hac)_(\d+k)-(dna|rna)-(.+)\.fastq")

ACC_FIELDS = ["timestamp", "fastq", "n_reads", "n_alignments", "map_rate", "accuracy_median",
              "expected_median", "delta_vs_expected", "nsys_pulled", "reference"]
CMP_FIELDS = ["fastq", "dtype", "stage", "n_reads", "stored_median", "stored_n_alignments",
              "mm224_median", "mm224_n_alignments", "mm226_median", "mm226_n_alignments",
              "delta_226_vs_stored_pp", "file_check", "remote_size", "timestamp"]

SSH_OPTS = ["-o", "ControlMaster=auto", "-o", f"ControlPath=/tmp/ssh-mm226-{JETSON_USER}@{JETSON_HOST}-%p",
            "-o", "ControlPersist=600"]


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)


def ssh(cmd: str) -> str:
    return subprocess.run(["ssh", *SSH_OPTS, f"{JETSON_USER}@{JETSON_HOST}", cmd],
                          check=True, text=True, capture_output=True).stdout


def close_ssh() -> None:
    subprocess.run(["ssh", "-O", "exit", *SSH_OPTS, f"{JETSON_USER}@{JETSON_HOST}"],
                   check=False, capture_output=True, text=True)


def pull(remote: str, dest_dir: Path) -> Path:
    run(["rsync", "-a", "--info=progress2", "-e", "ssh " + " ".join(SSH_OPTS),
         f"{JETSON_USER}@{JETSON_HOST}:{remote}", str(dest_dir) + "/"])
    return dest_dir / Path(remote).name


def mm_version(exe: str) -> str:
    return subprocess.run([exe, "--version"], check=True, text=True, capture_output=True).stdout.strip()


def count_reads(fastq: Path) -> int:
    with fastq.open("rb") as f:
        return sum(1 for _ in f) // 4


def median_identity(paf: Path) -> tuple[float | None, int]:
    """Same definition as fetch-and-minimap-final.py: col10/col11 for every line with col11 > 0, median over lines."""
    ratios: list[float] = []
    with paf.open() as f:
        for line in f:
            cols = line.split("\t")
            if len(cols) < 11:
                continue
            try:
                matches, block = float(cols[9]), float(cols[10])
            except ValueError:
                continue
            if block > 0:
                ratios.append(matches / block)
    if not ratios:
        return None, 0
    ratios.sort()
    mid = len(ratios) // 2
    return (ratios[mid] if len(ratios) % 2 else 0.5 * (ratios[mid - 1] + ratios[mid])), len(ratios)


def map_one(exe: str, dtype: str, target: Path, fastq: Path, paf: Path) -> tuple[float | None, int]:
    cmd = [exe, *PRESETS[dtype], "-t", str(THREADS), "--secondary=no", str(target), str(fastq)]
    print("+ " + " ".join(cmd) + f" > {paf.name}", flush=True)
    with paf.open("w") as out:
        subprocess.run(cmd, check=True, stdout=out, stderr=subprocess.DEVNULL)
    result = median_identity(paf)
    paf.unlink()
    return result


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def stored_rows() -> list[dict]:
    rows = []
    for csv_path in STORED_CSVS:
        for r in csv.DictReader(csv_path.open()):
            m = NAME_RE.fullmatch(r["fastq"])
            if not m:
                die(f"unexpected FASTQ name {r['fastq']!r} in {csv_path}")
            rows.append({**r, "dtype": m.group(3), "stage": m.group(4), "size_token": m.group(2),
                         "stored_csv": csv_path})
    order = {"1k": 0, "20k": 1, "500k": 2}
    return sorted(rows, key=lambda r: (order.get(r["size_token"], 9), r["dtype"], r["fastq"]))


def write_acc_csvs(done: list[dict]) -> None:
    """2.26 values in the original accuracy-CSV layout, one file per dtype/stage, SAME rows only."""
    groups: dict[str, list[dict]] = {}
    for r in done:
        if r["file_check"] == "SAME":
            groups.setdefault(f"minimap_accuracy-{r['dtype']}-{r['stage']}.csv", []).append(r)
    for name, rows in groups.items():
        with (OUT_DIR / name).open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=ACC_FIELDS)
            w.writeheader()
            for r in rows:
                n_reads, n_aln = int(r["n_reads"]), int(r["mm226_n_alignments"])
                ref = HG38_FASTA if r["dtype"] == "dna" else GENCODE_FASTA
                w.writerow({"timestamp": r["timestamp"], "fastq": r["fastq"], "n_reads": n_reads,
                            "n_alignments": n_aln, "map_rate": f"{100.0 * n_aln / n_reads:.1f}%",
                            "accuracy_median": r["mm226_median"], "expected_median": "-",
                            "delta_vs_expected": "-", "nsys_pulled": "", "reference": f"{ref} (minimap2 2.26)"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list what would run; no copies, mapping or writes")
    ap.add_argument("--only", default="", help="only FASTQs whose name contains this text (e.g. fast_1k)")
    ap.add_argument("--resume", action="store_true", help="continue into an existing OUT_DIR, skipping done rows")
    args = ap.parse_args()

    # tools and versions
    v_old, v_new = mm_version(MM_OLD), mm_version(MM_NEW)
    print(f"minimap2 old: {MM_OLD} -> {v_old}\nminimap2 new: {MM_NEW} -> {v_new}")
    if not v_old.startswith("2.24") or not v_new.startswith("2.26"):
        die("expected minimap2 2.24 (old) and 2.26 (new)")
    if not HG38_FASTA.is_file() or not HG38_MMI_OLD.is_file():
        die(f"missing {HG38_FASTA} or {HG38_MMI_OLD}")

    rows = [r for r in stored_rows() if args.only in r["fastq"]]
    if not rows:
        die("no stored accuracy rows match --only")

    atexit.register(close_ssh)
    listing = ssh(f"cd {REMOTE_KEPT} && stat -c '%n\t%s' output_*-[dr]na-*.fastq")
    remote = {line.split("\t")[0]: int(line.split("\t")[1]) for line in listing.splitlines() if line.strip()}
    missing = [r["fastq"] for r in rows if r["fastq"] not in remote]
    total = sum(remote.get(r["fastq"], 0) for r in rows)
    print(f"\n{len(rows)} FASTQs selected, {len(rows) - len(missing)} found in {REMOTE_KEPT}, "
          f"{len(missing)} missing; {total / 1e9:.2f} GB to pull, one file at a time")
    for name in missing:
        print(f"  MISSING  {name}")

    done_names: set[str] = set()
    cmp_path = OUT_DIR / "comparison.csv"
    if OUT_DIR.exists() and any(OUT_DIR.iterdir()):
        if not args.resume:
            die(f"{OUT_DIR} already has files; use --resume to continue, or choose a new OUT_DIR")
        if cmp_path.is_file():
            done_names = {r["fastq"] for r in csv.DictReader(cmp_path.open())}

    if args.dry_run:
        print("\nDry run. Would map, in this order:")
        for r in rows:
            state = "done" if r["fastq"] in done_names else ("MISSING" if r["fastq"] in missing else "")
            print(f"  {r['dtype']}  {r['fastq']:<46} {remote.get(r['fastq'], 0) / 1e6:9.1f} MB  "
                  f"stored {r['accuracy_median']}  {state}")
        print(f"\nOutputs would go to {OUT_DIR}; scratch in {WORK_DIR}. Nothing was written.")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    # references for 2.26: build the hg38 index once; copy and verify GENCODE once
    if any(r["dtype"] == "dna" for r in rows) and not HG38_MMI_NEW.is_file():
        run([MM_NEW, "-x", "map-ont", "-t", str(THREADS), "-d", str(HG38_MMI_NEW), str(HG38_FASTA)],
            stderr=subprocess.DEVNULL)
    if any(r["dtype"] == "rna" for r in rows) and not GENCODE_FASTA.is_file():
        pull(REMOTE_GENCODE, WORK_DIR)
        if md5(GENCODE_FASTA) != GENCODE_MD5:
            GENCODE_FASTA.unlink()
            die("GENCODE md5 does not match datasets.md; removed the copy")

    new_file = not cmp_path.is_file()
    with cmp_path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CMP_FIELDS)
        if new_file:
            w.writeheader()
        for i, r in enumerate(rows, 1):
            name = r["fastq"]
            if name in done_names or name in missing:
                continue
            print(f"\n[{i}/{len(rows)}] {name}", flush=True)
            fq = pull(f"{REMOTE_KEPT}/{name}", WORK_DIR)
            if fq.stat().st_size != remote[name]:
                die(f"size mismatch after copy for {name}")
            n_reads = count_reads(fq)
            if r["dtype"] == "dna":
                old = map_one(MM_OLD, "dna", HG38_MMI_OLD, fq, WORK_DIR / "old.paf")
                new = map_one(MM_NEW, "dna", HG38_MMI_NEW, fq, WORK_DIR / "new.paf")
            else:
                old = map_one(MM_OLD, "rna", GENCODE_FASTA, fq, WORK_DIR / "old.paf")
                new = map_one(MM_NEW, "rna", GENCODE_FASTA, fq, WORK_DIR / "new.paf")
            fq.unlink()
            same = (f"{old[0]:.6f}" == r["accuracy_median"] and old[1] == int(r["n_alignments"])
                    and n_reads == int(r["n_reads"]))
            row = {"fastq": name, "dtype": r["dtype"], "stage": r["stage"], "n_reads": n_reads,
                   "stored_median": r["accuracy_median"], "stored_n_alignments": r["n_alignments"],
                   "mm224_median": f"{old[0]:.6f}", "mm224_n_alignments": old[1],
                   "mm226_median": f"{new[0]:.6f}", "mm226_n_alignments": new[1],
                   "delta_226_vs_stored_pp": f"{100 * (new[0] - float(r['accuracy_median'])):+.4f}",
                   "file_check": "SAME" if same else "MISMATCH", "remote_size": remote[name],
                   "timestamp": datetime.now(timezone.utc).isoformat()}
            w.writerow(row)
            f.flush()
            print(f"  stored {r['accuracy_median']} ({r['n_alignments']})  2.24 {row['mm224_median']} ({old[1]})  "
                  f"2.26 {row['mm226_median']} ({new[1]})  {row['file_check']}", flush=True)

    done = list(csv.DictReader(cmp_path.open()))
    write_acc_csvs(done)
    changed = [d for d in done if d["file_check"] != "SAME"]
    differ = [d for d in done if d["file_check"] == "SAME" and d["mm226_median"] != d["stored_median"]]
    print(f"\nDone: {len(done)} rows in {cmp_path}")
    print(f"  file check SAME: {len(done) - len(changed)}, MISMATCH: {len(changed)}")
    print(f"  2.26 median differs from stored: {len(differ)}")
    for d in differ:
        print(f"    {d['fastq']}: {d['stored_median']} -> {d['mm226_median']} ({d['delta_226_vs_stored_pp']} pp)")
    print(f"Scratch folder {WORK_DIR} keeps the 2.26 index and GENCODE copy; delete it when finished.")


if __name__ == "__main__":
    main()
