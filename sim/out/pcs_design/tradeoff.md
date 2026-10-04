# PCS-P125 power stage and filter - re-optimisation with the real inductors (sim/pcs_tradeoff.py)

**CALCULATED, nothing measured.** Devices, heat sink, PWM and LCL: sim/pcs_design.py (E_on at the drawn R_G,on 8.75 ohm, E_off x2.17 from its step e, L1 ripple in the device currents); L1 / L2 / AC CM choke: sim/magnetics.py (gapped nanocrystalline / amorphous C-cores only - no powder block cores), each candidate's own requirement (L(I) minima at its own peaks, its HF current, its trip).  L1 = the cheapest verified part keeping the peak efficiency >= 98.7 % (98.5 % + 0.2-point model margin).

**Acceptance:** peak efficiency >= 98.5 % (AC-02); Tj <= 150 C at 110 % / 45 C (k 1.10, 120 % 2 min and 200 ms as step a) and 100 % rated current at 60 C inlet with the hottest device at 1.30 x the mean (D-057); every grid-current component of the carrier band <= 0.3 % of rated, resonance below f_s/6 (stiff) and above 2 x the current-loop bandwidth (SCR 5); 150 Hz earth current <= 300 mA at 5 uF to earth, carrier-band earth current <= the drawn design's (63 mA rms with 150 uH) at 1-20 uF, the AC CM choke sized per candidate; worst full-load efficiency (600-900 V, PF 1) >= 98.0 % (pcs_design rule); every device blocks <= 0.85 of its rating at the 1050 V DC trip before overshoot (the peak rule's necessary part - two-level overshoot is verified by step e's decks, the T-type's is not simulated).  The 0.67 rule is reported as the FIT per unit, not used as a filter.

| candidate | devices | f_sw | L1 / C_f / L2 | trip / unsat. (A) | 5k USD (cat) | filter 5k USD | filter kg | filter W 125 kW 750 V | peak / full eta | Tj 45 C, 60 C k1.3 current | DC film / half | L1 part | FIT 950 V | accept |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 drawn (97 uH, 32 kHz, 450 A) | 36 | 32 | 97 / 50 / 6 | 450 / 450 | **1194** (1482) | 506 | 48 | 538 | 98.98 / 98.30 | 123 C, 100 % | 5 | amor alfoil 0.8 mm N16, 14.5 kg, 121 USD, 168 W | 0.16 | yes |
| A1 L1 60 uH | 36 | 32 | 60 / 50 / 6 | 450 / 464 | **1192** (1479) | 505 | 28 | 383 | 99.11 / 98.43 | 122 C, 100 % | 5 | nano alfoil 0.8 mm N12, 7.5 kg, 118 USD, 116 W | 0.16 | yes |
| A1 L1 80 uH | 36 | 32 | 80 / 50 / 6 | 450 / 450 | **1194** (1482) | 506 | 28 | 424 | 99.10 / 98.40 | 123 C, 100 % | 5 | nano alfoil 0.8 mm N16, 7.8 kg, 121 USD, 130 W | 0.16 | yes |
| A1 L1 120 uH | 36 | 32 | 120 / 50 / 6 | 450 / 450 | **1131** (1409) | 443 | 39 | 543 | 98.99 / 98.30 | 123 C, 100 % | 5 | amor alfoil 0.8 mm N20, 11.6 kg, 103 USD, 170 W | 0.16 | yes |
| A1 L1 150 uH | 36 | 32 | 150 / 30 / 10 | 450 / 450 | **1165** (1451) | 478 | 44 | 520 | 99.01 / 98.31 | 123 C, 100 % | 5 | amor alfoil 0.8 mm N22, 11.8 kg, 105 USD, 162 W | 0.16 | yes |
| A2 16 kHz | 36 | 16 | 194 / 75 / 15 | 450 / 450 | **1202** (1499) | 515 | 43 | 563 | 99.19 / 98.49 | 106 C, 105 % | 5 | amor alfoil 0.8 mm N34, 11.2 kg, 105 USD, 176 W | 0.16 | yes |
| A2 24 kHz | 36 | 24 | 130 / 75 / 6 | 450 / 450 | **1092** (1365) | 404 | 30 | 528 | 99.09 / 98.42 | 115 C, 105 % | 5 | amor alfoil 0.8 mm N26, 8.6 kg, 85 USD, 165 W | 0.16 | yes |
| A2 48 kHz | 42 | 48 | 65 / 30 / 6 | 450 / 450 | **1156** (1438) | 437 | 23 | 361 | 98.92 / 98.36 | 121 C, 100 % | 5 | nano alfoil 0.8 mm N16, 6.3 kg, 103 USD, 109 W | 0.18 | yes |
| A1xA2 20 kHz, L1 117 uH | 36 | 20 | 117 / 100 / 6 | 450 / 450 | **1120** (1397) | 433 | 33 | 504 | 99.12 / 98.49 | 110 C, 105 % | 5 | amor alfoil 0.8 mm N20, 9.4 kg, 89 USD, 157 W | 0.16 | yes |
| A1xA2 20 kHz, L1 155 uH | 36 | 20 | 155 / 100 / 6 | 450 / 450 | **1137** (1418) | 449 | 35 | 482 | 99.16 / 98.50 | 111 C, 105 % | 5 | amor alfoil 0.8 mm N26, 10.0 kg, 95 USD, 149 W | 0.16 | yes |
| A1xA2 20 kHz, L1 194 uH | 36 | 20 | 194 / 100 / 6 | 450 / 450 | **1159** (1447) | 472 | 38 | 549 | 99.16 / 98.45 | 111 C, 105 % | 5 | amor alfoil 0.8 mm N34, 11.2 kg, 105 USD, 172 W | 0.16 | yes |
| A1xA2 20 kHz, L1 233 uH | 36 | 20 | 233 / 50 / 15 | 450 / 450 | **1209** (1504) | 522 | 48 | 550 | 99.17 / 98.45 | 111 C, 105 % | 5 | amor alfoil 0.8 mm N24, 13.1 kg, 115 USD, 171 W | 0.16 | yes |
| A1xA2 24 kHz, L1 97 uH | 36 | 24 | 97 / 75 / 6 | 450 / 450 | **1120** (1397) | 433 | 35 | 530 | 99.06 / 98.42 | 114 C, 105 % | 5 | amor alfoil 0.8 mm N20, 9.8 kg, 92 USD, 165 W | 0.16 | yes |
| A1xA2 24 kHz, L1 162 uH | 36 | 24 | 162 / 75 / 6 | 450 / 450 | **1126** (1406) | 439 | 35 | 481 | 99.12 / 98.45 | 115 C, 105 % | 5 | amor alfoil 0.8 mm N28, 10.2 kg, 96 USD, 149 W | 0.16 | yes |
| A1xA2 24 kHz, L1 194 uH | 36 | 24 | 194 / 30 / 15 | 450 / 450 | **1172** (1463) | 485 | 42 | 540 | 99.11 / 98.40 | 115 C, 105 % | 5 | amor alfoil 0.8 mm N34, 11.2 kg, 105 USD, 168 W | 0.16 | yes |
| A1xA2 28 kHz, L1 83 uH | 36 | 28 | 83 / 75 / 6 | 450 / 450 | **1207** (1497) | 520 | 49 | 576 | 98.98 / 98.33 | 119 C, 100 % | 5 | amor alfoil 0.8 mm N14, 14.8 kg, 123 USD, 181 W | 0.16 | yes |
| A1xA2 28 kHz, L1 111 uH | 36 | 28 | 111 / 75 / 6 | 450 / 450 | **1142** (1421) | 454 | 40 | 561 | 99.01 / 98.34 | 119 C, 100 % | 5 | amor alfoil 0.8 mm N18, 11.8 kg, 104 USD, 176 W | 0.16 | yes |
| A1xA2 28 kHz, L1 139 uH | 36 | 28 | 139 / 30 / 15 | 450 / 450 | **1183** (1472) | 495 | 45 | 579 | 99.03 / 98.32 | 119 C, 100 % | 5 | amor alfoil 0.8 mm N22, 12.3 kg, 108 USD, 181 W | 0.16 | yes |
| A1xA2 28 kHz, L1 167 uH | 36 | 28 | 167 / 30 / 15 | 450 / 450 | **1186** (1476) | 499 | 47 | 493 | 99.06 / 98.39 | 119 C, 100 % | 5 | amor alfoil 0.8 mm N22, 12.8 kg, 112 USD, 152 W | 0.16 | yes |
| A3 min-max everywhere (SVPWM) | 36 | 32 | 97 / 50 / 6 | 450 / 450 | **1142** (1422) | 455 | 40 | 549 | 98.96 / 98.30 | 123 C, 100 % | 5 | amor alfoil 0.8 mm N18, 11.8 kg, 104 USD, 172 W | 0.16 | yes |
| A3 AZSPWM1 | 36 | 32 | 97 / 30 / 15 | 450 / 450 | **1223** (1517) | 510 | 51 | 540 | 98.98 / 98.30 | 123 C, 100 % | 8 | amor alfoil 0.8 mm N16, 14.5 kg, 121 USD, 168 W | 0.16 | yes |
| A3 NSPWM (AZSPWM1 above 860 V) | 36 | 32 | 97 / 30 / 15 | 450 / 450 | **1377** (1698) | 665 | 36 | 456 | 99.30 / 98.56 | 123 C, 100 % | 8 | nano litz 0.2 mm N14, 9.6 kg, 173 USD, 141 W | 0.16 | no: leak |
| A4 trip from protection need | 36 | 32 | 97 / 50 / 6 | 425 / 434 | **1194** (1482) | 506 | 48 | 538 | 98.98 / 98.30 | 123 C, 100 % | 5 | amor alfoil 0.8 mm N16, 14.5 kg, 121 USD, 168 W | 0.16 | yes |
| B SiC T-type 1200 V (cross-check) | 72 | 32 | 49 / 50 / 6 | 465 / 483 | **1236** (1599) | 439 | 22 | 320 | 99.49 / 98.84 | 111 C, 105 % | 5 | nano alfoil 0.8 mm N10, 5.7 kg, 96 USD, 95 W | 66.25 | no: peak |
| B rule kept: 1700 V outer | 72 | 32 | 49 / 50 / 6 | 465 / 483 | **1279** (1619) | 439 | 22 | 320 | 99.46 / 98.64 | 122 C, 100 % | 5 | nano alfoil 0.8 mm N10, 5.7 kg, 96 USD, 95 W | 0.14 | yes |
| B-IGBT T-type 16 kHz (competitor class) | 126 | 16 | 97 / 30 / 47 | 465 / 474 | **1607** (2091) | 636 | 50 | 470 | 98.99 / 98.37 | 113 C, 100 % | 5 | amor alfoil 0.8 mm N16, 10.7 kg, 96 USD, 100 W | 357.69 | no: peak |
| A* two-level, levers combined | 36 | 24 | 130 / 75 / 6 | 450 / 450 | **1092** (1365) | 404 | 30 | 509 | 99.09 / 98.43 | 115 C, 105 % | 5 | amor alfoil 0.8 mm N26, 8.6 kg, 85 USD, 158 W | 0.16 | yes |
| A* two-level, same, sinusoidal PWM (no A3) | 36 | 24 | 130 / 75 / 6 | 450 / 450 | **1092** (1365) | 404 | 30 | 528 | 99.09 / 98.42 | 115 C, 105 % | 5 | amor alfoil 0.8 mm N26, 8.6 kg, 85 USD, 165 W | 0.16 | yes |

**Recommendation: A2 24 kHz** - 1092 USD at 5,000 units (1365 catalogue), 29 USD below the runner-up (A1xA2 20 kHz, L1 117 uH).

## A1 L1 inductance and A2 switching frequency

| candidate | L1 uH | ripple pp 950 V (A) | trip / unsat. (A) | L1 part (5k USD, kg, W at 180 A 750 V) | devices W 125 kW 750 V | C_f A per can | DC half-bank A / caps | L2 uH | resonance stiff / SCR 5 (kHz) vs f_s/6, 2 BW | CM choke cores | 5k USD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 drawn (97 uH, 32 kHz, 450 A) | 97 | 76 | 450 / 450 | amor alfoil 0.8 mm: 121, 14.5, 168 | 1386 | 8.8 | 130 / 5 | 6 | 9.5 / 2.4 vs 10.7, 2.0 | 3 | 1194 |
| A1 L1 60 uH | 60 | 124 | 450 / 464 | nano alfoil 0.8 mm: 118, 7.5, 116 | 1379 | 14.1 | 134 / 5 | 6 | 9.6 / 3.0 vs 10.7, 2.0 | 4 | 1192 |
| A1 L1 80 uH | 80 | 93 | 450 / 450 | nano alfoil 0.8 mm: 121, 7.8, 130 | 1383 | 10.6 | 131 / 5 | 6 | 9.5 / 2.6 vs 10.7, 2.0 | 3 | 1194 |
| A1 L1 120 uH | 120 | 62 | 450 / 450 | amor alfoil 0.8 mm: 103, 11.6, 170 | 1389 | 7.2 | 129 / 5 | 6 | 9.4 / 2.2 vs 10.7, 2.0 | 2 | 1131 |
| A1 L1 150 uH | 150 | 49 | 450 / 450 | amor alfoil 0.8 mm: 105, 11.8, 162 | 1393 | 5.7 | 129 / 5 | 10 | 9.5 / 2.6 vs 10.7, 2.0 | 3 | 1165 |
| A2 16 kHz | 194 | 76 | 450 / 450 | amor alfoil 0.8 mm: 105, 11.2, 176 | 1121 | 6.1 | 130 / 5 | 15 | 4.9 / 1.5 vs 5.3, 1.0 | 6 | 1202 |
| A2 24 kHz | 130 | 76 | 450 / 450 | amor alfoil 0.8 mm: 85, 8.6, 165 | 1253 | 6.1 | 130 / 5 | 6 | 7.7 / 1.7 vs 8.0, 1.5 | 3 | 1092 |
| A2 48 kHz | 65 | 76 | 450 / 450 | nano alfoil 0.8 mm: 103, 6.3, 109 | 1487 | 8.7 | 130 / 5 | 6 | 12.4 / 3.7 vs 16.0, 3.0 | 2 | 1156 |
| A1xA2 20 kHz, L1 117 uH | 117 | 102 | 450 / 450 | amor alfoil 0.8 mm: 89, 9.4, 157 | 1186 | 6.1 | 132 / 5 | 6 | 6.7 / 1.6 vs 6.7, 1.2 | 4 | 1120 |
| A1xA2 20 kHz, L1 155 uH | 155 | 76 | 450 / 450 | amor alfoil 0.8 mm: 95, 10.0, 149 | 1187 | 4.8 | 130 / 5 | 6 | 6.6 / 1.4 vs 6.7, 1.2 | 4 | 1137 |
| A1xA2 20 kHz, L1 194 uH | 194 | 61 | 450 / 450 | amor alfoil 0.8 mm: 105, 11.2, 172 | 1188 | 4.0 | 129 / 5 | 6 | 6.6 / 1.3 vs 6.7, 1.2 | 3 | 1159 |
| A1xA2 20 kHz, L1 233 uH | 233 | 51 | 450 / 450 | amor alfoil 0.8 mm: 115, 13.1, 171 | 1189 | 6.1 | 129 / 5 | 15 | 6.0 / 1.7 vs 6.7, 1.2 | 4 | 1209 |
| A1xA2 24 kHz, L1 97 uH | 97 | 102 | 450 / 450 | amor alfoil 0.8 mm: 92, 9.8, 165 | 1251 | 7.9 | 132 / 5 | 6 | 7.7 / 2.0 vs 8.0, 1.5 | 4 | 1120 |
| A1xA2 24 kHz, L1 162 uH | 162 | 61 | 450 / 450 | amor alfoil 0.8 mm: 96, 10.2, 149 | 1255 | 5.0 | 129 / 5 | 6 | 7.6 / 1.6 vs 8.0, 1.5 | 3 | 1126 |
| A1xA2 24 kHz, L1 194 uH | 194 | 51 | 450 / 450 | amor alfoil 0.8 mm: 105, 11.2, 168 | 1257 | 5.9 | 129 / 5 | 15 | 7.8 / 2.3 vs 8.0, 1.5 | 4 | 1172 |
| A1xA2 28 kHz, L1 83 uH | 83 | 102 | 450 / 450 | amor alfoil 0.8 mm: 123, 14.8, 181 | 1316 | 7.9 | 132 / 5 | 6 | 7.8 / 2.1 vs 9.3, 1.8 | 3 | 1207 |
| A1xA2 28 kHz, L1 111 uH | 111 | 76 | 450 / 450 | amor alfoil 0.8 mm: 104, 11.8, 176 | 1319 | 6.1 | 130 / 5 | 6 | 7.7 / 1.9 vs 9.3, 1.8 | 2 | 1142 |
| A1xA2 28 kHz, L1 139 uH | 139 | 61 | 450 / 450 | amor alfoil 0.8 mm: 108, 12.3, 181 | 1322 | 7.0 | 129 / 5 | 15 | 7.9 / 2.7 vs 9.3, 1.8 | 4 | 1183 |
| A1xA2 28 kHz, L1 167 uH | 167 | 51 | 450 / 450 | amor alfoil 0.8 mm: 112, 12.8, 152 | 1324 | 5.9 | 129 / 5 | 15 | 7.8 / 2.5 vs 9.3, 1.8 | 3 | 1186 |
| A* two-level, levers combined | 130 | 76 | 450 / 450 | amor alfoil 0.8 mm: 85, 8.6, 158 | 1253 | 6.0 | 130 / 5 | 6 | 7.7 / 1.7 vs 8.0, 1.5 | 3 | 1092 |
| A* two-level, same, sinusoidal PWM (no A3) | 130 | 76 | 450 / 450 | amor alfoil 0.8 mm: 85, 8.6, 165 | 1253 | 6.1 | 130 / 5 | 6 | 7.7 / 1.7 vs 8.0, 1.5 | 3 | 1092 |

## A3 reduced common-mode PWM (32 kHz, 97 uH)

| modulation | L1 HF 950 V: DM / CM / per phase (A rms) | CM into the DC midpoint (A rms) | CM at f_sw (V pk) | devices W 125 kW 750 V | carrier band max (% of rated) | 150 Hz CM (V rms) -> mA at 5 uF | CM choke cores | L1 5k USD | DC film per half | 5k USD |
|---|---|---|---|---|---|---|---|---|---|---|
| policy | 6.0 / 16.1 / 17.2 | 48 | 441 | 1386 | 0.144 | 55 -> 259 | 3 | 121 | 5 | 1194 |
| minmax | 5.6 / 15.8 / 16.7 | 47 | 432 | 1387 | 0.121 | 55 -> 259 | 3 | 104 | 5 | 1142 |
| policy + inverted middle carrier | 17.0 / 2.5 / 17.2 | 8 | 58 | 1386 | 0.278 | 55 -> 259 | 1 | 121 | 8 | 1223 |
| dpwm1 + inverted middle carrier | 17.0 / 2.5 / 17.2 | 8 | 58 | 1137 | 0.278 | 98 -> 462 | 1 | 173 | 8 | 1377 |

With the C_f star on the DC midpoint each L1 sees its own leg against the midpoint, so its ripple is set by that leg's reference alone: moving the carrier of the middle phase (AZSPWM1) shifts current from the common-mode path to the differential path but leaves the per-phase HF current, the core flux and the L1 unchanged; it cuts the common-mode current into the DC midpoint and the earth current (fewer CM-choke cores).  Only a different REFERENCE changes L1: min-max (SVPWM) and DPWM1 (NSPWM) flatten the leg reference and lower the ripple, at the price of a 150 Hz zero sequence at every DC voltage (earth leakage through the battery's capacitance; limit 300 mA at 5 uF).  NSPWM has no zero states only for m >= 0.77 (about 860 V DC at 400 V AC); above, it falls back to AZSPWM1.  All of it is firmware (ePWM action qualifiers).

## A4 hardware over-current window

Protection needs: the window must clear the largest operating peak - the 200 ms overload, 1.2 x 216 A = 259 A rms = 367 A peak, plus half the ripple (38 A at 97 uH, 950 V) = 405 A - with 5 % for the TMR gain and ladder tolerances: 425 A.  The core must then carry the trip plus the overshoot in the 1.5 us trip path (V_dc,trip / 2 across 0.9 L1: 9 A) unsaturated: 434 A against the drawn 450 A.  So +-450 A is needed within 4 %; the core shrinks by about that (L x I_trip sets the gapped C-core's section), L1 121 -> 121 USD at 5k.  A lower window would trip the unit inside its own 200 ms overload.  The drawn 450 A had no allowance for the trip-path overshoot - at 97 uH that is covered only because 450 A sits 25 A above the need.  Powder block cores (FeSiAl / high flux, not in the magnetics search) roll off softly and may be allowed to sit at 50 % of L at the trip instead of below saturation - that is the case where powder could be cheaper; it needs a powder-core pass.

## B SiC T-type and the 0.67 rule

- **B SiC T-type 1200 V (cross-check)**: 72 devices (T1 6 x SG2M035120LJ, T4 6 x SG2M035120LJ, T2 6 x SG2M035120LJ, T3 6 x SG2M035120LJ), 12 channels, 5 bias supplies; L1 49 uH (nano alfoil 0.8 mm, 5.7 kg, 96 USD); two-level corner (where min-max PWM alone would ripple the 5 + 5 bank's midpoint by more than 60 V pp): V_dc 590-950 V at |PF angle| 15-165 deg; there the L1 sees full-V_dc steps (ripple 153 A pp, trip 465 A); balancing the midpoint actively instead (ideal carrier-based control) would put up to 232 V rms of 150 Hz on the battery (1095 mA at 5 uF); devices block 0.88 of their rating at the 1050 V trip before overshoot (rule 0.85); FIT 66.3 at 950 V; 1236 USD at 5k, peak 99.49 %; fails: peak.
- **B rule kept: 1700 V outer**: 72 devices (T1 6 x SG2M040170HJ, T4 6 x SG2M040170HJ, T2 6 x SG2M035120LJ, T3 6 x SG2M035120LJ), 12 channels, 5 bias supplies; L1 49 uH (nano alfoil 0.8 mm, 5.7 kg, 96 USD); two-level corner (where min-max PWM alone would ripple the 5 + 5 bank's midpoint by more than 60 V pp): V_dc 590-950 V at |PF angle| 15-165 deg; there the L1 sees full-V_dc steps (ripple 153 A pp, trip 465 A); balancing the midpoint actively instead (ideal carrier-based control) would put up to 232 V rms of 150 Hz on the battery (1095 mA at 5 uF); devices block 0.62 of their rating at the 1050 V trip before overshoot (rule 0.85); FIT 0.1 at 950 V; 1279 USD at 5k, peak 99.46 %; meets every acceptance limit.
- **B-IGBT T-type 16 kHz (competitor class)**: 126 devices (T1 14 x CRG40T120BK3SD, T4 14 x CRG40T120BK3SD, T2 7 x CRG50T60AK3SD, T3 7 x CRG50T60AK3SD), 12 channels, 5 bias supplies; L1 97 uH (amor alfoil 0.8 mm, 10.7 kg, 96 USD); two-level corner (where min-max PWM alone would ripple the 5 + 5 bank's midpoint by more than 60 V pp): V_dc 590-950 V at |PF angle| 15-165 deg; there the L1 sees full-V_dc steps (ripple 153 A pp, trip 465 A); balancing the midpoint actively instead (ideal carrier-based control) would put up to 229 V rms of 150 Hz on the battery (1078 mA at 5 uF); devices block 0.89 of their rating at the 1050 V trip before overshoot (rule 0.85); FIT 357.7 at 950 V; 1607 USD at 5k, peak 98.99 %; fails: peak.

**Cost of the 0.67 rule:** two-level - none: 1200 V devices at the 1050 V DC trip would exceed the 0.85 x V_DSS peak rule before any overshoot, so the 1700 V part is needed anyway.  T-type - 1700 V outer devices cost +43 USD at 5k and take the FIT per unit from 66.3 to 0.14 (950 V, 25 C, sea level; Wolfspeed Gen 3 curve as proxy - no Chinese maker publishes cosmic-ray data).

## What the efficiency rules cost (L1 re-picked, A2 24 kHz)

Cheapest L1 that passes magnetics' own thermal screen: 85 USD; the chosen part's hot spot is 140 C at 60 C inlet (limit 140 C, OpenMagnetics-verified losses).

| rule | L1 part | L1 5k USD each | 3 x L1 vs chosen |
|---|---|---|---|
| chosen: peak >= 98.7 %, full load >= 98.0 % | amor alfoil N26, 8.6 kg | 85 | +0 |
| peak >= 98.5 % (no model margin), full load >= 98.0 % | amor alfoil N26, 8.6 kg | 85 | +0 |
| peak >= 98.5 %, no full-load floor | amor alfoil N26, 8.6 kg | 85 | +0 |

## Conclusion

**Recommended: A2 24 kHz**, 1092 USD at 5,000 units (1365 catalogue), 29 USD (2.6 %) below the runner-up A1xA2 20 kHz, L1 117 uH; peak efficiency 99.09 %, filter 404 USD / 30 kg.  The D-053 design corrected (A0) costs 1194 USD.
Same design point, other settings: A* two-level, levers combined (minmax, trip 450 A) 1092 USD; A* two-level, same, sinusoidal PWM (no A3) (policy, trip 450 A) 1092 USD - at this point the L1 is already the cheapest part that passes the thermal screen, so a flatter reference buys nothing and D-053's sinusoidal policy stays.
Cheapest acceptable T-type: B rule kept: 1700 V outer, 1279 USD (+187 USD against the best two-level).  The sweep's resolution is about +-20 USD: the magnetics grid is discrete and the cheapest part that meets the efficiency rules jumps between amorphous and nanocrystalline designs.
pcs_design.py carries this design (A2 24 kHz): f_sw 24 kHz, L1 130 uH, C_f 75 uF, L2 6 uH, trip 450 A, modulation policy; inductor cost, mass and loss from sim/magnetics.py.
