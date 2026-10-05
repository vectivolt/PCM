"""PV-P75 / PV-P100-110 module control (PV-01..PV-22, PV-C1, PV-C3): plant, digital control, stability, protection and
the delivered-power envelope of the cost-first build.

Run:  .venv/bin/python sim/pv_control.py        (then sim/pv_mppt.py for the MPPT study)
In:   the boards as drawn (D-044, D-058): hardware/PV-PWR (3 phases) and PV-PWR-4 (4 phases) design checks (port banks,
      inductor-current sensor, port-current path), hardware/PV-CTL design check (AFE, ADC, trip table, firmware layer);
      sim/out/pv_design/cell_spec.json + inductor_L_vs_I.csv (power stage), sim/out/magnetics/design_pv_inductor.json
      (inductor rev M2), sim/out/pv_design/module_spec.json (ratings, lean port limits, thermal derating),
      sim/out/port_design/port_spec.json 'lean' (precharge, port firmware rules).  Every input must exist: a missing file
      or line stops the run and names the script that writes it (no built-in substitute values).
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
import re
import sys

import numpy as np
import scipy.interpolate as sip
import scipy.linalg as sla

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out", "pv_control")
CELL_SPEC = os.path.join(HERE, "out", "pv_design", "cell_spec.json")
L_CSV = os.path.join(HERE, "out", "pv_design", "inductor_L_vs_I.csv")
MODULE_SPEC = os.path.join(HERE, "out", "pv_design", "module_spec.json")
MODULE_GRID = os.path.join(HERE, "out", "pv_design", "module_envelope.csv")
MAG_SPEC = os.path.join(HERE, "out", "magnetics", "design_pv_inductor.json")
PORT_SPEC = os.path.join(HERE, "out", "port_design", "port_spec.json")
PWR_CHECK = {3: os.path.join(ROOT, "hardware", "PV-PWR", "outputs", "PV-PWR_design_check.txt"),
             4: os.path.join(ROOT, "hardware", "PV-PWR-4", "outputs", "PV-PWR-4_design_check.txt")}
CTL_CHECK = os.path.join(ROOT, "hardware", "PV-CTL", "outputs", "PV-CTL_design_check.txt")
MAKER = {CELL_SPEC: "sim/pv_design.py", L_CSV: "sim/pv_design.py", MODULE_SPEC: "sim/pv_module.py",
         MODULE_GRID: "sim/pv_module.py", MAG_SPEC: "sim/magnetics.py", PORT_SPEC: "sim/port_design.py",
         PWR_CHECK[3]: "gen/pv_power.py", PWR_CHECK[4]: "gen/pv_power.py", CTL_CHECK: "gen/pv_ctrl.py"}
# phases -> (drawn build, module_spec module).  1 = one phase of PV-P75 (model-agreement test only); anything else stops.
BUILD = {1: (3, "PV-P75"), 3: (3, "PV-P75"), 4: (4, "PV-P100/110")}
# products on the drawn builds: phases, module_spec module, rated power key (PV-15: PV-P110 = 4 x PVCELL-27.5 = the
# 4-phase build's P_max as its rating)
PRODUCTS = {"PV-P75": (3, "PV-P75", "P_rated_kW"), "PV-P100": (4, "PV-P100/110", "P_rated_kW"),
            "PV-P110": (4, "PV-P100/110", "P_max_kW")}
sys.path.insert(0, HERE)
import pv_mppt as pvm  # noqa: E402  (PV array model + MPPT algorithm)

# ------------------------------------------------------------------------------------------------ assumptions (ours)
ASSUME = {
    "f_ci_Hz": (2500.0, "current-loop crossover target at L(45 A) (fixed gain, no scheduling)"),
    "ci_zero_ratio": (5.0, "current-loop PI zero = f_ci / this"),
    "f_cv_Hz": (300.0, "outer voltage-loop crossover target (port-current reference / that port's capacitance)"),
    "cv_zero_ratio": (6.0, "voltage-loop PI zero = f_cv / this (6 chosen over 4..10: +1.6 deg at the worst CPL corner)"),
    "k_ff_bus": (1.0, "feed-forward of the measured port current when that port is a DC bus (inverter = CPL); 0 for a "
                       "PV array or a battery (there it cancels the source's own damping / closes a unity loop)"),
    "mode_hyst": (0.02, "mode hysteresis in duty: leave band when the single-leg duty falls below D_max - this; sized "
                        ">= 2.5 x the dead-time residual of one leg (225 ns x 32 kHz = 0.0072 -> 0.018, rounded up) so "
                        "an unknown dead time cannot make the cell re-enter the mode it just left (asserted)"),
    "dt_range_ns": ((150.0, 600.0), "per-edge dead time at the gates: unknown to firmware, different per leg and per "
                                    "edge; covers the gate drive's 181-586 ns (cell_spec, asserted inside)"),
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
    "plim_trim_tau_s": (5e-3, "port-current limit: a slow integral trim on the MEASURED port current (I_A / I_B shunt) "
                              "corrects the conversion through the commanded duty, which the dead time biases by up to "
                              "t_d f_sw / D (~2 %)"),
    "ilim_slew_A_per_s": (2000.0, "slew of each cell's current limit: a mode change moves the phase limit (buck/boost <-> "
                                  "band) and the node-A power limit by 1/D_max - 1 = 5.3 % without kicking the current loop"),
    "t_conv_s": (0.3e-6, "F280039C conversion incl. sample and hold: 75 ns window + 11 ADCCLK at 60 MHz (SPRSP61C "
                         "6.13.3.2), rounded up"),
    "t_sh_s": (75e-9, "F280039C sample-and-hold window, the first part of t_conv_s (SPRSP61C 6.13.3.2)"),
    "adc_noise_lsb": (0.44, "ADC noise per sample incl. quantisation: F280039C ENOB 11.4 bits with an external VREFHI "
                            "(SPRSP61C 6.13.3.2.2) -> 2^0.6 / sqrt(12) LSB rms"),
    "desat_filter_ns": ((150.0, 265.0), "NSI6651 DESAT filter after the blanking (cell_spec protection desat_basis; "
                                        "sim/data/asia_drivers.md)"),
    "noise_pp_per_rms": (6.0, "peak-to-peak / rms of a sensor noise quoted as pp (+/-3 sigma): STK-HO/A 75 25 mVpp is "
                              "specified over DC-100 kHz; the PV-CTL AFE passes 331 kHz (noise above 100 kHz unknown)"),
    "body_v0_V": (2.9, "body-diode threshold, the lower of SG2M040170HJ (V_SD 3.7 V at 19 A, "
                       "p.6) and MSC035SMA170B4 (3.7 V at 30 A, p.4); assumption"),
    "body_r_ohm": (0.027, "body-diode slope through the MSC035SMA170B4 point (the steeper of the two; assumption)"),
    "aw_margin_A": (0.9, "anti-windup: a non-selected main loop may sit at most this far (per cell) above the selected one"),
    "vlim_qmax_A": (3.0, "V_B-max limit loop: integrator upper clamp (port amps).  With the load-current feed-forward "
                         "the candidate then rides this margin above the module's own current, so it neither winds up "
                         "nor closes a positive loop through a stiff bus, and is selected at once on a load rejection"),
    "f_isense_Hz": (200e3, "cell inductor-current sensor bandwidth REQUIRED (cell_spec minimum); the model uses the drawn "
                           "chain (PV-PWR sensor + PV-CTL AFE)"),
    "i_sense_fs_A": (100.0, "cell inductor-current range REQUIRED (cell_spec sensing)"),
    "res_il_req_A_per_LSB": (0.0759, "i_L resolution REQUIRED, kept from the control design of rev E (the boards were drawn "
                                     "to it); not binding: the drawn sensor's noise is about 30 counts pp"),
    "res_v_req_V_per_LSB": (0.638, "V_A / V_B resolution REQUIRED per outer-loop sample, kept from rev E (PV-CTL meets it "
                                   "with its 16-fold oversampling; the model samples once per outer step, 0.881 V/LSB)"),
    "res_ip_req_A_per_LSB": (0.258, "I_A / I_B resolution REQUIRED, kept from rev E"),
    "batt_R": (0.032, "battery/bus source resistance incl. loop (port_design A_RS_BATT 30 mOhm + A_R_LOOP 2 mOhm)"),
    "batt_L": (3.5e-6, "battery/bus source inductance incl. loop (port_design A_LS_BATT 3 uH + A_L_LOOP 0.5 uH)"),
    "L_mismatch": (0.10, "inductance mismatch between cells for the sharing study (+/-10 %, brief; A_L tolerance 8 %)"),
    "env_inlet_C": ((35.0, 45.0, 60.0), "inlet temperatures of the published envelope (PV-20: full power to 45 C; D-056: "
                                        "the 4-phase build to 35 C)"),
}


def A(k):
    return ASSUME[k][0]


# ------------------------------------------------------------------------------------------------ inputs (fail closed)
def need(path):
    """a generated input: stop if it is missing, naming the script that writes it"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"{os.path.relpath(path, ROOT)} is missing: run .venv/bin/python {MAKER[path]} first")
    return path


def grab(path, pat, conv=float):
    """the groups of the first match of pat in a generated design-check file; stop if the line is not there"""
    m = re.search(pat, open(need(path)).read())
    if not m:
        raise ValueError(f"{os.path.relpath(path, ROOT)} has no line matching {pat!r}: re-run {MAKER[path]} or update "
                         "this parser")
    return [conv(x) for x in m.groups()]


def drawn_chain(nb, cs, lean):
    """sensing, trips and banks of the drawn boards: PV-PWR(-4) for nb phases + PV-CTL.  JSON first: the trip bands and
    responses that cell_spec records for PV-CTL (sim/pv_design.py TRIP_HW / TRIP_BACKUP / OV_HW, which gen/pv_ctrl.py
    asserts equal to its own bands; centre = band midpoint) and the port OC band of port_spec lean; the design-check text
    for the rest.  Where a record and the check on disk differ, the record is used and the pair kept for the report."""
    pwr, ctl = PWR_CHECK[nb], CTL_CHECK
    d = dict(zip(("c_a", "c_b"), grab(pwr, r"port A ([\d.]+) uF incl\. decoupling[^\n]*?port B ([\d.]+) uF incl\. "
                                         r"decoupling")))
    d["sensor"], = grab(pwr, r"Inductor current: ([^\n]+?) per phase", str)
    d["lin_A"], d["gain_drift_pct"] = grab(pwr, r"linearity ([\d.]+) A \+ gain drift <= ([\d.]+) %")
    d["noise_mvpp"], d["noise_App"] = grab(pwr, r"Noise ([\d.]+) mVpp = ([\d.]+) A pp per sample")
    d["f_ip_kHz"], = grab(pwr, r"IA / IB bandwidth \(lean_shunt[^)]*\): ([\d.]+) kHz")
    d["desat_V"], d["desat_blank_ns"] = (grab(pwr, r"DESAT trips at V_DS ([\d.]+)-([\d.]+) V"),
                                         grab(pwr, r"DESAT trips at V_DS[^\n]*?blanking (\d+)-(\d+) ns"))
    d["il_mV_A"], d["lsb_il"], d["f_il_kHz"], d["t_il_us"] = grab(
        ctl, r"ADC IL1-4 \([^\n]*?\) - ([\d.]+) mV/A around[^\n]*?, ([\d.]+) A/LSB \(need[^\n]*?-3 dB ([\d.]+) kHz[^\n]*?"
             r"delay ([\d.]+) us")
    d["k_div"], d["v_fs"], d["lsb_v"], d["f_div_kHz"], d["f_adc_rc_MHz"] = grab(
        ctl, r"ADC VA / VB \(VMID \+ V/(\d+)\) - full scale (\d+) V[^\n]*?; ([\d.]+) V/LSB,[^\n]*?PV-PWR pole ([\d.]+) kHz"
             r"[^\n]*?this board's RC ([\d.]+) MHz")
    d["v_fs_neg"], = grab(ctl, r"ADC VAX / VBX / VPE \(bipolar around VMID\) - -(\d+)\.\.")
    d["lsb_ip"], = grab(ctl, r"IA ([\d.]+) A/LSB \(need")
    d["oc_local"] = grab(ctl, r"IL1-4 local window +\+([\d.]+)/-([\d.]+) A +band ([\d.]+)-([\d.]+) A +response ([\d.]+) us")
    d["oc_backup"] = grab(ctl, r"IL1-4 CMPSS backup +\+/-([\d.]+) A \(DAC\) +band ([\d.]+)-([\d.]+) A +response ([\d.]+) us")
    d["port_oc"] = grab(ctl, r"port OC \(PV-PWR, on FLT_N\) +\+/-([\d.]+) A +band ([\d.]+)-([\d.]+) A +response ([\d.]+) us")
    d["ov_hw"] = grab(ctl, r"port OV VA / VB +([\d.]+) V +band ([\d.]+)-([\d.]+) V +response ([\d.]+) us")
    d["ov_ppb"] = grab(ctl, r"port OV backup \(ADC PPB\) +([\d.]+) V +band ([\d.]+)-([\d.]+) V +response ([\d.]+) us")
    d["ppb_period_us"], d["ppb_lag_us"] = grab(ctl, r"converted every ([\d.]+) us: lag ([\d.]+) us")
    d["adc_conv"], d["adc_window_us"] = grab(ctl, r"ADC load - (\d+) conversions per ([\d.]+) us")
    hw, bk = cs["device_primary"]["trip_band_costfirst"]["hardware"], cs["device_primary"]["trip_band_costfirst"]["backup"]
    ov, po = cs["ov_trip_costfirst"], lean["port_oc_trip_A"]
    m = 0.5 * sum(hw["band_A"])
    rec = {"oc_local": [m, m, *hw["band_A"], hw["response_us"]],
           "oc_backup": [0.5 * sum(bk["band_A"]), *bk["band_A"], bk["response_us"]],
           "ov_hw": [0.5 * sum(ov["band_V"]), *ov["band_V"], ov["response_us"]],
           "port_oc": [0.5 * sum(po), *po, d["port_oc"][3]]}
    d["record_vs_check"] = {k: (d[k][-3:], v[-3:]) for k, v in rec.items()             # (check, record): band + response
                            if any(abs(a - b) > 1e-6 for a, b in zip(d[k][-3:], v[-3:]))}
    d.update(rec)
    d["ov_hw_lag_us"], d["ov_rc_us"] = d["ov_hw"][3], 1e3 / (2 * math.pi * d["f_div_kHz"])   # divider pole time constant
    return d


def load_params(N):
    """power stage, drawn boards and product limits -> parameter dict P (per cell unless noted); N selects the build"""
    if N not in BUILD:
        raise ValueError(f"no drawn build with {N} phases: PV-P75 = 3, PV-P100/110 = 4 (1 = one PV-P75 phase, model "
                         "tests only)")
    nb, mod = BUILD[N]
    cs, ms = json.load(open(need(CELL_SPEC))), json.load(open(need(MODULE_SPEC)))
    sw, ind, prot = cs["switching"], cs["inductor"], cs["protection"]
    fsw, td, dmax, tmin = sw["f_sw_kHz"] * 1e3, sw["dead_time_ns"] * 1e-9, sw["D_max"], sw["min_pulse_us"] * 1e-6
    td_gates = sw["dead_time_at_gates_ns"]
    lo_, hi_ = A("dt_range_ns")
    assert lo_ <= td_gates[0] and td_gates[1] <= hi_, ("gate dead time outside the control design range", td_gates)
    # inductor: the hand-off table's shape through the magnetics construction's three anchors (rev M2, D-039)
    tab = np.loadtxt(need(L_CSV), delimiter=",", skiprows=1)
    li, lv = tab[:, 0], tab[:, 1] * 1e-6
    e = json.load(open(need(MAG_SPEC)))["electrical"]
    i_dc, i_trip = ind["requirement"]["I_dc_max_A"], prot["inductor_overcurrent_hw_trip_A"]
    lv = lv * np.interp(li, [0.0, i_dc, i_trip], [e["L0_H"] / np.interp(0.0, li, lv), e["L_at_45A_nom_H"] / np.interp(
        i_dc, li, lv), e["L_trip_over_L0"] * e["L0_H"] / np.interp(i_trip, li, lv)])
    l0, i50, ltol = e["L0_H"], ind["I_at_50pct_L0_A"], ind["L_tolerance_pct"] / 100
    # product limits (module_spec: ratings; cost-first block: lean battery port, thermal derating vs inlet)
    mm, mc = ms["modules"][mod], ms["costfirst"]["modules"][mod]
    assert mm["cells"] == nb, (mod, mm["cells"], nb)
    temps, frac = mm["derating_fraction_of_P_max"]["inlet_C"], mc["derating_thermal_only_fraction"]
    assert len(temps) == len(frac)
    i_port = {"pv": mm["I_port_max_A"] * N / nb, "battery": mc["battery_port_limit_A"] * N / nb}
    lean = json.load(open(need(PORT_SPEC)))["lean"]
    ch = drawn_chain(nb, cs, lean)
    # inductor curve extended to the 50 %-of-L0 point and floored at 30 % of L0 (soft saturation, powder core)
    li2 = np.concatenate([li, [i50, 2 * i50]])
    lv2 = np.concatenate([lv, [0.5 * l0, max(0.3 * l0, 0.5 * l0 - (lv[-1] - 0.5 * l0) / (i50 - li[-1]) * i50)]])
    lstep = 0.05
    ltab = np.interp(np.arange(0, 400, lstep), li2, lv2).tolist()
    n_adc = A("adc_noise_lsb")
    P = dict(src=[os.path.relpath(p, ROOT) for p in (CELL_SPEC, L_CSV, MAG_SPEC, MODULE_SPEC, MODULE_GRID, PWR_CHECK[3],
                                                     PWR_CHECK[4], CTL_CHECK, PORT_SPEC)],
             N=N, build=nb, module=mod, fsw=fsw, T=1 / fsw, Ts=0.5 / fsw, td=td, Dmax=dmax, Dmin=tmin * fsw, tmin=tmin,
             R=cs["control_plant"]["R_series_mOhm"] * 1e-3,
             CA=ch["c_a"] * 1e-6 * N / nb, CB=ch["c_b"] * 1e-6 * N / nb,      # port banks incl. leg decoupling, as drawn
             Li=li2, Lv=lv2, ltab=ltab, lstep=lstep, L0=l0, i50=i50, Ltol=ltol, Lnom=float(np.interp(i_dc, li2, lv2)),
             i_trip=i_trip, v_ovp=prot["overvoltage_hw_trip_port_B_V"], v_ov_sw=prot["overvoltage_sw_limit_V"],
             v_uv=prot["undervoltage_stop_V"],
             # imax = the cell's share of the low-voltage port rating (a PORT current, not the inductor's);
             # i_phase_max = inductor mean limit where one leg switches with full ripple (buck / boost: the inductor's DC
             # design current, normal peak I_peak_normal); i_phase_max_band = inductor mean limit in the band, where both
             # legs sit near D_max with almost no ripple: the inductor's rms design value (cell_spec inductor I_rms_max =
             # the power stage's band point, P_lim at unity ratio)
             imax=cs["ratings"]["I_max_A_low_voltage_port"], pmax=cs["ratings"]["P_max_kW"] * 1e3,
             i_phase_max=i_dc, i_phase_max_band=ind["I_rms_max_A"],
             i_peak_normal=ind["requirement"]["I_peak_normal_max_A"],
             i_port_lim=i_port, p_frac_inlet=(temps, frac),
             tau_i=ch["t_il_us"] * 1e-6, tau_rc=1 / (2 * math.pi * ch["f_div_kHz"] * 1e3),
             tau_amc=1 / (2 * math.pi * ch["f_adc_rc_MHz"] * 1e6),
             tau_ip=1 / (2 * math.pi * ch["f_ip_kHz"] * 1e3) + 1 / (2 * math.pi * ch["f_adc_rc_MHz"] * 1e6),
             lsb_v=ch["lsb_v"], lsb_ip=ch["lsb_ip"], lsb_il=ch["lsb_il"], v_fs_adc=ch["v_fs"], v_fs_neg=ch["v_fs_neg"],
             sig_v=n_adc * ch["lsb_v"], sig_ip=n_adc * ch["lsb_ip"],          # buffer / shunt-amplifier noise << 1 count
             sig_il=math.hypot(ch["noise_App"] / A("noise_pp_per_rms"), n_adc * ch["lsb_il"]),
             sens_gain_err=ch["gain_drift_pct"] / 100, sens_off_err_A=ch["lin_A"], chain=ch, lean=lean,
             td_range=tuple(x * 1e-9 for x in A("dt_range_ns")), td_hat=A("dt_hat_ns") * 1e-9, td_gates=td_gates,
             device=cs["switch_positions"][0]["mpn"], driver=cs["gate_drive"]["driver"].split(" (")[0])
    assert P["i_phase_max_band"] >= P["i_phase_max"], "the band (low-ripple) limit is never below the buck / boost limit"
    return P


def port_limit(P, kind):
    """port-current limit of the build (lean port, flat in inlet temperature: module_spec cost-first)"""
    return P["i_port_lim"][kind]


def p_thermal(P, va, vb, t_inlet):
    """delivered power the module holds thermally at that inlet: module_spec's thermal-only derating fraction of the
    point's P_max (sim/pv_module.py holdable(): fans at 100 % or their cap, every trip band respected).  Not part of the
    simulated control: firmware derates on the NTC readings (PV-CTL firmware layer); the envelope applies it"""
    return float(np.interp(t_inlet, *P["p_frac_inlet"])) * P["N"] * min(P["pmax"], P["imax"] * min(va, vb))


def i_phase_limit(P, mode):
    """inductor mean-current limit by modulation mode (band: both legs near D_max, almost no ripple)"""
    return P["i_phase_max_band"] if mode == "band" else P["i_phase_max"]


def i_cell_max(P, va, vb, port_types=("pv", "battery")):
    """steady-state per-cell inductor-current limit at (va, vb): phase, node-A power, both port currents"""
    da, db, mode = duties_ss(P, va, vb)
    n = P["N"]
    return min(i_phase_limit(P, mode), P["pmax"] / max(da * va, 1.0), port_limit(P, port_types[0]) / (n * da),
               port_limit(P, port_types[1]) / (n * db))


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
    """controller gains (continuous design, verified on the exact sampled model in stability()); each port's voltage
    loop is scaled with that port's own bank (as drawn the banks differ: PV-P75 port B carries the 7th capacitor)"""
    N = N or P["N"]
    fci, fcv = A("f_ci_Hz"), A("f_cv_Hz")
    kp = 2 * math.pi * fci * P["Lnom"]
    ki = kp * 2 * math.pi * fci / A("ci_zero_ratio")
    kpv = {x: 2 * math.pi * fcv * P["C" + x] for x in "AB"}
    kiv = {x: kpv[x] * 2 * math.pi * fcv / A("cv_zero_ratio") for x in "AB"}
    return dict(kp=kp, ki=ki, kpv=kpv, kiv=kiv, hyst=A("mode_hyst"), aw=A("aw_margin_A"), delay=1,
                Ts=P["Ts"], Tv=P["T"], N=N, f_ci=fci, f_cv=fcv, t_soc=P["tau_i"], dt_slope=A("dt_slope_frac") * kp)


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
    il = i_cell_max(P, va, vb, sc.get("port_types", ("pv", "battery")))
    i0 = sc.get("i0", [0.0] * N)
    cells = [dict(DA=da, DB=db, DAp=da, DBp=db, mode=mode, q=0.0, q_f=0.0, u=0.0, i_mod=i0[k], on=False, fault=False,
                  td_hat=th[k], ilim=il, dta=0.0) for k in range(N)]
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
    c.update(DAp=da, DBp=db, mode=mode, u=ur, dta=ca if da < 1.0 else 0.0)   # dta: leg A's dead-time compensation [V]


def ctrl_outer(P, G, cs, sc, m, t):
    """outer loops (32 kHz): returns per-cell inductor-current references.  cmd = sc['cmd'](t, cs):
         ('I', i_cell)                       direct current command (BMS / test), clamped to the limits
         ('VA', v_ref, vb_max, allow_rev)    port-A voltage (MPPT / bus on A) with the port-B over-voltage limit loop
         ('VB', v_ref)                        port-B voltage (bus forming, bidirectional)
       min-selection of the candidate references with tracking anti-windup; per-cell limits: inductor (phase) current by
       mode, node-A power 27.5 kW, both port currents through the actual duty;
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
    types = sc.get("port_types", ("pv", "battery"))
    ia_lim, ib_lim = port_limit(P, types[0]), port_limit(P, types[1])
    p_lim = P["pmax"]
    cs["port_lim"] = (ia_lim, ib_lim)
    k_t = G["Tv"] / A("plim_trim_tau_s")
    tra, trb = cs.get("plim_trim", (1.0, 1.0))
    tra = min(max(tra + k_t * (ia_lim - abs(ia)) / ia_lim, 0.7), 1.0)
    trb = min(max(trb + k_t * (ib_lim - abs(ib)) / ib_lim, 0.7), 1.0)
    cs["plim_trim"] = (tra, trb)
    ia_lim, ib_lim = tra * ia_lim, trb * ib_lim
    for c in cells:                                                     # phase by mode, power over own v_A, ports
        tgt = min(i_phase_limit(P, c["mode"]), p_lim / max(c["DA"] * va - c["dta"], 1.0),   # node-A power: duty net of
                  ia_lim / (n * max(c["DA"], 0.05)), ib_lim / (n * max(c["DB"], 0.05)))   # the dead-time compensation
        c["ilim"] += min(max(tgt - c["ilim"], -step), step)
    lims = [c["ilim"] for c in cells]
    ilim = min(c["ilim"] for c in act) if act else P["i_phase_max"]
    cmd = sc["cmd"](t, cs)
    kind = cmd[0]
    if "i_init" in sc and not cs.get("init_done"):                      # start a run in steady state
        cs["init_done"] = True
        cs["qa"] = sc["i_init"] * n * da - sc.get("kff_a", 0.0) * ia
        cs["qb"] = sc["i_init"] * n * db - sc.get("kff_b", 0.0) * ib
    tv, dl = G["Tv"], G["aw"]
    (kpa, kia), (kpb, kib) = ((G["kpv"][x], G["kiv"][x]) for x in "AB")
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
        cand = {"VA": (ffa + kpa * ea + cs["qa"]) / (n * da)}
        if vbmax:
            eb = vbmax - vb
            ffb = kfb * ib
            cand["VBmax"] = (ffb + kpb * eb + cs["qb"]) / (n * db)
        sel = min(cand, key=cand.get)
        ir = min(max(cand[sel], lo), ilim)
        cs["sel"] = sel if lo < cand[sel] < ilim else ("ILIM" if cand[sel] >= ilim else "LO")
        cs["limited"] = cs["sel"] in ("ILIM", "VBmax")         # an upper limit holds the operating point (MPPT tracks)
        cs["qa"] += kia * tv * ea
        cs["qa"] = min(cs["qa"], (ir + dl) * n * da - ffa - kpa * ea)
        cs["qa"] = max(cs["qa"], (lo - dl) * n * da - ffa - kpa * ea)
        if vbmax:
            cs["qb"] = min(max(cs["qb"] + kib * tv * eb, -n * P["i_phase_max_band"]), A("vlim_qmax_A"))
        return [min(max(ir, -x if rev else 0.0), x) for x in lims]
    if kind == "VB":
        eb = cmd[1] - vb
        ffb = kfb * ib
        ub = (ffb + kpb * eb + cs["qb"]) / (n * db)
        ir = min(max(ub, -ilim), ilim)
        cs["sel"], cs["limited"] = ("VB" if ir == ub else "ILIM"), ir != ub
        cs["qb"] += kib * tv * eb
        cs["qb"] = min(max(cs["qb"], (-ilim - dl) * n * db - ffb - kpb * eb), (ilim + dl) * n * db - ffb - kpb * eb)
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


def trip_layers(P):
    """hardware trip layers as drawn (PV-CTL trip table, PV-PWR port comparator): (name, threshold, delay to gates off
    after the detected crossing).  oc: on the TRUE inductor current with PV-CTL's gates-off time from the crossing, sensor
    included (local window, CMPSS backup); ov: on the port voltage behind the PV-PWR divider pole and the PV-CTL RC, plus
    the rest of PV-CTL's lag (comparator: overdrive build-up + comparator + logic + driver taken as fixed - exact at
    PV-CTL's design slope, conservative on a steeper ramp; PPB backup: conversion period + chain); port: PV-PWR's port OC
    comparator on the port current behind the IM pole (its own IH pole is faster: conservative) + logic and driver"""
    c = P["chain"]
    rc = c["ov_rc_us"]
    return dict(oc=[("local", 0.5 * (c["oc_local"][0] + c["oc_local"][1]), c["oc_local"][4] * 1e-6),
                    ("CMPSS backup", c["oc_backup"][0], c["oc_backup"][3] * 1e-6)],
                ov=[("comparator", c["ov_hw"][0], (c["ov_hw_lag_us"] - rc) * 1e-6),
                    ("PPB backup", c["ov_ppb"][0], (c["ppb_lag_us"] - rc) * 1e-6)],
                port=[("port OC", c["port_oc"][0], c["port_oc"][3] * 1e-6)])


def body_v(P, N, I):
    """static reverse conduction of one port of the module at total current I >= 0 (bus- -> bus+): PV-PWR has no reverse
    clamp (D-044 gave up terminal-short survival), so each leg's two series body-diode positions (2 devices each) carry
    it.  Returns (V across the bank, A per body-diode device)."""
    v0, rb = A("body_v0_V"), A("body_r_ohm")
    v = 2 * v0 + rb * I / N
    return v, max(v - 2 * v0, 0.0) / rb / 2.0


def simulate(P, G, sc, t_end, switched=True, fine=None, h_max=None, seed=1):
    """time-domain simulation of N cells on shared port banks with the digital controller.
    sc keys: N, pa / pb (port dicts), va0, vb0, i0 (list), cmd(t, cs), t_en (gate enable), events [(t, fn(sim))],
             ltol (list, relative L error), sgain / soff (current-sensor errors), td (per cell 4 edge dead times [s]),
             hw (bool: hardware trips), layers (dict as trip_layers()),
             noise (bool), freeze_ref (t) optional: from t the controller output is frozen (protection-only studies).
    Returns a dict of logged arrays (one row per sampling slot) + fine waveforms between fine=(t0, t1)."""
    N = sc["N"]
    T, th = P["T"], P["Ts"]
    tdc = [tuple(x) for x in sc.get("td", [dt_of(DT_PLANT_FR[k % 4]) for k in range(N)])]
    rng = np.random.default_rng(seed)
    R, ca, cb = P["R"], sc.get("CA", P["CA"]), sc.get("CB", P["CB"])
    ltab, lstep = P["ltab"], P["lstep"]
    nl = len(ltab) - 1
    lfac = [1.0 + x for x in sc.get("ltol", [0.0] * N)]
    sgain, soff = sc.get("sgain", [0.0] * N), sc.get("soff", [0.0] * N)
    noise = sc.get("noise", True)
    hw = sc.get("hw", True)
    layers = sc.get("layers") or trip_layers(P)
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
    rev = {"a": [0.0, 0.0, 0.0, 0.0], "b": [0.0, 0.0, 0.0, 0.0]}   # I module, A per body device, its I2t, V reverse
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
    diode = {"a": 0.0, "b": 0.0}      # peak reverse (body diode) current per port, module total
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
            m_last = dict(va=quant(fva2, P["lsb_v"], P["sig_v"] * noise, rng, lo=-P["v_fs_neg"], hi=P["v_fs_adc"]),
                          vb=quant(fvb2, P["lsb_v"], P["sig_v"] * noise, rng, lo=-P["v_fs_neg"], hi=P["v_fs_adc"]),
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
                # a port bank cannot reverse beyond the legs' body diodes, which carry the external branch's
                # freewheeling current (static sharing, body_v)
                for port, v_, ie_, pp, sx in (("a", va, iea, pa, sA2), ("b", vb, ieb, pb, -sB2)):
                    if v_ < 0.0:
                        ic_ = max(sx - ext_cur(pp, v_, ie_)[0], 0.0)
                        vcl, i_body = body_v(P, N, ic_)
                        rv = rev[port]
                        rv[0], rv[1], rv[3] = max(rv[0], ic_), max(rv[1], i_body), max(rv[3], vcl)
                        rv[2] += i_body ** 2 * h
                        diode[port] = rv[0]
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
                # hardware trip layers (as drawn, trip_layers): detection -> gates off after the layer's delay; latching
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
                                  quant(fva2, P["lsb_v"], P["sig_v"] * noise, rng, lo=-P["v_fs_neg"], hi=P["v_fs_adc"]),
                                  quant(fvb2, P["lsb_v"], P["sig_v"] * noise, rng, lo=-P["v_fs_neg"], hi=P["v_fs_adc"]))
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
    lg["reverse"] = rev
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
    op = dict(N=N, va=va, vb=vb, pw=pw, i=i, da=da, db=db, mode=mode, L=L_of(P, i), CA=P["CA"] * N / P["N"],
              CB=P["CB"] * N / P["N"], use=use, ia=N * da * i, ib=N * db * i, g_rel=g_rel)
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
    N, L, R, CA, CB = op["N"], op["L"], P["R"], op["CA"], op["CB"]
    I, VA, VB, DA, DB = op["i"], op["va"], op["vb"], op["da"], op["db"]
    Am, Bm = np.zeros((NX, NX)), np.zeros((NX, 2))
    Am[0, 0], Am[0, 1], Am[0, 2], Bm[0, 0], Bm[0, 1] = -R / L, DA / L, -DB / L, VA / L, -VB / L
    Am[1, 3], Am[1, 0], Bm[1, 0] = 1 / CA, -N * DA / CA, -N * I / CA
    Am[2, 4], Am[2, 0], Bm[2, 1] = 1 / CB, N * DB / CB, N * I / CB
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
    Inner loop at Ts (sample at t_k + t_soc, G['delay'] samples of computation delay, ZOH); outer loop at Tv = 2 Ts,
    lifted.  Returns dict with frequency grids, L_i, L_v (or None), closed-loop spectral radii."""
    Ts, Tv, N = G["Ts"], G["Tv"], op["N"]
    Am, Bm = lin_plant(P, op)
    Phi, Gam = zoh(Am, Bm, Ts)
    Phit, Gamt = zoh(Am, Bm, G["t_soc"])
    mu, ma, mb = mod_sens(P, G, op)
    nd = 2 * G["delay"]
    n = NX + nd + 1                                 # X = [x, D applied (2), D pending (2, delay 2 only), q]
    law = slice(NX + nd - 2, NX + nd)               # the duty computed from this sample
    Sy = np.zeros((NX, n))                          # plant state at the sampling instant t_k + t_soc
    Sy[:, :NX], Sy[:, NX:NX + 2] = Phit, Gamt
    yi, yva, yvb = Sy[5], Sy[7], Sy[9]
    eq = np.zeros(n)
    eq[n - 1] = 1.0
    kp, ki = G["kp"] + G["ki"] * Ts, G["ki"]          # ctrl_cell integrates before use: C(z) = kp + ki Ts z/(z-1)
    Acl, Bcl = np.zeros((n, n)), np.zeros(n)
    Acl[:NX, :NX], Acl[:NX, NX:NX + 2] = Phi, Gam
    if nd == 4:                                     # the pending duty is applied one sample later
        Acl[NX:NX + 2, NX + 2:NX + 4] = np.eye(2)
    Acl[law] = np.outer(mu, -kp * yi + eq) + np.outer(ma, yva) + np.outer(mb, yvb)
    Bcl[law] = mu * kp
    Acl[n - 1] = eq - ki * Ts * yi
    Bcl[n - 1] = ki * Ts
    Aol = Acl.copy()
    Aol[law] = np.outer(ma, yva) + np.outer(mb, yvb)
    Bol = np.zeros(n)
    Bol[law] = mu
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
    port = op["outer"][1]                           # 'A' / 'B': that port's voltage-loop gains
    Al = np.zeros((m, m))
    Al[:n, :n] = A2 + np.outer(AB, Kc)
    Al[n, :n] = G["kiv"][port] * Tv * sig * ev
    Al[n, n] = 1.0
    Bl = np.zeros(m)
    Bl[:n] = AB * b
    Cl = np.zeros(m)
    Cl[:n] = kff * ef + G["kpv"][port] * sig * ev
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
            plim = N * i_cell_max(P, va, vb) * duties_ss(P, va, vb)[0] * va      # at the control's limits (envelope)
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
    assert G["delay"] == 1, "lin_step models the one-sample computation delay only"
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
    port = op["outer"][1]
    bq = np.zeros(m)
    bq[n] = -G["kiv"][port] * G["Tv"] * sig
    Z, y = np.zeros(m), []
    for _ in range(nsteps):
        y.append(Z[vrow])
        rp = Cl @ Z - G["kpv"][port] * sig
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
    i0 = math.copysign(min(abs(i_cmd), i_cell_max(P, v0, vb)), i_cmd)
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
    """V_A swept through V_B in both directions, both power directions, at full current (V_B 550 V: the phase limit steps
    45 -> 47.7 -> 45 A through the band) and at full power (V_B 800 / 860 V), for every dead-time set (each cell of a set
    has its own per-edge dead times); the command is the band phase limit, the cells' own limits clamp it"""
    cases = []
    ib = P["i_phase_max_band"]
    for name in DT_SETS_FR:
        for vb, (lo, hi) in ((550.0, (450.0, 650.0)), (800.0, (690.0, 900.0)), (860.0, (740.0, 1000.0))):
            for i_cmd in (ib, -ib):
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


def study_limits(P):
    """averaged-model check that each limit holds where it binds (command ramped 30 A -> 1.2 x the band phase limit in
    5 ms): PV-P75 at 550/550 V (band: the phase limit 47.7 A and both 135 A ports bind together), PV-P75 at 650/650 V
    (node-A power 27.5 kW per cell), PV-P100/110 battery port (lean 145 A)"""
    checks = []
    for N, va, vb, what in ((3, 550.0, 550.0, "phase"), (3, 650.0, 650.0, "power"), (4, 800.0, 600.0, "port B")):
        Pn = load_params(N)
        Gn = design(Pn, N)
        i1 = 1.2 * Pn["i_phase_max_band"]
        sc = ss_sc(Pn, N, va, vb, 30.0, cmd=lambda t_, cs, i1=i1: ("I", 30.0 + (i1 - 30.0) * min(max((t_ - 2e-3) / 5e-3, 0.0),
                                                                                            1.0)), noise=False)
        lg = simulate(Pn, Gn, sc, 30e-3, switched=False)
        k, kp = lg["t"] > 25e-3, lg["t"] > 2e-3
        got = {"phase": np.mean(lg["i"][k]), "power": np.mean(lg["va"][k] * lg["iea"][k]) / N,
               "port A": np.mean(np.abs(lg["iea"][k])), "port B": np.mean(np.abs(lg["ieb"][k]))}
        lim = {"phase": i_phase_limit(Pn, duties_ss(Pn, va, vb)[2]), "power": Pn["pmax"], "port A": port_limit(Pn, "pv"),
               "port B": port_limit(Pn, "battery")}
        pk = {"phase": np.max(lg["i"][kp]), "power": np.max(lg["va"][kp] * lg["iea"][kp]) / N,
              "port A": np.max(np.abs(lg["iea"][kp])), "port B": np.max(np.abs(lg["ieb"][kp]))}
        checks.append(dict(N=N, va=va, vb=vb, what=what, value=float(got[what]), limit=float(lim[what]),
                           peak=float(pk[what]), over=max(float(got[x] / lim[x]) for x in got)))
    return checks


ENV_POINTS = ((250.0, 1000.0), (500.0, 1000.0), (550.0, 550.0), (600.0, 600.0), (611.0, 611.0), (650.0, 650.0),
              (750.0, 750.0), (950.0, 950.0))
_LOSS = {}


def module_eff(n):
    """eta(va, vb, t_inlet) of the whole module at the point's P_lim: sim/pv_module.py's published grid (module_envelope.csv:
    cells by the sim/pv_design.py loss model, port conduction, aux supply, fans; 25 C inlet), bilinear in (V_A, V_B), less
    the largest 25 -> 45 C drop of module_spec's corners per kelvin above 25 C (linear beyond 45 C: ASSUMED).  Used for both
    power directions (the grid is A->B; the cell loss model is close to symmetric)"""
    if n not in _LOSS:
        rows = [r for r in csv.DictReader(open(need(MODULE_GRID))) if int(r["cells"]) == n]
        va, vb = sorted({float(r["va"]) for r in rows}), sorted({float(r["vb"]) for r in rows})
        g = np.full((len(va), len(vb)), np.nan)
        for r in rows:
            g[va.index(float(r["va"])), vb.index(float(r["vb"]))] = float(r["eff"])
        assert not np.isnan(g).any(), "module efficiency grid incomplete"
        f = sip.RegularGridInterpolator((va, vb), g)
        m = json.load(open(need(MODULE_SPEC)))["modules"][BUILD[n][1]]
        k = max(m["efficiency_corners_25C"][c] - m["efficiency_corners_45C"][c] for c in m["efficiency_corners_45C"]) / 20.0
        _LOSS[n] = lambda x, y, t: float(f((x, y))) - k * max(t - 25.0, 0.0)
    return _LOSS[n]


def envelope_point(P, va, vb, dirn, t_in, i_band=None):
    """steady state the control law of this script allows at (va, vb), power direction dirn ('A->B' / 'B->A'), inlet
    t_in: source-port power P_src, delivered power P_del = eta P_src and the binding limit.  Limits: inductor current by
    mode (i_band overrides the band value: 45 A = the earlier law), node-A power 27.5 kW per cell (node A = the source
    for A->B, the sink for B->A), port A (PV) and port B (battery) currents of the lean ports, and the thermal derating
    of module_spec on the delivered power (its own definition).  The phase-current limit is a node-A power too
    (N D_A V_A i_L): source power for A->B, delivered power for B->A"""
    n, eta = P["N"], module_eff(P["build"])(va, vb, t_in)
    da, db, mode = duties_ss(P, va, vb)
    iph = i_phase_limit(P, mode) if i_band is None or mode != "band" else i_band
    ph, pc, th = n * da * va * iph, n * P["pmax"], p_thermal(P, va, vb, t_in)
    ia, ib = port_limit(P, "pv") * va, port_limit(P, "battery") * vb
    lim = ({"phase current": ph, "node-A power": pc, "port A current": ia, "port B current": ib / eta}
           if dirn == "A->B" else
           {"phase current": ph / eta, "node-A power": pc / eta, "port B current": ib, "port A current": ia / eta})
    lim["thermal"] = th / eta
    p_src = min(lim.values())
    key = " = ".join(k for k, x in lim.items() if x <= 1.0005 * p_src)        # equal limits are all named
    return dict(mode=mode, p_src=p_src, p_del=eta * p_src, eta=eta, binding=key)


def v_unity_for(P, p_del, dirn, t_in):
    """lowest V_A = V_B at which the module delivers p_del (bisection; None if not even at 1000 V)"""
    if envelope_point(P, 1000.0, 1000.0, dirn, t_in)["p_del"] < p_del:
        return None
    lo, hi = 250.0, 1000.0
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if envelope_point(P, mid, mid, dirn, t_in)["p_del"] >= p_del else (mid, hi)
    return hi


def study_envelope():
    """delivered-power envelope per product (PV-P75, PV-P100, PV-P110), both directions, at the published inlets"""
    out = {}
    for name, (n, mod, key) in PRODUCTS.items():
        Pn = load_params(n)
        assert Pn["module"] == mod
        rated = json.load(open(need(MODULE_SPEC)))["modules"][mod][key] * 1e3
        rows = []
        for va, vb in ENV_POINTS:
            for dirn in ("A->B", "B->A"):
                r = dict(va=va, vb=vb, dir=dirn)
                for t in A("env_inlet_C"):
                    e = envelope_point(Pn, va, vb, dirn, t)
                    r.update({f"p_del_kW_{t:.0f}C": e["p_del"] / 1e3, f"binding_{t:.0f}C": e["binding"]})
                    if t == 45.0:
                        r.update(mode=e["mode"], p_src_kW_45C=e["p_src"] / 1e3, eta_45C=e["eta"],
                                 p_src_kW_45A_law=envelope_point(Pn, va, vb, dirn, t, i_band=Pn["i_phase_max"])["p_src"] / 1e3)
                r["rated_met_45C"] = r["p_del_kW_45C"] * 1e3 >= rated - 1.0
                rows.append(r)
        v_min = {d: {f"{t:.0f}C": v_unity_for(Pn, rated, d, t) for t in A("env_inlet_C")} for d in ("A->B", "B->A")}
        out[name] = dict(N=n, module=mod, rated_kW=rated / 1e3, i_port_lim_A=dict(Pn["i_port_lim"]), rows=rows,
                         v_unity_min_for_rated_V=v_min,
                         thermal_fraction=dict(zip([f"{t:.0f}C" for t in Pn["p_frac_inlet"][0]], Pn["p_frac_inlet"][1])))
    return out


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
    i0 = math.copysign(min(abs(i_cmd), i_cell_max(P, v0, vb)), i_cmd)
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
        for label, ge, oe in (("calibrated", Pn["sens_gain_err"], Pn["sens_off_err_A"]), ("uncalibrated", 0.02, 1.0)):
            sg = [ge, -ge, ge, -ge][:N]
            so = [oe, -oe, -oe, oe][:N]
            for va, vb in ((950.0, 550.0), (550.0, 950.0)):
                i1 = i_cell_max(Pn, va, vb)
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


def study_startup(P, G):
    """start-up of the cost-first build (D-044 / D-045): K_A closes the PV array straight onto the port-A bank (no PV
    precharge; the array is a current source - the closing transient of the array's own capacitance is the port design's
    open item, port_spec lean pv_make); port B precharges from the battery through the lean resistor until the hardware dV
    interlock lets K_B close (port design's figures, port_spec lean precharge); the gates start at a carrier zero with the
    feed-forward duties; MPPT from V_oc with a reference ramp"""
    N = P["N"]
    arr = pvm.array(17, 8)
    g0, tc = 300.0, 25.0
    pa = port_pv(arr, g0, tc)
    vbat = 800.0
    voc = float(pvm.array_curve(arr, g0, tc)[0][-1])
    h, t, v, ts, vs = 20e-6, 0.0, 0.0, [], []
    while v < 0.99 * voc:                                # the array charges the bank (Heun-free: I(v) only)
        ts.append(t)
        vs.append(v)
        v += h * ext_cur(pa, v, 0.0)[0] / P["CA"]
        t += h
    pre_b = P["lean"]["precharge"]["per_phases"][str(P["build"])]
    # converter start: sequence time origin = both contactors closed + 20 ms (precharge relay open)
    prm = dict(pvm.MPPT)
    sc = dict(N=N, pa=pa, pb=port_bat(vbat), va0=voc - 0.5, vb0=vbat, i0=[0.0] * N, t_en=2e-3, kff_a=0.0, kff_b=0.0,
              vref=voc, vr=voc, cmd=lambda t, cs: ("VA", sc["vr"], 1010.0, False))
    sc["on_outer"] = mppt_hook(prm)
    lg = simulate(P, G, sc, 0.60, switched=False)
    vm, im, pm = pvm.mpp(arr, g0, tc)
    k = lg["t"] > 0.45
    p_end = float(np.mean(lg["va"][k] * lg["iea"][k]))
    j_en = np.searchsorted(lg["t"], 2e-3)
    return dict(pv_charge=(np.array(ts), np.array(vs), t), pre_b=pre_b, dv_band=P["lean"]["precharge"]["dV_enable_V"],
                r_pre=P["lean"]["precharge"]["R_ohm"], voc=voc, vbat=vbat, lg=lg, p_end=p_end, pmpp=pm, vmpp=vm,
                eta_start=p_end / pm, i_after_enable=float(np.max(np.abs(lg["i"][j_en:j_en + 60]))))


def stability(P, N):
    Pn = load_params(N) if N != P["N"] else P
    Gn = design(Pn, N)
    g = pv_g_rel()
    rows = stability_sweep(Pn, Gn, N, g)
    # inductance robustness of the current loop: L(i) at 0 A, at the 45 A design point and at the trip, times the
    # catalogue +/-8 % and the brief's +/-10 %; with the one-sample computation delay of the design and with two (the
    # F280039C timing question, architecture R-14)
    lrob = []
    fi = np.logspace(1, math.log10(0.499 / Gn["Ts"]), 300)
    for delay in (1, 2):
        Gd = dict(Gn, delay=delay)
        for fac in (0.90, 0.92, 1.0, 1.08, 1.10):
            for va, vb, pw in ((250.0, 1000.0, N * Pn["imax"] * 250.0), (1000.0, 250.0, N * Pn["imax"] * 250.0),
                               (550.0, 550.0, N * Pn["imax"] * 550.0)):
                for i_at in (0.0, Pn["i_phase_max"], Pn["i_trip"]):
                    op = op_point(Pn, N, va, vb, pw, "cur")
                    op["L"] = fac * float(np.interp(i_at, Pn["Li"], Pn["Lv"]))
                    r = lin_loops(Pn, Gd, op, fgrid=fi)
                    m = margins(r["f"], r["Li"])
                    lrob.append(dict(delay=delay, fac=fac, i_at=i_at, va=va, vb=vb, L_uH=op["L"] * 1e6, pm=m["pm"],
                                     gm=m["gm_db"], fc=m["fc"], rho=r["rho_i"]))
    # the same computation-delay question over every current-loop corner of the sweep (L(i) at the operating point)
    dly = {}
    for delay in (1, 2):
        mm = [margins(*[lin_loops(Pn, dict(Gn, delay=delay), op_point(Pn, N, va, vb, pw, "cur"), fgrid=fi)[k]
                        for k in ("f", "Li")]) for use, va, vb, pw, *_ in corner_cases(Pn, N, g) if use == "cur"]
        dly[delay] = dict(pm=(min(x["pm"] for x in mm), max(x["pm"] for x in mm)), gm=min(x["gm_db"] for x in mm),
                          fc=(min(x["fc"] for x in mm), max(x["fc"] for x in mm)), cases=len(mm))
    # the same full-load CPL corners (either port) without the load-current feed-forward
    noff = []
    fgv = np.logspace(0, math.log10(0.499 / Gn["Tv"]), 300)
    for use, sgn in (("cvb_cpl", 1.0), ("cva_cpl", -1.0)):
        for va in VCORNERS:
            for vb in VCORNERS:
                p = N * i_cell_max(Pn, va, vb) * duties_ss(Pn, va, vb)[0] * va
                r = lin_loops(Pn, Gn, op_point(Pn, N, va, vb, sgn * p, use), kff=0.0, fgrid=fgv)
                noff.append(dict(use=use, va=va, vb=vb, rho=r["rho_v"], stable=r["rho_v"] < 1.0,
                                 pm=margins(r["fv"], r["Lv"])["pm"] if r["rho_v"] < 1.0 else None))
    # numbers quoted in the report notes: the worst constant-power-load corner (either port)
    worst = min((r for r in rows if r["use"] in ("cvb_cpl", "cva_cpl")), key=lambda r: r["pm_v"])
    op = op_point(Pn, N, worst["va"], worst["vb"], worst["p_kW"] * 1e3, worst["use"])
    on_a = worst["use"] == "cva_cpl"
    C, v_c = (op["CA"], worst["va"]) if on_a else (op["CB"], worst["vb"])
    fg = np.logspace(0, math.log10(0.499 / Gn["Tv"]), 400)
    pm_asis = margins(*[lin_loops(Pn, Gn, op, kff=A("k_ff_bus"), fgrid=fg)[k] for k in ("fv", "Lv")])["pm"]
    P20 = dict(Pn, tau_rc=1 / (2 * math.pi * 20e3))
    pm_20k = margins(*[lin_loops(P20, Gn, op, kff=A("k_ff_bus"), fgrid=fg)[k] for k in ("fv", "Lv")])["pm"]
    rz = [r for r in rows if r["use"] == "cvb_cpl" and r["va"] == 250.0 and r["vb"] == 1000.0 and r["load"] == "full"
          and r["p_kW"] > 0][0]
    i45 = Pn["i_phase_max"]
    f_rhpz = 250.0 / (2 * math.pi * L_of(Pn, i45) * i45)
    pw = abs(worst["p_kW"]) * 1e3
    notes = dict(C=C, port="A" if on_a else "B", v_cpl=v_c, i_cpl=pw / v_c, f_cpl=pw / v_c ** 2 / C / (2 * math.pi),
                 pm_rc_asis=pm_asis, pm_rc_20k=pm_20k, f_rhpz=f_rhpz, fc_rhpz=rz["fc_v"],
                 ph_rhpz=math.degrees(math.atan(rz["fc_v"] / f_rhpz)), worst=worst)
    red = {d: [reduced_pm(Pn, Gn, f, d) for f in (0.92, 1.0, 1.08)] for d in (1, 2)}
    return dict(rows=rows, lrob=lrob, dly=dly, red=red, noff=noff, G=Gn, P=Pn, g_rel=g, notes=notes)


def reduced_pm(P, G, fac, delay):
    """the review's reduced current loop (PCM-02), for comparison with lin_loops: 1/(sL + R) with an exact ZOH, the PI,
    `delay` samples of computation delay and the drawn i_L chain as one UNcompensated lag (no sampling offset, ideal
    ports, no modulator or feed-forward paths).  Returns (phase margin [deg], crossover [Hz]) at L(45 A) x fac"""
    ts, lh, tau = G["Ts"], fac * P["Lnom"], P["tau_i"]
    f = np.logspace(2, math.log10(0.499 / ts), 4000)
    z = np.exp(2j * math.pi * f * ts)
    a = math.exp(-P["R"] * ts / lh)
    lz = ((G["kp"] + G["ki"] * ts * z / (z - 1)) * (1 - a) / (P["R"] * (z - a)) * z ** -delay /
          (1 + 2j * math.pi * f * tau))
    k = int(np.argmin(np.abs(np.abs(lz) - 1.0)))
    return 180.0 + math.degrees(np.angle(lz[k])), float(f[k])


def v_dev_limit(P):
    """port voltage at the last switching event: 0.85 V_DSS with the turn-off ringing (cell_spec) scaled with V"""
    w = json.load(open(need(CELL_SPEC)))["worst_case_stresses"]
    return 0.85 * w["device_VDS_rating_V"] / (1 + w["turnoff_overshoot_V"] / P["v_ovp"]), w["turnoff_overshoot_V"]


def ov_case(P, G, kind, layers, switched=False):
    """port B opens while the control is frozen (inner current loops alive): kind 'power' = 82.5 kW from the PV array
    at the power limit with V_B 1000 V; 'current' = the cells at their limit (45 A, 135 A at port B) from a stiff port A
    at 1000 V with V_B 611 V, the steepest crossing of 1100 V.  Returns the log (layers = trip layers in force)."""
    N = P["N"]
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
        va0, vb0 = 1000.0, 611.0
        i_op = i_cell_max(P, va0, vb0)
        pa, cmd = None, (lambda t, cs: ("I", i_op))

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


def short_case(P, G, case, variant, layers):
    label, va, vb, port, sgn = case
    i0 = sgn * i_cell_max(P, va, vb)

    def short(sim, port=port):
        sim["sc"][port] = dict(kind="bat", V=0.0, R=5e-3, L=1.0e-6)
    sc = ss_sc(P, P["N"], va, vb, i0, cmd=lambda t, cs, i0=i0: ("I", i0), events=[(0.1e-3, short)], hw=layers is not None,
               layers=layers, uv_check=False, noise=False)
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
    # the inductor current when each cell's gates go off (what the trip timing decides); with no reverse clamp a
    # reversed bank can drive it further up afterwards through the body diodes (i_peak, the whole window)
    row["i_at_off"] = max((abs(float(np.interp(x, fw["t"], fw["i"][:, k]))) for k, x in enumerate(lg["trip_at"])
                           if x is not None), default=None)
    if layers is None:                       # no trip: rise times from each layer's upper band edge to L = 50 % L0
        t_ = fw["t"]
        ch = P["chain"]
        for key, thr in (("local", ch["oc_local"][3]), ("backup", ch["oc_backup"][2]), ("nominal", P["i_trip"])):
            if ii.max() > P["i50"]:
                kc = int(np.argmax(ii > thr))
                ks = int(np.argmax(ii > P["i50"]))
                row["t_" + key + "_to_sat_us"] = float(t_[ks] - t_[kc]) * 1e6
                row["didt_" + key] = (P["i50"] - thr) / row["t_" + key + "_to_sat_us"]
            else:
                row["t_" + key + "_to_sat_us"] = None
    rv = lg["reverse"][port[1]]
    row.update(rev_module_A=rv[0], body_dev_A=rv[1], body_I2t=rv[2], v_reverse=rv[3])
    return row


def study_protection(P, G):
    out = {}
    N = P["N"]
    ch = P["chain"]
    lay = trip_layers(P)
    v_lim = v_dev_limit(P)[0]
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
    # ---- P2 over-voltage protection only (control frozen): requirement curve (an ideal 1100 V detector on the measured
    # port voltage + logic and driver + added delay) and the drawn layers at their band edges and nominal
    t0 = ch["port_oc"][3] * 1e-6                          # comparator edge -> gates off: logic + driver (PV-CTL)
    ov = {}
    for kind in ("power", "current"):
        rows = []
        for extra in (0.0, 20e-6, 50e-6, 100e-6, 150e-6, 200e-6, 300e-6):
            r = ov_case(P, G, kind, dict(oc=lay["oc"], port=[], ov=[("1100 V + delay", P["v_ovp"], t0 + extra)]))
            r["extra_us"] = extra * 1e6
            rows.append(r)
        built = []
        for path, band, dly in (("comparator", ch["ov_hw"][:3], lay["ov"][0][2]),
                                ("PPB backup (comparator failed)", ch["ov_ppb"][:3], lay["ov"][1][2])):
            for thr in (band[1], band[2], band[0]):
                r = ov_case(P, G, kind, dict(oc=lay["oc"], port=[], ov=[(path, thr, dly)]))
                r.update(path=path, thr=thr)
                built.append(r)
        ov[kind] = dict(curve=rows, built=built)
    out["ov"] = ov
    # smallest port-B bank for which the frozen-control load rejection keeps the device rule with the comparator at its
    # upper band edge (both cases); module_spec cost-first port_film_bank B 'load_rejection' is the power-stage figure
    cb = {}
    for kind in ("power", "current"):
        lo, hi = 50e-6, 1000e-6
        layer = dict(oc=lay["oc"], port=[], ov=[("comparator", ch["ov_hw"][2], lay["ov"][0][2])])
        for _ in range(14):
            mid = 0.5 * (lo + hi)
            lo, hi = (lo, mid) if ov_case(dict(P, CB=mid), G, kind, layer)["v_off"] <= v_lim else (mid, hi)
        cb[kind] = hi
    out["cb_needed"] = cb
    # ---- P3 short circuits: terminal (all layers), bank (port shunt blind: local OC, then the CMPSS backup), no trip
    variants = [("terminal short, all trips as drawn", lay),
                ("bank short, local OC at its upper band", dict(oc=[("local", ch["oc_local"][3], lay["oc"][0][2])],
                                                               port=[], ov=lay["ov"])),
                ("bank short, local failed: CMPSS backup at its upper band", dict(oc=[("CMPSS backup", ch["oc_backup"][2],
                                                                                       lay["oc"][1][2])], port=[], ov=lay["ov"])),
                ("no trip", None)]
    out["shorts"] = [short_case(P, G, case, vname, lays) for case in SHORT_CASES for vname, lays in variants]
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
    average at the capacitor node (the ADC sees it behind the PV-PWR divider pole)"""
    out = []
    for va, vb in ((950.0, 550.0), (550.0, 950.0), (800.0, 800.0)):
        da, db, _ = duties_ss(P, va, vb)
        i = i_cell_max(P, va, vb)
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
    C = P["CA"] / P["N"]                         # one phase's share of the port-A bank

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
    tpc, vpc, t_pv = su["pv_charge"]
    pb = su["pre_b"]
    tt = np.linspace(0.0, pb["t_done"], 200)
    axs[0].plot(tpc * 1e3, vpc, label=f"port A bank charged by the array through K_A (no PV precharge): {t_pv*1e3:.1f} ms")
    axs[0].plot(tt * 1e3, su["vbat"] * (1 - np.exp(-tt / pb["tau"])), label=f"port B precharge via {su['r_pre']:.0f} ohm "
                f"(port design: tau {pb['tau']*1e3:.0f} ms)")
    axs[0].axhline(su["voc"], color="k", lw=0.4)
    axs[0].set_xlabel("ms after the contactor / precharge relay closes")
    axs[0].set_ylabel("V")
    axs[0].legend(fontsize=7)
    axs[0].set_title(f"cost-first ports: K_B closes inside the dV interlock (inrush {pb['i_pk']:.0f} A, port design)", fontsize=9)
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
    ax.axhline(P["v_ov_sw"], color="k", ls=":", lw=0.7, label=f"firmware OV {P['v_ov_sw']:.0f} V")
    ax.set_ylabel("V")
    ax2 = ax.twinx()
    ax2.plot(lg["t"] * 1e3, lg["im"].mean(axis=1), "b", lw=0.8, label="i_L")
    ax2.set_ylabel("A")
    ax.legend(fontsize=7, loc="center right")
    ax.set_title(f"load rejection at 82.5 kW, V_B=1000 V (bus opens at 2 ms), MPPT + V_B-max limit: peak "
                 f"{pr['rej_ctrl']['vb_peak']:.0f} V", fontsize=8)
    ax = axs[0, 1]
    rq = R["req"]
    for kind, col, lab in (("power", "C0", "(a) 82.5 kW"), ("current", "C3", "(b) frozen at the limit")):
        d = pr["ov"][kind]
        ax.plot([r["t_resp_us"] for r in d["curve"]], [r["v_off"] for r in d["curve"]], "-", color=col, lw=1,
                label=f"{lab}: ideal 1100 V detector + delay")
        for b in d["built"]:
            if b["t_resp_us"] is not None:
                cmp_ = b["path"] == "comparator"
                ax.plot(b["t_resp_us"], b["v_off"], "o" if cmp_ else "^", color=col, ms=5, mfc=col if cmp_ else "none")
    ax.axhline(rq["v_dev_lim"], color="r", ls="--", lw=0.8, label=f"device rule {rq['v_dev_lim']:.0f} V")
    ax.plot([], [], "ko", label="comparator (band edges, nominal)")
    ax.plot([], [], "k^", mfc="none", label="PPB backup alone")
    ax.set_xlabel("gates off after the true 1100 V crossing [us]")
    ax.set_ylabel("V at gates off [V]")
    ax.legend(fontsize=6)
    ax.set_title(f"over-voltage, control frozen: PV-CTL trip paths, port B {P['CB']*1e6:.0f} uF", fontsize=8)
    ax = axs[0, 2]
    for r in pr["shorts"]:
        if r["variant"].startswith("bank short, local OC"):
            fw = r["fw"]
            ax.plot((fw["t"] - r["t_short"]) * 1e6, np.max(np.abs(fw["i"]), axis=1), lw=0.8, label=r["label"][:40])
    ax.axhline(P["chain"]["oc_local"][3], color="k", ls=":", lw=0.7, label="local window upper band")
    ax.axhline(P["i50"], color="r", ls="--", lw=0.7, label="L = 50 % L0")
    ax.set_xlim(-5, 90)
    ax.set_xlabel("us after the short")
    ax.set_ylabel("max |i_L| of the cells [A]")
    ax.legend(fontsize=6)
    ax.set_title(f"bank shorts (port shunt blind): local OC at its upper band, {P['chain']['oc_local'][4]:.2f} us", fontsize=8)
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


def band_peak(P, edges):
    """largest normal peak of the inductor current while a cell is in band mode at its band limit, from the exact
    piecewise-linear ripple at the minimum inductance (L(i) - tolerance): V 250-1000 V, V_B / V_A over the band as the
    transition sweeps occupy it (from the lowest band->buck to the highest band->boost exit, i.e. with the hysteresis and
    the dead time, not only D_max .. 1/D_max), band duties of cell_spec switching.modes"""
    dm, n = P["Dmax"], P["N"]
    best = None
    for va in np.arange(250.0, 1000.1, 25.0):
        for r in np.linspace(edges["band->buck"][0], edges["band->boost"][1], 41):
            vb = va * r
            if not 250.0 <= vb <= 1000.0:
                continue
            da, db = dm * min(1.0, r), dm * min(1.0, 1.0 / r)
            i = min(P["i_phase_max_band"], P["pmax"] / (da * va), port_limit(P, "pv") / (n * da),
                    port_limit(P, "battery") / (n * db))
            fa, fb = ripple_edges(P, da, db, va, vb, L_of(P, i) * (1 - P["Ltol"]))
            h = max(abs(fa), abs(fb))
            if best is None or i + h > best["peak"]:
                best = dict(peak=i + h, ripple=2 * h, va=va, vb=vb, i=i, r_span=(edges["band->buck"][0],
                                                                                 edges["band->boost"][1]))
    return best


def derive_requirements(P, G, R):
    pr = R["prot"]
    ch = P["chain"]
    cs = json.load(open(need(CELL_SPEC)))
    rq = {}
    # device voltage rule at the last switching event: V_port (1 + overshoot/1100) <= 0.85 V_DSS (ringing scales with V)
    rq["v_dev_lim"], rq["os_v"] = v_dev_limit(P)
    rq["v_cap_lim"] = 1.15 * cs["capacitors"]["port_B"]["V_op_85C_V"]      # film 1.15 x V(85 C) (IEC 61071)
    t0 = ch["port_oc"][3]                                                   # logic + driver after a comparator edge
    for kind in ("power", "current"):
        d = pr["ov"][kind]
        cur = d["curve"]
        t_max = float(np.interp(rq["v_dev_lim"], [r["v_off"] for r in cur], [r["t_resp_us"] for r in cur]))
        slope = cur[0]["slope"]

        def pick(path, thr):
            return next(b for b in d["built"] if b["path"].startswith(path) and b["thr"] == thr)
        hi, nom = pick("comparator", ch["ov_hw"][2]), pick("comparator", ch["ov_hw"][0])
        bk_hi, bk_nom = pick("PPB", ch["ov_ppb"][2]), pick("PPB", ch["ov_ppb"][0])
        rq["ov_" + kind] = dict(t_max_us=t_max, slope=slope, lag_us=cur[0]["t_resp_us"] - t0, hi=hi, nom=nom, bk_hi=bk_hi,
                                bk_nom=bk_nom, lag_max_us=(rq["v_dev_lim"] - ch["ov_hw"][2]) / slope,
                                per_max_us=ch["ppb_period_us"] + (t_max - bk_hi["t_resp_us"]),
                                thr_max_V=ch["ov_hw"][0] - (hi["v_off"] - rq["v_dev_lim"]),
                                meets=hi["v_off"] <= rq["v_dev_lim"], bk_meets=bk_hi["v_off"] <= rq["v_dev_lim"])
    o = rq["ov_power"]
    rq.update(ov_t_max_us=o["t_max_us"], ov_t_designed_us=o["hi"]["t_resp_us"], ov_t_nominal_us=o["nom"]["t_resp_us"],
              ov_detect_lag_us=o["lag_us"], ov_slope=o["slope"], ov_v_off_designed=o["hi"]["v_off"],
              ov_v_peak_designed=o["hi"]["v_peak"], cb_needed_uF={k: v * 1e6 for k, v in pr["cb_needed"].items()})
    # inductor over-current, two layers: rise time from each layer's upper band edge to L = 50 % L0 without a trip
    sh = pr["shorts"]
    nohw = [r for r in sh if r["variant"] == "no trip"]
    rq["oc_t_max_local_us"] = min(r["t_local_to_sat_us"] for r in nohw if r["t_local_to_sat_us"])
    rq["oc_t_max_backup_us"] = min(r["t_backup_to_sat_us"] for r in nohw if r["t_backup_to_sat_us"])
    rq["oc_t_max_us"] = min(r["t_nominal_to_sat_us"] for r in nohw if r["t_nominal_to_sat_us"])
    rq["oc_didt_max"] = max(r["didt_local"] for r in nohw if r.get("didt_local"))
    loc = [r for r in sh if r["variant"].startswith("bank short, local OC")]
    bak = [r for r in sh if r["variant"].startswith("bank short, local failed")]
    term = [r for r in sh if r["variant"].startswith("terminal short")]
    rq.update(oc_local_t_us=ch["oc_local"][4], oc_local_peak=max(r["i_at_off"] for r in loc),
              oc_backup_t_us=ch["oc_backup"][3], oc_backup_peak=max(r["i_at_off"] for r in bak),
              oc_after_off_peak=max(r["i_peak"] for r in loc + bak),
              term_peak=max(r["i_at_off"] for r in term), term_window_peak=max(r["i_peak"] for r in term),
              term_first=sorted({r["first"] for r in term}),
              term_t_off_us=max(r["t_off_us"] for r in term), rev_module_A=max(r["rev_module_A"] for r in term),
              body_dev_A=max(r["body_dev_A"] for r in term), body_I2t=max(r["body_I2t"] for r in term),
              v_reverse=max(r["v_reverse"] for r in term),
              i_dm_position=cs["device_primary"]["trip_band_costfirst"]["hardware"]["I_DM_A"])
    rq["oc_t_designed_us"] = rq["oc_backup_t_us"]
    rq["oc_i_peak_designed"] = max(rq["oc_local_peak"], rq["oc_backup_peak"])
    # DESAT as drawn (PV-PWR gate drive: threshold and blanking) + the NSI6651 filter + the short-circuit booster
    sc_ = cs["gate_drive"]["short_circuit"]
    blank, filt = [x * 1e-3 for x in ch["desat_blank_ns"]], [x * 1e-3 for x in A("desat_filter_ns")]
    t_off = sc_["booster_detection_to_off_us_primary"]
    vth = ch["desat_V"]
    rds = {"SG2M040170HJ": (0.040, 0.088), "MSC035SMA170B4": (0.035, 0.065)}     # typ 25 / 175 C (datasheets p.4)
    rq["desat"] = dict(blank_range_us=blank, filter_range_us=filt, off_after_detect_us=t_off,
                       typ_us=0.5 * (blank[0] + blank[1]) + 0.5 * (filt[0] + filt[1]) + t_off,
                       max_us=blank[1] + filt[1] + t_off, scwt_1100_us=sc_["withstand_assumed_us"],
                       withstand_basis=sc_["basis"], soft_off_only_us=sc_["response_us"]["Q1"],
                       required_detect_to_off_us=sc_["required_detect_to_off_us_max"], vth=vth,
                       desat_A_25C=[2 * vth[0] / max(r[0] for r in rds.values()), 2 * vth[1] / min(r[0] for r in rds.values())],
                       desat_A_175C=[2 * vth[0] / max(r[1] for r in rds.values()), 2 * vth[1] / min(r[1] for r in rds.values())])
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
        w.writerow(["case", "detector", "threshold_V", "gate_off_after_true_1100V_crossing_us", "V_at_gate_off", "V_peak"])
        for kind, d in R["prot"]["ov"].items():
            for r in d["curve"]:
                w.writerow([kind, f"ideal 1100 V + {r['extra_us']:.0f} us", 1100.0, fmt(r["t_resp_us"]), fmt(r["v_off"]),
                            fmt(r["v_peak"])])
            for r in d["built"]:
                w.writerow([kind, r["path"], r["thr"], fmt(r["t_resp_us"]), fmt(r["v_off"]), fmt(r["v_peak"])])
    with open(os.path.join(OUT, "shorts.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case", "trips_in_force", "i_at_gates_off_A", "i_peak_A", "first_trip", "t_detect_us", "t_gates_off_us",
                    "local_band_to_50pct_L0_us", "backup_band_to_50pct_L0_us", "reverse_module_A", "body_diode_per_device_A",
                    "body_diode_I2t_A2s", "bank_reverse_V"])
        for r in R["prot"]["shorts"]:
            w.writerow([r["label"], r["variant"], fmt(r["i_at_off"]), f"{r['i_peak']:.1f}", r.get("first", ""), fmt(r.get("t_detect_us")),
                        fmt(r.get("t_off_us")), fmt(r.get("t_local_to_sat_us")), fmt(r.get("t_backup_to_sat_us")),
                        f"{r['rev_module_A']:.0f}", f"{r['body_dev_A']:.0f}", f"{r['body_I2t']:.1f}",
                        f"{r['v_reverse']:.2f}"])
    with open(os.path.join(OUT, "envelope.csv"), "w", newline="") as f:
        w = csv.writer(f)
        ts = [f"{t:.0f}C" for t in A("env_inlet_C")]
        w.writerow(["product", "rated_kW", "V_A", "V_B", "direction", "mode", "P_src_kW_45C", "binding_45C",
                    "P_src_kW_45C_earlier_45A_law", "eta_45C"] + [f"P_del_kW_{t}" for t in ts] + ["rated_met_45C"])
        for nm, e in R["env"].items():
            for r in e["rows"]:
                w.writerow([nm, e["rated_kW"], r["va"], r["vb"], r["dir"], r["mode"], f"{r['p_src_kW_45C']:.2f}", r["binding_45C"],
                            f"{r['p_src_kW_45A_law']:.2f}", f"{r['eta_45C']:.5f}"] + [f"{r['p_del_kW_' + t]:.2f}" for t in ts] +
                           [r["rated_met_45C"]])
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


def firmware_handoff(P):
    """second-layer firmware of the drawn boards (D-050): PV-CTL's table, written by gen/pv_ctrl.py for this spec, and
    the lean ports' rules (port_spec lean firmware_requirements)"""
    txt = open(need(CTL_CHECK)).read()
    k = txt.find("Firmware on top of the hardware")
    rows = [x for x in txt[k:].splitlines()[1:] if " | " in x] if k >= 0 else []
    if len(rows) < 2:
        raise ValueError(f"{os.path.relpath(CTL_CHECK, ROOT)}: no firmware table - re-run {MAKER[CTL_CHECK]}")
    head = [x.strip() for x in rows[0].split(" | ")]
    return dict(control_board=[dict(zip(head, [x.strip() for x in r.split(" | ")])) for r in rows[1:]],
                ports=P["lean"]["firmware_requirements"])


def limits_section(P, R):
    """the control's limits, kept apart: inductor (phase) current, node-A power, port currents, thermal derating"""
    req, bp, lo = json.load(open(need(CELL_SPEC)))["inductor"]["requirement"], R["band_peak"], P["chain"]["oc_local"][2]
    out = {"i_phase_max_A": {"buck_boost": P["i_phase_max"], "band": P["i_phase_max_band"],
                             "band_normal_peak_A": round(bp["peak"], 1),
                             "basis": f"inductor mean current, by the cell's own modulation mode. Buck / boost (one leg "
                                      f"switching, ripple up to {req['ripple_pp_max_A']:.1f} A pp): the inductor's DC design "
                                      f"current (cell_spec inductor I_dc_max; normal peak {P['i_peak_normal']:.1f} A, 1.10 x "
                                      f"= {1.1 * P['i_peak_normal']:.1f} A against the {lo:.1f} A window edge: " +
                                      ("clear" if 1.1 * P["i_peak_normal"] <= lo else
                                       f"short by {1.1 * P['i_peak_normal'] - lo:.1f} A - the window's own design-margin "
                                       "item (PV-CTL), the window stays above the normal peak") + "). Band (both legs "
                                      f"near D_max, ripple {bp['ripple']:.1f} A pp at most): the inductor's rms design value "
                                      "I_rms_max, the band point the power stage, devices and module thermal model were "
                                      f"sized at (P_lim at unity ratio); normal peak {bp['peak']:.1f} A over the band as "
                                      "occupied (report 12.2). Not raised beyond the hardware's design point"},
           "i_port_share_per_cell_A": P["imax"], "p_cell_kW_node_A": P["pmax"] / 1e3,
           "i_port_A": {f"{n} cells": R["stab"][n]["P"]["i_port_lim"]["pv"] for n in (3, 4)},
           "i_port_battery_A": {f"{n} cells": R["stab"][n]["P"]["i_port_lim"]["battery"] for n in (3, 4)},
           "port_limits_basis": "lean ports (module_spec): PV port (A) = the module rating I_port_max, no fuse; battery "
                                "port (B) = cost-first battery_port_limit_A (the aR fuse pair, D-056) - "
                                f"{R['stab'][3]['P']['i_port_lim']['battery']:.0f} A on PV-P75, "
                                f"{R['stab'][4]['P']['i_port_lim']['battery']:.0f} A on PV-P100/110; flat in inlet "
                                "temperature. Enforced per cell as port limit / (N x actual duty) with a slow trim on the "
                                "measured port current",
           "p_module_kW": {"3 cells": 3 * P["pmax"] / 1e3, "4 cells": 4 * P["pmax"] / 1e3},
           "thermal_derating_fraction_of_P_max": {f"{n} cells": dict(zip([f"{t:.0f} C" for t in R["stab"][n]["P"]["p_frac_inlet"][0]],
                                                                         R["stab"][n]["P"]["p_frac_inlet"][1])) for n in (3, 4)},
           "thermal_derating_basis": "module_spec cost-first derating_thermal_only_fraction (sim/pv_module.py, on the "
                                     "delivered power); firmware derates on the NTC readings (PV-CTL), the envelope applies "
                                     "the fraction",
           "v_port_limit_loop_V": 1010.0, "v_ov_firmware_V": P["v_ov_sw"], "v_uv_stop_V": P["v_uv"],
           "v_a_min_mppt_V": pvm.MPPT["v_lo"], "reverse_into_pv": "blocked (reference >= 0 in MPPT mode)",
           "limit_checks_averaged_model": [{k: round(v, 2) if isinstance(v, float) else v for k, v in c.items()}
                                           for c in R["limits"]]}
    return out


def write_spec(P, G, R):
    rq, ch = R["req"], P["chain"]
    st = {N: summary_stab(R["stab"][N]["rows"]) for N in (3, 4)}
    old = json.load(open(os.path.join(OUT, "control_spec.json"))) if os.path.exists(os.path.join(OUT, "control_spec.json")) else {}
    sh = R["share"]
    lr = R["stab"][3]["lrob"]
    dly = R["stab"][3]["dly"]
    o, oc_ = rq["ov_power"], rq["ov_current"]
    p75r, i_pv = R["env"]["PV-P75"]["rated_kW"] * 1e3, P["i_port_lim"]["pv"]
    spec = {
        "title": "PV-P75 / PV-P100-110 control specification (calculated / simulated, NOT bench-validated)",
        "generated_by": "sim/pv_control.py", "inputs": P["src"],
        "hardware_basis": f"cost-first build as drawn: PV-PWR (3 phases) / PV-PWR-4 (4 phases) rev A2, PV-CTL rev A1 "
                          f"(F280039C), inductor rev M2, sensor {ch['sensor']}; the CTRL-C2000 / PVCELL-25 / PV-PORT values "
                          "of the earlier platform are no longer used",
        "inputs_disagree": {k: {"PV-CTL_design_check_band_and_response": chk, "cell_spec_or_port_spec_record_used": rec}
                            for k, (chk, rec) in ch["record_vs_check"].items()},
        "loop_structure": {
            "inner": "per cell: average inductor-current PI (64 kHz, double update), port-voltage feed-forward inside the "
                     "modulator, dead-time feed-forward from the current REFERENCE with the midpoint dead-time estimate "
                     "(dead_time section), clamping anti-windup",
            "modulator": "buck / band / boost exactly as cell_spec switching.modes; controller output = average inductor "
                         "voltage, so the loop gain does not change between modes",
            "outer": "32 kHz, one per module: V_A loop (MPPT reference or DC bus on A) or V_B loop (DC bus forming on B), "
                     "port-B over-voltage limit loop, each with gains scaled to its own port bank; per-cell limits (limits "
                     "section): inductor current by mode, node-A power 27.5 kW, both port currents; candidates "
                     "min-selected with tracking anti-windup; port-current reference -> per-cell inductor-current "
                     "reference through the cells' ACTUAL duty (carries the mode hysteresis); each cell's limits use its "
                     "own duty and mode and are slew-limited (ilim_slew_A_per_s)",
            "mppt": "dP-P&O at 20 Hz on oversampled V_A, I_A (sim/pv_mppt.py) -> V_A reference, ramp-limited",
            "sharing": "equal per-cell references (I_port* / (N_active D)), each cell closes its own current loop",
            "feed_forward": {"bus port (inverter = constant-power load)": A("k_ff_bus"), "PV array port": 0.0, "battery port": 0.0,
                             "basis": "with a stiff source (battery) the port-current feed-forward closes a unity loop; on "
                                      "a PV port it cancels the array's own damping"}},
        "sampling": {"pwm_kHz": P["fsw"] / 1e3, "carrier": "centre-aligned up-down, double update (zero and period)",
                     "current_loop_kHz": 1e-3 / G["Ts"], "outer_loop_kHz": 1e-3 / G["Tv"], "mppt_Hz": 1 / pvm.MPPT["T_p"],
                     "i_L_sample": f"each cell at its own carrier zero and peak + {G['t_soc']*1e6:.2f} us (compensates the "
                                   "drawn sensor + AFE delay)",
                     "port_V_I_sample": "at cell 1's carrier zero (outer loop)",
                     "computation_delay": "one sample (duty computed from the sample at t_k is loaded at t_k + Ts) - "
                                          "REQUIRED: with two samples the current loop keeps only "
                                          f"{dly[2]['pm'][0]:.1f} deg (stability/computation_delay)",
                     "isr_budget_us": (G["Ts"] - G["t_soc"] - A("t_conv_s")) * 1e6,
                     "controller": "F280039C, 120 MHz C28x + 120 MHz CLA (architecture: ~200 cycles per current-loop "
                                   "execution = 1.7 us, 6 / 8 per 31.25 us; R-14 open until the firmware is timed)",
                     "adc_load": f"{ch['adc_conv']:.0f} conversions per {ch['adc_window_us']:.2f} us (PV-CTL sampling plan)",
                     "port_V_conversion_for_OV_PPB_us_max": round(o["per_max_us"], 1),
                     "port_V_conversion_note": f"the PPB backup evaluates every V_A / V_B conversion; PV-CTL converts them "
                                               f"every {ch['ppb_period_us']:.0f} us",
                     "interleave_deg": {"3 cells": 120, "4 cells": 90}},
        "current_loop": {"kp_V_per_A": G["kp"], "ki_V_per_As": G["ki"], "form": "q += ki Ts e; u = kp e + q (volts across L)",
                         "design_crossover_Hz": A("f_ci_Hz"), "PI_zero_Hz": A("f_ci_Hz") / A("ci_zero_ratio"),
                         "crossover_range_Hz_N3": st[3]["fc_i"], "min_PM_deg": st[3]["pm_i"]["pm_i"], "min_GM_dB": st[3]["gm_i"]["gm_i"],
                         "anti_windup": "integrator clamped to the realisable inductor voltage",
                         "limit_A": {"buck_boost": P["i_phase_max"], "band": P["i_phase_max_band"]},
                         "L_used_for_gain_uH": P["Lnom"] * 1e6,
                         "L_robustness_N3": {f"L(45 A) x {f:.2f}": {k: [round(fn(x[k] for x in lr if x["delay"] == 1 and
                                                                                 x["fac"] == f and x["i_at"] == P["i_phase_max"]), 2)
                                                                        for fn in (min, max)] for k in ("pm", "gm", "fc")}
                                             for f in (0.92, 1.0, 1.08)},
                         "reduced_model_PM_deg_L45_x0.92_1.00_1.08": {
                             f"{d} sample{'s' if d > 1 else ''}": [round(p, 2) for p, _ in R["stab"][3]["red"][d]] for d in (1, 2)},
                         "reduced_model_basis": "1/(sL + R), exact ZOH, PI, chain lag uncompensated, ideal ports (review "
                                                "PCM-02 cross-check; report section 5)"},
        "outer_loops": {"kpv_A_per_V": G["kpv"], "kiv_A_per_Vs": G["kiv"], "design_crossover_Hz": A("f_cv_Hz"),
                        "PI_zero_Hz": A("f_cv_Hz") / A("cv_zero_ratio"),
                        "kpv_kiv_scale": f"per port with that port's bank as drawn: N=3 A {P['CA']*1e6:.1f} uF / B "
                                         f"{P['CB']*1e6:.1f} uF; N=4 {R['stab'][4]['P']['CA']*1e6:.1f} / "
                                         f"{R['stab'][4]['P']['CB']*1e6:.1f} uF",
                        "kpv_kiv_N4": {x: [R["stab"][4]["G"]["kpv"][x], R["stab"][4]["G"]["kiv"][x]] for x in "AB"},
                        "antiwindup_margin_A_per_cell": A("aw_margin_A"), "vb_limit_integrator_max_A": A("vlim_qmax_A"),
                        "min_PM_deg_N3": st[3]["pm_v"]["pm_v"], "min_modulus_margin_N3": st[3]["mm_v"]["mm_v"],
                        "min_PM_deg_N4": st[4]["pm_v"]["pm_v"], "min_modulus_margin_N4": st[4]["mm_v"]["mm_v"]},
        "limits": limits_section(P, R),
        "envelope": {"policy": f"this script's control law: inductor current {P['i_phase_max']:.1f} A (buck / boost) / "
                               f"{P['i_phase_max_band']:.2f} A (band), node-A power {P['pmax']/1e3:.1f} kW per cell (node A "
                               "= the PV / source side for A->B, the sink for B->A; the phase limit is a node-A power too), "
                               "lean port currents, module_spec thermal derating on the delivered power; delivered = "
                               "source power x the module efficiency of sim/pv_module.py (module_envelope.csv at P_lim, "
                               "25 C, corrected to the inlet with module_spec's 25 -> 45 C corner drop; same for both "
                               "directions)",
                     "products": R["env"],
                     "note": f"{p75r/1e3:.0f} kW at exactly 550/550 V needs {p75r/550:.1f} A at the port before loss: "
                             f"PV-04/PV-07 (full load from 550 V) and PV-05/PV-08 ({i_pv:.0f} A) are inconsistent by "
                             f"{(p75r/550/i_pv - 1)*100:.1f} % at that corner; {i_pv*550/1e3:.2f} kW is the most the "
                             f"{i_pv:.0f} A port passes there before loss"},
        "modulator": {"D_max": P["Dmax"], "D_min": P["Dmin"], "dead_time_ns": list(A("dt_range_ns")), "min_pulse_us": P["tmin"] * 1e6,
                      "D_max_basis": f"1 - t_min f_sw, the same in every mode; t_min {P['tmin']*1e6:.3f} us = "
                                     f"{(P['tmin'] - 2 * P['td_gates'][1] * 1e-9)*1e6:.1f} us complementary on-time + 2 x "
                                     f"the {P['td_gates'][1]:.0f} ns longest dead time at the gates (cell_spec "
                                     "min_pulse_basis); the i_L sample's S/H window closes "
                                     f"{(P['tmin'] / 2 - G['t_soc'] - A('t_sh_s'))*1e9:.1f} ns before the edge of a minimum "
                                     "pulse centred on the sampling extremum (report 12.1)",
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
                            "ramp; the PI integrator takes the residual",
            "mode_logic": f"each candidate mode judged with its own dead-time terms; enter band at once when the single-leg "
                          f"duty exceeds D_max; leave band only below D_max - {G['hyst']} for both the demand u and the PI "
                          f"integrator filtered with {A('mode_exit_tau_s')*1e3:.0f} ms",
            "firmware_must": ["use the midpoint of the hardware dead-time range as the estimate for every edge (no per-unit "
                              "value is needed)", "drive the dead-time compensation from the slew-limited current "
                              "reference (closed-loop model), not from the measured current",
                              f"keep the band-exit hysteresis >= {G['hyst']} in duty and test the exit also on the PI "
                              f"integrator filtered with {A('mode_exit_tau_s')*1e3:.0f} ms",
                              "derive each cell's limits from its own duty and mode and slew them (no ratio map)",
                              "convert port current references with the cells' actual duty",
                              "optional: identify each edge's dead time (integrator jump at the first band entry / exit) - "
                              f"it cuts the worst transition error from {max(c['err_max'] for c in R['trans']):.2f} A to "
                              f"{max(x['err_id'] for x in R['ident']):.2f} A; not needed for stability or limits"],
            "transition_worst_error_A_over_range": max(c["err_max"] for c in R["trans"]),
            "zero_crossing_worst_error_A_over_range": max(z["err_max"] for z in R["zc"] if z["comp"] == "ref")},
        "measurement_requirements": R["meas"],
        "hardware_trips": {
            "architecture": "as drawn: PV-CTL rev A1 trip table (local IL window from each sensor's reference, CMPSS "
                            "backup, VA / VB comparator, ADC PPB backup) and PV-PWR rev A2 (port OC comparators on FLT_N, "
                            "gate-drive DESAT); one latch gates every PWM, EN and contactor command (PV-CTL design check)",
            "inductor_overcurrent": {
                "threshold_A": [-ch["oc_local"][1], ch["oc_local"][0]],
                "type": f"two layers: PV-CTL local window +{ch['oc_local'][0]:.1f} / -{ch['oc_local'][1]:.1f} A "
                        f"({ch['oc_local'][2]:.1f}-{ch['oc_local'][3]:.1f} A) -> latch, {ch['oc_local'][4]:.2f} us to gates "
                        f"off; CMPSS window on IL +/-{ch['oc_backup'][0]:.1f} A ({ch['oc_backup'][1]:.1f}-"
                        f"{ch['oc_backup'][2]:.1f} A), {ch['oc_backup'][3]:.2f} us, as backup",
                "max_response_us_trip_level_to_gates_off": rq["oc_t_max_local_us"],
                "basis": "time for the fastest bank-short current to rise from the layer's upper band edge to "
                         f"L = 50 % L0 ({P['i50']:.0f} A); device pulse rating {rq['i_dm_position']:.0f} A per position",
                "as_designed_us": rq["oc_backup_t_us"], "as_designed_peak_A": rq["oc_i_peak_designed"],
                "meets": rq["oc_local_peak"] < P["i50"] and rq["oc_backup_peak"] < P["i50"],
                "local": {"band_A": ch["oc_local"][2:4], "response_us": ch["oc_local"][4],
                          "max_response_us": rq["oc_t_max_local_us"], "peak_A": rq["oc_local_peak"],
                          "meets": rq["oc_local_peak"] < P["i50"]},
                "ctrl_backup": {"band_A": ch["oc_backup"][1:3], "max_response_us": rq["oc_t_max_backup_us"],
                                "response_us": ch["oc_backup"][3], "peak_A": rq["oc_backup_peak"],
                                "meets": rq["oc_backup_peak"] < P["i50"],
                                "note": "PV-CTL CMPSS1-4 window on the IL ADC pins (key name kept from the earlier card)"},
                "didt_max_A_per_us": rq["oc_didt_max"],
                "sensor_as_built": f"{ch['sensor']} on PV-PWR + PV-CTL AFE: {ch['il_mV_A']:.2f} mV/A at the ADC, "
                                   f"{ch['lsb_il']:.4f} A/count, -3 dB {ch['f_il_kHz']:.0f} kHz, delay {ch['t_il_us']:.2f} us, "
                                   f"noise {ch['noise_App']:.1f} A pp"},
            "port_overvoltage": {
                "threshold_V": ch["ov_hw"][0],
                "type": f"primary: PV-CTL comparator on VA / VB {ch['ov_hw'][0]:.0f} V ({ch['ov_hw'][1]:.0f}-"
                        f"{ch['ov_hw'][2]:.0f} V), lag {ch['ov_hw_lag_us']:.0f} us; backup: ADC PPB {ch['ov_ppb'][0]:.0f} V "
                        f"({ch['ov_ppb'][1]:.0f}-{ch['ov_ppb'][2]:.0f} V), converted every {ch['ppb_period_us']:.0f} us, "
                        f"lag {ch['ppb_lag_us']:.1f} us; both behind the PV-PWR divider pole ({ch['f_div_kHz']:.1f} kHz)",
                "max_response_us_true_crossing_to_gates_off": rq["ov_t_max_us"],
                "basis": f"port voltage at the last switching event <= {rq['v_dev_lim']:.0f} V (0.85 V_DSS incl. "
                         f"{rq['os_v']:.0f} V ringing scaled with V); film bank 1.15 x 1100 V = {rq['v_cap_lim']:.0f} V not "
                         f"binding; slope {rq['ov_slope']:.2f} V/us at 82.5 kW with port B {P['CB']*1e6:.1f} uF",
                "as_designed_us": rq["ov_t_designed_us"], "as_designed_detection_lag_us": rq["ov_detect_lag_us"],
                "as_designed_us_nominal_threshold": rq["ov_t_nominal_us"], "as_designed_V_at_gates_off": rq["ov_v_off_designed"],
                "meets": o["meets"],
                "ppb_backup_alone": {"V_at_gates_off_upper_band": o["bk_hi"]["v_off"],
                                     "V_at_gates_off_nominal": o["bk_nom"]["v_off"], "meets_upper_band": o["bk_meets"]},
                "port_B_bank_needed_uF": {k: round(v, 1) for k, v in rq["cb_needed_uF"].items()},
                "port_B_bank_drawn_uF": round(P["CB"] * 1e6, 1),
                "full_current_frozen_case": {
                    "case": "outer control frozen, cells held at their limit, stiff port A at 1000 V, port B (611 V) opens",
                    "slope_V_per_us": oc_["slope"], "max_response_us": oc_["t_max_us"],
                    "comparator_upper_band_response_us": oc_["hi"]["t_resp_us"],
                    "comparator_upper_band_V_at_gates_off": oc_["hi"]["v_off"], "meets": oc_["meets"],
                    "fix_options": {"comparator_nominal_threshold_V_max": round(oc_["thr_max_V"], 1),
                                    "or_detection_lag_us_max": round(oc_["lag_max_us"], 1)}}},
            "firmware_overvoltage_V": P["v_ov_sw"], "firmware_undervoltage_V": P["v_uv"],
            "desat": {**rq["desat"], "meets_typ": rq["desat"]["typ_us"] < rq["desat"]["scwt_1100_us"],
                      "meets_max": rq["desat"]["max_us"] < rq["desat"]["scwt_1100_us"],
                      "source": "PV-PWR gate drive (gdrv lean channel): threshold and blanking as drawn; booster "
                                "detection-to-off from cell_spec / gen/gdrv.py"},
            "port_overcurrent_A": {"OC": ch["port_oc"][0], "OC_band_A": ch["port_oc"][1:3],
                                   "response_us_after_detection": ch["port_oc"][3],
                                   "source": "PV-PWR comparators on the IH shunt path -> FLT_N (no firmware); backup "
                                             "CMPSS4 / ADC PPB on IA / IB (PV-CTL); no separate short-circuit level",
                                   "terminal_short": {"first_layer": rq["term_first"], "gates_off_us_after_short": rq["term_t_off_us"],
                                                      "i_L_peak_A": rq["term_peak"]}},
            "terminal_short_reverse_conduction": {
                "reverse_clamp": "none on PV-PWR (D-044 gave up terminal-short survival)",
                "module_A": rq["rev_module_A"], "body_diode_per_device_A": rq["body_dev_A"],
                "body_diode_I2t_A2s": rq["body_I2t"], "bank_reverse_V": rq["v_reverse"],
                "I_DM_per_position_A": rq["i_dm_position"],
                "note": "static model at the lumped port node (5 mOhm / 1 uH bolted short); the architecture accepts that "
                        "the port-side legs are likely destroyed (ARCHITECTURE-COSTFIRST 6.4)"}},
        "stability": {f"N{N}": {"cases": st[N]["n"], "unstable": st[N]["n_unstable"],
                                "min_PM_current_loop": [st[N]["pm_i"]["pm_i"], where(st[N]["pm_i"])],
                                "min_GM_current_loop_dB": [st[N]["gm_i"]["gm_i"], where(st[N]["gm_i"])],
                                "min_PM_outer": [st[N]["pm_v"]["pm_v"], where(st[N]["pm_v"])],
                                "min_modulus_margin_outer": [st[N]["mm_v"]["mm_v"], where(st[N]["mm_v"])],
                                "min_gain_margin_outer_dB": [min(st[N]["gm_v"]["gm_v"], st[N]["gm_v"]["gm_lo_v"]), where(st[N]["gm_v"])],
                                "cpl_without_feedforward_unstable_corners": sum(1 for x in R["stab"][N]["noff"] if not x["stable"]),
                                "cpl_without_feedforward_min_PM_deg": min((x["pm"] for x in R["stab"][N]["noff"] if x["stable"]),
                                                                          default=None),
                                "computation_delay": {f"{d} sample{'s' if d > 1 else ''}": {
                                    "current_loop_PM_deg": [round(x, 2) for x in R["stab"][N]["dly"][d]["pm"]],
                                    "current_loop_GM_dB_min": round(R["stab"][N]["dly"][d]["gm"], 2),
                                    "crossover_Hz": [round(x) for x in R["stab"][N]["dly"][d]["fc"]]} for d in (1, 2)}}
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
        "firmware_second_layer": firmware_handoff(P),
        "honesty": "Simulation only. Power stage ideal-switch, sensors first-order, PV/battery/CPL models are assumptions "
                   "listed in report.md; nothing is bench-validated.",
    }
    json.dump(spec, open(os.path.join(OUT, "control_spec.json"), "w"), indent=1, default=float)
    return spec


def meas_requirements(P, G, R):
    rq, ch = R["req"], P["chain"]
    sh_cal = max(r["dev_pct"] for r in R["share"] if r["label"] == "calibrated")
    post = ch["oc_backup"][3] - ch["t_il_us"]                 # comparator + filter + trip zone + logic + driver (PV-CTL)
    d_max = rq["oc_t_max_backup_us"] - post                    # current -> CMPSS input delay the backup layer allows
    o, oc = rq["ov_power"], rq["ov_current"]
    return {
        "cell_inductor_current": {
            "range_A": [-A("i_sense_fs_A"), A("i_sense_fs_A")], "bandwidth_kHz_min": A("f_isense_Hz") / 1e3,
            "group_delay_us_max": round(d_max, 2), "resolution_A_per_LSB": A("res_il_req_A_per_LSB"),
            "accuracy_after_cal": {"gain_pct": 100 * P["sens_gain_err"], "offset_A": P["sens_off_err_A"],
                                   "basis": "the drawn sensor after the 2-point calibration (PV-PWR design check), as used "
                                            "by the sharing study (result: sharing/)"},
            "comparator_threshold_band_A": ch["oc_backup"][1:3],
            "as_drawn": {"sensor": ch["sensor"], "mV_per_A_at_ADC": ch["il_mV_A"], "A_per_LSB": ch["lsb_il"],
                         "bandwidth_kHz": ch["f_il_kHz"], "delay_us": ch["t_il_us"], "noise_A_pp": ch["noise_App"],
                         "noise_A_rms_model": P["sig_il"]},
            "basis": f"CMPSS backup (upper band {ch['oc_backup'][2]:.1f} A) must have the gates off within "
                     f"{rq['oc_t_max_backup_us']:.2f} us (fastest bank-short rise to L = 50 % L0) = delay current -> CMPSS "
                     f"input <= {d_max:.2f} us + comparator, filter, trip zone and driver {post:.2f} us (PV-CTL); as drawn "
                     f"{ch['t_il_us']:.2f} us, peak {rq['oc_backup_peak']:.0f} A. Current-loop phase: sampled "
                     f"{G['t_soc']*1e6:.2f} us after the carrier extremum; sharing within {sh_cal:.1f} % of 45 A"},
        "port_voltage_VA_VB": {
            "range_V": [0, math.ceil(rq["v_dev_lim"])],        # every port voltage the protection analysis has to read
            "adc_full_scale_V": P["v_fs_adc"], "resolution_V_per_LSB": A("res_v_req_V_per_LSB"),
            "resolution_V_per_LSB_as_drawn": P["lsb_v"], "filter_pole_kHz_as_designed": ch["f_div_kHz"],
            "noise_V_rms_per_sample": P["sig_v"], "bandwidth_kHz_min": 3.0,
            "ov_detection_lag_us_max": round(o["lag_max_us"], 1),
            "ov_detection_lag_us_max_full_current_case": round(oc["lag_max_us"], 1),
            "conversion_period_us_max_for_ppb": round(o["per_max_us"], 1),
            "accuracy": "PV-CTL design check 'ADC accuracy' (after the 2-point calibration, incl. the PV-PWR divider)",
            "basis": f"the comparator at its upper band edge {ch['ov_hw'][2]:.0f} V keeps the gates-off voltage <= "
                     f"{rq['v_dev_lim']:.0f} V if its whole lag (divider pole + overdrive + comparator + logic) is <= "
                     f"{o['lag_max_us']:.0f} us at 82.5 kW ({oc['lag_max_us']:.0f} us in the frozen-current case); as drawn "
                     f"{ch['ov_hw_lag_us']:.0f} us. Range: up to the device-rule voltage, the highest port voltage the "
                     "trips and the over-voltage analysis read"},
        "port_current_IA_IB": {
            "range_A": [-500, 500], "resolution_A_per_LSB": A("res_ip_req_A_per_LSB"),
            "resolution_A_per_LSB_as_drawn": P["lsb_ip"], "noise_A_rms_per_sample": P["sig_ip"],
            "delay_us": P["tau_ip"] * 1e6, "bandwidth_kHz_min": 10.0,
            "basis": "load-current feed-forward on a bus port (the CPL margin at 250 V rests on it: without it see "
                     f"stability/N3/cpl_without_feedforward_*); as drawn: lean shunt {ch['f_ip_kHz']:.1f} kHz pole (PV-PWR); "
                     "MPPT power measurement (oversampling requirement in sim/pv_mppt.py)"}}


def env_table(a, env, names):
    """envelope rows of one build (products with the same phases share the rows; rated flags per product)"""
    e0 = env[names[0]]
    ts = [f"{t:.0f}C" for t in A("env_inlet_C")]
    a("| V_A / V_B [V] | direction | mode | P_src at 45 C [kW] | binding at 45 C | earlier law (45 A in band) P_src [kW] | "
      + " | ".join(f"P_del {t} [kW]" for t in ts) + " | " + " | ".join(f"{n} rated ({env[n]['rated_kW']:.0f} kW) at 45 C"
                                                                        for n in names) + " |")
    a("|---|---|---|---|---|---|" + "---|" * (len(ts) + len(names)))
    for k, r in enumerate(e0["rows"]):
        a(f"| {r['va']:.0f} / {r['vb']:.0f} | {r['dir']} | {r['mode']} | {r['p_src_kW_45C']:.2f} | {r['binding_45C']} | "
          f"{r['p_src_kW_45A_law']:.2f} | " + " | ".join(f"{r['p_del_kW_' + t]:.2f}" for t in ts) + " | " +
          " | ".join("yes" if env[n]["rows"][k]["rated_met_45C"] else "**no**" for n in names) + " |")


def write_report(P, G, R, spec):
    rq, st = R["req"], {N: summary_stab(R["stab"][N]["rows"]) for N in (3, 4)}
    ag, pr, su = R["agree"], R["prot"], R["start"]
    ch = P["chain"]
    P4 = R["stab"][4]["P"]
    L = []
    a = L.append
    a("# PV-P75 / PV-P100-110 module control: plant, loops, stability, protection, delivered-power envelope\n")
    a("Generated by `sim/pv_control.py`; every number below is written by the script. **Simulated / calculated, not "
      "bench-validated.** Hand-off: `control_spec.json`. MPPT: `sim/out/pv_mppt/report.md`. Hardware basis: the cost-first "
      f"build as drawn - PV-PWR / PV-PWR-4 rev A2, PV-CTL rev A1 (F280039C), inductor rev M2, {ch['sensor']}; the earlier "
      "platform's values (CTRL-C2000, PVCELL-25 with the LEM LA 150-P, PV-PORT) are no longer used.\n")
    a("## 1. Inputs and assumptions\n")
    a(f"- Inputs (read, never substituted - a missing file or line stops the run): {'; '.join(P['src'])}.")
    a(f"- Power stage: f_sw {P['fsw']/1e3:.0f} kHz, firmware dead band {P['td']*1e9:.0f} ns stretched by the gate-drive "
      f"interlock to {P['td_gates'][0]:.0f}-{P['td_gates'][1]:.0f} ns at the gates (designed for an unknown "
      f"{A('dt_range_ns')[0]:.0f}-{A('dt_range_ns')[1]:.0f} ns per edge), D_max {P['Dmax']:.5f} (min pulse "
      f"{P['tmin']*1e6:.3f} us), L(I) {P['L0']*1e6:.0f} uH at 0 A / {P['Lnom']*1e6:.0f} uH at 45 A / "
      f"{float(np.interp(P['i_trip'], P['Li'], P['Lv']))*1e6:.0f} uH at {P['i_trip']:.1f} A / 50 % of L0 at {P['i50']:.0f} A "
      f"(tolerance +/-{P['Ltol']*100:.0f} %), series R {P['R']*1e3:.1f} mOhm.")
    a(f"- Port banks as drawn (PV-PWR design check, incl. the leg decoupling films): PV-P75 port A {P['CA']*1e6:.1f} uF, port B "
      f"{P['CB']*1e6:.1f} uF; PV-P100/110 {P4['CA']*1e6:.1f} / {P4['CB']*1e6:.1f} uF. The 2.2 uF X capacitor per port sits on the "
      "terminal side of the contactor and the common-mode ring and is left out (conservative for over-voltage and CPL). "
      "The earlier model used a symmetric 3 x 90 + 4.4 = 274.4 uF.")
    a(f"- Inductor current as drawn: {ch['sensor']} on PV-PWR + PV-CTL AFE: {ch['il_mV_A']:.2f} mV/A at the ADC, "
      f"{ch['lsb_il']:.4f} A/LSB, -3 dB {ch['f_il_kHz']:.0f} kHz, delay {ch['t_il_us']:.2f} us (modelled as one lag of that "
      f"value, which is also the sampling offset), noise {ch['noise_mvpp']:.0f} mVpp = {ch['noise_App']:.1f} A pp -> "
      f"{P['sig_il']:.2f} A rms per sample (pp / {A('noise_pp_per_rms'):.0f}, ASSUMED crest factor); after calibration gain "
      f"drift <= {P['sens_gain_err']*100:.1f} %, linearity {P['sens_off_err_A']:.2f} A.")
    a(f"- Port sensing as drawn: V_A / V_B = V/{ch['k_div']:.0f} behind the PV-PWR divider pole {ch['f_div_kHz']:.1f} kHz "
      f"({P['tau_rc']*1e6:.1f} us) and the PV-CTL RC {ch['f_adc_rc_MHz']:.1f} MHz, {P['lsb_v']:.3f} V/LSB, full scale "
      f"-{P['v_fs_neg']:.0f}..+{P['v_fs_adc']:.0f} V; I_A / I_B = lean shunt 3.0 mV/A, pole {ch['f_ip_kHz']:.1f} kHz "
      f"(lag {P['tau_ip']*1e6:.1f} us), {P['lsb_ip']:.3f} A/LSB; F280039C ADC noise {A('adc_noise_lsb'):.2f} LSB rms (ENOB "
      f"11.4); {ch['adc_conv']:.0f} conversions per {ch['adc_window_us']:.2f} us (PV-CTL sampling plan).")
    a(f"- Hardware trips as drawn (bands and responses of the IL window, the CMPSS backup and the OV comparator from cell_spec's "
      f"PV-CTL records, the port OC band from port_spec lean, the rest from the PV-CTL trip table; centres = band midpoints): "
      f"inductor local window {ch['oc_local'][0]:.1f} A ({ch['oc_local'][2]:.1f}-{ch['oc_local'][3]:.1f} A), gates off "
      f"{ch['oc_local'][4]:.2f} us after the crossing; CMPSS backup +/-{ch['oc_backup'][0]:.1f} A ({ch['oc_backup'][1]:.1f}-"
      f"{ch['oc_backup'][2]:.1f} A), {ch['oc_backup'][3]:.2f} us; port OV comparator {ch['ov_hw'][0]:.0f} V ({ch['ov_hw'][1]:.0f}-"
      f"{ch['ov_hw'][2]:.0f} V), lag {ch['ov_hw_lag_us']:.0f} us from the true crossing (divider pole {ch['ov_rc_us']:.1f} us + "
      f"overdrive build-up + comparator + logic); ADC PPB backup {ch['ov_ppb'][0]:.0f} V ({ch['ov_ppb'][1]:.0f}-"
      f"{ch['ov_ppb'][2]:.0f} V) every {ch['ppb_period_us']:.0f} us, lag {ch['ppb_lag_us']:.1f} us; port OC "
      f"{ch['port_oc'][0]:.0f} A ({ch['port_oc'][1]:.0f}-{ch['port_oc'][2]:.0f} A) on PV-PWR, {ch['port_oc'][3]:.2f} us after "
      f"detection; firmware OV {P['v_ov_sw']:.0f} V, UV stop {P['v_uv']:.0f} V.")
    for k, (chk, rec) in ch["record_vs_check"].items():
        a(f"- **Inputs disagree ({k}):** {os.path.relpath(CTL_CHECK, ROOT)} on disk shows band {chk[0]:g}-{chk[1]:g}, response "
          f"{chk[2]:g} us; the JSON record used here says {rec[0]:g}-{rec[1]:g}, {rec[2]:g} us. One of the two generated files "
          f"is stale (gen/pv_ctrl.py stops its own build while they differ): re-run {MAKER[CTL_CHECK]}, then this script.")
    a(f"- Limits (section 12): inductor current {P['i_phase_max']:.1f} A in buck / boost, {P['i_phase_max_band']:.2f} A in the "
      f"band; node-A power {P['pmax']/1e3:.1f} kW per cell; port currents PV-P75 {P['i_port_lim']['pv']:.0f} / "
      f"{P['i_port_lim']['battery']:.0f} A, PV-P100/110 {P4['i_port_lim']['pv']:.0f} / {P4['i_port_lim']['battery']:.0f} A "
      "(PV port A / battery port B).")
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
    a(f"  V_B-max limit PI (+ I_B feed-forward, bus)  ------+--> (tracking --> i_L {P['i_phase_max']:.0f} / "
      f"{P['i_phase_max_band']:.1f} A  --> i_L* (same for all cells)")
    a("  or V_B loop (bus forming)                         |    anti-windup)  27.5 kW, ports              |")
    a("                                                                                                  v")
    a("  per cell (64 kHz, own carrier, 120/90 deg):  i_L(sampled at carrier zero/peak + t_soc) -> PI -> u = v_L*")
    a("     -> + dead-time feed-forward (midpoint estimate, sign from i_L*) -> modulator (buck/band/boost, V_A, V_B")
    a("        feed-forward, hysteresis) -> D_A, D_B -> shadow load at the next zero/peak -> ePWM (200 ns dead band)")
    a(f"     -> PC connector -> PV-PWR buffer -> {P['driver']} (dead time {P['td_gates'][0]:.0f}-{P['td_gates'][1]:.0f} ns at "
      f"the gates) -> 2 x {P['device']} per switch")
    a("  hardware (PV-CTL / PV-PWR): IL local window (comparator) and CMPSS window (backup) -> latch; VA/VB comparator +")
    a("        ADC PPB (backup); port OC comparators -> FLT_N; DESAT -> FLT_N -> latch -> all PWM, EN, contactors off")
    a("```\n")
    a(f"- Current loop: kp {G['kp']:.3f} V/A, ki {G['ki']:.0f} V/(A s) (design {A('f_ci_Hz'):.0f} Hz at L(45 A), zero at "
      f"{A('f_ci_Hz')/A('ci_zero_ratio'):.0f} Hz); achieved crossover {st[3]['fc_i'][0]:.0f}-{st[3]['fc_i'][1]:.0f} Hz over all corners.")
    a(f"- Outer loops, each scaled with its own port bank: V_A loop kp {G['kpv']['A']:.3f} A/V, ki {G['kiv']['A']:.1f} A/(V s); "
      f"V_B loops kp {G['kpv']['B']:.3f} A/V, ki {G['kiv']['B']:.1f} A/(V s) (3 phases; 4 phases "
      f"{R['stab'][4]['G']['kpv']['A']:.3f} / {R['stab'][4]['G']['kiv']['A']:.1f} on both ports), design {A('f_cv_Hz'):.0f} Hz, "
      f"zero at {A('f_cv_Hz')/A('cv_zero_ratio'):.0f} Hz. Load-current feed-forward 1 on a DC-bus port, 0 on a PV or battery "
      "port (section 5).")
    a(f"- Timing: sample at the carrier extremum + {G['t_soc']*1e6:.2f} us (the drawn i_L chain delay); duty loaded at the next "
      f"extremum (one-sample delay, {G['Ts']*1e6:.2f} us); budget per loop execution {(G['Ts']-G['t_soc']-A('t_conv_s'))*1e6:.1f} us "
      "after the conversion (F280039C: architecture estimate ~200 cycles = 1.7 us on the 120 MHz CLA, 6 / 8 executions per "
      "31.25 us - R-14 stays open until the firmware is timed); mode changes only take effect at a carrier zero.")
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
            a(f"| {name} | {vb:.0f} | {len(cc)} (V_A up/down, +/-{abs(cc[0]['i_cmd']):.1f} A command) | {max(c['err_max'] for c in cc):.2f} | "
              f"{max(c['err_rms'] for c in cc):.2f} | {'/'.join(sorted({str(m) for c in cc for m in c['mode_changes']}))} | "
              f"{max(c['i_peak'] for c in cc):.1f} |")
    tt = R["trans"]
    a(f"\nOver all {len(tt)} sweeps every cell enters and leaves the band exactly once (no chatter, also with the drawn "
      f"sensor's noise of {P['sig_il']:.2f} A rms per sample, which the sampled-current errors below include), the worst sampled-current "
      f"deviation is {max(c['err_max'] for c in tt):.2f} A ({max(c['err_max'] for c in tt if c['vb'] == 550.0):.2f} A at full current, "
      f"V_B 550 V) and the highest instantaneous current {max(c['i_peak'] for c in tt):.1f} A (local OC trip band starts at "
      f"{ch['oc_local'][2]:.1f} A). At 550 V the command is held by each cell's phase limit, which steps "
      f"{P['i_phase_max']:.1f} -> {P['i_phase_max_band']:.2f} -> {P['i_phase_max']:.1f} A with the cell's own mode; at 800 / "
      "860 V by its 27.5 kW limit, which steps by 1/D_max - 1 = 5.3 % the same way; both are slewed (part of the error is "
      "that tracking).\n")
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
      "outer sample and kicked the current loops across the mode thresholds. The limits and the port-current conversions now "
      "use each cell's actual duty and mode (which carry the hysteresis) and the limits are slewed.")
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
        Pn = R["stab"][N]["P"]
        a(f"**{N} cells** ({s['n']} cases: V_A, V_B in 250/550/950/1000 V, 5 % and 100 % of the power the limits allow there "
          f"(section 12), both directions; PV incremental conductance left of / at / right of the MPP = "
          f"{R['stab'][N]['g_rel']['left']:.2f} / 1 / {R['stab'][N]['g_rel']['right']:.2f} x I/V; CPL and CP source against "
          f"port A {Pn['CA']*1e6:.1f} uF / port B {Pn['CB']*1e6:.1f} uF; battery {A('batt_R')*1e3:.0f} mOhm / "
          f"{A('batt_L')*1e6:.1f} uH). Closed-loop eigenvalues: {s['n_unstable']} unstable.\n")
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
        nf = R["stab"][N]["noff"]
        a("\nWithout the load-current feed-forward (full-load corners): " + "; ".join(
            f"CPL on {u[2].upper()}: {sum(1 for x in nf if x['use'] == u and not x['stable'])} of 16 unstable" +
            "".join(f" ({x['va']:.0f}/{x['vb']:.0f} V)" for x in nf if x["use"] == u and not x["stable"]) +
            (f", stable ones PM >= {min(x['pm'] for x in nf if x['use'] == u and x['stable']):.1f} deg"
             if any(x["use"] == u and x["stable"] for x in nf) else "") for u in ("cvb_cpl", "cva_cpl")) +
          f" (with it: >= {s['pm_v']['pm_v']:.1f} deg). The feed-forward stays: it is what holds the margin at 250 V.\n")
    lr = R["stab"][3]["lrob"]
    a("Current loop against the inductance (3 cells; at 250->1000, 1000->250 and 550/550 V; L(I) at 0 A, at 45 A and at the "
      f"{P['i_trip']:.1f} A trip, times the factor) and against the computation delay (one sample = the design, two = the "
      "duty loaded a sample later):\n")
    a("| delay | L | PM [deg] | GM [dB] | crossover [Hz] |")
    a("|---|---|---|---|---|")
    for d in (1, 2):
        for lab, sel in ((f"L(45 A) x 0.92", lambda x: x["fac"] == 0.92 and x["i_at"] == P["i_phase_max"]),
                         ("L(45 A) nominal", lambda x: x["fac"] == 1.0 and x["i_at"] == P["i_phase_max"]),
                         ("L(45 A) x 1.08", lambda x: x["fac"] == 1.08 and x["i_at"] == P["i_phase_max"]),
                         ("all: L(0 A) / L(45 A) / L(trip) x 0.90-1.10", lambda x: True)):
            xs = [x for x in lr if x["delay"] == d and sel(x)]
            a(f"| {d} sample{'s' if d > 1 else ''} | {lab} | {min(x['pm'] for x in xs):.1f}-{max(x['pm'] for x in xs):.1f} | "
              f"{min(x['gm'] for x in xs):.1f}-{max(x['gm'] for x in xs):.1f} | {min(x['fc'] for x in xs):.0f}-"
              f"{max(x['fc'] for x in xs):.0f} |")
    dl = R["stab"][3]["dly"]
    a(f"\nOver all {dl[1]['cases']} current-loop corners of the sweep (L(I) at the operating point): one sample "
      f"{dl[1]['pm'][0]:.1f}-{dl[1]['pm'][1]:.1f} deg, GM >= {dl[1]['gm']:.1f} dB; two samples {dl[2]['pm'][0]:.1f}-"
      f"{dl[2]['pm'][1]:.1f} deg, GM >= {dl[2]['gm']:.1f} dB. A fixed gain copes with the soft saturation (no scheduling); "
      "the one-sample computation delay is a firmware REQUIREMENT (with two the loop is below the 45 deg rule and would need "
      "a lower crossover).\n")
    rd = R["stab"][3]["red"]
    ex = {d: [x for x in R["stab"][3]["lrob"] if x["delay"] == d and x["i_at"] == P["i_phase_max"] and x["fac"] in
              (0.92, 1.0, 1.08)] for d in (1, 2)}
    a("Cross-check with the review's reduced model (PCM-02: 1/(sL + R) with an exact ZOH, the same PI, the drawn 0.81 us "
      "chain as an uncompensated lag, ideal ports, L(45 A) x 0.92-1.08): " + "; ".join(
          f"{d} sample{'s' if d > 1 else ''} {min(p for p, _ in rd[d]):.1f}-{max(p for p, _ in rd[d]):.1f} deg at "
          f"{min(f for _, f in rd[d]):.0f}-{max(f for _, f in rd[d]):.0f} Hz against {min(x['pm'] for x in ex[d]):.1f}-"
          f"{max(x['pm'] for x in ex[d]):.1f} deg at {min(x['fc'] for x in ex[d]):.0f}-{max(x['fc'] for x in ex[d]):.0f} Hz "
          "here" for d in (1, 2)) + ". The exact sampled model carries the port banks and sources, the modulator "
      "sensitivities and the sampled V_A / V_B feed-forward, which lower its loop gain near crossover and with it the "
      "crossover itself: with one sample the two agree, with two the exact model keeps more phase because the extra sample "
      "costs less at the lower crossover. Both put two samples below 45 deg at the low-inductance end.\n")
    nt = R["stab"][3]["notes"]
    a(f"Notes: the worst constant-power-load corner is {where(nt['worst'])}: {nt['i_cpl']:.0f} A at {nt['v_cpl']:.0f} V on "
      f"port {nt['port']} against {nt['C']*1e6:.1f} uF (unstable pole {nt['f_cpl']:.0f} Hz). The margin there is set by how fast the "
      "feed-forward path (port-current sensor, sample, inner loop) cancels the negative conductance, not by the voltage "
      f"filter: moving the divider pole from {1e-3/(2*math.pi*P['tau_rc']):.1f} kHz to 20 kHz changes the PM there from "
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
      f"reading). With the drawn sensor after the 2-point calibration (gain drift <= {P['sens_gain_err']*100:.1f} %, linearity "
      f"{P['sens_off_err_A']:.2f} A, PV-PWR design check) every cell stays within "
      f"{max(r['dev_pct'] for r in R['share'] if r['label']=='calibrated'):.1f} % of 45 A; uncalibrated parts (2 %, 1 A) give "
      f"{max(r['dev_pct'] for r in R['share'] if r['label']=='uncalibrated'):.1f} %. The per-cell limits act on the sensor "
      "reading, so a cell with a low-reading sensor carries up to gain x limit + offset more (about +0.9 A) - inside the "
      f"inductor and device ratings (normal peak {P['i_peak_normal']:.1f} A, local window from {ch['oc_local'][2]:.1f} A).\n")
    a("![sharing](sharing.png)\n")
    rv = R["rev"]
    a("## 7. Bidirectional operation and power reversal\n")
    a(f"- Current command +40 -> -40 A per cell in 4 ms at 800->600 V (through zero, where the dead-time voltage changes "
      f"sign and the ripple crosses zero): max sampled-current error {rv['ramp']['err_max']:.2f} A, RMS {rv['ramp']['err_rms']:.2f} A.")
    a(f"- Bus forming on port B at 750 V (battery on A at 700 V), constant-power load ramped +75 kW -> -75 kW in 2 ms "
      f"(inverter reverses): V_B stays within {rv['cpl']['vb_min']:.1f}..{rv['cpl']['vb_max']:.1f} V; peak instantaneous i_L "
      f"{rv['cpl']['i_peak']:.1f} A; final per-cell current {rv['cpl']['i_end']:.1f} A.\n")
    a("![reversal](reversal.png)\n")
    a("## 8. Start-up sequence (cost-first ports, contactors, soft start, MPPT)\n")
    tpc, vpc, t_pv = su["pv_charge"]
    pb = su["pre_b"]
    a(f"1. K_A closes the PV array (17s x 8p, 300 W/m2, V_oc {su['voc']:.0f} V) straight onto the {P['CA']*1e6:.1f} uF port-A "
      f"bank - the cost-first PV port has no precharge (D-044 / D-045): the array, a current source, charges it to 99 % of "
      f"V_oc in {t_pv*1e3:.1f} ms. The contactor's closing transient from the array's own capacitance is the port design's "
      "open item (port_spec lean pv_make).")
    a(f"2. Port B precharges from the battery ({su['vbat']:.0f} V) through {su['r_pre']:.0f} ohm (port design, port_spec lean: "
      f"tau {pb['tau']*1e3:.0f} ms, done in {pb['t_done']*1e3:.0f} ms, {pb['E']:.0f} J); the hardware dV interlock "
      f"({su['dv_band'][0]:.1f}-{su['dv_band'][1]:.1f} V) lets K_B close, inrush {pb['i_pk']:.0f} A at the band top.")
    a(f"3. Gate enable at a carrier zero with the feed-forward duties (zero inductor voltage): the largest cell current in "
      f"the first two periods is {su['i_after_enable']:.2f} A (no inrush).")
    a(f"4. V_A reference = measured V_oc, MPPT steps down with {pvm.MPPT['dv_max_rel']*100:.0f} % steps, reference ramp 2000 V/s: "
      f"after 0.45 s the module delivers {su['eta_start']*100:.2f} % of P_mpp ({su['p_end']/1e3:.2f} of {su['pmpp']/1e3:.2f} kW).\n")
    a("![startup](startup.png)\n")
    a("## 9. Protection: requirements derived by simulation, checked against the trips as drawn (PV-CTL / PV-PWR)\n")
    a("### 9.1 Over-voltage after load rejection\n")
    rc = pr["rej_ctrl"]
    a(f"- With the control acting (MPPT at the 82.5 kW limit, bus at 1000 V opens): the V_B-max limit loop with "
      f"load-current feed-forward is selected within one outer sample; V_B peaks at **{rc['vb_peak']:.1f} V** and settles at "
      f"1010 V; firmware OV ({P['v_ov_sw']:.0f} V) not reached{'' if not rc['trip'] else ' - TRIPPED: ' + rc['trip']}.")
    a(f"- Bus forming on B at 950 V, 82.5 kW constant-power load dropping to zero: peak {pr['rej_vb']['vb_peak']:.1f} V.")
    a(f"- Protection only (controller output frozen, inner current loops alive, firmware OV disabled), two cases with port B "
      f"{P['CB']*1e6:.1f} uF: (a) 82.5 kW from the PV array at V_B 1000 V - V_B crosses 1100 V at {rq['ov_power']['slope']:.2f} "
      f"V/us; (b) the cells held at their limit from a stiff port A at 1000 V with V_B at 611 V - the steepest crossing, "
      f"{rq['ov_current']['slope']:.2f} V/us.")
    a(f"- Limit: last switching event at <= {rq['v_dev_lim']:.0f} V (0.85 x 1700 V with the {rq['os_v']:.0f} V turn-off ringing "
      f"(cell_spec) scaled to the bus voltage); the film bank's 1.15 x 1100 V = {rq['v_cap_lim']:.0f} V is not binding. Derived "
      f"requirement (an ideal detector at 1100 V on the measured voltage plus delay, table): **gates off <= "
      f"{rq['ov_power']['t_max_us']:.0f} us after the true 1100 V crossing in case (a), <= {rq['ov_current']['t_max_us']:.0f} us "
      "in case (b)**.\n")
    a("| ideal detector at 1100 V + added delay [us] | (a) gates off after crossing [us] | (a) V at gates off [V] | "
      "(b) gates off after crossing [us] | (b) V at gates off [V] |")
    a("|---|---|---|---|---|")
    for ra, rb in zip(pr["ov"]["power"]["curve"], pr["ov"]["current"]["curve"]):
        a(f"| {ra['extra_us']:.0f} | {fmt(ra['t_resp_us'])} | {fmt(ra['v_off'])} | {fmt(rb['t_resp_us'])} | {fmt(rb['v_off'])} |")
    a(f"\nAs drawn: the comparator sees the port voltage behind the divider pole ({ch['f_div_kHz']:.1f} kHz, a lag of "
      f"{rq['ov_detect_lag_us']:.0f} us on these ramps); the rest of PV-CTL's {ch['ov_hw_lag_us']:.0f} us lag (overdrive build-up, "
      f"comparator, logic, driver: {ch['ov_hw_lag_us'] - ch['ov_rc_us']:.1f} us) is applied as a fixed delay - exact at PV-CTL's "
      f"design slope, conservative on a steeper ramp. The PPB backup converts every {ch['ppb_period_us']:.0f} us.\n")
    a("| path | threshold [V] | (a) gates off after crossing [us] | (a) V at gates off [V] | (b) gates off after crossing [us] "
      "| (b) V at gates off [V] |")
    a("|---|---|---|---|---|---|")

    def vv(r):
        return fmt(r["v_off"]) + ("" if r["v_off"] <= rq["v_dev_lim"] else " **over**")
    for ra, rb in zip(pr["ov"]["power"]["built"], pr["ov"]["current"]["built"]):
        a(f"| {ra['path']} | {ra['thr']:.0f} | {fmt(ra['t_resp_us']) if ra['t_resp_us'] is not None else 'before 1100 V'} | "
          f"{vv(ra)} | {fmt(rb['t_resp_us']) if rb['t_resp_us'] is not None else 'before 1100 V'} | {vv(rb)} |")
    o, oc_ = rq["ov_power"], rq["ov_current"]
    cbn = rq["cb_needed_uF"]
    a(f"\n- **Comparator at its upper band edge ({ch['ov_hw'][2]:.0f} V): gates off at {o['hi']['v_off']:.1f} V in case (a) and "
      f"{oc_['hi']['v_off']:.1f} V in case (b) -> {'MET' if o['meets'] and oc_['meets'] else 'NOT MET'}** against "
      f"{rq['v_dev_lim']:.0f} V. Its whole lag may be up to {o['lag_max_us']:.0f} us (a) / {oc_['lag_max_us']:.0f} us (b); "
      f"drawn {ch['ov_hw_lag_us']:.0f} us.")
    pfb = json.load(open(need(MODULE_SPEC)))["costfirst"]["modules"][P["module"]]["port_film_bank"]["B"]
    c_part = pfb["C_min_uF"] / pfb["parts_min"]
    a(f"- **Port-B bank:** the smallest bank that keeps the gates-off voltage <= {rq['v_dev_lim']:.0f} V with the comparator "
      f"at its upper band edge is {cbn['power']:.0f} uF (a) / {cbn['current']:.0f} uF (b); drawn {P['CB']*1e6:.1f} uF "
      f"({pfb['parts_today']} x {c_part:.0f} uF + the leg decoupling films). The power-stage figure that sized the "
      f"{pfb['parts_min']}th capacitor (D-056; module_spec cost-first port_film_bank B) is {pfb['need_uF']['load_rejection']:.1f} "
      f"uF, counted there against the {c_part:.0f} uF parts alone (decoupling films left out), from a lumped "
      f"{min(P['N'] * P['imax'], port_limit(P, 'battery')):.0f} A into port B for the "
      f"whole {ch['ov_hw'][3]:.0f} us response. Here the frozen cells hold their inductor current while V_B rises past V_A, so "
      "in boost the port-B current N D_B i_L falls with D_B = V_A / V_B and the bank needs less. Without the "
      f"{pfb['parts_today']}th capacitor the bank would be {P['CB']*1e6 - c_part:.1f} uF with the decoupling films, above both "
      f"this model's {max(cbn.values()):.0f} uF and the lumped {pfb['need_uF']['load_rejection']:.1f} uF: a possible saving, "
      "for the owner to weigh against the margin (D-056 item 1 stands until then).")
    a(f"- PPB backup alone (comparator failed): {o['bk_nom']['v_off']:.0f} V at its nominal threshold, {o['bk_hi']['v_off']:.0f} V "
      f"at its upper band edge -> {'within' if o['bk_meets'] else 'above'} the 0.85 rule; a second line only, and the "
      f"{P['v_ov_sw']:.0f} V firmware trip on the outer-loop sample acts before either in any case where the firmware runs.\n")
    a("### 9.2 Short circuits\n")
    a("| case | trips in force | i_L at gates off [A] | peak i_L in the window [A] | first trip | detected after short [us] | "
      "gates off after short [us] | upper band -> 50 % L0, no trip: local / backup [us] |")
    a("|---|---|---|---|---|---|---|---|")
    for r in pr["shorts"]:
        a(f"| {r['label']} | {r['variant']} | {fmt(r['i_at_off'])} | {r['i_peak']:.1f} | {r.get('first', '-')} | "
          f"{fmt(r.get('t_detect_us'))} | {fmt(r.get('t_off_us'))} | {fmt(r.get('t_local_to_sat_us'))} / "
          f"{fmt(r.get('t_backup_to_sat_us'))} |")
    a(f"\n- Terminal shorts (5 mOhm / 1 uH bolted, at full current): first trip {', '.join(rq['term_first'])}, gates off "
      f"<= {rq['term_t_off_us']:.1f} us after the short, peak inductor current {rq['term_peak']:.0f} A. No reverse clamp is "
      f"fitted (D-044): the bank rings below zero and the legs' body diodes take the external branch's current - up to "
      f"{rq['rev_module_A']/1e3:.1f} kA per module, {rq['body_dev_A']:.0f} A per device ({rq['body_I2t']:.1f} A2s) against "
      f"I_DM {rq['i_dm_position']:.0f} A per position in this static lumped model; the architecture accepts that the "
      "port-side legs are likely destroyed (ARCHITECTURE-COSTFIRST 6.4).")
    a(f"- Bank shorts (inside the module, not seen by the port shunt): the inductor current rises at up to "
      f"{rq['oc_didt_max']:.1f} A/us. From the local window's upper band edge ({ch['oc_local'][3]:.1f} A) it reaches "
      f"{P['i50']:.0f} A (L = 50 % L0) in {rq['oc_t_max_local_us']:.2f} us: **local layer as drawn {rq['oc_local_t_us']:.2f} us "
      f"-> peak {rq['oc_local_peak']:.0f} A, {'met' if rq['oc_local_peak'] < P['i50'] else 'NOT met'}.** From the CMPSS "
      f"backup's upper band edge ({ch['oc_backup'][2]:.1f} A) {rq['oc_t_max_backup_us']:.2f} us remain: **backup as drawn "
      f"{rq['oc_backup_t_us']:.2f} us -> {rq['oc_backup_peak']:.0f} A, {'met' if rq['oc_backup_peak'] < P['i50'] else 'NOT met'}** "
      f"against {P['i50']:.0f} A and the {rq['i_dm_position']:.0f} A device pulse rating per position (current when the "
      f"gates go off). Without the reverse clamp the bank rings below zero in these cases and the reversed bank keeps "
      f"driving the inductor current up after the gates are off, through the body diodes: up to "
      f"{rq['oc_after_off_peak']:.0f} A inside the 0.2 ms window (static diode model) - part of the accepted loss of the "
      "port-side legs (ARCHITECTURE-COSTFIRST 6.4), not a trip-timing result.")
    a("- A short on port A while power flows A->B reverses the current: every layer is a window (+/-).")
    a("- After the trip the inductor current freewheels through two body diodes (2 x 2.9 V against it) and decays.")
    d = rq["desat"]
    a(f"\n### 9.3 Short inside a cell (leg shoot-through, switch-node or inductor short)\n\nNot seen by the inductor sensor "
      f"when the inductor is bypassed: the gate driver's DESAT is the protection ({P['driver']}, gate drive as drawn on "
      f"PV-PWR). Blanking {d['blank_range_us'][0]:.2f}-{d['blank_range_us'][1]:.2f} us + filter {d['filter_range_us'][0]:.2f}-"
      f"{d['filter_range_us'][1]:.2f} us after the device leaves saturation, then the short-circuit booster has the gates off "
      f"{d['off_after_detect_us']:.2f} us later (required <= {fmt(d['required_detect_to_off_us'], 1)} us; the driver's soft "
      f"turn-off alone {fmt(d['soft_off_only_us'], 2)} us): **{d['typ_us']:.2f} us typ / {d['max_us']:.2f} us worst** against "
      f"a short-circuit withstand of {d['scwt_1100_us']:.1f} us that is an ASSUMPTION (no Chinese datasheet states one): "
      f"{'met' if d['max_us'] < d['scwt_1100_us'] else 'NOT met'} with {d['scwt_1100_us']-d['max_us']:.2f} us margin if the "
      f"assumption holds. The DESAT threshold {d['vth'][0]:.2f}-{d['vth'][1]:.2f} V corresponds to {d['desat_A_25C'][0]:.0f}-"
      f"{d['desat_A_25C'][1]:.0f} A (25 C) and {d['desat_A_175C'][0]:.0f}-{d['desat_A_175C'][1]:.0f} A (175 C) per position over "
      "both device options: DESAT is a short-circuit detector only; the inductor OC layers are the over-current protection.\n")
    cl, pl = pr["cell_loss"], pr["pv_loss"]
    a("### 9.4 Loss of one cell at full load\n")
    a(f"MPPT on a 17s x 9p array at 1000 W/m2 (module at its 82.5 kW limit, i.e. every cell at its own 27.5 kW), cell 3 "
      f"faults at 3 ms: the references are re-divided by the healthy cells, which stay at their own limits (sampled "
      f"average max {cl['i_samp_max']:.1f} A, instantaneous peak {cl['i_peak']:.1f} A, below the local trip band); module power "
      f"{cl['p_before']/1e3:.1f} -> {cl['p_after']/1e3:.1f} kW and V_A moves right on the PV curve to {cl['va_max']:.0f} V; no trip"
      f"{'' if not cl['trip'] else ' (TRIP: ' + cl['trip'] + ')'}. Below full load the healthy cells take over the lost share "
      "up to the same per-cell limits.\n")
    a("### 9.5 Loss of the PV source\n")
    a(f"Array disconnected at 82.5 kW: V_A falls until the V_A loop (300 Hz, reference clamped >= 0, so no reverse power "
      f"into port A) has cut the current: minimum V_A {pl['va_min']:.0f} V (UV stop {P['v_uv']:.0f} V "
      f"{'not reached' if not pl['trip'] else 'reached: ' + pl['trip']}), V_B max {pl['vb_max']:.1f} V. The supervisor "
      "declares 'PV lost' on zero power with the reference not followed and stops; at an operating point below ~450 V the "
      "same dip reaches the UV stop, which is the intended outcome.\n")
    a("### 9.6 Firmware second layer (described, not simulated)\n")
    a("PV-CTL's design check writes the firmware layer on top of each hardware trip (CMPSS / PPB backups, soft limits, "
      "over-temperature derating, heartbeat, latch-clear rules, configuration lock, clock loss); it is carried into "
      "control_spec.json `firmware_second_layer` together with the lean ports' rules (polarity and dV enables, port OC "
      "latency).\n")
    a("![protection](protection.png)\n")
    a("## 10. Requirements on sensing and protection hardware\n")
    a("| signal / trip | requirement | as drawn | met |")
    a("|---|---|---|---|")
    m = R["meas"]
    ci = m["cell_inductor_current"]
    a(f"| cell i_L chain | +/-{A('i_sense_fs_A'):.0f} A, >= {A('f_isense_Hz')/1e3:.0f} kHz, delay to the CMPSS input <= "
      f"{ci['group_delay_us_max']:.2f} us, <= {A('res_il_req_A_per_LSB'):.4f} A/LSB | {ch['sensor']} + PV-CTL AFE: "
      f"{ch['t_il_us']:.2f} us, -3 dB {ch['f_il_kHz']:.0f} kHz, {ch['lsb_il']:.4f} A/LSB | "
      f"{'yes' if ch['t_il_us'] <= ci['group_delay_us_max'] and ch['f_il_kHz'] >= A('f_isense_Hz') / 1e3 else 'NO'} |")
    a(f"| IL local window | gates off <= {rq['oc_t_max_local_us']:.2f} us after {ch['oc_local'][3]:.1f} A | "
      f"{ch['oc_local'][4]:.2f} us, peak {rq['oc_local_peak']:.0f} A | {'yes' if rq['oc_local_peak'] < P['i50'] else 'NO'} |")
    a(f"| IL CMPSS backup | gates off <= {rq['oc_t_max_backup_us']:.2f} us after {ch['oc_backup'][2]:.1f} A | "
      f"{ch['oc_backup'][3]:.2f} us, peak {rq['oc_backup_peak']:.0f} A | {'yes' if rq['oc_backup_peak'] < P['i50'] else 'NO'} |")
    a(f"| port OV comparator (a, 82.5 kW) | gates off <= {o['t_max_us']:.0f} us after the true 1100 V crossing | "
      f"{fmt(o['hi']['t_resp_us'])} us at the {ch['ov_hw'][2]:.0f} V band edge, {o['hi']['v_off']:.0f} V | "
      f"{'yes' if o['meets'] else 'NO'} |")
    a(f"| port OV comparator (b, frozen at the limit) | <= {oc_['t_max_us']:.0f} us | {fmt(oc_['hi']['t_resp_us'])} us, "
      f"{oc_['hi']['v_off']:.0f} V | " + ("yes |" if oc_['meets'] else f"NO (threshold <= {oc_['thr_max_V']:.0f} V or lag <= "
                                                                          f"{oc_['lag_max_us']:.0f} us) |"))
    a(f"| port OV PPB backup | second line only | {o['bk_hi']['v_off']:.0f} V at the upper band edge | "
      f"{'within the 0.85 rule' if o['bk_meets'] else 'above the 0.85 rule, below the rating'} |")
    a(f"| port-B bank for load rejection | >= {max(cbn.values()):.0f} uF (this model) | {P['CB']*1e6:.1f} uF | "
      f"{'yes' if P['CB'] * 1e6 >= max(cbn.values()) else 'NO'} |")
    a(f"| V_A / V_B signal | >= 3 kHz, <= {A('res_v_req_V_per_LSB'):.3f} V/LSB per outer sample | {ch['f_div_kHz']:.1f} kHz pole, "
      f"{P['lsb_v']:.3f} V/LSB raw (PV-CTL oversamples 16 x) | yes |")
    a(f"| port OC (I_A, I_B) | terminal short off before the inductor current leaves its normal range | gates off "
      f"{rq['term_t_off_us']:.1f} us after the short, {rq['term_peak']:.0f} A | "
      f"{'yes' if rq['term_peak'] < ch['oc_local'][2] else 'NO'} |")
    a(f"| I_A / I_B for feed-forward | bus port, >= 10 kHz | lean shunt {ch['f_ip_kHz']:.1f} kHz (lag {P['tau_ip']*1e6:.1f} us) | "
      f"{'yes' if ch['f_ip_kHz'] >= 10.0 else 'NO'} |")
    a(f"| DESAT + booster | gates off within the withstand ({d['scwt_1100_us']:.1f} us ASSUMED) | {d['typ_us']:.2f} us typ / "
      f"{d['max_us']:.2f} us worst | {'yes, if the assumed withstand holds' if d['max_us'] < d['scwt_1100_us'] else 'NO'} |")
    a("| MPPT power measurement | see sim/out/pv_mppt/report.md | | |")
    a("\n## 11. Synchronous-sampling ripple (port A)\n")
    att = 3 * P["fsw"] / ch["f_div_kHz"] / 1e3
    for x in R["ripple"]:
        a(f"- {x['va']:.0f}->{x['vb']:.0f} V at {x['i']:.1f} A/cell: V_A ripple {x['va_pp']:.3f} V pp, sample minus period mean "
          f"at the capacitor {x['bias_raw']*1e3:.1f} mV; behind the {ch['f_div_kHz']:.1f} kHz divider pole the "
          f"{3*P['fsw']/1e3:.0f} kHz ripple is attenuated ~{att:.0f}x, so the ADC bias is negligible.")
    env = R["env"]
    a("\n## 12. Limits and the delivered-power envelope\n")
    a("### 12.1 Which limit is which\n")
    csx = json.load(open(need(CELL_SPEC)))
    mcf = json.load(open(need(MODULE_SPEC)))["costfirst"]["modules"][P["module"]]
    ix, tj, hs = csx["inductor"], mcf["tj_max_full_power_C"], mcf["inductor_hot_spot_full_power_C"]
    a(f"- **Inductor (phase) current**, the only limit on the inductor itself: {P['i_phase_max']:.1f} A mean in buck / boost "
      f"(one leg switching, ripple up to {ix['requirement']['ripple_pp_max_A']:.1f} A pp: the inductor's DC design current, "
      f"normal peak {P['i_peak_normal']:.1f} A; 1.10 x {P['i_peak_normal']:.1f} = {1.1 * P['i_peak_normal']:.1f} A against the "
      f"{ch['oc_local'][2]:.1f} A window edge: " + ("clear" if 1.1 * P["i_peak_normal"] <= ch["oc_local"][2] else
                                                    f"**short by {1.1 * P['i_peak_normal'] - ch['oc_local'][2]:.1f} A**, the "
                                                    "window's own design-margin item on PV-CTL (it stays above the normal "
                                                    "peak); not a consequence of the band limit") +
      f"); **{P['i_phase_max_band']:.2f} A in the band** (both legs at or near "
      f"D_max = {P['Dmax']:.5f}: almost no ripple; the inductor's rms design value I_rms_max, which is the power stage's band "
      f"point: P_lim at unity ratio = {P['imax']:.0f} A per cell at the port = {P['imax'] / P['Dmax']:.2f} A in the inductor). "
      "sim/pv_design.py and sim/pv_module.py evaluate the band corners at that current, so nothing in the hardware was sized "
      f"for less: devices {csx['worst_case_stresses']['device_Irms_per_position_A']:.1f} A rms per position (= I_rms_max x "
      f"sqrt(D_max)); inductor DC copper loss at the band limit {P['i_phase_max_band']**2 * ix['DCR_110C_mOhm'] * 1e-3:.1f} W "
      f"(R_dc at 110 C of the rev M2 winding; the small band ripple adds little) against the {ix['loss_worst_W']:.1f} W of "
      f"its worst point ({ix['loss_worst_at'][0]:.0f} / {ix['loss_worst_at'][1]:.0f} V, full ripple), whose hot spot "
      f"({hs['45C']:.1f} C at 45 C inlet, module model at {hs['at']}) sets the inductor limit; hottest junction of the "
      f"module model {tj['45C']:.1f} C at {tj['at_45C']} full power (45 C inlet, "
      f"limit {tj['limit_C']:.0f} C)" + (", a band point at this current" if duties_ss(
          P, *(float(x) for x in tj["at_45C"].split("->")))[2] == "band" else "") + ". Band peak at that current: 12.2.")
    a(f"- **Port currents** (lean ports, module_spec cost-first, flat in inlet temperature): PV port A {P['i_port_lim']['pv']:.0f} A, "
      f"battery port B {P['i_port_lim']['battery']:.0f} A on PV-P75; {P4['i_port_lim']['pv']:.0f} A / "
      f"{P4['i_port_lim']['battery']:.0f} A on PV-P100/110 (the HPE501 aR pair at 65 C fuse air, D-056), enforced per cell "
      "as limit / (N x actual duty) with a slow trim on the measured port current.")
    a(f"- **Power**: {P['pmax']/1e3:.1f} kW per cell at the converter's port-A node (= the PV / source side for A->B, the "
      "delivered side for B->A); **thermal derating** (module_spec cost-first, thermal only, fraction of the point's P_max on "
      "the delivered power): PV-P75 " + ", ".join(f"{t:.0f} C {f:.3f}" for t, f in zip(*P['p_frac_inlet'])) +
      "; PV-P100/110 " + ", ".join(f"{t:.0f} C {f:.3f}" for t, f in zip(*P4['p_frac_inlet'])) +
      ". Firmware derates on the NTC readings (PV-CTL firmware layer); the control simulation has no thermal state, the "
      "envelope applies the fraction.")
    t_on = P["tmin"] - 2 * P["td_gates"][1] * 1e-9
    a(f"- **D_max = 1 - t_min f_sw = {P['Dmax']:.5f}**, the same in every mode: t_min {P['tmin']*1e6:.3f} us = "
      f"{t_on*1e6:.1f} us of complementary on-time + 2 x the {P['td_gates'][1]:.0f} ns longest dead time at the gates "
      f"(cell_spec min_pulse_basis). Raising it needs a shorter minimum pulse: with no complementary on-time at all it would be "
      f"{1 - 2 * P['td_gates'][1] * 1e-9 * P['fsw']:.4f}, and the dead-time stretch is a protection that stays (D-050). The "
      f"sampling plan has no room either: the i_L sample is taken {G['t_soc']*1e6:.2f} us after the carrier extremum (the "
      f"drawn chain delay) and its {A('t_sh_s')*1e9:.0f} ns S/H window closes {(P['tmin'] / 2 - G['t_soc'] - A('t_sh_s'))*1e9:.1f} ns "
      f"before the edge of a minimum pulse centred on that extremum ({P['tmin']/2*1e6:.3f} us); the switch node moves one "
      "driver delay later. A shorter pulse puts the edge into the window unless the sample moves earlier, which gives up "
      "the chain-delay compensation. The band limit, not D_max, is the lever.\n")
    lc = R["limits"]
    a("Averaged-model checks that each limit holds where it binds (command ramped beyond it): " + "; ".join(
        f"{c['N']} cells at {c['va']:.0f}/{c['vb']:.0f} V: {c['what']} {c['value']:.2f} against {c['limit']:.2f} "
        f"(peak {c['peak']:.2f})" for c in lc) + " (A for currents, W per cell for power).\n")
    bp = R["band_peak"]
    a(f"### 12.2 Band peak at the band limit\n\nOver the band as the transition sweeps of section 4 occupy it (V_B / V_A "
      f"{bp['r_span'][0]:.3f}-{bp['r_span'][1]:.3f}: the band is left late, by the hysteresis and the dead time, not at "
      f"D_max / 1/D_max), V 250-1000 V, current at the cell's band limit there and the inductance at its -{P['Ltol']*100:.0f} % "
      f"tolerance, the normal peak is at most **{bp['peak']:.1f} A** (ripple {bp['ripple']:.1f} A pp at {bp['va']:.0f} -> "
      f"{bp['vb']:.0f} V, {bp['i']:.1f} A): below the {P['i_peak_normal']:.1f} A design peak, and 1.10 x {bp['peak']:.1f} = "
      f"{1.1 * bp['peak']:.1f} A stays under the {ch['oc_local'][2]:.1f} A window edge; L at {bp['peak']:.0f} A is "
      f"{float(np.interp(bp['peak'], P['Li'], P['Lv']))*1e6:.0f} uH nominal (L(45 A) {P['Lnom']*1e6:.0f} uH). The trip "
      "window therefore needs no change for the band limit. Nor does the over-voltage case of 9.1 (b): the band limit binds "
      f"only up to V_A = {P['pmax'] / (P['Dmax'] * P['i_phase_max_band']):.0f} V (above it the node-A power does), so frozen "
      f"cells at the band limit push at most {P['N'] * P['pmax'] / (P['Dmax'] * P['v_ovp']):.0f} A (N i_band V_A / V_B) "
      f"into port B at the {P['v_ovp']:.0f} V crossing (boost, D_B = V_A / V_B), against "
      f"{P['N'] * P['i_phase_max'] * 1000.0 / P['v_ovp']:.0f} A in case (b) (1000 V in, {P['i_phase_max']:.0f} A); "
      "calculated from the steady-state duties.\n")
    a("### 12.3 Envelope (steady state; P_src = power drawn at the source port, P_del = delivered at the other port)\n")
    a("Direction A->B = PV / port A to port B; B->A = battery / port B to port A. The earlier law (45 A on the inductor in "
      "every mode) is shown for comparison at 45 C (before loss).\n")
    a("**PV-P75 (3 phases):**\n")
    env_table(a, env, ["PV-P75"])
    a("\n**PV-P100 / PV-P110 (4 phases, same build; the products differ in rating only):**\n")
    env_table(a, env, ["PV-P100", "PV-P110"])
    a("\nLowest V_A = V_B at which the rated power is delivered:\n")
    a("| product | rated [kW] | direction | " + " | ".join(f"{t:.0f} C" for t in A("env_inlet_C")) + " |")
    a("|---|---|---|" + "---|" * len(A("env_inlet_C")))
    for nm, e in env.items():
        for d_, vs in e["v_unity_min_for_rated_V"].items():
            a(f"| {nm} | {e['rated_kW']:.0f} | {d_} | " + " | ".join("not reached" if v is None else f"{v:.0f} V"
                                                                     for v in vs.values()) + " |")
    p75 = env["PV-P75"]["rows"]
    r550 = [r for r in p75 if r["va"] == 550.0 and r["vb"] == 550.0 and r["dir"] == "A->B"][0]
    p_r, i_pv = env["PV-P75"]["rated_kW"] * 1e3, P["i_port_lim"]["pv"]
    a(f"\n- PV-P75 at 550/550 V: {r550['p_src_kW_45C']:.2f} kW from the PV port ({r550['binding_45C']}), "
      f"{r550['p_del_kW_45C']:.2f} kW delivered; with the earlier {P['i_phase_max']:.0f} A inductor ceiling it was "
      f"{r550['p_src_kW_45A_law']:.2f} kW before loss. **{p_r/1e3:.0f} kW at exactly 550 V needs {p_r/550:.1f} A at the port "
      f"before loss ({p_r/(550*r550['eta_45C']):.1f} A at the source port with the module loss): PV-04 / PV-07 (full load from "
      f"550 V) and PV-05 / PV-08 ({i_pv:.0f} A) are inconsistent by {(p_r/550/i_pv - 1)*100:.1f} % at that corner** - "
      f"{i_pv*550/1e3:.2f} kW is the most a {i_pv:.0f} A port passes there.")
    a(f"- PV-P75's {3*P['pmax']/1e3:.1f} kW maximum is reached at the PV / source port (node A) for A->B and as delivered "
      f"power for B->A; delivered A->B it is {3*P['pmax']/1e3:.1f} kW less the module loss.")
    i_b4, p_max4 = P4["i_port_lim"]["battery"], env["PV-P110"]["rated_kW"] * 1e3
    eta4 = [r["eta_45C"] for r in env["PV-P110"]["rows"] if r["va"] == r["vb"] and r["va"] >= 750.0]
    a(f"- PV-P100/110: the {i_b4:.0f} A battery port binds below about {p_max4 / i_b4:.0f} V ({p_max4/1e3:.0f} kW) / "
      f"{env['PV-P100']['rated_kW'] * 1e3 / i_b4:.0f} V ({env['PV-P100']['rated_kW']:.0f} kW) before loss; in B->A the battery "
      f"is the source, so the delivered power also carries the module loss ({env['PV-P100']['rated_kW']:.0f} kW delivered needs "
      "the battery above the 'B->A' voltage in the table). The thermal derating (D-056) holds the 4-phase P_max only to the "
      f"inlet where its fraction is 1 ({max(t for t, f in zip(*P4['p_frac_inlet']) if f >= 1.0):.0f} C); PV-P100's "
      f"{env['PV-P100']['rated_kW']:.0f} kW holds to the inlets the table marks. **PV-P110 ({p_max4/1e3:.0f} kW = 4 x "
      f"{P['pmax']/1e3:.1f} kW) is never delivered A->B**: the {P['pmax']/1e3:.1f} kW per cell is held at the source (PV) "
      f"node, so {p_max4/1e3:.0f} kW drawn from the PV port delivers that less the module loss; B->A it is delivered above the "
      "battery voltage in the table, at the full-power inlets only. Either PV-P110's rating is a PV-input rating, or the "
      f"per-cell limit is referenced to the delivered side (+{(1/min(eta4) - 1)*100:.1f} % on the cells at the unity points "
      ">= 750 V, which the thermal derating then has to hold).")
    a("- The products are selected by phase count only through `load_params(N)`: N = 3 -> PV-P75 values, N = 4 -> PV-P100/110 "
      "values, anything else stops; no P75 constant is used for the 4-phase build.\n")
    a("## 13. Open items and honesty\n")
    a("- Nothing is bench-validated. Switches are ideal (no switching transients, no C_oss charging in the dead time - the "
      "real dead-time voltage near zero current is smaller and smoother than modelled), the cells are lumped on one port "
      "node (no busbar inductance between cells), sensors are first-order lags (the drawn i_L chain's 0.2 us transport delay "
      "is folded into one lag of its total delay), the PV array is static (no array capacitance), the inverter on port B is "
      "an ideal constant-power load, the battery is an EMF behind 32 mOhm / 3.5 uH.")
    a(f"- The {ch['sensor']} noise is specified only over DC-100 kHz ({ch['noise_mvpp']:.0f} mVpp); the AFE passes "
      f"{ch['f_il_kHz']:.0f} kHz and the crest factor pp / rms = {A('noise_pp_per_rms'):.0f} is ASSUMED. The sampled-current errors "
      "in sections 4 and 7 include this noise.")
    a("- The comparator delays inside PV-CTL's responses are that board's own estimates (the TLV9024 publishes no maximum "
      "delay; PV-CTL's design check states the factor it ASSUMES on the typical figure); the over-voltage layers apply "
      "PV-CTL's total lag on the filtered voltage.")
    a("- The one-sample computation delay is a REQUIREMENT on the F280039C firmware (architecture R-14, not yet timed); with "
      "two samples the current loop drops below 45 deg.")
    pmv = min(st[N]["pm_v"]["pm_v"] for N in (3, 4))
    a(f"- The outer-loop phase margin at the worst constant-power-load corner (risk E4) is {st[3]['pm_v']['pm_v']:.1f} deg on "
      f"PV-P75 / {st[4]['pm_v']['pm_v']:.1f} deg on PV-P100/110 (modulus margin {st[3]['mm_v']['mm_v']:.2f} / "
      f"{st[4]['mm_v']['mm_v']:.2f}) with the drawn banks: adequate, " +
      ("below" if pmv < 45.0 else "at or above") + " the 45 deg rule of thumb; the inverter's own DC link raises it.")
    a("- The envelope's delivered power uses sim/pv_module.py's published module efficiency grid (calculated, at each "
      "point's P_lim and 25 C inlet, A->B), corrected to the inlet with the largest 25 -> 45 C drop of module_spec's corners "
      "(linear beyond 45 C: ASSUMED) and used for B->A as well; the thermal derating is that script's thermal-only figure "
      "(an estimate) applied on top of the control's limits.")
    a("- Terminal shorts are not survivable on PV-PWR (no reverse clamp, D-044); the body-diode figures are a static model.")
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
    ch = P["chain"]
    lo_band = ch["oc_local"][2]
    e_n = 4.0 * P["sig_il"]                    # the sampled current carries the drawn sensor's noise: 4 sigma allowance
    for c in R["trans"]:                       # over every dead-time set: no chatter, no spike, bounded error
        assert all(m == 2 for m in c["mode_changes"]), ("one band entry + exit per sweep", c["set"], c["vb"], c["v0"], c["i_cmd"])
        assert c["i_peak"] < lo_band - 10.0, ("transition peak below the OC trip band", c["set"], c["vb"])
        assert c["err_max"] < (2.0 if c["vb"] == 550.0 else 3.0) + e_n, ("transition error", c["set"], c["vb"], c["v0"], c["i_cmd"])
    assert len({c["set"] for c in R["trans"]}) == len(DT_SETS_FR) and len(R["trans"]) == 12 * len(DT_SETS_FR)
    assert G["hyst"] >= 2.5 * 0.5 * (A("dt_range_ns")[1] - A("dt_range_ns")[0]) * 1e-9 * P["fsw"] - 1e-12, "hysteresis vs residual"
    for c in R["limits"]:                      # each limit holds where it binds, none of the others is exceeded
        assert abs(c["value"] / c["limit"] - 1) < 0.015 and c["peak"] < 1.03 * c["limit"] and c["over"] < 1.015, ("limit", c)
    assert next(c for c in R["limits"] if c["what"] == "phase")["value"] > 1.02 * P["i_phase_max"], "band limit in use"
    for z in R["zc"]:
        if z["comp"] == "ref":
            assert z["err_max"] < 4.0 + e_n and z["near_rms"] < 1.0, ("zero crossing, reference-driven compensation", z["set"],
                                                                      z["label"])
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
        assert R["stab"][N]["dly"][2]["pm"][0] < R["stab"][N]["dly"][1]["pm"][0], "a later duty load costs phase"
    for k, fac in enumerate((0.92, 1.0, 1.08)):  # the reduced-model cross-check as the report states it (section 5)
        ex = {d: [x["pm"] for x in R["stab"][3]["lrob"] if x["delay"] == d and x["fac"] == fac and
                  x["i_at"] == P["i_phase_max"]] for d in (1, 2)}
        rd = {d: R["stab"][3]["red"][d][k][0] for d in (1, 2)}
        assert all(abs(x - rd[1]) < 1.0 for x in ex[1]) and min(ex[2]) > rd[2], ("reduced vs exact current loop", fac)
        assert fac != 0.92 or (max(ex[2]) < 45.0 and rd[2] < 45.0), "two samples below 45 deg at the low-L end"
    rq, pr = R["req"], R["prot"]
    assert pr["rej_ctrl"]["vb_peak"] < P["v_ov_sw"] and not pr["rej_ctrl"]["trip"], "load rejection handled by the limit loop"
    for kind in ("power", "current"):          # model consistency: later detection -> higher voltage at gates off
        cur = pr["ov"][kind]["curve"]
        assert all(b["v_off"] > a_["v_off"] for a_, b in zip(cur, cur[1:])), ("OV curve monotonic", kind)
        o = rq["ov_" + kind]
        assert o["hi"]["v_off"] > o["nom"]["v_off"] and o["bk_hi"]["v_off"] > o["bk_nom"]["v_off"], ("band ordering", kind)
        assert o["meets"], ("the comparator at its upper band edge keeps the device rule with the drawn port-B bank", kind)
    assert max(rq["cb_needed_uF"].values()) <= P["CB"] * 1e6, "drawn port-B bank >= the bank the load rejection needs"
    assert rq["oc_local_peak"] < P["i50"] and rq["oc_backup_peak"] < P["i50"], "OC layers as drawn"
    assert rq["term_peak"] < lo_band, "terminal shorts: gates off before the inductor current leaves its normal range"
    assert pr["cell_loss"]["i_peak"] < lo_band and not pr["cell_loss"]["trip"]
    assert pr["cell_loss"]["i_samp_max"] < P["i_phase_max_band"] * (1 + P["sens_gain_err"]) + 0.5
    assert min(pr["pv_loss"]["lg"]["im"][pr["pv_loss"]["lg"]["t"] > 3e-3].mean(axis=1)) > -1.0, "no reverse power into the PV port"
    su = R["start"]
    assert su["i_after_enable"] < 2.0 and su["eta_start"] > 0.97, "start-up without inrush, MPPT reaches the MPP"
    # limits and envelope (PCM-01, PCM-13)
    bp = R["band_peak"]
    assert bp["peak"] <= P["i_peak_normal"] and 1.1 * bp["peak"] <= lo_band, "band peak inside the design peak and window"
    e75 = {(r["va"], r["vb"], r["dir"]): r for r in R["env"]["PV-P75"]["rows"]}
    r = e75[(550.0, 550.0, "A->B")]
    assert abs(r["p_src_kW_45C"] - port_limit(P, "pv") * 0.550) < 0.05, "PV-P75 at 550/550 V: the PV port, not the inductor"
    assert abs(r["p_src_kW_45A_law"] - 3 * P["Dmax"] * 0.550 * P["i_phase_max"]) < 0.05, "earlier law: 3 D_max 550 V x 45 A"
    assert "phase current" not in e75[(550.0, 550.0, "B->A")]["binding_45C"], "B->A: the phase limit is a delivered power"
    e4 = R["env"]["PV-P100"]
    v_rev = e4["v_unity_min_for_rated_V"]["B->A"]["45C"]
    assert v_rev > e4["rated_kW"] * 1e3 / e4["i_port_lim_A"]["battery"], "B->A rated power needs more than P / I_B (loss)"
    assert all(r["p_del_kW_45C"] <= r["p_src_kW_45C"] for x in R["env"].values() for r in x["rows"])
    for n in (2, 5):                            # no product is selectable with another build's constants
        try:
            load_params(n)
        except ValueError:
            continue
        raise AssertionError(f"load_params({n}) must stop")


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
    R["limits"] = study_limits(P)
    R["band_peak"] = band_peak(P, R["band_edges"])
    R["env"] = study_envelope()
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
    print(f"OV (82.5 kW): need <= {rq['ov_t_max_us']:.0f} us, comparator upper band {fmt(rq['ov_t_designed_us'], 0)} us "
          f"({rq['ov_v_off_designed']:.0f} V); frozen at the limit: need <= {rq['ov_current']['t_max_us']:.0f} us, have "
          f"{fmt(rq['ov_current']['hi']['t_resp_us'], 0)} us ({rq['ov_current']['hi']['v_off']:.0f} V); port-B bank needed "
          f"{max(rq['cb_needed_uF'].values()):.0f} uF, drawn {P['CB']*1e6:.1f} uF")
    print(f"OC: local {rq['oc_local_t_us']:.2f} us -> {rq['oc_local_peak']:.0f} A, backup {rq['oc_backup_t_us']:.2f} us -> "
          f"{rq['oc_backup_peak']:.0f} A; terminal short: {rq['term_first']} off at {rq['term_t_off_us']:.1f} us, body diode "
          f"{rq['body_dev_A']:.0f} A/device (no clamp)")
    e = {(r["va"], r["vb"], r["dir"]): r for r in R["env"]["PV-P75"]["rows"]}[(550.0, 550.0, "A->B")]
    print(f"limits: phase {P['i_phase_max']:.1f} A (buck/boost) / {P['i_phase_max_band']:.2f} A (band, peak "
          f"{R['band_peak']['peak']:.1f} A); PV-P75 550/550 V A->B {e['p_src_kW_45C']:.2f} kW at the source (earlier law "
          f"{e['p_src_kW_45A_law']:.2f}), {e['p_del_kW_45C']:.2f} kW delivered")
    print(f"pv_control self-check passed ({time.time()-t0:.0f} s)")


if __name__ == "__main__":
    run()
