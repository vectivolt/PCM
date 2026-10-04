# DC-DC platform — project rules

Read `docs/requirements/REQUIREMENTS.md` before doing anything. It is the scope lock; the roadmap it was
distilled from is `docs/requirements/00-roadmap-source.md` (do not edit). Decisions live in
`docs/requirements/DECISIONS.md`.

## Hard rules

- **Scope:** PV-P75/100/110 (non-isolated buck-boost MPPT), DAB-D60 (isolated), and their shared ecosystem
  boards. Not the AC-side PCS, not STS, not the Vienna modules.
- **Schematic + BOM only.** No PCB layout, footprints, Gerbers or mechanics.
- **Python is the source of truth.** `hardware/` and `bom/` are generated; never hand-edit them.
- **Docs layout:** `docs/reference-designs/<vendor>/<design>/`, `docs/datasheets/<category>/`,
  manifest `docs/SOURCES.csv`.
- **Agents:** Fable 5.1 orchestrates; Opus 5.5 / Sonnet 5.5 subagents do the work.
- **Honesty:** nothing is bench-validated. Label simulated/calculated values; list unknowns.
- A task that does not trace to REQUIREMENTS.md is a diversion — ask first.
- **Cost is a hard constraint** (REQUIREMENTS.md SRC-5, decision D-044). The product baseline is the cost-first
  architecture in `docs/requirements/ARCHITECTURE-COSTFIRST.md` (power board + control board, controller on the
  negative DC rail, one reinforced barrier). The earlier boards (CTRL-C2000, SYS-IO-AUX, PV-PORT, PVCELL-25, AUX-HV,
  BMU-GW, DAB60) are the roadmap's full-featured implementation: kept, not developed further.

## Layout

```
gen/        schematic generators (schgen/symlib/sexpr = writer, dcdclib = builder + checks, one script per board)
sim/        simulations and design calculations; outputs in sim/out/
hardware/   generated KiCad 10 projects + PDF/netlist/ERC per board
bom/        generated BOM CSVs
docs/       requirements, reference designs, datasheets
```

## Commands

```bash
.venv/bin/python gen/<board>.py      # build one board: KiCad project, ERC, netlist check, PDF, BOM
.venv/bin/python sim/<script>.py     # run one simulation
```

Tools: KiCad 10 at `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`, `ngspice` 46 on PATH.
