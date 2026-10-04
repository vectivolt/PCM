# GDRV documented alternates (ECO-05)

ECO-05: "reinforced isolated SiC gate driver, UCC21710 primary (STGAP2SICS / EiceDRIVER / NCD5709x as documented alternates)".
The primary is designed in (`gen/gdrv.py`, D-005). This note documents the alternates from datasheets only, 2026-10-04. Nothing is bench-validated; nothing can be bought.

- Checked against the PDFs on disk in `docs/datasheets/gate-drivers/`: UCC21710 (SLUSD43B, May 2023), UCC21750 (SLUSD78C, Jan 2023), 1ED332xMC12N (Rev 1.03, 2024-04-17). Page = PDF page, equal to the printed page number in all three.
- STGAP2SICS and NCD57090/NCD57091 are NOT on disk. They have their own table (section 3) and provisional verdicts.
- "calc" = derived here from datasheet figures and `gen/gdrv.py` constants (same equations as `design_check()`), not a datasheet value.
- Verdicts: DROP-IN = same footprint and pin functions. ADAPTABLE = different pinout or protection scheme, change list given. NOT SUITABLE = isolation working voltage, output supply span,
  peak current or protection function does not cover the design; one deciding number given.

## 1. What the driver has to do

Each switch gets one isolated channel from `gdrv.channel()`: a UCC21710 (reinforced barrier, 3.3 V inputs, split OUTH/OUTL, soft turn-off, CLMPI Miller clamp, RDY/FLT/RST) with its own
UCC14241-Q1 bias module; short-circuit detection on the OC pin through an R1/R2/R3 divider and a 3 x US1M string to the drain (the OC pin has no current source, UCC21710 p38); FLT goes
to the hardware trip matrix. PV cell (D-007, `gen/pvcell.py`): 4 channels, 2 x MSC035SMA170B4 (1700 V) in parallel per switch, +20/-4 V (VDD-VEE 23.79-24.51 V), 32 kHz, bus 1000 V
with a 1100 V trip, switch-node dv/dt up to 92 V/ns, device peak 1401 V in the worst commutation (`sim/out/pv_design/report.md`); calculated gate peaks 5.8 A source / 8.2 A sink
(code limit 10 A); DESAT trips at V_DS 5.1-8.9 V after 261-463 ns; detect plus soft turn-off 1.35 us against 3.1 us typical short-circuit withstand; Miller hold by per-gate PNP
followers on CLMPI; AIN/APWM not used. DAB (`gen/dab60.py`): one channel per switch (8), CBB011M12GM4T 1200 V modules, +15/-4 V (VDD-VEE 18.84-19.41 V), 100 kHz, up to 950 V,
dv/dt 56.7 V/ns (module maximum 80 V/ns); gate peaks 6.26 A / 9.87 A at Ron/Roff 1.0/0.27 ohm (`sim/out/dab_design/dab_spec.json`); DESAT 5.5-9.6 V after 178-367 ns, 1.32 us total
against a 3 us surrogate withstand time; Miller current 2.0 A (2.9 A at 80 V/ns) held by CLMPI direct on the gate; module NTC read through AIN/APWM on one driver per bridge. The
code's barrier rules are VIOWM >= 1.2 x 1000 V and CMTI >= 1.5 x dv/dt. The UCC14241-Q1 (VIOWM 1414 VDC, certification planned) stays in every channel, so a driver above 1414 V
does not lower the channel's barrier.

## 2. Comparison, checked against the PDFs on disk

| Parameter | Design need | UCC21710 (primary) | UCC21750 | 1ED3321MC12N |
|---|---|---|---|---|
| Variant compared | - | UCC21710DW(R), SOIC-16 (p1) | UCC21750, SOIC-16 (p1) | 1ED3321MC12N: split OUTH/OUTL, soft-off, UVLO 12 V (p2). Not 1ED3320 (+3.3/-6 A typ), not 1ED3322 (UVLO 14.2 V max leaves 0.6 V on the 15 V rail; hard-off), not 1ED3323 (single OUT; hard-off) (p2, p21) |
| Isolation: VIORM / VIOWM / VIOTM, class, standard | VIOWM >= 1200 V DC (1.2 x 1000 V); 1100 V trip; 1401 V PV peak; >= 1414 V to match the bias module | 2121 Vpk / 1500 Vrms = 2121 Vdc / 8000 Vpk. Reinforced, DIN EN IEC 60747-17 (VDE 0884-17), cert 40040142 (p6-7). UL 1577 5700 Vrms (p7) | Same values (p5, p8). Cert 40040142, UL 1577 5700 Vrms (p8) | 1767 Vpk / 1249 Vrms (DC value not stated) / 8000 Vpk. Reinforced, IEC 60747-17, cert 40055138 (p1, p26). UL 1577 5700 Vrms (p26) |
| Package, creepage, clearance | ECO-10 not frozen; `gen/dab60.py` notes 8 mm packages are below the 10 mm needed at PD2 / 1000 V | DW SOIC-16, 10.3 x 7.5 mm (p1). CLR and CPG > 8 mm, CTI > 600 V, group I (p6). Land pattern examples: 9.3 mm row spacing, HV option 9.75 mm (p54) | DW SOIC-16 (p1). CLR and CPG > 8 mm, CTI > 600 V (p5) | DSO-16 wide body, not DSO-8 (p1, p27; the PDF has no "PG-" prefix). 10.3 x 7.5 mm, 1.27 mm pitch (p27). CLR and CPG > 8 mm, CTI > 400 (p26). Land pattern 10 mm row spacing (p28) |
| Output supply (VDD-VEE max) | PV 23.79-24.51 V; DAB 18.84-19.41 V | VDD-COM 13-33 V, VDD-VEE <= 33 V, abs max 36 V (p4) | Same (p4) | VCC2-VEE2 <= 35 V, VEE2 -20 to 0 V (p20); abs max 40 V (p18) |
| Peak source / sink | PV 5.8 / 8.2 A; DAB 6.26 / 9.87 A | 10 A / 10 A typ, CL 0.18 uF, 1 kHz (p8) | Same (p7) | Source min 4 / typ 6 A, sink min 4 / typ 8.5 A, not production tested (p22). Abs max +-9 A for 1 us (p18). RDSON,H 0.5/0.79/1.3, RDSON,L 0.35/0.51/0.85 ohm (p22) |
| UVLO, both sides | VDD-COM min 14.78 V on the 15 V rail must exceed UVLO max + 1 V; on +20 V the bias PG is the effective UVLO | VCC on 2.55/2.7/2.85, off 2.35/2.5/2.65 V; VDD-COM on 10.5/12.0/12.8, off 9.9/10.7/11.8 V (p8) | Same (p7) | VCC1 on max 3.1, off min 2.5 V; VCC2-GND2 on typ 12.0 / max 12.6, off min 10.4 / typ 11.0 V (p21). VEE2 not monitored (p14) |
| Short-circuit detection | Hard trip into the matrix; PV trip 5.1-8.9 V, DAB 5.5-9.6 V | OC pin, threshold 0.63/0.7/0.77 V, deglitch 95/120/180 ns (p9). No internal current source: DESAT only through an external divider (p38-39) | DESAT pin, threshold 8.5/9.15/9.8 V, internal source 430/500/570 uA (p8, p28) | DESAT pin, threshold 8.5/9.0/9.5 V, internal source 438/510/582 uA, I(DESAT) <= 5 mA abs max (p18, p24) |
| Blanking, response | PV blank 250-500 ns and detect + soft-off <= 2.0 us; DAB blank 150-500 ns and <= 70 % of 3 us | Set by R1-R3 and Cblk (eq 11, p38). OC to OUT(L) 90 %: 150/270/400 ns; OC to FLT 300/530/750 ns (p9) | Leading edge 200 ns (p8, typ; the table prints it twice) + Cblk charge. Deglitch 50/140/230 ns; DESAT to OUT(L) 90 %: 200 typ / 300 max ns; to FLT 400/580/750 ns (p8) | Leading edge 280/400/500 ns + Cblk charge. Filter 250 ns typ; DESAT to OUT low 380 typ / 500 max ns; to /FLT 2.25 us max (p25) |
| Soft / two-level turn-off | Soft turn-off (code uses ISTO min 250 mA) | Soft turn-off 250/400/570 mA (p9); no two-level | Same (p8) | Soft-off 230 mA typ, no min or max; watchdog 5-9 us (p22). 1ED3322/23 are hard-off (p2) |
| Miller clamp | PV: PNP followers, pin carries base current. DAB: pin direct on the gate, I_M 2.0 A (2.9 A at 80 V/ns) | Internal, CLMPI: 1.5/2.0/2.5 V above VEE, 4 A typ, 0.6 ohm typ, on-delay 15/50 ns (p9) | Same (p7-8) | Internal, CLAMP: 1.6/2.1/2.4 V above VEE2, 2 A min / 3 A typ, 0.4/0.6/1.0 ohm, activation <= 80 ns (p23). "Designed for a Miller current up to 2 A" (p10, p16) |
| Fault reporting, reset | FLT_N wired-OR to the trip matrix, reset by firmware | FLT open drain, latched until RST/EN low > 1000 ns then rising edge (p3-4, p28); mute 0.55-1 ms (p9) | Same (p3, p29) | /FLT open drain, low until /RST pulse >= 800 ns then rising edge (p17, p25). /FLT and RDY have internal pull-ups, 100 uA typ, up to 400 uA (p21) |
| Enable, ready | EN open = disabled (10k pull-down in the caller); RDY wire-AND with bias PG | RST/EN pin 14, internal 50 kohm pull-down, disabled when open (p28). RDY pin 12: VCC and VDD-COM good (p3) | Same (p3, p29) | /RST pin 14, internal pull-up 40-200 uA, low = off (p10, p21). RDY pin 12: both UVLOs and transmission ok (p10, p14) |
| Propagation delay, skew | Dead time 191-511 ns at the gates (PV); skew terms 60 ns in the code | 60/90/130 ns; PWD <= 30 ns; part to part <= 30 ns (p10) | Same (p9) | On 74/80/84, off 81/86/92 ns; distortion <= 11 ns (IN+), <= 17 ns (IN-); part to part <= 15 ns (p23-24) |
| CMTI | PV 92, DAB 56.7 (module max 80) V/ns; rule 1.5x = 138 / 85 / 120 V/ns | 150 V/ns min (p1, p9) | 150 V/ns min (p1, p8) | 300 kV/us = 300 V/ns, not production tested (p1, p20) |
| Isolated analog / temperature | DAB: module NTC on AIN/APWM of the L1 driver per bridge. PV: not used | AIN 0.6-4.5 V to APWM 400 kHz, duty 88 % to 10 %; IAIN 196/203/209 uA (p9, p28-29) | Same; IAIN 196/200/209 uA (p8) | None: no AIN or APWM pin (p8-9) |
| Operating temperature | TJ 131-136 C at a 125 C board (code) | TA -40 to 125 C, TJ -40 to 150 C (p4) | Same (p4) | TA -40 to 125 C, TJ -40 to 150 C (p20) |
| Input logic levels | 3.3 V CMOS from the controller | VCC 3.0-5.5 V; high >= 0.7 VCC, low <= 0.3 VCC (p4); at 3.3 V high max 2.31 V, low min 0.99 V (p8); IN+ pull-down, IN- pull-up 55 kohm (p8) | Same (p4, p7) | VCC1 up to 5.5 V; thresholds 0.3 / 0.7 x VCC1 (p20-21); 3.3 V and 5 V CMOS (p1-2). IN+ pull-down; IN- and /RST pull-up 40-200 uA (p21) |

## 3. Recalled from the public datasheets - NOT verified against a file on disk

STGAP2SICS and NCD57090/NCD57091 are on the "Still to download by hand" list in `docs/README.md` (st.com and onsemi.com refuse scripted downloads; `docs/SOURCES.csv` status MANUAL,
0 bytes; the manifest expects a 25-page NCD57090 PDF dated 2023-07-26). Every cell below is from memory or from the manifest title, not from a page. Re-check each cell page by page
once the PDFs are saved to `docs/datasheets/gate-drivers/`; until then the verdicts in section 4 for these two are provisional.

| Parameter | Design need | STGAP2SICS (recalled) | NCD57090 / NCD57091 (recalled) |
|---|---|---|---|
| Identity | - | Single isolated gate driver for SiC MOSFETs, 4 A (manifest title) | Isolated high-current IGBT/MOSFET driver, SOIC-8 WB, 5 kVrms (manifest title). Variants A-F in the manifest; letter to be chosen from the datasheet |
| Isolation (VIORM / VIOWM / VIOTM, class, standard) | VIOWM >= 1200 V DC; >= 1414 V to match the bias module | 6 kV galvanic isolation; VIORM, VIOWM, VIOTM, class, certification status: to be read from the datasheet | 5 kVrms; VIORM, VIOWM, VIOTM, class, standard: to be read from the datasheet |
| Package, creepage | > 8 mm class | SO-8 wide body; creepage: to be read | SOIC-8 wide body; creepage: to be read |
| Output supply | 24.51 V PV, 19.41 V DAB | Up to 26 V | To be read |
| Peak source / sink | PV 5.8 / 8.2 A, DAB 6.26 / 9.87 A | 4 A / 4 A | To be read |
| UVLO | VDD-COM min 14.78 V on the 15 V rail | Present; thresholds: to be read (must clear +15 V) | To be read (depends on the letter) |
| Short-circuit detection | DESAT or OC at the driver, FLT out | None on the part, UVLO and thermal shutdown only (moderate confidence: read the pin table first) | To be read. 8 pins leave no room for DESAT + FLT + RDY + EN together (inference from the package) |
| Blanking, response | PV <= 2.0 us, DAB <= 2.1 us | Not applicable if no detection | To be read |
| Soft / two-level turn-off | Soft turn-off | To be read | To be read |
| Miller clamp | PV PNP followers, DAB direct | To be read | To be read (which of the two has it) |
| Fault, reset, enable, ready | FLT_N, RDY, EN | To be read | To be read |
| Propagation delay, skew | Dead time 191-511 ns at the gates | To be read | To be read |
| CMTI | Rule 138 V/ns PV, 85 V/ns DAB (120 at 80 V/ns) | 100 V/ns | To be read |
| Analog / temperature channel | DAB NTC | None | To be read |
| Operating temperature | TJ 131-136 C at a 125 C board | To be read | To be read |
| Input logic | 3.3 V CMOS | 3.3 V / 5 V compatible | To be read |

## 4. Verdicts

| Alternate | PV cell (1700 V devices, +20/-4 V, 1100 V trip) | DAB (1200 V modules, +15/-4 V, 950 V) |
|---|---|---|
| UCC21750 | **ADAPTABLE** | **ADAPTABLE** |
| 1ED3321MC12N | **ADAPTABLE** | **NOT SUITABLE** |
| STGAP2SICS (provisional) | **NOT SUITABLE** | **NOT SUITABLE** |
| NCD57090 / NCD57091 (provisional) | **NOT SUITABLE** | **NOT SUITABLE** |

No alternate is DROP-IN.

### UCC21750: ADAPTABLE for both uses

Same footprint, 15 of 16 pin functions identical (p3). Pin 2 is DESAT with an internal 500 uA source and a 9 V threshold, not the 0.7 V OC input. Isolation ratings, supply span, current,
UVLO, clamp, logic, CMTI and skew are identical (p5-9); AIN/APWM differs only in IAIN (200 vs 203 uA typ). Populating it on the unchanged R1/R2/R3 network is unsafe: pin 2 would sit near
2 V (calc: 2.04 V PV, 1.80 V DAB, with the 0.5 mA source) against 8.5-9.8 V, so it would never trip. Changes in `channel()`:
- Pins: part UCC21750 (UCC21750.pdf), pin 2 net DESAT instead of OC. Nothing else moves.
- DESAT network: delete R1, R2, R3 and the DX/OC node split. Pin 2 carries Cblk to COM, the BAT54S clamp, Rs 220R and the 3 x US1M string. Cblk about 3.4-13 pF for the 250-500 ns window
  (calc: 200 ns leading edge + C x V / I at 15-23 ns per pF), the same order as the string capacitance: set it by measurement.
- Trip window: 9.15 V minus 3 x US1M VF minus I x Rs gives V_DS 5.4-8.2 V, nominal 6.8 V (calc, VF 0.5-1.0 V as in the code). Inside today's 5.1-8.9 V (PV) and 5.5-9.6 V (DAB); the
  DAB's 1.5 x 3.45 V on-state rule (5.2 V) is still met. The divider no longer sets the level; only the string length and Rs move it. Replace `desat_numbers()` and the OC end-value
  assertion in `design_check()`. `dnp="desat"` ties pin 2 to COM (p38).
- Clamp, logic polarity, supply levels: no change.
- Bias budget: `rlim_check()` and `p_bias` lose the VDD-R1 current term.
- Response (calc, Cblk 10 pF): blank 428 ns + DESAT to off 300 ns + soft turn-off 492 ns (PV) or 552 ns (DAB) = 1.22 us PV, 1.28 us DAB, against 1.35 / 1.32 us today.

### 1ED3321MC12N: PV cell ADAPTABLE

Covers the PV numbers: VIORM 1767 Vpk against the 1100 V trip (1.6 x) and the 1401 V peak (1.26 x); VCC2-VEE2 24.51 V against 35 V; sink 8.1 A (calc, RDSON,L min, Kelvin R and ESR as in
the code) against the 9 A absolute maximum; CMTI 300 V/ns against 138 V/ns; DESAT with soft-off. Changes in `channel()`:
- Pins: new part from the p8-9 table, same 16-pin wide-body package family but not the same pin map. Pins 9-15 equal the UCC21710. Pin 16 = GND1 (UCC: APWM). Pin 1 = VEE2 (UCC: AIN, tied to COM
  today): it must go to the VEE net. Pin 3 = GND2 (COM), 2 = DESAT, 7 = CLAMP, 5 = VCC2, 8 = VEE2. Footprint: land pattern differs (10 mm vs 9.3 / 9.75 mm row spacing), check per DEL-4.
- DESAT network: as for the UCC21750 but Cblk about 0. The leading edge alone is 280-500 ns and fills the 250-500 ns window; any capacitor pushes the maximum past 500 ns. Trip window
  5.4-7.9 V (calc). Worst-case response 1.84 us (calc: 500 + 500 ns + 0.84 us soft-off ramp scaled from the 47 nF figure, p25) against the 2.0 us budget: 8 % margin.
- Clamp: thresholds 1.6-2.4 V and delay <= 80 ns replace 1.5-2.5 V and 50 ns. The `mclamp` check then gives engagement at 214 ns (calc) against the earliest complementary turn-on of
  191 ns today; it passes only if the lower skew of this part (32 ns instead of 60 ns, p23-24) is credited to the dead time (219 ns, 5 ns margin). Re-run it and probably lengthen
  `DEADTIME_PV` (3.4k / 100p).
- Logic polarity: same sense (IN+ high and IN- low = on, /FLT low = fault, RDY high = ready). /RST has an internal pull-up where the UCC21710 has a pull-down (40-200 uA, VCC1 of the test
  not stated; if 5 V the pull-up is >= 25 kohm and the 10k EN pull-down gives 0.94 V against a 0.99 V minimum low threshold at 3.3 V, calc), so lower it. Reset pulse >= 800 ns.
- Supply levels: unchanged. 24.51 V against 35 V; UVLO max 12.6 V is far below +20 V, so as today the bias PG (flags below 16.9-18.7 V) is the effective UVLO.
- `design_check()`: new constants for RDSON,H/L, the 9 A limit, 230 mA soft-off, 1.6-2.4 V clamp; no AIN/APWM (the PV cell does not use it).

### 1ED3321MC12N: DAB NOT SUITABLE

Deciding reason: peak sink current. At the DAB's Roff 0.27 ohm the sink peak is 8.9 A with typical RDSON,L and 9.6 A with the datasheet minimum (calc; the UCC21710 design figure is
9.87 A), against an absolute maximum of 9 A (p18) and 8.5 A typ (p22). Holding 9 A needs Roff >= 0.41 ohm (E24 0.43 ohm), which changes the DAB's turn-off dv/dt and E_off (dab_spec
RG_note): a power-stage change, not a driver swap. Also against it: the clamp is rated for 2 A (p10, p16) and the DAB's Miller current is 2.0 A at 56.7 V/ns (2.9 A at 80 V/ns); the
worst-case DESAT response is 2.29 us (calc: 500 + 500 ns + 1.29 us ramp scaled from p25), 76 % of the 3 us surrogate against the code's 70 % limit; there is no AIN/APWM, so the module
NTC readout needs another isolated path. If the DAB owner accepts Roff >= 0.43 ohm the verdict becomes ADAPTABLE, with the PV change list above plus these three checks.

### STGAP2SICS and NCD57090/NCD57091: provisional, NOT SUITABLE for both uses

- STGAP2SICS. Deciding number: 4 A peak (manifest title) against 8.2 A sink (PV) and 9.87 A sink (DAB) in the designs. Also recalled: no short-circuit input, so ECO-05's driver-level
  DESAT/OCP is not met; CMTI 100 V/ns against the 138 V/ns rule for the PV cell. The verdict flips only if the datasheet shows a higher current rating and a detection input.
- NCD57090/NCD57091. Deciding reason (inference): SOIC-8 WB (manifest title) has 8 pins against the 16-pin function set of the code (DESAT/OC, FLT, RDY, RST/EN, CLMPI, OUTH/OUTL). Read
  first: pin table (DESAT/OC input and fault output?), VIORM/VIOWM >= 1414 V, VDD-VEE >= 24.51 V, peak current against 8.2 / 9.87 A, which letter A-F has a UVLO that clears +15 V and +20 V.

## 5. What is not known

- STGAP2SICS and NCD57090/NCD57091: no PDF on disk. Section 3 is memory plus manifest titles; no page citations. NCx57090y/NCx57091y variant letter not chosen.
- 1ED3321MC12N: peak currents, clamp current and CMTI are "not subject to production test" (p20, p22, p23). Soft-off current has no min or max. DC working voltage not stated (1249 Vrms
  only, p26). No psi-JB (RthJA 71.4 K/W and psi-JT 9.93 K/W only, p19), so the code's junction-temperature check cannot be reproduced. No clamp voltage versus current curve, so the
  code's "VEE + 0.5 V at 1 A" Miller model has no counterpart. VCC1 for the 40-200 uA /RST pull-up current is not stated (p21).
- UCC21750: leading edge printed "200 200" (typ or max unclear, p8). No formula for the external blanking time (calc here). US1M forward voltage at 0.5 mA is not in its datasheet (the
  code assumes 0.5-1.0 V).
- UCC21710 and UCC21750: 10 A source/sink are typical values (p8, p7); the code's "<= 10 A" test is on a typical.
- Certification: cert numbers 40040142 and 40055138 are quoted from the datasheets; the certificates are not on disk. UCC14241-Q1 certification is still planned (`gen/gdrv.py`) and caps
  the channel at 1414 VDC. ECO-10 insulation coordination is not frozen, so creepage for 8 mm packages is open.
- Bench or double-pulse tests needed: DESAT blanking with a few pF (parasitic dominated), device short-circuit withstand (MSC035SMA170B4 3.1 us typical only; Wolfspeed gives none),
  Miller hold at the clamp limit, CMTI on the real board at 92 V/ns, dead time against clamp engagement.
- Not assessed: price, stock, lifecycle.

## NSI6651 and its footprint-compatible fallback (gate drive rev 6, 2026-10-04)

The fitted driver is NOVOSENSE NSI6651ASC-Q1SWR (D-035/D-043). TI UCC21750 (docs/datasheets/gate-drivers/UCC21750.pdf)
fits the same SOIC-16W footprint: pins 2-15 have the same function. Pins 1 and 16 differ; `gen/gdrv.py` channel() wires
them so either part can be fitted without a board change:

| Pin | NSI6651ASC (DS p3) | UCC21750 (DS p3-4) | Wiring in channel() |
|---|---|---|---|
| 1 | VEE2 (also pin 8) | AIN, abs -0.3...5 V vs COM; tie low when unused | net P1: 0R to VEE **fitted** for NSI6651; 0R to COM **DNP** - for the UCC21750 swap the two (never fit both: that shorts COM-VEE) |
| 16 | TEST, "connect to GND1" | APWM output, "leave floating if unused", 20 mA abs | 1k to GND1 for both (NSI TEST held low; UCC21750 APWM sources <= 3.5 mA) |

Other differences, with what would have to change for the UCC21750:

- DESAT: threshold 8.5/9.15/9.8 V vs 8.5/9.26/9.8 V; ICHG 430-570 uA vs 350-650 uA (Q1); LEB 200 ns both; filter
  50-230 ns vs 150-265 ns; DESAT-to-OUT <= 300 ns both. The trip window (Rs 100R, 3 x US1MH) and the booster trigger
  (BZX84-B10, above the 9.8 V maximum of both) are unchanged. Blanking gets shorter at the long end (no change needed).
- Soft turn-off ISTO 250/400/570 mA vs 100/400/570 mA (Q1) / 250 mA min (industrial). Either way the rev-5 SC booster
  sets the turn-off, so nothing changes.
- Miller clamp threshold 1.5/2.0/2.5 V both. The UCC21750 clamp is stronger at the pin (CLMPI, like the UCC21710), and
  the per-device PMV30ENEA clamps and their control are unchanged.
- UVLO VDD 10.5/12.0/12.8 V vs 9.8/11.2/12.8 V (Q1): no change (the UCC14241 PG is the effective UVLO on 18-20 V rails).
- Insulation: VIMP 8000 Vpk vs 6250 Vpk. This is the reason to keep the fallback: the UCC21750 meets the 8 kV
  reinforced impulse without the SPD credit that D-043 makes a condition for the NSI6651. VIORM 2121 Vpk both.
- Timing: within the 70 ns delay-mismatch budget of design_check() (UCC21750 tsk-pp 30 ns), so the dead-time
  presets stay.
- The design_check() limits are the NSI6651 values. A UCC21750 build needs its dict (values above) before release.
