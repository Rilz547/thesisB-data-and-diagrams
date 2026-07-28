#!/usr/bin/env python3
"""
Load-imbalance accuracy pass (main PC: minimap2).

Hardwired for the load-imbalance Jetson re-runs:
  - Jetson 192.168.4.48, repo ~/slorado-riley-v2
  - Glob: output_*_overlap-li-*.fastq
  - Accuracy → overlapping/load-imbalance/accuracy/minimap_accuracy-load-imbalance.csv
  - Nsight / runner-summary fetch OFF by default
  - After each FASTQ is scored: delete that FASTQ and its .paf before the next

Run on your main PC (has minimap2), from the analysis directory that contains
overlapping/. Edit REFERENCE_FASTA / MINIMAP2 below if needed.

Examples
--------
  python3 fetch-and-minimap.py
  python3 fetch-and-minimap.py --fetch-only
  python3 fetch-and-minimap.py --accuracy-crunch
  python3 fetch-and-minimap.py --dry-run
  python3 fetch-and-minimap.py --keep-fastq --keep-paf   # override per-file cleanup
"""

from __future__ import annotations

import argparse
import atexit
import csv
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG — load-imbalance hardwired
# ---------------------------------------------------------------------------

JETSON_HOST = "192.168.4.48"
JETSON_USER = "riley"
JETSON_REPO = "~/slorado-riley-v2"

REMOTE_FASTQ_GLOB = "output_*_overlap-li-*.fastq"

LOCAL_WORK_DIR = Path.cwd() / "slorado-accuracy-tmp"

REFERENCE_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"
MINIMAP2 = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"
MINIMAP2_PRESET = "map-ont"
MINIMAP2_THREADS = 16
REFERENCE_MMI = LOCAL_WORK_DIR / f"{REFERENCE_FASTA.stem}.mmi"

EXPECTED_MEDIAN = {
    "fast": 0.940696,
    "hac": 0.976852,
}

RESULTS_CSV = LOCAL_WORK_DIR / "minimap_results.csv"

ACCURACY_DIR = Path.cwd() / "overlapping" / "load-imbalance" / "accuracy"
ACCURACY_CSV_NAME = "minimap_accuracy-load-imbalance.csv"

# Off for load-imbalance (no nsys / riley-runner artifacts for these sweeps).
FETCH_NSYS = False
NSYS_MATCH_WINDOW_SEC = 1500
NSIGHT_STATS_DIR = Path.cwd() / "overlapping" / "load-imbalance" / "nsight-stats"
AUTO_NSIGHT_CONVERT = False
RUNNER_SUMMARY_DIR = Path.cwd() / "overlapping" / "load-imbalance" / "runner-summaries"
FETCH_RUNNER_SUMMARIES = False

_SSH_CONTROL = f"/tmp/ssh-slorado-{JETSON_USER}@{JETSON_HOST}-%p"
_SSH_OPTS = [
    "-o", "ControlMaster=auto",
    "-o", f"ControlPath={_SSH_CONTROL}",
    "-o", "ControlPersist=120",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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


def run(cmd: list[str] | str, check: bool = True, shell: bool = False) -> subprocess.CompletedProcess:
    shown = cmd if shell else " ".join(cmd)
    print(c(f"+ {shown}", Col.DIM))
    return subprocess.run(cmd, check=check, shell=shell, text=True, capture_output=False)


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


def remote_list_fastqs(fastq_glob: str) -> list[str]:
    remote_cmd = (
        f"bash -lc 'cd {JETSON_REPO} && "
        f"compgen -G \"{fastq_glob}\" | sort || true'"
    )
    proc = subprocess.run(
        ssh_base() + [remote_cmd],
        check=True,
        text=True,
        capture_output=True,
    )
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def remote_mtime(rel_path: str) -> int | None:
    remote_cmd = (
        f"bash -lc 'cd {JETSON_REPO} && "
        f"if [ -e \"{rel_path}\" ]; then stat -c %Y \"{rel_path}\"; fi'"
    )
    proc = subprocess.run(
        ssh_base() + [remote_cmd],
        check=True,
        text=True,
        capture_output=True,
    )
    out = proc.stdout.strip()
    if not out:
        return None
    try:
        return int(out.splitlines()[-1])
    except ValueError:
        return None


def nsys_name_for_fastq(fastq_name: str) -> str | None:
    m = re.fullmatch(
        r"output_(fast|hac)_(\d+k)(_overlap)?(.*)\.fastq",
        Path(fastq_name).name,
    )
    if not m:
        return None
    model, size, overlap, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
    if overlap:
        return f"nsys_{model}_{size}_overlap{suffix}.nsys-rep"
    return f"nsys_{model}_{size}_base{suffix}.nsys-rep"


def append_suffixes_from_fastqs(fastq_rels: list[str]) -> list[str]:
    suffixes: set[str] = set()
    for rel in fastq_rels:
        m = re.fullmatch(
            r"output_(fast|hac)_(\d+k)(_overlap)?(.*)\.fastq",
            Path(rel).name,
        )
        if m:
            suffixes.add(m.group(4) or "")
    return sorted(suffixes)


def runner_summary_names(suffixes: list[str]) -> list[str]:
    names: list[str] = []
    for s in suffixes:
        names.extend(
            [
                f"riley-runner-output{s}.txt",
                f"riley-runner-timings{s}.csv",
                f"riley-runner-state{s}.json",
                f"riley-runner-fast-console{s}.txt",
                f"riley-runner-hac-console{s}.txt",
            ]
        )
    return names


def confirm_dest_writes(dest: Path, incoming_names: list[str], label: str) -> bool:
    dest = dest.expanduser().resolve()
    if not incoming_names:
        return True
    if not dest.exists():
        return True
    if not dest.is_dir():
        die(f"{label} path exists but is not a directory: {dest}")

    existing = {p.name for p in dest.iterdir() if p.is_file()}
    incoming = set(incoming_names)
    collisions = sorted(existing & incoming)
    others = sorted(existing - incoming)

    if others and not collisions:
        print(
            c(
                f"WARNING: {label} directory already has other files; "
                f"new files will be added alongside them in:\n  {dest}",
                Col.YELLOW,
            )
        )
        for n in others[:12]:
            print(c(f"  existing: {n}", Col.DIM))
        if len(others) > 12:
            print(c(f"  ... +{len(others) - 12} more", Col.DIM))
        return True

    if collisions:
        print(
            c(
                f"WARNING: {label} directory already has same-named file(s) "
                f"that will be overwritten:\n  {dest}",
                Col.YELLOW,
                Col.BOLD,
            )
        )
        for n in collisions:
            print(c(f"  overwrite: {n}", Col.YELLOW))
        if others:
            print(
                c(
                    f"  ({len(others)} other file(s) already in this directory will be left alone)",
                    Col.DIM,
                )
            )
        try:
            reply = input("Proceed and overwrite? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in {"y", "yes"}:
            print(c("Aborted.", Col.RED))
            return False
        return True

    return True


def nsys_sqlite_name(nsys_rep: str) -> str:
    name = Path(nsys_rep).name
    if name.endswith(".nsys-rep"):
        return name[: -len(".nsys-rep")] + ".sqlite"
    return str(Path(nsys_rep).with_suffix(".sqlite"))


def select_matching_nsys(
    fastq_rels: list[str],
    window_sec: int,
) -> list[tuple[str, str, int]]:
    matched: list[tuple[str, str, int]] = []
    for fq in fastq_rels:
        nsys = nsys_name_for_fastq(Path(fq).name)
        if nsys is None:
            print(c(f"  nsys: skip {Path(fq).name} (no naming map)", Col.DIM))
            continue
        t_fq = remote_mtime(fq)
        t_ns = remote_mtime(nsys)
        if t_fq is None:
            print(c(f"  nsys: skip {nsys} (fastq mtime missing for {fq})", Col.YELLOW))
            continue
        if t_ns is None:
            print(c(f"  nsys: skip {nsys} (not on Jetson)", Col.DIM))
            continue
        dt = abs(t_ns - t_fq)
        if dt > window_sec:
            print(
                c(
                    f"  nsys: skip {nsys} "
                    f"(|Δt|={dt}s > {window_sec}s vs {Path(fq).name})",
                    Col.YELLOW,
                )
            )
            continue
        sqlite = nsys_sqlite_name(nsys)
        has_sqlite = remote_mtime(sqlite) is not None
        extra = f" + {sqlite}" if has_sqlite else " (no .sqlite on Jetson)"
        print(
            c(
                f"  nsys: match {nsys}{extra}  (|Δt|={dt}s vs {Path(fq).name})",
                Col.GREEN if has_sqlite else Col.YELLOW,
            )
        )
        matched.append((fq, nsys, dt))
    return matched


def rsync_pull(remote_rel_paths: list[str], dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    if not remote_rel_paths:
        return []
    sources = [f"{JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}/{rel}" for rel in remote_rel_paths]
    run(["rsync", "-avP", "-e", rsync_ssh_e(), *sources, str(dest) + "/"])
    local_files: list[Path] = []
    for rel in remote_rel_paths:
        local = dest / Path(rel).name
        if not local.exists():
            die(f"rsync did not create {local}")
        local_files.append(local)
    return local_files


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
    n = len(ratios)
    if not ratios:
        return None, 0
    ratios.sort()
    mid = len(ratios) // 2
    if len(ratios) % 2:
        return ratios[mid], n
    return 0.5 * (ratios[mid - 1] + ratios[mid]), n


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
        MINIMAP2,
        "-cx",
        MINIMAP2_PRESET,
        "-t",
        str(MINIMAP2_THREADS),
        "--secondary=no",
        str(target),
        str(fastq),
    ]
    print(c(f"+ {' '.join(cmd)} > {out_paf}", Col.DIM))
    with out_paf.open("w") as out:
        subprocess.run(cmd, check=True, stdout=out)
    print()
    return median_identity(out_paf)


def expected_for(fastq_name: str) -> float | None:
    lower = fastq_name.lower()
    if "1k" not in lower:
        return None
    if "hac" in lower:
        return EXPECTED_MEDIAN["hac"]
    if "fast" in lower:
        return EXPECTED_MEDIAN["fast"]
    return None


ACCURACY_CSV_FIELDS = [
    "timestamp",
    "fastq",
    "n_reads",
    "n_alignments",
    "map_rate",
    "accuracy_median",
    "expected_median",
    "delta_vs_expected",
    "nsys_pulled",
    "reference",
]


def append_result_row(row: dict) -> None:
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not RESULTS_CSV.exists()
    with RESULTS_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ACCURACY_CSV_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow(row)


def write_accuracy_dir_csvs(rows: list[dict], out_dir: Path) -> list[Path]:
    """Single combined CSV for load-imbalance (do not split by APPEND)."""
    if not rows:
        return []
    out_names = [ACCURACY_CSV_NAME]
    if not confirm_dest_writes(out_dir, out_names, "accuracy"):
        die("accuracy write cancelled by user")

    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ACCURACY_CSV_NAME
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ACCURACY_CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(c(f"  wrote {path} ({len(rows)} row(s))", Col.GREEN))
    return [path]


def crunch_local_fastqs(
    fastqs: list[Path],
    *,
    nsys_by_fastq: dict[str, str] | None = None,
    keep_paf: bool = False,
    keep_fastq: bool = False,
) -> list[dict]:
    """Run minimap2 on local FASTQs; delete each FASTQ + PAF after scoring (default)."""
    if not REFERENCE_FASTA.is_file():
        die(
            f"reference not found: {REFERENCE_FASTA}\n"
            "Set REFERENCE_FASTA to your hg38 (or other) FASTA."
        )
    nsys_by_fastq = nsys_by_fastq or {}
    LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
    target = ensure_mmi(REFERENCE_FASTA, REFERENCE_MMI)
    summary_rows: list[dict] = []

    banner(f"minimap2  (target: {target.name}, -t {MINIMAP2_THREADS})", Col.MAGENTA)
    for i, fq in enumerate(fastqs, 1):
        print(c(f"[{i}/{len(fastqs)}] aligning {fq.name} …", Col.CYAN, Col.BOLD))
        n_reads = count_fastq_reads(fq)
        paf = fq.with_suffix(".paf")
        med, n_aln = run_minimap2(fq, target, paf)
        med_s = f"{med:.6f}" if med is not None else "NA"
        map_rate = (100.0 * n_aln / n_reads) if n_reads else 0.0
        map_s = f"{map_rate:.1f}%"
        if med is None:
            print(
                c(
                    f"  → {fq.name}: median identity = NA  "
                    f"({n_aln}/{n_reads} mapped = {map_s})",
                    Col.YELLOW,
                    Col.BOLD,
                )
            )
        else:
            style = Col.GREEN if map_rate >= 50 else Col.YELLOW
            print(
                c(
                    f"  → {fq.name}: median identity = {med_s}  "
                    f"({n_aln}/{n_reads} mapped = {map_s})",
                    style,
                    Col.BOLD,
                )
            )
        print()
        exp = expected_for(fq.name)
        if exp is not None:
            exp_s = f"{exp:.6f}"
            delta_s = f"{med - exp:+.6f}" if med is not None else "NA"
        else:
            exp_s = "-"
            delta_s = "-"
        nsys_name = nsys_by_fastq.get(fq.name, "")
        if not nsys_name:
            guessed = nsys_name_for_fastq(fq.name)
            if guessed and (LOCAL_WORK_DIR / guessed).is_file():
                nsys_name = guessed
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fastq": fq.name,
            "n_reads": str(n_reads),
            "n_alignments": str(n_aln),
            "map_rate": map_s,
            "accuracy_median": med_s,
            "expected_median": exp_s,
            "delta_vs_expected": delta_s,
            "nsys_pulled": nsys_name or "",
            "reference": str(REFERENCE_FASTA),
        }
        append_result_row(row)
        summary_rows.append(row)

        # Free disk before the next FASTQ (PAF + FASTQ).
        if not keep_paf:
            paf.unlink(missing_ok=True)
            print(c(f"  removed {paf.name}", Col.DIM))
        if not keep_fastq:
            fq.unlink(missing_ok=True)
            print(c(f"  removed {fq.name}", Col.DIM))

    return summary_rows


def accuracy_crunch(*, keep_paf: bool = False, keep_fastq: bool = False) -> None:
    mm2 = Path(MINIMAP2)
    if not (mm2.is_file() or shutil.which(MINIMAP2)):
        die(f"minimap2 not found ({MINIMAP2!r}); install it or set MINIMAP2 in the script")

    fastqs = sorted(LOCAL_WORK_DIR.glob("output_*_overlap-li-*.fastq"))
    if not fastqs:
        # fallback: any output_*.fastq already in work dir
        fastqs = sorted(LOCAL_WORK_DIR.glob("output_*.fastq"))
    if not fastqs:
        die(f"no load-imbalance FASTQs in {LOCAL_WORK_DIR} (pull first or use --keep-fastq)")

    banner("accuracy crunch (local temp FASTQs)", Col.MAGENTA)
    print(c(f"Work:     {LOCAL_WORK_DIR}", Col.DIM))
    print(c(f"Accuracy: {ACCURACY_DIR}", Col.DIM))
    print(c(f"Ref:      {REFERENCE_FASTA}", Col.DIM))
    print(f"Found {len(fastqs)} local FASTQ(s):")
    for fq in fastqs:
        print(f"  {fq.name}")

    summary_rows = crunch_local_fastqs(
        fastqs, keep_paf=keep_paf, keep_fastq=keep_fastq
    )
    print_summary(summary_rows)

    banner(f"write accuracy CSVs → {ACCURACY_DIR}", Col.GREEN)
    written = write_accuracy_dir_csvs(summary_rows, ACCURACY_DIR)
    print()
    for p in written:
        print(c(f"Accuracy CSV: {p}", Col.GREEN, Col.BOLD))
    print(c(f"Scratch log:  {RESULTS_CSV}", Col.DIM))
    print(c("done.", Col.GREEN, Col.BOLD))


def find_nsys() -> str:
    nsys = shutil.which("nsys")
    if nsys:
        return nsys
    candidates = [
        Path("/opt/nvidia/nsight-systems/2026.3.1/target-linux-x64/nsys"),
        Path("/opt/nvidia/nsight-systems/2026.3.1/host-linux-x64/nsys"),
        Path("/usr/local/bin/nsys"),
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    die("nsys not found on PATH; install Nsight Systems or fix /usr/local/bin/nsys")


def nsight_convert(
    out_dir: Path,
    work_dir: Path = LOCAL_WORK_DIR,
    reps: list[Path] | None = None,
    *,
    require_reps: bool = True,
) -> None:
    nsys = find_nsys()
    selected = reps is not None
    if reps is None:
        reps = sorted(work_dir.glob("*.nsys-rep"))
    else:
        reps = sorted(reps)
    if not reps:
        if require_reps:
            die(f"no .nsys-rep files in {work_dir}")
        print(c("  (no .nsys-rep to convert)", Col.DIM))
        return

    out_dir = out_dir.expanduser().resolve()
    out_names = [
        (rep.name[: -len(".nsys-rep")] if rep.name.endswith(".nsys-rep") else rep.stem)
        + "_stats.txt"
        for rep in reps
    ]
    if not confirm_dest_writes(out_dir, out_names, "nsight stats"):
        die("nsight stats convert cancelled by user")

    out_dir.mkdir(parents=True, exist_ok=True)

    banner(f"nsight convert → {out_dir}", Col.GREEN)
    print(c(f"nsys:  {nsys}", Col.DIM))
    print(c(f"src:   {work_dir if not selected else '(selected .nsys-rep files)'}", Col.DIM))
    print(f"Found {len(reps)} report(s)\n")

    for i, rep in enumerate(reps, 1):
        stem = rep.name[: -len(".nsys-rep")] if rep.name.endswith(".nsys-rep") else rep.stem
        out = out_dir / f"{stem}_stats.txt"
        print(c(f"[{i}/{len(reps)}] {rep.name} → {out.name}", Col.CYAN, Col.BOLD))
        print(c(f"+ {nsys} stats --force-export=true {rep} > {out}", Col.DIM))
        with out.open("w") as f:
            proc = subprocess.run(
                [nsys, "stats", "--force-export=true", str(rep)],
                stdout=f,
                stderr=subprocess.PIPE,
                text=True,
            )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
            print(c(f"  FAILED: {err}", Col.RED, Col.BOLD))
            continue
        print(c(f"  wrote {out} ({out.stat().st_size} bytes)", Col.GREEN))
        print()

    print(c(f"done. stats in {out_dir}", Col.GREEN, Col.BOLD))


def print_summary(rows: list[dict]) -> None:
    banner("summary", Col.GREEN)
    fq_w = max(36, max((len(r["fastq"]) for r in rows), default=36))
    ns_w = max(28, max((len(r.get("nsys_pulled", "") or "") for r in rows), default=28))
    hdr = (
        f"{'fastq':<{fq_w}} {'reads':>7} {'alns':>6} {'map%':>7} "
        f"{'median':>10} {'expected':>10} {'delta':>10} {'nsys':<{ns_w}}"
    )
    print(c(hdr, Col.BOLD))
    print(c("-" * len(hdr), Col.DIM))
    for r in rows:
        line = (
            f"{r['fastq']:<{fq_w}} {r['n_reads']:>7} {r['n_alignments']:>6} "
            f"{r['map_rate']:>7} {r['accuracy_median']:>10} "
            f"{r['expected_median']:>10} {r['delta_vs_expected']:>10} "
            f"{(r.get('nsys_pulled') or ''):<{ns_w}}"
        )
        map_ok = float(r["map_rate"].rstrip("%") or 0) >= 50
        delta = r["delta_vs_expected"]
        med_ok = r["accuracy_median"] != "NA" and delta not in ("", "NA", "-")
        if med_ok:
            try:
                med_ok = abs(float(delta)) < 0.01
            except ValueError:
                med_ok = False
        style = Col.GREEN if map_ok and (med_ok or delta == "-") else Col.YELLOW
        print(c(line, style))
    print()
    print(
        c(
            "Expected (reads_1k.blow5 / hg38 only):  FAST = 0.940696   HAC = 0.976852   (20k = —)",
            Col.DIM,
        )
    )
    low = [r for r in rows if float(r["map_rate"].rstrip("%") or 0) < 50]
    if low:
        print(
            c(
                "Low map rate → median is unreliable (tiny/spurious alignment set). "
                "Check Jetson FASTQ / basecaller output.",
                Col.YELLOW,
            )
        )
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list remote FASTQs only")
    ap.add_argument(
        "--fetch-only",
        action="store_true",
        help="rsync matched FASTQs into the work dir only (no minimap)",
    )
    ap.add_argument(
        "--keep-fastq",
        action="store_true",
        help="keep each FASTQ after scoring (default: delete immediately after each)",
    )
    ap.add_argument(
        "--keep-paf",
        action="store_true",
        help="keep each .paf after scoring (default: delete immediately after each)",
    )
    ap.add_argument(
        "--no-nsys",
        action="store_true",
        help="skip nsys (already off by default for load-imbalance)",
    )
    ap.add_argument(
        "--no-runner-summaries",
        action="store_true",
        help="skip runner summaries (already off by default)",
    )
    ap.add_argument(
        "--no-nsight-stats",
        action="store_true",
        help="skip nsight stats convert",
    )
    ap.add_argument(
        "--accuracy-crunch",
        action="store_true",
        help=f"skip Jetson/rsync; minimap2 FASTQs already in {LOCAL_WORK_DIR.name}/",
    )
    ap.add_argument(
        "--nsight-convert",
        metavar="DIRECTORY",
        help=f"offline `nsys stats` on .nsys-rep in {LOCAL_WORK_DIR.name}/",
    )
    ap.add_argument(
        "--nsys-window",
        type=int,
        default=NSYS_MATCH_WINDOW_SEC,
        metavar="SEC",
        help=f"nsys mtime window (default {NSYS_MATCH_WINDOW_SEC})",
    )
    ap.add_argument(
        "-f",
        "--file",
        action="append",
        dest="files",
        metavar="NAME",
        help="specific remote FASTQ (repo-relative); repeatable. Skips --glob.",
    )
    ap.add_argument(
        "--glob",
        default=REMOTE_FASTQ_GLOB,
        help=f"remote shell glob (default: {REMOTE_FASTQ_GLOB})",
    )
    args = ap.parse_args()

    if args.nsight_convert:
        nsight_convert(Path(args.nsight_convert))
        return

    if args.accuracy_crunch:
        accuracy_crunch(keep_paf=args.keep_paf, keep_fastq=args.keep_fastq)
        return

    mm2 = Path(MINIMAP2)
    need_minimap = not args.dry_run and not args.fetch_only
    if need_minimap and not (mm2.is_file() or shutil.which(MINIMAP2)):
        die(f"minimap2 not found ({MINIMAP2!r}); install it or set MINIMAP2 in the script")

    atexit.register(close_ssh_master)

    print(c(f"Jetson: {JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}", Col.BOLD))
    print(c(f"Work:   {LOCAL_WORK_DIR}", Col.DIM))
    print(c(f"Ref:    {REFERENCE_FASTA}", Col.DIM))
    print(c(f"Acc:    {ACCURACY_DIR} / {ACCURACY_CSV_NAME}", Col.DIM))
    print(c("Nsight: OFF (load-imbalance)", Col.DIM))
    if args.files:
        remotes = args.files
        print(f"Files:  {', '.join(remotes)}")
    else:
        print(f"Glob:   {args.glob}")
        remotes = remote_list_fastqs(args.glob)
        if not remotes:
            die(f"no remote files matched {args.glob!r}")

    print(f"Found {len(remotes)} remote FASTQ(s):")
    for r in remotes:
        print(f"  {r}")

    if args.fetch_only:
        if args.dry_run:
            print(c("(dry-run: would rsync the FASTQs above into work dir)", Col.DIM))
            return
        banner("rsync FASTQs only (--fetch-only)", Col.YELLOW)
        LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
        local_fastqs = rsync_pull(remotes, LOCAL_WORK_DIR)
        banner("fetch-only complete", Col.GREEN)
        print(c(f"FASTQs in {LOCAL_WORK_DIR}:", Col.BOLD))
        for fq in local_fastqs:
            print(f"  {fq.name}")
        print(c("Next: python3 fetch-and-minimap.py --accuracy-crunch", Col.DIM))
        print(c("done.", Col.GREEN, Col.BOLD))
        return

    fetch_nsys = FETCH_NSYS and not args.no_nsys
    nsys_matches: list[tuple[str, str, int]] = []
    if fetch_nsys:
        banner(f"nsys match check (window ±{args.nsys_window}s)", Col.CYAN)
        nsys_matches = select_matching_nsys(remotes, args.nsys_window)
        if not nsys_matches:
            print(c("  (no matching .nsys-rep files)", Col.DIM))

    fetch_summaries = FETCH_RUNNER_SUMMARIES and not args.no_runner_summaries
    runner_rels: list[str] = []
    if fetch_summaries:
        banner("runner summary / console match", Col.CYAN)
        suffixes = append_suffixes_from_fastqs(remotes)
        wanted = runner_summary_names(suffixes)
        for name in wanted:
            if remote_mtime(name) is None:
                print(c(f"  skip {name} (not on Jetson)", Col.DIM))
                continue
            print(c(f"  match {name}", Col.GREEN))
            runner_rels.append(name)
        if not runner_rels:
            print(c("  (no riley-runner-* files found)", Col.DIM))

    if args.dry_run:
        return

    if not REFERENCE_FASTA.is_file():
        die(
            f"reference not found: {REFERENCE_FASTA}\n"
            "Set REFERENCE_FASTA to your hg38 (or other) FASTA."
        )

    banner("rsync FASTQs (password once if needed)", Col.YELLOW)
    LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
    local_fastqs = rsync_pull(remotes, LOCAL_WORK_DIR)

    local_nsys: list[Path] = []
    nsys_by_fastq: dict[str, str] = {}
    if nsys_matches:
        banner("rsync matching nsys reports", Col.YELLOW)
        nsys_rels = [ns for _, ns, _ in nsys_matches]
        local_nsys = rsync_pull(nsys_rels, LOCAL_WORK_DIR)
        for fq, ns, _ in nsys_matches:
            nsys_by_fastq[Path(fq).name] = ns

    if runner_rels:
        banner(f"rsync runner summaries → {RUNNER_SUMMARY_DIR}", Col.YELLOW)
        if not confirm_dest_writes(RUNNER_SUMMARY_DIR, [Path(r).name for r in runner_rels], "runner summary"):
            die("runner summary pull cancelled by user")
        RUNNER_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
        rsync_pull(runner_rels, RUNNER_SUMMARY_DIR)

    summary_rows = crunch_local_fastqs(
        local_fastqs,
        nsys_by_fastq=nsys_by_fastq,
        keep_paf=args.keep_paf,
        keep_fastq=args.keep_fastq,
    )
    print_summary(summary_rows)

    banner(f"write accuracy CSVs → {ACCURACY_DIR}", Col.GREEN)
    written_acc = write_accuracy_dir_csvs(summary_rows, ACCURACY_DIR)

    do_stats = (
        AUTO_NSIGHT_CONVERT
        and not args.no_nsys
        and not args.no_nsight_stats
        and bool(local_nsys)
    )
    if do_stats:
        nsight_convert(NSIGHT_STATS_DIR, reps=local_nsys, require_reps=False)

    print()
    for p in written_acc:
        print(c(f"Accuracy CSV: {p}", Col.GREEN, Col.BOLD))
    print(c(f"Scratch log:  {RESULTS_CSV}", Col.DIM))
    print(c("done.", Col.GREEN, Col.BOLD))


if __name__ == "__main__":
    main()
