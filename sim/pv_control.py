"""PV-P75 / PV-P100-110 module control (PV-01..PV-22, PV-C1, PV-C3): plant, digital control, stability, protection.

Run:  .venv/bin/python sim/pv_control.py        (then sim/pv_mppt.py for the MPPT study)
In:   sim/out/pv_design/cell_spec.json + inductor_L_vs_I.csv (power stage), sim/out/port_design/port_spec.json (port
      sensing chain, precharge, X capacitors).  Interim values from the brief are used only if cell_spec.json is missing.
Out:  sim/out/pv_control/{report.md, control_spec.json, *.png, *.csv}

Calculated / simulated, not bench-validated.  Models:
  * switched: per cell the four gate signals of a centre-aligned double-update ePWM (dead time 200 ns inserted on each
    rising edge, ideal switches, body diodes conduct in the dead time according to the current sign), inductor L(i) from
    the hand-off curve, port capacitor banks shared by all cells, external source/load per port; integrated event by
    event (Heun between gate edges); sensor chains as exact first-order filters; ADC quantisation and noise.
  * averaged: the same equations with the switching functions replaced by their period averages, including the
    dead-time voltage derived from the edge currents of the ripple waveform (zero where the ripple crosses zero).
  * both are driven by ONE controller implementation (ctrl_cell / ctrl_outer) called at the sampling instants.
  * small-signal: the averaged model linearised, discretised exactly (ZOH at the 64 kHz sampling rate, sampling offset,
    one-sample computation delay, sensor poles) and closed with the same controller; the 32 kHz outer loop is lifted
    (2 inner samples per outer sample), so loop gains and closed-loop eigenvalues are exact for the sampled averaged model.
"""
import csv
import json
import math
import os
import sys

import numpy as np
import scipy.linalg as sla

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out", "pv_control")
CELL_SPEC = os.path.join(HERE, "out", "pv_design", "cell_spec.json")
L_CSV = os.path.join(HERE, "out", "pv_design", "inductor_L_vs_I.csv")
PORT_SPEC = os.path.join(HERE, "out", "port_design", "port_spec.json")
sys.path.insert(0, HERE)
import pv_mppt as pvm  # noqa: E402  (PV array model + MPPT algorithm)

# ------------------------------------------------------------------------------------------------ assumptions (ours)
ASSUME = {
    "f_ci_Hz": (2500.0, "current-loop crossover target at L(45 A) (fixed gain, no scheduling)"),
    "ci_zero_ratio": (5.0, "current-loop PI zero = f_ci / this"),
    "f_cv_Hz": (300.0, "outer voltage-loop crossover target (port-current reference / port capacitance)"),
    "cv_zero_ratio": (6.0, "voltage-loop PI zero = f_cv / this (6 chosen over 4..10: +1.6 deg at the worst CPL corner)"),
    "k_ff_bus": (1.0, "feed-forward of the measured port current when that port is a DC bus (inverter = CPL); 0 for a "
                       "PV array or a battery (there it cancels the source's own damping / closes a unity loop)"),
    "mode_hyst": (0.02, "mode hysteresis in duty: leave band when the single-leg duty falls below D_max - this; sized "
                        ">= 2.5 x the dead-time residual of one leg (225 ns x 32 kHz = 0.0072 -> 0.018, rounded up) so "
                        "an unknown dead time cannot make the cell re-enter the mode it just left (asserted)"),
    "dt_range_ns": ((150.0, 600.0), "per-edge dead time at the gates: unknown to firmware, different per leg and per "
                                    "edge; covers the NSI6651 gate drive's 181-586 ns (cell_spec, asserted inside)"),
    "dt_hat_ns": (375.0, "firmware dead-time estimate per edge = midpoint of dt_range (residual <= +/-225 ns)"),
    "dt_slope_frac": (0.4, "if the dead-time compensation is driven by the MEASURED current: its slope <= this x kp with "
                           "all four edge ramps overlapping (band, near zero current), since it is positive feedback on "
                           "the current and must never cancel more than this share of the proportional action"),
    "dt_comp_input": ("ref", "dead-time compensation driven by a model of the closed current loop fed with the "
                             "REFERENCE (feed-forward: adds no loop gain, so its sign ramp can be sharp, and on a "
                             "reversing step it flips with the current, not with the reference) - 'meas' = measured "
                             "current with the dt_slope_frac rule"),
    "dt_model_slew_A_per_us": (0.8, "that model = the reference slew-limited to the rate the loop reaches on a large step "
                                    "(kp x step / L); the type-2 loop follows slower ramps without lag, and so does the model"),
    "mode_exit_tau_s": (3e-3, "band is left only when the single-leg duty is below the exit threshold for the demand u "
                              "AND for the PI integrator filtered with this time constant (the steady-state demand: it "
                              "carries neither the proportional kick nor the integrator's overshoot of a large step); "
                              "band is entered on u alone (more voltage authority at once)"),
    "dt_ff_ramp_A": (2.0, "width of each edge's sign ramp when the compensation is driven by the reference"),
    "plim_trim_tau_s": (5e-3, "port-current limit vs inlet: a slow integral trim on the MEASURED port current (I_A / I_B "
                              "shunt) corrects the conversion through the commanded duty, which the dead time biases by "
                              "up to t_d f_sw / D (~2 %)"),
    "ilim_slew_A_per_s": (2000.0, "slew of each cell's current limit (27.5 kW over the cell's own node-A voltage): a mode "
                                  "change moves the limit by 1/D_max - 1 = 5.3 % without kicking the current loop"),
    "t_ppb_chain_s": (1.0e-6, "ADC PPB trip after the sample: S+H + conversion 0.6 us + ADCEVT -> ePWM X-BAR -> trip "
                              "zone + RS-422 + gate driver (gen/ctrl_c2000.py PPB_US uses the same 1.0 us)"),
    "ppb_period_s": (10e-6, "V_A / V_B conversion period seen by the over-voltage PPB (rev E card: every <= 10 us)"),
    "clamp_vto_V": (0.75, "bus reverse clamp WeEn WND75P16W6 threshold (assumption: the datasheet gives only V_F 1.05 V "
                          "typ at 75 A, 25 C)"),
    "clamp_rt_ohm": (4.0e-3, "bus reverse clamp WND75P16W6 slope through that point (assumption)"),
    "body_v0_V": (2.9, "body-diode threshold, the lower (worse for the clamp share) of SG2M040170HJ (V_SD 3.7 V at 19 A, "
                       "p.6) and MSC035SMA170B4 (3.7 V at 30 A, p.4); assumption"),
    "body_r_ohm": (0.027, "body-diode slope through the MSC035SMA170B4 point (the steeper of the two; assumption)"),
    "aw_margin_A": (0.9, "anti-windup: a non-selected main loop may sit at most this far (per cell) above the selected one"),
    "vlim_qmax_A": (3.0, "V_B-max limit loop: integrator upper clamp (port amps).  With the load-current feed-forward "
                         "the candidate then rides this margin above the module's own current, so it neither winds up "
                         "nor closes a positive loop through a stiff bus, and is selected at once on a load rejection"),
    "f_isense_Hz": (200e3, "cell inductor-current sensor bandwidth REQUIRED (cell_spec minimum); the model uses the "
                           "as-built LEM LA 150-P figure parsed from the PVCELL-25 design check"),
    "f_afe_cell_Hz": (483e3, "CTRL-C2000 cell AFE anti-alias pole (gen/ctrl_c2000.py AFE_CELL)"),
    "f_afe_port_Hz": (493e3, "CTRL-C2000 port AFE anti-alias pole (gen/ctrl_c2000.py AFE_PORT)"),
    "t_amc_s": (1.6e-6, "port-current AMC3302 signal delay 50-50 % typ (p.9) as a first-order lag; the port-voltage "
                        "amplifier delay is port_spec divider/amc_delay_s"),
    "i_sense_fs_A": (100.0, "cell inductor-current full scale -> +/-2 V isolated-amplifier class output (cell_spec)"),
    "adc_noise_lsb": (0.55, "ADC noise per sample (F28388D 12-bit ENOB 10.9 incl. quantisation -> 0.55 LSB rms)"),
    "isense_noise_A": (0.05, "cell current-sensor output noise, A rms (Hall/isolated-amplifier class; assumption)"),
    "amc3330_noise_V": (484e-6, "AMC3330 output noise over its 375 kHz bandwidth (250 uV in 100 kHz, p.8, scaled)"),
    "amc3302_noise_V": (627e-6, "AMC3302 output noise over its 340 kHz bandwidth (340 uV in 100 kHz, p.9, scaled)"),
    "t_trip_chain_s": (0.40e-6, "comparator -> gates off: CMPSS 60 ns max + digital filter 100 ns + X-BAR/TZ 20 ns + "
                                "RS-422 2x25 ns + NSI6651 delay 110 ns max + device t_d(off)+t_f 32 ns, rounded up"),
    "t_comp_s": (4.0e-6, "ISR: sample + conversion (0.6 us) + 4 cell loops + outer loop on CPU1/CLA (budget)"),
    "batt_R": (0.032, "battery/bus source resistance incl. loop (port_design A_RS_BATT 30 mOhm + A_R_LOOP 2 mOhm)"),
    "batt_L": (3.5e-6, "battery/bus source inductance incl. loop (port_design A_LS_BATT 3 uH + A_L_LOOP 0.5 uH)"),
    "sens_gain_err": (0.0083, "cell current-sensor gain error after EOL calibration, worst (same class as the port "
                              "channel: port_spec current/error_worst_compensated/135)"),
    "sens_off_err_A": (0.30, "cell current-sensor offset residual, worst: the LA 150-P offset is re-zeroed by firmware at "
                             "every idle (a null above 5 A is rejected); drift between idles assumed <= 0.3 A"),
    "L_mismatch": (0.10, "inductance mismatch between cells for the sharing study (+/-10 %, brief)"),
}


def A(k):
    return ASSUME[k][0]


# ------------------------------------------------------------------------------------------------ inputs
def load_params(N=3):
    """power-stage and port hand-off values -> parameter dict P (per cell unless noted)"""
    src = []
    if os.path.exists(CELL_SPEC):
        cs = json.load(open(CELL_SPEC))
        fsw = cs["switching"]["f_sw_kHz"] * 1e3
        td = cs["switching"]["dead_time_ns"] * 1e-9
        dmax = cs["switching"]["D_max"]
        tmin = cs["switching"]["min_pulse_us"] * 1e-6
        c_cell = cs["capacitors"]["port_A"]["C_total_uF"] * 1e-6
        r_ser = cs["control_plant"]["R_series_mOhm"] * 1e-3
        ind = cs["inductor"]
        prot = cs["protection"]
        i_trip, v_ovp, v_sw, v_uv = prot["inductor_overcurrent_hw_trip_A"], prot["overvoltage_hw_trip_port_B_V"], \
            prot["overvoltage_sw_limit_V"], prot["undervoltage_stop_V"]
        imax, pmax = cs["ratings"]["I_max_A_low_voltage_port"], cs["ratings"]["P_max_kW"] * 1e3
        tab = np.loadtxt(L_CSV, delimiter=",", skiprows=1)
        li, lv = tab[:, 0], tab[:, 1] * 1e-6
        l0, i50 = ind["L0_uH"] * 1e-6, ind.get("I_at_50pct_L0_A") or 109.0
        ltol = ind.get("L_tolerance_pct", 8.0) / 100
        desat_blank = prot["desat_blanking_ns"] * 1e-9
        td_gates = cs["switching"].get("dead_time_at_gates_ns", [td * 1e9, td * 1e9])
        lo_, hi_ = A("dt_range_ns")
        assert lo_ <= td_gates[0] and td_gates[1] <= hi_, ("gate dead time outside the control design range", td_gates)
        clamp = cs.get("bus_reverse_clamp", {})
        mag = os.path.join(HERE, "out", "magnetics", "design_pv_inductor.json")
        if os.path.exists(mag):                  # inductor construction owned by the magnetics engineer (D-039)
            e = json.load(open(mag))["electrical"]
            l0m, l45m, ltm = e["L0_H"], e["L_at_45A_nom_H"], e["L_trip_over_L0"] * e["L0_H"]
            k = np.interp(li, [0.0, imax, i_trip], [l0m / np.interp(0.0, li, lv), l45m / np.interp(imax, li, lv),
                                                      ltm / np.interp(i_trip, li, lv)])
            lv = lv * k                          # the table's shape, the construction's three anchors
            l0 = l0m
            src.append("sim/out/magnetics/design_pv_inductor.json (L0, L(45 A), L(trip) anchors)")
        src.append("sim/out/pv_design/cell_spec.json + inductor_L_vs_I.csv")
    else:                                    # interim values of the brief (sim/out/pv_tradeoff/selection.json)
        fsw, td, dmax, tmin, c_cell, r_ser = 50e3, 200e-9, 0.95, 1e-6, 45e-6, 0.05
        li, lv, l0, i50, ltol = np.array([0.0, 80.0]), np.array([139e-6, 139e-6]), 139e-6, 200.0, 0.08
        i_trip, v_ovp, v_sw, v_uv, imax, pmax, desat_blank = 72.4, 1100.0, 1050.0, 240.0, 45.0, 27.5e3, 500e-9
        td_gates, clamp = [200.0, 200.0], {}
        src.append("INTERIM brief values (cell_spec.json missing)")
    ps = json.load(open(PORT_SPEC))
    div = ps["divider"]
    r_th = div["R_bot_ohm"] * div["n_top"] * div["R_top_elem_ohm"] / (div["R_bot_ohm"] + div["n_top"] * div["R_top_elem_ohm"])
    tau_rc = div.get("lag_s", r_th * div["C_filter_F"])          # port engineer's divider lag (rev 6)
    t_amc_v = div.get("amc_delay_s", A("t_amc_s"))
    v_fs = div["V_full_scale"]                                   # 1 V at the AMC3330 input
    lsb_adc = 2.5 / 4096
    g_port, g_cell = 0.576, 0.402                                # CTRL-C2000 AFE gains (gen/ctrl_c2000.py)
    lsb_v = lsb_adc / (g_port * 2.0 / v_fs)                      # AMC3330 gain 2
    cur = ps["current"]
    a_i = cur["R_shunt_ohm"] * 41.0                              # AMC3302 gain 41
    lsb_ip = lsb_adc / (g_port * a_i)
    lsb_il = lsb_adc / (g_cell * 2.0 / A("i_sense_fs_A"))
    x_cap = ps["x_cap"]["C_F"] * ps["x_cap"]["per_port"]
    pre = ps["precharge"]
    variant = ps.get("variants", {}).get("135" if N == 3 else "180", {})
    src.append("sim/out/port_design/port_spec.json")
    # inductor curve extended to the 50 %-of-L0 point and floored at 30 % of L0 (soft saturation, powder core)
    li2 = np.concatenate([li, [i50, 2 * i50]])
    lv2 = np.concatenate([lv, [0.5 * l0, max(0.3 * l0, 0.5 * l0 - (lv[-1] - 0.5 * l0) / (i50 - li[-1]) * i50)]])
    lstep = 0.05
    ltab = np.interp(np.arange(0, 400, lstep), li2, lv2).tolist()
    P = dict(src=src, N=N, fsw=fsw, T=1 / fsw, Ts=0.5 / fsw, td=td, Dmax=dmax, Dmin=tmin * fsw, tmin=tmin, R=r_ser,
             C_cell=c_cell, C_x=x_cap, C_port=N * c_cell + x_cap, Li=li2, Lv=lv2, ltab=ltab, lstep=lstep, L0=l0, i50=i50,
             Ltol=ltol, Lnom=float(np.interp(imax, li2, lv2)), i_trip=i_trip, v_ovp=v_ovp, v_ov_sw=v_sw, v_uv=v_uv,
             imax=imax, pmax=pmax, desat_blank=desat_blank,
             tau_i=1 / (2 * math.pi * sensor_bw_hz()) + 1 / (2 * math.pi * A("f_afe_cell_Hz")),
             tau_rc=tau_rc, tau_amc=t_amc_v + 1 / (2 * math.pi * A("f_afe_port_Hz")),
             tau_ip=A("t_amc_s") + cur["R_filter_ohm"] * cur["C_filter_F"] + 1 / (2 * math.pi * A("f_afe_port_Hz")),
             lsb_v=lsb_v, lsb_ip=lsb_ip, lsb_il=lsb_il, v_fs_adc=1.25 / (g_port * 2.0 / v_fs), ip_fs_adc=1.25 / (g_port * a_i),
             sig_v=math.hypot(A("amc3330_noise_V") / 2.0 * v_fs, A("adc_noise_lsb") * lsb_v),
             sig_ip=math.hypot(A("amc3302_noise_V") / a_i, A("adc_noise_lsb") * lsb_ip),
             sig_il=math.hypot(A("isense_noise_A"), A("adc_noise_lsb") * lsb_il),
             r_pre=pre["R_ohm"], dv_ok=pre["dV_ok_V"], k_check=pre["k_check"], t_blank=pre["t_blank_s"],
             tau_max=pre["tau_max_s"], port_oc=variant.get("OC_trip_A"), port_sc=variant.get("SC_trip_A"),
             ov_port_design=ps["control"]["OV_trip_V"], div=div, cur=cur,
             i_port_max=N * imax, p_mod_max=N * pmax, pre=pre,
             td_range=tuple(x * 1e-9 for x in A("dt_range_ns")), td_hat=A("dt_hat_ns") * 1e-9, td_gates=td_gates,
             clamp=clamp, trips=rev_c_trips(), i_port_vs_inlet=ps.get("port_current_limit_vs_inlet_C"),
             device=(cs["switch_positions"][0]["mpn"] if os.path.exists(CELL_SPEC) else "?"),
             driver=(cs["gate_drive"]["driver"].split(" (")[0] if os.path.exists(CELL_SPEC) else "?"),
             rating_key="135" if N == 3 else "180")
    return P


def sensor_bw_hz():
    """as-built cell current-sensor bandwidth (PVCELL-25 design check: '-3 dB ... kHz'), else the required minimum"""
    if os.path.exists(PVCELL_CHECK):
        import re
        m = re.search(r"-3 dB ([\d.]+) kHz", open(PVCELL_CHECK).read())
        if m:
            return float(m.group(1)) * 1e3
    return A("f_isense_Hz")


def port_limit(P, kind, t_inlet):
    """firmware port-current limit at the inlet temperature (port_spec port_current_limit_vs_inlet_C)"""
    tab = P.get("i_port_vs_inlet")
    if not tab:
        return P["imax"] * P["N"]
    return float(np.interp(t_inlet, tab["inlet_C"], tab[f"{kind}/{P['rating_key']}"]))


PIN_PLAN = os.path.join(ROOT, "gen", "data", "ctrl_c2000_pin_plan.csv")
PVCELL_CHECK = os.path.join(ROOT, "hardware", "PVCELL-25", "outputs", "PVCELL-25_design_check.txt")
TRIP_FALLBACK = {      # coordinator 2026-10-04 (control card rev E), used only if the generated files are missing
    "ov_ppb": (1100.0, 1084.1, 1115.9), "ov_cmpss": (1110.0, 1055.6, 1164.4), "oc_backup": (91.5, 84.4, 98.6),
    "port_sc": (405.0, 383.6, 426.4), "port_oc": (202.5, 198.6, 206.4), "oc_local": (72.2, 68.5, 76.0),
    "t_local_us": (0.94, 1.73), "open_v": -181.0, "open_i": -312.0, "open_an1": -141.0, "open_an1_one": -56.0,
    "sensor_delay_us": 0.52}


def rev_c_trips():
    """hardware trip layers as built: CTRL-C2000 rev E (pin-plan column trip_band) and the PVCELL-25 local OC window
    (design check).  Each band = (nominal, low, high) in physical units."""
    import re
    t, src = dict(TRIP_FALLBACK), []
    num = r"(-?[\d.]+)"
    if os.path.exists(PIN_PLAN):
        rows = {r["signal"]: r.get("trip_band", "") for r in csv.DictReader(open(PIN_PLAN))}
        pats = {"ov_ppb": ("P_VB", r"OV " + num + r" V \(PPB\): " + num + "-" + num),
                "ov_cmpss": ("P_VB", r"OV backup " + num + r" V \(CMPSS high\): " + num + "-" + num),
                "oc_backup": ("C1_AN1", r"OC (?:backup )?\+/-" + num + r" A \(CMPSS window\): " + num + "-" + num),
                "port_sc": ("P_IA", r"SC \+/-" + num + r" A \(CMPSS\): " + num + "-" + num),
                "port_oc": ("P_IA", r"OC \+/-" + num + r" A \(PPB\): " + num + "-" + num)}
        for k, (sig, pat) in pats.items():
            m = re.search(pat, rows.get(sig, ""))
            if m:
                t[k] = tuple(float(x) for x in m.groups())
                src.append(k)
        for k, sig, pat in (("open_v", "P_VB", r"reads <= " + num + " V"), ("open_i", "P_IA", r"one open conductor reads <= "
                            + num + " A"), ("open_an1", "C1_AN1", r"reads <= " + num + " A"),
                            ("open_an1_one", "C1_AN1", r"one line open reads <= " + num + " A")):
            m = re.search(pat, rows.get(sig, ""))
            if m:
                t[k] = float(m.group(1))
    if os.path.exists(PVCELL_CHECK):
        txt = open(PVCELL_CHECK).read()
        m = re.search(r"\+/-" + num + r" A nominal.*?" + num + "-" + num + r" A with every tolerance", txt)
        m2 = re.search(r"gate off " + num + r" us; EN path \(TRSTFIL [\d.]+ us\) " + num + " us", txt)
        if m and m2:
            t["oc_local"] = tuple(float(x) for x in m.groups())
            t["t_local_us"] = tuple(float(x) for x in m2.groups())
            src.append("oc_local")
        m3 = re.search(r"delay tD90 [\d.]+ \+ driver [\d.]+ = ([\d.]+) us", txt)
        if m3:
            t["sensor_delay_us"] = float(m3.group(1))
            src.append("AN1 sensor delay")
    t["src"] = src
    return t


def L_of(P, i):
    """incremental inductance at |i| (table lookup, fast scalar)"""
    k = int(abs(i) / P["lstep"])
    tab = P["ltab"]
    return tab[k] if k < len(tab) else tab[-1]


def duties_ss(P, va, vb):
    """steady-state duties exactly as cell_spec switching.modes (buck / boost / band)"""
    dm = P["Dmax"]
    r = vb / va
    if r <= dm:
        return r, 1.0, "buck"
    if r >= 1.0 / dm:
        return 1.0, va / vb, "boost"
    return dm * min(1.0, r), dm * min(1.0, 1.0 / r), "band"


def ripple_edges(P, da, db, va, vb, L):
    """inductor-current offsets from the average at the falling edges of leg A and B (pulses centred on the carrier
    zero; the rising-edge offsets are the negatives).  Exact for the piecewise-linear ripple."""
    th = P["Ts"]
    ha, hb = min(da, 1.0) * th, min(db, 1.0) * th
    vbar = va * min(da, 1.0) - vb * min(db, 1.0)

    def off(t):
        return (va * min(t, ha) - vb * min(t, hb) - vbar * t) / L
    return off(ha), off(hb)


def conn_avg(P, da, db, va, vb, i, L, td4):
    """period-average connection of node A to A+ and node B to B+ (= effective duties) with the dead time of each
    edge, td4 = (leg A rising, leg A falling, leg B rising, leg B falling) [s] (rising = bottom off -> top on).
    Edge logic: a leg's node follows the current in the dead time (current out of node A -> low, into node B -> high)."""
    f = P["fsw"]
    tar, taf, tbr, tbf = td4
    fa, fb = ripple_edges(P, da, db, va, vb, L)
    sa = 1.0 if da >= 1.0 else da - tar * f * (i - fa > 0.0) + taf * f * (i + fa < 0.0)
    sb = 1.0 if db >= 1.0 else db + tbf * f * (i + fb > 0.0) - tbr * f * (i - fb < 0.0)
    return sa, sb


# ------------------------------------------------------------------------------------------------ controller
def design(P, N=None):
    """controller gains (continuous design, verified on the exact sampled model in stability())"""
    N = N or P["N"]
    fci, fcv = A("f_ci_Hz"), A("f_cv_Hz")
    kp = 2 * math.pi * fci * P["Lnom"]
    ki = kp * 2 * math.pi * fci / A("ci_zero_ratio")
    c = N * P["C_cell"] + P["C_x"]
    kpv = 2 * math.pi * fcv * c
    kiv = kpv * 2 * math.pi * fcv / A("cv_zero_ratio")
    return dict(kp=kp, ki=ki, kpv=kpv, kiv=kiv, hyst=A("mode_hyst"), aw=A("aw_margin_A"),
                Ts=P["Ts"], Tv=P["T"], N=N, C=c, f_ci=fci, f_cv=fcv, t_soc=P["tau_i"], dt_slope=A("dt_slope_frac") * kp)


def modulate(P, G, mode, u, ca, cb, va, vb, may_change=True, u_slow=None, band_req=False):
    """average inductor voltage u -> (D_A, D_B, mode, realised u).  Buck / boost with one static leg, band with both
    legs switching (cell_spec rule); the controller's u is kept continuous across modes.  ca / cb = dead-time voltage
    of leg A / B if that leg switches (dt_terms); each candidate mode is judged with its OWN compensation, otherwise
    entering band (which adds a leg's dead time) would immediately argue for leaving it again.  Hysteresis on leaving
    band; mode changes only take effect at a carrier zero (may_change) so the 1 us minimum pulse holds."""
    dm, dn, h = P["Dmax"], P["Dmin"], G["hyst"]
    va, vb = max(va, 1.0), max(vb, 1.0)
    da_buck, db_boost = (vb + u + ca) / va, (va - u - cb) / vb
    if may_change:
        if mode == "buck" and (da_buck > dm or band_req):
            mode = "band"
        elif mode == "boost" and (db_boost > dm or band_req):
            mode = "band"
        elif mode == "band":
            us = u if u_slow is None else u_slow
            if da_buck < dm - h and (vb + us + ca) / va < dm - h:
                mode = "buck"
            elif db_boost < dm - h and (va - us - cb) / vb < dm - h:
                mode = "boost"
    if mode == "buck":
        da, db, comp = min(max(da_buck, dn), dm), 1.0, ca
    elif mode == "boost":
        da, db, comp = 1.0, min(max(db_boost, dn), dm), cb
    else:
        ut = u + ca + cb
        vbs = min(dm * vb, dm * va - ut)
        da, db, comp = min(max((vbs + ut) / va, dn), dm), min(max(vbs / vb, dn), dm), ca + cb
    return da, db, mode, da * va - db * vb - comp


def dt_terms(P, G, c, im, va, vb):
    """controller estimate of the inductor voltage lost to the dead time by leg A (ca) and leg B (cb) if they switch.
    Needs only the RANGE of the dead time: each edge uses the midpoint estimate c['td_hat'] (the integrator takes the
    +/-175 ns residual), the edge currents come from the predicted ripple, and each edge's sign test is a ramp whose
    width W makes the total slope (all four edges overlapping) = dt_slope_frac x kp: the compensation (positive
    feedback on the measured current) can then never cancel more than that share of the proportional action."""
    f = P["fsw"]
    tar, taf, tbr, tbf = c["td_hat"]
    fa, fb = ripple_edges(P, c["DA"], c["DB"], va, vb, P["Lnom"])
    fa = 0.0 if c["DA"] >= 1.0 else fa
    fb = 0.0 if c["DB"] >= 1.0 else fb
    if G.get("dt_comp", A("dt_comp_input")) == "ref":
        w = A("dt_ff_ramp_A")
    else:
        w = max(f * (va * (tar + taf) + vb * (tbr + tbf)) / G["dt_slope"], 0.5)

    def pos(x):
        return min(max(0.5 + x / w, 0.0), 1.0)
    return va * f * (tar * pos(im - fa) - taf * pos(-(im + fa))), vb * f * (tbf * pos(im + fb) - tbr * pos(-(im - fb)))


def ctrl_init(P, G, sc, va, vb):
    N = sc["N"]
    da, db, mode = duties_ss(P, va, vb)
    th = sc.get("td_hat", (P["td_hat"],) * 4)            # one 4-tuple for every cell, or a list per cell
    th = [tuple(x) for x in th] if isinstance(th[0], (tuple, list)) else [tuple(th)] * N
    types, t_in = sc.get("port_types", ("pv", "battery")), sc.get("inlet_C", 45.0)
    il = min(P["imax"], P["pmax"] / max(da * va, 1.0), port_limit(P, types[0], t_in) / (N * max(da, 0.05)),
             port_limit(P, types[1], t_in) / (N * max(db, 0.05)))
    i0 = sc.get("i0", [0.0] * N)
    cells = [dict(DA=da, DB=db, DAp=da, DBp=db, mode=mode, q=0.0, q_f=0.0, u=0.0, i_mod=i0[k], on=False, fault=False,
                  td_hat=th[k], ilim=il) for k in range(N)]
    return dict(cells=cells, iref=[0.0] * N, qa=0.0, qb=0.0, sel="", trip="", limited=False, n_act=N,
                mppt=None, mppt_acc=[0.0, 0.0, 0], tick_next=None)


def ctrl_cell(P, G, c, im, va, vb, iref, may_change):
    """inner loop of one cell, run once per sample (64 kHz): PI on the sampled average inductor current, port-voltage
    feed-forward inside the modulator, dead-time compensation, clamping anti-windup.  Result -> pending duties."""
    e = iref - im
    c["q"] += G["ki"] * G["Ts"] * e
    u = G["kp"] * e + c["q"]
    sl = A("dt_model_slew_A_per_us") * 1e6 * G["Ts"]          # model of the closed current loop: slew-limited reference
    c["i_mod"] += min(max(iref - c["i_mod"], -sl), sl)
    ca, cb = dt_terms(P, G, c, c["i_mod"] if G.get("dt_comp", A("dt_comp_input")) == "ref" else im, va, vb)
    c["q_f"] += G["Ts"] / A("mode_exit_tau_s") * (c["q"] - c["q_f"])
    # a single-leg mode that saturates at D_max on a sample where the mode may not change (carrier peak) asks for band
    # at the next carrier zero: the anti-windup of the saturated sample would otherwise hide the request
    if not may_change and c["mode"] == "buck" and (vb + u + ca) / max(va, 1.0) > P["Dmax"]:
        c["band_req"] = True
    if not may_change and c["mode"] == "boost" and (va - u - cb) / max(vb, 1.0) > P["Dmax"]:
        c["band_req"] = True
    da, db, mode, ur = modulate(P, G, c["mode"], u, ca, cb, va, vb, may_change, u_slow=c["q_f"],
                                band_req=may_change and c.pop("band_req", False))
    if abs(ur - u) > 1e-9:
        c["q"] += ur - u
    c.update(DAp=da, DBp=db, mode=mode, u=ur)


def ctrl_outer(P, G, cs, sc, m, t):
    """outer loops (32 kHz): returns per-cell inductor-current references.  cmd = sc['cmd'](t, cs):
         ('I', i_cell)                       direct current command (BMS / test), clamped to the limits
         ('VA', v_ref, vb_max, allow_rev)    port-A voltage (MPPT / bus on A) with the port-B over-voltage limit loop
         ('VB', v_ref)                        port-B voltage (bus forming, bidirectional)
       min-selection of the candidate references with tracking anti-windup; per-cell limits 45 A and P_max per cell;
       firmware protections (over / under voltage) latch a trip of all cells."""
    va, vb, ia, ib = m["va"], m["vb"], m["ia"], m["ib"]
    if cs["trip"]:
        return [0.0] * sc["N"]
    if va > P["v_ov_sw"] or vb > P["v_ov_sw"]:
        cs["trip"] = f"SW OV at {t*1e3:.3f} ms"
    elif sc.get("uv_check", True) and any(c["on"] for c in cs["cells"]) and (va < P["v_uv"] or vb < P["v_uv"]):
        cs["trip"] = f"SW UV at {t*1e3:.3f} ms"
    if cs["trip"]:
        for c in cs["cells"]:
            c["on"] = False
        return [0.0] * sc["N"]
    cells = cs["cells"]
    act = [c for c in cells if not c["fault"]]                          # enabled cells (a faulted cell is excluded)
    n = max(1, len(act))
    cs["n_act"] = n
    # port current <-> cell current through the cells' ACTUAL duties (they carry the mode hysteresis); the ratio map
    # duties_ss would flip with measurement noise at a band edge and kick the references by 1/D_max
    da = sum(c["DA"] for c in act) / n if act else 1.0
    db = sum(c["DB"] for c in act) / n if act else 1.0
    step = A("ilim_slew_A_per_s") * G["Tv"]
    t_in = sc.get("inlet_C", 45.0)                                      # port-current limit vs inlet (port_spec rev 6)
    types = sc.get("port_types", ("pv", "battery"))
    ia_lim, ib_lim = port_limit(P, types[0], t_in), port_limit(P, types[1], t_in)
    cs["port_lim"] = (ia_lim, ib_lim)
    k_t = G["Tv"] / A("plim_trim_tau_s")
    tra, trb = cs.get("plim_trim", (1.0, 1.0))
    tra = min(max(tra + k_t * (ia_lim - abs(ia)) / ia_lim, 0.7), 1.0)
    trb = min(max(trb + k_t * (ib_lim - abs(ib)) / ib_lim, 0.7), 1.0)
    cs["plim_trim"] = (tra, trb)
    ia_lim, ib_lim = tra * ia_lim, trb * ib_lim
    for c in cells:                                                     # 45 A, 27.5 kW over the cell's own v_A, ports
        tgt = min(P["imax"], P["pmax"] / max(c["DA"] * va, 1.0), ia_lim / (n * max(c["DA"], 0.05)),
                  ib_lim / (n * max(c["DB"], 0.05)))
        c["ilim"] += min(max(tgt - c["ilim"], -step), step)
    lims = [c["ilim"] for c in cells]
    ilim = min(c["ilim"] for c in act) if act else P["imax"]
    cmd = sc["cmd"](t, cs)
    kind = cmd[0]
    if "i_init" in sc and not cs.get("init_done"):                      # start a run in steady state
        cs["init_done"] = True
        cs["qa"] = sc["i_init"] * n * da - sc.get("kff_a", 0.0) * ia
        cs["qb"] = sc["i_init"] * n * db - sc.get("kff_b", 0.0) * ib
    tv, kp, ki, dl = G["Tv"], G["kpv"], G["kiv"], G["aw"]
    kfa, kfb = sc.get("kff_a", 0.0), sc.get("kff_b", 0.0)
    if kind == "I":
        out = [min(max(cmd[1], -x), x) for x in lims]
        cs["sel"], cs["limited"] = "I", any(abs(o) < abs(cmd[1]) for o in out)
        return out
    if kind == "VA":
        vref, vbmax, rev = cmd[1], cmd[2], cmd[3]
        lo = -ilim if rev else 0.0
        ea = va - vref
        ffa = kfa * ia
        cand = {"VA": (ffa + kp * ea + cs["qa"]) / (n * da)}
        if vbmax:
            eb = vbmax - vb
            ffb = kfb * ib
            cand["VBmax"] = (ffb + kp * eb + cs["qb"]) / (n * db)
        sel = min(cand, key=cand.get)
        ir = min(max(cand[sel], lo), ilim)
        cs["sel"] = sel if lo < cand[sel] < ilim else ("ILIM" if cand[sel] >= ilim else "LO")
        cs["limited"] = cs["sel"] in ("ILIM", "VBmax")         # an upper limit holds the operating point (MPPT tracks)
        cs["qa"] += ki * tv * ea
        cs["qa"] = min(cs["qa"], (ir + dl) * n * da - ffa - kp * ea)
        cs["qa"] = max(cs["qa"], (lo - dl) * n * da - ffa - kp * ea)
        if vbmax:
            cs["qb"] = min(max(cs["qb"] + ki * tv * eb, -n * P["imax"]), A("vlim_qmax_A"))
        return [min(max(ir, -x if rev else 0.0), x) for x in lims]
    if kind == "VB":
        eb = cmd[1] - vb
        ffb = kfb * ib
        ub = (ffb + kp * eb + cs["qb"]) / (n * db)
        ir = min(max(ub, -ilim), ilim)
        cs["sel"], cs["limited"] = ("VB" if ir == ub else "ILIM"), ir != ub
        cs["qb"] += ki * tv * eb
        cs["qb"] = min(max(cs["qb"], (-ilim - dl) * n * db - ffb - kp * eb), (ilim + dl) * n * db - ffb - kp * eb)
        return [min(max(ir, -x), x) for x in lims]
    raise ValueError(kind)


# ------------------------------------------------------------------------------------------------ ports (external)
def port_bat(V, R=None, L=None):
    return dict(kind="bat", V=V, R=A("batt_R") if R is None else R, L=A("batt_L") if L is None else L)


def port_pv(arr, G, Tc):
    v = np.arange(0.0, 1300.0, 0.25)
    i = pvm.array_i(arr, np.full_like(v, G), Tc, v)
    return dict(kind="pv", arr=arr, G=G, Tc=Tc, dv=0.25, tab=np.maximum(i, -50.0).tolist())


def pv_set(p, G):
    q = port_pv(p["arr"], G, p["Tc"])
    p.update(G=G, tab=q["tab"])


def ext_cur(p, v, ie):
    """current into the module's port node from the external network, and d(ie)/dt for stateful ports"""
    k = p["kind"]
    if k == "bat":
        return ie, (p["V"] - p["R"] * ie - v) / p["L"]
    if k == "pv":
        x = v / p["dv"]
        j = int(x)
        tab = p["tab"]
        if j < 0:
            return tab[0], 0.0
        if j >= len(tab) - 1:
            return tab[-1], 0.0
        return tab[j] + (tab[j + 1] - tab[j]) * (x - j), 0.0
    if k == "cpl":
        return -p["P"] / max(v, p.get("vmin", 100.0)), 0.0
    if k == "res":
        return -v / p["R"], 0.0
    return 0.0, 0.0


# ------------------------------------------------------------------------------------------------ simulator
def quant(x, lsb, sig, rng, gain=0.0, off=0.0, lo=-1e9, hi=1e9):
    y = x * (1.0 + gain) + off + (rng.normal(0.0, sig) if sig > 0 else 0.0)
    return min(max(round(y / lsb) * lsb, lo), hi)


def leg_segs(tb, up, D, Dprev, tdr, tdf, th):
    """gate segments (t_start, top, bottom) of one leg over a half carrier period starting at tb; tdr = dead time
    before the top turns on (rising edge), tdf = before the bottom turns on (falling edge), as stretched by the
    gate-drive interlock (each switch's turn-on is delayed, its turn-off is not)"""
    if up:
        if D >= 1.0:
            return ((tb, 1, 0),)
        tf = tb + D * th
        return ((tb, 1, 0), (tf, 0, 0), (tf + tdf, 0, 1))
    if D >= 1.0:
        return ((tb, 1, 0),) if Dprev >= 1.0 else ((tb, 0, 0), (tb + tdr, 1, 0))
    tr = tb + (1.0 - D) * th
    if Dprev >= 1.0:
        return ((tb, 0, 0), (tb + tdf, 0, 1), (tr, 0, 0), (tr + tdr, 1, 0))
    return ((tb, 0, 1), (tr, 0, 0), (tr + tdr, 1, 0))


def seg_at(segs, t):
    s = segs[0]
    for x in segs:
        if x[0] <= t + 1e-15:
            s = x
    return s[1], s[2]


# plant dead times per cell when a study does not set them: (leg A rising, leg A falling, leg B rising, leg B falling)
# as fractions of the design range dt_range_ns, deliberately off the firmware's midpoint estimate and different per leg
DT_PLANT_FR = [(1.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 1.0), (0.7, 0.3, 0.3, 0.7), (0.3, 0.7, 0.7, 0.3)]


def dt_of(fr):
    """per-edge dead times [s] from fractions of the design range"""
    lo, hi = A("dt_range_ns")
    return tuple((lo + f * (hi - lo)) * 1e-9 for f in fr)


def trip_layers(P, oc_backup_t=None):
    """hardware trip layers as built (rev E): (name, threshold, delay to gates off after the detected crossing).
    oc: on the true inductor current (the PVCELL local window's stated gates-off time includes its own sensor), backup
    CMPSS on AN1 = sensor lag + chain; ov: on the port voltage as the ADC sees it (divider RC + isolated amplifier);
    port: on the port current as the ADC sees it (AMC3302 chain)"""
    t = P["trips"]
    tb = P["tau_i"] + A("t_trip_chain_s") if oc_backup_t is None else oc_backup_t
    return dict(oc=[("local", t["oc_local"][0], t["t_local_us"][1] * 1e-6), ("CTRL backup", t["oc_backup"][0], tb)],
                ov=[("PPB", t["ov_ppb"][0], A("ppb_period_s") + A("t_ppb_chain_s")),
                    ("CMPSS backup", t["ov_cmpss"][0], A("t_trip_chain_s"))],
                port=[("port SC", t["port_sc"][0], A("t_trip_chain_s")),
                      ("port OC", t["port_oc"][0], A("ppb_period_s") + A("t_ppb_chain_s"))])


def clamp_v(P, N, I, with_clamp=True):
    """static reverse conduction of one port of the module at total current I >= 0 (bus- -> bus+): per cell the bus
    reverse clamp (cell_spec bus_reverse_clamp, n devices in parallel) and the leg's two series body-diode positions
    (2 devices each).  Returns (V across the bank, A per clamp device, A per body-diode device)."""
    n = P["clamp"].get("devices_per_bank", 8) if with_clamp else 0
    vto, rt, v0, rb = A("clamp_vto_V"), A("clamp_rt_ohm"), A("body_v0_V"), A("body_r_ohm")
    ic = I / N
    if n and ic <= n * (2 * v0 - vto) / rt:
        v = vto + ic * rt / n
    elif n:
        v = (ic + n * vto / rt + 2 * v0 / rb) / (n / rt + 1.0 / rb)
    else:
        v = 2 * v0 + rb * ic
    return v, (max(v - vto, 0.0) / rt if n else 0.0), max(v - 2 * v0, 0.0) / rb / 2.0


def simulate(P, G, sc, t_end, switched=True, fine=None, h_max=None, seed=1):
    """time-domain simulation of N cells on shared port banks with the digital controller.
    sc keys: N, pa / pb (port dicts), va0, vb0, i0 (list), cmd(t, cs), t_en (gate enable), events [(t, fn(sim))],
             ltol (list, relative L error), sgain / soff (current-sensor errors), td (per cell 4 edge dead times [s]),
             hw (bool: hardware trips), layers (dict as trip_layers()), clamp (bool: bus reverse clamp fitted),
             noise (bool), freeze_ref (t) optional: from t the controller output is frozen (protection-only studies).
    Returns a dict of logged arrays (one row per sampling slot) + fine waveforms between fine=(t0, t1)."""
    N = sc["N"]
    T, th = P["T"], P["Ts"]
    tdc = [tuple(x) for x in sc.get("td", [dt_of(DT_PLANT_FR[k % 4]) for k in range(N)])]
    rng = np.random.default_rng(seed)
    R, ca, cb = P["R"], sc.get("CA", P["C_cell"] * N + P["C_x"]), sc.get("CB", P["C_cell"] * N + P["C_x"])
    ltab, lstep = P["ltab"], P["lstep"]
    nl = len(ltab) - 1
    lfac = [1.0 + x for x in sc.get("ltol", [0.0] * N)]
    sgain, soff = sc.get("sgain", [0.0] * N), sc.get("soff", [0.0] * N)
    noise = sc.get("noise", True)
    hw = sc.get("hw", True)
    layers = sc.get("layers") or trip_layers(P)
    with_clamp = sc.get("clamp", bool(P["clamp"]))
    vdio = 2 * A("body_v0_V")
    tau_i, tau_rc, tau_amc, tau_ip = P["tau_i"], P["tau_rc"], P["tau_amc"], P["tau_ip"]
    tsoc = G["t_soc"]
    hmax = h_max or 1.0
    pa, pb = sc["pa"], sc["pb"]
    # sampling boundaries of all cells within one period: offset -> [(cell, up)]
    bnd = {}
    for k in range(N):
        for h in (0, 1):
            bnd.setdefault(round(((k / N) + 0.5 * h) % 1.0, 12), []).append((k, h == 0))
    offs = sorted(bnd)
    nb = len(offs)
    # state
    i = list(sc.get("i0", [0.0] * N))
    va, vb = sc["va0"], sc["vb0"]
    iea = ext_cur(pa, va, 0.0)[0] if pa["kind"] != "bat" else sc.get("iea0", 0.0)
    ieb = ext_cur(pb, vb, 0.0)[0] if pb["kind"] != "bat" else sc.get("ieb0", 0.0)
    fi = list(i)
    fva1 = fva2 = va
    fvb1 = fvb2 = vb
    fia, fib = ext_cur(pa, va, iea)[0], -ext_cur(pb, vb, ieb)[0]
    cs = ctrl_init(P, G, sc, va, vb)
    cells = cs["cells"]
    if sc.get("start_on"):                                              # start a run in steady state
        for k, c in enumerate(cells):
            c.update(on=True, q=R * i[k], q_f=R * i[k])
            if "fixed_duty" in sc:
                c.update(DA=sc["fixed_duty"][0], DB=sc["fixed_duty"][1], DAp=sc["fixed_duty"][0], DBp=sc["fixed_duty"][1],
                         mode="band")
    tb, upk = [0.0] * N, [True] * N
    for k in range(N):                      # each cell's last carrier boundary at or before t = 0 (zero = up-half)
        m = math.floor(-k / N * 2.0 + 1e-9)
        tb[k], upk[k] = (k / N + 0.5 * m) * T, m % 2 == 0
    dprev = [(1.0, 1.0)] * N
    last_im = list(i)
    pend_sample = {}                     # cell -> sample time
    samples = {}                         # cell -> (im, va, vb)
    trip_at = [None] * N                 # scheduled hardware gate-off time per cell
    hw_flag = {"oc": [None] * N, "oc_layer": [None] * N, "ov": None, "ov_layer": None, "ov_off": None, "port": None,
               "port_layer": None, "port_off": None}
    fired = set()
    clamp = {"a": [0.0, 0.0, 0.0, 0.0, 0.0], "b": [0.0, 0.0, 0.0, 0.0, 0.0]}   # I module, A/clamp dev, A/body dev, I2t, V
    events = sorted(sc.get("events", []), key=lambda e: e[0])
    ev_k, ev_at = 0, []
    t_en = sc.get("t_en", 0.0)
    iref = [0.0] * N
    m_last = dict(va=va, vb=vb, ia=fia, ib=fib)
    n_slots = int(round(t_end / T)) * nb
    lg = {k: np.zeros(n_slots) for k in ("t", "va", "vb", "iea", "ieb", "vam", "vbm")}
    for k in ("i", "im", "iref", "da", "db", "mode"):
        lg[k] = np.zeros((n_slots, N))
    lg["sel"] = [""] * n_slots
    fw = {"t": [], "i": [], "va": [], "vb": []} if fine else None
    energy = {"ea": 0.0, "eb": 0.0, "er": 0.0}
    diode = {"a": 0.0, "b": 0.0}      # peak reverse (clamp + body diode) current per port, module total
    mode_code = {"buck": 0, "band": 1, "boost": 2}
    sim = dict(sc=sc, cs=cs, P=P)

    def lval(k, x):
        j = int(abs(x) / lstep)
        return (ltab[j] if j < nl else ltab[nl]) * lfac[k]

    def filt(y, x0, x1, tau, h):
        """exact first-order response to an input that is linear over h"""
        e = math.exp(-h / tau)
        m = (x1 - x0) / h
        return x1 - m * tau + (y - x0 + m * tau) * e

    for j in range(n_slots):
        per, jo = divmod(j, nb)
        t0 = (per + offs[jo]) * T
        t1 = (per + (offs[jo + 1] if jo + 1 < nb else 1.0 + offs[0])) * T
        while ev_k < len(events) and events[ev_k][0] <= t0 + 1e-12:
            ev_at.append(t0)                                # events act at the next slot boundary
            ov = events[ev_k][1](sim) or {}
            iea, ieb = ov.get("iea", iea), ov.get("ieb", ieb)
            ev_k += 1
        pa, pb = sc["pa"], sc["pb"]
        for p in (pa, pb):
            if "Vfun" in p:
                p["V"] = p["Vfun"](t0)
            if "Pfun" in p:
                p["P"] = p["Pfun"](t0)
        # --- boundary: load pending duties of the cells whose half period starts now, schedule their samples
        for k, up in bnd[offs[jo]]:
            c = cells[k]
            dprev[k] = (c["DA"], c["DB"])
            c["DA"], c["DB"] = c["DAp"], c["DBp"]
            tb[k], upk[k] = t0, up
            pend_sample[k] = t0 + tsoc
            if not c["on"] and not c["fault"] and t0 >= t_en and not cs["trip"]:
                da0, db0, md = duties_ss(P, max(m_last["va"], 1.0), max(m_last["vb"], 1.0))
                if up:                                   # enable at a carrier zero with the feed-forward duties
                    c.update(on=True, DA=da0, DB=db0, DAp=da0, DBp=db0, mode=md, q=0.0, q_f=0.0, i_mod=0.0)
                    dprev[k] = (da0, db0)
        # --- outer loop at cell 0's carrier zero (32 kHz), port measurements sampled now
        if (0, True) in bnd[offs[jo]]:
            m_last = dict(va=quant(fva2, P["lsb_v"], P["sig_v"] * noise, rng, lo=0, hi=P["v_fs_adc"]),
                          vb=quant(fvb2, P["lsb_v"], P["sig_v"] * noise, rng, lo=0, hi=P["v_fs_adc"]),
                          ia=quant(fia, P["lsb_ip"], P["sig_ip"] * noise, rng),
                          ib=quant(fib, P["lsb_ip"], P["sig_ip"] * noise, rng))
            if "freeze_ref" in sc and t0 >= sc["freeze_ref"]:
                pass
            else:
                iref = ctrl_outer(P, G, cs, sc, m_last, t0)
            if sc.get("on_outer"):
                sc["on_outer"](sim, t0, m_last)
        # --- gate segments for this slot
        segs = []
        for k in range(N):
            c = cells[k]
            if not c["on"] or c["fault"] or (trip_at[k] is not None and trip_at[k] <= t0):
                segs.append(None)
                continue
            if switched:
                tk = tdc[k]
                segs.append((leg_segs(tb[k], upk[k], c["DA"], dprev[k][0], tk[0], tk[1], th),
                             leg_segs(tb[k], upk[k], c["DB"], dprev[k][1], tk[2], tk[3], th)))
            else:
                segs.append("avg")
        bps = {t1}
        for k in range(N):
            if segs[k] is not None and segs[k] != "avg":
                for leg in segs[k]:
                    for x in leg:
                        if t0 < x[0] < t1:
                            bps.add(x[0])
            if trip_at[k] is not None and t0 < trip_at[k] < t1:
                bps.add(trip_at[k])
        for k, ts_ in pend_sample.items():
            if t0 < ts_ < t1:
                bps.add(ts_)
        bps = sorted(bps)
        ta = t0
        for tb_ in bps:
            h_all = tb_ - ta
            if h_all <= 1e-15:
                ta = tb_
                continue
            # connection of each cell's nodes to the rails (frozen over the sub-interval)
            sa, sb, off = [0.0] * N, [0.0] * N, [False] * N
            for k in range(N):
                gone = segs[k] is None or (trip_at[k] is not None and trip_at[k] <= ta + 1e-15)
                if gone:
                    off[k] = True
                    sa[k] = 1.0 if i[k] < 0 else 0.0
                    sb[k] = 1.0 if i[k] > 0 else 0.0
                    continue
                if segs[k] == "avg":
                    c = cells[k]
                    sa[k], sb[k] = conn_avg(P, c["DA"], c["DB"], va, vb, i[k], lval(k, i[k]), tdc[k])
                    continue
                ta_t, ta_b = seg_at(segs[k][0], ta)
                tb_t, tb_b = seg_at(segs[k][1], ta)
                sa[k] = 1.0 if (ta_t or (not ta_b and i[k] < 0)) else 0.0
                sb[k] = 1.0 if (tb_t or (not tb_b and i[k] > 0)) else 0.0
            nsub = max(1, int(math.ceil(h_all / hmax - 1e-9)))
            h = h_all / nsub
            for _ in range(nsub):
                # Heun step
                i0, va0, vb0, iea0, ieb0 = list(i), va, vb, iea, ieb
                ca_, dca = ext_cur(pa, va0, iea0)
                cb_, dcb = ext_cur(pb, vb0, ieb0)
                # all gates off: the current freewheels through two body diodes (drop vdio against it)
                di1 = [0.0 if (off[k] and i0[k] == 0.0) else (sa[k] * va0 - sb[k] * vb0 - R * i0[k] -
                                                               (vdio * math.copysign(1.0, i0[k]) if off[k] else 0.0))
                       / lval(k, i0[k]) for k in range(N)]
                sA = sum(sa[k] * i0[k] for k in range(N))
                sB = sum(sb[k] * i0[k] for k in range(N))
                dva1, dvb1 = (ca_ - sA) / ca, (cb_ + sB) / cb
                ip = [i0[k] + h * di1[k] for k in range(N)]
                vap, vbp = va0 + h * dva1, vb0 + h * dvb1
                ieap, iebp = iea0 + h * dca, ieb0 + h * dcb
                ca2, dca2 = ext_cur(pa, vap, ieap)
                cb2, dcb2 = ext_cur(pb, vbp, iebp)
                di2 = [0.0 if (off[k] and i0[k] == 0.0) else (sa[k] * vap - sb[k] * vbp - R * ip[k] -
                                                               (vdio * math.copysign(1.0, i0[k]) if off[k] else 0.0))
                       / lval(k, ip[k]) for k in range(N)]
                sA2 = sum(sa[k] * ip[k] for k in range(N))
                sB2 = sum(sb[k] * ip[k] for k in range(N))
                i = [i0[k] + 0.5 * h * (di1[k] + di2[k]) for k in range(N)]
                for k in range(N):
                    if off[k] and i[k] * i0[k] < 0.0:
                        i[k] = 0.0
                va = va0 + 0.5 * h * (dva1 + (ca2 - sA2) / ca)
                vb = vb0 + 0.5 * h * (dvb1 + (cb2 + sB2) / cb)
                iea = iea0 + 0.5 * h * (dca + dca2)
                ieb = ieb0 + 0.5 * h * (dcb + dcb2)
                # a port bank cannot reverse beyond its reverse clamp + the legs' body diodes, which carry the external
                # branch's freewheeling current (static sharing, clamp_v)
                for port, v_, ie_, pp, sx in (("a", va, iea, pa, sA2), ("b", vb, ieb, pb, -sB2)):
                    if v_ < 0.0:
                        ic_ = max(sx - ext_cur(pp, v_, ie_)[0], 0.0)
                        vcl, i_dev, i_body = clamp_v(P, N, ic_, with_clamp)
                        cl = clamp[port]
                        cl[0], cl[1], cl[2], cl[4] = max(cl[0], ic_), max(cl[1], i_dev), max(cl[2], i_body), max(cl[4], vcl)
                        cl[3] += i_dev ** 2 * h
                        diode[port] = cl[0]
                        if port == "a":
                            va = -vcl
                        else:
                            vb = -vcl
                cur_a, cur_b = ext_cur(pa, va, iea)[0], ext_cur(pb, vb, ieb)[0]
                energy["ea"] += 0.5 * h * (ca_ * va0 + cur_a * va)
                energy["eb"] -= 0.5 * h * (cb_ * vb0 + cur_b * vb)
                energy["er"] += 0.5 * h * R * sum(i0[k] ** 2 + i[k] ** 2 for k in range(N))
                fi = [filt(fi[k], i0[k], i[k], tau_i, h) for k in range(N)]
                fva1n, fvb1n = filt(fva1, va0, va, tau_rc, h), filt(fvb1, vb0, vb, tau_rc, h)
                fva2, fvb2 = filt(fva2, fva1, fva1n, tau_amc, h), filt(fvb2, fvb1, fvb1n, tau_amc, h)
                fva1, fvb1 = fva1n, fvb1n
                fia = filt(fia, ca_, cur_a, tau_ip, h)
                fib = filt(fib, -cb_, -cur_b, tau_ip, h)
                ta += h
                for k in range(N):                  # a scheduled gate-off takes effect within the sub-step
                    if not off[k] and trip_at[k] is not None and trip_at[k] <= ta + 1e-15:
                        off[k] = True
                        sa[k], sb[k] = (1.0 if i[k] < 0 else 0.0), (1.0 if i[k] > 0 else 0.0)
                # hardware trip layers (rev E, trip_layers): detection -> gates off after the layer's delay; latching
                if hw:
                    for k in range(N):
                        if cells[k]["on"]:
                            for name, thr, dly in layers["oc"]:
                                if (k, name) not in fired and abs(i[k]) > thr:
                                    fired.add((k, name))
                                    if trip_at[k] is None or ta + dly < trip_at[k]:
                                        trip_at[k], hw_flag["oc"][k], hw_flag["oc_layer"][k] = ta + dly, ta, name
                    for key, sig, lay in (("ov", max(fva2, fvb2), layers["ov"]), ("port", max(abs(fia), abs(fib)), layers["port"])):
                        for name, thr, dly in lay:
                            if (key, name) not in fired and sig > thr:
                                fired.add((key, name))
                                t_off = ta + dly
                                if hw_flag[key] is None or t_off < hw_flag[key + "_off"]:
                                    hw_flag[key], hw_flag[key + "_layer"], hw_flag[key + "_off"] = ta, name, t_off
                                for k in range(N):
                                    if trip_at[k] is None or t_off < trip_at[k]:
                                        trip_at[k] = t_off
                if fw is not None and fine[0] <= ta <= fine[1]:
                    fw["t"].append(ta)
                    fw["i"].append(list(i))
                    fw["va"].append(va)
                    fw["vb"].append(vb)
            ta = tb_
            for k in list(pend_sample):
                if abs(pend_sample[k] - ta) < 1e-12:
                    samples[k] = (quant(fi[k], P["lsb_il"], P["sig_il"] * noise, rng, sgain[k], soff[k]),
                                  quant(fva2, P["lsb_v"], P["sig_v"] * noise, rng, lo=0, hi=P["v_fs_adc"]),
                                  quant(fvb2, P["lsb_v"], P["sig_v"] * noise, rng, lo=0, hi=P["v_fs_adc"]))
                    del pend_sample[k]
        # --- end of slot: run the inner loops of the cells sampled in this slot
        for k in list(samples):
            c = cells[k]
            im, vam, vbm = samples.pop(k)
            if "fixed_duty" in sc:
                c.update(DAp=sc["fixed_duty"][0], DBp=sc["fixed_duty"][1])
            elif c["on"] and not c["fault"] and trip_at[k] is None:
                ctrl_cell(P, G, c, im, vam, vbm, iref[k], may_change=not upk[k])
            last_im[k] = im
        lg["im"][j] = last_im
        for k in range(N):
            if trip_at[k] is not None and trip_at[k] <= t1 and not cells[k]["fault"]:
                cells[k].update(on=False, fault=True)
        lg["t"][j], lg["va"][j], lg["vb"][j] = t1, va, vb
        lg["iea"][j], lg["ieb"][j] = ext_cur(pa, va, iea)[0], ext_cur(pb, vb, ieb)[0]     # into the module
        lg["vam"][j], lg["vbm"][j] = m_last["va"], m_last["vb"]
        lg["i"][j] = i
        lg["iref"][j] = iref
        lg["da"][j] = [c["DA"] for c in cells]
        lg["db"][j] = [c["DB"] for c in cells]
        lg["mode"][j] = [mode_code[c["mode"]] for c in cells]
        lg["sel"][j] = cs["sel"]
    lg["energy"] = energy
    lg["events_at"] = ev_at
    lg["diode"] = diode
    lg["clamp"] = clamp
    lg["hw"] = hw_flag
    lg["trip_at"] = trip_at
    lg["cs"] = cs
    lg["fine"] = {k: np.array(v) for k, v in fw.items()} if fw else None
    lg["state"] = dict(i=i, va=va, vb=vb, iea=iea, ieb=ieb)
    lg["CA"], lg["CB"] = ca, cb
    return lg


# ------------------------------------------------------------------------------------------------ small-signal model
NX = 12      # plant states: i, va, vb, iea, ieb, fi, fva1, fva2, fvb1, fvb2, fia, fib


def op_point(P, N, va, vb, pw, use, g_rel=1.0):
    """operating point of the aggregated module: power pw [W] (+ = A->B), use = 'mppt' | 'cvb_cpl' | 'cva_cpl' |
    'cvb_bat' | 'cur'; g_rel = PV incremental conductance relative to I/V (1 at the MPP)"""
    da, db, mode = duties_ss(P, va, vb)
    i = pw / (N * da * va)
    op = dict(N=N, va=va, vb=vb, pw=pw, i=i, da=da, db=db, mode=mode, L=L_of(P, i), C=N * P["C_cell"] + P["C_x"],
              use=use, ia=N * da * i, ib=N * db * i, g_rel=g_rel)
    bat = ("bat", A("batt_R"), A("batt_L"))
    if use == "mppt":
        op["pa"], op["pb"] = ("g", g_rel * op["ia"] / va), bat
    elif use == "cvb_cpl":
        op["pa"], op["pb"] = bat, ("g", -op["ib"] * vb / vb ** 2)        # CPL absorbing P_B = I_B V_B: g = -P/V^2
    elif use == "cva_cpl":
        op["pa"], op["pb"] = ("g", op["ia"] * va / va ** 2), bat         # P_A absorbed = -I_A V_A
    else:
        op["pa"], op["pb"] = bat, bat
    op["outer"] = {"mppt": "VA", "cva_cpl": "VA", "cvb_cpl": "VB", "cvb_bat": "VB"}.get(use)
    return op


def lin_plant(P, op):
    """continuous linearised averaged plant (aggregated identical cells): x' = A x + B [dDA, dDB]"""
    N, L, R, C = op["N"], op["L"], P["R"], op["C"]
    I, VA, VB, DA, DB = op["i"], op["va"], op["vb"], op["da"], op["db"]
    Am, Bm = np.zeros((NX, NX)), np.zeros((NX, 2))
    Am[0, 0], Am[0, 1], Am[0, 2], Bm[0, 0], Bm[0, 1] = -R / L, DA / L, -DB / L, VA / L, -VB / L
    Am[1, 3], Am[1, 0], Bm[1, 0] = 1 / C, -N * DA / C, -N * I / C
    Am[2, 4], Am[2, 0], Bm[2, 1] = 1 / C, N * DB / C, N * I / C
    for row, port, vcol in ((3, op["pa"], 1), (4, op["pb"], 2)):
        if port[0] == "bat":
            Am[row, row], Am[row, vcol] = -port[1] / port[2], -1 / port[2]
        else:                                           # algebraic port i = -g v, as a 0.1 us follower
            Am[row, row], Am[row, vcol] = -1e7, -port[1] * 1e7
    for row, src, tau in ((5, 0, P["tau_i"]), (6, 1, P["tau_rc"]), (7, 6, P["tau_amc"]), (8, 2, P["tau_rc"]),
                          (9, 8, P["tau_amc"]), (10, 3, P["tau_ip"])):
        Am[row, row], Am[row, src] = -1 / tau, 1 / tau
    Am[11, 11], Am[11, 4] = -1 / P["tau_ip"], -1 / P["tau_ip"]   # port-B shunt measures the current to the load
    return Am, Bm


def zoh(Am, Bm, h):
    n, m = Bm.shape
    M = np.zeros((n + m, n + m))
    M[:n, :n], M[:n, n:] = Am, Bm
    E = sla.expm(M * h)
    return E[:n, :n], E[:n, n:]


def mod_sens(P, G, op):
    """numerical sensitivities of the modulator duties to u, measured V_A, measured V_B at the operating point"""
    mode = op["mode"]
    u0 = op["da"] * op["va"] - op["db"] * op["vb"]

    def f(u, va, vb):
        da, db, _, _ = modulate(P, G, mode, u, 0.0, 0.0, va, vb, may_change=False)
        return np.array([da, db])
    du, dv = 1e-3, 1e-3
    mu = (f(u0 + du, op["va"], op["vb"]) - f(u0 - du, op["va"], op["vb"])) / (2 * du)
    ma = (f(u0, op["va"] + dv, op["vb"]) - f(u0, op["va"] - dv, op["vb"])) / (2 * dv)
    mb = (f(u0, op["va"], op["vb"] + dv) - f(u0, op["va"], op["vb"] - dv)) / (2 * dv)
    return mu, ma, mb


def lin_loops(P, G, op, kff=0.0, fgrid=None):
    """exact sampled-data loop gains of the linearised averaged model.
    Inner loop at Ts (sample at t_k + t_soc, one-sample delay, ZOH); outer loop at Tv = 2 Ts, lifted.
    Returns dict with frequency grids, L_i, L_v (or None), closed-loop spectral radii."""
    Ts, Tv, N = G["Ts"], G["Tv"], op["N"]
    Am, Bm = lin_plant(P, op)
    Phi, Gam = zoh(Am, Bm, Ts)
    Phit, Gamt = zoh(Am, Bm, G["t_soc"])
    mu, ma, mb = mod_sens(P, G, op)
    n = NX + 3                                      # X = [x, D(2), q]
    Sy = np.zeros((NX, n))                          # plant state at the sampling instant t_k + t_soc
    Sy[:, :NX], Sy[:, NX:NX + 2] = Phit, Gamt
    yi, yva, yvb = Sy[5], Sy[7], Sy[9]
    eq = np.zeros(n)
    eq[n - 1] = 1.0
    kp, ki = G["kp"] + G["ki"] * Ts, G["ki"]          # ctrl_cell integrates before use: C(z) = kp + ki Ts z/(z-1)
    Acl, Bcl = np.zeros((n, n)), np.zeros(n)
    Acl[:NX, :NX], Acl[:NX, NX:NX + 2] = Phi, Gam
    Acl[NX:NX + 2] = np.outer(mu, -kp * yi + eq) + np.outer(ma, yva) + np.outer(mb, yvb)
    Bcl[NX:NX + 2] = mu * kp
    Acl[n - 1] = eq - ki * Ts * yi
    Bcl[n - 1] = ki * Ts
    Aol = Acl.copy()
    Aol[NX:NX + 2] = np.outer(ma, yva) + np.outer(mb, yvb)
    Bol = np.zeros(n)
    Bol[NX:NX + 2] = mu
    Col = -kp * yi + eq
    f = fgrid if fgrid is not None else np.logspace(0, math.log10(0.499 / Ts), 700)
    zi = np.exp(1j * 2 * math.pi * f * Ts)
    Li = np.array([-(Col @ np.linalg.solve(z * np.eye(n) - Aol, Bol)) for z in zi])
    res = dict(f=f, Li=Li, rho_i=max(abs(np.linalg.eigvals(Acl))), op=op)
    if op["outer"] is None:
        return res
    # outer loop, lifted over two inner samples
    # port-current reference -> cell reference through the cells' actual duty (a state of the lifted model)
    if op["outer"] == "VA":
        cvo, cff, sig, d0, i0, kd = NX - 5, 10, 1.0, op["da"], op["ia"], NX
    else:
        cvo, cff, sig, d0, i0, kd = 9, 11, -1.0, op["db"], op["ib"], NX + 1
    ev = np.zeros(n)
    ev[cvo] = 1.0
    ef = np.zeros(n)
    ef[cff] = 1.0
    Kc = np.zeros(n)
    Kc[kd] = -i0 / (N * d0 ** 2)
    b = 1.0 / (N * d0)
    A2 = Acl @ Acl
    AB = (Acl + np.eye(n)) @ Bcl
    m = n + 1
    Al = np.zeros((m, m))
    Al[:n, :n] = A2 + np.outer(AB, Kc)
    Al[n, :n] = G["kiv"] * Tv * sig * ev
    Al[n, n] = 1.0
    Bl = np.zeros(m)
    Bl[:n] = AB * b
    Cl = np.zeros(m)
    Cl[:n] = kff * ef + G["kpv"] * sig * ev
    Cl[n] = 1.0
    fv = f[f < 0.499 / Tv]
    zv = np.exp(1j * 2 * math.pi * fv * Tv)
    Lv = np.array([-(Cl @ np.linalg.solve(z * np.eye(m) - Al, Bl)) for z in zv])
    Avcl = Al + np.outer(Bl, Cl)
    res.update(fv=fv, Lv=Lv, rho_v=max(abs(np.linalg.eigvals(Avcl))), Al=Al, Bl=Bl, Cl=Cl)
    return res


def margins(f, L):
    """phase margin (min over gain crossovers), gain margins (upper / lower) at -180 deg crossings, modulus margin"""
    mag = np.abs(L)
    ph = np.unwrap(np.angle(L))
    pm, fc = np.inf, None
    for k in range(len(f) - 1):
        if (mag[k] - 1) * (mag[k + 1] - 1) <= 0 and mag[k] != mag[k + 1]:
            x = (1 - mag[k]) / (mag[k + 1] - mag[k])
            p = ph[k] + x * (ph[k + 1] - ph[k])
            pmk = (math.degrees(p) + 180.0) % 360.0
            pmk = pmk - 360.0 if pmk > 180.0 else pmk
            if pmk < pm:
                pm, fc = pmk, f[k] + x * (f[k + 1] - f[k])
    gm_up, gm_lo = np.inf, np.inf
    ph_deg = np.degrees(ph)
    for k in range(len(f) - 1):            # crossings of the negative real axis: phase = -180 + 360 n
        n0, n1 = math.floor((ph_deg[k] + 180.0) / 360.0), math.floor((ph_deg[k + 1] + 180.0) / 360.0)
        if n0 != n1:
            target = -180.0 + 360.0 * max(n0, n1)
            x = (target - ph_deg[k]) / (ph_deg[k + 1] - ph_deg[k])
            g = 1.0 / (mag[k] + x * (mag[k + 1] - mag[k]))
            if g > 1:
                gm_up = min(gm_up, g)
            else:
                gm_lo = min(gm_lo, 1.0 / g)
    return dict(pm=pm, fc=fc, gm_db=20 * math.log10(gm_up) if np.isfinite(gm_up) else np.inf,
                gm_lo_db=20 * math.log10(gm_lo) if np.isfinite(gm_lo) else np.inf, mm=float(np.min(np.abs(1 + L))))


KFF_USE = {"mppt": 0.0, "cvb_bat": 0.0, "cvb_cpl": A("k_ff_bus"), "cva_cpl": A("k_ff_bus"), "cur": 0.0}   # by port type
VCORNERS = (250.0, 550.0, 950.0, 1000.0)
PV_G = {"left": None, "mpp": 1.0, "right": None}          # filled from the array model (relative conductance)


def pv_g_rel():
    """PV incremental conductance g = -dI/dV relative to I/V, left of the MPP (0.85 V_mp) and right (1.06 V_mp)"""
    arr = pvm.array(17, 8)
    vm, im, pm = pvm.mpp(arr, 1000.0, 25.0)
    out = {}
    for key, x in (("left", 0.85), ("mpp", 1.0), ("right", 1.06)):
        v = np.array([x * vm - 0.5, x * vm + 0.5])
        i = pvm.array_i(arr, np.full(2, 1000.0), 25.0, v)
        out[key] = float(-(i[1] - i[0]) / 1.0 * (x * vm) / np.mean(i))
    return out


def corner_cases(P, N, g_rel):
    """(use, va, vb, pw, label) over the 250/550/950/1000 V corners of both ports, light (5 %) and full load, both
    directions where the use case allows them"""
    cases = []
    for va in VCORNERS:
        for vb in VCORNERS:
            plim = min(N * P["pmax"], N * P["imax"] * min(va, vb))
            for load, fr in (("full", 1.0), ("light", 0.05)):
                p = fr * plim
                for side, g in g_rel.items():
                    cases.append(("mppt", va, vb, p, f"PV {side}", load, g))
                cases.append(("cvb_cpl", va, vb, p, "CPL on B, A->B", load, 1.0))
                cases.append(("cvb_cpl", va, vb, -p, "CP source on B, B->A", load, 1.0))
                cases.append(("cva_cpl", va, vb, -p, "CPL on A, B->A", load, 1.0))
                cases.append(("cva_cpl", va, vb, p, "CP source on A, A->B", load, 1.0))
                cases.append(("cvb_bat", va, vb, p, "battery on B (CV), A->B", load, 1.0))
                cases.append(("cur", va, vb, p, "current loop, A->B", load, 1.0))
                cases.append(("cur", va, vb, -p, "current loop, B->A", load, 1.0))
    return cases


def stability_sweep(P, G, N, g_rel, fgrid=None):
    rows = []
    fgrid = np.logspace(-2, math.log10(0.499 / G["Ts"]), 420) if fgrid is None else fgrid
    for use, va, vb, pw, label, load, g in corner_cases(P, N, g_rel):
        op = pc_op = op_point(P, N, va, vb, pw, use, g_rel=g)
        r = lin_loops(P, G, op, kff=KFF_USE[use], fgrid=fgrid)
        mi = margins(r["f"], r["Li"])
        row = dict(N=N, use=use, label=label, load=load, va=va, vb=vb, p_kW=pw / 1e3, mode=op["mode"], i_cell=op["i"],
                   L_uH=op["L"] * 1e6, fc_i=mi["fc"], pm_i=mi["pm"], gm_i=mi["gm_db"], mm_i=mi["mm"], rho_i=r["rho_i"])
        if "Lv" in r:
            mv = margins(r["fv"], r["Lv"])
            row.update(fc_v=mv["fc"], pm_v=mv["pm"], gm_v=mv["gm_db"], gm_lo_v=mv["gm_lo_db"], mm_v=mv["mm"], rho=r["rho_v"])
        else:
            row.update(fc_v=None, pm_v=None, gm_v=None, gm_lo_v=None, mm_v=None, rho=r["rho_i"])
        rows.append(row)
    return rows


def lin_step(P, G, op, kff, which, nsteps):
    """closed-loop step response of the sampled linear model: 'i' = unit current-reference step (output: sampled
    measured current, per fast sample); 'v' = unit voltage-reference step (output: true port voltage, per outer
    sample).  Used to check the small-signal model against the time-domain simulators."""
    r = lin_loops(P, G, op, kff=kff, fgrid=np.array([10.0]))
    Ts = G["Ts"]
    Am, Bm = lin_plant(P, op)
    Phi, Gam = zoh(Am, Bm, Ts)
    Phit, Gamt = zoh(Am, Bm, G["t_soc"])
    mu, ma, mb = mod_sens(P, G, op)
    n = NX + 3
    Sy = np.zeros((NX, n))
    Sy[:, :NX], Sy[:, NX:NX + 2] = Phit, Gamt
    eq = np.zeros(n)
    eq[n - 1] = 1.0
    Acl, Bcl = np.zeros((n, n)), np.zeros(n)
    Acl[:NX, :NX], Acl[:NX, NX:NX + 2] = Phi, Gam
    kpe = G["kp"] + G["ki"] * Ts
    Acl[NX:NX + 2] = np.outer(mu, -kpe * Sy[5] + eq) + np.outer(ma, Sy[7]) + np.outer(mb, Sy[9])
    Bcl[NX:NX + 2] = mu * kpe
    Acl[n - 1] = eq - G["ki"] * Ts * Sy[5]
    Bcl[n - 1] = G["ki"] * Ts
    if which == "i":
        X, y = np.zeros(n), []
        for _ in range(nsteps):
            y.append(Sy[5] @ X)
            X = Acl @ X + Bcl
        return np.array(y)
    Al, Bl, Cl = r["Al"], r["Bl"], r["Cl"]
    sig = 1.0 if op["outer"] == "VA" else -1.0
    vrow = 1 if op["outer"] == "VA" else 2
    m = Al.shape[0]
    bq = np.zeros(m)
    bq[n] = -G["kiv"] * G["Tv"] * sig
    Z, y = np.zeros(m), []
    for _ in range(nsteps):
        y.append(Z[vrow])
        rp = Cl @ Z - G["kpv"] * sig
        Z = Al @ Z + Bl * rp + bq
    return np.array(y)


# ------------------------------------------------------------------------------------------------ study helpers
def ss_sc(P, N, va, vb, i_cell, pa=None, pb=None, **kw):
    """scenario dict starting in steady state at (va, vb, i per cell): each cell's current includes its ripple offset
    at its own carrier phase at t = 0, a default battery port's EMF includes its R drop"""
    da, db, _ = duties_ss(P, va, vb)
    iea0, ieb0 = N * da * i_cell, -N * db * i_cell
    pa = pa or dict(port_bat(va), V=va + A("batt_R") * iea0)          # EMF = terminal + R x (current out of the EMF)
    pb = pb or dict(port_bat(vb), V=vb + A("batt_R") * ieb0)
    L, T, th = L_of(P, i_cell), P["T"], P["Ts"]
    ha, hb, vbar = min(da, 1.0) * th, min(db, 1.0) * th, va * min(da, 1.0) - vb * min(db, 1.0)

    def off(t):
        return (va * min(t, ha) - vb * min(t, hb) - vbar * t) / L
    i0 = []
    for k in range(N):
        tr = (-k * T / N) % T
        i0.append(float(i_cell) + (off(tr) if tr <= th else -off(T - tr)))
    sc = dict(N=N, pa=pa, pb=pb, va0=float(va), vb0=float(vb), i0=i0, t_en=0.0, start_on=True, i_init=float(i_cell),
              iea0=iea0, ieb0=ieb0)
    sc.update(kw)
    return sc


def fine_avg(fw, k, t0, t1):
    """time average of the true inductor current of cell k over [t0, t1] from the fine (piecewise-linear) record"""
    t, i = fw["t"], fw["i"][:, k]
    m = (t >= t0) & (t <= t1)
    return float(np.trapezoid(i[m], t[m]) / (t[m][-1] - t[m][0]))


def l_energy(P, i):
    """inductor energy with L(i): integral of i L(i) di"""
    x = np.linspace(0, abs(i), 200)
    return float(np.trapezoid(x * np.interp(x, P["Li"], P["Lv"]), x))


def savefig(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, name), dpi=110)
    import matplotlib.pyplot as plt
    plt.close(fig)


# ------------------------------------------------------------------------------------------------ studies
def study_agreement(P, G):
    """switched vs averaged vs small-signal: current steps in buck / boost / band; outer-loop steps against a CPL in
    boost (RHP zero) and against a PV array; steady-state duties against the analytic mode map; energy balance"""
    out = dict(steps=[], outer=[], duty=[], energy=None)
    for va, vb in ((900.0, 600.0), (600.0, 900.0), (800.0, 805.0)):
        da, db, md = duties_ss(P, va, vb)
        i0, i1 = 20.0, 30.0
        op = op_point(P, 3, va, vb, 3 * 25.0 * da * va, "cur")
        yl = lin_step(P, G, op, 0.0, "i", 160)
        res = {}
        for sw in (True, False):
            sc = ss_sc(P, 3, va, vb, i0, cmd=lambda t, cs: ("I", i0 if t < 2e-3 else i1), noise=False)
            lg = simulate(P, G, sc, 5e-3, switched=sw, fine=(1.0e-3, 1.9e-3) if sw else None)
            sel = np.arange(len(lg["t"])) % 3 == 0
            tt, ii = lg["t"][sel], lg["im"][sel].mean(axis=1)
            j0 = np.searchsorted(tt, 2e-3 + 1e-9)
            base = np.mean(ii[j0 - 40:j0])
            res[sw] = ((ii[j0:j0 + 160] - base) / (i1 - i0), lg)
        lgs = res[True][1]
        fw = lgs["fine"]
        # steady state before the step: duties vs the analytic map incl. R drop and each cell's own dead times
        j = np.searchsorted(lgs["t"], 1.9e-3)
        jj = (lgs["t"] > 1.0e-3) & (lgs["t"] < 1.9e-3)
        i_k = [fine_avg(fw, k, 1.0e-3, 1.9e-3) for k in range(3)]
        i_avg = float(np.mean(i_k))
        da_s, db_s = float(np.mean(lgs["da"][jj])), float(np.mean(lgs["db"][jj]))
        va_s, vb_s = float(np.mean(lgs["va"][jj])), float(np.mean(lgs["vb"][jj]))
        vl_res = 0.0
        for k in range(3):                       # average inductor voltage left in steady state (~0), worst cell
            sa, sb = conn_avg(P, float(np.mean(lgs["da"][jj, k])), float(np.mean(lgs["db"][jj, k])), va_s, vb_s, i_k[k],
                              L_of(P, i_k[k]), dt_of(DT_PLANT_FR[k]))
            r_ = sa * va_s - sb * vb_s - P["R"] * i_k[k]
            vl_res = r_ if abs(r_) > abs(vl_res) else vl_res
        ripple = float(np.max(fw["i"][:, 0]) - np.min(fw["i"][:, 0]))
        L = L_of(P, i_k[0])                      # cell 0's ripple, with its effective (dead-time) duties
        sa0, sb0 = conn_avg(P, float(np.mean(lgs["da"][jj, 0])), float(np.mean(lgs["db"][jj, 0])), va_s, vb_s, i_k[0], L,
                            dt_of(DT_PLANT_FR[0]))
        if md == "buck":
            rip_an = (va_s - vb_s) * sa0 * P["T"] / L
        elif md == "boost":
            rip_an = va_s * (1 - sb0) * P["T"] / L
        else:
            rip_an = None
        out["duty"].append(dict(mode=md, va=va_s, vb=vb_s, i=i_avg, da=da_s, db=db_s, da_map=duties_ss(P, va_s, vb_s)[0],
                                db_map=duties_ss(P, va_s, vb_s)[1], vl_residual=vl_res, ripple=ripple, ripple_an=rip_an))
        out["steps"].append(dict(mode=md, va=va, vb=vb, lin=yl, sw=res[True][0], avg=res[False][0],
                                 os_lin=yl.max() - 1, os_sw=res[True][0].max() - 1, os_avg=res[False][0].max() - 1,
                                 rms_sw=float(np.sqrt(np.mean((res[True][0] - yl) ** 2))),
                                 rms_avg=float(np.sqrt(np.mean((res[False][0] - yl) ** 2)))))
    # single cell and the 4-cell module (90 deg): same code, N = 1 / 4
    out["n_cells"] = []
    for N in (1, 4):
        Pn = load_params(N)
        Gn = design(Pn, N)
        va, vb = 900.0, 600.0
        da, db, _ = duties_ss(Pn, va, vb)
        op = op_point(Pn, N, va, vb, N * 25.0 * da * va, "cur")
        yl = lin_step(Pn, Gn, op, 0.0, "i", 160)
        res = {}
        for sw in (True, False):
            sc = ss_sc(Pn, N, va, vb, 20.0, cmd=lambda t, cs: ("I", 20.0 if t < 2e-3 else 30.0), noise=False)
            lg = simulate(Pn, Gn, sc, 5e-3, switched=sw)
            nb = 2 * N if N % 2 else N
            sel = np.arange(len(lg["t"])) % (nb // 2) == 0
            tt, ii = lg["t"][sel], lg["im"][sel].mean(axis=1)
            j0 = np.searchsorted(tt, 2e-3 + 1e-9)
            base = np.mean(ii[j0 - 40:j0])
            res[sw] = (ii[j0:j0 + 160] - base) / 10.0
        out["n_cells"].append(dict(N=N, os_lin=yl.max() - 1, os_sw=res[True].max() - 1, os_avg=res[False].max() - 1,
                                   rms_sw=float(np.sqrt(np.mean((res[True] - yl) ** 2))),
                                   rms_avg=float(np.sqrt(np.mean((res[False] - yl) ** 2)))))
    # outer loops
    arr = pvm.array(17, 8)
    vm, imp, pmp = pvm.mpp(arr, 1000.0, 25.0)
    cases = [("V_B loop, CPL 75 kW on B, boost 600->900 V (RHP zero)", "cvb_cpl", 600.0, 900.0, 75e3, 5.0, None),
             ("V_A loop, PV array at its MPP, battery on B", "mppt", vm, 800.0, pmp, -10.0, arr)]
    for label, use, va, vb, pw, dv, arr_ in cases:
        op = op_point(P, 3, va, vb, pw, use)
        kff = KFF_USE[use]
        yl = lin_step(P, G, op, kff, "v", 380)
        da, db, md = duties_ss(P, va, vb)
        i0 = pw / (3 * da * va)
        res = {}
        for sw in (True, False):
            if use == "cvb_cpl":
                sc = ss_sc(P, 3, va, vb, i0, pb=dict(kind="cpl", P=pw), kff_b=kff, noise=False,
                           cmd=lambda t, cs: ("VB", 900.0 if t < 10e-3 else 905.0))
            else:
                sc = ss_sc(P, 3, va, vb, i0, pa=port_pv(arr_, 1000.0, 25.0), kff_a=kff, noise=False,
                           cmd=lambda t, cs, vm=vm: ("VA", vm if t < 10e-3 else vm - 10.0, None, False))
            # 10 ms of settling first: the outer loop converts through the cells' actual duties, which the dead times
            # move by a few % from the ideal map used to initialise the run
            lg = simulate(P, G, sc, 22e-3, switched=sw)
            sel = np.arange(len(lg["t"])) % 6 == 5
            tt, vv = lg["t"][sel], lg["vb" if use == "cvb_cpl" else "va"][sel]
            j0 = np.searchsorted(tt, 10e-3)
            base = np.mean(vv[j0 - 30:j0])
            res[sw] = (vv[j0:j0 + 380] - base) / dv
        out["outer"].append(dict(label=label, lin=yl, sw=res[True], avg=res[False], os_lin=yl.max() - 1,
                                 os_sw=res[True].max() - 1, os_avg=res[False].max() - 1,
                                 rms_sw=float(np.sqrt(np.mean((res[True] - yl) ** 2))),
                                 rms_avg=float(np.sqrt(np.mean((res[False] - yl) ** 2)))))
    # energy balance over a switched transient (current step + port-voltage motion)
    sc = ss_sc(P, 3, 900.0, 600.0, 20.0, cmd=lambda t, cs: ("I", 20.0 if t < 1e-3 else 40.0))
    lg = simulate(P, G, sc, 4e-3, switched=True)
    st = lg["state"]
    e = lg["energy"]
    de_c = 0.5 * lg["CA"] * (st["va"] ** 2 - 900.0 ** 2) + 0.5 * lg["CB"] * (st["vb"] ** 2 - 600.0 ** 2)
    de_l = sum(l_energy(P, x) - l_energy(P, 20.0) for x in st["i"])
    de_s = 0.5 * A("batt_L") * (st["iea"] ** 2 - sc["iea0"] ** 2) + 0.5 * A("batt_L") * (st["ieb"] ** 2 - sc["ieb0"] ** 2)
    resid = e["ea"] - e["eb"] - e["er"] - de_c - de_l
    out["energy"] = dict(e_in=e["ea"], e_out=e["eb"], e_r=e["er"], de_c=de_c, de_l=de_l, de_src=de_s, residual=resid,
                         rel=abs(resid) / e["ea"])
    return out


# dead-time sets for the robustness runs: per cell (leg A rise, leg A fall, leg B rise, leg B fall) [ns]; together they
# put every pair of relevant edges (A rise + B fall for A->B, A fall + B rise for B->A) at 150/150, 500/500, 150/500
# and 500/150 ns, plus the firmware's own 325 ns and rise != fall inside one leg
DT_SETS_FR = {"min/max per leg": [(0, 0, 0, 0), (1, 1, 1, 1), (0, 0, 1, 1)],
              "asymmetric 1": [(1, 1, 0, 0), (0.5, 0.5, 0.5, 0.5), (1, 0, 0, 1)],
              "asymmetric 2": [(0, 1, 1, 0), (1, 0, 1, 0), (0, 1, 0, 1)]}


def dt_set(name):
    return [dt_of(c) for c in DT_SETS_FR[name]]


def dt_sets_ns():
    return {k: [tuple(round(x * 1e9) for x in dt_of(c)) for c in v] for k, v in DT_SETS_FR.items()}


def sweep_case(P, G, N, vb, v0, v1, i_cmd, t_sweep=0.030, switched=True, td=None):
    """V_A ramps v0 -> v1 (stiff programmable source) while port B is a battery at vb; current command i_cmd per cell"""
    da, db, _ = duties_ss(P, v0, vb)
    ilim = min(P["imax"], P["pmax"] / (da * v0))
    i0 = math.copysign(min(abs(i_cmd), ilim), i_cmd)
    pa = port_bat(v0)
    pa["Vfun"] = lambda t: v0 + (v1 - v0) * min(max((t - 0.002) / t_sweep, 0.0), 1.0)
    sc = ss_sc(P, N, v0, vb, i0, pa=pa, cmd=lambda t, cs: ("I", i_cmd))
    if td:
        sc["td"] = td
    lg = simulate(P, G, sc, t_sweep + 0.004, switched=switched)
    t = lg["t"]
    k = t > 0.0015
    err = lg["im"][k] - lg["iref"][k]
    mc = [int(np.sum(np.abs(np.diff(lg["mode"][k, c])) > 0)) for c in range(N)]
    ev = []                                   # mode changes: (cell, from, to, V_B/V_A)
    for c in range(N):
        mm = lg["mode"][:, c]
        for j in np.nonzero(np.diff(mm))[0]:
            ev.append((c, int(mm[j]), int(mm[j + 1]), float(lg["vb"][j] / lg["va"][j])))
    return dict(vb=vb, v0=v0, v1=v1, i_cmd=i_cmd, lg=lg, err_max=float(np.max(np.abs(err))),
                err_rms=float(np.sqrt(np.mean(err ** 2))), mode_changes=mc, i_peak=float(np.max(np.abs(lg["i"][k]))),
                va_rng=(float(lg["va"][k].min()), float(lg["va"][k].max())), vb_rng=(float(lg["vb"][k].min()), float(lg["vb"][k].max())),
                events=ev, td=td)


def study_transitions(P, G):
    """V_A swept through V_B in both directions, both power directions, at full current (V_B 550 V) and at full power
    (V_B 800 / 940 V), for every dead-time set (each cell of a set has its own per-edge dead times)"""
    cases = []
    for name in DT_SETS_FR:
        for vb, (lo, hi) in ((550.0, (450.0, 650.0)), (800.0, (690.0, 900.0)), (860.0, (740.0, 1000.0))):
            for i_cmd in (45.0, -45.0):
                for v0, v1 in ((lo, hi), (hi, lo)):
                    c = sweep_case(P, G, 3, vb, v0, v1, i_cmd, td=dt_set(name))
                    c["set"] = name
                    cases.append(c)
    return cases


def study_steps(P, G):
    """large current-reference steps at and next to V_A = V_B with the widest dead times: the band must not be left on
    the proportional kick (exit needs the filtered demand too)"""
    out = []
    for va, vb in ((800.0, 805.0), (800.0, 780.0), (780.0, 800.0)):
        for i0, i1 in ((20.0, 40.0), (40.0, 5.0), (30.0, -30.0)):
            sc = ss_sc(P, 3, va, vb, i0, cmd=lambda t, cs, i0=i0, i1=i1: ("I", i0 if t < 2e-3 else i1),
                       td=dt_set("min/max per leg"))
            lg = simulate(P, G, sc, 6e-3, switched=True)
            k = lg["t"] > 1.5e-3
            seq = []
            for c in range(3):
                m = lg["mode"][k, c]
                seq.append([int(m[0])] + [int(m[j + 1]) for j in np.nonzero(np.diff(m))[0]])
            kk = lg["t"] > 2e-3
            os_ = (np.max(lg["im"][kk]) - i1) if i1 > i0 else (i1 - np.min(lg["im"][kk]))
            out.append(dict(va=va, vb=vb, i0=i0, i1=i1, seq=seq, overshoot=float(os_), i_peak=float(np.max(np.abs(lg["i"][k])))))
    return out


def study_inlet(P):
    """firmware port-current limit vs inlet temperature (port_spec rev 6): the full-power window it leaves, and an
    averaged-model check that the limit holds (3 cells, PV port at 550 V; 4 cells, battery port at 600 V)"""
    tab = P["i_port_vs_inlet"]
    rows = []
    for N, ports in ((3, (("A", "pv"), ("B", "battery"))), (4, (("A", "pv"), ("B", "battery")))):
        Pn = load_params(N)
        for t in tab["inlet_C"]:
            for port, kind in ports:
                il = port_limit(Pn, kind, t)
                rows.append(dict(N=N, port=port, kind=kind, inlet_C=t, i_lim=il, v_min_rated=N * 25e3 / il,
                                 v_min_max=N * Pn["pmax"] / il))
    checks = []
    for N, va, vb, t, port in ((3, 550.0, 800.0, 60.0, "A"), (4, 550.0, 600.0, 60.0, "B")):
        Pn = load_params(N)
        Gn = design(Pn, N)
        sc = ss_sc(Pn, N, va, vb, 30.0, cmd=lambda t_, cs: ("I", 30.0 + 15.0 * min(max((t_ - 2e-3) / 5e-3, 0.0), 1.0)),
                   inlet_C=t, noise=False)      # supervisor command ramped 30 -> 45 A in 5 ms
        lg = simulate(Pn, Gn, sc, 30e-3, switched=False)
        k = lg["t"] > 25e-3
        i_port = float(np.mean(np.abs(lg["iea"][k] if port == "A" else lg["ieb"][k])))
        kind = "pv" if port == "A" else "battery"
        ip = np.abs(lg["iea"] if port == "A" else lg["ieb"])
        checks.append(dict(N=N, va=va, vb=vb, inlet_C=t, port=port, i_port=i_port, i_lim=port_limit(Pn, kind, t),
                           i_port_peak=float(np.max(ip[lg["t"] > 2e-3]))))
    return dict(rows=rows, checks=checks)


def study_identified(P, G, trans):
    """what identifying each edge's dead time (residual 0 instead of <= 175 ns) would buy at the transitions: the V_B
    900 V sweeps of two dead-time sets again, with the firmware estimate set to the plant value per cell"""
    out = []
    for c in trans:
        if c["vb"] == 860.0 and c["set"] in ("asymmetric 1", "min/max per leg"):
            ci = sweep_case_id(P, G, c["vb"], c["v0"], c["v1"], c["i_cmd"], dt_set(c["set"]))
            out.append(dict(set=c["set"], vb=c["vb"], v0=c["v0"], v1=c["v1"], i_cmd=c["i_cmd"], err_mid=c["err_max"],
                            err_id=ci["err_max"], mc_id=ci["mode_changes"]))
    return out


def band_edges(trans):
    """V_B / V_A at every mode change in the sweeps, per kind of change: (min, max)"""
    names = {(0, 1): "buck->band", (1, 0): "band->buck", (2, 1): "boost->band", (1, 2): "band->boost"}
    out = {}
    for c in trans:
        for cell, m0, m1, r in c["events"]:
            k = names.get((m0, m1), f"{m0}->{m1}")
            lo, hi = out.get(k, (r, r))
            out[k] = (min(lo, r), max(hi, r))
    return out


def sweep_case_id(P, G, vb, v0, v1, i_cmd, td):
    da, db, _ = duties_ss(P, v0, vb)
    i0 = math.copysign(min(abs(i_cmd), min(P["imax"], P["pmax"] / (da * v0))), i_cmd)
    pa = port_bat(v0)
    pa["Vfun"] = lambda t: v0 + (v1 - v0) * min(max((t - 0.002) / 0.030, 0.0), 1.0)
    sc = ss_sc(P, 3, v0, vb, i0, pa=pa, cmd=lambda t, cs: ("I", i_cmd), td=td, td_hat=td)
    lg = simulate(P, G, sc, 0.034, switched=True)
    k = lg["t"] > 0.0015
    return dict(err_max=float(np.max(np.abs(lg["im"][k] - lg["iref"][k]))),
                mode_changes=[int(np.sum(np.abs(np.diff(lg["mode"][k, c])) > 0)) for c in range(3)])


def study_zero_crossing(P, G):
    """where the dead-time compensation acts on a current near zero: a current reversal in buck (ripple crosses zero)
    and a slow reversal in band (almost no ripple: all four edges switch sign together), for every dead-time set and
    for the compensation driven by the reference (design) and by the measured current (alternative)"""
    out = []
    for comp in ("ref", "meas"):
        out += zero_crossing_runs(P, dict(G, dt_comp=comp), comp)
    return out


def zero_crossing_runs(P, G, comp):
    out = []
    for name in DT_SETS_FR:
        for label, va, vb, i_a, i_b, t_r in (("buck 800->600 V, +40 -> -40 A in 4 ms", 800.0, 600.0, 40.0, -40.0, 4e-3),
                                             ("band 800->805 V, +8 -> -8 A in 8 ms", 800.0, 805.0, 8.0, -8.0, 8e-3)):
            sc = ss_sc(P, 3, va, vb, i_a, cmd=lambda t, cs, a=i_a, b=i_b, tr=t_r: ("I", a + (b - a) * min(max((t - 2e-3) / tr, 0.0), 1.0)),
                       td=dt_set(name))
            lg = simulate(P, G, sc, t_r + 4e-3, switched=True)
            k = lg["t"] > 1.5e-3
            e = lg["im"][k] - lg["iref"][k]
            near = k & (np.abs(lg["iref"][:, 0]) < 10.0)
            en = lg["im"][near] - lg["iref"][near]
            out.append(dict(comp=comp, set=name, label=label, err_max=float(np.max(np.abs(e))), err_rms=float(np.sqrt(np.mean(e ** 2))),
                            near_rms=float(np.sqrt(np.mean(en ** 2))), near_max=float(np.max(np.abs(en))),
                            i_peak=float(np.max(np.abs(lg["i"][k]))), lg=lg))
    return out


def study_sharing(P, G):
    """current sharing with inductance and current-sensor mismatches (worst-case sign pattern)"""
    out = []
    for N in (3, 4):
        Pn = load_params(N)
        Gn = design(Pn, N)
        lt = [A("L_mismatch"), -A("L_mismatch"), 0.5 * A("L_mismatch"), -0.5 * A("L_mismatch")][:N]
        for label, ge, oe in (("calibrated", A("sens_gain_err"), A("sens_off_err_A")), ("uncalibrated", 0.02, 1.0)):
            sg = [ge, -ge, ge, -ge][:N]
            so = [oe, -oe, -oe, oe][:N]
            for va, vb in ((950.0, 550.0), (550.0, 950.0)):
                da, db, _ = duties_ss(Pn, va, vb)
                i1 = min(Pn["imax"], Pn["pmax"] / (da * va))
                i0 = 0.5 * i1
                sc = ss_sc(Pn, N, va, vb, i0, cmd=lambda t, cs, i0=i0, i1=i1: ("I", i0 if t < 4e-3 else i1),
                           ltol=lt, sgain=sg, soff=so)
                lg = simulate(Pn, Gn, sc, 12e-3, switched=True, fine=(3.0e-3, 12e-3))
                fw = lg["fine"]
                avg = [fine_avg(fw, k, 10e-3, 12e-3) for k in range(N)]
                mean = float(np.mean(avg))
                # dynamic: per-period averages during the step
                pk = float(np.max(np.abs(fw["i"][(fw["t"] > 4e-3) & (fw["t"] < 6e-3)])))
                expect = [(i1 - o) / (1 + g) for g, o in zip(sg, so)]
                out.append(dict(N=N, label=label, va=va, vb=vb, i_ref=i1, avg=avg, mean=mean,
                                spread=max(avg) - min(avg), dev_max=max(abs(a - mean) for a in avg),
                                dev_pct=100 * max(abs(a - mean) for a in avg) / Pn["imax"], expect=expect,
                                peak=pk, lg=lg, ltol=lt, sgain=sg, soff=so))
    return out


def study_reversal(P, G):
    """(a) BMS-style current command ramped through zero; (b) bus forming on B with a constant-power load that
    reverses (inverter turns from inverting to rectifying)"""
    out = {}
    sc = ss_sc(P, 3, 800.0, 600.0, 40.0, cmd=lambda t, cs: ("I", 40.0 - 80.0 * min(max((t - 2e-3) / 4e-3, 0.0), 1.0)))
    lg = simulate(P, G, sc, 9e-3, switched=True)
    k = lg["t"] > 1.5e-3
    err = lg["im"][k] - lg["iref"][k]
    out["ramp"] = dict(lg=lg, err_max=float(np.max(np.abs(err))), err_rms=float(np.sqrt(np.mean(err ** 2))))
    pw = 75e3
    va, vb = 700.0, 750.0
    da, db, _ = duties_ss(P, va, vb)
    i0 = pw / (3 * da * va)
    pb = dict(kind="cpl", P=pw, Pfun=lambda t: pw - 2 * pw * min(max((t - 3e-3) / 2e-3, 0.0), 1.0))
    sc = ss_sc(P, 3, va, vb, i0, pb=pb, kff_b=A("k_ff_bus"), cmd=lambda t, cs: ("VB", 750.0))
    lg = simulate(P, G, sc, 15e-3, switched=True)
    k = lg["t"] > 2e-3
    out["cpl"] = dict(lg=lg, vb_min=float(lg["vb"][k].min()), vb_max=float(lg["vb"][k].max()),
                      i_end=float(np.mean(lg["im"][-60:])), i_peak=float(np.max(np.abs(lg["i"][k]))))
    return out


def mppt_hook(prm, ramp=2000.0):
    """MPPT (sim/pv_mppt.py algorithm) run inside the time-domain model on the 32 kHz outer-loop samples, with a
    soft-start ramp limiter on the V_A reference"""
    st = dict(s=None, acc=[])

    def hook(sim, t, m):
        sc, cs = sim["sc"], sim["cs"]
        if t < sc["t_en"] or cs["trip"]:
            return
        if st["s"] is None:
            st["s"] = pvm.mppt_init(prm, m["va"], t)
            st["tick"] = t + 0.5 * prm["T_p"]
            sc["vref"] = sc["vr"] = m["va"]
        st["acc"].append((t, m["va"], m["va"] * m["ia"]))
        if t >= st["tick"]:
            w = [x for x in st["acc"] if x[0] > t - prm["w"]]
            vbar = sum(x[1] for x in w) / len(w)
            pbar = sum(x[2] for x in w) / len(w)
            sc["vref"] = pvm.mppt_tick(prm, st["s"], vbar, pbar, cs["limited"], t)
            st["tick"] += 0.5 * prm["T_p"]
            st["acc"] = w
        step = ramp * sim["P"]["T"]
        sc["vr"] += min(max(sc["vref"] - sc["vr"], -step), step)
    return hook


def precharge(P, C, src, v0=0.0, h=50e-6):
    """resistor precharge exactly as port_spec (R_pre, |dV| <= dV_ok to close, supervision curve after blanking);
    src(v_bus, r) -> current from the terminal into the bus through r.  Returns (t, v, i, t_close, ok)"""
    r = P["r_pre"]
    t, v, ts, vs, is_ = 0.0, v0, [], [], []
    v_term = src(v0, 0.0, open_circuit=True)
    t_out = P["pre"]["t_out_s"]
    while t < t_out:
        i = src(v, r)
        ts.append(t)
        vs.append(v)
        is_.append(i)
        if v_term - v <= P["dv_ok"]:
            return np.array(ts), np.array(vs), np.array(is_), t, True
        if t > P["t_blank"] and v < P["k_check"] * v_term * (1 - math.exp(-t / P["tau_max"])):
            return np.array(ts), np.array(vs), np.array(is_), t, False
        v += h * i / C
        t += h
    return np.array(ts), np.array(vs), np.array(is_), t, False


def study_startup(P, G):
    """start-up: precharge port A from the PV array and port B from the battery (port_spec sequence), close the main
    contactors at |dV| <= 10 V, enable the gates with feed-forward duties, MPPT from V_oc with a reference ramp"""
    N = 3
    C = N * P["C_cell"] + P["C_x"]
    arr = pvm.array(17, 8)
    g0, tc = 300.0, 25.0
    pa = port_pv(arr, g0, tc)
    vbat = 800.0

    def pv_src(v, r, open_circuit=False):
        if open_circuit:
            return float(pvm.array_curve(arr, g0, tc)[0][-1])
        lo, hi = 0.0, ext_cur(pa, 0.0, 0.0)[0]
        for _ in range(40):                          # I = I_pv(v + I r), monotonic -> bisection
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if ext_cur(pa, v + mid * r, 0.0)[0] > mid else (lo, mid)
        return 0.5 * (lo + hi)

    def bat_src(v, r, open_circuit=False):
        return vbat if open_circuit else (vbat - v) / (r + A("batt_R"))
    ta, va_t, ia_t, tca, oka = precharge(P, C, pv_src)
    tb, vb_t, ib_t, tcb, okb = precharge(P, C, bat_src)
    voc = pv_src(0, 0, True)
    # main contactor closing inrush: battery RLC (exact, 0.1 us steps), PV algebraic
    vb0 = vb_t[-1]
    Am = np.array([[-A("batt_R") / A("batt_L"), -1 / A("batt_L")], [1 / C, 0.0]])
    Ph, Gm = zoh(Am, np.array([[1 / A("batt_L")], [0.0]]), 0.1e-6)
    x, ib_in = np.array([0.0, vb0]), []
    for _ in range(20000):
        x = Ph @ x + Gm[:, 0] * vbat
        ib_in.append(x[0])
    va0 = va_t[-1]
    ia_in = ext_cur(pa, va0, 0.0)[0]
    # converter start: sequence time origin = both contactors closed + 20 ms (precharge relays open)
    prm = dict(pvm.MPPT)
    sc = dict(N=N, pa=pa, pb=port_bat(vbat), va0=voc - 0.5, vb0=vbat, i0=[0.0] * N, t_en=2e-3, kff_a=0.0, kff_b=0.0,
              vref=voc, vr=voc, cmd=lambda t, cs: ("VA", sc["vr"], 1010.0, False))
    sc["on_outer"] = mppt_hook(prm)
    lg = simulate(P, G, sc, 0.60, switched=False)
    vm, im, pm = pvm.mpp(arr, g0, tc)
    k = lg["t"] > 0.45
    p_end = float(np.mean(lg["va"][k] * lg["iea"][k]))
    j_en = np.searchsorted(lg["t"], 2e-3)
    return dict(pre_a=(ta, va_t, ia_t, tca, oka), pre_b=(tb, vb_t, ib_t, tcb, okb), voc=voc, ib_inrush=float(np.max(np.abs(ib_in))),
                ia_inrush=ia_in, lg=lg, p_end=p_end, pmpp=pm, vmpp=vm, eta_start=p_end / pm,
                i_after_enable=float(np.max(np.abs(lg["i"][j_en:j_en + 60]))), C=C)


def stability(P, N):
    Pn = load_params(N) if N != P["N"] else P
    Gn = design(Pn, N)
    g = pv_g_rel()
    rows = stability_sweep(Pn, Gn, N, g)
    # inductance robustness of the current loop: catalog tolerance and the brief's +-10 % on top of the L(i) curve
    lrob = []
    for fac in (0.90, 0.92, 1.08, 1.10):
        for va, vb, pw in ((250.0, 1000.0, N * 45 * 250.0), (1000.0, 250.0, N * 45 * 250.0), (550.0, 550.0, N * 45 * 550.0)):
            for i_over in (0.0, Pn["i_trip"]):                       # L at zero current (max) and at the trip (min)
                op = op_point(Pn, N, va, vb, pw, "cur")
                op["L"] = fac * float(np.interp(i_over, Pn["Li"], Pn["Lv"]))
                r = lin_loops(Pn, Gn, op, fgrid=np.logspace(1, math.log10(0.499 / Gn["Ts"]), 300))
                m = margins(r["f"], r["Li"])
                lrob.append(dict(fac=fac, va=va, vb=vb, L_uH=op["L"] * 1e6, pm=m["pm"], gm=m["gm_db"], fc=m["fc"], rho=r["rho_i"]))
    # the same CPL corners without the load-current feed-forward
    noff = []
    for va in VCORNERS:
        for vb in VCORNERS:
            p = min(N * Pn["pmax"], N * Pn["imax"] * min(va, vb))
            op = op_point(Pn, N, va, vb, p, "cvb_cpl")
            r = lin_loops(Pn, Gn, op, kff=0.0, fgrid=np.logspace(0, math.log10(0.499 / Gn["Tv"]), 300))
            noff.append(dict(va=va, vb=vb, rho=r["rho_v"], stable=r["rho_v"] < 1.0))
    # numbers quoted in the report notes
    C = N * Pn["C_cell"] + Pn["C_x"]
    worst = min((r for r in rows if r["use"] == "cvb_cpl"), key=lambda r: r["pm_v"])
    op = op_point(Pn, N, worst["va"], worst["vb"], worst["p_kW"] * 1e3, "cvb_cpl")
    fg = np.logspace(0, math.log10(0.499 / Gn["Tv"]), 400)
    pm_asis = margins(*[lin_loops(Pn, Gn, op, kff=A("k_ff_bus"), fgrid=fg)[k] for k in ("fv", "Lv")])["pm"]
    P20 = dict(Pn, tau_rc=1 / (2 * math.pi * 20e3))
    pm_20k = margins(*[lin_loops(P20, Gn, op, kff=A("k_ff_bus"), fgrid=fg)[k] for k in ("fv", "Lv")])["pm"]
    rz = [r for r in rows if r["use"] == "cvb_cpl" and r["va"] == 250.0 and r["vb"] == 1000.0 and r["load"] == "full"
          and r["p_kW"] > 0][0]
    f_rhpz = 250.0 / (2 * math.pi * L_of(Pn, 45.0) * 45.0)
    notes = dict(C=C, f_cpl=N * Pn["imax"] / (250.0 * C) / (2 * math.pi), pm_rc_asis=pm_asis, pm_rc_20k=pm_20k,
                 f_rhpz=f_rhpz, fc_rhpz=rz["fc_v"], ph_rhpz=math.degrees(math.atan(rz["fc_v"] / f_rhpz)))
    return dict(rows=rows, lrob=lrob, noff=noff, G=Gn, P=Pn, g_rel=g, notes=notes)


def ov_case(P, G, kind, layers, switched=False):
    """port B opens while the control is frozen (inner current loops alive): kind 'power' = 82.5 kW from the PV array
    at the power limit with V_B 1000 V; 'current' = 45 A per cell (135 A) from a stiff port A at 1000 V with V_B 611 V,
    the steepest crossing of 1100 V (port B current 135 A x D_B).  Returns the log (layers = trip layers in force)."""
    N = 3
    if kind == "power":
        arr9 = pvm.array(17, 9)
        vm9, im9, pm9 = pvm.mpp(arr9, 1000.0, 25.0)
        vv = np.linspace(vm9, vm9 * 1.15, 4000)
        pp = vv * pvm.array_i(arr9, np.full_like(vv, 1000.0), 25.0, vv)
        v_op = float(vv[np.argmin(np.abs(pp - N * P["pmax"]))]) if pm9 > N * P["pmax"] else vm9
        vb0 = 1000.0
        i_op = min(N * P["pmax"], pm9) / (N * duties_ss(P, v_op, vb0)[0] * v_op)
        pa, va0, cmd = port_pv(arr9, 1000.0, 25.0), v_op, (lambda t, cs: ("VA", vm9, 1010.0, False))
    else:
        va0, vb0, i_op = 1000.0, 611.0, P["imax"]
        pa, cmd = None, (lambda t, cs: ("I", P["imax"]))

    def reject(sim):
        sim["sc"]["pb"] = dict(kind="open")
    sc = ss_sc(P, N, va0, vb0, i_op, pa=pa, kff_a=0.0, kff_b=A("k_ff_bus"), cmd=cmd, events=[(2e-3, reject)],
               freeze_ref=2e-3, uv_check=False, layers=layers)
    t_end = 4e-3 if kind == "power" else 4.5e-3
    lg = simulate(dict(P, v_ov_sw=1e9), G, sc, t_end, switched=switched)   # firmware OV disabled: hardware only
    j = np.searchsorted(lg["t"], 2e-3)
    v = lg["vb"]
    offs = [x for x in lg["trip_at"] if x is not None]
    t_off = min(offs) if offs else None
    v_off = float(np.interp(t_off, lg["t"], v)) if t_off else None
    if v[j:].max() > P["v_ovp"]:
        jc = j + int(np.argmax(v[j:] > P["v_ovp"]))
        t_cross = float(np.interp(P["v_ovp"], v[jc - 1:jc + 1], lg["t"][jc - 1:jc + 1]))
        slope = float((v[jc] - v[jc - 64]) / (lg["t"][jc] - lg["t"][jc - 64])) / 1e6
    else:                                    # tripped before the true voltage reached 1100 V
        t_cross, slope = None, None
    t_resp = (t_off - t_cross) * 1e6 if (t_off and t_cross and v_off >= P["v_ovp"]) else None
    return dict(kind=kind, t_resp_us=t_resp, slope=slope, v_off=v_off, v_peak=float(v.max()),
                layer=lg["hw"]["ov_layer"], lg=lg)


SHORT_CASES = [("port B short, buck A->B 1000->600 V", 1000.0, 600.0, "pb", 1.0),
               ("port B short, boost A->B 600->1000 V", 600.0, 1000.0, "pb", 1.0),
               ("port B short, band A->B 1000->1000 V (fastest di/dt)", 1000.0, 1000.0, "pb", 1.0),
               ("port A short, buck A->B 1000->600 V (current reverses)", 1000.0, 600.0, "pa", 1.0),
               ("port A short, boost B->A 1000->600 V", 1000.0, 600.0, "pa", -1.0)]


def short_case(P, G, case, variant, layers, clamp=True):
    label, va, vb, port, sgn = case
    da, db, _ = duties_ss(P, va, vb)
    i0 = sgn * min(P["imax"], P["pmax"] / (da * va))

    def short(sim, port=port):
        sim["sc"][port] = dict(kind="bat", V=0.0, R=5e-3, L=1.0e-6)
    sc = ss_sc(P, 3, va, vb, i0, cmd=lambda t, cs, i0=i0: ("I", i0), events=[(0.1e-3, short)], hw=layers is not None,
               layers=layers, uv_check=False, noise=False, clamp=clamp)
    t_end = 0.60e-3 if variant.startswith("terminal") else 0.20e-3
    lg = simulate(dict(P, v_ov_sw=1e9, v_uv=-1.0), G, sc, t_end, switched=True, h_max=0.05e-6,
                  fine=(0.09e-3, min(t_end, 0.30e-3)))
    fw = lg["fine"]
    ii = np.max(np.abs(fw["i"]), axis=1)
    t_sh = lg["events_at"][0]
    row = dict(label=label, variant=variant, va=va, vb=vb, i0=i0, i_peak=float(ii.max()), fw=fw, t_short=t_sh)
    oc = [(x, n) for x, n in zip(lg["hw"]["oc"], lg["hw"]["oc_layer"]) if x is not None]
    first = []
    if oc:
        first.append((min(oc)[0], min(oc)[1]))
    if lg["hw"]["port"] is not None:
        first.append((lg["hw"]["port"], lg["hw"]["port_layer"]))
    if first:
        tf, name = min(first)
        row.update(t_detect_us=(tf - t_sh) * 1e6, first=name)
    offs = [x for x in lg["trip_at"] if x is not None]
    row["t_off_us"] = (min(offs) - t_sh) * 1e6 if offs else None
    if layers is None:                       # no trip: rise times from each layer's upper band edge to L = 50 % L0
        t_ = fw["t"]
        tr = P["trips"]
        for key, thr in (("local", tr["oc_local"][2]), ("backup", tr["oc_backup"][2]), ("nominal", P["i_trip"])):
            if ii.max() > P["i50"]:
                kc = int(np.argmax(ii > thr))
                ks = int(np.argmax(ii > P["i50"]))
                row["t_" + key + "_to_sat_us"] = float(t_[ks] - t_[kc]) * 1e6
                row["didt_" + key] = (P["i50"] - thr) / row["t_" + key + "_to_sat_us"]
            else:
                row["t_" + key + "_to_sat_us"] = None
    cl = lg["clamp"][port[1]]
    row.update(clamp_module_A=cl[0], clamp_dev_A=cl[1], body_dev_A=cl[2], clamp_I2t=cl[3], v_reverse=cl[4])
    return row


def study_protection(P, G):
    out = {}
    N = 3
    tr = P["trips"]
    lay = trip_layers(P)
    # ---- P1 load rejection at full power, V_B = 1000 V, with the control acting
    arr9 = pvm.array(17, 9)
    vm9, im9, pm9 = pvm.mpp(arr9, 1000.0, 25.0)
    vv = np.linspace(vm9, vm9 * 1.15, 4000)
    pp = vv * pvm.array_i(arr9, np.full_like(vv, 1000.0), 25.0, vv)
    v_op = float(vv[np.argmin(np.abs(pp - N * P["pmax"]))]) if pm9 > N * P["pmax"] else vm9
    i_op = min(N * P["pmax"], pm9) / (N * duties_ss(P, v_op, 1000.0)[0] * v_op)

    def reject(sim):
        sim["sc"]["pb"] = dict(kind="open")
    sc = ss_sc(P, N, v_op, 1000.0, i_op, pa=port_pv(arr9, 1000.0, 25.0), kff_a=0.0, kff_b=A("k_ff_bus"),
               cmd=lambda t, cs: ("VA", vm9, 1010.0, False), events=[(2e-3, reject)])
    lg = simulate(P, G, sc, 12e-3, switched=True)
    out["rej_ctrl"] = dict(lg=lg, vb_peak=float(lg["vb"].max()), trip=lg["cs"]["trip"], hw=lg["hw"]["ov"], v_op=v_op, i_op=i_op)
    # ---- P2 over-voltage protection only (control frozen): requirement curve and the rev E layers as built
    ov = {}
    for kind in ("power", "current"):
        rows = []
        for extra in (0.0, 20e-6, 50e-6, 100e-6, 150e-6, 200e-6, 300e-6):     # requirement: ideal 1100 V detector
            r = ov_case(P, G, kind, dict(oc=lay["oc"], port=[], ov=[("1100 V + delay", P["v_ovp"], A("t_trip_chain_s") + extra)]))
            r["extra_us"] = extra * 1e6
            rows.append(r)
        built = []
        for per in (A("ppb_period_s"), P["T"]):
            for thr in tr["ov_ppb"][1:] + (tr["ov_ppb"][0],):
                r = ov_case(P, G, kind, dict(oc=lay["oc"], port=[], ov=[("PPB", thr, per + A("t_ppb_chain_s"))]))
                r.update(path="PPB", thr=thr, period_us=per * 1e6)
                built.append(r)
        for thr in tr["ov_cmpss"][1:] + (tr["ov_cmpss"][0],):
            r = ov_case(P, G, kind, dict(oc=lay["oc"], port=[], ov=[("CMPSS backup", thr, A("t_trip_chain_s"))]))
            r.update(path="CMPSS backup (PPB failed)", thr=thr, period_us=0.0)
            built.append(r)
        ov[kind] = dict(curve=rows, built=built)
    out["ov"] = ov
    # ---- P3 short circuits: terminal (all layers), bank (port shunt blind: local OC, then CTRL backup), no trip
    t_amc = 2.1e-6 + 1 / (2 * math.pi * A("f_afe_cell_Hz")) + A("t_trip_chain_s")     # AMC3302 as drawn (max delay)
    variants = [("terminal short, all trips as built", lay),
                ("bank short, local OC at its upper band", dict(oc=[("local", tr["oc_local"][2], tr["t_local_us"][1] * 1e-6)],
                                                               port=[], ov=lay["ov"])),
                ("bank short, local failed: CTRL backup at its upper band", dict(oc=[("CTRL backup", tr["oc_backup"][2],
                                                                                      lay["oc"][1][2])], port=[], ov=lay["ov"])),
                ("no trip", None)]
    shorts = []
    for case in SHORT_CASES:
        for vname, lays in variants:
            shorts.append(short_case(P, G, case, vname, lays))
    worst = SHORT_CASES[2]
    shorts.append(short_case(P, G, worst, "bank short, CTRL backup with the AMC3302 as drawn",
                             dict(oc=[("CTRL backup", tr["oc_backup"][2], t_amc)], port=[], ov=lay["ov"])))
    shorts.append(short_case(P, G, SHORT_CASES[0], "terminal short WITHOUT the reverse clamp", lay, clamp=False))
    out["shorts"] = shorts
    out["t_amc"] = t_amc
    # ---- P4 loss of one cell at full load (MPPT on the PV array, power limit active)
    def lose(sim):
        sim["cs"]["cells"][2]["fault"] = True
    sc = ss_sc(P, N, v_op, 800.0, min(N * P["pmax"], pm9) / (N * duties_ss(P, v_op, 800.0)[0] * v_op),
               pa=port_pv(arr9, 1000.0, 25.0), kff_a=0.0, kff_b=0.0, cmd=lambda t, cs: ("VA", vm9, 1010.0, False),
               events=[(3e-3, lose)])
    lg = simulate(P, G, sc, 30e-3, switched=True)
    k = lg["t"] > 3e-3
    out["cell_loss"] = dict(lg=lg, i_samp_max=float(np.max(lg["im"][k][:, :2])), i_peak=float(np.max(np.abs(lg["i"][k][:, :2]))),
                            va_max=float(lg["va"][k].max()), p_before=float(np.mean((lg["va"] * lg["iea"])[(lg["t"] > 1e-3) & (lg["t"] < 3e-3)])),
                            p_after=float(np.mean((lg["va"] * lg["iea"])[lg["t"] > 25e-3])), trip=lg["cs"]["trip"],
                            hw=[x for x in lg["hw"]["oc"]])
    # ---- P5 loss of the PV source (array disconnected at full power)
    def pv_off(sim):
        sim["sc"]["pa"] = dict(kind="open")
    sc = ss_sc(P, N, v_op, 800.0, min(N * P["pmax"], pm9) / (N * duties_ss(P, v_op, 800.0)[0] * v_op),
               pa=port_pv(arr9, 1000.0, 25.0), kff_a=0.0, kff_b=0.0, cmd=lambda t, cs: ("VA", vm9, 1010.0, False),
               events=[(3e-3, pv_off)])
    lg = simulate(P, G, sc, 15e-3, switched=True)
    k = lg["t"] > 3e-3
    out["pv_loss"] = dict(lg=lg, va_min=float(lg["va"][k].min()), i_min=float(lg["i"][k].min()), trip=lg["cs"]["trip"],
                          i_peak=float(np.max(np.abs(lg["i"][k]))), vb_max=float(lg["vb"][k].max()))
    # ---- P6 load rejection when the module itself forms the bus on B (CPL 82.5 kW -> 0)
    da, db, _ = duties_ss(P, 800.0, 950.0)
    i0 = N * P["pmax"] / (N * da * 800.0)
    pb = dict(kind="cpl", P=N * P["pmax"], Pfun=lambda t: N * P["pmax"] if t < 2e-3 else 0.0)
    sc = ss_sc(P, N, 800.0, 950.0, i0, pb=pb, kff_b=A("k_ff_bus"), cmd=lambda t, cs: ("VB", 950.0))
    lg = simulate(P, G, sc, 10e-3, switched=True)
    out["rej_vb"] = dict(lg=lg, vb_peak=float(lg["vb"].max()), trip=lg["cs"]["trip"])
    return out


def ripple_bias(P, G):
    """synchronous-sampling error of the port voltages: value at the outer-loop sampling instant minus the period
    average, raw (capacitor node) and after the port-board RC + AMC3330 chain (what the ADC sees)"""
    out = []
    for va, vb in ((950.0, 550.0), (550.0, 950.0), (800.0, 800.0)):
        da, db, _ = duties_ss(P, va, vb)
        i = min(P["imax"], P["pmax"] / (da * va))
        sc = ss_sc(P, 3, va, vb, i, cmd=lambda t, cs, i=i: ("I", i), noise=False, pa=dict(kind="cpl", P=-3 * da * va * i))
        sc["pb"] = dict(kind="cpl", P=3 * db * vb * i)
        lg = simulate(P, G, sc, 1.2e-3, switched=True, fine=(0.6e-3, 1.2e-3))
        fw = lg["fine"]
        T = P["T"]
        t0s = np.arange(math.ceil(0.6e-3 / T) * T, 1.2e-3 - T, T)
        raw = [float(np.interp(t, fw["t"], fw["va"]) - np.mean(fw["va"][(fw["t"] >= t) & (fw["t"] < t + T)])) for t in t0s]
        out.append(dict(va=va, vb=vb, i=i, va_pp=float(np.ptp(fw["va"][fw["t"] > 0.9e-3])), bias_raw=float(np.mean(raw))))
    return out


def ngspice_check(P, G):
    """independent check of one switched waveform: one cell in band mode (both legs switching, 200 ns dead time,
    body diodes) with fixed duties, ngspice (ideal switches + diodes) against the switched model, 5 periods"""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("ngspice"):
        return None
    va, vb, i0, T, td = 800.0, 790.0, 30.0, P["T"], P["td_gates"][1] * 1e-9      # longest dead time at the gates
    da, db = P["Dmax"] * vb / va, P["Dmax"]
    L = L_of(P, i0)
    rs, ls = 1e-3, 1e-6
    C = P["C_cell"] + P["C_x"]

    def gate(name, node, D, top):
        if top:                                  # on [0, D T/2] and [T - D T/2 + td, T] -> two pulses per period
            return [f"V{name}a {node}a 0 PULSE(0 1 0 1n 1n {D*T/2:.6e} {T:.6e})",
                    f"V{name}b {node}b 0 PULSE(0 1 {T - D*T/2 + td:.6e} 1n 1n {D*T/2 - td:.6e} {T:.6e})",
                    f"B{name} {node} 0 V=max(v({node}a),v({node}b))"]
        return [f"V{name} {node} 0 PULSE(0 1 {D*T/2 + td:.6e} 1n 1n {(1 - D)*T - td:.6e} {T:.6e})"]
    lines = ["* PV cell band-mode check (generated by sim/pv_control.py): ideal switches, body diodes, dead time",
             ".model sw SW(Ron=1m Roff=10Meg Vt=0.5 Vh=0)", ".model bd D(Is=1e-12 N=0.05)",
             f"VA sa 0 {va + rs*i0*da}", f"RSA sa sa2 {rs}", f"LSA sa2 pa {ls} ic={i0*da}", f"CA pa 0 {C} ic={va}",
             f"VB sb 0 {vb - rs*i0*db}", f"RSB sb sb2 {rs}", f"LSB sb2 pb {ls} ic={-i0*db}", f"CB pb 0 {C} ic={vb}",
             "S1 pa na g1 0 sw", "D1 na pa bd", "S2 na 0 g2 0 sw", "D2 0 na bd",
             "S3 pb nb g3 0 sw", "D3 nb pb bd", "S4 nb 0 g4 0 sw", "D4 0 nb bd",
             f"L1 na x {L} ic={i0}", f"RL x nb {P['R']}"]
    lines += gate("g1", "g1", da, True) + gate("g2", "g2", da, False) + gate("g3", "g3", db, True) + gate("g4", "g4", db, False)
    tmp = tempfile.mkdtemp()
    dat = os.path.join(tmp, "band.dat")
    lines += [f".tran 2n {5*T:.6e} 0 2n uic", ".control", "run", "linearize i(L1)", f"wrdata {dat} i(L1)", "quit", ".endc", ".end"]
    deck = os.path.join(OUT, "ngspice_band_check.cir")
    open(deck, "w").write("\n".join(lines) + "\n")
    subprocess.run(["ngspice", "-b", deck], capture_output=True, timeout=120, check=True)
    d = np.loadtxt(dat)
    ts, isp = d[:, 0], d[:, 1]
    sc = dict(N=1, pa=dict(port_bat(va + rs*i0*da, rs, ls)), pb=dict(port_bat(vb - rs*i0*db, rs, ls)), va0=va, vb0=vb,
              i0=[i0], t_en=0.0, start_on=True, fixed_duty=(da, db), cmd=lambda t, cs: ("I", i0), noise=False, hw=False,
              td=[(td, td, td, td)],
              iea0=i0*da, ieb0=-i0*db, CA=C, CB=C, uv_check=False)
    lg = simulate(dict(P, ltab=[L] * len(P["ltab"])), G, sc, 5 * T, switched=True, fine=(0.0, 5 * T), h_max=0.05e-6)
    fw = lg["fine"]
    im = np.interp(ts, fw["t"], fw["i"][:, 0])
    k = ts > T                                   # first period: ngspice starts with S1 off until its first pulse
    e = (isp[k] - isp[k][0]) - (im[k] - im[k][0])
    rip = lambda x, t: max(np.ptp(x[(t > j*T) & (t <= (j+1)*T)]) for j in range(1, 5))
    return dict(deck=os.path.relpath(deck, ROOT), rms=float(np.sqrt(np.mean(e**2))), drift_sp=float(isp[-1] - isp[k][0]),
                drift_py=float(im[-1] - im[k][0]), rip_sp=rip(isp, ts), rip_py=rip(im, ts), t=ts, isp=isp, ipy=im, da=da, db=db)


def plot_all(P, G, R):
    import logging
    import matplotlib
    matplotlib.use("Agg")
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    ag = R["agree"]
    fig, axs = plt.subplots(2, 3, figsize=(15, 7.5))
    for ax, st in zip(axs[0], ag["steps"]):
        n = np.arange(len(st["lin"])) * G["Ts"] * 1e3
        ax.plot(n, st["lin"], "k-", lw=1.5, label="small-signal (exact sampled)")
        ax.plot(n, st["sw"], "r--", lw=1, label="switched")
        ax.plot(n, st["avg"], "b:", lw=1.2, label="averaged")
        ax.set_title(f"current step 20->30 A per cell, {st['mode']} {st['va']:.0f}->{st['vb']:.0f} V", fontsize=9)
        ax.set_xlabel("ms after step")
        ax.set_ylabel("normalised sampled i_L")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)
    for ax, st in zip(axs[1][:2], ag["outer"]):
        n = np.arange(len(st["lin"])) * G["Tv"] * 1e3
        ax.plot(n, st["lin"], "k-", lw=1.5, label="small-signal (lifted)")
        ax.plot(n, st["sw"], "r--", lw=1, label="switched")
        ax.plot(n, st["avg"], "b:", lw=1.2, label="averaged")
        ax.set_title(st["label"], fontsize=9)
        ax.set_xlabel("ms after reference step")
        ax.set_ylabel("normalised port voltage")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)
    fw = R["wave"]["fine"]
    ax = axs[1][2]
    for k in range(3):
        ax.plot((fw["t"] - fw["t"][0]) * 1e6, fw["i"][:, k], lw=0.8, label=f"cell {k+1} i_L")
    ax.set_xlabel("us")
    ax.set_ylabel("A")
    ax2 = ax.twinx()
    ax2.plot((fw["t"] - fw["t"][0]) * 1e6, fw["va"], "k", lw=0.6, label="V_A")
    ax2.set_ylabel("V_A [V]")
    ax.set_title("switched model, 3 cells interleaved 120 deg, buck 1000->600 V, 45 A", fontsize=9)
    ax.legend(fontsize=7, loc="upper left")
    savefig(fig, "agreement.png")
    sp = R.get("spice")
    if sp:
        fig, ax = plt.subplots(figsize=(9, 3.6))
        ax.plot(sp["t"] * 1e6, sp["isp"], "k", lw=1.2, label="ngspice (ideal switches + body diodes)")
        ax.plot(sp["t"] * 1e6, sp["ipy"], "r--", lw=1, label="switched model (sim/pv_control.py)")
        ax.set_xlabel("us")
        ax.set_ylabel("i_L [A]")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        ax.set_title(f"band mode 800->790 V, D_A {sp['da']:.4f} / D_B {sp['db']:.3f} fixed, 200 ns dead time: drift = "
                     "dead-time voltage of both legs", fontsize=9)
        savefig(fig, "ngspice_check.png")
    # transitions: for each V_B / power direction the sweep with the largest error over the dead-time sets
    tr = []
    for vb in sorted({c["vb"] for c in R["trans"]}):
        for sg in (1.0, -1.0):
            cc = [c for c in R["trans"] if c["vb"] == vb and c["i_cmd"] * sg > 0]
            tr.append(max(cc, key=lambda c: c["err_max"]))
    fig, axs = plt.subplots(3, 2, figsize=(15, 10.5))
    for ax, c in zip(axs.flat, tr):
        lg = c["lg"]
        t = lg["t"] * 1e3
        ax.plot(t, lg["i"][:, 0], color="0.75", lw=0.4, label="cell 1 i_L (instantaneous)")
        for k in range(3):
            ax.plot(t, lg["im"][:, k], lw=0.8, label=f"cell {k+1} sampled avg")
        ax.plot(t, lg["iref"][:, 0], "k--", lw=0.8, label="reference")
        ax.set_ylabel("A")
        ax.set_xlabel("ms")
        ax2 = ax.twinx()
        ax2.plot(t, lg["va"], "m", lw=0.9, label="V_A")
        ax2.plot(t, lg["vb"], "c", lw=0.9, label="V_B")
        ax2.plot(t, lg["vb"] + 30 * (lg["mode"][:, 0] - 1), "g", lw=0.7, label="mode cell 1 (V_B -30 buck / 0 band / +30 boost)")
        ax2.set_ylabel("V")
        ax.set_title(f"[{c['set']}] V_B={c['vb']:.0f} V, V_A {c['v0']:.0f}->{c['v1']:.0f} V, {c['i_cmd']:+.0f} A/cell: max err "
                     f"{c['err_max']:.2f} A, mode changes {c['mode_changes']}", fontsize=8)
        if c is tr[0]:
            ax.legend(fontsize=6, loc="lower left")
            ax2.legend(fontsize=6, loc="lower right")
    savefig(fig, "transitions.png")
    # sharing
    sh = R["share"]
    fig, axs = plt.subplots(1, 2, figsize=(15, 4.8))
    lab, x = [], 0
    for r in sh:
        for k, a in enumerate(r["avg"]):
            axs[0].bar(x + k * 0.18, a, 0.17, color=f"C{k}")
        lab.append((x + 0.27, f"N={r['N']} {r['label'][:5]}\n{r['va']:.0f}->{r['vb']:.0f}"))
        x += 1
    axs[0].set_xticks([p for p, _ in lab])
    axs[0].set_xticklabels([s for _, s in lab], fontsize=7)
    axs[0].set_ylim(35, 50)
    axs[0].axhline(45, color="k", lw=0.5)
    axs[0].set_ylabel("true average inductor current per cell [A]")
    axs[0].set_title("current sharing with L +-10 %, sensor gain/offset errors (worst sign pattern)", fontsize=9)
    r = [x for x in sh if x["N"] == 4 and x["label"] == "calibrated"][0]
    fw = r["lg"]["fine"]
    for k in range(4):
        axs[1].plot(fw["t"] * 1e3, fw["i"][:, k], lw=0.5, label=f"cell {k+1}: L {r['ltol'][k]*100:+.0f} %, gain {r['sgain'][k]*100:+.2f} %, "
                                                          f"offset {r['soff'][k]:+.1f} A")
    axs[1].set_xlim(3.5, 6)
    axs[1].set_xlabel("ms")
    axs[1].set_ylabel("i_L [A]")
    axs[1].legend(fontsize=7)
    axs[1].set_title(f"4 cells, step {r['i_ref']/2:.1f}->{r['i_ref']:.1f} A at {r['va']:.0f}->{r['vb']:.0f} V", fontsize=9)
    savefig(fig, "sharing.png")
    # reversal
    rv = R["rev"]
    fig, axs = plt.subplots(1, 2, figsize=(15, 4.6))
    lg = rv["ramp"]["lg"]
    axs[0].plot(lg["t"] * 1e3, lg["i"][:, 0], color="0.7", lw=0.4, label="cell 1 instantaneous")
    axs[0].plot(lg["t"] * 1e3, lg["im"].mean(axis=1), "b", lw=1, label="sampled average (mean of cells)")
    axs[0].plot(lg["t"] * 1e3, lg["iref"][:, 0], "k--", lw=0.8, label="command")
    axs[0].set_title(f"current command reversal +40 -> -40 A/cell in 4 ms (800->600 V): max err {rv['ramp']['err_max']:.2f} A", fontsize=9)
    axs[0].legend(fontsize=7)
    axs[0].set_xlabel("ms")
    lg = rv["cpl"]["lg"]
    axs[1].plot(lg["t"] * 1e3, lg["im"].mean(axis=1), "b", lw=1, label="i_L sampled average")
    axs[1].set_ylabel("A")
    ax2 = axs[1].twinx()
    ax2.plot(lg["t"] * 1e3, lg["vb"], "r", lw=0.8, label="V_B")
    ax2.set_ylabel("V_B [V]")
    axs[1].set_title(f"bus forming on B (750 V), constant-power load +75 -> -75 kW in 2 ms from 3 ms: V_B {rv['cpl']['vb_min']:.0f}.."
                     f"{rv['cpl']['vb_max']:.0f} V", fontsize=9)
    axs[1].legend(fontsize=7, loc="upper right")
    ax2.legend(fontsize=7, loc="lower right")
    savefig(fig, "reversal.png")
    # start-up
    su = R["start"]
    fig, axs = plt.subplots(1, 3, figsize=(16, 4.6))
    ta, va_t, ia_t, tca, _ = su["pre_a"]
    tb, vb_t, ib_t, tcb, _ = su["pre_b"]
    axs[0].plot(ta * 1e3, va_t, label=f"port A bus from PV via {P['r_pre']:.0f} ohm (close at {tca*1e3:.0f} ms)")
    axs[0].plot(tb * 1e3, vb_t, label=f"port B bus from battery (close at {tcb*1e3:.0f} ms)")
    axs[0].axhline(su["voc"], color="k", lw=0.4)
    axs[0].set_xlabel("ms after the precharge relay closes")
    axs[0].set_ylabel("V")
    axs[0].legend(fontsize=7)
    axs[0].set_title(f"precharge (port_spec sequence); contactor inrush: battery {su['ib_inrush']:.0f} A, PV {su['ia_inrush']:.1f} A", fontsize=9)
    lg = su["lg"]
    axs[1].plot(lg["t"], lg["va"], "m", label="V_A (PV)")
    axs[1].axhline(su["vmpp"], color="m", ls=":", lw=0.8, label="V_mpp")
    axs[1].set_xlabel("s after both contactors closed")
    axs[1].set_ylabel("V")
    ax2 = axs[1].twinx()
    ax2.plot(lg["t"], lg["im"].mean(axis=1), "b", lw=0.8, label="i_L per cell")
    ax2.set_ylabel("A")
    axs[1].legend(fontsize=7, loc="upper right")
    axs[1].set_title("gate enable at 2 ms with feed-forward duties, MPPT from V_oc (averaged model)", fontsize=9)
    axs[2].plot(lg["t"], lg["va"] * lg["iea"] / 1e3, "g", lw=0.8, label="PV power")
    axs[2].axhline(su["pmpp"] / 1e3, color="k", ls=":", label="P_mpp")
    axs[2].set_xlabel("s")
    axs[2].set_ylabel("kW")
    axs[2].legend(fontsize=7)
    axs[2].set_title(f"300 W/m2, 25 C, 17s x 8p array: {su['eta_start']*100:.2f} % of P_mpp after 0.45 s", fontsize=9)
    savefig(fig, "startup.png")
    # bode plots of the worst corners
    fig, axs = plt.subplots(2, 2, figsize=(14, 8))
    for col, (title, items) in enumerate((("current loop (inner, 64 kHz)", R["bode_i"]), ("outer loops (32 kHz, lifted)", R["bode_v"]))):
        for f, L, lab in items:
            ph = np.degrees(np.unwrap(np.angle(L)))
            ph -= 360.0 * math.ceil(ph[0] / 360.0) if ph[0] > 0 else 0.0     # open-loop unstable (CPL): same branch
            axs[0, col].semilogx(f, 20 * np.log10(np.abs(L)), lw=1, label=lab)
            axs[1, col].semilogx(f, ph, lw=1, label=lab)
        axs[0, col].axhline(0, color="k", lw=0.5)
        axs[1, col].axhline(-180, color="k", lw=0.5)
        axs[0, col].set_title(title, fontsize=9)
        axs[0, col].set_ylabel("|L| [dB]")
        axs[1, col].set_ylabel("phase [deg]")
        axs[1, col].set_xlabel("Hz")
        axs[0, col].legend(fontsize=6)
        axs[0, col].grid(alpha=0.3, which="both")
        axs[1, col].grid(alpha=0.3, which="both")
        axs[0, col].set_ylim(-40, 60)
    savefig(fig, "bode.png")
    # margin maps
    rows = R["stab"][3]["rows"]
    fig, axs = plt.subplots(1, 4, figsize=(18, 4.2))
    vs = list(VCORNERS)
    for ax, (title, sel, key) in zip(axs, (("current loop: min PM [deg]", lambda r: True, "pm_i"),
                                            ("V_A loop, PV (left/MPP/right): min PM", lambda r: r["use"] == "mppt", "pm_v"),
                                            ("V_B loop vs CPL / CP source: min PM", lambda r: r["use"] == "cvb_cpl", "pm_v"),
                                            ("V_A loop vs CPL / CP source: min PM", lambda r: r["use"] == "cva_cpl", "pm_v"))):
        z = np.array([[min(r[key] for r in rows if sel(r) and r["va"] == va and r["vb"] == vb) for va in vs] for vb in vs])
        im = ax.imshow(z, origin="lower", cmap="RdYlGn", vmin=20, vmax=80)
        for a in range(4):
            for b in range(4):
                ax.text(a, b, f"{z[b, a]:.0f}", ha="center", va="center", fontsize=8)
        ax.set_xticks(range(4))
        ax.set_xticklabels([f"{v:.0f}" for v in vs])
        ax.set_yticks(range(4))
        ax.set_yticklabels([f"{v:.0f}" for v in vs])
        ax.set_xlabel("V_A [V]")
        ax.set_ylabel("V_B [V]")
        ax.set_title(title + " (N=3, both loads, both directions)", fontsize=8)
    fig.colorbar(im, ax=axs[-1])
    savefig(fig, "margins_map.png")
    # dead time near zero current
    fig, axs = plt.subplots(1, 2, figsize=(15, 4.2))
    for ax, lab in zip(axs, ("buck", "band")):
        for z in R["zc"]:
            if z["label"].startswith(lab) and z["set"] == "min/max per leg":
                lg = z["lg"]
                ax.plot(lg["t"] * 1e3, lg["im"][:, 1], lw=0.8, label=f"cell 2 (500 ns all edges), {z['comp']}-driven")
        ax.plot(lg["t"] * 1e3, lg["iref"][:, 1], "k--", lw=0.8, label="reference")
        ax.set_xlabel("ms")
        ax.set_ylabel("sampled i_L [A]")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)
        ax.set_title(f"current reversal, {lab}: dead-time compensation driven by the reference vs the measured current", fontsize=8)
    savefig(fig, "dead_time_zero_crossing.png")
    # protection
    pr = R["prot"]
    fig, axs = plt.subplots(2, 3, figsize=(17, 9))
    lg = pr["rej_ctrl"]["lg"]
    ax = axs[0, 0]
    ax.plot(lg["t"] * 1e3, lg["vb"], "r", lw=0.8, label="V_B")
    ax.plot(lg["t"] * 1e3, lg["va"], "m", lw=0.8, label="V_A")
    ax.axhline(P["v_ov_sw"], color="k", ls=":", lw=0.7, label="firmware OV 1050 V")
    ax.set_ylabel("V")
    ax2 = ax.twinx()
    ax2.plot(lg["t"] * 1e3, lg["im"].mean(axis=1), "b", lw=0.8, label="i_L")
    ax2.set_ylabel("A")
    ax.legend(fontsize=7, loc="center right")
    ax.set_title(f"load rejection at 82.5 kW, V_B=1000 V (bus opens at 2 ms), MPPT + V_B-max limit: peak "
                 f"{pr['rej_ctrl']['vb_peak']:.0f} V", fontsize=8)
    ax = axs[0, 1]
    rq = R["req"]
    for kind, col, lab in (("power", "C0", "(a) 82.5 kW"), ("current", "C3", "(b) frozen 45 A")):
        d = pr["ov"][kind]
        ax.plot([r["t_resp_us"] for r in d["curve"]], [r["v_off"] for r in d["curve"]], "-", color=col, lw=1,
                label=f"{lab}: ideal 1100 V detector + delay")
        for b in d["built"]:
            if b["t_resp_us"] is not None:
                mk = "o" if b["path"] == "PPB" and b["period_us"] < 20 else ("s" if b["path"] == "PPB" else "^")
                ax.plot(b["t_resp_us"], b["v_off"], mk, color=col, ms=5, mfc="none" if b["path"] != "PPB" else col)
    ax.axhline(rq["v_dev_lim"], color="r", ls="--", lw=0.8, label=f"device rule {rq['v_dev_lim']:.0f} V")
    ax.plot([], [], "ko", label="PPB, 10 us conversions (band edges)")
    ax.plot([], [], "ks", label="PPB, 31.25 us (control rate only)")
    ax.plot([], [], "k^", mfc="none", label="CMPSS backup alone")
    ax.set_xlabel("gates off after the true 1100 V crossing [us]")
    ax.set_ylabel("V at gates off [V]")
    ax.legend(fontsize=6)
    ax.set_title("over-voltage, control frozen: rev E trip paths", fontsize=8)
    ax = axs[0, 2]
    for r in pr["shorts"]:
        if r["variant"].startswith("bank short, local OC"):
            fw = r["fw"]
            ax.plot((fw["t"] - r["t_short"]) * 1e6, np.max(np.abs(fw["i"]), axis=1), lw=0.8, label=r["label"][:40])
    ax.axhline(P["trips"]["oc_local"][2], color="k", ls=":", lw=0.7, label="local window upper band")
    ax.axhline(P["i50"], color="r", ls="--", lw=0.7, label="L = 50 % L0")
    ax.set_xlim(-5, 90)
    ax.set_xlabel("us after the short")
    ax.set_ylabel("max |i_L| of the cells [A]")
    ax.legend(fontsize=6)
    ax.set_title("bank shorts (port shunt blind): local OC at its upper band, 3.41 us", fontsize=8)
    for ax, key, title in ((axs[1, 0], "cell_loss", "loss of cell 3 at 82.5 kW (3 ms)"), (axs[1, 1], "pv_loss", "PV array disconnected at 82.5 kW (3 ms)")):
        lg = pr[key]["lg"]
        for k in range(3):
            ax.plot(lg["t"] * 1e3, lg["im"][:, k], lw=0.8, label=f"cell {k+1} i_L avg")
        ax.set_ylabel("A")
        ax2 = ax.twinx()
        ax2.plot(lg["t"] * 1e3, lg["va"], "m", lw=0.8, label="V_A")
        ax2.plot(lg["t"] * 1e3, lg["vb"], "c", lw=0.8, label="V_B")
        ax2.set_ylabel("V")
        ax.set_xlabel("ms")
        ax.legend(fontsize=7, loc="lower left")
        ax2.legend(fontsize=7, loc="lower right")
        ax.set_title(title, fontsize=8)
    lg = pr["rej_vb"]["lg"]
    ax = axs[1, 2]
    ax.plot(lg["t"] * 1e3, lg["vb"], "r", lw=0.8)
    ax.set_ylabel("V_B [V]")
    ax2 = ax.twinx()
    ax2.plot(lg["t"] * 1e3, lg["im"].mean(axis=1), "b", lw=0.8)
    ax2.set_ylabel("i_L [A]")
    ax.set_xlabel("ms")
    ax.set_title(f"bus forming on B (950 V): 82.5 kW CPL drops to 0 at 2 ms: peak {pr['rej_vb']['vb_peak']:.0f} V", fontsize=8)
    savefig(fig, "protection.png")


def bode_sets(P, G, R):
    rows = R["stab"][3]["rows"]
    fi = np.logspace(1, math.log10(0.499 / G["Ts"]), 600)
    items_i, items_v = [], []
    worst = min(rows, key=lambda r: r["pm_i"])
    for r, lab in ((worst, "worst PM"), (max(rows, key=lambda r: r["pm_i"]), "best PM")):
        op = op_point(P, 3, r["va"], r["vb"], r["p_kW"] * 1e3, r["use"], g_rel=R["stab"][3]["g_rel"]["mpp"])
        res = lin_loops(P, G, op, kff=KFF_USE[r["use"]], fgrid=fi)
        items_i.append((res["f"], res["Li"], f"{lab}: {r['use']} {r['va']:.0f}->{r['vb']:.0f} V {r['load']} ({r['mode']})"))
    g = R["stab"][3]["g_rel"]
    for use, va, vb, pw, gr, lab in (("cvb_cpl", 250.0, 250.0, 33.75e3, 1.0, "CPL on B 250/250 V full (worst)"),
                                     ("cvb_cpl", 550.0, 950.0, 74.25e3, 1.0, "CPL on B 550->950 V full (RHP zero)"),
                                     ("mppt", 550.0, 800.0, 74.25e3, g["left"], "PV left of MPP 550 V"),
                                     ("mppt", 550.0, 800.0, 74.25e3, g["mpp"], "PV at MPP 550 V"),
                                     ("mppt", 550.0, 800.0, 74.25e3, g["right"], "PV right of MPP 550 V"),
                                     ("cvb_bat", 550.0, 800.0, 74.25e3, 1.0, "battery CV on B")):
        op = op_point(P, 3, va, vb, pw, use, g_rel=gr)
        res = lin_loops(P, G, op, kff=KFF_USE[use], fgrid=np.logspace(0, math.log10(0.499 / G["Tv"]), 600))
        items_v.append((res["fv"], res["Lv"], lab))
    return items_i, items_v


def derive_requirements(P, G, R):
    pr = R["prot"]
    rq = {}
    # device voltage rule at the last switching event: V_port (1 + overshoot/1100) <= 0.85 V_DSS (ringing scales with V)
    cs = json.load(open(CELL_SPEC)) if os.path.exists(CELL_SPEC) else None
    os_v = cs["worst_case_stresses"]["turnoff_overshoot_V"] if cs else 284.0
    vdss = cs["worst_case_stresses"]["device_VDS_rating_V"] if cs else 1700.0
    rq["v_dev_lim"] = 0.85 * vdss / (1 + os_v / P["v_ovp"])
    rq["os_v"] = os_v
    rq["v_cap_lim"] = 1.15 * (cs["capacitors"]["port_B"]["V_op_85C_V"] if cs else 1100.0)
    tr = P["trips"]
    chain_us, per_us = A("t_trip_chain_s") * 1e6, A("ppb_period_s") * 1e6
    for kind in ("power", "current"):
        d = pr["ov"][kind]
        cur = d["curve"]
        t_max = float(np.interp(rq["v_dev_lim"], [r["v_off"] for r in cur], [r["t_resp_us"] for r in cur]))
        slope, lag = cur[0]["slope"], cur[0]["t_resp_us"] - chain_us

        def pick(path, thr, per=None):
            return next(b for b in d["built"] if b["path"].startswith(path) and b["thr"] == thr and
                        (per is None or abs(b["period_us"] - per) < 1e-6))
        hi, nom = pick("PPB", tr["ov_ppb"][2], per_us), pick("PPB", tr["ov_ppb"][0], per_us)
        hi_ctl = pick("PPB", tr["ov_ppb"][2], P["T"] * 1e6)
        cmp_hi, cmp_nom = pick("CMPSS", tr["ov_cmpss"][2]), pick("CMPSS", tr["ov_cmpss"][0])
        rq["ov_" + kind] = dict(t_max_us=t_max, slope=slope, lag_us=lag, hi=hi, nom=nom, hi_ctl=hi_ctl, cmp_hi=cmp_hi,
                                cmp_nom=cmp_nom, per_max_us=per_us + (t_max - hi["t_resp_us"]),
                                lag_max_us=(rq["v_dev_lim"] - tr["ov_ppb"][2]) / slope - per_us - A("t_ppb_chain_s") * 1e6,
                                thr_max_V=tr["ov_ppb"][0] - (hi["v_off"] - rq["v_dev_lim"]),
                                meets=hi["v_off"] <= rq["v_dev_lim"], meets_ctl_rate=hi_ctl["v_off"] <= rq["v_dev_lim"],
                                cmp_meets=cmp_hi["v_off"] <= rq["v_dev_lim"])
    o = rq["ov_power"]
    rq.update(ov_t_max_us=o["t_max_us"], ov_t_designed_us=o["hi"]["t_resp_us"], ov_t_nominal_us=o["nom"]["t_resp_us"],
              ov_detect_lag_us=o["lag_us"], ov_slope=o["slope"], ov_v_off_designed=o["hi"]["v_off"],
              ov_v_peak_designed=o["hi"]["v_peak"])
    # inductor over-current, two layers: rise time from each layer's upper band edge to L = 50 % L0 without a trip
    sh = pr["shorts"]
    nohw = [r for r in sh if r["variant"] == "no trip"]
    rq["oc_t_max_local_us"] = min(r["t_local_to_sat_us"] for r in nohw if r["t_local_to_sat_us"])
    rq["oc_t_max_backup_us"] = min(r["t_backup_to_sat_us"] for r in nohw if r["t_backup_to_sat_us"])
    rq["oc_t_max_us"] = min(r["t_nominal_to_sat_us"] for r in nohw if r["t_nominal_to_sat_us"])
    rq["oc_didt_max"] = max(r["didt_local"] for r in nohw if r.get("didt_local"))
    loc = [r for r in sh if r["variant"].startswith("bank short, local OC")]
    bak = [r for r in sh if r["variant"].startswith("bank short, local failed")]
    amc = [r for r in sh if r["variant"].startswith("bank short, CTRL backup with the AMC3302")]
    term = [r for r in sh if r["variant"].startswith("terminal short, all")]
    noc = [r for r in sh if r["variant"].startswith("terminal short WITHOUT")]
    rq.update(oc_local_t_us=tr["t_local_us"][1], oc_local_peak=max(r["i_peak"] for r in loc),
              oc_backup_t_us=(P["tau_i"] + A("t_trip_chain_s")) * 1e6, oc_backup_peak=max(r["i_peak"] for r in bak),
              oc_amc_t_us=pr["t_amc"] * 1e6, oc_amc_peak=max(r["i_peak"] for r in amc),
              term_peak=max(r["i_peak"] for r in term), term_first=sorted({r["first"] for r in term}),
              term_t_off_us=max(r["t_off_us"] for r in term),
              clamp_dev_A=max(r["clamp_dev_A"] for r in term), clamp_I2t=max(r["clamp_I2t"] for r in term),
              clamp_module_A=max(r["clamp_module_A"] for r in term), body_dev_A=max(r["body_dev_A"] for r in term),
              v_reverse=max(r["v_reverse"] for r in term), body_dev_noclamp_A=max(r["body_dev_A"] for r in noc),
              v_reverse_noclamp=max(r["v_reverse"] for r in noc),
              i_dm_position=(cs["switch_positions"][0]["parallel"] * cs["bus_reverse_clamp"]["body_diode_share_with_clamp"]
                             ["I_DM_A"]) if cs else 400.0)
    rq["oc_t_designed_us"] = rq["oc_backup_t_us"]
    rq["oc_i_peak_designed"] = max(rq["oc_local_peak"], rq["oc_backup_peak"])
    # DESAT: NSI6651 detection (leading-edge blanking + filter) and the rev-5 short-circuit booster (cell_spec)
    pro = cs["protection"] if cs else {}
    scg = (cs["gate_drive"].get("short_circuit", {}) if cs else {})
    blank = [x * 1e-9 for x in pro.get("desat_blanking_range_ns", [P["desat_blank"] * 1e9] * 2)]
    t_off = scg.get("booster_detection_to_off_us_primary", pro.get("short_circuit_detect_to_off_us", 0.62)) * 1e-6
    wst = scg.get("withstand_assumed_us", pro.get("short_circuit_withstand_us_typ", 2.0))
    vth = pro.get("desat_threshold_range_V", [pro.get("desat_VDS_threshold_V", 7.0)] * 2)
    rds = {"SG2M040170HJ": (0.040, 0.088), "MSC035SMA170B4": (0.035, 0.065)}     # typ 25 / 175 C (datasheets p.4)
    rq["desat"] = dict(blank_us=blank[1] * 1e6, blank_range_us=[x * 1e6 for x in blank], off_after_detect_us=t_off * 1e6,
                       typ_us=(0.5 * (blank[0] + blank[1]) + t_off) * 1e6, max_us=(blank[1] + t_off) * 1e6,
                       scwt_1200_us=3.1, scwt_1100_us=wst, withstand_basis=scg.get("basis", "cell_spec"),
                       soft_off_only_us=scg.get("response_us", {}).get("Q1"),
                       required_detect_to_off_us=scg.get("required_detect_to_off_us_max"), vth=vth,
                       gdrv_check_us=pro.get("short_circuit_detect_to_off_us"),
                       desat_A_25C=[2 * vth[0] / max(r[0] for r in rds.values()), 2 * vth[1] / min(r[0] for r in rds.values())],
                       desat_A_175C=[2 * vth[0] / max(r[1] for r in rds.values()), 2 * vth[1] / min(r[1] for r in rds.values())])
    clamp = P["clamp"]
    rq["clamp_ratings"] = clamp.get("ratings", {})
    rq["clamp_spec_dyn_body_A"] = clamp.get("body_diode_share_with_clamp", {}).get("per_device_peak_A_max")
    rq["clamp_spec_dev_A"] = clamp.get("surge_worst", {}).get("per_device_peak_A")
    # dead time: residual of the midpoint estimate and the band-edge shift it causes
    lo, hi = P["td_range"]
    rq["dt_res_s"] = 0.5 * (hi - lo)
    rq["dt_res_V"] = {v: rq["dt_res_s"] * P["fsw"] * v for v in (550.0, 800.0, 1000.0)}
    return rq


def fmt(x, n=1):
    return "-" if x is None else (f"{x:.{n}f}" if np.isfinite(x) else "inf")


def write_csvs(P, G, R):
    for N in (3, 4):
        rows = R["stab"][N]["rows"]
        keys = ["N", "use", "label", "load", "va", "vb", "p_kW", "mode", "i_cell", "L_uH", "fc_i", "pm_i", "gm_i", "mm_i",
                "fc_v", "pm_v", "gm_v", "gm_lo_v", "mm_v", "rho"]
        with open(os.path.join(OUT, f"margins_N{N}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(keys)
            for r in rows:
                w.writerow([r[k] if isinstance(r[k], str) else ("" if r[k] is None else f"{r[k]:.4g}") for k in keys])
    with open(os.path.join(OUT, "transitions.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dead_time_set", "V_B", "V_A_from", "V_A_to", "i_cmd_A", "err_max_A", "err_rms_A", "mode_changes_per_cell", "i_peak_A"])
        for c in R["trans"]:
            w.writerow([c["set"], c["vb"], c["v0"], c["v1"], c["i_cmd"], f"{c['err_max']:.3f}", f"{c['err_rms']:.3f}",
                        "/".join(map(str, c["mode_changes"])), f"{c['i_peak']:.2f}"])
    with open(os.path.join(OUT, "sharing.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["N", "case", "V_A", "V_B", "i_ref_A", "cell_avg_A", "spread_A", "max_dev_A", "max_dev_pct_of_45A", "expected_A"])
        for r in R["share"]:
            w.writerow([r["N"], r["label"], r["va"], r["vb"], f"{r['i_ref']:.2f}", " ".join(f"{a:.2f}" for a in r["avg"]),
                        f"{r['spread']:.3f}", f"{r['dev_max']:.3f}", f"{r['dev_pct']:.2f}", " ".join(f"{a:.2f}" for a in r["expect"])])
    with open(os.path.join(OUT, "protection_ov.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case", "detector", "threshold_V", "conversion_period_us", "gate_off_after_true_1100V_crossing_us",
                    "V_at_gate_off", "V_peak"])
        for kind, d in R["prot"]["ov"].items():
            for r in d["curve"]:
                w.writerow([kind, f"ideal 1100 V + {r['extra_us']:.0f} us", 1100.0, "", fmt(r["t_resp_us"]), fmt(r["v_off"]),
                            fmt(r["v_peak"])])
            for r in d["built"]:
                w.writerow([kind, r["path"], r["thr"], fmt(r["period_us"] or None), fmt(r["t_resp_us"]), fmt(r["v_off"]),
                            fmt(r["v_peak"])])
    with open(os.path.join(OUT, "shorts.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case", "trips_in_force", "i_peak_A", "first_trip", "t_detect_us", "t_gates_off_us",
                    "local_band_to_50pct_L0_us", "backup_band_to_50pct_L0_us", "clamp_module_A", "clamp_per_device_A",
                    "clamp_I2t_A2s", "body_diode_per_device_A", "bank_reverse_V"])
        for r in R["prot"]["shorts"]:
            w.writerow([r["label"], r["variant"], f"{r['i_peak']:.1f}", r.get("first", ""), fmt(r.get("t_detect_us")),
                        fmt(r.get("t_off_us")), fmt(r.get("t_local_to_sat_us")), fmt(r.get("t_backup_to_sat_us")),
                        f"{r['clamp_module_A']:.0f}", f"{r['clamp_dev_A']:.0f}", f"{r['clamp_I2t']:.1f}", f"{r['body_dev_A']:.1f}",
                        f"{r['v_reverse']:.2f}"])
    with open(os.path.join(OUT, "dead_time.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["study", "dead_time_set", "case", "compensation", "max_error_A", "rms_A", "mode_changes_per_cell"])
        for c in R["trans"]:
            w.writerow(["transition", c["set"], f"V_B {c['vb']:.0f} V, V_A {c['v0']:.0f}->{c['v1']:.0f} V, {c['i_cmd']:+.0f} A",
                        "ref", f"{c['err_max']:.3f}", f"{c['err_rms']:.3f}", "/".join(map(str, c["mode_changes"]))])
        for z in R["zc"]:
            w.writerow(["zero crossing", z["set"], z["label"], z["comp"], f"{z['err_max']:.3f}", f"{z['err_rms']:.3f}", ""])
        for x in R["ident"]:
            w.writerow(["identified dead time", x["set"], f"V_B {x['vb']:.0f} V, V_A {x['v0']:.0f}->{x['v1']:.0f} V, "
                        f"{x['i_cmd']:+.0f} A", "ref, exact estimate", f"{x['err_id']:.3f}", "", "/".join(map(str, x["mc_id"]))])


def summary_stab(rows):
    ri = min(rows, key=lambda r: r["pm_i"])
    gi = min(rows, key=lambda r: r["gm_i"])
    ov = [r for r in rows if r["pm_v"] is not None and r["use"] != "cvb_bat"]
    rv = min(ov, key=lambda r: r["pm_v"])
    mv = min(ov, key=lambda r: r["mm_v"])
    gv = min(ov, key=lambda r: min(r["gm_v"], r["gm_lo_v"]))
    return dict(pm_i=ri, gm_i=gi, pm_v=rv, mm_v=mv, gm_v=gv, n_unstable=sum(1 for r in rows if r["rho"] >= 1.0), n=len(rows),
                fc_i=(min(r["fc_i"] for r in rows), max(r["fc_i"] for r in rows)),
                bat=[r for r in rows if r["use"] == "cvb_bat"])


def where(r):
    return f"{r['use']} ({r['label']}), V_A {r['va']:.0f} V / V_B {r['vb']:.0f} V, {r['load']} load, {r['mode']}"


def readback_states():
    """PV-PORT ID-line readback states and their ADC-count bands (CTRL-C2000 pin plan, P_ID trip_band)"""
    import re
    if not os.path.exists(PIN_PLAN):
        return {}
    t = {r["signal"]: r.get("trip_band", "") for r in csv.DictReader(open(PIN_PLAN))}.get("P_ID", "")
    return {m.group(1): [int(m.group(2)), int(m.group(3))] for m in re.finditer(r"PV-PORT (A\d B\d|unpowered) (\d+)-(\d+)", t)}


def supervisor_sections(P, R):
    """supervisor / plausibility content handed to firmware (port_spec rev 6, CTRL-C2000 rev E, SYS-IO-AUX rev E)"""
    tr = P["trips"]
    ps = json.load(open(PORT_SPEC))
    imd = ps.get("imd", {})
    inl = R["inlet"]
    win = {}
    for r in inl["rows"]:
        win.setdefault(f"{r['N']} cells, port {r['port']} ({r['kind']})", []).append(
            {"inlet_C": r["inlet_C"], "i_port_max_A": round(r["i_lim"], 1), "v_min_for_rated_V": round(r["v_min_rated"]),
             "v_min_for_max_power_V": round(r["v_min_max"])})
    plaus = {
        "cell_AN1_offset": "the LEM LA 150-P offset is re-zeroed by firmware at every idle (gates off, i_L = 0); a null "
                           "above 5 A is rejected and the cell is not enabled",
        "cell_AN1_open_conductor": {"both_lines_open_reads_A_max": tr["open_an1"], "one_line_open_reads_A_max": tr["open_an1_one"],
                                    "one_line_open_other_line_reads_A": -86.0,
                                    "caught_by": "both lines open: the CTRL CMPSS window (trip). One line open (-56 / -86 A): "
                                                 "inside the window - caught by the idle null check (> 5 A rejected) and by the "
                                                 "cell's own hardware OC trip, NOT by the comparator window",
                                    "source": "CTRL-C2000 rev E pin plan / check_trips (the -86 A figure: coordinator, rev E check)"},
        "port_V_I_open_input": {"VA_VB_reads_V_max": tr["open_v"], "IA_IB_one_conductor_reads_A_max": tr["open_i"]},
        "port_ID_readback": {"states_adc_counts": readback_states(),
                             "meaning": "nA / nB = RB1 + RB2 of port A / B summed on the port ID line: RB1 = hold channel-1 "
                                        "output OR the surge-varistor monitor loop open, RB2 = hold channel 2 (port_spec "
                                        "readback / proof_test); decode table = CTRL-C2000 pin plan P_ID",
                             "varistor_monitor_judged_above_port_V": 200.0,
                             "varistor_monitor_note": "the monitor loop needs port voltage (0.25 mA at 250 V); the PV port is "
                                                      "dark at night, so RB1 from the monitor is only judged above 200 V",
                             "proof_test": "at every start: pass 1 contactors open (readback only), pass 2 after precharge "
                                           "with the contactor closed at zero current (port_spec proof_test windows W1..W4)"},
        "insulation_monitor": {"open_earth_lead": "reads as no swing between the two test states -> report IMD PE open "
                                                  "as a FAULT (port_spec imd pe_open_rule)",
                               "settle_s_per_state_3tau": imd.get("settle_3tau_s"), "prediction_s_per_state": imd.get(
                                   "prediction_s_per_state"), "prediction_note": "3-sample exponential prediction: about 3 s "
                                                                                  "for both states instead of 42-56 s",
                               "array_capacitance_basis": "200 nF/kWp (port_spec imd C_pv_F)",
                               "one_monitor_per_DC_system": "only one insulation monitor may be active per connected DC "
                                                            "system: when another one is present the module disables its "
                                                            "own by leaving both test strings open"}}
    sup = {
        "port_current_limit_vs_inlet_C": {"table": P["i_port_vs_inlet"], "applies": f"rating {P['rating_key']} A ports of "
                                          "this module; PV-type port A, battery-type port B (port_spec rev 6)",
                                          "implementation": "per-cell current limit = min(45 A, 27.5 kW / v_A, "
                                                            "k_A I_portA,max(T) / (N D_A), k_B I_portB,max(T) / (N D_B)), "
                                                            "slewed; k_A, k_B = slow integral trims (5 ms) on the measured "
                                                            "port currents, <= 1",
                                          "full_power_window": win,
                                          "check_averaged_model": [{k: round(v, 2) if isinstance(v, float) else v for k, v in c.items()}
                                                                   for c in inl["checks"]]},
        "aux_hv_restart_D034": {
            "situation": "while the 24 V bus is fed from AUX-HV the hardware forces fans, gate-drive supply and coil supply "
                         "off and trips both safety latches; losing the cabinet feeds at full power can dip the bus to "
                         "13.7 V, the control card reboots and the hardware latch state survives",
            "firmware_start_up": ["read the hardware latch state (FLT_LATCH_N, GATE_EN, CHB_OK) and the 24 V source status "
                                  "bit (SYS-IO-AUX expander 2 GPB5, 0 = AUX-HV is the source) before anything else",
                                  "log the event with both values (reset cause, latch state, source)",
                                  "keep the PWM drivers tri-stated and the contactors open: do NOT pulse FLT_CLR and do "
                                  "NOT re-arm by itself",
                                  "re-arm only on an explicit operator / supervisor command after the cabinet feed is "
                                  "back (GPB5 = 1) and the latch inputs are healthy, then run the normal start sequence "
                                  "(proof test, precharge, contactors, soft start)"]}}
    return plaus, sup


def write_spec(P, G, R):
    rq, tr = R["req"], P["trips"]
    plaus, sup = supervisor_sections(P, R)
    st = {N: summary_stab(R["stab"][N]["rows"]) for N in (3, 4)}
    old = json.load(open(os.path.join(OUT, "control_spec.json"))) if os.path.exists(os.path.join(OUT, "control_spec.json")) else {}
    sh = R["share"]
    spec = {
        "title": "PV-P75 / PV-P100-110 control specification (calculated / simulated, NOT bench-validated)",
        "generated_by": "sim/pv_control.py", "inputs": P["src"],
        "loop_structure": {
            "inner": "per cell: average inductor-current PI (64 kHz, double update), port-voltage feed-forward inside the "
                     "modulator, dead-time feed-forward from the current REFERENCE with the midpoint dead-time estimate "
                     "(dead_time section), clamping anti-windup",
            "modulator": "buck / band / boost exactly as cell_spec switching.modes; controller output = average inductor "
                         "voltage, so the loop gain does not change between modes",
            "outer": "32 kHz, one per module: V_A loop (MPPT reference or DC bus on A) or V_B loop (DC bus forming on B), "
                     "port-B over-voltage limit loop, per-cell current limit 45 A and power limit 27.5 kW; candidates "
                     "min-selected with tracking anti-windup; port-current reference -> per-cell inductor-current "
                     "reference through the cells' ACTUAL duty (carries the mode hysteresis); each cell's 27.5 kW limit "
                     "uses its own duty and is slew-limited (ilim_slew_A_per_s)",
            "mppt": "dP-P&O at 20 Hz on oversampled V_A, I_A (sim/pv_mppt.py) -> V_A reference, ramp-limited",
            "sharing": "equal per-cell references (I_port* / (N_active D)), each cell closes its own current loop",
            "feed_forward": {"bus port (inverter = constant-power load)": A("k_ff_bus"), "PV array port": 0.0, "battery port": 0.0,
                             "basis": "with a stiff source (battery) the port-current feed-forward closes a unity loop "
                                      "(battery CV case PM -2.5 deg); on a PV port it cancels the array's own damping"}},
        "sampling": {"pwm_kHz": P["fsw"] / 1e3, "carrier": "centre-aligned up-down, double update (zero and period)",
                     "current_loop_kHz": 1e-3 / G["Ts"], "outer_loop_kHz": 1e-3 / G["Tv"], "mppt_Hz": 1 / pvm.MPPT["T_p"],
                     "i_L_sample": f"each cell at its own carrier zero and peak + {G['t_soc']*1e6:.2f} us (sensor-lag compensation)",
                     "port_V_I_sample": "at cell 1's carrier zero (outer loop)",
                     "computation_delay": "one sample (duty computed from the sample at t_k is loaded at t_k + Ts)",
                     "isr_budget_us": (G["Ts"] - G["t_soc"] - 0.6e-6) * 1e6,
                     "mode_change": "only at a carrier zero (result of a peak-boundary sample), keeps the minimum pulse",
                     "port_V_conversion_for_OV_PPB_us_max": round(rq["ov_power"]["per_max_us"], 1),
                     "port_V_conversion_note": f"the OV PPB evaluates every V_A / V_B conversion: with only the "
                                               f"{P['fsw']/1e3:.0f} kHz outer-loop samples the 136 us requirement is missed "
                                               "at the PPB's upper band edge; convert V_A, V_B every <= 10 us (rev E card "
                                               "assumption; V_A is oversampled at 500 kS/s for MPPT anyway)",
                     "interleave_deg": {"3 cells": 120, "4 cells": 90}},
        "current_loop": {"kp_V_per_A": G["kp"], "ki_V_per_As": G["ki"], "form": "q += ki Ts e; u = kp e + q (volts across L)",
                         "design_crossover_Hz": A("f_ci_Hz"), "PI_zero_Hz": A("f_ci_Hz") / A("ci_zero_ratio"),
                         "crossover_range_Hz_N3": st[3]["fc_i"], "min_PM_deg": st[3]["pm_i"]["pm_i"], "min_GM_dB": st[3]["gm_i"]["gm_i"],
                         "anti_windup": "integrator clamped to the realisable inductor voltage", "limit_A": P["imax"],
                         "L_used_for_gain_uH": P["Lnom"] * 1e6},
        "outer_loops": {"kpv_A_per_V": G["kpv"], "kiv_A_per_Vs": G["kiv"], "design_crossover_Hz": A("f_cv_Hz"),
                        "PI_zero_Hz": A("f_cv_Hz") / A("cv_zero_ratio"), "kpv_kiv_scale": "with the port capacitance N x 90 uF + 4.4 uF",
                        "kpv_kiv_N4": [R["stab"][4]["G"]["kpv"], R["stab"][4]["G"]["kiv"]],
                        "antiwindup_margin_A_per_cell": A("aw_margin_A"), "vb_limit_integrator_max_A": A("vlim_qmax_A"),
                        "min_PM_deg_N3": st[3]["pm_v"]["pm_v"], "min_modulus_margin_N3": st[3]["mm_v"]["mm_v"],
                        "min_PM_deg_N4": st[4]["pm_v"]["pm_v"], "min_modulus_margin_N4": st[4]["mm_v"]["mm_v"]},
        "limits": {"i_cell_A": P["imax"], "p_cell_kW": P["pmax"] / 1e3, "i_port_A": {"3 cells": 3 * P["imax"], "4 cells": 4 * P["imax"]},
                   "p_module_kW": {"3 cells": 3 * P["pmax"] / 1e3, "4 cells": 4 * P["pmax"] / 1e3},
                   "v_port_limit_loop_V": 1010.0, "v_ov_firmware_V": P["v_ov_sw"], "v_uv_stop_V": P["v_uv"],
                   "i_port_vs_inlet_C": "supervisor/port_current_limit_vs_inlet_C",
                   "v_a_min_mppt_V": pvm.MPPT["v_lo"], "reverse_into_pv": "blocked (reference >= 0 in MPPT mode)"},
        "modulator": {"D_max": P["Dmax"], "D_min": P["Dmin"], "dead_time_ns": list(A("dt_range_ns")), "min_pulse_us": P["tmin"] * 1e6,
                      "enter_band": "single-leg duty (with that mode's dead-time term) > D_max",
                      "leave_band": f"single-leg duty < D_max - {G['hyst']}", "hysteresis_duty": G["hyst"],
                      "band_rule": "lower-voltage-side leg at D_max, the other leg carries the control; when the demand "
                                   "exceeds that leg, the roles swap continuously (v_B* = min(D_max V_B, D_max V_A - u))"},
        "dead_time": {
            "firmware_dead_band_ns": P["td"] * 1e9, "at_gates_ns_cell_spec": P["td_gates"], "design_range_ns": list(A("dt_range_ns")),
            "known_to_firmware": False, "estimate_ns_per_edge": A("dt_hat_ns"),
            "residual_ns_per_edge_max": rq["dt_res_s"] * 1e9,
            "residual_V_per_switching_leg": {f"{v:.0f} V": round(x, 2) for v, x in rq["dt_res_V"].items()},
            "compensation": "per switching leg, the voltage lost in the edge whose current flows against the commanded "
                            "transition (A->B: leg A rising, leg B falling; B->A: leg A falling, leg B rising) = "
                            "t_hat x f_sw x V_leg, applied as feed-forward in the modulator; the edge-current sign comes "
                            "from a model of the closed current loop (the current REFERENCE slew-limited to "
                            f"{A('dt_model_slew_A_per_us')} A/us) plus the predicted ripple, with a {A('dt_ff_ramp_A'):.0f} A "
                            "ramp (feed-forward adds no loop gain, so it can be sharp, and it flips with the current on a "
                            "reversing step); the PI integrator takes the residual",
            "mode_logic": f"each candidate mode judged with its own dead-time terms; enter band at once when the single-leg "
                          f"duty exceeds D_max; leave band only below D_max - {G['hyst']} (> 2.5 x the per-leg residual "
                          f"{rq['dt_res_s']*P['fsw']:.4f} in duty) for both the demand u and the PI integrator filtered with "
                          f"{A('mode_exit_tau_s')*1e3:.0f} ms, so neither an unknown dead time nor a large step makes a cell "
                          "leave and re-enter the band",
            "firmware_must": ["use the midpoint of the hardware dead-time range as the estimate for every edge (no per-unit "
                              "value is needed)", "drive the dead-time compensation from the slew-limited current "
                              "reference (closed-loop model), not from the measured current",
                              f"keep the band-exit hysteresis >= {G['hyst']} in duty and test the exit also on the PI "
                              f"integrator filtered with {A('mode_exit_tau_s')*1e3:.0f} ms",
                              "derive each cell's power limit from its own duty and slew it (no ratio map)",
                              "convert port current references with the cells' actual duty",
                              "optional: identify each edge's dead time (integrator jump at the first band entry / exit) - "
                              f"it cuts the worst transition error from {max(c['err_max'] for c in R['trans']):.2f} A to "
                              f"{max(x['err_id'] for x in R['ident']):.2f} A; not needed for stability or limits"],
            "transition_worst_error_A_over_range": max(c["err_max"] for c in R["trans"]),
            "zero_crossing_worst_error_A_over_range": max(z["err_max"] for z in R["zc"] if z["comp"] == "ref")},
        "measurement_requirements": R["meas"],
        "hardware_trips": {
            "architecture": "as built: CTRL-C2000 rev E (gen/data/ctrl_c2000_pin_plan.csv trip_band) and the PVCELL-25 "
                            "local OC window (hardware/PVCELL-25/outputs/PVCELL-25_design_check.txt); CMPSS references from the "
                            "2.5 V reference, offsets nulled by firmware at start-up; parsed: " + ", ".join(tr["src"]),
            "inductor_overcurrent": {
                "threshold_A": [-tr["oc_local"][0], tr["oc_local"][0]],
                "type": f"two layers: PVCELL-25 local window +/-{tr['oc_local'][0]:.1f} A ({tr['oc_local'][1]:.1f}-"
                        f"{tr['oc_local'][2]:.1f} A) -> PWM gated + EN, {tr['t_local_us'][0]:.2f}-{tr['t_local_us'][1]:.2f} us to gates "
                        f"off; CTRL-C2000 CMPSS window on AN1 +/-{tr['oc_backup'][0]:.0f} A ({tr['oc_backup'][1]:.1f}-"
                        f"{tr['oc_backup'][2]:.1f} A) as backup",
                "max_response_us_trip_level_to_gates_off": rq["oc_t_max_local_us"],
                "basis": "time for the fastest bank-short current to rise from the layer's upper band edge to "
                         f"L = 50 % L0 ({P['i50']:.0f} A); device pulse rating {rq['i_dm_position']:.0f} A per position",
                "as_designed_us": rq["oc_backup_t_us"], "as_designed_peak_A": rq["oc_i_peak_designed"],
                "meets": rq["oc_local_peak"] < P["i50"] and rq["oc_backup_peak"] < P["i50"],
                "local": {"band_A": list(tr["oc_local"][1:]), "response_us": list(tr["t_local_us"]),
                          "max_response_us": rq["oc_t_max_local_us"], "peak_A": rq["oc_local_peak"],
                          "meets": rq["oc_local_peak"] < P["i50"]},
                "ctrl_backup": {"band_A": list(tr["oc_backup"][1:]), "max_response_us": rq["oc_t_max_backup_us"],
                                "response_us_with_required_sensor": rq["oc_backup_t_us"], "peak_A": rq["oc_backup_peak"],
                                "meets": rq["oc_backup_peak"] < P["i50"], "response_us_amc3302_as_drawn": rq["oc_amc_t_us"],
                                "peak_A_amc3302_as_drawn": rq["oc_amc_peak"],
                                "meets_amc3302_as_drawn": rq["oc_amc_peak"] < P["i50"],
                                "note": "as_designed_us (read by gen/ctrl_c2000.py for this layer) assumes the cell sensor "
                                        "meets measurement_requirements/cell_inductor_current/group_delay_us_max"},
                "didt_max_A_per_us": rq["oc_didt_max"],
                "sensor_as_built": f"LEM LA 150-P (PVCELL-25 rev B): 20.0 mV/A, 0.0759 A/count, delay "
                                   f"{tr['sensor_delay_us']:.2f} us, -3 dB {sensor_bw_hz()/1e3:.0f} kHz (inferred)",
                "an1_open_conductor": "plausibility/cell_AN1_open_conductor"},
            "port_overvoltage": {
                "threshold_V": tr["ov_ppb"][0],
                "type": f"primary: ADC PPB limit on every V_A / V_B conversion, band {tr['ov_ppb'][1]:.1f}-{tr['ov_ppb'][2]:.1f} V; "
                        f"backup: CMPSS high {tr['ov_cmpss'][0]:.0f} V ({tr['ov_cmpss'][1]:.1f}-{tr['ov_cmpss'][2]:.1f} V); both "
                        "after the port-board divider RC and the AMC3330",
                "max_response_us_true_crossing_to_gates_off": rq["ov_t_max_us"],
                "basis": f"port voltage at the last switching event <= {rq['v_dev_lim']:.0f} V "
                         f"(0.85 V_DSS incl. {rq['os_v']:.0f} V ringing scaled with V); C4AQ 1.15 x 1100 V = "
                         f"{rq['v_cap_lim']:.0f} V not binding; slope {rq['ov_slope']:.2f} V/us at 82.5 kW",
                "as_designed_us": rq["ov_t_designed_us"], "as_designed_detection_lag_us": rq["ov_detect_lag_us"],
                "as_designed_us_nominal_threshold": rq["ov_t_nominal_us"], "as_designed_V_at_gates_off": rq["ov_v_off_designed"],
                "meets": rq["ov_power"]["meets"],
                "ppb_conversion_period_us": A("ppb_period_s") * 1e6, "ppb_conversion_period_us_max": rq["ov_power"]["per_max_us"],
                "ppb_at_outer_loop_rate_only": {"period_us": P["T"] * 1e6, "response_us": rq["ov_power"]["hi_ctl"]["t_resp_us"],
                                                "V_at_gates_off": rq["ov_power"]["hi_ctl"]["v_off"],
                                                "meets": rq["ov_power"]["meets_ctl_rate"]},
                "cmpss_backup_alone": {"V_at_gates_off_upper_band": rq["ov_power"]["cmp_hi"]["v_off"],
                                       "V_at_gates_off_nominal": rq["ov_power"]["cmp_nom"]["v_off"],
                                       "meets_0p85_rule_upper_band": rq["ov_power"]["cmp_meets"]},
                "full_current_frozen_case": {
                    "case": "outer control frozen, cells held at 45 A, stiff port A at 1000 V, port B (611 V) opens",
                    "slope_V_per_us": rq["ov_current"]["slope"], "max_response_us": rq["ov_current"]["t_max_us"],
                    "ppb_upper_band_response_us": rq["ov_current"]["hi"]["t_resp_us"],
                    "ppb_upper_band_V_at_gates_off": rq["ov_current"]["hi"]["v_off"], "meets": rq["ov_current"]["meets"],
                    "fix_options": {"ppb_nominal_threshold_V_max": round(rq["ov_current"]["thr_max_V"], 1),
                                    "or_detection_lag_us_max": round(rq["ov_current"]["lag_max_us"], 1)}}},
            "firmware_overvoltage_V": P["v_ov_sw"], "firmware_undervoltage_V": P["v_uv"],
            "desat": {**rq["desat"], "meets_typ": rq["desat"]["typ_us"] < rq["desat"]["scwt_1100_us"],
                      "meets_max": rq["desat"]["max_us"] < rq["desat"]["scwt_1100_us"]},
            "port_overcurrent_A": {"OC": tr["port_oc"][0], "SC": tr["port_sc"][0], "OC_band_A": list(tr["port_oc"][1:]),
                                   "SC_band_A": list(tr["port_sc"][1:]),
                                   "source": "CTRL-C2000 rev E: OC on the ADC PPB path, SC on CMPSS, IA / IB (all cells)",
                                   "terminal_short": {"first_layer": rq["term_first"], "gates_off_us_after_short": rq["term_t_off_us"],
                                                      "i_L_peak_A": rq["term_peak"]}},
            "bus_reverse_clamp": {"part": P["clamp"].get("part"), "devices_per_bank": P["clamp"].get("devices_per_bank"),
                                  "terminal_short_module_A": rq["clamp_module_A"], "per_device_A": rq["clamp_dev_A"],
                                  "per_device_I2t_A2s": rq["clamp_I2t"], "bank_reverse_V": rq["v_reverse"],
                                  "body_diode_per_device_A_static": rq["body_dev_A"],
                                  "body_diode_per_device_A_dynamic_cell_spec": rq["clamp_spec_dyn_body_A"],
                                  "without_clamp_body_diode_per_device_A": rq["body_dev_noclamp_A"],
                                  "ratings": rq["clamp_ratings"]},
            "open_input_detection": {"VA_VB_reads_V_max": tr["open_v"], "IA_IB_one_conductor_reads_A_max": tr["open_i"],
                                     "AN1_reads_A_max": tr["open_an1"],
                                     "firmware": "an open or unpowered sensor reads out of range and trips (CMPSS low / PPB "
                                                 "low); treat as a sensor fault, never as a measurement"},
            "pwm_enable": "CTRL-C2000 rev E: the PWM line drivers are tri-stated unless CHB_OK, the MCU enable and that cell's "
                          "RDY are all high (an un-ready cell never sees a gate command)"},
        "stability": {f"N{N}": {"cases": st[N]["n"], "unstable": st[N]["n_unstable"],
                                "min_PM_current_loop": [st[N]["pm_i"]["pm_i"], where(st[N]["pm_i"])],
                                "min_GM_current_loop_dB": [st[N]["gm_i"]["gm_i"], where(st[N]["gm_i"])],
                                "min_PM_outer": [st[N]["pm_v"]["pm_v"], where(st[N]["pm_v"])],
                                "min_modulus_margin_outer": [st[N]["mm_v"]["mm_v"], where(st[N]["mm_v"])],
                                "min_gain_margin_outer_dB": [min(st[N]["gm_v"]["gm_v"], st[N]["gm_v"]["gm_lo_v"]), where(st[N]["gm_v"])],
                                "cpl_without_feedforward_unstable_corners": sum(1 for x in R["stab"][N]["noff"] if not x["stable"])}
                      for N in (3, 4)},
        "transitions": {"max_sampled_current_error_A_full_current": max(c["err_max"] for c in R["trans"] if c["vb"] == 550.0),
                        "max_sampled_current_error_A_power_limited": max(c["err_max"] for c in R["trans"] if c["vb"] != 550.0),
                        "mode_changes_per_cell_per_sweep": sorted({m for c in R["trans"] for m in c["mode_changes"]}),
                        "i_peak_A": max(c["i_peak"] for c in R["trans"]),
                        "dead_time_sets_ns": dt_sets_ns(), "sweeps": len(R["trans"]),
                        "band_entry_exit_ratio_VB_over_VA": R["band_edges"]},
        "sharing": {"max_dev_pct_calibrated": max(r["dev_pct"] for r in sh if r["label"] == "calibrated"),
                    "max_dev_pct_uncalibrated": max(r["dev_pct"] for r in sh if r["label"] == "uncalibrated")},
        "mppt": old.get("mppt", "run sim/pv_mppt.py"),
        "plausibility": plaus,
        "supervisor": sup,
        "honesty": "Simulation only. Power stage ideal-switch, sensors first-order, PV/battery/CPL models are assumptions "
                   "listed in report.md; nothing is bench-validated.",
    }
    json.dump(spec, open(os.path.join(OUT, "control_spec.json"), "w"), indent=1, default=float)
    return spec


def meas_requirements(P, G, R):
    rq = R["req"]
    sh_cal = max(r["dev_pct"] for r in R["share"] if r["label"] == "calibrated")
    afe = 1 / (2 * math.pi * A("f_afe_cell_Hz")) * 1e6
    d_max = rq["oc_t_max_backup_us"] - A("t_trip_chain_s") * 1e6 - afe        # sensor delay the backup layer allows
    oc = rq["ov_current"]
    return {
        "cell_inductor_current": {
            "range_A": [-A("i_sense_fs_A"), A("i_sense_fs_A")], "bandwidth_kHz_min": A("f_isense_Hz") / 1e3,
            "group_delay_us_max": round(d_max, 2), "resolution_A_per_LSB": P["lsb_il"],
            "accuracy_after_cal": {"gain_pct": 100 * A("sens_gain_err"), "offset_A": A("sens_off_err_A")},
            "comparator_threshold_band_A": list(P["trips"]["oc_backup"][1:]),
            "basis": f"CTRL backup CMPSS window (upper band {P['trips']['oc_backup'][2]:.1f} A) must have the gates off within "
                     f"{rq['oc_t_max_backup_us']:.2f} us (fastest bank-short rise to L = 50 % L0) = sensor delay <= {d_max:.2f} us "
                     f"+ AFE {afe:.2f} us + chain {A('t_trip_chain_s')*1e6:.2f} us; as built (LEM LA 150-P, "
                     f"{P['trips']['sensor_delay_us']:.2f} us) the backup peaks at {rq['oc_backup_peak']:.0f} A (the replaced "
                     f"AMC3302, 2.1 us, would give {rq['oc_amc_peak']:.0f} A). Current-loop phase (sensor lag compensated by a "
                     f"{G['t_soc']*1e6:.2f} us SOC offset); sharing within {sh_cal:.1f} % of 45 A"},
        "port_voltage_VA_VB": {
            "range_V": [0, P["div"]["V_full_scale"]], "adc_full_scale_V": P["v_fs_adc"], "resolution_V_per_LSB": P["lsb_v"],
            "filter_pole_kHz_as_designed": 1e-3 / (2 * math.pi * P["tau_rc"]), "noise_V_rms_per_sample": P["sig_v"],
            "bandwidth_kHz_min": 3.0, "ov_detection_lag_us_max": round(rq["ov_power"]["lag_max_us"], 1),
            "ov_detection_lag_us_max_full_current_case": round(oc["lag_max_us"], 1),
            "conversion_period_us_max_for_ppb": round(rq["ov_power"]["per_max_us"], 1),
            "accuracy": "port_design: 0.42 % RSS / 0.73 % worst after calibration (sets the OV threshold band and the CV limit)",
            "basis": "outer-loop PM changes by < 3.5 deg between a 3.2 kHz and a 20 kHz pole; the PPB at its upper band "
                     f"edge {P['trips']['ov_ppb'][2]:.1f} V with V_A/V_B converted every <= {A('ppb_period_s')*1e6:.0f} us needs a "
                     f"lag <= {rq['ov_power']['lag_max_us']:.0f} us at 82.5 kW (have {rq['ov_detect_lag_us']:.0f} us); the "
                     f"frozen-current case would need <= {oc['lag_max_us']:.0f} us"},
        "port_current_IA_IB": {
            "range_A": [-500, 500], "resolution_A_per_LSB": P["lsb_ip"], "noise_A_rms_per_sample": P["sig_ip"],
            "delay_us": P["tau_ip"] * 1e6, "bandwidth_kHz_min": 10.0,
            "basis": "load-current feed-forward on a bus port (CPL stability at 250 V needs it: without it the 250/250 V "
                     "corner is unstable); MPPT power measurement (oversampling requirement in sim/pv_mppt.py); port SC "
                     f"trip (rev E CMPSS) reaches gates-off {rq['term_t_off_us']:.1f} us after a terminal short"}}


def write_report(P, G, R, spec):
    rq, st = R["req"], {N: summary_stab(R["stab"][N]["rows"]) for N in (3, 4)}
    ag, pr, su = R["agree"], R["prot"], R["start"]
    L = []
    a = L.append
    a("# PV-P75 / PV-P100-110 module control: plant, loops, stability, protection\n")
    a("Generated by `sim/pv_control.py`; every number below is written by the script. **Simulated / calculated, not "
      "bench-validated.** Hand-off: `control_spec.json`. MPPT: `sim/out/pv_mppt/report.md`.\n")
    a("## 1. Inputs and assumptions\n")
    a(f"- Power stage ({'; '.join(P['src'])}): f_sw {P['fsw']/1e3:.0f} kHz, firmware dead band {P['td']*1e9:.0f} ns stretched "
      f"by the gate-drive interlock to {P['td_gates'][0]:.0f}-{P['td_gates'][1]:.0f} ns at the gates (designed for an unknown "
      f"{A('dt_range_ns')[0]:.0f}-{A('dt_range_ns')[1]:.0f} ns per edge), D_max {P['Dmax']:.3f}, "
      f"min pulse {P['tmin']*1e6:.2f} us, L(I) {P['L0']*1e6:.0f} uH at 0 A / {P['Lnom']*1e6:.0f} uH at 45 A / "
      f"{float(np.interp(P['i_trip'], P['Li'], P['Lv']))*1e6:.0f} uH at the {P['i_trip']:.1f} A trip / 50 % of L0 at {P['i50']:.0f} A, "
      f"series R {P['R']*1e3:.1f} mOhm, {P['C_cell']*1e6:.0f} uF per port per cell (+{P['C_x']*1e6:.1f} uF port X capacitors), "
      f"limits {P['imax']:.0f} A / {P['pmax']/1e3:.1f} kW per cell, OVP {P['v_ovp']:.0f} V (hardware) / {P['v_ov_sw']:.0f} V "
      f"(firmware), UV stop {P['v_uv']:.0f} V.")
    a(f"- Port sensing (port_spec): divider lag {P['tau_rc']*1e6:.1f} us, isolated amplifiers {P['tau_amc']*1e6:.1f} us (voltage, "
      f"incl. AFE) / {P['tau_ip']*1e6:.1f} us (current); cell AN1 = LEM LA 150-P ({P['trips']['sensor_delay_us']:.2f} us, "
      f"-3 dB {sensor_bw_hz()/1e3:.0f} kHz, model lag {P['tau_i']*1e6:.2f} us incl. AFE); "
      f"CTRL-C2000 AFE ~490 kHz, 12-bit ADC: {P['lsb_v']:.3f} V/LSB, {P['lsb_ip']:.3f} A/LSB (port current), "
      f"{P['lsb_il']:.4f} A/LSB (cell current, +/-{A('i_sense_fs_A'):.0f} A full scale).")
    tr = P["trips"]
    a(f"- Hardware trips as built (CTRL-C2000 rev E pin plan, PVCELL-25 design check; parsed: {', '.join(tr['src'])}): port OV "
      f"PPB {tr['ov_ppb'][0]:.0f} V ({tr['ov_ppb'][1]:.1f}-{tr['ov_ppb'][2]:.1f} V) + CMPSS backup {tr['ov_cmpss'][0]:.0f} V "
      f"({tr['ov_cmpss'][1]:.1f}-{tr['ov_cmpss'][2]:.1f} V); cell OC local {tr['oc_local'][0]:.1f} A ({tr['oc_local'][1]:.1f}-"
      f"{tr['oc_local'][2]:.1f} A, {tr['t_local_us'][0]:.2f}-{tr['t_local_us'][1]:.2f} us) + CTRL CMPSS backup {tr['oc_backup'][0]:.0f} A "
      f"({tr['oc_backup'][1]:.1f}-{tr['oc_backup'][2]:.1f} A); port OC {tr['port_oc'][0]:.1f} A PPB ({tr['port_oc'][1]:.1f}-"
      f"{tr['port_oc'][2]:.1f} A), port SC {tr['port_sc'][0]:.0f} A CMPSS ({tr['port_sc'][1]:.1f}-{tr['port_sc'][2]:.1f} A).")
    a("- Our assumptions:\n")
    a("| key | value | meaning |")
    a("|---|---|---|")
    for k, (v, d) in ASSUME.items():
        a(f"| {k} | {v:g} | {d} |" if isinstance(v, (int, float)) else f"| {k} | {v} | {d} |")
    a(f"\nPlant dead times when a study does not vary them (unknown to the firmware, which uses {A('dt_hat_ns'):.0f} ns): cell k "
      "uses (leg A rise, A fall, B rise, B fall) = " + "; ".join(f"{tuple(round(y * 1e9) for y in dt_of(x))}" for x in DT_PLANT_FR[:3]) + " ns.")
    a("\n## 2. Loop structure and rates\n")
    a("```")
    a("MPPT (dP-P&O, 20 Hz, sim/pv_mppt.py) --V_A*--> [ramp] --+")
    a("                                                         v")
    a("  V_A loop  PI (+ I_A feed-forward on a bus port) --+   min-select   per-cell limits      I_port* / (N_act D_actual)")
    a("  V_B-max limit PI (+ I_B feed-forward, bus)  ------+--> (tracking --> 45 A, 27.5 kW  --> i_L* (same for all cells)")
    a("  or V_B loop (bus forming)                         |    anti-windup)    [32 kHz]                    |")
    a("                                                                                                  v")
    a("  per cell (64 kHz, own carrier, 120/90 deg):  i_L(sampled at carrier zero/peak + t_soc) -> PI -> u = v_L*")
    a("     -> + dead-time feed-forward (midpoint estimate, sign from i_L*) -> modulator (buck/band/boost, V_A, V_B")
    a("        feed-forward, hysteresis) -> D_A, D_B -> shadow load at the next zero/peak -> ePWM (200 ns dead band)")
    a(f"     -> RS-422 -> gate driver (dead time {P['td_gates'][0]:.0f}-{P['td_gates'][1]:.0f} ns at the gates) -> 2 x "
      f"{P['device']} per switch")
    a("  hardware (rev E): cell OC local window -> PWM gated; CMPSS window on AN1 (backup); V_A/V_B PPB (primary) +")
    a("        CMPSS (backup); I_A/I_B PPB (OC) + CMPSS (SC) -> trip zone; DESAT -> FLT -> trip zone")
    a("```\n")
    a(f"- Current loop: kp {G['kp']:.3f} V/A, ki {G['ki']:.0f} V/(A s) (design {A('f_ci_Hz'):.0f} Hz at L(45 A), zero at "
      f"{A('f_ci_Hz')/A('ci_zero_ratio'):.0f} Hz); achieved crossover {st[3]['fc_i'][0]:.0f}-{st[3]['fc_i'][1]:.0f} Hz over all corners.")
    a(f"- Outer loops: kp {G['kpv']:.3f} A/V, ki {G['kiv']:.1f} A/(V s) for 3 cells ({R['stab'][4]['G']['kpv']:.3f} / "
      f"{R['stab'][4]['G']['kiv']:.1f} for 4 cells; scaled with the port capacitance), design {A('f_cv_Hz'):.0f} Hz, zero at "
      f"{A('f_cv_Hz')/A('cv_zero_ratio'):.0f} Hz. Load-current feed-forward 1 on a DC-bus port, 0 on a PV or battery port (section 5).")
    a(f"- Timing: sample at the carrier extremum + {G['t_soc']*1e6:.2f} us (compensates the i_L sensor + AFE lag); duty loaded at "
      f"the next extremum (one-sample delay, {G['Ts']*1e6:.2f} us); ISR budget {(G['Ts']-G['t_soc']-0.6e-6)*1e6:.1f} us; "
      "mode changes only take effect at a carrier zero.")
    a("\n## 3. Plant models and their agreement\n")
    a("Switched model: gate-level PWM per cell (dead time on each rising edge, body diode chosen by the current sign), "
      "event-exact integration between edges; averaged model: the same equations with period-average switching functions "
      "and the dead-time voltage derived from the ripple edge currents; small-signal: the averaged model linearised, "
      "discretised exactly (ZOH, sampling offset, one-sample delay, sensor poles), outer loop lifted over two inner samples.\n")
    a("| test | overshoot small-signal / switched / averaged | RMS deviation from small-signal (switched / averaged) |")
    a("|---|---|---|")
    for s in ag["steps"]:
        a(f"| current step 20->30 A, {s['mode']} {s['va']:.0f}->{s['vb']:.0f} V | {s['os_lin']*100:.1f} / {s['os_sw']*100:.1f} / "
          f"{s['os_avg']*100:.1f} % | {s['rms_sw']*100:.1f} / {s['rms_avg']*100:.1f} % of the step |")
    for s in ag["n_cells"]:
        a(f"| current step 20->30 A, buck 900->600 V, {'single cell' if s['N'] == 1 else '4 cells at 90 deg'} | "
          f"{s['os_lin']*100:.1f} / {s['os_sw']*100:.1f} / {s['os_avg']*100:.1f} % | {s['rms_sw']*100:.1f} / {s['rms_avg']*100:.1f} % of the step |")
    for s in ag["outer"]:
        a(f"| {s['label']} | {s['os_lin']*100:.1f} / {s['os_sw']*100:.1f} / {s['os_avg']*100:.1f} % | "
          f"{s['rms_sw']*100:.1f} / {s['rms_avg']*100:.1f} % |")
    a("\nSteady state of the switched model against the analytic mode map (duties include the R drop and dead time):\n")
    a("| mode | V_A -> V_B | i [A] | D_A sim / map | D_B sim / map | residual avg v_L [V] | ripple sim / analytic [A] |")
    a("|---|---|---|---|---|---|---|")
    for d in ag["duty"]:
        a(f"| {d['mode']} | {d['va']:.1f} -> {d['vb']:.1f} | {d['i']:.2f} | {d['da']:.4f} / {d['da_map']:.4f} | "
          f"{d['db']:.4f} / {d['db_map']:.4f} | {d['vl_residual']:.3f} | {d['ripple']:.2f} / {fmt(d['ripple_an'], 2)} |")
    e = ag["energy"]
    a(f"\nEnergy balance of the switched model over a 20->40 A transient: in {e['e_in']:.2f} J, out {e['e_out']:.2f} J, "
      f"R loss {e['e_r']:.3f} J, capacitor {e['de_c']:.3f} J, inductor {e['de_l']:.3f} J -> residual {e['residual']:.3f} J "
      f"({e['rel']*100:.3f} %).\n")
    a("![agreement](agreement.png)\n")
    sp = R.get("spice")
    if sp:
        a(f"Independent check with ngspice ({sp['deck']}): one cell in band mode (800 -> 790 V, both legs switching, "
          f"{P['td_gates'][1]:.0f} ns dead time on every edge, body diodes) with fixed duties D_A {sp['da']:.4f} / D_B {sp['db']:.3f} from 30 A for 5 periods. The current "
          f"drifts by {sp['drift_sp']:.3f} A in ngspice and {sp['drift_py']:.3f} A in the switched model (the dead-time voltage "
          f"of both legs), ripple {sp['rip_sp']:.3f} / {sp['rip_py']:.3f} A, RMS waveform difference {sp['rms']*1e3:.1f} mA.\n")
        a("![ngspice](ngspice_check.png)\n")
    else:
        a("ngspice not found on PATH: the independent waveform check was skipped.\n")
    a("## 4. Mode transitions with an unknown dead time (V_A swept through V_B, switched model, 3 cells, 6.7 V/ms)\n")
    a("Every sweep is run for three dead-time sets; inside a set each cell has its own per-edge dead times (ns, leg A rise / "
      "A fall / B rise / B fall), the firmware always assumes " + f"{A('dt_hat_ns'):.0f} ns:\n")
    for k, v in dt_sets_ns().items():
        a(f"- {k}: " + "; ".join(f"cell {j+1} {x}" for j, x in enumerate(v)))
    a("\n| dead-time set | V_B [V] | sweeps | max sampled-current error [A] | max RMS [A] | mode changes per cell | peak i_L [A] |")
    a("|---|---|---|---|---|---|---|")
    for name in DT_SETS_FR:
        for vb in sorted({c["vb"] for c in R["trans"]}):
            cc = [c for c in R["trans"] if c["set"] == name and c["vb"] == vb]
            a(f"| {name} | {vb:.0f} | {len(cc)} (V_A up/down, +/-45 A) | {max(c['err_max'] for c in cc):.2f} | "
              f"{max(c['err_rms'] for c in cc):.2f} | {'/'.join(sorted({str(m) for c in cc for m in c['mode_changes']}))} | "
              f"{max(c['i_peak'] for c in cc):.1f} |")
    tt = R["trans"]
    a(f"\nOver all {len(tt)} sweeps every cell enters and leaves the band exactly once (no chatter), the worst sampled-current "
      f"deviation is {max(c['err_max'] for c in tt):.2f} A ({max(c['err_max'] for c in tt if c['vb'] == 550.0):.2f} A at full current, "
      f"V_B 550 V) and the highest instantaneous current {max(c['i_peak'] for c in tt):.1f} A (local OC trip band starts at "
      f"{P['trips']['oc_local'][1]:.1f} A). At 800 / 860 V the command is held by each cell's 27.5 kW limit, which "
      "steps by 1/D_max - 1 = 5.3 % with the cell's own mode and is slewed (part of the error there is that tracking).\n")
    be = R["band_edges"]
    a("Band edges seen in the sweeps (V_B / V_A at the mode change, all cells, sets and both power directions; the "
      f"dead-time-free rule is D_max = {P['Dmax']:.3f} and 1/D_max = {1/P['Dmax']:.3f}): " +
      "; ".join(f"{k} {v[0]:.4f}-{v[1]:.4f}" for k, v in be.items()) + ". With the longest dead times a leg's buck / "
      "boost duty can only reach D_max - t_d f_sw (" + f"{P['Dmax'] - A('dt_range_ns')[1] * 1e-9 * P['fsw']:.3f}" + "), so such a "
      "cell needs the band over a wider ratio window than the dead-time-free rule; the band costs both legs' switching "
      "loss there (power-stage efficiency model: nominal band only).\n")
    a("![transitions](transitions.png)\n")
    a("### 4.1 What the unknown dead time does and what the firmware does about it\n")
    a(f"- Per switching leg, the edge whose current opposes the commanded transition loses (or gains) t_d x f_sw x V_leg: "
      f"{A('dt_range_ns')[0]:.0f}-{A('dt_range_ns')[1]:.0f} ns is {A('dt_range_ns')[0]*1e-9*P['fsw']*100:.2f}-"
      f"{A('dt_range_ns')[1]*1e-9*P['fsw']*100:.2f} % of the leg voltage (" +
      ", ".join(f"{A('dt_range_ns')[0]*1e-9*P['fsw']*v:.1f}-{A('dt_range_ns')[1]*1e-9*P['fsw']*v:.1f} V at {v:.0f} V" for v in (550, 1000)) +
      "). The firmware compensates the midpoint; the residual is <= " +
      f"{rq['dt_res_s']*1e9:.0f} ns = " + ", ".join(f"{x:.1f} V at {v:.0f} V" for v, x in rq["dt_res_V"].items()) +
      f" per leg ({rq['dt_res_s']*1e9:.0f} ns x V volt-seconds per period).")
    a("- In a single-leg mode the PI integrator absorbs the switching leg's residual exactly (so the buck/boost -> band "
      "decision is exact); in band it holds the sum of both legs' residuals, so the band -> buck/boost decision is biased by "
      f"one leg's residual, <= {rq['dt_res_s']*P['fsw']:.4f} in duty. The band-exit hysteresis {G['hyst']} is {G['hyst']/(rq['dt_res_s']*P['fsw']):.1f} x "
      "that, so a cell cannot re-enter the mode it just left. When a leg starts or stops switching, its residual appears as "
      "a voltage step the current loop removes: that is the transition error in the table.")
    a("- The cause of the reported chatter was not the dead time itself: each cell's 27.5 kW current limit was computed "
      "from the ratio map duties_ss on the noisy V_B/V_A, so at a band edge the limit flipped by 1/D_max = +5.3 % every "
      "outer sample and kicked the current loops across the mode thresholds. The limit and the port-current conversions now "
      "use each cell's actual duty (which carries the hysteresis) and the limit is slewed.")
    zc = R["zc"]
    a("- Zero crossings (the compensation acts on a current near zero, where the ripple straddles zero in buck and all four "
      "edges flip together in band):\n")
    a("| compensation driven by | case | dead-time set | max error [A] | RMS near zero [A] | max near zero [A] |")
    a("|---|---|---|---|---|---|")
    for z in zc:
        a(f"| {'slew-limited reference (design)' if z['comp'] == 'ref' else 'measured current (slope-limited)'} | {z['label']} | {z['set']} | "
          f"{z['err_max']:.2f} | {z['near_rms']:.2f} | {z['near_max']:.2f} |")
    zr = [z for z in zc if z["comp"] == "ref"]
    zm = [z for z in zc if z["comp"] == "meas"]
    a(f"\n  Driven by the measured current the compensation is positive feedback on the current; its sign ramp must then be "
      f"slope-limited (<= {A('dt_slope_frac')} x kp with all four edges overlapping) and near zero it leaves the plant's sharp "
      f"dead-time step to the integrator: worst {max(z['err_max'] for z in zm):.2f} A. Driven by a model of the closed loop "
      f"(the reference slew-limited to {A('dt_model_slew_A_per_us')} A/us: equal to the reference on ramps, which the type-2 "
      "loop follows without lag, and rising like the current on a step) it adds no loop gain and can be sharp "
      f"({A('dt_ff_ramp_A'):.0f} A ramp): worst {max(z['err_max'] for z in zr):.2f} A, in the 500 ns cell where its edge current "
      "crosses zero (the buck ramp at 20 A/ms). Fed with the raw reference, a reversing step flipped the compensation "
      "before the current and added both legs' dead-time voltage to the overshoot; with a first-order model (80 us) the "
      "ramp lag mistimed the flip. Design: slew-limited model.\n\n  ![dead time](dead_time_zero_crossing.png)\n")
    a("- Large reference steps at / next to V_A = V_B (dead time 500 ns on every edge of cell 2, 150 ns on cell 1): mode "
      "sequence per cell (0 buck, 1 band, 2 boost) and overshoot beyond the new reference:\n")
    a("| V_A / V_B [V] | step [A] | modes cell 1 / 2 / 3 | overshoot [A] | peak i_L [A] |")
    a("|---|---|---|---|---|")
    for x in R["steps"]:
        a(f"| {x['va']:.0f} / {x['vb']:.0f} | {x['i0']:+.0f} -> {x['i1']:+.0f} | {' / '.join(''.join(map(str, q)) for q in x['seq'])} | "
          f"{x['overshoot']:.1f} | {x['i_peak']:.1f} |")
    a("\n  Inside the band no cell leaves it on a large step (exit needs the PI integrator filtered with "
      f"{A('mode_exit_tau_s')*1e3:.0f} ms to agree); a change "
      "of the final mode where the current reverses next to a band edge is the steady state moving, not an excursion. The "
      f"overshoot is the current loop's linear step response (~19 %, PI zero at {A('f_ci_Hz')/A('ci_zero_ratio'):.0f} Hz): "
      "commands from the BMS / supervisor should be slew-limited, as the outer loops are.\n")
    idn = R["ident"]
    a(f"- Optional identification: with each edge's dead time known exactly (residual 0) the worst transition error at "
      f"V_B 860 V falls from {max(x['err_mid'] for x in idn):.2f} A to {max(x['err_id'] for x in idn):.2f} A. Not needed for "
      "stability, chatter or limits; if wanted, firmware can learn each leg's residual from the integrator jump at its first "
      "band entry/exit (the jump is that leg's residual within (1 - D_max) x the voltage-measurement mismatch, ~0.7 V).")
    a("- Firmware therefore needs no per-unit dead-time value: midpoint estimate, compensation sign from the slew-limited "
      f"reference, band-exit hysteresis {G['hyst']} tested also on the filtered integrator, per-cell duty-based slewed limits "
      "(control_spec.json dead_time/firmware_must).\n")
    a("## 5. Stability over the envelope\n")
    for N in (3, 4):
        s = st[N]
        a(f"**{N} cells** ({s['n']} cases: V_A, V_B in 250/550/950/1000 V, 5 % and 100 % of P_lim, both directions; PV "
          f"incremental conductance left of / at / right of the MPP = {R['stab'][N]['g_rel']['left']:.2f} / 1 / "
          f"{R['stab'][N]['g_rel']['right']:.2f} x I/V; CPL and CP source against {N*P['C_cell']*1e6+P['C_x']*1e6:.0f} uF; battery "
          f"{A('batt_R')*1e3:.0f} mOhm / {A('batt_L')*1e6:.1f} uH). Closed-loop eigenvalues: {s['n_unstable']} unstable.\n")
        a("| loop | minimum | where |")
        a("|---|---|---|")
        a(f"| current, phase margin | {s['pm_i']['pm_i']:.1f} deg | {where(s['pm_i'])} |")
        a(f"| current, gain margin | {s['gm_i']['gm_i']:.1f} dB | {where(s['gm_i'])} |")
        a(f"| outer, phase margin | {s['pm_v']['pm_v']:.1f} deg (crossover {s['pm_v']['fc_v']:.0f} Hz) | {where(s['pm_v'])} |")
        a(f"| outer, modulus margin min abs(1+L) | {s['mm_v']['mm_v']:.2f} | {where(s['mm_v'])} |")
        a(f"| outer, gain margin (upper or lower) | {min(s['gm_v']['gm_v'], s['gm_v']['gm_lo_v']):.1f} dB | {where(s['gm_v'])} |")
        b = s["bat"]
        fcb = [x["fc_v"] for x in b if x["fc_v"] is not None]
        a(f"| CV on a battery (port B) | crossover {min(fcb):.2f}-{max(fcb):.2f} Hz, PM >= {min(x['pm_v'] for x in b):.0f} deg, "
          f"all stable | the battery's {A('batt_R')*1e3:.0f} mOhm makes the V_B plant tiny: the CV limit acts in ~0.1-1 s, "
          "adequate for a battery whose voltage moves slowly |")
        nof = sum(1 for x in R["stab"][N]["noff"] if not x["stable"])
        a(f"\nWithout the load-current feed-forward, {nof} of 16 CPL-on-B full-load corners are unstable "
          f"({', '.join(f'{x[chr(118)+chr(97)]:.0f}/{x[chr(118)+chr(98)]:.0f}' for x in R['stab'][N]['noff'] if not x['stable'])} V).\n")
    lr = R["stab"][3]["lrob"]
    a(f"Current loop with L at its extremes (0 A and the 72.4 A trip on the L(I) curve, times 0.90-1.10): PM "
      f"{min(x['pm'] for x in lr):.1f}-{max(x['pm'] for x in lr):.1f} deg, GM {min(x['gm'] for x in lr):.1f}-{max(x['gm'] for x in lr):.1f} dB, "
      f"crossover {min(x['fc'] for x in lr):.0f}-{max(x['fc'] for x in lr):.0f} Hz: a fixed gain copes with the soft saturation; "
      "no scheduling needed.\n")
    nt = R["stab"][3]["notes"]
    a(f"Notes: the worst outer-loop corner is the constant-power load drawing {3*P['imax']:.0f} A at 250 V (R = "
      f"-{250/(3*P['imax']):.2f} ohm against {nt['C']*1e6:.0f} uF, unstable pole {nt['f_cpl']:.0f} Hz). The margin there is set by how "
      "fast the feed-forward path (port-current sensor, sample, inner loop) cancels the negative conductance, not by the "
      f"voltage filter: moving the port-board RC from {1e-3/(2*math.pi*P['tau_rc']):.1f} kHz to 20 kHz changes the PM there from "
      f"{nt['pm_rc_asis']:.1f} to {nt['pm_rc_20k']:.1f} deg. The boost right-half-plane zero (regulating the higher-voltage port "
      f"while power flows into it) is in the model; at 250->1000 V, 45 A it sits at {nt['f_rhpz']/1e3:.2f} kHz and costs "
      f"{nt['ph_rhpz']:.1f} deg at that corner's {nt['fc_rhpz']:.0f} Hz crossover.\n")
    a("![margins](margins_map.png)\n\n![bode](bode.png)\n")
    a("## 6. Current sharing (switched model, worst sign pattern of the errors)\n")
    a("| cells | sensor errors | V_A -> V_B | reference [A] | per-cell average [A] | max deviation from mean | expected from sensor errors [A] |")
    a("|---|---|---|---|---|---|---|")
    for r in R["share"]:
        a(f"| {r['N']} | {r['label']} (gain {abs(r['sgain'][0])*100:.2f} %, offset {abs(r['soff'][0]):.1f} A) | {r['va']:.0f} -> {r['vb']:.0f} | "
          f"{r['i_ref']:.2f} | {' / '.join(f'{x:.2f}' for x in r['avg'])} | {r['dev_max']:.2f} A ({r['dev_pct']:.1f} % of 45 A) | "
          f"{' / '.join(f'{x:.2f}' for x in r['expect'])} |")
    a(f"\nInductance mismatch (L {', '.join(f'{x*100:+.0f} %' for x in R['share'][-1]['ltol'])}) only changes ripple and the "
      "transient; the DC share is set by the current-sensor gain and offset alone (each cell regulates its own sensor "
      "reading). Requirement: sensor gain <= +/-1 % and offset <= +/-0.3 A after calibration keeps every cell within "
      f"{max(r['dev_pct'] for r in R['share'] if r['label']=='calibrated'):.1f} % of 45 A; uncalibrated parts (2 %, 1 A) give "
      f"{max(r['dev_pct'] for r in R['share'] if r['label']=='uncalibrated'):.1f} %. The per-cell 45 A limit acts on the sensor "
      "reading, so a cell with a low-reading sensor carries up to gain x 45 A + offset more (46 A class) - inside the "
      "inductor and device ratings (peak normal 62.4 A, trip 72.4 A).\n")
    a("![sharing](sharing.png)\n")
    rv = R["rev"]
    a("## 7. Bidirectional operation and power reversal\n")
    a(f"- Current command +40 -> -40 A per cell in 4 ms at 800->600 V (through zero, where the dead-time voltage changes "
      f"sign and the ripple crosses zero): max sampled-current error {rv['ramp']['err_max']:.2f} A, RMS {rv['ramp']['err_rms']:.2f} A.")
    a(f"- Bus forming on port B at 750 V (battery on A at 700 V), constant-power load ramped +75 kW -> -75 kW in 2 ms "
      f"(inverter reverses): V_B stays within {rv['cpl']['vb_min']:.1f}..{rv['cpl']['vb_max']:.1f} V; peak instantaneous i_L "
      f"{rv['cpl']['i_peak']:.1f} A; final per-cell current {rv['cpl']['i_end']:.1f} A.\n")
    a("![reversal](reversal.png)\n")
    a("## 8. Start-up sequence (port_spec precharge, contactors, soft start, MPPT)\n")
    ta, va_t, ia_t, tca, oka = su["pre_a"]
    tb, vb_t, ib_t, tcb, okb = su["pre_b"]
    a(f"1. K_DISCH energised, K_A_PRE closes: the {su['C']*1e6:.0f} uF bus charges from the PV array (17s x 8p, 300 W/m2, "
      f"V_oc {su['voc']:.0f} V) through {P['r_pre']:.0f} ohm; |dV| <= {P['dv_ok']:.0f} V after {tca*1e3:.0f} ms "
      f"({'passed' if oka else 'FAILED'} the k = {P['k_check']} supervision curve); K_A_MAIN closes (inrush {su['ia_inrush']:.1f} A, "
      "the array is a current source), K_A_PRE opens 20 ms later.")
    a(f"2. K_B_PRE: port B bus from the battery (800 V) in {tcb*1e3:.0f} ms, K_B_MAIN closes at 10 V: inrush "
      f"{su['ib_inrush']:.0f} A (battery 32 mOhm / 3.5 uH; TDK HVC43 capacitive make 140 A at 20 V).")
    a(f"3. Gate enable at a carrier zero with the feed-forward duties (zero inductor voltage): the largest cell current in "
      f"the first two periods is {su['i_after_enable']:.2f} A (no inrush).")
    a(f"4. V_A reference = measured V_oc, MPPT steps down with {pvm.MPPT['dv_max_rel']*100:.0f} % steps, reference ramp 2000 V/s: "
      f"after 0.45 s the module delivers {su['eta_start']*100:.2f} % of P_mpp ({su['p_end']/1e3:.2f} of {su['pmpp']/1e3:.2f} kW).\n")
    a("![startup](startup.png)\n")
    a("## 9. Protection: requirements derived by simulation, checked against the trips as built (rev E)\n")
    a("### 9.1 Over-voltage after load rejection\n")
    rc = pr["rej_ctrl"]
    a(f"- With the control acting (MPPT at the 82.5 kW limit, bus at 1000 V opens): the V_B-max limit loop with "
      f"load-current feed-forward is selected within one outer sample; V_B peaks at **{rc['vb_peak']:.1f} V** and settles at "
      f"1010 V; firmware OV (1050 V) not reached{'' if not rc['trip'] else ' - TRIPPED: ' + rc['trip']}.")
    a(f"- Bus forming on B at 950 V, 82.5 kW constant-power load dropping to zero: peak {pr['rej_vb']['vb_peak']:.1f} V.")
    a(f"- Protection only (controller output frozen, inner current loops alive, firmware OV disabled), two cases: (a) "
      f"82.5 kW from the PV array at V_B 1000 V - V_B crosses 1100 V at {rq['ov_power']['slope']:.2f} V/us; (b) the cells held "
      f"at 45 A from a stiff port A at 1000 V with V_B at 611 V - the steepest crossing, {rq['ov_current']['slope']:.2f} V/us.")
    a(f"- Limit: last switching event at <= {rq['v_dev_lim']:.0f} V (0.85 x 1700 V with the {rq['os_v']:.0f} V turn-on ringing "
      f"(cell_spec) scaled to the bus voltage); C4AQ 1.15 x 1100 V = {rq['v_cap_lim']:.0f} V is not binding. Derived "
      f"requirement (ideal detector at 1100 V plus delay, table): **gates off <= {rq['ov_power']['t_max_us']:.0f} us after the "
      f"true 1100 V crossing in case (a), <= {rq['ov_current']['t_max_us']:.0f} us in case (b)**.\n")
    a("| ideal detector at 1100 V + added delay [us] | (a) gates off after crossing [us] | (a) V at gates off [V] | "
      "(b) gates off after crossing [us] | (b) V at gates off [V] |")
    a("|---|---|---|---|---|")
    for ra, rb in zip(pr["ov"]["power"]["curve"], pr["ov"]["current"]["curve"]):
        a(f"| {ra['extra_us']:.0f} | {fmt(ra['t_resp_us'])} | {fmt(ra['v_off'])} | {fmt(rb['t_resp_us'])} | {fmt(rb['v_off'])} |")
    a("\nAs built: the PPB compares every V_A / V_B conversion (worst phase: one full conversion period + "
      f"{A('t_ppb_chain_s')*1e6:.1f} us chain); the CMPSS backup is continuous ({A('t_trip_chain_s')*1e6:.1f} us chain). Both see "
      f"the port voltage through the divider RC ({1e-3/(2*math.pi*P['tau_rc']):.1f} kHz) and the isolated amplifier, a lag of "
      f"{rq['ov_detect_lag_us']:.0f} us on these ramps.\n")
    a("| path | threshold [V] | conversion period [us] | (a) gates off after crossing [us] | (a) V at gates off [V] | "
      "(b) gates off after crossing [us] | (b) V at gates off [V] |")
    a("|---|---|---|---|---|---|---|")

    def vv(r):
        return fmt(r["v_off"]) + ("" if r["v_off"] <= rq["v_dev_lim"] else " **over**")
    for ra, rb in zip(pr["ov"]["power"]["built"], pr["ov"]["current"]["built"]):
        a(f"| {ra['path']} | {ra['thr']:.1f} | {fmt(ra['period_us'] or None)} | "
          f"{fmt(ra['t_resp_us']) if ra['t_resp_us'] is not None else 'before 1100 V'} | {vv(ra)} | "
          f"{fmt(rb['t_resp_us']) if rb['t_resp_us'] is not None else 'before 1100 V'} | {vv(rb)} |")
    o, oc_ = rq["ov_power"], rq["ov_current"]
    a(f"\n- **PPB latency vs the {o['t_max_us']:.0f} us requirement (case a): with V_A / V_B converted every "
      f"{A('ppb_period_s')*1e6:.0f} us the PPB at its upper band edge ({P['trips']['ov_ppb'][2]:.1f} V) has the gates off "
      f"{o['hi']['t_resp_us']:.0f} us after the true crossing, at {o['hi']['v_off']:.1f} V -> {'MET' if o['meets'] else 'NOT MET'}** "
      f"(nominal threshold: {o['nom']['t_resp_us']:.0f} us). The conversion period may be up to {o['per_max_us']:.0f} us; with only "
      f"the {P['fsw']/1e3:.0f} kHz control samples ({P['T']*1e6:.2f} us) it is {o['hi_ctl']['t_resp_us']:.0f} us / "
      f"{o['hi_ctl']['v_off']:.1f} V -> {'met' if o['meets_ctl_rate'] else 'NOT met'}: V_A and V_B must be converted for the PPB at "
      f">= {1e3/o['per_max_us']:.0f} kS/s (V_A runs at 500 kS/s for MPPT anyway).")
    over_b = oc_['hi']['v_off'] - rq['v_dev_lim']
    a(f"- Case (b), the steepest crossing: the PPB at its upper band edge has the gates off at {oc_['hi']['v_off']:.1f} V -> "
      + (f"met (margin {rq['v_dev_lim'] - oc_['hi']['v_off']:.1f} V; the port board's shorter divider lag closed the earlier "
         "gap). " if oc_['meets'] else f"**NOT met** by {over_b:.1f} V ({oc_['hi']['v_off']*(1+rq['os_v']/P['v_ovp'])/1700:.3f} x "
         f"V_DSS at the device instead of 0.85). Either fix closes it: PPB nominal threshold <= {oc_['thr_max_V']:.0f} V or a "
         f"divider + amplifier lag <= {oc_['lag_max_us']:.0f} us. ") +
      "Case (b) needs a double fault (outer control frozen with the inner loops running, and the bus lost).")
    a(f"- CMPSS backup alone (PPB failed): {o['cmp_nom']['v_off']:.0f} V at its nominal threshold, {o['cmp_hi']['v_off']:.0f} V at "
      f"its upper band edge ({o['cmp_hi']['v_off']*(1+rq['os_v']/P['v_ovp'])/1700:.3f} x V_DSS at the device: above the 0.85 rule, "
      "below the 1700 V rating). Acceptable for a second line; the 1050 V firmware trip on the outer-loop sample acts before "
      "either in any case where the firmware runs.\n")
    a("### 9.2 Short circuits\n")
    a("| case | trips in force | peak i_L [A] | first trip | detected after short [us] | gates off after short [us] | "
      "upper band -> 50 % L0, no trip: local / backup [us] |")
    a("|---|---|---|---|---|---|---|")
    for r in pr["shorts"]:
        a(f"| {r['label']} | {r['variant']} | {r['i_peak']:.1f} | {r.get('first', '-')} | {fmt(r.get('t_detect_us'))} | "
          f"{fmt(r.get('t_off_us'))} | {fmt(r.get('t_local_to_sat_us'))} / {fmt(r.get('t_backup_to_sat_us'))} |")
    a(f"\n- Terminal shorts (5 mOhm / 1 uH bolted, at full power): the port SC CMPSS on I_A / I_B (rev E) trips first, gates "
      f"off <= {rq['term_t_off_us']:.1f} us after the short, peak inductor current {rq['term_peak']:.0f} A (normal peak).")
    a(f"- Bank shorts (inside the module, not seen by the port shunt): the inductor current rises at up to "
      f"{rq['oc_didt_max']:.1f} A/us. From the local window's upper band edge ({P['trips']['oc_local'][2]:.1f} A) it reaches "
      f"{P['i50']:.0f} A (L = 50 % L0) in {rq['oc_t_max_local_us']:.2f} us: **local layer as built {rq['oc_local_t_us']:.2f} us -> "
      f"peak {rq['oc_local_peak']:.0f} A, met.** From the CTRL backup's upper band edge ({P['trips']['oc_backup'][2]:.1f} A) only "
      f"{rq['oc_t_max_backup_us']:.2f} us remain: **backup as built (LEM LA 150-P {P['trips']['sensor_delay_us']:.2f} us + AFE + "
      f"chain = {rq['oc_backup_t_us']:.2f} us; requirement: sensor delay <= {R['meas']['cell_inductor_current']['group_delay_us_max']:.2f} us) "
      f"-> {rq['oc_backup_peak']:.0f} A, {'met' if rq['oc_backup_peak'] < P['i50'] else 'NOT met'}** against {P['i50']:.0f} A "
      f"(L = 50 % L0) and the {rq['i_dm_position']:.0f} A device pulse rating per position. For reference, the replaced "
      f"AMC3302 ({rq['oc_amc_t_us']:.2f} us) would give {rq['oc_amc_peak']:.0f} A.")
    a("- A short on port A while power flows A->B reverses the current: every layer is a window (+/-).")
    a(f"- **Bus reverse clamp (cell_spec bus_reverse_clamp, {P['clamp'].get('devices_per_bank', 8)} x "
      f"{P['clamp'].get('part', '?')} per bank), terminal short re-run:** the bank rings through zero and the clamp takes the "
      f"freewheel current: up to {rq['clamp_module_A']/1e3:.1f} kA per module, {rq['clamp_dev_A']:.0f} A and "
      f"{rq['clamp_I2t']:.0f} A2s per clamp device (rating I_FSM {rq['clamp_ratings'].get('I_FSM_10ms_A', '?')} A / I2t "
      f"{rq['clamp_ratings'].get('I2t_10ms_A2s', '?')} A2s at 10 ms), bank reversal {rq['v_reverse']:.1f} V; the legs' two series "
      f"body diodes need {2*A('body_v0_V'):.1f} V to conduct and carry {rq['body_dev_A']:.0f} A per device in this static "
      f"model (cell_spec's ngspice with branch inductances: {rq['clamp_spec_dyn_body_A']} A per device vs I_DM 200 A). Without "
      f"the clamp the same short puts {rq['body_dev_noclamp_A']:.0f} A on each body diode (bank reversed to "
      f"{-rq['v_reverse_noclamp']:.0f} V). **Confirmed: the clamp removes the body-diode over-current.** My short (5 mOhm / "
      f"1 uH) gives {rq['clamp_dev_A']:.0f} A per clamp device; cell_spec's worst case (4 cells, 1100 V, its own short and "
      f"branch model) is {rq['clamp_spec_dev_A']} A - both inside the ratings.")
    a("- After the trip the inductor current freewheels through two body diodes (2 x 2.9 V against it) and decays.")
    d = rq["desat"]
    a(f"\n### 9.3 Short inside a cell (leg shoot-through, switch-node or inductor short)\n\nNot seen by the inductor sensor "
      f"when the inductor is bypassed: the gate driver's DESAT is the protection ({P['driver']}, cell_spec). Detection "
      f"{d['blank_range_us'][0]:.2f}-{d['blank_range_us'][1]:.2f} us after the device leaves saturation (leading-edge blanking + "
      f"filter), then the rev-5 short-circuit booster has the gates off {d['off_after_detect_us']:.2f} us later (required <= "
      f"{fmt(d['required_detect_to_off_us'], 1)} us; the driver's internal soft turn-off alone would take "
      f"{fmt(d['soft_off_only_us'], 2)} us): **{d['typ_us']:.2f} us typ / {d['max_us']:.2f} us worst** against a short-circuit "
      f"withstand of {d['scwt_1100_us']:.1f} us that is an ASSUMPTION (no Chinese datasheet states one; the MSC035SMA170B4 "
      f"alternate states {d['scwt_1200_us']:.1f} us typ at 1200 V): met with {d['scwt_1100_us']-d['max_us']:.2f} us margin if the "
      f"assumption holds. The DESAT threshold {d['vth'][0]:.1f}-{d['vth'][1]:.1f} V corresponds to {d['desat_A_25C'][0]:.0f}-"
      f"{d['desat_A_25C'][1]:.0f} A (25 C) and {d['desat_A_175C'][0]:.0f}-{d['desat_A_175C'][1]:.0f} A (175 C) per position over "
      "both device options, i.e. DESAT is a short-circuit detector only; the inductor OC layers are the over-current protection. "
      "Open (cell_spec): the booster is sized for the Sichain primary only, not for the MSC035 fallback.\n")
    cl, pl = pr["cell_loss"], pr["pv_loss"]
    a("### 9.4 Loss of one cell at full load\n")
    a(f"MPPT on a 17s x 9p array at 1000 W/m2 (module at its 82.5 kW limit, i.e. every cell at its own 27.5 kW), cell 3 "
      f"faults at 3 ms: the references are re-divided by the healthy cells, which stay at their own 27.5 kW limit (sampled "
      f"average max {cl['i_samp_max']:.1f} A, instantaneous peak {cl['i_peak']:.1f} A, below the local trip band); module power "
      f"{cl['p_before']/1e3:.1f} -> {cl['p_after']/1e3:.1f} kW and V_A moves right on the PV curve to {cl['va_max']:.0f} V; no trip"
      f"{'' if not cl['trip'] else ' (TRIP: ' + cl['trip'] + ')'}. Below full load the healthy cells take over the lost share "
      "up to the same per-cell limits.\n")
    a("### 9.5 Loss of the PV source\n")
    a(f"Array disconnected at 82.5 kW: V_A falls at ~0.4 V/us until the V_A loop (300 Hz, reference clamped >= 0, so no "
      f"reverse power into port A) has cut the current: minimum V_A {pl['va_min']:.0f} V (UV stop 240 V "
      f"{'not reached' if not pl['trip'] else 'reached: ' + pl['trip']}), V_B max {pl['vb_max']:.1f} V. The supervisor "
      "declares 'PV lost' on zero power with the reference not followed and stops; at an operating point below ~450 V the "
      "same dip reaches the 240 V UV stop, which is the intended outcome.\n")
    tr = P["trips"]
    a("### 9.6 Other rev E features (described, not simulated)\n")
    a(f"- Open conductors: an open or unpowered V_A / V_B pair reads <= {tr['open_v']:.0f} V, an open I_A / I_B conductor <= "
      f"{tr['open_i']:.0f} A, an open AN1 <= {tr['open_an1']:.0f} A - all trip (CMPSS low / PPB low). Firmware must treat these "
      "as sensor faults (no restart attempt on a plausibility basis).")
    a("- The PWM line drivers on CTRL-C2000 are tri-stated unless CHB_OK, the MCU enable and that cell's RDY are all high: "
      "the controller's soft start (enable at a carrier zero with feed-forward duties, section 8) begins only after RDY.\n")
    a("![protection](protection.png)\n")
    a("## 10. Requirements on sensing and protection hardware\n")
    a("| signal / trip | requirement | as built / as drawn | met |")
    a("|---|---|---|---|")
    m = R["meas"]
    ci = m["cell_inductor_current"]
    a(f"| cell i_L sensor | +/-{A('i_sense_fs_A'):.0f} A, >= {A('f_isense_Hz')/1e3:.0f} kHz, signal delay <= "
      f"{ci['group_delay_us_max']:.2f} us (CTRL backup layer), gain <= 1 %, offset <= 0.3 A after the idle re-zero | LEM LA 150-P: "
      f"{P['trips']['sensor_delay_us']:.2f} us, -3 dB {sensor_bw_hz()/1e3:.0f} kHz (inferred), 0.0759 A/count | "
      f"{'yes' if P['trips']['sensor_delay_us'] <= ci['group_delay_us_max'] else 'NO'} |")
    a(f"| cell OC local window | gates off <= {rq['oc_t_max_local_us']:.2f} us after {tr['oc_local'][2]:.1f} A | "
      f"{tr['t_local_us'][0]:.2f}-{tr['t_local_us'][1]:.2f} us, peak {rq['oc_local_peak']:.0f} A | yes |")
    a(f"| cell OC CTRL backup | gates off <= {rq['oc_t_max_backup_us']:.2f} us after {tr['oc_backup'][2]:.1f} A | "
      f"{rq['oc_backup_t_us']:.2f} us with the required sensor, peak {rq['oc_backup_peak']:.0f} A | yes (with the required sensor) |")
    a(f"| V_A / V_B conversions for the PPB | every <= {o['per_max_us']:.0f} us | every <= 10 us (rev E) | yes |")
    a(f"| port OV PPB (case a, 82.5 kW) | gates off <= {o['t_max_us']:.0f} us after the true 1100 V crossing | "
      f"{o['hi']['t_resp_us']:.0f} us at the {tr['ov_ppb'][2]:.1f} V band edge | {'yes' if o['meets'] else 'NO'} |")
    a(f"| port OV PPB (case b, frozen 45 A) | <= {oc_['t_max_us']:.0f} us | {oc_['hi']['t_resp_us']:.0f} us | "
      + ("yes |" if oc_['meets'] else f"NO (threshold <= {oc_['thr_max_V']:.0f} V or lag <= {oc_['lag_max_us']:.0f} us) |"))
    a(f"| port OV CMPSS backup | second line only | {o['cmp_hi']['v_off']:.0f} V at the upper band edge | above the 0.85 rule, "
      "below the rating |")
    a(f"| V_A / V_B signal | 12 bit, >= 3 kHz | {P['lsb_v']:.2f} V/LSB, {1e-3/(2*math.pi*P['tau_rc']):.1f} kHz RC, lag "
      f"{rq['ov_detect_lag_us']:.0f} us | yes |")
    a(f"| port SC / OC (I_A, I_B) | terminal short off before the inductor current leaves its normal range | gates off "
      f"{rq['term_t_off_us']:.1f} us after the short, {rq['term_peak']:.0f} A | yes |")
    a(f"| I_A / I_B for feed-forward | bus port, <= ~10 us delay | AMC3302 chain {P['tau_ip']*1e6:.1f} us | yes |")
    a(f"| bus reverse clamp | body diodes <= I_DM on a terminal short | {rq['clamp_dev_A']:.0f} A / {rq['clamp_I2t']:.0f} A2s per "
      f"clamp device, body {rq['body_dev_A']:.0f} A (static) / {rq['clamp_spec_dyn_body_A']} A (cell_spec dynamic) | yes |")
    a(f"| DESAT + booster | gates off within the withstand ({d['scwt_1100_us']:.1f} us ASSUMED) | {d['typ_us']:.2f} us typ / "
      f"{d['max_us']:.2f} us worst | {'yes, if the assumed withstand holds' if d['max_us'] < d['scwt_1100_us'] else 'NO'} |")
    a("| MPPT power measurement | see sim/out/pv_mppt/report.md | | |")
    a("\n## 11. Synchronous-sampling ripple (port A)\n")
    for x in R["ripple"]:
        a(f"- {x['va']:.0f}->{x['vb']:.0f} V at {x['i']:.1f} A/cell: V_A ripple {x['va_pp']:.3f} V pp, sample minus period mean "
          f"at the capacitor {x['bias_raw']*1e3:.1f} mV; behind the 3.2 kHz RC the 96 kHz ripple is attenuated ~30x, so the "
          "ADC bias is negligible.")
    plaus, sup = supervisor_sections(P, R)
    a("\n## 12. Supervisor and plausibility (port board rev 6, control card rev E, SYS-IO-AUX rev E)\n")
    a("### 12.1 Port current limit vs inlet temperature (firmware)\n")
    a("Per-cell current limit = min(45 A, 27.5 kW / v_A, k_A I_portA,max(T) / (N D_A), k_B I_portB,max(T) / (N D_B)), "
      "slewed; k_A, k_B are slow integral trims (5 ms) on the measured port currents, because the conversion through the "
      "commanded duty is biased by the dead time (~2 %). The lowest port voltage at which rated / maximum power is still "
      "available:\n")
    w = sup["port_current_limit_vs_inlet_C"]["full_power_window"]
    temps = [x["inlet_C"] for x in next(iter(w.values()))]
    a("| port | " + " | ".join(f"{t:.0f} C" for t in temps) + " |")
    a("|---|" + "---|" * len(temps))
    for k, v in w.items():
        a(f"| {k}: I_max / V for rated / V for max | " + " | ".join(f"{x['i_port_max_A']:.1f} A / {x['v_min_for_rated_V']} / "
                                                             f"{x['v_min_for_max_power_V']} V" for x in v) + " |")
    ck = sup["port_current_limit_vs_inlet_C"]["check_averaged_model"]
    a("\n" + "; ".join(f"Check (averaged model, {c['N']} cells, 45 A commanded, {c['inlet_C']:.0f} C inlet): port {c['port']} "
                       f"carries {c['i_port']:.1f} A against the {c['i_lim']:.1f} A limit" for c in ck) + ".")
    a("- PV-P75 (PV port A, 135 A): above 45 C the PV port limit cuts into the full-power window from below - at 60 C rated "
      "power needs >= " + f"{[x for x in w['3 cells, port A (pv)'] if x['inlet_C'] == 60.0][0]['v_min_for_rated_V']} V and "
      f"82.5 kW >= {[x for x in w['3 cells, port A (pv)'] if x['inlet_C'] == 60.0][0]['v_min_for_max_power_V']} V (550 V at 45 C). "
      "PV-20 already derates power above 45 C, so this mostly coincides with the thermal derating. Port B (battery type, "
      "135 A) does not derate.")
    w4 = w["4 cells, port B (battery)"]
    a(f"- PV-P100/110 (battery port B, 180 A): the limit starts at 35 C; at 45 C rated 100 kW needs >= "
      f"{[x for x in w4 if x['inlet_C'] == 45.0][0]['v_min_for_rated_V']} V and 110 kW >= "
      f"{[x for x in w4 if x['inlet_C'] == 45.0][0]['v_min_for_max_power_V']} V on port B; at 60 C "
      f"{[x for x in w4 if x['inlet_C'] == 60.0][0]['v_min_for_rated_V']} / {[x for x in w4 if x['inlet_C'] == 60.0][0]['v_min_for_max_power_V']} V: "
      "the 550-950 V full-power window of PV-07 is not kept above 35 C on a battery port of the 4-cell module (fuse "
      "derating, port_spec) - an owner decision (bigger fuse) or a stated derating.\n")
    a("### 12.2 Plausibility\n")
    pa = plaus["cell_AN1_open_conductor"]
    a(f"- Cell AN1 (LEM LA 150-P): offset re-zeroed at every idle, a null above 5 A rejected. Both lines open reads <= "
      f"{pa['both_lines_open_reads_A_max']:.0f} A and trips the CTRL window; one line open reads {pa['one_line_open_reads_A_max']:.0f} / "
      f"{pa['one_line_open_other_line_reads_A']:.0f} A - inside the window: caught by the idle null check and the cell's own "
      "hardware OC trip, not by the comparator.")
    rb = plaus["port_ID_readback"]
    a(f"- Port ID line (PV-PORT): {len(rb['states_adc_counts'])} readback states decoded from ADC counts ("
      + ", ".join(f"{k} {v[0]}-{v[1]}" for k, v in list(rb['states_adc_counts'].items())[:3]) + ", ...): " + rb["meaning"] +
      f". The varistor monitor is judged only above {rb['varistor_monitor_judged_above_port_V']:.0f} V port voltage (dark PV port at night).")
    im = plaus["insulation_monitor"]
    a(f"- Insulation monitor: an open earth lead reads as no swing -> fault. Settling {im['settle_s_per_state_3tau'][0]}-"
      f"{im['settle_s_per_state_3tau'][1]} s per "
      f"state with 200 nF/kWp; a 3-sample prediction needs ~{im['prediction_s_per_state']} s per state (~3 s total). Only one "
      "monitor per connected DC system: the module disables its own by leaving both test strings open.\n")
    a("### 12.3 Start-up after a reboot on AUX-HV (D-034)\n")
    for x in sup["aux_hv_restart_D034"]["firmware_start_up"]:
        a(f"- {x}")
    a("\n## 13. Open items and honesty\n")
    a("- Nothing is bench-validated. Switches are ideal (no switching transients, no C_oss charging in the dead time - the "
      "real dead-time voltage near zero current is smaller and smoother than modelled), the cells are lumped on one port "
      "node (no busbar inductance between cells), sensors are first-order lags, the PV array is static (no array "
      "capacitance), the inverter on port B is an ideal constant-power load (the worst case: its own DC-link capacitance "
      "would only help), the battery is an EMF behind 32 mOhm / 3.5 uH.")
    a("- The cell inductor-current sensor is now a LEM LA 150-P (closed-loop Hall): its -3 dB bandwidth is inferred from "
      "LEM's -1 dB figure, its delay is LEM's tD90; the CTRL backup OC layer is the one that depends on that delay.")
    a(f"- Devices: Sichain {P['device']} (primary; no qualification statement, no short-circuit rating) with the "
      "MSC035SMA170B4 as the qualified fallback; the protection timing uses the assumed 2.0 us withstand.")
    a("- Dead time: the model's dead-time effect is ideal (body diode for the whole dead time, no C_oss charging); with "
      "soft transitions near zero current the real effect is smaller, never larger, than modelled.")
    a("- The bus reverse clamp is modelled statically at the lumped port node (no branch inductance); the dynamic share "
      "of the body diodes is cell_spec's ngspice result.")
    a("- The outer-loop phase margin at the 250 V / 135 A constant-power-load corner is ~40 deg (modulus margin "
      f"{st[3]['mm_v']['mm_v']:.2f}): adequate, below the 45 deg rule of thumb; a larger port capacitance or the inverter's own "
      "DC link raises it.")
    a("- Load-current feed-forward must be switched by port type (bus 1, PV/battery 0): the supervisor needs to know what "
      "is connected (BMS present = battery).")
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L) + "\n")


def self_check(P, G, R):
    """physics / model consistency checks that a broken model would fail"""
    d = duties_ss(P, 900.0, 600.0)
    assert abs(d[0] - 600 / 900) < 1e-12 and d[1] == 1.0 and d[2] == "buck"
    d = duties_ss(P, 600.0, 900.0)
    assert d[0] == 1.0 and abs(d[1] - 600 / 900) < 1e-12 and d[2] == "boost"
    d = duties_ss(P, 800.0, 800.0)
    assert d[2] == "band" and abs(d[0] - P["Dmax"]) < 1e-12 and abs(d[1] - P["Dmax"]) < 1e-12
    for u in (-20.0, 0.0, 20.0):                       # modulator delivers the requested inductor voltage in every mode
        for mode, va, vb in (("buck", 900.0, 600.0), ("boost", 600.0, 900.0), ("band", 800.0, 805.0)):
            da, db, md, ur = modulate(P, G, mode, u, 0.0, 0.0, va, vb, may_change=False)
            assert abs(ur - u) < 1e-9 and abs(da * va - db * vb - u) < 1e-9, (mode, u)
    ag = R["agree"]
    for s in ag["steps"] + ag["n_cells"]:
        assert s["rms_sw"] < 0.06 and s["rms_avg"] < 0.06, ("current-step agreement", s.get("mode", s.get("N")))
        assert abs(s["os_sw"] - s["os_lin"]) < 0.06 and abs(s["os_avg"] - s["os_lin"]) < 0.06
    for s in ag["outer"]:
        assert s["rms_sw"] < 0.08 and s["rms_avg"] < 0.08, ("outer-loop agreement", s["label"])
    for x in ag["duty"]:
        assert abs(x["vl_residual"]) < 0.5, "steady-state volt-second balance with the analytic duty map"
        if x["ripple_an"]:
            assert abs(x["ripple"] / x["ripple_an"] - 1) < 0.03, "ripple vs analytic"
    assert ag["energy"]["rel"] < 2e-3, "energy balance of the switched model"
    if R.get("spice"):
        sp = R["spice"]
        assert sp["rms"] < 0.05 and abs(sp["drift_sp"] - sp["drift_py"]) < 0.1, "switched model vs ngspice (band, dead time)"
    lo_band = P["trips"]["oc_local"][1]
    for c in R["trans"]:                       # over every dead-time set: no chatter, no spike, bounded error
        assert all(m == 2 for m in c["mode_changes"]), ("one band entry + exit per sweep", c["set"], c["vb"], c["v0"], c["i_cmd"])
        assert c["i_peak"] < lo_band - 10.0, ("transition peak below the OC trip band", c["set"], c["vb"])
        assert c["err_max"] < (2.0 if c["vb"] == 550.0 else 3.0), ("transition error", c["set"], c["vb"], c["v0"], c["i_cmd"])
    assert len({c["set"] for c in R["trans"]}) == len(DT_SETS_FR) and len(R["trans"]) == 12 * len(DT_SETS_FR)
    assert G["hyst"] >= 2.5 * 0.5 * (A("dt_range_ns")[1] - A("dt_range_ns")[0]) * 1e-9 * P["fsw"] - 1e-12, "hysteresis vs residual"
    for c in R["inlet"]["checks"]:              # port-current limit vs inlet temperature holds (measured-current trim)
        assert abs(c["i_port"] / c["i_lim"] - 1) < 0.01 and c["i_port_peak"] < 1.03 * c["i_lim"], ("inlet limit", c)
    for z in R["zc"]:
        if z["comp"] == "ref":
            assert z["err_max"] < 4.0 and z["near_rms"] < 1.0, ("zero crossing, reference-driven compensation", z["set"], z["label"])
    assert max(x["err_id"] for x in R["ident"]) <= max(x["err_mid"] for x in R["ident"]) + 0.3
    for x in R["steps"]:
        if x["va"] == 800.0 and x["vb"] == 805.0:
            assert all(q == [1] for q in x["seq"]), ("no band excursion on a large step", x["i0"], x["i1"])
        assert x["i_peak"] < lo_band - 10.0
    for r in R["share"]:
        lim = 1.5 * (r["i_ref"] * abs(r["sgain"][0]) + abs(r["soff"][0])) + 0.3
        assert r["dev_max"] < lim, ("sharing error explained by the sensor errors", r["N"], r["label"])
    for N in (3, 4):
        s = summary_stab(R["stab"][N]["rows"])
        assert s["n_unstable"] == 0, "closed-loop eigenvalues inside the unit circle at every corner"
        assert s["pm_i"]["pm_i"] > 45 and s["gm_i"]["gm_i"] > 6
        assert s["pm_v"]["pm_v"] > 30 and s["mm_v"]["mm_v"] > 0.5
    rq, pr = R["req"], R["prot"]
    assert pr["rej_ctrl"]["vb_peak"] < P["v_ov_sw"] and not pr["rej_ctrl"]["trip"], "load rejection handled by the limit loop"
    for kind in ("power", "current"):          # model consistency: later detection -> higher voltage at gates off
        cur = pr["ov"][kind]["curve"]
        assert all(b["v_off"] > a_["v_off"] for a_, b in zip(cur, cur[1:])), ("OV curve monotonic", kind)
        o = rq["ov_" + kind]
        assert o["hi_ctl"]["v_off"] > o["hi"]["v_off"] > o["nom"]["v_off"], ("PPB period / band ordering", kind)
    assert rq["ov_power"]["meets"], "rev E PPB (10 us conversions, upper band edge) meets the 82.5 kW load-rejection requirement"
    assert rq["oc_local_peak"] < P["i50"] and rq["oc_backup_peak"] < P["i50"], "OC layers (backup with the required sensor)"
    assert rq["term_first"] == ["port SC"] and rq["term_peak"] < lo_band, "terminal shorts: port SC trips first"
    assert rq["body_dev_A"] < 200.0 < rq["body_dev_noclamp_A"], "reverse clamp keeps the body diodes inside I_DM"
    assert rq["clamp_I2t"] < rq["clamp_ratings"].get("I2t_10ms_A2s", 1e9), "clamp I2t inside its rating"
    assert pr["cell_loss"]["i_peak"] < lo_band and not pr["cell_loss"]["trip"]
    assert pr["cell_loss"]["i_samp_max"] < P["imax"] * (1 + A("sens_gain_err")) + 0.5
    assert min(pr["pv_loss"]["lg"]["im"][pr["pv_loss"]["lg"]["t"] > 3e-3].mean(axis=1)) > -1.0, "no reverse power into the PV port"
    su = R["start"]
    assert su["pre_a"][4] and su["pre_b"][4] and su["ib_inrush"] < 140.0 and su["i_after_enable"] < 2.0
    assert su["eta_start"] > 0.97, "MPPT start-up reaches the MPP"


def run():
    import time
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    P = load_params(3)
    G = design(P)
    R = {}
    R["agree"] = study_agreement(P, G)
    sc = ss_sc(P, 3, 1000.0, 600.0, 45.0, cmd=lambda t, cs: ("I", 45.0), noise=False)
    R["wave"] = simulate(P, G, sc, 0.4e-3, switched=True, fine=(0.3e-3, 0.4e-3))
    R["trans"] = study_transitions(P, G)
    R["band_edges"] = band_edges(R["trans"])
    R["ident"] = study_identified(P, G, R["trans"])
    R["zc"] = study_zero_crossing(P, G)
    R["steps"] = study_steps(P, G)
    R["inlet"] = study_inlet(P)
    R["share"] = study_sharing(P, G)
    R["rev"] = study_reversal(P, G)
    R["start"] = study_startup(P, G)
    R["stab"] = {3: stability(P, 3), 4: stability(P, 4)}
    R["prot"] = study_protection(P, G)
    R["ripple"] = ripple_bias(P, G)
    R["spice"] = ngspice_check(P, G)
    R["req"] = derive_requirements(P, G, R)
    R["meas"] = meas_requirements(P, G, R)
    R["bode_i"], R["bode_v"] = bode_sets(P, G, R)
    plot_all(P, G, R)
    write_csvs(P, G, R)
    spec = write_spec(P, G, R)
    write_report(P, G, R, spec)
    self_check(P, G, R)
    s3 = summary_stab(R["stab"][3]["rows"])
    print(f"current loop: crossover {s3['fc_i'][0]:.0f}-{s3['fc_i'][1]:.0f} Hz, min PM {s3['pm_i']['pm_i']:.1f} deg, "
          f"min GM {s3['gm_i']['gm_i']:.1f} dB; outer min PM {s3['pm_v']['pm_v']:.1f} deg / mm {s3['mm_v']['mm_v']:.2f} at "
          f"{where(s3['pm_v'])}")
    rq = R["req"]
    print(f"transitions over {len(R['trans'])} sweeps x dead-time sets: max err {max(c['err_max'] for c in R['trans']):.2f} A "
          f"({max(c['err_max'] for c in R['trans'] if c['vb'] == 550.0):.2f} A at full current), mode changes "
          f"{sorted({m for c in R['trans'] for m in c['mode_changes']})}; zero crossing max "
          f"{max(z['err_max'] for z in R['zc'] if z['comp'] == 'ref'):.2f} A; sharing max "
          f"{max(r['dev_pct'] for r in R['share'] if r['label']=='calibrated'):.1f} % (calibrated)")
    print(f"OV (82.5 kW): need <= {rq['ov_t_max_us']:.0f} us, PPB upper band at 10 us conversions {rq['ov_t_designed_us']:.0f} us "
          f"({rq['ov_v_off_designed']:.0f} V); frozen 45 A: need <= {rq['ov_current']['t_max_us']:.0f} us, have "
          f"{rq['ov_current']['hi']['t_resp_us']:.0f} us ({rq['ov_current']['hi']['v_off']:.0f} V)")
    print(f"OC: local {rq['oc_local_t_us']:.2f} us -> {rq['oc_local_peak']:.0f} A, backup {rq['oc_backup_t_us']:.2f} us -> "
          f"{rq['oc_backup_peak']:.0f} A (AMC3302 as drawn {rq['oc_amc_peak']:.0f} A); terminal short: {rq['term_first']} off at "
          f"{rq['term_t_off_us']:.1f} us; clamp {rq['clamp_dev_A']:.0f} A/device, body {rq['body_dev_A']:.0f} A (no clamp "
          f"{rq['body_dev_noclamp_A']:.0f} A)")
    print(f"pv_control self-check passed ({time.time()-t0:.0f} s)")


if __name__ == "__main__":
    run()
