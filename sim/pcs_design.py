"""PCS-P125 three-phase bidirectional battery inverter - power-stage design (REQUIREMENTS AC-01..03, SRC-6).

Run:  .venv/bin/python sim/pcs_design.py
Out:  sim/out/pcs_design/{pcs_spec.json, report.md, *.csv}, ngspice decks sim/spice/pcs_*.cir
Device data: sim/pcs_devices.py (IGBTs, SiC Schottky, cosmic-ray data, prices) + sim/pv_devices.py (SiC MOSFETs).
Every value here is CALCULATED (datasheet-based averaged loss model, analytic filter/thermal models, ngspice transients);
nothing is measured or bench-validated.  Steps (in the order the script runs them):
  a  topology / device screen incl. the cosmic-ray failure rate     b  loss + thermal map over the envelope
  c  switching frequency + LCL filter, harmonic spectrum, THDi       d  DC link
  e  commutation (ngspice), gate drive, dead time, short circuit     f  AC / DC port, common mode and leakage
  g  cost (catalogue and 5,000 units)                                then: hand-over lists, report, self-check
"""
import csv
import json
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pcs_devices as dv  # noqa: E402
import pv_devices as pv  # noqa: E402
import pv_tradeoff as tr  # noqa: E402  (ngspice runner, heatsink model)

OUT = os.path.join(HERE, "out", "pcs_design")
SPEC, REP = {}, []          # pcs_spec.json content, report.md lines (filled step by step)
TRADEOFF = os.path.join(OUT, "tradeoff.json")


def filter_parts():
    """the best two-level filter of sim/pcs_tradeoff.py - L1 / L2 / AC CM choke designed and priced by sim/magnetics.py, loss per phase
    = a(V_dc) + k2 I^2.  None before that script has run: the step-c budgets and the architect's estimates stand in (report says so)"""
    try:
        t = json.load(open(TRADEOFF))
        return next(r for r in t["rows"] if r["name"] == t["two_level_best"])
    except (OSError, KeyError, StopIteration, ValueError, TypeError):
        return None


FP = filter_parts()
CORE_WORD = {"amor": "amorphous", "nano": "nanocrystalline"}
MOD_2L = FP["set"]["zs"] if FP else "policy"   # two-level modulation: 'minmax' = SVPWM-equivalent everywhere if sim/pcs_tradeoff.py chose it

# ------------------------------------------------------------------------------------------------ product (AC-02)
P_RATED, P_DC_MAX, S_MAX = 125e3, 137e3, 150e3
V_LL, V_TOL, F_GRID = 400.0, 0.15, 50.0
I_RATED, I_CONT, I_2MIN = 180.0, 198.0, 216.0        # A rms: rated, 110 % continuous, 120 % for 2 min (AC-02)
I_200MS = 1.2 * I_2MIN                               # > 120 % for 200 ms: current limit 1.2 x I_max (ARCHITECTURE-PCS section 5)
VDC = {"3W": (590.0, 950.0, 600.0, 900.0), "4W": (650.0, 950.0, 680.0, 900.0)}   # operating min/max, full-load min/max
I_DC_MAX, I_DC_CONT = 250.0, 230.0
T_IN, T_IN_HOT = 45.0, 60.0                          # inlet air: full power to 45 C, derate above (AC-02)
ETA_REQ = 0.985                                      # maximum efficiency >= 98.5 % (AC-02)

# ------------------------------------------------------------------------------------------------ design rules (stated once)
K_SHARE = {"sic": 1.10, "igbt": 1.15, "sbd": 1.10}   # hottest / mean current of paralleled discretes (see report b)
TJ_LIM = {   # (continuous at 110 % / 45 C inlet, 120 % 2-min treated as steady state)
    "sic": (150.0, 165.0), "sbd": (150.0, 165.0),
    "CRG40T120AK3SD": (125.0, 150.0), "CRG40T120BK3SD": (125.0, 145.0), "CRG50T60AK3SD": (125.0, 150.0),
    "NCE80TD65BT": (125.0, 145.0), "NCE40TD120VT": (150.0, 165.0), "HCG400FL120E3RA": (125.0, 145.0), "HCG375FL065E3RC": (125.0, 145.0)}
T_DEAD = {"sic": 250e-9, "igbt": 1.2e-6}             # s at the gates (sic: gen/gdrv class; IGBT: 1.0-1.5 us, ARCHITECTURE-PCS section 2)
# case-to-sink per TO-247: Al2O3 0.635 mm (k 25 W/mK, handbook) over the 15.5 x 20 mm tab + two 50 um grease lines (k 2.5) + 0.05 K/W
# contact (pv_tradeoff.PAD method; IGBT and SiC loss densities here allow alumina instead of the PV's AlN)
R_CS = 0.635e-3 / (25.0 * 3.1e-4) + 2 * 50e-6 / (2.5 * 3.1e-4) + 0.05
R_SPREAD = tr.RTH_SPREAD
GATE_CH = {"cat": (2.978 + 1.60 + 3.93 / 4), "5k": (1.544 + 1.36 + 3.37 / 4)}   # USD per NSI6651 channel (costfirst_pcs_bom.csv rows)
GATE_PER_DEV = {"cat": 0.10, "5k": 0.08}             # extra gate/Kelvin resistor per paralleled device beyond the first (ESTIMATE)
PAD = {"cat": 0.40, "5k": 0.30}                      # Al2O3 pad + clip + grease per device (costfirst_pcs_bom.csv)
L1_COST = (10.0, 6.0)                                # USD = 10 + 6 x (1/2 L I_pk^2 in J): the architect's L1 estimate (screen only; step c refines)
RIPPLE_PP = 0.25                                     # L1 ripple peak-to-peak / I_pk at 216 A (architect's sizing rule, screen)
FSW_CHOICE = FP["fsw_kHz"] * 1e3 if FP else 32e3      # sim/pcs_tradeoff.py's choice (section h); D-053's 32 kHz before it has run
MOD_3W = "minmax"     # three-LEVEL legs: min-max zero sequence everywhere (+ midpoint-balance offset) - a three-level midpoint needs it (step d)


def zs_policy(levels, m):
    """modulation policy: three-level -> min-max zero sequence always (midpoint); two-level -> sinusoidal PWM wherever it reaches
    (m <= 0.98: no 150 Hz common-mode voltage on a three-wire TN connection, step f), min-max zero sequence only above"""
    if levels == 3 or MOD_2L == "minmax":
        return "minmax"
    return "none" if m <= 0.98 else "minmax"


def wr(*a):
    REP.append(" ".join(str(x) for x in a))


# ------------------------------------------------------------------------------------------------ operating point
def op_point(vdc, v_ll, i_rms, ang_deg, l_tot, f=F_GRID):
    """grid-side current I at angle ang (0 = inverter at PF 1, 180 = rectifier at PF 1, +90 = current lagging the grid voltage).
    Converter phase voltage V_c = V_g + j w L_tot I.  Returns modulation index m = V_c,pk / (V_dc/2), the angle between converter
    voltage and current, and the converter-side P/Q."""
    vg = v_ll / math.sqrt(3.0)
    i = i_rms * complex(math.cos(math.radians(-ang_deg)), math.sin(math.radians(-ang_deg)))
    vc = vg + 1j * 2 * math.pi * f * l_tot * i
    m = abs(vc) * math.sqrt(2.0) / (vdc / 2.0)
    phi = math.atan2(vc.imag, vc.real) - math.atan2(i.imag, i.real)
    return m, phi, 3 * (vg * i.conjugate()).real, 3 * (vg * i.conjugate()).imag


def references(m, zs, n=720):
    """per-phase reference (in units of V_dc/2) over one fundamental period for phase a; zs: 'none' or 'minmax' (SVPWM-equivalent)"""
    th = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    abc = np.stack([m * np.sin(th - k * 2 * np.pi / 3) for k in range(3)])
    return th, abc[0] + zero_seq(abc, zs)


def zero_seq(abc, zs):
    """zero sequence added to the three references: 'none', 'minmax' (SVPWM-equivalent) or 'dpwm1' (the largest-magnitude phase
    clamped to its rail for 60 deg around its peak - the reference set of NSPWM / DPWM1)"""
    if zs == "minmax":
        return -0.5 * (abc.max(0) + abc.min(0))
    if zs == "dpwm1":
        k = np.argmax(np.abs(abc), 0)
        big = abc[k, np.arange(abc.shape[1])]
        return np.sign(big) - big
    return 0.0 * abc[0]


# ------------------------------------------------------------------------------------------------ device primitives
def kind(part):
    if part in dv.IGBTS or part in dv.MODULES:
        return "igbt"
    if part in dv.SBD:
        return "sbd"
    return "sic"


def dev(part):
    return dv.IGBTS.get(part) or dv.MODULES.get(part) or dv.SBD.get(part) or pv.MOSFETS[part]


def cond_w(part, elem, i_dev, tj, i_rip=0.0):
    """instantaneous conduction power of ONE device carrying i_dev (>= 0) in element 'sw' (forward) or 'rev' (reverse / diode);
    i_rip = rms of the L1 ripple it carries (SiC channel: adds R_DS(on) i_rip^2; IGBT / diode forward drops: neglected).
    Returns (w_switch_chip, w_diode_chip)"""
    k, d = kind(part), dev(part)
    if k == "igbt":
        return (dv.vce(d, i_dev, tj) * i_dev, 0.0 * i_dev) if elem == "sw" else (0.0 * i_dev, dv.vf(d, i_dev, tj) * i_dev)
    if k == "sbd":
        return 0.0 * i_dev, dv.vf_sbd(d, i_dev, tj) * i_dev
    return pv.rds(d, tj) * (i_dev ** 2 + i_rip ** 2), 0.0 * i_dev     # SiC: channel in both directions (synchronous rectification)


def sw_energy(part, role, v, i_dev, tj, di=0.0):
    """energy per event for ONE device: role 'hard' (turn-on + turn-off of the switching device, i.e. one period's pair) or
    'rec' (recovery of the passive device).  SiC: pv_devices convention E_on + E_off + E_rr - E_oss split as
    hard = E_on + E_off - E_oss, rec = E_rr (K_RR x Q_rr x V).  SBD: capacitive only, its E_C is inside the switch's E_on (0 here).
    di = peak-to-peak L1 ripple of the device's share: turn-on and recovery happen at the current valley (i - di/2, >= 0), turn-off at
    the peak (i + di/2).  E_on carries EON_MULT (data-sheet R_G -> drawn R_G,on), E_off EOFF_MULT (step e)."""
    k, d = kind(part), dev(part)
    i_lo, i_hi = np.maximum(i_dev - di / 2, 0.0), i_dev + di / 2
    if k == "igbt":
        if role == "hard":
            return dv.e_igbt(d, "on", v, i_lo, tj) + dv.e_igbt(d, "off", v, i_hi, tj), 0.0
        return 0.0, dv.e_igbt(d, "rec", v, i_lo, tj)
    if k == "sbd":
        return 0.0, 0.0
    if role == "hard":
        e = (pv.e_sw(d, "on", v, i_lo, tj) * EON_MULT["value"] + pv.e_sw(d, "off", v, i_hi, tj) * EOFF_MULT["value"] - pv.eoss(d, v))
        return np.maximum(e, 0.0), 0.0
    return pv.e_sw(d, "rr", v, i_lo, tj), 0.0


# ------------------------------------------------------------------------------------------------ topologies
# conduction table: state -> {current sign: [(position, element)]};  commutation table: (half, sign) -> (hard position, recovering position)
# current i > 0 = out of the leg into the grid.  T-type: T1/T4 outer, T2/T3 inner anti-series (T2 forward for i > 0).
# NPC1: T1..T4 in series, D5/D6 clamp diodes.  ANPC (TIDA-010210 scheme): T2/T3 line-frequency, T1/T5 (T4/T6) switch at f_sw.
TOPO = {
    "TT": {"pos": ("T1", "T2", "T3", "T4"), "vcomm": 0.5, "levels": 3,
           "cond": {"P": {1: [("T1", "sw")], -1: [("T1", "rev")]},
                    "O": {1: [("T2", "sw"), ("T3", "rev")], -1: [("T3", "sw"), ("T2", "rev")]},
                    "N": {1: [("T4", "rev")], -1: [("T4", "sw")]}},
           "comm": {("P", 1): ("T1", "T3"), ("P", -1): ("T3", "T1"), ("N", -1): ("T4", "T2"), ("N", 1): ("T2", "T4")},
           "block": {"T1": 1.0, "T4": 1.0, "T2": 0.5, "T3": 0.5}},        # largest steady blocking voltage / V_dc
    "NPC": {"pos": ("T1", "T2", "T3", "T4", "D5", "D6"), "vcomm": 0.5, "levels": 3,
            "cond": {"P": {1: [("T1", "sw"), ("T2", "sw")], -1: [("T1", "rev"), ("T2", "rev")]},
                     "O": {1: [("D5", "rev"), ("T2", "sw")], -1: [("T3", "sw"), ("D6", "rev")]},
                     "N": {1: [("T3", "rev"), ("T4", "rev")], -1: [("T3", "sw"), ("T4", "sw")]}},
            "comm": {("P", 1): ("T1", "D5"), ("P", -1): ("T3", "T1"), ("N", -1): ("T4", "D6"), ("N", 1): ("T2", "T4")},
            "block": {p: 0.5 for p in ("T1", "T2", "T3", "T4", "D5", "D6")}},
    "ANPC": {"pos": ("T1", "T2", "T3", "T4", "T5", "T6"), "vcomm": 0.5, "levels": 3,
             "cond": {"P": {1: [("T1", "sw"), ("T2", "sw")], -1: [("T1", "rev"), ("T2", "rev")]},
                      "Op": {1: [("T5", "sw"), ("T2", "sw")], -1: [("T5", "rev"), ("T2", "rev")]},
                      "On": {1: [("T6", "rev"), ("T3", "rev")], -1: [("T6", "sw"), ("T3", "sw")]},
                      "N": {1: [("T4", "rev"), ("T3", "rev")], -1: [("T4", "sw"), ("T3", "sw")]}},
             "comm": {("P", 1): ("T1", "T5"), ("P", -1): ("T5", "T1"), ("N", -1): ("T4", "T6"), ("N", 1): ("T6", "T4")},
             "block": {p: 0.5 for p in ("T1", "T2", "T3", "T4", "T5", "T6")}},
    "2L": {"pos": ("TH", "TL"), "vcomm": 1.0, "levels": 2,
           "cond": {"P": {1: [("TH", "sw")], -1: [("TH", "rev")]}, "N": {1: [("TL", "rev")], -1: [("TL", "sw")]}},
           "comm": {("P", 1): ("TH", "TL"), ("P", -1): ("TL", "TH")}, "block": {"TH": 1.0, "TL": 1.0}},
}


def leg_losses(topo, devs, fsw, vdc, m, phi, i_rms, zs, tj, n_th=360, l1=None):
    """averaged losses of ONE phase leg over a fundamental period.  devs: {pos: (part, n)}; tj: {pos: (Tj switch chip, Tj diode chip)}.
    l1 given: the L1 ripple (C_f star on the DC midpoint, so each L1 sees its own leg) enters conduction (rms) and switching (valley /
    peak current); clamped intervals (|ref| = 1, DPWM1) do not switch.
    Returns ({pos: {'sw': W, 'd': W (hottest device: k_share x mean current), 'tot': W of the whole position (mean devices)}}, leg W)."""
    t = TOPO[topo]
    th, vref = references(m, zs, n_th)
    i = i_rms * math.sqrt(2.0) * np.sin(th - phi)
    sgn = np.where(i >= 0, 1, -1)
    ai = np.abs(i)
    vsw = t["vcomm"] * vdc
    ar = np.minimum(np.abs(vref), 1.0)
    rip = (np.zeros(n_th) if not l1 else vdc * (1 - ar ** 2) / (4 * fsw * l1) if t["levels"] == 2 else
           vdc * ar * (1 - ar) / (2 * fsw * l1))                                # leg ripple, A peak-to-peak
    live = ar < 0.999                                                            # the leg switches in this interval
    if t["levels"] == 3:
        dpos, dneg = np.clip(vref, 0, 1), np.clip(-vref, 0, 1)
        if topo == "ANPC":
            states = (("P", dpos), ("Op", np.where(vref >= 0, 1 - dpos, 0.0)), ("On", np.where(vref < 0, 1 - dneg, 0.0)), ("N", dneg))
        else:
            states = (("P", dpos), ("O", np.where(vref >= 0, 1 - dpos, 1 - dneg)), ("N", dneg))
        halves = (("P", live & (vref >= 0)), ("N", live & (vref < 0)))
    else:
        d = np.clip(0.5 * (1 + vref), 0, 1)
        states, halves = (("P", d), ("N", 1 - d)), (("P", live),)

    def per_device(share):
        acc = {p: [np.zeros(n_th), np.zeros(n_th)] for p in t["pos"]}      # W per device (switch chip, diode chip)
        for state, frac in states:
            for s in (1, -1):
                sel = (sgn == s) & (frac > 0)
                if not sel.any():
                    continue
                for pos, elem in t["cond"][state][s]:
                    part, n = devs[pos]
                    k_ = (K_SHARE[kind(part)] if share else 1.0) / n
                    w_s, w_d = cond_w(part, elem, k_ * ai[sel], tj[pos][0] if elem == "sw" or kind(part) == "sic" else tj[pos][1],
                                      k_ * rip[sel] / math.sqrt(12.0))
                    acc[pos][0][sel] += frac[sel] * w_s
                    acc[pos][1][sel] += frac[sel] * w_d
        for half, hm in halves:
            for s in (1, -1):
                sel = hm & (sgn == s)
                if not sel.any():
                    continue
                hard, rec = t["comm"][(half, s)]
                for pos, role in ((hard, "hard"), (rec, "rec")):
                    part, n = devs[pos]
                    k_ = (K_SHARE[kind(part)] if share else 1.0) / n
                    e_s, e_d = sw_energy(part, role, vsw, k_ * ai[sel], tj[pos][0] if role == "hard" or kind(part) == "sic" else tj[pos][1],
                                         k_ * rip[sel])
                    acc[pos][0][sel] += fsw * e_s
                    acc[pos][1][sel] += fsw * e_d
                part, n = devs[rec]          # dead-time conduction of the recovering SiC device's body diode (2 dead times per period)
                if kind(part) == "sic":
                    d_ = dev(part)
                    idev = (K_SHARE["sic"] if share else 1.0) * ai[sel] / n
                    acc[rec][0][sel] += 2 * T_DEAD["sic"] * fsw * np.maximum(pv.vsd(d_, idev) - pv.rds(d_, tj[rec][0]) * idev, 0) * idev
        return {p: (float(a[0].mean()), float(a[1].mean())) for p, a in acc.items()}

    hot, mean = per_device(True), per_device(False)
    res = {p: {"sw": hot[p][0], "d": hot[p][1], "tot": devs[p][1] * sum(mean[p])} for p in t["pos"]}
    return res, sum(r["tot"] for r in res.values())


# transient: junction-case tau ~30 ms (TO-247 die + solder, ASSUMED: the data sheets give Z_th curves, not Foster terms, for these parts);
# case-sink tau = (R_cs + R_spread) x (Cu tab 2.3 g x 0.385 + Al2O3 pad 0.9 g x 0.78 J/gK) = 0.34 K/W x 1.6 J/K = 0.55 s (calculated)
TAU_JC, TAU_CS = 0.030, (R_CS + R_SPREAD) * (2.3 * 0.385 + 0.9 * 0.78)


def device_tj(part, w_sw, w_d, t_hs, t_pulse=None):
    """junction temperatures (switch chip, diode chip) of one TO-247 device on the heatsink at t_hs; t_pulse = s of a load step from
    t_hs-equilibrium (None = steady state)"""
    k, d = kind(part), dev(part)
    a_jc, a_cs = (1.0, 1.0) if t_pulse is None else (1 - math.exp(-t_pulse / TAU_JC), 1 - math.exp(-t_pulse / TAU_CS))
    if d.get("module"):          # R_thJH already includes the grease; 0.01 K/W per chip for base spreading (ASSUMED); module tau ~1 s
        a_jc = 1.0 if t_pulse is None else 1 - math.exp(-t_pulse / 0.15)
        base = t_hs + (w_sw + w_d) * 0.01
        return base + w_sw * d["rth_jc"] * a_jc, base + w_d * d["rth_jc_d"] * a_jc
    base = t_hs + (w_sw + w_d) * (R_CS + R_SPREAD) * a_cs
    if k == "igbt":
        return base + w_sw * d["rth_jc"] * a_jc, base + w_d * d["rth_jc_d"] * a_jc
    rth = d.get("rth_jc_max") or d["rth_jc"]
    return base + (w_sw + w_d) * rth * a_jc, base + (w_sw + w_d) * rth * a_jc


# ------------------------------------------------------------------------------------------------ heatsink + fans
# One plate-fin extrusion section per phase leg (150 mm wide, pv_tradeoff.heatsink_rth: Shah & London developing laminar flow,
# fin efficiency, effectiveness-NTU), fans pushing through the fin channels.  Delta AFB1224SHE-F00 (thermal/AFB1224SHE-F00.pdf):
# 24 V, 12 W (18 W max), 258 m3/h free air, 142 Pa shut-off (p1); P-Q curve p5 read at 0.6 m3/min steps; operating -10..+60 C (p3).
FAN = {"part": "AFB1224SHE-F00", "mfr": "Delta Electronics (TW)", "w": 12.0, "w_max": 18.0,
       "pq": [(0, 142.0), (36, 118.0), (72, 92.0), (108, 66.0), (144, 52.0), (180, 42.0), (216, 24.0), (252, 0.0)],
       "t_op": (-10.0, 60.0), "life": "70,000 h at 40 C (p2)", "price": {"cat": 13.15, "5k": 9.24}}
HS = {"n_fin": 24, "h_fin": 0.060, "t_fin": 1.5e-3, "length": 0.40, "fans_per_section": 1}   # one fan per section: the chosen design keeps
# >= 29 K margin at its sizing corners with one (34 K with two) - step b; the screen uses the same section for every candidate


def fan_flow(nfan, hs=HS):
    """operating point of nfan fans in parallel on one section: P_fan(Q / nfan) = dP_hs(Q) (+20 % for inlet/outlet grilles, ESTIMATE)"""
    qs = np.linspace(1.0, nfan * FAN["pq"][-1][0] - 1, 400)
    pf = np.interp(qs / nfan, [p[0] for p in FAN["pq"]], [p[1] for p in FAN["pq"]])
    ph = np.array([1.2 * tr.heatsink_dp(q, n_fin=hs["n_fin"], h_fin=hs["h_fin"], t_fin=hs["t_fin"], length=hs["length"]) for q in qs])
    k = int(np.argmin(np.abs(pf - ph)))
    return float(qs[k]), float(ph[k])


def section_rth(hs=HS):
    q, dp = fan_flow(hs["fans_per_section"], hs)
    r_conv, r_air, vel = tr.heatsink_rth(n_fin=hs["n_fin"], h_fin=hs["h_fin"], t_fin=hs["t_fin"], length=hs["length"], flow_m3h=q)
    return {"flow_m3h": q, "dp_Pa": dp, "r_sa": r_conv + r_air, "r_air": r_air, "vel": vel}


# ------------------------------------------------------------------------------------------------ design evaluation
def evaluate(cand, vdc, v_ll, i_rms, ang, t_in, fsw=None, zs=None, l_tot=None, n_iter=40):
    """one operating point of a candidate: leg losses with Tj iteration on the section heatsink.  Returns dict."""
    topo, devs = cand["topo"], cand["devs"]
    fsw = fsw or cand["fsw"]
    l_tot = l_tot if l_tot is not None else cand.get("l_tot", 130e-6)
    m, phi, p, q = op_point(vdc, v_ll, i_rms, ang, l_tot)
    if zs is None:
        zs = zs_policy(TOPO[topo]["levels"], m)
    mmax = 0.98 if zs == "none" else 0.98 * 2 / math.sqrt(3)
    hs = cand.get("hs_r") or section_rth()
    tj = {pos: (t_in + 40.0, t_in + 40.0) for pos in TOPO[topo]["pos"]}
    runaway = False
    for _ in range(n_iter):            # fixed point Tj -> losses -> Tj; > 250 C = thermal runaway (no steady state)
        res, tot = leg_losses(topo, devs, fsw, vdc, m, phi, i_rms, zs, tj, l1=cand.get("l1"))
        t_hs = t_in + tot * hs["r_sa"]
        new = {pos: device_tj(devs[pos][0], res[pos]["sw"], res[pos]["d"], t_hs) for pos in TOPO[topo]["pos"]}
        delta = max(abs(new[p][k] - tj[p][k]) for p in new for k in (0, 1))
        tj = new
        if max(max(v) for v in tj.values()) > 250.0:
            runaway = True
            break
        if delta < 0.2:
            break
    return {"m": m, "phi": phi, "p": p, "q": q, "zs": zs, "feasible": m <= mmax, "res": res, "leg_w": tot, "t_hs": t_hs, "tj": tj,
            "tj_max": max(max(v) for v in tj.values()), "runaway": runaway}


def tj_ok(cand, ev, overload=False):
    """per position: margin to its Tj limit (negative = violated)"""
    out = {}
    for pos, (part, n) in cand["devs"].items():
        lim = (TJ_LIM[part] if part in TJ_LIM else TJ_LIM[kind(part)])[1 if overload else 0]
        out[pos] = lim - max(ev["tj"][pos])
    return out


SIZING_CORNERS = [(vdc, ang) for vdc in (600.0, 750.0, 900.0) for ang in (0.0, 180.0, 90.0, -90.0, 45.0, 135.0)]


def hs_tau(hs=HS, r_sa=None):
    vol = 0.010 * 0.150 * hs["length"] + hs["n_fin"] * hs["t_fin"] * hs["h_fin"] * hs["length"]
    return (r_sa or section_rth(hs)["r_sa"]) * vol * 2700.0 * 900.0


def overload_tj(cand, vdc, ang, t_in=T_IN, v_ll=V_LL):
    """(Tj after 120 % for 2 min, Tj after 1.2 x I_max for 200 ms), both from the 110 % steady state: heatsink first-order RC
    (tau = R_sa x C_section), devices at their steady rise for the 2-min step and on the two-time-constant transient for 200 ms.
    Losses are taken from the steady evaluation at the overload current (hotter -> conservative).  Returns per-position dicts."""
    e110 = evaluate(cand, vdc, v_ll, I_CONT, ang, t_in)
    e120 = evaluate(cand, vdc, v_ll, I_2MIN, ang, t_in)
    e200 = evaluate(cand, vdc, v_ll, I_200MS, ang, t_in)
    if not (e110["feasible"] and e120["feasible"] and e200["feasible"]):
        return None
    tau = hs_tau(r_sa=cand["hs_r"]["r_sa"])
    ths2 = e110["t_hs"] + (e120["t_hs"] - e110["t_hs"]) * (1 - math.exp(-120.0 / tau))
    t2 = {p: device_tj(cand["devs"][p][0], e120["res"][p]["sw"], e120["res"][p]["d"], ths2) for p in e120["res"]}
    t200 = {}
    for p in e200["res"]:
        part = cand["devs"][p][0]
        hot = device_tj(part, e200["res"][p]["sw"], e200["res"][p]["d"], 0.0, t_pulse=0.2)
        base = device_tj(part, e110["res"][p]["sw"], e110["res"][p]["d"], e110["t_hs"])
        pre = device_tj(part, e110["res"][p]["sw"], e110["res"][p]["d"], 0.0, t_pulse=0.2)
        t200[p] = tuple(b + h - q for b, h, q in zip(base, hot, pre))      # superposition of the step on the 110 % state
    return {"e110": e110, "tj_2min": t2, "tj_200ms": t200, "t_hs_2min": ths2}


def tj_abs_max(part):
    d = dev(part)
    return d.get("tj_ovl") or d.get("tj_max") or 175.0


def size_candidate(cand, n_max=12):
    """smallest device count per position meeting, over the sizing corners at 45 C inlet: Tj <= continuous limit at 110 % (steady),
    <= 2-min limit after 120 % for 2 min, <= the absolute maximum after 1.2 x 216 A for 200 ms (overload_tj)"""
    for pos in cand["devs"]:
        cand["devs"][pos] = (cand["devs"][pos][0], 1)
    cand.pop("infeasible", None)
    for _ in range(1 if cand.get("fixed") else 80):
        worst = {}
        for vdc, ang in SIZING_CORNERS:
            o = overload_tj(cand, vdc, ang)
            if o is None:
                continue
            for pos, (part, n) in cand["devs"].items():
                lim = TJ_LIM[part] if part in TJ_LIM else TJ_LIM[kind(part)]
                mg = min(lim[0] - max(o["e110"]["tj"][pos]), lim[1] - max(o["tj_2min"][pos]), tj_abs_max(part) - max(o["tj_200ms"][pos]))
                if o["e110"]["runaway"] and max(o["e110"]["tj"][pos]) > 250.0:
                    mg = -999.0
                worst[pos] = min(worst.get(pos, 1e9), mg)
        bad = {p: mg for p, mg in worst.items() if mg < 0}
        if not bad:
            return worst
        if cand.get("fixed"):               # one module per phase: no count to raise
            p_ = min(bad, key=bad.get)
            cand["infeasible"] = f"{p_} misses its Tj limit by {-bad[p_]:.0f} K (one module per phase)"
            return worst
        pos = min(bad, key=bad.get)
        part, n = cand["devs"][pos]
        if n >= n_max:                      # no count up to n_max meets the limits: flag, keep n_max (reported as not feasible)
            cand["infeasible"] = f"{pos} ({part}) misses its Tj limit by {-bad[pos]:.0f} K even with {n_max} devices"
            return worst
        twins = {"T1": "T4", "T4": "T1", "T2": "T3", "T3": "T2", "D5": "D6", "D6": "D5", "T5": "T6", "T6": "T5", "TH": "TL", "TL": "TH"}
        for p_ in (pos, twins.get(pos)):
            if p_ in cand["devs"]:
                cand["devs"][p_] = (cand["devs"][p_][0], n + 1)
    raise RuntimeError("sizing did not converge")


def l1_for_ripple(cand, fsw=None):
    fsw = fsw or cand["fsw"]
    k = 8.0 if TOPO[cand["topo"]]["levels"] == 3 else 4.0
    return VDC["3W"][1] / (k * fsw * RIPPLE_PP * I_2MIN * math.sqrt(2))


# ------------------------------------------------------------------------------------------------ PWM spectrum and LCL
def pwm_wave(levels, m, fsw, zs="none", f0=F_GRID, n=2 ** 17, wire="3W"):
    """naturally sampled carrier PWM over one fundamental period (f_sw an integer multiple of f0).  3L = phase disposition (in-phase
    carriers 0..1 and -1..0), 2L = one carrier -1..1.  Returns t, the three leg voltages (units of V_dc/2, vs the DC midpoint) and the
    phase voltages the filter sees (3W: line-to-neutral of the floating star; 4W: leg minus an unmodulated N leg = leg vs midpoint)."""
    t = np.arange(n) / n / f0
    tri = np.abs(2.0 * ((t * fsw) % 1.0) - 1.0)                  # 1 -> 0 -> 1 each carrier period
    refs = np.stack([m * np.sin(2 * np.pi * f0 * t - k * 2 * np.pi / 3) for k in range(3)])
    refs = refs + zero_seq(refs, zs)
    if levels == 3:
        legs = np.where(refs > tri, 1.0, 0.0) - np.where(refs < tri - 1.0, 1.0, 0.0)
    else:
        legs = np.where(refs > 2 * tri - 1.0, 1.0, -1.0)
    ph = legs - legs.mean(0) if wire == "3W" else legs
    return t, legs, ph


def pwm_fourier(levels, m, fsw, zs="none", f0=F_GRID, hmax=None):
    """EXACT Fourier series of regular-sampled carrier PWM as the controller makes it: the reference is sampled at every carrier peak and
    valley (double update) and held for half a carrier period; 3L phase disposition / 2L single carrier, symmetric triangle.  Each pulse
    contributes (2/T0) v (e^-jkw0t1 - e^-jkw0t2)/(jkw0) - no time-grid quantisation.  Returns h (1..hmax), complex amplitudes (peak, units
    of V_dc/2) of the three legs vs the DC midpoint, shape (3, hmax)."""
    n = int(round(fsw / f0))
    hmax = hmax or 2 * n + 120
    T0, Tc = 1.0 / f0, 1.0 / fsw
    tk = np.arange(2 * n) * Tc / 2                                  # half-period starts; even k: carrier falling 1 -> 0, odd: rising
    th = 2 * np.pi * f0 * tk
    refs = np.stack([m * np.sin(th - k * 2 * np.pi / 3) for k in range(3)])
    refs = refs + zero_seq(refs, zs)
    falling = (np.arange(2 * n) % 2 == 0)
    h = np.arange(1, hmax + 1)
    w = 2 * np.pi * f0 * h
    out = np.zeros((3, hmax), complex)
    for ph in range(3):
        r = np.clip(refs[ph], -1.0, 1.0)
        if levels == 3:
            d = np.abs(r)
            val = np.sign(r)
            # phase disposition: P pulses sit at the carrier valley (ref > upper carrier), N pulses at the carrier peak (ref < lower carrier)
            at_valley = (r >= 0)
            t1 = np.where(falling == at_valley, tk + (1 - d) * Tc / 2, tk)
            t2 = np.where(falling == at_valley, tk + Tc / 2, tk + d * Tc / 2)
            segs = [(t1, t2, val)]
        else:
            d = 0.5 * (1 + r)                                            # +1 for d of the half period, -1 otherwise
            t1 = np.where(falling, tk + (1 - d) * Tc / 2, tk)
            t2 = np.where(falling, tk + Tc / 2, tk + d * Tc / 2)
            segs = [(t1, t2, np.full_like(r, 2.0)), (tk, tk + Tc / 2, np.full_like(r, -1.0))]
        for a, b, v in segs:
            out[ph] += (2.0 / T0) * np.sum(v[:, None] * (np.exp(-1j * np.outer(a, w)) - np.exp(-1j * np.outer(b, w))), axis=0) / (1j * w)
    return h, out


def spectrum(x):
    """one-period signal -> (harmonic number h, peak amplitude)"""
    X = np.fft.rfft(x) / len(x) * 2.0
    X[0] /= 2.0
    return np.arange(len(X)), np.abs(X)


def lcl_tf(f, l1, cf, l2, lg=0.0, r1=0.0, r2=0.0, rd=0.0, cd=0.0):
    """per-phase LCL: converter voltage -> converter current Y1 and grid current Y2 (grid = short circuit behind L_g).
    Damping: optional series R_d - C_d branch in parallel with C_f."""
    w = 2 * np.pi * np.asarray(f, float)
    zl1, zl2 = r1 + 1j * w * l1, r2 + 1j * w * (l2 + lg)
    zc = 1.0 / (1j * w * cf)
    if cd > 0:
        zc = 1.0 / (1.0 / zc + 1.0 / (rd + 1.0 / (1j * w * cd)))
    zp = zc * zl2 / (zc + zl2)
    y1 = 1.0 / (zl1 + zp)
    return y1, y1 * zc / (zc + zl2)


I2_LIM_FRAC = 0.003    # grid-current component above h35 <= 0.3 % of rated (IEEE 1547-2018 Table 26 / IEEE 519 class value, FROM MEMORY,
                       # applied beyond h50 as a DESIGN TARGET - no standard on file sets the 2-150 kHz grid current; see report c)
CF_SCREEN = 50e-6      # F per phase (star) for the screen: Q = 2.0 % of 125 kVA


def l2_for_limit(levels, fsw, l1, cf, wire="3W"):
    """smallest L2 that keeps every grid-current harmonic above h35 <= I2_LIM_FRAC x rated, worst of V_dc 950 V (sinusoidal PWM) and
    600 V (SVPWM), stiff grid (L_g = 0, worst case for attenuation)"""
    worst_v = None
    for vdc, zs in ((950.0, "none"), (600.0, "minmax")):
        m = V_LL * math.sqrt(2.0 / 3.0) / (vdc / 2) * 1.03
        h, c = pwm_fourier(levels, m, fsw, zs)
        v = np.abs(c[0] - c.mean(0) if wire == "3W" else c[0]) * vdc / 2
        worst_v = v if worst_v is None else np.maximum(worst_v, v)
    sel = h >= fsw / F_GRID - 40        # the carrier groups (sidebands to +-40 f0); the LCL resonance below is the control's job (step c)
    f = h[sel] * F_GRID
    lim = I2_LIM_FRAC * I_RATED * math.sqrt(2)
    for l2 in np.arange(2e-6, 400e-6, 1e-6):
        _, y2 = lcl_tf(f, l1, cf, l2, r1=5e-3, r2=5e-3)
        if np.max(np.abs(y2) * worst_v[sel]) <= lim:
            return l2
    return float("nan")


def filter_cost(l1, l2, cf, phases=3, col="cat"):
    """USD: L1 = 10 + 6 USD/J of 1/2 L1 I^2 (architect's estimate), L2 = 10 + 8 USD/J (fits the architect's 22 USD for 30 uH / 216 A),
    C_f 0.12 USD/uF (architect's 3.6 USD per 30 uF MKP); 5k: x0.8 / x0.85 (ASSUMED, as the architect)"""
    ipk1 = I_2MIN * math.sqrt(2) * (1 + RIPPLE_PP / 2)
    ipk2 = I_2MIN * math.sqrt(2)
    c = phases * ((L1_COST[0] + L1_COST[1] * 0.5 * l1 * ipk1 ** 2) + (10.0 + 8.0 * 0.5 * l2 * ipk2 ** 2))
    c_cf = phases * cf * 1e6 * 0.12
    return c * (1.0 if col == "cat" else 0.8) + c_cf * (1.0 if col == "cat" else 0.85)


def np_residual(m, phi, n=360):
    """IDEAL carrier-based midpoint control of a three-level leg set (averaged over each switching period): at every angle the zero
    sequence v0 is chosen inside its range to cancel the midpoint current sum(-|r_x + v0| i_x) (nearest to the min-max value); returns the
    residual midpoint current per unit of I_pk and v0 over one period.  Where no v0 cancels it (low PF, high m), the residual stays."""
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = np.stack([m * np.sin(th - k * 2 * np.pi / 3) for k in range(3)])
    i = np.stack([np.sin(th - k * 2 * np.pi / 3 - phi) for k in range(3)])
    vmm = -0.5 * (r.max(0) + r.min(0))
    v0, io = np.zeros(n), np.zeros(n)
    for k in range(n):
        lo, hi = -1 - r[:, k].min(), 1 - r[:, k].max()
        cand = sorted([lo, hi] + [x for x in -r[:, k] if lo < x < hi])
        vals = [-np.sum(np.abs(r[:, k] + c) * i[:, k]) for c in cand]
        sols = [a for a, fa in zip(cand, vals) if fa == 0.0]
        sols += [a - fa * (b - a) / (fb - fa) for a, b, fa, fb in zip(cand, cand[1:], vals, vals[1:]) if fa * fb < 0]
        if sols:
            v0[k] = min(sols, key=lambda x: abs(x - vmm[k]))
        else:
            j = int(np.argmin(np.abs(vals)))
            v0[k], io[k] = cand[j], vals[j]
    return th, io, v0


DCL_CACHE = {}
NP_CORNERS = [(600.0, 90.0), (750.0, 90.0), (950.0, 90.0), (600.0, 0.0), (950.0, 0.0), (750.0, 45.0)]


def dclink_count(cand):
    """film capacitors per half (C3D1U147, step d data) for a candidate: ripple-current rule (<= 70 % of I_max, carrier band + LF + the
    C_f-star common-mode share) and, for three-level legs, the midpoint rule (150 Hz ripple <= 0.1 U_N = 60 V pp with IDEAL zero-sequence
    midpoint control, np_residual).  110 % current, worst of the NP_CORNERS."""
    levels = TOPO[cand["topo"]]["levels"]
    key = (levels, cand["fsw"], round(cand["l1"] * 1e7))
    if key in DCL_CACHE:
        return DCL_CACHE[key]
    f = FILM["C3D1U147"]
    l_tot = cand["l1"] + cand.get("l2", 15e-6)
    i_half, need_c = 0.0, 0.0
    for vdc, ang in NP_CORNERS:
        m0 = op_point(vdc, V_LL, I_CONT, ang, l_tot)[0]
        r = dc_currents(vdc, I_CONT, ang, l_tot, n=2 ** 14, zs=zs_policy(levels, m0), levels=levels, fsw=cand["fsw"])
        m = r["m"]
        h, c = pwm_fourier(levels, m, cand["fsw"], zs_policy(levels, m), hmax=3 * int(round(cand["fsw"] / F_GRID)))
        w = 2 * np.pi * F_GRID * h
        vcm = np.abs(c.mean(0)) * vdc / 2
        sel = h > 50
        icm = math.sqrt(np.sum((vcm[sel] / np.abs(1j * w[sel] * cand["l1"] / 3 + 1 / (1j * w[sel] * 3 * CF_SCREEN))) ** 2) / 2)
        i_half = max(i_half, math.sqrt(max(r["ic1_lf"], r["ic2_lf"]) ** 2 + max(r["ic1_hf"], r["ic2_hf"]) ** 2 + (icm / 2) ** 2))
        if levels == 3:
            _, phi, _, _ = op_point(vdc, V_LL, I_CONT, ang, l_tot)
            th, io, v0 = np_residual(m, phi)
            vpp_1F = np.ptp(np.cumsum(io * I_CONT * math.sqrt(2)) * (1 / (F_GRID * len(th))) / 2.0)   # midpoint ripple with C_half = 1 F
            need_c = max(need_c, vpp_1F / (0.1 * f["un70"]))
    n_i = math.ceil(i_half / (pv.CAP_IRMS_USE * f["imax"]))
    n_m = math.ceil(need_c / f["C"]) if levels == 3 else 0
    DCL_CACHE[key] = {"per_half": max(n_i, n_m, 2), "ripple_rule": n_i, "midpoint_rule": n_m, "I_half_A": i_half, "C_np_mF": need_c * 1e3}
    return DCL_CACHE[key]


def screen_cost(cand, phases=3):
    """devices + insulators + gate channels + LCL estimate (USD per PCS) at catalogue (@1) and 5k (largest LCSC break) prices"""
    out = {}
    diodes = cand.get("diode_positions", ())
    for col, qty in (("cat", 1), ("5k", 10 ** 6)):
        if cand.get("module"):              # one module per phase: price once, TIM 1 USD, channels for the switch positions only
            dev_usd = dv.part_price(cand["module"], qty) * phases
            ndev = phases
            nch = sum(1 for p in cand["devs"] if p not in diodes) * phases
            gate = nch * GATE_CH[col]
            pads = 1.0 * phases
        else:
            dev_usd = sum(n * dv.part_price(part, qty) for part, n in cand["devs"].values()) * phases
            ndev = sum(n for part, n in cand["devs"].values()) * phases
            nch = sum(1 for p, (part, n) in cand["devs"].items() if kind(part) != "sbd") * phases
            nextra = sum(n - 1 for p, (part, n) in cand["devs"].items() if kind(part) != "sbd") * phases
            gate = nch * GATE_CH[col] + nextra * GATE_PER_DEV[col]
            pads = ndev * PAD[col]
        flt = filter_cost(cand["l1"], cand["l2"], CF_SCREEN, phases, col)
        dcl = dclink_count(cand)
        dc_usd = 2 * dcl["per_half"] * FILM_PRICE[col]
        out[col] = {"devices": dev_usd, "pads": pads, "gate": gate, "lcl": flt, "dc_link": dc_usd, "dc_link_per_half": dcl["per_half"],
                    "total": dev_usd + pads + gate + flt + dc_usd, "n_devices": ndev, "n_channels": nch,
                    "per_phase_power_stage": (dev_usd + pads + gate) / phases}
        # four-wire increment: a fourth leg (as one phase) + a neutral inductor of L1 class (ARCHITECTURE-PCS section 1)
        leg = (dev_usd + pads + gate) / phases
        ln = (L1_COST[0] + L1_COST[1] * 0.5 * cand["l1"] * (I_2MIN * math.sqrt(2) * (1 + RIPPLE_PP / 2)) ** 2) * (1.0 if col == "cat" else 0.8)
        out[col]["four_wire_increment"] = leg + ln
    return out


def cosmic(cand, vdc, phases=3, tj=25.0, h=0.0):
    """FIT (25 C, sea level unless given) of the phase legs at a steady DC voltage: sum over positions of
    n x die area x FIT/cm2(blocking V) x fraction of time blocking it (fundamental-period average at m ~ 0.8)."""
    topo, t = cand["topo"], TOPO[cand["topo"]]
    th, vref = references(V_LL * math.sqrt(2.0 / 3.0) / (vdc / 2), "none")   # m at 400 V AC: share of time at P / N ~ m / pi each
    dpos, dneg = np.clip(vref, 0, 1).mean(), np.clip(-vref, 0, 1).mean()
    fit, rows = 0.0, []
    for pos, (part, n) in cand["devs"].items():
        k = kind(part)
        if pos in cand.get("diode_positions", ()):      # module FRDs: six to seven decades more robust (AN 17-003 Fig.3)
            continue
        if k == "igbt":
            vr, tech, area = dev(part)["vces"], ("si_1200" if dev(part)["vces"] > 1000 else "si_650"), dev(part)["die_cm2"]
        elif k == "sbd":
            vr, tech, area = 1200.0, "sic", 0.10      # SBD die ASSUMED 0.10 cm2 (Wolfspeed SiC curve as for the MOSFETs)
        else:
            vr, tech, area = dev(part)["vdss"], "sic", dv.SIC_DIE_CM2[part]
        if t["block"][pos] == 1.0:   # T-type outer / 2L: full V_dc while the leg sits at the opposite rail, V_dc/2 in O state
            frac_full = 0.5 if topo == "2L" else (dneg if pos == "T1" else dpos)
            f = frac_full * dv.fit_per_cm2(tech, vdc, vr) + (0.0 if topo == "2L" else
                                                             (1 - dpos - dneg) * dv.fit_per_cm2(tech, vdc / 2, vr))
        else:                        # blocks V_dc/2: counted as 100 % of the time (conservative; these terms are negligible)
            f = dv.fit_per_cm2(tech, vdc / 2, vr) if not (tech == "si_650" and vdc / 2 > 500.0) else float("nan")
        f = float(f) * n * area * phases * float(dv.temp_factor(tj)) * dv.alt_factor(h)
        rows.append((pos, part, round(f, 3)))
        fit += f
    return fit, rows


def rule_ratio(cand, vdc=950.0):
    """largest steady blocking voltage / rating over the positions (project rule <= 0.67; NPC/ANPC/T-inner with the +10 % midpoint band)"""
    worst = 0.0
    for pos, (part, n) in cand["devs"].items():
        d = dev(part)
        vr = d.get("vces") or d.get("vdss") or d.get("vrrm")
        frac = TOPO[cand["topo"]]["block"][pos]
        v = vdc if frac == 1.0 else 0.55 * vdc
        worst = max(worst, v / vr)
    return worst


CANDIDATES = [
    {"name": "TT-IGBT (architect)", "topo": "TT", "fsw": 16e3,
     "devs": {"T1": ("CRG40T120BK3SD", 4), "T4": ("CRG40T120BK3SD", 4), "T2": ("CRG50T60AK3SD", 3), "T3": ("CRG50T60AK3SD", 3)},
     "note": "architect's parts: CRG40T120BK3SD outer, CR Micro 650 V inner (CRG50T60AK3SD, the documented 650 V part on LCSC)"},
    {"name": "TT-IGBT (best documented)", "topo": "TT", "fsw": 16e3,
     "devs": {"T1": ("NCE40TD120VT", 4), "T4": ("NCE40TD120VT", 4), "T2": ("NCE80TD65BT", 3), "T3": ("NCE80TD65BT", 3)},
     "note": "best documented Asian IGBTs on LCSC: NCE 1200 V (175 C, lowest V_CE(sat)) outer, NCE 650 V 80 A (fast) inner"},
    {"name": "TT-SiC 1200/1200 (rule check)", "topo": "TT", "fsw": 32e3,
     "devs": {"T1": ("SG2M035120LJ", 3), "T4": ("SG2M035120LJ", 3), "T2": ("SG2M035120LJ", 2), "T3": ("SG2M035120LJ", 2)},
     "note": "cheapest SiC, but 1200 V outer at 950 V"},
    {"name": "TT-SiC 1700/1200", "topo": "TT", "fsw": 32e3,
     "devs": {"T1": ("SG2M020170HJ", 3), "T4": ("SG2M020170HJ", 3), "T2": ("SG2M035120LJ", 2), "T3": ("SG2M035120LJ", 2)},
     "note": "1700 V outer (the PV device family), 1200 V inner"},
    {"name": "TT-SiC 1700(40m)/1200", "topo": "TT", "fsw": 32e3,
     "devs": {"T1": ("SG2M040170HJ", 4), "T4": ("SG2M040170HJ", 4), "T2": ("SG2M035120LJ", 2), "T3": ("SG2M035120LJ", 2)},
     "note": "as above with the 40 mOhm 1700 V part of the PV design (price = the PV estimate, RFQ)"},
    {"name": "NPC-SiC 1200 + SBD", "topo": "NPC", "fsw": 32e3,
     "devs": {"T1": ("SG2M035120LJ", 3), "T2": ("SG2M035120LJ", 3), "T3": ("SG2M035120LJ", 3), "T4": ("SG2M035120LJ", 3),
              "D5": ("YJD112040NQG2", 2), "D6": ("YJD112040NQG2", 2)},
     "note": "all devices block <= V_dc/2 (0.40-0.44 of 1200 V)"},
    {"name": "ANPC-SiC 1200", "topo": "ANPC", "fsw": 32e3,
     "devs": {p: ("SG2M035120LJ", 3) for p in ("T1", "T2", "T3", "T4", "T5", "T6")},
     "note": "TIDA-010210 scheme: T2/T3 at line frequency, T1/T5 and T4/T6 at f_sw (short commutation loop)"},
    {"name": "NPC-IGBT 1200 + SBD", "topo": "NPC", "fsw": 16e3,
     "devs": {"T1": ("NCE40TD120VT", 4), "T2": ("NCE40TD120VT", 4), "T3": ("NCE40TD120VT", 4), "T4": ("NCE40TD120VT", 4),
              "D5": ("YJD112040NQG2", 2), "D6": ("YJD112040NQG2", 2)},
     "note": "rule-compliant IGBT option (0.44 of 1200 V)"},
    {"name": "NPC-module 1200 V (HIITIO HCG400FL120E3RA)", "topo": "NPC", "fsw": 16e3, "module": "HCG400FL120E3RA", "fixed": True,
     "diode_positions": ("D5", "D6"), "devs": {p: ("HCG400FL120E3RA", 1) for p in ("T1", "T2", "T3", "T4", "D5", "D6")},
     "note": "one Easy 3B I-type module per phase, all 1200 V (0.44 at 950 V)"},
    {"name": "NPC-module 1200 V @8 kHz", "topo": "NPC", "fsw": 8e3, "module": "HCG400FL120E3RA", "fixed": True,
     "diode_positions": ("D5", "D6"), "devs": {p: ("HCG400FL120E3RA", 1) for p in ("T1", "T2", "T3", "T4", "D5", "D6")},
     "note": "the same module at half the frequency (its turn-off energy is large)"},
    {"name": "NPC-module 650 V (HIITIO HCG375FL065E3RC)", "topo": "NPC", "fsw": 16e3, "module": "HCG375FL065E3RC", "fixed": True,
     "diode_positions": ("D5", "D6"), "devs": {p: ("HCG375FL065E3RC", 1) for p in ("T1", "T2", "T3", "T4", "D5", "D6")},
     "note": "one Easy 3B I-type module per phase with 650 V switches (0.73 / 0.80 with the midpoint band at 950 V)"},
    {"name": "2L-SiC 1700", "topo": "2L", "fsw": FSW_CHOICE,
     "devs": {"TH": ("SG2M020170HJ", 6), "TL": ("SG2M020170HJ", 6)},
     "note": "two-level, 1700 V SiC (0.56 of rating at 950 V)"},
    {"name": "2L-SiC 1200 (rule check)", "topo": "2L", "fsw": 32e3,
     "devs": {"TH": ("SG2M035120LJ", 6), "TL": ("SG2M035120LJ", 6)},
     "note": "two-level with the cheapest 1200 V SiC: 0.79 of rating at 950 V - shows what the rule costs inside two-level"},
    {"name": "2L-SiC 1700(40m)", "topo": "2L", "fsw": FSW_CHOICE,
     "devs": {"TH": ("SG2M040170HJ", 8), "TL": ("SG2M040170HJ", 8)},
     "note": "two-level with the PV's 40 mOhm 1700 V part (price = estimate)"},
]


L_LOSS_REF = {"l1": (100e-6, 16e3, 70.0), "l2": (30e-6, 30.0)}   # architect's per-phase L1 / L2 loss at 125 kW (section 7: 210 / 90 W)


def filter_loss_est(l1, l2, fsw, i_rms):
    """SCREEN-ONLY scaling of the architect's inductor losses (ASSUMED, uniform for every candidate; step c gives the budget of the
    chosen design): loss ~ (stored energy)^0.75, L1 split 60 % copper (~I^2) / 40 % core (~sqrt(f_sw), ripple set by V_dc, not load)."""
    ipk = I_2MIN * math.sqrt(2)
    l1r, fr, p1 = L_LOSS_REF["l1"]
    l2r, p2 = L_LOSS_REF["l2"]
    k1 = (l1 / l1r) ** 0.75
    pl1 = p1 * k1 * (0.6 * (i_rms / I_RATED) ** 2 + 0.4 * math.sqrt(fsw / fr))
    pl2 = p2 * (l2 / l2r) ** 0.75 * (i_rms / I_RATED) ** 2 if l2 > 0 else 0.0
    return 3 * (pl1 + pl2)


def module_eta(cand, vdc=750.0, loads=(0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0)):
    """screen-level module efficiency vs load at PF 1, 400 V, 45 C: device model + filter_loss_est + ESTIMATES for the rest:
    70 W fixed (aux, fans) + 140 W x (I / 180 A)^2 (C_f, DC link, contactors, fuses, busbars: architect section 7)"""
    out = []
    for x in loads:
        ev = evaluate(cand, vdc, V_LL, I_RATED * x, 0.0, T_IN)
        p_loss = 3 * ev["leg_w"] + filter_loss_est(cand["l1"], cand["l2"], cand["fsw"], I_RATED * x) + 70.0 + 140.0 * x ** 2
        out.append((x, 1.0 - p_loss / ev["p"], p_loss))
    return out


def step_a():
    """(a) topology and device screen: device count per position from the Tj limits, losses, efficiency, cost, cosmic-ray FIT, the
    0.67 rule; decision = cheapest at 5,000 units among the candidates that keep every device <= 0.67 of its rating at 950 V
    (midpoint band included) and reach >= 98.7 % peak (98.5 % + 0.2 %-point model margin)"""
    hs = section_rth()
    rows = []
    for c in CANDIDATES:
        c["hs_r"] = hs
        c["l1"] = l1_for_ripple(c)
        c["l2"] = l2_for_limit(TOPO[c["topo"]]["levels"], c["fsw"], c["l1"], CF_SCREEN)
        c["l_tot"] = c["l1"] + c["l2"]
        c["margins"] = size_candidate(c)
        full = evaluate(c, 750.0, V_LL, I_RATED, 0.0, T_IN)          # 125 kW at a typical battery voltage
        f900 = evaluate(c, 900.0, V_LL, I_RATED, 0.0, T_IN)
        half = evaluate(c, 750.0, V_LL, I_RATED / 2, 0.0, T_IN)
        c["loss_full_W"], c["loss_900_W"], c["loss_half_W"] = 3 * full["leg_w"], 3 * f900["leg_w"], 3 * half["leg_w"]
        c["eta"] = module_eta(c)
        c["eta_peak"] = max(e for x, e, w in c["eta"])
        c["eta_full"] = c["eta"][-1][1]
        c["loss_mod_full_W"] = c["eta"][-1][2]
        c["cost"] = screen_cost(c)
        c["fit900"], c["fit_rows900"] = cosmic(c, 900.0)
        c["fit950"], c["fit_rows950"] = cosmic(c, 950.0)
        c["rule"] = rule_ratio(c)
        c["tj_full"] = full["tj_max"]
        rows.append(c)
    ok = [c for c in rows if c["rule"] <= dv.COSMIC["rule_vdc_frac"] + 1e-9 and c["eta_peak"] >= ETA_REQ + 0.002 and not c.get("infeasible")]
    finalists = sorted(ok, key=lambda c: c["cost"]["5k"]["total"])[:3]
    sweep = []
    for c in finalists:                      # switching-frequency sweep of the three cheapest compliant candidates
        fs = sorted({16e3, 24e3, 32e3, 40e3, 48e3, FSW_CHOICE})
        for f in fs:
            x = {k: (dict(v) if isinstance(v, dict) else v) for k, v in c.items() if k in ("name", "topo", "devs", "hs_r")}
            x["fsw"] = f
            x["l1"] = l1_for_ripple(x)
            x["l2"] = l2_for_limit(TOPO[x["topo"]]["levels"], f, x["l1"], CF_SCREEN)
            x["l_tot"] = x["l1"] + x["l2"]
            size_candidate(x)
            x["eta"] = module_eta(x)
            x["eta_peak"], x["eta_full"], x["loss_mod_full_W"] = max(e for _, e, _ in x["eta"]), x["eta"][-1][1], x["eta"][-1][2]
            x["cost"] = screen_cost(x)
            sweep.append(x)
    ok2 = [x for x in sweep if x["eta_peak"] >= ETA_REQ + 0.002 and not x.get("infeasible")]
    cheapest = min(ok2, key=lambda x: x["cost"]["5k"]["total"])
    # DECISION: the cheapest compliant THREE-LEVEL point at the chosen 32 kHz (reasons against the cheaper two-level and against
    # 48 kHz are quantified in report_a: CM voltage, magnetics energy, efficiency, L1-cost sensitivity, controller load)
    best3l = min([x for x in ok2 if TOPO[x["topo"]]["levels"] == 3 and abs(x["fsw"] - FSW_CHOICE) < 1],
                 key=lambda x: x["cost"]["5k"]["total"])
    best2l = min([x for x in ok2 if TOPO[x["topo"]]["levels"] == 2 and abs(x["fsw"] - FSW_CHOICE) < 1], key=lambda x: x["cost"]["5k"]["total"])
    choice = min((best3l, best2l), key=lambda x: x["cost"]["5k"]["total"])
    if FP:                              # the topology is decided with the real inductors (section h): two-level
        choice = best2l
    choice["best3l"] = best3l
    choice["rule"] = rule_ratio(choice)
    choice["cheapest"], choice["best2l"] = cheapest, best2l
    cm = {}
    for lev, x in ((3, best3l), (2, best2l)):
        _, legs, _ = pwm_wave(lev, V_LL * math.sqrt(2 / 3) / 475.0 * 1.03, x["fsw"], "none")
        h, a = spectrum(legs.mean(0) * 475.0)
        cm[lev] = {"cm_fsw_peak_V": float(a[int(round(x["fsw"] / F_GRID))]), "cm_rms_V": float(np.sqrt(np.mean((legs.mean(0) * 475.0) ** 2))),
                   "l1_energy_J": 0.5 * x["l1"] * (I_2MIN * math.sqrt(2) * (1 + RIPPLE_PP / 2)) ** 2}
    choice["cm_compare"] = cm
    choice["fit900"], choice["fit_rows900"] = cosmic(choice, 900.0)
    choice["fit950"], choice["fit_rows950"] = cosmic(choice, 950.0)
    SPEC["fsw_sweep"] = [{"name": x["name"], "fsw_kHz": x["fsw"] / 1e3, "devices": {p: f"{n} x {part}" for p, (part, n) in x["devs"].items()},
                          "L1_uH": round(x["l1"] * 1e6, 1), "L2_uH": round(x["l2"] * 1e6, 1), "loss_module_125kW_W": round(x["loss_mod_full_W"]),
                          "eta_peak": round(x["eta_peak"], 4), "usd_cat": round(x["cost"]["cat"]["total"], 1),
                          "usd_5k": round(x["cost"]["5k"]["total"], 1)} for x in sweep]
    # the architect's own 42-device T-type, checked as specified (4 + 3 per position, 16 kHz)
    arch = {"name": "architect 42 devices", "topo": "TT", "fsw": 16e3, "hs_r": hs, "l_tot": 130e-6,
            "devs": {"T1": ("CRG40T120BK3SD", 4), "T4": ("CRG40T120BK3SD", 4), "T2": ("CRG50T60AK3SD", 3), "T3": ("CRG50T60AK3SD", 3)}}
    a900 = evaluate(arch, 900.0, V_LL, I_RATED, 0.0, T_IN)
    a_q = evaluate(arch, 900.0, V_LL, I_RATED, 90.0, T_IN)
    a_r = evaluate(arch, 900.0, V_LL, I_RATED, 180.0, T_IN)
    SPEC["topology_screen"] = {
        "basis": "averaged loss model with datasheet curves (sim/pcs_devices.py, sim/pv_devices.py); Tj sizing at 198 A / 45 C and 216 A "
                 "(2 min as steady state); costs = devices + pads + gate channels + L1 estimate; CALCULATED, not measured",
        "heatsink_section": hs,
        "candidates": [{"name": c["name"], "topology": c["topo"], "fsw_kHz": c["fsw"] / 1e3,
                        "devices": {p: f"{n} x {part}" for p, (part, n) in c["devs"].items()},
                        "n_devices": c["cost"]["cat"]["n_devices"], "gate_channels": c["cost"]["cat"]["n_channels"],
                        "loss_125kW_750V_W": round(c["loss_full_W"]), "loss_125kW_900V_W": round(c["loss_900_W"]),
                        "eta_peak_est": round(c["eta_peak"], 4), "usd_catalogue": round(c["cost"]["cat"]["total"], 1),
                        "usd_5k": round(c["cost"]["5k"]["total"], 1), "max_V_over_rating_950V": round(c["rule"], 3),
                        "FIT_900V_25C_sea": round(c["fit900"], 3), "FIT_950V_25C_sea": round(c["fit950"], 3)} for c in rows],
        "architect_42_devices_check": {"loss_125kW_900V_W": round(3 * a900["leg_w"]), "Tj_max_PF1_C": round(a900["tj_max"], 1),
                                       "Tj_max_PF0_C": round(a_q["tj_max"], 1), "Tj_max_rectifier_C": round(a_r["tj_max"], 1),
                                       "Tj_rating_C": dv.IGBTS["CRG40T120BK3SD"]["tj_max"]},
        "choice": choice["name"]}
    return rows, choice, {"pf1": a900, "pf0": a_q, "rect": a_r, "sweep": sweep}


def report_a(rows, choice, arch):
    t = SPEC["topology_screen"]
    wr("## (a) Topology and devices\n")
    hs = t["heatsink_section"]
    wr(f"**Basis.** One 150 mm plate-fin section per leg ({HS['n_fin']} fins x {HS['h_fin']*1e3:.0f} mm, {HS['length']*1e3:.0f} mm long), "
       f"{HS['fans_per_section']} x {FAN['part']} per section: {hs['flow_m3h']:.0f} m3/h at {hs['dp_Pa']:.0f} Pa, R_sa {hs['r_sa']*1e3:.1f} mK/W "
       f"per section (calculated, pv_tradeoff heatsink model).  Device count per position = the smallest that keeps Tj within its limit "
       f"at 198 A (110 %) / 45 C inlet (continuous), after 216 A for 2 min (heatsink RC) and after 259 A for 200 ms (device transient), "
       f"over V_dc 600/750/900 V and PF angle "
       f"0/45/90/135/180/-90 deg (limits: SiC 150 / 165 / 175 C; CR Micro / NCE 150 C parts 125 / 145-150 / 150-175 C as rated).  "
       f"Losses: averaged model over the fundamental period (sim/pcs_devices.py docstring), sinusoidal PWM where it reaches, SVPWM zero "
       f"sequence below.  Costs here: devices + pads + gate channels + an L1 estimate only (step g has everything).  Efficiency: devices "
       f"+ an ESTIMATE for the rest (70 W + 440 W x (I/180 A)^2).\n")
    wr("| candidate | f_sw kHz | devices per position | dev | ch | device loss 125 kW @750 / 900 V (W) | module loss 125 kW (est.) | "
       "eta full / peak (est.) | USD cat / 5k (4W: +5k) | V/V_rated @950 V | FIT 900 / 950 V |")
    wr("|---|---|---|---|---|---|---|---|---|---|---|")
    for c in rows:
        dd = ", ".join(f"{p} {n}x{part}" for p, (part, n) in c["devs"].items())
        nm = c['name'] + (" - NOT FEASIBLE: " + c["infeasible"] if c.get("infeasible") else "")
        wr(f"| {nm} | {c['fsw']/1e3:.0f} | {dd} | {c['cost']['cat']['n_devices']} | {c['cost']['cat']['n_channels']} | "
           f"{c['loss_full_W']:.0f} / {c['loss_900_W']:.0f} | {c['loss_mod_full_W']:.0f} | {c['eta_full']*100:.2f} / {c['eta_peak']*100:.2f} % | "
           f"{c['cost']['cat']['total']:.0f} / {c['cost']['5k']['total']:.0f} (+{c['cost']['5k']['four_wire_increment']:.0f}) | "
           f"{c['rule']:.2f} | {c['fit900']:.2f} / {c['fit950']:.2f} |")
    wr("\nSwitching-frequency sweep of the three cheapest candidates that pass the 0.67 rule and the efficiency floor (devices re-sized, "
       "L1 for 25 % ripple, L2 for the 0.3 % carrier-band limit with C_f 50 uF):\n")
    wr("| candidate | f_sw kHz | devices | L1 / L2 uH | module loss 125 kW (est.) | peak eta | USD cat / 5k |")
    wr("|---|---|---|---|---|---|---|")
    for x in arch["sweep"]:
        dd = ", ".join(f"{p} {n}" for p, (part, n) in x["devs"].items())
        wr(f"| {x['name']} | {x['fsw']/1e3:.0f} | {dd} | {x['l1']*1e6:.0f} / {x['l2']*1e6:.0f} | {x['loss_mod_full_W']:.0f} | "
           f"{x['eta_peak']*100:.2f} % | {x['cost']['cat']['total']:.0f} / {x['cost']['5k']['total']:.0f} |")
    ch, b2, b3 = choice, choice["best2l"], choice["best3l"]
    d5 = lambda x: x["cost"]["5k"]["total"]
    cmp_ = {r["name"]: r for r in rows}
    arch_r, best_igbt = cmp_["TT-IGBT (architect)"], cmp_["TT-IGBT (best documented)"]
    tt12, l2_12 = cmp_["TT-SiC 1200/1200 (rule check)"], cmp_["2L-SiC 1200 (rule check)"]
    cm3, cm2 = ch["cm_compare"][3], ch["cm_compare"][2]
    dcl = lambda x: x["cost"]["5k"]["dc_link_per_half"]
    wr(f"\n**Decision: {'two-level' if TOPO[ch['topo']]['levels'] == 2 else 'three-level ' + ch['topo']}, {ch['fsw']/1e3:.0f} kHz, "
       + ", ".join(f"{p} {n} x {part}" for p, (part, n) in ch["devs"].items())
       + f"** - {ch['cost']['cat']['n_devices']} devices and {ch['cost']['cat']['n_channels']} gate channels for three-wire: the PV module's "
         f"1700 V SiC device, gate-drive channel and switching frequency.  Screen-scope cost (devices, pads, gate drive, LCL, DC link) "
         f"{ch['cost']['cat']['total']:.0f} USD catalogue / {d5(ch):.0f} USD at 5,000 units; module loss {ch['loss_mod_full_W']:.0f} W at 125 kW; "
         f"peak {ch['eta_peak']*100:.2f} % (estimates; step b gives the map).  **This replaces the architecture's three-level T-type.**\n")
    wr("Why:")
    wr(f"- **The 0.67 rule first (SRC-4).** 950/1700 = 0.56 for every device.  The architect's T-type: 950/1200 = 0.79 (0.75 at 900 V).")
    wr(f"- **The DC link decides between two and three levels.** A three-level leg draws a 150 Hz midpoint current; at PF 0 (150 kVA "
       f"reactive is in AC-02) and 600-750 V it cannot be cancelled by any zero sequence (np_residual: ideal carrier-based midpoint control "
       f"still leaves up to {DCL_CACHE[(3, FSW_CHOICE, round(b3['l1']*1e7))]['C_np_mF']:.1f} mF of need per half).  Keeping the ripple within the "
       f"film's 60 V pp limit takes {dcl(b3)} C3D1U147 per half for every three-level option, against {dcl(ch)} per half for two-level "
       f"(ripple-current-limited): {2*(dcl(b3)-dcl(ch))*FILM_PRICE['5k']:.0f} USD at 5k.  The architecture's 660 uF per half would ripple by "
       f"hundreds of volts there.")
    wr(f"- **Totals (5k, screen scope):** two-level 1700 V SiC {d5(ch):.0f} USD; best three-level ({b3['name']}, {b3['fsw']/1e3:.0f} kHz) "
       f"{d5(b3):.0f} USD; NPC / ANPC SiC {d5(cmp_['NPC-SiC 1200 + SBD']):.0f} / {d5(cmp_['ANPC-SiC 1200']):.0f} USD (ANPC does not earn its place: "
       f"its clamps add six channels and save no loss); the architect's IGBT T-type, sized correctly and with the DC link it needs, "
       f"{d5(arch_r):.0f} USD; the best documented IGBT T-type {d5(best_igbt):.0f} USD.")
    wr(f"- **Price of the safer choice:** none in money - the rule-compliant design is the cheapest one in the screen.  What the rule "
       f"costs inside two-level: {d5(ch)-d5(l2_12):.0f} USD against the same two-level with 1200 V SiC "
       f"({l2_12['name']}, 0.79 of rating at 950 V, FIT {l2_12['fit950']:.0f} at 950 V vs {ch['fit950']:.2f}).  The efficiency price against "
       f"the best SiC three-level: module loss {ch['loss_mod_full_W']:.0f} W vs {b3['loss_mod_full_W']:.0f} W at 125 kW, peak "
       f"{ch['eta_peak']*100:.2f} % vs {b3['eta_peak']*100:.2f} % ({(b3['eta_peak']-ch['eta_peak'])*100:.2f} %-points; the slower gate resistor "
       f"that the 950 V commutation needs adds about 60 W more, step e); against the architect's "
       f"IGBT T-type it is a gain ({arch_r['loss_mod_full_W']:.0f} W, peak {arch_r['eta_peak']*100:.2f} %).")
    wr(f"- **Four-wire:** the fourth leg of a two-level design is one more half-bridge; a T-type phase leg under 100 % unbalance pushes a "
       f"50 Hz current into its midpoint (step d) that the two-level bank does not have.")
    wr(f"- **What two-level costs elsewhere:** common-mode voltage at f_sw {cm2['cm_fsw_peak_V']:.0f} V peak against {cm3['cm_fsw_peak_V']:.0f} V "
       f"for three-level (950 V DC, calculated spectrum): the C_f star must be tied to the DC midpoint (split film bank) and the EMI filter "
       f"needs about 6 dB more common-mode attenuation (step f); full-V_dc steps at up to 60 V/ns (step e) on L1, the heatsink capacitance and the "
       f"cables; L1 stores {cm2['l1_energy_J']:.1f} J against {cm3['l1_energy_J']:.1f} J per phase (twice the inductor) - all inside the totals "
       f"above except the extra common-mode core.  The higher peak efficiency of three-level SiC does not pay for its "
       f"{d5(b3)-d5(ch):.0f} USD.")
    sw2 = [x for x in arch["sweep"] if x["name"] == ch["name"]]
    hi = max(sw2, key=lambda x: x["fsw"])
    if FP:
        wr(f"- **f_sw = {ch['fsw']/1e3:.0f} kHz** - the cheapest compliant point of the re-optimisation with the real inductor designs "
           f"(section h).  This screen, which prices L1 at 10 + 6 USD/J, points the other way ({hi['fsw']/1e3:.0f} kHz would 'save' "
           f"{d5(ch)-d5(hi):.0f} USD here): with the magnetics model in the loop the inductor loss, not its stored energy, sets its price, "
           f"and lower device and core loss at {ch['fsw']/1e3:.0f} kHz buy a cheaper L1.\n")
    else:
        wr(f"- **f_sw = {ch['fsw']/1e3:.0f} kHz** (the PV module's frequency, at which its gate-drive channel, bias power and Miller check are "
           f"validated).  The sweep is not at its cost minimum there: {hi['fsw']/1e3:.0f} kHz would save {d5(ch)-d5(hi):.0f} USD at 5k (smaller L1, "
           f"same device count) for {hi['loss_mod_full_W']-ch['loss_mod_full_W']:.0f} W more loss at 125 kW and {(ch['eta_peak']-hi['eta_peak'])*100:.2f} "
           f"%-points of peak efficiency, 50 % more controller load and its fourth carrier harmonic inside the 150 kHz EMI band - kept as an "
           f"open optimisation for after the inductor quotes.\n")
    ac = t["architect_42_devices_check"]
    wr(f"\n**The architecture document's screen is optimistic.** Its 42-device T-type (4 x CRG40T120BK3SD + 3 x 650 V per position, "
       f"16 kHz) gives {ac['loss_125kW_900V_W']} W of device loss at 125 kW / 900 V in this model, not 1,676 W: it used one 25 C "
       f"V_CE(sat) point and a linear E(I); the data sheets' hot V_CE(sat) curves and the super-linear E_on(I) (Fig.13 of the CR Micro "
       f"sheet: 3.2 mJ at 40 A, 7.3 mJ at 60 A) are higher.  Its hottest junction reaches {ac['Tj_max_PF1_C']:.0f} C at PF 1, "
       f"{ac['Tj_max_PF0_C']:.0f} C at PF 0 and {ac['Tj_max_rectifier_C']:.0f} C in rectifier mode (180 A, 900 V, 45 C) against a "
       f"{ac['Tj_rating_C']:.0f} C rating: the T-type with these parts needs {sum(n for p_, n in [(k, v[1]) for k, v in rows[0]['devs'].items()])*3} "
       f"devices, not 42.  The CR Micro 650 V part is slow (E_on 3.2 mJ at 400 V / 50 A, p3) and the inner positions switch at full "
       f"current in rectifier mode and at PF 0, which the PF-1 screen did not see.\n")


def cosmic_section(rows, choice):
    """(a) cosmic-ray failure rate: makers' data, the architect's T-type at 850/900/950 V, the chosen design; FIT tables into the spec"""
    cmp_ = {r["name"]: r for r in rows}
    arch_full = cmp_["TT-IGBT (architect)"]
    arch42 = {"topo": "TT", "devs": {"T1": ("CRG40T120BK3SD", 4), "T4": ("CRG40T120BK3SD", 4), "T2": ("CRG50T60AK3SD", 3),
                                     "T3": ("CRG50T60AK3SD", 3)}}
    tab = []
    for name, c in (("architect, 42 IGBTs as specified", arch42), (f"architect parts sized here ({arch_full['cost']['cat']['n_devices']} IGBTs)", arch_full),
                    (f"chosen: {choice['name']} ({'two' if TOPO[choice['topo']]['levels'] == 2 else 'three'}-level)", choice)):
        r = {"design": name}
        for v in (850.0, 900.0, 950.0):
            r[f"FIT_{v:.0f}V"] = round(cosmic(c, v)[0], 3)
        r["FIT_950V_Tj100C"] = round(cosmic(c, 950.0, tj=100.0)[0], 3)
        r["FIT_950V_2000m_Tj100C"] = round(cosmic(c, 950.0, tj=100.0, h=2000.0)[0], 3)
        r["FIT_950V_Tj_minus30C"] = round(cosmic(c, 950.0, tj=-30.0)[0], 3)
        tab.append(r)
    fleet = lambda fit: fit * 1e-9 * 8760.0 * 5000.0
    s_rule = float(dv.fit_per_cm2("si_1200", 0.67 * 1200))
    SPEC["cosmic_ray"] = {
        "sources": ["Semikron Danfoss AN 17-003 rev 01 (Fig.3, Table 2, sections 3.2 and 4) - docs/datasheets/power-semiconductors/"
                    "SemikronDanfoss-AN17-003-Cosmic-Ray-Failures.pdf", "Mitsubishi CMH-10370 (qualitative) - Mitsubishi-IGBT-LTDS-note.pdf",
                    "Wolfspeed Gen3 SiC FIT/cm2 (pv_devices.COSMIC_GEN3_1200, application brief on file)"],
        "si_1200V_FIT_per_cm2_25C_sea": {str(v): round(float(dv.fit_per_cm2("si_1200", v)), 2) for v in (804, 850, 900, 950, 1000)},
        "factors": "temperature exp(-(Tj-25)/47.6) (ABB via AN 17-003), altitude 2^(h/1000 m); outer T-type devices block V_dc ~m/pi of the time",
        "assumptions": "die area: 1200 V 40 A IGBT 0.39 cm2 (IGBT4 current density), SiC per pcs_devices.SIC_DIE_CM2; Chinese devices assumed to "
                       "follow the reference technology at the same V/V_rated - no Chinese maker publishes cosmic-ray data",
        "table": tab, "rule_frac": dv.COSMIC["rule_vdc_frac"]}
    wr("\n### Cosmic-ray failure rate versus DC voltage (the reason for the 0.67 rule)\n")
    wr("What the makers publish (all files under docs/datasheets/power-semiconductors/):")
    wr(f"- **Semikron Danfoss AN 17-003** (2024): measured failure rate of a 1200 V IGBT chip (12E4) at 25 C, sea level, per cm2 of chip: "
       + ", ".join(f"{v} V: {f} FIT/cm2" for v, f in SPEC['cosmic_ray']['si_1200V_FIT_per_cm2_25C_sea'].items())
       + " (Fig.3 and Table 2, calibrated on its 41 FIT/switch at 900 V example) - a factor of about 3.4 per 50 V; log-linear "
         "interpolation only, no extrapolation (p6); temperature factor exp(-(Tj-25)/47.6) and altitude factor 2^(h/1000 m) (p6); the "
         "free-wheeling diode is six to seven decades more robust (Fig.3); 3L NPC with 650 V chips at <= 500 V or 1200 V chips at <= 750 V "
         "is 'immune' (< 1 FIT/cm2, p9); in a T-type the outer switches block the full DC voltage about 25 % of the time and set the rate "
         "(p10).")
    wr("- **Mitsubishi** (LTDS note, 2025): failure rate rises with V_CE and altitude and falls with temperature; numbers only on request.")
    wr("- **Wolfspeed** (Gen3 1200 V SiC, on file): 0.01 FIT/cm2 at 640 V, 2.8 at 775 V, 30 at 900 V, 70 at 950 V.")
    wr("- **CR Micro, NCE, Sichain, Yangjie: nothing published.**  The Chinese devices are assumed to behave like the reference "
       "technology at the same fraction of their rating; their real curves are a release item (RFQ question to each maker).")
    wr(f"\nAt 950 V a 1200 V IGBT is at 0.79 of its rating: {float(dv.fit_per_cm2('si_1200', 950)):.0f} FIT/cm2 against "
       f"{s_rule:.2f} FIT/cm2 at the 0.67 rule voltage (804 V) - {float(dv.fit_per_cm2('si_1200', 950))/s_rule:.0f} times higher; at the "
       f"900 V full-load limit {float(dv.fit_per_cm2('si_1200', 900))/s_rule:.0f} times.  Per PCS (25 C, sea level unless stated; die areas "
       f"and the blocking-time share in pcs_spec.json):\n")
    wr("| design | FIT @850 V | @900 V | @950 V | @950 V, Tj 100 C | @950 V, 2000 m, Tj 100 C | @950 V, Tj -30 C (cold start) |")
    wr("|---|---|---|---|---|---|---|")
    for r in tab:
        wr(f"| {r['design']} | {r['FIT_850V']:.2f} | {r['FIT_900V']:.2f} | {r['FIT_950V']:.2f} | {r['FIT_950V_Tj100C']:.2f} | "
           f"{r['FIT_950V_2000m_Tj100C']:.2f} | {r['FIT_950V_Tj_minus30C']:.2f} |")
    wr(f"\nFor a fleet of 5,000 units operating continuously at 950 V (sea level, 25 C) the architect's 42-IGBT T-type would lose "
       f"{fleet(tab[0]['FIT_950V']):.1f} units a year to single-event burnout ({fleet(tab[0]['FIT_900V']):.1f} at 900 V; "
       f"{fleet(tab[0]['FIT_950V_2000m_Tj100C']):.1f} at 2,000 m and Tj 100 C); the chosen design {fleet(tab[2]['FIT_950V']):.3f}.  "
       f"A burnt outer IGBT shorts the full DC link through the leg - the DC fuses clear it, the module is a repair.  These are order-of-magnitude "
       f"figures (AN 17-003: 'order-of-magnitude estimates'), but the ratio is robust: the 1200 V T-type is two to three decades worse "
       f"than a design that respects the rule.  Limiting the T-type to 804 V would break AC-02 (950 V operating, 900 V full load); "
       f"derating power above 900 V does not help - cosmic-ray failures happen while blocking, at any current.\n")


# ------------------------------------------------------------------------------------------------ (b) loss and thermal map
# Loss budget of every block other than the semiconductors (per PCS; I = phase current, at 180 A unless scaled).  Inductor figures are
# the BUDGETS step c hands to the magnetics engineer; the rest are ESTIMATES from the named sources (re-read in steps d, f, g).
BUDGET = {
    "L1_cu_W_per_phase_180A": 35.0,      # budget (step c: winding DCR <= 1.0 mOhm incl. AC factor at 180 A)
    "L1_core_W_per_phase_750V": 22.0,    # budget at 750 V (ripple core + gap loss), scales ~ (V_dc/750)^2
    "L2_W_per_phase_180A": 12.0,         # budget (50 Hz-dominated)
    "Cf_damping_W": 6.0,                 # C_f ESR + passive damping branch (step c)
    "dc_link_W_full": 20.0,              # film ESR at the full-load ripple (step d recomputes)
    "ac_contactor_mohm_per_pole": 0.25,  # 2 in series x 3 poles; contact + terminals (ESTIMATE; NXC/CJX2 class data sheets not on file)
    "ac_coil_W": 6.0,                    # 2 contactors with economiser (ESTIMATE)
    "dc_contactor_mohm": 0.20, "dc_coil_W": 6.0,     # HFE82V class (port_spec lean contactor coil 6 W; contact R ESTIMATE)
    "dc_fuse_W_per_link_167A": 15.0,     # 400 A aR class at 167 A (ESTIMATE, scales with I^2; no fuse data sheet chosen, R-05)
    "busbar_W_180A": 30.0,               # busbars, terminals, PCB copper (ESTIMATE, ~I^2)
    "shunt_ohm": 100e-6,                 # DC shunt 2 x 200 uOhm in parallel (port_spec lean)
    "aux_W": 25.0,                       # controller, 12 gate channels (~0.5 W each), sensors, contactor drivers (ESTIMATE)
    "aux_eff": 0.85,                     # PV 75 W flyback efficiency (ESTIMATE)
}


def fans_w(n_fans, load):
    """fan electrical power: speed follows the load (>= 40 %), power ~ speed^3 (fan law)"""
    sp = min(max(load, 0.4), 1.0)
    return n_fans * FAN["w"] * sp ** 3


def other_losses(i_rms, vdc, p_ac, n_fans, load):
    b = BUDGET
    x = i_rms / I_RATED
    idc = abs(p_ac) / vdc
    out = {"L1": 3 * (b["L1_cu_W_per_phase_180A"] * x ** 2 + b["L1_core_W_per_phase_750V"] * (vdc / 750.0) ** 2),
           "L2": 3 * b["L2_W_per_phase_180A"] * x ** 2,
           "Cf + damping": b["Cf_damping_W"] * max(x, 0.3),
           "DC link": b["dc_link_W_full"] * x ** 2,
           "AC contactors": 6 * b["ac_contactor_mohm_per_pole"] * 1e-3 * i_rms ** 2 + b["ac_coil_W"],
           "DC contactor": b["dc_contactor_mohm"] * 1e-3 * idc ** 2 + b["dc_coil_W"],
           "DC fuses": 2 * b["dc_fuse_W_per_link_167A"] * (idc / 167.0) ** 2,
           "busbars, terminals": b["busbar_W_180A"] * x ** 2,
           "DC shunt": b["shunt_ohm"] * idc ** 2,
           "aux supply (control, drivers, sensors)": b["aux_W"] / b["aux_eff"],
           "fans": fans_w(n_fans, load) / b["aux_eff"]}
    if FP:                              # the magnetics-model parts instead of the budgets: a(V_dc) + k2 I^2 per phase
        for key in ("L1", "L2"):
            lp = FP[key]["loss_per_phase"]
            a = sorted((float(k), v) for k, v in lp["a"].items())
            out[key] = 3 * (float(np.interp(vdc, [x[0] for x in a], [x[1] for x in a])) + lp["k2"] * i_rms ** 2)
    return out


def design_from(choice):
    d = {k: (dict(v) if isinstance(v, dict) else v) for k, v in choice.items() if k in ("name", "topo", "devs", "fsw", "hs_r", "l1", "l2")}
    d["l2"] = max(d["l2"], 15e-6)       # step c sets the real L2; 15 uH placeholder for the modulation index here
    if FP:                              # the re-optimised filter (sim/pcs_tradeoff.py)
        d["l1"], d["l2"] = FP["L1_uH"] * 1e-6, FP["L2_uH"] * 1e-6
    d["l_tot"] = d["l1"] + d["l2"]
    return d


def eff_point(D, vdc, v_ll, i_rms, ang, t_in, n_fans):
    ev = evaluate(D, vdc, v_ll, i_rms, ang, t_in)
    oth = other_losses(i_rms, vdc, ev["p"], n_fans, i_rms / I_RATED)
    p_dev = 3 * ev["leg_w"]
    p_loss = p_dev + sum(oth.values())
    p_ac = abs(ev["p"])
    if ang in (0.0,) or math.cos(math.radians(ang)) > 0:      # inverter: DC -> AC, output = AC
        eta = p_ac / (p_ac + p_loss) if p_ac > 0 else 0.0
    else:                                                     # rectifier: AC -> DC, output = DC
        eta = (p_ac - p_loss) / p_ac if p_ac > 0 else 0.0
    return ev, oth, p_dev, p_loss, eta


def step_b(choice):
    D = design_from(choice)
    n_sec = 3
    n_fans = n_sec * HS["fans_per_section"]
    hs = D["hs_r"]
    # heatsink section thermal capacity (aluminium 2700 kg/m3, 900 J/kgK): base 10 mm + fins
    vol = 0.010 * 0.150 * HS["length"] + HS["n_fin"] * HS["t_fin"] * HS["h_fin"] * HS["length"]
    c_hs = vol * 2700.0 * 900.0
    tau = hs["r_sa"] * c_hs
    res = {"heatsink": {"sections": n_sec, "per_section": dict(HS), "fans": f"{n_fans} x {FAN['part']}", "flow_m3h_per_section": hs["flow_m3h"],
                        "dp_Pa": hs["dp_Pa"], "R_sa_K_per_W_per_section": hs["r_sa"], "mass_kg_per_section": vol * 2700.0,
                        "tau_s": tau, "insulator": "Al2O3 0.635 mm + 2 x 50 um grease + clip per TO-247: R_cs %.3f K/W" % R_CS,
                        "R_spread_K_per_W": R_SPREAD}}
    # ---- envelope map at 45 / 60 C (worst Tj per corner) and the efficiency map
    vdcs = (590.0, 600.0, 650.0, 700.0, 750.0, 800.0, 850.0, 900.0, 950.0)
    angs = (0.0, 45.0, 90.0, 135.0, 180.0, -45.0, -90.0, -135.0)
    env = []
    for t_in in (T_IN, T_IN_HOT):
        for vdc in vdcs:
            for vll in (V_LL * (1 - V_TOL), V_LL, V_LL * (1 + V_TOL)):
                for ang in angs:
                    for x in (0.25, 0.5, 0.75, 1.0, I_CONT / I_RATED):
                        ev = evaluate(D, vdc, vll, I_RATED * x, ang, t_in)
                        env.append({"t_in": t_in, "vdc": vdc, "vll": vll, "ang": ang, "load": x, "feasible": ev["feasible"], "m": ev["m"],
                                    "zs": ev["zs"], "p_dev": 3 * ev["leg_w"], "tj": ev["tj_max"], "t_hs": ev["t_hs"],
                                    "tj_pos": {p: max(v) for p, v in ev["tj"].items()}})
    feas45 = [e for e in env if e["feasible"] and e["t_in"] == T_IN]
    w45 = max(feas45, key=lambda e: e["tj"])
    infeas = sorted({(e["vdc"], round(e["vll"])) for e in env if not e["feasible"] and e["t_in"] == T_IN})
    res["envelope_45C"] = {"tj_max_C": w45["tj"], "at": {k: w45[k] for k in ("vdc", "vll", "ang", "load", "m", "zs", "t_hs", "tj_pos")},
                           "infeasible_vdc_vll": infeas}
    # full-load corners (110 % continuous) at 45 C and the 60 C picture
    for t_in in (T_IN, T_IN_HOT):
        fl = [e for e in env if e["feasible"] and e["t_in"] == t_in and abs(e["load"] - I_CONT / I_RATED) < 1e-9]
        res[f"tj_110pct_{t_in:.0f}C"] = max(e["tj"] for e in fl)
    # ---- efficiency map (PF 1, both directions, 400 V AC), loss budget, peak
    loads = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0, I_CONT / I_RATED)
    emap = {}
    for direction, ang in (("inverter", 0.0), ("rectifier", 180.0)):
        for vdc in (600.0, 700.0, 750.0, 800.0, 850.0, 900.0, 950.0):
            emap[(direction, vdc)] = [(x, eff_point(D, vdc, V_LL, I_RATED * x, ang, T_IN, n_fans)[4]) for x in loads]
    peak = max(((k, x, e) for k, v in emap.items() for x, e in v), key=lambda t: t[2])
    ev, oth, p_dev, p_loss, eta_full = eff_point(D, 750.0, V_LL, I_RATED, 0.0, T_IN, n_fans)
    _, oth9, p_dev9, p_loss9, eta_full9 = eff_point(D, 900.0, V_LL, I_RATED, 0.0, T_IN, n_fans)
    worst_full = min(e for (dirn, vdc), v in emap.items() for x, e in v if abs(x - 1.0) < 1e-9 and vdc <= VDC["3W"][3])   # 600-900 V
    res["efficiency"] = {"peak": peak[2], "peak_at": {"direction": peak[0][0], "vdc": peak[0][1], "load": peak[1]},
                         "full_load_750V": eta_full, "full_load_900V": eta_full9, "full_load_worst": worst_full,
                         "required_max": ETA_REQ, "met": peak[2] >= ETA_REQ,
                         "map": {f"{k[0]} {k[1]:.0f} V": {f"{x:.2f}": round(e, 5) for x, e in v} for k, v in emap.items()}}
    res["loss_budget_125kW_750V_W"] = dict({"semiconductors": p_dev}, **{k: v for k, v in oth.items()})
    res["loss_budget_125kW_900V_W"] = dict({"semiconductors": p_dev9}, **{k: v for k, v in oth9.items()})
    # device loss split at 125 kW / 750 V (conduction vs switching share -> modulation argument)
    dsw = {"cond": 0.0, "sw": 0.0}
    full_e = evaluate(D, 750.0, V_LL, I_RATED, 0.0, T_IN)
    cond_only = evaluate(D, 750.0, V_LL, I_RATED, 0.0, T_IN, fsw=1.0)
    res["switching_share_125kW_750V"] = 1.0 - cond_only["leg_w"] / full_e["leg_w"]
    # ---- derating map: largest current (<= 110 %) with every Tj <= its continuous limit, worst PF and AC voltage, per V_dc and inlet
    der = {}
    for t_in in (45.0, 50.0, 55.0, 60.0):
        for vdc in vdcs:
            best = 0.0
            for x in np.arange(1.20, 0.29, -0.05):
                ok = True
                for vll in (V_LL * (1 - V_TOL), V_LL, V_LL * (1 + V_TOL)):
                    for ang in (0.0, 90.0, 180.0, -90.0):
                        ev = evaluate(D, vdc, vll, I_RATED * x, ang, t_in)
                        if not ev["feasible"]:
                            continue
                        if min(tj_ok(D, ev, False).values()) < 0:
                            ok = False
                            break
                    if not ok:
                        break
                if ok:
                    best = x
                    break
            der[(t_in, vdc)] = best
    res["derating_current_frac"] = {f"{t:.0f}C": {f"{v:.0f}": round(der[(t, v)], 2) for v in vdcs} for t in (45.0, 50.0, 55.0, 60.0)}
    # ---- overloads: 120 % for 2 min (heatsink RC from the 110 % steady state), > 120 % for 200 ms (device rise at 1.2 x I_max)
    ov = []
    for vdc, ang in ((600.0, 0.0), (750.0, 0.0), (900.0, 90.0), (900.0, -90.0), (900.0, 180.0), (600.0, 180.0)):
        o = overload_tj(D, vdc, ang)
        if o is None:
            continue
        ov.append({"vdc": vdc, "ang": ang, "tj_120pct_2min_C": max(max(v) for v in o["tj_2min"].values()),
                   "tj_200ms_C": max(max(v) for v in o["tj_200ms"].values()), "t_hs_after_2min_C": o["t_hs_2min"]})
    res["overload"] = ov
    # ---- sharing sensitivity: hottest device at k = 1.2 (no R_DS(on) binning) at the worst 110 % corner
    ks = dict(K_SHARE)
    K_SHARE["sic"] = 1.20
    e_k = evaluate(D, w45["vdc"], w45["vll"], I_CONT, w45["ang"], T_IN)
    K_SHARE.update(ks)
    res["sharing_k1p2_tj_C"] = e_k["tj_max"]
    SPEC["thermal_and_losses"] = res
    return D, res


def report_b(D, r):
    wr("\n## (b) Losses, junction temperatures, efficiency and derating\n")
    h = r["heatsink"]
    wr(f"**Thermal concept (calculated):** one earthed extruded section per phase leg ({h['sections']} for three-wire, 4 for four-wire), "
       f"150 mm x {HS['length']*1e3:.0f} mm, {HS['n_fin']} fins x {HS['h_fin']*1e3:.0f} mm, {h['mass_kg_per_section']:.1f} kg each; "
       f"{h['fans']} ({HS['fans_per_section']} per section, push): {h['flow_m3h_per_section']:.0f} m3/h at {h['dp_Pa']:.0f} Pa per section, R_sa "
       f"{h['R_sa_K_per_W_per_section']*1e3:.1f} mK/W, thermal time constant {h['tau_s']:.0f} s.  Each TO-247 on {h['insulator']} "
       f"(basic insulation DC poles - PE as the PV design; Al2O3 is enough at these loss densities) + {R_SPREAD} K/W base spreading.  "
       f"The fan is rated -10..+60 C (p3): at 60 C inlet it is at its limit and below -10 C it is outside its rating (PV risk R-05).")
    lev = TOPO[D["topo"]]["levels"]
    if MOD_2L == "minmax":
        wr(f"\n**Modulation:** carrier-based two-level PWM, sampled twice per carrier period, **with the min-max zero sequence "
           f"(SVPWM-equivalent) at every operating point** - chosen in section h: it flattens each leg's reference, lowers the L1 ripple "
           f"and core loss and so buys a cheaper L1.  The price is a 150 Hz voltage between the battery and earth at every DC voltage on a "
           f"three-wire TN connection (step f: within the leakage limit up to 5 uF of battery capacitance to earth).  Discontinuous PWM "
           f"would cut the switching loss ({r['switching_share_125kW_750V']*100:.0f} % of the device loss at 125 kW / 750 V) but its zero "
           f"sequence is twice as large and excites the L1-C_f common-mode resonance through the C_f-star tie - not used.")
    else:
        wr(f"\n**Modulation:** carrier-based two-level PWM, sampled twice per carrier period, **sinusoidal references wherever they reach "
           f"(m <= 0.98) and the min-max zero sequence (SVPWM-equivalent) only above** (low DC voltage with high AC voltage: 590-680 V).  Why: "
           f"any low-frequency zero sequence becomes a 150 Hz voltage between the battery and earth on a three-wire TN connection (step f); "
           f"a two-level leg has no midpoint to balance, so nothing else asks for it.  Discontinuous PWM would cut the switching loss "
           f"({r['switching_share_125kW_750V']*100:.0f} % of the device loss at 125 kW / 750 V) but is all zero sequence - not used.")
    wr(f"\n**Current sharing of paralleled discretes:** the hottest device is taken to carry k = {K_SHARE['sic']:.2f} x the mean current "
       f"(conduction and switching) - it requires devices from one lot (R_DS(on) spread within +-10 %), Kelvin-source drive with a "
       f"0.5 ohm Kelvin resistor per device (gen/gdrv.py R_KS) and a symmetric layout; positive R_DS(on) temperature coefficient "
       f"(x1.4-1.9 from 25 to 150 C) stabilises it.  Without binning (data-sheet max/typ R_DS(on) "
       f"{pv.MOSFETS[D['devs'][next(iter(D['devs']))][0]]['rds25_max']/pv.MOSFETS[D['devs'][next(iter(D['devs']))][0]]['rds25']:.2f} for "
       f"{D['devs'][next(iter(D['devs']))][0]}) k = 1.2 gives "
       f"Tj {r['sharing_k1p2_tj_C']:.0f} C at the worst 110 % corner (vs {r['envelope_45C']['tj_max_C']:.0f} C): binning or one-lot "
       f"assembly is a production requirement.")
    e = r["envelope_45C"]
    wr(f"\n**Junction temperatures (calculated):** worst over the envelope at 45 C inlet (V_dc 590-950 V, AC 340-460 V, PF angle 0..+-180, "
       f"25-110 % current): **{e['tj_max_C']:.0f} C** at V_dc {e['at']['vdc']:.0f} V, AC {e['at']['vll']:.0f} V, PF angle {e['at']['ang']:.0f}, "
       f"{e['at']['load']*100:.0f} % (heatsink {e['at']['t_hs']:.0f} C; per position "
       + ", ".join(f"{p} {t:.0f} C" for p, t in e['at']['tj_pos'].items()) + f").  110 % at 60 C inlet: Tj {r['tj_110pct_60C']:.0f} C "
       f"(limits 150 C continuous / 165 C for 2 min, devices rated 175 C).  Not reachable (modulation): "
       + ", ".join(f"{v:.0f} V DC with {a} V AC" for v, a in e['infeasible_vdc_vll'][:8]) + ("" if len(e['infeasible_vdc_vll']) <= 8 else ", ..."))
    wr("\n| overload | V_dc | PF angle | Tj (C) |")
    wr("|---|---|---|---|")
    for o in r["overload"]:
        wr(f"| 120 % for 2 min (heatsink RC from the 110 % state, then {o['t_hs_after_2min_C']:.0f} C) | {o['vdc']:.0f} | {o['ang']:.0f} | {o['tj_120pct_2min_C']:.0f} |")
        wr(f"| 1.2 x 216 A = {I_200MS:.0f} A for 200 ms from the 110 % state (device transient, tau_jc {TAU_JC*1e3:.0f} ms, tau_cs {TAU_CS:.2f} s) | {o['vdc']:.0f} | {o['ang']:.0f} | {o['tj_200ms_C']:.0f} |")
    ef = r["efficiency"]
    wr(f"\n**Efficiency (calculated, 400 V AC, PF 1, 45 C):** peak **{ef['peak']*100:.2f} %** ({ef['peak_at']['direction']}, "
       f"{ef['peak_at']['vdc']:.0f} V, {ef['peak_at']['load']*100:.0f} % load) - the >= 98.5 % requirement is **{'met' if ef['met'] else 'NOT met'}** "
       f"(margin {(ef['peak']-ETA_REQ)*100:.2f} %-points against a model uncertainty of about +-0.2).  Full load: "
       f"{ef['full_load_750V']*100:.2f} % at 750 V, {ef['full_load_900V']*100:.2f} % at 900 V, worst {ef['full_load_worst']*100:.2f} %.\n")
    wr("| direction, V_dc | " + " | ".join(f"{float(x)*100:.0f} %" for x in list(ef["map"].values())[0]) + " |")
    wr("|---|" + "---|" * len(list(ef["map"].values())[0]))
    for k, v in ef["map"].items():
        wr(f"| {k} | " + " | ".join(f"{e_*100:.2f}" for e_ in v.values()) + " |")
    wr("\n**Loss budget at 125 kW (W, calculated / budget):**\n")
    wr("| block | 750 V | 900 V |")
    wr("|---|---|---|")
    for k in r["loss_budget_125kW_750V_W"]:
        wr(f"| {k} | {r['loss_budget_125kW_750V_W'][k]:.0f} | {r['loss_budget_125kW_900V_W'][k]:.0f} |")
    wr(f"| **total** | **{sum(r['loss_budget_125kW_750V_W'].values()):.0f}** | **{sum(r['loss_budget_125kW_900V_W'].values()):.0f}** |")
    wr("\n**Derating (calculated): largest continuous current (fraction of 180 A, searched up to 120 %; the product rating stops at 110 %) with every Tj within 150 C, worst of PF "
       "angle 0/90/180/-90 and AC 340/400/460 V:**\n")
    vd = list(next(iter(r["derating_current_frac"].values())).keys())
    wr("| inlet | " + " | ".join(f"{v} V" for v in vd) + " |")
    wr("|---|" + "---|" * len(vd))
    for t, row in r["derating_current_frac"].items():
        wr(f"| {t} | " + " | ".join(f"{row[v]:.2f}" for v in vd) + " |")


# ------------------------------------------------------------------------------------------------ (c) switching frequency + LCL filter
F_S = 2 * FSW_CHOICE          # control sampling: double update (C2000 ePWM, ARCHITECTURE-PCS section 6)
BW_LOOP = 1e3 * F_S / 64e3    # current-loop bandwidth: 1 kHz at 64 kHz sampling, scaled with f_s (sim/pcs_tradeoff.py uses the same rule)
T_DELAY = 1.5 / F_S           # computation + PWM transport delay
OC_TRIP = 450.0               # A peak per phase, hardware window comparator (step e: must sit above 1.2 x I_max peak + ripple)
# grid-current harmonic limits used (IEEE 1547-2018 Table 26/27, FROM MEMORY - the standard text is not on file; EN 50549-1 refers
# harmonics to the EN 61000-3 series and GB/T 34120 to a THD limit; these are the stricter, published-in-a-table values we know):
HARM_LIM = {"odd": [(11, 0.04), (17, 0.02), (23, 0.015), (35, 0.006), (51, 0.003)], "even": {2: 0.01, 4: 0.02, 6: 0.03},
            "even_rest_frac_of_odd": 0.25, "TRD": 0.05, "above_h50_design_target": I2_LIM_FRAC, "product_THDi": 0.03}


def harm_limit(h):
    if h % 2 == 0:
        if h in HARM_LIM["even"]:
            return HARM_LIM["even"][h]
        return HARM_LIM["even_rest_frac_of_odd"] * harm_limit(h + 1)
    for hmax, lim in HARM_LIM["odd"]:
        if h < hmax:
            return lim
    return HARM_LIM["above_h50_design_target"]


def lcl_design(D):
    """L1 for the ripple rule; C_f and L2 placing the stiff-grid resonance below f_s/6 (capacitor-current active damping works there)
    and meeting the 0.3 % carrier-band target with margin; passive R_d-C_d branch for robustness"""
    l1 = D["l1"] if FP else l1_for_ripple(D, FSW_CHOICE)
    cf, l2 = (FP["Cf_uF"] * 1e-6, FP["L2_uH"] * 1e-6) if FP else (50e-6, 15e-6)      # FP: sim/pcs_tradeoff.py's cheapest compliant pair
    rd, cd = 2.0, 10e-6
    return {"L1": l1, "Cf": cf, "L2": l2, "Rd": rd, "Cd": cd}


def f_res(l1, l2, cf, lg=0.0):
    lp = l1 * (l2 + lg) / (l1 + l2 + lg)
    return 1.0 / (2 * math.pi * math.sqrt(lp * cf))


def step_c(D):
    LEV = TOPO[D["topo"]]["levels"]
    F = lcl_design(D)
    l1, cf, l2, rd, cd = F["L1"], F["Cf"], F["L2"], F["Rd"], F["Cd"]
    D["l2"], D["l_tot"] = l2, l1 + l2
    zb = V_LL ** 2 / P_RATED
    scrs = (None, 50.0, 20.0, 10.0, 5.0)
    lgs = {scr: (0.0 if scr is None else zb / scr / (2 * math.pi * F_GRID)) for scr in scrs}
    res = {"filter": F, "pu": {"L1": 2 * math.pi * F_GRID * l1 / zb, "L2": 2 * math.pi * F_GRID * l2 / zb,
                               "Cf_Q_frac": 3 * 2 * math.pi * F_GRID * cf * (V_LL / math.sqrt(3)) ** 2 / P_RATED},
           "f_res_Hz": {("stiff" if scr is None else f"SCR {scr:.0f}"): f_res(l1, l2, cf, lg) for scr, lg in lgs.items()},
           "f_s_Hz": F_S, "f_crit_Hz": F_S / 6.0}
    # resonance peak with / without the passive branch (stiff grid)
    ff = np.linspace(500.0, 20e3, 4000)
    for tag, (r_, c_) in (("undamped", (0.0, 0.0)), ("passive_branch", (rd, cd))):
        _, y2 = lcl_tf(ff, l1, cf, l2, r1=3e-3, r2=1e-3, rd=r_, cd=c_)
        res[f"peak_gain_{tag}_dB"] = float(20 * np.log10(np.max(np.abs(y2)) * 2 * math.pi * 1e3 * l1))   # vs the L1-only admittance at 1 kHz
    # ---- harmonic spectrum at rated power (inverter PF 1), three DC voltages, grid cases
    spec_rows, worst_hf = [], {}
    pts = {}
    for vdc in (600.0, 750.0, 950.0):
        m, phi, p, q = op_point(vdc, V_LL, I_RATED, 0.0, l1 + l2)
        zs = zs_policy(LEV, m)
        h, c = pwm_fourier(LEV, m, FSW_CHOICE, zs)
        v = np.abs(c[0] - c.mean(0)) * vdc / 2                 # line-to-neutral of the floating star (DM)
        vcm = np.abs(c.mean(0)) * vdc / 2                      # common mode vs the DC midpoint
        pts[vdc] = (h, v, vcm, m, zs)
        for scr, lg in lgs.items():
            sel = h >= 2
            y1, y2 = lcl_tf(h[sel] * F_GRID, l1, cf, l2, lg=lg, r1=3e-3, r2=1e-3, rd=rd, cd=cd)
            i2 = np.abs(y2) * v[sel]
            hh = h[sel]
            i1n = I_RATED * math.sqrt(2)
            lo = hh <= 50
            thd50 = math.sqrt(np.sum(i2[lo] ** 2)) / i1n
            thd_all = math.sqrt(np.sum(i2 ** 2)) / i1n
            viol = [(int(x), float(c / i1n), harm_limit(int(x))) for x, c in zip(hh, i2) if c / i1n > harm_limit(int(x))]
            hf = hh > 50
            k = int(np.argmax(i2[hf]))
            spec_rows.append({"vdc": vdc, "grid": "stiff" if scr is None else f"SCR {scr:.0f}", "m": m, "zs": zs,
                              "THD_h2_50_pwm": thd50, "THD_all_pwm": thd_all, "largest_above_h50": (int(hh[hf][k]), float(i2[hf][k] / i1n)),
                              "violations": viol[:5]})
    res["spectrum"] = spec_rows
    # ---- converter-side ripple: DM (L1) + CM current through the C_f star - DC midpoint tie; time-domain peak
    rip = {}
    for vdc, (h, v, vcm, m, zs) in pts.items():
        sel = h > 1
        y1, y2 = lcl_tf(h[sel] * F_GRID, l1, cf, l2, r1=3e-3, r2=1e-3, rd=rd, cd=cd)
        i1h = np.abs(y1) * v[sel]
        w = 2 * math.pi * h[sel] * F_GRID
        icm_tot = np.abs(vcm[sel] / (1j * w * l1 / 3 + 1.0 / (1j * w * 3 * cf)))
        dm_rms = math.sqrt(np.sum(i1h[h[sel] > 50] ** 2) / 2)
        cm_rms_phase = math.sqrt(np.sum((icm_tot[h[sel] > 50] / 3) ** 2) / 2)
        rip[vdc] = {"dm_hf_rms_A": dm_rms, "cm_hf_rms_per_phase_A": cm_rms_phase, "hf_total_rms_A": math.hypot(dm_rms, cm_rms_phase),
                    "cm_fsw_V": float(vcm[h == int(round(FSW_CHOICE / F_GRID))][0]), "cm_total_rms_A": math.sqrt(np.sum(icm_tot[h[sel] > 50] ** 2) / 2)}
    res["ripple"] = rip
    # time-domain L1 ripple at 950 V with the C_f star tied to the midpoint: L1 sees leg-vs-midpoint minus its fundamental (C_f ~ short
    # at f_sw); ripple = integral / L1, low-frequency part removed with a one-carrier-period moving average; largest pp per carrier period
    vdc = 950.0
    m950, zs950 = pts[vdc][3], pts[vdc][4]
    t, legs, _ = pwm_wave(LEV, m950, FSW_CHOICE, zs950, n=2 ** 18)
    vfund = np.real(np.fft.irfft(np.where(np.arange(len(t) // 2 + 1) == 1, np.fft.rfft(legs[0]), 0), len(t)))
    di = np.cumsum((legs[0] - vfund) * vdc / 2) * (t[1] - t[0]) / l1
    nc = len(t) // int(round(FSW_CHOICE / F_GRID))
    di = di - np.convolve(np.concatenate([di[-nc:], di, di[:nc]]), np.ones(nc) / nc, mode="same")[nc:-nc]
    res["ripple_pp_950V_A"] = float(max(np.ptp(di[k:k + nc]) for k in range(0, len(t) - nc, nc)))
    res["ripple_pp_formula_A"] = 950.0 / ((8 if LEV == 3 else 4) * FSW_CHOICE * l1)
    res["levels"] = LEV
    # ---- light load: capacitor reactive power, inrush at connection
    res["Qcf_var"] = {f"{vll:.0f} V": 3 * 2 * math.pi * F_GRID * cf * (vll / math.sqrt(3)) ** 2 for vll in (V_LL * (1 - V_TOL), V_LL, V_LL * (1 + V_TOL))}
    res["Icf_A"] = 2 * math.pi * F_GRID * cf * V_LL * (1 + V_TOL) / math.sqrt(3)
    vpk = V_LL * (1 + V_TOL) * math.sqrt(2.0 / 3.0)
    z0 = math.sqrt(l2 / cf)
    res["inrush"] = {"unsynchronised_C_f_empty_peak_A": vpk / z0 * 2 / 2 + 0.0,
                     "synchronised_5pct_5deg_peak_A": abs(vpk * (1 - 0.95 * complex(math.cos(math.radians(5)), math.sin(math.radians(5))))) / z0,
                     "Z0_ohm": z0, "f_L2Cf_Hz": 1 / (2 * math.pi * math.sqrt(l2 * cf))}
    # ---- inductor electrical requirements (to the magnetics engineer)
    r750 = rip[750.0]
    r950 = rip[950.0]
    ipk_cont = I_CONT * math.sqrt(2) + res["ripple_pp_950V_A"] / 2
    ipk_2min = I_2MIN * math.sqrt(2) + res["ripple_pp_950V_A"] / 2
    ipk_200 = I_200MS * math.sqrt(2) + res["ripple_pp_950V_A"] / 2
    b = BUDGET
    SPEC["inductors"] = {
        "basis": "CALCULATED by sim/pcs_design.py step c (PWM spectrum + LCL transfer); loss = BUDGET the design must meet",
        "L1": {"function": "converter-side filter inductor, one per phase (three-wire 3, four-wire 3 + L_N)",
               "inductance_uH": round(l1 * 1e6, 1), "tolerance": "+-10 % at 0 A",
               "L_vs_I_min_uH": {"0 A": round(0.9 * l1 * 1e6, 1), f"{ipk_cont:.0f} A (110 % peak + ripple)": round(0.85 * l1 * 1e6, 1),
                                  f"{ipk_2min:.0f} A (120 %)": round(0.80 * l1 * 1e6, 1), f"{ipk_200:.0f} A (200 ms overload)": round(0.65 * l1 * 1e6, 1),
                                  f"{OC_TRIP:.0f} A (hardware trip)": round(0.5 * l1 * 1e6, 1)},
               "current": {"fundamental_rms_A": {"rated": I_RATED, "continuous": I_CONT, "2_min": I_2MIN, "200_ms": I_200MS},
                           "hf_rms_A_750V": round(r750["hf_total_rms_A"], 1), "hf_rms_A_950V": round(r950["hf_total_rms_A"], 1),
                           "of_which_common_mode_rms_A_950V": round(r950["cm_hf_rms_per_phase_A"], 1),
                           "ripple_pp_max_A": round(res["ripple_pp_950V_A"], 1), "peak_A": {"110 %": round(ipk_cont), "120 %": round(ipk_2min), "200 ms": round(ipk_200)}},
               "ripple_frequency": f"carrier groups at {FSW_CHOICE/1e3:.0f} kHz (sidebands +-2, +-4 x f0) and {2*FSW_CHOICE/1e3:.0f} kHz; switch-node "
                                   + ("steps of V_dc/2 (<= 525 V) at up to 30 V/ns" if LEV == 3 else "steps of V_dc (<= 1050 V) at up to 60 V/ns (step e)"),
               "volt_seconds": (f"max per half carrier period (V_dc/2)/(4 f_sw) = {950/2/(4*FSW_CHOICE)*1e3:.2f} mVs at 950 V" if LEV == 3 else
                                f"max per half carrier period (V_dc/2)/(2 f_sw) = {950/2/(2*FSW_CHOICE)*1e3:.2f} mVs at 950 V"),
               "loss_budget_W": {"copper_at_180A": b["L1_cu_W_per_phase_180A"], "copper_model": "P_cu = R_eq(50 Hz) I^2 + HF part, R_eq <= %.2f mOhm" % (b["L1_cu_W_per_phase_180A"] / I_RATED ** 2 * 1e3),
                                 "core_and_gap_at_750V": b["L1_core_W_per_phase_750V"], "core_scaling": "~(V_dc / 750 V)^2 at the same f_sw (ripple flux ~ V_dc)",
                                 "total_at_125kW_750V": b["L1_cu_W_per_phase_180A"] + b["L1_core_W_per_phase_750V"]},
               "insulation": "winding to core/PE: basic, DC-side system (1000 V DC, OVC II: 6 kV impulse / 2.2 kV rms test as ARCHITECTURE-PCS section 3), "
                             "partial-discharge free at 1.5 x the 1.0 kV peak working voltage (switch node vs PE: V_dc/2 + overshoot); turn-to-turn "
                             "and first-turn stress for " + ("525 V steps at 30 V/ns" if LEV == 3 else "1050 V steps at 60 V/ns"),
               "thermal": "class F (155 C) system; hot spot <= 140 C at 60 C inlet air (ASSUMED limit); placed downstream of the heatsink",
               "core_suggestion": "amorphous C-core (AT&M 1K107 class) with distributed gaps, Cu strip - the magnetics engineer decides"},
        "L2": {"function": "grid-side filter inductor, one per phase", "inductance_uH": round(l2 * 1e6, 1), "tolerance": "+-10 % at 0 A",
               "L_vs_I_min_uH": {"0 A": round(0.9 * l2 * 1e6, 1), f"{I_2MIN*math.sqrt(2):.0f} A": round(0.8 * l2 * 1e6, 1), f"{I_200MS*math.sqrt(2):.0f} A": round(0.6 * l2 * 1e6, 1)},
               "current": {"fundamental_rms_A": {"rated": I_RATED, "continuous": I_CONT, "2_min": I_2MIN, "200_ms": I_200MS},
                           "hf_rms_A": "< 0.5 % of rated (attenuated by C_f)"},
               "loss_budget_W": {"total_at_180A": b["L2_W_per_phase_180A"], "copper_model": "R_eq(50 Hz) <= %.2f mOhm" % (b["L2_W_per_phase_180A"] / I_RATED ** 2 * 1e3)},
               "insulation": "mains side, OVC III: basic to PE 4 kV impulse (6 kV if the AC SPD is not credited), 1.5 kV rms test",
               "thermal": "class F", "core_suggestion": "powder (Chinese FeSiAl / POCO class) or Si-steel; 50 Hz dominated"},
        "LN_four_wire": {"function": "neutral-leg inductor of the four-wire version", "inductance_uH": round(l1 * 1e6, 1),
                         "current": {"rms_A_100pct_unbalance": I_2MIN, "note": "single-phase full load returns 100 % of the phase current"},
                         "as": "L1 (same electrical requirement)"},
    }
    if FP:                              # the parts sim/pcs_tradeoff.py selected with sim/magnetics.py (cheapest meeting the efficiency rules)
        for key in ("L1", "L2"):
            x = FP[key]
            SPEC["inductors"][key]["selected_part"] = {k: x[k] for k in x if k not in ("loss_per_phase",)}
            SPEC["inductors"][key]["loss_budget_W"]["note"] = (
                "superseded: the efficiency rules of sim/pcs_tradeoff.py (peak >= 98.7 %, full load >= 98.0 % at 600-900 V) set the "
                f"loss; selected part {x.get('P_180A_750V_W', x.get('P_180A_W')):.0f} W at 180 A")
        SPEC["inductors"]["L1"]["core_suggestion"] = (f"selected: gapped {FP['L1']['mat']} C-core, {FP['L1']['wire']}, {FP['L1']['N']} turns "
                                                      "(sim/pcs_tradeoff.py + sim/magnetics.py); the magnetics engineer confirms")
        SPEC["inductors"]["AC_CM_choke"] = FP["cm_choke"]
    SPEC["lcl"] = res
    return res


def report_c(r):
    F = r["filter"]
    wr("\n## (c) Switching frequency and LCL filter\n")
    wr(f"**f_sw = {FSW_CHOICE/1e3:.0f} kHz**, control sampled at {F_S/1e3:.0f} kHz (double update), delay 1.5 T_s = {T_DELAY*1e6:.0f} us.  "
       f"**L1 {F['L1']*1e6:.0f} uH ({r['pu']['L1']*100:.1f} %), C_f {F['Cf']*1e6:.0f} uF per phase in star "
       f"(Q {r['pu']['Cf_Q_frac']*100:.1f} % of 125 kVA), L2 {F['L2']*1e6:.0f} uH ({r['pu']['L2']*100:.1f} %)**, passive branch "
       f"R_d {F['Rd']:.0f} ohm + C_d {F['Cd']*1e6:.0f} uF per phase across C_f, plus capacitor-current active damping.  "
       + (f"L1, C_f and L2 are the cheapest compliant set of the re-optimisation with the real inductor designs (section h, "
          f"sim/pcs_tradeoff.py): ripple V_dc/({8 if r['levels'] == 3 else 4} f L1) = {r['ripple_pp_formula_A']:.0f} A pp at 950 V "
          f"({r['ripple_pp_950V_A']:.0f} A from the time-domain PWM waveform); C_f and L2 place the stiff-grid resonance below f_s/6 = "
          f"{r['f_crit_Hz']/1e3:.1f} kHz (capacitor-current active damping), the SCR-5 resonance above twice the current-loop bandwidth, and "
          f"keep every carrier-band component of the grid current within the 0.3 % target." if FP else
          f"L1 from the ripple rule (25 % peak-to-peak of the 216 A peak at 950 V: V_dc/({8 if r['levels'] == 3 else 4} f L1) = "
          f"{r['ripple_pp_formula_A']:.0f} A, {r['ripple_pp_950V_A']:.0f} A pp from the time-domain PWM waveform); C_f and L2 place the "
          f"resonance where capacitor-current active damping works (below f_s/6 = {r['f_crit_Hz']/1e3:.1f} kHz) for every grid and keep the "
          f"carrier band several times below the 0.3 % target."))
    if FP:
        L1p, L2p, cm = FP["L1"], FP["L2"], FP["cm_choke"]
        wr(f"\n**Inductor parts (sim/magnetics.py, selected in sim/pcs_tradeoff.py):** L1 gapped {CORE_WORD[L1p['mat']]} C-core, {L1p['wire']}, "
           f"{L1p['N']} turns, {L1p['mass_kg']:.1f} kg, {L1p['usd'][0]:.0f} / {L1p['usd'][1]:.0f} USD (catalogue / 5k), {L1p['P_180A_750V_W']:.0f} W "
           f"at 180 A / 750 V and {L1p['P_198A_950V_W']:.0f} W at 198 A / 950 V (higher of own model and OpenMagnetics), hot spot "
           f"{L1p['T_hot_60C']:.0f} C at 60 C inlet; L2 {CORE_WORD[L2p['mat']]} C-core, {L2p['wire']}, {L2p['N']} turns, {L2p['mass_kg']:.1f} kg, "
           f"{L2p['usd'][0]:.0f} / {L2p['usd'][1]:.0f} USD, {L2p['P_180A_W']:.0f} W at 180 A; AC CM choke {cm['L_cm_uH']:.0f} uH = {cm['k']} x "
           f"Yunlu N-R-564440, {cm['usd'][0]:.0f} / {cm['usd'][1]:.0f} USD.  Their losses replace the step-c budgets in every efficiency "
           f"figure (per phase a(V_dc) + k I^2).  All ESTIMATES: no quote, no sample.")
    wr("\n| grid | f_res (kHz) |")
    wr("|---|---|")
    for k, v in r["f_res_Hz"].items():
        wr(f"| {k} | {v/1e3:.2f} |")
    wr(f"\nResonance stays between {min(r['f_res_Hz'].values())/1e3:.1f} and {max(r['f_res_Hz'].values())/1e3:.1f} kHz: below f_s/6, "
       f"where L1-current feedback with the 1.5 T_s delay is inherently damped, and above twice a {BW_LOOP/1e3:.2f} kHz current-loop bandwidth - one design "
       f"covers SCR 5 to a stiff grid.  The passive branch alone lowers "
       f"the resonance peak from {r['peak_gain_undamped_dB']:.0f} to {r['peak_gain_passive_branch_dB']:.0f} dB (relative to the L1 admittance at "
       f"1 kHz): it is the fallback if active damping is lost, not the main damping.")
    wr(f"\n**Limits used (standard texts NOT on file - from memory):** IEEE 1547-2018 Table 26/27 odd-harmonic limits 4.0 / 2.0 / 1.5 / 0.6 "
       f"/ 0.3 % (h < 11 / < 17 / < 23 / < 35 / < 50), even 1 / 2 / 3 % for h 2 / 4 / 6 and 25 % of the odd limit above, TRD 5 %; the product's "
       f"THDi < 3 %; above h50 (where no standard on file sets a limit; EN 50549-1 refers to the EN 61000-3 series, GB/T 34120 sets a THD) the "
       f"0.3 % per component is our design target.  Megarevo cites EN 50549-1/-10, GB/T 34120/34133 (pma_user-manual).\n")
    wr("| V_dc | grid | m | PWM | THDi h2-h50 from PWM | THDi incl. carrier band | largest component above h50 | limit violations |")
    wr("|---|---|---|---|---|---|---|---|")
    for x in r["spectrum"]:
        wr(f"| {x['vdc']:.0f} | {x['grid']} | {x['m']:.2f} | {x['zs']} | {x['THD_h2_50_pwm']*100:.3f} % | {x['THD_all_pwm']*100:.3f} % | "
           f"h{x['largest_above_h50'][0]}: {x['largest_above_h50'][1]*100:.3f} % | {len(x['violations'])} |")
    wr(f"\nThe PWM itself leaves THDi far below 3 %; the THDi at rated power will be set by the controller (dead time {T_DEAD['sic']*1e9:.0f} ns "
       f"at {FSW_CHOICE/1e3:.0f} kHz = a {2*T_DEAD['sic']*FSW_CHOICE*100:.1f} % volt-second error before compensation, sensor offsets, grid "
       f"background distortion, the loop gain at h5-h13): budget 2.5 % for those, to be verified by the control simulation (item 3 of "
       f"ARCHITECTURE-PCS section 9).")
    r9 = r["ripple"][950.0]
    wr(f"\n**Common mode.** With the C_f star tied to the DC midpoint (the architecture's choice, and necessary - step f), the carrier "
       f"harmonic of the {'three' if r['levels'] == 3 else 'two'}-level common-mode voltage ({r9['cm_fsw_V']:.0f} V peak at {FSW_CHOICE/1e3:.0f} kHz, 950 V) drives "
       f"{r9['cm_total_rms_A']:.0f} A rms through the three L1 in parallel, C_f and the DC midpoint ({r9['cm_hf_rms_per_phase_A']:.0f} A per "
       f"phase): it is part of the L1 ripple ({r9['hf_total_rms_A']:.0f} A rms HF per phase in total, DM {r9['dm_hf_rms_A']:.0f} A) and of the "
       f"DC-link ripple (step d).  The architecture document did not count it.")
    wr(f"\n**Light load:** C_f draws {r['Icf_A']:.1f} A capacitive at 460 V ("
       + ", ".join(f"{k}: {v/1e3:.2f} kvar" for k, v in r["Qcf_var"].items())
       + ") - the current controller compensates it at the grid terminals (PF -1..+1 control), no switched capacitor is needed.  "
       f"**Inrush:** connecting to the grid with C_f empty and the inverter off would ring L2-C_f at {r['inrush']['f_L2Cf_Hz']/1e3:.1f} kHz with "
       f"{r['inrush']['unsynchronised_C_f_empty_peak_A']:.0f} A peak (Z0 {r['inrush']['Z0_ohm']*1e3:.0f} mOhm) - forbidden: the sequence is DC "
       f"precharge, inverter builds the grid voltage on C_f, synchronise (|dV| <= 5 %, 5 deg), then close; the residual step gives "
       f"{r['inrush']['synchronised_5pct_5deg_peak_A']:.0f} A peak, which the current loop takes over within a few control periods.")
    wr("\nThe electrical requirement of L1, L2 and L_N (inductance versus current, rms / peak / ripple, frequency, loss budget, insulation) is "
       "written to pcs_spec.json under \"inductors\" for the magnetics engineer.")


# ------------------------------------------------------------------------------------------------ (d) DC link
# Faratronic C3D film (passives-capacitors/Faratronic-C3D.pdf, Q/FRK 0.GS.C.C3D-F10 2015-07): U_N at 70 C hot spot, 85 C rating, ESR at 10 kHz,
# I_max at 10 kHz / 70 C ambient / 15 K case rise (p11 note 5); life 100,000 h at U_N and 70 C hot spot (p3); life curve p12 (Uw/U_N vs h at
# 70 / 85 / 105 C hot spot); U_pp <= 0.1 U_N, case rise <= 15 K (p12); 1.1 U_N 30 % of on-load time, 1.15 U_N 30 min/day, 1.2 U_N 5 min/day (p12)
FILM = {"C3D1U147": {"C": 140e-6, "un70": 600.0, "un85": 500.0, "esr": 3.0e-3, "imax": 40.2, "mm": (57, 65, 45), "page": 8},
        "C3D2K117": {"C": 110e-6, "un70": 800.0, "un85": 700.0, "esr": 3.5e-3, "imax": 34.5, "mm": (57, 65, 45), "page": 11}}
FILM_PRICE = {"cat": 4.90, "5k": 4.17}     # ESTIMATE (architect: C3D1X505 0.557 USD @1000 on LCSC scaled by can volume 167 cm3); no quote
FILM_LIFE = {70.0: [(1000, 1.2), (20000, 1.2), (100000, 1.0)], 85.0: [(1000, 1.0), (20000, 1.0), (100000, 0.84)],
             105.0: [(1000, 0.7), (10000, 0.7), (20000, 0.6), (50000, 0.5)]}      # p12 typical curve (Uw/U_N, read off)
# electrolytic reference: Samyoung TDC (KR), LCSC C2835739 TDC400V470M35*35: 400 V 470 uF, 1.8 A at 120 Hz / 105 C, x1.41 at >= 10 kHz,
# 2,000 h at 105 C (datasheet on LCSC, 3 pages, not filed: technology comparison only); 3.74 USD @1000
ELCO = {"part": "Samyoung TDC400V470M35*35 V", "C": 470e-6, "ur": 400.0, "i_120": 1.8, "f_mult_hf": 1.41, "life_105": 2000.0,
        "price": {"cat": 6.35, "5k": 3.74}, "src": "LCSC C2835739 datasheet (Samyoung TDC series p1-p2), price LCSC via jlcsearch 2026-10-04"}


def film_life(u_frac, t_hs):
    """hours from the p12 curve: log-linear in hours between the read points, linear in T between 70/85/105 C (worst-case side)"""
    def at(t):
        pts = FILM_LIFE[t]
        hs = np.log10([p[0] for p in pts])
        us = np.array([p[1] for p in pts])
        if u_frac <= us[-1]:
            return 10 ** (hs[-1] + (us[-1] - u_frac) / max(us[-2] - us[-1], 1e-9) * (hs[-1] - hs[-2])) if t != 70.0 else 1e5 * (1.0 + (1.0 - u_frac) * 5)
        k = np.where(us >= u_frac)[0][-1]
        if k + 1 >= len(us):
            return 10 ** hs[k]
        return 10 ** (hs[k] + (us[k] - u_frac) / (us[k] - us[k + 1]) * (hs[k + 1] - hs[k]))
    ts = sorted(FILM_LIFE)
    if t_hs <= ts[0]:
        return at(ts[0])
    for a, b in zip(ts, ts[1:]):
        if t_hs <= b:
            la, lb = math.log10(at(a)), math.log10(at(b))
            return 10 ** (la + (lb - la) * (t_hs - a) / (b - a))
    return at(ts[-1]) * 0.5 ** ((t_hs - ts[-1]) / 10.0)


def dc_currents(vdc, i_rms, ang, l_tot, n=2 ** 16, zs=None, levels=3, fsw=None):
    """rail currents of the three legs over one period from the PWM switching functions and the phase currents (fundamental):
    i_P / i_O / i_N into the converter; capacitor currents of the upper / lower half with the battery as a smooth current source
    (two-level: no O state, i_O = 0)"""
    m, phi, p, q = op_point(vdc, V_LL, i_rms, ang, l_tot)
    zs = zs or ("none" if m <= 0.98 else "minmax")
    t, legs, _ = pwm_wave(levels, m, fsw or FSW_CHOICE, zs, n=n)
    th = 2 * math.pi * F_GRID * t
    ix = np.stack([i_rms * math.sqrt(2) * np.sin(th - k * 2 * math.pi / 3 - phi) for k in range(3)])
    i_p = np.sum(np.where(legs > 0.5, ix, 0.0), 0)
    i_n = np.sum(np.where(legs < -0.5, ix, 0.0), 0)
    i_o = -(i_p + i_n)
    idc = float(np.mean(i_p))
    ic1, ic2 = idc - i_p, idc + i_n
    def split(x):
        X = np.fft.rfft(x) / len(x)
        lf = np.sqrt(2 * np.sum(np.abs(X[1:41]) ** 2))
        hf = np.sqrt(2 * np.sum(np.abs(X[41:]) ** 2))
        return lf, hf, X
    c1 = split(ic1)
    c2 = split(ic2)
    o = split(i_o)
    return {"m": m, "zs": zs, "idc": idc, "ic1_lf": c1[0], "ic1_hf": c1[1], "ic2_lf": c2[0], "ic2_hf": c2[1],
            "io_lf": o[0], "io_spec": o[2], "io_mean": float(np.mean(i_o)), "t": t}


def step_d(D, rc):
    l_tot = D["l1"] + D["l2"]
    cases = [(vdc, ang) for vdc in (600.0, 750.0, 950.0) for ang in (0.0, 180.0, 90.0)]
    part = "C3D1U147"
    f = FILM[part]
    U_PP_MAX = 0.1 * f["un70"]          # C3D p12: ripple peak-to-peak <= 0.1 U_N

    def mid_pp(r, c_half):
        X = r["io_spec"].copy()
        h = np.arange(len(X))
        X[0] = 0
        X[41:] = 0
        V = np.where(h > 0, X / (1j * 2 * math.pi * F_GRID * np.maximum(h, 1) * 2 * c_half), 0)
        return float(np.ptp(np.fft.irfft(V * len(r["t"]), len(r["t"]))))

    def bank(zs_mode):
        rows = []
        for vdc, ang in cases:
            lev_ = TOPO[D["topo"]]["levels"]
            zs_ = {"policy": zs_policy(lev_, op_point(vdc, V_LL, I_CONT, ang, l_tot)[0]), "minmax": "minmax"}.get(zs_mode)
            r = dc_currents(vdc, I_CONT, ang, l_tot, zs=zs_, levels=lev_)
            cm_half = rc["ripple"][min(rc["ripple"], key=lambda v: abs(v - vdc))]["cm_total_rms_A"] / 2
            r["cm_half"] = cm_half
            if r["zs"] == "minmax":
                # 150 Hz (and triplen) zero sequence -> L1/3 + 3 C_f -> C_f star -> DC midpoint: an extra low-frequency midpoint current
                _, va = references(r["m"], "minmax", n=len(r["t"]))
                v0 = (va - r["m"] * np.sin(2 * math.pi * F_GRID * r["t"])) * vdc / 2
                V0 = np.fft.rfft(v0) / len(v0)
                hh = np.arange(len(V0))
                w = 2 * math.pi * F_GRID * np.maximum(hh, 1)
                I0 = np.where((hh > 0) & (hh <= 40), V0 / (1j * w * D["l1"] / 3 + 1 / (1j * w * 3 * rc["filter"]["Cf"])), 0)
                r["io_spec"] = r["io_spec"] + I0
                r["cf_cm_lf_A"] = float(np.sqrt(2 * np.sum(np.abs(I0) ** 2)))
            r["ic1_tot"] = math.sqrt(r["ic1_lf"] ** 2 + r["ic1_hf"] ** 2 + cm_half ** 2)
            r["ic2_tot"] = math.sqrt(r["ic2_lf"] ** 2 + r["ic2_hf"] ** 2 + cm_half ** 2)
            r["vdc"], r["ang"] = vdc, ang
            rows.append(r)
        worst = max(rows, key=lambda r: max(r["ic1_tot"], r["ic2_tot"]))
        i_half = max(worst["ic1_tot"], worst["ic2_tot"])
        n_i = math.ceil(i_half / (pv.CAP_IRMS_USE * f["imax"]))
        n = max(n_i, 4)
        while max(mid_pp(r, n * f["C"]) for r in rows) > U_PP_MAX and n < 400:
            n += 1
        return {"rows": rows, "worst": worst, "i_half": i_half, "n_i": n_i, "n": n}

    B = bank("policy")
    S = bank("none")
    rows, worst, i_half, n_i, n = B["rows"], B["worst"], B["i_half"], B["n_i"], B["n"]
    c_half = n * f["C"]
    mid = {f"{r['vdc']:.0f} V / {r['ang']:.0f} deg": mid_pp(r, c_half) for r in rows}
    sin_alt = {"per_half": S["n"], "C_half_mF": S["n"] * f["C"] * 1e3, "cost_usd": {k: 2 * S["n"] * v for k, v in FILM_PRICE.items()},
               "midpoint_lf_A_rms_max": max(r["io_lf"] for r in S["rows"])}
    # hot spot and life: capacitor air = inlet + 5 K (upstream of the heatsink, ESTIMATE); case rise 15 K x (I / I_max)^2 (p11 note 5)
    i_cap = i_half / n
    dT = 15.0 * (i_cap / f["imax"]) ** 2
    life = {}
    for t_in in (T_IN, T_IN_HOT):
        ths = t_in + 5.0 + dT
        for vdc in (750.0, 900.0, 950.0):
            life[f"{t_in:.0f}C_{vdc:.0f}V"] = {"T_hs_C": ths, "Uw_over_UN": vdc / 2 / f["un70"], "life_h": film_life(vdc / 2 / f["un70"], ths)}
    p_esr = 2 * n * (i_cap ** 2) * f["esr"]
    # electrolytic alternative: two 400 V cans in series per half; parallel strings for the ripple current (HF rating x1.41)
    i_can = ELCO["i_120"] * ELCO["f_mult_hf"]
    n_str = math.ceil(i_half / i_can)
    elco_cans = 2 * 2 * n_str
    elco_life = {f"{t_in:.0f}C": ELCO["life_105"] * 2 ** ((105.0 - (t_in + 5.0 + 20.0)) / 10.0) for t_in in (T_IN, T_IN_HOT)}
    # 4-wire: a THREE-level phase leg under unbalance pushes (1 - 8m/(3 pi)) x I of 50 Hz into the midpoint (fundamental of (1 - m|sin|) sin);
    # a two-level leg has no O state; the fourth (neutral) leg of either draws the neutral current from P and N
    lev = TOPO[D["topo"]]["levels"]
    m4 = V_LL * math.sqrt(2 / 3) / 475.0 * 1.03
    io_unbal = (1 - 8 * m4 / (3 * math.pi)) * I_2MIN * math.sqrt(2)
    dv_unbal = io_unbal / (2 * math.pi * F_GRID * 2 * c_half)
    i_bat_100 = P_RATED / 3 / 750.0 / math.sqrt(2)      # 100 Hz battery current of a single-phase full load (p = P/3 (1 - cos 2wt)) at 750 V
    # bleeder / discharge: per half R_b, to 60 V
    r_b = 440e3
    tau = r_b * c_half
    t60 = tau * math.log(475.0 / 60.0)
    res = {"part": part, "maker": "Xiamen Faratronic (CN)", "per_half": n, "total": 2 * n, "C_half_uF": c_half * 1e6,
           "C_series_uF": c_half / 2 * 1e6, "worst_case": {"vdc": worst["vdc"], "ang": worst["ang"], "I_half_rms_A": i_half,
                                                          "lf_A": max(worst["ic1_lf"], worst["ic2_lf"]), "hf_A": max(worst["ic1_hf"], worst["ic2_hf"]),
                                                          "cm_from_Cf_star_A": worst["cm_half"]},
           "rows": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items() if k in ("vdc", "ang", "m", "zs", "idc", "ic1_lf", "ic1_hf", "ic2_lf", "ic2_hf", "io_lf", "cm_half", "ic1_tot", "ic2_tot")} for r in rows],
           "n_for_ripple_current": n_i, "I_per_cap_A": i_cap, "case_rise_K": dT, "esr_loss_W": p_esr, "midpoint_pp_V": mid,
           "life": life, "voltage_use": {"Uw_950V_over_UN70": 475.0 / f["un70"], "Uw_band_over_UN70": 522.5 / f["un70"],
                                         "OV_trip_per_half_V": 560.0, "OV_trip_over_UN70": 560.0 / f["un70"]},
           "electrolytic_alternative": {"part": ELCO["part"], "strings_per_half": n_str, "cans": elco_cans, "life_h": elco_life,
                                        "cost_usd": {k: elco_cans * v for k, v in ELCO["price"].items()}, "note": "2 x 400 V in series per half + balancing resistors"},
           "film_cost_usd": {k: 2 * n * v for k, v in FILM_PRICE.items()}, "sinusoidal_pwm_alternative": sin_alt,
           "modulation": "min-max zero sequence" if TOPO[D["topo"]]["levels"] == 3 or MOD_2L == "minmax" else "sinusoidal, min-max only where m > 0.98",
           "U_pp_max_V": U_PP_MAX, "n_for_midpoint": n,
           "four_wire_unbalance": {"levels": lev, "io_50Hz_peak_A_if_three_level": io_unbal, "midpoint_50Hz_ripple_V_peak_if_three_level": dv_unbal,
                                   "battery_100Hz_A_rms_single_phase_full_load_750V": i_bat_100},
           "cf_star_lf_cm_A_max": max(r.get("cf_cm_lf_A", 0.0) for r in rows), "levels": lev,
           "three_level_per_half": DCL_CACHE.get((3, FSW_CHOICE, round(l1_for_ripple({"topo": "TT", "fsw": FSW_CHOICE}) * 1e7)), {}).get("per_half"),
           "bleeder": {"R_per_half_ohm": r_b, "P_W_per_half_950V": 475.0 ** 2 / r_b, "t_to_60V_s": t60}}
    SPEC["dc_link"] = res
    return res


def report_d(r):
    w = r["worst_case"]
    lev = r["levels"]
    wr("\n## (d) DC link\n")
    wr(f"**Split film bank: {r['per_half']} + {r['per_half']} x Faratronic {r['part']}** (140 uF, U_N 600 V at 70 C / 500 V at 85 C, ESR "
       f"3.0 mOhm, I_max 40.2 A, Faratronic-C3D.pdf p8): {r['C_half_uF']:.0f} uF per half, {r['C_series_uF']:.0f} uF across 950 V.  "
       + ("Two-level legs do not use the midpoint; it exists for the C_f star (the common-mode path, steps c and f), so the halves are "
          "sized by the ripple current: " if lev == 2 else
          f"The count is the larger of the ripple-current rule ({r['n_for_ripple_current']} per half) and the midpoint rule (150 Hz "
          f"ripple <= 0.1 U_N = {r['U_pp_max_V']:.0f} V pp, C3D p12; {r['n_for_midpoint']} per half).  Ripple current: ")
       + f"worst half-bank {w['I_half_rms_A']:.0f} A rms at {w['vdc']:.0f} V / PF angle {w['ang']:.0f} (110 %): {w['lf_A']:.0f} A below 2 kHz, "
         f"{w['hf_A']:.0f} A carrier band, {w['cm_from_Cf_star_A']:.0f} A of the C_f-star common-mode current (step c) - "
         f"{r['I_per_cap_A']:.1f} A per capacitor = {r['I_per_cap_A']/40.2*100:.0f} % of I_max (project rule <= 70 %), case rise "
         f"{r['case_rise_K']:.1f} K, ESR loss {r['esr_loss_W']:.0f} W.")
    wr("\n| operating point (110 %) | m | PWM | I_dc (A) | upper half LF / HF (A rms) | lower half LF / HF | midpoint LF from the legs (A rms) |")
    wr("|---|---|---|---|---|---|---|")
    for x in r["rows"]:
        wr(f"| {x['vdc']:.0f} V, {x['ang']:.0f} deg | {x['m']:.2f} | {x['zs']} | {x['idc']:.0f} | {x['ic1_lf']:.0f} / {x['ic1_hf']:.0f} | "
           f"{x['ic2_lf']:.0f} / {x['ic2_hf']:.0f} | {x['io_lf']:.0f} |")
    wr(f"\n**Voltage use and life (calculated from the p12 life curve):** 475 V per half at 950 V = {r['voltage_use']['Uw_950V_over_UN70']:.2f} "
       f"U_N; 522 V at a 10 % midpoint deviation = {r['voltage_use']['Uw_band_over_UN70']:.2f} U_N (1.1 U_N is allowed for 30 % of the "
       f"on-load time, p12); the 560 V per-half trip = {r['voltage_use']['OV_trip_over_UN70']:.2f} U_N (1.15 U_N allowed 30 min/day).  Hot spot = "
       f"inlet + 5 K + case rise: " + "; ".join(f"{k}: {v['T_hs_C']:.0f} C, {v['life_h']/1e3:.0f} kh" for k, v in r["life"].items())
       + ".  The 800 V-class C3D2K117 of the architecture is not needed (0.59 U_N, 34.5 A, 110 uF in the same can).")
    wr(f"\n**Midpoint ripple (110 %, SVPWM; including the 150 Hz common-mode current through C_f, up to {r['cf_star_lf_cm_A_max']:.0f} A "
       f"rms):** " + ", ".join(f"{k} {v:.1f} V" for k, v in r["midpoint_pp_V"].items()) + f" peak-to-peak - within {r['U_pp_max_V']:.0f} V.  "
       + ("The two-level legs need no midpoint control; static balance by the bleeders, a slow firmware check of the half voltages."
          if lev == 2 else "The average is held by the controller's zero-sequence offset - control-engineer item."))
    if lev == 2:
        wr(f"\n**Three-level contrast (why the topology changed):** a three-level leg draws a 150 Hz midpoint current that no zero sequence "
           f"cancels at PF 0 and 600-750 V (np_residual): {r['three_level_per_half']} capacitors per half instead of {r['per_half']} "
           f"(step a).  The architecture's 660 uF per half relied on a midpoint control that cannot work at PF 0.")
    fw = r["four_wire_unbalance"]
    wr(f"\n**Four-wire:** the fourth (neutral) leg is one more two-level half-bridge; it takes the neutral current from P and N, the "
       f"bank sees no 50 Hz midpoint current (a T-type phase leg would push {fw['io_50Hz_peak_A_if_three_level']:.0f} A peak into the "
       f"midpoint under 100 % unbalance - {fw['midpoint_50Hz_ripple_V_peak_if_three_level']:.0f} V peak on this bank).  A single-phase full "
       f"load pulsates at 100 Hz: about {fw['battery_100Hz_A_rms_single_phase_full_load_750V']:.0f} A rms at 750 V flows into the battery "
       f"(installation requirement, as the architecture said).")
    e = r["electrolytic_alternative"]
    wr(f"\n**Film against aluminium electrolytic (calculated):** the bank is ripple-current-limited, not energy-limited.  Electrolytics "
       f"({e['part']}, 400 V, 2 in series per half with balancing) carry {ELCO['i_120']*ELCO['f_mult_hf']:.2f} A each at >= 10 kHz: "
       f"{e['strings_per_half']} strings per half = {e['cans']} cans, {e['cost_usd']['cat']:.0f} / {e['cost_usd']['5k']:.0f} USD against "
       f"{r['film_cost_usd']['cat']:.0f} / {r['film_cost_usd']['5k']:.0f} USD of film (estimate), and a life of "
       + ", ".join(f"{k} inlet {v/1e3:.0f} kh" for k, v in e["life_h"].items()) + " (2,000 h at 105 C, x2 per 10 K, 20 K self-heating ESTIMATE) "
       f"against > 100 kh for film.  Film only.")
    b = r["bleeder"]
    wr(f"\n**Discharge:** bleeder {b['R_per_half_ohm']/1e3:.0f} kOhm per half (also the static balance), {b['P_W_per_half_950V']:.2f} W per half "
       f"at 950 V: 475 V -> 60 V in {b['t_to_60V_s']/60:.1f} min - label 'wait 10 min' needs the active path: the DC-side precharge relay "
       f"and resistor of the lean port discharge the bank in seconds when the firmware commands it (port_spec lean); the bleeder is the "
       f"passive backstop.")


# ------------------------------------------------------------------------------------------------ (e) commutation, gate drive, protection
CAL_JSON = os.path.join(HERE, "out", "pv_tradeoff", "vdmos_calibration.json")   # READ ONLY: VDMOS fits made for the PV design (same devices)
V_OV_TRIP = 1050.0            # V, DC over-voltage trip (discrete comparator on the control board, ADC limit as second layer, D-050)
# commutation loop, lumped equivalent for the six paralleled device pairs of one leg.  LAYOUT REQUIREMENT behind it: every high/low device
# pair gets its own local decoupling film at its pins, so each pair commutates its 1/6 share in a loop like the PV module's (20 nH for two
# devices = 40 nH per device); six in parallel = 6.7 nH lumped (design), 10 nH (sensitivity, 60 nH per device).  ESTIMATES, to be replaced
# by the extracted layout.  (A single 20-30 nH loop shared by all six - the first run of this step - gives 2.0-2.3 kV: not buildable.)
L_LOOP = {"design": 40e-9 / 6, "sensitivity": 60e-9 / 6}


def leg_model(n_pairs=6, l_scale=1.0):
    """physical leg for the commutation deck = the PV module's verified leg (pv_design.leg_net: C4AQ 3 x 2.2 uF decoupling, 15 nH bus,
    11.5 nH board + devices, damper 2 x 4.99 ohm + 2 x 4.7 nF in series) for every pair of devices, n_pairs/2 of them in parallel, on this
    product's bulk bank (5 + 5 x C3D1U147: 350 uF, ESL 2 x 52.5 nH / 5 - C3D p3 '< 1 nH per mm of lead spacing' - ESR 2 x 3 mOhm / 5).
    l_scale multiplies the bus and board inductances (sensitivity)."""
    k = n_pairs / 2.0
    dec = pv.C4AQ_DEC["C4AQUBU4220A1YJ"]
    return {"c_bulk": 350e-6, "esl_bulk": 2 * 52.5e-9 / 5, "esr_bulk": 2 * 3.0e-3 / 5, "l_bus": 15e-9 / k * l_scale,
            "c_dec": 3 * k * dec["C"], "esl_dec": dec["esl"] / (3 * k), "esr_dec": dec["esr"] / (3 * k),
            "l_rest": (20e-9 - pv.C4AQ_DEC["C4AQUBU4100A1WJ"]["esl"] / 2) / k * l_scale,
            "r_damp": 2 * 4.99 / k, "c_damp": 4.7e-9 / 2 * k, "decoupling_parts": int(3 * k), "damper_parts": int(4 * k)}
EOFF_MULT = {"value": 1.0}    # E_off(chosen R_G) / E_off(data-sheet R_G), set by step e; the efficiency is re-stated with it
EON_MULT = {"value": 1.0}     # E_on(drawn R_G,on) / E_on(data-sheet R_G) from the data sheet's E-vs-R_G curve, set by step e (D-057)


def pcs_dpt(d, npar, vdc, i_load, lloop, tag, rg, a, ton=1.0e-6, leg=None):
    """double-pulse commutation of one two-level leg: npar low-side VDMOS (pv_tradeoff.vdmos_card, calibrated C_gd shape 'a') switching,
    npar high-side devices held off at V_GS,off freewheeling the load current source; lumped loop L with pv_tradeoff's R_DAMP across it.
    Deck sim/spice/pcs_commutation_<tag>.cir.  Returns peak voltage, overshoot, di/dt, dv/dt, E_on, E_off."""
    os.makedirs(tr.SPICE, exist_ok=True)
    deck = os.path.join(tr.SPICE, f"pcs_commutation_{tag}.cir")
    t_on0, trs = 100e-9, 5e-9
    lines = [f"* PCS-P125 two-level leg commutation - {d['mfr']} SG2M040170HJ x{npar} per switch, {vdc:.0f} V, {i_load:.0f} A, "
             + (f"physical leg (bus {leg['l_bus']*1e9:.1f} nH, decoupling {leg['c_dec']*1e6:.1f} uF, board {leg['l_rest']*1e9:.2f} nH, damper "
                f"{leg['r_damp']:.2f} ohm + {leg['c_damp']*1e9:.1f} nF)" if leg else f"L_loop {lloop*1e9:.1f} nH")
             + f", R_G,eff {rg:.2f} ohm per device. Generated by sim/pcs_design.py (step e)",
             tr.vdmos_card("dut", d, a)] + tr._stage(vdc, lloop, leg, t_on0)
    if leg:              # pv_tradeoff's convention: R_DAMP (10 ohm) across every loop inductance (skin / eddy / ESR damping of the
        lines += [f"Rbusd bk lb {tr.R_DAMP}", f"Rlpd lb dtop {tr.R_DAMP}"]   # 50-100 MHz ringing) - also lets the six-pair deck start
    lines.append(f"Iload dtop mid {i_load}")
    lines += [f"Rclh{k} gh{k} ghd {tr.R_CLMPI}" for k in range(npar)]
    for k in range(npar):
        lines += [f"Mh{k} dtop gh{k} mid dut", f"Rgh{k} ghd gh{k} {rg}", f"Ml{k} mid gl{k} sl dut", f"Rgl{k} gld gl{k} {rg}"]
    lines += [f"Vgh ghd mid {d['vgs_off']}", "Vsense sl 0 0", f"Vgl gld 0 PULSE({d['vgs_off']} {d['vgs_on']} {t_on0} {trs} {trs} {ton} 1)"]
    vecs = "v(mid) v(dtop) i(Vsense) i(Lloop)"
    lines += [f".save {vecs}", f".tran 0.05n {t_on0 + ton + 0.6e-6} 0 0.05n", "OPTIONS", ".control", "run", f"linearize {vecs}",
              f"wrdata pcs_commutation_{tag}.dat {vecs}", "quit", ".endc", ".end"]
    for opt in tr.SOLVER_OPTIONS:
        open(deck, "w").write("\n".join(lines).replace("OPTIONS", opt) + "\n")
        try:
            tr.ngspice(deck, timeout=60)
            break
        except RuntimeError:
            continue
    else:
        raise RuntimeError(f"no solver setting converged for {deck}")
    t, (vds, vtop, ids, il) = tr.wrdata(os.path.join(tr.SPICE, f"pcs_commutation_{tag}.dat"))
    toff = t_on0 + trs + ton
    w_off, w_on = (t > toff) & (t < toff + 0.5e-6), (t > t_on0) & (t < t_on0 + 0.5e-6)
    vhs = vtop - vds
    return {"v_pk": float(max(vds[w_off].max(), vhs[w_on].max())), "os_off": float(vds[w_off].max() - vdc),
            "os_on": float(vhs[w_on].max() - vdc), "didt_A_per_ns": float(-np.gradient(il, t)[w_off].min() * 1e-9),
            "dvdt_V_per_ns": float(np.gradient(vds, t)[w_off].max() * 1e-9),
            "e_off_mJ": float(np.trapezoid((vds * ids)[w_off], t[w_off]) * 1e3), "e_on_mJ": float(np.trapezoid((vds * ids)[w_on], t[w_on]) * 1e3),
            "deck": os.path.relpath(deck, ROOT), "vdc": vdc, "i": i_load, "L_nH": (lloop or 0.0) * 1e9, "rg": rg}


def step_e(D, rc):
    part, n = D["devs"]["TH"]
    d = pv.MOSFETS[part]
    cal = json.load(open(CAL_JSON))[part]
    a, rg_cal = cal["a"], cal["rg_eff"]           # rg_cal reproduces the data-sheet E_off at R_G,ext = 2.5 ohm (p.5 test)
    ripple = rc["ripple_pp_950V_A"] / 2
    i_200 = I_200MS * math.sqrt(2) + ripple
    i_oc = OC_TRIP
    v_lim = pv.V_PK_FRAC * d["vdss"]
    sweep = []
    for k, dr in enumerate((0.0, 2.5, 5.0, 10.0)):              # added off-resistance per device
        r = pcs_dpt(d, n, V_OV_TRIP, i_oc, None, f"sweep{k}", rg_cal + dr, a, leg=leg_model(n, 1.5))
        r["rg_ext_off"] = 2.5 + dr
        sweep.append(r)
    ok = [r for r in sweep if r["v_pk"] <= v_lim]
    pick = min(ok, key=lambda r: r["rg"]) if ok else sweep[-1]
    rg = pick["rg"]
    nom = pcs_dpt(d, n, 950.0, i_200, None, "nominal", rg, a, leg=leg_model(n, 1.0))
    worst = pcs_dpt(d, n, V_OV_TRIP, i_oc, None, "worst", rg, a, leg=leg_model(n, 1.5))
    base = pcs_dpt(d, n, 950.0, i_200, None, "datasheet_rg", rg_cal, a, leg=leg_model(n, 1.0))
    eoff_factor = nom["e_off_mJ"] / max(base["e_off_mJ"], 1e-9)
    n_fans = 3 * HS["fans_per_section"]
    loss_b = 3 * evaluate(D, 750.0, V_LL, I_RATED, 0.0, T_IN)["leg_w"]
    rg_on = 3.75 if pick["rg_ext_off"] <= 2.5 else pick["rg_ext_off"] + 1.25       # drawn turn-on resistor per device
    EOFF_MULT["value"] = max(eoff_factor, 1.0)
    EON_MULT["value"] = pv.eon_rg_factor(d, rg_on)                                # D-057: E_on at the drawn R_G,on (x1.63 at 8.75 ohm)
    loss_c = 3 * evaluate(D, 750.0, V_LL, I_RATED, 0.0, T_IN)["leg_w"]
    etas = [eff_point(D, vdc, V_LL, I_RATED * x, ang, T_IN, n_fans)[4] for vdc in (600.0, 750.0, 900.0) for ang in (0.0, 180.0)
            for x in (0.2, 0.25, 0.3, 0.35, 0.4, 0.5)]
    eta_full_corr = eff_point(D, 750.0, V_LL, I_RATED, 0.0, T_IN, n_fans)[4]
    tj_corr = max(max(max(v) for v in overload_tj(D, vdc, ang)["tj_200ms"].values()) for vdc, ang in ((600.0, 0.0), (900.0, 90.0), (900.0, 180.0)))
    # gate drive per channel: one NSI6651ASC per switch position driving n gates; charge, power, peak current with a buffer
    gd = pv_design_gd = None
    qg = d["qg"] * n
    swing = d["vgs_on"] - d["vgs_off"]
    p_gate = qg * swing * FSW_CHOICE
    i_pk_needed = swing / (pick["rg_ext_off"] + d["rg_int"]) * n
    nsi_ipk = 10.0
    res = {"device": part, "n_parallel": n, "rails_V": (d["vgs_on"], d["vgs_off"]), "rg_cal_eff_ohm": rg_cal, "leg": leg_model(n, 1.0),
           "sweep_1050V_450A_30nH": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in sweep],
           "chosen": {"R_G_off_ext_ohm_per_device": pick["rg_ext_off"], "R_G_on_ext_ohm_per_device": rg_on,
                      "rg_eff": rg, "v_limit_V": v_lim},
           "nominal_950V": nom, "worst_1050V_450A_30nH": worst, "eoff_factor_vs_datasheet_rg": eoff_factor, "eon_factor_vs_datasheet_rg": EON_MULT["value"],
           "loss_b_W": loss_b, "loss_corr_W": loss_c, "eta_peak_corr": max(etas), "eta_full_corr": eta_full_corr, "tj_200ms_corr_C": tj_corr,
           "dead_time_ns": 300.0, "gate": {"Qg_per_channel_nC": qg * 1e9, "P_gate_W_per_channel": p_gate,
                                            "I_peak_A_needed": i_pk_needed, "NSI6651_peak_A": nsi_ipk},
           "desat": {"threshold_V_DS": "6.3-7.9 V (NSI6651 8.5-9.8 V minus 100 ohm x I_CHG and 3 x US1MH, gen/gdrv.py DESAT_CLASS[1700])",
                     "V_DS_at_OC_trip_V": float(OC_TRIP / n * K_SHARE['sic'] * pv.rds(d, 150.0)),
                     "blanking_ns": (150, 500), "response_to_off_us": 1.0, "t_sc_assumed_us": 2.0,
                     "note": "Sichain gives no short-circuit withstand: 2 us ASSUMED as for the PV module (pv_design T_SC_ASSUMED)"}}
    SPEC["commutation_and_gate_drive"] = res
    return res


def report_e(r):
    c, nom, w = r["chosen"], r["nominal_950V"], r["worst_1050V_450A_30nH"]
    wr("\n## (e) Commutation, gate drive, dead time, short circuit\n")
    lg = r["leg"]
    wr(f"**Commutation (ngspice, VDMOS models of {r['device']} fitted to the data sheet's Q_gd and E_off - the PV design's calibration, "
       f"read from sim/out/pv_tradeoff/vdmos_calibration.json):** {r['n_parallel']} devices per switch on the **PV module's verified physical "
       f"leg, repeated for every device pair** (pv_design.leg_net: 3 x KEMET C4AQ 2.2 uF / 1300 V decoupling at the pins, 15 nH bus, 11.5 nH "
       f"board + devices, RC damper 2 x 4.99 ohm + 2 x 4.7 nF per pair) - three pairs in parallel: {lg['c_dec']*1e6:.1f} uF, "
       f"{lg['l_bus']*1e9:.1f} nH, {lg['l_rest']*1e9:.2f} nH, damper {lg['r_damp']:.2f} ohm + {lg['c_damp']*1e9:.1f} nF on the 350 uF film "
       f"bank (ESL 21 nH).  This is a layout requirement: one shared loop of 20-30 nH for six pairs gave 2.0-2.3 kV in the first run of this "
       f"step.  Off-resistance sweep at the worst case (V_dc = {V_OV_TRIP:.0f} V = the DC over-voltage trip, I = {OC_TRIP:.0f} A = the "
       f"per-phase over-current trip, bus and board inductance x1.5):\n")
    wr("| R_G,off per device (ext) | peak V_DS (V) | overshoot (V) | di/dt, all six (A/ns) | dv/dt (V/ns) | E_off, all six (mJ) | deck |")
    wr("|---|---|---|---|---|---|---|")
    for x in r["sweep_1050V_450A_30nH"]:
        wr(f"| {x['rg_ext_off']:.1f} ohm | {x['v_pk']:.0f} | {x['os_off']:.0f} | {x['didt_A_per_ns']:.1f} | {x['dvdt_V_per_ns']:.0f} | "
           f"{x['e_off_mJ']:.2f} | {x['deck']} |")
    wr(f"\n**Chosen: R_G,off {c['R_G_off_ext_ohm_per_device']:.1f} ohm, R_G,on {c['R_G_on_ext_ohm_per_device']:.2f} ohm per device** "
       f"(the smallest of the sweep that holds the limit): worst case {w['v_pk']:.0f} V peak against the 0.85 x 1700 = {c['v_limit_V']:.0f} V "
       f"project limit; nominal (950 V, {nom['i']:.0f} A = 1.2 x 216 A peak + ripple, nominal leg): {nom['v_pk']:.0f} V, {nom['dvdt_V_per_ns']:.0f} V/ns, "
       f"{nom['didt_A_per_ns']:.1f} A/ns.  E_off at this R_G is {r['eoff_factor_vs_datasheet_rg']:.2f} x "
       f"the data-sheet curve (simulated) and E_on at R_G,on {c['R_G_on_ext_ohm_per_device']:.2f} ohm {r['eon_factor_vs_datasheet_rg']:.2f} x "
       f"(the data sheet's E-vs-R_G curve, D-057); the device loss at 125 kW / 750 V rises from {r['loss_b_W']:.0f} to {r['loss_corr_W']:.0f} W, peak "
       f"efficiency {r['eta_peak_corr']*100:.2f} % (full load 750 V {r['eta_full_corr']*100:.2f} %) - step b already carries both factors "
       f"(this step runs first).  Decks: sim/spice/pcs_commutation_*.cir.")
    g = r["gate"]
    wr(f"\n**Gate drive (the project's channel, gen/gdrv.py, NSI6651ASC):** one channel per switch position - **6 channels for three-wire, "
       f"8 for four-wire** (the architecture had 12/16).  Rails **+{r['rails_V'][0]:.0f} / {r['rails_V'][1]:.1f} V** (the PV setting for this "
       f"device, gdrv GATE_V (18, 3.5)); per-device gate resistor and 0.5 ohm Kelvin resistor (R_KS), per-gate Miller clamp FET as the PV rev-5 "
       f"channel; DESAT string 100 ohm + 3 x US1MH and the short-circuit booster (booster=True) unchanged.  Six gates per channel: "
       f"Q_g {g['Qg_per_channel_nC']:.0f} nC, {g['P_gate_W_per_channel']:.2f} W at {FSW_CHOICE/1e3:.0f} kHz (bias secondary budget 0.5 W - one SN6505B "
       f"transformer per phase with two secondaries); peak gate current {g['I_peak_A_needed']:.0f} A wanted against the NSI6651's "
       f"{g['NSI6651_peak_A']:.0f} A - **add a discrete NPN/PNP push-pull buffer per channel** (two SOT-89 transistors, about 0.3 USD) or "
       f"split the six devices over two drivers (+6 channels); the buffer is the cheaper answer.  Dead time: **{r['dead_time_ns']:.0f} ns** in "
       f"the ePWM dead-band, with the channel's RC + Schmitt stretch on IN- as the hardware minimum and the negative-rail detector "
       f"(gen/gdrv.py stretch=True, neg_det=True, D-050).  The stretch values of the PV preset (2 gates, no buffer) do NOT carry over: six "
       f"gates behind a buffer at R_G,off {r['chosen']['R_G_off_ext_ohm_per_device']:.1f} ohm turn off more slowly, so a '6 x SG2M040170HJ' "
       f"preset must be added to gen/gdrv.py and its design_check re-run before the channel is drawn.")
    ds = r["desat"]
    wr(f"\n**Short circuit:** DESAT trips at V_DS {ds['threshold_V_DS']}; at the {OC_TRIP:.0f} A over-current trip the hottest device sits "
       f"at {ds['V_DS_at_OC_trip_V']:.1f} V (no nuisance trip); blanking {ds['blanking_ns'][0]}-{ds['blanking_ns'][1]} ns, detection to off "
       f"<= {ds['response_to_off_us']:.1f} us with the booster - inside the {ds['t_sc_assumed_us']:.0f} us withstand ASSUMED for Sichain "
       f"(no rating published: SC test or maker statement is a release item).")
    wr("\n**If IGBTs were used instead (the architecture's choice) the channel would differ:** rails +15 / -8 V; DESAT threshold for "
       "V_CE(sat) of 3-4 V at twice rated current with 2-3 us blanking against a 3-10 us withstand (CR Micro / NCE parts state none or 3-10 us); "
       "no SiC booster (the driver's soft turn-off suffices, the IGBT limits its own short-circuit current); dead time 1.0-1.5 us; gate "
       "resistors 5-10 ohm; gate charge 208-331 nC per 40-80 A device at a 23 V swing.")
    wr("\n**Hardware / firmware split (D-050, as on the PV module):** the discrete layer stays and the controller is the second layer.  "
       "Discrete: driver DESAT + booster and UVLO -> FLT / RDY; default-off pull-downs; the external stop on the drivers' EN and on the latch; "
       "the RC dead-time stretch and the negative-rail detector in every channel; on the control board (PV-CTL as drawn, sheets 04 / 05) "
       "window comparators for each phase current (+-450 A), DC over-voltage (1050 V), over-temperature and open probe, one set-dominant "
       "latch, the heartbeat watchdog and the AND gating of every PWM, EN and coil line; on the DC port the polarity and precharge-dV "
       "interlocks, the hold-off comparator and the port over-current window (gen/port.py interlocks='full', oc_trip=True).  Second layer "
       "in the controller's own hardware: CMPSS windows, ADC limit trips, trip zone, dead-band.  Firmware: everything that is sequencing, "
       "monitoring or a non-hazard edge case (list in the hand-over section).  What does not carry over from the PV design is listed in "
       "step g.")


# ------------------------------------------------------------------------------------------------ (f) AC port, DC port, common mode
RCMU = {"continuous_mA": {"<=30kVA": 300.0, ">30kVA_per_kVA": 10.0}, "steps_mA_s": [(30, 0.3), (60, 0.15), (150, 0.04)],
        "src": "IEC 62109-2 residual-current monitoring, FROM MEMORY (standard text not on file)"}
C_BE = (1e-6, 5e-6, 20e-6)        # battery-system capacitance to earth, F (unknown, a range: rack Y-caps, cables, cell-to-frame)
L_CM_AC = FP["cm_choke"]["L_cm_uH"] * 1e-6 if FP else 150e-6   # AC CM choke: sim/pcs_tradeoff.py sizes it (earth current no worse than
#                                   the D-053 150 uH, Yunlu N-R-564440 cores by magnetics.pcs_cm_design); 150 uH before that script has run


def step_f(D, rc, rd):
    lev = TOPO[D["topo"]]["levels"]
    l1, cf = D["l1"], rc["filter"]["Cf"]
    res = {}
    # ---- low-frequency zero sequence where the min-max offset is used (two-level: only where sinusoidal PWM cannot reach)
    lf = []
    for vdc in (590.0, 620.0, 650.0, 680.0, 750.0, 900.0, 950.0):
        for vll in (V_LL, V_LL * (1 + V_TOL)):
            m, phi, p, q = op_point(vdc, vll, I_RATED, 0.0, D["l1"] + D["l2"])
            zs = zs_policy(lev, m)
            th, va = references(m, zs, 720)
            v0 = (va - m * np.sin(th)) * vdc / 2
            X = np.fft.rfft(v0) / len(v0)
            v150 = 2 * abs(X[3]) / math.sqrt(2)                 # rms of the 150 Hz component
            row = {"vdc": vdc, "vll": vll, "m": m, "zs": zs, "v150_rms": v150,
                   "i_leak_mA": {f"{c*1e6:.0f}uF": v150 * 2 * math.pi * 150 * c * 1e3 for c in C_BE}}
            lf.append(row)
    res["lf_zero_sequence"] = lf
    lim = RCMU["continuous_mA"][">30kVA_per_kVA"] * P_RATED / 1e3
    worst = max(lf, key=lambda r: r["v150_rms"])
    res["cbe_limit_uF_at_300mA"] = 300e-3 / max(worst["v150_rms"] * 2 * math.pi * 150, 1e-9) * 1e6
    res["cbe_limit_uF_at_10mA_per_kVA"] = lim * 1e-3 / max(worst["v150_rms"] * 2 * math.pi * 150, 1e-9) * 1e6
    # ---- switching-frequency common mode: C_f star tied to the DC midpoint; residual at the terminals; earth current via C_bE
    vcm = rc["ripple"][950.0]["cm_fsw_V"]
    w = 2 * math.pi * FSW_CHOICE
    z3cf, zl1 = 1 / (w * 3 * cf), w * l1 / 3
    v_term = vcm * z3cf / abs(zl1 - z3cf)
    i_hf = {f"{c*1e6:.0f}uF": {"no_choke_A": v_term / abs(1 / (w * c)), "with_choke_mA": v_term / abs(w * L_CM_AC - 1 / (w * c)) * 1e3}
            for c in C_BE}
    res["hf"] = {"v_cm_converter_V_pk": vcm, "v_cm_terminals_V_pk": v_term, "earth_current": i_hf, "L_cm_uH": L_CM_AC * 1e6}
    # ---- AC disconnect, precharge, synchronisation
    c_eq = rd["C_series_uF"] * 1e-6
    r_pre = 220.0
    res["ac_contactor"] = {"arrangement": "two 3-pole contactors in series (4-pole for four-wire), relay test before every connection",
                           "Ie_AC1_A_min": 1.15 * I_2MIN, "Ui_V": 1000, "Uimp_kV": 8, "coil": "24 V DC with economiser (ESTIMATE 20 W pull-in / 4 W hold)",
                           "candidates": "CHINT NXC-225 / CJX2-185, Delixi CJX2s-185 class - no data sheet on file (R-06, RFQ)"}
    res["precharge"] = {"R_ohm": r_pre, "C_eq_uF": c_eq * 1e6, "tau_s": r_pre * c_eq, "t_to_10V_s": r_pre * c_eq * math.log(950 / 10),
                        "E_J_950V": 0.5 * c_eq * 950 ** 2, "I_pk_A": 950 / r_pre}
    res["dc_port"] = {"contactor": "Hongfa HFE82V-300C/1000 (300 A at 85 C, break-once 1.5 kA; port_spec lean)",
                      "fuse": "aR >= 333 A (75 % rule at 230 A continuous, 1000 V DC) - 400 A class, RFQ (Hongfa HPE501 stops at 250 A: R-05)",
                      "hold_off_threshold_A": 1000.0, "shunt": "2 x 200 uOhm in parallel in DC- (port_spec lean): 25 mV at 250 A, 6.3 W",
                      "oc_trip_A": 400.0, "ov_trip_V": V_OV_TRIP}
    SPEC["ports_and_common_mode"] = res
    return res


def report_f(r, rc):
    wr("\n## (f) AC port, DC port, common mode\n")
    a = r["ac_contactor"]
    wr(f"**AC disconnect:** {a['arrangement']}; AC-1 >= {a['Ie_AC1_A_min']:.0f} A at 60 C, U_i {a['Ui_V']} V, U_imp {a['Uimp_kV']} kV, {a['coil']}; "
       f"{a['candidates']}.  Relay test (firmware, before every connection): close each contactor alone and read the voltage across the "
       f"other through the terminal- and C_f-side dividers - a welded pole shows as zero volts.  Two in series because the converter is "
       f"non-isolated and the disconnection must survive one welded contact (IEC 62109-2 / EN 50549-1 'AC relay automatic checking' as the "
       f"PMA cites them - clauses from memory).  The contactors make only after synchronisation (|dV| <= 5 %, 5 deg: {rc['inrush']['synchronised_5pct_5deg_peak_A']:.0f} A "
       f"peak, step c) and break only at zero current (firmware); with the controller dead the gates are off and only the brief diode-"
       f"rectifier current flows, inside the AC-1 breaking capacity.  No AC-side hold-off comparator (D-050 keeps it for the DC contactor): "
       f"after a trip the converter current is zero within microseconds, and a short behind the contactors is fed by the grid at a level only "
       f"the upstream breaker can clear - a hold-off could not help.  Synchronised closing is firmware: an unsynchronised close rings L2-C_f "
       f"({rc['inrush']['unsynchronised_C_f_empty_peak_A']:.0f} A peak) inside the contactor's making capacity - a stressed unit, not a hazard.")
    pc = r["precharge"]
    wr(f"\n**Precharge and start:** from the DC side through the lean port's relay and {pc['R_ohm']:.0f} ohm: C_eq {pc['C_eq_uF']:.0f} uF, "
       f"tau {pc['tau_s']*1e3:.0f} ms, within 10 V of 950 V after {pc['t_to_10V_s']:.2f} s, {pc['E_J_950V']:.0f} J, {pc['I_pk_A']:.1f} A peak.  "
       f"Then the inverter forms the grid voltage on C_f (current-limited), synchronises, runs the relay test, closes.  Grid start (battery "
       f"empty or disconnected) uses the architecture's six-diode tap for the auxiliary supply only.")
    wr("\n**Surge:** AC type II on the board (three thermally protected varistors L-PE, GDT N-PE in four-wire, monitored); C_f takes the "
       "residual; DC side: the PV rev-6 varistor network unchanged (Up,eff 3.79 kV).")
    lfz = r["lf_zero_sequence"]
    wr(f"\n**Common mode on a three-wire connection to an earthed-neutral (TN) grid.**  The DC side is galvanically tied to the grid: its "
       f"midpoint sits at earth potential plus whatever zero sequence the converter makes.  (1) Low frequency: "
       + ("this design uses the min-max zero sequence at every operating point (section h), so the battery carries its 150 Hz component at "
          "every DC voltage (rms, rated current):\n" if MOD_2L == "minmax" else
          "this design uses sinusoidal PWM wherever it reaches, so above about 680 V (400 V AC; about 780 V at 460 V AC) there is no 150 Hz "
          "common-mode voltage at all; below, the min-max zero sequence is needed (rms of its 150 Hz component, rated current):\n"))
    wr("| V_dc | AC | PWM | 150 Hz CM (V rms) | leakage at 1 / 5 / 20 uF to earth (mA) |")
    wr("|---|---|---|---|---|")
    for x in lfz:
        if x["vll"] > V_LL * 1.01 and x["vdc"] > 700:
            continue
        il = x["i_leak_mA"]
        wr(f"| {x['vdc']:.0f} V | {x['vll']:.0f} V | {x['zs']} | {x['v150_rms']:.0f} | {il['1uF']:.0f} / {il['5uF']:.0f} / {il['20uF']:.0f} |")
    wr(f"\nAgainst the residual-current monitor (IEC 62109-2 from memory: 10 mA per kVA continuous above 30 kVA = {RCMU['continuous_mA']['>30kVA_per_kVA']*P_RATED/1e3:.0f} mA "
       f"here, 300 mA for small units, sudden steps of 30 / 60 / 150 mA): the battery may have up to {r['cbe_limit_uF_at_300mA']:.0f} uF to "
       f"earth for a 300 mA budget ({r['cbe_limit_uF_at_10mA_per_kVA']:.0f} uF for the 10 mA/kVA one) "
       + ("at every DC voltage.  (The architecture's 0.7 A at 5 uF assumed 150 V of zero sequence - the min-max offset's 150 Hz part is "
          "about half that.)" if MOD_2L == "minmax" else
          "when it is operated below 680 V; above, no limit from this source.  (The architecture's 0.7 A at 5 uF assumed 150 V of zero "
          "sequence - the min-max offset's 150 Hz part is about half that, and it is not used above 680 V here.)"))
    h = r["hf"]
    wr(f"\n(2) Switching frequency: the converter's common-mode voltage ({h['v_cm_converter_V_pk']:.0f} V peak at {FSW_CHOICE/1e3:.0f} kHz, 950 V) is returned "
       f"locally through C_f and the DC midpoint (step c), leaving {h['v_cm_terminals_V_pk']:.1f} V peak between the terminals and the DC "
       f"midpoint; through the battery's earth capacitance that alone drives "
       + ", ".join(f"{k}: {v['no_choke_A']:.1f} A" for k, v in h["earth_current"].items())
       + f" at {FSW_CHOICE/1e3:.0f} kHz - so the AC conductors need a common-mode choke of >= {h['L_cm_uH']:.0f} uH "
         f"({FP['cm_choke']['k'] if FP else 3} nanocrystalline cores over the busbars instead of the architecture's two): " + ", ".join(f"{k}: {v['with_choke_mA']:.0f} mA" for k, v in h["earth_current"].items()) + ".")
    wr("\n**What the product needs (answer):** no transformer and no special modulation beyond the one above.  Three-wire on a TN grid is "
       "allowed with (a) the C_f star tied to the DC midpoint, (b) the AC common-mode choke, (c) an installation limit on the battery's "
       "capacitance to earth for operation below 680 V (stated above), (d) the RCMU thresholds set per the code; where the battery exceeds it "
       "or the grid code forbids the DC-to-earth voltage, the four-wire version (N connected, the N leg holds the zero sequence) or an "
       "isolation transformer is the installer's choice.  The battery rack sits at +-V_dc/2 against earth in operation (as the architecture "
       "noted): its insulation and IMD must be rated for that.")
    d = r["dc_port"]
    wr(f"\n**DC port = the lean battery port scaled to 250 A:** {d['contactor']}; {d['fuse']}; hold-off threshold {d['hold_off_threshold_A']:.0f} A "
       f"(the contactor stays closed above it and lets the fuse clear - discrete comparator, D-050) and hardware polarity / precharge-dV interlocks in the coil drives; {d['shunt']}; port over-current "
       f"{d['oc_trip_A']:.0f} A and over-voltage {d['ov_trip_V']:.0f} V as ADC limit trips.  The 400 A fuse's slow region (1.5-3 kA, seconds) "
       f"leaves the upstream requirement on the battery's own breaker (R-05: re-run the port_design coordination with the chosen fuse).")


# ------------------------------------------------------------------------------------------------ (g) cost
ARCH_COST = {"3W": (1037.04, 800.87), "4W": (1193.82, 919.18)}     # ARCHITECTURE-PCS section 12 / costfirst_pcs_bom.csv totals


# ---- D-050 discrete protection layer: counts from the PV boards, prices from the PV module's costed BOM (gen/cost.py output)
PV_COSTED = os.path.join(ROOT, "bom", "PV-P75_costed_BOM.csv")
GEN = os.path.join(ROOT, "gen")


def pv_price_book():
    """MPN (or (value, package) for GENERIC) -> (unit catalogue USD, unit 5k USD, 5k basis) from bom/PV-P75_costed_BOM.csv"""
    book = {}
    for r in csv.DictReader(open(PV_COSTED)):
        try:
            c, k = float(r["unit_cost_usd"]), float(r["unit_cost_5k_usd"])
        except ValueError:
            continue
        key = r["MPN"] or (r["Value"], r["Package"])
        if key not in book or c > book[key][0]:
            book[key] = (c, k, r["basis_5k"])
    return book


def price_parts(parts, book):
    """parts: {(mpn, value, package): n} -> (USD catalogue, USD 5k, USD 5k on REAL evidence); TLV9024PWR is unpriced in the costed BOM:
    gen/pv_ctrl.py's PRICE estimate 0.25 USD (TI class), x0.85 at 5k; a generic value missing from the book takes the book's dearest
    part of the same kind and package (R or C)"""
    cat = k5 = real = 0.0
    for (mpn, val, pkg), n in parts.items():
        if val == "TP" or n <= 0:
            continue
        key = mpn or (val, pkg)
        if key in book:
            c, k, basis = book[key]
        elif mpn == "TLV9024PWR":
            c, k, basis = 0.25, 0.2125, "ESTIMATE (pv_ctrl PRICE)"
        else:
            kind_c = bool(re.search(r"[pnu]\s*\d*V?|[pnu]$", val)) or " " in val
            same = [v for kk, v in book.items() if isinstance(kk, tuple) and kk[1] == pkg and
                    (bool(re.search(r"[pnu]\s*\d*V?|[pnu]$", kk[0])) or " " in kk[0]) == kind_c]
            c, k, basis = max(same) if same else (0.01, 0.01, "ESTIMATE")
        cat += n * c
        k5 += n * k
        real += n * k if str(basis).startswith("REAL") else 0.0
    return cat, k5, real


def sheet_parts(bom_csv, sheets):
    """parts of a generated board BOM whose references sit on the given sheets (ref = prefix + sheet number + two digits)"""
    out = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "bom", bom_csv))):
        if r.get("DNP"):
            continue
        n = sum(1 for x in re.split(r"[ ,;]+", r["References"].strip()) if x and int(re.sub(r"\D", "", x)[:-2] or 0) in sheets)
        if n:
            k = (r["MPN"] or "", r["Value"], r["Package"])
            out[k] = out.get(k, 0) + n
    return out


def port_d050_parts():
    """battery-type lean port (sheets 5-7 of gen/port.py build_lean): parts of interlocks='full', oc_trip=True that the hold-only
    variant does not have = polarity + precharge-dV interlocks and the port over-current window; plus the hold-off comparator itself"""
    import contextlib
    import io
    sys.path.insert(0, GEN)
    import port as gport

    def parts(il, oc):
        with contextlib.redirect_stdout(io.StringIO()):
            B, _, _ = gport.build_lean(il, oc)
        out = {}
        for ref, m in B.bom.items():
            if ref.startswith("#") or m.get("sourcing") == "NOPART":
                continue
            if int(re.sub(r"\D", "", ref)[:-2] or 0) in (5, 6, 7):
                k = (m["mpn"] or "", str(m["value"]), m.get("pkg", ""))
                out[k] = out.get(k, 0) + 1
        return out
    full, hold = parts("full", True), parts("hold", False)
    add = {k: full.get(k, 0) - hold.get(k, 0) for k in set(full) | set(hold) if full.get(k, 0) > hold.get(k, 0)}
    holdoff = {("TLV3502AIDCNR", "TLV3502AIDCNR", "SOT-23-8 (DCN)"): 1}
    return add, holdoff


def gdrv_d050_parts():
    """per channel, as gen/gdrv.py channel() draws them: dead-time stretch (R, BAT54, 100p, SN74LVC1G17, 100n, 100R, 100p) and the
    negative-rail detector (MMBT3904, 10k, 100k 0805, BAT54)"""
    sys.path.insert(0, GEN)
    import gdrv
    r_dt = gdrv.ohm(gdrv.DEADTIME_CLASS[1700][0])
    stretch = {("", r_dt, "0603"): 1, ("BAT54", "BAT54", "SOT-23"): 1, ("", "100p 50V", "0603"): 2,
               ("SN74LVC1G17DBVR", "SN74LVC1G17DBVR", "SOT-23-5"): 1, ("", "100n 50V", "0603"): 1, ("", "100R", "0603"): 1}
    negdet = {("MMBT3904,215", "MMBT3904,215", "SOT-23"): 1, ("", "10k", "0603"): 1, ("", "100k", "0805"): 1, ("BAT54", "BAT54", "SOT-23"): 1}
    return stretch, negdet


def protection_layer():
    """D-050 lines: (catalogue, 5k, 5k REAL) per item and the counts behind them"""
    book = pv_price_book()
    ctl3 = sheet_parts("PV-CTL-P75_BOM.csv", (4, 5))        # three phase windows (PCS three-wire)
    ctl4 = sheet_parts("PV-CTL_BOM.csv", (4, 5))            # four windows (PCS four-wire: + the N leg)
    port_add, holdoff = port_d050_parts()
    stretch, negdet = gdrv_d050_parts()
    out = {"control_3W": price_parts(ctl3, book), "control_4W": price_parts(ctl4, book), "port_interlocks_oc": price_parts(port_add, book),
           "port_holdoff": price_parts(holdoff, book), "stretch_per_ch": price_parts(stretch, book), "negdet_per_ch": price_parts(negdet, book),
           "counts": {"control_3W_parts": sum(n for k, n in ctl3.items() if k[1] != "TP"), "control_4W_parts": sum(n for k, n in ctl4.items() if k[1] != "TP"),
                      "control_3W_ICs": {k[0]: n for k, n in ctl3.items() if k[0]}, "port_added": {f"{k[0] or k[1]}": n for k, n in port_add.items()},
                      "stretch_per_channel": sum(stretch.values()), "negdet_per_channel": sum(negdet.values())}}
    return out


def filter_row(key, l, e):
    """BOM row of L1 or L2 from the magnetics-model part chosen by sim/pcs_tradeoff.py"""
    x = FP[key]
    core = CORE_WORD[x["mat"]]
    return ("LCL FILTER", f"{key} {l*1e6:.0f} uH, 216 A rms ({e:.1f} J): gapped {core} C-core, {x['wire']}, {x['N']} turns, {x['mass_kg']:.1f} kg",
            3, f"{key}-{l*1e6:.0f}u-216A (CUSTOM)", "CUSTOM (Yunlu / AT&M core)", x["usd"][0], x["usd"][1],
            "sim/magnetics.py design + cost model (core / conductor USD per kg, labour; catalogue 1k) - ESTIMATE, no quote",
            "magnetics.py 5k build factors (ASSUMED)", False)


def bom_rows(D, rc, rd, re_, PL):
    """every costed line: (block, function, qty, part, maker, unit cat, unit 5k, cat basis, 5k basis, evidence_5k?) - three-wire; the
    four-wire increment separately.  5k basis: 'EVIDENCE' = an LCSC volume break / maker 1 ku list / marketplace quote; else ASSUMED."""
    n_dev = sum(n for p_, (part, n) in D["devs"].items()) * 3
    part = D["devs"]["TH"][0]
    lg = re_["leg"]
    l1, l2, cf = D["l1"], D["l2"], rc["filter"]["Cf"]
    e1 = 0.5 * l1 * (I_2MIN * math.sqrt(2) * (1 + RIPPLE_PP / 2)) ** 2
    e2 = 0.5 * l2 * (I_2MIN * math.sqrt(2)) ** 2
    pr = dv.SIC_PRICE[part]["price"]
    R = [
        ("POWER", "SiC MOSFET 1700 V 40 mOhm TO-247-4L, 6 per switch, two-level legs", n_dev, part, "Sichain (CN)", pr[1], pr[1000],
         dv.SIC_PRICE[part]["note"], "ASSUMED x0.85 (the PV BOM's factor; RFQ)", False),
        ("POWER", "Device insulator Al2O3 0.635 mm + clip + grease (basic DC-PE)", n_dev, "Al2O3 TO-247 pad + clip", "generic (CN)", 0.40, 0.30,
         "ESTIMATE (costfirst_pcs_bom.csv)", "ASSUMED x0.75", False),
        ("POWER", f"DC-link film 140 uF 600 V (U_N 70 C), {rd['per_half']} per half, split bank", rd["total"], "C3D1U147+M0A", "Xiamen Faratronic (CN)",
         FILM_PRICE["cat"], FILM_PRICE["5k"], "ESTIMATE: LCSC C3D1X505 0.557 USD @1000 scaled by can volume (architect basis)", "ASSUMED x0.85", False),
        ("POWER", "Local decoupling film 2.2 uF 1300 V at every device pair (3 per pair)", 3 * lg["decoupling_parts"], "FCSA3DS225 (CBB138 DS)",
         "Jianghai (CN)", 0.45, 0.383, "ESTIMATE (costfirst_bom.csv PV row)", "ASSUMED x0.85", False),
        ("POWER", "RC damper per device pair: 2 x 4.7 nF 2000 V C0G + 6 x 15 ohm 2512", 3, "C0G 2220 + CRCW2512 class", "Fenghua / Yageo class",
         lg["damper_parts"] / 4 * (2 * 0.625 + 6 * 0.110), lg["damper_parts"] / 4 * (2 * 0.531 + 6 * 0.093), "PV BOM class prices", "ASSUMED x0.85", False),
        ("GATE DRIVE", "Isolated SiC gate driver, DESAT + soft turn-off + Miller clamp, one per switch position", 6, "NSI6651ASC-Q1SWR", "NOVOSENSE (CN)",
         2.978, 1.544, "prices.csv LCSC C33959952 @1", "LCSC tier slope 0.52 (CA-IS3062W) - EVIDENCE-derived", True),
        ("GATE DRIVE", "Channel discretes (gdrv.channel: booster, per-gate clamp FET, R_G, R_KS, DESAT string) for 6 gates + NPN/PNP buffer; "
         "stretch and negative-rail detector on the next line", 6, "class (per channel)", "Nexperia / Diodes / TSC / Yageo",
         2.60 - PL["stretch_per_ch"][0] + 4 * 0.25 + 0.30, (2.60 - PL["stretch_per_ch"][0] + 4 * 0.25 + 0.30) * 0.85,
         "PV row 2.60 USD (2 gates, contains the stretch) less the stretch + 0.25 USD per extra gate (ESTIMATE) + 0.30 buffer", "ASSUMED x0.85", False),
        ("GATE DRIVE", f"D-050 per channel: RC dead-time stretch ({PL['counts']['stretch_per_channel']} parts, SN74LVC1G17 Schmitt) + "
         f"negative-rail detector ({PL['counts']['negdet_per_channel']} parts, MMBT3904) - gen/gdrv.py stretch=True, neg_det=True", 6,
         "per gdrv.channel()", "TI / Nexperia + passives", PL["stretch_per_ch"][0] + PL["negdet_per_ch"][0],
         PL["stretch_per_ch"][1] + PL["negdet_per_ch"][1], "bom/PV-P75_costed_BOM.csv unit prices", "PV costed BOM 5k column (REAL for the ICs)", True),
        ("GATE DRIVE", "Gate bias per phase: SN6505B + custom transformer (2 secondaries) + 2 regulators", 3, "SN6505BDBVR + GDB-T1 + 2 x reg",
         "TI + CUSTOM", 0.926 + 1.50 + 2 * 0.30, 0.926 + 1.20 + 2 * 0.255, "TI 1ku + ESTIMATE", "MIXED: TI 1ku list, rest ASSUMED x0.80/0.85", False),
        filter_row("L1", l1, e1) if FP else
        ("LCL FILTER", f"L1 {l1*1e6:.0f} uH, 216 A rms / {I_2MIN*math.sqrt(2)*(1+RIPPLE_PP/2):.0f} A pk ({e1:.1f} J), amorphous C-core + Cu strip",
         3, f"L1-{l1*1e6:.0f}u-216A (CUSTOM)", "CUSTOM (AT&M / Yunlu core)", L1_COST[0] + L1_COST[1] * e1, (L1_COST[0] + L1_COST[1] * e1) * 0.8,
         "ESTIMATE: architect's 10 USD + 6 USD/J (42 USD for 100 uH)", "ASSUMED x0.80", False),
        filter_row("L2", l2, e2) if FP else
        ("LCL FILTER", f"L2 {l2*1e6:.0f} uH, 216 A rms ({e2:.2f} J), powder / Si-steel", 3, f"L2-{l2*1e6:.0f}u-216A (CUSTOM)", "CUSTOM",
         10.0 + 8.0 * e2, (10.0 + 8.0 * e2) * 0.8, "ESTIMATE: 10 USD + 8 USD/J (fits the architect's 22 USD for 30 uH)", "ASSUMED x0.80", False),
        ("LCL FILTER", f"C_f {cf*1e6:.0f} uF per phase ({math.ceil(cf / 25e-6 - 1e-9)} x {cf*1e6/math.ceil(cf / 25e-6 - 1e-9):.0f} uF 450 V AC MKP), "
         "star tied to the DC midpoint", 3 * math.ceil(cf / 25e-6 - 1e-9), f"{cf*1e6/math.ceil(cf / 25e-6 - 1e-9):.0f} uF 450 VAC MKP",
         "Faratronic / Jianghai class (CN)", 0.12 * cf * 1e6 / math.ceil(cf / 25e-6 - 1e-9), 0.12 * cf * 1e6 / math.ceil(cf / 25e-6 - 1e-9) * 0.85,
         "ESTIMATE: 0.12 USD/uF (architect)", "ASSUMED x0.85", False),
        ("LCL FILTER", "Passive damping branch 2 ohm 25 W + 10 uF per phase", 3, "RX24 2R + 10 uF MKP", "class", 3.0, 2.55, "ESTIMATE (architect)",
         "ASSUMED x0.85", False),
        ("SENSING", "Phase current sensor, open-loop TMR +-500 A (C_f side of L1)", 3, "STK-HO/A class", "Sinomags (CN)", 6.0, 4.8,
         "ESTIMATE (architect, range raised for the 450 A trip)", "ASSUMED x0.80", False),
        ("SENSING", "DC shunt 2 x 200 uOhm + zero-drift amplifier", 1, "3920 strip x2 + SGM8552 class", "class", 2.2, 1.87, "ESTIMATE (PV)", "ASSUMED x0.85", False),
        ("SENSING", "HV dividers 1 M 0.1 % (grid L1-3 at terminals and C_f, V_DC, V_mid, terminal, PE)", 60, "ARHV06BTC1004A", "Viking Tech (TW)",
         0.25, 0.21, "ESTIMATE (PV)", "ASSUMED x0.85", False),
        ("SENSING", "Divider buffers and filters (the trip comparators are the control-board and DC-port lines below)", 1, "class", "3PEAK / SGMICRO class",
         1.5, 1.28, "ESTIMATE", "ASSUMED x0.85", False),
        ("SENSING", "Residual-current sensor type B (fluxgate), 3 conductors", 1, "type-B RCM class", "CN fluxgate class", 10.0, 8.0,
         "ESTIMATE: no data sheet on file (R-10)", "ASSUMED x0.80", False),
        ("SENSING", "NTC probes (heatsink 3, L1 3, inlet 1, DC link 1)", 8, "NTC 10k lug", "EXSENSE / Shiheng class", 0.5, 0.4, "ESTIMATE", "ASSUMED x0.80", False),
        ("SENSING", "IMD strings + 2 x CA-IS3417WT (PV)", 1, "16 x ARHV13 + 2 x CA-IS3417WT", "Viking / Chipanalog", 8.8, 7.48, "PV BOM", "ASSUMED x0.85", False),
        ("DC PORT", "DC contactor 300 A 1000 V", 1, "HFE82V-300C/1000-24-H-C5-1", "Hongfa (CN)", 55.0, 38.0, "ESTIMATE (PV basis)",
         "marketplace volume quote 32.47 USD @30 (prices.csv) - EVIDENCE", True),
        ("DC PORT", "DC fuse aR 400 A 1000 V DC, one per pole", 2, "aR 400 A (Hongfa HPE / Sinofuse RS306 class, RFQ)", "Hongfa / Sinofuse (CN)",
         25.0, 17.0, "ESTIMATE: no public price", "ASSUMED x0.68 (RFQ)", False),
        ("DC PORT", "Precharge relay + 220 ohm + coil drivers (PV)", 1, "G7L-2A-X + RXLG 220R + drivers",
         "Omron / CN", 18.11, 14.6, "PV costfirst_bom rows", "ASSUMED x0.80", False),
        ("DC PORT", "Hold-off comparator (discrete; keeps the contactor closed above its breaking capacity)", 1, "TLV3502AIDCNR", "Texas Instruments",
         PL["port_holdoff"][0], PL["port_holdoff"][1], "bom/PV-P75_costed_BOM.csv", "REAL (PV costed BOM 5k column)", True),
        ("DC PORT", "D-050 port protection: polarity + precharge-dV interlock comparators and the port over-current window with their logic "
         "(gen/port.py lean_port interlocks='full', oc_trip=True, battery port) - "
         + ", ".join(f"{n} x {k}" for k, n in PL["counts"]["port_added"].items() if not k[0].isdigit()), 1, "lean-port parts (gen/port.py)",
         "TI + passives", PL["port_interlocks_oc"][0], PL["port_interlocks_oc"][1], "bom/PV-P75_costed_BOM.csv unit prices",
         f"PV costed BOM 5k column (REAL {PL['port_interlocks_oc'][2]:.2f} USD)", PL["port_interlocks_oc"][2] / max(PL["port_interlocks_oc"][1], 1e-9) > 0.5),
        ("DC PORT", "DC varistor network + X/Y caps + CM core (PV rev-6 network)", 1, "3 x TVT25751 + 2 x 002637115 + CNY65B ...", "Thinking / ETI / Vishay",
         29.4, 24.6, "PV costfirst_bom rows", "ASSUMED x0.85", False),
        ("DC PORT", "DC terminals M8 + busbars", 1, "2 x terminal + 3 x busbar (CUSTOM)", "CUSTOM", 31.0, 24.8, "cost_estimates.csv basis", "ASSUMED x0.80", False),
        ("AC PORT", "AC disconnect: two 3-pole contactors in series (AC-1 >= 250 A, 24 V DC coil, U_i 1000 V)", 2, "NXC-225 / CJX2-185 class",
         "CHINT / Delixi (CN)", 45.0, 36.0, "ESTIMATE: no OEM price (R-06)", "ASSUMED x0.80", False),
        ("AC PORT", "AC contactor coil drivers + economiser", 2, "class", "class", 0.7, 0.6, "PV basis", "ASSUMED x0.85", False),
        ("AC PORT", "AC surge protection type II on board, monitored", 1, "TVT25 (300 VAC) x3 + GDT", "Thinking (TW) class", 9.3, 7.9,
         "ESTIMATE (architect)", "ASSUMED x0.85", False),
        ("AC PORT", f"AC common-mode choke {L_CM_AC*1e6:.0f} uH: {FP['cm_choke']['k']} x Yunlu N-R-564440 nanocrystalline cores over the phase "
         f"busbars + 3 x Y1 ({FP['cm_choke']['mass']:.1f} kg)", 1, f"{FP['cm_choke']['k']} x N-R-564440 + 3 x Y1", "Yunlu (CN)",
         FP["cm_choke"]["usd"][0], FP["cm_choke"]["usd"][1], "sim/magnetics.py pcs_cm_design (core 20 USD/kg ESTIMATE) + Y1 1.5 USD",
         "magnetics.py 5k factors (ASSUMED)", False) if FP else
        ("AC PORT", f"AC common-mode choke >= {L_CM_AC*1e6:.0f} uH at 32 kHz: 3 nanocrystalline cores over the phase busbars + 3 x Y1", 1,
         "3 x ring core + 3 x Y1", "Yunlu / AT&M class", 18.9, 15.1, "ESTIMATE (architect's 2-core row x1.5)", "ASSUMED x0.80", False),
        ("AC PORT", "AC terminals (L1-L3 + PE) + busbars", 1, "4 x terminal + 3 x busbar", "CUSTOM", 39.0, 31.2, "ESTIMATE (architect)", "ASSUMED x0.80", False),
        ("AUX SUPPLY", "75 W flyback (PV AUX) on the DC link + 6-diode AC tap", 1, "AUX block (PV) + 6 x 1600 V diode", "TI / InventChip / CUSTOM",
         37.3, 31.0, "PV costfirst_bom + 3.0 estimate", "MIXED: TI 1ku list, rest ASSUMED", False),
        ("CONTROL", "PV control board, PCS assembly variant: F280039C, clock, EEPROM, supervisor, front end, board connector, extra 2 x 8 header "
         "(the discrete protection layer is the next line)", 1, "PV control board (F280039CSPZR)", "TI + class", 9.19 - 1.80 - 0.163 + 0.5,
         (9.19 - 1.80 - 0.163 + 0.5) * 0.92, "PV costfirst_bom CONTROL 9.19 less its 1.80 protection estimate and the 0.163 heartbeat share, "
         "+ header 0.5", "MIXED: TI 1ku list, rest x0.85", False),
        ("CONTROL", f"D-050 discrete protection layer as drawn on PV-CTL (sheets 04 trip / 05 latch, PV-P75 variant): "
         f"{PL['counts']['control_3W_parts']} parts - "
         + ", ".join(f"{n} x {k}" for k, n in sorted(PL['counts']['control_3W_ICs'].items(), key=lambda kv: -kv[1]))
         + " + ladders, pull-ups, decoupling", 1, "PV-CTL sheets 04/05 (gen/pv_ctrl.py)", "TI / Nexperia + passives",
         PL["control_3W"][0], PL["control_3W"][1], "bom/PV-P75_costed_BOM.csv unit prices (TLV9024PWR: pv_ctrl PRICE estimate)",
         f"PV costed BOM 5k column (REAL {PL['control_3W'][2]:.2f} USD of it)", PL["control_3W"][2] / max(PL["control_3W"][1], 1e-9) > 0.5),
        ("INTERFACE", "Reinforced barrier + SELV zone (CAN, RS-485, stop, status, fans) - PV design", 1, "CA-IS3062W + CA-IS3082WNX + 3 x CA-IS3821 + SELV",
         "Chipanalog + class", 8.09, 7.5, "PV costfirst_bom INTERFACE rows", "MIXED: LCSC highest break - EVIDENCE for the isolators", False),
        ("THERMAL", "Extruded heatsink, one 150 x 400 mm section per leg (4.0 kg each), earthed", 3, "Al extrusion (CUSTOM)", "CUSTOM",
         4.0 * 4.7 + 5.0, (4.0 * 4.7 + 5.0) * 0.8, "ESTIMATE: 4.7 USD/kg + 5 USD machining (architect basis)", "ASSUMED x0.80", False),
        ("THERMAL", f"Fan 120 x 38 mm 24 V, {HS['fans_per_section']} per section, + guard", 3 * HS["fans_per_section"], FAN["part"], FAN["mfr"],
         FAN["price"]["cat"], FAN["price"]["5k"],
         "Master Electronics 12.85 @504 + guard 0.30 (architect)", "ASSUMED x0.70 on the 504+ break", False),
        ("MECH-ELEC", "Press-fit terminals, harness, standoffs", 1, "class", "class", 13.4, 10.7, "ESTIMATE (PV basis)", "ASSUMED x0.80", False),
    ]
    n_leg = sum(n for p_, (part_, n) in D["devs"].items())
    W = [
        ("4-WIRE OPTION", "Neutral leg: two-level half-bridge, 12 x SG2M040170HJ + pads + local decoupling + damper", 1, f"{part} x{n_leg}", "Sichain (CN)",
         n_leg * (pr[1] + 0.40) + 9 * 0.45 + (2 * 0.625 + 6 * 0.110) * 3, n_leg * (pr[1000] + 0.30) + 9 * 0.383 + (2 * 0.531 + 6 * 0.093) * 3,
         "rows above", "rows above", False),
        ("4-WIRE OPTION", "Gate drive 2 channels (incl. D-050 stretch + negative-rail detector) + bias", 1,
         "2 x NSI6651 channel + buffer + 1 x bias", "NOVOSENSE + CUSTOM",
         2 * (2.978 + 3.90 + PL["negdet_per_ch"][0]) + 3.03,
         2 * (1.544 + 3.32 + PL["negdet_per_ch"][1]) + 2.64, "rows above", "rows above", False),
        ("4-WIRE OPTION", "D-050 control-board phase-4 comparators (PV-CTL PV-P100/110 assembly: N-leg current window, its NTC)", 1,
         "2 x TLV9024PWR + passives", "TI + passives", PL["control_4W"][0] - PL["control_3W"][0], PL["control_4W"][1] - PL["control_3W"][1],
         "bom/PV-CTL_BOM.csv minus PV-CTL-P75", "PV costed BOM / pv_ctrl estimate", False),
        ("4-WIRE OPTION", f"Neutral inductor L_N {l1*1e6:.0f} uH (as L1: the neutral leg's ripple is that of a phase leg) + TMR sensor", 1,
         "L1 class + STK class", "CUSTOM + Sinomags", (FP["L1"]["usd"][0] if FP else L1_COST[0] + L1_COST[1] * e1) + 6.0,
         (FP["L1"]["usd"][1] if FP else (L1_COST[0] + L1_COST[1] * e1) * 0.8) + 4.8, "rows above", "rows above", False),
        ("4-WIRE OPTION", "Heatsink section + fan for the fourth leg", 1, "as THERMAL rows", "CUSTOM + Delta",
         4.0 * 4.7 + 5.0 + HS["fans_per_section"] * FAN["price"]["cat"], (4.0 * 4.7 + 5.0) * 0.8 + HS["fans_per_section"] * FAN["price"]["5k"],
         "rows above", "rows above", False),
        ("4-WIRE OPTION", "4-pole instead of 3-pole AC contactors, N terminal and busbar, GDT N-PE", 1, "class", "class", 35.0, 28.0, "ESTIMATE (architect)",
         "ASSUMED x0.80", False),
    ]
    return R, W


def step_g(D, rc, rd, re_):
    PL = protection_layer()
    R, W = bom_rows(D, rc, rd, re_, PL)
    tot = {"cat": sum(r[2] * r[5] for r in R), "5k": sum(r[2] * r[6] for r in R)}
    ev5 = sum(r[2] * r[6] for r in R if r[9])
    w = {"cat": sum(r[2] * r[5] for r in W), "5k": sum(r[2] * r[6] for r in W)}
    blocks = {}
    for r in R:
        b = blocks.setdefault(r[0], [0.0, 0.0])
        b[0] += r[2] * r[5]
        b[1] += r[2] * r[6]
    res = {"three_wire": {"catalogue": tot["cat"], "5k": tot["5k"], "per_kW_cat": tot["cat"] / 125.0, "per_kW_5k": tot["5k"] / 125.0},
           "four_wire": {"catalogue": tot["cat"] + w["cat"], "5k": tot["5k"] + w["5k"]},
           "four_wire_increment": w, "blocks": {k: {"catalogue": round(v[0], 2), "5k": round(v[1], 2)} for k, v in blocks.items()},
           "evidence_share_5k": ev5 / tot["5k"], "architect": {"three_wire": ARCH_COST["3W"], "four_wire": ARCH_COST["4W"]},
           "rows": [{"block": r[0], "function": r[1], "qty": r[2], "part": r[3], "maker": r[4], "unit_cat_usd": round(r[5], 3),
                     "unit_5k_usd": round(r[6], 3), "ext_cat_usd": round(r[2] * r[5], 2), "ext_5k_usd": round(r[2] * r[6], 2),
                     "basis_cat": r[7], "basis_5k": r[8]} for r in R + W],
           "basis": "catalogue = LCSC @1 / maker list / estimate as named per row; 5,000 units = the row's 5k basis; nothing quoted or bought",
           "protection_D050": {"control_3W": PL["control_3W"][:2], "control_4W": PL["control_4W"][:2], "port_holdoff": PL["port_holdoff"][:2],
                               "port_interlocks_oc": PL["port_interlocks_oc"][:2], "per_channel_stretch_negdet": (PL["stretch_per_ch"][0] + PL["negdet_per_ch"][0],
                                                                                                                 PL["stretch_per_ch"][1] + PL["negdet_per_ch"][1]),
                               "counts": PL["counts"]}}
    # the architecture's own T-type corrected: the IGBT count of step a and the three-level midpoint bank of step d (C3D2K117 110 uF,
    # 80 V pp allowed for the 800 V class -> the same 3.5 mF per half scaled by 60/80)
    arch = next(c for c in SPEC["topology_screen"]["candidates"] if c["name"] == "TT-IGBT (architect)")
    cnt = {p_: int(v.split(" x ")[0]) for p_, v in arch["devices"].items()}
    extra_dev = 3 * ((cnt["T1"] + cnt["T4"] - 8) * (dv.part_price("CRG40T120BK3SD", 10 ** 6) + PAD["5k"])
                     + (cnt["T2"] + cnt["T3"] - 6) * (dv.part_price("CRG50T60AK3SD", 10 ** 6) + PAD["5k"]))
    c_np = DCL_CACHE[(3, 16e3, round(l1_for_ripple({"topo": "TT", "fsw": 16e3}) * 1e7))]["C_np_mF"] * 1e-3 if (3, 16e3, round(l1_for_ripple({"topo": "TT", "fsw": 16e3}) * 1e7)) in DCL_CACHE else 3.5e-3
    n_arch = math.ceil(c_np * 60.0 / 80.0 / 110e-6)
    res["arch_corr"] = {"devices_5k": extra_dev, "dc_link_5k": 2 * (n_arch - 6) * 4.17, "caps_per_half": n_arch}
    SPEC["cost_usd"] = res
    with open(os.path.join(OUT, "pcs_costed_bom.csv"), "w", newline="") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["block", "function", "qty", "part", "maker", "unit_cat_usd", "ext_cat_usd", "unit_5k_usd", "ext_5k_usd", "basis_cat", "basis_5k"])
        for r in R + W:
            wcsv.writerow([r[0], r[1], r[2], r[3], r[4], round(r[5], 3), round(r[2] * r[5], 2), round(r[6], 3), round(r[2] * r[6], 2), r[7], r[8]])
    return res


def report_g(r):
    wr("\n## (g) Cost (catalogue and 5,000 units)\n")
    wr("Every line named and priced in sim/out/pcs_design/pcs_costed_bom.csv (and pcs_spec.json 'cost_usd'); the basis of each price is in "
       "the row.  Block totals:\n")
    wr("| block | catalogue (USD) | 5,000 units (USD) |")
    wr("|---|---|---|")
    for k, v in r["blocks"].items():
        wr(f"| {k} | {v['catalogue']:.0f} | {v['5k']:.0f} |")
    pl = r["protection_D050"]
    per_ch = pl["per_channel_stretch_negdet"]
    wr(f"\n**Discrete protection layer (D-050, as on the PV module; counts from the PV boards, prices from bom/PV-P75_costed_BOM.csv):** "
       f"control board {pl['control_3W'][0]:.2f} / {pl['control_3W'][1]:.2f} USD (PV-CTL sheets 04 / 05, {pl['counts']['control_3W_parts']} parts; "
       f"four-wire = the PV-P100/110 assembly with the fourth window, +{pl['control_4W'][0]-pl['control_3W'][0]:.2f} USD); DC port: hold-off "
       f"comparator {pl['port_holdoff'][0]:.2f} USD (replaces a 0.5 USD guess), polarity + precharge-dV interlocks and the over-current window "
       f"{pl['port_interlocks_oc'][0]:.2f} / {pl['port_interlocks_oc'][1]:.2f} USD; per gate-drive channel stretch + negative-rail detector "
       f"{per_ch[0]:.2f} / {per_ch[1]:.2f} USD ({pl['counts']['stretch_per_channel']} + {pl['counts']['negdet_per_channel']} parts; the stretch "
       f"was already inside the PV channel estimate and is now its own line).  Firmware duplicates each trip as the second layer.")
    wr("\n**Where the PV discrete layer does not carry over unchanged:** (1) the phase-current windows keep their TLV9024 comparators but "
       "move to +-450 A on +-500 A TMR sensors: the sensor gain and the ladder values change, and the trip path (sensor response + comparator "
       "+ latch + driver) must stay within about 1.5 us at di/dt = V_dc/L1 = 9.8 A/us - with the PV's 73 A / 224 uH it was 4.5 A/us; the "
       "sensor's step response is the open data item; (2) the dead-time stretch needs a new gen/gdrv.py preset for six gates behind a buffer "
       "(step e); (3) the AC side has no counterpart of the DC-port interlocks: no hold-off (the converter current is zero after a trip; a "
       "short behind the contactors is grid-fed) and no hardware synchronism check (firmware; an unsynchronised close is a stressed unit, not "
       "a hazard); (4) grid over / under voltage and frequency, anti-islanding and the residual-current trips are firmware-only by nature "
       "(slow, code-dependent), with the RCMU self-test; (5) the second DC port of the PV board does not exist here - one port's interlocks.")
    t3, t4, a3, a4 = r["three_wire"], r["four_wire"], r["architect"]["three_wire"], r["architect"]["four_wire"]
    wr(f"| **three-wire total** | **{t3['catalogue']:.0f} ({t3['per_kW_cat']:.1f} USD/kW)** | **{t3['5k']:.0f} ({t3['per_kW_5k']:.1f} USD/kW)** |")
    wr(f"| four-wire increment | {r['four_wire_increment']['cat']:.0f} | {r['four_wire_increment']['5k']:.0f} |")
    wr(f"| **four-wire total** | **{t4['catalogue']:.0f}** | **{t4['5k']:.0f}** |")
    wr(f"\n**Against the architecture:** three-wire {t3['catalogue']:.0f} / {t3['5k']:.0f} USD against its {a3[0]:.0f} / {a3[1]:.0f} USD "
       f"({(t3['catalogue']/a3[0]-1)*100:+.0f} % / {(t3['5k']/a3[1]-1)*100:+.0f} %); four-wire {t4['catalogue']:.0f} / {t4['5k']:.0f} against "
       f"{a4[0]:.0f} / {a4[1]:.0f} USD.  The architecture's figures rest on 42 IGBTs that overheat (step a: 66 needed) and on a 660 uF "
       f"midpoint bank that its own modulation cannot hold at PF 0 (step d: 25 capacitors per half for any three-level design); "
       f"corrected for those two (+{r['arch_corr']['devices_5k']:.0f} USD of IGBTs and pads, +{r['arch_corr']['dc_link_5k']:.0f} USD of DC "
       f"link at 5k) its T-type would cost about {a3[1] + r['arch_corr']['devices_5k'] + r['arch_corr']['dc_link_5k']:.0f} USD at 5k"
       + (" on the architect's filter prices (section h prices it with the real inductors).  " if FP else ".  ")
       + f"Evidence behind the 5k figure: {r['evidence_share_5k']*100:.0f} % (LCSC breaks, marketplace quote); the rest are assumed factors "
       f"0.68-0.85 on estimates - the inductors, the contactors, the fuses, the heatsink and the SiC device price are RFQ items.")


def report_h():
    """(h) the power-stage and filter re-optimisation with the real inductor designs (sim/pcs_tradeoff.py; full tables in tradeoff.md)"""
    wr("\n## (h) Re-optimisation with the real inductor designs (sim/pcs_tradeoff.py)\n")
    try:
        t = json.load(open(TRADEOFF))
    except (OSError, ValueError):
        wr("Not run yet: the filter of steps c and g is priced with the step-c loss budgets and the architect's estimates.  Run "
           "sim/pcs_tradeoff.py, then this script again.")
        return
    wr(t["basis"] + "\n")
    wr("| candidate | 5k USD (catalogue) | peak / full-load (750 V) efficiency | filter 5k USD | filter kg | filter W at 125 kW, 750 V | meets the limits |")
    wr("|---|---|---|---|---|---|---|")
    sweep = ("A1", "A2", "A1xA2")
    best_of = {g: min((r for r in t["rows"] if r["group"] == g), key=lambda r: r["cost"]["5k"])["name"] for g in sweep
               if any(r["group"] == g for r in t["rows"])}
    for r in t["rows"]:
        if r["group"] in sweep and r["name"] != best_of[r["group"]]:
            continue                    # sweeps: their cheapest point here, every point in tradeoff.md
        wr(f"| {r['name']} | {r['cost']['5k']:.0f} ({r['cost']['cat']:.0f}) | {r['eta_peak']*100:.2f} / {r['eta_full_750V']*100:.2f} % | "
           f"{r['cost']['filter'][1]:.0f} | {r['filter_mass_kg']:.0f} | {r['filter_loss_W']['125kW_750V']:.0f} | "
           f"{'yes' if r['ok'] else 'no (' + ', '.join(k for k, v in r['accept'].items() if not v) + ')'} |")
    for x in t["conclusion"]:
        wr(f"\n{x}")
    SPEC["tradeoff"] = {k: t[k] for k in ("recommended", "two_level_best", "runner_up", "conclusion")}
    SPEC["tradeoff"]["rows"] = [{"name": r["name"], "usd_5k": round(r["cost"]["5k"], 1), "usd_cat": round(r["cost"]["cat"], 1),
                                 "eta_peak": round(r["eta_peak"], 5), "filter_usd_5k": round(r["cost"]["filter"][1], 1),
                                 "filter_kg": round(r["filter_mass_kg"], 1), "ok": r["ok"]} for r in t["rows"]]


def module_section(rows):
    """owner's input: three-level modules per phase next to the paralleled discretes - side-by-side table and the data-sheet verdicts"""
    wr("\n### Three-level modules (one per phase) against paralleled discretes\n")
    wr("Owner's input: one three-level module per phase removes the paralleling and may settle the cosmic-ray question.  Searched: HIITIO "
       "(catalogue: I-type NPC modules in Easy 2B / 3B at 650 V and 1200 V; 62 mm / E6 half bridges at 1700 V), StarPower (GD200TLQ120L3S "
       "3-level 1200 V 200 A is listed by RS, but the RS and Farnell pages refuse plain requests - not used), Macmic, Silan, CRRC, BYD, "
       "Leapers, AccoPower, Yangjie (no three-level module with a public data sheet or price found in this pass; sim/data/asia_modules.* "
       "from the parallel researcher was not on disk when this ran).  Two HIITIO data sheets filed:\n")
    wr("| module | circuit, package | data sheet | E_on / E_off (conditions) | R_th | isolation | short circuit | qualification | Western class | price (indicative) |")
    wr("|---|---|---|---|---|---|---|---|---|---|")
    for k, m in dv.MODULES.items():
        e = m["check"]
        wr(f"| {k} | {m['circuit']}, {m['package']} | {m['rev']}, complete tables + curves (curves coarse) | "
           f"{e[0][4]*1e3:.2f} / {e[1][4]*1e3:.2f} mJ at {e[0][1]} V, {e[0][2]} A, {e[0][3]:.0f} C | {m['rth_jc']} / {m['rth_jc_d']} K/W j-heatsink | {m['visol_V']/1e3:.0f} kV rms 60 s, basic | **none stated** | "
           f"{m['qualification'].split('(')[0].strip()} | {m['western_class']} | {m['price'][1]:.0f} / {m['price'][1000]:.0f} USD - {m['price_note']} |")
    wr("\n**Verdict on the data sheets:** good enough to compute losses and temperatures, **too thin to release**: no short-circuit "
       "withstand time (the DESAT blanking and response cannot be set against a rating), no qualification or reliability statement, no "
       "Western part named for the 'clone' claim, no price.\n")
    mods = [r for r in rows if r.get("module")]
    disc = [r for r in rows if r["name"] in ("TT-SiC 1700(40m)/1200", "NPC-SiC 1200 + SBD", "TT-IGBT (best documented)", "2L-SiC 1700(40m)")]
    wr("| option | per phase: power stage USD cat / 5k (devices, pads, gate drive) | device loss 125 kW @750 V | Tj / feasibility at the sizing "
       "corners (45 C) | gate channels per phase | assembly per phase | V/V_rated @950 V |")
    wr("|---|---|---|---|---|---|---|")
    for r in mods + disc:
        asm = ("1 screw-mounted module, press-fit pins, no paralleling" if r.get("module")
               else f"{r['cost']['cat']['n_devices']//3} TO-247 with clips and pads, paralleled per position")
        feas = ("NOT feasible - " + r["infeasible"]) if r.get("infeasible") else f"meets all limits (min margin {min(r['margins'].values()):.0f} K)"
        wr(f"| {r['name']} ({r['fsw']/1e3:.0f} kHz) | {r['cost']['cat']['per_phase_power_stage']:.0f} / {r['cost']['5k']['per_phase_power_stage']:.0f} | "
           f"{r['loss_full_W']:.0f} W | {feas} | {r['cost']['cat']['n_channels']//3} | {asm} | {r['rule']:.2f} |")
    wr("\n- A 300-400 A three-level module per phase does not carry this product: 198 A continuous is fine, but 216 A for 2 min and "
       "1.2 x 216 A for 200 ms at PF 0 push the inner IGBTs past the module's 150 C operating limit with the same heatsink; two modules "
       "per phase (or a 600 A class EconoDUAL / 62 mm three-level part) double the money.  Its turn-off energy (23 mJ at 600 V / 400 A, "
       "125 C) keeps it at 8-16 kHz, i.e. the large filter.")
    wr("- Every three-level option - module or discrete - also carries the 150 Hz midpoint DC link of step d (25 film capacitors per half).")
    wr("- **The 650 V I-type and cosmic rays:** Semikron's data (AN 17-003 p9) put 650 V chips below 1 FIT/cm2 only up to 500 V per "
       "device.  At 950 V a 650 V I-type device blocks 475 V (0.73 of its rating) and 522 V at the edge of the 10 % midpoint band (0.80) - "
       "past both the project's 0.67 rule and the last published data point; an outer/inner turn-off sequencing fault in an I-type also puts "
       "more than V_dc/2 across one device.  It does not settle the question; the 1200 V I-type module does (0.44), but at the loss and "
       "cost above.")
    SPEC["modules"] = {"searched": "HIITIO, StarPower, Macmic, Silan, CRRC, BYD, Leapers, AccoPower, Yangjie",
                       "filed": list(dv.MODULES), "result": {r["name"]: {"feasible": not r.get("infeasible"), "why": r.get("infeasible"),
                                                                        "per_phase_usd_5k": round(r["cost"]["5k"]["per_phase_power_stage"], 1),
                                                                        "device_loss_125kW_W": round(r["loss_full_W"])} for r in mods}}


def save():
    """write pcs_spec.json and report.md (called after every step so the files exist early)"""
    os.makedirs(OUT, exist_ok=True)

    def clean(o):
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, float) and not math.isfinite(o):
            return None
        return o
    with open(os.path.join(OUT, "pcs_spec.json"), "w") as f:
        json.dump(clean(SPEC), f, indent=1)
    with open(os.path.join(OUT, "report.md"), "w") as f:
        f.write("\n".join(REP) + "\n")


def handover(D, rb, rc, rd, re_, rf, rg_):
    """what the control engineer, the board designers and the firmware need next (written to report and spec)"""
    F = rc["filter"]
    ctrl = {
        "plant": {"L1_uH": round(F["L1"] * 1e6, 1), "Cf_uF_star": F["Cf"] * 1e6, "L2_uH": F["L2"] * 1e6, "Rd_ohm": F["Rd"], "Cd_uF": F["Cd"] * 1e6,
                  "R_L1_mohm_est": round((FP["L1"]["loss_per_phase"]["k2"] if FP else BUDGET["L1_cu_W_per_phase_180A"] / I_RATED ** 2) * 1e3, 2), "f_res_Hz": rc["f_res_Hz"],
                  "grid_SCR_range": "5 .. stiff", "C_dc_series_uF": rd["C_series_uF"], "C_f_star": "tied to the DC midpoint"},
        "sampling": {"f_sw_Hz": FSW_CHOICE, "f_s_Hz": F_S, "delay_s": T_DELAY, "dead_time_ns": re_["dead_time_ns"],
                     "dead_time_error": f"{2 * re_['dead_time_ns'] * 1e-9 * FSW_CHOICE * 100:.1f} % volt-seconds -> compensate (THDi < 3 %)"},
        "current_loop": f"L1 current (sensor on the C_f side of L1): resonance {min(rc['f_res_Hz'].values())/1e3:.1f}-"
                        f"{max(rc['f_res_Hz'].values())/1e3:.1f} kHz is below f_s/6 = {F_S/6e3:.1f} kHz, where converter-current "
                        f"feedback with 1.5 T_s delay is inherently damped; keep the bandwidth <= {BW_LOOP/1e3:.2f} kHz (below f_res(SCR 5)/2), resonant terms "
                        "at h5/h7/h11/h13; grid current for PF/THD = i_L1 - C_f dv_Cf/dt; passive R_d-C_d branch as the fallback damping",
        "pll": "on the C_f (or terminal) voltages, 400 V +-15 %, 50/60 Hz, SCR 5..stiff, unbalance and LVRT/HVRT (EN 50549-1 / GB/T 34120)",
        "modulation": ("two-level, min-max zero sequence (SVPWM-equivalent) at every operating point (section h)" if MOD_2L == "minmax" else
                       "two-level, sinusoidal where m <= 0.98, min-max zero sequence above (V_dc < ~680 V at 400 V, < ~780 V at 460 V)"),
        "midpoint": f"no control (two-level); firmware plausibility of the half voltages; C_f-star 150 Hz current <= {rd['cf_star_lf_cm_A_max']:.0f} A "
                    f"rms, ripple <= {max(rd['midpoint_pp_V'].values()):.0f} V pp",
        "grid_forming": f"voltage control on C_f (L1-C_f with the damping branch), current limit 1.2 x 216 A for 200 ms (Tj "
                        f"{max(o['tj_200ms_C'] for o in rb['overload']):.0f} C at the worst corner, step b), 120 % for 2 min, transitions grid "
                        f"<-> off-grid < 20 ms (Megarevo)",
        "four_wire": f"neutral leg two-level with L_N = {F['L1']*1e6:.0f} uH, reference = -sum of the phase currents' zero sequence; "
                     "100 Hz battery current of single-phase load (~39 A rms at 750 V) is an installation item",
        "limits": {"I_phase_trip_A": OC_TRIP, "I_dc_trip_A": 400.0, "V_dc_trip_V": V_OV_TRIP}}
    boards = {
        "power_stage": f"3 two-level legs (4 for four-wire): 6 x {D['devs']['TH'][0]} per switch (TO-247-4L, Kelvin source), Al2O3 pads on "
                       "one earthed section per leg; per device pair 3 x 2.2 uF / 1300 V film at the pins + RC damper (2 x 4.7 nF 2 kV C0G, "
                       "6 x 15 ohm 2512); DC link 5 + 5 x C3D1U147 in two series halves, midpoint to the C_f star",
        "gate_drive": f"6 channels (8 four-wire): NSI6651ASC + NPN/PNP buffer per channel driving 6 gates, R_G,on {re_['chosen']['R_G_on_ext_ohm_per_device']:.2f} / "
                      f"R_G,off {re_['chosen']['R_G_off_ext_ohm_per_device']:.1f} ohm per device, R_KS 0.5 ohm, per-gate clamp FET, DESAT 100 ohm + 3 x US1MH, "
                      "booster, RC dead-time stretch and negative-rail detector (stretch=True, neg_det=True - new '6 x SG2M040170HJ' "
                      "preset needed), rails +18 / -3.5 V; bias: one SN6505B transformer per phase (2 secondaries); default-off pull-downs; "
                      "EN from the external stop (wired-AND); FLT/RDY to the latch and the trip zone (D-050)",
        "control_board": "PV-CTL as drawn (gen/pv_ctrl.py), PV-P75 assembly for three-wire, PV-P100/110 assembly for four-wire: discrete "
                         "window comparators for each phase current (ladders re-valued for +-450 A on +-500 A TMR), DC over-voltage 1050 V, "
                         "over-temperature and open probe, the set-dominant latch, heartbeat watchdog, AND gating of 6 (8) PWM, EN, K_DC, "
                         "K_PRE, K_AC1 and K_AC2 (a spare LVC08 gate); the controller's CMPSS / ADC limits / trip zone as the second layer",
        "sensing": {"phase_current": "3 (4) open-loop TMR +-500 A on the C_f side of L1, bandwidth >= 100 kHz for the CMPSS window at +-450 A",
                    "grid_voltage": "terminal and C_f nodes, L1-L3 (+N), dividers to DC-: signal V_dc/2 +-375 V peak + 20 % surge headroom",
                    "dc": "V_DC+ - V_DC-, V_mid, terminal (bipolar, polarity), shunt 2 x 200 uOhm (25 mV at 250 A), ADC limit trips 400 A / 1050 V",
                    "residual_current": "type-B fluxgate over L1-L3 (+N), 30 mA resolution, 1.25 A continuous range",
                    "temperatures": "NTC: 3 (4) heatsink sections, 3 (4) L1, DC link, inlet"},
        "ports": "DC: HFE82V-300C/1000, 2 x aR 400 A, precharge 220 ohm, gen/port.py lean_port interlocks='full', oc_trip=True (hold-off, "
                 "polarity and precharge-dV interlocks, over-current window, all discrete); AC: 2 x 3-pole contactor in series (4-pole "
                 "four-wire), coil drivers with economiser gated by the latch, type II SPD, CM choke >= 150 uH (3 nanocrystalline cores)"}
    fw = ["second layer of every discrete trip (D-050): CMPSS phase windows +-450 A (digital filter <= 0.5 us), ADC limits I_dc 400 A and "
          "V_dc 1050 V, heatsink / L1 over-temperature from the NTCs, the DC-port polarity and precharge-dV conditions re-checked before any "
          "coil command, trip zone forcing all PWM low; the hardware latch is cleared by firmware only with no source active",
          "start-up self-test of every trip path, hardware and controller (inject through the ladders / DACs, read the latch and FLT/RDY), "
          "heartbeat to the discrete watchdog, read-back and lock of PWM, dead-band (300 ns, the gate-drive stretch is the floor) and trip "
          "configuration",
          "sequencing: DC precharge (polarity, |V_bank - V_bat| <= 10 V - also enforced in hardware), discharge through the precharge path on "
          "stop; insulation test (IMD) before every connection with the contactors open; inverter forms the grid voltage on C_f, synchronises "
          "(|dV| <= 5 %, 5 deg), runs the relay test (each contactor alone, voltage across the open one), closes; opens only at zero current",
          "grid protection EN 50549-1 / GB/T 34120: U/f windows and times, ROCOF / vector shift, active anti-islanding where IEC 62116 applies, "
          "LVRT / HVRT with reactive current, P(f), Q(U), cos phi(P) (settings by country)",
          "residual current (type-B sensor): 30 / 60 / 150 mA step trips within 0.3 / 0.15 / 0.04 s and the continuous limit per code (from "
          "memory - verify), sensor self-test",
          "current limit 1.2 x 216 A for <= 200 ms then trip; 120 % for <= 2 min; device thermal model; inlet-temperature derating (fan "
          "limit 60 C); fan speed control; open / shorted NTC plausibility",
          "monitoring and non-hazard edge cases: dead-time compensation, the modulation policy (" + ("min-max everywhere" if MOD_2L == "minmax" else "sinusoidal where m <= 0.98") + "), half-voltage "
          "plausibility, varistor and contactor feedback, unsynchronised-close prevention (a stressed unit, not a hazard: no extra hardware)"]
    SPEC["handover"] = {"control_engineer": ctrl, "board_designers": boards, "firmware_requirements": fw}
    wr("\n## Hand-over\n")
    wr("**Control engineer (plant and limits; pcs_spec.json 'handover'):**")
    wr(f"- LCL: L1 {F['L1']*1e6:.0f} uH (R about {ctrl['plant']['R_L1_mohm_est']} mOhm), C_f {F['Cf']*1e6:.0f} uF star (tied to the DC midpoint), "
       f"L2 {F['L2']*1e6:.0f} uH, R_d {F['Rd']:.0f} ohm + C_d {F['Cd']*1e6:.0f} uF per phase; resonance "
       + ", ".join(f"{k} {v/1e3:.2f} kHz" for k, v in rc["f_res_Hz"].items()) + f"; DC link {rd['C_series_uF']:.0f} uF across the bus.")
    wr(f"- Sampling {F_S/1e3:.0f} kHz double update, delay {T_DELAY*1e6:.0f} us, dead time {re_['dead_time_ns']:.0f} ns ({ctrl['sampling']['dead_time_error']}).")
    for k in ("current_loop", "pll", "modulation", "midpoint", "grid_forming", "four_wire"):
        wr(f"- {k.replace('_', ' ')}: {ctrl[k]}")
    wr("\n**Board designers:**")
    wr(f"- power stage: {boards['power_stage']}")
    wr(f"- gate drive: {boards['gate_drive']}")
    wr(f"- control board: {boards['control_board']}")
    for k, v in boards["sensing"].items():
        wr(f"- sensing, {k.replace('_', ' ')}: {v}")
    wr(f"- ports: {boards['ports']}")
    wr("\n**Firmware requirements this design relies on (D-050: firmware duplicates every hardware trip and takes sequencing, monitoring "
       "and non-hazard edge cases):**")
    for x in fw:
        wr(f"- {x}")


def unknowns():
    wr("\n## What is not verified (and where it would change the design)\n")
    for x in ["Nothing is measured: every loss, temperature, overshoot and spectrum here is calculated from data-sheet curves (read by eye, "
              "+-5 %) or simulated with fitted VDMOS models.",
              "Sichain SG2M040170HJ: no public price (4.07 USD is the PV module's estimate), no qualification statement, no short-circuit "
              "withstand (2 us assumed), no cosmic-ray data (the 0.67 rule is applied to its 1700 V rating by scaling Wolfspeed's 1200 V curve). "
              "The qualified fallback of the PV module (Microchip MSC035SMA170B4, 39 USD) does not fit this product's budget at 36 devices.",
              "Paralleling six TO-247 per switch: sharing k = 1.10 needs one-lot or binned devices and the per-pair decoupling layout; both are "
              "requirements, not results.  The commutation decks use the PV leg scaled x3 - re-run with the extracted layout.",
              "Inductors, contactors, the 400 A aR fuse, the heatsink, the film capacitors and the CM cores have no quotes; "
              f"{(1 - SPEC['cost_usd']['evidence_share_5k']) * 100:.0f} % of the 5,000-unit money is an assumed factor"
              + ("; the inductor prices are sim/magnetics.py's material + labour model (core and conductor USD/kg ESTIMATES), and its "
                 "search covers gapped nanocrystalline / amorphous C-cores only - no powder block cores." if FP else "."),
              "Standards: EN 50549-1, IEC 62109-2, IEEE 1547 (the harmonic limits used), IEC 62116, GB/T 34120 - from memory, texts not on file.",
              "Battery capacitance to earth (1-20 uF range assumed) decides the three-wire TN installation limit "
              + ("at every DC voltage (min-max PWM)." if MOD_2L == "minmax" else "below 680 V."),
              "Control: current loop, PLL, grid forming, four-wire neutral control and the THDi < 3 % at rated power are not simulated here "
              "(ARCHITECTURE-PCS section 9 items 3-7).",
              "Three-level modules (HIITIO): data sheets without short-circuit rating or qualification; prices indicative only.",
              "Fan AFB1224SHE-F00 is rated -10..+60 C: below -10 C start and at 60 C inlet it is outside / at its limit (PV R-05)."]:
        wr(f"- {x}")


def tradeoff_line():
    """one sentence on the re-optimisation (section h) for the summary"""
    t = SPEC.get("tradeoff", {})
    rows = {r["name"]: r for r in t.get("rows", [])}
    tt = [r for r in rows.values() if r["name"].startswith("B")]
    me = rows.get(t.get("two_level_best"), {})
    if not (tt and me):
        return ""
    ok = [r for r in tt if r["ok"]]
    b = min(ok or tt, key=lambda r: r["usd_5k"])
    cheap = min(tt, key=lambda r: r["usd_5k"])
    return (f"With the real inductor designs (section h) it is also the cheapest: {me['usd_5k']:.0f} USD at 5k against {b['usd_5k']:.0f} USD "
            f"for the cheapest compliant T-type ({b['name']})"
            + (f"; {cheap['name']} ({cheap['usd_5k']:.0f} USD) puts its 1200 V outer devices at {V_OV_TRIP / 1200:.2f} of rating at the {V_OV_TRIP:.0f} V trip." if cheap is not b else "."))


def summary(choice, rb, rc, rd, re_, rf, rg_):
    ef = rb["efficiency"]
    fit = SPEC["cosmic_ray"]["table"]
    lines = ["## Summary", "",
             f"- **Topology: two-level, 1700 V SiC ({choice['devs']['TH'][1]} x SG2M040170HJ per switch, {6 * choice['devs']['TH'][1]} devices, "
             f"6 gate channels), {FSW_CHOICE/1e3:.0f} kHz** - replaces the "
             f"architecture's three-level T-type with 1200 V IGBTs.  Every device at <= 0.56 of its rating at 950 V (rule 0.67); the T-type's "
             f"outer IGBTs sit at 0.79 and would fail by cosmic rays {fit[0]['FIT_950V']:.0f} FIT per unit at 950 V (25 C, sea level; Semikron "
             f"AN 17-003 data) against {fit[2]['FIT_950V']:.2f} FIT here.  "
             + (tradeoff_line() if FP else "The two-level design is also the cheapest: three-level legs need a 3.5 mF per-half midpoint "
                "bank for PF 0 that two-level does not."),
             f"- **Efficiency (calculated):** peak {re_['eta_peak_corr']*100:.2f} % (>= 98.5 % met), {re_['eta_full_corr']*100:.2f} % at 125 kW / 750 V; "
             f"worst Tj {rb['envelope_45C']['tj_max_C']:.0f} C at 110 % / 45 C inlet, {rb['tj_110pct_60C']:.0f} C at 60 C, "
             f"{max(o['tj_200ms_C'] for o in rb['overload']):.0f} C after 1.2 x 216 A for 200 ms (175 C rating).",
             f"- **Filter:** L1 {rc['filter']['L1']*1e6:.0f} uH, C_f {rc['filter']['Cf']*1e6:.0f} uF (star on the DC midpoint), L2 {rc['filter']['L2']*1e6:.0f} uH; "
             f"resonance {min(rc['f_res_Hz'].values())/1e3:.1f}-{max(rc['f_res_Hz'].values())/1e3:.1f} kHz; PWM THDi < 0.1 % (the 3 % is the controller's)"
             + (f"; inductors from sim/magnetics.py: 3 x L1 {CORE_WORD[FP['L1']['mat']]} {FP['L1']['mass_kg']:.1f} kg, 3 x L2 {FP['L2']['mass_kg']:.1f} kg, "
                f"CM choke {FP['cm_choke']['k']} cores - filter {FP['cost']['filter'][0]:.0f} / {FP['cost']['filter'][1]:.0f} USD, "
                f"{FP['filter_mass_kg']:.0f} kg, {FP['filter_loss_W']['125kW_750V']:.0f} W at 125 kW / 750 V." if FP else "."),
             f"- **DC link:** {rd['per_half']} + {rd['per_half']} x Faratronic C3D1U147 film, > 100 kh at 60 C inlet; electrolytics would need "
             f"{rd['electrolytic_alternative']['cans']} cans and last {min(rd['electrolytic_alternative']['life_h'].values())/1e3:.0f} kh.",
             f"- **Cost:** {rg_['three_wire']['catalogue']:.0f} / {rg_['three_wire']['5k']:.0f} USD (catalogue / 5,000 units) three-wire, "
             f"{rg_['four_wire']['catalogue']:.0f} / {rg_['four_wire']['5k']:.0f} USD four-wire, against the architecture's 1,037 / 801 and "
             + (f"1,194 / 919 USD, whose filter is priced at 10 + 6 USD/J; with the real inductors and its corrected IGBT count the "
                f"architecture's T-type comes to {next((r['usd_5k'] for r in SPEC['tradeoff']['rows'] if r['name'].startswith('B-IGBT')), float('nan')):.0f} "
                f"USD at 5k (section h)." if FP and SPEC.get("tradeoff") else
                f"1,194 / 919 USD - which become about {SPEC['topology_screen'] and (rg_['architect']['three_wire'][1] + rg_['arch_corr']['devices_5k'] + rg_['arch_corr']['dc_link_5k']):.0f} "
                f"USD at 5k for its own T-type once its IGBT count and midpoint bank are corrected."), ""]
    REP[4:4] = lines


def self_check(choice, rb, rc, rd, re_, rf, rg_):
    """assert-based: exits non-zero on failure"""
    lim = pv.V_DC_FRAC
    assert rule_ratio(choice) <= lim + 1e-9, "0.67 rule"
    assert re_["eta_peak_corr"] >= ETA_REQ, "peak efficiency"
    assert rb["efficiency"]["full_load_worst"] >= 0.98, "full-load efficiency"
    assert rb["envelope_45C"]["tj_max_C"] <= TJ_LIM["sic"][0], "Tj 110 % / 45 C"
    assert all(o["tj_120pct_2min_C"] <= TJ_LIM["sic"][1] and o["tj_200ms_C"] <= 175.0 for o in rb["overload"]), "overload Tj"
    assert re_["worst_1050V_450A_30nH"]["v_pk"] <= pv.V_PK_FRAC * pv.MOSFETS[choice["devs"]["TH"][0]]["vdss"] + 1e-6, "commutation peak"
    assert all(x["THD_h2_50_pwm"] < 0.03 and x["largest_above_h50"][1] < I2_LIM_FRAC for x in rc["spectrum"]), "harmonics"
    assert all(f < F_S / 6 for f in rc["f_res_Hz"].values()), "resonance below f_s/6"
    assert rd["I_per_cap_A"] <= pv.CAP_IRMS_USE * FILM[rd["part"]]["imax"] + 1e-9, "film ripple current"
    assert max(rd["midpoint_pp_V"].values()) <= rd["U_pp_max_V"], "midpoint ripple"
    assert rg_["four_wire"]["5k"] > rg_["three_wire"]["5k"] > 0, "cost totals"
    assert dv.self_check()
    assert "inductors" in SPEC and "cost_usd" in SPEC and "L1" in SPEC["inductors"], "spec blocks"
    for k in ("nominal_950V", "worst_1050V_450A_30nH"):
        assert os.path.exists(os.path.join(ROOT, re_[k]["deck"])), "ngspice deck missing"
    return True


def run():
    os.makedirs(OUT, exist_ok=True)
    REP.extend(["# PCS-P125 power stage - design report (sim/pcs_design.py)", "",
                "**Everything here is CALCULATED or SIMULATED (averaged loss model on data-sheet curves, analytic filter / thermal models, "
                "ngspice transients). Nothing is measured or bench-validated.** Device data and page references: sim/pcs_devices.py and "
                "sim/pv_devices.py. Requirements: REQUIREMENTS.md AC-01..03, SRC-1..6. Re-run: `.venv/bin/python sim/pcs_design.py`.", ""])
    rows, choice, arch = step_a()
    if FP:          # the trade study's device count: sized with E_on / E_off at the drawn gate resistors, AC 340-460 V and 1.3 sharing at 60 C
        choice["devs"] = {p: (v.split(" x ")[1], int(v.split(" x ")[0])) for p, v in FP["devices"].items()}
    report_a(rows, choice, arch)
    cosmic_section(rows, choice)
    module_section(rows)
    lev = TOPO[choice["topo"]]["levels"]
    SPEC["design"] = {"topology": ("two-level" if lev == 2 else "three-level " + choice["topo"]) + ", three-wire (four-wire = one more leg)",
                      "fsw_Hz": choice["fsw"], "modulation": ("min-max zero sequence everywhere" if MOD_2L == "minmax" else "sinusoidal where m <= 0.98, min-max zero sequence above") if lev == 2 else "min-max",
                      "devices": {p: {"part": part, "n_parallel": n} for p, (part, n) in choice["devs"].items()}}
    save()
    D = design_from(choice)
    # pre-pass of step e: the gate resistor that the commutation needs sets the E_off factor the loss map must carry
    re_ = step_e(D, {"ripple_pp_950V_A": 950.0 / (4 * FSW_CHOICE * D["l1"]) if lev == 2 else 950.0 / (8 * FSW_CHOICE * D["l1"])})
    D, rb = step_b(choice)
    report_b(D, rb)
    save()
    rc = step_c(D)
    report_c(rc)
    save()
    rd = step_d(D, rc)
    report_d(rd)
    save()
    report_e(re_)
    save()
    rf = step_f(D, rc, rd)
    report_f(rf, rc)
    save()
    rg_ = step_g(D, rc, rd, re_)
    report_g(rg_)
    report_h()
    handover(D, rb, rc, rd, re_, rf, rg_)
    unknowns()
    summary(choice, rb, rc, rd, re_, rf, rg_)
    save()
    self_check(choice, rb, rc, rd, re_, rf, rg_)
    print("\n".join(REP[:14]))
    print("pcs_design self-check passed")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"pcs_design SELF-CHECK FAILED: {e}")
        sys.exit(1)
