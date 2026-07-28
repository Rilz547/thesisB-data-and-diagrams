#!/usr/bin/env python3
"""
2D sub-sweep: batch width (-C) x flush threshold, narrow vs fixed-c, overlap-decode=yes.

Shows the interaction between batch width and the padding tax:
  padded% = (C - N)/C  -> grows with C for a fixed flush N.
So narrow's speedup (Layer 1) GROWS with batch width. This is the C-interaction surface.

Grid (FAST 1k):
  C      in {64, 128}   (C=256 OOMs on Jetson with overlap-decode; ~1GB contiguous alloc fails)
  flush  in {full (=-C), 32, 8, 4}
  mode   in {narrow (--fixed-c-batch=no), fixedc (--fixed-c-batch=yes)}
All with --overlap-decode=yes. 1 timed run per cell; full-batch cells run first to warm CUDA.

Writes per-cell FASTQs for PC-side minimap accuracy:
  output_fast_1k_overlap-li-C{C}-f{flush}-{mode}.fastq

Timing artifacts use the -with-output suffix so they do not overwrite the preserved
-no-output (/dev/null) timing set.

Usage (from repo root, after build):
  python3 batchwidth_sweep_test.py
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
CS = [64, 128]  # C=256 OOMs on this Jetson with --overlap-decode=yes (~1GB contiguous alloc fails)
FLUSH_PER_C = {  # flush values per C; "full" means -C (no early flush)
    64:  ["full", 32, 8, 4],
    128: ["full", 32, 8, 4],
}
MODES = [("narrow", "--fixed-c-batch=no"), ("fixedc", "--fixed-c-batch=yes")]
OUT_REPORT = REPO / "batchwidth-sweep-results-with-output.txt"
OUT_CSV = REPO / "batchwidth-sweep-timings-with-output.csv"
OUT_LOG_DIR = REPO / "batchwidth-sweep-logs-with-output"

TIMING_RE = re.compile(
    r"\[main\]\s+Real time:\s*([0-9.]+)\s*sec;\s*CPU time:\s*([0-9.]+)\s*sec;\s*"
    r"Peak RAM:\s*([0-9.]+)\s*GB", re.IGNORECASE)
PHASE_RE = re.compile(r"\[basecaller_main\]\s+-\s+(?:inference|decode):\s*([0-9.]+)\s+sec")
LI_RE = re.compile(
    r"load-imbalance:\s+(\d+)\s+GPU batches\s+\((\d+)\s+tail\),\s+(\d+)\s+real chunks,"
    r"\s+(\d+)\s+padded slots\s+->\s+([0-9.]+)%")


def fastq_name(c, flush, mode):
    return f"output_fast_1k_overlap-li-C{c}-f{flush}-{mode}.fastq"


def build_cmd(c, flush, mode_extra, fastq):
    ft = str(c) if flush == "full" else str(flush)
    parts = [SLORADO, "basecaller", f"-C {c}", "-c 12288", "-K 4096", "-p 150",
             "--overlap-decode=yes", mode_extra, f"--flush-threshold={ft}",
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
    csv_rows = ["C,flush,mode,real_s,cpu_s,peak_ram_gb,infer_s,decode_s,batches,tail,padded_pct,console_log,fastq"]
    results = {}  # C -> flush -> mode -> tuple

    lines.append("FAST 1k 2D sweep WITH FASTQ OUTPUT: batch width (C) x flush threshold (narrow vs fixedc)")
    lines.append(f"timestamp:    {ts}")
    lines.append(f"host nproc:   {nproc}")
    lines.append(f"model:        {MODEL}")
    lines.append(f"data:         {DATA}")
    lines.append(f"C values:     {CS}")
    lines.append(f"flush per C:  full, 32, 8, 4")
    lines.append(f"modes:        narrow (--fixed-c-batch=no), fixedc (--fixed-c-batch=yes)")
    lines.append("output:       per-cell FASTQ (not /dev/null); timings are slower than -no-output set")
    lines.append("")
    lines.append("Notes:")
    lines.append("  - padded% = (C-N)/C -> for fixed flush N, larger C = MORE padding -> bigger Layer 1 win.")
    lines.append("  - At full batches (flush=C), only the tail is partial; larger C = bigger tail waste.")
    lines.append("  - speedup (fixedc/narrow) = Layer 1; grows with C. Residual = Layer 2.")
    lines.append("  - FASTQ: output_fast_1k_overlap-li-C{C}-f{flush}-{mode}.fastq")
    lines.append("  - 1 timed run per cell; full-batch cells run first to warm CUDA per C.")
    lines.append("")
    print(f"FAST 1k 2D sweep (with FASTQ)  nproc={nproc}  -> {OUT_REPORT}", flush=True)

    for c in CS:
        results[c] = {}
        flushes = FLUSH_PER_C[c]
        for ft in flushes:
            results[c][ft] = {}
            for mode, extra in MODES:
                fq = fastq_name(c, ft, mode)
                cmd = build_cmd(c, ft, extra, fq)
                cmd_s = " ".join(shlex.quote(x) for x in cmd)
                log_path = OUT_LOG_DIR / f"C{c}_flush{ft}_{mode}.log"
                print(f"\n>> C={c} / flush={ft} / {mode}  ->  {log_path.name}  fastq={fq}", flush=True)
                print(f"   $ {cmd_s}", flush=True)
                real_s, cpu_s, ram, infer, decode, b, tl, pp = run_one(cmd, log_path)
                results[c][ft][mode] = (real_s, infer, decode, pp, b, tl)
                lines.append(f"  C={c} flush={ft:>4} {mode:<7}: real={real_s:.3f}s infer={infer:.3f}s "
                             f"decode={decode:.3f}s padded={pp:.1f}% batches={b} tail={tl} "
                             f"ram={ram:.3f}GB log={log_path.name} fastq={fq}")
                csv_rows.append(f"{c},{ft},{mode},{real_s:.6f},{cpu_s:.6f},{ram:.6f},{infer:.6f},"
                                f"{decode:.6f},{b},{tl},{pp:.6f},{log_path.name},{fq}")
                print(f"   real={real_s:.3f}s infer={infer:.3f}s decode={decode:.3f}s padded={pp:.1f}%", flush=True)
                OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
                OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")

    lines.append("")
    lines.append("=== Comparison: real time vs flush, per C ===")
    for c in CS:
        lines.append(f"\nC={c}:")
        hdr = f"{'flush':>6} {'padded%':>8} {'fixedc(s)':>10} {'narrow(s)':>10} {'speedup':>8}"
        lines.append(hdr); lines.append("-" * len(hdr))
        print(f"\nC={c}:", flush=True); print(hdr, flush=True); print("-" * len(hdr), flush=True)
        for ft in FLUSH_PER_C[c]:
            f = results[c][ft]["fixedc"]; n = results[c][ft]["narrow"]
            sp = (f[0] / n[0]) if n[0] else 0.0
            row = f"{str(ft):>6} {n[3]:>7.1f}% {f[0]:>10.3f} {n[0]:>10.3f} {sp:>7.2f}x"
            lines.append(row); print(row, flush=True)

    lines.append("")
    lines.append("=== Layer 1 speedup surface (fixedc/narrow), rows=C cols=flush ===")
    hdr = "C      " + "  ".join(f"flush={ft:>4}" for ft in ["full", 32, 8, 4])
    lines.append(hdr); lines.append("-" * len(hdr))
    print(f"\n{hdr}", flush=True); print("-" * len(hdr), flush=True)
    for c in CS:
        cells = []
        for ft in ["full", 32, 8, 4]:
            f = results[c][ft]["fixedc"]; n = results[c][ft]["narrow"]
            sp = (f[0] / n[0]) if n[0] else 0.0
            cells.append(f"{sp:>6.2f}x")
        row = f"C={c:<3} " + "  ".join(cells)
        lines.append(row); print(row, flush=True)

    lines.append("")
    lines.append("Interpretation:")
    lines.append("  - For a fixed flush N, padded% = (C-N)/C rises with C -> narrow removes more at larger C.")
    lines.append("  - Speedup (Layer 1) should grow down each row (flush down) AND across rows (C up).")
    lines.append("  - At full batches, narrow ~ fixedc (no regression); tail waste ~ (C-N_tail)/total grows with C.")
    lines.append("  - Residual narrow time growth at low flush = Layer 2 (launch overhead / occupancy).")
    lines.append("  - Use *-no-output timings for the performance narrative; these runs are for accuracy.")
    lines.append("")
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_REPORT}")
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote consoles under {OUT_LOG_DIR}/")
    print("Wrote FASTQs: output_fast_1k_overlap-li-C*-f*-*.fastq")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted - partial results in batchwidth-sweep-results-with-output.txt", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); raise SystemExit(1)
