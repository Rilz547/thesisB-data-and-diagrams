#!/usr/bin/env python3
"""
FAST 1k flush-threshold sweep: narrow vs fixed-c (baseline padding), both on the
v1.2 overlap-decode path. Maps the streaming/latency tradeoff.

For each flush threshold N (GPU batch flushes once N real chunks are queued):
  - narrow   (--fixed-c-batch=no)  : GPU launch right-sized to N (no padding)
  - fixedc   (--fixed-c-batch=yes) : GPU launch full -C wide, pads (C-N) dummy slots
Both run with --overlap-decode=yes (the v1.2 baseline path). -C held at 128.

Writes per-cell FASTQs for PC-side minimap accuracy:
  output_fast_1k_overlap-li-f{flush}-{mode}.fastq

Timing artifacts use the -with-output suffix so they do not overwrite the preserved
-no-output (/dev/null) timing set.

Usage (from repo root, after build):
  python3 flush_sweep_test.py
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
MODEL = "models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0"
DATA = "test/PGXXXX230339/reads_1k.blow5"
C = 128
FLUSH = [128, 96, 64, 48, 32, 24, 16, 12, 8, 4]
MODES = [("narrow", "--fixed-c-batch=no"), ("fixedc", "--fixed-c-batch=yes")]
OUT_REPORT = REPO / "flush-sweep-results-with-output.txt"
OUT_CSV = REPO / "flush-sweep-timings-with-output.csv"
OUT_LOG_DIR = REPO / "flush-sweep-logs-with-output"

TIMING_RE = re.compile(
    r"\[main\]\s+Real time:\s*([0-9.]+)\s*sec;\s*CPU time:\s*([0-9.]+)\s*sec;\s*"
    r"Peak RAM:\s*([0-9.]+)\s*GB", re.IGNORECASE)
PHASE_RE = re.compile(r"\[basecaller_main\]\s+-\s+(?:inference|decode):\s*([0-9.]+)\s+sec")
LI_RE = re.compile(
    r"load-imbalance:\s+(\d+)\s+GPU batches\s+\((\d+)\s+tail\),\s+(\d+)\s+real chunks,"
    r"\s+(\d+)\s+padded slots\s+->\s+([0-9.]+)%")


def fastq_name(flush, mode):
    return f"output_fast_1k_overlap-li-f{flush}-{mode}.fastq"


def build_cmd(flush, mode_extra, fastq):
    parts = [SLORADO, "basecaller", f"-C {C}", "-c 12288", "-K 4096", "-p 150",
             "--overlap-decode=yes", mode_extra, f"--flush-threshold={flush}",
             "-o", fastq, MODEL, DATA]
    return shlex.split(" ".join(parts))


def run_one(cmd, log_path):
    run_cmd = cmd
    if Path("/usr/bin/stdbuf").is_file() or Path("/bin/stdbuf").is_file():
        run_cmd = ["stdbuf", "-oL", "-eL", *cmd]
    proc = subprocess.Popen(run_cmd, cwd=REPO, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
    chunks = []
    for line in proc.stdout:
        chunks.append(line); sys.stdout.write(line); sys.stdout.flush()
    rc = proc.wait()
    text = "".join(chunks)
    log_path.write_text(text, encoding="utf-8")
    if rc != 0:
        raise RuntimeError(f"exit {rc}\n{text[-3000:]}")
    m = TIMING_RE.findall(text)
    if not m:
        raise RuntimeError(f"no [main] Real time line (see {log_path})")
    real_s, cpu_s, ram = map(float, m[-1])
    phases = PHASE_RE.findall(text)
    infer = float(phases[0]) if len(phases) > 0 else 0.0
    decode = float(phases[1]) if len(phases) > 1 else 0.0
    li = LI_RE.search(text)
    b, tl, rc_, ps, pp = li.groups() if li else ("", "", "", "", "")
    return real_s, cpu_s, ram, infer, decode, int(b) if b else 0, int(tl) if tl else 0, float(pp) if pp else 0.0


def main():
    if not (REPO / "slorado").is_file():
        print("ERROR: ./slorado missing - rebuild first", file=sys.stderr); return 2
    OUT_LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    nproc = os.cpu_count() or 1
    lines = []
    csv_rows = ["flush,mode,real_s,cpu_s,peak_ram_gb,infer_s,decode_s,batches,tail,padded_pct,console_log,fastq"]
    results = {}  # flush -> mode -> tuple

    lines.append("FAST 1k flush-threshold sweep WITH FASTQ OUTPUT (narrow vs fixed-c, overlap-decode=yes)")
    lines.append(f"timestamp:    {ts}")
    lines.append(f"host nproc:   {nproc}")
    lines.append(f"model:        {MODEL}")
    lines.append(f"data:         {DATA}")
    lines.append(f"-C:           {C}")
    lines.append(f"flush sweep:  {FLUSH}")
    lines.append(f"modes:        narrow (--fixed-c-batch=no), fixedc (--fixed-c-batch=yes)")
    lines.append("output:       per-cell FASTQ (not /dev/null); timings are slower than -no-output set")
    lines.append("")
    lines.append("Notes:")
    lines.append("  - narrow: GPU batch right-sized to N real chunks (no dummy-slot padding).")
    lines.append("  - fixedc: original fixed-C, pads (C-N) dummy slots (baseline padding waste).")
    lines.append("  - padded% = (C-N)/C = imbalance magnitude; fixedc wastes it, narrow avoids it.")
    lines.append("  - speedup (fixedc/narrow) = Layer 1 (padding tax removed).")
    lines.append("  - residual narrow time growth = Layer 2 (launch overhead + low occupancy).")
    lines.append("  - FASTQ: output_fast_1k_overlap-li-f{flush}-{mode}.fastq")
    lines.append("  - 1 timed run per (flush, mode); flush 128 first warms the CUDA context.")
    lines.append("")
    print(f"FAST 1k flush sweep (with FASTQ)  nproc={nproc}  -> {OUT_REPORT}", flush=True)

    for ft in FLUSH:
        results[ft] = {}
        for mode, extra in MODES:
            fq = fastq_name(ft, mode)
            cmd = build_cmd(ft, extra, fq)
            cmd_s = " ".join(shlex.quote(x) for x in cmd)
            log_path = OUT_LOG_DIR / f"flush{ft}_{mode}.log"
            print(f"\n>> flush={ft} / {mode}  ->  {log_path.name}  fastq={fq}", flush=True)
            print(f"   $ {cmd_s}", flush=True)
            real_s, cpu_s, ram, infer, decode, b, tl, pp = run_one(cmd, log_path)
            results[ft][mode] = (real_s, infer, decode, pp, b, tl)
            lines.append(f"  flush={ft:>3} {mode:<7}: real={real_s:.3f}s infer={infer:.3f}s "
                         f"decode={decode:.3f}s padded={pp:.1f}% batches={b} tail={tl} "
                         f"ram={ram:.3f}GB log={log_path.name} fastq={fq}")
            csv_rows.append(f"{ft},{mode},{real_s:.6f},{cpu_s:.6f},{ram:.6f},{infer:.6f},"
                            f"{decode:.6f},{b},{tl},{pp:.6f},{log_path.name},{fq}")
            print(f"   real={real_s:.3f}s infer={infer:.3f}s decode={decode:.3f}s padded={pp:.1f}%", flush=True)
            OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
            OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")

    lines.append("")
    lines.append("=== Comparison: real time vs flush threshold ===")
    hdr = f"{'flush':>6} {'padded%':>8} {'fixedc(s)':>10} {'narrow(s)':>10} {'speedup':>8} {'narrow infer':>12} {'narrow decode':>13}"
    lines.append(hdr); lines.append("-" * len(hdr))
    print(f"\n{hdr}", flush=True); print("-" * len(hdr), flush=True)
    for ft in FLUSH:
        f = results[ft]["fixedc"]; n = results[ft]["narrow"]
        sp = (f[0] / n[0]) if n[0] else 0.0
        row = (f"{ft:>6} {n[3]:>7.1f}% {f[0]:>10.3f} {n[0]:>10.3f} {sp:>7.2f}x "
               f"{n[1]:>11.3f} {n[2]:>12.3f}")
        lines.append(row); print(row, flush=True)

    lines.append("")
    lines.append("Interpretation:")
    lines.append("  - padded% rises as (C-N)/C: flush 128 ~0.4% -> flush 4 ~96.9%.")
    lines.append("  - fixedc real time blows up (padding waste + launch overhead).")
    lines.append("  - speedup = Layer 1 (padding removed by narrow); grows as flush drops.")
    lines.append("  - narrow time still grows (not flat) = Layer 2 (launch overhead / occupancy).")
    lines.append("  - narrow decode overtakes narrow infer at low flush (more kernels per batch).")
    lines.append("  - Use *-no-output timings for the performance narrative; these runs are for accuracy.")
    lines.append("")
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_REPORT}")
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote consoles under {OUT_LOG_DIR}/")
    print("Wrote FASTQs: output_fast_1k_overlap-li-f*-*.fastq")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted - partial results in flush-sweep-results-with-output.txt", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); raise SystemExit(1)
