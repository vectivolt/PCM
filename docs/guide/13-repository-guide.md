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
| `gen/pv_ctrl.py` | PV-CTL (BOM variant PV-CTL-P75) | cost-first control board; reads the PV-PWR design check as built and refuses a stale copy (hash stamp) |
| `gen/pcs_power.py` | PCS-PWR, PCS-PWR-4W | inverter power board, three-wire and four-wire: two-level legs (four with the neutral leg), split DC link, lean DC port at 250 A, AC contactors (3- or 4-pole), the AC start-up tap and AC precharge, LCL chassis parts; reads `sim/out/pcs_design/pcs_spec.json` and the magnetics design files at build time |
| `gen/pcs_ctrl.py` | PCS-CTL (BOM variants PCS-CTL-3W and PCS-CTL-4W) | inverter control board: a re-valued variant of PV-CTL with the Ethernet bridge fitted and its own pin plan, `gen/data/pcs_ctrl_pin_plan.csv` (four-wire view `gen/data/pcs_ctrl_pin_plan_4w.csv`, written on each build); reads the control study's sampling plan (`sim/out/pcs_control/pcs_control_spec.json`) and the PCS-PWR and PCS-PWR-4W checks as built (refuses a stale copy) |
| `gen/pvcell.py` | PVCELL-25 | earlier platform: one 25 kW cell |
| `gen/port.py` | PV-PORT, PV-PORT-180; with `lean`: PORT-LEAN, PORT-LEAN-HOLD | earlier platform port board; verification boards of the lean port |
| `gen/ctrl_c2000.py` · `gen/sys_io_aux.py` | CTRL-C2000 · SYS-IO-AUX | earlier platform controller card and system I/O |
| `gen/aux_hv.py` · `gen/bmu_gw.py` · `gen/gdrv.py` | AUX-HV · BMU-GW · GDRV-HB | earlier platform auxiliary supply, BMS gateway, gate-drive card |
| `gen/dab60.py` | DAB60 | **frozen**: rev B outputs kept, generator not maintained ([build_all.py](../../gen/build_all.py)); the DAB study in `sim/out/dab_design/` is not this board ([D-068](../requirements/DECISIONS.md)) |

**Frozen inputs of the earlier platform.** `gen/ctrl_c2000.py` (CTRL-C2000) and `gen/pvcell.py` (PVCELL-25) read frozen
snapshots of the specs they were designed to — [gen/data/control_spec_platform1.json](../../gen/data/control_spec_platform1.json)
and [gen/data/cell_spec_platform1.json](../../gen/data/cell_spec_platform1.json), the specs of commit `033d8d8` — because
the live `sim/out/pv_control/control_spec.json` and `sim/out/pv_design/cell_spec.json` now describe the cost-first boards
([D-065](../requirements/DECISIONS.md)). Like the frozen DAB60 outputs, they are not re-derived. The loaders the review
read fail closed (PCM-23): a missing or stale input stops the run instead of falling back to a built-in value.

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
| `sim/pv_control.py` · `sim/pv_mppt.py` | `pv_control/` · `pv_mppt/` | control loops on the drawn boards, protection timing, the delivered-power envelope (`envelope.csv`); MPPT |
| `sim/port_design.py` | `port_design/` | DC ports: precharge, coordination, sensing, insulation monitor |
| `sim/aux_hv_design.py` | `aux_hv_design/` | auxiliary flyback (30 W earlier, 75 W cost-first) |
| `sim/gdrv_miller.py` | `gdrv_miller/` | false turn-on check of the gate-drive channel |
| `sim/magnetics.py` | `magnetics/` | three-way magnetics verification; constructions and specifications |
| `sim/insulation.py` | `insulation/` | insulation coordination and the barrier audit |
| `sim/dab_design.py` · `sim/dab_control.py` | `dab_design/` · `dab_control/` | DAB-D60 device study (not the drawn DAB60 rev B0): power stage, admitted map, firmware rules FW-DAB-1…11; control |
| `sim/pcs_design.py` | `pcs_design/` | PCS-P125 power stage, DC link, operating envelope; the four-wire build, the AC-side start-up and the declarations (section i); firmware hand-over (30 rows) |
| `sim/pcs_control.py` | `pcs_control/` | PCS-P125 control: current loop, PLL, DC-link loop, grid forming, THDi estimate, sampling plan and protection state machine ([D-066](../requirements/DECISIONS.md)); off-grid restoration and accuracy, THDu, imbalance, the ride-through limiter and the neutral-leg loop ([D-077](../requirements/DECISIONS.md)) |
| `sim/pcs_tradeoff.py` | `pcs_design/tradeoff.md` | PCS-P125 stage and filter re-optimisation with the designed inductors (D-059, corrected by D-060) |
| `sim/pcs_crosscheck.py` | `pcs_design/crosscheck_wolfspeed.md` | independent check of the inverter topology against Wolfspeed's reference designs (D-057) |
| `sim/compare_megarevo.py` | `compare_megarevo/` | the row-by-row comparison of PV-P75 with the PMD-75-G3 |
| `sim/compare_megarevo_pcs.py` | `compare_megarevo_pcs/` | the row-by-row comparison of PCS-P125 and PCS-P125-4W with the PMA0125 (columns `megarevo_pma0125_published`, `pcs_p125_calculated`, `pcs_p125_4w_calculated`, `verdict`, `evidence`; one verdict per row) |
| `sim/pv_devices.py` · `sim/dab_devices.py` · `sim/pcs_devices.py` | `audit/` | device data with datasheet page references; device audits |

Every report states what is calculated, what is simulated and what is assumed. The spec JSON files are the hand-off:
generators read them, so a design value has one source. For the inverter the order is `sim/pcs_design.py` →
`sim/pcs_control.py` → `gen/pcs_power.py` → `gen/pcs_ctrl.py`: the control board reads the control study's sampling
plan, and the study takes the sensing chain (current-chain delay, divider poles) from the two boards' design checks.

### Regeneration order

After a change to a study, the whole chain in this order (as run on 2026-10-06):

```bash
.venv/bin/python sim/aux_hv_design.py      # only when the auxiliary allocations change (about 4 min)
.venv/bin/python sim/pv_design.py          # about 6 min
.venv/bin/python sim/pv_module.py          # module losses, thermal, standby - read by sim/compare_megarevo.py
.venv/bin/python sim/pv_control.py
.venv/bin/python sim/pv_mppt.py
.venv/bin/python sim/pcs_design.py
.venv/bin/python sim/pcs_control.py
.venv/bin/python sim/magnetics.py          # the PCS filter and auxiliary transformer designs the generators read
.venv/bin/python gen/build_all.py          # every maintained board and its variants (about 5 min), then the module BOMs
.venv/bin/python gen/port.py lean          # PORT-LEAN and PORT-LEAN-HOLD, which build_all does not build
.venv/bin/python gen/build_all.py --bom    # module BOMs from the board BOMs
.venv/bin/python gen/cost.py               # costed BOMs and bom/COST.md
.venv/bin/python sim/compare_megarevo.py       # PV-P75 against the PMD-75-G3
.venv/bin/python sim/compare_megarevo_pcs.py   # PCS-P125 / PCS-P125-4W against the PMA0125
.venv/bin/python gen/pin_audit.py
.venv/bin/python gen/temp_audit.py
.venv/bin/python sim/insulation.py
.venv/bin/python docs/assets/figures_overview.py
.venv/bin/python docs/assets/figures_design.py
```

Exit codes that are reports, not failures: both compare scripts exit 1 while a row is BELOW or PENDING (today: 3 PV rows
and 4 PCS rows below); `gen/temp_audit.py` exits 1 while a part has no audit row; `sim/insulation.py` exits 1 on its known
problems (102 today, risk B10); `sim/magnetics.py` exits 0 but prints the known MG-15 FAIL (risk D9). Read their output
diffs, not their exit codes. Long runs stall if the computer sleeps: keep it awake for the multi-minute scripts.

## 💰 Re-roll the BOMs and the cost

```bash
.venv/bin/python gen/build_all.py --bom   # module BOMs from the board BOMs (no rebuild)
.venv/bin/python gen/cost.py              # bom/*_costed_BOM.csv and bom/COST.md
.venv/bin/python docs/assets/figures_overview.py   # charts and generated tables of these pages
```

`gen/cost.py` prices each line from [`gen/data/prices.csv`](../../gen/data/prices.csv) (looked-up prices with URL and
date), [`cost_estimates.csv`](../../gen/data/cost_estimates.csv) (estimates with their basis) and
[`volume_factors.csv`](../../gen/data/volume_factors.csv) (5,000-unit factors). The supplier surveys behind the price
rows are in `sim/data/`: `asia_*.md` and the LCSC sourcing pass [lcsc_semis.md](../../sim/data/lcsc_semis.md) /
[lcsc_semis.csv](../../sim/data/lcsc_semis.csv) (every transistor and diode position, stock and price ladders read on
2026-10-05, [D-069](../requirements/DECISIONS.md)) — re-read the LCSC pages before ordering. A line with neither price nor estimate is
listed as UNPRICED and never counted as zero. The script exits with code 1 if its self-check fails.
[Sourcing and cost](07-sourcing-and-cost.md) explains the method.

## 🔬 Audits

| Command | What it proves |
|---|---|
| `.venv/bin/python gen/pin_audit.py` | every drawn pin table equals a ledger transcribed from the datasheet by someone who had not seen the drawing (`list` writes the parts list) |
| `.venv/bin/python gen/temp_audit.py` | every orderable part against the −30…+60 °C ambient requirement (hot limit 85 °C inside the enclosure) |
| `.venv/bin/python gen/check_interfaces.py` | the cables of the earlier platform's modules: pin map, driver direction, power. The cost-first board-to-board contract (`PC`) is checked inside the PV-PWR and PV-CTL design checks, and the inverter's `PCS_PC` and `PCS_X` inside the PCS-PWR and PCS-CTL design checks |
| `.venv/bin/python sim/insulation.py` | every barrier part of the earlier platform's boards against the insulation requirements — it has no domain map for PV-CTL, PCS-PWR, PCS-PWR-4W, PCS-CTL, PORT-LEAN and PORT-LEAN-HOLD and currently stops with 98 problems ([08 · Verification](08-verification.md#-audits)); those boards check their own barrier parts in their design checks |
| `.venv/bin/python sim/magnetics.py` | every magnetic part three ways; exits 1 if a check fails |

Review findings of the independent design reviews are kept in `gen/data/review_*.csv` and
`gen/data/integration_findings.csv` ([D-023](../requirements/DECISIONS.md)).
[review_pcm.csv](../../gen/data/review_pcm.csv) (27 findings of the review of commit `033d8d8`) added status, class and
closure columns; its dispositions are decisions D-065 to D-069 and D-072.
[review_r2.csv](../../gen/data/review_r2.csv) (14 findings of the re-check of commit `a427981`, severities high /
medium) has the same columns; its dispositions are [D-078](../requirements/DECISIONS.md) and
[D-079](../requirements/DECISIONS.md). [review_r3.csv](../../gen/data/review_r3.csv) (5 findings of the re-check of
commit `615e4b5`, severities high / low) has them too; its disposition is [D-080](../requirements/DECISIONS.md). The
latest, [review_r4.csv](../../gen/data/review_r4.csv) (the economical re-check of commit `845131d`: 12 decision areas
E01–E12, severities high / medium / low), keeps the same status, classification and closure columns; each area's
preferred option, alternative and no-change boundary are in its finding text, its cost basis in the evidence column and
the boundary that may not be waived in the recommendation column; its disposition is [D-081](../requirements/DECISIONS.md).

### Records added on 2026-10-06

| File or folder | What it is | Written by |
|---|---|---|
| [gen/data/megarevo_2026_parity.csv](../../gen/data/megarevo_2026_parity.csv) | the module-parity register against Megarevo (32 rows: area, item, their published statement, ours, verdict, action) | by hand per decision ([D-073](../requirements/DECISIONS.md) to [D-077](../requirements/DECISIONS.md)); rendered on [11 · Comparison](11-megarevo-comparison.md#-module-parity-register) by `figures_overview.py` |
| [gen/data/pcs_ctrl_pin_plan_4w.csv](../../gen/data/pcs_ctrl_pin_plan_4w.csv) | the four-wire view of the inverter controller's pin plan | `gen/pcs_ctrl.py` |
| [hardware/PCS-PWR-4W/](../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt) | the four-wire power board: KiCad project, PDF, netlist, ERC, report, design check | `gen/pcs_power.py` |
| [bom/PCS-PWR-4W_BOM.csv](../../bom/PCS-PWR-4W_BOM.csv), [bom/PCS-CTL-4W_BOM.csv](../../bom/PCS-CTL-4W_BOM.csv), [bom/PCS-P125-4W_module_BOM.csv](../../bom/PCS-P125-4W_module_BOM.csv), [bom/PCS-P125-4W_costed_BOM.csv](../../bom/PCS-P125-4W_costed_BOM.csv) | the four-wire board, assembly, module and costed BOMs | the generators, `gen/build_all.py --bom`, `gen/cost.py` |
| [sim/compare_megarevo_pcs.py](../../sim/compare_megarevo_pcs.py) → [sim/out/compare_megarevo_pcs/](../../sim/out/compare_megarevo_pcs/report.md) | the PCS comparison script and its `comparison.csv` / `report.md` | the script |
| [docs/reference-designs/megarevo/pma/spec-pma0125.md](../reference-designs/megarevo/pma/spec-pma0125.md), [MANUAL-NOTES.md](../reference-designs/megarevo/pma/MANUAL-NOTES.md) | the PMA0125 datasheet table transcribed verbatim; the PMA user manual's module-level statements with page numbers | by hand from the fetched PDFs |
| `docs/reference-designs/megarevo/catalogue-2026/` | Megarevo's 2026 catalogue V1.2 (module pages used) — the PDF is a manifest row in [docs/SOURCES.csv](../SOURCES.csv), fetched, not kept in git | `docs/fetch.py` |

Added on 2026-10-07: [requirements/INSTALLATION.md](../requirements/INSTALLATION.md) collects the installation conditions the modules depend on, one row per requirement with the generated line it comes from; it is written by hand from the design checks, so its quoted lines are re-verified after a rebuild (done after the rebuild of D-078 / D-079, again after that of D-080 and again after that of D-081: 77 rows). [gen/data/review_r2.csv](../../gen/data/review_r2.csv) holds the re-check R2; new keys of the hand-offs: `protection_chain` in `pcs_spec.json` (the trip chain at its gates-off current, the L1 trajectory, the backup-band requirement), `tolerance_regression`, `vf_bounded`, `dc_rejection_bounded`, `ride_through_rule`, `four_wire_coupled`, `ac_start_state_machine` and `review_r2` in `pcs_control_spec.json`. Added the same day by the re-check R3 ([D-080](../requirements/DECISIONS.md)): [gen/data/review_r3.csv](../../gen/data/review_r3.csv); `commutation_and_gate_drive.desat.rds_acceptance.grade_tier_tables`, `declarations.tiers_by_grade` and `grid_tie_start` in `pcs_spec.json`; `sync_close_state_machine`, `four_wire_short_protection`, `dc_bus_coupled_pv`, `vf_envelope_basis` and `review_r3` in `pcs_control_spec.json`.

### Records added on 2026-10-07 by the re-check R4

| File or key | What it is | Written by |
|---|---|---|
| [gen/data/review_r4.csv](../../gen/data/review_r4.csv) | the register of the economical re-check R4: twelve decision areas with status, classification and closure | by hand per review round ([D-081](../requirements/DECISIONS.md)) |
| [docs/requirements/RFQ-MAGNETICS.md](../requirements/RFQ-MAGNETICS.md) | the request-for-quotation index for the ten custom magnetic parts: quantities per module and build, the rows a quotation must hold, type and production tests, the estimate held, 18 open rows, the 48 kHz variant as an appendix | by hand from the winding sheets and the costed BOMs; re-read after a magnetics or BOM rebuild |
| `docs/datasheets/power-semiconductors/BYG23T.pdf` | Vishay BYG23T-M3 datasheet (doc 89429), the new DESAT diode — a manifest row in [docs/SOURCES.csv](../SOURCES.csv) and [docs/datasheets/SOURCES.csv](../datasheets/SOURCES.csv), fetched, not kept in git; its price rows (LCSC C145454) in `gen/data/prices.csv`, its pin-ledger rows in `gen/data/pin_ledger_E.csv` | `docs/fetch.py` |
| `BYG23T`, `DESAT_1700` and `STRING_ENV` in [gen/gdrv.py](../../gen/gdrv.py) | the diode's catalog entry; the 1,700 V-class string preset (two diodes, the V<sub>F</sub> band); the reverse-voltage envelope every 1,700 V board check is asserted against (1,150 V static, 1,445 V peak) | the generator |
| `CM_RING` in [gen/pv_power.py](../../gen/pv_power.py) | the port CM ring's BOM line, now the flat-permeability grade as an RFQ item keyed by the size code N-C-644025 | the generator |
| `commutation_and_gate_drive.desat.string_envelope`, `thermal_and_losses.cold_start` in `pcs_spec.json`; `dc_bus_cable`, `dc_bus_operating_window`, `vf_envelope_basis.statement` / `release_sequence` / `not_claimed` and `review_r4` in `pcs_control_spec.json` | the DESAT reverse-voltage states, the inverter's passive cold-start table, the cable model, the enforced DC-bus window with the harness contract, the envelope's capability statement and release sequence, the study's R4 dispositions | `sim/pcs_design.py`, `sim/pcs_control.py` |

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
counts are in [docs/LIBRARY.md](../LIBRARY.md), which `docs/fetch.py --summary` writes (do not edit it by hand). Never
move or rename a library file without first searching `gen/`, `sim/`, `bom/` and `hardware/` for its path.

**Firmware packages of reference designs** (the Wolfspeed CRD200DA23N-GMA, CRD60DD12N-GMB and CRD-60DD12N-K,
[D-071](../requirements/DECISIONS.md)) have their manifest rows like any other file and are unpacked in a `firmware/`
folder next to the design, which git ignores. Our static reading of each package lives beside its README as
`FIRMWARE-NOTES.md` (every number labelled read / calculated / inferred, with file and line); what they change for our
firmware is [REFERENCE-LESSONS.md §6](../requirements/REFERENCE-LESSONS.md). Nothing in them was built or run.

## 🧰 The documentation itself

| Item | Where |
|---|---|
| Style guide (voice, page skeleton, palette, Mermaid init block) | [docs/assets/STYLE.md](../assets/STYLE.md) |
| Charts of the landing, cost, comparison and status pages | [`docs/assets/figures_overview.py`](../assets/figures_overview.py) → `docs/assets/img/` |
| Charts of the design pages | [`docs/assets/figures_design.py`](../assets/figures_design.py) |
| Generated tables | between `<!-- BEGIN:name -->` and `<!-- END:name -->` in the pages; rewritten by the figure script — do not edit by hand |

Run the figure scripts after `gen/cost.py`, a board build or either compare script; they read the data at run time,
fail loudly if a data file or a marker is missing, and finish with a self-check. `figures_overview.py` writes the blocks
`cost-summary` (README, 07), `board-status` (01), `megarevo-score`, `megarevo-table`, `megarevo-pcs-score`,
`megarevo-pcs-table` and `megarevo-parity` (11), and the scorecards `megarevo_scorecard.png` and
`megarevo_pcs_scorecard.png`.

---

<!-- footer --> ← [Risks and open items](12-risks-and-open-items.md) · [Documentation index](../README.md) · [Glossary](glossary.md) →
