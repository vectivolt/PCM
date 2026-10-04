"""PCS-P125 power stage + LCL filter re-optimised with the real inductor designs in the loop (D-057 follow-up).

Run:  .venv/bin/python sim/pcs_tradeoff.py        (after sim/pcs_design.py - reads its pcs_spec.json and costed BOM rows)
Out:  sim/out/pcs_design/tradeoff.json, tradeoff.md
Models: devices, heat sink, PWM spectra, LCL, DC link from sim/pcs_design.py; L1 / L2 / AC CM choke from sim/magnetics.py (gapped
nanocrystalline / amorphous C-cores, litz / Cu foil / Al foil, OpenMagnetics 'higher of' check).  Neither file is edited here; the
only runtime substitutions are the modulation policy and switching-energy factors inside pcs_design and, for three-level or
re-modulated legs, the ripple-flux model passed to magnetics.pcs_ind_eval (restored after each use).
Candidates: A0 the drawn two-level design corrected, A1 L1 60-150 uH, A2 f_sw 16-48 kHz, A3 reduced common-mode PWM, A4 the
hardware over-current window, B the SiC T-type of the cross-check, B-IGBT the competitor-class IGBT T-type, and the cost of the
0.67 rule.  Everything CALCULATED; nothing measured.
"""
import contextlib
import copy
import functools
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import magnetics as mg  # noqa: E402
import pcs_design as pd  # noqa: E402
import pcs_devices as dv  # noqa: E402
import pv_devices as pv  # noqa: E402

OUT = pd.OUT
SQ2 = math.sqrt(2.0)
V_LL, F0 = pd.V_LL, pd.F_GRID
ETA_DESIGN = pd.ETA_REQ + 0.002     # L1 is chosen for >= 98.7 % peak: AC-02's 98.5 % + the 0.2-point model margin of step a
TRIP_TOL = 0.05                     # trip above the largest operating peak: TMR gain +-3 % + ladder / comparator 2 % (ESTIMATE)
T_TRIP = 1.5e-6                     # sensor + comparator + latch + driver + turn-off (pcs_design step g)
LEAK_LF = (5e-6, 0.300)             # 150 Hz earth current <= 300 mA at C_bE 5 uF (RCMU step class, IEC 62109-2 from memory)
ETA_FULL_MIN = 0.980                # worst full-load efficiency, 600-900 V, PF 1 (pcs_design self-check rule)
L_CM_REF = 150e-6                   # the D-053 AC CM choke (150 uH): its carrier-band earth current is the limit for every candidate
C_BE = (1e-6, 5e-6, 20e-6)
CF_OPTIONS = (30e-6, 50e-6, 75e-6, 100e-6)
CF_CAN = {"C": 25e-6, "imax": 15.0, "cat": 0.12e6, "5k": 0.102e6}   # MKP 450 VAC, <= 25 uF per can, 15 A rms per can ASSUMED (no data
#                                                                   sheet); USD per farad: 0.12 USD/uF (architect), x0.85 at 5k
L2_TABLE_UH = (6.0, 10.0, 15.0, 22.0, 33.0, 47.0)
SHARE_HOT, T_HOT = 1.30, pd.T_IN_HOT                # D-057: hottest device 1.30 x the mean, 60 C inlet
M_NSPWM = 0.77                       # NSPWM has no zero states only for 0.866 m >= 2/3 (largest reference in its clamp window)
VD_MAP = (600.0, 650.0, 700.0, 750.0, 800.0, 850.0, 900.0)
X_MAP = (0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.75)
OC_DRAWN = 450.0                     # A, the D-053 hardware window (pcs_design may adopt another one from this study)


def ZS0(lev, m):
    """the D-053 modulation policy (pcs_design.zs_policy as drawn): three-level min-max; two-level sinusoidal where m <= 0.98"""
    return "minmax" if lev == 3 else ("none" if m <= 0.98 else "minmax")


SPEC = json.load(open(os.path.join(OUT, "pcs_spec.json")))
CG = SPEC["commutation_and_gate_drive"]
EOFF = CG["eoff_factor_vs_datasheet_rg"]           # simulated E_off factor at R_G,off 7.5 ohm (step e)
RG_ON = CG["chosen"]["R_G_on_ext_ohm_per_device"]  # drawn R_G,on 8.75 ohm per device


def mult(part):
    """(E_on, E_off) factors: SiC at the drawn gate resistors (E_on from the data sheet's E-vs-R_G curve, E_off from step e's
    simulation - applied to the 1200 V part as the cross-check did); IGBTs at their data-sheet R_G (1, 1)"""
    if pd.kind(part) != "sic":
        return 1.0, 1.0
    return float(pv.eon_rg_factor(pv.MOSFETS[part], RG_ON)), EOFF


def levels(c):
    return pd.TOPO[c["topo"]]["levels"]


def zs_of(c, m, lev=None):
    lev = lev or levels(c)
    if c["zs"] == "minmax":
        return "minmax"
    if c["zs"] == "dpwm1" and lev == 2 and m >= M_NSPWM:
        return "dpwm1"
    return ZS0(lev, m)


@contextlib.contextmanager
def using(c, share=None):
    """the candidate's modulation, switching-energy factors and (optionally) sharing factor inside pcs_design"""
    keep = (pd.zs_policy, pd.EON_MULT["value"], pd.EOFF_MULT["value"], dict(pd.K_SHARE))
    pd.zs_policy = lambda lev, m: zs_of(c, m, lev)
    pd.EON_MULT["value"], pd.EOFF_MULT["value"] = mult(next(iter(c["devs"].values()))[0])
    if share:
        pd.K_SHARE.update(sic=share, igbt=share)
    try:
        yield
    finally:
        pd.zs_policy, pd.EON_MULT["value"], pd.EOFF_MULT["value"] = keep[:3]
        pd.K_SHARE.clear()
        pd.K_SHARE.update(keep[3])


# ------------------------------------------------------------------------------------------------ PWM with reduced-CM carriers
def refs3(m, th, zs):
    abc = np.stack([m * np.sin(th - k * 2 * np.pi / 3) for k in range(3)])
    return np.clip(abc + pd.zero_seq(abc, zs), -1.0, 1.0)


def fourier(lev, m, fsw, zs, inv=False, hmax=None):
    """complex harmonics (peak, units of V_dc/2) of the three legs vs the DC midpoint: pcs_design.pwm_fourier, and for two-level legs
    with inv=True the carrier of the phase holding the middle reference inverted (AZSPWM1 with sinusoidal / min-max references,
    NSPWM with DPWM1 references - Hava & Un, IEEE TPEL 2008/2011, from memory): no zero states, CM limited to +-V_dc/6"""
    if not inv:
        return pd.pwm_fourier(lev, m, fsw, zs, hmax=hmax)
    n = int(round(fsw / F0))
    hmax = hmax or 2 * n + 120
    T0, Tc = 1.0 / F0, 1.0 / fsw
    tk = np.arange(2 * n) * Tc / 2
    refs = refs3(m, 2 * np.pi * F0 * tk, zs)
    mid = np.argsort(refs, 0)[1]
    falling = np.arange(2 * n) % 2 == 0
    w = 2 * np.pi * F0 * np.arange(1, hmax + 1)
    out = np.zeros((3, hmax), complex)
    for ph in range(3):
        d = 0.5 * (1 + refs[ph])
        f_ = falling ^ (mid == ph)
        t1, t2 = np.where(f_, tk + (1 - d) * Tc / 2, tk), np.where(f_, tk + Tc / 2, tk + d * Tc / 2)
        for a, b, v in ((t1, t2, 2.0), (tk, tk + Tc / 2, -1.0)):
            out[ph] += (2.0 / T0) * v * np.sum(np.exp(-1j * np.outer(a, w)) - np.exp(-1j * np.outer(b, w)), axis=0) / (1j * w)
    return np.arange(1, hmax + 1), out


def wave(lev, m, fsw, zs, inv=False, n=2 ** 14):
    """naturally sampled legs over one period (pcs_design.pwm_wave), with the inverted middle-phase carrier when inv"""
    if not inv:
        t, legs, _ = pd.pwm_wave(lev, m, fsw, zs, n=n)
        return t, legs
    t = np.arange(n) / n / F0
    car = 2 * np.abs(2.0 * ((t * fsw) % 1.0) - 1.0) - 1
    refs = refs3(m, 2 * np.pi * F0 * t, zs)
    mid = np.argsort(refs, 0)[1]
    return t, np.stack([np.where(refs[k] > np.where(mid == k, -car, car), 1.0, -1.0) for k in range(3)])


def zcf(w, cf, rd=2.0, cd=10e-6):
    """one phase of C_f with the drawn passive damping branch (R_d 2 ohm + C_d 10 uF across it, step c) - also the common-mode path
    to the DC midpoint, where L1 and C_f form a series resonance near 1 / (2 pi sqrt(L1 C_f)) that low-order zero sequence excites"""
    return 1 / (1j * w * cf + 1 / (rd + 1 / (1j * w * cd)))


def hf_split(c, vdc, i_rms=pd.I_RATED, ang=0.0, lev=None, l1=None, cf=None, l2=None):
    """L1 high-frequency current per phase (A rms above h50) at an operating point: DM through the LCL plus the CM current through
    L1 / C_f to the DC midpoint, added as phasors; the CM total into the midpoint; the grid-current carrier band"""
    lev = lev or levels(c)
    l1, cf, l2 = l1 or c["l1"], cf or c["cf"], l2 or c["l2"]
    m, _, _, _ = pd.op_point(vdc, V_LL, i_rms, ang, l1 + l2)
    zs = zs_of(c, m, lev)
    h, cc = fourier(lev, m, c["fsw"], zs, c["inv"] and lev == 2)
    sel = h > 50
    f = h[sel] * F0
    w = 2 * np.pi * f
    vdm, vcm = (cc[0] - cc.mean(0))[sel] * vdc / 2, cc.mean(0)[sel] * vdc / 2
    y1, y2 = pd.lcl_tf(f, l1, cf, l2, r1=3e-3, r2=1e-3, rd=2.0, cd=10e-6)
    icm = vcm / (1j * w * l1 + zcf(w, cf))
    rms = lambda x: float(math.sqrt(np.sum(np.abs(x) ** 2) / 2))  # noqa: E731
    nf = int(round(c["fsw"] / F0))
    return {"m": m, "zs": zs, "dm": rms(y1 * vdm), "cm": rms(icm), "tot": rms(y1 * vdm + icm), "mid_cm": 3 * rms(icm),
            "vcm_fsw": float(abs(cc.mean(0)[nf - 1]) * vdc / 2), "i2_max": float(np.max(np.abs(y2 * vdm))) / (pd.I_RATED * SQ2),
            "h": h[sel], "vcm": vcm}


# ------------------------------------------------------------------------------------------------ filter: C_f, L2, CM choke
def f_res(l1, l2, cf, lg=0.0):
    return pd.f_res(l1, l2, cf, lg)


def worst_dm(c, l1):
    """worst-case DM voltage spectrum (peak V per harmonic) over 950 V and 600 V at rated current (and the T-type's two-level corner)"""
    worst = 0.0
    pts = [(950.0, levels(c)), (600.0, levels(c))] + ([(c["v_corner"], 2)] if c.get("v_corner") else [])
    for vdc, lev in pts:
        m = pd.op_point(vdc, V_LL, pd.I_RATED, 0.0, l1 + 15e-6)[0]
        h, cc = fourier(lev, m, c["fsw"], zs_of(c, m, lev), c["inv"] and lev == 2)
        worst = np.maximum(worst, np.abs(cc[0] - cc.mean(0)) * vdc / 2)
    return h, worst


def lcl_choice(c, l1, l2_cost, l2_set):
    """cheapest C_f (CF_OPTIONS) + L2 (L2_TABLE) that meets: every grid-current component of the carrier band <= 0.3 % of rated (stiff
    grid), the stiff-grid resonance below f_s/6 and the SCR-5 resonance above twice the current-loop bandwidth (1 kHz at 64 kHz
    sampling, scaled with f_s - ASSUMED)"""
    fs = 2 * c["fsw"]
    bw = 1e3 * fs / 64e3
    lg5 = V_LL ** 2 / pd.P_RATED / 5 / (2 * math.pi * F0)
    h, v = worst_dm(c, l1)
    sel = h >= c["fsw"] / F0 - 40
    lim = pd.I2_LIM_FRAC * pd.I_RATED * SQ2
    best = None
    for cf in CF_OPTIONS:
        for l2u in sorted(l2_set):
            l2 = l2u * 1e-6
            _, y2 = pd.lcl_tf(h[sel] * F0, l1, cf, l2, r1=5e-3, r2=5e-3)
            ok = (np.max(np.abs(y2) * v[sel]) <= lim and f_res(l1, l2, cf) < fs / 6 and f_res(l1, l2, cf, lg5) >= 2 * bw)
            if ok:
                cost = 3 * l2_cost(l2u)[1] + 3 * cf * CF_CAN["5k"]
                if best is None or cost < best[0]:
                    best = (cost, cf, l2)
                break
    if best is None:                                  # no option meets the window: keep the carrier-band answer, flag it
        return {"cf": 50e-6, "l2": 47e-6, "window_ok": False, "fs": fs, "bw": bw}
    _, cf, l2 = best
    return {"cf": cf, "l2": l2, "window_ok": True, "fs": fs, "bw": bw, "f_res_stiff": f_res(l1, l2, cf), "f_res_scr5": f_res(l1, l2, cf, lg5)}


def earth_fn(c):
    """carrier-band earth current (A rms, harmonics from f_sw - 40 f0 up) through C_bE 1 / 5 / 20 uF as a function of L_cm: terminal
    CM = converter CM (vs the DC midpoint) divided by L1/3 and 3 C_f (pcs_design step f).  The zero-sequence harmonics below the
    carrier band belong to the 150 Hz leakage check (v150_worst)"""
    sp = []
    pts = [(vdc, pd.I_RATED, 0.0, None) for vdc in (600.0, 750.0, 950.0)]
    if c.get("v_corner"):                       # T-type: its two-level corner mode makes the full two-level CM voltage
        pts.append((c["v_corner"], pd.I_RATED, 90.0, 2))
    for vdc, i, ang, lev in pts:
        s = hf_split(c, vdc, i, ang, lev=lev)
        k = s["h"] >= c["fsw"] / F0 - 40
        w = 2 * np.pi * F0 * s["h"][k]
        sp.append((w, s["vcm"][k] * zcf(w, c["cf"]) / (1j * w * c["l1"] + zcf(w, c["cf"]))))
    return lambda l_cm: np.max([[math.sqrt(np.sum(np.abs(vt / (1j * w * l_cm + 1 / (1j * w * cb))) ** 2) / 2) for cb in C_BE]
                                for w, vt in sp], 0)


@functools.lru_cache(maxsize=None)
def leak_hf_limit():
    """the drawn design's carrier-band earth current with its 150 uH choke (A0: 2L, 32 kHz, 97 uH, 50 uF, 15 uH) - the limit"""
    a0 = dict(topo="2L", fsw=32e3, zs="policy", inv=False, l1=pd.l1_for_ripple({"topo": "2L", "fsw": 32e3}), cf=50e-6, l2=15e-6)
    return float(max(earth_fn(a0)(L_CM_REF)))


def cm_choke(c, P):
    """AC CM choke: L_cm keeping the carrier-band earth current at or below the drawn design's (leak_hf_limit) for C_bE 1-20 uF; the
    choke itself from magnetics.pcs_cm_design (number of N-R-564440 cores)"""
    earth = earth_fn(c)
    lim = leak_hf_limit()
    lo, hi = 1e-6, 5e-3
    for _ in range(40):
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if max(earth(mid)) > lim * 1.0001 else (lo, mid)
    l_cm = hi
    ie = earth(l_cm)
    Pc = copy.deepcopy(P)
    Pc["f"] = c["fsw"]
    Pc["cm"]["hf"] = dict(Pc["cm"]["hf"], L_cm_uH=l_cm * 1e6, earth_current={f"{cb*1e6:.0f}uF": {"with_choke_mA": i * 1e3} for cb, i in zip(C_BE, ie)})
    Pc["cm"]["lf_zero_sequence"] = [{"v150_rms": c["v150"]}]
    d = mg.pcs_cm_design(Pc)
    return {"L_cm_uH": l_cm * 1e6, "earth_mA": [round(i * 1e3, 1) for i in ie], "k": d["k"], "mass": d["mass"],
            "usd": (d["cost"]["total_usd"] + 1.5, d["cost"]["build_5k_usd"] + 1.2)}         # + 3 Y1 capacitors (ESTIMATE)


def v150_worst(c):
    """largest 150 Hz zero-sequence voltage (V rms) over V_dc 590-950 V and AC 400 / 460 V at rated current (the modulation's own
    zero sequence; the T-type adds none beyond min-max - corner2l)"""
    worst = 0.0
    for vdc in (590.0, 650.0, 680.0, 750.0, 850.0, 950.0):
        for vll in (V_LL, V_LL * (1 + pd.V_TOL)):
            for ang in (0.0,):
                m, phi, _, _ = pd.op_point(vdc, vll, pd.I_RATED, ang, c["l1"] + c["l2"])
                th, va = pd.references(m, zs_of(c, m), 720)       # three-level: min-max only (corner2l premise)
                v0 = va - m * np.sin(th)
                X = np.fft.rfft(v0 * vdc / 2) / len(v0)
                worst = max(worst, 2 * abs(X[3]) / SQ2)
    return worst


# ------------------------------------------------------------------------------------------------ DC link
def dclink(c):
    """film capacitors per half (C3D1U147) by pcs_design.dclink_count's ripple-current rule (LF + carrier band + half the C_f-star
    CM current, 110 %, its NP_CORNERS) with this candidate's PWM; T-types >= 5 per half (the cross-check premise: two-level mode in
    the low-PF corner instead of the 150 Hz midpoint bank)"""
    f = pd.FILM["C3D1U147"]
    i_half = 0.0
    pts = [(vdc, ang, levels(c)) for vdc, ang in pd.NP_CORNERS]
    if c.get("v_corner"):
        pts += [(c["v_corner"], 90.0, 2), (min(c["v_corner"], 600.0), 90.0, 2)]
    for vdc, ang, lev in pts:
        m, phi, _, _ = pd.op_point(vdc, V_LL, pd.I_CONT, ang, c["l1"] + c["l2"])
        zs = zs_of(c, m, lev)
        t, legs = wave(lev, m, c["fsw"], zs, c["inv"] and lev == 2)
        th = 2 * np.pi * F0 * t
        ix = np.stack([pd.I_CONT * SQ2 * np.sin(th - k * 2 * np.pi / 3 - phi) for k in range(3)])
        i_p, i_n = np.sum(np.where(legs > 0.5, ix, 0.0), 0), np.sum(np.where(legs < -0.5, ix, 0.0), 0)
        idc = float(np.mean(i_p))
        parts = []
        for x in (idc - i_p, idc + i_n):
            X = np.fft.rfft(x) / len(x)
            parts.append((math.sqrt(2 * np.sum(np.abs(X[1:41]) ** 2)), math.sqrt(2 * np.sum(np.abs(X[41:]) ** 2))))
        h, cc = fourier(lev, m, c["fsw"], zs, c["inv"] and lev == 2, hmax=3 * int(round(c["fsw"] / F0)))
        w = 2 * np.pi * F0 * h
        vcm = np.abs(cc.mean(0)) * vdc / 2
        s = h > 50
        icm = math.sqrt(np.sum((3 * vcm[s] / np.abs(1j * w[s] * c["l1"] + zcf(w[s], c["cf"]))) ** 2) / 2)
        i_half = max(i_half, math.sqrt(max(p[0] for p in parts) ** 2 + max(p[1] for p in parts) ** 2 + (icm / 2) ** 2))
    n = math.ceil(i_half / (pv.CAP_IRMS_USE * f["imax"]))
    return {"per_half": max(n, 5 if levels(c) == 3 else 2), "I_half_A": i_half}


# ------------------------------------------------------------------------------------------------ inductors (magnetics.py in the loop)
@functools.lru_cache(maxsize=None)
def _ki(k, a, b):
    return mg._ki(k, a, b)


@contextlib.contextmanager
def ripple_flux(c, lev=None):
    """magnetics.cc_core_loss with this leg's flux ripple: two-level (V_dc/4f)(1 - r^2) or three-level (V_dc/2f)|r|(1 - |r|) under the
    candidate's own reference r (zero sequence, clamping), iGSE and cut factor as magnetics.py; calls with i_hf (L2) keep the original"""
    orig = mg.cc_core_loss
    lev = lev or levels(c)

    def core(cc, N, vdc, m_mod, f, I_pk50, L, i_hf=None):
        if i_hf:
            return orig(cc, N, vdc, m_mod, f, I_pk50, L, i_hf)
        mt = mg.CC_MAT[cc["mat"]]
        k, a, b = mt["stein"]
        th = np.linspace(0, 2 * np.pi, 96, endpoint=False)
        r = refs3(m_mod, th, zs_of(c, m_mod, lev))[0]
        ar = np.abs(r)
        db, d = ((vdc / (4 * f * N * cc["Ae"]) * (1 - r ** 2), 0.5 * (1 + r)) if lev == 2 else
                 (vdc / (2 * f * N * cc["Ae"]) * ar * (1 - ar), ar))
        d = np.clip(d, 1e-4, 1 - 1e-4)
        p_rip = float(np.mean(_ki(k, a, b) * db ** b * f ** a * (d ** (1 - a) + (1 - d) ** (1 - a)))) * cc["Ve"] * mt["cut"]
        b50 = L * I_pk50 / (N * cc["Ae"])
        p50 = mt["kh"] * 50 * b50 ** 2 * cc["mass"]
        return dict(P_ripple=p_rip, P_50Hz=p50, P=p_rip + p50, dB_pp_max=float(db.max()), B_50=b50, envelope=0.0)
    mg.cc_core_loss = core
    try:
        yield
    finally:
        mg.cc_core_loss = orig


def ripple_pp(c, l1):
    r = 950.0 / ((4 if levels(c) == 2 else 8) * c["fsw"] * l1)
    return max(r, c["v_corner"] / (4 * c["fsw"] * l1)) if c.get("v_corner") else r


def trip(c, l1):
    """(comparator trip, current the core must carry unsaturated): 'present' = 450 A as drawn unless this design's own peaks need more;
    'protection' (A4) = 200 ms overload peak + ripple/2 + TRIP_TOL, plus the overshoot in the trip path at V_dc,trip / 2 across 0.9 L1"""
    pk = pd.I_200MS * SQ2 + ripple_pp(c, l1) / 2
    need = pk * (1 + TRIP_TOL)
    over = pd.V_OV_TRIP / 2 / (0.9 * l1) * T_TRIP
    if c["trip"] == "protection":
        return need, need + over
    return max(OC_DRAWN, need), max(OC_DRAWN, need + over)


def l1_req(c, P):
    """magnetics.pcs_L1_req for this candidate: the spec's per-unit L(I) minima at this design's own peaks, HF current (DM + CM) at
    750 / 950 V from its PWM, the unsaturated current from trip()"""
    l1 = c["l1"]
    rip = ripple_pp(c, l1)
    pk = lambda i: i * SQ2 + rip / 2  # noqa: E731
    t, sat = trip(c, l1)
    tab = {0.0: 0.9 * l1, pk(pd.I_CONT): 0.85 * l1, pk(pd.I_2MIN): 0.80 * l1, pk(pd.I_200MS): 0.65 * l1, t: 0.5 * l1}
    b = SPEC["inductors"]["L1"]["loss_budget_W"]
    return dict(L=l1, tol=0.10, L_min_tab=tab, I_rated=pd.I_RATED, I_cont=pd.I_CONT, I_2min=pd.I_2MIN, I_200ms=pd.I_200MS,
                hf={v: c["hf"][v] for v in (750.0, 950.0)}, ripple_pp=rip, I_trip=sat, I_trip_set=t, budget=b["total_at_125kW_750V"],
                budget_cu=b["copper_at_180A"], budget_core=b["core_and_gap_at_750V"], T_hot_max=140.0)


_ROWS = {}
CTX = {}                               # per candidate: what pick_l1 needs for the efficiency-margin sensitivity


def l1_rows(c, P, rq):
    key = (levels(c), c["zs"], c["fsw"], round(rq["L"] * 1e8), round(rq["I_trip"]), round(rq["hf"][950.0], 1), round(rq["hf"][750.0], 1))
    if key not in _ROWS:
        with ripple_flux(c):
            _ROWS[key] = mg.pcs_search(P, rq, mats=("nano", "amor"), wires=mg.L1_WIRES, grid=mg.L1_GRID_MAIN)
    return _ROWS[key]


def loss_terms(r, c, P, hf_tab, lev=None):
    """per-phase inductor loss as core(V_dc) + HF copper(V_dc) + k I^2, from magnetics' own functions at each V_dc (OM 'higher of'
    factors applied once the part is verified)"""
    cc, N, wire, k = r["core"], r["N"], r["wire"], r["gap"]["k"]
    L = r.get("L") or c["l1"]
    out = {}
    with ripple_flux(c, lev):
        for vdc, hf in hf_tab.items():
            core = mg.cc_core_loss(cc, N, vdc, P["vph_pk"] / (vdc / 2), c["fsw"], 0.0, L)["P"]
            w = mg.cc_winding(cc, N, wire, 0.0, hf, c["fsw"], k)
            out[vdc] = (core, w["P_cu"])
    mt = mg.CC_MAT[cc["mat"]]
    k2 = mg.cc_winding(cc, N, wire, 1.0, 0.0, c["fsw"], k)["R_dc"] + mt["kh"] * 50 * (L * SQ2 / (N * cc["Ae"])) ** 2 * cc["mass"]
    fc = fu = 1.0
    if "design" in r:
        d = r["design"]["budget: 180 A, 750 V"]
        fc, fu = d["P_core"] / max(d["own_core"], 1e-9), d["P_cu"] / max(d["own_cu"], 1e-9)
    return {"a": {v: out[v][0] * fc + out[v][1] * fu for v in out}, "k2": k2 * fu}


def as_fn(d):
    """per-phase loss P(V_dc, I) = a(V_dc) + k2 I^2 (a = core + HF copper, k2 = DC-copper resistance + 50 Hz core term)"""
    return lambda vdc, i: d["a"][vdc] + d["k2"] * i ** 2


def l2_designs(P):
    """magnetics.pcs_l2_options for each L2 in L2_TABLE_UH (the spec's per-unit L(I) shape), cheapest verified part"""
    res = {}
    for l2u in L2_TABLE_UH:
        Pl = copy.deepcopy(P)
        x = Pl["L2"]
        sc = l2u / x["inductance_uH"]
        x["L_vs_I_min_uH"] = {k: v * sc for k, v in x["L_vs_I_min_uH"].items()}
        x["inductance_uH"] = l2u
        rq, rows, pk = mg.pcs_l2_options(Pl)
        r = pk.get("within_budget") or pk.get("cheapest")
        if r is None:
            continue
        res[l2u] = {"r": r, "rq": rq, "P": Pl, "usd": (r["cost"]["total_usd"], r["cost"]["build_5k_usd"]), "mass": r["mass"]}
    return res


def l2_loss(L2d):
    r, P = L2d["r"], L2d["P"]
    cc, N, wire, k = r["core"], r["N"], r["wire"], r["gap"]["k"]
    hf = L2d["rq"]["hf"][750.0]
    w0 = mg.cc_winding(cc, N, wire, 0.0, hf, P["f"], k)["P_cu"]
    core = mg.cc_core_loss(cc, N, 750.0, 0.8, P["f"], 0.0, r["L"], hf)["P"]
    k2 = mg.cc_winding(cc, N, wire, 1.0, 0.0, P["f"], k)["R_dc"]
    d = r["design"]["budget: 180 A, 750 V"]
    fu = d["P_cu"] / max(d["own_cu"], 1e-9)
    return {"a": {v: w0 * fu + core for v in VD_MAP + (950.0,)}, "k2": k2 * fu}


# ------------------------------------------------------------------------------------------------ devices
def corner2l(c):
    """(V_dc, PF angle) points where min-max PD-PWM alone (no balancing zero sequence beyond min-max) would ripple the 5 + 5 film
    bank's midpoint by more than 0.1 U_N = 60 V pp at 150 Hz (110 % current, AC 400 and 460 V, switching-period averaged midpoint
    current -sum |r_x| i_x): the T-type runs its outer devices as a two-level leg there (cross-check premise).  Active balancing
    (pcs_design.np_residual) would shrink this region but put up to ~110 V rms of 150 Hz on the battery (see the report)"""
    ch = 5 * pd.FILM["C3D1U147"]["C"]
    th = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    pts = set()
    for vdc in (590.0, 650.0, 700.0, 750.0, 800.0, 850.0, 900.0, 950.0):
        for ang in range(-180, 181, 15):
            for vll in (V_LL, V_LL * (1 + pd.V_TOL)):
                m, phi, _, _ = pd.op_point(vdc, vll, pd.I_CONT, float(ang), c["l1"] + 15e-6)
                r = refs3(m, th, "minmax")
                io = -np.sum(np.abs(r) * np.stack([np.sin(th - k * 2 * np.pi / 3 - phi) for k in range(3)]), 0)
                vpp = np.ptp(np.cumsum(io * pd.I_CONT * SQ2) * (1 / (F0 * len(th))) / 2.0) / ch
                if vpp > 0.1 * pd.FILM["C3D1U147"]["un70"]:
                    pts.add((vdc, float(ang)))
    return pts


def v150_balanced(c):
    """150 Hz zero sequence (V rms) if the T-type balanced its midpoint actively instead (IDEAL carrier-based control,
    pcs_design.np_residual, PF 1 and 0, AC 400 / 460 V) - the alternative to the two-level corner"""
    worst = 0.0
    for vdc in (650.0, 750.0, 950.0):
        for vll in (V_LL, V_LL * (1 + pd.V_TOL)):
            for ang in (0.0, 90.0):
                m, phi, _, _ = pd.op_point(vdc, vll, pd.I_RATED, ang, c["l1"] + c["l2"])
                th, _, v0 = pd.np_residual(min(m, 1.13), phi)
                X = np.fft.rfft(v0 * vdc / 2) / len(v0)
                worst = max(worst, 2 * abs(X[3]) / SQ2)
    return worst


def two_level_of(c):
    o = c["devs"]["T1"]
    return dict(c, topo="2L", devs={"TH": o, "TL": o}, corner=None, v_corner=None)


def worst_tj(c, t_in, x, share=None, corners=None):
    """worst junction (and its margin to the continuous limit) over V_dc x AC voltage x PF angle at current x I_rated"""
    pts = corners or [(v, a) for v in (600.0, 750.0, 900.0, 950.0) for a in (0.0, 90.0, 180.0, -90.0)]
    worst, marg = 0.0, {}
    with using(c, share):
        for vdc, ang in pts:
            for vll in (V_LL * (1 - pd.V_TOL), V_LL, V_LL * (1 + pd.V_TOL)):
                ev = pd.evaluate(c, vdc, vll, pd.I_RATED * x, ang, t_in)
                if not ev["feasible"]:
                    continue
                worst = max(worst, ev["tj_max"])
                for p, v in pd.tj_ok(c, ev).items():
                    marg[p] = min(marg.get(p, 1e9), v)
    return worst, marg


def size(c):
    """device count: pcs_design.size_candidate (110 % / 45 C, 120 % 2 min, 200 ms; k 1.10, AC 400 V), then one more device on the
    position with the smallest negative margin until every Tj holds at 110 % / 45 C over AC 340-460 V and V_dc to 950 V and at
    100 % rated / 60 C with k 1.30 (D-057); T-types also in their two-level corner (outer devices)"""
    with using(c):
        pd.size_candidate(c)
    twins = {"T1": "T4", "T4": "T1", "T2": "T3", "T3": "T2", "TH": "TL", "TL": "TH"}
    for _ in range(16):
        bad = {}
        checks = [(c, None, {})] + ([(two_level_of(c), sorted(c["corner"]), {"TH": "T1", "TL": "T4"})] if c.get("corner") else [])
        for cc_, cor, mp in checks:
            for t_in, x, sh in ((pd.T_IN, pd.I_CONT / pd.I_RATED, None), (T_HOT, 1.0, SHARE_HOT)):
                for p, v in worst_tj(cc_, t_in, x, sh, cor)[1].items():
                    if v < 0:
                        bad[mp.get(p, p)] = min(bad.get(mp.get(p, p), 0.0), v)
        if not bad:
            break
        hot = min(bad, key=bad.get)
        n = c["devs"][hot][1]
        for p_ in (hot, twins[hot]):
            c["devs"][p_] = (c["devs"][p_][0], n + 1)
    return c


def derate60(c):
    """largest current (fraction of 180 A, 0.05 steps) with every Tj within its continuous limit at 60 C inlet and k = 1.30"""
    for x in (1.10, 1.05, 1.00, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70):
        ok = min(worst_tj(c, T_HOT, x, SHARE_HOT)[1].values()) >= 0
        if ok and c.get("corner"):
            ok = min(worst_tj(two_level_of(c), T_HOT, x, SHARE_HOT, sorted(c["corner"]))[1].values()) >= 0
        if ok:
            return x
    return 0.0


def dev_map(c):
    """device + fixed losses at the efficiency-map points (inverter / rectifier PF 1, 400 V AC, 45 C) and at 125 kW / 750 V"""
    n_fans = 3 * pd.HS["fans_per_section"]
    pts = {}
    with using(c):
        for vdc in VD_MAP:
            for x in X_MAP + (1.0,):
                for ang in (0.0, 180.0):
                    ev = pd.evaluate(c, vdc, V_LL, pd.I_RATED * x, ang, pd.T_IN)
                    oth = pd.other_losses(pd.I_RATED * x, vdc, ev["p"], n_fans, x)
                    fixed = sum(v for k, v in oth.items() if k not in ("L1", "L2"))
                    pts[(vdc, x, ang)] = (abs(ev["p"]), 3 * ev["leg_w"], fixed)
    return pts


def eta(pts, lossL):
    out = {}
    for (vdc, x, ang), (p, pdv, fixed) in pts.items():
        pl = pdv + fixed + lossL(vdc, pd.I_RATED * x)
        out[(vdc, x, ang)] = p / (p + pl) if ang == 0.0 else (p - pl) / p
    return out


# ------------------------------------------------------------------------------------------------ cost
ROWS3 = [r for r in SPEC["cost_usd"]["rows"] if r["block"] != "4-WIRE OPTION"]
VAR = lambda r: r["block"] in ("POWER", "GATE DRIVE", "LCL FILTER") or "common-mode choke" in r["function"]  # noqa: E731
BASE = (sum(r["ext_cat_usd"] for r in ROWS3 if not VAR(r)), sum(r["ext_5k_usd"] for r in ROWS3 if not VAR(r)))
UNIT = {r["function"][:24]: (r["unit_cat_usd"], r["unit_5k_usd"]) for r in ROWS3}       # channel discretes etc. by row prefix


def cost(c, l1u, l2u, cmu, cf):
    """three-wire total: the drawn BOM's fixed rows + this candidate's power stage, gate drive and filter rows (unit prices as the
    drawn BOM; inductors and CM choke from magnetics.py)"""
    tt = c["topo"] == "TT"
    devs = c["devs"].values()
    nd = 3 * sum(n for _, n in devs)
    n_out = c["devs"]["T1" if tt else "TH"][1]
    cells = 2 if tt else 1
    pairs = math.ceil(n_out / 2)
    ch = 12 if tt else 6
    gates = max(n for _, n in devs)
    dis = (UNIT["Channel discretes (gdrv."][0] + (gates - 6) * 0.25, UNIT["Channel discretes (gdrv."][1] + (gates - 6) * 0.25 * 0.85)
    rows = [("devices", sum(3 * n * dv.part_price(p, 1) for p, n in devs), sum(3 * n * dv.part_price(p, 10 ** 6) for p, n in devs)),
            ("insulators", nd * pd.PAD["cat"], nd * pd.PAD["5k"]),
            ("DC-link film", 2 * c["dcl"]["per_half"] * pd.FILM_PRICE["cat"], 2 * c["dcl"]["per_half"] * pd.FILM_PRICE["5k"]),
            ("decoupling + dampers", 3 * cells * (1.5 * 2 * pairs * 0.45 + pairs / 3 * 5.73), 3 * cells * (1.5 * 2 * pairs * 0.383 + pairs / 3 * 4.86)),
            ("gate channels", ch * (2.978 + dis[0] + 0.122), ch * (1.544 + dis[1] + 0.107)),
            ("gate bias", (5 if tt else 3) * 3.026, (5 if tt else 3) * 2.636),
            ("L1 x3", 3 * l1u[0], 3 * l1u[1]), ("L2 x3", 3 * l2u[0], 3 * l2u[1]),
            ("C_f cans", 3 * cf * CF_CAN["cat"], 3 * cf * CF_CAN["5k"]), ("damping branch", 9.0, 7.65),
            ("AC CM choke", cmu[0], cmu[1])]
    if tt:
        rows.append(("T-type state interlock", 3 * 0.15, 3 * 0.12))
    return {"cat": BASE[0] + sum(r[1] for r in rows), "5k": BASE[1] + sum(r[2] for r in rows), "rows": rows,
            "filter": (sum(r[1] for r in rows if r[0] in ("L1 x3", "L2 x3", "C_f cans", "damping branch", "AC CM choke")),
                       sum(r[2] for r in rows if r[0] in ("L1 x3", "L2 x3", "C_f cans", "damping branch", "AC CM choke")))}


def pick_l1(c, P, rq, rows, pts, lossL2, peak=None, full=None):
    """cheapest L1 (rows sorted by 5k cost) that, verified (higher of own model and OpenMagnetics), keeps its hot spot <= 140 C at 60 C,
    the T-type's two-level corner <= 140 C, and the efficiency rules (peak >= ETA_DESIGN, worst full load >= ETA_FULL_MIN)"""
    peak, full = peak or ETA_DESIGN, full or ETA_FULL_MIN
    ok = lambda e: max(e.values()) >= peak and min(v for (vdc, x, a), v in e.items() if x == 1.0) >= full  # noqa: E731
    tried = 0
    for r in rows:
        fn = as_fn(loss_terms(r, c, P, {v: c["hf"][v] for v in VD_MAP}))
        if not ok(eta(pts, lambda v, i: 3 * (fn(v, i) + lossL2(v, i)))):
            continue
        tried += 1
        if tried > 400:                # magnetics.pcs_pick_verified's limit
            return None
        with ripple_flux(c):
            mg.pcs_verify(P, rq, r)
        if not r["ok_d"] or (c.get("v_corner") and corner_hot(r, c, P) > 140.0):
            continue
        fn = as_fn(loss_terms(r, c, P, {v: c["hf"][v] for v in VD_MAP}))
        if ok(eta(pts, lambda v, i: 3 * (fn(v, i) + lossL2(v, i)))):
            return r
    return None


# ------------------------------------------------------------------------------------------------ one candidate, end to end
def assess(c, P, L2T, log=print):
    c.setdefault("zs", "policy")
    c.setdefault("inv", False)
    c.setdefault("trip", "present")
    c["hs_r"] = pd.section_rth()
    c["l1"] = c.get("l1") or pd.l1_for_ripple(c)
    c["l2"], c["cf"] = 15e-6, 50e-6
    if c["topo"] == "TT":
        c["corner"] = corner2l(c)
        c["v_corner"] = max(v for v, a in c["corner"]) if c["corner"] else None
    f = lcl_choice(c, c["l1"], lambda u: L2T[u]["usd"], L2T)
    c["cf"], c["l2"] = f["cf"], f["l2"]
    c["l_tot"] = c["l1"] + c["l2"]
    c["hf"] = {v: hf_split(c, v)["tot"] for v in set(VD_MAP) | {750.0, 950.0}}
    size(c)
    ndev = 3 * sum(n for _, n in c["devs"].values())
    tj45, m45 = worst_tj(c, pd.T_IN, pd.I_CONT / pd.I_RATED)
    m45 = min(m45.values())
    if c.get("corner"):
        t2, m2 = worst_tj(two_level_of(c), pd.T_IN, pd.I_CONT / pd.I_RATED, None, sorted(c["corner"]))
        tj45, m45 = max(tj45, t2), min(m45, min(m2.values()))
    x60 = derate60(c)
    c["dcl"] = dclink(c)
    c["v150"] = v150_worst(c)
    cm = cm_choke(c, P)
    # ---- L1: cheapest verified part keeping the peak efficiency >= ETA_DESIGN (and, T-types, its two-level corner <= 140 C)
    P = dict(P, f=c["fsw"])            # magnetics evaluates winding / core / OpenMagnetics at P["f"]
    rq = l1_req(c, P)
    mg._PCS_OM.clear()                 # its cache key has no frequency / HF current: verify each candidate afresh
    rows = sorted(l1_rows(c, P, rq), key=lambda r: r["cost"]["build_5k_usd"])
    l2d = L2T[round(c["l2"] * 1e6)]
    l2t = l2_loss(l2d)
    lossL2 = as_fn(l2t)
    pts = dev_map(c)
    pick = pick_l1(c, P, rq, rows, pts, lossL2)
    if pick is None:
        log(f"  {c['name']}: no L1 part meets the efficiency targets - magnetics' cheapest verified part used")
        with ripple_flux(c):
            pick = mg.pcs_pick_verified(P, rq, rows)
    l1t = loss_terms(pick, c, P, {v: c["hf"][v] for v in VD_MAP + (950.0,)})
    fn = as_fn(l1t)
    lossL = lambda v, i: 3 * (fn(v, i) + lossL2(v, i))  # noqa: E731
    e = eta(pts, lossL)
    kpk = max(e, key=e.get)
    n_cf = math.ceil(c["cf"] / CF_CAN["C"] - 1e-9)
    i_cf = math.hypot(c["hf"][950.0], 2 * math.pi * F0 * c["cf"] * V_LL * (1 + pd.V_TOL) / math.sqrt(3)) / n_cf
    k = cost(c, (pick["cost"]["total_usd"], pick["cost"]["build_5k_usd"]), l2d["usd"], cm["usd"], c["cf"])
    with using(c):
        fit, _ = pd.cosmic(c, 950.0)
    s950 = hf_split(c, 950.0)
    res = {"name": c["name"], "group": c["group"], "topology": c["topo"], "fsw_kHz": c["fsw"] / 1e3, "modulation": c["zs"] + (" + inverted middle carrier" if c["inv"] else ""),
           "devices": {p: f"{n} x {part}" for p, (part, n) in c["devs"].items()}, "n_devices": ndev, "gate_channels": 12 if c["topo"] == "TT" else 6,
           "L1_uH": c["l1"] * 1e6, "Cf_uF": c["cf"] * 1e6, "L2_uH": c["l2"] * 1e6, "lcl_window_ok": f["window_ok"],
           "f_res_kHz": (f.get("f_res_stiff", 0) / 1e3, f.get("f_res_scr5", 0) / 1e3),
           "trip_A": rq["I_trip_set"], "I_unsat_A": rq["I_trip"], "ripple_pp_A": rq["ripple_pp"],
           "hf_950V": {"dm": s950["dm"], "cm": s950["cm"], "tot": s950["tot"], "mid_cm_A": s950["mid_cm"], "vcm_fsw_V": s950["vcm_fsw"]},
           "i2_carrier_max_pct": 100 * max(hf_split(c, v)["i2_max"] for v in (600.0, 950.0)),
           "L1": {"mat": pick["mat"], "wire": f"{pick['wire'][0]} {pick['wire'][1]*1e3:.1f} mm", "N": pick["N"], "mass_kg": pick["mass"],
                  "usd": (pick["cost"]["total_usd"], pick["cost"]["build_5k_usd"]), "P_180A_750V_W": pick["P_budget_d"],
                  "P_198A_950V_W": pick["P_worst_d"], "T_hot_60C": pick["T60_d"], "cheapest_feasible_5k": rows[0]["cost"]["build_5k_usd"] if rows else None,
                  "n_feasible": len(rows), "part": {"mat": pick["mat"], "geom_m": pick["geom"], "N": pick["N"], "wire": pick["wire"]},
                  "loss_per_phase": l1t},
           "L2": {"mat": l2d["r"]["mat"], "N": l2d["r"]["N"], "mass_kg": l2d["mass"], "usd": l2d["usd"], "wire": f"{l2d['r']['wire'][0]} {l2d['r']['wire'][1]*1e3:.1f} mm",
                  "part": {"mat": l2d["r"]["mat"], "geom_m": l2d["r"]["geom"], "N": l2d["r"]["N"], "wire": l2d["r"]["wire"]},
                  "P_180A_W": l2d["r"]["P_budget_d"], "loss_per_phase": l2t},
           "cm_choke": cm, "C_f_A_per_can": i_cf, "dc_link_per_half": c["dcl"]["per_half"], "I_half_A": c["dcl"]["I_half_A"],
           "v150_V": c["v150"], "leak_lf_mA_5uF": c["v150"] * 2 * math.pi * 150 * LEAK_LF[0] * 1e3,
           "eta_peak": e[kpk], "eta_peak_at": kpk, "eta_full_750V": e[(750.0, 1.0, 0.0)],
           "eta_full_worst": min(v for (vdc, x, a), v in e.items() if x == 1.0),
           "loss_125kW_750V": {"devices": pts[(750.0, 1.0, 0.0)][1], "L1+L2": lossL(750.0, pd.I_RATED), "rest": pts[(750.0, 1.0, 0.0)][2]},
           "tj_110pct_45C": tj45, "derate_60C_k1p3": x60, "corner_2L": sorted(c["corner"]) if c.get("corner") else None,
           "v150_if_balanced_V": v150_balanced(c) if levels(c) == 3 else None,
           "v_corner": c.get("v_corner"), "FIT_950V": fit, "rule_ratio": pd.rule_ratio(c), "peak_ratio_at_trip": peak_ratio(c),
           "filter_mass_kg": 3 * pick["mass"] + 3 * l2d["mass"] + cm["mass"],
           "filter_loss_W": {"125kW_750V": lossL(750.0, pd.I_RATED), "198A_950V": 3 * (pick["P_worst_d"] + l2d["r"]["P_worst_d"])},
           "cost": {"cat": k["cat"], "5k": k["5k"], "filter": k["filter"], "rows": k["rows"]},
           "set": {"fsw": c["fsw"], "l1_rel": c["l1"] / pd.l1_for_ripple(c), "zs": c["zs"], "inv": c["inv"], "trip": c["trip"]},
           "device_W_125kW_750V": pts[(750.0, 1.0, 0.0)][1]}
    CTX[c["name"]] = (c, P, rq, rows, pts, lossL2)
    res["accept"] = {"eta": res["eta_peak"] >= pd.ETA_REQ and res["eta_full_worst"] >= ETA_FULL_MIN, "tj": m45 >= 0.0 and x60 >= 1.0,
                     "harmonics": res["i2_carrier_max_pct"] <= 100 * pd.I2_LIM_FRAC + 1e-6 and f["window_ok"],
                     "leak": res["leak_lf_mA_5uF"] <= LEAK_LF[1] * 1e3 and max(cm["earth_mA"]) <= leak_hf_limit() * 1e3 + 0.5,
                     "peak": res["peak_ratio_at_trip"] <= pv.V_PK_FRAC + 1e-9}
    res["ok"] = all(res["accept"].values())
    log(f"  {res['name']:<34} {res['cost']['5k']:7.0f} USD 5k  eta {res['eta_peak']*100:.2f}/{res['eta_full_750V']*100:.2f}  "
        f"L1 {res['L1']['usd'][1]:.0f} {pick['mat']} {res['L1']['wire']}  filter {res['cost']['filter'][1]:.0f} USD {res['filter_mass_kg']:.0f} kg  "
        f"Tj {tj45:.0f} x60 {x60:.2f}  n {ndev}  {'ok' if res['ok'] else 'FAIL ' + str([k for k, v in res['accept'].items() if not v])}")
    return res


def peak_ratio(c):
    """largest voltage a device must block at the DC over-voltage trip (1050 V), as a fraction of its rating, BEFORE any overshoot:
    full V_dc for two-level switches and T-type outer devices, V_dc/2 (+10 % midpoint band) for T-type inner devices.  The project's
    peak rule allows 0.85 of rating including the overshoot - a necessary condition only (two-level: step e's decks cover the
    overshoot; the T-type's commutation is not simulated)"""
    out = 0.0
    for pos, (part, n) in c["devs"].items():
        d = pd.dev(part)
        vr = d.get("vces") or d.get("vdss") or d.get("vrrm")
        out = max(out, pd.V_OV_TRIP * (1.0 if pd.TOPO[c["topo"]]["block"][pos] == 1.0 else 0.55) / vr)
    return out


def corner_hot(r, c, P):
    """L1 hot spot in the T-type's two-level corner (V_corner, 110 %, 45 C inlet + preheat): two-level ripple flux and HF current"""
    v = c["v_corner"]
    hot = 0.0
    for i, t_in in ((pd.I_CONT, pd.T_IN), (pd.I_RATED, T_HOT)):          # 110 % at 45 C, 100 % at 60 C
        hf = hf_split(c, v, i, 90.0, lev=2)["tot"]
        with ripple_flux(c, 2):
            core = mg.cc_core_loss(r["core"], r["N"], v, P["vph_pk"] / (v / 2), c["fsw"], i * SQ2, c["l1"])["P"]
        w = mg.cc_winding(r["core"], r["N"], r["wire"], i, hf, c["fsw"], r["gap"]["k"])
        hot = max(hot, mg.cc_thermal(r["core"], r["w"], core + w["P_cu"], w["P_cu"], t_in + P["air"]["preheat_K"], P["air"]["h"])["T_hot"])
    return hot


# ------------------------------------------------------------------------------------------------ candidates and report
SIC17, SIC12 = "SG2M040170HJ", "SG2M035120LJ"


def candidates():
    a = {"topo": "2L", "devs": {"TH": (SIC17, 6), "TL": (SIC17, 6)}, "fsw": 32e3}
    C = [dict(a, name="A0 drawn (97 uH, 32 kHz, 450 A)", group="A0")]
    C += [dict(a, name=f"A1 L1 {u:.0f} uH", group="A1", l1=u * 1e-6) for u in (60.0, 80.0, 120.0, 150.0)]
    C += [dict(a, name=f"A2 {f/1e3:.0f} kHz", group="A2", fsw=f) for f in (16e3, 24e3, 48e3)]
    C += [dict(a, name=f"A1xA2 {f/1e3:.0f} kHz, L1 {k * pd.l1_for_ripple(dict(a, fsw=f))*1e6:.0f} uH", group="A1xA2", fsw=f,
               l1=k * pd.l1_for_ripple(dict(a, fsw=f))) for f in (20e3, 24e3, 28e3) for k in (0.75, 1.0, 1.25, 1.5) if (f, k) != (24e3, 1.0)]
    C += [dict(a, name="A3 min-max everywhere (SVPWM)", group="A3", zs="minmax"),
          dict(a, name="A3 AZSPWM1", group="A3", inv=True),
          dict(a, name="A3 NSPWM (AZSPWM1 above 860 V)", group="A3", zs="dpwm1", inv=True),
          dict(a, name="A4 trip from protection need", group="A4", trip="protection")]
    t = {"topo": "TT", "fsw": 32e3, "trip": "protection"}
    C += [dict(t, name="B SiC T-type 1200 V (cross-check)", group="B", devs={"T1": (SIC12, 5), "T4": (SIC12, 5), "T2": (SIC12, 6), "T3": (SIC12, 6)}),
          dict(t, name="B rule kept: 1700 V outer", group="B", devs={"T1": (SIC17, 6), "T4": (SIC17, 6), "T2": (SIC12, 6), "T3": (SIC12, 6)}),
          dict(t, name="B-IGBT T-type 16 kHz (competitor class)", group="B-IGBT", fsw=16e3,
               devs={"T1": ("CRG40T120BK3SD", 4), "T4": ("CRG40T120BK3SD", 4), "T2": ("CRG50T60AK3SD", 3), "T3": ("CRG50T60AK3SD", 3)})]
    return C


def report(rows, best, run_up, md):
    w = md.append
    w("# PCS-P125 power stage and filter - re-optimisation with the real inductors (sim/pcs_tradeoff.py)\n")
    w("**CALCULATED, nothing measured.** Devices, heat sink, PWM and LCL: sim/pcs_design.py (E_on at the drawn R_G,on "
      f"{RG_ON:.2f} ohm, E_off x{EOFF:.2f} from its step e, L1 ripple in the device currents); L1 / L2 / AC CM choke: sim/magnetics.py "
      "(gapped nanocrystalline / amorphous C-cores only - no powder block cores), each candidate's own requirement (L(I) minima at its "
      "own peaks, its HF current, its trip).  L1 = the cheapest verified part keeping the peak efficiency >= "
      f"{ETA_DESIGN*100:.1f} % (98.5 % + 0.2-point model margin).\n")
    w("**Acceptance:** peak efficiency >= 98.5 % (AC-02); Tj <= 150 C at 110 % / 45 C (k 1.10, 120 % 2 min and 200 ms as step a) "
      f"and 100 % rated current at 60 C inlet with the hottest device at {SHARE_HOT:.2f} x the mean (D-057); every grid-current "
      f"component of the carrier band <= {pd.I2_LIM_FRAC*100:.1f} % of rated, resonance below f_s/6 (stiff) and above 2 x the "
      f"current-loop bandwidth (SCR 5); 150 Hz earth current <= {LEAK_LF[1]*1e3:.0f} mA at {LEAK_LF[0]*1e6:.0f} uF to earth, "
      f"carrier-band earth current <= the drawn design's ({leak_hf_limit()*1e3:.0f} mA rms with 150 uH) at 1-20 uF, the AC CM choke sized per "
      f"candidate; worst full-load efficiency (600-900 V, PF 1) >= {ETA_FULL_MIN*100:.1f} % (pcs_design rule); every device blocks <= "
      f"{pv.V_PK_FRAC:.2f} of its rating at the {pd.V_OV_TRIP:.0f} V DC trip before overshoot (the peak rule's necessary part - "
      "two-level overshoot is verified by step e's decks, the T-type's is not simulated).  The 0.67 rule "
      "is reported as the FIT per unit, not used as a filter.\n")
    w("| candidate | devices | f_sw | L1 / C_f / L2 | trip / unsat. (A) | 5k USD (cat) | filter 5k USD | filter kg | filter W 125 kW 750 V | peak / full eta | Tj 45 C, 60 C k1.3 current | DC film / half | L1 part | FIT 950 V | accept |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        L = r["L1"]
        w(f"| {r['name']} | {r['n_devices']} | {r['fsw_kHz']:.0f} | {r['L1_uH']:.0f} / {r['Cf_uF']:.0f} / {r['L2_uH']:.0f} | "
          f"{r['trip_A']:.0f} / {r['I_unsat_A']:.0f} | **{r['cost']['5k']:.0f}** ({r['cost']['cat']:.0f}) | {r['cost']['filter'][1]:.0f} | "
          f"{r['filter_mass_kg']:.0f} | {r['filter_loss_W']['125kW_750V']:.0f} | {r['eta_peak']*100:.2f} / {r['eta_full_750V']*100:.2f} | "
          f"{r['tj_110pct_45C']:.0f} C, {r['derate_60C_k1p3']*100:.0f} % | {r['dc_link_per_half']} | {L['mat']} {L['wire']} N{L['N']}, "
          f"{L['mass_kg']:.1f} kg, {L['usd'][1]:.0f} USD, {L['P_180A_750V_W']:.0f} W | {r['FIT_950V']:.2f} | "
          f"{'yes' if r['ok'] else 'no: ' + ', '.join(k for k, v in r['accept'].items() if not v)} |")
    w(f"\n**Recommendation: {best['name']}** - {best['cost']['5k']:.0f} USD at 5,000 units ({best['cost']['cat']:.0f} catalogue), "
      f"{run_up['cost']['5k'] - best['cost']['5k']:.0f} USD below the runner-up ({run_up['name']}).")
    return md


def details(rows, md):
    """per-lever tables and the reasons behind them"""
    w = md.append
    g = lambda k: [r for r in rows if r["group"] == k]  # noqa: E731
    a0 = g("A0")[0]
    w("\n## A1 L1 inductance and A2 switching frequency\n")
    w("| candidate | L1 uH | ripple pp 950 V (A) | trip / unsat. (A) | L1 part (5k USD, kg, W at 180 A 750 V) | devices W 125 kW 750 V | "
      "C_f A per can | DC half-bank A / caps | L2 uH | resonance stiff / SCR 5 (kHz) vs f_s/6, 2 BW | CM choke cores | 5k USD |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in g("A0") + g("A1") + g("A2") + g("A1xA2") + g("A*"):
        L = r["L1"]
        fs = 2 * r["fsw_kHz"]
        w(f"| {r['name']} | {r['L1_uH']:.0f} | {r['ripple_pp_A']:.0f} | {r['trip_A']:.0f} / {r['I_unsat_A']:.0f} | {L['mat']} {L['wire']}: "
          f"{L['usd'][1]:.0f}, {L['mass_kg']:.1f}, {L['P_180A_750V_W']:.0f} | {r['device_W_125kW_750V']:.0f} | {r['C_f_A_per_can']:.1f} | "
          f"{r['I_half_A']:.0f} / {r['dc_link_per_half']} | {r['L2_uH']:.0f} | {r['f_res_kHz'][0]:.1f} / {r['f_res_kHz'][1]:.1f} vs "
          f"{fs/6:.1f}, {2*fs/64:.1f}{'' if r['lcl_window_ok'] else ' (NOT met)'} | {r['cm_choke']['k']} | {r['cost']['5k']:.0f} |")
    w("\n## A3 reduced common-mode PWM (32 kHz, 97 uH)\n")
    w("| modulation | L1 HF 950 V: DM / CM / per phase (A rms) | CM into the DC midpoint (A rms) | CM at f_sw (V pk) | devices W 125 kW 750 V | "
      "carrier band max (% of rated) | 150 Hz CM (V rms) -> mA at 5 uF | CM choke cores | L1 5k USD | DC film per half | 5k USD |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in g("A0") + g("A3"):
        h = r["hf_950V"]
        w(f"| {r['modulation']} | {h['dm']:.1f} / {h['cm']:.1f} / {h['tot']:.1f} | {h['mid_cm_A']:.0f} | {h['vcm_fsw_V']:.0f} | "
          f"{r['device_W_125kW_750V']:.0f} | {r['i2_carrier_max_pct']:.3f} | {r['v150_V']:.0f} -> {r['leak_lf_mA_5uF']:.0f} | "
          f"{r['cm_choke']['k']} | {r['L1']['usd'][1]:.0f} | {r['dc_link_per_half']} | {r['cost']['5k']:.0f} |")
    w("\nWith the C_f star on the DC midpoint each L1 sees its own leg against the midpoint, so its ripple is set by that leg's reference "
      "alone: moving the carrier of the middle phase (AZSPWM1) shifts current from the common-mode path to the differential path but "
      "leaves the per-phase HF current, the core flux and the L1 unchanged; it cuts the common-mode current into the DC midpoint and "
      "the earth current (fewer CM-choke cores).  Only a different REFERENCE changes L1: min-max (SVPWM) and DPWM1 (NSPWM) flatten "
      "the leg reference and lower the ripple, at the price of a 150 Hz zero sequence at every DC voltage (earth leakage through the "
      f"battery's capacitance; limit {LEAK_LF[1]*1e3:.0f} mA at {LEAK_LF[0]*1e6:.0f} uF).  NSPWM has no zero states only for "
      f"m >= {M_NSPWM} (about 860 V DC at 400 V AC); above, it falls back to AZSPWM1.  All of it is firmware (ePWM action qualifiers).")
    a4 = g("A4")[0]
    pk = pd.I_200MS * SQ2
    w("\n## A4 hardware over-current window\n")
    w(f"Protection needs: the window must clear the largest operating peak - the 200 ms overload, 1.2 x 216 A = {pd.I_200MS:.0f} A rms = "
      f"{pk:.0f} A peak, plus half the ripple ({a0['ripple_pp_A']/2:.0f} A at 97 uH, 950 V) = {pk + a0['ripple_pp_A']/2:.0f} A - with "
      f"{TRIP_TOL*100:.0f} % for the TMR gain and ladder tolerances: {a4['trip_A']:.0f} A.  The core must then carry the trip plus the "
      f"overshoot in the {T_TRIP*1e6:.1f} us trip path (V_dc,trip / 2 across 0.9 L1: {a4['I_unsat_A'] - a4['trip_A']:.0f} A) "
      f"unsaturated: {a4['I_unsat_A']:.0f} A against the drawn 450 A.  So +-450 A is needed within "
      f"{(450.0 / a4['I_unsat_A'] - 1) * 100:.0f} %; the core shrinks by about that (L x I_trip sets the gapped C-core's section), L1 "
      f"{a0['L1']['usd'][1]:.0f} -> {a4['L1']['usd'][1]:.0f} USD at 5k.  A lower window would trip the unit inside its own 200 ms "
      "overload.  The drawn 450 A had no allowance for the trip-path overshoot - at 97 uH that is covered only because 450 A sits 25 A "
      "above the need.  Powder block cores (FeSiAl / high flux, not in the magnetics search) roll off softly and may be allowed to sit at "
      "50 % of L at the trip instead of below saturation - that is the case where powder could be cheaper; it needs a powder-core pass.")
    w("\n## B SiC T-type and the 0.67 rule\n")
    for r in g("B") + g("B-IGBT"):
        cr = r["corner_2L"] or []
        vs = sorted({v for v, a in cr})
        an = sorted({abs(a) for v, a in cr})
        w(f"- **{r['name']}**: {r['n_devices']} devices ({', '.join(f'{p} {v}' for p, v in r['devices'].items())}), 12 channels, 5 bias "
          f"supplies; L1 {r['L1_uH']:.0f} uH ({r['L1']['mat']} {r['L1']['wire']}, {r['L1']['mass_kg']:.1f} kg, {r['L1']['usd'][1]:.0f} USD); "
          f"two-level corner (where min-max PWM alone would ripple the 5 + 5 bank's midpoint by more than 60 V pp): V_dc {vs[0] if vs else 0:.0f}-"
          f"{vs[-1] if vs else 0:.0f} V at |PF angle| {an[0] if an else 0:.0f}-{an[-1] if an else 0:.0f} deg; there the L1 sees full-V_dc "
          f"steps (ripple {r['ripple_pp_A']:.0f} A pp, trip {r['trip_A']:.0f} A); balancing the midpoint actively instead (ideal "
          f"carrier-based control) would put up to "
          f"{r['v150_if_balanced_V']:.0f} V rms of 150 Hz on the battery ({r['v150_if_balanced_V']*2*math.pi*150*LEAK_LF[0]*1e3:.0f} mA at 5 uF); "
          f"devices block {r['peak_ratio_at_trip']:.2f} of their rating at the 1050 V trip before overshoot (rule 0.85); "
          f"FIT {r['FIT_950V']:.1f} at 950 V; "
          f"{r['cost']['5k']:.0f} USD at 5k, peak {r['eta_peak']*100:.2f} %; "
          + ("meets every acceptance limit." if r["ok"] else "fails: " + ", ".join(k for k, v in r["accept"].items() if not v) + "."))
    b, bk = g("B")[0], g("B")[1]
    w(f"\n**Cost of the 0.67 rule:** two-level - none: 1200 V devices at the 1050 V DC trip would exceed the 0.85 x V_DSS peak rule "
      f"before any overshoot, so the 1700 V part is needed anyway.  T-type - 1700 V outer devices cost {bk['cost']['5k'] - b['cost']['5k']:+.0f} "
      f"USD at 5k and take the FIT per unit from {b['FIT_950V']:.1f} to {bk['FIT_950V']:.2f} (950 V, 25 C, sea level; Wolfspeed Gen 3 "
      "curve as proxy - no Chinese maker publishes cosmic-ray data).")
    return md


def margin_cost(r):
    """what the efficiency rules cost on the chosen two-level design: L1 re-picked at 98.5 % peak (no model margin) and without the
    full-load floor"""
    c, P, rq, rows, pts, lossL2 = CTX[r["name"]]
    mg._PCS_OM.clear()
    out = ["\n## What the efficiency rules cost (L1 re-picked, " + r["name"] + ")\n",
           f"Cheapest L1 that passes magnetics' own thermal screen: {r['L1']['cheapest_feasible_5k']:.0f} USD; the chosen part's hot spot is "
           f"{r['L1']['T_hot_60C']:.0f} C at 60 C inlet (limit 140 C, OpenMagnetics-verified losses).\n",
           "| rule | L1 part | L1 5k USD each | 3 x L1 vs chosen |", "|---|---|---|---|"]
    for tag, pk, fl in (("chosen: peak >= 98.7 %, full load >= 98.0 %", None, None), ("peak >= 98.5 % (no model margin), full load >= 98.0 %", pd.ETA_REQ, None),
                        ("peak >= 98.5 %, no full-load floor", pd.ETA_REQ, 1e-9)):
        x = pick_l1(c, P, rq, rows, pts, lossL2, pk, fl)
        if x:
            out.append(f"| {tag} | {x['mat']} {x['wire'][0]} N{x['N']}, {x['mass']:.1f} kg | {x['cost']['build_5k_usd']:.0f} | "
                       f"{3 * (x['cost']['build_5k_usd'] - r['L1']['usd'][1]):+.0f} |")
    return out


def conclusion(rows, best, run_up, best2):
    """the decision text (also read by pcs_design.py report section h)"""
    a0 = next(r for r in rows if r["group"] == "A0")
    out = [f"**Recommended: {best['name']}**, {best['cost']['5k']:.0f} USD at 5,000 units ({best['cost']['cat']:.0f} catalogue), "
           f"{run_up['cost']['5k'] - best['cost']['5k']:.0f} USD ({(run_up['cost']['5k'] / best['cost']['5k'] - 1) * 100:.1f} %) below the "
           f"runner-up {run_up['name']}; peak efficiency {best['eta_peak']*100:.2f} %, filter {best['cost']['filter'][1]:.0f} USD / "
           f"{best['filter_mass_kg']:.0f} kg.  The D-053 design corrected (A0) costs {a0['cost']['5k']:.0f} USD."]
    twins = [r for r in rows if r is not best and r["ok"] and r["topology"] == best["topology"] and r["fsw_kHz"] == best["fsw_kHz"]
             and abs(r["L1_uH"] - best["L1_uH"]) < 0.5]
    if twins:
        out.append("Same design point, other settings: " + "; ".join(f"{r['name']} ({r['modulation']}, trip {r['trip_A']:.0f} A) "
                                                                   f"{r['cost']['5k']:.0f} USD" for r in twins)
                   + (" - at this point the L1 is already the cheapest part that passes the thermal screen, so a flatter reference buys "
                      "nothing and D-053's sinusoidal policy stays." if best["set"]["zs"] == "policy" and not best["set"]["inv"] else "."))
    tt = [r for r in rows if r["topology"] == "TT"]
    tok = [r for r in tt if r["ok"]]
    bt = min(tok or tt, key=lambda r: r["cost"]["5k"])
    out.append(f"Cheapest {'acceptable ' if tok else ''}T-type: {bt['name']}, {bt['cost']['5k']:.0f} USD "
               f"({bt['cost']['5k'] - best2['cost']['5k']:+.0f} USD against the best two-level)"
               + ("" if tok else " - none meets every limit (" + ", ".join(sorted({k for r in tt for k, v in r["accept"].items() if not v})) + ")")
               + ".  The sweep's resolution is about +-20 USD: the magnetics grid is discrete and the cheapest part that meets the "
               "efficiency rules jumps between amorphous and nanocrystalline designs.")
    if best["topology"] != "2L":
        out.append(f"The T-type wins on these numbers; pcs_design.py stays on the best two-level variant ({best2['name']}, "
                   f"{best2['cost']['5k']:.0f} USD) until the owner decides.  What would change: topology T-type (66+ devices, 12 gate "
                   "channels, 5 bias supplies, a T-type state interlock), firmware two-level mode in the low-PF corner and midpoint control, "
                   "a 150 Hz zero sequence at every DC voltage, outer devices at 0.79 of rating (FIT in the table), new commutation "
                   "decks for the 1200 V devices.")
    else:
        out.append(f"pcs_design.py carries this design ({best2['name']}): f_sw {best2['fsw_kHz']:.0f} kHz, L1 {best2['L1_uH']:.0f} uH, "
                   f"C_f {best2['Cf_uF']:.0f} uF, L2 {best2['L2_uH']:.0f} uH, trip {best2['trip_A']:.0f} A, modulation {best2['modulation']}; "
                   "inductor cost, mass and loss from sim/magnetics.py.")
    return out


def self_check(rows, P):
    a0 = next(r for r in rows if r["group"] == "A0")
    ref = SPEC["cost_usd"]["three_wire"]                 # the BOM pcs_design.py wrote, rebuilt from this script's cost rows
    unit = lambda f: next((r["unit_cat_usd"], r["unit_5k_usd"]) for r in ROWS3 if f(r))  # noqa: E731
    devs = {p: (x["part"], x["n_parallel"]) for p, x in SPEC["design"]["devices"].items()}
    c = dict(name="spec", topo="2L", devs=devs, dcl={"per_half": SPEC["dc_link"]["per_half"]})
    old = cost(c, unit(lambda r: r["block"] == "LCL FILTER" and r["function"].startswith("L1 ")),
               unit(lambda r: r["block"] == "LCL FILTER" and r["function"].startswith("L2 ")),
               unit(lambda r: "common-mode choke" in r["function"]), SPEC["lcl"]["filter"]["Cf"])
    assert abs(old["5k"] - ref["5k"]) < 0.5 and abs(old["cat"] - ref["catalogue"]) < 0.5, ("cost bookkeeping", old["5k"], ref["5k"])
    e1 = 0.5 * a0["L1_uH"] * 1e-6 * (pd.I_2MIN * SQ2 * (1 + pd.RIPPLE_PP / 2)) ** 2
    assert a0["L1"]["usd"][1] > 2 * (pd.L1_COST[0] + pd.L1_COST[1] * e1) * 0.8, "real L1 dearer than the 10 + 6 USD/J estimate"
    h, cc = fourier(2, 0.85, 32e3, "none", inv=True)
    t, legs = wave(2, 0.85, 32e3, "none", inv=True)
    assert np.max(np.abs(legs.mean(0))) < 0.34, "AZSPWM1 must avoid the zero states (CM <= V_dc/6)"
    assert all(r["eta_peak"] > 0.97 and r["cost"]["5k"] > 500 for r in rows)
    return True


def combined(rows):
    """A*: the two-level design at the cheapest acceptable (f_sw, L1) of the A1 / A2 / A1xA2 sweeps, plus the A3 modulation and the A4
    trip where they beat A0"""
    a0 = next(r for r in rows if r["group"] == "A0")
    fl = min((r for r in rows if r["group"] in ("A0", "A1", "A2", "A1xA2") and r["ok"]), key=lambda r: r["cost"]["5k"])
    st = dict(a0["set"], fsw=fl["set"]["fsw"], l1_rel=fl["set"]["l1_rel"])          # f_sw and L1 from the cheapest of the sweeps
    for g, keys in (("A3", ("zs", "inv")), ("A4", ("trip",))):                       # firmware levers, where they beat A0
        ok = [r for r in rows if r["group"] == g and r["ok"] and r["cost"]["5k"] < a0["cost"]["5k"]]
        if ok:
            st.update({k: min(ok, key=lambda r: r["cost"]["5k"])["set"][k] for k in keys})
    out = []
    for tag, zs, inv in (("levers combined", st["zs"], st["inv"]), ("same, sinusoidal PWM (no A3)", "policy", False)):
        c = dict(candidates()[0], name=f"A* two-level, {tag}", group="A*", fsw=st["fsw"], zs=zs, inv=inv, trip=st["trip"])
        c["l1"] = st["l1_rel"] * pd.l1_for_ripple(c)
        out.append(c)
        if (st["zs"], st["inv"]) == ("policy", False):
            break
    return out


def main():
    os.chdir(os.path.dirname(HERE))
    P = mg.pcs_inputs()
    log = print
    log("L2 designs (magnetics.pcs_l2_options per inductance) ...")
    L2T = l2_designs(P)
    rows = []
    dump = lambda: json.dump({"rows": rows}, open(os.path.join(OUT, "tradeoff.json"), "w"), indent=1,  # noqa: E731
                             default=lambda o: o.item() if isinstance(o, np.generic) else str(o))
    for c in candidates():
        rows.append(assess(c, P, L2T, log))
        dump()
    for c in combined(rows):                               # every two-level lever at its cheapest acceptable setting
        rows.append(assess(c, P, L2T, log))
        dump()
    # cheapest acceptable; a tie (same whole dollar) goes to D-053's modulation (no 150 Hz zero sequence above ~680 V)
    ok = sorted((r for r in rows if r["ok"]), key=lambda r: (round(r["cost"]["5k"]), r["set"]["zs"] != "policy" or r["set"]["inv"]))
    best = ok[0]
    same = lambda r: r["topology"] == best["topology"] and r["fsw_kHz"] == best["fsw_kHz"] and abs(r["L1_uH"] - best["L1_uH"]) < 0.5  # noqa: E731
    run_up = next(r for r in ok[1:] if not same(r))           # the runner-up is a different design point, not a modulation twin
    best2 = next(r for r in ok if r["topology"] == "2L")
    md = details(rows, report(rows, best, run_up, []))
    md += margin_cost(best2)
    concl = conclusion(rows, best, run_up, best2)
    md += ["\n## Conclusion\n"] + concl
    open(os.path.join(OUT, "tradeoff.md"), "w").write("\n".join(md) + "\n")
    json.dump({"basis": md[1].strip() + " " + md[2].strip(), "rows": rows, "recommended": best["name"], "runner_up": run_up["name"],
               "two_level_best": best2["name"], "conclusion": concl}, open(os.path.join(OUT, "tradeoff.json"), "w"), indent=1,
              default=lambda o: o.item() if isinstance(o, np.generic) else str(o))
    self_check(rows, P)
    print("pcs_tradeoff self-check passed")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"pcs_tradeoff SELF-CHECK FAILED: {e}")
        sys.exit(1)
