#!/usr/bin/env python3
"""
Force-frac cascade accuracy pass (main PC: minimap2).

Default mode (--missing-20k): ONLY the incomplete 20k fracs that OOM'd:
  0.15, 0.20, 0.30, 0.40, 0.50

Looks for re-run FASTQs on the Jetson under:
  docs-riley/cascade-20k-missing-rerun/output_cascade_20k_frac*.fastq
  docs-riley/cascade-sanity/output_cascade_20k_frac*.fastq
  docs-riley/cascade-force-frac-sweep/*/fastq/output_cascade_20k_frac*.fastq

Baselines (overlap v1.2 measured — not "expected"):
  FAST 20k median = 0.939375
  HAC  20k median = 0.977333

Jetson: produce the missing FASTQs first (lower VRAM), e.g.:

  mkdir -p docs-riley/cascade-20k-missing-rerun
  for f in 0.15 0.20 0.30 0.40 0.50; do
    tag=$(python3 -c "print(f'frac{int(round({f}*1000)):03d}')")
    ./slorado basecaller \\
      -C 64 -c 12288 -K 4096 -p 150 \\
      --overlap-decode=yes --overlap-depth=1 --fixed-c-batch=no \\
      --flush-threshold=64 \\
      --cascade=yes \\
      --cascade-hac=models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0 \\
      --cascade-force-frac=$f \\
      --cascade-log=docs-riley/cascade-20k-missing-rerun/cascade_20k_${tag}_C64.tsv \\
      -o docs-riley/cascade-20k-missing-rerun/output_cascade_20k_${tag}_C64.fastq \\
      models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 \\
      test/PGXXXX230339/reads_20k.blow5
  done

Main PC (copy this script next to overlapping/):

  python3 fetch-and-minimap-cascade-frac.py --dry-run
  python3 fetch-and-minimap-cascade-frac.py
  python3 fetch-and-minimap-cascade-frac.py --all --stamp <UTCSTAMP>   # full sweep
"""

from __future__ import annotations

import argparse
import atexit
import csv
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

JETSON_HOST = "192.168.4.48"
JETSON_USER = "riley"
JETSON_REPO = "~/slorado-riley-v2"

REMOTE_FASTQ_GLOB = "docs-riley/cascade-force-frac-sweep/*/fastq/output_*.fastq"
REMOTE_TSV_GLOB = "docs-riley/cascade-force-frac-sweep/*/cascade-logs/*.tsv"
REMOTE_RESULTS_GLOB = "docs-riley/cascade-force-frac-sweep/*/results/cascade_frac_sweep_results.csv"

# Incomplete in the first force-frac sweep (OOM / truncated FASTQs).
MISSING_20K_FRACS = (0.15, 0.20, 0.30, 0.40, 0.50)
MISSING_20K_GLOBS = [
    "docs-riley/cascade-20k-missing-rerun/output_cascade_20k_frac*.fastq",
    "docs-riley/cascade-sanity/output_cascade_20k_frac*.fastq",
    "docs-riley/cascade-force-frac-sweep/*/fastq/output_cascade_20k_frac*.fastq",
]

LOCAL_WORK_DIR = Path.cwd() / "slorado-accuracy-tmp-cascade-frac"

REFERENCE_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"
MINIMAP2 = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"
MINIMAP2_PRESET = "map-ont"
MINIMAP2_THREADS = 16
REFERENCE_MMI = LOCAL_WORK_DIR / f"{REFERENCE_FASTA.stem}.mmi"

# Overlap v1.2 measured baselines (use these — not old "expected" constants).
BASELINE_MEDIAN = {
    ("fast", "1k"): 0.940763,
    ("hac", "1k"): 0.976852,
    ("fast", "20k"): 0.939375,
    ("hac", "20k"): 0.977333,
}

ACCURACY_DIR = Path.cwd() / "overlapping" / "cascade-force-frac" / "accuracy"
ACCURACY_CSV_NAME = "minimap_accuracy-cascade-frac.csv"
ACCURACY_CSV_MISSING = "minimap_accuracy-cascade-frac-missing20k.csv"
SWEEP_ARTIFACTS_DIR = Path.cwd() / "overlapping" / "cascade-force-frac" / "sweep-artifacts"
RESULTS_CSV = LOCAL_WORK_DIR / "minimap_results.csv"

_SSH_CONTROL = f"/tmp/ssh-slorado-cascade-frac-{JETSON_USER}@{JETSON_HOST}-%p"
_SSH_OPTS = [
    "-o", "ControlMaster=auto",
    "-o", f"ControlPath={_SSH_CONTROL}",
    "-o", "ControlPersist=120",
]

_USE_COLOR = sys.stdout.isatty()


class Col:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"


def c(text: str, *styles: str) -> str:
    if not _USE_COLOR or not styles:
        return text
    return f"{''.join(styles)}{text}{Col.RESET}"


def banner(title: str, style: str = Col.CYAN) -> None:
    print()
    print(c("=" * 60, style, Col.BOLD))
    print(c(f"  {title}", style, Col.BOLD))
    print(c("=" * 60, style, Col.BOLD))
    print()


def die(msg: str, code: int = 1) -> None:
    print(c(f"ERROR: {msg}", Col.RED, Col.BOLD), file=sys.stderr)
    sys.exit(code)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(c(f"+ {' '.join(cmd)}", Col.DIM))
    return subprocess.run(cmd, check=check, text=True)


def ssh_base() -> list[str]:
    return ["ssh", *_SSH_OPTS, f"{JETSON_USER}@{JETSON_HOST}"]


def rsync_ssh_e() -> str:
    return "ssh " + " ".join(_SSH_OPTS)


def close_ssh_master() -> None:
    subprocess.run(
        ["ssh", *_SSH_OPTS, "-O", "exit", f"{JETSON_USER}@{JETSON_HOST}"],
        check=False,
        capture_output=True,
        text=True,
    )


def remote_list_glob(glob_pat: str) -> list[str]:
    remote_cmd = (
        f"bash -lc 'cd {JETSON_REPO} && "
        f"compgen -G \"{glob_pat}\" | sort || true'"
    )
    proc = subprocess.run(
        ssh_base() + [remote_cmd],
        check=True,
        text=True,
        capture_output=True,
    )
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def filter_by_stamp(paths: list[str], stamp: str | None) -> list[str]:
    if not stamp:
        return paths
    needle = f"cascade-force-frac-sweep/{stamp}/"
    return [p for p in paths if needle in p]


def filter_add_only(dest: Path, remote_rel_paths: list[str], label: str) -> list[str]:
    """Keep only remotes whose basename is not already in dest (never overwrite)."""
    dest = dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    if not dest.is_dir():
        die(f"{label} path exists but is not a directory: {dest}")
    keep: list[str] = []
    skipped: list[str] = []
    for rel in remote_rel_paths:
        name = Path(rel).name
        if (dest / name).is_file():
            skipped.append(name)
        else:
            keep.append(rel)
    if skipped:
        print(c(f"Add-only {label}: skipping {len(skipped)} existing file(s)", Col.DIM))
        for n in skipped[:8]:
            print(c(f"  skip: {n}", Col.DIM))
        if len(skipped) > 8:
            print(c(f"  ... +{len(skipped) - 8} more", Col.DIM))
    if keep:
        print(c(f"Add-only {label}: pulling {len(keep)} new file(s)", Col.GREEN))
    else:
        print(c(f"Add-only {label}: nothing new to pull", Col.DIM))
    return keep


def rsync_pull(remote_rel_paths: list[str], dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    if not remote_rel_paths:
        return []
    sources = [f"{JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}/{rel}" for rel in remote_rel_paths]
    run(["rsync", "-avP", "-e", rsync_ssh_e(), *sources, str(dest) + "/"])
    out: list[Path] = []
    for rel in remote_rel_paths:
        local = dest / Path(rel).name
        if not local.exists():
            die(f"rsync did not create {local}")
        out.append(local)
    return out


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


def ensure_mmi(reference: Path, mmi: Path) -> Path:
    if mmi.is_file() and mmi.stat().st_mtime >= reference.stat().st_mtime:
        print(c(f"Using cached index: {mmi}", Col.DIM))
        return mmi
    banner(f"building minimap2 index → {mmi.name}", Col.YELLOW)
    mmi.parent.mkdir(parents=True, exist_ok=True)
    run([MINIMAP2, "-x", MINIMAP2_PRESET, "-t", str(MINIMAP2_THREADS), "-d", str(mmi), str(reference)])
    return mmi


def run_minimap2(fastq: Path, target: Path, out_paf: Path) -> tuple[float | None, int]:
    cmd = [
        MINIMAP2, "-cx", MINIMAP2_PRESET, "-t", str(MINIMAP2_THREADS),
        "--secondary=no", str(target), str(fastq),
    ]
    print(c(f"+ {' '.join(cmd)} > {out_paf}", Col.DIM))
    with out_paf.open("w") as out:
        subprocess.run(cmd, check=True, stdout=out)
    return median_identity(out_paf)


def frac_tag(frac: float) -> str:
    return f"frac{int(round(frac * 1000)):03d}"


def parse_meta(name: str) -> dict[str, str]:
    """
    output_cascade_1k_frac017_r01.fastq
    output_cascade_20k_frac300_C64.fastq
    output_fast_1k_baseline.fastq
    """
    base = Path(name).name
    m = re.fullmatch(
        r"output_cascade_(1k|20k)_frac(\d{3})(?:_r(\d+)|_C\d+)?\.fastq",
        base,
    )
    if m:
        return {
            "mode": "cascade",
            "size": m.group(1),
            "force_frac": f"{int(m.group(2)) / 1000:.3f}".rstrip("0").rstrip("."),
            "rep": m.group(3) or "1",
        }
    m = re.fullmatch(r"output_(fast|hac)_(1k|20k)_baseline\.fastq", base)
    if m:
        return {"mode": m.group(1), "size": m.group(2), "force_frac": "", "rep": "1"}
    return {"mode": "", "size": "", "force_frac": "", "rep": ""}


def is_missing_20k_target(path: str) -> bool:
    meta = parse_meta(Path(path).name)
    if meta.get("mode") != "cascade" or meta.get("size") != "20k":
        return False
    try:
        frac = float(meta["force_frac"])
    except (KeyError, ValueError):
        return False
    return any(abs(frac - t) < 1e-9 for t in MISSING_20K_FRACS)


def list_missing_20k_remotes() -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for g in MISSING_20K_GLOBS:
        for rel in remote_list_glob(g):
            if not is_missing_20k_target(rel):
                continue
            # Prefer cascade-20k-missing-rerun / C64 names over old truncated sweep files.
            name = Path(rel).name
            if name in seen:
                # replace if new path is under missing-rerun
                if "cascade-20k-missing-rerun" in rel:
                    found = [p for p in found if Path(p).name != name]
                    found.append(rel)
                    seen.add(name)
                continue
            seen.add(name)
            found.append(rel)
    # If both truncated sweep + C64 exist, keep C64 / missing-rerun only.
    by_frac: dict[str, list[str]] = defaultdict(list)
    for rel in found:
        by_frac[parse_meta(Path(rel).name)["force_frac"]].append(rel)
    picked: list[str] = []
    for frac in sorted(by_frac.keys(), key=lambda x: float(x)):
        cands = by_frac[frac]
        rerun = [p for p in cands if "cascade-20k-missing-rerun" in p or "_C64" in p]
        picked.append(sorted(rerun or cands)[-1])
    return picked


ACCURACY_FIELDS = [
    "timestamp",
    "fastq",
    "mode",
    "size",
    "force_frac",
    "rep",
    "n_reads",
    "n_alignments",
    "map_rate",
    "accuracy_median",
    "delta_vs_fast",
    "delta_vs_hac",
    "real_s",
    "pct_hac",
    "reference",
]


def load_timing_by_tag(artifacts_dir: Path) -> dict[str, dict]:
    """
    Lookup keys:
      - full tag (e.g. cascade_20k_frac300_C64)
      - force_frac string for 20k (e.g. '0.3' / '0.30') so C64 names still match
    """
    out: dict[str, dict] = {}

    def _store(tag: str, row: dict) -> None:
        if not tag:
            return
        info = {
            "real_s": (row.get("real_s") or "").strip(),
            "pct_hac": (row.get("pct_hac") or "").strip(),
        }
        # Prefer rows that actually have real_s over empty OOM placeholders.
        prev = out.get(tag)
        if prev and prev.get("real_s") and not info["real_s"]:
            return
        out[tag] = info
        frac = (row.get("force_frac") or "").strip()
        size = (row.get("size") or "").strip()
        if frac and size:
            out[f"{size}:{frac}"] = info
            try:
                out[f"{size}:{float(frac):g}"] = info
            except ValueError:
                pass

    for csv_path in list(artifacts_dir.glob("cascade_frac_sweep_results.csv")) + list(
        artifacts_dir.glob("cascade_missing_rerun_results.csv")
    ):
        with csv_path.open() as f:
            for row in csv.DictReader(f):
                _store(row.get("tag", ""), row)
    return out


def timing_for_fastq(fq_name: str, timing: dict[str, dict]) -> dict:
    tag = fq_name.replace("output_", "").replace(".fastq", "")
    if tag in timing and timing[tag].get("real_s"):
        return timing[tag]
    meta = parse_meta(fq_name)
    size = meta.get("size", "")
    frac = meta.get("force_frac", "")
    for key in (
        tag,
        f"{size}:{frac}",
        f"{size}:{float(frac):g}" if frac else "",
        f"cascade_{size}_{frac_tag(float(frac))}_C64" if frac and size else "",
        f"cascade_{size}_{frac_tag(float(frac))}_r01" if frac and size else "",
    ):
        if key and key in timing:
            return timing[key]
    return {}


def crunch_local_fastqs(
    fastqs: list[Path],
    *,
    timing: dict[str, dict] | None = None,
    keep_paf: bool = False,
    keep_fastq: bool = False,
) -> list[dict]:
    if not REFERENCE_FASTA.is_file():
        die(f"reference not found: {REFERENCE_FASTA}")
    timing = timing or {}
    LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
    target = ensure_mmi(REFERENCE_FASTA, REFERENCE_MMI)
    rows: list[dict] = []

    # First pass: score everything
    banner(f"minimap2  (-t {MINIMAP2_THREADS})", Col.MAGENTA)
    for i, fq in enumerate(fastqs, 1):
        print(c(f"[{i}/{len(fastqs)}] {fq.name}", Col.CYAN, Col.BOLD))
        meta = parse_meta(fq.name)
        n_reads = count_fastq_reads(fq)
        if meta.get("size") == "20k" and n_reads != 20000:
            print(
                c(
                    f"  SKIP {fq.name}: n_reads={n_reads} (want 20000; likely truncated OOM run)",
                    Col.YELLOW,
                    Col.BOLD,
                )
            )
            if not keep_fastq:
                fq.unlink(missing_ok=True)
            continue
        paf = fq.with_suffix(".paf")
        med, n_aln = run_minimap2(fq, target, paf)
        med_s = f"{med:.6f}" if med is not None else "NA"
        map_rate = (100.0 * n_aln / n_reads) if n_reads else 0.0
        map_s = f"{map_rate:.1f}%"
        tinfo = timing_for_fastq(fq.name, timing)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fastq": fq.name,
            "mode": meta.get("mode", ""),
            "size": meta.get("size", ""),
            "force_frac": meta.get("force_frac", ""),
            "rep": meta.get("rep", ""),
            "n_reads": str(n_reads),
            "n_alignments": str(n_aln),
            "map_rate": map_s,
            "accuracy_median": med_s,
            "delta_vs_fast": "",
            "delta_vs_hac": "",
            "real_s": tinfo.get("real_s", ""),
            "pct_hac": tinfo.get("pct_hac", ""),
            "reference": str(REFERENCE_FASTA),
            "_med": med,
        }
        rows.append(row)
        print(c(f"  → median={med_s}  map={map_s}  real_s={row['real_s'] or '-'}", Col.GREEN if med else Col.YELLOW))
        if not keep_paf:
            paf.unlink(missing_ok=True)
        if not keep_fastq:
            fq.unlink(missing_ok=True)

    # Overlap v1.2 measured baselines per size (override if this batch includes FAST/HAC).
    base_fast = {
        size: BASELINE_MEDIAN.get(("fast", size), BASELINE_MEDIAN[("fast", "1k")])
        for size in {r.get("size") or "1k" for r in rows}
    }
    base_hac = {
        size: BASELINE_MEDIAN.get(("hac", size), BASELINE_MEDIAN[("hac", "1k")])
        for size in {r.get("size") or "1k" for r in rows}
    }
    for r in rows:
        size = r.get("size") or "1k"
        if r.get("mode") == "fast" and r.get("_med") is not None:
            base_fast[size] = r["_med"]
        if r.get("mode") == "hac" and r.get("_med") is not None:
            base_hac[size] = r["_med"]

    for r in rows:
        med = r.pop("_med", None)
        size = r.get("size") or "1k"
        if med is None:
            r["delta_vs_fast"] = "NA"
            r["delta_vs_hac"] = "NA"
        else:
            r["delta_vs_fast"] = f"{med - base_fast[size]:+.6f}"
            r["delta_vs_hac"] = f"{med - base_hac[size]:+.6f}"

    return rows


def write_accuracy_csv(rows: list[dict], out_dir: Path, csv_name: str) -> Path:
    """Append new FASTQ rows; never rewrite/overwrite existing rows."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / csv_name
    existing: set[str] = set()
    if path.is_file():
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row.get("fastq"):
                    existing.add(row["fastq"])
    new_rows = [r for r in rows if r.get("fastq") not in existing]
    skipped = len(rows) - len(new_rows)
    if skipped:
        print(c(f"Accuracy CSV: skipping {skipped} row(s) already in {path.name}", Col.DIM))
    if not new_rows:
        print(c(f"Accuracy CSV: no new rows to add → {path}", Col.DIM))
        return path
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ACCURACY_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    print(c(f"appended {len(new_rows)} row(s) → {path}", Col.GREEN))
    return path


def print_curve(rows: list[dict]) -> None:
    """Per size: average cascade reps by force_frac; show FAST/HAC anchors."""
    banner("speed / accuracy curve (look for flatten)", Col.GREEN)

    by_size: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_size[r.get("size") or "?"].append(r)

    for size in sorted(by_size.keys()):
        group = by_size[size]
        print(c(f"--- {size} ---", Col.BOLD))
        # overlap v1.2 measured anchors
        fbase = BASELINE_MEDIAN.get(("fast", size))
        hbase = BASELINE_MEDIAN.get(("hac", size))
        if fbase is not None:
            print(c(f"  FAST v1.2 baseline median = {fbase:.6f}", Col.DIM))
        if hbase is not None:
            print(c(f"  HAC  v1.2 baseline median = {hbase:.6f}", Col.DIM))
        for mode in ("fast", "hac"):
            for r in group:
                if r["mode"] == mode:
                    print(
                        f"  {mode:8}  median={r['accuracy_median']:>10}  "
                        f"real_s={r['real_s'] or '-':>8}  map={r['map_rate']}"
                    )
                    break

        # cascade: average median / real_s across reps per frac
        casc = [r for r in group if r["mode"] == "cascade"]
        fracs: dict[str, list[dict]] = defaultdict(list)
        for r in casc:
            fracs[r["force_frac"]].append(r)

        print(
            f"  {'frac':>6}  {'median':>10}  {'Δfast':>10}  {'Δhac':>10}  "
            f"{'real_s':>8}  {'%HAC':>6}  {'map%':>7}  n"
        )
        prev_med: float | None = None
        for frac in sorted(fracs.keys(), key=lambda x: float(x) if x else 0.0):
            rs = fracs[frac]
            meds = [float(r["accuracy_median"]) for r in rs if r["accuracy_median"] != "NA"]
            reals = [float(r["real_s"]) for r in rs if r["real_s"] not in ("",)]
            dfast = [float(r["delta_vs_fast"]) for r in rs if r["delta_vs_fast"] not in ("", "NA")]
            dhac = [float(r["delta_vs_hac"]) for r in rs if r["delta_vs_hac"] not in ("", "NA")]
            pcts = [float(r["pct_hac"]) for r in rs if r["pct_hac"] not in ("",)]
            maps = [float(r["map_rate"].rstrip("%")) for r in rs if r["map_rate"]]
            if not meds:
                continue
            med = sum(meds) / len(meds)
            real = (sum(reals) / len(reals)) if reals else float("nan")
            df = (sum(dfast) / len(dfast)) if dfast else float("nan")
            dh = (sum(dhac) / len(dhac)) if dhac else float("nan")
            pct = (sum(pcts) / len(pcts)) if pcts else float("nan")
            mp = (sum(maps) / len(maps)) if maps else float("nan")
            dmed = "" if prev_med is None else f"  (Δmed vs prev {med - prev_med:+.4f})"
            prev_med = med
            better_fast = df > 0.001 if df == df else False
            style = Col.GREEN if better_fast else Col.YELLOW
            print(
                c(
                    f"  {frac:>6}  {med:10.6f}  {df:+10.6f}  {dh:+10.6f}  "
                    f"{real:8.1f}  {pct:5.1f}%  {mp:6.1f}%  n={len(rs)}{dmed}",
                    style,
                )
            )
        print()
        print(
            c(
                "Goal: Δfast > 0 and real_s << HAC. Flatten = small Δmed vs prev "
                "while real_s still climbing.",
                Col.DIM,
            )
        )
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--accuracy-crunch", action="store_true")
    ap.add_argument("--keep-fastq", action="store_true")
    ap.add_argument("--keep-paf", action="store_true")
    ap.add_argument(
        "--missing-20k",
        action="store_true",
        default=True,
        help="DEFAULT: only 20k fracs 0.15/0.20/0.30/0.40/0.50 (incomplete in first sweep)",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="pull/score entire force-frac sweep instead of missing-20k only",
    )
    ap.add_argument("--stamp", metavar="UTCSTAMP", help="only one sweep dir (with --all)")
    ap.add_argument("--glob", default=REMOTE_FASTQ_GLOB, help="remote glob (with --all)")
    ap.add_argument("--no-artifacts", action="store_true")
    args = ap.parse_args()
    missing_only = not args.all

    csv_name = ACCURACY_CSV_MISSING if missing_only else ACCURACY_CSV_NAME

    if args.accuracy_crunch:
        fastqs = sorted(LOCAL_WORK_DIR.glob("output_*.fastq"))
        if missing_only:
            fastqs = [fq for fq in fastqs if is_missing_20k_target(fq.name)]
        if not fastqs:
            die(f"no matching FASTQs in {LOCAL_WORK_DIR}")
        timing = load_timing_by_tag(SWEEP_ARTIFACTS_DIR)
        rows = crunch_local_fastqs(
            fastqs, timing=timing, keep_paf=args.keep_paf, keep_fastq=args.keep_fastq
        )
        write_accuracy_csv(rows, ACCURACY_DIR, csv_name)
        print_curve(rows)
        return

    mm2 = Path(MINIMAP2)
    need_mm = not args.dry_run and not args.fetch_only
    if need_mm and not (mm2.is_file() or shutil.which(MINIMAP2)):
        die(f"minimap2 not found: {MINIMAP2}")

    atexit.register(close_ssh_master)
    print(c(f"Jetson: {JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}", Col.BOLD))
    print(c(f"Work:   {LOCAL_WORK_DIR}", Col.DIM))
    print(c(f"Acc:    {ACCURACY_DIR}/{csv_name}", Col.DIM))
    if missing_only:
        print(c(
            f"Mode:   missing-20k only  fracs={list(MISSING_20K_FRACS)}  "
            f"(baselines FAST20k={BASELINE_MEDIAN[('fast','20k')]}  "
            f"HAC20k={BASELINE_MEDIAN[('hac','20k')]})",
            Col.BOLD,
        ))
    else:
        print(c("Mode:   full sweep (--all)", Col.BOLD))

    if missing_only:
        remotes = list_missing_20k_remotes()
        if not remotes:
            die(
                "no missing-20k FASTQs on Jetson yet.\n"
                "Re-run on Jetson into docs-riley/cascade-20k-missing-rerun/ "
                "(see script docstring), then retry."
            )
    else:
        remotes = filter_by_stamp(remote_list_glob(args.glob), args.stamp)
        if not remotes:
            die(f"no remote FASTQs matched {args.glob!r}" + (f" stamp={args.stamp}" if args.stamp else ""))

    print(f"Found {len(remotes)} remote FASTQ(s):")
    for r in remotes:
        print(f"  {r}")

    artifact_rels: list[str] = []
    if not args.no_artifacts and not missing_only:
        artifact_rels = filter_by_stamp(remote_list_glob(REMOTE_TSV_GLOB), args.stamp)
        artifact_rels += filter_by_stamp(remote_list_glob(REMOTE_RESULTS_GLOB), args.stamp)
    elif not args.no_artifacts and missing_only:
        # timing CSV for C64 re-runs + matching TSVs
        for cand in (
            "docs-riley/cascade-20k-missing-rerun/cascade_missing_rerun_results.csv",
        ):
            if remote_mtime_exists(cand):
                artifact_rels.append(cand)
        for rel in remotes:
            name = Path(rel).name
            meta = parse_meta(name)
            alt = str(Path(rel).parent / f"cascade_20k_{frac_tag(float(meta['force_frac']))}_C64.tsv")
            for cand in (alt, str(Path(rel).with_suffix(".tsv"))):
                if remote_mtime_exists(cand):
                    artifact_rels.append(cand)
                    break

    if args.dry_run:
        return

    banner("rsync FASTQs (add-only)", Col.YELLOW)
    LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
    wanted_fastq_names = [Path(r).name for r in remotes]
    remotes_new = filter_add_only(LOCAL_WORK_DIR, remotes, "FASTQs")
    rsync_pull(remotes_new, LOCAL_WORK_DIR)
    local_fastqs = [
        LOCAL_WORK_DIR / name
        for name in wanted_fastq_names
        if (LOCAL_WORK_DIR / name).is_file()
    ]
    if not local_fastqs:
        die("no local FASTQs available to score after add-only pull")

    if artifact_rels:
        banner(f"rsync artifacts → {SWEEP_ARTIFACTS_DIR} (add-only)", Col.YELLOW)
        artifact_rels = filter_add_only(SWEEP_ARTIFACTS_DIR, artifact_rels, "artifacts")
        rsync_pull(artifact_rels, SWEEP_ARTIFACTS_DIR)

    if args.fetch_only:
        print(c(f"Fetched {len(local_fastqs)} FASTQs. Next: --accuracy-crunch", Col.GREEN))
        return

    timing = load_timing_by_tag(SWEEP_ARTIFACTS_DIR)
    rows = crunch_local_fastqs(
        local_fastqs, timing=timing, keep_paf=args.keep_paf, keep_fastq=args.keep_fastq
    )
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not RESULTS_CSV.exists()
    with RESULTS_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ACCURACY_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(rows)

    path = write_accuracy_csv(rows, ACCURACY_DIR, csv_name)
    print_curve(rows)
    print(c(f"Accuracy CSV: {path}", Col.GREEN, Col.BOLD))
    print(c("done.", Col.GREEN, Col.BOLD))


def remote_mtime_exists(rel_path: str) -> bool:
    remote_cmd = (
        f"bash -lc 'cd {JETSON_REPO} && "
        f"if [ -e \"{rel_path}\" ]; then echo yes; fi'"
    )
    proc = subprocess.run(
        ssh_base() + [remote_cmd],
        check=True,
        text=True,
        capture_output=True,
    )
    return "yes" in proc.stdout


if __name__ == "__main__":
    main()
