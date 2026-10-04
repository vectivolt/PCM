"""PV-P75 (3 cells) and PV-P100/110 (4 cells) at module level: everything inside the enclosure.

Run:  .venv/bin/python sim/pv_module.py      (needs sim/out/pv_tradeoff/selection.json; pv_tradeoff runs first if missing)
Out:  sim/out/pv_design/{module_spec.json, module_report.md, module_*.png, module_*.csv}

Adds to the cell model (sim/pv_design.py): port-board losses read from sim/out/port_design (fuses, contactors, shunts,
busbar loop, bleeders, coil and sensor supply), control and auxiliary consumption, the fans at their real operating
point (heatsink pressure drop vs datasheet P-Q curves), a heatsink-temperature fan law, acoustic estimate, ambient
derating 25-60 C from the component limits, one-fan-failed capacity and altitude (air density on convection and on the
fan operating point).  Calculated, not measured; estimates are labelled where they are defined.
"""
import json
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pv_devices as dv  # noqa: E402
import pv_tradeoff as tr  # noqa: E402
import pv_design as pdz  # noqa: E402

OUT = pdz.OUT
PORT_SPEC = os.path.join(ROOT, "sim", "out", "port_design", "port_spec.json")
PORT_REPORT = os.path.join(ROOT, "sim", "out", "port_design", "report.md")
CTRL_GEN = os.path.join(ROOT, "gen", "ctrl_c2000.py")
MODULES = {3: "PV-P75", 4: "PV-P100/110"}
CP_AIR = 1005.0

# ------------------------------------------------------------------------------------------------ estimates (labelled)
ETA_LMR38020 = 0.88      # CTRL 24 V -> 5 V converter efficiency at 1.2 A (ESTIMATE, datasheet curve not read)
ETA_LMR36015 = 0.80      # port sensor 24 V -> 3.3 V supply efficiency at 0.34 A (ESTIMATE)
P_SYS_IO = 3.0           # W, SYS-IO-AUX own consumption (ESTIMATE: 4 isolated comm supplies + transceivers ~1.4 W, 3.3 V logic,
                         # expanders, watchdog, LEDs ~0.8 W, eFuse/DO quiescent and Ethernet LEDs ~0.8 W; no budget in gen/)
R_CMC_WINDING = 0.2e-3   # ohm per common-mode-choke winding (ESTIMATE: custom busbar toroid, no data in port design)
K_REST = 10.0            # loss coefficient of grille + port board + inductor/capacitor field + fan guards on the duct
                         # velocity (ESTIMATE, +-50 %)
DUCT_W_PER_CELL, DUCT_H = 0.140, 0.120   # m: duct cross-section per cell (3 cells = 0.42 m wide in the 444 mm envelope)
A_FAN = math.pi / 4 * (0.115 ** 2 - 0.040 ** 2)   # m2, free annulus of a 120 mm fan
K_LEAK = {"no flap": 4.0, "flap": 200.0}  # dead-fan reverse-flow loss coefficient: stopped rotor + guard / with backflow
                                          # shutter (ESTIMATES)
RHO_DS = 1.20            # kg/m3 at which the fan P-Q curves are given (20-25 C, sea level; ASSUMPTION)
INSTALL_DB = 3.0         # dB(A) added for back-pressure and duct noise in the enclosure (ESTIMATE)
H_IND0 = 25.0            # W/m2K inductor surface coefficient at 150 m3/h, 1.10 kg/m3 (as in pv_tradeoff); scaled ^0.6 with mass flow
T_IND_MAX = 155.0        # C inductor hot spot: class H insulation system (180 C) less 25 K, as the magnetics review asks (MG-07);
                         # the hot spot now includes the internal winding gradient (tr.IND_CORR); was 130 C for class F
DT_CAP_PRE = 3.0         # K preheat of the port-capacitor air by the port board (ESTIMATE)
T_INLET_MIN_REQ = -30.0  # C, PV-20 lowest operating inlet temperature
# fan volume price (coordinator: no single-piece price): RS Components UK lists 9GT1224P1S001 (RS 185-4535) at GBP 107.37 for 1-9
# and GBP 104.14 for 10+ (web search 2026-10-04; ECB 2026-10-02: 1 GBP = 1.3201 USD). No OEM quotation on file.
FAN_VOLUME_PRICE = {}    # none needed: gen/data/prices.csv has DigiKey's 21+ tier (112.92 USD), which tr.price() takes; RS UK 10+ is
                         # GBP 104.14 (= 137.5 USD, web search 2026-10-04) - no OEM quotation on file
NOISE_MAX = 65.0         # dB(A) at 1 m: the Megarevo figure the comparison holds us to (sim/compare_megarevo.py); PWM speed cap
S_MIN, T_ON, T_FULL = 0.30, 40.0, 60.0   # fan law, heatsink NTC: 30 % below 40 C, linear to 100 % at 60 C (our choice)
TI_ON, TI_FULL = 90.0, 115.0             # fan law, inductor NTC: 30 % below 90 C, linear to 100 % at 115 C; speed = max of both
DERATE_PTS = [(611, 611), (950, 611), (611, 950), (1000, 500)]   # highest-loss full-power points (pv_design report)
EVT = [(45, 1.0), (50, 0.8), (55, 0.6), (60, 0.4)]               # roadmap provisional EVT curve (% of rated)


def air_density(alt_m, t_c):
    """ISA pressure at altitude, ideal gas at the inlet temperature"""
    p = 101325.0 * (1 - 2.25577e-5 * alt_m) ** 5.25588
    return p / (287.05 * (t_c + 273.15))


# ------------------------------------------------------------------------------------------------ inputs read from files
def port_model(i_rating):
    """per-port series resistance and fixed losses for the 135 A or 180 A port variant, read from the port design
    outputs (not re-estimated)"""
    ps = json.load(open(PORT_SPEC))
    rep = open(PORT_REPORT).read()
    var = ps["variants"][str(int(i_rating))]
    r_loop_x = float(re.search(r"\| A_R_LOOP_X \| ([0-9.e+-]+) \|", rep).group(1))      # contactor + shunt + busbars
    m_bl = re.search(r"([0-9.]+) k: ([0-9.]+) W at 1000 V", rep)
    r_bleed = float(m_bl.group(1)) * 1e3
    r_fuse = var["fuse_loss_W"] / i_rating ** 2
    coil_w = ps["contactor"]["coil_V"] ** 2 / ps["contactor"]["coil_ohm"]
    sens_w = ps["sensor_supply"]["Vout"] * ps["sensor_supply"]["I_load_max_A"] / ETA_LMR36015
    return {"r_fuse": r_fuse, "r_loop_x": r_loop_x, "r_cmc": R_CMC_WINDING, "r_bleed": r_bleed, "coil_w": coil_w, "sens_w": sens_w,
            "fuse": var["fuse_mpn"], "fuse_in": var["fuse_In_A"], "fuse_derated": var["fuse_derated_A"], "i_rating": i_rating,
            "r_port": 2 * r_fuse + r_loop_x + 2 * R_CMC_WINDING,
            "src": {"fuse": f"port_spec.json variants/{int(i_rating)}: {var['fuse_mpn']} {var['fuse_loss_W']} W per link at {i_rating:.0f} A",
                    "loop": f"port report A_R_LOOP_X {r_loop_x*1e3:.2f} mOhm (contactor max 0.25 + shunt 0.1 + busbars)",
                    "bleeder": f"port report: {r_bleed/1e3:.0f} k per port (bleeder + divider + gate-bias string)",
                    "coil": "port_spec.json contactor coil_V^2/coil_ohm (no economiser in the port design)",
                    "sensor": f"port_spec.json sensor_supply Vout x I_load_max / {ETA_LMR36015} (efficiency ESTIMATE)",
                    "cmc": f"ESTIMATE {R_CMC_WINDING*1e3:.1f} mOhm per winding (custom part, no resistance in the port design)"}}


def ctrl_power():
    """CTRL-C2000 5 V load from the board script's own note, through the 24 V -> 5 V converter (efficiency ESTIMATE)"""
    m = re.search(r"about ([0-9.]+) A at 5 V", open(CTRL_GEN).read())
    i5 = float(m.group(1))
    return 5.0 * i5 / ETA_LMR38020, i5


# ------------------------------------------------------------------------------------------------ fans and airflow
def fan_dp(fan, q_m3h, s, rho):
    """fan pressure at flow q (m3/h) and speed fraction s (affinity laws), scaled with density"""
    pts = dv.FANS[fan]["pq"]
    q0 = q_m3h / max(s, 1e-6)
    if q0 >= pts[-1][0]:
        return -1e3 * (q0 - pts[-1][0] + 1.0)
    return s * s * rho / RHO_DS * float(np.interp(q0, [p[0] for p in pts], [p[1] for p in pts]))


def sys_dp(q_hs, ncell, rho):
    """pressure drop of the heatsink path for total heatsink flow q_hs (m3/h): heatsinks in parallel + lumped rest"""
    v = q_hs / 3600.0 / (ncell * DUCT_W_PER_CELL * DUCT_H)
    return tr.heatsink_dp(q_hs / ncell, rho) + K_REST * 0.5 * rho * v * v


def operating_point(fan, ncell, s, rho, n_fail=0, flap="flap"):
    """total heatsink flow (m3/h) where the working fans' curve meets the system; dead fans leak backwards"""
    nw = ncell - n_fail

    def leak(dp):
        return n_fail * A_FAN * math.sqrt(2 * max(dp, 0.0) / (rho * K_LEAK[flap])) * 3600.0

    def g(q):
        dp = sys_dp(q, ncell, rho)
        return fan_dp(fan, (q + leak(dp)) / nw, s, rho) - dp
    lo, hi = 0.0, ncell * dv.FANS[fan]["q_free"] * 1.5
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if g(mid) > 0:
            lo = mid
        else:
            hi = mid
    q = 0.5 * (lo + hi)
    dp = sys_dp(q, ncell, rho)
    return {"q_hs": q, "dp": dp, "q_leak": leak(dp), "q_fan": (q + leak(dp)) / nw, "resid": g(q)}


def fan_law(t_sink, t_ind=0.0):
    lin = lambda t, t0, t1: min(1.0, max(S_MIN, S_MIN + (1 - S_MIN) * (t - t0) / (t1 - t0)))
    return max(lin(t_sink, T_ON, T_FULL), lin(t_ind, TI_ON, TI_FULL))


def noise_1m(fan, s, n_run):
    f = dv.FANS[fan]           # 50 log10(speed), or the fan's own slope through its two datasheet points
    k = (f["lp_1m"] - f["lp_min"]) / -math.log10(f["s_min"]) if "lp_min" in f else 50.0
    lp = f["lp_1m"] + k * math.log10(max(s, 1e-3)) + INSTALL_DB
    return 10 * math.log10(n_run * 10 ** (lp / 10)) if n_run else 0.0


def noise_cap(fan, n):
    """largest speed fraction at which n fans stay within NOISE_MAX (PWM duty cap), <= 1"""
    lo, hi = dv.FANS[fan].get("s_min", 0.05), 1.0
    if noise_1m(fan, hi, n) <= NOISE_MAX:
        return 1.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if noise_1m(fan, mid, n) <= NOISE_MAX else (lo, mid)
    return lo


def fan_power(fan, s):
    """W per fan: p0 + (P_rated - p0) s^3 through the datasheet points (p0 = 0 when only the rated point is given)"""
    f = dv.FANS[fan]
    p0 = (f["p_min_w"] - f["p_w"] * f["s_min"] ** 3) / (1 - f["s_min"] ** 3) if "p_min_w" in f else 0.0
    return p0 + (f["p_w"] - p0) * s ** 3


# ------------------------------------------------------------------------------------------------ module state
def cap_allowed(v):
    """C4AQ hot-spot temperature allowed at DC voltage v: V_NDC 70 C / V_OP85 / V_OP105 (C4AQ p.6) for the 1300 V part"""
    tab = [(70.0, 1300.0), (85.0, 1100.0), (105.0, 850.0)]
    return float(np.interp(-v, [-x[1] for x in tab], [x[0] for x in tab]))


def module_state(m, va, vb, p, t_in=25.0, alt=0.0, fan=None, speed=None, n_fail=0, flap="flap"):
    """steady state of the module at (va, vb, p_out) with the fan law (speed=None) or a fixed speed fraction"""
    des, n, pm = m["des"], m["n"], m["port"]
    fan = fan or m["fan"]
    rho = air_density(alt, t_in)
    s = 1.0 if speed is None else speed
    for _ in range(40 if speed is None else 1):
        op = operating_point(fan, n, s, rho, n_fail, flap)
        qc = op["q_hs"] / n
        r = tr.cell_losses(dict(des, flow=max(qc, 1.0), rho=rho), va, vb, p / n, t_air=t_in)
        lc = r["loss"]
        p_semi = lc["cond"] + lc["sw"] + lc["dead"]
        dt_air_hs = p_semi / (rho * CP_AIR * max(qc, 1.0) / 3600.0)
        h_ind = H_IND0 * (rho * qc / (1.10 * 150.0)) ** 0.6
        t_ind = t_in + dt_air_hs + tr.ind_hot_rise(lc["core"] + lc["cu"], H_IND0 / max(h_ind, 1.0))   # magnetics review hot spot
        if speed is None:
            s_new = min(max(fan_law(r["t_sink"], t_ind), dv.FANS[fan].get("s_min", 0.0)),   # lowest specified PWM duty
                        m.get("s_cap", 1.0))                                                 # noise cap
            if abs(s_new - s) < 2e-3:
                s = s_new
                break
            s = 0.5 * s + 0.5 * s_new
    cap = des["caps"]["A"]
    i_cap = max(r["st"]["icap_rms_A"], r["st"]["icap_rms_B"]) / cap["n"]
    t_cap = t_in + DT_CAP_PRE + cap["esr"] * i_cap ** 2 * dv.C4AQ[cap["part"]]["rth"]
    p_cells = n * r["ptot"]
    i_b = abs(p) / vb
    i_a = (abs(p) + p_cells) / va
    port = (i_a ** 2 + i_b ** 2) * pm["r_port"] + (va ** 2 + vb ** 2) / pm["r_bleed"] + 2 * pm["coil_w"] + 2 * pm["sens_w"]
    ctrl = m["ctrl_w"] + P_SYS_IO
    n_run = n - n_fail
    fans = n_run * fan_power(fan, s)
    loss = {"cells": p_cells, "port": port, "ctrl_aux": ctrl, "fans": fans}
    for _ in range(50):       # input port current carries the output power plus every loss in the enclosure
        i_a = (abs(p) + sum(loss.values())) / va
        new_port = (i_a ** 2 + i_b ** 2) * pm["r_port"] + (va ** 2 + vb ** 2) / pm["r_bleed"] + 2 * pm["coil_w"] + 2 * pm["sens_w"]
        if abs(new_port - loss["port"]) < 1e-9:
            break
        loss["port"] = new_port
    ptot = sum(loss.values())
    i_a = (abs(p) + ptot) / va
    q_tot = op["q_hs"]
    return {"va": va, "vb": vb, "p": p, "eff": abs(p) / (abs(p) + ptot) if p else 0.0, "ptot": ptot, "loss": loss, "s": s,
            "q_hs": q_tot, "q_cell": qc, "dp": op["dp"], "q_leak": op["q_leak"], "rho": rho, "tj": r["tj_max"],
            "t_sink": r["t_sink"], "t_ind": t_ind, "t_cap": t_cap, "t_cap_allowed": cap_allowed(max(va, vb)),
            "t_exhaust": t_in + ptot / (rho * CP_AIR * max(q_tot, 1.0) / 3600.0), "noise": noise_1m(fan, s, n_run),
            "i_a": i_a, "i_b": i_b, "cell": r}


def limits_ok(st, m):
    return (st["tj"] <= pdz.TJ_DESIGN and st["t_ind"] <= T_IND_MAX and st["t_cap"] <= st["t_cap_allowed"]
            and st["t_sink"] <= m["t_trip"] - 3.0)


def sensitivity(m, temps):
    """derating with pessimistic estimates at once: K_rest x1.5, inductor h x0.7, case-sink R_th x1.3 (restored afterwards)"""
    global K_REST, H_IND0
    k0, h0, rcs0 = K_REST, H_IND0, dict(tr.RTH_CS)
    try:
        K_REST, H_IND0 = 1.5 * k0, 0.7 * h0
        for k in tr.RTH_CS:
            tr.RTH_CS[k] = 1.3 * rcs0[k]
        return [holdable(m, t) for t in temps], [holdable(m, t, 3000.0) for t in temps]
    finally:
        K_REST, H_IND0 = k0, h0
        tr.RTH_CS.update(rcs0)


def port_frac(n, t_in):
    """port-current limit / rating vs inlet (port_spec.json port_current_limit_vs_inlet_C, D-033): the lower of the PV-
    and battery-class curves of this module's port rating (either port can face a battery)"""
    pl = json.load(open(PORT_SPEC))["port_current_limit_vs_inlet_C"]
    rating = int(round(n * tr.I_MAX))
    curves = [pl[k] for k in pl if k.endswith("/%d" % rating)]
    return min(float(np.interp(t_in, pl["inlet_C"], c)) for c in curves) / rating


def holdable(m, t_in, alt=0.0, n_fail=0, flap="flap", fan=None):
    """largest fraction of the module P_max that every DERATE_PTS point holds with fans at 100 % (or the noise cap)"""
    worst = 1.0
    for va, vb in DERATE_PTS:
        lo, hi = 0.0, 1.0
        s_top = m.get("s_cap", 1.0)
        if limits_ok(module_state(m, va, vb, m["n"] * tr.p_limit(va, vb), t_in, alt, fan, s_top, n_fail, flap), m):
            continue
        for _ in range(11):
            mid = 0.5 * (lo + hi)
            if limits_ok(module_state(m, va, vb, mid * m["n"] * tr.p_limit(va, vb), t_in, alt, fan, s_top, n_fail, flap), m):
                lo = mid
            else:
                hi = mid
        worst = min(worst, lo)
    return min(worst, port_frac(m["n"], t_in))


# ------------------------------------------------------------------------------------------------ run
def run():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(OUT, exist_ok=True)
    cell_spec = json.load(open(os.path.join(OUT, "cell_spec.json")))
    role = cell_spec.get("module_basis_role", "primary")      # the device of the pair with the lower corner efficiency
    des, rec, sel, pick, target = pdz.choose(role)
    pm = port_model(3 * tr.I_MAX)
    ctrl_w, i5 = ctrl_power()
    t_trip = cell_spec["protection"]["heatsink_overtemperature_trip_C"]

    # ---- fan selection on the 3-cell module: full power at 45 C (fans at 100 %), law noise, one-fan-failed
    fan_rows = []
    for fan in dv.FANS:
        m = {"des": des, "n": 3, "port": pm, "ctrl_w": ctrl_w, "fan": fan, "t_trip": t_trip, "s_cap": noise_cap(fan, 3)}
        full = holdable(m, 45.0, fan=fan)
        st = module_state(m, 611, 611, 3 * tr.p_limit(611, 611), 45.0, fan=fan)
        fan_rows.append({"fan": fan, "hold45": full, "noise_full45": st["noise"], "s_full45": st["s"], "q_cell": st["q_cell"],
                         "fail_flap": holdable(m, 45.0, n_fail=1, flap="flap", fan=fan),
                         "fail_noflap": holdable(m, 45.0, n_fail=1, flap="no flap", fan=fan),
                         "i_ok": dv.FANS[fan]["i_max"] <= 2.4, "p_w": dv.FANS[fan]["p_w"],
                         "cold_ok": dv.FANS[fan].get("t_amb", (99.0,))[0] <= T_INLET_MIN_REQ})
    ok = [f for f in fan_rows if f["hold45"] >= 0.999 and f["i_ok"] and f["cold_ok"]] or \
        [f for f in fan_rows if f["hold45"] >= 0.999 and f["i_ok"]]          # no cold-rated fan: restriction stated
    choice = min(ok, key=lambda f: (round(f["noise_full45"], 1), -f["fail_flap"]))["fan"]

    res = {}
    for n, name in MODULES.items():
        m = {"des": des, "n": n, "port": port_model(n * tr.I_MAX), "ctrl_w": ctrl_w, "fan": choice, "t_trip": t_trip, "name": name,
             "s_cap": noise_cap(choice, n)}
        corners = {}
        for t_in in (25.0, 45.0):
            for va, vb in tr.CORNERS + [(611, 950), (950, 611), (611, 611)]:
                corners[f"{va}->{vb}@{t_in:.0f}C"] = module_state(m, va, vb, n * tr.p_limit(va, vb), t_in)
        vs = np.arange(tr.V_MIN, tr.V_MAX + 0.1, 50.0)
        grid = [module_state(m, va, vb, n * tr.p_limit(va, vb), 25.0) for va in vs for vb in vs]
        best = None
        for va in np.arange(300.0, 1001.0, 100.0):
            for vb in np.arange(300.0, 1001.0, 100.0):
                for fr in (0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
                    stt = module_state(m, va, vb, fr * n * tr.p_limit(va, vb), 25.0)
                    if best is None or stt["eff"] > best["eff"]:
                        best = stt
        temps = [25, 30, 35, 40, 45, 50, 55, 60]
        der = {"sea level": [holdable(m, t) for t in temps], "3000 m": [holdable(m, t, 3000.0) for t in temps],
               "1 fan failed (flap)": [holdable(m, t, n_fail=1, flap="flap") for t in temps],
               "1 fan failed (no flap)": [holdable(m, t, n_fail=1, flap="no flap") for t in temps]}
        alts = [0, 1000, 2000, 3000, 4000]
        alt45 = [holdable(m, 45.0, a) for a in alts]
        alt60 = [holdable(m, 60.0, a) for a in alts]
        worst_combo = holdable(m, 60.0, 3000.0, n_fail=1, flap="no flap")
        # RECOM RS3-2415D (cell board, +-15 V for the LEM LA 150-P): full load to 71 C, ~30 % at 85 C (gen/data/temp_audit.csv,
        # read off its p4 derating graph). LA 150-P draws 10 mA + I_S per rail, I_S = I_P / 2000 (LA_150-P.pdf p1): at the
        # trip current 46 % of the RS3's 100 mA per rail -> allowed ambient 71 + (100 - 46) / 5 C. Board air = exhaust (conservative).
        rs3_load = (10e-3 + des["i_ocp"] / 2000.0) / 0.1
        st60 = module_state(m, 950, 611, der["sea level"][-1] * n * tr.p_limit(950, 611), 60.0)
        rs3 = {"load_frac_per_rail": round(rs3_load, 3), "allowed_ambient_C": round(71.0 + (1.0 - rs3_load) * 100.0 / 5.0, 1),
               "board_air_C_at_60C_inlet": round(st60["t_exhaust"], 1)}
        rs3["ok"] = rs3["board_air_C_at_60C_inlet"] <= rs3["allowed_ambient_C"]
        sens = sensitivity(m, temps)
        load_tab = {t: [module_state(m, 611, 950, f * n * tr.p_limit(611, 950), t) for f in (0.25, 0.5, 0.75, 1.0)] for t in (25.0, 45.0)}
        loads = np.linspace(0.05, 1.0, 20)
        prof = {t: [module_state(m, 611, 950, f * n * tr.p_limit(611, 950), t) for f in loads] for t in (25.0, 45.0)}
        standby = ctrl_w + P_SYS_IO + 2 * pm["sens_w"] + n * tr.P_AUX_SENSE
        res[n] = {"m": m, "corners": corners, "grid": grid, "vs": vs, "peak": best, "temps": temps, "der": der, "alts": alts,
                  "alt45": alt45, "alt60": alt60, "worst_combo": worst_combo, "sens": sens, "load_tab": load_tab,
                  "loads": loads, "prof": prof, "standby": standby, "rs3": rs3}

    # ---------------------------------------------------------------- self-check (physics)
    for n, r in res.items():
        m = r["m"]
        for st in r["grid"] + list(r["corners"].values()):
            assert abs(sum(st["loss"].values()) - st["ptot"]) < 1e-6, "loss bookkeeping"
            assert st["eff"] < 1.0 and st["ptot"] > st["loss"]["cells"], "module adds losses to the cells"
        op = operating_point(m["fan"], n, 1.0, 1.1)
        assert abs(op["resid"]) < 0.01 * op["dp"], "fan curve meets system curve"
        for st in list(r["corners"].values()):     # energy balance at the ports: P_in - P_out = every loss in the enclosure
            assert abs(st["va"] * st["i_a"] - st["vb"] * st["i_b"] - st["ptot"]) < 1e-6 * st["ptot"], "port energy balance"
            assert st["t_ind"] > st["t_sink"] - 50 and st["tj"] > st["t_sink"], "temperatures ordered"
        for st in r["grid"] + list(r["corners"].values()) + [x for rows in r["load_tab"].values() for x in rows]:
            assert limits_ok(st, m), ("fan law leaves a limit exceeded", st["va"], st["vb"], st["p"], st["tj"], st["t_ind"])
        for key, curve in r["der"].items():        # derating never increases with temperature
            assert all(curve[k + 1] <= curve[k] + 1e-9 for k in range(len(curve) - 1)), ("derating monotonic", key)
        assert all(r["alt45"][k + 1] <= r["alt45"][k] + 1e-9 for k in range(len(r["alt45"]) - 1)), "altitude monotonic"
        assert r["der"]["1 fan failed (no flap)"][4] <= r["der"]["1 fan failed (flap)"][4] + 1e-9 <= r["der"]["sea level"][4] + 2e-9
        assert r["peak"]["eff"] < pdz.tr.cell_losses(des, r["peak"]["va"], r["peak"]["vb"], r["peak"]["p"] / n)["eff"], "module < cell"
    st = res[3]["corners"]["611->611@45C"]
    heat = st["ptot"]
    assert abs(heat - (st["loss"]["cells"] + st["loss"]["port"] + st["loss"]["ctrl_aux"] + st["loss"]["fans"])) < 1e-9

    write_outputs(res, fan_rows, choice, pm, ctrl_w, i5, des, cell_spec, plt)
    return res, fan_rows, choice


def write_outputs(res, fan_rows, choice, pm, ctrl_w, i5, des, cell_spec, plt):
    f = dv.FANS[choice]
    # ---- plots
    fig, axs = plt.subplots(1, 3, figsize=(16, 4.6))
    for n, r in res.items():
        for key, ls in (("sea level", "-"), ("3000 m", "--"), ("1 fan failed (flap)", ":"), ("1 fan failed (no flap)", "-.")):
            axs[0].plot(r["temps"], [x * n * tr.P_MAX / 1e3 for x in r["der"][key]], ls, label=f"{MODULES[n]} {key}")
    for n in res:
        axs[0].plot([t for t, _ in EVT], [p * n * tr.P_RATED / 1e3 for _, p in EVT], "k.", ms=8)
    axs[0].set_xlabel("inlet air [C]")
    axs[0].set_ylabel("power held [kW]")
    axs[0].set_title("derating (fans 100 %); dots = roadmap EVT curve x rated", fontsize=9)
    axs[0].legend(fontsize=6)
    axs[0].grid(alpha=0.3)
    qs = np.linspace(1, 3 * dv.FANS[choice]["q_free"], 200)
    for fan in dv.FANS:
        axs[1].plot(qs, [fan_dp(fan, q / 3, 1.0, 1.1) for q in qs], label=f"3 x {fan} in parallel (100 %)")
    axs[1].plot(qs, [sys_dp(q, 3, 1.1) for q in qs], "k", label="system: 3 heatsinks + rest")
    axs[1].set_ylim(0, 400)
    axs[1].set_xlabel("total heatsink flow [m3/h]")
    axs[1].set_ylabel("dp [Pa]")
    axs[1].legend(fontsize=7)
    axs[1].grid(alpha=0.3)
    r3 = res[3]
    for t, ls in ((25.0, "-"), (45.0, "--")):
        loads = r3["loads"] * 3 * tr.p_limit(611, 950) / 1e3
        axs[2].plot(loads, [x["loss"]["fans"] for x in r3["prof"][t]], ls, label=f"fan power, {t:.0f} C")
        axs[2].plot(loads, [x["noise"] for x in r3["prof"][t]], ls, label=f"noise dB(A) at 1 m, {t:.0f} C")
    axs[2].axhline(65, color="r", lw=0.6)
    axs[2].set_xlabel("PV-P75 power [kW] (611->950 V)")
    axs[2].legend(fontsize=7)
    axs[2].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "module_cooling_derating.png"), dpi=120)
    plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(12, 4.8))
    for ax, n in zip(axs, res):
        r = res[n]
        vs = r["vs"]
        z = np.array([[next(x["eff"] for x in r["grid"] if x["va"] == va and x["vb"] == vb) for va in vs] for vb in vs]) * 100
        cs = ax.contourf(vs, vs, z, levels=[97.0, 98.0, 98.5, 98.8, 99.0, 99.1, 99.2, 99.3, 99.4, 99.5], cmap="viridis")
        ax.contour(vs, vs, z, levels=[99.0], colors="r")
        ax.set_title(f"{MODULES[n]} module efficiency at P_lim, 25 C inlet (red 99 %)", fontsize=9)
        ax.set_xlabel("V_A [V]")
        ax.set_ylabel("V_B [V]")
        fig.colorbar(cs, ax=ax)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "module_efficiency_maps.png"), dpi=120)
    plt.close(fig)

    with open(os.path.join(OUT, "module_envelope.csv"), "w") as fh:
        fh.write("cells,va,vb,p_W,eff,loss_W,cells_W,port_W,ctrl_aux_W,fans_W,fan_speed,tj_C\n")
        for n, r in res.items():
            for x in r["grid"]:
                lo = x["loss"]
                fh.write(f"{n},{x['va']:.0f},{x['vb']:.0f},{x['p']:.0f},{x['eff']:.5f},{x['ptot']:.1f},{lo['cells']:.1f},{lo['port']:.1f},"
                         f"{lo['ctrl_aux']:.1f},{lo['fans']:.1f},{x['s']:.3f},{x['tj']:.1f}\n")

    # ---- module_spec.json (new file; new keys only)
    spec = {"title": "PV-P75 / PV-P100/110 module figures (calculated, not measured)", "generated_by": "sim/pv_module.py",
            "cell_spec": "sim/out/pv_design/cell_spec.json", "port_inputs": "sim/out/port_design/port_spec.json + report.md",
            "fan": {"mpn": choice, "manufacturer": f["mfr"], "datasheet": f["src"], "count_per_cell": 1, "rated_W": f["p_w"],
                    "rated_A": f["i_max"], "channel_limit_A": 2.4, "speed_law": {"min_fraction": S_MIN, "heatsink_C_at_min": T_ON,
                    "heatsink_C_at_full": T_FULL, "inductor_C_at_min": TI_ON, "inductor_C_at_full": TI_FULL,
                    "law": "max of two linear ramps: heatsink NTC and inductor NTC"},
                    "arrangement": "one 120 mm fan per cell in a common rear exhaust plenum, backflow shutter per fan (recommended)",
                    "ambient_rated_C": list(f.get("t_amb", (None, None))), "PV_20_inlet_C": [T_INLET_MIN_REQ, tr.T_AIR_HOT],
                    "min_speed_fraction": round(max(S_MIN, f.get("s_min", 0.0)), 3),
                    "speed_law_note": ("PWM duty never below the datasheet's lowest specified duty (35 % = 2900 rpm for the San Ace 120T); "
                                       f"speed capped so that all fans stay <= {NOISE_MAX:.0f} dB(A) at 1 m"),
                    "cold_restriction": (None if f.get("t_amb", (99.0,))[0] <= T_INLET_MIN_REQ else
                                         f"RESTRICTION: the datasheet rates the fan for {f['t_amb'][0]:.0f}..{f['t_amb'][1]:.0f} C ambient; "
                                         f"PV-20 asks for {T_INLET_MIN_REQ:.0f} C. Until a fan rated for {T_INLET_MIN_REQ:.0f} C or below is "
                                         f"qualified (none of the fans in docs/datasheets/thermal is: -20 C / -20 C / -10 C), the module is "
                                         f"specified for a {f['t_amb'][0]:.0f} C minimum inlet; at {T_INLET_MIN_REQ:.0f}..{f['t_amb'][0]:.0f} C "
                                         f"ask ebm-papst for a low-temperature release of this type or select a -30 C rated fan"),
                    "selection": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items()} for x in fan_rows]},
            "device_primary": cell_spec.get("device_primary"), "device_alternate": cell_spec.get("device_alternate"),
            "module_basis_device": des["dev"],
            "air": {"density_basis": "ISA pressure + ideal gas at the inlet temperature", "K_rest": K_REST,
                    "heatsink_dp_correlation": "Shah & London developing laminar parallel-plate f_app + Kays & London entrance/exit"},
            "modules": {}}
    for n, r in res.items():
        m = r["m"]
        c25 = {k.replace("@25C", ""): v for k, v in r["corners"].items() if k.endswith("@25C")}
        c45 = {k.replace("@45C", ""): v for k, v in r["corners"].items() if k.endswith("@45C")}
        full45 = c45["611->611"]
        pk = r["peak"]
        spec["modules"][MODULES[n]] = {
            "cells": n, "P_rated_kW": n * tr.P_RATED / 1e3, "P_max_kW": n * tr.P_MAX / 1e3, "I_port_max_A": n * tr.I_MAX,
            "V_ports_V": [tr.V_MIN, tr.V_MAX], "V_full_power_V": list(tr.V_FL),
            "efficiency_peak_megarevo_comparable": round(pk["eff"], 5),
            "efficiency_peak_at": {"va": pk["va"], "vb": pk["vb"], "p_kW": round(pk["p"] / 1e3, 2), "inlet_C": 25.0},
            "efficiency_basis": "everything inside the enclosure: cells (incl. gate-drive bias, cell sensing), port board, CTRL, "
                                "SYS-IO-AUX, fans; the cabinet 24 V supply efficiency is outside the enclosure and excluded",
            "efficiency_corners_25C": {k: round(v["eff"], 5) for k, v in c25.items()},
            "efficiency_corners_45C": {k: round(v["eff"], 5) for k, v in c45.items()},
            "losses_by_group_W_611V_611V_full_45C": {k: round(v, 1) for k, v in full45["loss"].items()},
            "heat_rejected_W_max_45C": round(max(v["ptot"] for v in c45.values()), 1),
            "airflow_m3h_full_45C": round(full45["q_hs"], 1), "airflow_per_cell_m3h": round(full45["q_cell"], 1),
            "fan_speed_full_45C": round(full45["s"], 3), "fan_power_W_full_45C": round(full45["loss"]["fans"], 1),
            "fan_speed_cap": round(m["s_cap"], 3), "noise_dBA_1m_all_fans_at_cap": round(noise_1m(m["fan"], m["s_cap"], n), 1),
            "noise_dBA_1m_all_fans_100pct": round(noise_1m(m["fan"], 1.0, n), 1),
            "fan_power_W_min": round(n * fan_power(m["fan"], max(S_MIN, dv.FANS[m["fan"]].get("s_min", 0.0))), 1),
            "noise_dBA_1m_full_45C": round(full45["noise"], 1),
            "noise_dBA_1m_full_25C": round(c25["611->611"]["noise"], 1),
            "exhaust_C_full_45C": round(full45["t_exhaust"], 1),
            "derating_fraction_of_P_max": {"inlet_C": r["temps"], **{k: [round(x, 3) for x in v] for k, v in r["der"].items()}},
            "derating_kW_sea_level": [round(x * n * tr.P_MAX / 1e3, 1) for x in r["der"]["sea level"]],
            "evt_roadmap_kW": {f"{t}": p * n * tr.P_RATED / 1e3 for t, p in EVT},
            "altitude_m": r["alts"], "derating_fraction_45C_vs_altitude": [round(x, 3) for x in r["alt45"]],
            "derating_fraction_60C_vs_altitude": [round(x, 3) for x in r["alt60"]],
            "worst_combined_60C_3000m_one_fan_no_flap": round(r["worst_combo"], 3),
            "derating_pessimistic_sea_level": [round(x, 3) for x in r["sens"][0]],
            "derating_pessimistic_3000m": [round(x, 3) for x in r["sens"][1]],
            "one_fan_failed_fraction_45C": {"flap": r["der"]["1 fan failed (flap)"][4], "no flap": r["der"]["1 fan failed (no flap)"][4]},
            "bus_capacitance_uF": {"port_A": n * des["caps"]["A"]["C"] * 1e6, "port_B": n * des["caps"]["B"]["C"] * 1e6,
                                   "port_X_caps_uF": 4.4, "note": "cell banks paralleled on the module bus + port-board X capacitors (2 x 2.2 uF per port)"},
            "standby_W_estimate": round(r["standby"], 1),
            "port_current_limit_fraction_vs_inlet": [round(port_frac(n, t), 3) for t in r["temps"]],
            "rs3_2415d_cell_board_supply": r["rs3"],
            "port_variant": {"I_A": m["port"]["i_rating"], "fuse": m["port"]["fuse"], "fuse_In_A": m["port"]["fuse_in"],
                             "fuse_derated_A_at_50C": m["port"]["fuse_derated"], "R_port_mOhm": round(m["port"]["r_port"] * 1e3, 3)}}
    fan_usd, fan_src = FAN_VOLUME_PRICE.get(choice, tr.price(choice))
    cell_usd = cell_spec["cost_usd"]["per_cell"]["total"] if "cost_usd" in cell_spec else None
    spec["cost_usd"] = {MODULES[n]: {"cells": round(n * cell_usd, 0) if cell_usd else None, "fans": round(n * fan_usd, 0),
                                     "total_cells_and_fans": round(n * ((cell_usd or 0.0) + fan_usd), 0),
                                     "per_kW_rated": round(n * ((cell_usd or 0.0) + fan_usd) / (n * tr.P_RATED / 1e3), 2)} for n in MODULES}
    spec["cost_usd"]["basis"] = (f"cells: cell_spec.json cost_usd (Gate-0b cost model); fan {choice}: {fan_usd:.2f} USD ({fan_src}); "
                                 "port board, CTRL, SYS-IO-AUX, enclosure, cabling and assembly NOT included")
    json.dump(spec, open(os.path.join(OUT, "module_spec.json"), "w"), indent=1, default=float, allow_nan=False)
    write_report(res, fan_rows, choice, pm, ctrl_w, i5, spec)


def write_report(res, fan_rows, choice, pm, ctrl_w, i5, spec):
    f = dv.FANS[choice]
    L = []
    a = L.append
    a("# PV-P75 / PV-P100/110 module level\n")
    a("Generated by `sim/pv_module.py`; every number is written by the script. **Calculated, not measured.** "
      "Comparison file: `module_spec.json`. Cell design: `report.md`, `cell_spec.json`.\n")
    a("## 1. What is inside the enclosure\n")
    a("| group | value | source |")
    a("|---|---|---|")
    rows = [("cells", "N x cell losses incl. gate-drive bias and cell sensing", "sim/pv_design.py"),
            ("port series resistance (135 A variant)", f"{pm['r_port']*1e3:.2f} mOhm per port: 2 fuses x {pm['r_fuse']*1e3:.3f}, "
                                       f"contactor+shunt+busbars {pm['r_loop_x']*1e3:.2f}, CM choke 2 x {pm['r_cmc']*1e3:.2f}",
             "; ".join(pm["src"][k] for k in ("fuse", "loop", "cmc"))),
            ("port series resistance (180 A variant)", f"{res[4]['m']['port']['r_port']*1e3:.2f} mOhm per port ({res[4]['m']['port']['fuse']})",
             res[4]["m"]["port"]["src"]["fuse"]),
            ("bleeders/dividers", f"{pm['r_bleed']/1e3:.0f} k per port (V^2/R)", pm["src"]["bleeder"]),
            ("contactor coils", f"2 x {pm['coil_w']:.1f} W", pm["src"]["coil"]),
            ("port sensor supplies", f"2 x {pm['sens_w']:.2f} W", pm["src"]["sensor"]),
            ("CTRL-C2000", f"{ctrl_w:.2f} W ({i5} A at 5 V / {ETA_LMR38020})", "gen/ctrl_c2000.py LMR38020 block note; efficiency ESTIMATE"),
            ("SYS-IO-AUX", f"{P_SYS_IO:.1f} W", "ESTIMATE (no consumption note in gen/sys_io_aux.py)"),
            ("fans", f"{choice}: {f['p_w']} W, {f['i_max']} A each at 100 % (x speed^3)", f["src"])]
    for k, v, s in rows:
        a(f"| {k} | {v} | {s} |")
    a("\nThe 24 V comes from the cabinet feeds (SYS-IO-AUX ORing; AUX-HV cannot carry the fans, gen/sys_io_aux.py): the cabinet "
      "supply's conversion loss is outside the enclosure. Self-powered through AUX-HV (75.4 % full load, CRD-025DD17P-J user "
      "guide via docs/reference-designs/wolfspeed/crd-020dd17p-j/README.md) the control/aux part would cost 1/0.754 = 1.33x.\n")
    a("## 2. Module efficiency (25 C inlet, sea level, fan law active)\n")
    a("| module | Megarevo-comparable peak | at | 550->950 | 950->550 | 550->550 | 950->950 | 611->611 (45 C) | standby est. |")
    a("|---|---|---|---|---|---|---|---|---|")
    for n, r in res.items():
        pk = r["peak"]
        c = r["corners"]
        a(f"| {MODULES[n]} | **{pk['eff']*100:.2f} %** | {pk['va']:.0f}->{pk['vb']:.0f} V, {pk['p']/1e3:.1f} kW | " +
          " | ".join(f"{c[f'{k}@25C']['eff']*100:.2f}" for k in ("550->950", "950->550", "550->550", "950->950")) +
          f" | {c['611->611@45C']['eff']*100:.2f} | {r['standby']:.1f} W |")
    a("\nMegarevo PMD-75-G3 publishes **Max. efficiency 99 %** and **standby < 20 W** (docs/reference-designs/megarevo/pmd-75-g3/spec.md).\n")
    a("Losses by group at 611->611 V full power, 45 C inlet [W]:\n")
    a("| module | cells | port | ctrl+aux | fans | total | heat to air |")
    a("|---|---|---|---|---|---|---|")
    for n, r in res.items():
        st = r["corners"]["611->611@45C"]
        lo = st["loss"]
        a(f"| {MODULES[n]} | {lo['cells']:.0f} | {lo['port']:.0f} | {lo['ctrl_aux']:.1f} | {lo['fans']:.1f} | {st['ptot']:.0f} | "
          f"{st['ptot']:.0f} (exhaust {st['t_exhaust']:.1f} C) |")
    a("\n![maps](module_efficiency_maps.png)\n")
    a("## 3. Cooling\n")
    a(f"- Heatsink pressure drop (per cell, geometry of sim/pv_tradeoff.py HS_GEOM): Shah & London apparent friction for developing "
      f"laminar flow between parallel plates plus Kays & London entrance/exit losses: {tr.heatsink_dp(100):.0f} / {tr.heatsink_dp(150):.0f} / "
      f"{tr.heatsink_dp(200):.0f} Pa at 100 / 150 / 200 m3/h. Rest of the module: K = {K_REST} on the duct velocity (ESTIMATE).")
    a(f"- Arrangement (assumption for this estimate; mechanics are out of scope): one 120 mm fan per cell in a common rear exhaust plenum. "
      f"A dead fan leaks backwards (K = {K_LEAK['no flap']} stopped rotor; {K_LEAK['flap']} with a backflow shutter).")
    a("\n| fan | full power at 45 C (fans 100 %) | noise at full power, 45 C (law) [dB(A) 1 m] | flow per cell [m3/h] | 1 fan failed, 45 C: shutter / none | current OK (<= 2.4 A) |")
    a("|---|---|---|---|---|---|")
    for x in fan_rows:
        a(f"| {x['fan']} | {x['hold45']*100:.0f} % | {x['noise_full45']:.1f} (speed {x['s_full45']*100:.0f} %) | {x['q_cell']:.0f} | "
          f"{x['fail_flap']*100:.0f} % / {x['fail_noflap']*100:.0f} % of P_max | {'yes' if x['i_ok'] else 'NO'} |")
    a(f"\n**Choice: {choice}** ({f['mfr']}), one per cell: the quietest fan that holds full power at 45 C within the channel current. "
      f"Noise basis: {f['lp_note']}; +50 log10(speed) and +{INSTALL_DB} dB installation (ESTIMATE); fans summed incoherently.")
    if spec["fan"]["cold_restriction"]:
        a(f"- **Cold limit:** {spec['fan']['cold_restriction']}.")
    else:
        a(f"- Rated {f['t_amb'][0]:.0f}..{f['t_amb'][1]:.0f} C ambient: PV-20's {T_INLET_MIN_REQ:.0f} C is met. Speed never below "
          f"{spec['fan']['min_speed_fraction']*100:.0f} % (its lowest specified PWM duty); capped at " +
          ", ".join(f"{MODULES[n]} {spec['modules'][MODULES[n]]['fan_speed_cap']*100:.0f} % "
                    f"({spec['modules'][MODULES[n]]['noise_dBA_1m_all_fans_at_cap']:.1f} dB(A), "
                    f"{spec['modules'][MODULES[n]]['noise_dBA_1m_all_fans_100pct']:.1f} at 100 %)" for n in MODULES) +
          f" so that all fans stay <= {NOISE_MAX:.0f} dB(A) at 1 m; derating below is computed at that cap.")
    a(f"- Fan law: speed = max(heatsink ramp {S_MIN*100:.0f} % at {T_ON:.0f} C -> 100 % at {T_FULL:.0f} C, inductor ramp {S_MIN*100:.0f} % at "
      f"{TI_ON:.0f} C -> 100 % at {TI_FULL:.0f} C). A heatsink-only law let the inductor reach ~128 C at 35 C inlet / full load (fans ~50 %): "
      f"the inductor NTC (roadmap: inductor temperature sensing) must take part. Fan power ~ speed^3.\n")
    a("![cooling](module_cooling_derating.png)\n")
    a(f"## 4. Ambient derating (fans at 100 % or the noise cap, limits: Tj {pdz.TJ_DESIGN:.0f} C design, inductor hot spot {T_IND_MAX:.0f} C, C4AQ hot spot vs voltage, heatsink trip - 3 K, port-current limit)\n")
    for n, r in res.items():
        a(f"**{MODULES[n]}** - power held [kW] (fraction of {n*tr.P_MAX/1e3:.1f} kW max):\n")
        a("| inlet [C] | " + " | ".join(str(t) for t in r["temps"]) + " |")
        a("|---|" + "---|" * len(r["temps"]))
        for key, curve in r["der"].items():
            a(f"| {key} | " + " | ".join(f"{x*n*tr.P_MAX/1e3:.1f}" for x in curve) + " |")
        a("| roadmap EVT (x rated) | " + " | ".join(f"{dict(EVT).get(t, 1.0 if t < 45 else float('nan'))*n*tr.P_RATED/1e3:.1f}" for t in r["temps"]) + " |")
        a("")
        a(f"Altitude (fans 100 %), fraction of P_max at 45 C: " + ", ".join(f"{alt} m {x*100:.0f} %" for alt, x in zip(r["alts"], r["alt45"]))
          + "; at 60 C: " + ", ".join(f"{alt} m {x*100:.0f} %" for alt, x in zip(r["alts"], r["alt60"])) + ".")
        a(f"Combined worst case 60 C + 3000 m + one fan dead without shutter: {r['worst_combo']*100:.0f} % of P_max.")
        a(f"Pessimistic estimates together (K_rest x1.5, inductor h x0.7, case-sink R_th x1.3): sea level " +
          ", ".join(f"{t} C {x*100:.0f} %" for t, x in zip(r["temps"], r["sens"][0])) + "; 3000 m " +
          ", ".join(f"{t} C {x*100:.0f} %" for t, x in zip(r["temps"], r["sens"][1])) + ".\n")
    a("Binding limits and margins at full power, 60 C inlet, sea level, fans 100 % (PV-P75, 950->611 V):\n")
    st = module_state(res[3]["m"], 950, 611, 3 * tr.p_limit(950, 611), 60.0, speed=1.0)
    a(f"Tj {st['tj']:.0f} C (limit {pdz.TJ_DESIGN:.0f}), inductor hot spot {st['t_ind']:.0f} C (limit {T_IND_MAX:.0f}), C4AQ hot spot "
      f"{st['t_cap']:.0f} C (limit {st['t_cap_allowed']:.0f} C at {max(st['va'], st['vb']):.0f} V), heatsink {st['t_sink']:.0f} C "
      f"(trip {res[3]['m']['t_trip']:.0f} C). The roadmap EVT curve is therefore conservative for these parts; it stays the EVT rule until "
      f"hot-chamber tests, and the port-board fuse derating above 45 C is still open.\n")
    a("Fan law in operation (PV-P75, 611->950 V):\n")
    a("| inlet | load | fan speed | fan power [W] | noise [dB(A) 1 m] | hottest Tj [C] | inductor [C] | module eff [%] |")
    a("|---|---|---|---|---|---|---|---|")
    for t, rows in res[3]["load_tab"].items():
        for fr, x in zip((0.25, 0.5, 0.75, 1.0), rows):
            a(f"| {t:.0f} C | {fr*100:.0f} % | {x['s']*100:.0f} % | {x['loss']['fans']:.1f} | {x['noise']:.1f} | {x['tj']:.0f} | "
              f"{x['t_ind']:.0f} | {x['eff']*100:.2f} |")
    a("")
    a("## 5. Against Megarevo PMD-75-G3 published figures\n")
    r3 = res[3]
    st45 = r3["corners"]["611->611@45C"]
    rows = [("Max. efficiency 99 %", f"{r3['peak']['eff']*100:.2f} % peak (Megarevo-comparable)", r3["peak"]["eff"] >= 0.99),
            ("Noise <= 65 dB at 1 m", f"{st45['noise']:.1f} dB(A) at full power 45 C (estimate)", st45["noise"] <= 65.0),
            ("Standby < 20 W", f"{r3['standby']:.1f} W (estimate: CTRL + SYS + port sensors + cell sensing)", r3["standby"] < 20.0),
            ("Full power to 45 C", f"{r3['der']['sea level'][4]*100:.0f} % of 82.5 kW at 45 C", r3["der"]["sea level"][4] >= 0.999),
            ("Derating above 3000 m", f"{r3['alt45'][3]*100:.0f} % of P_max at 3000 m, 45 C", r3["alt45"][3] >= 0.999)]
    a("| published | ours | met |")
    a("|---|---|---|")
    for k, v, ok in rows:
        a(f"| {k} | {v} | {'yes' if ok else '**no**'} |")
    a("\n## 6. Open items\n")
    a(f"- Port fuses are derated for 50 C at the fuse (inlet air path, port design A_T_FUSE): 135 A variant {res[3]['m']['port']['fuse']} "
      f"{res[3]['m']['port']['fuse_derated']:.0f} A, 180 A variant {res[4]['m']['port']['fuse']} {res[4]['m']['port']['fuse_derated']:.0f} A. Above "
      f"45 C inlet the 135 A link drops below 135 A - that is a port-board current derating not included in section 4 (open with the "
      f"port design).")
    a("- CM-choke resistance, SYS-IO-AUX consumption, converter efficiencies, K_rest, dead-fan K and the installation noise term are "
      "estimates; the derating and noise are only as good as the duct model - confirm with CFD/bench.")
    a("- The derating above is the lower of the power-stage limits and the port-current limit vs inlet of `port_spec.json` "
      "(D-033: 135 A PV port 124 A at 60 C, 180 A battery port 136 A at 60 C).")
    open(os.path.join(OUT, "module_report.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    res, fan_rows, choice = run()
    print(f"fan: {choice}")
    for n, r in res.items():
        c = r["corners"]
        print(f"{MODULES[n]}: peak {r['peak']['eff']*100:.2f} % at {r['peak']['va']:.0f}->{r['peak']['vb']:.0f} V {r['peak']['p']/1e3:.1f} kW; "
              f"corners 25C " + ", ".join(f"{k} {c[k+'@25C']['eff']*100:.2f}" for k in ("550->950", "950->550", "550->550", "950->950")) +
              f"; noise full 45C {c['611->611@45C']['noise']:.1f} dB(A); derating " +
              ", ".join(f"{t}C {x*100:.0f}%" for t, x in zip(r["temps"], r["der"]["sea level"])) +
              f"; 1 fan failed 45C flap {r['der']['1 fan failed (flap)'][4]*100:.0f}% / no flap {r['der']['1 fan failed (no flap)'][4]*100:.0f}%")
    print("pv_module self-check passed")
