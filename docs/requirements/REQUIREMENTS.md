# DC-DC platform — requirements (scope lock)

**Status:** baseline 2026-10-03, amended 2026-10-04 (§7 sourcing, cost, magnetics, budget; §8 DC-to-AC PCS, 5,000-unit cost basis) · **Owner:** Chinmoy Bhuyan · **Source of truth:** [00-roadmap-source.md](00-roadmap-source.md)

This file is what the project is measured against. If a task is not traceable to a line here, it is a
diversion: stop and ask. Changes to this file are made only on the user's instruction and are logged in
[DECISIONS.md](DECISIONS.md).

Legend: **R** = taken from the roadmap source (section quoted) · **A** = our assumption/default where the
source is silent — open to change, never to be presented as a customer requirement.

---

## 1. Scope

### In scope

| ID | Item |
|---|---|
| S-1 | **PV-P75** — 75 kW non-isolated bidirectional buck-boost DC/DC with MPPT, built from 3 × `PVCELL-25` |
| S-2 | **PV-P100 / PV-P110** — 4 × `PVCELL-25` / 4 × `PVCELL-27.5` derivative of S-1 (same cell, same boards) |
| S-3 | **DAB-D60** — 60 kW isolated bidirectional dual-active-bridge DC/DC |
| S-4 | **Ecosystem boards** shared by S-1…S-3: `CTRL-C2000`, `SYS-IO-AUX`, `GDRV` gate-driver card, `AUX-HV` bootstrap supply, `BMU-GW` gateway, port sensing, precharge/discharge |
| S-5 | **Schematics + BOM** for every board in S-1…S-4, generated from Python |
| S-6 | **Simulations** in Python (numpy/scipy, ngspice decks driven from Python) that justify every design value |
| S-7 | **Reference library** — reference designs and datasheets under `docs/` |
| S-8 | **DC-to-AC PCS** — three-phase bidirectional battery inverter, added by the owner on 2026-10-04 ("we need both dc dc and dc to ac"); baseline PCS-P125 (§8) |

### Out of scope (do not start without an explicit instruction)

- PCB layout, Gerbers, footprints, stack-ups, mechanical CAD, busbar drawings.
- STS-250 and cabinets. (The AC-side PCS came INTO scope on 2026-10-04 — see §8.)
- The existing 30/40/50 kW Vienna + LLC modules (separate repo `DC Modules/power-module-platform`).
- Firmware. (The controller *pin plan* is part of the schematic and is in scope.)
- Procurement actions, teardown purchases, certification.

---

## 2. PV-P75 / PV-P100 / PV-P110 (non-isolated)

Target = Megarevo PMD-75-G3 (roadmap §"MPPT target").

| ID | Requirement | Value | Src |
|---|---|---|---|
| PV-01 | Rated / maximum power | 75 kW / 82.5 kW | R |
| PV-02 | MPPT | 1 centralized tracker (one supervisor over all cells) | R |
| PV-03 | Port A (PV) operating voltage | 250–1000 V | R |
| PV-04 | Port A full-load window | 550–950 V | R |
| PV-05 | Port A maximum current | 135 A | R |
| PV-06 | Port B (bus/battery) operating voltage | 250–1000 V | R |
| PV-07 | Port B full-load window | 550–950 V | R |
| PV-08 | Port B maximum current | 135 A | R |
| PV-09 | Isolation | none (non-isolated) | R |
| PV-10 | Conversion | buck **and** boost — either port may be above or below the other | R |
| PV-11 | Peak efficiency target | ≥ 99 % | R |
| PV-12 | Cooling | temperature-controlled forced air | R |
| PV-13 | Envelope (information only, no mechanics here) | 550 × 444 × 133 mm, 30 kg | R |
| PV-14 | Communications | RS485, CAN, BMS interface | R |
| PV-15 | Cell structure | PV75 = 3 × PVCELL-25; PV100 = 4 × PVCELL-25; PV110 ≈ 4 × PVCELL-27.5 | R |
| PV-16 | Cell | non-isolated **synchronous** buck-boost, ~25–27.5 kW | R |
| PV-17 | Bidirectional power flow (BMS interface, reverse-voltage control per TIDM-BUCKBOOST-BIDIR) | yes | R |
| PV-18 | **Gate-0 topology study** must compare by device-loss and transient simulation: (a) 1700 V devices in a plain 2-level buck-boost, (b) 3-level buck-boost with lower-voltage devices, (c) series-connected / interleaved architecture | mandatory before schematic freeze | R |
| PV-19 | Power stage is our own design; Wolfspeed CRD-60DD12N for layout/interleaving lessons, TI TIDM-SOLAR-DCDC + TIDM-BUCKBOOST-BIDIR for MPPT/control | — | R |
| PV-20 | Ambient | −30…+60 °C, full power to 45 °C, derate above | R (PMD-75-G3 published table, see `docs/reference-designs/megarevo/pmd-75-g3/spec.md`) |
| PV-21 | Cells interleaved (120° for 3, 90° for 4) | yes | A |
| PV-22 | Power limit follows the 135 A current limit below 611 V (82.5 kW / 135 A) | derived | R |

### 2b. Candidate requirements — published by Megarevo for PMD-75-G3 but not carried in the roadmap

Not binding until the owner adopts them (tag **C**). The schematics *provision* for them where that is cheap.

| ID | Published row | Value | Effect on this project |
|---|---|---|---|
| PV-C1 | MPPT tracking accuracy | ≥ 99.9 % | acceptance figure for the MPPT simulation |
| PV-C2 | PV start-up voltage | 250 V | AUX-HV must start at or below 250 V |
| PV-C3 | Protection, both ports | over/under-voltage, over-current, open-circuit, short-circuit, over-temperature | sensing + trip thresholds on both ports |
| PV-C4 | Lightning protection | supported | DC surge protection on both ports |
| PV-C5 | Insulation impedance detection | supported | insulation-resistance measurement to PE on the port board |
| PV-C6 | Altitude / humidity / IP / noise | > 3000 m derating; ≤ 95 % RH non-condensing; IP20; ≤ 65 dB @ 1 m | insulation coordination and fan selection later |

## 3. DAB-D60 (isolated)

Basis = Wolfspeed CRD60DD12N-GMB (roadmap §"Isolated 60 kW DAB").

| ID | Requirement | Value | Src |
|---|---|---|---|
| DAB-01 | Rated power | 60 kW, bidirectional | R |
| DAB-02 | Topology | dual active bridge, 2 × full-bridge 1200 V SiC module (CBB011M12GM4T class) | R |
| DAB-03 | Switching frequency | 100 kHz | R |
| DAB-04 | Efficiency target | 99.2 % peak, > 98.8 % above 20 kW | R |
| DAB-05 | Operating map | full power ≈ 800 V, full power down to ≈ 700 V, derated toward 400 V — **reproduce the map ourselves** | R |
| DAB-06 | Transformer | custom; leakage inductance is part of the power-transfer function → transformer spec sheet is a deliverable | R |
| DAB-07 | Cooling | liquid cold plate | R |
| DAB-08 | Controls | ours; cross-checked against ST STSW-DABBIDIR and TI TIDA-010054 (single-phase-shift baseline) | R |
| DAB-09 | Parallel operation | 1…4 branches (+1 for N+1); branch fusing, contactors, current sharing and one-branch-fault behaviour are designed in | R |
| DAB-10 | Port 1 (DC bus side) | 590–950 V operating, 600–900 V full load | A (PCS DC envelope) |
| DAB-11 | Port 2 (battery side) | 400–900 V operating, full power ≥ 700 V | A (from DAB-05) |
| DAB-12 | Optional track — hardware only against a real customer need; schematic-level design is done here | — | R |

## 4. Ecosystem boards

| ID | Board | Requirement | Src |
|---|---|---|---|
| ECO-01 | `CTRL-C2000` | TMS320F28388D; 24 V input; isolated interfaces; ≥ 16 gate commands; ≥ 12 fast measurements; reused by PV and DAB | R |
| ECO-02 | `CTRL-C2000` | resources: 16–20 ADC channels, 4–8 SDFM channels, CAN-A (BMS), CAN-B (module bus), CAN-FD (service), Ethernet | R |
| ECO-03 | `SYS-IO-AUX` | redundant/protected 24 V input + HV bootstrap; ≥ 2 isolated CAN; ≥ 2 isolated RS485; 10/100 Ethernet; DI (E-stop, door, smoke, contactor/breaker/fan feedback); safety outputs (gate-enable chain, DC contactors, discharge); 8–12 NTC/RTD; ≥ 4 fan PWM+tach; insulation-monitor interface; service storage | R |
| ECO-04 | `SYS-IO-AUX` | hardware fault latch independent of firmware; independent hardware watchdog | R |
| ECO-05 | `GDRV` | reinforced isolated SiC gate driver, UCC21710 primary (STGAP2SICS / EiceDRIVER / NCD5709x as documented alternates); DESAT/OCP feeds a hardware trip matrix and the C2000 trip zone, never through CAN/Ethernet/supervisor | R |
| ECO-06 | `AUX-HV` | HV bootstrap supply (CRD-020DD17P-J basis, 60–1000 V input) → protected 24 V intermediate rail → isolated point-of-load supplies for drivers/control/sensors | R |
| ECO-07 | Precharge | conventional resistor + precharge contactor; TI TIDA-050063/050082 used for sizing only — their 800 V switch stage is not copied | R |
| ECO-08 | `BMU-GW` | BMS gateway (isolated CAN/RS485), not a cell monitor | R |
| ECO-09 | Sensing | busbar Hall/coreless or isolated shunt + sigma-delta | R |
| ECO-10 | Insulation basis | pollution degree 3 external / 2 internal, DC OVC II; creepage and hipot are **not** fixed from a generic table before insulation coordination is frozen | R |
| ECO-11 | Standards baseline | EN 62477-1, EN 62109-1/-2, EN IEC 61000-6-2, EN IEC 61000-6-4 | R |

## 5. Deliverables and acceptance

| ID | Deliverable | Accepted when |
|---|---|---|
| DEL-1 | Python generator per board (`gen/<board>.py`) | runs clean from a fresh checkout with `.venv` |
| DEL-2 | KiCad 10 hierarchical schematic + PDF per board (`hardware/<board>/`) | `kicad-cli` ERC has no unwaived violation; exported netlist is identical to the Python design table |
| DEL-3 | BOM per board + consolidated per module (`bom/`) | every line has manufacturer + MPN + datasheet reference, or is explicitly marked `GENERIC` (jellybean chip R/C fully specified by value, tolerance, package, rating), `RFQ` or `CUSTOM` |
| DEL-4 | Symbols pin-accurate to the datasheet in `docs/datasheets/` | pin table checked against the PDF, recorded per part |
| DEL-5 | Gate-0 topology study (PV-18) | report + script; decision logged in DECISIONS.md |
| DEL-6 | Design simulations: operating envelope, losses/efficiency map, thermal, ripple/interleaving, control loops, MPPT, DAB power-transfer/ZVS map, precharge | every design value in a schematic traces to a script in `sim/`; ngspice decks are kept |
| DEL-7 | Reference library | `docs/reference-designs/<vendor>/<design>/`, `docs/datasheets/<category>/`, manifest `docs/SOURCES.csv` |
| DEL-8 | Costed BOM per module and competitiveness report (SRC-1…SRC-3) | every orderable line has a price with its source and date; totals and cost per kW are generated by script; the comparison with Chinese market prices names its sources |
| DEL-9 | Magnetics verification report and one specification sheet per magnetic part (MAG-1, MAG-2) | OpenMagnetics result, our own calculation and the reference-design figure side by side for each part; every disagreement above the stated tolerance is explained |

## 6. Working rules

1. **Schematic and BOM only.** No PCB layout work of any kind.
2. **Python is the source.** KiCad files, PDFs, netlists and BOMs are generated outputs — never hand-edited.
3. **Docs layout is fixed:** reference designs in `docs/reference-designs/<vendor>/<design>/`, datasheets in `docs/datasheets/<category>/`, requirements in `docs/requirements/`.
4. **Agent roles:** Fable 5.1 orchestrates and reviews; Opus 5.5 and Sonnet 5.5 subagents do the work.
5. **References remove unknowns, they do not set ratings.** No 800/900 V reference stage is copied into a 950/1000 V product unchanged.
6. **Honesty boundary.** Nothing here is bench-validated. Simulated and calculated results are labelled as such; unknowns are listed, not hidden.

## 7. Sourcing, cost and magnetics (owner instruction, 2026-10-04)

The owner's words: "Use Asian/Chinese switches etc. so that we can compete in the market … check LCSC and proper
verified and reliable SiC / MOSFETs / diodes / drivers if possible … use OpenMagnetics for proper magnetics
verification, but don't trust it blindly … also compare with the reference designs' magnetics … (TI MCU is okay) …
check `stsw-dabbidir.zip` for the ST files … check out TI new designs like TIDA-010938 … make sure that our system is
reliable, robust and economically practical and competitive with Chinese ones … and make sure that we have everything."

| ID | Requirement | Src |
|---|---|---|
| SRC-1 | Power switches (SiC MOSFETs / modules), power diodes, gate drivers and the other cost-driving parts come from Asian — preferably Chinese — manufacturers, so the products can compete on price. The TI C2000 MCU stays | R |
| SRC-2 | Each such part is "proper, verified and reliable": complete datasheet on file, documented qualification (AEC-Q101 / JEDEC or the maker's reliability report), an established maker. Availability and price are checked on LCSC where the part is listed; a part that is not on LCSC needs another documented source | R |
| SRC-3 | The products must be economically practical and competitive with Chinese products: a costed BOM per module (cost per kW) and a comparison with market prices of comparable Chinese modules | R |
| SRC-4 | Reliability and robustness are not traded for cost: the derating rules, the hardware protection and the single-fault behaviour stay | R |
| MAG-1 | Every magnetic part (PV inductor, DAB transformer and series inductor, AUX-HV transformer, common-mode chokes, any gate-drive or bias transformer of ours) is verified with OpenMagnetics, cross-checked by an independent calculation of our own, and compared with the magnetics of the reference designs. Disagreements are reported, not averaged away | R |
| MAG-2 | Magnetics are finished deliverables: one specification per part that a winding house can quote and build from (core, material, gap, turns, wire, insulation system, losses, temperature rise, test voltages) | R |
| REF-1 | The ST STSW-DABBIDIR package supplied by the owner is filed under `docs/reference-designs/st/stdes-dabbidir/` and used for the DAB cross-check (DAB-08) | R |
| REF-2 | Recent TI reference designs (TIDA-010938 and the others relevant to MPPT DC/DC, bidirectional DC/DC, DAB/CLLLC, gate drive, sensing, auxiliary supplies) are reviewed, filed, and what they change for this design is recorded | R |

| SRC-5 | **Budget (owner, 2026-10-04):** "cost is too high compared with Megarevo … we must produce it within the budget"; benchmark: Megarevo's 1500 V string PCS, 228 kW version, sells for 1,500 USD (6.6 USD/kW). The products must be designed to a cost that can compete at that level; where a roadmap feature or an earlier decision stands in the way, the budget wins (see `COST-REVIEW.md`, decision D-044) | R |

Not changed by this section: PCB layout stays out of scope; nothing is purchased (prices are looked up, not paid).

## 8. DC-to-AC PCS and the volume basis (owner instruction, 2026-10-04)

The owner's words: "we need both dc dc and dc to ac .. check for volume like 5000 qty".

| ID | Requirement | Src |
|---|---|---|
| AC-01 | A three-phase bidirectional DC-to-AC power conversion system is part of the product family and is designed here to the same depth as the DC/DC: schematic, magnetics, BOM, simulations, cost | R |
| AC-02 | Baseline = the roadmap's highest-priority PCS, **PCS-P125**, measured against Megarevo PMA0125: 125 kW (137 kW max continuous DC), DC 590–950 V (full load 600–900 V), ±250 A DC max, 400/230 V AC, 180 A rated / 216 A max, 150 kVA max, PF −1…+1 **at full current** (rated current at any power factor, including purely reactive: the competitor's sheet prints the range next to its kVA rating with no separate reactive rating; D-057), THDi < 3 %, grid-following and off-grid operation, 100 % unbalanced load in the four-wire version, overload 110 % continuous / 120 % 2 min, max efficiency 98.5 %, forced air, −30…+60 °C (roadmap §"Exact PMA0125 target") | A (my choice of baseline — the owner named no rating; P60 and P105 are derivatives) |
| AC-03 | Price benchmark: Megarevo's 1500 V string PCS, 228 kW, sells for 1,500 USD (6.6 USD/kW). The PCS is designed cost-first from the start, on the same principles as the cost-first DC/DC (`ARCHITECTURE-COSTFIRST.md`) | R |
| SRC-6 | **Cost basis: 5,000 units.** Every costed BOM is reported at the price level of a 5,000-unit build as well as at catalogue prices, with the evidence behind each volume price and the share that is only an assumption | R |
| SRC-7 | **Hardware / firmware balance (owner, 2026-10-04):** "don't try to handle everything in the hardware … offload whatever possible to the firmware to save cost … a sweet balance between hardware and software for edge cases". Discrete protection hardware is kept only where it must be faster than the controller or must work with the controller dead and the consequence is a hazard; everything else uses the controller's own hardware or firmware. Chinese module makers that offer pin-compatible versions of Western power modules (HIITIO and similar) are to be considered for the SiC and IGBT positions | R |
