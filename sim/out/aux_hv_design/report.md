# AUX-HV design report, rev C1 (CALCULATED / SIMULATED - not bench-validated)

Written by `sim/aux_hv_design.py`. Requirement: ECO-06 (HV bootstrap supply), candidate PV-C2 (start <= 250 V). Schematic values: `aux_hv_spec.json` -> `gen/aux_hv.py`. Rev C: sourcing policy D-031 (Asian switch and parts, sec. 9 and 14), the final SYS-IO-AUX rev E feed (D-034, sec. 6), the insulation coordination D-032 / IC-15 / IC-17 (sec. 2, 11), no-load and standby power (sec. 9). Rev C1: the magnetics design rev M1 of T1 (A-33). Rev B (reviews AX-01..05, INT-08..13) in sec. 0b.

## 0. Rev C1 changes (T1 = magnetics design rev M1)
| item | change | numbers |
|---|---|---|
| T1 | DMEGC EC34A (ETD 34/17/11 class) DMR95; replaces the rev C0 ETD 29 / N97 interim build | loss at 30 W 0.91-1.15 W (copper x1.51: OpenMagnetics), 79 C at 60 C; leakage 31.6 uH (worst 47.4 uH, A-1; rev C0 26.1 uH); switched capacitance 32.9 pF vs 38.5 pF limit; 259 mT on A_min at the highest trip vs 349 mT; cost estimate 5.9 USD |
| efficiency | interim 2.3 W winding loss replaced by the design | 80.8 / 84.0 / 83.5 / 82.2 / 80.4 at 200-1000 V: 80 % target at 400-800 V met |
| drain clamp | 2 x SMCJ100A -> 3 x SMCJ70A (the M1 leakage is +21 %: with two TVS the worst-leakage and hiccup cases reach ~155 / ~164 C) | TVS 99 C at 30 W, 120 C worst leakage, 126 C in hiccup (limit 150 C, Bourns TJ max); V_DS 1323 V at 1000 V (limit 1360 V), 1428 V at 1100 V (limit 1530 V) |
| output polymer | 35 V -> 50 V (PCV1HVF101MB12FV-WE3, same series, 100 uF): the larger leakage spike on the aux winding widens the INT-08 backup band | backup band 23.82-30.97 V (rev C0 23.5-28.4 V) vs 42.5 V (85 % of 50 V); SYS-IO-AUX's feed input must tolerate it |
| rating | same 30 W continuous | 38 W for <= 91 ms; overload timer 91-189 ms |

## 0a. Rev C changes
| item | change | numbers |
|---|---|---|
| switch | IV2Q171R0D7Z (primary, AEC-Q101) / SG2M1K0170J2J (alternate, same pin-out); design takes the worse figure | switch loss at 1000 V 2.25 / 1.48 W, Tj 143 / 116 C; efficiency at 1000 V 80.4 / 82.1 % |
| gate drive | follower reference BZX84-B16 -> BZX84-A18, VDD clamp B18 -> B20 | VGS 15.5-18.1 V over -40..85 C (both makers recommend 15-18 V); hold-up 17.6 ms with 16.5 nC |
| SYS rev E feed | black start one ramp, hand-over within 6 ms, 27.8 / 30.15 W load | black start <= 37.3 W, hand-over <= 38.0 W (30.15 W case incl.), timer node <= 2.35 V vs 2.47 V lowest trip, bus >= 16.32 V; rating 30 W continuous stays. Stress case black start with 0.7 A held coils (not possible: contactors open): no trip |
| T1 insulation | coordinated levels in the T1 spec (rev C0: 2 tapes each side of the TIW, impregnation; rev C1: the design M1 build, sec. 11) | PD <= 10 pC at 2461 Vpk, 4400 V rms 60 s, 8 kV |
| T1 magnetics (MG-11/12/13) | rev C0: interim 2.3 W winding loss; requirements handed to the magnetics design | winding loss <= 1.52 W; rev C1 numbers in sec. 0 |
| standby | no-load / standby input power added | 0.80-1.30 W no load, 17.0-18.7 W at 13.9 W standby |
| sourcing | Asian parts where the datasheet covers the circuit's needs | sec. 14 |
| part stress | new `vrange` check in gen/aux_hv.py (VDC table) + this calculation for switching nodes | rev B values over the limit: R_AF2 10k 0603 and R_ZREG 10k 0603 (101 mW each, limit 60 mW), R_PGR 4.7k 1206 (176 mW, limit 150 mW) -> 22k (with 470 pF, same time constants), 1206, 10k; 9 switching / tap parts checked here: all inside |

## 0b. Rev B resolutions (kept)
| item | resolution | numbers |
|---|---|---|
| AX-01 surge | 2 x 22R per port + 2 x 3.3 uF film absorb the SPD residual (no clamp can stand off 1100 V and clamp <= 1.25 kV); TLV3202 OV lockout stops switching | HV_BULK 1199 / 1116 / 1158 V (4 kV/20 us, 2.75 kV/20 us, 2 kV/50 us from 1000 V); lockout 1114-1170 V (release 1119 V); V_DS at the stop 1498 V (88.1 %), afterwards = HV_BULK |
| AX-02 port-B fuse | resistors AHEAD of each fuse: fault current <= 25 A, so the 20 kA rating is irrelevant for the 50 kA port | clearing 24 J per AC10 vs ~57 J pulse rating |
| AX-03 staircase | foldback: aux-plateau detector adds C_TX on RT/CT below 7.7-10.1 V output | fsw 64.9 -> 5.9 kHz (max 6.8, staircase-free up to 14.3 kHz at 1100 V); ngspice sec. 10 |
| AX-04 overload | COMP-level timer (TLV3202 ch 2) latches SS low -> hiccup | trips above 34 / 52 / 88 W (min/typ/high corner) after 91-189 ms; never below the 30 W need (COMP 3.73 V < 3.89 V) |
| INT-08 backup band | aux sense 1k / 10n, R_FBT 105k (0.1 %), computed tracking budget (rev A: flat +/-5 %) | 23.82-30.97 V, 23.82-28.15 V loaded >= 13 W; above 23.97 V the SYS feed OV isolates it before the 26.13 V bus cut-off |
| INT-13 PG | TPS3700 -> SN74LVC1G17 (Ioff) on a 3.3 V Zener rail, 1k series | high 2.51-2.77 V; dead / unplugged = 0 V via the SYS 100k |
| INT-11 DAB | same board, port-B input parts not fitted | bom/AUX-HV_DAB_BOM.csv |
| INT-09/10 | 13 W standby case in loop and start-up; hand-over 34 W | sec. 6 |
| AX-05 | 10k from VDD_E to PGND (100k would let 330 uA at 125 C lift V_E to 33 V) | V_EB 0.4 / 3.3 V at 85 / 125 C |
| SYS rev E | output in 22.40-23.15 V; overshoots below the latched feed OV | feed UVLO max 21.49 V; max overshoot 23.20 V (margin 0.77 V to 23.97 V); hand-over bus min 16.32 V (> 15.48 V logic UVLO) |

## 1. Topology and controller
- Single-switch flyback, fixed 64.9 kHz (UCC28C59-Q1: oscillator 129.8 kHz halved by the internal T flip-flop, duty hard-limited to 47-48 %), DCM at full load down to 185 V with Lp at +7 %, peak current mode.
- Why: one 1700 V SiC switch (rev C: IV2Q171R0D7Z / SG2M1K0170J2J, the topology of Wolfspeed CRD-020DD17P-J) covers 200-1100 V without a high-side driver; DCM gives a first-order loop and no output-rectifier recovery; the 48 % duty limit removes slope compensation; the 16/12.5 V UVLO keeps the SiC gate above 12 V.
- Regulation: secondary ATL431 + CNY65B optocoupler (reinforced, VIORM 1800 Vpk), R-C feed-forward 33k + 39n from the output to the ATL431 REF so that the integrator cannot wind up during start-up or the cabinet hand-over. Backup: the controller's own error amplifier regulates the auxiliary winding at 23.82-30.97 V output if the opto loop opens.

| Vin | Ipk A | D | D2 | D + D2 | mode | efficiency |
|---|---|---|---|---|---|---|
| 200 V | 0.947 | 0.363 | 0.481 | 0.843 | DCM | 80.8 % |
| 400 V | 0.947 | 0.181 | 0.481 | 0.662 | DCM | 84.0 % |
| 600 V | 0.948 | 0.121 | 0.480 | 0.601 | DCM | 83.5 % |
| 800 V | 0.949 | 0.091 | 0.480 | 0.571 | DCM | 82.2 % |
| 1000 V | 0.950 | 0.073 | 0.479 | 0.552 | DCM | 80.4 % |

## 2. Transformer (CUSTOM, magnetics design rev M1: DMEGC EC34A set (DMR95)) - turns and Lp unchanged
| item | value |
|---|---|
| Np / Ns / Na | 90 / 15 / 16 (n = 6.00, aux ratio 1.067) |
| Lp | 1.148 mH +/-7 % (BCM boundary 1.229 mH at 185 V, 33 W) |
| AL / gap | 142 nH / 1.16 mm (design M1) |
| reflected voltage VR | 142 V (600 V, full load) |
| peak / RMS primary current | 0.947 A / 0.329 A at 200 V; 0.950 A / 0.148 A at 1000 V |
| peak / RMS secondary current | 5.55 A / 2.22 A |
| flux density | 124 mT pk at full load, 243 mT at the highest current-limit trip on A_e (limit 320), 259 mT on A_min (limit 349) |
| winding stack height | 5.78 mm of 7.15 mm window |
| leakage (A-1) | 1-D estimate 15.8 uH -> design 31.6 uH, worst 47.4 uH |
| switched winding capacitance (A-2, A-33) | 32.9 pF (rev C0 ETD 29 stack 36.3 pF) |
| transformer loss / temperature (A-33) | 0.91-1.15 W at 30 W, 16.2 K/W -> 79 C at 60 C |

**Magnetics review (MG-11/12/13, sim/out/magnetics/report.md) and the electrical requirements handed to the T1 construction (aux_hv_spec.json transformer.requirements):**

| requirement | value | status |
|---|---|---|
| Lp | 1.148 mH +/-7 % | agrees (magnetics check 1.137 / 1.236 mH) |
| turns ratio n / aux ratio | 6.00 / 1.0667 (Np 90, Ns 15, Na 16) | design M1: same |
| peak current full load / highest trip | 0.95 / 1.74 A | - |
| flux at the highest trip on A_min (MG-12) | <= 349 mT (0.85 x Bs 100 C) | design M1: 259 mT - meets, vendor to measure L(I) to 1.86 A at 120 C |
| winding loss at 30 W (MG-11) | <= 1.52 W (efficiency >= 80 % at 400-800 V: <= 1.68 W; current limit above the SYS black-start peak: <= 1.52 W) | design M1: 1.04 W (design M1 at 30 W: 0.69 W own / 1.04 W OpenMagnetics (the higher is used)) - meets |
| switched winding capacitance | <= 38.5 pF (the Asian switch's turn-on loss; was 40) | design M1: 32.9 pF |
| leakage (S shorted) | <= 34.0 uH | design M1: 31.6 uH (estimate) |
| insulation (MG-13 / IC-15) | PD <= 10 pC at 2461 Vpk on every unit, 4400 V rms 60 s, 8000 V impulse, creepage / clearance as sec. 11 | design M1: levels carried, potted |

- The Asian switch changes one requirement: the switched winding capacitance budget falls from 40 pF to 39 pF (its larger Eoss uses the turn-on loss margin). Rev C1 reads the design file `sim/out/magnetics/design_aux_hv_transformer.json` (rev M1) for every T1 figure (A-33).

Winding specification (also the T1 description in the schematic): CUSTOM flyback transformer AUX-HV T1 rev M1 (proposed construction - calculated, not built or measured; build to the winding sheet sim/out/magnetics/spec_aux_hv_transformer.md). DMEGC EC34A (ETD 34/17/11 class) DMR95, centre-leg gap 1.16 mm, 90:15:16 in the converter engineer's order P1|SH|S|AUX|P2, copper set back 2.0 mm from the gapped leg (1.0 mm former wall + 1.0 mm spacer), secondary in TIW-litz, vacuum-potted in a case. Core DMEGC EC34A set (DMR95), ETD 34/17/11 equivalent (Ae/Amin/le within 1 % of the MAS ETD 34/17/11 shape), gap 1.16 mm (A_L 142 nH). Lp 1.148 mH +/-7 % at 10 kHz (gap ground to A_L); leakage (S shorted) estimate 31.6 uH, <= 34.0 uH. Windings in order: P1: 45 t enamelled Cu 0.30 mm grade 2, 1 layer, start = DRAIN pin (dot); SH: copper foil 0.05 mm, 1 t, open with insulated overlap, -> PGND pin; S: 15 t TIW-litz 64 x 0.10 mm (three-layer extruded insulation certified for reinforced insulation, IEC 61558-2-16 / IEC 62368-1 Annex J), 1 layer, start = rectifier anode (dot), insulation unbroken to the pin, exits in PTFE sleeve; AUX: 16 t enamelled Cu 0.25 mm spaced over the breadth, start = aux rectifier anode (dot), finish = PGND; P2: 45 t enamelled Cu 0.30 mm grade 2, 1 layer, finish = HV_BULK pin. Insulation: VACUUM-POTTED (epoxy, class F, void-free) in a case - REQUIRED for the PD routine test; reinforced insulation = TIW-litz three-layer extruded insulation + 4 layers 0.06 mm polyester tape on each side of S; tapes P1-SH 12 L set the switched capacitance; primary pins (P1, P2, AUX, SH) on one row, secondary pins on the opposite row; the core is a primary-side conductive part. Creepage: 20 mm along the case / former between the pin rows (PD2, CTI unknown -> IIIa); 10 mm if the former and case material has CTI >= 600 (group I); inside the potting PD1: 6.4 mm; clearance >= 10.4 mm. ROUTINE test (every unit): PD <= 10 pC at 2461.0 V pk (extinction >= 1968.0 V pk), primary+aux+shield to secondary. TYPE tests: AC 4400.0 V rms 60 s; impulse 8000.0 V 1.2/50; thermal run at 30 W. Working voltage 1000 V DC, recurring peak 1323 V at 65 kHz (1428 V at the 1100 V trip). Peak primary current 0.95 A (limit max 1.74 A), 259 mT on A_min at the limit.

## 3. Voltage stress (rule: V_DS <= 80 % of 1700 V at 1000 V continuous, <= 90 % at 1100 V transient)
| node | value | limit | use |
|---|---|---|---|
| V_DS 1000 V, full load, TVS hot + VBR max + 40 V overshoot | 1323 V | 1360 V | 77.8 % |
| V_DS 1100 V, at the highest current-limit trip | 1428 V | 1530 V | 84.0 % |
| V_DS at the highest OV-lockout level (1170 V) + 1 us surge ramp, highest trip | 1498 V | 1530 V | 88.1 % |
| V_DS locked out at the surge peak (= HV_BULK) | 1292 V | 1700 V | 76.0 % |
| clamp (TVS stack) minimum vs VR | 233 V vs 142 V | > 1.4 x VR | 1.64 x |
| clamp diode BYG10Y reverse | 1388 V | 1600 V | 87 % |
| output rectifier VS-8ETU04S (+25 % ring) | 258 V | 400 V | 64 % |
| aux rectifier US1G (+25 % ring) | 276 V | 400 V | 69 % |
| start-up FET BSS126 (each of 3), at the surge peak | 431 V | 600 V | 72 % |
| HV string CRHV2512 (each of 3), at the surge peak | 431 V | 3000 V | 14 % |
| input OR diodes BYG10Y | 1100 V | 1600 V | 69 % |
| bulk film C4AQ, surge peak | 1199 V | VNDC 1300 V @ 70 C | 92 % |
| AC10 input resistor, surge (each of 2) | 1500 V pulse | 4500 V (<= 0.2 ms) | 33 % |
| gate drive (VDD) | 18.1 V nominal, Zener clamp 17.6-18.4 V | +12..+18 V static, +20 V transient | - |

## 4. Input: fuses, current-limiting resistors, surge absorber, OV lockout (AX-01, AX-02)
- Per port: stud -> 2 x 22R AC10 (pulse-rated wirewound) -> 1 A / 1000 VDC gPV fuse -> BYG10Y OR -> HV_BULK; 2 x 3.3 uF / 1300 V film (C4AQ) + 2 x 22 nF 2 kV. Tau 292 us, filter corner 546 Hz.
- AX-02: any fault behind the resistors (shorted film, MLCC, OR diode or switch path) is limited to 25 A, so the fuse interrupts 25 A at 1000 VDC instead of the port's 50 kA (its 20 kA rating is no longer the limit). Clearing energy per resistor 24 J (A-19) vs ~57 J AC10 single-pulse capability (p6, read off) = 43 %. The stud-to-resistor copper is the port conductor itself and is protected like the harness by the module's port fuse. A 22 R AC05 (rev A part) would only take ~11 J.
- Hot plug at 1100 V: 25 A peak, I2t 0.091 A2s = 16 % of the melting I2t (A-19 limit 20 %), 2.00 J per resistor. Operating loss at 200 V full load 0.85 W per resistor (8.4 W rating).
- Surges (A-20, switch locked out):

| residual | from | HV_BULK peak | dV/dt | peak current | per resistor | fuse I2t |
|---|---|---|---|---|---|---|
| SPD Up 4 kV / 20 us (A-20) | 1000 V | 1199 V | 10.3 V/us | 68 A | 1500 V, 2.0 J | 0.093 A2s |
| SPD Up 4 kV / 20 us (A-20) | 1100 V | 1292 V | 9.9 V/us | 66 A | 1450 V, 1.9 J | 0.087 A2s |
| review 2.75 kV / 20 us | 1000 V | 1116 V | 6.0 V/us | 40 A | 875 V, 0.7 J | 0.032 A2s |
| review 2.75 kV / 20 us | 1100 V | 1209 V | 5.7 V/us | 38 A | 825 V, 0.6 J | 0.028 A2s |
| review 2 kV / 50 us | 1000 V | 1158 V | 3.4 V/us | 23 A | 500 V, 0.6 J | 0.026 A2s |
| review 2 kV / 50 us | 1100 V | 1242 V | 3.1 V/us | 20 A | 450 V, 0.5 J | 0.021 A2s |

- Why no clamp on HV_BULK: a TVS or MOV that must not conduct at the 1100 V transient clamps at >= 1.4-1.6 kV at tens of amps, so it cannot hold <= 1.25 kV; the RC absorber keeps HV_BULK at 1199 V (1300 V film rating, dV/dt 10 V/us vs 80 V/us, 34 A per film vs 264 A) and the OV lockout makes the switch see only HV_BULK. The review's slow 1.5 kV swell is beyond the port rating (SPD Ucpv 1000 V): switched off, V_DS = 1500 V (88 %), cascode 500 V per FET (83 %), film 1.15 x VNDC (inside the 1.5 x VNDC surge rating, max 10 events).
- OV lockout: compensated string (3 x 10M || 100p, R_D1 11.5k || 68n, R_D2 53.6k || 15n, under-compensated: fast-step ratio 1.09-1.33 x DC, so a surge trips earlier) -> 1k / 220 pF -> TLV3202 1IN-; 1IN+ = primary ATL431 2.5 V via 10k, 1M hysteresis from 1OUT; 1OUT low pulls COMP below the 1.15 V offset through a BAT54. Trip 1142 V (1114-1170), release 1119 V (1090-1147): above the 1100 V transient, so AUX-HV keeps regulating through it. Response 1.0 us (A-23); at 10.3 V/us HV_BULK is 1170 V when switching stops -> V_DS 1498 V (88.1 % <= 90 %).
- Input capacitors fall below 60 V 3.2 s after disconnection while the start-up source cycles (calculated); through the string alone 578 s (warning label required).

## 5. Short circuit and overload (AX-03, AX-04)
- Staircase (A-21): per cycle at the current limit the minimum on-time adds Vin x t_on / Lp, a hard short resets only n x VF = 6.3 V reflected. At 74.2 kHz (highest fsw) the reset loses above Vout 4.7 V at 1100 V:

| Vin | t_on min | rise per cycle | reset per cycle (fsw max) | reset per cycle (folded, max) |
|---|---|---|---|---|
| 600 V | 0.25 us | +0.140 A | -0.078 A (staircase) | -0.868 A (bounded) |
| 600 V | 0.40 us | +0.225 A | -0.077 A (staircase) | -0.868 A (bounded) |
| 1000 V | 0.25 us | +0.234 A | -0.078 A (staircase) | -0.868 A (bounded) |
| 1000 V | 0.40 us | +0.375 A | -0.077 A (staircase) | -0.868 A (bounded) |
| 1100 V | 0.25 us | +0.258 A | -0.078 A (staircase) | -0.868 A (bounded) |
| 1100 V | 0.40 us | +0.412 A | -0.077 A (staircase) | -0.868 A (bounded) |

- Foldback: AUX -> US1G -> 4.7k -> 470p || 22k (tau 1.8 us charge / 10 us discharge) -> BZX84-B6V8 -> 2N7002 (Q_INV, gate 1M) -> FBK (47k to VREF) -> 2N7002 (Q_FB) switches 10n onto RT/CT: fsw 5.9 kHz (5.3-6.8), staircase-free up to 14.3 kHz. Engages below 7.7-10.1 V output (threshold 7.1-9.0 V on the detector, A-27) within ~45 us (3.4 cycles) of a hard short; holds off at light load down to ~1 W (detector 15.3 V at 3 W). Clamp spike on the aux (A-22, 25/50/75 %) lifts the detector only to 1.9 / 3.9 / 5.9 V. Folded power 3.3-12.6 W magnetising.
- In a sustained short the aux cannot hold VDD (folded spikes no longer pump VAUX), VDD runs down in 18 ms and the start-up recharges it in 0.44-1.86 s: hiccup with 1-4 % on-share; rectifier 12.0 W during the on-time (hard short, folded, high corner), 0.46 W average -> 100 C.
- Overload timer: COMP -> 59k -> COMPF (100k || 2.2u to PGND) -> TLV3202 2IN+, 2IN- = 2.5 V; 2OUT latches through a BAS16 (low leakage: 1.2 / 11.3 % threshold error at 85 / 125 C, datasheet maxima; a BAT54 would leak ~20 uA at 85 C here) and crowbars SS with a 2N7002 until VDD reaches UVLO (latch reset), then the start-up source restarts it. COMP at 30 W in the worst corner (Lp/fs min, Acs max, offset +0.1 V, A-24) is 3.73 V; trip at 3.89 / 3.98 / 4.06 V COMP = 34 / 52 / 88 W output (lowest / typical / highest corner). In current limit COMP sits at 4.62-5.18 V and the timer trips after 91 / 129 / 189 ms; hiccup off-time >= 0.44 s.
- **Overload limit (state for SYS-IO-AUX): continuous load <= 30 W. The supply never trips below 34 W; above 34-88 W (corner-dependent) or in current limit it delivers for 91-189 ms and then hiccups.**

Temperatures at the limit (T_amb 60 C, A-9, A-26):

| case | switch (IV2Q171R0D7Z unless named) | VS-8ETU04S | SMCJ70A (each) | T1 |
|---|---|---|---|---|
| 30 W, 1000 V, typical | 143 C (2.25 W) | 100 C (1.07 W) | 99 C (0.71 W) | 79 C (1.15 W) |
| 30 W, 1000 V, worst leakage (A-1) | 143 C (2.25 W) | 100 C (1.07 W) | 120 C (1.08 W) | 79 C (1.17 W) |
| 30 W, 1000 V, alternate switch SG2M1K0170J2J | 116 C (1.48 W) | 100 C (1.07 W) | 99 C (0.71 W) | 79 C (1.15 W) |
| 30.2 W port hold (<= 200 s), 1000 V, worst leakage | 143 C (2.25 W) | 100 C (1.08 W) | 120 C (1.09 W) | 79 C (1.18 W) |
| 30.2 W port hold, 1100 V transient, worst leakage | 158 C (2.64 W) | 100 C (1.08 W) | 120 C (1.09 W) | 79 C (1.18 W) |
| just below trip, typical (52 W) | 147 C (2.36 W) | 134 C (1.99 W) | 128 C (1.23 W) | 93 C (2.01 W) |
| just below trip, high corner (88 W) | 165 C (2.82 W) | 189 C (3.49 W) **over 175 C** | 165 C (1.90 W) **over 150 C** | 104 C (2.73 W) |
| hiccup at the highest limit (117 W on, duty 28 %) | 95 C (0.84 W) | 118 C (1.37 W) | 126 C (0.71 W) | 77 C (1.03 W) |

- Rated 30 W (also with the A-1 worst leakage), the typical-corner trip level and hiccup at the highest limit stay inside every rating. Continuous load just below the trip in the high-tolerance corner (88 W) does not: the COMP level bounds the peak current to only ~+/-10 % (offset, gain), so between 30 W and that level AUX-HV relies on the 30 W load contract (D-022).

## 6. Start-up, SYS-IO-AUX rev E feed, black start, hand-over (D-034)
- Start-up current (3 x BSS126 cascode, bottom FET self-biased by 4.7k): 0.27-0.43 mA (A-13). VDD 56 uF effective. First start 3.0 s (typ) / 6.2 s (worst, follower BAT54 leaking 42 uA at 85 C) after the input passes the brown-in level. At 125 C that BAT54 would leak ~330 uA, more than the lowest start-up current: LAYOUT REQUIREMENT - keep the VDD follower (BC817, BAT54) away from T1, the SiC switch and the clamp, local <= 85 C (A-28). VDD hold-up 17.6 ms with the 16.5 nC gate charge of IV2Q171R0D7Z (design for the worse).
- Model: AUX-HV averaged (loop, soft start, current limit, foldback, overload timer, VDD) + the rev E feed as SYS models it (`hardware/SYS-IO-AUX/outputs/SYS-IO-AUX_aux_feed.json`): UVLO 20.09-21.49 V rising / 18.34-19.66 V falling, latched OV 23.97 V, HGATE ramp 16-63 V/s into 1.28-2.46 mF, source follower 3.0 V below the gate (A-25); post-inhibit load A-17. Corners: 'min' = least AUX-HV power, slowest soft start, earliest timer, fastest ramp into the largest bus; 'max' = the opposite with the slowest ramp into the smallest bus.

| case | corner | aux takes VDD | output max / min | bus min | AUX-HV peak | time > 34 W | timer node peak (trip 2.47-2.53 V) | settled | result |
|---|---|---|---|---|---|---|---|---|---|
| start, cabinet present (feed held off) | min | 12.3 ms at 14.5 V | 22.76 / - V | - | 0.1 W | 0 ms | 0.84 V | 0.05 s | ok |
| start, cabinet present (feed held off) | typ | 12.0 ms at 14.5 V | 22.76 / - V | - | 0.1 W | 0 ms | 0.68 V | 0.05 s | ok |
| start, cabinet present (feed held off) | max | 11.2 ms at 14.6 V | 22.76 / - V | - | 0.1 W | 0 ms | 0.58 V | 0.05 s | ok |
| black start, 27.8 W | min | 12.3 ms at 14.5 V | 22.83 / - V | - | 37.3 W | 47 ms | 2.32 V | 0.44 s | ok |
| black start, 27.8 W | typ | 12.0 ms at 14.5 V | 22.78 / - V | - | 35.1 W | 25 ms | 2.05 V | 0.84 s | ok |
| black start, 27.8 W | max | 11.2 ms at 14.6 V | 22.77 / - V | - | 34.2 W | 0 ms | 1.77 V | 1.62 s | ok |
| black start, port 0.7 A (30.2 W) | min | 12.3 ms at 14.5 V | 22.97 / - V | - | 39.5 W | 94 ms | 2.40 V | 0.44 s | ok |
| cabinet hand-over, 27.8 W | min | - | 22.83 / 21.43 V | 17.11 V | 35.6 W | 21 ms | 2.29 V | 0.10 s | ok |
| cabinet hand-over, 27.8 W | typ | - | 22.78 / 22.21 V | 16.75 V | 33.9 W | 0 ms | 2.02 V | 0.19 s | ok |
| cabinet hand-over, 27.8 W | max | - | 22.77 / 22.38 V | 16.32 V | 33.5 W | 0 ms | 1.74 V | 0.40 s | ok |
| hand-over, port 0.7 A (30.2 W) | min | - | 22.82 / 21.38 V | 17.05 V | 38.0 W | 74 ms | 2.35 V | 0.10 s | ok |

- SYS-IO-AUX's own numbers (rev E json): black start 34.8-38.0 W peak, 42-60 ms above 34 W, ramp 0.35-1.39 s; hand-over bus minimum 16.32-17.11 V, 34.1-36.3 W peak, conduction after 3.8-6.0 ms. This model: black start 37.3 W peak (min corner) for 47 ms above the lowest timer threshold 34.1 W, the timer node reaches 2.32 V against its lowest trip level 2.47 V - no trip, 6 % margin; hand-over bus minimum 16.32 V, AUX-HV output never below 21.38 V (feed UVLO falling max 19.66 V). **Confirmed**: the SYS figures hold; the differences (~0.7 W lower peak) come from the AUX-HV output voltage (22.77 V here, 23.15 V assumed there).
- **Rating: 30 W continuous at 60 C ambient stays the right rating.** The worst post-inhibit load is 27.8 W (13.9 W of it only while a port holds its contactors, <= 200 s; 13.9 W otherwise) and 30.15 W for <= 200 s with two battery-port hold coils - thermally the same point as 30 W (sec. 5: the switch loss does not depend on the load in fixed-frequency DCM). The black-start / hand-over peaks (<= 38.0 W, above 35 W for <= 74 ms) do not trip the overload timer (node <= 2.35 V vs 2.47 V). State it to SYS-IO-AUX as: 30 W continuous, 38 W (lowest current limit) for <= 91 ms, the timer ends anything longer.
- The timer margin is thin in the all-tolerances-low corner: the 30.15 W hand-over peaks the timer node at 95 % of its lowest trip level, and a black start with that port load (not possible: contactors open) hiccups. Continuous load above 34.1 W is ended by the timer - SYS-IO-AUX's hardware inhibit (D-034) is what keeps the load below 30 W.
- Light load (feed held off, only the SYS feed parts on the output): AUX-HV skips cycles at high line; the foldback may engage between bursts (harmless). No-load and standby input power: sec. 9.
- PG_HVAUX dips low for the few ms of a hand-over sag below 21.3 V: firmware should qualify it over >= 50 ms.

## 7. Output set point, regulation, backup band (INT-08) and PG (INT-13)
- ATL431BI + 137k / 16.9k (0.1 %): 22.770 V nominal, 22.40-23.15 V worst case (spec +/-3 % = 22.12-23.48 V); inside SYS window 21.6-26.0 V, above the feed UVLO (max 21.49 V) by 0.91 V, below the latched feed OV (min 23.97 V) by 0.82 V.
- Full 0 -> 30 W step (CTR low/typ/high): minimum 22.15/22.47/22.53 V with the 2 mF bus, 21.84/22.32/22.41 V stand-alone; overshoot on release <= 23.47 V (below the latched feed OV 23.97 V).
- Backup loop (opto failed): aux sense US1G + 1k / 10n into 105k / 10k (0.1 %). Tracking budget calculated over load 0.35-30 W, rectifier 25-125 C, clamp spike 25-75 % (A-22), US1G 0.55-0.80 V and VFB 2.45-2.55 V: **23.82-30.97 V**; 23.82-28.15 V while loaded >= 13 W through the feed, i.e. below the 26.13 V bus cut-off whenever AUX-HV carries the module (nominal VFB: 24.97-30.31 V). Load is the dominant term (the peak detector droops at light load, the rectifier VF rises at full load: +/-6 %), then VFB +/-2 % - aux sensing cannot do better. The low end stays 0.68 V above the regulation band. Idling unloaded (feed held off) the top reaches the value above, but the latched SYS feed OV (23.97-25.57 V) refuses such a feed, so the bus cut-off can no longer be reached through AUX-HV and the cabinet feeds carry on (INT-08); PG flags OV above 24.93 V.
- PG_HVAUX (INT-13): TPS3700 OUTA/OUTB (wired) pull PGW low outside 20.61-21.39 V (UV falling) / 24.93-25.54 V (OV); PGW has 10k to a 3.3 V rail (10k + BZX84-B3V3 from the output, 2.64-2.80 V); SN74LVC1G17 (Schmitt, Ioff) drives PG_HVAUX through 1k: high 2.51-2.77 V into the SYS 100k pull-down. AUX-HV dead or the cable unplugged: the buffer is unpowered (Ioff) and SYS reads 0 V; output below ~11.7 V: buffer supply below its 1.65 V minimum, the TPS3700 holds PGW low as soon as it runs (1.8 V).

## 8. Control loop (A-7, A-8)
- Fast-lane type II + feed-forward: K = CTR x R_PU / R_LED (2.2k / 560R), integrator zero 25 Hz (C_Z 47n), feed-forward C_FF/C_Z = 0.8 rolled off at 124 Hz, COMP pole 32.9 kHz, opto pole 5.0 kHz (pessimistic, A-7).
- 216 corners (Vin 200/600/1000 V, 100 %/43 % (13 W)/10 % load, resistive/constant-power, CTR low/typ/high, opto pole pessimistic/typical, with/without the 2 mF bus): crossover 67-2794 Hz, phase margin >= 51 deg (worst: 200 V, 100 % P load, CTR hi, opto pess), gain margin >= 16 dB.
- Backup (aux) loop with 22k + 150n / 1.5n on COMP-FB: crossover 58-136 Hz, phase margin >= 55 deg.
- Optocoupler DC point: LED 2.0 mA typ, up to 10.1 mA (R_LED <= 1.74k), ATL431 >= 0.78 mA, cathode >= 15.3 V.

## 9. Losses and efficiency at 30 W out (target >= 80 % at 400-800 V), design switch IV2Q171R0D7Z
| loss (W) at 30 W out | 200 V | 400 V | 600 V | 800 V | 1000 V |
|---|---|---|---|---|---|
| switch conduction | 0.18 | 0.09 | 0.06 | 0.05 | 0.04 |
| switch turn-on (C_oss + winding C) | 0.12 | 0.35 | 0.70 | 1.18 | 1.79 |
| switch turn-off | 0.12 | 0.19 | 0.26 | 0.34 | 0.42 |
| TVS clamp | 2.11 | 2.11 | 2.12 | 2.12 | 2.13 |
| output rectifier | 1.07 | 1.07 | 1.07 | 1.07 | 1.07 |
| rectifier RC snubber | 0.01 | 0.04 | 0.07 | 0.11 | 0.16 |
| transformer core | 0.03 | 0.05 | 0.07 | 0.09 | 0.11 |
| transformer copper | 0.97 | 0.86 | 0.90 | 0.96 | 1.04 |
| current-sense resistors | 0.08 | 0.04 | 0.03 | 0.02 | 0.02 |
| output capacitor ESR | 0.03 | 0.03 | 0.03 | 0.03 | 0.03 |
| controller + bias (VDD) | 0.18 | 0.18 | 0.18 | 0.18 | 0.18 |
| secondary bias (ATL431, opto, PG, preload) | 0.16 | 0.16 | 0.16 | 0.16 | 0.16 |
| HV divider string | 0.00 | 0.01 | 0.01 | 0.02 | 0.03 |
| input path | 2.07 | 0.53 | 0.27 | 0.18 | 0.13 |
| **total loss** | 7.14 | 5.69 | 5.92 | 6.50 | 7.30 |
| **efficiency** | **80.8 %** | **84.0 %** | **83.5 %** | **82.2 %** | **80.4 %** |

**Switch comparison (both Asian candidates, same circuit; the design takes the worse of each figure):**

| | IV2Q171R0D7Z (InventChip) | SG2M1K0170J2J (Sichain) |
|---|---|---|
| qualification | AEC-Q101 (datasheet p1) | none stated in the datasheet |
| R_DS(on) @15 V: 25 C typ / max, 175 C typ | 0.95 / 1.25 / 1.45 ohm | 1.19 / 1.70 / 2.20 ohm |
| Eoss at 1000 V / Coss / Crss | 11.0 uJ / 15.3 pF / 2.2 pF | 4.3 uJ / 9.5 pF / 1.4 pF |
| Qg / Rg,int | 16.5 nC / 13 R | 7.3 nC / 19 R |
| VTH min 25 C / min hot (A-30) | 1.80 / 1.00 V | 2.50 / 1.85 V |
| switch loss 600 V / 1000 V / 1100 V | 1.02 / 2.25 / 2.63 W | 0.66 / 1.48 / 1.76 W |
| of which Miller (0 V off, A-31) at 1000 V, full load / max limit | 0.75 / 3.51 uJ (VG 1.61 / 2.71 V) | 0.00 / 0.25 uJ (VG 1.38 / 2.50 V) |
| Tj at 1000 V, 60 C (35 K/W board + RthJC) | 143 C | 116 C |
| efficiency 400 / 600 / 800 / 1000 V | 84.0 % / 83.5 % / 82.2 % / 80.4 % | 84.5 % / 84.4 % / 83.5 % / 82.1 % |
| gate drive VDD 15.5-18.1 V vs recommended on | 15-18 V, abs -10..+23 V | 15-18 V, abs -8..+22 V |
| pulsed drain current vs highest limit 1.74 A | 15.7 A | 11.4 A |

- Both fit: V_DS is set by the clamp (sec. 3, unchanged, both are 1700 V parts), the pin-out is the same (TO-263-7: 1 gate, 2 Kelvin source, 3-7 source, tab drain), the 15.5-18.1 V gate drive (BZX84-A18 follower, rev C) is inside both recommended on-voltage windows. Neither maker's recommended negative off-voltage is available from the UCC28C59 single-rail output: the 0 V off is checked as the Miller term (A-31) - a single-switch flyback has no second switch whose dv/dt could turn it on, and the self-induced gate voltage only adds a small channel current during the drain rise. IV2Q171R0D7Z runs hotter (larger Eoss, the datasheet EOFF residual, lower threshold), so it sets the design figures; SG2M1K0170J2J is the cooler alternate but its datasheet states no qualification (SRC-2 needs the maker's reliability report before it is used).

**No-load and standby input power** (TI reference review: PMP41009 measured 1.3 W no-load at 1000 V, 0.3 W at 350 V; PMP41031 0.78 W at 1000 V - both quasi-resonant):

| Vin | no load (feed held off: SYS feed parts 0.15 W) | standby 13.9 W (post-inhibit, no port hold) |
|---|---|---|
| 200 V | 0.80 W (every cycle) | 17.1 W in, 81.7 % |
| 400 V | 1.11 W (every cycle) | 17.0 W in, 82.3 % |
| 600 V | 1.30 W (skip, 53.6 k pulses/s) | 17.4 W in, 80.4 % |
| 800 V | 1.24 W (skip, 30.2 k pulses/s) | 18.0 W in, 77.6 % |
| 1000 V | 1.22 W (skip, 19.4 k pulses/s) | 18.7 W in, 74.5 % |

- Night drain of a port-B battery feeding an idle AUX-HV (D-021/D-022): 0.8-1.3 W, i.e. 19-31 Wh per day; the 1700 V switch's turn-on loss (Eoss + winding capacitance at Vin) is most of it at high line.

## 10. ngspice (`sim/spice/aux_hv_*.cir`; behavioural RT/CT oscillator, T flip-flop, PWM latch; linear core)
| Vin | fsw calc / sim kHz | Ipk calc / sim A | V_DS pk calc / sim V | Vout calc / sim V | ripple sim mV | eff calc / sim % |
|---|---|---|---|---|---|---|
| 200 V | 64.9 / 64.8 | 0.947 / 0.933 | 451 / 445 | 22.770 / 22.782 | 59 | 80.8 / 86.1 |
| 600 V | 64.9 / 65.0 | 0.948 / 0.931 | 851 / 850 | 22.770 / 22.801 | 61 | 83.5 / 87.9 |
| 1000 V | 64.9 / 64.9 | 0.950 / 0.921 | 1251 / 1251 | 22.770 / 22.823 | 63 | 80.4 / 85.9 |
Tolerances asserted: fsw +/-3 %, Ipk +/-10 %, V_DS +/-5 %, Vout +/-1 %; foldback node FBK stays low at full load.

**Hard output short at 1000 V** (30 mOhm + 20 mOhm switch at 0.3 ms, from full load; `aux_hv_1000V_short*.cir`, `spice_short.png`):

| | peak primary current | V_DS peak | last 0.3-0.8 ms: Ipk / fsw |
|---|---|---|---|
| foldback disabled | 2.56 A (climbs cycle by cycle past the limit; settles only where the model's diode drop resets it - a real core saturates first) | 1260 V | 2.56 A / 65.0 kHz |
| with foldback | 1.64 A (first 0.3 ms: 1.64 A) | 1256 V | 1.53 A / 6.0 kHz |

**Surge at 1000 V** (4 kV / 20 us at the port, `aux_hv_1000V_surge.cir`, `spice_surge.png`): HV_BULK peak 1198 V (calc 1199 V), lockout at 12.9 us after the surge start, V_DS peak 1439 V (84.7 %), output dip to 22.32 V.

## 11. Insulation requirement for the layout (not PCB dimensions)
- Barrier: AUX-HV primary (all HV nets, referenced to the OR negative PGND) <-> PELV secondary (GND, +24V_HVAUX). Only T1 (transformer) and U-opto (CNY65B) cross it; no Y capacitor is fitted.
- Working voltage across the barrier: 1000 V DC continuous, 1100 V transient (port voltage, any port pole may be earthed); repetitive peak at the primary drain end up to the V_DS peak above PGND. Insulation class: REINFORCED, pollution degree 2 inside the enclosure, overvoltage category II (DC), altitude per PV-C6 (> 3000 m derating).
- Creepage (IEC 62477-1 / IEC 60664-1, reinforced = 2 x basic) - indicative values to be confirmed by the insulation coordination of ECO-10, the standards are not in this repository: PD2 at 1000 V: material group I about 2 x 5.0 mm, group II about 2 x 7.1 mm, group IIIa/b about 2 x 10 mm.
- Clearance: reinforced insulation is dimensioned for the rated impulse voltage one step above the basic value for a 1000 V DC OVC II circuit (indicative 8 kV -> about 8 mm in inhomogeneous field at <= 2000 m), multiplied by the altitude factor (about 1.14 at 3000 m, 1.29 at 4000 m) - again to be fixed by ECO-10.
- Optocoupler CNY65B (datasheet p4): clearance >= 14 mm, creepage >= 14 mm, insulation thickness >= 3 mm, CTI 200 (group IIIa), VIORM 1800 Vpk, VIOTM 12 kV. If PD2/group IIIa reinforced creepage (indicative 20 mm) applies, the package surface alone is short: coat the opto area to PD1 per IEC 60664-3 or confirm with the certifier.
- Transformer T1: reinforced insulation by triple-insulated wire on the secondary (certified for reinforced insulation at >= 1000 V DC working voltage), TIW insulation continuous to the pin, primary and secondary on opposite pin rows of coil former B66359B1013T001 (rows 25.4 mm apart), dielectric type/routine test per IEC 62477-1 for the working voltage above.
- PCB: keep HV primary copper and PELV copper apart by the reinforced creepage/clearance above, measured along the surface and through air; a slot under T1 and U-opto is allowed for clearance but does not replace the component's own creepage. Functional HV spacing inside the primary: the port-to-port OR (two ports may differ by up to 1100 V) and the resistor chain ahead of each fuse (up to 4 kV surge across a 2-resistor chain).
- **Conformal coating (IC-17, D-032): the board is conformally coated to pollution degree 1 (IEC 60664-3 type 1) over the HV-PELV barrier parts - T1, the CNY65B and their HV copper.** Coating does not reduce clearance.
- **T1 (IC-15):** design M1 (proposed construction - calculated, not built or measured): VACUUM-POTTED (epoxy, class F, void-free) in a case - REQUIRED for the PD routine test; reinforced insulation = TIW-litz three-layer extruded insulation + 4 layers 0.06 mm polyester tape on each side of S; tapes P1-SH 12 L set the switched capacitance; primary pins (P1, P2, AUX, SH) on one row, secondary pins on the opposite row; the core is a primary-side conductive part. Creepage 20 mm along the case / former between the pin rows (PD2, CTI unknown -> IIIa); 10 mm if the former and case material has CTI >= 600 (group I); inside the potting PD1: 6.4 mm; clearance >= 10.4 mm. The rev C0 build (round TIW, impregnated) is superseded. Whether the build is PD-free at 2461 Vpk is decided by the routine test on every unit - no TIW datasheet gives a PD inception voltage for this construction.

## 12. Assumptions (ours)
- **A-1** Leakage inductance = 2.0 x the 1-D MMF estimate of the winding stack (terminations, uneven layers); 3.0 x for the stress and clamp-power worst case. Not measured: a transformer sample is required.
- **A-2** Primary winding switched capacitance: parallel-plate estimate (eps_r 3.2) of the drain-end layer P1 to the PGND shield and of P2 to the aux layer, each weighted by the mean square of its own swing along the layer (P1 1.0 -> 0.5, P2 0.5 -> 0 of the drain swing).
- **A-3** TVS breakdown rises +0.1 %/K (no coefficient on the Bourns sheet); hot case = 100 C junction.
- **A-4** 40 V drain overshoot above the TVS level: BYG10Y forward recovery (not specified) + clamp-loop inductance.
- **A-5** Output-rectifier ringing with the RC snubber adds 25 % to the ideal reverse voltage.
- **A-6** Aux tracking for the backup loop is CALCULATED (sec. 7): aux plateau = Na/Ns x (Vout + rectifier VF(i) + i x Rsec), US1G detector drop 0.55-0.80 V at mA, and 25-75 % of the primary clamp spike (Vclamp - VR) x Na/Np appearing on the aux for the leakage-reset time.
- **A-7** Optocoupler CTR at IF 1-5 mA for the B bin: 0.6 x (1.0..2.0), x 0.88..1.0 temperature, x 0.7 end of life; opto pole estimated as 110 kHz x 100 R / R_pullup (scaled from the only datasheet cut-off figure).
- **A-8** UCC28C59 error-amplifier source current up to 2 mA (datasheet gives min 0.5 / typ 1 mA, no max).
- **A-9** Thermal resistance to ambient through board copper: TO-263-7 and D2PAK 35 K/W, SMC clamp TVS 40 K/W lead-to-ambient on >= 4 cm2 2-oz copper per device (LAYOUT REQUIREMENT), transformer 21 K/W (53 x Ve[cm3]^-0.54 empirical) - estimates for a 2-oz board with local pours.
- **A-10** MLCC capacitance under DC bias: 10 uF/50 V X7R 1210 keeps 70 % at 15 V and 50 % at 23 V.
- **A-11** SiC turn-off loss = datasheet EOFF scaled linearly with V and I and with total gate resistance.
- **A-12** Gapped-core AL tolerance +/-7 % (ground gap, vendor-trimmed) for the inductance window.
- **A-13** BSS126 square law I = K (VGS - VGS(th))^2 with K from IDSS(min) 7 mA at VGS = 0 for each VGS(th).
- **A-14** DCM turn-on happens at a random point of the drain ring: mean V^2 = Vin^2 + 0.25 VR^2.
- **A-15** Copper 100 C; primary 0.30 mm grade-2 wire OD 0.342 mm; TIW 0.80 mm conductor, OD 1.00 mm; polyester tape 0.06 mm per layer.
- **A-16** VDD regulator: BZX84-B16 runs at ~0.7 mA, 0.3 V below its 5 mA value; BAT54 0.25-0.40 V at 5 mA.
- **A-17** Loads behind the SYS-IO-AUX rev E feed (D-034, hardware inhibit while AUX-HV is the source; SYS-IO-AUX_aux_feed.json): SYS logic + BMU-GW 6.24 W constant power above 6 V; CTRL 7.56 W constant power and the port board 0.6 A (0.7 A with two battery-port hold coils) above the +24V eFuse UVLO (15.77-16.41 V rising, 14.61-15.42 V falling); feed parts 0.145 W directly on the AUX output. Total 27.8 W at 23.15 V (30.1 W with 0.7 A, <= 200 s), 13.9 W without a port hold.
- **A-18** VDD consumption during start-up: UCC28C59 2 mA (max) + 1.25 x gate charge + COMP pull-up + 2 mA EA source (A-8) + LINE_OK/soft-start/start-up-disable/reference/comparator loads; slowest soft start uses the 0.5 mA EA minimum.
- **A-19** Fuse clearing I2t at 1000 VDC for the resistor-limited (<= 25 A) fault = 2 x the 20 kA melting I2t (arc included); repetitive inrush pulses kept <= 20 % of the melting I2t (our derating rule, the Schurter sheet has no pulse-life curve).
- **A-20** Port-SPD let-through modelled as a rectangular differential residual of Up = 4 kV for 20 us on top of the operating voltage (pessimistic: the real residual is a short peak), plus the review cases 2.75 kV / 20 us and 2 kV / 50 us; the whole residual appears across the AUX-HV input.
- **A-21** Minimum on-time in a short 0.25-0.40 us (CS filter + tD + SiC turn-off, review AX-03); secondary reset voltage in a hard short = output-rectifier VF at the limit current (hot), no wiring drop credited.
- **A-22** Clamp spike seen by the aux winding = 50 % (range 25-75 %) of (Vclamp - VR) x Na/Np for the leakage-reset time: the aux sits between S and P2 and links the P1-S leakage field only.
- **A-23** Compensated tap string: C_STR 100 pF +/-5 % C0G, bottom capacitors +/-5 % C0G; CRHV self-capacitance negligible; lockout response 1.0 us (1k/220p filter 0.22 us + TLV3202 tPD + COMP discharge + tD + gate).
- **A-24** COMP-to-CS offset 1.15 V +/-0.10 V (SLUSEV2C p7 gives a typical value only).
- **A-25** SYS feed switch (LM74800 rev E) modelled as SYS does: enabled T_DRV (0.94 ms) after the AUX output passes the UVLO rising level (or 1.08 ms after cabinet loss), HGATE then ramps at 16-63 V/s from the bus level and the bus follows 3.0 V below it (source follower) until the FET is fully on; opens 6 us after the AUX output falls below the UVLO falling level; latched OV above 23.97 V.
- **A-26** Overload thermal: two-node model per device - junction-to-lead/case Rth (datasheet) follows the instantaneous power within the 0.1-0.2 s timer on-time, board Rth (A-9) follows the hiccup average.
- **A-27** BZX84-B6V8 at 3-7 uA (foldback threshold): 6.0-6.9 V (Fig. 8 typical 6.3-6.6 V, B grade, -40..85 C).
- **A-28** Primary control area (controller, VDD follower, TLV3202 timer) <= 85 C local, which the CNY65 next to it needs anyway (its rating): BAT54 leakage 42 uA typ, BAS16 <= 1 uA; 125 C shown as a check.
- **A-29** SiC turn-off loss (replaces A-11): the channel overlap only. A datasheet EOFF larger than EOSS at its test voltage includes the Coss charge (stored, dissipated later at turn-on and counted there), so the channel part is EOFF - EOSS(test V), not scaled down with current (InventChip Fig. 21 is flat 1-2 A); a datasheet EOFF below EOSS is taken as the channel part (scaled with V and I). Plus the Miller term A-31.
- **A-30** Threshold at temperature: the 25 C minimum shifted by the typical drop to 150 C read off the maker's curve (IV2Q 1.8 - 0.8 = 1.0 V; SG2M 2.5 - 0.65 = 1.85 V); no maker states a hot minimum.
- **A-32** Transformer winding loss, interim (MG-11): 2.3 W at full load (the magnetics check's independent figure with gap fringing, 2.1-2.3 W; OpenMagnetics 5.6 W), scaled with Ipk^2 at other loads and counted on the secondary side (more magnetising power - conservative for the current limit). Rev C1: used only to state the winding-loss requirement (t1_budget); the losses come from A-33.
- **A-33** Rev C1 transformer = magnetics design rev M1 (design_aux_hv_transformer.json): core loss from its own iGSE table, copper from its own table x the OpenMagnetics / own ratio at 1000 V (the higher model), both interpolated in V_in and scaled with Ipk^2 (the file's note: ~P_out); leakage 2 / 3 x its 1-D value (A-1); switched capacitance = its P1-SH value x this model's / its value on the rev C0 stack (36.3 / 27 pF); thermal resistance = its temperature rise / loss (potted case, natural convection).
- **A-31** 0 V turn-off (UCC28C59 output, no negative rail): the drain rise induces Crss x dV/dt x R_off on the gate (R_off = R_G + Rg,int + 15 R driver pull-down max); above VTH(min, hot) the channel carries k (VG - VTH)^2 (k from the maker's 175 C transfer curve), solved self-consistently with dV/dt = (Ipk - I_ch) / (Coss + C_W); its energy is added to the turn-off loss.

## 13. Not verified / open
- Leakage, winding capacitance, the aux tracking and the clamp spike on the aux (A-22) are estimates: build a T1 sample (design M1), measure Lp, Llk, Cpri, PD and the aux waveform; check the foldback detector and the backup band (23.8-31.0 V, driven by the leakage) against the real spike. Own and OpenMagnetics copper loss differ by x1.5 and leakage by x4 - the sample decides.
- IV2Q171R0D7Z: 143 C junction at 1000 V / 60 C with the A-9 board estimate (35 K/W): little margin to our 150 C limit - confirm the copper area in layout; SG2M1K0170J2J's datasheet states no qualification.
- The SiC threshold at temperature (A-30), the datasheet EOFF interpretation (A-29) and the 0 V turn-off Miller term (A-31) are estimates from the makers' curves; measure the turn-off on a bench at 1000 V hot.
- Short at 1100 V on a T1 sample (saturation is not in the ngspice model): the first 3.4 cycles before the foldback engages still climb (sec. 10).
- Continuous load between 30 W and the timer threshold is not prevented by AUX-HV in every corner (the COMP level only bounds the peak current to ~+/-10 %); SYS-IO-AUX's hardware inhibit (D-034) holds it <= 30.2 W.
- CNY65 local ambient must stay <= 85 C (its rating); the module ambient is 60 C (PV-20).
- DAB-D60 build: port B is not fitted, so the port-to-port isolation is untouched; the DAB cannot black-start from port 2 (battery) without cabinet 24 V (accepted in D-021).
- The EA source current maximum is not specified (A-8); the opto is sized for 2 mA.

## 14. Sourcing (D-031, SRC-1..4)

| part | old | new (rev C) | why / why not | price (gen/data/prices.csv, 1 pc) |
|---|---|---|---|---|
| 1700 V SiC switch | Wolfspeed C3M0900170J-TR | InventChip IV2Q171R0D7Z; alternate Sichain SG2M1K0170J2J | both pin-compatible, both fit (sec. 9); IV2Q is AEC-Q101 and the hotter one, so it sets the design; SG2M states no qualification; neither is on the LCSC mirror (RFQ) | 1.83 USD (Richardson) (old) / RFQ (new) |
| input film 3.3 uF 1300 V (x2) | KEMET C4AQUBU4330A11J | Jianghai FCSA3DS335K050ID90BE3 | same 3.3 uF / 1300 V (70 C) / 27.5 mm; IEC 61071, 80 V/us and 264 A vs 29 V/us and 95 A; not on the LCSC mirror, maker's distribution (Jianghai Europe) | - (old) / RFQ (new) |
| output polymer 100 uF (x3) | Wurth 875115655003 (35 V) | Jianghai PCV1HVF101MB12FV-WE3 (50 V, rev C1) | 25 vs 30 mOhm, 3.8 vs 2.8 A; 50 V because the M1 leakage lifts the backup band to ~31 V (rev C: PCV1VVF101MB70FV-WE3 35 V); 3000 h vs 5000 h at 105 C - at ~65 C and a fifth of the ripple rating not life-limiting; RFQ | - (old) / RFQ (new) |
| PWM controller | TI UCC28C59QDRQ1 | keep (candidate ROHM BD28C59FJ-LB, JP) | ROHM has the same 16 / 12.5 V UVLO and 50 % duty (industrial rank); adopting it means re-deriving the COMP offset / gain, CS delay and oscillator data the loop, current limit and timer use - next rev | 2.06 USD (TI.com) |
| dual comparator | TI TLV3202AIDGKR | keep (candidate 3PEAK TP1942-SR) | TP1942: same pin-out, VOS 3 mV, 46 uA, LCSC C248572 - but VOL is specified only at 1 mA; the OV lockout must sink ~4.3 mA out of COMP below 1.15 V (TLV3202: <= 0.225 V at 4 mA) | 1.63 USD (TI.com) |
| shunt reference (x2) | TI ATL431BIDBZR | keep (candidate SGMICRO SGM431B) | SGM431B Imin 0.7 mA, Iref 2 uA, VI(dev) 25 mV: widens the 22.8 V set point by ~1 % and needs twice the bias - the low-power fast-lane loop relies on ATL431's 35 uA / 150 nA | 0.60 USD (TI.com) |
| window / line detectors | TI TPS3700DDCR | keep (candidates SGMICRO SGM882 / SGM883) | pin-compatible, 400 mV reference, but 27.5 mV internal hysteresis moves the PG release to ~22.8 V (above the 22.40 V regulation minimum) and the brown-in window - needs a redesign of both windows | 2.06 USD (TI.com) |
| line detector | TI TPS3710DDCR | keep (candidate SGMICRO SGM883) | as above | 1.60 USD (TI.com) |
| PG buffer | TI SN74LVC1G17DBVR | keep | 0.08 USD; the UMW copy on LCSC was not checked for Ioff | 0.11 USD (TI.com) |
| rectifiers | Vishay BYG10Y-E3/TR | keep (candidate TSC S1YH, AEC-Q101) | Taiwan Semiconductor datasheets found; 1600 V avalanche / IFSM / slow recovery for the clamp not checked in rev C | 0.14 USD (LCSC) |
| aux rectifiers | Diodes US1G-13-F | keep (candidate TSC US1GH) | not checked in rev C (cents) | 0.09 USD (LCSC) |
| output rectifier | Vishay VS-8ETU04S-M3 | keep (candidate TSC SFAS806GH) | 600 V superfast, higher VF; 8 A FRED VF / trr comparison not done in rev C | - |
| clamp TVS (x3) | Bourns SMCJ100A (x2) | Bourns SMCJ70A (x3), rev C1 (candidates TSC SMCJ70AH, JSCJ) | rev C1: the M1 transformer's leakage needs the clamp heat spread over three packages ; Asian equivalents and an LCSC listing for SMCJ70A not checked | - (old) / RFQ (new) |
| small signal | Nexperia BAT54 / BAT54S / BAS16 / BZX84 / BC817 / 2N7002BK | keep | cents; the design uses their hot-leakage, B/A-grade tolerance and 6V8 low-current curves (JSCJ / LRC / TSC equivalents exist on LCSC, not checked against those curves); Nexperia is owned by the Chinese Wingtech group | 0.05 USD (LCSC) |
| depletion FET | Infineon BSS126H6327XTSA2 | keep | no Asian 600 V depletion SOT-23 found quickly | 0.18 USD (LCSC) |
| pulse resistor (x4) | Vishay AC10000002209JAB00 | keep | no Asian cemented wirewound datasheet with a single-pulse energy curve found (24 J fuse-clearing energy) | 0.93 USD (Octopart) |
| HV string resistor (x3) | Vishay CRHV2512AF10M0FKFB | keep | 3 kV working voltage at 2512 not confirmed for the Asian HV chip series looked at | - |
| fuse (x2) | Schurter 0090.1001 | keep | no Asian 1 A 1000 VDC gPV PCB fuse with I2t and time-current curve (as D-033) | - |
| T1 core / bobbin | TDK ETD 29 N97 + B66359B1013T001 | DMEGC EC34A DMR95 (magnetics design M1, rev C1) | ETD 34 class, potted case; core ~0.40 USD, whole T1 estimated 5.9 USD (cost_estimates.csv, no quote) | - (old) / RFQ (new) |
| optocoupler | Vishay CNY65B | keep | no Asian reinforced optocoupler with >= 14 mm and a granted certificate found | 1.97 USD (LCSC) |
| terminals / connector | Wurth 7461057, Phoenix 1720479 | keep | mechanical; the connector mates with SYS-IO-AUX | - |


## 15. Case AUX75: the 75 W module supply (D-044, ARCHITECTURE-COSTFIRST.md sec. 3)

Second named case of this file (`aux75_spec.json`). Same controller, input block per tap, start-up, string, OV lockout, overload timer, foldback, VDD follower and soft start as the 30 W case; the live 24 V (on BUS-) regulates through a divider into FB (no opto, no shunt regulator); a reinforced SELV winding feeds fans and communication. Transformer figures are ESTIMATES (A-41..A-47) until `sim/out/magnetics/design_aux75_transformer.json` exists; then they are read from it.

| item | value |
|---|---|
| rev A1 | transformer = magnetics design M1 (A-51); line feed-forward R_FF into CS; clamp 6 x SMCJ33A; timer 39-100 ms; input 2 x 20 R per tap |
| worst overload (in the self-check) | 201 W on at 1000 V, highest limit corner (4.07 A; 4.46 A without feed-forward), timer 100 ms, duty 17 %, leakage at its limit: IV2Q171R0D7Z 84/175 C, VS-8ETU04S-M3 live 140/175 C, VS-8ETU04S-M3 SELV 118/175 C, SMCJ33A (each) 140/150 C, AUX-T1 111/130 C |
| margins at the worst corner | drain 1318 V at 1000 V (3.1 % below 1360 V), 1422 V at 1100 V (7.0 % below 1530 V); flux 329 mT on A_min (5.7 % below 349 mT); TVS 139 C at the leakage limit; 10 % on the drain at 1000 V is not reachable: VR >= 137 V (400 V SELV rectifier at 80 %) and the clamp >= 1.4 x VR put the floor at 1269 V (ideal clamp, VBR spread and 100 C) |
| input resistors per tap (AX-01/02 rules) | 2 x 22 R: I2t 16.4 %, 10.8 W at 250 V (5.10 W each); 2 x 20 R: I2t 18.1 %, 9.8 W at 250 V (4.64 W each); 2 x 18 R: I2t 20.1 %, 8.9 W at 250 V (4.17 W each) FAILS i2t; 2 x 15 R: I2t 24.1 %, 7.5 W at 250 V (3.48 W each) FAILS i2t |
| rev A3 (re-rated, one design for PV-P75, PV-P100/110, PCS-P125 three- and four-wire) | turns 50:9:10 (n 5.56, VR 137 V; rev A2 48:8:9, 148 V), Lp 0.485 mH kept, current sense 3 x 0.866R (E96, rev A2 3 x 0.91 R); the same core, switch, clamp, timer capacitor and input block; live-SELV leakage requirement 0.62 uH = 1.5 x the design's estimate (A-42's factor) |
| ratings (one design) | live 30 W continuous, 48 W for 1 s (every build; the 3-phase row carries the same live rating), SELV 64.2 W (3 phases: 42.2 W allocation) -> 94.2 W continuous, 112.2 W for 1 s, from 250 V (PV-03/06); lowest current limit 112.8 W; SELV at the live peak with the fans at 10 %: <= 30.1 V |
| design point | 64.9 kHz, Lp 0.485 mH +/-7 %, Np:N_live:N_selv 50:9:10 (n 5.6, VR 137 V), Ipk 2.56 A at 94.2 W, D 0.33 at 250 V |
| efficiency at 94.2 W | 76.3 / 78.3 / 83.3 / 84.8 / 84.8 / 84.2 % at 200 / 250 / 400 / 600 / 800 / 1000 V (200 V: gates off, fans 100 %) |
| efficiency, PV-P75 (3 phases), full power, fans 100 % | 81.3 / 84.5 / 85.0 / 84.5 / 83.4 % |
| efficiency, PV-P100/110 (4 phases), full power, fans 100 % | 78.8 / 83.5 / 84.8 / 84.7 / 84.1 % |
| IV2Q171R0D7Z at the rating | 3.45 W / Tj 136 C at 1000 V, 1.32 W / 89 C at 200 V (board 20 K/W, A-44) |
| SG2M1K0170J2J at the rating | 2.79 W / Tj 124 C at 1000 V, 1.79 W / 101 C at 200 V (board 20 K/W, A-44) |
| temperatures, rating, 1000 V | IV2Q171R0D7Z 136 C, VS-8ETU04S-M3 live 97 C, VS-8ETU04S-M3 SELV 136 C, SMCJ33A (each) 125 C, AUX-T1 102 C |
| temperatures, rating, 1000 V, worst leakage | IV2Q171R0D7Z 136 C, VS-8ETU04S-M3 live 97 C, VS-8ETU04S-M3 SELV 136 C, SMCJ33A (each) 139 C, AUX-T1 102 C |
| temperatures, hiccup, highest limit (stress) | IV2Q171R0D7Z 84 C, VS-8ETU04S-M3 live 140 C, VS-8ETU04S-M3 SELV 118 C, SMCJ33A (each) 140 C, AUX-T1 111 C |
| live 24 V band | 23.62-24.68 V (set point; FB divider 0.1 %) |
| SELV, 3 phases (fans 10 / 50 / 100 % of 40 W) | live 2.0 W: 25.9-27.3 / 24.3-26.1 / 22.8-24.9 V; live 6.7 W: 26.5-27.7 / 25.8-27.2 / 25.1-26.7 V; live 13.6 W: 26.9-28.2 / 26.3-27.6 / 25.9-27.2 V; live 17.0 W: 27.1-28.5 / 26.5-27.7 / 26.1-27.4 V; live 20.0 W: 27.2-28.6 / 26.6-27.8 / 26.2-27.5 V; live 24.0 W: 27.4-28.9 / 26.7-27.9 / 26.3-27.6 V; live 30.0 W: 27.7-29.2 / 26.8-28.1 / 26.5-27.7 V |
| SELV, 4 phases (fans 10 / 50 / 100 % of 62 W) | live 2.0 W: 25.7-27.1 / 23.4-25.4 / 21.4-23.9 V; live 6.7 W: 26.4-27.6 / 25.4-26.9 / 24.4-26.1 V; live 13.6 W: 26.8-28.0 / 26.1-27.4 / 25.5-26.9 V; live 17.0 W: 26.9-28.2 / 26.2-27.5 / 25.7-27.1 V; live 20.0 W: 27.0-28.4 / 26.3-27.6 / 25.8-27.2 V; live 24.0 W: 27.2-28.6 / 26.5-27.7 / 26.0-27.3 V; live 30.0 W: 27.4-28.9 / 26.6-27.8 / 26.2-27.5 V |
| firmware rule | full fan speed is guaranteed while the live 24 V carries >= 13.6 W (3 phases) / 17 W (4 phases), i.e. while the gate bias is on and the converter switches: SELV >= 25.9 / 25.7 V at 100 % fans (needs 24.5 V); with the gates off the SELV stays 21.4-27.7 V (fan buck 12-36 V) but full fan speed is not guaranteed |
| standby PV-P75 (aux in / module) | 11.1/16.8 / 11.3/17.0 / 12.0/17.7 / 12.8/18.5 / 14.0/19.7 W |
| standby PV-P100/110 (aux in / module) | 11.5/17.2 / 11.8/17.5 / 12.4/18.1 / 13.3/19.0 / 14.4/20.1 W |
| start-up | VDD charge 3.0-6.2 s after brown-in (30 W network), then 11-21 ms to regulation; follower takes VDD at 9-14 ms (VDD hold-up 38 ms) |
| brown-in / out, OV lockout | 206-215 V / 179-192 V, 1107-1162 V |
| hold-up (live, 11 W to 8 V) | 52 ms with 2400 uF (-20 %) |
| current limit / timer | 3.04-4.07 A (113-320 W); timer 39-100 ms, trip above 113 / 175 / 278 W |
| V_DS | 1318 V at 1000 V (limit 1360), 1422 V at 1100 V (limit 1530); clamp 6 x SMCJ33A >= 220 V vs VR 137 V |
| loop | crossover 259, 526, 727 Hz, phase margin 60, 71, 74 deg |

Assumptions of this case: A-41 leakage (M1 1-D value scaled: 15.2 uH nominal, 18.3 uH worst), A-42 live-SELV leakage 0.41 uH, A-43 switched capacitance 56 pF (limit 63 pF), A-44 switch board 20 K/W (>= 8 cm2 2-oz Cu both sides with vias), A-45 ETD 39-class core, A-46 winding resistances 3.24 / 0.144 / 0.072 R, A-47 transformer 15.8 K/W, A-48 two-output cycle model, A-49 loop with the referred SELV capacitance, A-50 start-up model.

Open (AUX75 rev A3): module standby < 20 W needs the contactor economiser at <= 1.5 W per coil (2 x 2.5 W: 22.4 W module at 1000 V); the flux margin at the highest current limit is 5.7 % (10 % needs Np x A_min >= 6740 mm2, the design has 6435: a larger core); the drain margin is 3.1 %; a continuous load between 112.2 W and the timer threshold (278 W at the high corner) is not stopped by this block; the switch needs the A-44 copper area (20 K/W); the input path costs 9.8 W at 250 V, full load. Rev A3: AUX-T1 is re-wound (50:9:10, gap re-ground for Lp 0.485 mH) - a new sample set is needed (Lp, both leakages, switched capacitance, PD); the Lp window that holds both the flux margin and the peak delivery with 3 x 0.866R is about +/-0.5 % of the nominal (the +/-7 % build tolerance is inside the checks); the E96 sense resistor value must be confirmed in the chosen low-ohm series.
