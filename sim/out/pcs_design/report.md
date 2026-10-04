# PCS-P125 power stage - design report (sim/pcs_design.py)

**Everything here is CALCULATED or SIMULATED (averaged loss model on data-sheet curves, analytic filter / thermal models, ngspice transients). Nothing is measured or bench-validated.** Device data and page references: sim/pcs_devices.py and sim/pv_devices.py. Requirements: REQUIREMENTS.md AC-01..03, SRC-1..6. Re-run: `.venv/bin/python sim/pcs_design.py`.

## Summary

- **Topology: two-level, 1700 V SiC (6 x SG2M040170HJ per switch, 36 devices, 6 gate channels), 32 kHz** - replaces the architecture's three-level T-type with 1200 V IGBTs.  Every device at <= 0.56 of its rating at 950 V (rule 0.67); the T-type's outer IGBTs sit at 0.79 and would fail by cosmic rays 104 FIT per unit at 950 V (25 C, sea level; Semikron AN 17-003 data) against 0.16 FIT here.  The two-level design is also the cheapest: three-level legs need a 3.5 mF per-half midpoint bank for PF 0 that two-level does not.
- **Efficiency (calculated):** peak 99.20 % (>= 98.5 % met), 98.65 % at 125 kW / 750 V; worst Tj 116 C at 110 % / 45 C inlet, 138 C at 60 C, 151 C after 1.2 x 216 A for 200 ms (175 C rating).
- **Filter:** L1 97 uH, C_f 50 uF (star on the DC midpoint), L2 15 uH; resonance 2.4-6.2 kHz; PWM THDi < 0.1 % (the 3 % is the controller's).
- **DC link:** 5 + 5 x Faratronic C3D1U147 film, > 100 kh at 60 C inlet; electrolytics would need 208 cans and last 8 kh.
- **Cost:** 1078 / 861 USD (catalogue / 5,000 units) three-wire, 1281 / 1024 USD four-wire, against the architecture's 1,037 / 801 and 1,194 / 919 USD - which become about 1015 USD at 5k for its own T-type once its IGBT count and midpoint bank are corrected.

## (a) Topology and devices

**Basis.** One 150 mm plate-fin section per leg (24 fins x 60 mm, 400 mm long), 1 x AFB1224SHE-F00 per section: 149 m3/h at 51 Pa, R_sa 50.3 mK/W per section (calculated, pv_tradeoff heatsink model).  Device count per position = the smallest that keeps Tj within its limit at 198 A (110 %) / 45 C inlet (continuous), after 216 A for 2 min (heatsink RC) and after 259 A for 200 ms (device transient), over V_dc 600/750/900 V and PF angle 0/45/90/135/180/-90 deg (limits: SiC 150 / 165 / 175 C; CR Micro / NCE 150 C parts 125 / 145-150 / 150-175 C as rated).  Losses: averaged model over the fundamental period (sim/pcs_devices.py docstring), sinusoidal PWM where it reaches, SVPWM zero sequence below.  Costs here: devices + pads + gate channels + an L1 estimate only (step g has everything).  Efficiency: devices + an ESTIMATE for the rest (70 W + 440 W x (I/180 A)^2).

| candidate | f_sw kHz | devices per position | dev | ch | device loss 125 kW @750 / 900 V (W) | module loss 125 kW (est.) | eta full / peak (est.) | USD cat / 5k (4W: +5k) | V/V_rated @950 V | FIT 900 / 950 V |
|---|---|---|---|---|---|---|---|---|---|---|
| TT-IGBT (architect) | 16 | T1 6xCRG40T120BK3SD, T4 6xCRG40T120BK3SD, T2 6xCRG50T60AK3SD, T3 6xCRG50T60AK3SD | 72 | 12 | 1734 / 1973 | 2198 | 98.24 / 98.57 % | 747 / 565 (+99) | 0.80 | 59.53 / 158.70 |
| TT-IGBT (best documented) | 16 | T1 4xNCE40TD120VT, T4 4xNCE40TD120VT, T2 6xNCE80TD65BT, T3 6xNCE80TD65BT | 60 | 12 | 1584 / 1779 | 2048 | 98.36 / 98.57 % | 738 / 548 (+93) | 0.80 | 46.89 / 113.00 |
| TT-SiC 1200/1200 (rule check) | 32 | T1 5xSG2M035120LJ, T4 5xSG2M035120LJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ | 66 | 12 | 992 / 1077 | 1364 | 98.91 / 99.30 % | 709 / 535 (+95) | 0.79 | 25.01 / 55.22 |
| TT-SiC 1700/1200 | 32 | T1 4xSG2M020170HJ, T4 4xSG2M020170HJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ | 60 | 12 | 969 / 1096 | 1341 | 98.92 / 99.23 % | 891 / 699 (+149) | 0.56 | 0.10 / 0.18 |
| TT-SiC 1700(40m)/1200 | 32 | T1 5xSG2M040170HJ, T4 5xSG2M040170HJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ | 66 | 12 | 1267 / 1318 | 1640 | 98.69 / 99.21 % | 726 / 571 (+107) | 0.56 | 0.08 / 0.13 |
| NPC-SiC 1200 + SBD | 32 | T1 5xSG2M035120LJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ, T4 5xSG2M035120LJ, D5 3xYJD112040NQG2, D6 3xYJD112040NQG2 | 84 | 12 | 1576 / 1626 | 1949 | 98.44 / 99.14 % | 821 / 606 (+118) | 0.44 | 0.10 / 0.10 |
| ANPC-SiC 1200 | 32 | T1 5xSG2M035120LJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ, T4 5xSG2M035120LJ, T5 5xSG2M035120LJ, T6 5xSG2M035120LJ | 96 | 18 | 1534 / 1549 | 1906 | 98.47 / 99.18 % | 861 / 636 (+128) | 0.44 | 0.12 / 0.12 |
| NPC-IGBT 1200 + SBD | 16 | T1 4xNCE40TD120VT, T2 5xNCE40TD120VT, T3 5xNCE40TD120VT, T4 4xNCE40TD120VT, D5 3xYJD112040NQG2, D6 3xYJD112040NQG2 | 72 | 12 | 2344 / 2504 | 2808 | 97.75 / 98.25 % | 856 / 621 (+118) | 0.44 | 9.28 / 9.28 |
| NPC-module 1200 V (HIITIO HCG400FL120E3RA) - NOT FEASIBLE: T3 misses its Tj limit by 32 K (one module per phase) | 16 | T1 1xHCG400FL120E3RA, T2 1xHCG400FL120E3RA, T3 1xHCG400FL120E3RA, T4 1xHCG400FL120E3RA, D5 1xHCG400FL120E3RA, D6 1xHCG400FL120E3RA | 3 | 12 | 1867 / 2025 | 2330 | 98.13 / 98.43 % | 1060 / 752 (+162) | 0.44 | 20.59 / 20.59 |
| NPC-module 1200 V @8 kHz - NOT FEASIBLE: T3 misses its Tj limit by 27 K (one module per phase) | 8 | T1 1xHCG400FL120E3RA, T2 1xHCG400FL120E3RA, T3 1xHCG400FL120E3RA, T4 1xHCG400FL120E3RA, D5 1xHCG400FL120E3RA, D6 1xHCG400FL120E3RA | 3 | 12 | 1625 / 1713 | 2282 | 98.17 / 98.52 % | 1230 / 889 (+189) | 0.44 | 20.59 / 20.59 |
| NPC-module 650 V (HIITIO HCG375FL065E3RC) - NOT FEASIBLE: T1 misses its Tj limit by 16 K (one module per phase) | 16 | T1 1xHCG375FL065E3RC, T2 1xHCG375FL065E3RC, T3 1xHCG375FL065E3RC, T4 1xHCG375FL065E3RC, D5 1xHCG375FL065E3RC, D6 1xHCG375FL065E3RC | 3 | 12 | 1713 / 1889 | 2176 | 98.25 / 98.52 % | 970 / 692 (+142) | 0.80 | 30.00 / 30.00 |
| 2L-SiC 1700 | 24 | TH 4xSG2M020170HJ, TL 4xSG2M020170HJ | 24 | 6 | 932 / 1016 | 1447 | 98.84 / 99.06 % | 607 / 485 (+133) | 0.56 | 0.04 / 0.21 |
| 2L-SiC 1200 (rule check) | 32 | TH 6xSG2M035120LJ, TL 6xSG2M035120LJ | 36 | 6 | 857 / 936 | 1327 | 98.94 / 99.14 % | 412 / 308 (+75) | 0.79 | 64.80 / 151.20 |
| 2L-SiC 1700(40m) | 24 | TH 6xSG2M040170HJ, TL 6xSG2M040170HJ | 36 | 6 | 1126 / 1193 | 1641 | 98.68 / 99.04 % | 469 / 381 (+98) | 0.56 | 0.03 / 0.16 |

Switching-frequency sweep of the three cheapest candidates that pass the 0.67 rule and the efficiency floor (devices re-sized, L1 for 25 % ripple, L2 for the 0.3 % carrier-band limit with C_f 50 uF):

| candidate | f_sw kHz | devices | L1 / L2 uH | module loss 125 kW (est.) | peak eta | USD cat / 5k |
|---|---|---|---|---|---|---|
| 2L-SiC 1700(40m) | 16 | TH 6, TL 6 | 194 / 14 | 1641 | 99.06 % | 547 / 443 |
| 2L-SiC 1700(40m) | 24 | TH 6, TL 6 | 130 / 6 | 1641 | 99.04 % | 469 / 381 |
| 2L-SiC 1700(40m) | 32 | TH 6, TL 6 | 97 / 4 | 1687 | 98.99 % | 432 / 351 |
| 2L-SiC 1700(40m) | 40 | TH 6, TL 6 | 78 / 3 | 1750 | 98.93 % | 410 / 334 |
| 2L-SiC 1700(40m) | 48 | TH 6, TL 6 | 65 / 2 | 1822 | 98.87 % | 395 / 322 |
| 2L-SiC 1700 | 16 | TH 4, TL 4 | 194 / 14 | 1429 | 99.10 % | 684 / 548 |
| 2L-SiC 1700 | 24 | TH 4, TL 4 | 130 / 6 | 1447 | 99.06 % | 607 / 485 |
| 2L-SiC 1700 | 32 | TH 4, TL 4 | 97 / 4 | 1513 | 99.00 % | 570 / 456 |
| 2L-SiC 1700 | 40 | TH 4, TL 4 | 78 / 3 | 1596 | 98.93 % | 548 / 439 |
| 2L-SiC 1700 | 48 | TH 4, TL 4 | 65 / 2 | 1689 | 98.85 % | 533 / 427 |
| TT-SiC 1700(40m)/1200 | 16 | T1 5, T4 5, T2 6, T3 6 | 97 / 13 | 1653 | 99.22 % | 797 / 629 |
| TT-SiC 1700(40m)/1200 | 24 | T1 5, T4 5, T2 6, T3 6 | 65 / 6 | 1630 | 99.22 % | 745 / 587 |
| TT-SiC 1700(40m)/1200 | 32 | T1 5, T4 5, T2 6, T3 6 | 49 / 4 | 1640 | 99.21 % | 726 / 571 |
| TT-SiC 1700(40m)/1200 | 40 | T1 5, T4 5, T2 6, T3 6 | 39 / 3 | 1660 | 99.19 % | 714 / 562 |
| TT-SiC 1700(40m)/1200 | 48 | T1 5, T4 5, T2 6, T3 6 | 32 / 2 | 1685 | 99.17 % | 706 / 555 |

**Decision: two-level, 32 kHz, TH 6 x SG2M040170HJ, TL 6 x SG2M040170HJ** - 36 devices and 6 gate channels for three-wire: the PV module's 1700 V SiC device, gate-drive channel and switching frequency.  Screen-scope cost (devices, pads, gate drive, LCL, DC link) 432 USD catalogue / 351 USD at 5,000 units; module loss 1687 W at 125 kW; peak 98.99 % (estimates; step b gives the map).  **This replaces the architecture's three-level T-type.**

Why:
- **The 0.67 rule first (SRC-4).** 950/1700 = 0.56 for every device.  The architect's T-type: 950/1200 = 0.79 (0.75 at 900 V).
- **The DC link decides between two and three levels.** A three-level leg draws a 150 Hz midpoint current; at PF 0 (150 kVA reactive is in AC-02) and 600-750 V it cannot be cancelled by any zero sequence (np_residual: ideal carrier-based midpoint control still leaves up to 3.5 mF of need per half).  Keeping the ripple within the film's 60 V pp limit takes 25 C3D1U147 per half for every three-level option, against 5 per half for two-level (ripple-current-limited): 167 USD at 5k.  The architecture's 660 uF per half would ripple by hundreds of volts there.
- **Totals (5k, screen scope):** two-level 1700 V SiC 351 USD; best three-level (TT-SiC 1700(40m)/1200, 32 kHz) 571 USD; NPC / ANPC SiC 606 / 636 USD (ANPC does not earn its place: its clamps add six channels and save no loss); the architect's IGBT T-type, sized correctly and with the DC link it needs, 565 USD; the best documented IGBT T-type 548 USD.
- **Price of the safer choice:** none in money - the rule-compliant design is the cheapest one in the screen.  What the rule costs inside two-level: 43 USD against the same two-level with 1200 V SiC (2L-SiC 1200 (rule check), 0.79 of rating at 950 V, FIT 151 at 950 V vs 0.16).  The efficiency price against the best SiC three-level: module loss 1687 W vs 1640 W at 125 kW, peak 98.99 % vs 99.21 % (0.22 %-points); against the architect's IGBT T-type it is a gain (2198 W, peak 98.57 %).
- **Four-wire:** the fourth leg of a two-level design is one more half-bridge; a T-type phase leg under 100 % unbalance pushes a 50 Hz current into its midpoint (step d) that the two-level bank does not have.
- **What two-level costs elsewhere:** common-mode voltage at f_sw 432 V peak against 235 V for three-level (950 V DC, calculated spectrum): the C_f star must be tied to the DC midpoint (split film bank) and the EMI filter needs about 6 dB more common-mode attenuation (step f); full-V_dc steps at up to 30 V/ns on L1, the heatsink capacitance and the cables; L1 stores 5.7 J against 2.9 J per phase (twice the inductor) - all inside the totals above except the extra common-mode core.  The higher peak efficiency of three-level SiC does not pay for its 220 USD.
- **f_sw = 32 kHz:** the cheapest point of the two-level sweep within 16-48 kHz that keeps the PV module's validated gate-drive frequency; above it the device count rises.


**The architecture document's screen is optimistic.** Its 42-device T-type (4 x CRG40T120BK3SD + 3 x 650 V per position, 16 kHz) gives 2638 W of device loss at 125 kW / 900 V in this model, not 1,676 W: it used one 25 C V_CE(sat) point and a linear E(I); the data sheets' hot V_CE(sat) curves and the super-linear E_on(I) (Fig.13 of the CR Micro sheet: 3.2 mJ at 40 A, 7.3 mJ at 60 A) are higher.  Its hottest junction reaches 154 C at PF 1, 160 C at PF 0 and 162 C in rectifier mode (180 A, 900 V, 45 C) against a 150 C rating: the T-type with these parts needs 72 devices, not 42.  The CR Micro 650 V part is slow (E_on 3.2 mJ at 400 V / 50 A, p3) and the inner positions switch at full current in rectifier mode and at PF 0, which the PF-1 screen did not see.


### Cosmic-ray failure rate versus DC voltage (the reason for the 0.67 rule)

What the makers publish (all files under docs/datasheets/power-semiconductors/):
- **Semikron Danfoss AN 17-003** (2024): measured failure rate of a 1200 V IGBT chip (12E4) at 25 C, sea level, per cm2 of chip: 804 V: 0.51 FIT/cm2, 850 V: 2.5 FIT/cm2, 900 V: 14.0 FIT/cm2, 950 V: 47.0 FIT/cm2, 1000 V: 163.0 FIT/cm2 (Fig.3 and Table 2, calibrated on its 41 FIT/switch at 900 V example) - a factor of about 3.4 per 50 V; log-linear interpolation only, no extrapolation (p6); temperature factor exp(-(Tj-25)/47.6) and altitude factor 2^(h/1000 m) (p6); the free-wheeling diode is six to seven decades more robust (Fig.3); 3L NPC with 650 V chips at <= 500 V or 1200 V chips at <= 750 V is 'immune' (< 1 FIT/cm2, p9); in a T-type the outer switches block the full DC voltage about 25 % of the time and set the rate (p10).
- **Mitsubishi** (LTDS note, 2025): failure rate rises with V_CE and altitude and falls with temperature; numbers only on request.
- **Wolfspeed** (Gen3 1200 V SiC, on file): 0.01 FIT/cm2 at 640 V, 2.8 at 775 V, 30 at 900 V, 70 at 950 V.
- **CR Micro, NCE, Sichain, Yangjie: nothing published.**  The Chinese devices are assumed to behave like the reference technology at the same fraction of their rating; their real curves are a release item (RFQ question to each maker).

At 950 V a 1200 V IGBT is at 0.79 of its rating: 47 FIT/cm2 against 0.51 FIT/cm2 at the 0.67 rule voltage (804 V) - 93 times higher; at the 900 V full-load limit 28 times.  Per PCS (25 C, sea level unless stated; die areas and the blocking-time share in pcs_spec.json):

| design | FIT @850 V | @900 V | @950 V | @950 V, Tj 100 C | @950 V, 2000 m, Tj 100 C | @950 V, Tj -30 C (cold start) |
|---|---|---|---|---|---|---|
| architect, 42 IGBTs as specified | 13.23 | 37.89 | 104.00 | 21.52 | 86.06 | 330.24 |
| architect parts sized here (72 IGBTs) | 22.54 | 59.53 | 158.69 | 32.83 | 131.32 | 503.94 |
| chosen T-type, 1700/1200 V SiC | 0.03 | 0.03 | 0.16 | 0.03 | 0.13 | 0.50 |

For a fleet of 5,000 units operating continuously at 950 V (sea level, 25 C) the architect's 42-IGBT T-type would lose 4.6 units a year to single-event burnout (1.7 at 900 V; 3.8 at 2,000 m and Tj 100 C); the chosen design 0.007.  A burnt outer IGBT shorts the full DC link through the leg - the DC fuses clear it, the module is a repair.  These are order-of-magnitude figures (AN 17-003: 'order-of-magnitude estimates'), but the ratio is robust: the 1200 V T-type is two to three decades worse than a design that respects the rule.  Limiting the T-type to 804 V would break AC-02 (950 V operating, 900 V full load); derating power above 900 V does not help - cosmic-ray failures happen while blocking, at any current.


### Three-level modules (one per phase) against paralleled discretes

Owner's input: one three-level module per phase removes the paralleling and may settle the cosmic-ray question.  Searched: HIITIO (catalogue: I-type NPC modules in Easy 2B / 3B at 650 V and 1200 V; 62 mm / E6 half bridges at 1700 V), StarPower (GD200TLQ120L3S 3-level 1200 V 200 A is listed by RS, but the RS and Farnell pages refuse plain requests - not used), Macmic, Silan, CRRC, BYD, Leapers, AccoPower, Yangjie (no three-level module with a public data sheet or price found in this pass; sim/data/asia_modules.* from the parallel researcher was not on disk when this ran).  Two HIITIO data sheets filed:

| module | circuit, package | data sheet | E_on / E_off (conditions) | R_th | isolation | short circuit | qualification | Western class | price (indicative) |
|---|---|---|---|---|---|---|---|---|---|
| HCG400FL120E3RA | I-type 3-level NPC; all IGBTs and diodes 1200 V, Easy 3B (Cu base, Al2O3) | Rev.B, complete tables + curves (curves coarse) | 14.46 / 23.42 mJ at 600 V, 400 A, 125 C | 0.19 / 0.26 K/W j-heatsink | 3 kV rms 60 s, basic | **none stated** | none stated | Infineon Easy 3B 3-level NPC class (not named by HIITIO) | 180 / 110 USD - INDICATIVE, no public price: 0.7 / 0.45 x the Western Easy 3B / SEMiX 5 references (see block note) |
| HCG375FL065E3RC | I-type 3-level NPC; all switches and diodes 650 V, Easy 3B (Al2O3 DBC) | Rev.B, complete tables + curves (curves coarse) | 6.55 / 6.92 mJ at 400 V, 180 A, 125 C | 0.25 / 0.27 K/W j-heatsink | 3 kV rms 60 s, basic | **none stated** | none stated | Infineon F3L400R07W3S5_B59 class (650 V 400 A 3-level Easy 3B; not named by HIITIO) | 150 / 90 USD - INDICATIVE, no public price (as above) |

**Verdict on the data sheets:** good enough to compute losses and temperatures, **too thin to release**: no short-circuit withstand time (the DESAT blanking and response cannot be set against a rating), no qualification or reliability statement, no Western part named for the 'clone' claim, no price.

| option | per phase: power stage USD cat / 5k (devices, pads, gate drive) | device loss 125 kW @750 V | Tj / feasibility at the sizing corners (45 C) | gate channels per phase | assembly per phase | V/V_rated @950 V |
|---|---|---|---|---|---|---|
| NPC-module 1200 V (HIITIO HCG400FL120E3RA) (16 kHz) | 203 / 126 | 1867 W | NOT feasible - T3 misses its Tj limit by 32 K (one module per phase) | 4 | 1 screw-mounted module, press-fit pins, no paralleling | 0.44 |
| NPC-module 1200 V @8 kHz (8 kHz) | 203 / 126 | 1625 W | NOT feasible - T3 misses its Tj limit by 27 K (one module per phase) | 4 | 1 screw-mounted module, press-fit pins, no paralleling | 0.44 |
| NPC-module 650 V (HIITIO HCG375FL065E3RC) (16 kHz) | 173 / 106 | 1713 W | NOT feasible - T1 misses its Tj limit by 16 K (one module per phase) | 4 | 1 screw-mounted module, press-fit pins, no paralleling | 0.80 |
| TT-IGBT (best documented) (16 kHz) | 96 / 58 | 1584 W | meets all limits (min margin 7 K) | 4 | 20 TO-247 with clips and pads, paralleled per position | 0.80 |
| TT-SiC 1700(40m)/1200 (32 kHz) | 116 / 85 | 1267 W | meets all limits (min margin 8 K) | 4 | 22 TO-247 with clips and pads, paralleled per position | 0.56 |
| NPC-SiC 1200 + SBD (32 kHz) | 147 / 96 | 1576 W | meets all limits (min margin 2 K) | 4 | 28 TO-247 with clips and pads, paralleled per position | 0.44 |
| 2L-SiC 1700(40m) (24 kHz) | 66 / 53 | 1126 W | meets all limits (min margin 37 K) | 2 | 12 TO-247 with clips and pads, paralleled per position | 0.56 |

- A 300-400 A three-level module per phase does not carry this product: 198 A continuous is fine, but 216 A for 2 min and 1.2 x 216 A for 200 ms at PF 0 push the inner IGBTs past the module's 150 C operating limit with the same heatsink; two modules per phase (or a 600 A class EconoDUAL / 62 mm three-level part) double the money.  Its turn-off energy (23 mJ at 600 V / 400 A, 125 C) keeps it at 8-16 kHz, i.e. the large filter.
- Every three-level option - module or discrete - also carries the 150 Hz midpoint DC link of step d (25 film capacitors per half).
- **The 650 V I-type and cosmic rays:** Semikron's data (AN 17-003 p9) put 650 V chips below 1 FIT/cm2 only up to 500 V per device.  At 950 V a 650 V I-type device blocks 475 V (0.73 of its rating) and 522 V at the edge of the 10 % midpoint band (0.80) - past both the project's 0.67 rule and the last published data point; an outer/inner turn-off sequencing fault in an I-type also puts more than V_dc/2 across one device.  It does not settle the question; the 1200 V I-type module does (0.44), but at the loss and cost above.

## (b) Losses, junction temperatures, efficiency and derating

**Thermal concept (calculated):** one earthed extruded section per phase leg (3 for three-wire, 4 for four-wire), 150 mm x 400 mm, 24 fins x 60 mm, 4.0 kg each; 3 x AFB1224SHE-F00 (1 per section, push): 149 m3/h at 51 Pa per section, R_sa 50.3 mK/W, thermal time constant 179 s.  Each TO-247 on Al2O3 0.635 mm + 2 x 50 um grease + clip per TO-247: R_cs 0.261 K/W (basic insulation DC poles - PE as the PV design; Al2O3 is enough at these loss densities) + 0.08 K/W base spreading.  The fan is rated -10..+60 C (p3): at 60 C inlet it is at its limit and below -10 C it is outside its rating (PV risk R-05).

**Modulation:** carrier-based two-level PWM, sampled twice per carrier period, **sinusoidal references wherever they reach (m <= 0.98) and the min-max zero sequence (SVPWM-equivalent) only above** (low DC voltage with high AC voltage: 590-680 V).  Why: any low-frequency zero sequence becomes a 150 Hz voltage between the battery and earth on a three-wire TN connection (step f); a two-level leg has no midpoint to balance, so nothing else asks for it.  Discontinuous PWM would cut the switching loss (33 % of the device loss at 125 kW / 750 V) but is all zero sequence - not used.

**Current sharing of paralleled discretes:** the hottest device is taken to carry k = 1.10 x the mean current (conduction and switching) - it requires devices from one lot (R_DS(on) spread within +-10 %), Kelvin-source drive with a 0.5 ohm Kelvin resistor per device (gen/gdrv.py R_KS) and a symmetric layout; positive R_DS(on) temperature coefficient (x1.4-1.9 from 25 to 150 C) stabilises it.  Without binning (data-sheet max/typ 1.45 for SG2M035120LJ) k = 1.2 gives Tj 126 C at the worst 110 % corner (vs 116 C): binning or one-lot assembly is a production requirement.

**Junction temperatures (calculated):** worst over the envelope at 45 C inlet (V_dc 590-950 V, AC 340-460 V, PF angle 0..+-180, 25-110 % current): **116 C** at V_dc 950 V, AC 340 V, PF angle 90, 110 % (heatsink 74 C; per position TH 116 C, TL 116 C).  110 % at 60 C inlet: Tj 138 C (limits 150 C continuous / 165 C for 2 min, devices rated 175 C).  Not reachable (modulation): 590 V DC with 400 V AC, 590 V DC with 460 V AC, 600 V DC with 460 V AC, 650 V DC with 460 V AC

| overload | V_dc | PF angle | Tj (C) |
|---|---|---|---|
| 120 % for 2 min (heatsink RC from the 110 % state, then 73 C) | 600 | 0 | 117 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 600 | 0 | 137 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 75 C) | 750 | 0 | 122 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 750 | 0 | 144 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 77 C) | 900 | 90 | 128 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 900 | 90 | 151 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 77 C) | 900 | -90 | 128 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 900 | -90 | 151 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 77 C) | 900 | 180 | 128 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 900 | 180 | 151 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 73 C) | 600 | 180 | 117 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 600 | 180 | 137 |

**Efficiency (calculated, 400 V AC, PF 1, 45 C):** peak **99.20 %** (inverter, 600 V, 35 % load) - the >= 98.5 % requirement is **met** (margin 0.70 %-points against a model uncertainty of about +-0.2).  Full load: 98.65 % at 750 V, 98.55 % at 900 V, worst 98.49 %.

| direction, V_dc | 5 % | 10 % | 15 % | 20 % | 25 % | 30 % | 35 % | 40 % | 50 % | 60 % | 75 % | 90 % | 100 % | 110 % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| inverter 600 V | 97.50 | 98.57 | 98.92 | 99.07 | 99.15 | 99.19 | 99.20 | 99.20 | 99.17 | 99.11 | 99.00 | 98.85 | 98.73 | 98.59 |
| inverter 700 V | 97.05 | 98.33 | 98.74 | 98.93 | 99.03 | 99.09 | 99.11 | 99.12 | 99.10 | 99.05 | 98.95 | 98.80 | 98.68 | 98.54 |
| inverter 750 V | 96.81 | 98.20 | 98.65 | 98.86 | 98.97 | 99.03 | 99.06 | 99.08 | 99.06 | 99.02 | 98.91 | 98.77 | 98.65 | 98.51 |
| inverter 800 V | 96.56 | 98.06 | 98.55 | 98.78 | 98.91 | 98.97 | 99.01 | 99.03 | 99.02 | 98.98 | 98.88 | 98.74 | 98.62 | 98.48 |
| inverter 850 V | 96.31 | 97.92 | 98.45 | 98.70 | 98.84 | 98.91 | 98.96 | 98.98 | 98.98 | 98.94 | 98.85 | 98.70 | 98.58 | 98.45 |
| inverter 900 V | 96.05 | 97.77 | 98.34 | 98.62 | 98.77 | 98.85 | 98.90 | 98.93 | 98.94 | 98.91 | 98.81 | 98.67 | 98.55 | 98.41 |
| inverter 950 V | 95.77 | 97.62 | 98.24 | 98.53 | 98.69 | 98.79 | 98.85 | 98.88 | 98.89 | 98.86 | 98.78 | 98.64 | 98.52 | 98.38 |
| rectifier 600 V | 97.43 | 98.55 | 98.91 | 99.06 | 99.14 | 99.18 | 99.20 | 99.20 | 99.16 | 99.11 | 98.99 | 98.83 | 98.71 | 98.57 |
| rectifier 700 V | 96.96 | 98.30 | 98.73 | 98.92 | 99.02 | 99.08 | 99.10 | 99.11 | 99.09 | 99.04 | 98.93 | 98.78 | 98.66 | 98.52 |
| rectifier 750 V | 96.71 | 98.16 | 98.63 | 98.85 | 98.96 | 99.02 | 99.05 | 99.07 | 99.05 | 99.01 | 98.90 | 98.75 | 98.63 | 98.49 |
| rectifier 800 V | 96.44 | 98.02 | 98.53 | 98.77 | 98.89 | 98.96 | 99.00 | 99.02 | 99.01 | 98.97 | 98.87 | 98.72 | 98.60 | 98.45 |
| rectifier 850 V | 96.17 | 97.87 | 98.42 | 98.68 | 98.82 | 98.90 | 98.95 | 98.97 | 98.97 | 98.93 | 98.83 | 98.69 | 98.56 | 98.42 |
| rectifier 900 V | 95.88 | 97.72 | 98.32 | 98.60 | 98.75 | 98.84 | 98.89 | 98.92 | 98.92 | 98.89 | 98.80 | 98.65 | 98.53 | 98.39 |
| rectifier 950 V | 95.58 | 97.56 | 98.20 | 98.51 | 98.68 | 98.78 | 98.83 | 98.87 | 98.88 | 98.85 | 98.76 | 98.62 | 98.50 | 98.35 |

**Loss budget at 125 kW (W, calculated / budget):**

| block | 750 V | 900 V |
|---|---|---|
| semiconductors | 1277 | 1381 |
| L1 | 171 | 200 |
| L2 | 36 | 36 |
| Cf + damping | 6 | 6 |
| DC link | 20 | 20 |
| AC contactors | 55 | 55 |
| DC contactor | 12 | 10 |
| DC fuses | 30 | 21 |
| busbars, terminals | 30 | 30 |
| DC shunt | 3 | 2 |
| aux supply (control, drivers, sensors) | 29 | 29 |
| fans | 42 | 42 |
| **total** | **1710** | **1832** |

**Derating (calculated): largest continuous current (fraction of 180 A, searched up to 120 %; the product rating stops at 110 %) with every Tj within 150 C, worst of PF angle 0/90/180/-90 and AC 340/400/460 V:**

| inlet | 590 V | 600 V | 650 V | 700 V | 750 V | 800 V | 850 V | 900 V | 950 V |
|---|---|---|---|---|---|---|---|---|---|
| 45C | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 |
| 50C | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 |
| 55C | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 |
| 60C | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.15 | 1.15 | 1.15 | 1.15 |

## (c) Switching frequency and LCL filter

**f_sw = 32 kHz**, control sampled at 64 kHz (double update), delay 1.5 T_s = 23 us.  **L1 97 uH (2.4 %), C_f 50 uF per phase in star (Q 2.0 % of 125 kVA), L2 15 uH (0.4 %)**, passive branch R_d 2 ohm + C_d 10 uF per phase across C_f, plus capacitor-current active damping.  L1 from the ripple rule (25 % peak-to-peak of the 216 A peak at 950 V: V_dc/(4 f L1) = 76 A, 76 A pp from the time-domain PWM waveform); C_f and L2 place the resonance where capacitor-current active damping works (below f_s/6 = 10.7 kHz) for every grid and keep the carrier band several times below the 0.3 % target.

| grid | f_res (kHz) |
|---|---|
| stiff | 6.24 |
| SCR 50 | 3.23 |
| SCR 20 | 2.74 |
| SCR 10 | 2.53 |
| SCR 5 | 2.41 |

Resonance stays between 2.4 and 6.2 kHz: below f_s/6, where L1-current feedback with the 1.5 T_s delay is inherently damped, and above twice a 1 kHz current-loop bandwidth - one design covers SCR 5 to a stiff grid.  The passive branch alone lowers the resonance peak from 39 to 5 dB (relative to the L1 admittance at 1 kHz): it is the fallback if active damping is lost, not the main damping.

**Limits used (standard texts NOT on file - from memory):** IEEE 1547-2018 Table 26/27 odd-harmonic limits 4.0 / 2.0 / 1.5 / 0.6 / 0.3 % (h < 11 / < 17 / < 23 / < 35 / < 50), even 1 / 2 / 3 % for h 2 / 4 / 6 and 25 % of the odd limit above, TRD 5 %; the product's THDi < 3 %; above h50 (where no standard on file sets a limit; EN 50549-1 refers to the EN 61000-3 series, GB/T 34120 sets a THD) the 0.3 % per component is our design target.  Megarevo cites EN 50549-1/-10, GB/T 34120/34133 (pma_user-manual).

| V_dc | grid | m | PWM | THDi h2-h50 from PWM | THDi incl. carrier band | largest component above h50 | limit violations |
|---|---|---|---|---|---|---|---|
| 600 | stiff | 1.09 | minmax | 0.003 % | 0.080 % | h638: 0.046 % | 0 |
| 600 | SCR 50 | 1.09 | minmax | 0.002 % | 0.012 % | h638: 0.007 % | 0 |
| 600 | SCR 20 | 1.09 | minmax | 0.001 % | 0.005 % | h638: 0.003 % | 0 |
| 600 | SCR 10 | 1.09 | minmax | 0.001 % | 0.003 % | h638: 0.002 % | 0 |
| 600 | SCR 5 | 1.09 | minmax | 0.000 % | 0.001 % | h638: 0.001 % | 0 |
| 750 | stiff | 0.87 | none | 0.000 % | 0.093 % | h638: 0.065 % | 0 |
| 750 | SCR 50 | 0.87 | none | 0.000 % | 0.014 % | h638: 0.010 % | 0 |
| 750 | SCR 20 | 0.87 | none | 0.000 % | 0.006 % | h638: 0.004 % | 0 |
| 750 | SCR 10 | 0.87 | none | 0.000 % | 0.003 % | h638: 0.002 % | 0 |
| 750 | SCR 5 | 0.87 | none | 0.000 % | 0.002 % | h638: 0.001 % | 0 |
| 950 | stiff | 0.69 | none | 0.000 % | 0.080 % | h638: 0.055 % | 0 |
| 950 | SCR 50 | 0.69 | none | 0.000 % | 0.012 % | h638: 0.008 % | 0 |
| 950 | SCR 20 | 0.69 | none | 0.000 % | 0.005 % | h638: 0.004 % | 0 |
| 950 | SCR 10 | 0.69 | none | 0.000 % | 0.003 % | h638: 0.002 % | 0 |
| 950 | SCR 5 | 0.69 | none | 0.000 % | 0.001 % | h638: 0.001 % | 0 |

The PWM itself leaves THDi far below 3 %; the THDi at rated power will be set by the controller (dead time 250 ns at 32 kHz = a 1.6 % volt-second error before compensation, sensor offsets, grid background distortion, the loop gain at h5-h13): budget 2.5 % for those, to be verified by the control simulation (item 3 of ARCHITECTURE-PCS section 9).

**Common mode.** With the C_f star tied to the DC midpoint (the architecture's choice, and necessary - step f), the carrier harmonic of the two-level common-mode voltage (441 V peak at 32 kHz, 950 V) drives 48 A rms through the three L1 in parallel, C_f and the DC midpoint (16 A per phase): it is part of the L1 ripple (17 A rms HF per phase in total, DM 6 A) and of the DC-link ripple (step d).  The architecture document did not count it.

**Light load:** C_f draws 4.2 A capacitive at 460 V (340 V: 1.82 kvar, 400 V: 2.51 kvar, 460 V: 3.32 kvar) - the current controller compensates it at the grid terminals (PF -1..+1 control), no switched capacitor is needed.  **Inrush:** connecting to the grid with C_f empty and the inverter off would ring L2-C_f at 5.8 kHz with 686 A peak (Z0 548 mOhm) - forbidden: the sequence is DC precharge, inverter builds the grid voltage on C_f, synchronise (|dV| <= 5 %, 5 deg), then close; the residual step gives 68 A peak, which the current loop takes over within a few control periods.

The electrical requirement of L1, L2 and L_N (inductance versus current, rms / peak / ripple, frequency, loss budget, insulation) is written to pcs_spec.json under "inductors" for the magnetics engineer.

## (d) DC link

**Split film bank: 5 + 5 x Faratronic C3D1U147** (140 uF, U_N 600 V at 70 C / 500 V at 85 C, ESR 3.0 mOhm, I_max 40.2 A, Faratronic-C3D.pdf p8): 700 uF per half, 350 uF across 950 V.  Two-level legs do not use the midpoint; it exists for the C_f star (the common-mode path, steps c and f), so the halves are sized by the ripple current: worst half-bank 130 A rms at 950 V / PF angle 180 (110 %): 0 A below 2 kHz, 128 A carrier band, 24 A of the C_f-star common-mode current (step c) - 26.0 A per capacitor = 65 % of I_max (project rule <= 70 %), case rise 6.3 K, ESR loss 20 W.

| operating point (110 %) | m | PWM | I_dc (A) | upper half LF / HF (A rms) | lower half LF / HF | midpoint LF from the legs (A rms) |
|---|---|---|---|---|---|---|
| 600 V, 0 deg | 1.09 | minmax | 229 | 0 / 81 | 0 / 81 | 0 |
| 600 V, 180 deg | 1.09 | minmax | -229 | 0 / 81 | 0 / 81 | 0 |
| 600 V, 90 deg | 1.12 | minmax | 0 | 1 / 110 | 1 / 110 | 0 |
| 750 V, 0 deg | 0.87 | none | 183 | 0 / 117 | 0 / 117 | 0 |
| 750 V, 180 deg | 0.87 | none | -183 | 0 / 117 | 0 / 117 | 0 |
| 750 V, 90 deg | 0.90 | none | 0 | 0 / 98 | 0 / 98 | 0 |
| 950 V, 0 deg | 0.69 | none | 144 | 0 / 128 | 0 / 128 | 0 |
| 950 V, 180 deg | 0.69 | none | -144 | 0 / 128 | 0 / 128 | 0 |
| 950 V, 90 deg | 0.71 | none | 0 | 0 / 87 | 0 / 87 | 0 |

**Voltage use and life (calculated from the p12 life curve):** 475 V per half at 950 V = 0.79 U_N; 522 V at a 10 % midpoint deviation = 0.87 U_N (1.1 U_N is allowed for 30 % of the on-load time, p12); the 560 V per-half trip = 0.93 U_N (1.15 U_N allowed 30 min/day).  Hot spot = inlet + 5 K + case rise: 45C_750V: 56 C, 288 kh; 45C_900V: 56 C, 225 kh; 45C_950V: 56 C, 204 kh; 60C_750V: 71 C, 316 kh; 60C_900V: 71 C, 227 kh; 60C_950V: 71 C, 200 kh.  The 800 V-class C3D2K117 of the architecture is not needed (0.59 U_N, 34.5 A, 110 uF in the same can).

**Midpoint ripple (110 %, SVPWM; including the 150 Hz common-mode current through C_f, up to 8 A rms):** 600 V / 0 deg 17.7 V, 600 V / 180 deg 17.7 V, 600 V / 90 deg 18.2 V, 750 V / 0 deg 0.0 V, 750 V / 180 deg 0.0 V, 750 V / 90 deg 0.0 V, 950 V / 0 deg 0.0 V, 950 V / 180 deg 0.0 V, 950 V / 90 deg 0.0 V peak-to-peak - within 60 V.  The two-level legs need no midpoint control; static balance by the bleeders, a slow firmware check of the half voltages.

**Three-level contrast (why the topology changed):** a three-level leg draws a 150 Hz midpoint current that no zero sequence cancels at PF 0 and 600-750 V (np_residual): 25 capacitors per half instead of 5 (step a).  The architecture's 660 uF per half relied on a midpoint control that cannot work at PF 0.

**Four-wire:** the fourth (neutral) leg is one more two-level half-bridge; it takes the neutral current from P and N, the bank sees no 50 Hz midpoint current (a T-type phase leg would push 122 A peak into the midpoint under 100 % unbalance - 277 V peak on this bank).  A single-phase full load pulsates at 100 Hz: about 39 A rms at 750 V flows into the battery (installation requirement, as the architecture said).

**Film against aluminium electrolytic (calculated):** the bank is ripple-current-limited, not energy-limited.  Electrolytics (Samyoung TDC400V470M35*35 V, 400 V, 2 in series per half with balancing) carry 2.54 A each at >= 10 kHz: 52 strings per half = 208 cans, 1321 / 778 USD against 49 / 42 USD of film (estimate), and a life of 45C inlet 23 kh, 60C inlet 8 kh (2,000 h at 105 C, x2 per 10 K, 20 K self-heating ESTIMATE) against > 100 kh for film.  Film only.

**Discharge:** bleeder 440 kOhm per half (also the static balance), 0.51 W per half at 950 V: 475 V -> 60 V in 10.6 min - label 'wait 10 min' needs the active path: the DC-side precharge relay and resistor of the lean port discharge the bank in seconds when the firmware commands it (port_spec lean); the bleeder is the passive backstop.

## (e) Commutation, gate drive, dead time, short circuit

**Commutation (ngspice, VDMOS models of SG2M040170HJ fitted to the data sheet's Q_gd and E_off - the PV design's calibration, read from sim/out/pv_tradeoff/vdmos_calibration.json):** 6 devices per switch on the **PV module's verified physical leg, repeated for every device pair** (pv_design.leg_net: 3 x KEMET C4AQ 2.2 uF / 1300 V decoupling at the pins, 15 nH bus, 11.5 nH board + devices, RC damper 2 x 4.99 ohm + 2 x 4.7 nF per pair) - three pairs in parallel: 19.8 uF, 5.0 nH, 3.83 nH, damper 3.33 ohm + 7.0 nF on the 350 uF film bank (ESL 21 nH).  This is a layout requirement: one shared loop of 20-30 nH for six pairs gave 2.0-2.3 kV in the first run of this step.  Off-resistance sweep at the worst case (V_dc = 1050 V = the DC over-voltage trip, I = 450 A = the per-phase over-current trip, bus and board inductance x1.5):

| R_G,off per device (ext) | peak V_DS (V) | overshoot (V) | di/dt, all six (A/ns) | dv/dt (V/ns) | E_off, all six (mJ) | deck |
|---|---|---|---|---|---|---|
| 2.5 ohm | 1491 | 441 | 57.6 | 117 | 2.83 | sim/spice/pcs_commutation_sweep0.cir |
| 5.0 ohm | 1446 | 396 | 51.9 | 83 | 4.43 | sim/spice/pcs_commutation_sweep1.cir |
| 7.5 ohm | 1408 | 358 | 47.1 | 64 | 6.01 | sim/spice/pcs_commutation_sweep2.cir |
| 12.5 ohm | 1352 | 302 | 39.8 | 44 | 9.11 | sim/spice/pcs_commutation_sweep3.cir |

**Chosen: R_G,off 7.5 ohm, R_G,on 8.75 ohm per device** (the smallest of the sweep that holds the limit): worst case 1408 V peak against the 0.85 x 1700 = 1445 V project limit; nominal (950 V, 405 A = 1.2 x 216 A peak + ripple, nominal leg): 1217 V, 59 V/ns, 48.4 A/ns.  E_off at this R_G is 2.17 x the data-sheet curve; the device loss at 125 kW / 750 V rises from 1218 to 1277 W, peak efficiency 99.20 % (full load 750 V 98.65 %) - step b already carries this factor (this step runs first).  Decks: sim/spice/pcs_commutation_*.cir.

**Gate drive (the project's channel, gen/gdrv.py, NSI6651ASC):** one channel per switch position - **6 channels for three-wire, 8 for four-wire** (the architecture had 12/16).  Rails **+18 / -3.5 V** (the PV setting for this device, gdrv GATE_V (18, 3.5)); per-device gate resistor and 0.5 ohm Kelvin resistor (R_KS), per-gate Miller clamp FET as the PV rev-5 channel; DESAT string 100 ohm + 3 x US1MH and the short-circuit booster (booster=True) unchanged.  Six gates per channel: Q_g 468 nC, 0.32 W at 32 kHz (bias secondary budget 0.5 W - one SN6505B transformer per phase with two secondaries); peak gate current 14 A wanted against the NSI6651's 10 A - **add a discrete NPN/PNP push-pull buffer per channel** (two SOT-89 transistors, about 0.3 USD) or split the six devices over two drivers (+6 channels); the buffer is the cheaper answer.  Dead time: **300 ns** in the ePWM dead-band (D-049: no RC stretch; the drivers' cross-wired IN-/interlock still blocks an overlap).

**Short circuit:** DESAT trips at V_DS 6.3-7.9 V (NSI6651 8.5-9.8 V minus 100 ohm x I_CHG and 3 x US1MH, gen/gdrv.py DESAT_CLASS[1700]); at the 450 A over-current trip the hottest device sits at 6.3 V (no nuisance trip); blanking 150-500 ns, detection to off <= 1.0 us with the booster - inside the 2 us withstand ASSUMED for Sichain (no rating published: SC test or maker statement is a release item).

**If IGBTs were used instead (the architecture's choice) the channel would differ:** rails +15 / -8 V; DESAT threshold for V_CE(sat) of 3-4 V at twice rated current with 2-3 us blanking against a 3-10 us withstand (CR Micro / NCE parts state none or 3-10 us); no SiC booster (the driver's soft turn-off suffices, the IGBT limits its own short-circuit current); dead time 1.0-1.5 us; gate resistors 5-10 ohm; gate charge 208-331 nC per 40-80 A device at a 23 V swing.

**Hardware / firmware split (D-049, owner's input 2):** discrete hardware only for what must act faster than the controller or with the controller dead where a hazard follows: driver DESAT + booster -> FLT -> trip-zone pin; driver UVLO -> RDY -> trip zone; pull-downs on every PWM and EN line (controller in reset = gates off); external stop wired onto the drivers' EN; the DC contactor hold-off comparator (lean port).  In the controller's own hardware: per-phase over-current (CMPSS window at +-450 A on the L1 sensor, digital filter) and DC over-current / over-voltage (ADC limit trips at 400 A / 1050 V) -> trip zone, latching all PWM low; dead time (dead-band).  Everything else - grid protection, relay tests, anti-islanding, sequencing, temperature, fans, insulation and residual-current monitoring - is firmware (list in the hand-over section).

## (f) AC port, DC port, common mode

**AC disconnect:** two 3-pole contactors in series (4-pole for four-wire), relay test before every connection; AC-1 >= 248 A at 60 C, U_i 1000 V, U_imp 8 kV, 24 V DC with economiser (ESTIMATE 20 W pull-in / 4 W hold); CHINT NXC-225 / CJX2-185, Delixi CJX2s-185 class - no data sheet on file (R-06, RFQ).  Relay test (firmware, before every connection): close each contactor alone and read the voltage across the other through the terminal- and C_f-side dividers - a welded pole shows as zero volts.  Two in series because the converter is non-isolated and the disconnection must survive one welded contact (IEC 62109-2 / EN 50549-1 'AC relay automatic checking' as the PMA cites them - clauses from memory).  The contactors make only after synchronisation (|dV| <= 5 %, 5 deg: 68 A peak, step c) and break only at zero current (firmware); with the controller dead the gates are off and only the brief diode-rectifier current flows, inside the AC-1 breaking capacity - no hardware hold-off is needed on the AC side (D-049).

**Precharge and start:** from the DC side through the lean port's relay and 220 ohm: C_eq 350 uF, tau 77 ms, within 10 V of 950 V after 0.35 s, 158 J, 4.3 A peak.  Then the inverter forms the grid voltage on C_f (current-limited), synchronises, runs the relay test, closes.  Grid start (battery empty or disconnected) uses the architecture's six-diode tap for the auxiliary supply only.

**Surge:** AC type II on the board (three thermally protected varistors L-PE, GDT N-PE in four-wire, monitored); C_f takes the residual; DC side: the PV rev-6 varistor network unchanged (Up,eff 3.79 kV).

**Common mode on a three-wire connection to an earthed-neutral (TN) grid.**  The DC side is galvanically tied to the grid: its midpoint sits at earth potential plus whatever zero sequence the converter makes.  (1) Low frequency: this design uses sinusoidal PWM wherever it reaches, so above about 680 V (400 V AC; about 780 V at 460 V AC) there is no 150 Hz common-mode voltage at all; below, the min-max zero sequence is needed (rms of its 150 Hz component, rated current):

| V_dc | AC | PWM | 150 Hz CM (V rms) | leakage at 1 / 5 / 20 uF to earth (mA) |
|---|---|---|---|---|
| 590 V | 400 V | minmax | 48 | 45 / 225 / 900 |
| 590 V | 460 V | minmax | 55 | 52 / 259 / 1035 |
| 620 V | 400 V | minmax | 48 | 45 / 225 / 900 |
| 620 V | 460 V | minmax | 55 | 52 / 259 / 1035 |
| 650 V | 400 V | minmax | 48 | 45 / 225 / 900 |
| 650 V | 460 V | minmax | 55 | 52 / 259 / 1035 |
| 680 V | 400 V | none | 0 | 0 / 0 / 0 |
| 680 V | 460 V | minmax | 55 | 52 / 259 / 1035 |
| 750 V | 400 V | none | 0 | 0 / 0 / 0 |
| 900 V | 400 V | none | 0 | 0 / 0 / 0 |
| 950 V | 400 V | none | 0 | 0 / 0 / 0 |

Against the residual-current monitor (IEC 62109-2 from memory: 10 mA per kVA continuous above 30 kVA = 1250 mA here, 300 mA for small units, sudden steps of 30 / 60 / 150 mA): the battery may have up to 6 uF to earth for a 300 mA budget (24 uF for the 10 mA/kVA one) when it is operated below 680 V; above, no limit from this source.  (The architecture's 0.7 A at 5 uF assumed 150 V of zero sequence - the min-max offset's 150 Hz part is about half that, and it is not used above 680 V here.)

(2) Switching frequency: the converter's common-mode voltage (441 V peak at 32 kHz, 950 V) is returned locally through C_f and the DC midpoint (step c), leaving 2.3 V peak between the terminals and the DC midpoint; through the battery's earth capacitance that alone drives 1uF: 0.5 A, 5uF: 2.3 A, 20uF: 9.1 A at 32 kHz - so the AC conductors need a common-mode choke of >= 150 uH at 32 kHz (three nanocrystalline cores over the busbars instead of the architecture's two): 1uF: 90 mA, 5uF: 77 mA, 20uF: 75 mA.

**What the product needs (answer):** no transformer and no special modulation beyond the one above.  Three-wire on a TN grid is allowed with (a) the C_f star tied to the DC midpoint, (b) the AC common-mode choke, (c) an installation limit on the battery's capacitance to earth for operation below 680 V (stated above), (d) the RCMU thresholds set per the code; where the battery exceeds it or the grid code forbids the DC-to-earth voltage, the four-wire version (N connected, the N leg holds the zero sequence) or an isolation transformer is the installer's choice.  The battery rack sits at +-V_dc/2 against earth in operation (as the architecture noted): its insulation and IMD must be rated for that.

**DC port = the lean battery port scaled to 250 A:** Hongfa HFE82V-300C/1000 (300 A at 85 C, break-once 1.5 kA; port_spec lean); aR >= 333 A (75 % rule at 230 A continuous, 1000 V DC) - 400 A class, RFQ (Hongfa HPE501 stops at 250 A: R-05); hold-off threshold 1000 A (the contactor stays closed above it and lets the fuse clear - discrete comparator, D-049); 2 x 200 uOhm in parallel in DC- (port_spec lean): 25 mV at 250 A, 6.3 W; port over-current 400 A and over-voltage 1050 V as ADC limit trips.  The 400 A fuse's slow region (1.5-3 kA, seconds) leaves the upstream requirement on the battery's own breaker (R-05: re-run the port_design coordination with the chosen fuse).

## (g) Cost (catalogue and 5,000 units)

Every line named and priced in sim/out/pcs_design/pcs_costed_bom.csv (and pcs_spec.json 'cost_usd'); the basis of each price is in the row.  Block totals:

| block | catalogue (USD) | 5,000 units (USD) |
|---|---|---|
| POWER | 239 | 202 |
| GATE DRIVE | 50 | 37 |
| LCL FILTER | 207 | 167 |
| SENSING | 60 | 49 |
| DC PORT | 184 | 136 |
| AC PORT | 159 | 127 |
| AUX SUPPLY | 37 | 31 |
| CONTROL | 10 | 9 |
| INTERFACE | 8 | 8 |
| THERMAL | 111 | 85 |
| MECH-ELEC | 13 | 11 |
| **three-wire total** | **1078 (8.6 USD/kW)** | **861 (6.9 USD/kW)** |
| four-wire increment | 203 | 162 |
| **four-wire total** | **1281** | **1024** |

**Against the architecture:** three-wire 1078 / 861 USD against its 1037 / 801 USD (+4 % / +8 %); four-wire 1281 / 1024 against 1194 / 919 USD.  The architecture's figures rest on 42 IGBTs that overheat (step a: 66 needed) and on a 660 uF midpoint bank that its own modulation cannot hold at PF 0 (step d: 25 capacitors per half for any three-level design); corrected for those two (+55 USD of IGBTs and pads, +158 USD of DC link at 5k) its T-type would cost about 1015 USD at 5k.  Evidence behind the 5k figure: 5 % (LCSC breaks, marketplace quote); the rest are assumed factors 0.68-0.85 on estimates - the inductors, the contactors, the fuses, the heatsink and the SiC device price are RFQ items.

## Hand-over

**Control engineer (plant and limits; pcs_spec.json 'handover'):**
- LCL: L1 97 uH (R about 1.08 mOhm), C_f 50 uF star (tied to the DC midpoint), L2 15 uH, R_d 2 ohm + C_d 10 uF per phase; resonance stiff 6.24 kHz, SCR 50 3.23 kHz, SCR 20 2.74 kHz, SCR 10 2.53 kHz, SCR 5 2.41 kHz; DC link 350 uF across the bus.
- Sampling 64 kHz double update, delay 23 us, dead time 300 ns (1.9 % volt-seconds -> compensate (THDi < 3 %)).
- current loop: L1 current (sensor on the C_f side of L1): resonance 2.4-6.2 kHz is below f_s/6 = 10.7 kHz, where converter-current feedback with 1.5 T_s delay is inherently damped; keep the bandwidth <= 1 kHz (below f_res(SCR 5)/2), resonant terms at h5/h7/h11/h13; grid current for PF/THD = i_L1 - C_f dv_Cf/dt; passive R_d-C_d branch as the fallback damping
- pll: on the C_f (or terminal) voltages, 400 V +-15 %, 50/60 Hz, SCR 5..stiff, unbalance and LVRT/HVRT (EN 50549-1 / GB/T 34120)
- modulation: two-level, sinusoidal where m <= 0.98, min-max zero sequence above (V_dc < ~680 V at 400 V, < ~780 V at 460 V)
- midpoint: no control (two-level); firmware plausibility of the half voltages; C_f-star 150 Hz current <= 8 A rms, ripple <= 18 V pp
- grid forming: voltage control on C_f (L1-C_f with the damping branch), current limit 1.2 x 216 A for 200 ms (Tj 141 C at that corner, step b), 120 % for 2 min, transitions grid <-> off-grid < 20 ms (Megarevo)
- four wire: neutral leg two-level with L_N = 97 uH, reference = -sum of the phase currents' zero sequence; 100 Hz battery current of single-phase load (~39 A rms at 750 V) is an installation item

**Board designers:**
- power stage: 3 two-level legs (4 for four-wire): 6 x SG2M040170HJ per switch (TO-247-4L, Kelvin source), Al2O3 pads on one earthed section per leg; per device pair 3 x 2.2 uF / 1300 V film at the pins + RC damper (2 x 4.7 nF 2 kV C0G, 6 x 15 ohm 2512); DC link 5 + 5 x C3D1U147 in two series halves, midpoint to the C_f star
- gate drive: 6 channels (8 four-wire): NSI6651ASC + NPN/PNP buffer per channel driving 6 gates, R_G,on 8.75 / R_G,off 7.5 ohm per device, R_KS 0.5 ohm, per-gate clamp FET, DESAT 100 ohm + 3 x US1MH, booster, rails +18 / -3.5 V; bias: one SN6505B transformer per phase (2 secondaries); default-off pull-downs; EN from the external stop (wired-AND); FLT/RDY to trip-zone pins (D-049)
- sensing, phase current: 3 (4) open-loop TMR +-500 A on the C_f side of L1, bandwidth >= 100 kHz for the CMPSS window at +-450 A
- sensing, grid voltage: terminal and C_f nodes, L1-L3 (+N), dividers to DC-: signal V_dc/2 +-375 V peak + 20 % surge headroom
- sensing, dc: V_DC+ - V_DC-, V_mid, terminal (bipolar, polarity), shunt 2 x 200 uOhm (25 mV at 250 A), ADC limit trips 400 A / 1050 V
- sensing, residual current: type-B fluxgate over L1-L3 (+N), 30 mA resolution, 1.25 A continuous range
- sensing, temperatures: NTC: 3 (4) heatsink sections, 3 (4) L1, DC link, inlet
- ports: DC: HFE82V-300C/1000, 2 x aR 400 A, precharge 220 ohm, hold-off comparator (discrete); AC: 2 x 3-pole contactor in series (4-pole four-wire), coil drivers with economiser, type II SPD, CM choke >= 150 uH (3 nanocrystalline cores)

**Firmware requirements this design relies on (D-049 allocation):**
- trip thresholds and timing: CMPSS phase window +-450 A (digital filter <= 0.5 us), ADC limits I_dc 400 A and V_dc 1050 V, trip zone latches all PWM low; clear only with no source active
- start-up self-test of every trip path (CMPSS, ADC limit, trip-zone, FLT/RDY inputs) and read-back / lock of the PWM, dead-band (300 ns) and trip configuration; windowed watchdog
- DC precharge: polarity check, close the DC contactor only with |V_bank - V_bat| <= 10 V; discharge through the precharge path on stop
- insulation test (IMD) before every connection, contactors open
- synchronise (|dV| <= 5 %, 5 deg), relay test (each contactor alone, voltage across the open one), close; open only at zero current
- grid protection EN 50549-1 / GB/T 34120: U/f windows and times, ROCOF / vector shift, active anti-islanding where IEC 62116 applies, LVRT / HVRT with reactive current, P(f), Q(U), cos phi(P) (settings by country)
- residual current: 30 / 60 / 150 mA step trips within 0.3 / 0.15 / 0.04 s, continuous limit per code (from memory - verify)
- current limit 1.2 x 216 A for <= 200 ms then trip; 120 % for <= 2 min; device thermal model + heatsink NTC; inlet-temperature derating (fan limit 60 C); fan speed control; open / shorted NTC detection
- dead-time compensation and the modulation policy (sinusoidal where m <= 0.98); half-voltage plausibility
- contactor coil economiser timing (discrete RC today, firmware PWM later option)

## What is not verified (and where it would change the design)

- Nothing is measured: every loss, temperature, overshoot and spectrum here is calculated from data-sheet curves (read by eye, +-5 %) or simulated with fitted VDMOS models.
- Sichain SG2M040170HJ: no public price (4.07 USD is the PV module's estimate), no qualification statement, no short-circuit withstand (2 us assumed), no cosmic-ray data (the 0.67 rule is applied to its 1700 V rating by scaling Wolfspeed's 1200 V curve). The qualified fallback of the PV module (Microchip MSC035SMA170B4, 39 USD) does not fit this product's budget at 36 devices.
- Paralleling six TO-247 per switch: sharing k = 1.10 needs one-lot or binned devices and the per-pair decoupling layout; both are requirements, not results.  The commutation decks use the PV leg scaled x3 - re-run with the extracted layout.
- Inductors, contactors, the 400 A aR fuse, the heatsink, the film capacitors and the CM cores have no quotes; 95 % of the 5,000-unit money is an assumed factor.
- Standards: EN 50549-1, IEC 62109-2, IEEE 1547 (the harmonic limits used), IEC 62116, GB/T 34120 - from memory, texts not on file.
- Battery capacitance to earth (1-20 uF range assumed) decides the three-wire TN installation limit below 680 V.
- Control: current loop, PLL, grid forming, four-wire neutral control and the THDi < 3 % at rated power are not simulated here (ARCHITECTURE-PCS section 9 items 3-7).
- Three-level modules (HIITIO): data sheets without short-circuit rating or qualification; prices indicative only.
- Fan AFB1224SHE-F00 is rated -10..+60 C: below -10 C start and at 60 C inlet it is outside / at its limit (PV R-05).
