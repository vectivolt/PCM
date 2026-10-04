"""SYS-IO-AUX - common system-I/O and auxiliary board of the 25-110 kW DC/DC family (requirements ECO-03, ECO-04;
24 V side of ECO-06; precharge / discharge drive of ECO-07). The low-voltage "motherboard": CTRL-C2000 plugs into
J301 (contract interfaces.SYS), the BMU-GW daughtercard into J302 (interfaces.GW), AUX-HV into J103 (interfaces.AUX).

  01 24 V entry      2 cabinet feeds (ORing) + AUX-HV (LM74800 switch, cabinet priority, OV, CdVdT ramp); 2 mF surge
                     reservoir, precision OV cut-off, PG window
  02 rails           +24V to CTRL (2 A eFuse), +24V_GD gate-drive bias (5 A eFuse, channel B), 5 V buck (EN UVLO),
                     3.3 V LDO
  04/05 field comms  CANB, MCAN (ISO1042) and SCIA, SCIB RS-485 (ISO1410), each with its own SN6505B isolated 5 V
  06 Ethernet        RJ45 with magnetics; centre taps on ETH_CT from the PHY
  07 DI              8 x IEC 61131-2 type 1/3 isolated inputs (ISO1212); DI1 = ESTOP_A, DI8 = ESTOP_B (other package);
                     FB_A/B_MAIN on J702 with GND return (INT-02)
  08 safety chain    dual channel: A (E-stop A, 24 V UV, spare loop, watchdog) -> latch A -> GATE_EN + 24V_DO switch A;
                     B (E-stop B, watchdog) -> latch B -> CHB_OK -> +24V_GD + 24V_DO switch B. Separate parts per channel.
                     Service jumper J802 disables the watchdog and holds both self-test inputs (chain not healthy).
                     AUX-HV source inhibit: while the LM74800 is enabled both channels trip (latched) and fans stop.
  09 DO              8 smart high-side outputs behind two series supply switches (one per channel), TVS coil clamps
  10 fans            4 x eFuse-switched 24 V fan supply (off by default and while AUX-HV is the source), PWM, tach
  11 NTC             12 NTC channels + 3 feed monitors + gate-drive bias current on an ADS7953 16-ch SAR ADC
  12 service         F-RAM, board temperature, two SPI expanders (fault readback, rail sense, channel self-test)

Every catalog pin table below was read from the PDF named in `ds` (document + figure/table in the comment).
check_chain() simulates the drawn chain gate by gate and runs the single-fault analysis on every build.
Nothing here is bench-validated.  Usage: .venv/bin/python gen/sys_io_aux.py
"""
import csv
import json
import math
import os
import sys
from itertools import product

import catalog
import dcdclib as L
import interfaces as IF

PROJECT, REV, DATE = "SYS-IO-AUX", "E0", "2026-10-04"
DS = "docs/datasheets/"
TI = "Texas Instruments"
# Phoenix Contact answers scripted downloads with HTTP 403 (same as the BMU-GW precedent): item number and rating
# from the maker listing / distributor records, not from a PDF on file -> listed as unverified in the report.
PX = "https://www.phoenixcontact.com/en-us/products/"
# Samtec SSW catalog p.1: "Mates: TSW"; 4.7 A per pin (SSW/TSM, 2 pins powered); insertion depth 3.68-6.35 mm.
# Samtec TSW catalog p.1: "Board Mates: SSW"; lead style -07 post C = 5.84 mm (inside 3.68-6.35 mm).
SSW = DS + "connectors/Samtec-SSW.pdf"


def ic(mfr, mpn, pkg, ds, desc, left, right, prefix="U"):
    return dict(mfr=mfr, mpn=mpn, prefix=prefix, pkg=pkg, ds=DS + ds, desc=desc, pins={"left": left, "right": right})


def conn(mpn, n, url, pkg, desc, mfr="Phoenix Contact"):
    stock = ("Connector_Generic", "Conn_02x%02d_Odd_Even" % (n // 2)) if mfr == "Samtec" else \
        ("Connector_Generic", "Conn_01x%02d" % n)
    return dict(mfr=mfr, mpn=mpn, prefix="J", pkg=pkg, ds=url, stock=stock, desc=desc)


CATALOG = dict(catalog.PARTS, **{
    # ---------------------------------------------------------------- 24 V entry
    # Bourns SF-2923HC-C electrical table: SF-2923HC30C-2 = 30 A, 60 VDC, 300 A @ 60 VDC interrupting, 1.2 mOhm
    "SF2923HC30": dict(mfr="Bourns", mpn="SF-2923HC30C-2", prefix="F", pkg="2923 SMD", stock=("Device", "Fuse"),
                       ds=DS + "protection/SF-2923HC.pdf", desc="Fuse 30 A 60 VDC, 300 A interrupting, single blow"),
    # Bourns SMCJ series table: SMCJ33CA VRWM 33 V, VBR 36.7-40.6 V, VC 53.3 V @ 28.1 A, 1500 W (10/1000 us)
    "SMCJ33CA": dict(mfr="Bourns", mpn="SMCJ33CA", prefix="D", pkg="DO-214AB (SMC)", stock=("Device", "D_TVS"),
                     ds=DS + "protection/SMCJ_Bourns.pdf", desc="TVS bidirectional 1500 W, VRWM 33 V, VC 53.3 V"),
    # Bourns SMAJ series table (400 W @ 1 ms, 1 W steady at TL 75 C): SMAJ12A VBR 13.3-14.7 V; SMAJ33A 36.7-40.6 V;
    # SMAJ36A 40-44.2 V. p.4 pulse rating curve ~150 W at 10 ms; pulse derating 100 % at 25 C -> 0 at 150 C.
    "SMAJ12A": dict(mfr="Bourns", mpn="SMAJ12A", prefix="D", pkg="DO-214AC (SMA)", stock=("Device", "D_Zener"),
                    ds=DS + "protection/SMAJ_Bourns.pdf", desc="TVS unidirectional 400 W, VRWM 12 V, VBR 13.3-14.7 V"),
    "SMAJ33A": dict(mfr="Bourns", mpn="SMAJ33A", prefix="D", pkg="DO-214AC (SMA)", stock=("Device", "D_Zener"),
                    ds=DS + "protection/SMAJ_Bourns.pdf", desc="TVS unidirectional 400 W, VRWM 33 V, VBR 36.7-40.6 V"),
    "SMAJ36A": dict(mfr="Bourns", mpn="SMAJ36A", prefix="D", pkg="DO-214AC (SMA)", stock=("Device", "D_Zener"),
                    ds=DS + "protection/SMAJ_Bourns.pdf", desc="TVS unidirectional 400 W, VRWM 36 V, VBR 40-44.2 V"),
    # SNOSD17G fig 5-1 / table 5-1 (DBV-6); VCAP >= 0.1 uF and >= 10 x Ciss; V(AK REG) 13-29 mV,
    # full conduction 34-57 mV, reverse turn-off V(AK REV) -17..-2 mV (table 7.5)
    "LM74700": ic(TI, "LM74700QDBVRQ1", "SOT-23-6 (DBV)", "power-supply/LM74700-Q1.pdf",
                  "Ideal-diode controller 3.2-65 V, reverse blocking / ORing",
                  ["6 ANODE pi", "3 EN i", None, "2 GND pi"], ["4 CATHODE i", "5 GATE o", "1 VCAP p"]),
    # CSD18532Q5B datasheet p.1 top view: S = 1-3, G = 4, D = 5-8; 60 V, 2.5 mOhm (VGS 10 V), Ciss 3.9 nF
    "CSD18532Q5B": ic(TI, "CSD18532Q5B", "SON 5x6 (DQJ)", "power-semiconductors/CSD18532Q5B.pdf",
                      "N-MOSFET 60 V 2.5 mOhm, ideal-diode ORing switch",
                      ["4 G p"], ["5 D p", "6 D p", "7 D p", "8 D p", None, "1 S p", "2 S p", "3 S p"], prefix="Q"),
    # SLVSET9G fig 5-2 / table 5-1 (TPS16630 PWP HTSSOP-20); I(ILIM) = 18 A*kOhm / R (+/-7 %, R >= 3k);
    # UVLO / OVP 1.176-1.224 V rising, OVP turn-off 11 us; SHDN, OVP, ILIM, dVdT abs max 5.5 V; IMON 27.9 uA/A;
    # dVdT 2 uA x 25 V/V; RON 30 mOhm typ, 45 mOhm max at 85 C; MODE = GND auto-retry
    "TPS16630": ic(TI, "TPS16630PWPR", "HTSSOP-20 (PWP)", "power-supply/TPS1663.pdf",
                   "eFuse 60 V 6 A 31 mOhm, adjustable ILIM, UVLO, OVP, IMON, FLT",
                   ["1 IN pi", "2 IN pi", "3 IN pi", "6 P_IN pi", None, "7 UVLO i", "8 OVP i", "13 ~{SHDN} i",
                    "12 MODE i", None, "9 GND pi", "21 EP p"],
                   ["18 OUT po", "19 OUT p", "20 OUT p", None, "15 ~{FLT} oc", "16 PGOOD oc", "14 IMON o",
                    "11 ILIM p", "10 dVdT p", None, "4 NC nc", "5 NC nc", "17 NC nc"]),
    # SBVS271A fig 4-1 (DDC SOT-6): VIT+ 396-404 mV, VIT- 387-400 mV, tpd(LH) 29 us, open-drain OUT high when
    # SENSE > VIT+ (used as UV-OK detector, and as an active-high OV detector)
    "TPS3710": ic(TI, "TPS3710DDCR", "SOT-6 (DDC)", "power-supply/TPS3710.pdf", "Voltage detector 400 mV, open drain",
                  ["5 VDD pi", "3 SENSE i", None, "2 GND pi", "4 GND pi", "6 GND pi"], ["1 OUT oc"]),
    # SBVS187G pin functions (DDC SOT-6): OUTA low when INA+ < VIT- (UV); OUTB low when INB- > VIT+ (OV); same
    # 400 mV reference / tolerances as TPS3710. Only the UV comparator (INA+) is used here.
    "TPS3700": ic(TI, "TPS3700DDCR", "SOT-6 (DDC)", "power-supply/TPS3700.pdf",
                  "Window comparator 400 mV, two open-drain outputs", ["5 VDD pi", "3 INA+ i", "4 INB- i", "2 GND pi"],
                  ["1 OUTA oc", "6 OUTB oc"]),
    # SBOS410O fig 5-2 (REF50xxEI SOIC-8): 1 EN, 2 VIN, 3 TEMP, 4 GND, 5 NR, 6 VOUT, 7 NC, 8 DNC; REF5025E initial
    # +/-0.025 %, 2.5 ppm/C max (-40..125 C box); CL 1-100 uF; characterised with NR open and EN = VIN
    "REF5025E": ic(TI, "REF5025EID", "SOIC-8 (D)", "sensing/REF5025E.pdf", "Precision reference 2.5 V 0.025 % 2.5 ppm/C",
                   ["2 VIN pi", "1 EN i", "3 TEMP o", "5 NR p", "4 GND pi"], ["6 VOUT po", None, "7 NC nc", "8 DNC nc"]),
    # SLCS005AH fig 4-1 (D SOIC-8) and table 5.6: VIO +/-4 mV (-40..125 C), IB <= 50 nA, VCM 0..V+ - 2 V,
    # open-collector outputs, inputs -0.3..38 V independent of supply, response ~1 us
    "LM2903B": ic(TI, "LM2903BIDR", "SOIC-8 (D)", "sensing/LM2903B.pdf", "Dual comparator, +/-4 mV, open collector",
                  ["3 1IN+ i", "2 1IN- i", None, "5 2IN+ i", "6 2IN- i", None, "4 GND pi"],
                  ["8 VCC pi", "1 1OUT oc", None, None, "7 2OUT oc"]),
    # Rubycon ZLH catalog: 50 V 1000 uF 16x25: Z 0.021 ohm (20 C, 100 kHz), 0.056 ohm (-10 C), Z(-40)/Z(20) <= 3,
    # +/-20 %, -40..105 C; part number = voltage + ZLH + capacitance + M + EFC + case
    "CB1000": dict(mfr="Rubycon", mpn="50ZLH1000MEFC16X25", prefix="C", pkg="radial 16x25 mm",
                   stock=("Device", "C_Polarized"), ds=DS + "passives-capacitors/Rubycon-ZLH.pdf",
                   desc="Aluminium electrolytic 1000 uF 50 V 20 %, low impedance 21 mOhm @ 100 kHz"),
    # ---------------------------------------------------------------- local rails
    # SNVSB49D fig 7-1 (RNX VQFN-HR 12); table 10-3: 5 V / 400 kHz -> RFBT 100k, RFBB 24.9k, 2 x 22 uF, 10 uH
    "LMR36015": ic(TI, "LMR36015ARNXR", "VQFN-HR-12 (RNX)", "power-supply/LMR36015.pdf",
                   "Synchronous buck 4.2-60 V in, 1.5 A, 400 kHz",
                   ["2 VIN pi", "10 VIN pi", "9 EN i", None, "6 AGND pi", "1 PGND pi", "11 PGND pi"],
                   ["12 SW p", "3 NC p", "4 BOOT p", "5 VCC p", "7 FB i", "8 PG oc"]),
    # WE-XHMI 74439346100 datasheet: 10 uH +/-20 %, Isat(10 %) 5.05 A, IR 5 A, RDC 26.5 mOhm typ
    "L10U": dict(mfr="Wurth Elektronik", mpn="74439346100", prefix="L", pkg="WE-XHMI 6060", stock=("Device", "L"),
                 ds=DS + "magnetics/WE-74439346100.pdf", desc="Power inductor 10 uH 20 %, Isat 5.05 A, 26.5 mOhm"),
    # SBVS338H fig 4-4 (DBV SOT-23-5): 1 IN, 2 GND, 3 EN (500k internal pull-down), 4 N/C, 5 OUT; table 5.5:
    # VOUT +/-1.5 % over VIN (VOUT+0.3)-6.0 V, 1-300 mA, TJ -40..125 C (DBV, VOUT >= 2.8 V); COUT 0.47-200 uF effective
    "TPS7A2033": ic(TI, "TPS7A2033PDBVR", "SOT-23-5 (DBV)", "power-supply/TPS7A20.pdf",
                    "LDO 3.3 V 300 mA, +/-1.5 % over line, load, temperature", ["1 IN pi", "3 EN i", "2 GND pi"],
                    ["5 OUT po", "4 NC nc"]),
    # TPS7A26 data sheet DRV (fixed) pinout: 1 OUT, 2 NC, 3 PG, 4 EN, 5 GND, 6 IN, pad to GND; 2.4-18 V in, fixed
    # outputs +/-1 % over temperature, VDO <= 280 mV at 250 mA; CIN >= 1 uF, COUT 1-100 uF (2.2 uF nominal)
    "TPS7A2650": ic(TI, "TPS7A2650DRVR", "WSON-6 (DRV)", "power-supply/TPS7A26.pdf",
                    "LDO 5.0 V 500 mA +/-1 %, 18 V in (isolated port supply)", ["6 IN pi", "4 EN i", "5 GND pi",
                                                                                "7 EP p"],
                    ["1 OUT po", "3 PG oc", "2 NC p"]),
    # Wurth 760390014 p.1: schematic 1-2-3 primary (N1+N2, CT = 2), 6-5-4 secondary (N3+N4, CT = 5), n = 1:1.3,
    # 11 V-us, 2500 VAC test, AEC-Q200; SN6505B table 9-3 row "5 V -> 5 V, 100 mA, SN6505B, LDO"
    "WE760390014": dict(mfr="Wurth Elektronik", mpn="760390014", prefix="T", pkg="SMD 6-pin 10.2x7.1 mm",
                        ds=DS + "magnetics/WE-760390014.pdf",
                        desc="Push-pull transformer 1:1.3 CT:CT, 475 uH, 11 Vus, 2500 VAC test, AEC-Q200",
                        pins={"left": ["1 P1 p", "2 PCT p", "3 P2 p"], "right": ["6 S1 p", "5 SCT p", "4 S2 p"]}),
    # SNOSD95C fig 6-1 / table 6-1 (DRR WSON-12): back-to-back common-drain FETs, VS = drain midpoint, OUT = HSFET
    # source; pad RTN floating. Table 7.5: V(UVLOR) and V(OVR) 1.195-1.267 V rising, V(UVLOF) and V(OVF) 1.091-1.159 V
    # falling, V(ENF) 0.3-0.93 V (shutdown); EN low -> HGATE off 3-6 us; enable -> DGATE on 98-270 us; 70 V abs max
    "LM74800": ic(TI, "LM74800QDRRRQ1", "WSON-12 (DRR)", "power-supply/LM7480-Q1.pdf",
                  "Ideal-diode + load-disconnect controller 3-65 V, UVLO and OV, back-to-back FETs",
                  ["2 A pi", "3 VSNS i", "4 SW p", "6 EN/UVLO i", "5 OV i", "7 GND pi"],
                  ["1 DGATE o", "12 C i", "10 VS pi", "11 CAP p", "8 HGATE o", "9 OUT i", "13 RTN nc"]),
    # SLLSEY7G fig 4-1 (ISO1211 D SOIC-8): 1 VCC1, 2 EN, 3 OUT, 4 GND1, 5 SUB, 6 FGND, 7 IN, 8 SENSE
    "ISO1211": ic(TI, "ISO1211DR", "SOIC-8 (D)", "isolation-interface/ISO1211.pdf",
                  "Isolated 24-60 V digital input, IEC 61131-2 type 1/2/3 (spare trip loop)",
                  ["1 VCC1 pi", "2 EN i", "3 OUT o", "4 GND1 pi"], ["8 SENSE i", "7 IN i", "6 FGND p", "5 SUB p"]),
    # Nexperia BZX84 Rev 7 table 2: 1 anode, 2 n.c., 3 cathode; C12 = 11.40-12.70 V; 250 mW
    "BZX84C12": dict(mfr="Nexperia", mpn="BZX84-C12,215", prefix="D", pkg="SOT-23", ds=DS + "protection/BZX84.pdf",
                     desc="Zener 12 V 5 % 250 mW", pins={"left": ["1 A p"], "right": ["3 K p", "2 NC nc"]}),
    # Diodes Inc DS11005 p.1 diagram: BAT54C pins 1 / 2 = anodes, pin 3 = common cathode
    "BAT54C": dict(mfr="Diodes Incorporated", mpn="BAT54C-7-F", prefix="D", pkg="SOT-23",
                   ds=DS + "protection/BAT54S-Diodes.pdf", desc="Dual common-cathode Schottky 30 V 200 mA",
                   pins={"left": ["1 A1 p", "2 A2 p"], "right": ["3 K p"]}),
    # Samtec TSW catalog (connectors/Samtec-TSW.pdf): TSW-102-07-G-S = 1x2, 2.54 mm, single row
    "J_SVC": dict(mfr="Samtec", mpn="TSW-102-07-G-S", prefix="J", pkg="1x2 2.54 mm header",
                  ds=DS + "connectors/Samtec-TSW.pdf", stock=("Connector_Generic", "Conn_01x02"),
                  desc="Service jumper header (fit a 2.54 mm shunt only for programming / debug)"),
    # Wurth WL-SMCW 0603 datasheets: AlInGaP, VF 2.0 V typ / 2.4 V max @ 20 mA (green InGaN would need ~3 V)
    "LED_G": dict(mfr="Wurth Elektronik", mpn="150060VS75000", prefix="D", pkg="0603", stock=("Device", "LED"),
                  ds=DS + "passives-capacitors/WE-150060VS75000.pdf", desc="LED 0603 bright green 570 nm, VF 2.0 V"),
    "LED_R": dict(mfr="Wurth Elektronik", mpn="150060RS75000", prefix="D", pkg="0603", stock=("Device", "LED"),
                  ds=DS + "passives-capacitors/WE-150060RS75000.pdf", desc="LED 0603 red 625 nm, VF 2.0 V"),
    # ---------------------------------------------------------------- safety chain
    # SBVS301B fig 5-1 / table 5-1 (DRC VSON-10); G33 = 3.3 V, +/-4 % window (table 11-1); table 6.6:
    # CWD 10k to VDD, SET0 = 0, SET1 = 1 -> tWDL 9.0 ms (max 10.35), tWDU 195 ms (min 165.8); CRST open -> tRST 200 ms
    "TPS3850G33": ic(TI, "TPS3850G33DRCR", "VSON-10 (DRC)", "power-supply/TPS3850.pdf",
                     "Supervisor 3.3 V +/-4 % window + programmable window watchdog",
                     ["1 VDD pi", "10 SENSE i", "7 WDI i", "3 SET0 i", "6 SET1 i", "2 CWD p", "4 CRST p",
                      "5 GND pi", "11 EP p"], ["9 ~{RESET} oc", "8 ~{WDO} oc"]),
    # SCES217AA pin table (DBV-5)
    "SN74LVC1G08": ic(TI, "SN74LVC1G08DBVR", "SOT-23-5 (DBV)", "isolation-interface/SN74LVC1G08.pdf",
                      "Single 2-input AND", ["1 A i", "2 B i", "3 GND pi"], ["5 VCC pi", "4 Y o"]),
    # SCES487I fig 4-1 / table 4-1 (DBV-6)
    "SN74LVC1G11": ic(TI, "SN74LVC1G11DBVR", "SOT-23-6 (DBV)", "isolation-interface/SN74LVC1G11.pdf",
                      "Single 3-input AND", ["1 A i", "3 B i", "6 C i", "2 GND pi"], ["5 VCC pi", "4 Y o"]),
    # SCES351Y pin table (DBV-5); inputs 5.5 V tolerant
    "SN74LVC1G17": ic(TI, "SN74LVC1G17DBVR", "SOT-23-5 (DBV)", "isolation-interface/SN74LVC1G17.pdf",
                      "Single Schmitt-trigger buffer", ["2 A i", "1 NC nc", "3 GND pi"], ["5 VCC pi", "4 Y o"]),
    # SCES794G fig 5-1 (DCU VSSOP-8) and function table 8-1: CLR low forces Q low; CLK rising edge loads D
    "SN74LVC1G74": ic(TI, "SN74LVC1G74DCUR", "VSSOP-8 (DCU)", "isolation-interface/SN74LVC1G74.pdf",
                      "D flip-flop with async preset / clear",
                      ["2 D i", "1 CLK i", "7 ~{PRE} i", "6 ~{CLR} i", "4 GND pi"], ["8 VCC pi", "5 Q o", "3 ~{Q} o"]),
    # SCAS283W fig 4-1 (PW TSSOP-14)
    "SN74LVC08A": ic(TI, "SN74LVC08APWR", "TSSOP-14 (PW)", "isolation-interface/SN74LVC08A.pdf", "Quad 2-input AND",
                     ["1 1A i", "2 1B i", "4 2A i", "5 2B i", "9 3A i", "10 3B i", "12 4A i", "13 4B i", None,
                      "7 GND pi"],
                     ["3 1Y o", None, "6 2Y o", None, "8 3Y o", None, "11 4Y o", None, None, "14 VCC pi"]),
    # ---------------------------------------------------------------- digital inputs / outputs
    # SLLSEY7G fig 4-2 (ISO1212 DBQ SSOP-16); 8.2.1.2: RSENSE 562R -> 2.25 mA, RTHR 1k (0.25 W MELF) -> type 1/3,
    # VIH <= 10.95 V, VIL >= 8.7 V; table 8-1: CIN 10 nF; fig 8-2: 500 pF FGND-PE. SUB = floating plane only.
    "ISO1212": ic(TI, "ISO1212DBQR", "SSOP-16 (DBQ)", "isolation-interface/ISO1211.pdf",
                  "Dual isolated 24-60 V digital input, IEC 61131-2 type 1/2/3, 2500 Vrms basic",
                  ["2 VCC1 pi", "3 EN i", "4 OUT1 o", "5 OUT2 o", "6 NC nc", "7 NC nc", None, "1 GND1 pi",
                   "8 GND1 pi"],
                  ["16 SENSE1 i", "15 IN1 i", "14 FGND1 p", None, "11 SENSE2 i", "10 IN2 i", "9 FGND2 p", None,
                   "13 SUB1 p", "12 SUB2 p"]),
    # SLVSF24C fig 6-3 / table 6-1 (version D, RHF VQFN-24): per-channel FLT1/FLT2. Table 7.5: ILNOM 4.0 A (one
    # channel) / 3.0 A per channel (both on) at 85 C; ICL 4.3-6.95 A with ILIM at GND, 1.58-2.3 A at 10k,
    # 0.52-0.82 A at 28.7k; RON 45 mOhm typ, 78 mOhm max at 125 C; VDS clamp 49-61 V; VS 4.5-36 V, 60 V abs max
    "TPS272C45D": ic(TI, "TPS272C45DRHFR", "VQFN-24 (RHF)", "isolation-interface/TPS272C45.pdf",
                     "Dual 45 mOhm 24 V industrial smart high-side switch, adjustable ILIM, per-channel fault",
                     ["8 VS pi", "9 VS pi", "23 VS pi", "24 VS pi", "19 VDD pi", None, "16 EN1 i", "15 EN2 i",
                      "12 DIA_EN i", "13 SEL i", "14 LATCH i", None, "17 GND pi", "25 EP p"],
                     ["1 VOUT1 p", "2 VOUT1 p", "3 VOUT1 p", None, "5 VOUT2 p", "6 VOUT2 p", "7 VOUT2 p", None,
                      "10 ~{FLT1} oc", "18 ~{FLT2} oc", "11 SNS o", "21 ILIM1 p", "20 ILIM2 p", "4 NC nc",
                      "22 NC nc"]),
    # Nexperia 2N7002BK table 2 pinning: 1 G, 2 S, 3 D; 60 V, 350 mA
    "2N7002BK": dict(mfr="Nexperia", mpn="2N7002BK,215", prefix="Q", pkg="SOT-23", ds=DS + "power-semiconductors/2N7002BK.pdf",
                     desc="N-MOSFET 60 V 350 mA, fan PWM open-drain driver",
                     pins={"left": ["1 G p"], "right": ["3 D p", "2 S p"]}),
    # Diodes Inc DS11005 rev 34-2 p.1 diagram: BAT54S pin 1 = A1, pin 2 = K2, pin 3 = K1/A2; 30 V, 200 mA
    "BAT54S": dict(mfr="Diodes Incorporated", mpn="BAT54S-7-F", prefix="D", pkg="SOT-23",
                   ds=DS + "protection/BAT54S-Diodes.pdf", desc="Dual series Schottky 30 V 200 mA, input clamp to 3V3/GND",
                   pins={"left": ["1 A1 p", "2 K2 p"], "right": ["3 COM p"]}),
    # ---------------------------------------------------------------- ADC, memory, expander
    # SLAS605C TSSOP-38 (DBT) pin table; VREF 2.0-3.0 V (spec'd 2.5 V), +VA 2.7-5.25 V, GPIO2 = range (0 -> 0..VREF)
    "ADS7953": ic(TI, "ADS7953SDBTR", "TSSOP-38 (DBT)", "sensing/ADS7953.pdf", "16-ch 12-bit 1 MSPS SAR ADC, SPI",
                  ["5 +VA pi", "29 +VA pi", "4 REFP i", "3 REFM i", None] +
                  ["%s CH%d i" % (p, n) for n, p in enumerate(["28", "27", "26", "25", "24", "23", "22", "21", "18",
                                                               "17", "16", "15", "14", "13", "12", "11"])],
                  ["36 +VBD pi", "31 ~{CS} i", "32 SCLK i", "33 SDI i", "34 SDO t", None, "7 MXO o", "8 AINP i",
                   "9 AINM i", None, "37 GPIO0 b", "38 GPIO1 b", "1 GPIO2 b", "2 GPIO3 b", None, "6 AGND pi",
                   "10 AGND pi", "19 AGND pi", "20 AGND pi", "30 AGND pi", "35 BDGND pi"]),
    # SBVS032K fig 5-1 (DBZ SOT-23-3); REF30: 25 mA load, CL 0.1-22 uF
    "REF3025": ic(TI, "REF3025AIDBZR", "SOT-23-3 (DBZ)", "sensing/REF3030.pdf", "Voltage reference 2.5 V 0.2 %",
                  ["1 IN pi", "3 GND pi"], ["2 OUT po"]),
    # SBOS854F fig 5-1 / table 5-1 (D SOIC-8); address 0x48 with A2..A0 = GND
    "TMP1075": ic(TI, "TMP1075DR", "SOIC-8 (D)", "sensing/TMP1075.pdf", "I2C temperature sensor +/-1 C",
                  ["8 V+ pi", "2 SCL i", "1 SDA b", "3 ALERT oc", "4 GND pi"], ["7 A0 i", "6 A1 i", "5 A2 i"]),
    # Infineon 001-84458 rev *K fig 1 (8-pin SOIC); 2.7-3.65 V, 10^14 cycles; address 0x50 with A2..A0 = GND
    "FM24CL64B": ic("Infineon", "FM24CL64B-GTR", "SOIC-8", "controllers/FM24CL64B.pdf",
                    "F-RAM 64 kbit I2C, 1e14 write cycles", ["8 VDD pi", "6 SCL i", "5 SDA b", "7 WP i", "4 VSS pi"],
                    ["1 A0 i", "2 A1 i", "3 A2 i"]),
    # Microchip DS20001952C table 2-1 (SSOP-28 column); IOCON.HAEN: up to 8 devices on one CS by A2..A0
    "MCP23S17": ic("Microchip", "MCP23S17T-E/SS", "SSOP-28", "controllers/MCP23S17.pdf", "16-bit SPI I/O expander",
                   ["9 VDD pi", "11 ~{CS} i", "12 SCK i", "13 SI i", "14 SO t", "18 ~{RESET} i", "15 A0 i",
                    "16 A1 i", "17 A2 i", "20 INTA o", "19 INTB o", "10 VSS pi"],
                   ["%d GPA%d b" % (21 + n, n) for n in range(8)] + [None] + ["%d GPB%d b" % (1 + n, n) for n in range(8)]),
    # ---------------------------------------------------------------- connectors
    # LINK-PP LPJ4012AHNL drawing LP08121230 rev A (image-only PDF) sheet 1: 1 TD+, 2 TCT, 3 TD-, 4 RD+, 5 RCT, 6 RD-,
    # 7 NC, 8 chassis ground via 1000 pF / 2 kV to the 75 R Bob-Smith node; green LED A10 / K9, yellow A12 / K11;
    # 1CT:1CT, OCL 350 uH min, hipot 1500 Vrms, operating -40..+85 C (PV-20: -30..+60 C ambient). Rev E: replaces
    # WE 7499010211A (0..+70 C). Not on LCSC (jlcsearch mirror, 2026-10-04): maker / its distributors.
    "RJ45": ic("LINK-PP", "LPJ4012AHNL", "RJ45 THT tab down, 1:1 magnetics, 2 LEDs", "connectors/LINK-PP-LPJ4012AHNL.pdf",
               "RJ45 10/100BASE-T, integrated magnetics and LEDs, -40..+85 C, 1500 Vrms hipot",
               ["1 TD+ p", "3 TD- p", "2 TCT p", None, "4 RD+ p", "6 RD- p", "5 RCT p", None, "7 NC nc"],
               ["10 LEDG_A p", "9 LEDG_K p", "12 LEDY_A p", "11 LEDY_K p", None, "8 SHLD p"], prefix="J"),
    "MH": dict(mfr="", mpn="", prefix="H", pkg="M3 plated hole", ds="", sourcing="NOPART",
               stock=("Mechanical", "MountingHole_Pad"), desc="Chassis (PE) bonding mounting hole"),
    "J_SYS": conn("SSW-140-01-G-D", 80, SSW, "2x40 2.54 mm socket THT",
                  "Socket strip 2x40 2.54 mm, mates TSW-140-07-G-D on CTRL-C2000, 4.7 A/pin", mfr="Samtec"),
    "J_GW": conn("SSW-105-01-G-D", 10, SSW, "2x5 2.54 mm socket THT", "Socket strip 2x5 2.54 mm, BMU-GW", mfr="Samtec"),
    "J_PC5_2": conn("1720466", 2, PX + "pcb-header-pc-5-2-g-762-1720466", "PC 5/ 2-G-7.62 THT",
                    "PCB header 2-pos 7.62 mm, 41 A 630 V (maker listing), 24 V feed"),
    "J_PC5_3": conn("1720479", 3, PX + "pcb-header-pc-5-3-g-762-1720479", "PC 5/ 3-G-7.62 THT",
                    "PCB header 3-pos 7.62 mm, 41 A 630 V (maker listing), AUX-HV feed"),
    "J_MC4": conn("1803293", 4, PX + "pcb-header-mc-15-4-g-381-1803293", "MC 1.5/ 4-G-3.81 THT",
                  "PCB header 4-pos 3.81 mm, 8 A 160 V (maker listing)"),
    "J_MC10": conn("1803358", 10, PX + "pcb-header-mc-15-10-g-381-1803358", "MC 1.5/10-G-3.81 THT",
                   "PCB header 10-pos 3.81 mm, 8 A 160 V (maker listing)"),
})

ISO_PORTS = ("CANB", "MCAN", "RSA", "RSB")


def domain_of(net):
    """PELV logic / 24 V, one domain per isolated field port (ISO_<port>_*), the DI field side (DIF_*), chassis PE."""
    if net.startswith("ISO_"):
        return net.split("_")[1]
    if net.startswith("DIF_"):
        return "DI"
    return "PE" if net == "PE" else "PELV"


def dec(B, *rails, n=1):
    """100 nF decoupling per supply pin group."""
    for r in rails:
        for _ in range(n):
            B.C("100n", r, "GND")


def chan(B, part, ch):
    """Record which safety channel a part belongs to (FMEA table)."""
    B.chan[part.ref] = ch
    return part


# ======================================================================================================== 01
AUX_DIV = {"en": ("100k", "6.49k"), "ov": ("100k", "5.23k"), "cab": ("100k", "1.93k")}   # top, bottom
# HGATE inrush network, LM7480-Q1 (SNOSD95C) fig 9-3 / eq. 2: R1 in series with CdVdT from HGATE to GND (R1 keeps the
# turn-off fast); 9.3.2.2: while the HGATE driver is disabled HGATE is internally connected to OUT, so CdVdT sits at
# the bus voltage and a hand-over starts from there. Value chosen by check_ramp (black start and hand-over).
DVDT = ("1k", "2.2u")


def sheet_power_in(B):
    B.new_sheet("01_power_in", "24 V entry, protection and window",
                "2 cabinet feeds (LM74700 ORing) + AUX-HV feed (LM74800 switch, cabinet priority, OV),\n"
                "2 mF surge reservoir, precision OV cut-off 26.13-26.42 V, PG_24V window, eFuse UVLO")
    for n in (1, 2):
        f = "24V_F%d" % n
        B.block("Feed %d: cabinet 24 V %s" % (n, "AB"[n - 1]),
                "Fuse 30 A / 60 VDC; TVS 1500 W bidirectional (53 V clamp, blocks -33 V wiring error).\n"
                "LM74700 + CSD18532Q5B: ideal diode, ORs the feeds, blocks reverse current and polarity.")
        B.part("J_PC5_2", {"1": "24V_IN%d" % n, "2": "GND"})
        B.part("SF2923HC30", {"1": "24V_IN%d" % n, "2": f})
        B.part("SMCJ33CA", {"1": f, "2": "GND"})
        B.C("100n", f, "GND", volt="100V")
        B.part("LM74700", {"6": f, "3": f, "2": "GND", "4": "24V_OR", "5": "OR%d_G" % n, "1": "OR%d_CAP" % n})
        B.C("100n", "OR%d_CAP" % n, f, volt="16V")
        B.part("CSD18532Q5B", {"4": "OR%d_G" % n, "5": "24V_OR", "6": "24V_OR", "7": "24V_OR", "8": "24V_OR",
                               "1": f, "2": f, "3": f})
        B.flag(f)
    B.block("Feed 3: AUX-HV 24 V (30 W bootstrap, interfaces.AUX)", "")
    B.aux_block = B.blk
    pins = IF.pins(IF.AUX, rename={"PG_HVAUX": "PG_HVAUX_IN"})
    B.part("J_PC5_3", pins)
    B.part("SF2923HC30", {"1": pins["1"], "2": "24V_F3"})
    B.part("SMCJ33CA", {"1": "24V_F3", "2": "GND"})
    B.C("100n", "24V_F3", "GND", volt="100V")
    B.part("LM74800", {"2": "24V_F3", "3": "24V_F3", "4": "AUX_SW", "6": "AUX_EN", "5": "AUX_OV", "7": "GND",
                       "1": "AUX_DG", "12": "AUX_CD", "10": "AUX_CD", "11": "AUX_CAP", "8": "AUX_HG", "9": "24V_OR",
                       "13": None})
    B.C("100n", "AUX_CAP", "AUX_CD", volt="25V")
    B.C("100n", "AUX_CD", "GND", volt="100V")
    B.R(DVDT[0], "AUX_HG", "AUX_DVDT", note="HGATE series R1")
    B.C(DVDT[1], "AUX_DVDT", "GND", pkg="1210", volt="100V", tol="10%")
    B.flag("AUX_CD")
    B.part("CSD18532Q5B", {"4": "AUX_DG", "1": "24V_F3", "2": "24V_F3", "3": "24V_F3", "5": "AUX_CD", "6": "AUX_CD",
                           "7": "AUX_CD", "8": "AUX_CD"})
    B.part("CSD18532Q5B", {"4": "AUX_HG", "5": "AUX_CD", "6": "AUX_CD", "7": "AUX_CD", "8": "AUX_CD", "1": "24V_OR",
                           "2": "24V_OR", "3": "24V_OR"})
    B.R(AUX_DIV["en"][0], "24V_F3", "AUX_EN", tol="0.1%", note="10 ppm/K thin film")
    B.R(AUX_DIV["en"][1], "AUX_EN", "GND", tol="0.1%", note="10 ppm/K thin film")
    B.R(AUX_DIV["ov"][0], "AUX_SW", "AUX_OV", tol="0.1%", note="10 ppm/K thin film")
    B.R(AUX_DIV["ov"][1], "AUX_OV", "GND", tol="0.1%", note="10 ppm/K thin film")
    B.C("10n", "AUX_EN", "GND", tol="10%")
    B.flag("24V_F3")
    B.block("Cabinet priority detector (powered from the AUX feed)",
            "BZX84-C12 rail from 24V_F3: works before any logic rail exists, so AUX-HV can start the module alone.\n"
            "Each TPS3700 OUTB pulls AUX_EN low (LM74800 shutdown, both FETs off) while its cabinet feed is healthy.\n"
            "OUTA (INA+ = VDET / 11) holds AUX_EN low while the detector itself is starting (SBVS187G 7.4.2, fig 22),\n"
            "so a hot-plugged AUX-HV can neither enable the LM74800 nor trip the inhibit before the decision is valid.")
    B.R("2.2k", "24V_F3", "VDET", pkg="1206")
    B.part("BZX84C12", {"1": "GND", "3": "VDET", "2": None})
    B.flag("VDET")
    B.R("100k", "VDET", "VDET_SNS")
    B.R("10k", "VDET_SNS", "GND")
    for n in (1, 2):
        s = "CAB%d_SNS" % n
        B.part("TPS3700", {"5": "VDET", "3": "VDET_SNS", "4": s, "2": "GND", "1": "AUX_EN", "6": "AUX_EN"})
        B.C("100n", "VDET", "GND", volt="25V")
        B.R(AUX_DIV["cab"][0], "24V_F%d" % n, s, tol="0.1%", note="10 ppm/K thin film")
        B.R(AUX_DIV["cab"][1], s, "GND", tol="0.1%", note="10 ppm/K thin film")
        B.C("10n", s, "GND", tol="10%")
    B.block("AUX-HV power-good", "PG_HVAUX is driven actively high by AUX-HV when good (interfaces.AUX): 100k pull-down\n"
                                "here, so a dead or unplugged AUX-HV reads not-good. 10k + BAT54S clamp toward CTRL.")
    B.R("100k", "PG_HVAUX_IN", "GND")
    B.R("10k", "PG_HVAUX_IN", "PG_HVAUX")
    B.part("BAT54S", {"1": "GND", "2": "3V3", "3": "PG_HVAUX"})
    B.C("1n", "PG_HVAUX", "GND", tol="10%")
    B.block("ORed 24 V bus 24V_OR", "Cabinet feeds share by ideal-diode ORing; AUX-HV joins only through its LM74800 switch.")
    for _ in range(4):
        B.C("10u", "24V_OR", "GND", pkg="1210", volt="50V", tol="10%")
    B.block("Surge reservoir", "")
    B.surge_block = B.blk
    for _ in range(2):
        B.part("CB1000", {"1": "24V_OR", "2": "GND"})
    B.flag("24V_OR")
    B.flag("GND")
    B.TP("24V_OR")
    B.TP("GND")
    B.block("24 V window: OV cut-off, PG_24V, eFuse UVLO", "")
    B.window_block = B.blk
    B.part("REF5025E", {"2": "5V", "1": "5V", "3": None, "5": None, "4": "GND", "6": "VREF24", "7": None, "8": None})
    B.C("1u", "VREF24", "GND", volt="16V", tol="10%")
    B.C("100n", "5V", "GND")
    B.part("LM2903B", {"8": "5V", "4": "GND", "3": "OV_SNS", "2": "VREF24", "1": "OV_CUT", "5": "VREF24",
                       "6": "PGH_SNS", "7": "PG_WIN"})
    B.C("100n", "5V", "GND")
    for top, bot, node in (("16.9k", "1.78k", "OV_SNS"), ("18.7k", "2.00k", "PGH_SNS")):
        B.R(top, "24V_OR", node, tol="0.1%", note="10 ppm/K thin film")
        B.R(bot, node, "GND", tol="0.1%", note="10 ppm/K thin film")
        B.C("10n", node, "GND", tol="10%")
    B.R("1M", "OV_CUT", "OV_SNS", note="hysteresis")
    B.R("10k", "OV_CUT", "3V3")
    B.part("TPS3700", {"5": "3V3", "3": "WIN_UV", "4": "GND", "2": "GND", "1": "PG_WIN", "6": None})
    B.R("100k", "24V_OR", "WIN_UV", tol="0.1%", note="10 ppm/K thin film")
    B.R("1.91k", "WIN_UV", "GND", tol="0.1%", note="10 ppm/K thin film")
    B.C("10n", "WIN_UV", "GND", tol="10%")
    B.R("10k", "PG_WIN", "3V3")
    B.part("SN74LVC1G17", {"2": "PG_WIN", "1": None, "3": "GND", "5": "3V3", "4": "PG24_B"})
    B.R("100R", "PG24_B", "PG_24V")
    B.R("100k", "24V_OR", "EF_UVLO", tol="0.1%")
    B.R("8.06k", "EF_UVLO", "GND", tol="0.1%")
    B.C("10n", "EF_UVLO", "GND", tol="10%")
    dec(B, "3V3", n=2)
    B.TP("OV_CUT")
    B.TP("PG_WIN")
    B.TP("VREF24")


# ======================================================================================================== 02
def efuse(B, vin, out, shdn, ilim_r, flt, imon=None, tag=""):
    """TPS16630: shared UVLO divider, OVP = OV_CUT (26.13-26.42 V cut-off), auto-retry (MODE = GND), 22 nF dVdT
    -> 2 uA x 25 / 22 nF = 2.3 V/ms (24 V in ~11 ms)."""
    p = B.part("TPS16630", {"1": vin, "2": vin, "3": vin, "6": vin, "7": "EF_UVLO", "8": "OV_CUT", "13": shdn,
                            "12": "GND", "9": "GND", "21": "GND", "18": out, "19": out, "20": out, "15": flt,
                            "16": None, "14": imon, "11": tag + "_ILIM", "10": tag + "_DVDT", "4": None, "5": None,
                            "17": None})
    B.R(ilim_r, tag + "_ILIM", "GND")
    B.C("22n", tag + "_DVDT", "GND", volt="16V", tol="10%")
    B.C("100n", vin, "GND", volt="100V")
    return p


def sheet_rails(B):
    B.new_sheet("02_rails", "24 V branches and local rails",
                "+24V to CTRL-C2000 (2 A eFuse), +24V_GD gate-drive bias (5 A eFuse, channel B),\n"
                "5 V buck (LMR36015), 3.3 V LDO, rail LEDs and test points")
    B.block("+24V branch to CTRL-C2000 and port board",
            "TPS16630 ILIM 9.09k -> 1.98 A (1.84-2.12 A): CTRL logic + port board (CTRL feeds it via a 1.1 A PTC).\n"
            "SHDN open = on (internal pull-up): CTRL power does not depend on this board's 3.3 V.")
    efuse(B, "24V_OR", "+24V", "P24_SHDN", "9.09k", "P24_FLT_N", tag="P24")
    B.C("100n", "P24_SHDN", "GND", volt="16V")
    for _ in range(2):
        B.C("10u", "+24V", "GND", pkg="1210", volt="50V", tol="10%")
    B.TP("+24V")
    B.block("+24V_GD gate-drive bias (safety channel B)",
            "TPS16630, SHDN = CHB_OK_L: channel B open -> no bias on any power board -> no gate. ILIM 3.57k ->\n"
            "5.04 A (4.69-5.39 A) for 4 A continuous (0.85 W at 53 mOhm max RON). FLT -> expander 2,\n"
            "IMON 27.9 uA/A -> ADC CH15. OV_CUT protects the UCC14241 modules (VIN 21-27 V).")
    chan(B, efuse(B, "24V_OR", "+24V_GD", "CHB_OK_L", "3.57k", "GD_FLT_N", imon="IMON_GD", tag="GD"), "B")
    for _ in range(2):
        B.C("10u", "+24V_GD", "GND", pkg="1210", volt="50V", tol="10%")
    B.TP("+24V_GD")
    B.block("5 V buck", "LMR36015A 400 kHz, table 10-3 values: 10 uH, 2 x 22 uF, RFB 100k/24.9k -> 5.02 V.\n"
                        "Load <= 0.72 A + BMU-GW 0.27 A (datasheet maxima, check_budget): 4 isolated ports, ADC, 3.3 V LDO.\n"
                        "EN UVLO 95.3k/10k (SNVSB49D 10.2.1.2.9.1): on 11.9-14.0 V, off 10.8-12.8 V (hysteresis 110 mV typ):\n"
                        "no logic load on a slowly rising AUX-HV black-start ramp below that; rides any hand-over.")
    B.part("LMR36015", {"2": "24V_OR", "10": "24V_OR", "9": "BUCK_EN", "6": "GND", "1": "GND", "11": "GND",
                        "12": "BUCK_SW", "3": "BUCK_SW", "4": "BUCK_BOOT", "5": "BUCK_VCC", "7": "BUCK_FB", "8": None})
    for _ in range(2):
        B.C("4.7u", "24V_OR", "GND", pkg="1210", volt="50V", tol="10%")
    B.C("100n", "24V_OR", "GND", volt="100V")
    B.C("100n", "BUCK_BOOT", "BUCK_SW", volt="16V")
    B.R("95.3k", "24V_OR", "BUCK_EN")
    B.R("10k", "BUCK_EN", "GND")
    B.C("1u", "BUCK_VCC", "GND", volt="16V")
    B.part("L10U", {"1": "BUCK_SW", "2": "5V"})
    B.R("100k", "5V", "BUCK_FB")
    B.R("24.9k", "BUCK_FB", "GND")
    for _ in range(2):
        B.C("22u", "5V", "GND", pkg="1210", volt="16V", tol="20%")
    B.flag("5V")
    B.block("3.3 V LDO", "")
    B.ldo_block = B.blk
    B.part("TPS7A2033", {"1": "5V", "3": "5V", "2": "GND", "5": "3V3", "4": None})
    B.C("1u", "5V", "GND", volt="16V")
    B.C("10u", "3V3", "GND", pkg="0805", volt="16V")
    B.block("Rail LEDs and test points", "24V_OR 10k (2.2 mA), 5V 1k, 3V3 470R; bright-green AlInGaP LEDs.")
    for rail, r, pkg in (("24V_OR", "10k", "0805"), ("5V", "1k", "0603"), ("3V3", "470R", "0603")):
        a = "LED_%s_A" % rail.replace("24V_OR", "24")
        B.R(r, rail, a, pkg=pkg)
        B.part("LED_G", {"2": a, "1": "GND"})
    B.TP("5V")
    B.TP("3V3")


# ======================================================================================================== 03
def sheet_ctrl_if(B):
    B.new_sheet("03_ctrl_if", "CTRL-C2000 and BMU-GW connectors",
                "J301 = interfaces.SYS 2x40 (every contract signal), J302 = interfaces.GW 2x5;\n"
                "CANA and SCIC go to the BMU-GW socket; SCIC receiver always enabled")
    B.block("CTRL-C2000 board-to-board (interfaces.SYS)",
            "Samtec SSW-140-01-G-D socket mates the TSW-140-07-G-D header on CTRL (both catalogs list the pair;\n"
            "TSW -07 post 5.84 mm inside SSW insertion depth 3.68-6.35 mm). 4.7 A/pin: +24V and +24V_GD 2 pins each.\n"
            "No I2C pull-ups here (CTRL has them). Pull-ups / pull-downs sit next to each driver on its sheet.")
    B.part("J_SYS", IF.pins(IF.SYS))
    B.C("100n", "+24V", "GND", volt="50V")
    B.C("100n", "+24V_GD", "GND", volt="50V")
    B.block("BMU-GW socket (interfaces.GW)", "CANA -> CAN_TX/RX, SCIC -> RS485_D/R/DE; RS485_RE_N tied low here:\n"
                                             "the SYS contract carries no SCIC_RE, so the receiver stays on.")
    B.part("J_GW", IF.pins(IF.GW, rename={"CAN_TX": "CANA_TX", "CAN_RX": "CANA_RX", "RS485_D": "SCIC_TX",
                                          "RS485_R": "SCIC_RX", "RS485_DE": "SCIC_DE", "RS485_RE_N": "GND"}))
    B.C("10u", "5V", "GND", pkg="0805", volt="16V")
    B.C("100n", "3V3", "GND")


# ======================================================================================================== 04/05
def iso_supply(B, tag, iso):
    """SN6505B push-pull + WE 760390014 1:1.3 + full-wave Schottky -> ~6.2-6.7 V unregulated -> TPS7A2650 5.0 V
    +/-1 % (SN6505B table 9-3 '5 V -> 5 V, 100 mA, with LDO'): VCC2 stays 4.95-5.05 V at any load (CSR-11)."""
    v, u, g = "ISO_%s_5V" % tag, "ISO_%s_VU" % tag, "ISO_%s_GND" % tag
    d1, d2, s1, s2 = "XF_%s_D1" % tag, "XF_%s_D2" % tag, "ISO_%s_S1" % tag, "ISO_%s_S2" % tag
    B.part("SN6505B", {"2": "5V", "5": "5V", "6": "GND", "4": "GND", "1": d1, "3": d2})
    B.C("100n", "5V", "GND")
    B.C("10u", "5V", "GND", pkg="0805", volt="16V")
    iso.add(B.part("WE760390014", {"1": d1, "2": "5V", "3": d2, "6": s1, "5": g, "4": s2}).ref)
    B.part("PMEG4010CEJ", {"2": s1, "1": u})
    B.part("PMEG4010CEJ", {"2": s2, "1": u})
    B.C("10u", u, g, pkg="0805", volt="16V")
    B.part("TPS7A2650", {"6": u, "4": u, "5": g, "7": g, "1": v, "3": None, "2": g})
    B.C("2.2u", v, g, volt="16V", tol="10%")
    B.C("100n", v, g)
    B.flag(u)
    B.flag(g)
    return v, g


def field_conn(B, tag, a, b, g):
    sh = "ISO_%s_SH" % tag
    B.part("J_MC4", {"1": a, "2": b, "3": g, "4": sh})
    B.R("1M", sh, g, pkg="1206")
    B.C("4.7n", sh, g, pkg="1206", volt="1kV", tol="10%")


def can_port(B, tag, tx, rx, title, iso):
    B.block(title, "ISO1042 5 kVrms, own isolated 5 V. Split termination 2 x 60.4R + 4.7n, closed by JP.\n"
                   "Terminal: 1 CANH, 2 CANL, 3 isolated GND, 4 shield. Port supply 760390014 + TPS7A2650: 5.0 V +/-1 %.")
    v, g = iso_supply(B, tag, iso)
    h, l, th, tm = ("ISO_%s_%s" % (tag, s) for s in ("H", "L", "TH", "TM"))
    iso.add(B.part("ISO1042", {"1": "3V3", "3": tx, "5": rx, "4": None, "6": None, "7": None, "2": "GND", "8": "GND",
                               "16": v, "11": v, "13": h, "12": l, "14": None, "15": g, "10": g, "9": g}).ref)
    B.C("100n", "3V3", "GND")
    B.C("100n", v, g)
    B.R("10k", tx, "3V3")               # recessive while CTRL is in reset
    B.part("JP_OPEN", {"1": h, "2": th})
    B.R("60.4R", th, tm, pkg="0805")
    B.R("60.4R", tm, l, pkg="0805")
    B.C("4.7n", tm, g, volt="100V", tol="10%")
    B.part("ESD2CAN24", {"1": h, "2": l, "3": g})
    field_conn(B, tag, h, l, g)


def rs485_port(B, tag, tx, rx, de, title, iso):
    B.block(title, "ISO1410 5 kVrms half duplex, own regulated isolated 5.0 V, fail-safe receiver always on (~RE = GND).\n"
                   "120R termination by JP. Terminal: 1 A, 2 B, 3 isolated GND, 4 shield (1M || 4.7n).")
    v, g = iso_supply(B, tag, iso)
    a, b, ta = ("ISO_%s_%s" % (tag, s) for s in ("A", "B", "TA"))
    iso.add(B.part("ISO1410", {"1": "3V3", "6": tx, "5": de, "4": "GND", "3": rx, "7": None, "2": "GND", "8": "GND",
                               "16": v, "12": a, "13": b, "10": None, "11": None, "14": None, "15": g, "9": g}).ref)
    B.C("100n", "3V3", "GND")
    B.C("100n", v, g)
    B.R("10k", tx, "3V3")
    B.R("10k", de, "GND")              # driver off while CTRL is in reset
    B.part("JP_OPEN", {"1": a, "2": ta})
    B.R("120R", ta, b, pkg="0805")
    B.part("SM712", {"1": a, "2": b, "3": g})
    field_conn(B, tag, a, b, g)


def sheet_comms(B, iso):
    B.new_sheet("04_can", "Isolated CAN ports (CANB, MCAN)",
                "CANB module bus and MCAN CAN-FD service port: ISO1042 + SN6505B isolated 5 V each,\n"
                "jumper-selectable split termination, ESD2CAN24 TVS, 4-pole field terminal")
    can_port(B, "CANB", "CANB_TX", "CANB_RX", "CANB module bus (isolated)", iso)
    can_port(B, "MCAN", "MCAN_TX", "MCAN_RX", "MCAN CAN FD service port (isolated)", iso)
    B.new_sheet("05_rs485", "Isolated RS-485 ports (SCIA, SCIB)",
                "Two half-duplex RS-485 ports: ISO1410 + SN6505B isolated 5 V each,\n"
                "jumper-selectable 120R termination, SM712 TVS, 4-pole field terminal")
    rs485_port(B, "RSA", "SCIA_TX", "SCIA_RX", "SCIA_DE", "RS-485 port 1 = SCIA (isolated)", iso)
    rs485_port(B, "RSB", "SCIB_TX", "SCIB_RX", "SCIB_DE", "RS-485 port 2 = SCIB (isolated)", iso)


# ======================================================================================================== 06
def sheet_eth(B, iso):
    B.new_sheet("06_eth", "Ethernet magnetics and chassis bond",
                "RJ45 with integrated 1:1 magnetics for the 100BASE-TX PHY on CTRL-C2000,\n"
                "centre taps on ETH_CT (PHY analog supply), link / activity LEDs, PE bonding point")
    B.block("RJ45 with magnetics (MDI from the PHY on CTRL)",
            "LINK-PP LPJ4012AHNL: 1CT:1CT, 1500 Vrms hipot, -40..+85 C (rev E, PV-20; was WE 7499010211A 0..70 C).\n"
            "Centre taps TCT / RCT on ETH_CT = the PHY's AVD sent from CTRL (SYS pin 77); decoupled only, no\n"
            "local 3.3 V. LEDs (VF 1.8-2.6 V): PHY pins are active-low (CTRL 2.49k strap pull-ups); 330R from local 3V3.")
    iso.add(B.part("RJ45", {"1": "ETH_TXP", "3": "ETH_TXN", "2": "ETH_CT", "4": "ETH_RXP", "6": "ETH_RXN",
                            "5": "ETH_CT", "10": "ETH_LEDG_A", "9": "ETH_LED_LINK", "12": "ETH_LEDY_A",
                            "11": "ETH_LED_ACT", "8": "PE", "7": None}).ref)
    B.C("100n", "ETH_CT", "GND")
    B.C("100n", "ETH_CT", "GND")
    B.R("330R", "3V3", "ETH_LEDG_A")
    B.R("330R", "3V3", "ETH_LEDY_A")
    B.block("Chassis (PE) bond", "Plated mounting hole to the module chassis: RJ45 Bob-Smith node,\n"
                                 "DI field-ground 470 pF (sheet 07). PELV 0 V is not bonded here.")
    B.part("MH", {"1": "PE"})
    B.flag("PE")


# ======================================================================================================== 07
FB_DI = (4, 5)          # FB_A_MAIN, FB_B_MAIN: module-internal, PELV GND referenced (interfaces.DI comment)
DI_PAIRS = ((1, 2), (3, 6), (4, 5), (7, 8))   # ISO1212 packages: no package mixes DIF_GND and GND channels


def di_field(n):
    """Field-side net prefix of input n: external inputs live in the DI domain (DIF_*), FB inputs in PELV (FB*)."""
    return ("FBF_" if n in FB_DI else "DIF_") + IF.DI[n - 1]


def sheet_di(B, iso, crossing):
    B.new_sheet("07_di", "Isolated 24 V digital inputs",
                "8 x IEC 61131-2 type 1/3 inputs, 4 x ISO1212; DI1 ESTOP_A (U701), DI8 ESTOP_B (U704)\n"
                "feed the chain; FB_A/B_MAIN (PELV GND) on J702 and in their own package U703")
    B.block("DI field terminals (interfaces.DI)",
            "J701 pin n = DIn for the six external inputs (4, 5 unused), 9/10 = field 0 V DIF_GND (470 pF / 2 kV to PE).\n"
            "FB_A_MAIN (DI4) / FB_B_MAIN (DI5) are module-internal, PELV: J702 1 FB_A, 2 GND, 3 FB_B, 4 GND.\n"
            "ISO1212 channels of one package share only functional insulation (+/-60 V, SLLSEY7G 6.1), so DI4 + DI5\n"
            "sit together in U703 with both FGND on GND; U701 / U702 / U704 carry only DIF_GND channels.")
    pins = {str(i): (None if i in FB_DI else di_field(i)) for i in range(1, 9)}
    pins.update({"9": "DIF_GND", "10": "DIF_GND"})
    B.part("J_MC10", pins)
    B.part("J_MC4", {"1": di_field(FB_DI[0]), "2": "GND", "3": di_field(FB_DI[1]), "4": "GND"})
    crossing.add(B.C("470p", "DIF_GND", "PE", pkg="1206", volt="2kV", diel="C0G", tol="5%").ref)
    B.flag("DIF_GND")
    chain_out = {1: ("ESTOP_A_OK", "A"), 8: ("ESTOP_B_OK", "B")}
    for a, b in DI_PAIRS:
        estop = [n for n in (a, b) if n in chain_out]
        fgnd = "GND" if a in FB_DI else "DIF_GND"
        B.block("DI%d %s, DI%d %s" % (a, IF.DI[a - 1], b, IF.DI[b - 1]),
                "RTHR 1k MELF 0.25 W, RSENSE 562R, CIN 10n: VIH <= 10.95 V, VIL >= 8.7 V, 2.25 mA -> type 1 and 3.\n"
                "SUB pins go to a floating 2 x 2 mm copper island only (datasheet), never to FGND. FGND = %s." % fgnd +
                "".join("\nDI%d = E-stop channel %s: drives the chain directly; DI%d to CTRL through 1k."
                        % (n, chain_out[n][1], n) for n in estop))
        p = {"2": "3V3", "3": "3V3", "6": None, "7": None, "1": "GND", "8": "GND", "13": None, "12": None,
             "14": fgnd, "9": fgnd}
        for n, (sp, ip, op) in ((a, ("16", "15", "4")), (b, ("11", "10", "5"))):
            pre = "FB_" if n in FB_DI else "DIF_"
            s, i = "%sS%d" % (pre, n), "%sI%d" % (pre, n)
            p[sp], p[ip], p[op] = s, i, chain_out.get(n, ("DI%d" % n,))[0]
            B.R("1k", di_field(n), s, pkg="MELF 0204", note="0.25 W RTHR")
            B.C("10n", s, fgnd, volt="100V", tol="10%")
            B.R("562R", s, i)
        u = B.part("ISO1212", p)
        if fgnd == "DIF_GND":
            iso.add(u.ref)          # barrier DI field <-> PELV; U703 (FB) is PELV on both sides
        B.C("100n", "3V3", "GND")
        for n in (a, b):
            if n in chain_out:
                chan(B, u, chain_out[n][1])
                B.R("1k", chain_out[n][0], "DI%d" % n, note="CTRL input cannot back-drive the chain")


def check_di_grounds(B):
    """No ISO1212 package may mix a DIF_GND channel with a GND channel (channel-to-channel = functional only)."""
    for p in B.D.parts.values():
        if p.lib_id.endswith(":ISO1212"):
            assert p.pins["14"] == p.pins["9"], ("ISO1212 mixes field grounds", p.ref)
            for sp in ("16", "11"):
                assert domain_of(p.pins[sp]) == domain_of(p.pins["14"]), ("field net vs FGND domain", p.ref, sp)
    return "4 ISO1212: U701/U702/U704 field on DIF_GND, U703 (FB_A/B_MAIN) on GND; no package mixes domains"


# ======================================================================================================== 08
TRUTH_A = (
    "CHANNEL A (1 = healthy). {a1}: FIELD_A_OK = ESTOP_A_OK & UV_OK & TEST_A_N;  {ra}: RST_A = Schmitt(SYS_RST_N)\n"
    "{a2}: NO_TRIP_A = FIELD_A_OK & TRIP_OK & RST_A     {a3} D-FF: CLR = NO_TRIP_A (dominant), D = 1, CLK = FLT_CLR\n"
    "{a4}: GATE_EN = LATCH_A_OK & NO_TRIP_A -> CTRL EN pairs     {a5}: DO_PWR_EN = LATCH_A_OK & NO_TRIP_A -> 24V_DO switch A\n"
    "  any A input 0            -> NO_TRIP_A 0 -> Q_A 0 at once -> GATE_EN 0, switch A off, FLT_LATCH_N 0\n"
    "  inputs back, FLT_CLR held -> stays latched (no automatic restart)\n"
    "  all inputs 1, FLT_CLR rise -> Q_A 1 -> GATE_EN 1, switch A on;  input still 0 at the edge -> stays tripped\n"
    "  power-up: TPS3850 holds SYS_RST_N low 200 ms -> both latches start tripped\n"
    "DO_PWR_EN has 10k to GND: an unpowered gate or a dead 3V3 leaves switch A OFF (its SHDN pull-up is <= 10 uA).")
TRUTH_B = (
    "CHANNEL B, separate parts. {rb}: RST_B = Schmitt(SYS_RST_N);  {b1}: NO_TRIP_B = ESTOP_B_OK & RST_B & TEST_B_N\n"
    "{b2} D-FF: CLR = NO_TRIP_B (dominant), D = 1, CLK = FLT_CLR     {b3}: CHB_OK = LATCH_B_OK & NO_TRIP_B\n"
    "CHB_OK -> +24V_GD switch (gate-drive bias), 24V_DO switch B, and via {b4} to CTRL (ANDed into every EN pair).\n"
    "  any B input 0 -> Q_B 0 -> +24V_GD off, switch B off, CHB_OK 0;  re-arm only by a FLT_CLR edge with B healthy\n"
    "24V_DO = switch A AND switch B in series. Gate off = GATE_EN 0 (A) OR +24V_GD off (B).\n"
    "CHB_OK_L has 10k to GND (two SHDN pull-ups x 10 uA -> 0.2 V < 0.8 V): unpowered driver or dead 3V3 = OFF.")
SERVICE = (
    "{j} service jumper (programming / debug only; no shunt fitted in service). Fitted: SVC_N = 0 ->\n"
    "SET1 = 0 and, through Q, SET0 = 1 -> TPS3850 watchdog DISABLED (table 6.6); BAT54C pulls TEST_A_N and\n"
    "TEST_B_N low -> both channels held tripped: GATE_EN, CHB_OK, FLT_LATCH_N = 0, 24V_DO and +24V_GD off. The\n"
    "module cannot look healthy or run power; CTRL (+24V) stays powered for programming. SVC_N -> expander 2.\n"
    "FIRMWARE: first WDI falling edge <= 150 ms after EVERY XRSn release (tWDU min 165.8 ms), then every\n"
    "10.35-165.8 ms (closed window 10.35 ms max). Report service mode (SVC_RB = 0) and refuse FLT_CLR.")


def sheet_chain(B):
    B.new_sheet("08_chain", "Dual-channel safety chain and watchdog",
                "A: E-stop A, 24 V UV, spare loop, watchdog -> latch A -> GATE_EN, 24V_DO switch A\n"
                "B: E-stop B, watchdog -> latch B -> CHB_OK -> +24V_GD, 24V_DO switch B")
    B.block("Channel A input: 24 V under-voltage", "TPS3710, 0.1 % divider: 18.2-19.2 V falling (18.7 nom), 19.0 rising\n"
                                                   "(HVC43 / AEV250 coils need >= 18 V / 9 V pick-up). Readback -> expander 2.\n"
                                                   "Self-test: UV_TEST (expander 2) shorts UV_SNS -> detector must trip.")
    chan(B, B.part("TPS3710", {"5": "3V3", "3": "UV_SNS", "2": "GND", "4": "GND", "6": "GND", "1": "UV_OK"}), "A")
    B.R("100k", "24V_OR", "UV_SNS", tol="0.1%", note="10 ppm/K thin film")
    B.R("2.15k", "UV_SNS", "GND", tol="0.1%", note="10 ppm/K thin film")
    B.C("100n", "UV_SNS", "GND", tol="10%")
    B.R("10k", "UV_OK", "3V3")
    B.C("100n", "3V3", "GND")
    B.part("2N7002BK", {"1": "UV_TEST", "3": "UV_SNS", "2": "GND"})
    B.R("100k", "UV_TEST", "GND", note="test off by default")
    B.block("Window watchdog and 3.3 V supervisor (both channels)",
            "TPS3850G33: 3V3 window +/-4 %, tRST 200 ms (CRST open). WDI falling edge every 10.35-165.8 ms\n"
            "(CWD 10k, SET0 0, SET1 1), else WDO pulses SYS_RST_N low 200 ms. Wired-AND with CTRL XRSn\n"
            "(CTRL 10k + 10 nF; 100k here keeps the node defined without CTRL). SET0 / SET1 from the service jumper.")
    chan(B, B.part("TPS3850G33", {"1": "3V3", "10": "3V3", "7": "WDI", "3": "WD_SET0", "6": "SVC_N", "2": "WD_CWD",
                                  "4": None, "5": "GND", "11": "GND", "9": "SYS_RST_N", "8": "SYS_RST_N"}), "AB")
    B.R("10k", "WD_CWD", "3V3")
    B.R("100k", "SYS_RST_N", "3V3")
    B.R("100k", "WDI", "GND")
    B.C("100n", "3V3", "GND")
    B.block("Channel A input: spare trip loop J801",
            "NC contacts in series between 1 and 2 (DAB: coolant-flow switch + cold-plate thermostat), wetted from\n"
            "24V_OR through 2.2k into an ISO1211 type 1/3 input on GND: 2.05-2.75 mA (current-limited), +/-60 V\n"
            "tolerant, open = trip; short to GND = 11 mA. Fit a wire link when unused. TRIP_TEST shorts the input.")
    B.part("J_MC4", {"1": "TRIP_SRC", "2": "TRIP_FLD", "3": "GND", "4": "GND"})
    B.R("2.2k", "24V_OR", "TRIP_SRC", pkg="2010", note="0.75 W")
    B.R("1k", "TRIP_FLD", "TRIP_S", pkg="MELF 0204", note="0.25 W RTHR")
    B.C("10n", "TRIP_S", "GND", volt="100V", tol="10%")
    B.R("562R", "TRIP_S", "TRIP_I")
    chan(B, B.part("ISO1211", {"1": "3V3", "2": "3V3", "3": "TRIP_OK", "4": "GND", "8": "TRIP_S", "7": "TRIP_I",
                               "6": "GND", "5": None}), "A")
    B.C("100n", "3V3", "GND")
    B.part("2N7002BK", {"1": "TRIP_TEST", "3": "TRIP_S", "2": "GND"})
    B.R("100k", "TRIP_TEST", "GND", note="test off by default")
    B.block("Service jumper: watchdog off, chain held tripped")
    B.blk.note = SERVICE.format(j=B.part("J_SVC", {"1": "SVC_N", "2": "GND"}).ref)
    B.R("10k", "SVC_N", "3V3")
    B.R("10k", "WD_SET0", "3V3")
    B.part("2N7002BK", {"1": "SVC_N", "3": "WD_SET0", "2": "GND"})
    B.part("BAT54C", {"1": "TEST_A_N", "2": "TEST_B_N", "3": "SVC_N"})
    B.TP("SVC_N")
    B.block("Both channels: AUX-HV source inhibit (D-022: AUX-HV alone never runs power)", "")
    B.aux_inh_block = B.blk
    B.part("TPS3700", {"5": "3V3", "3": "GND", "4": "AUX_SNS", "2": "GND", "1": None, "6": "AUX_INH_N"})
    B.C("100n", "3V3", "GND")
    B.R("100k", "AUX_EN", "AUX_SNS")
    B.R("200k", "AUX_SNS", "GND")
    B.R("200k", "AUX_TEST", "AUX_SNS")
    B.R("100k", "AUX_TEST", "GND", note="test off by default")
    B.R("10k", "AUX_INH_N", "3V3")
    B.part("BAT54C", {"1": "TEST_A_N", "2": "TEST_B_N", "3": "AUX_INH_N"})
    B.TP("AUX_INH_N")
    B.block("Channel A latch")
    ra = dict(
        ra=chan(B, B.part("SN74LVC1G17", {"2": "SYS_RST_N", "1": None, "3": "GND", "5": "3V3", "4": "RST_A"}),
                "A").ref,
        a1=chan(B, B.part("SN74LVC1G11", {"1": "ESTOP_A_OK", "3": "UV_OK", "6": "TEST_A_N", "2": "GND", "5": "3V3",
                                          "4": "FIELD_A_OK"}), "A").ref,
        a2=chan(B, B.part("SN74LVC1G11", {"1": "FIELD_A_OK", "3": "TRIP_OK", "6": "RST_A", "2": "GND",
                                          "5": "3V3", "4": "NO_TRIP_A"}), "A").ref,
        a3=chan(B, B.part("SN74LVC1G74", {"2": "3V3", "1": "FLT_CLR", "7": "3V3", "6": "NO_TRIP_A", "4": "GND",
                                          "8": "3V3", "5": "LATCH_A_OK", "3": "LATCH_A_TRIP"}), "A").ref,
        a4=chan(B, B.part("SN74LVC1G08", {"1": "LATCH_A_OK", "2": "NO_TRIP_A", "3": "GND", "5": "3V3",
                                          "4": "GATE_EN_L"}), "A").ref,
        a5=chan(B, B.part("SN74LVC1G08", {"1": "LATCH_A_OK", "2": "NO_TRIP_A", "3": "GND", "5": "3V3",
                                          "4": "DO_PWR_EN"}), "A").ref)
    B.blk.note = TRUTH_A.format(**ra)
    B.R("100k", "FLT_CLR", "GND")
    B.R("100R", "LATCH_A_OK", "FLT_LATCH_N")
    B.R("100R", "GATE_EN_L", "GATE_EN")
    B.R("100k", "GATE_EN", "GND", note="CTRL expects the pull-down on SYS")
    B.R("10k", "DO_PWR_EN", "GND", note="switch A off when the driver is unpowered")
    dec(B, "3V3", n=6)
    for net in ("ESTOP_A_OK", "UV_OK", "TRIP_OK", "SYS_RST_N", "FIELD_A_OK", "NO_TRIP_A", "LATCH_A_OK",
                "GATE_EN_L", "DO_PWR_EN"):
        B.TP(net)
    B.block("Channel B latch")
    rb = dict(
        rb=chan(B, B.part("SN74LVC1G17", {"2": "SYS_RST_N", "1": None, "3": "GND", "5": "3V3", "4": "RST_B"}),
                "B").ref,
        b1=chan(B, B.part("SN74LVC1G11", {"1": "ESTOP_B_OK", "3": "RST_B", "6": "TEST_B_N", "2": "GND",
                                          "5": "3V3", "4": "NO_TRIP_B"}), "B").ref,
        b2=chan(B, B.part("SN74LVC1G74", {"2": "3V3", "1": "FLT_CLR", "7": "3V3", "6": "NO_TRIP_B", "4": "GND",
                                          "8": "3V3", "5": "LATCH_B_OK", "3": "LATCH_B_TRIP"}), "B").ref,
        b3=chan(B, B.part("SN74LVC1G08", {"1": "LATCH_B_OK", "2": "NO_TRIP_B", "3": "GND", "5": "3V3",
                                          "4": "CHB_OK_L"}), "B").ref,
        b4=chan(B, B.part("SN74LVC1G17", {"2": "CHB_OK_L", "1": None, "3": "GND", "5": "3V3", "4": "CHB_OK_B"}),
                "B").ref)
    B.blk.note = TRUTH_B.format(**rb)
    B.R("100R", "CHB_OK_B", "CHB_OK")
    B.R("100k", "CHB_OK", "GND", note="CTRL expects the pull-down on SYS")
    B.R("10k", "CHB_OK_L", "GND", note="GD switch and switch B off when the driver is unpowered")
    dec(B, "3V3", n=5)
    for net in ("ESTOP_B_OK", "NO_TRIP_B", "LATCH_B_OK", "CHB_OK_L"):
        B.TP(net)
    B.block("E-stop status, LEDs and single-fault analysis")
    B.chain_block = B.blk
    B.R("10k", "ESTOP_A_OK", "ESTOP_A_S")
    B.R("10k", "ESTOP_B_OK", "ESTOP_B_S")
    chan(B, B.part("SN74LVC1G08", {"1": "ESTOP_A_S", "2": "ESTOP_B_S", "3": "GND", "5": "3V3", "4": "ESTOP_N_B"}),
         "status")
    B.R("100R", "ESTOP_N_B", "ESTOP_N")
    B.C("100n", "3V3", "GND")
    for net, led in (("GATE_EN_L", "LED_G"), ("CHB_OK_L", "LED_G"), ("LATCH_A_TRIP", "LED_R"),
                     ("LATCH_B_TRIP", "LED_R")):
        a = "LED_%s_A" % net
        B.R("470R", net, a)
        B.part(led, {"2": a, "1": "GND"})


# ======================================================================================================== 09
# Per-output limits (TPS272C45 SLVSF24C table 7.5 rows; RON 78 mOhm max at 125 C) - printed and put on sheet 09.
DO_CFG = {  # n: (R_ILIM or None = ILIM to GND, ICL min, ICL max, continuous, inrush A, inrush s)
    1: ("10k", 1.58, 2.30, 1.5, 1.5, None), 2: ("10k", 1.58, 2.30, 1.5, 1.5, None),
    3: ("10k", 1.58, 2.30, 1.5, 1.5, None), 4: ("10k", 1.58, 2.30, 1.5, 1.5, None),
    5: ("10k", 1.58, 2.30, 1.5, 1.5, None), 6: ("28.7k", 0.52, 0.82, 0.5, 0.5, None),
    7: ("28.7k", 0.52, 0.82, 0.5, 0.5, None), 8: ("10k", 1.58, 2.30, 1.5, 1.5, None)}
CLAMP_J, CLAMP_W = 0.5, 0.5     # SMAJ12A at 85 C: ~150 W x 52 % for 10 ms ~ 0.78 J -> 0.5 J; 1 W steady -> 0.5 W
# Output at coil turn-off: VS max = OV cut-off max; VOUT min = -(SMAJ12A VBR max 14.7 V + SMAJ36A forward <= 1.0 V)
VS_MAX, CLAMP_VOUT, VSOUT_ABS = 26.417, -(14.7 + 1.0), 48.0           # TPS272C45 SLVSF24C p7: VS - VOUT <= 48 V
COIL_V = VS_MAX - CLAMP_VOUT


def check_coil_turnoff(ov_max):
    v = ov_max - CLAMP_VOUT
    assert v <= VSOUT_ABS, ("TPS272C45 VS - VOUT at turn-off", v)
    return v


# Worst-case steady load per output, A (sim/out/port_design/report.md rev 4, 6.7 and 7.2). A battery-type port
# (DAB: both ports; PV-PORT: port B) drives TWO HVC43 coils in parallel on K_x_MAIN: <= 0.60 A (two cold 96 ohm
# coils at 26.45 V + gate divider). HVC43 coils are not economised (24 V, 96 ohm, gen/port.py): pull-in current =
# hold current, the L/R rise has no overshoot. G7L-X 0.125 A, 2 x TPSI3050 27 mA start each, IMD 3.4 mA, proof 0.5 mA.
DO_LOAD = {1: 0.125, 2: 0.60, 3: 0.125, 4: 0.60, 5: 2 * 0.027, 6: 0.0034, 7: 0.0034, 8: 0.0005}
COIL_E = 0.089                  # J into the SYS clamp per HVC43 coil, only after a shorted low side (port report 7.1)
DOS_ILIM = (5.58, 6.42)         # 24V_DO switches A and B, TPS16630 ILIM 3.01k (sheet 09)


def check_do_loads(B):
    """Rev E (port board rev 4): every output limit >= 1.25 x its worst load, both main-contactor outputs carry a
    coil pair, the 24V_DO sum stays below the switch limit with all outputs on and with any one output shorted,
    every TPS272C45 sits behind both series switches (24V_DO), a pair through the SYS clamp <= its energy rating."""
    for n, i in DO_LOAD.items():
        assert DO_CFG[n][1] >= 1.25 * i and DO_CFG[n][3] >= i, ("DO limit below its load", n, i, DO_CFG[n])
    tot = sum(DO_LOAD.values())
    short = max(tot - DO_LOAD[n] + DO_CFG[n][2] for n in DO_LOAD)
    assert tot <= 5.5 and short < DOS_ILIM[0], ("24V_DO branch", tot, short)
    vs = {p.pins["8"] for p in B.D.parts.values() if p.lib_id.endswith(":TPS272C45D")}
    assert vs == {"24V_DO"} and 2 * COIL_E <= CLAMP_J, ("two-switch coil rule / clamp energy", vs)
    return tot, short


def do_table():
    rows = []
    for n in range(1, 9):
        r, lo, hi, cont, ia, it = DO_CFG[n]
        rows.append(dict(DO="DO%d" % n, function=IF.DO[n - 1], R_ILIM=r or "GND", I_limit="%.2f-%.2f A" % (lo, hi),
                         continuous="%.1f A" % cont,
                         inrush="%.1f A x %s" % (ia, "%.1f s" % it if it else "unlimited (<= continuous)"),
                         clamp="%.1f J per turn-off, %.1f W avg, OUT -14.1..-15.7 V" % (CLAMP_J, CLAMP_W)))
    return rows


def sheet_do(B):
    B.new_sheet("09_do", "Gated 24 V outputs (contactors, relays)",
                "DO1..DO8 per interfaces.DO on 4 x TPS272C45D behind two series supply switches:\n"
                "switch A (channel A, DO_PWR_EN) and switch B (channel B, CHB_OK); TVS coil clamps")
    B.block("Output supply 24V_DO: two series switches, one per safety channel",
            "TPS16630 A (SHDN = DO_PWR_EN) -> 24V_DO_MID -> TPS16630 B (SHDN = CHB_OK_L) -> 24V_DO. ILIM 3.01k ->\n"
            "5.98 A (5.58-6.42 A) each. All eight outputs on: <= %.2f A (two HVC43 coil pairs, not economised);\n"
            "one output shorted: <= %.2f A - the other outputs keep running (check_do_loads). Rails read back via\n"
            "expander 2. While AUX-HV is the source both switches are held off (sheet 08)."
            % (sum(DO_LOAD.values()), max(sum(DO_LOAD.values()) - DO_LOAD[n] + DO_CFG[n][2] for n in DO_LOAD)))
    chan(B, efuse(B, "24V_OR", "24V_DO_MID", "DO_PWR_EN", "3.01k", "DOSA_FLT_N", tag="DOSA"), "A")
    B.C("10u", "24V_DO_MID", "GND", pkg="1210", volt="50V", tol="10%")
    chan(B, efuse(B, "24V_DO_MID", "24V_DO", "CHB_OK_L", "3.01k", "DOSB_FLT_N", tag="DOSB"), "B")
    for _ in range(2):
        B.C("10u", "24V_DO", "GND", pkg="1210", volt="50V", tol="10%")
    B.TP("24V_DO_MID")
    B.TP("24V_DO")
    B.block("Command gating", "DOn_EN = DOn & GATE_EN (SN74LVC08A). 100k pull-downs: outputs off while CTRL is\n"
                              "absent or in reset. K_DISCH (DO5) is held on only while both channels are healthy.")
    for q in range(2):
        g = {}
        for j, (ia, ib, y) in enumerate((("1", "2", "3"), ("4", "5", "6"), ("9", "10", "8"), ("12", "13", "11"))):
            n = 4 * q + j + 1
            g.update({ia: "DO%d" % n, ib: "GATE_EN_L", y: "DO%d_EN" % n})
        g.update({"7": "GND", "14": "3V3"})
        chan(B, B.part("SN74LVC08A", g), "A")
        B.C("100n", "3V3", "GND")
    for n in range(1, 9):
        B.R("100k", "DO%d" % n, "GND")
    cap = "\n".join("%-3s %-9s ILIM %-5s %-11s cont %-6s inrush %s" % (r["DO"], r["function"], r["R_ILIM"],
                                                                     r["I_limit"], r["continuous"], r["inrush"])
                    for r in do_table())
    titles = {1: "Port A precharge + main contactor", 3: "Port B precharge + main contactor",
              5: "Discharge relay + IMD switch P", 7: "IMD switch N + spare"}
    for a in (1, 3, 5, 7):
        b = a + 1
        note = ("%s. FLT1/2 -> expander 1. Clamp SMAJ36A (blocks +36 V) + SMAJ12A: OUT >= -15.7 V." % titles[a])
        if a in (1, 3):
            note += ("\nPort board (interfaces.DO): the K_x_MAIN line feeds the coil AND gates its low-side switch; the\n"
                     "coil's >= 50 V varistor is its only flyback path. At turn-off the gate falls with this output, the\n"
                     "coil current circulates in coil + varistor and this pin only pins the loop: VS - VOUT <= %.1f V\n"
                     "< 48 V abs max (check_coil_turnoff). Battery-type port (DAB both, PV-PORT B): TWO HVC43 coils\n"
                     "in parallel, <= 0.60 A, not economised (pull-in = hold) -> ILIM 10k = 1.58-2.30 A (check_do_loads)."
                     % COIL_V)
        if a == 7:
            note += ("\nPER-OUTPUT CAPABILITY (85 C ambient; calculated, not tested). Clamp energy every output: "
                     "%.1f J per\nturn-off, %.1f W average (SMAJ12A 400 W/1 ms, ~150 W/10 ms, derated to 85 C).\n" %
                     (CLAMP_J, CLAMP_W) + cap)
        B.block("DO%d %s, DO%d %s" % (a, IF.DO[a - 1], b, IF.DO[b - 1]), note)
        p = {"8": "24V_DO", "9": "24V_DO", "23": "24V_DO", "24": "24V_DO", "19": "GND", "16": "DO%d_EN" % a,
             "15": "DO%d_EN" % b, "12": "3V3", "13": "GND", "14": "GND", "17": "GND", "25": "GND", "4": None,
             "22": None, "10": "DO%d_FLT_N" % a, "18": "DO%d_FLT_N" % b, "11": "DO%d_SNS" % a}
        for n, outs, ilim in ((a, ("1", "2", "3"), "21"), (b, ("5", "6", "7"), "20")):
            out = IF.DO[n - 1]
            for o in outs:
                p[o] = out
            r = DO_CFG[n][0]
            if r:
                p[ilim] = "DO%d_ILIM" % n
                B.R(r, "DO%d_ILIM" % n, "GND")
            else:
                p[ilim] = "GND"        # internal limit 4.3-6.95 A (not used at present)
            B.part("SMAJ36A", {"1": out, "2": "DO%d_FW" % n})
            B.part("SMAJ12A", {"2": "DO%d_FW" % n, "1": "GND"})
        B.part("TPS272C45D", p)
        B.R("1k", "DO%d_SNS" % a, "GND", note="SNS unused (datasheet table 6-2)")
        B.C("100n", "24V_DO", "GND", volt="50V")
        B.part("J_MC4", {"1": IF.DO[a - 1], "2": "GND", "3": IF.DO[b - 1], "4": "GND"})


# ======================================================================================================== 10
def sheet_fans(B):
    B.new_sheet("10_fans", "Fan channels",
                "4 x TPS16630 fan supply (3.6 A limit, auto-retry, OV cut-off), open-drain PWM,\n"
                "clamped tach; sized for PFC1224DE-F00 (2.0 A typ, 2.4 A max, 26.5 V max)")
    for n in range(1, 5):
        v, g, t, d = "24V_FAN%d" % n, "FAN%d_G" % n, "FAN%d_TACHI" % n, "FAN%d_PWMO" % n
        B.block("Fan %d" % n, ("Terminal: 1 +24 V, 2 0 V, 3 tach (blue), 4 PWM (yellow). ILIM 4.99k -> 3.6 A.\n"
                               "FAN_ON = FAN_EN (expander 1 GPB4, 100k pull-down) AND AUX_INH_N: fans are OFF until\n"
                               "firmware sets FAN_EN and whenever AUX-HV is the source (no firmware in that path);\n"
                               "4.7k on FAN_ON keeps all four off if the gate is unpowered.\n"
                               "PWM inverted: FAN_PWM high = fan input low; once powered, PWM open = full speed.\n"
                               "Tach: open collector, 4.7k to 3V3 (0.7 mA < 5 mA), 4.7k + BAT54S clamp.")
                if n == 1 else "")
        efuse(B, "24V_OR", v, "FAN_ON", "4.99k", "FAN%d_FLT_N" % n, tag="FAN%d" % n)
        B.C("10u", v, "GND", pkg="1210", volt="50V", tol="10%")
        B.part("SMAJ33A", {"1": v, "2": "GND"})
        B.R("100R", "FAN_PWM%d" % n, g)
        B.R("100k", g, "GND")
        B.part("2N7002BK", {"1": g, "3": d, "2": "GND"})
        B.R("4.7k", "3V3", t)
        B.R("4.7k", t, "FAN_TACH%d" % n)
        B.part("BAT54S", {"1": "GND", "2": "3V3", "3": "FAN_TACH%d" % n})
        B.C("1n", "FAN_TACH%d" % n, "GND", tol="10%")
        B.part("J_MC4", {"1": v, "2": "GND", "3": t, "4": d})
        if n == 1:
            chan(B, B.part("SN74LVC1G08", {"1": "FAN_EN", "2": "AUX_INH_N", "3": "GND", "5": "3V3", "4": "FAN_ON"}),
                 "fan")
            B.C("100n", "3V3", "GND")
            B.R("100k", "FAN_EN", "GND", note="fans OFF until firmware enables (INT-09)")
            B.R("4.7k", "FAN_ON", "GND", note="fan supplies off when the gate is unpowered")


# ======================================================================================================== 11
NTC_NOTE = ("NTC: B57703M0103 (10k, B 3988). VREF 2.5 V -> 10k 0.1 % -> NTC; 1k + 100n -> ADC (ratiometric).\n"
            "Codes (12 bit): -40 C 3978, 25 C 2048, 125 C 135, 150 C 74. Open > 4040, short < 40:\n"
            "both are outside the -40..150 C band, so a broken or shorted sensor is distinguishable.")


def sheet_ntc(B):
    B.new_sheet("11_ntc", "NTC inputs, ADC and supply monitors",
                "ADS7953 16-ch SAR on SPI_CS_ADC_N, REF3025 2.5 V: CH0-11 = NTC1-12,\n"
                "CH12-14 = feed voltages, CH15 = +24V_GD gate-drive bias current (eFuse IMON)")
    B.block("ADC and reference", "ADS7953: +VA 5 V, +VBD 3.3 V, range 1 (0..VREF, GPIO2 pulled low), MXO -> AINP.\n"
                                 "GPIO0/1/3 inputs by default, pulled low. SDO tri-states when CS is high.")
    ch = {"5": "5V", "29": "5V", "4": "VREF", "3": "GND", "36": "3V3", "31": "SPI_CS_ADC_N", "32": "SPI_CLK",
          "33": "SPI_SIMO", "34": "SPI_SOMI", "7": "ADC_MX", "8": "ADC_MX", "9": "GND", "37": "ADC_GPIO",
          "38": "ADC_GPIO", "1": "ADC_RANGE", "2": "ADC_GPIO", "6": "GND", "10": "GND", "19": "GND", "20": "GND",
          "30": "GND", "35": "GND"}
    for k, p in enumerate(["28", "27", "26", "25", "24", "23", "22", "21", "18", "17", "16", "15", "14", "13", "12",
                           "11"]):
        ch[p] = "ADC_CH%d" % k
    B.part("ADS7953", ch)
    B.C("10u", "5V", "GND", pkg="0805", volt="16V")
    dec(B, "5V", "5V", "3V3")
    B.R("10k", "SPI_CS_ADC_N", "3V3")
    B.R("100k", "ADC_GPIO", "GND")
    B.R("10k", "ADC_RANGE", "GND")
    B.part("REF3025", {"1": "5V", "3": "GND", "2": "VREF"})
    B.C("1u", "5V", "GND", volt="16V")
    B.C("10u", "VREF", "GND", pkg="0805", volt="16V")
    B.C("100n", "VREF", "GND")
    B.TP("VREF")
    for title, rng, key in (("NTC1-NTC5", range(1, 6), "J_MC10"), ("NTC6-NTC10", range(6, 11), "J_MC10"),
                            ("NTC11-NTC12", range(11, 13), "J_MC4")):
        B.block(title, NTC_NOTE if rng[0] == 1 else "Terminal pairs: odd = NTC, even = 0 V return.")
        pins = {}
        for j, n in enumerate(rng):
            node = "NTC%d" % n
            pins[str(2 * j + 1)], pins[str(2 * j + 2)] = node, "GND"
            B.R("10k", "VREF", node, tol="0.1%", note="25 ppm")
            B.R("1k", node, "ADC_CH%d" % (n - 1))
            B.C("100n", "ADC_CH%d" % (n - 1), "GND")
        B.part(key, pins)
    B.block("Supply monitors", "Feeds: 121k / 10k -> full scale 32.7 V (CH12 feed 1, CH13 feed 2, CH14 AUX-HV).\n"
                               "CH15: IMON_GD 27.9 uA/A x 14.7k -> 6 A = 2.46 V (gate-drive bias of all power boards).")
    for k, f in ((12, "24V_F1"), (13, "24V_F2"), (14, "24V_F3")):
        B.R("121k", f, "ADC_CH%d" % k)
        B.R("10k", "ADC_CH%d" % k, "GND")
        B.C("100n", "ADC_CH%d" % k, "GND")
    B.R("1k", "IMON_GD", "ADC_CH15")
    B.R("14.7k", "IMON_GD", "GND")
    B.C("100n", "ADC_CH15", "GND")


# ======================================================================================================== 12
EXP1 = ["DO%d_FLT_N" % n for n in range(1, 9)] + ["FAN1_FLT_N", "FAN2_FLT_N", "FAN3_FLT_N", "FAN4_FLT_N", "FAN_EN",
                                                  "TMP_ALERT_N", "OV_RB", "P24_FLT_N"]
EXP2 = ["DOSA_FLT_N", "DOSB_FLT_N", "GD_FLT_N", "DO_MID_SNS", "DO_SNS", "GD_SNS", "TEST_A_N", "TEST_B_N",
        "UV_RB", "TRIP_RB", "UV_TEST", "TRIP_TEST", "SVC_RB", "AUX_INH_RB", "AUX_TEST", None]


def expander(B, addr, nets):
    p = {"9": "3V3", "11": "SPI_CS_IO_N", "12": "SPI_CLK", "13": "SPI_SIMO", "14": "SPI_SOMI", "18": "SYS_RST_N",
         "15": "3V3" if addr & 1 else "GND", "16": "GND", "17": "GND", "20": None, "19": None, "10": "GND"}
    for n, net in enumerate(nets):
        p[str(21 + n) if n < 8 else str(n - 7)] = net
    B.part("MCP23S17", p)
    B.C("100n", "3V3", "GND")


def sheet_service(B):
    B.new_sheet("12_service", "Service storage and diagnostics",
                "I2C: FM24CL64B F-RAM 0x50 (serial, calibration, fault log, hours), TMP1075 0x48;\n"
                "SPI_CS_IO_N: two MCP23S17 (HAEN, A0 0/1): faults, rail sense, channel self-test")
    B.block("Service F-RAM", "64 kbit F-RAM, 1e14 write cycles: an hours counter written every minute for\n"
                             "20 years is ~1e7 writes. WP low (writable). I2C pull-ups and the 0x57 EEPROM are on CTRL.")
    B.part("FM24CL64B", {"8": "3V3", "6": "I2C_SCL", "5": "I2C_SDA", "7": "GND", "4": "GND", "1": "GND", "2": "GND",
                         "3": "GND"})
    B.C("100n", "3V3", "GND")
    B.block("Board temperature", "TMP1075 at 0x48 (no clash with 0x50 / 0x57); ALERT -> expander 1 GPB5.")
    B.part("TMP1075", {"8": "3V3", "2": "I2C_SCL", "1": "I2C_SDA", "3": "TMP_ALERT_N", "4": "GND", "7": "GND",
                       "6": "GND", "5": "GND"})
    B.C("100n", "3V3", "GND")
    B.R("10k", "TMP_ALERT_N", "3V3")
    B.block("Expander 1 (address 0): output-driver and fan faults",
            "GPA0-7 = DO1-8 FLT (TPS272C45D), GPB0-3 = fan eFuse FLT, GPB4 = FAN_EN out, GPB5 = TMP ALERT,\n"
            "GPB6 = OV_CUT readback, GPB7 = +24V eFuse FLT. Inputs: enable GPPU pull-ups. Reset = SYS_RST_N.\n"
            "MCP23S17 at 3.3 V is rated -40..+85 C (DS20001952C p1; +125 C only at 4.5-5.5 V): enough for PV-20.")
    expander(B, 0, EXP1)
    B.R("10k", "SPI_CS_IO_N", "3V3")
    B.R("100k", "OV_CUT", "OV_RB", note="readback cannot hold OV_CUT")
    B.block("Expander 2 (address 1): chain self-test and switch readback",
            "GPA0-2 = DOS A / DOS B / GD eFuse FLT, GPA3-5 = 24V_DO_MID / 24V_DO / +24V_GD present (100k/15k:\n"
            "21.6 V -> 2.82 V; surge 3.93 V, clamp < 0.1 mA), GPA6/7 = TEST_A_N / TEST_B_N outputs (10k pull-ups: idle healthy;\n"
            "driving low can only trip a channel), GPB0/1 = UV_OK / TRIP_OK readback via 100k, GPB2/3 = UV_TEST /\n"
            "TRIP_TEST outputs (100k pull-downs: idle off; high forces that detector input into its trip state),\n"
            "GPB4 = SVC_RB (service jumper fitted = 0), GPB5 = AUX_INH_RB (0 = AUX-HV is the source, power outputs\n"
            "inhibited), GPB6 = AUX_TEST output (100k pull-down; high forces the inhibit), GPB7 spare (enable GPPU).")
    expander(B, 1, EXP2)
    for rail, sns in (("24V_DO_MID", "DO_MID_SNS"), ("24V_DO", "DO_SNS"), ("+24V_GD", "GD_SNS")):
        B.R("100k", rail, sns)
        B.R("15k", sns, "GND")
        B.C("10n", sns, "GND")
    B.R("10k", "TEST_A_N", "3V3")
    B.R("10k", "TEST_B_N", "3V3")
    B.R("100k", "UV_OK", "UV_RB", note="readback cannot hold UV_OK")
    B.R("100k", "TRIP_OK", "TRIP_RB")
    B.R("100k", "SVC_N", "SVC_RB", note="service-jumper readback")
    B.R("100k", "AUX_INH_N", "AUX_INH_RB", note="readback cannot hold AUX_INH_N")


# ======================================================================================================== chain check
LOGIC = {"SN74LVC1G08": [(("1", "2"), "4")], "SN74LVC1G11": [(("1", "3", "6"), "4")], "SN74LVC1G17": [(("2",), "4")],
         "SN74LVC08A": [(("1", "2"), "3"), (("4", "5"), "6"), (("9", "10"), "8"), (("12", "13"), "11")]}
ALIAS = {"ESTOP_A_S": "ESTOP_A_OK", "ESTOP_B_S": "ESTOP_B_OK"}      # 10k series resistors into the status gate
INPUTS = ("ESTOP_A_OK", "UV_OK", "TRIP_IN", "TEST_A_N", "ESTOP_B_OK", "TEST_B_N", "SYS_RST_N", "AUX_EN")
OBS = ("GATE_EN_L", "CHB_OK_B", "LATCH_A_OK", "ESTOP_A_OK", "ESTOP_B_OK", "ESTOP_N_B", "UV_OK", "TRIP_OK",
       "MID", "DO", "GD", "AUX_INH_N", "FANS")                     # what CTRL can read (direct, expander, tach)


class Chain:
    """Gate-level model built from the design table: AND / buffer gates, the two 74LVC1G74 latches, the BAT54C
    diodes that pull both TEST lines low, the three chain-switched TPS16630 and the four fan TPS16630 (on when
    ~SHDN is high) identified by their output net. AUX_EN = 1: the LM74800 EN/UVLO node is up (AUX-HV is the
    source); the sheet-08 TPS3700 then drives AUX_INH_N low (AUX_TEST injects the same condition)."""

    def __init__(self, B):
        self.gates, self.ffs, self.sw, self.fans, self.diodes = [], [], {}, [], []
        for p in B.D.parts.values():
            key = p.lib_id.split(":")[1]
            for ins, out in LOGIC.get(key, []):
                self.gates.append((p.ref, [p.pins[i] for i in ins], p.pins[out]))
            if key == "SN74LVC1G74":
                self.ffs.append(dict(ref=p.ref, d=p.pins["2"], clk=p.pins["1"], pre=p.pins["7"], clr=p.pins["6"],
                                     q=p.pins["5"], qn=p.pins["3"]))
            if key == "TPS16630":
                for role, net in (("A", "24V_DO_MID"), ("B", "24V_DO"), ("GD", "+24V_GD")):
                    if p.pins["18"] == net:
                        self.sw[role] = (p.ref, p.pins["13"])
                if p.pins["18"].startswith("24V_FAN"):
                    self.fans.append((p.ref, p.pins["13"]))
            if key == "BAT54C":
                self.diodes.append((p.pins["3"], [p.pins["1"], p.pins["2"]]))
        assert len(self.ffs) == 2 and len(self.sw) == 3, "two latches and three chain switches expected"
        assert len(self.fans) == 4 and len(self.diodes) == 2, "four fan switches and two BAT54C expected"

    def settle(self, inp, q, force):
        v = dict(inp, **{"3V3": 1, "GND": 0})
        # self-test MOSFETs: UV_TEST shorts the UV detector input, TRIP_TEST the spare-loop input (sheet 08)
        if force.get("UV_TEST", inp.get("UV_TEST", 0)):
            v["UV_OK"] = 0
        if force.get("TRIP_TEST", inp.get("TRIP_TEST", 0)):
            v["TRIP_IN"] = 0
        v["TRIP_OK"] = v["TRIP_IN"]                    # ISO1211 output = spare loop closed (field current flows)
        v["AUX_INH_N"] = int(not (force.get("AUX_EN", inp.get("AUX_EN", 0)) or force.get("AUX_TEST",
                                                                                     inp.get("AUX_TEST", 0))))
        for f in self.ffs:
            v[f["q"]], v[f["qn"]] = q[f["ref"]], 1 - q[f["ref"]]
        v.update(force)
        for k, anodes in self.diodes:                  # service jumper / AUX inhibit low -> both TEST lines low
            if v.get(k) == 0:
                v.update({a: 0 for a in anodes if a not in force})
        for _ in range(len(self.gates) + 2):
            for a, b in ALIAS.items():
                if b in v and a not in force:
                    v[a] = v[b]
            for ref, ins, out in self.gates:
                if out not in force and all(n in v for n in ins):
                    v[out] = int(all(v[n] for n in ins))
        on = {r: int(r in force.get("stuck_on", ()) or v.get(sh) == 1) for r, (ref, sh) in self.sw.items()}
        v.update(MID=on["A"], DO=on["A"] & on["B"], GD=on["GD"], SW_A=on["A"], SW_B=on["B"],
                 FANS=int(any(v.get(sh) == 1 for ref, sh in self.fans)))
        return v

    def step(self, inp, q, clk_prev, force):
        """Async clear dominates, then preset, then a rising FLT_CLR edge clocks D (SCES794G table 8-1)."""
        v = self.settle(inp, q, force)
        q = dict(q)
        for f in self.ffs:
            if v[f["clr"]] == 0:
                q[f["ref"]] = 0
            elif v[f["pre"]] == 0:
                q[f["ref"]] = 1
            elif clk_prev == 0 and v[f["clk"]] == 1:
                q[f["ref"]] = v[f["d"]]
        return q, self.settle(inp, q, force)

    def run(self, force, seq, q0):
        """Apply a sequence of (label, inputs); returns [(label, values)]."""
        q, clk, out = dict(q0), 0, []
        for label, inp in seq:
            q, v = self.step(inp, q, clk, force)
            clk = v["FLT_CLR"]
            out.append((label, v))
        return out


HEALTHY = dict({n: 1 for n in INPUTS}, FLT_CLR=0, UV_TEST=0, TRIP_TEST=0, SVC_N=1, AUX_EN=0, AUX_TEST=0, FAN_EN=1,
               **{"DO%d" % n: 1 for n in range(1, 9)})


def H(**kw):
    return dict(HEALTHY, **kw)


START = [("reset", H(SYS_RST_N=0)), ("reset released", H()), ("FLT_CLR", H(FLT_CLR=1)), ("running", H())]
SELFTEST = [("test A", H(TEST_A_N=0)), ("test A released", H()), ("re-arm", H(FLT_CLR=1)), ("running", H()),
            ("test B", H(TEST_B_N=0)), ("test B released", H()), ("re-arm", H(FLT_CLR=1)), ("running", H()),
            ("provoked watchdog", H(SYS_RST_N=0)), ("reboot", H()), ("re-arm", H(FLT_CLR=1)), ("running", H()),
            ("UV detector test", H(UV_TEST=1)), ("released", H()), ("re-arm", H(FLT_CLR=1)), ("running", H()),
            ("trip-loop test", H(TRIP_TEST=1)), ("released", H()), ("re-arm", H(FLT_CLR=1)), ("running", H()),
            ("AUX inhibit test", H(AUX_TEST=1)), ("released", H()), ("re-arm", H(FLT_CLR=1)), ("running", H())]
ESTOP = [("E-stop", H(ESTOP_A_OK=0, ESTOP_B_OK=0))]
WATCHDOG = [("watchdog", H(SYS_RST_N=0))]


def check_truth(C):
    """Exhaustive truth table of the drawn netlist: 2^8 trip-input combinations (incl. AUX-HV as the source) x 4
    power-up latch states; fans follow FAN_EN AND NOT AUX_EN without latching, the power branches stay latched."""
    for qa, qb in product((0, 1), repeat=2):
        q0 = {C.ffs[0]["ref"]: qa, C.ffs[1]["ref"]: qb}
        tr = C.run({}, START[:2], q0)
        v = tr[-1][1]
        assert v["GATE_EN_L"] == v["CHB_OK_L"] == v["SW_A"] == v["SW_B"] == v["GD"] == 0, "power-up must trip both"
        for bits in product((0, 1), repeat=len(INPUTS)):
            inp = dict(zip(INPUTS, bits))
            src = not inp["AUX_EN"]                              # AUX-HV not the source
            a_ok = inp["ESTOP_A_OK"] and inp["UV_OK"] and inp["TRIP_IN"] and inp["TEST_A_N"] and inp["SYS_RST_N"] \
                and src
            b_ok = inp["ESTOP_B_OK"] and inp["TEST_B_N"] and inp["SYS_RST_N"] and src
            seq = START + [("apply", H(**inp)), ("clear with inputs", H(FLT_CLR=1, **inp)), ("hold", H(**inp)),
                           ("restore", H()), ("re-arm", H(FLT_CLR=1)), ("final", H())]
            tr = dict(C.run({}, seq, q0))
            v = tr["apply"]
            assert (v["GATE_EN_L"], v["DO_PWR_EN"], v["SW_A"]) == (a_ok,) * 3, ("channel A trip", inp)
            assert (v["CHB_OK_L"], v["GD"], v["SW_B"], v["CHB_OK_B"]) == (b_ok,) * 4, ("channel B trip", inp)
            assert v["DO"] == (a_ok and b_ok) and v["ESTOP_N_B"] == (inp["ESTOP_A_OK"] and inp["ESTOP_B_OK"])
            for n in range(1, 9):
                assert v["DO%d_EN" % n] == a_ok, ("DO gating", n, inp)
            assert v["FANS"] == src and v["AUX_INH_N"] == src, ("fans / inhibit follow the source", inp)
            v = tr["hold"]                                       # clearing while a trip is active must not re-arm
            assert v["GATE_EN_L"] == a_ok and v["CHB_OK_L"] == b_ok, ("clear while tripped", inp)
            v = tr["restore"]                                    # trip gone, no new edge: still latched
            assert v["GATE_EN_L"] == a_ok and v["CHB_OK_L"] == b_ok, ("latching", inp)
            assert v["DO"] == (a_ok and b_ok) and v["GD"] == b_ok and v["FANS"] == 1, ("cabinet back", inp)
            v = tr["final"]
            assert v["GATE_EN_L"] == v["CHB_OK_L"] == v["DO"] == v["GD"] == 1, ("re-arm", inp)
        for t in ("UV_TEST", "TRIP_TEST"):                       # self-test MOSFETs trip channel A only
            v = C.run({}, START + [(t, H(**{t: 1}))], q0)[-1][1]
            assert v["GATE_EN_L"] == v["SW_A"] == 0 and v["CHB_OK_L"] == v["GD"] == 1, t
        v = C.run({}, START + [("AUX_TEST", H(AUX_TEST=1))], q0)[-1][1]          # inhibit test trips both, fans off
        assert v["GATE_EN_L"] == v["CHB_OK_L"] == v["DO"] == v["GD"] == v["FANS"] == v["AUX_INH_N"] == 0, "AUX_TEST"
        v = C.run({}, START + [("fans off", H(FAN_EN=0))], q0)[-1][1]
        assert v["FANS"] == 0 and v["DO"] == v["GD"] == 1, "FAN_EN"
    return len(C.gates)


DETECT = ("evident: module will not start", "latent: start-up self-test",
          "latent: E-stop proof test (DI1/DI8 or GATE_EN/CHB_OK disagree)", "latent: not detectable")
CMD_GATE = {1: "latent (operational, not E-stop): that output follows 24V_DO, command ignored - seen on contactor "
               "feedback / load current",
            0: "evident: that output cannot be energised (first command)"}
AUX_FAULT = {1: " (priority detector reads AUX active: outputs open)", 0: " (priority detector reads cabinet healthy)"}
AUX_AVAIL = ("no E-stop effect (availability): AUX-HV never takes over - found by the black-start test on AUX-HV "
             "alone")
INPUT_DRIVERS = {"ESTOP_A_OK": "A", "ESTOP_B_OK": "B", "UV_OK": "A", "TRIP_OK": "A", "TEST_A_N": "A", "TEST_B_N": "B",
                 "SVC_N": "A+B", "AUX_EN": "A+B", "AUX_INH_N": "A+B", "AUX_TEST": "A+B",
                 "UV_TEST": "A", "TRIP_TEST": "A", "SYS_RST_N": "A+B", "FLT_CLR": "A+B"}


def fmea(B, C):
    """Single-fault analysis: every gate / latch output of either channel, the E-stop isolator outputs, the UV
    detector, the watchdog / reset line, the self-test lines and FLT_CLR stuck high and stuck low; each chain switch
    stuck on. Per fault (worst case over the four power-up latch states): does the module start, does the firmware
    start-up self-test see it, and does opening the E-stop (both channels) still (i) remove GATE_EN or +24V_GD and
    (ii) open switch A or switch B of 24V_DO?"""
    owner = {}
    for p in B.D.parts.values():
        for net in p.pins.values():
            if net in INPUT_DRIVERS and p.lib_id.split(":")[1] in ("ISO1212", "ISO1211", "TPS3710", "TPS3850G33",
                                                                     "MCP23S17", "J_SVC", "TPS3700"):
                if p.lib_id.endswith(":J_SVC"):
                    owner[net] = p.ref              # the jumper, not the supervisor, drives SVC_N
                else:
                    owner.setdefault(net, p.ref)
    faults = [(ref, out, B.chan[ref]) for ref, ins, out in C.gates if B.chan.get(ref) in ("A", "B")]
    faults += [(f["ref"], f["q"], B.chan[f["ref"]]) for f in C.ffs]
    faults += [(owner.get(n, "CTRL"), n, ch) for n, ch in INPUT_DRIVERS.items()]
    cases = [(ref, net, ch, "stuck %s%s" % ("high" if s else "low", AUX_FAULT[s] if net == "AUX_EN" else ""), {net: s})
             for ref, net, ch in faults for s in (1, 0)]
    cases += [(C.sw[r][0], net, ch, "stuck on", {"stuck_on": (r,)})
              for r, net, ch in (("A", "24V_DO_MID", "A"), ("B", "24V_DO", "B"), ("GD", "+24V_GD", "B"))]
    qs = [{C.ffs[0]["ref"]: a, C.ffs[1]["ref"]: b} for a, b in product((0, 1), repeat=2)]
    seq = START + SELFTEST + ESTOP
    good = [C.run({}, seq, q0) for q0 in qs]
    obs = lambda t: [tuple(v[o] for o in OBS) for _, v in t]
    rows = []
    for ref, net, ch, fault, force in cases:
        worst = None
        for q0, g in zip(qs, good):
            tr = C.run(force, seq, q0)
            run, e = tr[len(START) - 1][1], tr[-1][1]
            starts = run["GATE_EN_L"] and run["CHB_OK_B"] and run["DO"] and run["GD"]     # as CTRL sees it
            det = 0 if not starts else 1 if obs(tr[:-1]) != obs(g[:-1]) else 2 if obs(tr[-1:]) != obs(g[-1:]) else 3
            gate_off, do_open = e["GATE_EN_L"] == 0 or e["GD"] == 0, e["SW_A"] == 0 or e["SW_B"] == 0
            w = C.run(force, START + WATCHDOG, q0)[-1][1]
            wd = (w["GATE_EN_L"] == 0 or w["GD"] == 0) and (w["SW_A"] == 0 or w["SW_B"] == 0)
            key = (not (gate_off and do_open), det, not wd)
            if worst is None or key > worst[0]:
                worst = (key, gate_off, do_open, det, wd)
        _, gate_off, do_open, det, wd = worst
        is_cmd = B.D.parts[ref].lib_id.endswith(":SN74LVC08A") if ref in B.D.parts else False
        detection = CMD_GATE[force.get(net)] if is_cmd else DETECT[det]
        if net == "SVC_N" and fault == "stuck high":
            detection = "no safety effect: service mode cannot be entered (seen when programming)"
        if net == "AUX_EN" and force[net] == 0:
            detection = AUX_AVAIL
        rows.append(dict(ref=ref, net=net, channel=ch, fault=fault, estop_gate_off="yes" if gate_off else "NO",
                         estop_do_open="yes" if do_open else "NO",
                         result="tolerated" if gate_off and do_open else "NOT TOLERATED", detection=detection,
                         watchdog_trip="kept" if wd else "lost"))
        if net == "AUX_EN" and force[net] == 0:              # same state as a healthy cabinet-only module
            rows.append(dict(rows[-1], fault="detector unpowered: AUX-HV absent or unplugged",
                             detection="no safety effect: cabinet-only operation arms normally"))
    return rows + unpowered(B, C)


def ohms(p):
    s = p.value.rstrip("R")
    return float(s[:-1]) * {"k": 1e3, "M": 1e6}[s[-1]] if s[-1] in "kM" else float(s)


LIVE = ("3V3", "5V", "24V_OR", "VDET")


def passive_level(B, net, dead):
    """Level a net settles to when nothing drives it: the strongest resistor to GND / a dead rail (0) or to a live
    rail (1); None = floating. A TPS1663 ~SHDN left floating is ON (internal pull-up, SLVSET9G p9)."""
    best = None
    for p in B.D.parts.values():
        if p.lib_id.endswith(":R") and net in p.pins.values():
            other = [n for n in p.pins.values() if n != net][0]
            lvl = 0 if other == "GND" or other in dead else 1 if other in LIVE else None
            if lvl is not None and (best is None or ohms(p) < best[1]):
                best = (lvl, ohms(p))
    return None if best is None else best[0]


def unpowered(B, C):
    """CSR-04: loss of 3V3 (every LVC / ISO / supervisor output high-Z, 3V3 pull-ups dead) and each chain driver
    unpowered on its own. Every chain-controlled switch and the fan supplies must then be OFF and GATE_EN low, from
    pull resistors alone."""
    drv = {out: ref for ref, ins, out in C.gates}
    for f in C.ffs:
        drv[f["q"]] = f["ref"]
    shdn = dict({r: net for r, (ref, net) in C.sw.items()}, FAN=C.fans[0][1])       # four fan eFuses share FAN_ON
    rows = []
    cases = [("3V3", "3V3 rail lost (common cause)", {"3V3"}, set(shdn.values()) | {"GATE_EN"})]
    cases += [(drv[n], "%s unpowered (output high-Z)" % drv[n], set(), {n}) for n in sorted(set(shdn.values()))]
    cases += [(drv["GATE_EN_L"], "%s unpowered (output high-Z)" % drv["GATE_EN_L"], set(), {"GATE_EN"})]
    for ref, label, dead, nets in cases:
        lvl = {n: passive_level(B, n, dead) for n in nets}
        on = {r: lvl[n] in (None, 1) for r, n in shdn.items() if n in nets}
        assert not any(on.values()), ("chain switch ON with %s" % label, on)
        assert lvl.get("GATE_EN", 0) == 0, ("GATE_EN high with %s" % label, lvl)
        rows.append(dict(ref=ref, net="/".join(sorted(nets)), channel="A+B" if ref == "3V3" else B.chan.get(ref, "?"),
                         fault=label, estop_gate_off="yes", estop_do_open="yes", result="tolerated",
                         detection="evident: fans cannot start (tach)" if nets == {shdn["FAN"]} else DETECT[0],
                         watchdog_trip="kept"))
    return rows


def check_3v3():
    """CSR-02: TPS7A2033 band against the TPS3850G33 window incl. accuracy and hysteresis (SBVS338H 5.5, SBVS301B)."""
    lo, hi = 3.3 * (1 - 0.015), 3.3 * (1 + 0.015)
    uv_rel = 3.3 * 0.96 * 1.008 * 1.008              # UV threshold max x (1 + max hysteresis): release point
    ov_min = 3.3 * 1.04 * 0.992
    assert lo - uv_rel >= 0.025 and ov_min - hi >= 0.025, ("3V3 vs supervisor window", lo, uv_rel, hi, ov_min)
    return lo, hi, (lo - uv_rel) * 1e3, (ov_min - hi) * 1e3


AUX_V = (22.40, 23.15)                    # AUX-HV regulation band (interfaces.AUX / D-022)
AUX_FAIL = (23.8, 28.5)                   # AUX-HV failed-feedback output range (INT-08)


def par(*rs):
    return 1 / sum(1 / x for x in rs)


def band_over(vt, r1, bots, rtol, ib):
    """thr_band over several possible bottom resistances (union of the bands)."""
    b = [thr_band(vt, r1, x, rtol, ib) for x in bots]
    return min(x[0] for x in b), max(x[1] for x in b)


R1P = 0.01 + 100e-6 * 65                  # 1 % thick film + 100 ppm/K x 65 K


def check_aux(B, uv_max, cmin):
    """INT-08/10/13: AUX-HV feed thresholds from the drawn dividers, the source-inhibit threshold on AUX_EN and the
    detector start-up hold-off (rev E), and the cabinet -> AUX hand-over sag."""
    r = lambda a, b: (rval(B, a, b))
    cab_r = thr_band((0.396, 0.404), r("24V_F1", "CAB1_SNS"), r("CAB1_SNS", "GND"), RT01, 15e-9)
    cab_f = thr_band((0.387, 0.400), r("24V_F1", "CAB1_SNS"), r("CAB1_SNS", "GND"), RT01, 15e-9)
    # inhibit divider on AUX_EN (sheet 08): AUX_SNS to GND = 200k || 200k (AUX_TEST driven low) or 200k || 300k
    rs, rg, rt, rpd = r("AUX_EN", "AUX_SNS"), r("AUX_SNS", "GND"), r("AUX_TEST", "AUX_SNS"), r("AUX_TEST", "GND")
    sns_bot = (par(rg, rt), par(rg, rt + rpd))
    en_bot = [par(r("AUX_EN", "GND"), rs + x) for x in sns_bot]
    en_r = band_over((1.195, 1.267), r("24V_F3", "AUX_EN"), en_bot, RT01, 0)
    en_f = band_over((1.091, 1.159), r("24V_F3", "AUX_EN"), en_bot, RT01, 0)
    inh_r = band_over((0.396, 0.404), rs, sns_bot, R1P, 15e-9)      # AUX_EN level that asserts AUX_INH_N (TPS3700 INB-)
    inh_f = band_over((0.387, 0.400), rs, sns_bot, R1P, 15e-9)
    assert inh_r[1] < 1.195 and inh_f[0] > 0.25, ("inhibit must assert before V(UVLOR) min and stay off at the "
                                                  "priority detector VOL 0.25 V", inh_r, inh_f)
    # hot-plugged AUX-HV (step to AUX max): AUX_EN is held by OUTA once VDET >= 1.3 V (VOL 250 mV at 0.4 mA)
    c_vdet = sum(1e-7 for p in B.D.parts.values() if p.lib_id.endswith(":C") and "VDET" in p.pins.values()) * 1.1
    t_hold = r("24V_F3", "VDET") * 1.01 * c_vdet * math.log(AUX_V[1] / (AUX_V[1] - 1.3))
    ven = AUX_V[1] * max(en_bot) / (r("24V_F3", "AUX_EN") * 0.99 + max(en_bot))
    tau_en = par(r("24V_F3", "AUX_EN") * 0.99, min(en_bot)) * 10e-9 * 0.9
    glitch = ven * (1 - math.exp(-t_hold / tau_en))
    vdet_sns = (11.40 * r("VDET_SNS", "GND") / (r("VDET", "VDET_SNS") + r("VDET_SNS", "GND")) * (1 - 2 * R1P),
                12.70 * r("VDET_SNS", "GND") / (r("VDET", "VDET_SNS") + r("VDET_SNS", "GND")) * (1 + 2 * R1P))
    assert AUX_V[1] / r("24V_F3", "AUX_EN") < 0.4e-3 and glitch < inh_r[0], ("detector hold-off", glitch, inh_r)
    assert 0.404 < vdet_sns[0] and vdet_sns[1] < 7.0, ("OUTA must release in operation (INA+ <= 7 V)", vdet_sns)
    ov_r = thr_band((1.195, 1.267), r("AUX_SW", "AUX_OV"), r("AUX_OV", "GND"), RT01, 0)
    ov_f = thr_band((1.091, 1.159), r("AUX_SW", "AUX_OV"), r("AUX_OV", "GND"), RT01, 0)
    assert cab_r[1] <= 21.6, ("a cabinet feed inside the window must have priority", cab_r)
    assert en_r[1] < AUX_V[0], ("AUX-HV must pass its own UVLO", en_r)
    assert AUX_V[1] < ov_r[0] and ov_r[1] < 26.10, ("AUX OV must sit between AUX max and the bus cut-off", ov_r)
    assert ov_f[1] < AUX_FAIL[0], ("a failed AUX-HV must stay off after an OV trip (no hiccup)", ov_f)
    return dict(cab_r=cab_r, cab_f=cab_f, en_r=en_r, en_f=en_f, ov_r=ov_r, ov_f=ov_f, inh_r=inh_r, inh_f=inh_f,
                glitch=glitch, t_hold=t_hold, en_bot=en_bot)


# ---------------------------------------------------------------- rev E: AUX-HV load budget and feed turn-on ramp
# AUX-HV rev B (sim/out/aux_hv_design/report.md sec. 5, 6, 13): continuous load <= 30 W (D-022); its current limit
# delivers >= 39.8 W (lowest corner); overload timer threshold 34 W (lowest corner), timer 88-181 ms.
AUX_PMAX, AUX_PLIM, AUX_PTMR, AUX_TTMR = 30.0, 39.8, 34.0, 0.088
# 5 V rail with the power branches off, datasheet maxima (A). ISO1042 ICC2 73.4 mA dominant into 60 R (SLLSF09F
# 6.x); ISO1410 ICC2 160 mA at 500 kbps into 54 R, 5 V (SLLSF22I 6.12); SN6505B I(VCC) 2.3 mA (SLLSEP9I 6.5); a
# push-pull primary carries n x the secondary current (760390014 n = 1.3, BMU-GW 750315371 n = 1.1, + 5 mA preload);
# ADS7953 +VA 3 mA (SLAS605C), REF5025E 1.2 mA (SBOS410O), 5V LED 3 mA; 3.3 V LDO 0.10 A allocation (all 3.3 V
# logic incl. BMU-GW side 1 and the status LEDs).
I5 = dict(can=1.3 * 73.4e-3 + 2.3e-3, rs485=1.3 * 160e-3 + 2.3e-3, gw=1.1 * (73.4e-3 + 160e-3 + 5e-3) + 2.3e-3,
          ldo=0.10, misc=3e-3 + 1.2e-3 + 3e-3)
ETA5 = 0.85                    # LMR36015 90 % typ at 24 V -> 5 V, 1 A (SNVSB49D p1), derated 5 points (assumed)
CTRL_W = 0.35 * 21.6           # gen/ctrl_c2000.py CTRL_24V_LOAD_A = 0.35 A at 21.6 V: constant power
PORT_A = 0.6                   # interfaces.PORT: port-board logic + hold-closed coils, <= 0.6 A for <= 200 s
FULL_A = 4 * 2.4 + 4.0 + 5.5   # running module before the inhibit acts: fans (PFC1224DE max), +24V_GD, 24V_DO sum
# LM7480-Q1 SNOSD95C: I(HGATE) source 39-75 uA (7.5); T(DRV_EN) eq. 1 = 175 us + C(CAP) x V(CAP UVLO) / I(CAP),
# taken as 270 us (tEN(dly) DGATE max) + 110 nF x 7.9 V / 1.3 mA; V(ENR) <= V(ENF) 0.93 + 0.095 V. CSD18532Q5B
# (SLPS322E): VGS(th) 1.5-2.2 V at 250 uA; ~1.5 A at -40 C needs <= 3.0 V (estimate from fig 4-3 and fig 4-6).
IHG, VGS_ON, T_DRV, V_ENR = (39e-6, 75e-6), 3.0, 270e-6 + 110e-9 * 7.9 / 1.3e-3, 0.93 + 0.095
CDV = (0.9 * 0.6, 1.1)         # CdVdT effective / nominal: -10 % and X7R DC bias -40 % at <= 37 V on 100 V (assumed)
TPD = (2 * 18e-6, 2 * 29e-6)   # TPS3700 tPHL / tPLH (SBVS187G 6.6 gives nominal values only: doubled)


def si(s):
    s = s.split()[0].rstrip("R")
    return float(s[:-1]) * {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6}[s[-1]] \
        if s[-1] in "pnumkM" else float(s)


def check_budget(B):
    """D-022: worst-case AUX-HV load with the fans, +24V_GD and 24V_DO forced off, at AUX max (asserted <= 30 W)."""
    v, r = AUX_V[1], lambda a, b: rval(B, a, b)
    div = lambda top, node: v / (r(top, node) + r(node, "GND"))
    i24 = (div("24V_OR", "OV_SNS") + div("24V_OR", "PGH_SNS") + div("24V_OR", "WIN_UV") + div("24V_OR", "EF_UVLO")
           + div("24V_OR", "UV_SNS") + (v - 2.4) / r("24V_OR", "LED_24_A") + v / r("24V_OR", "TRIP_SRC")
           + 1.7e-3 + 7 * 60e-6)        # TPS1663 IQ(ON) of the +24V branch, IQ(OFF) of the other seven (SLVSET9G 7.5)
    i_aux = ((v - 11.40) / r("24V_F3", "VDET") + div("24V_F3", "AUX_EN") + v / (r("AUX_SW", "AUX_OV") +
             r("AUX_OV", "GND")) + 495e-6)                                     # LM74800 I(Q) max (SNOSD95C 7.5)
    i5 = 2 * I5["can"] + 2 * I5["rs485"] + I5["ldo"] + I5["misc"]
    own, gw = i5 * 5.02 / ETA5 + i24 * v, I5["gw"] * 5.02 / ETA5
    rows = [("SYS-IO-AUX logic: 5 V %.2f A (4 isolated ports at max, 3V3 0.1 A) / %.2f + 24 V direct %.0f mA"
             % (i5, ETA5, i24 * 1e3), own),
            ("BMU-GW on this board's 5 V: %.0f mA (ISO1042 + ISO1410 at max)" % (I5["gw"] * 1e3), gw),
            ("AUX feed parts on 24V_F3: detector rail, dividers, LM74800 (%.1f mA)" % (i_aux * 1e3), i_aux * v),
            ("CTRL-C2000: 0.35 A at 21.6 V (gen/ctrl_c2000.py), constant power", CTRL_W),
            ("port board logic + hold-closed coils: %.1f A at %.2f V, <= 200 s (interfaces.PORT)" % (PORT_A, v),
             PORT_A * v),
            ("fans, +24V_GD, 24V_DO (incl. both K_x_MAIN coil pairs, %.2f A when on): forced off" % sum(DO_LOAD.values()),
             0.0)]
    total = sum(w for _, w in rows)
    assert total <= AUX_PMAX, ("AUX-HV load with the power branches inhibited", total)
    return dict(rows=rows, total=total, logic=own + gw, aux=i_aux * v, own=own + gw + i_aux * v, port=PORT_A * v)


def check_ramp(B, aux, bud, cmin):
    """Rev E item B: HGATE CdVdT against (1) a black start from 0 V and (2) cabinet loss -> AUX take-over, both at
    the post-inhibit load, slow and fast corners (I(HGATE), CdVdT, bus capacitance)."""
    r = lambda a, b: rval(B, a, b)
    cd = si(DVDT[1])
    cbus = sum(1000e-6 * 1.2 if p.lib_id.endswith(":CB1000") else si(p.value) * 1.1 for p in B.D.parts.values()
               if (p.lib_id.endswith(":C") or p.lib_id.endswith(":CB1000")) and "24V_OR" in p.pins.values())
    corners = dict(fast=(IHG[1], cd * CDV[0], cbus), slow=(IHG[0], cd * CDV[1], cmin))
    p24_r = thr_band((1.176, 1.224), r("24V_OR", "EF_UVLO"), r("EF_UVLO", "GND"), RT01, 150e-9)
    p24_f = thr_band((1.09, 1.15), r("24V_OR", "EF_UVLO"), r("EF_UVLO", "GND"), RT01, 150e-9)
    uvlo = max(p24_f[1], 14.75)        # +24V eFuse here (SLVSET9G 7.5); CTRL port eFuse TPS26600 13.25-14.75 V falling
    von = thr_band((1.157, 1.3), r("24V_OR", "BUCK_EN"), r("BUCK_EN", "GND"), R1P, 0)          # LMR36015 EN UVLO
    voff = thr_band((1.157 - 0.11, 1.3 - 0.11), r("24V_OR", "BUCK_EN"), r("BUCK_EN", "GND"), R1P, 0)   # 110 mV typ
    va = AUX_V[1]

    def i_load(v, on, logic=True):     # bus current with the power branches off; on = +24V branch (CTRL, port) on
        return (bud["logic"] / v if logic else 0.0) + ((CTRL_W / v + PORT_A) if on else 0.0)

    def aux_p(i):
        return va * i + bud["aux"]

    black = {}
    for k, (ih, c, cb) in corners.items():
        s, v, dv, p_pk, t_hi, e, pf = ih / c, 0.0, 0.01, 0.0, 0.0, 0.0, 0.0
        while v < AUX_V[0]:
            i = cb * s + i_load(v, v >= p24_r[0], v >= von[0])
            p_pk, pf = max(p_pk, aux_p(i)), max(pf, (va - v) * i)
            t_hi += dv / s if aux_p(i) > AUX_PTMR else 0.0
            e += (va - v) * i * dv / s
            v += dv
        black[k] = dict(vms=s / 1e3, t_pre=2.2 / s, t_ramp=AUX_V[0] / s, p_peak=p_pk, t_over34=t_hi, e_fet=e,
                        p_fet=pf, i_in=cb * s)
        assert p_pk <= AUX_PLIM and t_hi < AUX_TTMR, ("black start against the AUX-HV limit", k, black[k])
    ven = AUX_V[0] * min(aux["en_bot"]) / (r("24V_F3", "AUX_EN") * 1.01 + min(aux["en_bot"]))
    tau = par(r("24V_F3", "AUX_EN") * 1.01, max(aux["en_bot"])) * 10e-9 * 1.1
    rise = lambda vx: tau * math.log((ven - 0.25) / (ven - vx))
    t_rel = par(r("24V_F1", "CAB1_SNS"), r("CAB1_SNS", "GND")) * 10e-9 * 1.1 + TPD[0]
    t_inh = t_rel + rise(aux["inh_r"][1]) + TPD[1] + 1.5e-6         # + TPS1663 tSD(dly) 1.5 us
    t_en = t_rel + max(rise(V_ENR) + T_DRV, rise(1.267))
    assert t_inh < t_en, ("the inhibit must act before HGATE is enabled", t_inh, t_en)

    def handover(ih, c, cb, full):
        dt, t, v, vg, tc, vmin, p_pk, t_hi, e = 5e-6, 0.0, aux["cab_f"][0], None, None, 1e9, 0.0, 0.0, 0.0
        while t < 2.0:
            il = i_load(v, True) + (FULL_A if full and t < t_inh else 0.0)
            if t >= t_en:
                vg = v if vg is None else vg + ih / c * dt
                if tc is None and vg - v >= VGS_ON:
                    tc = t
            if tc is None:
                v -= il / cb * dt
            else:
                v, i = vg - VGS_ON, il + cb * ih / c
                p_pk, e = max(p_pk, aux_p(i)), e + (va - v) * i * dt
                t_hi += dt if aux_p(i) > AUX_PTMR else 0.0
                if v >= AUX_V[0] - 0.05:
                    break
            vmin, t = min(vmin, v), t + dt
        return dict(vmin=vmin, t_cond=tc, t_rec=t, p_peak=p_pk, t_over34=t_hi, e_fet=e)

    hand = {k: handover(*corners[k], full=False) for k in corners}
    full = min(handover(*corners[k], full=True)["vmin"] for k in corners)
    for k, h in hand.items():
        assert h["vmin"] - uvlo >= 0.25 and h["p_peak"] <= AUX_PLIM and h["t_over34"] < AUX_TTMR, \
            ("cabinet -> AUX take-over", k, h, uvlo)
    assert von[1] < p24_r[0] and voff[1] < full, ("5 V buck UVLO: on below the +24V eFuse, off below any dip", von,
                                                  voff, full)
    return dict(black=black, hand=hand, full=full, uvlo=uvlo, p24_r=p24_r, p24_f=p24_f, t_inh=t_inh, t_en=t_en,
                cbus=(cmin, cbus), cd=cd, von=von, voff=voff)


def fmea_summary(rows):
    n = len(rows)
    bad = [r for r in rows if r["result"] != "tolerated"]
    cnt = [sum(r["detection"] == d for r in rows) for d in DETECT]
    lst = lambda d: ", ".join("%s %s" % (r["net"], r["fault"].split()[1]) for r in rows if r["detection"] == d)
    wd = ", ".join("%s %s" % (r["net"], r["fault"].split()[1]) for r in rows if r["watchdog_trip"] == "lost")
    head = ("%d single faults, E-stop gate-off kept %d/%d, 24V_DO opened %d/%d: %s"
            % (n, sum(r["estop_gate_off"] == "yes" for r in rows), n, sum(r["estop_do_open"] == "yes" for r in rows),
               n, "no single fault defeats the E-stop" if not bad else "%d NOT TOLERATED" % len(bad)))
    ncmd = sum(r["detection"] in CMD_GATE.values() for r in rows)
    npw = sum(r["fault"].endswith("unpowered (output high-Z)") or r["fault"].startswith("3V3 rail lost") for r in rows)
    text = ("SINGLE-FAULT ANALYSIS (each build, outputs/SYS-IO-AUX_safety_fmea.csv): gate, latch, isolator, detector,\n"
            "self-test, service and FLT_CLR outputs stuck high/low; chain switches stuck on; 3V3 lost and each chain\n"
            "driver unpowered (%d rows: pull-downs hold every chain switch OFF and GATE_EN low). " % npw + head + ".\n"
            "Evident (module will not start): %d. Latent, found by the firmware start-up self-test: %d.\n"
            "Latent, found only by the E-stop proof test: %d (%s). Latent, not detectable: %d%s.\n"
            "DO command gates (%d rows, not E-stop): stuck high energises that output whenever 24V_DO is on - firmware\n"
            "checks contactor feedback before every FLT_CLR. Watchdog trip lost (shared SYS_RST_N): %s - start-up step 5.\n"
            "FIRMWARE AT EVERY START: (1) after reset GATE_EN, CHB_OK, FLT_LATCH_N, 24V_DO_MID, 24V_DO, +24V_GD all 0;\n"
            "(2) FLT_CLR -> all 1; (3) TEST_A_N low -> GATE_EN, FLT_LATCH_N, 24V_DO_MID, 24V_DO 0, CHB_OK and +24V_GD"
            " stay 1;\nrelease + FLT_CLR; (4) TEST_B_N low -> CHB_OK, +24V_GD, 24V_DO 0, GATE_EN and 24V_DO_MID stay 1;"
            " release + FLT_CLR;\n(5) kick WDI early (closed window) -> reset, both latches tripped; FLT_CLR; (6) UV_TEST"
            " high -> UV_RB 0, GATE_EN 0;\nFLT_CLR; (7) TRIP_TEST high -> TRIP_RB 0, GATE_EN 0; FLT_CLR; (8) AUX_TEST high ->"
            " AUX_INH_RB 0, GATE_EN, CHB_OK,\n24V_DO, +24V_GD 0, fans stop; release, FLT_CLR. In service: trip on any lasting"
            " DI1/DI8 or GATE_EN/CHB_OK\ndisagreement. AUX-HV priority detector: stuck 'cabinet healthy' or unpowered has"
            " no E-stop effect (AUX-HV cannot\ntake over / is absent - black-start test on AUX-HV alone); stuck 'AUX active'"
            " holds the module off (evident).\n"
            "E-STOP PROOF TEST (commissioning, every maintenance, at least yearly): with the module running (GATE_EN ="
            " CHB_OK = 1)\npress the E-stop. Within 100 ms CTRL must read DI1 = 0 AND DI8 = 0, ESTOP_N = 0, GATE_EN = 0,"
            " CHB_OK = 0, FLT_LATCH_N = 0\n(24V_DO and +24V_GD off). Release it: all must stay 0 until FLT_CLR. Either DI"
            " still 1 = stuck E-stop isolator: out of service."
            % (cnt[0], cnt[1], cnt[2], lst(DETECT[2]), cnt[3], " (%s)" % lst(DETECT[3]) if cnt[3] else "", ncmd,
               wd or "none"))
    return text, head, not bad


def rval(B, a, b):
    """Ohms of the resistor drawn between nets a and b."""
    for p in B.D.parts.values():
        if p.lib_id.endswith(":R") and set(p.pins.values()) == {a, b}:
            s = p.value.rstrip("R")
            return float(s[:-1]) * {"k": 1e3, "M": 1e6}[s[-1]] if s[-1] in "kM" else float(s)
    raise KeyError((a, b))


def thr_band(vt, r1, r2, rtol, ib, rh=None, vol=(0.0, 0.0)):
    """Worst-case bus voltage at which a divider tap (r1 top, r2 bottom) reaches a switching level in vt (min, max),
    with resistor tolerance rtol, input bias ib and an optional hysteresis resistor rh to an output at vol."""
    out = []
    for v, s1, s2, i, vo in product(vt, (-1, 1), (-1, 1), (0, ib), vol):
        a, b = r1 * (1 + s1 * rtol), r2 * (1 + s2 * rtol)
        out.append(v + a * (v / b + i + ((v - vo) / rh if rh else 0)))
    return min(out), max(out)


REF = (2.5 * (1 - 0.00025 - 2.5e-6 * 165), 2.5 * (1 + 0.00025 + 2.5e-6 * 165))   # REF5025E 0.025 % + 2.5 ppm/K box
RT01 = 0.001 + 10e-6 * 65                                                        # 0.1 % + 10 ppm/K x 65 K


def window_stack(B):
    """Worst-case 24 V thresholds from the drawn resistor values; D-013 limits asserted."""
    vio = 4e-3                                                                   # LM2903B -40..125 C
    ov = thr_band((REF[0] - vio, REF[1] + vio), rval(B, "24V_OR", "OV_SNS"), rval(B, "OV_SNS", "GND"), RT01, 50e-9,
                  rh=rval(B, "OV_CUT", "OV_SNS"), vol=(0.0, 0.55))
    pgh = thr_band((REF[0] - vio, REF[1] + vio), rval(B, "24V_OR", "PGH_SNS"), rval(B, "PGH_SNS", "GND"), RT01, 50e-9)
    pgl = thr_band((0.387, 0.400), rval(B, "24V_OR", "WIN_UV"), rval(B, "WIN_UV", "GND"), RT01, 25e-9)
    uv = thr_band((0.387, 0.400), rval(B, "24V_OR", "UV_SNS"), rval(B, "UV_SNS", "GND"), RT01, 25e-9)
    ef = thr_band((1.176, 1.224), rval(B, "24V_OR", "EF_UVLO"), rval(B, "EF_UVLO", "GND"), RT01, 150e-9) + \
        thr_band((1.09, 1.15), rval(B, "24V_OR", "EF_UVLO"), rval(B, "EF_UVLO", "GND"), RT01, 150e-9)
    assert 26.10 <= ov[0] and ov[1] <= 26.45, ("OV cut-off outside 26.10-26.45 V", ov)
    assert pgh[1] <= 26.10 and pgl[1] <= 21.6, ("PG_24V edges", pgh, pgl)
    text = ("D-013: window 21.6-26.0 V; nothing downstream above 26.45 V steady. Worst-case stacks (corners of every term):\n"
            "OV cut-off = LM2903B-1 vs REF5025E: ref 2.5 V +/-0.066 %% (0.025 %% + 2.5 ppm/K x 165 K), VIO +/-4 mV, IB 50 nA,\n"
            "16.9k/1.78k 0.1 %% + 10 ppm/K x 65 K each, 1M hysteresis to VOL 0-0.55 V -> %.3f-%.3f V -> OV_CUT -> OVP of all\n"
            "8 eFuses (8.5-14 us). PG_24V = high edge LM2903B-2 18.7k/2.00k %.3f-%.3f V AND low edge TPS3700 INA+\n"
            "(VIT- 387-400 mV) 100k/1.91k %.2f-%.2f V. Chain UV (sheet 08) %.2f-%.2f V. eFuse UVLO (TPS1663 1.176-1.224 /\n"
            "1.09-1.15 V, 100k/8.06k 0.1 %%) %.2f-%.2f V on, %.2f-%.2f V off: +24V (CTRL) is the logic limit at a hand-over.\n"
            "TVS SMCJ33CA (VBR >= 36.7 V) stays idle in the design surge - see the surge reservoir."
            % (ov + pgh + pgl + uv + ef))
    return dict(ov=ov, pgh=pgh, pgl=pgl, uv=uv), text


# Design surge: IEC 61000-4-5 1.2/50 us, 0.5 kV via 12 ohm (EN IEC 61000-6-2 DC-port line-to-earth level, taken as
# differential). Charge bound Q = V/Z x 72 us (the 1.2/50 us tail driving a short through Z) - conservative.
SURGE = dict(v=500.0, z=12.0, tail=72e-6)
SURGE_2 = dict(v=500.0, z=2.0, tail=24e-6)            # 0.5 kV via 2 ohm line-to-line, 8/20 us: reported, not covered
CB = dict(c=1000e-6, tol=0.8, cold=0.8, z20=0.021, zratio=3.0)   # ZLH: -20 %, cold derating (assumed), Z(-40)/Z(20)
ABSMAX = {"+24V_GD": [("UCC14241-Q1 VIN", 32.0)], "+24V": [("LMR38020 VIN", 85.0), ("TPS26600 IN", 62.0)],
          "24V_DO": [("TPS272C45 VS", 60.0)], "24V_DO_MID": [("TPS16630 IN", 67.0)],
          "24V_OR": [("CSD18532Q5B VDS", 60.0), ("LM74700 CATHODE (ROC)", 60.0), ("LMR36015 VIN", 66.0),
                     ("TPS16630 IN", 67.0)],
          "24V_FAN1-4": [("PFC1224DE-F00 (26.5 V operating, no abs max published)", None)]}


def check_surge(B, v0):
    """Bus peak = v0 (highest bus with loads still connected = OV cut-off max) + Q / C_min + I_peak x ESR_max. The
    branches track the bus (eFuse RON x branch C << surge, branch charging current << ILIM), so each sees the bus peak."""
    n = sum(1 for p in B.D.parts.values() if p.lib_id.endswith(":CB1000") and "24V_OR" in p.pins.values())
    cmin, esr = n * CB["c"] * CB["tol"] * CB["cold"], CB["z20"] * CB["zratio"] / n
    peak = lambda s: v0 + s["v"] / s["z"] * s["tail"] / cmin + s["v"] / s["z"] * esr
    bus, bus2 = peak(SURGE), peak(SURGE_2)
    assert bus < 36.7, ("design surge reaches the TVS", bus)
    rows, worst = [], 1e9
    for branch, loads in ABSMAX.items():
        for load, lim in loads:
            if lim is not None:
                assert bus <= lim, ("surge exceeds", branch, load, bus, lim)
                worst = min(worst, lim - bus)
            rows.append("%-10s %-50s %s" % (branch, load, "%.1f V limit, margin %.1f V" % (lim, lim - bus)
                                            if lim else "not rated: %.1f V for < 0.2 ms (unverified)" % bus))
    text = ("Design surge IEC 61000-4-5 0.5 kV / 12 ohm: I %.1f A, Q %.2f mC into %d x 1000 uF (C_min %.2f mF, ESR_max %.0f mOhm)\n"
            "-> bus and every branch <= %.2f V (TVS idle below 36.7 V; worst load margin %.1f V, UCC14241 32 V).\n"
            "0.5 kV / 2 ohm line-to-line (EN IEC 61000-6-2 only for 24 V cables > 30 m) would reach %.0f V: NOT covered -\n"
            "keep the cabinet 24 V wiring < 30 m or fit an external SPD. The OV cut-off may open the eFuses for ~1.3 ms."
            % (SURGE["v"] / SURGE["z"], SURGE["v"] / SURGE["z"] * SURGE["tail"] * 1e3, n, cmin * 1e3, esr * 1e3, bus,
               worst, bus2))
    return bus, rows, text, cmin


def check_chain(B):
    C = Chain(B)
    ng = check_truth(C)
    print("[PASS] Safety chain - gate-level simulation of the drawn dual-channel netlist matches the sheet-08 truth "
          "table (%d gates, 2 latches, %d input combinations incl. AUX-HV as the source x 4 power-up states)"
          % (ng, 2 ** len(INPUTS)))
    rows = fmea(B, C)
    text, head, ok = fmea_summary(rows)
    print("[%s] Single-fault analysis - %s" % ("PASS" if ok else "FAIL", head))
    return rows, text, ok


def write_fmea(rows):
    path = os.path.join(L.REPO, "hardware", PROJECT, "outputs", PROJECT + "_safety_fmea.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("        FMEA table -> %s" % os.path.relpath(path, L.REPO))


def write_ramp(aux, bud, ramp):
    """Feed ramp and post-inhibit load for re-running the AUX-HV simulation (rev E item B)."""
    path = os.path.join(L.REPO, "hardware", PROJECT, "outputs", PROJECT + "_aux_feed.json")
    d = dict(rev=REV, source="gen/sys_io_aux.py check_aux / check_budget / check_ramp (calculated, not measured)",
             feed=dict(uvlo_rising_V=aux["en_r"], uvlo_falling_V=aux["en_f"], ov_rising_V=aux["ov_r"],
                       ov_rearm_V=aux["ov_f"], cabinet_healthy_rising_V=aux["cab_r"],
                       cabinet_lost_falling_V=aux["cab_f"], inhibit_at_AUX_EN_V=aux["inh_r"]),
             hgate=dict(R1_ohm=si(DVDT[0]), CdVdT_nominal_F=ramp["cd"], CdVdT_effective_F=[ramp["cd"] * x for x in CDV],
                        I_HGATE_A=IHG, VGS_on_V=VGS_ON, bus_capacitance_F=ramp["cbus"],
                        ramp_V_per_ms=[ramp["black"]["slow"]["vms"], ramp["black"]["fast"]["vms"]]),
             black_start=ramp["black"],
             handover=dict(ramp["hand"], t_inhibit_s=ramp["t_inh"], t_hgate_enabled_s=ramp["t_en"],
                           logic_uvlo_V=ramp["uvlo"], full_power_bus_min_V=ramp["full"]),
             load_post_inhibit=dict(total_W=bud["total"], rows=bud["rows"], port_hold_s=200,
                                    without_port_hold_W=bud["total"] - bud["port"],
                                    model="this board + BMU-GW constant power above 6 V; CTRL constant power and the "
                                          "port 0.6 A above the +24V eFuse UVLO; feed parts on AUX-HV directly"))
    with open(path, "w") as f:
        json.dump(d, f, indent=1)
    print("        AUX feed ramp -> %s" % os.path.relpath(path, L.REPO))


# ======================================================================================================== build
def build_design():
    B = L.Builder(PROJECT, "SYS-IO-AUX", REV, DATE, CATALOG,
                  rails=["+24V", "+24V_GD", "24V_OR", "24V_DO", "24V_DO_MID", "5V", "3V3", "VREF"] +
                        ["ISO_%s_5V" % t for t in ISO_PORTS],
                  returns=["GND", "PE", "DIF_GND"] + ["ISO_%s_GND" % t for t in ISO_PORTS],
                  subtitle="System I/O and auxiliary board, 25-110 kW DC/DC family (ECO-03/04/06/07)",
                  comment1="Domains: PELV, CANB, MCAN, RSA, RSB, DI field, PE - only isolators cross (checked)",
                  comment4="Not bench-validated. Dual-channel chain gate- and single-fault-checked on every build.",
                  root_notes=[
                      "24 V: two cabinet feeds (fused 30 A, TVS 33 V, LM74700 ideal-diode ORing) and the AUX-HV "
                      "bootstrap feed (fused, TVS, LM74800 load switch with OV cut-off and a CdVdT ramp; it connects only "
                      "when no cabinet feed is healthy) onto 24V_OR with a 2 mF surge reservoir. Every outgoing branch is "
                      "a TPS16630 eFuse with the shared precision OV cut-off (26.13-26.42 V, D-013) and UVLO (16 V): "
                      "+24V to CTRL (2 A), +24V_GD gate-drive bias (5 A, channel B), 24V_DO via switch A and switch B "
                      "in series (6 A), four fans (3.6 A). While AUX-HV is the source, hardware forces the fans, +24V_GD "
                      "and 24V_DO off and latches both chain channels (D-022: AUX-HV <= 30 W; checked every build). "
                      "PG_24V = inside the 21.6-26.0 V window.",
                      "Safety chain (ECO-04), two channels on separate parts: A = E-stop A, 24 V UV, spare loop, "
                      "watchdog -> latch A -> GATE_EN + 24V_DO switch A; B = E-stop B, watchdog -> latch B -> CHB_OK "
                      "-> +24V_GD + 24V_DO switch B. Each channel alone stops the gates and opens 24V_DO; the build "
                      "simulates every single stuck fault (outputs/SYS-IO-AUX_safety_fmea.csv).",
                      "Isolation: CANB, MCAN, RS-485 A/B each have their own SN6505B supply and ground; the DI field "
                      "side (DIF_GND) is a separate domain; PE is reached only through the RJ45 Bob-Smith node and the "
                      "470 pF DI field-ground capacitor. Budget: 16 A steady worst case, 20 A for 100 ms."])
    B.chan = {}
    iso, crossing = set(), set()
    sheet_power_in(B)
    sheet_rails(B)
    sheet_ctrl_if(B)
    sheet_comms(B, iso)
    sheet_eth(B, iso)
    sheet_di(B, iso, crossing)
    sheet_chain(B)
    sheet_do(B)
    sheet_fans(B)
    sheet_ntc(B)
    sheet_service(B)
    return B, iso, crossing


if __name__ == "__main__":
    B, iso, crossing = build_design()
    try:
        bands, wtext = window_stack(B)
        bus, srows, stext, cmin = check_surge(B, bands["ov"][1])
        lo3, hi3, m_uv, m_ov = check_3v3()
        aux = check_aux(B, bands["uv"][1], cmin)
        di_text = check_di_grounds(B)
        coil = check_coil_turnoff(bands["ov"][1])
        do_tot, do_short = check_do_loads(B)
    except AssertionError as e:
        print("[FAIL] 24 V window / surge / 3V3 / AUX feed / DI grounds / coil turn-off - %s" % (e,))
        sys.exit(1)
    B.window_block.note = wtext
    B.surge_block.note = ("2 x 1000 uF 50 V low-impedance electrolytic absorb the surge charge (check_surge, every build).\n"
                          + stext)
    B.ldo_block.note = ("TPS7A2033 3.3 V +/-1.5 %% (line, load 1-300 mA, -40..125 C): %.4f-%.4f V. TPS3850G33 releases\n"
                        "RESET at <= 3.2189 V (3.168 V +0.8 %% +0.8 %% hysteresis) and trips OV at >= 3.4045 V: margins\n"
                        "%.1f mV / %.1f mV (check_3v3, every build; CSR-02). Load < 0.1 A, 0.2 W." % (lo3, hi3, m_uv, m_ov))
    try:
        bud = check_budget(B)
        ramp = check_ramp(B, aux, bud, cmin)
    except AssertionError as e:
        print("[FAIL] AUX-HV load budget / feed ramp - %s" % (e,))
        sys.exit(1)
    bk, hd = ramp["black"], ramp["hand"]
    B.aux_block.note = (
        "LM74800 + 2 x CSD18532Q5B back-to-back (common drain): ideal diode (DGATE) + load disconnect (HGATE).\n"
        "Connects only when NO cabinet feed is healthy (priority detector holds EN/UVLO in shutdown): cabinet healthy\n"
        "above %.2f-%.2f V rising, lost below %.2f-%.2f V falling (any in-window cabinet wins, INT-10). AUX UVLO\n"
        "%.2f-%.2f V rising / %.2f-%.2f V falling; AUX OV %.2f-%.2f V, re-arm below %.2f-%.2f V: a failed AUX-HV\n"
        "(23.8-28.5 V) is cut off before the 26.13 V bus cut-off and stays off (INT-08). AUX-HV overshoot must stay\n"
        "< %.2f V. While AUX_EN is up, sheet 08 forces fans, +24V_GD and 24V_DO off, latched (D-022, <= 30 W).\n"
        "HGATE ramp (LM7480-Q1 fig 9-3): R1 %s + CdVdT %s 100 V X7R; a disabled HGATE sits at OUT, so CdVdT tracks the\n"
        "bus. Black start: %.3f-%.3f V/ms, one ramp in %.2f-%.2f s, AUX-HV <= %.1f W (limit 39.8 W), > 34 W for <= %.0f\n"
        "ms (timer >= 88 ms). Cabinet -> AUX: inhibit <= %.2f ms, HGATE on <= %.2f ms, FET conducts <= %.2f ms after\n"
        "the detector releases; bus >= %.2f V > logic UVLO %.2f V (+24V eFuse) at %.1f W. AUX -> cabinet: AUX off\n"
        "~35 us, bus falls to the cabinet - no gap; +24V_GD / 24V_DO stay off until FLT_CLR."
        % (aux["cab_r"] + aux["cab_f"] + aux["en_r"] + aux["en_f"] + aux["ov_r"] + aux["ov_f"] +
           (aux["ov_r"][0], DVDT[0], DVDT[1], bk["slow"]["vms"], bk["fast"]["vms"], bk["fast"]["t_pre"] +
            bk["fast"]["t_ramp"], bk["slow"]["t_pre"] + bk["slow"]["t_ramp"], max(b["p_peak"] for b in bk.values()),
            max(b["t_over34"] for b in bk.values()) * 1e3, ramp["t_inh"] * 1e3, ramp["t_en"] * 1e3,
            max(h["t_cond"] for h in hd.values()) * 1e3, min(h["vmin"] for h in hd.values()), ramp["uvlo"],
            bud["total"])))
    B.aux_inh_block.note = (
        "AUX_EN (LM74800 EN/UVLO, sheet 01) -> 100k / (200k || 200k) -> TPS3700 INB-: AUX_INH_N low while AUX_EN >\n"
        "%.2f-%.2f V, i.e. before the LM74800 can enable (V(UVLOR) >= 1.195 V) and until it is back in shutdown\n"
        "(priority detector VOL <= 0.25 V). BAT54C pulls TEST_A_N and TEST_B_N low: both channels trip and LATCH\n"
        "(+24V_GD, 24V_DO off; GATE_EN, CHB_OK 0); FAN_ON = FAN_EN AND AUX_INH_N (sheet 10). No firmware in the path.\n"
        "Cabinet back: fans may restart, power needs FLT_CLR. AUX-HV absent: AUX_EN = 0, no inhibit (cabinet-only\n"
        "modules arm normally). Hot-plugged AUX-HV: the detector OUTA hold-off keeps AUX_EN <= %.2f V.\n"
        "Firmware reads AUX_INH_RB (expander 2 GPB5, 0 = AUX-HV is the source, power inhibited). Self-test: AUX_TEST\n"
        "(GPB6) high -> AUX_INH_RB 0, both channels trip, fans off; release, FLT_CLR.\n"
        "AUX-HV load with the branches off: %.1f W worst case <= 30 W (%.1f W of it the port board for <= 200 s)."
        % (aux["inh_r"] + (aux["glitch"], bud["total"], bud["port"])))
    print("[PASS] 24 V window - OV cut-off %.3f-%.3f V (D-013 26.10-26.45), PG_24V high edge %.3f-%.3f V (<= 26.10), "
          "low edge %.2f-%.2f V" % (bands["ov"] + bands["pgh"] + bands["pgl"]))
    print("[PASS] Surge let-through - IEC 61000-4-5 0.5 kV / 12 ohm: bus and every branch <= %.2f V, below every "
          "rated absolute maximum" % bus)
    for r in srows:
        print("        " + r)
    print("[PASS] 3V3 vs supervisor - TPS7A2033 %.4f-%.4f V, margin %.1f mV to UV release, %.1f mV to OV" %
          (lo3, hi3, m_uv, m_ov))
    print("[PASS] AUX-HV feed - cabinet priority rising %.2f-%.2f V (<= 21.6), AUX OV %.2f-%.2f V (23.15 < OV < 26.10), "
          "source inhibit at AUX_EN %.2f-%.2f V (< V(UVLOR) 1.195 V), hot-plugged AUX-HV held at <= %.2f V"
          % (aux["cab_r"] + aux["ov_r"] + aux["inh_r"] + (aux["glitch"],)))
    print("[PASS] AUX-HV load budget (fans, +24V_GD, 24V_DO forced off) - %.1f W <= 30 W at %.2f V; this board incl. "
          "BMU-GW and the feed parts %.1f W" % (bud["total"], AUX_V[1], bud["own"]))
    for label, w in bud["rows"]:
        print("        %5.2f W  %s" % (w, label))
    print("[PASS] AUX feed ramp - CdVdT %s + R1 %s: black start %.3f-%.3f V/ms, AUX-HV peak %.1f W (<= 39.8), > 34 W "
          "for <= %.0f ms (< 88); cabinet loss: bus >= %.2f V, %.2f V above the logic UVLO %.2f V"
          % (DVDT[1], DVDT[0], bk["slow"]["vms"], bk["fast"]["vms"], max(b["p_peak"] for b in bk.values()),
             max(b["t_over34"] for b in bk.values()) * 1e3, min(h["vmin"] for h in hd.values()),
             min(h["vmin"] for h in hd.values()) - ramp["uvlo"], ramp["uvlo"]))
    for k in ("slow", "fast"):
        b, h = bk[k], hd[k]
        print("        %s: black start %.3f V/ms, conducts after %.0f ms, ramp %.2f s, peak %.1f W, Q2 %.1f J (peak %.1f W)"
              "; hand-over conducts %.2f ms after release, bus min %.2f V, back in %.0f ms, peak %.1f W, > 34 W %.0f ms,"
              " Q2 %.1f J" % (k, b["vms"], b["t_pre"] * 1e3, b["t_ramp"], b["p_peak"], b["e_fet"], b["p_fet"],
                            h["t_cond"] * 1e3, h["vmin"], h["t_rec"] * 1e3, h["p_peak"], h["t_over34"] * 1e3, h["e_fet"]))
    print("        full-power cabinet loss (%.1f A until the inhibit, %.2f ms): bus min %.2f V - the +24V eFuse (CTRL, "
          "port board) may open below %.2f V; SYS logic (5 V buck off below %.2f V) and the latched trip ride through"
          % (FULL_A, ramp["t_inh"] * 1e3, ramp["full"], ramp["uvlo"], ramp["voff"][1]))
    print("[PASS] DI grounds - %s" % di_text)
    print("[PASS] Coil turn-off - TPS272C45 VS - VOUT <= %.1f V (abs max 48 V) with the port-board low-side switch + "
          "varistor" % coil)
    print("[PASS] DO loads - K_A_MAIN / K_B_MAIN carry a coil pair (0.60 A) on ILIM 10k (1.58-2.30 A); 24V_DO "
          "all outputs on %.2f A, one output shorted %.2f A < switch limit %.2f A; every TPS272C45 behind both "
          "series switches" % (do_tot, do_short, DOS_ILIM[0]))
    try:
        rows, text, chain_ok = check_chain(B)
    except AssertionError as e:
        print("[FAIL] Safety chain / unpowered-driver analysis - %s" % (e,))
        sys.exit(1)
    B.chain_block.note = text
    print("Per-output capability (sheet 09):")
    for r in do_table():
        print("  %-3s %-9s ILIM %-5s limit %-11s continuous %-5s inrush %-30s clamp %s" % tuple(r.values()))
    rc = L.build(B, domain_of=domain_of, isolators=iso, crossings=crossing)
    write_fmea(rows)
    write_ramp(aux, bud, ramp)
    if not chain_ok:
        print("%s: FAILED (single-fault analysis)" % PROJECT)
    sys.exit(rc or (0 if chain_ok else 1))
