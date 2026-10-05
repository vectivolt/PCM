# PCS-P125 power stage - design report (sim/pcs_design.py)

**Everything here is CALCULATED or SIMULATED (averaged loss model on data-sheet curves, analytic filter / thermal models, ngspice transients). Nothing is measured or bench-validated.** Device data and page references: sim/pcs_devices.py and sim/pv_devices.py. Requirements: REQUIREMENTS.md AC-01..03, SRC-1..6. Re-run: `.venv/bin/python sim/pcs_design.py`.

## Summary

- **Topology: two-level, 1700 V SiC (6 x SG2M040170HJ per switch, 36 devices, 6 gate channels), 32 kHz** - replaces the architecture's three-level T-type with 1200 V IGBTs.  Every device at <= 0.56 of its rating at 950 V (rule 0.67); the T-type's outer IGBTs sit at 0.79 and would fail by cosmic rays 104 FIT per unit at 950 V (25 C, sea level; Semikron AN 17-003 data) against 0.16 FIT here.  With the real inductor designs (section h) it is also the cheapest: 1130 USD at 5k against 1279 USD for the cheapest compliant T-type (B rule kept: 1700 V outer); B SiC T-type 1200 V (cross-check) (1236 USD) puts its 1200 V outer devices at 0.88 of rating at the 1050 V trip.
- **Efficiency (calculated):** peak 98.99 % (>= 98.5 % met), 98.30 % at 125 kW / 750 V; worst Tj 123 C at 110 % / 45 C inlet, 145 C at 60 C, 161 C after 1.2 x 216 A for 200 ms (175 C rating).
- **Filter:** L1 120 uH, C_f 50 uF (star on the DC midpoint), L2 6 uH; resonance 2.2-9.4 kHz; PWM THDi < 0.1 % (the 3 % is the controller's); inductors from sim/magnetics.py: 3 x L1 amorphous 11.6 kg, 3 x L2 1.2 kg, CM choke 2 cores - filter 549 / 443 USD, 39 kg, 543 W at 125 kW / 750 V.
- **DC link:** 5 + 5 x Faratronic C3D1U147 film, > 100 kh at 60 C inlet; electrolytics would need 204 cans and last 8 kh.
- **Cost:** 1409 / 1131 USD (catalogue / 5,000 units) three-wire, 1694 / 1361 USD four-wire, against the architecture's 1,037 / 801 and 1,194 / 919 USD, whose filter is priced at 10 + 6 USD/J; with the real inductors and its corrected IGBT count the architecture's T-type comes to 1790 USD at 5k (section h).

## (a) Topology and devices

**Basis.** One 150 mm plate-fin section per leg (24 fins x 60 mm, 400 mm long), 1 x AFB1224SHE-F00 per section: 149 m3/h at 51 Pa, R_sa 50.3 mK/W per section (calculated, pv_tradeoff heatsink model).  Device count per position = the smallest that keeps Tj within its limit at 198 A (110 %) / 45 C inlet (continuous), after 216 A for 2 min (heatsink RC) and after 259 A for 200 ms (device transient), over V_dc 600/750/900 V and PF angle 0/45/90/135/180/-90 deg (limits: SiC 150 / 165 / 175 C; CR Micro / NCE 150 C parts 125 / 145-150 / 150-175 C as rated).  Losses: averaged model over the fundamental period (sim/pcs_devices.py docstring), sinusoidal PWM where it reaches, SVPWM zero sequence below.  Costs here: devices + pads + gate channels + an L1 estimate only (step g has everything).  Efficiency: devices + an ESTIMATE for the rest (70 W + 440 W x (I/180 A)^2).

| candidate | f_sw kHz | devices per position | dev | ch | device loss 125 kW @750 / 900 V (W) | module loss 125 kW (est.) | eta full / peak (est.) | USD cat / 5k (4W: +5k) | V/V_rated @950 V | FIT 900 / 950 V |
|---|---|---|---|---|---|---|---|---|---|---|
| TT-IGBT (architect) | 16 | T1 6xCRG40T120BK3SD, T4 6xCRG40T120BK3SD, T2 5xCRG50T60AK3SD, T3 5xCRG50T60AK3SD | 66 | 12 | 1720 / 1934 | 2184 | 98.25 / 98.64 % | 734 / 557 (+96) | 0.80 | 57.73 / 156.90 |
| TT-IGBT (best documented) | 16 | T1 4xNCE40TD120VT, T4 4xNCE40TD120VT, T2 6xNCE80TD65BT, T3 6xNCE80TD65BT | 60 | 12 | 1559 / 1737 | 2023 | 98.38 / 98.62 % | 738 / 548 (+93) | 0.80 | 46.89 / 113.00 |
| TT-SiC 1200/1200 (rule check) | 32 | T1 5xSG2M035120LJ, T4 5xSG2M035120LJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ | 66 | 12 | 995 / 1084 | 1367 | 98.90 / 99.30 % | 709 / 535 (+95) | 0.79 | 25.01 / 55.22 |
| TT-SiC 1700/1200 | 32 | T1 4xSG2M020170HJ, T4 4xSG2M020170HJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ | 60 | 12 | 964 / 1089 | 1336 | 98.93 / 99.25 % | 891 / 699 (+149) | 0.56 | 0.10 / 0.18 |
| TT-SiC 1700(40m)/1200 | 32 | T1 5xSG2M040170HJ, T4 5xSG2M040170HJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ | 66 | 12 | 1267 / 1319 | 1640 | 98.69 / 99.22 % | 726 / 571 (+107) | 0.56 | 0.08 / 0.13 |
| NPC-SiC 1200 + SBD | 32 | T1 5xSG2M035120LJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ, T4 5xSG2M035120LJ, D5 3xYJD112040NQG2, D6 3xYJD112040NQG2 | 84 | 12 | 1585 / 1640 | 1957 | 98.43 / 99.13 % | 821 / 606 (+118) | 0.44 | 0.10 / 0.10 |
| ANPC-SiC 1200 | 32 | T1 5xSG2M035120LJ, T2 6xSG2M035120LJ, T3 6xSG2M035120LJ, T4 5xSG2M035120LJ, T5 5xSG2M035120LJ, T6 5xSG2M035120LJ | 96 | 18 | 1541 / 1562 | 1913 | 98.47 / 99.17 % | 861 / 636 (+128) | 0.44 | 0.12 / 0.12 |
| NPC-IGBT 1200 + SBD | 16 | T1 4xNCE40TD120VT, T2 5xNCE40TD120VT, T3 5xNCE40TD120VT, T4 4xNCE40TD120VT, D5 3xYJD112040NQG2, D6 3xYJD112040NQG2 | 72 | 12 | 2326 / 2473 | 2789 | 97.76 / 98.33 % | 856 / 621 (+118) | 0.44 | 9.28 / 9.28 |
| NPC-module 1200 V (HIITIO HCG400FL120E3RA) - NOT FEASIBLE: T3 misses its Tj limit by 32 K (one module per phase) | 16 | T1 1xHCG400FL120E3RA, T2 1xHCG400FL120E3RA, T3 1xHCG400FL120E3RA, T4 1xHCG400FL120E3RA, D5 1xHCG400FL120E3RA, D6 1xHCG400FL120E3RA | 3 | 12 | 1878 / 2044 | 2342 | 98.12 / 98.41 % | 1060 / 752 (+162) | 0.44 | 20.59 / 20.59 |
| NPC-module 1200 V @8 kHz - NOT FEASIBLE: T3 misses its Tj limit by 27 K (one module per phase) | 8 | T1 1xHCG400FL120E3RA, T2 1xHCG400FL120E3RA, T3 1xHCG400FL120E3RA, T4 1xHCG400FL120E3RA, D5 1xHCG400FL120E3RA, D6 1xHCG400FL120E3RA | 3 | 12 | 1630 / 1723 | 2287 | 98.17 / 98.51 % | 1230 / 889 (+189) | 0.44 | 20.59 / 20.59 |
| NPC-module 650 V (HIITIO HCG375FL065E3RC) - NOT FEASIBLE: T4 misses its Tj limit by 16 K (one module per phase) | 16 | T1 1xHCG375FL065E3RC, T2 1xHCG375FL065E3RC, T3 1xHCG375FL065E3RC, T4 1xHCG375FL065E3RC, D5 1xHCG375FL065E3RC, D6 1xHCG375FL065E3RC | 3 | 12 | 1705 / 1875 | 2168 | 98.26 / 98.53 % | 970 / 692 (+142) | 0.80 | 30.00 / 30.00 |
| 2L-SiC 1700 | 32 | TH 4xSG2M020170HJ, TL 4xSG2M020170HJ | 24 | 6 | 1023 / 1121 | 1493 | 98.80 / 99.02 % | 570 / 456 (+124) | 0.56 | 0.04 / 0.21 |
| 2L-SiC 1200 (rule check) | 32 | TH 6xSG2M035120LJ, TL 6xSG2M035120LJ | 36 | 6 | 850 / 922 | 1319 | 98.94 / 99.15 % | 412 / 308 (+75) | 0.79 | 64.80 / 151.20 |
| 2L-SiC 1700(40m) | 32 | TH 6xSG2M040170HJ, TL 6xSG2M040170HJ | 36 | 6 | 1200 / 1277 | 1669 | 98.66 / 99.02 % | 432 / 351 (+89) | 0.56 | 0.03 / 0.16 |

Switching-frequency sweep of the three cheapest candidates that pass the 0.67 rule and the efficiency floor (devices re-sized, L1 for 25 % ripple, L2 for the 0.3 % carrier-band limit with C_f 50 uF):

| candidate | f_sw kHz | devices | L1 / L2 uH | module loss 125 kW (est.) | peak eta | USD cat / 5k |
|---|---|---|---|---|---|---|
| 2L-SiC 1700(40m) | 16 | TH 6, TL 6 | 194 / 14 | 1635 | 99.07 % | 547 / 443 |
| 2L-SiC 1700(40m) | 24 | TH 6, TL 6 | 130 / 6 | 1628 | 99.06 % | 469 / 381 |
| 2L-SiC 1700(40m) | 32 | TH 6, TL 6 | 97 / 4 | 1669 | 99.02 % | 432 / 351 |
| 2L-SiC 1700(40m) | 40 | TH 6, TL 6 | 78 / 3 | 1727 | 98.97 % | 410 / 334 |
| 2L-SiC 1700(40m) | 48 | TH 6, TL 6 | 65 / 2 | 1792 | 98.91 % | 395 / 322 |
| 2L-SiC 1700 | 16 | TH 4, TL 4 | 194 / 14 | 1420 | 99.11 % | 684 / 548 |
| 2L-SiC 1700 | 24 | TH 4, TL 4 | 130 / 6 | 1433 | 99.08 % | 607 / 485 |
| 2L-SiC 1700 | 32 | TH 4, TL 4 | 97 / 4 | 1493 | 99.02 % | 570 / 456 |
| 2L-SiC 1700 | 40 | TH 4, TL 4 | 78 / 3 | 1570 | 98.96 % | 548 / 439 |
| 2L-SiC 1700 | 48 | TH 4, TL 4 | 65 / 2 | 1656 | 98.89 % | 533 / 427 |
| TT-SiC 1700(40m)/1200 | 16 | T1 5, T4 5, T2 6, T3 6 | 97 / 13 | 1657 | 99.22 % | 797 / 629 |
| TT-SiC 1700(40m)/1200 | 24 | T1 5, T4 5, T2 6, T3 6 | 65 / 6 | 1632 | 99.23 % | 745 / 587 |
| TT-SiC 1700(40m)/1200 | 32 | T1 5, T4 5, T2 6, T3 6 | 49 / 4 | 1640 | 99.22 % | 726 / 571 |
| TT-SiC 1700(40m)/1200 | 40 | T1 5, T4 5, T2 6, T3 6 | 39 / 3 | 1658 | 99.20 % | 714 / 562 |
| TT-SiC 1700(40m)/1200 | 48 | T1 5, T4 5, T2 6, T3 6 | 32 / 2 | 1681 | 99.18 % | 706 / 555 |

**Decision: two-level, 32 kHz, TH 6 x SG2M040170HJ, TL 6 x SG2M040170HJ** - 36 devices and 6 gate channels for three-wire: the PV module's 1700 V SiC device, gate-drive channel and switching frequency.  Screen-scope cost (devices, pads, gate drive, LCL, DC link) 432 USD catalogue / 351 USD at 5,000 units; module loss 1669 W at 125 kW; peak 99.02 % (estimates; step b gives the map).  **This replaces the architecture's three-level T-type.**

Why:
- **The 0.67 rule first (SRC-4).** 950/1700 = 0.56 for every device.  The architect's T-type: 950/1200 = 0.79 (0.75 at 900 V).
- **The DC link decides between two and three levels.** A three-level leg draws a 150 Hz midpoint current; at PF 0 (150 kVA reactive is in AC-02) and 600-750 V it cannot be cancelled by any zero sequence (np_residual: ideal carrier-based midpoint control still leaves up to 3.5 mF of need per half).  Keeping the ripple within the film's 60 V pp limit takes 25 C3D1U147 per half for every three-level option, against 5 per half for two-level (ripple-current-limited): 167 USD at 5k.  The architecture's 660 uF per half would ripple by hundreds of volts there.
- **Totals (5k, screen scope):** two-level 1700 V SiC 351 USD; best three-level (TT-SiC 1700(40m)/1200, 32 kHz) 571 USD; NPC / ANPC SiC 606 / 636 USD (ANPC does not earn its place: its clamps add six channels and save no loss); the architect's IGBT T-type, sized correctly and with the DC link it needs, 557 USD; the best documented IGBT T-type 548 USD.
- **Price of the safer choice:** none in money - the rule-compliant design is the cheapest one in the screen.  What the rule costs inside two-level: 43 USD against the same two-level with 1200 V SiC (2L-SiC 1200 (rule check), 0.79 of rating at 950 V, FIT 151 at 950 V vs 0.16).  The efficiency price against the best SiC three-level: module loss 1669 W vs 1640 W at 125 kW, peak 99.02 % vs 99.22 % (0.20 %-points; the slower gate resistor that the 950 V commutation needs adds about 60 W more, step e); against the architect's IGBT T-type it is a gain (2184 W, peak 98.64 %).
- **Four-wire:** the fourth leg of a two-level design is one more half-bridge; a T-type phase leg under 100 % unbalance pushes a 50 Hz current into its midpoint (step d) that the two-level bank does not have.
- **What two-level costs elsewhere:** common-mode voltage at f_sw 432 V peak against 235 V for three-level (950 V DC, calculated spectrum): the C_f star must be tied to the DC midpoint (split film bank) and the EMI filter needs about 6 dB more common-mode attenuation (step f); full-V_dc steps at up to 60 V/ns (step e) on L1, the heatsink capacitance and the cables; L1 stores 5.7 J against 2.9 J per phase (twice the inductor) - all inside the totals above except the extra common-mode core.  The higher peak efficiency of three-level SiC does not pay for its 220 USD.
- **f_sw = 32 kHz** - the cheapest compliant point of the re-optimisation with the real inductor designs (section h).  This screen, which prices L1 at 10 + 6 USD/J, points the other way (48 kHz would 'save' 29 USD here): with the magnetics model in the loop the inductor loss, not its stored energy, sets its price, and lower device and core loss at 32 kHz buy a cheaper L1.


**The architecture document's screen is optimistic.** Its 42-device T-type (4 x CRG40T120BK3SD + 3 x 650 V per position, 16 kHz) gives 2638 W of device loss at 125 kW / 900 V in this model, not 1,676 W: it used one 25 C V_CE(sat) point and a linear E(I); the data sheets' hot V_CE(sat) curves and the super-linear E_on(I) (Fig.13 of the CR Micro sheet: 3.2 mJ at 40 A, 7.3 mJ at 60 A) are higher.  Its hottest junction reaches 154 C at PF 1, 160 C at PF 0 and 162 C in rectifier mode (180 A, 900 V, 45 C) against a 150 C rating: the T-type with these parts needs 66 devices, not 42.  The CR Micro 650 V part is slow (E_on 3.2 mJ at 400 V / 50 A, p3) and the inner positions switch at full current in rectifier mode and at PF 0, which the PF-1 screen did not see.


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
| architect parts sized here (66 IGBTs) | 20.74 | 57.73 | 156.90 | 32.46 | 129.83 | 498.22 |
| chosen: 2L-SiC 1700(40m) (two-level) | 0.03 | 0.03 | 0.16 | 0.03 | 0.13 | 0.50 |

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
| NPC-module 1200 V (HIITIO HCG400FL120E3RA) (16 kHz) | 203 / 126 | 1878 W | NOT feasible - T3 misses its Tj limit by 32 K (one module per phase) | 4 | 1 screw-mounted module, press-fit pins, no paralleling | 0.44 |
| NPC-module 1200 V @8 kHz (8 kHz) | 203 / 126 | 1630 W | NOT feasible - T3 misses its Tj limit by 27 K (one module per phase) | 4 | 1 screw-mounted module, press-fit pins, no paralleling | 0.44 |
| NPC-module 650 V (HIITIO HCG375FL065E3RC) (16 kHz) | 173 / 106 | 1705 W | NOT feasible - T4 misses its Tj limit by 16 K (one module per phase) | 4 | 1 screw-mounted module, press-fit pins, no paralleling | 0.80 |
| TT-IGBT (best documented) (16 kHz) | 96 / 58 | 1559 W | meets all limits (min margin 8 K) | 4 | 20 TO-247 with clips and pads, paralleled per position | 0.80 |
| TT-SiC 1700(40m)/1200 (32 kHz) | 116 / 85 | 1267 W | meets all limits (min margin 8 K) | 4 | 22 TO-247 with clips and pads, paralleled per position | 0.56 |
| NPC-SiC 1200 + SBD (32 kHz) | 147 / 96 | 1585 W | meets all limits (min margin 2 K) | 4 | 28 TO-247 with clips and pads, paralleled per position | 0.44 |
| 2L-SiC 1700(40m) (32 kHz) | 66 / 53 | 1200 W | meets all limits (min margin 30 K) | 2 | 12 TO-247 with clips and pads, paralleled per position | 0.56 |

- A 300-400 A three-level module per phase does not carry this product: 198 A continuous is fine, but 216 A for 2 min and 1.2 x 216 A for 200 ms at PF 0 push the inner IGBTs past the module's 150 C operating limit with the same heatsink; two modules per phase (or a 600 A class EconoDUAL / 62 mm three-level part) double the money.  Its turn-off energy (23 mJ at 600 V / 400 A, 125 C) keeps it at 8-16 kHz, i.e. the large filter.
- Every three-level option - module or discrete - also carries the 150 Hz midpoint DC link of step d (25 film capacitors per half).
- **The 650 V I-type and cosmic rays:** Semikron's data (AN 17-003 p9) put 650 V chips below 1 FIT/cm2 only up to 500 V per device.  At 950 V a 650 V I-type device blocks 475 V (0.73 of its rating) and 522 V at the edge of the 10 % midpoint band (0.80) - past both the project's 0.67 rule and the last published data point; an outer/inner turn-off sequencing fault in an I-type also puts more than V_dc/2 across one device.  It does not settle the question; the 1200 V I-type module does (0.44), but at the loss and cost above.

## (b) Losses, junction temperatures, efficiency and derating

**Thermal concept (calculated):** one earthed extruded section per phase leg (3 for three-wire, 4 for four-wire), 150 mm x 400 mm, 24 fins x 60 mm, 4.0 kg each; 3 x AFB1224SHE-F00 (1 per section, push): 149 m3/h at 51 Pa per section, R_sa 50.3 mK/W, thermal time constant 179 s.  Each TO-247 on Al2O3 0.635 mm + 2 x 50 um grease + clip per TO-247: R_cs 0.261 K/W (basic insulation DC poles - PE as the PV design; Al2O3 is enough at these loss densities) + 0.08 K/W base spreading.  The fan is rated -10..+60 C (p3): at 60 C inlet it is at its limit and below -10 C it is outside its rating (PV risk R-05).

**Modulation:** carrier-based two-level PWM, sampled twice per carrier period, **sinusoidal references wherever they reach (m <= 0.98) and the min-max zero sequence (SVPWM-equivalent) only above** (low DC voltage with high AC voltage: 590-680 V).  Why: any low-frequency zero sequence becomes a 150 Hz voltage between the battery and earth on a three-wire TN connection (step f); a two-level leg has no midpoint to balance, so nothing else asks for it.  Discontinuous PWM would cut the switching loss (38 % of the device loss at 125 kW / 750 V) but is all zero sequence - not used.

**Current sharing of paralleled discretes:** the hottest device is taken to carry k = 1.10 x the mean current (conduction and switching) - it requires the R_DS(on) / V_GS(th) acceptance rule of the population analysis below (one lot alone does not bound it), Kelvin-source drive with a 0.5 ohm Kelvin resistor per device (gen/gdrv.py R_KS) and a symmetric layout; positive R_DS(on) temperature coefficient (x1.4-1.9 from 25 to 150 C) stabilises it.  Without binning (data-sheet max/typ R_DS(on) 1.30 for SG2M040170HJ) k = 1.2 gives Tj 134 C at the worst 110 % corner (vs 123 C): binning or one-lot assembly is a production requirement.

**Junction temperatures (calculated):** worst over the envelope at 45 C inlet (V_dc 590-950 V, AC 340-460 V, PF angle 0..+-180, 25-110 % current): **123 C** at V_dc 950 V, AC 460 V, PF angle 90, 110 % (heatsink 77 C; per position TH 123 C, TL 123 C).  110 % at 60 C inlet: Tj 145 C (limits 150 C continuous / 165 C for 2 min, devices rated 175 C).  Not reachable (modulation): 590 V DC with 400 V AC, 590 V DC with 460 V AC, 600 V DC with 460 V AC, 650 V DC with 460 V AC

| overload | V_dc | PF angle | Tj (C) |
|---|---|---|---|
| 120 % for 2 min (heatsink RC from the 110 % state, then 74 C) | 600 | 0 | 121 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 600 | 0 | 143 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 77 C) | 750 | 0 | 128 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 750 | 0 | 151 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 80 C) | 900 | 90 | 136 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 900 | 90 | 161 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 80 C) | 900 | -90 | 136 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 900 | -90 | 161 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 80 C) | 900 | 180 | 135 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 900 | 180 | 161 |
| 120 % for 2 min (heatsink RC from the 110 % state, then 74 C) | 600 | 180 | 121 |
| 1.2 x 216 A = 259 A for 200 ms from the 110 % state (device transient, tau_jc 30 ms, tau_cs 0.54 s) | 600 | 180 | 143 |

**Efficiency (calculated, 400 V AC, PF 1, 45 C):** peak **98.99 %** (inverter, 600 V, 40 % load) - the >= 98.5 % requirement is **met** (margin 0.49 %-points against a model uncertainty of about +-0.2).  Full load: 98.30 % at 750 V, 98.08 % at 900 V, worst 98.04 %.

| direction, V_dc | 5 % | 10 % | 15 % | 20 % | 25 % | 30 % | 35 % | 40 % | 50 % | 60 % | 75 % | 90 % | 100 % | 110 % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| inverter 600 V | 96.54 | 98.06 | 98.55 | 98.77 | 98.89 | 98.95 | 98.98 | 98.99 | 98.96 | 98.91 | 98.79 | 98.62 | 98.49 | 98.34 |
| inverter 700 V | 94.90 | 97.19 | 97.95 | 98.31 | 98.51 | 98.63 | 98.70 | 98.74 | 98.76 | 98.73 | 98.64 | 98.49 | 98.36 | 98.22 |
| inverter 750 V | 94.14 | 96.79 | 97.67 | 98.09 | 98.33 | 98.48 | 98.56 | 98.62 | 98.66 | 98.64 | 98.56 | 98.42 | 98.30 | 98.15 |
| inverter 800 V | 93.32 | 96.35 | 97.37 | 97.86 | 98.14 | 98.31 | 98.42 | 98.49 | 98.55 | 98.55 | 98.48 | 98.35 | 98.23 | 98.09 |
| inverter 850 V | 92.47 | 95.89 | 97.05 | 97.61 | 97.94 | 98.14 | 98.27 | 98.35 | 98.44 | 98.45 | 98.40 | 98.27 | 98.16 | 98.02 |
| inverter 900 V | 91.56 | 95.40 | 96.71 | 97.35 | 97.72 | 97.95 | 98.11 | 98.21 | 98.32 | 98.35 | 98.31 | 98.19 | 98.08 | 97.94 |
| inverter 950 V | 90.60 | 94.89 | 96.35 | 97.08 | 97.50 | 97.76 | 97.94 | 98.06 | 98.19 | 98.24 | 98.22 | 98.11 | 98.00 | 97.86 |
| rectifier 600 V | 96.41 | 98.02 | 98.52 | 98.76 | 98.88 | 98.94 | 98.97 | 98.98 | 98.95 | 98.90 | 98.77 | 98.60 | 98.46 | 98.31 |
| rectifier 700 V | 94.62 | 97.11 | 97.91 | 98.28 | 98.49 | 98.61 | 98.68 | 98.72 | 98.74 | 98.71 | 98.62 | 98.46 | 98.33 | 98.18 |
| rectifier 750 V | 93.77 | 96.68 | 97.61 | 98.06 | 98.30 | 98.45 | 98.54 | 98.60 | 98.64 | 98.62 | 98.54 | 98.40 | 98.27 | 98.12 |
| rectifier 800 V | 92.85 | 96.21 | 97.30 | 97.81 | 98.11 | 98.28 | 98.39 | 98.47 | 98.53 | 98.53 | 98.46 | 98.32 | 98.20 | 98.05 |
| rectifier 850 V | 91.86 | 95.71 | 96.95 | 97.55 | 97.89 | 98.10 | 98.24 | 98.32 | 98.41 | 98.43 | 98.37 | 98.24 | 98.12 | 97.97 |
| rectifier 900 V | 90.78 | 95.18 | 96.59 | 97.28 | 97.67 | 97.91 | 98.07 | 98.18 | 98.29 | 98.32 | 98.28 | 98.16 | 98.04 | 97.90 |
| rectifier 950 V | 89.62 | 94.62 | 96.22 | 96.99 | 97.43 | 97.71 | 97.89 | 98.02 | 98.16 | 98.21 | 98.18 | 98.08 | 97.96 | 97.82 |

**Loss budget at 125 kW (W, calculated / budget):**

| block | 750 V | 900 V |
|---|---|---|
| semiconductors | 1390 | 1524 |
| L1 | 509 | 669 |
| L2 | 34 | 34 |
| Cf + damping | 6 | 6 |
| DC link | 20 | 20 |
| AC contactors | 55 | 55 |
| DC contactor | 12 | 10 |
| DC fuses | 30 | 21 |
| busbars, terminals | 30 | 30 |
| DC shunt | 3 | 2 |
| aux supply (control, drivers, sensors) | 29 | 29 |
| fans | 42 | 42 |
| **total** | **2160** | **2442** |

**Derating (calculated): largest continuous current (fraction of 180 A, searched up to 120 %; the product rating stops at 110 %) with every Tj within 150 C, worst of PF angle 0/90/180/-90 and AC 340/400/460 V:**

| inlet | 590 V | 600 V | 650 V | 700 V | 750 V | 800 V | 850 V | 900 V | 950 V |
|---|---|---|---|---|---|---|---|---|---|
| 45C | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 |
| 50C | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.15 |
| 55C | 1.20 | 1.20 | 1.20 | 1.20 | 1.20 | 1.15 | 1.15 | 1.15 | 1.15 |
| 60C | 1.20 | 1.20 | 1.15 | 1.15 | 1.15 | 1.15 | 1.10 | 1.10 | 1.10 |

### Current sharing of six paralleled devices, by R_DS(on) population (PCM-06)

**Calculated** with the loss and thermal model above: the hottest device of a switch is the one with the lowest R_DS(on); its share k follows the six conductances with every R_DS(on) at its own junction temperature (iterated), and scales its conduction and switching loss as K_SHARE does; steady tiers at V_dc 950 V / 460 V AC / 90 deg, overload tiers at 900 V / 90 deg from the 110 % state.  The design model carries k = 1.10.

| population | tier | current (A) | k | hottest Tj (C) | other five (C) | limit (C) |
|---|---|---|---|---|---|---|
| data sheet: one 40 mOhm (typ) among five at 52 mOhm (max) | 45C 100 % | 180 | 1.216 | 117 | 113 | 150 |
| data sheet: one 40 mOhm (typ) among five at 52 mOhm (max) | 45C 110 % | 198 | 1.212 | 135 | 130 | 150 |
| data sheet: one 40 mOhm (typ) among five at 52 mOhm (max) | 45C 120 % 2 min | 216 | 1.212 | 153 | 145 | 165 |
| data sheet: one 40 mOhm (typ) among five at 52 mOhm (max) | 45C 200 ms | 246 (derated from 259) | 1.212 | 174 | 159 | 175 |
| data sheet: one 40 mOhm (typ) among five at 52 mOhm (max) | 60C 100 % | 180 | 1.215 | 138 | 134 | 150 |
| data sheet: one 40 mOhm (typ) among five at 52 mOhm (max) | 60C 110 % | 198 | 1.211 | 159 **over** | 154 | 150 |
| data sheet: one 40 mOhm (typ) among five at 52 mOhm (max) | 60C 120 % 2 min | 205 (derated from 216) | 1.212 | 165 | 158 | 165 |
| data sheet: one 40 mOhm (typ) among five at 52 mOhm (max) | 60C 200 ms | 220 (derated from 259) | 1.212 | 172 | 163 | 175 |
| acceptance rule: one at -5 % among five at +5 % | 45C 100 % | 180 | 1.080 | 107 | 106 | 150 |
| acceptance rule: one at -5 % among five at +5 % | 45C 110 % | 198 | 1.079 | 121 | 120 | 150 |
| acceptance rule: one at -5 % among five at +5 % | 45C 120 % 2 min | 216 | 1.079 | 133 | 131 | 165 |
| acceptance rule: one at -5 % among five at +5 % | 45C 200 ms | 259 | 1.079 | 156 | 151 | 175 |
| acceptance rule: one at -5 % among five at +5 % | 60C 100 % | 180 | 1.080 | 127 | 126 | 150 |
| acceptance rule: one at -5 % among five at +5 % | 60C 110 % | 198 | 1.079 | 143 | 141 | 150 |
| acceptance rule: one at -5 % among five at +5 % | 60C 120 % 2 min | 216 | 1.079 | 156 | 154 | 165 |
| acceptance rule: one at -5 % among five at +5 % | 60C 200 ms | 246 (derated from 259) | 1.079 | 169 | 165 | 175 |

The data sheet alone (one lot, V_GS(th) binning) does not bound the conduction share: the worst population takes k 1.22 (1.238 at equal temperatures; the temperature coefficient pulls it back only slightly because the hot device runs a few K above the others).  The trade study's 1.30 factor (D-057, sim/pcs_tradeoff.py: device count and 60 C rating at 100 % with k 1.30) covers that k where it is applied - 60 C, 100 %: 138 C <= 150 C - but the overload tiers are sized with the model's k 1.10: at 45 C inlet the 200 ms tier reaches 186 C against 175 C, and at 60 C the 110 % tier 159 C against 150 C.  Two ways out: (a, taken) **acceptance rule (BOM line of the SG2M040170HJ):** R_DS(on) of the six devices of one switch within +/-5 % of their mean at V_GS 18 V, I_D 38 A pulsed (< 200 us), T_J 25 C (incoming measurement or the maker's bin; the data sheet allows 40 typ / 52 max mOhm) - bounds the conduction share at k <= 1.086; V_GS(th) within +/-0.25 V (V_DS = V_GS, I_D 12 mA, 25 C; data-sheet spread 2.5-4.0 V) for the switching share; one lot per switch.  With it k stays <= 1.080 <= the model's 1.10: every 45 C tier holds at its nominal current; at 60 C inlet the firmware derates the overload tiers to 120 % 2 min 216 A, 200 ms 246 A (continuous: the derating map above).  (b) Without the rule the firmware would have to derate this population: 45C 200 ms 246 A; 60C 120 % 2 min 205 A; 60C 200 ms 220 A, and hold 60 C inlet to 100 % continuous (the trade study's k 1.30 rating) - cheaper in production, but 200 ms capability lost at 45 C; not taken.

## (c) Switching frequency and LCL filter

**f_sw = 32 kHz**, control sampled at 64 kHz (double update), delay 1.5 T_s = 23 us.  **L1 120 uH (2.9 %), C_f 50 uF per phase in star (Q 2.0 % of 125 kVA), L2 6 uH (0.1 %)**, passive branch R_d 2 ohm + C_d 10 uF per phase across C_f, plus capacitor-current active damping.  L1, C_f and L2 are the cheapest compliant set of the re-optimisation with the real inductor designs (section h, sim/pcs_tradeoff.py): ripple V_dc/(4 f L1) = 62 A pp at 950 V (62 A from the time-domain PWM waveform); C_f and L2 place the stiff-grid resonance below f_s/6 = 10.7 kHz (capacitor-current active damping), the SCR-5 resonance above twice the current-loop bandwidth, and keep every carrier-band component of the grid current within the 0.3 % target.

**Inductor parts (sim/magnetics.py, selected in sim/pcs_tradeoff.py):** L1 gapped amorphous C-core, alfoil 0.8 mm, 20 turns, 11.6 kg, 127 / 103 USD (catalogue / 5k), 170 W at 180 A / 750 V and 285 W at 198 A / 950 V (higher of own model and OpenMagnetics), hot spot 138 C at 60 C inlet; L2 amorphous C-core, alfoil 1.5 mm, 4 turns, 1.2 kg, 39 / 30 USD, 11 W at 180 A; AC CM choke 126 uH = 2 x Yunlu N-R-564440, 26 / 22 USD.  Their losses replace the step-c budgets in every efficiency figure (per phase a(V_dc) + k I^2).  All ESTIMATES: no quote, no sample.

| grid | f_res (kHz) |
|---|---|
| stiff | 9.42 |
| SCR 50 | 3.16 |
| SCR 20 | 2.58 |
| SCR 10 | 2.33 |
| SCR 5 | 2.20 |

Resonance stays between 2.2 and 9.4 kHz: below f_s/6, where L1-current feedback with the 1.5 T_s delay is inherently damped, and above twice a 1.00 kHz current-loop bandwidth - one design covers SCR 5 to a stiff grid (1.00 kHz is the lower edge of the usable crossover band, not a ceiling: the control study, sim/out/pcs_control, finds 1.0-3.25 kHz).  The passive branch alone lowers the resonance peak from 31 to 6 dB (relative to the L1 admittance at 1 kHz); the control study finds it required - active damping from the C_f voltage fails at the stiff grid.

**Limits used (standard texts NOT on file - from memory):** IEEE 1547-2018 Table 26/27 odd-harmonic limits 4.0 / 2.0 / 1.5 / 0.6 / 0.3 % (h < 11 / < 17 / < 23 / < 35 / < 50), even 1 / 2 / 3 % for h 2 / 4 / 6 and 25 % of the odd limit above, TRD 5 %; the product's THDi < 3 %; above h50 (where no standard on file sets a limit; EN 50549-1 refers to the EN 61000-3 series, GB/T 34120 sets a THD) the 0.3 % per component is our design target.  Megarevo cites EN 50549-1/-10, GB/T 34120/34133 (pma_user-manual).

| V_dc | grid | m | PWM | THDi h2-h50 from PWM | THDi incl. carrier band | largest component above h50 | limit violations |
|---|---|---|---|---|---|---|---|
| 600 | stiff | 1.09 | minmax | 0.002 % | 0.169 % | h638: 0.098 % | 2 |
| 600 | SCR 50 | 1.09 | minmax | 0.001 % | 0.011 % | h638: 0.006 % | 0 |
| 600 | SCR 20 | 1.09 | minmax | 0.001 % | 0.005 % | h638: 0.003 % | 0 |
| 600 | SCR 10 | 1.09 | minmax | 0.001 % | 0.002 % | h638: 0.001 % | 0 |
| 600 | SCR 5 | 1.09 | minmax | 0.000 % | 0.001 % | h638: 0.001 % | 0 |
| 750 | stiff | 0.87 | none | 0.000 % | 0.197 % | h638: 0.139 % | 2 |
| 750 | SCR 50 | 0.87 | none | 0.000 % | 0.013 % | h638: 0.009 % | 0 |
| 750 | SCR 20 | 0.87 | none | 0.000 % | 0.005 % | h638: 0.004 % | 0 |
| 750 | SCR 10 | 0.87 | none | 0.000 % | 0.003 % | h638: 0.002 % | 0 |
| 750 | SCR 5 | 0.87 | none | 0.000 % | 0.001 % | h638: 0.001 % | 0 |
| 950 | stiff | 0.69 | none | 0.000 % | 0.169 % | h638: 0.117 % | 2 |
| 950 | SCR 50 | 0.69 | none | 0.000 % | 0.011 % | h638: 0.007 % | 0 |
| 950 | SCR 20 | 0.69 | none | 0.000 % | 0.004 % | h638: 0.003 % | 0 |
| 950 | SCR 10 | 0.69 | none | 0.000 % | 0.002 % | h638: 0.002 % | 0 |
| 950 | SCR 5 | 0.69 | none | 0.000 % | 0.001 % | h638: 0.001 % | 0 |

The PWM itself leaves THDi far below 3 %; the THDi at rated power will be set by the controller (dead time 250 ns at 32 kHz = a 1.6 % volt-second error before compensation, sensor offsets, grid background distortion, the loop gain at h5-h13): budget 2.5 % for those, to be verified by the control simulation (item 3 of ARCHITECTURE-PCS section 9).

**Common mode.** With the C_f star tied to the DC midpoint (the architecture's choice, and necessary - step f), the carrier harmonic of the two-level common-mode voltage (441 V peak at 32 kHz, 950 V) drives 39 A rms through the three L1 in parallel, C_f and the DC midpoint (13 A per phase): it is part of the L1 ripple (14 A rms HF per phase in total, DM 5 A) and of the DC-link ripple (step d).  The architecture document did not count it.

**Light load:** C_f draws 4.2 A capacitive at 460 V (340 V: 1.82 kvar, 400 V: 2.51 kvar, 460 V: 3.32 kvar) - the current controller compensates it at the grid terminals (PF -1..+1 control), no switched capacitor is needed.  **Inrush:** connecting to the grid with C_f empty and the inverter off would ring L2-C_f at 9.2 kHz with 1084 A peak (Z0 346 mOhm) - forbidden: the sequence is DC precharge, inverter builds the grid voltage on C_f, synchronise (|dV| <= 5 %, 5 deg), then close; the residual step gives 107 A peak, which the current loop takes over within a few control periods.

The electrical requirement of L1, L2 and L_N (inductance versus current, rms / peak / ripple, frequency, loss budget, insulation) is written to pcs_spec.json under "inductors" for the magnetics engineer.

## (d) DC link

**Split film bank: 5 + 5 x Faratronic C3D1U147** (140 uF, U_N 600 V at 70 C / 500 V at 85 C, ESR 3.0 mOhm, I_max 40.2 A, Faratronic-C3D.pdf p8): 700 uF per half, 350 uF across 950 V.  Two-level legs do not use the midpoint; it exists for the C_f star (the common-mode path, steps c and f), so the halves are sized by the ripple current: worst half-bank 129 A rms at 950 V / PF angle 0 (110 %): 0 A below 2 kHz, 128 A carrier band, 20 A of the C_f-star common-mode current (step c) - 25.8 A per capacitor = 64 % of I_max (project rule <= 70 %), case rise 6.2 K, ESR loss 20 W.

| operating point (110 %) | m | PWM | I_dc (A) | upper half LF / HF (A rms) | lower half LF / HF | midpoint LF from the legs (A rms) |
|---|---|---|---|---|---|---|
| 600 V, 0 deg | 1.09 | minmax | 229 | 0 / 81 | 0 / 81 | 0 |
| 600 V, 180 deg | 1.09 | minmax | -229 | 0 / 81 | 0 / 81 | 0 |
| 600 V, 90 deg | 1.13 | minmax | 0 | 1 / 110 | 1 / 110 | 0 |
| 750 V, 0 deg | 0.87 | none | 183 | 0 / 117 | 0 / 117 | 0 |
| 750 V, 180 deg | 0.87 | none | -183 | 0 / 117 | 0 / 117 | 0 |
| 750 V, 90 deg | 0.90 | none | 0 | 0 / 99 | 0 / 99 | 0 |
| 950 V, 0 deg | 0.69 | none | 144 | 0 / 128 | 0 / 128 | 0 |
| 950 V, 180 deg | 0.69 | none | -144 | 0 / 128 | 0 / 128 | 0 |
| 950 V, 90 deg | 0.71 | none | 0 | 0 / 88 | 0 / 88 | 0 |

**Voltage use and life (calculated from the p12 life curve):** 475 V per half at 950 V = 0.79 U_N; 522 V at a 10 % midpoint deviation = 0.87 U_N (1.1 U_N is allowed for 30 % of the on-load time, p12); the 560 V per-half trip = 0.93 U_N (1.15 U_N allowed 30 min/day).  Hot spot = inlet + 5 K + case rise: 45C_750V: 56 C, 288 kh; 45C_900V: 56 C, 225 kh; 45C_950V: 56 C, 204 kh; 60C_750V: 71 C, 314 kh; 60C_900V: 71 C, 227 kh; 60C_950V: 71 C, 201 kh.  The 800 V-class C3D2K117 of the architecture is not needed (0.59 U_N, 34.5 A, 110 uF in the same can).

**Midpoint ripple (110 %, SVPWM; including the 150 Hz common-mode current through C_f, up to 9 A rms):** 600 V / 0 deg 18.4 V, 600 V / 180 deg 18.4 V, 600 V / 90 deg 19.0 V, 750 V / 0 deg 0.0 V, 750 V / 180 deg 0.0 V, 750 V / 90 deg 0.0 V, 950 V / 0 deg 0.0 V, 950 V / 180 deg 0.0 V, 950 V / 90 deg 0.0 V peak-to-peak - within 60 V.  The two-level legs need no midpoint control; static balance by the bleeders, a slow firmware check of the half voltages.

**Three-level contrast (why the topology changed):** a three-level leg draws a 150 Hz midpoint current that no zero sequence cancels at PF 0 and 600-750 V (np_residual): 25 capacitors per half instead of 5 (step a).  The architecture's 660 uF per half relied on a midpoint control that cannot work at PF 0.

**Four-wire:** the fourth (neutral) leg is one more two-level half-bridge; it takes the neutral current from P and N, the bank sees no 50 Hz midpoint current (a T-type phase leg would push 122 A peak into the midpoint under 100 % unbalance - 277 V peak on this bank).  A single-phase full load pulsates at 100 Hz: about 39 A rms at 750 V flows into the battery (installation requirement, as the architecture said).

**Film against aluminium electrolytic (calculated):** the bank is ripple-current-limited, not energy-limited.  Electrolytics (Samyoung TDC400V470M35*35 V, 400 V, 2 in series per half with balancing) carry 2.54 A each at >= 10 kHz: 51 strings per half = 204 cans, 1295 / 763 USD against 49 / 42 USD of film (estimate), and a life of 45C inlet 23 kh, 60C inlet 8 kh (2,000 h at 105 C, x2 per 10 K, 20 K self-heating ESTIMATE) against > 100 kh for film.  Film only.

**Capacitance on the bus (calculated):** 350 uF split bank + 59.4 uF of leg films at the device pins (3 legs x 9 x 2.2 uF, step e) = **409.4 uF** across DC+ / DC- (450.3 uF at +10 %) - the precharge (step f), the bleeder and the pulse ratings use this total; C3D1U147 tolerance code K (+/-10 %, the 9th character of the order code) - all bus figures use +10 % worst case.

**Discharge (calculated, PCM-03):** bleeder 4 x 93.1 k per half = 372 k (also the static balance).  After the DC contactor and both AC contactor sets open, the charge sits in the bank, the leg films, C_f + C_d (60 uF per phase, star on M) and the aux block's input bulk (6.6 uF): the C_f discharge through their 6 M VC dividers and, once the bus has fallen to their voltage, through the body diodes into the bus and its bleeders - they never hold more than the bus (body diodes) and they are on no accessible terminal (contactors open).  Largest voltage between any two conductors: 60 V after **14.7 min worst case** (worst case: every capacitor +10 %, bleeder +1 %, the 6 M dividers credited (VB, VA, VC x 3), from the 1050 V trip with the C_f charged to the 460 V AC peak at the worst of 6 phase angles; aux input bulk on the bus (its strings not credited); the C_f nodes held inside the bus by the body diodes; criterion: the largest voltage between any two conductors <= 60 V), 13.3 min nominal, label 'wait 15 min'.  Before PCM-03 (4 x 110 k, bank 350 uF only): the same worst case took 17.0 min.  Bleeder loss at 950 V 1.21 W (+0.19 W standby against 4 x 110 k); element 140 V / 0.211 W at the 560 V half trip.

**Upper half (PCM-04, calculated):** the hardware trips see the bus (VB, 1050 V) and the lower half (VA, 560 V); the upper half is VB - VA.  U_N of the C3D1U147 at its hot spot: 600 V at 45 C inlet (56 C), 592 V at 60 C (71 C); 1.5 U_N is allowed for 100 ms (C3D p12).

| mechanism | upper half (V) | VA seen (V) | x U_N(70 C) | time scale | verdict |
|---|---|---|---|---|---|
| capacitance tolerance split at precharge (C3D code K +/-10 %: whole upper half -10 %, lower +10 %) | 578 | 472 | 0.96 | static after the precharge, bled out with tau 4.3 min | inside U_N(70 C); |VA - VB/2| = 0.05 VB, below the 0.07 VB imbalance limit; VB - VA reaches the 540 V limit only above 982 V, outside the 950 V operating range |
| one lower-half capacitor shorted (the lower half collapses, the battery holds the upper half through the closed DC contactor) | 950 | 0 | 1.58 | until the DC contactor opens: firmware detection <= 72 us + release <= 10 ms + margin ~ 20 ms | 1.58 x U_N(70 C) at 950 V (1.75 at the 1050 V trip), plus the ring of the upper half's recharge through the battery inductance (not modelled): above every C3D allowance (1.5 U_N for 100 ms) after a first failure, for the ~20 ms until the DC contactor opens - only opening it ends the stress, so a discrete comparator would not shorten it |
| upper bleeder string open (only the films' insulation, >= 10,000 s / C, across the upper half) | 927 | 23 | 1.55 | drifts with tau 8.0 min from 475 V at 950 V: the 0.07 VB imbalance limit after 1.3 min, U_N(70 C) after 2.6 min | minutes - firmware imbalance limit (controlled stop) long before U_N |
| C_f-star current into M (150 Hz zero sequence <= 9 A rms + carrier band, step d) | 534 | 516 | 0.89 | cycle by cycle, zero mean (150 Hz alone: +/-9.9 V peak) | bounded ripple, no drift |
| DC offset / step in the zero sequence (C_f blocks DC; a step of V_dc/2 moves M through 3 C_f) | 576 | 474 | 0.96 | transient, bled out with tau 4.3 min | bounded, no steady drift |

**Decision: firmware, no hardware** - the one fast mechanism (a shorted lower-half capacitor) is relieved only by opening the DC contactor, whose 10 ms release dwarfs the firmware's 72 us latency - a discrete comparator would gain nothing; every other mechanism takes minutes or stays within U_N(70 C) (SRC-7 / D-050: firmware where it is adequate).  Firmware limit (hand-over): VB - VA >= 540 V (527-553 V with 0.6 % per channel, ASSUMED) = the hardware VA trip's nominal; VA < 0.25 VB or VB - VA < 0.25 VB (a shorted half); |VA - VB/2| > 0.07 VB for 1 s (the worst static split of +/-10 % halves is 0.05 VB); <= 72 us (31.25 us outer loop x 2 + one 10 us conversion); ADC VA (ADC-A) and VB (ADC-B), converted together every <= 10 us, difference in the outer loop -> trip: one-shot on all ePWM, K_B_M, K_A_M and K_AC2_M low (DC contactor releases in <= 10 ms unless HOLD), cause logged, no automatic restart; slow imbalance -> controlled stop, then the same.

## (e) Commutation, gate drive, dead time, short circuit

**Commutation (ngspice, VDMOS models of SG2M040170HJ fitted to the data sheet's Q_gd and E_off - the PV design's calibration, read from sim/out/pv_tradeoff/vdmos_calibration.json):** 6 devices per switch on the **PV module's verified physical leg, repeated for every device pair** (pv_design.leg_net: 3 x KEMET C4AQ 2.2 uF / 1300 V decoupling at the pins, 15 nH bus, 11.5 nH board + devices, RC damper 2 x 4.99 ohm + 2 x 4.7 nF per pair) - three pairs in parallel: 19.8 uF, 5.0 nH, 3.83 nH, damper 3.33 ohm + 7.0 nF on the 350 uF film bank (ESL 21 nH).  This is a layout requirement: one shared loop of 20-30 nH for six pairs gave 2.0-2.3 kV in the first run of this step.  Off-resistance sweep at the worst case (V_dc = 1050 V = the DC over-voltage trip, I = 450 A = the per-phase over-current trip, bus and board inductance x1.5):

| R_G,off per device (ext) | peak V_DS (V) | overshoot (V) | di/dt, all six (A/ns) | dv/dt (V/ns) | E_off, all six (mJ) | deck |
|---|---|---|---|---|---|---|
| 2.5 ohm | 1491 | 441 | 57.6 | 117 | 2.83 | sim/spice/pcs_commutation_sweep0.cir |
| 5.0 ohm | 1446 | 396 | 51.9 | 83 | 4.43 | sim/spice/pcs_commutation_sweep1.cir |
| 7.5 ohm | 1408 | 358 | 47.1 | 64 | 6.01 | sim/spice/pcs_commutation_sweep2.cir |
| 12.5 ohm | 1352 | 302 | 39.8 | 44 | 9.11 | sim/spice/pcs_commutation_sweep3.cir |

**Chosen: R_G,off 7.5 ohm, R_G,on 8.75 ohm per device** (the smallest of the sweep that holds the limit): worst case 1408 V peak against the 0.85 x 1700 = 1445 V project limit; nominal (950 V, 397 A = 1.2 x 216 A peak + ripple, nominal leg): 1213 V, 59 V/ns, 47.7 A/ns.  E_off at this R_G is 2.18 x the data-sheet curve (simulated) and E_on at R_G,on 8.75 ohm 1.63 x (the data sheet's E-vs-R_G curve, D-057); the device loss at 125 kW / 750 V rises from 1202 to 1390 W, peak efficiency 98.99 % (full load 750 V 98.30 %) - step b already carries both factors (this step runs first).  Decks: sim/spice/pcs_commutation_*.cir.

**Gate drive (the project's channel, gen/gdrv.py, NSI6651ASC):** one channel per switch position - **6 channels for three-wire, 8 for four-wire** (the architecture had 12/16).  Rails **+18 / -3.5 V** (the PV setting for this device, gdrv GATE_V (18, 3.5)); per-device gate resistor and 0.5 ohm Kelvin resistor (R_KS), per-gate Miller clamp FET as the PV rev-5 channel; DESAT 100 ohm + a US1MH string (2 diodes in the PCS preset since PCM-07: the 200 ms overload peak, above) and the short-circuit booster (booster=True).  Six gates per channel: Q_g 468 nC, 0.32 W at 32 kHz (bias secondary budget 0.5 W - one SN6505B transformer per phase with two secondaries); peak gate current 14 A wanted against the NSI6651's 10 A - **add a discrete NPN/PNP push-pull buffer per channel** (two SOT-89 transistors, about 0.3 USD) or split the six devices over two drivers (+6 channels); the buffer is the cheaper answer.  Dead time: **300 ns** in the ePWM dead-band, with the channel's RC + Schmitt stretch on IN- as the hardware minimum and the negative-rail detector (gen/gdrv.py stretch=True, neg_det=True, D-050).  The stretch values of the PV preset (2 gates, no buffer) do NOT carry over: six gates behind a buffer at R_G,off 7.5 ohm turn off more slowly, so a '6 x SG2M040170HJ' preset must be added to gen/gdrv.py and its design_check re-run before the channel is drawn.

**DESAT coordination input (PCM-07, calculated):** at the 200 ms overload peak a device carries 66.2 A; with all six at the hottest junction the model predicts there (161 C, step b) the switch shows **6.22 V** (typical R_DS(on) 94.0 mOhm incl. the Fig. 5 current factor 1.108 / 1.148 at 25 / 175 C), 6.68 V at 175 C; from a -30 C inlet the same overload peaks near 86 C: 4.09 V; a switch of data-sheet-maximum parts 8.09 / 8.69 / 5.31 V (1.2 x 216 A peak + half the 950 V ripple per device; all six at the hottest device's 200 ms junction (k 1.10 model); R_DS(on) Table 4 p4 typical (40 / 88 mOhm at 25 / 175 C, 38 A) along the Fig. 4/6 curve, x the p6 Fig. 5 current factor; 'max' = the 52 mOhm data-sheet maximum at 25 C scaled the same way (no hot maximum is published)).

**Short circuit:** DESAT trips at V_DS set by the gate-drive preset (gen/gdrv.py '6 x SG2M040170HJ': NSI6651 8.5-9.8 V less 100 ohm x I_CHG and the US1M string); PCS-PWR's design check coordinates it with the on-state voltage below; at the 450 A over-current trip the hottest device sits at 6.3 V (no nuisance trip); blanking 150-500 ns, detection to off <= 1.0 us with the booster (a response requirement, not an acceptance).  **Acceptance follows the DAB study's rule DR-05 (release block, risk C2):** in a short V_DS stays at the bus, so the current follows the gate voltage down from the instant it starts falling; the maker must confirm t_SC >= 2 x the energy-equivalent full-current time of the drawn chain and E_SC >= 2 x its fault energy at 1050 V / 150 C start / +18 V (gen/gdrv.py prints both for the '6 x SG2M040170HJ' preset; PCS-PWR design check).  Sichain publishes no withstand: the 2 us is an ASSUMPTION, not a rating - the short circuit is not shown to be covered.

**If IGBTs were used instead (the architecture's choice) the channel would differ:** rails +15 / -8 V; DESAT threshold for V_CE(sat) of 3-4 V at twice rated current with 2-3 us blanking against a 3-10 us withstand (CR Micro / NCE parts state none or 3-10 us); no SiC booster (the driver's soft turn-off suffices, the IGBT limits its own short-circuit current); dead time 1.0-1.5 us; gate resistors 5-10 ohm; gate charge 208-331 nC per 40-80 A device at a 23 V swing.

**Hardware / firmware split (D-050, as on the PV module):** the discrete layer stays and the controller is the second layer.  Discrete: driver DESAT + booster and UVLO -> FLT / RDY; default-off pull-downs; the external stop on the drivers' EN and on the latch; the RC dead-time stretch and the negative-rail detector in every channel; on the control board (PV-CTL as drawn, sheets 04 / 05) window comparators for each phase current (+-450 A), DC over-voltage (1050 V), over-temperature and open probe, one set-dominant latch, the heartbeat watchdog and the AND gating of every PWM, EN and coil line; on the DC port the polarity and precharge-dV interlocks, the hold-off comparator and the port over-current window (gen/port.py interlocks='full', oc_trip=True).  Second layer in the controller's own hardware: CMPSS windows, ADC limit trips, trip zone, dead-band.  Firmware: everything that is sequencing, monitoring or a non-hazard edge case (list in the hand-over section).  What does not carry over from the PV design is listed in step g.

## (f) AC port, DC port, common mode

**AC disconnect:** two 3-pole contactors in series (4-pole for four-wire), relay test before every connection; AC-1 >= 248 A at 60 C, U_i 1000 V, U_imp 8 kV, 24 V DC with economiser (ESTIMATE 20 W pull-in / 4 W hold); CHINT NXC-225 / CJX2-185, Delixi CJX2s-185 class - no data sheet on file (R-06, RFQ).  Relay test (firmware, before every connection): close each contactor alone and read the voltage across the other through the terminal- and C_f-side dividers - a welded pole shows as zero volts.  Two in series because the converter is non-isolated and the disconnection must survive one welded contact (IEC 62109-2 / EN 50549-1 'AC relay automatic checking' as the PMA cites them - clauses from memory).  The contactors make only after synchronisation (|dV| <= 5 %, 5 deg: 107 A peak, step c) and break only at zero current (firmware); with the controller dead the gates are off and only the brief diode-rectifier current flows, inside the AC-1 breaking capacity.  No AC-side hold-off comparator (D-050 keeps it for the DC contactor): after a trip the converter current is zero within microseconds, and a short behind the contactors is fed by the grid at a level only the upstream breaker can clear - a hold-off could not help.  Synchronised closing is firmware: an unsynchronised close rings L2-C_f (1084 A peak) inside the contactor's making capacity - a stressed unit, not a hazard.

**Precharge and start:** from the DC side through the lean port's relay and 220 ohm: C_eq 409.4 uF (bank + leg films), tau 90 ms, within 10 V of 950 V after 0.41 s, 185 J, 4.3 A peak; worst case 450 uF from the 1050 V trip: tau 104 ms, 0.48 s, 248 J; shorted bank 762 J in 0.14 s (charge from 0 to the 1050 V trip with C +10 % and R +5 % (tau_max); shorted bank: 1050 V on R -5 % until the abort at 1.1 tau_max + 30 ms relay release (the lean port's convention, port_spec lean)).  Then the inverter forms the grid voltage on C_f (current-limited), synchronises, runs the relay test, closes.  Grid start (battery empty or disconnected) uses the architecture's six-diode tap for the auxiliary supply only.

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

(2) Switching frequency: the converter's common-mode voltage (441 V peak at 32 kHz, 950 V) is returned locally through C_f and the DC midpoint (step c), leaving 1.8 V peak between the terminals and the DC midpoint; through the battery's earth capacitance that alone drives 1uF: 0.4 A, 5uF: 1.8 A, 20uF: 7.3 A at 32 kHz - so the AC conductors need a common-mode choke of >= 126 uH (2 nanocrystalline cores over the busbars instead of the architecture's two): 1uF: 90 mA, 5uF: 75 mA, 20uF: 73 mA.

**What the product needs (answer):** no transformer and no special modulation beyond the one above.  Three-wire on a TN grid is allowed with (a) the C_f star tied to the DC midpoint, (b) the AC common-mode choke, (c) an installation limit on the battery's capacitance to earth for operation below 680 V (stated above), (d) the RCMU thresholds set per the code; where the battery exceeds it or the grid code forbids the DC-to-earth voltage, the four-wire version (N connected, the N leg holds the zero sequence) or an isolation transformer is the installer's choice.  The battery rack sits at +-V_dc/2 against earth in operation (as the architecture noted): its insulation and IMD must be rated for that.

**DC port = the lean battery port scaled to 250 A:** Hongfa HFE82V-300C/1000 (300 A at 85 C, break-once 1.5 kA; port_spec lean); aR >= 333 A (75 % rule at 230 A continuous, 1000 V DC) - 400 A class, RFQ (Hongfa HPE501 stops at 250 A: R-05); hold-off threshold 1000 A (the contactor stays closed above it and lets the fuse clear - discrete comparator, D-050) and hardware polarity / precharge-dV interlocks in the coil drives; 2 x 200 uOhm in parallel in DC- (port_spec lean): 25 mV at 250 A, 6.3 W; port over-current 400 A and over-voltage 1050 V as ADC limit trips.  The 400 A fuse's slow region (1.5-3 kA, seconds) leaves the upstream requirement on the battery's own breaker (R-05: re-run the port_design coordination with the chosen fuse).

### Operating map (PCM-05)

**Basis:** CALCULATED steady state: V_c = V_g + (R + j w L) I, L = L1 + L2 = 126 uH, R = 16.0 mOhm per phase (one switch of six devices at 150 C + the L1 / L2 copper models); modulation limit 1.1316 (SVPWM linear limit x 0.98) less 0.033 dead-time compensation (2 x 510 ns x 32 kHz, ASSUMED: top of the drawn hardware dead time) and a 5 % current-loop headroom (ASSUMED); grid impedance and harmonics outside L2 not counted; P at the AC terminals.  The rectangular promise 'DC 590-950 V, AC 400 V +-15 %, rated current at any power factor' does not hold: the bridge needs a minimum DC voltage that grows with the AC voltage and with reactive output.  Even at the nominal 400 V it needs 624 V DC to connect and 605-643 V for rated current, so the bottom of the DC range (590-623 V) serves only grids up to about 360-380 V; rated current at any power factor up to 460 V needs 737 V - AC-02's 'full load 600-900 V' at 400 V +-15 % is not reachable with these margins (a requirement decision, not a design fix).  125 kW at 400 V is 180.4 A (rated 180 A), 150 kVA at 400 V is 216.5 A = the 2-min tier, not the 198 A continuous one.

| AC (rated 180 A) | PF 1 inverter | PF 1 rectifier | PF 0, Q > 0 (over-excited, current lags) | PF 0, Q < 0 (under-excited, current leads) | diodes conduct below | closing permissive |
|---|---|---|---|---|---|---|
| 340 V 50 Hz | 539 V | 523 V | 550 V | 511 V | 481 V | 530 V |
| 340 V 60 Hz | 539 V | 523 V | 554 V | 507 V | 481 V | 530 V |
| 360 V 50 Hz | 570 V | 554 V | 581 V | 542 V | 509 V | 562 V |
| 360 V 60 Hz | 570 V | 554 V | 585 V | 539 V | 509 V | 562 V |
| 400 V 50 Hz | 632 V | 617 V | 643 V | 605 V | 566 V | 624 V |
| 400 V 60 Hz | 632 V | 617 V | 647 V | 601 V | 566 V | 624 V |
| 440 V 50 Hz | 695 V | 679 V | 706 V | 667 V | 622 V | 687 V |
| 440 V 60 Hz | 695 V | 679 V | 710 V | 663 V | 622 V | 687 V |
| 460 V 50 Hz | 726 V | 710 V | 737 V | 698 V | 651 V | 718 V |
| 460 V 60 Hz | 726 V | 710 V | 741 V | 695 V | 651 V | 718 V |

**Admissible current (A rms, 50 Hz; capped at 198 A, 150 kVA and the DC limits 137 kW / 230 A):** PF 1 inverter / PF 0 Q > 0 / PF 0 Q < 0 (0 = cannot form the grid voltage at all)

| V_dc | 340 V AC | 360 V AC | 400 V AC | 440 V AC | 460 V AC |
|---|---|---|---|---|---|
| 590 V | 198 / 198 / 198 | 198 / 198 / 198 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 600 V | 198 / 198 / 198 | 198 / 198 / 198 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 650 V | 198 / 198 / 198 | 198 / 198 / 198 | 198 / 198 / 198 | 0 / 0 / 0 | 0 / 0 / 0 |
| 700 V | 198 / 198 / 198 | 198 / 198 / 198 | 198 / 198 / 198 | 180 / 126 / 197 | 0 / 0 / 0 |
| 750 V | 198 / 198 / 198 | 198 / 198 / 198 | 198 / 198 / 198 | 180 / 197 / 197 | 172 / 188 / 188 |
| 800 V | 198 / 198 / 198 | 198 / 198 / 198 | 198 / 198 / 198 | 180 / 197 / 197 | 172 / 188 / 188 |
| 850 V | 198 / 198 / 198 | 198 / 198 / 198 | 198 / 198 / 198 | 180 / 197 / 197 | 172 / 188 / 188 |
| 900 V | 198 / 198 / 198 | 198 / 198 / 198 | 198 / 198 / 198 | 180 / 197 / 197 | 172 / 188 / 188 |
| 950 V | 198 / 198 / 198 | 198 / 198 / 198 | 198 / 198 / 198 | 180 / 197 / 197 | 172 / 188 / 188 |

| overload tier | A rms | kVA at 340 V | kVA at 360 V | kVA at 400 V | kVA at 440 V | kVA at 460 V |
|---|---|---|---|---|---|---|
| 110 % continuous | 198 | 116.6 | 123.5 | 137.2 | 150.9 | 157.8 |
| 120 % 2 min | 216 | 127.2 | 134.7 | 149.6 | 164.6 | 172.1 |
| 200 ms limit (1.2 x 216 A) | 259 | 152.6 | 161.6 | 179.6 | 197.5 | 206.5 |

S is further capped at 150 kVA (AC-02).  **Below the rectification threshold** (V_dc < sqrt(2) V_LL, gates off or the modulation saturated) the body diodes rectify into the battery through L1 + L2 (+ grid): 460 V AC, 590 V DC, stiff grid: 846 A peak, 730 A mean DC; 460 V AC, 590 V DC, SCR 5: 113 A peak, 98 A mean DC; 460 V AC, 620 V DC, stiff grid: 158 A peak, 79 A mean DC; 460 V AC, 620 V DC, SCR 5: 21 A peak, 11 A mean DC; 440 V AC, 590 V DC, stiff grid: 190 A peak, 106 A mean DC; 440 V AC, 590 V DC, SCR 5: 25 A peak, 14 A mean DC; 440 V AC, 620 V DC, stiff grid: 3 A peak, 0 A mean DC; 440 V AC, 620 V DC, SCR 5: 0 A peak, 0 A mean DC (time-stepped diode bridge; not modelled: R, which lowers these currents, and L1 saturation in the stiff-grid case, which raises them).  So the firmware stops in a controlled way while it still has margin (V_dc < 1.05 x sqrt(2) V_LL: current to zero, both AC contactors open at zero current); if V_dc collapses faster, it trips and opens both AC contactors (they break the pulsed current at its zeros) and this current flows for their release, 50 ms ASSUMED - above 387-413 A DC the port's hardware over-current window also opens the DC contactor.  Every V_dc figure above carries the 5 % loop headroom: without it they are 4.8 % lower.

## (g) Cost (catalogue and 5,000 units)

Every line named and priced in sim/out/pcs_design/pcs_costed_bom.csv (and pcs_spec.json 'cost_usd'); the basis of each price is in the row.  Block totals:

| block | catalogue (USD) | 5,000 units (USD) |
|---|---|---|
| POWER | 239 | 202 |
| GATE DRIVE | 51 | 37 |
| LCL FILTER | 523 | 421 |
| SENSING | 60 | 49 |
| DC PORT | 190 | 142 |
| AC PORT | 166 | 134 |
| AUX SUPPLY | 37 | 31 |
| CONTROL | 12 | 11 |
| INTERFACE | 8 | 8 |
| THERMAL | 111 | 85 |
| MECH-ELEC | 13 | 11 |

**Discrete protection layer (D-050, as on the PV module; counts from the PV boards, prices from bom/PV-P75_costed_BOM.csv):** control board 3.91 / 3.67 USD (PV-CTL sheets 04 / 05, 81 parts; four-wire = the PV-P100/110 assembly with the fourth window, +0.50 USD); DC port: hold-off comparator 1.45 USD (replaces a 0.5 USD guess), polarity + precharge-dV interlocks and the over-current window 4.77 / 4.77 USD; per gate-drive channel stretch + negative-rail detector 0.13 / 0.12 USD (7 + 4 parts; the stretch was already inside the PV channel estimate and is now its own line).  Firmware duplicates each trip as the second layer.

**Where the PV discrete layer does not carry over unchanged:** (1) the phase-current windows keep their TLV9024 comparators but move to +-450 A on the Sinomags STK-250HO/4 (open-loop Hall, +-625 A, 3.2 mV/A, step response <= 2 us, PCM-20): the ladder values change, and the trip path (sensor <= 2 us + comparator + latch + driver, timed against the L1 limit in the PCS-CTL design check) runs at di/dt = V_dc/L1 = 8.75 A/us (1050 V / 120 uH) - with the PV's 73 A / 224 uH it was 4.5 A/us; (2) the dead-time stretch needs a new gen/gdrv.py preset for six gates behind a buffer (step e); (3) the AC side has no counterpart of the DC-port interlocks: no hold-off (the converter current is zero after a trip; a short behind the contactors is grid-fed) and no hardware synchronism check (firmware; an unsynchronised close is a stressed unit, not a hazard); (4) grid over / under voltage and frequency, anti-islanding and the residual-current trips are firmware-only by nature (slow, code-dependent), with the RCMU self-test; (5) the second DC port of the PV board does not exist here - one port's interlocks.
| **three-wire total** | **1409 (11.3 USD/kW)** | **1131 (9.0 USD/kW)** |
| four-wire increment | 285 | 230 |
| **four-wire total** | **1694** | **1361** |

**Against the architecture:** three-wire 1409 / 1131 USD against its 1037 / 801 USD (+36 % / +41 %); four-wire 1694 / 1361 against 1194 / 919 USD.  The architecture's figures rest on 42 IGBTs that overheat (step a: 66 needed) and on a 660 uF midpoint bank that its own modulation cannot hold at PF 0 (step d: 25 capacitors per half for any three-level design); corrected for those two (+47 USD of IGBTs and pads, +158 USD of DC link at 5k) its T-type would cost about 1006 USD at 5k on the architect's filter prices (section h prices it with the real inductors).  Evidence behind the 5k figure: 5 % (LCSC breaks, marketplace quote); the rest are assumed factors 0.68-0.85 on estimates - the inductors, the contactors, the fuses, the heatsink and the SiC device price are RFQ items.

## (h) Re-optimisation with the real inductor designs (sim/pcs_tradeoff.py)

**CALCULATED, nothing measured.** Devices, heat sink, PWM and LCL: sim/pcs_design.py (E_on at the drawn R_G,on 8.75 ohm, E_off x2.17 from its step e, L1 ripple in the device currents); L1 / L2 / AC CM choke: sim/magnetics.py (gapped nanocrystalline / amorphous C-cores only - no powder block cores), each candidate's own requirement (L(I) minima at its own peaks, its HF current, its trip).  L1 = the cheapest verified part keeping the peak efficiency >= 98.7 % (98.5 % + 0.2-point model margin). **Acceptance:** peak efficiency >= 98.5 % (AC-02); Tj <= 150 C at 110 % / 45 C (k 1.10, 120 % 2 min and 200 ms as step a) and 100 % rated current at 60 C inlet with the hottest device at 1.30 x the mean (D-057); every grid-current component of the carrier band <= 0.3 % of rated, resonance below f_s/6 (stiff) and above 2 x the current-loop bandwidth (SCR 5); 150 Hz earth current <= 300 mA at 5 uF to earth, carrier-band earth current <= the drawn design's (63 mA rms with 150 uH) at 1-20 uF, the AC CM choke sized per candidate; worst full-load efficiency (600-900 V, PF 1) >= 98.0 % (pcs_design rule); every device blocks <= 0.85 of its rating at the 1050 V DC trip before overshoot (the peak rule's necessary part - two-level overshoot is verified by step e's decks, the T-type's is not simulated).  The 0.67 rule is reported as the FIT per unit, not used as a filter.

| candidate | 5k USD (catalogue) | peak / full-load (750 V) efficiency | filter 5k USD | filter kg | filter W at 125 kW, 750 V | meets the limits |
|---|---|---|---|---|---|---|
| A0 drawn (97 uH, 32 kHz, 450 A) | 1194 (1482) | 98.98 / 98.30 % | 506 | 48 | 538 | yes |
| A1 L1 120 uH | 1131 (1409) | 98.99 / 98.30 % | 443 | 39 | 543 | yes |
| A2 48 kHz | 1156 (1438) | 98.92 / 98.36 % | 437 | 23 | 361 | yes |
| A1xA2 28 kHz, L1 111 uH | 1142 (1421) | 99.01 / 98.34 % | 454 | 40 | 561 | yes |
| A3 min-max everywhere (SVPWM) | 1142 (1422) | 98.96 / 98.30 % | 455 | 40 | 549 | yes |
| A3 AZSPWM1 | 1223 (1517) | 98.98 / 98.30 % | 510 | 51 | 540 | yes |
| A3 NSPWM (AZSPWM1 above 860 V) | 1377 (1698) | 99.30 / 98.56 % | 665 | 36 | 456 | no (leak) |
| A4 trip from protection need | 1194 (1482) | 98.98 / 98.30 % | 506 | 48 | 538 | yes |
| B SiC T-type 1200 V (cross-check) | 1236 (1599) | 99.49 / 98.84 % | 439 | 22 | 320 | no (peak) |
| B rule kept: 1700 V outer | 1279 (1619) | 99.46 / 98.64 % | 439 | 22 | 320 | yes |
| B-IGBT T-type 16 kHz (competitor class) | 1790 (2308) | 99.07 / 98.36 % | 820 | 50 | 491 | no (peak) |
| A* two-level, levers combined | 1131 (1409) | 98.99 / 98.32 % | 443 | 39 | 518 | yes |
| A* two-level, same, sinusoidal PWM (no A3) | 1131 (1409) | 98.99 / 98.30 % | 443 | 39 | 543 | yes |

**Recommended: A1 L1 120 uH**, 1131 USD at 5,000 units (1409 catalogue), 11 USD (1.0 %) below the runner-up A1xA2 28 kHz, L1 111 uH; peak efficiency 98.99 %, filter 443 USD / 39 kg.  The D-053 design corrected (A0) costs 1194 USD.

Same design point, other settings: A* two-level, levers combined (minmax, trip 450 A) 1131 USD; A* two-level, same, sinusoidal PWM (no A3) (policy, trip 450 A) 1131 USD - at this point the L1 is already the cheapest part that passes the thermal screen, so a flatter reference buys nothing and D-053's sinusoidal policy stays.

Cheapest acceptable T-type: B rule kept: 1700 V outer, 1279 USD (+148 USD against the best two-level).  The sweep's resolution is about +-20 USD: the magnetics grid is discrete and the cheapest part that meets the efficiency rules jumps between amorphous and nanocrystalline designs.

pcs_design.py carries this design (A1 L1 120 uH): f_sw 32 kHz, L1 120 uH, C_f 50 uF, L2 6 uH, trip 450 A, modulation policy; inductor cost, mass and loss from sim/magnetics.py.

## Hand-over

**Control engineer (plant and limits; pcs_spec.json 'handover'):**
- LCL: L1 120 uH (R about 2.95 mOhm), C_f 50 uF star (tied to the DC midpoint), L2 6 uH, R_d 2 ohm + C_d 10 uF per phase; resonance stiff 9.42 kHz, SCR 50 3.16 kHz, SCR 20 2.58 kHz, SCR 10 2.33 kHz, SCR 5 2.20 kHz; DC link 409.4 uF across the bus (350 uF bank + 59.4 uF leg films).
- Sampling 64 kHz double update, delay 23 us, dead time 300 ns (1.9 % volt-seconds -> compensate (THDi < 3 %)).
- current loop: L1 current (sensor on the C_f side of L1): resonance 2.2-9.4 kHz is below f_s/6 = 10.7 kHz, where converter-current feedback with 1.5 T_s delay is inherently damped.  The filter was sized for a loop of at least 1.00 kHz (resonance at SCR 5 above twice it): that is the lower edge of the usable crossover band, not a ceiling - the control study (sim/out/pcs_control/pcs_control_spec.json current_loop) finds 1.0-3.25 kHz meeting PM >= 40 deg / GM >= 6 dB from stiff to SCR 5 and takes 1.75 kHz on the stiff grid; a weak grid lowers the closed loop to about 0.3 kHz (SCR 5); resonant terms h5/h7 (h11/h13 fit at no admissible crossover); grid current for PF/THD = i_L1 - C_f dv_Cf/dt; the passive R_d-C_d branch is required (active damping from the C_f voltage fails at the stiff grid in that study)
- pll: on the C_f (or terminal) voltages, 400 V +-15 %, 50/60 Hz, SCR 5..stiff, unbalance and LVRT/HVRT (EN 50549-1 / GB/T 34120)
- modulation: two-level, sinusoidal where m <= 0.98, min-max zero sequence above (V_dc < ~680 V at 400 V, < ~780 V at 460 V)
- midpoint: no control (two-level); firmware plausibility of the half voltages; C_f-star 150 Hz current <= 9 A rms, ripple <= 19 V pp
- grid forming: voltage control on C_f (L1-C_f with the damping branch), current limit 1.2 x 216 A for 200 ms (Tj 161 C at the worst corner, step b), 120 % for 2 min, transitions grid <-> off-grid < 20 ms (Megarevo)
- four wire: neutral leg two-level with L_N = 120 uH, reference = -sum of the phase currents' zero sequence; 100 Hz battery current of single-phase load (~39 A rms at 750 V) is an installation item

**Board designers:**
- power stage: 3 two-level legs (4 for four-wire): 6 x SG2M040170HJ per switch (TO-247-4L, Kelvin source), Al2O3 pads on one earthed section per leg; per device pair 3 x 2.2 uF / 1300 V film at the pins + RC damper (2 x 4.7 nF 2 kV C0G, 6 x 15 ohm 2512); DC link 5 + 5 x C3D1U147 in two series halves, midpoint to the C_f star
- gate drive: 6 channels (8 four-wire): NSI6651ASC + NPN/PNP buffer per channel driving 6 gates, R_G,on 8.75 / R_G,off 7.5 ohm per device, R_KS 0.5 ohm, per-gate clamp FET, DESAT 100 ohm + 2 x US1MH (PCM-07), booster, RC dead-time stretch and negative-rail detector (stretch=True, neg_det=True - new '6 x SG2M040170HJ' preset needed), rails +18 / -3.5 V; bias: one SN6505B transformer per phase (2 secondaries); default-off pull-downs; EN from the external stop (wired-AND); FLT/RDY to the latch and the trip zone (D-050)
- control board: PV-CTL as drawn (gen/pv_ctrl.py), PV-P75 assembly for three-wire, PV-P100/110 assembly for four-wire: discrete window comparators for each phase current (ladders re-valued for +-450 A on the STK-250HO/4), DC over-voltage 1050 V, over-temperature and open probe, the set-dominant latch, heartbeat watchdog, AND gating of 6 (8) PWM, EN, K_DC, K_PRE, K_AC1 and K_AC2 (a spare LVC08 gate); the controller's CMPSS / ADC limits / trip zone as the second layer
- sensing, phase current: 3 (4) Sinomags STK-250HO/4 (open-loop Hall, I_PN 250 A, +-625 A, 3.2 mV/A around its Uref pin, 200 kHz, <= 2 us step response; pcs_spec phase_current_sensor, PCM-20) on the C_f side of L1, the busbar through its aperture; windows at +-450 A
- sensing, grid voltage: terminal and C_f nodes, L1-L3 (+N), dividers to DC-: signal V_dc/2 +-375 V peak + 20 % surge headroom
- sensing, dc: V_DC+ - V_DC-, V_mid, terminal (bipolar, polarity), shunt 2 x 200 uOhm (25 mV at 250 A), ADC limit trips 400 A / 1050 V
- sensing, residual current: type-B fluxgate over L1-L3 (+N), 30 mA resolution, 1.25 A continuous range
- sensing, temperatures: NTC: 3 (4) heatsink sections, 3 (4) L1, DC link, inlet
- ports: DC: HFE82V-300C/1000, 2 x aR 400 A, precharge 220 ohm, gen/port.py lean_port interlocks='full', oc_trip=True (hold-off, polarity and precharge-dV interlocks, over-current window, all discrete); AC: 2 x 3-pole contactor in series (4-pole four-wire), coil drivers with economiser gated by the latch, type II SPD, CM choke >= 150 uH (3 nanocrystalline cores)

**Firmware requirements this design relies on (D-050: firmware duplicates every hardware trip and takes sequencing, monitoring and non-hazard edge cases):**
- firmware practice adopted from the Wolfspeed firmware cross-check (docs/requirements/REFERENCE-LESSONS.md section 6, R-WS-1..9, 2026-10-05): range-checked bus/service inputs (no writable switching frequency or dead time in operation); ramp to zero and stop on communication loss (grid-following: fallback to zero power; grid-forming keeps forming); lock-out after the third hazard trip of a class within 10 min (assumed) and at once when hardware and firmware both see an over-voltage; explicit recovery threshold, dwell and restart-rate limit per non-latched limit; every exception path forces the one-shot trip and stops the heartbeat, clock-fail and emulation-stop trips enabled; the start-up self-test fires each fault line alone and the 10 ms read-back covers the trip routing; no bus-reachable mode disables a protection, parameter writes only in SERVICE with authorisation and timeout; redundant range-checked calibration whose failed load blocks the start
- second layer of every discrete trip (D-050): CMPSS phase windows +-450 A (digital filter <= 0.5 us), ADC limits I_dc 400 A and V_dc 1050 V, heatsink / L1 over-temperature from the NTCs, the DC-port polarity and precharge-dV conditions re-checked before any coil command, trip zone forcing all PWM low; the hardware latch is cleared by firmware only with no source active
- start-up self-test of every trip path, hardware and controller (inject through the ladders / DACs, read the latch and FLT/RDY), heartbeat to the discrete watchdog, read-back and lock of PWM, dead-band (300 ns, the gate-drive stretch is the floor) and trip configuration
- sequencing: DC precharge (polarity, |V_bank - V_bat| <= 10 V - also enforced in hardware), discharge through the precharge path on stop; insulation test (IMD) before every connection with the contactors open; inverter forms the grid voltage on C_f, synchronises (|dV| <= 5 %, 5 deg), runs the relay test (each contactor alone, voltage across the open one), closes; opens only at zero current
- grid protection EN 50549-1 / GB/T 34120: U/f windows and times, ROCOF / vector shift, active anti-islanding where IEC 62116 applies, LVRT / HVRT with reactive current, P(f), Q(U), cos phi(P) (settings by country)
- residual current (type-B sensor): 30 / 60 / 150 mA step trips within 0.3 / 0.15 / 0.04 s and the continuous limit per code (from memory - verify), sensor self-test
- current limit 1.2 x 216 A for <= 200 ms then trip; 120 % for <= 2 min; device thermal model; inlet-temperature derating (fan limit 60 C); fan speed control; open / shorted NTC plausibility
- monitoring and non-hazard edge cases: dead-time compensation, the modulation policy (sinusoidal where m <= 0.98), half-voltage plausibility, varistor and contactor feedback, unsynchronised-close prevention (a stressed unit, not a hazard: no extra hardware)
- upper DC-link half (VB - VA) and half imbalance: VB - VA >= 540 V (527-553 V with 0.6 % per channel, ASSUMED) = the hardware VA trip's nominal; VA < 0.25 VB or VB - VA < 0.25 VB (a shorted half); |VA - VB/2| > 0.07 VB for 1 s (the worst static split of +/-10 % halves is 0.05 VB) - ADC VA (ADC-A) and VB (ADC-B), converted together every <= 10 us, difference in the outer loop -> trip: one-shot on all ePWM, K_B_M, K_A_M and K_AC2_M low (DC contactor releases in <= 10 ms unless HOLD), cause logged, no automatic restart; slow imbalance -> controlled stop, then the same
- modulation-index limiter and operating map: pcs_spec operating_map (5 % loop headroom kept); e.g. rated current at PF 0, Q > 0 needs 643 V DC at 400 V, 737 V at 460 V - control ISR: m = sqrt(2)|V_c*| / (V_dc/2) clamped at 1.0990 (0.98 x 2/sqrt(3) less 2 t_d f_sw dead-time compensation); current references held inside the operating map's admissible current at the measured V_dc / V_ac / f (P or Q priority by setting) before the clamp is reached
- AC contactor closing permissive: V_dc >= 1.02 x sqrt(2) x V_LL,rms(measured) + 10 V and >= the map's V_dc for the commanded P / Q: 624 V at 400 V, 718 V at 460 V (no load) - VB against the line-to-line peak of VG1-3 (terminal side) before K_A_M / K_AC2_M go high - through the relay test and the final close; otherwise no close (the bridge would rectify into the battery)
- V_dc below the rectification threshold while running: meanwhile the body-diode rectifier drives 846 A peak / 730 A mean DC (460 V AC on 590 V, stiff grid, L1 saturation and R not modelled; 113 / 98 A at SCR 5) for <= 50 ms (AC contactor release, ASSUMED); above 387-413 A DC the port window opens the DC contactor first (<= 10 ms release) - outer loop: V_dc < 1.05 x sqrt(2) x V_LL,rms(measured) (the map's no-load limit without the loop headroom) -> controlled stop (current to zero within one grid period, both AC contactors open at zero current); V_dc < sqrt(2) x V_LL,rms + 5 V (the body diodes conduct) -> one-shot on all ePWM, K_A_M and K_AC2_M low (both break the pulsed diode current at its zeros), K_B kept unless the port's hardware over-current window (387-413 A) opens it
- current limit and overload tiers: 110 % continuous 198 A = 117 / 137 / 158 kVA at 340 / 400 / 460 V; 120 % 2 min 216 A = 127 / 150 / 172 kVA at 340 / 400 / 460 V; 200 ms limit (1.2 x 216 A) 259 A = 153 / 180 / 206 kVA at 340 / 400 / 460 V; at 60 C inlet: 120 % 2 min 216 A, 200 ms 246 A - rms of IL1-3 over each grid period, one timer / I2t per tier; reference limited to min(tier, 150 kVA / (sqrt(3) V_ac), the operating map); inlet above 45 C: derated tiers
- contactor coil sequencing (live 24 V budget, PCS-PWR): settled = pull-in window (AC coil module: ASSUMED <= 100 ms) + 100 ms; budget and limits in the PCS-PWR design check - K_PRE_M, K_B_M, K_A_M, K_AC2_M: never two coils pulling in at once; the DC contactor pulls in at the end of the precharge with the gates idle; the second AC contactor only after the first has settled on its economiser; a coil that does not reach its state (relay test VG / VC, the bank following the terminal) is released and retried after >= 10 s (ASSUMED: coil module and aux recovery), at most 3 attempts per start, then lock-out with the cause logged until a reset; a welded contact found by the relay test locks out at once; the DC precharge keeps the port's 3 attempts / 30 s lock-out (port_spec precharge)

## What is not verified (and where it would change the design)

- Nothing is measured: every loss, temperature, overshoot and spectrum here is calculated from data-sheet curves (read by eye, +-5 %) or simulated with fitted VDMOS models.
- Sichain SG2M040170HJ: no public price (4.07 USD is the PV module's estimate), no qualification statement, no short-circuit withstand (2 us assumed), no cosmic-ray data (the 0.67 rule is applied to its 1700 V rating by scaling Wolfspeed's 1200 V curve). The qualified fallback of the PV module (Microchip MSC035SMA170B4, 39 USD) does not fit this product's budget at 36 devices.
- Paralleling six TO-247 per switch: sharing k = 1.10 needs the R_DS(on) / V_GS(th) acceptance rule (section b) and the per-pair decoupling layout; both are requirements, not results.  The commutation decks use the PV leg scaled x3 - re-run with the extracted layout.
- Inductors, contactors, the 400 A aR fuse, the heatsink, the film capacitors and the CM cores have no quotes; 95 % of the 5,000-unit money is an assumed factor; the inductor prices are sim/magnetics.py's material + labour model (core and conductor USD/kg ESTIMATES), and its search covers gapped nanocrystalline / amorphous C-cores only - no powder block cores.
- Standards: EN 50549-1, IEC 62109-2, IEEE 1547 (the harmonic limits used), IEC 62116, GB/T 34120 - from memory, texts not on file.
- Battery capacitance to earth (1-20 uF range assumed) decides the three-wire TN installation limit below 680 V.
- Control: current loop, PLL, grid forming, four-wire neutral control and the THDi < 3 % at rated power are not simulated here (ARCHITECTURE-PCS section 9 items 3-7).
- Three-level modules (HIITIO): data sheets without short-circuit rating or qualification; prices indicative only.
- Fan AFB1224SHE-F00 is rated -10..+60 C: below -10 C start and at 60 C inlet it is outside / at its limit (PV R-05).
