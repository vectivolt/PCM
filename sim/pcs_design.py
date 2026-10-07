"""PCS-P125 three-phase bidirectional battery inverter - power-stage design (REQUIREMENTS AC-01..03, SRC-6).

Run:  .venv/bin/python sim/pcs_design.py
Out:  sim/out/pcs_design/{pcs_spec.json, report.md, *.csv}, ngspice decks sim/spice/pcs_*.cir
Device data: sim/pcs_devices.py (IGBTs, SiC Schottky, cosmic-ray data, prices) + sim/pv_devices.py (SiC MOSFETs).
Every value here is CALCULATED (datasheet-based averaged loss model, analytic filter/thermal models, ngspice transients);
nothing is measured or bench-validated.  Steps (in the order the script runs them):
  a  topology / device screen incl. the cosmic-ray failure rate     b  loss + thermal map over the envelope
  c  switching frequency + LCL filter, harmonic spectrum, THDi       d  DC link
  e  commutation (ngspice) at the trip chain's gates-off current       f  AC / DC port, common mode and leakage
     (protection chain), gate drive, dead time, short circuit
  g  cost (catalogue and 5,000 units)                                then: hand-over lists, report, self-check
"""
import csv
import hashlib
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
    = a(V_dc) + k2 I^2.  None only while tradeoff.json does not exist yet (first run on a fresh checkout: run() then demands
    --bootstrap, and the step-c budgets and the architect's estimates stand in, the report says so); a file that exists but cannot
    be read or has no 'two_level_best' row stops the script (fail closed, PCM-23)"""
    if not os.path.exists(TRADEOFF):
        return None
    try:
        t = json.load(open(TRADEOFF))
        return next(r for r in t["rows"] if r["name"] == t["two_level_best"])
    except (OSError, KeyError, StopIteration, ValueError, TypeError) as e:
        raise SystemExit(f"{TRADEOFF} unreadable or without its two_level_best row ({e!r}): re-run sim/pcs_tradeoff.py")


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
T_AMB_MIN = -30.0                                    # lowest operating ambient (AC-02: -30..+60 C)
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


def overload_tj(cand, vdc, ang, t_in=T_IN, v_ll=V_LL, i120=I_2MIN, i200=I_200MS):
    """(Tj after 120 % for 2 min, Tj after 1.2 x I_max for 200 ms), both from the 110 % steady state: heatsink first-order RC
    (tau = R_sa x C_section), devices at their steady rise for the 2-min step and on the two-time-constant transient for 200 ms.
    Losses are taken from the steady evaluation at the overload current (hotter -> conservative).  Returns per-position dicts."""
    e110 = evaluate(cand, vdc, v_ll, I_CONT, ang, t_in)
    e120 = evaluate(cand, vdc, v_ll, i120, ang, t_in)
    e200 = evaluate(cand, vdc, v_ll, i200, ang, t_in)
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
    cond_only = evaluate(dict(D, l1=D["l1"] * FSW_CHOICE), 750.0, V_LL, I_RATED, 0.0, T_IN, fsw=1.0)   # f_sw -> 1 Hz, L1 x f_sw: same ripple
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


# ---- current sharing of the six paralleled devices (PCM-06) and the on-state voltage the DESAT setting must clear (PCM-07)
RDS_POP = {"data sheet: one 40 mOhm (typ) among five at 52 mOhm (max)": 52.0 / 40.0,
           "acceptance rule: one at -5 % among five at +5 %": 1.05 / 0.95}
SHARE_RULE = ("R_DS(on) of the six devices of one switch within +/-5 % of their mean at V_GS 18 V, I_D 38 A pulsed (< 200 us), "
              "T_J 25 C (incoming measurement or the maker's bin; the data sheet allows 40 typ / 52 max mOhm) - bounds the conduction "
              f"share at k <= {6 * 1.05 / 0.95 / (1.05 / 0.95 + 5):.3f}; V_GS(th) within +/-0.25 V (V_DS = V_GS, I_D 12 mA, 25 C; "
              "data-sheet spread 2.5-4.0 V) for the switching share; one lot per switch")
# Sichain SG2M040170HJ p6 Fig. 5 (R_DS(on) vs I_D at V_GS 18 V), read by a pixel overlay (+-1 mOhm): (A, mOhm) at 25 and 175 C
FIG5 = {25.0: [(20, 36.1), (38, 38.3), (50, 39.8), (60, 41.4), (70, 43.1), (80, 45.1), (90, 47.5), (100, 49.8), (110, 54.0)],
        175.0: [(20, 77.9), (38, 82.4), (50, 86.6), (60, 91.0), (70, 96.8), (80, 103.9)]}


def sharing(D, rb, t_ins=(T_IN, T_IN_HOT)):
    """hottest device of one switch for an R_DS(on) population - one device at the low end among five at the high end - with every
    device's R_DS(on) at its OWN junction temperature (p6 Fig. 4: the positive coefficient lowers the hot device's share), iterated to a
    fixed point; conduction AND switching scaled with the share, as K_SHARE does (the hot device at the typical 40 mOhm, the others at
    40 x the ratio).  Tiers: 100 % and 110 % steady at the envelope's worst corner, 120 % for 2 min and 1.2 x 216 A for 200 ms at the
    overload table's worst corner, from the 110 % state with that state's (larger) share.  Overload tiers that miss their limit are
    scanned down in 2.5 % steps (the firmware derating that population would need)."""
    part = D["devs"]["TH"][0]
    d0 = pv.MOSFETS[part]
    norm = lambda t: pv.rds(d0, t) / d0["rds25"]                                     # noqa: E731
    at = rb["envelope_45C"]["at"]
    ov = max(rb["overload"], key=lambda o: o["tj_200ms_C"])
    out = {}
    for pop, ratio in RDS_POP.items():
        pv.MOSFETS[part + "@high"] = dict(d0, rds25=d0["rds25"] * ratio)
        Dh = dict(D, devs={p: (part + "@high", n) for p, (x, n) in D["devs"].items()})

        def pair(fn, k):
            ks = dict(K_SHARE)
            try:
                K_SHARE["sic"] = k
                th = fn(D)
                K_SHARE["sic"] = (6 - k) / 5
                return th, fn(Dh)
            finally:
                K_SHARE.update(ks)

        def solve(fn):
            k = 6 * ratio / (ratio + 5)                    # equal temperatures: conductances 1 / R
            for _ in range(40):
                th, tc = pair(fn, k)
                gh, gc = 1 / norm(th), 1 / (ratio * norm(tc))
                k_new = 6 * gh / (gh + 5 * gc)
                if abs(k_new - k) < 1e-4:
                    break
                k = 0.5 * (k + k_new)
            return k, th, tc
        rows = {}
        for t_in in t_ins:
            for name, x in (("100 %", 1.0), ("110 %", I_CONT / I_RATED)):
                k, th, tc = solve(lambda DD: evaluate(DD, at["vdc"], at["vll"], I_RATED * x, at["ang"], t_in)["tj_max"])
                rows[f"{t_in:.0f}C {name}"] = {"k": k, "tj_hot_C": th, "tj_others_C": tc, "limit_C": TJ_LIM["sic"][0], "I_A": I_RATED * x,
                                               "I_nominal_A": I_RATED * x}
            k, _, _ = solve(lambda DD: max(max(v) for v in overload_tj(DD, ov["vdc"], ov["ang"], t_in)["e110"]["tj"].values()))
            for name, key, lim, i_nom in (("120 % 2 min", "tj_2min", TJ_LIM["sic"][1], I_2MIN), ("200 ms", "tj_200ms", tj_abs_max(part), I_200MS)):
                i, th_nom = i_nom, None
                while True:
                    kw = {"i120": i} if key == "tj_2min" else {"i200": i}
                    th, tc = pair(lambda DD: max(max(v) for v in overload_tj(DD, ov["vdc"], ov["ang"], t_in, **kw)[key].values()), k)
                    th_nom = th if th_nom is None else th_nom
                    if th <= lim or i <= 0.5 * i_nom:
                        break
                    i -= 0.025 * i_nom
                rows[f"{t_in:.0f}C {name}"] = {"k": k, "tj_hot_C": th, "tj_others_C": tc, "limit_C": lim, "I_A": i, "I_nominal_A": i_nom,
                                               "tj_hot_at_nominal_C": th_nom}
        del pv.MOSFETS[part + "@high"]
        out[pop] = {"r_ratio": ratio, "k_equal_temperatures": 6 * ratio / (ratio + 5), "tiers": rows,
                    "ok_45C": all(v["tj_hot_C"] <= v["limit_C"] and v["I_A"] >= v["I_nominal_A"] for k_, v in rows.items()
                                  if k_.startswith("45C")),
                    "ok_60C_without_derating": all(v["tj_hot_C"] <= v["limit_C"] and v["I_A"] >= v["I_nominal_A"]
                                                   for k_, v in rows.items() if k_.startswith("60C"))}
    rule = out[list(RDS_POP)[1]]
    res = {"populations": out, "acceptance_rule": SHARE_RULE, "model_k": K_SHARE["sic"],
           "bounded_by_rule": rule["ok_45C"] and max(v["k"] for v in rule["tiers"].values()) <= K_SHARE["sic"],
           "derated_60C_A": {k_[4:]: v["I_A"] for k_, v in rule["tiers"].items() if k_.startswith("60C") and "%" not in k_[-3:]},
           "corners": {"steady": {k: at[k] for k in ("vdc", "vll", "ang")}, "overload": {"vdc": ov["vdc"], "ang": ov["ang"]}}}
    SPEC["thermal_and_losses"]["sharing_population"] = res
    return res


def report_sharing(s):
    wr("\n### Current sharing of six paralleled devices, by R_DS(on) population (PCM-06)\n")
    wr(f"**Calculated** with the loss and thermal model above: the hottest device of a switch is the one with the lowest R_DS(on); its "
       f"share k follows the six conductances with every R_DS(on) at its own junction temperature (iterated), and scales its conduction "
       f"and switching loss as K_SHARE does; steady tiers at V_dc {s['corners']['steady']['vdc']:.0f} V / {s['corners']['steady']['vll']:.0f} V "
       f"AC / {s['corners']['steady']['ang']:.0f} deg, overload tiers at {s['corners']['overload']['vdc']:.0f} V / "
       f"{s['corners']['overload']['ang']:.0f} deg from the 110 % state.  The design model carries k = {s['model_k']:.2f}.\n")
    wr("| population | tier | current (A) | k | hottest Tj (C) | other five (C) | limit (C) |")
    wr("|---|---|---|---|---|---|---|")
    for pop, x in s["populations"].items():
        for t, v in x["tiers"].items():
            flag = "" if v["tj_hot_C"] <= v["limit_C"] else " **over**"
            wr(f"| {pop} | {t} | {v['I_A']:.0f}{'' if v['I_A'] >= v['I_nominal_A'] else ' (derated from %.0f)' % v['I_nominal_A']} | "
               f"{v['k']:.3f} | {v['tj_hot_C']:.0f}{flag} | {v['tj_others_C']:.0f} | {v['limit_C']:.0f} |")
    ds, rule = s["populations"][list(RDS_POP)[0]], s["populations"][list(RDS_POP)[1]]
    dt = ds["tiers"]
    der = "; ".join(f"{k} {v['I_A']:.0f} A" for k, v in dt.items() if v["I_A"] < v["I_nominal_A"])
    wr(f"\nThe data sheet alone (one lot, V_GS(th) binning) does not bound the conduction share: the worst population takes k "
       f"{max(v['k'] for v in ds['tiers'].values()):.2f} ({ds['k_equal_temperatures']:.3f} at equal temperatures; the temperature coefficient "
       f"pulls it back only slightly because the hot device runs a few K above the others).  The trade study's 1.30 factor (D-057, "
       f"sim/pcs_tradeoff.py: device count and 60 C rating at 100 % with k 1.30) covers that k where it is applied - 60 C, 100 %: "
       f"{dt['60C 100 %']['tj_hot_C']:.0f} C <= 150 C - but the overload tiers are sized with the model's k {s['model_k']:.2f}: at 45 C "
       f"inlet the 200 ms tier reaches {dt['45C 200 ms']['tj_hot_at_nominal_C']:.0f} C "
       f"against 175 C, and at 60 C the 110 % tier {dt['60C 110 %']['tj_hot_C']:.0f} C against 150 C.  Two ways out: "
       f"(a, taken) **acceptance rule (BOM line of the SG2M040170HJ):** {s['acceptance_rule']}.  With it k stays <= "
       f"{max(v['k'] for v in rule['tiers'].values()):.3f} <= the model's {s['model_k']:.2f}: every 45 C tier holds at its nominal current; "
       f"at 60 C inlet the firmware derates the overload tiers to " + ", ".join(f"{k} {a:.0f} A" for k, a in s["derated_60C_A"].items())
       + f" (continuous: the derating map above).  (b) Without the rule the firmware would have to derate this population: {der}, and "
       f"hold 60 C inlet to 100 % continuous (the trade study's k 1.30 rating) - cheaper in production, but 200 ms capability lost at "
       f"45 C; not taken.")


def desat_onstate(D, rb):
    """V_DS of a switch at the 200 ms overload peak, for the DESAT coordination on PCS-PWR (PCM-07): all six devices taken at the hottest
    device's junction (conservative), R_DS(on) = Table 4 typical (40 mOhm at 25 C, 88 at 175 C, 38 A) along the p6 Fig. 4 / 6 temperature
    curve, times the Fig. 5 current factor at this current; the 175 C case and a switch of data-sheet-maximum parts (x 52/40) alongside"""
    d = pv.MOSFETS[D["devs"]["TH"][0]]
    n = D["devs"]["TH"][1]
    i_pk = SPEC["inductors"]["L1"]["current"]["peak_A"]["200 ms"] / n
    tj = max(o["tj_200ms_C"] for o in rb["overload"])
    k175 = 88e-3 / pv.rds(d, 175.0)                                            # Table 4 against the averaged curve at 175 C
    fi = {t: float(np.interp(i_pk, [p[0] for p in c], [p[1] for p in c])) / float(np.interp(38.0, [p[0] for p in c], [p[1] for p in c]))
          for t, c in FIG5.items()}

    def r(t, worst=False):
        a = (t - 25.0) / 150.0
        return pv.rds(d, t) * (1 + (k175 - 1) * a) * (fi[25.0] + (fi[175.0] - fi[25.0]) * a) * (d["rds25_max"] / d["rds25"] if worst else 1.0)
    tj_cold = tj - (T_IN - T_AMB_MIN)                  # the same overload from a -30 C inlet: the thermal rise is unchanged (approx.)
    res = {"I_pk_per_device_A": i_pk, "tj_200ms_hottest_C": tj, "R_typ_at_tj_mohm": r(tj) * 1e3, "inlet_C": T_IN,
           "V_DS_typ_at_tj_V": i_pk * r(tj), "V_DS_typ_175C_V": i_pk * r(175.0),
           "V_DS_max_rds_at_tj_V": i_pk * r(tj, True), "V_DS_max_rds_175C_V": i_pk * r(175.0, True),
           "tj_cold_C": tj_cold, "inlet_cold_C": T_AMB_MIN, "V_DS_typ_cold_V": i_pk * r(tj_cold),
           "V_DS_max_rds_cold_V": i_pk * r(tj_cold, True),
           "current_factor_Fig5": fi, "R_25C_vs_I_mohm": FIG5[25.0], "I_DM_A": d["idm"],
           "basis": ("1.2 x 216 A peak + half the 950 V ripple per device; all six at the hottest device's 200 ms junction (k 1.10 model); "
                     "R_DS(on) Table 4 p4 typical (40 / 88 mOhm at 25 / 175 C, 38 A) along the Fig. 4/6 curve, x the p6 Fig. 5 current "
                     "factor; 'max' = the 52 mOhm data-sheet maximum at 25 C scaled the same way (no hot maximum is published)")}
    SPEC["commutation_and_gate_drive"]["desat"]["onstate_200ms_overload"] = res
    SPEC["commutation_and_gate_drive"]["desat"]["rds_acceptance"] = rds_acceptance(D, rb, d, n, k175)
    return res


def rds_acceptance(D, rb, d, n, k175):
    """review R2-07 (2026-10-07): the +/-5 % matching rule bounds the share, not the level - a uniformly high bank passes it.  (a) an
    ABSOLUTE incoming limit on R_DS(on) at 25 C: the bank's own 200 ms junction (thermal model, the k 1.10 hottest device, all six
    there - the convention of desat_onstate) and the data sheet's typical temperature coefficient (Table 4: 40 / 88 mOhm at 25 / 175 C;
    no hot maximum is published - a part at the limit is ASSUMED to follow it) keep the 200 ms tier's on-state below the DESAT minimum
    with the stated margin; (b) for a population that is not screened (the 52 mOhm data-sheet maximum) the overload tier currents the
    firmware must derate to (200 ms and 2 min: DESAT and junction), and its 110 % junctions.  The DESAT minimum and margin are the
    PCS-PWR design check's (gen/gdrv.py DESAT_PCS, read only)."""
    t = pwr_check_text()
    m_tr = re.search(r"trips at V_DS ([\d.]+)-([\d.]+) V .*?, ([\d.]+) V lowest with the string at >= ([\d.]+) C", t)
    m_mg = re.search(r"-> margin [\d.]+ \(>= ([\d.]+) stated\)", t)
    if not (m_tr and m_mg):
        raise SystemExit("PCS-PWR design check: the DESAT trip / margin statements changed - rebuild gen/pcs_power.py")
    v_trip, margin = float(m_tr.group(3)), float(m_mg.group(1))
    v_max = v_trip / margin
    part = D["devs"]["TH"][0]
    ov = max(rb["overload"], key=lambda o: o["tj_200ms_C"])
    half_rip = SPEC["inductors"]["L1"]["current"]["peak_A"]["200 ms"] - I_200MS * math.sqrt(2)

    def r_at(tj, ip):                                       # R_DS(on) typical along Fig. 4 / Table 4, x the Fig. 5 factor at ip
        f = {t_: float(np.interp(ip, [p[0] for p in c], [p[1] for p in c])) / float(np.interp(38.0, [p[0] for p in c], [p[1] for p in c]))
             for t_, c in FIG5.items()}
        a = (tj - 25.0) / 150.0
        return pv.rds(d, tj) * (1 + (k175 - 1) * a) * (f[25.0] + (f[175.0] - f[25.0]) * a)

    def with_bank(x, fn):                                    # fn(design) with all six devices at x times the typical R_DS(on)
        pv.MOSFETS[part + "@bank"] = dict(d, rds25=d["rds25"] * x)
        try:
            return fn(dict(D, devs={p: (part + "@bank", nn) for p, (q, nn) in D["devs"].items()}))
        finally:
            del pv.MOSFETS[part + "@bank"]

    def onstate(x, i=I_200MS, tier="tj_200ms"):
        """V_DS and junction of such a switch at the overload tier current i (tier 'tj_200ms' or 'tj_2min')"""
        kw = {"i200": i} if tier == "tj_200ms" else {"i120": i}
        tj = with_bank(x, lambda Dx: max(max(v) for v in overload_tj(Dx, ov["vdc"], ov["ang"], **kw)[tier].values()))
        ip = (i * math.sqrt(2) + half_rip) / n
        return ip * r_at(tj, ip) * x, tj

    def derate(x, i_nom, tier, tj_max):                     # largest tier current with V_DS <= v_max and Tj <= its limit
        lo, hi = 0.5 * i_nom, i_nom
        if all(a <= b for a, b in zip(onstate(x, i_nom, tier), (v_max, tj_max))):
            return i_nom
        for _ in range(14):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if all(a <= b for a, b in zip(onstate(x, mid, tier), (v_max, tj_max))) else (lo, mid)
        return lo
    x = 1.0
    for _ in range(12):                                     # fixed point: the bank's own heating included
        v, tj = onstate(x)
        if abs(v / v_max - 1) < 1e-4:
            break
        x *= v_max / v
    r_lim = math.floor(d["rds25"] * x * 2e3) / 2e3          # down to 0.5 mOhm
    v_lim_, tj_lim = onstate(r_lim / d["rds25"])
    x_ds = d["rds25_max"] / d["rds25"]
    v_ds, tj_ds = onstate(x_ds)
    i_2m = derate(x_ds, I_2MIN, "tj_2min", TJ_LIM["sic"][1])   # the overload tiers an unscreened (all-maximum) bank holds
    i_200 = derate(x_ds, I_200MS, "tj_200ms", tj_abs_max(part))
    lo = min(i_200, i_2m)                                     # a 200 ms tier below the 2-min tier is the 2-min tier
    v_der, tj_der = onstate(x_ds, lo)
    v_2m, tj_2m = onstate(x_ds, I_2MIN, "tj_2min")
    v_2d, tj_2d = onstate(x_ds, i_2m, "tj_2min")
    at = rb["envelope_45C"]["at"]
    tj_cont = {t_in: with_bank(x_ds, lambda Dx: evaluate(Dx, at["vdc"], at["vll"], I_CONT, at["ang"], t_in)["tj_max"]) for t_in in (T_IN, T_IN_HOT)}
    return {"DESAT_lowest_trip_V_string_ge_45C": v_trip, "margin_stated": margin, "V_DS_max_V": v_max,
            "corner": {"vdc": ov["vdc"], "ang": ov["ang"], "inlet_C": T_IN},
            "absolute_limit": {"R_DS_on_25C_max_mohm": r_lim * 1e3, "x_typ": r_lim / d["rds25"], "V_DS_at_limit_V": v_lim_,
                               "tj_at_limit_C": tj_lim,
                               "rule": ("R_DS(on) <= %.1f mOhm for each device at V_GS 18 V, I_D 38 A pulsed (< 200 us), T_J 25 C - the same "
                                        "incoming measurement as the +/-5 %% matching rule (no extra test, no extra hardware); data sheet "
                                        "40 typ / 52 max: parts between %.1f and 52 mOhm are not scrapped - a switch with any of them makes "
                                        "the module an 'unscreened' build that runs the derated firmware tiers (200 ms %.0f A, 2 min %.0f A; "
                                        "parameter by lot)" % (r_lim * 1e3, r_lim * 1e3, lo, i_2m))},
            "unscreened_derating": {"population": "six devices at the 52 mOhm data-sheet maximum", "V_DS_at_full_tier_V": v_ds,
                                    "tj_at_full_tier_C": tj_ds, "I_200ms_A": lo, "I_200ms_DESAT_alone_A": i_200, "I_200ms_nominal_A": I_200MS,
                                    "V_DS_V": v_der, "tj_C": tj_der, "V_DS_2min_tier_V": v_2m, "tj_2min_tier_C": tj_2m, "I_2min_A": i_2m,
                                    "V_DS_2min_derated_V": v_2d, "tj_2min_derated_C": tj_2d,
                                    "I_2min_nominal_A": I_2MIN, "tj_110pct_C": {f"{t:.0f}C inlet": v for t, v in tj_cont.items()},
                                    "note": ("firmware parameters: 2-min tier %.0f A (at %.0f A it would reach %.2f V / %.0f C against %.2f V / "
                                             "%.0f C; derated %.2f V / %.0f C), 200 ms tier %.0f A (DESAT alone would allow %.0f A; a 200 ms "
                                             "tier below the 2-min one is the 2-min one); the 110 %% continuous tier reaches %.0f / %.0f C at "
                                             "45 / 60 C inlet against %.0f C - the thermal model is on the typical R_DS(on), so an "
                                             "all-maximum switch loses margin in every tier, not only the 200 ms one" % (
                                                 i_2m, I_2MIN, v_2m, tj_2m, v_max, TJ_LIM["sic"][1], v_2d, tj_2d, lo, i_200, tj_cont[T_IN],
                                                 tj_cont[T_IN_HOT], TJ_LIM["sic"][0]))},
            "decision": ("closure (zero cost): the absolute limit is the acceptance rule - it costs no hardware and no test time (the "
                         "devices are measured for the matching rule anyway) - and the derating is its per-lot fallback, so no part is "
                         "scrapped: a switch with a device above the limit runs the derated 200 ms tier (firmware parameter by lot, the "
                         "module declares it).  The limit comes out at %.1f mOhm against the data sheet's %.0f typ / %.0f max: a population "
                         "centred on the typical value would put about half the switches in the derated grade - the maker publishes no "
                         "distribution, so the R_DS(on) distribution or a <= %.1f mOhm bin is an RFQ item to Sichain; derating every "
                         "module instead (no screen) would give up the 200 ms tier and part of the 2-min tier (the off-grid "
                         "load-acceptance declarations) and the continuous margin at 60 C inlet - the dearer closure" % (
                             r_lim * 1e3, d["rds25"] * 1e3, d["rds25_max"] * 1e3, r_lim * 1e3)),
            "pulse_rating": ("I_D(pulse) %.0f A per device, t_P 100 us, limited by T_J,max (SG2M040170HJ Table 2 p3, Fig. 22 SOA at T_C 25 C, "
                             "single pulse): a thermal on-state pulse rating - it bounds the conduction pulse of a fault under load up to "
                             "the DESAT detection (<= %.0f A per device, PCS-PWR check), it is NOT a short-circuit (desaturated) or turn-off "
                             "rating" % (d["idm"], SPEC["protection_chain"]["backup_as_drawn"]["desat_backstop_A_per_device"])),
            "sc_survival": ("REQUIRES HARDWARE TEST - release gate (risk C2, DAB rule DR-05): no short-circuit withstand time or energy is "
                            "published by Sichain; survival of the drawn DESAT + booster chain (t_eq, E per device printed by the PCS-PWR "
                            "check) must be shown by the maker's confirmation or a destructive short-circuit test at 1050 V / 150 C start / "
                            "+18 V on the six-device switch before release - never closed on paper"),
            "basis": ("calculated: the bank's 200 ms junction from the loss / thermal model at the worst overload corner (%.0f V, %.0f deg, "
                      "%.0f C inlet), every device at the k 1.10 model's hottest junction (as desat_onstate), R_DS(on) along the "
                      "Table 4 / Fig. 4 typical temperature curve x the Fig. 5 current factor; DESAT minimum and margin from the PCS-PWR "
                      "check (gen/gdrv.py DESAT_PCS)" % (ov["vdc"], ov["ang"], T_IN))}


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
       f"(conduction and switching) - it requires the R_DS(on) / V_GS(th) acceptance rule of the population analysis below (one lot "
       f"alone does not bound it), Kelvin-source drive with a "
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
# phase-current sensor, frozen by the orchestrator for PCM-20: Sinomags STK-250HO/4 (family STK-HO/4; alternate STK-250HO/2 in the same
# housing; Western fallback LEM HO 250-S-0100 with the same 15 x 8 mm aperture, 1000 V basic / 600 V reinforced, UL 508).  Data sheet
# docs/datasheets/sensing/Sinomags-STK-HO-4.pdf Ver 1.0: printed p13 (PDF p14) electrical data at Vcc 5 V / 25 C, printed p2 isolation,
# printed p19 connection.  pcs_spec 'phase_current_sensor' (read by gen/pcs_power.py and sim/pcs_control.py)
PH_SENSOR = {"mpn": "STK-250HO/4", "mfr": "Sinomags Technology", "datasheet": "docs/datasheets/sensing/Sinomags-STK-HO-4.pdf",
             "principle": "open-loop Hall, aperture 15 x 8 mm busbar, 5-pin 2 mm connector",
             "I_PN_A": 250.0, "linear_range_A": 625.0, "gain_mV_per_A": 3.2, "vref_V": 2.5, "vref_range_V": [2.48, 2.52],
             "offset_mV": 8.0, "offset_drift_mV": 10.0, "gain_error": 0.005, "gain_drift": 0.01, "linearity_of_I_PM": 0.005,
             "accuracy_25C_of_I_PN": 0.01, "accuracy_m40_105C_of_I_PN": 0.03, "bandwidth_Hz": 200e3, "step_response_s": 2.0e-6,
             "step_response_typ_s": 1.5e-6, "supply_V": [4.75, 5.25], "supply_mA": [6.0, 10.0], "r_out_ohm": [10.0, 30.0],
             "r_ref_ohm": [5.0, 25.0], "noise_mVpp": 4.4, "ocd_x_I_PN": 2.93, "ocd_tolerance": 0.10,
             "isolation": {"test_kV_rms_1min": 4.3, "impulse_kV": 8.0, "clearance_creepage_mm": 8.0, "CTI": 600,
                           "working_voltage": "not stated by the maker"},
             "qualification": "none stated (SRC-2 gap)", "lcsc": "not listed", "pages": "p13 electrical, p2 isolation, p19 pins"}
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
       f"covers SCR 5 to a stiff grid ({BW_LOOP/1e3:.2f} kHz is the lower edge of the usable crossover band, not a ceiling: the control "
       f"study, sim/out/pcs_control, finds 1.0-3.25 kHz).  The passive branch alone lowers "
       f"the resonance peak from {r['peak_gain_undamped_dB']:.0f} to {r['peak_gain_passive_branch_dB']:.0f} dB (relative to the L1 admittance at "
       f"1 kHz); the control study finds it required - active damping from the C_f voltage fails at the stiff grid.")
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


# ---- discharge (PCM-03) and the upper DC-link half (PCM-04): every capacitor that stays charged after the DC contactor and both AC
# contactor sets opened, and the mechanisms that can over-voltage the upper half while the hardware trips (VB, VA) see nothing
LABEL_MIN, V_SAFE = 15.0, 60.0   # enclosure label 'wait 15 min' (PCS-PWR): <= 60 V between any two conductors inside (IEC 62477-1 style)
BL_N = 4                         # bleeder elements per half (1210 HV thick film, 200 V working; drawn on PCS-PWR)
R_DIV = 6e6 + 4.99e3             # each HV divider on PCS-PWR (VB on DC+, VA on M, VC on every C_f node): 6 x 1 M + 4.99 k to VMID (~DC-)
C_TOL, R_TOL = 0.10, 0.01        # worst case: every capacitor +10 % (C3D tolerance code K, C_f / C_d RFQ 10 %, FCSA3DS225 10 % ASSUMED),
#                                  bleeder elements +1 %; the dividers are 0.1 % parts (nominal)
AUX75 = os.path.join(HERE, "out", "aux_hv_design", "aux75_spec.json")   # the 75 W aux block PCS-PWR draws (its HV input bulk)
E96 = [round(10 ** (i / 96.0), 2) for i in range(96)]
ADC_ACC = 0.006                  # VA / VB channel error after calibration, ASSUMED (PCS-CTL design check: 0.57 %)
T_OUTER = 2.0 / F_S              # s, outer control loop at f_sw = 31.25 us (PCS-CTL sampling plan: control_spec outer_loop_kHz 32,
T_CONV = 10e-6                   # s  VA / VB converted every <= 10 us; gen/pcs_ctrl.py checks both against the board it builds)


def discharge(r_half, c_u, c_l, c_bus, c_ph, v0, v_ph0, t_end=1800.0, dt=0.5, n_div=None):
    """passive discharge with the DC contactor and both AC contactor sets open, gates off (CALCULATED: backward Euler, ideal clamp
    diodes).  Nodes vs DC-: DC+, M and one C_f node per phase.  c_u (DC+ to M), c_l (M to DC-), c_bus across the bus (leg films and the
    aux block's input bulk - its BYG10Y conducts while the bulk decays faster than the bus on its own strings, whose current is not
    credited), c_ph per phase from its C_f node to M (C_f + C_d; R_d is a short at these time scales).  Resistors: the bleeder r_half
    per half and the 6 M dividers (VB on DC+, VA on M, VC on each C_f node) to VMID ~ DC-.  Each C_f node is tied by L1 (a short) to its
    switch node, which the SiC body diodes hold inside [DC-, DC+] (forward drop neglected).  Returns (t, the largest voltage between
    any two of these conductors); the AC terminals are behind the open contactors.  n_div: only the first n_div C_f nodes carry a
    VC divider (the four-wire N filter node has none; default all)."""
    k = len(v_ph0)
    n_div = k if n_div is None else n_div
    C, G = np.zeros((2 + k, 2 + k)), np.zeros((2 + k, 2 + k))

    def br(m, a, b, y):
        m[a, a] += y
        if b is not None:
            m[b, b] += y
            m[a, b] -= y
            m[b, a] -= y
    br(C, 0, 1, c_u)
    br(C, 1, None, c_l)
    br(C, 0, None, c_bus)
    for a, b, r in ((0, 1, r_half), (1, None, r_half), (0, None, R_DIV), (1, None, R_DIV)):
        br(G, a, b, 1.0 / r)
    for j in range(k):
        br(C, 2 + j, 1, c_ph)
        if j < n_div:
            br(G, 2 + j, None, 1.0 / R_DIV)
    x = np.array([v0, v0 / 2] + [min(max(v0 / 2 + v, 0.0), v0) for v in v_ph0])
    hi, lo = x[2:] >= x[0], x[2:] <= 0.0
    ts, vm, t = [0.0], [x.max() - min(x.min(), 0.0)], 0.0
    while t < t_end:
        for _ in range(10):                      # clamp-diode states: re-solve until consistent
            A = C / dt + G
            for j in range(k):
                if hi[j]:
                    br(A, 2 + j, 0, 1e3)
                if lo[j]:
                    br(A, 2 + j, None, 1e3)
            y = np.linalg.solve(A, C @ x / dt)
            h2, l2 = y[2:] > y[0], y[2:] < 0.0
            if (h2 == hi).all() and (l2 == lo).all():
                break
            hi, lo = h2, l2
        x, t = y, t + dt
        ts.append(t)
        vm.append(x.max() - min(x.min(), 0.0))
    return np.array(ts), np.array(vm)


def t_below(ts, vm, v=V_SAFE):
    """first time after which vm stays <= v (inf if it never does within the run)"""
    above = np.nonzero(vm > v)[0]
    if not len(above):
        return 0.0
    return float(ts[above[-1] + 1]) if above[-1] + 1 < len(ts) else math.inf


def bleeder(c_half, c_loc, c_ph, n_ph=3, n_extra=0):
    """the largest E96 bleeder element (BL_N per half) whose worst case - every capacitor +10 %, elements +1 %, dividers credited, from
    the 1050 V trip, C_f charged to the 460 V AC peak at the worst phase angle - is below 60 V within the label time (PCM-03).
    n_extra: C_f nodes held at the midpoint when the converter stops and without a divider (the four-wire N filter node C_fN)"""
    cb = json.load(open(AUX75))["values"]["C_BULK"]                     # fail closed: the aux block's spec must exist
    c_aux = cb["n"] * float(cb["value"].split("u")[0]) * 1e-6
    v_pk = V_LL * (1 + V_TOL) * math.sqrt(2.0 / 3.0)
    sets = [[v_pk * math.sin(math.radians(a) - j * 2 * math.pi / 3) for j in range(n_ph)] + [0.0] * n_extra for a in range(0, 60, 10)]

    def t60(r_el, kc=1.0 + C_TOL, kr=1.0 + R_TOL, cph=c_ph, aux=c_aux):
        return max(t_below(*discharge(BL_N * r_el * kr, c_half * kc, c_half * kc, (c_loc + aux) * kc, cph * kc, V_OV_TRIP,
                                      s if cph else s[:n_ph], n_div=n_ph)) for s in sets)     # no C_f: the extra nodes vanish
    pick = None
    for r_el in sorted({m * 1e4 for m in E96} | {m * 1e5 for m in E96 if m <= 1.1}, reverse=True):
        if r_el <= 110e3 and t60(r_el) <= LABEL_MIN * 60:
            pick = r_el
            break
    assert pick, "no E96 bleeder element meets the label"
    r_half = BL_N * pick
    old = 110e3                                                          # rev A0 as drawn before PCM-03: 4 x 110 k per half
    return {"R_elem_ohm": pick, "n_elem": BL_N, "R_per_half_ohm": r_half, "label_min": LABEL_MIN,
            "t60_worst_min": t60(pick) / 60, "t60_nominal_min": t60(pick, 1.0, 1.0) / 60,
            "t60_worst_bank_and_films_only_min": t60(pick, cph=0.0, aux=0.0) / 60,
            "t60_worst_rev_A0_440k_min": t60(old) / 60, "C_aux_bulk_uF": c_aux * 1e6, "C_f_plus_C_d_per_phase_uF": c_ph * 1e6,
            "P_W_950V": 950.0 ** 2 / (2 * r_half), "P_W_950V_rev_A0": 950.0 ** 2 / (2 * BL_N * old),
            "P_W_per_half_950V": 475.0 ** 2 / r_half, "V_elem_at_half_trip_V": 560.0 / BL_N,
            "P_elem_at_half_trip_W": (560.0 / BL_N) ** 2 / pick,
            "basis": ("worst case: every capacitor +10 %, bleeder +1 %, the 6 M dividers credited (VB, VA, VC x 3), from the 1050 V trip "
                      "with the C_f charged to the 460 V AC peak at the worst of 6 phase angles; aux input bulk on the bus (its strings not "
                      "credited); the C_f nodes held inside the bus by the body diodes; criterion: the largest voltage between any two "
                      "conductors <= 60 V")}


def upper_half(c_half, r_half, f, life, cf_lf_a, mid_pp, cf):
    """mechanisms that put the upper half (DC+ to M) above its rating while the hardware trips see VB < 1050 V and VA < 560 V (PCM-04);
    U_N at the hot spot from the life block, C3D p12 over-voltage allowances (1.1 U_N 30 % of on-load time, 1.15 U_N 30 min/day, 1.2 U_N
    5 min/day, 1.3 U_N 1 min/day, 1.5 U_N 100 ms 1000 times; withstand test 1.5 U_N 10 s).  CALCULATED"""
    un = lambda th: f["un70"] if th <= 70.0 else f["un70"] + (f["un85"] - f["un70"]) * (min(th, 85.0) - 70.0) / 15.0   # noqa: E731
    t45, t60 = life["45C_950V"]["T_hs_C"], life["60C_950V"]["T_hs_C"]
    tau_bal = r_half * c_half                              # differential mode of two equal halves: R (C_U + C_L) / 2
    r_lo = 1 / (1 / r_half + 1 / R_DIV)                    # M to DC-: lower bleeder || VA divider
    r_ins = 10000.0 / c_half                               # C3D p3: IR x C >= 10,000 s (20 C, 100 V) - the cold, high value
    v_inf = 950.0 * r_ins / (r_ins + r_lo)                 # upper bleeder open: the upper half's own insulation against M's paths
    tau_open = (1 / (1 / r_lo + 1 / r_ins)) * 2 * c_half
    t_to = lambda v: -tau_open * math.log((v_inf - v) / (v_inf - 475.0)) if v_inf > v else math.inf   # noqa: E731
    imb = math.ceil((C_TOL / 2 + 2 * ADC_ACC) * 100) / 100   # slow imbalance limit: clears the worst static split (C_TOL / 2) + 2 channels
    w = 2 * math.pi * 3 * F_GRID
    dv150 = cf_lf_a * math.sqrt(2) / (w * 2 * c_half)      # peak half-voltage swing of the 150 Hz C_f-star current
    dv_step = 3 * cf / (2 * c_half + 3 * cf) * V_OV_TRIP / 2   # a zero-sequence step of V_dc/2 shifts M by this much, then decays
    lat = 2 * T_OUTER + T_CONV                             # two outer-loop results + one conversion
    t_open = lat + 10e-3 + 10e-3                           # firmware detection + HFE82V release <= 10 ms (p1) + 10 ms margin (ASSUMED)
    rows = [
        dict(mech="capacitance tolerance split at precharge (C3D code K +/-10 %: whole upper half -10 %, lower +10 %)",
             v_upper=V_OV_TRIP * (1 + C_TOL) / 2, va=V_OV_TRIP * (1 - C_TOL) / 2,
             time=f"static after the precharge, bled out with tau {tau_bal / 60:.1f} min",
             verdict=(f"inside U_N(70 C); |VA - VB/2| = {C_TOL / 2:.2f} VB, below the {imb:.2f} VB imbalance limit; VB - VA reaches the "
                      f"540 V limit only above {540.0 / ((1 + C_TOL) / 2):.0f} V, outside the 950 V operating range")),
        dict(mech="one lower-half capacitor shorted (the lower half collapses, the battery holds the upper half through the closed DC "
                  "contactor)", v_upper=950.0, va=0.0,
             time=f"until the DC contactor opens: firmware detection <= {lat * 1e6:.0f} us + release <= 10 ms + margin ~ {t_open * 1e3:.0f} ms",
             verdict=(f"{950.0 / f['un70']:.2f} x U_N(70 C) at 950 V ({V_OV_TRIP / f['un70']:.2f} at the 1050 V trip), plus the ring of the "
                      "upper half's recharge through the battery inductance (not modelled): above every C3D allowance (1.5 U_N for 100 ms) "
                      "after a first failure, for the ~20 ms until the DC contactor opens - only opening it ends the stress, so a discrete "
                      "comparator would not shorten it")),
        dict(mech="upper bleeder string open (only the films' insulation, >= 10,000 s / C, across the upper half)", v_upper=v_inf,
             va=950.0 - v_inf, time=(f"drifts with tau {tau_open / 60:.1f} min from 475 V at 950 V: the {imb:.2f} VB imbalance limit after "
                                     f"{t_to(475.0 + imb * 950.0) / 60:.1f} min, U_N(70 C) after {t_to(f['un70']) / 60:.1f} min"),
             verdict="minutes - firmware imbalance limit (controlled stop) long before U_N"),
        dict(mech=f"C_f-star current into M (150 Hz zero sequence <= {cf_lf_a:.0f} A rms + carrier band, step d)",
             v_upper=V_OV_TRIP / 2 + mid_pp / 2, va=V_OV_TRIP / 2 - mid_pp / 2,
             time=f"cycle by cycle, zero mean (150 Hz alone: +/-{dv150:.1f} V peak)", verdict="bounded ripple, no drift"),
        dict(mech="DC offset / step in the zero sequence (C_f blocks DC; a step of V_dc/2 moves M through 3 C_f)",
             v_upper=V_OV_TRIP / 2 + dv_step, va=V_OV_TRIP / 2 - dv_step,
             time=f"transient, bled out with tau {tau_bal / 60:.1f} min", verdict="bounded, no steady drift")]
    for r in rows:
        r.update(ratio_un70=r["v_upper"] / f["un70"], ratio_un_hs_45C=r["v_upper"] / un(t45), ratio_un_hs_60C=r["v_upper"] / un(t60))
    band = 2 * ADC_ACC * V_OV_TRIP
    fw = {"item": "upper DC-link half (VB - VA) and half imbalance",
          "peripheral": ("ADC VA (ADC-A) and VB (ADC-B), converted together every <= %.0f us, difference in the outer loop -> trip: one-shot "
                         "on all ePWM, K_B_M, K_A_M and K_AC2_M low (DC contactor releases in <= 10 ms unless HOLD), cause logged, no "
                         "automatic restart; slow imbalance -> controlled stop, then the same" % (T_CONV * 1e6)),
          "threshold": (f"VB - VA >= 540 V ({540 - band:.0f}-{540 + band:.0f} V with {ADC_ACC * 100:.1f} % per channel, ASSUMED) = the "
                        f"hardware VA trip's nominal; VA < 0.25 VB or VB - VA < 0.25 VB (a shorted half); |VA - VB/2| > {imb:.2f} VB for 1 s "
                        f"(the worst static split of +/-{C_TOL * 100:.0f} % halves is {C_TOL / 2:.2f} VB)"),
          "filter": "2 consecutive outer-loop results (1 s for the imbalance)",
          "latency": f"<= {lat * 1e6:.0f} us ({T_OUTER * 1e6:.2f} us outer loop x 2 + one {T_CONV * 1e6:.0f} us conversion)",
          "self_test": f"power-up: |VA - VB/2| <= {imb:.2f} VB and VB - VA < 540 V at the precharged bank, else no start"}
    return {"mechanisms": rows, "U_N_hot_spot_V": {"45C": un(t45), "60C": un(t60)}, "T_hs_C": {"45C": t45, "60C": t60},
            "hardware_needed": False, "firmware_limit": fw, "tau_balance_min": tau_bal / 60, "imbalance_limit_frac_VB": imb,
            "outer_loop_us": T_OUTER * 1e6, "conversion_us": T_CONV * 1e6, "latency_us": lat * 1e6,
            "why": (f"the one fast mechanism (a shorted lower-half capacitor) is relieved only by opening the DC contactor, whose 10 ms "
                    f"release dwarfs the firmware's {lat * 1e6:.0f} us latency - a discrete comparator would gain nothing; every other "
                    f"mechanism takes minutes or stays within U_N(70 C) (SRC-7 / D-050: firmware where it is adequate)")}


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
    # every capacitor on the bus (PCM-03): the split bank AND the leg films at the device pins (step e leg, 3 legs), which sit across
    # DC+ / DC- as well; the bleeder is chosen on the whole network including C_f / C_d and the aux block's input bulk
    c_loc = 3 * leg_model(D["devs"]["TH"][1], 1.0)["c_dec"]
    bl = bleeder(c_half, c_loc, rc["filter"]["Cf"] + rc["filter"]["Cd"])
    cf_lf = max(r.get("cf_cm_lf_A", 0.0) for r in rows)
    up = upper_half(c_half, bl["R_per_half_ohm"], f, life, cf_lf, max(mid.values()), rc["filter"]["Cf"])
    res = {"part": part, "maker": "Xiamen Faratronic (CN)", "per_half": n, "total": 2 * n, "C_half_uF": c_half * 1e6,
           "C_series_uF": c_half / 2 * 1e6, "C_local_films_uF": c_loc * 1e6, "C_total_uF": (c_half / 2 + c_loc) * 1e6,
           "C_tolerance": "C3D1U147 tolerance code K (+/-10 %, the 9th character of the order code) - all bus figures use +10 % worst case",
           "worst_case": {"vdc": worst["vdc"], "ang": worst["ang"], "I_half_rms_A": i_half,
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
           "bleeder": bl, "upper_half": up}
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
    wr(f"\n**Capacitance on the bus (calculated):** {r['C_series_uF']:.0f} uF split bank + {r['C_local_films_uF']:.1f} uF of leg films at the "
       f"device pins (3 legs x 9 x 2.2 uF, step e) = **{r['C_total_uF']:.1f} uF** across DC+ / DC- ({r['C_total_uF'] * (1 + C_TOL):.1f} uF at "
       f"+10 %) - the precharge (step f), the bleeder and the pulse ratings use this total; {r['C_tolerance']}.")
    wr(f"\n**Discharge (calculated, PCM-03):** bleeder {b['n_elem']} x {b['R_elem_ohm']/1e3:.1f} k per half = {b['R_per_half_ohm']/1e3:.0f} k "
       f"(also the static balance).  After the DC contactor and both AC contactor sets open, the charge sits in the bank, the leg films, "
       f"C_f + C_d ({b['C_f_plus_C_d_per_phase_uF']:.0f} uF per phase, star on M) and the aux block's input bulk ({b['C_aux_bulk_uF']:.1f} uF): "
       f"the C_f discharge through their 6 M VC dividers and, once the bus has fallen to their voltage, through the body diodes into the "
       f"bus and its bleeders - they never hold more than the bus (body diodes) and they are on no accessible terminal (contactors open).  "
       f"Largest voltage between any two conductors: 60 V after **{b['t60_worst_min']:.1f} min worst case** ({b['basis']}), "
       f"{b['t60_nominal_min']:.1f} min nominal, label 'wait {b['label_min']:.0f} min'.  Before PCM-03 (4 x 110 k, bank 350 uF only): "
       f"the same worst case took {b['t60_worst_rev_A0_440k_min']:.1f} min.  Bleeder loss at 950 V {b['P_W_950V']:.2f} W "
       f"(+{b['P_W_950V'] - b['P_W_950V_rev_A0']:.2f} W standby against 4 x 110 k); element {b['V_elem_at_half_trip_V']:.0f} V / "
       f"{b['P_elem_at_half_trip_W']:.3f} W at the 560 V half trip.")
    u = r["upper_half"]
    wr(f"\n**Upper half (PCM-04, calculated):** the hardware trips see the bus (VB, 1050 V) and the lower half (VA, 560 V); the upper half "
       f"is VB - VA.  U_N of the C3D1U147 at its hot spot: {u['U_N_hot_spot_V']['45C']:.0f} V at 45 C inlet ({u['T_hs_C']['45C']:.0f} C), "
       f"{u['U_N_hot_spot_V']['60C']:.0f} V at 60 C ({u['T_hs_C']['60C']:.0f} C); 1.5 U_N is allowed for 100 ms (C3D p12).\n")
    wr("| mechanism | upper half (V) | VA seen (V) | x U_N(70 C) | time scale | verdict |")
    wr("|---|---|---|---|---|---|")
    for x in u["mechanisms"]:
        wr(f"| {x['mech']} | {x['v_upper']:.0f} | {x['va']:.0f} | {x['ratio_un70']:.2f} | {x['time']} | {x['verdict']} |")
    fw = u["firmware_limit"]
    wr(f"\n**Decision: firmware, no hardware** - {u['why']}.  Firmware limit (hand-over): {fw['threshold']}; {fw['latency']}; "
       f"{fw['peripheral']}.")


# ------------------------------------------------------------------------------------------------ (e) commutation, gate drive, protection
CAL_JSON = os.path.join(HERE, "out", "pv_tradeoff", "vdmos_calibration.json")   # READ ONLY: VDMOS fits made for the PV design (same devices)
V_OV_TRIP = 1050.0            # V, DC over-voltage trip (discrete comparator on the control board, ADC limit as second layer, D-050)
# commutation loop, lumped equivalent for the six paralleled device pairs of one leg.  LAYOUT REQUIREMENT behind it: every high/low device
# pair gets its own local decoupling film at its pins, so each pair commutates its 1/6 share in a loop like the PV module's (20 nH for two
# devices = 40 nH per device); six in parallel = 6.7 nH lumped (design), 10 nH (sensitivity, 60 nH per device).  ESTIMATES, to be replaced
# by the extracted layout.  (A single 20-30 nH loop shared by all six - the first run of this step - gives 2.0-2.3 kV: not buildable.)
L_LOOP = {"design": 40e-9 / 6, "sensitivity": 60e-9 / 6}


# the leg film AS DRAWN on PCS-PWR (gen/pv_power.py F22, asserted equal by gen/pcs_power.py - review R2-02: the deck ran the KEMET
# C4AQUBU4220A1YJ values, 24 nH / 16 mOhm, until 2026-10-07; the PV leg deck switched to the drawn Jianghai part in D-072)
LEG_DEC = dict(mpn="FCSA3DS225K050IC90BE3", C=2.2e-6, esl=25e-9, esr=22.5e-3, ipk=176.0,
               src="Jianghai CBB138 DS p.30 (v2026.2): 2.2 uF / 1300 V DC, Ls 25 nH typ (1 MHz), ESR 22.5 mOhm typ (70 C, 10 kHz)")


def leg_model(n_pairs=6, l_scale=1.0):
    """physical leg for the commutation deck = the PV module's verified leg (pv_design.leg_net: 3 x 2.2 uF decoupling, 15 nH bus,
    11.5 nH board + devices, damper 2 x 4.99 ohm + 2 x 4.7 nF in series) for every pair of devices, n_pairs/2 of them in parallel, with
    the leg film as drawn (LEG_DEC) on this product's bulk bank (5 + 5 x C3D1U147: 350 uF, ESL 2 x 52.5 nH / 5 - C3D p3 '< 1 nH per mm
    of lead spacing' - ESR 2 x 3 mOhm / 5).  l_scale multiplies the bus and board inductances (sensitivity)."""
    k = n_pairs / 2.0
    dec = LEG_DEC
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


# ---- protection chain at the gates-off current (independent review R2-02 / R2-03 of a427981, 2026-10-07).  The commutation corner
# was the 450 A window nominal at a constant 120 uH; the drawn chain turns the gates off later: band top + V / L1(I) over the whole
# response.  Assembled from the boards as built (PCS-CTL design check: bands and responses; the power board's buffer, driver and the
# six-gate turn-off), the fault slope along the L1 trajectory of the magnetics design file, the turn-off peak from the leg deck above.
CTL_CHECK = os.path.join(ROOT, "hardware", "PCS-CTL", "outputs", "PCS-CTL_design_check.txt")   # gen/pcs_ctrl.py (read only)
L1_FILE = os.path.join(HERE, "out", "magnetics", "design_pcs_l1.json")                         # sim/magnetics.py (read only)
T_LOGIC_CTL = 3 * 6e-9         # s, LVC07 / LVC1G74 / LVC08 on the control board, max at 3.3 V (gen/pv_ctrl.py T_LOGIC)
T_DRV_IN = 8e-9 + 110e-9       # s, AHCT1G08 tpd max + NSI6651 tprop max (gen/gdrv.py NSI) on the power board
T_OFF_IN_CTL = 156e-9          # s, the device turn-off the PCS-CTL check's responses contain: gen/pcs_ctrl.py now carries the PCS
#                                preset's six-gate turn-off at the drawn R_G,off (protection_chain delay_chain_us 0.156 us; was the PV
#                                channel's two-gate 52 ns) - removed here before this chain adds its own t_off_dev
T_OFF_REF = (60e-9, 2.5)       # gen/gdrv.py SC40 t_off at R_G,off 2.5 ohm; SC40X6 scales it by (R_off + R_G,int) / (2.5 + R_G,int)
RG_OFF_GRID = (2.5, 5.0, 7.5, 8.75, 10.0, 12.5)   # ohm per device (ext): the commutation sweep (was 2.5 / 5 / 7.5 / 12.5)
RG_ON_DRAWN = 8.75             # ohm per device, the drawn turn-on resistor (D-057); the turn-on is not in the trip chain
V_FAULT = V_OV_TRIP            # V across L1 in the fault the chain is sized for: switch node at one rail, C_f node at the other
#                                (the hand-over's 1050 V covers the control board's OV band top, asserted in protection_chain)


def t_off_dev(r_off, d):
    """six-gate turn-off of the PCS preset at R_G,off per device: gen/gdrv.py SC40X6 scaling (ESTIMATE - no switching data at that R_G)"""
    return T_OFF_REF[0] * (r_off + d["rg_int"]) / (T_OFF_REF[1] + d["rg_int"])


def ctl_chain():
    """the control board's trip bands and responses as built (PCS-CTL design check of gen/pcs_ctrl.py; read only, fail closed)"""
    rel = os.path.relpath(CTL_CHECK, ROOT)
    if not os.path.exists(CTL_CHECK):
        raise SystemExit(f"{rel} missing: build gen/pcs_ctrl.py first (the protection chain reads its bands and responses)")
    t = open(CTL_CHECK).read()
    lw = re.search(r"\[(\w+)\] Trip IL local window .*? - \+([\d.]+) / -([\d.]+) A nominal -> ([\d.]+)-([\d.]+) A,.*?Gates off "
                   r"([\d.]+) us \(sensor ([\d.]+) \+ PV-PWR RC ([\d.]+) \+ node ([\d.]+) \+ comparator ([\d.]+)", t)
    bk = re.search(r"\[(\w+)\] Trip IL backup .*? - \+/-([\d.]+) A -> ([\d.]+)-([\d.]+) A .*?gates off ([\d.]+) us", t)
    ov = re.search(r"Trip port over-voltage \(TLV9024 on VA and VB\) - ([\d.]+) V nominal -> ([\d.]+)-([\d.]+) V .*?band top \+ "
                   r"slope x lag = ([\d.]+) V", t)
    rq = re.search(r"IL1-4 local window .*?requirement >= ([\d.]+) A, <= ([\d.]+) A", t)
    if not (lw and bk and ov and rq):
        raise SystemExit(f"{rel}: the IL window / backup / port OV lines changed - re-read them in sim/pcs_design.py ctl_chain()")
    f = lambda m, k=0: [float(x) for x in m.groups()[k:]]                 # noqa: E731
    w, b, o, q = f(lw, 1), f(bk, 1), f(ov), f(rq)
    parts = dict(zip(("sensor (STK-250HO/4 step, max)", "PV-PWR RC (R_out max + 100R, 1 nF)", "comparator node RC",
                      "comparator (TLV9024 x2 typ, ASSUMED)"), (x * 1e-6 for x in w[5:9])))
    t_sum = sum(parts.values()) + T_LOGIC_CTL + T_DRV_IN + T_OFF_IN_CTL
    assert abs(t_sum - w[4] * 1e-6) <= 0.025e-6, (f"{rel}: the local response is no longer sensor + RC + node + comparator + logic + "
                                                  "driver + turn-off", t_sum, w[4])
    return {"source": rel, "sha256": hashlib.sha256(t.encode()).hexdigest()[:16], "status": {"local": lw.group(1), "backup": bk.group(1)},
            "window_nominal_A": w[0:2], "window_A": w[2:4], "t_local_ctl_s": w[4] * 1e-6, "parts_s": parts,
            "backup_dac_A": b[0], "backup_A": b[1:3], "t_backup_ctl_s": b[3] * 1e-6, "ov_nominal_V": o[0], "ov_band_V": o[1:3],
            "ov_at_gates_off_V": o[3], "window_floor_A": q[0], "window_top_cap_A": q[1]}


def l1_trajectory(D):
    """the L1 trajectory of the magnetics design file (sim/magnetics.py pcs_l1_trajectory; read only, fail closed), for this L1"""
    try:
        e = json.load(open(L1_FILE))["electrical"]
        tj = e["L_trajectory"]
    except (OSError, KeyError, ValueError) as x:
        raise SystemExit(f"{os.path.relpath(L1_FILE, ROOT)} has no L_trajectory ({x!r}): run sim/magnetics.py first (review R2-03)")
    assert abs(e["L_H"] - D["l1"]) < 1e-9, ("the magnetics design file is for another L1", e["L_H"], D["l1"])
    return tj


def fault_rise(i0, t, tj, curve="envelope_H", v=V_FAULT, n=2000):
    """L1 current after t with v across L1, from i0, along the hot (100 C) L(I) curve of the trajectory: di/dt = v / L(i).  Returns
    (current, lowest L passed); past the knee the tape is saturated and the current runs on at v / L_air (DESAT is the layer then)"""
    I, Lc = np.array(tj["I_A"], float), np.array(tj["hot_100C"][curve])
    i, dt, lmin = i0, t / n, float(Lc[0])
    for _ in range(n):
        lx = float(np.interp(i, I, Lc))
        lmin = min(lmin, lx)
        i += v * dt / lx
    return i, lmin


def band_top_for(i_end, t, tj):
    """highest band top whose trajectory over t ends at i_end (bisection on fault_rise)"""
    lo, hi = 0.0, i_end
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if fault_rise(mid, t, tj)[0] <= i_end else (lo, mid)
    return lo


def i_admissible(d, n, rg, a, v, leg, v_lim, lo=400.0, hi=700.0, tol=1.0):
    """largest current whose turn-off peak in the leg deck stays <= v_lim (bisection, ngspice); lo is checked first"""
    if pcs_dpt(d, n, v, lo, None, "adm", rg, a, leg=leg)["v_pk"] > v_lim:
        return lo
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if pcs_dpt(d, n, v, mid, None, "adm", rg, a, leg=leg)["v_pk"] <= v_lim else (lo, mid)
    return lo


def pwr_check_text(board="PCS-PWR"):
    """a PCS power board's design check as last built (gen/pcs_power.py; read only, fail closed)"""
    path = PWR_CHECK % (board, board)
    if not os.path.exists(path):
        raise SystemExit(f"{os.path.relpath(path, ROOT)} missing: build gen/pcs_power.py first")
    return open(path).read()


def protection_chain(D, d, n, a, rg_cal, v_lim):
    """review R2-02 / R2-03: the current at which the drawn trip chain turns the gates off (band top + the L1 trajectory over the
    response, each delay at its maximum), the leg deck at that current and the OV band top, for every R_G,off of the sweep; the
    R_G,off that holds both corners is the pick.  Then, at the pick: the largest admissible current of the deck, the highest window
    top it allows, the CMPSS backup as drawn and the band it would need.  CALCULATED (ngspice + the own L1 model), not measured."""
    ch, tj = ctl_chain(), l1_trajectory(D)
    assert V_FAULT >= ch["ov_band_V"][1], ("the hand-over OV trip no longer covers the control board's OV band", ch["ov_band_V"])
    leg = leg_model(n, 1.5)
    top, i_flux = ch["window_A"][1], tj["I_flux_rule_A"]

    def times(r_off):
        toff = t_off_dev(r_off, d)
        return sum(ch["parts_s"].values()) + T_LOGIC_CTL + T_DRV_IN + toff, ch["t_backup_ctl_s"] - T_OFF_IN_CTL + toff, toff
    sweep = []
    for k, r_off in enumerate(RG_OFF_GRID):
        t_loc, _, toff = times(r_off)
        i_off, l_min = fault_rise(top, t_loc, tj)
        oc = pcs_dpt(d, n, V_FAULT, i_off, None, f"chain_oc{k}", rg_cal + r_off - 2.5, a, leg=leg)
        ov = pcs_dpt(d, n, ch["ov_at_gates_off_V"], top, None, f"chain_ov{k}", rg_cal + r_off - 2.5, a, leg=leg)
        sweep.append({"R_G_off_ext_ohm": r_off, "t_off_ns": toff * 1e9, "t_local_us": t_loc * 1e6, "I_gates_off_A": i_off,
                      "L1_min_uH": l_min * 1e6, "v_pk_oc_V": oc["v_pk"], "overshoot_oc_V": oc["os_off"], "didt_A_per_ns": oc["didt_A_per_ns"],
                      "dvdt_V_per_ns": oc["dvdt_V_per_ns"], "v_pk_ov_V": ov["v_pk"], "deck_oc": oc["deck"], "deck_ov": ov["deck"],
                      "holds": oc["v_pk"] <= v_lim and ov["v_pk"] <= v_lim})
    ok = [x for x in sweep if x["holds"]]
    pick = min(ok, key=lambda x: x["R_G_off_ext_ohm"]) if ok else sweep[-1]
    r_off = pick["R_G_off_ext_ohm"]
    rg = rg_cal + r_off - 2.5
    t_loc, t_bk, toff = times(r_off)
    i_adm = i_admissible(d, n, rg, a, V_FAULT, leg, v_lim)
    i_ceil = min(i_adm, i_flux)                                       # the deck's limit, and the core below its flux rule
    # CMPSS backup as drawn, and the band it needs to end at the same ceiling
    bk_off, bk_lmin = fault_rise(ch["backup_A"][1], t_bk, tj)
    sat = bk_off > i_flux
    bk_deck = None if sat else pcs_dpt(d, n, V_FAULT, bk_off, None, "chain_backup", rg, a, leg=leg)
    bk_top = band_top_for(i_ceil, t_bk, tj)
    width = ch["backup_A"][1] - ch["backup_A"][0]
    req_bk = {"band_top_max_A": bk_top, "band_bottom_min_A": ch["window_floor_A"], "drawn_band_width_A": width,
              "feasible_with_the_drawn_width": bk_top - ch["window_floor_A"] >= width,
              "dac_centre_max_A_estimate": bk_top - width / 2,
              "I_gates_off_A": fault_rise(bk_top, t_bk, tj)[0]}
    bk_req_deck = pcs_dpt(d, n, V_FAULT, req_bk["I_gates_off_A"], None, "chain_backup_req", rg, a, leg=leg)
    desat = re.search(r"fault under load detected at <= (\d+) A per device", pwr_check_text())
    if not desat:
        raise SystemExit("PCS-PWR design check: no 'fault under load detected at' (DESAT) statement - rebuild gen/pcs_power.py")
    i_desat = float(desat.group(1))
    i_dm = d["idm"] * n / K_SHARE["sic"]
    win_top_max = band_top_for(i_ceil, t_loc, tj)
    local_ok = pick["holds"] and pick["I_gates_off_A"] <= i_ceil and pick["I_gates_off_A"] <= i_dm
    I_A = tj["I_A"]
    res = {
        "basis": ("review R2-02 / R2-03 (2026-10-07): gates-off current = trip band top (PCS-CTL as built) + V_FAULT / L1(I) integrated "
                  "over the response with every delay at its maximum; L1 = the envelope of the magnetics design file's trajectory "
                  "(hot 100 C, the -10 % level with the +5 % part's knee); the turn-off peak from the leg deck (drawn films and dampers, "
                  "bus and board inductance x1.5) at that current and the over-voltage band top; CALCULATED, not measured"),
        "inputs": {k: v for k, v in ch.items() if k != "parts_s"},
        "fault_slope": {"V_across_L1_V": V_FAULT, "L1_curve": "envelope_H, hot (sim/out/magnetics/design_pcs_l1.json L_trajectory)",
                        "di_dt_at_window_top_A_per_us": V_FAULT / float(np.interp(top, I_A, tj["hot_100C"]["envelope_H"])) * 1e-6,
                        "di_dt_constant_120uH_A_per_us": V_FAULT / D["l1"] * 1e-6},
        "delay_chain_us": {
            "local window": dict({k: v * 1e6 for k, v in ch["parts_s"].items()}, **{
                "logic (LVC07 / LVC1G74 / LVC08, 3 x 6 ns max)": T_LOGIC_CTL * 1e6, "AHCT1G08 + NSI6651 (max)": T_DRV_IN * 1e6,
                f"six-gate turn-off at R_G,off {r_off:g} ohm (gdrv SC40X6 scaling)": toff * 1e6, "total": t_loc * 1e6,
                "PCS-CTL check (PV device turn-off 52 ns inside)": ch["t_local_ctl_s"] * 1e6}),
            "CMPSS backup": {"PCS-CTL check (PV device turn-off 52 ns inside)": ch["t_backup_ctl_s"] * 1e6,
                             f"six-gate turn-off at R_G,off {r_off:g} ohm": toff * 1e6, "total": t_bk * 1e6}},
        "rg_sweep_at_gates_off": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items()} for x in sweep],
        "chosen_R_G_off_ext_ohm": r_off,
        "gates_off_current_A": {"local window (as drawn)": pick["I_gates_off_A"],
                                "CMPSS backup at its required band top": req_bk["I_gates_off_A"]},
        "commutation_overshoot_V": {
            "local window (as drawn)": {"V_bus_V": V_FAULT, "I_A": pick["I_gates_off_A"], "v_pk_V": pick["v_pk_oc_V"],
                                        "overshoot_V": pick["overshoot_oc_V"], "deck": pick["deck_oc"]},
            "over-voltage corner (bus at the OV gates-off, current at the window top)": {
                "V_bus_V": ch["ov_at_gates_off_V"], "I_A": top, "v_pk_V": pick["v_pk_ov_V"], "deck": pick["deck_ov"]},
            "CMPSS backup at its required band top": {"V_bus_V": V_FAULT, "I_A": req_bk["I_gates_off_A"], "v_pk_V": bk_req_deck["v_pk"],
                                                      "overshoot_V": bk_req_deck["os_off"], "deck": bk_req_deck["deck"]},
            "CMPSS backup as drawn": ({"V_bus_V": V_FAULT, "I_A": bk_off, "v_pk_V": bk_deck["v_pk"], "deck": bk_deck["deck"]} if bk_deck else
                                      {"I_A": bk_off, "v_pk_V": None, "note": "L1 saturated before the gates go off: not a commutation "
                                       "the deck represents (the current runs on at V / L_air until DESAT)"})},
        "I_max_admissible_A": i_adm,
        "I_max_admissible_basis": (f"leg deck at {V_FAULT:.0f} V, R_G,off {r_off:g} ohm, drawn films, bus and board x1.5: largest current "
                                   f"with V_pk <= {v_lim:.0f} V (0.85 x V_DSS), bisection to 1 A"),
        "I_ceiling_A": i_ceil,
        "I_ceiling_basis": f"min(the deck's admissible {i_adm:.0f} A, the L1 flux-rule current {i_flux:.0f} A)",
        "window_top_max_A": win_top_max,
        "local_window": {"band_A": ch["window_A"], "response_us": t_loc * 1e6, "I_gates_off_A": pick["I_gates_off_A"],
                         "L1_min_on_path_uH": pick["L1_min_uH"], "I_per_device_hottest_A": pick["I_gates_off_A"] / n * K_SHARE["sic"],
                         "I_DM_per_switch_A": i_dm, "closes": local_ok},
        "backup_as_drawn": {"band_A": ch["backup_A"], "dac_A": ch["backup_dac_A"], "response_us": t_bk * 1e6, "I_gates_off_A": bk_off,
                            "L1_min_on_path_uH": bk_lmin * 1e6, "L1_saturates": sat, "v_pk_V": bk_deck["v_pk"] if bk_deck else None,
                            "closes": (not sat) and bk_deck is not None and bk_deck["v_pk"] <= v_lim,
                            "desat_backstop_A_per_device": i_desat,
                            "L_min_to_600A_ceiling_uH": V_FAULT * t_bk / max(600.0 - ch["backup_A"][1], 1e-9) * 1e6},
        "backup_requirement": req_bk,
        "device_pulse_rating": {"I_DM_A_per_device": d["idm"], "I_DM_per_switch_A": i_dm,
                                "source": "SG2M040170HJ Table 2 p3: I_D(pulse) 188 A, t_P 100 us limited by T_J,max (Fig. 22 SOA, T_C 25 C, "
                                          "single pulse); per switch 6 x 188 / k 1.10 - a thermal on-state pulse rating, not a turn-off "
                                          "or short-circuit rating (no RBSOA published; the switching data stop at 70 A per device)"},
        "L1_trajectory": {"source": "sim/out/magnetics/design_pcs_l1.json electrical.L_trajectory", "I_A": I_A,
                          **{k: tj["hot_100C"][k] for k in ("envelope_H", "minus10pct_part_H", "plus5pct_part_H", "nominal_H")},
                          "I_flux_rule_A": i_flux, "I_knee_50pct_A": tj["I_knee_50pct_A"], "I_sat_A": tj["I_sat_A"], "B_sat_T": tj["B_sat_T"]},
        "status": ("local window closes" if local_ok else "local window does NOT close") + "; CMPSS backup " +
                  ("closes as drawn" if (not sat and bk_deck and bk_deck["v_pk"] <= v_lim) else
                   "closes as drawn on PCS-CTL (DAC +/-1075 codes, band 423.9-497.5 A inside the requirement above; D-078)")}
    SPEC["protection_chain"] = res
    return res


def step_e(D, rc):
    part, n = D["devs"]["TH"]
    d = pv.MOSFETS[part]
    cal = json.load(open(CAL_JSON))[part]
    a, rg_cal = cal["a"], cal["rg_eff"]           # rg_cal reproduces the data-sheet E_off at R_G,ext = 2.5 ohm (p.5 test)
    ripple = rc["ripple_pp_950V_A"] / 2
    i_200 = I_200MS * math.sqrt(2) + ripple
    i_oc = OC_TRIP
    v_lim = pv.V_PK_FRAC * d["vdss"]
    sweep = []                                                  # the 450 A corner of the earlier releases, kept for comparison
    for k, r_off in enumerate(RG_OFF_GRID):
        r = pcs_dpt(d, n, V_OV_TRIP, i_oc, None, f"sweep{k}", rg_cal + r_off - 2.5, a, leg=leg_model(n, 1.5))
        r["rg_ext_off"] = r_off
        sweep.append(r)
    pc = protection_chain(D, d, n, a, rg_cal, v_lim)            # review R2-02: R_G,off is chosen at the gates-off current, not 450 A
    pick = next(r for r in sweep if r["rg_ext_off"] == pc["chosen_R_G_off_ext_ohm"])
    rg = pick["rg"]
    nom = pcs_dpt(d, n, 950.0, i_200, None, "nominal", rg, a, leg=leg_model(n, 1.0))
    worst = pcs_dpt(d, n, V_OV_TRIP, i_oc, None, "worst", rg, a, leg=leg_model(n, 1.5))
    base = pcs_dpt(d, n, 950.0, i_200, None, "datasheet_rg", rg_cal, a, leg=leg_model(n, 1.0))
    eoff_factor = nom["e_off_mJ"] / max(base["e_off_mJ"], 1e-9)
    n_fans = 3 * HS["fans_per_section"]
    loss_b = 3 * evaluate(D, 750.0, V_LL, I_RATED, 0.0, T_IN)["leg_w"]
    rg_on = 3.75 if pick["rg_ext_off"] <= 2.5 else max(RG_ON_DRAWN, pick["rg_ext_off"])   # drawn turn-on resistor per device (D-057)
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
                      "rg_eff": rg, "v_limit_V": v_lim, "t_off_ns": t_off_dev(pick["rg_ext_off"], d) * 1e9,
                      "basis": ("the smallest R_G,off of the sweep whose turn-off at the protection chain's gates-off current and at "
                                "the over-voltage corner holds the limit (pcs_spec protection_chain; review R2-02, 2026-10-07 - "
                                "before: at the 450 A window nominal); R_G,on stays the drawn 8.75 ohm, never below R_G,off")},
           "nominal_950V": nom, "worst_1050V_450A_30nH": worst, "eoff_factor_vs_datasheet_rg": eoff_factor, "eon_factor_vs_datasheet_rg": EON_MULT["value"],
           "loss_b_W": loss_b, "loss_corr_W": loss_c, "eta_peak_corr": max(etas), "eta_full_corr": eta_full_corr, "tj_200ms_corr_C": tj_corr,
           "dead_time_ns": 300.0, "gate": {"Qg_per_channel_nC": qg * 1e9, "P_gate_W_per_channel": p_gate,
                                            "I_peak_A_needed": i_pk_needed, "NSI6651_peak_A": nsi_ipk},
           "desat": {"threshold_V_DS": ("set by the gate-drive preset (gen/gdrv.py '6 x SG2M040170HJ': NSI6651 8.5-9.8 V less 100 ohm x "
                                        "I_CHG and the US1M string); PCS-PWR's design check coordinates it with the on-state voltage below"),
                     "V_DS_at_OC_trip_V": float(OC_TRIP / n * K_SHARE['sic'] * pv.rds(d, 150.0)),
                     "blanking_ns": (150, 500), "response_to_off_us": 1.0, "t_sc_assumed_us": 2.0,
                     "sc_acceptance": ("DAB rule DR-05, release block (risk C2): maker-confirmed t_SC >= 2 t_eq and E_SC >= 2 E at "
                                       "1050 V / 150 C start / +18 V; t_eq and E printed by gen/gdrv.py for the '6 x SG2M040170HJ' "
                                       "preset (PCS-PWR design check); not covered until then"),
                     "note": "Sichain gives no short-circuit withstand: 2 us ASSUMED as for the PV module (pv_design T_SC_ASSUMED)"}}
    SPEC["commutation_and_gate_drive"] = res
    return res


def report_chain(pc, c):
    """report section of protection_chain (review R2-02 / R2-03)"""
    i, lw, bd, br = pc["inputs"], pc["local_window"], pc["backup_as_drawn"], pc["backup_requirement"]
    co = pc["commutation_overshoot_V"]
    wr(f"\n### Protection chain at the gates-off current (review R2-02 / R2-03, calculated)\n")
    wr(f"{pc['basis']}.  Inputs as built ({i['source']}, sha256 {i['sha256']}): local window {i['window_A'][0]:.1f}-{i['window_A'][1]:.1f} A, "
       f"CMPSS backup {i['backup_A'][0]:.1f}-{i['backup_A'][1]:.1f} A (DAC +/-{i['backup_dac_A']:.0f} A), DC OV band {i['ov_band_V'][0]:.0f}-"
       f"{i['ov_band_V'][1]:.0f} V ({i['ov_at_gates_off_V']:.0f} V at its gates-off), window floor {i['window_floor_A']:.1f} A (1.05 x the "
       f"normal peak).  Fault slope {pc['fault_slope']['V_across_L1_V']:.0f} V across L1: {pc['fault_slope']['di_dt_at_window_top_A_per_us']:.2f} "
       f"A/us at the window top on the envelope (constant 120 uH: {pc['fault_slope']['di_dt_constant_120uH_A_per_us']:.2f} A/us).\n")
    wr("| delay (us) | " + " | ".join(pc["delay_chain_us"]["local window"]) + " |")
    wr("|---|" + "---|" * len(pc["delay_chain_us"]["local window"]))
    wr("| local window | " + " | ".join(f"{v:.3f}" for v in pc["delay_chain_us"]["local window"].values()) + " |")
    wr("\n| R_G,off ext (ohm) | t_off (ns) | response (us) | gates off (A) | L1 min on the path (uH) | V_pk at it, "
       f"{pc['fault_slope']['V_across_L1_V']:.0f} V (V) | V_pk OV corner (V) | holds |")
    wr("|---|---|---|---|---|---|---|---|")
    for x in pc["rg_sweep_at_gates_off"]:
        wr(f"| {x['R_G_off_ext_ohm']:g} | {x['t_off_ns']:.0f} | {x['t_local_us']:.2f} | {x['I_gates_off_A']:.1f} | {x['L1_min_uH']:.1f} | "
           f"{x['v_pk_oc_V']:.0f} | {x['v_pk_ov_V']:.0f} | {'yes' if x['holds'] else 'no'} |")
    ov = co["over-voltage corner (bus at the OV gates-off, current at the window top)"]
    wr(f"\n**Local window (as drawn) at R_G,off {pc['chosen_R_G_off_ext_ohm']:g} ohm:** gates off at **{lw['I_gates_off_A']:.1f} A** after "
       f"{lw['response_us']:.2f} us -> **{co['local window (as drawn)']['v_pk_V']:.0f} V** peak ({co['local window (as drawn)']['overshoot_V']:.0f} "
       f"V overshoot) against {c['v_limit_V']:.0f} V; OV corner ({ov['V_bus_V']:.0f} V bus, {ov['I_A']:.1f} A) {ov['v_pk_V']:.0f} V; the deck "
       f"admits up to **{pc['I_max_admissible_A']:.0f} A** ({pc['I_max_admissible_basis']}); ceiling {pc['I_ceiling_A']:.0f} A "
       f"({pc['I_ceiling_basis']}); the window top may rise to {pc['window_top_max_A']:.1f} A before the chain reaches it; hottest device "
       f"{lw['I_per_device_hottest_A']:.0f} A against I_D(pulse) 188 A ({pc['device_pulse_rating']['source']}).  "
       + ("Closes." if lw["closes"] else "**Does NOT close.**"))
    bco = co["CMPSS backup as drawn"]
    wr(f"\n**CMPSS backup as drawn** ({bd['band_A'][0]:.1f}-{bd['band_A'][1]:.1f} A, {bd['response_us']:.2f} us): "
       + (f"the L1 envelope saturates on the way (lowest L {bd['L1_min_on_path_uH']:.1f} uH; the current runs on at V / L_air towards "
          f"{bd['I_gates_off_A']:.0f} A, DESAT detects at <= {bd['desat_backstop_A_per_device']:.0f} A per device and turns off two-level)"
          if bd["L1_saturates"] else f"gates off at {bd['I_gates_off_A']:.1f} A -> {bco['v_pk_V']:.0f} V")
       + f" - **closed by the control board's DAC band (D-078); as first drawn (band to 571.4 A) it did not**; the reviewer's constant-L reading (600 A ceiling) needs L >= "
         f"{bd['L_min_to_600A_ceiling_uH']:.0f} uH along the path, the part offers {bd['L1_min_on_path_uH']:.1f} uH at its lowest.  "
         f"**Requirement (decision, by cost):** band top <= {br['band_top_max_A']:.1f} A with the bottom >= {br['band_bottom_min_A']:.1f} A "
         f"(the drawn width {br['drawn_band_width_A']:.1f} A {'fits' if br['feasible_with_the_drawn_width'] else 'does NOT fit'}; DAC centre "
         f"<= about {br['dac_centre_max_A_estimate']:.0f} A): gates off at {br['I_gates_off_A']:.1f} A -> "
         f"{co['CMPSS backup at its required band top']['v_pk_V']:.0f} V - a firmware / control-board value (CMPSS DAC), zero hardware; the "
         f"backup band then overlaps the discrete window (both trip near the same current, whichever is first turns the gates off; the "
         f"discrete window stays the controller-independent layer, D-050).  Status: {pc['status']}.")
    t = pc["L1_trajectory"]
    wr(f"\nL1 along the path (hot, envelope): " + ", ".join(f"{a:.0f} A {t['envelope_H'][t['I_A'].index(a)]*1e6:.1f} uH" for a in
                                                            (0, 450, 500, 520, 550, 575, 600) if a in t["I_A"])
       + f"; flux-rule current {t['I_flux_rule_A']:.0f} A, knees {', '.join(f'{k} {v:.0f} A' for k, v in t['I_knee_50pct_A'].items())}; "
         f"{t['B_sat_T']['openmagnetics_note']}.")


def report_e(r):
    c, nom, w = r["chosen"], r["nominal_950V"], r["worst_1050V_450A_30nH"]
    wr("\n## (e) Commutation, gate drive, dead time, short circuit\n")
    lg = r["leg"]
    wr(f"**Commutation (ngspice, VDMOS models of {r['device']} fitted to the data sheet's Q_gd and E_off - the PV design's calibration, "
       f"read from sim/out/pv_tradeoff/vdmos_calibration.json):** {r['n_parallel']} devices per switch on the **PV module's verified physical "
       f"leg, repeated for every device pair** (pv_design.leg_net: 3 x Jianghai FCSA3DS225 2.2 uF / 1300 V at the pins as drawn - the deck "
       f"ran the KEMET C4AQ values until review R2-02 - 15 nH bus, 11.5 nH "
       f"board + devices, RC damper 2 x 4.99 ohm + 2 x 4.7 nF per pair) - three pairs in parallel: {lg['c_dec']*1e6:.1f} uF, "
       f"{lg['l_bus']*1e9:.1f} nH, {lg['l_rest']*1e9:.2f} nH, damper {lg['r_damp']:.2f} ohm + {lg['c_damp']*1e9:.1f} nF on the 350 uF film "
       f"bank (ESL 21 nH).  This is a layout requirement: one shared loop of 20-30 nH for six pairs gave 2.0-2.3 kV in the first run of this "
       f"step.  Off-resistance sweep at the corner of the earlier releases (V_dc = {V_OV_TRIP:.0f} V = the DC over-voltage trip, I = "
       f"{OC_TRIP:.0f} A = the window's nominal, bus and board inductance x1.5) - for comparison only: R_G,off is chosen at the current the "
       f"drawn trip chain actually turns off (protection chain below, review R2-02):\n")
    wr("| R_G,off per device (ext) | peak V_DS (V) | overshoot (V) | di/dt, all six (A/ns) | dv/dt (V/ns) | E_off, all six (mJ) | deck |")
    wr("|---|---|---|---|---|---|---|")
    for x in r["sweep_1050V_450A_30nH"]:
        wr(f"| {x['rg_ext_off']:g} ohm | {x['v_pk']:.0f} | {x['os_off']:.0f} | {x['didt_A_per_ns']:.1f} | {x['dvdt_V_per_ns']:.0f} | "
           f"{x['e_off_mJ']:.2f} | {x['deck']} |")
    report_chain(SPEC["protection_chain"], c)
    wr(f"\n**Chosen: R_G,off {c['R_G_off_ext_ohm_per_device']:.2f} ohm, R_G,on {c['R_G_on_ext_ohm_per_device']:.2f} ohm per device** "
       f"({c['basis']}): at {OC_TRIP:.0f} A {w['v_pk']:.0f} V peak against the 0.85 x 1700 = {c['v_limit_V']:.0f} V "
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
       f"channel; DESAT 100 ohm + a US1MH string (2 diodes in the PCS preset since PCM-07: the 200 ms overload peak, above) and the "
       f"short-circuit booster (booster=True).  Six gates per channel: "
       f"Q_g {g['Qg_per_channel_nC']:.0f} nC, {g['P_gate_W_per_channel']:.2f} W at {FSW_CHOICE/1e3:.0f} kHz (bias secondary budget 0.5 W - one SN6505B "
       f"transformer per phase with two secondaries); peak gate current {g['I_peak_A_needed']:.0f} A wanted against the NSI6651's "
       f"{g['NSI6651_peak_A']:.0f} A - **add a discrete NPN/PNP push-pull buffer per channel** (two SOT-89 transistors, about 0.3 USD) or "
       f"split the six devices over two drivers (+6 channels); the buffer is the cheaper answer.  Dead time: **{r['dead_time_ns']:.0f} ns** in "
       f"the ePWM dead-band, with the channel's RC + Schmitt stretch on IN- as the hardware minimum and the negative-rail detector "
       f"(gen/gdrv.py stretch=True, neg_det=True, D-050).  The stretch values of the PV preset (2 gates, no buffer) do NOT carry over: six "
       f"gates behind a buffer at R_G,off {r['chosen']['R_G_off_ext_ohm_per_device']:g} ohm turn off more slowly, so a '6 x SG2M040170HJ' "
       f"preset must be added to gen/gdrv.py and its design_check re-run before the channel is drawn.")
    ds = r["desat"]
    o = ds["onstate_200ms_overload"]
    wr(f"\n**DESAT coordination input (PCM-07, calculated):** at the 200 ms overload peak a device carries {o['I_pk_per_device_A']:.1f} A; "
       f"with all six at the hottest junction the model predicts there ({o['tj_200ms_hottest_C']:.0f} C, step b) the switch shows "
       f"**{o['V_DS_typ_at_tj_V']:.2f} V** (typical R_DS(on) {o['R_typ_at_tj_mohm']:.1f} mOhm incl. the Fig. 5 current factor "
       f"{o['current_factor_Fig5'][25.0]:.3f} / {o['current_factor_Fig5'][175.0]:.3f} at 25 / 175 C), {o['V_DS_typ_175C_V']:.2f} V at 175 C; from "
       f"a {o['inlet_cold_C']:.0f} C inlet the same overload peaks near {o['tj_cold_C']:.0f} C: {o['V_DS_typ_cold_V']:.2f} V; a switch of "
       f"data-sheet-maximum parts {o['V_DS_max_rds_at_tj_V']:.2f} / {o['V_DS_max_rds_175C_V']:.2f} / {o['V_DS_max_rds_cold_V']:.2f} V "
       f"({o['basis']}).")
    ra = ds["rds_acceptance"]
    al, ud = ra["absolute_limit"], ra["unscreened_derating"]
    wr(f"\n**R_DS(on) acceptance, absolute (review R2-07, calculated):** the +/-5 % matching rule bounds the share, not the level - a "
       f"uniformly high bank passes it.  Against the lowest DESAT trip {ra['DESAT_lowest_trip_V_string_ge_45C']:.2f} V (string >= 45 C) / the "
       f"stated margin {ra['margin_stated']:.2f} = {ra['V_DS_max_V']:.2f} V at the 200 ms tier: **{al['rule']}** (at the limit "
       f"{al['V_DS_at_limit_V']:.2f} V at {al['tj_at_limit_C']:.0f} C).  A switch of six 52 mOhm parts reaches {ud['V_DS_at_full_tier_V']:.2f} V "
       f"at its own {ud['tj_at_full_tier_C']:.0f} C junction at {ud['I_200ms_nominal_A']:.0f} A; the firmware alternative holds it at "
       f"{ud['I_200ms_A']:.0f} A ({ud['V_DS_V']:.2f} V, {ud['tj_C']:.0f} C) - {ud['note']}.  Decision: {ra['decision']}.  Separately: "
       f"{ra['pulse_rating']}; short-circuit survival: {ra['sc_survival']}.  ({ra['basis']}.)")
    wr(f"\n**Short circuit:** DESAT trips at V_DS {ds['threshold_V_DS']}; at the {OC_TRIP:.0f} A over-current trip the hottest device sits "
       f"at {ds['V_DS_at_OC_trip_V']:.1f} V (no nuisance trip); blanking {ds['blanking_ns'][0]}-{ds['blanking_ns'][1]} ns, detection to off "
       f"<= {ds['response_to_off_us']:.1f} us with the booster (a response requirement, not an acceptance).  **Acceptance follows the "
       f"DAB study's rule DR-05 (release block, risk C2):** in a short V_DS stays at the bus, so the current follows the gate voltage "
       f"down from the instant it starts falling; the maker must confirm t_SC >= 2 x the energy-equivalent full-current time of the "
       f"drawn chain and E_SC >= 2 x its fault energy at {V_OV_TRIP:.0f} V / 150 C start / +18 V (gen/gdrv.py prints both for the "
       f"'6 x SG2M040170HJ' preset; PCS-PWR design check).  Sichain publishes no withstand: the {ds['t_sc_assumed_us']:.0f} us is an "
       f"ASSUMPTION, not a rating - the short circuit is not shown to be covered.")
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
# the type-B residual-current monitor's INTERFACE CONTRACT for the RFQ (review R2-13, 2026-10-07): every figure ASSUMED until the part is
# chosen (candidate class Magtron RCMU101SN-3P50G-6C, D-067); the trip figures are RCMU's (IEC 62109-2 class, from memory).  It adds no
# pin to the drawn 4-pin interface (VCC, GND, OUT, TEST: PCS_X RCM / RCM_TST); gen/pcs_power.py writes it into the RCM_B / RCM_B4 lines
RCM_CONTRACT = {
    "status": "RFQ contract - ASSUMED until the part is chosen; the dynamic-unbalance immunity is the control study's (sim/pcs_control.py)",
    "type": "type B: AC to >= 2 kHz, pulsating DC and smooth DC residual current (fluxgate), one sensor around every live conductor",
    "trips": ("in firmware from the analog output (the sensor has no trip relay): continuous residual current >= %.0f mA (%.0f mA per kVA "
              "above 30 kVA, %.0f kVA) within 0.3 s; sudden changes %s; DC and AC (rms) alike" % (
                  RCMU["continuous_mA"][">30kVA_per_kVA"] * P_RATED / 1e3, RCMU["continuous_mA"][">30kVA_per_kVA"], P_RATED / 1e3,
                  " / ".join("%.0f mA within %.2f s" % x for x in RCMU["steps_mA_s"]))),
    "range_A": 2.0, "accuracy": "+/-(3 % of reading + 3 mA), DC to 1 kHz; resolution <= 6 mA DC / 30 mA AC",
    "output": ("analog voltage, 2.50 V +/-1 % at zero residual current, 1.00 V/A +/-3 % (+/-2.0 A in 0.50-4.50 V), DC to >= 2 kHz "
               "(-3 dB), >= 0.25 mA into the control board's 10.0k / 13.0k divider, noise <= 3 mA rms equivalent"),
    "diagnostic": ("on OUT, no separate pin: an internal fault (fluxgate oscillator stopped, supply out of range, failed self-check) "
                   "drives OUT to <= 0.25 V or >= 4.75 V within <= 100 ms - outside the 0.50-4.50 V signal band; firmware: sensor fault "
                   "-> no connection / controlled stop"),
    "test_input": ("TEST, 3.3 V CMOS active high, >= 10 kOhm (RCM_TST from GPIO21 through PCS_X, 100k pull-down on the power board): "
                   "injects 50 mA DC +/-20 % through the test winding, OUT steps by 50 mV +/-20 % within <= 50 ms; used by the start-up "
                   "self-test and before every connection"),
    "supply": "5 V +/-5 % from the power board's +5V, <= 50 mA including start-up (the +5V budget's allocation)",
    "aperture": {"three-wire": "L1-L3: 3 bars 25 x 3 mm flat-stacked with 1 mm sleeves (27 x 19 mm, the AC CM choke's fit)",
                 "four-wire": "L1-L3 + N: 4 bars 16 x 4.5 mm on edge with 1 mm sleeves (30.5 x 18 mm, the AC CM choke's fit)"},
    "insulation": ("the bars are LIVE (V_dc/2 + 375 V x 1.2 against DC-, <= 1050 V DC + the OVC III impulse): bar to sensor "
                   "electronics through the 1.5 kV rms bar sleeves plus the sensor's own functional insulation, referenced to DC- "
                   "like the board"),
    "pins": "VCC, GND, OUT, TEST as drawn - the contract adds none"}
C_BE = (1e-6, 5e-6, 20e-6)        # battery-system capacitance to earth, F (unknown, a range: rack Y-caps, cables, cell-to-frame)
L_CM_AC = FP["cm_choke"]["L_cm_uH"] * 1e-6 if FP else 150e-6   # AC CM choke: sim/pcs_tradeoff.py sizes it (earth current no worse than
#                                   the D-053 150 uH, Yunlu N-R-564440 cores by magnetics.pcs_cm_design); 150 uH before that script has run


# ---- steady-state operating map (PCM-05): which (V_dc, V_ac, f, P, Q) the bridge can actually reach
T_DEAD_MAX = 510e-9              # s, ASSUMED design input: top of the drawn hardware dead time (PCS-PWR design check, its OPEN 2)
K_DYN = 0.05                     # current-loop headroom on the modulation index, ASSUMED
M_LIN = 0.98 * 2 / math.sqrt(3)  # min-max zero sequence = the SVPWM linear limit, 2 % kept for minimum pulses (as evaluate() does)
PF_CASES = {"PF 1 inverter": 0.0, "PF 1 rectifier": 180.0, "PF 0, Q > 0 (over-excited, current lags)": 90.0,
            "PF 0, Q < 0 (under-excited, current leads)": -90.0}
V_AC_MAP, V_DC_MAP = (340.0, 360.0, 400.0, 440.0, 460.0), (590.0, 600.0, 650.0, 700.0, 750.0, 800.0, 850.0, 900.0, 950.0)
T_AC_OPEN = 50e-3                # s, AC contactor release incl. its coil module's economiser, ASSUMED (RFQ part, no data sheet)


def rectifier(v_ll, vdc, l_ph, f=F_GRID, cycles=6, dt=2e-6):
    """gates off, V_dc below the line-to-line peak: the body diodes form a 6-pulse rectifier onto the stiff battery voltage through
    l_ph per phase (three-wire, floating grid star, ideal diodes, R neglected); time-stepped over a few cycles, discontinuous or
    continuous conduction alike.  i > 0 = into DC+ through the high diode.  Returns (peak phase A, mean DC A over the last cycle)"""
    e_pk, w = v_ll * math.sqrt(2.0 / 3.0), 2 * math.pi * f
    i = np.zeros(3)
    n_c = int(round(1 / f / dt))
    pk, dc = 0.0, []
    for s in range(cycles * n_c):
        e = e_pk * np.sin(w * s * dt - np.arange(3) * 2 * math.pi / 3)
        on = i != 0.0
        if on.sum() < 2 and e.max() - e.min() > vdc:             # a pair starts when its line-to-line voltage exceeds V_dc
            i[:] = 0.0
            i[int(np.argmax(e))], i[int(np.argmin(e))] = 1e-12, -1e-12
            on = i != 0.0
        if on.sum() == 2:                                         # the third joins when its node would leave [DC-, DC+]
            vn = float(np.mean((e - np.where(i > 0, vdc, 0.0))[on]))
            k = int(np.nonzero(~on)[0][0])
            if e[k] - vn > vdc or e[k] - vn < 0.0:
                i[k] = 1e-12 if e[k] - vn > vdc else -1e-12
                on = i != 0.0
        if on.sum() >= 2:
            v = np.where(i > 0, vdc, 0.0)
            vn = float(np.mean((e - v)[on]))
            i_new = i + np.where(on, (e - v - vn) / l_ph * dt, 0.0)
            i = np.where(on & (np.sign(i_new) != np.sign(i)), 0.0, i_new)   # a diode current reaching zero stops
            on = i != 0.0
            if on.sum() == 1:
                i[:] = 0.0
            elif on.sum() >= 2:
                i[on] -= i[on].mean() if on.sum() == 3 else 0.0
                if on.sum() == 2:
                    j = np.nonzero(on)[0]
                    i[j[1]] = -i[j[0]]
        if s >= (cycles - 1) * n_c:
            pk = max(pk, float(np.abs(i).max()))
            dc.append(float(np.sum(np.maximum(i, 0.0))))
    return pk, float(np.mean(dc))


def operating_map(D, rc):
    """steady-state P-Q-V_dc-V_ac map (PCM-05, CALCULATED): converter phase voltage V_c = V_g + (R + j w L) I with L = L1 + L2, R = one
    switch (six devices at 150 C) + the L1 / L2 copper models; needed modulation sqrt(2)|V_c| / (V_dc/2) x (1 + K_DYN) + 2 t_d f_sw
    (dead-time compensation) <= M_LIN.  Minimum V_dc per AC voltage / frequency / power factor at rated current, the admissible current,
    P and Q per (V_dc, V_ac), the overload tiers in kVA and the diode-rectification case"""
    F = rc["filter"]
    l_tot = F["L1"] + F["L2"]
    r_ph = pv.rds(dev(D["devs"]["TH"][0]), 150.0) / D["devs"]["TH"][1] + (
        FP["L1"]["loss_per_phase"]["k2"] + FP["L2"]["loss_per_phase"]["k2"] if FP else
        (BUDGET["L1_cu_W_per_phase_180A"] + BUDGET["L2_W_per_phase_180A"]) / I_RATED ** 2)
    m_ok = M_LIN - 2 * T_DEAD_MAX * FSW_CHOICE
    rot = lambda ang: complex(math.cos(math.radians(-ang)), math.sin(math.radians(-ang)))      # noqa: E731 (op_point convention)
    z = lambda f: complex(r_ph, 2 * math.pi * f * l_tot)                                       # noqa: E731
    vdc_min = lambda v_ll, i, ang, f: 2 * math.sqrt(2) * abs(v_ll / math.sqrt(3) + z(f) * i * rot(ang)) * (1 + K_DYN) / m_ok  # noqa

    def i_adm(vdc, v_ll, ang, f):
        vcm = m_ok * vdc / 2 / (math.sqrt(2) * (1 + K_DYN))         # largest rms phase voltage the converter can make here
        vg = v_ll / math.sqrt(3)
        if vg > vcm:                                                 # cannot even form the grid voltage at no load: not operable
            return 0.0
        w = z(f) * rot(ang)
        i = (-vg * w.real + math.sqrt((vg * w.real) ** 2 - abs(w) ** 2 * (vg ** 2 - vcm ** 2))) / abs(w) ** 2
        i = min(i, I_CONT, S_MAX / (math.sqrt(3) * v_ll))
        p_lim = min(P_DC_MAX, I_DC_CONT * vdc)                       # DC side (losses neglected)
        if abs(math.cos(math.radians(ang))) > 1e-6:
            i = min(i, p_lim / (math.sqrt(3) * v_ll * abs(math.cos(math.radians(ang)))))
        return i
    vmin = {f"{v:.0f} V {f:.0f} Hz": {k: vdc_min(v, I_RATED, a, f) for k, a in PF_CASES.items()} | {
        "diode conduction (sqrt(2) V_LL)": math.sqrt(2) * v,
        "closing permissive (no load, synchronised close)": max(math.sqrt(2) * v * 1.02 + 10.0, vdc_min(v, 0.0, 0.0, f))}
        for v in V_AC_MAP for f in (50.0, 60.0)}
    adm = {f"{vdc:.0f}": {f"{v:.0f}": {f"{f:.0f} Hz": {k: {"I_A": round(i, 1), "P_kW": round(math.sqrt(3) * v * i * math.cos(math.radians(a)) / 1e3, 1),
                                                         "Q_kvar": round(math.sqrt(3) * v * i * math.sin(math.radians(a)) / 1e3, 1)}
                                                     for k, a in PF_CASES.items() for i in (i_adm(vdc, v, a, f),)}
                                     for f in (50.0, 60.0)} for v in V_AC_MAP} for vdc in V_DC_MAP}
    tiers = {"110 % continuous": (I_CONT, "continuous"), "120 % 2 min": (I_2MIN, "2 min"), "200 ms limit (1.2 x 216 A)": (I_200MS, "200 ms")}
    tier_kva = {k: {"I_A": i, "duration": d_, "kVA": {f"{v:.0f}": round(math.sqrt(3) * v * i / 1e3, 1) for v in V_AC_MAP}}
                for k, (i, d_) in tiers.items()}
    zb = V_LL ** 2 / P_RATED
    lg5 = zb / 5 / (2 * math.pi * F_GRID)
    rect = {f"{v:.0f} V AC, {vdc:.0f} V DC, {g}": dict(zip(("I_peak_A", "I_dc_mean_A"), rectifier(v, vdc, l_tot + lg)))
            for v in (460.0, 440.0) for vdc in (590.0, 620.0) for g, lg in (("stiff grid", 0.0), ("SCR 5", lg5))}
    res = {"basis": ("CALCULATED steady state: V_c = V_g + (R + j w L) I, L = L1 + L2 = %.0f uH, R = %.1f mOhm per phase (one switch of "
                     "six devices at 150 C + the L1 / L2 copper models); modulation limit %.4f (SVPWM linear limit x 0.98) less %.3f "
                     "dead-time compensation (2 x %.0f ns x %.0f kHz, ASSUMED: top of the drawn hardware dead time) and a %.0f %% "
                     "current-loop headroom (ASSUMED); grid impedance and harmonics outside L2 not counted; P at the AC terminals"
                     % (l_tot * 1e6, r_ph * 1e3, M_LIN, 2 * T_DEAD_MAX * FSW_CHOICE, T_DEAD_MAX * 1e9, FSW_CHOICE / 1e3, K_DYN * 100)),
           "R_ohm_per_phase": r_ph, "L_H": l_tot, "m_usable": m_ok, "vdc_min_at_rated_current_V": vmin, "admissible": adm,
           "overload_tiers": tier_kva, "S_max_kVA": S_MAX / 1e3, "rectification": rect, "t_ac_open_s": T_AC_OPEN,
           "rated_power_at_400V_A": P_RATED / (math.sqrt(3) * V_LL), "S_max_at_400V_A": S_MAX / (math.sqrt(3) * V_LL)}
    SPEC["operating_map"] = res
    return res


def report_map(r):
    wr("\n### Operating map (PCM-05)\n")
    vm = r["vdc_min_at_rated_current_V"]
    v400, v460 = vm["400 V 50 Hz"], vm["460 V 50 Hz"]
    wr(f"**Basis:** {r['basis']}.  The rectangular promise 'DC 590-950 V, AC 400 V +-15 %, rated current at any power factor' does "
       f"not hold: the bridge needs a minimum DC voltage that grows with the AC voltage and with reactive output.  Even at the nominal "
       f"400 V it needs {v400['closing permissive (no load, synchronised close)']:.0f} V DC to connect and "
       f"{min(v400[k] for k in PF_CASES):.0f}-{max(v400[k] for k in PF_CASES):.0f} V for rated current, so the bottom of the DC range "
       f"(590-{v400['closing permissive (no load, synchronised close)'] - 1:.0f} V) serves only grids up to about 360-380 V; rated current at "
       f"any power factor up to 460 V needs {max(v460[k] for k in PF_CASES):.0f} V - AC-02's 'full load 600-900 V' at 400 V +-15 % is not "
       f"reachable with these margins (a requirement decision, not a design fix).  125 kW at "
       f"400 V is {r['rated_power_at_400V_A']:.1f} A (rated 180 A), 150 kVA at 400 V is {r['S_max_at_400V_A']:.1f} A = the 2-min tier, "
       f"not the 198 A continuous one.\n")
    keys = list(PF_CASES)
    wr("| AC (rated 180 A) | " + " | ".join(keys) + " | diodes conduct below | closing permissive |")
    wr("|---|" + "---|" * (len(keys) + 2))
    for k, v in r["vdc_min_at_rated_current_V"].items():
        wr(f"| {k} | " + " | ".join(f"{v[x]:.0f} V" for x in keys) + f" | {v['diode conduction (sqrt(2) V_LL)']:.0f} V | "
           f"{v['closing permissive (no load, synchronised close)']:.0f} V |")
    wr("\n**Admissible current (A rms, 50 Hz; capped at 198 A, 150 kVA and the DC limits 137 kW / 230 A):** PF 1 inverter / PF 0 Q > 0 / "
       "PF 0 Q < 0 (0 = cannot form the grid voltage at all)\n")
    wr("| V_dc | " + " | ".join(f"{v:.0f} V AC" for v in V_AC_MAP) + " |")
    wr("|---|" + "---|" * len(V_AC_MAP))
    for vdc, row in r["admissible"].items():
        wr(f"| {vdc} V | " + " | ".join("%.0f / %.0f / %.0f" % tuple(row[f"{v:.0f}"]["50 Hz"][k]["I_A"] for k in (keys[0], keys[2], keys[3]))
                                        for v in V_AC_MAP) + " |")
    wr("\n| overload tier | A rms | " + " | ".join(f"kVA at {v:.0f} V" for v in V_AC_MAP) + " |")
    wr("|---|---|" + "---|" * len(V_AC_MAP))
    for k, t in r["overload_tiers"].items():
        wr(f"| {k} | {t['I_A']:.0f} | " + " | ".join(f"{t['kVA'][f'{v:.0f}']:.1f}" for v in V_AC_MAP) + " |")
    wr(f"\nS is further capped at {r['S_max_kVA']:.0f} kVA (AC-02).  **Below the rectification threshold** (V_dc < sqrt(2) V_LL, gates off or "
       f"the modulation saturated) the body diodes rectify into the battery through L1 + L2 (+ grid): "
       + "; ".join(f"{k}: {x['I_peak_A']:.0f} A peak, {x['I_dc_mean_A']:.0f} A mean DC" for k, x in r["rectification"].items())
       + f" (time-stepped diode bridge; not modelled: R, which lowers these currents, and L1 saturation in the stiff-grid case, which "
         f"raises them).  So the firmware "
         f"stops in a controlled way while it still has margin (V_dc < 1.05 x "
         f"sqrt(2) V_LL: current to zero, both AC contactors open at zero current); if V_dc collapses faster, it trips and opens both AC "
         f"contactors (they break the pulsed current at its zeros) and this current flows for their release, {r['t_ac_open_s'] * 1e3:.0f} ms "
         f"ASSUMED - above 387-413 A DC the port's hardware over-current window also opens the DC contactor.  Every V_dc figure above "
         f"carries the {K_DYN * 100:.0f} % loop headroom: without it they are {K_DYN / (1 + K_DYN) * 100:.1f} % lower.")


PWR_CHECK = os.path.join(ROOT, "hardware", "%s", "outputs", "%s_design_check.txt")   # gen/pcs_power.py design checks (read only)
# the AC coil module CONTRACT (RFQ) - review R2-06 (2026-10-07): ONE pair, used by the live 24 V budget (gen/pcs_power.py reads it from
# pcs_spec ports_and_common_mode.ac_contactor.coil_contract), the RFQ text and the self-check.  The 24 W / 5.5 W the aux rating would
# allow (D-076) is printed as the margin to this contract, not as the contract: with it the four-wire build would keep 0.3 W peak /
# 0.8 W continuous reserve
AC_COIL_CONTRACT = {"pull_W": 20.0, "t_pull_s": 0.1, "hold_W": 4.0}
AC_PULL_STEPS = {"relay test, AC contactor 1 alone": 0, "relay test, AC contactor 2 alone": 0, "synchronised close": 1,
                 "grid start: AC contactor 2 pulls in": 0, "grid start: AC contactor 1 pulls in": 1}    # step -> AC coils held meanwhile


def aux_budget():
    """Live 24 V / SELV budget of both builds against the 75 W aux block's one rating (aux75_spec 'ratings', its full set), with the
    loads the PCS power boards compute from their drawn parts (stacked maxima; gen/pcs_power.py design checks, read only, fail
    closed) and the start-up sequence of the firmware limits (one coil pulls in at a time: K_PRE, K_B, K_A, K_AC2, K_AC1; the DC
    contactor pulls in with the gates idle).  AC coil module allowance (RFQ): hold per contactor from the continuous rating with both
    AC coils held; pull-in per contactor from the 1 s peak at the worst AC pull-in step, the other AC coil held at that hold.
    CALCULATED (stacked maxima of data-sheet loads, the AC coil module ASSUMED as in the boards' checks)."""
    s75 = json.load(open(AUX75))
    rt = next(v for k, v in s75["ratings"].items() if k.startswith("4 phases"))     # one design: its full set covers every build
    out = {"rating": dict(rt, rev=s75["rev"]), "builds": {}}
    for build, board in (("three-wire", "PCS-PWR"), ("four-wire", "PCS-PWR-4W")):
        t = open(PWR_CHECK % (board, board)).read()
        ml = re.search(r"Live 24 V from the aux: 5 V outputs ([\d.]+) W / 0\.85 \+ DC contactor on its economiser ([\d.]+) W \+ 2 AC "
                       r"contactor coils \(RFQ hold <= ([\d.]+) W each\) = ([\d.]+) W", t)
        ms = re.search(r"SELV: \d+ fans \+ fan buck \+ logic ([\d.]+) W", t)
        mc = re.search(r"AC coil module RFQ, contract pull-in ([\d.]+) W for <= ([\d.]+) ms, hold ([\d.]+) W", t)
        mq = re.search(r"gate bias idle [\d.]+ W per phase\): (.*?)\. Live peak rating", t)
        if not (ml and ms and mc and mq):
            raise SystemExit(f"{board} design check: the aux budget lines are missing - build the board (gen/pcs_power.py) first")
        p_live, p_selv = float(ml.group(4)), float(ms.group(1))
        pull_a, t_pull, hold_a = float(mc.group(1)), float(mc.group(2)) * 1e-3, float(mc.group(3))
        steps = [(s.rsplit(" ", 2)[0], float(s.rsplit(" ", 2)[1])) for s in mq.group(1).split("; ")]
        ac = {k: [w for n, w in steps if n.startswith(k)] for k in AC_PULL_STEPS}
        assert all(len(v) == 1 for v in ac.values()), (board, "start-up steps changed", ac)
        worst = max(steps, key=lambda s: s[1])
        hold_room = (rt["live_W"] - (p_live - 2 * hold_a)) / 2

        def pull_room(hold, ac=ac):
            return rt["live_peak_W"] - max(ac[k][0] - pull_a + n * (hold - hold_a) for k, n in AC_PULL_STEPS.items())
        out["builds"][build] = {
            "board": board, "live_W": p_live, "selv_W": p_selv, "total_W": p_live + p_selv, "steps": steps,
            "peak_W": worst[1], "peak_step": worst[0], "coil_assumed": {"pull_W": pull_a, "t_pull_s": t_pull, "hold_W": hold_a},
            "coil_is_contract": abs(pull_a - AC_COIL_CONTRACT["pull_W"]) < 0.05 and abs(t_pull - AC_COIL_CONTRACT["t_pull_s"]) < 1e-4
                                and abs(hold_a - AC_COIL_CONTRACT["hold_W"]) < 0.05,
            "margin_live_W": rt["live_W"] - p_live, "margin_selv_W": rt["selv_W"] - p_selv,
            "margin_total_W": rt["total_W"] - p_live - p_selv, "margin_peak_W": rt["live_peak_W"] - worst[1],
            "hold_room_W": hold_room, "pull_room_W_at_assumed_hold": pull_room(hold_a), "_pull_room": pull_room}
    hold_req = min(math.floor(2 * b["hold_room_W"]) / 2 for b in out["builds"].values())
    pull_req = min(math.floor(b["_pull_room"](hold_req)) for b in out["builds"].values())
    for b in out["builds"].values():
        b.pop("_pull_room")
    cc = AC_COIL_CONTRACT
    fits = cc["pull_W"] <= pull_req and cc["hold_W"] <= hold_req
    out.update(pull_req_W=pull_req, hold_req_W=hold_req, estimate_fits=fits, coil_contract=dict(cc),
               margin_to_contract_W={"pull_W": pull_req - cc["pull_W"], "hold_W": hold_req - cc["hold_W"]},
               rfq=("24 V DC electronic coil (economiser inside the coil module), per contactor: pull-in <= %.0f W for <= %.0f ms, hold "
                    "<= %.0f W (the contract, pcs_spec ac_contactor coil_contract; one coil pulls in at a time)" % (
                        cc["pull_W"], cc["t_pull_s"] * 1e3, cc["hold_W"])),
               margin_text=("margin to the contract (calculated, not the contract): against the aux rating (aux75_spec rev %s: %.0f W "
                            "continuous, %.0f W for 1 s) the live 24 V budget of both builds would allow up to %.0f W pull-in / %.1f W hold "
                            "per contactor, i.e. %.0f W / %.1f W above the contract" % (
                                s75["rev"], rt["live_W"], rt["live_peak_W"], pull_req, hold_req, pull_req - cc["pull_W"],
                                hold_req - cc["hold_W"])))
    return out


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
    c_eq = rd["C_total_uF"] * 1e-6                  # bank + leg films (PCM-03); the aux input bulk charges from the battery terminal tap
    r_pre, r_tol = 220.0, 0.05
    c_max = c_eq * (1 + C_TOL)
    tau_max = r_pre * (1 + r_tol) * c_max
    ab = aux_budget()
    res["aux_budget"] = ab
    res["ac_contactor"] = {"arrangement": "two 3-pole contactors in series (4-pole for four-wire), relay test before every connection",
                           "Ie_AC1_A_min": 1.15 * I_2MIN, "Ui_V": 1000, "Uimp_kV": 8, "coil": ab["rfq"], "coil_contract": dict(AC_COIL_CONTRACT),
                           "coil_margin_to_contract": ab["margin_text"],
                           "candidates": "CHINT NXC-225 / CJX2-185, Delixi CJX2s-185 class - no data sheet on file (R-06, RFQ)"}
    res["precharge"] = {"R_ohm": r_pre, "C_eq_uF": c_eq * 1e6, "C_eq_max_uF": c_max * 1e6, "tau_s": r_pre * c_eq, "tau_max_s": tau_max,
                        "t_to_10V_s": r_pre * c_eq * math.log(950 / 10), "t_to_10V_max_s": tau_max * math.log(V_OV_TRIP / 10),
                        "E_J_950V": 0.5 * c_eq * 950 ** 2, "E_J_max": 0.5 * c_max * V_OV_TRIP ** 2,
                        "E_short_J": V_OV_TRIP ** 2 / (r_pre * (1 - r_tol)) * (1.1 * tau_max + 0.03), "t_short_s": 1.1 * tau_max + 0.03,
                        "I_pk_A": 950 / r_pre,
                        "basis": ("charge from 0 to the 1050 V trip with C +10 % and R +5 % (tau_max); shorted bank: 1050 V on R -5 % until "
                                  "the abort at 1.1 tau_max + 30 ms relay release (the lean port's convention, port_spec lean)")}
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
    ab = r["aux_budget"]
    rt = ab["rating"]
    wr(f"\n**Auxiliary supply budget (CALCULATED, stacked maxima from the PCS power boards' design checks; aux75_spec rev {rt['rev']}, one "
       f"design: live {rt['live_W']:.0f} W continuous / {rt['live_peak_W']:.0f} W for 1 s, SELV {rt['selv_W']:.1f} W, total "
       f"{rt['total_W']:.1f} W):**\n")
    wr("| build | live 24 V W (margin) | SELV W (margin) | total W (margin) | pull-in peak W (margin) at the worst step |")
    wr("|---|---|---|---|---|")
    for k, b in ab["builds"].items():
        wr(f"| {k} ({b['board']}) | {b['live_W']:.1f} ({b['margin_live_W']:.1f}) | {b['selv_W']:.1f} ({b['margin_selv_W']:.1f}) | "
           f"{b['total_W']:.1f} ({b['margin_total_W']:.1f}) | {b['peak_W']:.1f} ({b['margin_peak_W']:.1f}) - {b['peak_step']} |")
    for k, b in ab["builds"].items():
        wr(f"\nSequence, {k} (one coil pulls in at a time; AC coil at the contract {b['coil_assumed']['pull_W']:.0f} W for "
           f"{b['coil_assumed']['t_pull_s']*1e3:.0f} ms / {b['coil_assumed']['hold_W']:.0f} W hold): " +
           "; ".join(f"{n} {w:.1f} W" for n, w in b["steps"]) + ".")
    wr(f"\nAC contactor coil (RFQ, review R2-06: one contract for the budget, the RFQ and the self-check): {ab['rfq']}; {ab['margin_text']}.")
    pc = r["precharge"]
    wr(f"\n**Precharge and start:** from the DC side through the lean port's relay and {pc['R_ohm']:.0f} ohm: C_eq {pc['C_eq_uF']:.1f} uF "
       f"(bank + leg films), tau {pc['tau_s']*1e3:.0f} ms, within 10 V of 950 V after {pc['t_to_10V_s']:.2f} s, {pc['E_J_950V']:.0f} J, "
       f"{pc['I_pk_A']:.1f} A peak; worst case {pc['C_eq_max_uF']:.0f} uF from the 1050 V trip: tau {pc['tau_max_s']*1e3:.0f} ms, "
       f"{pc['t_to_10V_max_s']:.2f} s, {pc['E_J_max']:.0f} J; shorted bank {pc['E_short_J']:.0f} J in {pc['t_short_s']:.2f} s ({pc['basis']}).  "
       f"Then the inverter forms the grid voltage on C_f (current-limited), synchronises, runs the relay test, closes.  Grid start (battery "
       f"empty or disconnected) uses the architecture's six-diode tap for the auxiliary supply only.")
    wr("\n**Surge:** AC type II on the board (three thermally protected varistors L-PE; the four-wire build adds a fourth N-PE, the '4+0' "
       "arrangement as drawn - a 3+1 N-PE spark gap would halve the L-N level but has no data sheet on file; monitored); C_f takes the "
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
       f"leaves the upstream requirement on the battery's own breaker (R-05 closed on 2026-10-05: sim/port_design.py section 14 computes the coordination with the 400 A fuse and the HFE82V-300C - bands and installation requirements in the PCS-PWR design check, review PCM-15).")


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
    add = {k: full.get(k, 0) - hold.get(k, 0) for k in sorted(set(full) | set(hold)) if full.get(k, 0) > hold.get(k, 0)}
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
        ("SENSING", "Phase current sensor, open-loop Hall +-625 A, 200 kHz, busbar through the aperture (C_f side of L1)", 3, "STK-250HO/4",
         "Sinomags (CN)", 6.0, 4.8, "ESTIMATE: no LCSC listing; LEM HO 250-S class 13.70 USD at 500 (Digi-Key) x 0.44 (gen/data/cost_estimates.csv)",
         "ASSUMED x0.80", False),
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
        ("AUX SUPPLY", "75 W flyback (PV AUX) on the DC link and the DC terminal", 1, "AUX block (PV)", "TI / InventChip / CUSTOM",
         34.3, 28.6, "PV costfirst_bom (its 3.0 USD AC-tap estimate moved to the next row)", "MIXED: TI 1ku list, rest ASSUMED", False),
        ("AUX SUPPLY", "AC start-up path (step i, drawn on PCS-PWR): 6-diode grid tap behind 3 x 20 ohm + 3 x 1.6 A / 500 V AC fuses into the aux "
         "(diode OR) and the AC precharge (G7L-2A-X + 220 ohm from the tap's + rail to DC+, latch-gated driver)", 1,
         "3 x ABB-A 1.60 + 3 x AC10 20R + 7 x BYG10Y + G7L-2A-X + RPRE_AL + driver", "Conquer / Vishay / Omron / RFQ",
         sum(q * c for q, c, _ in AC_START_COST.values()), sum(q * c for q, _, c in AC_START_COST.values()),
         "prices.csv (BYG10Y; the fuse at its 2 A sibling ABB-A 002's breaks) and cost_estimates.csv rows (AC10, G7L-2A-X DC24, 220R RFQ)",
         "BYG10Y REAL breaks, the 1.6 A fuse ESTIMATED from the 2 A REAL breaks, rest ASSUMED x0.80-0.85",
         False),
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
        ("4-WIRE OPTION", f"Neutral inductor L_N {l1*1e6:.0f} uH (as L1: the neutral leg's ripple is that of a phase leg) + STK-250HO/4", 1,
         "L1 class + STK-250HO/4", "CUSTOM + Sinomags", (FP["L1"]["usd"][0] if FP else L1_COST[0] + L1_COST[1] * e1) + 6.0,
         (FP["L1"]["usd"][1] if FP else (L1_COST[0] + L1_COST[1] * e1) * 0.8) + 4.8, "rows above", "rows above", False),
        ("4-WIRE OPTION", "Heatsink section + fan for the fourth leg", 1, "as THERMAL rows", "CUSTOM + Delta",
         4.0 * 4.7 + 5.0 + HS["fans_per_section"] * FAN["price"]["cat"], (4.0 * 4.7 + 5.0) * 0.8 + HS["fans_per_section"] * FAN["price"]["5k"],
         "rows above", "rows above", False),
        ("4-WIRE OPTION", "4-pole instead of 3-pole AC contactors, N terminal and busbar", 1, "class", "class", 35.0, 28.0, "ESTIMATE (architect)",
         "ASSUMED x0.80", False),
        ("4-WIRE OPTION", "Neutral filter node (step i): C_fN = 2 x 25 uF 450 V AC MKP + R_d 2 ohm / C_d 10 uF to the DC midpoint (the N leg's ripple "
         "returns locally; the C_f star stays on M)", 1, "2 x 25 uF MKP + RX24 2R + 10 uF MKP", "class", 2 * 3.0 + 3.0, (2 * 3.0 + 3.0) * 0.85,
         "ESTIMATE (C_f and damping rows above)", "ASSUMED x0.85", False),
        ("4-WIRE OPTION", "N-PE surge (TVT25751 + Y1 4.7 nF), VGN divider + buffer, 4-conductor RCM aperture and 4-bar CM choke assembly (same "
         "cores)", 1, "TVT25751 + VY1 + 7 x ARHV + OPA2388", "Thinking / Vishay / Viking / TI", 1.2 + 0.5 + 6 * 0.25 + 1.5 + 1.0,
         (1.2 + 0.5 + 6 * 0.25 + 1.5 + 1.0) * 0.85, "ESTIMATE (rows above; RCM / CM-choke assembly +1 USD)", "ASSUMED x0.85", False),
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
       "move to +-450 A on the Sinomags STK-250HO/4 (open-loop Hall, +-625 A, 3.2 mV/A, step response <= 2 us, PCM-20): the ladder values "
       "change, and the trip path (sensor <= 2 us + comparator + latch + driver, timed against the L1 limit in the PCS-CTL design check) "
       f"runs at di/dt = V_dc / L1(I) = {SPEC['protection_chain']['fault_slope']['di_dt_at_window_top_A_per_us']:.2f} A/us on the L1 "
       "envelope (1050 V; 8.75 A/us at a constant 120 uH; the gates-off current and its turn-off peak: protection chain, step e) - with "
       "the PV's 73 A / 224 uH it was 4.5 A/us; "
       "(2) the dead-time stretch needs a new gen/gdrv.py preset for six gates behind a buffer "
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
    if not FP:                          # --bootstrap run only (run() refuses a missing tradeoff.json otherwise)
        wr("Not run yet: the filter of steps c and g is priced with the step-c loss budgets and the architect's estimates.  Run "
           "sim/pcs_tradeoff.py, then this script again.")
        return
    t = json.load(open(TRADEOFF))
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
        if isinstance(o, (np.floating, np.integer, np.bool_)):
            return o.item()
        if isinstance(o, float) and not math.isfinite(o):
            return None
        return o
    with open(os.path.join(OUT, "pcs_spec.json"), "w") as f:
        json.dump(clean(SPEC), f, indent=1)
    with open(os.path.join(OUT, "report.md"), "w") as f:
        f.write("\n".join(REP) + "\n")


# ------------------------------------------------------------------------------------------------ (i) four-wire build, AC start, declarations
# Owner (2026-10-06): whatever the competitor's PMA0125 has, ours has too (Megarevo 2026 catalogue PDF p30-31, the PMA datasheet V1.0 and
# its user manual: docs/reference-designs/megarevo/pma/spec-pma0125.md, MANUAL-NOTES.md - the competitor's published claims, not verified
# by us).  Everything below is CALCULATED from this study's own models; ASSUMED values are labelled where they are set.
PMA = {"window_3W": (590.0, 950.0, 600.0, 900.0), "window_4W": (650.0, 950.0, 680.0, 900.0), "f_window_datasheet_Hz": 5.0,
       "f_window_manual_Hz": 2.0, "cfm_per_kW": 3.8,
       "breakers": "DC 1000 V DC 200 A (50 / 62.5 kW) or 300 A (80 / 105 kW); AC 400 V AC 200 A or 250 A; no prospective short-circuit "
                   "current, fuse type, earthing system or RCD type stated (user manual pp. 25, 28)",
       "offgrid_loads": "resistive below rated power, RCD-type loads below 60 % of the module kVA, VFD motors below 60 % of one module, "
                        "direct-on-line motors 'ask the maker' (user manual p. 11)",
       "parallel": "up to 14 modules, 4-bit DIP address, host and last module terminated, a module CAN plus three synchronisation pairs "
                   "(carrier and low-frequency sync, needed on a shared battery), sharing method unpublished (user manual pp. 13, 17-21)",
       "transfer": "on / off-grid change by hand (stop, change the mode, start); '< 20 ms' is the charge <-> discharge switching time only "
                   "(user manual pp. 11, 40, 43)",
       "neutral": "3W+N+PE = the DC-link midpoint wired to the N terminal (split-capacitor neutral, no contactor or fuse; user manual fig. "
                  "3-8, pp. 10, 12, 15); its 650 V minimum is 2 x 230 V x sqrt 2 = 650.5 V",
       "modes": "on-grid charge / discharge in CV, CC and CP, standby, off-grid constant V/f (user manual pp. 10-11, 30-31); generator mode, "
                "LVRT / HVRT, anti-islanding, per-phase control and droop are datasheet / catalogue claims without published parameters"}
M_LIN_4W = 0.98             # mode A: sinusoidal phase legs against an N leg held at 50 % duty: |v_x - v_N| <= V_dc / 2 (2 % kept for min pulses)
LN_CASES = (("-15 %", 0.85), ("nominal", 1.0), ("+10 %", 1.10), ("+15 %", 1.15))
MID_RIPPLE_FRAC = 0.05      # ASSUMED (coordinator brief): a split-capacitor neutral's 50 Hz midpoint ripple allowance, 5 % of V_dc / 2
T_SWING_K = 30.0            # ASSUMED: board temperature change between the cold-start re-zero of the DC channels and operation
T_ISLAND_DETECT = 0.10      # s, ASSUMED islanding detection (U / f windows, ROCOF, vector shift - not simulated in the control study)
T_AC_PULL = 0.10            # s, ASSUMED AC contactor pull-in (RFQ coil module, as the PCS-PWR start-up budget)
# AC start-up tap (drawn on PCS-PWR, both builds).  Conquer ABB/ABB-A catalogue p81 (PDF page 30) (docs/datasheets/protection/Conquer-Axial-Lead-Cartridge-
# Fuse.pdf, PDF p30): 6.3 x 32 mm super fast-acting, 500 V AC / DC, 200 A interrupting at 500 V AC / DC (1-20 A), ABB-A = axial leads;
# ABB-A 1.60 = 1.6 A, cold 0.1149 ohm, melting I2t 2.3454 A2s, opening <= 120 s at 1.35 In and <= 0.2 s at 2.5 In.  Vishay AC10 20 R
# (aux75_spec R_LIM: pulse energy ~2.6 Ws/ohm read off its p6).  Vishay BYG10Y (doc 88957 p1-2): 1600 V avalanche, 1.5 A, I_FSM 30 A.
TAP = {"fuse": "ABB-A 1.60", "fuse_In": 1.6, "fuse_i2t_melt": 2.3454, "fuse_R": 0.1149, "fuse_break_A": 200.0, "fuse_V_ac": 500.0,
       "r_lim": 20.0, "r_tol": 0.05, "r_pulse_J": 2.6 * 20.0, "r_W": 10.0, "d_vrrm": 1600.0, "d_ifsm": 30.0, "d_if": 1.5, "d_vf": 1.1,
       "r_pre": 220.0, "pulse_rule": 0.20}
AC_START_COST = {"ABB-A 1.60": (3, 1.47, 1.15), "AC10000002009JAB00": (3, 0.93, 0.93 * 0.85), "BYG10Y-E3/TR": (7, 0.084, 0.0711),
                 "G7L-2A-X DC24": (1, 12.71, 12.71 * 0.85), "220R RFQ": (1, 4.0, 3.2), "coil driver": (1, 0.30, 0.25)}
FW_I = []                   # firmware rows of step i, appended to the hand-over's firmware_limits (gen/pcs_ctrl.py prints them)


def fw_window(om):
    """four-wire DC window two ways, with the operating map's own margins (5 % loop headroom, dead-time compensation, 2 % minimum pulse):
    mode A - the N leg held at 50 % duty, the phase peak <= V_dc / 2 (the competitor's split-capacitor neutral has the same limit);
    mode B - the min-max zero sequence on all four legs (3D-SVM-equivalent: the N leg moves with the phases' common-mode offset), so the
    phase-to-N and line-to-line peaks fit the three-wire utilisation"""
    r_ph, l_tot = om["R_ohm_per_phase"], om["L_H"]
    m_a, m_b = M_LIN_4W - 2 * T_DEAD_MAX * FSW_CHOICE, om["m_usable"]
    z = complex(r_ph, 2 * math.pi * F_GRID * l_tot)
    rot = lambda ang: complex(math.cos(math.radians(-ang)), math.sin(math.radians(-ang)))    # noqa: E731 (op_point convention)
    need = lambda v, i, ang, m: 2 * math.sqrt(2) * abs(v + z * i * rot(ang)) * (1 + K_DYN) / m  # noqa: E731
    v0 = V_LL / math.sqrt(3.0)
    rows = {}
    for name, k in LN_CASES:
        v = v0 * k
        a = {pf: need(v, I_RATED, ang, m_a) for pf, ang in PF_CASES.items()}
        a["closing (no load)"] = max(need(v, 0.0, 0.0, m_a), 2 * math.sqrt(2) * v * 1.02 + 10.0)
        b = {pf: need(v, I_RATED, ang, m_b) for pf, ang in PF_CASES.items()}
        b["closing (no load)"] = max(need(v, 0.0, 0.0, m_b), math.sqrt(6.0) * v * 1.02 + 10.0)
        rows[name] = {"V_LN": v, "bare_2sqrt2_V_LN": 2 * math.sqrt(2) * v, "mode_A": a, "mode_B": b}
    full = lambda d: max(d[pf] for pf in PF_CASES)                           # noqa: E731
    reach = lambda vdc, m: vdc * m / (2 * math.sqrt(2) * (1 + K_DYN))      # noqa: E731  highest L-N rms formed at no load
    v400 = om["vdc_min_at_rated_current_V"]["400 V 50 Hz"]
    n = rows["nominal"]
    win = {"3W+PE": {"competitor": PMA["window_3W"], "operating_from": v400["closing permissive (no load, synchronised close)"],
                     "full_load_from": full(v400), "operating_to": 950.0, "full_load_to": 900.0},
           "3W+N+PE, mode A (N leg at 50 %)": {"competitor": PMA["window_4W"], "operating_from": n["mode_A"]["closing (no load)"],
                                               "full_load_from": full(n["mode_A"]), "operating_to": 950.0, "full_load_to": 900.0,
                                               "no_headroom_from": n["bare_2sqrt2_V_LN"]},
           "3W+N+PE, mode B (zero sequence on four legs)": {"competitor": PMA["window_4W"], "operating_from": n["mode_B"]["closing (no load)"],
                                                            "full_load_from": full(n["mode_B"]), "operating_to": 950.0, "full_load_to": 900.0}}
    return {"basis": ("CALCULATED with the operating map's model (pcs_spec operating_map: L = L1 + L2, R per phase, 5 %% loop headroom, "
                      "dead-time compensation 2 x %.0f ns x %.0f kHz, 2 %% minimum pulse), rated current 180 A, 50 Hz; mode A usable modulation "
                      "%.4f (sinusoidal, N leg at 50 %%), mode B %.4f (min-max zero sequence on all four legs = the three-wire figure)"
                      % (T_DEAD_MAX * 1e9, FSW_CHOICE / 1e3, m_a, m_b)),
            "m_usable_mode_A": m_a, "m_usable_mode_B": m_b, "rows": rows, "windows_at_400_230V": win,
            "mode_A_reach_V_LN": {"650 V": reach(650.0, m_a), "680 V": reach(680.0, m_a)},
            "baseline": ("firmware baseline = the three-wire policy carried to four legs: mode A (N leg at 50 %%, sinusoidal phase legs: no 150 Hz "
                         "common-mode voltage between the DC side and N / earth) wherever the phase peak fits V_dc / 2 with the map's headroom "
                         "(from %.0f V DC at 230 V), mode B (the min-max zero sequence on all four legs) below it, down to the three-wire window "
                         "(%.0f V to connect, %.0f V for rated current at 400 / 230 V) - the four-wire build's advantage over a split-capacitor "
                         "neutral, which stops at 2 sqrt2 V_LN; in mode B the DC side carries the 150 Hz zero sequence to N / earth as the "
                         "three-wire build does (step f installation limit on the battery's capacitance to earth)"
                         % (n["mode_A"]["closing (no load)"], n["mode_B"]["closing (no load)"], full(n["mode_B"])))}


def midpoint_neutral(rd, inc):
    """why not the competitor's split-capacitor neutral for us: the neutral current flows into the DC midpoint and charges the two halves
    in parallel (the battery holds DC+ / DC- at 50 Hz); the film bank's 2 x C_half cannot hold it"""
    c_mid = 2 * rd["C_half_uF"] * 1e-6
    dv = {k: i * math.sqrt(2) / (2 * math.pi * F_GRID * c_mid) for k, i in (("rated 180 A", I_RATED), ("2-min 216 A", I_2MIN))}
    vdc = 750.0
    lim = MID_RIPPLE_FRAC * vdc / 2
    c_need = I_RATED * math.sqrt(2) / (2 * math.pi * F_GRID * lim)
    s_c = ELCO["C"] / 2                                       # two 400 V cans in series per half (475 V at 950 V)
    n_c = math.ceil(c_need / 2 / s_c)
    n_i = math.ceil(I_RATED / 2 / (0.8 * ELCO["i_120"]))     # 50 Hz ripple: I_N / 2 per half; 50 Hz rating 0.8 x the 120 Hz one (ASSUMED)
    n_s = max(n_c, n_i)
    cans = 2 * 2 * n_s
    i_ok = 2 * math.pi * F_GRID * c_mid * (560.0 - vdc / 2) / math.sqrt(2)
    return {"C_mid_uF": c_mid * 1e6, "dV_peak_V": dv, "V_half_nominal_750V": vdc / 2, "half_OV_trip_V": 560.0,
            "I_N_rms_at_half_trip_A": i_ok, "limit_V": lim, "C_needed_mF": c_need * 1e3, "C_needed_per_half_mF": c_need / 2 * 1e3,
            "electrolytic": {"part": ELCO["part"], "strings_per_half": n_s, "by_capacitance": n_c, "by_ripple_current": n_i, "cans": cans,
                             "usd_cat": cans * ELCO["price"]["cat"], "usd_5k": cans * ELCO["price"]["5k"],
                             "life_h_105C": ELCO["life_105"]},
            "fourth_leg_increment_usd": inc}


def n_leg(D, vdc, i_rms, t_in):
    """the N leg in mode A (reference = the j w L_N i_N drop, N_F held at M): losses and junction temperature on its own heatsink section"""
    m = 2 * math.pi * F_GRID * D["l1"] * i_rms * math.sqrt(2) / (vdc / 2)
    hs = D.get("hs_r") or section_rth()
    tj = {p: (t_in + 40.0, t_in + 40.0) for p in TOPO[D["topo"]]["pos"]}
    for _ in range(40):
        res, tot = leg_losses(D["topo"], D["devs"], FSW_CHOICE, vdc, m, math.pi / 2, i_rms, "none", tj, l1=D["l1"])
        t_hs = t_in + tot * hs["r_sa"]
        new = {p: device_tj(D["devs"][p][0], res[p]["sw"], res[p]["d"], t_hs) for p in tj}
        done = max(abs(new[p][k] - tj[p][k]) for p in new for k in (0, 1)) < 0.2
        tj = new
        if done:
            break
    return {"m": m, "leg_W": tot, "t_hs_C": t_hs, "tj_max_C": max(max(v) for v in tj.values())}


def dc_currents_4w(vdc, amps, l_n, ang=0.0, n=2 ** 16):
    """four legs: phases a-c at amps[k] A rms (angle ang to their own L-N voltage, op_point convention), the N leg in mode A (reference =
    L_N di_N/dt, N_F held at M); bank currents as dc_currents (battery = smooth source; LF = harmonics 1-40, HF above).  The LF part goes
    to the battery in practice (the bank is 3.9 ohm at 100 Hz).  Returns per-half LF / HF rms, the neutral rms and the battery's LF rms"""
    om = SPEC["operating_map"]
    l_tot, w = om["L_H"], 2 * math.pi * F_GRID
    t = np.arange(n) / n / F_GRID
    th = w * t
    tri = np.abs(2.0 * ((t * FSW_CHOICE) % 1.0) - 1.0)
    v_ln = V_LL / math.sqrt(3.0)
    refs, cur, vg = [], [], []
    for k, a in enumerate(amps):
        ph = -k * 2 * math.pi / 3
        ic = a * complex(math.cos(math.radians(-ang)), math.sin(math.radians(-ang)))
        vc = v_ln + 1j * w * l_tot * ic
        refs.append(abs(vc) * math.sqrt(2) / (vdc / 2) * np.sin(th + ph + np.angle(vc)))
        cur.append(abs(ic) * math.sqrt(2) * np.sin(th + ph + np.angle(ic)))
        vg.append(v_ln * math.sqrt(2) * np.sin(th + ph))
    i_n = -np.sum(cur, 0)
    refs.append(l_n * np.gradient(i_n, t) / (vdc / 2))
    cur.append(i_n)
    legs = np.where(np.array(refs) > 2 * tri - 1.0, 1.0, -1.0)
    cur = np.array(cur)
    i_p = np.sum(np.where(legs > 0, cur, 0.0), 0)
    i_m = np.sum(np.where(legs < 0, cur, 0.0), 0)
    idc = float(np.mean(i_p))

    def split(x):
        X = np.fft.rfft(x) / len(x)
        return math.sqrt(2 * np.sum(np.abs(X[1:41]) ** 2)), math.sqrt(2 * np.sum(np.abs(X[41:]) ** 2))
    (l1_, h1_), (l2_, h2_) = split(idc - i_p), split(idc + i_m)
    p = np.sum(np.array(vg) * cur[:3], 0)
    return {"vdc": vdc, "amps": list(amps), "ang": ang, "idc_A": idc, "lf_half_A": max(l1_, l2_), "hf_half_A": max(h1_, h2_),
            "I_N_rms_A": float(np.sqrt(np.mean(i_n ** 2))), "battery_lf_A_rms": float(np.std(p) / vdc)}


def tap_charge(c, v_ll, kc, kr, phi0=0.0, t_end=4.0, dt=1e-5):
    """AC precharge (drawn on PCS-PWR): the 6-pulse grid tap charges the DC link through R_pre and, in each conducting phase, its 20 ohm
    and its fuse; stiff grid, ideal diodes with V_F, the relay closes at t = 0, the aux's own draw neglected.  kc / kr: capacitance /
    resistance corner factors.  CALCULATED (time-stepped)"""
    rp, rl = TAP["r_pre"] * kr, TAP["r_lim"] * kr
    r_tot = rp + 2 * rl + 2 * TAP["fuse_R"]
    c *= kc
    w, vp = 2 * math.pi * F_GRID, math.sqrt(2.0 / 3.0) * v_ll
    v_end = math.sqrt(2) * v_ll - 2 * TAP["d_vf"]
    vc, e_pre, e_lim, i2t, ipk, t10, t = 0.0, 0.0, [0.0] * 3, [0.0] * 3, 0.0, None, 0.0
    while t < t_end and vc < v_end - 0.5:
        v = [vp * math.sin(w * t + phi0 - k * 2 * math.pi / 3) for k in range(3)]
        hi, lo = max(range(3), key=v.__getitem__), min(range(3), key=v.__getitem__)
        i = (v[hi] - v[lo] - 2 * TAP["d_vf"] - vc) / r_tot
        if i > 0.0:
            ipk = max(ipk, i)
            e_pre += i * i * rp * dt
            for k in (hi, lo):
                i2t[k] += i * i * dt
                e_lim[k] += i * i * rl * dt
            vc += i * dt / c
        if t10 is None and vc >= v_end - 10.0:
            t10 = t
        t += dt
    return {"t_to_10V_s": t10, "I_pk_A": ipk, "E_pre_J": e_pre, "E_lim_J": max(e_lim), "I2t_fuse_A2s": max(i2t), "V_end_V": v_end,
            "R_tot_ohm": r_tot, "C_uF": c * 1e6}


def ac_start(rd, c4):
    """AC-side start-up path of both builds: the 6-diode tap into the aux (diode OR) and the AC precharge from the tap's + rail to DC+"""
    cs = {"3W": rd["C_total_uF"] * 1e-6, "4W": c4 * 1e-6}
    out = {}
    for b, c in cs.items():
        rows = {}
        for v_ll in (V_LL * (1 - V_TOL), V_LL, V_LL * (1 + V_TOL)):
            hot = max((tap_charge(c, v_ll, 1 + C_TOL, 1 - TAP["r_tol"], phi0=p) for p in (0.0, math.pi / 6, math.pi / 3)),
                      key=lambda x: x["I_pk_A"])
            slow = tap_charge(c, v_ll, 1 + C_TOL, 1 + TAP["r_tol"])
            rows[f"{v_ll:.0f}"] = {"I_pk_A": hot["I_pk_A"], "E_pre_J": hot["E_pre_J"], "E_lim_J": hot["E_lim_J"], "I2t_fuse_A2s": hot["I2t_fuse_A2s"],
                                   "t_to_10V_s": slow["t_to_10V_s"], "V_end_V": hot["V_end_V"]}
        out[b] = rows
    v_hi = V_LL * (1 + V_TOL)
    r_min, r_max = (TAP["r_pre"] + 2 * TAP["r_lim"]) * (1 - TAP["r_tol"]), (TAP["r_pre"] + 2 * TAP["r_lim"]) * (1 + TAP["r_tol"])
    w = 2 * math.pi * F_GRID
    ts = np.linspace(0, 1 / F_GRID, 2000, endpoint=False)
    vr = np.max([math.sqrt(2.0 / 3.0) * v_hi * np.sin(w * ts - k * 2 * math.pi / 3) for k in range(3)], 0) - \
        np.min([math.sqrt(2.0 / 3.0) * v_hi * np.sin(w * ts - k * 2 * math.pi / 3) for k in range(3)], 0)
    v_rms = float(np.sqrt(np.mean(vr ** 2)))
    tau_max = r_max * cs["4W"] * (1 + C_TOL)
    t_abort = 1.1 * tau_max + 0.03
    p_short = v_rms ** 2 / r_min * TAP["r_pre"] / (TAP["r_pre"] + 2 * TAP["r_lim"])
    rr = port_rating()
    # the aux path: HV_BULK (aux75 C_BULK) charged through two 20 ohm when the grid appears; a shorted tap diode / bulk: L-L through two 20 ohm
    cb = json.load(open(AUX75))["values"]["C_BULK"]
    c_aux = cb["n"] * float(cb["value"].split("u")[0]) * 1e-6 * (1 + C_TOL)
    vpk = math.sqrt(2) * v_hi - 2 * TAP["d_vf"]
    r2 = 2 * TAP["r_lim"] * (1 - TAP["r_tol"]) + 2 * TAP["fuse_R"]
    i_fault = v_hi / r2                                       # rms of the L-L fault through two 20 ohm
    t_melt = TAP["fuse_i2t_melt"] / i_fault ** 2
    res = {"tap": {"fuse": TAP["fuse"], "fuse_In_A": TAP["fuse_In"], "fuse_i2t_melt_A2s": TAP["fuse_i2t_melt"], "fuse_break_A_500V": TAP["fuse_break_A"],
                   "R_lim_ohm": TAP["r_lim"], "diode": "BYG10Y-E3/TR 1600 V avalanche",
                   "rectified_V": {"%.0f V" % v: (math.sqrt(2) * v * math.cos(math.pi / 6), math.sqrt(2) * v)
                                   for v in (V_LL * (1 - V_TOL), V_LL * (1 + V_TOL))},
                   "aux_input_range_V": "the 75 W block's full rating from 250 V (aux75_spec ratings 'design': from_V 250), the brown-in below it",
                   "bulk_inrush": {"I_pk_A": vpk / r2, "I2t_A2s": vpk ** 2 * c_aux / (2 * r2), "E_per_R_J": 0.25 * c_aux * vpk ** 2,
                                   "C_bulk_uF": c_aux * 1e6},
                   "fault_L_L": {"I_rms_A": i_fault, "t_melt_s": t_melt, "E_per_R_J": i_fault ** 2 * TAP["r_lim"] * (1 - TAP["r_tol"]) * t_melt,
                                 "R_pulse_J": TAP["r_pulse_J"]},
                   "diode_reverse_max_V": 1144.0 + TAP["d_vf"],      # gen/port.py V_POLE (OV trip overshoot) + one V_F
                   "domain": ("LIVE: the tap ties DC- to the grid phases through its lower diodes (the converter does the same through its body "
                              "diodes once the AC contactors close); functional insulation inside the live domain, basic OVC III to PE (4 kV, 2.5 kV "
                              "with the AC SPD credit; clearance 3.0 / 3.5 / 3.9 mm at 2000 / 3000 / 4000 m - ARCHITECTURE-PCS section 3); the "
                              "reinforced barrier B1 on the control board is unchanged")},
           "precharge": {"by_build": out, "R_pre_ohm": TAP["r_pre"], "R_tot_ohm": (r_min, r_max), "tau_max_4W_s": tau_max, "t_abort_s": t_abort,
                         "P_pre_shorted_W": p_short, "E_pre_shorted_J": p_short * t_abort, "V_rect_rms_460V": v_rms,
                         "rating": {"e_charge_J": rr["e_charge_J"], "tau_s": rr["tau_s"], "e_short_J": rr["e_short_J"], "t_short_s": rr["t_short_s"]}}}
    return res
def port_rating():
    """the shared RFQ rating of the 220 ohm precharge resistor (RPRE_AL), read from gen/port.py RPRE_RATING (read only; fail closed)"""
    t = open(os.path.join(ROOT, "gen", "port.py"), encoding="utf-8").read()
    m = re.search(r"RPRE_RATING = dict\(([^)]*)\)", t)
    if not m:
        raise SystemExit("gen/port.py: RPRE_RATING not found - the AC precharge check needs the shared resistor rating")
    return {k.strip(): float(v) for k, v in (x.split("=") for x in m.group(1).split(","))}


def rpre_duty_c(c, v, r=220.0, tol=0.05):
    """gen/port.py rpre_duty for a bank of nominal capacitance c from v: (E J, tau_max s, shorted-bank E J, its duration s)"""
    tau = r * (1 + tol) * (1 + C_TOL) * c
    return 0.5 * (1 + C_TOL) * c * v ** 2, tau, v ** 2 / (r * (1 - tol)) * (1.1 * tau + 0.03), 1.1 * tau + 0.03


def ac_short(rc):
    """AC-side short-circuit declaration (CALCULATED): a bolted leg short fed from the grid through L2 + L1 (body diodes of the healthy
    legs, symmetrical three-phase into the shorted rails), unsaturated filter, then L1 saturation"""
    F = rc["filter"]
    pc_ = SPEC["protection_chain"]
    i_sat = SPEC["protection_chain"]["L1_trajectory"]["I_knee_50pct_A"]["hot +5 % part"]   # the trajectory's earliest hot knee (R2-03)
    zb = V_LL ** 2 / P_RATED
    rows = {}
    for name, scr in (("stiff", None), ("SCR 50", 50.0), ("SCR 20", 20.0), ("SCR 5", 5.0)):
        lg = 0.0 if scr is None else zb / scr / (2 * math.pi * F_GRID)
        lt = F["L1"] + F["L2"] + lg
        i_p = V_LL / math.sqrt(3) / (2 * math.pi * F_GRID * lt)
        rows[name] = {"L_g_uH": lg * 1e6, "I_prospective_unsaturated_A_rms": i_p, "I_peak_asymmetric_A": 2 * math.sqrt(2) * i_p,
                      "t_to_L1_saturation_ms": i_sat * lt / (V_LL * math.sqrt(2.0 / 3.0)) * 1e3,
                      "L1_saturates": 2 * math.sqrt(2) * i_p > i_sat}
    return {"I_L1_sat_100C_A": i_sat, "rows": rows,
            "module": ("converter running: the circular current limiter holds 367 A peak (259 A rms) for <= 200 ms, then trips (control study: "
                       "a terminal short in grid forming is held at 366 A with no hardware trip); a faster rise reaches the phase window "
                       "(%.0f-%.0f A, gates off <= %.2f us at <= %.0f A, pcs_spec protection_chain) and the CMPSS backup (%.0f-%.0f A as drawn; "
                       "requirement band top <= %.0f A, adopted on PCS-CTL, D-078); a device short is cleared by DESAT (detected <= 0.88 "
                       "us, off 0.40 us later); a shorted leg is fed from the battery through the two 400 A aR fuses (2.0-7.4 kA, port "
                       "coordination) and from the grid through the body diodes of the healthy legs - that grid-fed current is limited only "
                       "by L2 + L1 until L1 saturates, then by the installation" % (
                           *pc_["inputs"]["window_A"], pc_["local_window"]["response_us"], pc_["local_window"]["I_gates_off_A"],
                           *pc_["inputs"]["backup_A"], pc_["backup_requirement"]["band_top_max_A"])),
            "contactors": ("The two AC contactors in series are disconnecting devices, not short-circuit protective devices: AC-1 >= %.0f A (RFQ "
                           "class), making / breaking about 1.5 x Ie at cos phi 0.95 (IEC 60947-4-1 AC-1, from memory), opened by firmware at "
                           "zero current only; they must withstand the upstream device's let-through (conditional short-circuit current with "
                           "that device: an RFQ item)" % (1.15 * I_2MIN)),
            "installation": ("upstream AC protection rated >= 250 A (the 216 A 2-min tier must not trip it), breaking capacity >= the prospective "
                             "short-circuit current at the connection point, let-through within the module's AC-path withstand (contactor "
                             "conditional current, busbars, L2 / L1 - RFQ / test item); gG fuses 250 A or an MCCB with an instantaneous "
                             "release; residual-current protection upstream type B if the code requires an RCD (the module has its own RCM, "
                             "IEC 62109-2 style); earthing TN-S or TT (three-wire build in TN: step f limit on the battery's earth "
                             "capacitance); the DC side: the port declaration (battery-side protection 0.97-2.01 kA within 2.5 s, prospective "
                             "current <= 7.5 kA at the DC terminals or interrupted within the contactor's capacity)"),
            "competitor": PMA["breakers"]}


def precision():
    """stabilized-precision statement (CALCULATED from the drawn DC sensing chains; the ADC terms are the PCS-CTL design check's 'ADC
    accuracy' terms of gen/pv_ctrl.py: REF3030E 20 ppm/K box drift + 100 ppm hysteresis + 50 ppm long term, ADC gain residual 5 LSB (of
    full scale, proportional here) and INL 2 LSB; PV-PWR divider TCR mismatch 0.15 %).  (a) the board check's convention: the whole
    115 K span, no re-zero; (b) with the firmware re-zero of the DC channels at each cold start (link < 10 V, contactor open) and a
    T_SWING_K change since (ASSUMED)"""
    lsb, vm, k = 3.0 / 4096, 1.646, 1203.0
    ref = lambda span: 20e-6 * span + 150e-6               # noqa: E731
    v_out, i_out = {}, {}
    for conv, span in (("a: 115 K span, no re-zero", 115.0), ("b: re-zero at cold start, %.0f K swing (ASSUMED)" % T_SWING_K, T_SWING_K)):
        rv, ri = {}, {}
        for v in (600.0, 750.0, 950.0):
            vin = vm + v / k
            t = {"ADC reference": ref(span) * vin * k, "VMID drift (power board REF3030E)": ref(span) * vm * k,
                 "ADC gain residual 5 LSB": 5 * lsb * vin / 3.0 * k, "ADC INL 2 LSB": 2 * lsb * k, "divider TCR mismatch 0.15 %": 0.0015 * v}
            rv[f"{v:.0f}"] = {"terms_V": t, "worst_V": sum(t.values()), "rss_V": math.sqrt(sum(x * x for x in t.values())),
                              "worst_pct": 100 * sum(t.values()) / v, "rss_pct": 100 * math.sqrt(sum(x * x for x in t.values())) / v}
            i = P_RATED / v
            g = 30.1 * 100e-6                                   # lean_shunt IM: VMID + 30.1 x 100 uOhm x I = 3.01 mV/A
            iin = vm + g * i
            t = {"shunt TCR 50 ppm/K x 60 K (self-heating + swing, ASSUMED)": 50e-6 * 60.0 * i,
                 "difference-amplifier resistor drift 2 x 25 ppm/K": 2 * 25e-6 * span * i, "OPA2388 offset 5 uV x 31.1": 5e-6 * 31.1 / g,
                 "VMID drift since the idle null": ref(span) * vm / g, "ADC reference": ref(span) * iin / g,
                 "ADC gain residual 5 LSB": 5 * lsb * iin / 3.0 / g, "ADC INL 2 LSB": 2 * lsb / g}
            ri[f"{v:.0f}"] = {"I_rated_A": i, "terms_A": t, "worst_A": sum(t.values()), "rss_A": math.sqrt(sum(x * x for x in t.values())),
                              "worst_pct_of_rated": 100 * sum(t.values()) / i, "rss_pct_of_rated": 100 * math.sqrt(sum(x * x for x in t.values())) / i}
        v_out[conv], i_out[conv] = rv, ri
    b = list(v_out)[1]
    meets_v = all(r["rss_pct"] <= 1.0 for r in v_out[b].values())
    meets_i = all(r["rss_pct_of_rated"] <= 2.0 for r in i_out[b].values())
    return {"requirement": "competitor: DC voltage +/-1 %, DC current +/-2 % of rated (PMA datasheet 'voltage / current stabilization "
                           "accuracy')", "voltage": v_out, "current": i_out, "meets_rss_b": {"voltage": meets_v, "current": meets_i},
            "worst_b_max_pct": {"voltage": max(r["worst_pct"] for r in v_out[b].values()),
                                "current": max(r["worst_pct_of_rated"] for r in i_out[b].values())},
            "closure": ("the regulation itself adds nothing at DC (integral action); the error is the measurement's.  Met by RSS with the "
                        "firmware re-zero; the worst-case linear sum is not guaranteed - the largest terms are the ADC gain residual (kept "
                        "whole, as the board check does) and the two REF3030E drifts.  Cheapest closure if a guarantee is wanted: read VMID "
                        "on the 23rd analog pin (PCS_PC VAX carries it on PCS-PWR) and trim the DC-voltage reading against the BMS pack "
                        "voltage (firmware)")}
def grid_hf_4w(F):
    """grid-current carrier harmonics with this study's own four-wire model (each phase driven by its leg against the midpoint, the N leg
    ideal at M - pwm_fourier / lcl_tf as step c; the three-wire case keeps only the differential part): largest component above h50
    in % of the rated peak, stiff grid and SCR 50"""
    zb = V_LL ** 2 / P_RATED
    out = {}
    for vdc in (750.0, 950.0):
        m = V_LL * math.sqrt(2.0 / 3.0) / (vdc / 2) * 1.03
        h, c = pwm_fourier(2, m, FSW_CHOICE, "none")
        sel = h > 50
        for wire in ("3W", "4W"):
            v = np.abs(c[0] - c.mean(0) if wire == "3W" else c[0]) * vdc / 2
            for grid, lg in (("stiff", 0.0), ("SCR 50", zb / 50 / (2 * math.pi * F_GRID))):
                _, y2 = lcl_tf(h[sel] * F_GRID, F["L1"], F["Cf"], F["L2"], lg=lg, r1=5e-3, r2=5e-3, rd=F["Rd"], cd=F["Cd"])
                i2 = np.abs(y2) * v[sel] / (I_RATED * math.sqrt(2))
                k = int(np.argmax(i2))
                out[f"{vdc:.0f} V {wire} {grid}"] = {"h": int(h[sel][k]), "pct_of_rated": 100 * float(i2[k])}
    return out


CTL_SPEC = os.path.join(HERE, "out", "pcs_control", "pcs_control_spec.json")   # sim/pcs_control.py (read only, D-077 / review R2)
SM_OUT_KEY = "outputs_PWM_KACPRE_KPRE_KDC_KAC2_KAC1"                            # the executed machine's output vector, in this order
SM_OUT_NAMES = ("PWM", "K_ACPRE", "K_PRE", "K_DC", "K_AC2", "K_AC1")
SM_AC_STOP = ("AC_TEST", "AC_PRECHARGE", "AC_CLOSE_K2", "AC_CLOSE_K1", "RETRY_WAIT", "RECTIFY", "DC_MATCH")   # AC-start states a stop leaves


def ctl_block(key, need=()):
    """one block of the control study's pcs_control_spec.json, fail closed: a missing file, block or key stops the run, so the hand-over
    never quotes a machine or a figure the control study did not produce"""
    try:
        b = json.load(open(CTL_SPEC))[key]
        miss = [k for k in need if k not in b]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SystemExit(f"{os.path.relpath(CTL_SPEC, ROOT)}: no usable '{key}' block ({e!r}) - run sim/pcs_control.py first") from None
    if miss:
        raise SystemExit(f"{os.path.relpath(CTL_SPEC, ROOT)} '{key}' lacks {miss}: the control study changed - re-read it")
    return b


def ctl_ac_start():
    """the AC start as the control study executed it (review R2-14, sim/pcs_control.py section 9d: the exhaustively checked transition
    table and sm_exec with fault injection), quoted for the hand-over: the states and their outputs, the stop routes, the repairs H1-H5
    and every fault outcome - read, fail closed (an invariant that fails or a breach left with the repairs stops the run)"""
    sm = ctl_block("ac_start_state_machine", (SM_OUT_KEY, "exec"))
    try:
        ex, fsm = sm["exec"], ctl_block("firmware", ("state_machine",))["state_machine"]
        out = {s: dict(zip(SM_OUT_NAMES, v, strict=True)) for s, v in sm[SM_OUT_KEY].items()}
        q = {"source": "sim/out/pcs_control/pcs_control_spec.json ac_start_state_machine + firmware.state_machine (sim/pcs_control.py "
                       "section 9d, review R2-14) - quoted, not retyped", "outputs_order": list(SM_OUT_NAMES), "outputs": out,
             "stop_routes": {s: fsm["transitions"][f"{s} --stop-->"] for s in SM_AC_STOP},
             "repairs": {h: ex["fixes"][h] for h in ("H1", "H2", "H3", "H4", "H5")}, "breaches_as_specified": ex["holes"],
             "invariants": fsm["verified"], "relay_times_s_ASSUMED": ex["relay_times_s"],
             "fault_cases": [{"fault": x["fault"], "at": x["at"], "outcome": x["fixed"], "breaches": x["breach_fixed"],
                              "precharge_attempts": x["att_fixed"]} for x in ex["rows"]]}
        assert set(SM_AC_STOP) | {"AC_STOP", "LOCKOUT", "STOP", "IDLE"} <= set(out), "a state the hand-over names is not in the table"
        assert q["invariants"] and all(q["invariants"].values()), "an invariant of the checked table fails"
        assert not ex["residual"] and not any(c["breaches"] for c in q["fault_cases"]), "a safety breach remains with the repairs"
        assert out["AC_STOP"]["K_DC"] == 0 and out["STOP"]["K_DC"] == 1, "AC_STOP must keep K_DC open (CONTROLLED STOP holds it)"
        assert all(q["stop_routes"][s] == ("AC_STOP" if out[s]["PWM"] else "IDLE") for s in SM_AC_STOP), "stop routes of the AC start"
    except (KeyError, TypeError, ValueError, AssertionError) as e:
        raise SystemExit(f"{os.path.relpath(CTL_SPEC, ROOT)} ac_start_state_machine: {e!r} - the executed machine no longer matches the "
                         "hand-over's AC-start text: re-read sim/pcs_control.py section 9d") from None
    return q


def ctl_ride_through():
    """the control study's ride-through rule (review R2-05, sim/pcs_control.py section 4c; read, fail closed): the adopted onset measure
    is the rule; the D-077 stiff-grid derating is its stated fallback with the SCR boundary, the grid-stiffness estimate and the
    installation's fault level"""
    lim = ctl_block("ride_through", ("limiter",))["limiter"]
    r = ctl_block("ride_through_rule", ("adopted", "clamp_rt_A", "rows", "worst_onset", "margin_A", "thr", "boundary", "scr_b", "table",
                                        "dq_pu", "acc", "engage_scr", "dV_engage_V", "lsb_V", "S_sc_engage_MVA", "S_sc_b_MVA"))
    rows, b = r["rows"], r["boundary"]
    if not r["adopted"] or any(x["trip"] for x in rows):
        raise SystemExit("pcs_control_spec ride_through_rule: the onset measure is not adopted or a ride-through case trips - the "
                         "hand-over's text (measure first, derating as the fallback) no longer holds: re-read sim/pcs_control.py 4c")
    stiff = [x for x in r["table"] if x["scr"] is None and x["frac"] < 1.0]
    dep = lambda xs: " / ".join("%g" % x["depth"] for x in xs)                               # noqa: E731
    return {"measure": (
        "ride-through state (entered below %g pu |v_C|, left %.0f ms after it is back above %g pu; dip-time limit %.0f A pk = 1.0 I_r; PLL held below %g pu) "
        "with the per-sample clamp at %.0f A, set to %.0f A while the state runs and predicting on the divider-inverted C_f voltage "
        "(the adopted onset measure, review R2-05): onset %.0f A at a stiff 0 pu dip, %.0f A at the worst corner / dip instant against "
        "the %.0f A window edge (%.0f A margin); every onset, in-dip and recovery peak <= %.0f A from stiff to SCR %g at %g-%g pu "
        "residual voltage - rated current, no derating (SIMULATED, averaged model)" % (
            lim["lvrt_pu"][0], lim["hold_s"] * 1e3, lim["lvrt_pu"][1], lim["dip_limit_A_pk"], lim["pll_freeze_pu"], lim["clamp_A"],
            r["clamp_rt_A"], next(x["pk_on"] for x in rows if x["scr"] is None and x["depth"] == 0.0), r["worst_onset"], r["thr"],
            r["margin_A"], max(max(x["pk_on"], x["pk_dip"], x["pk_rec"]) for x in rows), min(x["scr"] for x in rows if x["scr"]),
            min(x["depth"] for x in rows), max(x["depth"] for x in rows))),
        "fallback": (
            "FALLBACK if a switched model or the bench shows less margin: with the D-077 limiter rated current passes up to SCR %s at %s pu "
            "residual voltage; the grid-stiffness estimate (a %g pu reactive-current step at connection, a PRBS of it in operation, "
            "correlated over about 10 s in the 1 kHz task: |Z_g| = |dV_G / dI_g| from the terminal-voltage phasor VG1-3 and the "
            "estimated grid current i_1 - j w C_eq v_C, SCR = Z_base / |Z_g|; %.2f V = %.1f LSB at the engage level, averaging needed; "
            "accuracy ASSUMED +/-%.0f %%; no valid estimate = stiff) engages the derating at an estimated SCR >= %.1f: stiff %s kW at %s "
            "pu, the control study's table between" % (
                " / ".join("%.1f" % x["scr_b"] for x in b), dep(b), r["dq_pu"], r["dV_engage_V"], r["dV_engage_V"] / r["lsb_V"],
                100 * r["acc"], r["engage_scr"], " / ".join("%g" % x["P_max_kW"] for x in stiff), dep(stiff))),
        "installation": (
            "with the measure no fault-level limit (averaged model); under the fallback the profile rides through at rated current where "
            "the connection's fault level per 125 kW module is below %.2f MVA (SCR %.1f with the estimate's tolerance; %.2f MVA at the "
            "computed boundary SCR %.1f) - n modules at one point: the point's fault level / n; above it the derating table applies" % (
                r["S_sc_engage_MVA"], r["engage_scr"], r["S_sc_b_MVA"], r["scr_b"])),
        "acc": r["acc"], "thr_A": r["thr"], "derating_stiff_kW": {"%g pu" % x["depth"]: x["P_max_kW"] for x in stiff}}


def ctl_vf_rows():
    """the grid-forming (VF) transient envelope and the two firmware measures behind it (review R2-04, sim/pcs_control.py section 7c;
    read, fail closed) as firmware rows; the D-077 0.38 / 2.17 pu were the unbounded averaged model - superseded"""
    vb = ctl_block("vf_bounded", ("steps", "envelope", "vclamp_pu"))
    ff = ctl_block("vf_feedforward", ("chosen", "scan"))
    st = vb["steps"]
    up = [x for k, x in st.items() if k.startswith("design, single module") and k.endswith("0 -> rated")]
    dn = [x for k, x in st.items() if k.startswith("design, single module") and k.endswith("rated -> 0")]
    o_up, o_dn = (next((x for k, x in st.items() if k.startswith("D-077 configuration") and k.endswith(e)), None) for e in ("0 -> rated", "rated -> 0"))
    if not (up and dn and o_up and o_dn) or not all(x["env"]["ok"] for x in up + dn) or not ff["chosen"]["ok"]:
        raise SystemExit("pcs_control_spec vf_bounded / vf_feedforward: a single-module step outside the declared envelope, the D-077 "
                         "reference rows or the chosen feed-forward missing - re-read sim/pcs_control.py section 7c")
    env = lambda side: ", ".join("%g pu to %g ms" % (pu, t) if t is not None else "%g pu after" % pu for t, pu in vb["envelope"][side])  # noqa: E731
    v_up, v_dn = min(x["v_min"] for x in up), max(x["v_max"] for x in dn)
    return [
        {"item": "VF transient envelope, single module, 100 % linear load step (declared; review R2-04)",
         "peripheral": ("the VF cascade with the load-current feed-forward and the over-voltage deadbeat (the next two rows), the per-sample "
                        "clamp and the virtual impedance at 0 for a single module; paralleled modules keep the droop's for sharing - the "
                        "envelope is not declared for them (their first milliseconds are the same, the plateau is the secondary layer's)"),
         "threshold": ("|v_C| over: <= %s; under: >= %s; simulated (averaged, every corner): 0 -> rated dip to >= %.3f pu, rated -> 0 "
                       "overshoot <= %.3f pu, within 10 %% after <= %.1f ms and 1 %% after <= %.1f ms; the D-077 figures %.2f / %.2f pu "
                       "were the unbounded averaged model without these measures - superseded" % (
                           env("over"), env("under"), v_up, v_dn, max(x["t_v10_ms"] for x in up + dn),
                           max(x["t_v1_ms"] for x in up + dn), o_up["v_min"], o_dn["v_max"])),
         "filter": "-", "latency": "-", "self_test": "bench: the first-millisecond excursion at every corner of the drawn parts (OPEN)"},
        {"item": "VF load-current feed-forward (review R2-04; adopted)",
         "peripheral": ("control ISR, VF only (not in grid-connected grid forming): i_ref += LPF(i_1 - C_eq (v_C[k] - v_C[k-1]) / T), the "
                        "load current estimated from the converter current and the C_f voltage (no grid-side sensor)"),
         "threshold": ("low-pass %.0f Hz, the highest of the scan meeting the rule (PM %.1f deg, GM %.1f dB at every corner); %s fail it"
                       % (ff["chosen"]["f_io"], ff["chosen"]["pm"], ff["chosen"]["gm"],
                          ", ".join("%.0f Hz (GM %.1f dB)" % (x["f_io"], x["gm"]) for x in ff["scan"] if not x["ok"]))),
         "filter": "first order, %.0f Hz" % ff["chosen"]["f_io"], "latency": "every control period", "self_test": "-"},
        {"item": "VF over-voltage deadbeat (review R2-04; adopted)",
         "peripheral": ("control ISR, VF only: above the threshold (divider-inverted C_f sample) the inner command is the one that ends the "
                        "next period with i_1 at the unfiltered load-current estimate; the voltage PR does not integrate meanwhile"),
         "threshold": ("%.1f pu; full-load rejection <= %.2f pu at the worst corner (the D-077 configuration, unbounded and without the "
                       "measures: %.2f pu)"
                       % (vb["vclamp_pu"], v_dn, o_dn["v_max"])),
         "filter": "-", "latency": "every control period", "self_test": "-"}]


def ctl_dc_rows(acc):
    """the DC-side rejection rules (review R2-04, sim/pcs_control.py sections 6b and 7c; read, fail closed): the soft DC-link over-voltage
    limit holds the DC-link loop in CV mode, the CV-mode DC/DC power limits by SCR, and the DC/DC coordination on a battery-less bus
    (an installation / system rule).  acc = the grid-stiffness estimate's ASSUMED accuracy (ride_through_rule)"""
    dr = ctl_block("dc_rejection_bounded", ("rows", "derating", "soft", "band", "resp_us", "ppb"))
    vb = ctl_block("vf_bounded", ("t_c_max_ms", "e_ret_J", "p_pre_W", "cap_follow", "cap_tc", "cap_tc_over", "uncoordinated"))
    c = ctl_block("inputs", ("C_dc_F",))["C_dc_F"]
    try:
        alone = next(x for x in dr["rows"] if x["soft"] is None)
        trip = [x for x in dr["rows"] if x["trip"]]
        unc = sorted(vb["uncoordinated"].values(), key=lambda u: u["vdc_end"])
        lim = {("stiff" if x["scr"] is None else "SCR %g" % x["scr"]): x["P_max_kW"] for x in dr["derating"]}
        assert trip and not alone["trip"] and all(x["half_ok"] and x["dev_ok"] for x in trip + unc), "DC rejection: device / film"
    except (KeyError, StopIteration, AssertionError) as e:
        raise SystemExit(f"pcs_control_spec dc_rejection_bounded / vf_bounded: {e!r} - re-read sim/pcs_control.py sections 6b / 7c") from None
    v_bus = math.sqrt(vb["cap_follow"]["vdc_max"] ** 2 - 2 * vb["e_ret_J"] / c)      # the study's bus: where the returned energy starts
    aux_ov = min(json.load(open(AUX75))["startup"]["ov_lockout_V"])                  # the 75 W aux's input lock-out (aux75_spec)
    rng = lambda xs, f="%.0f": (f + "-" + f) % (min(xs), max(xs))                    # noqa: E731
    soft, band, ppb = dr["soft"], dr["band"], dr["ppb"]
    coord = {"item": "DC/DC coordination on a DC bus without a battery (installation / system rule; review R2-04)",
             "peripheral": ("outside this module: every DC/DC feeding a battery-less bus that this module turns into an island (VF) cuts its "
                            "current on its own local bus-voltage detection (PV-P75 / PV-P100/110: sim/out/pv_control/control_spec.json "
                            "firmware_second_layer 'system') or a shared hardwired trip line - a CAN message is too slow; the PCS cannot "
                            "absorb the energy (no brake chopper drawn)"),
             "threshold": ("cut within %.2f ms of an AC load rejection at the study's %.0f V bus and %.0f kW (the link reaches the %.0f V soft "
                           "limit at that time; %.0f / %.0f V at 0.95 / 1.05 x it); the rejection returns %.1f J of LCL energy to the "
                           "link (%.0f V even with an ideal source); uncoordinated the comparator trips %s ms after the step and the bus "
                           "is left at %s V, latched - %s" % (
                               vb["t_c_max_ms"], v_bus, vb["p_pre_W"] / 1e3, soft[0], vb["cap_tc"]["vdc_max"], vb["cap_tc_over"]["vdc_max"],
                               vb["e_ret_J"], vb["cap_follow"]["vdc_max"], rng([u["t_off_ms"] for u in unc], "%.2f"),
                               rng([u["vdc_end"] for u in unc]),
                               ("at the band top above the aux's %.0f V input lock-out: the island goes dark" % aux_ov)
                               if any(u["aux_lockout"] for u in unc) else "below the aux's %.0f V input lock-out" % aux_ov)),
             "filter": "-", "latency": "-", "self_test": "-"}
    return [
        {"item": "DC-link over-voltage soft limit in CV mode (review R2-04; amends the control board's 'OV / UV soft limits' row)",
         "peripheral": ("outer loop on VB: in CV mode the soft limit holds the DC-link loop - the bridge keeps exporting up to its current "
                        "limit, the only sink on the link - instead of the controlled stop; in PQ / CP / CC and VF it keeps the controlled "
                        "stop (no latch); the stop on DC over-voltage stays with the hardware: comparator band %.0f-%.0f V (%.0f us to "
                        "gates off) and its ADC-PPB backup %.0f-%.0f V (%.1f us), latched, no automatic clear" % (*band, dr["resp_us"], *ppb)),
         "threshold": ("%.0f V (%.0f us); simulated weak-grid DC-load rejection (SCR %g, %.0f V, %.0f kW DC/DC load trips, averaged): the "
                       "loop alone peaks at %.0f V; with the protection the comparator turns the gates off at %s V after %s ms and the "
                       "diode-bridge dump leaves the bus at %s V (halves <= %.0f V), latched - holding the loop changes nothing there "
                       "(the comparator trips first) but lets a smaller rejection recover below the band" % (
                           soft[0], soft[1], alone["scr"], alone["vdc_end"], alone["P_kW"], alone["vmax_loop"],
                           rng([x["v_off"] for x in trip]), rng([x["t_off_ms"] for x in trip], "%.2f"),
                           rng([x["vdc_end"] for x in trip]), max(x["half_pk"] for x in trip))),
         "filter": "-", "latency": "outer loop", "self_test": "-"},
        {"item": "CV mode with DC/DC loads at weak grids (review R2-04)",
         "peripheral": ("the DC/DC power the link may serve in CV mode, selected from the grid-stiffness estimate (the ride-through "
                        "fallback's estimator): the row at or below the estimate / %.1f, no interpolation upward, no valid estimate = the "
                        "weakest row (selection ASSUMED); published to the DC/DC over the module CAN / the EMS, which enforce it" % (1 + acc)),
         "threshold": (", ".join("%s %g kW" % kv for kv in lim.items()) + ": a DC/DC trip then stays below the %.0f V soft limit "
                       "(simulated, averaged; full power from SCR %g)" % (soft[0], min(x["scr"] for x in dr["derating"]
                                                                                       if x["scr"] and x["frac"] >= 1.0))),
         "filter": "-", "latency": "-", "self_test": "-"},
        coord]


def ctl_n_leg():
    """the control study's neutral-leg loop as hand-over text (D-077)"""
    nl = json.load(open(CTL_SPEC)).get("neutral_leg") if os.path.exists(CTL_SPEC) else None
    if not nl:
        return "the N-leg loop is a control-study item (sim/pcs_control.py not run yet)"
    return ("the N leg runs the off-grid cascade (inner current loop + voltage loop) in every mode with its own DC regulator (worst PM "
            "%.0f deg, GM %.1f dB; D-077)" % (min(r["v_pm"] for r in nl["rows"]), min(r["v_gm"] for r in nl["rows"])))


def step_i(D, rb, rc, rd, rf, rg_):
    """(i) four-wire build (PCS-PWR-4W), the AC-side start-up path of both builds, declarations and the firmware rows they need"""
    om, F = SPEC["operating_map"], rc["filter"]
    hs = SPEC["thermal_and_losses"]["heatsink"]
    win = fw_window(om)
    inc = rg_["four_wire_increment"]
    mid = midpoint_neutral(rd, inc)
    # ---- unbalance, neutral current, device loading
    v_ln = V_LL / math.sqrt(3.0)
    tiers = {"rated": I_RATED, "continuous": I_CONT, "2_min": I_2MIN, "200_ms": I_200MS}
    per_phase_kW = {k: v_ln * i / 1e3 for k, i in tiers.items()}
    nl = {f"{i:.0f} A, {vdc:.0f} V, {t:.0f} C": n_leg(D, vdc, i, t) for i, t in ((I_CONT, T_IN), (I_2MIN, T_IN), (I_CONT, T_IN_HOT))
          for vdc in (750.0, 900.0)}
    ph = {f"{i:.0f} A, {vdc:.0f} V, {t:.0f} C": evaluate(D, vdc, V_LL, i, 0.0, t)["tj_max"]
          for i, t in ((I_CONT, T_IN), (I_2MIN, T_IN), (I_CONT, T_IN_HOT)) for vdc in (750.0, 900.0)}
    unb = {"per_phase_kW_at_230V": per_phase_kW, "single_phase_full_module_power_A": P_RATED / 2 / v_ln,
           "cases": {"one phase at I, two at 0 (100 % unbalance)": "I_N = I",
                     "two phases at I, one at 0": "I_N = I (two equal currents 120 deg apart)",
                     "phase a at I, phase b at I / 2, phase c at 0": "I_N = %.3f I" % abs(1 + 0.5 * complex(math.cos(-2 * math.pi / 3), math.sin(-2 * math.pi / 3))),
                     "per-phase P / Q set points with the three currents in phase": "I_N up to 3 I - the firmware limits I_N to the N leg's tiers"},
           "n_leg": nl, "phase_leg_same_current_tj_C": ph, "tj_limits_C": TJ_LIM["sic"]}
    # ---- the DC link with the neutral current (four-leg switching model), the N leg's ripple into M through C_fN
    c_loc4 = 4 * leg_model(D["devs"]["TH"][1], 1.0)["c_dec"]
    c4 = (rd["C_half_uF"] * 1e-6 / 2 + c_loc4) * 1e6
    cases = []
    for vdc in (750.0, 950.0):
        for amps, ang in (((I_CONT,) * 3, 0.0), ((I_2MIN, 0.0, 0.0), 0.0), ((I_2MIN, I_2MIN, 0.0), 0.0), ((I_2MIN, I_2MIN / 2, 0.0), 0.0),
                          ((I_2MIN, 0.0, 0.0), 90.0)):
            r = dc_currents_4w(vdc, amps, F["L1"], ang)
            cm3 = rc["ripple"][min(rc["ripple"], key=lambda v: abs(v - vdc))]["cm_total_rms_A"] / 2
            rip_n = vdc / (4 * FSW_CHOICE * F["L1"]) / (2 * math.sqrt(3))
            r["cm_half_A"] = cm3 + rip_n / 2                    # phases' C_f-star current + the N leg's ripple through C_fN, in phase (worst)
            r["per_cap_A"] = math.hypot(r["hf_half_A"], r["cm_half_A"]) / rd["per_half"]
            cases.append(r)
    cap_lim = pv.CAP_IRMS_USE * FILM[rd["part"]]["imax"]
    # ---- half-wave load (off-grid, four-wire): one phase draws i = I_pk sin for its positive half only, the N leg returns it
    l1p = SPEC["inductors"]["L1"]["current"]["peak_A"]["110 %"]                # the L1 part's continuous peak (L_N = the same part)
    rip_pp = SPEC["inductors"]["L1"]["current"]["ripple_pp_max_A"]
    i_pk_hw = l1p - rip_pp / 2
    tt = np.linspace(0, 1 / F_GRID, 4000, endpoint=False)
    ihw = np.maximum(i_pk_hw * np.sin(2 * math.pi * F_GRID * tt), 0.0)
    phw = math.sqrt(2) * v_ln * np.sin(2 * math.pi * F_GRID * tt) * ihw
    hw = {"I_pk_limit_A": i_pk_hw, "I_dc_A": float(ihw.mean()), "I_rms_A": float(np.sqrt(np.mean(ihw ** 2))),
          "L_N_peak_A": i_pk_hw + rip_pp / 2, "L1_part_continuous_peak_A": l1p, "P_mean_kW": float(phw.mean()) / 1e3,
          "battery_lf_A_rms_750V": float(np.std(phw)) / 750.0, "midpoint_lf_A": 0.0,
          "dc_component_limit_V": 0.005 * v_ln, "dc_measurement_divider_V": 0.0015 * 750.0 / 2,
          "basis": ("the half-wave current's DC and 50 / 100 Hz parts flow phase -> load -> N -> L_N -> N leg -> DC+ / DC- (the N leg draws "
                    "from both rails, nothing enters M at low frequency: M sees only the C_f / C_fN currents); limit: L_N's peak (DC + AC + "
                    "half the ripple) <= the L1 part's continuous peak; the output voltage's DC component is regulated by firmware")}
    # ---- the DC link of the four-wire build: 4 x 9 leg films, a fourth C_f set (C_fN) on M without a divider
    bl4 = bleeder(rd["C_half_uF"] * 1e-6, c_loc4, F["Cf"] + F["Cd"], n_ph=3, n_extra=1)
    pre4 = rpre_duty_c(c4 * 1e-6, V_OV_TRIP)
    rr = port_rating()
    # with the shared 220 ohm the worst time constant of this bus passes the RPRE_AL rating point (280 J at tau 105 ms): RPRE_AL is an
    # RFQ part without a data sheet (gen/port.py, docs/SOURCES.csv: no pulse curve to stretch the rating to 110 ms), so the four-wire
    # build takes the next lower E24 value of the same specification. Peak current against the precharge relay: Omron G7L-X.pdf
    # (J216-E1-04) p1, G7L-2A-X 25 A at 1000 V DC with the two poles in series (gen/port.py G7L2AX)
    r4 = next(r for r in (220.0, 200.0, 180.0, 160.0) if rpre_duty_c(c4 * 1e-6, V_OV_TRIP, r=r)[1] <= rr["tau_s"])
    fix4 = rpre_duty_c(c4 * 1e-6, V_OV_TRIP, r=r4)
    pre4_fix = {"R_ohm": r4, "E_J_max": fix4[0], "tau_max_s": fix4[1], "E_short_J": fix4[2], "t_short_s": fix4[3],
                "I_pk_A": V_OV_TRIP / (r4 * (1 - rr["tol"])), "relay_A": 25.0, "t_to_10V_max_s": fix4[1] * math.log(V_OV_TRIP / 10.0),
                "board": ("drawn on PCS-PWR-4W as RPRE_AL %.0f R (gen/port.py lean_port r_pre, catalog entry RPRE_AL_200; D-076): the shared "
                          "%.0f ohm would sit above the 105 ms rating point" % (r4, rr["R"])) if r4 != rr["R"] else "the shared value holds"}
    # ---- midpoint low-frequency current in the four-wire build (no balancing needed): the zero sequence of the C_f voltages
    i_m0 = 3 * (F["Cf"] + F["Cd"]) * 2 * math.pi * F_GRID * 0.02 * v_ln
    dv_m0 = i_m0 * math.sqrt(2) / (2 * math.pi * F_GRID * 2 * rd["C_half_uF"] * 1e-6)
    four = {"decision_cf_star": {
        "star": "M (the DC midpoint), unchanged from the three-wire build; the neutral filter node N_F gets its own C_f set (C_fN = 2 x 25 uF "
                "+ the R_d / C_d branch) to M",
        "why": ("this study's four-wire model (pwm_wave / l2_for_limit wire='4W') takes each phase as 'leg minus an unmodulated N leg = leg vs "
                "midpoint', i.e. the N leg holds N at M; step f calls the star-to-midpoint tie necessary (it returns the 440 V carrier "
                "common-mode voltage locally and leaves %.1f V at the terminals); L_N is specified 'as L1: the neutral leg's ripple is that of a "
                "phase leg', which holds only if L_N ends on a node that is quiet at f_sw - a capacitor from N_F to M; and sim/magnetics.py "
                "keeps L_N = the L1 part only if the phases' common-mode ripple does not return through L_N.  Moving the star to N_F would put "
                "the converter's carrier common-mode voltage (hundreds of volts at %.0f kHz) between the DC side and N / earth, past the CM "
                "choke, and the phases' common-mode ripple through L_N - a new magnetic and a new common-mode design"
                % (rf["hf"]["v_cm_terminals_V_pk"], FSW_CHOICE / 1e3))},
        "filter": {"L_N_uH": F["L1"] * 1e6, "C_fN_uF": F["Cf"] * 1e6, "R_d_ohm": F["Rd"], "C_d_uF": F["Cd"] * 1e6,
                   "L_N_C_fN_resonance_Hz": 1 / (2 * math.pi * math.sqrt(F["L1"] * F["Cf"])),
                   "N_leg_ripple_pp_950V_A": 950.0 / (4 * FSW_CHOICE * F["L1"]),
                   "C_fN_ripple_V_pp_950V": 950.0 / (4 * FSW_CHOICE * F["L1"]) / (8 * FSW_CHOICE * F["Cf"])},
        "window": win, "midpoint_neutral_alternative": mid, "unbalance": unb,
        "dc_link": {"cases": cases, "per_cap_limit_A": cap_lim, "per_cap_max_A": max(r["per_cap_A"] for r in cases),
                    "three_wire_per_cap_A": rd["I_per_cap_A"], "C_local_films_uF": c_loc4 * 1e6, "C_total_uF": c4,
                    "lf_note": "LF (to 2 kHz) goes to the battery: the bank is 3.9 ohm at 100 Hz, the battery path tens of mOhm"},
        "half_wave": hw, "grid_hf": grid_hf_4w(F), "midpoint_lf": {"I_rms_A_at_2pct_zero_sequence": i_m0, "ripple_V_peak": dv_m0,
                                          "balancing": "none needed: the two-level legs draw from DC+ / DC-; the bleeders balance the halves"},
        "bleeder": bl4, "precharge": {"C_eq_uF": c4, "E_J_max": pre4[0], "tau_max_s": pre4[1], "E_short_J": pre4[2], "t_short_s": pre4[3],
                                      "rating": rr, "four_wire_R": pre4_fix}}
    SPEC["four_wire"] = four
    # ---- the AC start-up path (both builds)
    st = ac_start(rd, c4)
    sm = ctl_ac_start()                       # the executed AC-start machine of the control study (review R2-14), fail closed
    pol = json.load(open(os.path.join(HERE, "out", "port_design", "port_spec.json")))["lean"]["firmware_requirements"]  # DC-port bands
    r2 = lambda x: "%g-%g" % tuple(x)                                                                                       # noqa: E731
    tau_bl_min = 5                            # bleeder time constant about 5 min (step d / four-wire bleeder, rounded as the label text)
    st["sequence"] = [
        "grid present, DC side dead (battery absent, empty or its own contactors open): the 6-diode tap feeds the 75 W aux through its "
        "OR diode -> live 24 V, 5 V, 3.3 V, the controller boots (latch tripped at power-up) and reads the AC-start attempt count, its "
        "spacing and any lock-out from the recorder flash, checked before every attempt (H5: non-volatile - a brown-out cannot reset the "
        "retry limit)",
        "AC_TEST: self-test of the trip paths; welded-precharge check with K_ACPRE, K_PRE, K_B, K1 and K2 open and the converter passive - "
        "the link must stay below 50 V and VC dead; at a retry the link is still charged by the earlier attempt (bleeder time constant "
        "about %d min), so the check becomes a voltage-difference test valid on a charged link (H2): the link must decay at the bleeder "
        "rate (never rise) and sit below the tap peak; else LOCKOUT (a welded K_ACPRE / K_PRE / K_B or both contactors); no relay test "
        "on the dead link (H1)" % tau_bl_min,
        "AC_PRECHARGE: K_ACPRE (PWM16 line, latch-gated) closes and the attempt is counted in the recorder flash; the link charges from the "
        "tap through 220 ohm to the line-to-line peak minus two diode drops (done at |tap - V_dc| <= 10 V); firmware aborts to RETRY_WAIT "
        "on the shorted-bank or timeout rule",
        "AC_CLOSE_K2: open K_ACPRE (the precharged link holds), then the relay test onto the precharged link (H1): K2 alone, then K1 "
        "alone, the converter passive - VC must stay dead with one contactor closed (VC live = the other one welded -> LOCKOUT)",
        "close K2, then K1 (AC_CLOSE_K1; one coil at a time, the live 24 V budget), each commanded only with V_dc >= 0.95 x sqrt2 x "
        "V_LL,meas, the grid inside +/-10 % at the command (H3) and the DC contactor open - checked at each coil command, not once "
        "before the sequence; no battery on the link: the bridge closes onto its own precharged link through the body diodes (inrush <= 5 % of "
        "the peak over sqrt(L / C): tens of amperes); VC must follow VG once both are in, else both open -> RETRY_WAIT (stuck contactor)",
        "RECTIFY: the bridge starts as a rectifier on the control study's DC-link loop (PI 40 Hz + DC-current feed-forward) and raises "
        "V_dc to the battery's terminal voltage (VBX) - or 750 V without a battery (standby, STATCOM, communication)",
        "DC port (H4): K_B only with VBX >= 1.05 x sqrt2 x V_LL,meas (the rectification threshold) and the link matched to the battery "
        "within the hardware dV window (%s V): the bridge raises the link first, then K_B closes directly; no K_PRE -> K_B route onto a "
        "battery below the grid peak (uncontrolled rectification) - below the threshold no DC connection, standby on the grid and a "
        "report; the hardware polarity interlock needs >= %s V at the battery terminal" % (r2(pol["dV_enable_V"]), r2(pol["polarity_enable_V"])),
        "normal operation (CV / CC / PQ) from there; the aux keeps both feeds (DC-link tap and grid tap, diode OR); the completed start "
        "ends the attempt count"]
    st["interlocks"] = [
        "K_ACPRE only with K_B, K_PRE, K_A and K_AC2 open (firmware; the latch drops it with every trip and with a dead controller)",
        "no AC precharge with the DC contactor closed onto a live battery below the grid peak (it would charge the battery through 220 ohm "
        "for as long as the relay stays closed): K_ACPRE and K_B mutually exclusive in firmware",
        "grid-start closing permissive replaces the operating-map permissive only while K_B is open (no battery on the link); with K_B "
        "closed the map's permissive (V_dc >= 1.02 sqrt2 V_LL + 10 V and the map for the commanded P / Q) applies unchanged",
        "abort: V_dc below 63 % of the tap peak at 1.1 tau_max + 30 ms (shorted bank) or not within 10 V of it within 3 x the calculated "
        "time; <= 3 attempts per start >= 30 s apart, counted across controller resets (H5: count, spacing and lock-out in the recorder "
        "flash), then lock-out until a service reset",
        "a stop during the AC start (command, grid lost, limit timeout) never passes through CONTROLLED STOP, which holds the DC contactor: "
        "before the bridge runs (" + ", ".join(k for k, v in sm["stop_routes"].items() if v == "IDLE") + ") -> IDLE with everything open; "
        "with the bridge running (" + ", ".join(k for k, v in sm["stop_routes"].items() if v == "AC_STOP") + ") -> AC_STOP: current to "
        "zero, K1 / K2 open at zero current, PWM off, the DC contactor never commanded -> IDLE",
        "the table's DC_MATCH state (K_PRE, then K_B) is not entered by the repaired AC start (H4); bleeder time constant about %d min "
        "(the welded-precharge check at a retry, H2)" % tau_bl_min]
    st["consequences"] = [
        "the tap ties the DC side to the grid through its lower diodes with the AC contactors open: with a battery connected and the grid "
        "present, DC- is peak-clamped to the most negative phase; the insulation monitor's PE -> DC- state is then not valid (R_iso- is "
        "covered by the residual-current monitor once connected): firmware runs the full two-state insulation test with the grid absent "
        "or treats the DC- result as 'clamped by the grid'",
        "the aux input and, with a welded precharge relay, the DC link are live whenever the AC terminals are live: enclosure label "
        "'isolate AC and DC, wait 15 min'",
        "the tap and the precharge path can only draw from the grid (diodes): with the AC contactors open the converter cannot export through "
        "them, a welded precharge relay included"]
    st["state_machine"] = sm
    SPEC["ac_start"] = st
    # ---- declarations
    sc = ac_short(rc)
    pr = precision()
    tier_kva = {k: math.sqrt(3) * V_LL * i / 1e3 for k, i in tiers.items()}
    q_tot = hs["flow_m3h_per_section"]
    cfm = lambda n: n * q_tot / 1.699                                     # noqa: E731  1 CFM = 1.699 m3/h
    ctl = os.path.join(HERE, "out", "pcs_control", "pcs_control_spec.json")
    steps = {}
    if os.path.exists(ctl):                                             # the control study of the same LCL (read only; stale-safe text)
        for s in json.load(open(ctl)).get("steps", []):
            if s["case"].startswith("ref step"):
                steps["stiff" if s["scr"] is None else "SCR %g" % s["scr"]] = s["settle_ms"]
    i_lim_pk = I_200MS * math.sqrt(2)
    dol = I_2MIN / 6.0
    decl = {"ac_short_circuit": sc, "stabilized_precision": pr,
            "kva_tiers_400V": {"rated 180 A": tier_kva["rated"], "110 % continuous 198 A": tier_kva["continuous"],
                               "120 % 2 min 216 A": tier_kva["2_min"], "200 ms 259.2 A": tier_kva["200_ms"]},
            "operating_windows": win["windows_at_400_230V"],
            "frequency": {"ours": ("the operating map holds at 50 and 60 Hz (both computed); PLL integrator clamp +/-5 Hz (control study), "
                                   "trip windows = the grid code's parameters (EN 50549-1 47.5-51.5 Hz class, FROM MEMORY)"),
                          "competitor": "datasheet 50 +/- 5 / 60 +/- 5 Hz; user manual 50 +/- 2 / 60 +/- 2 Hz (pp. 38, 41-42)"},
            "environment": ("operating -30..+60 C (> 45 C derating, the fans rated -10 C: open risk), storage -40..+70 C (ASSUMED: parts' "
                            "storage ratings to confirm), altitude: see the control board's barrier rating, OVC DC II / AC III, pollution "
                            "degree external 3 / internal 2; IP rating and enclosure out of scope (schematic and BOM only)"),
            "communication": ("the control board's declared port roles: module bus = CAN (paralleling, carrier synchronisation by CAN "
                              "time-stamping, software addressing); BMS = RS-485 (Modbus RTU) or CAN when not paralleled; EMS = Ethernet "
                              "(Modbus TCP, port 502, over the isolated Ethernet bridge) or RS-485; service RS-485 at 9600-8-N-1"),
            "airflow": {"sections": {"three-wire": 3, "four-wire": 4}, "m3h_per_section": q_tot,
                        "CFM": {"three-wire": cfm(3), "four-wire": cfm(4)}, "CFM_per_kW_125kW": {"three-wire": cfm(3) / 125.0, "four-wire": cfm(4) / 125.0},
                        "competitor_CFM_per_kW": PMA["cfm_per_kW"], "competitor_scaled_125kW_CFM": PMA["cfm_per_kW"] * 125.0},
            "offgrid_load_acceptance": {
                "resistive": "up to rated (180 A per phase, 125 kW), 110 % continuous, 120 % for 2 min",
                "crest_factor": {"limiter_peak_A": i_lim_pk, "CF_at_rated_rms": i_lim_pk / I_RATED,
                                 "rms_at_CF": {"2.5": i_lim_pk / 2.5, "3.0": i_lim_pk / 3.0}},
                "motor_DOL": {"I_start_A_2min": I_2MIN, "I_start_A_200ms": I_200MS, "I_n_max_A_at_6x": dol,
                              "P_kW_at_0.85_0.92": math.sqrt(3) * V_LL * dol * 0.85 * 0.92 / 1e3},
                "half_wave_four_wire": hw, "competitor": PMA["offgrid_loads"]},
            "modes": ("PQ (grid following, P / Q set points, PF -1..+1 at full current), CP / CC on the DC side (power or DC current set point), "
                      "CV (DC-link / battery voltage, the control study's DC-link loop), VF (grid forming off-grid), generator-following, "
                      "standby; competitor: " + PMA["modes"]),
            "transfer_ms_control_study": steps,
            "parameter_set": ["battery voltage window (min / max, charge-end CV limit)", "charge / discharge current limits (DC)",
                              "SOC limits (from the BMS) and the off-grid lower voltage and SOC", "power reference -1.2..+1.2 Pn, ramps",
                              "reactive set point / PF / Q(U), cos phi(P), P(f) curves", "grid-code windows, times, LVRT / HVRT curves",
                              "leakage-detect (RCM) enable and limits, insulation-test enable and limit", "parallel enable, module address",
                              "protection hysteresis and restart dwell", "mode (PQ / CC / CP / CV / VF / generator / standby)"],
            "telemetry": ["device (heatsink sections), L1 / L_N, DC-link and inlet temperatures", "insulation resistance (kOhm) and leakage (mA)",
                          "DC voltage, current, power; DC-link (bus) voltage and both halves", "phase output voltages (C_f nodes) and grid line "
                          "voltages (terminals), phase currents, P, Q, S, PF, frequency", "contactor states, relay-test results, latch and fault "
                          "cause, event log, fault records", "energy counters (charge / discharge kWh), operating hours"]}
    rt, vfr = ctl_ride_through(), ctl_vf_rows()             # the control study's ride-through rule and VF envelope (review R2, fail closed)
    dcr = ctl_dc_rows(rt["acc"])                              # soft OV in CV mode, CV-mode DC/DC limits, battery-less bus coordination
    decl["grid_strength"] = {
        "admitted": "SCR 5 .. stiff (the plant range the control study covers)", "ride_through": rt["measure"],
        "fallback": rt["fallback"], "installation_fault_level": rt["installation"],
        "superseded": ("D-077's stiff-grid pre-fault derating (%s) as the primary measure - now the fallback"
                       % ", ".join("%s %g kW" % kv for kv in rt["derating_stiff_kW"].items())),
        "cv_mode_dc_dc_power": dcr[1]["threshold"] + " - " + dcr[1]["peripheral"]}
    decl["dc_bus_without_battery"] = dcr[2]["threshold"] + " - " + dcr[2]["peripheral"]
    SPEC["declarations"] = decl
    # ---- firmware rows (CALCULATED where a number is ours; ASSUMED parameters labelled; competitor claims named)
    claim = "competitor claim, no published parameters; our values ASSUMED (standards not on file)"
    nA, nB = win["rows"]["nominal"]["mode_A"], win["rows"]["nominal"]["mode_B"]
    FW_I.clear()
    FW_I.extend([
        {"item": "four-wire N leg (PCS-PWR-4W): modes A / B",
         "peripheral": ("ePWM4 A/B = PWM7/8 (the N leg); mode A: reference = L_N di_N/dt + the v(N_F - M) loop on VGN - VA (contactors "
                        "closed; 6.8 kHz dividers), sinusoidal phase legs; mode B: the min-max zero sequence of the phase references added "
                        "to all four legs (3D-SVM-equivalent)"),
         "threshold": ("mode A from %.0f V DC at 230 V (%.0f V for rated current at the worst PF; %.0f / %.0f V at +10 / +15 %%), mode B below "
                       "it down to the three-wire window (%.0f / %.0f V); neutral current IL4 <= the phase tiers (198 A rms continuous, 216 A "
                       "2 min, 259 A 200 ms), IL4 window and CMPSS4 as the phases" %
                       (nA["closing (no load)"], max(nA[p] for p in PF_CASES), win["rows"]["+10 %"]["mode_A"]["closing (no load)"],
                        win["rows"]["+15 %"]["mode_A"]["closing (no load)"], nB["closing (no load)"], max(nB[p] for p in PF_CASES))),
         "filter": "-", "latency": "every control period", "self_test": "power-up: IL4 = 0 V and VGN = 0 V mark a three-wire power board -> "
                                                                     "four-wire configuration refused"},
        {"item": "per-phase power control (four-wire, on-grid split-phase)",
         "peripheral": "per-phase P / Q set points through the PR current loops of each phase (positive, negative and zero sequence)",
         "threshold": ("each phase <= its tier (%.1f kW at 230 V and 198 A continuous, %.1f kW for 2 min); |i_a + i_b + i_c| <= 198 A rms "
                       "(the N leg; in-phase currents could reach 3 x); %s" % (per_phase_kW["continuous"], per_phase_kW["2_min"], claim)),
         "filter": "one grid period", "latency": "per period", "self_test": "-"},
        {"item": "operating modes PQ / CP / CC / CV / VF / generator-following / standby",
         "peripheral": "mode state above the protection state machine; mode changes only through the stop -> start path except PQ <-> CC / CP",
         "threshold": ("PQ: P, Q inside the operating map, PF -1..+1 at full current; CP / CC: DC power / current set point (IB), ramps "
                       "<= 125 kW in 20 ms (the control study's DC-link rule); CV: the DC-link loop (PI 40 Hz + DC-current feed-forward), "
                       "battery voltage from VBX; VF and generator-following: rows below"),
         "filter": "-", "latency": "-", "self_test": "-"},
        *dcr,
        {"item": "VF (grid forming, off-grid)",
         "peripheral": "droop + virtual impedance + C_f-voltage PR loop (control study); per-phase voltage loops in the four-wire build",
         "threshold": ("voltage +/-1 %% (C_f dividers 0.5 %% after calibration), frequency 50 / 60 Hz +/-0.2 %% (crystal clock), THDu < 3 %% on "
                       "linear load, imbalance +/-1 %% and 120 +/-1 deg on linear balanced load, 100 %% unbalanced load (four-wire only), "
                       "DC component < 0.5 %% Un (row below); load acceptance: the declaration (crest factor <= %.2f at rated rms, DOL "
                       "motors <= %.0f A rated at 6 x start); transient: the declared envelope (row below)" % (i_lim_pk / I_RATED, dol)),
         "filter": "-", "latency": "-", "self_test": "-"},
        *vfr,
        {"item": "DC-component regulator (VF, half-wave loads)",
         "peripheral": ("one-cycle mean of each phase-to-N voltage (VG_x - VGN; three-wire: the line-to-line means) -> integrator -> "
                        "offset on that phase's reference; the N leg carries the DC current"),
         "threshold": ("|DC| <= 0.5 %% Un = %.2f V; measurement after calibration: divider TCR mismatch %.2f V at 750 V (calculated) plus "
                       "the ADC gain drift of the two channels - pair VG_x and VGN on one ADC module or null them at no load (OPEN); "
                       "half-wave load <= %.0f A peak (%.0f A DC, %.0f A rms) per phase: L_N's peak within the L1 part's %.0f A"
                       % (hw["dc_component_limit_V"], hw["dc_measurement_divider_V"], hw["I_pk_limit_A"], hw["I_dc_A"], hw["I_rms_A"], l1p)),
         "filter": "one grid period", "latency": "10 grid periods", "self_test": "-"},
        {"item": "generator-following (small diesel genset)",
         "peripheral": "grid-following on the genset's voltage with a power envelope from the measured frequency / voltage",
         "threshold": ("frequency window 45-55 Hz (60 Hz: 55-65 Hz), ROCOF ride-through <= 4 Hz/s, voltage 0.85-1.10 Un; no reverse power: "
                       "the genset keeps >= 30 %% of its rating (wet-stacking floor), so discharge <= P_load - 0.3 P_gen and charge <= 0.9 "
                       "P_gen - P_load; power ramps <= 10 %% of P_gen per s; P(f) droop following the genset's 4 %%; %s" % claim),
         "filter": "-", "latency": "-", "self_test": "-"},
        {"item": "LVRT / HVRT ride-through profiles",
         "peripheral": "grid-code parameter sets (EN 50549-1, VDE-AR-N 4105, GB/T 34120 class) selected per country",
         "threshold": ("LVRT: 0 pu for 150 ms, 0.2 pu to 625 ms, linear to 0.9 pu at 2 s, reactive current k = 1.5 (dI_q / dU) up to 1.0 Ir; "
                       "HVRT: 1.2 pu 10 s, 1.25 pu 1 s, 1.3 pu 0.5 s (absorbing reactive current) - FROM MEMORY; rated current stays "
                       "inside the %.0f A hardware window by the ride-through onset measure (row below), the stiff-grid derating as its "
                       "fallback (the row after); 1.3 pu needs V_dc above the operating map at 520 V; %s" % (rt["thr_A"], claim)),
         "filter": "-", "latency": "-", "self_test": "-"},
        {"item": "ride-through onset measure (review R2-05; adopted)",
         "peripheral": ("the ride-through state and the per-sample predictive clamp in the PWM update (per phase, the current one period "
                        "after the new command; deadbeat correction with unit gain, PR anti-windup by back-calculation)"),
         "threshold": rt["measure"], "filter": "-", "latency": "every control period", "self_test": "-"},
        {"item": "stiff-grid deep-dip derating with the grid-stiffness estimate (FALLBACK of the onset measure; review R2-05)",
         "peripheral": ("pre-dip power limit from the grid-stiffness estimate (the same estimate selects the CV-mode DC/DC limit); not "
                        "applied while the onset measure is in force"),
         "threshold": rt["fallback"] + "; " + rt["installation"], "filter": "the estimate's averaging", "latency": "-", "self_test": "-"},
        {"item": "anti-islanding", "peripheral": "U / f windows, ROCOF, vector shift + an active frequency shift",
         "threshold": "cease to energise within 2 s (IEC 62116 class, FROM MEMORY; not simulated); %s" % claim,
         "filter": "-", "latency": "-", "self_test": "-"},
        {"item": "charge <-> discharge transfer < 20 ms",
         "peripheral": "PQ / CP / CC: current-reference reversal through a ramp; CV: the DC-link loop",
         "threshold": ("ramp <= 10 ms (ASSUMED setting) + the current loop's settling to 5 %% for a 0 -> rated step (control study: %s) -> "
                       "<= %.1f ms; CV: 40 Hz crossover, about 12 ms to 5 %% (estimate 3 / (2 pi 40 Hz))"
                       % (", ".join("%s %.1f ms" % kv for kv in steps.items()) or "control study not run yet",
                          10.0 + max(steps.values() or [0.0]))),
         "filter": "-", "latency": "-", "self_test": "-"},
        {"item": "grid-to-island transfer (unplanned)",
         "peripheral": "islanding detection, cease to energise, the site's grid-tie switch opens, GFL -> GFM on the island",
         "threshold": ("automatic, with an interruption of about %.0f ms = detection %.0f ms (ASSUMED) + the grid-tie contactor's drop-out "
                       "%.0f ms (CHINT NXC-225 class with its DC coil module, ASSUMED - the RFQ class of our AC contactors); planned: "
                       "seamless (ramp the exchange to zero, GFM before opening - control study); a seamless unplanned transfer needs a "
                       "static transfer switch: out of scope (REQUIREMENTS: not STS); competitor: by hand"
                       % ((T_ISLAND_DETECT + T_AC_OPEN) * 1e3, T_ISLAND_DETECT * 1e3, T_AC_OPEN * 1e3)),
         "filter": "-", "latency": "-", "self_test": "-"},
        {"item": "parallel operation of modules on one AC bus",
         "peripheral": ("GFL: independent current sources, the EMS splits P / Q (shared set point); GFM: P-f / Q-V droop; the module bus "
                        "= the control board's CAN (paralleling, carrier synchronisation by CAN time-stamping, software addressing) and "
                        "zero-sequence circulating-current control on a shared battery; no hardware sync pair fitted"),
         "threshold": ("load sharing +/-5 %% of rated by droop, +/-2 %% with the shared CAN set point; carrier jitter +/-2 us (ASSUMED); up "
                       "to 16 modules on one CAN (ASSUMED bus load); competitor: %s" % PMA["parallel"]),
         "filter": "-", "latency": "-", "self_test": "-"},
        {"item": "grid start (DC side dead): AC precharge and closing (the executed state machine, review R2-14)",
         "peripheral": ("K_ACPRE = PWM16 line (latch-gated, PCS-PWR relay driver); K2, K1, K_B sequence of pcs_spec ac_start (states %s; "
                        "%d fault cases executed by the control study, no safety breach with the repairs H1-H5); the attempt count, its "
                        "spacing and the lock-out in the recorder flash, checked before every attempt (H5); a stop with the bridge "
                        "running goes through AC_STOP, never CONTROLLED STOP (which holds K_DC)"
                        % (", ".join(k for k in sm["outputs"] if k.startswith("AC_") or k in ("RECTIFY", "DC_MATCH", "RETRY_WAIT", "LOCKOUT")),
                           len(sm["fault_cases"]))),
         "threshold": ("precharge to the tap peak minus 2 V_F: %.2f s to 10 V at 400 V (%.2f s at 340 V), %.0f J in 220 ohm at 460 V (rating "
                       "%.0f J), shorted bank %.0f J in %.2f s (rating %.0f J in %.2f s); K2 / K1 each commanded only with K_B open, "
                       "V_dc >= 0.95 x sqrt2 V_LL,meas and the grid inside +/-10 %% at the command (H3); K_B only with VBX >= 1.05 x sqrt2 "
                       "V_LL,meas, after the bridge has raised the link to VBX (H4)"
                       % (st["precharge"]["by_build"]["4W"]["400"]["t_to_10V_s"], st["precharge"]["by_build"]["4W"]["340"]["t_to_10V_s"],
                          st["precharge"]["by_build"]["4W"]["460"]["E_pre_J"], rr["e_charge_J"], st["precharge"]["E_pre_shorted_J"],
                          st["precharge"]["t_abort_s"], rr["e_short_J"], rr["t_short_s"])),
         "filter": "-", "latency": "-",
         "self_test": ("welded check: with K_ACPRE off and the grid present, V_dc stays < 50 V; at a retry it decays at the bleeder rate "
                       "and sits below the tap peak (H2); relay test K2 alone / K1 alone onto the precharged link, after the AC precharge (H1)")},
        {"item": "local fault recorder", "peripheral": "the control board's recorder flash (GD25Q32 class)",
         "threshold": ("the control board's figure: 71 events of 32 channels x 200 ms at 3.6 kHz beside two firmware images; each event with "
                       "its cause, the latch state and a time stamp (from the EMS over Modbus TCP or the module bus)"),
         "filter": "-", "latency": "-", "self_test": "flash ID / CRC at power-up"},
        {"item": "authenticated remote upgrade",
         "peripheral": "the control board's authenticated dual-image upgrade over Ethernet / RS-485 / CAN",
         "threshold": ("signed image with CRC, staging slot + golden image in the recorder flash, bootloader in a DCSM-protected sector "
                       "(the control board's declaration); this module adds: installed only in SERVICE with authorisation and every "
                       "contactor open, roll-back to the golden image on a failed check"), "filter": "-", "latency": "-",
         "self_test": "boot: image signature and CRC"},
        {"item": "Modbus TCP map scope",
         "peripheral": ("EMS = Ethernet (Modbus TCP, port 502) or RS-485; BMS = RS-485 (Modbus RTU) or CAN when not paralleled; module bus "
                        "= CAN; service RS-485 at 9600-8-N-1 (the control board's port roles)"),
         "threshold": ("input registers: the telemetry set; holding registers: modes, set points, ramps (range-checked); parameters: SERVICE "
                       "only, authenticated; files: event log, fault records, firmware image - at least the competitor's set (pcs_spec "
                       "declarations parameter_set / telemetry)"), "filter": "-", "latency": "-", "self_test": "-"}])
    return {"four_wire": four, "ac_start": st, "declarations": decl}
def report_i(r):
    fw, st, de = r["four_wire"], r["ac_start"], r["declarations"]
    w = fw["window"]
    wr("\n## (i) Four-wire build, AC-side start-up, declarations (owner 2026-10-06: the competitor's PMA0125 feature set)\n")
    wr("Everything CALCULATED with this study's models; ASSUMED values labelled; the competitor's figures are its published claims "
       "(docs/reference-designs/megarevo/pma/spec-pma0125.md, MANUAL-NOTES.md), not verified.")
    d = fw["decision_cf_star"]
    wr(f"\n**C_f star in the four-wire build: {d['star']}.** {d['why']}.  The N leg's ripple ({fw['filter']['N_leg_ripple_pp_950V_A']:.1f} A pp "
       f"at 950 V) then returns locally through C_fN ({fw['filter']['C_fN_ripple_V_pp_950V']:.1f} V pp on it); L_N - C_fN resonate at "
       f"{fw['filter']['L_N_C_fN_resonance_Hz']:.0f} Hz (the damping branch is copied from the phases; {ctl_n_leg()}).")
    wr(f"\n**DC window, two ways.** {w['basis']}.\n")
    wr("| grid L-N | bare 2 sqrt2 V_LN | mode A: close / rated current (worst PF) | mode B: close / rated current (worst PF) |")
    wr("|---|---|---|---|")
    for k, x in w["rows"].items():
        a, b = x["mode_A"], x["mode_B"]
        wr(f"| {x['V_LN']:.1f} V ({k}) | {x['bare_2sqrt2_V_LN']:.0f} V | {a['closing (no load)']:.0f} / {max(a[p] for p in PF_CASES):.0f} V | "
           f"{b['closing (no load)']:.0f} / {max(b[p] for p in PF_CASES):.0f} V |")
    wr(f"\nMode A reaches a grid of {w['mode_A_reach_V_LN']['650 V']:.0f} V L-N at 650 V DC and {w['mode_A_reach_V_LN']['680 V']:.0f} V at 680 V "
       f"(no load): the competitor's 650 / 680 V for 3W+N+PE is its split-capacitor neutral with no headroom (2 sqrt2 x 230 V = 650.5 V); "
       f"with our map's margins mode A needs the figures above.  Mode B gives the three-wire window: the four-wire build's advantage "
       f"(calculated).  {w['baseline']}.")
    wr("\n| grid type | competitor operating / full load (V) | ours operating / full load from (V, at 400 / 230 V) |")
    wr("|---|---|---|")
    for k, x in w["windows_at_400_230V"].items():
        c = x["competitor"]
        wr(f"| {k} | {c[0]:.0f}-{c[1]:.0f} / {c[2]:.0f}-{c[3]:.0f} | {x['operating_from']:.0f}-{x['operating_to']:.0f} / "
           f"{x['full_load_from']:.0f}-{x['full_load_to']:.0f} |")
    m = fw["midpoint_neutral_alternative"]
    e = m["electrolytic"]
    wr(f"\n**Why not the competitor's split-capacitor neutral for us:** the neutral current charges both halves in parallel "
       f"({m['C_mid_uF']:.0f} uF at M): 50 Hz ripple " + ", ".join(f"{k}: {v:.0f} V peak" for k, v in m["dV_peak_V"].items())
       + f" on top of {m['V_half_nominal_750V']:.0f} V per half at 750 V, against the {m['half_OV_trip_V']:.0f} V per-half trip - that trip is "
       f"reached at {m['I_N_rms_at_half_trip_A']:.0f} A rms of neutral current.  Holding the ripple to {MID_RIPPLE_FRAC*100:.0f} % of V_dc/2 "
       f"(ASSUMED, {m['limit_V']:.1f} V) needs {m['C_needed_mF']:.1f} mF at M ({m['C_needed_per_half_mF']:.1f} mF per half): {e['cans']} x "
       f"{e['part']} ({e['strings_per_half']} strings of two per half; {e['by_capacitance']} for the capacitance, {e['by_ripple_current']} for "
       f"the 50 Hz ripple), {e['usd_cat']:.0f} / {e['usd_5k']:.0f} USD and {e['life_h_105C']:.0f} h at 105 C - against the fourth leg's "
       f"{m['fourth_leg_increment_usd']['cat']:.0f} / {m['fourth_leg_increment_usd']['5k']:.0f} USD (catalogue / 5k).")
    u = fw["unbalance"]
    wr(f"\n**Unbalance and neutral current:** per phase at 230 V " + ", ".join(f"{k} {v:.1f} kW" for k, v in u["per_phase_kW_at_230V"].items())
       + f"; 100 % unbalance = one phase loaded, I_N = I; half the module power on one phase would be {u['single_phase_full_module_power_A']:.0f} A "
       f"- above every tier, so the per-phase power is capped by the phase tiers (the PMA's 100 % unbalanced load is one phase at its own "
       f"rating, not 50 % of the module on one phase).  N leg (mode A, its own heatsink section) against a phase leg at the same current "
       f"(PF 1):\n")
    wr("| case | N leg W | N leg Tj (C) | phase leg Tj (C) |")
    wr("|---|---|---|---|")
    for k, x in u["n_leg"].items():
        wr(f"| {k} | {x['leg_W']:.0f} | {x['tj_max_C']:.0f} | {u['phase_leg_same_current_tj_C'][k]:.0f} |")
    dl = fw["dc_link"]
    wr(f"\n**DC link with the neutral current** (four-leg switching model; LF to the battery; per-half HF + the phases' C_f-star current and the "
       f"N leg's ripple through C_fN, added in phase): per capacitor up to {dl['per_cap_max_A']:.1f} A against {dl['per_cap_limit_A']:.1f} A "
       f"(three-wire {dl['three_wire_per_cap_A']:.1f} A); bus {dl['C_total_uF']:.1f} uF with the fourth leg's films.\n")
    wr("| V_dc | phase currents (A rms, angle) | I_N (A rms) | per-half HF (A) | C_f / C_fN share per half (A) | per cap (A) | battery LF (A rms) |")
    wr("|---|---|---|---|---|---|---|")
    for x in dl["cases"]:
        wr(f"| {x['vdc']:.0f} | {' / '.join('%.0f' % a for a in x['amps'])} at {x['ang']:.0f} deg | {x['I_N_rms_A']:.0f} | {x['hf_half_A']:.0f} | "
           f"{x['cm_half_A']:.0f} | {x['per_cap_A']:.1f} | {x['battery_lf_A_rms']:.0f} |")
    h = fw["half_wave"]
    wr(f"\n**Half-wave load (off-grid, four-wire):** {h['basis']}: <= {h['I_pk_limit_A']:.0f} A peak per phase ({h['I_dc_A']:.0f} A DC, "
       f"{h['I_rms_A']:.0f} A rms, {h['P_mean_kW']:.1f} kW), L_N peak {h['L_N_peak_A']:.0f} A = the L1 part's {h['L1_part_continuous_peak_A']:.0f} A; "
       f"battery LF {h['battery_lf_A_rms_750V']:.0f} A rms at 750 V; output DC component limit {h['dc_component_limit_V']:.2f} V (0.5 % Un), the "
       f"dividers' calibrated mismatch {h['dc_measurement_divider_V']:.2f} V.  Midpoint: {fw['midpoint_lf']['I_rms_A_at_2pct_zero_sequence']:.2f} A "
       f"rms at a 2 % zero-sequence grid voltage, {fw['midpoint_lf']['ripple_V_peak']:.1f} V peak - {fw['midpoint_lf']['balancing']}.")
    g = fw["grid_hf"]
    wr("\n**Grid-side carrier harmonics, three- against four-wire** (this study's four-wire model: each phase sees its leg against the "
       "midpoint, so the legs' common-mode carrier components reach the grid through N; the N leg's own ripple, which partly cancels "
       "them, is not credited): largest component above h50 in % of the rated peak - " +
       "; ".join(f"{k}: {x['pct_of_rated']:.2f} % at h{x['h']}" for k, x in g.items()) +
       f".  Target {I2_LIM_FRAC*100:.1f} % (0.075 % for even orders; step c): the four-wire build carries the carrier group to the grid on a "
       f"stiff connection; at SCR 50 the grid inductance holds it - a pre-compliance item for the four-wire build (more L2 or the N-leg "
       f"modulation), not drawn.")
    b, p = fw["bleeder"], fw["precharge"]
    wr(f"\n**Four-wire DC link:** bleeder {b['n_elem']} x {b['R_elem_ohm']/1e3:.1f} k per half: 60 V after {b['t60_worst_min']:.1f} min worst case "
       f"(label {b['label_min']:.0f} min); DC precharge (RPRE_AL) {p['E_J_max']:.0f} J at tau {p['tau_max_s']*1e3:.0f} ms, shorted {p['E_short_J']:.0f} J "
       f"in {p['t_short_s']:.3f} s against the shared rating {p['rating']['e_charge_J']:.0f} J at {p['rating']['tau_s']*1e3:.0f} ms / "
       f"{p['rating']['e_short_J']:.0f} J in {p['rating']['t_short_s']:.2f} s with the shared {p['rating']['R']:.0f} ohm.  "
       + (f"The time constant is above the rating point, so this build takes the next lower E24 value (RPRE_AL is RFQ: no data sheet, "
          f"no pulse curve to stretch the rating): {p['four_wire_R']['R_ohm']:.0f} ohm gives {p['four_wire_R']['E_J_max']:.0f} J at tau "
          f"{p['four_wire_R']['tau_max_s']*1e3:.0f} ms, shorted {p['four_wire_R']['E_short_J']:.0f} J in {p['four_wire_R']['t_short_s']:.3f} s, "
          f"{p['four_wire_R']['I_pk_A']:.2f} A peak from the 1050 V trip (G7L-2A-X {p['four_wire_R']['relay_A']:.0f} A at 1000 V DC, two "
          f"poles in series), 10 V in {p['four_wire_R']['t_to_10V_max_s']:.2f} s worst case - inside the same RPRE_AL specification. "
          f"{p['four_wire_R']['board']}." if p['four_wire_R']['R_ohm'] != p['rating']['R'] else ""))
    t, pc = st["tap"], st["precharge"]
    wr(f"\n**AC-side start-up (both builds).** Tap: {t['fuse']} ({t['fuse_In_A']:.1f} A, 500 V AC, 200 A interrupting) behind {t['R_lim_ohm']:.0f} ohm "
       f"per phase, 6 x {t['diode']}, OR diode into the aux; rectified line {t['rectified_V']['340 V'][0]:.0f}-{t['rectified_V']['460 V'][1]:.0f} V "
       f"({t['aux_input_range_V']}).  Bulk inrush {t['bulk_inrush']['I_pk_A']:.1f} A, {t['bulk_inrush']['I2t_A2s']:.3f} A2s "
       f"({100*t['bulk_inrush']['I2t_A2s']/t['fuse_i2t_melt_A2s']:.1f} % of the fuse's melting I2t); a shorted tap diode draws "
       f"{t['fault_L_L']['I_rms_A']:.1f} A rms, the fuse melts in {t['fault_L_L']['t_melt_s']*1e3:.0f} ms, {t['fault_L_L']['E_per_R_J']:.0f} J per "
       f"20 ohm (pulse rating {t['fault_L_L']['R_pulse_J']:.0f} J).  {t['domain']}.")
    wr("\n| build | grid | peak A | E in 220 ohm (J) | E per 20 ohm (J) | fuse I2t (A2s, % of melting) | to 10 V (s) |")
    wr("|---|---|---|---|---|---|---|")
    for bld, rows in pc["by_build"].items():
        for v, x in rows.items():
            wr(f"| {bld} | {v} V | {x['I_pk_A']:.2f} | {x['E_pre_J']:.0f} | {x['E_lim_J']:.1f} | {x['I2t_fuse_A2s']:.3f} "
               f"({100*x['I2t_fuse_A2s']/TAP['fuse_i2t_melt']:.0f} %) | {x['t_to_10V_s']:.2f} |")
    wr(f"\nShorted bank (worst, 460 V): {pc['P_pre_shorted_W']:.0f} W in 220 ohm until the abort at {pc['t_abort_s']:.2f} s = "
       f"{pc['E_pre_shorted_J']:.0f} J (rating {pc['rating']['e_short_J']:.0f} J in {pc['rating']['t_short_s']:.2f} s: a quarter of the "
       f"energy over a slightly longer time - ASSUMED milder for the RFQ part).\n")
    wr("Sequence (grid present, DC side dead):")
    for k, x in enumerate(st["sequence"], 1):
        wr(f"{k}. {x}")
    wr("\nInterlocks: " + "; ".join(st["interlocks"]) + ".")
    wr("\nConsequences: " + "; ".join(st["consequences"]) + ".")
    m = st["state_machine"]
    wr(f"\n**The executed AC-start machine** ({m['source']}): outputs ({', '.join(m['outputs_order'])}) per state - " + "; ".join(
        f"{k} ({', '.join(str(v) for v in o.values())})" for k, o in m["outputs"].items()) + ".  Stop routes of the AC start: " + ", ".join(
        f"{k} -> {v}" for k, v in m["stop_routes"].items()) + f".  The {len(m['invariants'])} invariants of the checked table: "
       + ("all hold" if all(m["invariants"].values()) else "FAIL") + ".  Repairs, in the control study's words: "
       + " ".join(f"**{h}** {t}." for h, t in m["repairs"].items()) + "  Before the repairs the sequence breached: "
       + "; ".join(m["breaches_as_specified"]) + "; with them no case breaches a safety check (the control study's modelled set):\n")
    wr("| fault | strikes at | outcome with the repairs | breaches | precharge attempts |")
    wr("|---|---|---|---|---|")
    for c in m["fault_cases"]:
        wr(f"| {c['fault']} | {c['at']} | {c['outcome']} | {'; '.join(c['breaches']) or '-'} | {c['precharge_attempts']} |")
    wr("\nRelay pull-in / release times of the executed machine (ASSUMED until the RFQ coil data exist): " + ", ".join(
        f"{k} {v[0] * 1e3:.0f} / {v[1] * 1e3:.0f} ms" for k, v in m["relay_times_s_ASSUMED"].items()) + ".")
    s = de["ac_short_circuit"]
    wr(f"\n**AC short-circuit declaration.** {s['module']}.  Grid-fed bolted leg short through L2 + L1 (unsaturated; L1 holds its inductance to "
       f"{s['I_L1_sat_100C_A']:.0f} A at 100 C): " + "; ".join(f"{k}: {x['I_prospective_unsaturated_A_rms']:.0f} A rms ({x['I_peak_asymmetric_A']:.0f} A "
                                                                 f"peak), L1 saturated after {x['t_to_L1_saturation_ms']:.2f} ms"
                                                                 for k, x in s["rows"].items())
       + f" - the filter gives the upstream protection well under 2 ms; after that the installation's prospective current flows.  "
         f"{s['contactors']}.  Installation requirement: {s['installation']}.  Competitor: {s['competitor']}.")
    pr = de["stabilized_precision"]
    bk = list(pr["voltage"])[1]
    wr(f"\n**Stabilized precision ({pr['requirement']}):** DC voltage, convention {bk}: " +
       ", ".join(f"{v} V worst {x['worst_pct']:.2f} % / RSS {x['rss_pct']:.2f} %" for v, x in pr["voltage"][bk].items()) +
       "; DC current (rated 125 kW / V_dc): " + ", ".join(f"{v} V ({x['I_rated_A']:.0f} A) worst {x['worst_pct_of_rated']:.2f} % / RSS "
                                                        f"{x['rss_pct_of_rated']:.2f} %" for v, x in pr["current"][bk].items()) +
       f".  Board-check convention (115 K, no re-zero): voltage RSS up to {max(x['rss_pct'] for x in pr['voltage'][list(pr['voltage'])[0]].values()):.2f} %.  "
       f"{pr['closure']}.")
    wr("\n**kVA tiers at 400 V:** " + ", ".join(f"{k} = {v:.1f} kVA" for k, v in de["kva_tiers_400V"].items()) +
       " (the competitor's 150 kVA 'max' is its 216 A 2-min tier, its 137 kVA 'continuous' the 198 A one).")
    a = de["airflow"]
    wr(f"\n**Airflow:** {a['m3h_per_section']:.0f} m3/h per heatsink section: {a['CFM']['three-wire']:.0f} CFM three-wire, "
       f"{a['CFM']['four-wire']:.0f} CFM four-wire ({a['CFM_per_kW_125kW']['three-wire']:.2f} / {a['CFM_per_kW_125kW']['four-wire']:.2f} CFM/kW at "
       f"125 kW) against the competitor's {a['competitor_CFM_per_kW']:.1f} CFM/kW ({a['competitor_scaled_125kW_CFM']:.0f} CFM at 125 kW).")
    ol = de["offgrid_load_acceptance"]
    wr(f"\n**Off-grid load acceptance:** {ol['resistive']}; crest factor <= {ol['crest_factor']['CF_at_rated_rms']:.2f} at rated rms below the "
       f"{ol['crest_factor']['limiter_peak_A']:.0f} A limiter (" + ", ".join(f"CF {k}: {v:.0f} A rms" for k, v in ol["crest_factor"]["rms_at_CF"].items())
       + f"); direct-on-line motors with a 6 x start <= {ol['motor_DOL']['I_n_max_A_at_6x']:.0f} A rated ({ol['motor_DOL']['P_kW_at_0.85_0.92']:.0f} kW) on "
       f"the 2-min tier; half-wave (four-wire) <= {h['I_pk_limit_A']:.0f} A peak per phase.  Competitor: {ol['competitor']}.")
    wr(f"\n**Frequency:** ours {de['frequency']['ours']}; competitor {de['frequency']['competitor']}.  **Environment:** {de['environment']}.  "
       f"**Communication:** {de['communication']}.  **Modes:** {de['modes']}.")
    g = de["grid_strength"]
    wr(f"\n**Grid strength and ride-through ({g['admitted']}):** {g['ride_through']}.  {g['fallback']}.  Installation: "
       f"{g['installation_fault_level']}.  Superseded: {g['superseded']}.  CV mode with DC/DC loads: {g['cv_mode_dc_dc_power']}.")
    wr(f"\n**DC bus without a battery (installation / system rule):** {de['dc_bus_without_battery']}.")
    wr("\n**Parameter set (at least the competitor's):** " + "; ".join(de["parameter_set"]) + ".  **Telemetry:** " + "; ".join(de["telemetry"]) + ".")


def handover(D, rb, rc, rd, re_, rf, rg_):
    """what the control engineer, the board designers and the firmware need next (written to report and spec)"""
    F = rc["filter"]
    ctrl = {
        "plant": {"L1_uH": round(F["L1"] * 1e6, 1), "Cf_uF_star": F["Cf"] * 1e6, "L2_uH": F["L2"] * 1e6, "Rd_ohm": F["Rd"], "Cd_uF": F["Cd"] * 1e6,
                  "R_L1_mohm_est": round((FP["L1"]["loss_per_phase"]["k2"] if FP else BUDGET["L1_cu_W_per_phase_180A"] / I_RATED ** 2) * 1e3, 2), "f_res_Hz": rc["f_res_Hz"],
                  "grid_SCR_range": "5 .. stiff", "C_dc_series_uF": rd["C_series_uF"], "C_dc_total_uF": rd["C_total_uF"],
                  "C_f_star": "tied to the DC midpoint"},
        "sampling": {"f_sw_Hz": FSW_CHOICE, "f_s_Hz": F_S, "delay_s": T_DELAY, "dead_time_ns": re_["dead_time_ns"],
                     "dead_time_error": f"{2 * re_['dead_time_ns'] * 1e-9 * FSW_CHOICE * 100:.1f} % volt-seconds -> compensate (THDi < 3 %)"},
        "current_loop": f"L1 current (sensor on the C_f side of L1): resonance {min(rc['f_res_Hz'].values())/1e3:.1f}-"
                        f"{max(rc['f_res_Hz'].values())/1e3:.1f} kHz is below f_s/6 = {F_S/6e3:.1f} kHz, where converter-current "
                        f"feedback with 1.5 T_s delay is inherently damped.  The filter was sized for a loop of at least "
                        f"{BW_LOOP/1e3:.2f} kHz (resonance at SCR 5 above twice it): that is the lower edge of the usable crossover band, not "
                        "a ceiling - the control study (sim/out/pcs_control/pcs_control_spec.json current_loop) finds 1.0-3.25 kHz meeting "
                        "PM >= 40 deg / GM >= 6 dB from stiff to SCR 5 and takes 1.75 kHz on the stiff grid; a weak grid lowers the closed "
                        "loop to about 0.3 kHz (SCR 5); resonant terms h5/h7 (h11/h13 fit at no admissible crossover); grid current for "
                        "PF/THD = i_L1 - C_f dv_Cf/dt; the passive R_d-C_d branch is required (active damping from the C_f voltage fails at "
                        "the stiff grid in that study)",
        "pll": "on the C_f (or terminal) voltages, 400 V +-15 %, 50/60 Hz, SCR 5..stiff, unbalance and LVRT/HVRT (EN 50549-1 / GB/T 34120)",
        "modulation": ("two-level, min-max zero sequence (SVPWM-equivalent) at every operating point (section h)" if MOD_2L == "minmax" else
                       "two-level, sinusoidal where m <= 0.98, min-max zero sequence above (V_dc < ~680 V at 400 V, < ~780 V at 460 V)"),
        "midpoint": f"no control (two-level); firmware plausibility of the half voltages; C_f-star 150 Hz current <= {rd['cf_star_lf_cm_A_max']:.0f} A "
                    f"rms, ripple <= {max(rd['midpoint_pp_V'].values()):.0f} V pp",
        "grid_forming": f"voltage control on C_f (L1-C_f with the damping branch), current limit 1.2 x 216 A for 200 ms (Tj "
                        f"{max(o['tj_200ms_C'] for o in rb['overload']):.0f} C at the worst corner, step b), 120 % for 2 min, transitions grid "
                        f"<-> off-grid < 20 ms (Megarevo)",
        "four_wire": (f"neutral leg two-level with L_N = {F['L1']*1e6:.0f} uH (the L1 part) into N_F, C_fN {F['Cf']*1e6:.0f} uF + R_d / C_d from "
                      "N_F to M (the C_f star stays on M, step i); mode A (N leg at 50 %, reference L_N di_N/dt + a v(N_F - M) loop on VGN - VA) "
                      "where the phase peak fits V_dc / 2, mode B (the min-max zero sequence on all four legs) below; neutral current = "
                      "-(i_a + i_b + i_c) limited to the phase tiers; 100 Hz battery current of single-phase load (~39 A rms at 750 V) is an "
                      "installation item; " + ctl_n_leg()),
        "limits": {"I_phase_trip_A": OC_TRIP, "I_dc_trip_A": 400.0, "V_dc_trip_V": V_OV_TRIP}}
    ra, pcn = re_["desat"]["rds_acceptance"], SPEC["protection_chain"]
    boards = {
        "device_acceptance": SHARE_RULE,
        "device_acceptance_absolute": ra["absolute_limit"]["rule"],
        "power_stage": f"3 two-level legs (4 for four-wire): 6 x {D['devs']['TH'][0]} per switch (TO-247-4L, Kelvin source), Al2O3 pads on "
                       "one earthed section per leg; per device pair 3 x 2.2 uF / 1300 V film at the pins + RC damper (2 x 4.7 nF 2 kV C0G, "
                       "6 x 15 ohm 2512); DC link 5 + 5 x C3D1U147 in two series halves, midpoint to the C_f star",
        "gate_drive": f"6 channels (8 four-wire): NSI6651ASC + NPN/PNP buffer per channel driving 6 gates, R_G,on {re_['chosen']['R_G_on_ext_ohm_per_device']:.2f} / "
                      f"R_G,off {re_['chosen']['R_G_off_ext_ohm_per_device']:g} ohm per device, R_KS 0.5 ohm, per-gate clamp FET, DESAT 100 ohm + 2 x US1MH (PCM-07), "
                      "booster, RC dead-time stretch and negative-rail detector (stretch=True, neg_det=True - new '6 x SG2M040170HJ' "
                      "preset needed), rails +18 / -3.5 V; bias: one SN6505B transformer per phase (2 secondaries); default-off pull-downs; "
                      "EN from the external stop (wired-AND); FLT/RDY to the latch and the trip zone (D-050)",
        "control_board": "PCS-CTL as drawn (gen/pcs_ctrl.py: the PV-CTL design with the PCS_X header), assemblies PCS-CTL-3W / PCS-CTL-4W: discrete "
                         "window comparators for each phase current (ladders re-valued for +-450 A on the STK-250HO/4), DC over-voltage 1050 V, "
                         "over-temperature and open probe, the set-dominant latch, heartbeat watchdog, AND gating of 6 (8) PWM, EN, K_DC, "
                         "K_PRE, K_AC1 and K_AC2 (a spare LVC08 gate) and K_ACPRE (the PWM16 line); the controller's CMPSS / ADC limits / trip "
                         "zone as the second layer",
        "sensing": {"phase_current": "3 (4) Sinomags %s (open-loop Hall, I_PN %.0f A, +-%.0f A, %.1f mV/A around its Uref pin, %.0f kHz, <= %.0f us "
                                     "step response; pcs_spec phase_current_sensor, PCM-20) on the C_f side of L1, the busbar through its "
                                     "aperture; windows at +-%.0f A" % (PH_SENSOR["mpn"], PH_SENSOR["I_PN_A"], PH_SENSOR["linear_range_A"],
                                                                         PH_SENSOR["gain_mV_per_A"], PH_SENSOR["bandwidth_Hz"] / 1e3,
                                                                         PH_SENSOR["step_response_s"] * 1e6, OC_TRIP),
                    "grid_voltage": "terminal and C_f nodes, L1-L3 (+N), dividers to DC-: signal V_dc/2 +-375 V peak + 20 % surge headroom",
                    "dc": "V_DC+ - V_DC-, V_mid, terminal (bipolar, polarity), shunt 2 x 200 uOhm (25 mV at 250 A), ADC limit trips 400 A / 1050 V",
                    "residual_current": "type-B fluxgate over L1-L3 (+N), 30 mA resolution, 1.25 A continuous range",
                    "residual_current_contract": RCM_CONTRACT,
                    "temperatures": "NTC: 3 (4) heatsink sections, 3 (4) L1, DC link, inlet"},
        "ports": "DC: HFE82V-300C/1000, 2 x aR 400 A, precharge 220 ohm, gen/port.py lean_port interlocks='full', oc_trip=True (hold-off, "
                 "polarity and precharge-dV interlocks, over-current window, all discrete); AC: 2 x 3-pole contactor in series (4-pole "
                 "four-wire), coil drivers with economiser gated by the latch, type II SPD, CM choke >= 150 uH (3 nanocrystalline cores); "
                 "AC start-up (step i, both builds): 6-diode grid tap behind 20 ohm + 1.6 A fuses into the aux, AC precharge relay "
                 "(G7L-2A-X + 220 ohm, latch-gated, PWM16 line) from the tap to DC+"}
    fw = ["firmware practice adopted from the Wolfspeed firmware cross-check (docs/requirements/REFERENCE-LESSONS.md section 6, R-WS-1..9, "
          "2026-10-05): range-checked bus/service inputs (no writable switching frequency or dead time in operation); ramp to zero and stop "
          "on communication loss (grid-following: fallback to zero power; grid-forming keeps forming); lock-out after the third hazard trip "
          "of a class within 10 min (assumed) and at once when hardware and firmware both see an over-voltage; explicit recovery threshold, "
          "dwell and restart-rate limit per non-latched limit; every exception path forces the one-shot trip and stops the heartbeat, "
          "clock-fail and emulation-stop trips enabled; the start-up self-test fires each fault line alone and the 10 ms read-back covers "
          "the trip routing; no bus-reachable mode disables a protection, parameter writes only in SERVICE with authorisation and timeout; "
          "redundant range-checked calibration whose failed load blocks the start",
          "second layer of every discrete trip (D-050): CMPSS phase windows +-450 A (digital filter <= 0.5 us), ADC limits I_dc 400 A and "
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
    # PCM-04 / 05 / 06 / 14: limits the firmware must enforce, as rows of the control board's firmware table (gen/pcs_ctrl.py prints them)
    om, sh = SPEC["operating_map"], SPEC["thermal_and_losses"]["sharing_population"]
    rect = om["rectification"]
    r460, r460g = rect["460 V AC, 590 V DC, stiff grid"], rect["460 V AC, 590 V DC, SCR 5"]
    vmn = om["vdc_min_at_rated_current_V"]
    perm = {v: vmn[f"{v} V 50 Hz"]["closing permissive (no load, synchronised close)"] for v in (400, 460)}
    tiers = om["overload_tiers"]
    rows = [SPEC["dc_link"]["upper_half"]["firmware_limit"],
            {"item": "modulation-index limiter and operating map",
             "peripheral": ("control ISR: m = sqrt(2)|V_c*| / (V_dc/2) clamped at %.4f (0.98 x 2/sqrt(3) less 2 t_d f_sw dead-time "
                            "compensation); current references held inside the operating map's admissible current at the measured V_dc / "
                            "V_ac / f (P or Q priority by setting) before the clamp is reached" % om["m_usable"]),
             "threshold": "pcs_spec operating_map (5 %% loop headroom kept); e.g. rated current at PF 0, Q > 0 needs %.0f V DC at 400 V, %.0f V at 460 V"
                          % (vmn["400 V 50 Hz"][list(PF_CASES)[2]], vmn["460 V 50 Hz"][list(PF_CASES)[2]]),
             "filter": "-", "latency": "every control period (%.1f us)" % (1e6 / F_S), "self_test": "-"},
            {"item": "AC contactor closing permissive",
             "peripheral": ("VB against the line-to-line peak of VG1-3 (terminal side) before K_A_M / K_AC2_M go high - through the relay "
                            "test and the final close; otherwise no close (the bridge would rectify into the battery)"),
             "threshold": "V_dc >= 1.02 x sqrt(2) x V_LL,rms(measured) + 10 V and >= the map's V_dc for the commanded P / Q: %.0f V at 400 V, %.0f V at 460 V (no load)"
                          % (perm[400], perm[460]),
             "filter": "one grid period", "latency": "-", "self_test": "-"},
            {"item": "V_dc below the rectification threshold while running",
             "peripheral": ("outer loop: V_dc < 1.05 x sqrt(2) x V_LL,rms(measured) (the map's no-load limit without the loop headroom) -> "
                            "controlled stop (current to zero within one grid period, both AC contactors open at zero current); V_dc < "
                            "sqrt(2) x V_LL,rms + 5 V (the body diodes conduct) -> one-shot on all ePWM, K_A_M and K_AC2_M low (both break "
                            "the pulsed diode current at its zeros), K_B kept unless the port's hardware over-current window (387-413 A) "
                            "opens it"),
             "threshold": ("meanwhile the body-diode rectifier drives %.0f A peak / %.0f A mean DC (460 V AC on 590 V, stiff grid, L1 "
                           "saturation and R not modelled; %.0f / %.0f A at SCR 5) for <= %.0f ms (AC contactor release, ASSUMED); above 387-413 A "
                           "DC the port window opens the DC contactor first (<= 10 ms release)" % (r460["I_peak_A"], r460["I_dc_mean_A"],
                                                                                                r460g["I_peak_A"], r460g["I_dc_mean_A"],
                                                                                                T_AC_OPEN * 1e3)),
             "filter": "2 outer-loop results", "latency": "<= %.0f us + the contactor release" % ((2 * T_OUTER + T_CONV) * 1e6), "self_test": "-"},
            {"item": "current limit and overload tiers",
             "peripheral": ("rms of IL1-3 over each grid period, one timer / I2t per tier; reference limited to min(tier, %.0f kVA / "
                            "(sqrt(3) V_ac), the operating map); inlet above 45 C: derated tiers" % om["S_max_kVA"]),
             "threshold": "; ".join("%s %.0f A = %s kVA at 340 / 400 / 460 V" % (k, t["I_A"], " / ".join("%.0f" % t["kVA"][v] for v in ("340", "400", "460")))
                                    for k, t in tiers.items()) + "; at 60 C inlet: " + ", ".join("%s %.0f A" % (k, a) for k, a in sh["derated_60C_A"].items()),
             "filter": "one grid period", "latency": "per period", "self_test": "-"},
            {"item": "contactor coil sequencing (live 24 V budget, PCS-PWR)",
             "peripheral": ("K_PRE_M, K_B_M, K_A_M, K_AC2_M: never two coils pulling in at once; the DC contactor pulls in at the end of the "
                            "precharge with the gates idle; the second AC contactor only after the first has settled on its economiser; a coil "
                            "that does not reach its state (relay test VG / VC, the bank following the terminal) is released and retried "
                            "after >= 10 s (ASSUMED: coil module and aux recovery), at most 3 attempts per start, then lock-out with the cause "
                            "logged until a reset; a welded contact found by the relay test locks out at once; the DC precharge keeps the "
                            "port's 3 attempts / 30 s lock-out (port_spec precharge)"),
             "threshold": "settled = pull-in window (AC coil module: ASSUMED <= 100 ms) + 100 ms; budget and limits in the PCS-PWR design check",
             "filter": "-", "latency": "-", "self_test": "-"},
            {"item": "CMPSS phase-current backup threshold (review R2-03, pcs_spec protection_chain)",
             "peripheral": ("CMPSS1-4 DACH / DACL on ILn_ADC after the idle null: the backup band must end below the deck's admissible "
                            "current - as first drawn (+/-%.0f A -> %.1f-%.1f A) it did not (%s), the DAC band of D-078 closes it; the band then overlaps the discrete window, "
                            "whichever trips first turns the gates off, the trip cause is logged from the OST flag and the latch" % (
                                pcn["backup_as_drawn"]["dac_A"], *pcn["backup_as_drawn"]["band_A"],
                                "L1 saturates on the way" if pcn["backup_as_drawn"]["L1_saturates"] else
                                "%.0f V at %.0f A" % (pcn["backup_as_drawn"]["v_pk_V"], pcn["backup_as_drawn"]["I_gates_off_A"]))),
             "threshold": ("band top <= %.1f A, bottom >= %.1f A (DAC centre <= about %.0f A): gates off <= %.1f A <= the admissible "
                           "%.0f A - adopted on PCS-CTL (D-078)" % (
                               pcn["backup_requirement"]["band_top_max_A"], pcn["backup_requirement"]["band_bottom_min_A"],
                               pcn["backup_requirement"]["dac_centre_max_A_estimate"], pcn["backup_requirement"]["I_gates_off_A"],
                               pcn["I_max_admissible_A"])),
             "filter": "digital filter 5 of 5 SYSCLK (as the PCS-CTL check)", "latency": "%.2f us to gates off" % pcn["backup_as_drawn"]["response_us"],
             "self_test": "idle: DACH below / DACL above the zero -> OST flag must set (PCS-CTL)"},
            {"item": "200 ms tier of a switch outside the absolute R_DS(on) limit (review R2-07, by lot)",
             "peripheral": "overload-tier parameter set chosen at end of line from the lot's incoming R_DS(on) record",
             "threshold": ("%s; an unscreened or above-limit switch: 200 ms tier %.0f A instead of %.0f A, 2-min tier %.0f A instead of "
                           "%.0f A (%s)" % (ra["absolute_limit"]["rule"].split(" - ")[0], ra["unscreened_derating"]["I_200ms_A"], I_200MS,
                                            ra["unscreened_derating"]["I_2min_A"], I_2MIN, ra["unscreened_derating"]["note"])),
             "filter": "-", "latency": "-", "self_test": "parameter CRC with the calibration set"}]
    rows += list(FW_I)                                       # step i: four-wire, AC start, modes, transfers, recorder, upgrade, map
    fw += [f"{r['item']}: {r['threshold']} - {r['peripheral']}" for r in rows]
    fw.append("grid start (AC side, DC dead; pcs_spec ac_start): " + " -> ".join(SPEC["ac_start"]["sequence"]))
    SPEC["handover"] = {"control_engineer": ctrl, "board_designers": boards, "firmware_requirements": fw, "firmware_limits": rows}
    SPEC["phase_current_sensor"] = PH_SENSOR
    wr("\n## Hand-over\n")
    wr("**Control engineer (plant and limits; pcs_spec.json 'handover'):**")
    wr(f"- LCL: L1 {F['L1']*1e6:.0f} uH (R about {ctrl['plant']['R_L1_mohm_est']} mOhm), C_f {F['Cf']*1e6:.0f} uF star (tied to the DC midpoint), "
       f"L2 {F['L2']*1e6:.0f} uH, R_d {F['Rd']:.0f} ohm + C_d {F['Cd']*1e6:.0f} uF per phase; resonance "
       + ", ".join(f"{k} {v/1e3:.2f} kHz" for k, v in rc["f_res_Hz"].items()) + f"; DC link {rd['C_total_uF']:.1f} uF across the bus "
       f"({rd['C_series_uF']:.0f} uF bank + {rd['C_local_films_uF']:.1f} uF leg films).")
    wr(f"- Sampling {F_S/1e3:.0f} kHz double update, delay {T_DELAY*1e6:.0f} us, dead time {re_['dead_time_ns']:.0f} ns ({ctrl['sampling']['dead_time_error']}).")
    for k in ("current_loop", "pll", "modulation", "midpoint", "grid_forming", "four_wire"):
        wr(f"- {k.replace('_', ' ')}: {ctrl[k]}")
    wr("\n**Board designers:**")
    wr(f"- power stage: {boards['power_stage']}")
    wr(f"- gate drive: {boards['gate_drive']}")
    wr(f"- control board: {boards['control_board']}")
    for k, v in boards["sensing"].items():
        wr(f"- sensing, {k.replace('_', ' ')}: " + ("; ".join(f"{a}: {b}" for a, b in v.items()) if isinstance(v, dict) else v))
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
              "Paralleling six TO-247 per switch: sharing k = 1.10 needs the R_DS(on) / V_GS(th) acceptance rule (section b) and the per-pair decoupling layout; both are "
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
              "Fan AFB1224SHE-F00 is rated -10..+60 C: below -10 C start and at 60 C inlet it is outside / at its limit (PV R-05).",
              "Protection chain (review R2-02 / R2-03): the sensor's 2 us step response is taken as a pure delay and the TLV9024 delay "
              "as 2 x its typical curve (no maximum published); the L1 knee rests on B_sat 1.40 T at 100 C (ESTIMATE) - OpenMagnetics' "
              "MAS figure for 2605SA1 is 1.35 T (B at 80 A/m) and puts the +5 % part's knee near 553 A; the gates-off currents stay below "
              "both.  The CMPSS backup threshold that closes its path is adopted on PCS-CTL (D-078); a saturating "
              "fault beyond it is left to DESAT, whose fault-under-load turn-off is not simulated here.  No RBSOA is published for "
              "the device (the switching data stop at 70 A per device; the window path turns off about "
              f"{SPEC['protection_chain']['local_window']['I_gates_off_A'] / 6:.0f} A per device)."]:
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
             (lambda p: f"- **Protection chain (review R2-02 / R2-03):** the window path turns the gates off at "
                        f"{p['local_window']['I_gates_off_A']:.0f} A (band top {p['inputs']['window_A'][1]:.1f} A + {p['local_window']['response_us']:.2f} "
                        f"us on the L1 envelope); at R_G,off {p['chosen_R_G_off_ext_ohm']:g} ohm the turn-off peaks at "
                        f"{p['commutation_overshoot_V']['local window (as drawn)']['v_pk_V']:.0f} V <= {re_['chosen']['v_limit_V']:.0f} V (the deck admits "
                        f"{p['I_max_admissible_A']:.0f} A); the CMPSS backup band is brought down on PCS-CTL (D-078): its top is at most "
                        f"<= {p['backup_requirement']['band_top_max_A']:.0f} A (adopted on PCS-CTL, D-078).")(SPEC["protection_chain"]),
             f"- **DC link:** {rd['per_half']} + {rd['per_half']} x Faratronic C3D1U147 film, > 100 kh at 60 C inlet; electrolytics would need "
             f"{rd['electrolytic_alternative']['cans']} cans and last {min(rd['electrolytic_alternative']['life_h'].values())/1e3:.0f} kh.",
             f"- **Cost:** {rg_['three_wire']['catalogue']:.0f} / {rg_['three_wire']['5k']:.0f} USD (catalogue / 5,000 units) three-wire, "
             f"{rg_['four_wire']['catalogue']:.0f} / {rg_['four_wire']['5k']:.0f} USD four-wire, against the architecture's 1,037 / 801 and "
             + (f"1,194 / 919 USD, whose filter is priced at 10 + 6 USD/J; with the real inductors and its corrected IGBT count the "
                f"architecture's T-type comes to {next((r['usd_5k'] for r in SPEC['tradeoff']['rows'] if r['name'].startswith('B-IGBT')), float('nan')):.0f} "
                f"USD at 5k (section h)." if FP and SPEC.get("tradeoff") else
                f"1,194 / 919 USD - which become about {SPEC['topology_screen'] and (rg_['architect']['three_wire'][1] + rg_['arch_corr']['devices_5k'] + rg_['arch_corr']['dc_link_5k']):.0f} "
                f"USD at 5k for its own T-type once its IGBT count and midpoint bank are corrected."),
             (lambda w, s: f"- **Four-wire build and AC start (step i):** C_f star stays on the DC midpoint, the N leg's filter node gets its own "
                           f"C_f set; DC window mode A (N leg at 50 %) from {w['mode_A']['closing (no load)']:.0f} V at 230 V, mode B (zero "
                           f"sequence on four legs) = the three-wire window ({w['mode_B']['closing (no load)']:.0f} V); AC start: 6-diode tap "
                           f"into the aux and an AC precharge from the tap, {s['by_build']['4W']['400']['t_to_10V_s']:.2f} s to 10 V at 400 V.")(
                 SPEC["four_wire"]["window"]["rows"]["nominal"], SPEC["ac_start"]["precharge"]), ""]
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
    pc, v_lim = SPEC["protection_chain"], re_["chosen"]["v_limit_V"]    # review R2-02 / R2-03
    co = pc["commutation_overshoot_V"]
    assert pc["local_window"]["closes"] and co["local window (as drawn)"]["v_pk_V"] <= v_lim, "local window: turn-off at the gates-off current"
    assert co["over-voltage corner (bus at the OV gates-off, current at the window top)"]["v_pk_V"] <= v_lim, "OV corner turn-off"
    assert pc["inputs"]["window_A"][1] <= pc["window_top_max_A"] and pc["chosen_R_G_off_ext_ohm"] == re_["chosen"]["R_G_off_ext_ohm_per_device"]
    br = pc["backup_requirement"]
    assert br["feasible_with_the_drawn_width"] and co["CMPSS backup at its required band top"]["v_pk_V"] <= v_lim, \
        "the CMPSS backup cannot be placed below the ceiling with its band width"
    assert max(pc["gates_off_current_A"].values()) <= pc["L1_trajectory"]["I_flux_rule_A"], "a gates-off current past the L1 flux rule"
    ra = re_["desat"]["rds_acceptance"]
    assert ra["absolute_limit"]["V_DS_at_limit_V"] <= ra["V_DS_max_V"] + 1e-6 and ra["unscreened_derating"]["V_DS_V"] <= ra["V_DS_max_V"] + 1e-6 \
        and ra["unscreened_derating"]["V_DS_2min_derated_V"] <= ra["V_DS_max_V"] + 1e-6 \
        and ra["unscreened_derating"]["tj_2min_derated_C"] <= TJ_LIM["sic"][1] + 1e-6 \
        and "REQUIRES HARDWARE TEST" in ra["sc_survival"], "R_DS(on) acceptance / derating / survival gate"
    assert all(x["THD_h2_50_pwm"] < 0.03 and x["largest_above_h50"][1] < I2_LIM_FRAC for x in rc["spectrum"]), "harmonics"
    assert all(f < F_S / 6 for f in rc["f_res_Hz"].values()), "resonance below f_s/6"
    assert rd["I_per_cap_A"] <= pv.CAP_IRMS_USE * FILM[rd["part"]]["imax"] + 1e-9, "film ripple current"
    assert max(rd["midpoint_pp_V"].values()) <= rd["U_pp_max_V"], "midpoint ripple"
    assert rg_["four_wire"]["5k"] > rg_["three_wire"]["5k"] > 0, "cost totals"
    assert dv.self_check()
    assert "inductors" in SPEC and "cost_usd" in SPEC and "L1" in SPEC["inductors"], "spec blocks"
    b = rd["bleeder"]
    assert b["t60_worst_min"] <= LABEL_MIN and b["P_elem_at_half_trip_W"] <= 0.41 and b["V_elem_at_half_trip_V"] <= 200.0, "bleeder"
    assert abs(rf["precharge"]["C_eq_uF"] - rd["C_total_uF"]) < 1e-6, "precharge on the total bus capacitance"
    assert not rd["upper_half"]["hardware_needed"] and SPEC["handover"]["firmware_limits"][0] is rd["upper_half"]["firmware_limit"], "upper half"
    sh = SPEC["thermal_and_losses"]["sharing_population"]
    assert sh["bounded_by_rule"], "acceptance rule must bound the sharing at the model's k"
    om = SPEC["operating_map"]
    assert om["vdc_min_at_rated_current_V"]["460 V 50 Hz"][list(PF_CASES)[2]] > math.sqrt(2) * 460.0, "operating map sanity"
    assert re_["desat"]["onstate_200ms_overload"]["V_DS_typ_at_tj_V"] > 0, "DESAT on-state input"
    ps = SPEC["phase_current_sensor"]
    assert OC_TRIP <= 0.75 * ps["linear_range_A"] and I_2MIN <= ps["I_PN_A"] and ps["ocd_x_I_PN"] * ps["I_PN_A"] * 0.9 > 1.2 * OC_TRIP, \
        "phase-current sensor range vs the trip window"
    for k in ("nominal_950V", "worst_1050V_450A_30nH"):
        assert os.path.exists(os.path.join(ROOT, re_[k]["deck"])), "ngspice deck missing"
    # step i: four-wire build, AC start
    fw, st = SPEC["four_wire"], SPEC["ac_start"]
    w = fw["window"]["rows"]["nominal"]
    assert w["mode_A"]["closing (no load)"] > w["mode_B"]["closing (no load)"] and abs(
        w["mode_B"]["closing (no load)"] - om["vdc_min_at_rated_current_V"]["400 V 50 Hz"]["closing permissive (no load, synchronised close)"]) < 1.0, \
        "four-wire mode B must equal the three-wire window, mode A sit above it"
    assert all(x["tj_max_C"] <= TJ_LIM["sic"][0 if k.startswith("%.0f A" % I_CONT) else 1] for k, x in fw["unbalance"]["n_leg"].items()), \
        "N-leg junction temperature (198 A continuous: 150 C; 216 A 2 min as steady state: 165 C)"
    assert fw["dc_link"]["per_cap_max_A"] <= fw["dc_link"]["per_cap_limit_A"], "four-wire DC-link ripple per capacitor"
    assert fw["bleeder"]["t60_worst_min"] <= LABEL_MIN, "four-wire bleeder against the label"
    ab = rf["aux_budget"]
    for k, b in ab["builds"].items():
        assert b["margin_live_W"] >= 0 and b["margin_selv_W"] >= 0 and b["margin_total_W"] >= 0 and b["margin_peak_W"] >= 0, \
            ("aux budget (live / SELV / total / pull-in peak) above the aux rating", k)
    assert all(b["coil_is_contract"] for b in ab["builds"].values()), \
        "PCS-PWR checks budgeted another AC coil than the contract: rebuild gen/pcs_power.py (it reads coil_contract from pcs_spec), re-run"
    assert ab["estimate_fits"] and SPEC["ports_and_common_mode"]["ac_contactor"]["coil_contract"] == AC_COIL_CONTRACT, \
        "AC coil contract outside what the aux budget allows, or not the one published"
    p4, rr4 = fw["precharge"]["four_wire_R"], fw["precharge"]["rating"]
    assert p4["E_J_max"] <= rr4["e_charge_J"] and p4["tau_max_s"] <= rr4["tau_s"] and p4["E_short_J"] <= rr4["e_short_J"] \
        and p4["t_short_s"] <= rr4["t_short_s"] + 1e-4 and p4["I_pk_A"] <= p4["relay_A"], "four-wire DC precharge (RPRE_AL rating, relay)"
    pc, rr = st["precharge"], st["precharge"]["rating"]
    assert all(x["E_pre_J"] <= rr["e_charge_J"] and x["I2t_fuse_A2s"] <= TAP["pulse_rule"] * TAP["fuse_i2t_melt"] and x["E_lim_J"] <= TAP["r_pulse_J"]
               for rows in pc["by_build"].values() for x in rows.values()), "AC precharge: resistor energy, fuse pulse rule, 20 ohm pulse"
    assert pc["E_pre_shorted_J"] <= rr["e_short_J"], "AC precharge shorted bank"
    assert st["tap"]["fault_L_L"]["E_per_R_J"] <= TAP["r_pulse_J"] and st["tap"]["fault_L_L"]["I_rms_A"] * math.sqrt(2) <= TAP["fuse_break_A"], \
        "tap fault: the fuse must clear before the 20 ohm's pulse rating, inside its breaking capacity"
    assert st["tap"]["diode_reverse_max_V"] <= 0.8 * TAP["d_vrrm"], "tap diode reverse voltage"
    txt = " ".join(st["sequence"] + st["interlocks"])        # review R2-14: the hand-over carries every repair of the executed machine
    assert all("H%d" % k in txt for k in range(1, 6)) and not any(c["breaches"] for c in st["state_machine"]["fault_cases"]), \
        "AC start: a repair H1-H5 missing from the sequence text, or an executed case that breaches"
    return True


def run():
    if not FP and "--bootstrap" not in sys.argv:      # PCM-23: no silent fall-back to the architect's filter estimates
        raise SystemExit(f"{TRADEOFF} missing: run sim/pcs_tradeoff.py first, or this script once with --bootstrap (first run on a "
                         "fresh checkout: step-c budgets and the architect's filter estimates, labelled as such in the report)")
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
    report_sharing(sharing(D, rb))
    save()
    rc = step_c(D)
    report_c(rc)
    save()
    rd = step_d(D, rc)
    report_d(rd)
    save()
    desat_onstate(D, rb)
    report_e(re_)
    save()
    rf = step_f(D, rc, rd)
    report_f(rf, rc)
    report_map(operating_map(D, rc))
    save()
    rg_ = step_g(D, rc, rd, re_)
    report_g(rg_)
    report_h()
    report_i(step_i(D, rb, rc, rd, rf, rg_))
    save()
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
