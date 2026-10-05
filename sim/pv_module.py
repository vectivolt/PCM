"""PV-P75 (3 phases) and PV-P100/110 (4 phases) at module level, cost-first build (D-044..D-052): everything inside the enclosure.

Run:  .venv/bin/python sim/pv_module.py      (needs sim/out/pv_design/cell_spec.json from sim/pv_design.py)
Out:  sim/out/pv_design/{module_spec.json, module_report.md, module_*.png, module_*.csv}

As built (docs/requirements/ARCHITECTURE-COSTFIRST.md, hardware/PV-PWR rev A2 / PV-PWR-4, hardware/PV-CTL rev A1): all phases on
ONE earthed extrusion with AlN pads, three 120 mm Delta fans for both builds, one 75 W flyback (sim/out/aux_hv_design/aux75_spec.json)
feeding gate bias, logic, contactor coils and fans, the lean port of port_spec.json (PV port: one contactor, no fuse; battery
port: contactor + two 250 A aR links), no separate control/system boards.  Adds to the cell model (sim/pv_design.py) the port
conduction, the aux input power, the passive HV loads, the fans at their operating point on the shared heatsink, a fan law, noise,
ambient/altitude derating and one-fan-failed.  Calculated, not measured; estimates are labelled where they are defined.
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
AUX_SPEC = os.path.join(ROOT, "sim", "out", "aux_hv_design", "aux75_spec.json")
CTRL_SPEC = os.path.join(ROOT, "sim", "out", "pv_control", "control_spec.json")
MODULES = {3: "PV-P75", 4: "PV-P100/110"}
CP_AIR = 1005.0

# ------------------------------------------------------------------------------------------------ estimates (labelled)
K_REST = 10.0            # loss coefficient of grille + port parts + inductor/capacitor field + fan guards on the duct
                         # velocity (ESTIMATE, +-50 %)
DUCT_W, DUCT_H = 0.420, 0.120   # m: duct cross-section in the 444 mm envelope (both builds)
A_FAN = math.pi / 4 * (0.115 ** 2 - 0.040 ** 2)   # m2, free annulus of a 120 mm fan
K_LEAK = {"no flap": 4.0, "flap": 200.0}  # dead-fan reverse-flow loss coefficient: stopped rotor + guard / with backflow
                                          # shutter (ESTIMATES); the cost-first build has no shutters
RHO_DS = 1.20            # kg/m3 at which the fan P-Q curves are given (20-25 C, sea level; ASSUMPTION)
INSTALL_DB = 3.0         # dB(A) added for back-pressure and duct noise in the enclosure (ESTIMATE)
H_IND0 = 25.0            # W/m2K inductor surface coefficient at 150 m3/h, 1.10 kg/m3 (as in pv_tradeoff); scaled ^0.6 with mass flow
T_IND_MAX = 155.0        # C inductor hot spot: class H insulation system (180 C) less 25 K (MG-07)
DT_CAP_PRE = 3.0         # K preheat of the port-capacitor air by the port parts (ESTIMATE)
T_INLET_MIN_REQ = -30.0  # C, PV-20 lowest operating inlet temperature
NOISE_MAX = 65.0         # dB(A) at 1 m: the Megarevo figure the comparison holds us to (sim/compare_megarevo.py)
S_MIN, T_ON, T_FULL = 0.30, 40.0, 60.0   # fan law, heatsink NTC: 30 % below 40 C, linear to 100 % at 60 C (our choice)
TI_ON, TI_FULL = 90.0, 115.0             # fan law, inductor NTC: 30 % below 90 C, linear to 100 % at 115 C; speed = max of both
DERATE_PTS = [(611, 611), (950, 611), (611, 950), (1000, 500)]   # highest-loss full-power points (pv_design report)
EVT = [(45, 1.0), (50, 0.8), (55, 0.6), (60, 0.4)]               # roadmap provisional EVT curve (% of rated)

# ------------------------------------------------------------------------------------------------ cost-first build (as drawn)
N_FANS = 3                                         # both builds: 4 x 120 mm do not fit the 444 mm width (architecture sec. 9)
FAN_OF = {3: "AFB1224SHE-F00", 4: "FFB1224SHE-F00"}
FAN_P_CAP = {4: 17.0}                              # W per fan: the aux SELV budget caps the FFB (19.2 W rated) at 17 W
FAN_USD = {"AFB1224SHE-F00": (12.85, "Master Electronics @504, web-search result (gen/data/prices.csv)"),
           "FFB1224SHE-F00": (None, "no price on file")}
DELTA_USD = FAN_USD["AFB1224SHE-F00"][0]
# one earthed extrusion, 444 mm wide, 10 mm base (RTH_SPREAD basis), 40 mm x 1.5 mm fins; fin count solved so that R_sa (incl. air
# rise) = 0.060 K/W at 400 m3/h, 45 C air - the architecture's limit, so every figure below is for the worst heatsink the spec
# allows (ASSUMPTION: geometry stands in for the mechanical design, out of scope).  PV-P100/110: same section, 4/3 the length
# (4.8 vs 3.6 kg).  Flow spreads evenly over the fins (plenum between fans and fins ASSUMED).
HS = {"width": 0.444, "h_fin": 0.040, "t_fin": 1.5e-3, "t_base": 0.010, "length": {3: 0.150, 4: 0.200}, "n_fin": None}
R_SA_SPEC = {3: (0.060, 400.0), 4: (0.049, None)}  # K/W at m3/h (architecture sec. 9; PV-P100/110 flow not stated)
T_SINK_TRIP = (86.3, 89.5)   # C heatsink OT trip band, NTC1-4 (hardware/PV-CTL design check); derating keeps the sink below the low end
T_IND_TRIP = (146.2, 153.7)  # C inductor OT trip band, NTC5-8 (PV-CTL); derating keeps the hot spot below the low end
DP_RULE = (60.0, 420.0)      # Pa at m3/h: heatsink + module <= 60 Pa at 140 m3/h per fan section (architecture sec. 9)
# lean port (port_spec.json 'lean'): PV port = one HFE82V contactor, no fuse; battery port (B) = contactor + 2 x HPE501 250 A aR
LEAN_I_B = {3: 135.0, 4: 145.0}  # A battery-port limit: 135 A passes at 65 C fuse air (strict 145 A); 180 A build carries the
                                 # conservative 145 A at every inlet temperature (coordinator)
R_CONTACT = 0.2e-3       # ohm HFE82V contact resistance max at 250 A (Hongfa-HFE82V-300C.pdf p.1)
R_SHUNT = 0.1e-3         # ohm port shunt (port_spec lean shunt: 2 x 200 uOhm)
R_BUSBAR = 0.30e-3       # ohm busbars + lugs per port (port report A_R_LOOP_X 0.65 less contactor 0.25 and shunt 0.1; ESTIMATE)
R_VMON, R_DIV = 990e3, 6e6   # varistor monitor loop per port (PV-PWR); dividers 6 M (ASSUMPTION, PV-PWR
                                             # quotes 'the 6 M divider'): terminal x2 and PE always, bank x2 with the banks charged
P_BIAS = 3.0             # W per phase gate-drive bias at the 5 V rail (coordinator; PV-PWR worst raw 3.11 W)
P_LOGIC5 = 11.4 - 3 * 3.11   # W at the 5 V output besides gate bias, incl. 3.3 V and the control board (PV-PWR live budget, maxima)
ETA_5V = 0.85            # 5 V / 3.3 V converters (PV-PWR ASSUMED)
P_COIL_HOLD = (2.35, 3.26)   # W per contactor on the economiser, typ / max (port_spec lean coil_economiser)
ETA_FANBUCK, P_SELV5 = 0.90, 1.0   # fan buck efficiency and SELV 5 V branch W (PV-CTL budget)
V_OV_LIMIT = 1135.0      # V port-B limit for the load-rejection overshoot (coordinator, D-052)


def air_density(alt_m, t_c):
    """ISA pressure at altitude, ideal gas at the inlet temperature"""
    p = 101325.0 * (1 - 2.25577e-5 * alt_m) ** 5.25588
    return p / (287.05 * (t_c + 273.15))


# ------------------------------------------------------------------------------------------------ inputs read from files
AUX = json.load(open(AUX_SPEC))
LEAN = json.load(open(PORT_SPEC))["lean"]
R_FUSE = LEAN["battery_fuse"]["loss_W_per_link"]["135"] / 135.0 ** 2      # ohm per aR link (21.4 W at 135 A)
R_PORT_A = R_CONTACT + R_SHUNT + R_BUSBAR
R_BLEED = LEAN["bleeder"]["n"] * LEAN["bleeder"]["R_elem_ohm"]          # bleeder per port as drawn (port_spec lean, PV-PWR)
PWR_CHECK = {3: "hardware/PV-PWR/outputs/PV-PWR_design_check.txt", 4: "hardware/PV-PWR-4/outputs/PV-PWR-4_design_check.txt"}


def drawn_bank(n):
    """FCSA3DS456 count per port as drawn: the PV-PWR / PV-PWR-4 design check ('Port banks ...: A a / B b x FCSA3DS456')"""
    m = re.search(r"Port banks[^:]*: A (\d+) / B (\d+) x FCSA3DS456", open(os.path.join(ROOT, PWR_CHECK[n])).read())
    assert m, "port film bank count not found in " + PWR_CHECK[n]
    return {"A": int(m.group(1)), "B": int(m.group(2))}
R_PORT_B = R_PORT_A + 2 * R_FUSE


def aux_eta(n, v):
    """75 W flyback efficiency vs input (aux75_spec 'full power, fans 100 %' row of this build); fed from the higher port"""
    row = next(x for k, x in AUX["efficiency"].items() if k.startswith(MODULES[n] + " "))
    vs = sorted(float(k) for k in row)
    return float(np.interp(v, vs, [row[str(int(x))] for x in vs]))


def aux_in(n, v, fans_w, coil=P_COIL_HOLD[0]):
    """module input power of the aux supply: (live: bias + logic through the 5 V converters, two held coils; SELV: fan buck, 5 V)"""
    live = (n * P_BIAS + P_LOGIC5) / ETA_5V + 2 * coil
    return (live + fans_w / ETA_FANBUCK + P_SELV5) / aux_eta(n, v)


def p_hv(va, vb):
    """passive HV loads in operation: varistor monitor loops, bleeders, terminal and bank dividers per port, PE divider"""
    return (va ** 2 + vb ** 2) * (1 / R_VMON + 1 / R_BLEED + 2 / R_DIV) + max(va, vb) ** 2 / R_DIV


def standby(n, v, held):
    """gates off, fans off: aux75_spec standby aux input (coils at <= 1.5 W each) corrected to the coils' real state at the spec's
    incremental efficiency 0.80, plus the passive HV loads (bleeders and bank dividers only with the banks charged = held)"""
    sb = AUX["standby"][MODULES[n]][str(int(v))]["aux_in_W"]
    coil = 2 * ((P_COIL_HOLD[0] if held else 0.0) - 1.5) / 0.80
    hv = 2 * v ** 2 * (1 / R_VMON + 1 / R_DIV) + v ** 2 / R_DIV + (2 * v ** 2 * (1 / R_BLEED + 1 / R_DIV) if held else 0.0)
    return sb + coil + hv


def hs_geom(n, section=False):
    """shared extrusion of the n-phase build, or the 1/n section one phase sees (same R_sa x n at 1/n of the flow)"""
    g = {"n_fin": HS["n_fin"], "h_fin": HS["h_fin"], "t_fin": HS["t_fin"], "length": HS["length"][n], "width": HS["width"]}
    if section:
        g.update(n_fin=g["n_fin"] / n, width=g["width"] / n)
    return g


def hs_rsa(n, q, rho=1.10):
    rc, ra, _ = tr.heatsink_rth(flow_m3h=q, rho=rho, **hs_geom(n))
    return rc + ra


def hs_calibrate():
    """fin count giving R_sa = 0.060 K/W at 400 m3/h (45 C air) on the 3-phase extrusion"""
    lo, hi = 10.0, 80.0
    for _ in range(50):
        HS["n_fin"] = 0.5 * (lo + hi)
        lo, hi = (HS["n_fin"], hi) if hs_rsa(3, R_SA_SPEC[3][1]) > R_SA_SPEC[3][0] else (lo, HS["n_fin"])
    HS["mass_kg"] = {n: 2700 * HS["length"][n] * (HS["width"] * HS["t_base"] + HS["n_fin"] * HS["h_fin"] * HS["t_fin"]) for n in MODULES}


hs_calibrate()


# ------------------------------------------------------------------------------------------------ fans and airflow
def fan_dp(fan, q_m3h, s, rho):
    """fan pressure at flow q (m3/h) and speed fraction s (affinity laws), scaled with density"""
    pts = dv.FANS[fan]["pq"]
    q0 = q_m3h / max(s, 1e-6)
    if q0 >= pts[-1][0]:
        return -1e3 * (q0 - pts[-1][0] + 1.0)
    return s * s * rho / RHO_DS * float(np.interp(q0, [p[0] for p in pts], [p[1] for p in pts]))


def sys_dp(q_hs, ncell, rho):
    """pressure drop of the air path for total flow q_hs (m3/h): the shared extrusion of the ncell-phase build + lumped rest"""
    v = q_hs / 3600.0 / (DUCT_W * DUCT_H)
    return tr.heatsink_dp(q_hs, rho, **hs_geom(ncell)) + K_REST * 0.5 * rho * v * v


def operating_point(fan, ncell, s, rho, n_fail=0, flap="no flap"):
    """total heatsink flow (m3/h) where the N_FANS fans' curve meets the system; dead fans leak backwards"""
    nw = N_FANS - n_fail

    def leak(dp):
        return n_fail * A_FAN * math.sqrt(2 * max(dp, 0.0) / (rho * K_LEAK[flap])) * 3600.0

    def g(q):
        dp = sys_dp(q, ncell, rho)
        return fan_dp(fan, (q + leak(dp)) / nw, s, rho) - dp
    lo, hi = 0.0, N_FANS * dv.FANS[fan]["q_free"] * 1.5
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


def module_state(m, va, vb, p, t_in=25.0, alt=0.0, fan=None, speed=None, n_fail=0, flap="no flap"):
    """steady state of the module at (va, vb, p_out) with the fan law (speed=None) or a fixed speed fraction"""
    des, n = m["des"], m["n"]
    fan = fan or m["fan"]
    rho = air_density(alt, t_in)
    s = 1.0 if speed is None else speed
    for _ in range(40 if speed is None else 1):
        op = operating_point(fan, n, s, rho, n_fail, flap)
        qc = op["q_hs"] / n                        # each phase's share of the shared extrusion and of the air
        r = tr.cell_losses(dict(des, flow=max(qc, 1.0), rho=rho, hs_geom=hs_geom(n, True), aux_external=True), va, vb, p / n,
                           t_air=t_in)
        lc = r["loss"]
        p_semi = lc["cond"] + lc["sw"] + lc["dead"]
        dt_air_hs = p_semi / (rho * CP_AIR * max(qc, 1.0) / 3600.0)
        h_ind = H_IND0 * (rho * qc / (1.10 * 150.0)) ** 0.6
        t_ind = t_in + dt_air_hs + tr.ind_hot_rise(lc["core"] + lc["cu"], H_IND0 / max(h_ind, 1.0))   # magnetics hot spot
        if speed is None:
            s_new = min(max(fan_law(r["t_sink"], t_ind), dv.FANS[fan].get("s_min", 0.0)), m.get("s_cap", 1.0))
            if abs(s_new - s) < 2e-3:
                s = s_new
                break
            s = 0.5 * s + 0.5 * s_new
    cap = des["caps"]["A"]
    i_cap = max(r["st"]["icap_rms_A"], r["st"]["icap_rms_B"]) / cap["n"]
    t_cap = t_in + DT_CAP_PRE + cap["esr"] * i_cap ** 2 * dv.C4AQ[cap["part"]]["rth"]
    i_b = abs(p) / vb
    n_run = N_FANS - n_fail
    fans = n_run * fan_power(fan, s)
    va_aux = max(va, vb)
    loss = {"cells": n * r["ptot"], "port": 0.0, "ctrl_aux": aux_in(n, va_aux, 0.0) + p_hv(va, vb),
            "fans": aux_in(n, va_aux, fans) - aux_in(n, va_aux, 0.0)}
    for _ in range(50):       # input port current carries the output power plus every loss in the enclosure
        i_a = (abs(p) + sum(loss.values())) / va
        new_port = i_a ** 2 * R_PORT_A + i_b ** 2 * R_PORT_B
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
            "i_a": i_a, "i_b": i_b, "cell": r, "fan_w": fans, "r_sa": hs_rsa(n, q_tot, rho)}


def limits_ok(st, m):
    return (st["tj"] <= pdz.TJ_DESIGN and st["t_ind"] <= min(T_IND_MAX, T_IND_TRIP[0]) and st["t_cap"] <= st["t_cap_allowed"]
            and st["t_sink"] <= T_SINK_TRIP[0])


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


def port_cap(n, va, vb):
    """fraction of the point's P_max the lean battery port (B) carries: I_B <= LEAN_I_B (no limit on the PV port: 300 A contactor)"""
    return min(1.0, LEAN_I_B[n] * vb / (n * tr.p_limit(va, vb)))


def port_frac(n, t_in):
    """lean-port limit as a fraction of the port rating (flat in inlet temperature, see LEAN_I_B)"""
    return min(1.0, LEAN_I_B[n] / (n * tr.I_MAX))


def holdable(m, t_in, alt=0.0, n_fail=0, flap="no flap", fan=None, port=True):
    """largest fraction of the module P_max that every DERATE_PTS point holds with fans at 100 % (or the cap), within the
    component limits, below both OT trip bands and (port=True) within the lean battery-port current"""
    worst = 1.0
    s_top = m.get("s_cap", 1.0)
    for va, vb in DERATE_PTS:
        top = port_cap(m["n"], va, vb) if port else 1.0
        pmax = m["n"] * tr.p_limit(va, vb)

        def ok(f):
            return limits_ok(module_state(m, va, vb, f * pmax, t_in, alt, fan, s_top, n_fail, flap), m)
        if ok(top):
            worst = min(worst, top)
            continue
        lo, hi = 0.0, top
        for _ in range(11):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if ok(mid) else (lo, mid)
        worst = min(worst, lo)
    return worst


# ------------------------------------------------------------------------------------------------ passive cooling (PCM-17, D-045, risk A4)
# The Delta fans are rated -10..+60 C: below -10 C inlet the fans stay OFF (firmware) and the module runs on what it dissipates without
# forced air. ESTIMATES throughout (+-50 %, no bench data): natural convection between the fins and on the inductor, an enclosure that
# loses its heat through its walls only - NO credit for a chimney draft through the stopped fans and the grilles (orientation unknown),
# so the figures err on the low side of the real capability and the firmware override below covers the rest.
T_COLD = (-30.0, -20.0, -10.0)       # inlet (outside air) temperatures of the table, C; -10 C is the fans' own limit
G_ACC, SIGMA = 9.81, 5.670e-8
EPS_HS, F_RAD_HS = 0.10, 0.25        # bare aluminium extrusion (eps ~0.1): radiation from inside the fin channels reaches the walls only in part
EPS_IND, F_RAD_IND, H_IND_VERT = 0.85, 0.5, 0.075   # toroid: enamel / powder core; half of its radiation reaches the walls; vertical extent, m
H_ENC_IN, H_ENC_OUT, A_ENC = 6.0, 8.0, 0.70          # W/m2K inside (natural convection + radiation to the walls), outside (4 convection + 4 radiation
#                                                      at -30 C), m2 of the 550 x 444 x 133 mm enclosure with the back face against the wall
C_MODULE = 30.0 * 800.0              # J/K: PV-13's 30 kg x 0.8 kJ/(kg K) (mixed Al / Cu / steel / FR4), the structure the inlet air follows
V_AIR_FREE = 0.5 * 0.550 * 0.444 * 0.133   # m3 of free air inside (half of the enclosure volume)
T_SINK_PASSIVE, T_IND_PASSIVE = 75.0, 130.0   # C the passive power keeps the heatsink NTC / the inductor hot spot below (steady state)
T_SINK_START, T_IND_START = 80.0, 135.0       # C: firmware starts the fans at any heatsink / inductor NTC above this, whatever the inlet reads
FAN_ALT_USD = {"9GT": (88.0, 112.92), "4414": (33.94, 78.89)}   # USD per fan, low / high: Sanyo 88 (stock dealer) / 112.92 (DigiKey 21+) from gen/data/prices.csv;
#   ebm-papst 4414/2HHP = web-search snippet of 2026-10-05 (Octopart range, NOT verified on the pages, not in prices.csv)
F_PASSIVE_MIN = 0.05                 # of P_max: the lowest load the passive power is searched from (at exactly 0 the model still switches)
EQUAL_V = (611, 611)                 # the DERATE_PTS corner with V_A = V_B: both legs hard-switch, light-load losses 150-300 W (no passive power)
PASSIVE_K = {"nominal": (1.0, 1.0, 1.0), "pessimistic": (1.5, 1.3, 0.7), "optimistic": (0.7, 0.8, 1.4)}   # (R_sa,nc x, R_ind,nc x, G_enc x)


def air_props(t_c):
    """k, nu, alpha of air at t_c (C), 1 atm"""
    T = t_c + 273.15
    rho = 101325.0 / (287.05 * T)
    k = 0.0241 * (T / 273.15) ** 0.81
    return k, 1.458e-6 * T ** 1.5 / (T + 110.4) / rho, k / (rho * CP_AIR)


def nat_rsa(n, dts, t_air):
    """sink-to-enclosure-air resistance (K/W) of the n-phase extrusion in natural convection (fins vertical, chimney length = the fin
    length): Bar-Cohen and Rohsenow's composite correlation for isothermal parallel plates + straight-fin efficiency + the hindered
    radiation of a bare aluminium fin array; dts = sink excess over the air, K. ESTIMATE"""
    g = hs_geom(n)
    nf, h, t, L, W = g["n_fin"], g["h_fin"], g["t_fin"], g["length"], g["width"]
    s = W / nf - t
    tf = t_air + 0.5 * dts
    k, nu, al = air_props(tf)
    T = tf + 273.15
    el = G_ACC / T * max(dts, 1.0) * s ** 4 / (nu * al * L)           # Elenbaas number
    hc = (576.0 / el ** 2 + 2.873 / el ** 0.5) ** -0.5 * k / s
    htot = hc + F_RAD_HS * 4 * EPS_HS * SIGMA * T ** 3
    m = math.sqrt(2 * htot / (200.0 * t))
    eta = math.tanh(m * h) / (m * h)
    return 1.0 / (htot * eta * (nf * 2 * h * L + W * L))


def nat_h_ind(dts, t_air):
    """toroid surface coefficient (W/m2K) in natural convection + radiation; the forced-air model uses H_IND0 = 25 at 150 m3/h. ESTIMATE"""
    T = t_air + 0.5 * dts + 273.15
    hc = 1.42 * (max(dts, 1.0) / H_IND_VERT) ** 0.25                  # laminar vertical surface
    hr = F_RAD_IND * EPS_IND * SIGMA * ((T + dts / 2) ** 2 + (T - dts / 2) ** 2) * 2 * T
    return hc + hr


def g_enc(k=1.0):
    """enclosure to ambient conductance (W/K): inside and outside films in series over the wall area. ESTIMATE"""
    return k * A_ENC / (1 / H_ENC_IN + 1 / H_ENC_OUT)


def passive_state(m, va, vb, p, t_amb, k=PASSIVE_K["nominal"]):
    """steady state with the fans OFF at (va, vb, p) and outside air t_amb: the cell losses with the natural-convection heatsink resistance
    (tr.heatsink_rth replaced for the call), the inductor hot spot from its natural-convection h, and the enclosure air that all the heat
    has to reach through the walls. The losses rise with temperature, so the heat balance can have two solutions (or none: runaway); the
    iteration starts from the cold side and, being monotone in the enclosure air temperature, ends on the LOWEST one - the state the
    module reaches from a cold start; 'ok' False = no solution below 150 K above the outside air (runaway)"""
    n, des = m["n"], m["des"]
    t_air, dts, dti, ok = t_amb, 1.0, 1.0, False
    orig = tr.heatsink_rth
    try:
        for it in range(400):
            r_sec = nat_rsa(n, dts, t_air) * n * k[0]                  # a 1/n section of the sink at 1/n of the heat: n x the whole sink
            tr.heatsink_rth = lambda r_sec=r_sec, **kw: (r_sec, 0.0, 0.0)
            r = tr.cell_losses(dict(des, flow=100.0, rho=1.2, hs_geom=hs_geom(n, True), aux_external=True), va, vb, p / n, t_air=t_air)
            lc = r["loss"]
            dt_ind = tr.ind_hot_rise(lc["core"] + lc["cu"], H_IND0 / nat_h_ind(dti, t_air) * k[1])
            va_aux, i_b = max(va, vb), abs(p) / vb
            loss = {"cells": n * r["ptot"], "ctrl_aux": aux_in(n, va_aux, 0.0) + p_hv(va, vb), "port": 0.0}
            for _ in range(50):
                i_a = (abs(p) + sum(loss.values())) / va
                new_port = i_a ** 2 * R_PORT_A + i_b ** 2 * R_PORT_B
                if abs(new_port - loss["port"]) < 1e-9:
                    break
                loss["port"] = new_port
            ptot = sum(loss.values())
            t_new, dts_new = t_amb + ptot / g_enc(k[2]), r["t_sink"] - t_air
            if abs(t_new - t_air) < 0.01 and abs(dts_new - dts) < 0.01 and abs(dt_ind - dti) < 0.01:
                ok = True
                break
            if t_new - t_amb > 150.0:
                break
            t_air, dts, dti = t_new, 0.5 * (dts + max(dts_new, 1.0)), 0.5 * (dti + max(dt_ind, 1.0))
    finally:
        tr.heatsink_rth = orig
    cap = des["caps"]["A"]
    i_cap = max(r["st"]["icap_rms_A"], r["st"]["icap_rms_B"]) / cap["n"]
    return {"ok": ok, "t_air": float(t_air), "t_sink": float(r["t_sink"]), "tj": float(r["tj_max"]), "t_ind": float(t_air + dt_ind),
            "ptot": float(ptot), "t_cap": float(t_air + cap["esr"] * i_cap ** 2 * dv.C4AQ[cap["part"]]["rth"]),
            "p_ind": float(lc["core"] + lc["cu"]), "p_sink": float(lc["cond"] + lc["sw"] + lc["dead"]), "r_sa_nc": float(r_sec / n)}


def passive_ok(st, va, vb):
    return (st["ok"] and st["tj"] <= pdz.TJ_DESIGN and st["t_sink"] <= T_SINK_PASSIVE and st["t_ind"] <= T_IND_PASSIVE
            and st["t_cap"] <= cap_allowed(max(va, vb)))


def passive_by_point(m, t_amb, k=PASSIVE_K["nominal"]):
    """for every DERATE_PTS point: (W it holds with the fans OFF within the passive limits, fraction of that point's P_max, state at that
    load) - bisection on the fraction from F_PASSIVE_MIN, the lowest load searched; 0 W = even F_PASSIVE_MIN needs the fans"""
    n, out = m["n"], {}
    for va, vb in DERATE_PTS:
        pmax, top = n * tr.p_limit(va, vb), port_cap(n, va, vb)
        st_lo = passive_state(m, va, vb, F_PASSIVE_MIN * pmax, t_amb, k)
        if not passive_ok(st_lo, va, vb):
            f, st = 0.0, st_lo
        elif passive_ok(passive_state(m, va, vb, top * pmax, t_amb, k), va, vb):
            f, st = top, passive_state(m, va, vb, top * pmax, t_amb, k)
        else:
            lo, hi = F_PASSIVE_MIN, top
            for _ in range(9):
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if passive_ok(passive_state(m, va, vb, mid * pmax, t_amb, k), va, vb) else (lo, mid)
            f, st = lo, passive_state(m, va, vb, lo * pmax, t_amb, k)
        out[(va, vb)] = (f * pmax, f, st)
    return out


def warmup_s(ptot, c, k_g=1.0, d_t=20.0):
    """time for the lumped capacity c (J/K) to rise by d_t K above the outside air when ptot (W) is dissipated and the enclosure loses
    g_enc (W/K) to it: c / g x ln(ptot / (ptot - g d_t)); None when the steady state stays below d_t"""
    g = g_enc(k_g)
    return None if ptot <= g * d_t else c / g * math.log(ptot / (ptot - g * d_t))


def override_minutes(n, st, t_amb, horizon=6 * 3600.0, dt=10.0):
    """minutes from a cold soak at t_amb until the heatsink NTC reaches T_SINK_START or the inductor NTC T_IND_START (the firmware then starts
    the fans whatever the inlet reads) when the module dissipates the losses of the steady state `st`: three lumped nodes (heatsink,
    inductors, enclosure structure + air) with the steady-state conductances; ESTIMATE. None = not within the horizon"""
    c_s = HS["mass_kg"][n] * 900.0
    c_i = n * tr.IND_DESIGN["mass_kg"] * 500.0
    c_a = C_MODULE - c_s - c_i
    p_s, p_i = n * st["p_sink"], n * st["p_ind"]
    p_r = st["ptot"] - p_s - p_i
    g_s = 1.0 / st["r_sa_nc"]
    g_i = p_i / max(st["t_ind"] - st["t_air"], 1.0)
    ts = ti = ta = t_amb
    for k in range(int(horizon / dt)):
        qs, qi = g_s * (ts - ta), g_i * (ti - ta)
        ts += dt * (p_s - qs) / c_s
        ti += dt * (p_i - qi) / c_i
        ta += dt * (p_r + qs + qi - g_enc() * (ta - t_amb)) / c_a
        if ts >= T_SINK_START or ti >= T_IND_START:
            return (k + 1) * dt / 60.0
    return None


def cold_start(res):
    """PCM-17: the passive power of each build at -30 / -20 / -10 C inlet for each corner point, its sensitivity, the warm-up time of the air
    near the fan inlet from -30 to -10 C at that power, the time the override takes where no passive power exists, and the firmware rule;
    all ESTIMATES"""
    out = {}
    for n, r in res.items():
        m, rows = r["m"], []
        for t in T_COLD:
            pts = passive_by_point(m, t)
            lim = min((q for q in pts if q != EQUAL_V), key=lambda q: pts[q][0])      # the lowest corner point away from V_A = V_B
            sens = {tag: min(v[0] for q, v in passive_by_point(m, t, PASSIVE_K[tag]).items() if q != EQUAL_V) / 1e3
                    for tag in ("pessimistic", "optimistic")}
            by_pt = {"%.0f/%.0f" % q: {"kW": round(v[0] / 1e3, 1), "fraction_of_P_max": round(v[1], 3), "enclosure_air_C": round(v[2]["t_air"], 1),
                                       "heatsink_C": round(v[2]["t_sink"], 1), "junction_C": round(v[2]["tj"], 1),
                                       "inductor_hot_spot_C": round(v[2]["t_ind"], 1), "module_loss_W": round(v[2]["ptot"], 1),
                                       "override_after_min": (None if v[0] > 0 else (lambda x: None if x is None else round(x, 0))(
                                           override_minutes(n, v[2], t)))} for q, v in pts.items()}
            rows.append({"inlet_C": t, "kW": round(pts[lim][0] / 1e3, 1), "limiting_point_V": list(lim), "kW_by_point": by_pt,
                         "kW_at_equal_voltages": round(pts[EQUAL_V][0] / 1e3, 1),
                         "kW_pessimistic": round(sens["pessimistic"], 1), "kW_optimistic": round(sens["optimistic"], 1),
                         "module_loss_W": round(pts[lim][2]["ptot"], 1), "R_sa_natural_K_W": round(pts[lim][2]["r_sa_nc"], 3)})
        p30 = rows[0]["module_loss_W"]                                  # module loss at the passive limit, -30 C
        c_air = 1.45 * V_AIR_FREE * 718.0
        w_s, w_a = warmup_s(p30, C_MODULE), warmup_s(p30, c_air)
        out[MODULES[n]] = {"table": rows, "R_sa_forced_K_W": round(hs_rsa(n, r["op45"]["q_hs"]), 4),
                           "warmup_minus30_to_minus10_C_min": {"structure_and_air": None if w_s is None else round(w_s / 60, 0),
                                                              "air_only_s": None if w_a is None else round(w_a, 1),
                                                              "at_module_loss_W": round(p30, 0)}}
    return out


# ------------------------------------------------------------------------------------------------ run
def s_cap_of(fan, n):
    """speed cap: all N_FANS within NOISE_MAX, and the per-fan power cap of the build (fan power ~ speed^3 without p_min_w)"""
    cap = noise_cap(fan, N_FANS)
    return min(cap, (FAN_P_CAP[n] / dv.FANS[fan]["p_w"]) ** (1 / 3)) if n in FAN_P_CAP else cap


EU_W = [(0.05, 0.03), (0.10, 0.06), (0.20, 0.13), (0.30, 0.10), (0.50, 0.48), (1.00, 0.20)]   # European weighting (of rated)


def eu_eff(m, va, vb, t_in=25.0):
    n = m["n"]
    top = n * tr.p_limit(va, vb) * port_cap(n, va, vb)
    return sum(w * module_state(m, va, vb, min(f * n * tr.P_RATED, top), t_in)["eff"] for f, w in EU_W)


def bank_study(des, n, f_cv):
    """D-052(a): smallest interleaved common film bank per port (PV-PWR part: Jianghai FCSA3DS456 45 uF, I_rms max 22.1 A at
    85 C; 12 parts = 65 USD for PV-P75) from: ripple current <= 50 % of the summed rating (hand-off rule), dV_pp <= 1 % of V,
    load rejection on the battery port (current n x 45 A until the OV trip acts, then the inductor energy) <= V_OV_LIMIT"""
    c1, i1, usd1 = 45e-6, 22.1, 65.0 / 12
    rows = pdz.module_ripple(des, n)
    l45 = tr.inductor_L(des["ind"], tr.I_MAX)
    q_l = n * l45 * tr.I_MAX ** 2 / (2 * pdz.OV_HW[1])                  # inductor energy into port B after the gates are off
    i_lr = min(n * tr.I_MAX, LEAN_I_B[n])
    drawn = drawn_bank(n)
    out = {}
    for port in "AB":
        rr = [r for r in rows if r["port"] == port]
        need = {"ripple_current": max(r["irms_ac"] for r in rr) / (0.5 * i1) * c1,
                "voltage_ripple_1pct": max(r["dv_pp"] * r["C_total"] / (0.01 * r["v"]) for r in rr)}
        if port == "B":
            for tag, v_trip in (("load_rejection", pdz.OV_HW[1]), ("load_rejection_at_1100V_nominal", 1100.0)):
                need[tag] = (i_lr * pdz.OV_HW[2] + q_l) / (V_OV_LIMIT - v_trip)
        n_min = max(math.ceil(c / c1 - 1e-6) for k, c in need.items() if k != "load_rejection_at_1100V_nominal")
        x = {"need_uF": {k: round(v * 1e6, 1) for k, v in need.items()}, "parts_min": n_min, "parts_today": drawn[port],
             "C_min_uF": n_min * c1 * 1e6, "binding": max((k for k in need if k != "load_rejection_at_1100V_nominal"), key=need.get),
             "i_rms_ac_max_A": round(max(r["irms_ac"] for r in rr), 1)}
        if port == "B":   # OV response that would let the bank fall to the ripple / voltage-ripple minimum
            c_other = math.ceil(max(need["ripple_current"], need["voltage_ripple_1pct"]) / c1 - 1e-6) * c1
            x["ov_response_for_that_us"] = round(max((c_other * (V_OV_LIMIT - pdz.OV_HW[1]) - q_l) / i_lr, 0.0) * 1e6, 1)
            x["parts_with_that_response"] = round(c_other / c1)
            v_b = 550.0                                       # CPL pole at the low end of the full-power window, for the record
            p_b = min(n * tr.p_limit(v_b, v_b), LEAN_I_B[n] * v_b)
            x["cpl_pole_Hz_at_550V"] = {"today": round(p_b / (2 * math.pi * v_b ** 2 * drawn["B"] * c1), 0),
                                        "minimum": round(p_b / (2 * math.pi * v_b ** 2 * n_min * c1), 0), "f_cv_Hz": f_cv}
        out[port] = x
    out["saving_usd"] = round(sum(out[p_]["parts_today"] - out[p_]["parts_min"] for p_ in "AB") * usd1, 1)
    out["basis"] = (f"FCSA3DS456 45 uF {i1} A rms (85 C), {usd1:.2f} USD each (65 USD / 12, coordinator); OV trip "
                    f"{pdz.OV_HW[0]:.0f}-{pdz.OV_HW[1]:.0f} V, {pdz.OV_HW[2]*1e6:.0f} us response, limit {V_OV_LIMIT:.0f} V; "
                    f"load-rejection current {i_lr:.0f} A; ripple from pv_design.module_ripple (interleaved 360/n deg)")
    return out


def fsw_study(m, peak):
    """D-052(b): switching frequency with the ripple ratio held (L x 32 kHz / f): identical waveforms, so per-edge device energies
    stay and the device switching-related losses (sw, dead time, damper) and the gate-charge part of the bias scale with f.
    Inductor (ESTIMATE, upper bound of the saving): rev M2 loss held, core + copper cost ~ area product ~ L^0.75, insulation and
    labour fixed, the design file's margin kept.  Heatsink (ESTIMATE): cost ~ 1/R_sa (24.3 USD at the spec limit) when Tj at 45 C
    would pass 125 C."""
    des, n = m["des"], m["n"]
    c = tr.IND_DESIGN["cost"]
    var, fix = c["core_usd"] + c["copper_usd"], c["insulation_usd"] + c["labour_usd"]
    marg = c["total_usd"] / (var + fix)
    va, vb, p = peak["va"], peak["vb"], peak["p"]
    base = None
    rows = []
    for f in (24e3, 32e3, 40e3, 48e3, 64e3):
        k = f / pdz.FROZEN["fsw"]
        mk = dict(m, des=dict(des, fsw=f, ind=dict(des["ind"], al=des["ind"]["al"] / k)))
        g0 = tr.cell_losses(dict(des, aux_external=False), va, vb, p / n)["loss"]["gate"]
        hot = [module_state(mk, a_, b_, n * tr.p_limit(a_, b_) * port_cap(n, a_, b_), 45.0, speed=m["s_cap"]) for a_, b_ in DERATE_PTS]
        h = max(hot, key=lambda x: x["tj"])
        st = module_state(mk, va, vb, p, 25.0)
        st1 = module_state(m, va, vb, p, 25.0)
        ind_k = sum(st["cell"]["loss"][q] for q in ("core", "cu"))
        ind_1 = sum(st1["cell"]["loss"][q] for q in ("core", "cu"))
        loss = st["ptot"] - n * ind_k + n * ind_1 + n * g0 * (k - 1) / ETA_5V / aux_eta(n, max(va, vb))
        p_semi = n * sum(h["cell"]["loss"][q] for q in ("cond", "sw", "dead"))
        jsink = h["tj"] - h["t_sink"]
        r_need = min((pdz.TJ_DESIGN - jsink - 45.0) / p_semi, (T_SINK_TRIP[0] - 45.0) / p_semi)   # Tj limit and no OT trip at 45 C
        hs_x = max(h["r_sa"] / r_need - 1.0, 0.0) * 24.3
        row = {"fsw_kHz": f / 1e3, "L_45A_uH": round(tr.inductor_L(mk["des"]["ind"], tr.I_MAX) * 1e6, 1),
               "inductor_usd_per_phase": round(marg * (fix + var * k ** -0.75), 1), "peak_eff": round(p / (p + loss), 5),
               "loss_at_peak_point_W": round(loss, 1), "tj_max_45C_C": round(h["tj"], 1), "t_sink_45C_C": round(h["t_sink"], 1),
               "heatsink_extra_usd": round(hs_x, 1), "d_max": round(1 - tr.T_MIN_PULSE * f, 4)}
        rows.append(row)
        if f == pdz.FROZEN["fsw"]:
            base = row
    for row in rows:
        row["module_cost_delta_usd"] = round(n * (row["inductor_usd_per_phase"] - base["inductor_usd_per_phase"]) +
                                             row["heatsink_extra_usd"] - base["heatsink_extra_usd"], 1)
        row["fits_todays_heatsink"] = row["tj_max_45C_C"] <= pdz.TJ_DESIGN and row["t_sink_45C_C"] <= T_SINK_TRIP[0]
        row["ok"] = row["peak_eff"] >= 0.99      # with the heatsink grown as costed
    return rows


def run():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(OUT, exist_ok=True)
    cell_spec = json.load(open(os.path.join(OUT, "cell_spec.json")))
    role = cell_spec["module_basis_role"]      # the device of the pair with the lower corner efficiency (a cell_spec without it is stale)
    des, rec, sel, pick, target = pdz.choose(role)
    j = tr.IND_DESIGN
    ind_used = {"revision": j["revision"], "construction": j["construction"], "conductor": j["windings"][0]["conductor"],
                "cost_usd": j["cost"]["total_usd"], "T_hotspot_C": j["thermal"]["T_hotspot_C"],
                "worst_loss_W": round(j["thermal"]["dT_hotspot_K"] / j["thermal"]["Rth_hotspot_to_air_K_W"], 1)}
    print(f"inductor used: rev {ind_used['revision']}, {ind_used['conductor']}, worst {ind_used['worst_loss_W']} W, hot spot "
          f"{ind_used['T_hotspot_C']:.0f} C, {ind_used['cost_usd']} USD")

    # ---- fan comparison on the 3-phase build (the as-built fan is fixed by the boards: FAN_OF)
    fan_rows = []
    for fan in dv.FANS:
        m = {"des": des, "n": 3, "fan": fan, "s_cap": s_cap_of(fan, 3)}
        full = holdable(m, 45.0, fan=fan)
        st = module_state(m, 611, 611, 3 * tr.p_limit(611, 611), 45.0, fan=fan)
        fan_rows.append({"fan": fan, "hold45": full, "noise_full45": st["noise"], "s_full45": st["s"], "q_cell": st["q_cell"],
                         "fail_flap": holdable(m, 45.0, n_fail=1, flap="flap", fan=fan),
                         "fail_noflap": holdable(m, 45.0, n_fail=1, flap="no flap", fan=fan),
                         "i_ok": N_FANS * dv.FANS[fan]["p_w"] <= 36.0 + 1e-9, "p_w": dv.FANS[fan]["p_w"],
                         "cold_ok": dv.FANS[fan]["t_amb"][0] <= T_INLET_MIN_REQ})
    choice = FAN_OF[3]

    res = {}
    for n, name in MODULES.items():
        m = {"des": des, "n": n, "fan": FAN_OF[n], "name": name, "s_cap": s_cap_of(FAN_OF[n], n)}
        corners = {}
        for t_in in (25.0, 45.0):
            for va, vb in tr.CORNERS + [(611, 950), (950, 611), (611, 611)]:
                corners[f"{va}->{vb}@{t_in:.0f}C"] = module_state(m, va, vb, n * tr.p_limit(va, vb) * port_cap(n, va, vb), t_in)
        vs = np.arange(tr.V_MIN, tr.V_MAX + 0.1, 50.0)
        grid = [module_state(m, va, vb, n * tr.p_limit(va, vb) * port_cap(n, va, vb), 25.0) for va in vs for vb in vs]
        best = None
        for va in np.arange(300.0, 1001.0, 100.0):
            for vb in np.arange(300.0, 1001.0, 100.0):
                for fr in (0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
                    stt = module_state(m, va, vb, fr * n * tr.p_limit(va, vb) * port_cap(n, va, vb), 25.0)
                    if best is None or stt["eff"] > best["eff"]:
                        best = stt
        temps = [25, 30, 35, 40, 45, 50, 55, 60]
        der = {"sea level": [holdable(m, t) for t in temps], "3000 m": [holdable(m, t, 3000.0) for t in temps],
               "1 fan failed (flap)": [holdable(m, t, n_fail=1, flap="flap") for t in temps],
               "1 fan failed (no flap)": [holdable(m, t, n_fail=1, flap="no flap") for t in temps]}
        der_thermal = [holdable(m, t, port=False) for t in temps]
        alts = [0, 1000, 2000, 3000, 4000]
        alt45 = [holdable(m, 45.0, a) for a in alts]
        alt60 = [holdable(m, 60.0, a) for a in alts]
        worst_combo = holdable(m, 60.0, 3000.0, n_fail=1, flap="no flap")
        sens = sensitivity(m, temps)
        load_tab = {t: [module_state(m, 611, 950, f * n * tr.p_limit(611, 950), t) for f in (0.25, 0.5, 0.75, 1.0)] for t in (25.0, 45.0)}
        loads = np.linspace(0.05, 1.0, 20)
        prof = {t: [module_state(m, 611, 950, f * n * tr.p_limit(611, 950), t) for f in loads] for t in (25.0, 45.0)}
        hot = {t: max((module_state(m, va, vb, n * tr.p_limit(va, vb) * port_cap(n, va, vb), t, speed=m["s_cap"]) for va, vb in DERATE_PTS),
                      key=lambda x: x["tj"]) for t in (45.0, 60.0)}
        hot_np = max((module_state(m, va, vb, n * tr.p_limit(va, vb), 45.0, speed=m["s_cap"]) for va, vb in DERATE_PTS),
                     key=lambda x: (x["t_ind"] - T_IND_TRIP[0], x["tj"]))      # full current, no port cap
        hot_ind = {t: max((module_state(m, va, vb, n * tr.p_limit(va, vb) * port_cap(n, va, vb), t, speed=m["s_cap"]) for va, vb in DERATE_PTS),
                          key=lambda x: x["t_ind"]) for t in (45.0, 60.0)}
        sv = sorted(int(v) for v in AUX["standby"][name])
        stby = {"V": sv, "contactors_open_W": [round(standby(n, v, False), 1) for v in sv],
                "contactors_held_W": [round(standby(n, v, True), 1) for v in sv]}
        eu_v = (550, 750, 950)
        eu = {f"{va}->{vb}": eu_eff(m, va, vb) for va in eu_v for vb in eu_v}
        res[n] = {"m": m, "corners": corners, "grid": grid, "vs": vs, "peak": best, "temps": temps, "der": der, "alts": alts,
                  "alt45": alt45, "alt60": alt60, "worst_combo": worst_combo, "sens": sens, "load_tab": load_tab,
                  "loads": loads, "prof": prof, "standby": standby(n, 1000, True), "stby": stby, "eu": eu, "hot": hot,
                  "hot_ind": hot_ind, "der_thermal": der_thermal, "hot_np": hot_np,
                  "op45": module_state(m, 611, 611, n * tr.p_limit(611, 611) * port_cap(n, 611, 611), 45.0, speed=m["s_cap"])}

    f_cv = json.load(open(CTRL_SPEC))["outer_loops"]["design_crossover_Hz"]
    for n, r in res.items():
        r["bank"] = bank_study(des, n, f_cv)
    fsw = fsw_study(res[3]["m"], res[3]["peak"])
    cold = cold_start(res)

    # ---------------------------------------------------------------- self-check (physics)
    assert abs(hs_rsa(3, R_SA_SPEC[3][1]) - R_SA_SPEC[3][0]) < 1e-5, "extrusion calibrated to the spec R_sa"
    for n in MODULES:
        q = 400.0
        rc, ra, _ = tr.heatsink_rth(flow_m3h=q / n, rho=1.10, **hs_geom(n, True))
        assert abs((rc + ra) / n - hs_rsa(n, q)) < 1e-9 * hs_rsa(n, q), "a 1/n section at 1/n of the flow carries n x R_sa"
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
            assert st["i_b"] <= LEAN_I_B[n] + 1e-6, "battery-port current within the lean limit"
        at25 = r["grid"] + [v for k, v in r["corners"].items() if k.endswith("@25C")] + r["load_tab"][25.0]
        for st in at25:
            assert limits_ok(st, m), ("fan law leaves a limit exceeded at 25 C", st["va"], st["vb"], st["p"], st["tj"], st["t_ind"])
        for key, curve in list(r["der"].items()) + [("thermal", r["der_thermal"])]:   # derating never increases with temperature
            assert all(curve[k + 1] <= curve[k] + 1e-9 for k in range(len(curve) - 1)), ("derating monotonic", key)
        assert all(r["der"]["sea level"][k] <= r["der_thermal"][k] + 1e-3 for k in range(len(r["temps"]))), "port cap only lowers"   # bisection step
        assert all(r["alt45"][k + 1] <= r["alt45"][k] + 1e-9 for k in range(len(r["alt45"]) - 1)), "altitude monotonic"
        assert r["der"]["1 fan failed (no flap)"][4] <= r["der"]["1 fan failed (flap)"][4] + 1e-9 <= r["der"]["sea level"][4] + 2e-9
        pk = r["peak"]
        assert pk["eff"] < tr.cell_losses(dict(des, aux_external=True), pk["va"], pk["vb"], pk["p"] / n)["eff"], "module < cell"
        assert all(h > o for h, o in zip(r["stby"]["contactors_held_W"], r["stby"]["contactors_open_W"])), "holding costs power"
        assert r["bank"]["B"]["need_uF"]["load_rejection"] > r["bank"]["B"]["need_uF"]["load_rejection_at_1100V_nominal"]
    for name, c in cold.items():       # passive cooling: warmer outside air leaves less power, natural convection is worse than forced air
        kw = [away_from_equal(row) for row in c["table"]]
        assert all(kw[k + 1] <= kw[k] + 0.5 for k in range(len(kw) - 1)), ("passive power falls with the inlet temperature", name, kw)
        assert all(row["R_sa_natural_K_W"] > 2 * c["R_sa_forced_K_W"] for row in c["table"]), "natural-convection R_sa above the forced-air value"
        assert all(row["kW_pessimistic"] <= row["kW"] + 1e-9 <= row["kW_optimistic"] + 2e-9 for row in c["table"]), "sensitivity brackets the nominal"
    assert [x["fsw_kHz"] for x in fsw] == sorted(x["fsw_kHz"] for x in fsw)
    assert all(fsw[k + 1]["loss_at_peak_point_W"] > fsw[k]["loss_at_peak_point_W"] for k in range(len(fsw) - 1)), "loss rises with f"

    write_outputs(res, fan_rows, choice, des, cell_spec, plt, ind_used, fsw, cold)
    return res, fan_rows, choice, fsw


def away_from_equal(r):
    """lowest passive power (kW) of the corner points other than V_A = V_B = 611 V in one table row"""
    return r["kW"]


def cold_text(cold, f):
    """the cold-start restriction of module_spec fan.cold_restriction: what is limited, to how much, and the firmware rule (PCM-17)"""
    pts = "; ".join("%s: %s kW at %s C inlet (lowest of the corner points 950/611, 611/950, 1000/500 V; %s kW at 611/611 V)" % (
        name, " / ".join(str(away_from_equal(r)) for r in c["table"]), " / ".join("%.0f" % r["inlet_C"] for r in c["table"]),
        " / ".join(str(r["kW_at_equal_voltages"]) for r in c["table"])) for name, c in cold.items())
    wu = cold["PV-P75"]["warmup_minus30_to_minus10_C_min"]
    ov = cold["PV-P75"]["table"][0]["kW_by_point"]["611/611"]["override_after_min"]
    return (f"RESTRICTION: both Delta fans are rated {f['t_amb'][0]:.0f}..{f['t_amb'][1]:.0f} C operating (p.3 4-1; storage -40..+75 C); PV-20 asks "
            f"for {T_INLET_MIN_REQ:.0f} C. FIRMWARE RULE: while the inlet NTC reads below {f['t_amb'][0]:.0f} C the fans stay OFF and the module "
            f"delivers at most the passive power of the table row of the inlet reading at start-up (sustained, steady state): {pts} "
            f"(ESTIMATE, +-50 %: natural convection between the fins and on the inductors, an enclosure that loses heat through its walls only, "
            f"no credit for a chimney draft; limits: heatsink {T_SINK_PASSIVE:.0f} C, inductor hot spot {T_IND_PASSIVE:.0f} C, junction "
            f"{pdz.TJ_DESIGN:.0f} C). At V_A = V_B = 611 V (both legs hard-switching) even {F_PASSIVE_MIN*100:.0f} % load exceeds the passive limits in "
            f"steady state: there the start relies on the warm-up, not on a power limit. The fans are enabled once the inlet NTC reads above "
            f"{f['t_amb'][0]:.0f} C - the air near the intake warms from -30 to -10 C in about {wu['structure_and_air']:.0f} min at PV-P75's "
            f"module loss of {wu['at_module_loss_W']:.0f} W (the 30 kg structure it follows; the air alone in {wu['air_only_s']:.0f} s) - and the fans "
            f"start at ANY heatsink NTC above {T_SINK_START:.0f} C or inductor NTC above {T_IND_START:.0f} C whatever the inlet reads (5 K above "
            f"the passive-mode limits, below the 85 / 145 C derating levels of the second layer: a fan outside its rating beats an "
            f"over-temperature trip; at 611/611 V and -30 C that happens after about "
            f"{'more than 6 h' if ov is None else '%.0f min' % ov}). Open until a bench test: the passive capability itself. "
            f"Fan alternatives (the fan selection is NOT changed): Sanyo Denki 9GT1224P1S001 (120 x 120 x 38 mm, -40..+85 C, 26.4 W at 100 %, "
            f"i.e. over the 36 W SELV allocation of 3 fans) is the only fan ON FILE that reaches {T_INLET_MIN_REQ:.0f} C, at {FAN_ALT_USD['9GT'][0]:.0f}-"
            f"{FAN_ALT_USD['9GT'][1]:.0f} USD each against {DELTA_USD:.2f} USD @504 for the Delta (gen/data/prices.csv): "
            f"+{3 * (FAN_ALT_USD['9GT'][0] - DELTA_USD):.0f}..+{3 * (FAN_ALT_USD['9GT'][1] - DELTA_USD):.0f} USD per module (3 fans). "
            f"ebm-papst 4414/2HHP (119 x 119 x 38 mm, 24 V, 12 W, 285 m3/h, -20..+70 C, datasheet p.1 on file) reaches -20 C only, its supply "
            f"range is 18..28 V (p.1; the 7-24 V supply-voltage speed law of the fan buck does not apply to it) and the speed signal is an option; "
            f"price not on file, web-search snippets of 2026-10-05 (NOT verified on the pages, not in prices.csv): "
            f"{FAN_ALT_USD['4414'][0]:.2f}-{FAN_ALT_USD['4414'][1]:.2f} USD (Octopart range), 55.92 USD (eBay, single), 64.59 USD (Newark), i.e. "
            f"+{3 * (FAN_ALT_USD['4414'][0] - DELTA_USD):.0f}..+{3 * (FAN_ALT_USD['4414'][1] - DELTA_USD):.0f} USD per module")


def write_outputs(res, fan_rows, choice, des, cell_spec, plt, ind_used, fsw, cold):
    # ---- plots
    fig, axs = plt.subplots(1, 3, figsize=(16, 4.6))
    for n, r in res.items():
        for key, ls in (("sea level", "-"), ("3000 m", "--"), ("1 fan failed (flap)", ":"), ("1 fan failed (no flap)", "-.")):
            axs[0].plot(r["temps"], [x * n * tr.P_MAX / 1e3 for x in r["der"][key]], ls, label=f"{MODULES[n]} {key}")
    for n in res:
        axs[0].plot([t for t, _ in EVT], [p * n * tr.P_RATED / 1e3 for _, p in EVT], "k.", ms=8)
    axs[0].set_xlabel("inlet air [C]")
    axs[0].set_ylabel("power held [kW]")
    axs[0].set_title("derating (fans at cap, lean port); dots = roadmap EVT curve x rated", fontsize=9)
    axs[0].legend(fontsize=6)
    axs[0].grid(alpha=0.3)
    qs = np.linspace(1, N_FANS * 300.0, 200)
    for n in MODULES:
        f_ = FAN_OF[n]
        axs[1].plot(qs, [fan_dp(f_, q / N_FANS, res[n]["m"]["s_cap"], 1.1) for q in qs], label=f"{N_FANS} x {f_} at cap")
        axs[1].plot(qs, [sys_dp(q, n, 1.1) for q in qs], "--", label=f"system {MODULES[n]}: extrusion + rest")
    axs[1].plot([DP_RULE[1]], [DP_RULE[0]], "rx", label="rule: <= 60 Pa at 420 m3/h")
    axs[1].set_ylim(0, 160)
    axs[1].set_xlabel("total flow [m3/h]")
    axs[1].set_ylabel("dp [Pa]")
    axs[1].legend(fontsize=7)
    axs[1].grid(alpha=0.3)
    r3 = res[3]
    for t, ls in ((25.0, "-"), (45.0, "--")):
        loads = r3["loads"] * 3 * tr.p_limit(611, 950) / 1e3
        axs[2].plot(loads, [x["loss"]["fans"] for x in r3["prof"][t]], ls, label=f"fan power at the input, {t:.0f} C")
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
        ax.set_title(f"{MODULES[n]} module efficiency at P_lim (lean port), 25 C inlet (red 99 %)", fontsize=9)
        ax.set_xlabel("V_A (PV) [V]")
        ax.set_ylabel("V_B (battery) [V]")
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

    # ---- module_spec.json: the existing keys now hold the cost-first build (superseded in place); new items under 'costfirst'
    f = dv.FANS[choice]
    spec = {"title": "PV-P75 / PV-P100/110 module figures, cost-first build (calculated, not measured)", "generated_by": "sim/pv_module.py",
            "cell_spec": "sim/out/pv_design/cell_spec.json", "port_inputs": "sim/out/port_design/port_spec.json (lean block)",
            "fan": {"mpn": choice, "manufacturer": f["mfr"], "datasheet": f["src"], "count_per_cell": None, "count_per_module": N_FANS,
                    "rated_W": f["p_w"], "rated_A": f["i_max"], "channel_limit_A": None,
                    "speed_law": {"min_fraction": S_MIN, "heatsink_C_at_min": T_ON, "heatsink_C_at_full": T_FULL,
                                  "inductor_C_at_min": TI_ON, "inductor_C_at_full": TI_FULL,
                                  "law": "max of two linear ramps: heatsink NTC and inductor NTC; speed by the 7-24 V fan-buck voltage"},
                    "arrangement": (f"{N_FANS} x 120 mm on one shared extrusion for both builds (PV-P100/110: {N_FANS} x {FAN_OF[4]} capped at "
                                    f"{FAN_P_CAP[4]:.0f} W), no backflow shutters; plenum between fans and fins ASSUMED (even spread)"),
                    "ambient_rated_C": list(f["t_amb"]), "PV_20_inlet_C": [T_INLET_MIN_REQ, tr.T_AIR_HOT],
                    "min_speed_fraction": round(max(S_MIN, f.get("s_min", 0.0)), 3),
                    "speed_law_note": (f"supply-voltage control, no PWM input; never below {S_MIN*100:.0f} % (7 V of 24 V ~ 29 %, speed ~ voltage "
                                       f"ASSUMED); capped so that all fans stay <= {NOISE_MAX:.0f} dB(A) at 1 m and within the per-fan power cap"),
                    "cold_restriction": None if f["t_amb"][0] <= T_INLET_MIN_REQ else cold_text(cold, f),
                    "selection": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items()} for x in fan_rows],
                    "selection_note": ("comparison on the 3-phase cost-first module (fans at their cap); i_ok now = 3 fans' rated power "
                                       "fits the 36 W SELV fan allocation (PV-CTL budget); the module's fan is fixed by the boards")},
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
            "efficiency_basis": ("everything inside the enclosure: phases (gate bias excluded there), port conduction (contactors, "
                                 "shunts, busbars, battery-port aR links), aux flyback input (gate bias, logic incl. the control board, "
                                 "contactor coils held, fans through the fan buck), varistor monitors, bleeders, dividers"),
            "efficiency_corners_25C": {k: round(v["eff"], 5) for k, v in c25.items()},
            "efficiency_corners_45C": {k: round(v["eff"], 5) for k, v in c45.items()},
            "losses_by_group_W_611V_611V_full_45C": {k: round(v, 1) for k, v in full45["loss"].items()},
            "heat_rejected_W_max_45C": round(max(v["ptot"] for v in c45.values()), 1),
            "airflow_m3h_full_45C": round(full45["q_hs"], 1), "airflow_per_cell_m3h": round(full45["q_cell"], 1),
            "fan_speed_full_45C": round(full45["s"], 3), "fan_power_W_full_45C": round(full45["fan_w"], 1),
            "fan_speed_cap": round(m["s_cap"], 3), "noise_dBA_1m_all_fans_at_cap": round(noise_1m(m["fan"], m["s_cap"], N_FANS), 1),
            "noise_dBA_1m_all_fans_100pct": round(noise_1m(m["fan"], 1.0, N_FANS), 1),
            "fan_power_W_min": round(N_FANS * fan_power(m["fan"], max(S_MIN, dv.FANS[m["fan"]].get("s_min", 0.0))), 1),
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
            "bus_capacitance_uF": {"port_A": drawn_bank(n)["A"] * 45.0, "port_B": drawn_bank(n)["B"] * 45.0, "port_X_caps_uF": None,
                                   "note": "PV-PWR: FCSA3DS456 45 uF on the common bus as drawn (design check 'Port banks'; PV-P75 rev "
                                           "A2: 6 + 7, the 7th on phase 2, D-056); no separate port-board X caps"},
            "standby_W_estimate": round(r["standby"], 1),
            "port_current_limit_fraction_vs_inlet": [round(port_frac(n, t), 3) for t in r["temps"]],
            "rs3_2415d_cell_board_supply": {"superseded": "no cell boards in the cost-first build (D-044): the LA 150-P and its RS3-2415D "
                                                          "are replaced by STK-HO/A 75 sensors on PV-PWR"},
            "port_variant": {"I_A": n * tr.I_MAX, "fuse": f"battery port only: 2 x {LEAN['battery_fuse']['mpn']} ({LEAN['battery_fuse']['In_A']:.0f} A aR)",
                             "fuse_In_A": LEAN["battery_fuse"]["In_A"], "fuse_derated_A_at_50C": None,
                             "R_port_mOhm": {"PV (A)": round(R_PORT_A * 1e3, 3), "battery (B)": round(R_PORT_B * 1e3, 3)},
                             "battery_port_limit_A": LEAN_I_B[n]}}
    spec["costfirst"] = costfirst_block(res, des, cell_spec, ind_used, fsw, cold)
    fan_usd = FAN_USD[choice][0]
    cell_usd = cell_spec["cost_usd"]["per_cell"]["total"]          # a cell_spec without its cost block is stale: stop
    spec["cost_usd"] = {MODULES[n]: {"cells": round(n * cell_usd, 0),
                                     "fans": round(N_FANS * FAN_USD[FAN_OF[n]][0], 0) if FAN_USD[FAN_OF[n]][0] else None,
                                     "total_cells_and_fans": round(n * cell_usd + N_FANS * (FAN_USD[FAN_OF[n]][0] or 0.0), 0),
                                     "per_kW_rated": round((n * cell_usd + N_FANS * (FAN_USD[FAN_OF[n]][0] or 0.0)) /
                                                           (n * tr.P_RATED / 1e3), 2)} for n in MODULES}
    spec["cost_usd"]["basis"] = (f"cells: cell_spec.json cost_usd (Gate-0b per-cell model, NOT the cost-first boards - gen/cost.py "
                                 f"is the module cost); fans {N_FANS} x {choice} {fan_usd:.2f} USD ({FAN_USD[choice][1]}); "
                                 f"{FAN_OF[4]}: {FAN_USD[FAN_OF[4]][1]}")
    json.dump(spec, open(os.path.join(OUT, "module_spec.json"), "w"), indent=1, default=float, allow_nan=False)
    write_report(res, fan_rows, choice, spec)


def rsa_flow_exponent(n, q, rho=1.10, d=0.02):
    """-d ln R_sa / d ln Q of the n-phase extrusion at the flow q (m3/h): R_sa rises as Q^-exponent when the fans slow (PCM-26)"""
    return -(math.log(hs_rsa(n, q * (1 + d), rho)) - math.log(hs_rsa(n, q * (1 - d), rho))) / (math.log(1 + d) - math.log(1 - d))


def costfirst_block(res, des, cell_spec, ind_used, fsw, cold):
    """everything new for the cost-first build (D-044..D-052) in one place for sim/compare_megarevo.py"""
    cf = {"basis": ("ARCHITECTURE-COSTFIRST sec. 9; hardware/PV-PWR rev A2 / PV-PWR-4 and PV-CTL rev A1 design checks; port_spec "
                    "lean; aux75_spec; calculated, not measured"),
          "superseded_keys": ("modules.* efficiency, losses, airflow, fan, noise, derating, standby_W_estimate (now: contactors held, "
                              "1000 V), port_current_limit_fraction_vs_inlet (lean, flat), bus_capacitance_uF, port_variant, fan.*, "
                              "rs3_2415d_cell_board_supply (marked superseded)"),
          "inductor_used": ind_used,
          "heatsink": {"model": "one extrusion: 444 mm wide, 10 mm base, 40 x 1.5 mm fins; fin count solved for the spec R_sa",
                       "n_fin": round(HS["n_fin"], 1), "fin_pitch_mm": round(HS["width"] / HS["n_fin"] * 1e3, 2),
                       "length_mm": {MODULES[n]: HS["length"][n] * 1e3 for n in MODULES},
                       "mass_kg_model": {MODULES[n]: round(HS["mass_kg"][n], 2) for n in MODULES},
                       "R_sa_spec_K_W": {MODULES[n]: R_SA_SPEC[n][0] for n in MODULES},
                       "R_sa_at_operating_point_K_W": {MODULES[n]: round(res[n]["op45"]["r_sa"], 4) for n in MODULES},
                       "R_sa_flow_exponent": {MODULES[n]: round(rsa_flow_exponent(n, res[n]["op45"]["q_hs"]), 3) for n in MODULES},
                       "R_sa_flow_exponent_note": "R_sa ~ flow^-exponent at the 45 C full-power operating point (same heatsink model as "
                                                  "the spec calibration); used by the PV-PWR thermal line to apply the fan supply "
                                                  "contract's airflow (speed ~ fan voltage, airflow ~ speed: ASSUMED)",
                       "trip_band_C": list(T_SINK_TRIP), "inductor_trip_band_C": list(T_IND_TRIP)},
          "fans_operating_point_45C": {MODULES[n]: {"fan": FAN_OF[n], "speed": round(r["op45"]["s"], 3),
                                                    "flow_m3h_total": round(r["op45"]["q_hs"], 0),
                                                    "flow_per_fan_m3h": round(r["op45"]["q_hs"] / N_FANS, 0),
                                                    "dp_Pa": round(r["op45"]["dp"], 1),
                                                    "system_dp_at_420_m3h_Pa": round(sys_dp(DP_RULE[1], n, 1.10), 1),
                                                    "rule_dp_Pa_at_420_m3h": DP_RULE[0],
                                                    "fan_power_W": round(r["op45"]["fan_w"], 1)} for n, r in res.items()},
          "modules": {}}
    for n, r in res.items():
        h45, h60 = r["hot"][45.0], r["hot"][60.0]
        i45, i60 = r["hot_ind"][45.0], r["hot_ind"][60.0]
        eu = r["eu"]
        cf["modules"][MODULES[n]] = {
            "tj_max_full_power_C": {"45C": round(h45["tj"], 1), "60C": round(h60["tj"], 1), "at_45C": f"{h45['va']:.0f}->{h45['vb']:.0f}",
                                    "limit_C": pdz.TJ_DESIGN},
            "t_sink_full_power_C": {"45C": round(h45["t_sink"], 1), "60C": round(h60["t_sink"], 1), "trip_low_C": T_SINK_TRIP[0]},
            "inductor_hot_spot_full_power_C": {"45C": round(i45["t_ind"], 1), "60C": round(i60["t_ind"], 1),
                                               "at": f"{i45['va']:.0f}->{i45['vb']:.0f}", "trip_low_C": T_IND_TRIP[0],
                                               "limit_C": T_IND_MAX},
            "derating_thermal_only_fraction": [round(x, 3) for x in r["der_thermal"]],
            "full_current_no_port_cap_45C": {"at": f"{r['hot_np']['va']:.0f}->{r['hot_np']['vb']:.0f}", "tj_C": round(r["hot_np"]["tj"], 1),
                                             "t_sink_C": round(r["hot_np"]["t_sink"], 1), "inductor_hot_spot_C": round(r["hot_np"]["t_ind"], 1),
                                             "air_per_phase_m3h": round(r["hot_np"]["q_cell"], 0)},
            "battery_port_limit_A": LEAN_I_B[n],
            "full_power_window_battery_V": {"P_rated_from_V": round(max(tr.V_FL[0], n * tr.P_RATED / LEAN_I_B[n]), 0),
                                            "P_max_from_V": round(max(n * tr.P_MAX / (n * tr.I_MAX), n * tr.P_MAX / LEAN_I_B[n]), 0),
                                            "note": "below these the battery port carries LEAN_I_B x V_B"},
            "efficiency_eu_weighted": {"750->750": round(eu["750->750"], 5), "min": round(min(eu.values()), 5),
                                       "max": round(max(eu.values()), 5), "grid_V": [550, 750, 950],
                                       "weights": "0.03/0.06/0.13/0.10/0.48/0.20 at 5/10/20/30/50/100 % of rated, 25 C inlet"},
            "standby_W": r["stby"],
            "standby_note": ("gates off, fans off; open = contactors open (banks discharged, true standby); held = both contactors on "
                             "the economiser (2.35 W typ each), banks charged (bleeders + bank dividers on)"),
            "aux_input_W_full_45C": round(r["op45"]["loss"]["ctrl_aux"] + r["op45"]["loss"]["fans"] - p_hv(611, 611), 1),
            "port_conduction_W_full_45C": round(r["op45"]["loss"]["port"], 1),
            "port_film_bank": r["bank"]}
    tb = {k: cell_spec["device_" + k]["trip_band_costfirst"] for k in ("primary", "alternate") if k == "primary" or "device_" + k in cell_spec}
    cf["trip_band"] = {"per_device": tb, "ov_trip_V": list(pdz.OV_HW[:2]), "ov_response_us": pdz.OV_HW[2] * 1e6,
                       "note": "PV-PWR comparator band / CMPSS backup band; peaks with the OV-trip top across L(I) (calculated)"}
    cf["fsw_question"] = {"rows": fsw, "basis": fsw_study.__doc__.split("\n")[0] + " ... (see sim/pv_module.py fsw_study)"}
    cf["cold_start"] = {"basis": ("PCM-17 / D-045 / risk A4: fans OFF below the inlet temperature of their rating; passive power from natural "
                                  "convection on the heatsink and the inductors, heat leaving through the enclosure walls only (ESTIMATES, "
                                  "+-50 %: PASSIVE_K pessimistic / optimistic); sim/pv_module.py passive_by_point()"),
                        "policy": {"fan_rated_min_inlet_C": dv.FANS[FAN_OF[3]]["t_amb"][0], "table_inlet_C": list(T_COLD),
                                   "passive_limits_C": {"heatsink": T_SINK_PASSIVE, "inductor_hot_spot": T_IND_PASSIVE,
                                                        "junction": pdz.TJ_DESIGN},
                                   "fans_start_regardless_of_inlet_C": {"heatsink_NTC": T_SINK_START, "inductor_NTC": T_IND_START},
                                   "enclosure": {"area_m2": A_ENC, "h_inside_W_m2K": H_ENC_IN, "h_outside_W_m2K": H_ENC_OUT,
                                                 "G_W_per_K": round(g_enc(), 2), "structure_J_per_K": C_MODULE,
                                                 "free_air_m3": round(V_AIR_FREE, 4)},
                                   "estimates": {"heatsink_radiation": [EPS_HS, F_RAD_HS], "inductor": [EPS_IND, F_RAD_IND, H_IND_VERT],
                                                 "sensitivity_factors_Rsa_Rind_Genc": PASSIVE_K}},
                        "modules": cold,
                        "firmware_rule": ["inlet NTC < fan rating (-10 C) at start-up: fans OFF, module power <= the table row of the inlet "
                                          "reading (table kW, nominal), no other change to the control",
                                          "fans enabled (speed from the fan law) once the inlet NTC reads above the fan rating",
                                          "fans start at once at any heatsink NTC above the start level or inductor NTC above its start "
                                          "level, whatever the inlet reads (backstop for the passive estimate)"]}
    return cf


def write_report(res, fan_rows, choice, spec):
    f = dv.FANS[choice]
    cf = spec["costfirst"]
    L = []
    a = L.append
    a("# PV-P75 / PV-P100/110 module level - cost-first build\n")
    a("Generated by `sim/pv_module.py`; every number is written by the script. **Calculated, not measured.** "
      "Comparison file: `module_spec.json` (new items in its `costfirst` block). Cell design: `report.md`, `cell_spec.json`.\n")
    iu = cf["inductor_used"]
    a(f"Inductor read at run time: rev {iu['revision']}, {iu['conductor']} - worst {iu['worst_loss_W']} W, hot spot "
      f"{iu['T_hotspot_C']:.0f} C (magnetics basis), {iu['cost_usd']} USD.\n")
    a("## 1. What is inside the enclosure\n")
    a("| group | value | source |")
    a("|---|---|---|")
    rows = [("phases", "N x cell losses (gate bias and sensing moved to the aux line)", "sim/pv_design.py"),
            ("PV port (A)", f"{R_PORT_A*1e3:.2f} mOhm: HFE82V {R_CONTACT*1e3:.1f} + shunt {R_SHUNT*1e3:.1f} + busbars {R_BUSBAR*1e3:.2f}",
             "Hongfa-HFE82V-300C.pdf p.1; port_spec lean shunt; busbars ESTIMATE (port report A_R_LOOP_X)"),
            ("battery port (B)", f"{R_PORT_B*1e3:.2f} mOhm: as A + 2 x {LEAN['battery_fuse']['mpn']} {R_FUSE*1e3:.3f} mOhm",
             "port_spec lean battery_fuse loss_W_per_link"),
            ("aux 75 W flyback", f"bias {P_BIAS} W/phase + logic {P_LOGIC5:.2f} W through 5 V ({ETA_5V}), coils 2 x {P_COIL_HOLD[0]} W, "
             f"fans / {ETA_FANBUCK} + {P_SELV5} W SELV 5 V, all / eta_aux(V)", "PV-PWR / PV-CTL budgets; aux75_spec efficiency"),
            ("passive HV", f"per port: varistor monitor {R_VMON/1e3:.0f}k, bleeder {R_BLEED/1e3:.0f}k, 2 dividers {R_DIV/1e6:.0f} M; PE divider",
             "PV-PWR; dividers ASSUMPTION"),
            ("fans", f"{N_FANS} x {choice} ({f['p_w']} W), PV-P100/110: {N_FANS} x {FAN_OF[4]} capped {FAN_P_CAP[4]:.0f} W", f["src"])]
    for k, v, s_ in rows:
        a(f"| {k} | {v} | {s_} |")
    a("\n## 2. Module efficiency (25 C inlet, sea level, fan law)\n")
    a("| module | peak | at | 550->950 | 950->550 | 550->550 | 950->950 | 611->611 (45 C) | EU-weighted 750->750 (grid min-max) | standby open / held at 1000 V |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for n, r in res.items():
        pk, c, e = r["peak"], r["corners"], cf["modules"][MODULES[n]]
        sb = e["standby_W"]
        a(f"| {MODULES[n]} | **{pk['eff']*100:.2f} %** | {pk['va']:.0f}->{pk['vb']:.0f} V, {pk['p']/1e3:.1f} kW | " +
          " | ".join(f"{c[f'{k}@25C']['eff']*100:.2f}" for k in ("550->950", "950->550", "550->550", "950->950")) +
          f" | {c['611->611@45C']['eff']*100:.2f} | {e['efficiency_eu_weighted']['750->750']*100:.2f} "
          f"({e['efficiency_eu_weighted']['min']*100:.2f}-{e['efficiency_eu_weighted']['max']*100:.2f}) | "
          f"{sb['contactors_open_W'][-1]:.1f} / {sb['contactors_held_W'][-1]:.1f} W |")
    a("\nLosses by group at 611->611 V full power, 45 C inlet [W]:\n")
    a("| module | phases | port | aux + passive HV | fans (at the input) | total | exhaust |")
    a("|---|---|---|---|---|---|---|")
    for n, r in res.items():
        st = r["corners"]["611->611@45C"]
        lo = st["loss"]
        a(f"| {MODULES[n]} | {lo['cells']:.0f} | {lo['port']:.0f} | {lo['ctrl_aux']:.1f} | {lo['fans']:.1f} | {st['ptot']:.0f} | {st['t_exhaust']:.1f} C |")
    a("\n![maps](module_efficiency_maps.png)\n")
    a("## 3. Cooling\n")
    hs = cf["heatsink"]
    a(f"- One extrusion (model): {hs['n_fin']} fins at {hs['fin_pitch_mm']} mm pitch, 40 x 1.5 mm, 10 mm base, 444 mm wide, "
      f"{hs['length_mm']['PV-P75']:.0f} / {hs['length_mm']['PV-P100/110']:.0f} mm long ({hs['mass_kg_model']['PV-P75']} / "
      f"{hs['mass_kg_model']['PV-P100/110']} kg): solved for R_sa = {R_SA_SPEC[3][0]} K/W at {R_SA_SPEC[3][1]:.0f} m3/h, the spec limit.")
    for n in MODULES:
        o = cf["fans_operating_point_45C"][MODULES[n]]
        a(f"- {MODULES[n]}: {N_FANS} x {o['fan']} at {o['speed']*100:.0f} %: {o['flow_m3h_total']:.0f} m3/h ({o['flow_per_fan_m3h']:.0f} per fan) "
          f"at {o['dp_Pa']} Pa; R_sa there {hs['R_sa_at_operating_point_K_W'][MODULES[n]]} K/W (spec {R_SA_SPEC[n][0]}); system "
          f"{o['system_dp_at_420_m3h_Pa']} Pa at 420 m3/h (rule <= {DP_RULE[0]:.0f}); fans {o['fan_power_W']} W.")
    a(f"- Noise at 1 m, all fans (+{INSTALL_DB} dB installation, ESTIMATE): " + ", ".join(
        f"{MODULES[n]} {spec['modules'][MODULES[n]]['noise_dBA_1m_all_fans_at_cap']} dB(A) at the cap" for n in MODULES) + ".")
    if spec["fan"]["cold_restriction"]:
        a(f"- **Cold limit:** {spec['fan']['cold_restriction']}.")
    a("\n| fan (3-phase module, at its cap) | full power at 45 C | noise at full power 45 C | flow per phase [m3/h] | 1 fan failed 45 C: shutter / none | fits 36 W |")
    a("|---|---|---|---|---|---|")
    for x in fan_rows:
        a(f"| {x['fan']} | {x['hold45']*100:.0f} % | {x['noise_full45']:.1f} (speed {x['s_full45']*100:.0f} %) | {x['q_cell']:.0f} | "
          f"{x['fail_flap']*100:.0f} % / {x['fail_noflap']*100:.0f} % | {'yes' if x['i_ok'] else 'no'} |")
    a("\n![cooling](module_cooling_derating.png)\n")
    a("### Cold start: fans off below the fans' rated inlet (PCM-17, ESTIMATES)\n")
    a("| module | inlet [C] | passive power at 611/611 | 950/611 | 611/950 | 1000/500 [kW] (heatsink / inductor [C] there) | lowest of the three, pessimistic - optimistic estimates [kW] | module loss at the lowest of the three [W] |")
    a("|---|---|---|---|---|---|---|---|")
    for name, c in cf["cold_start"]["modules"].items():
        for r_ in c["table"]:
            bp = r_["kW_by_point"]
            cell = lambda q: f"{bp[q]['kW']} ({bp[q]['heatsink_C']:.0f} / {bp[q]['inductor_hot_spot_C']:.0f})"
            a(f"| {name} | {r_['inlet_C']:.0f} | {cell('611/611')} | {cell('950/611')} | {cell('611/950')} | {cell('1000/500')} | "
              f"{r_['kW_pessimistic']} - {r_['kW_optimistic']} | {r_['module_loss_W']} |")
    for name, c in cf["cold_start"]["modules"].items():
        w_ = c["warmup_minus30_to_minus10_C_min"]
        a(f"\n{name}: natural-convection R_sa {c['table'][0]['R_sa_natural_K_W']} K/W against {c['R_sa_forced_K_W']} K/W forced; the air near the "
          f"fan inlet warms from -30 to -10 C in about {w_['structure_and_air']:.0f} min at {w_['at_module_loss_W']:.0f} W (structure and air, "
          f"{cf['cold_start']['policy']['enclosure']['structure_J_per_K']/1e3:.0f} kJ/K) - the air alone in {w_['air_only_s']:.0f} s; where no "
          f"passive power exists the override starts the fans after " + ", ".join(f"{r_['inlet_C']:.0f} C: " + ("> 6 h" if r_['kW_by_point']['611/611']['override_after_min'] is None else f"{r_['kW_by_point']['611/611']['override_after_min']:.0f} min") for r_ in c["table"] if r_['kW_by_point']['611/611']['kW'] == 0) + " (611/611 V).")
    a("\nFirmware rule: " + "; ".join(cf["cold_start"]["firmware_rule"]) + f". Limits of the passive mode: heatsink "
      f"{T_SINK_PASSIVE:.0f} C, inductor hot spot {T_IND_PASSIVE:.0f} C, junction {pdz.TJ_DESIGN:.0f} C; fans start regardless of the inlet at heatsink "
      f"NTC >= {T_SINK_START:.0f} C or inductor NTC >= {T_IND_START:.0f} C. Estimates: " + cf["cold_start"]["basis"] + ".\n")
    a(f"## 4. Ambient derating (fans at the cap; Tj {pdz.TJ_DESIGN:.0f} C, inductor hot spot <= {T_IND_TRIP[0]} C (trip band low end), "
      f"heatsink <= {T_SINK_TRIP[0]} C (trip band low end), C4AQ hot spot, lean battery-port current)\n")
    for n, r in res.items():
        e = cf["modules"][MODULES[n]]
        a(f"**{MODULES[n]}** - power held [kW] (fraction of {n*tr.P_MAX/1e3:.1f} kW max):\n")
        a("| inlet [C] | " + " | ".join(str(t) for t in r["temps"]) + " |")
        a("|---|" + "---|" * len(r["temps"]))
        for key, curve in list(r["der"].items()) + [("thermal only (no port cap)", r["der_thermal"])]:
            a(f"| {key} | " + " | ".join(f"{x*n*tr.P_MAX/1e3:.1f}" for x in curve) + " |")
        a("")
        a(f"Full power: Tj {e['tj_max_full_power_C']['45C']} / {e['tj_max_full_power_C']['60C']} C, heatsink "
          f"{e['t_sink_full_power_C']['45C']} / {e['t_sink_full_power_C']['60C']} C, inductor hot spot "
          f"{e['inductor_hot_spot_full_power_C']['45C']} / {e['inductor_hot_spot_full_power_C']['60C']} C at 45 / 60 C inlet. "
          f"Battery port {LEAN_I_B[n]:.0f} A: rated power from V_B {e['full_power_window_battery_V']['P_rated_from_V']:.0f} V, P_max from "
          f"{e['full_power_window_battery_V']['P_max_from_V']:.0f} V.")
        a(f"Altitude, fraction of P_max at 45 C: " + ", ".join(f"{alt} m {x*100:.0f} %" for alt, x in zip(r["alts"], r["alt45"]))
          + "; at 60 C: " + ", ".join(f"{alt} m {x*100:.0f} %" for alt, x in zip(r["alts"], r["alt60"])) + ".")
        a(f"Worst case 60 C + 3000 m + one fan dead (no shutter): {r['worst_combo']*100:.0f} % of P_max. Pessimistic estimates together "
          f"(K_rest x1.5, inductor h x0.7, case-sink R_th x1.3), sea level: " +
          ", ".join(f"{t} C {x*100:.0f} %" for t, x in zip(r["temps"], r["sens"][0])) + ".\n")
    a("## 5. Trip bands against the boards (per phase)\n")
    a("| device | trip | band [A] | response | peak [A] | L/L0 (>= 0.35) | B/B_sat (<= 0.85) | I_DM [A] | V_pk at 1110 V (<= limit) | ok |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for role, tb in cf["trip_band"]["per_device"].items():
        for k, x in (tb or {}).items():
            if k == "basis":
                continue
            a(f"| {role} | {k} | {x['band_A'][0]}-{x['band_A'][1]} | {x['response_us']} us | {x['i_peak_A']} | {x['L_over_L0']} | "
              f"{x['B_over_Bsat']} | {x['I_DM_A']:.0f} | {x['v_pk_V']} ({x['v_pk_limit_V']}) | {x['ok']} |")
    a("\n## 6. Cost questions (D-052)\n")
    a("**(a) Common port film bank (FCSA3DS456 45 uF):**\n")
    a("| module | port | ripple current | voltage ripple 1 % | load rejection (1110 V / 1100 V trip) | minimum parts | today | binding |")
    a("|---|---|---|---|---|---|---|---|")
    for n in MODULES:
        bk = cf["modules"][MODULES[n]]["port_film_bank"]
        for p_ in "AB":
            x = bk[p_]
            nu = x["need_uF"]
            lr = f"{nu['load_rejection']:.0f} / {nu['load_rejection_at_1100V_nominal']:.0f} uF" if p_ == "B" else "-"
            a(f"| {MODULES[n]} | {p_} | {nu['ripple_current']:.0f} uF | {nu['voltage_ripple_1pct']:.0f} uF | {lr} | {x['parts_min']} | "
              f"{x['parts_today']} | {x['binding']} |")
        b_ = bk["B"]
        a(f"\n{MODULES[n]}: saving {bk['saving_usd']} USD per module; port B could fall to {b_['parts_with_that_response']} parts if the OV "
          f"trip acted within {b_['ov_response_for_that_us']} us (today {pdz.OV_HW[2]*1e6:.0f} us). CPL pole at 550 V: "
          f"{b_['cpl_pole_Hz_at_550V']['today']:.0f} Hz today, {b_['cpl_pole_Hz_at_550V']['minimum']:.0f} Hz at the minimum, against the "
          f"{b_['cpl_pole_Hz_at_550V']['f_cv_Hz']:.0f} Hz voltage-loop crossover - any smaller bank needs the control owner's re-run.\n")
    a("**(b) Switching frequency** (ripple ratio held; inductor saving is an upper bound with its loss held):\n")
    a("| fsw [kHz] | L(45 A) [uH] | inductor USD/phase | module loss at the peak point [W] | peak eff | Tj max 45 C | heatsink +USD | D_max | module cost delta | ok |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for x in fsw_rows(cf):
        a(f"| {x['fsw_kHz']:.0f} | {x['L_45A_uH']} | {x['inductor_usd_per_phase']} | {x['loss_at_peak_point_W']} | {x['peak_eff']*100:.2f} % | "
          f"{x['tj_max_45C_C']} | {x['heatsink_extra_usd']} | {x['d_max']} | {x['module_cost_delta_usd']} | {x['ok']} |")
    a("\n## 7. Open items\n")
    a("- Fan cold rating (R-05): see section 3; no cheaper fan than the San Ace 9GT with a published -30 C rating is on file.")
    a("- Heatsink geometry, plenum (even flow spread with one fan dead), K_rest, dead-fan K, installation noise, divider values and the "
      "aux efficiency at part load are estimates; confirm with CFD / bench.")
    a("- Port film bank: the cell-level capacitor temperature check still uses the C4AQ model of the cell design; the FCSA3DS456 bank is "
      "checked on PV-PWR (ripple 53 % of its 85 C rating).")
    open(os.path.join(OUT, "module_report.md"), "w").write("\n".join(L) + "\n")


def fsw_rows(cf):
    return cf["fsw_question"]["rows"]


if __name__ == "__main__":
    res, fan_rows, choice, fsw = run()
    spec = json.load(open(os.path.join(OUT, "module_spec.json")))
    cf = spec["costfirst"]
    print(f"fans: {N_FANS} x {choice} (PV-P75), {N_FANS} x {FAN_OF[4]} (PV-P100/110); extrusion n_fin {HS['n_fin']:.1f}, "
          f"mass {HS['mass_kg'][3]:.2f} / {HS['mass_kg'][4]:.2f} kg")
    for n, r in res.items():
        c, e, o = r["corners"], cf["modules"][MODULES[n]], cf["fans_operating_point_45C"][MODULES[n]]
        print(f"{MODULES[n]}: peak {r['peak']['eff']*100:.2f} % at {r['peak']['va']:.0f}->{r['peak']['vb']:.0f} V {r['peak']['p']/1e3:.1f} kW; "
              f"corners 25C " + ", ".join(f"{k} {c[k+'@25C']['eff']*100:.2f}" for k in ("550->950", "950->550", "550->550", "950->950")) +
              f"; EU 750->750 {e['efficiency_eu_weighted']['750->750']*100:.2f} ({e['efficiency_eu_weighted']['min']*100:.2f}-"
              f"{e['efficiency_eu_weighted']['max']*100:.2f}); standby 1000 V open {e['standby_W']['contactors_open_W'][-1]} / held "
              f"{e['standby_W']['contactors_held_W'][-1]} W")
        print(f"  air {o['flow_m3h_total']:.0f} m3/h at {o['dp_Pa']} Pa (sys at 420: {o['system_dp_at_420_m3h_Pa']} Pa), R_sa "
              f"{cf['heatsink']['R_sa_at_operating_point_K_W'][MODULES[n]]}; Tj 45/60 {e['tj_max_full_power_C']['45C']}/"
              f"{e['tj_max_full_power_C']['60C']} C, sink {e['t_sink_full_power_C']['45C']}/{e['t_sink_full_power_C']['60C']}, inductor "
              f"{e['inductor_hot_spot_full_power_C']['45C']}/{e['inductor_hot_spot_full_power_C']['60C']} C; noise cap "
              f"{spec['modules'][MODULES[n]]['noise_dBA_1m_all_fans_at_cap']} dB(A)")
        print("  derating " + ", ".join(f"{t}C {x*100:.0f}%" for t, x in zip(r["temps"], r["der"]["sea level"])) +
              "; thermal only " + ", ".join(f"{x*100:.0f}" for x in r["der_thermal"]) +
              f"; 1 fan failed 45C no flap {r['der']['1 fan failed (no flap)'][4]*100:.0f}%; 3000 m 45C {r['alt45'][3]*100:.0f}%; "
              f"worst combo {r['worst_combo']*100:.0f}%")
        bk = e["port_film_bank"]
        print(f"  bank: A min {bk['A']['parts_min']} ({bk['A']['binding']}), B min {bk['B']['parts_min']} ({bk['B']['binding']}), "
              f"today A {bk['A']['parts_today']} / B {bk['B']['parts_today']}; saving {bk['saving_usd']} USD; "
              f"B needs {bk['B']['need_uF']}; OV response for "
              f"{bk['B']['parts_with_that_response']} parts: {bk['B']['ov_response_for_that_us']} us")
    for x in fsw:
        print(f"  fsw {x['fsw_kHz']:.0f} kHz: ind {x['inductor_usd_per_phase']} USD, loss@peak {x['loss_at_peak_point_W']} W, peak "
              f"{x['peak_eff']*100:.2f} %, Tj45 {x['tj_max_45C_C']}, hs +{x['heatsink_extra_usd']}, D_max {x['d_max']}, "
              f"delta {x['module_cost_delta_usd']} USD, ok {x['ok']}")
    print("pv_module self-check passed")
