"""Insulation coordination for the whole DC-DC platform (REQUIREMENTS ECO-10, ECO-11, PV-C4..C6, DAB-10/11).

Run:  .venv/bin/python sim/insulation.py
Out:  sim/out/insulation/report.md, insulation_spec.json, barrier_audit.csv   (exit code != 0 if a self-check fails)

What it does
  1. Standard tables (IEC 60664-1, IEC/EN 62477-1, IEC 62109-1) as data - TRANSCRIBED FROM MEMORY of the standards,
     to be verified against the purchased editions before design freeze. Where a value was uncertain the
     conservative one is used and the choice is named next to the table.
  2. Circuits and their worst-case voltages, read from the designers' spec files (cell_spec, dab_spec, aux_hv_spec,
     port_spec) so that a design change moves the coordination with it.
  3. Requirements per insulation type and circuit pair at 2000 / 3000 / 4000 m, with and without the port SPD.
  4. Barrier audit: every Domain=ISOLATOR / CROSSING part in hardware/*/outputs/*_netlist.xml (re-read on every run)
     plus the barriers that are not single parts (heatsink pads, NTC mounts, magnetics, module base plates ...),
     compared with datasheet ratings (PDF + page cited). Verdicts are computed, never typed.
  5. Self-checks: unknown barrier part -> fail; reinforced PASS without datasheet evidence -> fail; every FAIL reported.
Nothing here is measured. Every rating is from a datasheet on file; every voltage is calculated or simulated.
"""
import csv
import glob
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sim", "out", "insulation")
DS = "docs/datasheets/"
ALTS = (2000, 3000, 4000)

# =====================================================================================================================
# 1. STANDARD TABLES - transcribed from memory, VERIFY against the purchased editions before design freeze
# =====================================================================================================================
STD_NOTE = ("All standard tables in this script are TRANSCRIBED FROM MEMORY of IEC 60664-1 (ed. 2.0:2007 numbering; "
            "ed. 3.0:2020 renumbered some tables), IEC/EN 62477-1:2012+A1 and IEC 62109-1:2010 - verify against the "
            "purchased editions before design freeze. No standard text could be bought for this project.")

# IEC 60664-1 Table F.1 (62477-1 uses the same values for its impulse table): rated impulse voltage [V] for OVC I..IV,
# keyed by the line-to-neutral (here: pole-to-earth, first fault) voltage "up to and including".
TABLE_F1 = {50: (330, 500, 800, 1500), 100: (500, 800, 1500, 2500), 150: (800, 1500, 2500, 4000),
            300: (1500, 2500, 4000, 6000), 600: (2500, 4000, 6000, 8000), 1000: (4000, 6000, 8000, 12000)}
PREFERRED_IMP = (330, 500, 800, 1500, 2500, 4000, 6000, 8000, 12000)   # 60664-1 4.2.3 preferred series [V]

# IEC 60664-1 Table F.2, case A (inhomogeneous field), clearance [mm] to withstand impulse, <= 2000 m: (kV, PD1, PD2, PD3)
TABLE_F2 = [(0.33, 0.01, 0.2, 0.8), (0.40, 0.02, 0.2, 0.8), (0.50, 0.04, 0.2, 0.8), (0.60, 0.06, 0.2, 0.8),
            (0.80, 0.10, 0.2, 0.8), (1.0, 0.15, 0.2, 0.8), (1.2, 0.25, 0.25, 0.8), (1.5, 0.5, 0.5, 0.8),
            (2.0, 1.0, 1.0, 1.0), (2.5, 1.5, 1.5, 1.5), (3.0, 2.0, 2.0, 2.0), (4.0, 3.0, 3.0, 3.0), (5.0, 4.0, 4.0, 4.0),
            (6.0, 5.5, 5.5, 5.5), (8.0, 8.0, 8.0, 8.0), (10.0, 11.0, 11.0, 11.0), (12.0, 14.0, 14.0, 14.0),
            (15.0, 18.0, 18.0, 18.0), (20.0, 25.0, 25.0, 25.0)]

# IEC 60664-1 Table F.7a, case A: clearance [mm] to withstand steady-state / recurring-peak / TOV voltage (kV peak)
TABLE_F7 = [(0.33, 0.01), (0.4, 0.02), (0.5, 0.04), (0.6, 0.06), (0.8, 0.13), (1.0, 0.26), (1.2, 0.42), (1.5, 0.76),
            (2.0, 1.27), (2.5, 1.8), (3.0, 2.4), (4.0, 3.8), (5.0, 5.7), (6.0, 7.9), (8.0, 11.0), (10.0, 15.2)]

# IEC 60664-1 Table F.4 (ed.3: F.5): creepage [mm] vs working voltage (V rms or DC).
# Columns: PWB PD1, PWB PD2 (printed wiring, CTI >= 175, defined up to 1000 V only), then other materials:
# PD1 (all groups), PD2 group I / II / III, PD3 group I / II / III (IIIa and IIIb take the same column).
TABLE_F4 = [(10, .025, .04, .08, .40, .40, .40, 1.0, 1.0, 1.0), (12.5, .025, .04, .09, .42, .42, .42, 1.05, 1.05, 1.05),
            (16, .025, .04, .10, .45, .45, .45, 1.1, 1.1, 1.1), (20, .025, .04, .11, .48, .48, .48, 1.2, 1.2, 1.2),
            (25, .025, .04, .125, .50, .50, .50, 1.25, 1.25, 1.25), (32, .025, .04, .14, .53, .53, .53, 1.3, 1.3, 1.3),
            (40, .025, .04, .16, .56, .80, 1.1, 1.4, 1.6, 1.8), (50, .025, .04, .18, .60, .85, 1.2, 1.5, 1.7, 1.9),
            (63, .04, .063, .20, .63, .90, 1.25, 1.6, 1.8, 2.0), (80, .063, .10, .22, .67, .95, 1.3, 1.7, 1.9, 2.1),
            (100, .10, .16, .25, .71, 1.0, 1.4, 1.8, 2.0, 2.2), (125, .16, .25, .28, .75, 1.05, 1.5, 1.9, 2.1, 2.4),
            (160, .25, .40, .32, .80, 1.1, 1.6, 2.0, 2.2, 2.5), (200, .40, .63, .42, 1.0, 1.4, 2.0, 2.5, 2.8, 3.2),
            (250, .56, 1.0, .56, 1.25, 1.8, 2.5, 3.2, 3.6, 4.0), (320, .75, 1.6, .75, 1.6, 2.2, 3.2, 4.0, 4.5, 5.0),
            (400, 1.0, 2.0, 1.0, 2.0, 2.8, 4.0, 5.0, 5.6, 6.3), (500, 1.3, 2.5, 1.3, 2.5, 3.6, 5.0, 6.3, 7.1, 8.0),
            (630, 1.8, 3.2, 1.8, 3.2, 4.5, 6.3, 8.0, 9.0, 10.0), (800, 2.4, 4.0, 2.4, 4.0, 5.6, 8.0, 10.0, 11.0, 12.5),
            (1000, 3.2, 5.0, 3.2, 5.0, 7.1, 10.0, 12.5, 14.0, 16.0),
            (1250, None, None, 4.2, 6.3, 9.0, 12.5, 16.0, 18.0, 20.0), (1600, None, None, 5.6, 8.0, 11.0, 16.0, 20.0, 22.0, 25.0),
            (2000, None, None, 7.5, 10.0, 14.0, 20.0, 25.0, 28.0, 32.0), (2500, None, None, 10.0, 12.5, 18.0, 25.0, 32.0, 36.0, 40.0),
            (3200, None, None, 12.5, 16.0, 22.0, 32.0, 40.0, 45.0, 50.0)]
INTERPOLATE = False   # conservative: next higher table row (60664-1 permits interpolation in places - verify)

# IEC 60664-1 Table A.2: altitude correction factor for clearances (normalised to 2000 m); 0 m is used for test levels
ALT_FACTOR = {0: 0.784, 200: 0.803, 500: 0.833, 1000: 0.887, 2000: 1.00, 3000: 1.14, 4000: 1.29, 5000: 1.48}

# IEC 60664-1 5.3.3.2 / 6.1.3.5 partial discharge factors: F1 1.2 safety, F2 1.25 inception/extinction hysteresis,
# F3 1.25 extra for reinforced. Design: extinction >= F1(*F3) * U_rp. Test (PD <= 10 pC; ICs <= 5 pC per IEC 60747-17):
# 1.5 x U_rp basic / 1.875 x U_rp reinforced - the same structure as the 60747-17 routine test (1.875 x VIORM).
F1, F2, F3 = 1.2, 1.25, 1.25
PD_THRESHOLD_V = 700.0     # PD test required for solid insulation above this recurring peak (62477-1 / 60664-1; value
                           # remembered as 700-750 V - the lower one is used)
HF_LIMIT_HZ = 30e3         # IEC 60664-4 applies above 30 kHz (PV 32 kHz, AUX-HV 65 kHz, DAB 100 kHz)
HF_CLEAR_FACTOR = 1.25     # 60664-4: inhomogeneous-field breakdown at HF can fall to ~80 % -> steady-state clearances x1.25

# AC withstand test level (rms, 60 s type / 1 s routine). Conservative rule = the TOV basis of 60664-1 / 62477-1:
# basic U_sys + 1200 V, reinforced 2 x that. Lenient alternative used only to grade a shortfall as a CONDITION:
# the classic 2 U + 1000 V (DC) per basic, x2 reinforced. Engineering floor for solid insulation with a working
# voltage above U_sys (DAB inter-port): 2 U_w + 1000 V DC.
def u_ac_test(kind, u_sys, u_w):
    """(conservative V rms, lenient V rms) AC test voltage for insulation type kind ('B','R') on a u_sys system."""
    tov = u_sys + 1200.0
    floor = (2 * u_w + 1000.0) / math.sqrt(2)
    lenient = (2 * u_sys + 1000.0) / math.sqrt(2)
    k = 2.0 if kind == "R" else 1.0
    return k * max(tov, floor), k * max(lenient, floor)


# IEC 62477-1 decisive voltage classes (from memory): limits (V rms AC, V peak AC, V DC)
DVC = {"A": (25, 35.4, 60), "B": (50, 71, 120), "C": (1000, 1414, 1500), "D": (math.inf, math.inf, math.inf)}


def dvc(v_dc):
    return next(k for k, lim in DVC.items() if v_dc <= lim[2])


def lookup(table, x, col=1, interp=INTERPOLATE):
    """Table value at x: next higher row (conservative) or linear interpolation; rounds up to 0.1 mm."""
    rows = [(r[0], r[col]) for r in table if r[col] is not None]
    if x <= rows[0][0]:
        return rows[0][1]
    for (x0, y0), (x1, y1) in zip(rows, rows[1:]):
        if x <= x1 + 1e-9:
            y = y0 + (y1 - y0) * (x - x0) / (x1 - x0) if interp else y1
            return math.ceil(y * 10 - 1e-9) / 10 if y >= 0.1 else y
    raise ValueError("value %s beyond the table" % x)


def inverse_f2(d_mm, col=2):
    """Impulse withstand [V] of a clearance d at 2000 m (Table F.2 case A read backwards, linear between rows)."""
    rows = [(r[0], r[col]) for r in TABLE_F2]
    if d_mm < rows[0][1]:
        return 0.0
    for (u0, d0), (u1, d1) in zip(rows, rows[1:]):
        if d_mm <= d1:
            return 1e3 * (u0 + (u1 - u0) * (d_mm - d0) / (d1 - d0)) if d1 > d0 else 1e3 * u0
    return 1e3 * rows[-1][0]


def clearance(u_imp, pd, alt, u_rp=0.0, hf=False, kind="B"):
    """Clearance [mm] = max(impulse basis, steady/recurring basis) x altitude factor."""
    c_imp = lookup(TABLE_F2, u_imp / 1e3, {1: 1, 2: 2, 3: 3}[pd])
    u_ss = u_rp * (1.6 if kind == "R" else 1.0) * (HF_CLEAR_FACTOR if hf else 1.0)   # 60664-1 5.1.3: 160 % reinforced
    c_ss = max(lookup(TABLE_F7, u_ss / 1e3) if u_ss > 330 else 0.0, {1: 0.0, 2: 0.2, 3: 0.8}[pd])
    return math.ceil(max(c_imp, c_ss) * ALT_FACTOR[alt] * 10 - 1e-9) / 10


def creepage(u_w, pd, group, pwb=False, kind="B"):
    """Creepage [mm] at working voltage u_w; group 'I','II','III'; pwb = printed-wiring column (PD1/PD2, <= 1000 V)."""
    if pwb and pd in (1, 2) and u_w <= 1000:
        col = {1: 1, 2: 2}[pd]
    elif pd == 1:
        col = 3
    else:
        col = {(2, "I"): 4, (2, "II"): 5, (2, "III"): 6, (3, "I"): 7, (3, "II"): 8, (3, "III"): 9}[(pd, group)]
    c = lookup(TABLE_F4, max(u_w, 10.0), col)
    return c * (2.0 if kind == "R" else 1.0)


def mat_group(cti):
    return "I" if cti >= 600 else "II" if cti >= 400 else "III"


def imp_required(kind, base):
    """Reinforced = next preferred step above basic, or 160 % of a non-preferred basic value (60664-1 5.1.6)."""
    if kind != "R":
        return base
    if any(abs(base - p) < 1 for p in PREFERRED_IMP):
        return next(p for p in PREFERRED_IMP if p > base + 1)
    return 1.6 * base


def test_impulse_sea_level(u_imp, alt):
    """Impulse test voltage at sea level that proves a clearance dimensioned for u_imp at altitude alt (Table F.2
    read backwards with the 0 m factor; approximates 60664-1 Table F.5 - verify)."""
    d = lookup(TABLE_F2, u_imp / 1e3, 2, interp=True) * ALT_FACTOR[alt]
    return inverse_f2(d / ALT_FACTOR[0])


# =====================================================================================================================
# 2. FROZEN ASSUMPTIONS AND CIRCUITS (voltages read from the designers' spec files)
# =====================================================================================================================
def load(path):
    with open(os.path.join(ROOT, path)) as f:
        return json.load(f)


CELL = load("sim/out/pv_design/cell_spec.json")
DAB = load("sim/out/dab_design/dab_spec.json")
AUX = load("sim/out/aux_hv_design/aux_hv_spec.json")
PORT = load("sim/out/port_design/port_spec.json")

W = CELL["worst_case_stresses"]
PV_V, PV_TRIP = W["device_VDS_continuous_max_V"], W["port_cap_V_max_V"]                 # 1000 V, 1100 V
PV_RP_TRIP = W["device_VDS_peak_V"]                                                     # 1401 V at the trip corner
PV_RP = PV_V + (PV_RP_TRIP - PV_TRIP) * PV_V / PV_TRIP     # ringing scales with the blocking voltage (pv_design method)
D1, D2 = DAB["power_stage"]["port1"]["V_operating"][1], DAB["power_stage"]["port2"]["V_operating"][1]
D1_TRIP, D2_TRIP = DAB["protection"]["V1_ov_hw_V"], DAB["protection"]["V2_ov_hw_V"]
D1_LR = DAB["protection"]["local_ov_board"]["port1"]["V_peak"]
D2_LR = DAB["protection"]["local_ov_board"]["port2"]["V_peak"]
D_RP1 = DAB["worst_case_stresses"]["module_V_peak_V"]
D_OVS = D_RP1 - D1                                         # overshoot above the operating maximum, both bridges
AUX_RP, AUX_RP_TRIP, AUX_F = AUX["summary"]["vds_cont"], AUX["summary"]["vds_trans"], AUX["summary"]["fsw"]

SYSTEM_V = 1000.0          # rated system voltage of every HV port (PV-03/06 1000 V; DAB-10 950 V -> 1000 V row)
OVC = "II"                 # ECO-10: DC OVC II
PD_INSIDE, PD_OUTSIDE = 2, 3   # ECO-10: pollution degree 2 internal, 3 external
PCB_CTI = 175              # ASSUMPTION: standard FR-4, CTI >= 175 (group IIIa) unless a high-CTI laminate is specified
PELV_MAX = 26.45           # D-013: 24 V window 21.6-26.0 V, cut-off 26.10-26.45 V
FIELD_SURGE = 1000.0       # EN IEC 61000-6-2 surge on signal/DC I/O lines, line-to-earth (EMC, functional only)

# Port SPD (PV-C4). Up is at In; the connection leads add L' x len x di/dt (In over the 8 us front).
SPD_LEAD_M, SPD_L_PER_M = 0.5, 1.0e-6     # ASSUMPTION: 0.5 m total SPD lead length, 1 uH/m
SPDS = {"952515": dict(name="DEHN DEHNguard M YPV SCI 1000 FM (952515, as drawn)", up_pe=4000.0, up_pp=4000.0,
                       In=12.5e3, ucpv=1000.0, monitored=False,
                       src="DEHN product page (no PDF retrievable), recorded in gen/port.py catalog: Up <= 4 kV, "
                           "In 12.5 kA, UCPV 1000 V, ISCPV 10 kA; FM contact not wired (gen/port.py block note)"),
        "972147": dict(name="DEHN DG ME DC Y 1000 FM (972147, port-B candidate PA-06)", up_pe=2500.0, up_pp=3750.0,
                       In=12.5e3, ucpv=1000.0, monitored=False,
                       src=DS + "protection/972147.pdf p1: UP (DC+/DC-)->PE < 2.5 kV, DC+->DC- < 3.75 kV, In 12.5 kA, "
                           "no response up to 1250 V DC L+/L- -> PE"),
        "59.D040": dict(name="Raycap ProTec T2-1500DCGU-3Y (59.D040, battery ports as drawn)", up_pe=2500.0,
                        up_pp=4100.0, In=20e3, ucpv=1000.0, monitored=False, alt_max=3000,
                        src=DS + "protection/ProTec-T2-1000DCGU-3Y.pdf p1 (1500 column): Uc (+)-(-) 1500 V, (+/-)-PE "
                            "1000 V, In 20 kA, Up (+)-(-) 4100 V, (+/-)-PE 2500 V, altitude max 3000 m; 59.D040 has "
                            "no remote contact (59.D041 has)")}
SPDS["952515"]["alt_max"] = None          # no datasheet on file: altitude rating unknown
SPDS["952515"]["rc"] = "FM remote-signal contact not wired (gen/port.py block note)"
SPDS["59.D040"]["rc"] = "59.D040 has no remote contact (59.D041 has)"
SPDS["972147"]["rc"] = "FM contact (candidate)"
SPDS["972147"]["alt_max"] = None
SPD_DRAWN = "952515"                      # default; assign_spds() replaces it per system from the netlists


def up_eff(spd, mode="pe"):
    s = SPDS[spd]
    return (s["up_pe"] if mode == "pe" else s["up_pp"]) + SPD_L_PER_M * SPD_LEAD_M * s["In"] / 8e-6


EARTHING = [
    "PV array (port A) and battery / DC bus (port B): IT (floating) with insulation monitoring (PV-C5), OR one pole "
    "earthed by the installation; a FIRST EARTH FAULT may persist (the IMD only alarms). Worst credible case used: "
    "either pole at PE potential, so every HV node can sit at the full port voltage to PE.",
    "PV-P75/100/110: ports A and B share BUS- (non-isolated), so they are ONE circuit; pole-to-PE <= %.0f V continuous, "
    "%.0f V at the hardware OV trip." % (PV_V, PV_TRIP),
    "DAB-D60: port 1 (PCS DC bus) and port 2 (battery) are separate IT systems. Port 1 is assumed NOT galvanically tied "
    "to the AC mains (PCS with its own LF transformer, or otherwise separated); if the PCS is transformerless, port 1 "
    "becomes a mains-connected circuit (grid TOV U0 + 1200 V, mains OVC) and this coordination must be re-run.",
    "DAB inter-port barrier: each port may carry its own first fault at opposite poles (port 1 + pole and port 2 - pole "
    "at PE), so the transformer sees V1 + V2 = %.0f V DC plus one switching overshoot." % (D1 + D2),
    "PELV 24 V system: earthed at one point (PELV 0 V bonded to PE), window 21.6-26.0 V (D-013). DVC A.",
    "Field I/O (isolated CAN/RS-485, digital inputs, BMS gateway, Ethernet): DVC A on both sides of their isolators; "
    "the BMS communication port is assumed SELV/PELV per the battery system's own standard.",
]

# Systems: HV circuits carry the voltages, 'node' picks the recurring peak a barrier sees.
SYSTEMS = {
    "PV": dict(name="PV-P75/100/110 HV (ports A+B, common BUS-)", v=PV_V, trip=PV_TRIP, rp_sw=PV_RP,
               rp_sw_trip=PV_RP_TRIP, f=CELL["switching"]["f_sw_kHz"] * 1e3, sys=SYSTEM_V, spd=SPD_DRAWN),
    "DAB1": dict(name="DAB-D60 port 1 (DC bus)", v=D1, trip=max(D1_TRIP, D1_LR), rp_sw=D_RP1, rp_sw_trip=D1_LR + D_OVS,
                 f=100e3, sys=SYSTEM_V, spd=SPD_DRAWN),
    "DAB2": dict(name="DAB-D60 port 2 (battery)", v=D2, trip=max(D2_TRIP, D2_LR), rp_sw=D2 + D_OVS,
                 rp_sw_trip=D2_LR + D_OVS, f=100e3, sys=SYSTEM_V, spd=SPD_DRAWN),
    "AUX": dict(name="AUX-HV primary (fed from PV ports or DAB port 1 - PV values govern)", v=PV_V, trip=PV_TRIP,
                rp_sw=AUX_RP, rp_sw_trip=AUX_RP_TRIP, f=AUX_F, sys=SYSTEM_V, spd=SPD_DRAWN),
    "PELV": dict(name="PELV 24 V / logic (earthed)", v=PELV_MAX),
    "PE": dict(name="PE / chassis / heatsink / cold plate", v=0.0),
    "FIELD": dict(name="Field I/O (isolated CAN, RS-485, DI, BMS, Ethernet cable side)", v=60.0),
}
HV_SYSTEMS = ("PV", "DAB1", "DAB2", "AUX")

# Circuits = netlist domains mapped per board. node: 'dc' = sits on a DC pole, 'sw' = on a switch node (recurring peak).
CIRCUITS = {
    "PV_HV": ("PV", "dc", "PV HV poles (A+, B+, BUS-)"), "PV_SW": ("PV", "sw", "PV gate-drive island / switch node"),
    "DAB_P1": ("DAB1", "dc", "DAB port 1 poles"), "DAB_P1_SW": ("DAB1", "sw", "DAB bridge-1 gate island / AC node"),
    "DAB_P2": ("DAB2", "dc", "DAB port 2 poles"), "DAB_P2_SW": ("DAB2", "sw", "DAB bridge-2 gate island / AC node"),
    "AUX_PRI": ("AUX", "dc", "AUX-HV primary (PGND / HV_BULK)"), "AUX_SW": ("AUX", "sw", "AUX-HV flyback drain"),
    "PELV": ("PELV", "dc", "PELV 24 V / 3.3 V logic"), "PE": ("PE", "dc", "PE / chassis"),
    "FIELD_COMM": ("FIELD", "dc", "isolated CAN / RS-485 field side"), "FIELD_DI": ("FIELD", "dc", "DI field side"),
    "FIELD_BMS": ("FIELD", "dc", "BMS gateway field side"),
}

# netlist domain -> circuit, per board (None = the board was built without domain fields)
BOARD_DOMAINS = {
    "PVCELL-25": {"HV": "PV_HV", "GD_S1": "PV_SW", "GD_S2": "PV_SW", "GD_S3": "PV_SW", "GD_S4": "PV_SW",
                  "PELV": "PELV", "PE": "PE"},
    "PV-PORT": {"HV": "PV_HV", "PELV": "PELV", "PE": "PE"},
    "GDRV-HB": {"HS": "PV_SW", "LS": "PV_SW", "DCP": "PV_HV", "PELV": "PELV"},   # generic 1000 V card: PV envelope
    "DAB60": dict({"HV1": "DAB_P1", "HV2": "DAB_P2", "PELV": "PELV", "PE": "PE"},
                  **{"GD_B1%s%d" % (s, k): "DAB_P1_SW" for s in "HL" for k in (1, 2)},
                  **{"GD_B2%s%d" % (s, k): "DAB_P2_SW" for s in "HL" for k in (1, 2)}),
    "AUX-HV": {"HV": "AUX_PRI", "PELV": "PELV"},
    "SYS-IO-AUX": {"PELV": "PELV", "PE": "PE", "CANB": "FIELD_COMM", "MCAN": "FIELD_COMM", "RSA": "FIELD_COMM",
                   "RSB": "FIELD_COMM", "DI": "FIELD_DI"},
    "BMU-GW": {"PELV": "PELV", "BMS": "FIELD_BMS"},
    "CTRL-C2000": {None: "PELV"},
}
# Nets whose real potential differs from the declared domain (checked against the netlist topology in nets_override()).
NET_OVERRIDES = {
    "PV-PORT": [(r"/IMD[PN]_(SRC|G|DRV|VDDH|VDDM|SW)$", "PV_HV",
                 "IMD test switch: common-source C2M1000170D pair, the PE-side FET blocks when the pole is below PE, so "
                 "the source/gate island follows the pole (BUS- sits ~V/2 below PE in normal operation) - it is an HV "
                 "net, not PE")],
}


def board_cfg(table, board):
    """Per-board configuration: exact name, else the longest known prefix (PV-PORT-180 -> PV-PORT)."""
    if board in table:
        return table[board]
    k = max((k for k in table if k and board.startswith(k)), key=len, default=None)
    return table.get(k)


def required_type(sa, sb):
    """Insulation type required between two systems (62477-1: protective separation DVC C <-> DVC A = reinforced;
    DVC C <-> accessible PE-bonded parts = basic; DAB port 1 <-> port 2 = simple separation (basic); else functional)."""
    s = {sa, sb}
    hv = s & set(HV_SYSTEMS)
    if len(s) == 1 or ({"AUX", "PV"} == s) or ({"AUX", "DAB1"} == s):
        return "F"
    if hv and s & {"PELV", "FIELD"}:
        return "R"
    if hv and "PE" in s:
        return "B"
    if hv == {"DAB1", "DAB2"}:
        return "B"
    if len(hv) == 2:
        return "B"
    return "F"


def pair_voltages(sa, sb, node):
    """(u_w DC/rms working, u_rp recurring peak, u_tov transient peak, f [Hz], u_sys) across a barrier sa | sb."""
    hv = [s for s in (sa, sb) if s in HV_SYSTEMS]
    if not hv:                                                      # ELV <-> ELV / PE
        v = max(SYSTEMS[sa]["v"], SYSTEMS[sb]["v"])
        return v, v * math.sqrt(2) if v < 60 else v, v, 0.0, v
    if set(hv) == {"DAB1", "DAB2"}:                                 # DAB inter-port: V1 + V2 (+ one overshoot)
        uw = D1 + D2
        return uw, uw + (D_OVS if node == "sw" else 0.0), D1_LR + D2_LR + D_OVS, 100e3, SYSTEM_V
    s = SYSTEMS[hv[0]]
    sw = node == "sw"
    return s["v"], s["rp_sw"] if sw else s["v"], s["rp_sw_trip"] if sw else s["trip"], s["f"] if sw else 0.0, s["sys"]


def worst_spd(systems, mode="pe"):
    """The SPD with the highest effective protection level (mode 'pe' or 'pp') drawn on the given HV systems."""
    c = [m for s in systems if s in HV_SYSTEMS for m in SYSTEMS[s].get("spds", [SYSTEMS[s].get("spd", SPD_DRAWN)])]
    return max(c, key=lambda m: up_eff(m, mode)) if c else SPD_DRAWN


def barrier_req(kind, sa, sb, node="dc", imp=None, spd=None, n_series=1, share=1.0):
    """Altitude-independent requirement for one barrier (or for one element of an n-element string).
    imp: 'port' = the common-mode / pole impulse reaches it; 'internal' = behind the DC-link (recurring peak only);
    'field' = EMC surge on field I/O. share = voltage non-uniformity factor of a string element."""
    u_w, u_rp, u_tov, f, u_sys = pair_voltages(sa, sb, node)
    hv = sa in HV_SYSTEMS or sb in HV_SYSTEMS
    spd = spd or worst_spd((sa, sb), "pp" if sa == sb else "pe")
    imp = imp or ("port" if kind in "BR" else "internal")
    if imp == "port" and hv:
        base = TABLE_F1[min(k for k in TABLE_F1 if k >= u_sys)][("I", "II", "III", "IV").index(OVC)]
        mode = "pp" if sa == sb else "pe"
        spd_base = min(base, up_eff(spd, mode))
        imp_n, imp_s = imp_required(kind, base), imp_required(kind, spd_base)
    elif imp == "field" or not hv:
        imp_n = imp_s = FIELD_SURGE
    else:
        imp_n = imp_s = 0.0
    k = share / n_series
    r = dict(kind=kind, sa=sa, sb=sb, node=node, imp_mode=imp, u_sys=u_sys, f=f, hf=f > HF_LIMIT_HZ,
             u_w=u_w * k, u_rp=u_rp * k, u_tov=u_tov * k, imp_nospd=imp_n * k, imp_spd=imp_s * k, n=n_series,
             spd=spd, spd_alt=SPDS[spd].get("alt_max") or 99999)
    if kind in "BR" and hv:
        r["ac"], r["ac_lenient"] = u_ac_test(kind, u_sys, u_w)
        fac = F1 * F2 * (F3 if kind == "R" else 1.0)
        r["pd_needed"] = node == "sw" and u_rp > PD_THRESHOLD_V     # DC-only stress: no recurring-peak PD test
        r["u_pd_test"] = fac * u_rp                    # PD <= 10 pC at this level (routine/type)
        r["u_pd_ext"] = F1 * (F3 if kind == "R" else 1.0) * u_rp
    else:
        r["ac"] = r["ac_lenient"] = 0.0
        r["pd_needed"], r["u_pd_test"], r["u_pd_ext"] = False, 0.0, 0.0
    return r


def req_clearance(r, alt, spd_credit, pd=PD_INSIDE):
    u = r["imp_spd"] if spd_credit and alt <= r["spd_alt"] else r["imp_nospd"]
    return clearance(u, pd, alt, r["u_rp"], r["hf"], r["kind"])


def req_creepage(r, pd, group, pwb=False):
    return creepage(r["u_w"], pd, group, pwb, "R" if r["kind"] == "R" else "B")


# =====================================================================================================================
# 3. NETLIST ENUMERATION (re-read on every run - the boards are being revised by other agents)
# =====================================================================================================================
def read_netlist(path):
    root = ET.parse(path).getroot()
    comps = {}
    for c in root.iter("comp"):
        fl = {x.get("name"): (x.text or "") for x in c.iter("field")}
        comps[c.get("ref")] = dict(value=c.findtext("value") or "", mpn=fl.get("MPN", ""), dom=fl.get("Domain"),
                                   desc=fl.get("Description", ""), src=fl.get("Sourcing", ""))
    nets = defaultdict(list)
    for n in root.iter("net"):
        for nd in n.iter("node"):
            nets[n.get("name")].append((nd.get("ref"), nd.get("pin")))
    return comps, dict(nets)


def board_barriers(board, comps, nets, problems):
    """Barrier parts of one board as chains: [{refs, mpn, value, kind, circuits}]; domains mapped to circuits."""
    dmap = board_cfg(BOARD_DOMAINS, board)
    if dmap is None:
        problems.append("board %s has no entry in BOARD_DOMAINS (new board: add its domain map)" % board)
        return []
    for ref, c in comps.items():
        if c["dom"] not in ("ISOLATOR", "CROSSING") and c["dom"] not in dmap:
            problems.append("%s %s: unknown domain %r (extend BOARD_DOMAINS)" % (board, ref, c["dom"]))
    ovr = board_cfg(NET_OVERRIDES, board) or []

    def net_circ(name):
        for rx, circ, _ in ovr:
            if re.search(rx, name):
                return {circ}
        return {dmap[comps[r]["dom"]] for r, _ in nets[name]
                if comps[r]["dom"] not in ("ISOLATOR", "CROSSING") and comps[r]["dom"] in dmap}

    bar = {r for r, c in comps.items() if c["dom"] in ("ISOLATOR", "CROSSING")}
    parent = {r: r for r in bar}

    def find(r):
        while parent[r] != r:
            parent[r] = parent[parent[r]]
            r = parent[r]
        return r
    side = defaultdict(set)
    for name, nodes in nets.items():
        refs = {r for r, _ in nodes}
        circ = net_circ(name)
        b = sorted(refs & bar)
        for r in b:
            side[r] |= circ
        if not circ and len(b) > 1 and all(comps[r]["dom"] == "CROSSING" for r in b):   # chain through a '?' net
            for r in b[1:]:
                parent[find(r)] = find(b[0])
    chains = defaultdict(list)
    for r in bar:
        chains[find(r)].append(r)
    out = []
    for members in chains.values():
        members.sort(key=lambda x: (re.sub(r"\d", "", x), int(re.sub(r"\D", "", x) or 0)))
        c0 = comps[members[0]]
        out.append(dict(board=board, refs=members, mpn=c0["mpn"] or c0["value"], value=c0["value"], kind=c0["dom"],
                        src=c0["src"], circuits=set().union(*(side[r] for r in members)),
                        mpns={comps[r]["mpn"] or comps[r]["value"] for r in members}))
    return out


def scan_boards(problems):
    boards = {}
    for bdir in sorted(glob.glob(os.path.join(ROOT, "hardware", "*", ""))):
        board = os.path.basename(os.path.dirname(bdir))
        path = os.path.join(bdir, "outputs", board + "_netlist.xml")
        for _ in range(18):                 # a board that is being rebuilt has no netlist for a while: wait <= 90 s
            if os.path.exists(path):
                break
            time.sleep(5)
        else:
            problems.append("%s: no outputs/%s_netlist.xml (board being rebuilt?) - rerun" % (board, board))
            continue
        for attempt in range(4):            # another agent may be rewriting the file right now
            try:
                comps, nets = read_netlist(path)
                break
            except ET.ParseError as e:
                if attempt == 3:
                    problems.append("%s: netlist unreadable (%s) - board being rebuilt? rerun" % (board, e))
                time.sleep(5)
        else:
            continue
        boards[board] = dict(comps=comps, nets=nets, path=os.path.relpath(path, ROOT),
                             barriers=board_barriers(board, comps, nets, problems))
        if board.startswith("PV-PORT"):  # premise of the IMD override: common-source SiC pair, one drain on PE
            for name, nodes in nets.items():
                if re.search(r"/IMD[PN]_SRC$", name):
                    fets = [r for r, p in nodes if comps[r]["mpn"] == "C2M1000170D" and p == "3"]
                    drains = {n for n, nd in nets.items() for r, p in nd if r in fets and p == "2"}
                    if len(fets) != 2 or "PE" not in drains:
                        problems.append("PV-PORT %s: IMD switch is no longer a common-source pair with one drain on PE "
                                        "- review NET_OVERRIDES" % name)
    assign_spds(boards, problems)
    return boards


def assign_spds(boards, problems):
    """Each HV system takes the worst (highest Up,eff) SPD drawn on it; PV ports share one circuit (common BUS-)."""
    found = defaultdict(set)
    for board, d in boards.items():
        for ch in d["barriers"]:
            if ch["mpn"] in SPDS:
                for c in ch["circuits"]:
                    if system_of(c) in HV_SYSTEMS:
                        found[system_of(c)].add(ch["mpn"])
            elif "spd" in (d["comps"][ch["refs"][0]]["desc"] + ch["mpn"]).lower() and ch["mpn"] not in PARTS:
                problems.append("%s %s %s looks like an SPD but is not in SPDS" % (board, ch["refs"], ch["mpn"]))
    for s, mpns in found.items():
        SYSTEMS[s]["spd"] = max(mpns, key=up_eff)
        SYSTEMS[s]["spds"] = sorted(mpns)
    SYSTEMS["AUX"]["spd"] = worst_spd(("PV", "DAB1"))
    SYSTEMS["AUX"]["spds"] = sorted(set(SYSTEMS["PV"].get("spds", [])) | set(SYSTEMS["DAB1"].get("spds", [])))



# =====================================================================================================================
# 4. RATINGS - datasheet values (PDF + page) for every barrier part; custom parts carry what their spec text states.
#    ins: insulation type claimed by the maker (R/B/F, None = not stated). status: 'rated' (datasheet), 'spec' (our
#    CUSTOM/RFQ text, not yet bought), 'none' (nothing specified). viowm DC working [V], viorm repetitive peak [V],
#    viotm 60 s transient [Vpk], vimp impulse in air [V], viosm surge of the barrier itself [V], uimp impulse withstand
#    of a component without mm data [V], viso 60 s AC test [V rms], clr / cpg [mm], cti, pd_test PD test level with
#    <= 10 pC [Vpk], alt_max [m]. pairs = per circuit-pair ratings for multi-barrier custom parts.
# =====================================================================================================================
def P(**k):
    return k


TIC = "certified: VDE 0884-17 cert. 40040142, UL 1577"
PARTS = {
    # ---- gate drive: GDRV block on PVCELL-25, DAB60, GDRV-HB
    "UCC21710DWR": P(pd=2, ins="R", viorm=2121, viowm=2121, viotm=8000, vimp=8000, viosm=8000, clr=8.0, cpg=8.0, cti=600,
                     viso=5700, cert=TIC + " E181974", ev=DS + "gate-drivers/UCC21710.pdf p6-7 (SLUSD43B)"),
    "UCC14241QDWNRQ1": P(pd=2, ins="R", viorm=1414, viowm=1414, viotm=7071, vimp=7692, viosm=10000, clr=8.0, cpg=8.0,
                         cti=600, viso=5000, cert="PLANNED: VDE 0884-17, UL 1577 and CQC certificates all 'planned'",
                         ev=DS + "gate-drivers/UCC14241-Q1.pdf p7, p12 (SLUSF09A)"),
    # ---- isolated sensing / switch drivers: PV-PORT, DAB60
    "AMC3330DWE": P(pd=2, ins="R", viorm=1700, viowm=1700, viotm=6000, vimp=7700, viosm=10000, clr=8.0, cpg=8.0, cti=600,
                    viso=4250, cert=TIC + " E181974", ev=DS + "sensing/AMC3330.pdf p6-7 (SBASA34B)"),
    "AMC3302DWE": P(pd=2, ins="R", viorm=1700, viowm=1700, viotm=6000, viosm=6250, clr=8.0, cpg=8.0, cti=600, viso=4250,
                    cert="certified: DIN VDE V 0884-11:2017 cert. 40040142, UL E181974 (no VIMP: older edition)",
                    ev=DS + "sensing/AMC3302.pdf p6-7 (SBASA11B)"),
    "TPSI3050QDWZRQ1": P(pd=2, ins="R", viorm=1414, viowm=1414, viotm=7070, vimp=9230, viosm=12000, clr=8.5, cpg=8.5,
                         cti=600, viso=5000, cert=TIC + " UL-US-2300613-0",
                         ev=DS + "isolation-interface/TPSI3050-Q1.pdf p6-7 (SLVSFJ7D)"),
    "ISO7720FDWR": P(pd=2, ins="R", viorm=2121, viowm=2121, viotm=8000, vimp=8000, viosm=12800, clr=8.0, cpg=8.0, cti=600,
                     viso=5000, cert="certified: VDE 0884-17 (DW-16), UL 1577",
                     ev=DS + "isolation-interface/ISO7721.pdf p10, p12 (SLLSEP3G, ISO772x DW-16 column)"),
    "UCC12050DVE": P(pd=2, ins="R", viorm=1697, viowm=1697, viotm=7071, viosm=6250, clr=8.0, cpg=8.0, cti=600, viso=5000,
                     viorm_pd=1414, cert="certified: DIN V VDE V 0884-11:2017 reinforced (VIORM 1697 Vpk), UL 1577",
                     note="the qpd lines (1.875 x VIORM = 2651 Vpk) correspond to 1414 Vpk, not 1697 Vpk: datasheet "
                          "inconsistency - 1414 Vpk used as the PD-proven VIORM",
                     ev=DS + "power-supply/UCC12050.pdf p5-6 (SNVSB38D)"),
    # ---- field I/O: SYS-IO-AUX, BMU-GW (functional barriers between two DVC A circuits)
    "ISO1042DWR": P(pd=2, ins="R", viorm=1500, viowm=2121, viotm=7071, viosm=10000, clr=8.15, cpg=8.15, cti=600, viso=5000,
                    cert="reinforced per datasheet", ev=DS + "isolation-interface/ISO1042.pdf p1, p7 (SLLSF09F)"),
    "ISO1410DWR": P(pd=2, ins="R", viorm=1500, viowm=2121, viotm=7071, viosm=10000, clr=8.0, cpg=8.0, cti=600, viso=5000,
                    cert="reinforced per datasheet", ev=DS + "isolation-interface/ISO1410.pdf p7 (SLLSF22I)"),
    "ISO1212DBQR": P(pd=2, ins="B", viorm=566, viowm=566, viotm=3600, viosm=4000, clr=3.7, cpg=3.7, cti=600, viso=2500,
                     ev=DS + "isolation-interface/ISO1211.pdf p7 (SLLSEY7G, DBQ-16 column)"),
    "760390014": P(ins="F", viowm=566, viso=2500, ev=DS + "magnetics/WE-760390014.pdf p1 (VT 2500 V AC), p2 "
                                                      "(functional insulation, 400 V rms working, IEC 60950-1)"),
    "750315371": P(ins="F", viowm=400, viso=2500, ev=DS + "magnetics/WE-750315371.pdf p1 (VT 2500 V AC), p2 "
                                                     "(functional, 265 V rms / 400 V pk, OVC II, IEC 60950-1)"),
    "LPJ4012AHNL": P(ins="F", viso=1500, ev=DS + "connectors/LINK-PP-LPJ4012AHNL.pdf p1 (drawing LP08121230 rev A, "
                                                 "image: hipot 1500 Vrms; 1000 pF / 2 kV Bob-Smith capacitor to chassis)"),
    "7499010211A": P(ins="F", viso=1591, ev=DS + "connectors/WE-7499010211A.pdf p3 (insulation test 2250 V DC, 1 min)"),
    "470p 2kV": P(ins="F", viowm=2000, uimp=2000, clr=2.0, cpg=2.0,
                  ev="GENERIC 470 pF 2 kV C0G 1206 (gen/sys_io_aux.py C701, DIF_GND-PE); 1206 gap ~2 mm (assumption)"),
    # ---- AUX-HV
    "CNY65B": P(pd=2, ins="R", viorm=1800, viowm=1800, viotm=12000, clr=14.0, cpg=14.0, cti=200, viso=8200,
                cert="certified: DIN EN 60747-5-5 (VDE 0884-5), UL 1577; climatic 55/85/21 (85 C max)",
                note="no VIOWM given: VIORM used as the DC limit; insulation thickness >= 3 mm",
                ev=DS + "isolation-interface/CNY65.pdf p1, p4 (doc 83540 rev 2.4)"),
    "AUX-HV-T1 rev A0": P(status="spec", node="sw", ev="gen/aux_hv.py T1 description; sim/out/aux_hv_design/report.md sec. 11",
                          pairs={("AUX", "PELV"): P(ins="R", viowm=1000, clr=24.0, cpg=24.0, cti=175,
                                 note="TIW secondary 'certified reinforced, >= 1000 V DC working'; hipot 'per IEC 62477-1 "
                                      "reinforced for 1000 V DC (value set by ECO-10)'; pin rows 25.4 mm apart on "
                                      "B66359B1013T001 (GFR PBT, CTI not stated -> IIIa assumed); no PD level stated")}),
    # ---- contactor and precharge relay: coil (and mirror contact) PELV <-> main contacts HV
    "B88269X7340C011": P(pd=2, ins=None, viowm=1000, uimp=8000, viso=4400,
                         note="contact-to-coil 4400 V AC and Uimp 8 kV (IEC 60947-4-1), PD2, 1000 V DC; the insulation "
                              "TYPE is not stated and p11 recommends separating or shielding the coil/aux side",
                         ev=DS + "protection/HVC43MC.pdf p2-3, p11 (version 02, 2024-10-22)"),
    "G7L-2A-X DC24": P(ins=None, viowm=1000, uimp=10000, viso=4000,
                       note="coil-contacts 4000 V AC 1 min, impulse 10 kV (1.2x50); type, creepage and clearance not "
                            "stated", ev=DS + "protection/G7L-X.pdf p2 (Cat. J216-E1-04)"),
    # ---- HV-PE crossings on the port blocks
    "VY1472M63Y5UQ63V0": P(ins="R", viowm=1500, uimp=8000, viso=4000, clr=8.4, cpg=8.4, cti=175, pwb=True,
                           cert="class Y1 per IEC 60384-14 (may bridge basic or reinforced insulation)",
                           note="1500 V DC, 4000 V AC test, 8 kV impulse; lead spacing 10.0 mm (code ...3V0), 0.6 mm "
                                "leads -> ~8.4 mm pad-to-pad (estimate)",
                           ev=DS + "passives-capacitors/VY1.pdf p1-3 (doc 28537 rev 28-Sep-2026)"),
    "952515": P(spd=True, mpn="952515", ucpv=1000.0, ev=SPDS["952515"]["src"]),
    "59.D040": P(spd=True, mpn="59.D040", ucpv=1000.0, ev=SPDS["59.D040"]["src"]),
}

# String elements (series resistors / diodes inside an HV circuit or across HV-PE): checked per element.
# umax continuous [V], upulse single-pulse [V], gap = terminal-to-terminal distance on the body / pads [mm]
ELEMENTS = {
    "TNPV12061M00BEEA": P(umax=700, upulse=1400, gap=2.0, ev=DS + "passives-capacitors/TNPV-e3.pdf p1 (Umax 700 V), "
                          "p9 (single-pulse overload 2 x Umax, 10/700 us), p10 (contact distance To >= 2.0 mm)"),
    "TNPV12062M00BEEA": P(umax=700, upulse=1400, gap=2.0, ev=DS + "passives-capacitors/TNPV-e3.pdf p1, p9, p10"),
    "TNPV1210124KBEEA": P(umax=1000, upulse=2000, gap=2.0, ev=DS + "passives-capacitors/TNPV-e3.pdf p1 (Umax 1000 V), "
                          "p9, p10 (To >= 2.0 mm)"),
    "US1M-13-F": P(umax=1000, upulse=1000, gap=1.5, ev=DS + "power-semiconductors/US1M.pdf p2 (VRRM 1000 V), p4 "
                   "(SMA land pattern gap G = 1.5 mm; no avalanche rating -> pulse = VRRM)"),
    "CRHV2512AF10M0FKFB": P(umax=3000, upulse=3000, gap=3.5, ev=DS + "passives-capacitors/CRHV.pdf p1 (3000 V working; "
                            "no pulse rating -> = Umax; 2512 gap ~3.5 mm assumed)"),
    "47k": P(umax=500, upulse=1000, gap=3.5, ev="GENERIC 47 k 2512 1 W thick film, '>= 500 V working' (gen/port.py); "
             "pulse 2 x Umax and 3.5 mm pad gap are package-typical ASSUMPTIONS"),
}
TERMINAL_NET = re.compile(r"_(X|IN|F)[+-]$")   # port.py names: terminal side of the contactor (sees the DM impulse)
STRING_SHARE = 1.3     # ASSUMPTION: worst element share of a string (tolerance, capacitive grading, leakage)

# ---- batch 2: power stage, magnetics, current sensors (PVCELL-25, DAB60) - custom parts carry their spec text
PARTS.update({
    "LA 150-P": P(pd=2, ins="B", node="sw", viowm=1000, uimp=8000, viso=4300, pd_test=1300 * math.sqrt(2), clr=8.0, cpg=8.0,
                  cti=600, composite="double insulation = LA 150-P basic + lead A supplementary insulation (CUSTOM, "
                  "L_CELL spec: 'SUPPLEMENTARY for 1000 V DC working, PD-free above 2.1 kV peak', screened to PE): the "
                  "lead must be type-tested to the basic levels of this row",
                  note="EN 50178: basic 1000 V / reinforced 600 V, OV 3, PD2 (ratings for <= 2000 m); Ud 4.3 kV, "
                       "UNi 8 kV, Ut 1.3 kV rms (< 10 pC), dCp = dCI = 8 mm, CTI 600",
                  ev=DS + "sensing/LA_150-P.pdf p3 (24 Jul 2024, v5)"),
    "L_CELL": P(status="spec", node="sw", ev="gen/pvcell.py L_DESC (L401 description); sim/out/pv_design/cell_spec.json",
                pairs={("PV", "PE"): P(ins="B", note="'winding to core/bracket basic insulation, core and bracket bonded "
                                                     "to PE' - no voltage, recurring peak, PD or test level stated"),
                       ("PV", "PELV"): P(ins="R", viowm=1000, note="embedded NTC: 'REINFORCED insulation NTC + leads to "
                                         "winding for 1000 V DC working' - the 32 kHz recurring peak and PD level are "
                                         "not stated"),
                       ("PELV", "PE"): P(ins="F")}),
    "Transformer 11:12": P(status="spec", node="sw", ev="sim/out/dab_design/dab_spec.json transformer.isolation; "
                                                         "gen/dab60.py XF_DESC (T1101 description)",
                           pairs={("DAB1", "DAB2"): P(ins="B", viowm=1850, note="'basic (pending system decision)', "
                                                      "working-voltage basis 1850 V DC, PD <= 10 pC at a level 'TBD by "
                                                      "ECO-10'; hipot TBD"),
                                  ("DAB1", "PELV"): P(ins="R", note="embedded NTC 'leads TH1/TH2 reinforced to PELV' - "
                                                      "no voltage, PD or test level stated"),
                                  ("DAB2", "PELV"): P(ins="R", note="embedded NTC 'leads TH1/TH2 reinforced to PELV' - "
                                                      "no voltage, PD or test level stated")}),
    "CBB011M12GM4T": P(ins="F", clr=5.0, cpg=6.3, cti=200, note="terminal-to-terminal clearance 5.0 mm, creepage 6.3 mm, "
                       "CTI 200", ev=DS + "power-semiconductors/CBB011M12GM4T.pdf p3 (Rev. 3, June 2026)"),
    "MSC035SMA170B4": P(ins="F", clr=1.2, cpg=0.5,
                        note="no creepage/clearance in the datasheet; TO-247-4L lead pitch 2.54 mm, lead width <= 1.33 mm "
                             "(Table 2-1 R, J) -> ~1.2 mm air between drain and source leads, ~0.5 mm between plated "
                             "pads on the PCB (estimate)",
                        ev=DS + "power-semiconductors/MSC035SMA170B4.pdf p9-10 (DS00005160A)"),
})


def desc(c):
    return (c["mpn"] + " | " + c["value"] + " | " + c["desc"]).lower()


# Barriers that are not a single Domain=ISOLATOR/CROSSING part. 'pair': ('own', X) = the HV system the part sits in.
NONPART = [
    P(id="TO247-PAD", match=lambda c: c["mpn"] == "MSC035SMA170B4", pair=("own", "PE"), node="sw",
      what="heatsink insulation under each TO-247-4 tab (drain = A+/B+ or a switch node) to the PE-bonded heatsink",
      rt=P(status="none", ev="gen/pvcell.py ('insulated mount on the PE-bonded heatsink' - no pad part or spec); "
                             "sim/out/pv_design/report.md (case-sink 0.35 K/W 'insulated, estimate')")),
    P(id="VS60-TAB", match=lambda c: c["mpn"] == "VS-60EPS16-M3", pair=("own", "PE"), node="dc",
      what="bank reverse clamp, TO-247AC base = cathode (bus +): exposed live tab, mounting not specified",
      rt=P(status="spec", ins="B", note="cell_spec: on the laminated bus at the C4AQ bank terminals - if it is put on the "
                                        "heatsink it needs the same basic pad as the MOSFETs",
           ev=DS + "power-semiconductors/VS-60EPS16-M3.pdf p1 (base = cathode); sim/out/pv_design/report.md sec. 7")),
    P(id="MODULE-BASE", match=lambda c: c["mpn"] == "CBB011M12GM4T", pair=("own", "PE"), node="sw",
      what="SiC module substrate / base plate (pre-applied TIM) to the PE cold plate",
      rt=P(ins=None, viso=3000, clr=10.0, cpg=11.5, cti=200, note="Visol 3 kV AC 1 min; terminal-to-heatsink clearance "
           "10.0 mm, creepage 11.5 mm; no PD, impulse or insulation type stated",
           ev=DS + "power-semiconductors/CBB011M12GM4T.pdf p3 (Rev. 3, June 2026)")),
    P(id="DIODE-MODULE-BASE", match=lambda c: c["mpn"] == "DD600N16K", pair=("own", "PE"), node="dc",
      what="reverse-clamp diode module base plate to chassis / cold plate (insulated base)",
      rt=P(ins=None, viso=3000, note="VISOL 3.6 kV rms 1 s / 3.0 kV rms 1 min; no impulse, creepage or PD data; mounting "
                                     "surface not stated in gen/dab60.py (CHASSIS-MOUNTED)",
           ev=DS + "power-semiconductors/DD600N16K.pdf p2 (rev 3.4)")),
    P(id="HOB-PRI-SEC", match=lambda c: c["mpn"] == "HOB 130-P", pair=("own", "own"), node="sw",
      what="transformer-current sensor: primary jumper in the AC path, secondary on the bridge's own DC- (functional)",
      rt=P(pd=2, ins="B", viowm=1000, uimp=9600, viso=4400, pd_test=1500 * math.sqrt(2), clr=11.0, cpg=11.0, cti=600,
           alt_max=2000, note="basic 1000 V / reinforced 600 V (IEC 61800-5-1, CAT III, PD2); insulation coordination "
                              "stated for 2000 m", ev=DS + "sensing/HOB-P_series.pdf p4")),
    P(id="XFMR-CORE", match=lambda c: c["value"].startswith("Transformer 11:12"), pair=[("DAB1", "PE"), ("DAB2", "PE")],
      node="sw", what="DAB transformer windings to core / clamp / cold plate (cold-plate mounted)",
      rt=P(status="none", ev="sim/out/dab_design/dab_spec.json transformer (only the winding-to-winding class is given); "
                             "gen/dab60.py XF_DESC")),
    P(id="LSER-CORE", match=lambda c: "custom series inductor" in desc(c), pair=("own", "PE"), node="sw",
      what="DAB series inductor winding to core / cold plate (cold-plate mounted)",
      rt=P(status="none", ev="sim/out/dab_design/dab_spec.json series_inductor; gen/dab60.py LS_DESC - no insulation "
                             "statement")),
    P(id="NTC-SHUNT", match=lambda c: c["mpn"].startswith("B57703M") and "shunt" in desc(c), pair=("own", "PELV"), node="dc",
      what="port shunt NTC (wired to SYS-IO-AUX PELV) clamped to the shunt copper on BUS- over an insulating pad",
      rt=P(status="spec", ins="R", viowm=1000, note="pad is a requirement in the NTC_SH description, not a BOM item; the "
           "probe's own test is only 1000 V AC / 1 s", ev="gen/port.py NTC_SH description; " + DS +
                                                           "sensing/B57703M.pdf p2 (Vtest 1000 V AC, 1 s)")),
    P(id="NTC-HEATSINK", match=lambda c: c["mpn"].startswith("B57703M") and ("heatsink" in desc(c) or "cold-plate" in desc(c)),
      pair=("PELV", "PE"), node="dc", what="heatsink / cold-plate NTC probe (PELV) on PE metal: functional",
      rt=P(ins="F", viso=1000, ev=DS + "sensing/B57703M.pdf p2 (Vtest 1000 V AC, 1 s)")),
    P(id="THERMOSTAT", match=lambda c: c["mpn"] == "3100U00031461", pair=("own", "PE"), node="dc",
      what="discharge thermal cut-off at HV, mounted on the PE-bonded RST 200 housing over an insulating pad",
      rt=P(status="spec", ins="B", viowm=1000, note="'mount over a basic-insulating pad (1000 VDC working, OVC II)' - pad "
           "not a BOM item; thermostat terminal-to-case insulation not given", ev="gen/port.py TSTAT_DIS description; "
                                                                                + DS + "thermal/Honeywell-3100U.pdf")),
    P(id="RST200", match=lambda c: "rst 200" in desc(c), pair=("own", "PE"), node="dc",
      what="precharge / discharge resistor element to its PE-bonded aluminium housing",
      rt=P(ins=None, viso=3500, note="insulation layer 3.5 kV rms 50 Hz 1 min, IR >= 500 MOhm at 1000 V DC; no impulse "
                                     "or creepage data", ev=DS + "protection/Miba-RST-200.pdf p1")),
    P(id="FUSE-BASE", match=lambda c: c["mpn"].startswith("HP10NH"), pair=("own", "PE"), node="dc", zone=PD_OUTSIDE,
      what="NH fuse base (HPBB11PPR / HPBB21PPR) pole to PE mounting plate, in the inlet air path",
      rt=P(ins=None, viowm=1000, uimp=8000, note="Uimp 8 kV; no creepage or AC test data",
           ev=DS + "protection/NH-gPV-1000VDC.pdf p2")),
    P(id="FUSE-BASE-ETI", match=lambda c: c["mpn"].startswith("0041"), pair=("own", "PE"), node="dc", zone=PD_OUTSIDE,
      what="ETI NH gBat fuse base (NH1 1000 V DC / PK1XL 1500 V) pole to PE mounting plate",
      rt=P(ins=None, viowm=1000, note="catalogue gives rated voltage and base type only - no impulse, creepage or "
                                      "insulation data", ev=DS + "protection/ETI-Green-Protect.pdf p103, p108")),
    P(id="CM-CHOKE", match=lambda c: c["value"].startswith("CM choke"), pair=("own", "PE"), node="dc",
      what="port common-mode choke windings to core / mounting (chassis)",
      rt=P(status="spec", ins="B", viowm=1000, uimp=4000, note="'1000 VDC working (+4 kV surge) ... winding-core basic "
           "insulation'", ev="gen/port.py CMC160 / CMC200 description")),
    P(id="DC-TERMINAL", match=lambda c: c["value"].startswith("DC terminal"), pair=("own", "PE"), node="dc",
      zone=PD_OUTSIDE, what="DC terminal stud feed-through to the PE enclosure (external, PD3)",
      rt=P(status="spec", ins="B", note="'external creepage per PD3 insulation coordination (ECO-10)' - no value yet",
           ev="gen/port.py TERM200 / TERM250 description")),
    P(id="X-CAP", match=lambda c: c["value"].startswith("X 2.2u"), pair=("own", "own"), node="dc", imp="port",
      what="X capacitor across the SPD / after the CM choke (pole-pole, functional)",
      rt=P(status="spec", ins="F", viowm=1300, uimp=4500, note="RFQ: impulse >= 4.5 kV (PA-07)",
           ev="gen/port.py XCAP_2U2 description; sim/out/port_design/report.md sec. 9")),
    P(id="MODULE-NTC", match=lambda c: c["mpn"] == "CBB011M12GM4T", pair=("own", "own"), node="sw",
      what="module NTC read by the low-side UCC21710 AIN on its own gate island (the UCC21710 is the PELV barrier)",
      rt=P(ins="F", ev=DS + "power-semiconductors/CBB011M12GM4T.pdf p3; gen/dab60.py (NTC on AIN/APWM)")),
]


def find_strings(comps, nets, dmap):
    """Series strings (>= 3 equal two-pin parts joined by nets that carry exactly two of their pins) in HV circuits."""
    key = {r: c["mpn"] or c["value"] for r, c in comps.items() if r[0] in "RD"}
    pins = defaultdict(list)
    for name, nodes in nets.items():
        for r, p in nodes:
            pins[r].append(name)
    adj = defaultdict(set)
    for name, nodes in nets.items():
        rs = [r for r, _ in nodes if r in key]
        for k in set(key[r] for r in rs):
            same = [r for r in rs if key[r] == k]
            others = [r for r, _ in nodes if r not in same]
            if len(same) == 2 and same[0] != same[1] and len(others) <= 2 and all(r[0] == "C" for r in others):
                adj[same[0]].add(same[1])
                adj[same[1]].add(same[0])
    seen, out = set(), []
    for r in adj:
        if r in seen:
            continue
        comp, st = set(), [r]
        while st:
            x = st.pop()
            if x not in comp:
                comp.add(x)
                st += adj[x]
        seen |= comp
        if len(comp) < 3 or key[r] not in ELEMENTS:
            continue
        inner = {n for x in comp for n in pins[x] if sum(1 for y in comp if n in pins[y]) == 2}
        ends = sorted({n for x in comp for n in pins[x]} - inner)
        circ = set()
        for n in ends:
            for y, _ in nets[n]:
                d = comps[y]["dom"]
                if y not in comp and d in dmap:
                    circ.add(dmap[d])
        out.append(dict(refs=sorted(comp, key=lambda x: int(re.sub(r"\D", "", x))), mpn=key[r], ends=ends, circuits=circ))
    return out

# =====================================================================================================================
# 5. JUDGE - every verdict is the result of these comparisons
# =====================================================================================================================
NAMES = {"F": "functional", "B": "basic", "R": "reinforced"}
ORDER = {"F": 0, "B": 1, "R": 2}
BOARD_MAIN_HV = {"PVCELL-25": "PV", "PV-PORT": "PV", "GDRV-HB": "PV", "DAB60": "DAB1", "AUX-HV": "AUX"}


def derate_imp(u, alt):
    """Impulse a component rated u (<= 2000 m, no mm data) still withstands at altitude alt (Table F.2 backwards)."""
    if alt <= 2000:
        return u
    return inverse_f2(lookup(TABLE_F2, u / 1e3, 2, interp=True) / ALT_FACTOR[alt])


def judge(rq, rt, alt, zone=PD_INSIDE):
    """Compare one requirement rq with one rating set rt at altitude alt -> (verdict, [notes])."""
    fails, conds = [], []
    kind, status = rq["kind"], rt.get("status", "rated")
    if rt.get("spd"):
        s = SYSTEMS[rq["sa"]] if rq["sa"] in HV_SYSTEMS else SYSTEMS[rq["sb"]]
        (fails if rt["ucpv"] < s["v"] else []).append("UCPV %.0f V < %.0f V continuous" % (rt["ucpv"], s["v"]))
        if rt["ucpv"] < s["trip"]:
            conds.append("UCPV %.0f V < %.0f V (OV trip / load rejection): the SPD conducts and ages in OV events - "
                         "its maker to confirm" % (rt["ucpv"], s["trip"]))
        if SPDS[rt["mpn"]].get("alt_max") and alt > SPDS[rt["mpn"]]["alt_max"]:
            fails.append("SPD rated for <= %d m only" % SPDS[rt["mpn"]]["alt_max"])
        elif not SPDS[rt["mpn"]].get("alt_max") and alt > 2000:
            conds.append("SPD altitude rating not on file (no datasheet) - confirm >= %d m" % alt)
        if not SPDS[rt["mpn"]]["monitored"]:
            conds.append("%s: an SPD that has disconnected goes unnoticed, so its protection level cannot be "
                         "credited to the insulation inside the module" % SPDS[rt["mpn"]]["rc"])
        return ("FAIL" if fails else "PASS WITH CONDITION" if conds else "PASS"), fails + conds
    if status == "none":
        return "FAIL", ["not specified: no part, rating or spec text exists for this insulation (requirement in this row)"]
    if status == "spec":
        conds.append("custom / RFQ: the spec must carry the levels of this row; verify by type test")
    if rt.get("composite"):
        conds.append(rt["composite"])
    ins = rt.get("ins")
    if kind != "F":
        if ins is None:
            conds.append("insulation type not stated by the maker - %s to be confirmed in writing" % NAMES[kind])
        elif ORDER[ins] < ORDER[kind]:
            fails.append("rated %s, %s required" % (NAMES[ins], NAMES[kind]))

    def cmp(have, need, need_spd, what):
        if have is None or need <= 0:
            return
        if have >= need - 0.5:
            return
        if have >= need_spd - 0.5:
            conds.append("%s %.0f V < %.0f V: passes only with the SPD credit (%.0f V)" % (what, have, need, need_spd))
            return
        (conds if kind == "F" and what.startswith("clearance") else fails).append(
            "%s %.0f V < required %.0f V (%.0f V with SPD credit)" % (what, have, need, need_spd))

    if rt.get("viowm") and rq["u_w"] > rt["viowm"] + 0.5:
        fails.append("working voltage %.0f V > rated %.0f V" % (rq["u_w"], rt["viowm"]))
    if rt.get("viorm"):
        if rq["u_rp"] > rt["viorm"] + 0.5:
            fails.append("recurring peak %.0f V > VIORM %.0f V" % (rq["u_rp"], rt["viorm"]))
        elif rt.get("viorm_pd") and rq["u_rp"] > rt["viorm_pd"]:
            conds.append("recurring peak %.0f V > the PD-proven %.0f V (datasheet inconsistency)" % (rq["u_rp"], rt["viorm_pd"]))
    elif rq["pd_needed"] and kind != "F":
        if rt.get("pd_test"):
            if rt["pd_test"] < rq["u_pd_ext"] - 0.5:
                fails.append("PD test level %.0f Vpk < extinction requirement %.0f Vpk" % (rt["pd_test"], rq["u_pd_ext"]))
        else:
            conds.append("PD performance not stated: needs PD <= 10 pC at %.0f Vpk (extinction >= %.0f Vpk)"
                         % (rq["u_pd_test"], rq["u_pd_ext"]))
    n_imp, s_imp = rq["imp_nospd"], rq["imp_spd"] if alt <= rq["spd_alt"] else rq["imp_nospd"]
    have_imp = False
    if rt.get("viosm"):
        cmp(rt["viosm"], n_imp, s_imp, "barrier surge VIOSM")
        have_imp = True
    if rt.get("vimp"):
        cmp(rt["vimp"], n_imp, s_imp, "impulse VIMP (air)")
        have_imp = True
    if rt.get("uimp"):
        u = rt["uimp"] if rt.get("clr") else derate_imp(rt["uimp"], alt)
        cmp(u, n_imp, s_imp, "impulse withstand%s" % ("" if rt.get("clr") or alt <= 2000 else " at %d m" % alt))
        have_imp = True
    need_cl, need_cl_s = req_clearance(rq, alt, False, zone), req_clearance(rq, alt, True, zone)
    if rt.get("clr"):
        if rt["clr"] < need_cl - 1e-6:
            if rt["clr"] >= need_cl_s - 1e-6:
                conds.append("clearance %.1f mm < %.1f mm: passes only with the SPD credit (%.1f mm)" % (rt["clr"], need_cl, need_cl_s))
            else:
                (conds if kind == "F" else fails).append(
                    "clearance %.1f mm < %.1f mm (%.1f mm with SPD credit)%s" % (rt["clr"], need_cl, need_cl_s,
                    " - functional: IEC 60664-3 Type 2 coating or the 62477-1 functional short-circuit test" if kind == "F" else ""))
        have_imp = True
    if not have_imp and kind != "F":
        conds.append("no impulse or clearance data")
    if rt.get("cpg"):
        grp, pwb = mat_group(rt.get("cti") or 100), rt.get("pwb", False)
        cr, cr1 = req_creepage(rq, zone, grp, pwb), req_creepage(rq, 1, grp, pwb)
        c = rt["cpg"]
        if c >= max(cr, need_cl) - 1e-6:
            pass
        elif c >= max(cr, need_cl_s) - 1e-6:
            conds.append("creepage %.1f mm >= %.1f mm only with the SPD-credited clearance floor" % (c, cr))
        elif c >= max(cr1, need_cl) - 1e-6:
            conds.append("creepage %.1f mm < %.1f mm (PD%d, group %s): coat to PD1 per IEC 60664-3 (then %.1f mm)"
                         % (c, cr, zone, grp, max(cr1, need_cl)))
        elif c >= max(cr1, need_cl_s) - 1e-6:
            conds.append("creepage %.1f mm: needs PD1 coating AND the SPD credit" % c)
        else:
            (conds if kind == "F" else fails).append(
                "creepage %.1f mm < %.1f mm even at PD1 (%.1f mm)%s" % (c, cr, max(cr1, need_cl),
                " - functional: IEC 60664-3 Type 2 coating or the 62477-1 functional short-circuit test" if kind == "F" else ""))
    elif kind != "F":
        conds.append("creepage not stated")
    if kind != "F":
        ac = rt.get("viso") or (rt["viotm"] / math.sqrt(2) if rt.get("viotm") else None)
        if ac is None:
            conds.append("AC withstand not stated: type test %.0f V rms required" % rq["ac"])
        elif ac < rq["ac"] - 0.5:
            if ac >= rq["ac_lenient"] - 0.5:
                conds.append("AC withstand %.0f V rms < %.0f V rms (TOV rule) but >= %.0f V rms (2U+1000 rule): the "
                             "applicable 62477-1 level decides" % (ac, rq["ac"], rq["ac_lenient"]))
            else:
                fails.append("AC withstand %.0f V rms < %.0f V rms required" % (ac, rq["ac_lenient"]))
        if "planned" in rt.get("cert", "").lower():
            conds.append("safety certification still PLANNED in the datasheet")
        if rt.get("alt_max") and alt > rt["alt_max"]:
            conds.append("maker's insulation data stated for <= %d m" % rt["alt_max"])
        if zone > (rt.get("pd") or 2) or (zone > 2 and not rt.get("pd")):
            conds.append("in a PD%d zone: the part's rating is for PD%s - PD3 creepage applies (or a PD2 enclosure)"
                         % (zone, rt.get("pd") or " (not stated)"))
        if rq["hf"] and status == "spec":
            conds.append("IEC 60664-4: %.0f kHz recurring stress - PD-free at the operating frequency, check dielectric "
                         "heating of the solid insulation" % (rq["f"] / 1e3))
    return ("FAIL" if fails else "PASS WITH CONDITION" if conds else "PASS"), fails + conds


def judge_string(rq, el, alt, crosses_pe):
    """Per-element check of a series string: voltage, pulse, creepage and clearance across each body."""
    fails, conds = [], []
    if rq["u_w"] > el["umax"]:
        fails.append("element working voltage %.0f V > %.0f V" % (rq["u_w"], el["umax"]))
    if rq["u_rp"] > el["umax"]:
        fails.append("element recurring peak %.0f V > %.0f V" % (rq["u_rp"], el["umax"]))
    if rq["imp_nospd"] > el["upulse"]:
        (conds if rq["imp_spd"] <= el["upulse"] else fails).append(
            "element impulse share %.0f V > pulse rating %.0f V (%.0f V with SPD credit)"
            % (rq["imp_nospd"], el["upulse"], rq["imp_spd"]))
    cr2, cr1 = creepage(rq["u_w"], 2, "III", pwb=True), creepage(rq["u_w"], 1, "III", pwb=True)
    cl = clearance(rq["imp_nospd"], 2, alt, rq["u_rp"], rq["hf"])
    cl_s = clearance(rq["imp_spd"], 2, alt, rq["u_rp"], rq["hf"])
    need = max(cr2, cl)
    if el["gap"] < need:
        if el["gap"] >= max(cr2, cl_s):
            conds.append("gap %.1f mm needs the SPD-credited clearance (%.1f mm)" % (el["gap"], cl_s))
        elif el["gap"] >= max(cr1, cl):
            conds.append("gap %.1f mm < %.1f mm per body at PD2: coat to PD1 (IEC 60664-3)" % (el["gap"], need))
        elif el["gap"] >= max(cr1, cl_s):
            conds.append("gap %.1f mm per body: needs PD1 coating and the SPD credit" % el["gap"])
        else:
            conds.append("gap %.1f mm per body < %.1f mm even at PD1: Type 2 coating or functional short-circuit "
                         "test" % (el["gap"], max(cr1, cl_s)))
    if crosses_pe:
        u_hipot = rq["ac_basic_dc"] / rq["n"] * STRING_SHARE
        if u_hipot > el["umax"]:
            conds.append("production hipot HV-PE puts %.0f V on each element (> %.0f V): disconnect or test below it"
                         % (u_hipot, el["umax"]))
    return ("FAIL" if fails else "PASS WITH CONDITION" if conds else "PASS"), fails + conds


# =====================================================================================================================
# 6. AUDIT - every barrier on every board, grouped per (board, part, circuit pair)
# =====================================================================================================================
def system_of(circ):
    return CIRCUITS[circ][0]


def lookup_part(mpn, value):
    if mpn in PARTS:
        return PARTS[mpn]
    return next((v for k, v in PARTS.items() if mpn.startswith(k) or value.startswith(k)), None)


def pair_key(sa, sb):
    return tuple(sorted((sa, sb), key=lambda s: list(SYSTEMS).index(s)))


def build_audit(boards, problems):
    rows = {}

    def add(board, ref, mpn, what, sa, sb, kind, node, rq, rt, ev, src, zone=PD_INSIDE, string=None):
        k = (board, mpn, what, sa, sb, kind, node, rq["imp_mode"])
        if k not in rows:
            rows[k] = dict(board=board, refs=[], mpn=mpn, what=what, sa=sa, sb=sb, kind=kind, node=node, rq=rq, rt=rt,
                           ev=ev, src=src, zone=zone, string=string)
        rows[k]["refs"] += [ref] if isinstance(ref, str) else ref

    for board, d in boards.items():
        comps, nets, chains = d["comps"], d["nets"], d["barriers"]
        dmap = board_cfg(BOARD_DOMAINS, board)
        if dmap is None:                              # unknown board: already a problem (board_barriers), skip it
            continue
        if set(dmap) == {None}:                       # no domain data: no isolator may hide on this board
            for r, c in comps.items():
                if c["mpn"] and (lookup_part(c["mpn"], c["value"]) or c["mpn"] in ELEMENTS):
                    problems.append("%s %s %s: barrier part on a board built without isolation domains" % (board, r, c["mpn"]))
            continue
        covered = set()
        for s in find_strings(comps, nets, dmap):
            systems = sorted({system_of(c) for c in s["circuits"]}, key=list(SYSTEMS).index)
            hv = [x for x in systems if x in HV_SYSTEMS]
            if not hv or set(systems) - set(HV_SYSTEMS) - {"PE"}:
                problems.append("%s string %s: unexpected end circuits %s" % (board, s["refs"], s["circuits"]))
                continue
            sb = "PE" if "PE" in systems else hv[-1]
            sw = any(CIRCUITS[c][1] == "sw" for c in s["circuits"])
            port_side = sb == "PE" or any(TERMINAL_NET.search(n) for n in s["ends"])
            rq = barrier_req("F", hv[0], sb, "sw" if sw else "dc", imp="port" if port_side and not sw else "internal",
                             n_series=len(s["refs"]), share=STRING_SHARE)
            rq["ac_basic_dc"] = barrier_req("B", hv[0], "PE")["ac"] * math.sqrt(2)
            el = ELEMENTS[s["mpn"]]
            what = "series string of %d (%s)" % (len(s["refs"]), "HV-PE" if sb == "PE" else "within " + hv[0])
            add(board, ",".join(s["refs"]), s["mpn"], what, hv[0], sb, "F", "sw" if sw else "dc", rq, el, el["ev"],
                "string", string=dict(n=len(s["refs"]), pe=sb == "PE"))
            covered |= set(s["refs"])
        for ch in chains:
            if set(ch["refs"]) <= covered:
                continue
            if len(ch["mpns"]) > 1:
                problems.append("%s chain %s mixes parts %s" % (board, ch["refs"], ch["mpns"]))
            rt0 = lookup_part(ch["mpn"], ch["value"])
            if rt0 is None:
                problems.append("%s %s %s: barrier part with NO rating entry (add it to PARTS)" % (board, ch["refs"], ch["mpn"]))
                continue
            systems = sorted({system_of(c) for c in ch["circuits"]}, key=list(SYSTEMS).index)
            pairs = [(a, b) for i, a in enumerate(systems) for b in systems[i + 1:]] or [(systems[0], systems[0])]
            for sa, sb in pairs:
                kind = required_type(sa, sb)
                rt = dict(rt0, **rt0.get("pairs", {}).get(pair_key(sa, sb), {}))
                if "pairs" in rt0 and pair_key(sa, sb) not in rt0["pairs"]:
                    problems.append("%s %s: no rating for circuit pair %s-%s" % (board, ch["refs"], sa, sb))
                    continue
                node = rt0.get("node") or ("sw" if any(CIRCUITS[c][1] == "sw" for c in ch["circuits"]
                                                       if system_of(c) in (sa, sb)) else "dc")
                elv = not ({sa, sb} & set(HV_SYSTEMS))
                rk = "B" if rt.get("composite") and kind == "R" else kind
                rq = barrier_req(rk, sa, sb, node, imp="field" if elv else None)
                rq["kind_required"] = kind
                add(board, ch["refs"], ch["mpn"], "netlist %s" % ch["kind"].lower(), sa, sb, kind, node, rq, rt,
                    rt0.get("ev", ""), "netlist")
        for np in NONPART:
            for r, c in comps.items():
                if not np["match"](c):
                    continue
                own = board_cfg(BOARD_MAIN_HV, board)
                if c["dom"] in dmap and system_of(dmap[c["dom"]]) in HV_SYSTEMS:
                    own = system_of(dmap[c["dom"]])
                else:
                    for ch in chains:
                        hv = sorted(system_of(x) for x in ch["circuits"] if system_of(x) in HV_SYSTEMS)
                        if r in ch["refs"] and hv:
                            own = hv[0]
                prs = np["pair"] if isinstance(np["pair"], list) else [np["pair"]]
                for sa, sb in prs:
                    sa, sb = (own if sa == "own" else sa), (own if sb == "own" else sb)
                    if sa is None or sb is None:
                        problems.append("%s %s: non-part barrier %s on a board without an HV system" % (board, r, np["id"]))
                        continue
                    kind = np.get("kind") or required_type(sa, sb)
                    elv = not ({sa, sb} & set(HV_SYSTEMS))
                    rq = barrier_req(kind, sa, sb, np["node"], imp=np.get("imp") or ("field" if elv else None))
                    rq["kind_required"] = kind
                    add(board, r, np["id"], np["what"], sa, sb, kind, np["node"], rq, np["rt"], np["rt"].get("ev", ""),
                        "non-part", zone=np.get("zone", PD_INSIDE))
    out = sorted(rows.values(), key=lambda x: (x["board"], x["src"], x["mpn"], x["sa"], x["sb"]))
    for row in out:
        row["refs"] = sorted(set(",".join(row["refs"]).split(",")), key=lambda x: (re.sub(r"\d", "", x), int(re.sub(r"\D", "", x) or 0)))
        row["v"] = {}
        for alt in ALTS:
            if row["string"]:
                row["v"][alt] = judge_string(row["rq"], row["rt"], alt, row["string"]["pe"])
            else:
                row["v"][alt] = judge(row["rq"], row["rt"], alt, row["zone"])
    # coverage: every Domain=ISOLATOR/CROSSING ref on every board is in some row
    for board, d in boards.items():
        inrows = {r for row in out if row["board"] == board for r in row["refs"]}
        for ch in d["barriers"]:
            for r in ch["refs"]:
                if r not in inrows:
                    problems.append("%s %s (%s): barrier part not covered by the audit" % (board, r, ch["mpn"]))
    return out




# =====================================================================================================================
# 7. REQUIREMENT TABLES AND LAYOUT RULES (derived from the same functions as the audit)
# =====================================================================================================================
UP_GOOD = 4000.0     # an SPD whose effective pole-PE level (incl. leads) is <= 4.0 kV lets reinforced drop to 6 kV
REQ_CASES = [        # (label, kind, sa, sb, node, imp)
    ("Basic HV-PE, DC pole (Y caps, fuse bases, terminals)", "B", "PV", "PE", "dc", None),
    ("Basic HV-PE, switch node (TO-247 pad, magnetic cores)", "B", "PV", "PE", "sw", None),
    ("Reinforced HV-PELV, DC pole (AMC, TPSI, contactor coil)", "R", "PV", "PELV", "dc", None),
    ("Reinforced HV-PELV, PV switch node (UCC21710/UCC14241)", "R", "PV", "PELV", "sw", None),
    ("Reinforced HV-PELV, AUX-HV flyback T1", "R", "AUX", "PELV", "sw", None),
    ("Basic DAB port 1 - PE, AC node (module, T1 core)", "B", "DAB1", "PE", "sw", None),
    ("Reinforced DAB port 1 - PELV, AC node (drivers, T1 NTC)", "R", "DAB1", "PELV", "sw", None),
    ("Basic DAB port 1 - port 2 (transformer, simple separation)", "B", "DAB1", "DAB2", "sw", None),
    ("Functional pole-pole, terminal side (dividers, X caps)", "F", "PV", "PV", "dc", "port"),
    ("Functional inside the power stage (DESAT, device pins)", "F", "PV", "PV", "sw", "internal"),
    ("Functional PELV - field I/O (CAN, RS-485, DI, BMS)", "F", "PELV", "FIELD", "dc", "field"),
]


def req_summary(label, kind, sa, sb, node, imp):
    r = barrier_req(kind, sa, sb, node, imp)
    good = imp_required(kind, min(r["imp_nospd"] if kind != "R" else 6000.0, UP_GOOD)) if kind != "F" else r["imp_spd"]
    grp_pcb = mat_group(PCB_CTI)
    row = dict(label=label, kind=kind, sa=sa, sb=sb, node=node, u_w=r["u_w"], u_rp=r["u_rp"], u_tov=r["u_tov"],
               f_khz=r["f"] / 1e3, imp=r["imp_nospd"], imp_spd=r["imp_spd"], spd=r["spd"], imp_good=good,
               ac=r["ac"], ac_dc=r["ac"] * math.sqrt(2), ac_lenient=r["ac_lenient"],
               pd_test=r["u_pd_test"] if r["pd_needed"] else 0.0, pd_ext=r["u_pd_ext"] if r["pd_needed"] else 0.0,
               cr_pd1=req_creepage(r, 1, grp_pcb, pwb=True), cr_pd2=req_creepage(r, 2, grp_pcb, pwb=True),
               cr_pd2_I=req_creepage(r, 2, "I"), cr_pd3_I=req_creepage(r, 3, "I"), cr_pd3_III=req_creepage(r, 3, "III"))
    for alt in ALTS:
        row["cl_%d" % alt] = req_clearance(r, alt, False)
        row["cl_spd_%d" % alt] = req_clearance(r, alt, True)
        row["cl_good_%d" % alt] = clearance(good, PD_INSIDE, alt, r["u_rp"], r["hf"], kind)
        row["imp_test_%d" % alt] = test_impulse_sea_level(r["imp_nospd"], alt) if r["imp_nospd"] else 0.0
    return row


def max_up_for(alt, clr_mm=8.0):
    """Largest effective SPD level (pole-PE, incl. leads) that keeps reinforced clearance <= clr_mm at alt."""
    best = 0.0
    for up in range(1500, 6001, 50):
        if clearance(imp_required("R", float(up)), PD_INSIDE, alt) <= clr_mm + 1e-9:
            best = float(up)
    return best


# Per board: which circuit pairs exist (from the audit rows) plus the always-present HV ones.
def layout_rules(audit):
    rules = defaultdict(dict)

    def put(b, sa, sb, kind, node, imp):
        k = (sa, sb, kind, imp if kind == "F" else None)
        if rules[b].get(k) != "sw":
            rules[b][k] = node
    for row in audit:
        b = row["board"]
        if not row["string"]:
            put(b, row["sa"], row["sb"], row["kind"], row["node"], row["rq"]["imp_mode"])
        for s in [x for x in (row["sa"], row["sb"]) if x in HV_SYSTEMS]:
            put(b, s, "PE", "B", "dc", None)
            put(b, s, s, "F", "dc", "port" if b.startswith(("PV-PORT", "DAB60", "AUX-HV")) else "internal")
    out = {}
    for b, pairs in rules.items():
        rows = []
        for (sa, sb, kind, imp), node in sorted(pairs.items(), key=lambda x: (-ORDER[x[0][2]], str(x[0]))):
            r = barrier_req(kind, sa, sb, node, imp)
            grp = mat_group(PCB_CTI)
            pwb = r["u_w"] <= 1000
            rows.append(dict(sa=sa, sb=sb, kind=kind, node=node, u_w=r["u_w"], u_rp=r["u_rp"], f=r["f"],
                             where={"port": "terminal side / port", "internal": "inside the power stage",
                                    "field": "ELV / field I/O"}.get(imp, "switch node" if node == "sw" else "DC poles"),
                             cl={a: req_clearance(r, a, False) for a in ALTS},
                             cl_spd={a: req_clearance(r, a, True) for a in ALTS},
                             cr2=req_creepage(r, 2, grp, pwb), cr1=req_creepage(r, 1, grp, pwb),
                             cr3=req_creepage(r, 3, grp), ac=r["ac"], imp=r["imp_nospd"], imp_spd=r["imp_spd"],
                             pd=r["u_pd_test"] if r["pd_needed"] else 0.0))
        out[b] = rows
    return out


# =====================================================================================================================
# 8. FINDINGS for the board designers (gen/data/review_insulation.csv). 'covers' = part keys whose FAIL rows the
#    finding reports; the self-check fails if a FAIL row is covered by none.
# =====================================================================================================================
def findings(V):
    F = []

    def f(sev, where, finding, evidence, rec, covers=()):
        F.append(dict(id="IC-%02d" % (len(F) + 1), severity=sev, where=where, finding=finding, evidence=evidence,
                      recommendation=rec, covers=set(covers)))
    f("critical", "all HV boards (PVCELL-25, GDRV-HB, PV-PORT, PV-PORT-180, DAB60, AUX-HV); PV-C6",
      "All HV-PELV isolator ICs are 8.0-8.5 mm packages (UCC21710, UCC14241-Q1, AMC3330, AMC3302, ISO7720, UCC12050, "
      "TPSI3050; only the CNY65B opto has 14 mm). Reinforced at 1000 V DC OVC II needs 8 kV -> %.1f / %.1f / %.1f mm "
      "clearance at 2000 / 3000 / 4000 m. The drawn port SPD (DEHN 952515, Up <= 4 kV at In 12.5 kA) gives Up,eff ~%.1f "
      "kV with 0.5 m of leads, so the reinforced requirement only drops to %.2f kV (160 %% rule) - the same 8 mm row: "
      "the SPD buys nothing for reinforced insulation. These barriers therefore FAIL at 3000 and 4000 m. Coating does not "
      "help clearance, and PV-C6 '> 3000 m derating' cannot be met by power derating (insulation does not derate)."
      % (V["clR"][2000], V["clR"][3000], V["clR"][4000], V["upeff_952515"] / 1e3, V["impR_spd"] / 1e3),
      "this report sec. 3-4; datasheets: UCC21710.pdf p6, UCC14241-Q1.pdf p7, AMC3330.pdf p6, AMC3302.pdf p6, "
      "ISO7721.pdf p10, UCC12050.pdf p5, TPSI3050-Q1.pdf p6 (CLR 8-8.5 mm)",
      "Owner decision (ECO-10 / PV-C6): (a) claim 2000 m; or (b) fit SPDs whose effective pole-PE level incl. leads is "
      "<= %.1f kV (e.g. Up 2.5 kV pole-PE with <= 0.5 m leads), rated for the target altitude, with a monitored remote "
      "contact that stops the converter - then reinforced = 6 kV -> %.1f / %.1f / %.1f mm and the 8 mm packages pass to "
      "4000 m; or (c) isolators with >= %.1f mm clearance or IEC 60664-3 Type 2 encapsulation qualified at the "
      "altitude-corrected impulse." % (V["up_max_4000"] / 1e3, V["clR_good"][2000], V["clR_good"][3000],
                                      V["clR_good"][4000], V["clR"][4000]),
      covers=["UCC21710DWR", "UCC14241QDWNRQ1", "AMC3330DWE", "AMC3302DWE", "ISO7720FDWR", "UCC12050DVE",
              "TPSI3050QDWZRQ1", "B88269X7340C011"])
    f("major", "PV-PORT / PV-PORT-180 U303, U503 (AMC3302); DAB60 U1303, U1503 (AMC3302), U501, U1001 (UCC12050)",
      "Barrier surge rating VIOSM 6250 V (no VIMP given; VDE 0884-11 edition) is below the 8 kV reinforced impulse "
      "and below the %.2f kV left after the drawn SPD credit: FAIL at every altitude." % (V["impR_spd"] / 1e3),
      "sensing/AMC3302.pdf p6 (VIOSM 6250 VPK, test 10 kV); power-supply/UCC12050.pdf p5 (VIOSM 6250 VPK)",
      "Either the SPD credit of IC-01 option (b) (reinforced 6 kV <= 6.25 kV), or replace by parts rated VIMP >= 8 kV "
      "and VIOSM >= 10 kV (check current TI revisions of AMC3302 / a 5 kVrms DC/DC with a 60747-17 VIMP rating).",
      covers=["AMC3302DWE", "UCC12050DVE"])
    f("major", "PV-PORT, PV-PORT-180, DAB60 (AMC3330 x6/x6/x6); PVCELL-25, GDRV-HB, DAB60 (UCC14241-Q1)",
      "AMC3330 VIMP 7700 V and UCC14241-Q1 VIMP 7692 V are below the 8 kV reinforced impulse: they pass only with an "
      "SPD credit (7.65 kV with the drawn 952515: 0.5-0.7 %% margin - not a design margin). AMC3330 / AMC3302 "
      "VISO 4250 V rms is also below the conservative reinforced AC test (%.0f V rms, TOV rule) and passes only the "
      "lenient 2U+1000 rule (%.0f V rms)." % (V["acR"], V["acR_len"]),
      "sensing/AMC3330.pdf p6 (VIMP 7700, VISO 4250); gate-drivers/UCC14241-Q1.pdf p7 (VIMP 7692)",
      "Resolve together with IC-01 (SPD credit) and confirm the reinforced AC test level for DC circuits in the "
      "purchased EN 62477-1; otherwise choose parts with VIMP >= 8 kV.", covers=["AMC3330DWE", "UCC14241QDWNRQ1"])
    f("critical", "PV-PORT / PV-PORT-180 K202, K403; DAB60 K1202, K1402 (Omron G7L-2A-X DC24 precharge relay)",
      "The relay coil is driven from the PELV DO, its contacts sit on the 1000 V poles: this is protective separation "
      "(reinforced). The datasheet gives only 4000 V AC coil-contacts (1 min) and 10 kV impulse - below the %.0f V rms "
      "reinforced AC test by either rule - and states no insulation type, creepage or clearance." % V["acR_len"],
      "protection/G7L-X.pdf p2 (Cat. J216-E1-04)",
      "Get Omron's VDE 40045061 certificate content (insulation category coil-contact); if it is not reinforced for "
      ">= 1000 V, feed the coil from its own isolated supply (the supply barrier then adds the second insulation) or "
      "use a relay with a stated reinforced coil-contact rating.", covers=["G7L-2A-X DC24"])
    f("major", "PV-PORT / PV-PORT-180 K201, K401, K402; DAB60 K1201, K1401 (TDK HVC43-MC)",
      "Coil and mirror (FB) contact are PELV, main contacts HV: reinforced needed. TDK gives 4400 V AC contact-to-coil "
      "and Uimp 8 kV (IEC 60947-4-1) - consistent with reinforced at <= 2000 m - but no insulation type and no "
      "creepage, and p11 asks to separate or shield the LV side. At 3000 / 4000 m the 8 kV air rating derates to "
      "~%.1f / %.1f kV < 8 kV." % (V["hvc_3000"] / 1e3, V["hvc_4000"] / 1e3),
      "protection/HVC43MC.pdf p2-3, p11 (version 02)",
      "Ask TDK to confirm reinforced (or double) insulation coil + aux vs main circuit for 1000 V DC, OVC II, PD2 and "
      "the target altitude; route coil/FB wiring away from the HV studs with reinforced spacing (layout table).",
      covers=["B88269X7340C011"])
    f("major", "PVCELL-25, GDRV-HB, DAB60 (UCC14241-Q1); gen/gdrv.py design_check",
      "UCC14241-Q1 certification is still 'planned' in the datasheet (D-016 risk) and its VIORM 1414 Vpk leaves %.0f %% "
      "margin over the PV switch-node recurring peak (%.0f V at 1000 V; %.0f V at the 1100 V trip corner = %.0f %%). "
      "The gdrv design check compares only VIOWM with the 1000 V DC bus - it misses VIORM vs the switch node and the "
      "8 kV impulse (VIMP 7692 V)." % (100 * (1414 / PV_RP - 1), PV_RP, PV_RP_TRIP, 100 * (1414 / PV_RP_TRIP - 1)),
      "gate-drivers/UCC14241-Q1.pdf p7, p12; hardware/GDRV-HB/outputs/GDRV-HB_design_check.txt ('Barrier vs 1000 V DC "
      "bus')", "Add VIORM >= switch-node recurring peak and VIMP >= reinforced impulse to gdrv.design_check(); hold the "
      "VDE certificate as a release condition (D-016).", covers=["UCC14241QDWNRQ1"])
    f("critical", "PVCELL-25 Q301-Q308 (MSC035SMA170B4 on the PE heatsink)",
      "The heatsink insulation of the TO-247-4 tabs (drain = A+/B+ or a switch node: %.0f V recurring at 32 kHz, 85 V/ns) "
      "is basic insulation and is not specified at all: no pad, no thickness, no bushing/clip, Rth 0.35 K/W is an "
      "estimate. Requirement: basic for 1000 V DC, impulse 6 kV, AC %.0f V rms, PD-free (extinction >= %.0f Vpk, test "
      "%.0f Vpk <= 10 pC), pad overhang >= creepage %.1f mm (PD2) / %.1f mm (PD3 air path) and >= clearance %.1f / %.1f / "
      "%.1f mm (2000/3000/4000 m)." % (PV_RP, V["acB"], V["pdB_ext"], V["pdB_test"], V["crB2"], V["crB3"],
                                       V["clB"][2000], V["clB"][3000], V["clB"][4000]),
      "gen/pvcell.py MSC035SMA170B4 desc ('insulated mount'); sim/out/pv_design/report.md (case-sink 'insulated, "
      "estimate')", "Specify the pad (ceramic AlN/Al2O3 or a PD-qualified film), mounting (clip or insulated bushing "
      "for the PE screw), overhang, and add a PD type test on the assembled heatsink; re-run the thermal model with its "
      "real Rth.", covers=["TO247-PAD"])
    f("major", "DAB60 T1101 (transformer), L1101 (series inductor), both cold-plate mounted",
      "Only the winding-to-winding class is given ('basic (pending system decision)', 1850 V DC basis, PD/hipot 'TBD by "
      "ECO-10'); winding-to-core / cold-plate (PE) insulation of T1101 and L1101 is not specified at all. Coordinated "
      "levels: P1-P2 basic (simple separation) 1850 V DC, %.0f Vpk recurring at 100 kHz -> PD <= 10 pC at %.0f Vpk, AC %.0f "
      "V rms, impulse 6 kV, creepage %.1f mm (group I) / %.1f mm (IIIa) PD2; windings-core basic: PD at %.0f Vpk, AC "
      "%.0f V rms; windings-NTC reinforced: PD at %.0f Vpk, AC %.0f V rms, impulse 8 kV."
      % (V["dab_rp12"], V["dab_pd12"], V["dab_ac12"], V["dab_cr12_I"], V["dab_cr12_III"], V["dab_pdB"], V["acB"],
         V["dab_pdR"], V["acR"]),
      "sim/out/dab_design/dab_spec.json transformer.isolation, series_inductor; gen/dab60.py XF_DESC / LS_DESC",
      "Write these levels into dab_spec transformer.isolation and series_inductor (and the RFQ): the 'pending' class "
      "is resolved - basic (simple separation) at 1850 V DC is the requirement.", covers=["XFMR-CORE", "LSER-CORE"])
    f("major", "PV-PORT / PV-PORT-180 L201, L401; DAB60 L1201, L1401 (CUSTOM CM choke)",
      "The CM-choke spec gives '+4 kV surge' winding-core: below the 6 kV basic impulse and below the %.2f kV left with "
      "the drawn SPD credit; no AC test or creepage is stated." % (V["upeff_952515"] / 1e3),
      "gen/port.py CMC160 / CMC200 description", "Specify winding-core and winding-winding: basic, 1000 V DC, impulse "
      ">= 6 kV, AC %.0f V rms 60 s, creepage >= %.1f mm (PD2) / %.1f mm (PD3)." % (V["acB"], V["crB2"], V["crB3"]),
      covers=["CM-CHOKE"])
    f("minor", "PV-PORT / PV-PORT-180 C201, C202, C401, C402; DAB60 C1201, C1202, C1401, C1402 (X capacitor RFQ)",
      "RFQ impulse >= 4.5 kV pole-pole is below the effective pole-pole SPD level with leads (%.2f kV for the drawn "
      "SPDs) and below 6 kV without credit." % (V["upeff_pp_worst"] / 1e3),
      "gen/port.py XCAP_2U2; sim/out/port_design/report.md sec. 9 (PA-07)",
      "Raise the RFQ to >= 6 kV (no SPD dependency) or keep the capacitor leads at the SPD terminals and require "
      ">= Up,eff + 10 %.", covers=["X-CAP"])
    f("major", "PV-PORT / PV-PORT-180 U603, U604 (TPSI3050, IMD test switches); gen/port.py imd()",
      "The IMD switch secondaries are declared in the PE domain, but with the switch open the PE-side SiC of the "
      "common-source pair blocks and the source/gate island follows the pole through the other FET's body diode: "
      "BUS- sits ~V/2 below PE in normal operation (up to %.0f V with a fault). U603/U604 are therefore HV-PELV "
      "reinforced barriers (TPSI3050 is reinforced, so the part passes) and IMDx_SRC/G/DRV/VDDH/VDDM/SW are HV nets."
      % PV_V, "hardware/PV-PORT/outputs/PV-PORT_netlist.xml (Q601/Q602, Q603/Q604 sources on IMDx_SRC, one drain on PE)",
      "Declare those nets HV (or their own floating domain) in port.imd() so the build check and the layout apply "
      "reinforced spacing to PELV and basic spacing to PE.")
    f("major", "PV-PORT / PV-PORT-180 FV201 (952515), FV401 (59.D040); DAB60 FV1201, FV1401",
      "SPD status is not monitored (952515 FM contact not wired; 59.D040 has no remote contact - 59.D041 has), so a "
      "disconnected SPD goes unnoticed and no insulation inside the module may rely on its protection level. UCPV "
      "1000 V is below the PV 1100 V OV trip and the DAB port-1 1048 V load-rejection peak. 59.D040 is rated only to "
      "3000 m.", "protection/ProTec-T2-1000DCGU-3Y.pdf p1 (altitude 3000 m, RC optional); gen/port.py block note "
      "('remote-signal contact not wired')", "Use the -R / FM variants, wire the contact to a SYS-IO-AUX DI and stop "
      "the converter on it; confirm 1100 V tolerance with the SPD makers; pick SPDs rated for the claimed altitude.",
      covers=["59.D040", "952515"])
    f("major", "sim/out/port_design/report.md sec. 9 and PA-08; gen/port.py root note",
      "The surge table rates reinforced barriers against Up = 4 kV ('AMC3330 VIMP 7700 V, margin 1.93', 'AMC3302 "
      "VIOSM 6250 V, margin 1.56'): with an SPD credit the reinforced requirement is 160 %% of Up,eff (>= 6.4 kV for Up "
      "4 kV alone, %.2f kV with leads), not Up; without credit it is 8 kV. PA-08's 'clearance at 3000 m to be "
      "confirmed' resolves to: 8 mm packages do not meet it (%.1f mm)." % (V["impR_spd"] / 1e3, V["clR"][3000]),
      "sim/out/port_design/report.md sec. 9 table", "Replace the margin column with the coordinated requirement "
      "(this report sec. 3).")
    f("major", "hardware/DAB60/outputs/DAB60_design_check.txt (Insulation, item 4)",
      "'HV1/HV2 -> PELV only through reinforced parts: UCC12050 VIOWM 1697 VDC, AMC3330 1700 VDC, ISO7720F 2121 VDC vs "
      "1000 V DC working' is incomplete and not sufficient: HV1/HV2 also reach PELV through the gate drivers and their "
      "bias modules, TPSI3050, AMC3302, the HVC43 / G7L coils, the shunt-NTC pads and the transformer NTC; and VIOWM "
      "alone does not cover the 8 kV impulse that UCC12050 (VIOSM 6250 V) and AMC3330 (VIMP 7700 V) do not meet.",
      "DAB60_design_check.txt; this report sec. 4", "Base the DAB60 insulation check on this audit (or import its "
      "verdicts) instead of VIOWM only.")
    f("major", "AUX-HV T301 (CUSTOM flyback transformer); sim/out/aux_hv_design/report.md sec. 11",
      "The T1 spec says reinforced 'for 1000 V DC working', but the primary drain end carries a %.0f V recurring peak "
      "at 65 kHz (%.0f V at the 1100 V trip); TIW certified for 1000 V DC working says nothing about PD at that "
      "stress. Coordinated levels: PD <= 10 pC at %.0f Vpk (extinction >= %.0f Vpk), AC %.0f V rms 60 s, impulse "
      "8 kV, creepage 20 mm PD2 (bobbin CTI not stated -> IIIa) or 6.4 mm PD1 and >= clearance."
      % (AUX_RP, AUX_RP_TRIP, V["aux_pd"], V["aux_pd_ext"], V["acR"]),
      "gen/aux_hv.py T1 description; sim/out/aux_hv_design/aux_hv_spec.json summary.vds_cont / vds_trans",
      "Add these levels to the T1 description and the RFQ; require a PD test on every transformer.")
    f("major", "PVCELL-25 L401 (CUSTOM inductor), CS501 (LA 150-P + lead A)",
      "The inductor winding is a switch node (%.0f V recurring at 32 kHz). Its spec gives the embedded-NTC insulation "
      "only as 'reinforced for 1000 V DC working' and winding-core as 'basic' with no levels. Coordinated: NTC-winding "
      "reinforced PD <= 10 pC at %.0f Vpk, AC %.0f V rms, impulse 8 kV; winding-core basic PD at %.0f Vpk, AC %.0f V "
      "rms, impulse 6 kV. The LA 150-P (basic 1000 V, PD test 1.3 kV rms = 1838 Vpk) plus the screened lead A "
      "(supplementary, 'PD-free above 2.1 kV peak') form double insulation - acceptable, but the lead must be "
      "type-tested to the basic levels." % (PV_RP, V["pdR_sw"], V["acR"], V["pdB_test"], V["acB"]),
      "gen/pvcell.py L_DESC; sensing/LA_150-P.pdf p3", "Add the levels to L_DESC and the RFQ.")
    f("major", "PVCELL-25, GDRV-HB, DAB60, AUX-HV boards (PV-PORT already has it as PA-08)",
      "Conformal coating to PD1 (IEC 60664-3, Type 1) is REQUIRED over every HV-PELV isolator footprint and its HV "
      "copper on all HV boards: 8-8.5 mm package creepage < %.1f mm reinforced at PD2 (PCB and group I package). Only "
      "PV-PORT states it; the DAB60 check even asks for 'reinforced creepage at those 3 footprints per bridge (8 mm "
      "bodies)', which is not achievable uncoated. CNY65B (14 mm, CTI 200 -> 20 mm needed at PD2) needs it too."
      % V["crR2"], "this report sec. 4-5", "State the coating requirement on every HV board (root note + layout rule).")
    f("major", "mechanical zoning; ECO-10 'PD3 external'; PV-12 forced air, PV-C6 IP20",
      "The PV module is forced-air cooled at IP20: boards and fuses in the cooling-air path see outside dust and "
      "humidity (the port design puts the fuses in the inlet air). At PD3 basic creepage at 1000 V is %.1f mm (group "
      "I) / %.1f mm (IIIa) and reinforced %.1f / %.1f mm - not achievable on the boards." % (V["crB3_I"], V["crB3"],
                                                                                               V["crR3_I"], V["crR3"]),
      "REQUIREMENTS ECO-10, PV-12, PV-C6; sim/out/port_design/report.md (A_T_FUSE 'fuses in the INLET air path')",
      "Define PD2 compartments for the boards (or coat all HV boards to PD1); keep only parts with PD3 ratings "
      "(terminals, fuse bases, SPDs) in the air path and give them PD3 creepage.")
    f("major", "DAB60 assembly: port section (both port.port() blocks, io sheet), busbars and harness carrying both ports",
      "Port-1 and port-2 circuits are separated by basic insulation at %.0f V DC (both ports with a first fault at "
      "opposite poles): above the 1000 V limit of the printed-wiring creepage columns, so the PCB needs %.1f mm (CTI "
      ">= 600, group I) or %.1f mm (standard FR-4, IIIa) uncoated at PD2, %.1f mm coated (PD1), clearance %.1f / %.1f / "
      "%.1f mm. This applies wherever port-1 and port-2 conductors meet: the two port blocks on the port board, the "
      "transformer terminations, busbars and harnesses." % (D1 + D2, V["dab_cr12_I"], V["dab_cr12_III"], V["dab_cr12_pd1"], V["clB"][2000], V["clB"][3000],
                  V["clB"][4000]), "this report sec. 5 (DAB60 table)",
      "Specify a CTI >= 600 laminate for DAB60 or keep the two port areas on separate boards / coated.")
    f("minor", "PV-PORT / PV-PORT-180 RT201, RT401; DAB60 RT1201, RT1401 (shunt NTC on BUS-)",
      "The reinforced pad under the shunt NTC is a requirement in the description only: no part, material, thickness "
      "or test level; the probe's own test is 1000 V AC / 1 s.", "gen/port.py NTC_SH; sensing/B57703M.pdf p2",
      "Specify the pad: reinforced, AC %.0f V rms, impulse 8 kV, creepage around the tag >= %.1f mm (PD2) and >= the "
      "clearance at the claimed altitude; or use a double-insulated probe." % (V["acR"], V["crR2"]))
    f("minor", "PV-PORT / PV-PORT-180 / DAB60 RST 200 resistors, 3100U thermostats, NH fuse bases, DC terminals; "
      "DAB60 DD600N16K and CBB011M12GM4T base plates",
      "HV-PE basic insulation of chassis parts is only partly documented: no impulse/creepage data (RST 200: 3.5 kV "
      "rms only; DD600N16K: 3.0 kV rms 1 min; CBB011M12GM4T: Visol 3 kV, no PD data at the 100 kHz AC-node stress; "
      "fuse bases: Uimp 8 kV only); thermostat pad and DC-terminal creepage are text requirements without values.",
      "protection/Miba-RST-200.pdf p1; power-semiconductors/DD600N16K.pdf p2; CBB011M12GM4T.pdf p3; "
      "protection/NH-gPV-1000VDC.pdf p2", "Collect creepage/impulse (and for the module PD) data from the makers; give "
      "the DC terminals PD3 creepage %.1f mm (group I) / %.1f mm (IIIa) to PE." % (V["crB3_I"], V["crB3"]))
    f("minor", "PVCELL-25, GDRV-HB, DAB60 DESAT strings (3 x US1M, SMA); MSC035SMA170B4 pins; CBB011M12GM4T terminals",
      "Functional insulation inside the power stage is below the PD2 table values: SMA pad gap 1.5 mm vs %.1f mm per "
      "diode; TO-247-4 drain-to-source/gate pads ~0.5 mm (2.54 mm pitch, no package creepage data) vs %.1f mm; module "
      "terminal-to-terminal creepage 6.3 mm (CTI 200) vs 10 mm." % (V["desat_cr"], V["crB2"]),
      "power-semiconductors/US1M.pdf p4; MSC035SMA170B4.pdf p9-10; CBB011M12GM4T.pdf p3",
      "Coat the power-stage boards to PD1 and add a milled slot between the drain pin and pins 2-4 of each TO-247-4; "
      "confirm by the EN 62477-1 functional-insulation short-circuit test.")
    f("minor", "production test (all HV boards / module)",
      "The module hipot HV-PE (%.0f V rms or %.0f V DC) and HV-PELV (%.0f V rms) cannot be applied with the SPDs (UCPV "
      "1000 V), the IMD SiC switches (1700 V) and the Y capacitors (1500 V DC) connected; the dividers take %.0f V per "
      "element (TNPV1206 700 V)." % (V["acB"], V["acB"] * math.sqrt(2), V["acR"], V["hipot_per_el"]),
      "this report sec. 5 (tests)", "Write the hipot procedure: SPDs plugged out, IMD and Y-capacitor paths opened (or "
      "a DC test below their ratings), isolator barriers tested at component level (VISO) and the boards' spacing by "
      "measurement.")
    return F


# =====================================================================================================================
# 9. OUTPUTS
# =====================================================================================================================
def key_values():
    rR, rB = barrier_req("R", "PV", "PELV", "dc"), barrier_req("B", "PV", "PE", "sw")
    r12, rdB, rdR = barrier_req("B", "DAB1", "DAB2", "sw"), barrier_req("B", "DAB1", "PE", "sw"), \
        barrier_req("R", "DAB1", "PELV", "sw")
    raux, rRs = barrier_req("R", "AUX", "PELV", "sw"), barrier_req("R", "PV", "PELV", "sw")
    acR, acR_len = u_ac_test("R", SYSTEM_V, SYSTEM_V)
    drawn = sorted({m for s in HV_SYSTEMS for m in SYSTEMS[s].get("spds", [])}) or [SPD_DRAWN]
    return dict(clR={a: req_clearance(rR, a, False) for a in ALTS}, clB={a: req_clearance(rB, a, False) for a in ALTS},
                clR_good={a: clearance(6000.0, PD_INSIDE, a) for a in ALTS}, upeff_952515=up_eff("952515"),
                impR_spd=rR["imp_spd"], up_max_4000=max_up_for(4000), acR=acR, acR_len=acR_len, acB=rB["ac"],
                hvc_3000=derate_imp(8000, 3000), hvc_4000=derate_imp(8000, 4000), pdB_ext=rB["u_pd_ext"],
                pdB_test=rB["u_pd_test"], crB2=creepage(1000, 2, "III", True), crB3=creepage(1000, 3, "III"),
                crB3_I=creepage(1000, 3, "I"), crR2=creepage(1000, 2, "III", True, "R"),
                crR3=creepage(1000, 3, "III", kind="R"), crR3_I=creepage(1000, 3, "I", kind="R"),
                dab_rp12=r12["u_rp"], dab_pd12=r12["u_pd_test"], dab_ac12=r12["ac"],
                dab_cr12_I=creepage(r12["u_w"], 2, "I"), dab_cr12_III=creepage(r12["u_w"], 2, "III"),
                dab_cr12_pd1=creepage(r12["u_w"], 1, "I"), dab_pdB=rdB["u_pd_test"], dab_pdR=rdR["u_pd_test"],
                upeff_pp_worst=max(up_eff(m, "pp") for m in drawn), aux_pd=raux["u_pd_test"],
                aux_pd_ext=raux["u_pd_ext"], pdR_sw=rRs["u_pd_test"],
                desat_cr=creepage(PV_V / 3 * STRING_SHARE, 2, "III", True),
                hipot_per_el=rB["ac"] * math.sqrt(2) / 6 * STRING_SHARE, drawn=drawn)


TAGS = [("coat to PD1", "coat"), ("PD1 coating", "coat"), ("Type 2 coating", "Type-2 coat / func test"), ("SPD credit", "SPD"), ("PLANNED", "cert planned"),
        ("type not stated", "type?"), ("custom / RFQ", "spec"), ("62477-1 level decides", "AC rule"),
        ("PD performance not stated", "PD?"), ("creepage not stated", "creepage?"), ("no impulse", "impulse?"),
        ("AC withstand not stated", "AC?"), ("stated for <=", "maker alt"), ("60664-4", "HF"),
        ("double insulation", "double"), ("UCPV", "Uc<trip"), ("remote", "unmonitored"), ("functional short", "func test"),
        ("production hipot", "hipot"), ("datasheet inconsistency", "ds incons."), ("needs the SPD-credited", "SPD"),
        ("altitude rating not on file", "SPD alt?"), ("PD3 zone", "PD3")]


def tag(verdict, notes):
    if verdict == "FAIL":
        return "**FAIL**: " + notes[0][:90]
    t = []
    for k, v in TAGS:
        if any(k in n for n in notes) and v not in t:
            t.append(v)
    return ("COND: " + ", ".join(t)) if verdict != "PASS" else "PASS"


def rating_text(rt):
    if rt.get("spd"):
        s = SPDS[rt["mpn"]]
        return "Up %.1f kV pe / %.1f kV pp, In %.1f kA, Uc %.0f V" % (s["up_pe"] / 1e3, s["up_pp"] / 1e3, s["In"] / 1e3,
                                                                     s["ucpv"])
    if "umax" in rt:
        return "Umax %.0f V, pulse %.0f V, gap %.1f mm" % (rt["umax"], rt["upulse"], rt["gap"])
    if rt.get("status") == "none":
        return "not specified"
    f = [("ins", "%s", lambda x: {"R": "reinforced", "B": "basic", "F": "functional"}.get(x, "type n/s")),
         ("viowm", "VIOWM %.0f V", None), ("viorm", "VIORM %.0f Vpk", None), ("vimp", "VIMP %.0f V", None),
         ("viosm", "VIOSM %.0f V", None), ("uimp", "Uimp %.0f V", None), ("viso", "AC %.0f Vrms", None),
         ("pd_test", "PD test %.0f Vpk", None), ("clr", "CLR %.1f mm", None), ("cpg", "CPG %.1f mm", None),
         ("cti", "CTI %.0f", None)]
    out = []
    for k, fmt, conv in f:
        if k in rt and rt[k] is not None:
            out.append(fmt % (conv(rt[k]) if conv else rt[k]))
    return ("spec: " if rt.get("status") == "spec" else "") + ", ".join(out)


def mm(x):
    return "%.1f" % x if x >= 0.1 else "%.2f" % x


def md(headers, rows):
    s = "| " + " | ".join(headers) + " |\n|" + "---|" * len(headers) + "\n"
    return s + "".join("| " + " | ".join(str(c) for c in r) + " |\n" for r in rows)


PROBLEMS = []


def write_outputs(boards, audit, problems, fnd, V):
    PROBLEMS[:] = problems
    os.makedirs(OUT, exist_ok=True)
    # ---- barrier_audit.csv
    with open(os.path.join(OUT, "barrier_audit.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["board", "refs", "part", "barrier", "circuit_a", "circuit_b", "required", "node", "U_work_V",
                    "U_recurring_peak_V", "U_transient_V", "f_kHz", "U_imp_req_V", "U_imp_req_with_SPD_V", "SPD",
                    "clearance_req_2000_mm", "clearance_req_3000_mm", "clearance_req_4000_mm", "creepage_req_PD2_mm",
                    "creepage_req_PD1_mm", "AC_test_req_Vrms", "PD_test_req_Vpk", "rated",
                    "verdict_2000m", "verdict_3000m", "verdict_4000m", "notes_2000m", "notes_3000m", "notes_4000m",
                    "evidence"])
        for r in audit:
            q, kind = r["rq"], r["kind"]
            grp = mat_group(r["rt"].get("cti") or PCB_CTI)
            w.writerow([r["board"], " ".join(r["refs"]), r["mpn"], r["what"], r["sa"], r["sb"], NAMES[kind], r["node"],
                        "%.0f" % q["u_w"], "%.0f" % q["u_rp"], "%.0f" % q["u_tov"], "%.0f" % (q["f"] / 1e3),
                        "%.0f" % q["imp_nospd"], "%.0f" % q["imp_spd"], q["spd"]]
                       + ["%.1f" % req_clearance(q, a, False, r["zone"]) for a in ALTS]
                       + ["%.1f" % req_creepage(q, r["zone"], grp), "%.1f" % req_creepage(q, 1, grp),
                          "%.0f" % q["ac"], "%.0f" % q["u_pd_test"] if q["pd_needed"] else "",
                          rating_text(r["rt"])] + [r["v"][a][0] for a in ALTS] + ["; ".join(r["v"][a][1]) for a in ALTS]
                       + [r["ev"]])
    # ---- review_insulation.csv
    with open(os.path.join(ROOT, "gen", "data", "review_insulation.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "severity", "where", "finding", "evidence", "recommendation"])
        for x in fnd:
            w.writerow([x["id"], x["severity"], x["where"], x["finding"], x["evidence"], x["recommendation"]])
    # ---- insulation_spec.json
    counts = {a: {v: sum(1 for r in audit if r["v"][a][0] == v) for v in ("PASS", "PASS WITH CONDITION", "FAIL")}
              for a in ALTS}
    spec = dict(generated_by="sim/insulation.py", note=STD_NOTE + " Calculated, not measured.",
                assumptions=dict(earthing=EARTHING, ovc=OVC, system_voltage_V=SYSTEM_V, pd_inside=PD_INSIDE,
                                 pd_outside=PD_OUTSIDE, pcb_cti=PCB_CTI, altitudes_m=list(ALTS),
                                 spd_lead_m=SPD_LEAD_M, string_share=STRING_SHARE, interpolate=INTERPOLATE),
                systems={k: {kk: vv for kk, vv in v.items()} for k, v in SYSTEMS.items()},
                spds={k: dict(v, up_eff_pe=up_eff(k), up_eff_pp=up_eff(k, "pp")) for k, v in SPDS.items()},
                spd_max_up_eff_for_8mm={a: max_up_for(a) for a in ALTS},
                requirements=[req_summary(*c) for c in REQ_CASES], layout_rules=layout_rules(audit),
                verdict_counts=counts,
                audit=[dict(board=r["board"], refs=r["refs"], part=r["mpn"], barrier=r["what"], between=[r["sa"], r["sb"]],
                            required=NAMES[r["kind"]], verdicts={a: r["v"][a][0] for a in ALTS},
                            notes={a: r["v"][a][1] for a in ALTS}, evidence=r["ev"]) for r in audit],
                findings=[{k: v for k, v in x.items() if k != "covers"} for x in fnd], problems=problems)
    with open(os.path.join(OUT, "insulation_spec.json"), "w") as fh:
        json.dump(spec, fh, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    with open(os.path.join(OUT, "report.md"), "w") as fh:
        fh.write(report_md(boards, audit, fnd, V, counts))
    return counts


def report_md(boards, audit, fnd, V, counts):
    L = []
    a = L.append
    fail = {x: [r for r in audit if r["v"][x][0] == "FAIL"] for x in ALTS}
    alt_only = {x: [r for r in fail[x] if r["v"][2000][0] != "FAIL"] for x in (3000, 4000)}
    nparts = sum(len(r["refs"]) for r in audit)
    a("# Insulation coordination - DC-DC platform (ECO-10)\n")
    a("Generated by `sim/insulation.py` from the netlists on disk at run time (%s) and the designers' spec files. "
      "**Calculated, not measured; nothing here is bench-validated.** %s\n" % (", ".join(sorted(boards)), STD_NOTE))
    a("## Summary\n")
    a("- Audit: %d rows covering %d barrier parts on %d boards (every Domain = ISOLATOR / CROSSING part, the series "
      "strings they belong to, and the barriers that are not single parts). Verdicts:\n\n" % (len(audit), nparts, len(boards)))
    a(md(["altitude", "PASS", "PASS WITH CONDITION", "FAIL"],
         [["%d m" % x, counts[x]["PASS"], counts[x]["PASS WITH CONDITION"], counts[x]["FAIL"]] for x in ALTS]) + "\n")
    a("- **Altitude that can be claimed today: %s.** %d rows fail already at 2000 m (not altitude-related, listed "
      "below); once they are fixed and the conditions are met (conformal coating to PD1 on every HV-PELV isolator "
      "footprint, monitored SPDs for the parts that pass the 8 kV impulse only with the SPD credit, certificates and "
      "custom-part specs carrying the levels) the platform is coordinated for **2000 m**. 3000 m and 4000 m are blocked by %d / %d further rows whose failure is clearance or "
      "impulse at altitude: every 8-8.5 mm isolator package needs %.1f / %.1f mm at 3000 / 4000 m. The drawn SPDs do "
      "not change that (Up,eff %.2f kV -> reinforced %.2f kV = the 8 kV row). An SPD with an effective pole-PE level "
      "<= %.1f kV incl. leads, monitored and rated for the altitude, would bring reinforced down to 6 kV (%.1f / %.1f / "
      "%.1f mm) and open 4000 m for the 8 mm packages (see IC-01).\n"
      % ("none" if fail[2000] else "2000 m", len(fail[2000]), len(alt_only[3000]), len(alt_only[4000]),
         V["clR"][3000], V["clR"][4000], V["upeff_952515"] / 1e3, V["impR_spd"] / 1e3, V["up_max_4000"] / 1e3,
         V["clR_good"][2000], V["clR_good"][3000], V["clR_good"][4000]))
    a("- FAIL at 2000 m (all altitudes):\n")
    for r in fail[2000]:
        a("  - %s %s (%s, %s-%s %s): %s\n" % (r["board"], " ".join(r["refs"]), r["mpn"], r["sa"], r["sb"],
                                           NAMES[r["kind"]], r["v"][2000][1][0]))
    a("- Additional FAIL at 3000 / 4000 m: %s.\n" % "; ".join(sorted({"%s %s" % (r["mpn"], r["board"]) for r in
                                                                       alt_only[4000]})))
    a("- Findings for the board designers: `gen/data/review_insulation.csv` (%d rows: %s).\n\n" % (
        len(fnd), ", ".join("%d %s" % (sum(1 for x in fnd if x["severity"] == s), s) for s in ("critical", "major", "minor"))))
    # ---- 1. assumptions
    a("## 1. Frozen assumptions\n")
    for e in EARTHING:
        a("- %s\n" % e)
    a("- Overvoltage category: **DC OVC II** (ECO-10) for every HV port; rated system voltage **%.0f V** (pole-to-PE with "
      "a first fault) -> basic impulse %.0f V (IEC 60664-1 Table F.1). Temporary over-voltage: no mains TOV (DC "
      "circuits not connected to the mains); the circuits' own maxima are the hardware OV trips and load-rejection "
      "peaks (PV %.0f V, DAB port 1 %.0f V, port 2 %.0f V).\n" % (SYSTEM_V, TABLE_F1[1000][1], PV_TRIP, D1_LR, D2_LR))
    a("- Pollution degree: **PD2 inside, PD3 outside** (ECO-10). Boards and parts in the forced-air cooling path of the "
      "IP20 PV module are treated as PD3 unless they sit in a PD2 compartment or are coated to PD1 (IEC 60664-3 Type 1). "
      "PCB laminate: standard FR-4, CTI >= %d (group IIIa) unless a CTI >= 600 laminate is specified.\n" % PCB_CTI)
    a("- Altitude classes evaluated: 2000 / 3000 / 4000 m (IEC 60664-1 Table A.2 factors 1.00 / 1.14 / 1.29). PV-C6 "
      "asks for '> 3000 m derating' and the thermal design claims no derating to 4000 m (D-029): insulation cannot be "
      "derated, so the clearances must be designed for the highest altitude claimed.\n")
    a("- Port SPDs drawn: %s. Effective protection level = Up + %.1f uH/m x %.1f m x In/8 us (lead drop, ASSUMPTION).\n"
      % ("; ".join("%s (%s): Up,eff %.2f kV pole-PE / %.2f kV pole-pole%s" % (m, SPDS[m]["name"], up_eff(m) / 1e3,
                                                                               up_eff(m, "pp") / 1e3,
                                                                               ", rated to %d m" % SPDS[m]["alt_max"] if SPDS[m].get("alt_max") else "")
                   for m in V["drawn"]), SPD_L_PER_M * 1e6, SPD_LEAD_M))
    a("- Table look-ups take the next higher row (no interpolation) - conservative; string elements carry %.1f x their "
      "equal share (tolerance, capacitive grading, leakage).\n\n" % STRING_SHARE)
    # ---- 2. circuits
    a("## 2. Circuits and working voltages\n")
    rows = []
    for k, (s, node, name) in CIRCUITS.items():
        S = SYSTEMS[s]
        if s in HV_SYSTEMS:
            rows.append([k, name, "C", "%.0f" % S["v"], "%.0f" % (S["rp_sw"] if node == "sw" else S["v"]),
                         "%.0f" % (S["rp_sw_trip"] if node == "sw" else S["trip"]),
                         "%.0f" % (S["f"] / 1e3) if node == "sw" else "DC"])
        else:
            rows.append([k, name, dvc(S["v"]), "%.1f" % S["v"], "-", "-", "DC"])
    a("\n" + md(["circuit", "what", "DVC", "working V to PE (worst earthing)", "recurring peak V", "transient peak V", "f kHz"], rows))
    a("\nSources: PV `cell_spec.json` (1000 V continuous, 1100 V trip, device peak %.0f V at the trip corner -> %.0f V at "
      "1000 V with the ringing scaled to the blocking voltage, 32 kHz, 71-92 V/ns); DAB `dab_spec.json` (port 1 %.0f V, "
      "port 2 %.0f V, trips %.0f / %.0f V, load-rejection %.0f / %.0f V, module peak %.0f V, 100 kHz, ~57 V/ns); AUX-HV "
      "`aux_hv_spec.json` (V_DS %.0f V continuous / %.0f V transient, %.0f kHz). The gate-drive islands and their bias "
      "modules see the **switch node**, not the DC rail: high-side islands swing between the poles with the overshoot, "
      "and with a pole at PE the whole swing appears across the barrier. DAB port 1 - port 2: %.0f V DC (V1 + V2, first "
      "faults at opposite poles) + one overshoot = %.0f V recurring at 100 kHz.\n\n"
      % (PV_RP_TRIP, PV_RP, D1, D2, D1_TRIP, D2_TRIP, D1_LR, D2_LR, D_RP1, AUX_RP, AUX_RP_TRIP, AUX_F / 1e3, D1 + D2,
         D1 + D2 + D_OVS))
    # ---- 3. requirements
    a("## 3. Requirements per insulation type\n")
    a("Types (EN 62477-1): DVC C circuit to PELV / field I/O = **protective separation = reinforced** (or double); DVC C "
      "to PE-bonded accessible metal = **basic** + protective bonding; DAB port 1 to port 2 = **basic** (simple "
      "separation - the dab_spec 'pending' class is resolved); inside one HV circuit and between DVC A circuits = "
      "**functional** (may be reduced below the tables only with the 62477-1 functional short-circuit test).\n\n")
    R = [req_summary(*c) for c in REQ_CASES]
    a(md(["case", "U_w V", "U_rp V", "impulse V (no SPD / drawn SPD / Up,eff<=4 kV)", "AC test V rms (DC)",
          "PD test Vpk (<=10 pC)", "clearance mm 2000/3000/4000 (no SPD)", "with Up,eff<=4 kV", "creepage PCB PD2 / PD1 / PD3 (I / IIIa)"],
         [[x["label"], "%.0f" % x["u_w"], "%.0f" % x["u_rp"],
           "%.0f / %.0f / %.0f" % (x["imp"], x["imp_spd"], x["imp_good"]) if x["imp"] else "- (behind the DC link)",
           ("%.0f (%.0f)" % (x["ac"], x["ac_dc"])) if x["ac"] else "-", "%.0f" % x["pd_test"] if x["pd_test"] else "-",
           " / ".join("%.1f" % x["cl_%d" % y] for y in ALTS), " / ".join("%.1f" % x["cl_good_%d" % y] for y in ALTS),
           " / ".join(mm(x[k]) for k in ("cr_pd2", "cr_pd1", "cr_pd3_I", "cr_pd3_III"))] for x in R]))
    a("\nRules behind the table: impulse - basic from Table F.1 (1000 V row, OVC II), reinforced one preferred step "
      "higher, or 160 %% of a non-preferred basic value (60664-1 5.1.6); clearance - Table F.2 case A x altitude "
      "factor, never below the steady-state / recurring-peak value of Table F.7 (x1.6 reinforced, x%.2f above 30 kHz "
      "per IEC 60664-4); creepage - Table F.4 at the working voltage, x2 for reinforced, printed-wiring columns only up "
      "to 1000 V, and **never less than the clearance** at the same altitude; AC test - %.0f V + U_sys basic, x2 "
      "reinforced (TOV basis; the lenient 2U + 1000 V rule gives %.0f / %.0f V rms and is only used to grade a "
      "shortfall), floor 2 U_w + 1000 V DC for working voltages above U_sys; PD - required on solid insulation that "
      "carries a recurring switching peak above %.0f V: extinction >= 1.2 x U_rp (basic) / 1.5 x U_rp (reinforced), "
      "test at 1.5 / 1.875 x U_rp with <= 10 pC (ICs: VIORM >= U_rp, their routine test is 1.875 x VIORM).\n\n"
      % (HF_CLEAR_FACTOR, 1200, u_ac_test("B", SYSTEM_V, SYSTEM_V)[1], u_ac_test("R", SYSTEM_V, SYSTEM_V)[1],
         PD_THRESHOLD_V))
    a("IEC 62109-1 (PV port, PV-P75/100/110): PV circuits are OVC II by default and the system voltage is the array "
      "open-circuit voltage at the lowest ambient (-30 C, PV-20) - it must stay <= %.0f V (PV-03) for the 1000 V row to "
      "hold; PV circuits to accessible DVC A circuits need reinforced or double insulation (same as above); a "
      "non-isolated converter needs the array insulation-resistance check before start and residual-current "
      "monitoring (IEC 62109-2) - PV-C5's insulation measurement serves the first, the second is not in the drawn "
      "design (open point). Values from memory - verify.\n\n" % SYSTEM_V)
    a("### 3.1 When the port SPD may reduce the impulse level\n\n")
    a("The SPD's protection level may replace the OVC II value (6 kV basic / 8 kV reinforced) for insulation inside "
      "the module only if ALL of these hold - otherwise use the 'no SPD' column:\n"
      "1. The SPD is part of the equipment (fitted at the port terminals inside the module, not the installer's).\n"
      "2. Its effective level (Up at In plus the lead drop, pole-PE for barriers to PE/PELV, pole-pole for functional "
      "insulation between poles) is below the reduced impulse value - preferably a preferred-series value (4.0 kV), "
      "because a non-preferred basic value raises reinforced to 160 % of it.\n"
      "3. It is rated for the port: IEC 61643-31 (PV) / -41 (DC/battery), UCPV / Uc >= the port maximum (including the "
      "OV-trip excursion), short-circuit rating for the source, altitude rating >= the claimed altitude.\n"
      "4. Its end-of-life disconnection is monitored (remote contact wired to a DI) and the converter stops when it "
      "operates - otherwise the reduced insulation is unprotected after the first SPD failure.\n"
      "5. Conservative reading kept here: for **reinforced** insulation the credit is shown as a CONDITION and must be "
      "confirmed against the purchased EN 62477-1 and with the certifier.\n\n")
    a("Drawn today: the SPD credit reduces basic HV-PE from %.0f V to %.0f V (clearance %.1f -> %.1f mm at 2000 m) but "
      "leaves reinforced at the 8 kV row (%.0f V). Largest effective SPD level that keeps reinforced clearance <= 8.0 mm: "
      "%s.\n\n" % (TABLE_F1[1000][1], min(6000, V["upeff_952515"]), V["clB"][2000],
                   lookup(TABLE_F2, V["upeff_952515"] / 1e3, 2), V["impR_spd"],
                   ", ".join("%.1f kV at %d m" % (max_up_for(x) / 1e3, x) for x in ALTS)))
    a("### 3.2 Altitude\n")
    a("Clearances scale with Table A.2 (x1.14 at 3000 m, x1.29 at 4000 m); creepage and solid insulation do not, but "
      "creepage may not be smaller than the clearance. External PD on film edges (heatsink pads, bobbins) incepts lower "
      "at low pressure: run PD type tests on samples at the reduced pressure of the claimed altitude or add the "
      "altitude factor to the PD test level. Impulse type tests at sea level for clearances dimensioned for altitude "
      "H (computed from Table F.2 read backwards with the 0 m factor; IEC 60664-1 Table F.5 governs): reinforced 8 kV "
      "-> %s; basic 6 kV -> %s.\n\n" % (", ".join("%.1f kV (%d m)" % (test_impulse_sea_level(8000, x) / 1e3, x) for x in ALTS),
                                      ", ".join("%.1f kV (%d m)" % (test_impulse_sea_level(6000, x) / 1e3, x) for x in ALTS)))
    a("### 3.3 High-frequency stress (IEC 60664-4)\n")
    a("The switching fundamentals (PV 32 kHz, AUX-HV 65 kHz, DAB 100 kHz) are above 30 kHz, so IEC 60664-4 applies to "
      "every barrier that sees a switch node: clearances for the recurring peak are taken x%.2f (inhomogeneous-field "
      "breakdown can fall to ~80 %%); solid insulation must be PD-free at the operating recurring peak (PD repetition "
      "scales with frequency, so any PD is life-limiting) and its dielectric heating checked for the custom magnetics. "
      "The 60664-4 creepage table for > 30 kHz could not be reproduced reliably from memory; at <= 100 kHz and <= 2.1 "
      "kVpk it is believed to be below the Table F.4 PD2 values used here - **verify**.\n\n" % HF_CLEAR_FACTOR)
    # ---- 4. audit
    a("## 4. Barrier audit\n")
    a("Every row is computed (`judge()` / `judge_string()`); full notes, ratings and evidence per altitude are in "
      "`barrier_audit.csv`. Tags: coat = needs PD1 coating, SPD = passes only with the SPD credit, spec = custom/RFQ "
      "text must carry the levels, type? = maker states no insulation type, AC rule = between the two AC-test rules, "
      "PD? / creepage? / impulse? / AC? = value not stated, cert planned = certification not yet granted.\n\n")
    a(md(["board", "refs", "part / barrier", "between", "required", "U_w / U_rp V", "rated (datasheet)", "2000 m", "3000 m", "4000 m"],
         [[r["board"], " ".join(r["refs"]) if len(r["refs"]) <= 6 else " ".join(r["refs"][:5]) + " ... (%d)" % len(r["refs"]),
           r["mpn"] + ("" if r["src"] == "netlist" else " - " + r["what"][:60]), "%s-%s" % (r["sa"], r["sb"]),
           NAMES[r["kind"]] + (" (element)" if r["string"] else ""), "%.0f / %.0f" % (r["rq"]["u_w"], r["rq"]["u_rp"]),
           rating_text(r["rt"])[:90]] + [tag(*r["v"][x]) for x in ALTS] for r in audit]))
    # ---- 5. layout rules
    a("\n## 5. Rules for the PCB layout phase\n")
    a("### 5.1 General\n")
    a("- Measure creepage along every surface (PCB, package, potting edge) and clearance through air between the "
      "nearest conductive points of the two circuits, pads and vias included. Creepage >= clearance always.\n"
      "- **Conformal coating to PD1 (IEC 60664-3 Type 1) is required** over every HV-PELV isolator footprint and its "
      "HV-side copper on PVCELL-25, GDRV-HB, PV-PORT(-180), DAB60 and AUX-HV: the 8-8.5 mm packages are below the %.1f "
      "mm reinforced PD2 creepage. Coating does not reduce clearance. Type 2 protection (coating as solid insulation) "
      "removes the spacing requirement under it only after the 60664-3 type tests at the full impulse and PD levels.\n"
      "- Slots: a slot counts only if wider than X = 1.0 mm (PD2) / 1.5 mm (PD3) / 0.25 mm (PD1); a slot under an "
      "isolator body increases the PCB creepage only, never the package's own creepage.\n"
      "- Boards in the cooling-air path are PD3 unless coated: PD3 values below are not practical for reinforced "
      "spacing on a PCB.\n"
      "- PELV wiring (ribbon cables, NTC leads, coil and FB wires) inside the module must be segregated from HV "
      "busbars and HV wiring by reinforced spacing or double insulation (sleeving).\n"
      "- Heatsinks, cold plates, transformer cores and housings of chassis resistors are PE: basic spacing from every "
      "HV conductor, reinforced from PELV.\n\n" % V["crR2"])
    a("### 5.2 Per board (minimum values; 'SPD' = with the drawn SPD credit; PCB creepage FR-4 CTI >= 175)\n")
    LR = layout_rules(audit)
    for b in sorted(LR):
        a("**%s**\n\n" % b)
        a(md(["between", "type", "where", "U_w / U_rp V", "clearance mm 2000/3000/4000", "with SPD credit",
              "creepage mm PD2 / PD1 coated / PD3", "AC test V rms", "impulse V", "PD test Vpk"],
             [["%s-%s" % (x["sa"], x["sb"]), NAMES[x["kind"]], x["where"], "%.0f / %.0f" % (x["u_w"], x["u_rp"]),
               " / ".join("%.1f" % x["cl"][y] for y in ALTS), " / ".join("%.1f" % x["cl_spd"][y] for y in ALTS),
               " / ".join(mm(x[k]) for k in ("cr2", "cr1", "cr3")), "%.0f" % x["ac"] if x["ac"] else "-",
               "%.0f" % x["imp"] if x["imp"] else "-", "%.0f" % x["pd"] if x["pd"] else "-"] for x in LR[b]]))
        for label, keys in (("Coat to PD1 (IEC 60664-3 Type 1) over these footprints and their HV copper",
                             ("coat to PD1", "PD1 coating")),
                            ("Below the PD1 values: Type 2 coating, or a milled slot plus the 62477-1 functional "
                             "short-circuit test", ("Type 2 coating",))):
            refs = sorted({ref for r in audit if r["board"] == b for ref in r["refs"]
                           if any(k in n for n in r["v"][2000][1] for k in keys)})
            if refs:
                a("\n%s: %s.\n" % (label, " ".join(refs)))
        a("\n")
    for b in sorted(set(boards) - set(LR)):
        a("**%s**: PELV only (no barrier parts) - functional spacing only; no HV conductor may be routed on or next to "
          "it, and its cables must stay segregated from HV wiring.\n\n" % b)
    a("### 5.3 Test levels per barrier type (type test 60 s, routine 1 s)\n")
    a("\n" + md(["barrier", "AC V rms (DC V)", "impulse 1.2/50 V", "PD test Vpk (<= 10 pC)"],
         [[x["label"], ("%.0f (%.0f)" % (x["ac"], x["ac_dc"])) if x["ac"] else "-", "%.0f" % x["imp"] if x["imp"] else "-",
           "%.0f" % x["pd_test"] if x["pd_test"] else "-"] for x in R]))
    a("\nModule hipot: unplug the SPDs, open the IMD and Y-capacitor paths (or test DC below their ratings) - see IC-%s.\n\n"
      % next(x["id"][3:] for x in fnd if x["where"].startswith("production test")))
    # ---- 6. open points
    a("## 6. Open points\n")
    a("- **Altitude:** today none can be claimed (FAILs at 2000 m, sec. Summary); 2000 m after those are fixed; 3000 / "
      "4000 m need the owner decision of IC-01 (SPD credit with a monitored SPD of Up,eff <= %.1f kV rated for the "
      "altitude, or >= %.1f mm isolators / Type 2 encapsulation). PV-C6 is a candidate requirement (D-009 open).\n"
      % (V["up_max_4000"] / 1e3, V["clR"][4000]))
    a("- **Standards:** every table here is from memory - verify Table F.1/F.2/F.4/F.7/A.2 values, the 160 %% rule, "
      "the interpolation rule, the 62477-1 AC test level for DC circuits not connected to the mains (TOV rule %.0f V "
      "rms vs 2U+1000 rule %.0f V rms reinforced), the PD threshold (700 vs 750 V), the conditions for the SPD credit "
      "on reinforced insulation, IEC 62109-1 PV-specific clauses and the IEC 60664-4 HF creepage table.\n"
      % (u_ac_test("R", SYSTEM_V, SYSTEM_V)[0], u_ac_test("R", SYSTEM_V, SYSTEM_V)[1]))
    a("- **Earthing / PCS:** DAB port 1 assumed not mains-connected; if the PCS is transformerless, port 1 inherits "
      "the grid TOV (U0 + 1200 V) and mains OVC - re-run. Battery and PV earthing practice of the customer (one pole "
      "earthed vs IT with IMD) decides whether the 1850 V DAB inter-port basis can be reduced.\n")
    a("- **Unverified data:** CTI of the PCB laminate and of custom bobbins; package creepage of MSC035SMA170B4 and "
      "the US1M/2512 land patterns (estimates); DEHN 952515 datasheet (product page only, altitude unknown); TDK and "
      "Omron insulation types; PD behaviour of every custom magnetic part; SPD lead lengths (0.5 m assumed); the "
      "switching overshoots (simulated, layout-dependent: re-run with the extracted loop inductance).\n")
    a("- **Pending decisions that change this coordination:** D-017 (one or two DAB modules per bridge: same "
      "barriers, more of them), D-020 (PV-P100/110 180 A port: PV-PORT-180 audited here with the same verdicts), "
      "D-022 (AUX-HV power: a larger flyback changes T1), D-016 (UCC14241-Q1 certification).\n")
    a("- **Problems seen while running:** %s.\n\n" % ("; ".join(PROBLEMS) or "none"))
    a("## Appendix A - standard tables as used (transcribed from memory - verify)\n")
    a("Cross-checks against manufacturer documents on file (they confirm, they do not replace the standard): "
      "TI TIDUF55 (TIDA-010253 design guide) p5 Table 2-1 'IEC 60664-1-2020 1500-V BESS insulation requirements': "
      "clearance basic 7.1 mm / reinforced 10.4 mm at <= 4000 m and 8.14 / 11.84 mm at 5000 m, creepage 8 / 16 mm - "
      "the same as Table F.2 6 kV -> 5.5 mm and 8 kV -> 8.0 mm x Table A.2 1.29 / 1.48, reinforced one step up, and "
      "Table F.4 PD2 group I 8.0 mm at 1600 V x2 (asserted in self_check_tables()). The 'overvoltage category per IEC "
      "60664-1' rows of the TI isolator datasheets match Table F.1 at the part's VIMP / VIOSM: UCC21710 p6 and ISO7721 "
      "p10 (8 kV): <= 600 V rms I-IV, <= 1000 V rms I-III (F.1: 600 V OVC IV = 8 kV, 1000 V OVC III = 8 kV); AMC3330 "
      "p6 (7.7 kV): <= 1000 V rms I-II (1000 V OVC II = 6 kV); ISO1211 p7 DBQ (4 kV): <= 150 V rms I-IV, <= 300 V rms "
      "I-III (150 V OVC IV = 300 V OVC III = 4 kV). The VDE 0884-17 PD factors in the same tables (1.2 / 1.6 / 1.875 x "
      "VIORM) follow the F1 / F2 / F3 structure used here. TDK HVC43MC p3 rates contact-to-coil at 4400 V AC = 2 x "
      "(1000 V + 1200 V), the TOV-basis reinforced test level used here.\n\n")
    a("Table F.1 rated impulse (V), OVC I-IV: " + "; ".join("%d V: %s" % (k, "/".join(str(x) for x in v))
                                                           for k, v in TABLE_F1.items()) + ".\n\n")
    a("Table F.2 case A clearance (mm) PD1/PD2/PD3 at kV: " + "; ".join("%g: %g/%g/%g" % r for r in TABLE_F2) + ".\n\n")
    a("Table F.7a case A clearance (mm) at kV peak: " + "; ".join("%g: %g" % r for r in TABLE_F7) + ".\n\n")
    a("Table F.4 creepage (mm) at V: PWB PD1, PWB PD2, PD1, PD2 I/II/III, PD3 I/II/III: " + "; ".join(
        "%g: %s" % (r[0], "/".join("-" if x is None else "%g" % x for x in r[1:])) for r in TABLE_F4) + ".\n\n")
    a("Table A.2 altitude factors: " + ", ".join("%d m %.3f" % kv for kv in ALT_FACTOR.items()) + ".\n")
    return "".join(L)


# =====================================================================================================================
# 10. MAIN + SELF-CHECKS
# =====================================================================================================================
def self_check_tables():
    assert lookup(TABLE_F2, 6.0, 2) == 5.5 and lookup(TABLE_F2, 8.0, 2) == 8.0 and lookup(TABLE_F2, 4.781, 2) == 4.0
    assert imp_required("R", 6000) == 8000 and imp_required("R", 4000) == 6000 and abs(imp_required("R", 4500) - 7200) < 1
    assert clearance(8000, 2, 4000) == 10.4 and clearance(6000, 2, 3000) == 6.3 and clearance(8000, 2, 2000) == 8.0
    assert creepage(1000, 2, "III", True) == 5.0 and creepage(1000, 2, "I") == 5.0 and creepage(1000, 2, "III", kind="R") == 20.0
    assert creepage(1850, 2, "I") == 10.0 and creepage(1000, 1, "I", True, "R") == 6.4 and creepage(1000, 3, "III") == 16.0
    assert u_ac_test("R", 1000, 1000)[0] == 4400 and u_ac_test("B", 1000, 1850)[0] > 3300
    assert ALT_FACTOR[3000] == 1.14 and ALT_FACTOR[4000] == 1.29
    # TI TIDUF55 p5 Table 2-1 (60664-1:2020, 1500 V BESS): 7.1 / 10.4 mm at 4000 m, 8.14 / 11.84 mm at 5000 m, 8 / 16 mm
    assert clearance(6000, 2, 4000) == 7.1 and clearance(8000, 2, 4000) == 10.4 and creepage(1600, 2, "I") == 8.0
    assert abs(lookup(TABLE_F2, 6.0, 2) * ALT_FACTOR[5000] - 8.14) < 0.01 and abs(8.0 * ALT_FACTOR[5000] - 11.84) < 0.01
    assert abs(PV_RP - 1273) < 5 and PV_TRIP == 1100 and D1 == 950 and D2 == 900, "spec files changed - review"


def main():
    self_check_tables()
    problems = []
    boards = scan_boards(problems)
    audit = build_audit(boards, problems)
    V = key_values()
    fnd = findings(V)
    counts = write_outputs(boards, audit, problems, fnd, V)
    for x in ALTS:
        print("%d m: %s" % (x, counts[x]))
    print("wrote %s/{report.md, insulation_spec.json, barrier_audit.csv} and gen/data/review_insulation.csv (%d findings)"
          % (os.path.relpath(OUT, ROOT), len(fnd)))
    # ---- self-checks (exit code != 0 on failure)
    for p in problems:
        print("PROBLEM:", p)
    assert not problems, "%d problem(s): unknown barrier part / domain / board, or a netlist could not be read" % len(problems)
    for r in audit:                      # reinforced PASS only with datasheet evidence of a reinforced rating
        for x in ALTS:
            if r["kind"] == "R" and r["v"][x][0] == "PASS":
                assert r["rt"].get("ins") == "R" and r["rt"].get("status", "rated") == "rated" and \
                    DS in r["ev"], "reinforced PASS without datasheet evidence: %s %s" % (r["board"], r["refs"])
    covered = set().union(*(x["covers"] for x in fnd))
    unreported = sorted({"%s %s" % (r["board"], r["mpn"]) for r in audit for x in ALTS
                         if r["v"][x][0] == "FAIL" and r["mpn"] not in covered})
    assert not unreported, "FAIL rows not reported in review_insulation.csv: %s" % unreported
    assert all(r["v"][x][0] in ("PASS", "PASS WITH CONDITION", "FAIL") for r in audit for x in ALTS)
    print("self-checks passed")


if __name__ == "__main__":
    main()
