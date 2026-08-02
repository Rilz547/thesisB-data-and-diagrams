#!/usr/bin/env python3
"""
Promoted-only accuracy check (main PC).

Pulls:
  1) cascade thr=10 FASTQ
  2) FAST-only FASTQ (same 1k reads)
  3) cascade TSV (read_id / mean_q / model)

Keeps only reads marked `hac` in the TSV, runs minimap2 on each subset,
prints median identity for that hard tail, then deletes temps.

Run on main PC after the Jetson commands in the docstring below.

Examples
--------
  python3 cascade_promoted_minimap.py
  python3 cascade_promoted_minimap.py --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

JETSON_HOST = "192.168.4.48"
JETSON_USER = "riley"
JETSON_REPO = "~/slorado-riley-v2"

# Defaults match the thr10 sweep + a FAST-only sibling under cascade-sanity/
REMOTE_CASCADE_FQ = (
    "docs-riley/cascade-threshold-sweep/20260731T040555Z/fastq/"
    "output_cascade_1k_thr10_r01.fastq"
)
REMOTE_CASCADE_TSV = (
    "docs-riley/cascade-threshold-sweep/20260731T040555Z/cascade-logs/"
    "cascade_1k_thr10_r01.tsv"
)
REMOTE_FAST_FQ = "docs-riley/cascade-sanity/output_fast_1k_baseline.fastq"

REFERENCE_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"
MINIMAP2 = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"
MINIMAP2_PRESET = "map-ont"
MINIMAP2_THREADS = 16


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def rsync_pull(remote_rel: str, dest: Path) -> None:
    src = f"{JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}/{remote_rel}"
    print(f"Pull: {src}")
    subprocess.run(["rsync", "-avP", src, str(dest)], check=True)
    if not dest.is_file():
        die(f"rsync did not create {dest}")


def load_promoted_ids(tsv: Path) -> list[str]:
    ids: list[str] = []
    with tsv.open() as f:
        header = f.readline()
        if not header.lower().startswith("read_id"):
            die(f"unexpected TSV header in {tsv}: {header!r}")
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            rid, _mq, model = parts[0], parts[1], parts[2]
            if model.strip().lower() == "hac":
                ids.append(rid)
    return ids


def filter_fastq(src: Path, keep_ids: set[str], dest: Path) -> int:
    """Write only records whose header read_id (first token after @) is in keep_ids."""
    n = 0
    with src.open() as fin, dest.open("w") as fout:
        while True:
            h = fin.readline()
            if not h:
                break
            seq = fin.readline()
            plus = fin.readline()
            qual = fin.readline()
            if not qual:
                die(f"truncated FASTQ: {src}")
            if not h.startswith("@"):
                die(f"bad FASTQ header in {src}: {h[:80]!r}")
            rid = h[1:].split()[0]
            if rid in keep_ids:
                fout.write(h)
                fout.write(seq)
                fout.write(plus)
                fout.write(qual)
                n += 1
    return n


def median_identity(paf: Path) -> tuple[float | None, int]:
    ratios: list[float] = []
    with paf.open() as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 11:
                continue
            try:
                matches = float(cols[9])
                block = float(cols[10])
            except ValueError:
                continue
            if block > 0:
                ratios.append(matches / block)
    if not ratios:
        return None, 0
    ratios.sort()
    mid = len(ratios) // 2
    if len(ratios) % 2:
        return ratios[mid], len(ratios)
    return 0.5 * (ratios[mid - 1] + ratios[mid]), len(ratios)


def run_minimap(fastq: Path, paf: Path) -> tuple[float | None, int]:
    print(f"minimap2 {fastq.name} …")
    with paf.open("w") as out:
        subprocess.run(
            [
                MINIMAP2,
                "-cx",
                MINIMAP2_PRESET,
                "-t",
                str(MINIMAP2_THREADS),
                "--secondary=no",
                str(REFERENCE_FASTA),
                str(fastq),
            ],
            check=True,
            stdout=out,
        )
    return median_identity(paf)


def fmt_med(med: float | None) -> str:
    return f"{med:.6f}" if med is not None else "NA"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cascade-fq", default=REMOTE_CASCADE_FQ)
    ap.add_argument("--cascade-tsv", default=REMOTE_CASCADE_TSV)
    ap.add_argument("--fast-fq", default=REMOTE_FAST_FQ)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print("Would pull:")
        print(f"  {args.cascade_fq}")
        print(f"  {args.cascade_tsv}")
        print(f"  {args.fast_fq}")
        return

    if not Path(MINIMAP2).is_file():
        die(f"minimap2 not found: {MINIMAP2}")
    if not REFERENCE_FASTA.is_file():
        die(f"reference not found: {REFERENCE_FASTA}")

    with tempfile.TemporaryDirectory(prefix="cascade-promoted-") as tmp:
        td = Path(tmp)
        cas_fq = td / "cascade.fastq"
        fast_fq = td / "fast.fastq"
        tsv = td / "cascade.tsv"

        rsync_pull(args.cascade_fq, cas_fq)
        rsync_pull(args.cascade_tsv, tsv)
        rsync_pull(args.fast_fq, fast_fq)

        promoted = load_promoted_ids(tsv)
        keep = set(promoted)
        print(f"\nPromoted (hac) read IDs: {len(promoted)}")
        if not promoted:
            die("no hac rows in TSV")

        cas_sub = td / "cascade_promoted.fastq"
        fast_sub = td / "fast_promoted.fastq"
        n_cas = filter_fastq(cas_fq, keep, cas_sub)
        n_fast = filter_fastq(fast_fq, keep, fast_sub)
        print(f"Filtered cascade FASTQ: {n_cas} reads")
        print(f"Filtered FAST FASTQ:    {n_fast} reads")
        if n_cas != len(promoted) or n_fast != len(promoted):
            print(
                "WARNING: filtered counts != promoted ID count "
                f"(cas={n_cas}, fast={n_fast}, ids={len(promoted)})",
                file=sys.stderr,
            )

        med_cas, aln_cas = run_minimap(cas_sub, td / "cascade_promoted.paf")
        med_fast, aln_fast = run_minimap(fast_sub, td / "fast_promoted.paf")

        print()
        print("=" * 60)
        print(f"  subset: promoted reads only (thr=10 TSV model=hac)")
        print(f"  n_ids:  {len(promoted)}")
        print("-" * 60)
        print(f"  FAST-only on those IDs:  median={fmt_med(med_fast)}  alns={aln_fast}/{n_fast}")
        print(f"  Cascade/HAC on those:    median={fmt_med(med_cas)}  alns={aln_cas}/{n_cas}")
        if med_cas is not None and med_fast is not None:
            print(f"  Δ (cascade − FAST):     {med_cas - med_fast:+.6f}")
        print("=" * 60)
        print()
        if med_cas is not None and med_fast is not None:
            d = med_cas - med_fast
            if d > 0.01:
                print("→ HAC clearly helps the mean-Q-selected tail. Router is useful.")
            elif d > 0.002:
                print("→ Small positive gain on the tail. Router helps a bit.")
            elif d > -0.002:
                print("→ No real change. mean-Q may be a weak selector for identity.")
            else:
                print("→ Cascade worse on the tail — unexpected; paste this back.")
        print("(temp files deleted)")


if __name__ == "__main__":
    main()
