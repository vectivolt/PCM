<!-- breadcrumb -->
[Home](../../README.md) › [Documentation](../README.md) › Installation conditions

# 🛡️ Installation conditions

> What a cabinet integrator must provide for the PV modules' and the inverter's stated protection and ratings to hold — one list, every row traced to the generated line it comes from.

![as of](https://img.shields.io/badge/as%20of-2026--10--07-5B6B7A?style=flat-square)
![numbers](https://img.shields.io/badge/numbers-calculated%20%C2%B7%20assumed%20%C2%B7%20estimated-F2A007?style=flat-square)
![bench](https://img.shields.io/badge/bench--validated-nothing-E4572E?style=flat-square)
![rows](https://img.shields.io/badge/installation%20requirements-77-0B1F33?style=flat-square)
![finding](https://img.shields.io/badge/review%20finding-R2--12%20closed%20%28D--078%29-00A99D?style=flat-square)

---

> [!IMPORTANT]
> **What this list is, and what it is not.** It collects, in one place, the conditions that the modules' own checks
> assume about the world outside the module: the source on each DC port, the upstream protection and the fault
> currents it must clear, the earthing, the environment, the control wiring and the labels. Nothing here is
> bench-validated. Every figure is calculated, simulated or assumed in the file it is quoted from, and the file's own
> labels — ASSUMED, ESTIMATE, RFQ (request for quotation), FROM MEMORY, OPEN — are kept where it puts them.
> **A condition recorded here is not a protection the module provides.** Where a row says the upstream device must clear
> a current band, the module does not clear that band itself; a cabinet that does not meet the row leaves the band
> unprotected and the module's stated coverage does not hold (review finding R2-12,
> [review_r2.csv](../../gen/data/review_r2.csv)). This is not a certification, a safety case or a claim of standalone
> protection.

> [!NOTE]
> **Re-verified after the rebuild of 2026-10-07** ([D-078](DECISIONS.md), [D-079](DECISIONS.md)), and again after the
> re-runs of the inverter chain and the PV boards at about 12:00 and 12:07 the same day. Every quoted fragment was found again in its
> file and its line number corrected (hashes in the box below); where the text had changed the quote now carries the new
> text (the L1 saturation current, the four-wire coil contract, the attempt counter, the ride-through measure, the
> uncoordinated DC-rejection figures). INST-27 was rewritten and INST-71 to INST-74 added; four rows of
> [Where the records disagree](#where-the-records-disagree) are reconciled by notes in the architecture files.
> **Re-verified again after the rebuild for the re-check R3** ([D-080](DECISIONS.md), later on 2026-10-07): every quoted
> fragment found in its file at the line its link names (68 labels re-pointed to moved lines); the quotes whose text
> changed now carry the new text — the module grades and their tier tables (INST-73, INST-44), the coupled PCS / PV
> bus rules (INST-71), the ride-through clamp and fallback figures (INST-27), the grid-tie close (INST-74) — and INST-75
> (the bolted output short on the four-wire build) is added. **Re-verified once more after the rebuild for the
> economical re-check R4** ([D-081](DECISIONS.md), the evening of 2026-10-07): every quoted fragment found in its file at
> the line its link names (97 labels re-pointed — the generated files gained lines, and the guide pages cited by line were
> edited in the same documentation pass); the quotes whose text changed carry the new text — the enforced DC-bus window
> and the cable model (INST-71), the inverter's environment line with its −10 °C rating (INST-44); INST-76 (the harness
> between the PV modules and the inverter) and INST-77 (the inverter's cold rating) are added, grade B's 124.57 kW is in
> INST-73, INST-28 carries the staged release of grid forming, and the installation-facing
> [standing restrictions](#standing-restrictions) of the review are listed. After a
> later rebuild, re-find each quoted fragment with `grep -nF '<fragment>' <file>` and correct the line number in its link;
> a fragment that no longer matches means the requirement may have changed.

## How to read this page

| Module | Power board | Control board | Its generated checks |
|---|---|---|---|
| PV-P75 (75 kW, three phases) | PV-PWR | PV-CTL (build PV-CTL-P75) | [PV-PWR][PV-PWR:1], [PV-CTL][PV-CTL:1] |
| PV-P100 / PV-P110 (four phases) | PV-PWR-4 | PV-CTL | [PV-PWR-4][PV-PWR-4:1], [PV-CTL][PV-CTL:1] |
| PCS-P125 (inverter, three-wire) | PCS-PWR | PCS-CTL (build 3W) | [PCS-PWR][PCS-PWR:1], [PCS-CTL][PCS-CTL:1] |
| PCS-P125-4W (inverter, four-wire) | PCS-PWR-4W | PCS-CTL (build 4W) | [PCS-PWR-4W][PCS-PWR-4W:1], [PCS-CTL][PCS-CTL:1] |

- **Rows.** Each requirement has an ID, `INST-nn`, in the order of the sections below; the table at the end is the list,
  one row per requirement with *why* (the module's own limit that makes it necessary) and *source* (the generated
  line). The sections carry the evidence: verbatim quotes with the line they are on.
- **Quotes.** Verbatim (the generated files are ASCII; the hand-written records keep their own characters); `…` between two quoted pieces marks an omission. A link
  such as [PV-PWR:15] goes to that line (`?plain=1` for Markdown files, which GitHub would otherwise render).
- **Labels kept from the files.** CALCULATED: a model or data sheet in a script under `sim/` or `gen/`. ASSUMED /
  ESTIMATE: the file says so. RFQ: part or data not yet in hand. OPEN: the file says it is not closed.
- **Which number wins.** Where two records give different numbers the row uses the generated one — the record wins over
  a page — and the difference is listed under [Where the records disagree](#where-the-records-disagree).
- **Terms, once.** IMD: insulation monitoring device. RCM: residual-current monitor. SPD: surge protective device. OVC:
  overvoltage category. SCR: short-circuit ratio (grid strength). EMS / BMS: energy / battery management system.
  SELV / PELV: safety / protective extra-low voltage. DVC: decisive voltage class. PE: protective earth. PD: pollution degree. K_A / K_B: the contactors of port A / B. L/R: time constant of the fault circuit. pu: per unit.

<details>
<summary>Source files as read — sha256 (first 16 hex), lines and modification time, re-read after the rebuild for the re-check R4 (D-081) on 2026-10-07 (PV-CTL's check and the module report rewritten with the same content)</summary>

| File | Lines | sha256 (first 16 hex) | Modified |
|---|---:|---|---|
| [hardware/PV-PWR/outputs/PV-PWR_design_check.txt](../../hardware/PV-PWR/outputs/PV-PWR_design_check.txt) | 39 | `605ad30b98672cbf` | 2026-10-07 20:21 |
| [hardware/PV-PWR-4/outputs/PV-PWR-4_design_check.txt](../../hardware/PV-PWR-4/outputs/PV-PWR-4_design_check.txt) | 39 | `93927cc86c53954f` | 2026-10-07 20:21 |
| [hardware/PV-CTL/outputs/PV-CTL_design_check.txt](../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt) | 89 | `c762b158ba7926ef` | 2026-10-07 20:22 |
| [hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt](../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt) | 34 | `7de3d8949f0b72da` | 2026-10-07 20:22 |
| [hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt](../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt) | 46 | `dc0c3fe173066ceb` | 2026-10-07 20:23 |
| [hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt](../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt) | 136 | `c61398a6ce70b363` | 2026-10-07 20:23 |
| [sim/out/pcs_design/pcs_spec.json](../../sim/out/pcs_design/pcs_spec.json) | 13330 | `92c24c603f2865af` | 2026-10-07 20:13 |
| [sim/out/port_design/report.md](../../sim/out/port_design/report.md) | 471 | `7d15d615f48e685a` | 2026-10-05 20:49 |
| [sim/out/port_design/port_spec.json](../../sim/out/port_design/port_spec.json) | 916 | `96916e7da3b75eb7` | 2026-10-05 20:49 |
| [sim/out/pcs_design/report.md](../../sim/out/pcs_design/report.md) | 810 | `2acf70a88a54ec16` | 2026-10-07 20:13 |
| [sim/out/pcs_control/report.md](../../sim/out/pcs_control/report.md) | 967 | `bc682a5be70bd1c3` | 2026-10-07 20:20 |
| [sim/out/pv_control/report.md](../../sim/out/pv_control/report.md) | 458 | `1e9991ffb992a2c9` | 2026-10-07 20:15 |
| [sim/out/pv_design/module_report.md](../../sim/out/pv_design/module_report.md) | 139 | `48719cae5328d9ad` | 2026-10-07 19:43 |
| [sim/out/insulation/report.md](../../sim/out/insulation/report.md) | 380 | `2134017ef90341b0` | 2026-10-07 20:26 |

The design checks print the hashes of their own inputs in their first lines ("Built from", "Inputs read at this
build"); the hand-written records cited by line (REQUIREMENTS, DECISIONS, the architecture files, the guide pages)
are not stamped.

</details>

---

## ⚡ 1 DC port of the PV modules

The PV modules have two DC ports on one shared negative rail. Port A faces the PV array, port B a battery or DC bus.
Each port has one contactor (K_A, K_B). Only port B has fuses (one 250 A aR link per pole) and a precharge; port A has
neither.

### 1.1 Port A — the PV array

Port A's declaration is printed by both power-board checks (review PCM-16, [D-072](DECISIONS.md)). The three-phase
PV-PWR is the PV-P75, the four-phase PV-PWR-4 the PV-P100/110; only the numbers differ.

> `Port A admits: a current-limited PV array only - short-circuit current at the module terminals <= 168.8 A (1.25 x the 135 A port rating; K_A breaks 300 A at 1000 V both polarities x 200 operations: margin x1.78, carries x2.22), open-circuit voltage <= 1000 V (OV trip band 1039-1110 V), connected to the DC terminals through string fuses in the combiner (IEC 62548, ARCHITECTURE-COSTFIRST 6.3) and a load-break isolator, array and cable capacitance terminal to terminal <= 1.1 uF behind >= 11 uH (the 330 A discharge peak the making estimate assumes, back-calculated: ESTIMATE; the array's capacitance to PE is 16.5 uF, common mode only)` — [PV-PWR:15]

→ INST-01 … INST-05, INST-09

> `short-circuit current at the module terminals <= 225.0 A (1.25 x the 180 A port rating; K_A breaks 300 A at 1000 V both polarities x 200 operations: margin x1.33, carries x1.67)` … `the array's capacitance to PE is 22.0 uF` — [PV-PWR-4:15]

> `Port A does NOT admit: a stiff DC source (battery, DC bus, rectifier, another converter) - K_A would close onto the empty 290 uF bank with a peak of about 7.4 kA at 1000 V (0.03 ohm + 3.5 uH source and loop, 145 J stored: 53x the making rating's 140 A, weld likely), a fault fed by it is cleared only by K_A (<= 300 A normal opening, hold-off to 967-1033 A, 1500 A once) with no fuse behind it, and nothing blocks reverse current; a stiff DC source on port A requires the PV-PORT-180 / lean port with fuse and precharge (gen/port.py lean_port as the battery port B: 2 x HPE501 250 A aR links, 220 ohm precharge, polarity and dV interlocks)` — [PV-PWR:15]

> `K_A would close onto the empty 386 uF bank with a peak of about 8.3 kA at 1000 V (0.03 ohm + 3.5 uH source and loop, 193 J stored: 59x the making rating's 140 A, weld likely)` — [PV-PWR-4:15]

→ INST-06

> `Reverse power B -> A is NOT admitted with an array: there is no series diode, fuse or precharge on port A, the port over-current window (387-413 A, both directions, FLT_N) protects the module and not the array, and an array driven backwards conducts through its cells and bypass diodes (limited only by the modules' reverse-current rating and the combiner's string fuses) - firmware holds the port A current at >= 0 while the port is declared PV (second layer behind the converter's own current limit).` — [PV-PWR:15]

→ INST-07

> `K_A only after an insulation measurement of ≥ 33 kΩ (IEC 62109-2 rule, quoted from memory)` — [guide 04:169]
>
> `a load-break DC isolator upstream for maintenance` — [ARCH-COSTFIRST:281]

→ INST-04, INST-08

**Not closed (OPEN).** Connecting the array while port B is dead is the one case the files do not cover:

> `with port B live, after the converter has pre-charged the 290 uF A bank to within 20 V (then the making current is the array's Isc), otherwise onto the EMPTY bank at the array's Voc: making current about 499 A (Isc 169 A + 330 A discharge, ESTIMATE) against a published making rating of 140 A at 20 V only - covered: NO (Hongfa to confirm, R-04).` — [PV-PWR:15]
>
> `making current about 555 A (Isc 225 A + 330 A discharge, ESTIMATE)` — [PV-PWR-4:15]

→ INST-10. No installation condition closes it; the module's own mitigation is the precharge-first rule with port B live.

### 1.2 Port B — battery or DC bus

The PV power-board checks print no port B coordination line; it is in the port report (lean port) and in `port_spec.json`.
The lean battery port holds the contactor closed above its hold-off band and lets the aR link clear the high currents;
the band in between belongs to the installation ([guide 04:179-184]):

| Current from the battery into the module | Who clears it |
|---|---|
| ≤ 300 A | converter control and K_B, normal opening |
| 300 A – 967 A | K_B opens (fault opening; contactor replaced afterwards) |
| **967 A – 1,250 A** | **neither — the upstream battery protection** (K_B is held; the 250 A aR link breaks only from 1.25 kA) |
| 1,250 A – 50 kA | HPE501/000B100-250 aR fuse in each pole |

> `upstream protection interrupts any current of 0.97-1.25 kA into the battery port within 0.26 s; prospective short-circuit current <= 50 kA at the module terminals with L/R <= 1 ms (the contactor's breaking data are at L/R <= 1 ms; Hongfa states no L/R for the HPE501's 50 kA); the cable to the module is protected upstream.` — [port report:456]
>
> `967-1250 A within 0.26 s` … `contactor held, aR link below its 5 In breaking range; fastest pre-arc at 5 In with the curve 10 % left` — [port report:434]
>
> `The upstream battery protection (rack fuse, DC breaker or BMS-controlled rack contactor)` — [ARCH-COSTFIRST:269]

The same numbers are machine-readable in [port_spec:696-702] (`installation`: band 967–1250 A, 0.261 s, 50 kA, L/R 1 ms).
→ INST-11 … INST-13. R2-12 asks for the upstream device, the fault current, L/R and the clearing time: the device types
the files name are a rack fuse, a DC breaker or a BMS-controlled rack contactor; they do not give that device a DC
voltage rating (the contactor data the band is matched to are at 1000 V).

---

## ⚡ 2 DC port of the inverter

The inverter's battery port is the same lean port scaled to 250 A: one contactor (HFE82V-300C), a 400 A aR link per
pole (HIITIO HCHVF1000-400A-38R), the 220 Ω precharge (200 Ω on the four-wire board). The coordination of the fuse with
the contactor is calculated in the port report, section 14 (review PCM-15); both power-board checks print its result.

> `up to the hold-off band the contactor breaks (port OC window 387-413 A, 2.3 openings at 1033 A, 1000 V, L/R <= 1 ms); 967-2009 A nothing clears quickly (contactor held, fuse below its breaking range / slower than the contactor's 180 C curve); the 400 A links clear first only 2029-7414 A; above 7487 A prospective the contactor's short-circuit capacity (8 kA 6 ms, 10 kA 1.5 ms) is exceeded and the let-through peak reaches the bounce region (>= 6 kA from 6083 A, >= 8 kA from 19348 A; at 50 kA 10122 A by the cut-off chart, 12770-16861 A DC at the melting I2t).` — [PCS-PWR:15], [PCS-PWR-4W:16]

> `INSTALLATION REQUIREMENT: the battery-side protection must interrupt any current of 0.97-2.01 kA into the PCS DC port within 2.5 s (the contactor's 130 C curve at the band top; 25 s at 1 kA), and either limit the battery's prospective short-circuit current at the PCS DC terminals to <= 7.5 kA (L/R <= 1 ms) or interrupt currents above it within the contactor's short-circuit capacity (6 ms at 8 kA, 1.5 ms at 10 kA) - the PCS's own 400 A links protect the contactor only between 2.0-7.4 kA.` — [PCS-PWR:15], [PCS-PWR-4W:16]

> `The fuse data are averages from a chart with no tolerance band (+/-10 % in current assumed); its high-current tail (2.5-6 ms at 20-50 kA) is not consistent with the tabulated 43 kA2s melting I2t (adiabatic melting at 50 kA takes < 1 ms) - an RFQ / test item.` — [PCS-PWR:15], [PCS-PWR-4W:16]

| Fault current into the DC port | Cleared by | The installation must |
|---|---|---|
| up to the 967–1,033 A hold-off band | the contactor, on the 387–413 A window (about 2.3 openings at the band top) | nothing |
| 0.97–2.01 kA | nothing quickly | interrupt within 2.5 s (25 s at 1 kA) |
| 2.0–7.4 kA | the 400 A links | nothing; at 2 kA and above the contacts are likely bonded (weld check) |
| above 7.5 kA prospective | the contactor's capacity is exceeded (8 kA for 6 ms, 10 kA for 1.5 ms) | limit the prospective current to ≤ 7.5 kA at L/R ≤ 1 ms, or interrupt above it within that capacity |

<sub>Bands from [port report:463-468]; the 7.5 kA of the requirement is the 7,487 A "not coordinated above" figure of [port_spec:904] rounded.</sub>

→ INST-14, INST-15. The chart tail that does not match the tabulated I²t is open (see [Open](#open-what-the-files-do-not-close)).

**The battery's earth capacitance (three-wire builds, and four-wire mode B).** Below about 680 V DC at 400 V AC the
converter needs a zero-sequence offset, which puts a 150 Hz voltage on the battery's capacitance to earth:

> `so above about 680 V (400 V AC; about 780 V at 460 V AC) there is no 150 Hz common-mode voltage at all; below, the min-max zero sequence is needed` — [pcs_design report:435]
>
> `the battery may have up to 6 uF to earth for a 300 mA budget (24 uF for the 10 mA/kVA one) when it is operated below 680 V; above, no limit from this source.` — [pcs_design report:451]
>
> `(c) an installation limit on the battery's capacitance to earth for operation below 680 V (stated above)` … `The battery rack sits at +-V_dc/2 against earth in operation (as the architecture noted): its insulation and IMD must be rated for that.` — [pcs_design report:455]
>
> `in mode B the DC side carries the 150 Hz zero sequence to N / earth as the three-wire build does (step f installation limit on the battery's capacitance to earth)` — [PCS-PWR-4W:34]

[pcs_spec:7355-7356] give the unrounded limits: 5.79 µF at 300 mA, 24.1 µF at 10 mA per kVA. The report's
residual-current values (10 mA per kVA, 300 mA, steps of 30 / 60 / 150 mA) are IEC 62109-2 quoted FROM MEMORY.
→ INST-16, INST-17

**Low-frequency battery current and the voltage window.**

> `750 V, 216/0/0 A at 0 deg: I_N 216 A, HF 103 A + C_f / C_fN 20 A per half, 21.0 A per capacitor, battery LF 47 A rms` … `950 V, 216/0/0 A at 0 deg: I_N 216 A, HF 97 A + C_f / C_fN 28 A per half, 20.3 A per capacitor, battery LF 37 A rms` — [PCS-PWR-4W:38]
>
> `100 Hz battery current of single-phase load (~39 A rms at 750 V) is an installation item` — [pcs_spec:12808]

→ INST-18

> `V_dc >= 1.02 x sqrt(2) x V_LL,rms(measured) + 10 V and >= the map's V_dc for the commanded P / Q: 624 V at 400 V, 718 V at 460 V (no load)` — [pcs_spec:12859], [PCS-CTL:94]
>
> `rated current at PF 0, Q > 0 needs 643 V DC at 400 V, 737 V at 460 V` — [pcs_spec:12858]
>
> `230.9 V L-N (nominal): mode A 724 V to connect / 746 V for rated current (worst PF), mode B 624 / 643 V, bare 2 sqrt2 V_LN 653 V` — [PCS-PWR-4W:34]
>
> `meanwhile the body-diode rectifier drives 846 A peak / 730 A mean DC (460 V AC on 590 V, stiff grid, L1 saturation and R not modelled; 113 / 98 A at SCR 5) for <= 50 ms (AC contactor release, ASSUMED)` — [pcs_spec:12860]

→ INST-19. The 5 % loop headroom and the dead-time compensation behind these windows are ASSUMED (risk G1).

**DC/DC converters on the inverter's DC link** (reviews R2-04, R3-03 and R4 E04, [D-079](DECISIONS.md),
[D-080](DECISIONS.md), [D-081](DECISIONS.md)). The inverter's link is 0.41 mF of film and it has no brake chopper, so
system rules fall on whatever else is connected to the DC bus. Since D-080 the battery-less case is the coupled PCS / PV
trajectory — the PV modules' own protections read from their specs, simulated on the averaged model — instead of an
assumed 50 µs DC/DC stop; since D-081 the model carries the cable between the modules, and the set point is an
**enforced window**, not a figure the integrator chooses:

> `with the coordination row the island rides through at 750 / 782 V set points (bus <= 853 V against the 975 V soft limit, the PV current cut 0.20-0.28 ms after the rejection) and the PCS trips at 950 V (bus 994-1016 V, dark)` … `uncoordinated the comparator trips 1.50 / 1.94 ms after the step (band edges 978 / 1048 V) and the bus is left at 1060-1061 V, latched - below the aux's 1107 V input lock-out` — [pcs_spec:12773], [PCS-CTL:107]
>
> `enforced operating window (product rule in both modules' parameter sets, pcs_control_spec dc_bus_operating_window, review R4 E04): CV set point default 750 V (3W+PE 750 V, 3W+N+PE, mode A (N leg at 50 %) 755 V, 3W+N+PE, mode B (zero sequence on four legs) 750 V); clamped to <= 782 V in the PV module's parameter set` … `the PCS refuses to start an island (VF) on a bus declared battery-less above 791 V` — [pcs_spec:12773], [PCS-CTL:107]
>
> `the one-node model's 921 V (1 x 82.5 kW) / 909 V (2 x 75 kW) and the cable model's 846 V are model validities, NOT ratings (64 V between the clamp and the cable validity); cable model (2 / 10 / 20 m runs, 0.6 / 1 uH/m): every corner rides through, PCS-node peaks 849-887 V at 750 V, 878-914 V at 782 V` — [pcs_spec:12773], [PCS-CTL:107]
>
> `the PV modules' coordination row (V_B > V_B* + 30 V -> every cell's current reference to zero) is required for VF on a battery-less bus: without it every full-load rejection trips the PCS (bus 1063-1069 V); the CV set point in VF on a battery-less bus: default 750 V, firmware limit <= 782 V (the PV coordination row's own validity) - the enforced product rule (review R4 E04: pcs_control_spec dc_bus_operating_window); 909 V is this one-node model's validity only (no cable, the PV banks on the PCS node) - not a rating; the PV power on one link <= the PCS's DC power tier (one module at 82.5 kW and two at the 2-min tier studied)` — [pcs_spec:12773], [PCS-CTL:107]
>
> `a hardwired trip line (cost class low: an isolated digital line, a connector and a latch input, a few USD per module) is not needed inside the enforced window; in this one-node model it would be needed above 909 V and must stop the modules within 1 x 82.5 kW 116 us, 2 x 75 kW 56 us of the rejection at 950 V; not added; with the PV firmware dead the bus ends at 1126-1130 V, above the aux's 1107 V input lock-out: dark until it bleeds down` … `a CAN message is too slow; the PCS cannot absorb the energy (no brake chopper drawn)` — [pcs_spec:12773], [PCS-CTL:107]
>
> `x2 holds to V_B* = 782 V, above it a shared hardwired trip line or a battery` — [pv_control report:343]
>
> `parameter set: V_B* default 750 V in CV mode on a bus the installation declares battery-less (755 V when the PCS-P125 is the 3W+N+PE, mode A (N leg at 50 %) build), clamped to <= 782 V` … `a V_B* above the clamp is refused while the bus is declared battery-less, and the coordination row stays enabled; the PCS-P125 starts an island only at V_dc <= 791 V (its own refusal)` — [pv_control report:345]

The PV modules carry the cut as a firmware row (V<sub>B</sub> 30 V above its set point → every phase's current reference
to 0, 209 µs; [pv_control report:343]) and, since D-081, the window as a second system row (default, clamp and refusal;
[pv_control report:345]); the inverter carries its own island-start refusal. → INST-71

**The harness between the PV modules and the inverter** (review R4 E04, [D-081](DECISIONS.md)). The coupled model's
validity depends on the runs between the modules' port-B terminals and the inverter's DC terminals; the harness the study
assumed (2 / 10 / 20 m, 0.6 / 1.0 µH per metre, ASSUMED) becomes a contract for the integrator:

> `harness contract (installation): run length <= 20 m per PV module, its port-B terminals to the PCS's DC terminals; one run per module (a shared busbar section counts in each run)` … `loop inductance <= 1 uH per metre of run and <= 20 uH per run including fuses, disconnectors and busbar sections: DC+ and DC- routed together (a two-core cable, or the single cores bundled and touching in the same tray), never separated or looped through other sections; no series choke or ferrite on the link (its differential-mode inductance counts)` — [pcs_spec:12773], [PCS-CTL:107]
>
> `cross-section: copper, sized by the installation rules for the module's 135 A port current; loop resistance <= 12.6 mOhm per run (20 m of 70 mm2 at 90 C: 1.7 V at 135 A, the drop the set-point floors carry); heavier conductors allowed (the first peak hardly depends on the resistance: +1.8 V lossless at the worst run)` — [pcs_spec:12773], [PCS-CTL:107]
>
> `nothing else on the link: no capacitance, filter, DC source or load beyond the PCS's DC link and the PV modules' port-B banks as drawn (a battery or another source is a different study); at most two PV-P75 modules per PCS link, their power <= the PCS's DC power tier (section 7d)` … `the commissioning record carries the as-built runs (length, cross-section, routing), the battery-less declaration, the set point and both modules' serials` — [pcs_spec:12773], [PCS-CTL:107]
>
> `Harness ASSUMED ('dc_cable'): runs of 2, 10, 20 m, loop inductance 0.6 / 1 uH per metre of run (DC+ and DC- together), the resistance of 95 mm2 copper at 20 C (the least damping` … `capacitor ESR, busbars and the skin effect not credited` — [pcs_control report:592]

→ INST-76. The figures are a study's assumptions turned into limits; a run outside them (longer, more inductive,
separated conductors, a choke, a third module, a battery or another source on the link) is outside the study.

> `SCR 5 89.5 kW, SCR 10 119.5 kW, SCR 20 125 kW, stiff 125 kW: a DC/DC trip then stays below the 975 V soft limit (simulated, averaged; full power from SCR 20)` … `the row at or below the estimate / 1.3, no interpolation upward, no valid estimate = the weakest row (selection ASSUMED); published to the DC/DC over the module CAN / the EMS, which enforce it` — [pcs_spec:12771], [PCS-CTL:106]

→ INST-72

---

## ⚡ 3 AC port of the inverter

### 3.1 Both builds

The inverter has no AC fuse in its main power path (the 1.6 A fuses of the start-up tap protect only the tap). Two AC
contactors in series (K1, K2) disconnect it and are not short-circuit protective devices; the upstream device is the
protection.

> `converter running: the circular current limiter holds 367 A peak (259 A rms) for <= 200 ms, then trips` … `a shorted leg is fed from the battery through the two 400 A aR fuses (2.0-7.4 kA, port coordination) and from the grid through the body diodes of the healthy legs - that grid-fed current is limited only by L2 + L1 until L1 saturates, then by the installation.` — [PCS-PWR:29], [PCS-PWR-4W:41]

> `Grid-fed bolted leg short through L2 + L1 (L1 keeps its inductance to 578 A at 100 C): stiff 5834 A rms (16502 A peak), L1 saturated after 0.22 ms; SCR 50 3543 A rms (10021 A peak), L1 saturated after 0.37 ms; SCR 20 2229 A rms (6306 A peak), L1 saturated after 0.58 ms; SCR 5 781 A rms (2210 A peak), L1 saturated after 1.66 ms - the filter leaves the upstream protection well under 2 ms, then the installation's prospective current flows.` — [PCS-PWR:29], [PCS-PWR-4W:41]

> `The two AC contactors in series are disconnecting devices, not short-circuit protective devices: AC-1 >= 248 A (RFQ class), making / breaking about 1.5 x Ie at cos phi 0.95 (IEC 60947-4-1 AC-1, from memory), opened by firmware at zero current only; they must withstand the upstream device's let-through (conditional short-circuit current with that device: an RFQ item).` — [PCS-PWR:29], [PCS-PWR-4W:41]

> `INSTALLATION REQUIREMENT: upstream AC protection rated >= 250 A (the 216 A 2-min tier must not trip it), breaking capacity >= the prospective short-circuit current at the connection point, let-through within the module's AC-path withstand (contactor conditional current, busbars, L2 / L1 - RFQ / test item); gG fuses 250 A or an MCCB with an instantaneous release; residual-current protection upstream type B if the code requires an RCD (the module has its own RCM, IEC 62109-2 style); earthing TN-S or TT (three-wire build in TN: step f limit on the battery's earth capacitance); the DC side: the port declaration (battery-side protection 0.97-2.01 kA within 2.5 s, prospective current <= 7.5 kA at the DC terminals or interrupted within the contactor's capacity).` — [PCS-PWR:29], [PCS-PWR-4W:41]
>
> `upstream AC protection rated >= 250 A (the 216 A 2-min tier must not trip it), breaking capacity >= the prospective short-circuit current at the connection point` — [pcs_spec:12311]

→ INST-20 … INST-24

> `3 x TVT25751 (Uc 465 V AC >= 1.1 x 400 V for an earth fault in IT) + 3 x Y1 4.7 nF (500 V AC) to PE_T; type-B RCM sensor (RFQ) to PCS_X RCM / RCM_TST.` — [PCS-PWR:16], [PCS-PWR-4W:17]
>
> `residual current (type-B sensor): 30 / 60 / 150 mA step trips within 0.3 / 0.15 / 0.04 s and the continuous limit per code (from memory - verify), sensor self-test` — [pcs_spec:12854]

→ INST-23, INST-24

> `basic OVC III to PE (4 kV, 2.5 kV with the AC SPD credit; clearance 3.0 / 3.5 / 3.9 mm at 2000 / 3000 / 4000 m - ARCHITECTURE-PCS section 3)` — [PCS-PWR:24], [PCS-PWR-4W:28]
>
> `400 V +-15 %, 50/60 Hz, SCR 5..stiff` — [pcs_spec:12804]
>
> `"grid_SCR_range": "5 .. stiff"` — [pcs_spec:12791]

→ INST-25, INST-26

### 3.2 The four-wire build — the neutral

> `K1 + K2 4-pole (RFQ, the N pole switched with the phases, the same coil contract: 20 W pull-in / 4 W hold)` … `RCM type B around L1-L3 + N (RFQ, aperture 4 x 250 A); N terminal 250 A as the phases` … `L-N protection through two discs in series, 2 x 1240 V = 2480 V at 150 A (8/20 us) against the 4 kV OVC III rated impulse at 230 V` — [PCS-PWR-4W:19]

> `per phase at 230 V rated 41.6 kW, continuous 45.7 kW, 2_min 49.9 kW, 200_ms 59.9 kW; one phase at I, two at 0 (100 % unbalance): I_N = I` … `per-phase P / Q set points with the three currents in phase: I_N up to 3 I - the firmware limits I_N to the N leg's tiers; half the module power on one phase = 271 A, above every tier (the 100 % unbalanced load is one phase at its own tier)` — [PCS-PWR-4W:37]

> `neutral current IL4 <= the phase tiers (198 A rms continuous, 216 A 2 min, 259 A 200 ms)` — [pcs_spec:12867]

> `<= 280 A peak per phase (89 A DC, 140 A rms), L_N peak 311 A = the L1 part's continuous peak; battery LF 43 A rms at 750 V; output DC component <= 1.15 V (0.5 % Un) by the firmware regulator` — [PCS-PWR-4W:39]

→ INST-34 … INST-38. The files do not say where the neutral is bonded to earth (see [Open](#open-what-the-files-do-not-close)).

**A bolted output short in four-wire grid forming** (review R3-02, [D-080](DECISIONS.md); simulated on the averaged
per-phase model with both hardware trip layers replayed on their own signals). The module declares what it does; the
island's own short-circuit protection is planned around it:

> `a bolted output short in four-wire grid forming: ridden for the firmware's 200 ms at the 200 ms tier (the onset clamp keeps every studied corner off both hardware layers), then the firmware trip -> FAULT` — [pcs_spec:12314]
>
> `a unit whose layers sit outside the modelled bands trips in hardware instead: gates off within the protection chain (far below the ceiling), the latch holds, FAULT; no automatic clear after an over-current trip (the latch-clear row); a restart by command into a persisting short trips again; the third hazard trip of a class within 10 min -> LOCKOUT (ASSUMED, the firmware-practice row)` — [pcs_spec:12315]
>
> `"onset_clamp": "270 A per phase (and the N leg) for 5 ms after the phase-to-N voltage collapses, prediction on the divider-inverted C_f voltage, then the normal 378.0 A clamp",` … `"hold_A": 378.661,` — [pcs_spec:12316-12318]

→ INST-75. The 13.3 A between the hold's largest sensed peak and the lower trip edge rests on the averaged model; a
switched model and the bench are the gate ([pcs_design report:710]).

### 3.3 Firmware rows that put a condition on the installation

These are rows of the inverter's firmware hand-over (`handover.firmware_requirements` in `pcs_spec.json`, mirrored in the
PCS-CTL check). They are specifications for firmware that does not exist yet; the values are ASSUMED where the file says so.

**Grid stiffness and ride-through** (reviews R2-05 and R3-02, [D-079](DECISIONS.md), [D-080](DECISIONS.md)). Since the
re-check R2 the firmware keeps the onset of a deep dip under both hardware layers at rated power — in the averaged
model; since R3 the screen uses the lower edge of the two layers and a noise term:

> `with the per-sample clamp at 378.0 A (min(window low edge 426.0 A, CMPSS backup low edge 423.9 A) = 423.9 A - ripple/2 30.9 A - iclamp_margin_A 15.0 A = 378.0 A (sampled, ripple-midpoint current; the window-referenced value was 380.1 A)), set to 270 A while the state runs and predicting on the divider-inverted C_f voltage (the adopted onset measure, review R2-05): onset 382 A at a stiff 0 pu dip, 403 A at the worst corner / dip instant, screened against the lower edge of the two hardware layers (window 426.0 A, CMPSS backup 423.9 A): margin 23.0 / 20.9 A, net of the 2.53 A noise term 20.5 / 18.4 A (review R3-02); every onset, in-dip and recovery peak <= 389 A from stiff to SCR 5 at 0-0.5 pu residual voltage - rated current, no derating (SIMULATED, averaged model)` — [pcs_spec:12880], [PCS-CTL:115]

If a switched model or the bench shows less than that margin, the fallback applies, and with it a condition on the
connection:

> `FALLBACK if a switched model or the bench shows less margin: with the D-077 limiter rated current passes up to SCR 80.2 / 113.8 / 184.3 / 793.8 at 0 / 0.1 / 0.2 / 0.3 pu residual voltage` … `engages the derating at an estimated SCR >= 61.7: stiff 94.5 / 104.5 / 114 / 124 kW at 0 / 0.1 / 0.2 / 0.3 pu, the control study's table between` — [pcs_spec:12881], [PCS-CTL:116]
>
> `under the fallback the profile rides through at rated current where the connection's fault level per 125 kW module is below 7.71 MVA (SCR 61.7 with the estimate's tolerance; 10.02 MVA at the computed boundary SCR 80.2) - n modules at one point: the point's fault level / n; above it the derating table applies` — [pcs_spec:12769]
>
> `0 pu: 94.5 kW (76 %, onset 423 A); 0.1 pu: 104.5 kW (84 %, onset 424 A); 0.2 pu: 114.0 kW (91 %, onset 423 A); 0.3 pu: 124.0 kW (99 %, onset 423 A)` — [pcs_control report:291]

→ INST-27 (rewritten on 2026-10-07: the derating was the primary measure before [D-079](DECISIONS.md); its figures
re-tabled against the lower edge 423.9 A by [D-080](DECISIONS.md) — they were 8.07 MVA and 95.5 / 105.5 / 115 kW). The estimator's
accuracy (±30 %) is ASSUMED, and the reactive-current profile and every connection-code figure stay FROM MEMORY
([pcs_control report:347]).

**Off-grid loads.**

> `off-grid loads: crest factor <= 2.04 at rated rms below the 367 A limiter, DOL motors <= 36 A rated at a 6 x start` — [PCS-PWR:31], [PCS-PWR-4W:43]

[pcs_spec:12709-12724] adds the crest-factor curve (146.6 A rms at CF 2.5, 122.2 A rms at CF 3.0) and the motor tier (216 A
for 2 min, 259.2 A for 200 ms). Since [D-081](DECISIONS.md) (review R4 E06) the off-grid envelope is the module's
declared output capability, not a statement about the loads, and grid forming is released in stages:

> `release sequence: (1) grid-following operation qualified and released first -> (2) grid forming (VF) released for managed island loads with an agreed voltage-time and interruption specification, against which the declared capability is checked -> (3) general-purpose off-grid or UPS-like supply only after the required output class and its test method are established (IEC 62040-3 not claimed)` — [pcs_spec:12874], [PCS-CTL:109]

→ INST-28

**The module's grade** (reviews R2-07 and R3-04, [D-078](DECISIONS.md), [D-080](DECISIONS.md)). The overload and
continuous tiers depend on the module's SiC grade — grade A if every device passed an absolute on-resistance limit,
grade B otherwise, each with its own tier table by inlet temperature, DC voltage, power factor and start state. The grade
is bound to the module serial, and the integrator records it:

> `R_DS(on) <= 40.0 mOhm for each device at V_GS 18 V, I_D 38 A pulsed (< 200 us), T_J 25 C` … `a module with any of them is grade B and runs grade B's tier table (pcs_spec desat rds_acceptance grade_tier_tables, by inlet, V_dc, PF and start state; at 45 C inlet continuous 196.8 A, 2 min = 200 ms 207.5 A; at 60 C 179.8 / 192.6 A, 110 % not held), a parameter set bound to the module serial` — [pcs_spec:12817]
>
> `the end-of-line test writes the grade's tier table into the controller's parameter set (CRC with the calibration set) from the lot's incoming R_DS(on) record, the controller reports the grade with the module serial, and the integrator records the grade with the module serial; a module without a grade record runs grade B; replacing a device re-grades the module from the replacement's record` — [pcs_spec:12818]
>
> `grade A (every device <= 40.0 mOhm): 198 / 216 / 259.2 A at <= 45 C, at 60 C 198.0 / 216.0 / 242.9 A (continuous / 2 min / 200 ms from steady)` — [PCS-CTL:99]
>
> `a 200 ms excursion is admitted only from the continuous state or cold; once the current has been above the continuous tier for more than 0.22 s (the 200 ms excursion + 20 ms) the 200 ms limit equals the 2 min tier until the current has been at or below the continuous tier for 12 min - both grades` — [PCS-CTL:100], [pcs_spec:12865]

→ INST-73; the load and motor-start sizing of INST-28 uses the tiers of the module's grade.

**Generator following, island transfer, parallel operation.**

> `frequency window 45-55 Hz (60 Hz: 55-65 Hz), ROCOF ride-through <= 4 Hz/s, voltage 0.85-1.10 Un; no reverse power: the genset keeps >= 30 % of its rating (wet-stacking floor), so discharge <= P_load - 0.3 P_gen and charge <= 0.9 P_gen - P_load; power ramps <= 10 % of P_gen per s; P(f) droop following the genset's 4 %; competitor claim, no published parameters; our values ASSUMED (standards not on file)` — [PCS-CTL:113], [pcs_spec:12878]

> `automatic, with an interruption of about 150 ms = detection 100 ms (ASSUMED) + the grid-tie contactor's drop-out 50 ms (CHINT NXC-225 class with its DC coil module, ASSUMED - the RFQ class of our AC contactors)` … `a seamless unplanned transfer needs a static transfer switch: out of scope (REQUIREMENTS: not STS)` — [PCS-CTL:119], [pcs_spec:12884]

> `GFL: independent current sources, the EMS splits P / Q (shared set point); GFM: P-f / Q-V droop; the module bus = the control board's CAN (paralleling, carrier synchronisation by CAN time-stamping, software addressing) and zero-sequence circulating-current control on a shared battery; no hardware sync pair fitted` … `load sharing +/-5 % of rated by droop, +/-2 % with the shared CAN set point; carrier jitter +/-2 us (ASSUMED); up to 16 modules on one CAN (ASSUMED bus load)` — [PCS-CTL:120], [pcs_spec:12885]

→ INST-29 … INST-31

**AC start (grid start with the DC side dead).** Since [D-079](DECISIONS.md) (review R2-14) the sequence is the control
study's executed, fault-injected state machine with five repairs (H1–H5); four of its rules put a condition on the
installation — the grid's state at each contactor command, the battery's voltage before the DC connection, and the
retry limit that survives a reset:

> `grid present, DC side dead (battery absent, empty or its own contactors open): the 6-diode tap feeds the 75 W aux through its OR diode` — [PCS-PWR:26], [PCS-PWR-4W:30]
>
> `rectified 416-651 V at 340-460 V AC, the 75 W block's full rating from 250 V (aux75_spec ratings 'design': from_V 250), the brown-in below it` — [PCS-PWR:24], [PCS-PWR-4W:28]
>
> `<= 3 attempts per start >= 30 s apart, counted across controller resets (H5: count, spacing and lock-out in the recorder flash), then lock-out until a service reset` — [PCS-PWR:27], [PCS-PWR-4W:31]
>
> `then the relay test onto the precharged link (H1): K2 alone, then K1 alone, the converter passive` … `each commanded only with V_dc >= 0.95 x sqrt2 x V_LL,meas, the grid inside +/-10 % at the command (H3) and the DC contactor open - checked at each coil command, not once before the sequence` — [pcs_spec:11428-11429]
>
> `DC port (H4): K_B only with VBX >= 1.05 x sqrt2 x V_LL,meas (the rectification threshold)` … `below the threshold no DC connection, standby on the grid and a report` — [pcs_spec:11431]

→ INST-32, INST-74, and the two consequences in sections 4 and 7 (INST-43, INST-68). What the executed machine leaves
open is listed under [Open](#open-what-the-files-do-not-close).

**Grid-tie close with the DC side live** (review R3-01, [D-080](DECISIONS.md)). The synchronised close from the battery
side now pulls in one AC contactor at a time and checks the grid window at each coil command, as the grid start does:

> `"path": "SYNC -> SYNC_CLOSE_K2 -> SYNC_CLOSE_K1 -> GFL (grid start) / GFM (dead-bus start)",` — [pcs_spec:11923]
>
> `VG inside +/-10 %, |dV| <= 5 %, |dtheta| <= 5 deg, |df| <= 0.1 Hz (ASSUMED), V_dc above the DC closing permissive - at the K2 and the K1 command and every tick of the settle wait until the path forms` — [pcs_spec:12026]

→ INST-74.

**Grid code (set at commissioning).**

> `grid protection EN 50549-1 / GB/T 34120: U/f windows and times, ROCOF / vector shift, active anti-islanding where IEC 62116 applies, LVRT / HVRT with reactive current, P(f), Q(U), cos phi(P) (settings by country)` — [pcs_spec:12853]
>
> `LVRT: 0 pu for 150 ms, 0.2 pu to 625 ms, linear to 0.9 pu at 2 s, reactive current k = 1.5 (dI_q / dU) up to 1.0 Ir; HVRT: 1.2 pu 10 s, 1.25 pu 1 s, 1.3 pu 0.5 s (absorbing reactive current) - FROM MEMORY` — [pcs_spec:12879], [PCS-CTL:114]
>
> `cease to energise within 2 s (IEC 62116 class, FROM MEMORY; not simulated)` — [pcs_spec:12882], [PCS-CTL:117]

→ INST-33

---

## 🛡️ 4 Earthing and insulation monitoring

**PV modules.** Array and battery are one galvanically connected circuit through the shared negative rail:

> `PV array, battery and DC bus are floating with insulation monitoring or one pole earthed, a first earth fault may persist` — [DECISIONS:38]
>
> `IT (floating) with insulation monitoring (PV-C5), OR one pole earthed by the installation; a FIRST EARTH FAULT may persist (the IMD only alarms)` — [insulation report:39]
>
> `ports A and B share BUS- (non-isolated), so they are ONE circuit` — [insulation report:40]

> `the module disables its own by keeping IMD_SW_P/N de-energised (strings open, fail-safe); it still loads each pole with the 6.005 M dividers, which the active monitor must count (system spec)` — [port report:367]
>
> `One active insulation monitor per galvanically connected system` — [guide 04:194]
>
> `PV-P75/100/110 has no residual-current sensor (DC/DC: insulation monitoring only)` — [PV-CTL:50]

→ INST-39, INST-40

> `Y1 4.7 nF to PE_T: 1144 V pole-PE vs 1500 VDC (VY1 p1) = 76 %; MOV3 / Y / IMD strings return through the removable PE_T link` — [PV-PWR:9]
>
> `(6) the PE return for the Y capacitors, MOV3 and the IMD strings (removable strap` — [ARCH-COSTFIRST:134]

→ INST-41

> `the capacitance to PE puts about 11 mA into the protective conductor, so the product needs the high-touch-current measures (PE conductor ≥ 10 mm² Cu and a warning label)` — [DECISIONS:54]
>
> `A PE stud with ≥ 16 mm² Cu (line conductors 35-50 mm²; the stud carries the installation's PE sized to the line` … `conductors - the module's own PE bond is ≥ 10 mm² Cu per D-048, INSTALLATION.md INST-42)` — [ARCH-COSTFIRST:128-129]

→ INST-42. The two records used to give different conductor sizes; since 2026-10-07 the architecture file reconciles
them — the 16 mm² stud carries the installation's PE sized to the line conductors, the module's own PE bond is ≥ 10 mm²
Cu (see below).

**Inverter.** The grid tap ties the DC side to the grid whenever the AC terminals are live:

> `with a battery connected and the grid present, DC- is peak-clamped to the most negative phase; the insulation monitor's PE -> DC- state is then not valid (R_iso- is covered by the residual-current monitor once connected): firmware runs the full two-state insulation test with the grid absent or treats the DC- result as 'clamped by the grid'` — [PCS-PWR:28], [PCS-PWR-4W:32]
>
> `LIVE: the tap ties DC- to the grid phases through its lower diodes (the converter does the same through its body diodes once the AC contactors close)` — [PCS-PWR:24], [PCS-PWR-4W:28]

→ INST-43

---

## 📐 5 Environment

> `−30…+60 °C, full power to 45 °C, derate above` — [REQUIREMENTS:64]
>
> `Cold rating amended by delegation (D-081, review R4 / E11): full operation from -10 C inlet; below -10 C the fans stay off and the module holds only the passive-cooling table of the design check` — [REQUIREMENTS:169]
>
> `| sea level | 82.5 | 82.5 | 82.5 | 82.5 | 82.5 | 78.7 | 71.5 | 62.2 |` — [module report:76]
>
> `at 60 C inlet the firmware's overload tiers are the grade-A tier table's 120 % 2 min 216.0 A, 200 ms 242.9 A (the lowest over 600-950 V; this population holds them at its 900 V corner)` — [PCS-PWR:5], [PCS-PWR-4W:5]

The PV-P75 row is its power held in kW at an inlet of 25, 30 … 60 °C (sea level, [module report:74-76]). PV-P100/110 holds
88.6 kW to 55 °C and 79.3 kW at 60 °C with the battery-port current cap (`| sea level | 88.6 | 88.6 | 88.6 | 88.6 | 88.6 | 88.6 | 88.6 | 79.3 |`,
[module report:90]); without the cap ("thermal only") it holds 110 kW to 35 °C and 100.9 kW at 45 °C ([module report:94]). → INST-44

> `environment: operating -10..+60 C at power (> 45 C derating by module grade - tiers_by_grade, at 60 C grade A 198.0 / 216.0 / 242.9 A; grade B 179.8 / 192.6 / 192.6 A (grade B: the 60 C continuous limit 179.8 A = 124.57 kW at PF 1 and 400 V AC - the rated 125 kW at 400 V needs 180.42 A (the rated 180 A is 124.71 kW))); -30..-10 C inlet: fans off (rated -10 C), standby or the passive table only - the milder rating, decided by cost; a cold-rated fan RFQ is the path back to -30 C at power` … `storage -40..+70 C (ASSUMED: parts' storage ratings to confirm), altitude: see the control board's barrier rating, OVC DC II / AC III, pollution degree external 3 / internal 2; IP rating and enclosure out of scope (schematic and BOM only)` — [PCS-PWR:31], [PCS-PWR-4W:43]
>
> `both Delta fans are rated -10..60 C operating (p.3 4-1; storage -40..+75 C)` — [module report:40]

→ INST-45, INST-47

**Altitude.** The ceiling is set by the control board's barrier, not by the thermal model:

> `DECLARED 2000 m - BARRIER: 2000 m (B1 isolators 8.0 mm minimum clearance). THERMAL MODEL (module_spec, 45 C inlet, PV only; the inverter has no altitude model): PV-P75 100.0 / 93.8 / 86.3 / 79.2 / 70.4 % at 0 / 1000 / 2000 / 3000 / 4000 m; PV-P100/110 80.6 / 80.6 / 77.3 / 68.4 / 60.5 % at 0 / 1000 / 2000 / 3000 / 4000 m` — [PV-CTL:26], [PCS-CTL:25]
>
> `clearance 8.0 / 9.2 / 10.4 / 11.9 mm needed at 2000 / 3000 / 4000 / 5000 m` — [PV-CTL:24], [PCS-CTL:23]
>
> `The 15 mm (WW) parts exist and reach 5000 m` … `above the ~1 USD rule -> NOT fitted, the boards stay rated 2000 m` … `a port SPD with Up,eff <= 4.0 kV and its own altitude rating (module decision, power board): the 8 mm parts then reach 4000 m` — [PV-CTL:26], [PCS-CTL:25]
>
> `(4) module level, not this board: power board B2 6.3 / 7.1 mm at 3000 / 4000 m, the port SPDs' own altitude ratings (Raycap 59.D040: 3000 m max; DEHN 952515 not stated), contactors, fuses and fans not assessed` — [PV-CTL:27], [PCS-CTL:26]

→ INST-46

**Pollution degree, overvoltage category, standards.**

> `pollution degree 3 external / 2 internal, DC OVC II` — [REQUIREMENTS:113]
>
> `EN 62477-1, EN 62109-1/-2, EN IEC 61000-6-2, EN IEC 61000-6-4` — [REQUIREMENTS:114]
>
> `pollution degree 2 inside a closed electronics compartment with all HV boards coated, pollution degree 3 in the cooling-air path (only terminals, fuse bases and arresters there)` — [DECISIONS:38]

→ INST-26, INST-47, INST-50. Humidity, IP rating and noise appear only as the owner's undecided candidate PV-C6 (`> 3000 m derating; ≤ 95 % RH non-condensing; IP20; ≤ 65 dB @ 1 m`, [REQUIREMENTS:79], decision D-009 open); the inverter's declaration puts IP rating and enclosure out of scope.

**Inlet air and airflow.** The module's own fans deliver the airflow; the thermal model keeps a 3 % tolerance on it:

> `R_sa <= 0.060 K/W incl. air rise at ~402 m3/h` … `the airflow reduction is 1.7 % against the 3 % allowed` … `sink 77.4 C at 45 C inlet` — [PV-PWR:4]
>
> `R_sa <= 0.049 K/W incl. air rise at ~456 m3/h` … `airflow x1.000 = 456 m3/h` … `sink 80.0 C at 45 C inlet` — [PV-PWR-4:4]
>
> `airflow 264 CFM (2.11 CFM/kW) against the competitor's 3.8 CFM/kW` — [PCS-PWR:31]
>
> `airflow 352 CFM (2.82 CFM/kW) against the competitor's 3.8 CFM/kW` — [PCS-PWR-4W:43]
>
> `3 x R_sa 0.050 K/W at 149 m3/h` — [PCS-PWR:22]
>
> `4 x R_sa 0.050 K/W at 149 m3/h` — [PCS-PWR-4W:26]

→ INST-48. The files give no allowance for back-pressure, filters or recirculation in the cabinet (see [Open](#open-what-the-files-do-not-close)).

**Cold start.**

> `inlet NTC < fan rating (-10 C) at start-up: fans OFF, module power <= the table row of the inlet reading (table kW, nominal), no other change to the control` … `passive power PV-P75 19.2 / 17.5 / 15.5 kW at -30 / -20 / -10 C inlet; PV-P100/110 19.9 / 14.9 / 4.9 kW at -30 / -20 / -10 C inlet (ESTIMATE +-50 %, module_spec.json); start regardless of the inlet: heatsink NTC 80 C, inductor NTC 135 C` — [PV-CTL:89]

→ INST-49 (the PV modules).

The inverter has the same rule since [D-081](DECISIONS.md) (review R4 E11), on its own passive table — too small to be
useful, which is why its cold rating is full operation from −10 °C inlet:

> `COLD START, fans off (review R4 / E11; pcs_spec thermal_and_losses cold_start; ESTIMATES +/-50 %): kVA at 400 V AC this build holds without its fans, by inlet -30 / -20 / -10 C: 600 V DC 20 / 15 / 8, 650 V DC 14 / 8 / 0, 700 V DC 3 / 0 / 0, 750 V DC 0 / 0 / 0` — [PCS-PWR:23]
>
> `600 V DC 14 / 7 / 0, 650 V DC 5 / 0 / 0, 700 V DC 0 / 0 / 0` — [PCS-PWR-4W:27]
>
> `fans OFF while the inlet NTC reads below -10 C (the AFB1224SHE-F00 rating, -10..60 C); below it the current reference is limited to this table at the measured inlet and V_dc (the warmer inlet row and the higher V_dc point between table points); where the table is 0 the module stays in standby, gates off (aux and contactor coils only)` … `fans ON at an inlet of -10 C or above, or whatever the inlet reads once any heatsink NTC exceeds 80 C` — [PCS-PWR:23], [PCS-CTL:101]
>
> `inlet NTC open / short -> fans ON (protects the module; the fan may then run below its rating)` — [PCS-CTL:101]

→ INST-77 (risk F14, [guide 12:366]).

---

## 🎛️ 6 Communications and control wiring

**Ports and roles** (declared in firmware / configuration; the hardware is one CAN, one RS-485, one stop input, one
status relay and, on the inverter only, one Ethernet port):

> `module bus (paralleling, carrier synchronisation by CAN time-stamping, software addressing) = the one CAN; BMS = RS-485 (Modbus RTU) or, when the module is not paralleled, CAN; EMS = Ethernet (Modbus TCP, port 502, not fitted on PV-CTL) or RS-485; HMI / local PC tool = Modbus TCP over the Ethernet bridge or the RS-485 at 9600-8-N-1 (the HMI panel and its 12 V supply are a cabinet accessory, not added; no web server: the bridge is transparent)` — [PV-CTL:49]
>
> `EMS = Ethernet (Modbus TCP, port 502, fitted) or RS-485` — [PCS-CTL:49]
>
> `One Ethernet port, not the PMA's two: a daisy chain is the cabinet's switch.` — [PV-CTL:49], [PCS-CTL:49]
>
> `Where it fails: a CAN-only BMS whose bit rate or identifiers cannot share the module bus while the modules run in parallel -> BMS on RS-485 needed (the EMS then on Ethernet); on PV-CTL (Ethernet not fitted) fit the Ethernet option first` — [PV-CTL:49], [PCS-CTL:49]

→ INST-51 … INST-54, INST-56

**Termination, addresses, loss of communication.**

> `CAN: split 2 x 60.4 R + 4.7 nF switched in by a solder jumper (1); RS-485: 120 R by a solder jumper (1); open = not terminated (the two bus ends close theirs; fail-safe receiver). Selectable today, no DIP added. Module address: by software over the module CAN (firmware row), no address DIP` — [PV-CTL:48], [PCS-CTL:48]
>
> `ramp to zero and stop on communication loss (ASSUMED 1 s on CAN)` — [PCS-CTL:88]
>
> `ramp to zero and stop on communication loss (grid-following: fallback to zero power; grid-forming keeps forming)` — [pcs_spec:12849]

→ INST-53, INST-55

**Stop chain, ready permissive, battery-fault input, status relay.**

> `ON >= 8.8 V (a 24 V PLC output: ON >= 15 V), OFF <= 3.4 V; the dry contact fed from S_24V >= 12 V gives 9.7 V; open contact, cut wire or lost SELV -> STOP_OK low within 1.47 ms (RC from the 5.1 V clamp to 0.8 V) + 12 ns isolator, no firmware` — [PV-CTL:13], [PCS-CTL:10]
>
> `Rev A2: J_STOP 2 / 3 stop line in / out and 4 / 5 0 V in / out (loop-through) to chain a cabinet 24 V stop line (each module 0.84 mA at 36 V); the S_EN_SRC dry-contact supply serves 1 module (ON >= 8.8 V at S_24V >= 12 V); READY permissive (PMA rocker p.13) = J_IO 6-7 in series (wire link if unused): open = stop in hardware - the firmware sees one STOP_OK for both.` — [PV-CTL:47], [PCS-CTL:47]
>
> `DI1 battery fault / BMS contact (PMA BAT_FAULT): J_IO 4 = 5 V wetting via 1.0k, 5 = input, 10k down, 10k + 100 nF (1.1 ms), 5.1 V clamp -> CA-IS3842HW -> GPIO52 (XINT3): closed contact 4.43 V >= 2.2 V (VIT+ 2.0 V + 10 %); NO / NC meaning set in firmware.` — [PV-CTL:47], [PCS-CTL:47]
>
> `energised = healthy, NC on any trip, stop or supply loss` … `SELV contacts only (<= 30 V AC / 60 V DC, 2 A 30 V DC)` — [PV-CTL:47], [PCS-CTL:47]

→ INST-57 … INST-60

**What may be connected, and how far from live copper.**

> `Field I/O (isolated CAN/RS-485, digital inputs, BMS gateway, Ethernet): DVC A on both sides of their isolators; the BMS communication port is assumed SELV/PELV per the battery system's own standard.` — [insulation report:44]
>
> `The BMS / PCS side may earth its 0 V; the SELV 0 V is tied to PE through 1 MΩ ‖ 4.7 nF Y2` — [ARCH-COSTFIRST:113]
>
> `(3) layout: every SELV connector (fans, stop / IO, CAN, RS-485, Ethernet) and the SELV harness >= 9.2 mm clearance from LIVE copper and >= 10.0 mm creepage (PD2) - the architecture's 10 mm keep-out holds to 3000 m, 4000 m needs 10.4 mm` — [PV-CTL:27], [PCS-CTL:26]
>
> `SELV connectors at the front, ≥ 10 mm from any live part` — [ARCH-COSTFIRST:133]

→ INST-61, INST-62

**No rated safety function; no cabinet 24 V.**

> `a cabinet that needs a rated stop must open its own DC switching devices; service needs isolated tools` — [guide 12:283]
>
> `the module is dark when both ports are below ~200 V` — [ARCH-COSTFIRST:499]
>
> `a port rises above 206–215 V` — [guide 04:356]

→ INST-63, INST-64

---

## 🧰 7 Service

**Discharge time and labels.** The passive bleeders take the banks to 60 V once the sources are disconnected (the inverter's figure is stated with the DC contactor and both AC contactor sets open):

> `port A 289.8 uF incl. decoupling: 1100 -> 60 V in 8.2 min (bleeder alone 8.2 min, with the 6 M divider 7.5); worst case (R +1 %, C +10 %, divider, from 1144 V) 8.4 min; port B 334.8 uF incl. decoupling: 1100 -> 60 V in 9.5 min (bleeder alone 9.5 min, with the 6 M divider 8.7); worst case (R +1 %, C +10 %, divider, from 1144 V) 9.7 min` … `label 'wait 10 min'` — [PV-PWR:14]
>
> `worst case (R +1 %, C +10 %, divider, from 1144 V) 11.2 min` … `label 'wait 15 min'` — [PV-PWR-4:14]
>
> `(3) the label "Hazardous voltage — wait 10 min (PV-P100/110: 15 min) after` — [ARCH-COSTFIRST:131]

→ INST-65, INST-66

> `with the DC contactor and both AC contactor sets open, bank, films, C_f + C_d (60 uF per phase, star on M) and the aux input bulk (6.6 uF) fall below 60 V between any two conductors after 14.7 min worst case (13.3 nominal; R +1 %, C +10 %, the five 6 M dividers VB / VA / VC1-3 credited, from 1050 V with C_f at the 460 V AC peak) <= label 'wait 15 min'` — [PCS-PWR:3]
>
> `fall below 60 V between any two conductors after 14.8 min worst case` — [PCS-PWR-4W:3]
>
> `the aux input and, with a welded precharge relay, the DC link are live whenever the AC terminals are live: enclosure label 'isolate AC and DC, wait 15 min'` — [PCS-PWR:28], [PCS-PWR-4W:32]

→ INST-67, INST-68

**Contactors and tools.**

> `above 300 A flags the contactor for replacement.` — [guide 04:175]
>
> `terminal voltage does not follow; a weld blocks restart.` — [guide 04:177]
>
> `"HAZARDOUS VOLTAGE — isolated debug probe only (≥ 1000 V DC rated, e.g. an isolated XDS)"` — [ARCH-COSTFIRST:120]

→ INST-69, INST-70

---

<a id="where-the-records-disagree"></a>

<a id="standing-restrictions"></a>

## ⚠️ Standing restrictions (review R4)

The economical re-check R4 ([review_r4.csv](../../gen/data/review_r4.csv), [D-081](DECISIONS.md)) accepted several
limitations instead of adding hardware, each with a boundary that may not be waived. These are the installation-facing
ones — what a site must not do, and which row of this list carries it. They are accepted limitations, not ignored
hazards; the full set, with what would lift each, is on
[12 · Risks](../guide/12-risks-and-open-items.md#standing-restrictions).

| Restriction (R4 area) | What the installation must not do | Boundary that stays | Rows |
|---|---|---|---|
| PV-P75 at 550 V (E07) | size a site for 75 kW delivered at a 550 V battery or bus: the 135 A port carries 75 kW from 556 V, 74.25 kW at 550 V × 135 A (the delivered rating from 562 V at equal port voltages) | no 75 kW claim at that corner; the arithmetic is not waived | — (REQUIREMENTS PV-04 / PV-05; risk G2) |
| PV-P100/110 battery port, 145 A | expect 100 kW below 690 V (A→B) / 697 V (B→A), or full power above 35 °C inlet; PV-P110's 110 kW is a PV-input rating | the ideal minima (689.66 / 758.62 V) are not ratings: the delivered direction and the losses stay in | INST-44 |
| Battery-less DC bus (E04) | run the bus above the 782 V clamp, put a battery, filter or another source on the link, or wire outside the harness contract | the one-node 909 V is a model validity, not a rating; above 782 V the coordination margin is not held | INST-71, INST-76 |
| Grid forming (E06) | treat the declared envelope as compatibility with any 400 / 230 V island load: grid forming is released for managed island loads with an agreed voltage-time and interruption specification | not a universal ITIC or UPS claim; IEC 62040-3 not claimed | INST-28, INST-75 |
| Cold ambient (E11) | run the inverter at power below −10 °C inlet, or the PV modules below −10 °C other than on their passive table | a storage rating is not fan qualification; an untested passive table is not a full-power guarantee | INST-44, INST-49, INST-77 |
| Upstream DC fault protection (E10) | leave the 0.97–1.25 kA band (PV battery port), the 0.97–2.01 kA band or a prospective current above 7.5 kA (inverter DC terminals) without an upstream device that clears it | no waiver of the uncovered bands: the actual device, its clearing time, the prospective current and L/R are bound per installation | INST-11 … INST-15 |
| Grade B (E05) | size a grade-B inverter for 125 kW at 60 °C inlet: it is a 124.57 kW module there (PF 1, 400 V) | grade B only inside its published map; short-circuit survival is still a release gate | INST-73 |
| Three-wire against four-wire (E12) | use the three-wire build for line-to-neutral or unbalanced loads; on a TN grid run it below 680 V DC with more than the stated battery capacitance to earth | no four-wire or unbalanced-load claim on the three-wire hardware | INST-16, INST-34 … INST-38 |

<sub>Not installation-facing, and so not rows here: the PV comparator's 1.09 nuisance margin (E08, an availability
restriction of the module) and the deferred DAB-D60 (E09, no product to install).</sub>

## ⚠️ Where the records disagree

Each row gives the number this page uses and the other record. None is resolved here; the records are the owner's to
reconcile. Rows marked *reconciled* were settled by the notes ARCHITECTURE-COSTFIRST.md and ARCHITECTURE-PCS.md received
on 2026-10-07; they stay listed so that the trace is kept. The generated line is used where one exists, as the [documentation index](../README.md) says a record wins
over a page.

| What | This page uses | The other record | Note |
|---|---|---|---|
| **Battery-port fault band of the PV modules** (INST-11, INST-12) — band and time reconciled on 2026-10-07, L/R not | 0.97–1.25 kA (967–1,250 A) within 0.26 s; ≤ 50 kA at L/R ≤ 1 ms — [port report:456], [port_spec:696-702], [guide 04:183] | 0.95–1.3 kA within 0.25 s; ≤ 50 kA at L/R ≤ 3 ms — [ARCH-COSTFIRST:265], [ARCH-COSTFIRST:269-274], [ARCH-COSTFIRST:584]; repeated as risk B6, 0.95–1.3 kA — [guide 12:284] | The architecture now names the drawn values beside its own and says the stricter of each pair (0.95–1.3 kA, 0.25 s) satisfies both, and that this page carries the drawn values ([ARCH-COSTFIRST:269-274]). Still different: its 50 kA at **L/R ≤ 3 ms** against the drawn ≤ 1 ms — the 1.0 kA rule rests on contactor data at L/R ≤ 1 ms, "not at the architecture's 3 ms" ([port report:457]); INST-12 keeps 1 ms |
| **PE conductor and leakage current** (INST-42) — conductor sizes reconciled on 2026-10-07, touch current not | PE conductor ≥ 10 mm² Cu and a high-touch-current label, about 11 mA — [DECISIONS:54], [guide 03:460], [guide 12:282] | PE stud ≥ 16 mm² Cu, which "carries the installation's PE sized to the line conductors", and "the module's own PE bond is ≥ 10 mm² Cu per D-048" — [ARCH-COSTFIRST:128-129]; "touch current with PE open ≤ 3.5 mA", estimate 0.5–16 mA rms — [ARCH-COSTFIRST:380-383]; 1.06 mA at 150 Hz and 14.2 mA at 20 kHz — [port report:398] | the two sizes now describe two conductors: the installation's PE at the stud (sized to the 35–50 mm² line conductors, ≥ 16 mm²) and the module's own bond (≥ 10 mm²); INST-42 states both. Still different: D-048 accepted about 11 mA, above the architecture's 3.5 mA touch-current figure |
| **Three-wire build on a TN grid** (INST-16) — reconciled on 2026-10-07 | allowed with a battery capacitance to earth ≤ 5.8 µF below 680 V; four-wire or an isolation transformer where it is exceeded — [pcs_design report:455] | "the installer must connect 3W+PE through a transformer or keep N unloaded", now marked "superseded by the drawn rule: the three-wire build limits the battery's earth capacitance to about 6 µF on a TN grid" — [ARCH-PCS:110-112] | the architecture text was for the three-level stage with 150 V of zero sequence; the two-level study (D-053) recomputed it ([pcs_design report:451]) and the architecture now defers to it; no difference left |
| **100 Hz battery current of a single-phase load** (INST-18) — reconciled on 2026-10-07 | up to 47 A rms at 750 V (216 A tier), 37 A at 950 V — [PCS-PWR-4W:38] | about 39 A rms at 750 V — [pcs_spec:12808]; about 45 A rms for about 42 kW, now annotated "the drawn figures are about 39 A at the operating-map point and 47 A rms worst in the four-wire design check - different load points" — [ARCH-PCS:118-120] | different load points, now stated in the architecture too: the 39 A matches one phase at the rated 180 A (230 V × 180 A ÷ (√2 × 750 V)), the 47 A one phase at 216 A, the 45 A the earlier T-type stage; INST-18 states the largest of the current design |
| **Discharge time to 60 V** (INST-65, INST-66) | PV-P75 9.7 min, PV-P100/110 11.2 min worst case — [PV-PWR:14], [PV-PWR-4:14] | 9.2 min and 10.5 min — [port report:443], [port report:444] | the check counts the leg decoupling ("incl. decoupling"); both fit the labels (10 / 15 min); the PV-P75's 0.3 min is the thinnest margin on any label |
| **Inlet temperature for full power** (INST-44) | full power to 45 °C, derating above (PV-P75 62.2 kW = 75 % at 60 °C) — [REQUIREMENTS:64], [module report:76], [DECISIONS:62] | "60 C inlet (module_spec: full power to 60 C)" — [port report:24], [port report:60] | the port report's 60 °C is a conservative fuse-temperature basis worded as a rating; INST-44 is unaffected |
| **Altitude derating of the PV modules** (INST-46) | derating from 1,000 m (PV-P75 93.8 / 86.3 / 79.2 / 70.4 % at 1,000–4,000 m) — [PV-CTL:26] | "No derating from 25 to 60 °C, to 4000 m" — [DECISIONS:29]; "the thermal design claims no derating to 4000 m (D-029)" — [insulation report:47]; PV-C6 "> 3000 m derating" — [REQUIREMENTS:79] | D-029 is the earlier fan and platform, replaced by the cost-first module of D-056 and D-075; PV-C6 is a candidate the owner has not adopted (D-009) |
| **Are the AC surge arresters monitored?** (INST-24, INST-26) | not monitored; a failure goes unseen, the alarm is still RFQ — [guide 12:362], [guide 12:458], [guide 09:835] | "monitored" — [pcs_design report:433] | the generated inverter checks do not say; treat as not monitored until the records agree |
| **Wording of the inverter's two-source label** (INST-68) | 'isolate AC and DC, wait 15 min' — [PCS-PWR:28] | "two sources — isolate AC and DC; wait 15 min" — [guide 09:493], [guide 12:289] | same meaning; the generated line is used |
| **Uncoordinated DC/DC on a battery-less bus** (INST-71) — reconciled (found on the R4 re-verification of 2026-10-07) | gates off 1.50 / 1.94 ms after the step, bus left at 1,060–1,061 V (the coupled PCS / PV trajectory of [D-080](DECISIONS.md)) — [pcs_spec:12773], [PCS-CTL:107], [pcs_control report:528] | the PV module's coordination row now prints the same figures ("1.50-1.94 ms after the rejection and the bus is left at 1060-1061 V, latched") — [pv_control report:343]; it printed D-079's 0.59–0.75 ms and 1,048–1,110 V when this row was written | no difference left; the row's own rule (209 µs, × 2 to 782 V) did not change |

<a id="open-what-the-files-do-not-close"></a>

## ⚠️ Open: what the files do not close

Conditions the integrator cannot yet be held to, because the files give no value or say the value is not confirmed.

- **PV-only start (INST-10).** The making rating of the PV contactor at 1000 V is not published; the module's mitigation is the precharge-first rule with port B live ([PV-PWR:15]).
- **Let-through of the AC path (INST-22).** The conditional short-circuit current of the AC contactors with the upstream device is an RFQ / test item; no value is on file ([PCS-PWR:29]).
- **Fuse data (INST-14, INST-15).** The inverter's 400 A link is read from chart averages with no tolerance band, and its high-current tail does not match the tabulated I²t — an RFQ / test item ([PCS-PWR:15]). On the PV battery port Hongfa states no L/R for the aR link's 50 kA, "1.5 kA once" is a no-fire test and the 1.0 kA rule rests on an interpolation ([port report:457]).
- **Rating of the upstream device.** The lines quoted for INST-11, INST-12, INST-14 and INST-15 give a current band, a clearing time, a prospective current and an L/R; they do not give the upstream device a DC voltage rating (the contactor data they are matched to are at 1000 V).
- **Neutral bonding.** Where the neutral is bonded to earth in the four-wire build, on grid and off grid, is not stated; the files declare TN-S or TT ([PCS-PWR-4W:41]).
- **PE conductor of the inverter.** No size is stated. The files give earth-leakage currents of 73–90 mA at 32 kHz through the common-mode choke ([pcs_design report:453]) and 45–52 mA per µF at 150 Hz below 680 V ([pcs_design report:439-446]).
- **Cabinet airflow (INST-48).** No allowance for back-pressure, filters or recirculation is stated beyond the 3 % tolerance of the thermal model.
- **The inverter's altitude derating** is not modelled ([PCS-CTL:25]). Its cold start is modelled since D-081, as an
  estimate (±50 %) with the enclosure ASSUMED, and not validated in a cold chamber (INST-77; risk F14, [guide 12:366]); a
  cold-rated fan is an open RFQ.
- **What the control study does not cover (INST-31, INST-32, INST-74).** Parallel units (the restoration consensus over the module CAN is stated, not modelled) and the AC start's electrical transients ([pcs_control report:30]). The AC start itself is a state of the checked and executed machine since D-079 (review R2-14); left open there: DC_MATCH stays in the table though the repaired start never enters it, the H2 numbers live in the code but not yet in the JSON, RECTIFY with a battery present has no DC-match timeout, a grid sag during the 100 ms pull-in is not among the 35 cases, and the relay timings and the 40 W auxiliary draw are ASSUMED ([DECISIONS:85]). The grid-tie close from the battery side (INST-74, since D-080) rests on an ASSUMED synchronisation model, path check after K1 (a small active-power step) and relay timings ([DECISIONS:86]).
- **Grid stiffness (INST-27).** The adopted ride-through measure leaves 23.0 A to the window, 20.5 A net of an ASSUMED ADC noise (±1 LSB) and 18.4 A to the CMPSS backup's lower edge, in the averaged model; a switched model and the bench decide whether the fallback, with its 7.71 MVA-per-module condition, is ever needed. The stiffness estimator's ±30 % is ASSUMED.
- **DC/DC coordination (INST-71, INST-72, INST-76).** The peaks inside the enforced window are simulation figures
  (averaged; the PV modules' V<sub>B</sub> loop, capacitor ESR and the skin effect not credited; the harness ASSUMED;
  unequal run lengths not studied) until a switched model and the bench confirm them ([pcs_control report:584],
  [pcs_control report:592]). The one-node 909 V and the cable model's 846 V are model validities, not ratings; above the
  782 V clamp the PV row's × 2 time margin is not held ([pv_control report:343]). No hardwired trip line is drawn and none
  is needed inside the window; DC/DC converters of other makes are not covered by any file.
- **Four-wire bolted output short (INST-75).** The 13.3 A between the hold's largest sensed peak and the CMPSS backup's lower edge rests on the averaged model with a rebuilt ripple; a switched model and the bench are the gate, and the files name no downstream device or its clearing time ([pcs_design report:710]).
- **Residual-current monitor (INST-23).** The module's own type-B monitor is an RFQ contract (thresholds from IEC 62109-2, FROM MEMORY); no part with a data sheet is chosen ([PCS-PWR:17]).
- **Grid-tie switch (INST-29).** The command path from the inverter to the site's switch is not described.
- **Surge protection on the DC side.** The varistor thermal links have no DC rating ([guide 12:281]), and IEC 61643-32 may ask for a Type 2 arrester with In ≥ 5 kA where the installer fits none ([port report:371]).
- **Humidity, IP rating, noise.** Only the candidate PV-C6 states them; the owner has not adopted it (D-009).
- **Bus parameters.** The CAN bit rate and the RS-485 settings other than the service port's 9600-8-N-1 are not stated.
- **Standard texts.** The clauses behind INST-08, INST-16, INST-23, INST-24, INST-26, INST-33 and INST-50 are quoted FROM MEMORY (risk B1, [guide 12:279]).

---

## 📐 The list

Source labels are links to the line: `PV-PWR`, `PV-PWR-4`, `PV-CTL`, `PCS-PWR`, `PCS-PWR-4W`, `PCS-CTL` are the design
checks under `hardware/*/outputs/`; `pcs_spec` and `port_spec` the JSON specs; `port report`, `pcs_design report`,
`pcs_control report`, `pv_control report`, `module report`, `insulation report` the reports under `sim/out/`. Rows
INST-71 to INST-74 were added and INST-27 rewritten on 2026-10-07 ([D-078](DECISIONS.md), [D-079](DECISIONS.md)); after
the re-check R3 the same day INST-75 was added and INST-27, INST-28, INST-44, INST-71, INST-73 and INST-74 were updated
([D-080](DECISIONS.md)); after the economical re-check R4 INST-76 and INST-77 were added and INST-28, INST-44, INST-49,
INST-71 and INST-73 were updated ([D-081](DECISIONS.md)).

| Requirement | Why (the module's own limit) | Source |
|---|---|---|
| **INST-01** · PV port A — Connect a current-limited PV array only. | Port A has one contactor and no fuse, series diode or precharge; K_A breaks 300 A at 1000 V, both polarities, 200 operations. | [PV-PWR:15], [PV-PWR-4:15] |
| **INST-02** · PV port A — Array short-circuit current at the module terminals ≤ 168.8 A (PV-P75) or ≤ 225.0 A (PV-P100/110). | 1.25 × the 135 A / 180 A port rating; K_A margin ×1.78 / ×1.33 breaking, ×2.22 / ×1.67 carrying. | [PV-PWR:15], [PV-PWR-4:15] |
| **INST-03** · PV port A — Array open-circuit voltage ≤ 1000 V. | Over-voltage trip band 1039–1110 V. | [PV-PWR:15], [PV-PWR-4:15] |
| **INST-04** · PV port A — Connect the array through string fuses in the combiner (IEC 62548) and a load-break DC isolator, which is also the maintenance disconnect. | The module has no fuse on port A. | [PV-PWR:15], [PV-PWR-4:15], [ARCH-COSTFIRST:281] |
| **INST-05** · PV port A — Array and cable capacitance, terminal to terminal, ≤ 1.1 µF behind ≥ 11 µH (ESTIMATE). | Bounds the 330 A discharge peak that the K_A making estimate assumes; back-calculated. | [PV-PWR:15], [PV-PWR-4:15] |
| **INST-06** · PV port A — No stiff DC source (battery, DC bus, rectifier, another converter) on port A; a stiff source needs the fused, precharged lean port instead. | K_A would close onto the empty bank (290 / 386 µF) at a peak of about 7.4 / 8.3 kA at 1000 V, 53× / 59× its 140 A making rating, weld likely; a fault from such a source is cleared only by K_A (≤ 300 A normal opening, hold-off to 967–1033 A, 1500 A once). | [PV-PWR:15], [PV-PWR-4:15] |
| **INST-07** · PV port A — No reverse power from port B into an array on port A: keep the port declared PV. | No series diode, fuse or precharge: the 387–413 A port window protects the module, not the array, which conducts backwards limited only by its modules' reverse-current rating and the combiner's string fuses; firmware holds port A current ≥ 0 as the second layer. | [PV-PWR:15], [PV-PWR-4:15] |
| **INST-08** · PV port A — Array insulation resistance ≥ 33 kΩ before K_A can close. | Firmware closes K_A only after an insulation measurement ≥ 33 kΩ (IEC 62109-2 rule, FROM MEMORY). | [guide 04:169] |
| **INST-09** · PV port A — Array capacitance to PE ≤ 16.5 µF (PV-P75) / 22.0 µF (PV-P100/110) (ASSUMED: 200 nF/kWp). | Counted as common mode only; the port design's insulation-monitor settling (τ 7.1 / 9.4 s at 1 MΩ, three-sample prediction) is sized on it. | [PV-PWR:15], [PV-PWR-4:15], [port report:34], [port report:367] |
| **INST-10** · PV port A — OPEN: a PV-only start (port B not live) closes K_A onto the empty bank at the array's V<sub>oc</sub>; no installation condition makes this covered. With port B live the converter precharges bank A to within 20 V first. | Estimated making current about 499 A (PV-P75) / 555 A (PV-P100/110) against a published making rating of 140 A at 20 V only; Hongfa to confirm (R-04). | [PV-PWR:15], [PV-PWR-4:15], [guide 12:287] |
| **INST-11** · PV port B — The upstream battery or DC-bus protection interrupts any current of 0.97–1.25 kA (967–1,250 A) into the module's battery port within 0.26 s. | K_B is held above the 967–1033 A hold-off band and the 250 A aR link breaks only from 1.25 kA (5 × In): the module clears nothing in this band. | [port report:456], [port report:434], [port report:431], [port_spec:696-702], [guide 04:183] |
| **INST-12** · PV port B — The prospective short-circuit current at the module's battery terminals is ≤ 50 kA with L/R ≤ 1 ms. | The contactor's breaking data exist only at L/R ≤ 1 ms; Hongfa states no L/R for the aR link's 50 kA. | [port report:456], [port report:457], [port_spec:696-702] |
| **INST-13** · PV port B — The cable from the battery protection to the module is protected by that upstream device. | The module's fuses protect the module, not the external cable. | [port report:456], [guide 04:193], [ARCH-COSTFIRST:275] |
| **INST-14** · PCS DC port — The battery-side protection interrupts any current of 0.97–2.01 kA into the PCS DC port within 2.5 s (25 s at 1 kA). | The contactor is held above 967–1033 A; the 400 A link is below its breaking range or slower than the contactor's 180 °C curve; 2.5 s is the contactor's 130 °C curve at the band top. | [PCS-PWR:15], [PCS-PWR-4W:16], [port report:466], [port report:471], [port_spec:895-899] |
| **INST-15** · PCS DC port — The battery's prospective short-circuit current at the PCS DC terminals is ≤ 7.5 kA (L/R ≤ 1 ms), or the battery-side protection interrupts currents above that within the contactor's short-circuit capacity (6 ms at 8 kA, 1.5 ms at 10 kA). | The 400 A links protect the contactor only between 2.0 and 7.4 kA; above 7,487 A prospective the capacity is exceeded and the let-through peak reaches the bounce region (≥ 6 kA from 6,083 A, ≥ 8 kA from 19,348 A). Fuse data are chart averages (±10 % assumed). | [PCS-PWR:15], [PCS-PWR-4W:16], [port report:468], [port report:471], [port_spec:904] |
| **INST-16** · PCS DC port — Battery capacitance to earth ≤ 5.8 µF (300 mA budget) when a three-wire build on a TN grid, or a four-wire build in mode B, runs below 680 V DC at 400 V AC (about 780 V DC at 460 V AC); ≤ 24.1 µF if the 10 mA-per-kVA rule governs. The 1–20 µF range is ASSUMED; the IEC 62109-2 values are FROM MEMORY. | Below those DC voltages the min-max zero sequence puts 48–55 V rms at 150 Hz on the battery's earth capacitance: 45 mA per µF at 400 V AC (52 mA per µF at 460 V AC). | [PCS-PWR:29], [PCS-PWR-4W:34], [pcs_design report:435], [pcs_design report:439-446], [pcs_design report:451], [pcs_design report:455], [pcs_design report:806], [pcs_spec:7355-7356] |
| **INST-17** · PCS DC port — The battery's insulation and its insulation monitor are rated for ±V<sub>dc</sub>/2 against earth in operation. | The battery rack sits at ±V<sub>dc</sub>/2 against earth. | [pcs_design report:455] |
| **INST-18** · PCS DC port — The battery tolerates the 100 Hz ripple of a single-phase or unbalanced AC load on the four-wire build: up to 47 A rms at 750 V DC (one phase at the 216 A tier) and 37 A rms at 950 V DC (the hand-over quotes about 39 A rms at 750 V); an off-grid half-wave load gives 43 A rms at 750 V. | The unbalanced load's 100 Hz power pulsation goes to the battery, not to the DC midpoint: an "installation item". | [PCS-PWR-4W:38], [PCS-PWR-4W:39], [pcs_spec:12808] |
| **INST-19** · PCS DC port — The battery's voltage range clears the grid-dependent window: DC link ≥ 624 V to connect at 400 V AC (718 V at 460 V) and 643 V (737 V) for rated current at the worst power factor; four-wire mode A needs 724 V to connect at 230 V line-to-neutral (796 V at +10 %, 833 V at +15 % grid) and 746 / 819 / 855 V for rated current. | Firmware permissive: below it the bridge would rectify into the battery (846 A peak, 730 A mean at 460 V AC on 590 V DC, stiff grid). 5 % loop headroom and dead-time compensation ASSUMED. | [pcs_spec:12858], [pcs_spec:12859], [pcs_spec:12860], [PCS-PWR-4W:34], [PCS-CTL:94] |
| **INST-20** · AC port — The upstream AC protection is rated ≥ 250 A so that the 216 A 2-minute tier does not trip it: gG fuses 250 A, or an MCCB with an instantaneous release. | The inverter has no AC fuse in its main power path; its current limiter holds 367 A peak (259 A rms) for ≤ 200 ms, then trips. | [PCS-PWR:29], [PCS-PWR-4W:41], [pcs_spec:12311] |
| **INST-21** · AC port — The upstream device's breaking capacity ≥ the prospective short-circuit current at the connection point. | A grid-fed leg short is limited only by L2 + L1 until L1 saturates (0.22 ms stiff … 1.66 ms at SCR 5), then by the installation: 5,834 A rms (16,502 A peak) stiff, 3,543 A at SCR 50, 2,229 A at SCR 20, 781 A at SCR 5. | [PCS-PWR:29], [PCS-PWR-4W:41], [pcs_spec:12279-12308] |
| **INST-22** · AC port — The upstream device's let-through stays within the module's AC-path withstand (contactor conditional short-circuit current with that device, busbars, L2 / L1). OPEN: RFQ / test item, no value on file. | The two AC contactors (AC-1 ≥ 248 A) are disconnecting devices, not short-circuit protective devices; they must withstand that let-through. | [PCS-PWR:29], [PCS-PWR-4W:41], [pcs_spec:12310] |
| **INST-23** · AC port — Residual-current protection upstream is type B where the code requires an RCD. | The module has its own type-B residual-current monitor, an RFQ contract since [D-078](DECISIONS.md) (part not chosen; ASSUMED): firmware trips at ≥ 1,250 mA continuous within 0.3 s and at 30 / 60 / 150 mA steps within 0.3 / 0.15 / 0.04 s, IEC 62109-2 class figures FROM MEMORY. | [PCS-PWR:29], [PCS-PWR:16], [PCS-PWR:17], [pcs_spec:12854] |
| **INST-24** · AC port — The earthing system is TN-S or TT. | Declared; the three-wire build in TN is bound by INST-16. The AC arresters are rated U<sub>c</sub> 465 V AC ≥ 1.1 × 400 V for an earth fault in IT. | [PCS-PWR:29], [PCS-PWR:16] |
| **INST-25** · AC port — The grid is 400 V AC ±15 % at 50 or 60 Hz with a strength from SCR 5 to stiff. | Design basis of the PLL and the current loop (PM ≥ 40°, GM ≥ 6 dB from stiff to SCR 5; resonance 2.2 kHz at SCR 5, 9.4 kHz stiff). | [pcs_spec:12804], [pcs_spec:12791], [pcs_spec:12803], [PCS-PWR:11], [PCS-PWR:31] |
| **INST-26** · AC port — The AC terminals are in overvoltage category III and the DC terminals in category II. | AC clearances and the AC arresters assume a 4 kV rated impulse for OVC III (2.5 kV with the AC SPD credit); DC OVC II from ECO-10. | [PCS-PWR:24], [PCS-PWR:31], [REQUIREMENTS:113] |
| **INST-27** · AC port — *Rewritten 2026-10-07 ([D-079](DECISIONS.md)); its figures re-tabled after the re-check R3 ([D-080](DECISIONS.md)).* Ride-through at rated power needs no pre-dip power limit with the adopted firmware measure (simulated, averaged model). If a switched model or the bench shows less than its margin — 23.0 A to the window edge, 20.5 A net of an ADC-noise term, 18.4 A net to the CMPSS backup's lower edge — the fallback applies: the module rides the profile through at rated current only where the connection's fault level per 125 kW module is below 7.71 MVA (n modules at one point: the point's fault level / n); above it the firmware derates the pre-dip power to 94.5 / 104.5 / 114.0 / 124.0 kW for dips to 0 / 0.1 / 0.2 / 0.3 pu, engaged by its own grid-stiffness estimate at an estimated SCR ≥ 61.7. (Before D-080: 8.07 MVA, 95.5 / 105.5 / 115 kW, SCR 64.5.) | With the 270 A ride-through clamp on the divider-inverted C<sub>f</sub> voltage the onset peaks at 382 A (403 A at the worst corner), screened against the lower edge of the two hardware layers, 423.9 A (window 426.0 A); with the D-077 limiter alone rated current passes only up to SCR 80.2 / 113.8 / 184.3 / 793.8 at 0 / 0.1 / 0.2 / 0.3 pu. Estimator accuracy ±30 % and the ADC noise (±1 LSB) ASSUMED; the profile is FROM MEMORY. | [pcs_spec:12880], [pcs_spec:12881], [pcs_spec:12769], [PCS-CTL:115], [PCS-CTL:116], [pcs_control report:291], [pcs_control report:347] |
| **INST-28** · AC port — Off-grid loads: crest factor ≤ 2.04 at rated rms (146.6 A rms at CF 2.5, 122.2 A rms at CF 3.0); direct-on-line motors ≤ 36 A rated at a 6× start — sized on the tiers of the module's grade and inlet temperature (INST-73). Grid forming serves managed island loads with an agreed voltage-time and interruption specification, recorded with the module serial; general-purpose off-grid or UPS-like supply is not released ([D-081](DECISIONS.md)). | The current limiter holds 367 A peak; the overload tiers are 216 A for 2 min and 259.2 A for 200 ms — grade A's to 45 °C inlet; grade B and higher inlets hold less ([D-080](DECISIONS.md)). The off-grid envelope (REQUIREMENTS AC-04) is the module's declared output capability on a load step, not a load-compatibility claim. | [PCS-PWR:31], [PCS-PWR-4W:43], [pcs_spec:12709-12724], [pcs_spec:12874], [PCS-CTL:109] |
| **INST-29** · AC port — An unplanned transfer to an island needs the site's grid-tie switch to open; the interruption is about 150 ms. A seamless unplanned transfer needs a static transfer switch (out of scope). | Detection 100 ms (ASSUMED) + switch drop-out 50 ms (ASSUMED, CHINT NXC-225 class). The command path to that switch is not described in the files. | [PCS-CTL:119], [pcs_spec:12884] |
| **INST-30** · AC port — Generator following: the genset's frequency stays within 45–55 Hz (55–65 Hz at 60 Hz), its ROCOF ≤ 4 Hz/s and its voltage 0.85–1.10 U<sub>n</sub>; the genset keeps ≥ 30 % of its rating (discharge ≤ P<sub>load</sub> − 0.3 P<sub>gen</sub>, charge ≤ 0.9 P<sub>gen</sub> − P<sub>load</sub>); power ramps ≤ 10 % of P<sub>gen</sub> per s; droop 4 % assumed. ASSUMED — standards not on file. | Wet-stacking floor of the genset; no reverse power. | [PCS-CTL:113], [pcs_spec:12878] |
| **INST-31** · AC port — Parallel modules on one AC bus: grid-following modules are independent current sources and the EMS splits P and Q (one shared set point); grid-forming modules share by droop (P-f / Q-V): ±5 % by droop, ±2 % with the shared CAN set point; modules on a shared battery rely on the zero-sequence circulating-current control. | No hardware synchronisation pair is fitted; the carrier is synchronised over CAN (jitter ±2 µs ASSUMED). | [PCS-CTL:120], [pcs_spec:12885] |
| **INST-32** · AC port — Grid start with the DC side dead needs the grid at the AC terminals (characterised at 340, 400 and 460 V AC) and the battery absent, empty or behind its own open contactors; at most 3 attempts per start, ≥ 30 s apart, counted across controller resets, then lock-out until a service reset. | The 6-diode tap feeds the 75 W auxiliary; the AC precharge brings the DC link to the line-to-line peak minus two diode drops, within 10 V after 0.86 / 0.94 / 1.02 s at 340 / 400 / 460 V (three-wire; four-wire 0.91 / 0.99 / 1.07 s); the attempt count, spacing and lock-out live in the recorder flash (H5). | [PCS-PWR:24-27], [PCS-PWR-4W:28-31] |
| **INST-33** · AC port — Select the grid-code parameter set for the country at commissioning (EN 50549-1, VDE-AR-N 4105 or GB/T 34120 class: voltage / frequency windows and times, ROCOF, vector shift, LVRT / HVRT curves, P(f), Q(U), cos φ(P)). The module's defaults — LVRT 0 pu for 150 ms, 0.2 pu to 625 ms, linear to 0.9 pu at 2 s; HVRT 1.2 pu 10 s, 1.25 pu 1 s, 1.3 pu 0.5 s; cease to energise within 2 s — are FROM MEMORY and ASSUMED. | Grid protection is firmware that does not exist yet; the standard texts are not on file and anti-islanding is not simulated (IEC 62116 not claimed). | [pcs_spec:12853], [pcs_spec:12879], [pcs_spec:12882], [PCS-CTL:114], [PCS-CTL:117], [guide 12:337] |
| **INST-34** · Four-wire neutral — The N terminal is rated 250 A like the phase terminals, and the N pole is switched with the phases (4-pole K1 / K2, RFQ). | N terminal 250 A as the phases; the N leg carries the neutral current. | [PCS-PWR-4W:19] |
| **INST-35** · Four-wire neutral — Neutral current stays within the N leg's tiers: 198 A rms continuous, 216 A for 2 min, 259 A for 200 ms; per-phase set points whose currents add in the neutral (up to 3 × I) are limited by firmware. | One phase at I, two at 0: I<sub>N</sub> = I; half the module power on one phase (271 A) is above every tier and not claimed. | [pcs_spec:12867], [PCS-PWR-4W:37] |
| **INST-36** · Four-wire neutral — "100 % unbalanced load" means one phase at its own tier: 41.6 / 45.7 / 49.9 kW per phase at 230 V (rated / continuous / 2 min), with I<sub>N</sub> = I. | The module's per-phase tiers at 230 V; one loaded phase gives I<sub>N</sub> = I. | [PCS-PWR-4W:37] |
| **INST-37** · Four-wire neutral — Off-grid half-wave loads ≤ 280 A peak per phase (89 A DC, 140 A rms); the output DC component is held to ≤ 1.15 V (0.5 % U<sub>n</sub>) by the firmware regulator (measurement floor 0.56 V). | L<sub>N</sub> peaks at 311 A, the L1 part's continuous peak. | [PCS-PWR-4W:39], [pcs_spec:12877] |
| **INST-38** · Four-wire neutral — Equipment between L and N withstands the L–N surge level of 2,480 V at 150 A (8/20 µs). | Two varistor discs in series (2 × 1,240 V) against the 4 kV OVC III rated impulse at 230 V; a 3+1 circuit has no GDT data sheet on file. | [PCS-PWR-4W:19], [guide 12:364] |
| **INST-39** · Earthing — The DC system (PV array and battery or DC bus, one circuit through the shared negative rail) is floating with insulation monitoring, or has one pole earthed by the installation; a first earth fault may persist (the monitor only alarms). | Insulation coordination assumes either pole at PE potential. | [DECISIONS:38], [insulation report:39], [insulation report:40], [ARCH-COSTFIRST:281] |
| **INST-40** · Earthing — One active insulation monitor per galvanically connected DC system; an external monitor counts the module's own 6 MΩ pole dividers. | The module disables its own monitor by de-energising its test switches, but its dividers still load each pole. | [port report:367], [guide 04:194], [PV-PWR:14] |
| **INST-41** · Earthing — The removable PE_T link is closed in service (opened only for the hipot) and PE is connected. | The Y capacitors (4.7 nF to PE_T per port), MOV3 and the insulation-monitor strings return through it. | [PV-PWR:9], [ARCH-COSTFIRST:134-135] |
| **INST-42** · Earthing — PV modules: the installation's PE conductor at the module's PE stud is sized to the line conductors (35–50 mm²; ≥ 16 mm² Cu); the module's own PE bond is ≥ 10 mm² Cu; a high-touch-current warning label. | The capacitance to PE puts about 11 mA into the protective conductor (D-048); the two conductor sizes were reconciled in the architecture file on 2026-10-07. No PE size is stated for the inverter. | [DECISIONS:54], [guide 12:282], [guide 03:460], [ARCH-COSTFIRST:128-129] |
| **INST-43** · Earthing — Inverter with the grid present and a battery connected: the DC− insulation reading is invalid; the full two-state insulation test runs with the grid absent. | The grid tap ties DC− to the grid through its lower diodes (DC− peak-clamped to the most negative phase); R<sub>iso−</sub> is covered by the RCM once connected. | [PCS-PWR:28], [PCS-PWR-4W:32], [pcs_spec:11443] |
| **INST-44** · Environment — Inlet air: PV modules −30 … +60 °C (below −10 °C on the passive table, INST-49); the inverter −10 … +60 °C at power (below −10 °C standby or its passive table only, INST-77); full power to 45 °C, derating above (PV-P75: 82.5 kW to 45 °C, then 78.7 / 71.5 / 62.2 kW at 50 / 55 / 60 °C; inverter at 60 °C by module grade: grade A 198.0 / 216.0 / 242.9 A, grade B 179.8 / 192.6 / 192.6 A — continuous / 2 min / 200 ms; grade B's 179.8 A is 124.57 kW at PF 1 and 400 V). | PV-20; PV-P75 heatsink 77.4 °C at 45 °C inlet; the inverter derates above 45 °C by its grade's tier table ([D-080](DECISIONS.md); the 246 A printed before was grade A's 900 V point); its fans are rated −10 °C and its cold rating was set to −10 °C at power by [D-081](DECISIONS.md) (AC-02 amended by delegation). | [REQUIREMENTS:64], [REQUIREMENTS:169], [module report:74-76], [PV-PWR:4], [PCS-PWR:5], [PCS-PWR:31] |
| **INST-45** · Environment — Storage −40 … +70 °C for the inverter (ASSUMED: the parts' storage ratings are to be confirmed); the PV fans' −40 … +75 °C is the only storage figure on file for the PV modules. | Declared; not checked part by part. | [PCS-PWR:31], [PCS-PWR-4W:43], [module report:40] |
| **INST-46** · Environment — Site altitude ≤ 2,000 m (declared). The PV modules lose power from 1,000 m (45 °C inlet: PV-P75 93.8 / 86.3 / 79.2 / 70.4 % and PV-P100/110 80.6 / 77.3 / 68.4 / 60.5 % at 1,000 / 2,000 / 3,000 / 4,000 m); the inverter has no altitude model. | Barrier B1 gives 8.0 mm clearance; 9.2 / 10.4 / 11.9 mm are needed at 3,000 / 4,000 / 5,000 m. The options — 15 mm (WW) isolators, +1.53 to +3.02 USD per board, or a port SPD with Up,eff ≤ 4.0 kV for 4,000 m — are not fitted. | [PV-CTL:26], [PV-CTL:24], [PV-CTL:27], [PCS-CTL:25], [PCS-CTL:23] |
| **INST-47** · Environment — Pollution degree ≤ 3 outside the module (cooling-air path, terminals, fuse bases, arresters); the module's electronics compartment is PD2 only while closed and coated. | ECO-10 and D-032. IP rating and enclosure are out of scope (schematic and BOM only). | [REQUIREMENTS:113], [DECISIONS:38], [PCS-PWR:31] |
| **INST-48** · Environment — Do not reduce the airflow the module's own fans deliver by more than the margin left: the PV thermal model tolerates 3 % in total, of which the fan supply already uses 1.7 % (PV-P75) and none (PV-P100/110); the inverter's tolerance is not stated. Airflow: 402 m³/h (PV-P75), about 456 m³/h (PV-P100/110), 149 m³/h per heatsink section for the inverter (264 CFM three-wire, 352 CFM four-wire). No cabinet pressure allowance is stated. | R<sub>sa</sub> ≤ 0.060 K/W (PV-P75), ≤ 0.049 K/W (PV-P100/110), 0.050 K/W per section (inverter). | [PV-PWR:4], [PV-PWR-4:4], [PCS-PWR:22], [PCS-PWR:31], [PCS-PWR-4W:26], [PCS-PWR-4W:43] |
| **INST-49** · Environment — Cold start of the PV modules: with the inlet below −10 °C at start-up the fans stay off and the power is limited to the passive table (PV-P75 19.2 / 17.5 / 15.5 kW at −30 / −20 / −10 °C inlet; PV-P100/110 19.9 / 14.9 / 4.9 kW; none at 611 / 611 V; ESTIMATE ±50 %); the fans start at once at a heatsink NTC ≥ 80 °C or an inductor NTC ≥ 135 °C. The inverter's rule is INST-77. | The fans are rated −10 … +60 °C against PV-20's −30 °C. | [PV-CTL:89], [module report:40] |
| **INST-50** · Environment — The installation is assessed against the standards baseline EN 62477-1, EN 62109-1/-2, EN IEC 61000-6-2 and EN IEC 61000-6-4; the clause values used in the design are FROM MEMORY and unverified. | ECO-11; certification is out of scope; standard texts are not on file (risk B1). | [REQUIREMENTS:114], [guide 12:279] |
| **INST-51** · Control wiring — One CAN per module is the module bus (paralleling, carrier synchronisation, software addressing). The BMS uses RS-485 (Modbus RTU), or CAN only when the module is not paralleled. The EMS uses Ethernet (Modbus TCP, port 502) or RS-485; a PC tool uses RS-485 at 9600-8-N-1 or Ethernet. PV-CTL has no Ethernet fitted (footprints only); PCS-CTL has. | Declared port roles; one CAN, one RS-485 and one Ethernet bridge in hardware. | [PV-CTL:49], [PCS-CTL:49], [PV-CTL:45], [PCS-CTL:45] |
| **INST-52** · Control wiring — A CAN-only BMS that cannot share the module bus's bit rate or identifiers while modules run in parallel needs RS-485 to the BMS and Ethernet to the EMS; on the PV module, fit the Ethernet option first. | The one CAN is the module bus. | [PV-CTL:49], [PCS-CTL:49] |
| **INST-53** · Control wiring — Terminate each bus at its two physical ends only: CAN with the split-termination jumper (2 × 60.4 Ω + 4.7 nF), RS-485 with its 120 Ω jumper; open = not terminated. | Termination is a solder jumper on the module (open = not terminated); the two bus ends close theirs and the RS-485 receiver is fail-safe. | [PV-CTL:48], [PCS-CTL:48] |
| **INST-54** · Control wiring — Module addresses are set by software over the module CAN (no address switch); up to 16 modules on one CAN (ASSUMED bus load); there is no hardware synchronisation pair. | Declared; no DIP, no second isolated pair across the barrier. | [PV-CTL:48], [PCS-CTL:120], [PV-CTL:49] |
| **INST-55** · Control wiring — Keep the EMS / BMS link alive: on communication loss the module ramps to zero and stops (ASSUMED 1 s on CAN); a grid-forming inverter keeps forming. | Firmware practice adopted from the Wolfspeed firmware cross-check. | [PCS-CTL:88], [pcs_spec:12849] |
| **INST-56** · Control wiring — One Ethernet port per module: a daisy chain is the cabinet's switch; the HMI panel and its 12 V supply are a cabinet accessory; one TCP client per port is assumed. | No web server; the bridge is transparent. | [PV-CTL:49], [PCS-CTL:49], [PCS-CTL:84] |
| **INST-57** · Control wiring — Stop: wire the cabinet stop line as a closed loop that opens to stop — an open contact, a cut wire or a lost SELV supply stops the module; ON ≥ 8.8 V, OFF ≤ 3.4 V; chain modules through J_STOP (2 / 3 line in / out, 4 / 5 0 V in / out; 0.84 mA per module at 36 V); a dry contact fed from the module's own supply serves one module only. | Hardware stop through the default-low isolator: STOP_OK low within 1.47 ms, no firmware. | [PV-CTL:13], [PV-CTL:47], [PCS-CTL:10], [PCS-CTL:47] |
| **INST-58** · Control wiring — READY permissive: J_IO 6–7 is in series with the stop input; fit a wire link when no ready contact is used. | Open = stop in hardware; the firmware cannot tell it from STOP. | [PV-CTL:47], [PCS-CTL:47] |
| **INST-59** · Control wiring — The battery-fault / BMS contact on DI1 is a dry contact (the module supplies 5 V wetting); its NO / NC sense is a firmware parameter. | Closed contact 4.43 V ≥ the 2.2 V threshold. | [PV-CTL:47], [PCS-CTL:47] |
| **INST-60** · Control wiring — The status relay contacts carry SELV only: ≤ 30 V AC / 60 V DC, 2 A at 30 V DC; energised = healthy, NC on any trip, stop or supply loss. | Hongfa HFD27/005-S changeover contact. | [PV-CTL:47], [PCS-CTL:47] |
| **INST-61** · Control wiring — Every external circuit on the control board's connectors (CAN, RS-485, Ethernet, stop, READY, DI1, relay) is SELV/PELV (decisive voltage class A); the BMS port is assumed SELV/PELV per the battery system's own standard. The BMS / PCS side may earth its 0 V. | Barrier B1 is sized for it; the SELV 0 V is tied to PE through 1 MΩ ‖ 4.7 nF, so a solid bond is harmless. | [insulation report:44], [ARCH-COSTFIRST:113] |
| **INST-62** · Control wiring — Keep SELV connectors and the SELV harness at least 10 mm from live copper (the architecture's keep-out, 10.0 mm creepage at PD2). The clearance needed is 8.0 mm at the declared 2,000 m; 9.2 mm (3,000 m) and 10.4 mm (4,000 m) are not declared. | Barrier B1 creepage is 8 mm and needs conformal coating to PD1; the 10 mm keep-out holds to 3,000 m. | [PV-CTL:27], [PCS-CTL:26], [ARCH-COSTFIRST:133], [PV-CTL:24] |
| **INST-63** · Control wiring — The external stop is a single-channel functional stop with no SIL / PL claim: a cabinet that needs a rated safety function opens its own DC switching devices. | One latch, no dual channel (D-044). | [guide 12:283], [guide 04:439], [ARCH-COSTFIRST:494] |
| **INST-64** · Control wiring — PV modules have no cabinet 24 V input: they are self-powered from their DC ports and dark when both are below about 200 V (the auxiliary starts when a port rises above 206–215 V). | Given up in the cost-first design: cabinet 24 V and redundant feeds. | [ARCH-COSTFIRST:499], [guide 04:356] |
| **INST-65** · Service — PV-P75: the label "wait 10 min" after disconnection (worst case to 60 V: 8.4 min port A, 9.7 min port B, from 1144 V, R +1 %, C +10 %). | Passive bleeders, 8 × 73.2 kΩ per port. | [PV-PWR:14], [ARCH-COSTFIRST:131] |
| **INST-66** · Service — PV-P100/110: the label "wait 15 min" (worst case 11.2 min). | Passive bleeders, 8 × 73.2 kΩ per port. | [PV-PWR-4:14], [ARCH-COSTFIRST:131] |
| **INST-67** · Service — Inverter: the label "wait 15 min" (worst case 14.7 min three-wire, 14.8 min four-wire, with the DC contactor and both AC contactor sets open, from 1050 V). | Bleeders 4 × 93.1 kΩ (4 × 88.7 kΩ four-wire) per half. | [PCS-PWR:3], [PCS-PWR-4W:3] |
| **INST-68** · Service — Inverter enclosure label "isolate AC and DC, wait 15 min" (two sources). | The auxiliary input and, with a welded precharge relay, the DC link are live whenever the AC terminals are live. | [PCS-PWR:28], [PCS-PWR-4W:32] |
| **INST-69** · Service — PV modules: after any fault opening above 300 A replace the DC contactor; a detected weld blocks restart until it is replaced. | The contactor is rated 200 openings at 300 A / 1000 V and every opening above 300 A flags it; the inverter uses the same contactor, the flag is stated for the PV module only. | [guide 04:172], [guide 04:175], [guide 04:177] |
| **INST-70** · Service — The control board's debug header is live (up to 1000 V from earth): isolated probe only (≥ 1000 V DC rated); field service and firmware updates go over the SELV communication ports, so no live access is needed in the field; service needs isolated tools. | The controller sits on the negative DC rail. | [ARCH-COSTFIRST:119], [guide 12:283] |
| **INST-71** · PCS DC port — *Added 2026-10-07 ([D-079](DECISIONS.md)); rewritten after the re-checks R3 ([D-080](DECISIONS.md)) and R4 ([D-081](DECISIONS.md)).* On a DC bus without a battery that the inverter turns into an island (VF): the DC/DC converters are PV modules with their bus-coordination row active (V<sub>B</sub> more than 30 V above its set point → every phase's current reference to zero) — mandatory; declare the bus battery-less in both modules' parameter sets and keep the **CV set point at the 750 V default** (755 V when the inverter is the four-wire build in mode A); the PV module refuses a set point above **782 V** and the inverter refuses to start the island above **791 V** — the one-node 909 / 921 V and the cable model's 846 V are model validities, not ratings, and no installation may run above the window; the PV power on one link stays within the inverter's DC power tier (studied up to one PV module at 82.5 kW, or two at 75 kW each); the harness is INST-76. No hardwired trip line is required inside the window. DC/DC converters of other makes are not covered by any file. | The inverter has no brake chopper and cannot absorb the 16.1 J a full-load rejection returns to its 0.41 mF link. Coupled PCS / PV trajectory with the inter-module cable (simulated, averaged; the PV modules' own limit, comparator and coordination row read from their specs; ASSUMED harness): with the row every corner rides through at 750 and 782 V (PCS node ≤ 887 / ≤ 914 V against the 975 V soft limit); without it every full-load rejection trips the inverter (bus 1,063–1,069 V); with the PV firmware dead the bus ends at 1,126–1,130 V, above the auxiliary's 1,107 V input lock-out; above 782 V the PV row's × 2 time margin is not held. The PV board's 1.47 ms external stop never acts first. | [pcs_spec:12773], [PCS-CTL:107], [pv_control report:343], [pv_control report:345], [pcs_control report:584] |
| **INST-72** · PCS DC port — *Added 2026-10-07 ([D-079](DECISIONS.md)).* With the inverter holding the DC link in CV mode, the DC/DC power it serves stays within the row its grid-stiffness estimate selects: 89.5 kW at SCR 5, 119.5 kW at SCR 10, 125 kW from SCR 20 (no valid estimate = the weakest row); the DC/DC converters and the EMS enforce the limit the module publishes over CAN. | A DC/DC trip then stays below the 975 V soft limit (simulated, averaged); larger steps at a weak grid end in the DC over-voltage trip. Row selection ASSUMED. | [pcs_spec:12771], [PCS-CTL:106] |
| **INST-73** · Inverter — *Added 2026-10-07 ([D-078](DECISIONS.md)); rewritten after the re-check R3 ([D-080](DECISIONS.md)); grade B's rating added after R4 ([D-081](DECISIONS.md)).* Record each module's grade with its serial, as the controller reports it: grade A (every SiC device ≤ 40.0 mΩ at 25 °C) holds the full tiers to 45 °C inlet and 198.0 / 216.0 / 242.9 A at 60 °C; grade B (any device above) holds 196.8 / 207.5 / 207.5 A at 45 °C and 179.8 / 192.6 / 192.6 A at 60 °C (continuous / 2 min / 200 ms, steady start; its 200 ms tier equals its 2-minute tier) — at 60 °C inlet a grade-B module is a **124.57 kW** module at PF 1 and 400 V (125 kW needs 180.42 A; the rated 180 A is 124.71 kW): size the site's power to it ([D-081](DECISIONS.md)). A module without a grade record runs grade B; a replaced device re-grades the module. The load and motor-start sizing of INST-28 uses the tiers of the recorded grade at the site's inlet temperature; a 200 ms excursion is available only from the continuous state or cold, and within 12 min after more than 0.22 s above the continuous tier the 200 ms limit is the 2-minute tier. | The thermal model runs on typical R<sub>DS(on)</sub>: six data-sheet-maximum devices would take a switch to 9.68 V at 198 °C in the 200 ms tier, above the DESAT floor, and to 178 °C in the 110 % tier at 60 °C. Grade tables calculated over inlet 25–60 °C, 600–950 V, PF 1 and 0 and the start state; the hot R<sub>DS(on)</sub> follows the typical curve (ASSUMED). The limit equals the data-sheet typical; Sichain's distribution (RFQ) decides how many modules are grade B. | [pcs_spec:12817], [pcs_spec:12818], [pcs_spec:5347], [PCS-PWR:10], [PCS-PWR:31], [PCS-CTL:99], [PCS-CTL:100] |
| **INST-74** · AC port — *Added 2026-10-07 ([D-079](DECISIONS.md)); the grid-tie close added after the re-check R3 ([D-080](DECISIONS.md)).* During a grid start the grid at the AC terminals is inside ±10 % of nominal at each contactor command (the relay test and the closing of K2, then K1, run onto the module's own precharged link); the battery is connected only at ≥ 1.05 × √2 × V<sub>LL</sub> (measured) — below that the module stays in standby on the grid without a DC connection and reports it. The grid-tie close from the battery side likewise closes K2, then K1, with the grid inside ±10 % and synchronised (\|ΔV\| ≤ 5 %, \|Δθ\| ≤ 5°, \|Δf\| ≤ 0.1 Hz ASSUMED) at each coil command and until the path forms. | Repairs H1, H3 and H4 of the executed AC-start machine: a relay test on the dead link turned a welded contactor into an unprecharged close; a sagging grid let a contactor close onto a drained link; a battery below the grid peak was rectified into without control. R3-01: the close from the battery side had pulled in both coils at once (54.9 / 58.2 W against the 48 W live peak). | [pcs_spec:11428-11429], [pcs_spec:11431], [pcs_spec:11923], [pcs_spec:12026] |
| **INST-75** · AC port, four-wire off-grid — *Added after the re-check R3 ([D-080](DECISIONS.md)).* Plan the island's short-circuit protection on the module's declared behaviour: a bolted output short is ridden for 200 ms at the 200 ms tier (held at about 379 A peak on the shorted phase), then the module trips to FAULT; there is no automatic clear after an over-current trip, a restart by command into a short that persists trips again, and the third trip of a class within 10 min locks the module out (ASSUMED). A downstream device meant to clear a branch short selectively has to do so within that 200 ms at that current (our reading of the declaration; no file names a downstream device). | Simulated (averaged, both hardware trip layers replayed): the 5 ms onset clamp keeps every studied corner off both layers, 13.3 A below the CMPSS backup's 423.9 A edge; a unit outside the modelled bands trips in hardware instead — latched, FAULT. A switched model and the bench are the gate. | [pcs_spec:12314], [pcs_spec:12315], [pcs_spec:12316-12318], [pcs_design report:710] |
| **INST-76** · PCS DC port, harness — *Added after the re-check R4 ([D-081](DECISIONS.md)).* On a battery-less bus (INST-71) wire each PV module to the inverter by its own run: **≤ 20 m** from its port-B terminals to the inverter's DC terminals (a shared busbar section counts in each run); loop inductance **≤ 1 µH per metre and ≤ 20 µH per run**, fuses, disconnectors and busbar sections included — DC+ and DC− routed together (a two-core cable, or single cores bundled and touching in the same tray), never separated or looped through other sections; **no series choke or ferrite** on the link; copper sized by the installation rules for the 135 A port current with a loop resistance **≤ 12.6 mΩ per run** (heavier conductors allowed); **nothing else on the link** — no capacitance, filter, DC source or load beyond the inverter's DC link and the PV modules' port-B banks; **at most two PV-P75 per inverter link**, their power within its DC power tier; the commissioning record carries the as-built runs (length, cross-section, routing), the battery-less declaration, the set point and both modules' serials. | The window of INST-71 rests on the cable model's ASSUMED harness (2 / 10 / 20 m, 0.6 / 1.0 µH/m): the cable adds up to +28 V to the inverter node's first peak, set by the inductance, hardly by the resistance (+1.8 V lossless at the worst run); 12.6 mΩ is 20 m of 70 mm² at 90 °C, the 1.7 V at 135 A that the set-point floors carry. A battery or another source on the link is a different study. | [pcs_spec:12773], [PCS-CTL:107], [pcs_control report:592] |
| **INST-77** · Inverter, environment — *Added after the re-check R4 ([D-081](DECISIONS.md)).* Plan the inverter for full operation from −10 °C inlet. Below −10 °C its fans stay off and it holds standby or the passive table only — 20 / 15 / 8 kVA (three-wire) and 14 / 7 / 0 kVA (four-wire) at 600 V DC and −30 / −20 / −10 °C inlet, nothing above 700 V DC (ESTIMATE ±50 %); the fans start at −10 °C or above, once any heatsink NTC exceeds 80 °C, or on a failed inlet NTC. Power below −10 °C inlet is not available from the module as drawn (a cold-rated fan is an open RFQ). | REQUIREMENTS AC-02 amended by delegation: the drawn fans are rated −10 … +60 °C; the passive table is too small to be useful over the DC window, and the only −30 °C fan on file was rejected on cost (+225–399 USD per module, 2.2 × the fan power). The table is an unvalidated estimate (fans off, closed enclosure ASSUMED). | [REQUIREMENTS:169], [PCS-PWR:23], [PCS-PWR-4W:27], [PCS-CTL:101], [PCS-PWR:31] |

---

<!-- footer -->
← [Risks and open items](../guide/12-risks-and-open-items.md) · [Documentation index](../README.md) · [Protection and safety](../guide/04-protection-and-safety.md) →

<!-- Link targets, one per cited line or range. Re-find a quoted fragment with grep -nF. -->

[PV-PWR:1]: ../../hardware/PV-PWR/outputs/PV-PWR_design_check.txt
[PV-CTL:1]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt
[PV-PWR-4:1]: ../../hardware/PV-PWR-4/outputs/PV-PWR-4_design_check.txt
[PCS-PWR:1]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt
[PCS-CTL:1]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt
[PCS-PWR-4W:1]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt
[PV-PWR:15]: ../../hardware/PV-PWR/outputs/PV-PWR_design_check.txt#L15
[PV-PWR-4:15]: ../../hardware/PV-PWR-4/outputs/PV-PWR-4_design_check.txt#L15
[guide 04:169]: ../guide/04-protection-and-safety.md?plain=1#L169
[ARCH-COSTFIRST:281]: ARCHITECTURE-COSTFIRST.md?plain=1#L281
[guide 04:179-184]: ../guide/04-protection-and-safety.md?plain=1#L179-L184
[port report:456]: ../../sim/out/port_design/report.md?plain=1#L456
[port report:434]: ../../sim/out/port_design/report.md?plain=1#L434
[ARCH-COSTFIRST:269]: ARCHITECTURE-COSTFIRST.md?plain=1#L269
[port_spec:696-702]: ../../sim/out/port_design/port_spec.json#L696-L702
[PCS-PWR:15]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L15
[PCS-PWR-4W:16]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L16
[port report:463-468]: ../../sim/out/port_design/report.md?plain=1#L463-L468
[port_spec:904]: ../../sim/out/port_design/port_spec.json#L904
[pcs_design report:435]: ../../sim/out/pcs_design/report.md?plain=1#L435
[pcs_design report:451]: ../../sim/out/pcs_design/report.md?plain=1#L451
[pcs_design report:455]: ../../sim/out/pcs_design/report.md?plain=1#L455
[PCS-PWR-4W:34]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L34
[pcs_spec:7355-7356]: ../../sim/out/pcs_design/pcs_spec.json#L7355-L7356
[PCS-PWR-4W:38]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L38
[pcs_spec:12808]: ../../sim/out/pcs_design/pcs_spec.json#L12808
[pcs_spec:12859]: ../../sim/out/pcs_design/pcs_spec.json#L12859
[PCS-CTL:94]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L94
[pcs_spec:12858]: ../../sim/out/pcs_design/pcs_spec.json#L12858
[pcs_spec:12860]: ../../sim/out/pcs_design/pcs_spec.json#L12860
[pcs_spec:12773]: ../../sim/out/pcs_design/pcs_spec.json#L12773
[PCS-CTL:107]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L107
[pv_control report:343]: ../../sim/out/pv_control/report.md?plain=1#L343
[pv_control report:345]: ../../sim/out/pv_control/report.md?plain=1#L345
[pcs_control report:592]: ../../sim/out/pcs_control/report.md?plain=1#L592
[pcs_spec:12771]: ../../sim/out/pcs_design/pcs_spec.json#L12771
[PCS-CTL:106]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L106
[PCS-PWR:29]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L29
[PCS-PWR-4W:41]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L41
[pcs_spec:12311]: ../../sim/out/pcs_design/pcs_spec.json#L12311
[PCS-PWR:16]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L16
[PCS-PWR-4W:17]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L17
[pcs_spec:12854]: ../../sim/out/pcs_design/pcs_spec.json#L12854
[PCS-PWR:24]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L24
[PCS-PWR-4W:28]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L28
[pcs_spec:12804]: ../../sim/out/pcs_design/pcs_spec.json#L12804
[pcs_spec:12791]: ../../sim/out/pcs_design/pcs_spec.json#L12791
[PCS-PWR-4W:19]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L19
[PCS-PWR-4W:37]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L37
[pcs_spec:12867]: ../../sim/out/pcs_design/pcs_spec.json#L12867
[PCS-PWR-4W:39]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L39
[pcs_spec:12880]: ../../sim/out/pcs_design/pcs_spec.json#L12880
[PCS-CTL:115]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L115
[pcs_spec:12881]: ../../sim/out/pcs_design/pcs_spec.json#L12881
[PCS-CTL:116]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L116
[pcs_spec:12769]: ../../sim/out/pcs_design/pcs_spec.json#L12769
[pcs_control report:291]: ../../sim/out/pcs_control/report.md?plain=1#L291
[pcs_control report:347]: ../../sim/out/pcs_control/report.md?plain=1#L347
[PCS-PWR:31]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L31
[PCS-PWR-4W:43]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L43
[pcs_spec:12709-12724]: ../../sim/out/pcs_design/pcs_spec.json#L12709-L12724
[pcs_spec:12817]: ../../sim/out/pcs_design/pcs_spec.json#L12817
[PCS-CTL:99]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L99
[PCS-CTL:113]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L113
[pcs_spec:12878]: ../../sim/out/pcs_design/pcs_spec.json#L12878
[PCS-CTL:119]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L119
[pcs_spec:12884]: ../../sim/out/pcs_design/pcs_spec.json#L12884
[PCS-CTL:120]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L120
[pcs_spec:12885]: ../../sim/out/pcs_design/pcs_spec.json#L12885
[PCS-PWR:26]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L26
[PCS-PWR-4W:30]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L30
[PCS-PWR:27]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L27
[PCS-PWR-4W:31]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L31
[pcs_spec:11428-11429]: ../../sim/out/pcs_design/pcs_spec.json#L11428-L11429
[pcs_spec:11431]: ../../sim/out/pcs_design/pcs_spec.json#L11431
[pcs_spec:12853]: ../../sim/out/pcs_design/pcs_spec.json#L12853
[pcs_spec:12879]: ../../sim/out/pcs_design/pcs_spec.json#L12879
[PCS-CTL:114]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L114
[pcs_spec:12882]: ../../sim/out/pcs_design/pcs_spec.json#L12882
[PCS-CTL:117]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L117
[DECISIONS:38]: DECISIONS.md?plain=1#L38
[insulation report:39]: ../../sim/out/insulation/report.md?plain=1#L39
[insulation report:40]: ../../sim/out/insulation/report.md?plain=1#L40
[port report:367]: ../../sim/out/port_design/report.md?plain=1#L367
[guide 04:194]: ../guide/04-protection-and-safety.md?plain=1#L194
[PV-CTL:50]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L50
[PV-PWR:9]: ../../hardware/PV-PWR/outputs/PV-PWR_design_check.txt#L9
[ARCH-COSTFIRST:134]: ARCHITECTURE-COSTFIRST.md?plain=1#L134
[DECISIONS:54]: DECISIONS.md?plain=1#L54
[ARCH-COSTFIRST:128-129]: ARCHITECTURE-COSTFIRST.md?plain=1#L128-L129
[PCS-PWR:28]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L28
[PCS-PWR-4W:32]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L32
[REQUIREMENTS:64]: REQUIREMENTS.md?plain=1#L64
[REQUIREMENTS:169]: REQUIREMENTS.md?plain=1#L169
[pcs_spec:12874]: ../../sim/out/pcs_design/pcs_spec.json#L12874
[PCS-CTL:109]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L109
[PCS-PWR:23]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L23
[PCS-PWR-4W:27]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L27
[PCS-CTL:101]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L101
[module report:76]: ../../sim/out/pv_design/module_report.md?plain=1#L76
[PCS-PWR:5]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L5
[PCS-PWR-4W:5]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L5
[module report:74-76]: ../../sim/out/pv_design/module_report.md?plain=1#L74-L76
[module report:90]: ../../sim/out/pv_design/module_report.md?plain=1#L90
[module report:94]: ../../sim/out/pv_design/module_report.md?plain=1#L94
[module report:40]: ../../sim/out/pv_design/module_report.md?plain=1#L40
[PV-CTL:26]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L26
[PCS-CTL:25]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L25
[PV-CTL:24]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L24
[PCS-CTL:23]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L23
[PV-CTL:27]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L27
[PCS-CTL:26]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L26
[REQUIREMENTS:113]: REQUIREMENTS.md?plain=1#L113
[REQUIREMENTS:114]: REQUIREMENTS.md?plain=1#L114
[REQUIREMENTS:79]: REQUIREMENTS.md?plain=1#L79
[PV-PWR:4]: ../../hardware/PV-PWR/outputs/PV-PWR_design_check.txt#L4
[PV-PWR-4:4]: ../../hardware/PV-PWR-4/outputs/PV-PWR-4_design_check.txt#L4
[PCS-PWR:22]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L22
[PCS-PWR-4W:26]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L26
[PV-CTL:89]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L89
[guide 12:366]: ../guide/12-risks-and-open-items.md?plain=1#L366
[PV-CTL:49]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L49
[PCS-CTL:49]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L49
[PV-CTL:48]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L48
[PCS-CTL:48]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L48
[PCS-CTL:88]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L88
[pcs_spec:12849]: ../../sim/out/pcs_design/pcs_spec.json#L12849
[PV-CTL:13]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L13
[PCS-CTL:10]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L10
[PV-CTL:47]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L47
[PCS-CTL:47]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L47
[insulation report:44]: ../../sim/out/insulation/report.md?plain=1#L44
[ARCH-COSTFIRST:113]: ARCHITECTURE-COSTFIRST.md?plain=1#L113
[ARCH-COSTFIRST:133]: ARCHITECTURE-COSTFIRST.md?plain=1#L133
[guide 12:283]: ../guide/12-risks-and-open-items.md?plain=1#L283
[ARCH-COSTFIRST:499]: ARCHITECTURE-COSTFIRST.md?plain=1#L499
[guide 04:356]: ../guide/04-protection-and-safety.md?plain=1#L356
[PV-PWR:14]: ../../hardware/PV-PWR/outputs/PV-PWR_design_check.txt#L14
[PV-PWR-4:14]: ../../hardware/PV-PWR-4/outputs/PV-PWR-4_design_check.txt#L14
[ARCH-COSTFIRST:131]: ARCHITECTURE-COSTFIRST.md?plain=1#L131
[PCS-PWR:3]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L3
[PCS-PWR-4W:3]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L3
[guide 04:175]: ../guide/04-protection-and-safety.md?plain=1#L175
[guide 04:177]: ../guide/04-protection-and-safety.md?plain=1#L177
[ARCH-COSTFIRST:120]: ARCHITECTURE-COSTFIRST.md?plain=1#L120
[guide 04:183]: ../guide/04-protection-and-safety.md?plain=1#L183
[ARCH-COSTFIRST:265]: ARCHITECTURE-COSTFIRST.md?plain=1#L265
[ARCH-COSTFIRST:269-274]: ARCHITECTURE-COSTFIRST.md?plain=1#L269-L274
[ARCH-COSTFIRST:584]: ARCHITECTURE-COSTFIRST.md?plain=1#L584
[guide 12:284]: ../guide/12-risks-and-open-items.md?plain=1#L284
[port report:457]: ../../sim/out/port_design/report.md?plain=1#L457
[guide 03:460]: ../guide/03-power-stage.md?plain=1#L460
[guide 12:282]: ../guide/12-risks-and-open-items.md?plain=1#L282
[ARCH-COSTFIRST:380-383]: ARCHITECTURE-COSTFIRST.md?plain=1#L380-L383
[port report:398]: ../../sim/out/port_design/report.md?plain=1#L398
[ARCH-PCS:110-112]: ARCHITECTURE-PCS.md?plain=1#L110-L112
[ARCH-PCS:118-120]: ARCHITECTURE-PCS.md?plain=1#L118-L120
[port report:443]: ../../sim/out/port_design/report.md?plain=1#L443
[port report:444]: ../../sim/out/port_design/report.md?plain=1#L444
[DECISIONS:62]: DECISIONS.md?plain=1#L62
[port report:24]: ../../sim/out/port_design/report.md?plain=1#L24
[port report:60]: ../../sim/out/port_design/report.md?plain=1#L60
[DECISIONS:29]: DECISIONS.md?plain=1#L29
[insulation report:47]: ../../sim/out/insulation/report.md?plain=1#L47
[guide 12:362]: ../guide/12-risks-and-open-items.md?plain=1#L362
[guide 12:458]: ../guide/12-risks-and-open-items.md?plain=1#L458
[guide 09:835]: ../guide/09-pcs-p125.md?plain=1#L835
[pcs_design report:433]: ../../sim/out/pcs_design/report.md?plain=1#L433
[guide 09:493]: ../guide/09-pcs-p125.md?plain=1#L493
[guide 12:289]: ../guide/12-risks-and-open-items.md?plain=1#L289
[pcs_control report:528]: ../../sim/out/pcs_control/report.md?plain=1#L528
[pcs_design report:453]: ../../sim/out/pcs_design/report.md?plain=1#L453
[pcs_design report:439-446]: ../../sim/out/pcs_design/report.md?plain=1#L439-L446
[pcs_control report:30]: ../../sim/out/pcs_control/report.md?plain=1#L30
[DECISIONS:85]: DECISIONS.md?plain=1#L85
[PCS-PWR:17]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L17
[guide 12:281]: ../guide/12-risks-and-open-items.md?plain=1#L281
[port report:371]: ../../sim/out/port_design/report.md?plain=1#L371
[guide 12:279]: ../guide/12-risks-and-open-items.md?plain=1#L279
[port report:34]: ../../sim/out/port_design/report.md?plain=1#L34
[guide 12:287]: ../guide/12-risks-and-open-items.md?plain=1#L287
[port report:431]: ../../sim/out/port_design/report.md?plain=1#L431
[guide 04:193]: ../guide/04-protection-and-safety.md?plain=1#L193
[ARCH-COSTFIRST:275]: ARCHITECTURE-COSTFIRST.md?plain=1#L275
[port report:466]: ../../sim/out/port_design/report.md?plain=1#L466
[port report:471]: ../../sim/out/port_design/report.md?plain=1#L471
[port_spec:895-899]: ../../sim/out/port_design/port_spec.json#L895-L899
[port report:468]: ../../sim/out/port_design/report.md?plain=1#L468
[pcs_design report:806]: ../../sim/out/pcs_design/report.md?plain=1#L806
[pcs_spec:12279-12308]: ../../sim/out/pcs_design/pcs_spec.json#L12279-L12308
[pcs_spec:12310]: ../../sim/out/pcs_design/pcs_spec.json#L12310
[pcs_spec:12803]: ../../sim/out/pcs_design/pcs_spec.json#L12803
[PCS-PWR:11]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L11
[PCS-PWR:24-27]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L24-L27
[PCS-PWR-4W:28-31]: ../../hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt#L28-L31
[guide 12:337]: ../guide/12-risks-and-open-items.md?plain=1#L337
[pcs_spec:12877]: ../../sim/out/pcs_design/pcs_spec.json#L12877
[guide 12:364]: ../guide/12-risks-and-open-items.md?plain=1#L364
[ARCH-COSTFIRST:134-135]: ARCHITECTURE-COSTFIRST.md?plain=1#L134-L135
[pcs_spec:11443]: ../../sim/out/pcs_design/pcs_spec.json#L11443
[PV-CTL:45]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt#L45
[PCS-CTL:45]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L45
[PCS-CTL:84]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L84
[guide 04:439]: ../guide/04-protection-and-safety.md?plain=1#L439
[ARCH-COSTFIRST:494]: ARCHITECTURE-COSTFIRST.md?plain=1#L494
[guide 04:172]: ../guide/04-protection-and-safety.md?plain=1#L172
[ARCH-COSTFIRST:119]: ARCHITECTURE-COSTFIRST.md?plain=1#L119
[DECISIONS:86]: DECISIONS.md?plain=1#L86
[PCS-CTL:100]: ../../hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt#L100
[PCS-PWR:10]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt#L10
[pcs_control report:584]: ../../sim/out/pcs_control/report.md?plain=1#L584
[pcs_design report:710]: ../../sim/out/pcs_design/report.md?plain=1#L710
[pcs_spec:11923]: ../../sim/out/pcs_design/pcs_spec.json#L11923
[pcs_spec:12026]: ../../sim/out/pcs_design/pcs_spec.json#L12026
[pcs_spec:12314]: ../../sim/out/pcs_design/pcs_spec.json#L12314
[pcs_spec:12315]: ../../sim/out/pcs_design/pcs_spec.json#L12315
[pcs_spec:12316-12318]: ../../sim/out/pcs_design/pcs_spec.json#L12316-L12318
[pcs_spec:12818]: ../../sim/out/pcs_design/pcs_spec.json#L12818
[pcs_spec:12865]: ../../sim/out/pcs_design/pcs_spec.json#L12865
[pcs_spec:5347]: ../../sim/out/pcs_design/pcs_spec.json#L5347
