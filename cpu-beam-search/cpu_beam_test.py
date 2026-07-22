#!/usr/bin/env python3
"""
Throwaway CPU-beam hybrid vs GPU-beam baseline timing suite.

For each config, runs the same number of timed /dev/null runs for:
  A) baseline  — GPU beam (--cpu-beam=no --overlap-decode=no)
  B) cpu-beam  — hybrid CPU beam (--cpu-beam=yes --overlap-decode=no)

  FAST/HAC 1k  → 5 timed runs each mode
  FAST/HAC 20k → 1 timed run each mode
No warmup. Ends with a direct comparison table for the writeup.

Usage (from repo root, after rebuild):
  python3 cpu_beam_test.py
"""

from __future__ import annotations

import os
import re
import shlex
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
SLORADO = "./slorado"
BASE_ARGS = "-C 128"
OUT_REPORT = REPO / "cpu-beam-results.txt"
OUT_CSV = REPO / "cpu-beam-timings.csv"

TIMING_RE = re.compile(
    r"\[main\]\s+Real time:\s*([0-9.]+)\s*sec;\s*"
    r"CPU time:\s*([0-9.]+)\s*sec;\s*"
    r"Peak RAM:\s*([0-9.]+)\s*GB",
    re.IGNORECASE,
)

THREADS_RE = re.compile(r"cpu beam threads:\s*(\d+)", re.IGNORECASE)

# (label, n_runs, model, data)
CONFIGS = [
    ("FAST 1k", 5, "models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0", "test/PGXXXX230339/reads_1k.blow5"),
    ("HAC 1k", 5, "models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0", "test/PGXXXX230339/reads_1k.blow5"),
    ("FAST 20k", 1, "models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0", "test/PGXXXX230339/reads_20k.blow5"),
    ("HAC 20k", 1, "models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0", "test/PGXXXX230339/reads_20k.blow5"),
]

# mode_key, extra CLI
MODES = [
    ("baseline", "--cpu-beam=no --overlap-decode=no"),
    ("cpu_beam", "--cpu-beam=yes --overlap-decode=no"),
]


def build_cmd(model: str, data: str, extra: str) -> list[str]:
    parts = [SLORADO, "basecaller"]
    parts.extend(shlex.split(BASE_ARGS))
    parts.extend(["-o", "/dev/null"])
    parts.extend(shlex.split(extra))
    parts.extend([model, data])
    return parts


def run_one(cmd: list[str]) -> tuple[float, float, float, int | None]:
    proc = subprocess.run(
        cmd,
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    text = proc.stdout
    if proc.returncode != 0:
        raise RuntimeError(f"exit {proc.returncode}\n{text[-4000:]}")
    m = TIMING_RE.findall(text)
    if not m:
        raise RuntimeError("could not parse [main] Real time line")
    real_s, cpu_s, ram = map(float, m[-1])
    tm = THREADS_RE.search(text)
    threads = int(tm.group(1)) if tm else None
    return real_s, cpu_s, ram, threads


def summarise(reals: list[float]) -> dict[str, float]:
    n = len(reals)
    mean = statistics.fmean(reals)
    stdev = statistics.stdev(reals) if n >= 2 else 0.0
    return {
        "n": float(n),
        "mean": mean,
        "stdev": stdev,
        "min": min(reals),
        "max": max(reals),
        "cv_pct": (stdev / mean * 100.0) if mean else 0.0,
    }


def main() -> int:
    if not (REPO / "slorado").is_file():
        print("ERROR: ./slorado missing — rebuild first", file=sys.stderr)
        return 2

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    nproc = os.cpu_count() or 1

    lines: list[str] = []
    csv_rows: list[str] = ["config,mode,run,real_s,cpu_s,peak_ram_gb,cpu_beam_threads"]

    # label -> mode -> list of real times
    results: dict[str, dict[str, list[float]]] = {}

    lines.append("CPU-beam hybrid vs GPU-beam baseline (paired timing suite)")
    lines.append(f"timestamp:     {ts}")
    lines.append(f"repo:          {REPO}")
    lines.append(f"host nproc:    {nproc}")
    lines.append(f"BASE_ARGS:     {BASE_ARGS!r}")
    lines.append("modes:")
    for mode, extra in MODES:
        lines.append(f"  {mode}: {extra!r}")
    lines.append("")
    lines.append("Notes:")
    lines.append("  - Same run counts for baseline and cpu_beam (5× 1k, 1× 20k). No warmup.")
    lines.append("  - cpu_beam: beam on CPU (nproc threads); bwd/fwd-post/qual/gen stay on GPU.")
    lines.append("  - Extra D2H (scores+bwd) + H2D (states+moves) + sync at the beam boundary.")
    lines.append("  - Comparison table at end uses mean real time (or the single run for n=1).")
    lines.append("")

    print(f"CPU-beam vs baseline  nproc={nproc}  → {OUT_REPORT}", flush=True)

    reported_threads: int | None = None

    for label, n_runs, model, data in CONFIGS:
        results[label] = {}
        for mode, extra in MODES:
            cmd = build_cmd(model, data, extra)
            cmd_s = " ".join(shlex.quote(x) for x in cmd)
            lines.append(f"=== {label} | {mode} ===")
            lines.append(f"command: {cmd_s}")
            print(f"\n>> {label} / {mode}  ({n_runs} run(s))", flush=True)
            print(f"   $ {cmd_s}", flush=True)

            reals: list[float] = []
            for i in range(1, n_runs + 1):
                print(f"   run {i}/{n_runs}...", flush=True)
                real_s, cpu_s, ram, threads = run_one(cmd)
                if threads is not None:
                    reported_threads = threads
                reals.append(real_s)
                lines.append(
                    f"  run {i}: real={real_s:.3f}s  cpu={cpu_s:.3f}s  peak_ram={ram:.3f}GB"
                    + (f"  cpu_beam_threads={threads}" if threads is not None else "")
                )
                csv_rows.append(
                    f"{label},{mode},{i},{real_s:.6f},{cpu_s:.6f},{ram:.6f},"
                    f"{threads if threads is not None else ''}"
                )
                print(
                    f"   run {i}: real={real_s:.3f}s  cpu={cpu_s:.3f}s  ram={ram:.3f}GB",
                    flush=True,
                )

            s = summarise(reals)
            results[label][mode] = reals
            if s["n"] >= 2:
                lines.append(
                    f"  summary: n={int(s['n'])} mean={s['mean']:.3f}s stdev={s['stdev']:.3f}s "
                    f"min={s['min']:.3f}s max={s['max']:.3f}s CV%={s['cv_pct']:.3f}%"
                )
            else:
                lines.append(f"  summary: n=1 real={reals[0]:.3f}s")
            lines.append("")

            # Flush after each mode so Ctrl+C still leaves partial data
            OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
            OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")

    # --- comparison ---
    lines.append("=== Comparison (mean real time; baseline = GPU beam) ===")
    hdr = (
        f"{'config':<12}  {'baseline(s)':>12}  {'cpu_beam(s)':>12}  "
        f"{'delta(s)':>10}  {'slowdown':>9}  {'% slower':>9}"
    )
    lines.append(hdr)
    lines.append("-" * len(hdr))
    print(f"\n{hdr}", flush=True)
    print("-" * len(hdr), flush=True)

    for label, _n, _m, _d in CONFIGS:
        base = summarise(results[label]["baseline"])["mean"]
        cpu = summarise(results[label]["cpu_beam"])["mean"]
        delta = cpu - base
        slowdown = (cpu / base) if base else 0.0
        pct = ((cpu - base) / base * 100.0) if base else 0.0
        row = (
            f"{label:<12}  {base:>12.3f}  {cpu:>12.3f}  "
            f"{delta:>+10.3f}  {slowdown:>8.2f}x  {pct:>+8.1f}%"
        )
        lines.append(row)
        print(row, flush=True)

    lines.append("")
    lines.append("=== Environment ===")
    lines.append(f"reported cpu_beam_threads: {reported_threads}")
    lines.append(f"os.cpu_count():            {nproc}")
    lines.append("")
    lines.append("Interpretation hooks:")
    lines.append("  - slowdown > 1 means CPU-beam hybrid is slower than GPU beam (expected).")
    lines.append("  - Extra PCIe traffic + host sync at the beam boundary usually dominates.")
    lines.append("  - Could still matter for heavier models on hosts with stronger CPUs.")
    lines.append("")

    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_REPORT}")
    print(f"Wrote {OUT_CSV}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted — partial results may be in cpu-beam-results.txt", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
