#!/usr/bin/env python3
"""
Main PC only (needs minimap2) — speculative-decode accuracy pass.

1. rsync Jetson sweep folder: docs-riley/spec-decode-sweep/<stamp>/
2. run minimap2 on each FASTQ
3. write results into a NEW local folder (keeps timing + accuracy together)

Copy this file to your PC analysis tree (e.g. thesisB-data-and-diagrams/), edit CONFIG,
then:

  python3 fetch-and-minimap-spec.py --dry-run
  python3 fetch-and-minimap-spec.py                  # latest stamp
  python3 fetch-and-minimap-spec.py --stamp 20260802T110000Z
  python3 fetch-and-minimap-spec.py --keep-paf

Hand the printed table / results CSV back to the Jetson chat.
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
# CONFIG — edit for your PC
# ---------------------------------------------------------------------------

JETSON_HOST = "192.168.4.48"  # or "riley-jetson"
JETSON_USER = "riley"
JETSON_REPO = "~/slorado-riley-v2"

REMOTE_SWEEP_ROOT = "docs-riley/spec-decode-sweep"

# Where this script writes the collected pack (new folder per stamp).
LOCAL_OUT_ROOT = Path.cwd() / "overlapping" / "spec-decode"

REFERENCE_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"
MINIMAP2 = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"
MINIMAP2_PRESET = "map-ont"
MINIMAP2_THREADS = 16

# Scratch for index + optional PAFs (not the deliverable pack).
LOCAL_WORK_DIR = Path.cwd() / "slorado-accuracy-tmp-spec"
REFERENCE_MMI = LOCAL_WORK_DIR / f"{REFERENCE_FASTA.stem}.mmi"

_SSH_CONTROL = f"/tmp/ssh-slorado-spec-{JETSON_USER}@{JETSON_HOST}-%p"
_SSH_OPTS = [
    "-o",
    "ControlMaster=auto",
    "-o",
    f"ControlPath={_SSH_CONTROL}",
    "-o",
    "ControlPersist=120",
]


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"+ {' '.join(cmd)}")
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


def remote_list_stamps() -> list[str]:
    remote_cmd = (
        f"bash -lc 'cd {JETSON_REPO} && "
        f"ls -1 {REMOTE_SWEEP_ROOT} 2>/dev/null | sort || true'"
    )
    proc = subprocess.run(
        ssh_base() + [remote_cmd],
        check=True,
        text=True,
        capture_output=True,
    )
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def remote_list_glob(glob_pat: str) -> list[str]:
    remote_cmd = (
        f"bash -lc 'cd {JETSON_REPO} && " f'compgen -G "{glob_pat}" | sort || true\''
    )
    proc = subprocess.run(
        ssh_base() + [remote_cmd],
        check=True,
        text=True,
        capture_output=True,
    )
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def rsync_pull_tree(remote_rel_dir: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    src = f"{JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}/{remote_rel_dir}/"
    run(["rsync", "-avP", "-e", rsync_ssh_e(), src, str(dest) + "/"])


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
        print(f"Using cached index: {mmi}")
        return mmi
    print(f"Building minimap2 index → {mmi}")
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
    print(f"+ {' '.join(cmd)} > {out_paf}")
    with out_paf.open("w") as out:
        subprocess.run(cmd, check=True, stdout=out)
    return median_identity(out_paf)


def parse_tag_from_fastq(name: str) -> dict[str, str]:
    """output_fast_1k_q10_m2_brave.fastq → fields."""
    base = Path(name).name
    m = re.fullmatch(r"output_(fast|hac)_(1k|20k)_(.+)\.fastq", base)
    if not m:
        return {"model": "", "size": "", "tag": Path(base).stem}
    return {"model": m.group(1), "size": m.group(2), "tag": m.group(3)}


def load_manifest(
    manifest: Path,
) -> tuple[dict[str, dict[str, str]], dict[tuple[str, str], dict[str, str]]]:
    """Return (by_fastq_basename, by_(size,tag) timing row from phase=time)."""
    by_fastq: dict[str, dict[str, str]] = {}
    by_time: dict[tuple[str, str], dict[str, str]] = {}
    if not manifest.is_file():
        return by_fastq, by_time
    with manifest.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        phase = (r.get("phase") or "").strip()
        tag = (r.get("tag") or "").strip()
        size = (r.get("size") or "").strip()
        fq = (r.get("fastq") or "").strip()
        if phase == "time" and tag and size:
            by_time[(size, tag)] = r
        if fq:
            by_fastq[fq] = r
        elif phase == "keep" and tag and size:
            # older manifests without fastq column fill
            by_fastq[f"output_{(r.get('model') or 'fast')}_{size}_{tag}.fastq"] = r
    return by_fastq, by_time


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Fetch speculative FASTQ sweep + minimap2 accuracy"
    )
    ap.add_argument(
        "--stamp",
        default="",
        help="UTC stamp under spec-decode-sweep/ (default: latest)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="list remote stamps / files only"
    )
    ap.add_argument(
        "--keep-paf", action="store_true", help="keep .paf under the results pack"
    )
    ap.add_argument(
        "--fetch-only", action="store_true", help="rsync only; skip minimap"
    )
    ap.add_argument(
        "--no-delete-local-fastq",
        action="store_true",
        help="keep pulled FASTQs in the pack (default: delete after accuracy)",
    )
    args = ap.parse_args()

    atexit.register(close_ssh_master)

    if shutil.which("rsync") is None:
        die("rsync not found")
    if (
        not args.dry_run
        and not args.fetch_only
        and shutil.which(MINIMAP2) is None
        and not Path(MINIMAP2).is_file()
    ):
        die(f"minimap2 not found ({MINIMAP2!r})")

    stamps = remote_list_stamps()
    if not stamps:
        die(f"no stamps under {JETSON_REPO}/{REMOTE_SWEEP_ROOT}")
    stamp = args.stamp.strip() or stamps[-1]
    if stamp not in stamps:
        die(f"stamp {stamp!r} not found; available: {', '.join(stamps)}")

    remote_dir = f"{REMOTE_SWEEP_ROOT}/{stamp}"
    remote_fastqs = remote_list_glob(f"{remote_dir}/fastq/output_*.fastq")
    print(f"Jetson: {JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}")
    print(f"Stamp:  {stamp}")
    print(f"FASTQs: {len(remote_fastqs)}")
    for p in remote_fastqs:
        print(f"  {p}")

    if args.dry_run:
        return

    out_dir = LOCAL_OUT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nResults pack → {out_dir.resolve()}")

    # Pull whole stamp tree (fastq/, logs/, manifest.csv, SUMMARY.txt).
    rsync_pull_tree(remote_dir, out_dir)

    manifest_path = out_dir / "manifest.csv"
    by_fastq, by_time = load_manifest(manifest_path)
    fastq_dir = out_dir / "fastq"
    local_fastqs = (
        sorted(fastq_dir.glob("output_*.fastq")) if fastq_dir.is_dir() else []
    )
    if not local_fastqs:
        die(f"no FASTQs after rsync in {fastq_dir}")

    if args.fetch_only:
        print("Fetch-only done.")
        return

    if not REFERENCE_FASTA.is_file():
        die(f"reference not found: {REFERENCE_FASTA}")

    LOCAL_WORK_DIR.mkdir(parents=True, exist_ok=True)
    target = ensure_mmi(REFERENCE_FASTA, REFERENCE_MMI)
    paf_dir = out_dir / "paf"
    if args.keep_paf:
        paf_dir.mkdir(parents=True, exist_ok=True)

    acc_csv = out_dir / "minimap_accuracy.csv"
    fields = [
        "timestamp",
        "stamp",
        "fastq",
        "tag",
        "model",
        "size",
        "speculative",
        "q_thr",
        "margin_thr",
        "brave",
        "real_s_time",
        "real_s_keep",
        "alpha",
        "repaired",
        "drafted",
        "n_reads",
        "n_alignments",
        "median_identity",
        "map_pct",
        "delta_vs_baseline",
    ]

    rows_out: list[dict[str, str]] = []
    baseline_med_by_size: dict[str, float] = {}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Baselines first within each size so delta_vs_baseline is same-size.
    ordered = sorted(
        local_fastqs,
        key=lambda p: (
            parse_tag_from_fastq(p.name)["size"],
            0 if parse_tag_from_fastq(p.name)["tag"] == "baseline" else 1,
            p.name,
        ),
    )

    for fq in ordered:
        meta = parse_tag_from_fastq(fq.name)
        man = by_fastq.get(fq.name, {})
        tag = meta["tag"] or man.get("tag", "")
        size = meta["size"] or man.get("size", "")
        time_row = by_time.get((size, tag), {})
        n_reads = count_fastq_reads(fq)
        paf_path = (
            (paf_dir / f"{fq.stem}.paf")
            if args.keep_paf
            else (LOCAL_WORK_DIR / f"{fq.stem}.paf")
        )
        med, n_alns = run_minimap2(fq, target, paf_path)
        map_pct = (100.0 * n_alns / n_reads) if n_reads else 0.0
        if tag == "baseline" and med is not None:
            baseline_med_by_size[size] = med
        delta = ""
        bmed = baseline_med_by_size.get(size)
        if med is not None and bmed is not None and tag != "baseline":
            delta = f"{med - bmed:+.6f}"

        real_s_time = time_row.get("real_s", "")
        real_s_keep = man.get("real_s", "")
        alpha = time_row.get("alpha") or man.get("alpha", "")
        repaired = time_row.get("repaired") or man.get("repaired", "")
        drafted = time_row.get("drafted") or man.get("drafted", "")

        row = {
            "timestamp": ts,
            "stamp": stamp,
            "fastq": fq.name,
            "tag": tag,
            "model": meta["model"] or man.get("model", ""),
            "size": size,
            "speculative": man.get("speculative") or time_row.get("speculative", ""),
            "q_thr": man.get("q_thr") or time_row.get("q_thr", ""),
            "margin_thr": man.get("margin_thr") or time_row.get("margin_thr", ""),
            "brave": man.get("brave") or time_row.get("brave", ""),
            "real_s_time": real_s_time,
            "real_s_keep": real_s_keep,
            "alpha": alpha,
            "repaired": repaired,
            "drafted": drafted,
            "n_reads": str(n_reads),
            "n_alignments": str(n_alns),
            "median_identity": f"{med:.6f}" if med is not None else "",
            "map_pct": f"{map_pct:.2f}",
            "delta_vs_baseline": delta,
        }
        rows_out.append(row)
        print(
            f"  {fq.name}: median_id={row['median_identity']} "
            f"map%={row['map_pct']} real_s(time)={real_s_time or '—'} delta={delta or '—'}"
        )

        if not args.keep_paf and paf_path.exists():
            paf_path.unlink()
        if not args.no_delete_local_fastq:
            fq.unlink()

    with acc_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows_out)

    # Human-readable table for pasting back into chat.
    summary = out_dir / "RESULTS.txt"
    with summary.open("w") as f:
        f.write(f"stamp={stamp}\n")
        f.write(f"generated={ts}\n")
        f.write(f"pack={out_dir.resolve()}\n\n")
        hdr = (
            f"{'size':4} {'tag':22} {'real_s':>8} {'alpha':>7} "
            f"{'map%':>7} {'median_id':>10} {'delta':>10}\n"
        )
        f.write(hdr)
        f.write("-" * len(hdr) + "\n")
        for r in rows_out:
            f.write(
                f"{r['size']:4} {r['tag']:22} {r['real_s_time']:>8} {r['alpha']:>7} "
                f"{r['map_pct']:>7} {r['median_identity']:>10} {r['delta_vs_baseline']:>10}\n"
            )
    print()
    print(summary.read_text())
    print(f"Wrote {acc_csv}")
    print(f"Wrote {summary}")
    print("Paste RESULTS.txt (or minimap_accuracy.csv) back into the Jetson chat.")


if __name__ == "__main__":
    main()
