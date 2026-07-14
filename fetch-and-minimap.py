#!/usr/bin/env python3
"""
Run on your main PC (has minimap2), not the Jetson.

  1. rsync matching FASTQs from the Jetson repo
  2. optionally rsync matching .nsys-rep + sibling .sqlite (mtime close to the FASTQ)
  3. run minimap2 against a reference for each FASTQ
  4. delete the local FASTQs (nsys reports + sqlite are kept)

Edit the CONFIG block, then:

  python3 fetch-and-minimap.py
  python3 fetch-and-minimap.py --dry-run
  python3 fetch-and-minimap.py --file output_fast_1k.fastq
  python3 fetch-and-minimap.py -f a.fastq -f b.fastq
  python3 fetch-and-minimap.py --keep-paf
  python3 fetch-and-minimap.py --no-nsys
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
# CONFIG — edit for your machines
# ---------------------------------------------------------------------------

JETSON_HOST = "192.168.4.36"  # hostname or IP (must work with ssh)
JETSON_USER = "riley"
JETSON_REPO = "~/slorado-riley-v2"

# Glob relative to JETSON_REPO (shell glob on the remote).
REMOTE_FASTQ_GLOB = "output_*.fastq"

# Local scratch for pulled FASTQs / nsys (cwd relative).
LOCAL_WORK_DIR = Path.cwd() / "slorado-accuracy-tmp"

# Reference FASTA on the *main PC* (use a real genome for meaningful accuracy).
REFERENCE_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"

MINIMAP2 = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"
MINIMAP2_PRESET = "map-ont"
MINIMAP2_THREADS = 16

REFERENCE_MMI = LOCAL_WORK_DIR / f"{REFERENCE_FASTA.stem}.mmi"

EXPECTED_MEDIAN = {
    # reads_1k.blow5 only — no published expected for 20k
    "fast": 0.940696,  # FAST v5.0.0
    "hac": 0.976852,  # HAC v5.0.0
}

RESULTS_CSV = LOCAL_WORK_DIR / "minimap_results.csv"

# Pull nsys_*.nsys-rep alongside FASTQs when remote mtimes are close enough.
# nsight_runner.sh writes e.g. output_fast_1k.fastq + nsys_fast_1k_base.nsys-rep
# in the same run, so mtimes are typically within seconds–minutes.
FETCH_NSYS = True
NSYS_MATCH_WINDOW_SEC = 1300  # |mtime(nsys) - mtime(fastq)| must be ≤ this

_SSH_CONTROL = f"/tmp/ssh-slorado-{JETSON_USER}@{JETSON_HOST}-%p"
_SSH_OPTS = [
    "-o",
    "ControlMaster=auto",
    "-o",
    f"ControlPath={_SSH_CONTROL}",
    "-o",
    "ControlPersist=120",
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
    """Epoch mtime of repo-relative file on Jetson, or None if missing."""
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
    """Map output_*.fastq → nsys_*.nsys-rep per nsight_runner.sh naming."""
    # output_fast_1k_overlap.fastq → nsys_fast_1k_overlap.nsys-rep
    # output_fast_1k.fastq         → nsys_fast_1k_base.nsys-rep
    m = re.fullmatch(
        r"output_(fast|hac)_(\d+k)(_overlap)?\.fastq",
        Path(fastq_name).name,
    )
    if not m:
        return None
    model, size, overlap = m.group(1), m.group(2), m.group(3)
    if overlap:
        return f"nsys_{model}_{size}_overlap.nsys-rep"
    return f"nsys_{model}_{size}_base.nsys-rep"


def nsys_sqlite_name(nsys_rep: str) -> str:
    """Sibling DB for CLI (nsys stats / queries): foo.nsys-rep → foo.sqlite."""
    name = Path(nsys_rep).name
    if name.endswith(".nsys-rep"):
        return name[: -len(".nsys-rep")] + ".sqlite"
    return str(Path(nsys_rep).with_suffix(".sqlite"))


def select_matching_nsys(
    fastq_rels: list[str],
    window_sec: int,
) -> list[tuple[str, str, int]]:
    """Return [(fastq_rel, nsys_rel, dt_sec), ...] for time-matched reports."""
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
    """One rsync for all files (single auth via ControlMaster). Overwrites locals."""
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
    """Expected median for 1k PGXXXX runs only; 20k (and others) → None."""
    lower = fastq_name.lower()
    if "1k" not in lower:
        return None
    if "hac" in lower:
        return EXPECTED_MEDIAN["hac"]
    if "fast" in lower:
        return EXPECTED_MEDIAN["fast"]
    return None


def append_result_row(row: dict) -> None:
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not RESULTS_CSV.exists()
    with RESULTS_CSV.open("a", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
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
            ],
        )
        if write_header:
            w.writeheader()
        w.writerow(row)


def print_summary(rows: list[dict]) -> None:
    banner("summary", Col.GREEN)
    hdr = (
        f"{'fastq':<36} {'reads':>7} {'alns':>6} {'map%':>7} "
        f"{'median':>10} {'expected':>10} {'delta':>10} {'nsys':>6}"
    )
    print(c(hdr, Col.BOLD))
    print(c("-" * len(hdr), Col.DIM))
    for r in rows:
        line = (
            f"{r['fastq']:<36} {r['n_reads']:>7} {r['n_alignments']:>6} "
            f"{r['map_rate']:>7} {r['accuracy_median']:>10} "
            f"{r['expected_median']:>10} {r['delta_vs_expected']:>10} "
            f"{r.get('nsys_pulled', ''):>6}"
        )
        map_ok = float(r["map_rate"].rstrip("%") or 0) >= 50
        delta = r["delta_vs_expected"]
        med_ok = r["accuracy_median"] != "NA" and delta not in ("", "NA", "-")
        if med_ok:
            try:
                med_ok = abs(float(delta)) < 0.01
            except ValueError:
                med_ok = False
        # 20k has no expected ("-"): colour by map rate only
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
    ap.add_argument("--dry-run", action="store_true", help="list remote FASTQs / matching nsys only")
    ap.add_argument("--keep-fastq", action="store_true", help="do not delete pulled FASTQs")
    ap.add_argument("--keep-paf", action="store_true", help="keep .paf alignment files")
    ap.add_argument("--no-nsys", action="store_true", help="do not fetch .nsys-rep files")
    ap.add_argument(
        "--nsys-window",
        type=int,
        default=NSYS_MATCH_WINDOW_SEC,
        metavar="SEC",
        help=f"max |mtime(nsys)-mtime(fastq)| to treat as same run (default {NSYS_MATCH_WINDOW_SEC})",
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
        help=f"remote glob under repo (default: {REMOTE_FASTQ_GLOB})",
    )
    args = ap.parse_args()

    mm2 = Path(MINIMAP2)
    if not args.dry_run and not (mm2.is_file() or shutil.which(MINIMAP2)):
        die(f"minimap2 not found ({MINIMAP2!r}); install it or set MINIMAP2 in the script")

    atexit.register(close_ssh_master)

    print(c(f"Jetson: {JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}", Col.BOLD))
    print(c(f"Work:   {LOCAL_WORK_DIR}", Col.DIM))
    print(c(f"Ref:    {REFERENCE_FASTA}", Col.DIM))
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

    fetch_nsys = FETCH_NSYS and not args.no_nsys
    nsys_matches: list[tuple[str, str, int]] = []
    if fetch_nsys:
        banner(f"nsys match check (window ±{args.nsys_window}s)", Col.CYAN)
        nsys_matches = select_matching_nsys(remotes, args.nsys_window)
        if not nsys_matches:
            print(c("  (no matching .nsys-rep files)", Col.DIM))

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
    local_nsys_sqlite: list[Path] = []
    nsys_by_fastq: dict[str, str] = {}
    if nsys_matches:
        banner("rsync matching nsys reports + sqlite (kept locally; overwrite OK)", Col.YELLOW)
        nsys_rels = [ns for _, ns, _ in nsys_matches]
        sqlite_rels = []
        for _, ns, _ in nsys_matches:
            sq = nsys_sqlite_name(ns)
            if remote_mtime(sq) is not None:
                sqlite_rels.append(sq)
            else:
                print(c(f"  warn: no remote {sq} (CLI stats may need it)", Col.YELLOW))
        local_nsys = rsync_pull(nsys_rels, LOCAL_WORK_DIR)
        if sqlite_rels:
            local_nsys_sqlite = rsync_pull(sqlite_rels, LOCAL_WORK_DIR)
        for fq, ns, _ in nsys_matches:
            nsys_by_fastq[Path(fq).name] = ns

    target = ensure_mmi(REFERENCE_FASTA, REFERENCE_MMI)

    summary_rows: list[dict] = []
    banner(f"minimap2  (target: {target.name}, -t {MINIMAP2_THREADS})", Col.MAGENTA)
    for i, fq in enumerate(local_fastqs, 1):
        print(c(f"[{i}/{len(local_fastqs)}] aligning {fq.name} …", Col.CYAN, Col.BOLD))
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
        if not args.keep_paf:
            paf.unlink(missing_ok=True)

    print_summary(summary_rows)

    if not args.keep_fastq:
        banner("deleting local FASTQs (nsys + sqlite kept)", Col.YELLOW)
        for fq in local_fastqs:
            fq.unlink(missing_ok=True)
            print(f"  removed {fq}")

    if local_nsys:
        banner("Nsight Systems (open on this PC)", Col.GREEN)
        print("GUI:")
        for p in local_nsys:
            print(c(f"  nsys-ui {p}", Col.BOLD))
        print(c("  (or File → Open in the Nsight Systems app)", Col.DIM))
        if local_nsys_sqlite:
            print("CLI (needs sibling .sqlite):")
            for p in local_nsys_sqlite:
                # nsys stats typically takes the .nsys-rep; sqlite must sit beside it
                rep = p.with_name(p.name[: -len(".sqlite")] + ".nsys-rep")
                print(c(f"  nsys stats {rep}", Col.BOLD))
        else:
            print(c("  (no .sqlite pulled — CLI export/stats may fail)", Col.YELLOW))
        print(c(f"  reports left in {LOCAL_WORK_DIR} (not deleted)", Col.DIM))

    print()
    print(c(f"Summary CSV: {RESULTS_CSV}", Col.GREEN, Col.BOLD))
    print(c("done.", Col.GREEN, Col.BOLD))


if __name__ == "__main__":
    main()
