"""PCS-PWR - power board of the PCS-P125 three-phase battery inverter (D-053, D-057, D-060; the power stage, filter and
cost of docs/requirements/ARCHITECTURE-PCS.md are superseded by sim/pcs_design.py; its ports, the single reinforced
barrier and the control-board variant stand). Two-level bridge, 6 x Sichain SG2M040170HJ per switch, 32 kHz, DC
590-950 V on a split film DC link (midpoint M = the C_f star), LCL filter with chassis inductors. Everything live is
referenced to DC- (GND = AGND = DC-); the control board is PV-CTL as an assembly variant (contract PCS_PC + PCS_X in
gen/interfaces.py). Every value is read at build time from sim/out/pcs_design/pcs_spec.json and the magnetics design
files; design_check() asserts them on every build.

STAGES DONE / TODO (each DONE stage builds and passes every check of gen/dcdclib.py):
  1 DONE  skeleton: PCS_PC + PCS_X connectors, DC link 5 + 5 C3D1U147 with midpoint, bleeders per half, DC-link
          dividers (midpoint here; V(DC+) from the DC port's bank divider since stage 4), phase leg a (2 x 6 devices,
          Al2O3 pads, 3 sub-cells of 3 x 2.2 uF + RC damper)
  2 DONE  gate drive: gdrv preset SC40X6 '6 x SG2M040170HJ' (2 PNP turn-off followers, clamp inverter / release for six
          gates, dead-time stretch 3.65k/100p), 2 channels per leg (per device Ron 8.75 / Roff 7.5 ohm, Kelvin R 0.5 ohm,
          10 k + 1 nF at the pins, per-gate Miller clamp, DESAT + booster, negative-rail detector), per-phase bias
          (SN6505B + T_BIAS4, 2 of its 4 secondaries), 3.3 -> 5 V command buffers
  3 DONE  legs b, c; phase-current sensors (RFQ: no +/-500 A, >= 100 kHz open-loop part with a data sheet found)
          between L1 and the C_f node, references ILnR to PCS_PC; L1 / L2 (chassis, CUSTOM, design files of
          sim/magnetics.py) with board studs; C_f 2 x 25 uF per phase to the star M, damping branch R_d + C_d; AC
          dividers at the C_f nodes (VC1-3) and at the terminal taps (VG1-3)
  4 DONE  DC port: gen/port.py lean_port 'battery' (HFE82V-300C contactor, HIITIO HCHVF1000-400A-38R aR per pole,
          precharge 220 ohm relay, varistor network + monitor, BUS- shunt with hold-off and 400 A over-current windows,
          polarity / dV interlocks 'full'; its bleeder left out: the DC link has its own); DC terminals 250 A, X / Y
          capacitors, removable PE link, insulation monitor (lean_imd)
  5 DONE  AC port: K1 / K2 3-pole AC contactors in series (RFQ, 24 V DC coil with its own economiser) on fail-safe
          high-side drivers (port._lean_drv) from K_A / K_AC2; CM choke (chassis, design file) between K2 and the AC
          terminals; 3 x thermally protected varistor L-PE_T (TVT25751) + 3 x Y1; type-B RCM sensor (RFQ) with test
          input; relay test = VG against VC (stage-3 dividers), one contactor at a time
  6 DONE  supplies: pv_power.sheets_supplies (+5V / +5V_GD LMR38020, +3V3 TPS62130 with ramps, SELV connector) and the
          75 W aux block of gen/aux_hv.py from the DC terminal tap B_T+ and the DC link (BYG10Y OR); bias of leg a on +5V,
          legs b, c on +5V_GD; heatsink NTC lug probe per section (NTC1-3), L1 winding NTC leads (NTC5-7), DC-link NTC
          (NTC9); heatsink sections; budgets, trip bands, discharge times, ranges and domains in design_check(). Fans: the
          three 120 mm fans hang on PV-CTL's SELV fan headers, as on the PV module (no fan connector on this board)
OPEN: (1) DESAT trips at V_DS 5.4-8.3 V (3 x US1M): the on-state voltage at the 200 ms overload peak (1.2 x 216 A + ripple,
      ~66 A per device) at 160-175 C is 5.0-5.7 V - a nuisance trip is possible in that overload; conservative choice kept
      (DESAT as designed); fix with 2 x US1M or a Zener in the string after a measurement. (2) The hardware dead time at
      the gates is 185-510 ns (six gates need >= 157 ns): a firmware dead band below ~430 ns is extended by the hardware -
      dead-time compensation must use the measured value, not the hand-over's 300 ns. (3) Phase-current sensor is an RFQ
      line: Sinomags STK-HO/A 200 stops at +/-375 A, the STK-BS/S1 and CHIPSENSE HS1V / AS1V busbar sensors reach
      25-60 kHz only (data sheets read 2026-10-05, not filed); C_f, C_d and R_d are RFQ lines (no AC film capacitor or
      power resistor with a data sheet on file). (4) DC fuse HCHVF1000-400A-38R: its coordination with the contactor
      (hold-off band, installation band, OC latency) is not computed - sim/port_design.py covers the 250 A HPE501 only
      (the PCS engineer's 'aR >= 333 A, 400 A class' is met; 37 W per link at 230 A). (5) AC contactors and the RCM sensor
      are RFQ lines (no data sheet on file: CHINT NXC-225 / Delixi CJX2s-185 class; type-B RCM for 3 x 250 A); the
      economiser is required inside the contactor's DC coil module - a board economiser like the HFE82V's cannot be
      sized without coil data. The AC varistors are not monitored (their MON leads are wired to test points).
      (6) PV-CTL gates K_A with HEALTHY OR HOLD (DC hold-off); on the PCS, K_A drives AC contactor 1 and the PCS assembly
      must gate it with HEALTHY alone. (7) No AC-side start-up tap (the hand-over's 6-diode AC tap is not drawn): the aux
      starts from the battery terminal (B_T+) or the DC link only. (8) Aux live winding: 23 W of its 24 W design rating
      (gate bias of six 6-device channels + 3 contactor coils; the AC coils' 4 W hold is an RFQ assumption).
Usage: .venv/bin/python gen/pcs_power.py
"""
import json
import math
import os
import sys

import catalog
import dcdclib as L
import gdrv
import interfaces as IF
import port
import pv_power
import pvcell

PROJECT, REV, DATE = "PCS-PWR", "A0", "2026-10-05"
DS = "docs/datasheets/"
SPEC = json.load(open(os.path.join(L.REPO, "sim", "out", "pcs_design", "pcs_spec.json")))
CG = SPEC["commutation_and_gate_drive"]
DCL = SPEC["dc_link"]
LIM = SPEC["handover"]["control_engineer"]["limits"]
N_PAR = CG["n_parallel"]                       # devices per switch
N_SUB = 3                                      # sub-cells per leg: 2 high + 2 low devices each (pcs_spec 'leg': 9 / 3)
N_DEC = CG["leg"]["decoupling_parts"] // N_SUB  # 2.2 uF film per sub-cell

# ------------------------------------------------------------------------------------------------ catalog
PARTS = {
    # Faratronic C3D (passives-capacitors/Faratronic-C3D.pdf, Q/FRK 0.GS.C.C3D-F10): C3D1U147 = 140 uF, U_N 600 V at 70 C
    # hot spot / 500 V at 85 C, 57 x 65 x 45 mm, 4 pins P1 52.5 / P2 20.3 mm, ESR 3.0 mOhm, I_max 40.2 A (10 kHz, 70 C);
    # the '+++***' suffix (lead length / packing) is chosen at order
    "C3D1U147": dict(mfr="Xiamen Faratronic", mpn="C3D1U147+M0A", prefix="C", pkg="4-pin 57 x 65 x 45 mm, P1 52.5 / P2 20.3",
                     ds=DS + "passives-capacitors/Faratronic-C3D.pdf", stock=("Device", "C"),
                     desc="DC-link PP film 140 uF, 600 VDC (70 C) / 500 VDC (85 C), ESR 3.0 mOhm, Imax 40.2 A (10 kHz, "
                          "70 C), 4 pins (split DC link, one half)"),
    "PAD_AL2O3": dict(mfr="", mpn="", prefix="P", pkg="ceramic pad 0.635 mm, TO-247", ds="", sourcing="CUSTOM",
                      desc="TO-247 heatsink INSULATOR Al2O3 0.635 mm + spring clip + grease, basic DC-PE (pcs_spec "
                           "board_designers power_stage; sim/pcs_design.py cost basis); one earthed heatsink section per leg",
                      pins={"left": ["1 TAB p"], "right": ["2 HS p"]}),
    "J_PCX": dict(pv_power.PARTS["J_PC"], mpn="X9555WV-2x08-6TV01", pkg="2x8 2.54 mm box header THT",
                  stock=("Connector_Generic", "Conn_02x08_Odd_Even"),
                  desc="Shrouded box header 2x8 2.54 mm: PCS_X contract to the control board (grid voltages, RCM, K_AC2, "
                       "NTC9), live (250 V, 3 A)"),
}
PARTS["NTC_SMD"] = dict(mfr="Murata", mpn="NCP15XH103F03RC", prefix="RT", pkg="0402", stock=("Device", "Thermistor_NTC"),
                        ds=DS + "sensing/Murata-NCP-NTC.pdf", desc="NTC 10 k 1 % B25/50 3380 K (DC-link film bank, on board)")
PARTS["HEATSINK_PCS"] = dict(mfr="", mpn="", prefix="HS", pkg="Al extrusion section", ds="", sourcing="CUSTOM",
                             desc="CHASSIS CUSTOM heatsink section per leg (12 x TO-247 on Al2O3 pads), EARTHED (two PE bonds), "
                                  "sized by sim/pcs_design.py thermal_and_losses.heatsink", pins={"left": ["1 PE p"]})
CATALOG = {**pv_power.CATALOG, **{k: pvcell.CATALOG[k] for k in ("SG2M040170HJ", "R_DAMP", "NETTIE", "AHCT1G08")}, **PARTS}

MAGF = {k: json.load(open(os.path.join(L.REPO, "sim", "out", "magnetics", "design_pcs_%s.json" % k)))
        for k in ("l1", "l2", "cm_choke")}                                   # read at build time (sim/magnetics.py)


def mag_desc(k, what):
    m = MAGF[k]
    return ("CHASSIS-MOUNTED CUSTOM %s rev %s (sim/out/magnetics/design_pcs_%s.json, winding sheet spec_pcs_%s.md): %s; "
            "%.1f kg; %s. %s" % (what, m["revision"], k, k, m["construction"][:150], m["mass_kg"], m["status"],
                                 str(m["insulation"].get("system", ""))[:120]))


PARTS.update({
    "L1_PCS": dict(mfr="", mpn="", prefix="L", pkg="chassis, 2 power terminals + NTC leads + core bond", ds="",
                   sourcing="CUSTOM", desc=mag_desc("l1", "LCL inductor L1 %.0f uH" % (MAGF["l1"]["electrical"]["L_H"] * 1e6)),
                   pins={"left": ["1 A p", None, "3 NTC1 p"], "right": ["2 B p", None, "4 NTC2 p", "5 CORE p"]}),
    "L2_PCS": dict(mfr="", mpn="", prefix="L", pkg="chassis, 2 power terminals + core bond", ds="", sourcing="CUSTOM",
                   desc=mag_desc("l2", "LCL inductor L2 %.0f uH" % (MAGF["l2"]["electrical"]["L_H"] * 1e6)),
                   pins={"left": ["1 A p"], "right": ["2 B p", "3 CORE p"]}),
    # no +/-500 A open-loop sensor with >= 100 kHz and a data sheet was found (OPEN 3): RFQ with the hand-over's needs
    "CS_PH": dict(mfr="", mpn="", prefix="CS", pkg="PCB mount, integrated primary bar 250 A rms", ds="", sourcing="RFQ",
                  desc="RFQ open-loop (TMR / Hall) current sensor: measuring range >= +/-500 A, I_PN >= 216 A rms (250 A "
                       "continuous primary), 5 V supply, Vout = Vref + G x I with the reference on its own pin (Vref 2.5 V), "
                       "G 3.5-4.0 mV/A, bandwidth >= 100 kHz, response <= 1 us, accuracy +/-2 % after calibration, primary-"
                       "secondary basic for 1000 V DC + 400 V AC (OVC III), -40..105 C (pcs_spec sensing.phase_current)",
                  pins={"left": ["5 IP+ p", "6 IP- p"], "right": ["1 +UC pi", "2 GND pi", "3 UOUT o", "4 UREF o"]}),
    "CF_25U": dict(mfr="", mpn="", prefix="C", pkg="chassis or board, M6 / 4-pin", ds="", sourcing="RFQ", stock=("Device", "C"),
                   desc="RFQ AC filter capacitor MKP 25 uF 10 %, 450 V AC (rated for the phase-to-midpoint voltage incl. "
                        "the 150 Hz zero sequence), >= 15 A rms, self-healing, overpressure disconnector (C_f, 2 per phase)"),
    "CD_10U": dict(mfr="", mpn="", prefix="C", pkg="board, 4-pin", ds="", sourcing="RFQ", stock=("Device", "C"),
                   desc="RFQ AC film capacitor MKP 10 uF 10 %, 450 V AC, >= 5 A rms (LCL damping branch C_d)"),
    "RD_2R": dict(mfr="", mpn="", prefix="R", pkg="aluminium housing, chassis", ds="", sourcing="RFQ", stock=("Device", "R"),
                  desc="RFQ power resistor 2 ohm 5 %, 25 W in aluminium housing on the heatsink, 1000 V insulation to the "
                       "housing (LCL damping branch R_d)"),
})
CATALOG.update({k: PARTS[k] for k in ("L1_PCS", "L2_PCS", "CS_PH", "CF_25U", "CD_10U", "RD_2R")})
CATALOG.update({k: port.CATALOG[k] for k in ("STUD",)})
# HIITIO HCHVF1000 series (protection/HIITIO-HCHVF1000-Series.pdf): 400A-38R aR 1000 V DC, 50 kA (tau 2.5 ms), I2t pre-arc
# 43083 / clearing 289502 A2s, 112 W at 400 A; UL Recognized E533379 (certificate on file). sim/data/asia_modules.md sec. 7
PARTS["HCHVF400"] = dict(mfr="Zhejiang HIITIO New Energy", mpn="HCHVF1000-400A-38R", prefix="F", pkg="38 mm cartridge, bolted",
                         ds=DS + "protection/HIITIO-HCHVF1000-Series.pdf", stock=("Device", "Fuse"),
                         desc="CHASSIS-MOUNTED aR fuse 400 A 1000 VDC, 50 kA (L/R 2.5 ms), 112 W at In, UL E533379: DC port")
CATALOG["HCHVF400"] = PARTS["HCHVF400"]
ACC = SPEC["ports_and_common_mode"]["ac_contactor"]
VY1_VDC_PCS = 1500.0                                        # Vishay VY1 p1: Y1 500 V AC / 1500 V DC
PARTS.update({
    "KAC3": dict(mfr="", mpn="", prefix="K", pkg="chassis, 3 main poles M8 + coil connector", ds="", sourcing="RFQ",
                 desc="CHASSIS-MOUNTED RFQ 3-pole AC contactor (CHINT NXC-225 / Delixi CJX2s-185 class): AC-1 >= %.0f A at "
                      "400-460 V AC, Ui %d V, Uimp %d kV, 24 V DC coil WITH integrated economiser (pull-in <= 20 W, hold <= 4 W), "
                      "coil-contact Ui >= 1000 V, mechanical life >= 1e6; 2 in series per phase set (relay test)" %
                      (ACC["Ie_AC1_A_min"], ACC["Ui_V"], ACC["Uimp_kV"]),
                 pins={"left": ["1 L1 p", "3 L2 p", "5 L3 p", None, "7 A1 p"], "right": ["2 T1 p", "4 T2 p", "6 T3 p", None,
                                                                                    "8 A2 p"]}),
    "CM_PCS": dict(mfr="", mpn="", prefix="L", pkg="chassis, cores over the 3 phase busbars", ds="", sourcing="CUSTOM",
                   desc=mag_desc("cm_choke", "AC common-mode choke %.0f uH min" %
                                 (MAGF["cm_choke"]["electrical"]["L_min_H"] * 1e6)),
                   pins={"left": ["1 A1 p", "3 B1 p", "5 C1 p"], "right": ["2 A2 p", "4 B2 p", "6 C2 p"]}),
    "RCM_B": dict(mfr="", mpn="", prefix="CS", pkg="chassis or board, aperture for 3 x 250 A", ds="", sourcing="RFQ",
                  desc="RFQ type-B residual-current sensor (fluxgate) around L1-L3: 6 mA DC / 30 mA AC resolution, analog "
                       "output 0-1.25 A range, test-winding input, 5 V supply, aperture for 3 x 250 A busbars (pcs_spec "
                       "sensing.residual_current; IEC 62109-2 / IEC 62752 style)",
                  pins={"left": ["1 VCC pi", "2 GND pi"], "right": ["3 OUT o", "4 TEST i"]}),
})
CATALOG.update({k: PARTS[k] for k in ("KAC3", "CM_PCS", "RCM_B")})
FUSE = dict(In=400.0, p_in=112.0, i2t_clr=289502.0)        # HCHVF1000-400A-38R row
I_DC = 250.0                                                # DC port rating (hand-over: contactor 300 A, shunt 25 mV at 250 A)
LCL = SPEC["lcl"]["filter"]

# ------------------------------------------------------------------------------------------------ design inputs
F_DC = dict(c=140e-6, un70=600.0, un85=500.0, esr=3.0e-3, imax=40.2)      # C3D p8 row C3D1U147 (as sim/pcs_design.py FILM)
F22 = pv_power.F22                                                          # FCSA3DS225 (Jianghai CBB138 p30)
V_DC_MAX = LIM["V_dc_trip_V"]                  # hardware DC over-voltage trip (pcs_spec limits)
V_HALF_TRIP = DCL["voltage_use"]["OV_trip_per_half_V"]
BL_N = 4                                       # bleeder elements per half (1210, 200 V working each)
BL_R = DCL["bleeder"]["R_per_half_ohm"] / BL_N
LABEL_MIN = 15.0                               # enclosure label 'wait 15 min' (the DC link alone; checked worst case)
T_BOARD = pvcell.T_BOARD
HSK = SPEC["thermal_and_losses"]["heatsink"]
GD = gdrv.SC40X6                               # gate-drive preset: one channel, 6 x SG2M040170HJ (gen/gdrv.py)
GATE_V = GD["gate_v"]
assert list(GATE_V) == [CG["rails_V"][0], -CG["rails_V"][1]] and GD["n"] == N_PAR, "gate rails / devices vs pcs_spec"
assert GD["r_gate"] == (CG["chosen"]["R_G_on_ext_ohm_per_device"], CG["chosen"]["R_G_off_ext_ohm_per_device"]), "R_G"
V_PEAK = CG["worst_1050V_450A_30nH"]["v_pk"]   # switch-node recurring peak at the trip corner (NSI6651 VIORM check)
LEGS = ("A", "B", "C")                         # drawn legs
PWM = {"A": ("PWM1", "PWM2"), "B": ("PWM3", "PWM4"), "C": ("PWM5", "PWM6")}   # PCS_PC: (high, low) per leg


def e96(x):
    """nearest E96 value (the bleeder element)."""
    dec = 10 ** math.floor(math.log10(x))
    vals = [round(10 ** (i / 96.0), 2) for i in range(96)]
    return min((v * dec for v in vals), key=lambda v: abs(v - x))


def leg_nets(ph):
    """(DC+, switch node, DC-) of leg ph ('A', 'B', 'C')."""
    return "DC+", "SW_" + ph, "DC-"


def dev_tag(ph, sw, k):
    """device k (1..6) of switch sw ('H' high, 'L' low) in leg ph -> tag 'AH3'."""
    return "%s%s%d" % (ph, sw, k)


# ------------------------------------------------------------------------------------------------ sheets
def sheet_pc(B, st):
    """PCS_PC (2x32, pin for pin the PV board's PC) and PCS_X (2x8); command pull-downs, RDY pull-up, star points."""
    B.new_sheet("01_pc", "Control connectors, defaults",
                "PCS_PC (= PC pin for pin, 2x32) and PCS_X (2x8) to PV-CTL (PCS assembly);\n"
                "command pull-downs, RDY pull-up, DC- star points")
    used = {"PWM%d" % k for k in range(1, 7)}
    nc = {"PWM%d" % k for k in range(7, 17)} | {"NTC4", "NTC8"}
    held = {"IA": "D_VMID", "IA_H": "D_VMID", "VAX": "D_VMID"}         # lines the PCS does not use: defined at mid-scale
    held.update(PC_RENAME)
    B.block("PCS_PC (gen/interfaces.py), live",
            "Pin for pin the PV board's PC: PWM1-6 = legs a, b, c (high, low); K_A = K_AC1, K_B = K_DC, K_PRE.\n"
            "IA, IA_H, VAX held at VMID; PWM7-16, NTC4, NTC8 not connected (three-wire); IL4 / IL4R = 0 V.")
    pins = IF.pins(IF.PCS_PC)
    B.part("J_PC", {k: (None if v in nc else held.get(v, v)) for k, v in pins.items()})
    B.block("PCS_X (gen/interfaces.py), live", "Grid voltages at the terminals (VG) and the C_f nodes (VC), RCM, K_AC2, "
            "NTC9.\nVGN (four-wire) not connected.")
    B.part("J_PCX", {k: (None if v == "VGN" else v) for k, v in IF.pins(IF.PCS_X).items()})
    B.block("Command defaults", "Every command input pulled low here (open connector = gates off, coils off).")
    for net in ["EN", "BIAS_EN"] + sorted(used):
        B.R("10k", net, "GND", note="pull-down: open = off")
    for net in ("K_A", "K_B", "K_PRE", "K_AC2", "IMD_SW1", "IMD_SW2", "RCM_TST"):
        B.R("100k", net, "GND", note="open = off")
    B.R("4.99k", "+3V3", "RDY", note="RDY pull-up (power board)")
    B.C("100p", "RDY", "GND", diel="C0G", tol="5%")
    B.C("100p", "FLT_N", "GND", diel="C0G", tol="5%")
    B.block("Command buffers (gdrv.input_buffers): 3.3 V -> 5 V SN74AHCT1G08",
            "NSI6651 inputs need VCC1 = 5 V levels; each PWM is ANDed with EN here, so a low EN removes every command.")
    pairs = [("EN", "EN5", None), ("BIAS_EN", "BIAS_EN5", None)] + [(w, w + "_5V", "EN") for ph in LEGS for w in PWM[ph]]
    gdrv.input_buffers(B, pairs, v5="+5V", gnd="GND", part="AHCT1G08")
    gdrv.input_buffers(B, [("BIAS_EN", "BIAS_EN5G", None)], v5="+5V_GD", gnd="GND", part="AHCT1G08")
    B.R("10k", "EN5", "GND", note="pull-down: unpowered buffer = drivers disabled")
    B.R("10k", "IL4", "AGND", note="three-wire variant code: IL4 = 0 V")
    B.R("10k", "IL4R", "AGND", note="three-wire: no phase-4 sensor reference")
    B.C("10u", "+3V3", "GND", pkg="1210", volt="16V")
    B.C("100n", "+3V3", "GND", volt="50V")
    B.block("Ground star points", "AGND meets GND at one point; GND meets DC- at one point next to the DC shunt.")
    B.part("NETTIE", {"1": "AGND", "2": "GND"})
    B.part("NETTIE", {"1": "GND", "2": "DC-"})
    for net in ("+3V3", "+5V", "GND", "AGND"):
        B.flag(net)


def sheet_dclink(B, st):
    """Split film DC link (midpoint M), bleeders per half, dividers for V(DC+) and V(M) with the VMID reference."""
    B.new_sheet("02_dclink", "DC link, bleeders, sensing",
                "%d + %d x C3D1U147 in two series halves, midpoint M (to the C_f star);\n"
                "bleeder per half; VB = V(DC+), VA = V(M) around VMID (REF3030E)" % (DCL["per_half"], DCL["per_half"]))
    B.block("Split DC link", "Upper half DC+ to M, lower half M to DC-, %d x 140 uF each (%.0f uF per half, %.0f uF in "
            "series).\nM is the C_f star point of the LCL filter (no midpoint control: two-level)." %
            (DCL["per_half"], DCL["C_half_uF"], DCL["C_series_uF"]))
    for a_, b_ in (("DC+", "M"), ("M", "DC-")):
        for _ in range(DCL["per_half"]):
            B.part("C3D1U147", {"1": a_, "2": b_})
    B.block("Bleeders (balance the halves, discharge the link)", "%d x %s 1210 per half (%s); label 'wait %.0f min'." %
            (BL_N, pv_power.gdrv.ohm(e96(BL_R)), pv_power.gdrv.ohm(BL_N * e96(BL_R)), LABEL_MIN))
    for half, (a_, b_) in (("U", ("DC+", "M")), ("L", ("M", "DC-"))):
        nodes = [a_] + ["BL%s%d" % (half, k) for k in range(1, BL_N)] + [b_]
        for x, y in zip(nodes, nodes[1:]):
            B.R(pv_power.gdrv.ohm(e96(BL_R)), x, y, pkg="1210", note="HV thick film, >= 200 V working")
    B.block("Reference (port.lean_refs: REF3030E, VMID 1.646 V and the DC-port threshold ladders)",
            "Shared with the DC port (stage 4). VMID buffered here; IA, IA_H, VAX of PCS_PC sit on it.")
    st["refs"] = r = port.lean_refs(B, "D", "+5V", "+3V3", "AGND")
    B.block("Midpoint divider (6 x 1 M ARHV06 + 4.99 k to VMID, 4.7 nF: 1 V = 1203 V)",
            "VA = VMID + V(M)/1203 (OPA2388 follower); its second half is the VMID follower. VB (DC+) comes from the DC\n"
            "port's bank divider (lean_dividers).")
    for top, tap in (("M", "D_VAD"),):
        nodes = [top] + ["%s_%d" % (tap, k) for k in range(1, 6)] + [tap]
        for x, y in zip(nodes, nodes[1:]):
            B.part("ARHV06_1M", {"1": x, "2": y}, value="1M")
        B.part("TNPW_4K99", {"1": tap, "2": r["VMID"]}, value="4.99k")
        B.C("4.7n", tap, r["VMID"], diel="C0G", tol="5%")
    B.part("OPA2388", {"3": "D_VAD", "2": "VA", "1": "VA", "5": r["VMIDR"], "6": r["VMID"], "7": r["VMID"], "8": "+3V3",
                       "4": "AGND"})
    B.C("100n", "+3V3", "AGND")
    B.block("DC-link temperature (NTC9 to PCS_X)", "Murata NCP15 10 k on the board between the film capacitors; biased on "
            "the control board.")
    B.part("NTC_SMD", {"1": "NTC9", "2": "AGND"})
    B.C("100n", "NTC9", "AGND")


def sheet_leg(B, ph, st):
    """Leg ph: high switch (DC+ -> SW) and low switch (SW -> DC-), 6 x SG2M040170HJ each on Al2O3 pads; per device
    10 k + 1 nF gate-source at the pins; three sub-cells (2 high + 2 low devices) with 3 x 2.2 uF and one RC damper."""
    dcp, sw, dcn = leg_nets(ph)
    B.new_sheet("%02d_leg%s" % (3 + "ABC".index(ph), ph.lower()), "Phase leg %s, power stage" % ph.lower(),
                "2 x %d x SG2M040170HJ on Al2O3 pads (earthed section %s); 10 k + 1 nF per gate;\n"
                "%d sub-cells: %d x 2.2 uF film + RC damper each" % (N_PAR, ph.lower(), N_SUB, N_DEC))
    for sw_, (drain, source) in (("H", (dcp, sw)), ("L", (sw, dcn))):
        B.block("Leg %s %s switch: %d x SG2M040170HJ" % (ph.lower(), "high" if sw_ == "H" else "low", N_PAR),
                "Per device: own gate net G_%s<k> and Kelvin net KS_%s<k> (gate network on the gate-drive sheet). BOM NOTE:\n"
                "one lot per switch, V_GS(th) binned to +/-0.25 V or a sharing test at incoming inspection\n"
                "(crosscheck_wolfspeed.md claim 5)." % (ph + sw_, ph + sw_))
        for k in range(1, N_PAR + 1):
            t = dev_tag(ph, sw_, k)
            st["xing"].add(B.part("SG2M040170HJ", {"1": drain, "5": drain, "2": source, "3": "KS_" + t,
                                                   "4": "G_" + t}).ref)
            st["iso"].add(B.part("PAD_AL2O3", {"1": drain, "2": "PE"}, value="PAD Al2O3 0.635 mm (CUSTOM)").ref)
    for s in range(1, N_SUB + 1):
        d1, d2, d3 = ("DMP_%s%d_%d" % (ph, s, i) for i in (1, 2, 3))
        B.block("Leg %s sub-cell %d: decoupling + RC damper" % (ph.lower(), s),
                "%d x FCSA3DS225 at devices %d-%d (high and low); damper %d x (%d x %s) + %d x %s 2 kV C0G in series,\n"
                "loop at the pins <= 3 nH (LAYOUT)." % (N_DEC, 2 * s - 1, 2 * s, pvcell.DAMP_SER, pvcell.DAMP_PAR,
                                                         pv_power.gdrv.ohm(pvcell.DAMP_R), pvcell.DAMP_C[1],
                                                         pv_power.gdrv.farad(pvcell.DAMP_C[0])))
        for _ in range(N_DEC):
            B.part("FILM2U2", {"1": dcp, "2": dcn})
        B.C(pv_power.gdrv.farad(pvcell.DAMP_C[0]), dcp, d1, pkg="2220", volt="2000V", diel="C0G", tol="5%")
        B.C(pv_power.gdrv.farad(pvcell.DAMP_C[0]), d1, d2, pkg="2220", volt="2000V", diel="C0G", tol="5%")
        for n1, n2 in ((d2, d3), (d3, dcn)):
            for _ in range(pvcell.DAMP_PAR):
                B.part("R_DAMP", {"1": n1, "2": n2}, value=pv_power.gdrv.ohm(pvcell.DAMP_R))


def sheet_gd(B, ph, st):
    """Leg ph: two gdrv.channel() with the SC40X6 preset (bias='ext') and the phase's bias_phase (2 secondaries)."""
    dcp, sw, dcn = leg_nets(ph)
    chans = []
    for sw_, drain, (own, other) in (("H", dcp, PWM[ph]), ("L", sw, PWM[ph][::-1])):
        t = ph + sw_
        st["sheet"] += 1
        B.new_sheet("%02d_gd%s" % (st["sheet"], t.lower()), "Leg %s %s-side gate drive" % (ph.lower(), "high" if sw_ == "H"
                                                                                           else "low"),
                    "gdrv.channel() %s: NSI6651ASC-Q1 +%g/-%g V, %g/%g ohm per device;\n"
                    "PNP turn-off buffers, Miller clamps, booster, DESAT%s" % ((GD["name"],) + GATE_V + GD["r_gate"] +
                                                                           ("; bias SN6505B + T_BIAS4" if sw_ == "L" else "",)))
        ch = gdrv.channel(B, t, pwm=own + "_5V", en="EN5", flt_n="FLT_N", rdy="RDY",
                          gate=tuple("G_" + dev_tag(ph, sw_, k) for k in range(1, N_PAR + 1)),
                          source=tuple("KS_" + dev_tag(ph, sw_, k) for k in range(1, N_PAR + 1)), drain=drain,
                          vdd="VDD_" + t, vee="VEE_" + t, interlock=other + "_5V", vin="+24V", vcc="+5V", gnd="GND",
                          gate_v=GATE_V, r_on=GD["r_gate"][0], r_off=GD["r_gate"][1], vclass=GD["vclass"], r_sb=GD["r_sb"],
                          v_peak=V_PEAK, deadtime=GD["deadtime"], bias="ext", neg_det=True,
                          sink_buffer=GD["sink_buffer"], inv=GD["inv"], rel=GD["rel"], c_gs=GD["c_gs"])
        st["gd"].update({n: "GD_" + t for n in ch.sec})
        st["iso"] |= set(ch.iso)
        st["xing"] |= set(ch.xing)
        chans.append((t, "VDD_" + t, "COM_" + t, "VEE_" + t))
    xf, sec = gdrv.bias_phase(B, "PH" + ph, chans, gate_v=GATE_V, v5="+5V" if ph == "A" else "+5V_GD", gnd="GND",
                              bias_en="BIAS_EN5" if ph == "A" else "BIAS_EN5G")
    st["iso"] |= set(xf)
    for t, nets in sec.items():
        st["gd"].update({n: "GD_" + t for n in nets})
    B.block("Leg %s: layout rules" % ph.lower(),
            "Gate and Kelvin of each device as coupled pairs of equal length from the COM star; Ron, Roff, Kelvin R, 10k + 1n,\n"
            "Zeners and the clamp FET + C_VL at each device (loop <= 1 nH). PNP followers next to their three gates. DESAT\n"
            "string >= 2.5 mm pad gap per diode; coat to PD1. Miller hold of six gates NOT simulated - BENCH TEST.")


def sheet_filter(B, ph, st):
    """Phase ph: switch node to L1 (chassis), L1 out through the phase-current sensor to the C_f node F, C_f (2 x 25 uF)
    and the damping branch to the star M, F to L2 (chassis); studs for the chassis leads."""
    n = "ABC".index(ph) + 1
    sw, f, lo, l2o = "SW_" + ph, "F_" + ph, "L1O_" + ph, "L2O_" + ph
    st["sheet"] += 1
    B.new_sheet("%02d_filt%s" % (st["sheet"], ph.lower()), "Phase %s LCL filter, current sensor" % ph.lower(),
                "Switch node -> L1 (chassis) -> sensor -> C_f node F_%s: C_f 2 x 25 uF + R_d-C_d\n"
                "to the star M (DC midpoint) -> L2 (chassis) -> AC port" % ph)
    B.block("Phase %s: L1 and L2 (chassis, CUSTOM) and their studs" % ph.lower(),
            "Board studs (REDCUBE press-fit, 100 A each): 3 x switch node to L1, 3 x L1 out, 3 x C_f node to L2,\n"
            "3 x L2 out (to AC contactor K1 on the port sheet). Cores bonded to PE.")
    st["iso"].add(B.part("L1_PCS", {"1": sw, "2": lo, "3": "NTC%d" % (4 + n), "4": "AGND", "5": "PE"},
                         value="L1 %.0fu (CUSTOM)" % (LCL["L1"] * 1e6)).ref)
    st["iso"].add(B.part("L2_PCS", {"1": f, "2": l2o, "3": "PE"}, value="L2 %.0fu (CUSTOM)" % (LCL["L2"] * 1e6)).ref)
    for net in (sw, lo, f, l2o):
        for _ in range(3):
            B.part("STUD", {"1": net})
    B.block("Phase %s current sensor (C_f side of L1)" % ph.lower(),
            "IL%d = Vref + G x I (I + = L1 -> C_f), IL%dR = its Vref: 100R + 1 nF each at the source (as PV-PWR).\n"
            "The primary sits between L1 and the C_f node (a 50 Hz-smooth node, not the switch node)." % (n, n))
    B.part("CS_PH", {"5": lo, "6": f, "1": "+5V", "2": "AGND", "3": "IL%d_S" % n, "4": "IL%dR_S" % n}, value="CS +/-500A RFQ")
    B.C("100n", "+5V", "AGND")
    for x in ("IL%d" % n, "IL%dR" % n):
        B.R("100R", x + "_S", x)
        B.C("1n", x, "AGND", diel="C0G", tol="5%")
    B.block("Phase %s C_f and damping branch (star = M, the DC midpoint)" % ph.lower(),
            "C_f = 2 x 25 uF MKP (%.0f uF); R_d %.0f ohm + C_d %.0f uF in series, F_%s to M." %
            (LCL["Cf"] * 1e6, LCL["Rd"], LCL["Cd"] * 1e6, ph))
    for _ in range(2):
        B.part("CF_25U", {"1": f, "2": "M"}, value="25u 450VAC RFQ")
    B.part("CD_10U", {"1": f, "2": "RD_" + ph}, value="10u 450VAC RFQ")
    B.part("RD_2R", {"1": "RD_" + ph, "2": "M"}, value="2R 25W RFQ")
    B.part("STUD", {"1": "AC_T_" + ph})


def sheet_acsense(B, st):
    """AC voltage dividers at the C_f nodes (VC1-3) and at the terminal taps (VG1-3): VMID + V/1203, OPA2388 followers."""
    st["sheet"] += 1
    B.new_sheet("%02d_acsense" % st["sheet"], "AC voltage sensing",
                "6 x (6 x 1 M ARHV06 + 4.99 k to VMID, 4.7 nF): VC1-3 at the C_f nodes,\n"
                "VG1-3 at the AC terminal taps; OPA2388 followers to PCS_X")
    r = st["refs"]
    B.block("Dividers (1 V = 1203 V around VMID, bipolar: the terminals float against DC- with the contactors open)",
            "Relay test: VG vs VC per phase with one contactor closed at a time (firmware).")
    outs = []
    for k, ph in enumerate("ABC", 1):
        for top, out in (("F_" + ph, "VC%d" % k), ("AC_T_" + ph, "VG%d" % k)):
            tap = out + "_D"
            nodes = [top] + ["%s_%d" % (tap, i) for i in range(1, 6)] + [tap]
            for x, y in zip(nodes, nodes[1:]):
                B.part("ARHV06_1M", {"1": x, "2": y}, value="1M")
            B.part("TNPW_4K99", {"1": tap, "2": r["VMID"]}, value="4.99k")
            B.C("4.7n", tap, r["VMID"], diel="C0G", tol="5%")
            outs.append((tap, out))
    for i in range(0, len(outs), 2):
        (pa, oa), (pb, ob) = outs[i], outs[i + 1]
        B.part("OPA2388", {"3": pa, "2": oa, "1": oa, "5": pb, "6": ob, "7": ob, "8": "+3V3", "4": "AGND"})
        B.C("100n", "+3V3", "AGND")


def sheet_pe(B, st):
    st["sheet"] += 1
    B.new_sheet("%02d_pe" % st["sheet"], "PE bonds, heatsink, NTC inputs",
                "Board PE bond, heatsink sections (earthed), heatsink NTC lug probes,\n"
                "L1 winding NTC leads, NTC filters (biased on the control board)")
    B.block("PE and heatsink", "Three earthed heatsink sections (one per leg); the Al2O3 pads isolate the device tabs "
            "(basic DC-PE).")
    B.part("PE_BOND", {"1": "PE"}, value="PE bond M4 (board PE)")
    for ph in st["legs"]:
        B.part("HEATSINK_PCS", {"1": "PE"}, value="heatsink section %s (CUSTOM)" % ph.lower())
    B.flag("PE")
    B.block("NTC inputs (NTC to AGND, biased on the control board)", "NTC1-3 = heatsink sections a-c: lug probe on the "
            "EARTHED heatsink, >= 2.5 kV rms lead-to-lug (R-10).\nNTC5-7 = L1 winding NTCs (leads on 2-pin headers). "
            "100 nF at each header.")
    for k, ph in enumerate(st["legs"], 1):
        st["iso"].add(B.part("NTC_HS", {"1": "NTC%d" % k, "2": "AGND", "3": "PE"}, value="NTC 10k lug probe (CUSTOM)").ref)
        for n in ("NTC%d" % k, "NTC%d" % (4 + k)):
            B.part("J_NTC2", {"1": n, "2": "AGND"})
            B.C("100n", n, "AGND")


def pending(B):
    """A PCS_PC / PCS_X net that no block drives yet gets a test point and is listed as PENDING in the design check."""
    nets = B.D.nets()
    todo = sorted(n for n, nodes in nets.items() if len([x for x in nodes if not x[0].startswith("#")]) == 1
                  and n in set(IF.PCS_PC) | set(IF.PCS_X))
    for n in todo:
        B.TP(n)
    return todo


# PCS_PC name -> this board's net (gen/port.py lean_* name their outputs <port>_<signal>; the DC port is 'B')
PC_RENAME = {"IB": "B_IM", "IB_H": "B_IH", "VB": "B_VB_O", "VBX": "B_VX_O", "VPE": "IMD_AUX_O", "HOLD": "B_HOLD"}


def sheets_dcport(B, st):
    """DC (battery) port with gen/port.py lean_port() on three sheets, then terminals, X / Y, PE link and the IMD."""
    st["sheet"] += 1
    no = st["sheet"]
    pb = port.lean_port(B, "B", "battery", no, "B_IN+", "B_IN-", "DC+", "DC-", "K_B", cmd_pre="K_PRE", dom=st["dom"],
                        xing=st["xing"], refs=st["refs"], fuse="HCHVF400", bleeder=False)
    B.flag("+24V")                                         # live 24 V: the aux block (stage 6)
    st["sheet"] += 3
    st["port"] = pb
    B.new_sheet("%02d_dcterm" % st["sheet"], "DC terminals, insulation test",
                "DC terminals 250 A, X / Y capacitors, board entries, removable PE link,\n"
                "port status to PCS_PC, insulation monitor (port.lean_imd)")
    B.block("DC terminals and filter", "B_TERM+/- (250 A), X 2.2 uF 1300 V across the port, Y1 4.7 nF pole - PE_T (removable "
            "PE link for the hipot).\nBoard taps B_IN+ / B_IN- by short links.")
    for pol in "+-":
        B.part("TERM250", {"1": "B_TERM" + pol}, value="DC terminal B%s 250 A (CUSTOM)" % pol)
    B.part("CM_RING", {"1": "B_TERM+", "2": "B_IN+", "3": "B_TERM-", "4": "B_IN-"})
    B.part("XCAP_2U2", {"1": "B_IN+", "2": "B_IN-"}, value="X 2.2u 1300V RFQ")
    for pol in "+-":
        st["xing"].add(B.part("VY1_4N7", {"1": "B_IN" + pol, "2": "PE_T"}).ref)
    for net, k in (("DC+", 3), ("B_T-", 3), ("B_IN+", 1), ("B_IN-", 1), ("B_T+", 1)):
        for _ in range(k):
            B.part("STUD", {"1": net})
    B.block("Port status to PCS_PC", "HOLD = B_HOLD (|I| >= 0.97-1.03 kA: contactor held). MOV_OK high while the varistor "
            "monitor loop conducts.\nPort over-current (387-413 A) pulls FLT_N low (open drain): the latch trips "
            "without firmware.")
    B.R("10k", "+3V3", "MOV_OK", note="high = varistor monitor loop intact")
    for g, d in (("B_SPD_N", "MOV_OK"), ("B_OC", "FLT_N")):
        B.part("BSS138BK", {"1": g, "3": d, "2": "GND"})
    B.block("PE", "PE_T (Y capacitors, MOV3, IMD strings) reaches PE through a removable strap (open for the hipot).")
    B.part("PE_LINK", {"1": "PE_T", "2": "PE"})
    B.part("PE_BOND", {"1": "PE_D"}, value="PE bond M4 (IMD divider reference)")
    B.block("Insulation monitor (port.lean_imd)", "Battery side, contactors open (IEC 62109-2 style check before connecting):\n"
                                                  "IMD_SW1 = string B_IN+ -> PE, IMD_SW2 = PE -> DC-; VPE to PCS_PC.")
    port.lean_imd(B, "B_IN+", "DC-", "PE_T", "PE_D", st["refs"], "IMD_SW1", "IMD_SW2", "+5V", "GND", "+3V3", "AGND",
                  st["dom"], st["xing"])
    nets = B.D.nets()
    for c, n in PC_RENAME.items():
        assert n in nets, "PCS_PC %s: port function no longer drives %s" % (c, n)


def sheet_acport(B, st):
    """AC port: K1 and K2 in series (chassis, RFQ) on fail-safe drivers, CM choke, terminals, varistors + Y1 to PE_T, RCM."""
    st["sheet"] += 1
    B.new_sheet("%02d_acport" % st["sheet"], "AC port: contactors, SPD, RCM",
                "L2 out -> K1 -> K2 -> CM choke -> AC terminals; coil drivers (fail-safe high side);\n"
                "3 x TVT25751 + 3 x Y1 to PE_T; type-B RCM sensor")
    B.block("AC contactors K1, K2 (chassis, RFQ): two 3-pole contactors in series",
            "Relay test before every connection: K1 alone, then K2 alone; the voltage across the open one is VG - VC\n"
            "(stage-3 dividers). Close only after synchronisation, open only at zero current (firmware).")
    B.part("KAC3", {"1": "L2O_A", "3": "L2O_B", "5": "L2O_C", "2": "KM_A", "4": "KM_B", "6": "KM_C", "7": "AC_K1_COIL",
                    "8": "GND"}, value="K1 AC-1 250A RFQ")
    B.part("KAC3", {"1": "KM_A", "3": "KM_B", "5": "KM_C", "2": "K2O_A", "4": "K2O_B", "6": "K2O_C", "7": "AC_K2_COIL",
                    "8": "GND"}, value="K2 AC-1 250A RFQ")
    B.block("Coil drivers (port._lean_drv): BSS138 -> IRFR9214 from the live 24 V, S2M + SMBJ33A clamp",
            "K1 from K_A (= K_AC1), K2 from K_AC2; logic lost = coil off. The contactors' DC coil modules economise.")
    port._lean_drv(B, "AC", "_K1", "K_A", "AC_K1_COIL", "+24V", "GND")
    port._lean_drv(B, "AC", "_K2", "K_AC2", "AC_K2_COIL", "+24V", "GND")
    B.block("CM choke (chassis, CUSTOM) and AC terminals", "One turn of the three phase busbars through the cores; "
            "terminals L1-L3 250 A; PE terminal on the chassis.")
    B.part("CM_PCS", {"1": "K2O_A", "2": "AC_T_A", "3": "K2O_B", "4": "AC_T_B", "5": "K2O_C", "6": "AC_T_C"},
           value="CM choke (CUSTOM)")
    for ph in "ABC":
        B.part("TERM250", {"1": "AC_T_" + ph}, value="AC terminal L%d 250 A (CUSTOM)" % ("ABC".index(ph) + 1))
    B.block("AC surge protection (type II) and Y1", "Per phase a thermally protected varistor TVT25751 (465 V AC) and a "
            "Y1 4.7 nF to PE_T;\nthe removable PE link opens them for the hipot. Monitor leads to test points (OPEN 5).")
    for ph in "ABC":
        st["xing"].add(B.part("MOV_TVT25", {"1": "AC_T_" + ph, "2": "PE_T", "3": "MOV_AC_" + ph}).ref)
        st["dom"]["MOV_AC_" + ph] = "PE"
        B.TP("MOV_AC_" + ph)
        st["xing"].add(B.part("VY1_4N7", {"1": "AC_T_" + ph, "2": "PE_T"}).ref)
    B.block("Residual-current sensor (type B, RFQ)", "Around L1-L3 between the CM choke and the terminals; RCM analog out "
            "and RCM_TST to PCS_X.")
    B.part("RCM_B", {"1": "+5V", "2": "AGND", "3": "RCM_S", "4": "RCM_TST"}, value="RCM type B RFQ")
    B.R("100R", "RCM_S", "RCM")
    B.C("1n", "RCM", "AGND", diel="C0G", tol="5%")
    B.C("100n", "+5V", "AGND")


def build_design():
    B = L.Builder(PROJECT, "PCS-P125 power", REV, DATE, CATALOG, rails=["+24V", "+5V", "+3V3"],
                  returns=["GND", "AGND", "PE"],
                  subtitle="PCS-P125: two-level SiC bridge (6 x SG2M040170HJ per switch), split DC link, LCL, ports",
                  comment1="Live board referenced to DC-; gate islands functional; HV-PE basic",
                  comment4="Not bench-validated. Values read from sim/out/pcs_design/pcs_spec.json; checks: design_check()")
    st = dict(iso=set(), xing=set(), dom={"PE_T": "PE", "PE_D": "PE"}, gd={}, legs=list(LEGS))
    sheet_pc(B, st)
    sheet_dclink(B, st)
    for ph in st["legs"]:
        sheet_leg(B, ph, st)
    st["sheet"] = 2 + len(st["legs"])
    for ph in st["legs"]:
        sheet_gd(B, ph, st)
    for ph in st["legs"]:
        sheet_filter(B, ph, st)
    sheet_acsense(B, st)
    sheets_dcport(B, st)
    sheet_acport(B, st)
    sheet_pe(B, st)
    pv_power.sheets_supplies(B, 3, st, no=st["sheet"] + 1, taps=("B_T+", "DC+", "DC-"),
                             gd_what="gate bias of legs b and c, +3V3 (TPS62130)")
    st["todo"] = pending(B)
    return B, st


# ------------------------------------------------------------------------------------------------ design check
def design_check(B, st):
    """Every number behind this board against pcs_spec.json and the data sheets. Returns [lines]."""
    out = []
    say = lambda f, *a: out.append(f % a)
    parts = list(B.D.parts.values())
    key = lambda p_: p_.lib_id.split(":")[1]
    cnt = lambda k: sum(1 for p_ in parts if key(p_) == k)
    n_leg = len(st["legs"])

    # ---- DC link: capacitance, voltage use, ripple current (pcs_spec dc_link)
    halves = {h: [p_ for p_ in parts if key(p_) == "C3D1U147" and set(p_.pins.values()) == s]
              for h, s in (("upper", {"DC+", "M"}), ("lower", {"M", "DC-"}))}
    assert all(len(v) == DCL["per_half"] for v in halves.values()), "DC-link halves"
    c_half = DCL["per_half"] * F_DC["c"]
    assert abs(c_half * 1e6 - DCL["C_half_uF"]) < 0.5, "C per half differs from pcs_spec"
    u_w = DCL["voltage_use"]["Uw_950V_over_UN70"]
    assert u_w <= 0.85 and V_HALF_TRIP <= 1.15 * F_DC["un85"] and DCL["I_per_cap_A"] <= 0.7 * F_DC["imax"], "DC-link film"
    say("DC link: %d + %d x C3D1U147 = %.0f uF per half, %.0f uF in series; 475 V per half at 950 V = %.2f x U_N(70 C); "
        "per-half OV trip %.0f V <= 1.15 x U_N(85 C) %.0f V; ripple %.1f A rms per capacitor (pcs_spec worst corner) = "
        "%.0f %% of I_max %.1f A (70 C, 10 kHz); life %.0f kh at 45 C / 950 V (pcs_spec)", DCL["per_half"], DCL["per_half"],
        c_half * 1e6, c_half / 2 * 1e6, u_w, V_HALF_TRIP, 1.15 * F_DC["un85"], DCL["I_per_cap_A"],
        100 * DCL["I_per_cap_A"] / F_DC["imax"], F_DC["imax"], DCL["life"]["45C_950V"]["life_h"] / 1e3)

    # ---- bleeders: discharge time (worst case), element stress, balance
    r_el = e96(BL_R)
    r_half = BL_N * r_el
    r_div = 6e6 + 4.99e3
    c_total = c_half / 2
    r_tot_wc = 1 / (1 / (2 * r_half * 1.01) + 1 / r_div)                # R +1 %, C +10 %, the V(DC+) divider in parallel
    t60 = r_tot_wc * c_total * 1.1 * math.log(V_DC_MAX / 60.0) / 60.0
    t60_nom = 2 * r_half * c_total * math.log(V_DC_MAX / 60.0) / 60.0
    v_el, p_lim = V_HALF_TRIP / BL_N, 0.5 * (155.0 - T_BOARD) / (155.0 - 70.0)
    p_el = v_el ** 2 / r_el
    p_950 = 950.0 ** 2 / (2 * r_half)
    assert t60 <= LABEL_MIN and v_el <= 200.0 and p_el <= p_lim, "bleeder"
    say("Bleeders: %d x %s 1210 per half = %s (pcs_spec %s); DC link %.0f uF: %.0f -> 60 V in %.1f min nominal, %.1f min "
        "worst case (R +1 %%, C +10 %%, V(DC+) divider in parallel) <= label 'wait %.0f min'; element %.0f V at the %.0f V "
        "per-half trip (200 V working), %.3f W vs %.2f W derated to %.0f C; %.2f W at 950 V; the equal strings balance "
        "the halves statically (no midpoint control, two-level)", BL_N, pv_power.gdrv.ohm(r_el), pv_power.gdrv.ohm(r_half),
        pv_power.gdrv.ohm(DCL["bleeder"]["R_per_half_ohm"]), c_total * 1e6, V_DC_MAX, t60_nom, t60, LABEL_MIN, v_el,
        V_HALF_TRIP, p_el, p_lim, T_BOARD, p_950)

    # ---- DC-link dividers into PCS_PC VB / VA
    vmid = (3.0 * 0.998 * 16.35 / 29.8 * 0.999, 3.0 * 1.002 * 16.35 / 29.8 * 1.001)   # lean_refs ladder (gen/port.py)
    v_hi = vmid[1] + 1.1 * V_DC_MAX / 1203.0
    assert v_hi <= 3.0, "VB above the 3.0 V ADC range at 1.1 x the OV trip"
    say("DC-link sensing: VB = VMID + V(DC+)/1203, VA = VMID + V(M)/1203 (6 x 1 M ARHV06 + 4.99 k, 4.7 nF: 30 us), VMID "
        "%.3f-%.3f V; 1.1 x %.0f V -> %.3f V < 3.0 V (PV-CTL ADC range); the control board's OV comparators must be set "
        "to %.0f V on VB and %.0f V on VA (PCS assembly)", vmid[0], vmid[1], V_DC_MAX, v_hi, V_DC_MAX, V_HALF_TRIP)

    # ---- power stage as drawn against the hand-over
    lg = CG["leg"]
    n_dev, n_dec = cnt("SG2M040170HJ"), cnt("FILM2U2")
    assert n_dev == 2 * N_PAR * n_leg and cnt("PAD_AL2O3") == n_dev, "device / pad count"
    assert n_dec == lg["decoupling_parts"] * n_leg and abs(n_dec / n_leg * F22["c"] - lg["c_dec"]) < 1e-9, "decoupling"
    r_d = pvcell.DAMP_SER * pvcell.DAMP_R / pvcell.DAMP_PAR / N_SUB
    c_d = N_SUB * pvcell.DAMP_C[0] / pvcell.DAMP_C[1]
    assert abs(r_d - lg["r_damp"]) / lg["r_damp"] < 0.01 and abs(c_d - lg["c_damp"]) / lg["c_damp"] < 0.01, "damper"
    dev = pvcell.DEV
    v_pk = CG["worst_1050V_450A_30nH"]["v_pk"]
    assert v_pk <= CG["chosen"]["v_limit_V"] <= 0.85 * dev["vdss"], "device peak"
    say("Power stage (as drawn, %d of 3 legs): %d x SG2M040170HJ (%d per switch) on Al2O3 pads; per device 10 k + 1 nF "
        "gate-source at the pins; per leg %d x FCSA3DS225 (%.1f uF, pcs_spec %.1f uF) and %d dampers (%.2f ohm / %.2f nF "
        "per leg, pcs_spec %.2f ohm / %.2f nF); worst turn-off peak %.0f V at 1050 V / 450 A (pcs_spec commutation deck) "
        "<= %.0f V = 0.85 x 1700 V. BOM NOTE: one lot per switch, V_GS(th) binned +/-0.25 V or an incoming sharing test",
        n_leg, n_dev, N_PAR, lg["decoupling_parts"], n_dec / n_leg * F22["c"] * 1e6, lg["c_dec"] * 1e6, N_SUB, r_d,
        c_d * 1e9, lg["r_damp"], lg["c_damp"] * 1e9, v_pk, CG["chosen"]["v_limit_V"])
    # ---- gate drive (gdrv preset SC40X6 checks + this board's rails, dead time at +5V, DESAT against the PCS currents)
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        g_out, _ = gdrv.design_check()
    gk = GD["name"]
    vt_r = gdrv.ext_bias_numbers(GATE_V)["vt"]
    f_sw = SPEC["design"]["fsw_Hz"]
    p_ch = (GD["qg"] * N_PAR * vt_r[1] * f_sw + gdrv.NSI["icc2"] * vt_r[1] + N_PAR * 18.6 ** 2 / 10e3 + vt_r[1] ** 2 / 10e3)
    e = gdrv.ext_bias_numbers(GATE_V, p_ch=p_ch, n_ch=2)
    on_w, off_w = gdrv.VGS_WIN[GATE_V], (-GATE_V[1] - 0.3, -GATE_V[1] + 0.3)
    assert e["von"][0] - on_w[0] >= 0.1 and on_w[1] - e["von"][1] >= 0.1 and off_w[0] <= -e["v3"][1] and -e["v3"][0] <= off_w[1]
    assert abs(CG["gate"]["P_gate_W_per_channel"] - GD["qg"] * N_PAR * 21.5 * f_sw) < 0.01, "gate charge vs pcs_spec"
    assert 2 * p_ch <= gdrv.BIAS_XF["n_sec"] * gdrv.P_CH[GATE_V], "two 6-gate channels above T_BIAS4's design load (4 PV channels)"
    v5_old, pvcell.V5 = pvcell.V5, pv_power.V5
    try:
        t_lo, t_hi = pvcell.stretch5(*GD["deadtime"])
    finally:
        pvcell.V5 = v5_old
    skew = gdrv.NSI["skew"] + pvcell.AHCT["tpd"][1] - pvcell.AHCT["tpd"][0]
    dt = (t_lo + pvcell.LVC17_5V["tpd"][0] - skew, t_hi + pvcell.LVC17_5V["tpd"][1] + skew)
    t_eng = float(g_out["mclamp" + gk].split("clamps on <= ")[1].split(" ns")[0]) * 1e-9
    assert dt[0] >= max(GD["t_off"] + 20e-9, t_eng), "hardware dead time at +5V below turn-off / clamp engagement"
    (tr_lo, tr_hi), _, (bl_lo, bl_hi) = gdrv.desat_numbers(gdrv.DESAT_CLASS[GD["vclass"]])
    v_trip = CG["desat"]["V_DS_at_OC_trip_V"]
    i_ol = (1.2 * 216.0 * math.sqrt(2) + SPEC["design"].get("ripple_pk_A", 31.0)) / N_PAR      # 200 ms overload peak
    v_ol = i_ol * GD["v_on_ohm175"] if "v_on_ohm175" in GD else i_ol * 3.13 / 36.2           # SC40: 3.13 V at 36.2 A, 175 C
    dvdt = CG["worst_1050V_450A_30nH"]["dvdt_V_per_ns"]
    assert gdrv.NSI["cmti"] >= 1.2 * dvdt and bl_lo >= GD["blank"][0], "CMTI / blanking"
    say("Gate drive (gdrv preset '%s', bias 'ext'): %d channels on %d x bias_phase (T_BIAS4 1:%g, 2 of 4 secondaries); "
        "per channel %.2f W regulated (gate charge %.0f nC x %.1f V x %.0f kHz = %.2f W, pcs_spec %.2f W; 6 x 10k, driver) "
        "-> VGS on %.2f-%.2f V in %.1f-%.1f V, off -%.2f..-%.2f V; bias input %.2f W per phase from +5V. %s; %s",
        gk, 2 * n_leg, n_leg, gdrv.XF_N, p_ch, GD["qg"] * N_PAR * 1e9, vt_r[1], f_sw / 1e3,
        GD["qg"] * N_PAR * vt_r[1] * f_sw, CG["gate"]["P_gate_W_per_channel"], e["von"][0], e["von"][1], on_w[0], on_w[1],
        e["v3"][0], e["v3"][1], e["p_in"], g_out["ipk" + gk].strip(), g_out["boost" + gk].strip()[:200])
    say("Dead time at the gates (stretch %s/%s on the SN74LVC1G17 at +5V %.2f-%.2f V, NSI6651 skew + AHCT spread %.0f ns): "
        "%.0f-%.0f ns >= max(turn-off %.0f ns + 20 ns ESTIMATE, Miller clamps of six gates on %.0f ns); a firmware dead band "
        "below %.0f ns is extended by the hardware (hand-over: 300 ns) - OPEN (2)", gdrv.ohm(GD["deadtime"][0]),
        gdrv.farad(GD["deadtime"][1]), pv_power.V5[0], pv_power.V5[1], skew * 1e9, dt[0] * 1e9, dt[1] * 1e9,
        GD["t_off"] * 1e9, t_eng * 1e9, (t_hi + pvcell.LVC17_5V["tpd"][1]) * 1e9)
    say("DESAT (3 x US1M, Rs 100R): trips at V_DS %.2f-%.2f V, blanking %.0f-%.0f ns; V_DS at the +/-450 A trip %.2f V "
        "(pcs_spec, 75 A per device, 175 C) - DESAT is the layer above the comparator window; at the 200 ms overload peak "
        "(1.2 x 216 A x 1.41 + ripple = %.0f A per device) the on-state voltage is about %.1f V at 175 C (SC40 3.13 V at "
        "36.2 A, linear): lowest trip / on-state = %.2f, a trip in that overload is possible: OPEN (1). CMTI 150 V/ns vs %.0f V/ns "
        "(pcs_spec worst turn-off)",
        tr_lo, tr_hi, bl_lo * 1e9, bl_hi * 1e9, v_trip, i_ol, v_ol, tr_lo / v_ol, dvdt)
    # ---- LCL filter and phase-current sensing (stage 3)
    l1, l2 = MAGF["l1"]["electrical"], MAGF["l2"]["electrical"]
    assert abs(l1["L_H"] - LCL["L1"]) < 1e-9 and abs(l2["L_H"] - LCL["L2"]) < 1e-9, "inductor design files vs pcs_spec"
    assert all(l1["L_inc_at_minus10pct_part_H"][k] >= 0.999 * l1["L_min_required_H"][k] for k in l1["L_min_required_H"])
    #   (0 A: 107.95 uH against 108.0 uH - the design file's L0 is 119.94 uH, a 0.05 % rounding, accepted here)
    n_cf = cnt("CF_25U")
    assert n_cf == 2 * n_leg and abs(2 * 25e-6 - LCL["Cf"]) < 1e-9 and cnt("CD_10U") == cnt("RD_2R") == n_leg, "C_f / damping"
    assert LCL["Rd"] == 2.0 and LCL["Cd"] == 10e-6 and cnt("CS_PH") == n_leg and cnt("L1_PCS") == cnt("L2_PCS") == n_leg
    i_trip = LIM["I_phase_trip_A"]
    say("LCL filter (pcs_spec lcl, chassis inductors from sim/magnetics.py): L1 %.0f uH rev %s (%.1f kg, at -10 %% tolerance "
        "%.0f uH at %s A >= the %.0f uH required there), L2 %.0f uH rev %s; C_f 2 x 25 uF MKP per phase = %.0f uF, star on M; "
        "damping R_d %.0f ohm + C_d %.0f uF per phase (fallback damping, pcs_spec); resonance %.1f kHz stiff / %.1f kHz at "
        "SCR 5 (pcs_spec). Phase-current sensor (RFQ, OPEN 3): +/-500 A, >= 100 kHz, between L1 and C_f, the window "
        "+/-%.0f A sits at 0.9 of its range; IL1-3 and IL1R-3R through 100R + 1 nF (0.13 us) to PCS_PC",
        LCL["L1"] * 1e6, MAGF["l1"]["revision"], MAGF["l1"]["mass_kg"], l1["L_inc_at_minus10pct_part_H"]["450 A"] * 1e6,
        "450", l1["L_min_required_H"]["450 A"] * 1e6, LCL["L2"] * 1e6, MAGF["l2"]["revision"], LCL["Cf"] * 1e6,
        LCL["Rd"], LCL["Cd"] * 1e6, SPEC["lcl"]["f_res_Hz"]["stiff"] / 1e3, SPEC["lcl"]["f_res_Hz"]["SCR 5"] / 1e3, i_trip)
    v_ac = 1.2 * 375.0
    hi = vmid[1] + (V_DC_MAX / 2 + v_ac) / 1203.0
    assert hi <= 3.0 and vmid[0] - 1985.0 / 1203.0 >= -0.01, "AC divider range"
    say("AC sensing: VC1-3 (C_f nodes) and VG1-3 (terminal taps) = VMID + V/1203 to PCS_X (6 x 1 M ARHV06 + 4.99 k, 4.7 nF: "
        "6.8 kHz); in operation V(node - DC-) = V_dc/2 +/- 375 V x 1.2 -> up to %.3f V at the 1050 V trip (3.0 V range); "
        "bipolar +/-1985 V readable for the floating terminals with the contactors open; relay test = VG against VC per "
        "phase, one contactor closed at a time (firmware)", hi)
    # ---- DC port (stage 4): lean battery port at 250 A
    lp = port.S["lean"]
    ct = lp["contactors"]["HFE82V"]
    i_cont = DCL["rows"][0]["idc"]                                     # 228.6 A: the largest DC current of the study
    i_fuse = SPEC["ports_and_common_mode"]["dc_port"]
    p_link = FUSE["p_in"] * (230.0 / FUSE["In"]) ** 2
    pre = SPEC["ports_and_common_mode"]["precharge"]
    e_short = 950.0 ** 2 / lp["precharge"]["R_ohm"] * (1.1 * pre["tau_s"] + 30e-3)
    assert ct["ith_85C"] >= I_DC and 230.0 <= 0.75 * FUSE["In"] and FUSE["i2t_clr"] <= ct["sc_I2t_min"], "contactor / fuse"
    assert pre["E_J_950V"] <= 180.0 and e_short <= 600.0 and pre["t_to_10V_s"] < 1.0, "precharge resistor (RPRE_AL rating)"
    r_sh = lp["shunt"]["R_ohm"]
    assert I_DC ** 2 * r_sh / 2 <= 0.5 * 12.0, "shunt element above 50 % of its 12 W"
    say("DC port (gen/port.py lean_port 'battery', interlocks full, OC trip on): HFE82V-300C %.0f A at 85 C vs %.0f A port "
        "(largest study DC current %.0f A); HCHVF1000-400A-38R per pole: 230 A continuous = %.0f %% of In (<= 75 %%), %.0f W "
        "per link, clearing I2t %.0f kA2s <= the contactor's %.0f kA2s; precharge %.0f ohm on %.0f uF: tau %.0f ms, 10 V in "
        "%.2f s, %.0f J at 950 V, shorted bank %.0f J (RPRE_AL: >= 180 J at 80 ms, 600 J); shunt 2 x 200 uOhm: %.1f W at "
        "%.0f A; port OC window %d-%d A on FLT_N, hold-off %d-%d A, polarity enable %d-%d V, precharge dV %.1f-%.1f V "
        "(port_spec lean); bleeder of the port left out (the DC link has its own). OPEN (4): fuse coordination not computed",
        ct["ith_85C"], I_DC, i_cont, 100 * 230.0 / FUSE["In"], p_link, FUSE["i2t_clr"] / 1e3, ct["sc_I2t_min"] / 1e3,
        lp["precharge"]["R_ohm"], DCL["C_series_uF"], pre["tau_s"] * 1e3, pre["t_to_10V_s"], pre["E_J_950V"], e_short,
        I_DC ** 2 * r_sh, I_DC, lp["port_oc_trip_A"][0], lp["port_oc_trip_A"][1], lp["contactor"]["hold_off_band_A"][0],
        lp["contactor"]["hold_off_band_A"][1], lp["polarity_enable_V"][0], lp["polarity_enable_V"][1],
        lp["precharge"]["dV_enable_V"][0], lp["precharge"]["dV_enable_V"][1])
    # ---- AC port (stage 5)
    cm = MAGF["cm_choke"]["electrical"]
    assert cnt("KAC3") == 2 and cm["L_min_H"] >= cm["L_required_H"] and cnt("MOV_TVT25") >= 3 and cnt("RCM_B") == 1
    u_c, tov = 465.0, 1.1 * 400.0                                       # TVT25751 Uc (Thinking TVT p2); IT-earth-fault TOV
    assert u_c >= tov and port.V_POLE <= 0.8 * VY1_VDC_PCS, "AC varistor Uc / Y1 rating"
    say("AC port: K1 + K2 in series (RFQ, AC-1 >= %.0f A, Ui %d V, Uimp %d kV, 24 V DC coil with economiser) on fail-safe "
        "high-side drivers from K_A / K_AC2 (logic lost = coil off); relay test VG vs VC with one contactor closed; CM choke "
        "rev %s: L_min %.0f uH >= %.0f uH required (sim/magnetics.py), %.2f A rms 150 Hz CM at B %.2f T; 3 x TVT25751 "
        "(Uc %.0f V AC >= 1.1 x 400 V for an earth fault in IT) + 3 x Y1 4.7 nF (500 V AC) to PE_T; type-B RCM sensor "
        "(RFQ) to PCS_X RCM / RCM_TST. OPEN (5), (6)", ACC["Ie_AC1_A_min"], ACC["Ui_V"], ACC["Uimp_kV"],
        MAGF["cm_choke"]["revision"], cm["L_min_H"] * 1e6, cm["L_required_H"] * 1e6, cm["I_LF_cm_A_rms"], cm["B_LF_T"], u_c)
    # ---- supplies and the 75 W aux block (stage 6): datasheet maxima, ESTIMATES marked
    IQ, V5_, V33_ = pv_power.IQ, pv_power.V5, pv_power.V33
    p_bias = e["p_in"]                                                   # per phase at the bias primary (above)
    n_ahct5, n_ch_ = 2 + 2 * n_leg, 2 * n_leg
    i_rcm, i_cs = 50e-3, 20e-3                                           # RFQ sensors: ASSUMED supply currents
    i5 = {"gate bias leg a": p_bias / V5_[0], "SN6505B": IQ["sn6505"], "NSI6651 VCC1": n_ch_ * gdrv.NSI["icc1"],
          "AHCT1G08": n_ahct5 * (IQ["ahct"] + 2 * IQ["ahct_d"]), "pull-downs": (n_ahct5 + 1) * V5_[1] / 10e3,
          "phase-current sensors (ASSUMED 20 mA)": n_leg * i_cs, "RCM sensor (ASSUMED 50 mA)": i_rcm,
          "REF3030E + ladders": IQ["ref3030"] + 3.0 / 29.8e3 + 3.0 / 30.2e3, "IMD SSR LEDs (test)": 2 * (V5_[1] - 1.2) / 330.0}
    i33 = {"OPA2388 x %d" % cnt("OPA2388"): 2 * cnt("OPA2388") * IQ["opa2388"],
           "TLV3502 x %d" % cnt("TLV3502"): 2 * cnt("TLV3502") * IQ["tlv3502"], "LVC gates": 12 * IQ["lvc"],
           "pull-ups (RDY, MOV_OK)": V33_[1] / 4.99e3 + V33_[1] / 10e3, "control board (allocation)": pv_power.CTRL_LOAD["+3V3"]}
    s33 = sum(i33.values())
    i5g = {"gate bias legs b, c": 2 * (p_bias / V5_[0] + IQ["sn6505"]), "AHCT1G08 (BIAS_EN5G)": IQ["ahct"] + IQ["ahct_d"],
           "TPS62130 -> +3V3 (eta 0.85 ASSUMED)": s33 * V33_[1] / (0.85 * V5_[0])}
    s5, s5g = sum(i5.values()), sum(i5g.values())
    assert s5 <= 0.8 * 2.0 and s5g <= 0.8 * 2.0 and s33 <= 0.8 * 3.0, "5 V / 3.3 V converter load above 80 %"
    p5_out = (s5 + s5g) * V5_[1]
    ec = pv_power.ECON
    p_coils = ec["hold_W_max"] + 2 * 4.0                                 # DC contactor economised + 2 AC coils (RFQ <= 4 W)
    p_live = p5_out / 0.85 + p_coils
    rt = pv_power.aux_rating(4)       # the aux hardware is one design for both PV builds; its full set is the '4 phases' one
    p_selv = 3 * 12.85 * 1.1 + 0.6                                       # 3 fans x 12 W rated + fan buck 10 % + SELV logic
    over = p_live > rt["live_W"]
    say("Supplies (datasheet maxima; RFQ sensors ASSUMED): +5V (LMR38020, 2 A) %.2f A = %.0f %% (%s); +5V_GD %.2f A = %.0f %% "
        "(%s); +3V3 (TPS62130, 3 A) %.0f mA = %.0f %%. Live 24 V from the aux: 5 V outputs %.1f W / 0.85 + DC contactor on "
        "its economiser %.2f W + 2 AC contactor coils (RFQ hold <= 4 W each) = %.1f W against the aux block's live rating "
        "%.1f W (aux75_spec, the design's full set '4 phases'): %s; SELV: 3 fans + fan buck + logic %.1f W vs %.1f W; "
        "total %.1f W vs %.1f W",
        s5, 50 * s5, ", ".join("%s %.0f mA" % (k, v * 1e3) for k, v in i5.items()), s5g, 50 * s5g,
        ", ".join("%s %.0f mA" % (k, v * 1e3) for k, v in i5g.items()), s33 * 1e3, 100 * s33 / 3.0, p5_out, ec["hold_W_max"],
        p_live, rt["live_W"], "OVER by %.1f W - OPEN (8): the live winding of AUX-T1 must be re-rated for the PCS (more "
        "gate bias, two AC coils) or the AC coils moved to the SELV side" % (p_live - rt["live_W"]) if over else
        "inside at %.0f %% - OPEN (8): the AC coils' hold power is an RFQ assumption" % (100 * p_live / rt["live_W"]),
        p_selv, rt["selv_W"], p_live + p_selv, rt["total_W"])
    # ---- trip bands and responses this board provides (the control board's ladders are re-valued for the PCS)
    say("Trip sources on this board: DESAT per channel (%.1f-%.1f V, booster); DC-port over-current %d-%d A (TLV3502 window on "
        "the shunt, open drain on FLT_N, no firmware); hold-off %d-%d A (HOLD); RDY low on a missing bias rail, +5V / +5V_GD "
        "below 4.52-4.69 V or the aux live 24 V outside its window. On the control board (PCS assembly, its ladders "
        "re-valued): phase windows +/-%.0f A on IL1-3 (sensor gain RFQ), DC OV %.0f V on VB and %.0f V on VA, heatsink and "
        "L1 OT, open probe, heartbeat, ENABLE; CMPSS / ADC limits as the second layer (pcs_spec firmware requirements)",
        tr_lo, tr_hi, lp["port_oc_trip_A"][0], lp["port_oc_trip_A"][1], lp["contactor"]["hold_off_band_A"][0],
        lp["contactor"]["hold_off_band_A"][1], LIM["I_phase_trip_A"], V_DC_MAX, V_HALF_TRIP)
    say("Thermal inputs: NTC1-3 lug probes on the earthed heatsink sections (%d x R_sa %.3f K/W at %.0f m3/h, pcs_spec), "
        "NTC5-7 in the L1 windings, NTC9 at the DC-link bank (Murata NCP15, 10 k 1 %%); three 120 mm fans on PV-CTL's SELV "
        "headers (%s)", HSK["sections"], HSK["R_sa_K_per_W_per_section"], HSK["flow_m3h_per_section"], HSK["fans"])
    say("Pending PCS_PC / PCS_X nets (test points until their stage): %s", ", ".join(st["todo"]) or "none")
    return out


def vranges():
    """DC range of the assessed nets against DC- (LIVE) or PE; switching / gate / bleeder-internal nets unranged."""
    r = {"GND": (0.0, 0.0), "AGND": (0.0, 0.0), "DC-": (0.0, 0.0), "PE": (0.0, 0.0), "+3V3": (0.0, 3.47),
         "+5V": (0.0, 5.25), "DC+": (0.0, V_DC_MAX), "VA": (0.0, 3.3), "VB": (0.0, 3.3), "RDY": (0.0, 3.47),
         "FLT_N": (0.0, 3.47)}
    r.update({n: (0.0, 3.47) for n in ["EN", "BIAS_EN", "K_A", "K_B", "K_PRE", "K_AC2", "IMD_SW1", "IMD_SW2", "RCM_TST",
                                       "IL4", "IL4R"] + ["PWM%d" % k for k in range(1, 9)]})
    r.update({"M": (0.0, V_HALF_TRIP)})
    r.update({n: (0.0, 5.0) for n in ["IL%d" % k for k in range(1, 4)] + ["IL%dR" % k for k in range(1, 4)]})
    r.update({n: (0.0, 3.3) for n in ["VC%d" % k for k in range(1, 4)] + ["VG%d" % k for k in range(1, 4)]})
    v5 = pv_power.V5
    r.update({n: (0.0, v5[1]) for n in ["EN5", "BIAS_EN5"] + [w + "_5V" for ph in LEGS for w in PWM[ph]]})
    e = gdrv.ext_bias_numbers(GATE_V)
    for ph in LEGS:
        for sw_ in "HL":
            t = ph + sw_
            r.update(gdrv.net_ranges(t, GATE_V, "VDD_" + t, "VEE_" + t,
                                     tuple("KS_" + dev_tag(ph, sw_, k) for k in range(1, N_PAR + 1)), gnd="GND", vcc="+5V",
                                     vin="+24V"))
            r.update({"%s_%s" % (x, t): (0.0, v5[1]) for x in ("DTS", "DTB", "PG")})
            b = lambda x: "%s_PH%s%s" % (x, ph, t)
            r[b("RAW")] = (-e["v3"][1] + e["raw"][0], -e["v3"][0] + e["raw"][1])
            r.update({b(x): (-e["v3"][1] + gdrv.VREF431[0], -e["v3"][0] + gdrv.VREF431[1]) for x in ("RREF", "CREF")})
    r.update({"+5V": v5, "+5V_GD": v5, "+24V": (pv_power.V24[0], pv_power.S75["nets"]["LIVE"][1]), "+3V3": pv_power.V33,
              "FB_5V": (0.985, 1.015), "FB_5VG": (0.985, 1.015), "PG_5V": (0.0, v5[1]), "PG_5VG": (0.0, v5[1]),
              "RT_5V": (0.0, 1.5), "RT_5VG": (0.0, 1.5), "FB_3V3": (0.78, 0.83), "SS_3V3": (0.0, 1.3), "DIS_G": (0.0, 10.4),
              "SUP5G_SNS": (0.0, v5[1] * pv_power.SUP_DIV[1] / sum(pv_power.SUP_DIV)), "BIAS_EN5G": (0.0, v5[1]),
              "NTC9": (0.0, 3.47)})
    r.update({"NTC%d" % k: (0.0, 3.47) for k in (1, 2, 3, 5, 6, 7)})
    return r


def domain_for(st):
    return lambda net: (st["gd"].get(net) or st["dom"].get(net) or ("PE" if net == "PE" else "SELV" if
                                                                     net in st.get("selv", ()) else "LIVE"))


if __name__ == "__main__":
    B, st = build_design()
    lines = design_check(B, st)
    B.D.root_notes = ["CALCULATED, not measured: design_check() asserts every number on each build (outputs/%s_design_"
                      "check.txt); values are read from sim/out/pcs_design/pcs_spec.json at build time." % PROJECT,
                      "Domains: LIVE (DC- = GND = AGND, the AC side included), GD_<leg><H/L> gate islands (functional: "
                      "NSI6651, T_BIAS4), PE (pads, heatsink, NTC lugs, Y / varistor / IMD crossings), SELV (AUX-T1).",
                      "Stages 1-6 drawn (gen/pcs_power.py header); OPEN items listed there and in the design check."]
    rv = vranges()
    for k_, v_ in st["aux"]["vrange"].items():          # the aux block's own nets (status = RDY keeps this board's range)
        rv.setdefault(k_, v_)
    rc = L.build(B, waivers=dict(gdrv.ERC_WAIVERS), domain_of=domain_for(st), isolators=st["iso"], crossings=st["xing"],
                 vrange=lambda n: rv.get(n) or port.vrange_lean(n))
    with open(os.path.join(L.REPO, "hardware", PROJECT, "outputs", PROJECT + "_design_check.txt"), "w") as f:
        f.write("CALCULATED by gen/pcs_power.py design_check() - not measured, not bench-validated.\n")
        f.write("\n".join(lines) + "\n")
    sys.exit(rc)
