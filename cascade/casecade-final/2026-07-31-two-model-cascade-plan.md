# Two-model cascade — Implementation Plan

> Implement only after Riley approves the design doc.

**Goal:** Flag-gated FAST→HAC cascade: FAST all reads, HAC only when router says hard.

**Architecture:** Two-pass per databatch; mean-Q router after stitch; HAC re-basecall HARD subset.

**Tech:** Existing slorado runners + openfish; no new models.

**Design:** `docs-riley/2026-07-31-two-model-cascade-design.md`

## Global constraints

- Default off (`--cascade=no`)
- Docs in `docs-riley/`
- Compose with overlap + narrow + flush (do not regress depth=1 path)
- Always log `% promoted` and wall time

---

### Task 1: CLI + options

**Files:** `src/slorado.h`, `src/slorado.cpp`, `src/basecaller_main.cpp`

- [ ] Add `cascade`, `cascade_hac_path`, `cascade_threshold`, `cascade_metric` to `opt_t`
- [ ] Parse `--cascade=yes|no`, `--cascade-hac=PATH`, `--cascade-threshold=FLOAT`
- [ ] Print cascade settings in banner
- [ ] Validate: cascade=yes ⇒ HAC path set; models exist

### Task 2: Dual model init (sequential-safe)

**Files:** `src/torchbox.cpp`, `src/slorado.h` / core

- [ ] When cascade: init FAST runners from positional model; init HAC runners from `--cascade-hac` **or** document unload/reload if VRAM fails
- [ ] Smoke: both load on Orin with `-C 128`

### Task 3: Router

**Files:** new small helper e.g. `src/cascade_router.cpp` + header, or inline in `slorado.cpp`

- [ ] `mean_phred(qstring) → float`
- [ ] `is_hard(mean_q, threshold) → bool`
- [ ] Unit-style self-check on a few synthetic qstrings (optional)

### Task 4: Two-pass `process_db` / output path

**Files:** `src/slorado.cpp`, maybe `src/basecall.cpp` entry

- [ ] Pass A: existing FAST `basecall_db` + `postprocess_signal`
- [ ] Classify reads; build HARD index list
- [ ] Pass B: HAC basecall HARD reads only; overwrite `sequence`/`qstring`/`moves`
- [ ] Counters: `n_fast_kept`, `n_hac_promoted`
- [ ] Print summary line at end of run

### Task 5: Cascade TSV (optional but recommended)

- [ ] `--cascade-log=FILE` or auto `out.cascade.tsv`: `read_id,mean_q,model`
- [ ] Helps demos / threshold tuning

### Task 6: Validation script notes

**Files:** `docs-riley/` short howto

- [ ] Commands for FAST-only / HAC-only / cascade threshold sweep on 1k
- [ ] Point at `fetch-and-minimap.py` for identity
- [ ] Fill results table in design doc when Riley runs benches

## Done when

- Cascade off ≡ old behaviour  
- Extreme thresholds behave as FAST-only / HAC-only  
- Mid threshold shows `% promoted` + wall between the two bounds  
