# Yeraz Electric Transit Optimizer

## Overview

Yeraz is a data-driven decision support tool that helps city planners
decide which bus routes and depots to electrify under a fixed budget. It
balances three competing aims — emissions impact, ridership, and equity —
and selects a plan using an **exact mixed-integer program (MIP)** solved to
global optimality, rather than a rule-of-thumb heuristic.

> **Licensing notice**
> Code prior to 2025-12-13 (v1.0) was licensed under
> Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0).
> From 2025-12-13 onward (v1.1+), this repository is licensed under the
> GNU Affero General Public License v3.0 (AGPL-3.0).

A core design goal is the **dual-workflow** structure, so the tool works
whether or not a city has GTFS data:

1. **Data-rich cities** — a full pipeline that processes standard GTFS and
   geospatial data (`src/app.py`).
2. **Data-scarce cities** — an interactive tool for manually creating and
   analyzing routes where no GTFS feed exists (`src/app_manual.py`).

## How route selection works

- **Scoring.** Each candidate route is scored on three pillars:
  - *Ridership potential* — a machine-learned demand model.
  - *Emissions impact* — a physics-informed score from route length,
    gradient, and service frequency.
  - *Equity* — population and building density within a walkable radius of
    every stop, as a proxy for underserved-area reach.
- **Selection.** An exact MIP (`src/mip_heuristic.py`, via PuLP/CBC) picks
  the routes, buses, and depot assignments that maximize a weighted,
  normalized combination of the three scores, subject to:
  - Amortized capital + energy budget
  - Fleet availability
  - Per-depot charging-time and energy-supply capacity
  - One bus model and one depot per selected route
  - A chance-constrained range check (with an optional true stochastic
    reliability margin, off by default)

  Both pipelines — GTFS-based (`app.py`) and manual/no-GTFS
  (`app_manual.py`) — use the exact MIP by default. A corrected greedy
  heuristic (`src/greedy_heuristic.py`) is kept in the repo as a fast
  baseline and for benchmarking the MIP's optimality gain — it uses the
  same cost accounting as the MIP so the comparison isolates selection
  strategy rather than bookkeeping differences.

- **Output.** A summary table plus interactive per-route maps (with
  real road-network paths, not straight lines) showing the recommended
  plan directly in the app.

## How to Run

### 1. Setup

Clone this repository and navigate into the project directory. It is
recommended to use a Python virtual environment.

```bash
git clone https://github.com/artok06/Yeraz-Electric-Transit-Optimizer.git
cd Yeraz-Electric-Transit-Optimizer
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

The MIP solver (CBC) ships with the `pulp` package — no separate install
needed. Road-network map paths use the public OSRM demo server via
`requests`/`polyline`; if that endpoint is unreachable, maps fall back to
straight-line segments automatically.

### 3. Prepare the ML Model

The application uses a pre-trained ridership model. To generate the model
file (`ridership.joblib`), run the training script first. *(Requires a
training dataset such as `cta_initial_routes.csv` and `cta_ridership.csv`
in `data/training/`.)*

```bash
python src/ridership_model_trainer.py
```

### 4. Prepare Data

- **GTFS data** — available for many cities via the
  [Mobility Database](https://mobilitydatabase.org/).
- **Geospatial data** — population (GHS-POP) and building (GHS-BUILT-S)
  rasters from the
  [Copernicus Global Human Settlement Layer](https://human-settlement.emergency.copernicus.eu/download.php).

### 5. Run the Application

**GTFS-based pipeline (exact MIP by default):**

```bash
python -m streamlit run src/app.py
```

**Manual route creator (for cities without GTFS, also exact MIP by default):**

```bash
python -m streamlit run src/app_manual.py
```

## Solver notes

`mip_heuristic.py` accepts the same required flags as `greedy_heuristic.py`
(weights, budget, energy cost, max routes, weather factor, safety margin,
reserve fleet factor, nightly charging window), plus optional flags that
default to off so behavior matches the greedy baseline unless explicitly
enabled:

| Flag | Default | Effect when set |
|---|---|---|
| `--fare` | `0.0` | Includes fare-revenue offset in the objective |
| `--alpha_gradient` | `0.0` | Adds a road-gradient energy penalty |
| `--mu`, `--sigma`, `--epsilon` | off | Enables the true stochastic chance constraint for range feasibility (`ε` default `0.99` when enabled) |

## About

A dual-workflow decision support tool for bus fleet electrification —
built and presented as part of INFORMS TSL 2026 (Cambridge, MA).

**Author:** Artavazd Khachatryan · American University of Armenia
**License:** AGPL-3.0 (v1.1+) / CC BY-SA 4.0 (v1.0, pre-2025-12-13)
