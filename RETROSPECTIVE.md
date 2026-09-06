# Project Retrospective

**Status: Archived.** Active development on Yeraz has concluded. This
document summarizes what the project set out to do, what it achieved, and
where it landed — as a reference for anyone who finds this repository
later, including a future version of the author.

## Achievements

- **Finalist**, AI x City Climate Action Hackathon 2025
- Presented at **Yerevan Sustainable Energy Days 2026** — awarded a
  Certificate of Professional Consultation by the Yerevan Municipality
- Presented at **INFORMS TSL 2026** (Cambridge, MA)

## What it set out to do

Electrifying a city bus fleet forces a hard question under a fixed budget:
which routes to convert first? Most planning tools assume rich data — GTFS
feeds, automatic passenger counters, telemetry — that many cities,
especially outside wealthy transit systems, simply don't have. Yeraz set
out to make that decision computable anyway: take GTFS where it exists, or
manually-digitized route geometry where it doesn't, and turn it into a
fleet/route/depot electrification plan.

## What it became

- A dual-workflow tool (GTFS-based and manual/no-GTFS pipelines) scoring
  candidate routes on ridership potential (ML demand model), emissions
  impact (physics-informed), and equity (geospatial underserved-area
  proxy).
- An exact mixed-integer program (PuLP/CBC) that selects routes, fleet,
  and depot assignments to global optimality — not just a greedy
  approximation — subject to budget, fleet, and depot charging-capacity
  constraints, with an optional chance-constrained range-reliability
  check.
- Tested on a real case: Yerevan, Armenia — 17 real bus routes, 2 real
  depots, a scarce electric fleet.

## Results (Yerevan testbed)

- The exact MIP outperformed a (cost-model-corrected) greedy heuristic by
  **11–37%** across budget tiers.
- The binding constraint turned out to be **depot charging capacity, not
  budget** — value plateaued at 6 electrified routes regardless of
  additional funding.
- A real equity/efficiency trade-off exists on the Pareto frontier: an
  equity-focused plan gained **+38% equity for −17% ridership** relative to
  an efficiency-focused one, though the two objectives largely aligned for
  Yerevan overall.
- Presented at INFORMS TSL 2026 (Cambridge, MA).

## Why it's archived

Active development has ended. The repository is kept public and read-only
as a working reference implementation rather than continuing to evolve —
future changes (including the `app_manual.py` MIP migration noted in the
changelog) are not planned by the original author. Under the AGPL-3.0
license, anyone is welcome to fork this repository and continue the work.

## For anyone building on this

- The core selection engine (`src/mip_heuristic.py`) is designed as a
  drop-in replacement for the greedy baseline — same CLI, same CSV
  contracts — so it's a reasonable starting point to adapt to a different
  city or objective.
- See `CHANGELOG.md` for the full technical history and known limitations
  as of the final release (`v2.0.0`).
- See `README.md` for setup and data-source instructions.

---
Artavazd Khachatryan · American University of Armenia · [artkhachatryan.me](https://artkhachatryan.me/)
