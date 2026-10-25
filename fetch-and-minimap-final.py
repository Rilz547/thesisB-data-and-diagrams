#!/usr/bin/env python3
"""
Final-campaign DNA accuracy (run on the main PC, which has minimap2 and the hg38 reference), not on the Jetson.

Pulls the kept mode-B FASTQs of the final campaign from the Jetson (docs-riley/bench-runs/kept/output_<fast|hac>_<size>-dna-<stage>.fastq),
aligns each with minimap2 against hg38 and records the median identity (PAF col10 / col11 over every line, --secondary=no,
the preprint's method). DNA ONLY: RNA FASTQs need a different preset and reference (the Jetson's rna_accuracy_csv.py does those).

Differences from fetch-and-minimap.py (which is left unchanged for the older experiments; nsys and runner-summary handling were dropped):
  * one FASTQ at a time: pull -> align -> record -> delete, so at most one FASTQ (a 500k file is ~4 GB) is on this PC at once;
  * results are MERGED into final-campaign/accuracy/minimap_accuracy-<dtype>-<stage>.csv (one CSV per stage); re-running a subset
    (e.g. the 500k files later) adds or replaces its rows and never wipes the rest. No overwrite prompt is needed;
  * already-recorded FASTQs are skipped (use --redo to recompute); an interrupted run loses nothing;
  * reads are counted as lines/4 (the old '@' count can over-count when a quality line starts with '@');
  * pre_overlap and exploration files are never processed; --glob is repeatable.

Typical use (from the thesisB repo root):

  # 0. what would be fetched, with sizes, and what is already done (SSH only)
  python3 fetch-and-minimap-final.py --dry-run

  # 1. transfer only (do this while the Jetson is IDLE; never during a measured run), keeps the FASTQs locally
  python3 fetch-and-minimap-final.py --fetch-only

  # 2. align what is already local, one file at a time, deleting each FASTQ after it is aligned (offline)
  python3 fetch-and-minimap-final.py --accuracy-crunch

  # or do pull + align + delete in one go
  python3 fetch-and-minimap-final.py

  # subsets: 1k and 20k only / 500k only / one file
  python3 fetch-and-minimap-final.py --glob 'docs-riley/bench-runs/kept/output_*_?k-dna-*.fastq' --glob 'docs-riley/bench-runs/kept/output_*_??k-dna-*.fastq'
  python3 fetch-and-minimap-final.py --glob 'docs-riley/bench-runs/kept/output_*_500k-dna-*.fastq'
  python3 fetch-and-minimap-final.py -f docs-riley/bench-runs/kept/output_fast_500k-dna-spec.fastq

Local FASTQs are deleted after alignment unless --keep-fastq is given. The originals stay on the Jetson in bench-runs/kept/.
"""

from __future__ import annotations

import argparse
import atexit
import csv
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG — edit for your machines
# ---------------------------------------------------------------------------

JETSON_HOST = "192.168.8.211"
JETSON_USER = "riley"
JETSON_REPO = "~/slorado-riley-v2"

# Globs relative to JETSON_REPO (shell globs on the remote). Default: every DNA FASTQ kept by the campaign.
REMOTE_FASTQ_GLOBS = ["docs-riley/bench-runs/kept/output_*-dna-*.fastq"]
EXCLUDE_SUBSTR = (
    "pre_overlap",
    "explore",
)  # invalid stage and exploration files are never processed

LOCAL_WORK_DIR = (
    Path.cwd() / "slorado-accuracy-tmp"
)  # pulled FASTQs and the cached .mmi index live here

REFERENCE_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"
MINIMAP2 = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"
MINIMAP2_PRESET = "map-ont"
MINIMAP2_THREADS = 16
REFERENCE_MMI = LOCAL_WORK_DIR / f"{REFERENCE_FASTA.stem}.mmi"

EXPECTED_MEDIAN = {  # reads_1k.blow5 only (published, upstream README); no published value for 20k / 500k
    "fast": 0.940696,
    "hac": 0.976852,
}

RESULTS_CSV = LOCAL_WORK_DIR / "minimap_results.csv"  # scratch running log (append)
ACCURACY_DIR = Path.cwd() / "final-campaign" / "accuracy"  # durable CSVs, one per stage

FASTQ_RE = re.compile(r"output_(fast|hac)_(\d+k)(?:_overlap)?(.*)\.fastq")
CSV_FIELDS = [
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


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print(c(f"+ {' '.join(cmd)}", Col.DIM))
    return subprocess.run(cmd, check=True, text=True)


def gb(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


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


def remote_list_fastqs(globs: list[str]) -> list[tuple[str, int]]:
    """[(repo-relative path, size in bytes)] for every match of any glob, minus excluded names, in name order."""
    quoted = " ".join(shlex.quote(g) for g in globs)
    inner = f"cd {JETSON_REPO} && for g in {quoted}; do compgen -G \"$g\"; done | sort -u | xargs -r stat -c '%s %n'"
    proc = subprocess.run(
        ssh_base() + [f"bash -lc {shlex.quote(inner)}"],
        check=True,
        text=True,
        capture_output=True,
    )
    out: list[tuple[str, int]] = []
    for ln in proc.stdout.splitlines():
        parts = ln.strip().split(" ", 1)
        if (
            len(parts) == 2
            and parts[0].isdigit()
            and not any(x in parts[1] for x in EXCLUDE_SUBSTR)
        ):
            out.append((parts[1], int(parts[0])))
    return out


def remote_size(rel: str) -> int | None:
    proc = subprocess.run(
        ssh_base()
        + [
            f"bash -lc {shlex.quote(f'cd {JETSON_REPO} && stat -c %s {shlex.quote(rel)}')}"
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    out = proc.stdout.strip()
    return int(out) if out.isdigit() else None


def rsync_pull(rel: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    run(
        [
            "rsync",
            "-avP",
            "-e",
            rsync_ssh_e(),
            f"{JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}/{rel}",
            str(dest) + "/",
        ]
    )
    local = dest / Path(rel).name
    if not local.exists():
        die(f"rsync did not create {local}")
    return local


def count_fastq_reads(fastq: Path) -> int:
    n = 0
    with fastq.open("rb") as f:
        for n, _ in enumerate(f, 1):
            pass
    return n // 4


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
                matches, block = float(cols[9]), float(cols[10])
            except ValueError:
                continue
            if block > 0:
                ratios.append(matches / block)
    if not ratios:
        return None, 0
    ratios.sort()
    mid = len(ratios) // 2
    return (
        ratios[mid] if len(ratios) % 2 else 0.5 * (ratios[mid - 1] + ratios[mid])
    ), len(ratios)


def ensure_mmi(reference: Path, mmi: Path) -> Path:
    if mmi.is_file() and mmi.stat().st_mtime >= reference.stat().st_mtime:
        print(c(f"Using cached index: {mmi}", Col.DIM))
        return mmi
    banner(f"building minimap2 index -> {mmi.name}", Col.YELLOW)
    mmi.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            MINIMAP2,
            "-x",
            MINIMAP2_PRESET,
            "-t",
            str(MINIMAP2_THREADS),
            "-d",
            str(mmi),
            str(reference),
        ]
    )
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
    """Published expected median for the 1k files only; 20k / 500k have none."""
    m = FASTQ_RE.fullmatch(Path(fastq_name).name)
    if not m or m.group(2) != "1k":
        return None
    return EXPECTED_MEDIAN.get(m.group(1))


def stage_suffix(fastq_name: str) -> str:
    m = FASTQ_RE.fullmatch(Path(fastq_name).name)
    return m.group(3) if m else ""


def csv_path_for(fastq_name: str) -> Path:
    return ACCURACY_DIR / f"minimap_accuracy{stage_suffix(fastq_name)}.csv"


def recorded_fastqs() -> set[str]:
    done: set[str] = set()
    if ACCURACY_DIR.is_dir():
        for p in ACCURACY_DIR.glob("minimap_accuracy-*.csv"):
            with p.open(newline="") as f:
                done.update(r["fastq"] for r in csv.DictReader(f) if r.get("fastq"))
    return done


def merge_row_into_csv(row: dict) -> Path:
    """Add or replace this FASTQ's row in its stage CSV; every other row is kept."""
    path = csv_path_for(row["fastq"])
    ACCURACY_DIR.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict] = {}
    if path.exists():
        with path.open(newline="") as f:
            rows = {r["fastq"]: r for r in csv.DictReader(f) if r.get("fastq")}
    rows[row["fastq"]] = row
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    return path


def append_scratch_log(row: dict) -> None:
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not RESULTS_CSV.exists()
    with RESULTS_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def crunch_one(fq: Path, target: Path, idx: int, total: int, keep_paf: bool) -> dict:
    print(
        c(
            f"[{idx}/{total}] aligning {fq.name} ({gb(fq.stat().st_size)}) ...",
            Col.CYAN,
            Col.BOLD,
        )
    )
    n_reads = count_fastq_reads(fq)
    paf = fq.with_suffix(".paf")
    med, n_aln = run_minimap2(fq, target, paf)
    med_s = f"{med:.6f}" if med is not None else "NA"
    map_rate = 100.0 * n_aln / n_reads if n_reads else 0.0
    style = Col.GREEN if (med is not None and map_rate >= 50) else Col.YELLOW
    print(
        c(
            f"  -> {fq.name}: median identity = {med_s}  ({n_aln} alignments / {n_reads} reads = {map_rate:.1f}%)",
            style,
            Col.BOLD,
        )
    )
    print()
    exp = expected_for(fq.name)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fastq": fq.name,
        "n_reads": str(n_reads),
        "n_alignments": str(n_aln),
        "map_rate": f"{map_rate:.1f}%",
        "accuracy_median": med_s,
        "expected_median": f"{exp:.6f}" if exp is not None else "-",
        "delta_vs_expected": (
            f"{med - exp:+.6f}"
            if (exp is not None and med is not None)
            else ("NA" if exp is not None else "-")
        ),
        "nsys_pulled": "",
        "reference": str(REFERENCE_FASTA),
    }
    append_scratch_log(row)
    path = merge_row_into_csv(row)
    print(c(f"  recorded in {path.name}", Col.DIM))
    if not keep_paf:
        paf.unlink(missing_ok=True)
    return row


def check_tools() -> None:
    if not (Path(MINIMAP2).is_file() or shutil.which(MINIMAP2)):
        die(f"minimap2 not found ({MINIMAP2!r}); set MINIMAP2 in the script")
    if not REFERENCE_FASTA.is_file():
        die(f"reference not found: {REFERENCE_FASTA}")


def print_summary(rows: list[dict]) -> None:
    if not rows:
        return
    banner("summary (this run)", Col.GREEN)
    w = max(36, max(len(r["fastq"]) for r in rows))
    hdr = f"{'fastq':<{w}} {'reads':>7} {'alns':>7} {'aln/read':>9} {'median':>10} {'expected':>10} {'delta':>10}"
    print(c(hdr, Col.BOLD))
    print(c("-" * len(hdr), Col.DIM))
    for r in rows:
        rate = float(r["map_rate"].rstrip("%") or 0)
        bad = r["accuracy_median"] == "NA" or rate < 50
        print(
            c(
                f"{r['fastq']:<{w}} {r['n_reads']:>7} {r['n_alignments']:>7} {r['map_rate']:>9} {r['accuracy_median']:>10} "
                f"{r['expected_median']:>10} {r['delta_vs_expected']:>10}",
                Col.YELLOW if bad else Col.GREEN,
            )
        )
    print()
    print(
        c(
            "Expected (published, 1k only): FAST 0.940696, HAC 0.976852. 'aln/read' counts PAF lines per read and can exceed 100 % "
            "(supplementary alignments).",
            Col.DIM,
        )
    )
    if any(
        r["accuracy_median"] == "NA" or float(r["map_rate"].rstrip("%") or 0) < 50
        for r in rows
    ):
        print(
            c(
                "Low map rate or NA: the median is unreliable; check that FASTQ.",
                Col.YELLOW,
            )
        )
    print()


# ---------------------------------------------------------------------------
# modes
# ---------------------------------------------------------------------------


def resolve_remotes(args: argparse.Namespace) -> list[tuple[str, int]]:
    if args.files:
        out = []
        for rel in args.files:
            size = remote_size(rel)
            if size is None:
                die(f"not found on the Jetson: {rel}")
            out.append((rel, size))
        return out
    remotes = remote_list_fastqs(args.glob or REMOTE_FASTQ_GLOBS)
    if not remotes:
        die(f"no remote files matched {args.glob or REMOTE_FASTQ_GLOBS}")
    return remotes


def mode_dry_run(remotes: list[tuple[str, int]]) -> None:
    done = recorded_fastqs()
    total = sum(s for _, s in remotes)
    print(
        f"\n{len(remotes)} FASTQ(s), {gb(total)} in total; {sum(1 for r, _ in remotes if Path(r).name in done)} already recorded in {ACCURACY_DIR}\n"
    )
    for rel, size in remotes:
        print(
            f"  {'done ' if Path(rel).name in done else 'todo '} {gb(size):>9}  {rel}"
        )
    free = shutil.disk_usage(Path.cwd()).free
    print(
        f"\nlocal free space: {gb(free)} (the default pipeline needs room for one file at a time; --fetch-only needs {gb(total)})"
    )


def mode_fetch_only(remotes: list[tuple[str, int]], redo: bool) -> None:
    done = set() if redo else recorded_fastqs()
    todo = [(r, s) for r, s in remotes if Path(r).name not in done]
    total = sum(s for _, s in todo)
    LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(LOCAL_WORK_DIR).free
    print(
        f"{len(todo)} of {len(remotes)} FASTQ(s) to transfer ({gb(total)}); local free space {gb(free)}"
    )
    if free < total + 5e9:
        die(
            "not enough free space for --fetch-only; use the default pipeline (one file at a time) or free some space"
        )
    for i, (rel, size) in enumerate(todo, 1):
        banner(f"[{i}/{len(todo)}] {Path(rel).name} ({gb(size)})", Col.YELLOW)
        rsync_pull(rel, LOCAL_WORK_DIR)
    print(
        c(
            f"\nfetch-only complete: {len(todo)} file(s) in {LOCAL_WORK_DIR}",
            Col.GREEN,
            Col.BOLD,
        )
    )
    print(c("Next: python3 fetch-and-minimap-final.py --accuracy-crunch", Col.DIM))


def mode_crunch_local(args: argparse.Namespace) -> list[dict]:
    check_tools()
    fastqs = sorted(
        p
        for p in LOCAL_WORK_DIR.glob("output_*-dna-*.fastq")
        if not any(x in p.name for x in EXCLUDE_SUBSTR)
    )
    done = set() if args.redo else recorded_fastqs()
    todo = [p for p in fastqs if p.name not in done]
    if not fastqs:
        die(f"no DNA output_*.fastq in {LOCAL_WORK_DIR} (run --fetch-only first)")
    print(
        f"{len(fastqs)} local FASTQ(s), {len(fastqs) - len(todo)} already recorded, {len(todo)} to align"
    )
    LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
    target = ensure_mmi(REFERENCE_FASTA, REFERENCE_MMI)
    banner(
        f"minimap2 ({MINIMAP2_PRESET}, -t {MINIMAP2_THREADS}, target {target.name})",
        Col.MAGENTA,
    )
    rows = []
    for i, fq in enumerate(todo, 1):
        rows.append(crunch_one(fq, target, i, len(todo), args.keep_paf))
        if not args.keep_fastq:
            fq.unlink(missing_ok=True)
            print(c(f"  removed local {fq.name}", Col.DIM))
    return rows


def mode_pipeline(
    remotes: list[tuple[str, int]], args: argparse.Namespace
) -> list[dict]:
    check_tools()
    done = set() if args.redo else recorded_fastqs()
    todo = [(r, s) for r, s in remotes if Path(r).name not in done]
    print(
        f"{len(remotes)} FASTQ(s) matched, {len(remotes) - len(todo)} already recorded, {len(todo)} to do ({gb(sum(s for _, s in todo))})"
    )
    LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
    target = ensure_mmi(REFERENCE_FASTA, REFERENCE_MMI)
    rows = []
    for i, (rel, size) in enumerate(todo, 1):
        if shutil.disk_usage(LOCAL_WORK_DIR).free < 2 * size + 2e9:
            die(f"not enough free space for {Path(rel).name} ({gb(size)})")
        banner(f"[{i}/{len(todo)}] pull {Path(rel).name} ({gb(size)})", Col.YELLOW)
        fq = rsync_pull(rel, LOCAL_WORK_DIR)
        rows.append(crunch_one(fq, target, i, len(todo), args.keep_paf))
        if not args.keep_fastq:
            fq.unlink(missing_ok=True)
            print(c(f"  removed local {fq.name}", Col.DIM))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="list matching Jetson FASTQs with sizes and whether each is already recorded; no transfer",
    )
    ap.add_argument(
        "--fetch-only",
        action="store_true",
        help="rsync the matching FASTQs into the work dir and stop (do this only while the Jetson is idle)",
    )
    ap.add_argument(
        "--accuracy-crunch",
        action="store_true",
        help="offline: align the DNA FASTQs already in the work dir, one at a time",
    )
    ap.add_argument(
        "--keep-fastq",
        action="store_true",
        help="keep local FASTQs after alignment (default: delete each one right after it is aligned)",
    )
    ap.add_argument(
        "--keep-paf", action="store_true", help="keep .paf files (default: delete)"
    )
    ap.add_argument(
        "--redo",
        action="store_true",
        help="recompute FASTQs that are already recorded in the accuracy CSVs",
    )
    ap.add_argument(
        "-f",
        "--file",
        action="append",
        dest="files",
        metavar="REL_PATH",
        help="specific Jetson FASTQ (repo-relative); repeatable; skips --glob",
    )
    ap.add_argument(
        "--glob",
        action="append",
        metavar="GLOB",
        help=f"remote shell glob under the Jetson repo; repeatable (default: {REMOTE_FASTQ_GLOBS[0]})",
    )
    args = ap.parse_args()

    if args.accuracy_crunch:
        rows = mode_crunch_local(args)
        print_summary(rows)
        print(c(f"Accuracy CSVs in {ACCURACY_DIR}", Col.GREEN, Col.BOLD))
        return

    atexit.register(close_ssh_master)
    print(
        c(
            f"Jetson: {JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}   Work: {LOCAL_WORK_DIR}   Accuracy: {ACCURACY_DIR}",
            Col.DIM,
        )
    )
    remotes = resolve_remotes(args)
    if args.dry_run:
        mode_dry_run(remotes)
        return
    if args.fetch_only:
        mode_fetch_only(remotes, args.redo)
        return
    rows = mode_pipeline(remotes, args)
    print_summary(rows)
    print(c(f"Accuracy CSVs in {ACCURACY_DIR}", Col.GREEN, Col.BOLD))
    print(c("done.", Col.GREEN, Col.BOLD))


if __name__ == "__main__":
    main()
