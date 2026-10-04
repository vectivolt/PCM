<!-- breadcrumb --> [Home](../../README.md) › [Documentation](../README.md) › Decisions

# 📐 Decisions

> A readable digest of the decision register, D-001 to D-060, grouped by theme — what was decided, why, and whether it still holds.

![range](https://img.shields.io/badge/register-D--001%E2%80%A6D--060-0B1F33?style=flat-square)
![as of](https://img.shields.io/badge/as%20of-2026--10--05-5B6B7A?style=flat-square)
![authority](https://img.shields.io/badge/authority-DECISIONS.md-00A99D?style=flat-square)

---

> [!NOTE]
> This page summarises; **[DECISIONS.md](../requirements/DECISIONS.md) is the record** and wins wherever the two differ.
> The register never deletes a row — a later decision supersedes it. The badge shows where a decision stands today,
> taking the later rows into account; the register's own status column is left as it was written.

**Badges.**
![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) in force ·
![open](https://img.shields.io/badge/-open-E4572E?style=flat-square) awaiting a decision ·
![in progress](https://img.shields.io/badge/-in%20progress-F2A007?style=flat-square) work running ·
![recorded](https://img.shields.io/badge/-recorded-5B6B7A?style=flat-square) a finding or status, not a design choice ·
![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) holds for the full-featured platform only ·
![superseded](https://img.shields.io/badge/-superseded-8FA3B5?style=flat-square) replaced by a later decision ·
![closed](https://img.shields.io/badge/-closed-0B1F33?style=flat-square) settled by a later decision

---

## 📐 Scope, process and tools

| ID | Decision · why | Today |
|---|---|---|
| D-001 | **Scope:** the DC/DC modules PV-P75/100/110 and DAB-D60 with their shared boards; schematic + BOM + simulation only.<br/><sub>Owner instruction. The DC-to-AC PCS joined the scope with D-046.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-002 | **Toolchain:** Python 3.12 (numpy, scipy, matplotlib), KiCad 10 `kicad-cli`, ngspice 46; the schematic writer comes from an earlier in-house project.<br/><sub>Already proven against kicad-cli 10; no new dependency.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-003 | **No footprints:** every part carries a package field only.<br/><sub>PCB layout is out of scope.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-008 | **Ambient −30…+60 °C, derating above 45 °C,** re-tagged from assumption to sourced requirement.<br/><sub>The PMD-75-G3 page and datasheet state it for the competitor product itself.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-009 | **Six published PMD-75-G3 rows** the roadmap did not carry (MPPT accuracy, start-up voltage, protections, lightning, insulation detection, environment) recorded as candidate requirements.<br/><sub>Adopted as design targets by D-051.</sub> | ![closed](https://img.shields.io/badge/-closed%20by%20D--051-0B1F33?style=flat-square) |
| D-046 | **DC-to-AC PCS in scope,** baseline PCS-P125 (125 kW, 590–950 V DC, 400/230 V AC) against Megarevo PMA0125; costs also judged at 5,000 units.<br/><sub>Owner: "we need both dc dc and dc to ac .. check for volume like 5000 qty". The rating is an assumption the owner can change.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-051 | **The owner delegated the open decisions;** taken and recorded: working budgets (800 USD PV-P75, 950 USD DAB-D60), the port filter, hardware protections stay; D-009, D-017, D-020 and D-022 closed.<br/><sub>Can be overturned by the owner. Its inductor line was amended by D-054.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |

## 🧭 Architecture and protection philosophy

| ID | Decision · why | Today |
|---|---|---|
| D-044 | **Cost-first architecture** for the PV module: control on the negative DC rail behind one reinforced barrier, two boards instead of eight, self-powered, a single-core C2000, lean ports, no clamp banks, one trip latch.<br/><sub>The full-featured BOM was several times the benchmark level and trimming could not close the gap ([COST-REVIEW.md](../requirements/COST-REVIEW.md)). Supersedes D-012, D-019, D-024, D-025, D-033, D-034 and D-038 for the cost-first product.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-045 | **Cost-first specification accepted** ([ARCHITECTURE-COSTFIRST.md](../requirements/ARCHITECTURE-COSTFIRST.md)): power board + F280039C control board, one barrier, a 75 W flyback fed from both ports, one contactor per port.<br/><sub>Budget not met at the architect's estimate; the fan's temperature rating is open.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-049 | **Offload protection to the controller and firmware** instead of discrete comparators.<br/><sub>Superseded by D-050: the saving was about 17 USD per module.</sub> | ![superseded](https://img.shields.io/badge/-superseded%20by%20D--050-8FA3B5?style=flat-square) |
| D-050 | **Discrete hardware protections stay;** firmware duplicates each as a second layer and takes sequencing and rare edge cases.<br/><sub>Removing them saves about 2 %; a firmware dead time of zero would destroy the switches before DESAT could act.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-004 | **Controller TMS320F28388D** on a common CTRL-C2000 card.<br/><sub>The cost-first design uses a single-core F280039C (D-045).</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-010 | **Inter-board connector contracts** in `gen/interfaces.py`; gate commands and faults cross boards as RS-422 pairs.<br/><sub>The cost-first boards use the `PC` contract (D-045).</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-012 | **Dual-channel hardware safety chain,** no single point of failure.<br/><sub>Superseded for the cost-first product by D-044 (one latch).</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-013 | **24 V system window** 21.6–26.0 V with an over-voltage cut-off.<br/><sub>The cost-first module has no cabinet 24 V.</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-026 | **System I/O feed rules:** the HV bootstrap feeds the 24 V bus only when no cabinet feed is healthy; fans stay off until firmware enables them.<br/><sub>Review findings on the earlier platform.</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-034 | **Bootstrap-only operation:** fans, gate-drive and coil supplies forced off in hardware; black start in one ramp.<br/><sub>Superseded for the cost-first product by D-044.</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |

## ⚡ PV power stage and gate drive

| ID | Decision · why | Today |
|---|---|---|
| D-007 | **PV cell topology:** two-level four-switch buck-boost with 1700 V SiC, 32 kHz, one 224 µH inductor per cell.<br/><sub>Gate-0 study: lowest cost at every efficiency level it reaches. Confirmed with Asian devices by D-041. Open: no FIT-against-voltage curve for the device.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-037 | **Device sourcing rule:** a primary and an alternate Asian maker per switch position; the design is done for the worse of the two.<br/><sub>No Chinese maker publishes a reliability report or a short-circuit rating — recorded as a risk.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-041 | **Gate-0 re-run with Asian devices:** the cell stays two-level; primary 2 × Sichain SG2M040170HJ per position.<br/><sub>Cheaper than the best three-level option; the device set is conditional on the gate-drive items (closed in D-043 with conditions).</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-043 | **Gate drive rev 5:** NSI6651 with a clamp per gate and a short-circuit turn-off booster; InventChip is *not* an alternate; Microchip MSC035SMA170B4 is the qualified fallback.<br/><sub>Conditions: the driver's 6,250 V impulse rating needs the arrester credit; Sichain's reliability data is a release condition.</sub> | ![frozen](https://img.shields.io/badge/-frozen%2C%20with%20conditions-00A99D?style=flat-square) |
| D-005 | **Gate driver TI UCC21710.**<br/><sub>Changed to NOVOSENSE NSI6651 by D-035.</sub> | ![superseded](https://img.shields.io/badge/-superseded%20by%20D--035-8FA3B5?style=flat-square) |
| D-035 | **Gate driver NOVOSENSE NSI6651ASC:** 10 A, DESAT, reinforced with VDE 0884-17 / UL 1577 / CQC certificates, AEC-Q100 grade 1.<br/><sub>About 3 USD against 4.4 USD; its open bias-supply half was settled by D-036.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-016 | **Gate-drive bias TI UCC14241-Q1** (reinforced; certification "planned" in its datasheet).<br/><sub>The cost-first channel uses a per-phase bias transformer (D-045, D-048).</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-036 | **Bias supply stays UCC14241-Q1;** cheaper Mornsun modules only with an insulation report for 1100 V DC.<br/><sub>Closes the open half of D-035.</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-028 | **Paralleled-gate turn-off:** a PNP follower per device to its own Kelvin pin; hardware dead time 191–511 ns.<br/><sub>**Open risk:** the margin at the extreme corner rests on an estimated loop inductance — the first item for the double-pulse test.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-030 | **Inductor current by a closed-loop transducer** (LEM LA 150-P) instead of a shunt and isolated amplifier.<br/><sub>The cost-first power board uses Sinomags STK-HO/A 75 sensors.</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-027 | **Reverse-clamp rectifiers** across each DC bank, so a terminal short does not drive kiloamperes through the SiC body diodes.<br/><sub>Terminal-short survival was given up in the cost-first design (D-044).</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |

## 🛡️ DC ports, protection and insulation

| ID | Decision · why | Today |
|---|---|---|
| D-006 | **Precharge by resistor and contactor.**<br/><sub>From the roadmap; kept on the cost-first battery port.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-019 | **135 A port protection:** gPV fuses, TDK contactor, a hold-closed interlock so the fuse always clears first.<br/><sub>Superseded for the cost-first product by D-044.</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-020 | **The 180 A port is not fully coordinated.**<br/><sub>Superseded by D-033; closed by D-051.</sub> | ![closed](https://img.shields.io/badge/-closed%20by%20D--051-0B1F33?style=flat-square) |
| D-024 | **Contactor coil drive** with two independent switches in every coil-current path.<br/><sub>Superseded for the cost-first product by D-044.</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-025 | **Active discharge** inhibited while the contactor is closed, time-limited, with a thermal cut-off.<br/><sub>Superseded for the cost-first product by D-044: passive bleeders.</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-033 | **Port source types:** battery-class fuse, battery-rated arrester and two contactors in parallel for stiff sources.<br/><sub>Supersedes D-020; superseded for the cost-first product by D-044.</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-042 | **Surge protection by a monitored varistor network** on the board instead of DIN-rail arresters.<br/><sub>Reused in the cost-first design. Open: the varistors' thermal links have no DC rating.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-032 | **Insulation coordination:** reinforced between the DC side and anything touchable (8 kV impulse), basic to PE; 2000 m once the listed failures are fixed.<br/><sub>Standard values transcribed from memory; the arrester credit is not verified against the standard text.</sub> | ![frozen](https://img.shields.io/badge/-frozen%2C%20to%20be%20confirmed-00A99D?style=flat-square) |

## ⚡ Auxiliary supply and thermal

| ID | Decision · why | Today |
|---|---|---|
| D-021 | **HV bootstrap supply:** 30 W single-switch flyback on 1700 V SiC, starting at 229 V; on the DAB fed from port 1 only.<br/><sub>The cost-first module has a 75 W flyback fed from both ports (D-045).</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |
| D-022 | **The 30 W bootstrap cannot run the module at power** — it boots, communicates and shuts down.<br/><sub>Closed by D-051: the cost-first design is self-powered at 75 W.</sub> | ![closed](https://img.shields.io/badge/-closed%20by%20D--051-0B1F33?style=flat-square) |
| D-029 | **Fan San Ace 9GT1224P1S001,** −40…+85 °C, one per cell.<br/><sub>The cost-first fan is rated −10 °C — an open risk (R-05).</sub> | ![earlier platform](https://img.shields.io/badge/-earlier%20platform-5B6B7A?style=flat-square) |

## 🧲 Magnetics

| ID | Decision · why | Today |
|---|---|---|
| D-039 | **Magnetics verified three ways** (OpenMagnetics, our own calculation, the designer's figure); the magnetics engineer owns every construction.<br/><sub>The first verification found large loss gaps on the DAB transformer and series inductor.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-048 | **Cost-first magnetics:** PV inductor rev M2 (magnet wire on the same toroids), port filter = one ring plus capacitance to PE, new bias and auxiliary transformers.<br/><sub>About 11 mA into PE accepted (high-touch-current measures). The winding choice was amended by D-054.</sub> | ![amended](https://img.shields.io/badge/-amended%20by%20D--054-8FA3B5?style=flat-square) |
| D-054 | **PV inductor in solid round wire,** 145 °C hot spot at the worst point (limit 155 °C); the auxiliary transformer's SELV winding goes to litz.<br/><sub>D-048's edgewise choice rested on a tool run on the wrong conductor; the two models disagree by 27 % on the flat wire.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |

## 💰 Sourcing and cost

| ID | Decision · why | Today |
|---|---|---|
| D-031 | **Sourcing policy:** cost-driving parts from Asian, preferably Chinese, makers with documented qualification, checked on LCSC; the TI C2000 stays.<br/><sub>Owner instruction; re-opened the device and topology choices for re-runs.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-038 | **Design-to-cost targets** at 1,000 pieces (PV-P75 ≤ 2,000 USD).<br/><sub>Superseded by the 800 USD working budget of D-044.</sub> | ![superseded](https://img.shields.io/badge/-superseded%20by%20D--044-8FA3B5?style=flat-square) |
| D-040 | **Second sources for everything that is not a power device:** 107 lines studied, 21 adopted; some parts kept on purpose.<br/><sub>On the control boards this buys supply security and documentation rather than money.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-052 | **Cost of the drawn cost-first boards** recorded against the benchmark, with the next cost levers.<br/><sub>Its figures predate D-054; current figures are generated in <a href="../../bom/COST.md">bom/COST.md</a>. Budget not met; most of the total is estimate.</sub> | ![recorded](https://img.shields.io/badge/-recorded-5B6B7A?style=flat-square) |
| D-055 | **Chinese power modules stay request-for-quotation alternates;** the designs stay on paralleled discretes.<br/><sub>No candidate has a public price; break-even prices are set; the BASiC BMF008MR12E2G3 is the module to quote for the DAB.</sub> | ![recorded](https://img.shields.io/badge/-recorded-5B6B7A?style=flat-square) |
| D-056 | **PV module re-run on the drawn boards:** peak 99.47 %, full power to 45 °C inlet; a seventh battery-side film capacitor and a narrower over-current window go into the next board revisions; 32 kHz stays; the PV-side bank is not reduced.<br/><sub>Four comparison rows fall below Megarevo's published table (cold limit, altitude, standby, load rejection until the seventh capacitor); PV-P100/110 is a derated build on these boards.</sub> | ![closed](https://img.shields.io/badge/-board%20items%20closed%20by%20D--058-0B1F33?style=flat-square) |
| D-058 | **Board revisions of D-056 drawn:** PV-PWR and PV-PWR-4 rev A2, PV-CTL rev A1; battery-side film bank of the three-phase board 7 × 45 µF (315 µF), bleeder 8 × 73.2 kΩ per port, over-current window 68.9–79.9 A with the thresholds derived from each sensor's own reference output.<br/><sub>Closes the load-rejection overshoot and moves the window's lower edge from 6 % to 10.6 % above the normal peak; the 70 A target is missed by 1.1 A and accepted. Four former 24 V / 5 V pins of the board-to-board connector now carry the sensor references, and standby with both contactors held rises to 21.0 W. Open: the comparator's offset at the trip node, the reference output's drive rating and drift, and the trip chain of sim/pv_control.py.</sub> | ![frozen](https://img.shields.io/badge/-frozen%2C%20with%20conditions-00A99D?style=flat-square) |

## ⚡ DAB-D60

| ID | Decision · why | Today |
|---|---|---|
| D-011 | **The DAB-04 efficiency figures are Wolfspeed web claims;** the user guide documents less.<br/><sub>Our own efficiency map comes from `sim/dab_design.py`.</sub> | ![recorded](https://img.shields.io/badge/-noted-5B6B7A?style=flat-square) |
| D-014 | **DAB-D60 design:** turns ratio 11:12, 6.5 µH total series inductance, 150 µH magnetising, triple-phase-shift modulation, firmware flux balance.<br/><sub>Full power only inside the derating map.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |
| D-015 | **DAB efficiency reported twice:** by the loss model and by the model scaled to Wolfspeed's measurements (about 2.5 × the loss).<br/><sub>DAB-04 is not claimed; only the bench can close the gap.</sub> | ![recorded](https://img.shields.io/badge/-noted-5B6B7A?style=flat-square) |
| D-017 | **One power module per bridge,** with the thermal risk stated.<br/><sub>Closed by D-051: superseded by the paralleled-discrete design.</sub> | ![closed](https://img.shields.io/badge/-closed%20by%20D--051-0B1F33?style=flat-square) |
| D-018 | **Parallel DAB branches** share current through a common reference over CAN and a current loop per branch; no carrier-sync line.<br/><sub>The system controller caps total power; the PCS needs DC-current feed-forward.</sub> | ![frozen](https://img.shields.io/badge/-frozen-00A99D?style=flat-square) |

## ⚡ PCS-P125

| ID | Decision · why | Today |
|---|---|---|
| D-047 | **PCS-P125 architecture:** three-level T-type with discrete Chinese IGBTs at 16 kHz.<br/><sub>Withdrawn by D-053: its devices overheat and its DC link cannot hold the midpoint at power factor 0.</sub> | ![superseded](https://img.shields.io/badge/-withdrawn%20by%20D--053-8FA3B5?style=flat-square) |
| D-053 | **PCS-P125 power stage: two-level on 1700 V SiC,** 6 × Sichain SG2M040170HJ per switch, 32 kHz, LCL filter.<br/><sub>Every device at ≤ 0.56 of its rating; confirmed by the cross-check of D-057. Its frequency, filter, cost and efficiency are replaced by the design point of D-060 (which corrects D-059).</sub> | ![baseline](https://img.shields.io/badge/-baseline-00A99D?style=flat-square) |
| D-057 | **Two-level stands after an independent cross-check** against Wolfspeed's T-type and two-level reference designs - on robustness and simplicity, not on cost.<br/><sub>Inductors were priced at half their material cost (about 979–1,028 USD at 5,000 units, not 869); peak efficiency about 98.9–99.1 %, not 99.20 %; threshold binning is required for six paralleled devices; AC-02 now states full current at any power factor. The cost and efficiency figures are replaced by D-060 (which corrects D-059).</sub> | ![frozen](https://img.shields.io/badge/-topology%20frozen-00A99D?style=flat-square) |
| D-059 | **PCS-P125 design point after re-optimisation with real inductor designs: the two-level SiC stage stays, at 24 kHz** — 36 × SG2M040170HJ (6 per switch), L1 130 µH, C<sub>f</sub> 75 µF, L2 6 µH, hardware trip ±450 A.<br/><sub>Calculated: peak efficiency 99.09 %, 98.42 % at full load; three-wire 1,365 USD at catalogue prices and 1,092 USD at 5,000 units (8.7 USD/kW), four-wire 1,629 / 1,304 USD; the filter is 404 USD and 30 kg of that. More than twenty candidates were compared with their inductors designed, not priced by a rule: the earlier design corrected costs 1,194 USD, a SiC T-type 1,236 USD (1,279 USD with 1700 V outer devices), an IGBT T-type at 16 kHz 1,607 USD. Open: powder-core inductors were not searched (the filter is 37 % of the BOM), L1 sits at its 140 °C hot-spot limit, the stiff-grid resonance is 7.7 kHz against 8.0 kHz, the current-loop bandwidth falls to 0.75 kHz with THDi not simulated, and nothing is quoted. Its 24 kHz point and these figures are superseded by D-060: they rested on a fault in the magnetics tool.</sub> | ![superseded](https://img.shields.io/badge/-superseded%20by%20D--060-8FA3B5?style=flat-square) |
| D-060 | **Corrects D-059: the inverter's design point is 32 kHz, L1 120 µH, C<sub>f</sub> 50 µF, L2 6 µH** — still two-level on 36 × SG2M040170HJ.<br/><sub>Calculated: peak efficiency 98.99 %, 98.30 % at full load; worst junction 123 °C at 110 % load and 45 °C inlet; three-wire 1,409 USD at catalogue prices and 1,131 USD at 5,000 units (9.0 USD/kW), four-wire 1,694 / 1,361 USD; the filter is 443 USD (549 at catalogue prices), 39 kg and 39 % of the BOM. The 24 kHz point rested on a fault in the magnetics tool (a permeability of 1 for the amorphous core material at 20–30 kHz, so an air core): modelled properly its inductor loses 263 W, not 165 W, and reaches 198 °C. With the corrected model the next two-level point is only 11 USD away, so the exact frequency and inductance are not a firm result; the two-level choice is (148 USD below the cheapest T-type that keeps the voltage rule). Open: powder-core inductors were not searched, the two loss models differ by 1.8 × on foil copper loss, the amorphous core-loss fit is quoted from memory, L1 sits at its hot-spot limit, THDi and the control loops are not simulated, and nothing is quoted.</sub> | ![frozen](https://img.shields.io/badge/-frozen%2C%20with%20conditions-00A99D?style=flat-square) |

## 🔬 Reviews

| ID | Decision · why | Today |
|---|---|---|
| D-023 | **Independent reviews** found 58 defects (7 critical) after every board already passed its own checks.<br/><sub>Assigned to board revisions; findings in `gen/data/review_*.csv` and `integration_findings.csv`.</sub> | ![in progress](https://img.shields.io/badge/-in%20progress-F2A007?style=flat-square) |

---

<!-- footer --> ← [Glossary](glossary.md) · [Documentation index](../README.md) · [Overview](01-overview.md) →
