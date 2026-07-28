#!/usr/bin/env python3
"""
Load-imbalance spot-checks beyond the FAST 1k grids:

  FAST 20k (scale check at the flush=64 sweet spot):
    - flush=128 narrow  (full-batch baseline)
    - flush=64  narrow  (sweet spot)
    - flush=64  fixedc  (padding baseline at sweet spot)

  HAC 1k (model-agnostic check; not a full sweep):
    - flush=128 / 64  x  narrow / fixedc   (4 cells)

All with --overlap-decode=yes, -C 128, defaults -c 12288 -K 4096 -p 150.
Writes FASTQs for PC-side minimap accuracy.

Usage (from repo root, after build):
  python3 li_spotcheck_test.py
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
C = 128
OUT_REPORT = REPO / "li-spotcheck-results-with-output.txt"
OUT_CSV = REPO / "li-spotcheck-timings-with-output.csv"
OUT_LOG_DIR = REPO / "li-spotcheck-logs-with-output"

# (label, model, data, size_tag, flush, mode_name, mode_extra)
CELLS = [
    ("FAST 20k", "models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0",
     "test/PGXXXX230339/reads_20k.blow5", "fast_20k", 128, "narrow", "--fixed-c-batch=no"),
    ("FAST 20k", "models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0",
     "test/PGXXXX230339/reads_20k.blow5", "fast_20k", 64, "narrow", "--fixed-c-batch=no"),
    ("FAST 20k", "models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0",
     "test/PGXXXX230339/reads_20k.blow5", "fast_20k", 64, "fixedc", "--fixed-c-batch=yes"),
    ("HAC 1k", "models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0",
     "test/PGXXXX230339/reads_1k.blow5", "hac_1k", 128, "narrow", "--fixed-c-batch=no"),
    ("HAC 1k", "models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0",
     "test/PGXXXX230339/reads_1k.blow5", "hac_1k", 128, "fixedc", "--fixed-c-batch=yes"),
    ("HAC 1k", "models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0",
     "test/PGXXXX230339/reads_1k.blow5", "hac_1k", 64, "narrow", "--fixed-c-batch=no"),
    ("HAC 1k", "models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0",
     "test/PGXXXX230339/reads_1k.blow5", "hac_1k", 64, "fixedc", "--fixed-c-batch=yes"),
]

TIMING_RE = re.compile(
    r"\[main\]\s+Real time:\s*([0-9.]+)\s*sec;\s*CPU time:\s*([0-9.]+)\s*sec;\s*"
    r"Peak RAM:\s*([0-9.]+)\s*GB", re.IGNORECASE)
PHASE_RE = re.compile(r"\[basecaller_main\]\s+-\s+(?:inference|decode):\s*([0-9.]+)\s+sec")
LI_RE = re.compile(
    r"load-imbalance:\s+(\d+)\s+GPU batches\s+\((\d+)\s+tail\),\s+(\d+)\s+real chunks,"
    r"\s+(\d+)\s+padded slots\s+->\s+([0-9.]+)%")


def fastq_name(size_tag, flush, mode):
    # size_tag is e.g. fast_20k / hac_1k -> output_fast_20k_overlap-li-f64-narrow.fastq
    model, size = size_tag.split("_", 1)
    return f"output_{model}_{size}_overlap-li-f{flush}-{mode}.fastq"


def build_cmd(model, data, flush, mode_extra, fastq):
    parts = [SLORADO, "basecaller", f"-C {C}", "-c 12288", "-K 4096", "-p 150",
             "--overlap-decode=yes", mode_extra, f"--flush-threshold={flush}",
             "-o", fastq, model, data]
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
    csv_rows = ["label,size_tag,flush,mode,real_s,cpu_s,peak_ram_gb,infer_s,decode_s,batches,tail,padded_pct,console_log,fastq"]

    lines.append("Load-imbalance spot-check WITH FASTQ OUTPUT (FAST 20k + HAC 1k)")
    lines.append(f"timestamp:    {ts}")
    lines.append(f"host nproc:   {nproc}")
    lines.append(f"-C:           {C}")
    lines.append("cells:        FAST 20k (f128-narrow, f64-narrow, f64-fixedc); "
                 "HAC 1k (f128/f64 x narrow/fixedc)")
    lines.append("output:       per-cell FASTQ; use for accuracy + spot timing (not primary grid)")
    lines.append("")
    print(f"li spot-check (with FASTQ)  nproc={nproc}  -> {OUT_REPORT}", flush=True)

    for label, model, data, size_tag, flush, mode, extra in CELLS:
        fq = fastq_name(size_tag, flush, mode)
        cmd = build_cmd(model, data, flush, extra, fq)
        cmd_s = " ".join(shlex.quote(x) for x in cmd)
        log_path = OUT_LOG_DIR / f"{size_tag}_f{flush}_{mode}.log"
        print(f"\n>> {label} / flush={flush} / {mode}  ->  {log_path.name}  fastq={fq}", flush=True)
        print(f"   $ {cmd_s}", flush=True)
        real_s, cpu_s, ram, infer, decode, b, tl, pp = run_one(cmd, log_path)
        lines.append(f"  {label:<8} flush={flush:>3} {mode:<7}: real={real_s:.3f}s infer={infer:.3f}s "
                     f"decode={decode:.3f}s padded={pp:.1f}% batches={b} tail={tl} "
                     f"ram={ram:.3f}GB log={log_path.name} fastq={fq}")
        csv_rows.append(f"{label},{size_tag},{flush},{mode},{real_s:.6f},{cpu_s:.6f},{ram:.6f},"
                        f"{infer:.6f},{decode:.6f},{b},{tl},{pp:.6f},{log_path.name},{fq}")
        print(f"   real={real_s:.3f}s infer={infer:.3f}s decode={decode:.3f}s padded={pp:.1f}%", flush=True)
        OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")

    lines.append("")
    lines.append("Notes:")
    lines.append("  - FAST 20k: compare f64-narrow vs f128-narrow (sweet-spot at scale) and vs f64-fixedc.")
    lines.append("  - HAC 1k: 4-cell check that Layer 1 still appears on a heavier model.")
    lines.append("  - Primary FAST 1k grids remain in flush_sweep_test.py / batchwidth_sweep_test.py.")
    lines.append("")
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_CSV.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_REPORT}")
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote consoles under {OUT_LOG_DIR}/")
    print("Wrote FASTQs: output_{fast_20k,hac_1k}_overlap-li-f*-*.fastq")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted - partial results in li-spotcheck-results-with-output.txt", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); raise SystemExit(1)
