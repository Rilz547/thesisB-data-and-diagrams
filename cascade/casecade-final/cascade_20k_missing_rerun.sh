#!/usr/bin/env bash
# Re-run incomplete 20k cascade force-frac points with lower VRAM (-C 64).
# Fracs: 0.15 0.20 0.30 0.40 0.50
#
# From repo root, venv active, rebuilt slorado:
#   bash docs-riley/cascade_20k_missing_rerun.sh

set -euo pipefail
cd "$(dirname "$0")/.."

OUT=docs-riley/cascade-20k-missing-rerun
mkdir -p "$OUT"

FAST=models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0
HAC=models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0
READS=test/PGXXXX230339/reads_20k.blow5

RESULTS="$OUT/cascade_missing_rerun_results.csv"
if [[ ! -f "$RESULTS" ]]; then
  echo "tag,size,force_frac,real_s,pct_hac,peak_ram_gb,source,note" > "$RESULTS"
fi

for f in 0.15 0.20 0.30 0.40 0.50; do
  tag=$(python3 -c "print(f'frac{int(round(${f}*1000)):03d}')")
  cons="$OUT/console_cascade_20k_${tag}_C64.txt"
  echo "=== 20k ${tag} force_frac=${f} -C 64 ==="
  ./slorado basecaller \
    -C 64 -c 12288 -K 4096 -p 150 \
    --overlap-decode=yes \
    --overlap-depth=1 \
    --fixed-c-batch=no \
    --flush-threshold=64 \
    --cascade=yes \
    --cascade-hac="$HAC" \
    --cascade-force-frac="$f" \
    --cascade-log="$OUT/cascade_20k_${tag}_C64.tsv" \
    -o "$OUT/output_cascade_20k_${tag}_C64.fastq" \
    "$FAST" \
    "$READS" \
    2>&1 | tee "$cons"

  # Append timing so fetch-and-minimap can join real_s / %HAC.
  python3 - "$RESULTS" "$tag" "$f" "$cons" <<'PY'
import re, sys
from pathlib import Path
results, tag, frac, cons = sys.argv[1:5]
text = Path(cons).read_text()
real = re.search(r"\[main\] Real time: ([\d.]+) sec; CPU time: ([\d.]+) sec; Peak RAM: ([\d.]+) GB", text)
cas = re.search(r"promoted_hac=\d+ \(([\d.]+)% HAC\)", text)
real_s = real.group(1) if real else ""
peak = real.group(3) if real else ""
pct = cas.group(1) if cas else f"{float(frac)*100:g}"
# replace existing row for this tag if present
rows = Path(results).read_text().splitlines()
hdr, body = rows[0], [ln for ln in rows[1:] if not ln.startswith(f"cascade_20k_{tag}_C64,")]
body.append(
    f"cascade_20k_{tag}_C64,20k,{frac},{real_s},{pct},{peak},console,tee'd by cascade_20k_missing_rerun.sh"
)
Path(results).write_text(hdr + "\n" + "\n".join(body) + "\n")
print(f"  recorded real_s={real_s or 'NA'} pct_hac={pct}")
PY
done

echo "Done. Results: $RESULTS"
echo "On main PC:"
echo "  python3 fetch-and-minimap-cascade-frac.py"
