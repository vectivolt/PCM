# Asian sourcing of the isolation, sensing, interface, power-management, logic and passive parts

Status: 2026-10-04, first full pass (see section 9 for where it stopped). Scope: REQUIREMENTS.md section 7
(SRC-1...4), decision D-031, and the coordinator's priority list of 2026-10-04 (isolated amplifiers -> other barrier
parts -> current transducers -> HV divider resistors and shunts -> connectors -> CTRL-C2000 / SYS-IO-AUX ICs).
Datasheet study only: nothing bought, nothing measured, nothing bench-validated. Per-line data with page references:
`gen/data/alternates.csv`. Prices: `gen/data/prices.csv` (LCSC lines dated 2026-10-04 read through the
jlcsearch.tscircuit.com mirror). Labels: **[DS pN]** datasheet page, **[calc]** calculated here, **[est]** estimate.

## 0. Method and limits

- Work list: every ORDERABLE / RFQ (maker, MPN) of `bom/PV-P75_module_BOM.csv` and `bom/DAB-D60_module_BOM.csv`
  (168 lines after removing the SiC devices, gate driver and driver bias, port fuses / contactors / SPDs, magnetic
  parts and the TMS320F28388D), ranked by money with `bom/*_costed_BOM.csv` (TI 1k prices). The module BOMs were
  15-25 min older than the board BOMs; the only difference is the RJ45 (already LINK-PP LPJ4012AHNL on SYS-IO-AUX rev E).
- Owned by other engineers since the coordinator's message (not processed here, listed in section 7): PV cell film
  banks, fan, clamp diodes and leg damper; port fuses, contactors, SPDs, precharge relay and resistors, thermostats,
  X capacitor, CM choke, IMD switches; DAB film / HF capacitors, snubbers, flow switch; everything on AUX-HV.
- Barrier rule applied (sim/out/insulation/report.md): an HV-to-PELV part must be reinforced with working voltage
  >= 1100 V DC, impulse >= 8 kV, surge VIOSM >= 10 kV preferred, >= 8 mm, granted certification - judged against
  that rule, not against the TI part it replaces.
- LCSC: the mirror only holds JLCPCB-stocked parts and its free-text search is broken (it returns unrelated parts), so
  parts were identified by subcategory/package listings and exact LCSC-code lookups. Stock figures are the mirror's.
- Savings below are per module, incumbent at the costed-BOM price (TI 1k) against the alternate at the LCSC 1k break.
  Caveat: several TI incumbents are cheaper on LCSC than at TI list (AMC3330DWER 4.07, AMC3302DWER 2.25 USD @1k), so
  part of the "saving" is also available by buying the TI part through LCSC.

## 1. The isolated-amplifier decision (AMC3330 / AMC3302)

**Decision: Chipanalog (川土微, Shanghai) CA-IS1311BG replaces the AMC3330 on every channel and, behind a high-side gain
stage, the AMC3302. Its high side is supplied by Chipanalog CA-IS3115AW (1.5 W reinforced isolated DC/DC), one per HV
reference node. On the DAB the CA-IS3115AW is also the pin-compatible replacement of the UCC12050 that already feeds
each bridge's HV-side sensor island.**

| rating (barrier rule) | AMC3330 | AMC3302 | UCC12050 | **CA-IS1311BG** | **CA-IS3115AW** |
|---|---|---|---|---|---|
| VIOWM DC / VIORM | 1700 VDC / 1700 Vpk | 1700 / 1700 | 1697 / 1697 | **2121 VDC / 2121 Vpk** | **2121 VDC / 2121 Vpk** |
| VIMP (>= 8 kV) | 7700 V | - | - | **9846 V** | **9846 V** |
| VIOSM (>= 10 kV pref.) | 10000 V | 6250 V FAIL | 6250 V FAIL | **12800 V** (certified) | **12800 V** (certified) |
| VISO (UL 1577) | 4250 Vrms | 4250 Vrms | 5000 Vrms | **5000 Vrms** | **5000 Vrms** |
| CLR / CPG, CTI | >= 8 mm | >= 8 mm | > 8 mm | **8 mm, CTI > 600** | **> 8 mm, CTI > 600** |
| certificates | VDE 0884-17 | VDE (0884-11 style table) | see TI DS (not re-checked) | **VDE 0884-17 reinforced 40057278, UL E511334, CQC24001434134, TUV 2253313** | **same VDE/UL/TUV, CQC23001406424** |
| CMTI min | 85 kV/us | - | - | **100 kV/us** (BG; the plain G is only 15) | 150 typ |

Sources: CA-IS1311x v1.06 p6-7, CA-IS3115AW v1.09 p6-7 (docs/datasheets/sensing/, power-supply/); AMC/UCC figures from
the insulation report. Both Chipanalog parts pass the 4400 Vrms reinforced AC test the AMC parts miss (IC-03). They do
NOT fix the 3000/4000 m clearance problem (IC-01): still 8 mm bodies.

Why not the others: CA-IS1300G05G / B25G (the Chipanalog +/-50 mV and +/-250 mV twins of AMC3302 / AMC1300) list VIOSM
8000 Vpk with VDE "pending" in their current datasheets (v1.05, v1.02 Q1); the maker's web table shows 12.8 kV, which is
the 1.6 x test level. NOVOSENSE NSI1311 (LCSC-hosted Rev 1.2) has VIOSM 6250; NSI1312 (Rev 1.3) VIMP 6250; newer NOVOSENSE
revisions could not be downloaded (novosns.com serves its files only through its web app). 2Pai pi8300E is basic only.
CA-IS3105W (0.5 W isolated DC/DC) has VIORM 849 Vpk, VIOSM 6250 and only pending certificates - rejected.

What changes, per use (all FUNCTIONAL; details in the alternates rows). Pin map CA-IS1311BG (SOIC-8 WB) [DS p4]: 1 VDD1,
2 VIN, 3 SHTDN (active high with an internal pull-up: tie it to GND1, otherwise the part stays in shutdown and outputs
fail-safe), 4 GND1, 5 GND2, 6 VOUTN, 7 VOUTP, 8 VDD2; the AMC33x0 DC/DC, LDO and DIAG pins and their nine capacitors go.
1. **Port voltages V/VX** (PV-PORT 4, DAB60 4): CA-IS1311BG is single-ended -0.1...2.0 V, gain 1. Divider bottom
   4.99 k -> 10.0 k 0.1 % (2.0 V = 1202 V keeps the +/-2 V PORT full scale), filter 4.7 nF -> 2.2 nF (lag 22 us), INN
   tie removed. A reversed terminal reads <= 0 V; the existing "terminal voltage in window before precharge" rule blocks
   it.
2. **IMD channels** (PV-PORT AUX1, AUX2): AUX1 = V(PE - BUS-) >= 0 -> as item 1. AUX2 = V(PE - A+) <= 0 -> its bottom
   resistor (10 k) returns to a ~1.95 V Thevenin source from a 2.048 V series reference on the A+ node supply
   (V_in = 1.95 V + V/601 stays inside -0.1...2.0 V for V = -1100...0 V).
3. **Port current shunts** (AMC3302: PV-PORT 2, DAB60 2): shunt +/-64 mV x 15 + 1.000 V -> CA-IS1311BG (zero-drift op amp on
   the BUS- node supply), output 0...2 V with 1.0 V = 0 A; DIAG recreated by a PELV window comparator (fail-safe -2.5 V =
   high side lost; > 2.05 V = open Kelvin wire pulled up by 10 M). **Safety chain:** the contactor hold-closed logic
   (|I| > trip AND FB AND DIAG) must subtract the 1.0 V offset and take DIAG from the comparator; the port engineer
   re-derives the hold band and the proof-test windows. The AMC3302 FAILS the barrier rule today, so this block changes in
   any case; the cleaner path, if Chipanalog grants the CA-IS1300G05G a VDE certificate with VIMP >= 8 kV, is the twin
   part (same +/-50 mV, gain 41, fail-safe) - ask the maker. Verdict: OPTION (redesign needed).
4. **DAB AN1** (HOB current, bipolar): the CELL contract (+/-2 V around 1.44 V) is shared with the PV cells and must not
   change. HV side: the existing OPA4388 pair makes VIN = 1.0 V + k'(Uout - Uref) (k' = 2k, offset 0.4 x Uref); PELV side
   on DAB60: a re-centering difference amplifier (gain 2, -1.0 V) restores +/-2 V before the CELL connector and the
   DC-flux window. Fallback: keep the AMC3330 on these two channels (passes only with the SPD credit, IC-03).
5. **High-side supply**: CA-IS3115AW, 5.0 V (SEL = VISO), >= 240 mA, one per node: PV-PORT BUS- node (VA, VAX, VB, VBX,
   AUX1, IA, IB: 7 x 13.5 mA + ~10 mA) and A+ node (AUX2); DAB60 one per bridge (the UCC12050 replacement, carrying the
   HOB, the OPA4388/TLV3502 island and that port's three amplifiers). A lost node supply puts all its amplifiers into
   fail-safe at once - the firmware and the hold logic must treat any fail-safe as a trip (they already do for I).
6. **Re-run**: sim/port_design.py (divider lag, OV tolerance, hold band), gen/dab60.py design_check (OV comparator band
   with gain 0.3 % / offset 1.5 mV, response with tPD 2.1 us, 5 V budget with the CA-IS3115AW efficiency curve), the
   insulation audit (sim/insulation.py rows for these parts).

Money [calc, LCSC 1k for the alternates]: PV-P75 50.72 -> ~11.6 USD (**-39 USD**), DAB-D60 58.67 -> ~11.3 USD
(**-45 USD** incl. the AN1 re-centering stages; -4.5 USD of it is the UCC12050 swap alone). Availability is the weak point: LCSC (mirror) shows 13
CA-IS1311BG and 2 CA-IS3115AW in stock (547 of the low-CMTI CA-IS1311G); Chipanalog sells through its agents - a
second channel must be confirmed before release.

## 2. Other barrier parts

| part (board) | decision | insulation vs rule | money |
|---|---|---|---|
| UCC12050DVE (DAB60 x2) | **ADOPT CA-IS3115AW, DROP-IN** (pin 5 SYNC_OK unused -> tie to GND; SEL coding identical) | VIOSM 12.8 kV vs 6.25 kV (fixes FAIL IC-02) | 3.97 -> 1.70 USD |
| ISO7720FDWR (DAB60 x2) | KEEP. CA-IS3820LW is the near drop-in (pin 7 NC / pin 8 GNDA differ) but VIOSM 8 kV < ISO7720F 12.8 kV; CA-IS3820LWW (15 mm, 7.5 kVrms) is the OPTION if > 2000 m is ever claimed | Asian part weaker on surge | < 0.5 USD |
| TPSI3050-Q1 (PV-PORT x4, DAB60 x2) | KEEP: no Asian isolated switch driver with power transfer and published reinforced data. Note to the port engineer: Chipanalog CA-IS3417WT (1700 V SiC SSR made for insulation monitoring) could replace TPSI3050 + 2 x C2M1000170D per IMD test string once its certificates (pending, VIOSM 8 kV) are granted | passes today (coat) | - |
| ISO1042 / ISO1410 / SN6505B + WE 760390014 / 750315371 (SYS-IO-AUX, BMU-GW) | KEEP: functional PELV-FIELD barrier only; NOVOSENSE NSI1042 (CAN FD 5 Mbps, 5 kVrms, 10 kV surge) is the candidate twin but its datasheet could not be obtained (novosns.com web app) - to download by hand | functional | ~2 USD per port |
| ISO1212 / ISO1211 (SYS-IO-AUX) | KEEP on purpose: E-stop A/B and the spare trip loop (safety chain); the Asian DI isolators found are 8-channel parts that would put both E-stop channels in one package | functional | < 1 USD |

## 3. Current transducers

| part | alternate | key comparison | verdict |
|---|---|---|---|
| LEM LA 150-P (PVCELL-25 x3) | **Sinomags STB-150LA/ZN** (closed-loop TMR, aperture ~21 x 12 mm); second: CHIPSENSE CS1V 150 P00 | 5 V single supply (LA: +/-15 V -> the RS3-2415D with its 71 C limit disappears); BW 300 kHz typ (req. >= 200 kHz); response 0.3 us typ (req. delay <= 1.04 us; LA 0.5 us max); +/-0.8 % / +/-1.1 % at 85 C (LA 0.5 %); offset 5 mV = 1.2 A (firmware re-zero at idle); Ud 4 kV, Uw 8 kV, 12.9 mm, CTI 600, insulation class not stated -> keep the screened lead as supplementary insulation as today | OPTION (RFQ price; ask for max response and insulation class) |
| LEM HOB 130-P (DAB60 x2) | **Sinomags STK-HO/A 130** (open-loop TMR) | same pin functions 1 +Vc 2 GND 3 Vout 4 Vref 5 NC, primary 6-9 -> 10-13, body 35.4 x 25 vs 38 x 22 mm; 6.154 vs 8 mV/A, IPM +/-325 vs +/-250 A, 1 MHz, 0.2 us, Icc 7 vs 26 mA; accuracy +/-1.5 % (+/-3 % over temperature) and offset +/-10 mV are 2x worse -> OC ladder and DC-flux window re-tolerancing | OPTION (RFQ price) |

Both makers publish complete data sheets (isolation, electrical, step response, bandwidth plot, dimensions with pin
numbering); neither gives an AEC or dv/dt immunity figure (LEM gives none for these parts either). CHIPSENSE CS3A (the
13.5 x 10 mm LA-P look-alike) was rejected: basic 600 V only, 6.7 mm.

## 4. Ranked changes (saving per module, risk)

Savings [calc] = quantity x (incumbent price - alternate price) on the price basis printed in each alternates row (incumbent: costed BOM, mostly TI / distributor 1k; alternate: LCSC 100 or 1k break). Treat them as +/-30 %: the price bases are not uniform and several incumbents are themselves cheaper on LCSC. RFQ parts show '?'.

| # | change | class | verdict | PV-P75 USD | DAB-D60 USD | risk |
|---|---|---|---|---|---|---|
| 1 | AMC3330DWE (Texas Instruments) -> CA-IS1311BG (+ CA-IS3115AW high-side supply per HV node) (Chipanalog (Shanghai)) | FUNCTIONAL | ADOPT | 29.32 | 31.12 | medium |
| 2 | CSM2F-8518-L100J01 (Bourns) -> ARCS8518DL100A9 (Changsha Resi Electronics (RESI / C&B Electronics Shenzhen)) | FUNCTIONAL | ADOPT | 13.16 | 13.16 | medium |
| 3 | 1803293 (Phoenix Contact) -> KF2EDGR-3.81-4P (Cixi Kefa Electronic (KEFA)) | DROP-IN | ADOPT | 9.55 | 10.19 | low |
| 4 | TNPV12062M00BEEA (Vishay) -> FVF06FT-2004 (Prosperity Dielectrics (PDC)) | DROP-IN | ADOPT | 9.85 | 9.85 | low |
| 5 | AMC3302DWE (Texas Instruments) -> CA-IS1311BG + HV-side zero-drift gain stage (G = 15, +1.0 V offset) on the CA-IS3115AW node (Chipanalog (Shanghai)) | FUNCTIONAL | OPTION | 9.84 | 9.84 | medium-high (safety chain) |
| 6 | 61204021621 (Wurth Elektronik) -> X9555WV-2x20-6TV01 (XKB Connection) | DROP-IN | ADOPT | 8.17 | 7.00 | low |
| 7 | SSW-140-01-G-D (Samtec) -> X6521FV-2x40-C85D32 (XKB Connection) | FUNCTIONAL | OPTION | 7.46 | 7.46 | medium |
| 8 | 74439346100 (Wurth Elektronik) -> FXL0630-100-M (SZ Cjiang Technology) | FUNCTIONAL | OPTION | 7.59 | 6.08 | medium |
| 9 | 1803358 (Phoenix Contact) -> KF2EDGR-3.81-10P (Cixi Kefa Electronic (KEFA)) | DROP-IN | ADOPT | 6.35 | 6.35 | low |
| 10 | TSW-140-07-G-D (Samtec) -> X6521WV-2x40H-C60D30 (XKB Connection) | FUNCTIONAL | OPTION | 4.18 | 4.18 | medium |
| 11 | 1720479 (Phoenix Contact) -> ECH762R-03P (Dinkle Enterprise) | DROP-IN | OPTION | 4.03 | 4.03 | low |
| 12 | TNPW12064K99BEEA (Vishay) -> RT1206BRD074K99L (Yageo) | FUNCTIONAL | OPTION | 3.78 | 2.52 | medium |
| 13 | ZXTP25040DFHTA (Diodes Incorporated) -> PBSS5540X,135 (Nexperia) | FUNCTIONAL | OPTION | 5.69 | 0.00 | medium |
| 14 | ASDMB-25.000MHZ-XC-T (Abracon) -> SG-210STF 25.000000MHz Y (X1G004171005900) (Seiko Epson) | DROP-IN | OPTION | 2.73 | 2.73 | low |
| 15 | 74437368150 (Wurth Elektronik) -> FXL1040-150-M (SZ Cjiang Technology) | FUNCTIONAL | OPTION | 2.38 | 2.38 | medium |
| 16 | FTSH-110-01-L-DV-K (Samtec) -> X1270WVS-2x10B-9TV01 (XKB Connection) | DROP-IN | OPTION | 2.31 | 2.31 | low |
| 17 | UCC12050DVE (Texas Instruments) -> CA-IS3115AW (Chipanalog (Shanghai)) | DROP-IN | ADOPT | 0.00 | 4.55 | low |
| 18 | TSW-110-07-G-D (Samtec) -> X6521WV-2x10H-C60D30 (XKB Connection) | DROP-IN | ADOPT | 1.81 | 1.81 | low |
| 19 | SSW-105-01-G-D (Samtec) -> X6521FV-2x05-C85D32 (XKB Connection) | DROP-IN | ADOPT | 1.80 | 1.80 | low |
| 20 | 61202621621 (Wurth Elektronik) -> X9555WV-2x13-6TV01 (XKB Connection) | DROP-IN | ADOPT | 1.54 | 1.54 | low |
| 21 | 691322110002 (Wurth Elektronik) -> KF2EDGR-3.5-2P (Cixi Kefa Electronic (KEFA)) | DROP-IN | ADOPT | 1.02 | 1.02 | low |
| 22 | TSW-105-07-G-D (Samtec) -> X6521WV-2x05H-C60D30 (XKB Connection) | DROP-IN | ADOPT | 0.87 | 0.87 | low |
| 23 | 74438356022 (Wurth Elektronik) -> FXL0530-2R2-M (SZ Cjiang Technology) | FUNCTIONAL | OPTION | 0.87 | 0.87 | medium |
| 24 | 1803316 (Phoenix Contact) -> KF2EDGR-3.81-6P (Cixi Kefa Electronic (KEFA)) | DROP-IN | ADOPT | 0.82 | 0.82 | low |
| 25 | CDSOT23-SM712 (Bourns) -> SM712 (MDD (Microdiode Semiconductor, Shenzhen)) | DROP-IN | OPTION | 0.70 | 0.70 | low |
| 26 | TSW-102-07-G-S (Samtec) -> X6511WV-02H-C60D30 (XKB Connection) | DROP-IN | ADOPT | 0.80 | 0.20 | low |
| 27 | SMCJ33CA (Bourns) -> SMCJ33CA/TR13 (Brightking) | DROP-IN | OPTION | 0.35 | 0.35 | low |
| 28 | TSW-103-07-G-S (Samtec) -> X6511WV-03H-C60D30 (XKB Connection) | DROP-IN | ADOPT | 0.25 | 0.25 | low |
| 29 | SN74LVC08APWR (Texas Instruments) -> 74LVC08APW,118 (Nexperia) | DROP-IN | OPTION | 0.06 | 0.30 | low |
| 30 | BAT54C-7-F (Diodes Incorporated) -> BAT54C,215 (Nexperia) | DROP-IN | ADOPT | 0.18 | 0.18 | low |
| 31 | 742792641 (Wurth Elektronik) -> CBW160808U301T (Guangdong Fenghua Advanced Technology) | DROP-IN | ADOPT | 0.14 | 0.14 | low |
| 32 | FM24CL64B-GTR (Infineon) -> MB85RC64TAPNF-G-BDERE1 (Fujitsu Semiconductor Memory Solution (now RAMXEED)) | DROP-IN | ADOPT | 0.12 | 0.12 | low |
| 33 | 24LC256T-I/SN (Microchip) -> BL24C256A-PARC (Shanghai Belling) | DROP-IN | ADOPT | 0.11 | 0.11 | low |
| 34 | SN74LVC244ADWR (Texas Instruments) -> 74LVC244AD,118 (Nexperia) | DROP-IN | OPTION | 0.07 | 0.07 | low |
| 35 | BAT54S-7-F (Diodes Incorporated) -> BAT54S,215 (Nexperia) | DROP-IN | ADOPT | 0.06 | 0.06 | low |
| 36 | MMBT3904-7-F (Diodes Inc) -> MMBT3904,215 (Nexperia) | DROP-IN | ADOPT | 0.01 | 0.01 | low |
| 37 | LA 150-P (LEM) -> STB-150LA/ZN (aperture version) (Sinomags (Anhui Sinomags Technology)) | FUNCTIONAL | OPTION | ? | ? | medium |
| 38 | HOB 130-P (LEM) -> STK-HO/A 130 (Sinomags (Anhui Sinomags Technology)) | FUNCTIONAL | OPTION | ? | ? | medium |
| 39 | 691322310014 (Wurth Elektronik) -> KF2EDGR-3.81-14P (Cixi Kefa Electronic (KEFA)) | DROP-IN | OPTION | ? | ? | low |
| 40 | 1720466 (Phoenix Contact) -> ECH762R-02P (Dinkle Enterprise) | DROP-IN | OPTION | ? | ? | low |
| 41 | US1M-13-F (Diodes Incorporated) -> US1MH (Taiwan Semiconductor) | DROP-IN | OPTION | ? | ? | low |
| 42 | SMAJ12A (Bourns) -> SMAJ12AH (Taiwan Semiconductor) | DROP-IN | OPTION | ? | ? | low |
| 43 | SMAJ36A (Bourns) -> SMAJ36AH (Taiwan Semiconductor) | DROP-IN | OPTION | ? | ? | low |
| 44 | SMAJ33A (Bourns) -> SMAJ33AH (Taiwan Semiconductor) | DROP-IN | OPTION | ? | ? | low |
| 45 | TNPV12061M00BEEA (Vishay) -> ARHV06BTC1004A (Viking Tech) | DROP-IN | OPTION | ? | ? | low |
| 46 | TNPV1210124KBEEA (Vishay) -> ARHV13BTC1243A (Viking Tech) | DROP-IN | OPTION | ? | ? | low |
| 47 | MMBT3906-7-F (Diodes Inc) -> MMBT3906,215 (Nexperia) | DROP-IN | OPTION | -0.08 | -0.08 | low |
| 48 | SN74LVC1G74DCUR (Texas Instruments) -> 74LVC1G74DC,125 (Nexperia) | DROP-IN | OPTION | -0.11 | -0.09 | low |
| 49 | SN74LVC1G32DBVR (Texas Instruments) -> 74LVC1G32GV,125 (Nexperia) | DROP-IN | OPTION | -0.27 | -0.27 | low |
| 50 | SN74LVC1G17DBVR (Texas Instruments) -> 74LVC1G17GV,125 (Nexperia) | DROP-IN | OPTION | -1.34 | -1.02 | low |

Totals [calc]: ADOPT 86 USD per PV-P75, 91 USD per DAB-D60; OPTION (priced ones) a further 50 / 42 USD; RFQ items (LA 150-P, HOB 130-P, Viking TNPV twins, TSC diodes) not counted.

## 5. Per board: the ADOPT changes for the board designers

### PV-PORT
- **AMC3330DWE -> CA-IS1311BG (+ CA-IS3115AW high-side supply per HV node)** (Chipanalog (Shanghai), FUNCTIONAL). Voltage channels (unipolar): divider bottom 4.99 k -> 10.0 k 0.1 % (6 M + 10 k: 2.0 V = 1202 V keeps the +/-2 V PORT full scale with gain 1), filter 4.7 nF -> 2.2 nF (lag 22 us <= 30 us), INN tie disappears (single-ended VIN to HGND). Terminal-side VAX/VBX read <= 0 for a reversed terminal: the firmware 'terminal in window before precharge' rule already blocks it. PV-PORT IMD: AUX1 = V(PE-BUS-) is >= 0 -> direct; AUX2 = V(PE-A+) is <= 0 -> the bottom resistor (10 k) returns to a ~1.95 V Thevenin source taken from a 2.048 V series reference on the A+ node supply (V_in = 1.95 V + V/601 stays inside -0.1...2.0 V for V = -1100...0 V). DAB AN1 (bipolar HOB signal; the CELL contract +/-2 V on 1.44 V is shared with the PV cells and must not change): HV side VIN = 1.0 V + k'(Uout - Uref) from the existing OPA4388 pair (k' = 2k, offset 0.4 x Uref) and a PELV-side re-centering difference amplifier on DAB60 (gain 2, -1.0 V) restores +/-2 V before the CELL connector and the DC-flux window; fallback: keep the AMC3330 on the two AN1 channels (passes only with the SPD credit, IC-03). High side: one CA-IS3115AW (5 V, >= 240 mA) per HV reference node - PV-PORT: BUS- node (VA, VAX, VB, VBX, AUX1, IA, IB) and A+ node (AUX2); DAB60: the CA-IS3115AW that replaces each UCC12050 also feeds the port V/VX/I amplifiers of that bridge. A shared supply loss puts every amplifier on that node into fail-safe at once: firmware and the port hold logic must treat any fail-safe as a trip (it already does for I). Re-run: port_design divider/lag + OV tolerance, DAB60 design_check (OV comparator band with gain 0.3 %/offset 1.5 mV, response with tPD 2.1 us), insulation audit (rows AMC3330 -> CA-IS1311BG pass at 2000 m; still 8 mm -> 3000/4000 m clearance issue IC-01 unchanged).
  - checked: input -0.1..2.0 V single-ended, 1 GOhm, +/-15 nA (AMC3330 +/-1 V differential); gain 1 (2); no integrated DC/DC: needs VDD1 3.0-5.5 V, IDD1 <= 13.5 mA [DS p9]; gain error +/-0.3 % / +/-40 ppm/C (+/-0.2 %); offset +/-1.5 mV / +/-15 uV/C (+/-0.3 mV); nonlinearity +/-0.08 %; BW 220 kHz min / 275 typ; tPD 2.1 us max; VCMOUT 1.44 V (same); ROUT < 0.2 ohm; fail-safe -2.6..-2.5 V on VDD1 loss/UV/SHTDN (same sense as AMC3330 -2.57 V); CMTI >= 100 kV/us (AMC3330 85 min); SOIC-8 wide body instead of SOIC-16 wide; -40..125 C. Insulation better than AMC3330 on every row: VIMP 9.8 vs 7.7 kV, VIOSM 12.8 vs 10 kV, VISO 5000 vs 4250 Vrms (passes the 4400 Vrms reinforced AC test of IC-03), VIORM 2121 vs 1700 Vpk
  - datasheet: docs/datasheets/sensing/CA-IS1311x.pdf; docs/datasheets/power-supply/CA-IS3115AW.pdf (CA-IS1311x v1.06 p2 (ordering, CMTI grades), p4 (pins), p5 (ratings), p6-7 (isolation, certificates), p8-9 (electrical); CA-IS3115AW v1.09 p4-8)
- **CSM2F-8518-L100J01 -> ARCS8518DL100A9** (Changsha Resi Electronics (RESI / C&B Electronics Shenzhen), FUNCTIONAL). pin map: 1/2 = bolt terminals, 3/4 = the two sense pins (A9 'PIN' structure) - schematic unchanged; MECHANICAL: M6 bolts (7.0 mm holes) and new sense-pin positions for the busbar / sense-lead design (busbar not yet drawn); price 9.99 USD is the DigiKey 25-pc tier (100-pc tier not shown, so <= 9.99) vs Bourns 19.24 at 25 / 16.57 at 100; re-run sim/port_design.py with SH_TOL 0.005, SH_TCR 150e-6 (worst side), SH_EMF 0.5e-6: hold band narrows, compensated error ~0.84 % (A_TCR_RES 50 ppm/K residual still to be characterised on samples); J-grade (5 %) code ARCS8518JL100A9 exists if cheaper
  - checked: same 8518 body 85x18x3 mm, 60 mm hole pitch, Mn-Cu e-beam type; 100 uOhm 36 W (rated current 600 A) = ; tolerance 0.5 % vs 5 % (better); TCR on the pin variant 100 ppm/K (+20..+175 C) / 150 ppm/K (-55..+20 C) vs 125 ppm/K on test points (alloy < 50); thermal EMF < 0.5 vs < 1.5 uV/K (better); L < 3 nH; current coefficient < 7 ppm/A (Bourns: not stated); range -55..+175 C (TCR/derating) vs -40..+170 C; load life 1000 h +/-1.0 % = ; thermal shock 1000 cyc +/-0.5 % vs +/-1.0 %; HTE 170 C 1000 h +/-1.0 % vs +/-1.5 %; 85/85 +/-0.5 % = ; BOLT HOLES 2 x 7.0 mm vs 8.3 mm (M6 instead of M8); sense pins at other positions; pulse energy only as a curve (p3, not read numerically) - Bourns states no number either
  - datasheet: docs/datasheets/sensing/RESI-ARCS8518.pdf (1-3)
- **TNPV12062M00BEEA -> FVF06FT-2004** (Prosperity Dielectrics (PDC), DROP-IN). saving 0.985 USD x 10 = 9.85 USD per PV-P75 and per DAB-D60 (vs LCSC TNPV 0.9989; 7.86 vs the BOM estimate 0.80); checks to re-run: bias current +/-4 % (1 % + drift) -> Miller energy 107 mJ +4 % and latch-holding current (23 uA vs 1.3 uA) in sim/port_design.py; add FVF06FT-2004 to sim/insulation.py ELEMENTS (umax 800, upulse 1600, gap 1.7) and re-run; JLC stock only 5 - order the -M code via PDC distribution (LCSC sells the standard code); Viking ARHV06BTC2004A is the precision DROP-IN if ever needed (no price)
  - checked: 1206 both, 2 pins; 2.00 M; max RCWV 800 V (707 V at 2 M) vs TNPV Umax 700 V; overload 1600 V vs pulse 1400 V; 0.25 W both; -55..+155 C vs -55..+125 C; tolerance 1 % vs 0.1 %, TCR 100 vs 25 ppm/K, life 1000 h at 125 C dR <= 3 % vs 0.05 % - precision does NOT matter here: element 200 V nominal / 260 V (1.3 share) / 286 V transient vs 0.5 x 707 V; internal string, no impulse share (insulation audit); gap need 1.6 mm (PD2 board, 260 V) vs FVF06 body gap >= 1.7 mm and 1206 pads; thick film
  - datasheet: docs/datasheets/passives-capacitors/PDC-FVF.pdf (2-4, 7-8)
- **61202621621 -> X9555WV-2x13-6TV01** (XKB Connection, DROP-IN). Incumbent price from the costed BOM (Farnell JP 1-pc, JPY 158.5). Saving 0.77 USD x 2 per module. Ribbon-cable IDC sockets are in the harness, not the BOM; any standard 2.54 mm IDC socket mates both.
  - checked: pitch 2.54, 2xN shrouded box header, vertical THT, pin 0.64 mm sq; XKB drawing marks pin 1 with a triangle on the housing and prints no numerals (numbering follows the standard odd/even IDC scheme, Conn_02xNN_Odd_Even); XKB 3 A AC/DC, 250 V, 1000 V AC withstand, -40..+125 C (6T/PA9T UL94 V0), gold flash vs Wurth WR-BHD 3 A, 250 V, 600 V AC, -40..+105 C, PBT UL94 V0, gold, 30 mating cycles, UL E323964; body length 40.62 mm (2x13) / 58.40 mm (2x20) vs Wurth 40.68 / 58.46 mm (same pin-to-pin 30.48 / 48.26 mm); no mating-cycle figure on the XKB sheet; PORT contract carries 24 V at 0.7 A for 200 s (battery port) on two pins: 0.35 A/pin, far under 3 A
  - datasheet: docs/datasheets/connectors/XKB-X9555WV.pdf (p1 (spec, part-number key, dimension table 2x03-2x32))
- **691322110002 -> KF2EDGR-3.5-2P** (Cixi Kefa Electronic (KEFA), DROP-IN). Signal-level connector, no current or voltage stress in our use (sensor on the heatsink, PELV). Incumbent price is the costed-BOM ESTIMATE (0.55 USD). Plug of the same maker needed in the harness. Plug-side wire range of KF2EDGK-3.5 is AWG 28-16 (3.81 sheet read; 3.5 plug sheet not read).
  - checked: pitch 3.5, 2 poles, right angle, 0.8 mm pins, hole 1.4 mm equal; Wurth 10 A / 300 V (cULus), 10.5 A VDE vs KEFA UL 300 V 8 A (B) / 10 A (D), IEC 160 V 7 A; both -40..+105 C, PA66 UL94 V0, tin; NTC bias current is microamps; mating plug KF2EDGK-3.5-2P (C440847, 0.1705 at 100)
  - datasheet: docs/datasheets/connectors/KEFA-KF2EDGR-3.5.pdf; incumbent docs/datasheets/connectors/WE-691322110002.pdf (KEFA p1; Wurth p1)
- **MMBT3904-7-F -> MMBT3904,215** (Nexperia, DROP-IN). Consolidation onto Nexperia + AEC-Q101 grade, 0.0292 vs 0.0362 USD. The discharge-timer latch runs at uA-mA base currents: hFE at 0.1 mA >= 60 is guaranteed by the Nexperia sheet; re-read the Diodes table before relying on a tighter figure. Owner of the discharge-timer timing numbers (gen/port.py n['dis_*']) should re-run the check; no schematic change.
  - checked: Same SOT-23 pinout 1 B, 2 E, 3 C. VCEO 40=40 V; VCBO 60=60 V; VEBO 6=6 V; IC 200=200 mA; hFE >=60/80/100-300/60/30 at 0.1/1/10/50/100 mA (JEDEC 2N3904 table, same as Diodes class); VCEsat <=200 mV (10 mA) / 300 mV (50 mA); VBEsat 650-850 mV (10 mA), <=950 mV (50 mA); ICBO <=50 nA at 30 V; fT >=300 MHz; Cc <=4 pF; Ptot 250 mW at 25 C (Diodes 310 mW min pad); Tamb -65..150 vs -55..150 C; AEC-Q101 qualified (the -7-F is standard grade)
  - datasheet: docs/datasheets/power-semiconductors/Nexperia-MMBT3904.pdf (1-4)

### DAB60
- **AMC3330DWE -> CA-IS1311BG (+ CA-IS3115AW high-side supply per HV node)** (Chipanalog (Shanghai), FUNCTIONAL). Voltage channels (unipolar): divider bottom 4.99 k -> 10.0 k 0.1 % (6 M + 10 k: 2.0 V = 1202 V keeps the +/-2 V PORT full scale with gain 1), filter 4.7 nF -> 2.2 nF (lag 22 us <= 30 us), INN tie disappears (single-ended VIN to HGND). Terminal-side VAX/VBX read <= 0 for a reversed terminal: the firmware 'terminal in window before precharge' rule already blocks it. PV-PORT IMD: AUX1 = V(PE-BUS-) is >= 0 -> direct; AUX2 = V(PE-A+) is <= 0 -> the bottom resistor (10 k) returns to a ~1.95 V Thevenin source taken from a 2.048 V series reference on the A+ node supply (V_in = 1.95 V + V/601 stays inside -0.1...2.0 V for V = -1100...0 V). DAB AN1 (bipolar HOB signal; the CELL contract +/-2 V on 1.44 V is shared with the PV cells and must not change): HV side VIN = 1.0 V + k'(Uout - Uref) from the existing OPA4388 pair (k' = 2k, offset 0.4 x Uref) and a PELV-side re-centering difference amplifier on DAB60 (gain 2, -1.0 V) restores +/-2 V before the CELL connector and the DC-flux window; fallback: keep the AMC3330 on the two AN1 channels (passes only with the SPD credit, IC-03). High side: one CA-IS3115AW (5 V, >= 240 mA) per HV reference node - PV-PORT: BUS- node (VA, VAX, VB, VBX, AUX1, IA, IB) and A+ node (AUX2); DAB60: the CA-IS3115AW that replaces each UCC12050 also feeds the port V/VX/I amplifiers of that bridge. A shared supply loss puts every amplifier on that node into fail-safe at once: firmware and the port hold logic must treat any fail-safe as a trip (it already does for I). Re-run: port_design divider/lag + OV tolerance, DAB60 design_check (OV comparator band with gain 0.3 %/offset 1.5 mV, response with tPD 2.1 us), insulation audit (rows AMC3330 -> CA-IS1311BG pass at 2000 m; still 8 mm -> 3000/4000 m clearance issue IC-01 unchanged).
  - checked: input -0.1..2.0 V single-ended, 1 GOhm, +/-15 nA (AMC3330 +/-1 V differential); gain 1 (2); no integrated DC/DC: needs VDD1 3.0-5.5 V, IDD1 <= 13.5 mA [DS p9]; gain error +/-0.3 % / +/-40 ppm/C (+/-0.2 %); offset +/-1.5 mV / +/-15 uV/C (+/-0.3 mV); nonlinearity +/-0.08 %; BW 220 kHz min / 275 typ; tPD 2.1 us max; VCMOUT 1.44 V (same); ROUT < 0.2 ohm; fail-safe -2.6..-2.5 V on VDD1 loss/UV/SHTDN (same sense as AMC3330 -2.57 V); CMTI >= 100 kV/us (AMC3330 85 min); SOIC-8 wide body instead of SOIC-16 wide; -40..125 C. Insulation better than AMC3330 on every row: VIMP 9.8 vs 7.7 kV, VIOSM 12.8 vs 10 kV, VISO 5000 vs 4250 Vrms (passes the 4400 Vrms reinforced AC test of IC-03), VIORM 2121 vs 1700 Vpk
  - datasheet: docs/datasheets/sensing/CA-IS1311x.pdf; docs/datasheets/power-supply/CA-IS3115AW.pdf (CA-IS1311x v1.06 p2 (ordering, CMTI grades), p4 (pins), p5 (ratings), p6-7 (isolation, certificates), p8-9 (electrical); CA-IS3115AW v1.09 p4-8)
- **CSM2F-8518-L100J01 -> ARCS8518DL100A9** (Changsha Resi Electronics (RESI / C&B Electronics Shenzhen), FUNCTIONAL). pin map: 1/2 = bolt terminals, 3/4 = the two sense pins (A9 'PIN' structure) - schematic unchanged; MECHANICAL: M6 bolts (7.0 mm holes) and new sense-pin positions for the busbar / sense-lead design (busbar not yet drawn); price 9.99 USD is the DigiKey 25-pc tier (100-pc tier not shown, so <= 9.99) vs Bourns 19.24 at 25 / 16.57 at 100; re-run sim/port_design.py with SH_TOL 0.005, SH_TCR 150e-6 (worst side), SH_EMF 0.5e-6: hold band narrows, compensated error ~0.84 % (A_TCR_RES 50 ppm/K residual still to be characterised on samples); J-grade (5 %) code ARCS8518JL100A9 exists if cheaper
  - checked: same 8518 body 85x18x3 mm, 60 mm hole pitch, Mn-Cu e-beam type; 100 uOhm 36 W (rated current 600 A) = ; tolerance 0.5 % vs 5 % (better); TCR on the pin variant 100 ppm/K (+20..+175 C) / 150 ppm/K (-55..+20 C) vs 125 ppm/K on test points (alloy < 50); thermal EMF < 0.5 vs < 1.5 uV/K (better); L < 3 nH; current coefficient < 7 ppm/A (Bourns: not stated); range -55..+175 C (TCR/derating) vs -40..+170 C; load life 1000 h +/-1.0 % = ; thermal shock 1000 cyc +/-0.5 % vs +/-1.0 %; HTE 170 C 1000 h +/-1.0 % vs +/-1.5 %; 85/85 +/-0.5 % = ; BOLT HOLES 2 x 7.0 mm vs 8.3 mm (M6 instead of M8); sense pins at other positions; pulse energy only as a curve (p3, not read numerically) - Bourns states no number either
  - datasheet: docs/datasheets/sensing/RESI-ARCS8518.pdf (1-3)
- **1803293 -> KF2EDGR-3.81-4P** (Cixi Kefa Electronic (KEFA), DROP-IN). Order the KEFA plug KF2EDGK-3.81-4P (C440860, 0.2093 at 100) with the header; the harness plug is not a BOM line, so the harness drawing must name it. Incumbent is itself on LCSC (C480536, stock 1940, 0.7208 at 100) so supply is not the issue; saving 0.64 USD x 15 = 9.6 USD per PV-P75, 10.2 per DAB-D60. phoenixcontact.com refuses scripted downloads (HTTP 403); the Phoenix sheet now on file is the LCSC-hosted copy of the 2010 catalog extract, which has no operating temperature and no contact numbering, so the temperature-audit gap stays for the incumbent; the alternate has a stated range. Second sources: Kangnex WJ15EDGRC-3.81-04P-14-00A (C7245, 79662 in stock, 0.0583; UL 300 V 8 A, IEC 160 V 7 A, -40..+105 C), Degson 15EDGRC-3.81-04P-14-00A(H) (UL 300 V 8 A, IEC 250 V 7 A, -40..105 C 'depending on derating curve'). To request from KEFA: UL file number and VDE certificate. No pin numbers are printed on any of the sheets; pin 1 is a footprint convention.
  - checked: pitch 3.81 / 1 row / right angle / 0.8 mm square pins equal; UL 300 V 8 A = 300 V 8 A; IEC 160 V (III/2) = 160 V (III/2), impulse 2.5 kV = 2.5 kV; IEC current 7 A vs 8 A (our worst case is 3.6 A, the fan-channel eFuse limit); tin plating both; PA66 UL94 V0 vs PBT UL94 V0; operating range -40..+105 C stated vs none stated in the Phoenix extract; solder pin 3.7 vs 3.4 mm, hole 1.4 vs 1.2 mm, body P*3.81+0.80 vs P*3.81+1.39 mm and edge offset differ by about 0.25-0.6 mm (re-check the footprint at layout); pin map: 1..4 in a row, same as the symbol (J_MC4 pins 1-4); mating plug KF2EDGK-3.81-4P (C440860) is UL 300 V 8 A, AWG 28-16, M2 screw 0.2 N.m
  - datasheet: docs/datasheets/connectors/KEFA-KF2EDGR-3.81.pdf (header); docs/datasheets/connectors/KEFA-KF2EDGK-3.81.pdf (mating plug) (header p1 (drawing and data table); plug p1)
- **TNPV12062M00BEEA -> FVF06FT-2004** (Prosperity Dielectrics (PDC), DROP-IN). saving 0.985 USD x 10 = 9.85 USD per PV-P75 and per DAB-D60 (vs LCSC TNPV 0.9989; 7.86 vs the BOM estimate 0.80); checks to re-run: bias current +/-4 % (1 % + drift) -> Miller energy 107 mJ +4 % and latch-holding current (23 uA vs 1.3 uA) in sim/port_design.py; add FVF06FT-2004 to sim/insulation.py ELEMENTS (umax 800, upulse 1600, gap 1.7) and re-run; JLC stock only 5 - order the -M code via PDC distribution (LCSC sells the standard code); Viking ARHV06BTC2004A is the precision DROP-IN if ever needed (no price)
  - checked: 1206 both, 2 pins; 2.00 M; max RCWV 800 V (707 V at 2 M) vs TNPV Umax 700 V; overload 1600 V vs pulse 1400 V; 0.25 W both; -55..+155 C vs -55..+125 C; tolerance 1 % vs 0.1 %, TCR 100 vs 25 ppm/K, life 1000 h at 125 C dR <= 3 % vs 0.05 % - precision does NOT matter here: element 200 V nominal / 260 V (1.3 share) / 286 V transient vs 0.5 x 707 V; internal string, no impulse share (insulation audit); gap need 1.6 mm (PD2 board, 260 V) vs FVF06 body gap >= 1.7 mm and 1206 pads; thick film
  - datasheet: docs/datasheets/passives-capacitors/PDC-FVF.pdf (2-4, 7-8)
- **61204021621 -> X9555WV-2x20-6TV01** (XKB Connection, DROP-IN). Incumbent price is the costed-BOM ESTIMATE (scaled from the 2x13). Saving 1.17 USD x 7 per PV-P75, x 6 per DAB-D60. The sheet in the repo is the 2x13 drawing; the 2x20 appears in its dimension table (A 48.26, B 56.40, C 58.40). 1444 in stock on the mirror.
  - checked: pitch 2.54, 2xN shrouded box header, vertical THT, pin 0.64 mm sq; XKB drawing marks pin 1 with a triangle on the housing and prints no numerals (numbering follows the standard odd/even IDC scheme, Conn_02xNN_Odd_Even); XKB 3 A AC/DC, 250 V, 1000 V AC withstand, -40..+125 C (6T/PA9T UL94 V0), gold flash vs Wurth WR-BHD 3 A, 250 V, 600 V AC, -40..+105 C, PBT UL94 V0, gold, 30 mating cycles, UL E323964; body length 40.62 mm (2x13) / 58.40 mm (2x20) vs Wurth 40.68 / 58.46 mm (same pin-to-pin 30.48 / 48.26 mm); no mating-cycle figure on the XKB sheet; CELL contract: +24V/+24V_GD on 2 pins (<= 1.0 A continuous, eFuse 1.49 A) = about 0.75 A/pin, PWM/analog signals otherwise
  - datasheet: docs/datasheets/connectors/XKB-X9555WV.pdf (p1)
- **UCC12050DVE -> CA-IS3115AW** (Chipanalog (Shanghai), DROP-IN). Fixes the UCC12050 insulation FAIL (VIOSM 6250 < 8000). Netlist change: U501/U1001 pin 5 None -> GND. Re-run DAB60 design_check 5 V budget with the CA-IS3115AW efficiency (60 % at 300 mA; light-load curve p11 - read it, the script assumes 45 %) and the insulation audit rows DAB60 U501/U1001. EMI: on-chip transformer converter, no CISPR statement for this part (Chipanalog gives layout advice p17) - include in the EMC pre-scan. The extra 1 W headroom lets this supply also feed the CA-IS1311BG port amplifiers of the same bridge.
  - checked: pin map checked against DAB60 netlist: 1 EN, 2 GNDP, 3 VINP, 13 SEL (= VISO -> 5.0 V, same SEL coding 5.0/5.4/3.3/3.7), 14 VISO, 9-12/15/16 GNDS identical; pin 4 SYNC (tied to GND here) = GNDP; pin 5 SYNC_OK (unused, NC here) = GNDP -> tie pin 5 to GND; pins 6-8 GNDP (GND here). 1.5 W / 240 mA min at 5 V (UCC12050 0.5 W); VISO 4.75-5.25 V; no-load input 4.9 mA typ; CVISO 4.7-1000 uF (10 uF fitted); soft start, hiccup short-circuit, OTP, EN; no SYNC; -40..125 C; MSL 3. Insulation: VIOSM 12.8 kV vs 6.25 kV (the UCC12050 FAILS IC-02, this passes)
  - datasheet: docs/datasheets/power-supply/CA-IS3115AW.pdf (v1.09 p1 (features), p4 (pins), p5-6 (ratings, isolation), p7 (certificates), p8 (electrical))
- **61202621621 -> X9555WV-2x13-6TV01** (XKB Connection, DROP-IN). Incumbent price from the costed BOM (Farnell JP 1-pc, JPY 158.5). Saving 0.77 USD x 2 per module. Ribbon-cable IDC sockets are in the harness, not the BOM; any standard 2.54 mm IDC socket mates both.
  - checked: pitch 2.54, 2xN shrouded box header, vertical THT, pin 0.64 mm sq; XKB drawing marks pin 1 with a triangle on the housing and prints no numerals (numbering follows the standard odd/even IDC scheme, Conn_02xNN_Odd_Even); XKB 3 A AC/DC, 250 V, 1000 V AC withstand, -40..+125 C (6T/PA9T UL94 V0), gold flash vs Wurth WR-BHD 3 A, 250 V, 600 V AC, -40..+105 C, PBT UL94 V0, gold, 30 mating cycles, UL E323964; body length 40.62 mm (2x13) / 58.40 mm (2x20) vs Wurth 40.68 / 58.46 mm (same pin-to-pin 30.48 / 48.26 mm); no mating-cycle figure on the XKB sheet; PORT contract carries 24 V at 0.7 A for 200 s (battery port) on two pins: 0.35 A/pin, far under 3 A
  - datasheet: docs/datasheets/connectors/XKB-X9555WV.pdf (p1 (spec, part-number key, dimension table 2x03-2x32))
- **691322110002 -> KF2EDGR-3.5-2P** (Cixi Kefa Electronic (KEFA), DROP-IN). Signal-level connector, no current or voltage stress in our use (sensor on the heatsink, PELV). Incumbent price is the costed-BOM ESTIMATE (0.55 USD). Plug of the same maker needed in the harness. Plug-side wire range of KF2EDGK-3.5 is AWG 28-16 (3.81 sheet read; 3.5 plug sheet not read).
  - checked: pitch 3.5, 2 poles, right angle, 0.8 mm pins, hole 1.4 mm equal; Wurth 10 A / 300 V (cULus), 10.5 A VDE vs KEFA UL 300 V 8 A (B) / 10 A (D), IEC 160 V 7 A; both -40..+105 C, PA66 UL94 V0, tin; NTC bias current is microamps; mating plug KF2EDGK-3.5-2P (C440847, 0.1705 at 100)
  - datasheet: docs/datasheets/connectors/KEFA-KF2EDGR-3.5.pdf; incumbent docs/datasheets/connectors/WE-691322110002.pdf (KEFA p1; Wurth p1)
- **MMBT3904-7-F -> MMBT3904,215** (Nexperia, DROP-IN). Consolidation onto Nexperia + AEC-Q101 grade, 0.0292 vs 0.0362 USD. The discharge-timer latch runs at uA-mA base currents: hFE at 0.1 mA >= 60 is guaranteed by the Nexperia sheet; re-read the Diodes table before relying on a tighter figure. Owner of the discharge-timer timing numbers (gen/port.py n['dis_*']) should re-run the check; no schematic change.
  - checked: Same SOT-23 pinout 1 B, 2 E, 3 C. VCEO 40=40 V; VCBO 60=60 V; VEBO 6=6 V; IC 200=200 mA; hFE >=60/80/100-300/60/30 at 0.1/1/10/50/100 mA (JEDEC 2N3904 table, same as Diodes class); VCEsat <=200 mV (10 mA) / 300 mV (50 mA); VBEsat 650-850 mV (10 mA), <=950 mV (50 mA); ICBO <=50 nA at 30 V; fT >=300 MHz; Cc <=4 pF; Ptot 250 mW at 25 C (Diodes 310 mW min pad); Tamb -65..150 vs -55..150 C; AEC-Q101 qualified (the -7-F is standard grade)
  - datasheet: docs/datasheets/power-semiconductors/Nexperia-MMBT3904.pdf (1-4)

### PVCELL-25
- **61204021621 -> X9555WV-2x20-6TV01** (XKB Connection, DROP-IN). Incumbent price is the costed-BOM ESTIMATE (scaled from the 2x13). Saving 1.17 USD x 7 per PV-P75, x 6 per DAB-D60. The sheet in the repo is the 2x13 drawing; the 2x20 appears in its dimension table (A 48.26, B 56.40, C 58.40). 1444 in stock on the mirror.
  - checked: pitch 2.54, 2xN shrouded box header, vertical THT, pin 0.64 mm sq; XKB drawing marks pin 1 with a triangle on the housing and prints no numerals (numbering follows the standard odd/even IDC scheme, Conn_02xNN_Odd_Even); XKB 3 A AC/DC, 250 V, 1000 V AC withstand, -40..+125 C (6T/PA9T UL94 V0), gold flash vs Wurth WR-BHD 3 A, 250 V, 600 V AC, -40..+105 C, PBT UL94 V0, gold, 30 mating cycles, UL E323964; body length 40.62 mm (2x13) / 58.40 mm (2x20) vs Wurth 40.68 / 58.46 mm (same pin-to-pin 30.48 / 48.26 mm); no mating-cycle figure on the XKB sheet; CELL contract: +24V/+24V_GD on 2 pins (<= 1.0 A continuous, eFuse 1.49 A) = about 0.75 A/pin, PWM/analog signals otherwise
  - datasheet: docs/datasheets/connectors/XKB-X9555WV.pdf (p1)
- **TSW-102-07-G-S -> X6511WV-02H-C60D30** (XKB Connection, DROP-IN). 0.2 USD saving per piece x 5 pieces per PV-P75 is small; the point is one maker and an LCSC-stocked part (5511 in stock).
  - checked: single row 1x2, pins 1-2; 3 A, 250 V, -40..+105 C, 1 u-inch gold, PA6T UL94 V0 vs Samtec 4.7 A (gold) / -55..+105 C as recorded; jumper/shunt use only, no current
  - datasheet: docs/datasheets/connectors/XKB-X6511W.pdf (p1)

### CTRL-C2000
- **61204021621 -> X9555WV-2x20-6TV01** (XKB Connection, DROP-IN). Incumbent price is the costed-BOM ESTIMATE (scaled from the 2x13). Saving 1.17 USD x 7 per PV-P75, x 6 per DAB-D60. The sheet in the repo is the 2x13 drawing; the 2x20 appears in its dimension table (A 48.26, B 56.40, C 58.40). 1444 in stock on the mirror.
  - checked: pitch 2.54, 2xN shrouded box header, vertical THT, pin 0.64 mm sq; XKB drawing marks pin 1 with a triangle on the housing and prints no numerals (numbering follows the standard odd/even IDC scheme, Conn_02xNN_Odd_Even); XKB 3 A AC/DC, 250 V, 1000 V AC withstand, -40..+125 C (6T/PA9T UL94 V0), gold flash vs Wurth WR-BHD 3 A, 250 V, 600 V AC, -40..+105 C, PBT UL94 V0, gold, 30 mating cycles, UL E323964; body length 40.62 mm (2x13) / 58.40 mm (2x20) vs Wurth 40.68 / 58.46 mm (same pin-to-pin 30.48 / 48.26 mm); no mating-cycle figure on the XKB sheet; CELL contract: +24V/+24V_GD on 2 pins (<= 1.0 A continuous, eFuse 1.49 A) = about 0.75 A/pin, PWM/analog signals otherwise
  - datasheet: docs/datasheets/connectors/XKB-X9555WV.pdf (p1)
- **TSW-110-07-G-D -> X6521WV-2x10H-C60D30** (XKB Connection, DROP-IN). Expansion header for ribbon/test use, no power pins. Saving about 1.8 USD per module. Female partner (not in the BOM) would be X6521FV-2x10-C85D32 (C718226).
  - checked: pin numbering drawn (1,3,5.. / 2,4,6..); 3 A, 250 V, -40..+105 C, 1 u-inch gold, PA6T UL94 V0 vs Samtec 4.7 A, -55..+125 C; signal-level use only; post 6.0 vs 5.84 mm, tail 3.0 vs 3.05 mm, plastic 2.5 mm, pin 0.64 mm square
  - datasheet: docs/datasheets/connectors/XKB-X6521W.pdf (p1)
- **61202621621 -> X9555WV-2x13-6TV01** (XKB Connection, DROP-IN). Incumbent price from the costed BOM (Farnell JP 1-pc, JPY 158.5). Saving 0.77 USD x 2 per module. Ribbon-cable IDC sockets are in the harness, not the BOM; any standard 2.54 mm IDC socket mates both.
  - checked: pitch 2.54, 2xN shrouded box header, vertical THT, pin 0.64 mm sq; XKB drawing marks pin 1 with a triangle on the housing and prints no numerals (numbering follows the standard odd/even IDC scheme, Conn_02xNN_Odd_Even); XKB 3 A AC/DC, 250 V, 1000 V AC withstand, -40..+125 C (6T/PA9T UL94 V0), gold flash vs Wurth WR-BHD 3 A, 250 V, 600 V AC, -40..+105 C, PBT UL94 V0, gold, 30 mating cycles, UL E323964; body length 40.62 mm (2x13) / 58.40 mm (2x20) vs Wurth 40.68 / 58.46 mm (same pin-to-pin 30.48 / 48.26 mm); no mating-cycle figure on the XKB sheet; PORT contract carries 24 V at 0.7 A for 200 s (battery port) on two pins: 0.35 A/pin, far under 3 A
  - datasheet: docs/datasheets/connectors/XKB-X9555WV.pdf (p1 (spec, part-number key, dimension table 2x03-2x32))
- **TSW-103-07-G-S -> X6511WV-03H-C60D30** (XKB Connection, DROP-IN). Signal-level; stock 11665.
  - checked: single row 1x3, pins 1-3; 3 A, 250 V, -40..+105 C, 1 u-inch gold vs Samtec 4.7 A; logic-level signals only
  - datasheet: docs/datasheets/connectors/XKB-X6511W.pdf (p1)
- **742792641 -> CBW160808U301T** (Guangdong Fenghua Advanced Technology, DROP-IN). saving 0.068 USD x 2 per module vs the 0.08 USD estimate; EMC behaviour of the bead not re-checked (optional EMC part)
  - checked: 0603 both, 2 pins (non-polar); 300 R +/-25 % at 100 MHz = ; rated current 1.2 A vs 1.5 A (load: magnetics 22 mA + DP83822 AVD, block note 'ferrite drop < 4 mV') ; RDC 0.20 vs 0.15 R (drop < 15 mV at 70 mA); -55..+125 C incl. self-heating = ; Zmax/curve not compared
  - datasheet: docs/datasheets/magnetics/Fenghua-CBW.pdf (1-3, (row CBW160808U301T), operating-temperature table)
- **24LC256T-I/SN -> BL24C256A-PARC** (Shanghai Belling, DROP-IN). No circuit change; firmware page size and write time (3-5 ms) compatible - confirm the driver's write-poll timeout.
  - checked: pinout identical (A0-A2, VSS, SDA, SCL, WP, VCC) [DS p1-2]; 1 MHz (400 kHz); 1.7-5.5 V (2.5-5.5); 1 M cycles, 100 y retention (1 M, 200 y); -40..85 C (same); page 64 B (24LC256 64 B)
  - datasheet: docs/datasheets/controllers/Belling-BL24C256A.pdf (p1-2 (features, pins))
- **BAT54S-7-F -> BAT54S,215** (Nexperia, DROP-IN). Consolidation onto Nexperia, parts already in the BOM (BAT54, BZX84, 2N7002BK). Price neutral (0.0389 vs 0.0412 at 100); LCSC stock 6296 vs 73933 for the Diodes part (enough for 52 per module pair, check for volume). Do NOT use the cheaper bare 'BAT54S' C408389 (0.0137): it is not Nexperia (3-page generic sheet, maker not stated). Symbol/net map unchanged (catalog key BAT54S, ds BAT54S.pdf already registered); gdrv/ctrl BAT54_VF = (0.10, 0.32) V stays valid because it was read from the Nexperia sheet. Re-point the generators' ds= from BAT54S-Diodes.pdf to BAT54S.pdf.
  - checked: Same SOT-23 pin table (1 A1, 2 K2, 3 K1;A2) as the repo symbol. VR 30=30 V; IF 200=200 mA; IFRM 300 mA; IFSM 600 mA (tp<10 ms, Nexperia) vs 600 mA (t<1 s, Diodes); VF <=800 mV at 100 mA both, <=240 mV at 0.1 mA and <=320 mV at 1 mA (the BAT54_VF value gdrv.py already takes from the Nexperia sheet); IR <=2 uA at 25 V both; Cd <=10 pF at 1 V both; trr <=5 ns both; Ptot 250 mW (Diodes PD 200 mW); Tamb -55..150 (Diodes TJ -65..150); Nexperia part has no AEC statement in this generic sheet (Diodes: JEDEC-referenced qualification)
  - datasheet: docs/datasheets/protection/BAT54S.pdf (1-4 (already on file))

### SYS-IO-AUX
- **1803293 -> KF2EDGR-3.81-4P** (Cixi Kefa Electronic (KEFA), DROP-IN). Order the KEFA plug KF2EDGK-3.81-4P (C440860, 0.2093 at 100) with the header; the harness plug is not a BOM line, so the harness drawing must name it. Incumbent is itself on LCSC (C480536, stock 1940, 0.7208 at 100) so supply is not the issue; saving 0.64 USD x 15 = 9.6 USD per PV-P75, 10.2 per DAB-D60. phoenixcontact.com refuses scripted downloads (HTTP 403); the Phoenix sheet now on file is the LCSC-hosted copy of the 2010 catalog extract, which has no operating temperature and no contact numbering, so the temperature-audit gap stays for the incumbent; the alternate has a stated range. Second sources: Kangnex WJ15EDGRC-3.81-04P-14-00A (C7245, 79662 in stock, 0.0583; UL 300 V 8 A, IEC 160 V 7 A, -40..+105 C), Degson 15EDGRC-3.81-04P-14-00A(H) (UL 300 V 8 A, IEC 250 V 7 A, -40..105 C 'depending on derating curve'). To request from KEFA: UL file number and VDE certificate. No pin numbers are printed on any of the sheets; pin 1 is a footprint convention.
  - checked: pitch 3.81 / 1 row / right angle / 0.8 mm square pins equal; UL 300 V 8 A = 300 V 8 A; IEC 160 V (III/2) = 160 V (III/2), impulse 2.5 kV = 2.5 kV; IEC current 7 A vs 8 A (our worst case is 3.6 A, the fan-channel eFuse limit); tin plating both; PA66 UL94 V0 vs PBT UL94 V0; operating range -40..+105 C stated vs none stated in the Phoenix extract; solder pin 3.7 vs 3.4 mm, hole 1.4 vs 1.2 mm, body P*3.81+0.80 vs P*3.81+1.39 mm and edge offset differ by about 0.25-0.6 mm (re-check the footprint at layout); pin map: 1..4 in a row, same as the symbol (J_MC4 pins 1-4); mating plug KF2EDGK-3.81-4P (C440860) is UL 300 V 8 A, AWG 28-16, M2 screw 0.2 N.m
  - datasheet: docs/datasheets/connectors/KEFA-KF2EDGR-3.81.pdf (header); docs/datasheets/connectors/KEFA-KF2EDGK-3.81.pdf (mating plug) (header p1 (drawing and data table); plug p1)
- **1803358 -> KF2EDGR-3.81-10P** (Cixi Kefa Electronic (KEFA), DROP-IN). Incumbent price 2.30 is the costed-BOM ESTIMATE (scaled by pole count); the LCSC prices of the 4-pos/6-pos Phoenix parts imply about 1.33 USD at 100 for 10 poles (extrapolated, not a quote), so the saving is 1.15-2.1 USD x 3 per module. Phoenix 1803358 itself is not on the LCSC mirror. The datasheet on file is the 4-pos/6-pos catalog extract of the same series (series-level data).
  - checked: pitch 3.81 / 1 row / right angle / 0.8 mm square pins equal; UL 300 V 8 A = 300 V 8 A; IEC 160 V (III/2) = 160 V (III/2), impulse 2.5 kV = 2.5 kV; IEC current 7 A vs 8 A (our worst case is 3.6 A, the fan-channel eFuse limit); tin plating both; PA66 UL94 V0 vs PBT UL94 V0; operating range -40..+105 C stated vs none stated in the Phoenix extract; solder pin 3.7 vs 3.4 mm, hole 1.4 vs 1.2 mm, body P*3.81+0.80 vs P*3.81+1.39 mm and edge offset differ by about 0.25-0.6 mm (re-check the footprint at layout); 10 poles in a row, pins 1..10 as the symbol (NTC: odd = NTC, even = 0 V; DI: pin n = DIn); signal currents (NTC bias about 0.25 mA, DI 2-3 mA); mating plug KF2EDGK-3.81-10P (C440855)
  - datasheet: docs/datasheets/connectors/KEFA-KF2EDGR-3.81.pdf (header); docs/datasheets/connectors/KEFA-KF2EDGK-3.81.pdf (mating plug) (header p1; plug p1)
- **SSW-105-01-G-D -> X6521FV-2x05-C85D32** (XKB Connection, DROP-IN). Replace together with TSW-105-07-G-D on BMU-GW (same maker pair). Saving about 1.8 USD per module for the pair at the work-file prices.
  - checked: pin numbering drawn (1,3,5.. / 2,4,6..); 3 A, 250 V, -40..+105 C, 1 u-inch gold, PA6T UL94 V0 vs Samtec 4.7 A, -55..+125 C; signal-level use only; female 8.5 mm tall, pin 3.2 mm; the 5 V pin carries the BMS-side supply (a fraction of an ampere)
  - datasheet: docs/datasheets/connectors/XKB-X6521F.pdf; mate docs/datasheets/connectors/XKB-X6521W.pdf (p1)
- **TSW-102-07-G-S -> X6511WV-02H-C60D30** (XKB Connection, DROP-IN). 0.2 USD saving per piece x 5 pieces per PV-P75 is small; the point is one maker and an LCSC-stocked part (5511 in stock).
  - checked: single row 1x2, pins 1-2; 3 A, 250 V, -40..+105 C, 1 u-inch gold, PA6T UL94 V0 vs Samtec 4.7 A (gold) / -55..+105 C as recorded; jumper/shunt use only, no current
  - datasheet: docs/datasheets/connectors/XKB-X6511W.pdf (p1)
- **BAT54C-7-F -> BAT54C,215** (Nexperia, DROP-IN). Price 0.0217 vs 0.1136 USD (the Diodes -7-F part is 5x dearer on LCSC): saves 0.19 USD per module. No circuit change; point the BAT54C catalog entry to Nexperia BAT54C,215 and the new datasheet.
  - checked: Same SOT-23 pin table (1 A1, 2 A2, 3 K1,K2 common cathode) as the repo symbol (sys_io_aux.py cites Diodes DS11005 diagram: same). VR 30 V; IF 200 mA; IFRM 300 mA; IFSM 600 mA; VF <=800 mV at 100 mA; IR <=2 uA at 25 V; Cd <=10 pF; trr <=5 ns; Ptot 250 mW; Tj 150 C: all equal to the Diodes sheet
  - datasheet: docs/datasheets/protection/Nexperia-BAT54C.pdf (1-4)
- **FM24CL64B-GTR -> MB85RC64TAPNF-G-BDERE1** (Fujitsu Semiconductor Memory Solution (now RAMXEED), DROP-IN). No circuit change. Check that the event-log retention target is met by 10 y at 85 C board temperature (compare the FM24CL64B figure).
  - checked: pinout identical (1-3 A0-A2, 4 VSS, 5 SDA, 6 SCL, 7 WP, 8 VDD; SOP-8 = SOIC-8 footprint) [DS p2]; VDD 1.8-3.6 V (2.7-3.65); 3.4 MHz HS mode (1 MHz); endurance 1e13 (1e14); retention 10 y at +85 C, 95 y at +55 C (Fujitsu); -40..+85 C (same); no-polling write like F-RAM
  - datasheet: docs/datasheets/controllers/Fujitsu-MB85RC64TA.pdf (DS501-00044-2v0-E p1 (features), p2 (pins))
- **BAT54S-7-F -> BAT54S,215** (Nexperia, DROP-IN). Consolidation onto Nexperia, parts already in the BOM (BAT54, BZX84, 2N7002BK). Price neutral (0.0389 vs 0.0412 at 100); LCSC stock 6296 vs 73933 for the Diodes part (enough for 52 per module pair, check for volume). Do NOT use the cheaper bare 'BAT54S' C408389 (0.0137): it is not Nexperia (3-page generic sheet, maker not stated). Symbol/net map unchanged (catalog key BAT54S, ds BAT54S.pdf already registered); gdrv/ctrl BAT54_VF = (0.10, 0.32) V stays valid because it was read from the Nexperia sheet. Re-point the generators' ds= from BAT54S-Diodes.pdf to BAT54S.pdf.
  - checked: Same SOT-23 pin table (1 A1, 2 K2, 3 K1;A2) as the repo symbol. VR 30=30 V; IF 200=200 mA; IFRM 300 mA; IFSM 600 mA (tp<10 ms, Nexperia) vs 600 mA (t<1 s, Diodes); VF <=800 mV at 100 mA both, <=240 mV at 0.1 mA and <=320 mV at 1 mA (the BAT54_VF value gdrv.py already takes from the Nexperia sheet); IR <=2 uA at 25 V both; Cd <=10 pF at 1 V both; trr <=5 ns both; Ptot 250 mW (Diodes PD 200 mW); Tamb -55..150 (Diodes TJ -65..150); Nexperia part has no AEC statement in this generic sheet (Diodes: JEDEC-referenced qualification)
  - datasheet: docs/datasheets/protection/BAT54S.pdf (1-4 (already on file))

### BMU-GW
- **TSW-105-07-G-D -> X6521WV-2x05H-C60D30** (XKB Connection, DROP-IN). Change with SSW-105-01-G-D as a pair. Temp-audit note: the Samtec TSW is on file as -55..+105 C; XKB states -40..+105 C.
  - checked: pin numbering drawn (1,3,5.. / 2,4,6..); 3 A, 250 V, -40..+105 C, 1 u-inch gold, PA6T UL94 V0 vs Samtec 4.7 A, -55..+125 C; signal-level use only; post 6.0 vs 5.84 mm, tail 3.0 vs 3.05 mm
  - datasheet: docs/datasheets/connectors/XKB-X6521W.pdf; mate docs/datasheets/connectors/XKB-X6521F.pdf (p1)
- **1803316 -> KF2EDGR-3.81-6P** (Cixi Kefa Electronic (KEFA), DROP-IN). Incumbent is on LCSC (C480580, 1956 in stock, 0.9225 at 100) - the costed BOM carries 1.40 USD as an estimate, the real LCSC price is lower. Saving 0.82 USD per module. Field-bus connector: the BMS side is outside PELV; check the BMU-GW creepage at layout (drawing gives no creepage; IEC 160 V III/2 equals the incumbent's rating).
  - checked: pitch 3.81 / 1 row / right angle / 0.8 mm square pins equal; UL 300 V 8 A = 300 V 8 A; IEC 160 V (III/2) = 160 V (III/2), impulse 2.5 kV = 2.5 kV; IEC current 7 A vs 8 A (our worst case is 3.6 A, the fan-channel eFuse limit); tin plating both; PA66 UL94 V0 vs PBT UL94 V0; operating range -40..+105 C stated vs none stated in the Phoenix extract; solder pin 3.7 vs 3.4 mm, hole 1.4 vs 1.2 mm, body P*3.81+0.80 vs P*3.81+1.39 mm and edge offset differ by about 0.25-0.6 mm (re-check the footprint at layout); 6 poles in a row, pins 1..6 as the symbol; signals only (CAN, RS-485, 5 V), currents far below 1 A; mating plug KF2EDGK-3.81-6P (C440862)
  - datasheet: docs/datasheets/connectors/KEFA-KF2EDGR-3.81.pdf (header); docs/datasheets/connectors/KEFA-KF2EDGK-3.81.pdf (mating plug) (header p1 (same drawing covers 2-24 poles); plug p1)

## 5b. HV divider resistors, shunts, passives, connectors and discretes (summary; details in the CSV notes)

- **HV divider strings (TNPV, coordinator priority 4):** LCSC stocks no Asian high-voltage thin-film resistor >= 300 V at
  our values. Viking Tech ARHV06 / ARHV13 (Taiwan; AEC-Q200, 700 V / 1000 V, 2 x Umax pulse, 0.1 %, 25 ppm/K, VCR
  1.5 ppm/V) match the TNPV1206 / TNPV1210 on paper -> DROP-IN, OPTION until a price is quoted. Yageo VT (613 V, no pulse
  or VCR rating) and Susumu RGV (no pulse / VCR rating) rejected. The 2 M discharge-gate bias string needs no precision:
  PDC FVF06FT-2004 (Taiwan, 800 V RCWV, AEC-Q200 suffix) ADOPT, -9.85 USD per module. The 48 TNPV1210 bleeders on the
  three PVCELL-25 boards need no thin film either: a 1206 HV thick film would save ~37 USD per PV-P75 (no 124 k on LCSC;
  value change 8 x 130 k gives t60 292 s vs 300 s) - left to the PV engineer. The 4.99 k bottom resistor (Yageo RT) stays
  OPTION: +/-0.5 % 1000 h drift vs 0.05 % and the divider budget has no drift term.
- **Port shunt:** Bourns CSM2F-8518 -> RESI (Changsha Resi / C&B Electronics) ARCS8518DL100A9, FUNCTIONAL ADOPT (-13.2 USD per
  module): same 85 x 18 x 3 mm and 60 mm hole pitch, 0.5 % (5 %), TCR 100 ppm/K above 20 C / 150 below (125), EMF
  < 0.5 uV/K; bolt holes 7.0 mm (M6, was M8) and different sense-pin positions; no explicit AEC-Q200 sentence in the maker
  sheet (endurance table given). Re-run sim/port_design.py hold band with the RESI constants.
- **Y1 capacitor VY1472M (HV-PE):** KEEP - no Asian Y1 disc found that states a DC rating; the insulation audit relies on
  the Vishay 1500 V DC figure.
- **Connectors (coordinator priority 5):** Phoenix MC 1.5 headers -> KEFA KF2EDGR-3.81-xP (+ KF2EDGK plugs), DROP-IN ADOPT
  (UL 300 V 8 A, IEC 160 V 7 A, -40..+105 C, numbered drawing; footprint re-check at layout: body/hole offsets differ);
  the Phoenix sheets on file (LCSC copies) give no temperature range - the KEFA drawing closes that temperature-audit gap.
  Phoenix PC 5 7.62 mm -> Dinkle ECH762R, OPTION (footprint equivalence not proven). Wurth WR-BHD box headers -> XKB
  X9555WV (3 A, -40..+125 C), ADOPT. Samtec 2.54 mm headers/sockets -> XKB X6521 / X6511 (gold, numbered drawings), ADOPT
  for signal headers; the 2 x 40 power pair (4.7 A/pin used) is OPTION - needs a third +24V_GD pin. FTSH cTI-20 -> XKB
  X1270WVS, OPTION (footprint differs, low stock).
- **Discretes:** Nexperia lines are already Asian (LCSC checks in the CSV). Diodes Inc BAT54S/BAT54C/MMBT3904/3906 ->
  Nexperia equivalents; TVS SMAJ/SMCJ -> Taiwan Semiconductor / Brightking (OPTION); US1M DESAT diodes -> TSC US1MH
  (OPTION, capacitance and leakage at 125 C compared); ZXTP25040 Miller-hold PNP -> Nexperia PBSS5540X (FUNCTIONAL, OPTION:
  no SOT-23 Asian PNP reaches the 9 A pulse); CSD18532Q5B and ESD2CAN24 KEEP (no Asian part with the needed VGS(th) /
  capacitance and qualification).
- **Other passives:** Rubycon ZLH on LCSC at 0.46 / 0.10 USD (cheaper than the costed BOM). Wurth inductors -> SZ Cjiang
  FXL (OPTION: body sizes and Isat definition differ, re-check each regulator); ferrite bead -> Fenghua CBW160808U301T ADOPT;
  Abracon MEMS oscillator -> Epson SG-210STF (OPTION; the -40..105 C grade is not on LCSC). BOM data error found:
  gen/port.py describes 74439346100 as "SMD 4040" but it is the 6060 size (PV-PORT, PVCELL-25, DAB60 BOMs).

## 6. Kept on purpose

- **Safety chain:** TPS3700 / TPS3703 / TPS3710 / TPS3850 (rail windows, resets, the ECO-04 hardware watchdog),
  ISO1212 / ISO1211 (E-stop A/B, door, smoke, contactor feedback, spare trip loop), TPS272C45 (contactor coils and DOs),
  REF3030E / REF3020E / LM4040 (hold-logic, OC and OV thresholds), the fault-latch logic (LVC1G74 / LVC08 / LVC11:
  Nexperia listed only as an approved second source, one source per build), TPS16630 / TPS26600 / LM74800 (power-path
  protection). No Asian part with equal documentation was found; the saving would be a few USD per module.
- **Timing-critical signal chain:** TLV3502 (4.5 ns; no Asian comparator <= 5 ns with RRIN and push-pull found - buy the
  TI part through LCSC instead: TLV3502AIDR 1.10 USD and TLV3502AIDCNR 1.45 USD @1k vs 2.71 / 2.05 TI list), AM26LV31E /
  AM26LV32E (gate-command links, skew budget), THVD1450, OPA4388 / OPA2388 (no 10 MHz zero-crossover zero-drift Asian
  op amp; the DAB OV timing uses the OPA2388 overload recovery), OPA4322 / OPA2322 (re-chosen if the LA 150-P changes).
- **Barrier parts that pass and whose Asian twin is weaker:** ISO7720F (CA-IS3820LW VIOSM 8 kV < 12.8 kV), TPSI3050.
- **Low value / no verified twin:** ADS7953, DP83822 (Motorcomm PHY needs footprint + firmware, datasheet under NDA),
  LMR36015 / LMR38020 / TPS6213x / TPS7A20 / TPS7A26, MCP23S17, TMP1075 (the NOVOSENSE NST175 costs more), TMUX1208,
  LM2903B, LM74700, REF5025 (note: the A grade REF5025AIDGKR is 1.09 USD on LCSC if 8 ppm/C fits the budget).

## 7. Not processed here (owned by other engineers since the coordinator's message of 2026-10-04)

- PV engineer: Sanyo Denki 9GT1224P1S001 fan, KEMET C4AQUEW5450A3BJ / C4AQUBU4220A1YJ film banks, VS-60EPS16 clamp
  diodes, CRCW2512-HP leg damper. Findings the passives helper had already collected are kept in the alternates notes.
- Port engineer: port fuses, contactors, SPDs, Omron G7L precharge relay, Miba RST 200, Honeywell thermostats, X capacitor
  RFQ, TDK varistor (coil clamp), IRFL214 / IRFR9214 / S2M / SMBJ33A coil parts, IMD switches (see CA-IS3417WT note).
- DAB engineer: C4AQQEW5650A3BJ / C4AQUEW5450A3BJ banks, HF snubber MLCCs and 13.6 R, DD600N16K clamp, SIKA flow switch,
  Honeywell 3106U.
- AUX-HV engineer: CNY65B, UCC28C59-Q1, TLV3202, ATL431, BSS126, BYG10Y, US1G, VS-8ETU04S, SMCJ100A, CRHV2512 10 M,
  AC10 22 R, polymer 875115655003, C4AQUBU4330A11J and the AUX-HV-only Nexperia small signal parts.
- Also outside this brief: SiC MOSFETs (incl. C2M1000170D - not in the device study's class list, flagged), gate driver
  and driver bias, Schurter 0090.1001 1000 V gPV tap fuse (port-fuse family).

## 8. What I could not verify / to download by hand

- NOVOSENSE current datasheets (NSI1311 / NSI1300 / NSI1042 newer revisions): novosns.com only serves files through its
  web app; the LCSC-hosted NSI1311 is Rev 1.2 (2021) with VIOSM 6250 V.
- 2Pai (rpsemi.com) datasheets: behind a registration form (isolated amplifiers are "basic" per its own web table).
- Nexperia datasheets: assets.nexperia.com answers 403 to a plain request; LCSC-hosted copies were filed instead.
- Chipanalog: certificate scope of VDE 40057278 (the same number is printed for CA-IS1311, CA-IS3115AW and CA-IS382x -
  confirm on the VDE certificate which part numbers it lists); CA-IS1300G05G's newer datasheet / VDE grant; light-load
  efficiency of CA-IS3115AW (curve only, p11); EMI of the on-chip-transformer DC/DC (no CISPR statement).
- Prices: Sinomags STB-150LA/ZN and STK-HO/A 130, CHIPSENSE CS1V 150 P00 (not on the LCSC mirror; RFQ). LCSC stock of
  CA-IS1311BG (13) and CA-IS3115AW (2) is too low for production - second channel needed.
- Sinomags / CHIPSENSE: maximum (not typical) response times, insulation class statement, dv/dt immunity.

## 9. Where I stopped

- Done: every line of the coordinator's priorities 1-3 (isolated amplifiers, the other barrier parts, both current
  transducers), priority 4 (HV dividers, shunt) and 5 (connectors, terminal blocks), and every IC of CTRL-C2000 /
  SYS-IO-AUX at least briefly; discretes and the remaining passives as listed in the CSV. Lines owned by other engineers
  are in section 7 and not in the CSV.
- Not done: RFQs (Sinomags, CHIPSENSE, Viking, Chipanalog volume price and second channel); the light-load efficiency
  of CA-IS3115AW; NOVOSENSE / 2Pai documents that need a manual download; any re-run of the design scripts (the board
  designers do that when they apply the changes - this study edits no generator, simulation or board file).
- Prices: every LCSC price used here is appended to gen/data/prices.csv (2026-10-04, mirror of the LCSC catalogue);
  datasheets are filed under docs/datasheets/ and registered in both manifests.
