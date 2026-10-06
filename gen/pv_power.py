"""PV-PWR / PV-PWR-4 - POWER board of the cost-first PV module (decision D-044, docs/requirements/ARCHITECTURE-COSTFIRST.md
sections 1, 3-6, 8-11): ONE board with every phase (3 for PV-P75, 4 for PV-P100/110) and everything that is not the
controller. All of it is live and referenced to BUS- (GND = AGND = BUS-, joined at one star point); other domains are
only the gate-drive islands (functional), the AUX-T1 reinforced SELV winding (leaves on a 2-pin connector) and PE.

  01        PC connector (interfaces.PC) to the control board, 3.3 -> 5 V command buffers (gdrv.input_buffers), RDY / FLT_N,
            +5 V supervisor, port status glue (HOLD, MOV_OK, port over-current into FLT_N), ground star points
  per phase power stage (2 x SG2M040170HJ per switch on AlN pads, film decoupling + RC damper per leg, the phase's share of
            the port banks), gate bias (gdrv.bias_phase), inductor (CUSTOM, chassis) through a Sinomags STK-HO/A 75
            open-loop current sensor, heatsink and inductor NTC inputs; two sheets of gdrv.channel(bias="ext") per phase
  ports     gen/port.py lean_port() A (PV) and B (battery), DC terminals, CM ring core, X/Y capacitors, insulation monitor
  supplies  +5V / +5V_GD (2 x LMR38020) from the live 24 V, +3V3 (TPS62130, controlled ramps), SELV connector
  aux       gen/aux_hv.py aux75_block(): 75 W flyback from both ports' terminal taps, live 24 V + reinforced SELV 24 V
Values are read at build time from sim/out/pv_design/cell_spec.json, pv_control/control_spec.json, pv_design/module_spec.json,
magnetics/design_pv_inductor.json and port_design/port_spec.json; design_check() asserts them on every build.
Usage: .venv/bin/python gen/pv_power.py      (builds PV-PWR and PV-PWR-4, each with its own checks and BOM)
Rev A2 (2026-10-05, D-056): PV-P75 battery-side film bank 7 x 45 uF (7th on phase 2), bleeders 8 x 73.2 k, STK Uref on PC (IL1R-IL4R).
"""
import hashlib
import json
import math
import os
import re
import sys

import numpy as np

import aux_hv
import catalog
import dcdclib as L
import gdrv
import interfaces as IF
import port
import pvcell

REV, DATE = "A2", "2026-10-05"
PROJECT = {3: "PV-PWR", 4: "PV-PWR-4"}
DS = "docs/datasheets/"
OUT = lambda *p: os.path.join(L.REPO, "sim", "out", *p)


SPEC = json.load(open(os.path.join(L.REPO, "sim", "out", "pv_design", "cell_spec.json")))   # LIVE cell spec (pvcell.SPEC is the earlier platform's frozen snapshot)                                                   # cell_spec.json (per phase = one former cell)
CTRL = json.load(open(OUT("pv_control", "control_spec.json")))
MODS = json.load(open(OUT("pv_design", "module_spec.json")))
MAG = json.load(open(OUT("magnetics", "design_pv_inductor.json")))   # read at build time: rev M1 / M2 / later
spec = pvcell.make_spec(SPEC)
# every spec file this board's numbers are read from (relative to the repository): the design check records their hashes, so that
# gen/pv_ctrl.py refuses a power-board check that was built from other versions (PCM-23)
STAMP_FILES = ("sim/out/pv_design/cell_spec.json", "sim/out/pv_control/control_spec.json", "sim/out/pv_design/module_spec.json",
               "sim/out/pv_design/report.md", "sim/out/magnetics/design_pv_inductor.json", "sim/out/port_design/port_spec.json",
               "sim/out/aux_hv_design/aux75_spec.json", "sim/out/gdrv_miller/result_primary.json", "sim/out/port_design/report.md")
FAN_FLOW_TOL = 0.03          # airflow reduction treated as inside the thermal margin (PCM-26); more than this derates module_spec

# ------------------------------------------------------------------------------------------------ catalog
MAG_ROW = MAG["cost"]["cost_estimates_row"].split(",")[0]
_el = MAG["electrical"]
L_DESC = ("CHASSIS-MOUNTED CUSTOM power inductor rev %s (sim/out/magnetics/design_pv_inductor.json, winding sheet "
          "sim/out/magnetics/spec_pv_inductor.md): %s. L0 %.0f uH, %.0f uH nom / %.0f uH min at 45 A, ripple %.1f App at "
          "%.0f kHz, %.2f kg, %s. Leads A (through the phase's STK-HO/A 75 on the board) and B on press-fit studs; "
          "embedded winding NTC to a 2-pin header (NTC line on PC); core and clamp bonded to PE (basic winding-core, "
          "B2): %s" % (MAG["revision"], MAG["construction"], _el["L0_H"] * 1e6, _el["L_at_45A_nom_H"] * 1e6,
                        _el["L_at_45A_min_H"] * 1e6, _el["ripple_pp_A"], _el["f_ripple_Hz"] / 1e3, MAG["mass_kg"],
                        MAG["status"], MAG["insulation"]["system"]))
PARTS = {
    # XKB X9555WV drawing (single page, docs/datasheets/connectors/XKB-X9555WV.pdf): poles 2x03 ... 2x32, 2.54 mm,
    # 250 V AC/DC, 3 A, 1000 V AC 1 min, -40..+125 C; odd/even numbering = KiCad Conn_02x32_Odd_Even (as the 2x20 / 2x13)
    "J_PC": dict(mfr="XKB Connection", mpn="X9555WV-2x32-6TV01", prefix="J", pkg="2x32 2.54 mm box header THT",
                 ds=DS + "connectors/XKB-X9555WV.pdf", stock=("Connector_Generic", "Conn_02x32_Odd_Even"),
                 desc="Shrouded box header 2x32 2.54 mm: PC contract to the control board, live (250 V, 3 A, -40..125 C)"),
    # Sinomags STK-HO/A series Ver1.5 (2026-08-31), Sinomags-STK-HO-A.pdf: p5 (sec. 3) STK-HO/A 75 data; p16 (PDF page
    # 17) 'Dimension & Pin definitions': 1 +Uc, 2 GND (- or 0 V), 3 Uout, 4 Uref (OUT), 5 NC; integrated U primary
    # pins 6-9 and 10-13 (body arrow: 6-9 -> 10-13 = positive output, verify at incoming inspection)
    "STK75": dict(mfr="Sinomags Technology", mpn="STK-HO/A 75", prefix="CS", pkg="PCB mount 35.4 x 25 x 21.7 mm, U-bar primary",
                  ds=DS + "sensing/Sinomags-STK-HO-A.pdf",
                  desc="Open-loop current sensor, integrated primary, IPN 75 A rms, IPM +/-187.5 A, 10.667 mV/A around "
                       "Vref 2.5 V at Vcc 5 V, 1 MHz, 0.2 us; 4 kV rms / 8 kV impulse, 13.8 mm creepage",
                  pins={"left": ["6 IP+ p", "7 IP+ p", "8 IP+ p", "9 IP+ p", None, "1 +UC pi", "2 GND pi"],
                        "right": ["10 IP- p", "11 IP- p", "12 IP- p", "13 IP- p", None, "3 UOUT o", "4 UREF o",
                                  "5 NC nc"]}),
    # Jianghai CBB 138 DS (Jianghai-JE26-Film.pdf p28-30, v2026.2), class A3: 1300 VDC <= 70 C / 1100 VDC <= 85 C hot
    # spot. Order-code letters as gen/aux_hv.py (K = 10 %, pin style 0, 5.0 mm pins) - confirm with Jianghai.
    "FILM45": dict(mfr="Jianghai", mpn="FCSA3DS456K050H8F9DE3", prefix="C", pkg="4-pin 57.5 x 35 x 70 mm, P1 52.5 / P2 20.3",
                   ds=DS + "passives-capacitors/Jianghai-JE26-Film.pdf", stock=("Device", "C"),
                   desc="DC-link PP film 45 uF 10 %, 1300 VDC (70 C) / 1100 VDC (85 C), Imax 22.1 A (85 C) / 29.0 A (70 C) "
                        "at 10 kHz, peak 900 A, ESR 4.0 mOhm, Ls 35 nH, IEC 61071 (port bank)"),
    "FILM2U2": dict(mfr="Jianghai", mpn="FCSA3DS225K050IC90BE3", prefix="C", pkg="2-pin 32 x 14 x 28 mm, pitch 27.5 mm",
                    ds=DS + "passives-capacitors/Jianghai-JE26-Film.pdf", stock=("Device", "C"),
                    desc="PP film 2.2 uF 10 %, 1300 VDC (70 C) / 1100 VDC (85 C), Imax 3.9 A (85 C) / 5.2 A (70 C), peak "
                         "176 A, ESR 22.5 mOhm, Ls 25 nH, 80 V/us (leg decoupling)"),
    "L_PV": dict(mfr="", mpn="", prefix="L", pkg="chassis, 2 power leads + NTC leads + core clamp", ds="", sourcing="CUSTOM",
                 desc=L_DESC, pins={"left": ["1 A p", None, "3 NTC1 p"], "right": ["2 B p", None, "4 NTC2 p", "5 CORE p"]}),
    # Heatsink NTC: no Asian probe with a stated >= 2.2 kV rms lead-to-lug rating was found on file (R-10): made to order
    "NTC_HS": dict(mfr="", mpn="", prefix="RT", pkg="ring-lug probe M4, 2 x 300 mm PTFE leads", ds="", sourcing="CUSTOM",
                   desc="CHASSIS CUSTOM NTC lug probe (EXSENSE / Shiheng class, made to order): 10 k 1 %, R/T curve of the "
                        "inductor NTC (B25/100 3988 K, TDK 8016 equivalent), -40..150 C, lug screwed to the EARTHED "
                        "heatsink: lead-to-lug dielectric >= 2500 V rms 60 s, double PTFE leads (basic HV-PE for 1000 V "
                        "DC, insulation_spec 'Basic HV-PE', R-10)",
                   pins={"left": ["1 T1 p", "2 T2 p"], "right": ["3 LUG p"]}),
    "HEATSINK": dict(mfr="", mpn="", prefix="HS", pkg="Al extrusion, 2 device rows", ds="", sourcing="CUSTOM",
                     desc="CHASSIS CUSTOM heatsink: one Al extrusion for all devices (two rows: leg A, leg B), EARTHED (two "
                          "PE bonds >= 6 mm2), R_sa <= 0.06 K/W at 400 m3/h (PV-P75) / 0.049 K/W (PV-P100/110), with the "
                          "TO-247 spring clips (one per device, on the plastic body) and G-779-class grease (two bond "
                          "lines per AlN pad)", pins={"left": ["1 PE p"]}),
    # Qingdao Yunlu nanocrystalline core manual (Yunlu-Nanocrystalline-Cores.pdf, ring table): N-C-644025 64/40/25 mm,
    # cased 67/37/29 mm, Ae 225 mm2, le 160.4 mm, mu_i 70000 / 24000 +/-30 %
    "CM_RING": dict(mfr="Qingdao Yunlu", mpn="N-C-644025", prefix="L", pkg="ring 67/37/29 mm cased, on both port conductors",
                    ds=DS + "magnetics/Yunlu-Nanocrystalline-Cores.pdf",
                    desc="CHASSIS nanocrystalline ring core 64/40/25 mm (cased), mu_i 70000 / 24000 +/-30 %: one turn of "
                         "BOTH conductors of the port = CM choke for 150 kHz-30 MHz (ARCHITECTURE 8.1); pins = conductors",
                    pins={"left": ["1 A1 p", "3 B1 p"], "right": ["2 A2 p", "4 B2 p"]}),
}
PARTS["J_NTC2"] = dict(port.CATALOG["J_NTC"], desc="NTC leads (heatsink probe / inductor winding NTC), 2-pin pluggable")
PARTS["J_SELV"] = dict(port.CATALOG["J_NTC"], desc="SELV 24 V (AUX-T1 reinforced winding) to the control board's SELV zone: "
                                                  ">= 10 mm from live copper, double-insulated wires (ARCHITECTURE 10, J2)")
PICK_PVCELL = ("SG2M040170HJ", "PAD_ALN", "R_DAMP", "AHCT1G08", "TPS3710", "NETTIE")
CATALOG = {**catalog.PARTS, **gdrv.PARTS, **port.CATALOG, **{k: pvcell.CATALOG[k] for k in PICK_PVCELL},
           **{k: sys.modules["ctrl_c2000"].PARTS[k] for k in ("LMR38020", "L15U", "TPS62130", "L2U2")}, **PARTS}

# ------------------------------------------------------------------------------------------------ design inputs
N_PAR = spec("switch_positions", 0, "parallel")
R_GATE = (spec("switch_positions", 0, "gate_resistor_on_ohm"), spec("switch_positions", 0, "gate_resistor_off_ohm"))
GATE_V = (spec("gate_drive", "V_GS_on_V"), -spec("gate_drive", "V_GS_off_V"))
VCLASS, R_SB = pvcell.VCLASS, pvcell.R_SB
V_PEAK = spec("switch_node_recurring_peak", "at_1100V_trip_V")
DT = (pvcell.DT_R, 100e-12)                    # interlock stretch, as PVCELL-25 rev C0 (window checked at this board's 5 V)
NEG_DET = True   # gdrv rev 9 defaults it OFF, but its simulation shows a lost negative rail is NOT caught by DESAT
#                  (~+5.1 V at the die, ~1.6 mJ per event, ~50 W at 32 kHz, undetected): the detector is fitted here
V_OVP = spec("protection", "overvoltage_hw_trip_port_A_V")
FB5 = (100e3, 24.9e3)                          # LMR38020 RFBT / RFBB 0.1 % (ctrl_c2000 circuit, SNVSC40E table 9-1)
V5 = (0.985 * (1 + FB5[0] * 0.999 / (FB5[1] * 1.001)), 1.015 * (1 + FB5[0] * 1.001 / (FB5[1] * 0.999)))   # VREF p6
FB33 = (31.6e3, 10.0e3)                        # TPS62130 FB divider 0.1 % -> 3.328 V nominal
V33 = (0.7816 * (1 + FB33[0] * 0.999 / (FB33[1] * 1.001)) * 0.999 - 100e-9 * FB33[0],
       0.8224 * (1 + FB33[0] * 1.001 / (FB33[1] * 0.999)) * 1.001 + 100e-9 * FB33[0])
#   +3V3: TPS62130 SLVSAG7F p5 VFB 781.6-822.4 mV in power-save mode (785.6-814.4 PWM), FB leakage <= 100 nA, +/-0.1 %
#   line/load (as gen/ctrl_c2000.py supervisor()); control board: >= 3.20 V (its reference dropout)
SS33 = 470e-12                                 # TPS62130 SS/TR capacitor, C0G 5 %: sets the +3V3 ramp-up (F28003x SRVDDIO)
R_DIS33 = 1.2                                  # +3V3 discharge resistor (2512): sets the ramp-down when the rails fail
S75 = json.load(open(aux_hv.SPEC75_PATH))      # 75 W aux block (gen/aux_hv.py aux75_block), read at build time
V24 = tuple(S75["regulation"]["live_V"])       # live 24 V window of the aux block
assert gdrv.V5_RANGE[0] <= V5[0] and V5[1] <= gdrv.V5_RANGE[1], "+5V outside the range gdrv's bias numbers assume"
SUP_DIV = (107e3, 10.0e3)                      # +5V -> TPS3710 SENSE: RDY low below 4.52-4.69 V (AHCT VCC min 4.5 V)


DEC_N = spec("decoupling", "count_per_leg")
BANK_N = spec("capacitors", "port_A", "count")       # per phase and port (cell_spec)
BANK_XTRA = {3: {"B": 2}}                           # rev A2 (D-056): PV-P75's 7th battery-side capacitor, on phase 2


def bank_n(n_ph, t, p):
    """FCSA3DS456 count of port t ('A' / 'B') at phase p (module_spec costfirst port_film_bank sets the minimum)."""
    return BANK_N + (1 if BANK_XTRA.get(n_ph, {}).get(t) == p else 0)


def bank_total(n_ph, t):
    return sum(bank_n(n_ph, t, p) for p in range(1, n_ph + 1))
DAMP_R, DAMP_PAR, DAMP_SER = pvcell.DAMP_R, pvcell.DAMP_PAR, pvcell.DAMP_SER
DAMP_C = pvcell.DAMP_C                         # (value per element, elements in series)
HS_PAD = spec("heatsink_insulator")


def pwm(p, k):
    """PC contract: phase p, switch Sk -> PWM(4(p-1)+k)."""
    return "PWM%d" % (4 * (p - 1) + k)


def pos(p, s):
    """(drain, power source, complementary switch) of switch s in phase p: leg A = S1/S2 on port A, leg B = S3/S4."""
    a, b = "SW_A%d" % p, "SW_B%d" % p
    return {"S1": ("A_BUS+", a, "S2"), "S2": (a, "BUS-", "S1"), "S3": ("B_BUS+", b, "S4"), "S4": (b, "BUS-", "S3")}[s]


def tag(p, s):
    return "P%d%s" % (p, s)


def v5_of(p):
    """Gate-bias primary rail of phase p: phases 1-2 on +5V (with the logic), 3-4 on +5V_GD (second LMR38020)."""
    return "+5V" if p <= 2 else "+5V_GD"


def en_of(p):
    """SN6505B enable of phase p, buffered from the same rail as its VCC (SLLSEP9I abs max: EN <= VCC + 0.5 V)."""
    return "BIAS_EN5" if p <= 2 else "BIAS_EN5G"


# ------------------------------------------------------------------------------------------------ sheets
def sheet_pc(B, n_ph):
    """PC connector, command level shift, RDY / FLT_N, +5 V supervisor, ground star points."""
    B.new_sheet("01_pc", "PC connector, command buffers, status",
                "interfaces.PC (2x32) to the control board, 3.3 -> 5 V command buffers,\n"
                "RDY / FLT_N, +5 V supervisor, port status glue, BUS- star points")
    nc = {"NTC4", "NTC8"} | {pwm(4, k) for k in range(1, 5)} if n_ph == 3 else set()
    B.block("PC connector (interfaces.PC), live",
            "Both boards live: GND = AGND = BUS-. +3V3 goes TO the control board (rev A2: the former +24V / +5V pins carry\n"
            "the sensor references IL1R-IL4R). Commands are 3.3 V logic after the control board's trip latch; every command\n"
            "input is pulled low here (open = off).%s"
            % ("\nPV-P75: PWM13-16, NTC4 and NTC8 not connected; IL4 and IL4R pulled to AGND (0 V = no phase 4)."
               if n_ph == 3 else ""))
    pins = IF.pins(IF.PC, rename=PC_RENAME)
    B.part("J_PC", {k: (None if v in nc else v) for k, v in pins.items()})
    B.C("10u", "+3V3", "GND", pkg="1210", volt="16V")
    B.C("100n", "+3V3", "GND", volt="50V")
    B.block("Command inputs: pull-downs and 3.3 -> 5 V buffers (gdrv.input_buffers)",
            "NSI6651 inputs are specified at VCC1 = 5 V only (VINH <= 3.5 V, p7) and the SN6505B EN needs 0.7 VCC: one\n"
            "SN74AHCT1G08 (VIH 2.0 V) per line. Each PWM is ANDed with EN here as well, so a low EN removes every\n"
            "command within 8 ns, ahead of the drivers' RST/EN filter (0.48-0.8 us).")
    for net in ["EN", "BIAS_EN"] + [pwm(p, k) for p in range(1, n_ph + 1) for k in range(1, 5)]:
        B.R("10k", net, "GND", note="pull-down: open = off")
    pairs = [("EN", "EN5", None), ("BIAS_EN", "BIAS_EN5", None)] + \
            [(pwm(p, k), pwm(p, k) + "_5V", "EN") for p in range(1, n_ph + 1) for k in range(1, 5)]
    gdrv.input_buffers(B, pairs, v5="+5V", gnd="GND", part="AHCT1G08")
    if n_ph > 2:     # phases 3-4: SN6505B on +5V_GD, so their enable comes from a buffer on that rail
        gdrv.input_buffers(B, [("BIAS_EN", "BIAS_EN5G", None)], v5="+5V_GD", gnd="GND", part="AHCT1G08")
    B.R("10k", "EN5", "GND", note="pull-down: unpowered buffer = drivers disabled")
    B.block("RDY (wired-AND) and FLT_N (wired-OR)",
            "RDY: every NSI6651 RDY (VCC1 and VCC2 UVLO), the +5V and +5V_GD supervisors and the aux status (live 24 V\n"
            "UV), pulled up HERE to +3V3 (4.99k). FLT_N: every NSI6651 /FLT + port OC; pull-up on the control board.\n"
            "+5V supervisor TPS3710 %s/%s: RDY low below %.2f-%.2f V (AHCT VCC >= 4.5 V, bias headroom)."
            % (gdrv.ohm(SUP_DIV[0]), gdrv.ohm(SUP_DIV[1]), *sup_window()[0]))
    B.R("4.99k", "+3V3", "RDY", note="RDY pull-up (power board)")
    B.C("100p", "RDY", "GND", diel="C0G", tol="5%")
    B.C("100p", "FLT_N", "GND", diel="C0G", tol="5%")
    B.part("TPS3710", {"5": "+5V", "3": "SUP5_SNS", "1": "RDY", "2": "GND", "4": "GND", "6": "GND"})
    B.C("100n", "+5V", "GND")
    B.R(gdrv.ohm(SUP_DIV[0]), "+5V", "SUP5_SNS", tol="0.1%")
    B.R(gdrv.ohm(SUP_DIV[1]), "SUP5_SNS", "GND", tol="0.1%")
    B.C("1n", "SUP5_SNS", "GND")
    if n_ph == 3:
        B.R("10k", "IL4", "AGND", note="PV-P75 variant code: IL4 = 0 V (a fitted sensor reads >= 0.5 V)")
        B.R("10k", "IL4R", "AGND", note="PV-P75: no phase-4 sensor reference")
    B.block("Port status to PC: HOLD, MOV_OK, port over-current into FLT_N",
            "HOLD = A_HOLD OR B_HOLD (|I_port| >= 0.97-1.03 kA, contactor held). MOV_OK high only while BOTH varistor\n"
            "monitor loops conduct (each reads 'open' below ~200 V on its port). Port OC (|I| >= 387-413 A, lean_shunt)\n"
            "pulls FLT_N low (open drain): the control board's latch trips without firmware (aR-fuse gap, port_spec).\n"
            "K_A / K_B / K_PRE pulled low: open = coil off (the hardware interlocks follow on the port sheets).")
    B.part("LVC1G32", {"1": "A_HOLD", "2": "B_HOLD", "5": "+3V3", "4": "HOLD", "3": "GND"})
    B.C("100n", "+3V3", "GND")
    B.R("10k", "+3V3", "MOV_OK", note="high = both varistor monitor loops intact")
    for g, d in (("A_SPD_N", "MOV_OK"), ("B_SPD_N", "MOV_OK"), ("A_OC", "FLT_N"), ("B_OC", "FLT_N")):
        B.part("BSS138BK", {"1": g, "3": d, "2": "GND"})
    for net in ("K_A", "K_B", "K_PRE"):
        B.R("100k", net, "GND", note="open = coil off")
    B.block("Ground star points", "AGND meets GND at one point; GND meets BUS- at one point next to the port shunts\n"
                                  "(controller ground = BUS-). Layout: gate-drive primaries decoupled to GND locally.")
    B.part("NETTIE", {"1": "AGND", "2": "GND"})
    B.part("NETTIE", {"1": "GND", "2": "BUS-"})
    for net in ("+5V", "+3V3", "GND", "AGND"):          # +24V no longer on this sheet (rev A2: not on PC)
        B.flag(net)


# PC contract name -> this board's net: the port functions (gen/port.py lean_*) name their outputs <port>_<signal>
PC_RENAME = {"IA": "A_IM", "IA_H": "A_IH", "IB": "B_IM", "IB_H": "B_IH", "VA": "A_VB_O", "VAX": "A_VX_O", "VB": "B_VB_O",
             "VBX": "B_VX_O", "VPE": "IMD_AUX_O"}


def sheets_ports(B, n_ph, st):
    """Port A (PV) and port B (battery) with gen/port.py lean_port(), then terminals, CM rings, X/Y capacitors, board
    entries, PE links and the insulation monitor (lean_imd) on one sheet."""
    no = 2 + 3 * n_ph
    dom, xing = st["dom"], st["xing"]
    pa = port.lean_port(B, "A", "pv", no, "A_IN+", "A_IN-", "A_BUS+", "BUS-", "K_A", dom=dom, xing=xing)
    pb = port.lean_port(B, "B", "battery", no + 2, "B_IN+", "B_IN-", "B_BUS+", "BUS-", "K_B", cmd_pre="K_PRE", dom=dom,
                        xing=xing)
    st["ports"] = (pa, pb)
    rating = {3: 135, 4: 180}[n_ph]
    term = {135: "TERM200", 180: "TERM250"}[rating]
    B.new_sheet("%02d_terminals_imd" % (no + 4), "Terminals, EMI filter, insulation test",
                "DC terminals, nanocrystalline CM ring per port, X / Y capacitors, board\n"
                "entries (press-fit), PE links, heatsink, insulation monitor (port.lean_imd)")
    for t in ("A", "B"):
        B.block("Port %s terminals and EMI filter" % t,
                "%s: chassis terminals %s_TERM+/- (%d A port), both conductors once through the N-C-644025 ring (CM, 150 kHz-\n"
                "30 MHz, ARCHITECTURE 8.1), X 2.2 uF 1300 V at the varistor network, Y1 4.7 nF pole-PE_T (removable PE link\n"
                "for the hipot). Board taps %s_IN+%s by short links." % ({"A": "PV", "B": "battery"}[t], t, rating, t,
                                                                       "" if t == "A" else " / B_IN- / B_T+"))
        for pol in "+-":
            B.part(term, {"1": "%s_TERM%s" % (t, pol)}, value="DC terminal %s %d A (CUSTOM)" % (t + pol, rating))
        B.part("CM_RING", {"1": t + "_TERM+", "2": t + "_IN+", "3": t + "_TERM-", "4": t + "_IN-"})
        B.part("XCAP_2U2", {"1": t + "_IN+", "2": t + "_IN-"}, value="X 2.2u 1300V RFQ")
        for pol in "+-":
            xing.add(B.part("VY1_4N7", {"1": t + "_IN" + pol, "2": "PE_T"}).ref)
    B.block("Board entries (Wurth REDCUBE press-fit M3, 100 A each)",
            "Bus entries from the contactors / fuses: A_BUS+, B_BUS+, A_IN- and B_T- (into the shunts), two studs each;\n"
            "taps A_IN+, B_IN+, B_IN-, B_T+ (varistors, dividers, IMD, precharge, aux input), one each.")
    for net, k in (("A_BUS+", 2), ("B_BUS+", 2), ("A_IN-", 2), ("B_T-", 2), ("A_IN+", 1), ("B_IN+", 1), ("B_IN-", 1),
                   ("B_T+", 1)):
        for _ in range(k):
            B.part("STUD", {"1": net})
    B.block("PE: links, heatsink", "PE_T (Y capacitors, MOV3, IMD strings) reaches PE through a removable strap (open for the\n"
                                   "hipot, IC-23); PE_D = IMD divider bond; the heatsink is earthed at two points.")
    B.part("PE_LINK", {"1": "PE_T", "2": "PE"})
    B.part("PE_BOND", {"1": "PE_D"}, value="PE bond M4 (IMD divider reference)")
    B.part("PE_BOND", {"1": "PE"}, value="PE bond M4 (board PE)")
    B.part("HEATSINK", {"1": "PE"}, value="heatsink %d x TO-247 (CUSTOM)" % (8 * n_ph))
    B.block("Insulation monitor (port.lean_imd)", "IMD_SW1 = string A+ -> PE, IMD_SW2 = string PE -> BUS- (high = connected);\n"
                                                  "VPE = PE divider against BUS- (VMID of port A).")
    port.lean_imd(B, "A_IN+", "BUS-", "PE_T", "PE_D", pa["refs"], "IMD_SW1", "IMD_SW2", "+5V", "GND", "+3V3", "AGND", dom, xing)
    nets = B.D.nets()
    for c, n in PC_RENAME.items():
        assert n in nets, "PC %s: port function no longer drives %s" % (c, n)


def sheet_power(B, p, st):
    """Phase p: legs A/B (2 x SG2M040170HJ per switch on AlN pads), film decoupling + RC damper per leg, the phase's
    share of both port banks, inductor through the current sensor, NTC inputs, per-phase gate bias."""
    sa, sb, la = "SW_A%d" % p, "SW_B%d" % p, "L_A%d" % p
    B.new_sheet("%02d_ph%d_power" % (2 + 3 * (p - 1), p), "Phase %d power stage, bias, sensing" % p,
                "Legs A/B (2 x SG2M040170HJ per switch), decoupling, dampers, bank share,\n"
                "inductor + STK-HO/A 75, NTC inputs, gate bias (gdrv.bias_phase)")
    for leg, (top, bot), bus in (("A", ("S1", "S2"), "A_BUS+"), ("B", ("S3", "S4"), "B_BUS+")):
        B.block("Phase %d leg %s: %s (top) / %s (bottom), %d x SG2M040170HJ each" % (p, leg, top, bot, N_PAR),
                "Kelvin source (pin 3) of each device to its own driver COM star (gate-drive sheet). Each tab on its own\n"
                "%s pad to the EARTHED heatsink (basic HV-PE, B2). LAYOUT (IC-22): milled slot between drain pin 1 and\n"
                "pins 2-4; equal drain / source busbar paths for the two devices of a switch."
                % HS_PAD["material"].split(",")[0])
        for s in (top, bot):
            drain, source, _ = pos(p, s)
            for d in "ab"[:N_PAR]:
                st["xing"].add(B.part("SG2M040170HJ", {"1": drain, "5": drain, "2": source, "3": "KS_%s%s" % (tag(p, s), d),
                                                       "4": "G_%s%s" % (tag(p, s), d)}).ref)
                st["iso"].add(B.part("PAD_ALN", {"1": drain, "2": "PE"},
                                     value="PAD AlN %g mm (CUSTOM)" % HS_PAD["thickness_mm"]).ref)
        d1, d2, d3 = ("DMP_%s%d_%d" % (leg, p, i) for i in (1, 2, 3))
        B.block("Phase %d leg %s decoupling + RC damper" % (p, leg),
                "%d x FCSA3DS225 at the %s pins: commutation loop <= %.0f nH, bank <= %.0f nH from the leg (LAYOUT, leg deck).\n"
                "Damper (PVR-02): %d x (%d x %s CRCW2512-HP) + %d x %s 2 kV C0G in series, loop at the pins <= 3 nH."
                % (DEC_N, "/".join((top, bot)), (pvcell.LEG["Ldc"] + pvcell.LEG["Lloop"]) * 1e9, pvcell.L_BUS_REQ * 1e9,
                   DAMP_SER, DAMP_PAR, gdrv.ohm(DAMP_R), DAMP_C[1], gdrv.farad(DAMP_C[0])))
        for _ in range(DEC_N):
            B.part("FILM2U2", {"1": bus, "2": "BUS-"})
        B.C(gdrv.farad(DAMP_C[0]), bus, d1, pkg="2220", volt="2000V", diel="C0G", tol="5%")
        B.C(gdrv.farad(DAMP_C[0]), d1, d2, pkg="2220", volt="2000V", diel="C0G", tol="5%")
        for n1, n2 in ((d2, d3), (d3, "BUS-")):
            for _ in range(DAMP_PAR):
                B.part("R_DAMP", {"1": n1, "2": n2}, value=gdrv.ohm(DAMP_R))
    na, nb, n_ph = bank_n(st["n_ph"], "A", p), bank_n(st["n_ph"], "B", p), st["n_ph"]
    B.block("Phase %d share of the port banks" % p,
            "%d x FCSA3DS456 (45 uF) on A_BUS+ and %d on B_BUS+, at this phase's legs: the banks of all phases form the\n"
            "port A / port B DC links (%d / %d x 45 uF)%s. Bleeders: one string per port on the port sheets."
            % (na, nb, bank_total(n_ph, "A"), bank_total(n_ph, "B"),
               ";\nthe third on B_BUS+ is the battery side's seventh (D-056: full-power load rejection)" if nb > BANK_N else ""))
    for bus, k in (("A_BUS+", na), ("B_BUS+", nb)):
        for _ in range(k):
            B.part("FILM45", {"1": bus, "2": "BUS-"})
    B.block("Phase %d inductor (chassis, CUSTOM) and current sensor" % p,
            "Lead A from SW_A%d passes the STK-HO/A 75 U-primary (pins 6-9 in, 10-13 out: + = SW_A -> SW_B) to stud\n"
            "L_A%d; lead B on stud SW_B%d. IL%d = 2.5 V + 10.667 mV/A (Vref 2.48-2.52 V), 0.5-4.5 V over +/-187.5 A;\n"
            "100R + 1 nF at the source. IL%dR = the sensor's own Uref (pin 4), 100R + 1 nF: PV-CTL references its trip\n"
            "window to it. Primary at the switch node: functional barrier (4 kV rms, 8 kV impulse)." % (p, p, p, p, p))
    st["iso"].add(B.part("L_PV", {"1": la, "2": sb, "3": "NTC%d" % (4 + p), "4": "AGND", "5": "PE"}, value=MAG_ROW).ref)
    B.part("STUD", {"1": la})
    B.part("STUD", {"1": sb})
    B.part("STK75", dict({str(k): sa for k in range(6, 10)}, **{str(k): la for k in range(10, 14)},
                          **{"1": "+5V", "2": "AGND", "3": "IL%d_S" % p, "4": "IL%dR_S" % p, "5": None}))
    B.C("100n", "+5V", "AGND")
    for n in ("IL%d" % p, "IL%dR" % p):
        B.R("100R", n + "_S", n)
        B.C("1n", n, "AGND", diel="C0G", tol="5%")
    B.block("Phase %d NTC inputs (NTC to AGND, biased on the control board)" % p,
            "NTC%d = heatsink section of phase %d: lug probe on the EARTHED heatsink, >= 2.5 kV rms lead-to-lug (R-10).\n"
            "NTC%d = inductor winding NTC (functional to the winding). 100 nF at the header against switch-node noise."
            % (p, p, 4 + p))
    st["iso"].add(B.part("NTC_HS", {"1": "NTC%d" % p, "2": "AGND", "3": "PE"}, value="NTC 10k lug probe (CUSTOM)").ref)
    B.flag("PE")
    for n in ("NTC%d" % p, "NTC%d" % (4 + p)):
        B.part("J_NTC2", {"1": n, "2": "AGND"})
        B.C("100n", n, "AGND")
    chans =[(tag(p, s), "VDD_" + tag(p, s), "COM_" + tag(p, s), "VEE_" + tag(p, s)) for s in ("S1", "S2", "S3", "S4")]
    xf, sec = gdrv.bias_phase(B, "PH%d" % p, chans, gate_v=GATE_V, v5=v5_of(p), gnd="GND", bias_en=en_of(p))
    st["iso"] |= set(xf)
    for t, nets in sec.items():
        st["gd"].update({n: "GD_" + t for n in nets})


def sheet_drive(B, p, leg, st):
    """Phase p, one leg: two gdrv.channel() rev 7 with bias="ext" (rails from the phase's bias_phase)."""
    pair = ("S1", "S2") if leg == "A" else ("S3", "S4")
    B.new_sheet("%02d_ph%d_gd%s" % (3 + 3 * (p - 1) + (leg == "B"), p, leg.lower()),
                "Phase %d leg %s gate drive (%s, %s)" % ((p, leg) + pair),
                "gdrv.channel() rev 7, bias='ext': NSI6651ASC-Q1 +%g/-%g V, %g/%g ohm per device,\n"
                "Miller clamps, SC booster %gR, interlock + dead-time stretch, DESAT to the drain"
                % (GATE_V + R_GATE + (R_SB,)))
    for s in pair:
        drain, _, other = pos(p, s)
        t = tag(p, s)
        ch = gdrv.channel(B, t, pwm=pwm(p, int(s[1])) + "_5V", en="EN5", flt_n="FLT_N", rdy="RDY",
                          gate=tuple("G_%s%s" % (t, d) for d in "ab"[:N_PAR]),
                          source=tuple("KS_%s%s" % (t, d) for d in "ab"[:N_PAR]), drain=drain, vdd="VDD_" + t,
                          vee="VEE_" + t, interlock=pwm(p, int(other[1])) + "_5V", vin="+24V", vcc="+5V", gnd="GND",
                          gate_v=GATE_V, r_on=R_GATE[0], r_off=R_GATE[1], vclass=VCLASS, r_sb=R_SB, v_peak=V_PEAK,
                          deadtime=DT, bias="ext", neg_det=NEG_DET)
        st["gd"].update({n: "GD_" + t for n in ch.sec})
        st["iso"] |= set(ch.iso)
        st["xing"] |= set(ch.xing)
    ms = gdrv.MILLER_SIM["%d x %s" % (N_PAR, "SG2M040170HJ")]
    B.block("Leg %s: layout rules" % leg,
            "Gate and Kelvin of each device as coupled pairs of equal length (+/-10 %%) from the COM star; Ron, Roff, Kelvin\n"
            "R, 10k, Zeners at each device. Clamp FET + C_VL at its device's gate-Kelvin pins, loop <= %.0f nH. DESAT string\n"
            ">= 2.5 mm pad gap per diode; board coated to PD1 (IC-22). Miller hold (ngspice, gdrv): die %+.2f V vs VGS(th)\n"
            "min %.2f V at 175 C - BENCH TEST. Driver input side and bias primary decoupled to GND locally (CM current)."
            % (gdrv.L_CLAMP_MAX * 1e9, ms["die_l1"], gdrv.SC40["vth175"]))


def sup_window():
    """TPS3710 (SBVS271A p5: VIT- 387-400 mV, VIT+ 396-404 mV) on +5V through SUP_DIV, 0.1 %: (fall, rise) ranges."""
    r1, r2 = SUP_DIV
    k = lambda lo: (1 + r1 * (0.999 if lo else 1.001) / (r2 * (1.001 if lo else 0.999)))
    vf, vr = pvcell.TPS37["vit_f"], pvcell.TPS37["vit_r"]
    return (vf[0] * k(True), vf[1] * k(False)), (vr[0] * k(True), vr[1] * k(False))


def pending(B):
    """Guard: a PC net that no block drives gets a test point and is listed in the design check as PENDING (none today)."""
    nets = B.D.nets()
    todo = sorted(n for n, nodes in nets.items() if len([x for x in nodes if not x[0].startswith("#")]) == 1
                  and n in set(IF.PC) | set(PC_RENAME.values()))
    for n in todo:
        B.TP(n)
    return todo


# ------------------------------------------------------------------------------------------------ domains, ranges
def vranges(n_ph):
    """DC range of every assessed net against its own domain's reference (LIVE: BUS-; gate island: its COM star; PE: PE).
    gdrv.net_ranges() per channel (its logic side moved to +5V), the bias regulator nodes, then this board's nets; the
    port sheets fall back to port.vrange_lean(). Switching / gate / pulse nets stay unranged (design_check covers them)."""
    r = {}
    e = gdrv.ext_bias_numbers(GATE_V)
    for p in range(1, n_ph + 1):
        for s in ("S1", "S2", "S3", "S4"):
            t = tag(p, s)
            r.update(gdrv.net_ranges(t, GATE_V, "VDD_" + t, "VEE_" + t, tuple("KS_%s%s" % (t, d) for d in "ab"[:N_PAR]),
                                     gnd="GND", vcc="+5V", vin="+24V"))
            r.update({"%s_%s" % (x, t): (0.0, V5[1]) for x in ("DTS", "DTB", "PG")})
            b = lambda x: "%s_PH%d%s" % (x, p, t)
            r[b("RAW")] = (-e["v3"][1] + e["raw"][0], -e["v3"][0] + e["raw"][1])
            r.update({b(x): (-e["v3"][1] + gdrv.VREF431[0], -e["v3"][0] + gdrv.VREF431[1]) for x in ("RREF", "CREF")})
    r.update({"+24V": (V24[0], S75["nets"]["LIVE"][1]), "+5V": V5, "+5V_GD": V5, "+3V3": V33, "GND": (0.0, 0.0),
              "AGND": (0.0, 0.0), "BUS-": (0.0, 0.0), "PE": (0.0, 0.0), "SUP5_SNS": (0.0, V5[1] * SUP_DIV[1] / sum(SUP_DIV)), "A_BUS+": (0.0, port.V_POLE),
              "SUP5G_SNS": (0.0, V5[1] * SUP_DIV[1] / sum(SUP_DIV)), "FB_5V": (0.985, 1.015), "FB_5VG": (0.985, 1.015),
              "PG_5V": (0.0, V5[1]), "PG_5VG": (0.0, V5[1]), "RT_5V": (0.0, 1.5), "RT_5VG": (0.0, 1.5),
              "FB_3V3": (0.78, 0.83), "SS_3V3": (0.0, 1.3), "DIS_G": (0.0, 10.4),
              "B_BUS+": (0.0, port.V_POLE)})
    r.update({n: (0.0, V33[1]) for n in ["EN", "BIAS_EN", "RDY", "FLT_N", "HOLD", "MOV_OK", "K_A", "K_B", "K_PRE", "IMD_SW1",
                                         "IMD_SW2"] + ["PWM%d" % k for k in range(1, 17)] + ["NTC%d" % k for k in range(1, 9)]})
    r.update({n: (0.0, V5[1]) for n in ["EN5", "BIAS_EN5", "BIAS_EN5G"] + ["PWM%d_5V" % k for k in range(1, 17)]
              + ["IL%d" % k for k in range(1, 5)] + ["IL%dR" % k for k in range(1, 5)]})
    return r


def build_design(n_ph):
    B = L.Builder(PROJECT[n_ph], "%s power board" % PROJECT[n_ph], REV, DATE, CATALOG, rails=["+24V", "+5V", "+3V3"],
                  returns=["GND", "AGND", "PE"],
                  subtitle="%s: %d interleaved FSBB phases, gate drive, sensing, ports, aux (D-044)"
                           % ("PV-P75" if n_ph == 3 else "PV-P100/110", n_ph),
                  comment1="Live board referenced to BUS-; gate islands functional; SELV winding reinforced; HV-PE basic",
                  comment4="Not bench-validated. Values read from sim/out/*.json; checks: design_check()")
    st = dict(gd={}, iso=set(), xing=set(), dom={"PE_T": "PE", "PE_D": "PE"}, n_ph=n_ph)
    sheet_pc(B, n_ph)
    for p in range(1, n_ph + 1):
        sheet_power(B, p, st)
        sheet_drive(B, p, "A", st)
        sheet_drive(B, p, "B", st)
    sheets_ports(B, n_ph, st)
    sheets_supplies(B, n_ph, st)
    st["todo"] = pending(B)
    return B, st


def pin_numbers(entry):
    if "stock" in entry:
        import symlib
        return {q.number for q in symlib.symbol_pins(symlib.kicad_symbol(entry["stock"][0], entry["stock"][1], "x"))}
    return {x.split()[0] for u in (entry.get("units") or [entry["pins"]]) for side in u.values() if isinstance(side, list)
            for x in side if x}


def sheets_supplies(B, n_ph, st, no=None, taps=("A_IN+", "B_T+", "BUS-"), gd_what=None):
    """Live rails from the aux block's live 24 V: +5V and +5V_GD (LMR38020, 2 A each), +3V3 (TPS62130 from +5V_GD with a
    controlled ramp-up and a discharge stage), the +5V_GD supervisor and the SELV connector; then gen/aux_hv.py
    aux75_block (4 sheets) through aux_block()."""
    no = 2 + 3 * n_ph + 5 if no is None else no
    B.new_sheet("%02d_supplies" % no, "Live 5 V / 3.3 V, SELV connector",
                "+24V (aux live) -> +5V and +5V_GD (LMR38020), +3V3 (TPS62130 from +5V_GD,\n"
                "ramp-up and discharge), +5V_GD supervisor, SELV connector to the control board")
    for rail, t, what in (("+5V", "5V", "NSI6651 inputs, AHCT, sensors, references, PC +5V, gate bias of phases 1-2"),
                          ("+5V_GD", "5VG", gd_what or "gate bias of phases 3%s" % ("-4" if n_ph == 4 else " only"))):
        B.block("24 V -> %s (LMR38020, 400 kHz, 2 A)" % rail,
                "%s. ctrl_c2000 circuit (SNVSC40E table 9-1, 400 kHz / 5 V / 2 A): 15 uH, 3 x 22 uF, RT 64.9k,\n"
                "FB 100k / 24.9k 0.1 %% -> %.2f-%.2f V. Load in design_check (<= 80 %% of 2 A)." % (what, V5[0], V5[1]))
        B.part("LMR38020", {"3": "+24V", "2": "+24V", "4": "RT_" + t, "8": "SW_" + t, "7": "BOOT_" + t, "5": "FB_" + t,
                            "6": "PG_" + t, "1": "GND", "9": "GND"})
        B.C("4.7u", "+24V", "GND", pkg="1210", volt="50V")
        B.C("4.7u", "+24V", "GND", pkg="1210", volt="50V")
        B.C("100n", "+24V", "GND", volt="100V")
        B.R("64.9k", "RT_" + t, "GND")
        B.C("100n", "BOOT_" + t, "SW_" + t)
        B.part("L15U", {"1": "SW_" + t, "2": rail})
        for _ in range(3):
            B.C("22u", rail, "GND", pkg="1206", volt="16V", tol="10%")
        B.R(gdrv.ohm(FB5[0]), rail, "FB_" + t, tol="0.1%")
        B.R(gdrv.ohm(FB5[1]), "FB_" + t, "GND", tol="0.1%")
        B.R("100k", rail, "PG_" + t)
        B.TP("PG_" + t)
        B.flag(rail)
    B.block("+5V_GD supervisor on RDY", "TPS3710 %s/%s as on +5V: RDY low while the bias rail of phases 3%s is below "
            "4.52-4.69 V." % (gdrv.ohm(SUP_DIV[0]), gdrv.ohm(SUP_DIV[1]), "-4" if n_ph == 4 else ""))
    B.part("TPS3710", {"5": "+5V_GD", "3": "SUP5G_SNS", "1": "RDY", "2": "GND", "4": "GND", "6": "GND"})
    B.C("100n", "+5V_GD", "GND")
    B.R(gdrv.ohm(SUP_DIV[0]), "+5V_GD", "SUP5G_SNS", tol="0.1%")
    B.R(gdrv.ohm(SUP_DIV[1]), "SUP5G_SNS", "GND", tol="0.1%")
    B.C("1n", "SUP5G_SNS", "GND")
    B.block("+5V_GD -> +3V3 (TPS62130, 2.5 MHz) with a controlled ramp",
            "EN = PG_5VG. FB %s / %s 0.1 %% -> %.3f-%.3f V (>= 3.20 V for the control board's reference). SS/TR %s C0G:\n"
            "ramp-up 12-16 mV/us (F28003x VDDIO 8-100 mV/us). Ports' logic, RDY / MOV_OK pull-ups, PC +3V3 (control <= 157 mA)."
            % (gdrv.ohm(FB33[0]), gdrv.ohm(FB33[1]), V33[0], V33[1], gdrv.farad(SS33)))
    B.part("TPS62130", {"11": "+5V_GD", "12": "+5V_GD", "10": "+5V_GD", "13": "PG_5VG", "9": "SS_3V3", "7": "GND",
                        "8": "GND", "1": "SW_3V3", "2": "SW_3V3", "3": "SW_3V3", "14": "+3V3", "5": "FB_3V3", "4": None,
                        "6": "GND", "15": "GND", "16": "GND", "17": "GND"})
    B.C("10u", "+5V_GD", "GND", pkg="1210", volt="25V")
    B.C("100n", "+5V_GD", "GND")
    B.C(gdrv.farad(SS33), "SS_3V3", "GND", diel="C0G", tol="5%")
    B.part("L2U2", {"1": "SW_3V3", "2": "+3V3"})
    B.C("22u", "+3V3", "GND", pkg="0805", volt="10V", tol="10%")
    B.C("22u", "+3V3", "GND", pkg="0805", volt="10V", tol="10%")
    B.R(gdrv.ohm(FB33[0]), "+3V3", "FB_3V3", tol="0.1%")
    B.R(gdrv.ohm(FB33[1]), "FB_3V3", "GND", tol="0.1%")
    B.block("+3V3 discharge (ramp-down when the rails fail)",
            "PG_5VG low (+5V_GD < 90-94 %%, i.e. the live 24 V is gone) disables the TPS62130 and lets the 100k from +24V\n"
            "(10 V Zener) turn on PMV30ENEA + %s: +3V3 falls at >= 20 mV/us through the BOR region (F28003x SRVDDIO-DN)."
            % gdrv.ohm(R_DIS33))
    B.part("AO3400A", {"1": "DIS_G", "2": "GND", "3": "DIS_D"})
    B.R(gdrv.ohm(R_DIS33), "+3V3", "DIS_D", pkg="2512", note="+3V3 discharge, pulse 0.5 mJ")
    B.R("100k", "+24V", "DIS_G", note="discharge on unless PG_5VG holds it off")
    B.part("BZX84-B10", {"1": "GND", "2": None, "3": "DIS_G"})
    B.part("2N7002BK", {"1": "PG_5VG", "2": "GND", "3": "DIS_G"})
    B.block("SELV 24 V to the control board (J2)", "AUX-T1 reinforced winding SELV_24V / SELV_0V, cross-regulated %.1f-%.1f V "
            "(aux75_spec); full fan speed needs\n%.1f V. %s. Fan buck (12-36 V in) + SELV logic on the control board;\n"
            ">= 10 mm from live copper; nothing else of the SELV domain is on this board."
            % (S75["regulation"]["selv_V_all"][0], S75["regulation"]["selv_V_all"][1],
               S75["regulation"]["fan_full_speed_needs_V"], FAN_RULE))
    B.part("J_SELV", {"1": "SELV_24V", "2": "SELV_0V"})
    st["aux"] = aux_block(B, no + 1, taps)
    st["iso"] |= set(st["aux"]["isolators"])
    st["selv"] = set(st["aux"]["selv_nets"])


FAN_RULE = "Full fan speed is only guaranteed while the converter is switching"
AUX_SWAP = {}    # catalogue keys defined by this board AND aux_hv with a different entry: {key: (board MPN, aux MPN)}


def aux_block(B, sheet_no, taps=("A_IN+", "B_T+", "BUS-")):
    """gen/aux_hv.py aux75_block(). Keys that both catalogues define (aux_hv vs gdrv / port / pvcell, e.g. 'BAT54' =
    'BAT54' here and 'BAT54,215' there - the same Nexperia part with its packing suffix) are swapped to the aux entry for
    the call only, so the aux block's own MPN self-check holds, then restored. Checked: equal pin numbers, every shared
    key maps to the same MPN before and after the call, and every part drawn under a shared key carries exactly the
    board MPN (board parts) or the aux MPN (aux parts) - a silent part change cannot pass."""
    shared = sorted(k for k in S75["parts"] if k in B.catalog)
    before = {k: B.catalog[k]["mpn"] for k in shared}
    swap = {k: B.catalog[k] for k in shared if B.catalog[k] != aux_hv.CATALOG[k]}
    for k in swap:
        assert pin_numbers(swap[k]) == pin_numbers(aux_hv.CATALOG[k]), "catalogue key %s: aux and board symbols differ" % k
        B.catalog[k] = aux_hv.CATALOG[k]
    try:
        aux = aux_hv.aux75_block(B, a_tap=taps[0], b_tap=taps[1], bus_n=taps[2], live="+24V", selv_p="SELV_24V",
                                 selv_n="SELV_0V", status="RDY", prefix="AUX_", sheet_no=sheet_no)
    finally:
        B.catalog.update(swap)
    assert {k: B.catalog[k]["mpn"] for k in shared} == before, "a shared catalogue key changed its MPN across the aux call"
    mine = set(aux["refs"])
    for ref, p_ in B.D.parts.items():
        k = p_.lib_id.split(":")[1]
        if k in before:
            want = aux_hv.CATALOG[k]["mpn"] if ref in mine else before[k]
            assert B.bom[ref]["mpn"] == want, "%s (key %s) drawn as %s, expected %s" % (ref, k, B.bom[ref]["mpn"], want)
    AUX_SWAP.update({k: (before[k], aux_hv.CATALOG[k]["mpn"]) for k in swap})
    return aux


def domain_for(st):
    return lambda net: st["gd"].get(net) or st["dom"].get(net) or ("PE" if net == "PE" else "SELV" if
                                                                   net in st["selv"] else "LIVE")


# ------------------------------------------------------------------------------------------------ design check
# Datasheet values used below (document, page). "ASSUMED" marks a value that is not a datasheet or spec number.
STK = dict(ipn=75.0, ipm=187.5, g=10.667e-3, vref=(2.48, 2.52), voe=10e-3, voe_t=0.015, vfs=0.8, lin=0.005, x_t=0.03,
           t_res=0.2e-6, bw=1e6, icc=9e-3, vcc=(4.75, 5.25), noise_pp=25e-3, r_out=(15.0, 25.0), r_ref=(12.0, 20.0),
           ud=4000.0, uw=8000.0, creep=13.78)
#   Sinomags STK-HO/A Ver1.5 p5 (STK-HO/A 75): IPN 75 A, IPM +/-187.5 A, Vcc 4.75-5.25 V, Icc <= 9 mA, Vref 2.48-2.52 V,
#   V_FS 0.8 V at IPN, R_out 15-25 ohm, R_ref 12-20 ohm (Uref output), Voe +/-10 mV (Vout - Vref at 0 A),
#   Voe drift +/-1.5 % V_FS, G_th 10.667 mV/A, linearity +/-0.5 % IPN,
#   t_res 0.2 us, BW 1 MHz, noise 25 mVpp (DC-100 kHz), accuracy +/-3 % IPN -40..105 C; p3: 4 kV rms, 8 kV 1.2/50 us,
#   creepage = clearance 13.78 mm. STK-HO/A 130 (architecture's pick, p7): IPN 130 A, 6.154 mV/A, linearity 0.7 % IPN.
STK130 = dict(ipn=130.0, g=6.154e-3, lin=0.007)
F45 = dict(c=45e-6, v85=1100.0, v70=1300.0, irms85=22.1, ipk=900.0, esl=35e-9, esr=4.0e-3)   # Jianghai CBB138 DS p30
F22 = dict(c=2.2e-6, v85=1100.0, v70=1300.0, irms85=3.9, ipk=176.0, esl=25e-9, esr=22.5e-3, rth=33.0)   # p30 row 2.2 uF
VY1_VDC = 1500.0                                   # Vishay VY1 p1: Y1 500 VAC / 1500 VDC
IQ = dict(opa2388=2.6e-3, tlv3502=5e-3, ref3030=37e-6, lvc=10e-6, ahct=10e-6, ahct_d=1.5e-3, sn6505=2.3e-3)
#   OPA4388 SBOS777D IQ <= 2.6 mA per amplifier; TLV3502 SBOS321E IQ <= 5 mA (pvcell TLV); REF3030 <= 37 uA; LVC ICC
#   <= 10 uA; SN74AHCT1G08 SCLS315S ICC <= 10 uA + dICC <= 1.5 mA per input at 3.4 V; SN6505B SLLSEP9I I(VCC) <= 2.3 mA
COIL = dict(hfe82v=6.0, g7l=2.3)                   # W at 24 V: Hongfa HFE82V-300C p1 (no economiser); Omron G7L-X p2 DC24
NSI = gdrv.NSI
NSI_TYP = dict(icc1=1.6e-3, icc2=3.3e-3)           # NSI66x1A-Q1 p.6: ICC1 0.6 / 1.6 / 4 mA, ICC2 1 / 3.3 / 7 mA (min / typ / max)
T_IN, T_BOARD, ETA_5V = 45.0, pvcell.T_BOARD, 0.85   # inlet (PV-11/PV-20 rating point); board 85 C, 5 V buck ASSUMED
ETA_33 = 0.85                                      # TPS62130 5 -> 3.3 V at 0.1-0.4 A: ASSUMED (SLVSAG7F curves ~0.88-0.92)
R_SA = {3: 0.06, 4: 0.049}                         # K/W heatsink target incl. air rise (ARCHITECTURE section 9)
CTL_C33, CTL_C33A = (11.0e-6, 2.9e-6), 2.5e-6     # PV-CTL rev A0 netlist: +3V3 = 10u + 1u (large) + 29 x 100n; +3V3A 2.5 uF
CTL_IL_LOAD = 39.4e3                              # PV-CTL rev A1: load of each IL line (its x 0.7462 divider)
CTL_ILR_LOAD = 39.8e3                             # PV-CTL rev A1: load of each ILnR line (715R + 19.1k + 20.0k trip ladder)
CTRL_LOAD = {"+3V3": 0.20, "+5V": 0.0, "+24V": 0.0}   # A allocated to the control board (PV-CTL rev A0 draws <= 0.157 A on
#                                                    +3V3 and nothing from +5V / +24V)
ECON = port.S["lean"]["coil_economiser"]           # gen/port.py lean_contactor economiser (default on): hold / pull-in
SELV_LOGIC_W = 2.2                                 # control board SELV zone, allocation (D-075): PV-CTL 1.44 W stacked maximum with the
#                                                    Ethernet bridge not fitted, 2.13 W fitted (PCS-CTL); the aux block allocates the same 2.2 W
SELV_W = {3: 39.6 + SELV_LOGIC_W, 4: 56.1 + SELV_LOGIC_W}   # ARCHITECTURE section 3: fans at full speed (+10 % buck) + SELV logic
LABEL_MIN = {3: 10.0, 4: 15.0}                     # ARCHITECTURE 2.2 (4): enclosure label 'wait 10 / 15 min'


def aux_rating(n_ph):
    """The aux block's ratings for this variant (aux75_spec 'ratings': live_W, live_peak_W, selv_W, total_W)."""
    return next(v for k, v in S75["ratings"].items() if k.startswith("%d phases" % n_ph))


def selv_rows(n_ph):
    """aux75_spec selv_table_V: [(live 24 V load W, SELV minimum V)] with the fans at 100 %, for this build"""
    return sorted((float(re.search(r"live ([\d.]+) W", k).group(1)), v[0]) for k, v in S75["regulation"]["selv_table_V"].items()
                  if k.startswith("%d phases" % n_ph) and k.endswith("fans 100 %"))


def live_min_for_fans(n_ph):
    """Live 24 V load above which the SELV winding stays >= fan_full_speed_needs_V with the fans at 100 % (aux75_spec
    selv_table_V minima, linear between its live-load rows)."""
    need = S75["regulation"]["fan_full_speed_needs_V"]
    rows = selv_rows(n_ph)
    for (w1, v1), (w2, v2) in zip(rows, rows[1:]):
        if v1 < need <= v2:
            return w1 + (need - v1) * (w2 - w1) / (v2 - v1)
    return rows[0][0] if rows[0][1] >= need else float("inf")


def selv_min_at(n_ph, live_w):
    """SELV minimum (V) at a live 24 V load with the fans at 100 %: aux75_spec selv_table_V, linear between its rows"""
    rows = selv_rows(n_ph)
    return float(np.interp(live_w, [w for w, _ in rows], [v for _, v in rows]))


def fan_contract(n_ph):
    """PCM-26 - ONE contract for the fan supply. S_24V at the worst cross-regulation point (aux75_spec: fans at 100 %, the live load at
    which the SELV winding first guarantees fan_full_speed_needs_V), what the fan buck then passes (gen/pv_ctrl.py fan_ceiling: the
    TPS54360B in dropout with the drawn choke and catch diode, at the fans' current), the voltage the thermal model's fans run at
    (module_spec fan_speed_cap x 24 V) and the airflow ratio that follows (speed ~ voltage ASSUMED, as sim/pv_module.py). gen/pv_ctrl.py
    re-derives it from its own drawn parts and compares with the line this board prints"""
    import pv_ctrl as CTL
    var = "PV-P75" if n_ph == 3 else "PV-P100/110"
    m = MODS["modules"][var]
    s_min = S75["regulation"]["fan_full_speed_needs_V"]
    v_need = CTL.FAN_V_RATED * m["fan_speed_cap"]
    i = CTL.FAN_W[var] / v_need
    v_pass = CTL.fan_ceiling(s_min, i)
    ratio = min(1.0, v_pass / v_need)
    if ratio < 1.0 - FAN_FLOW_TOL:
        raise SystemExit("%s: the fan supply passes only %.2f V of the %.2f V the thermal model's fans run at (airflow x%.3f < x%.3f): "
                         "derate module_spec airflow and the heatsink temperature (sim/pv_module.py)" %
                         (var, v_pass, v_need, ratio, 1.0 - FAN_FLOW_TOL))
    return dict(var=var, s_min=s_min, v_need=v_need, i=i, v_pass=v_pass, ratio=ratio, flow_min=1.0 - FAN_FLOW_TOL,
                q0=m["airflow_m3h_full_45C"], exp=MODS["costfirst"]["heatsink"]["R_sa_flow_exponent"][var],
                v_rated=CTL.FAN_V_RATED, d=CTL.FAN_BUCK["d"], rds=CTL.FAN_BUCK["rds"], rdc=CTL.fan_r_dc(),
                vin_full=CTL.fan_vin_for(CTL.FAN_V_RATED, i))


def port_assumption(name):
    """one of the port design's assumptions (the table 'A_*' of sim/out/port_design/report.md); a missing one stops the build"""
    m = re.search(r"^\| %s \| ([0-9.e+-]+) \|" % name, open(OUT("port_design", "report.md")).read(), re.M)
    if not m:
        raise SystemExit("%s not found in sim/out/port_design/report.md: regenerate it with .venv/bin/python sim/port_design.py" % name)
    return float(m.group(1))


def rlc_peak(v, r, l, c):
    """peak current (A) of a series R-L-C closing onto an uncharged capacitor c from a stiff source v (the closed form of the port design)"""
    a, w0 = r / (2 * l), 1 / math.sqrt(l * c)
    if a < w0:                                   # under-damped: i = v / (wd l) e^(-a t) sin(wd t), peak where tan(wd t) = wd / a
        wd = math.sqrt(w0 * w0 - a * a)
        t = math.atan(wd / a) / wd
        return v / (wd * l) * math.exp(-a * t) * math.sin(wd * t)
    w = math.sqrt(max(a * a - w0 * w0, 1e-12))   # over-damped: i = v / (2 w l) (e^((-a + w) t) - e^((-a - w) t))
    t = math.log((a + w) / (a - w)) / (2 * w)
    return v / (2 * w * l) * (math.exp((-a + w) * t) - math.exp((-a - w) * t))


def port_a_declaration(n_ph):
    """PCM-16: what port A tolerates, from the drawn hardware (port_spec lean, the port design's assumptions): the admissible source type,
    external fusing, connection sequence, source capacitance and reverse-power permission, as one block of the design check. No hardware."""
    rating = {3: 135, 4: 180}[n_ph]
    ln = port.S["lean"]
    pv, ct, mk, hold = ln["pv_port"][str(rating)], ln["contactors"]["HFE82V"], ln["pv_make"][str(rating)], ln["contactor"]
    v_max = 1000.0
    c_a = bank_total(n_ph, "A") * F45["c"] + DEC_N * n_ph * F22["c"]                  # the bank behind K_A
    esr = F45["esr"] / bank_total(n_ph, "A")
    r_s, l_s = port_assumption("A_RS_BATT"), port_assumption("A_LS_BATT")             # a stiff source: 0.03 ohm, 3 uH incl. a short cable
    r_x, l_x = port_assumption("A_R_LOOP_X"), port_assumption("A_L_LOOP")             # the module's own loop to the bank
    i_stiff = rlc_peak(v_max, r_s + r_x + esr, l_s + l_x, c_a)
    i_dis, c_pe = port_assumption("A_I_MAKE_PV"), port_assumption("A_C_PV_PE" if n_ph == 3 else "A_C_PV_PE_110")
    l_arr = 10e-6 + l_x                                                               # port_design: a PV source behind 10 uH of cable
    c_src = l_arr * (i_dis / v_max) ** 2                                             # source capacitance whose discharge peaks at i_dis
    oc = ln["port_oc_trip_A"]
    return ("PORT A DECLARATION (PCM-16; calculated from port_spec lean and the port design's assumptions, ESTIMATES labelled; no hardware "
            "change). Port A admits: a current-limited PV array only - short-circuit current at the module terminals <= %.1f A (1.25 x the "
            "%d A port rating; K_A breaks %.0f A at 1000 V both polarities x %d operations: margin x%.2f, carries x%.2f), open-circuit "
            "voltage <= %.0f V (OV trip band 1039-1110 V), connected to the DC terminals through string fuses in the combiner (IEC 62548, "
            "ARCHITECTURE-COSTFIRST 6.3) and a load-break isolator, array and cable capacitance terminal to terminal <= %.1f uF behind "
            ">= %.0f uH (the 330 A discharge peak the making estimate assumes, back-calculated: ESTIMATE; the array's capacitance to PE "
            "is %.1f uF, common mode only); connection: K_A closes only with the latch clear, VAX above the polarity enable (%d-%d V "
            "band: a reversed array cannot be closed onto), the IMD result in range and, with port B live, after the converter has "
            "pre-charged the %.0f uF A bank to within 20 V (then the making current is the array's Isc), otherwise onto the EMPTY bank "
            "at the array's Voc: making current about %.0f A (Isc %.0f A + %.0f A discharge, ESTIMATE) against a published making "
            "rating of %.0f A at %.0f V only - covered: %s (Hongfa to confirm, R-04). Reverse power B -> A is NOT admitted with an "
            "array: there is no series diode, fuse or precharge on port A, the port over-current window (%d-%d A, both directions, "
            "FLT_N) protects the module and not the array, and an array driven backwards conducts through its cells and bypass diodes "
            "(limited only by the modules' reverse-current rating and the combiner's string fuses) - firmware holds the port A "
            "current at >= 0 while the port is declared PV (second layer behind the converter's own current limit). Port A does NOT "
            "admit: a stiff DC source (battery, DC bus, rectifier, another converter) - K_A would close onto the empty %.0f uF bank "
            "with a peak of about %.1f kA at %.0f V (%.2f ohm + %.1f uH source and loop, %.0f J stored: %.0fx the making rating's %.0f A, "
            "weld likely), a fault fed by it is cleared only by K_A (<= %.0f A normal opening, hold-off to %d-%d A, %.0f A once) with no "
            "fuse behind it, and nothing blocks reverse current; a stiff DC source on port A requires the PV-PORT-180 / lean port with "
            "fuse and precharge (gen/port.py lean_port as the battery port B: 2 x HPE501 250 A aR links, 220 ohm precharge, "
            "polarity and dV interlocks)" %
            (pv["isc"], rating, ct["break_1000V_both"], ct["n_break"], pv["brk_margin"], pv["carry"], v_max, c_src * 1e6, l_arr * 1e6,
             c_pe * 1e6, ln["polarity_enable_V"][0], ln["polarity_enable_V"][1], c_a * 1e6, mk["i_make_est"], pv["isc"], i_dis,
             ct["make_published"][0], ct["make_published"][1], "yes" if mk["covered"] else "NO", oc[0], oc[1], c_a * 1e6, i_stiff / 1e3,
             v_max, r_s + r_x + esr, (l_s + l_x) * 1e6, 0.5 * c_a * v_max ** 2, i_stiff / ct["make_published"][0],
             ct["make_published"][0], ct["break_1000V_both"], hold["hold_off_band_A"][0], hold["hold_off_band_A"][1],
             hold["break_once_published_A"]))


def leg_commutation():
    """PCM-18 (R-08 closed): the leg commutation with the capacitors AS DRAWN - cell_spec leg_commutation (sim/pv_design.py: the Jianghai
    FCSA3DS456 bank share and the FCSA3DS225 leg decoupling in the ngspice leg deck, at 1000 V / 1100 V and at the normal peak / the
    hardware-trip current) and the gate-source excursion of the held-off device at the same corners (sim/gdrv_miller.py). Returns the
    result text; the limits are asserted, a missing or stale record stops the build"""
    cd, lc, dc = spec("capacitors_drawn"), spec("leg_commutation"), spec("decoupling")
    for blk, f in ((cd["bank"], F45), (cd["decoupling"], F22)):        # the deck's capacitor data = the parts drawn here
        for k_cd, k_f in (("C", "c"), ("vop85", "v85"), ("vndc", "v70"), ("irms85", "irms85"), ("ipkr", "ipk"), ("esl", "esl"), ("esr", "esr")):
            assert abs(blk[k_cd] / f[k_f] - 1) < 1e-9, "cell_spec capacitors_drawn %s differs from the part drawn here (%s): re-run sim/pv_design.py" % (k_cd, k_f)
    assert cd["bank"]["count_per_phase_and_port"] == BANK_N and cd["decoupling"]["count_per_leg"] == DEC_N == dc["count_per_leg"], \
        "cell_spec capacitors_drawn counts differ from the drawing: re-run sim/pv_design.py"
    path = OUT("gdrv_miller", "result_primary.json")
    if not os.path.exists(path):
        raise SystemExit("%s is missing: generate it with .venv/bin/python sim/gdrv_miller.py" % os.path.relpath(path, L.REPO))
    gm = json.load(open(path))["primary"]
    if "corners" not in gm or gm["dev"] != "SG2M040170HJ":
        raise SystemExit("%s has no corner runs of the SG2M040170HJ: re-run sim/gdrv_miller.py" % os.path.relpath(path, L.REPO))
    near = lambda r, v, i: abs(r["V"] - v) < 1e-6 and abs(r["I_A"] - i) < 0.06
    worst_run = next(r for r in gm["runs"] if abs(r["l_clamp_nH"] - 1.0) < 1e-9)      # 1100 V at the hardware-trip current, 1 nH loop
    gs = []
    for c in lc["corners"]:
        if c["V"] == lc["corners"][-1]["V"] and c["case"] == "hardware trip":          # the worst-device corner = the miller study's own run
            gs.append((worst_run["nfet"]["pin_pk"], worst_run["nfet"]["die_pk"], worst_run["v_pk"]))
            continue
        m = [r for r in gm["corners"] if near(r, c["V"], c["I_A"])]
        if len(m) != 1:
            raise SystemExit("%s has no run at %.0f V / %.1f A (cell_spec leg_commutation corner): re-run sim/gdrv_miller.py" %
                             (os.path.relpath(path, L.REPO), c["V"], c["I_A"]))
        gs.append((m[0]["pin_pk"], m[0]["die_pk"], m[0]["device_peak_V"]))
    for c, g in zip(lc["corners"], gs):                                                 # the same leg deck behind both files
        assert abs(c["device_peak_V"] - g[2]) < 1.0, "gdrv_miller and cell_spec leg decks disagree (stale): re-run sim/pv_design.py and sim/gdrv_miller.py"
    v_lim, vth = lc["device_peak_limit_V"], gm["vth_175_min"]
    die_max = max(g[1] for g in gs)
    assert max(c["device_peak_V"] for c in lc["corners"]) <= v_lim, "leg commutation: device peak above 0.85 x V_DSS"
    assert die_max <= vth - 0.5 + 1e-9, "held-off device: gate-source excursion above V_GS(th) min less the 0.5 V design margin"
    net, cs = lc["network"], lc["corners"]
    return ("Leg commutation with the capacitors AS DRAWN (PCM-18, R-08 closed; cell_spec leg_commutation, ngspice VDMOS decks "
            "sim/spice/pv_dpt_leg_c*.cir, calculated): bank share %d x FCSA3DS456 per phase (%.0f nH / %.1f mOhm each, Jianghai CBB138 "
            "p.30 values, the earlier deck ran the KEMET 19 / 24 nH) -> %.0f nH bus -> %d x FCSA3DS225 (%.1f nH / %.1f mOhm per leg) -> "
            "%.1f nH board + devices -> RC damper; device V_DS peak %s against %.0f V = 0.85 x 1700 V; gate-source of the held-off "
            "device (gdrv clamp network, 1 nH loop, %.1f V bias, sim/gdrv_miller.py): pin %.2f..%.2f V, die %+.2f..%+.2f V against "
            "V_GS(th) min %.2f V at 175 C (margin %.2f V, design margin 0.5 V); decoupling capacitor current per FCSA3DS225: peak "
            "%.1f-%.1f A over the corners (%.1f A at the worst on-time, %.1f A with the Q_rr surrogate) against Ipkr %.0f A, rms "
            "%.2f-%.2f A (%.2f A at the worst on-time) against %.1f A at 85 C / %.1f A at 70 C" %
            (cd["bank"]["count_per_phase_and_port"], cd["bank"]["esl"] * 1e9, cd["bank"]["esr"] * 1e3, net["bus_nH"],
             cd["decoupling"]["count_per_leg"], net["decoupling_ESL_nH"], net["decoupling_ESR_mOhm"], net["board_and_devices_nH"],
             "; ".join("%.0f V (%.0f V / %.1f A %s)" % (c["device_peak_V"], c["V"], c["I_A"], c["case"]) for c in cs), v_lim,
             gm["runs"][0]["voff"], min(g[0] for g in gs), max(g[0] for g in gs), min(g[1] for g in gs), die_max, vth, vth - die_max,
             min(c["decoupling_peak_per_cap_A"] for c in cs), max(c["decoupling_peak_per_cap_A"] for c in cs),
             dc["peak_current_per_cap_A"], dc["peak_current_per_cap_with_recovery_A"], cd["decoupling"]["ipkr"],
             min(c["decoupling_rms_per_cap_A"] for c in cs), max(c["decoupling_rms_per_cap_A"] for c in cs), dc["rms_per_cap_A"],
             cd["decoupling"]["irms85"], cd["decoupling"]["irms"]))


def stamp():
    """the 'Built from' line of the design check: sha256 (first 16 hex) of every spec file this board's numbers were read from"""
    return "Built from (sha256, first 16 hex): " + "; ".join(
        "%s %s" % (f, hashlib.sha256(open(os.path.join(L.REPO, f), "rb").read()).hexdigest()[:16]) for f in STAMP_FILES)


def island_idle_in_w():
    """Upper bound of one island's 5 V input power with the bias on and the gates NOT switching: resistor currents at the
    highest rails (COM bias 4.7k -1 %, both dividers, VCG 10k, gate pull-downs 10k, negative-rail detector 100k) plus the
    NSI6651 ICC2 maximum, drawn through the linear regulator from the highest raw voltage, transformer 90 %."""
    e = gdrv.ext_bias_numbers(GATE_V)
    dv = gdrv.EXT_DIV[GATE_V]
    i = (e["von"][1] / (4.7e3 * 0.99) + e["vt"][1] / (sum(dv["vdd"]) * 0.999) + e["v3"][1] / (sum(dv["vee"]) * 0.999)
         + (e["von"][1] - 3.26) / (10e3 * 0.99) + N_PAR * e["v3"][1] / (10e3 * 0.99) + NSI["icc2"]
         + (e["vt"][1] / 100e3 if NEG_DET else 0.0))
    return e["raw"][1] * i / 0.9


def island_resistor_w():
    """Deterministic resistor power of one gate island at its lowest rails (COM bias 4.7k, both 0.1 % dividers, VCG
    supply 10k, gate pull-downs 10k): the floor of the bias load with the gates off."""
    e = gdrv.ext_bias_numbers(GATE_V)
    gv = gdrv.GATE_V[GATE_V]
    i = (e["von"][0] / 4.7e3 + e["vt"][0] / sum(gv["fbvdd"]) + e["v3"][0] / sum(gv["fbvee"])
         + (e["von"][0] - 3.34) / 10e3 + N_PAR * e["v3"][0] / 10e3)
    return e["vt"][0] * i


def thermal_inputs():
    """R_th chain and the per-phase device losses of the full-load table of sim/out/pv_design/report.md (cell_spec does
    not carry them): (R_jc, R_cs, R_spread, worst device loss per phase over the full-load points)."""
    txt = open(OUT("pv_design", "report.md")).read()

    def f(pat):
        m = re.search(pat, txt)
        if not m:
            raise SystemExit("sim/out/pv_design/report.md: %r not found - re-run sim/pv_design.py" % pat)
        return float(m.group(1))
    rows = re.findall(r"^\| (\d+)->(\d+) \| ([\d.]+) \| \w+ \| [\d.]+ \| [\d.]+ \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \|", txt,
                      re.M)
    if len(rows) < 8:
        raise SystemExit("sim/out/pv_design/report.md: the full-load table is not found - re-run sim/pv_design.py")
    return (f(r"R_th,jc ([\d.]+) K/W"), f(r"case-sink ([\d.]+) K/W"), f(r"spreading ([\d.]+) K/W"),
            max(float(c) + float(s_) + float(d) for *_, c, s_, d in rows))


def design_check(B, n_ph, st):
    """Every number behind this board, asserted against the spec files and the data sheets. Returns ({key: line}, N)."""
    out, N = {}, {}
    say = lambda k, f, *a: out.__setitem__(k, f % a)
    parts = list(B.D.parts.values())
    key = lambda p_: p_.lib_id.split(":")[1]
    cnt = lambda k: sum(1 for p_ in parts if key(p_) == k)

    # ---- power devices against the cell_spec stresses (per phase = one former cell)
    dev, w = pvcell.DEV, spec("worst_case_stresses")
    assert w["device_VDS_peak_V"] <= 0.85 * dev["vdss"] and w["device_VDS_continuous_max_V"] <= 0.67 * dev["vdss"]
    assert w["device_Irms_per_device_A"] <= dev["id100"] and w["device_I_turnoff_max_per_device_A"] <= dev["idm"]
    assert cnt("SG2M040170HJ") == 8 * n_ph and cnt("PAD_ALN") == 8 * n_ph, "device / pad count"
    say("dev", "Devices %d x SG2M040170HJ (2 per switch): V_DS peak %.0f V = %.2f x 1700 V (<= 0.85; the larger of the lumped 20 nH loop deck, "
        "%.0f V, which has no capacitor model, and the physical-leg deck with the capacitors as drawn, %.0f V: see the leg commutation line), "
        "continuous %.0f V = %.2f x (<= 0.67); %.1f A rms per device vs I_D(100 C) %.0f A; turn-off %.1f A vs IDM %.0f A (cell_spec)",
        8 * n_ph, w["device_VDS_peak_V"], w["device_VDS_peak_V"] / dev["vdss"], w["device_VDS_peak_lumped_deck_V"],
        w["device_VDS_peak_physical_leg_V"], w["device_VDS_continuous_max_V"],
        w["device_VDS_continuous_max_V"] / dev["vdss"], w["device_Irms_per_device_A"], dev["id100"],
        w["device_I_turnoff_max_per_device_A"], dev["idm"])

    # ---- thermal: one earthed heatsink for every device (ARCHITECTURE 9), losses from report.md / cell_spec. The fans run on the voltage the
    # fan supply contract guarantees (fan_contract): a lower voltage slows them, the airflow follows their speed, R_sa follows the airflow
    rjc, rcs, rsp, p_ph = thermal_inputs()
    fc = fan_contract(n_ph)
    p_dev = spec("worst_case_stresses", "device_loss_max_per_position_W") / N_PAR
    dt_js = p_dev * (rjc + rcs + rsp)
    rsa = R_SA[n_ph] * fc["ratio"] ** -fc["exp"]
    t_hs = T_IN + rsa * n_ph * p_ph
    tj = t_hs + dt_js
    trip_ok = 125.0 - dt_js - 5.0
    flow = fc["q0"]
    assert tj <= 125.0 and HS_PAD["Rth_cs_K_W"] == rcs, "Tj above the 125 C design limit on the shared heatsink"
    N.update(tj=tj, t_hs=t_hs, trip_ok=trip_ok)
    say("thermal", "Thermal (shared heatsink, R_sa <= %.3f K/W incl. air rise at ~%.0f m3/h, module_spec; fan supply contract below: the "
        "fan buck passes %.2f V at the worst supply point against the %.2f V the thermal model's fans run at = airflow x%.3f = %.0f "
        "m3/h, R_sa x%.4f (flow exponent %.2f of the module_spec heatsink model) = %.4f K/W, %s): devices %.1f W per phase (report.md full-load table) "
        "x %d = %.0f W -> sink %.1f C at %.0f C inlet; hottest device %.1f W x (R_jc %.2f + AlN %.3f + spread %.2f K/W) = %.1f K -> Tj "
        "%.1f C (<= 125 C design limit, %.1f K of it unused; per-cell heatsinks gave %.0f C). FINDING for the control board: the "
        "heatsink OT trip must be <= %.1f C (was %.0f C) to keep the cell_spec 5 K margin",
        R_SA[n_ph], flow, fc["v_pass"], fc["v_need"], fc["ratio"], flow * fc["ratio"], fc["ratio"] ** -fc["exp"], fc["exp"], rsa,
        ("inside the thermal margin: the airflow reduction is %.1f %% against the %.0f %% allowed and the sink moves by %.1f K (%.1f K "
         "even if R_sa followed 1 / airflow)" % (100 * (1 - fc["ratio"]), 100 * FAN_FLOW_TOL, t_hs - (T_IN + R_SA[n_ph] * n_ph * p_ph),
                                                 (1 / fc["ratio"] - 1) * R_SA[n_ph] * n_ph * p_ph) if fc["ratio"] < 1.0 else
         "no reduction: the fans' model voltage is below what the fan buck passes"),
        p_ph, n_ph, n_ph * p_ph, t_hs, T_IN, p_dev, rjc, rcs, rsp, dt_js, tj, 125.0 - tj,
        spec("worst_case_stresses", "Tj_max_45C_C"), trip_ok, spec("protection", "heatsink_overtemperature_trip_C"))

    # ---- inductor (read at build time) against the electrical requirement of cell_spec
    req, el, th = spec("inductor", "requirement"), MAG["electrical"], MAG["thermal"]
    assert el["L_at_45A_min_H"] * 1e6 >= req["L_at_45A_uH_min"] - 0.05 and el["L_trip_over_L0"] >= req["L_at_trip_fraction_of_L0_min"]
    assert th["T_hotspot_C"] <= th["T_hotspot_max_C"] and cnt("L_PV") == n_ph, "inductor hot spot / count"
    loss = max(r["P_total_W"] for r in MAG["loss_table"])                 # worst point of the file on disk
    p_rated = 1e3 * MODS["modules"]["PV-P75" if n_ph == 3 else "PV-P100/110"]["P_rated_kW"]
    d_eta = n_ph * (loss - req["loss_allowed_W"]) / p_rated
    usd = MAG["cost"]["total_usd"]
    N.update(ind_loss=loss, ind_deta=d_eta, ind_usd=usd)
    say("ind", "Inductor rev %s x %d (chassis, %s): L(45 A) min %.1f uH >= %.1f, L(trip)/L0 %.2f >= %.2f; hot spot %.0f C <= "
        "%.0f C at %.0f C local air. Worst-point loss %.1f W vs the %.1f W cell budget: %+.0f W per module = %+.3f %% points "
        "of efficiency at rated power (worst point, ESTIMATE); cost %.1f USD each (estimate in the file) vs 80 USD in the "
        "architecture = %+.0f USD per module", MAG["revision"], n_ph, MAG["construction"][:70], el["L_at_45A_min_H"] * 1e6,
        req["L_at_45A_uH_min"], el["L_trip_over_L0"], req["L_at_trip_fraction_of_L0_min"], th["T_hotspot_C"],
        th["T_hotspot_max_C"], th["T_air_local_C"], loss, req["loss_allowed_W"], n_ph * (loss - req["loss_allowed_W"]),
        -100 * d_eta, usd, n_ph * (usd - 80.0))

    # ---- capacitors: banks, decoupling, damper, X / Y
    vmax, c_a = w["port_cap_V_max_V"], spec("capacitors", "port_A")
    assert vmax <= F45["v85"] and port.V_POLE <= 1.15 * F45["v85"], "bank voltage vs 1100 V (85 C) / IEC 61071 1.15 x UN"
    i_cap = c_a["ripple_worst_Arms"] / BANK_N                            # per-phase worst; the fewest per phase = BANK_N
    assert i_cap <= 0.7 * F45["irms85"], "bank ripple per capacitor above 70 % of Imax (85 C)"
    fb = MODS["costfirst"]["modules"]["PV-P75" if n_ph == 3 else "PV-P100/110"]["port_film_bank"]
    n_bank = {t: bank_total(n_ph, t) for t in "AB"}
    assert all(n_bank[t] >= fb[t]["parts_min"] for t in "AB"), "port film bank below module_spec costfirst parts_min"
    dc = spec("decoupling")
    assert dc["peak_current_per_cap_with_recovery_A"] <= F22["ipk"] and dc["rms_per_cap_A"] <= F22["irms85"]
    assert F22["esl"] / DEC_N <= 8.5e-9 + 0.5e-9 and vmax <= F22["v85"], "decoupling ESL / voltage"
    p_dec = dc["rms_per_cap_A"] ** 2 * F22["esr"]
    say("caps", "Port banks (rev A2): A %d / B %d x FCSA3DS456 45 uF (%s per phase; %.0f / %.0f uF) >= module_spec costfirst "
        "minimum %d / %d (A: %s; B: %s, load rejection %.1f uF); %.0f V max vs 1100 V at 85 C hot spot "
        "(OV overshoot %.0f V <= 1.15 x 1100 V, IEC 61071); ripple %.1f A rms per capacitor (per-phase worst, no interleaving "
        "credit) = %.0f %% of Imax 22.1 A (85 C). Leg decoupling %d x FCSA3DS225: peak %.1f A (with recovery) vs 176 A, "
        "%.2f A rms vs 3.9 A (85 C, x%.2f), %.2f W per capacitor (%.1f K at 33 K/W); ESL %.1f nH per leg vs the 8.5 nH basis "
        "(the leg deck now runs these parts: next line)",
        n_bank["A"], n_bank["B"], "/".join("%d+%d" % (bank_n(n_ph, "A", q), bank_n(n_ph, "B", q)) for q in range(1, n_ph + 1)),
        n_bank["A"] * F45["c"] * 1e6, n_bank["B"] * F45["c"] * 1e6, fb["A"]["parts_min"], fb["B"]["parts_min"],
        fb["A"]["binding"], fb["B"]["binding"], fb["B"]["need_uF"]["load_rejection"], vmax, port.V_POLE, i_cap,
        100 * i_cap / F45["irms85"], DEC_N,
        dc["peak_current_per_cap_with_recovery_A"], dc["rms_per_cap_A"], F22["irms85"] / dc["rms_per_cap_A"], p_dec,
        p_dec * F22["rth"], F22["esl"] / DEC_N * 1e9)
    say("legcomm", "%s", leg_commutation())
    dmp = spec("damper")
    rq = dmp["required_resistor_rating"]
    p_part = pvcell.CRCW["p70"] * (pvcell.CRCW["t_max"] - T_BOARD) / (pvcell.CRCW["t_max"] - 70.0)
    assert DAMP_SER * DAMP_PAR * p_part >= rq["continuous_W_per_leg_min"] and pvcell.CRCW["umax"] >= dmp["peak_voltage_per_resistor_V"]
    assert dmp["capacitor_voltage_per_element_V"] <= 0.5 * 2000.0 and abs(DAMP_SER * DAMP_R / DAMP_PAR - dmp["R_per_leg_ohm"]) < 0.1
    say("damp", "Damper per leg: %d x (%d x %s CRCW2512-HP) = %.2f ohm + 2 x 4.7 nF 2 kV C0G; %.1f W rated at %.0f C board vs "
        ">= %.1f W required (%.2f W at the trip corner); %.0f V per element vs 500 V; C0G %.0f V per element (<= 50 %%)",
        DAMP_SER, DAMP_PAR, gdrv.ohm(DAMP_R), DAMP_SER * DAMP_R / DAMP_PAR, DAMP_SER * DAMP_PAR * p_part, T_BOARD,
        rq["continuous_W_per_leg_min"], dmp["power_per_leg_W_at_trip_1100V"], dmp["peak_voltage_per_resistor_V"],
        dmp["capacitor_voltage_per_element_V"])
    assert port.V_POLE <= 0.8 * VY1_VDC, "Y1 capacitor DC rating"
    say("xy", "X 2.2 uF / 1300 V per port (RFQ, impulse >= 4.5 kV, port.py XCAP_2U2); Y1 4.7 nF to PE_T: %.0f V pole-PE vs "
        "1500 VDC (VY1 p1) = %.0f %%; MOV3 / Y / IMD strings return through the removable PE_T link", port.V_POLE,
        100 * port.V_POLE / VY1_VDC)

    # ---- gate drive (gdrv rev 8 asserts its own rails, windows, DESAT, booster, Miller hold, negative-rail detector)
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        g_out, _ = gdrv.design_check()
    e = gdrv.ext_bias_numbers(GATE_V)
    on_w = gdrv.VGS_WIN[GATE_V]                              # gdrv rev 8 (cell_spec rail_range_basis: +/-0.5 V on)
    off_w = (-GATE_V[1] - 0.3, -GATE_V[1] + 0.3)               # cell_spec rail_range_basis: +/-0.3 V off
    assert e["von"][0] - on_w[0] >= 0.1 and on_w[1] - e["von"][1] >= 0.1, "VGS(on) band not inside gdrv's window by 0.1 V"
    assert off_w[0] <= -e["v3"][1] and -e["v3"][0] <= off_w[1], "VGS(off) band outside cell_spec's +/-0.3 V"
    (tr_lo, tr_hi), _, (bl_lo, bl_hi) = gdrv.desat_numbers(gdrv.DESAT_CLASS[VCLASS])
    v_on = spec("gate_drive", "requirements", "per_device", "primary", "vds_on_at_trip_175C_V")
    assert tr_lo >= 1.5 * v_on and NSI["cmti"] >= 1.2 * spec("device_primary", "turn_on_dvdt_V_per_ns")
    N["von"] = e["von"]
    say("gate", "Gate drive (gdrv.channel lean, dead-time stretch on, negative-rail detector %s): %d channels + %d x bias_phase (T_BIAS4 1:%g, high-side "
        "islands on the outer windings): VGS on %.2f-%.2f V in %.1f-%.1f V (margins %.2f / %.2f V), off -%.2f..-%.2f V in "
        "%.1f..%.1f V; DESAT trips at V_DS %.2f-%.2f V vs %.2f V on-state at the trip current, 175 C (x%.1f), blanking "
        "%.0f-%.0f ns; CMTI 150 V/ns vs %.0f V/ns (x%.2f, THIN); gdrv: %s", "fitted" if NEG_DET else "not fitted",
        4 * n_ph, n_ph, gdrv.XF_N, e["von"][0],
        e["von"][1], on_w[0], on_w[1], e["von"][0] - on_w[0], on_w[1] - e["von"][1], e["v3"][0], e["v3"][1], off_w[0],
        off_w[1], tr_lo, tr_hi, v_on, tr_lo / v_on, bl_lo * 1e9, bl_hi * 1e9, spec("device_primary", "turn_on_dvdt_V_per_ns"),
        NSI["cmti"] / spec("device_primary", "turn_on_dvdt_V_per_ns"), g_out["ext%g/%g" % GATE_V].split("): ", 1)[-1][:330])

    # ---- dead time at this board's +5V (pvcell.stretch5 evaluated at V5; AHCT1G08 per line)
    v5_old, pvcell.V5 = pvcell.V5, V5
    try:
        t_lo, t_hi = pvcell.stretch5(*DT)
    finally:
        pvcell.V5 = v5_old
    skew = NSI["skew"] + pvcell.AHCT["tpd"][1] - pvcell.AHCT["tpd"][0]
    dt = ((t_lo + pvcell.LVC17_5V["tpd"][0] - skew) * 1e9, (t_hi + pvcell.LVC17_5V["tpd"][1] + skew) * 1e9)
    win = spec("switching", "dead_time_at_gates_ns")
    assert win[0] <= dt[0] and dt[1] <= win[1], "hardware dead time outside cell_spec's window at +5V %.2f-%.2f V" % V5
    N["dt"] = dt
    say("dt", "Dead time at the gates: stretch %s/100p C0G on the SN74LVC1G17 at +5V %.2f-%.2f V = %.0f-%.0f ns, NSI6651 skew "
        "%.0f ns, AHCT1G08 spread %.0f ns -> %.0f-%.0f ns inside cell_spec %d-%d ns; firmware dead time %d ns is never "
        "extended", gdrv.ohm(DT[0]), V5[0], V5[1], t_lo * 1e9, t_hi * 1e9, NSI["skew"] * 1e9,
        (pvcell.AHCT["tpd"][1] - pvcell.AHCT["tpd"][0]) * 1e9, dt[0], dt[1], win[0], win[1],
        spec("switching", "dead_time_firmware_ns"))

    # ---- inductor-current sensor chain against control_spec
    cc = CTRL["measurement_requirements"]["cell_inductor_current"]
    oc = CTRL["hardware_trips"]["inductor_overcurrent"]
    i_sat, i_pk = spec("inductor", "I_at_50pct_L0_A"), oc["ctrl_backup"]["peak_A"]
    rc_ = (STK["r_out"][1] + 100.0) * 1e-9
    t_s = STK["t_res"] + rc_
    bw = 1 / math.sqrt(1 / STK["bw"] ** 2 + (2 * math.pi * rc_) ** 2)
    assert STK["ipm"] >= max(max(abs(x) for x in cc["range_A"]), oc["ctrl_backup"]["band_A"][1], i_pk, i_sat)
    assert bw >= cc["bandwidth_kHz_min"] * 1e3 and t_s <= cc["group_delay_us_max"] * 1e-6
    assert t_s <= 0.1 * oc["local"]["max_response_us"] * 1e-6, "sensor uses > 10 % of the local OC response budget"
    i_rms = spec("inductor", "I_rms_max_A")
    assert i_rms <= STK["ipn"] and STK["vcc"][0] <= V5[0] and V5[1] <= STK["vcc"][1], "sensor current / supply"
    i45 = spec("protection", "current_limit_avg_A")
    drift = STK["x_t"] - STK["voe_t"] - STK["lin"]          # gain drift bound over temperature after the idle re-zero
    err = (STK["lin"] * STK["ipn"] + drift * i45) / i45
    err130 = (STK130["lin"] * STK130["ipn"] + (STK["x_t"] - STK["voe_t"] - STK130["lin"]) * i45) / i45
    assert err <= 0.02, "sensor residual error at 45 A above the +/-2 % sharing figure (ARCHITECTURE 5)"
    acc = cc["accuracy_after_cal"]       # the control study's sensor accuracy after calibration: never tighter than the drawn sensor
    assert acc["gain_pct"] >= drift * 100 - 1e-6 and acc["offset_A"] >= STK["lin"] * STK["ipn"] - 0.01, \
        "control_spec cell_inductor_current accuracy_after_cal is tighter than the sensor as drawn (gain drift %.2f %%, linearity %.2f A)" % (
            drift * 100, STK["lin"] * STK["ipn"])
    zero = (STK["vref"][1] - 2.5 + STK["voe"] + STK["voe_t"] * STK["vfs"]) / STK["g"]
    zero_r = (STK["voe"] + STK["voe_t"] * STK["vfs"]) / STK["g"]              # against the sensor's own Uref (ILnR)
    noise = STK["noise_pp"] / STK["g"]
    N.update(t_s=t_s, err=err, zero=zero)
    say("isense", "Inductor current: STK-HO/A 75 per phase (architecture: HO/A 130), %.3f mV/A around Vref 2.48-2.52 V; linear "
        "+/-%.1f A >= range %g A, backup band top %.1f A, backup peak %.1f A, L = 50 %% L0 at %.0f A; %.0f A rms vs IPN %.0f A; "
        "delay 0.2 us + RC %.2f us = %.2f us vs <= %.2f us (CMPSS backup) and %.2f us (local OC); BW %.0f kHz >= %.0f kHz. "
        "After 2-point calibration + idle re-zero: linearity %.2f A + gain drift <= %.1f %% (3 %% - 1.5 %% offset - 0.5 %% "
        "linearity, not separated in the data sheet) = %.2f %% at %.0f A <= 2 %% sharing (HO/A 130: %.2f %%); control_spec "
        "(sharing study) takes gain %.2f %% and offset %.2f A after the calibration - the drift bound and the linearity above, "
        "asserted not tighter than the sensor as drawn. Uncalibrated zero +/-%.1f A against a fixed reference (Vref + Voe + drift), +/-%.1f A "
        "against the sensor's own Uref (Voe + drift): the fixed-threshold OC window of PV-CTL rev A1 is set from ILnR. "
        "Noise 25 mVpp = %.1f A pp per sample (R-07). Primary at the switch node: 4 kV rms, "
        "8 kV impulse, 13.8 mm; PD and dv/dt immunity not stated (R-07, bench)", STK["g"] * 1e3, STK["ipm"],
        max(cc["range_A"]), oc["ctrl_backup"]["band_A"][1], i_pk, i_sat, i_rms, STK["ipn"], rc_ * 1e6, t_s * 1e6,
        cc["group_delay_us_max"], oc["local"]["max_response_us"], bw / 1e3, cc["bandwidth_kHz_min"],
        STK["lin"] * STK["ipn"], drift * 100, err * 100, i45, err130 * 100, cc["accuracy_after_cal"]["gain_pct"],
        cc["accuracy_after_cal"]["offset_A"], zero, zero_r, noise)

    # ---- port current paths (gen/port.py lean_shunt) for the PC levels and the port OC trip
    r_sh = port.S["lean"]["shunt"]["R_ohm"]
    ref = (3.0 * 0.998, 3.0 * 1.002)                       # REF3030E 0.2 % (REF3030 p1)
    k_mid = (14.7 + 1.65) / (11.8 + 1.65 + 1.65 + 14.7)
    vmid = (ref[0] * k_mid * 0.999, ref[1] * k_mid * 1.001)
    swing = 0.05                                             # OPA2388 output swing to the rail at <= 1 mA: ASSUMED 50 mV
    lin_im = min((vmid[0] - swing) / (30 * r_sh), (V33[0] - swing - vmid[1]) / (30 * r_sh))
    lin_ih = min((vmid[0] - swing) / (10 * r_sh), (V33[0] - swing - vmid[1]) / (10 * r_sh))
    oc_band = port.S["lean"]["port_oc_trip_A"]
    assert lin_im >= 450.0 and lin_ih >= port.S["lean"]["contactor"]["hold_off_band_A"][1], "port current paths saturate"
    N.update(vmid=vmid, lin_im=lin_im, lin_ih=lin_ih)
    say("iport", "Port currents (lean_shunt, 100 uOhm in BUS-): IA/IB = VMID + 3.0 mV/A (G 30), linear +/-%.0f A at +3V3 min; "
        "IA_H/IB_H = VMID + 1.0 mV/A (G 10), +/-%.0f A; VMID %.3f-%.3f V per port (REF3030E ladder, not on PC: zero at "
        "idle). Port OC %d-%d A: on-board comparators on the IH path (lean_shunt) -> A_OC/B_OC -> open drain on FLT_N (no "
        "PC change); firmware duplicates it on IA (400 A = VMID -/+ 1.20 V, inside the %.0f A linear range)", lin_im,
        lin_ih, vmid[0], vmid[1], oc_band[0], oc_band[1], lin_im)

    # ---- bleeders (gen/port.py lean_bleeder) and discharge time
    bl = port.S["lean"]["bleeder"]
    r_bl = bl["n"] * bl["R_elem_ohm"]
    r_div = 6 * 1e6 + 4.99e3
    tmin = []
    for bus, bank in (("A_BUS+", "A"), ("B_BUS+", "B")):
        c = sum(F45["c"] for p_ in parts if key(p_) == "FILM45" and p_.pins["1"] == bus) + \
            sum(F22["c"] for p_ in parts if key(p_) == "FILM2U2" and p_.pins["1"] == bus)
        r = 1 / (1 / r_bl + 1 / r_div)
        r_wc = 1 / (1 / (r_bl * 1.01) + 1 / r_div)                # R +1 %, C +10 % (K), from the OV overshoot pole voltage
        tmin.append((bank, c, r * c * math.log(V_OVP / 60.0) / 60.0, r_bl * c * math.log(V_OVP / 60.0) / 60.0,
                     r_wc * c * 1.1 * math.log(port.V_POLE / 60.0) / 60.0))
    v_el = port.V_POLE / bl["n"]
    p_el = v_el ** 2 / bl["R_elem_ohm"]
    p_lim = 0.5 * (155.0 - T_BOARD) / (155.0 - 70.0)          # 1210 thick film 0.5 W at 70 C, linear to 155 C (R_PKG)
    assert all(t[3] <= LABEL_MIN[n_ph] and t[4] <= LABEL_MIN[n_ph] for t in tmin) and v_el <= 200.0 and p_el <= p_lim, \
        "bleeder"
    say("bleed", "Bleeders (port.lean_bleeder): %d x %s 1210 per port = %s; %s; element %.0f V at %.0f V (200 V working) and "
        "%.3f W vs %.2f W derated to %.0f C (1000 V: %.3f W); label 'wait %.0f min'", bl["n"], gdrv.ohm(bl["R_elem_ohm"]),
        gdrv.ohm(r_bl), "; ".join("port %s %.1f uF incl. decoupling: 1100 -> 60 V in %.1f min (bleeder alone %.1f min, with "
                                   "the 6 M divider %.1f); worst case (R +1 %%, C +10 %%, divider, from %.0f V) %.1f min"
                                   % (b, c * 1e6, t1, t1, t0, port.V_POLE, t2) for b, c, t0, t1, t2 in tmin),
        v_el, port.V_POLE, p_el, p_lim, T_BOARD, (1000.0 / bl["n"]) ** 2 / bl["R_elem_ohm"], LABEL_MIN[n_ph])

    say("portA", "%s", port_a_declaration(n_ph))

    # ---- supply budgets: +5V / +5V_GD (LMR38020 2 A), +3V3 (TPS62130 3 A, from +5V_GD), live +24V against the aux block
    n_buf, n_ch = 2 + 4 * n_ph, 4 * n_ph                      # AHCT1G08 on +5V (the +5V_GD one is in i5g)
    i_ph = e["p_in"] / V5[0]                                   # gdrv: bias input power per phase at 5.25 V raw, worst
    on5 = [p for p in range(1, n_ph + 1) if v5_of(p) == "+5V"]
    i5 = {"gate bias of phases %s (%.2f W each)" % (",".join(map(str, on5)), e["p_in"]): len(on5) * i_ph,
          "SN6505B": len(on5) * IQ["sn6505"], "NSI6651 VCC1": n_ch * NSI["icc1"],
          "AHCT1G08 (2 inputs at 3.3 V)": n_buf * (IQ["ahct"] + 2 * IQ["ahct_d"]),
          "5 V pull-downs (commands high)": (n_ch + 1 + n_ph) * V5[1] / 10e3, "STK-HO/A 75": n_ph * STK["icc"],
          "REF3030E + ladders": 2 * (IQ["ref3030"] + 3.0 / 29.8e3 + 3.0 / 30.2e3),
          "IMD SSR LEDs (test)": 2 * (V5[1] - 1.2) / 330.0, "control board (allocation)": CTRL_LOAD["+5V"]}
    i33 = {"OPA2388 x 7": 14 * IQ["opa2388"], "TLV3502 x 7": 14 * IQ["tlv3502"], "LVC gates": 12 * IQ["lvc"],
           "pull-ups (RDY, MOV_OK, SPD_N)": V33[1] / 4.99e3 + V33[1] / 10e3 + 2 * V33[1] / 220e3,
           "control board (allocation)": CTRL_LOAD["+3V3"]}
    s33 = sum(i33.values())
    i5g = {"gate bias of phases %s" % ",".join(str(p) for p in range(3, n_ph + 1)): (n_ph - 2) * (i_ph + IQ["sn6505"]),
           "AHCT1G08 (BIAS_EN5G)": IQ["ahct"] + IQ["ahct_d"], "pull-downs": (n_ph - 2) * V5[1] / 10e3,
           "TPS62130 -> +3V3 (eta %.2f ASSUMED)" % ETA_33: s33 * V33[1] / (ETA_33 * V5[0])}
    s5, s5g = sum(i5.values()), sum(i5g.values())
    assert s5 <= 0.8 * 2.0 and s5g <= 0.8 * 2.0 and s33 <= 0.8 * 3.0, "5 V / 3.3 V converter load above 80 %"
    say("supply5", "+5V (LMR38020 #1, 2 A): %.2f A = %.0f %% (%s); +5V_GD (LMR38020 #2): %.2f A = %.0f %% (%s); +3V3 (TPS62130, "
        "3 A): %.0f mA = %.0f %% (%s). Datasheet maxima; gate bias %.2f W per phase from gdrv ext_bias_numbers (worst "
        "raw at 5.25 V, T_BIAS4 1:%g)", s5, 50 * s5, ", ".join("%s %.0f mA" % (k, v * 1e3) for k, v in i5.items()), s5g,
        50 * s5g, ", ".join("%s %.0f mA" % (k, v * 1e3) for k, v in i5g.items()), s33 * 1e3, 100 * s33 / 3.0,
        ", ".join("%s %.0f mA" % (k, v * 1e3) for k, v in i33.items()), e["p_in"], gdrv.XF_N)
    # converter OUTPUT power: gdrv's bias input power is already a power (at its worst raw); the rest is current x V5 max
    bias_i = n_ph * i_ph
    p5_out = n_ph * e["p_in"] + (s5 + s5g - bias_i) * V5[1]
    p_imd = i5["IMD SSR LEDs (test)"] * V5[1] / ETA_5V           # IMD runs with K_A open only (ARCHITECTURE 5, 6.2-1)
    p_bucks = p5_out / ETA_5V
    rt = aux_rating(n_ph)
    r_c, r_h = 24.0 ** 2 / ECON["pull_in_W_at_24V"], ECON["R_hold_ohm"]   # HFE82V coil 96 ohm (24 V, 6 W, p1)
    cold = 0.85 * (1 - 0.00393 * 63.0)        # coil R -15 % (tolerance ASSUMED: none in the data sheet) at -40 C copper
    pull_max = V24[1] ** 2 / (r_c * cold)                      # one coil at full voltage for 100-152 ms
    g7l_max = V24[1] * 0.0958 * 1.15 / (1 - 0.00393 * 63.0)    # G7L-2A-X DC24 95.8 mA +/-15 % (p2) at -40 C
    p_live = p_bucks - p_imd + 2 * ECON["hold_W_max"]          # converter switching: both contactors held
    p_peak = p_bucks + ECON["hold_W_max"] + pull_max           # (a) K_A pulls in at the end of the A-bank precharge from
    #                                                            port B (converter may still switch), K_B held
    p_idle = p_bucks - n_ph * (e["p_in"] - 4 * island_idle_in_w()) / ETA_5V
    p_peak_b = p_idle + ECON["hold_W_max"] + pull_max + g7l_max  # (b) K_B pulls in at the end of the battery precharge:
    #                                                            precharge relay still on, K_A held, gates not switching
    p_aux = p_live + SELV_W[n_ph]
    hold = S75["holdup"]
    t_hold = hold["c_uF"] * 1e-6 * (V24[0] ** 2 - hold["v_end"] ** 2) / (2 * p_live) * 1e3
    # ---- PCM-14: what the live budget sums, bottom-up from the data sheets, and the same budget with typical values. The summed
    # figure is the stack of the maxima (gate bias from the P_CH allocation at the highest 5 V rail, every 5 V load at its data-sheet
    # maximum, both coils at their maximum hold power); the rating is the aux block's per-winding figure, so the reserve is printed
    # for the stack AND for the typical case
    vt_, v3_, von_, dvx = e["vt"], e["v3"], e["von"], gdrv.EXT_DIV[GATE_V]
    q_g = spec("gate_drive", "Q_g_per_device_nC") * 1e-9                 # SG2M040170HJ Q_g at its data-sheet swing (typical only)
    c_gs_ext = sum(L.number(p_.value.split()[0]) for p_ in parts if key(p_) == "C" and
                   any(str(x).startswith("G_P") for x in p_.pins.values()) and any(str(x).startswith("KS_P") for x in p_.pins.values()))
    c_gs_gate = c_gs_ext / (n_ch * N_PAR)                                # external gate-source capacitor per gate (the PCS preset fits 1 nF)

    def ch_a(typ):
        """one gate-drive channel's load from its VDD-VEE rail (A), bottom-up from the data sheets; typ = typical figures, rails at nominal"""
        vt, v3, von = tuple(sum(x) / 2 for x in (vt_, v3_, von_)) if typ else (vt_[1], v3_[0], von_[1])
        return {"gate charge": (q_g * N_PAR + c_gs_gate * N_PAR * vt) * pvcell.F_SW,
                "NSI6651 ICC2": (NSI_TYP if typ else NSI)["icc2"], "DESAT charge current": NSI["ichg"][1 if typ else 2],
                "10k OUTH-gate": N_PAR * von / 10e3, "dividers": vt / sum(dvx["vdd"]) + v3 / sum(dvx["vee"]),
                "COM bias 4.7k": (vt - v3) / 4.7e3}

    def bias_w(i_ch, typ):
        """5 V input power of one phase's gate bias for a channel load i_ch (gdrv.ext_bias_numbers' formula: raw rail at full load)"""
        v5hi, v5lo = (sum(V5) / 2,) * 2 if typ else gdrv.V5_RANGE[::-1]
        vf = sum(gdrv.VF_BRIDGE) / 2 if typ else gdrv.VF_BRIDGE[1]
        i_pri = 4 * 30.0 * i_ch / v5lo / 0.9
        return 4 * (gdrv.BIAS_XF["n"] * (v5hi - i_pri * gdrv.BIAS_XF["r_on"]) - 2 * vf) * i_ch / 0.9
    ch_max, ch_typ = ch_a(False), ch_a(True)
    i_max, i_typ = sum(ch_max.values()), sum(ch_typ.values())
    assert abs(bias_w(e["i_ld"], False) / e["p_in"] - 1) < 1e-9, "bias power formula no longer equals gdrv.ext_bias_numbers"
    assert i_max <= e["i_ld"], "gate-bias allocation (gdrv P_CH) below the bottom-up data-sheet maximum of a channel"
    p_bias_max, p_bias_typ = bias_w(i_max, False), bias_w(i_typ, True)
    d_typ = (2 * (ECON["hold_W_max"] - ECON["hold_W_at_24V"]) + (n_ph * (e["p_in"] - p_bias_typ) +
                                                               n_ch * (NSI["icc1"] - NSI_TYP["icc1"]) * V5[1]) / ETA_5V)
    p_live_typ = p_live - d_typ
    N.update(i5=s5, i5g=s5g, i33=s33, p_live=p_live, p_aux=p_aux, t_hold=t_hold, live_w=rt["live_W"])
    say("live24", "Live 24 V budget (maxima): 5 V / 3.3 V converters %.1f W (5 V output %.1f W incl. %.2f W at 3.3 V, eta %.2f "
        "ASSUMED; IMD LEDs %.2f W not counted while switching: the IMD runs with K_A open) + 2 contactors held on the "
        "economiser at %.2f W max each (port_spec lean/coil_economiser; was 2 x 6 W) = %.1f W <= aux live rating %.1f W "
        "(aux75_spec ratings, x%.3f); SELV fans + logic %.1f W <= %.1f W; total %.1f W <= %.1f W. Pull-in (%.1f W max: cold "
        "coil -15 %% ASSUMED at -40 C, 100-152 ms): (a) K_A pulls in, K_B held, converter switching = %.1f W; (b) K_B pulls "
        "in at the end of the battery precharge with the precharge relay still on (%.1f W max) and K_A held, gates not "
        "switching (port B open; bias idle %.2f W per phase) = %.1f W; both <= live peak %.1f W (reserve %.1f W = %.1f %% in the "
        "tighter case a). Hold-up of the aux's %.0f uF at %.1f W: %.0f ms to %.0f V", p_bucks, p5_out, s33 * V33[1], ETA_5V, p_imd,
        ECON["hold_W_max"], p_live, rt["live_W"], rt["live_W"] / p_live, SELV_W[n_ph], rt["selv_W"], p_aux, rt["total_W"], pull_max,
        p_peak, g7l_max, 4 * island_idle_in_w(), p_peak_b, rt["live_peak_W"], rt["live_peak_W"] - max(p_peak, p_peak_b),
        100 * (rt["live_peak_W"] - max(p_peak, p_peak_b)) / rt["live_peak_W"], hold["c_uF"], p_live, t_hold, hold["v_end"])
    say("live24typ", "Live 24 V reserve, honestly (PCM-14): the %.1f W above is a STACK of maxima - %.0f %% of it is gate bias (%d "
        "channels x allocation %.1f mA from VDD-VEE = %.2f W per phase at the highest 5 V rail), the rest every 5 V / 3.3 V load at its "
        "maximum and both coils at %.2f W. Gate bias bottom-up from the data sheets per channel: %s = %.1f mA at the maxima (allocation "
        "%.1f mA covers it x%.2f; Q_g %.0f nC is the data sheet's typical at its 22 V swing - the real swing is %.1f-%.1f V; external "
        "gate-source capacitor %s) and %.1f mA at the typical figures (ICC2 %.1f mA typ, ICC1 %.1f mA typ, NSI66x1A-Q1 p.6; rails at "
        "nominal) -> %.2f W per phase (maxima %.2f W, allocation %.2f W). Same budget with the typical gate bias, ICC1 and the coils at "
        "their typical hold power (%.2f W each; every other load still at its maximum) = %.1f W = %.0f %% of the %.1f W rating, "
        "reserve %.1f W against %.1f W for the stack. Pull-in peaks stay stacked maxima (cold coil): %.1f W of the %.1f W live peak. "
        "Per-winding capability is the aux block's own rating (aux75_spec rev %s, one design for every module: live %.1f W continuous, "
        "%.1f W for 1 s; SELV %.1f W for this build), not the converter's %.1f W total: this board needs no change of the winding "
        "allocation (the live peak must hold %.0f ms of the 1 s rating)", p_live, 100 * n_ph * e["p_in"] / ETA_5V / p_live, n_ch,
        e["i_ld"] * 1e3, e["p_in"], ECON["hold_W_max"], ", ".join("%s %.1f" % (k, v * 1e3) for k, v in ch_max.items()), i_max * 1e3,
        e["i_ld"] * 1e3, e["i_ld"] / i_max, q_g * 1e9, vt_[0], vt_[1],
        "%.0f pF each (%.0f pF fitted)" % (c_gs_gate * 1e12, c_gs_ext * 1e12) if c_gs_ext else "none fitted on this board (gdrv c_gs=None; "
        "the PCS preset fits 1 nF per gate)", i_typ * 1e3, NSI_TYP["icc2"] * 1e3, NSI_TYP["icc1"] * 1e3, p_bias_typ, p_bias_max,
        e["p_in"], ECON["hold_W_at_24V"], p_live_typ, 100 * p_live_typ / rt["live_W"], rt["live_W"], rt["live_W"] - p_live_typ,
        rt["live_W"] - p_live, max(p_peak, p_peak_b), rt["live_peak_W"], S75["rev"], rt["live_W"], rt["live_peak_W"], rt["selv_W"],
        rt["total_W"], ECON["pull_in_window_s"][1] * 1e3)
    assert p_live <= rt["live_W"] and SELV_W[n_ph] <= rt["selv_W"] and p_aux <= rt["total_W"], \
        "live / SELV / total load above the aux block's ratings (aux75_spec)"
    assert max(p_peak, p_peak_b) <= rt["live_peak_W"], "contactor pull-in peak above the aux live peak rating"
    nets = B.D.nets()
    assert all(t + "_K_LO" in nets for t in "AB"), "lean_contactor drawn without its economiser"
    # pull-in at the hottest coil from the directly regulated live 24 V; behaviour in the hold-up sag
    t70, v70, v21, t21 = 70.0, ECON["pull_in_supply_needed_V_at_70C"], 21.0, ECON["pull_in_guaranteed_to_C_at_21V"]
    t_max = t70 + (V24[0] - v70) * (t70 - t21) / (v70 - v21)   # linear in the port engineer's two points
    assert V24[0] >= 1.03 * v70, "contactor pull-in at a 70 C coil not guaranteed from the live 24 V minimum"
    r_hot = r_c * 1.15 * (1 + 0.00393 * (t70 - 23.0))
    v_feed_2v = 2.0 * (r_hot + r_h) / r_hot
    # contactor retry rule (PCM-14): every pull-in attempt costs e_pull from the live winding; a firmware rule bounds the attempts so that
    # repeated attempts cannot average above the reserve the stacked maxima leave on the live rating
    e_pull = pull_max * ECON["pull_in_window_s"][1]
    reserve = rt["live_W"] - p_live
    t_retry = max(10.0, e_pull / reserve)
    pre = port.S["precharge"]
    say("pullin", "Contactor pull-in from the aux's directly regulated live 24 V (%.2f-%.2f V, not the 21.0 V the port "
        "engineer checked): needs %.2f V at a 70 C coil (port_spec) -> x%.3f; guaranteed up to a %.0f C coil at %.2f V "
        "(linear through the port_spec points 21.0 V / %.1f C and %.2f V / 70 C). Cold-coil case of the budget: coil 96 ohm x 0.85 "
        "(-15 %% ASSUMED, no tolerance in the data sheet) x copper at -40 C (PV-20 asks -30 C: %.1f W) = %.1f ohm at %.2f V = %.1f W for "
        "%.0f-%.0f ms. Hold-up sag after both ports are lost: RDY falls at the aux UV (21.0-21.8 V) and the gates stop; a closed "
        "contactor needs only its hold current - on the economiser the coil sees V_feed x R_c / (R_c + %.1f ohm), so a hot coil (%.0f "
        "ohm) reaches the 2 V release guarantee only at a %.1f V feed (the real holding level is typical-only in the data sheet); a "
        "feed that recovers re-arms the full-voltage pull-in (any fall resets the economiser). FIRMWARE RULE (retry): a pull-in that "
        "does not close (the terminal / bank voltage does not follow within 0.5 s, ASSUMED) is retried at most %d times per contactor "
        "per close command and at least %.0f s apart - one attempt takes %.1f J from the live winding, %.2f W averaged over that "
        "interval against the %.2f W the stacked maxima leave on the live rating - then the contactor stays open for %.0f s and the "
        "fault is latched (the precharge lock-out of port_spec)", V24[0], V24[1], v70, V24[0] / v70, t_max, V24[0], t21, v70,
        V24[1] ** 2 / (r_c * 0.85 * (1 - 0.00393 * 53.0)), r_c * cold, V24[1], pull_max, ECON["pull_in_window_s"][0] * 1e3,
        ECON["pull_in_window_s"][1] * 1e3, r_h, r_hot, v_feed_2v, pre["max_attempts"], t_retry, e_pull, e_pull / t_retry, reserve,
        pre["lockout_s"])
    assert e_pull / t_retry <= reserve + 1e-9, "retry interval too short for the live reserve"
    # live load while switching against the SELV cross-regulation (aux75_spec selv_table_V, fans at 100 %)
    e_typ = gdrv.ext_bias_numbers(GATE_V)
    q_sw = spec("gate_drive", "Q_g_per_device_nC") * 1e-9 * N_PAR * pvcell.F_SW * e_typ["vt"][0]   # per channel
    isl = 4 * n_ph * (island_resistor_w() + q_sw) * e_typ["raw"][0] / e_typ["vt"][1]
    hold_min = V24[0] ** 2 / (r_hot + r_h * 1.01)
    p_low = (isl + n_ph * 5e-3 * V5[0]) / 0.95 + 2 * hold_min
    w_fan = live_min_for_fans(n_ph)
    assert p_low >= w_fan, "live load while switching below what the SELV needs for full fan speed"
    N.update(p_low=p_low, w_fan=w_fan)
    say("fans", "%s (rule, printed on the supplies sheet). The SELV winding gives >= %.1f V at 100 %% fans (full speed) once "
        "the live 24 V carries >= %.1f W (aux75_spec selv_table_V, interpolated). While switching the live load is >= %.1f W "
        "(lower estimate: island resistor currents + gate charge at typical Q_g and the lowest rails, sensors at Icc min, eta "
        "0.95, both coils held hot at %.2f W) and <= %.1f W (maxima) -> x%.2f; at that lowest live load the SELV minimum is %.2f V. "
        "Gates off: SELV %.1f-%.1f V (fan buck 12-36 V passes what it gets; run-on cooling only). The A0 minimum-load stage (13 x 1.3k, "
        "6.2 W) is deleted. Fan supply contract: S_24V >= %.2f V at the worst cross-regulation point, fan buck passes >= %.2f V at %.2f "
        "A, fans need %.2f V (airflow x%.3f, limit x%.3f) - the worst cross-regulation point is the live load %.1f W (fans 100 %%) "
        "where aux75_spec first guarantees that SELV level; the fan buck (TPS54360B, gen/pv_ctrl.py fan_ceiling) runs in dropout: D "
        "%.2f, R_DS(on) %.2f ohm, choke + trace %.0f mOhm, SS56, so it passes S_24V less %.2f V at that current (24.00 V itself would "
        "need S_24V >= %.2f V; the earlier 25.6 V of the control board was set point + 1.5 V, ASSUMED); the fans' speed follows their "
        "voltage (ASSUMED, as the 7 V = 29 %% law of sim/pv_module.py) and the airflow their speed, so the thermal line above runs on "
        "airflow x%.3f; the control board prints the same voltage from its own drawn parts", FAN_RULE,
        S75["regulation"]["fan_full_speed_needs_V"], w_fan, p_low, hold_min, p_live, p_low / w_fan, selv_min_at(n_ph, p_low),
        S75["regulation"]["selv_V_gates_off"][0], S75["regulation"]["selv_V_gates_off"][1], fc["s_min"], fc["v_pass"], fc["i"],
        fc["v_need"], fc["ratio"], fc["flow_min"], w_fan, fc["d"], fc["rds"], 1e3 * fc["rdc"], fc["s_min"] - fc["v_pass"],
        fc["vin_full"], fc["ratio"])
    sw_lo, sw_hi = sup_window()
    assert sw_lo[0] >= 4.5 and sw_hi[1] <= V5[0], "+5V supervisor window vs AHCT VCC 4.5 V / the 5 V tolerance"
    say("sup", "+5V and +5V_GD (%.2f-%.2f V, LMR38020 VREF 0.985-1.015 V, 0.1 %% divider) supervised by TPS3710 %s/%s 0.1 %%: "
        "RDY low below %.3f-%.3f V, released above %.3f-%.3f V (AHCT VCC >= 4.5 V; gdrv bias headroom asserted at 4.75 V); "
        "the aux status (live 24 V UV 21.0-21.8 V) is wired-AND on RDY too", V5[0], V5[1], gdrv.ohm(SUP_DIV[0]),
        gdrv.ohm(SUP_DIV[1]), sw_lo[0], sw_lo[1], sw_hi[0], sw_hi[1])

    # ---- +3V3 for the control board: level and ramps (F28003x SPRSP61C SRVDDIO-UP 8-100, -DN 20-100 mV/us)
    nets = B.D.nets()
    big = lambda c: c >= 1e-6
    c_my = [L.number(p_.value.split()[0]) for p_ in parts if key(p_) == "C" and set(p_.pins.values()) in
            ({"+3V3", "GND"}, {"+3V3", "AGND"})]
    c_lo = sum(c * (0.5 if big(c) else 0.85) for c in c_my) + CTL_C33[0] * 0.5 + CTL_C33[1] * 0.85
    c_hi = sum(c * 1.1 for c in c_my) + (sum(CTL_C33) + CTL_C33A) * 1.1
    up = (V33[0] * 2.3e-6 / (1.25 * SS33 * 1.05), V33[1] * 2.7e-6 / (1.25 * SS33 * 0.95))      # SLVSAG7F p5 ISS, eq 10
    i_in = c_hi * up[1] + s33                                     # above 0.5 V: loads on, vs ILIMF 3.6 A min (p5)
    i_lo5 = c_hi * up[1]                                          # below 0.5 V: loads ~off, vs the 1.6 A typ reduced limit
    r_dn = (R_DIS33 * 0.99, R_DIS33 * 1.01 + 0.04)                 # + PMV30ENEA <= 40 mOhm at VGS 4.5 V (p6)
    dn = ((2.5 / r_dn[1]) / c_hi, (V33[1] / r_dn[0] + s33) / c_lo)
    assert V33[0] >= 3.20 and up[0] >= 8e3 and up[1] <= 100e3 and i_in <= 0.8 * 3.6 and i_lo5 <= 1.6, \
        "+3V3 level / ramp-up / inrush"
    assert dn[0] >= 20e3 and dn[1] <= 100e3 and "SS_3V3" in nets and "DIS_D" in nets, "+3V3 ramp-down"
    say("rail33", "+3V3 to the control board (TPS62130 from +5V_GD, FB %s/%s 0.1 %%): %.3f-%.3f V (power-save worst, FB "
        "leakage) >= 3.20 V (PV-CTL reference dropout); ramp-up SS/TR %s C0G 5 %% (ISS 2.3-2.7 uA, eq 10): %.1f-%.1f mV/us "
        "in 8-100 (F28003x SRVDDIO-UP), inrush %.2f A below 0.5 V (<= 1.6 A typ reduced limit) / %.2f A with the loads (<= "
        "80 %% of ILIMF 3.6 A) into %.0f-%.0f uF (this "
        "board's %.1f uF nominal + PV-CTL %.1f + %.1f uF, MLCC DC bias 0.5-1.1 ASSUMED); ramp-down: PG_5VG low disables the "
        "TPS62130 and %s + PMV30ENEA discharge: %.0f mV/us at 2.5 V (C max, no load) to %.0f mV/us at %.2f V (C min, "
        "%.0f mA load) in 20-100 (SRVDDIO-DN) through the I/O BOR band; the LMR36015 of A0 (3-6 ms internal soft start = "
        "0.6-1.1 mV/us, load-limited fall ~4 mV/us) met neither", gdrv.ohm(FB33[0]), gdrv.ohm(FB33[1]), V33[0], V33[1],
        gdrv.farad(SS33), up[0] / 1e3, up[1] / 1e3, i_lo5, i_in, c_lo * 1e6, c_hi * 1e6, sum(c_my) * 1e6,
        (CTL_C33[0] + CTL_C33[1]) * 1e6, CTL_C33A * 1e6, gdrv.ohm(R_DIS33), dn[0] / 1e3, dn[1] / 1e3, V33[1], s33 * 1e3)

    # ---- loads the control board (PV-CTL rev A1) puts on this board's outputs
    v_rdy = V33[0] * 100e3 / (100e3 + 4.99e3 * 1.01)
    v_mov = V33[0] * 100e3 / (100e3 + 10e3 * 1.01)
    i_flt = V33[1] / (4.99e3 * 0.99)
    r_src = (STK["r_out"][0] + 99.0, STK["r_out"][1] + 101.0)
    g_il = [CTL_IL_LOAD / (CTL_IL_LOAD + r) for r in r_src]
    r_srf = (STK["r_ref"][0] + 99.0, STK["r_ref"][1] + 101.0)
    assert v_rdy >= 2.0 + 0.5 and v_mov >= 2.0 + 0.5 and i_flt <= 5e-3 and 4.5 / CTL_IL_LOAD <= 1e-3, "PC loads"
    assert STK["vref"][1] / CTL_ILR_LOAD <= 0.1e-3, "Uref load above 0.1 mA (no drive rating published: kept small)"
    N["il_gain"] = g_il
    say("ctlload", "Loads from the control board (PV-CTL rev A1): FLT_N 4.99k pull-up there -> %.2f mA into the NSI6651 / "
        "BSS138BK open drains (VOL <= 0.3 V at 5 mA); RDY 4.99k here vs 100k pull-down there -> high >= %.2f V (VIH 2.0 V); "
        "MOV_OK 10k here vs 100k -> high >= %.2f V; HOLD push-pull into 100k; each IL line into %.1f kOhm: source %.0f-%.0f "
        "ohm (sensor R_out 15-25 + 100R) -> gain x%.4f-x%.4f (-%.2f..-%.2f %%, removed by the 2-point calibration; spread "
        "%.3f %%), zero 2.50 V -> %.3f V (re-zeroed at idle), drive <= %.2f mA; each ILnR line into %.1f kOhm (trip ladder): "
        "source %.0f-%.0f ohm (sensor R_ref 12-20 + 100R), <= %.0f uA from Uref (drive capability not published: ASSUMED "
        "fine at this level)", i_flt * 1e3, v_rdy, v_mov,
        CTL_IL_LOAD / 1e3, r_src[0], r_src[1], g_il[1], g_il[0], 100 * (1 - g_il[0]), 100 * (1 - g_il[1]),
        100 * (g_il[0] - g_il[1]), 2.5 * g_il[1], 4.5 / CTL_IL_LOAD * 1e3, CTL_ILR_LOAD / 1e3, r_srf[0], r_srf[1],
        STK["vref"][1] / CTL_ILR_LOAD * 1e6)

    # ---- IA / IB bandwidth (gen/port.py lean_shunt: G 30 amplifier, feedback R || C)
    bw = []
    for t in ("A", "B"):
        rf = [L.number(p_.value) for p_ in parts if key(p_) == "R" and set(p_.pins.values()) == {t + "_SHAN", t + "_IM"}]
        cf = [L.number(p_.value.split()[0]) for p_ in parts if key(p_) == "C" and set(p_.pins.values()) ==
              {t + "_SHAN", t + "_IM"}]
        assert len(rf) == 1 and len(cf) == 1, "lean_shunt measurement path changed"
        bw.append(1 / (2 * math.pi * rf[0] * cf[0]))
    need = CTRL["measurement_requirements"]["port_current_IA_IB"]["bandwidth_kHz_min"] * 1e3
    say("iabw", "IA / IB bandwidth (lean_shunt feedback %s || %s): %.1f kHz vs >= %.0f kHz (control_spec)%s", "30.1k",
        gdrv.farad(cf[0]), min(bw) / 1e3, need / 1e3, "" if min(bw) >= need else
        " - WAITS for the port engineer: 470 pF gives %.1f kHz" % (1 / (2 * math.pi * rf[0] * 470e-12) / 1e3))
    if min(bw) < need:
        st.setdefault("wait", []).append("gen/port.py lean_shunt: IA / IB feedback capacitor %s -> 470 pF (%.1f -> %.1f kHz, "
                                         "control needs %.0f kHz)" % (gdrv.farad(cf[0]), min(bw) / 1e3,
                                                                     1 / (2 * math.pi * rf[0] * 470e-12) / 1e3, need / 1e3))

    # ---- standby consumption: true standby (both contactors open) and idle with both held (gates and fans off)
    cond = S75["standby"]["condition"]
    h_ref = float(re.search(r"held at <= ([\d.]+) W each", cond).group(1))
    m_ = re.search(r"with 2 x ([\d.]+) W the 4-phase module draws ([\d.]+) W at (\d+) V", cond)
    var = "PV-P75" if n_ph == 3 else "PV-P100/110"
    eta_inc = 2 * (float(m_.group(1)) - h_ref) / (float(m_.group(2)) - S75["standby"]["PV-P100/110"][m_.group(3)]["module_W"])
    bl_r, mon_r = port.S["lean"]["bleeder"]["n"] * port.S["lean"]["bleeder"]["R_elem_ohm"], \
        port.S["spd"]["n_mon"] * port.S["spd"]["R_mon_ohm"]
    rows = []
    for v in ("600", "1000"):
        V = float(v)
        aux_in = S75["standby"][var][v]["aux_in_W"]
        p_open = 2 * V ** 2 / 6.005e6 + 2 * V ** 2 / mon_r + (V / 2) ** 2 / 6.005e6
        p_conn = p_open + 2 * V ** 2 / bl_r + 2 * V ** 2 / 6.005e6
        held = aux_in + 2 * (ECON["hold_W_at_24V"] - h_ref) / eta_inc + p_conn
        opened = aux_in - 2 * h_ref / eta_inc + p_open
        rows.append((V, opened, held))
    N["standby"] = rows
    say("standby", "Standby consumption (gates off, fans off; ESTIMATE): both contactors OPEN (true standby, banks discharged) "
        "%s; both HELD on the economiser (%.2f W each typ) %s. Basis: aux75_spec standby aux input (gates off, fans off, "
        "contactors held at <= %.1f W each) corrected by the coil "
        "power at the spec's incremental efficiency %.2f, plus this board's passive HV loads from the port_spec values: "
        "terminal dividers, varistor monitor loops (%s per port) and the PE divider always; bleeders (%s) and bank dividers "
        "only with the banks charged", ", ".join("%.1f W at %.0f V" % (o, V) for V, o, _ in rows), ECON["hold_W_at_24V"],
        ", ".join("%.1f W at %.0f V" % (h, V) for V, _, h in rows), h_ref, eta_inc, gdrv.ohm(mon_r), gdrv.ohm(bl_r))

    # ---- PC connector: every signal's source and what the control board must present
    n_od = n_ch + 2
    lvl = [
        "PWM1-%d, EN, BIAS_EN: 3.3 V CMOS IN, 10k pull-down + SN74AHCT1G08 (VIH 2.0 V, VIL 0.8 V); control drives "
        ">= 2.4 V at 0.4 mA; PWMk is also ANDed with EN here" % (4 * n_ph),
        "K_A, K_B, K_PRE IN: 100k pull-down + SN74LVC1G11 (VIH 2.0 V at 3.3 V); IMD_SW1/2 IN: 100k + BSS138BK gate "
        "(VGS(th) <= 1.5 V)",
        "FLT_N OUT: open drain, %d NSI6651 /FLT (0.3 V at 5 mA) + 2 BSS138BK (port OC), 100 pF; control board MUST pull "
        "up to its 3.3 V (4.99k recommended); low = DESAT/UVLO (latched in the driver) OR port OC (live)" % n_ch,
        "RDY OUT: open drain wired-AND (%d NSI6651 RDY + TPS3710), pulled up HERE (4.99k to +3V3), 100 pF; control board: "
        "CMOS input, no pull-up (a >= 100k pull-down is allowed)" % n_ch,
        "HOLD OUT: push-pull 3.3 V (SN74LVC1G32); MOV_OK OUT: open drain + 10k pull-up to +3V3 here, valid only while "
        "both ports are above ~200 V",
        "IL1-%d OUT: 2.50 V (2.48-2.52) + 10.667 mV/A (+ = SW_A -> SW_B), +/-100 A = 1.43-3.57 V, sensor swing 0.5-4.5 V, "
        "source 15-25 ohm + 100 ohm, 1 nF; NOT ratiometric; the control AFE must scale/clamp to its 3.3 V ADC (gain ~1.45 "
        "about 2.5 V -> 0.052 A/LSB)%s" % (n_ph, "; IL4 = 0 V on PV-P75 (variant code)" if n_ph == 3 else ""),
        "IL1R-%dR OUT: sensor reference Uref %.2f-%.2f V (STK pin 4; ILn - ILnR = %.0f +/- %.0f mV at 0 A + 10.667 mV/A), "
        "source %.0f-%.0f ohm (R_ref + 100R), 1 nF; PV-CTL rev A1 takes its IL trip thresholds from it%s"
        % (n_ph, STK["vref"][0], STK["vref"][1], 0.0, STK["voe"] * 1e3, STK["r_ref"][0] + 99.0, STK["r_ref"][1] + 101.0,
           "; IL4R = 0 V on PV-P75" if n_ph == 3 else ""),
        "IA, IB OUT: OPA2388, VMID %.3f-%.3f V + 3.0 mV/A (I into the module at the port's - terminal), linear +/-%.0f A; "
        "IA_H, IB_H: VMID + 1.0 mV/A, +/-%.0f A" % (vmid[0], vmid[1], lin_im, lin_ih),
        "VA, VB OUT (bank side): VMID + V/1203 (OPA2388); VAX, VBX (terminal side, bipolar), VPE (PE vs BUS-): VMID +/- "
        "V/1203; 1144 V = 0.951 V above VMID",
        "NTC1-%d (heatsink) / NTC5-%d (inductors): NTC 10 k to AGND + 100 nF here; the control board biases and reads them"
        "%s" % (n_ph, 4 + n_ph, "; NTC4 / NTC8 open on PV-P75" if n_ph == 3 else ""),
        "Supplies OUT: +3V3 (1 pin, 3 A); +24V / +5V are no longer on PC (rev A2: pins 1-2 / 5-6 carry IL1R-IL4R); "
        "control board allocation %.0f / %.0f / %.0f mA" % (CTRL_LOAD["+24V"] * 1e3, CTRL_LOAD["+5V"] * 1e3,
                                                             CTRL_LOAD["+3V3"] * 1e3)]
    for k, line in enumerate(lvl):
        say("pc%02d" % k, "PC: %s", line)
    assert n_od <= 20 and V33[1] / 4.99e3 <= 5e-3, "open-drain loading"

    # ---- default off with the PC connector open: structural check on the drawn netlist
    say("deflt", "%s", default_off(B, n_ph))

    # ---- catalogue keys shared with gen/aux_hv.py (aux_block() swaps them for the aux call and checks the MPNs)
    assert AUX_SWAP, "aux_block() did not run"
    say("swap", "Catalogue keys defined both here and in gen/aux_hv.py, kept apart by aux_block() (pin numbers equal, MPN "
        "per drawn part checked before / after the call): %s", "; ".join("%s = %s here / %s in the aux block" % (k, a, b)
                                                                   for k, (a, b) in sorted(AUX_SWAP.items())))

    # ---- drawing read back
    for p in range(1, n_ph + 1):
        for s in ("S1", "S2", "S3", "S4"):
            t = tag(p, s)
            for d in "ab"[:N_PAR]:
                g = "G_%s%s" % (t, d)
                ron = [p_.value for p_ in parts if key(p_) == "R" and set(p_.pins.values()) == {"OUTH_" + t, g}]
                roff = [p_.value for p_ in parts if key(p_) == "R" and set(p_.pins.values()) == {"OUTL_" + t, g}]
                assert (ron, roff) == ([gdrv.ohm(R_GATE[0])], [gdrv.ohm(R_GATE[1])]), "%s gate resistors" % g
    for bus in ("A_BUS+", "B_BUS+"):
        n45 = sum(1 for p_ in parts if key(p_) == "FILM45" and p_.pins["1"] == bus)
        n22 = sum(1 for p_ in parts if key(p_) == "FILM2U2" and p_.pins["1"] == bus)
        assert (n45, n22) == (bank_total(n_ph, bus[0]), DEC_N * n_ph), "%s bank %d + decoupling %d" % (bus, n45, n22)
    assert cnt("NSI6651ASC") == n_ch and cnt("T_BIAS4") == n_ph and cnt("STK75") == n_ph and cnt("SN6505B") == n_ph
    say("draw", "Drawing = spec: %d phases, %d x SG2M040170HJ, Ron/Roff %g/%g ohm on every gate, %d + %d bank capacitors, %d "
        "+ %d decoupling, %d NSI6651, %d T_BIAS4, %d STK-HO/A 75; pending PC nets: %s", n_ph, 8 * n_ph, R_GATE[0], R_GATE[1],
        bank_total(n_ph, "A"), bank_total(n_ph, "B"), DEC_N * n_ph, DEC_N * n_ph, n_ch, n_ph, n_ph, ", ".join(st["todo"]) or "none")
    return out, N


def default_off(B, n_ph):
    """Asserts that with the PC connector open (control board absent or in reset) no gate can turn on and no contactor
    coil can be energised: every command input has a pull-down and no pull-up, EN gates every PWM buffer, the drivers'
    RST/EN and the bias enable are pulled down, every coil-drive gate is pulled down."""
    parts = list(B.D.parts.values())
    key = lambda p_: p_.lib_id.split(":")[1]
    rails = {"+3V3", "+5V", "+5V_GD", "+24V"}
    res = lambda net, other: [p_ for p_ in parts if key(p_) == "R" and set(p_.pins.values()) == {net} | other]
    down = lambda net: any(res(net, {g}) for g in ("GND", "AGND"))
    cmds = ["EN", "BIAS_EN", "K_A", "K_B", "K_PRE", "IMD_SW1", "IMD_SW2"] + [pwm(p, k) for p in range(1, n_ph + 1)
                                                                             for k in range(1, 5)]
    for c in cmds:
        assert down(c) and not any(res(c, {r}) for r in rails), "%s: no pull-down or a pull-up" % c
    buf = {p_.pins["4"]: p_ for p_ in parts if key(p_) == "AHCT1G08"}
    for p in range(1, n_ph + 1):
        for k in range(1, 5):
            b_ = buf[pwm(p, k) + "_5V"]
            assert {b_.pins["1"], b_.pins["2"]} == {pwm(p, k), "EN"}, "%s not gated by EN" % pwm(p, k)
    drv = [p_ for p_ in parts if key(p_) == "NSI6651ASC"]
    assert drv and all(p_.pins["14"] == "EN5" for p_ in drv) and down("EN5") and buf["EN5"].pins["1"] == "EN"
    sn = [p_ for p_ in parts if key(p_) == "SN6505B"]
    assert all(down(p_.pins["5"]) and buf[p_.pins["5"]].pins["1"] == "BIAS_EN" for p_ in sn), "bias enable not default-off"
    coil_g = sorted({n for p_ in parts for n in p_.pins.values() if n and re.fullmatch(r"[AB]_[KP]_G", n)})
    assert coil_g and all(down(g) for g in coil_g), "coil-drive gate without pull-down"
    return ("Default off with the PC connector open (asserted on the netlist): %d command inputs pulled down, none pulled "
            "up; every PWM buffer ANDed with EN; all %d NSI6651 RST/EN on EN5 (pulled down, buffered from EN); %d SN6505B "
            "enables pulled down (bias off); coil-drive gates %s pulled down -> no gate turns on, no coil is energised%s"
            % (len(cmds), len(drv), len(sn), "/".join(coil_g), "; PWM13-16 not connected on PV-P75" if n_ph == 3 else ""))


def cover(N, n_ph):
    """Root-sheet notes (one A3 line each)."""
    lines = [
        "CALCULATED, not measured: design_check() asserts every number on each build (outputs/%s_design_check.txt); values "
        "are read from sim/out/*.json at build time." % PROJECT[n_ph],
        "Domains: LIVE (BUS- = GND = AGND), GD_PxSy gate islands (functional: NSI6651, T_BIAS4), PE (AlN pads, inductor "
        "cores, heatsink NTC lugs, Y / MOV3 / IMD crossings), SELV (AUX-T1 reinforced winding -> J_SELV). Coat to PD1.",
        "Shared heatsink: Tj %.0f C at 45 C inlet (R_sa target); heatsink OT trip must be <= %.0f C. Dead time %.0f-%.0f ns. "
        "Sensor delay %.2f us, error %.1f %% at 45 A after calibration." % (N["tj"], N["trip_ok"], N["dt"][0], N["dt"][1],
                                                                          N["t_s"] * 1e6, N["err"] * 100),
        "Live 24 V %.1f W (economiser) <= aux live rating %.1f W; module aux %.0f W. %s." % (N["p_live"], N["live_w"],
                                                                                            N["p_aux"], FAN_RULE)]
    assert max(len(x) for x in lines) <= 235
    return lines


WAIVERS = dict(gdrv.ERC_WAIVERS)   # NSI6651 GND2 = the gate island's COM star (gen/port.py has the same waiver type)

if __name__ == "__main__":
    rc = 0
    for n_ph in (3, 4):
        B, st = build_design(n_ph)
        lines, N = design_check(B, n_ph, st)
        B.D.root_notes = cover(N, n_ph)
        rv = vranges(n_ph)
        for k, v in st["aux"]["vrange"].items():          # the aux block's own nets (status = RDY keeps this board's range)
            rv.setdefault(k, v)
        rc |= L.build(B, waivers=WAIVERS, domain_of=domain_for(st), isolators=st["iso"], crossings=st["xing"],
                      vrange=lambda n, rv=rv: rv.get(n) or port.vrange_lean(n))
        with open(os.path.join(L.REPO, "hardware", PROJECT[n_ph], "outputs", PROJECT[n_ph] + "_design_check.txt"), "w") as f:
            f.write("CALCULATED by gen/pv_power.py design_check() - not measured, not bench-validated.\n")
            f.write(stamp() + "\n")
            f.write("\n".join(lines.values()) + "\n")
            f.write("".join("WAITS FOR (outside this board): %s\n" % x for x in st.get("wait", [])))
        for x in st.get("wait", []):
            print("[WAIT] %s: %s" % (PROJECT[n_ph], x))
    sys.exit(rc)
