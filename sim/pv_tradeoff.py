"""PV-18 Gate-0 topology study for PVCELL-25/27.5 (one cell of PV-P75/100/110).

Run:  .venv/bin/python sim/pv_tradeoff.py      -> sim/out/pv_tradeoff/{report.md, *.csv, *.png}

Candidates (all non-isolated, bidirectional, either port may be higher):
  A2k  2-level four-switch buck-boost (FSBB), 2000 V SiC        (PV-18 a, 1700/2000 V devices in a plain buck-boost)
  A17  2-level FSBB, 1700 V SiC
  B    3-level flying-capacitor FSBB (8 switch positions), 1200 V SiC   (PV-18 b)
  C    series-stacked symmetric split-bus: two half-voltage FSBBs in series, midpoint on both ports, 1200 V SiC (PV-18 c)
  Cm   as C with Wolfspeed FM3 half-bridge modules
Everything is calculated (analytic piecewise-linear waveforms + datasheet loss data), nothing is measured.
This file also holds the shared cell model (waveform, losses, inductor, thermal) that pv_design.py imports.
"""
import csv
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pv_devices as dv  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "pv_tradeoff")
MU0 = 4e-7 * math.pi

# ------------------------------------------------------------------------------------------------ requirement inputs
P_RATED, P_MAX, I_MAX = 25e3, 27.5e3, 45.0      # per cell: 75 kW / 82.5 kW / 135 A over 3 cells (PV-01, PV-05, PV-08, PV-15)
V_MIN, V_MAX = 250.0, 1000.0                    # both ports (PV-03, PV-06)
V_FL = (550.0, 950.0)                           # full-power window (PV-04, PV-07)
V_OVP = 1100.0                                  # port overvoltage trip used for device stress (assumption: 10 % above V_MAX)
T_AIR = 45.0                                    # inlet air at full power (PV-20)
T_AIR_HOT = 60.0                                # derate-to temperature (PV-20)


def p_limit(va, vb, p_cap=P_MAX):
    """cell power limit: 27.5 kW, and the 45 A current limit on the lower-voltage port (PV-22)"""
    return min(p_cap, I_MAX * min(va, vb))


# ------------------------------------------------------------------------------------------------ assumptions (stated)
T_DEAD_FW = 200e-9       # s, firmware dead time (centre-aligned PWM)
T_DEAD = 511e-9          # s, dead time at the gates, MAXIMUM: the UCC21710 interlock stretch (3.4k/100p, gen/gdrv.py rev 4) lengthens
                         # the firmware 200 ns by up to 251 ns, + driver skew -> body-diode conduction per edge (loss)
T_DEAD_MIN = 191e-9      # s, dead time at the gates, minimum (gen/gdrv.py): time a soft edge has to finish its ZVS transition
T_ON_MIN = 0.6e-6        # s, minimum on-time of the complementary switch inside a minimum pulse (the former 1 us - 2 x 200 ns)
T_MIN_PULSE = T_ON_MIN + 2 * T_DEAD   # s, minimum on/off time of a switch pair -> D_max = 1 - T_MIN_PULSE*fsw
R_MISC = 0.6e-3          # ohm, busbar/PCB/terminals/current-sense in series with the inductor current (estimate)
FR_AC = 2.0              # winding AC/DC resistance factor applied to the ripple component (Litz, 2-4 layers; estimate)
J_CU = 4.5e6             # A/m2 copper current density limit (forced air, Litz)
K_CU = 0.30              # copper fill of the E-core window (Litz packing x bobbin x 1 kV insulation; estimate)
IND_LAMBDA = 8.0         # W per kg: inductor choice minimises P_loss + IND_LAMBDA*mass (10 years x 2000 full-load h; estimate)
MU_FRAC_MIN = 0.35       # inductance left at the overcurrent trip must be >= 35 % of L0 (soft-saturation rule)
B_FRAC_MAX = 0.85        # B_peak at the overcurrent trip <= 85 % of B_sat
OCP_MARGIN = 1.15        # hardware inductor-current trip = 1.15 x the largest normal peak (45 A + half the max ripple)


def i_ocp(ripple):
    """hardware peak-current trip for a design with peak-peak ripple = ripple x 45 A at the worst point"""
    return OCP_MARGIN * I_MAX * (1 + ripple / 2)
IND_MASS_MAX = 6.0       # kg per cell (3 cells must fit in a 30 kg PMD-75-G3 class envelope; assumption)
IND_DT_MAX = 70.0        # K hot-spot rise of the wound inductor above local air (class F Litz, core < 200 C)
TJ_MAX = 150.0           # C design limit (devices rated 175 C)
P_AUX_DRV = 1.0          # W per isolated gate-driver channel (bias DC/DC + driver quiescent; estimate)
P_AUX_SENSE = 2.0        # W per cell for current/voltage sensing incl. 1 kV dividers (estimate); +0.5 W per FC/midpoint sense
# commutation-loop inductance per topology (estimates, to be replaced by extraction from the layout)
L_LOOP = {"2L": 20e-9, "3L": 25e-9, "SPLIT": 20e-9, "module": 11.4e-9 + 5e-9}
# insulated mounting of TO-247 class discretes (Al2O3 + grease, clip): case-to-sink estimate
# case-to-heatsink per TO-247 (IC-07, D-032: earthed heatsink, basic insulation): AlN ceramic 1.0 mm (k 170 W/mK: 0.02 K/W over
# the 15.5 x 20 mm tab), two silicone-grease bond lines 50 um, k 2.5 W/mK (0.13 K/W), +0.05 K/W flatness/contact allowance.
# k values are handbook values, NOT from a filed pad datasheet; the pad, its PD test and the clip force are specified in
# pv_design cell_spec 'heatsink_insulator'.  (Was 0.35 K/W, an unspecified estimate.)
PAD = {"material": "AlN ceramic (aluminium nitride), 1.0 mm", "k_W_mK": 170.0, "t_m": 1.0e-3, "grease_k": 2.5, "grease_t": 50e-6,
       "area_m2": 15.5e-3 * 20e-3, "contact": 0.05}
_rcs = PAD["t_m"] / (PAD["k_W_mK"] * PAD["area_m2"]) + 2 * PAD["grease_t"] / (PAD["grease_k"] * PAD["area_m2"]) + PAD["contact"]
RTH_CS = {"TO-247-4": round(_rcs, 3), "TO-247-4-PLUS (PG-TO247-4-PLUS-NT14)": 0.25}


def rth_cs(d):
    """case-to-heatsink of a device: its package entry, any TO-247-4 / TO-247-4L outline -> the TO-247-4 pad value"""
    pk = d["package"]
    return RTH_CS.get(pk, RTH_CS["TO-247-4"] if pk.startswith("TO-247-4") and "PLUS" not in pk else 0.0)
RTH_SPREAD = 0.08        # K/W local base spreading per device footprint (10 mm Al base; estimate)


# ------------------------------------------------------------------------------------------------ heatsink
HS_GEOM = {"n_fin": 30, "h_fin": 0.060, "t_fin": 1.5e-3, "length": 0.30, "width": 0.150}   # per-cell heatsink (estimate)
MU_AIR = 1.925e-5   # Pa s, air at ~45 C (nu = mu/rho = 1.75e-5 m2/s at 1.10 kg/m3)


def heatsink_dp(flow_m3h, rho=1.10, n_fin=30, h_fin=0.060, t_fin=1.5e-3, length=0.30):
    """pressure drop of the plate-fin heatsink: Shah & London apparent Fanning friction for hydrodynamically developing
    laminar flow between parallel plates, f_app Re = 3.44/sqrt(L+) + (24 + K/(4 L+) - 3.44/sqrt(L+)) / (1 + C L+^-2),
    K(inf) = 0.674, C = 2.9e-5, L+ = L/(D_h Re); plus Kays & London entrance/exit losses K_c = 0.42 (1 - s^2),
    K_e = (1 - s)^2 with s = channel open-area ratio.  Returns Pa."""
    pitch = 0.150 / n_fin
    s_gap = pitch - t_fin
    vel = flow_m3h / 3600.0 / (n_fin * s_gap * h_fin)
    if vel <= 0:
        return 0.0
    dh = 2 * s_gap
    re = rho * vel * dh / MU_AIR
    lp = length / (dh * re)
    fre = 3.44 / math.sqrt(lp) + (24.0 + 0.674 / (4 * lp) - 3.44 / math.sqrt(lp)) / (1 + 2.9e-5 / lp ** 2)
    sig = s_gap / pitch
    q = 0.5 * rho * vel ** 2
    return (4 * fre / re * length / dh + 0.42 * (1 - sig ** 2) + (1 - sig) ** 2) * q


def heatsink_rth(n_fin=30, h_fin=0.060, t_fin=1.5e-3, length=0.30, flow_m3h=150.0, rho=1.10):
    """plate-fin extrusion in a ducted air stream (one heatsink per cell).
    Shah & London developing-laminar parallel-plate Nusselt number, straight-fin efficiency, effectiveness-NTU air heat-up.
    Returns (R_conv K/W, R_air K/W (mean air rise per W), air velocity m/s).  Geometry and flow are ASSUMPTIONS:
    150 m3/h per cell ~ 450-600 m3/h installed for PV-P75 (roadmap 'First-order airflow calculation' P60/P80 rows)."""
    k_air, pr, cp, k_al = 0.0275, 0.71, 1005.0, 200.0
    nu = MU_AIR / rho
    pitch = 0.150 / n_fin
    s = pitch - t_fin
    q = flow_m3h / 3600.0
    vel = q / (n_fin * s * h_fin)
    dh = 2 * s
    gz = dh / length * vel * dh / nu * pr
    nu_m = 7.54 + 0.03 * gz / (1 + 0.016 * gz ** (2 / 3))
    h = nu_m * k_air / dh
    m = math.sqrt(2 * h / (k_al * t_fin))
    eta = math.tanh(m * h_fin) / (m * h_fin)
    area = n_fin * 2 * h_fin * length + 0.150 * length
    # effectiveness-NTU: sink-to-inlet resistance 1/(m cp (1 - exp(-NTU))), NTU = h eta A / (m cp); returned split as
    # r_air (mean air rise 0.5/(m cp), for reporting) + r_conv (the rest) so that r_conv + r_air is the full resistance
    mcp = rho * q * cp
    ntu = h * eta * area / mcp
    r_eff = 1.0 / (mcp * (1.0 - math.exp(-ntu)))
    r_air = 0.5 / mcp
    return r_eff - r_air, r_air, vel


# ------------------------------------------------------------------------------------------------ waveform engine
def duties(va, vb, fsw):
    """top-switch duty of leg A and leg B (D_A*V_A = D_B*V_B).  Buck/boost with one static leg, both legs switching
    in a band around V_A = V_B where one duty would exceed D_max."""
    dmax = 1.0 - T_MIN_PULSE * fsw
    r = vb / va
    if r <= dmax:
        return r, 1.0, "buck"
    if r >= 1.0 / dmax:
        return 1.0, va / vb, "boost"
    return dmax * min(1.0, r), dmax * min(1.0, 1.0 / r), "band"


def waveform(va, vb, p, fsw, L, nlev):
    """One switching period of a FSBB with nlev-level legs (2 or 3, flying capacitor at V/2).
    p = power delivered to port B (>0) or to port A (<0).  i_L positive from leg A to leg B.
    Returns dict with breakpoint times t, currents i (piecewise linear), per-interval pair states, events."""
    T = 1.0 / fsw
    da, db, mode = duties(va, vb, fsw)
    pairs = []          # (leg, k, duty, centre)
    for leg, d in (("A", da), ("B", db)):
        if d >= 1.0:
            continue
        cs = [0.0] if nlev == 2 else [0.0, 0.5 * T]
        for k, c in enumerate(cs):
            pairs.append((leg, k, d, c))
    bps = {0.0, T}
    for leg, k, d, c in pairs:
        bps.add((c - d * T / 2) % T)
        bps.add((c + d * T / 2) % T)
    t = np.array(sorted(bps))
    tm = 0.5 * (t[:-1] + t[1:])
    dt = np.diff(t)

    def state(leg, k):
        for lg, kk, d, c in pairs:
            if lg == leg and kk == k:
                x = (tm - c + T / 2) % T - T / 2
                return (np.abs(x) < d * T / 2).astype(float)
        return np.ones_like(tm)          # static leg: top switches on

    s = {(leg, k): state(leg, k) for leg in "AB" for k in range(nlev - 1)}
    vnode = {}
    for leg, v in (("A", va), ("B", vb)):
        vnode[leg] = v * s[(leg, 0)] if nlev == 2 else 0.5 * v * (s[(leg, 0)] + s[(leg, 1)])
    vl = vnode["A"] - vnode["B"]
    i = np.concatenate([[0.0], np.cumsum(vl * dt / L)])
    vs_err = i[-1] - i[0]
    # current offset from the power: <s_outer,B * i> = p/vb  (or <s_outer,A * i> = p/va for p<0)
    so = s[("B", 0)] if p >= 0 else s[("A", 0)]
    vp = vb if p >= 0 else va
    i1, i2 = i[:-1], i[1:]
    ints_i = dt * (i1 + i2) / 2
    i0 = (p / vp - np.sum(so * ints_i) / T) / (np.sum(so * dt) / T)
    i = i + i0
    return {"t": t, "i": i, "dt": dt, "s": s, "pairs": pairs, "T": T, "mode": mode, "da": da, "db": db,
            "va": va, "vb": vb, "vs_err": vs_err, "nlev": nlev, "L": L, "p": p}


def _int_i2(i1, i2, dt):
    return dt * (i1 * i1 + i1 * i2 + i2 * i2) / 3.0


def wf_stats(w):
    """RMS/average quantities of a waveform (exact for piecewise-linear current)"""
    t, i, dt, T = w["t"], w["i"], w["dt"], w["T"]
    i1, i2 = i[:-1], i[1:]
    ii = _int_i2(i1, i2, dt)
    ia = dt * (i1 + i2) / 2
    st = {"iavg": ia.sum() / T, "irms": math.sqrt(ii.sum() / T), "imax": i.max(), "imin": i.min(),
          "ripple": i.max() - i.min()}
    nl = w["nlev"]
    for leg in "AB":
        for k in range(nl - 1):
            sk = w["s"][(leg, k)]
            st[f"rms2_top_{leg}{k}"] = np.sum(sk * ii) / T
            st[f"rms2_bot_{leg}{k}"] = np.sum((1 - sk) * ii) / T
        so = w["s"][(leg, 0)]
        iport = np.sum(so * ia) / T                     # current drawn from (A) / delivered to (B) the port
        st[f"iport_{leg}"] = iport
        st[f"icap_rms_{leg}"] = math.sqrt(max(np.sum(so * ii) / T - iport ** 2, 0.0))
        if nl == 3:
            dfc = w["s"][(leg, 0)] - w["s"][(leg, 1)]
            st[f"ifc_avg_{leg}"] = np.sum(dfc * ia) / T
            st[f"ifc_rms_{leg}"] = math.sqrt(np.sum(dfc * dfc * ii) / T)
            # flying-cap charge excursion (peak-peak) over the period
            q = np.concatenate([[0.0], np.cumsum(dfc * ia * (1 if leg == "A" else -1))])
            st[f"qfc_pp_{leg}"] = q.max() - q.min()
    st["pA"] = w["va"] * st["iport_A"]
    st["pB"] = w["vb"] * st["iport_B"]
    return st


def events(w):
    """switching events per pair: (leg, k, 'rise'|'fall', i_out) with i_out = current out of the leg's switch node"""
    t, i, T = w["t"], w["i"], w["T"]
    ev = []
    for leg, k, d, c in w["pairs"]:
        for kind, te in (("rise", (c - d * T / 2) % T), ("fall", (c + d * T / 2) % T)):
            ie = float(np.interp(te, t, i))
            ev.append((leg, k, kind, ie if leg == "A" else -ie))
    return ev


def sample(w, n=4096, shift=0.0):
    """sample i_L and the port currents on a uniform grid (for interleaving sums); shift in periods"""
    T = w["T"]
    ts = (np.arange(n) / n * T + shift * T) % T
    il = np.interp(ts, w["t"], w["i"])
    seg = np.clip(np.searchsorted(w["t"], ts, side="right") - 1, 0, len(w["dt"]) - 1)
    ia = w["s"][("A", 0)][seg] * il
    ib = w["s"][("B", 0)][seg] * il
    return il, ia, ib


# ------------------------------------------------------------------------------------------------ inductor
def mu_frac(mat, h_am):
    h_oe = np.abs(h_am) * 4 * math.pi / 1000.0
    if "bias_poco" in mat:       # POCO catalogue form: %u = a / (1 + (H/b)^c) + d, H in Oe
        a, b, c, d = mat["bias_poco"]
        return (a / (1.0 + (h_oe / b) ** c) + d) / 100.0
    a, b, c = mat["bias"]
    return (1.0 / (a + b * h_oe ** c)) / 100.0


def b_dc(mat, h_am):
    """B(H) = mu0*mu_i*integral(%mu dH) - incremental permeability integrated (T)"""
    hs = np.linspace(0, abs(h_am), 200)
    return MU0 * mat["mu"] * np.trapezoid(mu_frac(mat, hs), hs)


def igse_k(a, b, c):
    """catalog P[mW/cm3] = a*B^b*f[kHz]^c  ->  SI k (W/m3 with f in Hz, B in T) and iGSE k_i"""
    k = a * 1e3 * (1e-3) ** c
    th = np.linspace(0, 2 * math.pi, 4001)
    integ = np.trapezoid(np.abs(np.cos(th)) ** c, th)
    ki = k / ((2 * math.pi) ** (c - 1) * integ * 2 ** (b - c))
    return k, ki


def core_loss_density(mat, w, nturn, le):
    """iGSE on the piecewise-linear flux B = mu0*mu*%mu(Hdc)*N*i/le (W/m3); the higher of the material's loss fits"""
    i, dt, T = w["i"], w["dt"], w["T"]
    hdc = nturn * abs(np.sum(dt * (i[:-1] + i[1:]) / 2) / T) / le
    kb = MU0 * mat["mu"] * mu_frac(mat, hdc) * nturn / le
    db_seg = kb * np.diff(i)
    dbpp = kb * (i.max() - i.min())
    m = dt > 0
    if dbpp <= 0:
        return 0.0
    pv = 0.0
    for a, b, c, _src in mat["loss"]:
        _, ki = igse_k(a, b, c)
        pv = max(pv, ki * dbpp ** (b - c) * np.sum(np.abs(db_seg[m] / dt[m]) ** c * dt[m]) / T)
    return pv


def design_inductor(L_req, i_rms, wf_design, i_trip, mats=None, objective="loss+mass"):
    """Pick the lightest (or lowest-loss) catalog E-core stack + turns meeting: L_inc(45 A) >= L_req,
    %mu(I_OCP) >= MU_FRAC_MIN, B(I_OCP) <= B_FRAC_MAX*Bsat, window fill, current density, temperature rise.
    wf_design: waveform at the inductor design point (for core loss)."""
    best = None
    st = wf_stats(wf_design)
    for cname, core in dv.ECORES.items():
        for mname, mat in dv.MATERIALS.items():
            if mats and mname not in mats:
                continue
            if mat["mu"] not in core["AL"]:
                continue
            for nst in (1, 2, 3, 4):
                al = core["AL"][mat["mu"]] * 1e-9 * nst
                le = core["le"] * 1e-3
                ae = core["Ae"] * 1e-6 * nst
                nturn = None
                for n in range(4, 160):
                    if al * n * n * mu_frac(mat, n * I_MAX / le) >= L_req:
                        nturn = n
                        break
                if nturn is None:
                    continue
                h_ocp = nturn * i_trip / le
                if mu_frac(mat, h_ocp) < MU_FRAC_MIN or b_dc(mat, h_ocp) > B_FRAC_MAX * mat["bsat"]:
                    continue
                a_cu = i_rms / J_CU
                wa = core["M"] * 2 * core["D"] * 1e-6
                if nturn * a_cu > K_CU * wa:
                    continue
                ctot = core["C"] * nst * 1e-3
                mlt = 2 * (core["F"] * 1e-3 + ctot) + math.pi * core["M"] * 1e-3
                rdc20 = dv.CU_RHO20 * nturn * mlt / a_cu
                ve = core["Ve"] * 1e-9 * nst
                m_core = ve * mat["rho"]
                m_cu = 1.15 * nturn * mlt * a_cu * 8960          # +15 % Litz serving, bobbin, insulation
                pcore = core_loss_density(mat, wf_design, nturn, le) * ve
                rdc = rdc20 * (1 + dv.CU_ALPHA * (110 - 20))
                pcu = rdc * (st["iavg"] ** 2 + FR_AC * (st["irms"] ** 2 - st["iavg"] ** 2))
                # wound-part surface (box around core + winding) and forced-air rise: dT = P/(h*A), h = 25 W/m2K (estimate)
                surf = 2 * (core["A"] * 2 * core["B"] + core["A"] * (ctot * 1e3 + 2 * core["M"]) + 2 * core["B"] * (ctot * 1e3 + 2 * core["M"])) * 1e-6
                dtemp = (pcore + pcu) / (25.0 * surf)
                if dtemp > IND_DT_MAX:
                    continue
                mass = m_core + m_cu
                cand = {"core": cname, "material": mname, "part": f"00{mat['code']}{cname}0{mat['mu']:02d}", "n_stack": nst,
                        "turns": nturn, "L0": al * nturn ** 2, "L_full": al * nturn ** 2 * mu_frac(mat, nturn * I_MAX / le),
                        "L_ocp": al * nturn ** 2 * mu_frac(mat, h_ocp), "mu_ocp": mu_frac(mat, h_ocp), "B_ocp": b_dc(mat, h_ocp),
                        "B_full": b_dc(mat, nturn * I_MAX / le), "a_cu": a_cu, "mlt": mlt, "rdc20": rdc20, "le": le, "ae": ae,
                        "ve": ve, "mass": mass, "m_core": m_core, "m_cu": m_cu, "p_core_design": pcore, "p_cu_design": pcu,
                        "dT": dtemp, "surface": surf, "_mat": mat, "al": al}
                key = mass if objective == "mass" else (pcore + pcu + IND_LAMBDA * mass)
                if mass > IND_MASS_MAX:
                    continue
                if best is None or key < best[0]:
                    best = (key, cand)
    return None if best is None else best[1]


def inductor_L(ind, idc):
    return ind["al"] * ind["turns"] ** 2 * mu_frac(ind["_mat"], ind["turns"] * abs(idc) / ind["le"])


# PV inductor: the magnetics engineer's construction (sim/out/magnetics/design_pv_inductor.json, keys documented at the top of
# sim/magnetics.py) - L(I) from its core (A_L, N, l_e) with the POCO NPC 26 DC-bias fit, core loss from its loss table vs B_m
# (scaled to the higher of catalogue / OpenMagnetics as the file does), copper from its loss grid (R_eff = P_cu / I_rms^2),
# hot spot from its thermal resistance. Without the file: the interim figures of the magnetics review (loss 54 W, rise 75 K).
IND_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sim", "out", "magnetics", "design_pv_inductor.json")
POCO_NPC26_BIAS = (93.5843, 314.4894, 2.5844, 5.4287)   # sim/data/asian_magnetic_materials.csv (POCO 2026 catalogue p27, NPC 26)
IND_CORR = {"loss": 54.0 / 48.3, "hot_rise_K": 75.0, "at_W": 54.0, "internal_frac": 19.0 / 75.0,
            "source": "interim: sim/out/magnetics/report.md independent calculation (loss 54 W, hot-spot rise 75 K)"}
IND_DESIGN = None
if os.path.exists(IND_JSON):
    _j = json.load(open(IND_JSON))
    _th = _j["thermal"]
    IND_DESIGN = _j
    IND_CORR = {"loss": 1.0, "hot_rise_K": _th["dT_hotspot_K"], "at_W": _th["dT_hotspot_K"] / _th["Rth_hotspot_to_air_K_W"],
                "internal_frac": _th["dT_internal_K"] / _th["dT_hotspot_K"], "rth_K_W": _th["Rth_hotspot_to_air_K_W"],
                "source": f"{os.path.relpath(IND_JSON, ROOT if 'ROOT' in globals() else os.path.dirname(IND_JSON))} rev {_j['revision']}"}
else:
    print("note: sim/out/magnetics/design_pv_inductor.json not found - inductor uses the interim review figures (54 W, 75 K)")


def inductor_from_design(j=None):
    """ind dict (the keys the loss / thermal / spec code reads) for the magnetics engineer's construction"""
    j = j or IND_DESIGN
    core, el, w = j["core"], j["electrical"], j["windings"][0]
    mat = {"mu": 26, "bsat": el["B_sat_T"], "bias_poco": POCO_NPC26_BIAS, "rho": 6500, "name": core["material"]}
    n, le, ae = w["turns"], core["le_m"], core["Ae_m2"]
    ind = {"design": j, "core": core["part_number"], "material": f"POCO {core['material']}", "part": core["part_number"], "n_stack": 2,
           "turns": n, "al": core["AL_H"], "le": le, "ae": ae, "ve": core["Ve_m3"], "_mat": mat, "a_cu": w["copper_area_m2"],
           "mass": j["mass_kg"], "m_core": None, "m_cu": None, "surface": None, "dT": j["thermal"]["dT_hotspot_K"],
           "rdc20": w["R_dc_110C_ohm"] / (1 + dv.CU_ALPHA * 90), "cost_usd": j["cost"]["total_usd"]}
    ind["L0"] = ind["al"] * n * n
    ind["L_full"] = inductor_L(ind, I_MAX)
    ind["B_full"] = b_dc(mat, n * I_MAX / le)
    ind["conductor"] = w["conductor"]
    lt = max(j["loss_table"], key=lambda r: r["P_total_W"])
    bm, pc = zip(*j["loss_model"]["core"]["table_W_vs_Bm_T"])
    ind["k_core"] = lt["P_core_W"] / float(np.interp(lt["B_pk_T"], bm, pc))    # the file's "higher of" factor
    ind["core_table"] = (np.array(bm), np.array(pc))
    g = j["loss_grid"]
    ind["r_eff"] = (np.array(g["I_rms_A"]), np.array(g["P_cu_W"]) / np.array(g["I_rms_A"]) ** 2)
    return ind


def ind_ocp_fields(ind, i_ocp):
    h = ind["turns"] * i_ocp / ind["le"]
    ind.update({"L_ocp": inductor_L(ind, i_ocp), "mu_ocp": mu_frac(ind["_mat"], h), "B_ocp": b_dc(ind["_mat"], h)})
    return ind


def ind_hot_rise(p_ind, h_ratio=1.0):
    """inductor hot-spot rise above local air (K) for loss p_ind; h_ratio = h(design air flow) / h(actual)"""
    c = IND_CORR
    return c["hot_rise_K"] * p_ind / c["at_W"] * (c["internal_frac"] + (1 - c["internal_frac"]) * h_ratio)


def inductor_loss(ind, w, t_wind=110.0):
    st = wf_stats(w)
    if ind.get("design"):        # magnetics engineer's construction: B_m = L(I) x ripple / (2 N A_e), copper from R_eff(I_rms)
        bm = inductor_L(ind, st["iavg"]) * st["ripple"] / (2 * ind["turns"] * ind["ae"])
        pcore = float(np.interp(bm, *ind["core_table"])) * ind["k_core"]
        pcu = float(np.interp(st["irms"], *ind["r_eff"])) * st["irms"] ** 2
        return pcore, pcu
    pcore = core_loss_density(ind["_mat"], w, ind["turns"], ind["le"]) * ind["ve"] * IND_CORR["loss"]
    rdc = ind["rdc20"] * (1 + dv.CU_ALPHA * (t_wind - 20))
    pcu = rdc * (st["iavg"] ** 2 + FR_AC * max(st["irms"] ** 2 - st["iavg"] ** 2, 0.0)) * IND_CORR["loss"]
    return pcore, pcu


# ------------------------------------------------------------------------------------------------ semiconductor losses
def qoss(d, v):
    """C_oss charge from the E_oss curve: Q(V) = integral dE/v"""
    vs = np.linspace(1.0, max(abs(v), 2.0), 200)
    e = dv.eoss(d, vs)
    return float(np.sum(np.diff(e) / (0.5 * (vs[1:] + vs[:-1]))))


def damper_energy(tab, hard_on, v, i):
    """energy (J) into a leg's RC damper for one edge: bilinear in the ngspice table (v, i grid; clamped at its edges).
    hard_on: hard turn-on edge (table 'e_on'), else a hard turn-off of the conducting device ('e_off')."""
    e = np.array(tab["e_on" if hard_on else "e_off"])
    row = [float(np.interp(i, tab["i"], e[k])) for k in range(len(tab["v"]))]
    return float(np.interp(v, tab["v"], row))


def edge_losses(d, npar, vsw, i_out, kind, tj, eon_mult=1.0, eoff_mult=1.0):
    """energy into (top, bottom) device positions for one edge of one switch pair.
    kind 'rise' = bottom off -> top on, 'fall' = top off -> bottom on.  i_out = current out of the switch node.
    returns (E_top_sw, E_bot_sw, E_top_dt, E_bot_dt, hard_flag)"""
    n = npar
    ia = abs(i_out) / n
    eo = float(dv.eoss(d, vsw))
    qo = qoss(d, vsw)
    vsd = float(dv.vsd(d, ia))
    e_dt = n * vsd * ia * T_DEAD
    # the device that turns on is 'on' (top for rise); its partner is 'off'
    hard_on = (i_out > 0) if kind == "rise" else (i_out < 0)
    if hard_on:
        e_on = n * (float(dv.e_sw(d, "on", vsw, ia, tj)) * eon_mult + eo)
        e_rr = n * max(float(dv.e_sw(d, "rr", vsw, ia, tj)) - eo, 0.0)
        e_new, e_old, dt_old, dt_new = e_on, e_rr, e_dt, 0.0
    else:
        # the conducting device turns off with |i| (current then drives the node to the other rail)
        e_off = n * max(float(dv.e_sw(d, "off", vsw, ia, tj)) * eoff_mult - eo, 0.0)
        x = min(1.0, abs(i_out) * T_DEAD_MIN / (2 * n * qo)) if qo > 0 else 1.0
        vr = vsw * (1 - x)                                    # incomplete ZVS: residual voltage at turn-on
        e_res = n * qoss(d, vr) * vr if vr > 1.0 else 0.0
        e_new, e_old, dt_old, dt_new = e_res, e_off, 0.0, e_dt * x
    if kind == "rise":
        return e_new, e_old, dt_new, dt_old, hard_on
    return e_old, e_new, dt_old, dt_new, hard_on


# ------------------------------------------------------------------------------------------------ cell model
def make_design(topo, dev, npar, fsw, ind, caps=None, flow_m3h=150.0, name=None):
    """topo: '2L' (A), '3L' (B flying cap), 'SPLIT' (C: two half-voltage FSBBs in series, losses = 2 x half-cell)"""
    d = dv.MOSFETS[dev]
    return {"name": name or f"{topo}-{dev}x{npar}-{fsw/1e3:.0f}k", "topo": topo, "dev": dev, "d": d, "npar": npar,
            "fsw": fsw, "ind": ind, "caps": caps or {}, "flow": flow_m3h,
            "nlev": 3 if topo == "3L" else 2, "vscale": 0.5 if topo == "SPLIT" else 1.0}


def positions(design):
    """switch positions of one cell: list of (leg, k, 'top'|'bot') (C has two identical half-cells -> x2 in counts)"""
    nl = design["nlev"]
    return [(leg, k, tb) for leg in "AB" for k in range(nl - 1) for tb in ("top", "bot")]


def cell_losses(design, va, vb, p, tj=None, t_air=T_AIR, waveform_only=False):
    """all losses of one cell at (va, vb, p).  tj: dict position -> Tj (C); None = iterate with the thermal model."""
    topo, d, n, fsw, ind = design["topo"], design["d"], design["npar"], design["fsw"], design["ind"]
    nl, vsc = design["nlev"], design["vscale"]
    half = 2 if topo == "SPLIT" else 1
    va_c, vb_c, p_c = va * vsc, vb * vsc, p / half          # one (half-)cell
    i_guess = abs(p_c) / min(va_c, vb_c)
    L = inductor_L(ind, i_guess)
    w = waveform(va_c, vb_c, p_c, fsw, L, nl)
    if waveform_only:
        return w
    st = wf_stats(w)
    ev = events(w)
    pos = positions(design)
    r_conv, r_air, _ = heatsink_rth(flow_m3h=design["flow"], rho=design.get("rho", 1.10))
    if tj is None:
        tj = {q: 100.0 for q in pos}
        iterate = 8
    else:
        iterate = 1
    vleg = {"A": va_c / (nl - 1), "B": vb_c / (nl - 1)}      # commutation voltage of a pair (FC at V/2)
    rks = design.get("rg_ks", 0.0)                            # per-device Kelvin-source resistor, in both gate paths
    eon_mult = dv.eon_rg_factor(d, d["rg_ext"] * design.get("rg_on_mult", 1.0) + rks)
    eoff_mult = dv.eoff_rg_factor(d, d["rg_ext"] * design.get("rg_off_mult", 1.0) + rks) * d.get("eoff_vgs_factor", 1.0)
    for _ in range(iterate):
        pc, psw, pdt = {q: 0.0 for q in pos}, {q: 0.0 for q in pos}, {q: 0.0 for q in pos}
        pdamp = 0.0
        hard = 0
        for leg in "AB":
            for k in range(nl - 1):
                rt = float(dv.rds(d, tj[(leg, k, "top")])) / n
                rb = float(dv.rds(d, tj[(leg, k, "bot")])) / n
                pc[(leg, k, "top")] = st[f"rms2_top_{leg}{k}"] * rt
                pc[(leg, k, "bot")] = st[f"rms2_bot_{leg}{k}"] * rb
        for leg, k, kind, iout in ev:
            et, eb, dtt, dtb, hd = edge_losses(d, n, vleg[leg], iout, kind, tj[(leg, k, "top")], eon_mult, eoff_mult)
            psw[(leg, k, "top")] += et * fsw
            psw[(leg, k, "bot")] += eb * fsw
            pdt[(leg, k, "top")] += dtt * fsw
            pdt[(leg, k, "bot")] += dtb * fsw
            hard += hd
            if design.get("damp"):                            # leg RC damper: ringing energy of this edge (ngspice table)
                pdamp += damper_energy(design["damp"], hd, vleg[leg], abs(iout)) * fsw
        pdev = {q: pc[q] + psw[q] + pdt[q] for q in pos}
        p_semi = sum(pdev.values()) * half
        if d.get("rth_includes_tim"):
            rjs = d["rth_jc"]
        else:
            rjs = d.get("rth_jc_max", d["rth_jc"]) + rth_cs(d) / 1.0
        t_sink = t_air + p_semi * (r_conv + r_air)
        tj_new = {q: t_sink + pdev[q] / n * (rjs + RTH_SPREAD) for q in pos}
        if max(tj_new.values()) > 400:
            tj = tj_new
            break
        tj = {q: 0.5 * tj[q] + 0.5 * tj_new[q] for q in pos} if iterate > 1 else tj_new
    pcore, pcu = inductor_loss(ind, w)
    # capacitors: AC current of the switching ports flows in the local port bank; flying caps in 3L
    pcap = 0.0
    for leg in "AB":
        cb = design["caps"].get(leg)
        if cb:
            pcap += st[f"icap_rms_{leg}"] ** 2 * cb["esr"] / cb["n"]
        if nl == 3 and design["caps"].get("FC"):
            fc = design["caps"]["FC"]
            pcap += st[f"ifc_rms_{leg}"] ** 2 * fc["esr"] / fc["n"]
    pgate = len(w["pairs"]) * 2 * n * d["qg"] * (d["vgs_on"] - d["vgs_off"]) * fsw   # switching devices only
    pmisc = st["irms"] ** 2 * R_MISC
    paux = len(pos) * P_AUX_DRV + P_AUX_SENSE / half + (2 * 0.5 if nl == 3 else 0.0) + (0.5 if topo == "SPLIT" else 0.0)
    loss = {"cond": sum(pc.values()) * half, "sw": sum(psw.values()) * half, "dead": sum(pdt.values()) * half,
            "core": pcore * half, "cu": pcu * half, "cap": pcap * half, "gate": pgate * half, "misc": pmisc * half,
            "aux": paux * half, "damp": pdamp * half}
    ptot = sum(loss.values())
    pout = abs(p)
    res = {"va": va, "vb": vb, "p": p, "mode": w["mode"], "da": w["da"], "db": w["db"], "L": L, "loss": loss,
           "ptot": ptot, "eff": pout / (pout + ptot) if pout > 0 else 0.0, "tj": tj, "tj_max": max(tj.values()),
           "t_sink": t_sink, "st": st, "w": w, "pdev": pdev, "pc": pc, "psw": psw, "pdt": pdt, "hard_edges": hard,
           "vs_err": w["vs_err"], "half": half}
    return res


# ================================================================================================ ngspice (shared)
import subprocess  # noqa: E402

SPICE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spice")


def ngspice(deck, timeout=900):
    """run 'ngspice -b' on a deck in sim/spice; raises on error"""
    try:
        r = subprocess.run(["ngspice", "-b", os.path.basename(deck)], cwd=os.path.dirname(deck), capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ngspice timed out after {timeout} s on {deck}")
    if r.returncode != 0 or "Error" in r.stdout + r.stderr or "aborted" in r.stdout + r.stderr:
        raise RuntimeError(f"ngspice failed on {deck}:\n{(r.stdout + r.stderr)[-1500:]}")
    return r.stdout


SOLVER_OPTIONS = [".options method=gear", ".options method=gear maxord=2 reltol=2e-3 abstol=1e-9",
                  ".options method=gear maxord=2 reltol=2e-3 abstol=1e-9 gmin=1e-10",
                  ".options method=gear maxord=2 reltol=5e-3 abstol=1e-8", ".options method=trap trtol=7"]
R_DAMP = 10.0            # ohm in parallel with every loop inductance: skin-effect / eddy / cap-ESR damping of the 50-100 MHz
                         # ringing (lossy-inductor representation, ESTIMATE: decays the ringing in ~2-3 cycles)
SOLVER_TIMEOUT = 20      # s per attempt (a converging commutation run takes 1-3 s); stalled runs fall through


def wrdata(path, keep=False):
    """ngspice wrdata (no singlescale): columns t0 v0 t1 v1 ... -> time, [vectors]; the .dat is deleted after reading
    (decks are kept in sim/spice, data is regenerated by running the script)"""
    d = np.loadtxt(path)
    if not keep:
        os.remove(path)
    return d[:, 0], [d[:, 2 * k + 1] for k in range(d.shape[1] // 2)]


def vgs_factor(d, tag):
    """E_off at the applied off-state gate voltage / E_off at the datasheet one, calibrated VDMOS at the datasheet point"""
    cal = calibrate(d, tag)
    v, i = d["sw_v"], d["check"][0][2]
    e_app = dpt(d, 1, v, i, 15e-9, f"{tag}_vgs", rg=cal["rg_eff"], a=cal["a"], vgs_off=d["vgs_off"])["e_off"]
    e_ds = dpt(d, 1, v, i, 15e-9, f"{tag}_vgs", rg=cal["rg_eff"], a=cal["a"], vgs_off=d.get("vgs_off_ds", d["vgs_off"]))["e_off"]
    return e_app / e_ds


def vdmos_card(name, d, a=0.5):
    """ngspice VDMOS model from datasheet anchors (25 C): V_th, g_fs (or a +4 V plateau at the test current),
    R_DS(on) at the drive voltage, C_iss/C_rss/C_oss at the datasheet voltage (body-diode junction C, M = 0.5),
    Q_gd via the C_gd shape parameter a (fitted in fit_vdmos), body diode from V_SD, recovery time from Q_rr."""
    vth = d["vth"]
    kp = d["gfs"] ** 2 / (2 * d["gfs_i"]) if d.get("gfs") else 2 * d["gfs_i"] / 4.0 ** 2
    r25 = d["rds25"] - (2.2e-3 if d.get("half_bridge_module") else 0.0)
    rch = 1.0 / (kp * (d["vgs_on"] - vth))
    rd = max(r25 - rch - 1e-3, 0.1 * r25)
    crss, ciss, coss, vc = d["crss"], d["ciss"], d["coss"], d["c_at"]
    cjo = (coss - crss) * (1 + vc / 3.0) ** 0.5
    nvt = 3 * 0.02585
    isat = 1.0 / math.exp(2.7 / nvt)
    rb = max((d["vsd"] - 2.7 - nvt * math.log(d["vsd_i"])) / d["vsd_i"], 1e-3)
    # body diode capacitive only (Tt = 0): the SPICE junction diode recovers abruptly (no soft-recovery parameter) and
    # gave an unphysical ~600 V spike on the recovering device with Tt > 0.  Q_rr / E_rr stay in the datasheet loss model;
    # the diode-side overshoot is a Gate-1 double-pulse measurement item.
    tt = 0.0
    return (f".model {name} VDMOS(Vto={vth} Kp={kp:.4g} lambda=0.01 Rd={rd:.4g} Rs=0.5m Rg={d['rg_int']} "
            f"Cgs={ciss - crss:.4g} Cgdmax={0.2 * ciss:.4g} Cgdmin={crss:.4g} a={a:.4g} Cjo={cjo:.4g} Vj=3 M=0.5 "
            f"Is={isat:.3g} N=3 Rb={rb:.4g} Tt={tt:.3g})")


def fit_vdmos(d, tag):
    """bisection on the C_gd shape parameter a so that the simulated Q_gd(0..V_test) equals the datasheet Q_gd"""
    os.makedirs(SPICE, exist_ok=True)
    deck = os.path.join(SPICE, f"pv_fit_{tag}.cir")
    vt = d["qgd_v"]

    def qgd(a):
        txt = (f"* PVCELL VDMOS C_gd fit for {tag}: drain ramp 0..{vt:.0f} V, gate held at {d['vgs_off']} V\n"
               f"{vdmos_card('m', d, a)}\nVg g 0 {d['vgs_off']}\nVd dd 0 PWL(0 0 10u {vt})\nRd dd dn 1m\nM1 dn g 0 m\n"
               f".save i(Vg) v(dn)\n.tran 5n 10u\n.control\nrun\nwrdata pv_fit_{tag}.dat i(Vg) v(dn)\nquit\n.endc\n.end\n")
        open(deck, "w").write(txt)
        ngspice(deck)
        t, (ig, vd) = wrdata(os.path.join(SPICE, f"pv_fit_{tag}.dat"))
        return abs(np.trapezoid(ig, t))
    lo, hi = math.log(1e-3), math.log(50.0)
    for _ in range(18):
        mid = 0.5 * (lo + hi)
        if qgd(math.exp(mid)) > d["qgd"]:      # larger a -> C_gd collapses faster -> less charge
            lo = mid
        else:
            hi = mid
    a = math.exp(0.5 * (lo + hi))
    q = qgd(a)
    return a, q


_CAL = {}
CARD_REV = 6      # bump when vdmos_card() or the commutation deck changes so cached calibrations are refitted


def calibrate(d, tag):
    """VDMOS C_gd shape fitted to Q_gd, then the effective external gate resistance fitted so that the simulated
    turn-off energy at the datasheet test point (V_ref, I_test, 25 C, 15 nH) equals the datasheet-model E_off.
    The transient model is used for di/dt / overshoot only; its turn-on energy is not used (VDMOS diode recovery)."""
    if tag in _CAL:
        return _CAL[tag]
    v, i = d["sw_v"], d["check"][0][2]
    target = float(dv.e_sw(d, "off", v, i, 25.0))
    cache = os.path.join(OUT, "vdmos_calibration.json")       # written by this function; reused only if the
    old = json.load(open(cache)) if os.path.exists(cache) else {}   # datasheet targets it was fitted to are unchanged
    o = old.get(tag)
    if o and abs(o["e_off_ds"] - target) < 1e-9 and abs(o["qgd_ds"] - d["qgd"]) < 1e-12 and o.get("card_rev") == CARD_REV:
        _CAL[tag] = o
        return o
    a, q = fit_vdmos(d, tag)
    lo, hi = math.log(0.3), math.log(25.0)
    vds_gate = d.get("vgs_off_ds", d["vgs_off"])         # the datasheet energies belong to the datasheet gate drive
    for _ in range(7):
        rg = math.exp(0.5 * (lo + hi))
        try:
            e = dpt(d, 1, v, i, 15e-9, f"{tag}_cal", rg=rg, a=a, vgs_off=vds_gate)["e_off"]
        except RuntimeError:                       # treat a non-converging point as 'too fast' and move up in R_G
            lo = math.log(rg)
            continue
        if e > target:
            hi = math.log(rg)
        else:
            lo = math.log(rg)
    rg = math.exp(0.5 * (lo + hi))
    r = dpt(d, 1, v, i, 15e-9, f"{tag}_cal", rg=rg, a=a, vgs_off=vds_gate)
    _CAL[tag] = {"a": a, "qgd": q, "qgd_ds": d["qgd"], "rg_eff": rg, "e_off_sim": r["e_off"], "e_off_ds": target, "v": v, "i": i,
                 "card_rev": CARD_REV}
    os.makedirs(OUT, exist_ok=True)
    old[tag] = _CAL[tag]
    json.dump(old, open(cache, "w"), indent=1)
    return _CAL[tag]


R_CLMPI = 0.6        # ohm, UCC21710 Miller clamp pull-down (SLUSD43B p.9: 0.6 ohm at 0.2 A, 4 A, threshold VEE + 1.5..2.5 V)
# gen/gdrv.py rev 4, paralleled devices: per gate a ZXTP25040DFH follower (E gate, B CLMPI node, C into C_VL on the device's
# Kelvin pin, R_VL from VEE), a PMEG4010CEJ (A node, K gate), R_PU from OUTH to the node
R_PU, R_VL, C_VL, VCLMPTH = 150.0, 4.7, 1e-6, 2.0
PNP_CARD = (".model zxtp PNP(IS=1.74e-13 BF=30 BR=10 RB=0.5 RE=0.02 RC=0.04 CJE=150p CJC=50p VJC=0.75 MJC=0.4 TF=0.59n "
            "VAF=100)")      # ZXTP25040DFH p4 worst case: hFE 30 and V_BE(on) 0.90 V at 3 A, V_CE(sat) 0.22 V, Cobo 17.4 pF, fT 270 MHz
PMEG_CARD = ".model dpmeg D(Is=6.2e-8 N=1 Rs=0.14 Cjo=77p Vj=0.4 M=0.4)"   # PMEG4010CEJ p3: 310 mV at 10 mA, 570 mV at 1 A
T_PNP = -30.0        # C, gate-driver board cold (V_BE up): worst case for the hold level


def _stage(vdc, lloop, leg, t_on0):
    """bus -> dtop (top drain); bus- = node 0.  leg None: lumped loop with R_DAMP across it; else bulk bank -> l_bus ->
    decoupling -> l_rest -> pins with the RC damper (+ the switched Q_rr surrogate across the top position)"""
    if leg is None:
        return [f"Vdc dcp 0 {vdc}", f"Rloop dcp dl 5m", f"Lloop dl dtop {lloop}", f"Rdamp dl dtop {R_DAMP}"]
    lines = [f"Vdc dcp 0 {vdc}", "Rsrc dcp bk 50m",
             f"Cbk bk bk1 {leg['c_bulk']}", f"Lbk bk1 bk2 {leg['esl_bulk']}", f"Rbk bk2 0 {leg['esr_bulk']}",
             f"Lbus bk lb {leg['l_bus']}",
             f"Cdc lb dc1 {leg['c_dec']}", f"Ldc dc1 dc2 {leg['esl_dec']}", f"Rdc dc2 0 {leg['esr_dec']}",
             f"Lloop lb dtop {leg['l_rest']}", f"Rdmp dtop dm {leg['r_damp']}", f"Cdmp dm 0 {leg['c_damp']}",
             "Rdmpx dm 0 1e8"]          # 11 uA leak: lets the DC operating point converge (else a ringing start)
    if leg.get("c_rr"):
        lines += [f"Crr crr mid {leg['c_rr']}", "Srr dtop crr sctl 0 swrr", ".model swrr SW(Vt=0.5 Vh=0.1 Ron=1m Roff=1e9)",
                  f"Vsctl sctl 0 PULSE(1 0 {t_on0 + 0.3e-6} 1n 1n 1 2)"]
    return lines


def _run_deck(deck, lines, tag):
    for opt in SOLVER_OPTIONS:                     # stiff VDMOS edges: fall back through integration settings
        open(deck, "w").write("\n".join(lines).replace("OPTIONS", opt) + "\n")
        try:
            ngspice(deck, timeout=SOLVER_TIMEOUT)
            return wrdata(os.path.join(SPICE, f"pv_dpt_{tag}.dat"))
        except RuntimeError:
            continue
    raise RuntimeError(f"no solver setting converged for {deck}")


def gate_deck(d, npar, vdc, i_load, lloop, tag, rg, a, rg_on, rks, leg=None, mode="miller", vth_victim=None, ton=1.0e-6):
    """the rev-4 gate network AS DRAWN (gen/gdrv.py channel(), paralleled devices) on the LOW-side devices, Kelvin pin =
    bus- (keeps the exponential parts near 0 V, where ngspice converges): per gate Ron/Roff split outputs, 10k + Zener pair
    to the Kelvin pin, PMEG4010CEJ (A node, K gate), ZXTP25040DFH (B node, E gate, C into C_VL on the Kelvin pin, R_VL from
    VEE), Kelvin R to the COM star; per channel R_PU from OUTH to the node, the UCC21710 clamp FET (R_CLMPI) from the node to
    VEE.  mode 'miller': the high side switches (hard turn-on into the low side's body diodes), the low side is held off with
    its clamp engaged.  mode 'own': the low side switches; its clamp FET closes 50 ns after the command is low and the node
    is below VEE + VCLMPTH, opens with the command high; the high side is held off (R_CLMPI stand-in)."""
    os.makedirs(SPICE, exist_ok=True)
    voff, von, tr_, t_on0 = d["vgs_off"], d["vgs_on"], 5e-9, 100e-9
    deck = os.path.join(SPICE, f"pv_dpt_{tag}.cir")
    vic = "vic" if vth_victim is not None else "dut"
    lines = [f"* PVCELL gate network check ({mode}) - {d['mfr']} x{npar}, {vdc:.0f} V, {i_load:.1f} A, R_KS {rks} ohm. "
             f"Generated by sim/pv_tradeoff.py gate_deck()", vdmos_card("dut", d, a)]
    if vth_victim is not None:
        lines.append(vdmos_card("vic", dict(d, vth=vth_victim), a))
    lines += _stage(vdc, lloop, leg, t_on0)
    lines += [PNP_CARD, PMEG_CARD, ".model dsteer D(Is=1e-12 N=0.3)", ".model dz22 D(Is=1e-14 BV=21.0 IBV=5m Rs=1)",
              ".model dz56 D(Is=1e-14 BV=4.9 IBV=5m Rs=2)", f".model swcl SW(Vt=0.5 Vh=0.05 Ron={R_CLMPI} Roff=1e9)",
              "Vsense sl 0 0", f"Rksl kl sl {rks / npar}", f"Vel vel kl {voff}", f"Rksh kh mid {rks / npar}"]
    low_sw = mode == "own"
    pulse = f"PULSE({voff} {von} {t_on0} {tr_} {tr_} {ton} 1)"
    if low_sw:           # low side switches with its full channel; high side held off
        lines += [f"Iload dtop mid {i_load}", f"Vgl gld kl {pulse}", "Dgon gld outh dsteer", "Dgof outl gld dsteer",
                  f"Rpu outh cll {R_PU}", "Scl cll vel ctld 0 swcl", f"Bct ctl 0 V=u(-v(gld,kl))*u({VCLMPTH}-v(cll,vel))",
                  "Rct ctl ctld 1k", "Cct ctld 0 50p", "Dct ctld ctl dsteer", f"Vgh ghd kh {voff}", "Vsh dtop dth 0"]
    else:                # high side switches; low side held off, its clamp FET on
        lines += [f"Iload mid 0 {i_load}", f"Vgh ghd kh {pulse}", f"Rcl cll vel {R_CLMPI}", "Vsh dtop dth 0"]
    for k in range(npar):
        gx = "gxl0" if k == 0 else f"gl{k}"
        lines += [f"Ml{k} mid {gx} sl {'dut' if low_sw else vic}", f"Mh{k} dth gh{k} mid dut",
                  f"Dpm{k} cll gl{k} dpmeg", f"Q{k} {'qc0' if k == 0 else f'vl{k}'} cll gl{k} zxtp temp={T_PNP}",
                  f"Cvl{k} vl{k} sl {C_VL}", f"Rvl{k} vel vl{k} {R_VL}", f"Rgs{k} gl{k} sl 10k",
                  f"Dzp{k} zm{k} gl{k} dz22", f"Dzn{k} zm{k} sl dz56"]
        if low_sw:
            lines += [f"Rgon{k} outh gl{k} {rg_on}", f"Rgof{k} gl{k} outl {rg}", f"Rgh{k} ghd gh{k} {rg}", f"Rclh{k} gh{k} ghd {R_CLMPI}"]
        else:
            lines += [f"Rgl{k} vel gl{k} {rg}", f"Dgon{k} ghd gn{k} dsteer", f"Rgon{k} gn{k} gh{k} {rg_on}",
                      f"Dgof{k} gf{k} ghd dsteer", f"Rgof{k} gh{k} gf{k} {rg}"]
    lines += ["Vig0 gl0 gxl0 0", "Vq0 qc0 vl0 0"]
    vecs = "v(mid) v(dtop) v(gl0) v(gxl0) i(Vig0) i(Vq0) v(vl0) i(Vsense) i(Vsh)" + (" v(ctld)" if low_sw else "")
    lines += [f".save {vecs}", f".tran 0.05n {t_on0 + ton + 0.6e-6} 0 0.05n", "OPTIONS",
              ".control", "run", f"linearize {vecs}", f"wrdata pv_dpt_{tag}.dat {vecs}", "quit", ".endc", ".end"]
    t, vv = _run_deck(deck, lines, tag)
    vmid, vtop, vg, vgx, ig, iq, vl, ilow, ihigh = vv[:9]
    toff = t_on0 + tr_ + ton
    w_on = (t > t_on0) & (t < t_on0 + 0.5e-6)
    w_off = (t > toff) & (t < toff + 0.5e-6)
    die = vgx - ig * d["rg_int"]
    res = {"mode": mode, "vdc": vdc, "i_load": i_load, "deck": os.path.relpath(deck, os.path.dirname(os.path.dirname(SPICE)))}
    if low_sw:
        ctl = vv[9]
        t_cl = t[(t > toff) & (ctl > 0.5)]
        t_cl = float(t_cl[0]) if len(t_cl) else float(t[-1])
        res.update(i_pnp_own_on=float(np.abs(iq[(t > t_on0) & (t < toff)]).max()),
                   i_pnp_own_off_pre=float(np.abs(iq[(t > toff) & (t < t_cl)]).max()), t_clamp_own=t_cl - toff,
                   i_pnp_own_after=float(np.abs(iq[w_off & (t >= t_cl)]).max()),
                   e_on=float(np.trapezoid((vmid * ilow)[w_on], t[w_on])), dvdt_on=float(-np.gradient(vmid, t)[w_on].min()))
    else:
        vhs = vtop - vmid
        on = w_on & (iq > 0.5)
        res.update(vgs_off_peak=float(vg[w_on].max()), vgs_off_peak_die=float(die[w_on].max()),
                   i_miller_pk=float(-ig[w_on].min()), i_pnp_pk=float(iq[w_on].max()),
                   v_ec_min=float((vg - vl)[on].min()) if on.any() else None, vgs_off_min=float(vg[w_off].min()),
                   e_on=float(np.trapezoid((vhs * ihigh)[w_on], t[w_on])), dvdt_on=float(np.gradient(vmid, t)[w_on].max()),
                   v_pk=float(vmid[w_on].max()))
    return res


def miller_victim_deck(d, npar, wave, tag, rg, a, rks, vth_victim=None):
    """the held-off (victim) devices with the rev-4 gate network AS DRAWN (gen/gdrv.py channel(): per gate R_off to VEE,
    10k + Zener pair to the Kelvin pin, PMEG4010CEJ A node / K gate, ZXTP25040DFH B node / E gate / C into C_VL on the
    Kelvin pin with R_VL from VEE, R_KS to the COM star; the UCC21710 clamp FET holds the node at VEE), source = Kelvin
    pin = ground.  Drain driven by the V_DS(t) the victim sees in the switching deck (dpt(..., wave=True)): the Miller
    current follows from the real dv/dt and the victim's own C_gd; the victim's partial conduction does not feed back
    into the dv/dt (conservative).  Returns pin / die peaks, Miller and PNP currents, V_EC, the victim's energy."""
    os.makedirs(SPICE, exist_ok=True)
    tw, vw = wave
    deck = os.path.join(SPICE, f"pv_dpt_{tag}.cir")
    vic = "vic" if vth_victim is not None else "dut"
    pwl = " ".join(f"{x:.4e} {y:.2f}" for x, y in zip(tw[::2], vw[::2]))
    lines = [f"* PVCELL held-off device with the rev-4 gate network as drawn, V_DS from the switching deck. Generated by "
             f"sim/pv_tradeoff.py miller_victim_deck()", vdmos_card(vic, dict(d, vth=vth_victim) if vth_victim else d, a),
             PNP_CARD, PMEG_CARD, ".model dz22 D(Is=1e-14 BV=21.0 IBV=5m Rs=1)", ".model dz56 D(Is=1e-14 BV=4.9 IBV=5m Rs=2)",
             f"Vd dd 0 PWL({pwl})", "Vsense sl 0 0", f"Rksl kl sl {rks / npar}", f"Vel vel kl {d['vgs_off']}", f"Rcl cll vel {R_CLMPI}"]
    for k in range(npar):
        gx = "gxl0" if k == 0 else f"gl{k}"
        lines += [f"Ml{k} dd {gx} sl {vic}", f"Rgl{k} vel gl{k} {rg}", f"Dpm{k} cll gl{k} dpmeg",
                  f"Q{k} {'qc0' if k == 0 else f'vl{k}'} cll gl{k} zxtp temp={T_PNP}", f"Cvl{k} vl{k} sl {C_VL}",
                  f"Rvl{k} vel vl{k} {R_VL}", f"Rgs{k} gl{k} sl 10k", f"Dzp{k} zm{k} gl{k} dz22", f"Dzn{k} zm{k} sl dz56"]
    vecs = "v(dd) v(gl0) v(gxl0) i(Vig0) i(Vq0) v(vl0) i(Vd)"
    lines += ["Vig0 gl0 gxl0 0", "Vq0 qc0 vl0 0", f".save {vecs}", f".tran 0.05n {tw[-1]:.4e} 0 0.05n", "OPTIONS",
              ".control", "run", f"linearize {vecs}", f"wrdata pv_dpt_{tag}.dat {vecs}", "quit", ".endc", ".end"]
    t, (vdd, vg, vgx, ig, iq, vl, idd) = _run_deck(deck, lines, tag)
    on = iq > 0.5
    return {"vgs_off_peak": float(vg.max()), "vgs_off_peak_die": float((vgx - ig * d["rg_int"]).max()),
            "i_miller_pk": float(-ig.min()), "i_pnp_pk": float(iq.max()), "v_ec_min": float((vg - vl)[on].min()) if on.any() else None,
            "e_vic": float(np.trapezoid(-vdd * idd, t)), "dvdt": float(np.gradient(vdd, t).max()),
            "deck": os.path.relpath(deck, os.path.dirname(os.path.dirname(SPICE)))}


def dpt(d, npar, vdc, i_load, lloop, tag, rg=None, a=None, rg_on=None, vgs_off=None, clamp=True, rks=0.0, leg=None, ton=1.0e-6,
        vth_victim=None, wave=False):
    """double-pulse style commutation (low-side DUT, high-side body diode freewheels), VDMOS models.
    rks: per-device Kelvin-source resistor (in every gate loop, also the Miller-clamp path).
    leg: None = lumped loop lloop with R_DAMP across it; else the physical leg (dict): bulk bank -> l_bus -> decoupling ->
         l_rest -> device pins, RC damper r_damp/c_damp at the pins, optional c_rr = recovery-charge surrogate across the
         freewheeling position, switched in for the turn-on edge only.
    Returns overshoots, di/dt, dv/dt, E_on, E_off, the held-off gate at the pin and at the die (internal R_G), the Miller
    current per device, and for a leg the damper energy per edge and the decoupling peak current.
    ton: DUT on-time between the hard turn-on and the turn-off; vth_victim: V_th of the held-off devices (default = model).
    clamp: the held-off gates are held at VEE through R_CLMPI each (one device: the drawn CLMPI-to-gate; paralleled: a
    stand-in for the rev-4 PNP followers, whose as-drawn check is gate_deck())."""
    os.makedirs(SPICE, exist_ok=True)
    rg = d["rg_ext"] if rg is None else rg
    rg_on = rg if rg_on is None else rg_on          # separate turn-on resistor (diode-steered) when given
    voff = d["vgs_off"] if vgs_off is None else vgs_off
    a = a if a is not None else fit_vdmos(d, tag)[0]
    deck = os.path.join(SPICE, f"pv_dpt_{tag}.cir")
    tr = 5e-9
    t_on0 = 100e-9
    lines = [f"* PVCELL commutation (double-pulse) test - {d['mfr']} {tag} x{npar}, {vdc:.0f} V, {i_load:.1f} A, "
             f"L_loop {lloop*1e9:.1f} nH, R_G {rg} ohm per device. Generated by sim/pv_tradeoff.py",
             vdmos_card("dut", d, a)]
    vic = "dut"
    if vth_victim is not None:
        vic = "vic"
        lines.append(vdmos_card("vic", dict(d, vth=vth_victim), a))
    lines += _stage(vdc, lloop, leg, t_on0)
    lines.append(f"Iload dtop mid {i_load}")
    lines.append(".model dsteer D(Is=1e-12 N=0.3)")
    if clamp:
        lines += [f"Rclh{k} gh{k} ghd {R_CLMPI}" for k in range(npar)]
    for k in range(npar):
        gx = "gx0" if k == 0 else f"gh{k}"                   # device 0: gate-current sense between pin and die
        lines += [f"Mh{k} dtop {gx} mid {vic}", f"Rgh{k} ghd gh{k} {rg}", f"Ml{k} mid gl{k} sl dut"]
        if abs(rg_on - rg) < 1e-9:
            lines += [f"Rgl{k} gld gl{k} {rg}"]
        else:
            lines += [f"Dgon{k} gld gn{k} dsteer", f"Rgon{k} gn{k} gl{k} {rg_on}",
                      f"Dgof{k} gf{k} gld dsteer", f"Rgof{k} gl{k} gf{k} {rg}"]
    lines.append("Vig0 gh0 gx0 0")
    if rks > 0:            # drivers referenced to the Kelvin sources through rks per device (npar equal loops in parallel)
        lines += [f"Vgh ghd kh {voff}", f"Rksh kh mid {rks / npar}", "Vsense sl 0 0", f"Rksl kl sl {rks / npar}",
                  f"Vgl gld kl PULSE({voff} {d['vgs_on']} {t_on0} {tr} {tr} {ton} 1)"]
    else:
        lines += [f"Vgh ghd mid {voff}", "Vsense sl 0 0", f"Vgl gld 0 PULSE({voff} {d['vgs_on']} {t_on0} {tr} {tr} {ton} 1)"]
    vecs = "v(mid) v(dtop) v(gh0) i(Vsense) i(Lloop) v(gx0) i(Vig0)" + (" v(dm) i(Ldc)" if leg else "")
    lines += [f".save {vecs}", f".tran 0.05n {t_on0 + ton + 0.6e-6} 0 0.05n", "OPTIONS",
              ".control", "run", f"linearize {vecs}", f"wrdata pv_dpt_{tag}.dat {vecs}", "quit", ".endc", ".end"]
    t, vv = _run_deck(deck, lines, tag)
    vds, vtop, vgh, ids, il, vgx, ig = vv[:7]
    vhs = vtop - vds                                   # high-side (freewheeling) device V_DS
    vgs_hs = vgh - vds                                 # gate of the device held off while its partner turns on (Miller)
    vgs_die = vgx - vds - ig * d["rg_int"]             # at the die: pin + gate current out of the gate x internal R_G
    toff = t_on0 + tr + ton
    w_off = (t > toff) & (t < toff + 0.5e-6)
    w_on = (t > t_on0) & (t < t_on0 + 0.5e-6)
    didt = np.gradient(il, t)
    e_off = np.trapezoid((vds * ids)[w_off], t[w_off])
    e_on = np.trapezoid((vds * ids)[w_on], t[w_on])
    os_off = float(vds[w_off].max() - vdc)             # switching device at its turn-off
    os_on = float(vhs[w_on].max() - vdc)               # complementary device while the switch turns on hard
    res = {"v_pk": vdc + max(os_off, os_on), "dv_os": max(os_off, os_on), "dv_os_off": os_off, "dv_os_on": os_on,
           "didt": float(-didt[w_off].min()), "dvdt": float(np.gradient(vds, t)[w_off].max()),
           "dvdt_on": float(-np.gradient(vds, t)[w_on].min()), "e_off": float(e_off), "e_on": float(e_on), "a": a,
           "rg": rg, "rg_on": rg_on, "vdc": vdc, "i_load": i_load, "vgs_off": voff, "rks": rks,
           "vgs_off_peak": float(vgs_hs[w_on].max()), "vgs_off_peak_die": float(vgs_die[w_on].max()),
           "i_miller_pk": float(-ig[w_on].min()),
           "deck": os.path.relpath(deck, os.path.dirname(os.path.dirname(SPICE)))}
    if wave:             # held-off device V_DS around the hard turn-on, 0.1 ns grid (drives miller_victim_deck)
        tw = np.arange(t_on0 - 5e-9, t_on0 + 0.4e-6, 0.1e-9)
        res["wave"] = (tw - tw[0], np.interp(tw, t, vhs))
    if leg:
        vd, idc = vv[7], vv[8]
        ir = (vtop - vd) / leg["r_damp"]
        p = ir ** 2 * leg["r_damp"]
        res.update(e_damp_on=float(np.trapezoid(p[w_on], t[w_on])), e_damp_off=float(np.trapezoid(p[w_off], t[w_off])),
                   i_damp_pk=float(np.abs(ir).max()), i_dec_pk=float(np.abs(idc).max()), i_dec_sq=float(np.trapezoid(idc ** 2, t)),
                   i_dec_pk_on=float(np.abs(idc[w_on]).max()), i_dec_pk_off=float(np.abs(idc[w_off]).max()))
    return res


# ================================================================================================ Gate-0 study
CANDIDATES = {
    "A2k": {"label": "A  2-level FSBB, 2000 V SiC", "topo": "2L", "devs": ["IMYH200R012M1H", "IMYH200R024M1H"], "npar": [1, 2]},
    "A17": {"label": "A  2-level FSBB, 1700 V SiC", "topo": "2L", "devs": ["MSC035SMA170B4"], "npar": [1, 2, 3]},
    "B": {"label": "B  3-level flying-cap FSBB, 1200 V SiC", "topo": "3L", "devs": ["C3M0016120K", "C3M0021120K", "C3M0032120K"], "npar": [1, 2]},
    "C": {"label": "C  series-stacked split-bus (2 half-V FSBB), 1200 V SiC", "topo": "SPLIT", "devs": ["C3M0016120K", "C3M0021120K", "C3M0032120K"], "npar": [1, 2]},
    "Cm": {"label": "C  as above with FM3 half-bridge modules", "topo": "SPLIT", "devs": ["CAB011M12FM3"], "npar": [1]},
    "X12": {"label": "ref. 2-level FSBB with 1200 V SiC (voltage rule)", "topo": "2L", "devs": ["C3M0016120K"], "npar": [1, 2]},
}
FSW_SET = [10e3, 12.5e3, 16e3, 20e3, 25e3, 32e3, 40e3, 50e3]
RIPPLE_SET = [0.30, 0.45, 0.60, 0.80]    # peak-peak inductor ripple / 45 A at the worst-ripple point
F_RIPPLE_MIN = 20e3                      # inductor ripple frequency >= 20 kHz (audible-noise ASSUMPTION; 3L ripple is at 2*fsw)
ETA_CORNER_REQ = 0.990                   # selection: every full-power-window corner >= 99.0 % (PV-11 held across the window)
# power-stage BOM index weights relative to one C3M0016120K (ESTIMATES for ranking only, replace with RFQ data):
W_DRIVER, W_IND_KG, W_CAP, W_FC_HW, W_MID_HW = 0.5, 0.35, 0.3, 0.5, 0.3
# full-load evaluation points (V_A, V_B): window corners, the 611 V / 27.5 kW / 45 A points (PV-22), V_A = V_B
FL_POINTS = [(550, 950), (950, 550), (550, 550), (950, 950), (611, 950), (950, 611), (611, 611), (1000, 500), (500, 1000)]
CORNERS = [(550, 950), (950, 550), (550, 550), (950, 950)]
# voltage-class price premium per unit die area (ESTIMATE: lower-volume classes, 2 kV single source); module packaging
V_PREMIUM = {1200: 1.0, 1700: 1.4, 2000: 1.6}
MODULE_PREMIUM = 1.25
REF_DEV = "C3M0016120K"
FC_DEV_FRAC = 0.05                       # flying-cap / midpoint deviation allowed (+-5 % of V/2) for device stress


RG_ON_MULT = {}          # (device, npar, topo) -> turn-on resistor multiplier needed for the peak-voltage rule
RG_ON_STEPS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0]   # Gate-0b: up to 6x (fast Asian parts need more turn-on damping)


def transient_check(des):
    """worst commutation (OVP x share, hardware trip current, estimated loop) with the calibrated VDMOS model; smallest
    R_G,on multiplier for which the switching device (turn-off) and the complementary device (hard turn-on ringing) stay
    <= V_PK_FRAC x V_DSS.  Returns (multiplier or None, sim result at that multiplier)."""
    d, topo = des["d"], des["topo"]
    frac = 1.0 if topo == "2L" else 0.5 * (1 + FC_DEV_FRAC)
    lloop = L_LOOP["module"] if d.get("half_bridge_module") else L_LOOP[topo]
    cal = calibrate(d, des["dev"])
    r = None
    for m in RG_ON_STEPS:
        try:
            r = dpt(d, des["npar"], V_OVP * frac, des["i_ocp"], lloop, f"{des['dev']}x{des['npar']}_{topo}_{V_OVP*frac:.0f}V",
                    rg=cal["rg_eff"], a=cal["a"], rg_on=cal["rg_eff"] * m)
        except RuntimeError as e:
            print(f"warning: transient for {des['name']} did not converge: {str(e)[:100]}")
            return None, None
        r["cal"], r["mult"] = cal, m
        if r["v_pk"] <= dv.V_PK_FRAC * d["vdss"]:
            return m, r
    return None, r


def optimise_checked(cand_key, selector=None, max_iter=4):
    """optimise, then verify the pick with the transient model; if it needs a larger turn-on resistor, record the
    multiplier (its E_on penalty from the datasheet E-vs-R_G curve enters the loss model) and optimise again"""
    for _ in range(max_iter):
        rows, pick = optimise(cand_key, selector=selector)
        if pick is None:
            return rows, None
        des = pick["des"]
        key = (des["dev"], des["npar"], des["topo"])
        m, r = transient_check(des)
        pick["transient"] = r
        if m is None or m <= des["rg_on_mult"] + 1e-9:
            pick["rg_on_ok"] = m is not None
            return rows, pick
        RG_ON_MULT[key] = m
    return rows, pick


def n_positions(topo):
    return {"2L": 4, "3L": 8, "SPLIT": 8}[topo]


def semi_cost(dev, npar, topo):
    """relative semiconductor cost: die-area proxy (C_iss vs C3M0016120K) x voltage-class premium, per cell (ESTIMATE)"""
    d, r = dv.MOSFETS[dev], dv.MOSFETS[REF_DEV]
    per = d["ciss"] / r["ciss"] * V_PREMIUM[d["vdss"]] * (MODULE_PREMIUM if d.get("half_bridge_module") else 1.0)
    return per * npar * n_positions(topo)


def design_point_for_inductor(topo):
    """(V_A, V_B, P) of the worst ripple x current point used for L sizing and core-loss design (per (half-)cell volts)"""
    if topo == "3L":
        return 1000.0, 750.0, p_limit(1000, 750)
    if topo == "SPLIT":
        return 500.0, 250.0, p_limit(1000, 500) / 2
    return 1000.0, 500.0, p_limit(1000, 500)


def build_design(cand_key, dev, npar, fsw, ripple):
    c = CANDIDATES[cand_key]
    topo = c["topo"]
    nl = 3 if topo == "3L" else 2
    va, vb, p = design_point_for_inductor(topo)
    vmax = va
    k = 16 if nl == 3 else 4
    L_req = vmax / (k * ripple * I_MAX * fsw)
    wd = waveform(va, vb, p, fsw, L_req, nl)
    st = wf_stats(wd)
    trip = i_ocp(ripple)
    ind = design_inductor(L_req, max(st["irms"], I_MAX), wd, trip)
    if ind is None:
        return None
    des = make_design(topo, dev, npar, fsw, ind, name=f"{cand_key}:{dev}x{npar}@{fsw/1e3:.0f}k/r{ripple:.2f}")
    des["i_ocp"] = trip
    des["cand"] = cand_key
    des["rg_on_mult"] = RG_ON_MULT.get((dev, npar, c["topo"]), 1.0)
    des["ripple_spec"] = ripple
    des["L_req"] = L_req
    des["caps"] = size_caps(des)
    return des


def size_caps(des, points=None):
    """port (and flying) capacitors from KEMET C4AQ: ripple current <= 70 % of rating and dV_pp <= 1 % of V (cell alone)"""
    topo = des["topo"]
    points = points or FL_POINTS + [(250, 1000), (1000, 250), (700, 700)]
    port_part = "C4AQCEW6130A3BJ" if topo == "SPLIT" else "C4AQUEW5450A3BJ"     # half-bus 650 V parts / 1300 V parts
    fc_part = "C4AQIEW6100A3BJ"
    worst = {"A": [0.0, 0.0], "B": [0.0, 0.0], "FC": [0.0, 0.0]}           # [I_rms, C needed]
    for va, vb in points:
        for sgn in (1, -1):
            p = sgn * p_limit(va, vb)
            w = cell_losses(des, va, vb, p, waveform_only=True)
            st = wf_stats(w)
            il, ia, ib = sample(w, 2048)
            for leg, ip, v in (("A", ia, w["va"]), ("B", ib, w["vb"])):
                q = np.cumsum(ip - ip.mean()) * w["T"] / len(ip)
                cneed = (q.max() - q.min()) / (0.01 * v)
                worst[leg][0] = max(worst[leg][0], st[f"icap_rms_{leg}"])
                worst[leg][1] = max(worst[leg][1], cneed)
                if des["nlev"] == 3 and w[f"d{leg.lower()}"] < 1:
                    worst["FC"][0] = max(worst["FC"][0], st[f"ifc_rms_{leg}"])
                    worst["FC"][1] = max(worst["FC"][1], st[f"qfc_pp_{leg}"] / (0.05 * v / 2))
    caps = {}
    for key, part in (("A", port_part), ("B", port_part), ("FC", fc_part)):
        if key == "FC" and des["nlev"] != 3:
            continue
        cp = dv.C4AQ[part]
        n = max(1, math.ceil(worst[key][0] / (dv.CAP_IRMS_USE * cp["irms"])), math.ceil(worst[key][1] / cp["C"]))
        caps[key] = {"part": part, "n": n, "C": n * cp["C"], "esr": cp["esr"], "irms_worst": worst[key][0],
                     "irms_rating": n * cp["irms"], "C_need": worst[key][1], "v_rating": cp["vndc"], "vop85": cp["vop85"]}
    return caps


def evaluate(des, points=FL_POINTS, t_air=T_AIR):
    res = [cell_losses(des, va, vb, p_limit(va, vb), t_air=t_air) for va, vb in points]
    return res


def voltage_utilisation(des, sim=None):
    """continuous and peak device voltage vs the rules in pv_devices.  Peak = OVP x share + the larger of the
    datasheet-fall-time L di/dt estimate (turn-off) and the ngspice VDMOS commutation (turn-off of the switching device
    and hard-turn-on ringing of the complementary device) at the hardware trip current."""
    d, topo = des["d"], des["topo"]
    frac = 1.0 if topo == "2L" else 0.5 * (1 + FC_DEV_FRAC)
    lloop = L_LOOP["module"] if d.get("half_bridge_module") else L_LOOP[topo]
    i_pk = des["i_ocp"]
    didt_a = float(dv.didt_off(d, i_pk / des["npar"])) * des["npar"]
    dv_os = max(lloop * didt_a, sim["dv_os"] if sim else 0.0)
    v_dc = V_MAX * frac
    v_pk = V_OVP * frac + dv_os
    # sea-level SEB rate: Wolfspeed Gen3 1200 V curve; other classes scaled by V_DSS (ASSUMPTION, no vendor curve on file)
    v_eq = v_dc * 1200.0 / d["vdss"]
    fit = float(np.exp(np.interp(v_eq, [p[0] for p in dv.COSMIC_GEN3_1200["v_fit_cm2"]],
                                 np.log([p[1] for p in dv.COSMIC_GEN3_1200["v_fit_cm2"]]), left=np.log(1e-3), right=np.log(1e3))))
    return {"v_dc": v_dc, "v_pk": v_pk, "dv_os": dv_os, "didt_analytic": didt_a, "sim": sim, "i_pk": i_pk,
            "dv_os_analytic": lloop * didt_a, "lloop": lloop, "u_dc": v_dc / d["vdss"], "u_pk": v_pk / d["vdss"], "fit_cm2": fit,
            "fit_basis": "vendor curve" if d["vdss"] == 1200 and "Gen3" in d["gen"] else "scaled by V_DSS",
            "ok_dc": v_dc <= dv.V_DC_FRAC * d["vdss"], "ok_pk": v_pk <= dv.V_PK_FRAC * d["vdss"]}


def summarise(des, res):
    fl = [r for r in res]
    corners = [r for r in res if (r["va"], r["vb"]) in CORNERS]
    return {"eta_fl_mean": float(np.mean([r["eff"] for r in fl])), "eta_fl_min": float(min(r["eff"] for r in fl)),
            "eta_corner_min": float(min(r["eff"] for r in corners)), "tj_max": float(max(r["tj_max"] for r in fl)),
            "loss_fl_max": float(max(r["ptot"] for r in fl))}


def bom_index(des):
    """semiconductors + gate-driver channels + inductor mass + film caps + FC/midpoint extras (ESTIMATE)"""
    topo = des["topo"]
    npos = n_positions(topo)
    nind = 2 if topo == "SPLIT" else 1
    ncap = sum(c["n"] * (2 if k == "FC" else 1) for k, c in des["caps"].items()) * (2 if topo == "SPLIT" else 1)  # 2 FCs; SPLIT upper+lower banks
    extra = 2 * W_FC_HW if topo == "3L" else (2 * W_MID_HW if topo == "SPLIT" else 0.0)
    return (semi_cost(des["dev"], des["npar"], topo) + W_DRIVER * npos + W_IND_KG * des["ind"]["mass"] * nind
            + W_CAP * ncap + extra)


def optimise(cand_key, verbose=False, f_ripple_min=None, selector=None):
    c = CANDIDATES[cand_key]
    frm = F_RIPPLE_MIN if f_ripple_min is None else f_ripple_min
    rows = []
    for dev in c["devs"]:
        for npar in c["npar"]:
            for fsw in FSW_SET:
                if fsw * (2 if c["topo"] == "3L" else 1) < frm - 1:
                    continue
                for rp in RIPPLE_SET:
                    des = build_design(cand_key, dev, npar, fsw, rp)
                    if des is None:
                        continue
                    res = evaluate(des)
                    s = summarise(des, res)
                    s.update({"des": des, "cost": semi_cost(dev, npar, c["topo"]), "bom": bom_index(des),
                              "ind_mass": des["ind"]["mass"] * (2 if c["topo"] == "SPLIT" else 1),
                              "feasible": s["tj_max"] <= TJ_MAX})
                    rows.append(s)
    feas = [r for r in rows if r["feasible"]]
    if not feas:
        return rows, None
    if selector is not None:
        return rows, selector(feas)
    ok = [r for r in feas if r["eta_corner_min"] >= ETA_CORNER_REQ]
    if ok:
        pick = min(ok, key=lambda r: (round(r["bom"], 2), -r["eta_fl_mean"]))
        pick["meets_req"] = True
    else:                                          # cannot hold 99 % at the corners: report its best effort
        pick = max(feas, key=lambda r: r["eta_corner_min"])
        pick["meets_req"] = False
    return rows, pick


def envelope(des, step=50.0, loads=(1.0, 0.5, 0.25), t_air=T_AIR):
    """efficiency over the (V_A, V_B) grid at fractions of the local power limit; power A->B (B->A is the mirror)"""
    vs = np.arange(V_MIN, V_MAX + 0.1, step)
    out = []
    for fr in loads:
        for va in vs:
            for vb in vs:
                p = fr * p_limit(va, vb)
                r = cell_losses(des, va, vb, p, t_air=t_air)
                out.append({"load": fr, "va": va, "vb": vb, "p": p, "eff": r["eff"], "ptot": r["ptot"], "tj": r["tj_max"],
                            "mode": r["mode"]})
    return out


def peak_efficiency(des, t_air=T_AIR):
    """search over load fraction and voltage pairs for the highest efficiency"""
    best = None
    for va in np.arange(V_MIN, V_MAX + 1, 50.0):
        for vb in np.arange(V_MIN, V_MAX + 1, 50.0):
            for fr in (0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
                r = cell_losses(des, va, vb, fr * p_limit(va, vb), t_air=t_air)
                if best is None or r["eff"] > best["eff"]:
                    best = {"eff": r["eff"], "va": va, "vb": vb, "p": fr * p_limit(va, vb), "mode": r["mode"]}
    return best


def ratio_sweep(des, va=800.0, t_air=T_AIR):
    out = []
    for vb in np.linspace(600, 1000, 81):
        r = cell_losses(des, va, vb, p_limit(va, vb), t_air=t_air)
        out.append({"vb": vb, "eff": r["eff"], "mode": r["mode"], "tj": r["tj_max"], "ripple": r["st"]["ripple"]})
    return out


def self_check():
    # 1. iGSE reproduces the catalog Steinmetz law for a sinusoid
    mat = dv.MATERIALS["KoolMuMAX-26"]
    a, b, c, _ = mat["loss"][0]
    k, ki = igse_k(a, b, c)
    f, bpk = 25e3, 0.08
    tt = np.linspace(0, 1 / f, 2001)
    bb = bpk * np.sin(2 * math.pi * f * tt)
    pv = ki * (2 * bpk) ** (b - c) * np.sum(np.abs(np.diff(bb) / np.diff(tt)) ** c * np.diff(tt)) * f
    assert abs(pv / (k * f ** c * bpk ** b) - 1) < 0.01, "iGSE"
    # 2. volt-second balance, ripple formula, ideal power balance on the 2L and 3L engines
    for nl, va, vb in ((2, 900.0, 500.0), (2, 500.0, 900.0), (3, 900.0, 500.0), (3, 500.0, 900.0), (2, 800, 800), (3, 800, 805)):
        w = waveform(va, vb, 20e3, 25e3, 300e-6, nl)
        st = wf_stats(w)
        assert abs(w["vs_err"]) < 1e-9 * max(st["ripple"], 1), "volt-second"
        assert abs(st["pA"] - st["pB"]) < 1e-6 * 20e3, "ideal power balance"
        if nl == 2 and va != vb:
            vh, vl = max(va, vb), min(va, vb)
            assert abs(st["ripple"] - (vh - vl) * vl / vh / (300e-6 * 25e3)) < 1e-6, "2L ripple formula"
        if nl == 3:
            assert abs(st["ifc_avg_A"]) < 1e-6 and abs(st["ifc_avg_B"]) < 1e-6, "flying cap charge balance"
    # 3. mirror symmetry of the bidirectional cell: losses(V_A, V_B, +P) == losses(V_B, V_A, -P)
    des = build_design("A2k", "IMYH200R012M1H", 1, 20e3, 0.45)
    r1 = cell_losses(des, 900, 600, 20e3)
    r2 = cell_losses(des, 600, 900, -20e3)
    assert abs(r1["ptot"] / r2["ptot"] - 1) < 1e-6, ("mirror symmetry", r1["ptot"], r2["ptot"])
    # 4. loss bookkeeping: total = sum of parts, eff = Pout/(Pout+loss), semis = sum of per-device losses
    assert abs(sum(r1["loss"].values()) - r1["ptot"]) < 1e-9
    assert abs(r1["loss"]["cond"] + r1["loss"]["sw"] + r1["loss"]["dead"] - sum(r1["pdev"].values())) < 1e-6
    assert abs(r1["eff"] - 20e3 / (20e3 + r1["ptot"])) < 1e-12
    return True


# ================================================================================================ outputs
COMPLEXITY = {   # qualitative control/hardware complexity, fixed by topology (engineering judgement, not computed)
    "2L": "1 inductor-current loop + buck/boost/band mode logic; 4 gate drives; V_A, V_B, i_L sensing",
    "3L": "as 2L plus 2 flying-cap voltage loops (2 isolated V sensors), FC precharge before switching, "
          "phase-shifted PWM on 8 channels, FC must be held while a leg is static",
    "SPLIT": "2 current loops + midpoint (power-split) balance loop, 2 inductors, midpoint V sensing on both ports; "
             "ports share NO rail (A- != B- when V_A != V_B) - PV/battery must be floating/symmetric",
}
VAEQVB = {
    "2L": "band mode: both legs hard-switch at full voltage (switching loss ~2x of buck/boost), inductor ripple ~0",
    "3L": "band mode: 8 switches active, both flying caps must be regulated at once; ripple ~0",
    "SPLIT": "band mode in both half-cells: 4 legs switching at V/2; ripple ~0",
}


def _w(path, rows, cols):
    with open(path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(cols)
        for r in rows:
            wr.writerow([r.get(c, "") if not isinstance(r.get(c), float) else f"{r[c]:.6g}" for c in cols])


def _design_row(cand, r):
    d = r["des"]
    ind = d["ind"]
    return {"cand": cand, "design": d["name"], "device": d["dev"], "npar": d["npar"], "fsw_kHz": d["fsw"] / 1e3,
            "ripple_spec": d["ripple_spec"], "L_uH": ind["L_full"] * 1e6, "ind_part": ind["part"], "n_stack": ind["n_stack"],
            "turns": ind["turns"], "ind_mass_kg_total": r["ind_mass"], "eta_corner_min": r["eta_corner_min"],
            "eta_fl_mean": r["eta_fl_mean"], "eta_fl_min": r["eta_fl_min"], "tj_max_C": r["tj_max"], "semi_index": r["cost"],
            "bom_index": r["bom"], "feasible": r["feasible"]}


def run():
    os.makedirs(OUT, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    assert self_check()
    allrows, picks = {}, {}
    for ck in CANDIDATES:
        allrows[ck], picks[ck] = optimise_checked(ck)
    _w(os.path.join(OUT, "designs.csv"), [_design_row(ck, r) for ck in CANDIDATES for r in allrows[ck]],
       list(_design_row("A2k", allrows["A2k"][0]).keys()))

    # ---- detailed figures for each selected design
    summ = {}
    for ck, r in picks.items():
        des = r["des"]
        res = evaluate(des)
        res60 = evaluate(des, t_air=T_AIR_HOT)
        pk = peak_efficiency(des)
        env = envelope(des, step=50.0, loads=(1.0, 0.5))
        sweep = ratio_sweep(des)
        vu = voltage_utilisation(des, r.get("transient"))
        hs = heatsink_rth(flow_m3h=des["flow"])
        summ[ck] = {"pick": r, "res": res, "res60": res60, "peak": pk, "env": env, "sweep": sweep, "vu": vu, "hs": hs}
        _w(os.path.join(OUT, f"envelope_{ck}.csv"), env, ["load", "va", "vb", "p", "eff", "ptot", "tj", "mode"])

    # Pareto fronts: min BOM index vs corner-min efficiency
    levels = np.arange(0.985, 0.9961, 0.0005)
    fronts = {}
    for ck in CANDIDATES:
        fronts[ck] = []
        for l in levels:
            ok = [r for r in allrows[ck] if r["feasible"] and r["eta_corner_min"] >= l]
            fronts[ck].append(min(r["bom"] for r in ok) if ok else np.nan)
    _w(os.path.join(OUT, "fronts.csv"), [dict({"eta_corner_min": l}, **{ck: fronts[ck][k] for ck in CANDIDATES}) for k, l in enumerate(levels)],
       ["eta_corner_min"] + list(CANDIDATES))

    fig, ax = plt.subplots(figsize=(8, 5))
    for ck in CANDIDATES:
        ax.plot(levels * 100, fronts[ck], marker="o", ms=3, label=CANDIDATES[ck]["label"], ls="--" if ck == "X12" else "-")
    ax.set_xlabel("lowest efficiency at the four full-power-window corners [%]")
    ax.set_ylabel("power-stage BOM index (estimate, 1 = one C3M0016120K)")
    ax.set_title("PVCELL Gate-0: cheapest design reaching each efficiency level (Tj<=150 C, ripple>=20 kHz)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fronts.png"), dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for ck, s in summ.items():
        ax.plot([x["vb"] for x in s["sweep"]], [x["eff"] * 100 for x in s["sweep"]], label=ck)
    ax.axvline(800, color="k", lw=0.5)
    ax.axhline(99.0, color="r", lw=0.5)
    ax.set_xlabel("V_B [V] at V_A = 800 V, P = local limit (27.5 kW)")
    ax.set_ylabel("efficiency [%]")
    ax.set_title("Efficiency through the V_A = V_B transition (selected design per candidate)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "transition_sweep.png"), dpi=130)
    plt.close(fig)

    keys = [k for k in CANDIDATES]
    fig, axs = plt.subplots(2, 3, figsize=(13, 8))
    for ax, ck in zip(axs.ravel(), keys):
        e = [x for x in summ[ck]["env"] if x["load"] == 1.0]
        vs = sorted(set(x["va"] for x in e))
        z = np.array([[next(x["eff"] for x in e if x["va"] == va and x["vb"] == vb) for va in vs] for vb in vs]) * 100
        cs = ax.contourf(vs, vs, z, levels=[97.5, 98.5, 98.8, 99.0, 99.1, 99.2, 99.3, 99.4, 99.5, 99.6, 99.7], cmap="viridis")
        ax.contour(vs, vs, z, levels=[99.0], colors="r", linewidths=1.0)
        ax.add_patch(plt.Rectangle((550, 550), 400, 400, fill=False, ec="w", ls="--"))
        ax.set_title(f"{ck}: eta at P_limit (red = 99 %)", fontsize=9)
        ax.set_xlabel("V_A [V]")
        ax.set_ylabel("V_B [V]")
        fig.colorbar(cs, ax=ax, fraction=0.046)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "efficiency_maps.png"), dpi=110)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    parts = ["cond", "sw", "dead", "core", "cu", "cap", "gate", "misc", "aux"]
    pts = [(950, 611), (611, 950), (611, 611)]
    xl, bottom = [], None
    data = []
    for ck in keys:
        for va, vb in pts:
            r = next(x for x in summ[ck]["res"] if x["va"] == va and x["vb"] == vb)
            data.append([r["loss"][p] for p in parts])
            xl.append(f"{ck}\n{va}->{vb}")
    data = np.array(data)
    bottom = np.zeros(len(xl))
    for j, p in enumerate(parts):
        ax.bar(range(len(xl)), data[:, j], bottom=bottom, label=p)
        bottom += data[:, j]
    ax.set_xticks(range(len(xl)))
    ax.set_xticklabels(xl, fontsize=6)
    ax.set_ylabel("loss per cell [W] at 27.5 kW, 45 A")
    ax.legend(fontsize=7, ncol=4)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "loss_breakdown.png"), dpi=130)
    plt.close(fig)

    # ---- summary table + recommendation
    sel = []
    for ck in keys:
        s, r = summ[ck], summ[ck]["pick"]
        des, ind, vu = r["des"], r["des"]["ind"], s["vu"]
        corners = {f"{x['va']}->{x['vb']}": x["eff"] for x in s["res"] if (x["va"], x["vb"]) in CORNERS}
        env1 = [x for x in s["env"] if x["load"] == 1.0]
        sel.append({"cand": ck, "label": CANDIDATES[ck]["label"], "topo": des["topo"], "device": des["dev"], "vdss": des["d"]["vdss"],
                    "npar": des["npar"], "n_devices": n_positions(des["topo"]) * des["npar"], "n_drivers": n_positions(des["topo"]),
                    "fsw_kHz": des["fsw"] / 1e3, "f_ripple_kHz": des["fsw"] / 1e3 * (2 if des["topo"] == "3L" else 1),
                    "L_uH": ind["L_full"] * 1e6, "n_inductors": 2 if des["topo"] == "SPLIT" else 1,
                    "E_L_J": 0.5 * ind["L_full"] * (I_MAX * (1 + des["ripple_spec"] / 2)) ** 2 * (2 if des["topo"] == "SPLIT" else 1),
                    "ind": f"{ind['part']} x{ind['n_stack']}, {ind['turns']} t", "ind_mass": r["ind_mass"],
                    "caps": ", ".join(f"{k}:{v['n']}x{v['part']}" + (" per leg" if k == "FC" else "") for k, v in des["caps"].items())
                            + (" (x2: upper+lower)" if des["topo"] == "SPLIT" else ""),
                    "cap_C_uF": {k: v["C"] * 1e6 for k, v in des["caps"].items()}, "cap_banks": des["caps"],
                    "eta_corner_min": r["eta_corner_min"], "corners": corners, "eta_fl_mean": r["eta_fl_mean"],
                    "eta_fl_min": r["eta_fl_min"], "eta_env_min_fullload": min(x["eff"] for x in env1),
                    "eta_peak": s["peak"]["eff"], "peak_at": s["peak"], "tj45": r["tj_max"],
                    "tj60": max(x["tj_max"] for x in s["res60"]), "semi": r["cost"], "bom": r["bom"], "meets": r["meets_req"],
                    "u_dc": vu["u_dc"], "u_pk": vu["u_pk"], "v_pk": vu["v_pk"], "dv_os": vu["dv_os"], "fit": vu["fit_cm2"],
                    "i_ocp": des["i_ocp"], "didt_a": vu["didt_analytic"], "didt_sim": vu["sim"]["didt"] if vu["sim"] else float("nan"),
                    "os_off": vu["sim"]["dv_os_off"] if vu["sim"] else float("nan"), "os_on": vu["sim"]["dv_os_on"] if vu["sim"] else float("nan"),
                    "rg_on_mult": des["rg_on_mult"],
                    "dpt_deck": vu["sim"]["deck"] if vu["sim"] else "no convergence",
                    "rg_cal": (f"{vu['sim']['cal']['rg_eff']:.2f} (ds {des['d']['rg_ext']}), E_off {vu['sim']['cal']['e_off_sim']*1e6:.0f}/"
                               f"{vu['sim']['cal']['e_off_ds']*1e6:.0f} uJ") if vu["sim"] else "-",
                    "fit_basis": vu["fit_basis"], "ok_v": vu["ok_dc"] and vu["ok_pk"],
                    "dip": min(x["eff"] for x in s["sweep"]) , "dip_ref": max(x["eff"] for x in s["sweep"])})
    _w(os.path.join(OUT, "selected.csv"), sel, ["cand", "label", "device", "npar", "n_devices", "n_drivers", "fsw_kHz", "f_ripple_kHz",
                                                 "L_uH", "n_inductors", "E_L_J", "ind", "ind_mass", "caps", "eta_corner_min", "eta_fl_mean",
                                                 "eta_peak", "tj45", "tj60", "semi", "bom", "u_dc", "u_pk", "fit", "meets", "ok_v"])
    lv = [k for k, l in enumerate(levels) if 0.9899 < l < 0.9931]
    for x in sel:
        f = np.array([fronts[x["cand"]][k] for k in lv])
        fin = f[~np.isnan(f)]
        x["front_score"] = float(np.mean(np.where(np.isnan(f), 1.5 * (fin.max() if fin.size else 99.0), f)))
        x["eta_max_corner"] = max([l for k, l in enumerate(levels) if not np.isnan(fronts[x["cand"]][k])], default=float("nan"))
    valid = [x for x in sel if x["ok_v"] and x["meets"] and x["tj45"] <= TJ_MAX and x["eta_peak"] >= 0.99]
    ranked = sorted(valid, key=lambda x: x["front_score"])
    rec, runner = ranked[0], next(x for x in ranked if x["topo"] != ranked[0]["topo"])
    json.dump({"recommended": rec["cand"], "topology": rec["topo"], "runner_up": runner["cand"],
               "rg_on_mult": [[k[0], k[1], k[2], v] for k, v in RG_ON_MULT.items()],
               "selected": {x["cand"]: {k: v for k, v in x.items() if k not in ("peak_at", "cap_banks")} for x in sel}},
              open(os.path.join(OUT, "selection.json"), "w"), indent=1, default=float)
    write_report(sel, rec, runner, summ, fronts, levels)
    return sel, rec, runner


def write_report(sel, rec, runner, summ, fronts, levels):
    L = []
    a = L.append
    hs = heatsink_rth()
    a("# PVCELL-25/27.5 Gate-0 topology study (PV-18)\n")
    a("Generated by `sim/pv_tradeoff.py` - every number below is written by the script. **Calculated, not measured.**\n")
    a(f"**Recommendation: {CANDIDATES[rec['cand']]['label'].strip()}** (topology A, two-level four-switch buck-boost). "
      f"Runner-up: {CANDIDATES[runner['cand']]['label'].strip()}.\n")
    a("## 1. Basis\n")
    a(f"- Cell: {P_RATED/1e3:.0f} kW rated / {P_MAX/1e3:.1f} kW max, {I_MAX:.0f} A on the lower-voltage port, both ports "
      f"{V_MIN:.0f}-{V_MAX:.0f} V, full power {V_FL[0]:.0f}-{V_FL[1]:.0f} V, P_lim = min({P_MAX/1e3:.1f} kW, {I_MAX:.0f} A x min(V_A,V_B)) (PV-22).")
    a(f"- Bidirectional: the FSBB is mirror-symmetric, losses(V_A,V_B,+P) = losses(V_B,V_A,-P) (asserted in the self-check), so all maps are A->B.")
    a(f"- Full-load evaluation points (V_A->V_B): {', '.join(f'{p[0]}->{p[1]}' for p in FL_POINTS)}; corners = {', '.join(f'{p[0]}->{p[1]}' for p in CORNERS)}.")
    a(f"- Modulation: buck or boost with one static leg; both legs switch only in the band V_B/V_A in (D_max, 1/D_max), "
      f"D_max = 1 - {T_MIN_PULSE*1e6:.1f} us x f_sw; centre-aligned PWM; 3-level legs phase-shifted 180 deg (FC at V/2).")
    a(f"- Design space per candidate: f_sw {', '.join(f'{f/1e3:g}' for f in FSW_SET)} kHz, peak-peak ripple "
      f"{', '.join(f'{r:.2f}' for r in RIPPLE_SET)} x 45 A at the worst-ripple point, devices and parallel count as listed. "
      f"Inductor ripple frequency >= {F_RIPPLE_MIN/1e3:.0f} kHz (audible-noise assumption). For every combination a catalog inductor "
      f"and C4AQ capacitor banks are designed automatically.")
    a(f"- Selection inside a candidate: the lowest power-stage BOM index whose efficiency is >= {ETA_CORNER_REQ*100:.1f} % at every "
      f"full-power-window corner and Tj <= {TJ_MAX:.0f} C at {T_AIR:.0f} C inlet. Candidate ranking: among candidates that pass the voltage "
      f"rules and reach >= 99 % peak, the lowest mean BOM index over the corner-efficiency levels 99.0-99.3 % (front average, unreachable "
      f"level = 1.5 x the candidate's dearest design), so the ranking does not hinge on one point of the front.\n")
    a("## 2. Selected design per candidate\n")
    a("| | " + " | ".join(x["cand"] for x in sel) + " |")
    a("|---|" + "---|" * len(sel))
    rows = [
        ("topology", lambda x: x["label"]),
        ("device (V_DSS)", lambda x: f"{x['device']} ({x['vdss']} V)"),
        ("devices / gate drivers per cell", lambda x: f"{x['n_devices']} / {x['n_drivers']}"),
        ("f_sw / ripple freq [kHz]", lambda x: f"{x['fsw_kHz']:.1f} / {x['f_ripple_kHz']:.1f}"),
        ("L at 45 A [uH] x inductors", lambda x: f"{x['L_uH']:.0f} x{x['n_inductors']}"),
        ("inductor energy 0.5 L Ipk^2 [J] (all)", lambda x: f"{x['E_L_J']:.2f}"),
        ("inductor (Magnetics catalog)", lambda x: x["ind"]),
        ("inductor mass [kg] (all)", lambda x: f"{x['ind_mass']:.1f}"),
        ("film caps per cell (C4AQ)", lambda x: x["caps"]),
        ("cap banks: C [uF] / worst ripple [A rms] / rating used [A rms]", lambda x: "; ".join(
            f"{k} {v['C']*1e6:.0f}/{v['irms_worst']:.1f}/{dv.CAP_IRMS_USE*v['irms_rating']:.1f}" for k, v in x["cap_banks"].items())),
        ("eta min at corners [%]", lambda x: f"{x['eta_corner_min']*100:.2f}"),
        ("eta mean full-load points [%]", lambda x: f"{x['eta_fl_mean']*100:.2f}"),
        ("eta min over whole envelope at P_lim [%]", lambda x: f"{x['eta_env_min_fullload']*100:.2f}"),
        ("eta peak [%] (at)", lambda x: f"{x['eta_peak']*100:.2f} ({x['peak_at']['va']:.0f}->{x['peak_at']['vb']:.0f} V, {x['peak_at']['p']/1e3:.1f} kW)"),
        ("eta dip at V_A=V_B=800 V (sweep min/max) [%]", lambda x: f"{x['dip']*100:.2f} / {x['dip_ref']*100:.2f}"),
        ("worst Tj at 45 / 60 C inlet, full load [C]", lambda x: f"{x['tj45']:.0f} / {x['tj60']:.0f}"),
        ("DC utilisation V_dc/V_DSS (rule <= 0.67)", lambda x: f"{x['u_dc']:.2f}"),
        ("hardware current trip [A]", lambda x: f"{x['i_ocp']:.1f}"),
        ("turn-off di/dt at trip: datasheet t_f / ngspice [A/ns]", lambda x: f"{x['didt_a']/1e9:.1f} / {x['didt_sim']/1e9:.1f}"),
        ("ngspice model: R_G,eff fitted to E_off [ohm]", lambda x: x["rg_cal"]),
        ("ngspice overshoot: turn-off / complementary at turn-on [V]", lambda x: f"{x['os_off']:.0f} / {x['os_on']:.0f}"),
        ("R_G,on / R_G,off needed for the peak rule (E_on penalty in losses)", lambda x: f"{x['rg_on_mult']:.1f}"),
        ("peak incl. L di/dt at trip & OVP (rule <= 0.85)", lambda x: f"{x['u_pk']:.2f} ({x['v_pk']:.0f} V, +{x['dv_os']:.0f} V)"),
        ("sea-level SEB rate [FIT/cm2] (basis)", lambda x: f"{x['fit']:.3g} ({x['fit_basis']})"),
        ("semiconductor cost index (est.)", lambda x: f"{x['semi']:.1f}"),
        ("power-stage BOM index (est.)", lambda x: f"{x['bom']:.1f}"),
        ("passes voltage rules / 99 % corners", lambda x: f"{'yes' if x['ok_v'] else 'NO'} / {'yes' if x['meets'] else 'NO'}"),
    ]
    for name, fn in rows:
        a(f"| {name} | " + " | ".join(fn(x) for x in sel) + " |")
    a("")
    a("Full-power corner efficiencies [%]:\n")
    a("| corner | " + " | ".join(x["cand"] for x in sel) + " |")
    a("|---|" + "---|" * len(sel))
    for c in CORNERS:
        key = f"{c[0]}->{c[1]}"
        a(f"| {key} | " + " | ".join(f"{x['corners'][key]*100:.2f}" for x in sel) + " |")
    a("")
    a("## 3. Cheapest design per efficiency level (BOM index; '-' = not reachable)\n")
    show = [0.988, 0.990, 0.991, 0.992, 0.993, 0.994, 0.995]
    a("| corner-min eta | " + " | ".join(CANDIDATES.keys()) + " |")
    a("|---|" + "---|" * len(CANDIDATES))
    for l in show:
        k = int(round((l - levels[0]) / 0.0005))
        a(f"| >= {l*100:.1f} % | " + " | ".join("-" if np.isnan(fronts[ck][k]) else f"{fronts[ck][k]:.1f}" for ck in CANDIDATES) + " |")
    a("\n![fronts](fronts.png)\n")
    a("## 4. Qualitative comparison\n")
    a("| topology | control / hardware complexity | behaviour at V_A ~= V_B |")
    a("|---|---|---|")
    for t, lab in (("2L", "A"), ("3L", "B"), ("SPLIT", "C")):
        a(f"| {lab} | {COMPLEXITY[t]} | {VAEQVB[t]} |")
    a("\n![sweep](transition_sweep.png)\n\n![maps](efficiency_maps.png)\n\n![losses](loss_breakdown.png)\n")
    a("## 5. Why this recommendation\n")
    def facts(x):
        return (f"{x['n_devices']} x {x['device']} ({x['vdss']} V), {x['n_drivers']} gate drivers, {x['n_inductors']} inductor(s) "
                f"{x['ind_mass']:.1f} kg, BOM index {x['bom']:.1f} at the selection point, front average {x['front_score']:.1f}, "
                f"best reachable corner efficiency {x['eta_max_corner']*100:.2f} %")
    a(f"1. **Recommended {rec['cand']}** ({rec['label'].strip()}): {rec['eta_corner_min']*100:.2f} % at the worst full-power corner, "
      f"{rec['eta_peak']*100:.2f} % peak; {facts(rec)}. Lowest cost at every efficiency level up to its limit; common negative rail "
      f"between the ports; no balancing loop ({COMPLEXITY[rec['topo']]}).")
    a(f"2. **Runner-up {runner['cand']}** ({runner['label'].strip()}): {runner['eta_corner_min']*100:.2f} % worst corner, "
      f"{runner['eta_peak']*100:.2f} % peak; {facts(runner)}. Lost on cost and complexity: {COMPLEXITY[runner['topo']]}.")
    k = 3
    for x in sorted(sel, key=lambda y: y["front_score"]):
        if x["cand"] in (rec["cand"], runner["cand"]):
            continue
        why = ("Excluded by the voltage rule: " + f"{x['u_dc']:.2f} x V_DSS continuous ({x['fit']:.0f} FIT/cm2 on the vendor curve) and "
               f"{x['u_pk']:.2f} x V_DSS peak" if not x["ok_v"] else COMPLEXITY[x["topo"]])
        a(f"{k}. **{x['cand']}** ({x['label'].strip()}): {facts(x)}. {why}.")
        k += 1
    a("")
    a("## 6. Rules, assumptions and what most affects the conclusion\n")
    a(f"- **Voltage rule** (applied to every device): continuous DC blocking <= {dv.V_DC_FRAC:.2f} x V_DSS, peak (OVP {V_OVP:.0f} V + L_loop di/dt at "
      f"the hardware current trip, {OCP_MARGIN} x the largest normal peak) <= {dv.V_PK_FRAC:.2f} x V_DSS; L di/dt is the larger of "
      f"the datasheet-fall-time estimate and the ngspice VDMOS commutation (decks sim/spice/pv_dpt_*.cir); each VDMOS model has Q_gd "
      f"fitted to the datasheet and its effective gate resistance fitted so that the simulated E_off at the datasheet test point equals the "
      f"datasheet value. Source of the 0.67: {dv.COSMIC_GEN3_1200['src']} (Gen3 1200 V: ~5 FIT/cm2 at 800 V, "
      f"150 FIT/cm2 at 1000 V). No 1700/2000 V FIT curve is on file: the rule is scaled by V_DSS - **ask Microchip/Infineon for FIT vs V before Gate 0 closes.**")
    a(f"- Loop inductance (estimates): 2L/C discrete {L_LOOP['2L']*1e9:.0f} nH, 3L outer loop {L_LOOP['3L']*1e9:.0f} nH, FM3 {L_LOOP['module']*1e9:.1f} nH, "
      f"each with {R_DAMP:.0f} ohm in parallel (skin-effect/ESR damping of the 50-100 MHz ringing; with only 5 mOhm series damping the "
      f"ringing never decays and the complementary-device peak doubles - the damping value is the least certain transient input).")
    a(f"- Transient check per selected design (sim/spice/pv_dpt_*.cir): turn-off of the switching device AND ringing of the complementary "
      f"device while the switch turns on hard, at OVP x share and the hardware trip current. Where the peak rule fails, the smallest turn-on "
      f"resistor multiplier from {RG_ON_STEPS} that passes is applied and its E_on penalty (datasheet E-vs-R_G curve) is put into the "
      f"losses before re-optimising (multipliers used: {dict((f'{k[0]}x{k[1]} {k[2]}', v) for k, v in RG_ON_MULT.items()) or 'none'}).")
    a("- Source quality: the cosmic-ray curve is read from a Wolfspeed marketing paper (an independent audit re-read our points within 21 %); "
      "Infineon's tabulated E_oss is ~27 % below the integral of its own C_oss curve (using the table under-subtracts E_oss, i.e. errs high on "
      "loss); the IMYH200R024M1H E_off table (p5) is 30 % below its own plots (p10) - the plots are used.")
    a(f"- Switching energies: datasheet double-pulse curves scaled E ~ (V/V_ref)^kv x (1+kt(Tj-T_ref)); E_rr from vendor E_fr/E_RR or "
      f"{dv.K_RR} x Q_rr x V; per hard edge E_on+E_oss (switch) and E_rr-E_oss (diode), per hard turn-off E_off-E_oss, residual-voltage loss "
      f"for incomplete ZVS; dead time {T_DEAD*1e9:.0f} ns on the body diode. Model accuracy is not validated: treat +-0.1 %-point as the band.")
    a(f"- Heatsink (estimate): one plate-fin extrusion per cell, 150 x 300 mm base, 30 fins x 60 mm, {150:.0f} m3/h -> sink-to-inlet "
      f"{hs[0]+hs[1]:.3f} K/W (effectiveness-NTU; mean air rise {hs[1]:.3f} K/W), {hs[2]:.1f} m/s; the module-level fan operating point is in "
      f"sim/out/pv_design/module_report.md; insulated TO-247 mount R_cs = {RTH_CS['TO-247-4']} K/W (TO-247-4-PLUS {RTH_CS['TO-247-4-PLUS (PG-TO247-4-PLUS-NT14)']}), "
      f"+{RTH_SPREAD} K/W spreading; datasheet max R_th,jc where given. Tj is not the limiting factor for any candidate at this airflow.")
    a(f"- Inductor: Magnetics Kool Mu MAX / XFlux E-cores (2025 catalog A_L and DC-bias fits); core loss by iGSE with the HIGHER of the catalog E-core fit and the catalog toroid fit / XFlux bulletin 'Shapes' value (the sources disagree by 1.4-2.3x; the E-core fit is the low one), Litz copper at "
      f"{J_CU/1e6:.1f} A/mm2, fill {K_CU}, AC factor {FR_AC} on the ripple, >= {MU_FRAC_MIN*100:.0f} % of L0 and B <= {B_FRAC_MAX:.2f} B_sat at the hardware trip current, "
      f"chosen to minimise loss + {IND_LAMBDA} W/kg x mass, <= {IND_MASS_MAX} kg.")
    a(f"- Cost: die-area proxy (C_iss relative to C3M0016120K) x voltage-class premium {V_PREMIUM} (modules x{MODULE_PREMIUM}); BOM index adds "
      f"{W_DRIVER} per gate-driver channel, {W_IND_KG} per kg of inductor, {W_CAP} per C4AQ capacitor, {W_FC_HW} per flying-cap leg (precharge + "
      f"isolated V sense), {W_MID_HW} per midpoint sense. **All cost weights are estimates**; the ranking A < B < C holds over the whole front (section 3).")
    a17, a2k = next(x for x in sel if x["cand"] == "A17"), next(x for x in sel if x["cand"] == "A2k")
    amax = max(l for k, l in enumerate(levels) if not (np.isnan(fronts["A17"][k]) and np.isnan(fronts["A2k"][k])))
    bwin = [l for k, l in enumerate(levels) if not np.isnan(fronts["B"][k])
            and (np.isnan(fronts["A17"][k]) or fronts["B"][k] < fronts["A17"][k])]
    a(f"- Assumptions that would change the answer: (i) the 1700 V variant runs at {a17['u_dc']:.2f} x V_DSS DC - if vendor FIT data "
      f"force a lower limit, the 2000 V variant ({a2k['u_dc']:.2f}) keeps topology A valid; (ii) allowing an audible (< {F_RIPPLE_MIN/1e3:.0f} kHz) "
      f"ripple makes A cheaper still; (iii) B is cheaper than A only from {min(bwin)*100 if bwin else float('nan'):.2f} % corner efficiency upward "
      f"and A cannot exceed {amax*100:.2f} % - a full-load target above that would require B.")
    a(f"- Efficiency is power-stage efficiency including gate-drive energy, {P_AUX_DRV} W per isolated driver channel and {P_AUX_SENSE} W "
      f"sensing per cell (estimates); fans and the controller are module-level and excluded.")
    a(f"- Not modelled: PCB/busbar losses beyond {R_MISC*1e3:.1f} mOhm, fan and control power, EMI filter, current sharing between parallel dies "
      f"(assumed perfect), FC/midpoint voltage ripple effect on loss, thermal coupling between cells.")
    a("\n## 7. Where 99 % is not reached (P = P_lim, A->B, 50 V grid)\n")
    a("PV-11 is a peak-efficiency target, so these points are reported, not failures of PV-11.\n")
    for x in sel:
        e = [y for y in summ[x["cand"]]["env"] if y["load"] == 1.0]
        bad = [y for y in e if y["eff"] < 0.99]
        if not bad:
            a(f"- {x['cand']}: none")
            continue
        rat = lambda y: max(y["va"], y["vb"]) / min(y["va"], y["vb"])
        vlow = max([min(y["va"], y["vb"]) for y in bad if rat(y) <= 1.3], default=float("nan"))
        rhi = min([rat(y) for y in bad if min(y["va"], y["vb"]) >= V_FL[0]], default=float("nan"))
        inwin = [y for y in bad if V_FL[0] <= y["va"] <= V_FL[1] and V_FL[0] <= y["vb"] <= V_FL[1]]
        worst = min(bad, key=lambda y: y["eff"])
        a(f"- {x['cand']}: {len(bad)}/{len(e)} grid points below 99 %: near V_A=V_B up to min(V) = {vlow:.0f} V (current-limited "
          f"power); with both ports >= {V_FL[0]:.0f} V: " + (f"from conversion ratio {rhi:.2f} up" if rhi == rhi else "none") + f"; {len(inwin)} inside the 550-950 V window; "
          f"worst {worst['eff']*100:.2f} % at {worst['va']:.0f}->{worst['vb']:.0f} V, {worst['p']/1e3:.1f} kW.")
    a("\n## 8. Files\n")
    a("- `designs.csv` every evaluated design; `selected.csv` table above; `fronts.csv`; `envelope_<cand>.csv` efficiency grids (A->B, P_lim and 50 %); `selection.json`.")
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L) + "\n")



# ================================================================================================ Gate-0b (D-031, SRC-1..4)
# Asian devices and a real cost model in USD (gen/data/prices.csv, 2026-10-04).  2-level candidates are evaluated at the
# frozen D-007 point (32 kHz, ripple 0.80, today's inductor: keeping it is the point of staying 2-level); 3-level
# candidates are optimised over f_sw x ripple for the lowest cost that meets the rules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICE_FILE = os.path.join(ROOT, "gen", "data", "prices.csv")
QTY = 1000               # price break used: the largest listed break <= 1000 pcs (a few hundred modules a year; ASSUMPTION)
# ECB reference rates of 2026-10-02 as used by gen/cost.py (units per EUR: USD 1.1225, GBP 0.85033, HKD 8.8084, CNY 7.5259)
FX = {"USD": 1.0, "EUR": 1.1225, "GBP": 1.1225 / 0.85033, "HKD": 1.1225 / 8.8084, "CNY": 1.1225 / 7.5259}
# parts without a public price: (USD, basis) - every one an ESTIMATE
PRICE_EST = {
    "IV2Q17020T4Z": (None, "InventChip: no public price (RFQ) -> Sichain SG2M020170HJ LCSC price x INV_FACTOR"),
    "IV2Q17040T4Z": (None, "InventChip: RFQ -> SG2M040170HJ estimate x INV_FACTOR"),
    "IV3Q12035T4Z": (None, "InventChip: RFQ -> Sichain SG2M035120LJ LCSC price x INV_FACTOR"),
    "SG2M040170HJ": (9.74 * (20.0 / 40.0) ** 1.26, "Sichain: RFQ; SG2M020170HJ 9.74 USD scaled by (R ratio)^1.26, the exponent of the "
                                                   "Sichain 1200 V LCSC prices (13 mOhm 7.31, 27 mOhm 3.46, 33 mOhm 2.26 USD)"),
    "B3M040120Z": (None, "BASiC: RFQ -> Sichain SG2M035120LJ LCSC price (same class)"),
    "C4AQIEW6100A3BJ": (10.87, "flying capacitor: no price; taken = the C4AQUEW5450A3BJ broker price (same can size)"),
    "WND75P16W6": (None, "WeEn: RFQ -> Vishay VS-60EPS16-M3 LCSC price"),
}
INV_FACTOR = 1.0         # InventChip price / Sichain price (sensitivity 1.0 and 1.5, coordinator instruction)
# cost items without a part price (ESTIMATES, basis in the comment)
C_CH_SUPPORT = 1.20      # USD per gate-drive channel: DESAT string 3 x US1M, dividers, RLIM, ~12 MLCC, stretch logic
C_DEV_SUPPORT = 0.85     # USD per paralleled device: ZXTP25040DFH follower (no price; ~0.55) + PMEG4010CEJ 0.30 (LCSC)
C_INSUL = 1.00           # USD per device: AlN pad 1.0 mm + spring clip (no quotation on file)
# inductor on the basis of gen/data/cost_estimates.csv 'L_CELL': Litz 14.4 USD/kg (LME Cu 2026-10-02) + 18 drawing/insulation,
# bobbin/insulation/NTC/terminals 6 + winding labour and test 12, +15 % scrap and margin; core: Magnetics Kool Mu MAX 35 USD/kg
# (that row) or an Asian FeSiAl core (POCO NPC/NPH-L, sim/data/asian_magnetic_materials.csv) at 15 USD/kg - ESTIMATE, no quote:
# about 40 % of the Western price, the usual spread between Chinese and US powder-core catalogue prices (not verified)
C_CORE_KG = {"Magnetics": 35.0, "Asian": 15.0}
C_CU_KG, C_WIND, C_IND_MARGIN = 14.4 + 18.0, 6.0 + 12.0, 1.15
CORE_SOURCE = "Asian"
C_HS = 27.0              # USD per cell heatsink: gen/data/cost_estimates.csv 'heatsink' (4.4 kg Al, LME + extrusion, CNC, anodise)
C_FC_SENSE = 6.0         # USD per 3-level leg: direct flying-capacitor voltage measurement (isolated amplifier + divider)
C_FC_PRE = 4.0           # USD per 3-level leg: flying-capacitor pre-charge / clamp network (REFERENCE-LESSONS.md)
C_DAMP = 2.0             # USD per switching leg: RC damper (heatsink-mounted resistors + 2 kV C0G)
N_CLAMP = 16             # bank reverse-clamp rectifiers per cell (sim/pv_design.py)
CLAMP_COST_PART = "WND75P16W6"   # WeEn (RFQ -> priced as the Vishay VS-60EPS16-M3 on LCSC)
_PRICES = None


def price(mpn):
    """(USD per unit, source text) at the largest break <= QTY, cheapest documented source; PRICE_EST otherwise"""
    global _PRICES
    if _PRICES is None:
        _PRICES = list(csv.DictReader(open(PRICE_FILE)))
    best = None
    for r in _PRICES:
        if r["mpn"].upper().startswith(mpn.upper()) and r["unit_price"]:
            q = int(float(r["qty"])) if r["qty"] else 1
            if q <= QTY:
                usd = float(r["unit_price"]) * FX[r["currency"]]
                key = (-q, usd)
                if best is None or key < best[0]:
                    best = (key, usd, f"{r['source']} {r['date']}, {r['unit_price']} {r['currency']} @{q}")
    if best:
        return best[1], best[2]
    usd, basis = PRICE_EST[mpn]
    if usd is None:
        ref = {"IV2Q17020T4Z": "SG2M020170HJ", "IV2Q17040T4Z": "SG2M040170HJ", "IV3Q12035T4Z": "SG2M035120LJ",
               "B3M040120Z": "SG2M035120LJ", "WND75P16W6": "VS-60EPS16-M3"}[mpn]
        usd = price(ref)[0] * (INV_FACTOR if mpn.startswith("IV") else 1.0)
    return usd, "ESTIMATE: " + basis


MODELLED = ("MSC035SMA170B4", "L_CELL", "VS-60EPS16-M3", "C4AQUEW5450A3BJ", "C4AQUBU4220A1YJ", "UCC14241", "UCC21710",
            "ZXTP25040DFH", "PMEG4010CEJ", "4.7n 2000V", "CRCW251215R0")   # PVCELL-25 BOM lines this model prices itself
_REST = None


def board_rest_usd():
    """USD per cell of the PVCELL-25 BOM lines the model does not price itself (sensing, bleeders, logic, connectors,
    passives): bom/PV-P75_costed_BOM.csv (gen/cost.py), PVCELL-25 quantity share / 3 cells"""
    global _REST
    if _REST is None:
        import re
        tot = 0.0
        for r in csv.DictReader(open(os.path.join(ROOT, "bom", "PV-P75_costed_BOM.csv"))):
            m = re.search(r"PVCELL-25: (\d+)", r["Used on"])
            if not m or any((r["MPN"] or r["Value"]).upper().startswith(x.upper()) for x in MODELLED):
                continue
            try:
                tot += float(r["unit_cost_usd"]) * int(m.group(1))
            except ValueError:
                continue
        _REST = tot / 3.0
    return _REST


def inductor_cost(ind, core=None):
    """USD for one wound inductor (basis above); the magnetics engineer's estimate when the inductor comes from its file"""
    if ind.get("cost_usd") is not None and core is None:
        return ind["cost_usd"]
    if ind.get("m_core") is None:
        return float("nan")
    return C_IND_MARGIN * (ind["m_core"] * C_CORE_KG[core or CORE_SOURCE] + ind["m_cu"] * C_CU_KG + C_WIND)


def cost_usd(des, dev_factor=1.0, n_clamp=None):
    """power-stage cost of one cell in USD, itemised (devices x dev_factor for the price sensitivity)"""
    topo, n = des["topo"], des["npar"]
    npos = n_positions(topo)
    nlegs = 2
    p_dev = price(des["dev"])[0] * dev_factor
    drv, bias = price("NSI6651")[0], price("UCC14241")[0]
    ind = des["ind"]
    c = {"devices": npos * n * p_dev,
         "gate_drive": npos * (drv + bias + C_CH_SUPPORT) + (npos * n * C_DEV_SUPPORT if n > 1 else 0.0),
         "insulators": npos * n * C_INSUL,
         "inductor": inductor_cost(ind) * (2 if topo == "SPLIT" else 1),
         "port_caps": sum(des["caps"][k]["n"] * price(des["caps"][k]["part"])[0] for k in "AB"),
         "decoupling": nlegs * 3 * price("C4AQUBU4220A1YJ")[0],
         "heatsink": C_HS, "clamp_diodes": (n_clamp or N_CLAMP) * price(CLAMP_COST_PART)[0], "damper": nlegs * C_DAMP,
         "board_rest": board_rest_usd()}
    if topo == "3L":
        fc = des["caps"]["FC"]
        c["flying_caps"] = nlegs * fc["n"] * price(fc["part"])[0]
        c["fc_sense_precharge"] = nlegs * (C_FC_SENSE + C_FC_PRE)
    c["total"] = sum(c.values())
    return c


G0B_2L = {"REF": ("MSC035SMA170B4", 2), "IC20x1": ("IV2Q17020T4Z", 1), "IC20x2": ("IV2Q17020T4Z", 2), "IC40x2": ("IV2Q17040T4Z", 2),
          "SC20x1": ("SG2M020170HJ", 1), "SC20x2": ("SG2M020170HJ", 2), "SC40x2": ("SG2M040170HJ", 2)}
G0B_3L_DEVS = ["SCT4036KRHR", "SG2M035120LJ", "IV3Q12035T4Z", "B3M040120Z", "SG2M014120LJ"]
DECISION_MARGIN = 1.15   # coordinator rule: 2-level stays if its cost <= 1.15 x the best 3-level candidate


def g0b_row(key, des, rules=True):
    res = evaluate(des)
    s = summarise(des, res)
    m, tsim = transient_check(des)
    des["rg_on_mult"] = m or des["rg_on_mult"]
    if m is not None:
        res = evaluate(des)
        s = summarise(des, res)
    vu = voltage_utilisation(des, tsim)
    fr = des["fsw"] * (2 if des["topo"] == "3L" else 1)
    cost = cost_usd(des)
    d = des["d"]
    ok = {"eta": s["eta_corner_min"] >= ETA_CORNER_REQ, "tj": s["tj_max"] <= TJ_MAX, "u_dc": vu["u_dc"] <= dv.V_DC_FRAC,
          "u_pk": m is not None and vu["u_pk"] <= dv.V_PK_FRAC, "f_ripple": fr >= F_RIPPLE_MIN - 1}
    return {"key": key, "topo": des["topo"], "dev": des["dev"], "mfr": d["mfr"], "npar": des["npar"], "fsw": des["fsw"],
            "ripple": des["ripple_spec"], "eta_corner_min": s["eta_corner_min"], "eta_fl_mean": s["eta_fl_mean"], "tj_max": s["tj_max"],
            "u_dc": vu["u_dc"], "u_pk": vu["u_pk"], "rg_on_mult": des["rg_on_mult"], "f_ripple": fr, "channels": n_positions(des["topo"]),
            "cost": cost, "ok": ok, "meets": all(ok.values()), "des": des, "ind_mass": des["ind"]["mass"],
            "price_src": price(des["dev"])[1]}


_IND_CACHE = {}


def build_design_cached(cand_key, dev, npar, fsw, ripple):
    """build_design with the inductor reused per (topology, f_sw, ripple): the inductor does not depend on the device"""
    topo = CANDIDATES[cand_key]["topo"]
    k = (topo, fsw, ripple)
    if k in _IND_CACHE:
        orig = design_inductor
        globals()["design_inductor"] = lambda *a, **kw: _IND_CACHE[k]
        try:
            return build_design(cand_key, dev, npar, fsw, ripple)
        finally:
            globals()["design_inductor"] = orig
    des = build_design(cand_key, dev, npar, fsw, ripple)
    if des is not None:
        _IND_CACHE[k] = des["ind"]
    return des


def cheapest(cand, dev, n, key, max_iter=3):
    """lowest-cost f_sw x ripple point meeting Tj and the 99 % corners, verified by the commutation check (re-optimised
    with the R_G,on multiplier the check asks for, as optimise_checked does)"""
    topo = CANDIDATES[cand]["topo"]
    for _ in range(max_iter):
        best = None
        for fsw in FSW_SET:
            if fsw * (2 if topo == "3L" else 1) < F_RIPPLE_MIN - 1:
                continue
            for rp in RIPPLE_SET:
                des = build_design_cached(cand, dev, n, fsw, rp)
                if des is None:
                    continue
                s_ = summarise(des, evaluate(des))
                if s_["tj_max"] > TJ_MAX or s_["eta_corner_min"] < ETA_CORNER_REQ:
                    continue
                c = cost_usd(des)["total"]
                if best is None or c < best[0]:
                    best = (c, des)
        if best is None:
            return None
        des = best[1]
        m, _ = transient_check(des)
        k = (dev, n, topo)
        if m is None or m <= des["rg_on_mult"] + 1e-9:
            return g0b_row(key, des)
        RG_ON_MULT[k] = m
    return g0b_row(key, best[1])


def run_g0b():
    """Gate-0b: ranking by USD cost among the candidates that meet every rule; decision by DECISION_MARGIN"""
    global INV_FACTOR
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for key, (dev, n) in G0B_2L.items():          # 2-level at the frozen D-007 point
        des = build_design_cached("A17", dev, n, 32e3, 0.80)
        rows.append(g0b_row(key, des))
    for key, (dev, n) in G0B_2L.items():          # 2-level, cost-optimal f_sw x ripple (cost as a selection criterion)
        if key != "REF":
            r = cheapest("A17", dev, n, f"{key}-opt")
            if r:
                rows.append(r)
    for dev in [x for x in G0B_3L_DEVS if x in dv.MOSFETS]:   # 3-level: cheapest feasible (Tj, 99 %) point over f_sw x ripple
        for n in (1, 2):
            r = cheapest("B", dev, n, f"3L-{dev}x{n}")
            if r is None:
                des = build_design_cached("B", dev, n, 32e3, 0.80)
                if des is not None:
                    r = g0b_row(f"3L-{dev}x{n}", des)
                    r["note"] = "no f_sw/ripple point meets Tj and 99 %: shown at 32 kHz / 0.80"
            if r:
                rows.append(r)
    # sensitivity: device price +-30 %, InventChip at 1.0 / 1.5 x the Sichain price
    for r in rows:
        r["cost_dev_m30"] = cost_usd(r["des"], 0.7)["total"]
        r["cost_dev_p30"] = cost_usd(r["des"], 1.3)["total"]
        INV_FACTOR = 1.5
        r["cost_inv15"] = cost_usd(r["des"])["total"]
        INV_FACTOR = 1.0
    ok2 = [r for r in rows if r["topo"] == "2L" and r["meets"] and r["key"] != "REF"]
    ok3 = [r for r in rows if r["topo"] == "3L" and r["meets"]]
    b2 = min(ok2, key=lambda r: r["cost"]["total"]) if ok2 else None
    b3 = min(ok3, key=lambda r: r["cost"]["total"]) if ok3 else None
    stay = b2 is not None and (b3 is None or b2["cost"]["total"] <= DECISION_MARGIN * b3["cost"]["total"])
    robust = {}
    for lab, k2, k3 in (("device -30 %", "cost_dev_m30", "cost_dev_m30"), ("device +30 %", "cost_dev_p30", "cost_dev_p30"),
                        ("InventChip 1.5 x Sichain", "cost_inv15", "cost_inv15")):
        c2 = min((r[k2] for r in ok2), default=None)
        c3 = min((r[k3] for r in ok3), default=None)
        robust[lab] = None if c2 is None else (c3 is None or c2 <= DECISION_MARGIN * c3)
    out = {"rows": rows, "best_2L": b2, "best_3L": b3, "stay_2L": stay, "robust": robust}
    write_g0b(out)
    return out


def write_g0b(o):
    cols = ["key", "topo", "mfr", "dev", "npar", "fsw_kHz", "ripple", "channels", "cost_usd", "cost_dev_m30", "cost_dev_p30", "cost_inv15",
            "eta_corner_min_pct", "tj_max_C", "u_dc", "u_pk", "rg_on_mult", "f_ripple_kHz", "ind_mass_kg", "meets", "fails", "price_src"]
    rows = []
    for r in o["rows"]:
        rows.append({"key": r["key"], "topo": r["topo"], "mfr": r["mfr"], "dev": r["dev"], "npar": r["npar"], "fsw_kHz": r["fsw"] / 1e3,
                     "ripple": r["ripple"], "channels": r["channels"], "cost_usd": r["cost"]["total"], "cost_dev_m30": r["cost_dev_m30"],
                     "cost_dev_p30": r["cost_dev_p30"], "cost_inv15": r["cost_inv15"], "eta_corner_min_pct": 100 * r["eta_corner_min"],
                     "tj_max_C": r["tj_max"], "u_dc": r["u_dc"], "u_pk": r["u_pk"], "rg_on_mult": r["rg_on_mult"],
                     "f_ripple_kHz": r["f_ripple"] / 1e3, "ind_mass_kg": r["ind_mass"], "meets": r["meets"],
                     "fails": " ".join(k for k, v in r["ok"].items() if not v), "price_src": r["price_src"]})
    _w(os.path.join(OUT, "gate0b.csv"), rows, cols)
    pick = lambda r: None if r is None else {"key": r["key"], "dev": r["dev"], "npar": r["npar"], "fsw": r["fsw"], "ripple": r["ripple"],
                                             "cost_usd": round(r["cost"]["total"], 2), "cost_items": {k: round(v, 2) for k, v in r["cost"].items()}}
    json.dump({"best_2L": pick(o["best_2L"]), "best_3L": pick(o["best_3L"]), "stay_2L": o["stay_2L"], "robust": o["robust"],
               "rule": f"2-level stays if it meets every rule and costs <= {DECISION_MARGIN} x the best 3-level candidate"},
              open(os.path.join(OUT, "gate0b.json"), "w"), indent=1)
    L = ["# PVCELL Gate-0b: Asian devices and USD cost (D-031)\n",
         "Generated by `sim/pv_tradeoff.py run_g0b()`. Calculated, not measured. Prices: `gen/data/prices.csv` (largest break "
         f"<= {QTY} pcs, cheapest documented source); parts without a price carry an ESTIMATE with its basis (PRICE_EST). "
         "2-level at the frozen D-007 point (32 kHz, ripple 0.80, today's inductor); 3-level optimised over f_sw x ripple.\n",
         f"Rules: lowest full-power corner efficiency >= {ETA_CORNER_REQ*100:.1f} %, Tj <= {TJ_MAX:.0f} C at {T_AIR:.0f} C inlet, "
         f"u_dc <= {dv.V_DC_FRAC}, u_pk <= {dv.V_PK_FRAC} (calibrated VDMOS commutation at OVP and the trip current), ripple "
         f">= {F_RIPPLE_MIN/1e3:.0f} kHz. Cost = power stage per cell (devices, gate-drive channels, insulators, inductor, film caps, "
         "heatsink, bank clamps, dampers; for 3-level also flying caps, their measurement and pre-charge).\n",
         "| cand | devices per position | f_sw | channels | cost USD/cell | -30 % / +30 % dev | InventChip 1.5x | corner eta | Tj max | u_dc / u_pk | meets |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(o["rows"], key=lambda r: (not r["meets"], r["cost"]["total"])):
        L.append(f"| {r['key']} | {r['npar']} x {r['dev']} ({r['mfr']}) | {r['fsw']/1e3:.0f} kHz | {r['channels']} | "
                 f"**{r['cost']['total']:.0f}** | {r['cost_dev_m30']:.0f} / {r['cost_dev_p30']:.0f} | {r['cost_inv15']:.0f} | "
                 f"{r['eta_corner_min']*100:.2f} % | {r['tj_max']:.0f} C | {r['u_dc']:.2f} / {r['u_pk']:.2f} | "
                 + ("yes" if r["meets"] else "no: " + " ".join(k for k, v in r["ok"].items() if not v)) + " |")
    b2, b3 = o["best_2L"], o["best_3L"]
    L.append("")
    for lab, r in (("best 2-level", b2), ("best 3-level", b3)):
        if r:
            L.append(f"- {lab}: {r['key']} - " + ", ".join(f"{k} {v:.1f}" for k, v in r["cost"].items()) + " USD")
    L.append(f"\n**Decision (coordinator rule, 2-level if <= {DECISION_MARGIN} x the best 3-level): "
             + ("STAY 2-LEVEL" if o["stay_2L"] else "3-LEVEL / STOP") + f"**; robust to: " +
             ", ".join(f"{k}: {'same' if v == o['stay_2L'] else 'CHANGES'}" for k, v in o["robust"].items()))
    L.append("\nPrice sources and estimates:\n")
    for mpn in sorted({r["dev"] for r in o["rows"]} | {"NSI6651", "UCC14241", "C4AQUBU4220A1YJ", "C4AQUEW5450A3BJ", "VS-60EPS16-M3"}):
        u, src = price(mpn)
        L.append(f"- {mpn}: {u:.2f} USD - {src}")
    L.append(f"- per-channel support {C_CH_SUPPORT}, per paralleled device {C_DEV_SUPPORT}, insulator {C_INSUL}, core {C_CORE_KG}/kg, "
             f"Litz {C_CU_KG}/kg, winding {C_WIND}, heatsink {C_HS}, FC sense {C_FC_SENSE} + pre-charge {C_FC_PRE} per leg, damper "
             f"{C_DAMP} per leg (USD): ESTIMATES without quotations. Exchange rates {FX} ESTIMATE.")
    open(os.path.join(OUT, "gate0b.md"), "w").write("\n".join(L) + "\n")

if __name__ == "__main__":
    if "g0b" in sys.argv:
        o = run_g0b()
        for r in sorted(o["rows"], key=lambda r: (not r["meets"], r["cost"]["total"])):
            print(f"{r['key']:22s} {r['cost']['total']:7.1f} USD  eta {r['eta_corner_min']*100:.2f}  Tj {r['tj_max']:.0f}  "
                  f"u_pk {r['u_pk']:.2f}  meets {r['meets']}")
        print("stay 2-level:", o["stay_2L"], o["robust"])
        print("pv_tradeoff Gate-0b done")
        sys.exit(0)
    sel, rec, runner = run()
    print(f"recommended: {rec['cand']} ({rec['label']}), runner-up: {runner['cand']}")
    for x in sel:
        print(f"{x['cand']:4s} corner-min {x['eta_corner_min']*100:.2f} %  peak {x['eta_peak']*100:.2f} %  Tj45 {x['tj45']:.0f} C  "
              f"BOM {x['bom']:.1f}  u_dc {x['u_dc']:.2f}  u_pk {x['u_pk']:.2f}")
    print("pv_tradeoff self-check passed")
