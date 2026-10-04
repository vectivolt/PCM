"""PCS-P125 topology cross-check against Wolfspeed CRD-25BDA6512N-K (REQUIREMENTS AC-01..03, SRC-4..7, REF-1/2, MAG-2).

Run:  .venv/bin/python sim/pcs_crosscheck.py
Out:  sim/out/pcs_design/crosscheck_wolfspeed.md (the only file this script writes)

Tests the six claims behind decision D-053 (two-level 1700 V SiC chosen over a three-level T-type) independently of the
study's own reasoning.  It REUSES, read-only: pcs_design.leg_losses / op_point / references / zs_policy / filter_loss_est
(the study's averaged loss model), pv_devices (SiC data and E / R_DS(on) primitives), pcs_devices (prices, die areas,
cosmic-ray data), and the study's outputs pcs_spec.json and pcs_costed_bom.csv.  None of those files is changed: the
Wolfspeed middle switch C3M0025065K (not in pv_devices) is added to the in-memory device table only, and the two
sensitivity runs that change a model constant restore it afterwards.

Labels in the report: MEASURED-BY-WOLFSPEED, CALCULATED-BY-WOLFSPEED (Wolfspeed's own simulation or calculation),
DATASHEET, CALCULATED (ours, from stated inputs), ESTIMATE (ours, rests on a named assumption).  Nothing of ours is
bench-validated.  Literature quoted from memory is marked "(from memory)".
"""
import csv
import json
import math
import os
import sys

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pcs_design as pd  # noqa: E402  (importing does not run the study: run() is guarded by __main__)
import pcs_devices as dv  # noqa: E402
import pv_devices as pv  # noqa: E402

OUT = os.path.join(HERE, "out", "pcs_design", "crosscheck_wolfspeed.md")
SPEC = json.load(open(os.path.join(HERE, "out", "pcs_design", "pcs_spec.json")))
BOM_A = list(csv.DictReader(open(os.path.join(HERE, "out", "pcs_design", "pcs_costed_bom.csv"))))
WS = "docs/reference-designs/wolfspeed/crd-25bda6512n-k/"
UG = WS + "crd-25bda6512n-k_user-guide_prd-08613.pdf"          # PRD-08613 Rev. 2, Oct 2024 (pages = printed page numbers)
W = 2 * math.pi * pd.F_GRID
SQ2 = math.sqrt(2.0)

# ============================================================================================ Wolfspeed CRD-25BDA6512N-K
# MEASURED-BY-WOLFSPEED, UG Table 6 p42: inverter mode, 60 kHz, three-phase resistive load, Yokogawa WT5000.
# (V_dc, V_LL) -> [(P_in kW, P_out kW, efficiency %)].  At 900 V the input power was limited by the source (footnote).
WS_ETA = {
    (670, 400): [(3.69, 3.66, 99.20), (7.02, 6.97, 99.27), (10.76, 10.68, 99.25), (14.47, 14.34, 99.13), (18.07, 17.89, 98.99),
                 (21.38, 21.13, 98.82), (24.92, 24.56, 98.57)],
    (800, 380): [(3.32, 3.28, 98.71), (6.37, 6.31, 98.98), (9.69, 9.60, 98.98), (13.07, 12.93, 98.90), (16.43, 16.23, 98.79),
                 (19.57, 19.31, 98.65), (22.97, 22.60, 98.47), (26.47, 25.98, 98.35)],
    (800, 400): [(3.70, 3.65, 98.68), (7.09, 7.02, 99.00), (10.74, 10.63, 99.00), (14.56, 14.41, 98.94), (18.20, 17.98, 98.79),
                 (21.45, 21.16, 98.63), (25.27, 24.85, 98.45)],
    (800, 480): [(5.22, 5.18, 99.18), (10.05, 9.98, 99.28), (15.27, 15.16, 99.25), (20.37, 20.20, 99.12), (25.46, 25.19, 98.93)],
    (900, 400): [(3.70, 3.63, 98.31), (7.10, 7.01, 98.75), (10.74, 10.62, 98.85), (14.49, 14.31, 98.77), (17.72, 17.48, 98.64)],
    (900, 480): [(5.21, 5.16, 98.90), (10.04, 9.95, 99.10), (15.22, 15.08, 99.09)],
}
# UG Table 12 p53 (800 V, 400 V, 25 kW, 60 kHz): case temperature MEASURED-BY-WOLFSPEED (K-type thermocouple), loss and
# junction temperature CALCULATED-BY-WOLFSPEED.  UG Table 2 p13: Wolfspeed's loss SIMULATION at 25 kW, 85 C heat sink.
WS_T12 = {"T1": {"loss_W": 28.5, "Tc": 58.2, "Tj": 70.5}, "T2": {"loss_W": 11.0, "Tc": 55.5, "Tj": 60.6}}
WS_T2 = {"outer_sw_W": 75.79, "outer_cond_W": 129.17, "inner_cond_W": 70.1, "total_W": 275.06, "eta_devices": 98.9}
WS_DEV = {"T1": ("C3M0032120K", 1), "T4": ("C3M0032120K", 1), "T2": ("C3M0025065K", 1), "T3": ("C3M0025065K", 1)}
WS_FSW = 60e3                 # UG Table 1 p10
WS_L = 125e-6                 # BOM line 46 L1_1..3 'Line Choke, 125uH, 100kHz, 6.5mOhm DCR' (no maker); one choke per phase in the
WS_DCR = 6.5e-3               #   UG p28 photograph; schematic sheet 2.x draws L5 (131 uH, 7.5 mOhm) in parallel - an alternate footprint
WS_L_CALC = 102e-6            # UG p14: L_INV from the guide's LC-filter procedure (25 kW, 50 Hz, 64 kHz, 230 V)
WS_CF, WS_CF_CALC = 10e-6, 24.9e-6   # BOM line 10 C22 (TDK B32926C3106K000, X2 305 VAC) per phase; UG p14: the procedure gave 24.9 uF
WS_RD = 0.68                  # BOM line 63 R26 (0.68 ohm 7 W wire-wound) in series with C22
WS_CHALF = 4 * 470e-6         # BOM line 1 C1-C8 KEMET ALA8DD471EE500 (470 uF 500 V); UG p25: equally divided DC+/mid and mid/DC-
WS_P = 25e3
WS_I = 36.0                   # UG Table 1 p11, A rms at 800 V / 25 kW
WS_CHOKE_G = 700.0            # UG Fig. 3 p13: 'SiC solution 64 kHz (700 g) x 3 = 2100 g'
# gate network, schematic sheet 2.1 (R39-R45, D3): turn-on 5.1 ohm (R40); turn-off R40 || (R39 + Schottky D3) = 2.55 ohm;
# 1 ohm (R41) in the Kelvin-source return of both; 10 k + 1 nF gate-source at the device.  DATASHEET C3M0032120K Fig. 25 p8
# (800 V, 40 A, 25 C, own body diode as FWD): E_on 500 uJ at 2.5 ohm -> 1460 uJ at 20 ohm, E_off 90 -> 760 uJ (read by eye, linear).
WS_RG_ON, WS_RG_OFF = 5.1 + 1.0, 5.1 / 2 + 1.0
EON_RG_C32 = [(2.5, 500e-6), (20.0, 1460e-6)]
EOFF_RG_C32 = [(2.5, 90e-6), (20.0, 760e-6)]
# other T-type references on file (per-kW DC-link check)
TIDA01606 = {"C_half": 3 * 240e-6, "P": 11e3, "src": "TIDA-01606 BOM item 17 (6 x KEMET ALA8DA241CD500, 240 uF 500 V); "
             "TIDUE53 Rev J p15: '1000-V DC link ... use 1200-V FETs ... middle switches ... 500 V ... 650-V device'"}
CRD200 = {"C_bus": 162.5e-6, "P": 200e3, "I": 167.0, "n_caps": 10, "irms_cap": 37.0,
          "src": "crd200da23n-gma README / PRD-09623 section 3.1: 2 x 5 x KEMET C4AQQEW5650A3BJ (65 uF 1.1 kV)"}

# Wolfspeed's middle switch, DATASHEET C3M0025065K Rev. 03 Sep 2024 (docs/datasheets/power-semiconductors/C3M0025065K.pdf,
# filed 2026-10-05), entered in the pv_devices format and added to the IN-MEMORY table only.
C3M0025065K = {
    "mfr": "Wolfspeed", "src": pv.SEMI + "C3M0025065K.pdf", "rev": "Rev. 03, Sep 2024", "vdss": 650, "package": "TO-247-4",
    "vgs_on": 15.0, "vgs_off": -4.0, "rg_ext": 2.5, "rg_int": 1.3,                    # p2
    "rds25": 25e-3, "rds25_max": 34e-3,                                                # p2: 15 V, 33.5 A; 33 mOhm at 175 C
    "rds_norm": [(25, 1.00), (50, 1.00), (75, 1.03), (100, 1.07), (125, 1.13), (150, 1.21), (175, 1.32)],   # Fig. 4 p4
    "rth_jc": 0.46,                                                                    # p3
    "eoss": [(0, 0.0), (400, 18.9e-6), (600, 37e-6)],     # p2: 37 uJ at 600 V; Co(er) 236 pF over 0-400 V -> 18.9 uJ
    "sw_v": 400.0, "sw_t": 25.0,                          # Fig. 23 p7: 25 C, 400 V, R_G(ext) 2.5 ohm, FWD = own body diode
    "eon": [(10, 63e-6), (20, 88e-6), (30, 110e-6), (40, 135e-6), (50, 161e-6), (60, 190e-6), (65, 204e-6)],
    "eoff": [(10, 25e-6), (20, 25e-6), (30, 41e-6), (40, 67e-6), (50, 107e-6), (60, 157e-6), (65, 180e-6)],
    "kv_on": 1.48, "kv_off": 1.30,      # ASSUMED = pv_devices C3M0032120K (no E-vs-V figure)
    "kt_on": 0.0, "kt_off": 0.0005,     # Fig. 25 p8: E_on flat 25-175 C, E_off 48 -> 52 uJ
    "qrr": 453e-9, "qrr_i": 33.5, "qrr_t": 175.0,        # p3 (the larger of the two printed Q_rr)
    "vsd": 4.5, "vsd_i": 16.8, "tf": 8e-9, "tf_i": 33.5,  # p3 (175 C, V_GS -4 V); p2
}
pv.MOSFETS.setdefault("C3M0025065K", C3M0025065K)


def lin(pts, x):
    return float(np.interp(x, [p[0] for p in pts], [p[1] for p in pts]))


# ============================================================================================ claim 1: midpoint ripple
def np_bounds(m, phi, n=360, nv=201):
    """Range of the local-average midpoint current (per unit of I_pk) that a carrier-based three-level modulator can pick at
    each angle through its zero sequence v0 (every |v_x + v0| <= 1).  i_np = sum (1 - |v_x|) i_x = -sum |v_x| i_x (three-wire).
    Phase references v_x = m sin(th - k 120 deg) in units of V_dc/2, currents i_x = sin(th - k 120 deg - phi)."""
    th = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    k = np.arange(3)[:, None] * 2 * np.pi / 3
    v, i = m * np.sin(th - k), np.sin(th - k - phi)
    lo, hi = -1.0 - v.min(0), 1.0 - v.max(0)
    v0 = lo + (hi - lo) * np.linspace(0.0, 1.0, nv)[:, None]
    inp = -(np.abs(v[None] + v0[:, None, :]) * i[None]).sum(1)
    return th, inp.min(0), inp.max(0), v, i


def np_qpp(m, phi, mode="best", n=360):
    """Peak-to-peak midpoint charge over one fundamental period, in units of I_pk / omega.
    'spwm'  : v0 = 0 (analytic: 0.5 m at PF 0, 0.342 m at PF 1, m <= 1);
    'minmax': the SVPWM-equivalent zero sequence;
    'best'  : the least swing ANY carrier-based zero sequence can reach - a linear programme over the admissible i_np(th),
              i.e. an ideal predictive midpoint controller (a lower bound for every real carrier-based controller)."""
    th, a, b, v, i = np_bounds(m, phi, n)
    dth = th[1] - th[0]
    if mode in ("spwm", "minmax"):
        v0 = 0.0 if mode == "spwm" else -0.5 * (v.max(0) + v.min(0))
        q = np.cumsum(-(np.abs(v + v0) * i).sum(0)) * dth
        return float(q.max() - q.min())
    N = 2 * n + 2                       # variables i_0..i_n-1, q_0..q_n-1, q_max, q_min
    c = np.zeros(N)
    c[-2], c[-1] = 1.0, -1.0
    aeq, aub = lil_matrix((n, N)), lil_matrix((2 * n, N))
    for k in range(n):
        aeq[k, n + (k + 1) % n], aeq[k, n + k], aeq[k, k] = 1.0, -1.0, -dth      # periodic charge balance
        aub[k, n + k], aub[k, N - 2] = 1.0, -1.0                                 # q_k <= q_max
        aub[n + k, n + k], aub[n + k, N - 1] = -1.0, 1.0                         # q_min <= q_k
    r = linprog(c, A_ub=aub.tocsr(), b_ub=np.zeros(2 * n), A_eq=aeq.tocsr(), b_eq=np.zeros(n),
                bounds=[(a[k], b[k]) for k in range(n)] + [(None, None)] * (n + 2), method="highs")
    assert r.status == 0, r.message
    return float(r.fun)


def c_half(qpp, i_rms, dv):
    """F per DC-link half for a peak-to-peak midpoint ripple dv.  The midpoint current divides between both halves (the
    battery holds the total), so C_eq = 2 C_half."""
    return qpp * i_rms * SQ2 / (W * 2.0 * dv)


L_TOT_B = 49e-6 + 15e-6      # B's L1 + L2 (claim 3; the study's 3-level L1 at 32 kHz and its L2)
GRID_Q = {"IEEE 1547-2018 category B (Q = 0.44 S)": (0.44 * pd.I_RATED, 90.0),
          "EN 50549-1 / GB/T 34120 class (PF 0.9 at rated current)": (pd.I_RATED, 25.84)}


def claim1():
    r = {"rows": []}
    for vdc in (600.0, 750.0, 950.0):
        for ang in (0.0, 25.84, 36.87, 60.0, 90.0, -90.0):
            m, phi, _, _ = pd.op_point(vdc, pd.V_LL, pd.I_2MIN, ang, L_TOT_B)
            q = {"best": np_qpp(m, phi), "minmax": np_qpp(m, phi, "minmax"),
                 "spwm": np_qpp(m, phi, "spwm") if m <= 1.0 else float("nan")}
            r["rows"].append({"vdc": vdc, "ang": ang, "m": m, **q,
                              **{f"C_{i:.0f}A_{dv:.0f}V": c_half(q["best"], i, dv) for i in (pd.I_CONT, pd.I_2MIN) for dv in (60.0, 120.0)}})
    worst = lambda vdc, key: max(x[key] for x in r["rows"] if x["vdc"] == vdc and abs(x["ang"]) == 90.0)   # noqa: E731
    r["C_pf0"] = {vdc: {k: worst(vdc, k) for k in ("C_198A_60V", "C_216A_60V", "C_198A_120V", "C_216A_120V")} for vdc in (600.0, 750.0, 950.0)}
    # grid-code reactive ranges at the 600 V corner (the worst): 60 V pp
    r["grid"] = {}
    for name, (i_rms, ang) in GRID_Q.items():
        cs = []
        for a in (ang, -ang):
            m, phi, _, _ = pd.op_point(600.0, pd.V_LL, i_rms, a, L_TOT_B)
            cs.append(c_half(np_qpp(m, phi), i_rms, 60.0))
        r["grid"][name] = max(cs)
    # the same at 950 V, where the half voltage (and the device stress) is highest
    m950, phi950, _, _ = pd.op_point(950.0, pd.V_LL, pd.I_2MIN, 90.0, L_TOT_B)
    q950 = np_qpp(m950, phi950)
    r["dv_950_700uF"] = q950 * pd.I_2MIN * SQ2 / (W * 2 * 700e-6)
    m600, phi600, _, _ = pd.op_point(600.0, pd.V_LL, pd.I_2MIN, 90.0, L_TOT_B)
    r["dv_600_700uF"] = np_qpp(m600, phi600) * pd.I_2MIN * SQ2 / (W * 2 * 700e-6)
    # 150 Hz midpoint current (min-max zero sequence, 600 V, PF 0, 216 A): rms into the midpoint, i.e. into both halves
    th, _, _, v, i = np_bounds(m600, phi600)
    inp = -(np.abs(v - 0.5 * (v.max(0) + v.min(0))) * i).sum(0) * pd.I_2MIN * SQ2
    r["inp_rms"] = float(np.sqrt(np.mean(inp ** 2)))
    # per-kW capacitance of the references (F per half per kW)
    r["per_kW_uF"] = {"Wolfspeed CRD-25BDA6512N-K (T-type, electrolytic)": WS_CHALF / WS_P * 1e9,
                      "TI TIDA-01606 (T-type, electrolytic)": TIDA01606["C_half"] / TIDA01606["P"] * 1e9,
                      "study's three-level need (3.5 mF per half)": 3.5e-3 / pd.P_RATED * 1e9,
                      "candidate A (two-level, 700 uF film per half)": SPEC["dc_link"]["C_half_uF"] * 1e-6 / pd.P_RATED * 1e9}
    # two-level film banks are sized by ripple current: capacitors per ampere of phase current (A vs CRD200DA23N-GMA)
    r["twol"] = {"A_caps_per_A": 2 * SPEC["dc_link"]["per_half"] / pd.I_CONT, "crd200_caps_per_A": CRD200["n_caps"] / CRD200["I"],
                 "A_uF_per_kW_bus": SPEC["dc_link"]["C_half_uF"] / 2 / pd.P_RATED * 1e3,
                 "crd200_uF_per_kW_bus": CRD200["C_bus"] / CRD200["P"] * 1e9,
                 "A_I_cap_A": SPEC["dc_link"]["I_per_cap_A"], "A_I_cap_max": pd.FILM["C3D1U147"]["imax"]}
    # Wolfspeed's own bank at its rating and PF 0 (it was only tested near PF 1)
    mw, phw, _, _ = pd.op_point(800.0, 400.0, WS_I, 90.0, WS_L)
    r["ws_dv_pf0"] = np_qpp(mw, phw) * WS_I * SQ2 / (W * 2 * WS_CHALF)
    r["ws_scaled_mF"] = WS_CHALF / WS_P * pd.P_RATED * 1e3
    r.update(two_level_fallback())
    return r


B_DEVS = {"T1": ("SG2M035120LJ", 5), "T4": ("SG2M035120LJ", 5), "T2": ("SG2M035120LJ", 6), "T3": ("SG2M035120LJ", 6)}
CAND_B = {"name": "B: T-type, Sichain 1200 V SiC (study's TT-SiC 1200/1200 sizing)", "topo": "TT", "fsw": pd.FSW_CHOICE,
          "devs": B_DEVS, "l_tot": L_TOT_B}
CAND_A = {"name": "A: two-level, 6 x SG2M040170HJ", "topo": "2L", "fsw": pd.FSW_CHOICE,
          "devs": {"TH": ("SG2M040170HJ", 6), "TL": ("SG2M040170HJ", 6)}, "l_tot": 97e-6 + 15e-6}


def two_level_fallback(vdc=600.0, ang=90.0, i_rms=pd.I_CONT, t_in=pd.T_IN):
    """Cost of removing the midpoint current altogether in the reactive corner: run B's T-type legs as two-level legs on their
    outer devices (P/N states only - the limit case of double-signal / virtual-vector PWM, which mixes the two).  Losses and
    junction temperatures from the study's own model (pcs_design.evaluate, its heat sink, 45 C inlet)."""
    ev3 = pd.evaluate(CAND_B, vdc, pd.V_LL, i_rms, ang, t_in, zs="minmax")
    c2 = {"topo": "2L", "fsw": pd.FSW_CHOICE, "devs": {"TH": B_DEVS["T1"], "TL": B_DEVS["T4"]}, "l_tot": L_TOT_B}
    ev2 = pd.evaluate(c2, vdc, pd.V_LL, i_rms, ang, t_in, zs="minmax")
    lim = pd.TJ_LIM["sic"][0]
    return {"fb": {"vdc": vdc, "ang": ang, "i": i_rms, "m": ev3["m"], "leg3_W": ev3["leg_w"], "leg2_W": ev2["leg_w"],
                   "tj3": ev3["tj_max"], "tj2": ev2["tj_max"], "lim": lim,
                   "tjB_110": max(pd.evaluate(CAND_B, v, pd.V_LL, pd.I_CONT, a, t_in, zs="minmax")["tj_max"] for v, a in pd.SIZING_CORNERS)}}


# ============================================================================================ claim 2: cosmic rays
# Wolfspeed Gen 3 / Gen 4 1200 V SiC, sea level, FIT per cm2 vs V_DS: OUR reading of the application brief's Fig. 4 p7
# (power-semiconductors/Wolfspeed_Designed_to_Last_Even_in_the_Harshest_Environments.pdf), 300-dpi render, read by eye
# (about +-10 V in position).  The study's own reading (pcs_devices.COSMIC['sic_gen3_1200']) is compared in the self-check.
GEN3 = [(638, 0.01), (682, 0.1), (716, 0.47), (737, 1.0), (770, 2.5), (812, 7.2), (829, 10.0), (868, 21.0), (920, 49.0),
        (973, 100.0), (999, 162.0)]
GEN4 = [(865, 0.01), (897, 0.1), (936, 1.0), (999, 10.0), (1092, 100.0)]
# battery voltage profile (ESTIMATE): LFP string sized to the 950 V maximum (260 cells x 3.65 V), share of ENERGISED time.
# Rest / plateau 3.25-3.35 V per cell (845-871 V) most of the time; the last few % of charge above 3.4 V; 3.65 V only at
# the end of a full charge.  Not a measurement: a real ESS profile depends on the SOC window and the dispatch.
LFP_PROFILE = [(949.0, 0.005), (910.0, 0.02), (885.0, 0.125), (866.0, 0.35), (845.0, 0.40), (800.0, 0.08), (700.0, 0.02)]
H_YEAR = 8760.0


def fit_cm2(pts, v):
    """log-linear; below the first point the first value is returned as an upper bound (AN 17-003 p6 practice)"""
    return float(np.exp(np.interp(max(v, pts[0][0]), [p[0] for p in pts], np.log([p[1] for p in pts]))))


def share_full_block(m):
    """fraction of time a T-type outer device blocks the full V_dc (the opposite rail is connected): mean N-state duty,
    min-max zero sequence; ~m/pi.  A two-level switch blocks V_dc 50 % of the time."""
    _, vref = pd.references(m, "minmax")
    return float(np.mean(np.clip(-vref, 0.0, 1.0)))


def unit_fit(design, v, curve=GEN3):
    """FIT of one PCS at a DC voltage held continuously, 25 C, sea level (AN 17-003 temperature/altitude factors not applied)"""
    m, _, _, _ = pd.op_point(v, pd.V_LL, pd.I_RATED, 0.0, L_TOT_B)
    sh = share_full_block(m)
    die = dv.SIC_DIE_CM2
    if design == "A":                    # 36 x 1700 V: Wolfspeed 1200 V curve at V x 1200/1700 (the study's scaling assumption)
        return 36 * die["SG2M040170HJ"] * 0.5 * fit_cm2(curve, v * 1200.0 / 1700.0)
    if design == "B":                    # 30 outer 1200 V at V_dc for share sh; 36 inner 1200 V at V_dc/2 (upper bound)
        return 30 * die["SG2M035120LJ"] * sh * fit_cm2(curve, v) + 36 * die["SG2M035120LJ"] * fit_cm2(curve, v / 2)
    if design == "B650":                 # inner 650 V class (Wolfspeed's choice), 0.10 cm2 ASSUMED, voltage-fraction scaling
        return 30 * die["SG2M035120LJ"] * sh * fit_cm2(curve, v) + 36 * 0.10 * fit_cm2(curve, (v / 2) * 1200.0 / 650.0)
    if design == "2L1200":
        return 36 * die["SG2M035120LJ"] * 0.5 * fit_cm2(curve, v)
    if design == "IGBT-TT":              # D-047's 24 outer 1200 V IGBTs (0.39 cm2, pcs_devices) on Semikron's Si curve
        return 24 * dv.IGBTS["CRG40T120BK3SD"]["die_cm2"] * sh * float(dv.fit_per_cm2("si_1200", v))
    raise ValueError(design)


def profile_fit(design, curve=GEN3):
    return sum(w * unit_fit(design, v, curve) for v, w in LFP_PROFILE)


def claim2():
    designs = ("A", "B", "B650", "2L1200", "IGBT-TT")
    r = {"cm2": {v: {"gen3": fit_cm2(GEN3, v), "gen4": fit_cm2(GEN4, v), "si_1200": float(dv.fit_per_cm2("si_1200", v)),
                     "study_gen3": float(dv.fit_per_cm2("sic", v, 1200.0))} for v in (800.0, 850.0, 900.0, 950.0)},
         "unit": {d: {"950": unit_fit(d, 950.0), "900": unit_fit(d, 900.0), "866": unit_fit(d, 866.0), "profile": profile_fit(d),
                      "profile_gen4": profile_fit(d, GEN4)} for d in designs},
         "share": {v: share_full_block(pd.op_point(v, pd.V_LL, pd.I_RATED, 0.0, L_TOT_B)[0]) for v in (750.0, 950.0)}}
    r["fleet_per_year"] = {d: r["unit"][d]["profile"] * 1e-9 * H_YEAR * 5000 for d in designs}
    r["fleet_950"] = {d: r["unit"][d]["950"] * 1e-9 * H_YEAR * 5000 for d in designs}
    r["v_eq_650_475"] = 475.0 * 1200.0 / 650.0
    r["above_900"] = sum(w for v, w in LFP_PROFILE if v > 900.0)
    return r


# ============================================================================================ claim 3: B costed, same basis
def row(block, part):
    hits = [x for x in BOM_A if x["block"] == block and x["part"].startswith(part)]
    assert len(hits) == 1, (block, part, len(hits))
    return hits[0]


def l_price(l_h, i_pk=344.0):
    """the study's own inductor price model (pcs_costed_bom.csv L1 row): 10 USD + 6 USD/J catalogue, x0.80 at 5,000"""
    e = 0.5 * l_h * i_pk ** 2
    return 10.0 + 6.0 * e, (10.0 + 6.0 * e) * 0.8, e


def bom_b(n_half):
    """B on A's price basis: every row of A's costed BOM is kept except the ones a T-type changes.  Unit prices are A's rows
    (or pcs_devices.part_price for the SiC device), so the comparison carries no new price assumption except where named."""
    keep_out = {("POWER", "SG2M040170HJ"), ("POWER", "Al2O3"), ("POWER", "C3D1U147"), ("POWER", "FCSA3DS225"),
                ("POWER", "C0G 2220"), ("GATE DRIVE", "NSI6651ASC"), ("GATE DRIVE", "class (per channel)"),
                ("GATE DRIVE", "per gdrv.channel()"), ("GATE DRIVE", "SN6505BDBVR"), ("LCL FILTER", "L1-97u")}
    base = [x for x in BOM_A if x["block"] != "4-WIRE OPTION" and not any(x["block"] == b and x["part"].startswith(p) for b, p in keep_out)]
    out = [(x["block"], x["function"][:60], float(x["qty"]), float(x["unit_cat_usd"]), float(x["unit_5k_usd"]), "A's row") for x in base]
    n_dev = sum(n for _, n in B_DEVS.values()) * 3
    sic = "SG2M035120LJ"
    out.append(("POWER", "SiC 1200 V 33 mOhm, T1/T4 5 + T2/T3 6 per phase", n_dev, dv.part_price(sic, 1), dv.part_price(sic, 5000),
                "pcs_devices: LCSC C52109938 @1 / @900 (REAL)"))
    for blk, prt, q, note in (("POWER", "Al2O3", n_dev, "A's unit price per device"),
                              ("POWER", "FCSA3DS225", 54, "ESTIMATE: 2 commutation cells per phase -> 2 x A's 27 (A's 1300 V part kept)"),
                              ("POWER", "C0G 2220", 6, "ESTIMATE: one damper per commutation cell (2 per phase)"),
                              ("GATE DRIVE", "NSI6651ASC", 12, "4 channels per phase"),
                              ("GATE DRIVE", "class (per channel)", 12, "A's channel discretes per channel"),
                              ("GATE DRIVE", "per gdrv.channel()", 12, "D-050 stretch + negative-rail detector per channel"),
                              ("GATE DRIVE", "SN6505BDBVR", 5, "Wolfspeed's scheme: common-drain inner pair -> 3 phase-node + 1 midpoint + 1 DC- supply"),
                              ("POWER", "C3D1U147", 2 * n_half, f"{n_half} per half")):
        a = row(blk, prt)
        out.append((blk, a["function"][:60], q, float(a["unit_cat_usd"]), float(a["unit_5k_usd"]), note))
    cat, k5, e = l_price(49e-6)
    out.append(("LCL FILTER", f"L1 49 uH, 216 A rms / 344 A pk ({e:.1f} J)", 3, cat, k5, "the study's price model (10 USD + 6 USD/J, x0.80)"))
    out.append(("CONTROL", "T-type state interlock (outer/inner of one half never on together), 3 x AND gate + RC", 3, 0.15, 0.12,
                "ESTIMATE"))
    return out


def claim3():
    tot = lambda rows, k: sum(q * (c if k == "cat" else f) for _, _, q, c, f, _ in rows)   # noqa: E731
    a_cat, a_5k = SPEC["cost_usd"]["three_wire"]["catalogue"], SPEC["cost_usd"]["three_wire"]["5k"]
    c1 = claim1_cache()
    film = pd.FILM["C3D1U147"]["C"]
    variants = {
        "B1 study's premise: full 216 A at PF 0, 60 V pp, best carrier-based control": math.ceil(c1["C_pf0"][600.0]["C_216A_60V"] / film),
        "B2 full 216 A at PF 0, 120 V pp": math.ceil(c1["C_pf0"][600.0]["C_216A_120V"] / film),
        "B3 grid-code reactive range only, 60 V pp": math.ceil(max(c1["grid"].values()) / film),
        "B4 full current at PF 0 by a two-level fallback in the reactive corner (firmware), 5 per half": SPEC["dc_link"]["per_half"],
    }
    r = {"A": (a_cat, a_5k), "variants": {}, "rows_B4": bom_b(SPEC["dc_link"]["per_half"])}
    for name, n in variants.items():
        rows = bom_b(n)
        r["variants"][name] = {"n_half": n, "cat": tot(rows, "cat"), "5k": tot(rows, "5k")}
    r["A_check"] = (sum(float(x["ext_cat_usd"]) for x in BOM_A if x["block"] != "4-WIRE OPTION"),
                    sum(float(x["ext_5k_usd"]) for x in BOM_A if x["block"] != "4-WIRE OPTION"))
    r["perf"] = performance()
    # the same totals with L1 and L2 re-priced from their mass (claim 4), moderate and aggressive design points
    c4 = claim4()
    l2 = 3 * (c4["L2"]["usd_5k"] - c4["study"]["L2"][1])
    b4 = r["variants"][[k for k in r["variants"] if k.startswith("B4")][0]]["5k"]
    r["corrected_5k"] = {d: {"A": a_5k + 3 * (c4["A_L1"][d]["usd_5k"] - c4["study"]["L1"][1]) + l2,
                             "B4": b4 + 3 * (c4["B_L1"][d]["usd_5k"] - c4["study_B_L1"][1]) + l2} for d in ("moderate", "aggressive")}
    return r


_C1 = {}


def claim1_cache():
    if not _C1:
        _C1.update(claim1())
    return _C1


def device_w(cand, vdc, load, ang=0.0, tj=None, eon_rg=None):
    """three-leg device loss (W) and AC power at a load fraction of 180 A, 400 V; Tj from the study's heat-sink iteration
    (evaluate) unless tj is given; eon_rg = {part: factor} multiplies E_on for the given parts (sensitivity)"""
    orig = pv.eon_rg_factor
    if eon_rg:
        pv.eon_rg_factor = lambda d, rg: next((f for p, f in eon_rg.items() if pv.MOSFETS.get(p) is d), 1.0)
    try:
        ev = pd.evaluate(cand, vdc, pd.V_LL, pd.I_RATED * load, ang, pd.T_IN, zs=None if cand["topo"] == "2L" else "minmax")
    finally:
        pv.eon_rg_factor = orig
    return 3 * ev["leg_w"], ev["p"], ev["tj_max"]


def performance():
    """A against B on the study's own model: device loss and L1/L2 loss (pcs_design.filter_loss_est), E_off x2.17 (the study's
    step-e factor) on both; and the E_on correction to the chosen R_G,on (claim 6) as a second line."""
    pd.EOFF_MULT["value"] = SPEC["commutation_and_gate_drive"]["eoff_factor_vs_datasheet_rg"]
    rg_on = SPEC["commutation_and_gate_drive"]["chosen"]["R_G_on_ext_ohm_per_device"]
    fon = {p: pv.eon_rg_factor(pv.MOSFETS[p], rg_on) for p in ("SG2M040170HJ", "SG2M035120LJ")}
    out = {"eon_factor": fon, "rg_on": rg_on}
    try:
        for tag, cand, l1 in (("A", CAND_A, 97e-6), ("B", CAND_B, 49e-6)):
            for vdc, load in ((750.0, 1.0), (900.0, 1.0), (600.0, 0.35)):
                w, p, tj = device_w(cand, vdc, load)
                w2, _, tj2 = device_w(cand, vdc, load, eon_rg=fon)
                lf = pd.filter_loss_est(l1, 15e-6, pd.FSW_CHOICE, pd.I_RATED * load)
                out[(tag, vdc, load)] = {"dev_W": w, "dev_W_eon": w2, "filt_W": lf, "p_W": p, "tj": tj, "tj_eon": tj2}
    finally:
        pd.EOFF_MULT["value"] = 1.0
    return out


# ============================================================================================ claim 4: filter
# Area-product estimate of an inductor (ESTIMATE): A_w A_c = L I_pk I_rms / (B_pk J k_u); C-core (or toroid treated alike)
# of leg width a, build 1.5 a, window a x 2.5 a, stacking 0.8: AP = 3.0 a^4, core volume 15.2 a^3 (gross), copper k_u 2.5 a^2
# x MLT 6.57 a.  Prices: copper 18 USD/kg (the repo's basis, gen/data/cost_estimates.csv row 'L_CELL 224uH'); amorphous
# cut core 7.5 USD/kg and Si-steel 3 USD/kg ESTIMATED (no quote on file); insulation + winding + test 11 USD (the L_CELL
# row's 4 + 7 USD) grown with the square root of the mass above 1.5 kg, +10 % as the L_CELL basis; x0.80 at 5,000 as the study.
CU_USD_KG, CU_RHO_100C = 18.0, 2.3e-8
CORES = {"amorphous": {"rho": 7.18, "usd_kg": 7.5}, "powder": {"rho": 6.0, "usd_kg": 6.0}, "si_steel": {"rho": 7.65, "usd_kg": 3.0}}


def ap_inductor(l_h, i_pk, i_rms, b_pk, j_a_mm2, ku, core):
    ap = l_h * i_pk * i_rms / (b_pk * j_a_mm2 * 1e6 * ku) * 1e8          # cm^4
    a = (ap / 3.0) ** 0.25                                               # cm
    core_kg = 15.2 * a ** 3 * 0.8 * CORES[core]["rho"] / 1000.0
    cu_cm3 = ku * 2.5 * a ** 2 * 6.57 * a
    cu_kg = cu_cm3 * 8.96 / 1000.0
    p_cu = CU_RHO_100C * (j_a_mm2 * 1e6) ** 2 * cu_cm3 * 1e-6            # W at I_rms
    labour = 11.0 * max(1.0, (core_kg + cu_kg) / 1.5) ** 0.5             # L_CELL's 4 + 7 USD for ~1.5 kg, grown with size
    usd = (core_kg * CORES[core]["usd_kg"] + cu_kg * CU_USD_KG + labour) * 1.10
    return {"ap_cm4": ap, "a_cm": a, "core_kg": core_kg, "cu_kg": cu_kg, "kg": core_kg + cu_kg, "p_cu_W": p_cu, "usd_cat": usd, "usd_5k": 0.8 * usd}


def per_unit(p, v_ll, l_h, c_f, fsw, vdc, levels, i_pk):
    zb = v_ll ** 2 / p
    ripple = vdc / ((8.0 if levels == 3 else 4.0) * l_h * fsw)        # worst-case p-p ripple of one leg (d = 0.5)
    return {"L_pct": 100 * l_h * W / zb, "C_pct": 100 * c_f * W * zb, "ripple_pp_A": ripple, "ripple_of_pk": ripple / i_pk,
            "LfI_over_V": l_h * fsw * i_pk / vdc, "uH_kW": l_h * 1e6 * p / 1e3}


def claim4():
    i_pk_ws = WS_I * SQ2
    r = {"ws": per_unit(WS_P, 400.0, WS_L, WS_CF, WS_FSW, 800.0, 3, i_pk_ws),
         "ws_calc": per_unit(WS_P, 400.0, WS_L_CALC, WS_CF_CALC, 64e3, 800.0, 3, i_pk_ws),
         "A": per_unit(pd.P_RATED, pd.V_LL, 97e-6, 50e-6, pd.FSW_CHOICE, 950.0, 2, pd.I_2MIN * SQ2),
         "B": per_unit(pd.P_RATED, pd.V_LL, 49e-6, 50e-6, pd.FSW_CHOICE, 950.0, 3, pd.I_2MIN * SQ2),
         "L2_pct": 100 * 15e-6 * W / (pd.V_LL ** 2 / pd.P_RATED)}
    # method check on the one inductor with a published mass: Wolfspeed's 125 uH choke (powder toroid, 60 kHz) at 0.5 T, 4.5 A/mm2
    r["ws_ap"] = ap_inductor(WS_L, i_pk_ws * 1.13, WS_I, 0.5, 4.5, 0.35, "powder")      # I_pk incl. the 13 A p-p ripple / 2
    # A's L1 and B's L1 (amorphous C-core, 216 A rms, 344 A pk incl. ripple as the study's row), two design points each
    for tag, l in (("A_L1", 97e-6), ("B_L1", 49e-6)):
        r[tag] = {"moderate": ap_inductor(l, 344.0, pd.I_2MIN, 1.2, 3.5, 0.45, "amorphous"),
                  "aggressive": ap_inductor(l, 344.0, pd.I_2MIN, 1.3, 4.5, 0.50, "amorphous")}
    r["L2"] = ap_inductor(15e-6, 344.0, pd.I_2MIN, 1.4, 3.5, 0.45, "si_steel")
    r["study"] = {k: (float(row("LCL FILTER", p)["unit_cat_usd"]), float(row("LCL FILTER", p)["unit_5k_usd"]))
                  for k, p in (("L1", "L1-97u"), ("L2", "L2-15u"))}
    r["study_B_L1"] = l_price(49e-6)[:2]
    # amorphous core loss at the ripple (Metglas 2605SA1 fit P = 6.5 f^1.51 B^1.74 W/kg, f kHz, B T - from memory),
    # ripple flux = B_pk x (ripple / I_pk); averaged over the fundamental as (d(1-d) / 0.25)^1.74 for a 2L leg at m = 0.87
    th = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    d = 0.5 * (1 + 0.87 * np.sin(th))
    avg = float(np.mean((d * (1 - d) / 0.25) ** 1.74))
    bac = 1.2 * r["A"]["ripple_pp_A"] / 344.0 / 2.0
    r["A_core_W"] = 6.5 * (pd.FSW_CHOICE / 1e3) ** 1.51 * bac ** 1.74 * avg * r["A_L1"]["moderate"]["core_kg"]
    r["A_L1_budget"] = SPEC["inductors"]["L1"]["loss_budget_W"]
    return r


# ============================================================================================ claim 5: six in parallel
def claim5():
    """Sensitivity of A's hottest device to dynamic current sharing: the study takes the hottest of six devices at 1.10 x the
    mean current (conduction AND switching); 1.30 is the kind of switching-loss imbalance reported for unbinned SiC parts
    (from memory, see the report).  Study's model and heat sink at its worst corner, E_off x2.17 as the study."""
    old = pd.K_SHARE["sic"]
    pd.EOFF_MULT["value"] = SPEC["commutation_and_gate_drive"]["eoff_factor_vs_datasheet_rg"]
    at = SPEC["thermal_and_losses"]["envelope_45C"]["at"]
    out = {"corner": at}
    try:
        for k in (1.10, 1.30):
            pd.K_SHARE["sic"] = k
            out[k] = pd.evaluate(CAND_A, at["vdc"], at["vll"], pd.I_RATED * at["load"], at["ang"], pd.T_IN)["tj_max"]
            out[(k, "hot")] = pd.evaluate(CAND_A, at["vdc"], at["vll"], pd.I_RATED * at["load"], at["ang"], pd.T_IN_HOT)["tj_max"]
    finally:
        pd.K_SHARE["sic"] = old
        pd.EOFF_MULT["value"] = 1.0
    d = pv.MOSFETS["SG2M040170HJ"]
    out["vth"] = (d["vth_min"], d["vth"], 4.0)        # SG2M040170HJ Table 4 p4 min / typ / max
    out["gfs"] = d["gfs"]
    out["lim"] = pd.TJ_LIM["sic"][0]
    return out


# ============================================================================================ claim 6: calibration
EON_RG_C25 = [(2.5, 130e-6), (20.0, 480e-6)]      # C3M0025065K Fig. 24 p7 (400 V, 33.5 A, 25 C), read by eye
EOFF_RG_C25 = [(2.5, 60e-6), (20.0, 315e-6)]
T_AMB_WS = 25.0                                    # ASSUMED: the guide gives no ambient for Table 12


def ws_point(vdc, vll, p_out_kw, rg="datasheet", zs="minmax"):
    """device loss of the Wolfspeed board at one Table 6 point with the study's model (pcs_design.leg_losses).
    rg='datasheet': E at the data-sheet R_G (as the study applies it); rg='board': E_on / E_off scaled to the board's gate
    network with the data sheets' E-vs-R_G figures.  Tj: the board's own thermal path from Table 12 (device rise above
    ambient proportional to device loss, anchored at Wolfspeed's 28.5 W / 70.5 C and 11 W / 60.6 C)."""
    i_load = p_out_kw * 1e3 / (math.sqrt(3) * vll)
    i_cf = vll / math.sqrt(3) * W * WS_CF                          # C22 current (resistive load: leading the load current)
    i_conv = math.hypot(i_load, i_cf)
    ang = -math.degrees(math.atan2(i_cf, i_load))
    m, phi, _, _ = pd.op_point(vdc, vll, i_conv, ang, WS_L)
    orig_on = pv.eon_rg_factor
    if rg == "board":
        f = {"C3M0032120K": lin(EON_RG_C32, WS_RG_ON) / lin(EON_RG_C32, 2.5), "C3M0025065K": lin(EON_RG_C25, WS_RG_ON) / lin(EON_RG_C25, 2.5)}
        pv.eon_rg_factor = lambda d, r: next((v for k, v in f.items() if pv.MOSFETS[k] is d), 1.0)
        pd.EOFF_MULT["value"] = lin(EOFF_RG_C32, WS_RG_OFF) / lin(EOFF_RG_C32, 2.5)
    tj = {p: (60.0, 60.0) for p in WS_DEV}
    try:
        for _ in range(8):
            res, leg = pd.leg_losses("TT", WS_DEV, WS_FSW, vdc, m, phi, i_conv, zs, tj)
            new = {}
            for p, ref in (("T1", "T1"), ("T4", "T1"), ("T2", "T2"), ("T3", "T2")):
                t = T_AMB_WS + (WS_T12[ref]["Tj"] - T_AMB_WS) * res[p]["tot"] / WS_T12[ref]["loss_W"]
                new[p] = (t, t)
            tj = new
    finally:
        pv.eon_rg_factor = orig_on
        pd.EOFF_MULT["value"] = 1.0
    return {"dev_W": 3 * leg, "T1_W": res["T1"]["tot"], "T2_W": res["T2"]["tot"], "i": i_conv, "m": m,
            "dcr_W": 3 * i_conv ** 2 * WS_DCR, "tj1": tj["T1"][0]}


def claim6():
    rows = []
    for (vdc, vll), pts in WS_ETA.items():
        for p_in, p_out, eta in pts:
            a = ws_point(vdc, vll, p_out)
            b = ws_point(vdc, vll, p_out, rg="board")
            loss_eta = p_out * 1e3 * (100.0 / eta - 1.0)
            loss_p = (p_in - p_out) * 1e3
            rows.append({"vdc": vdc, "vll": vll, "p_out": p_out, "eta": eta, "loss_eta": loss_eta, "loss_p": loss_p,
                         "dev_a": a["dev_W"], "dev_b": b["dev_W"], "dcr": b["dcr_W"], "i": b["i"], "T1_b": b["T1_W"], "T2_b": b["T2_W"],
                         "tj1": b["tj1"], "rest_b": min(loss_eta, loss_p) - b["dev_W"] - b["dcr_W"]})
    full = next(x for x in rows if (x["vdc"], x["vll"]) == (800, 400) and x["p_out"] > 24)
    out = {"rows": rows, "full": full, "ws_dev_T12": 3 * (2 * WS_T12["T1"]["loss_W"] + 2 * WS_T12["T2"]["loss_W"])}
    # load-independent part: extrapolate (measured loss - model device loss - DCR) to zero load on the 800 V / 400 V series
    s = [x for x in rows if (x["vdc"], x["vll"]) == (800, 400)]
    k = np.polyfit([x["p_out"] for x in s], [x["rest_b"] for x in s], 1)
    out["rest_fit"] = (float(k[1]), float(k[0]))          # W at zero load, W per kW
    out["share_full"] = (full["dev_b"] + full["dcr"]) / full["loss_eta"]
    # CRD200DA23N-GMA Fig. 50 (PRD-09623 section 5.2): 167 A rms at 300 Hz into 129 uH - reactive power the load can take
    out["crd200_kvar"] = 3 * 167.0 ** 2 * 2 * math.pi * 300.0 * 129e-6 / 1e3
    return out


# ============================================================================================ report
def f0(x):
    return f"{x:,.0f}"


def report(c1, c2, c3, c4, c5, c6):
    L = []
    w = L.append
    b4 = c3["variants"][[k for k in c3["variants"] if k.startswith("B4")][0]]
    a_cat, a_5k = c3["A"]
    pf = c3["perf"]
    gain = lambda v, ld, key: (pf[("A", v, ld)][key] + pf[("A", v, ld)]["filt_W"]) - (pf[("B", v, ld)][key] + pf[("B", v, ld)]["filt_W"])  # noqa: E731
    cm, ca = c3["corrected_5k"]["moderate"], c3["corrected_5k"]["aggressive"]
    a35, a750 = pf[("A", 600.0, 0.35)], pf[("A", 750.0, 1.0)]
    pk, fl_eta = SPEC["thermal_and_losses"]["efficiency"]["peak"], SPEC["thermal_and_losses"]["efficiency"]["full_load_750V"]
    de_pk = 100 * (a35["dev_W_eon"] - a35["dev_W"]) / a35["p_W"]
    de_fl = 100 * (a750["dev_W_eon"] - a750["dev_W"]) / a750["p_W"]
    w("# PCS-P125 topology cross-check against Wolfspeed CRD-25BDA6512N-K (sim/pcs_crosscheck.py)")
    g_lo, g_hi = (100 * gain(750.0, 1.0, k) / 124.7e3 for k in ("dev_W", "dev_W_eon"))
    w(f"**Verdict: A holds with conditions.** B - a T-type done the Wolfspeed way with Chinese SiC (66 x Sichain 1200 V, 12 gate "
      f"channels) - is CALCULATED {g_lo:.1f}-{g_hi:.1f} %-points more efficient at 125 kW and, once the filter inductors are priced "
      f"from their mass, costs about the same: {f0(ca['B4'])}-{f0(cm['B4'])} against A's {f0(ca['A'])}-{f0(cm['A'])} USD at 5,000 units "
      f"(on the study's own prices B is +{f0(b4['5k'] - a_5k)} USD with the midpoint handled by firmware and up to "
      f"+{f0(max(v['5k'] for v in c3['variants'].values()) - a_5k)} USD with the capacitors the study's premise needs).  ")
    w(f"A keeps the decision on robustness and simplicity, not on cost: its devices sit at 0.56 of rating (SRC-4 keeps the 0.67 "
      f"rule; B's outer devices sit at 0.79) and give {c2['unit']['A']['950']:.2f} FIT per unit at a constant 950 V against B's "
      f"{c2['unit']['B']['950']:.0f} ({c2['unit']['B']['profile']:.0f} on a battery profile; Wolfspeed Gen 3 data as proxy, no Chinese maker "
      f"publishes any), and it needs 6 gate channels instead of 12 and no midpoint control.  ")
    w("Claims 1 and 2 are overstated as general statements (3.5 mF per half holds only for carrier-based control at full current, "
      "PF 0 and 600 V; the 0.67 rule stands in for a FIT budget); claims 4 and 6 expose two optimistic numbers behind A - inductor "
      f"prices about half their material cost, and E_on taken at the data-sheet gate resistor (peak {100 * pk:.2f} % is more like "
      f"{100 * pk - de_pk - 0.2:.1f}-{100 * pk - de_pk:.1f} %).  None reverses the choice; the conditions are at the end.  ")
    w("Labels: MEASURED-BY-WOLFSPEED, CALCULATED-BY-WOLFSPEED, DATASHEET, CALCULATED (ours), ESTIMATE (ours, named assumption). "
      "Nothing of ours is bench-validated.\n")
    w("Inputs: Wolfspeed user guide PRD-08613 Rev. 2 (pages as printed), schematic and BOM rev 3.0 in "
      "`docs/reference-designs/wolfspeed/crd-25bda6512n-k/` (README there); the study's model and outputs, read-only "
      "(`sim/pcs_design.py`, `pcs_devices.py`, `pv_devices.py`, `sim/out/pcs_design/pcs_spec.json`, `pcs_costed_bom.csv`); "
      "C3M0025065K data sheet filed for this check.  The second Wolfspeed design, CRD200DA23N-GMA (200 kW two-level, 1500 V), "
      "is used where it helps (claims 2, 3, 6).\n")

    # ---------------------------------------------------------------- claim 1
    w("## Claim 1 - 'any three-level stage needs about 3.5 mF per DC-link half for the 150 Hz midpoint ripple at PF 0'\n")
    w("**Method (CALCULATED, independent of the study's code).** Local-average midpoint current of three three-level legs, "
      "i_np = sum (1 - |v_x|) i_x, with the phase references free to take any zero sequence that keeps every |v_x| <= 1.  For each "
      "operating point a linear programme finds the least peak-to-peak midpoint charge that ANY carrier-based zero sequence can "
      "reach over a fundamental period (an ideal predictive midpoint controller - no real controller does better).  Both halves "
      "take the midpoint current (the battery holds the total voltage), so C_eq = 2 C_half.  m and the converter-side angle "
      "include B's L1 + L2 (64 uH).  Check: with no zero sequence the swing is 0.5 m I_pk/omega at PF 0 and 0.342 m at PF 1 "
      "(textbook result for sinusoidal PWM, from memory; reproduced, see self-check).\n")
    w("| V_dc | m at PF 0 | charge swing at PF 0 (I_pk/omega): SPWM / min-max / best | C per half for 60 V pp, 198 / 216 A | for 120 V pp, 198 / 216 A |")
    w("|---|---|---|---|---|")
    for vdc in (600.0, 750.0, 950.0):
        x = max((r for r in c1["rows"] if r["vdc"] == vdc and abs(r["ang"]) == 90.0), key=lambda r: r["best"])
        cc = c1["C_pf0"][vdc]
        sp = "n/a (m > 1)" if math.isnan(x["spwm"]) else f"{x['spwm']:.3f}"
        w(f"| {vdc:.0f} V | {x['m']:.2f} | {sp} / {x['minmax']:.3f} / {x['best']:.3f} | {cc['C_198A_60V'] * 1e3:.2f} / "
          f"{cc['C_216A_60V'] * 1e3:.2f} mF | {cc['C_198A_120V'] * 1e3:.2f} / {cc['C_216A_120V'] * 1e3:.2f} mF |")
    ang_row = lambda vdc: ", ".join(f"{r['ang']:.0f} deg: {r['best']:.2f}" for r in c1["rows"] if r["vdc"] == vdc and r["ang"] >= 0)  # noqa: E731
    w(f"\nBest-case residual charge against PF angle (I_pk/omega; 0 = the zero sequence cancels it): 600 V - {ang_row(600.0)}; "
      f"750 V - {ang_row(750.0)}; 950 V - {ang_row(950.0)}.\n")
    g = list(c1["grid"].items())
    w(f"**The number holds - at one corner.** CALCULATED: {c1['C_pf0'][600.0]['C_198A_60V'] * 1e3:.1f}-{c1['C_pf0'][600.0]['C_216A_60V'] * 1e3:.1f} mF "
      f"per half at 600 V, PF 0, 198-216 A and 60 V pp; {c1['C_pf0'][750.0]['C_216A_60V'] * 1e3:.1f} mF at 750 V and "
      f"{c1['C_pf0'][950.0]['C_216A_60V'] * 1e3:.1f} mF at 950 V.  The study's 3.5 mF is therefore right for its premise (to within "
      f"10 %).  It is not right for 'any three-level stage': each of the three premises carries it.\n")
    w(f"1. **Full current at PF 0.** REQUIREMENTS AC-02 says 'PF -1...+1'; Megarevo's PMA0125 data sheet "
      f"(`docs/reference-designs/megarevo/pma/pma-125-135kw_datasheet_v1-0.pdf`, AC table) prints 'Adjustable power factor range "
      f">0.99; -1~+1' next to 137 kVA continuous and 150 kVA maximum, with no separate reactive-power rating - so full current at "
      f"PF 0 is implied, not stated; off-grid, the load sets the power factor.  The grid codes the study names ask for less "
      f"(clauses from memory, not on file): {g[0][0]} needs {g[0][1] * 1e3:.2f} mF and {g[1][0]} {g[1][1] * 1e3:.2f} mF per half at "
      f"600 V and 60 V pp (CALCULATED).  Low-voltage ride-through reactive current is short and comes with a low grid voltage "
      f"(low m), where the residual is small.\n")
    w(f"2. **60 V pp everywhere.** The worst ripple is at the bottom of the DC range, where each half sits at 300 V and is far "
      f"from any voltage limit; at 950 V, where the half voltage matters, the best-case ripple with A's own 700 uF per half is "
      f"{c1['dv_950_700uF']:.0f} V pp (CALCULATED, 216 A, PF 0), {100 * (c1['dv_950_700uF'] / 60 - 1):.0f} % above the study's 60 V target.  A voltage-dependent "
      f"limit (the half voltage stays below the 600 V film rating and the inner devices' derating) would cut the need sharply; "
      f"at 600 V the same 700 uF would swing {c1['dv_600_700uF']:.0f} V pp, which the modulator can follow (each phase can still "
      f"reach +-V_dc/2 when the levels' actual values are fed forward) but which we would not accept without a control simulation.\n")
    fb = c1["fb"]
    w(f"3. **Carrier-based modulation.** Modulations that cancel the low-frequency midpoint current at any m and PF exist: the "
      f"nearest-three-virtual-vector PWM (Busquets-Monge, Bordonau, Boroyevich, Somavilla, IEEE PEL 2004) and its carrier form, "
      f"double-signal PWM (Pou et al., IEEE TIE 2007) - from memory: each leg uses all three levels inside a carrier period, which "
      f"raises the switching losses and the output ripple while it is active, so hybrids use it only where the carrier-based "
      f"residual is non-zero (low PF, high m).  The limit case is two-level operation of the T-type's outer devices, which draws "
      f"no midpoint current at all.  On the study's own model and heat sink (CALCULATED, B's devices, {fb['vdc']:.0f} V, PF 0, "
      f"{fb['i']:.0f} A, 45 C inlet): leg loss {fb['leg3_W']:.0f} W three-level -> {fb['leg2_W']:.0f} W two-level, hottest junction "
      f"{fb['tj3']:.0f} -> {fb['tj2']:.0f} C against the {fb['lim']:.0f} C limit (B's worst junction over the study's sizing corners at "
      f"110 % is {fb['tjB_110']:.0f} C).  So the PF-0 corner can be covered by firmware with B's device count; the cost is "
      f"two-level ripple in L1 in that mode (B's 49 uH L1 sees about 96 A p-p at 600 V instead of its 76 A design value) and a "
      f"two-level common-mode step, both no worse than A's.  The 150 Hz midpoint current itself is a thermal non-issue for film: "
      f"{c1['inp_rms']:.0f} A rms at 600 V / PF 0 / 216 A with min-max, i.e. about {c1['inp_rms'] / 2 / 5:.0f} A rms per capacitor "
      f"with 5 per half (C3D1U147: 40.2 A).\n")
    w("| reference (three-level unless stated) | capacitance per half per kW | at 125 kW |")
    w("|---|---|---|")
    for k, v in c1["per_kW_uF"].items():
        w(f"| {k} | {v:.1f} uF/kW | {v * 125 / 1e3:.2f} mF |")
    w(f"\nWhat Wolfspeed fitted: 4 x 470 uF / 500 V aluminium electrolytics per half (BOM line 1; UG p25) = 75 uF per kW per half, "
      f"2.7 times the study's 3.5 mF scaled to 25 kW, plus 2 x 2 uF film and 2 x 0.1 uF C0G per phase across the halves "
      f"(schematic sheet 2.x, C9-C12).  At its own rating that bank would ripple {c1['ws_dv_pf0']:.0f} V pp at PF 0 (CALCULATED, "
      f"best case).  The guide says nothing about reactive operation: the inverter was tested into a resistive load and the PFC "
      f"near unity power factor (UG Table 10 p49: 0.999 at 25 kW, >= 0.99 above 5 kW, 0.90 at 1.27 kW).  TI's TIDA-01606 fits "
      f"about the same per kW.  Both use 500 V electrolytics per half, which a 900 V maximum allows (0.90 of rating); at our "
      f"950 V they would need two in series per half, and on the study's own price for 400 V cans "
      f"(Samyoung TDC400V470, {SPEC['dc_link']['electrolytic_alternative']['cost_usd']['5k'] / SPEC['dc_link']['electrolytic_alternative']['cans']:.2f} "
      f"USD at 5,000) that costs about the same per microfarad as the film it would replace.\n")
    w("Side effect the study already counts (its step f): a three-level midpoint controller needs a low-frequency zero sequence at "
      "every DC voltage, i.e. a 150 Hz voltage between the battery and earth on a three-wire TN connection; A needs one only below "
      "about 680 V.  This, not the capacitance, is B's lasting disadvantage on the DC link.\n")
    span = max(v["5k"] for v in c3["variants"].values()) - b4["5k"]
    w("**Effect on the decision:** the claim is true for the premise the study chose and false as a general statement; with the "
      "reactive corner handled by firmware a T-type needs A's 5 + 5 film bank (ESTIMATE: HF ripple per half no larger than "
      f"A's), which moves B's cost by {f0(span)} USD at 5,000 units (claim 3).  It does not by itself decide between A and B.\n")

    # ---------------------------------------------------------------- claim 2
    u = c2["unit"]
    w("## Claim 2 - '1200 V devices at 950 V fail by cosmic rays at about 104 FIT per unit; the 0.67-of-rating rule settles it'\n")
    w("Per cm2 of die, 25 C, sea level, blocking the voltage continuously.  Wolfspeed Gen 3 / Gen 4 1200 V SiC: our reading of Fig. 4 "
      "p7 of Wolfspeed's application brief on file (300-dpi render, about +-10 V); 'study' = the study's own reading of the same "
      "figure; Si IGBT = Semikron AN 17-003 (Fig. 3 / Table 2, as calibrated in pcs_devices):\n")
    w("| V_DS | SiC Gen 3 (ours / study) | SiC Gen 4 | Si 1200 V IGBT |")
    w("|---|---|---|---|")
    for v, x in c2["cm2"].items():
        w(f"| {v:.0f} V | {x['gen3']:.1f} / {x['study_gen3']:.1f} | {x['gen4']:.2f} | {x['si_1200']:.1f} |")
    w(f"\nPer PCS (CALCULATED from the curves; die areas = pcs_devices' ASSUMED values, 0.12 cm2 for a 33-36 mOhm 1200 V die, "
      f"0.15 cm2 for the 40 mOhm 1700 V die; a T-type outer device blocks the full V_dc only while the opposite rail is connected: "
      f"{100 * c2['share'][950.0]:.0f} % of the time at 950 V, {100 * c2['share'][750.0]:.0f} % at 750 V, min-max PWM; a two-level switch 50 %).  "
      f"'Profile' = an LFP string sized to 950 V (260 cells), ESTIMATED time shares: {', '.join(f'{v:.0f} V {100 * s:.1f} %' for v, s in LFP_PROFILE)} "
      f"of energised time ({100 * c2['above_900']:.1f} % above 900 V):\n")
    w("| design | FIT at 950 V | at 900 V | at 866 V | profile | profile, Gen 4 devices | failures per year, 5,000 units (profile / 950 V) |")
    w("|---|---|---|---|---|---|---|")
    names = {"A": "A: two-level, 36 x 1700 V SiC (1200 V curve at V x 1200/1700)", "B": "B: T-type, 30 outer + 36 inner 1200 V SiC",
             "B650": "B with 650 V inner devices (Wolfspeed's choice), voltage-fraction scaling ASSUMED",
             "2L1200": "two-level, 36 x 1200 V SiC", "IGBT-TT": "D-047: T-type, 24 outer 1200 V IGBTs (Semikron curve)"}
    for d, n in names.items():
        w(f"| {n} | {u[d]['950']:.2f} | {u[d]['900']:.2f} | {u[d]['866']:.2f} | {u[d]['profile']:.2f} | {u[d]['profile_gen4']:.2f} | "
          f"{c2['fleet_per_year'][d]:.2f} / {c2['fleet_950'][d]:.2f} |")
    w(f"\n- **The 104 FIT is reproduced** for the IGBT T-type ({u['IGBT-TT']['950']:.0f} FIT at 950 V).  It is a silicon-IGBT figure; "
      f"for 1200 V SiC the per-cm2 rate is no better (Gen 3: {c2['cm2'][950.0]['gen3']:.0f} against {c2['cm2'][950.0]['si_1200']:.0f} FIT/cm2 at "
      f"950 V) but the dies are smaller: B gives {u['B']['950']:.0f} FIT at a constant 950 V, {u['B']['900']:.0f} at 900 V, about "
      f"{u['B']['profile']:.0f} on the profile.  Gen 4 is about 40 times better at 950 V and puts B at {u['B']['profile_gen4']:.2f} - "
      f"the device generation matters more than the voltage rule.  No Chinese maker (Sichain included) publishes any curve.\n"
      f"- **650 V parts in an I-type at 475 V:** no 650 V SiC curve is on file.  Scaling the 1200 V curve by the fraction of rating "
      f"(the study's method) would put 475 V on 650 V at {c2['v_eq_650_475']:.0f} V-equivalent, about {fit_cm2(GEN3, c2['v_eq_650_475']):.0f} "
      f"FIT/cm2 - but the only measured 650 V data on file contradict that scaling: Semikron finds 650 V silicon chips below "
      f"1 FIT/cm2 up to 500 V and calls a 1000 V NPC on 650 V chips 'immune' (AN 17-003 p9).  Unresolved for SiC; B avoids it by "
      f"using 1200 V inner devices (475 V = 0.40 of rating).\n"
      f"- **How the industry runs 1000 V-class three-level stages (documents on file):** Semikron AN 17-003 p8 - 1000 V PV inverters "
      f"on 1200 V devices are 'electrically feasible' because the highest voltages occur at low current; p10 - a T-type carries about "
      f"one third of the cosmic-ray rate of a two-level stage with the same chips; TI TIDA-01606 (TIDUE53 p15) - designed for a "
      f"1000 V DC link with 1200 V outer and 650 V middle SiC; Wolfspeed CRD-25BDA6512N-K - 900 V maximum on 1200 V outer devices "
      f"(0.75) and 650 V middle devices at 450 V (0.69); Infineon REF-10KW3LNPC2 - 850 V maximum on a 1200 V / 650 V NPC2.  The "
      f"two-level alternative at about two thirds of rating also has a published precedent: Wolfspeed CRD200DA23N-GMA, 1500 V on "
      f"2300 V modules (0.65).\n"
      f"- **Battery profile:** unlike PV, a battery holds its voltage near the top of the range for long periods - an LFP string "
      f"sits on its plateau at about 0.9 of its maximum, so the PV argument (high voltage only at low current) transfers only "
      f"partly.  On the profile above the string spends about {100 * c2['above_900']:.1f} % of its time above 900 V; the profile rate is "
      f"{100 * u['B']['profile'] / u['B']['950']:.0f} % of the constant-950 V rate (ESTIMATE; the real share depends on the SOC window "
      f"and dispatch, and on whether standby parks the legs: a T-type parked in the zero state blocks only V_dc/2 on every device).\n")
    w(f"**Effect on the decision:** the rule does not settle it; the evidence does, partly.  B is a {u['B']['profile']:.0f}-"
      f"{u['B']['950']:.0f} FIT design on a proxy curve (CALCULATED; for scale, from memory, power-converter field failure rates "
      f"are of the order of 1 % per year, about 1,000 FIT, so this is a few per cent of the total - acceptable on a 100 FIT budget, "
      f"but a burnt outer device shorts the DC link), and the Chinese devices' real curves are unknown.  A is "
      f"{u['B']['950'] / u['A']['950']:.0f} times better at 950 V at no cost penalty, which is a legitimate reason to prefer it - "
      f"stated as a FIT budget and a data gap, not as a rule.\n")

    # ---------------------------------------------------------------- claim 3
    w("## Claim 3 - B done the Wolfspeed way with Chinese SiC, scaled to 125 kW and 590-950 V, on the study's price basis\n")
    w(f"**Scaling.** Wolfspeed runs one device per position at {WS_I:.0f} A rms and 60 kHz and reaches only 70.5 C junction "
      f"(Table 12 p53, CALCULATED-BY-WOLFSPEED from a MEASURED 58.2 C case) - its device count is set by efficiency, not by "
      f"temperature.  At the same current per device 125 kW needs 5 per position.  The study's own sizing at its junction limits "
      f"(110 % / 45 C inlet, 150 C; 120 % for 2 min and 200 ms overloads) gives 5 outer + 6 inner Sichain SG2M035120LJ "
      f"(1200 V, 33 mOhm, LCSC-priced) per phase leg, 66 devices - the 'TT-SiC 1200/1200' row of its screen; on its model B's "
      f"hottest junction over the sizing corners at 110 % is {c1['fb']['tjB_110']:.0f} C.  No Chinese 650-750 V SiC in TO-247-4 has a "
      f"public price, so the inner position uses the same 1200 V part (it also removes the 650 V cosmic-ray question).  "
      f"32 kHz as A.  Gate drive: 12 channels (A's channel, driver and D-050 parts per channel) and Wolfspeed's bias scheme - the "
      f"middle pair in common drain shares the phase-node supply with the high-side switch, so 5 isolated supplies serve 12 "
      f"channels (UG p19-21).  LCL: L1 49 uH (three-level, 25 % ripple at 32 kHz - the study's own figure), C_f 50 uF, L2 15 uH "
      f"(resonance 6.6 kHz stiff grid, below f_s/6).  Heat sink, ports, sensing, control, aux: A's rows unchanged.\n")
    w("| variant (three-wire) | film C3D1U147 per half | catalogue USD | 5,000 units USD | vs A at 5,000 |")
    w("|---|---|---|---|---|")
    w(f"| A (study, D-053 + D-050 protection) | {SPEC['dc_link']['per_half']} | {f0(a_cat)} | {f0(a_5k)} | - |")
    for k, v in c3["variants"].items():
        w(f"| {k} | {v['n_half']} | {f0(v['cat'])} | {f0(v['5k'])} | {v['5k'] - a_5k:+.0f} USD ({100 * (v['5k'] / a_5k - 1):+.1f} %) |")
    w("\nRows of B that differ from A (B4; every other row is A's, same unit prices):\n")
    w("| block | item | qty | USD cat / 5k each | basis |")
    w("|---|---|---|---|---|")
    for blk, fn, q, c, f, note in c3["rows_B4"]:
        if note != "A's row":
            w(f"| {blk} | {fn} | {q:.0f} | {c:.2f} / {f:.2f} | {note} |")
    w(f"\n**Performance on the study's model** (CALCULATED; E_off x{SPEC['commutation_and_gate_drive']['eoff_factor_vs_datasheet_rg']:.2f} "
      f"as the study's step e for both; junction temperatures from its heat-sink iteration at 45 C inlet; L1/L2 loss from its "
      f"filter_loss_est; second column with E_on scaled to the chosen R_G,on {pf['rg_on']:.2f} ohm per device by the data sheets' "
      f"E-vs-R_G curves, x{pf['eon_factor']['SG2M040170HJ']:.2f} for A's device and x{pf['eon_factor']['SG2M035120LJ']:.2f} for B's):\n")
    w("| point | A devices W (E_on corrected) | A L1+L2 W | B devices W (E_on corrected) | B L1+L2 W | B saves | Tj max A / B |")
    w("|---|---|---|---|---|---|---|")
    for vdc, ld in ((750.0, 1.0), (900.0, 1.0), (600.0, 0.35)):
        a, b = pf[("A", vdc, ld)], pf[("B", vdc, ld)]
        sav = (a["dev_W_eon"] + a["filt_W"]) - (b["dev_W_eon"] + b["filt_W"])
        w(f"| {vdc:.0f} V, {100 * ld:.0f} % ({a['p_W'] / 1e3:.1f} kW) | {a['dev_W']:.0f} ({a['dev_W_eon']:.0f}) | {a['filt_W']:.0f} | "
          f"{b['dev_W']:.0f} ({b['dev_W_eon']:.0f}) | {b['filt_W']:.0f} | {sav:.0f} W = {100 * sav / a['p_W']:.2f} %-points | "
          f"{a['tj_eon']:.0f} / {b['tj_eon']:.0f} C |")
    d_lo, d_hi = sorted((ca["B4"] - ca["A"], cm["B4"] - cm["A"]))
    w(f"\n**With the inductors re-priced from their mass (claim 4)**, at 5,000 units: A {f0(ca['A'])}-{f0(cm['A'])} USD, B4 "
      f"{f0(ca['B4'])}-{f0(cm['B4'])} USD (aggressive and moderate design points) - B is {f0(d_lo)}-{f0(d_hi)} USD dearer "
      f"({100 * d_lo / cm['A']:.0f}-{100 * d_hi / ca['A']:.0f} %), i.e. at parity within the precision of these estimates: its smaller L1 "
      f"recovers most of what its extra gate channels, devices and decoupling cost.  B's lower loss would let its heat sink shrink "
      f"by perhaps a fifth (ESTIMATE, 10-20 USD; not taken).\n")
    w(f"**Effect on the decision:** B is better on efficiency (CALCULATED {g_hi:.2f} %-points at 125 kW / 750 V, "
      f"{100 * gain(600.0, 0.35, 'dev_W_eon') / pf[('A', 600.0, 0.35)]['p_W']:.2f} at the 35 % peak point, E_on corrected) and on L1 "
      f"size, and no cheaper under any DC-link premise; it doubles the gate channels (12 against 6), nearly doubles the devices "
      f"(66 against 36), needs a midpoint controller with a firmware fallback and a low-frequency zero sequence at every DC "
      f"voltage, breaks the project's 0.67 derating rule (SRC-4) on its outer devices, and carries claim 2's data gap.  The 98.5 % "
      f"efficiency requirement is met by both, so the gain buys nothing the requirements ask for.  A holds; B is the documented "
      f"fallback if efficiency is ever sold or a Chinese maker publishes cosmic-ray data.\n")

    # ---------------------------------------------------------------- claim 4
    w("## Claim 4 - the filter: L1 97 uH, C_f 50 uF, L2 15 uH against the reference, and the inductor cost\n")
    w("| design | L in % of Z_base | C_f in % | worst ripple p-p / I_pk | L f I_pk / V_dc | uH x kW |")
    w("|---|---|---|---|---|---|")
    for tag, lab in (("ws", f"Wolfspeed as built (BOM L1 125 uH, C22 10 uF), three-level, 60 kHz, 25 kW"),
                     ("ws_calc", "Wolfspeed's own procedure (UG p14: 102 uH, 24.9 uF at 64 kHz)"),
                     ("A", "A: two-level, 32 kHz, 125 kW (L1 97 uH, C_f 50 uF)"), ("B", "B: three-level, 32 kHz (L1 49 uH, C_f 50 uF)")):
        x = c4[tag]
        w(f"| {lab} | {x['L_pct']:.2f} % | {x['C_pct']:.1f} % | {x['ripple_pp_A']:.1f} A = {100 * x['ripple_of_pk']:.0f} % | "
          f"{x['LfI_over_V']:.2f} | {f0(x['uH_kW'])} |")
    w(f"\nPer unit the designs agree: both size L for about 25 % peak-to-peak ripple of the peak current; a three-level leg needs "
      f"half the L x f of a two-level leg for the same ripple (0.48-0.50 against 1.00), and A switches at about half Wolfspeed's "
      f"frequency, hence its L1 is 3.9 times Wolfspeed's in per unit.  C_f is 2.0 % of base in both (Wolfspeed cut its own 5 % "
      f"result to 10 uF for nominal noise suppression only, UG p14-15, with a separate EMI board).  Wolfspeed has no L2 on "
      f"the board (an external FN3256H line filter in the test set-up, UG Fig. 26 p32); A's L2 is {c4['L2_pct']:.2f} %.  Wolfspeed damps "
      f"with 0.68 ohm in series with each C_f (R26, 7 W) and ties the C_f star to the DC midpoint through C21 (2.2 uF on the "
      f"schematic, 0.1 uF X1 in the BOM - the two disagree); A ties its star to the midpoint directly.  Wolfspeed gives no core, "
      f"material, turns or wire - only 125 uH / 6.5 mOhm DCR / 100 kHz in the BOM (no maker), a 700 g choke (Fig. 3 p13), a 40 mm "
      f"height limit and a 120 C limit (p15), and references to powder-core design notes; the photograph (p28) shows a "
      f"round-wire toroid.\n")
    wa = c4["ws_ap"]
    w(f"**Inductor cost from mass (ESTIMATE).** Area product A_w A_c = L I_pk I_rms / (B_pk J k_u) on a C-core proportion; copper "
      f"18 USD/kg (the repo's basis), amorphous core 7.5 USD/kg and Si-steel 3 USD/kg (ESTIMATED, no quote), insulation/winding/"
      f"test from the repo's L_CELL row grown with size, +10 %.  The method reproduces the one published mass: Wolfspeed's choke at "
      f"0.5 T, 4.5 A/mm2, k_u 0.35 comes out at {wa['kg'] * 1000:.0f} g against Wolfspeed's 700 g.\n")
    w("| inductor | design point | AP cm4 | core kg | Cu kg | Cu loss at I_rms | USD cat / 5k (ours) | study's USD cat / 5k |")
    w("|---|---|---|---|---|---|---|---|")
    for tag, lab, st in (("A_L1", "A L1 97 uH, 216 A rms, 344 A pk", c4["study"]["L1"]), ("B_L1", "B L1 49 uH, same current", c4["study_B_L1"])):
        for dp, txt in (("moderate", "1.2 T, 3.5 A/mm2, k_u 0.45"), ("aggressive", "1.3 T, 4.5 A/mm2, k_u 0.50")):
            x = c4[tag][dp]
            w(f"| {lab} | {txt} | {x['ap_cm4']:.0f} | {x['core_kg']:.2f} | {x['cu_kg']:.2f} | {x['p_cu_W']:.0f} W | {x['usd_cat']:.0f} / {x['usd_5k']:.0f} | "
              f"{st[0]:.0f} / {st[1]:.0f} |")
    x = c4["L2"]
    w(f"| L2 15 uH (A and B) | Si-steel 1.4 T, 3.5 A/mm2 | {x['ap_cm4']:.0f} | {x['core_kg']:.2f} | {x['cu_kg']:.2f} | {x['p_cu_W']:.0f} W | "
      f"{x['usd_cat']:.0f} / {x['usd_5k']:.0f} | {c4['study']['L2'][0]:.0f} / {c4['study']['L2'][1]:.0f} |")
    bud = c4["A_L1_budget"]
    w(f"\nThe study's price model (10 USD + 6-8 USD per joule) under-prices these inductors by about 1.8-2.3 times: A's L1 alone "
      f"carries 2.0-2.5 kg of copper, 36-45 USD at the repo's own copper price, against 35.5 USD for the whole part at 5,000 units.  "
      f"The loss budget is also tight: the study allows {bud['copper_at_180A']:.0f} W copper at 180 A and {bud['core_and_gap_at_750V']:.0f} W "
      f"core per L1 ({bud['total_at_125kW_750V']:.0f} W in all); the moderate design above has about "
      f"{c4['A_L1']['moderate']['p_cu_W'] * (180 / 216) ** 2:.0f} W of DC copper loss at 180 A, and an amorphous core at 32 kHz with "
      f"A's ripple loses about {c4['A_core_W']:.0f} W (ESTIMATE, Metglas 2605SA1 loss fit quoted from memory) - meeting {bud['total_at_125kW_750V']:.0f} W "
      f"needs a lower current density and a lower-loss core (powder or nanocrystalline), i.e. a bigger and dearer part.\n")
    w("**Effect on the decision:** the values are believable and consistent with the reference; the prices are not.  Re-pricing "
      "hits A harder than B (A's L1 stores twice the energy) but by less than B's extra gate drive and devices cost - see the "
      f"corrected totals in claim 3.  It raises A's own figure by roughly {f0(ca['A'] - a_5k)}-{f0(cm['A'] - a_5k)} USD at 5,000 "
      f"units (to {ca['A'] / 125:.1f}-{cm['A'] / 125:.1f} USD/kW), which matters against the 6.6 USD/kW benchmark (SRC-5).\n")

    # ---------------------------------------------------------------- claim 5
    w("## Claim 5 - six TO-247-4 devices in parallel per switch at 32 kHz\n")
    w("Published guidance (application notes NOT on file; quoted from memory, to be filed when the gate-drive sheet is drawn): "
      "Wolfspeed's and Infineon's SiC paralleling notes and the IEEE literature (e.g. Li, Munk-Nielsen et al., 'Influences of "
      "device and circuit mismatches on paralleling silicon carbide MOSFETs', IEEE TPEL 2016) agree on the mechanisms: static "
      "sharing is self-correcting (positive R_DS(on) temperature coefficient); dynamic sharing is set by the spread of V_GS(th) "
      "and transconductance and by unequal common-source and power-loop inductance, and concentrates switching loss in the "
      "fastest device; paralleled gates can oscillate against each other through the shared gate node.  The remedies are "
      "schematic-level and layout-level: one driver (or buffer) per switch with an individual gate resistor per device, a small "
      "resistor in each Kelvin-source return to damp circulating currents, symmetric power and gate loops, devices from one lot "
      "or binned for V_GS(th), and a derating of the order of 10-20 %.  None of this is a showstopper for a schematic-level design; "
      "the risk sits in the layout and in the device spread, which this project cannot test.\n")
    w(f"On file: Wolfspeed parallels two C3M0075120K per switch in CRD-60DD12N; CRD-25BDA6512N-K, though not paralleled, shows "
      f"the per-device network to copy - turn-on 5.1 ohm, turn-off 5.1 ohm || (5.1 ohm + Schottky), 1 ohm in the Kelvin-source "
      f"return, 10 kohm + 1 nF gate-source at the device, Miller clamp straight to the gate (schematic sheet 2.x).\n")
    w(f"**The number that matters:** Sichain prints V_GS(th) {c5['vth'][0]:.1f} / {c5['vth'][1]:.1f} / {c5['vth'][2]:.1f} V "
      f"(min / typ / max) for SG2M040170HJ, with g_fs about {c5['gfs']:.0f} S - a 1.5 V window, so unbinned parts can differ by tens of "
      f"amperes during the transition.  The study takes the hottest of six at 1.10 x the mean current; at 1.30 (CALCULATED, the "
      f"study's model at its worst corner, {c5['corner']['vdc']:.0f} V, {c5['corner']['vll']:.0f} V AC, PF angle {c5['corner']['ang']:.0f}, 110 %; "
      f"applied to conduction and switching alike, so an upper bound) the worst junction rises from {c5[1.10]:.0f} C to {c5[1.30]:.0f} C at "
      f"45 C inlet and from {c5[(1.10, 'hot')]:.0f} C to {c5[(1.30, 'hot')]:.0f} C at 60 C inlet (limit {c5['lim']:.0f} C continuous).\n")
    w("What the schematic must carry for each paralleled device (most already in gen/gdrv.py's channel; check each against the "
      "PCS sheet when it is drawn): (1) its own R_G,on and R_G,off (diode-steered, as Wolfspeed); (2) a 0.5-1 ohm Kelvin-source "
      "resistor; (3) gate-source pull-down and a small gate-source capacitor at the device pins; (4) a Miller-clamp path per gate "
      "or per tight group; (5) the buffer stage sized for six gates (the study's 14.5 A peak need against the driver's 10 A); "
      "(6) local DC-link decoupling per device pair and the RC damper; (7) DESAT sensing on one device per switch with the blanking "
      "matched to the paralleled turn-on; (8) one temperature sensor per heat-sink section; (9) a BOM note: one lot per switch, "
      "V_GS(th) binned to +-0.25 V or a sharing test at incoming inspection.\n")
    w(f"**Effect on the decision:** no showstopper; a condition.  Without binning the 60 C-inlet derating of the study is "
      f"optimistic ({c5[(1.30, 'hot')]:.0f} C against {c5['lim']:.0f} C at 1.30 sharing).  B has the same issue with 5-6 devices per "
      f"position and twice the gate channels.\n")

    # ---------------------------------------------------------------- claim 6
    fl = c6["full"]
    w("## Claim 6 - calibration: the study's loss model against Wolfspeed's measured efficiency\n")
    w("The study's model (pcs_design.leg_losses, T-type table, data-sheet curves through pv_devices) applied to the Wolfspeed board: "
      "C3M0032120K outer (pv_devices' entry), C3M0025065K middle (data sheet, added in memory), one device per position, 60 kHz, "
      "min-max zero sequence (closest available to Wolfspeed's third-harmonic injection), the converter current including C22's, "
      f"junction temperatures scaled from Wolfspeed's Table 12 (ambient {T_AMB_WS:.0f} C ASSUMED).  Two variants: (a) as the study "
      "applies the model - energies at the data-sheet gate resistor; (b) energies scaled to the board's gate network with the "
      f"data sheets' E-vs-R_G figures (turn-on {WS_RG_ON:.1f} ohm, turn-off {WS_RG_OFF:.2f} ohm incl. the 1 ohm Kelvin resistor: "
      f"E_on x{lin(EON_RG_C32, WS_RG_ON) / lin(EON_RG_C32, 2.5):.2f}, E_off x{lin(EOFF_RG_C32, WS_RG_OFF) / lin(EOFF_RG_C32, 2.5):.2f} for "
      f"C3M0032120K).  'Measured loss' = P_out (100/eta - 1) from the efficiency column, MEASURED-BY-WOLFSPEED; the power columns "
      f"of the same table give up to {max(r['loss_p'] - r['loss_eta'] for r in c6['rows']):.0f} W more at full power (the table is "
      f"internally inconsistent by 0.1-0.2 % there).  DCR = 3 I^2 x 6.5 mOhm (BOM).\n")
    w("| V_dc / V_LL | P_out kW | measured loss W (eta / P columns) | model devices W (a) / (b) | inductor DCR W | left for core, damping, relays, PCB, caps W | devices + DCR as share of measured |")
    w("|---|---|---|---|---|---|---|")
    for x in c6["rows"]:
        if (x["vdc"], x["vll"]) in ((800, 400), (670, 400), (900, 400)) or x is fl:
            w(f"| {x['vdc']} / {x['vll']} | {x['p_out']:.2f} | {x['loss_eta']:.0f} / {x['loss_p']:.0f} | {x['dev_a']:.0f} / {x['dev_b']:.0f} | "
              f"{x['dcr']:.0f} | {x['rest_b']:.0f} | {100 * (x['dev_b'] + x['dcr']) / x['loss_eta']:.0f} % |")
    w(f"\n**Model against Wolfspeed's own device-loss figures** (25 kW, 800 V / 400 V): Wolfspeed CALCULATED 28.5 W per outer and "
      f"11.0 W per middle device from its MEASURED case temperatures (Table 12) - {c6['ws_dev_T12']:.0f} W for the 12 devices; the study's "
      f"model gives {fl['dev_a']:.0f} W as applied (a) and {fl['dev_b']:.0f} W with the board's gate resistors (b), split "
      f"{fl['T1_b']:.1f} W outer / {fl['T2_b']:.1f} W middle.  Wolfspeed's own simulation (Table 2 p13, 85 C heat sink) gives "
      f"{WS_T2['total_W']:.0f} W.  So the model's device total is within {100 * abs(fl['dev_b'] / c6['ws_dev_T12'] - 1):.0f} % of "
      f"Wolfspeed's when the gate resistors are honoured and {100 * (1 - fl['dev_a'] / c6['ws_dev_T12']):.0f} % low when they are not, "
      f"but it puts {100 * (1 - fl['T1_b'] / 28.5):.0f} % less in the outer device and {100 * (fl['T2_b'] / 11.0 - 1):.0f} % more in the "
      f"middle one than Wolfspeed does (the T-type's 400 V switching energy is scaled from 800 V curves by an ASSUMED exponent).\n")
    w(f"**Against the measurement**, devices + inductor DCR explain {100 * c6['share_full']:.0f} % of the measured loss at 25 kW; the "
      f"remaining {fl['rest_b']:.0f} W (about {100 * fl['rest_b'] / (fl['p_out'] * 1e3):.2f} % of the power) is everything the device model "
      f"does not cover - L core and AC-copper loss at 60 kHz, the 0.68 ohm damping resistors, relays and NTC bypass, PCB copper, "
      f"capacitor ESR.  It grows roughly in proportion to load (fit on the 800 V / 400 V series: {c6['rest_fit'][0]:.0f} W + "
      f"{c6['rest_fit'][1]:.1f} W per kW), i.e. it behaves like series resistance and ripple-driven loss, not like a fixed loss.  "
      f"Wolfspeed's measured efficiency cannot separate these; the board publishes no inductor data.\n")
    w(f"**Second reference not usable:** CRD200DA23N-GMA's Figure 50 (PRD-09623 section 5.2) plots efficiency against 'output power' "
      f"up to 200 kW, but the test ran 167 A rms at 300 Hz into a 129 uH load - about {c6['crd200_kvar']:.0f} kvar of reactive power "
      f"(CALCULATED) from a 30 kW supply - and the guide does not say how 'output power' was defined.  Not used.\n")
    w(f"**How far to trust A's 99.20 %:** (1) the device model is within about 10 % on Wolfspeed's board once the gate resistors "
      f"are honoured - but A's own run takes E_on at the data-sheet 2.5 ohm while its gate drive uses {pf['rg_on']:.2f} ohm: "
      f"correcting that adds {a35['dev_W_eon'] - a35['dev_W']:.0f} W at the 35 % peak point ({100 * (a35['dev_W_eon'] - a35['dev_W']) / a35['p_W']:.2f} "
      f"%-points) and {pf[('A', 750.0, 1.0)]['dev_W_eon'] - pf[('A', 750.0, 1.0)]['dev_W']:.0f} W at 125 kW / 750 V; (2) the passive part: "
      f"Wolfspeed's board loses about {100 * fl['rest_b'] / (fl['p_out'] * 1e3):.1f} % of its power outside the devices and DCR, A's "
      f"budget allows about 0.3 % for filter, contactors, fuses and busbars together, and claim 4 shows the L1 budget is "
      f"optimistic.  Read the {100 * pk:.2f} % as about {100 * pk - de_pk - 0.2:.1f}-{100 * pk - de_pk:.1f} % and the "
      f"{100 * fl_eta:.2f} % full-load figure as about {100 * fl_eta - de_fl - 0.2:.1f}-{100 * fl_eta - de_fl:.1f} % (ESTIMATE: the E_on "
      f"correction plus up to 0.2 %-point of passive loss); the requirement (maximum efficiency >= 98.5 %) stays met, the "
      f"full-load figure is not a requirement.\n")

    # ---------------------------------------------------------------- changes
    w("## What to change in the PCS design\n")
    for i, t in enumerate([
        f"Re-price L1 and L2 from a mass-based design (MAG-1/MAG-2 deliverables): the 10 USD + 6-8 USD/J model is about half the "
        f"material cost; A's 5,000-unit figure rises to about {f0(ca['A'])}-{f0(cm['A'])} USD ({ca['A'] / 125:.1f}-{cm['A'] / 125:.1f} USD/kW).  "
        f"Report it against the 6.6 USD/kW benchmark (SRC-5) rather than the {f0(a_5k)} USD now in D-053.",
        f"Design L1 to its loss budget before trusting the efficiency: an amorphous C-core at 32 kHz with 25 % ripple loses about "
        f"{c4['A_core_W']:.0f} W in the core alone against a {c4['A_L1_budget']['total_at_125kW_750V']:.0f} W total budget; try powder or "
        f"nanocrystalline cores, or a higher L1 for less ripple, and restate the budget.",
        f"Scale E_on to the chosen R_G,on in the efficiency and junction-temperature runs (data-sheet curve: x{pf['eon_factor']['SG2M040170HJ']:.2f} "
        f"at {pf['rg_on']:.2f} ohm), or lower R_G,on if the commutation simulation allows; A's peak drops about "
        f"{100 * (a35['dev_W_eon'] - a35['dev_W']) / a35['p_W']:.1f} %-point.",
        f"Write the reactive-power requirement explicitly in REQUIREMENTS AC-02: full current at PF 0 (what the competitor's "
        f"'PF -1~+1' with 137 kVA continuous implies) or the grid-code range (about 0.44 S).  It sizes any three-level DC link "
        f"(claim 1: {c1['C_pf0'][600.0]['C_216A_60V'] * 1e3:.1f} mF against {max(c1['grid'].values()) * 1e3:.1f} mF per half) and A's own thermal "
        f"corners.",
        "Restate D-053's reasons: the 3.5 mF applies to carrier-based midpoint control at full current, PF 0 and 600 V, not to any "
        f"three-level stage; the cosmic-ray case is a FIT budget (B {c2['unit']['B']['profile']:.0f}-{c2['unit']['B']['950']:.0f} FIT on "
        f"Wolfspeed Gen 3 data, A {c2['unit']['A']['950']:.2f}) plus the absence of any Chinese maker's data, not the 0.67 rule.",
        "Put V_GS(th) binning (or one lot per switch plus an incoming sharing test) and a 0.5-1 ohm Kelvin-source resistor per "
        "device on the PCS gate-drive sheet; re-run the 60 C-inlet derating with a 1.3 sharing factor for switching loss.",
        "Copy Wolfspeed's per-device gate network (diode-steered turn-off, Kelvin resistor, 10 k + 1 nF at the pins) and, for any "
        "T-type variant, its common-drain middle switch: 5 isolated bias supplies for 12 channels.",
        "Keep B documented as the fallback with its preconditions: a firmware two-level (or double-signal PWM) mode for the "
        "low-PF corner, 1200 V middle devices, a T-type state interlock, and cosmic-ray data from the SiC supplier (RFQ question "
        f"to Sichain and the others).  Revisit if efficiency is ever sold: B saves about "
        f"{100 * gain(750.0, 1.0, 'dev_W_eon') / 124.7e3:.1f} %-point at full load.",
        f"Keep A's 5 + 5 film bank: per ampere of phase current it matches Wolfspeed's two-level CRD200DA23N-GMA "
        f"({c1['twol']['A_caps_per_A']:.3f} against {c1['twol']['crd200_caps_per_A']:.3f} capacitors per A, both ripple-current sized; "
        f"A's capacitors run at {c1['twol']['A_I_cap_A']:.0f} of {c1['twol']['A_I_cap_max']:.1f} A) - it is not oversized; Wolfspeed's 0.81 uF/kW is "
        f"lower only because its 1.1 kV capacitors carry the same current with less capacitance.",
        "Fix the Wolfspeed rows of sim/data/reference_magnetics.csv when its choke data arrive (core, turns, wire are offered on "
        "request through Wolfspeed's forum, UG p15); until then the AP method above is calibrated on its 700 g only."], 1):
        w(f"{i}. {t}")
    w("\n## Not verified / unknown\n")
    w("- Every number of ours is CALCULATED or ESTIMATED; nothing is bench-validated.  Wolfspeed's numbers are its own (labelled).\n"
      "- Standards clauses (IEEE 1547-2018 reactive capability, EN 50549-1, GB/T 34120), the double-signal / virtual-vector PWM "
      "costs, the paralleling application notes, the amorphous loss fit and inverter field failure rates are quoted from memory.\n"
      "- The LFP voltage profile, the die areas, the 650 V voltage-fraction scaling and all inductor material prices are assumptions.\n"
      "- B's HF ripple current per DC-link half was not computed (taken as A's 5 per half); the two-level fallback's L1 core loss "
      "and its control were not simulated.\n"
      "- Wolfspeed's firmware (dead time, modulation details, which trips act in hardware) is not on file; the L1/L5 alternate "
      "footprints and the C21 value (schematic 2.2 uF, BOM 0.1 uF) are read as noted.\n")
    return L


# ============================================================================================ self-check and main
def self_check(c1, c2, c3, c4, c5, c6):
    # midpoint: textbook sinusoidal-PWM swings, ordering best <= min-max, and the C formula
    assert abs(np_qpp(0.8, math.pi / 2, "spwm") / (0.5 * 0.8) - 1) < 0.01
    assert abs(np_qpp(0.8, 0.0, "spwm") / (0.342 * 0.8) - 1) < 0.01
    for r in c1["rows"]:
        assert r["best"] <= r["minmax"] + 1e-6, r
    assert np_qpp(0.6, 0.0) < 1e-3                                   # PF 1: a zero sequence cancels it
    assert 3.0e-3 < c1["C_pf0"][600.0]["C_216A_60V"] < 4.2e-3        # the study's 3.5 mF, reproduced within ~15 %
    # cosmic: our curve reading agrees with the study's within 25 % at 850-950 V; the IGBT T-type reproduces ~104 FIT
    for v in (850.0, 900.0, 950.0):
        x = c2["cm2"][v]
        assert abs(x["gen3"] / x["study_gen3"] - 1) < 0.25, (v, x)
    assert 90 < c2["unit"]["IGBT-TT"]["950"] < 120
    assert c2["unit"]["A"]["950"] < 1.0 < c2["unit"]["B"]["profile"] < c2["unit"]["B"]["950"]
    # cost: A's CSV reproduces the study's total; B rows are A's basis plus the changed lines
    assert abs(c3["A_check"][1] - c3["A"][1]) < 0.5 and abs(c3["A_check"][0] - c3["A"][0]) < 0.5
    ns = [v["n_half"] for v in c3["variants"].values()]
    assert ns[0] >= ns[1] >= ns[2] >= ns[3] == SPEC["dc_link"]["per_half"]
    # filter: the area-product method reproduces Wolfspeed's 700 g choke within 25 %; per-unit ripple rules agree
    assert abs(c4["ws_ap"]["kg"] * 1000 / WS_CHOKE_G - 1) < 0.25
    assert abs(c4["A"]["ripple_of_pk"] - c4["ws"]["ripple_of_pk"]) < 0.03
    # calibration: model devices + DCR stay below every measured loss (the rest is the passives); all 35 points read
    assert len(c6["rows"]) == sum(len(v) for v in WS_ETA.values()) == 35
    assert all(r["rest_b"] > 0 for r in c6["rows"])
    assert abs(c6["full"]["dev_b"] / c6["ws_dev_T12"] - 1) < 0.15
    assert c5[1.30] > c5[1.10]
    return True


def main():
    c1 = claim1_cache()
    c2, c3, c4, c5, c6 = claim2(), claim3(), claim4(), claim5(), claim6()
    self_check(c1, c2, c3, c4, c5, c6)
    with open(OUT, "w") as fh:
        fh.write("\n".join(report(c1, c2, c3, c4, c5, c6)) + "\n")
    print(f"wrote {os.path.relpath(OUT, ROOT)}; pcs_crosscheck self-check passed")


if __name__ == "__main__":
    main()
