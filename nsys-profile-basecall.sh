#!/bin/bash

# A quick safety check: Kill any lingering background nsys daemons that might be locking files
killall nsys-ui nsys 2>/dev/null

echo "Running NSight profile for 1k fast basecaller..."
rm -f nsys_fast_1k_base.nsys-rep nsys_fast_1k_base.sqlite
nsys profile \
  --force-overwrite=true \
  --trace=cuda,nvtx,osrt \
  --sample=none \
  --output=nsys_fast_1k_base \
  ./slorado basecaller -C 128 -K 2048 -o output_fast_1k.fastq \
  models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 \
  test/PGXXXX230339/reads_1k.blow5;

echo "Running NSight profile for 1k fast overlap caller..."
rm -f nsys_fast_1k_overlap.nsys-rep nsys_fast_1k_overlap.sqlite
nsys profile \
  --force-overwrite=true \
  --trace=cuda,nvtx,osrt \
  --sample=none \
  --output=nsys_fast_1k_overlap \
  ./slorado basecaller --overlap-decode=yes -C 128 -K 2048 -o output_fast_1k_overlap.fastq \
  models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 \
  test/PGXXXX230339/reads_1k.blow5;

# echo "Running NSight profile for 20k fast basecaller..."
# rm -f nsys_fast_20k_base.nsys-rep nsys_fast_20k_base.sqlite
# nsys profile \
#   --force-overwrite=true \
#   --trace=cuda,nvtx,osrt \
#   --sample=none \
#   --output=nsys_fast_20k_base \
#   ./slorado basecaller -C 128 -o output_fast_20k.fastq \
#   models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 \
#   test/PGXXXX230339/reads_20k.blow5;

# echo "Running NSight profile for 20k fast overlap caller..."
# rm -f nsys_fast_20k_overlap.nsys-rep nsys_fast_20k_overlap.sqlite
# nsys profile \
#   --force-overwrite=true \
#   --trace=cuda,nvtx,osrt \
#   --sample=none \
#   --output=nsys_fast_20k_overlap \
#   ./slorado basecaller --overlap-decode=yes -C 128 -o output_fast_20k_overlap.fastq \
#   models/dna_r10.4.1_e8.2_400bps_fast@v5.0.0 \
#   test/PGXXXX230339/reads_20k.blow5;

# echo "Running NSight profile for 1k hac basecaller..."
# rm -f nsys_hac_1k_base.nsys-rep nsys_hac_1k_base.sqlite
# nsys profile \
#   --force-overwrite=true \
#   --trace=cuda,nvtx,osrt \
#   --sample=none \
#   --output=nsys_hac_1k_base \
#   ./slorado basecaller -C 128 -o output_hac_1k.fastq \
#   models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0 \
#   test/PGXXXX230339/reads_1k.blow5;

# echo "Running NSight profile for 1k hac overlap caller..."
# rm -f nsys_hac_1k_overlap.nsys-rep nsys_hac_1k_overlap.sqlite
# nsys profile \
#   --force-overwrite=true \
#   --trace=cuda,nvtx,osrt \
#   --sample=none \
#   --output=nsys_hac_1k_overlap \
#   ./slorado basecaller --overlap-decode=yes -C 128 -o output_hac_1k_overlap.fastq \
#   models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0 \
#   test/PGXXXX230339/reads_1k.blow5;

# echo "Running NSight profile for 20k hac basecaller..."
# rm -f nsys_hac_20k_base.nsys-rep nsys_hac_20k_base.sqlite
# nsys profile \
#   --force-overwrite=true \
#   --trace=cuda,nvtx,osrt \
#   --sample=none \
#   --output=nsys_hac_20k_base \
#   ./slorado basecaller -C 128 -o output_hac_20k.fastq \
#   models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0 \
#   test/PGXXXX230339/reads_20k.blow5;

# echo "Running NSight profile for 20k hac overlap caller..."
# rm -f nsys_hac_20k_overlap.nsys-rep nsys_hac_20k_overlap.sqlite
# nsys profile \
#   --force-overwrite=true \
#   --trace=cuda,nvtx,osrt \
#   --sample=none \
#   --output=nsys_hac_20k_overlap \
#   ./slorado basecaller --overlap-decode=yes -C 128 -o output_hac_20k_overlap.fastq \
#   models/dna_r10.4.1_e8.2_400bps_hac@v5.0.0 \
#   test/PGXXXX230339/reads_20k.blow5;

echo "Done. FASTQs written:"
ls -lh output_{fast,hac}_{1k,20k}{,_overlap}.fastq 2>/dev/null
