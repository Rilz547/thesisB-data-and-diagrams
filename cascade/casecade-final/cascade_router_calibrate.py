#!/usr/bin/env python3
"""
Cascade router calibration (main PC).

Uses the 1k FAST-only + thr50 (=HAC) FASTQs already on the Jetson to answer:
  Which FAST-side score best ranks reads by true HAC identity gain?

Steps
  1. rsync FAST + HAC FASTQs
  2. minimap2 each → per-read best identity
  3. from FAST Q-string: mean_q, frac_q<10, frac_q<15, p10_q, length
  4. for promote budgets 5–30%: compare each score vs oracle (rank by Δ)
  5. print a short recommendation; delete temps

Run on main PC:

  python3 cascade_router_calibrate.py
  python3 cascade_router_calibrate.py --keep-csv   # also write rows CSV beside cwd

Jetson files expected (already produced):
  docs-riley/cascade-sanity/output_fast_1k_baseline.fastq
  docs-riley/cascade-sanity/output_cascade_1k_thr50_sanity.fastq
"""

from __future__ import annotations

import argparse
import csv
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

JETSON_HOST = "192.168.4.48"
JETSON_USER = "riley"
JETSON_REPO = "~/slorado-riley-v2"

REMOTE_FAST = "docs-riley/cascade-sanity/output_fast_1k_baseline.fastq"
REMOTE_HAC = "docs-riley/cascade-sanity/output_cascade_1k_thr50_sanity.fastq"

REFERENCE_FASTA = Path.home() / "Desktop" / "slorado_analysis" / "ref" / "hg38noAlt.fa"
MINIMAP2 = "/home/riley/Desktop/slorado_analysis/mm2-gb/minimap2"
MINIMAP2_PRESET = "map-ont"
MINIMAP2_THREADS = 16

PROMOTE_PCTS = (5, 10, 15, 17, 20, 25, 30)  # 17 ≈ thr10 promote rate


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def rsync_pull(remote_rel: str, dest: Path) -> None:
    src = f"{JETSON_USER}@{JETSON_HOST}:{JETSON_REPO}/{remote_rel}"
    print(f"Pull: {src}")
    subprocess.run(["rsync", "-avP", src, str(dest)], check=True)
    if not dest.is_file():
        die(f"rsync did not create {dest}")


def parse_fastq_features(path: Path) -> dict[str, dict]:
    """read_id → {len, mean_q, frac_lt10, frac_lt15, p10_q} from FASTQ."""
    out: dict[str, dict] = {}
    with path.open() as f:
        while True:
            h = f.readline()
            if not h:
                break
            seq = f.readline().rstrip("\n")
            plus = f.readline()
            qual = f.readline().rstrip("\n")
            if not qual:
                die(f"truncated FASTQ {path}")
            rid = h[1:].split()[0]
            if not qual:
                qs: list[int] = []
            else:
                qs = [ord(c) - 33 for c in qual]
            n = len(qs)
            if n == 0:
                mean_q = 0.0
                frac10 = 1.0
                frac15 = 1.0
                p10 = 0.0
            else:
                mean_q = sum(qs) / n
                frac10 = sum(1 for q in qs if q < 10) / n
                frac15 = sum(1 for q in qs if q < 15) / n
                qs_s = sorted(qs)
                idx = min(n - 1, max(0, math.ceil(0.10 * n) - 1))
                p10 = float(qs_s[idx])
            out[rid] = {
                "len": len(seq),
                "mean_q": mean_q,
                "frac_lt10": frac10,
                "frac_lt15": frac15,
                "p10_q": p10,
            }
    return out


def run_minimap(fastq: Path, paf: Path) -> None:
    print(f"minimap2 {fastq.name} …")
    with paf.open("w") as out:
        subprocess.run(
            [
                MINIMAP2,
                "-cx",
                MINIMAP2_PRESET,
                "-t",
                str(MINIMAP2_THREADS),
                "--secondary=no",
                str(REFERENCE_FASTA),
                str(fastq),
            ],
            check=True,
            stdout=out,
        )


def best_identity_by_read(paf: Path) -> dict[str, float]:
    """Best matches/block_len per query name. Unmapped → absent."""
    best: dict[str, float] = {}
    with paf.open() as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 11:
                continue
            qname = cols[0]
            try:
                matches = float(cols[9])
                block = float(cols[10])
            except ValueError:
                continue
            if block <= 0:
                continue
            ident = matches / block
            if qname not in best or ident > best[qname]:
                best[qname] = ident
    return best


@dataclass
class ReadRow:
    rid: str
    mean_q: float
    frac_lt10: float
    frac_lt15: float
    p10_q: float
    length: int
    id_fast: float  # 0.0 if unmapped
    id_hac: float
    mapped_fast: bool
    mapped_hac: bool

    @property
    def delta(self) -> float:
        return self.id_hac - self.id_fast


# score_name → (key, higher_means_harder)
SCORES: list[tuple[str, str, bool]] = [
    ("mean_q", "mean_q", False),          # lower = harder
    ("frac_lt10", "frac_lt10", True),     # higher = harder
    ("frac_lt15", "frac_lt15", True),
    ("p10_q", "p10_q", False),            # lower = harder
    ("neg_length", "length", False),      # shorter first (weak prior)
]


def rank_indices(rows: list[ReadRow], attr: str, higher_harder: bool) -> list[int]:
    keyed = []
    for i, r in enumerate(rows):
        v = getattr(r, attr) if attr != "length" else r.length
        # for neg_length we pass attr=length and higher_harder=False → short first
        keyed.append((v, i))
    keyed.sort(key=lambda t: t[0], reverse=higher_harder)
    return [i for _, i in keyed]


def oracle_rank(rows: list[ReadRow]) -> list[int]:
    """Promote highest true Δ first (identity gain)."""
    keyed = [(r.delta, i) for i, r in enumerate(rows)]
    keyed.sort(key=lambda t: t[0], reverse=True)
    return [i for _, i in keyed]


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman ρ without numpy."""
    n = len(xs)
    if n < 3:
        return float("nan")

    def ranks(vals: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: vals[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    deny = math.sqrt(sum((b - my) ** 2 for b in ry))
    if denx == 0 or deny == 0:
        return float("nan")
    return num / (denx * deny)


def eval_budget(rows: list[ReadRow], order: list[int], k: int) -> dict:
    picked = order[:k]
    deltas = [rows[i].delta for i in picked]
    mean_d = sum(deltas) / k if k else 0.0
    # positive oracle mass captured
    all_pos = sum(max(0.0, r.delta) for r in rows)
    got_pos = sum(max(0.0, rows[i].delta) for i in picked)
    frac_pos = (got_pos / all_pos) if all_pos > 0 else float("nan")
    mean_fast = sum(rows[i].id_fast for i in picked) / k
    mean_hac = sum(rows[i].id_hac for i in picked) / k
    return {
        "k": k,
        "mean_delta": mean_d,
        "frac_pos_gain": frac_pos,
        "mean_id_fast": mean_fast,
        "mean_id_hac": mean_hac,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", default=REMOTE_FAST, help="repo-relative FAST FASTQ")
    ap.add_argument("--hac", default=REMOTE_HAC, help="repo-relative HAC FASTQ (thr50 cascade)")
    ap.add_argument("--keep-csv", action="store_true", help="write per-read CSV to cwd")
    ap.add_argument("--local-fast", type=Path, help="skip rsync; use local FAST FASTQ")
    ap.add_argument("--local-hac", type=Path, help="skip rsync; use local HAC FASTQ")
    args = ap.parse_args()

    if not Path(MINIMAP2).is_file():
        die(f"minimap2 not found: {MINIMAP2}")
    if not REFERENCE_FASTA.is_file():
        die(f"reference not found: {REFERENCE_FASTA}")

    with tempfile.TemporaryDirectory(prefix="cascade-calibrate-") as tmp:
        td = Path(tmp)
        fast_fq = td / "fast.fastq"
        hac_fq = td / "hac.fastq"

        if args.local_fast and args.local_hac:
            fast_fq.write_bytes(args.local_fast.read_bytes())
            hac_fq.write_bytes(args.local_hac.read_bytes())
        else:
            rsync_pull(args.fast, fast_fq)
            rsync_pull(args.hac, hac_fq)

        feats = parse_fastq_features(fast_fq)
        run_minimap(fast_fq, td / "fast.paf")
        run_minimap(hac_fq, td / "hac.paf")
        id_fast = best_identity_by_read(td / "fast.paf")
        id_hac = best_identity_by_read(td / "hac.paf")

        # Align on FASTQ read set
        rows: list[ReadRow] = []
        missing_hac = 0
        for rid, f in feats.items():
            if rid not in id_hac and rid not in id_fast:
                # still include — both unmapped
                pass
            mf = rid in id_fast
            mh = rid in id_hac
            if not mh:
                missing_hac += 1
            rows.append(
                ReadRow(
                    rid=rid,
                    mean_q=f["mean_q"],
                    frac_lt10=f["frac_lt10"],
                    frac_lt15=f["frac_lt15"],
                    p10_q=f["p10_q"],
                    length=f["len"],
                    id_fast=id_fast.get(rid, 0.0),
                    id_hac=id_hac.get(rid, 0.0),
                    mapped_fast=mf,
                    mapped_hac=mh,
                )
            )

        n = len(rows)
        print(f"\nReads: {n}  (HAC unmapped as id=0: {missing_hac})")
        pos = sum(1 for r in rows if r.delta > 0.01)
        neg = sum(1 for r in rows if r.delta < -0.01)
        print(f"Δ identity: {pos} improved >1pt, {neg} worse >1pt, "
              f"mean Δ={sum(r.delta for r in rows)/n:.4f}")

        # Spearman: feature vs true Δ (flip sign so "harder" correlates positively with Δ)
        print("\nSpearman ρ(feature_as_hard_score, Δ identity):")
        print(f"  {'score':<12} {'rho':>8}   (higher |rho| + positive = better hard-ranker)")
        rho_table: list[tuple[str, float]] = []
        for name, attr, higher_harder in SCORES:
            raw = [float(getattr(r, attr) if attr != "length" else r.length) for r in rows]
            # hard score: higher = more should promote
            hard = raw if higher_harder else [-v for v in raw]
            deltas = [r.delta for r in rows]
            rho = spearman(hard, deltas)
            rho_table.append((name, rho))
            print(f"  {name:<12} {rho:+8.3f}")

        oracle = oracle_rank(rows)
        print("\nPromote-budget simulation (mean Δ of selected set | % of positive-Δ mass captured):")
        hdr = f"{'budget':>8}"
        names = ["oracle"] + [s[0] for s in SCORES]
        for name in names:
            hdr += f"  {name:>22}"
        print(hdr)

        best_at_17: dict[str, float] = {}
        for pct in PROMOTE_PCTS:
            k = max(1, int(round(n * pct / 100.0)))
            line = f"{pct:>6}%/{k:<4}"
            for name in names:
                if name == "oracle":
                    order = oracle
                else:
                    attr = next(a for n_, a, _ in SCORES if n_ == name)
                    hh = next(h for n_, _, h in SCORES if n_ == name)
                    order = rank_indices(rows, attr, hh)
                m = eval_budget(rows, order, k)
                cell = f"{m['mean_delta']:+.3f}|{100*m['frac_pos_gain']:4.0f}%"
                line += f"  {cell:>22}"
                if pct == 17:
                    best_at_17[name] = m["frac_pos_gain"]
            print(line)

        # Recommendation
        print("\n" + "=" * 64)
        # pick best non-oracle by frac_pos at 17% (≈ current thr10 rate)
        cands = [(n, v) for n, v in best_at_17.items() if n != "oracle"]
        cands.sort(key=lambda t: t[1], reverse=True)
        winner, win_frac = cands[0]
        oracle_frac = best_at_17.get("oracle", float("nan"))
        mean_q_frac = best_at_17.get("mean_q", float("nan"))
        print(f"  At ~17% promote budget (≈ thr=10 rate):")
        print(f"    oracle captures {100*oracle_frac:.1f}% of positive Δ mass")
        print(f"    mean_q captures {100*mean_q_frac:.1f}%")
        print(f"    best score: {winner} → {100*win_frac:.1f}%")
        if winner == "mean_q" or (win_frac - mean_q_frac) < 0.03:
            print("\n  → Recommendation: keep mean_q ranking; add --cascade-force-frac")
            print("    (promote worst X% by mean_q). Absolute thr is fine but % is portable.")
        else:
            print(f"\n  → Recommendation: switch router score to '{winner}',")
            print("    then promote worst X% / below a cut on that score.")
        print("=" * 64)

        # Absolute thr=10 cut (current router) vs same count ranked by mean_q
        thr10_n = sum(1 for r in rows if r.mean_q < 10.0)
        order_thr = sorted(range(n), key=lambda i: (0 if rows[i].mean_q < 10 else 1, rows[i].mean_q))
        m_thr = eval_budget(rows, order_thr, thr10_n)
        print(f"\n  Absolute thr=10 selects {thr10_n} reads "
              f"({100*thr10_n/n:.1f}%): meanΔ={m_thr['mean_delta']:+.3f}, "
              f"pos-mass={100*m_thr['frac_pos_gain']:.1f}%")

        if args.keep_csv:
            out_csv = Path.cwd() / "cascade_router_calibrate_rows.csv"
            with out_csv.open("w", newline="") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "read_id", "mean_q", "frac_lt10", "frac_lt15", "p10_q", "length",
                        "id_fast", "id_hac", "delta", "mapped_fast", "mapped_hac",
                    ],
                )
                w.writeheader()
                for r in rows:
                    w.writerow(
                        {
                            "read_id": r.rid,
                            "mean_q": f"{r.mean_q:.4f}",
                            "frac_lt10": f"{r.frac_lt10:.4f}",
                            "frac_lt15": f"{r.frac_lt15:.4f}",
                            "p10_q": f"{r.p10_q:.2f}",
                            "length": r.length,
                            "id_fast": f"{r.id_fast:.6f}",
                            "id_hac": f"{r.id_hac:.6f}",
                            "delta": f"{r.delta:.6f}",
                            "mapped_fast": int(r.mapped_fast),
                            "mapped_hac": int(r.mapped_hac),
                        }
                    )
            print(f"\nWrote {out_csv}")

        print("\n(temp FASTQs/PAFs deleted)")
        print("Paste the Spearman table + budget table + recommendation block back in chat.")


if __name__ == "__main__":
    main()
