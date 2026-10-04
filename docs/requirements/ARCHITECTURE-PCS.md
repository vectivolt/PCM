# Cost-first architecture — PCS-P125 three-phase battery inverter (specification for the board designers)

**Date:** 2026-10-04 · **Decision basis:** D-046, REQUIREMENTS.md §8 (AC-01…03, SRC-6), the cost-first principles of
`ARCHITECTURE-COSTFIRST.md` (D-045) · **Costed parts list:** `gen/data/costfirst_pcs_bom.csv` (catalogue and 5,000-unit
columns; totals in §12)

**Honesty boundary.** Nothing is built, bought, simulated in time domain or measured. The topology screen in §1 is a
first-order loss calculation (scratch script, formulas and device data stated); every design value below is to be
confirmed by the simulations of §9. Standard clauses are named as the Megarevo documents cite them; their text was
not bought — requirements marked *(from memory)* must be checked. 83 % of the 5,000-unit money is an assumed factor.

**Product (AC-02):** 125 kW (137 kW max continuous DC), DC 590–950 V (full load 600–900 V; 650–950 V for 3W+N+PE),
±250 A DC (±230 A continuous), 400/230 V AC, 180 A rated / 198 A continuous / 216 A max, 150 kVA, PF −1…+1,
THDi < 3 %, grid-following and grid-forming / off-grid, 100 % unbalanced load in the four-wire version, overload 110 %
continuous / 120 % 2 min / > 120 % 200 ms, max efficiency 98.5 %, forced air, −30…+60 °C (derate above 45 °C).
Megarevo PMA0125 cites (`pma_user-manual_20250327-v2-00.pdf`, technical tables): EN 62477-1, EN 62109-1, EN 62109-2
(safety); EN 50549-1, EN 50549-10, GB/T 34120, GB/T 34133 (grid); EN IEC 61000-6-2 / -6-4 (EMC); Class I; DC SPD type
II, AC SPD type II; DC OVC II / AC OVC III; PD3 outside / PD2 inside; "DC shorted protection: fuses + DC contactor",
"AC shorted protection: current control", residual-current monitoring, insulation-resistance detection and "AC relay
automatic checking" integrated.

---

## 0. The design in one page

- **Topology: three-level T-type (NPC2) with discrete Chinese IGBTs at 16 kHz** — per phase 2 outer positions of
  4 × 1200 V / 40 A (CR Micro CRG40T120 family, 3.72 USD on LCSC) and 2 inner positions of 3 × 650 V / 40 A; 42 IGBTs,
  12 gate-drive channels (3-wire). Screen: devices 1.68 kW at 125 kW / 900 V (98.66 % for the devices alone), module
  ≈ 98.2 % at full load and ≈ 98.7 % peak (≥ 98.5 % required). It is the cheapest option with a filter of
  reasonable size; SiC T-type is 0.5 % points better and 70 USD dearer in devices, two-level 1700 V SiC is 128 USD
  dearer in devices and needs the largest filter.
- **LCL:** L1 100 µH (amorphous C-core) – C_f 60 µF per phase (star point on the DC midpoint) – L2 30 µH; resonance
  4.3 kHz, active damping plus a small passive branch.
- **3W+PE baseline; 3W+N+PE = a fourth T-type leg** (14 IGBTs, 4 channels, neutral inductor, +157 USD) — the neutral
  current of a 100 % unbalanced load cannot be carried by the split DC link alone.
- **Controller on DC-** (the PV choice): the PV control board is reused as an **assembly variant** (extra 2 × 8 header
  for grid voltages, second AC contactor, RCM test; PCS firmware). F280039C is sufficient; the F28P550SJ6PZR
  (150 MHz, five ADCs) costs only +0.39 USD if the analog channels run out.
- **One reinforced barrier**, unchanged from PV (the DC side's 1000 V, OVC II requirement — 8 kV, 6 kV with the
  varistor credit — is higher than the mains OVC III reinforced 6 kV). AC-side parts add mains OVC III clearances to PE.
- **DC port = the lean battery port of the PV design** (aR fuse per pole — 400 A class, HFE82V contactor, precharge,
  varistor network, IMD). **AC port:** two 3-pole contactors in series with relay check (no AC fuses: "current
  control", as Megarevo), type II varistor SPD, CM cores, RCMU (type-B fluxgate).
- **Cost (3-wire):** **1,037 USD at catalogue (8.3 USD/kW), 801 USD at 5,000 units (6.4 USD/kW)**; 4-wire 1,194 / 919 USD.
  PV-P75 at 5,000 units: 731 USD (9.8 USD/kW).
- **Against the benchmark** (228 kW, 630 V AC, 1,500 USD = 6.6 USD/kW selling price): our unit carries **1.58 × the AC
  current per kW** (1.44 A/kW against 0.92 A/kW) and 1.6-1.8 × the DC current per kW. Scaled for current and for an
  assumed 60 % BOM share of the selling price, the benchmark corresponds to a BOM of ≈ 690 USD for a 125 kW / 400 V unit:
  **3-wire PCS +16 % at 5,000 units (+50 % at catalogue), 4-wire +33 %, PV-P75 +78 %** (§12).

---

## 1. Topology decision (with numbers)

**Screen** (scratch script, first order, unity PF, sinusoidal PWM with the T-type duty functions, conduction
P = m·V0·Î/4 + 2m·r·Î²/(3π) for the outer and the complementary expression for the inner pair; switching energy scaled
linearly with voltage and current from one data-sheet point; 125 kW, 900 V DC, 400 V AC, 180 A; L1 for 25 % ripple at
216 A; L1 cost ≈ 10 USD + 6 USD/J of ½·L·Î² — an estimate):

| option | f_sw | device loss at 125 kW | devices alone | devices | gate channels | device USD | L1 (µH) | L1 USD (3) |
|---|---|---|---|---|---|---|---|---|
| **3L T-type, IGBT 1200 V × 4 outer + 650 V × 3 inner (40 A chips)** | 16 kHz | **1,676 W** | **98.66 %** | 42 | 12 | **116** | 92 | 107 |
| 3L T-type, IGBT × 3 + × 3 | 16 kHz | 1,876 W | 98.50 % | 36 | 12 | 94 | 92 | 107 |
| 3L T-type, SiC 1200 V 14 mΩ × 3 + 750 V 25 mΩ × 3 | 40 kHz | 1,068 W | 99.15 % | 36 | 12 | 186 | 37 | 61 |
| 3L T-type hybrid: SiC outer × 2 + IGBT inner × 3 | 32 kHz | 1,238 W | 99.01 % | 30 | 12 | 115 | 46 | 69 |
| 2L, SiC 1700 V 40 mΩ × 10 per switch (PV device) | 20 kHz | 709 W | 99.43 % | 60 | 6 (10 gates each) | 244 | 147 | 154 |
| 3L I-type NPC1, IGBT 650 V × 3 + clamp diodes | 16 kHz | 2,007 W | 98.39 % | 54 | 12 | 72 | 92 | 107 |

Device data: IGBT 1200 V — **CR Micro CRG40T120AK3S data sheet** (LCSC datasheet host, filed in `docs/datasheets/
power-semiconductors/CRMICRO-CRG40T120AK3S.pdf`): V_CE(sat) 1.9 / 2.4 V at 40 A, 25 °C; E_on + E_off 4.86 mJ at
600 V / 40 A / 150 °C; R_th,jc 0.45 K/W; 175 °C; **its co-pack diode is only 20 A** — the inverter needs the
full-rated-diode variant (the CRG40T120BK3SD on LCSC, C2981191, 3.72 USD @1, 840 in stock, is listed with the same
switching figures; its data sheet must confirm the diode). IGBT 650 V 40 A (CR Micro CRG40T65AK5HD, LCSC C3975125) and
the 750 V SiC values are class assumptions (V0 0.8 V / 25 mΩ / 1.3 mJ; 40 mΩ / 0.25 mJ). SiC 1200 V: Sichain
SG2M014120LJ class (7.31 USD, LCSC). SiC 1700 V: SG2M040170HJ (PV data). Infineon's REF-10KW3LNPC2 uses the same
class — 40 A discretes IKZA40N120CH7 + IKZA40N65EH7 in NPC2 at 24 kHz — which supports the choice.

**Decision: 3L T-type with IGBTs at 16 kHz.** Reasons: (1) cheapest devices that meet the efficiency (a 1200 V / 40 A IGBT is 3.72 USD,
0.09 USD per ampere of rating; SiC T-type needs 1.6 × the device money for the same positions); (2) the switches commutate half the DC link (≤ 475 V), so
16 kHz IGBT switching loss is small and the filter stays at L1 ≈ 100 µH; (3) the roadmap baseline and Megarevo's own
statement ("three-level SVPWM and midpoint balance", "industrial IGBT power modules"); (4) 12 channels fit the 16 ePWM
of the F280039C with room for the 4-wire leg. **Why not the others:** SiC T-type (+70 USD devices, −46 USD filter,
+0.5 % points) is the upgrade if a customer pays for efficiency; two-level SiC needs 60 devices and the biggest
filter; I-type NPC1 puts two devices in every conduction path (+330 W) and needs clamp diodes and the long commutation
loop. **Discretes, not modules:** a Chinese 1200 V / 300-400 A half-bridge module (StarPower GD400HFY120C2S class, 62 mm,
sold at RS) plus an inner common-emitter module would cost ≈ 60-80 USD per phase (no public price — RFQ) against
≈ 39 USD per phase of discretes and pads; modules stay the fallback if paralleling four TO-247 per position proves
hard in layout.

**Margin that changes:** the outer IGBTs block the full DC link. At 950 V that is **0.79 × V_CES** (0.75 at 900 V),
against the 0.67 rule the PV design applied to SiC. IGBTs are less sensitive to cosmic rays than SiC, but the
maker's FIT-versus-V_CE curve is a release condition (R-02). Above 900 V the firmware limits power (Megarevo's own
full-load window ends at 900 V).

**Three-wire versus four-wire.** 3W+PE (PMA "3W+PE", DC from 590 V): the DC midpoint is not connected to N; the
modulation may use zero-sequence injection (SVPWM / third harmonic). **For a TN grid the installer must connect
3W+PE through a transformer or keep N unloaded** — otherwise the 150 Hz zero-sequence voltage between the DC side and
earth drives current through the battery's capacitance to earth (with 5 µF of rack capacitance and 150 V of
zero-sequence: ≈ 0.7 A, over the 300 mA residual-current trip). 3W+N+PE (DC from 650 V, as Megarevo): N is formed by a
**fourth T-type leg** with its own 100 µH inductor; it supplies the neutral current of a 100 % unbalanced load
(216 A) so the split DC link sees no 50 Hz neutral current (a direct midpoint connection would need ≈ 50 mF per half (100 mF in total)
to keep the midpoint within ±10 V). Cost **+157 USD at catalogue, +118 USD at 5,000 units** (fourth leg 14 IGBTs,
4 channels + bias, neutral inductor + capacitor + sensor, 4-pole contactors, N terminal). The 100 Hz power pulsation
of an unbalanced load (≈ 42 kW at 100 % unbalance) flows into the battery (≈ 45 A rms at 100 Hz): an installation
requirement on the battery and its cabling.

---

## 2. Power stage (block level)

| block | specification |
|---|---|
| devices | per phase: T1/T4 (outer) 4 × 1200 V 40 A IGBT, T2/T3 (inner, common emitter) 3 × 650 V 40 A IGBT; TO-247 on one earthed heatsink with Al2O3 0.635 mm pads + spring clips (IGBT loss density allows alumina; PV uses AlN); full-rated co-pack diodes; per-device gate resistor; positive V_CE(sat) temperature coefficient for sharing |
| switching | 16 kHz, three-level SVPWM with midpoint balancing by the redundant small vectors (3-wire) / phase-disposition PWM without LF zero sequence (4-wire); dead time 1.0-1.5 µs (IGBT); hardware interlock of the forbidden T-type states in the drivers' IN−/interlock and the F280039C CLB (TIDA-010210 lesson) |
| DC link | split bank: 2 halves × 6 × Faratronic C3D2K117 (110 µF, 800 V at 70 °C, I_max 34.5 A) = 660 µF per half; each half ≤ 475 V + midpoint deviation; HF ripple ≈ 90 A rms (≈ 15 A per capacitor); one 2 µF / 800 V film per half per phase at the T-type cell for the commutation loops; film only (no electrolytics: life and ripple); passive balancing + bleeder resistors ≈ 440 kΩ per half (0.5 W per half, 60 V after 10 min; 8 × 110 kΩ 1210, ≈ 0.4 USD, not itemised in the CSV) |
| LCL filter | L1 100 µH ±10 % at 216 A rms / 305 A peak (≥ 50 % L at 400 A), amorphous C-core + Cu strip (Chinese AT&M / Yunlu cores), ripple ≤ 25 % at 216 A; C_f 60 µF per phase (2 × 30 µF / 450 V AC MKP), star point tied to the DC midpoint (gives the HF common-mode current a local path); L2 30 µH (powder or Si-steel E-core, 50 Hz dominated); resonance 4.3 kHz (between 10 × 50 Hz and f_sw/3); grid-side attenuation at 16 kHz ≈ 1/17 → ≈ 0.9 % of rated current; reactive power of C_f 3.0 kvar (2.4 % of 125 kVA); active damping (capacitor-current feedback) + 2 Ω / 10 µF passive branch per phase for robustness; losses ≈ 300 W at full load (estimate) |
| AC EMI | two nanocrystalline cores over the three (four) phase conductors + 3 × 4.7 nF Y1 to PE; the C_f star on the midpoint is the first CM stage; limits: EN IEC 61000-6-4 / CISPR 11 class A at the AC mains port (150 kHz-30 MHz) — pre-compliance decides 1 or 2 cores |
| AC disconnect | **two 3-pole contactors in series** (4-pole for 3W+N+PE), AC-1 ≥ 250 A at 60 °C, 24 V DC coil with economiser on the live side, Ui 1000 V, Uimp 8 kV, opened only after the inverter has driven the current to zero; **relay check before every connection** (each contactor alone, voltage across it measured) — IEC 62109-2 (non-isolated converter: single-fault-safe disconnection) and EN 50549-1 (interface switch, "AC relay automatic checking" in the PMA) *(clauses from memory)*; no AC fuses (short circuits are limited by current control, as Megarevo states) |
| grid protection (firmware on hardware sensing) | EN 50549-1: U/f interface protection with settable thresholds and times, loss-of-mains detection (ROCOF / vector shift, plus an active method where a code needs IEC 62116), fault ride-through (LVRT/HVRT, GB/T 34120 / EN 50549-1), Q(U), cos φ(P), P(f); GB/T 34120 / 34133 tests; RCMU per IEC 62109-2 (300 mA continuous, 30/60/150 mA steps) *(from memory)* |
| DC port | the PV lean battery port: aR fuse per pole (**400 A class**, because the HPE501 family stops at 250 A and the 75 % long-term rule needs ≥ 333 A for 230 A continuous — RFQ), HFE82V-300C/1000 in DC+ (300 A at 85 °C, breaks 1.5 kA once), G7L precharge relay + 220 Ω (C_eq 330 µF: 149 J at 950 V, 0.33 s to ΔV ≤ 10 V), shunt in DC-, hold-off rule and upstream requirement as PV §6.3 (re-derived for the 400 A fuse: its 5 In = 2 kA would widen the gap — the hold-off threshold and the upstream band must be re-computed, R-05) |
| surge | DC: the PV rev-6 varistor network (Up,eff 3.79 kV); AC: type II on board — 3 thermally protected varistors L-PE (Thinking TVT25 300 V AC class) + GDT N-PE (4-wire), monitored like the DC network; OVC III at the terminals |
| aux supply | the PV 75 W flyback on the DC link (590-950 V) + a 6-diode tap from the AC side (start from the grid when the battery is empty or disconnected) |

---

## 3. Insulation concept

**Controller reference: DC-, as in the PV design** (the PV control board and its barrier are reused). Requirements
from `sim/insulation.py`'s tables (scratch evaluation with its `TABLE_F1`, `imp_required`, `clearance`, `creepage`,
`u_ac_test`):

| barrier | type | requirement (impulse; clearance 2000/3000/4000 m; creepage PD2 (PD3 grp I); AC test) | part |
|---|---|---|---|
| B1 controller (DC-) ↔ SELV | reinforced | DC side 1000 V, OVC II: **8 kV** (6 kV with the DC varistor credit); 8.0/9.2/10.4 mm (5.5/6.3/7.1); 10.0 mm (25 mm); 4400 V rms — **unchanged from PV**; the mains side asks reinforced 6 kV (OVC III at 230 V L-E), lower | AUX-T1 SELV winding, CA-IS3062W, CA-IS3082W, 3 × CA-IS3821 (PV) |
| DC poles ↔ PE | basic | 6 kV (4 kV credit); 5.5/6.3/7.1 mm; 5.0 mm (12.5 mm); 2200 V rms | as PV (pads, terminals, Y caps, MOV3, IMD strings) |
| **AC lines ↔ PE (new)** | basic | 230 V L-E, **OVC III: 4 kV** (2.5 kV with the AC SPD credit); 3.0/3.5/3.9 mm; 1.0 mm PWB (3.2 mm PD3 grp I); 1430 V rms | AC terminals, contactor bodies, Y1 caps, SPD, C_f to PE spacing, current-sensor housings |
| AC lines ↔ controller (dividers, sensors, contactor coil-contact) | functional | up to ≈ 850 V peak (V_dc/2 + 325 V) + the clamped mains surge | divider strings 6 × 1206 (≥ 4 kV in series), TMR sensors, contactor coil-contact (Ui 1000 V) |
| controller on the DC midpoint instead (option) | reinforced | still 8 kV (the 1000 V DC system sets the row), creepage 8.0 mm instead of 10.0 mm — **no gain worth losing the PV reuse** | — |

**What changes against the PV design:** (1) the AC side is mains, OVC III: an AC SPD and mains clearances to PE; (2)
temporary overvoltages of the grid (U0 + 1200 V, 5 s) enter the AC-side insulation tests; (3) the DC side is now
galvanically tied to the grid: in grid-connected operation the battery poles sit at about ±V_dc/2 against N/PE — the
battery rack's insulation must be rated for that, and the module's IMD runs only when the AC contactors are open
(insulation check before connecting, IEC 62109-2 style); (4) the RCMU (type-B fluxgate around all live AC conductors)
is new; (5) the contactor coils (live) face mains-potential contacts: coil-contact Ui ≥ 1000 V, coil-to-mounting
basic as in PV (R-04 of the PV document).

---

## 4. Sensing (no isolated amplifiers)

| quantity | how | accuracy / bandwidth | dv/dt |
|---|---|---|---|
| grid phase voltages at the terminals and at C_f (6) | dividers 6 × 1 MΩ 0.1 % to DC-, offset by the ADC front end (signal = V_dc/2 ± 325 V) | ≤ 0.5 % after calibration (< 1 % required: PMA "voltage accuracy"), ≥ 5 kHz | none (filtered nodes) |
| relay check | difference of terminal and C_f voltages per phase, per contactor | — | none |
| inverter currents (L1) | open-loop TMR sensor ±400 A **on the C_f side of L1** (a 50 Hz-smooth node, not the switch node) | ±1.5 %, calibrated; 1 MHz, 0.2 µs for the OC window | low (the switch node is avoided) |
| grid current | computed: i_L1 − C_f·dv/dt (no sensor) | ≤ 1 % at rated (THDi < 3 % needs the L1 loop + model) | — |
| DC current | shunt in DC- (2 × 200 µΩ), zero-drift amplifier, as PV | ≤ 0.8 % | none |
| DC link halves | dividers V(DC+ − DC-), V(M − DC-) | ≤ 0.5 % | none |
| DC terminal voltage (precharge, polarity) | divider, bipolar | ≤ 0.5 % | none |
| residual current (RCMU) | type-B fluxgate sensor around L1-L3 (+N) | 30 mA steps, 300 mA continuous | — |
| insulation resistance (battery side, contactors open) | PV IMD (2 × CA-IS3417WT strings + PE divider) | as PV | — |
| temperatures | NTC: heatsink 3, L1 3, DC link 1, inlet 1 (heatsink probes basic-insulated) | ±2 K | — |

---

## 5. Protection and safety

The PV latch (§6.1 of `ARCHITECTURE-COSTFIRST.md`) is reused: one set-dominant flip-flop, PWM gating, all drivers'
EN low, MCU trip zone, status output; cleared by firmware only when no source is active.

| trip | sensing | threshold | path | gates off |
|---|---|---|---|---|
| phase over-current (per phase + N leg) | TMR → window comparator | ±400 A peak (1.3 × 305 A) | latch | ≤ 1.5 µs; L1 limits di/dt to 950 V / 100 µH = 9.5 A/µs |
| backup OC | CMPSS (4) → trip zone | ±440 A | on-chip | ≤ 1.3 µs |
| device short circuit | NSI6651 DESAT (IGBT: ≈ 7 V threshold, 2-3 µs blanking), soft turn-off | per channel | driver → FLT_N → latch | IGBT withstand 5-10 µs (maker data to confirm) |
| DC over-voltage, each half | dividers → comparators | 1050 V total / 560 V per half | latch | ≤ 35 µs |
| midpoint deviation | V(M − DC-) vs V_dc/2 | ±10 % | latch | ms |
| over-temperature, aux loss (RDY), watchdog, external stop | as PV | as PV | latch | as PV |
| grid out of window / loss of mains / RCMU step / insulation fault | firmware on the sensed values | EN 50549-1 settings | firmware stops PWM, then opens the AC contactors at zero current | ≤ 200 ms (EN 50549 / GB/T settings) |

**AC short circuit (terminal or grid fault):** the inverter limits its current (≤ 1.2 × I_max for 200 ms, then
trips) — no AC fuse, as Megarevo; the upstream AC breaker protects the cable. **DC side:** fuses + contactor + hold-off
as PV. **Contactor rules:** close the AC contactors only after synchronisation (|ΔV| ≤ 5 % per phase) and a passed
relay check; open only at zero current.

---

## 6. Controller: reuse the PV control board

**Answer: the PV control board as an assembly variant, F280039C unchanged.** Evidence that the part carries a
three-level T-type three-phase inverter: TI TIDA-01606 (11 kW T-type, TMS320F280039C control card offered) and
Wolfspeed CRD-25BDA6512N-K (25 kW T-type, F280039C) both run on it, at 50-90 and 60 kHz — four to five times our
16 kHz.

| resource | F280039C | PCS 3-wire | PCS 4-wire |
|---|---|---|---|
| ePWM | 16 | 12 (2 modules per phase: outer pair, inner pair) | 16 — all |
| ADC channels | 25 | 6 grid V + 3 i_L + DC I + RCM + 3 DC V + VPE + 6 NTC + 2 rails = **23** | 24 (NTCs multiplexed if more) |
| CMPSS | 4 | 3 phase windows | 4 — all |
| CPU / CLA | 120 MHz + CLA | current loops at 32 kHz (double update) with active damping ≈ 3 × 2 × 250 cycles = 12.5 µs of 31.25 µs on the CLA (40 %); PLL, VSG (grid-forming) swing equation, voltage loops, midpoint balance, sequence decomposition on the CPU at 2-8 kHz | ≈ 55 % CLA |
| communication | DCAN + MCAN, 2 SCI, 2 LIN, SPI, I2C | CAN + RS-485 (Modbus RTU) via the PV SELV zone | same |

**Assembly variant:** the PV board's 2 × 32 contract `PC` (gen/interfaces.py) carries 16 PWM, EN/FLT/RDY, three
contactor commands (K_A → AC contactor 1, K_B → DC contactor, K_PRE), HOLD, two IMD switches, MOV_OK, IL1-IL4,
IA/IB, five voltages and eight NTCs; the PCS needs **one extra 2 × 8 header** (footprint on every control board,
fitted only on the PCS variant) for 6 grid voltages, K_AC2 and the RCM test winding. Firmware differs. Ethernet
(PMA has it) is not fitted (cost-first; Modbus RTU on RS-485). **Upgrade path:** TMS320F28P550SJ6PZR, 150 MHz, five
ADCs, **4.78 USD at 1 ku** (TI.com, read 2026-10-04; +0.39 USD) if the four-wire grid-forming firmware or the analog
channel count needs it — its pin compatibility with the F280039C board is not yet checked (R-09).

---

## 7. Auxiliary supply, gate drive, thermal

**Aux:** the PV 75 W flyback unchanged (input 590-950 V is inside its 200-1100 V range) plus a six-diode tap from the
AC side, so the PCS can start from the grid. Load: 12 (16) gate channels ≈ 5 W (IGBT gate charge ≈ 0.21 µC × 23 V ×
16 kHz × 4 devices ≈ 0.3 W per channel, plus quiescent and regulation), controller 1.5 W, 2 AC + 1 DC contactor economised ≈ 5 W, RCMU 0.5 W, fans
4 × 12 W at full speed → ≈ 62 W: inside the 75 W rating.

**Gate drive:** `gen/gdrv.py` `channel()` with the NSI6651ASC (DESAT, soft turn-off) at +15 / −8 V, `booster=False`
(IGBT short-circuit withstand 5-10 µs does not need the SiC booster), the driver's own Miller clamp (IGBT dv/dt
≈ 5-10 V/ns), one gate resistor per paralleled device, DESAT threshold re-set for V_CE(sat) (≈ 7 V); bias = the PV
per-phase transformer scheme (rails changed by the secondary turns and the shunt reference). About 45 parts per
channel instead of 85.

**Thermal:** losses at 125 kW / 900 V / 45 °C: devices 1,680 W, L1 ≈ 210 W, L2 ≈ 90 W, C_f + damping ≈ 20 W, DC
link ≈ 30 W, contactors + fuses + busbars ≈ 90 W, aux + fans ≈ 70 W → **≈ 2.2 kW (98.2 %)**; at 50 % load ≈ 0.8 kW
(**98.7 %, the peak**; first order, §9 item 1 confirms). One earthed heatsink for all 42 IGBTs, **R_sa ≤ 0.03 K/W**
(T_sink ≤ 95 °C at 45 °C inlet; per device ≈ 40 W × (0.45 + 0.25) K/W = 28 K → Tj ≈ 123 °C, hot-spot devices to be
checked), ≈ 6.5 kg; **4 × Delta AFB1224SHE-F00** (as PV, 12 W, 258 m³/h, −10 °C rating — PV risk R-05 applies);
inductors downstream in the same air stream. Envelope (PMA0125: 650 × 700 × 220 mm, 5U) allows a wider fan row
than the PV module.

---

## 8. Boards, reuse, part count

| board | content | parts (3-wire) | 4-wire |
|---|---|---|---|
| **POWER-PCS** | 42 IGBTs, 12 channels × ~45 + bias (3 × 49), split DC link (18 film), 3 TMR sensors, dividers (60 R), DC shunt, IMD, DC varistor network, AC SPD, RCMU sensor, contactor drivers, aux flyback (~150), press-fits | ≈ 1,250 | ≈ 1,480 |
| **CONTROL (PV board, PCS variant)** | F280039C, latch, comparators, B1 and SELV zone, extra header | ≈ 250 | ≈ 255 |
| chassis | 6 inductors, 6 C_f, 2 AC contactors, DC contactor, 2 DC fuses, precharge R, terminals, busbars, heatsink, 4 fans | (BOM, not placements) | +1 inductor |
| **module** | | **≈ 1,500** | ≈ 1,735 |

**Reused unchanged from the PV design:** control board + B1 barrier + SELV interface; aux flyback; gate-drive channel
(with `booster=False`) and the per-phase bias scheme; DC battery port (contactor, precharge, varistor network, IMD,
shunt, hold-off logic); the latch; build checks (`gen/dcdclib.py`), insulation tables, cost model. **New:** T-type
power stage, LCL magnetics, AC port (contactors, AC SPD, RCMU, CM cores), grid sensing, the fourth leg.

---

## 9. Simulations needed before boards are drawn (in this order)

| # | simulation | output that the boards need | basis |
|---|---|---|---|
| 1 | **Loss and thermal map** of the T-type with the real CR Micro data (V_CE(sat) and E curves, diode data of the full-rated variant): V_dc 590-950 V × PF −1…+1 × load, Tj per position, heatsink | device count per position, f_sw, R_sa, efficiency map (98.5 % peak) | the screen of §1 → `sim/pcs_design.py` |
| 2 | **LCL design + resonance**: L1/L2/C_f vs grid impedance (SCR 2…∞), damping, attenuation at f_sw, magnetics (MAG-1/2: amorphous C-core L1, L2, OpenMagnetics check) | inductor and capacitor specs | `sim/magnetics.py` method |
| 3 | **Current control + PLL** (grid-following): LCL active damping with sampling/PWM delay, stability over grid impedance, THDi < 3 % | loop gains, sampling plan, sensor bandwidth | pv_control method + TIDA-01606 |
| 4 | **Midpoint balance** (3-wire SVPWM, PF −1…+1, 590 V, transients) | DC-link half sizing, balancing gains | — |
| 5 | **Grid-forming (VSG) and transitions** grid ↔ off-grid (< 20 ms), parallel modules | outer-loop design, current limiting in grid-forming | — |
| 6 | **Unbalanced load / four-wire**: fourth-leg control, 100 % unbalance, half-wave loads, battery 100 Hz ripple | neutral-leg rating, battery ripple requirement | — |
| 7 | **Fault ride-through and AC short circuit**: LVRT/HVRT profiles (EN 50549-1, GB/T 34120), current limit at 1.2 × I_max for 200 ms | device peak current, trip thresholds | — |
| 8 | **Relay / contactor and DC coordination**: relay-check sequence, synchronised closing, opening at zero current; DC fuse (400 A aR) + contactor + hold-off band; precharge | coordination table, installation requirement | `sim/port_design.py` method |
| 9 | **Common mode and leakage**: CM voltage spectrum for 3W/4W modulation, battery capacitance to earth, RCMU thresholds, EMI pre-compliance model | CM core count, modulation constraints, installation limits | the PV §8.1 method |
| 10 | **Insulation** re-run with the PCS systems (AC OVC III added) | barrier table | `sim/insulation.py` |

---

## 10. What the lean design gives up

1. **Efficiency headroom:** ≈ 98.2 % at full load and ≈ 98.7 % peak (IGBT) instead of ≈ 99 %+ with SiC T-type; it
   meets Megarevo's 98.5 % but does not beat it.
2. **Cosmic-ray margin:** outer IGBTs at 0.79 × V_CES at 950 V (PV rule for SiC: 0.67); power limited above 900 V.
3. **AC fuses:** none — AC short circuits rely on the inverter's current control and the installation's AC breaker.
4. **Ethernet** (PMA has it), touch screen / web server, more than one CAN/RS-485: not fitted.
5. **Four-wire unbalance capability costs a fourth leg** (+157 USD); the 3-wire baseline needs a transformer or an
   unloaded N in TN grids (zero-sequence leakage).
6. **Isolated measurements:** grid voltages through dividers to the live DC- and open-loop TMR currents (±1.5 %
   before calibration) instead of isolated amplifiers / closed-loop transducers.
7. **Service access:** as PV — live controller, isolated tools, CAN updates.
8. **Rated safety function:** single-channel stop input, no SIL/PL claim.

---

## 11. Open risks

| # | risk | next step |
|---|---|---|
| R-01 | IGBT data: only the CRG40T120AK3S data sheet is filed (its diode is half-rated); the BK3SD and the 650 V CRG40T65AK5HD data sheets, diode ratings, short-circuit withstand are missing; 650 V price unread | file the data sheets; RFQ CR Micro / Silan / StarPower; check Infineon IGBT7 class as the reference |
| R-02 | 0.79 × V_CES at 950 V on the outer IGBTs: no FIT-vs-V_CE curve from the maker | maker statement; limit to 900 V if needed |
| R-03 | Paralleling 4 TO-247 IGBTs per position (current sharing, gate loops, layout) | double-pulse test plan; module fallback (RFQ) |
| R-04 | LCL magnetics are custom (amorphous C-core L1 at 216 A); costs are estimates; core loss at 16 kHz ripple | MAG-1/2 + quotes |
| R-05 | DC fuse 400 A aR: no Asian part with data chosen; hold-off band and upstream requirement change with 5 In = 2 kA | Hongfa / Sinofuse RFQ; re-run the PV §6.3 coordination |
| R-06 | AC contactors: no OEM price (retail 217-500 USD per NXC-225 on eBay); coil-to-mounting and coil-contact insulation with a live coil | CHINT / Delixi quote and data sheets |
| R-07 | Grid codes and safety clauses from memory (EN 50549-1, IEC 62109-2, GB/T 34120); relay redundancy, RCMU and anti-islanding details unverified | buy the standards |
| R-08 | Zero-sequence leakage of the 3-wire version in TN grids; battery capacitance to earth unknown | §9 item 9; installation rule |
| R-09 | F280039C: 23-24 of 25 ADC channels, 16 of 16 PWM (4-wire); CPU load for 4-wire grid-forming not timed; F28P550 pin compatibility unknown | firmware budget; data-sheet check |
| R-10 | Type-B RCM sensor: no Chinese data sheet on file | file one |
| R-11 | 83 % of the 5,000-unit figure is an assumed factor | quotes at 5 k |

---

## 12. Cost — both products, catalogue and 5,000 units, against the benchmark

**PCS-P125 (`gen/data/costfirst_pcs_bom.csv`):**

| block | catalogue (USD) | 5,000 units (USD) | largest items |
|---|---|---|---|
| LCL FILTER | 222.60 | 179.61 | 3 × L1 126, 3 × L2 66 |
| POWER | 194.22 | 144.56 | 24 outer IGBT 89.2, 18 inner 27.0, DC-link film 58.8 |
| DC PORT | 183.51 | 136.00 | contactor 55, 2 aR fuses 50, varistor network 29.4 |
| AC PORT | 152.30 | 122.40 | 2 contactors 90, terminals + busbars 39 |
| THERMAL | 88.20 | 65.46 | 4 fans 52.6, heatsink 35.6 |
| GATE DRIVE | 66.73 | 44.96 | 12 NSI6651 35.7 |
| SENSING | 60.50 | 49.68 | 60 divider R 15, 3 TMR 18, RCM 10 |
| AUX SUPPLY | 37.30 | 31.00 | PV flyback + AC tap |
| MECH-ELEC | 13.40 | 10.70 | |
| CONTROL | 10.20 | 9.00 | F280039C 4.40 |
| INTERFACE | 8.09 | 7.50 | |
| **3-wire total** | **1,037.04 (8.3 USD/kW)** | **800.87 (6.4 USD/kW)** | evidence behind the 5k figure: 17 % (LCSC highest breaks, TI 1ku, LCSC tier slopes, marketplace quote); the rest assumed factors 0.67-0.85 |
| 4-WIRE OPTION | +156.78 | +118.31 | fourth leg, neutral inductor, 4-pole contactors |
| **4-wire total** | **1,193.82 (9.6 USD/kW)** | **919.18 (7.4 USD/kW)** | |

**PV-P75 (`gen/data/costfirst_bom.csv`, columns `unit_price_5k_usd`, `basis_5k` added):** catalogue 965.72 USD
(12.9 USD/kW) → **5,000 units 731.26 USD (9.8 USD/kW)**; PV-P100/110 1,176.64 → 889.79 USD. Evidence behind the 5k
figure: 14 % (CA-IS3062W / CA-IS3082WNX LCSC 1000+ breaks, TPS3823 6000+ break, TI 1ku list prices, the LCSC tier
slope of CA-IS3062W applied to the NSI6651, the 300 A / 1000 V contactor marketplace quote); the earlier blanket factors
are replaced row by row, and the inductor's 55 USD is a volume *estimate*, not evidence.

**Against the benchmark.** Megarevo MPHV 228 kW: 1,500 USD selling price = 6.6 USD/kW; 630 V AC → 209 A, i.e. **0.92 A
per kW AC**; DC 914-1500 V, 281 A max → 1.09-1.23 A/kW. PCS-P125: 400 V AC → 180 A = **1.44 A/kW (1.58 ×)**; DC ±250 A
→ 2.0 A/kW (1.6-1.8 ×). Semiconductors, inductors, contactors, fuses, busbars and the film banks scale with current,
not with power. Assumptions (from `gen/cost.py`: 25 % gross margin, BOM = 80 % of cost of goods): the benchmark's BOM
≈ 1,500 × 0.75 × 0.80 = 900 USD = 3.95 USD/kW; with 70 % of a BOM scaling with current, a 125 kW / 400 V unit built
the benchmark's way would cost ≈ 3.95 × (0.3 + 0.7 × 1.58) × 125 ≈ **690 USD**.

| product | catalogue | 5,000 units | 5k vs the current-adjusted benchmark BOM |
|---|---|---|---|
| PCS-P125 3-wire | 1,037 USD (8.3 USD/kW) | 801 USD (6.4 USD/kW) | **+16 %** (≈ 690 USD) |
| PCS-P125 4-wire | 1,194 USD (9.6) | 919 USD (7.4) | +33 % |
| PV-P75 (DC/DC) | 966 USD (12.9) | 731 USD (9.8) | **+78 %** (≈ 410 USD at 1.55 × current per kW) |

Why the PCS is closer than the DC/DC: the PCS has one filter for 125 kW, the DC/DC has three 80 USD inductors for 75 kW
and protection on two DC ports. Why neither reaches 6.6 USD/kW as BOM: that figure is a *selling price* of a
higher-voltage unit; the honest comparison is the current-adjusted ≈ 690 USD, and the PCS is within ≈ 16 % of it at
5,000 units — with 83 % of that figure resting on assumed factors.
