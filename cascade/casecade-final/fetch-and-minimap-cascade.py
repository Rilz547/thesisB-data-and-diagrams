#!/usr/bin/env python3
"""
Cascade accuracy pass (main PC: minimap2).

Hardwired for the cascade threshold sweep on Jetson:
  - Jetson 192.168.4.48, repo ~/slorado-riley-v2
  - Glob: docs-riley/cascade-threshold-sweep/*/fastq/output_cascade_*.fastq
  - Accuracy → overlapping/cascade/accuracy/minimap_accuracy-cascade.csv
  - Also pulls matching cascade-logs/*.tsv + results/*.csv into work dir
  - Nsight / runner-summary fetch OFF
  - After each FASTQ is scored: delete that FASTQ and its .paf before the next

Copy this file to your main-PC analysis directory (same place as the
load-imbalance fetch-and-minimap.py), then run from that directory
(the one that contains overlapping/).

Examples
--------
  python3 fetch-and-minimap-cascade.py
  python3 fetch-and-minimap-cascade.py --fetch-only
  python3 fetch-and-minimap-cascade.py --accuracy-crunch
  python3 fetch-and-minimap-cascade.py --dry-run
  python3 fetch-and-minimap-cascade.py --keep-fastq --keep-paf
  python3 fetch-and-minimap-cascade.py --stamp 20260731T050000Z   # one sweep only
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
# CONFIG — cascade hardwired
# ---------------------------------------------------------------------------

JETSON_HOST = "192.168.4.48"
JETSON_USER = "riley"
JETSON_REPO = "~/slorado-riley-v2"

# Matches cascade_threshold_sweep.py layout.
REMOTE_FASTQ_GLOB = "docs-riley/cascade-threshold-sweep/*/fastq/output_cascade_*.fastq"
REMOTE_TSV_GLOB = "docs-riley/cascade-threshold-sweep/*/cascade-logs/*.tsv"
REMOTE_RESULTS_GLOB = "docs-riley/cascade-threshold-sweep/*/results/cascade_sweep_results.csv"

LOCAL_WORK_DIR = Path.cwd() / "slorado-accuracy-tmp-cascade"

REFERENCE_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"
MINIMAP2 = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"
MINIMAP2_PRESET = "map-ont"
MINIMAP2_THREADS = 16
REFERENCE_MMI = LOCAL_WORK_DIR / f"{REFERENCE_FASTA.stem}.mmi"

# 1k baselines (same as load-imbalance). Cascade sits between them; delta vs HAC is informative.
EXPECTED_MEDIAN = {
    "fast": 0.940696,
    "hac": 0.976852,
}

RESULTS_CSV = LOCAL_WORK_DIR / "minimap_results.csv"

ACCURACY_DIR = Path.cwd() / "overlapping" / "cascade" / "accuracy"
ACCURACY_CSV_NAME = "minimap_accuracy-cascade.csv"

# Side artifacts from the Jetson sweep (timing / promote fractions).
SWEEP_ARTIFACTS_DIR = Path.cwd() / "overlapping" / "cascade" / "sweep-artifacts"

FETCH_NSYS = False
NSYS_MATCH_WINDOW_SEC = 1500
FETCH_RUNNER_SUMMARIES = False
AUTO_NSIGHT_CONVERT = False

_SSH_CONTROL = f"/tmp/ssh-slorado-cascade-{JETSON_USER}@{JETSON_HOST}-%p"
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


def parse_cascade_meta(fastq_name: str) -> dict[str, str]:
    """
    output_cascade_1k_thr10_r01.fastq → size/threshold/rep
    """
    m = re.fullmatch(
        r"output_cascade_(1k|20k)_thr(\d+(?:p\d+)?)_r(\d+)\.fastq",
        Path(fastq_name).name,
    )
    if not m:
        return {"size": "", "threshold": "", "rep": ""}
    thr = m.group(2).replace("p", ".")
    return {"size": m.group(1), "threshold": thr, "rep": m.group(3)}


def expected_for(fastq_name: str) -> float | None:
    """Cascade has no single expected; show HAC 1k baseline as reference for 1k only."""
    meta = parse_cascade_meta(fastq_name)
    if meta.get("size") == "1k":
        return EXPECTED_MEDIAN["hac"]
    return None


ACCURACY_CSV_FIELDS = [
    "timestamp",
    "fastq",
    "size",
    "threshold",
    "rep",
    "n_reads",
    "n_alignments",
    "map_rate",
    "accuracy_median",
    "expected_median_hac1k",
    "delta_vs_hac1k",
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
    keep_paf: bool = False,
    keep_fastq: bool = False,
) -> list[dict]:
    if not REFERENCE_FASTA.is_file():
        die(
            f"reference not found: {REFERENCE_FASTA}\n"
            "Set REFERENCE_FASTA to your hg38 (or other) FASTA."
        )
    LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
    target = ensure_mmi(REFERENCE_FASTA, REFERENCE_MMI)
    summary_rows: list[dict] = []

    banner(f"minimap2  (target: {target.name}, -t {MINIMAP2_THREADS})", Col.MAGENTA)
    for i, fq in enumerate(fastqs, 1):
        print(c(f"[{i}/{len(fastqs)}] aligning {fq.name} …", Col.CYAN, Col.BOLD))
        meta = parse_cascade_meta(fq.name)
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
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fastq": fq.name,
            "size": meta.get("size", ""),
            "threshold": meta.get("threshold", ""),
            "rep": meta.get("rep", ""),
            "n_reads": str(n_reads),
            "n_alignments": str(n_aln),
            "map_rate": map_s,
            "accuracy_median": med_s,
            "expected_median_hac1k": exp_s,
            "delta_vs_hac1k": delta_s,
            "reference": str(REFERENCE_FASTA),
        }
        append_result_row(row)
        summary_rows.append(row)

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

    fastqs = sorted(LOCAL_WORK_DIR.glob("output_cascade_*.fastq"))
    if not fastqs:
        fastqs = sorted(LOCAL_WORK_DIR.glob("output_*.fastq"))
    if not fastqs:
        die(f"no cascade FASTQs in {LOCAL_WORK_DIR} (pull first or use --keep-fastq)")

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


def print_summary(rows: list[dict]) -> None:
    banner("summary", Col.GREEN)
    fq_w = max(40, max((len(r["fastq"]) for r in rows), default=40))
    hdr = (
        f"{'fastq':<{fq_w}} {'thr':>5} {'reads':>7} {'alns':>6} {'map%':>7} "
        f"{'median':>10} {'hac1k':>10} {'delta':>10}"
    )
    print(c(hdr, Col.BOLD))
    print(c("-" * len(hdr), Col.DIM))
    for r in rows:
        line = (
            f"{r['fastq']:<{fq_w}} {r.get('threshold', ''):>5} {r['n_reads']:>7} "
            f"{r['n_alignments']:>6} {r['map_rate']:>7} {r['accuracy_median']:>10} "
            f"{r['expected_median_hac1k']:>10} {r['delta_vs_hac1k']:>10}"
        )
        map_ok = float(r["map_rate"].rstrip("%") or 0) >= 50
        style = Col.GREEN if map_ok else Col.YELLOW
        print(c(line, style))
    print()
    print(
        c(
            "Reference (reads_1k / hg38):  FAST = 0.940696   HAC = 0.976852   "
            "(cascade 1k delta column is vs HAC)",
            Col.DIM,
        )
    )
    print()


def filter_by_stamp(paths: list[str], stamp: str | None) -> list[str]:
    if not stamp:
        return paths
    needle = f"cascade-threshold-sweep/{stamp}/"
    return [p for p in paths if needle in p]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list remote files only")
    ap.add_argument(
        "--fetch-only",
        action="store_true",
        help="rsync into work dir only (no minimap)",
    )
    ap.add_argument("--keep-fastq", action="store_true")
    ap.add_argument("--keep-paf", action="store_true")
    ap.add_argument(
        "--accuracy-crunch",
        action="store_true",
        help=f"skip Jetson/rsync; minimap2 FASTQs already in {LOCAL_WORK_DIR.name}/",
    )
    ap.add_argument(
        "--stamp",
        metavar="UTCSTAMP",
        help="only pull one sweep dir, e.g. 20260731T050000Z",
    )
    ap.add_argument(
        "-f",
        "--file",
        action="append",
        dest="files",
        metavar="NAME",
        help="specific remote FASTQ (repo-relative); repeatable",
    )
    ap.add_argument(
        "--glob",
        default=REMOTE_FASTQ_GLOB,
        help=f"remote shell glob (default: {REMOTE_FASTQ_GLOB})",
    )
    ap.add_argument(
        "--no-artifacts",
        action="store_true",
        help="skip pulling cascade .tsv + sweep results.csv",
    )
    args = ap.parse_args()

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
    print(c(f"Arts:   {SWEEP_ARTIFACTS_DIR}", Col.DIM))
    print(c("Nsight: OFF (cascade)", Col.DIM))

    if args.files:
        remotes = args.files
        print(f"Files:  {', '.join(remotes)}")
    else:
        print(f"Glob:   {args.glob}")
        remotes = filter_by_stamp(remote_list_glob(args.glob), args.stamp)
        if not remotes:
            die(f"no remote files matched {args.glob!r}" + (f" stamp={args.stamp}" if args.stamp else ""))

    print(f"Found {len(remotes)} remote FASTQ(s):")
    for r in remotes:
        print(f"  {r}")

    artifact_rels: list[str] = []
    if not args.no_artifacts:
        tsvs = filter_by_stamp(remote_list_glob(REMOTE_TSV_GLOB), args.stamp)
        results = filter_by_stamp(remote_list_glob(REMOTE_RESULTS_GLOB), args.stamp)
        artifact_rels = tsvs + results
        if artifact_rels:
            print(f"Also {len(artifact_rels)} sweep artifact(s) (tsv/results):")
            for r in artifact_rels[:8]:
                print(f"  {r}")
            if len(artifact_rels) > 8:
                print(c(f"  ... +{len(artifact_rels) - 8} more", Col.DIM))

    if args.fetch_only:
        if args.dry_run:
            print(c("(dry-run: would rsync the files above)", Col.DIM))
            return
        banner("rsync FASTQs + artifacts (--fetch-only)", Col.YELLOW)
        LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
        local_fastqs = rsync_pull(remotes, LOCAL_WORK_DIR)
        if artifact_rels:
            if not confirm_dest_writes(
                SWEEP_ARTIFACTS_DIR, [Path(r).name for r in artifact_rels], "sweep artifacts"
            ):
                die("artifact pull cancelled")
            rsync_pull(artifact_rels, SWEEP_ARTIFACTS_DIR)
        banner("fetch-only complete", Col.GREEN)
        print(c(f"FASTQs in {LOCAL_WORK_DIR}:", Col.BOLD))
        for fq in local_fastqs:
            print(f"  {fq.name}")
        print(c("Next: python3 fetch-and-minimap-cascade.py --accuracy-crunch", Col.DIM))
        print(c("done.", Col.GREEN, Col.BOLD))
        return

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

    if artifact_rels:
        banner(f"rsync sweep artifacts → {SWEEP_ARTIFACTS_DIR}", Col.YELLOW)
        if not confirm_dest_writes(
            SWEEP_ARTIFACTS_DIR, [Path(r).name for r in artifact_rels], "sweep artifacts"
        ):
            die("artifact pull cancelled")
        SWEEP_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        rsync_pull(artifact_rels, SWEEP_ARTIFACTS_DIR)

    summary_rows = crunch_local_fastqs(
        local_fastqs,
        keep_paf=args.keep_paf,
        keep_fastq=args.keep_fastq,
    )
    print_summary(summary_rows)

    banner(f"write accuracy CSVs → {ACCURACY_DIR}", Col.GREEN)
    written_acc = write_accuracy_dir_csvs(summary_rows, ACCURACY_DIR)

    print()
    for p in written_acc:
        print(c(f"Accuracy CSV: {p}", Col.GREEN, Col.BOLD))
    print(c(f"Scratch log:  {RESULTS_CSV}", Col.DIM))
    print(c(f"Artifacts:    {SWEEP_ARTIFACTS_DIR}", Col.DIM))
    print(c("done.", Col.GREEN, Col.BOLD))


if __name__ == "__main__":
    main()
