#!/usr/bin/env python3
"""
Main PC — pull Jetson power-runs and make quick charts.

1. rsync docs-riley/power-runs/ from Jetson into a NEW local folder
2. per run: CPU / GPU / RAM+swap / power-over-time PNGs from samples.csv
3. summary bar charts from all_summaries.csv (wall, peak power, energy)

Copy to your PC analysis tree, edit CONFIG, then:

  python3 fetch-and-plot-power.py --dry-run
  python3 fetch-and-plot-power.py
  python3 fetch-and-plot-power.py --stamp 20260811   # only run dirs matching substring

Needs: matplotlib (pip install matplotlib)
"""

from __future__ import annotations

import argparse
import atexit
import csv
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

REMOTE_POWER_ROOT = "docs-riley/power-runs"

# Deliverable pack (new folder each pull).
LOCAL_OUT_ROOT = Path.cwd() / "power-runs" / "baseline"

_SSH_CONTROL = f"/tmp/ssh-slorado-power-{JETSON_USER}@{JETSON_HOST}-%p"
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
        ["ssh", "-O", "exit", *_SSH_OPTS, f"{JETSON_USER}@{JETSON_HOST}"],
        check=False,
        capture_output=True,
        text=True,
    )


def ensure_ssh() -> None:
    atexit.register(close_ssh_master)
    r = subprocess.run(
        [*ssh_base(), "true"],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        die(f"SSH to {JETSON_USER}@{JETSON_HOST} failed:\n{r.stderr}")


def remote_ls_runs(stamp_filter: str | None) -> list[str]:
    """List remote run directory names (not all_summaries.csv)."""
    cmd = (
        f"cd {JETSON_REPO}/{REMOTE_POWER_ROOT} && "
        "ls -1d */ 2>/dev/null | sed 's:/$::' | sort"
    )
    r = subprocess.run(
        [*ssh_base(), cmd],
        check=True,
        capture_output=True,
        text=True,
    )
    names = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if stamp_filter:
        names = [n for n in names if stamp_filter in n]
    return names


def read_samples(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def to_float(row: dict[str, str], key: str) -> float | None:
    v = row.get(key, "")
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def series(rows: list[dict[str, str]], key: str) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for i, row in enumerate(rows):
        y = to_float(row, key)
        if y is None:
            continue
        xs.append(float(i))  # 1 Hz → seconds from start
        ys.append(y)
    return xs, ys


def plot_run(run_dir: Path, charts_dir: Path) -> list[Path]:
    import matplotlib.pyplot as plt

    samples_path = run_dir / "samples.csv"
    if not samples_path.is_file():
        print(f"  skip (no samples.csv): {run_dir.name}")
        return []

    rows = read_samples(samples_path)
    if not rows:
        print(f"  skip (empty samples): {run_dir.name}")
        return []

    charts_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    title = run_dir.name

    specs = [
        ("cpu_over_time.png", "cpu_mean_pct", "CPU mean (%)", "CPU"),
        ("gpu_over_time.png", "gr3d_pct", "GPU GR3D (%)", "GPU"),
        ("power_over_time.png", "power_total_mW", "Power (mW, VDD_IN)", "Power"),
    ]

    for fname, key, ylabel, short in specs:
        xs, ys = series(rows, key)
        if not xs:
            print(f"  warn: no {key} in {run_dir.name}")
            continue
        fig, ax = plt.subplots(figsize=(9, 3.5))
        ax.plot(xs, ys, linewidth=1.2, color="#1f4e79")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(ylabel)
        ax.set_title(f"{short} — {title}")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        dest = charts_dir / fname
        fig.savefig(dest, dpi=140)
        plt.close(fig)
        out.append(dest)

    # RAM + swap on one axes
    x_ram, y_ram = series(rows, "ram_used_mb")
    x_sw, y_sw = series(rows, "swap_used_mb")
    if x_ram or x_sw:
        fig, ax = plt.subplots(figsize=(9, 3.5))
        if x_ram:
            ax.plot(x_ram, y_ram, linewidth=1.2, color="#1f4e79", label="RAM used (MB)")
        if x_sw:
            ax.plot(x_sw, y_sw, linewidth=1.2, color="#c45c26", label="Swap used (MB)")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Memory (MB)")
        ax.set_title(f"RAM / swap — {title}")
        ax.legend(loc="best")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        dest = charts_dir / "ram_swap_over_time.png"
        fig.savefig(dest, dpi=140)
        plt.close(fig)
        out.append(dest)

    return out


def plot_summary_bars(summaries: Path, out_dir: Path) -> list[Path]:
    import matplotlib.pyplot as plt

    if not summaries.is_file():
        return []

    with summaries.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []

    # One bar group per label|model|size
    labels = []
    wall, peak, energy = [], [], []
    for r in rows:
        labels.append(f"{r.get('label','')}\n{r.get('model','')}_{r.get('size','')}")
        wall.append(to_float(r, "wall_s") or 0.0)
        peak.append((to_float(r, "peak_power_mW") or 0.0) / 1000.0)  # W
        energy.append(to_float(r, "energy_J") or 0.0)

    out: list[Path] = []
    x = list(range(len(labels)))

    for fname, vals, ylabel, title in [
        ("summary_wall_s.png", wall, "Real time (s)", "Wall time by run"),
        ("summary_peak_power_W.png", peak, "Peak power (W)", "Peak VDD_IN by run"),
        ("summary_energy_J.png", energy, "Energy (J)", "Energy by run"),
    ]:
        fig, ax = plt.subplots(figsize=(max(8, 1.2 * len(labels)), 4))
        ax.bar(x, vals, color="#1f4e79")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=0, fontsize=8)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        dest = out_dir / fname
        fig.savefig(dest, dpi=140)
        plt.close(fig)
        out.append(dest)

    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="list remote runs only")
    ap.add_argument(
        "--stamp",
        default=None,
        help="only pull run dirs whose name contains this substring",
    )
    ap.add_argument(
        "--no-pull",
        action="store_true",
        help="reuse latest local pack under LOCAL_OUT_ROOT (plot only)",
    )
    ap.add_argument(
        "--local-dir",
        type=Path,
        default=None,
        help="plot an existing local power-runs pack (implies --no-pull)",
    )
    args = ap.parse_args()

    try:
        import matplotlib  # noqa: F401
    except ImportError:
        die("matplotlib required: pip install matplotlib")

    if args.local_dir is not None:
        pack = args.local_dir.expanduser().resolve()
        if not pack.is_dir():
            die(f"local dir not found: {pack}")
        print(f"Plotting existing pack: {pack}")
        _plot_pack(pack)
        return

    if args.no_pull:
        if not LOCAL_OUT_ROOT.is_dir():
            die(f"no local packs under {LOCAL_OUT_ROOT}")
        packs = sorted(
            [p for p in LOCAL_OUT_ROOT.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
        )
        if not packs:
            die(f"no local packs under {LOCAL_OUT_ROOT}")
        pack = packs[-1]
        print(f"Re-plotting latest pack: {pack}")
        _plot_pack(pack)
        return

    ensure_ssh()
    names = remote_ls_runs(args.stamp)
    if not names:
        die(
            f"no remote runs under {REMOTE_POWER_ROOT}"
            + (f" matching {args.stamp!r}" if args.stamp else "")
        )

    print(f"Remote runs ({len(names)}):")
    for n in names:
        print(f"  {n}")

    if args.dry_run:
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pack = LOCAL_OUT_ROOT / stamp
    pack.mkdir(parents=True, exist_ok=False)
    raw = pack / "raw"
    raw.mkdir()

    remote_base = f"{JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}/{REMOTE_POWER_ROOT}"
    # Pull each selected run dir + the rolling summary table.
    for n in names:
        run(
            [
                "rsync",
                "-az",
                "-e",
                rsync_ssh_e(),
                f"{remote_base}/{n}/",
                str(raw / n) + "/",
            ]
        )
    run(
        [
            "rsync",
            "-az",
            "-e",
            rsync_ssh_e(),
            f"{remote_base}/all_summaries.csv",
            str(raw / "all_summaries.csv"),
        ],
        check=False,
    )

    summary_src = raw / "all_summaries.csv"
    if summary_src.is_file():
        shutil.copy2(summary_src, pack / "all_summaries.csv")

    _plot_pack(pack)
    print(f"\nDone. Pack: {pack}")
    print(f"  charts/: per-run PNGs + summary_*.png")
    print(f"  raw/:    samples.csv / summary.csv / logs")


def _plot_pack(pack: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")

    raw = pack / "raw" if (pack / "raw").is_dir() else pack
    charts_root = pack / "charts"
    charts_root.mkdir(parents=True, exist_ok=True)

    run_dirs = sorted(
        [p for p in raw.iterdir() if p.is_dir() and (p / "samples.csv").is_file()]
    )
    print(f"Plotting {len(run_dirs)} run(s) → {charts_root}")
    for rd in run_dirs:
        out = plot_run(rd, charts_root / rd.name)
        print(f"  {rd.name}: {len(out)} charts")

    summaries = pack / "all_summaries.csv"
    if not summaries.is_file():
        # try raw
        alt = raw / "all_summaries.csv"
        if alt.is_file():
            summaries = alt
    bars = plot_summary_bars(summaries, charts_root)
    for b in bars:
        print(f"  summary: {b.name}")


if __name__ == "__main__":
    main()
