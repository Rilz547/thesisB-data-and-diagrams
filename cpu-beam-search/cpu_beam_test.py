#!/usr/bin/env python3
"""
Throwaway: Bonson openfish GPU-scan + CPU-beam vs full GPU decode baseline.

For each config×mode:
  1) one cache-warming run (logged, not timed in comparison)
  2) one timed /dev/null run

  A) baseline  — GPU beam (--cpu-beam=no --overlap-decode=no)
  B) cpu_beam  — managed-memory scan + CPU beam (--cpu-beam=yes)

  FAST/HAC × 1k and 20k
Full console saved per run. Comparison table at end.

Usage (from repo root, after rebuild against Bonson openfish/dev):
  python3 cpu_beam_test.py
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
SLORADO = "./slorado"
BASE_ARGS = "-C 128"
OUT_REPORT = REPO / "cpu-beam-results.txt"
OUT_CSV = REPO / "cpu-beam-timings.csv"
OUT_LOG_DIR = REPO / "cpu-beam-logs"

TIMING_RE = re.compile(
    r"\[main\]\s+Real time:\s*([0-9.]+)\s*sec;\s*"
    r"CPU time:\s*([0-9.]+)\s*sec;\s*"
    r"Peak RAM:\s*([0-9.]+)\s*GB",
    re.IGNORECASE,
)

THREADS_RE = re.compile(r"cpu beam threads:\s*(\d+)", re.IGNORECASE)

# (label, n_timed_runs, model, data)
CONFIGS = [
    ("FAST 1k", 1, "models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0", "test/PGXXXX230339/reads_1k.blow5"),
    ("HAC 1k", 1, "models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0", "test/PGXXXX230339/reads_1k.blow5"),
    ("FAST 20k", 1, "models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0", "test/PGXXXX230339/reads_20k.blow5"),
    ("HAC 20k", 1, "models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0", "test/PGXXXX230339/reads_20k.blow5"),
]

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


def slug(label: str, mode: str, kind: str = "") -> str:
    base = f"{label.replace(' ', '_')}_{mode}"
    return f"{base}_{kind}" if kind else base


def run_one(cmd: list[str], log_path: Path) -> tuple[float, float, float, int | None]:
    """Run cmd, stream stdout/stderr live, and also save the full console to log_path."""
    # stdbuf keeps C/C++ stderr line-buffered when piped (otherwise it looks "stuck").
    run_cmd = cmd
    if Path("/usr/bin/stdbuf").is_file() or Path("/bin/stdbuf").is_file():
        run_cmd = ["stdbuf", "-oL", "-eL", *cmd]

    proc = subprocess.Popen(
        run_cmd,
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        chunks.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    rc = proc.wait()
    text = "".join(chunks)
    log_path.write_text(text, encoding="utf-8")
    if rc != 0:
        raise RuntimeError(f"exit {rc}\n{text[-4000:]}")
    m = TIMING_RE.findall(text)
    if not m:
        raise RuntimeError(f"could not parse [main] Real time line (see {log_path})")
    real_s, cpu_s, ram = map(float, m[-1])
    tm = THREADS_RE.search(text)
    threads = int(tm.group(1)) if tm else None
    return real_s, cpu_s, ram, threads


def main() -> int:
    if not (REPO / "slorado").is_file():
        print("ERROR: ./slorado missing — rebuild first", file=sys.stderr)
        return 2

    OUT_LOG_DIR.mkdir(exist_ok=True)

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    nproc = os.cpu_count() or 1

    lines: list[str] = []
    csv_rows: list[str] = [
        "config,mode,phase,run,real_s,cpu_s,peak_ram_gb,cpu_beam_threads,console_log"
    ]

    # label -> mode -> real time (timed runs only)
    results: dict[str, dict[str, float]] = {}

    lines.append("CPU-beam (Bonson managed hostvis) vs GPU-beam baseline")
    lines.append(f"timestamp:     {ts}")
    lines.append(f"repo:          {REPO}")
    lines.append(f"host nproc:    {nproc}")
    lines.append(f"BASE_ARGS:     {BASE_ARGS!r}")
    lines.append(f"console logs:  {OUT_LOG_DIR}/")
    lines.append("modes:")
    for mode, extra in MODES:
        lines.append(f"  {mode}: {extra!r}")
    lines.append("")
    lines.append("Notes:")
    lines.append("  - Per config×mode: 1 cache-warm run, then 1 timed run (FAST/HAC × 1k/20k).")
    lines.append("  - Warmup is logged/CSV'd but excluded from the comparison table.")
    lines.append("  - cpu_beam: openfish_gpubuf_init_hostvis + gpu_scan + decode_cpu_beam.")
    lines.append("  - Scan tensors use cudaMallocManaged; scores D2H as fp32 for CPU beam.")
    lines.append("  - Full stdout/stderr for each run is under cpu-beam-logs/.")
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
            print(f"\n>> {label} / {mode}  (1 warmup + {n_runs} timed)", flush=True)
            print(f"   $ {cmd_s}", flush=True)

            # --- cache warm (not used in comparison) ---
            warm_log = OUT_LOG_DIR / f"{slug(label, mode, 'warmup')}.log"
            print(f"   warmup → {warm_log.name} ...", flush=True)
            w_real, w_cpu, w_ram, w_threads = run_one(cmd, warm_log)
            if w_threads is not None:
                reported_threads = w_threads
            lines.append(
                f"  warmup: real={w_real:.3f}s  cpu={w_cpu:.3f}s  peak_ram={w_ram:.3f}GB"
                + (f"  cpu_beam_threads={w_threads}" if w_threads is not None else "")
                + f"  log={warm_log.name}"
            )
            csv_rows.append(
                f"{label},{mode},warmup,0,{w_real:.6f},{w_cpu:.6f},{w_ram:.6f},"
                f"{w_threads if w_threads is not None else ''},{warm_log.name}"
            )
            print(
                f"   warmup: real={w_real:.3f}s  cpu={w_cpu:.3f}s  ram={w_ram:.3f}GB",
                flush=True,
            )

            # --- timed runs ---
            for i in range(1, n_runs + 1):
                log_path = OUT_LOG_DIR / f"{slug(label, mode)}.log"
                print(f"   timed {i}/{n_runs} → {log_path.name} ...", flush=True)
                real_s, cpu_s, ram, threads = run_one(cmd, log_path)
                if threads is not None:
                    reported_threads = threads
                results[label][mode] = real_s
                lines.append(
                    f"  timed {i}: real={real_s:.3f}s  cpu={cpu_s:.3f}s  peak_ram={ram:.3f}GB"
                    + (f"  cpu_beam_threads={threads}" if threads is not None else "")
                    + f"  log={log_path.name}"
                )
                csv_rows.append(
                    f"{label},{mode},timed,{i},{real_s:.6f},{cpu_s:.6f},{ram:.6f},"
                    f"{threads if threads is not None else ''},{log_path.name}"
                )
                print(
                    f"   timed {i}: real={real_s:.3f}s  cpu={cpu_s:.3f}s  ram={ram:.3f}GB",
                    flush=True,
                )

            lines.append("")
            OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
            OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")

    lines.append("=== Comparison (timed real time; baseline = GPU beam) ===")
    hdr = (
        f"{'config':<12}  {'baseline(s)':>12}  {'cpu_beam(s)':>12}  "
        f"{'delta(s)':>10}  {'slowdown':>9}  {'% slower':>9}"
    )
    lines.append(hdr)
    lines.append("-" * len(hdr))
    print(f"\n{hdr}", flush=True)
    print("-" * len(hdr), flush=True)

    for label, _n, _m, _d in CONFIGS:
        base = results[label]["baseline"]
        cpu = results[label]["cpu_beam"]
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
    lines.append("  - slowdown > 1 means CPU-beam path is slower than fused GPU decode.")
    lines.append("  - Scores still need a host fp32 copy; bwd/post should not (cudaMallocManaged).")
    lines.append("")

    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_REPORT}")
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote consoles under {OUT_LOG_DIR}/")
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
