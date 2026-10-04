"""AUX-HV - high-voltage bootstrap auxiliary supply of the 25-110 kW DC/DC family (REQUIREMENTS ECO-06, candidate
PV-C2). Makes the isolated 22.8 V / 30 W control rail directly from whichever DC port is higher (250-1000 V class,
1100 V transient), so the controller, communications and gate drivers start without the cabinet 24 V. Its output
feeds SYS-IO-AUX feed 3 (LM74800 switch, connector contract interfaces.AUX).

  01 HV input      per port: REDCUBE studs, 2 x 22 R AC10 pulse-rated wirewound AHEAD of the 1 A / 1000 VDC fuse
                   (fault current <= 25 A), BYG10Y diode OR of both poles; 2 x 3.3 uF / 1300 V film = surge absorber
  02 start-up      3 x BSS126 depletion cascode on a compensated 3 x 10 M CRHV2512 tap string, TPS3710 line brown-in,
                   TLV3202: channel 1 input OV lockout (pulls COMP low), channel 2 latched overload timer (crowbars SS)
  03 power stage   UCC28C59-Q1 (48 % max duty), IV2Q171R0D7Z (alt. SG2M1K0170J2J), 2 x 1.5 R sense, TVS clamp,
                   T1 (CUSTOM, DMEGC EC34A DMR95, design M1), aux -> BC817 follower -> VDD, aux backup loop on FB, frequency foldback
  04 secondary     VS-8ETU04S + RC snubber, 3 x 100 uF polymer, ATL431 + CNY65B fast-lane feedback with R-C
                   feed-forward, TPS3700 window -> SN74LVC1G17 -> PG_HVAUX driven actively high, output connector

Build options of the same board: PV = everything fitted (both ports). DAB-D60 = port 1 only (decision D-021): the
port-B input parts (2 studs, 2 x 22 R, fuse, 2 OR diodes) are not fitted -> bom/AUX-HV_DAB_BOM.csv.

Every component value is read from sim/out/aux_hv_design/aux_hv_spec.json (written by sim/aux_hv_design.py), so the
schematic cannot drift from the calculation. Catalog pin tables were read from the PDFs named in `ds` (document and
page in the comment). Nothing here is bench-validated.  Usage: .venv/bin/python gen/aux_hv.py
"""
import collections
import json
import os
import sys

import catalog
import dcdclib as L
import interfaces as IF

PROJECT, REV, DATE = "AUX-HV", "C1", "2026-10-04"
DS = "docs/datasheets/"
TI, VISHAY, NXP = "Texas Instruments", "Vishay", "Nexperia"
PV_ONLY = " (PV build only; not fitted on DAB-D60)"
SPEC_PATH = os.path.join(L.REPO, "sim", "out", "aux_hv_design", "aux_hv_spec.json")
if not os.path.exists(SPEC_PATH):
    raise SystemExit("missing %s - run .venv/bin/python sim/aux_hv_design.py first" % SPEC_PATH)
SPEC = json.load(open(SPEC_PATH))
V = SPEC["values"]


def ic(mfr, mpn, pkg, ds, desc, left, right, prefix="U"):
    return dict(mfr=mfr, mpn=mpn, prefix=prefix, pkg=pkg, ds=DS + ds, desc=desc, pins={"left": left, "right": right})


def pv_only(entry):
    """Port-B copy of a catalog entry: same part, own BOM line marked PV-only (the DAB-D60 build leaves it out)."""
    return dict(entry, desc=entry["desc"] + PV_ONLY)


CATALOG = dict(catalog.PARTS, **{
    # ------------------------------------------------------------------------------------------------ HV input
    # Wurth REDCUBE press-fit 7461057 (WE-7461057.pdf p1): M3 thread terminal; insulation by layout spacing only
    "STUD": dict(mfr="Wurth Elektronik", mpn="7461057", prefix="J", pkg="REDCUBE press-fit M3",
                 ds=DS + "connectors/WE-7461057.pdf", stock=("Connector_Generic", "Conn_01x01"),
                 desc="HV wire terminal for an M3 ring lug (1000 VDC by board spacing)"),
    # Schurter ASO 10.3x38 (ASO-0090.1001.pdf): p1 1000 VDC gPV, PCB version; p3 0090.1001 = 1 A, melting I2t 0.554 A2s;
    # p4 note 1: 20 kA @ 1000 VDC, L/R < 2 ms (here it only ever breaks <= 25 A: the AC10 pair ahead of it limits)
    "FUSE_HV": dict(mfr="Schurter", mpn="0090.1001", prefix="F", pkg="10.3 x 38 mm, PCB pins",
                    ds=DS + "protection/ASO-0090.1001.pdf", stock=("Device", "Fuse"),
                    desc="Fuse 1 A 1000 VDC gPV, 20 kA breaking, PCB pins (one per DC port, behind 2 x 22 R)"),
    # Vishay BYG10 doc 88957: p1 VRRM 1600 V, IF(AV) 1.5 A, IFSM 30 A, SMA, colour band = cathode (KiCad D: 1 K, 2 A);
    # p2 VF <= 1.1 V @ 1 A, trr <= 4 us (slow recovery, wanted in the clamp per TI SLUSEV2C p31)
    "BYG10Y": dict(mfr=VISHAY, mpn="BYG10Y-E3/TR", prefix="D", pkg="SMA (DO-214AC)",
                   ds=DS + "power-semiconductors/BYG10Y.pdf", stock=("Device", "D"),
                   desc="Avalanche rectifier 1600 V 1.5 A, standard recovery"),
    # Vishay Draloric AC10 (AC-AC-AT-AC-NI.pdf): p1 10 W / 8.4 W @ 70 C, 0.22-560 R; p2 part number AC10000002209JAB00
    # = 22 R 5 %, packaging AB; p6 pulse energy ~2.6 Ws/Ohm at 22 R (read off); p7 peak pulse 4.5 kV (<= 0.2 ms); p12 44 x 8 mm
    "AC10_22R": dict(mfr="Vishay Draloric", mpn="AC10000002209JAB00", prefix="R", pkg="axial cemented wirewound 10 W, 44 x 8 mm",
                     ds=DS + "passives-capacitors/AC-AC-AT-AC-NI.pdf", stock=("Device", "R"),
                     desc="Resistor 22 R 5 % 10 W cemented wirewound, pulse-rated (current limit ahead of the port fuse)"),
    # Jianghai CBB 138 DS (Jianghai-JE26-Film.pdf p28-30): FCSA3DS335 3.3 uF, 1300 VDC <= 70 C / 1100 V <= 85 C, 264 A,
    # 80 V/us, 32 x 28 x 18 mm, pitch 27.5 mm, IEC 61071; order code tolerance / packaging letters per p30 (K = 10 %)
    "C4AQ_3U3": dict(mfr="Jianghai", mpn="FCSA3DS335K050ID90BE3", prefix="C", pkg="radial 27.5 mm, 2 pins 5.0 mm, 32 x 28 x 18 mm",
                     ds=DS + "passives-capacitors/Jianghai-JE26-Film.pdf", stock=("Device", "C"),
                     desc="Film capacitor 3.3 uF 1300 VDC, PP DC link, IEC 61071 (input surge absorber); was KEMET C4AQUBU4330A11J"),
    # ------------------------------------------------------------------------------------------------ start-up
    # Vishay CRHV doc 68002: p1 CRHV2512 1 W, 3000 V working, 10M-1G +/-1 %, 100 ppm/C; p2 part-number format, VCR 10 ppm/V
    "CRHV_10M": dict(mfr=VISHAY, mpn="CRHV2512AF10M0FKFB", prefix="R", pkg="2512", ds=DS + "passives-capacitors/CRHV.pdf",
                     stock=("Device", "R"), desc="Resistor 10 M 1 % 2512 high-voltage thick film, 3000 V"),
    # Infineon BSS126 Rev 2.1: p1 pins 1 gate, 2 source, 3 drain; 600 V depletion, VGS +/-20 V; p2 VGS(th) -2.7..-1.6 V
    "BSS126": dict(mfr="Infineon", mpn="BSS126H6327XTSA2", prefix="Q", pkg="SOT-23", ds=DS + "power-semiconductors/BSS126.pdf",
                   desc="N-MOSFET 600 V depletion mode, IDSS >= 7 mA (HV start-up cascode)",
                   pins={"left": ["1 G i"], "right": ["3 D p", "2 S p"]}),
    # Nexperia 2N7002BK: Table 2 p2 pins 1 G, 2 S, 3 D (= KiCad 2N7002); 60 V; p6 VGS(th) 1.1-2.1 V
    "2N7002BK": dict(mfr=NXP, mpn="2N7002BK,215", prefix="Q", pkg="SOT-23", stock=("Transistor_FET", "2N7002"),
                     ds=DS + "power-semiconductors/2N7002BK.pdf", desc="N-MOSFET 60 V 350 mA (logic switch)"),
    # TI TPS3710 SBVS271A: p3 DDC pins 1 OUT, 2 GND, 3 SENSE, 4 GND, 5 VDD, 6 GND; p5 VIT+ 396-404 mV, VIT- 387-400 mV
    "TPS3710": ic(TI, "TPS3710DDCR", "SOT-6 (DDC)", "power-supply/TPS3710.pdf", "Voltage detector 400 mV, open drain",
                  ["5 VDD pi", "3 SENSE i", None, "2 GND pi", "4 GND pi", "6 GND pi"], ["1 OUT oc"]),
    # TI TLV3202 (TLV3202.pdf): p3 DGK pins 1 1OUT, 2 1IN-, 3 1IN+, 4 GND, 5 2IN+, 6 2IN-, 7 2OUT, 8 VCC (push-pull);
    # p4 VS 2.7-5.5 V; p5 VIO <= 5 mV, IQ <= 65 uA per channel, tPD <= 55 ns
    "TLV3202": ic(TI, "TLV3202AIDGKR", "VSSOP-8 (DGK)", "sensing/TLV3202.pdf",
                  "Dual comparator 40 ns, 40 uA/ch, push-pull, rail-to-rail inputs",
                  ["8 VCC pi", "2 1IN- i", "3 1IN+ i", None, "5 2IN+ i", "6 2IN- i", None, "4 GND pi"], ["1 1OUT o", None, None, None, "7 2OUT o"]),
    # Nexperia BAT54 (1 Jul 2022): Table 2 p2 pin 1 A, 2 n.c., 3 K (= KiCad BAT54W)
    "BAT54": dict(mfr=NXP, mpn="BAT54,215", prefix="D", pkg="SOT-23", stock=("Diode", "BAT54W"),
                  ds=DS + "protection/BAT54.pdf", desc="Schottky diode 30 V 200 mA"),
    # Nexperia BAS16 (BAS16.pdf): p2 Table 3 SOT23 pin 1 anode, 2 n.c., 3 cathode (= KiCad BAT54W layout); p2 IR <= 30 nA
    # @ 25 V, 25 C (low leakage: used on the high-impedance overload-timer node)
    "BAS16": dict(mfr=NXP, mpn="BAS16,215", prefix="D", pkg="SOT-23", stock=("Diode", "BAT54W"),
                  ds=DS + "protection/BAS16.pdf", desc="Switching diode 100 V 215 mA, low leakage (timer latch)"),
    # Nexperia BAT54S (1 Jul 2022): Table 2 p1 pin 1 A1, 2 K2, 3 K1/A2 (= KiCad BAT54S 1 A, 2 K, 3 COM)
    "BAT54S": dict(mfr=NXP, mpn="BAT54S,215", prefix="D", pkg="SOT-23", stock=("Diode", "BAT54S"),
                   ds=DS + "protection/BAT54S.pdf", desc="Dual series Schottky 30 V (soft start: clamp + discharge)"),
    # ------------------------------------------------------------------------------------------------ power stage
    # TI SLUSEV2C (UCC28C58-Q1.pdf, UCC28C5x-Q1 family): p4 Fig 6-1 / Table 6-1 SOIC-8 pins; p8 UCC28C59: UVLO 16/12.5 V,
    # Dmax 47-48 % (OUT = oscillator / 2); p5 VDD abs max 30 V
    "UCC28C59": ic(TI, "UCC28C59QDRQ1", "SOIC-8 (D)", "power-supply/UCC28C58-Q1.pdf",
                   "Current-mode PWM controller for SiC, UVLO 16/12.5 V, max duty 48 %, AEC-Q100",
                   ["7 VDD pi", "8 VREF po", "4 RT/CT p", "2 FB i", None, "5 GND pi"], ["6 OUT o", "3 CS i", None, "1 COMP o"]),
    # InventChip IV2Q171R0D7Z Rev1.2 (IV2Q171R0D7Z.pdf): p1 pins 1 gate, 2 driver (Kelvin) source, 3-7 power source,
    # tab drain; 1700 V, VGS on 15-18 V, AEC-Q101 qualified. Alternate (same pin-out, SG2M1K0170J2J.pdf p1: gate 1,
    # driver source 2, power source 3-7, drain tab): Sichain SG2M1K0170J2J - sim/aux_hv_design.py sizes for the worse
    "SW1700": ic("InventChip", "IV2Q171R0D7Z", "TO-263-7", "power-semiconductors/IV2Q171R0D7Z.pdf",
                 "SiC MOSFET 1700 V 1 Ohm, Kelvin source, AEC-Q101; alternate Sichain SG2M1K0170J2J (same pin-out)",
                 ["1 G i", "2 KS p"], ["8 D p", "3 S p", "4 S p", "5 S p", "6 S p", "7 S p"], prefix="Q"),
    # Bourns SMCJ (SMCJ_Bourns.pdf): p2 SMCJ70A VBR 77.8-86.0 V, VC 113 V @ 13.3 A; p1 1500 W, PM(AV) 5 W, SMC, band = cathode
    "SMCJ70A": dict(mfr="Bourns", mpn="SMCJ70A", prefix="D", pkg="DO-214AB (SMC)", stock=("Device", "D_Zener"),
                    ds=DS + "protection/SMCJ_Bourns.pdf", desc="TVS unidirectional 1500 W, VRWM 70 V, VBR 77.8-86.0 V (clamp, 3 in series)"),
    # Diodes Inc US1x DS16008 (US1M.pdf) p2: US1G 400 V 1 A, VF <= 1.3 V, trr <= 50 ns; SMA (KiCad US1M: 1 K, 2 A)
    "US1G": dict(mfr="Diodes Incorporated", mpn="US1G-13-F", prefix="D", pkg="SMA", stock=("Diode", "US1M"),
                 ds=DS + "power-semiconductors/US1M.pdf", desc="Ultrafast rectifier 400 V 1 A"),
    # Nexperia BZX84 Rev 7 (BZX84.pdf): p2 Table 2 pin 1 A, 2 n.c., 3 K; p5-p6 Table 8 B3V3 3.23-3.37 V, B6V8 6.66-6.94 V,
    # A18 17.82-18.18 V, B20 19.60-20.40 V (all @ 5 mA)
    "BZX84-B3V3": dict(mfr=NXP, mpn="BZX84-B3V3,215", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                       ds=DS + "protection/BZX84.pdf", desc="Zener 3.3 V 2 % (PG buffer rail)"),
    "BZX84-B6V8": dict(mfr=NXP, mpn="BZX84-B6V8,215", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                       ds=DS + "protection/BZX84.pdf", desc="Zener 6.8 V 2 % (foldback threshold)"),
    "BZX84-A18": dict(mfr=NXP, mpn="BZX84-A18,215", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                      ds=DS + "protection/BZX84.pdf", desc="Zener 18 V 1 % (VDD follower reference = SiC gate drive)"),
    "BZX84-B20": dict(mfr=NXP, mpn="BZX84-B20,215", prefix="D", pkg="SOT-23", stock=("Diode", "BZX84Cxx"),
                      ds=DS + "protection/BZX84.pdf", desc="Zener 20 V 2 % (VDD / gate over-voltage clamp)"),
    # Nexperia BC817 series (BC817.pdf): p2 Table 3 pins 1 B, 2 E, 3 C; p3 VCEO 45 V, VEBO 5 V; p5 hFE 160-400 (-25)
    "BC817-25": dict(mfr=NXP, mpn="BC817-25,215", prefix="Q", pkg="SOT-23", stock=("Transistor_BJT", "BC817"),
                     ds=DS + "power-semiconductors/BC817.pdf", desc="NPN 45 V 500 mA (VDD emitter follower)"),
    "T1": dict(mfr="CUSTOM", mpn=SPEC["transformer"]["mpn"], prefix="T", ds="",
               pkg="%s (ETD 34 class) potted case, 14-pin former" % SPEC["transformer"]["core"],
               sourcing="CUSTOM", desc=SPEC["transformer"]["desc"],
               pins={"left": ["1 PS p", "2 PF p", None, "4 AS p", "5 AF p", None, "3 SH p"], "right": ["9 SS p", None, "8 SF p"]}),
    # ------------------------------------------------------------------------------------------------ secondary
    # Vishay VS-8ETU04S-M3 doc 96388: p1 D2PAK pins 1 N/C, 2 (tab) cathode, 3 anode; 400 V 8 A, VF 0.94 V @ 8 A 150 C
    "VS-8ETU04S": ic(VISHAY, "VS-8ETU04S-M3", "D2PAK (TO-263AB)", "power-semiconductors/VS-8ETU04S-M3.pdf",
                     "Ultrafast rectifier 400 V 8 A FRED Pt (output)", ["3 A p", "1 NC nc"], ["2 K p"], prefix="D"),
    # Jianghai PC HVF VF (Jianghai-JE26-Polymer.pdf p16-17): PCV1HVF101MB12FV-WE3 100 uF 50 V, ESR <= 25 mOhm, 3.8 A
    # @ 100 kHz, 3000 h @ 105 C, SMD 8 x 12.2 mm (+ = pin 1); rev C1 (was PCV1VVF101MB70FV-WE3 35 V)
    "CPOL100": dict(mfr="Jianghai", mpn="PCV1HVF101MB12FV-WE3", prefix="C", pkg="SMD V-chip 8.0 x 12.2 mm",
                    ds=DS + "passives-capacitors/Jianghai-JE26-Polymer.pdf", stock=("Device", "C_Polarized"),
                    desc="Aluminium polymer capacitor 100 uF 50 V, ESR 25 mOhm, 3.8 A, 3000 h @ 105 C (JE26 p17); was Wurth 875115655003"),
    # Vishay CNY65 doc 83540: p1 top view / p8 drawing name the terminals only (A, cathode, collector, emitter); numbered
    # here as DIP-4 1 A, 2 K, 3 E, 4 C. p1 VIORM 1800 Vpk, DTI >= 3 mm; p3 CNY65B CTR 100-200 % @ 10 mA; p4 creepage 14 mm
    "CNY65B": ic(VISHAY, "CNY65B", "DIP-4 HV 600 mil (15.24 mm)", "isolation-interface/CNY65.pdf",
                 "Optocoupler phototransistor, reinforced, VIORM 1800 Vpk, creepage/clearance >= 14 mm, CTR 100-200 %",
                 ["1 A p", "2 K p"], ["4 C p", "3 E p"]),
    # TI ATL431 (ATL431.pdf): p3 Fig 4-1 / Table 4-1 DBZ pins 1 CATHODE, 2 REF, 3 ANODE; p4 B grade 2.487-2.512 V
    "ATL431": ic(TI, "ATL431BIDBZR", "SOT-23-3 (DBZ)", "power-supply/ATL431.pdf",
                 "Shunt reference 2.5 V 0.5 %, Ika(min) 35 uA, 36 V", ["2 REF i"], ["1 K p", "3 A p"]),
    # TI TPS3700 SBVS187G: p4 DDC pins 1 OUTA, 2 GND, 3 INA+, 4 INB-, 5 VDD, 6 OUTB; p6 VIT+ 396-404 mV
    "TPS3700": ic(TI, "TPS3700DDCR", "SOT-6 (DDC)", "power-supply/TPS3700.pdf",
                  "Window voltage detector, 400 mV, two open-drain outputs", ["5 VDD pi", "3 INA+ i", "4 INB- i", "2 GND pi"],
                  ["1 OUTA oc", "6 OUTB oc"]),
    # TI SN74LVC1G17 (SN74LVC1G17.pdf): p3 DBV pins 1 NC, 2 A, 3 GND, 4 Y, 5 VCC; p1 Ioff (unpowered output high-Z),
    # p5 VCC 1.65-5.5 V, p6 VOH >= VCC - 0.1 V @ -100 uA
    "LVC1G17": dict(mfr=TI, mpn="SN74LVC1G17DBVR", prefix="U", pkg="SOT-23-5 (DBV)",
                    ds=DS + "isolation-interface/SN74LVC1G17.pdf", desc="Single Schmitt-trigger buffer, Ioff (PG_HVAUX driver)",
                    pins={"left": ["2 A i", "1 NC nc", "5 VCC pi", "3 GND pi"], "right": ["4 Y o"]}),
    # Phoenix Contact 1720479 PC 5/ 3-G-7.62: maker listing (Phoenix answers scripted downloads with HTTP 403, as for
    # SYS-IO-AUX); same header type as the AUX-HV feed on SYS-IO-AUX J103, cable with PC 5/3-ST plugs at both ends
    "J_OUT": dict(mfr="Phoenix Contact", mpn="1720479", prefix="J", pkg="PC 5/ 3-G-7.62 THT",
                  ds="https://www.phoenixcontact.com/en-us/products/pcb-header-pc-5-3-g-762-1720479",
                  stock=("Connector_Generic", "Conn_01x03"), desc="PCB header 3-pos 7.62 mm (interfaces.AUX output)"),
})
for key in ("STUD", "AC10_22R", "FUSE_HV", "BYG10Y"):
    CATALOG[key + "_PB"] = pv_only(CATALOG[key])

# PELV (output) side of the barrier; every other net belongs to the HV primary, referenced to PGND = OR negative
PELV = {"+24V_HVAUX", "GND", "PG_HVAUX", "SEC_A", "SN_M", "LED_A", "K431", "FBS", "FFM", "PG_UV", "PG_OV", "PG3V3",
        "PGW", "PGO"}


def domain_of(net):
    return "PELV" if net in PELV else "HV"


# DC range of each net against its own domain's reference (HV: PGND, PELV: GND) for the jellybean stress check in
# L.build(). Switching nodes (drain, source/CS, gate, rectified aux / secondary, snubber) and the correlated taps of
# the HV string (their resistors and capacitors are rated by sim/aux_hv_design.py) have no entry.
VO_MAX = SPEC["summary"]["backup_band"][1] + 0.5            # output ceiling: opto failed, backup loop at its top
VDD_MAX = 20.4 + 0.018 * 60                                  # BZX84-B20 clamp at 85 C
VDC = {"PGND": (0, 0), "GND": (0, 0), "HV_BULK": (0, 1100), "VREF": (0, 5.2), "VDD": (0, VDD_MAX), "VDD_E": (0, VDD_MAX),
       "VAUX": (0, 1.07 * (VO_MAX + 1.0)), "AUXS": (0, 1.07 * (VO_MAX + 1.0)), "AF1": (0, 1.07 * (VO_MAX + 1.0)),
       "ZB": (0, 19.2), "QIG": (0, 1.07 * (VO_MAX + 1.0)), "FBK": (0, 5.2), "CTXD": (0, 2.6), "RTCT": (0, 2.6),
       "FB": (0, 2.6), "EA_M": (0, 5.2), "COMP": (0, 5.2), "SS": (0, 5.2), "LINE_OK": (0, 5.2), "LSO": (0, 2.7),
       "LS": (0, 2.2), "LSOF": (0, 2.7), "VR25": (0, 2.6), "OVREF": (0, 2.6), "OVL_N": (0, 5.2), "COMPF": (0, 5.2),
       "OLATCH": (0, 5.2), "SSG": (0, 5.2), "SU_EN": (0, 5.2), "SU_G": (0, VDD_MAX),
       "+24V_HVAUX": (0, VO_MAX), "FBS": (0, 2.6), "FFM": (0, VO_MAX), "K431": (0, VO_MAX), "PG_UV": (0, 2.6),
       "PG_OV": (0, 2.6), "PG3V3": (0, 3.4), "PGW": (0, 3.4), "PGO": (0, 3.4), "PG_HVAUX": (0, 3.6)}


def R(B, key, n1, n2):
    e = V[key]
    return B.R(e["value"], n1, n2, pkg=e.get("pkg", "0603"), tol=e.get("tol", "1%"), note=e.get("note", ""))


def C(B, key, n1, n2):
    e = V[key]
    return B.C(e["value"], n1, n2, pkg=e.get("pkg", "0603"), volt=e.get("volt", "50V"), diel=e.get("diel", "X7R"),
               tol=e.get("tol", ""))


def sheet_input(B, port_b):
    B.new_sheet("01_input", "HV input, limit, fuse, OR",
                "Per port: studs, 2 x 22 R pulse-rated AHEAD of the 1 A / 1000 VDC fuse, BYG10Y OR\n"
                "of both poles; 2 x 3.3 uF / 1300 V film = surge absorber.\n"
                "Port B is not fitted on the DAB-D60 build (bom/AUX-HV_DAB_BOM.csv).")
    for port, sfx in (("A", ""), ("B", "_PB")):
        B.block("Port %s%s" % (port, ": PV build only (not fitted on DAB-D60)" if sfx else ""),
                "%s\nFuse: %s" % (V["R_LIM"]["why"], V["F_PORT"]["why"]))
        p = "P%s" % port
        refs = [B.part("STUD" + sfx, {"1": p + "_P"}).ref, B.part("STUD" + sfx, {"1": p + "_N"}).ref]
        chain = [p + "_P"] + ["%s_R%d" % (p, i + 1) for i in range(V["R_LIM"]["n"] - 1)] + [p + "_RF"]
        for a, b in zip(chain, chain[1:]):
            refs.append(B.part("AC10_22R" + sfx, {"1": a, "2": b}, value=V["R_LIM"]["value"]).ref)
        refs.append(B.part("FUSE_HV" + sfx, {"1": p + "_RF", "2": p + "_F"}).ref)
        refs.append(B.part("BYG10Y" + sfx, {"2": p + "_F", "1": "HV_BULK"}).ref)
        refs.append(B.part("BYG10Y" + sfx, {"2": "PGND", "1": p + "_N"}).ref)
        if sfx:
            port_b.update(refs)
    B.block("Surge absorber / input filter", "Film: %s." % V["C_BULK"]["why"])
    for _ in range(V["C_BULK"]["n"]):
        B.part("C4AQ_3U3", {"1": "HV_BULK", "2": "PGND"}, value=V["C_BULK"]["value"])
    B.flag("HV_BULK")
    B.flag("PGND")


def sheet_startup(B):
    B.new_sheet("02_startup", "Start-up, brown-in, OV, timer",
                "3 x BSS126 cascode on a compensated tap string; TPS3710 brown-in pulls COMP low;\n"
                "TLV3202 ch 1 = input OV lockout (COMP low),\n"
                "ch 2 = latched overload timer (soft-start crowbar) -> hiccup")
    B.block("Compensated tap string (also the input-capacitor bleeder)", "%s\n%s\nBottom: %s / %s." % (
        V["R_STR"]["why"], V["C_STR"]["why"], V["R_D1"]["why"], V["R_D2"]["why"]))
    for a, b in (("HV_BULK", "SU_T2"), ("SU_T2", "SU_T1"), ("SU_T1", "LSO")):
        B.part("CRHV_10M", {"1": a, "2": b}, value=V["R_STR"]["value"])
        C(B, "C_STR", a, b)
    R(B, "R_D1", "LSO", "LS")
    C(B, "C_D1", "LSO", "LS")
    R(B, "R_D2", "LS", "PGND")
    C(B, "C_D2", "LS", "PGND")
    B.block("Depletion cascode current source", "Each FET holds ~Vin/3 (367 V at 1100 V). Bottom FET: VGS = -I x R_s.\n"
            "%s. VREF high -> Q pulls SU_G to PGND -> VGS = -VDD -> off." % V["R_SU_S"]["why"])
    B.part("BSS126", {"3": "HV_BULK", "1": "SU_T2", "2": "SU_S2"})
    B.part("BSS126", {"3": "SU_S2", "1": "SU_T1", "2": "SU_S1"})
    B.part("BSS126", {"3": "SU_S1", "1": "SU_G", "2": "SU_RS"})
    R(B, "R_SU_S", "SU_RS", "VDD")
    R(B, "R_SU_G", "SU_G", "VDD")
    B.part("2N7002BK", {"1": "SU_EN", "2": "PGND", "3": "SU_G"})
    R(B, "R_SU_EN", "VREF", "SU_EN")
    R(B, "R_SU_EN_PD", "SU_EN", "PGND")
    B.block("Line brown-in", "LINE_OK low -> BAT54 holds COMP below the 1.15 V offset (zero duty).")
    B.part("TPS3710", {"5": "VREF", "3": "LS", "2": "PGND", "4": "PGND", "6": "PGND", "1": "LINE_OK"})
    C(B, "C_DEC", "VREF", "PGND")
    R(B, "R_LINE_PU", "VREF", "LINE_OK")
    R(B, "R_HYS", "LINE_OK", "LS")
    B.part("BAT54", {"1": "COMP", "2": None, "3": "LINE_OK"})
    B.block("Input OV lockout and overload timer (TLV3202 on VREF, 2.5 V reference)",
            "Ch 1: %s; 1OUT low pulls COMP low through a BAT54.\nCh 2: %s; %s. 2OUT latches through a BAS16 and turns\n"
            "the 2N7002 on SS on until VDD reaches UVLO (VREF off resets the latch) -> hiccup." % (
                V["R_D1"]["why"], V["R_TA"]["why"], V["C_TMR"]["why"]))
    B.part("ATL431", {"1": "VR25", "2": "VR25", "3": "PGND"})
    R(B, "R_REF", "VREF", "VR25")
    B.part("TLV3202", {"8": "VREF", "4": "PGND", "2": "LSOF", "3": "OVREF", "1": "OVL_N", "5": "COMPF", "6": "VR25",
                       "7": "OLATCH"})
    C(B, "C_DEC", "VREF", "PGND")
    R(B, "R_LSF", "LSO", "LSOF")
    C(B, "C_LSF", "LSOF", "PGND")
    R(B, "R_OVH1", "VR25", "OVREF")
    R(B, "R_OVH2", "OVL_N", "OVREF")
    B.part("BAT54", {"1": "COMP", "2": None, "3": "OVL_N"})
    R(B, "R_TA", "COMP", "COMPF")
    R(B, "R_TB", "COMPF", "PGND")
    C(B, "C_TMR", "COMPF", "PGND")
    B.part("BAS16", {"1": "OLATCH", "2": None, "3": "COMPF"})
    R(B, "R_SSG", "OLATCH", "SSG")
    R(B, "R_SSG_PD", "SSG", "PGND")
    B.part("2N7002BK", {"1": "SSG", "2": "PGND", "3": "SS"})
    B.TP("COMPF")


def sheet_power(B, iso):
    B.new_sheet("03_power", "PWM, SiC, clamp, T1, foldback",
                "UCC28C59-Q1 at %.0f kHz, IV2Q171R0D7Z 1700 V SiC, TVS clamp, T1 (CUSTOM EC34A),\n"
                "aux -> BC817 follower -> VDD, aux backup loop on FB,\n"
                "foldback: 10 nF onto RT/CT below ~8 V output (no current staircase)" % (SPEC["summary"]["fsw"] / 1e3))
    B.block("PWM controller", "%s. VDD %s.\nUVLO 16/12.5 V (SiC), duty < 48 %%, no slope compensation needed (DCM, D < 0.5)."
            % (V["R_T"]["why"], V["C_VDD"]["why"]))
    B.part("UCC28C59", {"7": "VDD", "8": "VREF", "4": "RTCT", "2": "FB", "5": "PGND", "6": "OUT", "3": "CS", "1": "COMP"})
    C(B, "C_VREF", "VREF", "PGND")
    R(B, "R_T", "VREF", "RTCT")
    C(B, "C_T", "RTCT", "PGND")
    for _ in range(V["C_VDD"]["n"]):
        C(B, "C_VDD", "VDD", "PGND")
    C(B, "C_DEC", "VDD", "PGND")
    B.part("BZX84-B20", {"1": "PGND", "2": None, "3": "VDD"})
    B.flag("VDD")
    B.TP("VDD")
    B.block("Frequency foldback (AX-03)", "%s.\nAux plateau -> US1G -> %s -> %s || %s -> BZX84-B6V8 -> Q_INV: aux healthy holds FBK low;\n"
            "short: FBK -> VREF via %s, Q_FB connects C_TX. %s." % (
                V["C_TX"]["why"], V["R_AF1"]["value"], V["C_AF"]["value"], V["R_AF2"]["value"], V["R_FBK"]["value"],
                V["R_AF1"]["why"]))
    B.part("US1G", {"2": "AUX", "1": "AFR"})
    R(B, "R_AF1", "AFR", "AF1")
    C(B, "C_AF", "AF1", "PGND")
    R(B, "R_AF2", "AF1", "PGND")
    B.part("BZX84-B6V8", {"3": "AF1", "2": None, "1": "QIG"})
    R(B, "R_QG", "QIG", "PGND")
    B.part("2N7002BK", {"1": "QIG", "2": "PGND", "3": "FBK"})
    R(B, "R_FBK", "VREF", "FBK")
    B.part("2N7002BK", {"1": "FBK", "2": "PGND", "3": "CTXD"})
    C(B, "C_TX", "RTCT", "CTXD")
    B.TP("FBK")
    B.block("Soft start and COMP", "COMP = opto collector + %s pull-up + EA source; BAT54S clamps COMP to the soft-start\n"
            "node (%s, %s) and discharges it into VREF in UVLO." % (V["R_PU"]["value"], V["R_SS"]["value"], V["C_SS"]["value"]))
    R(B, "R_PU", "VREF", "COMP")
    C(B, "C_P", "COMP", "PGND")
    B.part("BAT54S", {"1": "COMP", "3": "SS", "2": "VREF"})
    R(B, "R_SS", "VREF", "SS")
    C(B, "C_SS", "SS", "PGND")
    B.TP("COMP")
    B.block("Aux winding: VDD follower, backup over-voltage loop", "Na > Ns: the aux takes VDD over at %.1f V output, below the\n"
            "16.1 V SYS eFuse UVLO. %s. %s.\n%s." % (SPEC["summary"]["aux_takeover_V"], V["R_ZREG"]["why"], V["R_FBT"]["why"],
                                                    V["R_VDDE"]["why"]))
    B.part("US1G", {"2": "AUX", "1": "AUX_R"})
    R(B, "R_AUX", "AUX_R", "VAUX")
    for _ in range(V["C_AUX"]["n"]):
        C(B, "C_AUX", "VAUX", "PGND")
    R(B, "R_ZREG", "VAUX", "ZB")
    B.part("BZX84-A18", {"1": "PGND", "2": None, "3": "ZB"})
    B.part("BC817-25", {"1": "ZB", "3": "VAUX", "2": "VDD_E"})
    B.part("BAT54", {"1": "VDD_E", "2": None, "3": "VDD"})
    R(B, "R_VDDE", "VDD_E", "PGND")
    B.part("US1G", {"2": "AUX", "1": "AUXS_R"})
    R(B, "R_AUXS", "AUXS_R", "AUXS")
    C(B, "C_AUXS", "AUXS", "PGND")
    R(B, "R_FBT", "AUXS", "FB")
    R(B, "R_FBB", "FB", "PGND")
    R(B, "R_EA", "COMP", "EA_M")
    C(B, "C_EA", "EA_M", "FB")
    C(B, "C_EAHF", "COMP", "FB")
    B.block("SiC switch and current sense", "Kelvin source (pin 2) on the sense node. %s.\nCS RC filter %s / %s (no internal "
            "blanking)." % (V["R_CS"]["why"], V["R_CSF"]["value"], V["C_CSF"]["value"]))
    R(B, "R_G", "OUT", "GATE")
    R(B, "R_GS", "GATE", "SRC")
    B.part("SW1700", {"1": "GATE", "2": "SRC", "3": "SRC", "4": "SRC", "5": "SRC", "6": "SRC", "7": "SRC", "8": "DRAIN"})
    for _ in range(V["R_CS"]["n"]):
        R(B, "R_CS", "SRC", "PGND")
    R(B, "R_CSF", "SRC", "CS")
    C(B, "C_CSF", "CS", "PGND")
    for _ in range(V["C_HF"]["n"]):
        C(B, "C_HF", "HV_BULK", "PGND")
    B.block("Drain clamp", "BYG10Y (slow recovery recycles part of the leakage energy) + 3 x SMCJ70A (SMC: >= 4 cm2 copper each).\n"
            "V_DS peak %.0f V at 1000 V, %.0f V at 1100 V (80 / 90 %% rule on 1700 V)." % (
                SPEC["summary"]["vds_cont"], SPEC["summary"]["vds_trans"]))
    B.part("BYG10Y", {"2": "DRAIN", "1": "CLMP"})
    B.part("SMCJ70A", {"1": "CLMP", "2": "TVM"})
    B.part("SMCJ70A", {"1": "TVM", "2": "TVM2"})
    B.part("SMCJ70A", {"1": "TVM2", "2": "HV_BULK"})
    B.block("Transformer T1 (CUSTOM - reinforced barrier)", "Dots at the winding starts PS, AS, SS. Np/Ns/Na %d/%d/%d, Lp %.3f mH.\n"
            "Full winding and insulation specification in the T1 description field." % (
                SPEC["transformer"]["np"], SPEC["transformer"]["ns"], SPEC["transformer"]["na"], SPEC["transformer"]["lp_mh"]))
    iso.add(B.part("T1", {"1": "DRAIN", "2": "HV_BULK", "3": "PGND", "4": "AUX", "5": "PGND", "9": "SEC_A", "8": "GND"}).ref)


def sheet_secondary(B, iso):
    B.new_sheet("04_secondary", "Rectifier, output, feedback, PG",
                "VS-8ETU04S + RC snubber, 3 x 100 uF polymer, ATL431 + CNY65B fast-lane type II\n"
                "with R-C feed-forward, TPS3700 window -> SN74LVC1G17 -> PG_HVAUX (active high),\n"
                "output connector (interfaces.AUX)")
    B.block("Rectifier and output capacitors", "D2PAK tab = cathode on the quiet output node. Snubber %s + %s.\n"
            "%s. Output: %s." % (V["R_SN"]["value"], V["C_SN"]["value"], V["C_OUT_POL"]["why"], V["R_UP"]["why"]))
    B.part("VS-8ETU04S", {"3": "SEC_A", "1": None, "2": "+24V_HVAUX"})
    R(B, "R_SN", "SEC_A", "SN_M")
    C(B, "C_SN", "SN_M", "+24V_HVAUX")
    for _ in range(V["C_OUT_POL"]["n"]):
        B.part("CPOL100", {"1": "+24V_HVAUX", "2": "GND"})
    for _ in range(V["C_OUT_MLCC"]["n"]):
        C(B, "C_OUT_MLCC", "+24V_HVAUX", "GND")
    R(B, "R_PRELOAD", "+24V_HVAUX", "GND")
    B.flag("+24V_HVAUX")
    B.flag("GND")
    B.block("Feedback across the barrier", "ATL431 integrates (C_Z %s); %s.\n%s %s + %s. CNY65B collector pulls COMP on the "
            "primary." % (V["C_Z"]["value"], V["R_LED"]["why"], V["R_FF"]["why"], V["R_FF"]["value"], V["C_FF"]["value"]))
    R(B, "R_LED", "+24V_HVAUX", "LED_A")
    R(B, "R_BIAS", "LED_A", "K431")
    iso.add(B.part("CNY65B", {"1": "LED_A", "2": "K431", "4": "COMP", "3": "PGND"}).ref)
    B.part("ATL431", {"1": "K431", "2": "FBS", "3": "GND"})
    R(B, "R_UP", "+24V_HVAUX", "FBS")
    R(B, "R_LO", "FBS", "GND")
    C(B, "C_Z", "K431", "FBS")
    R(B, "R_FF", "+24V_HVAUX", "FFM")
    C(B, "C_FF", "FFM", "FBS")
    B.block("PG_HVAUX, driven actively high (interfaces.AUX, INT-13)",
            "%s.\nTPS3700 on a 3.3 V Zener rail; OUTA/OUTB low outside the window; SN74LVC1G17 (Ioff) drives the line:\n"
            "%s. Dead or unplugged -> unpowered buffer -> the SYS-IO-AUX 100k pull-down reads not-good." % (
                V["R_PG_A"]["why"], V["R_PGO"]["why"]))
    R(B, "R_PGR", "+24V_HVAUX", "PG3V3")
    B.part("BZX84-B3V3", {"1": "GND", "2": None, "3": "PG3V3"})
    C(B, "C_PGR", "PG3V3", "GND")
    B.flag("PG3V3")
    B.part("TPS3700", {"5": "PG3V3", "3": "PG_UV", "4": "PG_OV", "2": "GND", "1": "PGW", "6": "PGW"})
    R(B, "R_PG_A", "+24V_HVAUX", "PG_UV")
    R(B, "R_PG_B", "PG_UV", "PG_OV")
    R(B, "R_PG_C", "PG_OV", "GND")
    C(B, "C_PGF", "PG_UV", "GND")
    R(B, "R_PGW", "PG3V3", "PGW")
    B.part("LVC1G17", {"5": "PG3V3", "3": "GND", "2": "PGW", "1": None, "4": "PGO"})
    R(B, "R_PGO", "PGO", "PG_HVAUX")
    B.TP("PG_HVAUX")
    B.block("Output connector (interfaces.AUX)", "1 +24V_HVAUX, 2 GND, 3 PG_HVAUX - mates SYS-IO-AUX feed 3 (J103).")
    B.part("J_OUT", IF.pins(IF.AUX))


def build_design():
    s = SPEC["summary"]
    eta = s["eta"]
    B = L.Builder(PROJECT, "AUX-HV (builds PV, DAB-D60)", REV, DATE, CATALOG,
                  rails=["+24V_HVAUX", "VDD", "VREF", "HV_BULK"], returns=["GND", "PGND"],
                  subtitle="22.8 V / 30 W isolated control rail from the DC ports, 200-1000 V (1100 V transient)",
                  comment1="Barrier: T1 (PD-tested, reinforced) + CNY65B, conformally coated (PD1) - D-032",
                  comment4="Builds: PV all fitted / DAB-D60 port B not fitted (DAB BOM). Not bench-validated.",
                  root_notes=[
                      "BUILD OPTIONS: PV = all parts fitted (bom/AUX-HV_BOM.csv). DAB-D60 = port 1 only (D-021): the "
                      "port-B input parts marked 'PV build only' (2 studs, %d x 22 R, fuse, 2 OR diodes) are not fitted "
                      "(bom/AUX-HV_DAB_BOM.csv)." % V["R_LIM"]["n"],
                      "CONFORMAL COATING (IC-17, D-032): coated to pollution degree 1 (IEC 60664-3 type 1) over the "
                      "HV-PELV barrier parts T1, CNY65B and their HV copper. T1: PD <= 10 pC at 2461 Vpk on every unit, "
                      "4400 V rms 60 s, 8 kV impulse.",
                      "CALCULATED (sim/aux_hv_design.py rev C1): DCM flyback %.1f kHz; start %.0f V (worst %.0f V), stop "
                      "%.0f-%.0f V; out %.2f-%.2f V; eta %.1f/%.1f/%.1f/%.1f/%.1f %% at 200-1000 V (T1 design M1); "
                      "switch %s (alt. %s), gate %.1f-%.1f V." % (
                          s["fsw"] / 1e3, SPEC["start_up_voltage_nominal_V"], SPEC["start_up_voltage_V"],
                          SPEC["stop_voltage_V"][0], SPEC["stop_voltage_V"][2], s["vout"][0], s["vout"][2],
                          eta["200"] * 100, eta["400"] * 100, eta["600"] * 100, eta["800"] * 100, eta["1000"] * 100,
                          SPEC["switch"]["main"], SPEC["switch"]["alternate"], *SPEC["switch"]["vgs_V"]),
                      "Protections: OV lockout %.0f-%.0f V; 4 kV/20 us residual -> HV_BULK %.0f V; foldback %.1f kHz; "
                      "overload timer %.0f-%.0f ms -> hiccup. Rating 30 W continuous, %.0f W <= %.0f ms (SYS rev E 27.8 W, "
                      "30.15 W <= 200 s). V_DS %.0f / %.0f V at 1000 / 1100 V." % (
                          s["ov_lockout"][0], s["ov_lockout"][2], s["surge_bulk_max"], s["f_fold"] / 1e3,
                          s["timer_ms"][0], s["timer_ms"][2], SPEC["rating"]["short_W"], SPEC["rating"]["short_ms"],
                          s["vds_cont"], s["vds_trans"]),
                      "LAYOUT (not dimensions): reinforced HV <-> PELV for 1000 V DC, PD2, OVC II (IEC 62477-1 / "
                      "62109-1, ECO-10); only T1 and the CNY65B cross; 1000 V spacing between HV nets and between the "
                      "ports; >= 4 cm2 2-oz Cu per SMCJ70A; VDD follower <= 85 C, away from T1 / SiC / clamp (sim "
                      "report sec. 4, 6, 11)."])
    iso, port_b = set(), set()
    sheet_input(B, port_b)
    sheet_startup(B)
    sheet_power(B, iso)
    sheet_secondary(B, iso)
    return B, iso, port_b


class _BomView:
    """Read-only view of the board BOM for one build option (L.write_bom only reads .bom)."""

    def __init__(self, bom, dnp_refs):
        self.bom = {ref: dict(m, dnp=True) if ref in dnp_refs else m for ref, m in bom.items()}


def write_dab_bom(B, port_b):
    path = os.path.join(L.REPO, "bom", PROJECT + "_DAB_BOM.csv")
    res = L.Results()
    L.write_bom(_BomView(B.bom, port_b), path, res)
    n = len(port_b)
    expect = 2 + V["R_LIM"]["n"] + 1 + 2                       # studs, resistors, fuse, OR diodes
    res.add("DAB-D60 build option: port-B input parts not fitted", n == expect and all(r in B.bom for r in port_b),
            "%d parts marked DNP (%s) -> %s" % (n, " ".join(sorted(port_b, key=L.natural)), os.path.relpath(path, L.REPO)))
    return all(r["result"] == "pass" for r in res)


# ================================================================================ AUX75: reusable 75 W supply block
# The cost-first module's only supply (D-044, ARCHITECTURE-COSTFIRST.md sec. 3), drawn by the power-board generator
# with aux75_block(). Values: sim/out/aux_hv_design/aux75_spec.json (case AUX75 of sim/aux_hv_design.py). Same
# circuit as sheets 01-03 above except: one tap per port (+ pole; the return is BUS-), the live 24 V winding replaces
# the aux winding (FB divider, VDD follower and foldback detector on it), no opto / shunt regulator, a reinforced SELV
# winding, 4 x SMCJ54A clamp, hold-up bank, TPS3700 window (status + output-OV stop). gen/aux_hv.py's own builds do
# not call it.
SPEC75_PATH = os.path.join(L.REPO, "sim", "out", "aux_hv_design", "aux75_spec.json")
CATALOG.update({
    # Bourns SMCJ (SMCJ_Bourns.pdf): p2 SMCJ33A VBR 36.7-40.6 V, VC 53.3 V @ 28.1 A; p1 1500 W, PM(AV) 5 W, band = cathode
    "SMCJ33A": dict(mfr="Bourns", mpn="SMCJ33A", prefix="D", pkg="DO-214AB (SMC)", stock=("Device", "D_Zener"),
                    ds=DS + "protection/SMCJ_Bourns.pdf", desc="TVS unidirectional 1500 W, VRWM 33 V, VBR 36.7-40.6 V (AUX75 clamp, 6 in series)"),
    # Vishay AC series (AC-AC-AT-AC-NI.pdf): part number AC10000 + 0 + 0 + 3-digit value + multiplier (9 = x 0.1) + J +
    # AB + 00, values from E24 (note 1) -> 20 R = AC10000002009JAB00; ratings as the 22 R (AC10_22R)
    "AC10_20R": dict(mfr="Vishay Draloric", mpn="AC10000002009JAB00", prefix="R", pkg="axial cemented wirewound 10 W, 44 x 8 mm",
                     ds=DS + "passives-capacitors/AC-AC-AT-AC-NI.pdf", stock=("Device", "R"),
                     desc="Resistor 20 R 5 % 10 W cemented wirewound, pulse-rated (AUX75 tap, ahead of the fuse)"),
    # Rubycon ZLH (Rubycon-ZLH.pdf) p2: 35ZLH1500MEFC12.5X30 1500 uF 35 V, 3.45 A rms, Z 0.013 R; p1 10000 h @ 105 C
    "CHOLD1500": dict(mfr="Rubycon", mpn="35ZLH1500MEFC12.5X30", prefix="C", pkg="radial 12.5 x 30 mm, 5.0 mm pitch",
                      ds=DS + "passives-capacitors/Rubycon-ZLH.pdf", stock=("Device", "C_Polarized"),
                      desc="Aluminium electrolytic 1500 uF 35 V, low Z, 10000 h @ 105 C (AUX75 live hold-up)"),
    "AUXT1_75": dict(mfr="CUSTOM", mpn="AUX-T1 75 W (CUSTOM)", prefix="T", ds="",
                     sourcing="CUSTOM", pkg="ETD 39 class, potted, 14-pin former",
                     desc="CUSTOM AUX-T1: primary, shield, live 24 V winding (functional to the primary), SELV 24 V "
                          "winding REINFORCED to all others; electrical requirement in sim/out/aux_hv_design/aux75_spec.json",
                     pins={"left": ["1 PS p", "2 PF p", None, "3 SH p", None, "4 LS p", "5 LF p"], "right": ["9 SS p", None, "8 SF p"]}),
})
AUX75_EST = {"IV2Q171R0D7Z": 2.50,                                                             # ARCHITECTURE sec. 14
             "FCSA3DS335K050ID90BE3": 1.50, "PCV1HVF101MB12FV-WE3": 0.30, "35ZLH1500MEFC12.5X30": 0.40, "SMCJ33A": 0.10,
             "AC10000002009JAB00": 0.93,
             "0090.1001": 3.00, "VS-8ETU04S-M3": 1.13, "CRHV2512AF10M0FKFB": 0.25, "BZX84-B20,215": 0.02,
             "BZX84-A18,215": 0.03, "BZX84-B6V8,215": 0.02}
# ESTIMATES where gen/data/prices.csv has no usable row: the switch, transformer and rectifier as the costed BOM of
# ARCHITECTURE-COSTFIRST.md sec. 14; the rest class figures (10 x 38 mm gPV fuse, polymer / electrolytic, 2512 HV chip)
GENERIC_USD = 0.006                 # chip R / MLCC class average (cost_estimates.csv class rows: 0.004-0.03 USD)


def aux75_block(B, a_tap="A_T+", b_tap="B_T+", bus_n="BUS-", live="+24V_LIVE", selv_p="+24V_SELV", selv_n="SELV_0V",
                status="AUX_OK", prefix="AUX_", sheet_no=60):
    """Draw the 75 W module supply into the caller's builder B on four sheets (sheet_no .. sheet_no + 3).
    a_tap, b_tap: the ports' + terminal taps; bus_n: the common return (BUS-); live: the live 24 V on bus_n (contactor
    coils, 5 V buck, VDD follower); selv_p / selv_n: the reinforced SELV 24 V pair (fans, communication); status: open
    drain, released while the live 24 V is inside its window (pull up to <= 5.5 V at the user). Internal nets get prefix.
    Returns dict(isolators={T1 ref} for L.build, selv_nets for domain_of, vrange={net: (lo, hi)} for the stress check,
    refs, spec)."""
    s75 = json.load(open(SPEC75_PATH))
    v = s75["values"]
    for k in s75["parts"]:
        B.catalog.setdefault(k, CATALOG[k])
    if s75.get("transformer"):                                        # the magnetics design's construction (A-51)
        B.catalog["AUXT1_75"] = dict(CATALOG["AUXT1_75"], desc="CUSTOM AUX-T1 75 W rev %s: %s; electrical requirement in "
                                     "sim/out/aux_hv_design/aux75_spec.json" % (s75["transformer"]["rev"], s75["transformer"]["construction"]))
    rlim_key = v["R_LIM"].get("part", "AC10_22R")
    tvs_key = [k for k in s75["parts"] if k.startswith("SMCJ")][0]
    ext = {"BUS-": bus_n, "LIVE": live, "SELV_P": selv_p, "SELV_N": selv_n, "STATUS": status}
    n = lambda net: None if net is None else ext.get(net, prefix + net)
    refs = []

    def part(key, pins, value=None):
        refs.append(B.part(key, {p: n(x) for p, x in pins.items()}, value=value).ref)
        return refs[-1]

    def r(key, a, b, value=None):
        e = v[key]
        refs.append(B.R(value or e["value"], n(a), n(b), pkg=e.get("pkg", "0603"), tol=e.get("tol", "1%")).ref)

    def c(key, a, b):
        e = v[key]
        refs.append(B.C(e["value"], n(a), n(b), pkg=e.get("pkg", "0603"), volt=e.get("volt", "50V"),
                        diel=e.get("diel", "X7R"), tol=e.get("tol", "")).ref)
    dp, tr = s75["design_point"], s75["transformer_requirement"]
    B.new_sheet("%02d_aux75_input" % sheet_no, "AUX75 input taps, OR, film",
                "One tap per port (+ pole, terminal side), return = %s:\n2 x %s R pulse-rated ahead of the 1 A / 1000 VDC fuse,"
                " BYG10Y OR;\n2 x 3.3 uF / 1300 V film = surge absorber (AUX-HV rev C1 input block)" % (bus_n, v["R_LIM"]["value"]))
    for tap, t in ((a_tap, "PA"), (b_tap, "PB")):
        B.block("Tap %s" % tap, "%s\nFuse: %s" % (v["R_LIM"]["why"], v["F_PORT"]["why"]))
        chain = [tap] + [prefix + "%s_R%d" % (t, i + 1) for i in range(v["R_LIM"]["n"] - 1)] + [prefix + t + "_RF"]
        for a, b in zip(chain, chain[1:]):
            refs.append(B.part(rlim_key, {"1": a, "2": b}, value=v["R_LIM"]["value"]).ref)
        part("FUSE_HV", {"1": t + "_RF", "2": t + "_F"})
        part("BYG10Y", {"2": t + "_F", "1": "HV_BULK"})
    B.block("Surge absorber / input filter", "Film: %s." % v["C_BULK"]["why"])
    for _ in range(v["C_BULK"]["n"]):
        part("C4AQ_3U3", {"1": "HV_BULK", "2": "BUS-"}, value=v["C_BULK"]["value"])
    B.flag(n("HV_BULK"))
    B.flag(bus_n)
    B.new_sheet("%02d_aux75_startup" % (sheet_no + 1), "AUX75 start-up, brown-in, OV, timer",
                "As AUX-HV rev C1 sheet 02: 3 x BSS126 cascode, TPS3710 brown-in %.0f-%.0f V,\n"
                "TLV3202 OV lockout %.0f-%.0f V and overload timer %.0f-%.0f ms" % (s75["startup"]["brown_in_V"][0], s75["startup"]["brown_in_V"][2],
                                                               s75["startup"]["ov_lockout_V"][0], s75["startup"]["ov_lockout_V"][2],
                                                               s75["protection"]["timer_ms"][0], s75["protection"]["timer_ms"][2]))
    B.block("Compensated tap string", "%s\n%s" % (v["R_D1"]["why"], v["R_HYS"]["why"]))
    for a, b in (("HV_BULK", "SU_T2"), ("SU_T2", "SU_T1"), ("SU_T1", "LSO")):
        part("CRHV_10M", {"1": a, "2": b}, value=v["R_STR"]["value"])
        c("C_STR", a, b)
    r("R_D1", "LSO", "LS")
    c("C_D1", "LSO", "LS")
    r("R_D2", "LS", "BUS-")
    c("C_D2", "LS", "BUS-")
    B.block("Depletion cascode current source", "VREF high -> start-up off (as AUX-HV).")
    part("BSS126", {"3": "HV_BULK", "1": "SU_T2", "2": "SU_S2"})
    part("BSS126", {"3": "SU_S2", "1": "SU_T1", "2": "SU_S1"})
    part("BSS126", {"3": "SU_S1", "1": "SU_G", "2": "SU_RS"})
    r("R_SU_S", "SU_RS", "VDD")
    r("R_SU_G", "SU_G", "VDD")
    part("2N7002BK", {"1": "SU_EN", "2": "BUS-", "3": "SU_G"})
    r("R_SU_EN", "VREF", "SU_EN")
    r("R_SU_EN_PD", "SU_EN", "BUS-")
    B.block("Line brown-in", "LINE_OK low -> BAT54 holds COMP below the 1.15 V offset.")
    part("TPS3710", {"5": "VREF", "3": "LS", "2": "BUS-", "4": "BUS-", "6": "BUS-", "1": "LINE_OK"})
    c("C_DEC", "VREF", "BUS-")
    r("R_LINE_PU", "VREF", "LINE_OK")
    r("R_HYS", "LINE_OK", "LS")
    part("BAT54", {"1": "COMP", "2": None, "3": "LINE_OK"})
    B.block("Input OV lockout and overload timer", "Ch 1: OV lockout -> COMP low. Ch 2: %s; %s -> latch -> SS crowbar -> hiccup." % (
        v["R_TA"]["why"], v["C_TMR"]["why"]))
    part("ATL431", {"1": "VR25", "2": "VR25", "3": "BUS-"})
    r("R_REF", "VREF", "VR25")
    part("TLV3202", {"8": "VREF", "4": "BUS-", "2": "LSOF", "3": "OVREF", "1": "OVL_N", "5": "COMPF", "6": "VR25", "7": "OLATCH"})
    c("C_DEC", "VREF", "BUS-")
    r("R_LSF", "LSO", "LSOF")
    c("C_LSF", "LSOF", "BUS-")
    r("R_OVH1", "VR25", "OVREF")
    r("R_OVH2", "OVL_N", "OVREF")
    part("BAT54", {"1": "COMP", "2": None, "3": "OVL_N"})
    r("R_TA", "COMP", "COMPF")
    r("R_TB", "COMPF", "BUS-")
    c("C_TMR", "COMPF", "BUS-")
    part("BAS16", {"1": "OLATCH", "2": None, "3": "COMPF"})
    r("R_SSG", "OLATCH", "SSG")
    r("R_SSG_PD", "SSG", "BUS-")
    part("2N7002BK", {"1": "SSG", "2": "BUS-", "3": "SS"})
    B.new_sheet("%02d_aux75_power" % (sheet_no + 2), "AUX75 PWM, SiC, clamp, AUX-T1",
                "UCC28C59-Q1 %.1f kHz, %s, Lp %.3f mH, Np:Nlive:Nselv %d:%d:%d;\n"
                "live 24 V regulated by the FB divider (no opto); VDD from the live 24 V\n"
                "through the BC817 follower; foldback detector on the live winding" % (
                    dp["fsw_kHz"], s75["switch"]["primary"], dp["lp_mH"], tr["turns"]["np"], tr["turns"]["n_live"], tr["turns"]["n_selv"]))
    B.block("PWM controller and error amplifier", "%s\n%s %s %s" % (v["R_FBT"]["why"], v["R_EA"]["why"], v["C_EA"]["why"], v["C_EAHF"]["why"]))
    part("UCC28C59", {"7": "VDD", "8": "VREF", "4": "RTCT", "2": "FB", "5": "BUS-", "6": "OUT", "3": "CS", "1": "COMP"})
    c("C_VREF", "VREF", "BUS-")
    r("R_T", "VREF", "RTCT")
    c("C_T", "RTCT", "BUS-")
    for _ in range(v["C_VDD"]["n"]):
        c("C_VDD", "VDD", "BUS-")
    c("C_DEC", "VDD", "BUS-")
    part("BZX84-B20", {"1": "BUS-", "2": None, "3": "VDD"})
    B.flag(n("VDD"))
    r("R_FBT", "LIVE", "FB")
    r("R_FBB", "FB", "BUS-")
    r("R_EA", "COMP", "EA_M")
    c("C_EA", "EA_M", "FB")
    c("C_EAHF", "COMP", "FB")
    part("BAT54S", {"1": "COMP", "3": "SS", "2": "VREF"})
    r("R_SS", "VREF", "SS")
    c("C_SS", "SS", "BUS-")
    B.block("VDD follower from the live 24 V", v["R_ZREG"]["why"])
    r("R_ZREG", "LIVE", "ZB")
    part("BZX84-A18", {"1": "BUS-", "2": None, "3": "ZB"})
    part("BC817-25", {"1": "ZB", "3": "LIVE", "2": "VDD_E"})
    part("BAT54", {"1": "VDD_E", "2": None, "3": "VDD"})
    r("R_VDDE", "VDD_E", "BUS-")
    B.block("Frequency foldback on the live winding plateau (AX-03)", v["C_TX"]["why"])
    part("US1G", {"2": "LIVE_A", "1": "AFR"})
    r("R_AF1", "AFR", "AF1")
    c("C_AF", "AF1", "BUS-")
    r("R_AF2", "AF1", "BUS-")
    part("BZX84-B6V8", {"3": "AF1", "2": None, "1": "QIG"})
    r("R_QG", "QIG", "BUS-")
    part("2N7002BK", {"1": "QIG", "2": "BUS-", "3": "FBK"})
    r("R_FBK", "VREF", "FBK")
    part("2N7002BK", {"1": "FBK", "2": "BUS-", "3": "CTXD"})
    c("C_TX", "RTCT", "CTXD")
    B.block("SiC switch, current sense, line feed-forward, clamp", "%s\n%s\nClamp: BYG10Y + %d x %s; V_DS %.0f / %.0f V at 1000 / "
            "1100 V." % (v["R_CS"]["why"], v["R_FF"]["why"], s75["parts"][tvs_key], tvs_key, s75["protection"]["vds_V"]["cont"],
                         s75["protection"]["vds_V"]["trans"]))
    r("R_G", "OUT", "GATE")
    r("R_GS", "GATE", "SRC")
    part("SW1700", {"1": "GATE", "2": "SRC", "3": "SRC", "4": "SRC", "5": "SRC", "6": "SRC", "7": "SRC", "8": "DRAIN"})
    for _ in range(v["R_CS"]["n"]):
        r("R_CS", "SRC", "BUS-")
    r("R_CSF", "SRC", "CS")
    c("C_CSF", "CS", "BUS-")
    part("CRHV_10M", {"1": "HV_BULK", "2": "CS"}, value=v["R_FF"]["value"])
    for _ in range(v["C_HF"]["n"]):
        c("C_HF", "HV_BULK", "BUS-")
    part("BYG10Y", {"2": "DRAIN", "1": "CLMP"})
    chain = ["CLMP"] + ["TVM%d" % i for i in range(1, s75["parts"][tvs_key])] + ["HV_BULK"]
    for a, b in zip(chain, chain[1:]):
        part(tvs_key, {"1": a, "2": b})
    B.block("AUX-T1 (CUSTOM - the module's reinforced supply barrier B1)",
            "Lp %.3f mH +/-7 %%; shield and live winding on %s; SELV winding reinforced (PD <= 10 pC routine test).\n"
            "Electrical requirement: aux75_spec.json transformer_requirement." % (dp["lp_mH"], bus_n))
    t1 = part("AUXT1_75", {"1": "DRAIN", "2": "HV_BULK", "3": "BUS-", "4": "LIVE_A", "5": "BUS-", "9": "SELV_A", "8": "SELV_N"})
    B.new_sheet("%02d_aux75_outputs" % (sheet_no + 3), "AUX75 live 24 V, SELV 24 V, status",
                "Live %.2f-%.2f V (FB); SELV %.1f-%.1f V over the load table,\ngates off %.1f-%.1f V;"
                " hold-up %.0f ms at %.0f W;\nstatus = TPS3700 OUTA (UV); OUTB (OV) pulls SS low" % (
                    *s75["regulation"]["live_V"], *s75["regulation"]["selv_V_all"], *s75["regulation"]["selv_V_gates_off"],
                    s75["holdup"]["t_ms"], s75["holdup"]["load_W"]))
    B.block("Live 24 V rectifier, hold-up", "D2PAK tab = cathode on the output. Snubber %s + %s." % (v["R_SN"]["value"], v["C_SN"]["value"]))
    part("VS-8ETU04S", {"3": "LIVE_A", "1": None, "2": "LIVE"})
    r("R_SN", "LIVE_A", "SN_L")
    c("C_SN", "SN_L", "LIVE")
    for _ in range(2):
        part("CHOLD1500", {"1": "LIVE", "2": "BUS-"})
    part("CPOL100", {"1": "LIVE", "2": "BUS-"})
    for _ in range(v["C_LIVE_MLCC"]["n"]):
        c("C_LIVE_MLCC", "LIVE", "BUS-")
    B.flag(n("LIVE"))
    B.block("SELV 24 V rectifier (reinforced side)", "Cross-regulated from the live winding.")
    part("VS-8ETU04S", {"3": "SELV_A", "1": None, "2": "SELV_P"})
    r("R_SN", "SELV_A", "SN_S")
    c("C_SN", "SN_S", "SELV_P")
    for _ in range(3):
        part("CPOL100", {"1": "SELV_P", "2": "SELV_N"})
    for _ in range(v["C_SELV_MLCC"]["n"]):
        c("C_SELV_MLCC", "SELV_P", "SELV_N")
    B.flag(n("SELV_P"))
    B.flag(n("SELV_N"))
    B.block("Status and output OV stop (TPS3700 on a 3.3 V Zener rail)", v["R_PG_A"]["why"])
    r("R_PGR", "LIVE", "PG3V3")
    part("BZX84-B3V3", {"1": "BUS-", "2": None, "3": "PG3V3"})
    c("C_PGR", "PG3V3", "BUS-")
    B.flag(n("PG3V3"))
    part("TPS3700", {"5": "PG3V3", "3": "PG_UV", "4": "PG_OV", "2": "BUS-", "1": "STATUS", "6": "PG_OVN"})
    r("R_PG_A", "LIVE", "PG_UV")
    r("R_PG_B", "PG_UV", "PG_OV")
    r("R_PG_C", "PG_OV", "BUS-")
    c("C_PGF", "PG_UV", "BUS-")
    refs.append(B.R("100k", n("VREF"), n("PG_OVN"), pkg="0603").ref)
    part("BAT54", {"1": "SS", "2": None, "3": "PG_OVN"})
    # asserts: exactly the catalog parts the sim's case lists, and every value present
    drawn = collections.Counter(B.bom[x]["mpn"] for x in refs if B.bom[x]["sourcing"] != "GENERIC")
    want = collections.Counter({CATALOG[k]["mpn"]: q for k, q in s75["parts"].items()})
    assert drawn == want, ("AUX75 parts differ from aux75_spec.json", drawn - want, want - drawn)
    assert s75["design_point"]["outputs"]["live"]["ref"] == "BUS-" and tr["turns"]["np"] > 0
    vr = {n(k): tuple(x) for k, x in s75["nets"].items()}
    return dict(isolators={t1}, selv_nets={n("SELV_P"), n("SELV_N"), n("SELV_A"), n("SN_S")}, vrange=vr, refs=refs, spec=s75)


def aux75_cost(B, refs):
    """Block cost at catalogue prices: gen/cost.py's best price (<= 1000 pcs) per MPN, stated estimates otherwise."""
    import cost
    book = cost.load_prices()[0]
    est = cost.load_estimates()
    lines, tot = [], 0.0
    for mpn, q in collections.Counter(B.bom[x]["mpn"] or "GENERIC" for x in refs).items():
        if mpn == "GENERIC":
            u, src = GENERIC_USD, "class estimate"
        elif book.get(mpn.upper()):
            _, u, pick = cost.best_price(book[mpn.upper()])
            src = pick["src"]
        elif mpn in est:
            u, src = est[mpn][0], "cost_estimates.csv"
        elif mpn in AUX75_EST:
            u, src = AUX75_EST[mpn], "ESTIMATE"
        else:
            u, src = 0.0, "UNPRICED"
        lines.append((mpn, q, u, src))
        tot += q * u
    return tot, sorted(lines, key=lambda x: -x[1] * x[2])


def aux75_selftest():
    """python gen/aux_hv.py --aux75: draws the block into a scratch board, runs the domain, stress, ERC and netlist
    checks there (outside hardware/), prints the block cost."""
    import shutil
    import tempfile
    B = L.Builder("AUX75-TEST", "AUX75 block test", "A0", DATE, {}, rails=[], returns=["BUS-"])
    out = aux75_block(B)
    for net in ("A_T+", "B_T+", "AUX_OK"):                 # stand-ins for the power board's connections
        B.TP(net)
    R = L.Results()
    L.check_domains(B, R, lambda net: "SELV" if net in out["selv_nets"] else "HV", out["isolators"])
    L.check_stress(B, R, out["vrange"].get)
    hw = tempfile.mkdtemp(prefix="aux75_")
    L.write_project(B, hw)
    erc, xml = os.path.join(hw, "erc.json"), os.path.join(hw, "net.xml")
    L.run([L.KCLI, "sch", "erc", "--format", "json", "--severity-all", "-o", erc, "AUX75-TEST.kicad_sch"], cwd=hw, check=False)
    L.run([L.KCLI, "sch", "export", "netlist", "--format", "kicadxml", "-o", xml, "AUX75-TEST.kicad_sch"], cwd=hw)
    L.check_erc(erc, R)
    L.check_netlist_identity(B.D, xml, R)
    shutil.rmtree(hw, ignore_errors=True)
    tot, lines = aux75_cost(B, out["refs"])
    for mpn, q, u, src in [x for x in lines if x[3] != "class estimate"]:
        print("  %-52s %3d x %7.3f = %6.2f USD (%s)" % (mpn[:52], q, u, q * u, src))
    print("AUX75 block: %d parts, %.2f USD at catalogue prices (estimates where marked)" % (len(out["refs"]), tot))
    with open(os.path.join(L.REPO, "sim", "out", "aux_hv_design", "aux75_block_cost.csv"), "w") as fh:
        fh.write("mpn,qty,unit_usd,source\n" + "".join('"%s",%d,%.4f,%s\n' % ln for ln in lines))
    return all(r["result"] == "pass" for r in R)


if __name__ == "__main__":
    if "--aux75" in sys.argv:
        sys.exit(0 if aux75_selftest() else "AUX75 block: FAILED")
    B, iso, port_b = build_design()
    if not write_dab_bom(B, port_b):
        sys.exit("%s: FAILED (DAB-D60 build option)" % PROJECT)
    sys.exit(L.build(B, domain_of=domain_of, isolators=iso, vrange=VDC.get))
