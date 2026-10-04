"""GDRV - isolated SiC gate-driver channel block (rev 5, D-035: NOVOSENSE NSI6651ASC; D-036: TI UCC14241-Q1 bias) and the
GDRV-HB card.

1. Reusable block:  `channel(B, tag, pwm, en, flt_n, rdy, gate, source, drain, vdd, vee, ...)` adds ONE isolated
   channel to the current sheet of Builder B (it opens its own blocks there) and returns Channel(iso, xing, sec):
   iso = barrier refs (driver + bias module), xing = DESAT diode refs (they touch the drain), sec = the channel's
   floating-domain nets (use them in domain_of). Example, one leg on a power board:

       for leg in (1, 2):
           for side, other in (("H", "L"), ("L", "H")):
               t = "%s%d" % (side, leg)
               ch = gdrv.channel(B, t, pwm="PWM_%s" % t, en="EN", flt_n="FLT_N", rdy="RDY", gate="G_" + t,
                                 source="KS_" + t, drain="D_" + t, vdd="VDD_" + t, vee="VEE_" + t,
                                 interlock="PWM_%s%d" % (other, leg), r_on=2.0, r_off=1.0)
   Paralleled devices (PV cell, D-037: 2 x SG2M040170HJ per switch) - one gate net AND one Kelvin net per device;
   r_on/r_off are then per device:
       ch = gdrv.channel(B, t, ..., gate=("G_%sa" % t, "G_%sb" % t), source=("KS_%sa" % t, "KS_%sb" % t),
                         gate_v=(18, 3.5), vclass=1700, r_on=3.75, r_off=2.5, r_sb=22.0, v_peak=1396.0)
   net_ranges(...) returns the channel's DC net ranges for dcdclib.build(vrange=...).
   The caller adds once per shared net: FLT_N and RDY pull-ups (4.99k + 100p), EN pull-down (10k), PWR_FLAG on
   the logic rails.

2. `.venv/bin/python gen/gdrv.py` builds GDRV-HB: two channels for one leg of a CBB011M12GM4T full-bridge module.

Per channel (NSI66x1A-Q1 Rev 1.1 application section 9; UCC14241-Q1 SLUSF09A section 12.2):
  logic   IN+ = own PWM (100R/100p, 10k pull-down: an open or unpowered input is a low input -> gate off);
          IN- = the complementary switch's PWM, falling edge stretched (2.21k/100p + BAT54 + SN74LVC1G17 Schmitt):
          the driver ANDs IN+ with /IN- -> shoot-through interlock + a minimum dead time that
          equals max(firmware dead time, hardware stretch) and never adds delay to a correctly timed edge.
          RDY/FLT open drain (wired-AND / wired-OR on the caller's net); bias power-good pulls RDY low via 2N7002BK.
  bias    UCC14241-Q1 24 V -> +15/-4 V or +20/-4 V (VDD-COM / COM-VEE, GATE_V), regulated, reinforced barrier, RDR.
  gate    split Ron/Roff, CLAMP straight to the gate (Miller clamp), 10k gate-source, anti-series Zener clamp; with
          paralleled devices all of that per device plus an AO3400A Miller clamp at the gate-Kelvin pins (see channel()).
  DESAT   NSI6651 DESAT pin (internal blanking source), Rs + 3 x US1MH (1000 V) string to the drain, BAT54S clamp.
  SC      booster: DESAT above the driver's maximum trip fires an AO3400A + R_sb per gate (two-level, then fast off).
  NTC     none on the driver (no AIN/APWM): thermistors are plain analog lines to the CELL connector AN2/AN3.
Design numbers are computed and asserted by design_check() on every build.
"""
import math
import os
import sys
from collections import namedtuple

import json

import catalog
import dcdclib as L

# T_BIAS4 design by the magnetics engineer (rev M1): ratio, barrier capacitances, cost - read, not copied
_XF = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sim", "out", "magnetics",
                                  "design_gdrv_bias_transformer.json")))
XF_N = _XF["electrical"]["ratio"]
XF_C = _XF["electrical"]["capacitance_F"]          # adjacent / one_apart / two_apart / high_side_to_primary
XF_COST = (_XF["cost"]["catalogue_1k_usd"], _XF["cost"]["build_5k_usd"])

PROJECT, REV, DATE = "GDRV-HB", "J0", "2026-10-04"
DS = "docs/datasheets/"
TI, NXP = "Texas Instruments", "Nexperia"
DS_SAMTEC = "https://suddendocs.samtec.com/catalog_english/tsw_th.pdf"

PARTS = {
    # UCC21710 SLUSD43B (May 2023): pins Table 5-1 + Fig 5-1 p3. Limits: abs max/ROC p4, insulation p6-7,
    # electrical p8-9, switching p10, function table p29, application p31-44.
    "UCC21710": dict(mfr=TI, mpn="UCC21710DWR", prefix="U", pkg="SOIC-16W (DW)", ds=DS + "gate-drivers/UCC21710.pdf",
                     desc="Isolated SiC gate driver 10 A, OC/DESAT, soft turn-off, Miller clamp, AIN-APWM; reinforced "
                          "VDE 0884-17, VIOWM 2121 VDC, CMTI >= 150 V/ns",
                     pins={"left": ["15 VCC pi", "10 IN+ i", "11 IN- i", "14 RST/EN i", None, "13 ~{FLT} oc",
                                    "12 RDY oc", "16 APWM o", None, "9 GND pi"],
                           "right": ["5 VDD pi", "4 OUTH o", "6 OUTL o", "7 CLMPI o", None, "2 OC i", "1 AIN i",
                                     None, "3 COM pi", "8 VEE pi"]}),
    # NOVOSENSE NSI66x1A-Q1 Rev 1.1 (docs/datasheets/gate-drivers/NSI66x1A-Q1.pdf; industrial twin NSI66x1A.pdf):
    # NSI6651ASC pin table p3 (1 and 8 VEE2, 2 DESAT, 3 GND2, 4 OUTH, 5 VCC2, 6 OUTL, 7 CLAMP | 9 GND1, 10 IN+, 11 IN-,
    # 12 RDY, 13 /FLT, 14 /RST(EN), 15 VCC1, 16 TEST "recommended to connect to GND1"). Abs max / ROC p4, UVLO p6,
    # outputs + clamp + logic p7, DESAT + soft turn-off p9, timing p9-10, insulation p15-16, certificates p18.
    # Primary: -Q1SWR (AEC-Q100 grade 1, -40..150 C); alternate: NSI6651ASC-DSWR (industrial, ISTO >= 250 mA).
    "NSI6651ASC": dict(mfr="NOVOSENSE", mpn="NSI6651ASC-Q1SWR", prefix="U", pkg="SOW-16 (10.3 x 7.5 mm)",
                       ds=DS + "gate-drivers/NSI66x1A-Q1.pdf",
                       desc="Isolated smart SiC gate driver 10 A, DESAT + soft turn-off, Miller clamp, FLT/RDY; reinforced "
                            "VDE 0884-17 (40052820) VIORM 2121 Vpk, UL 1577 5.7 kVrms; AEC-Q100 grade 1. Alternate: "
                            "NSI6651ASC-DSWR",
                       pins={"left": ["15 VCC1 pi", "10 IN+ i", "11 IN- i", "14 ~{RST}/EN i", None, "13 ~{FLT} oc",
                                      "12 RDY oc", "16 TEST i", None, "9 GND1 pi"],
                             "right": ["5 VCC2 pi", "4 OUTH o", "6 OUTL o", "7 CLAMP o", None, "2 DESAT i", None,
                                       "3 GND2 pi", "1 VEE2 p", "8 VEE2 pi"]}),   # pin 1 passive: 0R option VEE / COM (UCC21750 AIN)
    # UCC14241-Q1 SLUSF09A (Aug 2023): pins Table 6-1 + Fig 6-1 p4-5; VIN 21-27 V p6; insulation p7-8 (VIOWM 1414 VDC,
    # reinforced, certification PLANNED); 2 W at TA <= 85 C, 1.5 W at 105 C p1/p9; design equations p32-39.
    "UCC14241": dict(mfr=TI, mpn="UCC14241QDWNRQ1", prefix="U", pkg="SSOP-36 (DWN)", ds=DS + "gate-drivers/UCC14241-Q1.pdf",
                     desc="Isolated DC/DC module 24 V in, 2 W, adjustable VDD-VEE and COM-VEE, PG, reinforced (planned "
                          "VDE 0884-17) VIOWM 1414 VDC, CIO < 3.5 pF, CMTI > 150 V/ns",
                     pins={"left": ["7 VIN pi", "6 VIN pi", "4 ENA i", "3 ~{PG} oc", None]
                           + ["%d GNDP pi" % n for n in (1, 2, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)],
                           "right": ["29 VDD p", "28 VDD p", "34 FBVDD i", "33 FBVEE i", "32 RLIM p", "35 VEEA p", None]
                           + ["%d VEE p" % n for n in (19, 20, 21, 22, 23, 24, 25, 26, 27, 30, 31, 36)]}),
    # SN74LVC1G17 SCES351Y: DBV pins p3; VT+/VT- Table 5-1 p6; tpd 1.5-4.6 ns at 3.3 V p7; CI 4.5 pF p6.
    "SN74LVC1G17": dict(mfr=TI, mpn="SN74LVC1G17DBVR", prefix="U", pkg="SOT-23-5 (DBV)",
                        ds=DS + "isolation-interface/SN74LVC1G17.pdf", desc="Single Schmitt-trigger buffer, 3.3 V",
                        pins={"left": ["5 VCC pi", "2 A i", "3 GND pi"], "right": ["4 Y o", None, "1 NC nc"]}),
    # Nexperia BAT54 (1 Jul 2022): Table 2 p2 pin 1 A, 2 n.c., 3 K (= KiCad BAT54W numbering); VF <= 320 mV at 1 mA p4.
    "BAT54": dict(mfr=NXP, mpn="BAT54", prefix="D", pkg="SOT-23", stock=("Diode", "BAT54W"), ds=DS + "protection/BAT54.pdf",
                  desc="Schottky diode 30 V 200 mA, trr <= 5 ns"),
    # Nexperia BAT54S (1 Jul 2022): Table 2 p2 pin 1 A1, 2 K2, 3 K1/A2 (= KiCad BAT54S 1 A, 2 K, 3 COM).
    "BAT54S": dict(mfr=NXP, mpn="BAT54S", prefix="D", pkg="SOT-23", stock=("Diode", "BAT54S"), ds=DS + "protection/BAT54S.pdf",
                   desc="Dual series Schottky 30 V 200 mA (sense-node clamp to COM and VDD)"),
    # Nexperia BZX84 Rev 7: Table 2 p2 pin 1 A, 2 n.c., 3 K; VZ Table 8 p5 (B16 15.70-16.30 V, B5V6 5.49-5.71 V at 5 mA).
    "BZX84-B16": dict(mfr=NXP, mpn="BZX84-B16", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                      ds=DS + "protection/BZX84.pdf", desc="Zener 16 V 2 % 250 mW (gate clamp, positive)"),
    "BZX84-B5V6": dict(mfr=NXP, mpn="BZX84-B5V6", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                       ds=DS + "protection/BZX84.pdf", desc="Zener 5.6 V 2 % 250 mW (gate clamp, negative)"),
    "BZX84-B22": dict(mfr=NXP, mpn="BZX84-B22", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),       # Table 8 p5
                      ds=DS + "protection/BZX84.pdf", desc="Zener 22 V 2 % 250 mW (gate clamp, positive, +20 V rail)"),
    # PMEG4010CEJ (shared catalog entry): per-gate Miller-clamp diode when devices are paralleled. Nexperia datasheet
    # (docs/datasheets/protection/PMEG4010CEJ.pdf) p2: VR 40 V, IFRM 7 A (tp <= 1 ms), VF <= 570 mV at 1 A.
    # Diodes Inc US1M DS16008 Rev 11-2: VRRM 1000 V, trr 75 ns, CT 10 pF at 4 V, IR 5/100 uA at 25/100 C p2; order US1M-13-F p1.
    # DESAT string: Taiwan Semiconductor US1MH (gen/data/alternates.csv DROP-IN for Diodes US1M-13-F, rev 5): VRRM 1000 V,
    # CJ 10 pF typ at 4 V, trr 75 ns, IR 150 uA at 125 C, AEC-Q101 (TSC-US1xH.pdf p1-3), same SMA land and cathode band.
    "US1M": dict(mfr="Taiwan Semiconductor", mpn="US1MH", prefix="D", pkg="SMA (DO-214AC)", stock=("Diode", "US1M"),
                 ds=DS + "power-semiconductors/TSC-US1xH.pdf",
                 desc="Ultrafast rectifier 1000 V 1 A trr 75 ns, AEC-Q101 (DESAT string)"),
    # Rev 7 per-phase bias (bias_phase): custom transformer, PNP-pass regulator, shunt for COM. PBSS5540X (Nexperia,
    # Nexperia-PBSS5540X.pdf p1-2, SOT-89: 1 E, 2 C/tab, 3 B; VCEO -40 V, IC -4 A, AEC-Q101). ATL431BIDBZR (TI ATL431.pdf
    # p3-4: DBZ 1 K, 2 REF, 3 A; Vref 2.475-2.525 V, IKA(min) <= 35 uA, VKA <= 36 V).
    "T_BIAS4": dict(mfr="", mpn="", prefix="T", pkg="EP10-class, sectioned bobbin, TIW secondaries", ds="", sourcing="CUSTOM",
                    desc="Gate-bias transformer per phase (sim/out/magnetics/design_gdrv_bias_transformer.json): %s %s, "
                         "%s, ratio 1:%g, barrier %.2f pF high-side winding to primary / %.2f pF adjacent sections, functional "
                         "insulation PD-free >= 1.75 kVpk; %.2f USD (1 k, ESTIMATE)" % (_XF["core"]["maker"],
                         _XF["core"]["part_number"], _XF["electrical"]["turns"], XF_N, XF_C["high_side_to_primary"] * 1e12,
                         XF_C["adjacent"] * 1e12, XF_COST[0]),
                    pins={"left": ["1 D1 p", "2 CT p", "3 D2 p"],
                          "right": ["4 S1A p", "5 S1B p", None, "6 S2A p", "7 S2B p", None, "8 S3A p", "9 S3B p", None,
                                    "10 S4A p", "11 S4B p"]}),
    "PBSS5540X": dict(mfr=NXP, mpn="PBSS5540X,135", prefix="Q", pkg="SOT-89", ds=DS + "power-semiconductors/Nexperia-PBSS5540X.pdf",
                      desc="PNP 40 V 4 A low VCEsat, AEC-Q101: pass transistor of the per-channel bias regulator",
                      pins={"left": ["3 B i"], "right": ["1 E p", "2 C p"]}),
    "ATL431-GD": dict(mfr=TI, mpn="ATL431BIDBZR", prefix="U", pkg="SOT-23-3 (DBZ)", ds=DS + "power-supply/ATL431.pdf",
                      desc="Shunt reference 2.5 V +/-1 %, 36 V: bias regulator and COM split",
                      pins={"left": ["2 REF i"], "right": ["1 K p", "3 A p"]}),
    # Miller clamp + SC booster FET behind the catalog KEY "AO3400A" (key kept for the board files; rev 6 part): Nexperia
    # PMV30ENEAR (LCSC C553092), Nexperia-PMV30ENEA.pdf: p1-2 pins 1 G / 2 S / 3 D, VDS 40 V, VGS +/-20 V, IDM 19 A (10 us),
    # AEC-Q101, Tj 175 C; p6 VGS(th) 1.0-2.5 V, RDSon <= 30 mOhm at 10 V / <= 40 mOhm at 4.5 V, QG(tot) 7.8 nC at 10 V.
    "AO3400A": dict(mfr=NXP, mpn="PMV30ENEAR", prefix="Q", pkg="SOT-23", stock=("Transistor_FET", "2N7002"),
                    ds=DS + "power-semiconductors/Nexperia-PMV30ENEA.pdf",
                    desc="N-MOSFET 40 V 4.8 A (19 A pulse), <= 40 mOhm at VGS 4.5 V, AEC-Q101: Miller clamp / SC booster"),
    "BZX84-B3V3,215": dict(mfr=NXP, mpn="BZX84-B3V3", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                       ds=DS + "protection/BZX84.pdf", desc="Zener 3.3 V 2 % 250 mW (clamp-FET gate supply / trigger shift)"),
    "MMBT3904,215": dict(mfr=NXP, mpn="MMBT3904,215", prefix="Q", pkg="SOT-23", stock=("Transistor_BJT", "MMBT3904"),
                     ds=DS + "power-semiconductors/Nexperia-MMBT3904.pdf", desc="NPN 40 V 200 mA (clamp release / SC trigger)"),
    "MMBT3906,215": dict(mfr=NXP, mpn="MMBT3906,215", prefix="Q", pkg="SOT-23", stock=("Transistor_BJT", "MMBT3906"),
                     ds=DS + "power-semiconductors/Nexperia-MMBT3906.pdf", desc="PNP 40 V 200 mA (clamp inverter / SC booster)"),
    "BZX84-B10": dict(mfr=NXP, mpn="BZX84-B10", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                      ds=DS + "protection/BZX84.pdf", desc="Zener 10 V 2 % 250 mW (SC trigger level / booster gate clamp)"),
    "BZX84-B20,215": dict(mfr=NXP, mpn="BZX84-B20,215", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                      ds=DS + "protection/BZX84.pdf", desc="Zener 20 V 2 % 250 mW (gate clamp, positive, +18 V rail)"),
    "BZX84-B4V7": dict(mfr=NXP, mpn="BZX84-B4V7", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                       ds=DS + "protection/BZX84.pdf", desc="Zener 4.7 V 2 % 250 mW (gate clamp, negative, -3/-3.5 V rail)"),
    # Nexperia 2N7002BK (17 Oct 2024): Table 2 p2 pin 1 G, 2 S, 3 D; VGSth 1.1-2.1 V, RDSon <= 2 ohm at 5 V p6.
    "2N7002BK": dict(mfr=NXP, mpn="2N7002BK", prefix="Q", pkg="SOT-23", stock=("Transistor_FET", "2N7002"),
                     ds=DS + "power-semiconductors/2N7002BK.pdf", desc="N-MOSFET 60 V 350 mA logic level"),
    # Diodes Inc ZXTP25040DFH (Oct 2021): p1 pin-out 1 B, 2 E, 3 C (= KiCad BC807); p2 VCEO -40 V, VECO -3 V, VEBO -7 V,
    # IC -3 A, ICM -9 A; p4 hFE >= 30 at -3 A/-2 V, VBE(sat) <= 1.0 V and VCE(sat) <= 220 mV at -3 A/-300 mA, Cobo 17.4 pF.
    # The Q-suffix part is the AEC-Q101 grade. Datasheet read from the URL; PDF not yet in docs/ (download needs approval).
    "ZXTP25040DFH": dict(mfr="Diodes Incorporated", mpn="ZXTP25040DFHTA", prefix="Q", pkg="SOT-23",
                         stock=("Transistor_BJT", "BC807"), ds=DS + "power-semiconductors/ZXTP25040DFH.pdf",
                         desc="PNP 40 V 3 A (9 A pulse) low VCEsat: local Miller-hold follower at each gate"),
    # Samtec TSW through-hole headers (DS_SAMTEC): board-to-board / solder pins.
    "J_LOGIC": dict(mfr="Samtec", mpn="TSW-108-07-G-D", prefix="J", pkg="2x8 2.54 mm THT", ds=DS_SAMTEC,
                    stock=("Connector_Generic", "Conn_02x08_Odd_Even"), desc="Header 2x8 2.54 mm, logic side to power board"),
    "J_GATE_H": dict(mfr="Samtec", mpn="TSW-104-07-G-S", prefix="J", pkg="1x4 2.54 mm THT", ds=DS_SAMTEC,
                     stock=("Connector_Generic", "Conn_01x04"), desc="Header 1x4 2.54 mm, gate/Kelvin source pins"),
    "J_GATE_L": dict(mfr="Samtec", mpn="TSW-106-07-G-S", prefix="J", pkg="1x6 2.54 mm THT", ds=DS_SAMTEC,
                     stock=("Connector_Generic", "Conn_01x06"), desc="Header 1x6 2.54 mm, gate/Kelvin source/NTC pins"),
    "J_HV": dict(mfr="Samtec", mpn="TSW-101-07-G-S", prefix="J", pkg="1x1 2.54 mm THT", ds=DS_SAMTEC,
                 stock=("Connector_Generic", "Conn_01x01"), desc="Single pin, drain sense; isolated by board spacing"),
}
CATALOG = dict(catalog.PARTS, **PARTS)

R_GATE = (2.0, 1.0)                     # Ron, Roff (ohm) for the GM4 module card; checked in design_check()
# DESAT per device voltage class (NSI6651 DESAT pin, p9: internal ICHG source, VDESAT_TH 8.5-9.8 V): DESAT - Rs - n x US1M
# - drain, BAT54S clamp of the pin to COM/VDD, Cblk footprint fitted DNP (c_blk None): string, clamp and pin capacitance
# alone set the blanking. 3 x 1000 V for both classes (fewer diodes = more capacitance = longer blanking).
DESAT_CLASS = {1200: dict(n_dhv=3, rs=100.0, c_blk=None), 1700: dict(n_dhv=3, rs=100.0, c_blk=None)}
DEADTIME = (2.37e3, 100e-12)            # falling-edge stretch R, C on the interlock input (GM4 / Gen-3 turn-off 75 ns;
                                        # 2.21k -> 2.37k for the NSI6651's 70 ns delay-mismatch budget)
DEADTIME_DAB3 = (4.02e3, 100e-12)       # 2-3 x 1200 V discretes per switch: the rev-5 clamps of the slowest set
                                        # (3 x IV3Q12013T4Z) must be on before the complementary turn-on
DEADTIME_PV = (3.40e3, 100e-12)         # 2 x MSC035SMA170B4: long enough that the Miller clamp of the paralleled gates
                                        # is on before the complementary turn-on at the minimum hardware DT (design_check)
DEADTIME_CLASS = {1200: DEADTIME, 1700: DEADTIME_PV}
R_KS = 0.5                              # paralleled devices: per-device Kelvin resistor to the COM star (PVR-05/06)
R_PU = 150.0                            # paralleled devices: OUTH -> CLAMP node, lifts that node at turn-on
R_VL, C_VL = 4.7, 1e-6                  # paralleled devices: local VEE per device (R from VEE, C to its Kelvin pin)
R_SB_CLASS = {1200: 68.0, 1700: 22.0}   # SC booster series R per device (gate -> booster FET -> VEE): sets the two-level
                                        # gate during the driver's reaction and the turn-off di/dt (design_check asserts)
RDR = (1.82e3, 124.0)                   # UCC14241 RLIM1 (source path), RLIM2 + DLIM (sink path), SLUSF09A Case B

# (VDD-COM, COM-VEE) rail settings and everything that depends on the rail: UCC14241 feedback dividers (SLUSF09A
# eq 19/20 p39, 0.1 %), gate clamp Zeners, number of 22u COM-VEE capacitors (K23, eq 4), RDR network (eq 14/15) and the
# default device voltage class (vclass -> DESAT_CLASS / DEADTIME_CLASS). Every row is checked by design_check(). (15, 4) = Wolfspeed Gen-3 / GM4 VGS(op) (C3M0021120K p1, CBB011M12GM4T p1); (20, 4) = Microchip
# MSC035SMA170B4 (recommended 20/-5 V, DS00005160A p3) with the off-bias reduced to -4 V, see REFUSED.
GATE_V = {
    (15, 4): dict(fbvdd=(66.5e3, 10e3), fbvee=(6.04e3, 10e3), zener=("BZX84-B16", "BZX84-B5V6"), c3=2, rdr=RDR,
                  rlim1_pkg="0805", vclass=1200),
    (18, 3): dict(fbvdd=(73.2e3, 10e3), fbvee=(2.00e3, 10e3), zener=("BZX84-B20,215", "BZX84-B4V7"), c3=3,
                  rdr=(1.58e3, 95.3), rlim1_pkg="1206", vclass=None),
    (18, 3.5): dict(fbvdd=(76.8e3, 10e3), fbvee=(4.02e3, 10e3), zener=("BZX84-B20,215", "BZX84-B4V7"), c3=3,
                    rdr=(1.58e3, 97.6), rlim1_pkg="1206", vclass=None),
    (18, 4): dict(fbvdd=(78.7e3, 10e3), fbvee=(6.04e3, 10e3), zener=("BZX84-B20,215", "BZX84-B5V6"), c3=3,
                  rdr=(1.40e3, 97.6), rlim1_pkg="1206", vclass=None),
    (20, 4): dict(fbvdd=(86.6e3, 10e3), fbvee=(6.04e3, 10e3), zener=("BZX84-B22", "BZX84-B5V6"), c3=3,
                  rdr=(1.54e3, 97.6), rlim1_pkg="1206", vclass=1700),
}
REFUSED = {(20, 5): "+20/-5 V needs VDD-VEE = 25.0 V; the UCC14241-Q1 regulates to +/-1.3 % (SLUSF09A p9), i.e. up to "
                    "25.33 V, above its 25 V maximum (p6 ROC, p9 range). Use (20, 4): VDD-VEE 23.8-24.5 V."}

Channel = namedtuple("Channel", "iso xing sec")
ERC_WAIVERS = {"ground_pin_not_ground": "NSI6651ASC pin 3 is named GND2 in the datasheet (p3) but is the isolated driver-side "
                                        "reference = the device's Kelvin source (floating COM net), deliberately not the board "
                                        "GND"}   # merge into the board's build(waivers=...)


def ohm(v):
    return "%gM" % (v / 1e6) if v >= 1e6 else "%gk" % (v / 1e3) if v >= 1e3 else "%gR" % v


def farad(v):
    return "%gu" % (v * 1e6) if v >= 1e-6 else "%gn" % (v * 1e9) if v >= 1e-9 else "%gp" % round(v * 1e12, 3)


def channel(B, tag, pwm, en, flt_n, rdy, gate, source, drain, vdd, vee, interlock=None, ntc=None, apwm=None,
            vin="+24V", vcc="3V3", gnd="GND", gate_v=(15, 4), r_on=R_GATE[0], r_off=R_GATE[1], desat=None, deadtime=None,
            dnp=("apwm_rc",), r_ks=R_KS, vclass=None, r_sb=None, booster=True, v_peak=None, bias="ucc14241",
            lean=True, neg_det=True, stretch=True):
    """One isolated NSI6651ASC channel (D-035), added to Builder B's current sheet as four blocks.

    pwm        logic command (1 = on), 3.3 V CMOS; pulled down here
    interlock  the complementary switch's command (same leg): drives IN- through the dead-time stretcher (None: IN- = GND)
    en         shared RST/EN net (low = gate off; held low >= 0.8 us (tRST_FIL max, p9) it clears a latched DESAT fault)
    flt_n/rdy  shared open-drain nets (caller adds one pull-up each)
    gate       one net (one device or a module: Ron/Roff into it, CLMPI straight to it), or a tuple of nets for
               paralleled devices: each gate gets its own Ron/Roff (r_on/r_off are then PER DEVICE), 10k and Zener
               pair to its own Kelvin net, and the PMEG4010CEJ + ZXTP25040DFH Miller hold on the CLAMP pin.
    source     Kelvin source. One device: one net, it is the driver COM. Paralleled devices: a tuple of the devices'
               own Kelvin nets, same order as gate; each joins the driver COM star (internal net COM_<tag>) through
               r_ks (pulse-rated, default R_KS), so the power-source busbar and the Kelvin traces no longer close a
               loop without resistance. drain (DESAT sense, one common drain), vdd/vee (floating rails, created here)
    gate_v     (VDD-COM, COM-VEE) rail, a key of GATE_V (see the list there; design_check() prints which device suits
               which pair). (20, 5) is refused (REFUSED: outside the UCC14241-Q1 range once its tolerance is counted)
    vclass     device voltage class 1200 / 1700 -> DESAT_CLASS and DEADTIME_CLASS presets; None = the rail's default
    ntc, apwm  NOT SUPPORTED any more: the NSI6651 has no AIN/APWM. Route the thermistor as a plain analog line to the
               CELL connector AN2/AN3 on the power board (D-035); passing either raises ValueError.
    r_on/r_off gate resistors (ohm); desat = dict(n_dhv, rs, c_blk); deadtime = (R, C); None = the class preset
    booster    short-circuit turn-off booster (rev 5): DESAT above the driver's maximum trip -> MMBT3904 -> MMBT3906 charges
               BG (10 V Zener) -> per device AO3400A + r_sb (None = R_SB_CLASS) from the gate to VEE; BG holds ~3 us
    bias       "ucc14241" (default): own UCC14241-Q1 + PG -> RDY. "ext": no bias network - vdd / vee and the COM net
               (source, or the COM star) are the caller's rails, e.g. from bias_phase(); RDY then reports a missing rail
               through the NSI6651's output-side UVLO (VCC2-GND2, p6) and the bias block's 5 V supervisor
    lean       rev 8 parts count (default True): one booster MOSFET per channel (R_sb per gate stays), no unfitted DESAT-off /
               Cblk footprints, 2 x 22u COM-VEE with bias="ext", EN 100p once per EN net and VCC1 1u once per leg (both
               tracked on the Builder). lean=False = the rev 7 netlist
    neg_det    bias="ext": lost-negative-rail detector (MMBT3904 on COM-VEE, 100k from VDD, BAT54 into DESAT) - a lost
               COM-VEE trips DESAT at the next turn-on (FLT, booster holds the gates). Default ON (rev 9 decision): a lost
               rail gives a partial turn-on at every partner edge that DESAT never sees (design_check 'negrail')
    stretch    True (default, D-050): RC + Schmitt falling-edge stretch on IN- = hardware minimum dead time. False: IN-
               wired straight to the complementary command (overlap of the COMMANDS still blocked by IN+ AND NOT IN-);
               the dead time then comes only from the controller's dead-band - see design_check 'dtfw' for the range
               and the residual risk of a firmware dead time of zero
    vcc        documented arrangement (rev 7): vcc = "+5V" - the NSI6651 VINH/VINL are specified at VCC1 = 5 V only (p7,
               VINH <= 3.5 V); drive IN+/EN through input_buffers() (AHCT, VIH 2.0 V) and pull RDY / FLT up to +5 V
    v_peak     the board's switch-node RECURRING peak (V, at the trip corner): asserted <= VIORM of the NSI6651 and the
               UCC14241-Q1 (IC-06). None = not checked here (design_check() checks the PV and DAB values)
    dnp        optional parts fitted DNP: "gclamp" (gate Zeners), "desat" (string DNP, 0R from DESAT to COM fitted:
               DESAT off). "apwm_rc" is accepted and ignored.

    Miller hold with paralleled devices (rev 5): per gate an AO3400A N-MOSFET from the gate pin to a local VEE node
    (C_VL on that device's own Kelvin pin, R_VL from VEE), so the Miller current closes gate -> FET -> C_VL -> Kelvin
    without the Kelvin R or the driver. The FET gates (node CG) are driven to COM (VGS = COM-VEE) by an MMBT3906 inverter
    whose base hangs (BAT54 + 2.2k) on the CLAMP node - a PMEG4010CEJ per gate (anode on the node) makes that node follow
    the lowest gate, so the clamps engage once every gate is below about -1.2 V (device already off) and fully when
    the NSI6651 clamp pulls the node to VEE. An MMBT3904 release switch, fed from OUTH through a 5.6 V Zener (speed-up
    47p), holds CG at VEE while OUTH drives, so the clamps can never fight the turn-on. R_PU from OUTH lifts the node at
    turn-on. The rev-4 PNP follower could not hold the Chinese 1700 V parts (ngspice, sim/gdrv_miller.py).
    design_check() asserts the timing, the FET ratings and the simulated die voltage. Rev 6: the clamp FET behind the
    key "AO3400A" is the 40 V Nexperia PMV30ENEA, gated from VCG = COM + 3.3 V (BZX84-B3V3 from VDD via 10k; a second
    B3V3 in the inverter base path keeps the trigger at about -1 V), so it serves the +18/-3.5 V and +20/-4 V rails.

    PV cell assembly variants (same schematic; only these values differ):
        2 x SG2M040170HJ (primary): gate_v=(18, 3.5) -> FBVDD 76.8k/10k, FBVEE 4.02k/10k, Zeners BZX84-B20 + B4V7,
                                    RLIM1 1.58k; r_on/r_off 3.75/2.5 ohm
        2 x MSC035SMA170B4 (fallback): gate_v=(20, 4) -> FBVDD 86.6k/10k, FBVEE 6.04k/10k, Zeners BZX84-B22 + B5V6,
                                    RLIM1 1.54k; r_on/r_off 6.0/4.0 ohm
        common: vclass=1700, r_sb=22.0, deadtime DEADTIME_PV (3.40k/100p), RLIM2 97.6R, 3 x 22u, DESAT Rs 100R + 3 x US1MH

    Footprint fallback TI UCC21750 (8 kV VIMP): pin 1 is VEE2 on the NSI6651 but AIN on the UCC21750 (net P1: 0R to VEE
    fitted, 0R to COM DNP - swap them for the UCC21750, never fit both); pin 16 is TEST / APWM (1k to GND1 suits both).
    See docs/requirements/GDRV-ALTERNATES.md.
    """
    if gate_v not in GATE_V:
        raise ValueError("gate_v %s: %s" % (gate_v, REFUSED.get(gate_v, "supported rails are %s" % sorted(GATE_V))))
    if v_peak is not None:
        assert v_peak <= min(NSI["viorm"], UCC14241["viorm"]), "channel %s: switch-node peak %.0f V above VIORM" % (tag, v_peak)
    if ntc or apwm:
        raise ValueError("channel %s: the NSI6651ASC has no AIN/APWM - route the thermistor as a plain analog line to the "
                         "CELL connector AN2/AN3 (D-035) and call channel() with ntc=None, apwm=None" % tag)
    gv = GATE_V[gate_v]
    vclass = vclass or gv.get("vclass")
    if vclass not in DESAT_CLASS:
        raise ValueError("channel %s: pass vclass (one of %s) for rail %s" % (tag, sorted(DESAT_CLASS), gate_v))
    desat, deadtime = desat or DESAT_CLASS[vclass], deadtime or DEADTIME_CLASS[vclass]
    r_sb = r_sb or R_SB_CLASS[vclass]
    gates = [gate] if isinstance(gate, str) else list(gate)
    kelv = [source] if isinstance(source, str) else list(source)
    if len(kelv) != len(gates):
        raise ValueError("channel %s: give one Kelvin source net per gate (%d gates, %d sources)" % (tag, len(gates), len(kelv)))
    par = len(gates) > 1
    n = lambda s: "%s_%s" % (s, tag)
    com = n("COM") if par else kelv[0]                 # driver COM: the device's Kelvin net, or the star of the Kelvin Rs
    zms = [n("ZM%d" % i) for i in range(1, len(gates) + 1)] if par else [n("ZM")]
    clmp = n("CLMP") if par else gates[0]
    internal = {n(s) for s in ("INP", "INN", "DTS", "DTB", "PG", "OUTH", "OUTL", "DX", "DY", "FBVDD", "FBVEE",
                               "RLIM", "RLIMD", "CLMP", "CG", "QIB", "QID", "QRB", "QRZ", "BTZ", "BTB", "BTC", "BHB",
                               "BHC", "BG", "VCG", "QIZ", "P1", "P16", "SB", "NDB", "NDX")} | {n("SB%d" % i) for i in
                                                                                            range(1, len(gates) + 1)} | {n("DS%d" % i) for i in range(1, desat["n_dhv"])} | set(zms)
    vls = [n("VEEL%d" % i) for i in range(1, len(gates) + 1)] if par else [None]
    internal |= ({n("COM")} | set(vls)) if par else set()
    clash = internal & {pwm, en, flt_n, rdy, drain, vdd, vee, interlock, ntc, apwm, vin, vcc, gnd, *gates, *kelv}
    assert not clash, "channel %s: caller net names collide with internal nets %s" % (tag, sorted(clash))
    sec = {vdd, vee, com, clmp, n("OUTH"), n("OUTL"), n("DX"), n("DY"), n("FBVDD"), n("FBVEE"), n("RLIM"),
           n("RLIMD"), *gates, *kelv, *zms} | (set(vls) if par else set())
    sec |= {n("P1")} | ({n("CG"), n("QIB"), n("QID"), n("QRB"), n("QRZ"), n("VCG"), n("QIZ")} if par else set()) | ({n("BTZ"), n("BTB"), n("BTC"), n("BHB"),
            n("BHC"), n("BG"), n("SB")} | {n("SB%d" % i) for i in range(1, len(gates) + 1)} if booster else set())
    sec |= {n("NDB"), n("NDX")} if bias == "ext" and neg_det else set()
    xing = []

    B.block("%s: logic side" % tag, "IN+ = own PWM; IN- = complementary switch, falling edge stretched %s/%s\n"
            "(interlock + min dead time = max(firmware, hardware)). Bias PG (active low) pulls RDY low."
            % (ohm(deadtime[0]), farad(deadtime[1])))
    B.R("10k", pwm, gnd, note="pull-down: open input = gate off")
    B.R("100R", pwm, n("INP"))
    B.C("100p", n("INP"), gnd, diel="C0G", tol="5%")
    inn = gnd
    if interlock and not stretch:
        inn = interlock                                             # D-049: cross-wire only, dead time from firmware
    elif interlock:
        inn = n("INN")
        B.R(ohm(deadtime[0]), interlock, n("DTS"))
        B.part("BAT54", {"1": interlock, "2": None, "3": n("DTS")})
        B.C(farad(deadtime[1]), n("DTS"), gnd, diel="C0G", tol="5%")
        B.part("SN74LVC1G17", {"1": None, "2": n("DTS"), "3": gnd, "4": n("DTB"), "5": vcc})
        B.C("100n", vcc, gnd)
        B.R("100R", n("DTB"), inn)
        B.C("100p", inn, gnd, diel="C0G", tol="5%")
    shared = B.__dict__.setdefault("_gdrv_shared", set())
    if not lean or ("vcc1", frozenset({pwm, interlock or pwm})) not in shared:
        B.C("1u", vcc, gnd, volt="16V")                            # lean: once per leg
        shared.add(("vcc1", frozenset({pwm, interlock or pwm})))
    B.C("100n", vcc, gnd)
    if not lean or ("en", en) not in shared:
        B.C("100p", en, gnd, diel="C0G", tol="5%")                 # lean: once per EN net
        shared.add(("en", en))
    if bias != "ext":
        B.R("10k", n("PG"), vcc)
        B.part("2N7002BK", {"1": n("PG"), "2": gnd, "3": rdy})

    u_bias = None
    if bias == "ext":
        for net in (vdd, vee, com):
            B.flag(net)
    else:
        B.block("%s: isolated bias +%g V / -%g V" % (tag, gate_v[0], gate_v[1]),
                "UCC14241-Q1, 24 V in, regulated +/-1.3 %, reinforced barrier.\nRDR: RLIM1 sources COM, RLIM2 + DLIM sink it.")
        u_bias = B.part("UCC14241", dict({"7": vin, "6": vin, "4": vcc, "3": n("PG"), "29": vdd, "28": vdd, "34": n("FBVDD"),
                                          "33": n("FBVEE"), "32": n("RLIM"), "35": vee},
                                         **{str(p): gnd for p in (1, 2, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)},
                                         **{str(p): vee for p in (19, 20, 21, 22, 23, 24, 25, 26, 27, 30, 31, 36)}))
        B.C("10u", vin, gnd, pkg="1210", volt="50V")
        B.C("100n", vin, gnd)
        B.C("2.2u", vdd, vee, pkg="0805", volt="50V")
        B.C("100n", vdd, vee)
        B.R(ohm(gv["fbvdd"][0]), vdd, n("FBVDD"), tol="0.1%")
        B.R(ohm(gv["fbvdd"][1]), n("FBVDD"), vee, tol="0.1%")
        B.C("330p", n("FBVDD"), vee, diel="C0G", tol="5%")
        B.R(ohm(gv["fbvee"][0]), com, n("FBVEE"), tol="0.1%")
        B.R(ohm(gv["fbvee"][1]), n("FBVEE"), vee, tol="0.1%")
        B.C("330p", n("FBVEE"), vee, diel="C0G", tol="5%")
        B.R(ohm(gv["rdr"][0]), n("RLIM"), com, pkg=gv["rlim1_pkg"])
        B.R(ohm(gv["rdr"][1]), n("RLIM"), n("RLIMD"), pkg="0805")
        B.part("BAT54", {"1": com, "2": None, "3": n("RLIMD")})
        for net in (vdd, vee, com):
            B.flag(net)

    zp, zn = gv["zener"]
    clamp = "about +%.0f V / -%.1f V" % (VZ[zp][1] + VF_Z, VZ[zn][1] + VF_Z)
    if len(gates) == 1:
        B.block("%s: driver and gate" % tag, "NSI6651ASC CLAMP direct to the gate (active Miller clamp). Kelvin = COM.\n"
                "Zener pair clamps the gate to %s when the driver cannot." % clamp)
    else:
        B.block("%s: driver and %d gates" % (tag, len(gates)), "Per device: Ron/Roff, %s Kelvin R to the COM star, 10k and "
                "Zeners (%s) on its own Kelvin net.\nMiller hold: N-MOSFET clamp (PMV30ENEA) gate-pin to a local VEE (%s on the "
                "Kelvin pin). LAYOUT: FET + %s at the device, loop <= 1 nH." % (ohm(r_ks), clamp, farad(C_VL), farad(C_VL)))
    u_drv = B.part("NSI6651ASC", {"15": vcc, "10": n("INP"), "11": inn, "14": en, "13": flt_n, "12": rdy, "16": n("P16"),
                                  "9": gnd, "5": vdd, "4": n("OUTH"), "6": n("OUTL"), "7": clmp, "2": n("DX"), "3": com,
                                  "1": n("P1"), "8": vee})
    # footprint fallback TI UCC21750 (pin 1 AIN, pin 16 APWM, else identical): pin 1 to VEE for the NSI6651 (VEE2) or to
    # COM for the UCC21750 (AIN unused; -0.3 V abs vs COM) - fit exactly one; pin 16 1k to GND1 for both (NSI TEST low,
    # UCC21750 APWM <= 3.5 mA of its 20 mA). docs/requirements/GDRV-ALTERNATES.md.
    B.R("0R", n("P1"), vee, note="fit for NSI6651ASC (pin 1 = VEE2)")
    B.R("0R", n("P1"), com, dnp=True, note="fit instead for UCC21750 (pin 1 = AIN unused)")
    B.R("1k", n("P16"), gnd, note="NSI6651 TEST low / UCC21750 APWM load")
    B.C("10u", vdd, com, pkg="1210", volt="50V")
    B.C("100n", vdd, com)
    for _ in range(gv["c3"] - (1 if lean and bias == "ext" else 0)):   # ext + lean: 2 x 22u (ripple: design_check)
        B.C("22u", com, vee, pkg="1210", volt="25V")
    B.C("100n", com, vee)
    for g, ks, zm, vl in zip(gates, kelv, zms, vls):
        B.R(ohm(r_on), n("OUTH"), g, pkg="2512", note="Ron, pulse-rated thick film 1 W")
        B.R(ohm(r_off), n("OUTL"), g, pkg="2512", note="Roff, pulse-rated thick film 1 W")
        B.R("10k", g, ks)
        B.part(zp, {"1": zm, "2": None, "3": g}, dnp="gclamp" in dnp)
        B.part(zn, {"1": zm, "2": None, "3": ks}, dnp="gclamp" in dnp)
        if par:
            B.R(ohm(r_ks), ks, com, pkg="1206", note="Kelvin decoupling, pulse-rated thick film")
            B.part("PMEG4010CEJ", {"1": g, "2": clmp})                  # K gate, A CLAMP node (lowest gate + VF)
            B.part("AO3400A", {"1": n("CG"), "2": vl, "3": g})          # Miller clamp: G CG, S local VEE, D gate
            B.C(farad(C_VL), vl, ks, pkg="0805", volt="50V")
            B.R(ohm(R_VL), vee, vl)
        if booster:
            sbn = n("SB") if lean else n("SB%d" % (gates.index(g) + 1))
            if not lean:
                B.part("AO3400A", {"1": n("BG"), "2": vl or vee, "3": sbn})   # SC booster FET per gate (rev 7)
            B.R(ohm(r_sb), sbn, g, pkg="1206", note="SC booster: sets two-level gate and turn-off di/dt")
    if booster and lean:
        B.part("AO3400A", {"1": n("BG"), "2": vee, "3": n("SB")})          # one booster FET per channel (rev 8)
    if par:
        B.R(ohm(R_PU), n("OUTH"), clmp, note="lifts the CLAMP node at turn-on")
        B.block("%s: Miller clamp control" % tag, "MMBT3906 inverter (E COM, base BAT54+2.2k on CLAMP) drives CG to COM once "
                "the lowest gate is below ~-1.2 V;\nMMBT3904 release (from OUTH via 5.6 V Zener) holds CG at VEE while "
                "OUTH drives.")
        B.R("10k", vdd, n("VCG"), note="clamp-FET gate supply")
        B.part("BZX84-B3V3,215", {"1": com, "2": None, "3": n("VCG")})       # VCG = COM + 3.3 V
        B.C("100n", n("VCG"), com)
        B.part("MMBT3906,215", {"1": n("QIB"), "2": n("VCG"), "3": n("CG")})   # 1 B, 2 E, 3 C
        B.R("10k", n("VCG"), n("QIB"))
        B.R("330R", n("QIB"), n("QID"))
        B.part("BZX84-B3V3,215", {"1": n("QIZ"), "2": None, "3": n("QID")})  # trigger shift: K base side, A to the BAT54
        B.part("BAT54", {"1": n("QIZ"), "2": None, "3": clmp})          # A, K CLAMP node
        B.R("10k", n("CG"), vee)
        B.part("MMBT3904,215", {"1": n("QRB"), "2": vee, "3": n("CG")})     # release switch
        B.R("10k", n("QRB"), vee)
        B.R("1.5k", n("QRZ"), n("QRB"))
        B.C("47p", n("QRZ"), n("QRB"), diel="C0G", tol="5%")
        B.part("BZX84-B5V6", {"1": n("QRZ"), "2": None, "3": n("OUTH")})
    if booster:
        B.block("%s: short-circuit booster" % tag, "DESAT > 10.3-10.9 V (above the 9.8 V maximum trip) -> MMBT3904 -> "
                "MMBT3906 charges BG to VEE + 10 V;\nper device AO3400A + %s pull the gate to VEE; BG holds ~3 us (1k + FET "
                "gates)." % ohm(r_sb))
        B.part("BZX84-B10", {"1": n("BTZ"), "2": None, "3": n("DX")})   # A BTZ, K DESAT pin node
        B.R("22k", n("BTZ"), n("BTB"))
        B.R("100k", n("BTB"), com)
        B.part("MMBT3904,215", {"1": n("BTB"), "2": com, "3": n("BTC")})
        B.R("2.2k", n("BTC"), n("BHB"))
        B.R("10k", n("BHB"), vdd)
        B.part("MMBT3906,215", {"1": n("BHB"), "2": vdd, "3": n("BHC")})
        B.R("220R", n("BHC"), n("BG"))
        B.part("BZX84-B10", {"1": vee, "2": None, "3": n("BG")})        # A VEE, K BG
        B.R("1k", n("BG"), vee)

    B.block("%s: DESAT" % tag, "DESAT pin (internal %.0f uA blanking source, trip 8.5-9.8 V) - Rs - %d x US1M - drain.\n"
            "BAT54S keeps DX between COM and VDD against dv/dt through the string; Cblk footprint DNP."
            % (NSI["ichg"][1] * 1e6, desat["n_dhv"]))
    off = "desat" in dnp
    if not lean or desat["c_blk"]:
        B.C(farad(desat["c_blk"] or 2.2e-12), n("DX"), com, diel="C0G", tol="5%", dnp=desat["c_blk"] is None)
    if not lean or off:
        B.R("0R", n("DX"), com, dnp=not off, note="fitted only with DESAT off")
    B.part("BAT54S", {"1": com, "3": n("DX"), "2": vdd}, rot={1: 270})
    if bias == "ext" and neg_det:      # lost COM-VEE -> NDX to VDD -> DESAT high at the next turn-on (FLT + booster)
        B.part("MMBT3904,215", {"1": n("NDB"), "2": vee, "3": n("NDX")})
        B.R("10k", com, n("NDB"))
        B.R("100k", vdd, n("NDX"), pkg="0805")
        B.part("BAT54", {"1": n("NDX"), "2": None, "3": n("DX")})
    B.R(ohm(desat["rs"]), n("DX"), n("DY"))
    nodes = [n("DY")] + [n("DS%d" % i) for i in range(1, desat["n_dhv"])] + [drain]
    sec |= set(nodes[:-1])
    for a, k in zip(nodes, nodes[1:]):
        xing.append(B.part("US1M", {"2": a, "1": k}, dnp=off).ref)
    return Channel([u_drv.ref] + ([u_bias.ref] if u_bias else []), xing, sec)


# ---------------------------------------------------------------------------------------------- rev 7 helpers
V5_RANGE = (4.75, 5.25)       # live 5 V rail at the SN6505B: +/-5 % ASSUMED (aux supply spec to confirm)
BIAS_XF = dict(n=XF_N, f_min=350e3, r_on=0.25, n_sec=4, c_ps_max=5e-12, c_ss_max=5e-12, pd_free=1750.0, v_rec=1401.0,
               v_dc=1000.0, v_surge=3790.0)   # SN6505B: RON <= 0.25 ohm at 4.5 V (p1), 420 kHz typ (f_min ASSUMED)
VF_BRIDGE = (0.25, 0.45)      # PMEG4010CEJ at ~20 mA (p3: <= 310 mV at 10 mA, <= 390 mV at 100 mA) - interpolated
EXT_DIV = {(18, 3.5): dict(vdd=(77.7e3, 10.2e3), vee=(4.02e3, 10e3)),   # E192 0.1 %: VDD-VEE 21.54 V: VGS(on) inside
                                                                         # 17.5-18.5 V and cell_spec's 17.83-18.56 V
           (20, 4): dict(vdd=(86.6e3, 10.1e3), vee=(6.04e3, 10e3))}       # in its window (design_check)
VGS_WIN = {(18, 3.5): (17.5, 18.5), (20, 4): (19.5, 20.5)}               # cell_spec window; Microchip variant +/-0.5 V
P_CH = {(18, 3.5): 0.42, (20, 4): 0.47}   # W regulated per channel (cost review: 0.273 W gate charge at 32 kHz + VCC2 +
                                          # regulation; +20/-4 V scaled by the 24/21.5 V swing)
VREF431 = (2.475, 2.525)                  # ATL431B, p4


def ext_bias_numbers(gate_v):
    """bias_phase() rails and budget for one rail set: (VDD-VEE, COM-VEE, VGS on, raw min/max, PNP loss, input current)."""
    dv = EXT_DIV[gate_v]
    rat = lambda r, k: r[0] * k / r[1]
    vt = (VREF431[0] * (1 + rat(dv["vdd"], 0.999 / 1.001)), VREF431[1] * (1 + rat(dv["vdd"], 1.001 / 0.999)))
    v3 = (VREF431[0] * (1 + rat(dv["vee"], 0.999 / 1.001)), VREF431[1] * (1 + rat(dv["vee"], 1.001 / 0.999)))
    i_ld = P_CH[gate_v] / vt[0] + (vt[1] - v3[0]) / 4.7e3                  # gate load + COM bias resistor
    i_pri = BIAS_XF["n_sec"] * 30.0 * i_ld / V5_RANGE[0] / 0.9            # per phase, raw ~30 V worst, 90 % transformer
    raw_lo = BIAS_XF["n"] * (V5_RANGE[0] - i_pri * (BIAS_XF["r_on"] + 0.2)) - 2 * VF_BRIDGE[1]   # 0.2 ohm windings ASSUMED
    raw_hi = BIAS_XF["n"] * V5_RANGE[1] - 2 * VF_BRIDGE[0]
    raw_fl = BIAS_XF["n"] * (V5_RANGE[1] - i_pri * BIAS_XF["r_on"]) - 2 * VF_BRIDGE[1]           # max raw at full load
    p_pnp = (raw_fl - vt[0]) * i_ld
    return dict(vt=vt, v3=v3, von=(vt[0] - v3[1], vt[1] - v3[0]), i_ld=i_ld, i_pri=i_pri, raw=(raw_lo, raw_hi), p_pnp=p_pnp,
                p_in=BIAS_XF["n_sec"] * raw_fl * i_ld / 0.9)


def bias_transformer_req():
    """Electrical requirement of T_BIAS4 for the magnetics engineer (written by __main__ to sim/out/gdrv_miller/)."""
    x, hi = BIAS_XF, max(ext_bias_numbers(g)["i_ld"] for g in P_CH)
    return {"part": "T_BIAS4, one per phase (4 channels), driven by TI SN6505B (centre-tapped primary, 420 kHz typ)",
            "input_V": list(V5_RANGE), "f_min_Hz": x["f_min"], "volt_seconds_per_half_primary_Vs": V5_RANGE[1] / (2 * x["f_min"]),
            "turns_ratio_each_secondary_to_half_primary": x["n"], "secondaries": x["n_sec"],
            "secondary_load_per_winding_A_dc": round(hi, 4), "secondary_rms_A_est": round(1.3 * hi, 4),
            "primary_current_A_avg_per_phase": round(max(ext_bias_numbers(g)["i_pri"] for g in P_CH), 3),
            "magnetizing_current_max_A_pk": 0.15, "winding_resistance_max_ohm": {"half_primary": 0.1, "secondary": 2.0},
            "rectifier": "full bridge per secondary (PMEG4010CEJ), raw DC 25.8-30.5 V", "insulation": "FUNCTIONAL (B3, "
            "ARCHITECTURE-COSTFIRST.md section 2): primary <-> each secondary AND secondary <-> secondary",
            "working_V_dc": x["v_dc"], "recurring_peak_V": x["v_rec"], "pd_free_V_pk_min": x["pd_free"],
            "surge_V_1.2_50us": x["v_surge"], "capacitance_max_F": {"primary_to_each_secondary": x["c_ps_max"],
                                                                    "secondary_to_secondary": x["c_ss_max"]},
            "construction_hint": "EP10-class, sectioned bobbin, triple-insulated wire on the secondaries",
            "basis": "gen/gdrv.py ext_bias_numbers() / design_check(); 5 V +/-5 % and f_min ASSUMED"}



def bias_phase(B, ph, chans, gate_v=(18, 3.5), v5="+5V", gnd="GND", bias_en="BIAS_EN", windings=(1, 2, 4, 3)):
    """Per-phase gate bias (rev 7, ARCHITECTURE-COSTFIRST.md section 4 (c)): one SN6505B (BIAS_EN high = on, 10k
    pull-down = off) driving one custom 4-secondary transformer T_BIAS4; per channel a PMEG4010CEJ bridge, a PBSS5540X +
    ATL431 series regulator for VDD-VEE and an ATL431 shunt (4.7k from VDD) that holds COM-VEE - both with the
    UCC14241 divider ratios of GATE_V[gate_v] (2.5 V references), so the channel's rails are those of the UCC14241 rows.
    chans = up to 4 tuples (tag, vdd, com, vee): the nets the caller passes to channel(..., bias="ext"), in the order
    high-side, low-side, high-side, low-side; windings maps them to secondaries S1..S4 so that the two high-side islands
    sit on the outer sections S1 / S4 (XF_C one_apart to the primary) and the BUS- referenced low-side ones on S2 / S3.
    Returns (iso, sec): the transformer ref (a barrier part: put it in the build's isolators) and {tag: set of the
    secondary nets} to merge into that channel's domain."""
    assert 1 <= len(chans) <= BIAS_XF["n_sec"], "bias_phase %s: 1-4 channels" % ph
    dv = EXT_DIV.get(gate_v, dict(vdd=GATE_V[gate_v]["fbvdd"], vee=GATE_V[gate_v]["fbvee"]))
    n = lambda x, t="": "%s_%s%s" % (x, ph, t)
    B.block("%s: gate bias (per phase)" % ph, "SN6505B push-pull 420 kHz from %s, BIAS_EN high = on; T_BIAS4 custom, "
            "functional insulation\n(PD-free >= 1.75 kVpk, <= 5 pF per secondary). Per channel: bridge, PNP regulator "
            "VDD-VEE, ATL431 COM split." % v5)
    B.part("SN6505B", {"2": v5, "5": bias_en, "6": gnd, "4": gnd, "1": n("D1"), "3": n("D2")})
    B.R("10k", bias_en, gnd, note="BIAS_EN open = bias off")
    B.C("10u", v5, gnd, pkg="1206", volt="16V")
    B.C("100n", v5, gnd)
    pins = {"1": n("D1"), "2": v5, "3": n("D2")}
    sec = {}
    for i, (t, vdd, com, vee) in enumerate(chans):
        a, b, raw = n("SA", t), n("SB", t), n("RAW", t)
        w = windings[i] - 1
        pins.update({str(4 + 2 * w): a, str(5 + 2 * w): b})
        for x, k in ((a, raw), (b, raw)):
            B.part("PMEG4010CEJ", {"1": k, "2": x})                    # K raw, A secondary
        for x in (a, b):
            B.part("PMEG4010CEJ", {"1": x, "2": vee})                  # K secondary, A VEE
        B.C("4.7u", raw, vee, pkg="1206", volt="50V")
        rb, rk, rr, cr = n("RB", t), n("RK", t), n("RREF", t), n("CREF", t)
        B.part("PBSS5540X", {"1": raw, "2": vdd, "3": rb})
        B.R("10k", raw, rb)
        B.R("1k", rb, rk)
        B.part("ATL431-GD", {"1": rk, "2": rr, "3": vee})
        B.C("10n", rk, rr, diel="C0G", tol="5%")
        B.R(ohm(dv["vdd"][0]), vdd, rr, tol="0.1%")
        B.R(ohm(dv["vdd"][1]), rr, vee, tol="0.1%")
        B.C("2.2u", vdd, vee, pkg="0805", volt="50V")
        B.R("4.7k", vdd, com, pkg="0805", note="COM bias: sources the ATL431 shunt")
        B.part("ATL431-GD", {"1": com, "2": cr, "3": vee})
        B.R(ohm(dv["vee"][0]), com, cr, tol="0.1%")
        B.R(ohm(dv["vee"][1]), cr, vee, tol="0.1%")
        sec[t] = {a, b, raw, rb, rk, rr, cr}
    for w in set(range(BIAS_XF["n_sec"])) - {windings[i] - 1 for i in range(len(chans))}:
        pins.update({str(4 + 2 * w): None, str(5 + 2 * w): None})
    xf = B.part("T_BIAS4", pins)
    return [xf.ref], sec


def input_buffers(B, pairs, v5="+5V", gnd="GND", part="AHCT1G08"):
    """Documented input arrangement (rev 7): VCC1 = +5 V, every logic input of the NSI6651 (IN+, IN- source, RST/EN)
    through one SN74AHCT1G08 (TTL VIH 2.0 V from the 3.3 V controller, 5 V CMOS out); pairs = [(in_net, out_net,
    gate_net or None)]: Y = in AND gate (gate None -> tied to +5 V). The part key must be in the caller's catalog (the
    cell board's "AHCT1G08": pins 1 A, 2 B, 3 GND, 4 Y, 5 VCC). RDY / FLT pull-ups go to +5 V."""
    for a, y, g in pairs:
        B.part(part, {"1": a, "2": g or v5, "3": gnd, "4": y, "5": v5})
        B.C("100n", v5, gnd)


VIN_RANGE, VCC_RANGE = (21.6, 26.0), (3.135, 3.465)     # system 24 V (gate-drive supply) and the 3.3 V logic rail


def net_ranges(tag, gate_v, vdd, vee, source, gnd="GND", vcc="3V3", vin="+24V", booster=True):
    """DC range (vmin, vmax) of the channel's nets for dcdclib.build(vrange=...): logic-side nets against GND, floating
    nets against the channel's own COM (the Kelvin source / COM star). Switching nets (gates, OUTH/OUTL, CLAMP, DESAT,
    string) have no DC range and are left out (None). Merge the result into the board's own table."""
    vt, v3, v2 = rails(gate_v)
    n = lambda x: "%s_%s" % (x, tag)
    kelv = [source] if isinstance(source, str) else list(source)
    par = len(kelv) > 1
    com = n("COM") if par else kelv[0]
    vee_r = (-v3[1], -v3[0])
    vref = UCC14241["vref"]
    r = {vin: VIN_RANGE, vcc: VCC_RANGE, gnd: (0.0, 0.0), vdd: (v2[0], v2[1]), vee: vee_r, com: (0.0, 0.0),
         n("FBVDD"): (vee_r[0] + vref[0], vee_r[1] + vref[1]), n("FBVEE"): (vee_r[0] + vref[0], vee_r[1] + vref[1])}
    # INP / INN are not ranged: each sits behind a 100R feeding only a CMOS input and 100p, so its DC level IS the driving
    # net's (no DC current) - a range per net would wrongly put 3.5 V across the 100R
    r.update({x: (0.0, VCC_RANGE[1]) for x in (n("DTS"), n("DTB"), n("PG"))})
    r.update({k: (0.0, 0.0) for k in kelv})
    if par:
        r.update({n("VEEL%d" % i): vee_r for i in range(1, len(kelv) + 1)})
        r.update({n("CG"): (vee_r[0], 0.0), n("QIB"): (-1.0, 0.0), n("QID"): (vee_r[0], 0.0)})
    if booster:   # normal-operation DC levels (the fault state lasts ~3 us and is checked as a pulse in design_check)
        r.update({n("BG"): vee_r, n("BHC"): vee_r, n("BHB"): (v2[0], v2[1]), n("BTC"): (v2[0], v2[1]),
                  n("BTB"): (0.0, 0.0), n("BTZ"): (0.0, 0.0)})
    return r


# ---------------------------------------------------------------------------------------------- design checks
# Datasheet limits (document, page). "typ" = the datasheet gives only a typical value.
UCC21710 = dict(vdd_com=(13.0, 33.0), vdd_vee_max=33.0, vdd_on_max=12.8, i_pk=10.0, roh_eff=0.7, rol=0.3,  # p4, p8, p34 (typ)
                ivddq=5.9e-3, ivccq=4.0e-3, psi_jb=32.3, tj_max=150.0, vocth=(0.63, 0.70, 0.77),           # p8, p5, p9
                t_ocoff=(150e-9, 400e-9), t_ocflt=(300e-9, 750e-9), i_sto=(0.25, 0.40, 0.57),           # p9
                i_ain=(196e-6, 203e-6, 209e-6), v_ain=(0.6, 4.5), pwd_skew=60e-9, cmti=150.0,          # p9, p10
                viowm=2121.0, viorm=2121.0, viotm=8000.0, cio=1.0,                                      # p6
                vclmpth=(1.5, 2.0, 2.5), t_dclmpi=(15e-9, 50e-9), vclmpi_1a=0.5, rclmpi=0.6)            # p9 (typ 0.5/0.6)
# NSI66x1A-Q1 (Q1 limits, -40..150 C; industrial grade where it differs): ROC p4; ICC1/ICC2 p6; ROH/ROL, peak 11/12 A at
# VCC2 15 V, clamp VEE2 + 0.8 V at 1 A and threshold 1.5-2.5 V, VINH/VINL (at VCC1 = 5 V only) p7; DESAT p9; timing p9-10;
# insulation p15 (VIMP 6250 Vpk in air per IEC 62368-1, VIOSM 10 kVpk); certificates p18 (all granted).
NSI = dict(vcc2_com=(13.0, 32.0), vcc2_vee_max=32.0, vcc2_vee_abs=35.0, vee_com_abs=-17.5, uvlo_on=(9.8, 11.2, 12.8),
           uvlo_off=(9.0, 10.4, 11.8), icc1=4e-3, icc2=7e-3, roh=2.2, rol=0.3, i_pk=10.0, ioh_pk=11.0, vcc2_pk=15.0,
           vclmpth=(1.5, 2.0, 2.5), vclamp_1a=0.8, t_clamp=50e-9, vinh=(2.5, 2.9, 3.5), vinl=(1.5, 2.1, 2.5),
           vdesat=(8.5, 9.26, 9.8), ichg=(350e-6, 500e-6, 650e-6), t_leb=(150e-9, 200e-9, 250e-9), t_fil=(150e-9, 265e-9),
           t_off=(150e-9, 300e-9), t_flt=(400e-9, 750e-9), t_rst=(480e-9, 800e-9), i_sto={"Q1": (0.10, 0.40, 0.57),
           "industrial": (0.25, 0.40, 0.57)}, tprop=(70e-9, 80e-9, 110e-9), pwd=30e-9, tpw_min=70e-9, cmti=150.0,
           viowm=2121.0, viorm=2121.0, viotm=8000.0, vimp=6250.0, viosm=10000.0, cio=0.8,
           certs="VDE 0884-17 reinforced 40052820, UL 1577 E500602, CQC20001264939: granted (p18)",
           psi_jb=39.0, tj_max=150.0, skew=40e-9 + 30e-9)    # p5 PsiJB; skew = tprop spread 70-110 ns + PWD 30 ns
ROH_EFF = NSI["vcc2_pk"] / NSI["ioh_pk"]  # 1.36 ohm effective pull-up from the 11 A typical peak (p7), for the 10 A check
BAT54S_CD = (2e-12, 6e-12)                # per diode, effective over the 0-9.8 V DESAT charge: datasheet <= 10 pF at 1 V
                                          # (BAT54S p3), falling with VR (Fig 3) - range ASSUMED
C_DESAT_PIN = (2e-12, 4e-12)              # NSI6651 DESAT pin + trace: NOT in the datasheet, ASSUMED
# t_leb is typical-only (200 ns) - +/-25 % ASSUMED; t_clamp (clamp on-delay) is a typical curve only (Fig 6.18 p13) - the
# UCC21710 maximum 50 ns is ASSUMED. ioh_pk/vcc2_pk: effective pull-up for the 10 A check = 15 V / 11 A (p7, typical).
PMEG = dict(cd=77e-12, vf_1a=0.57, vf_10ma=0.31)   # PMEG4010CEJ p3: Cd <= 77 pF at 1 V; VF <= 570 mV at 1 A, 310 at 10 mA
C_PIN = 20e-12                       # CLMPI pin + trace capacitance: NOT in the datasheet, assumed
ZXTP = dict(vceo=40.0, icm=9.0, hfe_3a=30.0, vbe_sat=1.0, vce_sat=0.22, cobo=17.4e-12)   # ZXTP25040DFH p2, p4 (25 C)
VBE_COLD = 0.13                      # VBE rise 25 -> -40 C at -2 mV/K: typical silicon tempco, ASSUMED
V_LAYOUT = 0.3                       # L di/dt of the driver-referenced return (Kelvin/VEE route): ASSUMED allowance
L_LOC = 1e-9                         # gate - clamp FET - C_VL - Kelvin pin loop: layout rule, ASSUMED
DAB_DVDT = 56.7                      # V/ns, sim/out/dab_design/dab_spec.json dvdt_off_V_per_ns (RG_off 0.27 ohm)
UCC14241 = dict(vimp=7692.0, viosm=10000.0, certs="VDE 0884-17 certification PLANNED (p7)", p_85c=2.0, p_105c=1.5, vin=(21.0, 27.0), vdd_vee=(15.0, 25.0), vref=(2.4675, 2.5325),   # p1, p6, p9
                uvp=(2.175, 2.35), ovlo_max=32.55, cmti=150.0, rlim_int=30.0, com_vee_min=2.5,          # p10, p35, p6
                viowm=1414.0, viorm=1414.0, viotm=7071.0, cio=3.5)                                       # p7
LVC1G17 = dict(vtm_ratio=(0.89 / 3.0, 1.20 / 3.0 + (1.97 / 4.5 - 1.20 / 3.0) * 0.465 / 1.5), tpd=(1.5e-9, 4.6e-9),
               ci=4.5e-12)                     # SCES351Y Table 5-1 p6 (3.0 V row, max interpolated to 3.465 V), p7
US1M_VRRM, US1M_VF = 1000.0, (0.5, 1.0)        # DS16008 p2; VF at ~1 mA NOT specified (Fig 2 starts at 10 mA) - assumed
BAT54_VF = (0.10, 0.32)                        # BAT54 p4: <= 320 mV at 1 mA; 0.10 V assumed at end of charge
VZ = {"BZX84-B16": (15.70, 16.30), "BZX84-B22": (21.60, 22.40), "BZX84-B5V6": (5.49, 5.71),   # BZX84 Table 8 p5, 5 mA
      "BZX84-B20,215": (19.60, 20.40), "BZX84-B10": (9.80, 10.20), "BZX84-B4V7": (4.61, 4.79),
      "BZX84-B3V3,215": (3.26, 3.34)}
VF_Z = 0.9                                                                                    # BZX84 Table 7 p4, 10 mA
T_SC = (3.0e-6, "IMZA120R020M1H p3: tSC 3 us at VDD <= 800 V, VGS 15 V, Tvj,start 25 C")
DVDT = 80.0                      # V/ns, GM4 p8 Fig 25 dv/dt_off up to ~80 V/ns at 200 A, RG(off) = 0
NTC = dict(r25=5000.0, tol=0.05, b=3380.0, b_hot=3523.0)       # CBB011M12GM4T p3 (B25/50, B25/100)
# Power devices: data per device, the rail each runs on, gate resistors (per device if per_dev), frequencies, and the
# acceptance windows. CBB011M12GM4T Rev 3: p2 QG/Ciss/RG(int)/RDS(on), p3 package R, p8 Fig 24 timing (read off the
# graph); normal peak 150 A is ASSUMED (60 kW at >= 700 V, peak/avg ~1.75). Its VGS(max) -10/+23 V (p1) is wider
# than Gen-3, so the 15 V rail is held to C3M0016120K's -8/+19 V (transient, p1).
GM4 = dict(name="CBB011M12GM4T", gate_v=(15, 4), vclass=1200, n=1, per_dev=False, r_gate=R_GATE, fsw=(100e3,), qg=405e-9,
           ciss=10.1e-9, rg=1.4, vth_min=1.8, t_off=75e-9, vgs_max=(-10.0, 23.0), v_on=150.0 * (19.8e-3 + 3.18e-3),
           blank=(150e-9, 500e-9), t_sc=T_SC, rds=(11.0e-3 + 3.18e-3, 19.8e-3 + 3.18e-3), idm=400.0,
           crss=36e-12, vth175=1.8 * 2.0 / 2.5)    # p2: Crss 36 pF at 800 V; VGS(th) 1.8 min/2.5 typ, 2.0 typ at 175 C
# C3M0016120K (largest-Qg 1200 V TO-247-4 in docs/): p1 VGS(max), p2 QG 223 nC, Ciss 6922 pF, RG(int) 2.6 ohm,
# td(off) + tf 62 + 13 ns. Bias-power worst case of the 15 V rail, one common Ron/Roff.
C3M16 = dict(name="3 x C3M0016120K", gate_v=(15, 4), vclass=1200, n=3, per_dev=False, r_gate=R_GATE, fsw=(100e3,), qg=223e-9,
             ciss=6.922e-9, rg=2.6, vth_min=1.8, t_off=75e-9, vgs_max=(-8.0, 19.0), v_on=None, blank=(150e-9, 500e-9),
             t_sc=T_SC)
# MSC035SMA170B4 DS00005160A: p2 VGS +23/-10 V static, +25/-12 V transient, VGS(th) >= 1.9 V; p3 recommended 20/-5 V,
# QG 178 nC, Ciss 3300 pF, ESR 0.85 ohm, td(off) 15 + tf 17 ns at RG 4 ohm, SCWT 3.1 us (typical only). PV cell
# (D-007, sim/out/pv_design/cell_spec.json): 2 per switch, 6 / 4 ohm per device, 32 kHz, DESAT 7 V, V_DS 2.4 V at the
# 36.2 A turn-off maximum (65 mOhm at 175 C), detect + soft turn-off <= 2 us, firmware dead time 200 ns. Blanking
# window 250-500 ns: >= 250 ns covers the hard-switched turn-on (td(on) + tr = 14 ns at 4 ohm, p3) plus recovery
# ringing with margin at 6 ohm; <= 500 ns keeps the worst case, not just the nominal, inside the 2 us budget.
MSC = dict(name="2 x MSC035SMA170B4", gate_v=(20, 4), vclass=1700, n=2, per_dev=True, r_gate=(6.0, 4.0), fsw=(32e3, 50e3), qg=178e-9,
           ciss=3.3e-9, rg=0.85, vth_min=1.9, t_off=32e-9, vgs_max=(-12.0, 25.0), v_on=2.4, blank=(250e-9, 500e-9),
           t_sc=(3.1e-6, "MSC035SMA170B4 p3: SCWT 3.1 us TYPICAL at VDS 1200 V, VGS 20 V"), t_resp=2.0e-6,
           trip_nom=(6.5, 7.5), fw_dt=200e-9, vth175=1.9 * 2.5 / 3.2,   # VGS(th) min at 175 C: p2 min x Fig 1-15 typ ratio
           qgd=27e-9, i_m=2.75)    # p3 QGD; I_M per device: sim/out/pv_design/report.md (1.34-2.75 A, 250-1100 V)
# Chinese devices (D-037, cell_spec / dab re-runs). Datasheet pages: sim/data/asia_devices.csv. Hot V_th is typical-only on
# both makers' sheets: V_th(min, 175 C) = V_th(min, 25 C) x V_th(typ, 175 C) / V_th(typ, 25 C) - ASSUMPTION (same rule as the
# PV engineer). No short-circuit withstand in any Chinese datasheet: t_sc 2 us ASSUMED (cell_spec short_circuit), t_resp
# 1.0 us = required DESAT-detection-to-off. i_sc = SC current per device at the rail's VGS(on), ASSUMED 2 x the pulsed
# ID rating (no SC data); v_on = V_DS(on) at the trip/peak current, 175 C. DAB: 3 per switch at 65 kHz; Ron/Roff per
# device are PLACEHOLDERS until the DAB engineer's re-run (the checks re-run with his values).
SC40 = dict(name="2 x SG2M040170HJ", gate_v=(18, 3.5), vclass=1700, n=2, per_dev=True, r_gate=(3.75, 2.5), fsw=(32e3,),
            qg=78e-9, qg_swing=22.0, ciss=2.594e-9, rg=1.4, vth_min=2.5, vth_typ=3.1, vth175=2.5 * 2.4 / 3.1, t_off=60e-9,
            vgs_max=(-8.0, 22.0), vgs_dc=(-8.0, 22.0), vgs_bd=-4.0, v_on=3.13, blank=(250e-9, 1.0),
            t_sc=(2.0e-6, "ASSUMED: no SCWT in the Sichain datasheet"), t_resp=1.0e-6, i_sc=2 * 188.0, v_rated=1700.0,
            v_bus_sc=1100.0, l_loop=20e-9, r_sb=22.0)
IV3Q = dict(name="3 x IV3Q12013T4Z", gate_v=(18, 3.5), vclass=1200, n=3, per_dev=True, r_gate=(4.7, 3.3), fsw=(65e3,),
            qg=187e-9, qg_swing=23.0, ciss=4.774e-9, rg=2.3, vth_min=2.0, vth_typ=2.8, vth175=2.0 * 2.0 / 2.8, t_off=60e-9,
            vgs_max=(-10.0, 23.0), vgs_dc=(-5.0, 20.0), v_on=1.6, blank=(250e-9, 1.0),
            t_sc=(2.0e-6, "ASSUMED: no SCWT in the InventChip datasheet"), t_resp=1.0e-6, i_sc=2 * 367.0, v_rated=1200.0,
            v_bus_sc=950.0, l_loop=15e-9, r_sb=68.0, deadtime=DEADTIME_DAB3)
SC14 = dict(name="2 x SG2M014120LJ", gate_v=(18, 3.5), vclass=1200, n=2, per_dev=True, r_gate=(4.7, 3.3), fsw=(65e3,),
            qg=211e-9, qg_swing=22.0, ciss=5.22e-9, rg=2.6, vth_min=2.3, vth_typ=2.8, vth175=2.3 * 2.2 / 2.8, t_off=60e-9,
            vgs_max=(-8.0, 22.0), vgs_dc=(-8.0, 22.0), vgs_bd=-4.0, v_on=1.66, blank=(250e-9, 1.0),
            t_sc=(2.0e-6, "ASSUMED: no SCWT in the Sichain datasheet"), t_resp=1.0e-6, i_sc=2 * 491.0, v_rated=1200.0,
            v_bus_sc=950.0, l_loop=15e-9, r_sb=56.0, deadtime=DEADTIME_DAB3)   # 3 per switch: no R_sb meets both 1.0 us and 0.85 x 1200 V
for _d, _x in ((GM4, dict(vth_typ=2.5, i_sc=2 * 400.0, v_rated=1200.0, v_bus_sc=950.0, l_loop=16.4e-9, t_resp=1.0e-6, r_sb=15.0)),
               (C3M16, dict(vth_typ=2.5, i_sc=2 * 250.0, v_rated=1200.0, v_bus_sc=950.0, l_loop=20e-9, t_resp=1.0e-6, r_sb=10.0)),
               (MSC, dict(vth_typ=3.2, i_sc=2 * 200.0, v_rated=1700.0, v_bus_sc=1100.0, l_loop=20e-9, qg_swing=25.0,
                          t_resp=1.0e-6, r_sb=22.0, deadtime=DEADTIME_PV, vgs_dc=(-10.0, 23.0)))):   # IDM 200 A, VGS(th) 3.2 typ
    _d.update(_x)
DEVICES = (GM4, C3M16, SC40, MSC, IV3Q, SC14)   # MSC = qualified fallback of the PV cell (assembly variant, +20/-4 V)
# ngspice Miller hold (sim/gdrv_miller.py: PV engineer's physical-leg V_DS(t) at 1100 V / 72.4 A, calibrated VDMOS with the
# datasheet C(V), victim with the rev-5 clamp; die = pin + I_gate x R_G,int). die_l1 = clamp loop 1 nH (layout rule), l0 = 0.
MILLER_SIM = {"2 x SG2M040170HJ": dict(die_l1=1.41, die_l0=0.95, pin_l1=-2.47, i_m=3.22, rev4=2.14, dvdt=115.0),
              "2 x MSC035SMA170B4": dict(die_l1=0.43, die_l0=None, pin_l1=-2.95, i_m=4.52, rev4=1.42, dvdt=85.0)}
# DAB sets, hard-switched turn-on at 950 V (sim/gdrv_miller.py dab_case, 15 nH lumped loop, rev-6 clamp): NOT held at the
# datasheet switching speed - they need a slower aggressor turn-on (open item for the DAB engineer, printed, no assert)
NEG_RAIL_SIM = dict(die=5.13, e_extra=1.574e-3,    # sim/gdrv_miller.py case('lostrail', ..., off-rail 0 V): Sichain pair
                    msc_die=4.55, msc_e_extra=1.882e-3)   # 2 x MSC035SMA170B4 at +20 V, 85 V/ns, V_th(min, 175 C) 1.48 V
MILLER_DAB = {"2 x SG2M014120LJ": "R_G,on 2.2 ohm (100 V/ns) die +3.07 V; 6.6 ohm (53 V/ns) +2.62; 11 ohm (42 V/ns) +2.16; "
                                  "17.5 ohm (35 V/ns, E_on +33 % at 20 A) +1.51 V vs V_th(min, 175 C) 1.81 V: margin 0.30 V - "
                                  "needs <= ~30 V/ns (R_G,on >= ~22 ohm, interpolated) for 0.5 V",
              "3 x IV3Q12013T4Z": "PROXY model (SG2M014120LJ library entry with the IV3Q datasheet C, Q_gd, V_th, R_G,int): die "
                                  "+3.77 V at 97 V/ns, +2.89 V even at 19 V/ns (R_G,on 25 ohm) vs 1.43 V: FAILS at every "
                                  "speed (internal R_G 2.3 ohm x I_M 2.7 A) - not usable with -3.5 V; confirm with a "
                                  "calibrated model, else choose another part"}
MILLER_REJECTED = {"2 x IV2Q17020T4Z": "die +4.00 V (ideal clamp, -3.5 V), +4.25 V (1 nH); -5 V rail +2.37 V; R_G,on x2 "
                                       "(45 V/ns, E_on +7 %) +3.88 V; vs V_th(min, 175 C) 1.47 V: FAILS - not an alternate",
                   "2 x IV2Q17040T4Z": "die +4.41 V (ideal clamp) / +4.69 V (1 nH) vs 1.46 V: FAILS",
                   "1 x SG2M020170HJ": "leg deck did not converge at 1100 V / 72 A; PV engineer rev 4: +3.96 V vs 1.85 V"}
L_CLAMP_MAX = 1e-9   # H, gate pin - AO3400A - C_VL - Kelvin pin loop: LAYOUT RULE (the simulated die voltage assumes it)
AO = dict(vds=40.0, vgs=20.0, vth=(1.0, 1.6, 2.5), idm=19.0, ciss=440e-12, q_on=5.5e-9, rds=40e-3)
# PMV30ENEA p1-p6 (key "AO3400A"); q_on = gate charge to ~6.6 V, ASSUMED from QG(tot) 7.8 nC at 10 V (p6) scaled
V_PEAK = {"PV 1000 V": 1269.0, "PV trip 1100 V": 1396.0, "DAB 1000 V": 1312.0,          # cell_spec, IC-06/IC-15
          "DAB trip (REQUIRED max)": 1400.0}     # DAB engineer must hold the trip-corner recurring peak <= 1414 / 1.01 V
V_PEAK_DAB_TRIP_EST = 1419.0                     # IC-15 estimate at the 1100 V trip: 19 V above the requirement (open)
V_IMP = {"no SPD credit": 8000.0, "drawn SPD credit (IC-13)": 7650.0, "IC-01 option b": 6000.0}   # reinforced impulse req.


def rails(gate_v):
    """VDD-VEE, COM-VEE and VDD-COM ranges from the UCC14241 reference and the 0.1 % dividers of one rail."""
    (t1, b1), (t2, b2) = GATE_V[gate_v]["fbvdd"], GATE_V[gate_v]["fbvee"]
    lo, hi = UCC14241["vref"]
    vt = (lo * (1 + t1 * 0.999 / (b1 * 1.001)), hi * (1 + t1 * 1.001 / (b1 * 0.999)))
    v3 = (lo * (1 + t2 * 0.999 / (b2 * 1.001)), hi * (1 + t2 * 1.001 / (b2 * 0.999)))
    return vt, v3, (vt[0] - v3[1], vt[1] - v3[0])


def desat_numbers(d):
    """NSI6651 DESAT (p9): trip V_DS = VDESAT_TH - ICHG x Rs - n x VF; blanking = tLEB + C x VDESAT_TH / ICHG with C =
    Cblk + string (US1M CJ 10 pF at 4 V / n, +/-20 %) + BAT54S (2 diodes) + pin. Returns trip (min, max), nominal trip,
    blanking (min, max)."""
    n, rs, cb = d["n_dhv"], d["rs"], d["c_blk"] or 0.0
    trip = (NSI["vdesat"][0] - NSI["ichg"][2] * rs * 1.01 - n * US1M_VF[1],
            NSI["vdesat"][2] - NSI["ichg"][0] * rs * 0.99 - n * US1M_VF[0])
    nom = NSI["vdesat"][1] - NSI["ichg"][1] * rs - n * 0.75
    c_lo = cb * 0.95 + 10e-12 / n * 0.8 + 2 * BAT54S_CD[0] + C_DESAT_PIN[0]
    c_hi = cb * 1.05 + 10e-12 / n * 1.2 + 2 * BAT54S_CD[1] + C_DESAT_PIN[1]
    blank = (NSI["t_leb"][0] + c_lo * NSI["vdesat"][0] / NSI["ichg"][2],
             NSI["t_leb"][2] + c_hi * NSI["vdesat"][2] / NSI["ichg"][0])
    return trip, nom, blank


def stretch(deadtime):
    """Falling-edge stretch of the interlock input: RC to the SN74LVC1G17 VT- over VCC 3.3 V +/-5 %, R 1 %, C 5 %."""
    r, c = deadtime
    vcc = (3.135, 3.465)
    t_min = min(r * 0.99 * c * 0.95 * math.log((v - BAT54_VF[1]) / (LVC1G17["vtm_ratio"][1] * v)) for v in vcc)
    t_max = max(r * 1.01 * (c * 1.05 + LVC1G17["ci"]) * math.log((v - BAT54_VF[0]) / (LVC1G17["vtm_ratio"][0] * v))
                for v in vcc)
    return t_min, t_max


def rlim_check(gate_v, v2, v3, qg_f, n_rgs):
    """UCC14241 COM-VEE regulator, SLUSF09A eq 4 (K23), 10-12 (RLIM bounds), 14-15 (RDR) with C2 = 10u (VDD-COM),
    C3 = c3 x 22u (COM-VEE), +/-30 % capacitance (tolerance + DC bias + temperature, assumed). qg_f = worst gate
    charge x frequency on this rail, n_rgs = gate pull-down resistors per channel."""
    gv = GATE_V[gate_v]
    c2, c3, t, rint = 10e-6, 22e-6 * gv["c3"], 0.30, UCC14241["rlim_int"]
    i_vc = NSI["ichg"][2] + n_rgs * v2 / 10e3 + NSI["icc2"]                                    # VDD->COM
    i_ce = v3 / sum(gv["fbvee"])                                       # COM->VEE: FBVEE divider
    sink, src = i_vc - i_ce, n_rgs * v3 / 10e3                         # src: gates held low, pull-downs load COM-VEE
    imax = UCC14241["p_85c"] / (v2 + v3)
    k23 = v2 * (imax - i_ce) / (v3 * (imax - i_vc))
    m_h = abs(c3 * (1 + t) / (c2 * (1 - t) + c3 * (1 + t)) - c3 / (c2 + c3))
    m_l = abs(c2 * (1 + t) / (c2 * (1 + t) + c3 * (1 - t)) - c2 / (c2 + c3))
    r_h = v2 / (m_h * qg_f + src) - rint
    r_l = min(v3 / (m_l * qg_f + sink) - rint, v3 / (c3 * (1 + t) * 0.1 * v3 / 3e-3 + sink) - rint)
    rlim1 = min(3e3, v2 / (c3 * (1 + t) * 0.1 * v3 / 3e-3 + src) - rint)
    rlim2 = (v3 - 0.5) / (v3 * (1 / r_l - 1 / r_h))
    p_rlim = v2 ** 2 / gv["rdr"][0] * 0.33 + sink ** 2 * gv["rdr"][1] + 0.5 * sink   # SLUSF09A eq 16/18 + DLIM
    return k23, c3 / c2, r_h, r_l, rlim1, rlim2, sink, p_rlim


def design_check():
    """Prints the numbers behind the schematic values, asserts them against datasheet limits; returns ({key: line},
    cover lines for the GDRV-HB root sheet)."""
    out, num = {}, {}
    say = lambda k, s, *a: out.__setitem__(k, s % a)
    for gate_v, gv in GATE_V.items():
        k = "%g/%g" % gate_v
        vt, v3, v2 = rails(gate_v)
        own = [d for d in DEVICES if d["gate_v"] == gate_v]       # device presets on this rail (may be none)
        devs = own or list(DEVICES)                                   # RLIM sizing: worst gate charge of any preset
        lim = (max(d["vgs_max"][0] for d in own), min(d["vgs_max"][1] for d in own)) if own else (-8.0, 22.0)
        say("rails" + k, "+%g/-%g V rail (UCC14241, 0.1%% dividers %s/%s and %s/%s): VDD-VEE %.2f-%.2f V (limit 15-25 V), "
            "COM-VEE %.2f-%.2f V -> VGS on %.2f-%.2f V, off -%.2f..-%.2f V", gate_v[0], gate_v[1], ohm(gv["fbvdd"][0]),
            ohm(gv["fbvdd"][1]), ohm(gv["fbvee"][0]), ohm(gv["fbvee"][1]), vt[0], vt[1], v3[0], v3[1], v2[0], v2[1],
            v3[0], v3[1])
        assert UCC14241["vdd_vee"][0] <= vt[0] and vt[1] <= UCC14241["vdd_vee"][1], "VDD-VEE outside UCC14241-Q1 15-25 V"
        assert 0.95 * gate_v[0] <= v2[0] and v2[1] <= 1.05 * gate_v[0], "VGS(on) outside the set point +/-5 %"
        assert v2[0] >= max(NSI["uvlo_on"][2] + 1.0, NSI["vcc2_com"][0]) and v2[1] <= NSI["vcc2_com"][1], "VCC2-GND2 vs NSI"
        assert vt[1] <= NSI["vcc2_vee_max"] and UCC14241["ovlo_max"] < NSI["vcc2_vee_abs"], "VCC2-VEE2 vs NSI ROC 32 / abs 35 V"
        assert -v3[1] >= NSI["vee_com_abs"], "VEE2-GND2 below the NSI absolute -17.5 V"
        assert UCC14241["com_vee_min"] <= v3[0] and v3[1] < -lim[0] and v2[1] < lim[1], "rails vs ROC / VGS(max)"
        ratio = 1 + gv["fbvdd"][0] / gv["fbvdd"][1]
        pg = (UCC14241["uvp"][0] * ratio - v3[1], UCC14241["uvp"][1] * ratio - v3[0])
        say("uvlo" + k, "   UVLO: NSI6651 VCC2-GND2 UVLO on <= %.1f V; UCC14241 PG (90 %% UVP) flags VDD-COM below %.1f-%.1f V "
            "and pulls RDY low via the 2N7002BK%s; +24V at the card must stay %.0f-%.0f V (UCC14241 ROC)",
            NSI["uvlo_on"][2], pg[0], pg[1], "" if gate_v[0] < 18 else " - for an 18-20 V device this is the effective "
            "UVLO (the driver's own 12.8 V one is too low)", *UCC14241["vin"])
        zp, zn = VZ[gv["zener"][0]], VZ[gv["zener"][1]]
        say("clamp" + k, "   gate clamp %s + %s: conducts above +%.2f / below -%.2f V, clamps by +%.2f / -%.2f V (5-10 mA) "
            "vs transient VGS(max) +%.0f / %.0f V", gv["zener"][0], gv["zener"][1], zp[0], zn[0], zp[1] + VF_Z,
            zn[1] + VF_Z, lim[1], lim[0])
        assert zp[0] > v2[1] and zn[0] > v3[1], "Zener clamp would conduct inside the operating gate swing"
        if own:
            assert zp[1] + VF_Z <= lim[1] and zn[1] + VF_Z <= -lim[0], "gate clamp above VGS(max)"
        for d0 in own:   # TIDT256 p.19 (REFERENCE-LESSONS): +0.5 V bias overshoot at enable / VIN ramp vs the ABSOLUTE rating
            dc = d0.get("vgs_dc", d0["vgs_max"])
            assert v2[1] + 0.5 <= dc[1] and v3[1] + 0.5 <= -dc[0], "%s: rail + 0.5 V overshoot above the absolute VGS" % d0["name"]
            assert v3[1] <= -d0.get("vgs_bd", -99.0), "%s: off-rail below the body-diode VGS limit" % d0["name"]
        say("devok" + k, "   devices on this rail: %s (rail max + 0.5 V start-up overshoot vs DC/abs VGS: +%.2f / -%.2f V)",
            ", ".join(d0["name"] for d0 in own) or "none (rail checked for the UCC14241 and the NSI6651 only)", v2[1] + 0.5,
            v3[1] + 0.5)
        qg_f = max(d["qg"] * d["n"] * max(d["fsw"]) for d in devs)
        n_rgs = max(d["n"] if d["per_dev"] else 1 for d in devs)
        k23, c_ratio, r_h, r_l, rlim1, rlim2, sink, p_rlim = rlim_check(gate_v, v2[1], v3[1], qg_f, n_rgs)
        say("rlim" + k, "   RLIM (SLUSF09A eq 4, 10-15): K23 %.2f vs C3/C2 %.1f (%d x 22u / 10u); bounds H %.0f / L %.0f ohm "
            "-> RLIM1 <= %.0f, RLIM2 <= %.0f; fitted %s / %s, loss %.0f mW", k23, c_ratio, gv["c3"], r_h, r_l, rlim1,
            rlim2, ohm(gv["rdr"][0]), ohm(gv["rdr"][1]), p_rlim * 1e3)
        assert 0.9 * k23 <= c_ratio <= 1.3 * k23, "COM-VEE / VDD-COM capacitor ratio off K23"
        assert gv["rdr"][0] <= rlim1 and gv["rdr"][1] <= rlim2, "RDR resistors above the SLUSF09A eq 14/15 limits"
        p_list = (("RLIM1 " + gv["rlim1_pkg"], v2[1] ** 2 / gv["rdr"][0] * 0.33, {"0805": 0.125, "1206": 0.25}[gv["rlim1_pkg"]]),
                  ("RLIM2 0805", sink ** 2 * gv["rdr"][1], 0.125))
        for name, p_w, rating in p_list:
            assert p_w <= 0.5 * rating, "%s dissipates %.0f mW" % (name, p_w * 1e3)
        say("rpower" + k, "   resistor dissipation (worst case): %s - all <= 50 %% of rating",
            ", ".join("%s %.0f mW" % (nm, p_w * 1e3) for nm, p_w, _ in p_list))
        num[gate_v] = dict(v2=v2, v3=v3, p_rlim=p_rlim)

    for dev in DEVICES:
        gv, k, n = GATE_V[dev["gate_v"]], dev["name"], dev["n"]
        vt, v3, v2 = rails(dev["gate_v"])
        vtm, (ron, roff) = vt[1], dev["r_gate"]
        # path resistance seen by the driver (SLUSD43B eq 1/5): per-device resistors split n ways, a common one does not
        rks = R_KS if dev["per_dev"] else 0.0                  # paralleled devices: Kelvin R in every gate loop
        r_on_t = ROH_EFF + ((ron + rks + dev["rg"]) / n if dev["per_dev"] else ron + dev["rg"] / n)
        r_off_t = NSI["rol"] + ((roff + rks + dev["rg"]) / n if dev["per_dev"] else roff + dev["rg"] / n)
        share = 1.0 / n ** 2 if dev["per_dev"] else 1.0             # one external resistor's share of the path loss
        n_rgs = n if dev["per_dev"] else 1
        i_src, i_snk = vtm / r_on_t + (vtm / R_PU if dev["per_dev"] else 0.0), vtm / r_off_t   # + OUTH-R_PU-gates
        rec = num[k] = dict(p_bias={}, ipk=(i_src, i_snk))
        for f in dev["fsw"]:
            e_sw = dev["qg"] * n * vtm * f                          # gate power drawn from the bias, W
            p_ron, p_roff = 0.5 * e_sw * ron * share / r_on_t, 0.5 * e_sw * roff * share / r_off_t
            p_drv = NSI["icc2"] * vtm + NSI["icc1"] * 3.465 + 0.5 * e_sw * (ROH_EFF / r_on_t + NSI["rol"] / r_off_t)
            tj = 125.0 + NSI["psi_jb"] * p_drv                       # PsiJB 39 C/W (p5), board at 125 C
            p_bias = (e_sw + NSI["icc2"] * vtm + NSI["ichg"][2] * v2[1] + n_rgs * v2[1] ** 2 / 10e3
                      + vtm ** 2 / sum(gv["fbvdd"]) + v3[1] ** 2 / sum(gv["fbvee"]) + num[dev["gate_v"]]["p_rlim"])
            rec["p_bias"][f] = p_bias
            say("bias%s%d" % (k, f), "%s @ %.0f kHz, +%g/-%g V: Qg %.0f nC x %.2f V -> gate %.2f W; bias %.2f W vs 2.0 W "
                "(TA<=85 C) margin %.2f, vs 1.5 W (105 C) margin %.2f; Ron %.2f W, Roff %.2f W each (2512, 1 W); "
                "driver %.2f W, TJ %.0f C at 125 C board", k, f / 1e3, dev["gate_v"][0], dev["gate_v"][1], dev["qg"] * n * 1e9,
                vtm, e_sw, p_bias, UCC14241["p_85c"] / p_bias, UCC14241["p_105c"] / p_bias, p_ron, p_roff, p_drv, tj)
            assert max(p_ron, p_roff) <= 0.5, "gate resistor above 50 % of a 1 W 2512 rating"
            p_rks = 0.5 * e_sw * rks * share * (1 / r_on_t + 1 / r_off_t)
            assert p_rks <= 0.125, "Kelvin resistor above 50 % of a 1206 rating"
            assert tj <= NSI["tj_max"], "NSI6651 junction above 150 C"
            assert UCC14241["p_85c"] / p_bias >= 1.2, "bias margin below 1.2 at 85 C"
            if dev is not C3M16:
                assert UCC14241["p_105c"] / p_bias >= 1.25, "bias margin below 1.25 at 105 C"
        extra = ""
        if dev["per_dev"]:
            i_bare = vtm / ((roff + rks + dev["rg"]) / n)
            extra = ("; with ROL not credited sink %.1f A - Roff >= %.2f ohm per device keeps that <= 10 A too"
                     % (i_bare, vtm / NSI["i_pk"] * n - dev["rg"] - rks))
        say("ipk" + k, "   peak gate current (NSI6651: pull-up 15 V / 11 A = 1.36 ohm, ROL 0.3 ohm, p7 typ; %s): source %.1f A, "
            "sink %.1f A "
            "(<= 10 A)%s", "%g/%g ohm + %g ohm Kelvin R per device x %d" % (ron, roff, rks, n) if dev["per_dev"]
            else "%g/%g ohm" % (ron, roff), i_src, i_snk, extra)
        assert max(i_src, i_snk) <= NSI["i_pk"], "peak gate current above the NSI6651 10 A rating"

        d = DESAT_CLASS[dev["vclass"]]
        (vlo, vhi), nom, (tlo, thi) = desat_numbers(d)
        say("desat" + k, "   DESAT (NSI6651 pin, Rs %s, %d x US1M, Cblk DNP): trip at V_DS %.2f-%.2f V (nominal %.2f V), string "
            ">= %.2f mA at trip; blanking %.0f-%.0f ns (>= %.0f ns needed)", ohm(d["rs"]), d["n_dhv"], vlo, vhi, nom,
            NSI["ichg"][0] * 1e3, tlo * 1e9, thi * 1e9, dev["blank"][0] * 1e9)
        assert dev["blank"][0] <= tlo, "blanking shorter than the device turn-on + ringing"
        if dev["v_on"]:
            assert vlo >= 1.5 * dev["v_on"], "DESAT could trip in normal operation"
        if "trip_nom" in dev:
            assert dev["trip_nom"][0] <= nom <= dev["trip_nom"][1], "nominal DESAT trip off the specified V_DS"
        if "rds" in dev:
            say("trip" + k, "   %s: normal peak 150 A (assumed) at 175 C = %.1f V; trip current %.0f-%.0f A at 25 C, %.0f-%.0f A "
                "at 175 C (IDM %.0f A)", k, dev["v_on"], vlo / dev["rds"][0], vhi / dev["rds"][0], vlo / dev["rds"][1],
                vhi / dev["rds"][1], dev["idm"])
        elif dev["v_on"]:
            say("trip" + k, "   %s: on-state %.1f V at the turn-off maximum (cell_spec) -> trip margin %.1f x", k, dev["v_on"],
                vlo / dev["v_on"])
        sc = {}
        for grade, isto in NSI["i_sto"].items():                       # SC: V_DS stays high, no Miller step
            t_sto = n * dev["ciss"] * (v2[1] - dev["vth_min"]) / isto[0]
            sc[grade] = thi + NSI["t_fil"][1] + NSI["t_off"][1] + t_sto
        t_tot = None
        say("sc" + k, "   %s short circuit (internal soft turn-off only, PROVISIONAL): blank %.0f + filter %.0f + DESAT-OUT %.0f "
            "ns + %d x Ciss %.1f nF at ISTO min -> %.2f us (-Q1, 100 mA) / %.2f us (industrial, 250 mA) vs %.1f us [%s]", k,
            thi * 1e9, NSI["t_fil"][1] * 1e9, NSI["t_off"][1] * 1e9, n, dev["ciss"] * 1e9, sc["Q1"] * 1e6,
            sc["industrial"] * 1e6, dev["t_sc"][0] * 1e6, dev["t_sc"][1])
        dtc = dev.get("deadtime", DEADTIME_CLASS[dev["vclass"]])
        t_min, t_max = stretch(dtc)
        g_min = t_min + LVC1G17["tpd"][0] - NSI["skew"]
        g_max = t_max + LVC1G17["tpd"][1] + NSI["skew"]
        fw = t_max + LVC1G17["tpd"][1]
        note = ""
        if "fw_dt" in dev:
            note = "; firmware DT %.0f ns is extended by at most %.0f ns" % (dev["fw_dt"] * 1e9,
                                                                             max(0.0, fw - dev["fw_dt"]) * 1e9)
        say("dt" + k, "   dead time (stretch %s/%s, firmware DT = 0): %.0f-%.0f ns at the gates vs turn-off %.0f ns + 20 ns; "
            "a firmware DT >= %.0f ns is never extended%s", ohm(dtc[0]), farad(dtc[1]), g_min * 1e9,
            g_max * 1e9, dev["t_off"] * 1e9, fw * 1e9, note)
        assert g_min >= dev["t_off"] + 20e-9, "hardware dead time below device turn-off + 20 ns"
        if dev["per_dev"]:
            # Miller hold (rev 5): per-gate AO3400A from the gate pin to a local VEE on the Kelvin pin. The MMBT3906 inverter
            # (E COM, base BAT54 + 2.2k on the CLAMP node = lowest gate + PMEG VF) conducts once the gates are below about
            # -1.2 V; it then charges n x Ciss(AO3400A) to COM at >= 100 mA (hFE >= 100 at ~1 mA base). Gate fall: QG over its
            # test swing as one capacitance through Roff + R_KS + ESR + n x ROL.
            tau_off = (roff * 1.01 + rks * 1.01 + dev["rg"] + n * NSI["rol"]) * dev["qg"] / dev.get("qg_swing", 25.0)
            vgs_cl = VZ["BZX84-B3V3,215"][0] + v3[0] - 0.2                      # CG high = VCG = COM + 3.3 V
            t_eng = tau_off * math.log(vt[1] / (v3[0] - 1.2)) + n * AO["q_on"] / 0.2   # MMBT3906 ~200 mA (330R base)
            t_rel = 20e-9 + n * AO["q_on"] / 0.2                               # release MMBT3904 (1.5k base) ~200 mA
            vds_st, vds_tr = v2[1] + v3[1], VZ[gv["zener"][0]][1] + VF_Z + v3[1]
            sim = MILLER_SIM.get(k)
            say("mclamp" + k, "   Miller hold (rev 5, %d x AO3400A at the gate-Kelvin pins, MMBT3906 inverter on CLAMP, MMBT3904 "
                "release): clamps on <= %.0f ns after the turn-off command vs earliest complementary turn-on %.0f ns; released "
                "<= %.0f ns after OUTH rises; clamp FET (PMV30ENEA, key AO3400A) VGS %.2f V (RDS(on) <= 40 mOhm at 4.5 V), "
                "VDS %.1f V static / %.1f V at the Zener clamp (40 V). %s", n, t_eng * 1e9, g_min * 1e9, t_rel * 1e9, vgs_cl,
                vds_st, vds_tr,
                ("ngspice (sim/gdrv_miller.py, 1100 V / 72.4 A, %.0f V/ns, clamp 80 mOhm hot): die %+.2f V with a %.0f nH clamp "
                 "loop (rev 4 %+.2f V) at I_M %.2f A vs V_th(min, 175 C) %.2f V (assumed typ-ratio) -> margin %.2f V"
                 % (sim["dvdt"], sim["die_l1"], L_CLAMP_MAX * 1e9, sim["rev4"], sim["i_m"], dev["vth175"],
                    dev["vth175"] - sim["die_l1"])) if sim else MILLER_DAB.get(k,
                "NOT re-simulated (no device model): run sim/gdrv_miller.py before release."))
            assert t_eng <= g_min, "Miller clamps not on before the earliest complementary turn-on (minimum hardware DT)"
            assert t_rel <= 110e-9, "clamp release too slow at turn-on (adds to the turn-on delay)"
            assert 4.5 <= vgs_cl and VZ["BZX84-B3V3,215"][1] + v3[1] <= 0.8 * AO["vgs"], "clamp FET VGS outside 4.5 V .. 80 %"
            assert vds_st <= 0.8 * AO["vds"] and vds_tr <= AO["vds"], "clamp FET VDS above 80 % static / 100 % transient"
            if sim:
                assert sim["die_l1"] <= dev["vth175"] - 0.5, "die gate-source within 0.5 V of V_th(min, 175 C)"
                assert sim["i_m"] <= AO["idm"] / 2, "Miller current above IDM/2 of the clamp FET"
        # short-circuit booster (rev 5): until the driver reacts (filter + DESAT-to-OUT max) OUTH and the booster divide
        # the rail -> two-level gate vg2; then the booster discharges the gates through R_sb + R_G,int. SC current scales
        # with (VGS - V_th)^2 from i_sc at VGS(on) (ASSUMED square law); overshoot = L_loop x n x I_sc / t_fall.
        r_sb = dev.get("r_sb", R_SB_CLASS[dev["vclass"]])
        nd = n if dev["per_dev"] else 1
        r_up = ron + rks + n * ROH_EFF if dev["per_dev"] else ron + ROH_EFF
        c_g = dev["ciss"] if dev["per_dev"] else n * dev["ciss"]
        rgi = dev["rg"] if dev["per_dev"] else dev["rg"] / n
        vg2 = -v3[0] + vt[1] * r_sb / (r_sb + r_up)
        t_drv = NSI["t_fil"][1] + NSI["t_off"][1]
        t_f = (r_sb + rgi) * c_g * math.log((vg2 + v3[0]) / (dev["vth_typ"] + v3[0]))
        i_sc2 = n * dev["i_sc"] * max(0.0, (vg2 - dev["vth_typ"]) / (v2[1] - dev["vth_typ"])) ** 2
        v_pk = dev["v_bus_sc"] + dev["l_loop"] * i_sc2 / t_f
        dx_on = dev["v_on"] + d["n_dhv"] * US1M_VF[1] + NSI["ichg"][2] * d["rs"] if dev["v_on"] else 0.0
        thr = (VZ["BZX84-B10"][0] + 0.5, VZ["BZX84-B10"][1] + 0.75)               # DESAT level that fires the booster
        t_hold = 1e3 * nd * AO["ciss"] * math.log(VZ["BZX84-B10"][0] / AO["vth"][2])
        e_rsb = (vt[1] * r_sb / (r_sb + r_up)) ** 2 / r_sb * t_drv
        say("boost" + k, "   SC booster (%s per device): OUTH drives %.0f ns after DESAT detection -> two-level gate %+.1f V "
            "(SC current %.0f A in total, from %.0f A per device at VGS(on), assumed); then off in %.0f ns -> detection to off "
            "%.2f us vs %.1f us; turn-off overshoot %.0f V + %.0f nH x %.0f A / %.0f ns = %.0f V vs 0.85 x %.0f V = %.0f V; "
            "trigger at DESAT %.1f-%.1f V (driver trip <= 9.8 V; normal on-state DESAT <= %.1f V); hold %.1f us; R_sb pulse "
            "%.1f uJ", ohm(r_sb), t_drv * 1e9, vg2, i_sc2, dev["i_sc"], t_f * 1e9, (t_drv + t_f) * 1e6,
            dev["t_resp"] * 1e6, dev["v_bus_sc"], dev["l_loop"] * 1e9, i_sc2, t_f * 1e9, v_pk, dev["v_rated"],
            0.85 * dev["v_rated"], thr[0], thr[1], dx_on, t_hold * 1e6, e_rsb * 1e6)
        assert t_drv + t_f <= dev["t_resp"], "short-circuit detection-to-off above the required time"
        assert v_pk <= 0.85 * dev["v_rated"], "short-circuit turn-off overshoot above 0.85 x V_DS rating"
        assert thr[0] >= NSI["vdesat"][2] + 0.3 and thr[0] - dx_on >= 2.0 and thr[1] <= v2[0] - 2.0, "booster trigger level"
        assert t_hold >= 2 * t_f and e_rsb <= 100e-6, "booster hold time / R_sb pulse energy"
        sc["booster"] = t_drv + t_f
        if "crss" in dev:
            # single device: CLAMP direct to the gate (NSI: VEE2 + 0.8 V at 1 A, taken as 0.8 ohm - linear beyond 1 A
            # ASSUMED) in parallel with Roff + ROL; I_M = Crss(800 V) x dv/dt (understates the low-voltage Cgd peak)
            r_r, hold, r_c = roff * 0.99 + NSI["rol"], [], NSI["vclamp_1a"]
            for dvdt in (DAB_DVDT, DVDT):
                im = dev["crss"] * dvdt * 1e9
                pin = -v3[0] + im * r_r * r_c / (r_r + r_c)
                hold.append((im, dvdt, pin, pin + im * dev["rg"]))
            say("mgm4" + k, "   Miller hold, single device (CLMPI direct + Roff %g ohm): I_M = Crss(800 V) x dv/dt = %.1f A "
                "at %.1f V/ns (DAB) / %.1f A at %.0f V/ns (max): pin %.2f / %.2f V, die (RG(int) %.1f ohm) %.2f / %.2f V "
                "vs VGS(th) min 1.8 V at 25 C, %.2f V at 175 C (derived)", roff, hold[0][0], hold[0][1], hold[1][0],
                hold[1][1], hold[0][2], hold[1][2], dev["rg"], hold[0][3], hold[1][3], dev["vth175"])
            assert hold[0][3] <= dev["vth175"] - 0.5, "module die gate-source within 0.5 V of VGS(th) min at the DAB dv/dt"
        rec.update(trip=(vlo, vhi), blank=(tlo, thi), t_sc=sc["booster"], dt=(g_min, g_max, fw))

    n_v = min(d["n_dhv"] for d in DESAT_CLASS.values()) * US1M_VRRM
    say("string", "DESAT string >= %d x US1M = %.0f V blocking vs 1700 V devices (margin %.2f) on the 1000 V bus",
        n_v / US1M_VRRM, n_v, n_v / 1700)
    assert n_v >= 1.5 * 1700, "DESAT string blocking below 1.5 x 1700 V"
    say("no_tsc", "Wolfspeed CBB011M12GM4T / C3M0016120K / C3M0021120K datasheets give NO short-circuit withstand time; "
        "MSC035SMA170B4 gives 3.1 us as a typical value only")
    say("barrier", "Barrier vs 1000 V DC bus: NSI6651 reinforced VIOWM %.0f VDC / VIORM %.0f Vpk / VIOTM %.0f Vpk "
        "(certified, VDE cert 40052820); UCC14241-Q1 reinforced VIOWM %.0f VDC / VIORM %.0f Vpk / VIOTM %.0f Vpk "
        "(certification PLANNED per datasheet); CMTI >= %.0f V/ns both vs <= %.0f V/ns expected (margin %.1f); CIO %.0f "
        "+ %.1f pF per channel", NSI["viowm"], NSI["viorm"], NSI["viotm"], UCC14241["viowm"],
        UCC14241["viorm"], UCC14241["viotm"], NSI["cmti"], DVDT, NSI["cmti"] / DVDT, NSI["cio"], UCC14241["cio"])
    assert min(NSI["viowm"], UCC14241["viowm"]) >= 1000.0 * 1.2, "barrier working voltage below 1.2 x 1000 V"
    # IC-06: VIORM of both barrier parts vs the switch-node RECURRING peak; IC-01/02/03/13: VIMP vs the reinforced impulse
    for part, lim_ in (("NSI6651ASC", NSI), ("UCC14241-Q1", UCC14241)):
        say("viorm_" + part, "%s VIORM %.0f Vpk vs switch-node recurring peak: %s; VIMP %.0f Vpk / VIOSM %.0f Vpk vs reinforced "
            "impulse %s; %s", part, lim_["viorm"], ", ".join("%s %.0f V (margin %.2f)" % (kk, vv, lim_["viorm"] / vv)
                                                             for kk, vv in V_PEAK.items()),
            lim_["vimp"], lim_["viosm"], ", ".join("%s %.0f V (%s)" % (kk, vv, "ok" if lim_["vimp"] >= vv else "FAIL")
                                                   for kk, vv in V_IMP.items()), lim_["certs"])
        assert lim_["viorm"] >= 1.01 * max(V_PEAK.values()), "%s VIORM below 1.01 x a switch-node recurring peak" % part
        assert lim_["viosm"] >= 10000.0, "%s VIOSM below 10 kV (reinforced surge)" % part
    say("blockers", "RELEASE BLOCKERS (insulation, owner decision IC-01): NSI6651ASC VIMP 6250 Vpk passes the reinforced impulse "
        "only with SPDs at IC-01 option b (6.0 kV); UCC14241-Q1 VIMP 7692 Vpk needs the drawn SPD credit (7.65 kV, 0.5 %%); "
        "UCC14241-Q1 VIORM 1414 Vpk vs DAB trip-corner peak: requirement <= %.0f V (1 %% margin), IC-15 estimate %.0f V (open, "
        "DAB engineer); UCC14241-Q1 VDE certificate still planned", V_PEAK["DAB trip (REQUIRED max)"], V_PEAK_DAB_TRIP_EST)
    for kk, vv in MILLER_DAB.items():
        say("dabmiller" + kk, "OPEN (DAB engineer) - Miller hold %s at 950 V hard-switched: %s", kk, vv)
    assert min(NSI["cmti"], UCC14241["cmti"]) >= 1.5 * DVDT, "CMTI below 1.5 x expected dv/dt"
    say("mgj2", "Rejected bias module: Murata MGJ2D241505SC is reinforced only to 150 Vrms (UL60950) and 2.4 kVDC "
        "functional - not a reinforced barrier at 1000 V. Rejected rail: %s", REFUSED[(20, 5)])
    for g in P_CH:                     # rev 7/8: bias="ext" rails from bias_phase() (functional insulation, no VIMP blocker)
        e = ext_bias_numbers(g)
        devs_g = [d0 for d0 in DEVICES if d0["gate_v"] == g]
        win = VGS_WIN[g]
        i_hp, i_hl = XF_C["high_side_to_primary"] * 124e9, XF_C["adjacent"] * 124e9
        q_ev = max([d0["n"] * d0["qg"] for d0 in devs_g] or [0.0])
        rip = q_ev / ((GATE_V[g]["c3"] - 1) * 22e-6 * 0.7)          # lean ext: 2 x 22u at 70 % (DC bias) per gate event
        say("ext%g/%g" % g, "bias='ext' +%g/-%g V (bias_phase: SN6505B, T_BIAS4 %s %s 1:%g, PBSS5540X + ATL431 %s/%s, ATL431 "
            "COM split %s/%s): VDD-VEE %.2f-%.2f V, COM-VEE %.2f-%.2f V -> VGS on %.2f-%.2f V in %.1f-%.1f V (margins %.2f / "
            "%.2f V); raw %.1f-%.1f V (regulator headroom %.2f V, bridge VR <= %.1f V of 40 V); PNP %.0f mW; per phase %.2f W "
            "from 5 V = %.2f A (SN6505B 1 A); 2 x 22u COM-VEE ripple %.0f mV per %.0f nC gate event; common mode per high-side "
            "channel at 124 V/ns: %.2f A to the primary (%.2f pF) + %.2f A to its leg's low-side island (adjacent %.2f pF) + "
            "0.10 A NSI6651 (0.8 pF), all returning to BUS- locally; insulation FUNCTIONAL (PD-free >= %.0f Vpk vs %.0f V "
            "recurring); creepage on the coated board ASSUMED 3.2 mm (IEC 60664-3 type 1 -> PD1, 1250 V), 5.0 mm uncoated",
            g[0], g[1], _XF["core"]["maker"], _XF["core"]["part_number"].split()[0], XF_N, ohm(EXT_DIV[g]["vdd"][0]),
            ohm(EXT_DIV[g]["vdd"][1]), ohm(EXT_DIV[g]["vee"][0]), ohm(EXT_DIV[g]["vee"][1]), e["vt"][0], e["vt"][1],
            e["v3"][0], e["v3"][1], e["von"][0], e["von"][1], win[0], win[1], e["von"][0] - win[0], win[1] - e["von"][1],
            e["raw"][0], e["raw"][1], e["raw"][0] - e["vt"][1], e["raw"][1], e["p_pnp"] * 1e3, e["p_in"],
            e["p_in"] / V5_RANGE[0], rip * 1e3, q_ev * 1e9, i_hp, XF_C["high_side_to_primary"] * 1e12, i_hl,
            XF_C["adjacent"] * 1e12, BIAS_XF["pd_free"], BIAS_XF["v_rec"])
        assert e["raw"][0] - e["vt"][1] >= 0.5, "bias regulator headroom below 0.5 V at 4.75 V"
        assert e["raw"][1] <= 0.8 * 40.0 and e["raw"][1] <= 36.0 * 0.9, "bridge diode / ATL431 cathode voltage"
        assert e["p_pnp"] <= 0.25 and e["p_in"] / V5_RANGE[0] <= 0.7 * 1.0, "PNP loss / SN6505B current"
        assert e["von"][0] - win[0] >= 0.1 and win[1] - e["von"][1] >= 0.1, "VGS(on) band not inside its window by 0.1 V"
        assert e["von"][0] >= NSI["uvlo_on"][2] + 1.0, "VCC2-GND2 too close to the NSI6651 UVLO"
        assert rip <= 0.02 * e["v3"][0], "COM-VEE ripple above 2 % with 2 x 22u"
        for d0 in devs_g:
            dc = d0.get("vgs_dc", d0["vgs_max"])
            assert e["von"][1] + 0.5 <= dc[1] and e["v3"][1] + 0.5 <= -dc[0], "%s: ext rail + 0.5 V above abs VGS" % d0["name"]
            assert e["v3"][1] <= -d0.get("vgs_bd", -99.0), "%s: ext off-rail below the body-diode limit" % d0["name"]
    nr = NEG_RAIL_SIM
    say("negrail", "Lost negative rail (bias='ext', ngspice sim/gdrv_miller.py, 2 x SG2M040170HJ, 1100 V / 72.4 A, 115 V/ns, "
        "off-rail 0 V): die %+.2f V vs V_th(min, 175 C) %.2f V -> a minimum-threshold victim pair conducts %.2f mJ per hard "
        "turn-on of its partner = %.0f W at 32 kHz (~%.0f W per device, ~+%.0f K on Tj): SURVIVABLE, but each event lasts "
        "tens of ns < DESAT blanking %.0f ns, so DESAT / booster do NOT clear it and FLT_N stays high - a silent fault "
        "(extra loss, aggressor E_on up). Microchip variant: die %+.2f V vs 1.48 V, %.2f mJ per event = %.0f W. NOT covered "
        "by DESAT / booster -> neg_det=True by default (4 parts, ~0.03 USD/channel): a DESAT fault (FLT_N) at the next turn-on",
        nr["die"], SC40["vth175"], nr["e_extra"] * 1e3, nr["e_extra"] * 32e3, nr["e_extra"] * 16e3,
        nr["e_extra"] * 16e3 * 0.9, desat_numbers(DESAT_CLASS[1700])[2][0] * 1e9, nr["msc_die"], nr["msc_e_extra"] * 1e3,
        nr["msc_e_extra"] * 32e3)
    assert nr["die"] > SC40["vth175"], "negative-rail argument changed: re-check whether the detector is still needed"
    # D-049: no hardware stretch -> firmware dead-band only. Gate dead time = firmware DT + driver mismatch (NSI6651 70 ns
    # skew budget + AHCT 1-8 ns); zero firmware DT: gate-level overlap = tpHL max + device turn-off - tpLH min - td(on)
    skew = NSI["skew"] + 7e-9
    win = (181e-9, 586e-9)
    ov = NSI["tprop"][2] + SC40["t_off"] - NSI["tprop"][0] - 15e-9
    i_st = SC40["n"] * SC40["i_sc"]
    e_st = SC40["v_bus_sc"] * i_st * (ov - i_st / (SC40["v_bus_sc"] / SC40["l_loop"]))
    say("dtfw", "stretch=False (D-049): IN- = the complementary command, so overlapping COMMANDS are still blocked inside the "
        "NSI6651 (IN+ AND NOT IN-). For gates at %.0f-%.0f ns the controller must set %.0f-%.0f ns at its pins (driver "
        "mismatch +/-%.0f ns). Residual risk, firmware dead time 0: gate overlap ~%.0f ns per edge (tpHL %.0f + turn-off "
        "%.0f - tpLH %.0f - td(on) 15); shoot-through rises at %.0f A/ns to ~%.0f A (assumed SC current), ~%.0f mJ per edge; "
        "it ends before DESAT blanking (>= %.0f ns), so DESAT does NOT catch it -> devices destroyed within ms. Firmware must "
        "lock the dead-band registers (EALLOW) and verify them before EN", win[0] * 1e9, win[1] * 1e9,
        (win[0] + skew) * 1e9, (win[1] - skew) * 1e9, skew * 1e9, ov * 1e9, NSI["tprop"][2] * 1e9, SC40["t_off"] * 1e9,
        NSI["tprop"][0] * 1e9, SC40["v_bus_sc"] / SC40["l_loop"] * 1e-9, i_st, e_st * 1e3,
        desat_numbers(DESAT_CLASS[1700])[2][0] * 1e9)
    assert win[0] + skew < win[1] - skew, "firmware dead-time window empty"
    for line in out.values():
        print(line)
    r15, m, d3 = num[(15, 4)], num[GM4["name"]], num[C3M16["name"]]
    cover = ["CALCULATED by gen/gdrv.py design_check(), not measured (full list in outputs/%s_design_check.txt). Gate rails "
             "+%.2f..%.2f / -%.2f..-%.2f V. Bias %.2f W of 2.0 W for the module at 100 kHz; worst case 3 x C3M0016120K %.2f W. "
             "Peak gate current %.1f / %.1f A (10 A rating)." % (PROJECT, r15["v2"][0], r15["v2"][1], r15["v3"][0], r15["v3"][1],
                                                             m["p_bias"][100e3], d3["p_bias"][100e3], m["ipk"][0], m["ipk"][1]),
             "DESAT trips at V_DS %.1f-%.1f V after %.0f-%.0f ns blanking; worst-case gate-off %.2f us (module) / %.2f us "
             "(3 discretes) vs tSC 3 us of IMZA120R020M1H. Wolfspeed CBB011M12GM4T / C3M datasheets give no tSC: unverified."
             % (m["trip"][0], m["trip"][1], m["blank"][0] * 1e9, m["blank"][1] * 1e9, m["t_sc"] * 1e6, d3["t_sc"] * 1e6),
             "Hardware dead time %.0f-%.0f ns at the gates = max(firmware, hardware); firmware >= %.0f ns is never stretched. "
             "Module NTC: plain analog line on the power board (no AIN). Barrier: NSI6651ASC VIOWM 2121 VDC (certified), "
             "UCC14241-Q1 1414 VDC (certification planned); CMTI >= 150 V/ns." % (m["dt"][0] * 1e9, m["dt"][1] * 1e9,
                                                                                  m["dt"][2] * 1e9)]
    return out, cover


# ---------------------------------------------------------------------------------------------- GDRV-HB card
DCP_NETS = {"D_H"}                                     # DC+ potential (high-side drain sense)


def build_design():
    lines, cover = design_check()
    B = L.Builder(PROJECT, "GDRV-HB half-bridge gate driver", REV, DATE, CATALOG, rails=["+24V", "3V3"], returns=["GND"],
                  subtitle="Two NSI6651ASC channels for one leg of a CBB011M12GM4T module (DAB-D60)",
                  comment1="Barrier: NSI6651ASC + UCC14241-Q1 reinforced (VIOWM 2121 / 1414 VDC) vs 1000 V bus",
                  comment4="Not bench-validated. PELV = logic side; HS, LS = floating channels; DCP = DC+ sense.",
                  root_notes=cover + ["Layout (not in this repo): card over the module signal pins, driver within 25 mm of "
                                      "J201/J301, gate loop < 100 mm (roadmap GDRV); D_H/D_L pins and DESAT strings need "
                                      "1000 V + overshoot creepage per the ECO-10 insulation coordination."])
    iso, xing = set(), set()

    B.new_sheet("01_interface", "Logic interface",
                "J101 to the power board: +24V, 3.3 V, PWM_H/PWM_L (single-ended, already\n"
                "received from RS-422 on the power board), EN, FLT_N, RDY (pin 15 spare: GND)")
    B.block("Power-board connector (PELV)", "3.3 V logic = CELL contract level; one RS-422 receiver per power board,\n"
            "N cards wire-OR FLT_N and wire-AND RDY on the board. EN low (or open) = both gates off.")
    B.part("J_LOGIC", {"1": "+24V", "2": "+24V", "3": "GND", "4": "GND", "5": "3V3", "6": "GND", "7": "PWM_H", "8": "GND",
                       "9": "PWM_L", "10": "GND", "11": "EN", "12": "GND", "13": "FLT_N", "14": "RDY", "15": "GND",
                       "16": "GND"})
    for net in ("+24V", "3V3", "GND"):
        B.flag(net)
    B.R("10k", "EN", "GND", note="EN open = disabled")
    B.R("4.99k", "FLT_N", "3V3")
    B.C("100p", "FLT_N", "GND", diel="C0G", tol="5%")
    B.R("4.99k", "RDY", "3V3")
    B.C("100p", "RDY", "GND", diel="C0G", tol="5%")

    chans = {}
    for key, side, other, jg, gpins in (("02_high", "H", "L", "J_GATE_H", ["G_H", "KS_H", "G_H", "KS_H"]),
                                        ("03_low", "L", "H", "J_GATE_H", ["G_L", "KS_L", "G_L", "KS_L"])):
        B.new_sheet(key, "%s-side channel" % ("High" if side == "H" else "Low"),
                    "NSI6651ASC + UCC14241-Q1 +15/-4 V, DESAT (3 x US1M), interlock with PWM_%s,\n"
                    "gate/Kelvin pins to the module" % other)
        ch = channel(B, side, pwm="PWM_" + side, en="EN", flt_n="FLT_N", rdy="RDY", gate="G_" + side, source="KS_" + side,
                     drain="D_" + side, vdd="VDD_" + side, vee="VEE_" + side, interlock="PWM_" + other, r_sb=GM4["r_sb"],
                     v_peak=V_PEAK["DAB 1000 V"])
        drain_src = "module DC+" if side == "H" else "module AC (switch node)"
        B.block("%s: module pins" % side, "Solder pins to the power board under the module: G/KS interleaved. The module\n"
                "NTC is NOT on this card: plain analog line to the CELL AN2/AN3 on the power board. Drain sense on its own "
                "pin (%s), spaced for 1000 V." % drain_src)
        B.part(jg, {str(i): net for i, net in enumerate(gpins, 1)})
        B.part("J_HV", {"1": "D_" + side})
        iso |= set(ch.iso)
        xing |= set(ch.xing)
        chans[side] = ch
    return B, iso, xing, chans, lines


def domain_of_for(chans):
    def domain_of(net):
        if net in DCP_NETS:
            return "DCP"
        if net in chans["H"].sec or net == "D_L":          # D_L = switch node = high-side COM potential
            return "HS"
        if net in chans["L"].sec:
            return "LS"
        return "PELV"
    return domain_of


if __name__ == "__main__":
    B, iso, xing, chans, lines = build_design()
    vr = {net: VCC_RANGE if net != "GND" else (0.0, 0.0) for net in ("PWM_H", "PWM_L", "EN", "FLT_N", "RDY", "GND")}
    for side in "HL":
        vr.update(net_ranges(side, (15, 4), "VDD_" + side, "VEE_" + side, "KS_" + side))
    rc = L.build(B, domain_of=domain_of_for(chans), isolators=iso, crossings=xing, vrange=vr.get,
                 waivers=ERC_WAIVERS)
    import json
    os.makedirs(os.path.join(L.REPO, "sim", "out", "gdrv_miller"), exist_ok=True)
    with open(os.path.join(L.REPO, "sim", "out", "gdrv_miller", "bias_transformer_req.json"), "w") as f:
        json.dump(bias_transformer_req(), f, indent=1)
    with open(os.path.join(L.REPO, "hardware", PROJECT, "outputs", PROJECT + "_design_check.txt"), "w") as f:
        f.write("CALCULATED by gen/gdrv.py design_check() - not measured, not bench-validated.\n")
        f.write("\n".join(lines.values()) + "\n")
    sys.exit(rc)
