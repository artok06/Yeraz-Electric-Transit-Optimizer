# Changelog

All notable changes to Yeraz are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.0.0] — Unreleased (this update)

### Added
- **`src/mip_heuristic.py`** — an exact mixed-integer program (PuLP / CBC) that
  replaces greedy route selection with a globally-optimal solve. Same CLI
  interface and I/O contract as `greedy_heuristic.py` (drop-in replacement:
  reads the same input CSVs, writes the same `results.json` and per-route
  folium maps). Implements:
  - Weighted multi-objective (emissions / equity / ridership impact)
  - Amortized capital + energy budget constraint
  - Chance-constrained range with a robust reliability margin
    `W = μ + Φ⁻¹(1−ε)·σ` (disabled by default — see below)
  - One-model / one-depot assignment per selected route
  - Fleet availability and per-depot charging-time + energy-supply capacity
  - Optional, off-by-default extensions: fare-revenue offset (`--fare`),
    gradient energy penalty (`--alpha_gradient`), and the true chance
    constraint (`--mu`, `--sigma`, `--epsilon`). Left off by default so
    out-of-the-box behavior matches the greedy baseline.
- Real road-network route paths on generated maps (via OSRM), replacing
  straight-line stop-to-stop segments, in both the greedy and MIP map output.
- `requirements.txt` (previously referenced by the README but not present in
  the repo).

### Changed
- **`src/app.py`** (GTFS pipeline) and **`src/app_manual.py`** (manual /
  no-GTFS pipeline): both now call `mip_heuristic.py` by default instead of
  `greedy_heuristic.py`. Default sidebar values in `app.py` updated: equity
  and ridership weights to 0.5 / 0.5 (environmental weight now defaults to
  0.0), max routes 5 → 25, energy cost $0.20 → $0.11/kWh, reserve fleet
  factor 0.10 → 0.12.
- **`src/greedy_heuristic.py`**: cost model corrected to match the MIP's
  accounting, so exact-vs-greedy comparisons isolate the *selection
  strategy* rather than bookkeeping differences. Specifically:
  1. Energy is now costed for the whole assigned fleet, not per bus (the old
     version under-counted energy roughly by fleet size).
  2. Depot O&M is charged once per activated depot, not once per route
     assigned to that depot (the old version double/triple-counted it).
  3. Depot capacity now uses the same time-shared charging-time and
     energy-supply limits as the MIP, instead of a simple "buses ≤
     chargers" slot count.
- **`src/app_manual.py`** results view rebuilt as a KPI dashboard with tabs
  (routes / maps / notes), progress-bar score columns, and a JSON results
  download button.
- **`src/equity_score.py`** (v6.0 → v7.0): replaced a shared, statically
  cleaned temp directory with a per-run timestamped temp directory, fixing a
  file-lock race condition when runs overlap.

---

## [1.1.0] — 2025-12-13
### Changed
- Relicensed from CC BY-SA 4.0 to AGPL-3.0 for all future development
  (v1.1+). Code prior to this date remains available under CC BY-SA 4.0.

---

## [1.0.0] — 2025-08-27
### Added
- Initial public release. Dual-workflow design:
  - `src/app.py` — GTFS-based pipeline for data-rich cities.
  - `src/app_manual.py` — interactive manual route creator for data-scarce
    cities.
- Multi-criteria route scoring: ML-predicted ridership, physics-informed
  emissions score, and a geospatial equity/coverage proxy.
- Greedy heuristic route selection (`src/greedy_heuristic.py`) under budget,
  fleet, and depot constraints.
- Interactive per-route maps and summary tables.
