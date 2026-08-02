#!/usr/bin/env python3
"""
Cascade threshold sweep (Jetson).

Runs FAST→HAC cascade across thresholds:
  - 1k  × 3 reps per threshold
  - 20k × 1 rep  per threshold

Does NOT run automatically in CI — invoke yourself from repo root with venv active:

  python3 docs-riley/cascade_threshold_sweep.py --dry-run
  python3 docs-riley/cascade_threshold_sweep.py
  python3 docs-riley/cascade_threshold_sweep.py --skip-20k   # 1k only
  python3 docs-riley/cascade_threshold_sweep.py --skip-1k    # 20k only

Layout (under OUT_ROOT, stamped):
  fastq/          output_cascade_{1k|20k}_thr{NN}_r{RR}.fastq
  cascade-logs/   matching .tsv (read_id / mean_q / model)
  consoles/       full slorado stdout per run
  results/        summary .csv + .txt for the whole sweep

Edit THRESHOLDS / FLUSH_* in CONFIG if you want a different set.
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
FIXED_C_BATCH = "no"  # narrow partials

# Match prior showers: 1k used flush=64; successful 20k cascade used full C (=128).
FLUSH_1K = 64
FLUSH_20K = 128

# Promote if FAST mean Phred Q < threshold.
THRESHOLDS = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0]

REPS_1K = 3
REPS_20K = 1

WARMUP_FAST_1K = True  # one FAST-only 1k before timed cascade suite

STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT_ROOT = Path(__file__).resolve().parent / "cascade-threshold-sweep" / STAMP

REAL_TIME_RE = re.compile(
    r"\[main\] Real time: ([\d.]+) sec; CPU time: ([\d.]+) sec; Peak RAM: ([\d.]+) GB"
)
PROC_RE = re.compile(r"\[basecaller_main\] data processing: ([\d.]+) sec")
CASCADE_RE = re.compile(
    r"cascade: kept_fast=(\d+) promoted_hac=(\d+) \(([\d.]+)% HAC\) threshold=([\d.]+)"
)

CSV_FIELDS = [
    "stamp",
    "size",
    "threshold",
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def thr_tag(thr: float) -> str:
    """10.0 → thr10 ; 8.5 → thr8p5"""
    if float(thr).is_integer():
        return f"thr{int(thr):02d}"
    s = f"{thr:g}".replace(".", "p")
    return f"thr{s}"


def build_cascade_cmd(
    *,
    reads: Path,
    thr: float,
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
        f"--cascade-threshold={thr}",
        f"--cascade-log={cascade_log}",
        "-o",
        str(out_fastq),
        str(MODEL_FAST),
        str(reads),
    ]


def build_warmup_cmd() -> list[str]:
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
        str(FLUSH_1K),
        "-o",
        "/dev/null",
        str(MODEL_FAST),
        str(READS_1K),
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
        out["threshold_reported"] = float(m.group(4))
    return out


def ensure_bins() -> None:
    if not SLORADO.is_file():
        print(f"ERROR: slorado binary missing: {SLORADO}", file=sys.stderr)
        sys.exit(1)
    for p in (MODEL_FAST, MODEL_HAC, READS_1K, READS_20K):
        if not p.exists():
            print(f"ERROR: missing path: {p}", file=sys.stderr)
            sys.exit(1)


def run_one(cmd: list[str], console_path: Path) -> tuple[dict, int, str]:
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
    return metrics, proc.returncode, text


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
        f.write(f"cascade threshold sweep  {STAMP}\n")
        f.write(f"thresholds: {THRESHOLDS}\n")
        f.write(f"1k reps={REPS_1K} flush={FLUSH_1K}; 20k reps={REPS_20K} flush={FLUSH_20K}\n")
        f.write(
            f"flags: -C {GPU_BATCH} -c {CHUNK_SIZE} -K {READ_BATCH} -p {OVERLAP} "
            f"overlap-decode={OVERLAP_DECODE} depth={OVERLAP_DEPTH} "
            f"fixed-c-batch={FIXED_C_BATCH}\n"
        )
        f.write(f"fast={MODEL_FAST.name}\n")
        f.write(f"hac={MODEL_HAC.name}\n")
        f.write(f"out_root={OUT_ROOT}\n")
        f.write("-" * 72 + "\n")


def plan_jobs(*, do_1k: bool, do_20k: bool) -> list[dict]:
    jobs: list[dict] = []
    if do_1k:
        for thr in THRESHOLDS:
            for rep in range(1, REPS_1K + 1):
                jobs.append({"size": "1k", "thr": thr, "rep": rep, "flush": FLUSH_1K, "reads": READS_1K})
    if do_20k:
        for thr in THRESHOLDS:
            for rep in range(1, REPS_20K + 1):
                jobs.append({"size": "20k", "thr": thr, "rep": rep, "flush": FLUSH_20K, "reads": READS_20K})
    return jobs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print planned runs only")
    ap.add_argument("--skip-1k", action="store_true")
    ap.add_argument("--skip-20k", action="store_true")
    ap.add_argument("--no-warmup", action="store_true")
    args = ap.parse_args()

    do_1k = not args.skip_1k
    do_20k = not args.skip_20k
    if not do_1k and not do_20k:
        print("ERROR: nothing to run (--skip-1k and --skip-20k)", file=sys.stderr)
        sys.exit(1)

    ensure_bins()
    jobs = plan_jobs(do_1k=do_1k, do_20k=do_20k)

    fastq_dir = OUT_ROOT / "fastq"
    log_dir = OUT_ROOT / "cascade-logs"
    cons_dir = OUT_ROOT / "consoles"
    res_dir = OUT_ROOT / "results"
    csv_path = res_dir / "cascade_sweep_results.csv"
    txt_path = res_dir / "cascade_sweep_results.txt"

    n_1k = sum(1 for j in jobs if j["size"] == "1k")
    n_20k = sum(1 for j in jobs if j["size"] == "20k")
    print(f"OUT_ROOT: {OUT_ROOT}")
    print(f"Planned: {len(jobs)} runs  (1k={n_1k}, 20k={n_20k})")
    print(f"Thresholds: {THRESHOLDS}")
    for j in jobs:
        tag = f"cascade_{j['size']}_{thr_tag(j['thr'])}_r{j['rep']:02d}"
        print(f"  {tag}  flush={j['flush']}")

    if args.dry_run:
        print("(dry-run: exiting)")
        return

    for d in (fastq_dir, log_dir, cons_dir, res_dir):
        d.mkdir(parents=True, exist_ok=True)
    write_txt_header(txt_path)

    if WARMUP_FAST_1K and not args.no_warmup:
        print("\n=== warmup: FAST-only 1k ===")
        warm_cons = cons_dir / "warmup_fast_1k.txt"
        metrics, code, _ = run_one(build_warmup_cmd(), warm_cons)
        print(
            f"  warmup exit={code} real={metrics.get('real_s', 'NA')}s "
            f"(console {warm_cons.name})"
        )
        if code != 0:
            print("ERROR: warmup failed; aborting", file=sys.stderr)
            sys.exit(code)

    for i, j in enumerate(jobs, 1):
        size = j["size"]
        thr = j["thr"]
        rep = j["rep"]
        tag = f"cascade_{size}_{thr_tag(thr)}_r{rep:02d}"
        out_fastq = fastq_dir / f"output_{tag}.fastq"
        cascade_tsv = log_dir / f"{tag}.tsv"
        console = cons_dir / f"{tag}.txt"

        cmd = build_cascade_cmd(
            reads=j["reads"],
            thr=thr,
            flush=j["flush"],
            out_fastq=out_fastq,
            cascade_log=cascade_tsv,
        )

        print(f"\n=== [{i}/{len(jobs)}] {tag} ===")
        print("  " + " ".join(cmd))

        metrics, code, _ = run_one(cmd, console)
        row = {
            "stamp": STAMP,
            "size": size,
            "threshold": f"{thr:g}",
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
            "cascade_tsv": str(cascade_tsv.relative_to(REPO)),
            "console": str(console.relative_to(REPO)),
            "exit_code": code,
        }
        append_csv(csv_path, row)
        with txt_path.open("a") as f:
            f.write(
                f"{tag}: exit={code} real={row['real_s']}s proc={row['proc_s']}s "
                f"peak_ram={row['peak_ram_gb']}GB "
                f"fast={row['kept_fast']} hac={row['promoted_hac']} "
                f"({row['pct_hac']}% HAC)\n"
            )
            f.write(f"  fastq={out_fastq}\n")
            f.write(f"  tsv={cascade_tsv}\n")

        print(
            f"  → exit={code} real={row['real_s']}s "
            f"kept_fast={row['kept_fast']} promoted_hac={row['promoted_hac']} "
            f"({row['pct_hac']}% HAC)"
        )
        if code != 0:
            print(f"ERROR: run failed; see {console}", file=sys.stderr)
            # continue so a single thr failure doesn't wipe the sweep
            continue

    print("\n=== done ===")
    print(f"Results CSV: {csv_path}")
    print(f"Results TXT: {txt_path}")
    print(f"FASTQs:      {fastq_dir}")
    print(f"Cascade TSV: {log_dir}")
    print(
        "\nOn main PC (after copy/adapt of fetch script):\n"
        "  python3 fetch-and-minimap-cascade.py\n"
        f"  # remote glob: docs-riley/cascade-threshold-sweep/{STAMP}/fastq/output_cascade_*.fastq"
    )


if __name__ == "__main__":
    main()
