<!-- breadcrumb --> [Home](../../README.md) › [Documentation](../README.md) › Repository guide

# 🧰 Repository guide

> How to build a board, run a simulation, re-roll the BOMs and the cost, run the audits, and add a part or a board without breaking the rules.

![python](https://img.shields.io/badge/Python-3.12-5B6B7A?style=flat-square)
![kicad](https://img.shields.io/badge/KiCad-10%20%28kicad--cli%29-5B6B7A?style=flat-square)
![ngspice](https://img.shields.io/badge/ngspice-46-5B6B7A?style=flat-square)
![rule](https://img.shields.io/badge/hardware%2F%20%26%20bom%2F-generated%2C%20never%20edited-0B1F33?style=flat-square)

---

> [!IMPORTANT]
> **Python is the source of truth.** Everything in `hardware/` and `bom/` is written by a script; a hand edit is lost
> on the next build and breaks the netlist identity check. Change the generator, then rebuild
> ([CLAUDE.md](../../CLAUDE.md), [REQUIREMENTS.md §6](../requirements/REQUIREMENTS.md#6-working-rules)).

## 🧰 Set up

```bash
python3.12 -m venv .venv
.venv/bin/pip install numpy scipy matplotlib pyelftools
.venv/bin/python docs/fetch.py              # third-party documents: not in git, needed by the BOM check
```

| Tool | Version | Used by |
|---|---|---|
| Python | 3.12 with numpy, scipy, matplotlib, pyelftools | everything |
| KiCad | 10 — `kicad-cli` (macOS: `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`) | ERC, netlist export, PDF |
| ngspice | 46, on `PATH` | the decks in `sim/spice/` |
| PyOpenMagnetics | 1.7.33, optional | `sim/magnetics.py` only; no macOS-arm64 wheel — built from source, procedure in [magnetics report §1](../../sim/out/magnetics/report.md) |

## ⚡ Build a board

One generator per board. A run writes the KiCad project, runs every check and writes the BOM:

```bash
.venv/bin/python gen/pv_power.py    # one board (here two: PV-PWR and PV-PWR-4)
.venv/bin/python gen/build_all.py   # every maintained board, then the module BOMs
```

| Generator | KiCad projects | Role |
|---|---|---|
| `gen/pv_power.py` | PV-PWR, PV-PWR-4 | cost-first power board, 3 and 4 phases |
| `gen/pv_ctrl.py` | PV-CTL (BOM variant PV-CTL-P75) | cost-first control board |
| `gen/pcs_power.py` | PCS-PWR | inverter power board, three-wire: three two-level legs, split DC link, lean DC port at 250 A, AC contactors, LCL chassis parts; reads `sim/out/pcs_design/pcs_spec.json` and the magnetics design files at build time |
| `gen/pcs_ctrl.py` | PCS-CTL (BOM variant PCS-CTL-3W) | inverter control board: a re-valued variant of PV-CTL with its own pin plan, `gen/data/pcs_ctrl_pin_plan.csv` |
| `gen/pvcell.py` | PVCELL-25 | earlier platform: one 25 kW cell |
| `gen/port.py` | PV-PORT, PV-PORT-180; with `lean`: PORT-LEAN, PORT-LEAN-HOLD | earlier platform port board; verification boards of the lean port |
| `gen/ctrl_c2000.py` · `gen/sys_io_aux.py` | CTRL-C2000 · SYS-IO-AUX | earlier platform controller card and system I/O |
| `gen/aux_hv.py` · `gen/bmu_gw.py` · `gen/gdrv.py` | AUX-HV · BMU-GW · GDRV-HB | earlier platform auxiliary supply, BMS gateway, gate-drive card |
| `gen/dab60.py` | DAB60 | **frozen**: rev B outputs kept, generator not maintained ([build_all.py](../../gen/build_all.py)) |

Each board's outputs land in `hardware/<board>/outputs/`: the schematic PDF, the netlist (`.net` and XML), the ERC
result, `<board>_report.json` (the checks below), `<board>_design_check.txt` (the board's own calculations) and the
symbol pin list used by the pin audit. Live status of every board: [Overview › Status](01-overview.md#-status).

### What every build checks

| Check | Passes when | Can it be waived? |
|---|---|---|
| ERC (`kicad-cli`, all severities) | no unwaived violation | a *warning* type, with a written reason; errors never |
| Netlist identity | the exported KiCad netlist equals the Python design table, net by net | no |
| Isolation domains | only declared isolators and deliberate crossings touch two domains | no |
| Part stress (jellybean parts) | capacitors ≤ 80 % of rated voltage; resistors ≤ 60 % of rated power and within their working voltage | no |
| PDF | the schematic renders | no |
| BOM completeness | every line has maker + MPN + datasheet on file, or is GENERIC / RFQ / CUSTOM / NOPART | no |
| Board design check | the board's own `design_check()`: trip thresholds, timing, ratings, loads across connectors | it fails the build |

Details of the writer and the `Builder` API: [gen/README.md](../../gen/README.md). What the checks do **not** prove —
layout, parasitics, real component behaviour — is in [Verification](08-verification.md).

## 🔬 Run a simulation

```bash
.venv/bin/python sim/pv_design.py        # writes sim/out/pv_design/ (report.md, cell_spec.json, plots)
```

| Script | Output folder | What it decides |
|---|---|---|
| `sim/pv_tradeoff.py` | `pv_tradeoff/` | PV cell topology (Gate-0 and its re-run with Asian devices) |
| `sim/pv_design.py` · `sim/pv_module.py` | `pv_design/` | cell power stage; module losses, thermal, derating, noise |
| `sim/pv_control.py` · `sim/pv_mppt.py` | `pv_control/` · `pv_mppt/` | control loops, protection timing; MPPT |
| `sim/port_design.py` | `port_design/` | DC ports: precharge, coordination, sensing, insulation monitor |
| `sim/aux_hv_design.py` | `aux_hv_design/` | auxiliary flyback (30 W earlier, 75 W cost-first) |
| `sim/gdrv_miller.py` | `gdrv_miller/` | false turn-on check of the gate-drive channel |
| `sim/magnetics.py` | `magnetics/` | three-way magnetics verification; constructions and specifications |
| `sim/insulation.py` | `insulation/` | insulation coordination and the barrier audit |
| `sim/dab_design.py` · `sim/dab_control.py` | `dab_design/` · `dab_control/` | DAB-D60 power stage, derating map; control |
| `sim/pcs_design.py` | `pcs_design/` | PCS-P125 power stage |
| `sim/pcs_tradeoff.py` | `pcs_design/tradeoff.md` | PCS-P125 stage and filter re-optimisation with the designed inductors (D-059, corrected by D-060) |
| `sim/pcs_crosscheck.py` | `pcs_design/crosscheck_wolfspeed.md` | independent check of the inverter topology against Wolfspeed's reference designs (D-057) |
| `sim/compare_megarevo.py` | `compare_megarevo/` | the row-by-row comparison with the PMD-75-G3 |
| `sim/pv_devices.py` · `sim/dab_devices.py` · `sim/pcs_devices.py` | `audit/` | device data with datasheet page references; device audits |

Every report states what is calculated, what is simulated and what is assumed. The spec JSON files are the hand-off:
generators read them, so a design value has one source.

## 💰 Re-roll the BOMs and the cost

```bash
.venv/bin/python gen/build_all.py --bom   # module BOMs from the board BOMs (no rebuild)
.venv/bin/python gen/cost.py              # bom/*_costed_BOM.csv and bom/COST.md
.venv/bin/python docs/assets/figures_overview.py   # charts and generated tables of these pages
```

`gen/cost.py` prices each line from [`gen/data/prices.csv`](../../gen/data/prices.csv) (looked-up prices with URL and
date), [`cost_estimates.csv`](../../gen/data/cost_estimates.csv) (estimates with their basis) and
[`volume_factors.csv`](../../gen/data/volume_factors.csv) (5,000-unit factors). A line with neither price nor estimate is
listed as UNPRICED and never counted as zero. The script exits with code 1 if its self-check fails.
[Sourcing and cost](07-sourcing-and-cost.md) explains the method.

## 🔬 Audits

| Command | What it proves |
|---|---|
| `.venv/bin/python gen/pin_audit.py` | every drawn pin table equals a ledger transcribed from the datasheet by someone who had not seen the drawing (`list` writes the parts list) |
| `.venv/bin/python gen/temp_audit.py` | every orderable part against the −30…+60 °C ambient requirement (hot limit 85 °C inside the enclosure) |
| `.venv/bin/python gen/check_interfaces.py` | the cables of the earlier platform's modules: pin map, driver direction, power. The cost-first board-to-board contract (`PC`) is checked inside the PV-PWR and PV-CTL design checks, and the inverter's `PCS_PC` and `PCS_X` inside the PCS-PWR and PCS-CTL design checks |
| `.venv/bin/python sim/insulation.py` | every barrier part against the insulation requirements |
| `.venv/bin/python sim/magnetics.py` | every magnetic part three ways; exits 1 if a check fails |

Review findings of the independent design reviews are kept in `gen/data/review_*.csv` and
`gen/data/integration_findings.csv` ([D-023](../requirements/DECISIONS.md)).

## 📐 Conventions

### Adding a part

1. **Datasheet first.** Save the maker's PDF as `docs/datasheets/<category>/<MPN>.pdf` (maker's own site, no login) and
   add its row to the manifests ([docs/LIBRARY.md](../LIBRARY.md), "How to add a document").
2. **Pin table from the PDF, never from memory** (`pdftotext -layout`), with the document number and page in a comment.
3. **Catalog entry** in the board script; a part shared by several boards goes into `gen/catalog.py`.
4. **Real orderable MPN**, or `sourcing="CUSTOM"` / `"RFQ"` / `"GENERIC"` with the full specification in the description.
5. **Price**: a row in `gen/data/prices.csv` (source, quantity break, date) or an estimate with its basis in
   `gen/data/cost_estimates.csv`.
6. **Pin ledger** row for the pin audit, transcribed independently.

### Adding a board

1. Copy the pattern of [`gen/bmu_gw.py`](../../gen/bmu_gw.py); the `Builder` API is in [gen/README.md](../../gen/README.md).
2. Every pin of every symbol gets a net or `None`; a net with one pin is an ERC error.
3. Declare the isolation domains, isolators and crossings; give `vrange()` for the part-stress check.
4. Inter-board connectors come from `gen/interfaces.py` — both ends import the same table.
5. Register the board in `BOARDS` and its module in `MODULES` of [`gen/build_all.py`](../../gen/build_all.py).

### Scope rules

A task that does not trace to [REQUIREMENTS.md](../requirements/REQUIREMENTS.md) is a diversion — ask first. No PCB
layout, footprints, Gerbers or mechanics. Nothing is presented as measured.

## 📚 The reference library

Third-party documents (datasheets, reference designs, competitor files) are **not redistributed** in this
repository; [`docs/SOURCES.csv`](../SOURCES.csv) lists every file with its URL and SHA-256, and our own notes
(READMEs, `spec.md`) stay in git.

| Command | What it does |
|---|---|
| `.venv/bin/python docs/fetch.py` | downloads every missing file marked OK with a plain request and keeps it only if its SHA-256 matches the manifest |
| `.venv/bin/python docs/fetch.py --check` | verifies what is on disk; also flags a file without a manifest row, a path listed twice, and a by-hand file that is present |
| `.venv/bin/python docs/fetch.py --summary` | regenerates the count tables of [docs/LIBRARY.md](../LIBRARY.md) |

Files that sites refuse to serve to a script are marked MANUAL and must be saved by hand — the list and the current
counts are in [docs/LIBRARY.md](../LIBRARY.md). Never move or rename a library file without first searching `gen/`,
`sim/`, `bom/` and `hardware/` for its path.

## 🧰 The documentation itself

| Item | Where |
|---|---|
| Style guide (voice, page skeleton, palette, Mermaid init block) | [docs/assets/STYLE.md](../assets/STYLE.md) |
| Charts of the landing, cost, comparison and status pages | [`docs/assets/figures_overview.py`](../assets/figures_overview.py) → `docs/assets/img/` |
| Charts of the design pages | [`docs/assets/figures_design.py`](../assets/figures_design.py) |
| Generated tables | between `<!-- BEGIN:name -->` and `<!-- END:name -->` in the pages; rewritten by the figure script — do not edit by hand |

Run the figure scripts after `gen/cost.py`, a board build or `sim/compare_megarevo.py`; they read the data at run time,
fail loudly if a data file or a marker is missing, and finish with a self-check.

---

<!-- footer --> ← [Risks and open items](12-risks-and-open-items.md) · [Documentation index](../README.md) · [Glossary](glossary.md) →
