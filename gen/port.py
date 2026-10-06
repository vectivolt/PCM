"""DC PORT block + PV-PORT board (REQUIREMENTS.md PV-03..PV-08, PV-C3..C5, ECO-07, ECO-09, ECO-10).

One port = everything between a module's DC terminals and its internal bus: terminals, a fuse on each pole, DC SPD to
PE, X/Y + common-mode filter, bidirectional main contactor (+ pole), precharge relay + resistor, 100 uOhm shunt in the
- pole, fail-safe active discharge (inhibited while the contactor is closed, time-limited, thermal cut-off) + passive
bleeder, voltage sense on both sides of the contactor and current sense as isolated-amplifier pairs of the PORT
contract, coil drives with two switches in every coil-current path, a two-channel hold-closed interlock with a
proof-test input and readback. Every value comes from sim/port_design.py (sim/out/port_design/port_spec.json - run
it first).

Reuse (the DAB board calls it twice, for two ports isolated from each other):

    import port
    CATALOG = dict(catalog.PARTS, **port.CATALOG)      # port.PARTS + gdrv's SN74LVC1G17 (skip it if you merge gdrv)
    p1 = port.port(B, "A", "P1_BUS+", "P1_BUS-", sheet_no=2, hv="HV1")   # adds sheets 02 + 03 (135 A default)
    p2 = port.port(B, "B", "P2_BUS+", "P2_BUS-", sheet_no=4, hv="HV2")   # adds sheets 04 + 05
    # PV-P100/110 (4 cells, 180 A per port): port.port(..., rating=180) - see report sec. 6.4 / 12 for its limits
    B.part("J_CTRL", {str(i): x for i, x in enumerate(port.CTRL_PINS, 1)})   # 14-way, one harness for every board
    # route p1["nets"]["rb1"], ["rb2"] (and p2's) to CTRL on a spare line (PV-PORT: ID-line DAC in board_io())
    domain_of = lambda n: {**p1["domains"], **p2["domains"]}.get(n, "PE" if n == "PE" else "PELV")
    L.build(B, waivers=port.WAIVERS, domain_of=domain_of, isolators=p1["isolators"] | p2["isolators"], crossings=...)

The Builder must declare rails "+24V", "+3V3S" and returns "GND", "AGND", "PE" (defaults of port(); pass other names
if the board differs) and carry +24V/GND/AGND/+3V3S/PE flags as PV-PORT's board_io() does.

The tag ("A"/"B") selects the PORT-contract pairs (V<tag>, V<tag>X, I<tag>) and the DO/DI names (K_<tag>_PRE,
K_<tag>_MAIN, FB_<tag>_MAIN, K_DISCH, DO_SPARE = proof test). Net names, references and the HV domain all derive from
the arguments.

PV-PORT (this file run as a script): port A (PV) + port B (bus/battery) with a COMMON NEGATIVE "BUS-" (non-isolated
buck-boost), so both ports are one HV domain; plus the insulation-resistance measurement (PV-C5), PORT connector,
control connector, ID resistor with the readback DAC, sensor supply and PE bond. A split-midpoint variant would pass
a different bus_neg (and hv) per port - nothing else in port() changes.

    .venv/bin/python gen/port.py
"""
import json
import re
import os
import sys

import catalog
import dcdclib as L
import gdrv
import interfaces as IF

PROJECT, REV, DATE = "PV-PORT", "F0", "2026-10-04"
DS = "docs/datasheets/"
TI = "Texas Instruments"
SPEC_PATH = os.path.join(L.REPO, "sim", "out", "port_design", "port_spec.json")
if not os.path.exists(SPEC_PATH):
    raise SystemExit("run .venv/bin/python sim/port_design.py first (it writes %s)" % os.path.relpath(SPEC_PATH, L.REPO))
S = json.load(open(SPEC_PATH))
ohm, farad = gdrv.ohm, gdrv.farad
# RFQ rating of the 220 ohm aluminium-housed precharge resistor (RPRE_AL), the same part on every battery-type port. rpre_duty()
# gives the duty of one bank: charge from 0 to v with C +10 % and R +5 % (tau), and a shorted bank on R -5 % until the abort at
# 1.1 tau + 30 ms relay release (port_spec lean convention). Covered (PCM-03): PCS-P125 409.4 uF (bank + leg films) from the
# 1050 V trip 248 J / 104 ms, shorted 762 J in 0.14 s; PV-P75 port B 334.8 uF and PV-P100 386.4 uF (incl. decoupling, PV-PWR
# design checks) from the 1144 V pole peak 241 / 278 J at 85 / 98 ms, shorted 774 / 864 J in 0.12 / 0.14 s
RPRE_RATING = dict(R=220.0, tol=0.05, e_charge_J=280.0, tau_s=0.105, e_short_J=900.0, t_short_s=0.15)


def rpre_duty(c, v, r_ohm=None):
    """(charge energy J, tau_max s, shorted-bank energy J, its duration s) of a bank of nominal capacitance c from v;
    r_ohm = another resistance of the same RFQ family (the four-wire inverter's 200 ohm, D-076), same energy rating"""
    r = dict(RPRE_RATING, R=r_ohm or RPRE_RATING["R"])
    tau = r["R"] * (1 + r["tol"]) * 1.1 * c
    return 0.5 * 1.1 * c * v ** 2, tau, v ** 2 / (r["R"] * (1 - r["tol"])) * (1.1 * tau + 0.03), 1.1 * tau + 0.03

def _rpre_al(r_ohm):
    """Catalog entry of the RFQ aluminium-housed precharge resistor at a value of the family (220 ohm ports, 200 ohm four-wire PCS)."""
    return dict(mfr="", mpn="", prefix="R", pkg="aluminium housing, M4", ds="", sourcing="RFQ", stock=("Device", "R"),
                desc="CHASSIS-MOUNTED RFQ precharge resistor %.0f ohm 5 %%, 1000 V DC (1144 V peak), single pulse >= %.0f J in "
                     "%.2f s (shorted bank to the abort) and >= %.0f J at tau %.0f ms (bank charge), basic insulation to the housing "
                     "(2200 V rms)" % (r_ohm, RPRE_RATING["e_short_J"], RPRE_RATING["t_short_s"], RPRE_RATING["e_charge_J"],
                                       RPRE_RATING["tau_s"] * 1e3))


AMC_PINS = {  # identical pinout AMC3330 (SBASA34B Fig 4-1 / Table 4-1, p3) and AMC3302 (SBASA11B Fig 5-1 / Table 5-1, p4)
    "left": ["6 INP i", "7 INN i", None, "1 DCDC_OUT po", "3 HLDO_IN pi", "5 HLDO_OUT po", "4 NC nc", None,
             "2 DCDC_HGND pi", "8 HGND pi"],
    "right": ["11 OUTP o", "10 OUTN o", "14 DIAG oc", None, "12 VDD pi", "13 LDO_OUT po", "16 DCDC_IN pi", None,
              "15 DCDC_GND pi", "9 GND pi"]}

# Control connector to SYS-IO-AUX (14-way, the same harness on PV-PORT and DAB60-PORT): the eight DO lines in
# interfaces.DO order (DO_SPARE = proof-test input), each FB line with its own GND return beside it (INT-02,
# SYS J702 order FB_A, GND, FB_B, GND), then two returns for the coil currents.
CTRL_PINS = IF.DO + ["FB_A_MAIN", "GND", "FB_B_MAIN", "GND", "GND", "GND"]
assert len(CTRL_PINS) == 14 and {"FB_A_MAIN", "FB_B_MAIN"} <= set(IF.DI) and "DO_SPARE" in IF.DO


def _r(mpn, value_desc, ds, pkg):
    return dict(mfr="Vishay", mpn=mpn, prefix="R", pkg=pkg, ds=ds, stock=("Device", "R"), desc=value_desc)


PARTS = {
    # ---- power path (chassis / busbar mounted unless noted)
    # TDK HVC43MC.pdf v02: p2 'main terminals without polarity (bi-directional)', p3 ratings, p7 pins (Coil+ pos 2,
    # Coil- pos 3, Aux COM pos 1, Aux NC pos 4; A1/A2 M6 studs), p8 ordering code HVC43-250A-24MC = B88269X7340C011
    "HVC43MC": dict(mfr="TDK Electronics", mpn="B88269X7340C011", prefix="K", pkg="chassis, M6 studs + Micro-Fit 4p",
                    ds=DS + "protection/HVC43MC.pdf",
                    desc="CHASSIS-MOUNTED main contactor HVC43-250A-24MC, 1000 VDC, 250 A, bidirectional (no polarity), "
                         "24 V coil 96 ohm, mirror aux contact NC",
                    pins={"left": ["A1 A1 p", None, "2 COIL+ p", "3 COIL- p"],
                          "right": ["A2 A2 p", None, "1 AUX_COM p", "4 AUX_NC p"]}),
    # Omron G7L-X.pdf (J216-E1-04): p1 ratings 25 A @ 1000 VDC two poles in series; p2 terminals 0/1 coil, 8(+) 6 4 2(-);
    # p4 picture 1: series link 6-4, current enters 8 (normal polarity)
    "G7L2AX": dict(mfr="Omron", mpn="G7L-2A-X DC24", prefix="K", pkg="PCB relay 52.5x35.5 mm", ds=DS + "protection/G7L-X.pdf",
                   desc="Precharge relay DPST-NO, poles in series: 25 A @ 1000 VDC normal polarity only, 24 V coil 250 ohm",
                   pins={"left": ["8 P1+ p", "6 P1 p", "4 P2 p", "2 P2- p"], "right": ["1 COIL p", "0 COIL p"]}),
    # Mersen NH-gPV-1000VDC.pdf (DS-LFPVHP10NH-16-0922): p1 1000 VDC, 50 kA at L/R 1 ms (UL); p2 HP10NH1GPV160
    # (E1028288) 160 A, 23 W at In / HP10NH2GPV250 (Y1037620) 250 A, 31 W at In; p2-p3 bases HPBB11PPR / HPBB21PPR
    "HP10NH1GPV160": dict(mfr="Mersen", mpn="HP10NH1GPV160", prefix="F", pkg="NH1 blade, in base HPBB11PPR",
                          ds=DS + "protection/NH-gPV-1000VDC.pdf", stock=("Device", "Fuse"),
                          desc="CHASSIS-MOUNTED fuse link gPV 1000 VDC 160 A NH1 (135 A port; base HPBB11PPR, Uimp 8 kV; "
                               "inlet air path; 135 A to 45 C inlet, then the firmware current limit)"),
    "HP10NH2GPV250": dict(mfr="Mersen", mpn="HP10NH2GPV250", prefix="F", pkg="NH2 blade, in base HPBB21PPR",
                          ds=DS + "protection/NH-gPV-1000VDC.pdf", stock=("Device", "Fuse"),
                          desc="CHASSIS-MOUNTED fuse link gPV 1000 VDC 250 A NH2 (180 A port; base HPBB21PPR, Uimp 8 kV; "
                               "inlet air path)"),
    "ETI_GBAT200": dict(mfr="ETI", mpn="004110760", prefix="F", pkg="NH1 blade, ETI NH1 base 1000 V DC",
                        ds=DS + "protection/ETI-Green-Protect.pdf", stock=("Device", "Fuse"),
                        desc="CHASSIS-MOUNTED fuse link NH1 gBat 200 A 1000 V DC, 30 kA (L/R 1 ms), battery ports 135 / 180 A"),
    # ON-BOARD SURGE PROTECTION (rev F0, replaces the DIN-rail arresters). Thinking-TVT-Series.pdf (2026.02): p9 TVT25751
    # 25 mm potting, V1mA 750 V, 465 VAC / 615 VDC, Vc 1240 V at 150 A, Imax 25 kA, 497 J (2 ms); p2 part code K = +/-10 %,
    # F = three-terminal, KG = straight leads; p14 thermal fuse element in series with the disc, monitor lead at their
    # junction. Pin numbers ours (1 = line / thermal-link end, 2 = disc end, 3 = monitor lead) - audit against p5-p7.
    "MOV_TVT25": dict(mfr="Thinking Electronic", mpn="TVT25751KFKG", prefix="RV", pkg="25 mm disc, 3 leads, potted",
                      ds=DS + "protection/Thinking-TVT-Series.pdf",
                      desc="Thermally protected varistor 615 VDC, Vc 1240 V @ 150 A, Imax 25 kA, thermal link + monitor lead",
                      pins={"left": ["1 L p"], "right": ["2 N p", "3 MON p"]}),
    # ETI-Green-Protect.pdf: CH14x51 gPV 1000 V d.c. (L/R 2 ms), 30 kA d.c. IEC 60269-6; 36 A = 002637115, pre-arc I2t
    # 450 A2s, operating 1190 A2s (catalogue table 'CH14x51 gPV 1000V d.c.'); CH14-PCB clip 006710340 ('Clip contact for
    # CH14 fuse-links'), two per link.
    "FUSE_SPD": dict(mfr="ETI", mpn="002637115", prefix="F", pkg="14x51 cartridge in 2 x CH14-PCB clips",
                     ds=DS + "protection/ETI-Green-Protect.pdf", stock=("Device", "Fuse"),
                     desc="Varistor branch fuse CH14x51 gPV 36 A 1000 V DC, 30 kA (L/R 2 ms), pre-arc 450 A2s"),
    "CLIP14": dict(mfr="ETI", mpn="006710340", prefix="X", pkg="PCB clip for 14 mm cartridge",
                   ds=DS + "protection/ETI-Green-Protect.pdf", stock=("Connector_Generic", "Conn_01x01"),
                   desc="Fuse clip CH14-PCB (two per varistor branch fuse)"),
    # Vishay CNY65.pdf (doc 83540): p1 reinforced (VDE 0884), VIORM 1800 Vpk, VIOTM 12 kV, creepage/clearance >= 14 mm;
    # p3 CNY65B CTR 100-200 % @ 10 mA; DIP-4 pins 1 A, 2 K, 3 E, 4 C (gen/aux_hv.py)
    "CNY65B": dict(mfr="Vishay", mpn="CNY65B", prefix="U", pkg="DIP-4 HV 600 mil (15.24 mm)", ds=DS + "isolation-interface/CNY65.pdf",
                   desc="Optocoupler phototransistor, reinforced, VIOTM 12 kV, creepage >= 14 mm: varistor-network monitor",
                   pins={"left": ["1 A p", "2 K p"], "right": ["4 C p", "3 E p"]}),
    "CMC160": dict(mfr="", mpn="", prefix="L", pkg="busbar toroid", ds="", sourcing="CUSTOM",
                   desc="CHASSIS-MOUNTED CUSTOM common-mode choke (135 A port): 2 windings, 160 A DC continuous each, "
                        "1000 VDC working, L_cm >= 0.5 mH @ 10 kHz and |Z_cm| >= 50 ohm 16-100 kHz (port_spec cm_choke), "
                        "no saturation with the 150 Hz CM current of port_spec cm_choke and the DM flux at rated current "
                        "(core, material and construction to be set by the magnetics design, MG-10); basic winding-core "
                        "/ winding-winding: impulse >= 6 kV, 2200 V rms 60 s, creepage >= 5.0 mm (PD2) (IC-09); "
                        "dT <= 40 K at 160 A",
                   pins={"left": ["1 A1 p", "3 B1 p"], "right": ["2 A2 p", "4 B2 p"]}),
    "CMC200": dict(mfr="", mpn="", prefix="L", pkg="busbar toroid", ds="", sourcing="CUSTOM",
                   desc="CHASSIS-MOUNTED CUSTOM common-mode choke (180 A port): 2 windings, 200 A DC continuous each, "
                        "1000 VDC working, L_cm >= 0.5 mH @ 10 kHz and |Z_cm| >= 50 ohm 16-100 kHz (port_spec cm_choke), "
                        "no saturation with the 150 Hz CM current of port_spec cm_choke and the DM flux at rated current "
                        "(core, material and construction to be set by the magnetics design, MG-10); basic winding-core "
                        "/ winding-winding: impulse >= 6 kV, 2200 V rms 60 s, creepage >= 5.0 mm (PD2) (IC-09); "
                        "dT <= 40 K at 200 A",
                   pins={"left": ["1 A1 p", "3 B1 p"], "right": ["2 A2 p", "4 B2 p"]}),
    "TERM200": dict(mfr="", mpn="", prefix="J", pkg="M8 stud", ds="", sourcing="CUSTOM",
                    stock=("Connector_Generic", "Conn_01x01"),
                    desc="CHASSIS-MOUNTED CUSTOM DC terminal: M8 busbar stud feed-through, 200 A, 1000 VDC, touch cover, "
                         "external creepage to PE >= 12.5 mm (group I) / 16.0 mm (IIIa), PD3 cooling-air path (IC-18/IC-21)"),
    "TERM250": dict(mfr="", mpn="", prefix="J", pkg="M10 stud", ds="", sourcing="CUSTOM",
                    stock=("Connector_Generic", "Conn_01x01"),
                    desc="CHASSIS-MOUNTED CUSTOM DC terminal: M10 busbar stud feed-through, 250 A, 1000 VDC, touch cover, "
                         "external creepage to PE >= 12.5 mm (group I) / 16.0 mm (IIIa), PD3 cooling-air path (IC-18/IC-21)"),
    # Bourns CSM2F-8518.pdf p1-p2: 100 uOhm +/-5 %, 36 W, TCR 125 ppm/K on test points; 2 bolts + voltage test points
    # (pin numbers ours: 1/2 bolts, 3/4 test points)
    "CSM2F8518": dict(mfr="Bourns", mpn="CSM2F-8518-L100J01", prefix="RS", pkg="busbar shunt 85x18 mm",
                      ds=DS + "sensing/CSM2F-8518.pdf",
                      desc="BUSBAR-MOUNTED shunt 100 uOhm 5 % 36 W, Kelvin test points (pins 3/4)",
                      pins={"left": ["1 BOLT1 p", "3 SENSE1 p"], "right": ["2 BOLT2 p", "4 SENSE2 p"]}),
    # Miba Miba-RST-200.pdf p15 (PA-05): RST 200 10-300 ohm +/-5 %, 200 W at 70 C for 120 s, <= 1000 V DC precharge
    # voltage, pulse 2300 J (tau 0.1 s) / 3150 J (0.2 s) / 4000 J (0.5 s), 3000 W for 3 s, 3.5 kV rms insulation, M5.
    # The sheet lists the series without ordering codes: value by RFQ.
    "RST200_PRE": dict(mfr="", mpn="", prefix="R", pkg="Miba RST 200 aluminium housing, M5", sourcing="RFQ",
                       ds=DS + "protection/Miba-RST-200.pdf", stock=("Device", "R"),
                       desc="CHASSIS-MOUNTED RFQ Miba RST 200 precharge resistor 220 ohm +/-5 %, <= 1000 V DC, "
                            "pulse 2300 J at tau 0.1 s, 3000 W for 3 s, insulation 3.5 kV rms"),
    "RST200_DIS": dict(mfr="", mpn="", prefix="R", pkg="Miba RST 200 aluminium housing, M5", sourcing="RFQ",
                       ds=DS + "protection/Miba-RST-200.pdf", stock=("Device", "R"),
                       desc="CHASSIS-MOUNTED RFQ Miba RST 200 discharge resistor 300 ohm +/-5 %, <= 1000 V DC, "
                            "pulse 4000 J at tau 0.5 s, 3000 W for 3 s, insulation 3.5 kV rms"),
    # X capacitor (PA-07): the KEMET C4AQ (C4AQ.pdf p5) allows only 1.5 x VNDC = 1950 V, 10 times; RFQ spec instead
    "XCAP_2U2": dict(mfr="", mpn="", prefix="C", pkg="radial film, 27.5 mm pitch", ds="", sourcing="RFQ",
                     stock=("Device", "C"),
                     desc="RFQ X capacitor film 2.2 uF, 1300 VDC at 70 C (1100 V at 85 C), ESR <= 16 mOhm, impulse "
                          ">= 4.5 kV peak (>= 1.1 x the 3.8 kV pole-pole level of the varistor Y, IC-10; leads at the "
                          "varistor network), no lifetime surge-count limit below 1000 events"),
    # Vishay VY1.pdf (doc 28537) p1-p3: Y1 500 VAC / 1500 VDC, 4.7 nF Y5U, bulk, inline kinked, 10 mm
    "VY1_4N7": dict(mfr="Vishay", mpn="VY1472M63Y5UQ63V0", prefix="C", pkg="disc 16 mm, 10 mm leads",
                    ds=DS + "passives-capacitors/VY1.pdf", stock=("Device", "C"), desc="Y1 capacitor 4.7 nF 500 VAC / 1500 VDC"),
    # Würth WE-7461057.pdf: REDCUBE press-fit, M3 thread, 100 A
    "STUD": dict(mfr="Wurth Elektronik", mpn="7461057", prefix="J", pkg="REDCUBE press-fit M3", ds=DS + "connectors/WE-7461057.pdf",
                 stock=("Connector_Generic", "Conn_01x01"), desc="PCB HV connection point (ring lug / busbar), M3, 100 A"),
    # ---- coil drive / suppression
    # TDK SIOV-Leaded-Standard.pdf: B72210S0300K101 = S10K30, 30 VRMS / 38 VDC, v(1 mA) 47 V, vc 93 V @ 5 A
    # (HVC43MC p7: varistor, clamping >= 50 V)
    "S10K30": dict(mfr="TDK Electronics", mpn="B72210S0300K101", prefix="RV", pkg="disc 10 mm", stock=("Device", "Varistor"),
                   ds=DS + "protection/SIOV-Leaded-Standard.pdf", desc="Varistor S10K30 38 VDC, contactor coil clamp"),
    # Diodes Inc S2M.pdf (S2A/A-S2M/A, ds16004): 1000 V 1.5 A, SMB, cathode = pin 1
    "S2M": dict(mfr="Diodes Inc", mpn="S2M-13-F", prefix="D", pkg="SMB", stock=("Device", "D"), ds=DS + "power-semiconductors/S2M.pdf",
                desc="Rectifier 1000 V 1.5 A"),
    # MDD MDD-SMBJ-series.pdf p3 table: SMBJ33A VRWM 33 V, VBR 36.7-40.6 V, VC 53.3 V, IPP 11.3 A (same JEDEC values as the Bourns
    # sheet it replaces; cathode band = unidirectional, p1) (Omron p5: zener 1-2 x coil voltage)
    "SMBJ33A": dict(mfr="MDD (Microdiode Semiconductor)", mpn="SMBJ33A", prefix="D", pkg="SMB", stock=("Device", "D_Zener"), ds=DS + "protection/MDD-SMBJ-series.pdf",   # MDD primary (LCSC C173526, in stock), Bourns SMBJ33A = alternate not on LCSC: sim/data/lcsc_semis.md P24, 2026-10-05
                    desc="TVS 33 V unidirectional, relay coil clamp zener"),
    # Vishay IRFL214.pdf (doc 91194) p1: 250 V, +/-20 V, RDS(on) <= 2.0 ohm at 10 V, VGS(th) 2-4 V, EAS 50 mJ;
    # SOT-223 1 G, 2 D, 3 S (tab = D)
    "IRFL214": dict(mfr="Vishay", mpn="IRFL214TRPbF", prefix="Q", pkg="SOT-223", ds=DS + "power-semiconductors/IRFL214.pdf",
                    desc="N-MOSFET 250 V 2.0 ohm: coil low-side switch", pins={"left": ["1 G i"], "right": ["2 D p", "3 S p"]}),
    # Vishay IRFR9214.pdf (doc 91282) p1: -250 V, +/-20 V, RDS(on) <= 3.0 ohm at -10 V; DPAK 1 G, 2 D, 3 S (tab = D)
    "IRFR9214": dict(mfr="Vishay", mpn="IRFR9214TRPbF", prefix="Q", pkg="DPAK (TO-252)",
                     ds=DS + "power-semiconductors/IRFR9214.pdf", desc="P-MOSFET -250 V 3.0 ohm: hold-closed high-side switch",
                     pins={"left": ["1 G i"], "right": ["3 S p", "2 D p"]}),
    # ---- fail-safe discharge / IMD switches
    # Wolfspeed C2M1000170D.pdf: 1700 V, 1 ohm, VGS -10/+25 V; package p.last: 1 gate, 2 drain, 3 source (TO-247-3)
    "C2M1000170D": dict(mfr="Wolfspeed", mpn="C2M1000170D", prefix="Q", pkg="TO-247-3", ds=DS + "power-semiconductors/C2M1000170D.pdf",
                        desc="SiC MOSFET 1700 V 1 ohm", pins={"left": ["1 G i"], "right": ["2 D p", "3 S p"]}),
    # Nexperia BSS138BK.pdf Table 2: 1 G, 2 S, 3 D; 60 V, VGS +/-20 V
    "BSS138BK": dict(mfr="Nexperia", mpn="BSS138BK", prefix="Q", pkg="SOT-23", ds=DS + "power-semiconductors/BSS138BK.pdf",
                     desc="N-MOSFET 60 V 360 mA", pins={"left": ["1 G i"], "right": ["3 D p", "2 S p"]}),
    # Nexperia BZX84.pdf Table 2: 1 anode, 2 n.c., 3 cathode
    "BZX84C15": dict(mfr="Nexperia", mpn="BZX84-C15", prefix="D", pkg="SOT-23", ds=DS + "protection/BZX84.pdf",
                     desc="Zener 15 V 250 mW", pins={"left": ["3 K p"], "right": ["1 A p", "2 NC nc"]}),
    # Nexperia BAS16.pdf Table 3 (SOT23): 1 anode, 2 n.c., 3 cathode; VR 100 V, IF 215 mA, IR <= 0.5 uA at 80 V
    "BAS16": dict(mfr="Nexperia", mpn="BAS16", prefix="D", pkg="SOT-23", ds=DS + "protection/BAS16.pdf",
                  desc="Switching diode 100 V 215 mA, low leakage", pins={"left": ["1 A p"], "right": ["3 K p", "2 NC nc"]}),
    # Diodes Inc MMBT3904.pdf (ds30036) / MMBT3906.pdf p1 pinout (SOT23 top view): 1 B, 2 E, 3 C; VCEO 40 V, VEBO 6 V
    # rev F0 (D-040 ADOPT, gen/data/alternates.csv): Nexperia MMBT3904,215, Nexperia-MMBT3904.pdf p1-4: same SOT-23 pins
    "MMBT3904": dict(mfr="Nexperia", mpn="MMBT3904,215", prefix="Q", pkg="SOT-23", ds=DS + "power-semiconductors/Nexperia-MMBT3904.pdf",
                     desc="NPN 40 V 200 mA", pins={"left": ["1 B p"], "right": ["3 C p", "2 E p"]}),
    "MMBT3906": dict(mfr="Diodes Inc", mpn="MMBT3906-7-F", prefix="Q", pkg="SOT-23", ds=DS + "power-semiconductors/MMBT3906.pdf",
                     desc="PNP 40 V 200 mA", pins={"left": ["1 B p"], "right": ["2 E p", "3 C p"]}),
    # Honeywell-3100U.pdf (selection guide 009100-2-EN) close-on-rise table: 3100U 00031461 closes 82 +/-3 C (180 F),
    # opens 71 +/-3 C (160 F); SPST hermetic, 2 terminals, flange
    "TSTAT_DIS": dict(mfr="", mpn="", prefix="S", pkg="KSD301 disc, metal cap, 2 screw feet", ds="", sourcing="RFQ",
                      stock=("Switch", "SW_SPST"),
                      desc="RESISTOR-MOUNTED RFQ KSD301-class bimetal thermostat, NORMALLY OPEN, closes 80 +/-5 C rising, "
                           "~15 K differential; class data 1000 V AC 1 min terminals-cap, <= 50 mOhm; dry-circuit duty "
                           "here (<= 0.2 mA at 15 V): gold or minimum-load statement required; sits at HV - mount over a "
                           "basic-insulating pad (1000 VDC working, 2200 V rms, OVC II)"),
    # TI TPSI3050-Q1 SLVSFJ7D Fig 5-1 / Table 5-1 p4; two-wire mode EN 6.5-48 V (p5, sec 8.3.6); reinforced 5 kVRMS,
    # VIOWM 1414 VDC (p6)
    "TPSI3050": dict(mfr=TI, mpn="TPSI3050QDWZRQ1", prefix="U", pkg="SOIC-8 wide (DWZ)", ds=DS + "isolation-interface/TPSI3050-Q1.pdf",
                     desc="Reinforced isolated switch driver, two-wire mode from a 24 V DO (EN), 10 V gate drive",
                     pins={"left": ["1 EN i", "2 PXFR i", "3 VDDP p", "4 VSSP pi"],
                           "right": ["8 VDRV o", "7 VDDH po", "6 VDDM po", "5 VSSS pi"]}),
    # ---- sensing
    "AMC3330": dict(mfr=TI, mpn="AMC3330DWE", prefix="U", pkg="SOIC-16 wide (DWE)", ds=DS + "sensing/AMC3330.pdf",
                    desc="Reinforced isolated amplifier +/-1 V in, gain 2, integrated DC/DC, VIOWM 1700 VDC", pins=AMC_PINS),
    "AMC3302": dict(mfr=TI, mpn="AMC3302DWE", prefix="U", pkg="SOIC-16 wide (DWE)", ds=DS + "sensing/AMC3302.pdf",
                    desc="Reinforced isolated amplifier +/-50 mV in, gain 41, integrated DC/DC, VIOWM 1700 VDC", pins=AMC_PINS),
    # Vishay TNPV-e3.pdf (doc 28881) p1-p3: TNPV1206 700 V 0.1 % 25 ppm/K VCR < 1 ppm/V; TNPV1210 1000 V
    "TNPV_1M": _r("TNPV12061M00BEEA", "HV thin-film resistor 1.00 M 0.1 % 25 ppm/K 700 V", DS + "passives-capacitors/TNPV-e3.pdf", "1206"),
    # rev F0 (D-040 ADOPT): PDC FVF06FT-2004, PDC-FVF.pdf p2-4: 1206, 2.00 M 1 %, max RCWV 800 V (707 V at 2 M), overload
    # 1600 V - discharge gate-bias string only (226 V per element at the trip overshoot; precision not needed)
    "TNPV_2M": dict(mfr="Prosperity Dielectrics", mpn="FVF06FT-2004", prefix="R", pkg="1206", stock=("Device", "R"),
                    ds=DS + "passives-capacitors/PDC-FVF.pdf", desc="HV thick-film resistor 2.00 M 1 % 100 ppm/K, 707 V working"),
    "TNPV_124K": _r("TNPV1210124KBEEA", "HV thin-film resistor 124 k 0.1 % 25 ppm/K 1000 V", DS + "passives-capacitors/TNPV-e3.pdf", "1210"),
    # Vishay TNPW-e3.pdf p1: TNPW1206 0.1 % 25 ppm/K
    "TNPW_4K99": _r("TNPW12064K99BEEA", "Thin-film resistor 4.99 k 0.1 % 25 ppm/K", DS + "passives-capacitors/TNPW-e3.pdf", "1206"),
    # ---- hold-closed interlock (two channels per main contactor) and shunt NTC
    # TI TLV3502 SBOS321E p3: 1 +INA, 2 -INA, 3 +INB, 4 -INB, 5 V-, 6 OUTB, 7 OUTA, 8 V+; VOS 6.5 mV, 6 mV hysteresis
    "TLV3502": dict(mfr=TI, mpn="TLV3502AIDCNR", prefix="U", pkg="SOT-23-8 (DCN)", ds=DS + "sensing/TLV3502.pdf",
                    desc="Dual comparator rail-to-rail push-pull: over-current detect for the contactor hold",
                    pins={"left": ["1 +INA i", "2 -INA i", "3 +INB i", "4 -INB i"],
                          "right": ["8 V+ pi", "7 OUTA o", "6 OUTB o", "5 V- pi"]}),
    # TI REF3030 (REF30/REF30E sheet) Table 5-1: 1 IN, 2 OUT, 3 GND; REF30E: 3.0 V +/-0.1 %, 15 ppm/K
    "REF3030E": dict(mfr=TI, mpn="REF3030EAIDBZR", prefix="U", pkg="SOT-23-3 (DBZ)", ds=DS + "sensing/REF3030.pdf",
                     desc="Voltage reference 3.0 V 0.1 % 15 ppm/K (hold threshold)",
                     pins={"left": ["1 IN pi"], "right": ["2 OUT po", "3 GND pi"]}),
    # TI SN74LVC1G32 SCES219W Table 4-1 (DBV): 1 A, 2 B, 3 GND, 4 Y, 5 VCC
    "LVC1G32": dict(mfr=TI, mpn="SN74LVC1G32DBVR", prefix="U", pkg="SOT-23-5 (DBV)", ds=DS + "isolation-interface/SN74LVC1G32.pdf",
                    desc="Single 2-input OR", pins={"left": ["1 A i", "2 B i"], "right": ["5 VCC pi", "4 Y o", "3 GND pi"]}),
    # TI SN74LVC1G11 SCES487I Table 4-1 (DBV): 1 A, 3 B, 6 C, 2 GND, 5 VCC, 4 Y
    "LVC1G11": dict(mfr=TI, mpn="SN74LVC1G11DBVR", prefix="U", pkg="SOT-23-6 (DBV)", ds=DS + "isolation-interface/SN74LVC1G11.pdf",
                    desc="Single 3-input AND", pins={"left": ["1 A i", "3 B i", "6 C i"], "right": ["5 VCC pi", "4 Y o", "2 GND pi"]}),
    # TDK B57703M.pdf: B57703M0103A018 10 k +/-2 %, B25/100 3988 K, metal tag, 200 mm AWG 26 leads, test 1000 VAC 1 s
    "NTC_SH": dict(mfr="TDK Electronics", mpn="B57703M0103A018", prefix="RT", pkg="metal-tag probe, 200 mm leads",
                   ds=DS + "sensing/B57703M.pdf", stock=("Device", "Thermistor_NTC"),
                   desc="CHASSIS: shunt NTC 10 k, clamped to the shunt copper over a REINFORCED insulating pad (1000 VDC "
                        "working, OVC II - the probe's own 1000 VAC/1 s test is not enough). Pad spec (IC-20): reinforced, AC 4400 V "
                        "rms 60 s, impulse >= 6 kV (8 kV without the surge credit), creepage around the tag >= 10.0 mm "
                        "(PD2) and >= the clearance at the claimed altitude; to SYS-IO-AUX NTC input"),
    # rev F0 (D-040 ADOPT): Kefa KF2EDGR-3.5-2P (KEFA-KF2EDGR-3.5.pdf p1), 3.5 mm, 2 pins, replaces Wurth 691322110002
    "J_NTC": dict(mfr="Cixi Kefa", mpn="KF2EDGR-3.5-2P", prefix="J", pkg="2p 3.5 mm pluggable header",
                  ds=DS + "connectors/KEFA-KF2EDGR-3.5.pdf", stock=("Connector_Generic", "Conn_01x02"),
                  desc="Shunt NTC to SYS-IO-AUX NTC input (harness)"),
    # ---- board: sensor supply, connectors, PE
    # TI LMR36015 SNVSB49D Pin Functions p4 (VQFN-HR 12); sec 10.2 3.3 V / 400 kHz: RFBT 100 k, RFBB 43.2 k, 10 uH, 2 x 22 uF
    "LMR36015": dict(mfr=TI, mpn="LMR36015ARNXR", prefix="U", pkg="VQFN-HR-12 (RNX)", ds=DS + "power-supply/LMR36015.pdf",
                     desc="Buck 4.2-60 V in, 1.5 A, 400 kHz, sensor supply 3.3 V",
                     pins={"left": ["2 VIN pi", "10 VIN pi", "9 EN i", None, "3 NC nc", None, "1 PGND pi", "11 PGND pi"],
                           "right": ["12 SW o", "4 BOOT p", "7 FB i", "8 PG oc", "5 VCC po", None, "6 AGND pi"]}),
    "WE_L10U": dict(mfr="Wurth Elektronik", mpn="74439346100", prefix="L", pkg="SMD 4040", stock=("Device", "L"),
                    ds=DS + "magnetics/WE-74439346100.pdf", desc="Power inductor 10 uH 5 A"),
    # Würth WE-61202621621.pdf: WR-BHD 2.54 mm box header 26 pins (PORT contract, interfaces.PORT)
    # rev F0 (D-040 ADOPT): XKB X9555WV-2x13-6TV01 (XKB-X9555WV.pdf p1), standard odd/even IDC numbering as Wurth
    "J_PORT": dict(mfr="XKB Connection", mpn="X9555WV-2x13-6TV01", prefix="J", pkg="2x13 2.54 mm box header", ds=DS + "connectors/XKB-X9555WV.pdf",
                   stock=("Connector_Generic", "Conn_02x13_Odd_Even"), desc="PORT connector to CTRL-C2000 (26-way ribbon)"),
    # Würth WE-691322310014.pdf: WR-TBL 322, 3.81 mm, 14 pins, 10 A, 300 V
    "J_CTRL": dict(mfr="Wurth Elektronik", mpn="691322310014", prefix="J", pkg="14p 3.81 mm pluggable header",
                   ds=DS + "connectors/WE-691322310014.pdf", stock=("Connector_Generic", "Conn_01x14"),
                   desc="Control connector to SYS-IO-AUX: 8 DO lines, FB lines each with its own GND return, coil returns"),
    "PE_BOND": dict(mfr="", mpn="", prefix="MH", pkg="plated M4 hole", ds="", sourcing="NOPART",
                    desc="PE bond: plated M4 mounting hole + serrated washer to chassis PE", pins={"left": ["1 PE p"]}),
    "PE_LINK": dict(mfr="", mpn="", prefix="LK", pkg="2 plated M4 holes + removable strap", ds="", sourcing="NOPART",
                    desc="PE_T link to chassis PE: removable strap, OPEN for the hipot test (IC-23)",
                    pins={"left": ["1 PE_T p"], "right": ["2 PE p"]}),
}
# ---- LEAN PORT parts (D-044; sim/port_design.py sec. 13, port_spec 'lean'). Pin numbers of chassis parts are ours.
LEAN_PARTS = {
    # Hongfa-HFE82V-300C.pdf p1: 1000 VDC, 300 A at 85 C, no polarity on load and coil, 24 V coil 6 W, coil-contact
    # 3000 V AC; M6 female load terminals (A1/A2 ours), coil connector C5 (pins 1/2 ours)
    "HFE82V": dict(mfr="Hongfa", mpn="HFE82V-300C/1000-24-H-C5-1", prefix="K", pkg="chassis, M6 female + coil connector",
                   ds=DS + "protection/Hongfa-HFE82V-300C.pdf",
                   desc="CHASSIS-MOUNTED main contactor 300 A 1000 VDC, no polarity, 24 V 6 W coil, no aux contact",
                   pins={"left": ["A1 A1 p", None, "1 COIL1 p"], "right": ["A2 A2 p", None, "2 COIL2 p"]}),
    # Hongfa-HPE501-1000Vdc.pdf p1: aR 250 A 1000 VDC, breaks 5 In - 50 kA, size 000 bolted M6 (pins 1/2 ours)
    "HPE501": dict(mfr="Hongfa", mpn="HPE501/000B100-250", prefix="F", pkg="size 000 square body, M6 bolted",
                   ds=DS + "protection/Hongfa-HPE501-1000Vdc.pdf", stock=("Device", "Fuse"),
                   desc="CHASSIS-MOUNTED aR fuse 250 A 1000 VDC, breaks 1.25-50 kA only (partial range), battery port"),
    # Bourns CSS2H-3920.pdf p1: CSS2H-3920R-L200F 0.2 mOhm 12 W 1 %; two in parallel = 100 uOhm (Kelvin by layout)
    "CSS2H_L200": dict(mfr="Bourns", mpn="CSS2H-3920R-L200F", prefix="RS", pkg="3920 metal strip", stock=("Device", "R"),
                       ds=DS + "sensing/CSS2H-3920.pdf", desc="Current sense resistor 0.2 mOhm 1 % 12 W (two in parallel)"),
    # Chipanalog CA-IS3417WT.pdf table 6-1: NC 1,2,6,7,8; CATHODE 3,5; ANODE 4; Drain2 9,10; Drain1 11,12; 1700 V off,
    # 50 ohm on, IF(ON) 7-10-30 mA, UL 1577 5 kV rms, VIOTM 7070 Vpk
    "CAIS3417": dict(mfr="Chipanalog", mpn="CA-IS3417WT", prefix="U", pkg="SOIC12-WB (WT)",
                     ds=DS + "isolation-interface/CA-IS3417WT.pdf",
                     desc="Isolated 1700 V back-to-back SiC SSR, 50 ohm, opto-compatible input: IMD string switch",
                     pins={"left": ["4 ANODE i", "3 CATHODE1 p", "5 CATHODE2 p", "1 NC1 nc", "2 NC2 nc", "6 NC3 nc"],
                           "right": ["11 Drain1 p", "12 Drain1 p", "9 Drain2 p", "10 Drain2 p", "7 NC4 nc", "8 NC5 nc"]}),
    # Viking-ARHV-A.pdf: ARHV high-voltage chip resistors, 1206 / 1210 (alternates.csv rows for TNPV)
    "ARHV06_1M": dict(mfr="Viking Tech", mpn="ARHV06BTC1004A", prefix="R", pkg="1206", stock=("Device", "R"),
                      ds=DS + "passives-capacitors/Viking-ARHV-A.pdf", desc="HV chip resistor 1.00 M 0.1 % 25 ppm/K"),
    "ARHV13_124K": dict(mfr="Viking Tech", mpn="ARHV13BTC1243A", prefix="R", pkg="1210", stock=("Device", "R"),
                        ds=DS + "passives-capacitors/Viking-ARHV-A.pdf", desc="HV chip resistor 124 k 0.1 % 25 ppm/K"),
    "RPRE_AL": _rpre_al(220.0), "RPRE_AL_200": _rpre_al(200.0),
    # TI OPA2388 SBOS777D 'Pin Functions' (D SOIC-8), as gen/ctrl_c2000.py
    "OPA2388": dict(mfr=TI, mpn="OPA2388IDR", prefix="U", pkg="SOIC-8 (D)", ds=DS + "sensing/OPA4388.pdf",
                    desc="Dual zero-drift RRIO op amp, 10 MHz, 2.5-5.5 V",
                    pins={"left": ["3 +INA i", "2 -INA i", None, "5 +INB i", "6 -INB i"],
                          "right": ["1 OUTA o", None, None, "7 OUTB o", None, "8 V+ pi", "4 V- pi"]}),
    # Nexperia-PMV30ENEA.pdf: SOT-23 1 G, 2 S, 3 D; 40 V, VGS +/-20 V, VGS(th) 1.0-2.5 V, 30 mOhm at 10 V (gdrv key AO3400A)
    "PMV30ENEA": dict(gdrv.PARTS["AO3400A"], desc="N-MOSFET 40 V 4.8 A: coil economiser bypass (pull-in)"),
    "J_LEANIO": dict(mfr="", mpn="", prefix="J", pkg="verification interface", ds="", sourcing="NOPART",
                     stock=("Connector_Generic", "Conn_01x14"), desc="PORT-LEAN verification: supplies and controller lines"),
    "J_LEANHV": dict(mfr="", mpn="", prefix="J", pkg="verification interface", ds="", sourcing="NOPART",
                     stock=("Connector_Generic", "Conn_01x07"), desc="PORT-LEAN verification: port terminals and banks"),
}
PARTS.update(LEAN_PARTS)
# SN74LVC1G17 is gdrv's catalog key (pins SCES351Y p3); boards that do not merge gdrv.PARTS take it from here
CATALOG = dict(PARTS, SN74LVC1G17=gdrv.PARTS["SN74LVC1G17"])


def readback_states(code="PV-PORT"):
    """ID-pin readback states of a port board for CTRL-C2000 (single source of truth = sim/port_design.py ->
    port_spec.json "readback_codes"): code "PV-PORT" or "DAB60-PORT". Returns {"states": [{nA, nB, V_nom, V_lo, V_hi}],
    "gap_counts", "unpowered_V": [lo, hi], "R_eff_ohm", "R_ohm": {r_id, r_a, r_b, ...}, "pullup_ohm", "vbias_V",
    "adc_lsb_V"}; nA / nB = RB1 + RB2 of port A / B (0, 1, 2), bands include V_OH, 0.1 % parts and the hold-coil
    ground lift."""
    return S["readback_codes"][code]


def _check_spec():
    """The schematic's values must be the ones sim/port_design.py justified (DEL-6)."""
    d, h, pt, rb = S["discharge"], S["hold"], S["proof_test"], S["readback"]
    pairs = [(S["contactor"]["mpn"], PARTS["HVC43MC"]["mpn"]), (S["precharge"]["relay_mpn"], PARTS["G7L2AX"]["mpn"]),
             (S["precharge"]["R_ohm"], 220.0), ([d["R_ohm"], d["R_elem_ohm"], d["n_R"]], [600.0, 300.0, 2]),
             (d["switch"], PARTS["C2M1000170D"]["mpn"]), (d["driver"], PARTS["TPSI3050"]["mpn"]),
             (d["thermostat_mpn"], PARTS["TSTAT_DIS"]["mpn"]), (d["C_vddp_F"], 270e-9), (d["R_en_pd_ohm"], 100e3),
             (d["R_gate_ref_ohm"], [1e6, 1e6]), ([d["R_timer_ohm"], d["C_timer_F"]], [4.7e6, 1e-6]),
             ([d["R_latch_ohm"], d["R_tstat_ohm"]], [1e6, 100e3]),
             (S["divider"]["R_top_mpn"], PARTS["TNPV_1M"]["mpn"]), (S["divider"]["R_bot_mpn"], PARTS["TNPW_4K99"]["mpn"]),
             (S["divider"]["C_filter_F"], 4.7e-9),
             (S["current"]["shunt_mpn"], PARTS["CSM2F8518"]["mpn"]), (S["current"]["amp"], PARTS["AMC3302"]["mpn"]),
             (S["variants"]["135"]["fuse_mpn"], PARTS["HP10NH1GPV160"]["mpn"]),
             (S["variants"]["180"]["fuse_mpn"], PARTS["HP10NH2GPV250"]["mpn"]), (S["spd"]["mov_mpn"], PARTS["MOV_TVT25"]["mpn"]),
             (h["comparator"], PARTS["TLV3502"]["mpn"]), (h["vref"], PARTS["REF3030E"]["mpn"]),
             (h["or"], PARTS["LVC1G32"]["mpn"]), (h["and3"], PARTS["LVC1G11"]["mpn"]),
             (h["switch"], PARTS["IRFR9214"]["mpn"]), (S["coil"]["low_side"], PARTS["IRFL214"]["mpn"]),
             (S["coil"]["R_gate_div_ohm"], [10e3, 22e3]), (S["current"]["ntc_mpn"], PARTS["NTC_SH"]["mpn"]),
             ([h["channels"], h["R1_ohm"], h["R2_ohm"], h["Rt_ohm"], h["R3_ohm"], h["R4_ohm"]],
              [2, 49.9e3, 118e3, 86.6e3, 88.7e3, 113e3]),
             (h["R_fb_div_ohm"], [100e3, 25.5e3]), (h["R_diag_pullup_ohm"], 47e3),
             ([pt["R_tst_div_ohm"], pt["R_os_ohm"], pt["C_os_F"], pt["C_dly_F"], pt["R_os_in_ohm"]],
              [[100e3, 15e3], 47e3, 3.3e-6, 22e-6, 10e3]),
             ([rb["R_rb_A_ohm"], rb["R_rb_B_ohm"], rb["R_id_ohm"], rb["R_hc_div_ohm"]], [118e3, 42.2e3, 28.0e3, [100e3, 15e3]]),
             ([S["sources"]["battery"][r]["fuse_mpn"] for r in ("135", "180")], [PARTS["ETI_GBAT200"]["mpn"]] * 2),
             (S["spd"]["fuse_mpn"], PARTS["FUSE_SPD"]["mpn"]), (S["spd"]["clip_mpn"], PARTS["CLIP14"]["mpn"]),
             (S["spd"]["monitor_opto"], PARTS["CNY65B"]["mpn"]),
             (S["x_cap"]["mpn"], PARTS["XCAP_2U2"]["sourcing"]), (S["y_cap"]["mpn"], PARTS["VY1_4N7"]["mpn"]),
             (S["imd"]["R_elem_mpn"], PARTS["TNPV_124K"]["mpn"]), (S["sensor_supply"]["regulator"], PARTS["LMR36015"]["mpn"]),
             (d["bias_string"], [5, 2e6]), (d["bleeder_string"], [10, 47e3]), (d["R_pxfr_ohm"], 7.32e3),
             (S["imd"]["n_string"], 8), (S["imd"]["R_elem_ohm"], 124e3), (S["control"]["R_fb_pullup_ohm"], 1.5e3),
             (S["control"]["R_do_pulldown_ohm"], 47e3), (S["control"]["en_or_diode"], PARTS["BAS16"]["mpn"]),
             (S["sensor_supply"]["R_fbb_ohm"], 43.2e3), (S["current"]["R_filter_ohm"], 10.0)]
    bad = [p for p in pairs if p[0] != p[1]]
    assert not bad, "port_spec.json and gen/port.py disagree: %s" % bad


def _string(B, key, n, top, bottom, node, dom, hv):
    """n equal catalog resistors in series top -> bottom; internal nodes '<node>1..' join domain hv."""
    nets = [top] + ["%s%d" % (node, i) for i in range(1, n)] + [bottom]
    refs = []
    for a_, b_ in zip(nets, nets[1:]):
        refs.append(B.part(key, {"1": a_, "2": b_}).ref)
    for x in nets[1:-1]:
        dom[x] = hv
    return refs


def _low_side(B, gate_src, drain, gnd, g):
    """IRFL214 coil low-side switch, gate from gate_src (24 V level) through 10 k / 22 k (15-18 V)."""
    B.R("10k", gate_src, g)
    B.R("22k", g, gnd, note="gate-GND: off when the gate source is off")
    B.part("IRFL214", {"1": g, "2": drain, "3": gnd})


def _amc(B, key, inp, inn, hgnd, outp, outn, vdd, agnd, dom, hv, base, diag=None):
    """AMC3330 / AMC3302 with the decoupling of SBASA34B Fig 7-5 (SBASA11B Fig 8-4): VDD 1 n + 1 u, DCDC_IN 100 n,
    DCDC_OUT 1 u + 1 n (+ 100 n on HLDO_IN), HLDO_OUT 100 n C0G + 1 n. Returns the reference."""
    dco, hldo, ldo = base + "_DCO", base + "_HLDO", base + "_LDO"
    dom[dco] = dom[hldo] = hv
    ref = B.part(key, {"6": inp, "7": inn, "1": dco, "3": dco, "5": hldo, "4": None, "2": hgnd, "8": hgnd,
                       "11": outp, "10": outn, "14": diag, "12": vdd, "13": ldo, "16": ldo, "15": agnd, "9": agnd}).ref
    if diag:
        B.R("47k", diag, vdd, note="DIAG pull-up (SBASA11B Fig 7-5)")
    B.C("1n", vdd, agnd)
    B.C("1u", vdd, agnd, pkg="0805", volt="25V")
    B.C("100n", ldo, agnd)
    B.C("1u", dco, hgnd, pkg="0805", volt="25V")
    B.C("1n", dco, hgnd)
    B.C("100n", dco, hgnd)
    B.C("100n", hldo, hgnd, pkg="1206", diel="C0G")
    B.C("1n", hldo, hgnd)
    return ref


def _divider(B, top, ref_net, tap, dom, hv, label):
    """6 x 1.00 M + 4.99 k || 4.7 nF C0G (port_spec 'divider': lag <= 30 us). Returns the refs of the string."""
    d = S["divider"]
    assert d["n_top"] == 6 and d["R_top_elem_ohm"] == 1e6 and d["R_bot_ohm"] == 4.99e3, "port_spec divider changed"
    refs = _string(B, "TNPV_1M", d["n_top"], top, tap, label + "_D", dom, hv)
    B.part("TNPW_4K99", {"1": tap, "2": ref_net})
    B.C(farad(d["C_filter_F"]), tap, ref_net, diel="C0G", tol="5%")
    dom[tap] = hv
    return refs


def _sw_driver(B, en, vssp, vsss, drv, dom, dom_sec, base):
    """TPSI3050-Q1 in two-wire mode (EN = 24 V line, SLVSFJ7D sec 8.3.6): R_PXFR 7.32 k (1.9 mA), C_VDDP 270 nF C0G
    (220-330 nF absolute, PA-10), C_DIV1 = C_DIV2 = 100 nF. Secondary rails join dom_sec. Returns the reference."""
    px, vddp, vddh, vddm = base + "_PX", base + "_VDDP", base + "_VDDH", base + "_VDDM"
    dom[vddh] = dom[vddm] = dom[drv] = dom_sec
    ref = B.part("TPSI3050", {"1": en, "2": px, "3": vddp, "4": vssp, "8": drv, "7": vddh, "6": vddm, "5": vsss}).ref
    B.R(ohm(S["discharge"]["R_pxfr_ohm"]), px, vssp, note="R_PXFR: two-wire EN current 1.9 mA (SLVSFJ7D table 8-2)")
    B.C(farad(S["discharge"]["C_vddp_F"]), vddp, vssp, pkg="1210", volt="25V", diel="C0G", tol="5%")
    B.C("100n", vddh, vddm, volt="25V")
    B.C("100n", vddm, vsss, volt="25V")
    return ref


def _schmitt(B, inp, out, vdd, agnd):
    B.part("SN74LVC1G17", {"2": inp, "4": out, "5": vdd, "3": agnd, "1": None})
    B.C("100n", vdd, agnd)


def varistor_y(B, t, pos, neg, pe_t, vdd, agnd, hv, dom, iso, xing):
    """Monitored varistor Y at a port's terminals (rev F0, port_spec 'spd'; reused unchanged by the lean port):
    branch fuses in PCB clips, 3 x TVT25751 (pos/M, neg/M, M/pe_t), monitor loop through both fuses and all three
    thermal links into a CNY65B. Adds to dom/iso/xing; returns the monitor output net <t>_SPD_N (high = network open,
    220 k pull-up to vdd). MOV3 is the declared HV-PE crossing; the CNY65B is an isolator only where vdd/agnd sit in
    another domain (PV-PORT), on the lean board it is functional."""
    n = {"inp": pos, "inn": neg}
    sg = S["spd"]
    B.block("On-board surge protection: varistor Y (IC-01, IC-12)",
            "TVT25751 x 3 (615 VDC, thermal link + monitor lead): MOV1 +/M, MOV2 -/M, MOV3 M/PE_T. Up,eff %.0f V pole-PE\n"
            "and pole-pole at In %.0f kA (calc., <= 4.0 kV -> reinforced 6 kV / basic 4 kV, D-032); Imax %.0f kA. Branch\n"
            "fuses ETI CH14x51 gPV 36 A 30 kA (L/R 2 ms). Monitor loop through both fuses and all three links -> CNY65B\n"
            "-> RB1 (sum on the ID line): RB1 = 1 at rest above 200 V = varistor network open -> CTRL inhibits/stops."
            % (sg["Up_eff_pe_V"], sg["In_A"] / 1e3, sg["Imax_A"] / 1e3))
    sp_, sn_, m_ = t + "_SP+", t + "_SP-", t + "_SPM"
    mon = [t + "_MON%d" % k for k in (1, 2, 3)]
    for x in (sp_, sn_, m_, *mon, t + "_MONA"):
        dom[x] = hv
    for a_, b_ in ((n["inp"], sp_), (n["inn"], sn_)):
        B.part("FUSE_SPD", {"1": a_, "2": b_}, value="36A gPV 14x51")
        B.part("CLIP14", {"1": a_})
        B.part("CLIP14", {"1": b_})
    B.part("MOV_TVT25", {"1": sp_, "2": m_, "3": mon[0]})
    B.part("MOV_TVT25", {"1": sn_, "2": m_, "3": mon[1]})
    xing.add(B.part("MOV_TVT25", {"1": m_, "2": pe_t, "3": mon[2]}).ref)
    ra = [mon[0]] + ["%s_MR%d" % (t, k) for k in range(1, sg["n_mon"] // 2)] + [t + "_MONA"]
    rb_ = [m_] + ["%s_MS%d" % (t, k) for k in range(1, sg["n_mon"] // 2)] + [mon[1]]
    for chain in (ra, rb_):
        for x in chain[1:-1]:
            dom[x] = hv
        for a_, b_ in zip(chain, chain[1:]):
            B.R(ohm(sg["R_mon_ohm"]), a_, b_, pkg="2512", note="monitor loop, <= 300 V per element in an In surge")
    spdn = t + "_SPD_N"
    iso.add(B.part("CNY65B", {"1": t + "_MONA", "2": mon[2], "4": spdn, "3": agnd}).ref)
    B.part("BAS16", {"1": mon[2], "3": t + "_MONA", "2": None})       # reverse-surge path around the LED
    B.R("220k", vdd, spdn, note="SPD_N high = varistor network open")
    return spdn


def port(B, tag, bus_pos, bus_neg, sheet_no, hv="HV", vdd="+3V3S", agnd="AGND", gnd="GND", pe="PE", v24="+24V",
         rating=135, source="pv", aux_tap=True, pe_t=None):
    """Add ONE complete DC port on two new sheets (sheet_no: power path, sheet_no + 1: sensing + hold logic).
    rating = 135 (PV-P75, default) or 180 (PV-P100/110, 4 x 45 A): selects fuse, CM-choke and terminal ratings and the
    OC/SC trip notes (sim/port_design.py sec. 12). source = "pv" (default, PV array: gPV fuse, DEHN 952515, one HVC43)
    or "battery" (battery rack / stiff DC bus: ETI gBat fuse, Raycap SPD with its own backup fuses at the terminals, a
    PAIR of HVC43 in parallel, FB = both closed; report sec. 6.7). aux_tap=False leaves out the terminal-side stud on
    <tag>_X+ (AUX-HV tap). The hold logic uses agnd and gnd: the board ties them.
    Returns {"isolators": set, "crossings": set, "domains": {net: domain}, "nets": {role: net}}; nets["rb1"],
    nets["rb2"] are the proof-test readback outputs (3.3 V CMOS) the board must carry to CTRL."""
    _check_spec()
    assert rating in (135, 180), "port rating must be 135 or 180 A"
    assert source in ("pv", "battery"), "port source must be pv or battery"
    V, src = S["variants"][str(rating)], S["sources"][source][str(rating)]
    fuse, cmc, term = {135: ("HP10NH1GPV160", "CMC160", "TERM200"), 180: ("HP10NH2GPV250", "CMC200", "TERM250")}[rating]
    batt = source == "battery"
    if batt:
        fuse = "ETI_GBAT200"
        assert PARTS[fuse]["mpn"] == src["fuse_mpn"] and src["n_contactors"] == 2, "port_spec sources/battery changed"
    t = tag
    n = dict(inp=t + "_IN+", inn=t + "_IN-", fp=t + "_F+", fn=t + "_F-", xp=t + "_X+", xn=t + "_X-",
             bus_pos=bus_pos, bus_neg=bus_neg, pre_m=t + "_PRE_M", pre_r=t + "_PRE_R", sh_p=t + "_SH_P",
             sh_n=t + "_SH_N", dis_m=t + "_DIS_M", dis_d=t + "_DIS_D", dis_g=t + "_DIS_G", dis_off=t + "_DIS_OFF",
             dis_ref=t + "_DIS_REF", dis_t=t + "_DIS_T", dis_te=t + "_DIS_TE", dis_sb=t + "_DIS_SB",
             dis_sg=t + "_DIS_SG", dis_th=t + "_DIS_TH")
    do_pre, do_main, fb, do_dis, do_test = "K_%s_PRE" % t, "K_%s_MAIN" % t, "FB_%s_MAIN" % t, "K_DISCH", "DO_SPARE"
    for x in (do_pre, do_main, do_dis, do_test):
        assert x in IF.DO, x
    assert fb in IF.DI, fb
    dom = {v: hv for v in n.values()}
    iso, xing = set(), set()
    hc, hc1, hc2, ts = t + "_HC_", t + "_HC1_", t + "_HC2_", t + "_TST_"
    n.update(rb1=t + "_RB1", rb2=hc2 + "OC", test=do_test)
    d, pt = S["discharge"], S["proof_test"]

    B.new_sheet("%02d_port%s_power" % (sheet_no, t), "Port %s power path" % t,
                "Terminals, fuses, SPD, EMI filter, contactor, precharge relay,\n"
                "coil drives, hold stage, shunt, discharge + time limit, bleeder")
    sg, link = S["spd"], pe_t
    pe_t = pe_t or pe
    B.block("Terminals, fuses (%s)" % ("battery-type port" if batt else "PV-type port"),
            "CHASSIS-MOUNTED. %d A port: %s %.0f A 1000 V DC in each pole, derated %.0f A (fuses in the INLET air path,\n"
            "PD3: terminals / fuse bases only, creepage to PE >= 16.0 mm IIIa, IC-18/IC-21).%s"
            % (rating, "ETI gBat (30 kA at L/R 1 ms)" if batt else "gPV", (src if batt else V)["fuse_In_A"],
               (src if batt else V)["fuse_derated_A"], "" if not batt or rating == 135 else
               "\nFIRMWARE CURRENT LIMIT (D-020): %s A at inlet %s C (port_spec port_current_limit_vs_inlet_C)."
               % ("/".join("%.0f" % x for x in S["port_current_limit_vs_inlet_C"]["battery/180"]),
                  "/".join("%.0f" % x for x in S["port_current_limit_vs_inlet_C"]["inlet_C"]))))
    B.part(term, {"1": n["inp"]}, value="DC terminal + (CUSTOM)")
    B.part(term, {"1": n["inn"]}, value="DC terminal - (CUSTOM)")
    B.part(fuse, {"1": n["inp"], "2": n["fp"]})
    B.part(fuse, {"1": n["inn"], "2": n["fn"]})

    spdn = varistor_y(B, t, n["inp"], n["inn"], pe_t, vdd, agnd, hv, dom, iso, xing)
    if link:     # PV-PORT: own removable PE return; pe_t=None (DAB60 default) puts MOV3 and the Y capacitors on pe
        B.block("PE link (IC-23, IMD lesson 4)", "MOV3, Y capacitors and the IMD test strings return on %s: a removable\n"
                                                "M4 strap to chassis PE, opened for the hipot. Open strap = no IMD swing." % pe_t)
        B.part("PE_LINK", {"1": pe_t, "2": pe})
        dom[pe_t] = "PE"

    B.block("EMI filter", "X 2 x 2.2 uF film (RFQ: impulse >= 4.5 kV, PA-07), CM choke (CUSTOM), Y1 4.7 nF pole-PE\n"
                          "(1500 VDC rated). Y capacitors are declared HV-PE crossings.")
    B.part("XCAP_2U2", {"1": n["fp"], "2": n["fn"]}, value="X 2.2u 1300V RFQ")
    B.part(cmc, {"1": n["fp"], "2": n["xp"], "3": n["fn"], "4": n["xn"]}, value="CM choke %d A (CUSTOM)" % V["cmc_rating_A"])
    B.part("XCAP_2U2", {"1": n["xp"], "2": n["xn"]}, value="X 2.2u 1300V RFQ")
    xing.add(B.part("VY1_4N7", {"1": n["xp"], "2": pe_t}).ref)
    xing.add(B.part("VY1_4N7", {"1": n["xn"], "2": pe_t}).ref)

    B.block("Main contactor: coil drive + feedback",
            "HVC43-250A-24MC, + pole, bidirectional (TDK p2). Opens at zero current in normal operation; cut-off\n"
            "450 A @ 1000 V once. K_%s_MAIN feeds coil+ (S2M) AND gates the IRFL214 low side: two switches in the\n"
            "path. S10K30 directly across the coil = the only flyback path (>= 54 V down to drop-out; TDK p7 >= 50 V),\n"
            "V_DS <= %.0f V. FB: NC mirror contact inverted by %.1f k: FB_%s_MAIN high = CLOSED (also feeds the TPSI EN OR).\n"
            "IC-05: coil + mirror contact to main >= 4400 V AC, Uimp 8 kV (HVC43MC p3): reinforced with the 6 kV credit\n"
            "(6.6 kV at 4000 m); insulation type not stated (TDK to confirm); coil/FB wiring >= 10 mm from the studs.%s"
            % (t, S["coil"]["V_ds_max_V"], S["control"]["R_fb_pullup_ohm"] / 1e3, t,
               "\nPV-class port: no stiff source allowed (battery / DC bus = source battery)." if not batt else
               "\nBattery-type port: TWO contactors in parallel (report 6.7), coordinated with the 200 A gBat."))
    coil_p, coil_n = t + "_KM_COIL", t + "_KM_LO"
    nk = 2 if batt else 1
    fbs = [fb] if nk == 1 else [t + "_FB1", t + "_FB2"]           # one mirror node per contactor
    fb_any = fb if nk == 1 else t + "_FB_ANY"                      # 'any contactor closed': hold logic + TPSI EN
    for k in range(nk):
        iso.add(B.part("HVC43MC", {"A1": n["xp"], "A2": bus_pos, "2": coil_p, "3": coil_n, "1": gnd, "4": fbs[k]}).ref)
        B.part("S10K30", {"1": coil_p, "2": coil_n})
        B.R(ohm(S["control"]["R_fb_pullup_ohm"]), v24, fbs[k], pkg="2512",
            note="1 W: aux wetting 14-18 mA (>= 10 mA, TDK p3) and FB inversion")
    B.part("S2M", {"2": do_main, "1": coil_p})                 # K_x_MAIN feed; blocks the hold current from the DO line
    for k in range(nk):
        _low_side(B, do_main, coil_n, gnd, t + "_KM_G1" + "AB"[k] * (nk > 1))
    if nk == 2:
        B.block("Contactor pair feedback (battery-type port)",
                "FB_%s_MAIN (SYS DI) = BOTH contactors closed: own 1.5 k pull-up, a BAS16 to each mirror node, so a\n"
                "contactor that failed to close is seen at once (the single-contactor state is not coordinated). The hold\n"
                "logic and the TPSI3050 EN use %s = ANY closed (BAS16 diode-OR): a single welded contactor still inhibits\n"
                "the discharge and arms the hold; firmware finds a single weld by VA vs VAX. K_%s_MAIN: %.2f A (2 coils)."
                % (t, fb_any, t, src["DO_current_A"]))
        B.R(ohm(S["control"]["R_fb_pullup_ohm"]), v24, fb, pkg="2512", note="1 W: DI pull-up (both closed)")
        for k in range(2):
            B.part("BAS16", {"1": fb, "3": fbs[k], "2": None})
            B.part("BAS16", {"1": fbs[k], "3": fb_any, "2": None})
        B.R("100k", fb_any, gnd, note="defined low when both contactors are open")

    B.block("Hold-closed power stage (2 independent channels)",
            "Channel 1: HOLD1 -> BSS138 -> IRFR9214 high side from the port +24V (not cut by E-stop) -> S2M -> coil+.\n"
            "Channel 2: IRFL214 low side coil- -> GND, gate fed from the channel-1 output (10 k / 22 k) and clamped\n"
            "off by a BSS138 unless HOLD2: it can only conduct while channel 1 conducts, so no flyback ever finds the\n"
            "SYS DO clamp. No single shorted switch energises the coil (report sec. 7.1 fault table).")
    B.part("BSS138BK", {"1": hc1 + "HOLD", "3": hc + "PD", "2": gnd})
    B.R("10k", hc + "PD", hc + "PG", pkg="0805")
    B.R("10k", v24, hc + "PG", pkg="0805", note="gate-source: off by default, VGS 13 V")
    B.part("IRFR9214", {"1": hc + "PG", "3": v24, "2": hc + "OUT"})
    B.part("S2M", {"2": hc + "OUT", "1": coil_p})
    B.R("10k", hc + "OUT", hc + "G2", pkg="0805")
    B.R("22k", hc + "G2", gnd, note="gate-GND")
    for _ in range(nk):
        B.part("IRFL214", {"1": hc + "G2", "2": coil_n, "3": gnd})
    B.part("BSS138BK", {"1": hc + "X", "3": hc + "G2", "2": gnd})          # clamp: Q_LS2 off unless HOLD2
    B.R("47k", hc + "OUT", hc + "X")
    B.R("47k", hc + "X", gnd)
    B.part("BSS138BK", {"1": hc2 + "HOLD", "3": hc + "X", "2": gnd})        # HOLD2 releases the clamp

    B.block("Precharge relay + resistor",
            "G7L-2A-X, poles in series, normal polarity = current 8 -> 2 (terminal -> bus). K_%s_PRE feeds the coil\n"
            "and gates its IRFL214 low side; S2M + SMBJ33A across the coil (37-42 V, Omron p5). R_pre %.0f ohm Miba\n"
            "RST 200 (RFQ, pulse-rated). Complete at |dV| <= %.0f V after the VA/VAX null; abort below %.1f x Vterm x\n"
            "(1-exp(-t/%.2f s)) or at %.2f s; max %d attempts, then %d s lock-out (sim/port_design.py)."
            % (t, S["precharge"]["R_ohm"], S["precharge"]["dV_ok_V"], S["precharge"]["k_check"],
               S["precharge"]["tau_max_s"], S["precharge"]["t_out_s"], S["precharge"]["max_attempts"],
               S["precharge"]["lockout_s"]))
    coil_r = t + "_KP_LO"
    iso.add(B.part("G7L2AX", {"8": n["xp"], "6": n["pre_m"], "4": n["pre_m"], "2": n["pre_r"], "1": do_pre,
                              "0": coil_r}).ref)
    B.part("SMBJ33A", {"1": coil_r, "2": t + "_KP_Z"})
    B.part("S2M", {"2": t + "_KP_Z", "1": do_pre})
    _low_side(B, do_pre, coil_r, gnd, t + "_KP_G")
    B.part("RST200_PRE", {"1": n["pre_r"], "2": bus_pos}, value="220R RST200 RFQ")

    B.block("Shunt (- pole) + shunt NTC", "CSM2F-8518 100 uOhm, busbar-mounted. Kelvin test points to the IA/IB amplifier.\n"
                                          "Positive current = power into the module at this port. NTC on the shunt\n"
                                          "(reinforced pad) -> SYS-IO-AUX NTC input: firmware TCR compensation.")
    B.part("CSM2F8518", {"1": n["xn"], "2": bus_neg, "3": n["sh_n"], "4": n["sh_p"]})
    B.part("NTC_SH", {"1": "NTC_%s_1" % t, "2": "NTC_%s_2" % t})
    B.part("J_NTC", {"1": "NTC_%s_1" % t, "2": "NTC_%s_2" % t})

    B.block("Fail-safe active discharge, inhibited while the contactor is closed",
            "TPSI3050 EN = K_DISCH OR FB_%s_MAIN (2 x BAS16): powered = BSS138 holds the SiC gate low. Unpowered\n"
            "= the bus biases the gate (10 M, 15 V zener) -> 2 x 300 ohm Miba RST 200 discharge the bus. FB is high\n"
            "while the contactor is closed (or welded), so an E-stop or a lost K_DISCH cannot discharge a live port;\n"
            "FB keeps a running TPSI on, SYS energises K_DISCH first at power-up (27 mA start-up)." % t)
    B.part("RST200_DIS", {"1": bus_pos, "2": n["dis_m"]}, value="300R RST200 RFQ")
    B.part("RST200_DIS", {"1": n["dis_m"], "2": n["dis_d"]}, value="300R RST200 RFQ")
    B.part("C2M1000170D", {"1": n["dis_g"], "2": n["dis_d"], "3": bus_neg})
    _string(B, "TNPV_2M", d["bias_string"][0], bus_pos, n["dis_g"], t + "_DIS_B", dom, hv)
    B.part("BZX84C15", {"3": n["dis_g"], "1": bus_neg, "2": None})
    B.part("BSS138BK", {"1": n["dis_off"], "3": n["dis_g"], "2": bus_neg})
    B.R("100k", n["dis_off"], bus_neg, note="gate pull-down: off when the driver is unpowered")
    en = t + "_DIS_EN"
    B.part("BAS16", {"1": do_dis, "3": en, "2": None})
    B.part("BAS16", {"1": fb_any, "3": en, "2": None})
    B.R(ohm(d["R_en_pd_ohm"]), en, gnd, note="EN pull-down behind the diode-OR")
    iso.add(_sw_driver(B, en, gnd, bus_neg, n["dis_off"], dom, hv, t + "_DIS"))
    B.R("47k", do_dis, gnd, note="DO line pull-down")
    B.flag(bus_neg)

    B.block("Discharge time limit + thermal cut-off (HV side, needs no 24 V)",
            "DIS_G charges 1 uF through 4.7 M; a MMBT3906 compares it with DIS_G/2 (1 M / 1 M, also the gate pull-\n"
            "down) and fires the MMBT3906/MMBT3904 latch: gate clamped to 0.75 V after %.1f-%.1f s (needed <= 1.8 s),\n"
            "held by the bias current until K_DISCH returns (BSS138 resets it). Thermostat KSD301-class (closes 80 C) on an\n"
            "RST 200 housing fires the same latch; it is at HV - basic-insulating pad to the housing (PE)."
            % tuple(d["t_limit_s"]))
    B.R(ohm(d["R_gate_ref_ohm"][0]), n["dis_g"], n["dis_ref"])
    B.R(ohm(d["R_gate_ref_ohm"][1]), n["dis_ref"], bus_neg)
    B.R(ohm(d["R_timer_ohm"]), n["dis_g"], n["dis_t"])
    B.C(farad(d["C_timer_F"]), n["dis_t"], bus_neg, pkg="1206", volt="50V", tol="10%")
    B.part("BAS16", {"1": n["dis_t"], "3": n["dis_te"], "2": None})
    B.part("MMBT3906", {"2": n["dis_te"], "1": n["dis_ref"], "3": n["dis_sg"]})       # timer comparator
    B.part("MMBT3906", {"2": n["dis_g"], "1": n["dis_sb"], "3": n["dis_sg"]})         # latch PNP
    B.part("MMBT3904", {"3": n["dis_sb"], "1": n["dis_sg"], "2": bus_neg})            # latch NPN
    B.R(ohm(d["R_latch_ohm"]), n["dis_g"], n["dis_sb"])
    B.R(ohm(d["R_latch_ohm"]), n["dis_sg"], bus_neg)
    B.C("1n", n["dis_sg"], bus_neg, diel="C0G")
    B.part("TSTAT_DIS", {"1": n["dis_g"], "2": n["dis_th"]}, value="KSD301 NO 80C RFQ")
    B.R(ohm(d["R_tstat_ohm"]), n["dis_th"], n["dis_sg"])

    B.block("Passive bleeder + board connection points",
            "%d x 47 k 2512 1 W (100 V each at 1000 V): back-up discharge, 2.4 W at 1000 V.\n"
            "REDCUBE studs: bus +/- to the power cells, terminal-side tap." % d["bleeder_string"][0])
    nb, rb = d["bleeder_string"]
    nets = [bus_pos] + ["%s_BL%d" % (t, i) for i in range(1, nb)] + [bus_neg]
    for a_, b_ in zip(nets, nets[1:]):
        B.R("47k", a_, b_, pkg="2512", note="1 W thick film, >= 500 V working")
    for x in nets[1:-1]:
        dom[x] = hv
    assert rb == 47e3
    B.part("STUD", {"1": bus_pos})
    B.part("STUD", {"1": bus_neg})
    if aux_tap:
        B.part("STUD", {"1": n["xp"]})

    B.new_sheet("%02d_port%s_sense" % (sheet_no + 1, t), "Port %s sensing and hold logic" % t,
                "V%s bus side, V%sX terminal side (AMC3330), I%s current (AMC3302),\n"
                "two-channel hold logic, proof-test injection, readback RB1/RB2" % (t, t, t))
    for lab, top in (("V" + t, bus_pos), ("V%sX" % t, n["xp"])):
        side = "bus" if top == bus_pos else "terminal"
        B.block("%s - %s-side voltage" % (lab, side),
                "6 x TNPV1206 1.00 M (167 V each at 1000 V) + 4.99 k: 1 V = 1203 V, clip 1504 V. 4.7 nF: lag\n"
                "<= %.0f us incl. AMC3330. Out: %s_P/N (+/-2 V on 1.44 V, PORT contract). HGND = %s."
                % (S["divider"]["lag_s"] * 1e6, lab, bus_neg))
        _divider(B, top, bus_neg, lab + "_IN", dom, hv, lab)
        iso.add(_amc(B, "AMC3330", lab + "_IN", bus_neg, bus_neg, lab + "_P", lab + "_N", vdd, agnd, dom, hv, lab))
    lab = "I" + t
    B.block("%s - port current" % lab,
            "Linear +/-500 A, clip +/-640 A (OC trip %.0f A, SC trip %.0f A inside). 10 ohm + 10 nF input\n"
            "filter (SBASA11B Fig 8-2). HGND on the shunt's X- bolt by its own trace. Fail-safe out = -2.57 V."
            % (V["OC_trip_A"], V["SC_trip_A"]))
    ip, inn_ = lab + "_INP", lab + "_INN"
    dom[ip] = dom[inn_] = hv
    B.R("10R", n["sh_p"], ip)
    B.R("10R", n["sh_n"], inn_)
    B.C("10n", ip, inn_, diel="C0G")
    diag = lab + "_DIAG"
    iso.add(_amc(B, "AMC3302", ip, inn_, n["xn"], lab + "_P", lab + "_N", vdd, agnd, dom, hv, lab, diag=diag))
    B.flag(n["xn"])

    H = S["hold"]
    ip_, in_ = "I%s_P" % t, "I%s_N" % t
    inj = {1: (ts + "T", ts + "TD"), 2: (ts + "T2A", ts + "T2B")}
    for ch, h in ((1, hc1), (2, hc2)):
        B.block("Hold-closed logic, channel %d (%s)" % (ch, "IRFR9214 high side" if ch == 1 else "IRFL214 low side"),
                "|I%s| > %.0f A (band %.0f-%.0f A, own REF3030E + 0.1 %% network, CM-cancelling) AND FB = closed AND\n"
                "DIAG = valid -> HOLD%d. Test injection 86.6 k from %s (side A) / %s (side B): shifts the threshold\n"
                "by %.0f A, so a healthy side trips at 0 A under TEST only." %
                (t, H["I_trip_nom_A"], H["I_trip_band_A"][0], H["I_trip_band_A"][1], ch, inj[ch][0], inj[ch][1],
                 pt["shift_A"]))
        B.part("REF3030E", {"1": vdd, "2": h + "REF", "3": agnd})
        B.C("100n", vdd, agnd)
        B.C("100n", h + "REF", agnd)
        for side, pos_src, neg_src, vt in (("A", ip_, in_, inj[ch][0]), ("B", in_, ip_, inj[ch][1])):
            B.R(ohm(H["R1_ohm"]), pos_src, h + "P" + side, tol="0.1%")
            B.R(ohm(H["R2_ohm"]), h + "P" + side, agnd, tol="0.1%")
            B.R(ohm(H["Rt_ohm"]), vt, h + "P" + side, tol="0.1%", note="proof-test injection")
            B.R(ohm(H["R1_ohm"]), neg_src, h + "N" + side, tol="0.1%")
            B.R(ohm(H["R3_ohm"]), h + "N" + side, h + "REF", tol="0.1%")
            B.R(ohm(H["R4_ohm"]), h + "N" + side, agnd, tol="0.1%")
        B.part("TLV3502", {"1": h + "PA", "2": h + "NA", "3": h + "PB", "4": h + "NB", "8": vdd, "7": h + "OCP",
                           "6": h + "OCN", "5": agnd})
        B.C("100n", vdd, agnd)
        B.part("LVC1G32", {"1": h + "OCP", "2": h + "OCN", "5": vdd, "4": h + "OC", "3": agnd})
        B.C("100n", vdd, agnd)
        B.R(ohm(H["R_fb_div_ohm"][0]), fb_any, h + "FB", note="FB 24 V -> logic, max 5.4 V (5.5 V tolerant)")
        B.R(ohm(H["R_fb_div_ohm"][1]), h + "FB", agnd)
        B.part("LVC1G11", {"1": h + "OC", "3": h + "FB", "6": diag, "5": vdd, "4": h + "HOLD", "2": agnd})
        B.C("100n", vdd, agnd)
        B.R("100k", h + "HOLD", agnd, note="hold off while the logic is unpowered")

    B.block("Proof-test injection (DO_SPARE = TEST)",
            "100 k / 15 k -> Schmitt buffer T (ch1 side A). T -> 47 k / 22 uF -> T_d (ch1 side B). One-shots\n"
            "(3.3 uF / 47 k) of T -> T2A (ch2 side A) and of T_d -> T2B (ch2 side B). No single fault trips both\n"
            "channels for longer than one one-shot. Firmware runs it at every start, contactor open and closed\n"
            "(report sec. 7.4: windows, what it finds and what it does not).")
    B.R(ohm(pt["R_tst_div_ohm"][0]), do_test, ts + "IN")
    B.R(ohm(pt["R_tst_div_ohm"][1]), ts + "IN", agnd)
    _schmitt(B, ts + "IN", ts + "T", vdd, agnd)
    B.R(ohm(pt["R_os_ohm"]), ts + "T", ts + "DRC")
    B.C(farad(pt["C_dly_F"]), ts + "DRC", agnd, pkg="1206", volt="25V", tol="10%")
    _schmitt(B, ts + "DRC", ts + "TD", vdd, agnd)
    for src, x, out in ((ts + "T", ts + "XA", ts + "T2A"), (ts + "TD", ts + "XB", ts + "T2B")):
        B.C(farad(pt["C_os_F"]), src, x, pkg="0805", volt="25V", tol="10%")
        B.R(ohm(pt["R_os_ohm"]), x, agnd)
        B.R(ohm(pt["R_os_in_ohm"]), x, x + "I", note="limits the input clamp current on the falling edge")
        _schmitt(B, x + "I", out, vdd, agnd)

    B.block("Readback RB1 / RB2 (to CTRL on a spare line)",
            "RB1 = OC1 OR hold high-side output present (100 k / 15 k): at rest RB1 = 1 means a stuck channel 1.\n"
            "RB2 = OC2. PV-PORT sums them onto the ID line (board_io); firmware reads RB1 + RB2 against the\n"
            "measured current in operation and through the proof-test windows at start.")
    rbd = S["readback"]["R_hc_div_ohm"]
    B.R(ohm(rbd[0]), hc + "OUT", hc + "OUTL")
    B.R(ohm(rbd[1]), hc + "OUTL", agnd)
    B.part("LVC1G32", {"1": hc1 + "OC", "2": hc + "OUTL", "5": vdd, "4": t + "_RB1A", "3": agnd})
    B.C("100n", vdd, agnd)
    B.part("LVC1G32", {"1": t + "_RB1A", "2": spdn, "5": vdd, "4": n["rb1"], "3": agnd})   # OR varistor-network open
    B.C("100n", vdd, agnd)
    B.TP(n["rb1"])
    B.TP(n["rb2"])
    return {"isolators": iso, "crossings": xing, "domains": dom, "nets": n}


def imd(B, sheet_no, pole_pos, pole_neg, hv="HV", vdd="+3V3S", agnd="AGND", gnd="GND", pe="PE", pe_t="PE_T",
        pe_d="PE_D"):
    """Insulation-resistance measurement (PV-C5): AUX1 = V(PE - pole_neg), AUX2 = V(PE - pole_pos), switched test
    strings pole_pos -> PE_T (IMD_SW_P) and pole_neg -> PE_T (IMD_SW_N). IC-11: with a switch open the source/gate
    island follows a pole, so the island and the TPSI3050 secondary are HV; the PE-side SiC of each pair is the HV-PE
    crossing. IMD lessons (REFERENCE-LESSONS 1, 4): dividers on PE_D (own bond), strings on PE_T (port() links it to PE
    with a removable strap) - an open PE connection shows as 'no swing', not as perfect insulation."""
    dom, iso, xing = {pe_d: pe, pe_t: pe}, set(), set()
    B.new_sheet("%02d_imd" % sheet_no, "Insulation measurement",
                "AUX1 = V(PE-BUS-), AUX2 = V(PE-A+) (AMC3330); 992 k test strings\n"
                "A+ -> PE, BUS- -> PE, each with a common-source SiC pair (TPSI3050)")
    for lab, ref_net in (("AUX1", pole_neg), ("AUX2", pole_pos)):
        B.block("%s - PE relative to %s" % (lab, ref_net),
                "6 x 1.00 M from PE + 4.99 k: +/-1203 V linear. The divider is a fixed, known 6.005 M path to PE\n"
                "(included in the IMD equations, sim/port_design.py sec. 8). First element = HV-PE crossing.")
        refs = _divider(B, pe_d, ref_net, lab + "_IN", dom, hv, lab)
        xing.add(refs[0])
        iso.add(_amc(B, "AMC3330", lab + "_IN", ref_net, ref_net, lab + "_P", lab + "_N", vdd, agnd, dom, hv, lab))
        B.flag(ref_net)
    s = S["imd"]
    for sw, pole, do in (("P", pole_pos, "IMD_SW_P"), ("N", pole_neg, "IMD_SW_N")):
        assert do in IF.DO
        node, src, g, drv = "IMD%s_SW" % sw, "IMD%s_SRC" % sw, "IMD%s_G" % sw, "IMD%s_DRV" % sw
        dom[node] = dom[src] = dom[g] = dom[drv] = hv
        B.block("Test string %s -> PE (%s)" % (pole, do),
                "%d x TNPV1210 124 k = 992 k (125 V, 0.13 W each at 1000 V): 1 mA test current. Energised = connected.\n"
                "Common-source C2M1000170D pair (PA-09): blocks either polarity, so a pole below PE cannot leak through\n"
                "a body diode. TPSI3050 two-wire from the 24 V DO, reinforced. IDSS <= 100 uA each - EOL baseline cal."
                % s["n_string"])
        _string(B, "TNPV_124K", s["n_string"], pole, node, "IMD%s_R" % sw, dom, hv)
        B.part("C2M1000170D", {"1": g, "2": node, "3": src})
        xing.add(B.part("C2M1000170D", {"1": g, "2": pe_t, "3": src}).ref)
        B.R("10R", drv, g)
        B.R("100k", g, src, note="gate-source: switch open when the driver is unpowered")
        iso.add(_sw_driver(B, do, gnd, src, drv, dom, hv, "IMD%s" % sw))
        B.R("47k", do, gnd, note="DO line pull-down")
        B.flag(src)
    B.block("PE_D bond + one active monitor per DC system",
            "PE_D: own M4 bond (dividers only). The module disables its own measurement by leaving IMD_SW_P/N\n"
            "de-energised (strings open, fail-safe): a passive module still loads each pole with 6.005 M to PE.")
    B.part("PE_BOND", {"1": pe_d}, value="PE bond M4 (IMD divider reference)")
    return {"isolators": iso, "crossings": xing, "domains": dom}


def board_io(B, sheet_no, readback):
    """PORT connector (interfaces.PORT), control connector (CTRL_PINS), ID + readback DAC, sensor 3.3 V, PE bond.
    readback = [(rb1, rb2, R)] per port: both outputs drive the ID line through R (sim/port_design.py sec. 7.4)."""
    rb = S["readback"]
    B.new_sheet("%02d_io" % sheet_no, "Connectors and sensor supply",
                "PORT 26-way to CTRL-C2000, 14-way to SYS-IO-AUX, ID + hold readback,\n"
                "+24V -> +3V3S buck, AGND tie, PE bond")
    B.block("PORT connector (CTRL-C2000) + ID with hold readback",
            "interfaces.PORT, ribbon 1:1. AMC outputs drive the cable directly (<= 500 pF, AMC3330 p4); anti-alias RC\n"
            "is on CTRL-C2000. ID: %s || readback network = %.2f k (PV-PORT code %s; CTRL-C2000 rev D values). RB\n"
            "outputs through 2 x %s (port A) and 2 x %s (port B): nine states, >= %.0f counts apart (%.0f with the hold\n"
            "coils on, sim/port_design.py sec. 7.4); CTRL decodes them with its port-only table."
            % (ohm(rb["R_id_ohm"]), rb["R_id_effective_ohm"] / 1e3, IF.ID_OHM["PV-PORT"], ohm(rb["R_rb_A_ohm"]),
               ohm(rb["R_rb_B_ohm"]), rb["gap_counts"], rb["gap_counts_coils_on"]))
    B.part("J_PORT", IF.pins(IF.PORT))
    code = float(IF.ID_OHM["PV-PORT"].rstrip("k")) * 1e3      # PV-PORT-180 carries the same board code
    assert abs(rb["R_id_effective_ohm"] / code - 1) < 0.005, "readback network moves the board ID"
    B.R(ohm(rb["R_id_ohm"]), "ID", "GND", tol="0.1%", note="board ID PV-PORT with the readback network (interfaces.ID_OHM)")
    for rb1, rb2, r in readback:
        B.R(ohm(r), rb1, "ID", tol="0.1%", note="hold readback")
        B.R(ohm(r), rb2, "ID", tol="0.1%", note="hold readback")
    for net in ("+24V", "GND", "AGND"):
        B.flag(net)
    B.block("Control connector (SYS-IO-AUX)", "port.CTRL_PINS: DO1-DO8 (DO_SPARE = proof test), FB_A + its GND, FB_B +\n"
                                              "its GND (INT-02), 2 coil returns. Port B (battery) drives 2 HVC43 coils:\n"
                                              "K_B_MAIN up to 0.60 A -> SYS DO4 needs ILIM 10 k (1.58-2.30 A).")
    B.part("J_CTRL", {str(i): x for i, x in enumerate(CTRL_PINS, 1)})
    B.block("Sensor supply +3V3S", "LMR36015 400 kHz, 24 V -> 3.315 V (100 k / 43.2 k, SNVSB49D sec 10.2), <= 0.34 A for\n"
                                   "6 x AMC3330 + 2 x AMC3302 + hold logic. AGND joins GND only here (0 ohm).")
    B.part("LMR36015", {"2": "+24V", "10": "+24V", "9": "+24V", "3": None, "1": "GND", "11": "GND",
                        "12": "SS_SW", "4": "SS_BOOT", "7": "SS_FB", "8": None, "5": "SS_VCC", "6": "GND"})
    B.C("4.7u", "+24V", "GND", pkg="1206", volt="50V")
    B.C("220n", "+24V", "GND", volt="50V")
    B.C("100n", "SS_BOOT", "SS_SW")
    B.C("1u", "SS_VCC", "GND", volt="16V")
    B.part("WE_L10U", {"1": "SS_SW", "2": "+3V3S"})
    B.R("100k", "+3V3S", "SS_FB")
    B.R("43.2k", "SS_FB", "GND")
    B.C("22u", "+3V3S", "GND", pkg="1206", volt="10V")
    B.C("22u", "+3V3S", "GND", pkg="1206", volt="10V")
    B.R("0R", "AGND", "GND", note="single AGND-GND tie")
    B.flag("+3V3S")
    B.block("PE bond", "Board PE to chassis at the SPD / Y-capacitor end.")
    B.part("PE_BOND", {"1": "PE"}, value="PE bond M4")
    B.flag("PE")


def build_design(rating=135):
    """PV-PORT (rating 135, PV-P75) or PV-PORT-180 (PV-P100/110): port A = PV, port B = battery / DC bus."""
    B = L.Builder(PROJECT + ("" if rating == 135 else "-%d" % rating), "PV-PORT %d A port board" % rating, REV,
                  DATE, dict(catalog.PARTS, **CATALOG),
                  rails=["+24V", "+3V3S"], returns=["GND", "AGND", "PE"],
                  subtitle="Ports A (PV) and B (bus/battery) of %s, common negative BUS-" %
                           ("PV-P75" if rating == 135 else "PV-P100/110"),
                  comment1="HV-PELV reinforced (1000 VDC, OVC II, PD2, coated to PD1); HV-PE basic + bonding",
                  comment4="Not bench-validated. Values: sim/port_design.py -> port_spec.json",
                  root_notes=["Common negative: port A and port B share BUS- (non-isolated buck-boost), so both ports are "
                              "one HV domain; the main contactors switch the + pole only, fuses are in both poles. "
                              "Chassis-mounted parts (terminals, fuses, CM choke, contactors, RST 200 resistors, "
                              "thermostats, shunt) are drawn here with their real MPN or an RFQ / CUSTOM specification.",
                              "Isolators (checked on every build): AMC3330/AMC3302, TPSI3050-Q1, CNY65B, contactor and "
                              "relay coil-contact. Declared HV-PE crossings: MOV3 of each varistor Y, Y capacitors, the "
                              "PE-side SiC of each insulation-measurement pair, the PE-end element of the AUX dividers.",
                              "IC-17/IC-18: the whole board is conformal-coated to PD1 (IEC 60664-3 Type 1) over all HV "
                              "copper and every HV-PELV isolator footprint (8-8.5 mm packages < 10 mm reinforced at PD2). "
                              "Only the NH fuse links / bases and the DC terminals sit in the cooling-air path (PD3); the "
                              "board, varistor network and branch fuses are in the closed PD2 compartment.",
                              "Surge credit (D-032, IC-01/02/03): the on-board varistor Y limits pole-PE and pole-pole to "
                              "3.8 kV at In 3 kA (calculated) and is monitored: reinforced is dimensioned for 6 kV (AMC3302 "
                              "6.25 kV, AMC3330 7.7 kV pass on this case only). IC-04 OPEN: the G7L coil-contact rating "
                              "(4000 V AC) is below reinforced - fix in rev F1 (own isolated coil supply).",
                              "SYS-IO-AUX: K_DISCH energised first at power-up; the port block itself inhibits the "
                              "discharge of a closed port. Coil flyback never reaches the DO clamps in normal operation "
                              "(two-switch coil drives, varistor / zener across each coil)."])
    readback = [("%s_RB1" % t, "%s_HC2_OC" % t, r) for t, r in (("A", S["readback"]["R_rb_A_ohm"]),
                                                                  ("B", S["readback"]["R_rb_B_ohm"]))]
    board_io(B, 1, readback)
    pa = port(B, "A", "A_BUS+", "BUS-", 2, rating=rating, source="pv", pe_t="PE_T")
    pb = port(B, "B", "B_BUS+", "BUS-", 4, rating=rating, source="battery", pe_t="PE_T")
    assert [(p["nets"]["rb1"], p["nets"]["rb2"]) for p in (pa, pb)] == [r[:2] for r in readback]
    im = imd(B, 6, pa["nets"]["xp"], "BUS-")
    dom = {}
    for d in (pa, pb, im):
        dom.update(d["domains"])
    iso = pa["isolators"] | pb["isolators"] | im["isolators"]
    xing = pa["crossings"] | pb["crossings"] | im["crossings"]
    return B, dom, iso, xing


# =====================================================================================================
# LEAN PORT circuits (D-044, ARCHITECTURE-COSTFIRST sec. 5, 6, 8): sheets of the ONE power board. Everything is
# referenced to BUS- (= controller ground gnd/agnd); numbers from port_spec 'lean' (sim/port_design.py sec. 13).
# Each function draws on the current sheet of B and returns its output nets; PE-side nets go into dom.
# =====================================================================================================
def _lean_drv(B, t, key, g, coil_p, v24, gnd):
    """Fail-safe high-side coil drive: logic G -> BSS138 -> IRFR9214 from the live 24 V (logic lost = coil off);
    S2M + SMBJ33A clamp (fast release, ~34 V)."""
    pd, pg, z = t + key + "_PD", t + key + "_PG", t + key + "_Z"
    B.part("BSS138BK", {"1": g, "3": pd, "2": gnd})
    B.R("100k", g, gnd, note="coil off while the logic is unpowered")
    B.R("10k", pd, pg, pkg="0805")
    B.R("10k", v24, pg, pkg="0805", note="gate-source: off by default")
    B.part("IRFR9214", {"1": pg, "3": v24, "2": coil_p})
    B.part("SMBJ33A", {"1": z, "2": coil_p})
    B.part("S2M", {"1": z, "2": gnd})


def lean_refs(B, t, v5, vdd, agnd):
    """REF3030E (from +5 V) and two 0.1 % ladders: VMID 1.646 V (buffered: drives the divider and amplifier references),
    TH_HI/TH_LO = VMID +/- 0.166 V (200 V on the 1203:1 dividers = 10 V on the G = 20 dV amplifier), HOLD_HI/LO = VMID
    +/- 1.000 V (1.0 kA on 100 uOhm x 10), OC_HI/LO = VMID +/- 0.401 V (400 A). Returns {name: net}."""
    r = {k: t + "_" + k for k in ("REF", "VMIDR", "VMID", "TH_HI", "TH_LO", "HOLD_HI", "HOLD_LO", "OC_HI", "OC_LO")}
    B.part("REF3030E", {"1": v5, "2": r["REF"], "3": agnd})
    B.C("100n", v5, agnd)
    B.C("100n", r["REF"], agnd)
    for a_, b_, v in ((r["REF"], r["TH_HI"], "11.8k"), (r["TH_HI"], r["VMIDR"], "1.65k"), (r["VMIDR"], r["TH_LO"], "1.65k"),
                      (r["TH_LO"], agnd, "14.7k"), (r["REF"], r["HOLD_HI"], "3.57k"), (r["HOLD_HI"], r["OC_HI"], "6.04k"),
                      (r["OC_HI"], r["OC_LO"], "8.06k"), (r["OC_LO"], r["HOLD_LO"], "6.04k"), (r["HOLD_LO"], agnd, "6.49k")):
        B.R(v, a_, b_, tol="0.1%")
    return r


def lean_dividers(B, t, term_pos, bank_pos, r, vdd, agnd, dv=True, vmid_buffer=True):
    """Bank-side and bipolar terminal-side dividers (6 x 1 M ARHV06 + 4.99 k to VMID, 4.7 nF: 1 V = 1203 V around
    VMID), buffered (OPA2388); with dv a G = 20 difference amplifier gives <t>_DVA = VMID + 20 (V_term - V_bank)/1203.
    The VMID follower sits on the second amplifier. Returns {"vx", "vb", "dva"}."""
    out = {"vx": t + "_VX_O", "vb": t + "_VB_O", "dva": t + "_DVA"}
    for top, tap in ((term_pos, t + "_VXD"), (bank_pos, t + "_VBD")):
        nodes = [top] + ["%s_%d" % (tap, k) for k in range(1, 6)] + [tap]
        for a_, b_ in zip(nodes, nodes[1:]):
            B.part("ARHV06_1M", {"1": a_, "2": b_}, value="1M")
        B.part("TNPW_4K99", {"1": tap, "2": r["VMID"]}, value="4.99k")
        B.C("4.7n", tap, r["VMID"], diel="C0G", tol="5%")
    B.part("OPA2388", {"3": t + "_VXD", "2": out["vx"], "1": out["vx"], "5": t + "_VBD", "6": out["vb"], "7": out["vb"],
                       "8": vdd, "4": agnd})
    B.C("100n", vdd, agnd)
    if dv:
        B.R("1.00k", out["vx"], t + "_DVP", tol="0.1%")
        B.R("20.0k", t + "_DVP", r["VMID"], tol="0.1%")
        B.R("1.00k", out["vb"], t + "_DVN", tol="0.1%")
        B.R("20.0k", t + "_DVN", out["dva"], tol="0.1%")
    pa, na, oa = (t + "_DVP", t + "_DVN", out["dva"]) if dv else (agnd, t + "_SPARE", t + "_SPARE")
    pb_, nb_, ob_ = (r["VMIDR"], r["VMID"], r["VMID"]) if vmid_buffer else (agnd, t + "_SPAREB", t + "_SPAREB")
    B.part("OPA2388", {"3": pa, "2": na, "1": oa, "5": pb_, "6": nb_, "7": ob_, "8": vdd, "4": agnd})
    B.C("100n", vdd, agnd)
    return out


def lean_shunt(B, t, port_neg, bus_neg, r, vdd, agnd, oc=True):
    """BUS- shunt (2 x CSS2H-3920R-L200F = 100 uOhm, Kelvin by layout) and a dual zero-drift difference amplifier:
    <t>_IM = VMID + 30 x V_sh (measurement, +/-500 A in +/-1.5 V), <t>_IH = VMID + 10 x V_sh (hold-off / OC path, +/-1.5
    kA). Window comparators on IH: <t>_HOLD (|I| >= 0.97-1.03 kA, keeps an energised contactor closed) and <t>_OC
    (|I| >= 387-413 A, a source for the trip latch; oc=False leaves it to the controller's ADC limit trip on IM,
    D-049 - port_spec lean/firmware_requirements). Returns {"im", "ih", "hold", "oc"} (oc None when omitted)."""
    o = {k: t + "_" + k.upper() for k in ("im", "ih", "hold", "oc")}
    for _ in range(2):
        B.part("CSS2H_L200", {"1": port_neg, "2": bus_neg}, value="0.2m")
    for net, g, rf, cf in (("im", "A", "30.1k", "470p"), ("ih", "B", "10.0k", "1n")):   # IM 11.3 kHz, IH 15.9 kHz
        p_, n_ = t + "_SH%sP" % g, t + "_SH%sN" % g
        B.R("1.00k", port_neg, p_, tol="0.1%")
        B.R(rf, p_, r["VMID"], tol="0.1%")
        B.R("1.00k", bus_neg, n_, tol="0.1%")
        B.R(rf, n_, o[net], tol="0.1%")
        B.C(cf, n_, o[net], diel="C0G", tol="5%")
    B.part("OPA2388", {"3": t + "_SHAP", "2": t + "_SHAN", "1": o["im"], "5": t + "_SHBP", "6": t + "_SHBN", "7": o["ih"],
                       "8": vdd, "4": agnd})
    B.C("100n", vdd, agnd)
    if not oc:
        o["oc"] = None
    for out_, hi, lo in ((o["hold"], r["HOLD_HI"], r["HOLD_LO"]),) + (((o["oc"], r["OC_HI"], r["OC_LO"]),) if oc else ()):
        B.part("TLV3502", {"1": o["ih"], "2": hi, "3": lo, "4": o["ih"], "8": vdd, "7": out_ + "P", "6": out_ + "N",
                           "5": agnd})
        B.C("100n", vdd, agnd)
        B.part("LVC1G32", {"1": out_ + "P", "2": out_ + "N", "5": vdd, "4": out_, "3": agnd})
        B.C("100n", vdd, agnd)
    return o


def lean_interlock(B, t, d, r, vdd, agnd, dv=True):
    """Hardware close interlocks (ARCHITECTURE 6.2-1): <t>_POL_OK = terminal side >= 187-213 V of the right polarity;
    with dv, <t>_DV_OK = |V_bank - V_term| <= 3.5-16.5 V (precharge done). Returns {"pol", "dv"} (dv -> vdd if unused)."""
    o = {"pol": t + "_POL_OK", "dv": t + "_DV_OK" if dv else vdd}
    ob_ = t + "_DVLO" if dv else None
    B.part("TLV3502", {"1": d["vx"], "2": r["TH_HI"], "3": d["dva"] if dv else agnd, "4": r["TH_LO"] if dv else vdd,
                       "8": vdd, "7": o["pol"], "6": ob_, "5": agnd})
    B.C("100n", vdd, agnd)
    if dv:
        B.part("TLV3502", {"1": r["TH_HI"], "2": d["dva"], "3": agnd, "4": vdd, "8": vdd, "7": t + "_DVHI",
                           "6": None, "5": agnd})
        B.C("100n", vdd, agnd)
        B.part("LVC1G11", {"1": t + "_DVLO", "3": t + "_DVHI", "6": vdd, "5": vdd, "4": o["dv"], "2": agnd})
        B.C("100n", vdd, agnd)
    return o


def lean_contactor(B, t, term_pos, bank_pos, cmd, pol_ok, dv_ok, hold, v24, gnd, vdd, agnd, economiser=True,
                   part="HFE82V"):
    """HFE82V-300C/1000 in the + pole. Coil = (cmd AND pol_ok AND dv_ok) OR (hold AND coil_on): the hold-off keeps an
    energised coil on above 0.97-1.03 kA and can never energise an open contactor. cmd = firmware command already
    gated by the trip latch (control board). No auxiliary contact: weld check by firmware (bank moved, VX not
    following). economiser (default): coil return through R_h = 4 x 37.4 ohm, bypassed by a PMV30ENEA for 100-152 ms
    after every rise of the coil feed (C_t 1 u / R_t 56 k differentiator, BZX84-C15 clamps +15 V / -0.7 V, so any
    fall of the feed - off or supply dip - resets it and the next rise pulls in at full voltage). No firmware; hold
    2.35 W at 24 V instead of 6 W (port_spec lean/coil_economiser). economiser=False: coil straight to gnd (rev F0).
    Returns the logic net <t>_K_G."""
    a_, hg, g, coil = t + "_K_A", t + "_K_HG", t + "_K_G", t + "_K_COIL"
    lo = t + "_K_LO" if economiser else gnd
    assert part in S["lean"]["contactors"], "contactor %s is not coordinated in port_spec lean/contactors" % part
    B.part(part, {"A1": term_pos, "A2": bank_pos, "1": coil, "2": lo})
    if economiser:
        e, gb = S["lean"]["coil_economiser"], t + "_K_GB"
        nodes = [lo] + ["%s_K_H%d" % (t, k) for k in (1, 2, 3)] + [gnd]
        for a_, b_ in zip(nodes, nodes[1:]):
            B.R("37.4", a_, b_, pkg="2512", note="economiser hold resistor, 4 in series = %.0f ohm" % e["R_hold_ohm"])
        B.part("PMV30ENEA", {"1": gb, "3": lo, "2": gnd})
        B.C("1u", coil, gb, pkg="1206", volt="50V", tol="10%")
        B.R("56k", gb, gnd, note="pull-in window R_t C_t = 56 ms")
        B.part("BZX84C15", {"3": gb, "1": gnd, "2": None})
    B.R("100k", cmd, gnd, note="command off while the control board is absent (D-049)")
    if pol_ok is None and dv_ok is None:      # interlocks='hold': firmware sequences polarity / dV (D-049)
        a_ = cmd
    else:
        B.part("LVC1G11", {"1": cmd, "3": pol_ok or vdd, "6": dv_ok or vdd, "5": vdd, "4": a_, "2": agnd})
        B.C("100n", vdd, agnd)
    B.part("LVC1G11", {"1": hold, "3": g, "6": vdd, "5": vdd, "4": hg, "2": agnd})
    B.C("100n", vdd, agnd)
    B.part("LVC1G32", {"1": a_, "2": hg, "5": vdd, "4": g, "3": agnd})
    B.C("100n", vdd, agnd)
    _lean_drv(B, t, "_K", g, coil, v24, gnd)
    return g


def lean_precharge(B, t, term_pos, bank_pos, cmd_pre, pol_ok, v24, gnd, vdd, agnd, r_ohm=220.0):     # pol_ok None: no gating
    """Battery port: G7L-2A-X (poles in series, current terminal -> bank) + 220 ohm aluminium-housed resistor (RFQ,
    port_spec lean/precharge); coil = cmd_pre AND pol_ok. Returns the logic net <t>_P_G."""
    pm, pr, g, coil = t + "_PRE_M", t + "_PRE_R", t + "_P_G", t + "_P_COIL"
    B.part("G7L2AX", {"8": term_pos, "6": pm, "4": pm, "2": pr, "1": coil, "0": gnd})
    B.part("RPRE_AL" if r_ohm == 220.0 else "RPRE_AL_%.0f" % r_ohm, {"1": pr, "2": bank_pos}, value="%.0fR RFQ" % r_ohm)
    if pol_ok is None:                        # interlocks='hold': the 100 k of the driver pulls the command down
        g = cmd_pre
    else:
        B.R("100k", cmd_pre, gnd, note="command off while the control board is absent")
        B.part("LVC1G11", {"1": cmd_pre, "3": pol_ok, "6": vdd, "5": vdd, "4": g, "2": agnd})
        B.C("100n", vdd, agnd)
    _lean_drv(B, t, "_P", g, coil, v24, gnd)
    return g


def lean_bleeder(B, t, bank_pos, bus_neg):
    """Passive bleeder on a port bank: 8 x 73.2 k 1210 (585.6 k since D-056's seventh battery-side capacitor; port_spec
    lean/bleeder: 60 V within the label time, worst case). 'Wait 10 min' (3 phases) / 15 min (4 phases) label on the enclosure."""
    b = S["lean"]["bleeder"]
    nodes = [bank_pos] + ["%s_BL%d" % (t, k) for k in range(1, b["n"])] + [bus_neg]
    for a_, b_ in zip(nodes, nodes[1:]):
        B.R(ohm(b["R_elem_ohm"]), a_, b_, pkg="1210", note="HV thick film, >= 200 V working")


def lean_imd(B, pole_pos, pole_neg, pe_t, pe_d, r, cmd_p, cmd_n, v5, gnd, vdd, agnd, dom, xing):
    """Insulation monitor (two-state method, port_spec imd): PE divider (6 x 1 M from pe_d + 4.99 k to VMID) ->
    IMD_AUX_O; test strings 8 x 124 k from pole_pos and from pole_neg, each through a CA-IS3417WT (1700 V SSR) to pe_t;
    SSR LEDs from +5 V through 330 ohm (11 mA) switched by BSS138 from the controller (cmd_p / cmd_n, default off).
    Returns {"aux"}."""
    dom.update({pe_t: "PE", pe_d: "PE"})
    nodes = [pe_d] + ["IMD_AUXD_%d" % k for k in range(1, 6)] + ["IMD_AUXD"]
    for k, (a_, b_) in enumerate(zip(nodes, nodes[1:])):
        ref = B.part("ARHV06_1M", {"1": a_, "2": b_}, value="1M").ref
        if k == 0:
            xing.add(ref)
    B.part("TNPW_4K99", {"1": "IMD_AUXD", "2": r["VMID"]}, value="4.99k")
    B.C("4.7n", "IMD_AUXD", r["VMID"], diel="C0G", tol="5%")
    B.part("OPA2388", {"3": "IMD_AUXD", "2": "IMD_AUX_O", "1": "IMD_AUX_O", "5": agnd, "6": "IMD_SPARE",
                       "7": "IMD_SPARE", "8": vdd, "4": agnd})
    B.C("100n", vdd, agnd)
    for sw, pole, cmd in (("P", pole_pos, cmd_p), ("N", pole_neg, cmd_n)):
        node, led, k_ = "IMD%s_S" % sw, "IMD%s_A" % sw, "IMD%s_K" % sw
        chain = [pole] + ["IMD%s_R%d" % (sw, k) for k in range(1, S["imd"]["n_string"])] + [node]
        for a_, b_ in zip(chain, chain[1:]):
            B.part("ARHV13_124K", {"1": a_, "2": b_}, value="124k")
        xing.add(B.part("CAIS3417", {"4": led, "3": k_, "5": k_, "1": None, "2": None, "6": None, "11": node, "12": node,
                                     "9": pe_t, "10": pe_t, "7": None, "8": None}).ref)
        B.R("330R", v5, led, pkg="0805", note="SSR input 11 mA (IF(ON) 7-30 mA)")
        B.part("BSS138BK", {"1": cmd, "3": k_, "2": gnd})
        B.R("100k", cmd, gnd, note="string open by default")
    return {"aux": "IMD_AUX_O"}


def lean_port(B, t, source, sheet_no, term_pos, term_neg, bank_pos, bus_neg, cmd, cmd_pre=None, pe_t="PE_T", v24="+24V",
              v5="+5V", vdd="+3V3", gnd="GND", agnd="AGND", dom=None, xing=None, economiser=True, interlocks="full",
              oc_trip=True, refs=None, contactor=None, fuse="HPE501", bleeder=True, r_pre=220.0):
    """One lean port on three sheets (power path; sensing + interlocks; coil drives), source 'pv' (contactor only) or 'battery'
    (aR fuse per pole, precharge, dV interlock). Returns the nets the controller needs. fuse: catalog key of the aR link
    (HPE501 = the coordinated 250 A part of port_spec lean; another key = the caller coordinates it); bleeder=False when the
    bank has its own bleeders (PCS split DC link)."""
    batt = source == "battery"
    dom = {} if dom is None else dom
    xing = set() if xing is None else xing
    lp = S["lean"]
    B.new_sheet("%02d_lean%s_power" % (sheet_no, t), "Port %s power path (%s)" % (t, source),
                "Varistor Y + monitor%s" % (", aR fuses" if batt else ""))
    hpe = fuse == "HPE501"
    B.block("Port %s (%s source), D-044 lean port" % (t, source),
            "Terminal side %s / %s, bank %s. %s\nContactor opens <= 0.97 kA, held above (port_spec lean). %s" %
            (term_pos, term_neg, bank_pos, ("HPE501 250 A aR in both poles (breaks 1.25-50 kA only)." if hpe else
                                            "%s aR in both poles." % B.catalog[fuse]["mpn"]) if batt else
             "No fuse, no precharge: array Isc <= 1.25 x rating.",
             "Installation: upstream clears %d-%d A within %.2f s." % (lp["installation"]["band_A"][0],
                                                                       lp["installation"]["band_A"][1],
                                                                       lp["installation"]["clear_within_s"])
             if hpe or not batt else "Fuse / contactor coordination: the board's design check."))
    spdn = varistor_y(B, t, term_pos, term_neg, pe_t, vdd, agnd, "LIVE", dom, set(), xing)
    tp, tn = (t + "_T+", t + "_T-") if batt else (term_pos, term_neg)
    if batt:
        B.block("aR fuses (battery-type port)", ("HPE501/000B100-250 in both poles: breaks 1.25-50 kA only (partial range);\n"
                                                "the port OC trip and the contactor keep it out of 250 A-1.25 kA.") if hpe else
                "%s in both poles (aR, partial range): see the board's design check." % B.catalog[fuse]["mpn"])
        B.part(fuse, {"1": term_pos, "2": tp})
        B.part(fuse, {"1": term_neg, "2": tn})
    B.new_sheet("%02d_lean%s_ctrl" % (sheet_no + 1, t), "Port %s sensing and interlocks" % t,
                "References, BUS- shunt amplifier + hold-off / OC windows, dividers, dV amplifier,\n"
                "polarity and dV interlocks")
    r = refs or lean_refs(B, t, v5, vdd, agnd)        # refs: one reference / VMID shared by both ports
    assert interlocks in ("full", "hold"), "interlocks: 'full' (hardware polarity / dV) or 'hold' (hold-off only)"
    # D-050: 'full' and oc_trip=True are the design; 'hold' / oc_trip=False (D-049) are kept only as options
    full = interlocks == "full"
    sh = lean_shunt(B, t, tn, bus_neg, r, vdd, agnd, oc=oc_trip)
    d = lean_dividers(B, t, tp, bank_pos, r, vdd, agnd, dv=batt and full, vmid_buffer=refs is None)
    il = lean_interlock(B, t, d, r, vdd, agnd, dv=batt) if full else {"pol": None, "dv": None}
    B.new_sheet("%02d_lean%s_drive" % (sheet_no + 2, t), "Port %s coil drives" % t,
                "Contactor%s: interlock logic, fail-safe high side, economiser;\npassive bank bleeder" %
                (" + precharge relay" if batt else ""))
    k = lean_contactor(B, t, tp, bank_pos, cmd, il["pol"], il["dv"], sh["hold"], v24, gnd, vdd, agnd, economiser=economiser,
                       part=contactor or S["lean"]["contactor_per_source"][source])
    if bleeder:
        lean_bleeder(B, t, bank_pos, bus_neg)
    pg = lean_precharge(B, t, tp, bank_pos, cmd_pre, il["pol"], v24, gnd, vdd, agnd, r_ohm=r_pre) if batt else None
    return dict(spd_n=spdn, vx=d["vx"], vb=d["vb"], im=sh["im"], oc=sh["oc"], hold=sh["hold"], pol_ok=il["pol"],
                dv_ok=il["dv"], k_g=k, p_g=pg, vmid=r["VMID"], refs=r, dom=dom, xing=xing)


def vrange_lean(net):
    """DC ranges for the lean sheets against BUS- (the live reference); PE nets 0. None = not assessed."""
    if net in ("GND", "AGND", "BUS-", "PE", "PE_T", "PE_D"):
        return (0.0, 0.0)
    if net == "+24V":
        return (0.0, V24[1])
    if net == "+5V":
        return (0.0, 5.5)
    if net == "+3V3" or re.search(r"_(REF|VMIDR|VMID|TH_HI|TH_LO|HOLD_HI|HOLD_LO|OC_HI|OC_LO|VX_O|VB_O|DVA|IM|IH|HOLD|OC|"
                                  r"POL_OK|DV_OK|DVLO|DVHI|K_A|K_HG|K_G|P_G|SPD_N|SHAP|SHAN|SHBP|SHBN|DVP|DVN)$|^(CMD_|IMD_AUX_O$)", net):
        return (0.0, 3.45)
    if re.search(r"_(K|P)_PD$|_(K|P)_PG$", net):
        return (0.0, V24[1])
    if re.search(r"_(IN|T|SP)\+$|_PRE_[MR]$", net):
        return (-V_POLE, V_POLE)
    if re.search(r"_BK\+$", net):
        return (0.0, V_POLE)
    if re.search(r"_(IN|T|SP)-$", net):
        return (-0.5, 0.5)
    return None


def build_lean(interlocks="full", oc_trip=True):
    """Verification board PORT-LEAN: both lean ports + the insulation monitor drawn with the functions above (the power
    board designer calls the same functions). Not a product board; it proves the sheets build, pass ERC, the
    domain check and the stress check."""
    B = L.Builder("PORT-LEAN" + ("" if interlocks == "full" else "-HOLD"), "Lean port sheets", REV, DATE, dict(catalog.PARTS, **CATALOG),
                  rails=["+24V", "+5V", "+3V3"], returns=["GND", "AGND", "PE"],
                  subtitle="D-044 lean ports A (PV) and B (battery) + insulation monitor, BUS- referenced",
                  comment1="Live side referenced to BUS-; basic HV-PE; functional inside the live side",
                  comment4="Not bench-validated. Values: sim/port_design.py -> port_spec.json 'lean'",
                  root_notes=["Verification build of the reusable lean-port functions of gen/port.py (lean_port, "
                              "varistor_y, lean_contactor, lean_precharge, lean_bleeder, lean_dividers, lean_shunt, "
                              "lean_interlock, lean_imd). The product sheets live on the power board."])
    dom, xing = {"PE_T": "PE"}, set()
    B.new_sheet("01_lean_io", "Supplies and commands", "Live 24 V / 5 V / 3.3 V, BUS- tie, commands")
    B.block("Supplies and commands", "On the power board these come from the AUX-T1 live winding and the controller.")
    B.part("J_LEANIO", {"1": "+24V", "2": "+5V", "3": "+3V3", "4": "GND", "5": "CMD_KA", "6": "CMD_KB", "7": "CMD_PB",
                        "8": "CMD_IMDP", "9": "CMD_IMDN", "10": "A_OC" if oc_trip else None, "11": "B_OC" if oc_trip else None,
                        "12": "A_IM", "13": "B_IM", "14": "IMD_AUX_O"})
    for net in ("+24V", "+5V", "+3V3", "GND", "AGND", "PE"):
        B.flag(net)
    B.R("0R", "AGND", "GND", note="single AGND-GND tie")
    B.R("0R", "GND", "BUS-", pkg="2512", note="controller ground = BUS- (star point)")
    B.part("PE_LINK", {"1": "PE_T", "2": "PE"})
    B.part("PE_BOND", {"1": "PE_D"}, value="PE bond M4 (IMD divider reference)")
    B.part("J_LEANHV", {"1": "A_IN+", "2": "A_IN-", "3": "B_IN+", "4": "B_IN-", "5": "A_BK+", "6": "B_BK+", "7": "BUS-"})
    kw = dict(dom=dom, xing=xing, interlocks=interlocks, oc_trip=oc_trip)
    pa = lean_port(B, "A", "pv", 2, "A_IN+", "A_IN-", "A_BK+", "BUS-", "CMD_KA", **kw)
    pb = lean_port(B, "B", "battery", 5, "B_IN+", "B_IN-", "B_BK+", "BUS-", "CMD_KB", cmd_pre="CMD_PB", refs=pa["refs"], **kw)
    for cmd in ("CMD_KA", "CMD_KB", "CMD_PB"):      # D-049: control board absent -> every coil command reads 'off'
        pd = [p for p in B.D.parts.values() if p.lib_id.split(":")[1] == "R" and sorted(p.pins.values()) == [cmd, "GND"]]
        assert pd and all((L.number(p.value) or 0) <= 220e3 for p in pd), "%s has no pull-down to GND" % cmd
    B.new_sheet("08_lean_imd", "Insulation monitor", "PE divider, two switched 992 k strings through CA-IS3417WT")
    r = pa["refs"]
    lean_imd(B, "A_IN+", "BUS-", "PE_T", "PE_D", r, "CMD_IMDP", "CMD_IMDN", "+5V", "GND", "+3V3", "AGND", dom, xing)
    return B, dom, xing


V24 = (21.6, 26.45)       # SYS-IO-AUX / CELL 24 V bus (sim/port_design.py V24)
V_POLE = 1144.0           # highest DC on a pole: OV trip overshoot (port_spec assumptions A_V_TRIP_OS)


def vrange(net):
    """DC range of a port-block net against its own domain's reference (PELV: GND, HV: the port's negative bus, PE: PE),
    or None (coil, gate, switch, string tap, pulse node) - for L.build(vrange=...); dab60.py can pass it unchanged.
    # ponytail: name patterns of port()/imd()/board_io(); a new net without a pattern is simply not assessed."""
    if net in ("GND", "AGND", "PE", "PE_T", "PE_D") or re.fullmatch(r"(P\d_)?BUS\d?-", net):
        return (0.0, 0.0)
    if net == "+24V" or net in IF.DO or net in IF.DI or re.search(r"_FB(\d|_ANY)$|_HC_(PD|PG|OUT|G2|X)$", net):
        return (0.0, V24[1])
    if net == "+3V3S" or re.search(r"^(ID|[VI][A-Z0-9]*_[PN]|AUX\d_[PN])$|_RB1A?$|_SPD_N$|_HC_OUTL$|_HC[12]_|_TST_(IN|T|TD|DRC|T2A|T2B)$", net):
        return (0.0, 3.45)
    if re.search(r"_(IN|F|X|SP)\+$|BUS\d?\+$|_(PRE_M|PRE_R|DIS_M|DIS_D)$", net):
        return (0.0, V_POLE)
    if re.search(r"_(IN|F|X|SP)-$", net):
        return (-0.5, 0.5)
    if re.search(r"_DIS_(G|T|TE|REF|SB|SG|TH|OFF)$", net):
        return (0.0, 16.0)
    return None


# Warning types waived (never errors). Callers of port() / imd() need the same entry.
WAIVERS = {"ground_pin_not_ground": "AMC3330/AMC3302 HGND and DCDC_HGND are the isolated high-side references and sit "
                                    "on HV nets (BUS-, <tag>_X-, A_X+) by design; a GND pin wrongly placed on a PELV "
                                    "or HV net is still caught by the isolation-domain check"}

if __name__ == "__main__":
    rc = 0
    if "lean" in sys.argv:
        for mode in (("full", True), ("hold", False)):          # D-049: hardware interlocks, or hold-off only
            B, dom, xing = build_lean(*mode)
            rc |= L.build(B, waivers=WAIVERS, domain_of=lambda net: dom.get(net, "PE" if net == "PE" else "LIVE"),
                          isolators=set(), crossings=xing, vrange=vrange_lean)
        sys.exit(rc)
    for r in (135, 180):            # PV-PORT (PV-P75) and PV-PORT-180 (PV-P100/110): same checks, own BOM
        B, dom, iso, xing = build_design(r)
        rc |= L.build(B, waivers=WAIVERS, domain_of=lambda net: dom.get(net, "PE" if net == "PE" else "PELV"),
                      isolators=iso, crossings=xing, vrange=vrange)
    sys.exit(rc)
