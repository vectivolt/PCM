# PCS-P125 topology cross-check against Wolfspeed CRD-25BDA6512N-K (sim/pcs_crosscheck.py)
**Verdict: A holds with conditions.** B - a T-type done the Wolfspeed way with Chinese SiC (66 x Sichain 1200 V, 12 gate channels) - is CALCULATED 0.3-0.4 %-points more efficient at 125 kW and, once the filter inductors are priced from their mass, costs about the same: 1,004-1,035 against A's 979-1,028 USD at 5,000 units (on the study's own prices B is +53 USD with the midpoint handled by firmware and up to +245 USD with the capacitors the study's premise needs).  
A keeps the decision on robustness and simplicity, not on cost: its devices sit at 0.56 of rating (SRC-4 keeps the 0.67 rule; B's outer devices sit at 0.79) and give 0.15 FIT per unit at a constant 950 V against B's 62 (16 on a battery profile; Wolfspeed Gen 3 data as proxy, no Chinese maker publishes any), and it needs 6 gate channels instead of 12 and no midpoint control.  
Claims 1 and 2 are overstated as general statements (3.5 mF per half holds only for carrier-based control at full current, PF 0 and 600 V; the 0.67 rule stands in for a FIT budget); claims 4 and 6 expose two optimistic numbers behind A - inductor prices about half their material cost, and E_on taken at the data-sheet gate resistor (peak 99.20 % is more like 98.9-99.1 %).  None reverses the choice; the conditions are at the end.  
Labels: MEASURED-BY-WOLFSPEED, CALCULATED-BY-WOLFSPEED, DATASHEET, CALCULATED (ours), ESTIMATE (ours, named assumption). Nothing of ours is bench-validated.

Inputs: Wolfspeed user guide PRD-08613 Rev. 2 (pages as printed), schematic and BOM rev 3.0 in `docs/reference-designs/wolfspeed/crd-25bda6512n-k/` (README there); the study's model and outputs, read-only (`sim/pcs_design.py`, `pcs_devices.py`, `pv_devices.py`, `sim/out/pcs_design/pcs_spec.json`, `pcs_costed_bom.csv`); C3M0025065K data sheet filed for this check.  The second Wolfspeed design, CRD200DA23N-GMA (200 kW two-level, 1500 V), is used where it helps (claims 2, 3, 6).

## Claim 1 - 'any three-level stage needs about 3.5 mF per DC-link half for the 150 Hz midpoint ripple at PF 0'

**Method (CALCULATED, independent of the study's code).** Local-average midpoint current of three three-level legs, i_np = sum (1 - |v_x|) i_x, with the phase references free to take any zero sequence that keeps every |v_x| <= 1.  For each operating point a linear programme finds the least peak-to-peak midpoint charge that ANY carrier-based zero sequence can reach over a fundamental period (an ideal predictive midpoint controller - no real controller does better).  Both halves take the midpoint current (the battery holds the total voltage), so C_eq = 2 C_half.  m and the converter-side angle include B's L1 + L2 (64 uH).  Check: with no zero sequence the swing is 0.5 m I_pk/omega at PF 0 and 0.342 m at PF 1 (textbook result for sinusoidal PWM, from memory; reproduced, see self-check).

| V_dc | m at PF 0 | charge swing at PF 0 (I_pk/omega): SPWM / min-max / best | C per half for 60 V pp, 198 / 216 A | for 120 V pp, 198 / 216 A |
|---|---|---|---|---|
| 600 V | 1.11 | n/a (m > 1) / 0.503 / 0.470 | 3.49 / 3.81 mF | 1.75 / 1.90 mF |
| 750 V | 0.89 | 0.444 / 0.402 / 0.269 | 2.00 / 2.18 mF | 1.00 / 1.09 mF |
| 950 V | 0.70 | 0.350 / 0.318 / 0.099 | 0.74 / 0.81 mF | 0.37 / 0.40 mF |

Best-case residual charge against PF angle (I_pk/omega; 0 = the zero sequence cancels it): 600 V - 0 deg: 0.00, 26 deg: 0.13, 37 deg: 0.21, 60 deg: 0.37, 90 deg: 0.47; 750 V - 0 deg: 0.00, 26 deg: 0.00, 37 deg: 0.00, 60 deg: 0.12, 90 deg: 0.27; 950 V - 0 deg: 0.00, 26 deg: 0.00, 37 deg: 0.00, 60 deg: 0.00, 90 deg: 0.10.

**The number holds - at one corner.** CALCULATED: 3.5-3.8 mF per half at 600 V, PF 0, 198-216 A and 60 V pp; 2.2 mF at 750 V and 0.8 mF at 950 V.  The study's 3.5 mF is therefore right for its premise (to within 10 %).  It is not right for 'any three-level stage': each of the three premises carries it.

1. **Full current at PF 0.** REQUIREMENTS AC-02 says 'PF -1...+1'; Megarevo's PMA0125 data sheet (`docs/reference-designs/megarevo/pma/pma-125-135kw_datasheet_v1-0.pdf`, AC table) prints 'Adjustable power factor range >0.99; -1~+1' next to 137 kVA continuous and 150 kVA maximum, with no separate reactive-power rating - so full current at PF 0 is implied, not stated; off-grid, the load sets the power factor.  The grid codes the study names ask for less (clauses from memory, not on file): IEEE 1547-2018 category B (Q = 0.44 S) needs 1.36 mF and EN 50549-1 / GB/T 34120 class (PF 0.9 at rated current) 0.84 mF per half at 600 V and 60 V pp (CALCULATED).  Low-voltage ride-through reactive current is short and comes with a low grid voltage (low m), where the residual is small.

2. **60 V pp everywhere.** The worst ripple is at the bottom of the DC range, where each half sits at 300 V and is far from any voltage limit; at 950 V, where the half voltage matters, the best-case ripple with A's own 700 uF per half is 69 V pp (CALCULATED, 216 A, PF 0), 15 % above the study's 60 V target.  A voltage-dependent limit (the half voltage stays below the 600 V film rating and the inner devices' derating) would cut the need sharply; at 600 V the same 700 uF would swing 326 V pp, which the modulator can follow (each phase can still reach +-V_dc/2 when the levels' actual values are fed forward) but which we would not accept without a control simulation.

3. **Carrier-based modulation.** Modulations that cancel the low-frequency midpoint current at any m and PF exist: the nearest-three-virtual-vector PWM (Busquets-Monge, Bordonau, Boroyevich, Somavilla, IEEE PEL 2004) and its carrier form, double-signal PWM (Pou et al., IEEE TIE 2007) - from memory: each leg uses all three levels inside a carrier period, which raises the switching losses and the output ripple while it is active, so hybrids use it only where the carrier-based residual is non-zero (low PF, high m).  The limit case is two-level operation of the T-type's outer devices, which draws no midpoint current at all.  On the study's own model and heat sink (CALCULATED, B's devices, 600 V, PF 0, 198 A, 45 C inlet): leg loss 425 W three-level -> 400 W two-level, hottest junction 89 -> 108 C against the 150 C limit (B's worst junction over the study's sizing corners at 110 % is 103 C).  So the PF-0 corner can be covered by firmware with B's device count; the cost is two-level ripple in L1 in that mode (B's 49 uH L1 sees about 96 A p-p at 600 V instead of its 76 A design value) and a two-level common-mode step, both no worse than A's.  The 150 Hz midpoint current itself is a thermal non-issue for film: 170 A rms at 600 V / PF 0 / 216 A with min-max, i.e. about 17 A rms per capacitor with 5 per half (C3D1U147: 40.2 A).

| reference (three-level unless stated) | capacitance per half per kW | at 125 kW |
|---|---|---|
| Wolfspeed CRD-25BDA6512N-K (T-type, electrolytic) | 75.2 uF/kW | 9.40 mF |
| TI TIDA-01606 (T-type, electrolytic) | 65.5 uF/kW | 8.18 mF |
| study's three-level need (3.5 mF per half) | 28.0 uF/kW | 3.50 mF |
| candidate A (two-level, 700 uF film per half) | 5.6 uF/kW | 0.70 mF |

What Wolfspeed fitted: 4 x 470 uF / 500 V aluminium electrolytics per half (BOM line 1; UG p25) = 75 uF per kW per half, 2.7 times the study's 3.5 mF scaled to 25 kW, plus 2 x 2 uF film and 2 x 0.1 uF C0G per phase across the halves (schematic sheet 2.x, C9-C12).  At its own rating that bank would ripple 9 V pp at PF 0 (CALCULATED, best case).  The guide says nothing about reactive operation: the inverter was tested into a resistive load and the PFC near unity power factor (UG Table 10 p49: 0.999 at 25 kW, >= 0.99 above 5 kW, 0.90 at 1.27 kW).  TI's TIDA-01606 fits about the same per kW.  Both use 500 V electrolytics per half, which a 900 V maximum allows (0.90 of rating); at our 950 V they would need two in series per half, and on the study's own price for 400 V cans (Samyoung TDC400V470, 3.74 USD at 5,000) that costs about the same per microfarad as the film it would replace.

Side effect the study already counts (its step f): a three-level midpoint controller needs a low-frequency zero sequence at every DC voltage, i.e. a 150 Hz voltage between the battery and earth on a three-wire TN connection; A needs one only below about 680 V.  This, not the capacitance, is B's lasting disadvantage on the DC link.

**Effect on the decision:** the claim is true for the premise the study chose and false as a general statement; with the reactive corner handled by firmware a T-type needs A's 5 + 5 film bank (ESTIMATE: HF ripple per half no larger than A's), which moves B's cost by 192 USD at 5,000 units (claim 3).  It does not by itself decide between A and B.

## Claim 2 - '1200 V devices at 950 V fail by cosmic rays at about 104 FIT per unit; the 0.67-of-rating rule settles it'

Per cm2 of die, 25 C, sea level, blocking the voltage continuously.  Wolfspeed Gen 3 / Gen 4 1200 V SiC: our reading of Fig. 4 p7 of Wolfspeed's application brief on file (300-dpi render, about +-10 V); 'study' = the study's own reading of the same figure; Si IGBT = Semikron AN 17-003 (Fig. 3 / Table 2, as calibrated in pcs_devices):

| V_DS | SiC Gen 3 (ours / study) | SiC Gen 4 | Si 1200 V IGBT |
|---|---|---|---|
| 800 V | 5.3 / 5.0 | 0.01 | 0.4 |
| 850 V | 14.9 / 13.7 | 0.01 | 2.5 |
| 900 V | 35.4 / 30.0 | 0.12 | 14.0 |
| 950 V | 73.4 / 70.0 | 1.67 | 47.0 |

Per PCS (CALCULATED from the curves; die areas = pcs_devices' ASSUMED values, 0.12 cm2 for a 33-36 mOhm 1200 V die, 0.15 cm2 for the 40 mOhm 1700 V die; a T-type outer device blocks the full V_dc only while the opposite rail is connected: 23 % of the time at 950 V, 30 % at 750 V, min-max PWM; a two-level switch 50 %).  'Profile' = an LFP string sized to 950 V (260 cells), ESTIMATED time shares: 949 V 0.5 %, 910 V 2.0 %, 885 V 12.5 %, 866 V 35.0 %, 845 V 40.0 %, 800 V 8.0 %, 700 V 2.0 % of energised time (2.5 % above 900 V):

| design | FIT at 950 V | at 900 V | at 866 V | profile | profile, Gen 4 devices | failures per year, 5,000 units (profile / 950 V) |
|---|---|---|---|---|---|---|
| A: two-level, 36 x 1700 V SiC (1200 V curve at V x 1200/1700) | 0.15 | 0.03 | 0.03 | 0.03 | 0.03 | 0.00 / 0.01 |
| B: T-type, 30 outer + 36 inner 1200 V SiC | 61.74 | 31.44 | 18.69 | 16.29 | 0.07 | 0.71 / 2.70 |
| B with 650 V inner devices (Wolfspeed's choice), voltage-fraction scaling ASSUMED | 149.12 | 68.63 | 37.51 | 32.66 | 0.06 | 1.43 / 6.53 |
| two-level, 36 x 1200 V SiC | 158.49 | 76.40 | 43.67 | 37.99 | 0.06 | 1.66 / 6.94 |
| D-047: T-type, 24 outer 1200 V IGBTs (Semikron curve) | 102.74 | 32.30 | 10.40 | 9.59 | 9.59 | 0.42 / 4.50 |

- **The 104 FIT is reproduced** for the IGBT T-type (103 FIT at 950 V).  It is a silicon-IGBT figure; for 1200 V SiC the per-cm2 rate is no better (Gen 3: 73 against 47 FIT/cm2 at 950 V) but the dies are smaller: B gives 62 FIT at a constant 950 V, 31 at 900 V, about 16 on the profile.  Gen 4 is about 40 times better at 950 V and puts B at 0.07 - the device generation matters more than the voltage rule.  No Chinese maker (Sichain included) publishes any curve.
- **650 V parts in an I-type at 475 V:** no 650 V SiC curve is on file.  Scaling the 1200 V curve by the fraction of rating (the study's method) would put 475 V on 650 V at 877 V-equivalent, about 24 FIT/cm2 - but the only measured 650 V data on file contradict that scaling: Semikron finds 650 V silicon chips below 1 FIT/cm2 up to 500 V and calls a 1000 V NPC on 650 V chips 'immune' (AN 17-003 p9).  Unresolved for SiC; B avoids it by using 1200 V inner devices (475 V = 0.40 of rating).
- **How the industry runs 1000 V-class three-level stages (documents on file):** Semikron AN 17-003 p8 - 1000 V PV inverters on 1200 V devices are 'electrically feasible' because the highest voltages occur at low current; p10 - a T-type carries about one third of the cosmic-ray rate of a two-level stage with the same chips; TI TIDA-01606 (TIDUE53 p15) - designed for a 1000 V DC link with 1200 V outer and 650 V middle SiC; Wolfspeed CRD-25BDA6512N-K - 900 V maximum on 1200 V outer devices (0.75) and 650 V middle devices at 450 V (0.69); Infineon REF-10KW3LNPC2 - 850 V maximum on a 1200 V / 650 V NPC2.  The two-level alternative at about two thirds of rating also has a published precedent: Wolfspeed CRD200DA23N-GMA, 1500 V on 2300 V modules (0.65).
- **Battery profile:** unlike PV, a battery holds its voltage near the top of the range for long periods - an LFP string sits on its plateau at about 0.9 of its maximum, so the PV argument (high voltage only at low current) transfers only partly.  On the profile above the string spends about 2.5 % of its time above 900 V; the profile rate is 26 % of the constant-950 V rate (ESTIMATE; the real share depends on the SOC window and dispatch, and on whether standby parks the legs: a T-type parked in the zero state blocks only V_dc/2 on every device).

**Effect on the decision:** the rule does not settle it; the evidence does, partly.  B is a 16-62 FIT design on a proxy curve (CALCULATED; for scale, from memory, power-converter field failure rates are of the order of 1 % per year, about 1,000 FIT, so this is a few per cent of the total - acceptable on a 100 FIT budget, but a burnt outer device shorts the DC link), and the Chinese devices' real curves are unknown.  A is 415 times better at 950 V at no cost penalty, which is a legitimate reason to prefer it - stated as a FIT budget and a data gap, not as a rule.

## Claim 3 - B done the Wolfspeed way with Chinese SiC, scaled to 125 kW and 590-950 V, on the study's price basis

**Scaling.** Wolfspeed runs one device per position at 36 A rms and 60 kHz and reaches only 70.5 C junction (Table 12 p53, CALCULATED-BY-WOLFSPEED from a MEASURED 58.2 C case) - its device count is set by efficiency, not by temperature.  At the same current per device 125 kW needs 5 per position.  The study's own sizing at its junction limits (110 % / 45 C inlet, 150 C; 120 % for 2 min and 200 ms overloads) gives 5 outer + 6 inner Sichain SG2M035120LJ (1200 V, 33 mOhm, LCSC-priced) per phase leg, 66 devices - the 'TT-SiC 1200/1200' row of its screen; on its model B's hottest junction over the sizing corners at 110 % is 103 C.  No Chinese 650-750 V SiC in TO-247-4 has a public price, so the inner position uses the same 1200 V part (it also removes the 650 V cosmic-ray question).  32 kHz as A.  Gate drive: 12 channels (A's channel, driver and D-050 parts per channel) and Wolfspeed's bias scheme - the middle pair in common drain shares the phase-node supply with the high-side switch, so 5 isolated supplies serve 12 channels (UG p19-21).  LCL: L1 49 uH (three-level, 25 % ripple at 32 kHz - the study's own figure), C_f 50 uF, L2 15 uH (resonance 6.6 kHz stiff grid, below f_s/6).  Heat sink, ports, sensing, control, aux: A's rows unchanged.

| variant (three-wire) | film C3D1U147 per half | catalogue USD | 5,000 units USD | vs A at 5,000 |
|---|---|---|---|---|
| A (study, D-053 + D-050 protection) | 5 | 1,086 | 869 | - |
| B1 study's premise: full 216 A at PF 0, 60 V pp, best carrier-based control | 28 | 1,434 | 1,114 | +245 USD (+28.1 %) |
| B2 full 216 A at PF 0, 120 V pp | 14 | 1,297 | 997 | +128 USD (+14.7 %) |
| B3 grid-code reactive range only, 60 V pp | 10 | 1,257 | 964 | +94 USD (+10.9 %) |
| B4 full current at PF 0 by a two-level fallback in the reactive corner (firmware), 5 per half | 5 | 1,208 | 922 | +53 USD (+6.1 %) |

Rows of B that differ from A (B4; every other row is A's, same unit prices):

| block | item | qty | USD cat / 5k each | basis |
|---|---|---|---|---|
| POWER | SiC 1200 V 33 mOhm, T1/T4 5 + T2/T3 6 per phase | 66 | 3.50 / 2.26 | pcs_devices: LCSC C52109938 @1 / @900 (REAL) |
| POWER | Device insulator Al2O3 0.635 mm + clip + grease (basic DC-PE | 66 | 0.40 / 0.30 | A's unit price per device |
| POWER | Local decoupling film 2.2 uF 1300 V at every device pair (3  | 54 | 0.45 / 0.38 | ESTIMATE: 2 commutation cells per phase -> 2 x A's 27 (A's 1300 V part kept) |
| POWER | RC damper per device pair: 2 x 4.7 nF 2000 V C0G + 6 x 15 oh | 6 | 5.73 / 4.86 | ESTIMATE: one damper per commutation cell (2 per phase) |
| GATE DRIVE | Isolated SiC gate driver, DESAT + soft turn-off + Miller cla | 12 | 2.98 / 1.54 | 4 channels per phase |
| GATE DRIVE | Channel discretes (gdrv.channel: booster, per-gate clamp FET | 12 | 3.81 / 3.24 | A's channel discretes per channel |
| GATE DRIVE | D-050 per channel: RC dead-time stretch (7 parts, SN74LVC1G1 | 12 | 0.12 / 0.11 | D-050 stretch + negative-rail detector per channel |
| GATE DRIVE | Gate bias per phase: SN6505B + custom transformer (2 seconda | 5 | 3.03 / 2.64 | Wolfspeed's scheme: common-drain inner pair -> 3 phase-node + 1 midpoint + 1 DC- supply |
| POWER | DC-link film 140 uF 600 V (U_N 70 C), 5 per half, split bank | 10 | 4.90 / 4.17 | 5 per half |
| LCL FILTER | L1 49 uH, 216 A rms / 344 A pk (2.9 J) | 3 | 27.40 / 21.92 | the study's price model (10 USD + 6 USD/J, x0.80) |
| CONTROL | T-type state interlock (outer/inner of one half never on together), 3 x AND gate + RC | 3 | 0.15 / 0.12 | ESTIMATE |

**Performance on the study's model** (CALCULATED; E_off x2.17 as the study's step e for both; junction temperatures from its heat-sink iteration at 45 C inlet; L1/L2 loss from its filter_loss_est; second column with E_on scaled to the chosen R_G,on 8.75 ohm per device by the data sheets' E-vs-R_G curves, x1.63 for A's device and x1.81 for B's):

| point | A devices W (E_on corrected) | A L1+L2 W | B devices W (E_on corrected) | B L1+L2 W | B saves | Tj max A / B |
|---|---|---|---|---|---|---|
| 750 V, 100 % (124.7 kW) | 1277 (1410) | 293 | 1007 (1029) | 197 | 477 W = 0.38 %-points | 102 / 84 C |
| 900 V, 100 % (124.7 kW) | 1381 (1560) | 293 | 1096 (1129) | 197 | 527 W = 0.42 %-points | 108 / 83 C |
| 600 V, 35 % (43.6 kW) | 225 (269) | 138 | 125 (132) | 85 | 190 W = 0.43 %-points | 56 / 51 C |

**With the inductors re-priced from their mass (claim 4)**, at 5,000 units: A 979-1,028 USD, B4 1,004-1,035 USD (aggressive and moderate design points) - B is 7-25 USD dearer (1-3 %), i.e. at parity within the precision of these estimates: its smaller L1 recovers most of what its extra gate channels, devices and decoupling cost.  B's lower loss would let its heat sink shrink by perhaps a fifth (ESTIMATE, 10-20 USD; not taken).

**Effect on the decision:** B is better on efficiency (CALCULATED 0.38 %-points at 125 kW / 750 V, 0.43 at the 35 % peak point, E_on corrected) and on L1 size, and no cheaper under any DC-link premise; it doubles the gate channels (12 against 6), nearly doubles the devices (66 against 36), needs a midpoint controller with a firmware fallback and a low-frequency zero sequence at every DC voltage, breaks the project's 0.67 derating rule (SRC-4) on its outer devices, and carries claim 2's data gap.  The 98.5 % efficiency requirement is met by both, so the gain buys nothing the requirements ask for.  A holds; B is the documented fallback if efficiency is ever sold or a Chinese maker publishes cosmic-ray data.

## Claim 4 - the filter: L1 97 uH, C_f 50 uF, L2 15 uH against the reference, and the inductor cost

| design | L in % of Z_base | C_f in % | worst ripple p-p / I_pk | L f I_pk / V_dc | uH x kW |
|---|---|---|---|---|---|
| Wolfspeed as built (BOM L1 125 uH, C22 10 uF), three-level, 60 kHz, 25 kW | 0.61 % | 2.0 % | 13.3 A = 26 % | 0.48 | 3,125 |
| Wolfspeed's own procedure (UG p14: 102 uH, 24.9 uF at 64 kHz) | 0.50 % | 5.0 % | 15.3 A = 30 % | 0.42 | 2,550 |
| A: two-level, 32 kHz, 125 kW (L1 97 uH, C_f 50 uF) | 2.38 % | 2.0 % | 76.5 A = 25 % | 1.00 | 12,125 |
| B: three-level, 32 kHz (L1 49 uH, C_f 50 uF) | 1.20 % | 2.0 % | 75.7 A = 25 % | 0.50 | 6,125 |

Per unit the designs agree: both size L for about 25 % peak-to-peak ripple of the peak current; a three-level leg needs half the L x f of a two-level leg for the same ripple (0.48-0.50 against 1.00), and A switches at about half Wolfspeed's frequency, hence its L1 is 3.9 times Wolfspeed's in per unit.  C_f is 2.0 % of base in both (Wolfspeed cut its own 5 % result to 10 uF for nominal noise suppression only, UG p14-15, with a separate EMI board).  Wolfspeed has no L2 on the board (an external FN3256H line filter in the test set-up, UG Fig. 26 p32); A's L2 is 0.37 %.  Wolfspeed damps with 0.68 ohm in series with each C_f (R26, 7 W) and ties the C_f star to the DC midpoint through C21 (2.2 uF on the schematic, 0.1 uF X1 in the BOM - the two disagree); A ties its star to the midpoint directly.  Wolfspeed gives no core, material, turns or wire - only 125 uH / 6.5 mOhm DCR / 100 kHz in the BOM (no maker), a 700 g choke (Fig. 3 p13), a 40 mm height limit and a 120 C limit (p15), and references to powder-core design notes; the photograph (p28) shows a round-wire toroid.

**Inductor cost from mass (ESTIMATE).** Area product A_w A_c = L I_pk I_rms / (B_pk J k_u) on a C-core proportion; copper 18 USD/kg (the repo's basis), amorphous core 7.5 USD/kg and Si-steel 3 USD/kg (ESTIMATED, no quote), insulation/winding/test from the repo's L_CELL row grown with size, +10 %.  The method reproduces the one published mass: Wolfspeed's choke at 0.5 T, 4.5 A/mm2, k_u 0.35 comes out at 750 g against Wolfspeed's 700 g.

| inductor | design point | AP cm4 | core kg | Cu kg | Cu loss at I_rms | USD cat / 5k (ours) | study's USD cat / 5k |
|---|---|---|---|---|---|---|---|
| A L1 97 uH, 216 A rms, 344 A pk | 1.2 T, 3.5 A/mm2, k_u 0.45 | 381 | 3.31 | 2.51 | 79 W | 101 / 81 | 44 / 36 |
| A L1 97 uH, 216 A rms, 344 A pk | 1.3 T, 4.5 A/mm2, k_u 0.50 | 246 | 2.38 | 2.01 | 104 W | 80 / 64 | 44 / 36 |
| B L1 49 uH, same current | 1.2 T, 3.5 A/mm2, k_u 0.45 | 193 | 1.98 | 1.50 | 47 W | 65 / 52 | 27 / 22 |
| B L1 49 uH, same current | 1.3 T, 4.5 A/mm2, k_u 0.50 | 124 | 1.43 | 1.20 | 63 W | 52 / 41 | 27 / 22 |
| L2 15 uH (A and B) | Si-steel 1.4 T, 3.5 A/mm2 | 51 | 0.77 | 0.55 | 17 W | 26 / 20 | 16 / 12 |

The study's price model (10 USD + 6-8 USD per joule) under-prices these inductors by about 1.8-2.3 times: A's L1 alone carries 2.0-2.5 kg of copper, 36-45 USD at the repo's own copper price, against 35.5 USD for the whole part at 5,000 units.  The loss budget is also tight: the study allows 35 W copper at 180 A and 22 W core per L1 (57 W in all); the moderate design above has about 55 W of DC copper loss at 180 A, and an amorphous core at 32 kHz with A's ripple loses about 59 W (ESTIMATE, Metglas 2605SA1 loss fit quoted from memory) - meeting 57 W needs a lower current density and a lower-loss core (powder or nanocrystalline), i.e. a bigger and dearer part.

**Effect on the decision:** the values are believable and consistent with the reference; the prices are not.  Re-pricing hits A harder than B (A's L1 stores twice the energy) but by less than B's extra gate drive and devices cost - see the corrected totals in claim 3.  It raises A's own figure by roughly 110-159 USD at 5,000 units (to 7.8-8.2 USD/kW), which matters against the 6.6 USD/kW benchmark (SRC-5).

## Claim 5 - six TO-247-4 devices in parallel per switch at 32 kHz

Published guidance (application notes NOT on file; quoted from memory, to be filed when the gate-drive sheet is drawn): Wolfspeed's and Infineon's SiC paralleling notes and the IEEE literature (e.g. Li, Munk-Nielsen et al., 'Influences of device and circuit mismatches on paralleling silicon carbide MOSFETs', IEEE TPEL 2016) agree on the mechanisms: static sharing is self-correcting (positive R_DS(on) temperature coefficient); dynamic sharing is set by the spread of V_GS(th) and transconductance and by unequal common-source and power-loop inductance, and concentrates switching loss in the fastest device; paralleled gates can oscillate against each other through the shared gate node.  The remedies are schematic-level and layout-level: one driver (or buffer) per switch with an individual gate resistor per device, a small resistor in each Kelvin-source return to damp circulating currents, symmetric power and gate loops, devices from one lot or binned for V_GS(th), and a derating of the order of 10-20 %.  None of this is a showstopper for a schematic-level design; the risk sits in the layout and in the device spread, which this project cannot test.

On file: Wolfspeed parallels two C3M0075120K per switch in CRD-60DD12N; CRD-25BDA6512N-K, though not paralleled, shows the per-device network to copy - turn-on 5.1 ohm, turn-off 5.1 ohm || (5.1 ohm + Schottky), 1 ohm in the Kelvin-source return, 10 kohm + 1 nF gate-source at the device, Miller clamp straight to the gate (schematic sheet 2.x).

**The number that matters:** Sichain prints V_GS(th) 2.5 / 3.1 / 4.0 V (min / typ / max) for SG2M040170HJ, with g_fs about 23 S - a 1.5 V window, so unbinned parts can differ by tens of amperes during the transition.  The study takes the hottest of six at 1.10 x the mean current; at 1.30 (CALCULATED, the study's model at its worst corner, 950 V, 340 V AC, PF angle 90, 110 %; applied to conduction and switching alike, so an upper bound) the worst junction rises from 116 C to 138 C at 45 C inlet and from 138 C to 163 C at 60 C inlet (limit 150 C continuous).

What the schematic must carry for each paralleled device (most already in gen/gdrv.py's channel; check each against the PCS sheet when it is drawn): (1) its own R_G,on and R_G,off (diode-steered, as Wolfspeed); (2) a 0.5-1 ohm Kelvin-source resistor; (3) gate-source pull-down and a small gate-source capacitor at the device pins; (4) a Miller-clamp path per gate or per tight group; (5) the buffer stage sized for six gates (the study's 14.5 A peak need against the driver's 10 A); (6) local DC-link decoupling per device pair and the RC damper; (7) DESAT sensing on one device per switch with the blanking matched to the paralleled turn-on; (8) one temperature sensor per heat-sink section; (9) a BOM note: one lot per switch, V_GS(th) binned to +-0.25 V or a sharing test at incoming inspection.

**Effect on the decision:** no showstopper; a condition.  Without binning the 60 C-inlet derating of the study is optimistic (163 C against 150 C at 1.30 sharing).  B has the same issue with 5-6 devices per position and twice the gate channels.

## Claim 6 - calibration: the study's loss model against Wolfspeed's measured efficiency

The study's model (pcs_design.leg_losses, T-type table, data-sheet curves through pv_devices) applied to the Wolfspeed board: C3M0032120K outer (pv_devices' entry), C3M0025065K middle (data sheet, added in memory), one device per position, 60 kHz, min-max zero sequence (closest available to Wolfspeed's third-harmonic injection), the converter current including C22's, junction temperatures scaled from Wolfspeed's Table 12 (ambient 25 C ASSUMED).  Two variants: (a) as the study applies the model - energies at the data-sheet gate resistor; (b) energies scaled to the board's gate network with the data sheets' E-vs-R_G figures (turn-on 6.1 ohm, turn-off 3.55 ohm incl. the 1 ohm Kelvin resistor: E_on x1.39, E_off x1.45 for C3M0032120K).  'Measured loss' = P_out (100/eta - 1) from the efficiency column, MEASURED-BY-WOLFSPEED; the power columns of the same table give up to 54 W more at full power (the table is internally inconsistent by 0.1-0.2 % there).  DCR = 3 I^2 x 6.5 mOhm (BOM).

| V_dc / V_LL | P_out kW | measured loss W (eta / P columns) | model devices W (a) / (b) | inductor DCR W | left for core, damping, relays, PCB, caps W | devices + DCR as share of measured |
|---|---|---|---|---|---|---|
| 670 / 400 | 3.66 | 30 / 30 | 14 / 18 | 1 | 11 | 62 % |
| 670 / 400 | 6.97 | 51 / 50 | 26 / 31 | 2 | 17 | 65 % |
| 670 / 400 | 10.68 | 81 / 80 | 47 / 53 | 5 | 23 | 71 % |
| 670 / 400 | 14.34 | 126 / 130 | 74 / 81 | 8 | 36 | 71 % |
| 670 / 400 | 17.89 | 183 / 180 | 107 / 116 | 13 | 51 | 71 % |
| 670 / 400 | 21.13 | 252 / 250 | 143 / 155 | 18 | 77 | 68 % |
| 670 / 400 | 24.56 | 356 / 360 | 190 / 204 | 25 | 128 | 64 % |
| 800 / 400 | 3.65 | 49 / 50 | 16 / 22 | 1 | 26 | 46 % |
| 800 / 400 | 7.02 | 71 / 70 | 31 / 37 | 2 | 31 | 55 % |
| 800 / 400 | 10.63 | 107 / 110 | 52 / 60 | 5 | 43 | 60 % |
| 800 / 400 | 14.41 | 154 / 150 | 83 / 93 | 8 | 49 | 66 % |
| 800 / 400 | 17.98 | 220 / 220 | 119 / 131 | 13 | 76 | 66 % |
| 800 / 400 | 21.16 | 294 / 290 | 159 / 173 | 18 | 99 | 65 % |
| 800 / 400 | 24.85 | 391 / 420 | 214 / 231 | 25 | 135 | 66 % |
| 900 / 400 | 3.63 | 62 / 70 | 19 / 25 | 1 | 37 | 41 % |
| 900 / 400 | 7.01 | 89 / 90 | 34 / 41 | 2 | 45 | 49 % |
| 900 / 400 | 10.62 | 124 / 120 | 57 / 66 | 5 | 49 | 57 % |
| 900 / 400 | 14.31 | 178 / 180 | 88 / 100 | 8 | 70 | 61 % |
| 900 / 400 | 17.48 | 241 / 240 | 122 / 136 | 12 | 92 | 61 % |

**Model against Wolfspeed's own device-loss figures** (25 kW, 800 V / 400 V): Wolfspeed CALCULATED 28.5 W per outer and 11.0 W per middle device from its MEASURED case temperatures (Table 12) - 237 W for the 12 devices; the study's model gives 214 W as applied (a) and 231 W with the board's gate resistors (b), split 23.0 W outer / 15.5 W middle.  Wolfspeed's own simulation (Table 2 p13, 85 C heat sink) gives 275 W.  So the model's device total is within 2 % of Wolfspeed's when the gate resistors are honoured and 10 % low when they are not, but it puts 19 % less in the outer device and 41 % more in the middle one than Wolfspeed does (the T-type's 400 V switching energy is scaled from 800 V curves by an ASSUMED exponent).

**Against the measurement**, devices + inductor DCR explain 66 % of the measured loss at 25 kW; the remaining 135 W (about 0.54 % of the power) is everything the device model does not cover - L core and AC-copper loss at 60 kHz, the 0.68 ohm damping resistors, relays and NTC bypass, PCB copper, capacitor ESR.  It grows roughly in proportion to load (fit on the 800 V / 400 V series: -5 W + 5.0 W per kW), i.e. it behaves like series resistance and ripple-driven loss, not like a fixed loss.  Wolfspeed's measured efficiency cannot separate these; the board publishes no inductor data.

**Second reference not usable:** CRD200DA23N-GMA's Figure 50 (PRD-09623 section 5.2) plots efficiency against 'output power' up to 200 kW, but the test ran 167 A rms at 300 Hz into a 129 uH load - about 20 kvar of reactive power (CALCULATED) from a 30 kW supply - and the guide does not say how 'output power' was defined.  Not used.

**How far to trust A's 99.20 %:** (1) the device model is within about 10 % on Wolfspeed's board once the gate resistors are honoured - but A's own run takes E_on at the data-sheet 2.5 ohm while its gate drive uses 8.75 ohm: correcting that adds 44 W at the 35 % peak point (0.10 %-points) and 133 W at 125 kW / 750 V; (2) the passive part: Wolfspeed's board loses about 0.5 % of its power outside the devices and DCR, A's budget allows about 0.3 % for filter, contactors, fuses and busbars together, and claim 4 shows the L1 budget is optimistic.  Read the 99.20 % as about 98.9-99.1 % and the 98.65 % full-load figure as about 98.3-98.5 % (ESTIMATE: the E_on correction plus up to 0.2 %-point of passive loss); the requirement (maximum efficiency >= 98.5 %) stays met, the full-load figure is not a requirement.

## What to change in the PCS design

1. Re-price L1 and L2 from a mass-based design (MAG-1/MAG-2 deliverables): the 10 USD + 6-8 USD/J model is about half the material cost; A's 5,000-unit figure rises to about 979-1,028 USD (7.8-8.2 USD/kW).  Report it against the 6.6 USD/kW benchmark (SRC-5) rather than the 869 USD now in D-053.
2. Design L1 to its loss budget before trusting the efficiency: an amorphous C-core at 32 kHz with 25 % ripple loses about 59 W in the core alone against a 57 W total budget; try powder or nanocrystalline cores, or a higher L1 for less ripple, and restate the budget.
3. Scale E_on to the chosen R_G,on in the efficiency and junction-temperature runs (data-sheet curve: x1.63 at 8.75 ohm), or lower R_G,on if the commutation simulation allows; A's peak drops about 0.1 %-point.
4. Write the reactive-power requirement explicitly in REQUIREMENTS AC-02: full current at PF 0 (what the competitor's 'PF -1~+1' with 137 kVA continuous implies) or the grid-code range (about 0.44 S).  It sizes any three-level DC link (claim 1: 3.8 mF against 1.4 mF per half) and A's own thermal corners.
5. Restate D-053's reasons: the 3.5 mF applies to carrier-based midpoint control at full current, PF 0 and 600 V, not to any three-level stage; the cosmic-ray case is a FIT budget (B 16-62 FIT on Wolfspeed Gen 3 data, A 0.15) plus the absence of any Chinese maker's data, not the 0.67 rule.
6. Put V_GS(th) binning (or one lot per switch plus an incoming sharing test) and a 0.5-1 ohm Kelvin-source resistor per device on the PCS gate-drive sheet; re-run the 60 C-inlet derating with a 1.3 sharing factor for switching loss.
7. Copy Wolfspeed's per-device gate network (diode-steered turn-off, Kelvin resistor, 10 k + 1 nF at the pins) and, for any T-type variant, its common-drain middle switch: 5 isolated bias supplies for 12 channels.
8. Keep B documented as the fallback with its preconditions: a firmware two-level (or double-signal PWM) mode for the low-PF corner, 1200 V middle devices, a T-type state interlock, and cosmic-ray data from the SiC supplier (RFQ question to Sichain and the others).  Revisit if efficiency is ever sold: B saves about 0.4 %-point at full load.
9. Keep A's 5 + 5 film bank: per ampere of phase current it matches Wolfspeed's two-level CRD200DA23N-GMA (0.051 against 0.060 capacitors per A, both ripple-current sized; A's capacitors run at 26 of 40.2 A) - it is not oversized; Wolfspeed's 0.81 uF/kW is lower only because its 1.1 kV capacitors carry the same current with less capacitance.
10. Fix the Wolfspeed rows of sim/data/reference_magnetics.csv when its choke data arrive (core, turns, wire are offered on request through Wolfspeed's forum, UG p15); until then the AP method above is calibrated on its 700 g only.

## Not verified / unknown

- Every number of ours is CALCULATED or ESTIMATED; nothing is bench-validated.  Wolfspeed's numbers are its own (labelled).
- Standards clauses (IEEE 1547-2018 reactive capability, EN 50549-1, GB/T 34120), the double-signal / virtual-vector PWM costs, the paralleling application notes, the amorphous loss fit and inverter field failure rates are quoted from memory.
- The LFP voltage profile, the die areas, the 650 V voltage-fraction scaling and all inductor material prices are assumptions.
- B's HF ripple current per DC-link half was not computed (taken as A's 5 per half); the two-level fallback's L1 core loss and its control were not simulated.
- Wolfspeed's firmware (dead time, modulation details, which trips act in hardware) is not on file; the L1/L5 alternate footprints and the C21 value (schematic 2.2 uF, BOM 0.1 uF) are read as noted.

