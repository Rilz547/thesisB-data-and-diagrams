#!/usr/bin/env python3
"""
Cascade force-frac sweep (Jetson).

Goal: find the promote-fraction knee — more accurate than FAST, faster than HAC.

Runs FAST→HAC cascade across force-frac values:
  - 1k  × 3 reps per frac
  - 20k × 1 rep  per frac
  - plus FAST-only and HAC-only 1k baselines (1 each) for the curve anchors

Invoke yourself from repo root (venv active); does not auto-run:

  python3 docs-riley/cascade_force_frac_sweep.py --dry-run
  python3 docs-riley/cascade_force_frac_sweep.py
  python3 docs-riley/cascade_force_frac_sweep.py --skip-20k

Layout (OUT_ROOT stamped):
  fastq/          output_cascade_{1k|20k}_frac{FFF}_r{RR}.fastq
                  output_fast_1k_baseline.fastq / output_hac_1k_baseline.fastq
  cascade-logs/   *.tsv
  consoles/       full stdout
  results/        cascade_frac_sweep_results.csv + .txt

Main PC accuracy:
  python3 fetch-and-minimap-cascade-frac.py --stamp <UTCSTAMP>
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
SLORADO = REPO / "slorado"

MODEL_FAST = REPO / "models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0"
MODEL_HAC = REPO / "models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0"
READS_1K = REPO / "test/PGXXXX230339/reads_1k.blow5"
READS_20K = REPO / "test/PGXXXX230339/reads_20k.blow5"

GPU_BATCH = 128
CHUNK_SIZE = 12288
READ_BATCH = 4096
OVERLAP = 150
OVERLAP_DECODE = "yes"
OVERLAP_DEPTH = 1
FIXED_C_BATCH = "no"
FLUSH_1K = 64
FLUSH_20K = 128

# Dense around the calibrated knee (~17%); extend to see flatten toward HAC.
FORCE_FRACS = [0.05, 0.10, 0.15, 0.17, 0.20, 0.25, 0.30, 0.40, 0.50]

REPS_1K = 3
REPS_20K = 1
RUN_BASELINES_1K = True  # FAST-only + HAC-only anchors
WARMUP_FAST_1K = True

STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT_ROOT = Path(__file__).resolve().parent / "cascade-force-frac-sweep" / STAMP

REAL_TIME_RE = re.compile(
    r"\[main\] Real time: ([\d.]+) sec; CPU time: ([\d.]+) sec; Peak RAM: ([\d.]+) GB"
)
PROC_RE = re.compile(r"\[basecaller_main\] data processing: ([\d.]+) sec")
CASCADE_RE = re.compile(
    r"cascade: kept_fast=(\d+) promoted_hac=(\d+) \(([\d.]+)% HAC\) force_frac=([\d.]+)"
)

CSV_FIELDS = [
    "stamp",
    "mode",  # cascade | fast | hac
    "size",
    "force_frac",
    "rep",
    "tag",
    "real_s",
    "cpu_s",
    "proc_s",
    "peak_ram_gb",
    "wall_s",
    "kept_fast",
    "promoted_hac",
    "pct_hac",
    "fastq",
    "cascade_tsv",
    "console",
    "exit_code",
]


def frac_tag(frac: float) -> str:
    """0.17 → frac017 ; 0.05 → frac005 ; 0.5 → frac050"""
    return f"frac{int(round(frac * 1000)):03d}"


def build_cascade_cmd(
    *,
    reads: Path,
    frac: float,
    flush: int,
    out_fastq: Path,
    cascade_log: Path,
) -> list[str]:
    return [
        str(SLORADO),
        "basecaller",
        "-C",
        str(GPU_BATCH),
        "-c",
        str(CHUNK_SIZE),
        "-K",
        str(READ_BATCH),
        "-p",
        str(OVERLAP),
        f"--overlap-decode={OVERLAP_DECODE}",
        f"--overlap-depth={OVERLAP_DEPTH}",
        f"--fixed-c-batch={FIXED_C_BATCH}",
        "--flush-threshold",
        str(flush),
        "--cascade=yes",
        f"--cascade-hac={MODEL_HAC}",
        f"--cascade-force-frac={frac:g}",
        f"--cascade-log={cascade_log}",
        "-o",
        str(out_fastq),
        str(MODEL_FAST),
        str(reads),
    ]


def build_single_cmd(model: Path, reads: Path, flush: int, out_fastq: Path) -> list[str]:
    return [
        str(SLORADO),
        "basecaller",
        "-C",
        str(GPU_BATCH),
        "-c",
        str(CHUNK_SIZE),
        "-K",
        str(READ_BATCH),
        "-p",
        str(OVERLAP),
        f"--overlap-decode={OVERLAP_DECODE}",
        f"--overlap-depth={OVERLAP_DEPTH}",
        f"--fixed-c-batch={FIXED_C_BATCH}",
        "--flush-threshold",
        str(flush),
        "-o",
        str(out_fastq),
        str(model),
        str(reads),
    ]


def parse_metrics(text: str) -> dict:
    out: dict = {}
    m = REAL_TIME_RE.search(text)
    if m:
        out["real_s"] = float(m.group(1))
        out["cpu_s"] = float(m.group(2))
        out["peak_ram_gb"] = float(m.group(3))
    m = PROC_RE.search(text)
    if m:
        out["proc_s"] = float(m.group(1))
    m = CASCADE_RE.search(text)
    if m:
        out["kept_fast"] = int(m.group(1))
        out["promoted_hac"] = int(m.group(2))
        out["pct_hac"] = float(m.group(3))
        out["force_frac_reported"] = float(m.group(4))
    return out


def ensure_bins() -> None:
    if not SLORADO.is_file():
        print(f"ERROR: slorado binary missing: {SLORADO}", file=sys.stderr)
        sys.exit(1)
    for p in (MODEL_FAST, MODEL_HAC, READS_1K, READS_20K):
        if not p.exists():
            print(f"ERROR: missing path: {p}", file=sys.stderr)
            sys.exit(1)


def run_one(cmd: list[str], console_path: Path) -> tuple[dict, int]:
    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    wall = time.time() - t0
    text = proc.stdout or ""
    console_path.parent.mkdir(parents=True, exist_ok=True)
    console_path.write_text(text)
    metrics = parse_metrics(text)
    metrics["wall_s"] = wall
    return metrics, proc.returncode


def append_csv(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow(row)


def write_txt_header(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write(f"cascade force-frac sweep  {STAMP}\n")
        f.write(f"force_fracs: {FORCE_FRACS}\n")
        f.write(f"1k reps={REPS_1K} flush={FLUSH_1K}; 20k reps={REPS_20K} flush={FLUSH_20K}\n")
        f.write(
            f"flags: -C {GPU_BATCH} -c {CHUNK_SIZE} -K {READ_BATCH} -p {OVERLAP} "
            f"overlap-decode={OVERLAP_DECODE} depth={OVERLAP_DEPTH} "
            f"fixed-c-batch={FIXED_C_BATCH}\n"
        )
        f.write(f"out_root={OUT_ROOT}\n")
        f.write("-" * 72 + "\n")


def plan_jobs(*, do_1k: bool, do_20k: bool, do_baselines: bool) -> list[dict]:
    jobs: list[dict] = []
    if do_baselines and do_1k:
        jobs.append({"mode": "fast", "size": "1k", "frac": "", "rep": 1, "flush": FLUSH_1K, "reads": READS_1K, "model": MODEL_FAST})
        jobs.append({"mode": "hac", "size": "1k", "frac": "", "rep": 1, "flush": FLUSH_1K, "reads": READS_1K, "model": MODEL_HAC})
    if do_1k:
        for frac in FORCE_FRACS:
            for rep in range(1, REPS_1K + 1):
                jobs.append({"mode": "cascade", "size": "1k", "frac": frac, "rep": rep, "flush": FLUSH_1K, "reads": READS_1K})
    if do_20k:
        for frac in FORCE_FRACS:
            for rep in range(1, REPS_20K + 1):
                jobs.append({"mode": "cascade", "size": "20k", "frac": frac, "rep": rep, "flush": FLUSH_20K, "reads": READS_20K})
    return jobs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-1k", action="store_true")
    ap.add_argument("--skip-20k", action="store_true")
    ap.add_argument("--no-baselines", action="store_true")
    ap.add_argument("--no-warmup", action="store_true")
    args = ap.parse_args()

    do_1k = not args.skip_1k
    do_20k = not args.skip_20k
    do_baselines = RUN_BASELINES_1K and not args.no_baselines and do_1k
    if not do_1k and not do_20k:
        print("ERROR: nothing to run", file=sys.stderr)
        sys.exit(1)

    ensure_bins()
    jobs = plan_jobs(do_1k=do_1k, do_20k=do_20k, do_baselines=do_baselines)

    fastq_dir = OUT_ROOT / "fastq"
    log_dir = OUT_ROOT / "cascade-logs"
    cons_dir = OUT_ROOT / "consoles"
    res_dir = OUT_ROOT / "results"
    csv_path = res_dir / "cascade_frac_sweep_results.csv"
    txt_path = res_dir / "cascade_frac_sweep_results.txt"

    print(f"OUT_ROOT: {OUT_ROOT}")
    print(f"Planned: {len(jobs)} runs")
    print(f"Force fracs: {FORCE_FRACS}")
    for j in jobs:
        if j["mode"] == "cascade":
            tag = f"cascade_{j['size']}_{frac_tag(j['frac'])}_r{j['rep']:02d}"
        else:
            tag = f"{j['mode']}_{j['size']}_baseline"
        print(f"  {tag}")

    if args.dry_run:
        print("(dry-run: exiting)")
        return

    for d in (fastq_dir, log_dir, cons_dir, res_dir):
        d.mkdir(parents=True, exist_ok=True)
    write_txt_header(txt_path)

    if WARMUP_FAST_1K and not args.no_warmup:
        print("\n=== warmup: FAST-only 1k ===")
        warm_cons = cons_dir / "warmup_fast_1k.txt"
        metrics, code = run_one(
            build_single_cmd(MODEL_FAST, READS_1K, FLUSH_1K, Path("/dev/null")),
            warm_cons,
        )
        print(f"  warmup exit={code} real={metrics.get('real_s', 'NA')}s")
        if code != 0:
            print("ERROR: warmup failed", file=sys.stderr)
            sys.exit(code)

    for i, j in enumerate(jobs, 1):
        mode = j["mode"]
        size = j["size"]
        rep = j["rep"]
        if mode == "cascade":
            frac = float(j["frac"])
            tag = f"cascade_{size}_{frac_tag(frac)}_r{rep:02d}"
            out_fastq = fastq_dir / f"output_{tag}.fastq"
            cascade_tsv = log_dir / f"{tag}.tsv"
            console = cons_dir / f"{tag}.txt"
            cmd = build_cascade_cmd(
                reads=j["reads"],
                frac=frac,
                flush=j["flush"],
                out_fastq=out_fastq,
                cascade_log=cascade_tsv,
            )
            frac_s = f"{frac:g}"
        else:
            tag = f"{mode}_{size}_baseline"
            out_fastq = fastq_dir / f"output_{tag}.fastq"
            cascade_tsv = ""
            console = cons_dir / f"{tag}.txt"
            cmd = build_single_cmd(j["model"], j["reads"], j["flush"], out_fastq)
            frac_s = ""

        print(f"\n=== [{i}/{len(jobs)}] {tag} ===")
        print("  " + " ".join(cmd))
        metrics, code = run_one(cmd, console)
        row = {
            "stamp": STAMP,
            "mode": mode,
            "size": size,
            "force_frac": frac_s,
            "rep": rep,
            "tag": tag,
            "real_s": metrics.get("real_s", ""),
            "cpu_s": metrics.get("cpu_s", ""),
            "proc_s": metrics.get("proc_s", ""),
            "peak_ram_gb": metrics.get("peak_ram_gb", ""),
            "wall_s": f"{metrics.get('wall_s', 0):.3f}",
            "kept_fast": metrics.get("kept_fast", ""),
            "promoted_hac": metrics.get("promoted_hac", ""),
            "pct_hac": metrics.get("pct_hac", ""),
            "fastq": str(out_fastq.relative_to(REPO)),
            "cascade_tsv": str(Path(cascade_tsv).relative_to(REPO)) if cascade_tsv else "",
            "console": str(console.relative_to(REPO)),
            "exit_code": code,
        }
        append_csv(csv_path, row)
        with txt_path.open("a") as f:
            f.write(
                f"{tag}: exit={code} real={row['real_s']}s "
                f"fast={row['kept_fast']} hac={row['promoted_hac']} ({row['pct_hac']}% HAC)\n"
            )
            f.write(f"  fastq={out_fastq}\n")
        print(f"  → exit={code} real={row['real_s']}s pct_hac={row['pct_hac']}")

    print("\n=== done ===")
    print(f"Results CSV: {csv_path}")
    print(f"Results TXT: {txt_path}")
    print(f"FASTQs:      {fastq_dir}")
    print(
        "\nOn main PC:\n"
        f"  python3 fetch-and-minimap-cascade-frac.py --stamp {STAMP}"
    )


if __name__ == "__main__":
    main()
