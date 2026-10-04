"""PV-P75 / PV-P100-110 centralized MPPT study (PV-02, PV-C1, PV-C2): PV array model and tracking performance.

Run:  .venv/bin/python sim/pv_mppt.py      (after sim/pv_control.py: reads sim/out/pv_control/control_spec.json for the
      V_A-loop bandwidth and the port measurement chain; falls back to the values below if it is missing)
Out:  sim/out/pv_mppt/{report.md, *.png, *.csv, mppt_result.json}; adds the "mppt" section to control_spec.json.

Calculated, not measured.  Module data are a GENERIC 545 W class (144 half-cut mono PERC) datasheet set, not a product.
Array model: single-diode (De Soto five-parameter temperature/irradiance laws), three bypass diodes per module.
Tracking model: quasi-static - the closed V_A loop (sim/pv_control.py) is a first-order lag with its closed-loop
bandwidth; the converter's current / power limits clamp the PV current; the MPPT sees window averages of the port-A
voltage and current measured through the real chain (12-bit ADC LSB, isolated-amplifier noise, ADC noise, calibrated
offset, interleaving ripple at the synchronous sampling instant).  The quasi-static model is checked against the
averaged switching-cycle model of sim/pv_control.py in that script's self-check.
"""
import json
import math
import os
import sys

import numpy as np
from scipy.optimize import fsolve
from scipy.special import lambertw, ndtr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out", "pv_mppt")
SPEC = os.path.join(HERE, "out", "pv_control", "control_spec.json")

# ------------------------------------------------------------------------------------------------ PV module (assumption)
# generic 545 W class module, typical 2022-2025 datasheet values (STC 1000 W/m2, 25 C, AM1.5); n = ideality (assumed)
MOD = dict(pmp=545.0, vmp=41.80, imp=13.04, voc=49.65, isc=13.92, a_isc=0.00048, b_voc=-0.0027, g_pmp=-0.0035,
           ncell=72, nsub=3, n=1.10, v_bypass=0.5)
K_B, Q_E = 1.380649e-23, 1.602176634e-19
T_REF, G_REF, EG_REF = 298.15, 1000.0, 1.121


def lambertw_exp(x):
    """W(exp(x)) for any real x (vectorised); Newton in log form where exp(x) would overflow"""
    x = np.asarray(x, dtype=float)
    out = np.empty_like(x)
    lo = x < 200.0
    out[lo] = np.real(lambertw(np.exp(x[lo])))
    if np.any(~lo):
        xs = x[~lo]
        w = xs - np.log(xs)
        for _ in range(6):
            w = w - (w + np.log(w) - xs) / (1.0 + 1.0 / w)
        out[~lo] = w
    return out


def fit_module(m=MOD):
    """single-diode parameters at STC from the datasheet points (n fixed): I_L, I_0, R_s, R_sh"""
    a = m["n"] * m["ncell"] * K_B * T_REF / Q_E

    def eqs(x):
        il, ln_i0, rs, rsh = x
        i0 = math.exp(ln_i0)
        vm = m["vmp"] + m["imp"] * rs
        gd = i0 / a * math.exp(vm / a) + 1.0 / rsh
        return [il - i0 * (math.exp(m["isc"] * rs / a) - 1) - m["isc"] * rs / rsh - m["isc"],
                il - i0 * (math.exp(m["voc"] / a) - 1) - m["voc"] / rsh,
                il - i0 * (math.exp(vm / a) - 1) - vm / rsh - m["imp"],
                m["imp"] - m["vmp"] * gd / (1 + rs * gd)]
    x0 = [m["isc"], math.log(m["isc"]) - m["voc"] / a, 0.2, 300.0]
    sol, info, ok, msg = fsolve(eqs, x0, full_output=True, xtol=1e-12)
    assert ok == 1 and max(abs(np.array(eqs(sol)))) < 1e-6, msg
    il, ln_i0, rs, rsh = sol
    assert rs > 0 and rsh > 0
    fit = dict(IL=il, I0=math.exp(ln_i0), Rs=rs, Rsh=rsh, a=a, Eg=EG_REF)
    # fifth parameter: the I_0 temperature law's band-gap term is fitted so that dVoc/dT equals the datasheet value
    # (the generic 1.121 eV gives -0.35 %/K against the datasheet -0.27 %/K)
    def beta(eg):
        fit["Eg"] = eg
        v1, v2 = (float(v_from_i(np.array([0.0]), *params(G_REF, tc, fit))[0]) for tc in (20.0, 30.0))
        return (v2 - v1) / 10.0 / m["voc"] - m["b_voc"]
    lo, hi = 0.8, 2.5
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if beta(mid) < 0 else (mid, hi)
    fit["Eg"] = 0.5 * (lo + hi)
    return fit


def params(G, Tc, fit=None, m=MOD):
    """De Soto translation to irradiance G [W/m2] and cell temperature Tc [C] (arrays allowed)"""
    fit = FIT if fit is None else fit
    G = np.maximum(np.asarray(G, dtype=float), 1e-3)
    T = np.asarray(Tc, dtype=float) + 273.15
    a = fit["a"] * T / T_REF
    il = G / G_REF * (fit["IL"] + m["a_isc"] * m["isc"] * (T - T_REF))
    eg0 = fit["Eg"]
    eg = eg0 * (1 - 0.0002677 * (T - T_REF))
    i0 = fit["I0"] * (T / T_REF) ** 3 * np.exp(eg0 * Q_E / (K_B * T_REF) - eg * Q_E / (K_B * T))
    rsh = fit["Rsh"] * G_REF / G
    return il, i0, fit["Rs"], rsh, a


FIT = None


def i_from_v(v, il, i0, rs, rsh, a):
    """module current at module voltage v (Lambert-W closed form, pvlib i_from_v)"""
    k = 1.0 + rs / rsh
    lnth = np.log(rs * i0 / (a * k)) + (rs * (il + i0) + v) / (a * k)
    return (il + i0 - v / rsh) / k - a / rs * lambertw_exp(lnth)


def v_from_i(i, il, i0, rs, rsh, a):
    """module (or sub-string, with scaled a/rs/rsh) voltage at current i (pvlib v_from_i)"""
    lnps = np.log(i0 * rsh / a) + (il + i0 - i) * rsh / a
    return (il + i0 - i) * rsh - i * rs - a * lambertw_exp(lnps)


FIT = fit_module()


# ------------------------------------------------------------------------------------------------ array
def array(ns, npar, shade=None, name=""):
    """ns modules per string, npar strings; shade = (n_substrings_shaded per string, irradiance factor) or None"""
    return dict(ns=ns, np=npar, shade=shade, name=name or f"{ns}s x {npar}p")


def array_i(arr, G, Tc, V):
    """array current [A] at array voltage V [V] (G, V may be arrays of the same shape)"""
    V = np.asarray(V, dtype=float)
    if arr["shade"] is None:
        il, i0, rs, rsh, a = params(G, Tc)
        return arr["np"] * i_from_v(V / arr["ns"], il, i0, rs, rsh, a)
    tab_i, tab_v = string_vi(arr, float(np.mean(G)), float(np.mean(Tc)))
    return arr["np"] * np.interp(V, tab_v[::-1], tab_i[::-1], left=tab_i[-1] * 0 + tab_i[0], right=-1e3)


_SVI = {}


def string_vi(arr, G, Tc, n=6000):
    key = (arr["ns"], arr["shade"], round(float(G), 6), round(float(Tc), 6), n)
    if key not in _SVI:
        _SVI[key] = _string_vi(arr, G, Tc, n)
    return _SVI[key]


def _string_vi(arr, G, Tc, n=6000):
    """string V(I) with one bypass diode per sub-string (MOD nsub per module); returns (I grid, V)"""
    nsub_tot = arr["ns"] * MOD["nsub"]
    n_sh, fac = arr["shade"] if arr["shade"] else (0, 1.0)
    il, i0, rs, rsh, a = params(G, Tc)
    k = MOD["nsub"]
    i = np.linspace(-0.5, il * 1.001, n)

    def vsub(g_fac):
        il2, i02, rs2, rsh2, a2 = params(G * g_fac, Tc)
        v = v_from_i(i, il2, i02, rs2 / k, rsh2 / k, a2 / k)
        return np.maximum(v, -MOD["v_bypass"])
    v = (nsub_tot - n_sh) * vsub(1.0) + (n_sh * vsub(fac) if n_sh else 0.0)
    return i, v


def array_curve(arr, G, Tc, n=4000):
    """(V, I, P) over 0..Voc"""
    if arr["shade"] is None:
        il, i0, rs, rsh, a = params(G, Tc)
        voc = arr["ns"] * float(v_from_i(np.array([0.0]), il, i0, rs, rsh, a)[0])
        V = np.linspace(0.0, voc, n)
        I = array_i(arr, G, Tc, V)
    else:
        ti, tv = string_vi(arr, G, Tc)
        I = arr["np"] * ti[::-1]
        V = tv[::-1]
        keep = V >= 0
        V, I = V[keep], I[keep]
    return V, I, V * I


def mpp(arr, G, Tc):
    """global maximum power point (V, I, P) by dense search + parabolic refinement"""
    V, I, P = array_curve(arr, G, Tc)
    k = int(np.argmax(P))
    if 0 < k < len(P) - 1:
        lo, hi = V[k - 1], V[k + 1]
        vv = np.linspace(lo, hi, 201)
        pp = vv * array_i(arr, np.full_like(vv, G), Tc, vv)
        j = int(np.argmax(pp))
        return float(vv[j]), float(pp[j] / vv[j]), float(pp[j])
    return float(V[k]), float(I[k]), float(P[k])


def local_maxima(arr, G, Tc):
    V, I, P = array_curve(arr, G, Tc)
    idx = [k for k in range(1, len(P) - 1) if P[k] >= P[k - 1] and P[k] > P[k + 1] and P[k] > 0.02 * P.max()]
    return [(float(V[k]), float(P[k])) for k in idx]


# ------------------------------------------------------------------------------------------------ MPPT algorithm
# dP-P&O (Sera et al., IEEE TIE 55(7) 2008): a mid-period power sample separates the perturbation's effect from the
# irradiance drift; step adapted to the normalised slope (elasticity dlnP/dlnV); global scan on schedule / on request.
MPPT = dict(T_p=0.05, w=0.020, dv_min_rel=0.006, dv_max_rel=0.03, a_step=0.03, v_lo=255.0, v_hi=1000.0,
            scan_rate=1000.0, scan_period=300.0, scan_lo_rel=0.45, oversample_sps=500e3, pin_probe=4)


def mppt_init(p, v0, t0=0.0):
    return dict(vref=float(v0), dv=p["dv_max_rel"] * v0, dirn=-1.0, pk=None, px=None, phase=0, dv_last=0.0,
                scan=None, t_scan=t0, limited=False)


def mppt_tick(p, s, vbar, pbar, limited, t):
    """called every T_p/2 with the measured window averages; returns the new V_A reference (s is updated)"""
    if s["scan"] is not None:
        return s["vref"]
    if limited:                                   # a converter limit holds the operating point: track, do not wind up;
        # the limited point is right of the MPP, so the first step after release goes left with the large step
        s.update(vref=vbar, dv=p["dv_max_rel"] * vbar, pk=None, px=None, phase=0, dirn=-1.0, limited=True)
        return s["vref"]
    if s["limited"]:
        s["limited"] = False
    if s["phase"] == 0:
        s["px"], s["phase"] = pbar, 1
        return s["vref"]
    if s["pk"] is not None and s["px"] is not None and s["dv_last"] != 0.0:
        dp = (s["px"] - s["pk"]) - (pbar - s["px"])
        if dp < 0.0:
            s["dirn"] = -s["dirn"]
        el = abs(dp) / max(pbar, 1.0) / max(abs(s["dv_last"]) / max(vbar, 1.0), 1e-6)
        s["dv"] = min(max(p["a_step"] * el * vbar, p["dv_min_rel"] * vbar), p["dv_max_rel"] * vbar)
    s["pk"], s["phase"] = pbar, 0
    v_new = min(max(s["vref"] + s["dirn"] * s["dv"], p["v_lo"]), p["v_hi"])
    if abs(v_new - s["vref"]) < 1e-9:             # pinned at the floor / ceiling: probe away with the minimum step,
        s["pin"] = s.get("pin", 0) + 1            # and only every p["pin_probe"]-th period (the probe itself costs)
        if s["pin"] % p["pin_probe"]:
            s["dv_last"] = 0.0
            return s["vref"]
        s["dirn"], s["dv"] = -s["dirn"], p["dv_min_rel"] * vbar
        v_new = min(max(s["vref"] + s["dirn"] * s["dv"], p["v_lo"]), p["v_hi"])
    s["dv_last"] = v_new - s["vref"]
    s["vref"] = v_new
    if p["scan_period"] and t - s["t_scan"] >= p["scan_period"]:
        start_scan(p, s, vbar, t)
    return s["vref"]


def start_scan(p, s, vbar, t):
    s["scan"] = dict(v_hi=s["vref"] if s["vref"] > vbar else vbar, best_v=vbar, best_p=-1.0)
    s["scan"]["v_hi"] = min(max(s["scan"]["v_hi"] * 1.0, p["v_lo"]), p["v_hi"])
    s["t_scan"] = t


def scan_step(p, s, v_meas, p_meas, dt, v_lo_abs):
    """1 ms step of a global scan: sweep the reference down at scan_rate, remember the best measured point"""
    sc = s["scan"]
    if p_meas > sc["best_p"]:
        sc["best_p"], sc["best_v"] = p_meas, v_meas
    s["vref"] -= p["scan_rate"] * dt
    if s["vref"] <= v_lo_abs:
        s.update(vref=sc["best_v"], scan=None, pk=None, px=None, phase=0, dv=p["dv_min_rel"] * sc["best_v"],
                 dv_last=0.0, dirn=-1.0)
    return s["vref"]


# ------------------------------------------------------------------------------------------------ measurement chain
def chain_defaults():
    """port-A measurement chain and V_A-loop response, from sim/pv_control.py (same parameter source)"""
    sys.path.insert(0, HERE)
    import pv_control as pc
    P = pc.load_params(3)
    G = pc.design(P)
    vm, im, pm = mpp(array(17, 8), 1000.0, 25.0)
    op = pc.op_point(P, 3, vm, 800.0, pm, "mppt", g_rel=1.0)
    y = pc.lin_step(P, G, op, 0.0, "v", 400)
    tau = (int(np.argmax(y >= 1 - math.exp(-1))) + 0.5) * G["Tv"]
    return dict(lsb_v=P["lsb_v"], lsb_i=P["lsb_ip"], sig_v=P["sig_v"], sig_i=P["sig_ip"], tau=tau, imax=3 * P["imax"],
                pmax=3 * P["pmax"], v_lo=MPPT["v_lo"], off_i=0.0, bias_v=0.0, sps=MPPT["oversample_sps"], quant=True, noise=True,
                n_corr=1.0, P=P, G=G)


def qmean(x, sig, lsb):
    """expected value of a quantised sample of x + N(0, sig) (sig = 0: plain rounding)"""
    x = np.asarray(x, dtype=float)
    if sig <= 1e-12:
        return np.round(x / lsb) * lsb
    k0 = np.round(x / lsb)
    acc = np.zeros_like(x)
    for d in range(-8, 9):
        k = k0 + d
        acc += k * lsb * (ndtr(((k + 0.5) * lsb - x) / sig) - ndtr(((k - 0.5) * lsb - x) / sig))
    return acc


def measure(vw, iw, ch, rng):
    """window averages as the firmware sees them: N oversampled 12-bit samples of V_A and I_A"""
    n = max(1.0, ch["sps"] * MPPT["w"] / ch["n_corr"])
    v, i = float(np.mean(vw)) + ch["bias_v"], float(np.mean(iw)) + ch["off_i"]
    if ch["quant"]:
        sv, si = (ch["sig_v"], ch["sig_i"]) if ch["noise"] else (0.0, 0.0)
        v = float(qmean(v, sv, ch["lsb_v"]))
        i = float(qmean(i, si, ch["lsb_i"]))
    if ch["noise"]:
        v += rng.normal(0.0, math.hypot(ch["sig_v"], ch["lsb_v"] / math.sqrt(12)) / math.sqrt(n))
        i += rng.normal(0.0, math.hypot(ch["sig_i"], ch["lsb_i"] / math.sqrt(12)) / math.sqrt(n))
    return v, v * i


def p_avail(arr, G, Tc, imax=None, pmax=None, vmin=None):
    """highest power the module may take from the array inside its MPPT window: max over V >= vmin of
    V x min(I_pv, I_max, P_max / V) (= P_mpp when no limit binds) - the reference for the tracking efficiency; what
    lies outside (curtailment, MPP below the floor) is reported separately as limit / range loss"""
    V, I, P = array_curve(arr, G, Tc)
    if imax is None:
        return float(P.max())
    vmin = MPPT["v_lo"] if vmin is None else vmin
    k = V >= vmin
    if not np.any(k):
        return 0.0
    return float(np.max(V[k] * np.minimum(I[k], np.minimum(imax, pmax / np.maximum(V[k], 1.0)))))


def pmpp_table(arr, Tc, gs=None, imax=None, pmax=None):
    gs = np.concatenate([[1.0, 2.0, 5.0], np.arange(10.0, 1301.0, 10.0)]) if gs is None else gs
    return gs, np.array([p_avail(arr, g, Tc, imax, pmax) for g in gs])


def op_limited(arr, G, Tc, V, ch):
    """operating point when the module limits clamp the current (right of the MPP): I_pv(V) = min(I_max, P_max / V)"""
    I = array_i(arr, G, Tc, V)
    lim = np.minimum(ch["imax"], ch["pmax"] / np.maximum(V, 1.0))
    over = I > lim
    if not np.any(over):
        return V, I, over
    lo, hi = V[over].copy(), np.full(int(over.sum()), 1300.0)
    g = G[over] if np.ndim(G) else G
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        f = array_i(arr, g, Tc, mid) - np.minimum(ch["imax"], ch["pmax"] / mid)
        lo, hi = np.where(f > 0, mid, lo), np.where(f > 0, hi, mid)
    V = V.copy()
    I = I.copy()
    V[over] = 0.5 * (lo + hi)
    I[over] = array_i(arr, g, Tc, V[over])
    return V, I, over


def run_mppt(arr, gfun, Tc, t_end, ch, prm=None, v0=None, t_eval=0.0, seed=7, record=False, ptab=None, start_v=None):
    """quasi-static tracking simulation, 1 ms steps; returns energy-weighted MPPT efficiency over [t_eval, t_end]"""
    prm = dict(MPPT) if prm is None else prm
    rng = np.random.default_rng(seed)
    gs, ps = ptab if ptab is not None else pmpp_table(arr, Tc)
    dt = 1e-3
    nstep = int(round(prm["T_p"] / 2 / dt))
    nw = int(round(prm["w"] / dt))
    if v0 is None:
        g0 = float(gfun(np.array([0.0]))[0])
        v0 = float(array_curve(arr, g0, Tc)[0][-1])
    s = mppt_init(prm, v0)
    v_now = v0
    t = 0.0
    e_dc = e_mpp = 0.0
    rec = {"t": [], "v": [], "p": [], "pm": [], "vref": [], "lim": []} if record else None
    lim_flag = False
    nt = int(round(t_end / (nstep * dt)))
    for _ in range(nt):
        ts = t + dt * np.arange(1, nstep + 1)
        g = gfun(ts)
        if s["scan"] is not None:                     # 1 ms resolution during a global scan
            vv, ii = np.empty(nstep), np.empty(nstep)
            for k in range(nstep):
                vref = s["vref"]
                v_now = vref + (v_now - vref) * math.exp(-dt / ch["tau"])
                vk, ik, _ = op_limited(arr, np.array([g[k]]), Tc, np.array([max(v_now, ch["v_lo"])]), ch)
                vv[k], ii[k] = vk[0], ik[0]
                if s["scan"] is not None:
                    vm, pm_ = measure(vv[k:k + 1], ii[k:k + 1], dict(ch, sps=ch["sps"] / nw), rng)
                    scan_step(prm, s, vm, pm_, dt, max(prm["v_lo"], prm["scan_lo_rel"] * s["scan"]["v_hi"]))
            lim = np.zeros(nstep, bool)
        else:
            vref = s["vref"]
            vv = vref + (v_now - vref) * np.exp(-(ts - t) / ch["tau"])
            vv = np.maximum(vv, ch["v_lo"])
            vv, ii, lim = op_limited(arr, g, Tc, vv, ch)
        v_now = float(vv[-1])
        pp = vv * ii
        pm = np.interp(g, gs, ps)
        m = ts > t_eval
        e_dc += float(np.sum(pp[m])) * dt
        e_mpp += float(np.sum(pm[m])) * dt
        lim_flag = bool(lim[-nw:].any())
        vbar, pbar = measure(vv[-nw:], ii[-nw:], ch, rng)
        t = float(ts[-1])
        if s["scan"] is None:
            mppt_tick(prm, s, vbar, pbar, lim_flag, t)
        if record:
            rec["t"].append(ts)
            rec["v"].append(vv)
            rec["p"].append(pp)
            rec["pm"].append(pm)
            rec["vref"].append(np.full(nstep, s["vref"]))
            rec["lim"].append(lim)
    out = dict(eta=e_dc / e_mpp if e_mpp > 0 else float("nan"), e_dc=e_dc, e_mpp=e_mpp)
    if record:
        out.update({k: np.concatenate(v) for k, v in rec.items()})
    return out


# ------------------------------------------------------------------------------------------------ studies
EU_W = ((50.0, 0.03), (100.0, 0.06), (200.0, 0.13), (300.0, 0.10), (500.0, 0.48), (1000.0, 0.20))   # 5..100 % weights


def const(g):
    return lambda ts: np.full_like(ts, g)


def static_point(arr, G, Tc, ch, prm=None, t_set=4.0, t_meas=30.0, seed=7):
    vm, im, pm = mpp(arr, G, Tc)
    ptab = pmpp_table(arr, Tc, np.array([G * 0.999, G, G * 1.001]), ch["imax"], ch["pmax"])
    r = run_mppt(arr, const(G), Tc, t_set + t_meas, ch, prm, v0=min(max(vm * 1.03, ch["v_lo"]), 1000.0), t_eval=t_set,
                 seed=seed, record=True, ptab=ptab)
    k = r["t"] > t_set
    pav = float(ptab[1][1])
    return dict(G=G, Tc=Tc, vmp=vm, pmp=pm, eta=r["eta"], v_mean=float(r["v"][k].mean()), v_std=float(r["v"][k].std()),
                limited=bool(r["lim"][k].any()), range_loss=1.0 - pav / pm, outside=vm < MPPT["v_lo"])


def ramp_profile(g_lo, g_hi, slope, dwell=10.0, reps=1):
    """EN 50530-style trapezoid: dwell at g_lo, ramp up at slope [W/m2/s], dwell at g_hi, ramp down, dwell"""
    tr = (g_hi - g_lo) / slope
    pts = [(0.0, g_lo)]
    t = 0.0
    for _ in range(reps):
        t += dwell
        pts.append((t, g_lo))
        t += tr
        pts.append((t, g_hi))
        t += dwell
        pts.append((t, g_hi))
        t += tr
        pts.append((t, g_lo))
    t += dwell
    pts.append((t, g_lo))
    tt, gg = np.array(pts).T
    return (lambda ts: np.interp(ts, tt, gg)), float(t)


def study_static(ch):
    main = array(17, 8, name="17s x 8p (1000 V class, 74 kW STC)")
    low = array(8, 8, name="8s x 8p (low voltage, 35 kW STC)")
    rows = []
    for arr, temps in ((main, (0.0, 25.0, 50.0, 70.0)), (low, (25.0, 70.0))):
        for Tc in temps:
            for G in (50.0, 100.0, 200.0, 300.0, 500.0, 700.0, 1000.0):
                r = static_point(arr, G, Tc, ch)
                r["array"] = arr["name"]
                rows.append(r)
    eu = sum(w * next(r["eta"] for r in rows if r["array"] == main["name"] and r["Tc"] == 25.0 and r["G"] == g) for g, w in EU_W)
    return rows, eu


def study_meas(ch):
    """effect of quantisation, noise, sampling rate, offset and ripple on the static efficiency (main array, 25 C)"""
    arr = array(17, 8)
    variants = [("ideal measurement", dict(quant=False, noise=False)),
                ("12-bit quantisation only", dict(quant=True, noise=False)),
                ("+ noise, 32 kS/s (control samples only)", dict(sps=32e3)),
                ("+ noise, 500 kS/s oversampled (design)", dict()),
                ("design + 0.3 A offset + ripple bias", dict(off_i=0.3, bias_v=ch["ripple_v"]))]
    out = []
    for lab, mod in variants:
        c = dict(ch, **mod)
        out.append((lab, [static_point(arr, g, 25.0, c)["eta"] for g in (50.0, 100.0, 300.0, 1000.0)]))
    return out


def study_dynamic(ch):
    arr = array(17, 8)
    ptab = pmpp_table(arr, 25.0, None, ch["imax"], ch["pmax"])
    res = []
    for g_lo, g_hi, slopes in ((100.0, 500.0, (0.5, 2.0, 10.0, 30.0, 50.0)), (300.0, 1000.0, (10.0, 30.0, 50.0, 100.0))):
        for sl in slopes:
            gf, T = ramp_profile(g_lo, g_hi, sl)
            vm0 = mpp(arr, g_lo, 25.0)[0]
            r = run_mppt(arr, gf, 25.0, T, ch, v0=vm0, ptab=ptab, record=(sl in (10.0, 100.0)))
            res.append(dict(test=f"{g_lo:.0f}->{g_hi:.0f} W/m2", slope=sl, T=T, eta=r["eta"], e=r["e_mpp"],
                            rec=r if sl in (10.0, 100.0) else None))
    tot = sum(x["eta"] * x["e"] for x in res) / sum(x["e"] for x in res)
    return res, tot


def study_startup(ch):
    """start from V_oc on a low-voltage array whose MPP sits near the 250 V floor: morning ramp 20 -> 400 W/m2"""
    arr = array(7, 8, name="7s x 8p")
    out = {}
    gf, T = (lambda ts: np.interp(ts, [0.0, 60.0], [20.0, 400.0])), 60.0
    ptab = pmpp_table(arr, 25.0, None, ch["imax"], ch["pmax"])
    r = run_mppt(arr, gf, 25.0, T, ch, ptab=ptab, record=True)
    out["ramp"] = dict(arr=arr["name"], eta=r["eta"], rec=r)
    for G, Tc in ((200.0, 25.0), (1000.0, 70.0)):
        x = static_point(arr, G, Tc, ch)
        out[f"{G:.0f}/{Tc:.0f}"] = x
    return out


def study_curtail(ch):
    out = []
    for arr, lab in ((array(12, 11, name="12s x 11p"), "135 A port-current limit"), (array(17, 11, name="17s x 11p"), "82.5 kW power limit")):
        gf, T = ramp_profile(700.0, 1150.0, 30.0, dwell=10.0)
        ptab = pmpp_table(arr, 25.0, None, ch["imax"], ch["pmax"])
        r = run_mppt(arr, gf, 25.0, T, ch, v0=mpp(arr, 700.0, 25.0)[0], ptab=ptab, record=True)
        gs, pav = ptab
        out.append(dict(arr=arr["name"], label=lab, eta=r["eta"], rec=r, pmpp_peak=mpp(arr, 1150.0, 25.0)[2],
                        i_max=float(np.max(r["p"] / r["v"])), p_max=float(np.max(r["p"]))))
    return out


def study_shading(ch):
    arr = array(17, 8, shade=(18, 0.3), name="17s x 8p, 6 of 17 modules per string at 30 %")
    G, Tc = 1000.0, 25.0
    peaks = local_maxima(arr, G, Tc)
    pg = mpp(arr, G, Tc)
    ptab = (np.array([G - 1, G, G + 1]), np.full(3, pg[2]))
    voc = float(array_curve(arr, G, Tc)[0][-1])
    no_scan = run_mppt(arr, const(G), Tc, 20.0, ch, prm=dict(MPPT, scan_period=0.0), v0=voc, ptab=ptab, record=True)
    with_scan = run_mppt(arr, const(G), Tc, 20.0, ch, prm=dict(MPPT, scan_period=8.0), v0=voc, ptab=ptab, record=True)
    k = with_scan["t"] > 15.0
    # cost of the periodic scan in unshaded operation (300 s schedule)
    plain = array(17, 8)
    c_no = run_mppt(plain, const(600.0), Tc, 600.0, ch, prm=dict(MPPT, scan_period=0.0), v0=mpp(plain, 600.0, Tc)[0],
                    ptab=pmpp_table(plain, Tc, np.array([599.0, 600.0, 601.0])))
    c_sc = run_mppt(plain, const(600.0), Tc, 600.0, ch, prm=dict(MPPT, scan_period=300.0), v0=mpp(plain, 600.0, Tc)[0],
                    ptab=pmpp_table(plain, Tc, np.array([599.0, 600.0, 601.0])))
    return dict(arr=arr["name"], peaks=peaks, glob=pg, no_scan=no_scan, with_scan=with_scan,
                v_after=float(with_scan["v"][k].mean()), eta_after=float(with_scan["p"][k].mean() / pg[2]),
                eta_local=float(no_scan["p"][no_scan["t"] > 10].mean() / pg[2]), scan_cost=c_no["eta"] - c_sc["eta"])


def check_vs_averaged(ch):
    """the quasi-static tracker against the averaged switching-cycle model of sim/pv_control.py (same MPPT code,
    32 kHz samples, no noise): operating-point trajectory from 0.88 V_mp"""
    import pv_control as pc
    P, Gn = ch["P"], ch["G"]
    arr = array(17, 8)
    vm, im, pm = mpp(arr, 1000.0, 25.0)
    v0 = 0.88 * vm
    da, db, _ = pc.duties_ss(P, v0, 800.0)
    i0 = float(array_i(arr, 1000.0, 25.0, np.array([v0]))[0]) / (3 * da)
    sc = pc.ss_sc(P, 3, v0, 800.0, i0, pa=pc.port_pv(arr, 1000.0, 25.0), kff_a=0.0, kff_b=0.0, noise=False)
    sc.update(vref=v0, vr=v0, t_en=0.0, cmd=lambda t, cs: ("VA", sc["vr"], 1010.0, False))
    sc["on_outer"] = pc.mppt_hook(dict(MPPT), ramp=1e9)
    lg = pc.simulate(P, Gn, sc, 0.40, switched=False)
    q = run_mppt(arr, const(1000.0), 25.0, 0.40, dict(ch, sps=32e3, noise=False, quant=True), v0=v0, record=True)
    tq = q["t"]
    va_av = np.interp(tq, lg["t"], lg["va"])
    k = tq > 0.25
    p_av = float(np.mean(np.interp(tq[k], lg["t"], lg["va"] * lg["iea"])))
    return dict(t=tq, v_q=q["v"], v_av=va_av, rms_dv=float(np.sqrt(np.mean((q["v"] - va_av) ** 2))),
                p_q=float(np.mean(q["p"][k])), p_av=p_av, pmpp=pm, vmp=vm)


# ------------------------------------------------------------------------------------------------ output
def run():
    import time
    import logging
    import matplotlib
    matplotlib.use("Agg")
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    ch = chain_defaults()
    sys.path.insert(0, HERE)
    import pv_control as pc
    rb = pc.ripple_bias(ch["P"], ch["G"])
    att = 1.0 / math.hypot(1.0, 3 * ch["P"]["fsw"] * 2 * math.pi * ch["P"]["tau_rc"])     # port-board RC at 3 f_sw
    ch["ripple_v"] = max(abs(x["bias_raw"]) for x in rb) * att
    st_rows, eu = study_static(ch)
    meas = study_meas(ch)
    dyn, dyn_tot = study_dynamic(ch)
    su = study_startup(ch)
    cu = study_curtail(ch)
    sh = study_shading(ch)
    av = check_vs_averaged(ch)
    main_rows = [r for r in st_rows if r["array"].startswith("17s")]
    low_rows = [r for r in st_rows if r["array"].startswith("8s")]
    stat_min = min(st_rows, key=lambda r: r["eta"])
    stat_min_in = min((r for r in st_rows if not r["outside"]), key=lambda r: r["eta"])
    # ---------------------------------------------------------------- figures
    arr = array(17, 8)
    fig, axs = plt.subplots(1, 3, figsize=(16, 4.4))
    for G, Tc in ((1000.0, 25.0), (1000.0, 70.0), (500.0, 25.0), (100.0, 25.0), (1000.0, 0.0)):
        V, I, Pw = array_curve(arr, G, Tc)
        axs[0].plot(V, I, label=f"{G:.0f} W/m2, {Tc:.0f} C")
        axs[1].plot(V, Pw / 1e3)
    sarr = array(17, 8, shade=(18, 0.3))
    V, I, Pw = array_curve(sarr, 1000.0, 25.0)
    axs[2].plot(V, Pw / 1e3, "k", label="partial shading (6 of 17 modules at 30 %)")
    V, I, Pw = array_curve(arr, 1000.0, 25.0)
    axs[2].plot(V, Pw / 1e3, "C0:", label="unshaded")
    axs[0].set_xlabel("V [V]")
    axs[0].set_ylabel("I [A]")
    axs[0].legend(fontsize=7)
    axs[0].set_title("array 17s x 8p (generic 545 W modules), single-diode model", fontsize=9)
    axs[1].set_xlabel("V [V]")
    axs[1].set_ylabel("P [kW]")
    axs[2].set_xlabel("V [V]")
    axs[2].set_ylabel("P [kW]")
    axs[2].legend(fontsize=7)
    for ax in axs:
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "array.png"), dpi=110)
    plt.close(fig)
    fig, axs = plt.subplots(2, 3, figsize=(17, 8.5))
    ax = axs[0, 0]
    for Tc in (0.0, 25.0, 50.0, 70.0):
        rr = [r for r in main_rows if r["Tc"] == Tc]
        ax.plot([r["G"] for r in rr], [r["eta"] * 100 for r in rr], "o-", label=f"17s x 8p, {Tc:.0f} C")
    for Tc in (25.0, 70.0):
        rr = [r for r in low_rows if r["Tc"] == Tc]
        ax.plot([r["G"] for r in rr], [r["eta"] * 100 for r in rr], "s--", label=f"8s x 8p, {Tc:.0f} C")
    ax.axhline(99.9, color="r", lw=0.8, label="PV-C1 99.9 %")
    ax.set_xlabel("irradiance [W/m2]")
    ax.set_ylabel("static MPPT efficiency [%]")
    ax.set_ylim(99.0, 100.0)
    ax.legend(fontsize=6)
    ax.grid(alpha=0.3)
    ax.set_title("static (30 s after 4 s settling per point)", fontsize=9)
    ax = axs[0, 1]
    for lab, etas in meas:
        ax.plot((50, 100, 300, 1000), [e * 100 for e in etas], "o-", label=lab)
    ax.axhline(99.9, color="r", lw=0.8)
    ax.set_xscale("log")
    ax.set_ylim(98.5, 100.0)
    ax.set_xlabel("irradiance [W/m2]")
    ax.set_ylabel("static MPPT efficiency [%]")
    ax.legend(fontsize=6)
    ax.grid(alpha=0.3)
    ax.set_title("measurement-chain effects (17s x 8p, 25 C)", fontsize=9)
    ax = axs[0, 2]
    for d in dyn:
        if d["rec"] is not None:
            r = d["rec"]
            ax.plot(r["t"], r["p"] / 1e3, lw=0.6, label=f"P_DC, {d['test']} at {d['slope']:.0f} W/m2/s")
            ax.plot(r["t"], np.interp(r["t"], *pmpp_table(arr, 25.0, None, ch["imax"], ch["pmax"])) * 0 + r["pm"] / 1e3, "k:", lw=0.6)
    ax.set_xlabel("s")
    ax.set_ylabel("kW")
    ax.legend(fontsize=6)
    ax.set_title(f"EN 50530-style ramps: overall dynamic efficiency {dyn_tot*100:.3f} %", fontsize=9)
    ax = axs[1, 0]
    r = su["ramp"]["rec"]
    ax.plot(r["t"], r["v"], "m", lw=0.7, label="V_A")
    ax.plot(r["t"], r["vref"], "k:", lw=0.6, label="V_A reference")
    ax.axhline(MPPT["v_lo"], color="r", lw=0.6, label="255 V floor")
    ax.set_xlabel("s")
    ax.set_ylabel("V")
    ax2 = ax.twinx()
    ax2.plot(r["t"], r["p"] / 1e3, "g", lw=0.6, label="P_DC")
    ax2.plot(r["t"], r["pm"] / 1e3, "k--", lw=0.6, label="P_mpp")
    ax2.set_ylabel("kW")
    ax.legend(fontsize=6, loc="upper left")
    ax2.legend(fontsize=6, loc="lower right")
    ax.set_title(f"start from V_oc, 7s x 8p, 20->400 W/m2 in 60 s: {su['ramp']['eta']*100:.2f} %", fontsize=9)
    ax = axs[1, 1]
    for c in cu:
        r = c["rec"]
        ax.plot(r["t"], r["p"] / 1e3, lw=0.7, label=f"{c['arr']}: P_DC ({c['label']})")
        ax.plot(r["t"], r["pm"] / 1e3, "k:", lw=0.6)
    ax.set_xlabel("s")
    ax.set_ylabel("kW")
    ax.legend(fontsize=6)
    ax.set_title("curtailment: 700->1150->700 W/m2 (dotted: available power under the limit)", fontsize=9)
    ax = axs[1, 2]
    for key, lab in (("no_scan", "P&O only"), ("with_scan", "P&O + global scan (every 8 s here)")):
        r = sh[key]
        ax.plot(r["t"], r["p"] / 1e3, lw=0.7, label=lab)
    ax.axhline(sh["glob"][2] / 1e3, color="k", ls=":", lw=0.7, label="global maximum")
    ax.set_xlabel("s")
    ax.set_ylabel("kW")
    ax.legend(fontsize=6)
    ax.set_title(f"partial shading, two maxima ({', '.join(f'{v:.0f} V/{p/1e3:.1f} kW' for v, p in sh['peaks'])})", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "mppt.png"), dpi=110)
    plt.close(fig)
    # ---------------------------------------------------------------- tables, report, spec
    with open(os.path.join(OUT, "static.csv"), "w") as f:
        f.write("array,Tc_C,G_Wm2,V_mpp,P_mpp_W,eta,V_mean,V_std,limited\n")
        for r in st_rows:
            f.write(f"{r['array']},{r['Tc']},{r['G']},{r['vmp']:.2f},{r['pmp']:.1f},{r['eta']:.6f},{r['v_mean']:.2f},{r['v_std']:.2f},{r['limited']}\n")
    with open(os.path.join(OUT, "dynamic.csv"), "w") as f:
        f.write("test,slope_Wm2s,duration_s,eta\n")
        for d in dyn:
            f.write(f"{d['test']},{d['slope']},{d['T']:.1f},{d['eta']:.6f}\n")
    fails = [r for r in st_rows if r["eta"] < 0.999]
    outside = [r for r in st_rows if r["outside"]]
    res = dict(algorithm="dP-P&O (Sera 2008) with elasticity-adaptive step + scheduled global scan",
               parameters={k: v for k, v in MPPT.items()},
               measurement={"oversampling_kSPS_V_A_I_A": MPPT["oversample_sps"] / 1e3, "window_ms": MPPT["w"] * 1e3,
                            "basis": "with the 32 kHz control samples only the static efficiency falls to "
                                     f"{meas[2][1][0]*100:.2f} % at 50 W/m2"},
               static_eta_min_in_window=stat_min_in["eta"],
               static_eta_min_in_window_at=[stat_min_in["array"], stat_min_in["G"], stat_min_in["Tc"]],
               static_eta_min_overall=stat_min["eta"], static_eta_min_overall_at=[stat_min["array"], stat_min["G"], stat_min["Tc"]],
               static_eta_eu_weighted=eu, dynamic_eta_overall=dyn_tot,
               dynamic={f"{d['test']} @ {d['slope']:g} W/m2/s": d["eta"] for d in dyn},
               static_points_below_99_9=[(r["array"], r["G"], r["Tc"], round(r["eta"] * 100, 3)) for r in fails],
               pv_c1_met_static_in_window=not [r for r in fails if not r["outside"]], pv_c1_met_dynamic=dyn_tot >= 0.999,
               shading={"local_only_eta": sh["eta_local"], "with_scan_eta": sh["eta_after"], "scan_cost_pct": 100 * sh["scan_cost"]},
               curtailment={c["label"]: c["eta"] for c in cu}, va_loop_tau_ms=ch["tau"] * 1e3)
    json.dump(res, open(os.path.join(OUT, "mppt_result.json"), "w"), indent=1, default=float)
    sp = os.path.join(HERE, "out", "pv_control", "control_spec.json")
    if os.path.exists(sp):
        spec = json.load(open(sp))
        spec["mppt"] = res
        json.dump(spec, open(sp, "w"), indent=1, default=float)
    write_report(ch, st_rows, eu, meas, dyn, dyn_tot, su, cu, sh, av, stat_min, fails, rb, outside, stat_min_in)
    # ---------------------------------------------------------------- self-check
    assert abs((float(v_from_i(np.array([0.0]), *params(G_REF, 30.0))[0]) - float(v_from_i(np.array([0.0]), *params(G_REF, 20.0))[0]))
               / 10.0 / MOD["voc"] - MOD["b_voc"]) < 2e-5, "V_oc temperature coefficient"
    vm, im, pm = mpp(array(1, 1), G_REF, 25.0)
    assert abs(pm / MOD["pmp"] - 1) < 1e-3 and abs(vm / MOD["vmp"] - 1) < 2e-3, "module MPP at STC"
    top = [r for r in main_rows if r["G"] == 1000.0 and r["Tc"] == 25.0][0]
    assert top["eta"] > 0.999 and abs(top["v_mean"] / top["vmp"] - 1) < 0.01, "MPPT finds the known maximum"
    assert abs(sh["v_after"] / sh["glob"][0] - 1) < 0.03 and sh["eta_after"] > 0.99, "global scan finds the global maximum"
    assert sh["eta_local"] < 0.7, "P&O alone stays on the local maximum (the shading case is a real test)"
    assert av["rms_dv"] < 5.0 and abs(av["p_q"] / av["p_av"] - 1) < 0.005, "quasi-static tracker vs averaged model"
    for c in cu:
        assert c["i_max"] <= ch["imax"] * 1.001 and c["p_max"] <= ch["pmax"] * 1.001 and c["eta"] > 0.99, "curtailment"
    print(f"static MPPT efficiency: min {stat_min_in['eta']*100:.3f} % with the MPP in the window ({stat_min_in['G']:.0f} W/m2, "
          f"{stat_min_in['Tc']:.0f} C), {stat_min['eta']*100:.3f} % overall ({stat_min['array'].split(' (')[0]}, {stat_min['G']:.0f} W/m2, "
          f"{stat_min['Tc']:.0f} C, MPP below the floor), "
          f"EU-weighted {eu*100:.3f} %; dynamic {dyn_tot*100:.3f} %; points below 99.9 %: {len(fails)}")
    print(f"pv_mppt self-check passed ({time.time()-t0:.0f} s)")
    return res


def write_report(ch, st_rows, eu, meas, dyn, dyn_tot, su, cu, sh, av, stat_min, fails, rb, outside, stat_min_in):
    L = []
    a = L.append
    a("# PV-P75 / PV-P100-110 MPPT study (PV-02, PV-C1, PV-C2)\n")
    a("Generated by `sim/pv_mppt.py`; every number is written by the script. **Simulated, not measured.** Results are also "
      "written to `sim/out/pv_control/control_spec.json` (section `mppt`) and `mppt_result.json`.\n")
    a("## 1. PV array model\n")
    a(f"- Module: GENERIC 545 W class (144 half-cut mono PERC) datasheet values - P_mp {MOD['pmp']:.0f} W, V_mp {MOD['vmp']} V, "
      f"I_mp {MOD['imp']} A, V_oc {MOD['voc']} V, I_sc {MOD['isc']} A, dI_sc/dT +{MOD['a_isc']*100:.3f} %/K, dV_oc/dT "
      f"{MOD['b_voc']*100:.2f} %/K. Not a specific product (assumption).")
    a(f"- Single-diode model fitted at STC (ideality {MOD['n']} assumed): I_L {FIT['IL']:.3f} A, I_0 {FIT['I0']:.3e} A, R_s "
      f"{FIT['Rs']*1e3:.1f} mOhm, R_sh {FIT['Rsh']:.1f} ohm; De Soto translation in G and T with the band-gap term fitted to the "
      f"datasheet dV_oc/dT (E_g,eff {FIT['Eg']:.3f} eV). Model dP_mp/dT "
      f"{(mpp(array(1,1),1000.0,35.0)[2]/mpp(array(1,1),1000.0,25.0)[2]-1)*10:.2f} %/K (datasheet {MOD['g_pmp']*100:.2f} %/K).")
    a(f"- Strings of 17 modules (V_oc {array_curve(array(17, 8), 1000.0, -30.0)[0][-1]:.0f} V at -30 C, inside the 1000 V port "
      f"range), 8 strings = {mpp(array(17, 8), 1000.0, 25.0)[2]/1e3:.1f} kW at STC (EN 50530 100 % point ~ rated 75 kW). "
      "Three bypass diodes per module for the shading case. Low-voltage arrays 8s x 8p and 7s x 8p for the lower range "
      "and the 250 V start.\n")
    a("![array](array.png)\n")
    a("## 2. Algorithm and why\n")
    a("dP-P&O (perturb and observe with a mid-period power sample, Sera et al. 2008) at "
      f"{1/MPPT['T_p']:.0f} Hz on the V_A reference of the cascaded V_A loop (sim/pv_control.py), with:")
    a(f"- step adapted to the measured normalised slope |dlnP/dlnV| x {MPPT['a_step']}, clamped to "
      f"{MPPT['dv_min_rel']*100:.1f}-{MPPT['dv_max_rel']*100:.0f} % of V_A;")
    a(f"- measurements = averages over the last {MPPT['w']*1e3:.0f} ms of each half period (the V_A loop settles in "
      f"~{ch['tau']*1e3*3:.1f} ms, time constant {ch['tau']*1e3:.2f} ms from the exact sampled model), V_A and I_A "
      f"oversampled at {MPPT['oversample_sps']/1e3:.0f} kS/s;")
    a("- the irradiance-drift cancellation of dP-P&O (perturbation effect = (P_x - P_k) - (P_k+1 - P_x)) removes the "
      "wrong-direction steps of plain P&O on ramps (EN 50530 dynamic test), at the cost of half the update rate;")
    a(f"- freeze-and-track while a module limit (135 A / 82.5 kW / V_B max) holds the operating point (no reference "
      f"wind-up), floor {MPPT['v_lo']:.0f} V;")
    a(f"- global scan (reference swept down to {MPPT['scan_lo_rel']*100:.0f} % of V at {MPPT['scan_rate']:.0f} V/s, then P&O "
      f"from the best point) every {MPPT['scan_period']:.0f} s for partial shading.")
    a("Incremental conductance was not chosen: with the 0.26 A current LSB its dI/dV estimate is noise-dominated at low "
      "irradiance and on ramps it has the same drift problem; model-based / fractional-V_oc methods need array "
      "knowledge. P&O variants are what the TI references (TIDM-SOLAR-DCDC, TIDM-BUCKBOOST-BIDIR) use too.\n")
    a("## 3. Static MPPT efficiency (energy over 30 s after 4 s settling; reference = available power under the module limits)\n")
    a("| array | T_cell [C] | " + " | ".join(f"{g:.0f} W/m2" for g in (50, 100, 200, 300, 500, 700, 1000)) + " |")
    a("|---|---|" + "---|" * 7)
    for arr in sorted({r["array"] for r in st_rows}, reverse=True):
        for Tc in sorted({r["Tc"] for r in st_rows if r["array"] == arr}):
            rr = sorted([r for r in st_rows if r["array"] == arr and r["Tc"] == Tc], key=lambda r: r["G"])
            a(f"| {arr} | {Tc:.0f} | " + " | ".join(f"{r['eta']*100:.3f}" for r in rr) + " |")
    for r in outside:
        a(f"\n- {r['array'].split(' (')[0]} at {r['G']:.0f} W/m2, {r['Tc']:.0f} C: V_mp {r['vmp']:.0f} V is below the "
          f"{MPPT['v_lo']:.0f} V tracking floor (port range 250-1000 V): the tracker reaches {r['eta']*100:.3f} % of what is "
          f"available above the floor; the floor itself costs {r['range_loss']*100:.2f} % of P_mpp (range loss, not tracking).")
    a(f"\nMinimum with the MPP in the window {stat_min_in['eta']*100:.3f} % ({stat_min_in['array']}, {stat_min_in['G']:.0f} W/m2, "
      f"{stat_min_in['Tc']:.0f} C); overall {stat_min['eta']*100:.3f} % ({stat_min['array']}, {stat_min['G']:.0f} W/m2, {stat_min['Tc']:.0f} C); "
      f"EU-weighted (5/10/20/30/50/100 % weights 0.03/0.06/0.13/0.10/0.48/0.20, 25 C) **{eu*100:.3f} %**.")
    fin = [r for r in fails if not r["outside"]]
    fout = [r for r in fails if r["outside"]]
    if fin:
        a(f"\n**PV-C1 (>= 99.9 %) is NOT met at {len(fin)} static points with the MPP inside the window:** " +
          "; ".join(f"{r['array'].split(' (')[0]} {r['G']:.0f} W/m2 {r['Tc']:.0f} C = {r['eta']*100:.3f} %" for r in fin) +
          ". Why: at low irradiance the P&O decision is limited by the power-measurement noise (port-current LSB 0.26 A "
          "against a few amperes of array current) and the minimum step costs a fixed fraction; see section 4.")
    else:
        a("\n**PV-C1 (>= 99.9 % static) is met at every grid point whose MPP lies inside the 255-1000 V tracking window** - "
          "with the oversampled measurement of section 4; it is not met without it.")
    if fout:
        a("**Not met where the MPP lies below the 255 V floor:** " +
          "; ".join(f"{r['array'].split(' (')[0]} {r['G']:.0f} W/m2 {r['Tc']:.0f} C = {r['eta']*100:.3f} % of the power "
                    f"available above the floor (plus {r['range_loss']*100:.2f} % range loss)" for r in fout) +
          ". The tracker sits on the floor and must probe upward now and then to notice when the MPP moves back into the "
          "window; each probe on that steep flank costs power. A module/array combination whose MPP falls below 250 V "
          "is outside PV-03 anyway.")
    a("\n## 4. Measurement chain: noise, quantisation, sampling rate, offset, interleaving ripple\n")
    a(f"Port-A chain (sim/out/port_design): V_A {ch['lsb_v']:.3f} V/LSB, {ch['sig_v']:.2f} V rms noise per sample (AMC3330 + ADC); "
      f"I_A {ch['lsb_i']:.3f} A/LSB, {ch['sig_i']:.2f} A rms per sample (AMC3302 over the 100 uOhm shunt + ADC); interleaving ripple "
      f"at the synchronous sampling instant after the 3.2 kHz divider RC: {ch['ripple_v']*1e3:.2f} mV (capacitor node: "
      f"{max(abs(x['bias_raw']) for x in rb)*1e3:.0f} mV).\n")
    a("| measurement | 50 W/m2 | 100 W/m2 | 300 W/m2 | 1000 W/m2 |")
    a("|---|---|---|---|---|")
    for lab, etas in meas:
        a(f"| {lab} | " + " | ".join(f"{e*100:.3f} %" for e in etas) + " |")
    a("\n- Quantisation alone (no noise to dither it) leaves a staircase in the measured power that P&O can sit on; the real "
      "noise (~0.8 LSB rms) dithers it, and averaging then recovers sub-LSB resolution.")
    a("- With only the control-loop samples (32 kS/s, 640 per window) the power noise at 50-100 W/m2 exceeds the change a "
      "minimum step produces near the MPP and the operating point random-walks: below 99.9 %. **Requirement: V_A and I_A "
      f"oversampled at >= {MPPT['oversample_sps']/1e3:.0f} kS/s (>= 10 000 independent samples per {MPPT['w']*1e3:.0f} ms window), "
      "accumulated by DMA/CLA, or an equivalent sigma-delta channel.** ADC-A/ADC-D have the capacity (3.5 MS/s each).")
    a("- A calibrated current offset of 0.3 A shifts the perceived MPP by dP/dV = -I_off; its cost is "
      f"{(meas[3][1][0]-meas[4][1][0])*100:.3f} %-points at 50 W/m2 and negligible above. Gain errors do not move the MPP.")
    a("- The interleaving ripple at the synchronous sampling instant is negligible behind the port-board RC.\n")
    a("## 5. Dynamic MPPT efficiency (EN 50530-style ramps, 25 C)\n")
    a("Shortened sequences (one cycle per slope, 10 s dwells; the standard repeats each slope several times): energy-based "
      "efficiency over the whole sequence.\n")
    a("| sequence | slope [W/m2/s] | duration [s] | efficiency |")
    a("|---|---|---|---|")
    for d in dyn:
        a(f"| {d['test']} | {d['slope']:g} | {d['T']:.0f} | {d['eta']*100:.3f} % |")
    a(f"\nOverall (energy-weighted): **{dyn_tot*100:.3f} %** -> PV-C1 {'met' if dyn_tot >= 0.999 else 'NOT met'} dynamically "
      f"(lowest: {min(dyn, key=lambda d: d['eta'])['test']} at {min(dyn, key=lambda d: d['eta'])['slope']:g} W/m2/s, "
      f"{min(d['eta'] for d in dyn)*100:.3f} %).\n")
    a("## 6. Start-up from the 250 V region (PV-C2)\n")
    r = su["ramp"]
    a(f"- 7s x 8p array (V_oc ~{7*MOD['voc']:.0f} V at STC), morning ramp 20 -> 400 W/m2 in 60 s, tracking starts at V_oc: "
      f"energy efficiency {r['eta']*100:.2f} % including the descent from V_oc.")
    for key in ("200/25", "1000/70"):
        x = su[key]
        a(f"- static {key.replace('/', ' W/m2, ')} C: V_mp {x['vmp']:.0f} V, operating {x['v_mean']:.0f} V, tracking "
          f"efficiency {x['eta']*100:.3f} %" + (f"; the MPP is below the {MPPT['v_lo']:.0f} V floor (port range 250-1000 V), "
                                                 f"which costs a further {x['range_loss']*100:.2f} % of P_mpp - set by the port "
                                                 "range, not the tracker" if x["outside"] else ""))
    a("\n## 7. Behaviour at the 135 A and 82.5 kW limits (curtailment)\n")
    for c in cu:
        a(f"- {c['arr']} ({c['label']}), irradiance 700 -> 1150 -> 700 W/m2 at 30 W/m2/s (P_mpp up to {c['pmpp_peak']/1e3:.1f} kW): "
          f"highest port current {c['i_max']:.1f} A, highest power {c['p_max']/1e3:.2f} kW; efficiency against the available "
          f"power under the limit {c['eta']*100:.3f} %. While limited, the MPPT holds its reference at the measured V_A, so "
          "it resumes without wind-up when the limit releases.")
    a("- The limited operating point is always right of the MPP (higher voltage, lower current): stable for the V_A loop "
      "(the array behaves as a voltage source there).\n")
    a("## 8. Partial shading (two local maxima)\n")
    a(f"- {sh['arr']} at 1000 W/m2: maxima at {', '.join(f'{v:.0f} V / {p/1e3:.1f} kW' for v, p in sh['peaks'])}; global "
      f"{sh['glob'][0]:.0f} V / {sh['glob'][2]/1e3:.1f} kW.")
    a(f"- Starting from V_oc, P&O alone settles on the local maximum: {sh['eta_local']*100:.1f} % of the global maximum.")
    a(f"- With the global scan (every 8 s in this demonstration) the tracker moves to {sh['v_after']:.0f} V: "
      f"{sh['eta_after']*100:.2f} % of the global maximum.")
    a(f"- Cost of the scheduled scan (every {MPPT['scan_period']:.0f} s) in unshaded operation at 600 W/m2: "
      f"{sh['scan_cost']*100:.3f} %-points of static efficiency.\n")
    a("![mppt](mppt.png)\n")
    a("## 9. Model check against the switching-cycle-averaged model\n")
    a(f"The same MPPT code driving the averaged model of sim/pv_control.py (32 kHz samples, no noise) from 0.88 V_mp at "
      f"1000 W/m2: RMS difference of the V_A trajectory against the quasi-static tracker {av['rms_dv']:.2f} V over 0.4 s; "
      f"mean power over the last 0.15 s {av['p_av']/1e3:.2f} kW (averaged) vs {av['p_q']/1e3:.2f} kW (quasi-static), P_mpp "
      f"{av['pmpp']/1e3:.2f} kW.\n")
    a("## 10. Verdict on PV-C1 (>= 99.9 % MPPT accuracy) and honesty\n")
    a(f"- Static: {'met at every grid point with the MPP in the window' if not [r for r in fails if not r['outside']] else 'NOT met at in-window points (section 3)'}"
      f"{'' if not [r for r in fails if r['outside']] else ', not met where the MPP is below the 255 V floor'} "
      f"(minimum in the window {stat_min_in['eta']*100:.3f} %, EU-weighted {eu*100:.3f} %) - only with the oversampled V_A/I_A "
      "measurement. "
      f"Dynamic (EN 50530-style ramps): {dyn_tot*100:.3f} % overall.")
    a("- What is not modelled: array capacitance and cable inductance, module mismatch inside the array, irradiance noise "
      "faster than the ramps, ADC 1/f noise and drift inside the window, temperature transients; the module data are "
      "generic. The V_A loop is represented by its closed-loop time constant (checked against the averaged model).")
    a("- Megarevo's 99.9 % is a published figure without a stated test method; ours is EN 50530-style simulation. Nothing "
      "here is measured.")
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    run()
