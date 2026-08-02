#!/usr/bin/env python3
"""
One-shot: pull ONE cascade FASTQ from Jetson → minimap2 → print median → delete.

Run on your main PC (has minimap2 + ssh to Jetson). No lasting accuracy CSV.

Examples
--------
  # default: the thr=50 always-promote 1k sanity FASTQ
  python3 cascade_sanity_minimap.py

  # custom remote path (repo-relative)
  python3 cascade_sanity_minimap.py \\
    --remote docs-riley/cascade-sanity/output_cascade_1k_thr50_sanity.fastq
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

# Prefer force_frac=1.0 all-HAC sanity FASTQ if present; thr50 name is legacy.
DEFAULT_REMOTE = "docs-riley/cascade-sanity/output_cascade_1k_thr50_sanity.fastq"

REFERENCE_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"
MINIMAP2 = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"
MINIMAP2_PRESET = "map-ont"
MINIMAP2_THREADS = 16

EXPECTED = {
    "fast_1k": 0.940696,
    "hac_1k": 0.976852,
}


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def count_fastq_reads(fastq: Path) -> int:
    n = 0
    with fastq.open() as f:
        for line in f:
            if line.startswith("@"):
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remote", default=DEFAULT_REMOTE, help="repo-relative FASTQ on Jetson")
    ap.add_argument("--keep", action="store_true", help="do not delete temp FASTQ/PAF")
    args = ap.parse_args()

    mm2 = Path(MINIMAP2)
    if not mm2.is_file():
        die(f"minimap2 not found: {MINIMAP2}")
    if not REFERENCE_FASTA.is_file():
        die(f"reference not found: {REFERENCE_FASTA}")

    remote = args.remote.lstrip("/")
    src = f"{JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}/{remote}"

    with tempfile.TemporaryDirectory(prefix="cascade-sanity-") as tmp:
        tmp_dir = Path(tmp)
        local_fq = tmp_dir / Path(remote).name
        local_paf = local_fq.with_suffix(".paf")

        print(f"Pull: {src}")
        subprocess.run(
            ["rsync", "-avP", src, str(local_fq)],
            check=True,
        )
        if not local_fq.is_file():
            die(f"rsync did not create {local_fq}")

        n_reads = count_fastq_reads(local_fq)
        print(f"Reads: {n_reads}")
        print(f"minimap2 → {local_paf.name}")
        with local_paf.open("w") as out:
            subprocess.run(
                [
                    str(mm2),
                    "-cx",
                    MINIMAP2_PRESET,
                    "-t",
                    str(MINIMAP2_THREADS),
                    "--secondary=no",
                    str(REFERENCE_FASTA),
                    str(local_fq),
                ],
                check=True,
                stdout=out,
            )

        med, n_aln = median_identity(local_paf)
        map_rate = (100.0 * n_aln / n_reads) if n_reads else 0.0
        med_s = f"{med:.6f}" if med is not None else "NA"

        print()
        print("=" * 56)
        print(f"  file:    {local_fq.name}")
        print(f"  reads:   {n_reads}")
        print(f"  alns:    {n_aln}")
        print(f"  map%:    {map_rate:.1f}%")
        print(f"  median:  {med_s}")
        print("-" * 56)
        print(f"  FAST 1k expected: {EXPECTED['fast_1k']:.6f}")
        print(f"  HAC  1k expected: {EXPECTED['hac_1k']:.6f}")
        if med is not None:
            print(f"  Δ vs FAST: {med - EXPECTED['fast_1k']:+.6f}")
            print(f"  Δ vs HAC:  {med - EXPECTED['hac_1k']:+.6f}")
        print("=" * 56)
        print()
        if med is not None and abs(med - EXPECTED["hac_1k"]) < 0.005:
            print("→ Looks like HAC. Cascade pass-B plumbing is probably OK.")
        elif med is not None and abs(med - EXPECTED["fast_1k"]) < 0.005:
            print("→ Still looks like FAST. Pass-B may not be replacing sequences.")
        else:
            print("→ In between / unexpected — paste this block back in chat.")

        if args.keep:
            keep_dir = Path.cwd() / "cascade-sanity-keep"
            keep_dir.mkdir(exist_ok=True)
            for p in (local_fq, local_paf):
                dest = keep_dir / p.name
                dest.write_bytes(p.read_bytes())
                print(f"kept {dest}")
        else:
            print("(temp FASTQ + PAF deleted with temp dir)")


if __name__ == "__main__":
    main()
