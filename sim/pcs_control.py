"""PCS-P125 inverter control study: current loop, PLL, DC-link loop, grid forming, firmware and protection state machine.

Closes the sampled-loop part of review finding PCM-21 / open item E1 (REQUIREMENTS AC-01, AC-02, SRC-7).
Run:  caffeinate -i .venv/bin/python sim/pcs_control.py
In:   read at run time, never copied: sim/out/pcs_design/pcs_spec.json (LCL, sampling, current tiers, DC link, sequencing),
      sim/out/magnetics/design_pcs_{l1,l2,cm_choke}.json (L(I), -10 % part, R_dc, DM leakage), hardware/PCS-{PWR,CTL}/
      outputs/*_design_check.txt (sensor chains, windows, dead time, ADC plan), gen/data/pcs_ctrl_pin_plan.csv (ADC pins,
      routes), sim/dab_control.py (its PCS DC-link assumption, read only), bom/{PCS-PWR,PCS-CTL,PCS-P125_module}_BOM.csv
      (divider / charge-bucket tolerances, the controller oscillator), gen/pv_ctrl.py (ADC_RC, the charge bucket PCS-CTL re-uses);
      sim/pcs_design.py imported for its exact PWM Fourier series and modulation policy (THDu, section 8b).  The phase-current
      sensor frozen by the design study (STK-250HO/4) is read from pcs_spec when present, else ASSUME['sensor'], labelled.
Out:  sim/out/pcs_control/{report.md, pcs_control_spec.json, bode_scr.png, steps.png, faults.png, pll_weak_grid.png, gfm.png,
      vf_and_ride_through.png}
Sections added for D-074's open items: 4b ride-through limiter, 7b VF secondary restoration and accuracy, 8b THDu, 8c output
imbalance, 9b four-wire neutral leg and per-phase loops.

CALCULATED / SIMULATED, not measured.  One plant, two views:
  * per phase (alpha-beta; three-wire, so the C_f star on the DC midpoint carries common mode only): L1 + R_dc, C_f with the
    R_d-C_d branch, L2 + R_dc + the CM choke's DM leakage, a Thevenin grid (SCR, R/X) or an island load, current and voltage
    sensor chains as first-order lags.  Discretised exactly at the double-update rate: zero-order hold on the converter
    voltage (the regular-sampled PWM average), the exact integral of the rotating grid voltage, one period of computation
    delay (1.5 T_s with the hold).
  * linear: loop gain broken at the modulator, closed-loop eigenvalues and responses (alpha-beta, exact for the sampled
    averaged model).
  * nonlinear: the same sampled matrices rotated into the synchronous frame, with the PLL, reference and modulation limiters,
    anti-windup, droop, virtual impedance and a DC link with a constant-power DC/DC; run sample by sample, and linearised
    numerically (Newton equilibrium, central differences) for the PLL, DC-link and grid-forming eigenvalues and loop gains.
  Averaged: no switching ripple (+ripple/2 is added to every peak), dead time only as a voltage disturbance (estimate).
"""
import cmath
import csv
import itertools
import json
import math
import os
import re
import time

import numpy as np
from scipy.linalg import expm
from scipy.signal import cont2discrete, tf2ss

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out", "pcs_control")
SPEC = os.path.join(HERE, "out", "pcs_design", "pcs_spec.json")
MAG = os.path.join(HERE, "out", "magnetics", "design_pcs_%s.json")
CHK = os.path.join(ROOT, "hardware", "PCS-%s", "outputs", "PCS-%s_design_check.txt")
PINS = os.path.join(ROOT, "gen", "data", "pcs_ctrl_pin_plan.csv")
BOM = os.path.join(ROOT, "bom", "%s.csv")                   # drawn BOMs: oscillator, divider and charge-bucket tolerances
PVCTL = os.path.join(ROOT, "gen", "pv_ctrl.py")             # the control board's ADC charge bucket (PCS-CTL re-uses it)
DAB = os.path.join(HERE, "dab_control.py")
AUX = os.path.join(HERE, "out", "aux_hv_design", "aux75_spec.json")      # the 75 W aux block (start-up, brown-out, OV lock-out)
V_LL, P_N, F0 = 400.0, 125e3, 50.0          # REQUIREMENTS AC-02
RULE_PM, RULE_GM = 40.0, 6.0               # margin rule of the study brief (every admitted SCR, every corner)
R_OPEN = 1e4                               # ohm: an open contactor / open terminal in the linear plant

ASSUME = {
    "grid_rx": (0.1, "grid R/X behind the point of connection (Thevenin, SCR on the 400 V / 125 kW base)"),
    "scr": ((None, 20.0, 10.0, 5.0), "SCRs studied (None = stiff, L_g 0); pcs_spec admits '5 .. stiff'"),
    "cf_tol": (None, "C_f tolerance: set at run time from the drawn BOM line by load() (review R2-01; was ASSUMED 5 %)"),
    "l_high": (None, "inductance corner at 0 A: set at run time from the magnetics files' L_tol by load()"),
    "tol_random": ((1000, 20261007), "tolerance regression (review R2-01): size and seed of the random sample inside the tolerance "
                                     "box, added to the full factorial (reproducible)"),
    "sensor": ({"part": "Sinomags STK-250HO/4", "G_mV_per_A": 3.2, "Vref_V": 2.5, "linear_A": 625.0, "bw_kHz": 200.0,
                "t_step_us": 2.0},
               "phase-current sensor being frozen in sim/pcs_design.py by a parallel task (orchestrator brief 2026-10-05; "
               "read from pcs_spec phase_current_sensor at run time when that block exists, else this entry); PCS-CTL rev A0 re-valued its ladders for the 3.2 mV/A part (PCM-20). Loop: the "
               "design check's chain delay with its sensor term replaced by t_step, as one lag (conservative: 200 kHz alone "
               "is a 0.8 us lag)"),
    "slow_sensor": (2.0, "robustness corner: current-chain delay x this (a sensor or AFE slower than its data-sheet "
                         "maximum)"),
    "fc_grid_Hz": ((500.0, 6000.0, 250.0), "candidate stiff-grid crossovers of the current loop, K_p = 2 pi f_c (L1 + L2)"),
    "fz_Hz": (25.0, "fundamental resonant gain K_r = 2 K_p 2 pi f_z (dq-equivalent PI zero, error time constant ~6 ms)"),
    "harmonics": ((5, 7, 11, 13), "resonant terms named by the pcs_spec hand-over; each kept only if its lead fits"),
    "sigma_h_Hz": (10.0, "harmonic resonant gains K_rh = 2 K_p 2 pi sigma (harmonic error time constant ~16 ms)"),
    "lead_margin_deg": (30.0, "harmonic term kept only if |phi_h + angle(plant seen by it)| <= 90 - this at every SCR"),
    "res_decay_frac": (0.5, "... and only if its closed-loop mode decays at >= this x 2 pi sigma_h at every SCR and corner "
                            "(a slower mode is close to the stability limit: the GFL reference path tipped h13 over at "
                            "SCR 5 on import)"),
    "sogi_k": (math.sqrt(2), "SOGI band-pass of the C_f-voltage feed-forward (fundamental only, damping 0.71)"),
    "ff_reseed_pu": (0.10, "firmware: when the sensed C_f voltage leaves the SOGI output by more than this (pu of the "
                           "phase peak), the SOGI state is re-seeded on the sensed vector (large steps only; no effect on "
                           "the small-signal loop)"),
    "f_pll_Hz": (30.0, "SRF-PLL -3 dB bandwidth (zeta 0.707; 20-50 Hz class)"),
    "pll_dw_max_Hz": (5.0, "PLL frequency (integrator) clamp +/- (45-55 Hz), conditional integration"),
    "pll_slew_Hz": (25.0, "PLL total angle slew clamp +/- (lets the proportional path follow a phase jump)"),
    "vd_lpf_Hz": (20.0, "low-pass of the PLL-frame voltage that turns P/Q into current references (at 100 Hz the P/V "
                        "conversion couples the DC-link loop into the h5/h7 resonant modes at SCR 5 and destabilises "
                        "them on import)"),
    "f_dc_Hz": (40.0, "DC-link voltage-loop crossover on C_dc (PI zero at a quarter of it)"),
    "dc_zero_ratio": (4.0, "DC-link PI zero = f_dc / this"),
    "p_cpl_W": (125e3, "DC/DC as a constant-power source or load on the DC bus, up to the PCS rating"),
    "m_max": (0.98, "modulation limit |u| <= m_max V_dc / sqrt(3) (min-max zero sequence; pcs_spec's 0.98 rule)"),
    "f_cv_max_Hz": (2000.0, "grid-forming C_f-voltage loop: highest crossover parameter tried, 100 Hz steps (inner loop "
                            "P only)"),
    "fzv_ratios": ((2.0, 3.0, 5.0, 10.0), "grid-forming voltage PR: dq-equivalent zero = crossover / ratio, scanned with "
                                         "the crossover; choice = the pair meeting the rule with the largest headroom "
                                         "min(PM - 40 deg, 5 x (GM - 6 dB))"),
    "gfm_zeta_min": (0.10, "grid forming, grid-connected: least-damped closed-loop mode below 1 kHz has at least this damping "
                           "ratio at every SCR (the multi-loop counterpart of the margin rule; the gains before the two-axis "
                           "scan left 0.02 at about 100 Hz)"),
    "droop_p": (0.02, "P-f droop: 1 Hz (2 %) per 125 kW"),
    "droop_q": (0.05, "Q-V droop: 5 % of V per 125 kvar"),
    "f_pq_Hz": (5.0, "power measurement low-pass of the droop"),
    "zv_pu": ((0.15, 0.30), "virtual impedance R_v, X_v (pu of 1.28 ohm), quasi-static at 50 Hz (0.05 / 0.15 pu left the "
                           "grid-connected droop mode at 2-3 Hz unstable on every SCR; this pair is the smallest of a "
                           "scan that is stable from stiff to SCR 5)"),
    "bg_harm_pct": ({5: 3.0, 7: 2.0, 11: 1.0, 13: 1.0}, "background grid-voltage harmonics for the THDi estimate (typical "
                                                        "LV values; EN 50160 allows 6 / 5 / 3.5 / 3 %)"),
    "dt_resid_ns": (50.0, "dead-time compensation residual once each leg's dead time is identified at commissioning"),
    "r_sc_ohm": (5e-3, "terminal short: resistance (bolted fault behind a short cable)"),
    "l_sc_H": (5e-6, "terminal short: cable inductance"),
    "t_lim_s": (0.2, "firmware: current limit 1.2 x I_max held for <= this, then trip (pcs_spec hand-over)"),
    "sec_rule": ((10.0, 0.10), "VF secondary restoration time constant (firmware parameter, 0.5-2 s class) = the smallest of "
                               "sec_T_scan_s whose restoration modes are real, at least this x slower than the nearest primary mode (the "
                               "5 Hz measurement filters) and move that mode by at most this fraction, and >= this x can_cycle_s"),
    "sec_reseed_pu": (0.02, "VF secondary voltage layer: re-seeded downwards when its state exceeds the virtual-impedance drop of the "
                            "present output current by more than this (load rejection); never upwards (the slow integrator restores)"),
    "sec_T_scan_s": ((0.5, 1.0, 2.0), "secondary time constants compared on the linearised island at rated load"),
    "iclamp_margin_A": (15.0, "per-sample current clamp level = window low edge - ripple/2 - this (prediction error of the sensed "
                              "C_f voltage and of L1)"),
    "lvrt_pu": ((0.85, 0.90), "ride-through state: entered when the sensed C_f voltage magnitude falls below the first, its hold "
                              "timer runs down only above the second (EN 50549-1 class thresholds, FROM MEMORY)"),
    "pll_freeze_pu": (0.2, "ride-through: PLL integrator and angle held (free-running at the pre-dip frequency) while the sensed "
                           "C_f voltage magnitude is below this; resumes above it"),
    "lvrt_hold_s": (0.04, "dip-time current limit kept this long after the voltage has returned (two grid periods)"),
    "rt_rule": ({"table_scr": (75.0, 100.0, 150.0, 200.0, 300.0, 500.0), "dq_pu": 0.2, "acc": 0.30, "margin_A": 15.0},
                "stiff-grid ride-through (review R2-05): tabulated SCRs of the fallback derating table above the boundary; the "
                "grid-stiffness estimate's reactive-current step (pu of rated) at connection and its accuracy in |Z_g| (ASSUMED +/-30 %: "
                "the grid's own voltage variation over the correlation window dominates; the method's deterministic error is computed); "
                "the margin the adopted onset measure must keep below the window's low edge at its worst corner (the clamp's "
                "prediction error, as iclamp_margin_A)"),
    "f_io_scan_Hz": ((500.0, 1000.0, 2000.0, 5000.0), "VF load-current feed-forward i_1 - C_eq dv_C/dt (review R2-04): low-pass "
                     "candidates; the highest meeting the margin rule at every corner is taken (one 0.88 V LSB of the sensed C_f voltage "
                     "in one sample is 3.4 A of C_eq dv/dt: a 1 kHz filter, about 10 samples, holds the quantisation noise below 0.5 A rms)"),
    "vf_vclamp_pu": (1.2, "VF over-voltage deadbeat (review R2-04): above this |v_C| (divider-inverted sample) the inner loop's command "
                          "is the one that ends the next period with i_1 at the unfiltered load-current estimate; the voltage PR does "
                          "not integrate meanwhile (inactive in normal operation)"),
    "vf_envelope": (({"over": ((0.5, 1.9), (3.0, 1.3), (5.0, 1.2), (20.0, 1.1), (50.0, 1.05), (float("inf"), 1.03)),
                       "under": ((0.5, 0.35), (3.0, 0.75), (20.0, 0.88), (50.0, 0.95), (float("inf"), 0.97))},
                      {"over": ((1.0, 2.0), (3.0, 1.4), (500.0, 1.2), (float("inf"), 1.1)),
                       "under": ((20.0, 0.0), (500.0, 0.7), (10000.0, 0.8), (float("inf"), 0.9))}),
                     "VF transient envelope for a 100 % linear (resistive) load step, |v_C| in pu against the time after the step, "
                     "piecewise constant (each limit valid up to its time in ms): first = the envelope DECLARED here (single module, "
                     "set to contain the worst corner with margin); second = an ITIC (CBEMA) curve style class FROM MEMORY (2.0 / 1.4 / "
                     "1.2 / 1.1 pu over to 1 ms / 3 ms / 0.5 s / steady, 0 / 0.7 / 0.8 / 0.9 pu under to 20 ms / 0.5 s / 10 s / steady) - "
                     "not verified, the standard text (ITIC, IEC 62040-3 classes) is not on file; IEC 62040-3 class 1 is not claimed"),
    "dc_reg_T_s": (0.02, "four-wire DC-component regulators (one-cycle mean -> integrator -> offset on the reference): time constant, "
                         "a firmware parameter (review R2-13 compares 0.2 s: the half-wave DC transient then lasts 0.8 s)"),
    "dcdc_stop_us": (50.0, "a DC/DC feeding the link stops this long after the PCS's DC over-voltage trip (a shared trip line or its own "
                           "comparator at the same level: ASSUMED - the DC/DC firmware is not modelled here)"),
    "osc_ageing_ppm": (10.0, "controller oscillator ageing over the module life (Epson SG-210STF data sheet: +/-3 ppm in the first year "
                             "at 25 C, lifetime not guaranteed by the maker; its +/-50 ppm tolerance covers initial, temperature, "
                             "supply and load)"),
    "can_cycle_s": (0.05, "module-CAN cycle of the paralleled modules' restoration consensus (ASSUMED; not modelled)"),
    "isr_cyc_per_op": (2.5, "C28x + FPU32 + TMU, compiled C: cycles per floating-point operation incl. loads/stores"),
    "isr_overhead_cyc": (80, "ISR entry/exit and context save with the FPU registers, cycles"),
    "codes": ({"rocof_Hz_s": 2.0, "f_band_Hz": (47.5, 51.5), "phase_jump_deg": 30.0, "dip_pu": 0.5, "deep_pu": 0.1,
               "dip_s": 0.15, "island_s": 2.0, "transfer_ms": 20.0},
              "grid-code-like limits FROM MEMORY (EN 50549-1 / ENTSO-E RfG / IEC 62116 class; Megarevo's 20 ms transfer), "
              "not verified - the standards are not on file"),
}


REVIEW_R2 = {   # the independent reviewer's re-check of commit a427981 (gen/data/review_r2.csv): HIS figures, quoted, not recomputed here
    "R2-01": dict(cases=1944, stable=1944, pm_deg={"stiff": 71.15, "SCR 20": 61.38, "SCR 10": 61.68, "SCR 5": 49.04},
                  basis="his inner-current-loop reproduction, mixed tolerances incl. C_f +/-10 %; all linearly stable"),
    "R2-04": dict(sag_pu=0.380, overshoot_pu=2.170, dc_rejection_peak_V=1098.0, ov_band_V=(978.0, 1048.0)),
    "R2-05": dict(onset_A=(484.0, 465.0, 445.0), residual_pu=(0.0, 0.1, 0.2), trip_edge_A=426.0, prefault_kW=(95.5, 105.5, 115.0)),
}


def A(k):
    return ASSUME[k][0]


def cx(x):
    """unit phasor e^{jx}"""
    return complex(math.cos(x), math.sin(x))


def lp(f, T):
    """first-order discrete low-pass coefficient"""
    return 1.0 - math.exp(-2 * math.pi * f * T)


# ---------------------------------------------------------------------------------------------------------- inputs
def read_json(path, need=(), tries=60):
    """read a JSON file another study may be rewriting at this moment: retry while it is incomplete or still lacks a
    top-level key written in a later stage (sim/pcs_design.py writes pcs_spec.json more than once per run)"""
    for _ in range(tries):
        try:
            d = json.load(open(path))
            if all(k in d for k in need):
                return d
        except json.JSONDecodeError:
            pass
        time.sleep(1.0)
    raise RuntimeError(f"{path}: unreadable or missing {need} after {tries} s - re-run the script that writes it")


def load():
    """pcs_spec + magnetics files + the two design checks -> parameter dict P (read at run time, nothing copied)"""
    sp = read_json(SPEC, need=("lcl", "inductors", "dc_link", "design", "commutation_and_gate_drive", "handover"))
    ce, flt = sp["handover"]["control_engineer"], sp["lcl"]["filter"]
    mg = {k: read_json(MAG % k) for k in ("l1", "l2", "cm_choke")}
    pwr, ctl = (open(CHK % (b, b)).read() for b in ("PWR", "CTL"))

    def grab(txt, pat, what):
        r = re.search(pat, txt, re.S)
        assert r, f"{what}: pattern {pat!r} not found - the source changed, re-read it"
        return [float(x) for x in r.groups()]

    sm = ce["sampling"]
    fs, fsw = sm["f_s_Hz"], sm["f_sw_Hz"]
    assert abs(fs - 2 * fsw) < 1e-6 and abs(sm["delay_s"] - 1.5 / fs) < 1e-12, "pcs_spec: no longer double update, 1.5 T_s"
    P = dict(fs=fs, fsw=fsw, T=1 / fs, w0=2 * math.pi * F0, V0=V_LL * math.sqrt(2 / 3), zb=V_LL ** 2 / P_N)
    P["R"] = cx(-P["w0"] * P["T"])                        # one sample of rotation into the synchronous frame
    e1, e2, ec = (mg[k]["electrical"] for k in ("l1", "l2", "cm_choke"))
    pct = re.search(r"DM leakage taken as ([\d.]+) %", ec.get("note", ""))
    tl = {}                                                   # inductance tolerance of each part (review R2-01: read, fail closed)
    for k, e in (("L1", e1), ("L2", e2)):
        tl[k] = grab(e.get("L_tol", ""), r"\+/-([\d.]+) % at 0 A", f"{k} L_tol (magnetics file)")[0] / 100
        assert f"L_inc_at_minus{tl[k] * 100:.0f}pct_part_H" in e, f"{k}: the -{tl[k]:.0%} part's L(I) is not in the magnetics file"
    ASSUME["l_high"] = (1 + tl["L1"], f"+{tl['L1']:.0%} inductance corner at 0 A (read at run time: magnetics L_tol '{e1['L_tol']}')")
    P.update(L1=flt["L1"], L2=flt["L2"], Cf=flt["Cf"], Rd=flt["Rd"], Cd=flt["Cd"],
             Ldm=ec["L_nom_H"] * (float(pct.group(1)) if pct else 0.3) / 100,
             R1=mg["l1"]["loss_model"]["winding"]["R_dc_110C_ohm"], R2=mg["l2"]["loss_model"]["winding"]["R_dc_110C_ohm"],
             L1_lo=min(e1["L_inc_at_minus10pct_part_H"].values()), L2_lo=min(e2["L_inc_at_minus10pct_part_H"].values()),
             L1_hi=max(e1["L_inc_vs_I_H"].values()) * (1 + tl["L1"]), L2_hi=max(e2["L_inc_vs_I_H"].values()) * (1 + tl["L2"]),
             L1_env=min(e1["L_min_required_H"].values()), L2_env=min(e2["L_min_required_H"].values()),
             rev=(mg["l1"]["revision"], mg["l2"]["revision"]), f_res_spec=sp["lcl"]["f_res_Hz"],
             pwm_thd=max(r["THD_all_pwm"] for r in sp["lcl"]["spectrum"]), sensor_in_spec="STK-250HO" in json.dumps(sp))
    P["Ceq"] = P["Cf"] + P["Cd"]                          # 50 Hz: the R_d-C_d branch is capacitive
    scr_min = grab(ce["plant"]["grid_SCR_range"], r"(\d+)", "admitted SCR range")[0]
    assert min(s for s in A("scr") if s) == scr_min, "the studied SCRs must reach pcs_spec's weakest admitted grid"
    # sensor chains and windows from the drawn boards
    P["ti_chk"] = grab(ctl, r"ADC IL1-4 .*?delay ([\d.]+) us", "IL ADC chain")[0] * 1e-6
    # PCS-CTL rev A0 (PCM-20): "[INFO] Phase-current sensor STK-250HO/4 (...) - 3.2 mV/A around its Uref pin ..., step response 2.0 us max"
    P["G_chk"], t_chk = grab(ctl, r"Phase-current sensor (?:ASSUMED|STK-\S+).*?-? ?G? ?([\d.]+) mV/A.*?(?:step )?response ([\d.]+) us", "sensor")
    if "phase_current_sensor" in sp:                                   # frozen part in pcs_spec: it overrides ASSUME["sensor"]
        _x = sp["phase_current_sensor"]
        A("sensor").update(part=_x.get("mfr", "") + " " + _x["mpn"], G_mV_per_A=_x["gain_mV_per_A"], Vref_V=_x["vref_V"],
                           linear_A=_x["linear_range_A"], bw_kHz=_x["bandwidth_Hz"] / 1e3, t_step_us=_x["step_response_s"] * 1e6)
        P["sensor_src"] = "sim/out/pcs_design/pcs_spec.json phase_current_sensor (read at run time)"
    else:
        P["sensor_src"] = "ASSUMED (pcs_spec carries no phase_current_sensor block)"
    P["t_sensor_chk"], P["t_sensor"] = t_chk * 1e-6, A("sensor")["t_step_us"] * 1e-6
    P["ti"] = P["ti_chk"] - P["t_sensor_chk"] + P["t_sensor"]     # the drawn chain with the frozen sensor's step response
    P["trip_t"] = grab(ctl, r"Gates off ([\d.]+) us \(sensor ([\d.]+).*?-> ([\d.]+) A <= (\d+) A", "window trip timing")
    P["didt"] = grab(ctl, r"di/dt ([\d.]+) A/us", "di/dt")[0]
    P["win_top_req"] = grab(ctl, r"top (?:<=|below the backup) ([\d.]+) A", "window top requirement")[0]   # PCS-CTL rev A0 wording (PCM-20)
    P["f_vdiv"] = grab(pwr, r"VC1-3 \(C_f nodes\).*?([\d.]+) kHz\)", "C_f voltage divider")[0] * 1e3
    P["tv"] = 1 / (2 * math.pi * P["f_vdiv"])
    P["t_vdc"] = grab(pwr, r"DC-link sensing:.*?nF: ([\d.]+) us\)", "DC-link divider")[0] * 1e-6
    P["f_ib"] = grab(ctl, r"lean_shunt pole ([\d.]+) kHz", "DC current chain")[0] * 1e3
    w = grab(ctl, r"IL1-4 local window\s+\+([\d.]+)/-([\d.]+) A\s+band ([\d.]+)-([\d.]+) A\s+response ([\d.]+) us", "window")
    P["win"] = dict(nom=min(w[0], w[1]), lo=w[2], hi=w[3], t_us=w[4])
    c = grab(ctl, r"IL1-4 CMPSS backup\s+\+/-([\d.]+) A \(DAC\)\s+band ([\d.]+)-([\d.]+) A\s+response ([\d.]+) us", "CMPSS")
    P["cmpss"] = dict(nom=c[0], lo=c[1], hi=c[2], t_us=c[3])
    try:                                                             # rev A0 (PCM-19/20): "7.2 % above the normal peak 397.5 A"
        _pct, _pk = grab(ctl, r"([\d.]+) % above the normal peak ([\d.]+) A", "window basis")
        P["win_basis"] = (_pk * (1 + _pct / 100), 1 + _pct / 100, _pk)
    except AssertionError:                                           # earlier wording: "needs >= X A (1.10 x normal peak Y A)"
        P["win_basis"] = grab(ctl, r"[Nn]eeds >= ([\d.]+) A \(([\d.]+) x normal peak ([\d.]+) A\)", "window basis")
    P["dt_gate_ns"] = grab(pwr, r"Dead time at the gates.*?\): (\d+)-(\d+) ns", "dead time")
    P["dt_floor_ns"] = grab(pwr, r"firmware dead band below (\d+) ns", "dead-time floor")[0]
    P["dt_spec_ns"] = sm["dead_time_ns"]
    P["dt_fw_ctl_ns"] = grab(ctl, r"dead time \| ePWM dead-band generator(?:.*?\| | set to )(\d+) ns", "PCS-CTL dead band")[0]   # rev A0 wording
    P["adc_plan"] = grab(ctl, r"ADC load - .*?([\d.]+) conversions per ([\d.]+) us on (\d+) ADCs = (\d+) %", "ADC plan")   # rev A0: inverter plan, 25.2 per period
    P["tmux_ms"] = grab(ctl, r"settled within (\d+) ms", "NTC multiplexer")[0]
    P["t_pre"] = grab(pwr, r"10 V in ([\d.]+) s", "precharge time")[0]
    P["ov_dc"] = grab(ctl, r"port OV VA / VB\s+(\d+) V\s+band (\d+)-(\d+) V", "DC over-voltage")
    P["v_lsb"] = grab(ctl, r"ADC VA / VB \(VMID \+ V/(\d+)\).*?([\d.]+) V/LSB", "VA / VB LSB")   # the VG channels share it
    P["ov_resp_us"] = grab(ctl, r"port OV VA / VB\s+\d+ V\s+band \d+-\d+ V\s+response ([\d.]+) us", "DC OV response")[0]
    P["ov_ppb"] = grab(ctl, r"port OV backup \(ADC PPB\)\s+\d+ V\s+band (\d+)-(\d+) V\s+response ([\d.]+) us", "DC OV backup")
    P["ov_soft"] = grab(ctl, r"OV / UV soft limits \| ADC[^|]*\| ([\d.]+) V[^|]*\| outer loop \| ([\d.]+) us", "soft OV")
    _ax = read_json(AUX)                                            # the 75 W aux: input OV lock-out, brown-in / -out, start-up,
    P["aux"] = dict(ov=min(_ax["startup"]["ov_lockout_V"]), brown_in=list(_ax["startup"]["brown_in_V"]),   # the logic hold-up
                    brown_out=list(_ax["startup"]["brown_out_V"]), start_s=list(_ax["startup"]["vdd_charge_s"]),
                    holdup_ms=_ax["holdup"]["t_ms"])
    P["cmpss_req"] = grab(ctl, r"IL1-4 CMPSS backup.*?requirement ([\d.]+)-([\d.]+) A", "CMPSS requirement text")
    # ratings, ripple, DC link, sequencing
    cur = sp["inductors"]["L1"]["current"]
    P["tiers"] = {k: v for k, v in cur["fundamental_rms_A"].items()}
    P["I_rated"] = cur["fundamental_rms_A"]["rated"] * math.sqrt(2)
    P["I_lim"] = cur["fundamental_rms_A"]["200_ms"] * math.sqrt(2)
    P["ripple_pp"] = cur["ripple_pp_max_A"]
    dl, leg = sp["dc_link"], sp["commutation_and_gate_drive"]["leg"]
    key = next((k for k in ("C_total_uF", "C_eff_uF", "C_link_total_uF") if k in dl), None)
    P["Cdc"] = dl[key] * 1e-6 if key else dl["C_series_uF"] * 1e-6 + 3 * leg["c_dec"]
    P["Cdc_src"] = f"dc_link.{key}" if key else (f"dc_link.C_series_uF {dl['C_series_uF']:.0f} uF + 3 legs x "
                                                f"commutation leg c_dec {leg['c_dec'] * 1e6:.1f} uF")
    P["C_half"], P["C_loc"] = dl["C_half_uF"] * 1e-6, dl["C_local_films_uF"] * 1e-6      # the halves and the leg films (post-trip)
    assert abs(P["C_half"] / 2 + P["C_loc"] - P["Cdc"]) < 1e-9, "dc_link: halves + leg films are not the total the loop uses"
    P["U_N_half"] = dl["upper_half"]["U_N_hot_spot_V"]["45C"]                        # C3D1U147 rated voltage per half (hot spot)
    fw = " ".join(sp["handover"]["firmware_requirements"])
    P["sync"] = grab(fw, r"\|dV\| <= (\d+) %, (\d+) deg", "sync permissive")
    P["inrush_A"] = next(v for k, v in sp["lcl"]["inrush"].items() if k.startswith("synchronised"))
    om = sp.get("operating_map", {})                       # the steady-state map (another study; optional)
    P["u_frac"] = om["m_usable"] / 2 if "m_usable" in om else A("m_max") / math.sqrt(3)
    P["u_src"] = (f"pcs_spec operating_map m_usable {om['m_usable']:.4f} (dead-time and headroom deducted)" if "m_usable" in om
                  else f"ASSUMED m_max {A('m_max')} with min-max zero sequence")
    P["map_vdc440"] = max(om["vdc_min_at_rated_current_V"]["440 V 50 Hz"].values()) if "vdc_min_at_rated_current_V" in om else None
    P["map_close"] = next((x["threshold"] for x in sp["handover"].get("firmware_limits", [])
                           if x.get("item", "").startswith("AC contactor closing")), None)
    rows = [r for r in csv.DictReader(open(PINS)) if r["net"]]
    P["pins"], P["routes"] = {r["net"]: r["datasheet_name"] for r in rows}, {r["net"]: r["route"] for r in rows}
    P["va_max_us"] = grab(P["routes"]["VA_ADC"], r"<= (\d+) us", "VA conversion interval (pin plan)")[0]
    P["c_pcs_dab"] = grab(open(DAB).read(), r"'C_pcs': \(([\d.e-]+)", "the DAB study's PCS link")[0]
    # ---- sections 4b, 7b, 8b-d, 9b (VF accuracy, THDu, imbalance, ride-through, neutral leg): inputs, fail closed
    a = grab(ctl, r"ADC accuracy, this board's share[^\n]*?RSS ([\d.]+) %[^\n]*?worst ([\d.]+) % \(REF3030E drift ([\d.]+) %"
                  r"[^\n]*?mismatch ([\d.]+) % = ([\d.]+) %", "ADC accuracy share")
    P["adc_acc"] = dict(zip(("rss", "worst", "ref", "tcr", "total"), a))
    assert abs(a[1] + a[3] - a[4]) < 0.011, "PCS-CTL ADC accuracy line: worst + divider TCR != the stated total"
    n, rt, rb, c, fk = grab(pwr, r"VC1-3 \(C_f nodes\).*?\((\d+) x ([\d.]+) M ARHV06 \+ ([\d.]+) k, ([\d.]+) nF: ([\d.]+) kHz\)",
                            "AC divider values")
    rows = {k: list(csv.DictReader(open(BOM % k))) for k in ("PCS-PWR_BOM", "PCS-CTL_BOM", "PCS-P125_module_BOM")}

    def bom_tol(b, pick, what):
        hit = [r for r in rows[b] if pick(r)]
        assert len(hit) == 1, f"bom/{b}.csv: {len(hit)} lines for {what} (one expected) - the BOM changed, re-read it"
        return grab(hit[0]["Description"], r"([\d.]+) ?%", f"{what} tolerance")[0] / 100

    P["vdiv"] = dict(n=int(n), R_top=rt * 1e6, R_bot=rb * 1e3, C=c * 1e-9, f_stated=fk * 1e3,
                     tol_top=bom_tol("PCS-PWR_BOM", lambda r: r["MPN"] == "ARHV06BTC1004A", "1 M ARHV06"),
                     tol_bot=bom_tol("PCS-PWR_BOM", lambda r: r["MPN"] == "TNPW12064K99BEEA", "4.99 k TNPW"),
                     tol_c=bom_tol("PCS-PWR_BOM", lambda r: r["Value"] == "4.7n 50V" and "C0G" in r["Description"], "4.7 nF C0G"))
    rb_, cb_ = grab(open(PVCTL).read(), r'ADC_RC = \("(\d+)R", "(\d+)n"\)', "gen/pv_ctrl.py ADC_RC")
    P["bucket"] = dict(R=rb_, C=cb_ * 1e-9,
                       tol_r=bom_tol("PCS-CTL_BOM", lambda r: r["Value"] == "%dR" % rb_ and r["Package"] == "0603", "100 R charge bucket"),
                       tol_c=bom_tol("PCS-CTL_BOM", lambda r: r["Value"] == "%dn 50V" % cb_ and "C0G" in r["Description"], "1 nF C0G bucket"))
    def lcl_tol(tag, what, value):                         # the LCL parts as drawn (review R2-01: the study had ASSUMED C_f 5 %)
        hit = [r for r in rows["PCS-PWR_BOM"] if tag in r["Description"]]
        assert len(hit) == 1, f"bom/PCS-PWR_BOM.csv: {len(hit)} lines tagged {tag!r} ({what}) - the BOM changed, re-read it"
        v = grab(hit[0]["Value"], r"([\d.]+)", f"{what} value")[0]
        assert abs(v / value - 1) < 1e-6, f"{what}: BOM value {hit[0]['Value']!r} is not pcs_spec's {value:g}"
        return bom_tol("PCS-PWR_BOM", lambda r: tag in r["Description"], what)
    P["tol"] = dict(L1=tl["L1"], L2=tl["L2"], Cf=lcl_tol("(C_f, 2 per phase)", "C_f", P["Cf"] / 2 * 1e6),
                    Cd=lcl_tol("(LCL damping branch C_d)", "C_d", P["Cd"] * 1e6), Rd=lcl_tol("(LCL damping branch R_d)", "R_d", P["Rd"]))
    ASSUME["cf_tol"] = (P["tol"]["Cf"], "C_f tolerance, read at run time from the drawn BOM line (bom/PCS-PWR_BOM.csv; review R2-01 - "
                                         "the study had ASSUMED 5 %); C_d and R_d likewise (P['tol'])")
    _ty = sp.get("phase_current_sensor", {}).get("step_response_typ_s")
    P["ti_typ"] = P["ti_chk"] - P["t_sensor_chk"] + _ty if _ty else P["ti"]     # fastest chain of the tolerance regression
    osc = [r for r in rows["PCS-P125_module_BOM"] if r["MPN"] == "X1G0041710033"]
    assert len(osc) == 1, "module BOM: the controller oscillator X1G0041710033 is not one line - re-read the BOM"
    P["osc"] = dict(ppm=grab(osc[0]["Description"], r"\+/-(\d+) ppm", "oscillator tolerance")[0], part=osc[0]["Description"].split(",")[0])
    lv = grab(fw, r"LVRT: ([\d.]+) pu for (\d+) ms, ([\d.]+) pu to (\d+) ms, linear to ([\d.]+) pu at ([\d.]+) s, reactive current "
                  r"k = ([\d.]+) \(dI_q / dU\) up to ([\d.]+) Ir", "LVRT profile (hand-over)")
    P["lvrt_profile"] = [(lv[0], lv[1] / 1e3), (lv[2], lv[3] / 1e3), (lv[4], lv[5])]
    P["lvrt_ilim"] = lv[7] * P["I_rated"]                  # dip-time limit = the profile's reactive-current cap (1.0 I_r, peak)
    P["iclamp"] = P["win"]["lo"] - P["ripple_pp"] / 2 - A("iclamp_margin_A")
    assert P["iclamp"] > P["I_lim"], "per-sample clamp below the 200 ms tier: the window leaves no room for it"
    fw4 = sp.get("four_wire")
    assert fw4, "pcs_spec has no four_wire block: re-run sim/pcs_design.py"
    P["fw4"] = dict(fw4["filter"], half_wave=fw4["half_wave"], modeA_from=fw4["window"]["windows_at_400_230V"][
        next(k for k in fw4["window"]["windows_at_400_230V"] if "mode A" in k)]["full_load_from"], m_A=fw4["window"]["m_usable_mode_A"],
        hw_pk_A=sp["declarations"]["offgrid_load_acceptance"]["half_wave_four_wire"]["I_pk_limit_A"],
        LN_pk_A=sp["declarations"]["offgrid_load_acceptance"]["half_wave_four_wire"]["L1_part_continuous_peak_A"])
    ow = sp["declarations"]["operating_windows"]["3W+PE"]
    P["vf_vdc"] = (ow["full_load_from"], 750.0, ow["operating_to"])     # VF DC voltages for THDu (window ends + nominal)
    P["pc"] = protection_chain(sp, P, ctl)
    acs = sp["ac_start"]                                            # the grid-start sequence (review R2-14), read from its own text
    seq = " ".join(acs["sequence"] + acs["interlocks"])
    P["acs"] = dict(R=min(acs["precharge"]["R_tot_ohm"]), t10=acs["precharge"]["by_build"]["3W"]["400"]["t_to_10V_s"],
                    t_abort=acs["precharge"]["t_abort_s"], weld_V=grab(seq, r"link must stay below (\d+) V", "welded-precharge check")[0],
                    close=grab(seq, r"V_dc >= ([\d.]+) x sqrt2 x V_LL", "grid-start closing permissive")[0],
                    dv=grab(seq, r"dV window \(([\d.]+)-([\d.]+) V\)", "DC dV window"),
                    pol=grab(seq, r"polarity interlock needs >= (\d+)-(\d+) V", "polarity interlock"),
                    abort=grab(seq, r"below (\d+) % of the tap peak", "shorted-bank rule")[0] / 100,
                    retry=grab(seq, r"<= (\d+) attempts per start >= (\d+) s apart", "retry rule"),
                    tau_bl=60 * grab(seq, r"bleeder time constant about (\d+) min", "bleeder time constant")[0])
    return P


def protection_chain(sp, P, ctl):
    """the phase window's gates-off chain and the commutation screen (review R2-02 / R2-04 / R2-05): from pcs_spec 'protection_chain'
    (sim/pcs_design.py, the power-stage study) when the block exists - fail closed on a malformed block or one that disagrees with
    the PCS-CTL check this study reads - else from the drawn boards (PCS-CTL window band, response and requirement top; pcs_spec's
    commutation deck at its test current) with a printed note.  The turn-off overshoot at another current is scaled linearly from
    the deck point (ESTIMATE, as gen/pcs_ctrl.py does); an over-voltage trip inside the block's over-voltage corner (bus at the OV
    gates-off, current at the window top) is covered by that corner's deck result directly"""
    cg = sp["commutation_and_gate_drive"]
    if "protection_chain" not in sp:
        deck = cg["worst_1050V_450A_30nH"]
        pc = dict(I_trip_lo=P["win"]["lo"], I_trip_hi=P["win"]["hi"], t_resp_us=P["win"]["t_us"], didt_A_us=P["didt"],
                  I_screen=grab_(ctl, r"IL1-4 local window.*?requirement >= [\d.]+ A, <= ([\d.]+) A", "window requirement top")[0],
                  V_os=deck["os_off"], I_os=deck["i"], V_bus_deck=deck["vdc"], V_limit=cg["chosen"]["v_limit_V"],
                  V_ov_off=None, V_pk_ov=None, I_ov=None, I_knee=None)
        pc["I_gates_off"] = pc["I_trip_hi"] + pc["didt_A_us"] * pc["t_resp_us"]
        print("NOTE pcs_control: pcs_spec has no 'protection_chain' block - the threshold screen (%.0f A) and the overshoot allowance "
              "(%.0f V at %.0f A) are read from the PCS-CTL design check and pcs_spec's commutation deck" % (pc["I_screen"], pc["V_os"], pc["I_os"]))
        return dict(pc, src="PCS-CTL design check + pcs_spec commutation_and_gate_drive (protection_chain absent)")
    b = sp["protection_chain"]
    try:
        inp, lw, oc = b["inputs"], b["local_window"], b["commutation_overshoot_V"]
        w = next(v for k, v in oc.items() if k.startswith("local window"))
        ov = next(v for k, v in oc.items() if k.startswith("over-voltage corner"))
        pc = dict(I_trip_lo=float(lw["band_A"][0]), I_trip_hi=float(lw["band_A"][1]), t_resp_us=float(lw["response_us"]),
                  didt_A_us=float(b["fault_slope"]["di_dt_at_window_top_A_per_us"]), I_gates_off=float(lw["I_gates_off_A"]),
                  I_screen=float(b["I_max_admissible_A"]), V_os=float(w["overshoot_V"]), I_os=float(w["I_A"]), V_bus_deck=float(w["V_bus_V"]),
                  V_limit=float(cg["chosen"]["v_limit_V"]), V_ov_off=float(inp["ov_at_gates_off_V"]), V_pk_ov=float(ov["v_pk_V"]),
                  I_ov=float(ov["I_A"]), ov_band=tuple(float(x) for x in inp["ov_band_V"]), closes=bool(lw["closes"]),
                  I_knee=float(b["L1_trajectory"]["I_knee_50pct_A"]["hot envelope"]))
    except (KeyError, StopIteration, TypeError, ValueError, IndexError) as e:
        raise RuntimeError(f"pcs_spec protection_chain malformed ({e!r}): re-run sim/pcs_design.py or update protection_chain()") from None
    assert all(math.isfinite(v) for v in pc.values() if isinstance(v, float)), ("protection_chain: a non-finite figure", pc)
    assert (pc["I_trip_lo"], pc["I_trip_hi"]) == (P["win"]["lo"], P["win"]["hi"]) and pc["ov_band"] == tuple(P["ov_dc"][1:]), \
        "pcs_spec protection_chain and the PCS-CTL design check disagree on the window / OV band: re-run sim/pcs_design.py"
    return dict(pc, src="pcs_spec protection_chain (sim/pcs_design.py, read at run time)", status=b.get("status", ""))


def grab_(txt, pat, what):
    r = re.search(pat, txt, re.S)
    assert r, f"{what}: pattern {pat!r} not found - the source changed, re-read it"
    return [float(x) for x in r.groups()]


def v_dev(P, I, V_bus):
    """device voltage at a turn-off of current I from bus V_bus: inside the protection chain's over-voltage corner (bus <= the OV
    gates-off bus, current <= the window top) that corner's deck result; otherwise the bus + the deck's overshoot scaled linearly
    with current (ESTIMATE)"""
    pc = P["pc"]
    if pc.get("V_ov_off") and V_bus <= pc["V_ov_off"] and abs(I) <= pc["I_ov"]:
        return pc["V_pk_ov"]
    return V_bus + pc["V_os"] * abs(I) / pc["I_os"]


# ---------------------------------------------------------------------------------------------------------- plant
_PL = {}


def corner(P, cn):
    """(L1, L2 + DM leakage, C_f, current-chain delay, R_d, C_d) of a named corner - every part at the drawn assembly's tolerance
    (P['tol']: BOM and magnetics files), low = all low, high = all high - or of a mixed corner ('mix', k_L1, k_L2, k_Cf, k_Cd,
    k_Rd, k_ti): factors on the nominal values (k_L2 on L2 + DM leakage)"""
    t, ti, dm = P["tol"], P["ti"], P["Ldm"]
    if isinstance(cn, tuple):
        _, kl1, kl2, kcf, kcd, krd, kti = cn
        return P["L1"] * kl1, (P["L2"] + dm) * kl2, P["Cf"] * kcf, ti * kti, P["Rd"] * krd, P["Cd"] * kcd
    l = (P["Cf"] * (1 - t["Cf"]), P["Rd"] * (1 - t["Rd"]), P["Cd"] * (1 - t["Cd"]))
    h = (P["Cf"] * (1 + t["Cf"]), P["Rd"] * (1 + t["Rd"]), P["Cd"] * (1 + t["Cd"]))
    return {"nom": (P["L1"], P["L2"] + dm, P["Cf"], ti, P["Rd"], P["Cd"]),
            "low": (P["L1_lo"], P["L2_lo"] + dm, l[0], ti, l[1], l[2]),
            "high": (P["L1_hi"], P["L2_hi"] + dm, h[0], ti, h[1], h[2]),
            "slow": (P["L1_lo"], P["L2_lo"] + dm, l[0], ti * A("slow_sensor"), l[1], l[2]),
            "env": (P["L1_env"], P["L2_env"] + dm, l[0], ti, l[1], l[2])}[cn]


def plant(P, cn="nom", kind="grid", scr=None, damp="passive", RL=None):
    """per-phase plant, states i1 vC vD i2 ig yi yv yg (yi, yv, yg = sensed i1, v_Cf, v_terminal), discretised.
    kind: grid (L2 + L_g to the source), open (contactors open, grid on the terminals), load (grid + local load RL at the
    terminals), nl (island, no load), rl (island, load RL, default 1 pu), short (terminal short)"""
    key = (cn, kind, scr, damp, RL, P["tv"], P["ti"])
    if key in _PL:
        return _PL[key]
    L1, L2, Cf, ti, Rd, Cd = corner(P, cn)
    Rg = Lg = R2x = 0.0
    if scr and kind in ("grid", "open", "load"):
        z = P["zb"] / scr
        x = z / math.hypot(1.0, A("grid_rx"))
        Rg, Lg = A("grid_rx") * x, x / P["w0"]
    if kind in ("open", "nl"):
        R2x = R_OPEN
    elif kind == "rl":
        Rg = RL or P["zb"]
    elif kind == "short":
        Rg, Lg = A("r_sc_ohm"), A("l_sc_H")
    n = 8
    Ac, Bu, Bg, vt = np.zeros((n, n)), np.zeros(n), np.zeros(n), np.zeros(n)
    R2 = P["R2"] + R2x
    Ac[0, 0], Ac[0, 1], Bu[0] = -P["R1"] / L1, -1 / L1, 1 / L1
    Ac[1, 0], Ac[1, 3] = 1 / Cf, -1 / Cf
    if damp == "passive":
        g = 1 / Rd
        Ac[1, 1], Ac[1, 2], Ac[2, 1], Ac[2, 2] = -g / Cf, g / Cf, g / Cd, -g / Cd
    else:
        Ac[2, 2] = -1e3                                         # branch not fitted: decoupled dummy state
    if kind == "load":                                          # grid and local load: two inductor currents
        assert Lg > 0, "local load needs a finite grid inductance"
        vt[3], vt[4], vtg = RL, -RL, 0.0
        Ac[3, 1], Ac[3, 3] = 1 / L2, -R2 / L2
        Ac[3] -= vt / L2
        Ac[4] += vt / Lg
        Ac[4, 4] -= Rg / Lg
        Bg[4] = -1 / Lg
    else:                                                       # L2 in series with the grid or the island load
        Ls = L2 + Lg
        Ac[3, 1], Ac[3, 3], Bg[3] = 1 / Ls, -(R2 + Rg) / Ls, -1 / Ls
        Ac[4, 4] = -1e3
        vt[1], vt[3], vtg = Lg / Ls, (Rg * L2 - R2 * Lg) / Ls, L2 / Ls
    Ac[5, 0], Ac[5, 5] = 1 / ti, -1 / ti
    Ac[6, 1], Ac[6, 6] = 1 / P["tv"], -1 / P["tv"]
    Ac[7] += vt / P["tv"]
    Ac[7, 7] -= 1 / P["tv"]
    Bg[7] = vtg / P["tv"]
    T = P["T"]
    M = np.zeros((n + 1, n + 1))
    M[:n, :n], M[:n, n] = Ac, Bu
    E = expm(M * T)

    def rot(s):                                                 # exact integral of a rotating grid voltage over T
        Mc = np.zeros((n + 1, n + 1), complex)
        Mc[:n, :n], Mc[:n, n], Mc[n, n] = Ac, Bg, s
        return expm(Mc * T)[:n, n]

    pl = dict(A=Ac, Bu=Bu, Bg=Bg, Phi=E[:n, :n], Gu=E[:n, n], Gp=rot(1j * P["w0"]), Gn=rot(-1j * P["w0"]), rot=rot,
              ig=4 if kind == "load" else 3, Lg=Lg, Rg=Rg, kind=kind)
    _PL[key] = pl
    return pl


def f_res(pl):
    """highest-frequency resonant pole pair of the physical states: (natural frequency Hz, damping ratio)"""
    ev = np.linalg.eigvals(pl["A"][:5, :5])
    ev = ev[np.abs(ev.imag) > 2 * np.pi * 300]
    if not len(ev):
        return None, None
    e = ev[np.argmax(np.abs(ev.imag))]
    return abs(e) / (2 * np.pi), -e.real / abs(e)


# ---------------------------------------------------------------------------------------------------------- controller
NOBLK = (np.zeros((0, 0)), np.zeros(0), np.zeros(0), 0.0)


def dss(num, den, T):
    """continuous transfer function -> discrete state space (Tustin)"""
    nd, dd, _ = cont2discrete((np.atleast_1d(num), np.atleast_1d(den)), T, method="bilinear")
    a, b, c, d = tf2ss(np.squeeze(nd), dd)
    return a, b[:, 0], c[0], float(d[0, 0])


def par(*blks):
    """parallel connection of SISO blocks (same input, outputs summed)"""
    n = sum(len(b[1]) for b in blks)
    Ab, Bb, Cb, D, i = np.zeros((n, n)), np.zeros(n), np.zeros(n), 0.0, 0
    for a, b, c, d in blks:
        m = len(b)
        Ab[i:i + m, i:i + m], Bb[i:i + m], Cb[i:i + m] = a, b, c
        D += d
        i += m
    return Ab, Bb, Cb, D


def fr(blk, z):
    """frequency response of a discrete block at the points z"""
    a, b, c, d = blk
    if not len(b):
        return np.full(len(z), d, complex)
    X = np.linalg.solve(z[:, None, None] * np.eye(len(b)) - a, np.tile(b, (len(z), 1))[..., None])[..., 0]
    return X @ c + d


def resonant(P, h, K, phi):
    """K (s cos phi - w sin phi) / (s^2 + w^2) at h x 50 Hz, Tustin with the pole frequency pre-warped (exact in z)"""
    wp = 2 / P["T"] * math.tan(h * P["w0"] * P["T"] / 2)
    return dss([K * math.cos(phi), -K * wp * math.sin(phi)], [1, 0, wp * wp], P["T"])


def sogi(P):
    """band-pass k w s / (s^2 + k w s + w^2): unity gain, zero phase at exactly 50 Hz"""
    wp, k = 2 / P["T"] * math.tan(P["w0"] * P["T"] / 2), A("sogi_k")
    return dss([k * wp, 0], [1, k * wp, wp * wp], P["T"])


def pfr(pl, z):
    """plant: sensed current and C_f voltage per volt of converter voltage, at the points z"""
    n = len(pl["Gu"])
    X = np.linalg.solve(z[:, None, None] * np.eye(n) - pl["Phi"], np.tile(pl["Gu"], (len(z), 1))[..., None])[..., 0]
    return X[:, 5], X[:, 6]


def ctrl(P, fc, ff="sogi", kad=0.0, harm=None, decay_rule=True):
    """alpha-beta PR current controller for the stiff-grid crossover fc.  K_p = 2 pi fc (L1 + L2); fundamental resonant
    term; harmonic terms with a phase lead centred on the spread, over the SCRs, of the plant phase seen through the K_p
    loop (kept only if the spread leaves lead_margin_deg).  yv block = C_f-voltage feed-forward (SOGI, 'full' or none)
    minus an optional capacitor-current active damping K_ad C_f dv_C/dt from the sensed C_f voltage."""
    T, w0 = P["T"], P["w0"]
    Kp = 2 * math.pi * fc * (P["L1"] + P["L2"] + P["Ldm"])
    blks = [sogi(P)] if ff == "sogi" else ([(np.zeros((0, 0)), np.zeros(0), np.zeros(0), 1.0)] if ff == "full" else [])
    if kad:
        g = kad * P["Cf"] / T
        blks.append((np.zeros((1, 1)), np.ones(1), np.array([g]), -g))
    yv = par(*blks) if blks else NOBLK
    K = dict(fc=fc, Kp=Kp, yv=yv, ff=ff, kad=kad, harm=[], phi={}, spread={})
    res = [resonant(P, 1, 2 * Kp * 2 * math.pi * A("fz_Hz"), 0.0)]
    for h in (A("harmonics") if harm is None else harm):
        z = np.array([cx(h * w0 * T)])
        ang = []
        for scr in A("scr"):
            Gi, Gv = pfr(plant(P, "nom", "grid", scr), z)
            ang.append(np.angle(Gi / z / (1 + (Kp * Gi - fr(yv, z) * Gv) / z))[0])
        ang = np.unwrap(ang)
        phi = -(ang.max() + ang.min()) / 2
        K["spread"][h] = math.degrees(np.max(np.abs(ang + phi)))
        if K["spread"][h] <= 90 - A("lead_margin_deg"):
            K["harm"].append(h)
            K["phi"][h] = phi
    while True:                                              # drop slow resonant modes (weakest first), rebuild
        K["res"], K["res1"] = par(*res, *[resonant(P, h, 2 * Kp * 2 * math.pi * A("sigma_h_Hz"), K["phi"][h])
                                           for h in K["harm"]]), res[0]
        K["decay"] = {h: -math.inf for h in K["harm"]}
        for scr in A("scr"):
            for cn in ("nom", "low"):
                s = np.log(np.linalg.eigvals(closed(plant(P, cn, "grid", scr), K)[0]).astype(complex)) / T
                for h in K["harm"]:
                    near = [x.real for x in s if abs(abs(x.imag) / (2 * np.pi) - h * F0) < 30]
                    K["decay"][h] = max(K["decay"][h], max(near) if near else 0.0)
        slow = [h for h in K["harm"] if K["decay"][h] > -A("res_decay_frac") * 2 * math.pi * A("sigma_h_Hz")]
        if not slow or not decay_rule:
            return K
        K["harm"].remove(max(slow, key=lambda h: K["decay"][h]))


def p_only(K):
    """the same proportional gain and voltage block without resonant terms (grid-forming inner loop)"""
    return dict(K, res=NOBLK, res1=NOBLK, harm=[], phi={})


# ---------------------------------------------------------------------------------------------------------- linear analysis
FGRID = None


def fgrid(P):
    global FGRID
    if FGRID is None:
        FGRID = np.logspace(0, math.log10(0.999 * P["fs"] / 2), 3000)
    return FGRID


def loop(P, K, pl, f):
    """loop gain at the modulator: L = z^-1 [C(z) G_i(z) - Y(z) G_v(z)] (negative-feedback convention)"""
    z = np.exp(2j * np.pi * f * P["T"])
    Gi, Gv = pfr(pl, z)
    return ((K["Kp"] + fr(K["res"], z)) * Gi - fr(K["yv"], z) * Gv) / z


def margins(f, L):
    """Nyquist geometry, valid on both sides of the antiresonance of converter-current feedback: PM = smallest angular
    distance of a gain crossover from -1; GM = 1/|L| at negative-real-axis crossings with |L| < 1 (gm_lo: |L| where > 1);
    mm = min |1 + L|"""
    m = np.abs(L)
    pm, fc, gm, gml = [], [], [], []
    for k in range(len(f) - 1):
        if (m[k] - 1) * (m[k + 1] - 1) <= 0 and m[k] != m[k + 1]:
            x = (1 - m[k]) / (m[k + 1] - m[k])
            pm.append(180 - abs(math.degrees(np.angle(L[k] + x * (L[k + 1] - L[k])))))
            fc.append(f[k] + x * (f[k + 1] - f[k]))
        a, b = L[k].imag, L[k + 1].imag
        if a * b < 0:
            x = a / (a - b)
            Lx = L[k] + x * (L[k + 1] - L[k])
            if Lx.real < 0:
                (gm if abs(Lx) < 1 else gml).append(20 * math.log10(1 / abs(Lx)) if abs(Lx) < 1 else 20 * math.log10(abs(Lx)))
    i = int(np.argmin(pm)) if pm else None
    return dict(pm=min(pm) if pm else math.inf, f_pm=fc[i] if pm else None, fc=fc, gm=min(gm) if gm else math.inf,
                gm_lo=min(gml) if gml else math.inf, mm=float(np.min(np.abs(1 + L))))


def closed(pl, K):
    """closed current loop (alpha-beta): states plant, applied voltage, resonant, voltage block -> (A, B_ref, B_w)"""
    Phi, Gu = pl["Phi"], pl["Gu"]
    n = len(Gu)
    Ar, Br, Cr, Dr = K["res"]
    Af, Bf, Cf_, Df = K["yv"]
    nr, nf = len(Br), len(Bf)
    N, Ci, Cv, Dc = n + 1 + nr + nf, np.eye(n)[5], np.eye(n)[6], K["Kp"] + Dr
    Ac = np.zeros((N, N))
    Ac[:n, :n], Ac[:n, n] = Phi, Gu
    Ac[n, :n], Ac[n, n + 1:n + 1 + nr], Ac[n, n + 1 + nr:] = -Dc * Ci + Df * Cv, Cr, Cf_
    Ac[n + 1:n + 1 + nr, :n], Ac[n + 1:n + 1 + nr, n + 1:n + 1 + nr] = -np.outer(Br, Ci), Ar
    Ac[n + 1 + nr:, :n], Ac[n + 1 + nr:, n + 1 + nr:] = np.outer(Bf, Cv), Af
    Bref, Bw = np.zeros(N), np.zeros(N)
    Bref[n], Bref[n + 1:n + 1 + nr], Bw[:n] = Dc, Br, Gu
    return Ac, Bref, Bw


def ss_fr(Ac, B, C, z):
    """C (zI - A)^-1 B at the points z (B may be one vector or one per point)"""
    Bm = np.tile(B, (len(z), 1)) if B.ndim == 1 else B
    X = np.linalg.solve(z[:, None, None] * np.eye(len(Ac)) - Ac, Bm[..., None])[..., 0]
    return X @ C


def evaluate(P, K, cn="nom", kind="grid", scr=None, damp="passive", full=True):
    """margins, closed-loop poles and (full) the reference response of the current loop at one case"""
    pl = plant(P, cn, kind, scr, damp)
    f = fgrid(P)
    L = loop(P, K, pl, f)
    r = margins(f, L)
    Ac, Bref, _ = closed(pl, K)
    r["rho"] = float(np.max(np.abs(np.linalg.eigvals(Ac))))
    r["f_res"], r["zeta_res"] = f_res(pl)
    r["ok"] = bool(r["pm"] >= RULE_PM and min(r["gm"], r["gm_lo"]) >= RULE_GM and r["rho"] < 1 - 1e-9)
    if full:                                   # reference response: peak with the full controller, -3 dB without harmonics
        z = np.exp(2j * np.pi * f * P["T"])
        sel = f > 60
        Tz = np.abs(ss_fr(Ac, Bref, np.eye(len(Ac))[5], z))[sel]
        r["peak_db"], r["f_peak"] = float(20 * np.log10(Tz.max())), float(f[sel][np.argmax(Tz)])
        Tg = np.abs(ss_fr(Ac, Bref, np.eye(len(Ac))[pl["ig"]], z))[sel]          # grid current per reference
        r["peak_ig_db"], r["f_peak_ig"] = float(20 * np.log10(Tg.max())), float(f[sel][np.argmax(Tg)])
        A1, B1, _ = closed(pl, dict(K, res=K["res1"]))
        T1 = np.abs(ss_fr(A1, B1, np.eye(len(A1))[5], z))[sel]
        below = np.nonzero(T1 < 1 / math.sqrt(2))[0]
        r["f_bw"] = float(f[sel][below[0]]) if len(below) else None
        r["L"] = L
    return r


# ---------------------------------------------------------------------------------------------------------- nonlinear model
CK = ("xp", "d", "xr", "xf", "xv", "yvp", "iof")            # complex states (synchronous frame)
RK = ("th", "xi", "vd", "thv", "pf", "qf", "vdc", "vdcm", "iem", "xdc", "dws", "dvs", "vmf", "lvt")


def gains(P, K, Kv, f_pll=None, f_dc=None, f_io=None):
    """controller set: current PR (grid following), P-only inner loop + voltage PR (grid forming), PLL, DC link, droop"""
    T = P["T"]
    f_pll, f_dc = f_pll or A("f_pll_Hz"), f_dc or A("f_dc_Hz")
    wn = 2 * math.pi * f_pll / 2.058                          # -3 dB of a type-2 loop with zeta 0.707 = 2.058 w_n
    kp_dc = 2 * math.pi * f_dc * P["Cdc"]
    Af = K["yv"][0]
    return dict(K=K, Kin=p_only(K), Kv=Kv, f_pll=f_pll, pll_kp=2 * 0.707 * wn, pll_ki=wn * wn, reseed=True,
                ff_ss=np.linalg.inv(np.eye(len(Af)) - P["R"] * Af) if len(Af) else None,
                dwmax=2 * math.pi * A("pll_dw_max_Hz"), dwslew=2 * math.pi * A("pll_slew_Hz"), a_vd=lp(A("vd_lpf_Hz"), T), f_dc=f_dc, kp_dc=kp_dc,
                a_tv=math.exp(-T / P["tv"]), a_io=lp(f_io or A("f_io_scan_Hz")[0], T), f_io=f_io or A("f_io_scan_Hz")[0],
                ki_dc=kp_dc * 2 * math.pi * f_dc / A("dc_zero_ratio"), a_vdc=1 - math.exp(-T / P["t_vdc"]),
                a_ib=lp(P["f_ib"], T), a_pq=lp(A("f_pq_Hz"), T), mp=2 * math.pi * F0 * A("droop_p") / P_N,
                nq=A("droop_q") * P["V0"] / P_N, Zv=complex(*A("zv_pu")) * P["zb"])


def step(P, G, S, X, k, inp):
    """one control period: firmware at the sampling instant (sensed states of X), then the plant over the period with the
    voltage commanded one period earlier.  Everything in the synchronous frame; alpha-beta blocks rotated by P['R']."""
    T, R, w0, Ceq = P["T"], P["R"], P["w0"], P["Ceq"]
    pl, xp, brk = S["pl"], X["xp"], S.get("brk")
    yi, yv = xp[5], xp[6]
    N, o = dict(X), {}
    vp = yv * cx(-X["th"])                                    # SRF-PLL on the sensed C_f voltage, amplitude-normalised
    vm = max(abs(vp), 0.1 * P["V0"])
    dw0 = G["pll_kp"] * vp.imag / vm + X["xi"]
    dw = min(max(dw0, -G["dwslew"]), G["dwslew"])
    if brk == "pll":
        o["brk"], dw = dw, S["ext"]
    if S.get("lvrt") and abs(yv) < A("pll_freeze_pu") * P["V0"]:   # ride-through: below the freeze level the PLL free-runs at
        dw = X["xi"]                                              # its held frequency (it would lock onto the module's own
    elif dw == dw0 or (dw0 > 0) != (vp.imag > 0):             # current through the grid impedance); conditional integration
        N["xi"] = min(max(X["xi"] + T * G["pll_ki"] * vp.imag / vm, -G["dwmax"]), G["dwmax"])   # while the slew clamp acts
    N["th"] = X["th"] + T * dw
    N["vd"] = X["vd"] + G["a_vd"] * (vp.real - X["vd"])
    o["dw"] = dw
    Pref, Qref, vcl = inp.get("P", 0.0), inp.get("Q", 0.0), False
    if S.get("dc"):                                           # DC-link voltage loop -> active power reference
        edc = X["vdcm"] - S["vdc_ref"]
        pv = G["kp_dc"] * edc + X["xdc"]
        if brk == "dc":
            o["brk"], pv = pv, S["ext"]
        N["xdc"] = X["xdc"] + T * G["ki_dc"] * edc
        Pref, Qref = X["vdcm"] * (pv + S["kff_dc"] * X["iem"]), 0.0
    if S["mode"] == "gfl":
        i2r = inp["i2ref"] if "i2ref" in inp else (Pref - 1j * Qref) / (1.5 * max(X["vd"], 0.1 * P["V0"]))
        i1r = i2r + 1j * w0 * Ceq * X["vd"]                   # + the C_f current at 50 Hz
        ilim = S["ilim"]
        if S.get("lvrt"):                                     # ride-through state (section 4b): sensed |v_C| below the entry
            vm1, lv_ = abs(yv) / P["V0"], S.get("lvrt_pu") or A("lvrt_pu")   # level arms a hold timer; the dip-time limit applies
            N["lvt"] = (A("lvrt_hold_s") if vm1 < lv_[0] else X["lvt"] if vm1 < lv_[1] else max(0.0, X["lvt"] - T))   # while it runs
            if X["lvt"] > 0:
                ilim = min(ilim, S["lvrt"])
        i1r *= S.get("iscale", 1.0)                           # controlled stop (section 6b): reference ramped to zero
        o["lim"] = abs(i1r) > ilim
        if o["lim"]:
            i1r *= ilim / abs(i1r)
        iref = inp["iref_dq"] if "iref_dq" in inp else i1r * cx(X["th"])
        Kc = G["K"]
    else:                                                     # grid forming: droop + virtual impedance + C_f voltage PR
        io = yi - 1j * w0 * Ceq * yv                          # output current at 50 Hz (no grid-current sensor)
        s = 1.5 * yv * io.conjugate()
        N["pf"], N["qf"] = X["pf"] + G["a_pq"] * (s.real - X["pf"]), X["qf"] + G["a_pq"] * (s.imag - X["qf"])
        dwv = G["mp"] * (S["Pset"] - X["pf"]) + X["dws"]          # droop + the secondary frequency layer (0 unless VF)
        N["thv"] = X["thv"] + T * dwv
        if S.get("sec"):                                          # VF secondary restoration (section 7b): slow integrators on the
            N["dws"] = X["dws"] - T * dwv / S["sec_T"]            # generator's frequency error and on the C_f-voltage magnitude
            N["vmf"] = X["vmf"] + G["a_pq"] * (abs(yv) - X["vmf"])
            N["dvs"] = X["dvs"] + T * (P["V0"] - X["vmf"]) / S["sec_T"]
            if S["sec"] == "reseed":                              # downward re-seed: never more restoration than the present
                w = G["Zv"] * io / cx(X["thv"])                   # output current's virtual-impedance drop needs (+ a margin)
                need = w.real + math.sqrt(max(P["V0"] ** 2 - w.imag ** 2, 0.0)) - S["Vset"] - G["nq"] * (S["Qset"] - X["qf"])
                N["dvs"] = min(N["dvs"], max(need, 0.0) + A("sec_reseed_pu") * P["V0"])
        rv = cx(X["thv"])
        vr = (S["Vset"] + X["dvs"] + G["nq"] * (S["Qset"] - X["qf"]) - G["Zv"] * io / rv) * rv
        ev = vr - yv
        Av, Bv, Cv, Dv = G["Kv"]["res"]
        iu = G["Kv"]["Kp"] * ev + Cv @ X["xv"] + Dv * ev + 1j * w0 * Ceq * vr
        iod = yi - Ceq * (yv - R * X["yvp"]) / T              # load-current estimate i_1 - C_eq dv_C/dt from the sensed values
        if S.get("ioff"):                                     # VF load-current feed-forward (section 7c), low-passed
            N["iof"] = X["iof"] + G["a_io"] * (iod - X["iof"])
            iu += S["ioff"] * N["iof"]
        if S.get("vclamp"):                                   # VF over-voltage deadbeat (section 7c): C_f voltage from the
            vq = (yv - G["a_tv"] * R * X["yvp"]) / (1 - G["a_tv"])   # divider-inverted sample
            vcl = abs(vq) > S["vclamp"] * P["V0"]
        if brk == "gfmv":
            o["brk"], iu = iu.real, S["ext"] + 1j * iu.imag
        o["lim"] = abs(iu) > S["ilim"]
        iref = iu * S["ilim"] / abs(iu) if o["lim"] else iu
        N["xv"] = R * (Av @ X["xv"] + (0.0 if vcl else Bv * (ev + (iref - iu) / G["Kv"]["Kp"])))   # conditional integration
        Kc = G["Kin"]
        o["dwv"], o["s"] = dwv, s
    e = iref - yi                                             # current controller with clamping anti-windup
    Ar, Br, Cr, Dr = Kc["res"]
    Af, Bf, Cf_, Df = Kc["yv"]
    uu = Kc["Kp"] * e + (Cr @ X["xr"] + Dr * e if len(Br) else 0) + (Cf_ @ X["xf"] if len(Bf) else 0) + Df * yv
    if vcl:                                                   # above the threshold the inner loop is replaced by a deadbeat command
        i_n = yi + (X["d"] - vq) * T / P["L1_lo"]              # that ends the next period with i_1 at the unfiltered load-current
        uu = vq + P["L1_lo"] * (iod - i_n) / T                 # estimate (C_f stops charging)
    o["vcl"] = vcl
    if brk == "cur":
        o["brk"], uu = uu.real, S["ext"] + 1j * uu.imag
    N["yvp"] = yv                                             # the previous sensed C_f voltage (divider inversion, load feed-forward)
    if S.get("iclamp"):                                       # per-sample clamp; options of section 4c: a lower level while the
        Ic = S["iclamp_rt"] if S.get("iclamp_rt") and N.get("lvt", X["lvt"]) > 0 else S["iclamp"]   # ride-through state runs, and
        vp_ = (yv - G["a_tv"] * R * X["yvp"]) / (1 - G["a_tv"]) if S.get("vc_comp") else yv          # the divider pole inverted
        uc, o["clamp"] = iclamp(P, Ic, X, yi, vp_, uu, k)
    else:
        uc, o["clamp"] = uu, False
    umax = P["u_frac"] * X["vdcm"]
    o["usat"] = abs(uc) > umax
    u = uc * umax / abs(uc) if o["usat"] else uc
    if len(Br):
        N["xr"] = R * (Ar @ X["xr"] + Br * (e + (u - uu) / Kc["Kp"]))
    if len(Bf):
        N["xf"] = R * (Af @ X["xf"] + Bf * yv)
        if G.get("reseed") and abs(Cf_ @ X["xf"] + Df * yv - yv) > A("ff_reseed_pu") * P["V0"]:
            N["xf"] = G["ff_ss"] @ (R * Bf) * yv                 # SOGI steady state for the present vector
            o["reseed"] = True
    dact = X["d"] * X["vdc"] / X["vdcm"]                      # the modulator normalises with the sensed V_dc
    vgn = inp.get("vgn", 0.0)
    N["xp"] = R * (pl["Phi"] @ xp + pl["Gu"] * dact + pl["Gp"] * inp.get("vgp", 0.0)
                   + (pl["Gn"] * vgn * cx(-2 * w0 * k * T) if vgn else 0.0))
    N["d"] = R * u
    if S.get("dc") or S.get("cdc"):                           # DC link with a constant-power DC/DC, or a DC-side source model
        pc = 1.5 * (dact * xp[0].conjugate()).real            # (S['src'](k, X, p_bridge) -> current into the link, section 7c)
        iext = S["src"](k, X, pc) if "src" in S else S["pcpl"] * (X["vdc"] / S["vdc_ref"]) ** S.get("k_cpl", 0.0) / X["vdc"]
        N["vdc"] = X["vdc"] + T * (iext - pc / X["vdc"]) / P["Cdc"]
        N["vdcm"] = X["vdcm"] + G["a_vdc"] * (X["vdc"] - X["vdcm"])
        N["iem"] = X["iem"] + G["a_ib"] * (iext - X["iem"])
    o["iref"], o["u"] = iref, u
    return N, o


def iclamp(P, Ic, X, yi, yv, uu, k):
    """per-sample current clamp in the PWM update (firmware, section 4b).  Per phase: the converter current at the next update
    instant follows from the sensed current and the command already committed (d - v_C over L1 of the -10 % part); with the
    new command it is predicted one period further; a phase whose prediction leaves +/-Ic gets the command that ends that period
    at Ic (deadbeat on the excess), added as a vector with unit gain on that phase (the floating star shares it with the others).
    Returns the command and whether it acted."""
    T, L = P["T"], P["L1_lo"]
    ph, du = cx(P["w0"] * k * T), 0j
    for p in range(3):
        a = ph * cx(-2 * math.pi * p / 3)
        i, v, d, u = ((x * a).real for x in (yi, yv, X["d"], uu))
        ip = i + (d - v) * T / L
        i2 = ip + (u - v) * T / L
        if abs(i2) > Ic:
            du += (math.copysign(Ic, i2) - i2) * L / T * a.conjugate()
    return uu + du, du != 0


def active(S):
    """states that move in this mode (the others are held, and would show as eigenvalues at 1)"""
    if S["mode"] == "gfl":
        return ("xp", "d", "xr", "xf", "th", "xi", "vd") + (("vdc", "vdcm", "iem", "xdc") if S.get("dc") else ())
    return ("xp", "d", "xf", "xv", "thv", "pf", "qf") + (("dws", "dvs", "vmf") if S.get("sec") else ()) + \
        (("iof", "yvp") if S.get("ioff") else ())


def pack(X, keys):
    v = []
    for k in keys:
        a = np.atleast_1d(X[k])
        v += [a.real, a.imag] if k in CK else [a.astype(float)]
    return np.concatenate(v)


def unpack(v, X, keys):
    Y, i = dict(X), 0
    for k in keys:
        if k in CK:
            n = np.atleast_1d(X[k]).size
            c = v[i:i + n] + 1j * v[i + n:i + 2 * n]
            i += 2 * n
            Y[k] = c if np.ndim(X[k]) else complex(c[0])
        else:
            Y[k] = float(v[i])
            i += 1
    return Y


def jac(F, x, nout=None):
    """central-difference Jacobian"""
    h = 1e-6 * np.maximum(1.0, np.abs(x))
    cols = []
    for i in range(len(x)):
        e = np.zeros(len(x))
        e[i] = h[i]
        cols.append((F(x + e) - F(x - e)) / (2 * h[i]))
    return np.array(cols).T


def setup(P, G, mode="gfl", scr=None, kind="grid", cn="nom", damp="passive", RL=None, Pop=0.0, Qop=0.0, vdc=750.0,
          dc=False, pcpl=0.0, kff_dc=1.0, ilim=None, Vset=None, th_v=0.0, sec=False, sec_T=None, iclamp=None, lvrt=None):
    """configuration S and a phasor initial state X (plant exact in steady state; controller states settle in equil/pre-run)"""
    pl = plant(P, cn, kind, scr, damp, RL)
    grid_on = kind in ("grid", "open", "load")
    S = dict(mode=mode, pl=pl, ilim=ilim or P["I_lim"], dc=dc, pcpl=pcpl, kff_dc=kff_dc, vdc_ref=vdc, Pset=Pop, Qset=Qop,
             Vset=Vset or P["V0"], inp=dict(vgp=complex(P["V0"]) if grid_on else 0j, P=Pop, Q=Qop), sec=sec, sec_T=sec_T,
             iclamp=iclamp, lvrt=lvrt)
    assert not sec or sec_T, "secondary restoration needs its time constant"
    M = np.linalg.inv(1j * P["w0"] * np.eye(8) - pl["A"])
    xu, xg = M @ pl["Bu"], M @ pl["Bg"] * S["inp"]["vgp"]
    tgt, val = (3, (Pop - 1j * Qop) / (1.5 * P["V0"])) if mode == "gfl" else (1, S["Vset"] * cx(th_v))
    U = (val - xg[tgt]) / xu[tgt]
    xp = xu * U + xg
    yv = xp[6]
    Af, Bf, _, _ = G["K"]["yv"]
    io = xp[5] - 1j * P["w0"] * P["Ceq"] * yv
    s = 1.5 * yv * io.conjugate()
    X = dict(xp=xp.astype(complex), d=complex(U), xr=np.zeros(len(G["K"]["res"][1]), complex),
             xf=(np.linalg.solve(np.eye(len(Bf)) - P["R"] * Af, P["R"] * Bf * yv) if len(Bf) else np.zeros(0, complex)),
             xv=np.zeros(len(G["Kv"]["res"][1]), complex), th=cmath.phase(yv), xi=0.0, vd=abs(yv), thv=th_v,
             pf=s.real, qf=s.imag, vdc=vdc, vdcm=vdc, iem=pcpl / vdc, xdc=pcpl / vdc * (1 - kff_dc),
             dws=G["mp"] * (s.real - Pop) if sec else 0.0, dvs=0.0, vmf=abs(yv), lvt=0.0, yvp=complex(yv),
             iof=complex(xp[5] - P["Ceq"] * yv * (1 - P["R"]) / P["T"]))
    return S, X


def equil(P, G, S, X, it=12):
    """Newton on the synchronous-frame fixed point (exists while grid connected); returns X* and the residual"""
    keys = active(S)

    def F(v):
        return pack(step(P, G, S, unpack(v, X, keys), 0, S["inp"])[0], keys)

    x = pack(X, keys)
    for _ in range(it):
        r = F(x) - x
        if np.linalg.norm(r) < 1e-9 * (1 + np.linalg.norm(x)):
            break
        x = x - np.linalg.lstsq(jac(F, x) - np.eye(len(x)), r, rcond=None)[0]
    return unpack(x, X, keys), float(np.linalg.norm(F(x) - x) / (1 + np.linalg.norm(x)))


def linmodes(P, G, S, X):
    """closed-loop Jacobian eigenvalues -> spectral radius and the least-damped mode below 1 kHz (zeta, f)"""
    keys = active(S)
    J = jac(lambda v: pack(step(P, G, S, unpack(v, X, keys), 0, S["inp"])[0], keys), pack(X, keys))
    lam = np.linalg.eigvals(J)
    s = np.log(lam.astype(complex)) / P["T"]
    f = np.abs(s.imag) / (2 * np.pi)
    zeta = np.where(np.abs(s) > 0, -s.real / np.maximum(np.abs(s), 1e-12), 1.0)
    sel = (f < 1000) & (np.abs(lam) > 1e-6)
    i = np.argmin(np.where(sel, zeta, np.inf))
    return dict(rho=float(np.max(np.abs(lam))), zeta=float(zeta[i]), f=float(f[i]), lam=lam)


def lin_break(P, G, S, X, brk, f):
    """loop gain at a broken scalar signal (pll: PLL frequency, dc: DC-loop output, gfmv: voltage-loop output)"""
    keys = active(S)
    S1 = dict(S, brk=brk, ext=0.0)
    e0 = step(P, G, S1, X, 0, S["inp"])[1]["brk"]

    def F(v):
        S1["ext"] = v[-1]
        N, o = step(P, G, S1, unpack(v[:-1], X, keys), 0, S["inp"])
        return np.concatenate([pack(N, keys), [o["brk"]]])

    J = jac(F, np.concatenate([pack(X, keys), [e0]]))
    Am, B, C, D = J[:-1, :-1], J[:-1, -1], J[-1, :-1], J[-1, -1]
    z = np.exp(2j * np.pi * f * P["T"])
    return -(ss_fr(Am, B, C, z) + D)


def hold_state(P, blk, val):
    """state of a resonant stack on its synchronous-frame integrator mode giving the output val (bumpless transfer)"""
    a, b, c, d = blk
    w, V = np.linalg.eig(P["R"] * a)
    v = V[:, np.argmin(np.abs(w - 1))]
    return v * val / (c @ v)


def run(P, G, S, X, t_end, ev=None):
    """sample-by-sample simulation; ev(t, S, X) may change the configuration, the inputs or the mode (X in place)"""
    n, X = int(round(t_end / P["T"])), dict(X)
    rec = {k: np.zeros(n, complex) for k in ("i1", "ig", "i2", "vc", "yv", "iref", "u")}
    rec.update({k: np.zeros(n) for k in ("t", "th", "dw", "xi", "vdc", "lim", "usat", "thv", "clamp", "dvs")})
    for k in range(n):
        t = k * P["T"]
        if ev and ev(t, S, X) == "stop":                      # gates off (a trip): the record ends at sample k, X is the state there
            rec = {key: v[:k] for key, v in rec.items()}
            break
        xp = X["xp"]
        rec["t"][k], rec["i1"][k], rec["ig"][k], rec["i2"][k], rec["vc"][k] = t, xp[0], xp[S["pl"]["ig"]], xp[3], xp[1]
        rec["yv"][k] = xp[6]
        rec["th"][k], rec["vdc"][k], rec["thv"][k], rec["xi"][k] = X["th"], X["vdc"], X["thv"], X["xi"]
        X, o = step(P, G, S, X, k, S["inp"])
        rec["iref"][k], rec["u"][k], rec["dw"][k] = o["iref"], o["u"], o["dw"]
        rec["lim"][k], rec["usat"][k], rec["clamp"][k], rec["dvs"][k] = o["lim"], o["usat"], o["clamp"], X["dvs"]
    return rec, X


def pk_phase(r, sl=slice(None), w0=2 * math.pi * F0):
    """largest instantaneous phase current of the averaged model in a window (abc from the synchronous frame)"""
    ab = r["i1"][sl] * np.exp(1j * w0 * r["t"][sl])
    return float(max(np.max(np.abs((ab * cx(-2 * math.pi * p / 3)).real)) for p in range(3)))


def agreement(P, G):
    """self-check: the synchronous-frame simulator (firmware code path) against the alpha-beta linear closed loop, for a
    60 A reference step at the stiff grid (PLL bypassed) -> max deviation of i1, A"""
    S, X = setup(P, G, "gfl", None, Pop=0.5 * P_N)
    X, _ = equil(P, G, S, X)
    i0, n, w0, T = complex(X["xp"][5]), 1500, P["w0"], P["T"]
    S["inp"]["iref_dq"] = i0
    r, _ = run(P, G, S, dict(X), n * T, lambda t, S_, X_: S_["inp"].update(iref_dq=i0 + 60.0) if t >= 2e-3 else None)
    Ac, Bref, _ = closed(S["pl"], G["K"])
    x = np.concatenate([X["xp"], [X["d"]], X["xr"], X["xf"]]).astype(complex)
    Bg = np.zeros(len(Ac), complex)
    Bg[:8] = S["pl"]["Gp"] * S["inp"]["vgp"]
    err = 0.0
    for k in range(n):
        ph = np.exp(1j * w0 * k * T)
        err = max(err, abs(x[0] / ph - r["i1"][k]))
        x = Ac @ x + Bref * (i0 + (60.0 if k * T >= 2e-3 else 0.0)) * ph + Bg * ph
    return err


# ---------------------------------------------------------------------------------------------------------- 1. current loop
CORNERS = ("nom", "low", "high", "slow")


def design_fc(P):
    """sweep the stiff-grid crossover over the rule at every admitted SCR and corner (passive branch, full controller); the
    rule fails at both ends (low: the weak grid pulls the crossover onto the resonant terms; high: the stiff-grid resonance
    near f_s/6).  Choice: the grid point nearest the geometric centre of the feasible band (equal ratio headroom to both
    ends).  Also the delay-only limit: the same controller on an L1 + L2 plant without C_f."""
    lo, hi, st = A("fc_grid_Hz")
    rows = []
    for fc in np.arange(lo, hi + st / 2, st):
        K = ctrl(P, fc)
        ev = [evaluate(P, K, cn, "grid", scr, full=False) for scr in A("scr") for cn in CORNERS]
        rows.append(dict(fc=float(fc), pm=min(e["pm"] for e in ev), gm=min(min(e["gm"], e["gm_lo"]) for e in ev),
                         rho=max(e["rho"] for e in ev), ok=all(e["ok"] for e in ev), harm=list(K["harm"])))
    feas = [r["fc"] for r in rows if r["ok"]]
    assert feas, "no current-loop crossover meets the margin rule"
    band = (min(feas), max(feas))
    assert all(r["ok"] for r in rows if band[0] <= r["fc"] <= band[1]), "feasible crossovers are not one band"
    best = min(feas, key=lambda fc: abs(math.log(fc / math.sqrt(band[0] * band[1]))))
    Ls = P["L1"] + P["L2"] + P["Ldm"]                          # delay-only limit: pure inductor + sensor chain
    Ac = np.array([[-P["R1"] / Ls, 0, 0, 0, 0, 0, 0, 0]] + [[0] * 8] * 4 +
                  [[1 / P["ti"], 0, 0, 0, 0, -1 / P["ti"], 0, 0]] + [[0] * 8] * 2, float)
    M = np.zeros((9, 9))
    M[:8, :8], M[0, 8] = Ac, 1 / Ls
    E = expm(M * P["T"])
    pl = dict(Phi=E[:8, :8], Gu=E[:8, 8])
    f = fgrid(P)
    dl = None
    for fc in np.arange(lo, 3 * hi, st):
        r = margins(f, loop(P, ctrl(P, fc, ff="none", harm=()), pl, f))
        if r["pm"] < RULE_PM or min(r["gm"], r["gm_lo"]) < RULE_GM:
            break
        dl = float(fc)
    return best, band, rows, dl


def table_current(P, K):
    """margins table: SCR x damping (none / passive as drawn / capacitor-current active damping from the sensed C_f
    voltage, no branch) x corner; the active-damping gain is the best of a scan.  The grid-forming inner loop is judged
    with its voltage loop closed (design_gfm_voltage): alone it leaves the 50 Hz C_f voltage unregulated by design."""
    rows = []
    for damp in ("none", "passive"):
        for scr in A("scr"):
            for cn in CORNERS + ("env",):
                r = evaluate(P, K, cn, "grid", scr, damp)
                rows.append(dict(damp=damp, scr=scr, cn=cn, **{k: v for k, v in r.items() if k != "L"}))
    scan = []
    for kad in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
        Ka = ctrl(P, K["fc"], kad=kad)
        ev = [evaluate(P, Ka, cn, "grid", scr, "none", full=False) for scr in A("scr") for cn in CORNERS]
        scan.append(dict(kad=kad, pm=min(e["pm"] for e in ev), gm=min(min(e["gm"], e["gm_lo"]) for e in ev),
                         ok=all(e["ok"] for e in ev), rho=max(e["rho"] for e in ev),
                         worst=min(ev, key=lambda e: (e["ok"], e["pm"]))))
    kbest = max(scan, key=lambda s: (s["ok"], min(s["pm"] - RULE_PM, 5 * (s["gm"] - RULE_GM)) if s["rho"] < 1 else -1e9))
    Ka = ctrl(P, K["fc"], kad=kbest["kad"])
    for scr in A("scr"):
        for cn in CORNERS:
            r = evaluate(P, Ka, cn, "grid", scr, "none")
            rows.append(dict(damp="active", scr=scr, cn=cn, **{k: v for k, v in r.items() if k != "L"}))
    return rows, scan, kbest["kad"]


def study_tolerance(P, K):
    """review R2-01: the current loop at the mixed corners of the drawn assembly's tolerances (P['tol']).  Full factorial: L1, L2,
    C_f, C_d, R_d each at low / nominal / high, the current-chain delay nominal and x slow_sensor, every studied grid (3^5 x 2 x 4
    cases); plus a seeded random sample inside the same box with the delay anywhere from the sensor's typical step response to
    x slow_sensor and 1/SCR uniform on [0, 1/SCR_min] (stiff included).  Design gains; margins and the largest closed-loop pole"""
    t, dm = P["tol"], P["Ldm"]
    lev = dict(L1=(P["L1_lo"] / P["L1"], 1.0, P["L1_hi"] / P["L1"]),
               L2=((P["L2_lo"] + dm) / (P["L2"] + dm), 1.0, (P["L2_hi"] + dm) / (P["L2"] + dm)),
               Cf=(1 - t["Cf"], 1.0, 1 + t["Cf"]), Cd=(1 - t["Cd"], 1.0, 1 + t["Cd"]), Rd=(1 - t["Rd"], 1.0, 1 + t["Rd"]))

    def one(k, scr):
        cn = ("mix",) + tuple(float(x) for x in k)
        r = evaluate(P, K, cn, "grid", scr, full=False)
        _PL.pop((cn, "grid", scr, "passive", None, P["tv"], P["ti"]), None)          # one-off plants: not cached
        return dict(scr=scr, k=cn[1:], pm=r["pm"], gm=min(r["gm"], r["gm_lo"]), rho=r["rho"], ok=r["ok"])
    fact = [one(k + (d,), scr) for k in itertools.product(*lev.values()) for d in (1.0, A("slow_sensor")) for scr in A("scr")]
    n_r, seed = A("tol_random")
    rng, x_max = np.random.default_rng(seed), 1 / min(s for s in A("scr") if s)
    rand = []
    for _ in range(n_r):
        k = tuple(rng.uniform(v[0], v[2]) for v in lev.values()) + (rng.uniform(P["ti_typ"] / P["ti"], A("slow_sensor")),)
        x = float(rng.uniform(0.0, x_max))
        rand.append(one(k, 1 / x if x > 0 else None))
    names = tuple(lev) + ("t_i",)
    lab = lambda d: ", ".join(f"{n} x{v:.3f}" for n, v in zip(names, d["k"]))
    summ = lambda rows: dict(n=len(rows), n_pass=sum(r["ok"] for r in rows), pm=min(r["pm"] for r in rows),
                             pm_at=lab(min(rows, key=lambda r: r["pm"])) + f", {scr_name(min(rows, key=lambda r: r['pm'])['scr'])}",
                             gm=min(r["gm"] for r in rows), gm_at=lab(min(rows, key=lambda r: r["gm"])) + f", {scr_name(min(rows, key=lambda r: r['gm'])['scr'])}",
                             rho=max(r["rho"] for r in rows), stable=all(r["rho"] < 1 for r in rows))
    per = []
    for scr in A("scr"):
        rr = [r for r in fact if r["scr"] == scr]
        nom = next(r for r in rr if r["k"] == (1.0,) * 6)
        per.append(dict(scr=scr, nom_pm=nom["pm"], nom_gm=nom["gm"], **{k: v for k, v in summ(rr).items() if k != "n"}))
    return dict(tol=dict(t), levels={k: list(v) for k, v in lev.items()}, delay_s=dict(typ=P["ti_typ"], nom=P["ti"], slow=P["ti"] * A("slow_sensor")),
                factorial=summ(fact), random=dict(summ(rand), seed=seed, scr_min=min(s for s in A("scr") if s)), per_scr=per,
                all_pass=all(r["ok"] for r in fact + rand), reviewer=REVIEW_R2["R2-01"])


def ff_variants(P, K):
    """why the C_f-voltage feed-forward is band-passed: margins with none / SOGI / full feed-forward (nominal corner)"""
    out = {}
    for ff in ("none", "sogi", "full"):
        Kf = ctrl(P, K["fc"], ff=ff)
        out[ff] = {str(scr): evaluate(P, Kf, "nom", "grid", scr, full=False) for scr in A("scr")}
    return out


def gfm_bw(P, Kin, Kv, kind, cn, f, z, pl=None):
    """closed grid-forming voltage loop: -3 dB bandwidth of v_C / v_ref (first frequency above 60 Hz below 1/sqrt 2)"""
    pl = pl or plant(P, cn, kind, None)
    Z, (_, Bref, _) = cascade(P, pl, Kin, Kv), closed(pl, Kin)
    Bv = np.concatenate([Bref * (Kv["Kp"] + Kv["res"][3]), Kv["res"][1]])
    below = np.nonzero((np.abs(ss_fr(Z, Bv, np.eye(len(Z))[6], z)) < 1 / math.sqrt(2)) & (f > 60))[0]
    return float(f[below[0]]) if len(below) else None


def design_gfm_voltage(P, K):
    """grid-forming C_f-voltage loop (alpha-beta PR around the P-only inner current loop with the feed-forward): scan the
    crossover parameter f_cv (K_pv = 2 pi f_cv C_eq) and the PR zero ratio against the rule off-grid at no load and
    rated resistive load, every corner, with the cascade's poles.  The feasible set is not one band (two islands per
    ratio, split by a gain-margin notch), so no band centre is taken: choice = the pair with the largest headroom
    min(PM - rule, 5 (GM - rule)).  Also returned: the fastest pair meeting the rule (closed-loop bandwidth at rated
    load), the 'achievable' figure."""
    f, Kin = fgrid(P), p_only(K)
    z = np.exp(2j * np.pi * f * P["T"])
    rows = []
    for ratio in A("fzv_ratios"):
        for fcv in np.arange(100.0, A("f_cv_max_Hz") + 1, 100.0):
            Kpv = 2 * math.pi * fcv * P["Ceq"]
            Kv = dict(Kp=Kpv, res=resonant(P, 1, 2 * Kpv * 2 * math.pi * fcv / ratio, 0.0), yv=NOBLK)
            ev = []
            for kind in ("nl", "rl"):
                for cn in CORNERS:
                    pl = plant(P, cn, kind, None)
                    Ac, Bref, _ = closed(pl, Kin)
                    m = margins(f, (Kv["Kp"] + fr(Kv["res"], z)) * ss_fr(Ac, Bref, np.eye(len(Ac))[6], z))
                    m.update(kind=kind, cn=cn, rho=float(np.max(np.abs(np.linalg.eigvals(cascade(P, pl, Kin, Kv))))))
                    ev.append(m)
            pm, gm = min(m["pm"] for m in ev), min(min(m["gm"], m["gm_lo"]) for m in ev)
            rows.append(dict(fcv=float(fcv), ratio=ratio, pm=pm, gm=gm, rho=max(m["rho"] for m in ev), kv=Kv, ev=ev,
                             ok=bool(pm >= RULE_PM and gm >= RULE_GM and all(m["rho"] < 1 - 1e-9 for m in ev)),
                             head=min(pm - RULE_PM, 5 * (gm - RULE_GM))))
    feas = [r for r in rows if r["ok"]]
    assert feas, "no grid-forming voltage-loop gain pair meets the rule"
    for r in feas:
        r["f_bw_rl"] = gfm_bw(P, Kin, r["kv"], "rl", "nom", f, z)
    best = max(feas, key=lambda r: r["head"])
    for m in best["ev"]:
        m["f_bw"] = gfm_bw(P, Kin, best["kv"], m["kind"], m["cn"], f, z)
    return best, max(feas, key=lambda r: r["f_bw_rl"] or 0.0), rows


def cascade(P, pl, Kin, Kv):
    """alpha-beta closed loop of the grid-forming cascade (voltage PR -> current P + feed-forward -> plant), for its poles"""
    Ac, Bref, _ = closed(pl, Kin)
    Av, Bv, Cv, Dv = Kv["res"]
    n, m, Cy = len(Ac), len(Bv), np.eye(len(Ac))[6]
    Kd = Kv["Kp"] + Dv                                       # i_ref = Kd (v_ref - y_v) + Cv x_v
    Z = np.zeros((n + m, n + m))
    Z[:n, :n] = Ac - Kd * np.outer(Bref, Cy)
    Z[:n, n:] = np.outer(Bref, Cv)
    Z[n:, :n], Z[n:, n:] = -np.outer(Bv, Cy), Av
    return Z


def vloop_ff(P, Kin, Kv, a, kinds=("nl", "rl"), corners=CORNERS):
    """VF cascade with the load-current feed-forward (section 7c), alpha-beta: i_ref = C_v(z) (v_ref - y_v) + io_hat, io_hat =
    LPF(z) (y_i - C_eq (y_v - y_v[k-1]) / T), LPF(z) = a z / (z - 1 + a).  Closed-loop matrix over [inner closed loop, voltage PR,
    filter, delayed y_v] for the poles; loop gain broken at i_ref, L = C T_v - LPF T_i + LPF C_eq D T_v (T_v, T_i: inner closed
    loop i_ref -> y_v, y_i), for the margins"""
    f = fgrid(P)
    z = np.exp(2j * np.pi * f * P["T"])
    Av, Bv, Cv, Dv = Kv["res"]
    Kd, c, out = Kv["Kp"] + Dv, P["Ceq"] / P["T"], []
    for kind in kinds:
        for cn in corners:
            pl = plant(P, cn, kind, None)
            Ac, Bref, _ = closed(pl, Kin)
            n, m = len(Ac), len(Bv)
            Cy, Ci = np.eye(n)[6], np.eye(n)[5]
            row_io = np.concatenate([a * (Ci - c * Cy), np.zeros(m), [1 - a, a * c]])
            Z = np.zeros((n + m + 2, n + m + 2))
            Z[:n, :n] = Ac
            Z[:n] += np.outer(Bref, np.concatenate([-Kd * Cy, Cv, [0.0, 0.0]]) + row_io)
            Z[n:n + m, :n], Z[n:n + m, n:n + m] = -np.outer(Bv, Cy), Av
            Z[n + m], Z[n + m + 1, :n] = row_io, Cy
            Tv, Ti = ss_fr(Ac, Bref, Cy, z), ss_fr(Ac, Bref, Ci, z)
            lpf = a * z / (z - 1 + a)
            m_ = margins(f, (Kv["Kp"] + fr(Kv["res"], z)) * Tv - lpf * Ti + lpf * P["Ceq"] * (1 - 1 / z) / P["T"] * Tv)
            m_.update(kind=kind, cn=cn, rho=float(np.max(np.abs(np.linalg.eigvals(Z)))))
            m_["ok"] = bool(m_["pm"] >= RULE_PM and min(m_["gm"], m_["gm_lo"]) >= RULE_GM and m_["rho"] < 1 - 1e-9)
            out.append(m_)
    return out


# ---------------------------------------------------------------------------------------------------------- 2. responses
def ripple_peak(P, r, sl=slice(None)):
    """hardware-relevant peak: largest instantaneous phase current of the averaged model + half the worst PWM ripple"""
    return pk_phase(r, sl) + P["ripple_pp"] / 2


def pll_frame(r):
    return r["i1"] * np.exp(-1j * r["th"])


def tier_of(P, pk):
    """the lowest current tier whose peak (tier rms x sqrt 2) an averaged peak stays within"""
    return next((f"{k} ({v:g} A rms)" for k, v in sorted(P["tiers"].items(), key=lambda x: x[1])
                 if pk <= v * math.sqrt(2) * 1.0001), "above every tier")


def settle(t, x, final, band, t0):
    """time after t0 until x stays within band of final"""
    out = np.nonzero(np.abs(x - final) > band)[0]
    return float(t[out[-1]] - t0) if len(out) else 0.0


def study_steps(P, G):
    """current loop: reference step 0 -> rated, grid voltage -10 % / +10 %, 30 deg phase jump; stiff and SCR 5, 125 kW
    export at 750 V (reference step from zero); +10 % repeated at 600 V (modulation limit)"""
    out = []
    t0, Ir, V0 = 0.005, P["I_rated"], P["V0"]
    cases = [("ref step 0 -> rated", 0.0, None), ("grid -10 %", P_N, ("v", 0.9)), ("grid +10 %", P_N, ("v", 1.1)),
             ("phase jump 30 deg", P_N, ("ph", math.radians(A("codes")["phase_jump_deg"])))]
    for scr in (None, 5.0):
        for name, Pop, dist in cases:
            for vdc in ((750.0, 600.0) if name == "grid +10 %" else (750.0,)):
                S, X = setup(P, G, "gfl", scr, Pop=Pop, vdc=vdc)
                X, res = equil(P, G, S, X)
                if dist is None:
                    S["inp"]["i2ref"] = 0j

                def ev(t, S_, X_, dist=dist):
                    if t < t0:
                        return
                    if dist is None:
                        S_["inp"]["i2ref"] = complex(Ir)
                    elif dist[0] == "v":
                        S_["inp"]["vgp"] = V0 * dist[1]
                    else:
                        S_["inp"]["vgp"] = V0 * cx(dist[1])
                r, _ = run(P, G, S, X, t0 + (0.04 if dist is None else 0.12), ev)
                ip = pll_frame(r)
                k0 = int(t0 / P["T"])
                i_end = ip[-1]
                d = dict(case=name, scr=scr, vdc=vdc, eq_res=res, pk=ripple_peak(P, r), pk_avg=pk_phase(r),
                         settle_ms=1e3 * settle(r["t"][k0:], ip[k0:], i_end, 0.05 * Ir, t0),
                         dev_A=float(np.max(np.abs(ip[k0:] - i_end))), usat_ms=1e3 * P["T"] * float(np.sum(r["usat"])),
                         lim_ms=1e3 * P["T"] * float(np.sum(r["lim"])), err_end=float(abs(r["iref"][-1] - r["i1"][-1])))
                if dist is None:
                    dd = ip[k0:].real
                    d["overshoot_pct"] = 100 * (dd.max() - i_end.real) / (i_end.real - ip[k0 - 1].real)
                d["tier"], d["runaway"] = tier_of(P, d["pk_avg"]), d["pk"] > P["win"]["hi"]
                d["rec"] = r if (vdc == 750.0) else None
                out.append(d)
    return out


def study_faults(P, G):
    """grid faults at 125 kW export with the power reference held (so the current reference saturates): symmetric
    0.5 pu and 0.1 pu dips and an unbalanced dip (V+ 0.6, V- 0.3 pu), 150 ms then recovery; stiff and SCR 5.  Design =
    SOGI feed-forward re-seeded on large steps, limit 1.2 x I_max.  Variants: without the re-seed, without the re-seed at
    the 2-min tier limit, and without any feed-forward"""
    c = A("codes")
    t0, t1 = 0.01, 0.01 + c["dip_s"]
    cases = [("0.5 pu dip", c["dip_pu"], 0.0), ("0.1 pu dip", c["deep_pu"], 0.0), ("unbalanced 0.6/0.3 pu", 0.6, 0.3)]
    runs = [(n, vp, vn, scr, "sogi", None) for scr in (None, 5.0) for n, vp, vn in cases]
    i2m = P["tiers"]["2_min"] * math.sqrt(2)
    runs += [(n + ", no re-seed", vp, vn, scr, "noreseed", None) for scr in (None, 5.0) for n, vp, vn in cases]
    runs += [(n + f", no re-seed, limit {P['tiers']['2_min']:g} A rms", vp, vn, scr, "noreseed", i2m)
             for scr in (None, 5.0) for n, vp, vn in cases]
    runs += [("0.5 pu dip, no feed-forward", c["dip_pu"], 0.0, 5.0, "none", None)]
    out = []
    for name, vp, vn, scr, ff, ilim in runs:
        Gx = (G if ff == "sogi" else dict(G, reseed=False) if ff == "noreseed" else
              dict(G, K=ctrl(P, G["K"]["fc"], ff=ff), Kin=p_only(ctrl(P, G["K"]["fc"], ff=ff)), reseed=False))
        S, X = setup(P, Gx, "gfl", scr, Pop=P_N, ilim=ilim)
        X, _ = equil(P, Gx, S, X)

        def ev(t, S_, X_, vp=vp, vn=vn):
            on = t0 <= t < t1
            S_["inp"]["vgp"] = complex(P["V0"] * (vp if on else 1.0))
            S_["inp"]["vgn"] = complex(P["V0"] * vn) if on else 0j
        r, _ = run(P, Gx, S, X, t1 + 0.1, ev)
        k0, k1 = int(t0 / P["T"]), int(t1 / P["T"])
        pk = [ripple_peak(P, r, slice(k0, k1)), ripple_peak(P, r, slice(k1, None))]
        win = [P["win"]["lo"], P["win"]["nom"]]
        out.append(dict(case=name, scr=scr, ff=ff, ilim=S["ilim"], pk_dip=pk[0], pk_rec=pk[1],
                        lim_ms=1e3 * P["T"] * float(np.sum(r["lim"])), usat_ms=1e3 * P["T"] * float(np.sum(r["usat"])),
                        over_lo=max(pk) > win[0], over_nom=max(pk) > win[1],
                        i_hold=float(np.mean(np.abs(r["i1"][k1 - int(0.02 / P["T"]):k1]))), rec=r))
    return out


def study_divider(P, G):
    """what-if for the hardware: the stiff-grid 0.1 pu dip with a faster C_f-voltage divider (30 kHz instead of the drawn
    pole) - the divider lag is part of the delay before the converter voltage follows the grid"""
    c = A("codes")
    out = {}
    for fdiv in (P["f_vdiv"], 30e3):
        Pq = dict(P, tv=1 / (2 * math.pi * fdiv))
        S, X = setup(Pq, G, "gfl", None, Pop=P_N)
        X, _ = equil(Pq, G, S, X)
        r, _ = run(Pq, G, S, X, 0.01 + c["dip_s"] + 0.05, lambda t, S_, X_: S_["inp"].update(
            vgp=complex(Pq["V0"] * (c["deep_pu"] if 0.01 <= t < 0.01 + c["dip_s"] else 1.0))))
        out[fdiv] = ripple_peak(Pq, r)
    return out


# ---------------------------------------------------------------------------------------------------------- 3. PLL
def study_pll(P, G):
    """SRF-PLL against the weak grid: for each SCR and +/-125 kW, the PLL loop gain (broken at the PLL frequency, every
    other loop closed) and the closed-loop poles over the PLL bandwidth; then the 30 deg phase jump and the 2 Hz/s ramp
    at the design bandwidth (time domain)"""
    fl = np.geomspace(5.0, 400.0, 22)
    f = np.geomspace(0.5, 2000.0, 1500)
    sweep = []
    for scr in A("scr"):
        for Pop in (P_N, -P_N):
            S, X = setup(P, G, "gfl", scr, Pop=Pop)
            X, res = equil(P, G, S, X)
            rows = []
            for fp in fl:
                Gp = gains(P, G["K"], G["Kv"], f_pll=fp)
                m = margins(f, lin_break(P, Gp, S, X, "pll", f))
                lm = linmodes(P, Gp, S, X)
                rows.append(dict(f_pll=float(fp), pm=m["pm"], gm=min(m["gm"], m["gm_lo"]), rho=lm["rho"],
                                 ok=bool(m["pm"] >= RULE_PM and min(m["gm"], m["gm_lo"]) >= RULE_GM and lm["rho"] < 1)))
            okl = [r["ok"] for r in rows]
            fmax = rows[okl.index(False) - 1]["f_pll"] if False in okl and okl.index(False) > 0 else (
                rows[-1]["f_pll"] if all(okl) else None)
            funst = next((r["f_pll"] for r in rows if r["rho"] >= 1), None)
            Gd = gains(P, G["K"], G["Kv"])
            md = margins(f, lin_break(P, Gd, S, X, "pll", f))
            sweep.append(dict(scr=scr, P=Pop, rows=rows, f_max=fmax, f_unstable=funst, pm_design=md["pm"],
                              gm_design=min(md["gm"], md["gm_lo"]), eq_res=res))
    c, t0 = A("codes"), 0.01
    tsim = []
    for scr in (None, 5.0):
        for name in ("phase jump", "frequency ramp"):
            S, X = setup(P, G, "gfl", scr, Pop=P_N)
            X, _ = equil(P, G, S, X)
            if name == "phase jump":
                ev = (lambda t, S_, X_: S_["inp"].update(vgp=P["V0"] * cx(math.radians(c["phase_jump_deg"])))
                      if t >= t0 else None)
                tend = 0.15
            else:
                ev = (lambda t, S_, X_: S_["inp"].update(vgp=P["V0"] * cx(-math.pi * c["rocof_Hz_s"] * (t - t0) ** 2))
                      if t >= t0 else None)
                tend = 0.4
            r, _ = run(P, G, S, X, tend, ev)
            err = np.degrees(np.angle(np.exp(1j * r["th"]) / r["yv"]))   # against its own (sensed) input
            k0 = int(t0 / P["T"])
            d = dict(case=name, scr=scr, err_pk=float(np.max(np.abs(err[k0:]))), df_pk=float(np.max(np.abs(r["xi"][k0:]))) / (2 * math.pi),
                     pk=ripple_peak(P, r), err_end=float(err[-1]), rec=r)
            if name == "phase jump":
                d["settle_ms"] = 1e3 * settle(r["t"][k0:], err[k0:], 0.0, 2.0, t0)
                kp = k0 + int(np.argmax(np.abs(err[k0:])))            # what the angle-error peak is made of
                d["t_err_pk_us"] = 1e6 * (kp - k0) * P["T"]
                d["v_ang_pk"] = float(np.degrees(np.angle(r["yv"][kp] / r["yv"][k0 - 1])))
                d["pll_moved_pk"] = float(np.degrees(r["th"][kp] - r["th"][k0 - 1]))
            else:
                wn = 2 * math.pi * G["f_pll"] / 2.058
                d["err_theory_deg"] = math.degrees(2 * math.pi * c["rocof_Hz_s"] / wn ** 2)
            tsim.append(d)
    return dict(sweep=sweep, tsim=tsim, f_list=fl)


# ---------------------------------------------------------------------------------------------------------- 4. DC link
def study_dc(P, G):
    """the inverter holds V_dc while the DC/DC is a constant-power source (+) or load (-): loop broken at the voltage-loop
    output, every other loop closed; with and without the measured DC-current feed-forward; then load steps"""
    f = np.geomspace(0.5, 2000.0, 1500)
    lin = []
    for scr in (None, 5.0):
        for vdc in (600.0, 750.0):
            for pc in (A("p_cpl_W"), -A("p_cpl_W")):
                for kff in (1.0, 0.0):
                    S, X = setup(P, G, "gfl", scr, Pop=pc, vdc=vdc, dc=True, pcpl=pc, kff_dc=kff)
                    X, res = equil(P, G, S, X)
                    if step(P, G, S, X, 0, S["inp"])[1]["usat"]:      # operating point outside the modulation range
                        lin.append(dict(scr=scr, vdc=vdc, pcpl=pc, kff=kff, pm=None, gm=None, fc=None, rho=None,
                                        eq_res=res, ok=None))
                        continue
                    m = margins(f, lin_break(P, G, S, X, "dc", f))
                    lm = linmodes(P, G, S, X)
                    lin.append(dict(scr=scr, vdc=vdc, pcpl=pc, kff=kff, pm=m["pm"], gm=min(m["gm"], m["gm_lo"]),
                                    fc=m["f_pm"], rho=lm["rho"], eq_res=res,
                                    ok=bool(m["pm"] >= RULE_PM and min(m["gm"], m["gm_lo"]) >= RULE_GM and lm["rho"] < 1)))
    tsim, pc = [], A("p_cpl_W")
    for scr in (None, 5.0):                                  # DC/DC load applied as a ramp (0 = step) and tripped
        for t_ramp in (0.0, 0.005, 0.02):
            for mult in ((1.0, 4.0) if t_ramp == 0.0 else (1.0,)):
                for name, p0, p1 in (("DC/DC load 0 -> 125 kW", 0.0, -pc), ("DC/DC load 125 kW trips", -pc, 0.0)):
                    if t_ramp and name.endswith("trips"):
                        continue
                    Px = dict(P, Cdc=P["Cdc"] * mult)
                    Gx = gains(Px, G["K"], G["Kv"])
                    S, X = setup(Px, Gx, "gfl", scr, Pop=p0, vdc=750.0, dc=True, pcpl=p0, kff_dc=1.0)
                    X, _ = equil(Px, Gx, S, X)

                    def ev(t, S_, X_, p0=p0, p1=p1, t_ramp=t_ramp):
                        x = 0.0 if t < 0.01 else (1.0 if not t_ramp else min(1.0, (t - 0.01) / t_ramp))
                        S_["pcpl"] = p0 + (p1 - p0) * x
                    r, _ = run(Px, Gx, S, X, 0.1, ev)
                    ok = bool(np.all(np.isfinite(r["vdc"])) and abs(r["vdc"][-1] - 750.0) < 5.0 and r["vdc"].min() > 0)
                    tsim.append(dict(case=name, scr=scr, t_ramp_ms=1e3 * t_ramp, Cdc_mF=1e3 * Px["Cdc"], recovered=ok,
                                     vmin=float(r["vdc"].min()), vmax=float(r["vdc"].max()), pk=ripple_peak(P, r)))
    rhp = pc / (600.0 ** 2 * P["Cdc"]) / (2 * math.pi)
    return dict(lin=lin, tsim=tsim, f_rhp_600=rhp, e_link_J=0.5 * P["Cdc"] * 750.0 ** 2)


# ---------------------------------------------------------------------------------------------------------- 6. harmonics
HARM_ALL = (5, 7, 11, 13, 17, 19, 23, 25, 29, 31, 35, 37)


def harmonic_response(P, K, scr, cn="nom"):
    """closed-loop grid-current amplitude per volt at each odd non-triplen harmonic: from a background grid voltage (exact
    rotating input) and from a converter-voltage disturbance (dead time), alpha-beta, sequence-independent"""
    pl = plant(P, cn, "grid", scr)
    Ac, _, Bw = closed(pl, K)
    n, out = len(pl["Gu"]), {}
    Cg = np.eye(len(Ac))[pl["ig"]]
    for h in HARM_ALL:
        z = np.array([cx(h * P["w0"] * P["T"])])
        Bg = np.zeros(len(Ac), complex)
        Bg[:n] = pl["rot"](1j * h * P["w0"])
        out[h] = (abs(ss_fr(Ac, Bg, Cg, z)[0]), abs(ss_fr(Ac, Bw, Cg, z)[0]))
    return out


def study_thd(P, alt):
    """THDi estimate at rated current (CALCULATED from the linear closed loop, not a switched simulation): background
    distortion (assumed levels) + dead-time square-wave error (4 dV / (pi h), dV = V_dc f_sw t_d) at 900 V, worst SCR, plus
    the PWM figure of pcs_spec; harmonics added in phase per order (worst case), rms over the orders"""
    bg = A("bg_harm_pct")
    dmin, dmax = P["dt_gate_ns"]
    cases = {"none (0 ns: background only)": 0.0, f"no compensation ({dmax:.0f} ns)": dmax,
             "compensated at the band midpoint": (dmax - dmin) / 2, "compensated, identified per leg": A("dt_resid_ns")}
    rows = []
    for label, Kx in alt.items():
        for scr in A("scr"):
            hr = harmonic_response(P, Kx, scr)
            for dlab, td in cases.items():
                dv = 900.0 * P["fsw"] * td * 1e-9
                ih = {h: hr[h][0] * bg.get(h, 0.0) / 100 * P["V0"] + hr[h][1] * 4 * dv / (math.pi * h) for h in HARM_ALL}
                thd = math.sqrt(sum(v * v for v in ih.values()) + (P["pwm_thd"] * P["I_rated"]) ** 2) / P["I_rated"]
                rows.append(dict(ctrl=label, scr=scr, dt=dlab, td_ns=td, thd_pct=100 * thd,
                                 i5=ih[5], i7=ih[7], i11=ih[11], i13=ih[13],
                                 y_bg={h: hr[h][0] for h in (5, 7, 11, 13)}, y_dt={h: hr[h][1] for h in (5, 7, 11, 13)}))
    return rows


# ---------------------------------------------------------------------------------------------------------- 5. grid forming
def to_gfm(P, G, S, X):
    """bumpless GFL -> GFM: droop angle and voltage set so the reference equals the present C_f voltage behind the
    virtual impedance, droop set points = measured P/Q, voltage PR on its integrator mode giving the present reference"""
    yv, Ceq = X["xp"][6], P["Ceq"]
    vd = max(X["vd"], 0.1 * P["V0"])
    i1r = (S["inp"]["P"] - 1j * S["inp"]["Q"]) / (1.5 * vd) + 1j * P["w0"] * Ceq * vd
    iref = i1r * min(1.0, S["ilim"] / abs(i1r)) * cx(X["th"])
    io = X["xp"][5] - 1j * P["w0"] * P["Ceq"] * yv
    s, e = 1.5 * yv * io.conjugate(), yv + G["Zv"] * io
    X["thv"], X["pf"], X["qf"] = cmath.phase(e), s.real, s.imag
    S.update(mode="gfm", Pset=s.real, Qset=s.imag, Vset=abs(e))
    X["xv"] = hold_state(P, G["Kv"]["res"], iref - 1j * P["w0"] * P["Ceq"] * yv)


def to_gfl(P, G, S, X):
    """bumpless GFM -> GFL: P/Q references = measured, PR on its integrator mode giving the present converter voltage"""
    S.update(mode="gfl")
    S["inp"].update(P=X["pf"], Q=X["qf"])
    yv, Af, Bf, Cf_, Df = X["xp"][6], *G["K"]["yv"]
    i1r = (X["pf"] - 1j * X["qf"]) / (1.5 * X["vd"]) + 1j * P["w0"] * P["Ceq"] * X["vd"]
    e = i1r * cx(X["th"]) - X["xp"][5]
    X["xr"] = hold_state(P, G["K"]["res"], X["d"] / P["R"] - G["K"]["Kp"] * e - (Cf_ @ X["xf"] + Df * yv))


def study_gfm(P, G):
    """grid forming: (a) grid-connected poles at each SCR, (b) off-grid load step, (c) off-grid terminal short with current
    limiting and recovery, (d) synchronised closing at the permissive edge then GFM -> GFL, (e) planned islanding with a
    local load (GFL -> GFM, then the upstream breaker opens)"""
    out = {"lin": []}
    for scr in A("scr"):
        S, X = setup(P, G, "gfm", scr, Pop=0.5 * P_N, th_v=0.0)
        X, res = equil(P, G, S, X)
        lm = linmodes(P, G, S, X)
        out["lin"].append(dict(scr=scr, rho=lm["rho"], zeta=lm["zeta"], f=lm["f"], eq_res=res, ok=lm["rho"] < 1))
    t0 = 0.01
    S, X = setup(P, G, "gfm", kind="nl")                    # (b) off-grid 0 -> rated resistive load
    X, _ = equil(P, G, S, X)
    r, _ = run(P, G, S, X, 0.25, lambda t, S_, X_: S_.update(pl=plant(P, "nom", "rl")) if t >= t0 else None)
    vm, k0 = np.abs(r["vc"]) / P["V0"], int(t0 / P["T"])
    out["load_step"] = dict(vmin=float(vm[k0:].min()), t10_ms=1e3 * settle(r["t"][k0:], vm[k0:], vm[-1], 0.10, t0),
                            t5_ms=1e3 * settle(r["t"][k0:], vm[k0:], vm[-1], 0.05, t0), v_end=float(vm[-1]),
                            df_end=float(G["mp"] * (0 - 1.5 * P["V0"] ** 2 / P["zb"]) / (2 * math.pi)), pk=ripple_peak(P, r), rec=r)
    S, X = setup(P, G, "gfm", kind="rl", Pop=P_N)           # (c) terminal short 150 ms from rated load, then cleared
    X, _ = equil(P, G, S, X)
    t1 = t0 + A("codes")["dip_s"]

    def ev(t, S_, X_):
        S_["pl"] = plant(P, "nom", "short" if t0 <= t < t1 else "rl")
    r, _ = run(P, G, S, X, t1 + 0.1, ev)
    k1 = int(t1 / P["T"])
    lim_on = np.nonzero(r["lim"])[0]
    out["short"] = dict(pk=ripple_peak(P, r), pk_avg=pk_phase(r), i_hold=float(np.mean(np.abs(r["i1"][k1 - 640:k1]))),
                        i2_pk=float(np.max(np.abs(r["i2"]))), lim_ms=1e3 * P["T"] * len(lim_on),
                        t_fw_trip_ms=1e3 * A("t_lim_s"), vmax_rec=float(np.max(np.abs(r["vc"][k1:])) / P["V0"]),
                        t_rec5_ms=1e3 * settle(r["t"][k1:], np.abs(r["vc"][k1:]) / P["V0"], 1.0, 0.05, t1), rec=r)
    out["sync"] = []                                          # (d) close at the permissive edge, then GFM -> GFL
    dV, dth = P["sync"][0] / 100, math.radians(P["sync"][1])
    for scr in (None, 5.0):
        S, X = setup(P, G, "gfm", scr, kind="open", Vset=P["V0"] * (1 + dV), th_v=dth)
        X, _ = equil(P, G, S, X)
        t2 = t0 + 0.1
        st = {}

        def ev(t, S_, X_, scr=scr, st=st):
            if t >= t0 and S_["pl"]["kind"] == "open":
                S_["pl"] = plant(P, "nom", "grid", scr)
            if t >= t2 and S_["mode"] == "gfm":
                to_gfl(P, G, S_, X_)
                st["k"] = True
        r, _ = run(P, G, S, X, t2 + 0.08, ev)
        ka, kb = int(t0 / P["T"]), int(t2 / P["T"])
        out["sync"].append(dict(scr=scr, pk_close=ripple_peak(P, r, slice(ka, kb)), pk_close_avg=pk_phase(r, slice(ka, kb)),
                                ig_close=float(np.max(np.abs(r["ig"][ka:kb]))), pk_switch=ripple_peak(P, r, slice(kb, None)),
                                dev_switch=float(np.max(np.abs(r["i1"][kb:] - r["i1"][kb]))), rec=r))
    RL, out["island"] = 2 * P["zb"], []                      # (e) local load 62.5 kW, SCR 10; inverter at 125 kW (0.5 pu
    for Pop in (P_N, P_N / 2):                                #     export: a load rejection) or at the load (planned)
        S, X = setup(P, G, "gfl", 10.0, kind="load", RL=RL, Pop=Pop)
        X, _ = equil(P, G, S, X)
        ta, tb = t0, t0 + 0.05

        def ev(t, S_, X_):
            if t >= ta and S_["mode"] == "gfl":
                to_gfm(P, G, S_, X_)
            if t >= tb and S_["pl"]["kind"] == "load":
                S_["pl"] = plant(P, "nom", "rl", RL=RL)
                S_["inp"]["vgp"] = 0j
        r, _ = run(P, G, S, X, 0.4, ev)
        vm = np.abs(r["vc"]) / P["V0"]
        ka, kb = int(ta / P["T"]), int(tb / P["T"])
        k10, k20 = (int((tb + x / 1e3) / P["T"]) for x in (10.0, A("codes")["transfer_ms"]))
        fv = np.gradient(np.unwrap(r["thv"]), P["T"]) / (2 * math.pi)
        out["island"].append(dict(P_kW=Pop / 1e3, export_kW=(Pop - 1.5 * P["V0"] ** 2 / RL) / 1e3,
                                  dv_switch=float(np.max(np.abs(vm[ka:kb] - vm[ka - 1]))), v_pk=float(vm[kb:].max()),
                                  v_10ms=float(vm[k10]), v_end=float(vm[-1]), dv20=float(np.max(np.abs(vm[kb:k20] - vm[kb - 1]))),
                                  df_end=float(fv[-1]), pk=pk_phase(r) + P["ripple_pp"] / 2, rec=r))
    return out


# ---------------------------------------------------------------------------------------------------------- 4b. ride-through
def rt_dip(P, G, depth, scr, Pop=P_N, layers="all", ts=0.01, dur=None, tail=0.1, lvrt_ilim=None, iclamp=None, lvrt_pu=None,
           iclamp_rt=None, vc_comp=False, cn="nom"):
    """one ride-through case: a symmetric dip to `depth` pu at ts for dur (default the code's 150 ms) at Pop export, power reference
    held; layers 'all' = ride-through state + per-sample clamp, 'clamp' = the clamp alone; overrides (dip-time limit, clamp level,
    ride-through entry / hold levels) for the control-measure variants of section 4c.  Onset = the first ms; peaks = averaged +
    ripple/2"""
    c, T = A("codes"), P["T"]
    n1, dur = int(round(1e-3 / T)), (c["dip_s"] if dur is None else dur)
    S, X = setup(P, G, "gfl", scr, cn=cn, Pop=Pop, iclamp=iclamp or P["iclamp"], lvrt=(lvrt_ilim or P["lvrt_ilim"]) if layers == "all" else None)
    S.update(lvrt_pu=lvrt_pu, iclamp_rt=iclamp_rt, vc_comp=vc_comp)
    X, _ = equil(P, G, S, X)
    r, _ = run(P, G, S, X, ts + dur + tail,
               lambda t, S_, X_: S_["inp"].update(vgp=complex(P["V0"] * (depth if ts <= t < ts + dur else 1.0))))
    k0, k1 = int(round(ts / T)), int(round((ts + dur) / T))
    d = dict(scr=scr, depth=depth, P_kW=Pop / 1e3, layers=layers, pk_on=ripple_peak(P, r, slice(k0, k0 + n1)))
    if tail:
        ia = np.abs(pll_frame(r))[k0:k1]
        fin = float(np.mean(ia[-int(0.02 / T):]))
        d.update(pk_dip=ripple_peak(P, r, slice(k0 + n1, k1)), pk_rec=ripple_peak(P, r, slice(k1, None)), i_dip=fin,
                 settle_ms=1e3 * settle(r["t"][k0:k1], ia, fin, 0.05 * fin, ts), clamp_ms=1e3 * T * float(np.sum(r["clamp"])),
                 usat_ms=1e3 * T * float(np.sum(r["usat"])))
        d["pk"] = max(d["pk_on"], d["pk_dip"], d["pk_rec"])
        d["trip"] = d["pk"] > P["win"]["lo"]
    return d


def rt_derate(P, G, depth, scr, thr, on=None):
    """largest pre-dip export whose onset peak stays at or below thr (the onset is nearly affine in the pre-dip current: solve
    from two points, round down to 0.5 kW, step down until the simulated onset is inside)"""
    on = rt_dip(P, G, depth, scr, dur=0.003, tail=0.0)["pk_on"] if on is None else on
    if on <= thr:
        return dict(depth=depth, scr=scr, P_max_kW=P_N / 1e3, frac=1.0, onset=on)
    p2 = 0.6 * P_N
    on2 = rt_dip(P, G, depth, scr, Pop=p2, dur=0.003, tail=0.0)["pk_on"]
    pm = math.floor((p2 + (P_N - p2) * (thr - on2) / (on - on2)) / 500.0) * 500.0
    for _ in range(10):
        chk = rt_dip(P, G, depth, scr, Pop=pm, dur=0.003, tail=0.0)["pk_on"]
        if chk <= thr:
            break
        pm -= 500.0
    return dict(depth=depth, scr=scr, P_max_kW=pm / 1e3, frac=pm / P_N, onset=chk)


def study_ride_through(P, G):
    """the firmware current limiter in ride-through (D-074 open item: a 0.1 pu dip at 125 kW reached 465 A against the 426 A low
    edge of the hardware window).  Layers: the circular reference limiter of section 4; the ride-through state (sensed |v_C|
    below lvrt_pu arms a hold timer: reference limit = the hand-over profile's dip-time cap, PLL free-running below
    pll_freeze_pu); the per-sample predictive clamp in the PWM update at the window low edge - ripple/2 - margin.  Dips at
    125 kW export, power reference held, 150 ms: five depths x five grids with every layer; the clamp alone at stiff and SCR 5;
    the stiff-grid onset against the pre-dip power (derating); the study's dip instant against a 60 deg scan"""
    T, lo, t0 = P["T"], P["win"]["lo"], 0.01
    dip = lambda *a, **k: rt_dip(P, G, *a, **k)
    grids, depths = (None, 50.0) + tuple(s for s in A("scr") if s), (0.0, 0.1, 0.2, 0.3, 0.5)
    rows = [dip(d, s) for s in grids for d in depths]
    clamp_only = [dip(d, s, layers="clamp") for s in (None, 5.0) for d in (0.1, 0.3, 0.5)]
    der = [rt_derate(P, G, d, None, lo, next(r["pk_on"] for r in rows if r["scr"] is None and r["depth"] == d)) for d in depths]
    scan = [dip(depths[0], None, ts=t0 + j / F0 / 36, dur=0.003, tail=0.0)["pk_on"] for j in range(6)]
    m50 = [evaluate(P, G["K"], cn, "grid", 50.0, full=False) for cn in CORNERS]
    return dict(rows=rows, clamp_only=clamp_only, derating=der, instant_scan=scan, grids=grids, depths=depths,
                scr50=dict(pm=min(e["pm"] for e in m50), gm=min(min(e["gm"], e["gm_lo"]) for e in m50), ok=all(e["ok"] for e in m50)))


def study_rt_rule(P, G, R):
    """review R2-05: the stiff-grid onset.  (1) Firmware measures at the stiff 0 pu dip (the dip-time limit, the clamp level, the
    ride-through entry level, the clamp's own C_f-voltage estimate), with two hardware latency what-ifs for the explanation; the
    measure adopted - the per-sample clamp at the dip-time limit + iclamp_margin_A while the ride-through state runs, its prediction
    on the sensed C_f voltage with the divider's known pole inverted - checked at every corner, both divider-pole tolerance ends,
    six dip instants and the full ride-through table.  (2) The enforceable fallback rule for the D-077 limiter: the exact SCR
    boundary per residual voltage (bisection on 1/SCR between stiff and SCR 50: the onset falls monotonically with the grid
    impedance), the derating table above it, the firmware's grid-stiffness estimate (a reactive-current step at connection: the
    terminal-voltage phasor change against the estimated grid-current change i_1 - j w C_eq v_C, on the Newton fixed points = its
    deterministic error; the background-voltage error ASSUMED) with the engage threshold, and the installation declaration"""
    pc, rt = P["pc"], R["rt"]
    thr, crt = pc["I_trip_lo"], P["lvrt_ilim"] + A("iclamp_margin_A")
    M = dict(iclamp_rt=crt, vc_comp=True)                         # the adopted measure
    on = lambda depth, scr, Pq=None, **kw: rt_dip(Pq or P, G, depth, scr, dur=0.003, tail=0.0, **kw)["pk_on"]
    meas = [("D-077 limiter (section 4b)", on(0.0, None)),
            ("dip-time limit 0.5 I_r instead of 1.0 I_r", on(0.0, None, lvrt_ilim=0.5 * P["I_rated"])),
            ("ride-through state entered below 0.95 pu instead of %g pu" % A("lvrt_pu")[0], on(0.0, None, lvrt_pu=(0.95, 0.97))),
            ("clamp at %.0f A (the dip-time limit + %.0f A) while the ride-through state runs" % (crt, A("iclamp_margin_A")),
             on(0.0, None, iclamp_rt=crt)),
            ("clamp prediction on the sensed C_f voltage with the divider pole inverted", on(0.0, None, vc_comp=True)),
            ("ADOPTED: both (ride-through clamp level + divider inversion)", on(0.0, None, **M)),
            ("HARDWARE what-if: C_f divider pole 30 kHz (D-077 limiter)", on(0.0, None, Pq=dict(P, tv=1 / (2 * math.pi * 30e3)))),
            ("HARDWARE limit: lag-free C_f-voltage measurement (D-077 limiter, 1.5 T_s kept)",
             on(0.0, None, Pq=dict(P, tv=1 / (2 * math.pi * 1e6))))]
    rob = {cn: [on(d, None, cn=cn, **M) for d in (0.0, 0.1, 0.2, 0.3)] for cn in CORNERS}
    dvt = P["vdiv"]["tol_c"] + max(P["vdiv"]["tol_top"], P["vdiv"]["tol_bot"])      # the divider pole's tolerance (drawn BOM values)
    rob_div = {k: [on(d, None, Pq=dict(P, tv=P["tv"] / k), **M) for d in (0.0, 0.1, 0.2)] for k in (1 - dvt, 1 + dvt)}
    scan = {cn: [on(0.0, None, ts=0.01 + j / F0 / 36, cn=cn, **M) for j in range(6)] for cn in ("nom", "low")}
    rows = [rt_dip(P, G, d, s, **M) for s in rt["grids"] for d in rt["depths"]]
    worst = max([v for x in rob.values() for v in x] + [v for x in rob_div.values() for v in x] + [v for x in scan.values() for v in x])
    # (2) the fallback rule for the D-077 limiter
    stiff = {d["depth"]: d["pk_on"] for d in rt["rows"] if d["scr"] is None}
    bnd = []
    for depth in (d for d in rt["depths"] if stiff[d] > thr):
        a, b, ob = 0.0, 1 / 50.0, on(depth, 50.0)                # x = 1/SCR: a fails (onset > thr), b passes
        assert ob <= thr, ("ride-through: SCR 50 no longer passes at rated current", depth, ob)
        m50 = thr - ob
        for _ in range(12):
            m = (a + b) / 2
            om = on(depth, 1 / m)
            a, b, ob = (m, b, ob) if om > thr else (a, m, om)
        bnd.append(dict(depth=depth, scr_b=1 / b, onset_b=ob, onset_stiff=stiff[depth], margin_scr50_A=m50))
    scr_b = min(d["scr_b"] for d in bnd)
    rows_scr = [s for s in A("rt_rule")["table_scr"] if s > scr_b] + [None]
    table = [rt_derate(P, G, d, s, thr) for s in rows_scr for d in (0.0, 0.1, 0.2, 0.3)]
    dq, acc = A("rt_rule")["dq_pu"], A("rt_rule")["acc"]
    est = []
    for scr in (5.0, 20.0, 50.0, round(scr_b, 1), 200.0):
        pts = []
        for q in (0.0, dq * P_N):
            S, X = setup(P, G, "gfl", scr, Pop=0.0, Qop=q)
            X, _ = equil(P, G, S, X)
            xp = X["xp"]
            pts.append((xp[7], xp[5] - 1j * P["w0"] * P["Ceq"] * xp[6]))
        Z = (pts[1][0] - pts[0][0]) / (pts[1][1] - pts[0][1])
        est.append(dict(scr=scr, scr_est=P["zb"] / abs(Z), err_pct=100 * (P["zb"] / abs(Z) / scr - 1), dV_V=abs(pts[1][0] - pts[0][0]),
                        dI_A=abs(pts[1][1] - pts[0][1])))
    lsb = P["v_lsb"]
    eng = scr_b / (1 + acc)
    return dict(thr=thr, thr_src=pc["src"], clamp_rt_A=crt, measures=meas, robust=rob, robust_div=rob_div, div_tol=dvt, instant_scan=scan, rows=rows,
                worst_onset=worst, margin_A=thr - worst, adopted=worst <= thr - A("rt_rule")["margin_A"],
                boundary=bnd, scr_b=scr_b, table=table, table_scr=rows_scr, estimator=est, dq_pu=dq, acc=acc, engage_scr=eng,
                lsb_V=lsb[1], div=lsb[0], dV_engage_V=P["zb"] / eng * est[0]["dI_A"], S_sc_engage_MVA=eng * P_N / 1e6,
                S_sc_b_MVA=scr_b * P_N / 1e6, I_off_max=max(d["onset_stiff"] for d in bnd), I_screen=pc["I_screen"])


# ---------------------------------------------------------------------------------------------------------- 6b / 7c. bounded
def gates_off_tail(P, X, k, pl, vgp=0j, isrc=lambda tau: 0.0, t_max=0.03, dt=1e-6):
    """review R2-04: the bridge after a trip, every gate off.  Per leg L1 (R_dc) into its C_f node (star on M) with the R_d-C_d
    branch; the switch node sits on the rail the leg current flows to - bottom diode (-v_bot) for i_1 > 0, top diode (+v_top) for
    i_1 < 0 - or the leg blocks at zero current while -v_bot < v_C < v_top; the grid side in alpha-beta (three-wire: no zero
    sequence) through L2 + DM leakage + the plant's grid / island impedance to the grid source (kind grid), a resistive island load
    (rl) or open (nl); the DC link as its two halves (C_half each) with the leg films C_loc across the pair and a DC-side current
    isrc(tau) into DC+.  Initial state: the synchronous-frame state X at sample k, halves at V_dc/2.  RK4 at dt; a leg current that
    crosses zero is set to zero (its diode blocks).  Runs until every leg current has been zero for 1 ms, or t_max"""
    T, w0, a3 = P["T"], P["w0"], cx(2 * math.pi / 3)
    xab = X["xp"] * cx(w0 * k * T)
    rot = [cx(-2 * math.pi * p / 3) for p in range(3)]
    y0 = [(xab[j] * rot[p]).real for j in (0, 1, 2) for p in range(3)]
    i20 = xab[3] if pl["kind"] in ("grid", "rl") else 0j
    y = y0 + [i20.real, i20.imag, X["vdc"] / 2, X["vdc"] / 2]
    L1, R1, Cf, Cd, Rd = P["L1"], P["R1"], P["Cf"], P["Cd"], P["Rd"]
    Lt, Rt = P["L2"] + P["Ldm"] + pl["Lg"], P["R2"] + pl["Rg"]
    open_ = pl["kind"] not in ("grid", "rl")
    ca, cc = P["C_half"] + P["C_loc"], P["C_loc"]
    det = ca * ca - cc * cc
    t0 = k * T

    def f(tau, y):
        i1, vC, vD = y[0:3], y[3:6], y[6:9]
        i2, vt, vb = complex(y[9], y[10]), y[11], y[12]
        d1, dC, dD, top, bot = [0.0] * 3, [0.0] * 3, [0.0] * 3, 0.0, 0.0
        for p in range(3):
            if i1[p] > 0:
                u, bot = -vb, bot + i1[p]
            elif i1[p] < 0:
                u, top = vt, top + i1[p]
            else:
                u = vt if vC[p] > vt else (-vb if vC[p] < -vb else vC[p])
            d1[p] = (u - vC[p] - R1 * i1[p]) / L1
            idp = (vC[p] - vD[p]) / Rd
            dC[p] = (i1[p] - (i2 * rot[p]).real - idp) / Cf
            dD[p] = idp / Cd
        if open_:
            di2 = 0j
        else:
            vab = 2 / 3 * (vC[0] + vC[1] * a3 + vC[2] / a3)
            di2 = (vab - Rt * i2 - (vgp * cx(w0 * (t0 + tau)) if pl["kind"] == "grid" else 0.0)) / Lt
        it, ib = isrc(tau) - top, isrc(tau) + bot              # into the top half / the bottom half
        return d1 + dC + dD + [di2.real, di2.imag, (ca * it - cc * ib) / det, (ca * ib - cc * it) / det]
    ts, vdc, vcm, im = [], [], [], []
    tau, quiet, hmax = 0.0, 0.0, 0.0
    while tau < t_max and quiet < 1e-3:
        k1 = f(tau, y)
        k2 = f(tau + dt / 2, [a + dt / 2 * b for a, b in zip(y, k1)])
        k3 = f(tau + dt / 2, [a + dt / 2 * b for a, b in zip(y, k2)])
        k4 = f(tau + dt, [a + dt * b for a, b in zip(y, k3)])
        yn = [a + dt / 6 * (b + 2 * c + 2 * d + e) for a, b, c, d, e in zip(y, k1, k2, k3, k4)]
        for p in range(3):
            if y[p] * yn[p] < 0:
                yn[p] = 0.0
        y, tau, hmax = yn, tau + dt, max(hmax, yn[11], yn[12])
        quiet = quiet + dt if all(v == 0.0 for v in y[0:3]) else 0.0
        if int(round(tau / dt)) % 10 == 0:
            ts.append(tau)
            vdc.append(y[11] + y[12])
            vcm.append(abs(2 / 3 * (y[3] + y[4] * a3 + y[5] / a3)) / P["V0"])
            im.append(max(abs(v) for v in y[0:3]))
    vdc, vcm = np.array(vdc), np.array(vcm)
    e = lambda vt, vb: 0.5 * P["C_half"] * (vt * vt + vb * vb) + 0.5 * P["C_loc"] * (vt + vb) ** 2
    return dict(i_off=max(abs(v) for v in y0[0:3]), v_off=X["vdc"], vdc_pk=float(vdc.max()), vdc_end=float(vdc[-1]), vt_end=y[11],
                vb_end=y[12], half_pk=hmax, t_zero_ms=1e3 * (tau - quiet), vc_end_pu=float(vcm[-1]), t=np.array(ts), vdc=vdc,
                vcm=vcm, im=np.array(im), e_in_J=e(y[11], y[12]) - e(X["vdc"] / 2, X["vdc"] / 2))


def prot_run(P, G, S, X, t_end, ev0, hw, soft=None):
    """sample-by-sample run with the DC-link over-voltage protection in the loop: the firmware soft limit (P['ov_soft']: level,
    latency; soft 'stop' = the drawn controlled stop, current reference ramped to zero over one grid period; 'hold' = the loop keeps
    running; None = not modelled), the hardware comparator at hw with its response, the ADC PPB backup (paired: band low edge with
    the PPB's low edge, top with top); gates off at the first.  Returns (record, X at gates-off or at the end, trip dict or None)"""
    st = {}
    ppb = float("inf") if math.isinf(hw) else P["ov_ppb"][0] if hw <= P["ov_dc"][1] + 1e-9 else P["ov_ppb"][1]

    def ev(t, S_, X_):
        ev0(t, S_, X_)
        v = X_["vdc"]
        if soft and "soft" not in st and v >= P["ov_soft"][0]:
            st["soft"] = t + P["ov_soft"][1] * 1e-6
        if soft == "stop" and "soft" in st and t >= st["soft"]:
            S_["iscale"] = max(0.0, 1.0 - (t - st["soft"]) * F0)
        for key, th, rs in (("hw", hw, P["ov_resp_us"]), ("ppb", ppb, P["ov_ppb"][2])):
            if key not in st and v >= th:
                st[key] = t + rs * 1e-6
        offs = [st[x] for x in ("hw", "ppb") if x in st]
        if offs and t >= min(offs):
            st.update(t_off=t, v_off=v, by="comparator" if st.get("hw", 1e9) <= st.get("ppb", 1e9) else "ADC PPB backup")
            return "stop"
    r, Xe = run(P, G, S, X, t_end, ev)
    return r, Xe, (st if "t_off" in st else None)


def trip_check(P, tail):
    """device and DC-link checks after a gates-off event: the bus plus the commutation overshoot at the gates-off current
    (averaged + ripple/2) against the device limit, that current against the commutation screen, each half against the film's
    allowance (1.5 U_N for 100 ms, D-067 / pcs_spec dc_link) and its rating, the bus left behind against the aux's input lock-out"""
    pc, I = P["pc"], tail["i_off"] + P["ripple_pp"] / 2
    return dict(I_off=I, V_dev=v_dev(P, I, tail["v_off"]), dev_ok=v_dev(P, I, tail["v_off"]) <= pc["V_limit"], screen_ok=I <= pc["I_screen"],
                half_ok=tail["half_pk"] <= 1.5 * P["U_N_half"], half_rated=max(tail["vt_end"], tail["vb_end"]) <= P["U_N_half"],
                aux_lockout=tail["vdc_end"] >= P["aux"]["ov"])


def study_dc_trip(P, G):
    """review R2-04: the weak-grid DC-load rejection of section 6 (a 125 kW DC/DC load on the link trips; SCR 5, 750 V, the measured
    DC-current feed-forward) with the protection in the loop - the per-sample clamp, the firmware soft limit as drawn (controlled
    stop) or holding the DC-link loop, the hardware comparator at either end of its band with the ADC PPB backup - and after gates
    off the diode-bridge tail (the DC/DC has tripped: no DC-side current) with the device / film / aux checks; then the largest
    DC/DC power per grid whose trip stays below the soft limit"""
    t0, inf = 0.01, float("inf")

    def case(scr, p0, soft, hw, t_end=0.1):
        S, X = setup(P, G, "gfl", scr, Pop=-p0, vdc=750.0, dc=True, pcpl=-p0, kff_dc=1.0, iclamp=P["iclamp"])
        X, _ = equil(P, G, S, X)
        r, Xe, tr = prot_run(P, G, S, X, t_end, lambda t, S_, X_: S_.update(pcpl=0.0) if t >= t0 else None, hw, soft)
        d = dict(scr=scr, P_kW=p0 / 1e3, soft=soft, hw=hw, vmax_loop=float(r["vdc"].max()), trip=tr is not None, rec=r)
        if tr:
            tail = gates_off_tail(P, Xe, len(r["t"]), S["pl"], S["inp"]["vgp"])
            d.update(t_off_ms=1e3 * (tr["t_off"] - t0), by=tr["by"], v_off=tr["v_off"], vdc_pk=tail["vdc_pk"], vdc_end=tail["vdc_end"],
                     half_pk=tail["half_pk"], t_zero_ms=tail["t_zero_ms"], e_in_J=tail["e_in_J"], tail=tail, **trip_check(P, tail))
        else:
            d.update(vdc_end=float(r["vdc"][-1]), recovered=bool(abs(r["vdc"][-1] - 750.0) < 5.0))
        return d
    lo, hi = P["ov_dc"][1], P["ov_dc"][2]
    pn = A("p_cpl_W")
    cases = [("section 6: the loop alone, no protection", None, inf), ("as drawn: soft limit = controlled stop, comparator at the band's "
             "low edge", "stop", lo), ("as drawn, comparator at the band's top edge", "stop", hi), ("soft limit holds the DC-link loop, "
             "comparator at the low edge", "hold", lo), ("soft limit holds the loop, comparator at the top edge", "hold", hi)]
    rows = [dict(case(5.0, pn, s, h), case_name=n) for n, s, h in cases]
    der = []
    for scr in (5.0, 10.0, 20.0, None):                       # largest DC/DC power whose trip stays below the soft limit
        if case(scr, pn, "hold", inf)["vmax_loop"] <= P["ov_soft"][0]:
            der.append(dict(scr=scr, P_max_kW=pn / 1e3, frac=1.0))
            continue
        a, b = 0.0, pn
        for _ in range(8):
            m = (a + b) / 2
            a, b = (m, b) if case(scr, m, "hold", inf)["vmax_loop"] <= P["ov_soft"][0] else (a, m)
        der.append(dict(scr=scr, P_max_kW=math.floor(a / 500.0) * 0.5, frac=math.floor(a / 500.0) * 500.0 / pn))
    return dict(rows=rows, derating=der, t0=t0, soft=P["ov_soft"], band=(lo, hi), resp_us=P["ov_resp_us"], ppb=P["ov_ppb"])


VFD = lambda: dict(ioff=1.0, vclamp=A("vf_vclamp_pu"))      # the VF firmware measures of section 7c (the clamp goes in setup)


def env_check(t_ms, vm, env):
    """voltage-versus-time envelope check: env = {'over': [(t_end_ms, limit_pu), ...], 'under': [...]} (piecewise constant, each limit
    valid until its t_end); returns the smallest margin to each side (pu, negative = outside)"""
    def lim(rows, t):
        return next(v for te, v in rows if t <= te)
    over = min(lim(env["over"], t) - v for t, v in zip(t_ms, vm))
    under = min(v - lim(env["under"], t) for t, v in zip(t_ms, vm))
    return dict(over=float(over), under=float(under), ok=bool(over >= 0 and under >= 0))


def study_vf_bounded(P, G, R):
    """review R2-04: the off-grid (VF) full-load steps with the plant bounded and the firmware measures in the loop.  Bounds: the
    modulation limit (the firmware's circular limiter at u_frac x V_dc,meas, inside the bridge's hexagon), the per-sample clamp,
    the DC link as a stiff battery or as its finite capacitance (pcs_spec) with a DC-side source model, the hardware DC over-voltage
    comparator / ADC PPB and, once the gates are off, the diode-bridge tail (the legs' diodes return the L1 and C_f energy into the
    two halves).  Firmware measures (section 7c): the load-current feed-forward, the over-voltage deadbeat, the single-module
    virtual impedance 0 (paralleled modules keep the droop's Z_v: section 7b).  (a) battery: 0 -> rated and rated -> 0 for the
    D-077 configuration (no clamp, no measures: the unbounded averaged figures, superseded), the design at every corner (single
    module) and paralleled; (b) capacitance only with an ideally coordinated, unidirectional source (it follows the bridge's draw
    and absorbs nothing): rated -> 0, the V_dc rise from the returned LCL energy; (c) capacitance only, the source holding its
    pre-step current for t_c: the largest t_c that stays below the soft limit, and the uncoordinated case (held until the PCS trips,
    the DC/DC stops dcdc_stop_us later) with its gates-off trajectory; (d) the declared envelope and the ITIC-style class"""
    Ts, t0, T = R["sec"]["T_s"], 0.01, P["T"]
    k0s = int(round(t0 / T))

    def stp(ka, kb, design=True, zv=0j, cn="nom", tend=0.3, src=None, hw=None, i_hold=0.0):
        Gx = dict(G, Zv=zv)
        S, X = setup(P, Gx, "gfm", kind=ka, cn=cn, sec="reseed", sec_T=Ts, iclamp=P["iclamp"] if design else None)
        if design:
            S.update(VFD())
        if src:
            S.update(cdc=True, src=src)
        X, _ = equil(P, Gx, S, X)
        ev0 = lambda t, S_, X_: S_.update(pl=plant(P, cn, kb)) if t >= t0 and S_["pl"]["kind"] != kb else None
        if hw:
            r, Xe, tr = prot_run(P, Gx, S, X, tend, ev0, hw)
        else:
            (r, Xe), tr = run(P, Gx, S, X, tend, ev0), None
        vm, tt = np.abs(r["vc"][k0s:]) / P["V0"], r["t"][k0s:]
        fv = np.gradient(np.unwrap(r["thv"]), T)[k0s:] / (2 * math.pi)
        d = dict(v_min=float(vm.min()), v_max=float(vm.max()), t_v10_ms=1e3 * settle(tt, vm, 1.0, 0.10, t0), t_v1_ms=1e3 * settle(tt, vm, 1.0, 0.01, t0),
                 f_min=float(fv.min()), f_max=float(fv.max()), v_end=float(vm[-1]), pk=ripple_peak(P, r), vdc_max=float(r["vdc"].max()),
                 vcl_ms=None, env=env_check(1e3 * (tt - t0), vm, A("vf_envelope")[0]), itic=env_check(1e3 * (tt - t0), vm, A("vf_envelope")[1]),
                 t_end_s=tend)
        if tr:
            ih = src.last[0] if i_hold is None else i_hold
            tail = gates_off_tail(P, Xe, len(r["t"]), S["pl"], 0j, isrc=lambda tau: ih if tau < A("dcdc_stop_us") * 1e-6 else 0.0)
            d.update(trip=True, t_off_ms=1e3 * (tr["t_off"] - t0), by=tr["by"], v_off=tr["v_off"], vdc_pk=tail["vdc_pk"], vdc_end=tail["vdc_end"],
                     half_pk=tail["half_pk"], **trip_check(P, tail))
        return d, r
    out, recs = {}, {}
    for name, a in (("0 -> rated", ("nl", "rl")), ("rated -> 0", ("rl", "nl"))):
        out[f"D-077 configuration (unbounded averaged), {name}"], recs["D-077 " + name] = stp(*a, design=False, zv=G["Zv"])
        for cn in CORNERS:
            out[f"design, single module, {cn}, {name}"], r = stp(*a, cn=cn, tend=2.0 if cn == "nom" else 0.3)
            if cn == "nom":
                recs[name] = r
        out[f"design, paralleled (Z_v {A('zv_pu')[0]:g} + j{A('zv_pu')[1]:g} pu), {name}"] = R["sec"]["steps"][name]
    # (b) / (c) the DC link as its capacitance: the source supplies the bridge up to the step, then keeps the current it had at the
    #           step for n samples, then follows the bridge's draw unidirectionally (absorbs nothing)
    def held(n):
        last = [0.0]

        def f(k, X, pc):
            if k < k0s:
                last[0] = pc / X["vdc"]
                return last[0]
            return last[0] if k < k0s + n else max(0.0, pc / X["vdc"])
        f.last = last
        return f
    p_pre = 1.5 * P["V0"] ** 2 / P["zb"]                          # the rated resistive load at 1 pu (= P_N)
    db, _ = stp("rl", "nl", tend=0.05, src=held(0))
    v_soft = P["ov_soft"][0]
    e_ret = 0.5 * P["Cdc"] * (db["vdc_max"] ** 2 - 750.0 ** 2)
    t_c = (0.5 * P["Cdc"] * (v_soft ** 2 - 750.0 ** 2) - e_ret) / p_pre
    dc_, _ = stp("rl", "nl", tend=0.05, src=held(int(0.95 * t_c / T)))
    dc2, _ = stp("rl", "nl", tend=0.05, src=held(int(1.05 * t_c / T)))
    unc = {}
    for hw in (P["ov_dc"][1], P["ov_dc"][2]):
        f = held(10 ** 9)
        unc[hw], _ = stp("rl", "nl", tend=0.05, src=f, hw=hw, i_hold=None)
        unc[hw]["i_hold_A"] = f.last[0]
    return dict(steps=out, battery_rec=recs, cap_follow=db, e_ret_J=e_ret, t_c_max_ms=1e3 * t_c, cap_tc=dc_, cap_tc_over=dc2, p_pre_W=p_pre,
                uncoordinated=unc,
                envelope=A("vf_envelope")[0], itic=A("vf_envelope")[1], f_io=G["f_io"], vclamp_pu=A("vf_vclamp_pu"))


def design_vf_ff(P, K, Kv):
    """VF load-current feed-forward (section 7c): the highest low-pass of f_io_scan_Hz whose VF cascade meets the rule at every
    corner (no load and rated load)"""
    rows = []
    for f in A("f_io_scan_Hz"):
        ev = vloop_ff(P, p_only(K), Kv, lp(f, P["T"]))
        rows.append(dict(f_io=f, pm=min(m["pm"] for m in ev), gm=min(min(m["gm"], m["gm_lo"]) for m in ev), rho=max(m["rho"] for m in ev),
                         ok=all(m["ok"] for m in ev)))
    ok = [r for r in rows if r["ok"]]
    assert ok, ("VF load-current feed-forward: no low-pass meets the rule", rows)
    return max(ok, key=lambda r: r["f_io"]), rows


# ---------------------------------------------------------------------------------------------------------- 7b. VF secondary
def study_secondary(P, G):
    """VF (off-grid) secondary restoration: slow integrators on the generator's frequency and on the sensed C_f-voltage
    magnitude above the droop + virtual-impedance primary.  (a) time constant: the linearised island at rated load for each
    sec_T_scan_s value - the restoration modes and the nearest primary mode (the 5 Hz measurement filters); choice by sec_rule.
    (b) 0 -> rated and rated -> 0 resistive steps (the second with and without the downward re-seed, and the primary layer alone
    for reference).  (c) steady state = the Newton fixed point (it exists with the restoration: the island then runs at 50 Hz
    in the synchronous frame)"""
    T, (sep_min, shift_max) = P["T"], A("sec_rule")
    scan = []
    for Ts in A("sec_T_scan_s"):
        S, X = setup(P, G, "gfm", kind="rl", sec="reseed", sec_T=Ts, iclamp=P["iclamp"])
        S.update(VFD())                          # the VF firmware of section 7c (feed-forward, over-voltage deadbeat, clamp)
        X, res = equil(P, G, S, X)
        s = np.log(linmodes(P, G, S, X)["lam"].astype(complex)) / T
        s = s[np.abs(s) > 1e-3]                  # the island's free angle (eigenvalue 1): no angle reference off-grid
        s = s[np.argsort(np.abs(s))]
        osc = s[(np.abs(s.imag) > 1e-6) & (np.abs(s.imag) < 2 * math.pi * 1e3)]
        zo = osc[np.argmin(-osc.real / np.abs(osc))]
        d = dict(T_s=Ts, tau_s=sorted(float(-1 / x.real) for x in s[:2]), real=bool(np.all(np.abs(s[:2].imag) < 1e-6)),
                 sep=float(abs(s[2]) / np.max(np.abs(s[:2]))), shift_pct=float(100 * abs(abs(s[2]) / (2 * math.pi * A("f_pq_Hz")) - 1)),
                 zeta=float(-zo.real / abs(zo)), f_osc=float(abs(zo.imag) / (2 * math.pi)), eq_res=res)
        d["ok"] = bool(d["real"] and d["sep"] >= sep_min and d["shift_pct"] <= 100 * shift_max and Ts >= sep_min * A("can_cycle_s"))
        scan.append(d)
    assert any(d["ok"] for d in scan), "no secondary time constant meets sec_rule"
    Ts, ts = min(d["T_s"] for d in scan if d["ok"]), 0.01

    def stp(k0_, k1_, sec, tend, Pop=0.0):
        S, X = setup(P, G, "gfm", kind=k0_, sec=sec, sec_T=Ts if sec else None, Pop=Pop, iclamp=P["iclamp"])
        S.update(VFD())
        X, _ = equil(P, G, S, X)
        r, _ = run(P, G, S, X, tend, lambda t, S_, X_: S_.update(pl=plant(P, "nom", k1_)) if t >= ts else None)
        k = int(ts / T)
        vm, fv, tt = np.abs(r["vc"][k:]) / P["V0"], np.gradient(np.unwrap(r["thv"]), T)[k:] / (2 * math.pi), r["t"][k:]
        return dict(v_min=float(vm.min()), v_max=float(vm.max()), t_v10_ms=1e3 * settle(tt, vm, 1.0, 0.10, ts),
                    t_v1_s=settle(tt, vm, 1.0, 0.01, ts), f_min=float(fv.min()), f_max=float(fv.max()),
                    t_f_s=settle(tt, fv, 0.0, 0.002 * F0, ts), v_end=float(vm[-1]), f_end=float(fv[-1]), pk=ripple_peak(P, r),
                    t_end_s=tend, env=env_check(1e3 * (tt - ts), vm, A("vf_envelope")[0]),
                    itic=env_check(1e3 * (tt - ts), vm, A("vf_envelope")[1])), r
    p_l = 1.5 * P["V0"] ** 2 / P["zb"]
    steps, recs = {}, {}
    for name, a in (("0 -> rated", ("nl", "rl", "reseed", 2.5)), ("rated -> 0", ("rl", "nl", "reseed", 2.5)),
                    ("rated -> 0, no re-seed", ("rl", "nl", True, 2.5)), ("rated -> 0, primary layer only", ("rl", "nl", False, 0.3, p_l))):
        steps[name], recs[name] = stp(*a)
    ss = {}
    for kind, name in (("nl", "no load"), ("rl", "rated resistive")):
        S, X = setup(P, G, "gfm", kind=kind, sec="reseed", sec_T=Ts, iclamp=P["iclamp"])
        S.update(VFD())
        X, res = equil(P, G, S, X)
        vt = abs(P["zb"] * X["xp"][3]) if kind == "rl" else abs(X["xp"][1])    # the load's voltage; no load: the C_f node
        ss[name] = dict(v_C_pu=abs(X["xp"][1]) / P["V0"], v_t_pu=vt / P["V0"], dvs_pu=X["dvs"] / P["V0"], eq_res=res,
                        df_Hz=(G["mp"] * (S["Pset"] - X["pf"]) + X["dws"]) / (2 * math.pi))
    return dict(T_s=Ts, scan=scan, steps=steps, ss=ss, reseed_pu=A("sec_reseed_pu"), can_cycle_s=A("can_cycle_s"),
                rec={k: recs[k] for k in ("0 -> rated", "rated -> 0")})


def vf_accuracy(P, sec):
    """voltage = regulation residual (the restoration's fixed point at the load terminals) + the control board's sensing floor
    (its ADC share after the two-point calibration + the divider TCR, as its design check states them); frequency = the
    oscillator (module BOM) + ageing (ASSUMED) + the angle generator's quantisation + the restoration residual"""
    a = P["adc_acc"]
    res = 100 * max(abs(d["v_t_pu"] - 1) for d in sec["ss"].values())
    v = dict(residual_pct=res, adc_rss_pct=a["rss"], adc_worst_pct=a["worst"], tcr_pct=a["tcr"], sensing_worst_pct=a["total"],
             sensing_rss_pct=math.hypot(a["rss"], a["tcr"]), bound_worst_pct=res + a["total"], bound_rss_pct=res + math.hypot(a["rss"], a["tcr"]))
    inc = [f / P["fs"] * 2 ** 32 for f in (50.0, 60.0)]
    acc = 1e6 * max(abs(round(x) - x) / x for x in inc)           # 32-bit per-unit phase accumulator, rounded increment
    th, d32, pi32, tp32, turns = np.float32(0.0), np.float32(2 * math.pi * F0 / P["fs"]), np.float32(math.pi), np.float32(2 * math.pi), 0
    for _ in range(int(P["fs"])):                                 # the rejected alternative: one second of a float32 radian
        th = np.float32(th + d32)                                 # accumulator wrapped to +/-pi
        if th >= pi32:
            th, turns = np.float32(th - tp32), turns + 1
    f32 = 1e6 * ((turns * 2 * math.pi + float(th)) / (2 * math.pi * F0) - 1)
    fres = 1e6 * max(abs(d["df_Hz"]) for d in sec["ss"].values()) / F0
    f = dict(oscillator_ppm=P["osc"]["ppm"], oscillator=P["osc"]["part"], ageing_ppm=A("osc_ageing_ppm"), accumulator_ppm=acc,
             float32_radian_ppm=f32, residual_ppm=fres)
    f["bound_ppm"] = f["oscillator_ppm"] + f["ageing_ppm"] + acc + fres
    f["bound_pct"] = f["bound_ppm"] / 1e4
    return dict(voltage=v, frequency=f)


# ---------------------------------------------------------------------------------------------------------- 8b. THDu
_PD = []


def pcs_design_module():
    """sim/pcs_design.py's exact PWM Fourier series and modulation policy, imported (code reuse, its import prints muted)"""
    if not _PD:
        import contextlib
        import io as _io
        import sys as _sys
        _sys.path.insert(0, HERE)
        with contextlib.redirect_stdout(_io.StringIO()):
            import pcs_design
        _PD.append(pcs_design)
    return _PD[0]


def vloop_eval(P, Kin, Kv, pl_of=None, kinds=("nl", "rl"), corners=CORNERS):
    """C_f-voltage loop around the inner current loop: margins at the voltage-controller output, the cascade's largest pole"""
    f = fgrid(P)
    z = np.exp(2j * np.pi * f * P["T"])
    out = []
    for kind in kinds:
        for cn in corners:
            pl = pl_of(kind, cn) if pl_of else plant(P, cn, kind, None)
            Ac, Bref, _ = closed(pl, Kin)
            m = margins(f, (Kv["Kp"] + fr(Kv["res"], z)) * ss_fr(Ac, Bref, np.eye(len(Ac))[6], z))
            m.update(kind=kind, cn=cn, rho=float(np.max(np.abs(np.linalg.eigvals(cascade(P, pl, Kin, Kv))))))
            m["ok"] = bool(m["pm"] >= RULE_PM and min(m["gm"], m["gm_lo"]) >= RULE_GM and m["rho"] < 1 - 1e-9)
            out.append(m)
    return out


def design_gfm_harm(P, K, Kv):
    """h5 / h7 resonant terms in the C_f-voltage PR (VF THDu option): lead centred on the spread of the inner closed loop's phase
    at h x 50 Hz over no load / rated load and the corners, a term considered only if the spread leaves lead_margin_deg; the
    variant is evaluated against the margin rule at every case and against the current loop's decay rule (closed-loop mode of
    each term decaying at >= res_decay_frac x 2 pi sigma_h); adopted only if both hold"""
    Kin, T, w0 = p_only(K), P["T"], P["w0"]
    Kh = 2 * Kv["Kp"] * 2 * math.pi * A("sigma_h_Hz")
    keep, phi, spread = [], {}, {}
    for h in (5, 7):
        z = np.array([cx(h * w0 * T)])
        ang = []
        for kind in ("nl", "rl"):
            for cn in CORNERS:
                Ac, Bref, _ = closed(plant(P, cn, kind, None), Kin)
                ang.append(np.angle(ss_fr(Ac, Bref, np.eye(len(Ac))[6], z)[0]))
        ang = np.unwrap(ang)
        phi[h] = -(ang.max() + ang.min()) / 2
        spread[h] = math.degrees(np.max(np.abs(ang + phi[h])))
        if spread[h] <= 90 - A("lead_margin_deg"):
            keep.append(h)
    Kvh = dict(Kv, res=par(Kv["res"], *[resonant(P, h, Kh, phi[h]) for h in keep]))
    ev = vloop_eval(P, Kin, Kvh)
    decay = {}
    for h in keep:
        near = []
        for kind in ("nl", "rl"):
            for cn in ("nom", "low"):
                s = np.log(np.linalg.eigvals(cascade(P, plant(P, cn, kind, None), Kin, Kvh)).astype(complex)) / T
                near += [x.real for x in s if abs(abs(x.imag) / (2 * np.pi) - h * F0) < 30]
        decay[h] = max(near) if near else 0.0
    need = -A("res_decay_frac") * 2 * math.pi * A("sigma_h_Hz")
    ok_m, ok_d = all(m["ok"] for m in ev), all(v <= need for v in decay.values())
    return dict(Kv=Kvh, harm=list(keep), phi={h: math.degrees(phi[h]) for h in phi}, spread=spread, Kh=Kh, ev=ev, ok_margin=ok_m,
                decay=decay, decay_need=need, ok_decay=ok_d, adopted=bool(keep and ok_m and ok_d),
                pm=min(m["pm"] for m in ev), gm=min(min(m["gm"], m["gm_lo"]) for m in ev))


def lc_v(P, f, RL=None, term=False, L=None, C=None, Rd=None, Cd=None):
    """continuous per-phase transfer converter voltage -> C_f voltage (term: -> load terminals) with the R_d-C_d branch, L2 + DM
    leakage, R_dc and a resistive load RL (None: no load; L, C, Rd, Cd override L1, C_f and the branch)"""
    w = 2 * np.pi * np.asarray(f, float)
    zl1 = P["R1"] + 1j * w * (L or P["L1"])
    zc = 1 / (1j * w * (C or P["Cf"]) + 1 / ((Rd or P["Rd"]) + 1 / (1j * w * (Cd or P["Cd"]))))
    if RL is None:
        return zc / (zl1 + zc)
    zo = P["R2"] + 1j * w * (P["L2"] + P["Ldm"]) + RL
    zp = zc * zo / (zc + zo)
    return zp / (zl1 + zp) * (RL / zo if term else 1.0)


def zs_mismatch(P, u0, hs):
    """zero sequence u0 (one fundamental period, volts) applied to legs whose L1-C_f filters sit at opposite tolerance corners
    (corner 'high' against corner 'low': L1, C_f and the R_d-C_d branch at the assembly's tolerances; unloaded): the difference
    of the two responses per order h, and u0's amplitude per order"""
    U0 = np.abs(np.fft.rfft(u0) / len(u0) * 2)[hs]
    (lh, _, ch, _, rh, dh), (ll, _, cl, _, rl, dl) = corner(P, "high"), corner(P, "low")
    dH = np.abs(lc_v(P, hs * F0, L=lh, C=ch, Rd=rh, Cd=dh) - lc_v(P, hs * F0, L=ll, C=cl, Rd=rl, Cd=dl))
    return U0, dH


def study_thdu(P, G, R):
    """THDu on a linear balanced load, VF mode (CALCULATED, analytical - not a switched simulation): (a) the carrier groups of
    regular-sampled double-update PWM (sim/pcs_design.py's exact Fourier series) through the LCL with the load, at the load
    terminals; (b) the dead-time error (the PCS-PWR band's top, and the per-leg compensation residual) with a ripple-aware shape
    (the error saturates only where the current exceeds half the local ripple), through the closed VF loop (voltage PR with and
    without the h5 / h7 terms); (c) three-wire with the min-max zero sequence active: its 150 Hz family leaking into the measured
    alpha-beta through the per-channel gain error; the DC component separately.  Three-wire = line-to-neutral of the floating
    star (non-triplen orders); four-wire mode A = phase-to-N with the N leg at 50 % (every order, the N leg's carrier term
    included; below the mode-A window mode B, the phases' min-max zero sequence on all four legs, its carrier approximated with
    the N leg at zero reference).  Wherever a zero sequence is modulated, the filters' tolerance mismatch leaves part of it in
    the output (2/3 of the corner difference in the three-wire line-to-neutral, all of it phase-to-N).  Harmonics h2-h50 plus
    the carrier groups, as a fraction of the fundamental."""
    pd, T, w0 = pcs_design_module(), P["T"], P["w0"]
    Kin, hv = p_only(G["K"]), R["gfm_harm"]
    loops = {"PR h1": G["Kv"]}
    if hv["harm"] and hv["ok_margin"]:                           # the option, adopted or not (THDu both ways)
        loops["PR h1+" + "+".join(f"h{h}" for h in hv["harm"])] = hv["Kv"]
    loads = {"rated": P["zb"], "50 %": 2 * P["zb"], "no load": None}
    hs = np.arange(2, 51)
    z = np.exp(1j * hs * w0 * T)
    e_g = P["adc_acc"]["total"] / 100
    dts = {f"{P['dt_gate_ns'][1]:.0f} ns, not compensated": P["dt_gate_ns"][1], "compensated per leg": A("dt_resid_ns")}
    _, cN = pd.pwm_fourier(2, 0.0, P["fsw"], "none")
    rows, N = [], 1280
    th = 2 * np.pi * np.arange(N) / N
    for lname, RL in loads.items():
        kind = "rl" if RL else "nl"
        pl = plant(P, "nom", kind, None, RL=RL)
        M = np.linalg.inv(1j * w0 * np.eye(8) - pl["A"])
        xu = M @ pl["Bu"]
        Uv = P["V0"] / xu[1]                                     # converter voltage for V0 on C_f, angle 0
        i1 = xu[0] * Uv
        Hd = {}
        for ln, Kv in loops.items():                             # converter-voltage disturbance -> load terminals, closed VF loop
            Z = cascade(P, pl, Kin, Kv)
            Bw = np.zeros(len(Z))
            Bw[:8] = pl["Gu"]
            Cv = np.zeros(len(Z))
            Cv[3 if RL else 1] = RL if RL else 1.0
            Hd[ln] = np.abs(ss_fr(Z, Bw, Cv, z))
        for wire in ("3W", "4W"):
            for vdc in P["vf_vdc"]:
                m = abs(Uv) / (vdc / 2)
                zs = pd.zs_policy(2, m) if wire == "3W" else ("none" if vdc >= P["fw4"]["modeA_from"] else "minmax")
                h, cpl = pd.pwm_fourier(2, m, P["fsw"], zs)
                sel = h > 50
                Ht = lc_v(P, h[sel] * F0, RL, term=True)
                if wire == "3W":
                    vh = Ht * (cpl[0] - cpl.mean(0))[sel] * vdc / 2
                else:
                    vh = (Ht * cpl[0][sel] - lc_v(P, h[sel] * F0) * cN[0][sel]) * vdc / 2
                carrier = float(np.sqrt(np.sum(np.abs(vh) ** 2)) / P["V0"])
                refs = np.stack([m * np.cos(th + np.angle(Uv) - k * 2 * np.pi / 3) for k in range(3)])
                u0 = pd.zero_seq(refs, zs) if zs != "none" else np.zeros(N)
                d = refs[0] + u0
                ia = np.real(i1 * np.exp(1j * th))
                rip = vdc * (1 - np.clip(d, -1, 1) ** 2) / (4 * P["L1"] * P["fsw"])
                shape = -np.clip(ia / np.maximum(rip / 2, 1e-9), -1, 1)
                E = np.fft.rfft(shape) / N * 2
                odd = (hs % 2 == 1) & ((hs % 3 != 0) if wire == "3W" else True)
                leak, mism = np.zeros(len(hs)), np.zeros(len(hs))
                if zs != "none":                                  # 150 Hz family of the zero sequence on the C_f nodes
                    U0, dH = zs_mismatch(P, u0 * vdc / 2, hs)
                    mism = (2 / 3 if wire == "3W" else 1.0) * dH * U0
                    if wire == "3W":                              # ... and through the measured alpha-beta (gain error)
                        leak = 4 / 3 * e_g * U0 * np.abs(lc_v(P, hs * F0))
                for ln in loops:
                    for dn, td in dts.items():
                        dv = vdc * P["fsw"] * td * 1e-9
                        dth = np.where(odd, Hd[ln] * np.abs(E[hs]) * dv, 0.0)
                        lo_ = np.sqrt(dth ** 2 + leak ** 2 + mism ** 2) / P["V0"]
                        thd = math.sqrt(carrier ** 2 + float(np.sum(lo_ ** 2)))
                        terms = sorted([("carrier groups", carrier)] + [(f"h{x} dead time", y / P["V0"]) for x, y in zip(hs, dth) if y > 0]
                                       + [(f"h{x} zero sequence via the gain error", y / P["V0"]) for x, y in zip(hs, leak) if y > 0]
                                       + [(f"h{x} zero sequence via the filter mismatch", y / P["V0"]) for x, y in zip(hs, mism) if y > 0],
                                       key=lambda t: -t[1])
                        rows.append(dict(wire=wire, mode=("mode A" if zs == "none" else "mode B") if wire == "4W" else zs, vdc=vdc,
                                         load=lname, m=m, zs=zs, loop=ln, dead_time=dn, thdu_pct=100 * thd, carrier_pct=100 * carrier,
                                         dead_time_pct=100 * float(np.sqrt(np.sum(dth ** 2))) / P["V0"],
                                         leak_pct=100 * float(np.sqrt(np.sum(leak ** 2))) / P["V0"],
                                         mismatch_pct=100 * float(np.sqrt(np.sum(mism ** 2))) / P["V0"],
                                         dominant=[(n, 100 * v) for n, v in terms[:3]]))
    hw = P["fw4"]["half_wave"]
    dc = dict(dc_V=hw["dc_measurement_divider_V"], limit_V=hw["dc_component_limit_V"],
              dc_pct_Un=100 * hw["dc_measurement_divider_V"] / (P["V0"] / math.sqrt(2)))
    return dict(rows=rows, loops=list(loops), dc=dc, harm=hv["harm"], harm_adopted=hv["adopted"])


# ---------------------------------------------------------------------------------------------------------- 8c. imbalance
def study_imbalance(P, R):
    """output voltage imbalance on a linear balanced load (CALCULATED).  The loop regulates the MEASURED voltages exactly at
    50 / 60 Hz (resonant terms at +/- w0), so the output carries the inverse of each channel's gain and phase error: per-channel
    gain after the two-point calibration = the control board's ADC share + divider TCR, every term independent per channel
    (conservative: the reference term is common to the three ADCs only if they share one reference - not credited); per-channel
    phase = the drawn anti-alias poles at their BOM tolerances (power-board divider pole, control-board charge bucket); the
    software angle generator's float32 constants; the ADC plan's simultaneity.  Three-wire: alpha-beta PR -> positive sequence
    of the measured set = the reference, negative sequence = 0, zero sequence free (the line-to-line output); four-wire: one PR
    per phase on its phase-to-N voltage.  Worst over every corner of signs."""
    vd, bk, a = P["vdiv"], P["bucket"], np.exp(2j * np.pi / 3)
    rth = 1 / (1 / vd["R_bot"] + 1 / (vd["n"] * vd["R_top"]))
    assert abs(1 / (2 * math.pi * rth * vd["C"]) / vd["f_stated"] - 1) < 0.02, "AC divider pole vs the PCS-PWR design check"
    e = P["adc_acc"]["total"] / 100

    def lag(f, kt=1.0, kb=1.0, kc=1.0, kr=1.0, kk=1.0):
        r = 1 / (1 / (vd["R_bot"] * kb) + 1 / (vd["n"] * vd["R_top"] * kt))
        return math.atan(2 * math.pi * f * r * vd["C"] * kc) + math.atan(2 * math.pi * f * bk["R"] * kr * bk["C"] * kk)
    tols = (vd["tol_top"], vd["tol_bot"], vd["tol_c"], bk["tol_r"], bk["tol_c"])
    gen = max(abs(float(np.float32(math.sqrt(3) / 2)) / (math.sqrt(3) / 2) - 1),        # rad: float32 inverse-Clarke constant,
              2 * math.pi * abs(float(np.float32(1 / 3)) - 1 / 3))                     # and the 1/3-turn offset of a per-phase reference
    vc = [rd for rd in R["plan"]["rounds"] if set(rd["nets"]) == {"VC1_ADC", "VC2_ADC", "VC3_ADC"}]
    assert len(vc) == 1, "ADC plan: VC1-3 are not one conversion round"
    out = {}
    for f in (50.0, 60.0):
        ph0 = lag(f)
        dphi = max(abs(lag(f, *[1 + s * t for s, t in zip(sg, tols)]) - ph0) for sg in itertools.product((-1, 1), repeat=5))
        skew = 0.0 if vc[0]["adc"] else 2 * math.pi * f * R["plan"]["t_conv_us"] * 1e-6
        worst = {"3W": [0.0, 0.0, 0.0], "4W": [0.0, 0.0, 0.0]}
        for gs in np.ndindex(2, 2, 2):
            for ps in np.ndindex(2, 2, 2):
                c = np.array([(1 + e * (2 * gs[k] - 1)) * np.exp(-1j * ((2 * ps[k] - 1) * (dphi + skew) + ph0)) for k in range(3)])
                v4 = np.array([a ** -k for k in range(3)]) / c * np.exp(-1j * ph0)          # per phase: measured = the reference
                cb, s2, sm2 = c.mean(), np.mean(c * a ** (2 * np.arange(3))), np.mean(c * a ** (-2 * np.arange(3)))
                Pp, Nn = np.linalg.solve(np.array([[cb, s2], [sm2, cb]]), np.array([1.0, 0.0]))
                v3 = np.array([Pp * a ** -k + Nn * a ** k for k in range(3)])
                v3 = v3 - np.roll(v3, -1)                                                     # line-to-line a-b, b-c, c-a
                for wire, v in (("3W", v3), ("4W", v4)):
                    amp = np.abs(v)
                    dang = np.degrees(np.angle(v / np.roll(v, -1))) - 120.0                   # displacement to the next phase
                    w = worst[wire]
                    w[0] = max(w[0], 100 * float(np.max(np.abs(amp / amp.mean() - 1))))
                    w[1] = max(w[1], float(np.max(np.abs(dang))) + math.degrees(gen))
                    seq = [abs(np.mean(v * a ** (s * np.arange(3)))) for s in (1, -1)]
                    w[2] = max(w[2], 100 * seq[1] / seq[0])
        out[f"{f:g} Hz"] = dict(lag_nom_deg=math.degrees(ph0), dphi_deg=math.degrees(dphi), skew_deg=math.degrees(skew),
                                **{w: dict(amp_pct=v[0], angle_deg=v[1], vuf_pct=v[2]) for w, v in worst.items()})
    b = {w: dict(amp_pct=max(out[k][w]["amp_pct"] for k in out), angle_deg=max(out[k][w]["angle_deg"] for k in out),
                 vuf_pct=max(out[k][w]["vuf_pct"] for k in out)) for w in ("3W", "4W")}
    return dict(per_freq=out, bound=b, gain_pct=100 * e, generator_deg=math.degrees(gen), vc_round_simultaneous=bool(vc[0]["adc"]),
                divider=dict(R_th_ohm=rth, C_nF=vd["C"] * 1e9, tol_C=vd["tol_c"], tol_R=vd["tol_bot"]),
                bucket=dict(R_ohm=bk["R"], C_nF=bk["C"] * 1e9, tol_C=bk["tol_c"], tol_R=bk["tol_r"]))


# ---------------------------------------------------------------------------------------------------------- 9b. neutral leg
def zoh(Ac, B, T):
    n = len(B)
    M = np.zeros((n + 1, n + 1))
    M[:n, :n], M[:n, n] = Ac, B
    E = expm(M * T)
    return E[:n, :n], E[:n, n]


def nplant(P, cn, Lt=None, Rt=0.0, Ct=None, Rdt=None, Cdt=None, src=False):
    """four-wire neutral leg: L_N (the L1 part, same corners) into N_F with C_fN (= C_f, same tolerance) and its R_d-C_d branch
    to M; beyond N_F the path L_t + R_t into a capacitor C_t (with its R_dt-C_dt branch) to M - the phases' C_f star through
    their L2 - or (src) into a held phase voltage, the input Gs (neither: N terminal open).  States i_N, v_N, v_dN, i_t, v_t,
    y_i (IL4), y_v (VGN - VA), v_dt; a current drawn from N_F is the input Gi.  Discretised as plant()"""
    L1, _, Cf, ti, Rd, Cd = corner(P, cn)
    Ac, Bu, Bi, Bs, g = np.zeros((8, 8)), np.zeros(8), np.zeros(8), np.zeros(8), 1 / Rd
    Ac[0, 0], Ac[0, 1], Bu[0] = -P["R1"] / L1, -1 / L1, 1 / L1
    Ac[1, 0], Ac[1, 1], Ac[1, 2], Bi[1] = 1 / Cf, -g / Cf, g / Cf, -1 / Cf
    Ac[2, 1], Ac[2, 2] = g / Cd, -g / Cd
    Ac[3, 3] = Ac[4, 4] = Ac[7, 7] = -1e3                         # path absent: decoupled dummy states
    if Ct or src:
        Ac[1, 3] = -1 / Cf
        Ac[3, 1], Ac[3, 3] = 1 / Lt, -Rt / Lt
    if src:
        Bs[3] = -1 / Lt
    elif Ct:
        Ct, gt = Ct * Cf / P["Cf"], 1 / Rdt
        Ac[3, 4] = -1 / Lt
        Ac[4, 3], Ac[4, 4], Ac[4, 7] = 1 / Ct, -gt / Ct, gt / Ct
        Ac[7, 4], Ac[7, 7] = gt / Cdt, -gt / Cdt
    Ac[5, 0], Ac[5, 5] = 1 / ti, -1 / ti
    Ac[6, 1], Ac[6, 6] = 1 / P["tv"], -1 / P["tv"]
    Phi, Gu = zoh(Ac, Bu, P["T"])
    return dict(A=Ac, Bu=Bu, Phi=Phi, Gu=Gu, Gi=zoh(Ac, Bi, P["T"])[1], Gs=zoh(Ac, Bs, P["T"])[1], ig=3, kind="n")


def study_neutral(P, G, R):
    """four-wire neutral leg (PCS-PWR-4W as drawn, no new topology): L_N = the L1 part, C_fN = C_f and the same R_d-C_d branch
    (asserted from pcs_spec four_wire), but no L2 of its own: beyond N_F the neutral current returns through the load and the
    phases' L2 to their C_f star on M (off-grid) or through the grid's neutral and the phases' L2 in parallel (grid-connected).
    (a) the phases' PR current controller on IL4 and the phases' C_f-voltage PR on v(N_F - M), margins at every corner for the
    N-node terminations; (b) the N-node excursion when one phase takes its rated current at once (100 % unbalance, mode A,
    linear, the neutral current as a current source); (c) mode B: the residual of the zero-sequence feed-forward in the
    phase-to-N voltages from the L1 / C_f tolerance mismatch; (d) the DC interaction with the DC-component regulators"""
    fw = P["fw4"]
    same = dict(L_N=abs(fw["L_N_uH"] * 1e-6 / P["L1"] - 1) < 1e-6, C_fN=abs(fw["C_fN_uF"] * 1e-6 / P["Cf"] - 1) < 1e-6,
                R_d=abs(fw["R_d_ohm"] / P["Rd"] - 1) < 1e-6, C_d=abs(fw["C_d_uF"] * 1e-6 / P["Cd"] - 1) < 1e-6)
    assert all(same.values()), ("pcs_spec four_wire: the N filter is no longer the phase filter", same)
    K, Kin, Kv, f = G["K"], p_only(G["K"]), G["Kv"], fgrid(P)
    dm, zb = P["Ldm"], P["zb"]

    def term(case, cn, scr=None):
        _, L2c, _, _, rd, cd = corner(P, cn)
        if case == "open":
            return nplant(P, cn)
        if case == "balanced":
            return nplant(P, cn, dm + L2c / 3, (P["R2"] + zb) / 3, 3 * P["Cf"], rd / 3, 3 * cd)
        if case == "unbalanced":
            return nplant(P, cn, dm + L2c, P["R2"] + zb, P["Cf"], rd, cd)
        if case == "unbalanced, held":
            return nplant(P, cn, dm + L2c, P["R2"] + zb, src=True)
        x = P["zb"] / scr / math.hypot(1.0, A("grid_rx")) if scr else 0.0
        return nplant(P, cn, dm + (L2c + x / P["w0"]) / 3, (P["R2"] + A("grid_rx") * x) / 3, 3 * P["Cf"], rd / 3, 3 * cd)
    cases = [("off-grid, N terminal open (balanced load: no neutral current)", "open", None),
             ("off-grid, balanced rated load, the loads' star on N, the phase nodes as their C_f", "balanced", None),
             ("off-grid, 100 % unbalance, the loaded phase's node as its C_f", "unbalanced", None),
             ("off-grid, 100 % unbalance, the loaded phase's node held by its loop", "unbalanced, held", None)] + \
            [(f"grid-connected, {scr_name(s)}: zero-sequence path to the phases' C_f star", "grid", s) for s in A("scr")]
    rows = []
    for name, case, scr in cases:
        ci, cv = [], []
        for cn in CORNERS:
            pl = term(case, cn, scr)
            m = margins(f, loop(P, K, pl, f))
            m["rho"] = float(np.max(np.abs(np.linalg.eigvals(closed(pl, K)[0]))))
            m["ok"] = bool(m["pm"] >= RULE_PM and min(m["gm"], m["gm_lo"]) >= RULE_GM and m["rho"] < 1 - 1e-9)
            ci.append(m)
        cv = vloop_eval(P, Kin, Kv, pl_of=lambda kind, cn: term(case, cn, scr), kinds=(case,))
        pn = term(case, "nom", scr)
        rows.append(dict(case=name, i_pm=min(m["pm"] for m in ci), i_gm=min(min(m["gm"], m["gm_lo"]) for m in ci),
                         i_rho=max(m["rho"] for m in ci), i_ok=all(m["ok"] for m in ci),
                         v_pm=min(m["pm"] for m in cv), v_gm=min(min(m["gm"], m["gm_lo"]) for m in cv),
                         v_rho=max(m["rho"] for m in cv), v_ok=all(m["ok"] for m in cv),
                         v_bw=gfm_bw(P, Kin, Kv, None, "nom", f, np.exp(2j * np.pi * f * P["T"]), pl=pn)))
    ph = [r for r in R["table"] if r["damp"] == "passive" and r["cn"] in CORNERS]
    ref = dict(i_pm=min(r["pm"] for r in ph), i_gm=min(min(r["gm"], r["gm_lo"]) for r in ph),
               v_pm=R["gv"]["pm"], v_gm=R["gv"]["gm"], bw={m["kind"]: m["f_bw"] for m in R["gv"]["ev"] if m["cn"] == "nom"})
    # (b) N-node excursion: a rated resistive load switched on between the held phase-a voltage (at its peak) and N, cascade closed
    pl = term("unbalanced, held", "nom")
    Z = cascade(P, pl, Kin, Kv)
    B = np.zeros(len(Z))
    B[:8] = pl["Gs"]
    x, vN, iN, n = np.zeros(len(Z)), [], [], int(0.1 / P["T"])
    for k in range(n):
        x = Z @ x + B * P["V0"] * math.cos(P["w0"] * k * P["T"])
        vN.append(x[1])
        iN.append(x[3])
    vN = np.abs(np.array(vN)) / P["V0"]
    exc = dict(pk_pu=float(vN.max()), t1_ms=1e3 * settle(np.arange(n) * P["T"], vN, 0.0, 0.01, 0.0),
               end_pu=float(vN[-int(0.02 / P["T"]):].max()), iN_pk=float(np.max(np.abs(iN))))
    # (c) mode B: zero sequence of the phase references (min-max) at the lowest DC voltage, through the phase and N filters at
    #     opposite tolerance corners (unloaded L-C + branch each); its DC content
    pd = pcs_design_module()
    th = 2 * np.pi * np.arange(1280) / 1280
    vdc = P["vf_vdc"][0]
    plr = plant(P, "nom", "rl", None)
    m = abs(P["V0"] / (np.linalg.inv(1j * P["w0"] * np.eye(8) - plr["A"]) @ plr["Bu"])[1]) / (vdc / 2)   # rated resistive, V0 on C_f
    u0 = pd.zero_seq(np.stack([m * np.cos(th - k * 2 * np.pi / 3) for k in range(3)]), "minmax") * vdc / 2
    hs = np.arange(2, 51)
    U0, dH = zs_mismatch(P, u0, hs)
    k = int(np.argmax(dH * U0))
    modeB = dict(vdc=vdc, m=m, u0_150_V=float(U0[1]), resid_pct=100 * float(np.sqrt(np.sum((dH * U0) ** 2))) / P["V0"],
                 resid_150_pct=100 * float(dH[1] * U0[1]) / P["V0"], h_max=int(hs[k]), resid_hmax_pct=100 * float(dH[k] * U0[k]) / P["V0"],
                 u0_mean_V=float(np.mean(u0)))
    dc = dict(K_pv_S=Kv["Kp"], I_dc_A=P["fw4"]["half_wave"]["I_dc_A"], offset_without_V=P["fw4"]["half_wave"]["I_dc_A"] / Kv["Kp"])
    return dict(same=same, rows=rows, phase_ref=ref, excursion=exc, modeB=modeB, dc=dc)


# ---------------------------------------------------------------------------------------------------------- 9c. four-wire coupled
N4, IN4, VCN4, VDN4, YIN4, YVN4 = 26, 21, 22, 23, 24, 25    # per phase p: 7p + (i1, vC, vD, i2, ig, yi, yv); then the N leg
_P4 = {}


def ph4_plant(P, loads, scr=None):
    """four-wire per-phase plant, stationary frame (review R2-13): three phase legs (L1 + R_dc into C_f with the R_d-C_d branch to M,
    L2 + DM leakage to the terminal) and the N leg (L_N = the L1 part into N_F, C_fN + its branch to M); every load returns to N_F (the
    N terminal); grid-connected (scr not None): each terminal also through L_g, R_g to its source, the grid's star solid at N_F
    (ASSUMED); loads per phase: ('R', ohm) | ('open',) | ('short',) (r_sc + l_sc to N).  Discretised at T: ZOH on the four leg voltages
    (vs M), the exact integral of the rotating grid sources"""
    key = (loads, scr)
    if key in _P4:
        return _P4[key]
    Ac, Bu, Bg = np.zeros((N4, N4)), np.zeros((N4, 4)), np.zeros((N4, 3))
    L1, R1, Cf, Cd, Rd, ti, tv = P["L1"], P["R1"], P["Cf"], P["Cd"], P["Rd"], P["ti"], P["tv"]
    if scr:
        x = P["zb"] / scr / math.hypot(1.0, A("grid_rx"))
        Rg, Lg = A("grid_rx") * x, x / P["w0"]
    for p in range(3):
        i1, vC, vD, i2, ig, yi, yv = (7 * p + j for j in range(7))
        ld = loads[p]
        Rp = ld[1] if ld[0] == "R" else A("r_sc_ohm") if ld[0] == "short" else R_OPEN
        L2e = P["L2"] + P["Ldm"] + (A("l_sc_H") if ld[0] == "short" else 0.0)
        Ac[i1, i1], Ac[i1, vC], Bu[i1, p] = -R1 / L1, -1 / L1, 1 / L1
        Ac[vC, i1], Ac[vC, i2], Ac[vC, vC], Ac[vC, vD] = 1 / Cf, -1 / Cf, -1 / (Rd * Cf), 1 / (Rd * Cf)
        Ac[vD, vC], Ac[vD, vD] = 1 / (Rd * Cd), -1 / (Rd * Cd)
        Ac[i2, vC], Ac[i2, VCN4], Ac[i2, i2] = 1 / L2e, -1 / L2e, -(P["R2"] + Rp) / L2e
        if scr:
            Ac[i2, ig], Ac[ig, i2], Ac[ig, ig], Bg[ig, p] = Rp / L2e, Rp / Lg, -(Rp + Rg) / Lg, -1 / Lg
        else:
            Ac[ig, ig] = -1e3
        Ac[yi, i1], Ac[yi, yi], Ac[yv, vC], Ac[yv, yv] = 1 / ti, -1 / ti, 1 / tv, -1 / tv
        Ac[VCN4, i2] = 1 / Cf
    Ac[IN4, IN4], Ac[IN4, VCN4], Bu[IN4, 3] = -R1 / L1, -1 / L1, 1 / L1
    Ac[VCN4, IN4], Ac[VCN4, VCN4], Ac[VCN4, VDN4] = 1 / Cf, -1 / (Rd * Cf), 1 / (Rd * Cf)
    Ac[VDN4, VCN4], Ac[VDN4, VDN4] = 1 / (Rd * Cd), -1 / (Rd * Cd)
    Ac[YIN4, IN4], Ac[YIN4, YIN4], Ac[YVN4, VCN4], Ac[YVN4, YVN4] = 1 / ti, -1 / ti, 1 / tv, -1 / tv
    Phi, Gu = zoh(Ac, np.zeros(N4), P["T"])
    M = np.zeros((N4 + 4, N4 + 4))
    M[:N4, :N4], M[:N4, N4:] = Ac, Bu
    Gu = expm(M * P["T"])[:N4, N4:]
    Gg = []
    for p in range(3):
        Mc = np.zeros((N4 + 1, N4 + 1), complex)
        Mc[:N4, :N4], Mc[:N4, N4], Mc[N4, N4] = Ac, Bg[:, p], 1j * P["w0"]
        Gg.append(expm(Mc * P["T"])[:N4, N4] * cx(-2 * math.pi * p / 3))
    _P4[key] = dict(Phi=Phi, Gu=Gu, Gg=np.array(Gg).sum(0), grid=bool(scr))
    return _P4[key]


def ph4_run(P, G, t_end, loads_of, mode_of, grid_of=lambda t: None, I_ref=(0j, 0j, 0j), t_lim=None, n_ff=False, vg=None, T_dc=None):
    """four-wire per-phase simulation (review R2-13): the plant of ph4_plant switched by loads_of(t) / grid_of(t) (a half-wave load
    ('hw', ohm) resolved each sample by the sign of its phase-to-N voltage); controllers per phase, stationary frame, the blocks of
    the three-wire design applied per phase: grid forming (mode 'gfm') = the C_f-voltage PR on the phase-to-N voltage with the C_f
    current of the reference, the load-current feed-forward and the over-voltage deadbeat of section 7c, the reference limiter at
    the 200 ms tier, the P-only inner loop with the SOGI feed-forward; grid following ('gfl') = the PR current controller (h1, h5, h7
    with their leads) per phase on the reference Re(I_p e^{j(w0 t - 2 pi p / 3)}) + the C_f current, the angle the grid's (no PLL);
    the N leg in every mode its cascade of section 9b holding v(N_F - M) at zero, with (n_ff) the sum of the phases' load-current
    estimates as the feed-forward of its current reference (the return current the loads draw); DC regulators (one-cycle means -> integrators -> offsets) on the
    three phase-to-N voltages and on N_F; the per-sample clamp on all four legs; each leg within m_A V_dc/2 (mode A).  The DC link a
    battery (750 V stiff).  t_lim: the 200 ms limit timer trips (gates off, the run ends).  Bumpless GFL -> GFM: voltage PR states zero
    (the feed-forward carries the load); GFM -> GFL: the references and the h1 states from one-cycle DFTs of the present currents and
    commands"""
    T, w0, V0, Ceq, Kp = P["T"], P["w0"], P["V0"], P["Ceq"], G["K"]["Kp"]
    Ar, Br, Cr, Dr = G["K"]["res"]
    Af, Bf, Cfb, Df = G["K"]["yv"]
    Av, Bv, Cv, Dv = G["Kv"]["res"]
    Kpv, L, Ic, ilim = G["Kv"]["Kp"], P["L1_lo"], P["iclamp"], P["I_lim"]
    umax, a_io, a_tv, vth = P["fw4"]["m_A"] * 375.0, G["a_io"], G["a_tv"], A("vf_vclamp_pu") * V0
    ncyc, kdc = int(round(P["fs"] / F0)), T / (T_dc or A("dc_reg_T_s"))
    n = int(round(t_end / T))
    x, d = np.zeros(N4), np.zeros(4)
    xr, xf, xv = np.zeros((4, len(Br))), np.zeros((4, len(Bf))), np.zeros((4, len(Bv)))
    io, yvp, xdc = np.zeros(4), np.zeros(4), np.zeros(4)
    buf, bsum = np.zeros((4, ncyc)), np.zeros(4)
    Iref, mode, tl, k_lim = list(I_ref), None, 0.0, -10 ** 9
    rec = {k: np.zeros(n) for k in ("t", "va", "vb", "vc", "vn", "ia", "ib", "ic", "iN", "da", "dn", "lim", "vcl")}
    hist_u, hist_i, hist_f = np.zeros((4, ncyc)), np.zeros((4, ncyc)), np.zeros((4, ncyc))
    trip = None
    for k in range(n):
        t = k * T
        ld = loads_of(t)
        yv_all = np.array([x[7 * p + 6] for p in range(3)] + [x[YVN4]])
        hw = tuple(("R", l[1]) if l[0] == "hw" and yv_all[p] - yv_all[3] > 0 else ("open",) if l[0] == "hw" else l for p, l in enumerate(ld))
        pl = ph4_plant(P, hw, grid_of(t))
        yi_all = np.array([x[7 * p + 5] for p in range(3)] + [x[YIN4]])
        m_new = mode_of(t)
        if m_new != mode and mode is not None:
            if m_new == "gfm":
                xv[:], xdc[:3] = 0.0, 0.0
            else:                                              # GFM -> GFL: one-cycle DFT of the present currents and commands
                js = np.arange(k - ncyc, k)
                ph = np.exp(-1j * w0 * T * js)
                Cr1, Ar1 = Cr[:2], Ar[:2, :2]                  # the h1 block (first in the stack)
                for p in range(3):
                    I1, U1, F1 = (2 / ncyc * np.sum(h[p][js % ncyc] * ph) for h in (hist_i, hist_u, hist_f))
                    Iref[p] = I1 * cx(2 * math.pi * p / 3) - 1j * w0 * Ceq * V0
                    W = U1 - F1                                # the PR output that continues the present command at zero error
                    xr[p][:] = 0.0
                    xr[p][:2] = np.linalg.solve(np.array([Cr1, Cr1 @ Ar1]), np.array([(W * cx(w0 * T * k)).real, (W * cx(w0 * T * (k + 1))).real]))
        mode = m_new
        u = np.zeros(4)
        lim_any = vcl_any = False
        for q in range(4):                                     # q 0..2 phases, 3 = the N leg
            y_i, y_v = yi_all[q], yv_all[q]
            vq = (y_v - a_tv * yvp[q]) / (1 - a_tv)
            iod = y_i - Ceq * (y_v - yvp[q]) / T
            io[q] += a_io * (iod - io[q])
            ff = (Cfb @ xf[q] + Df * y_v) if len(Bf) else 0.0
            xf[q] = Af @ xf[q] + Bf * y_v
            th = w0 * t - 2 * math.pi * q / 3
            bsum[q] += (y_v - (yv_all[3] if q < 3 else 0.0)) - buf[q][k % ncyc]
            buf[q][k % ncyc] = y_v - (yv_all[3] if q < 3 else 0.0)
            if k >= ncyc:
                xdc[q] += kdc * bsum[q] / ncyc
            if q == 3 or mode == "gfm":                         # voltage cascade (the N leg always; the phases when grid forming)
                vr = (V0 * math.cos(th) if q < 3 else 0.0) - xdc[q]
                e = vr - (y_v - (yv_all[3] if q < 3 else 0.0))
                iu = Kpv * e + Cv @ xv[q] + Dv * e + ((io[q] - Ceq * V0 * w0 * math.sin(th)) if q < 3 else   # N leg: the 9b cascade
                                                      (-io[0] - io[1] - io[2] if n_ff else 0.0))       # + the phases' load currents
                ir = max(-ilim, min(ilim, iu))
                lim_any |= ir != iu
                vc_ = abs(vq) > vth and q < 3
                xv[q] = Av @ xv[q] + (0.0 if vc_ else Bv * (e + (ir - iu) / Kpv))
                uu = Kp * (ir - y_i) + ff
                if vc_:
                    uu = vq + L * (iod - (y_i + (d[q] - vq) * T / L)) / T
                    vcl_any = True
            else:                                              # grid-following PR per phase
                ir = (Iref[q] * cx(th)).real + (1j * w0 * Ceq * V0 * cx(th)).real
                if abs(Iref[q] + 1j * w0 * Ceq * V0) > ilim:
                    ir *= ilim / abs(Iref[q] + 1j * w0 * Ceq * V0)
                    lim_any = True
                e = ir - y_i
                uu = Kp * e + Cr @ xr[q] + Dr * e + ff
            ip = y_i + (d[q] - vq) * T / L                     # per-sample clamp (section 4b), each leg, on the divider-inverted
            i2 = ip + (uu - vq) * T / L                        # C_f voltage (the section 4c firmware)
            uc = uu + (math.copysign(Ic, i2) - i2) * L / T if abs(i2) > Ic else uu
            u[q] = max(-umax, min(umax, uc))
            if q < 3 and mode == "gfl":
                xr[q] = Ar @ xr[q] + Br * (e + (u[q] - uu) / Kp)
            yvp[q] = y_v
            hist_u[q][k % ncyc], hist_i[q][k % ncyc], hist_f[q][k % ncyc] = u[q], y_i, ff
        k_lim = k if lim_any else k_lim                        # the limit timer runs while the limiter acted within the last cycle
        tl = tl + T if k - k_lim < ncyc else 0.0
        x = pl["Phi"] @ x + pl["Gu"] @ d + ((pl["Gg"] * (vg or V0) * cx(w0 * t)).real if pl["grid"] else 0.0)
        d = u
        vn = x[VCN4]
        rec["t"][k], rec["vn"][k], rec["iN"][k], rec["dn"][k], rec["lim"][k], rec["vcl"][k] = t, vn, x[IN4], d[3], lim_any, vcl_any
        for p, (kv, ki) in enumerate((("va", "ia"), ("vb", "ib"), ("vc", "ic"))):
            rec[kv][k], rec[ki][k] = x[7 * p + 1] - vn, x[7 * p]
        rec["da"][k] = d[0]
        if t_lim and tl > t_lim:
            trip = t
            rec = {key: v[:k + 1] for key, v in rec.items()}
            break
    return rec, trip


def cyc_amp(P, t, v):
    """one-cycle sliding DFT magnitude of v (the fundamental amplitude over the last grid period), pu of V0; NaN for the first cycle"""
    n = int(round(P["fs"] / F0))
    z = v * np.exp(-1j * P["w0"] * t)
    c = np.concatenate([[0], np.cumsum(z)])
    out = np.full(len(v), np.nan)
    out[n - 1:] = np.abs(c[n:] - c[:-n]) * 2 / n / P["V0"]
    return out


def study_four_wire(P, G):
    """review R2-13: the coupled four-wire cases the per-phase averaged model can carry (ph4_run; the DC link a battery at 750 V,
    mode A): (1) a 100 % unbalanced step in grid forming (rated resistive load phase a to N, b and c open), with and without the
    N-leg feed-forward option; (2) a half-wave load step (diode + resistor phase a to N at the declared peak) with the DC-component
    regulators; (3) a line-to-neutral short on phase a from a balanced rated load, held past the 200 ms limit timer (the firmware
    trip), the N leg carrying the return current; (4) GFL -> GFM islanding at SCR 10 with an unbalanced local load (phase a): planned
    (each phase's current = its local load: no exchange) and with balanced 125 kW export (phases b and c rejected); (5) GFM -> GFL:
    the island with the phase-a load closed onto the SCR 10 grid at the permissive edge, then grid following"""
    T, V0, zb, w0 = P["T"], P["V0"], P["zb"], P["w0"]
    rp, lo = P["ripple_pp"] / 2, P["win"]["lo"]
    R_hw = V0 / P["fw4"]["hw_pk_A"]

    def summ(rec, t0, extra=None):
        k0, n = int(round(t0 / T)), int(round(P["fs"] / F0))
        d = dict(t0=t0)
        for p, key in enumerate(("va", "vb", "vc")):
            ref = V0 * np.cos(w0 * rec["t"] - 2 * math.pi * p / 3)
            dev = np.abs(rec[key] - ref)[k0:] / V0
            amp = cyc_amp(P, rec["t"], rec[key])[k0:]
            ok1 = np.nonzero(np.abs(amp - 1) > 0.01)[0]
            d[key] = dict(dev_pk=float(dev.max()), dev10_ms=1e3 * T * float(np.sum(dev > 0.10)), amp_min=float(np.nanmin(amp)),
                          amp_max=float(np.nanmax(amp)), amp1_ms=1e3 * T * float(ok1[-1] + 1) if len(ok1) else 0.0, amp_end=float(amp[-1]),
                          dc_pk=float(np.max(np.abs(np.convolve(rec[key][k0:], np.ones(n) / n, "valid")))) if len(rec["t"]) - k0 > n else None)
        d.update(vn_pk=float(np.max(np.abs(rec["vn"][k0:])) / V0),
                 vn_dc=float(np.max(np.abs(np.convolve(rec["vn"][k0:], np.ones(n) / n, "valid")))) if len(rec["t"]) - k0 > n else None,
                 i_pk={k: float(np.max(np.abs(rec[k]))) + rp for k in ("ia", "ib", "ic", "iN")}, lim_ms=1e3 * T * float(np.sum(rec["lim"])),
                 vcl_ms=1e3 * T * float(np.sum(rec["vcl"])), u_pk=float(np.max(np.abs(rec["da"]))), uN_pk=float(np.max(np.abs(rec["dn"]))))
        d["window"] = max(d["i_pk"].values()) > lo
        return d
    out, t0 = {}, 0.1
    open3, bal = (("open",),) * 3, (("R", zb),) * 3
    for nff in (False, True):
        r, _ = ph4_run(P, G, 0.3, lambda t: (("R", zb) if t >= t0 else ("open",), ("open",), ("open",)), lambda t: "gfm", n_ff=nff)
        out["100 % unbalanced step (phase a rated, b / c open)" + (", N-leg feed-forward option" if nff else "")] = summ(r, t0)
    ncy = int(round(P["fs"] / F0))
    for Tdc in (A("dc_reg_T_s"), 0.2):
        r, _ = ph4_run(P, G, 0.8, lambda t: (("hw", R_hw) if t >= t0 else ("open",), ("open",), ("open",)), lambda t: "gfm", T_dc=Tdc)
        hw = summ(r, t0)
        k0, k1 = int(round(t0 / T)), int(round(0.6 / T))
        ma = np.convolve(r["va"], np.ones(ncy) / ncy, "valid")
        bad = np.nonzero(np.abs(ma[k0:]) > P["fw4"]["half_wave"]["dc_component_limit_V"])[0]
        hw.update(T_dc=Tdc, R_hw=R_hw, LN_pk=float(np.max(np.abs(r["iN"]))) + rp, LN_lim=P["fw4"]["LN_pk_A"], dc_end_V=float(ma[-1]),
                  dc_limit_V=P["fw4"]["half_wave"]["dc_component_limit_V"], t_dc_ms=1e3 * T * (bad[-1] + 1) if len(bad) else 0.0,
                  vn_dc_end_V=float(np.mean(r["vn"][k1:])), I_dc=float(-np.mean(r["iN"][k1:])))
        out["half-wave load step (phase a, %.0f A peak declared), DC regulators T %g s" % (P["fw4"]["hw_pk_A"], Tdc)] = hw
    r, trip = ph4_run(P, G, t0 + 0.3, lambda t: (("short",) if t >= t0 else ("R", zb), ("R", zb), ("R", zb)), lambda t: "gfm", t_lim=A("t_lim_s"))
    sh = summ(r, t0)
    kh = int(round((t0 + 0.05) / T))
    sh.update(trip_ms=1e3 * (trip - t0) if trip else None, ia_hold=float(np.max(np.abs(r["ia"][kh:]))), iN_hold=float(np.max(np.abs(r["iN"][kh:]))),
              vb_amp=float(np.nanmin(cyc_amp(P, r["t"], r["vb"])[kh:])), vc_amp=float(np.nanmin(cyc_amp(P, r["t"], r["vc"])[kh:])))
    out["line-to-neutral short phase a from rated balanced load"] = sh
    t1 = t0 + 0.05
    for name, Ir in (("planned (phase a supplies its load, no exchange)", (V0 / zb, 0j, 0j)), ("125 kW balanced export (b, c rejected)",
                     (P["I_rated"] + 0j,) * 3)):
        r, _ = ph4_run(P, G, t1 + 0.15, lambda t: (("R", zb), ("open",), ("open",)), lambda t: "gfl" if t < t1 - 0.01 else "gfm",
                       grid_of=lambda t: 10.0 if t < t1 else None, I_ref=Ir)
        out["GFL -> GFM islanding at SCR 10, local load phase a, " + name] = summ(r, t1 - 0.01)
    dV, dth = P["sync"][0] / 100, math.radians(P["sync"][1])
    t2 = t0 + 0.05
    r, _ = ph4_run(P, G, t2 + 0.2, lambda t: (("R", zb), ("open",), ("open",)), lambda t: "gfm" if t < t2 + 0.02 else "gfl",
                   grid_of=lambda t: 10.0 if t >= t2 else None, vg=V0 * (1 + dV) * cx(dth))
    out["GFM -> GFL: close onto SCR 10 at the permissive edge, phase-a load, then grid following"] = summ(r, t2)
    return dict(cases=out, R_hw=R_hw, m_A=P["fw4"]["m_A"], dc_reg_T_s=A("dc_reg_T_s"), t_lim_s=A("t_lim_s"))


# ---------------------------------------------------------------------------------------------------------- 7. firmware
ROUNDS = (("IL1_ADC", "IL2_ADC", "IL3_ADC"), ("VC1_ADC", "VC2_ADC", "VC3_ADC"), ("VA_ADC", "VB_ADC", "IB_ADC"),
          ("VG1_ADC", "VG2_ADC", "VG3_ADC"))
SLOW = ("NTC_MUX", "NTC9", "NTC_IN", "RCM_ADC", "VPE_ADC", "VBX_ADC", "IB_H_ADC")


def sampling_plan(P):
    """conversion rounds of three simultaneous samples (one per ADC) from the drawn pin plan; per carrier period: rounds
    1-3 at each of the two update instants, the grid-voltage round once, VA/VB as often as the pin plan's OV-backup
    interval needs, the slow channels once per ms"""
    import itertools
    plan = []
    for rnd in ROUNDS:
        mods = [{x[0] for x in P["pins"][net].split("/") if x[:1] in "ABC" and x[1:2].isdigit()} for net in rnd]
        perm = next((p for p in itertools.permutations("ABC") if all(p[i] in mods[i] for i in range(3))), None)
        plan.append(dict(nets=rnd, pins=[P["pins"][n] for n in rnd], adc=perm))
    tc = 1e6 / P["fsw"]                                         # carrier period, us
    k_va = math.ceil(tc / P["va_max_us"])                       # VA/VB conversions per period (pin plan: <= x us apart)
    n_conv = 2 * 3 * 3 + 3 + 2 * max(0, k_va - 2) + len(SLOW) * tc * 1e-3
    t_conv = P["adc_plan"][1] * P["adc_plan"][2] * P["adc_plan"][3] / 100 / P["adc_plan"][0]
    return dict(rounds=plan, conv_per_period=n_conv, t_conv_us=t_conv, busy_pct=100 * n_conv / 3 * t_conv / tc,
                latency_us=3 * t_conv, all_simultaneous=all(r["adc"] for r in plan), tmux_scan_ms=8 * P["tmux_ms"],
                k_va=k_va, va_interval_us=tc / k_va)


def isr_budget(P, K):
    """operation count of the control interrupt at f_s (estimate; cost model in ASSUME)"""
    nb = 1 + len(K["harm"])
    tasks = [("ADC results, offset and gain: 3 i + 3 v_C + 2 V_dc + I_dc", 18, "gfl"), ("Clarke of current and C_f voltage", 8, "gfl"),
             (f"PR current controller, {nb} biquads x 2 axes + anti-windup", 10 * nb + 4, "gfl"),
             ("SOGI feed-forward 2 axes + re-seed test", 15, "gfl"), ("reference rotation, C_f term, circular limiter", 15, "gfl"),
             ("modulation limit, inverse Clarke, min-max zero sequence, 1/V_dc, dead-time comp.", 31, "gfl"),
             ("PWM compare writes (HRPWM)", 6, "gfl"), ("SRF-PLL with clamps", 15, "gfl"), ("software limits, flags", 15, "gfl"),
             ("per-sample current clamp: 3 phases, prediction, correction (section 4b)", 36, "gfl"),
             ("ride-through state, dip-time limit, PLL freeze (section 4b)", 6, "gfl"),
             ("DC-link PI + DC-current feed-forward", 7, "dc"),
             ("grid forming: P/Q, droop, virtual impedance, voltage PR 2 axes", 34, "gfm"),
             ("VF secondary: magnitude filter, re-seed test (the integrators run in the 1 kHz task)", 8, "gfm"),
             ("four-wire: third axis of the per-phase loops, N-leg cascade (P current + SOGI + voltage PR), N DC offset", 70, "4w")]
    cyc = A("isr_cyc_per_op")
    out = {}
    for mode, keys in (("GFL", ("gfl",)), ("GFL + DC link", ("gfl", "dc")), ("GFM + DC link", ("gfl", "dc", "gfm")),
                       ("GFM + DC link, four-wire", ("gfl", "dc", "gfm", "4w"))):
        ops = sum(t[1] for t in tasks if t[2] in keys)
        c = ops * cyc + A("isr_overhead_cyc")
        out[mode] = dict(ops=ops, cycles=c, us=c / 120.0, pct=100 * c / 120.0 / (1e6 / P["fs"]))
    return tasks, out


SM_STATES = ("IDLE", "PRECHARGE", "SYNC", "GFL", "GFM", "STOP", "FAULT", "SERVICE",
             "AC_TEST", "AC_PRECHARGE", "AC_CLOSE_K2", "AC_CLOSE_K1", "RECTIFY", "DC_MATCH", "AC_STOP", "RETRY_WAIT", "LOCKOUT")  # R2-14
SM_OUT6 = {"IDLE": (0, 0, 0, 0, 0, 0), "PRECHARGE": (0, 0, 1, 0, 0, 0), "SYNC": (1, 0, 0, 1, 0, 0), "GFL": (1, 0, 0, 1, 1, 1),
           "GFM": (1, 0, 0, 1, 1, 1), "STOP": (1, 0, 0, 1, 1, 1), "FAULT": (0, 0, 0, 0, 0, 0), "SERVICE": (0, 0, 0, 0, 0, 0),
           "AC_TEST": (0, 0, 0, 0, 0, 0), "AC_PRECHARGE": (0, 1, 0, 0, 0, 0), "AC_CLOSE_K2": (0, 0, 0, 0, 1, 0),
           "AC_CLOSE_K1": (0, 0, 0, 0, 1, 1), "RECTIFY": (1, 0, 0, 0, 1, 1), "DC_MATCH": (1, 0, 1, 0, 1, 1), "AC_STOP": (1, 0, 0, 0, 1, 1),
           "RETRY_WAIT": (0, 0, 0, 0, 0, 0), "LOCKOUT": (0, 0, 0, 0, 0, 0)}         # PWM, K_ACPRE, K_PRE, K_DC, K_AC2, K_AC1
SM_OUT = {k: (v[0], v[2], v[3], int(v[4] and v[5])) for k, v in SM_OUT6.items()}   # PWM, K_PRE, K_DC, K_AC1+2 (the earlier key)
SM_T = {("IDLE", "start"): "PRECHARGE", ("IDLE", "service"): "SERVICE", ("PRECHARGE", "precharged"): "SYNC",
        ("PRECHARGE", "timeout"): "FAULT", ("PRECHARGE", "stop"): "IDLE", ("SYNC", "synced_grid"): "GFL",
        ("SYNC", "dead_bus"): "GFM", ("SYNC", "relay_fail"): "FAULT", ("SYNC", "stop"): "IDLE",
        ("GFL", "island_cmd"): "GFM", ("GFM", "gfl_cmd"): "GFL", ("GFL", "grid_out"): "STOP", ("GFM", "grid_out"): "GFM",
        ("GFL", "stop"): "STOP", ("GFM", "stop"): "STOP", ("GFL", "limit_timeout"): "STOP", ("GFM", "limit_timeout"): "STOP",
        ("STOP", "stopped"): "IDLE", ("FAULT", "clear"): "IDLE", ("SERVICE", "exit"): "IDLE",
        # AC start (pcs_spec ac_start; grid present, DC side dead) - review R2-14
        ("IDLE", "grid_start"): "AC_TEST", ("AC_TEST", "tested"): "AC_PRECHARGE", ("AC_TEST", "welded"): "LOCKOUT",
        ("AC_PRECHARGE", "ac_precharged"): "AC_CLOSE_K2", ("AC_PRECHARGE", "abort"): "RETRY_WAIT",
        ("AC_CLOSE_K2", "k2_settled"): "AC_CLOSE_K1", ("AC_CLOSE_K2", "no_permissive"): "RETRY_WAIT",
        ("AC_CLOSE_K1", "k1_settled"): "RECTIFY", ("AC_CLOSE_K1", "no_permissive"): "RETRY_WAIT", ("AC_CLOSE_K1", "stuck_open"): "RETRY_WAIT",
        ("RECTIFY", "dc_matched"): "GFL", ("RECTIFY", "dc_mismatch"): "DC_MATCH", ("RECTIFY", "limit_timeout"): "AC_STOP",
        ("DC_MATCH", "precharged"): "GFL", ("DC_MATCH", "timeout"): "AC_STOP", ("AC_STOP", "stopped"): "IDLE",
        ("RETRY_WAIT", "retry"): "AC_TEST", ("RETRY_WAIT", "attempts_out"): "LOCKOUT", ("LOCKOUT", "reset"): "IDLE"}
for _s in ("AC_TEST", "AC_PRECHARGE", "AC_CLOSE_K2", "AC_CLOSE_K1", "RETRY_WAIT"):          # a stop or a lost grid ends the sequence
    SM_T[(_s, "stop")] = SM_T[(_s, "grid_lost")] = "IDLE"
for _s in ("RECTIFY", "DC_MATCH"):          # the bridge runs: its own controlled stop (CONTROLLED STOP holds K_DC closed - the
    SM_T[(_s, "stop")] = SM_T[(_s, "grid_lost")] = "AC_STOP"   # exhaustive check caught that route closing the DC contactor)
SM_EVENTS = sorted({e for _, e in SM_T} | {"hw_trip", "fw_trip", "brownout"})
for _s in SM_STATES:                                         # every state: hardware latch and firmware hazard trip -> FAULT; an aux
    if _s != "LOCKOUT":                                      # brown-out resets the controller (latch set, all off) -> IDLE; the
        SM_T[(_s, "hw_trip")] = SM_T[(_s, "fw_trip")] = "FAULT"                     # lock-out survives both (non-volatile)
        SM_T[(_s, "brownout")] = "IDLE"


def verify_sm():
    """exhaustive check of the transition table over every (state, event) pair: invariants and reachability (the AC-start states of
    review R2-14 included).  Outputs: PWM, K_ACPRE, K_PRE, K_DC, K_AC2, K_AC1"""
    nxt = lambda s, e: SM_T.get((s, e), s)
    O = SM_OUT6
    pairs = [(s, e) for s in SM_STATES for e in SM_EVENTS]
    closes = lambda s, e, j: O[nxt(s, e)][j] and not O[s][j]
    inv = {"I1 hw_trip and fw_trip lead to FAULT from every state but LOCKOUT (which they leave in place)": all(
               nxt(s, "hw_trip") == nxt(s, "fw_trip") == ("LOCKOUT" if s == "LOCKOUT" else "FAULT") for s in SM_STATES),
           "I2 FAULT, LOCKOUT, RETRY_WAIT, IDLE: everything off": all(not any(O[s]) for s in ("FAULT", "LOCKOUT", "RETRY_WAIT", "IDLE")),
           "I3 the AC power path (K_AC1 and K_AC2) forms only out of SYNC on a permissive or out of AC_CLOSE_K2 on 'k2_settled'": all(
               not (O[nxt(s, e)][4] and O[nxt(s, e)][5] and not (O[s][4] and O[s][5])) or (s, e) in
               (("SYNC", "synced_grid"), ("SYNC", "dead_bus"), ("AC_CLOSE_K2", "k2_settled")) for s, e in pairs),
           "I4 the DC contactor closes only out of PRECHARGE / DC_MATCH on 'precharged' or RECTIFY on 'dc_matched'": all(
               not closes(s, e, 3) or (s, e) in (("PRECHARGE", "precharged"), ("DC_MATCH", "precharged"), ("RECTIFY", "dc_matched"))
               for s, e in pairs),
           "I5 PWM only in SYNC, GFL, GFM, STOP, RECTIFY, DC_MATCH, AC_STOP": all(
               O[s][0] == (s in ("SYNC", "GFL", "GFM", "STOP", "RECTIFY", "DC_MATCH", "AC_STOP")) for s in SM_STATES),
           "I6 FAULT is left only by 'clear' (or a brown-out reset), to IDLE": all(
               nxt("FAULT", e) == "FAULT" or (e in ("clear", "brownout") and nxt("FAULT", e) == "IDLE") for e in SM_EVENTS),
           "I9 K_ACPRE only in AC_PRECHARGE, with K_PRE, K_DC, K_AC1 and K_AC2 open": all(
               not O[s][1] or (s == "AC_PRECHARGE" and not any(O[s][j] for j in (2, 3, 4, 5))) for s in SM_STATES),
           "I10 K_ACPRE and K_DC never commanded together": all(not (O[s][1] and O[s][3]) for s in SM_STATES),
           "I11 LOCKOUT is left only by 'reset', to IDLE (trips and brown-outs leave it)": all(
               nxt("LOCKOUT", e) == "LOCKOUT" or (e == "reset" and nxt("LOCKOUT", e) == "IDLE") for e in SM_EVENTS),
           "I12 no transition closes both AC contactors at once (one coil at a time: K2, then K1)": all(
               not (closes(s, e, 4) and closes(s, e, 5)) for s, e in pairs if s not in ("SYNC",)),
           "I13 the AC precharge relay opens before any AC contactor closes": all(
               not ((closes(s, e, 4) or closes(s, e, 5)) and O[nxt(s, e)][1]) for s, e in pairs)}

    def reach(a):
        seen, todo = {a}, [a]
        while todo:
            s = todo.pop()
            for e in SM_EVENTS:
                if nxt(s, e) not in seen:
                    seen.add(nxt(s, e))
                    todo.append(nxt(s, e))
        return seen
    inv["I7 every state reachable from IDLE"] = reach("IDLE") == set(SM_STATES)
    inv["I8 IDLE reachable from every state"] = all("IDLE" in reach(s) for s in SM_STATES)
    return inv


SM_RELAY_T = {"K_ACPRE": (0.02, 0.01), "K_PRE": (0.02, 0.01), "K_B": (0.05, 0.01), "K2": (0.10, 0.05), "K1": (0.10, 0.05)}


SM_FIXES = {"H1": "the relay test of K1 / K2 after the AC precharge, onto the precharged link (as first specified (D-074, before the H1-H5 repairs of D-079) it runs on the dead link: a "
                  "welded contactor turns the test into an unprecharged close)",
            "H2": "at a retry the welded-precharge check accepts a link charged by the earlier attempt if it decays at the bleeder rate "
                  "(never rises) and sits below the tap peak (as first specified (D-074, before the H1-H5 repairs of D-079): 'below 50 V', impossible within minutes - false lock-out)",
            "H3": "the closing permissive also needs the grid inside +/-10 % at the command (as first specified (D-074, before the H1-H5 repairs of D-079) it uses the measured, possibly "
                  "sagging, peak; the contactor closes 100 ms later onto a link the aux has drained)",
            "H4": "the DC contactor permissive VBX >= 1.05 sqrt2 V_LL,meas, the hand-over's rectification threshold (as first specified (D-074, before the H1-H5 repairs of D-079) the K_PRE "
                  "route closes K_B onto a battery below the grid peak: uncontrolled rectification); below it standby and a report",
            "H5": "the attempt counter, its spacing and the lock-out kept across a controller reset and checked before every attempt (as "
                  "specified they live in RAM: an aux brown-out restarts the count)"}


def sm_exec(P, fault=None, at="start", fixes=frozenset(SM_FIXES), vbat=750.0, vll=400.0, t_end=240.0, dt=5e-3):
    """review R2-14: the AC-start sequence (pcs_spec ac_start) executed against a plant at the firmware's slow-task rate dt.
    Plant: the grid (V_LL, a sag or a loss), the battery terminal VBX (0 = dead: absent or behind its own open contactor), the link
    (C_dc, the bleeders, the aux's draw while it feeds from the link - 40 W ASSUMED -, an optional 5 ohm bank short; exact RC
    update), the 6-diode tap (line peak - 2 V_F) through R_tot and K_ACPRE (charges only), K_PRE (220 ohm, either way), K_B, K2 / K1
    with VG / VC (VC = VG once both are actually closed), the body diodes (the link held at the line peak once both are closed),
    the bridge as a rectifier (its DC-link loop ramps the link, ASSUMED 1 kV/s), the aux (fed from the higher of tap and link:
    brown-in / brown-out, start-up and hold-up from aux75_spec); relays with pull-in / release times (SM_RELAY_T, ASSUMED) and
    injected weld / stuck-open.  Firmware 'as first specified (D-074, before the H1-H5 repairs of D-079)' = the sequence and interlocks of pcs_spec ac_start read literally (relay
    test before the AC precharge on the dead link, the welded-precharge check 'link below 50 V' at every attempt, the closing
    permissive on the measured line peak at the command, the K_PRE route: K_PRE then K_B, the attempt counter in RAM); fixes = the
    repairs of SM_FIXES applied (the holes this model found, section 9d).  Faults strike at the entry of state `at` ('start' = before power-up).  Returns the
    outcome, the state trace, the safety breaches and the precharge attempts"""
    a, ax = P["acs"], P["aux"]
    C, R_pre, R_sh = P["Cdc"], 220.0, 5.0
    bi, bo, t_su, t_hu = np.mean(ax["brown_in"]), np.mean(ax["brown_out"]), min(ax["start_s"]), ax["holdup_ms"] * 1e-3
    pol, dvw, n_try, t_sp = a["pol"][1], a["dv"][1], int(a["retry"][0]), a["retry"][1]
    rl = {k: dict(cmd=0, act=0, tc=-1.0, f=None) for k in SM_RELAY_T}
    pl = dict(g=1.0, vbx=0.0, vdc=0.0, short=fault == "short_bank", sag_until=-1.0, sag_g=1.0, back_at=None)
    if fault == "low_battery":
        vll, vbat = 460.0, 600.0
    if fault and fault.startswith("weld:") and at == "start":
        rl[fault[5:]].update(act=1, f="weld")
    if fault and fault.startswith("stuck:"):
        rl[fault[6:]]["f"] = "stuck"
    fw = dict(s="OFF", t=0.0, k=0, att=0, last=-1e9, lock=False, why="", v0=0.0)
    on, t_ok, t_low, pwm = False, 0.0, 0.0, False
    trace, breach, prech, injected, both_was = [], set(), [], set(), False

    def cmd(**kw):
        for k, v in kw.items():
            if rl[k]["cmd"] != v:
                rl[k].update(cmd=v, tc=t)

    def go(s_, why=""):
        fw.update(s=s_, t=0.0, k=0)
        trace.append((round(t, 3), s_, why))
        if why:
            fw["why"] = why
        if fault and at == s_ and (s_ not in injected or fault == "brownouts"):
            injected.add(s_)
            if fault == "grid_lost":
                pl["g"] = 0.0
            elif fault == "grid_lost_5s":
                pl["g"], pl["back_at"] = 0.0, t + 5.0
            elif fault in ("brownout", "brownouts"):
                pl["sag_until"], pl["sag_g"] = t + 0.3, 0.2
            elif fault in ("dc_appears", "low_battery"):
                pl["vbx"] = vbat
            elif fault == "weld:K_B":                          # K_B welded closed, the battery behind it then appears
                rl["K_B"].update(act=1, f="weld")
                pl["vbx"] = vbat
            elif fault.startswith("weld:"):                    # welds on its next opening
                rl[fault[5:]]["f"] = "weld"

    def relay_test(k0):                                        # K2 alone, then K1 alone, VC must stay dead (sub-steps k0..k0+4)
        k, ti = fw["k"] - k0, fw["t"]
        if k == 0:
            cmd(K2=1)
            fw.update(k=k0 + 1, t=0.0)
        elif k == 1 and ti >= 0.25:
            if VC > 0.1 * vll:
                fw["lock"] = True
                go("LOCKOUT", "relay test: K1 welded")
                return None
            cmd(K2=0)
            fw.update(k=k0 + 2, t=0.0)
        elif k == 2 and ti >= 0.1:
            cmd(K1=1)
            fw.update(k=k0 + 3, t=0.0)
        elif k == 3 and ti >= 0.25:
            if VC > 0.1 * vll:
                fw["lock"] = True
                go("LOCKOUT", "relay test: K2 welded")
                return None
            cmd(K1=0)
            fw.update(k=k0 + 4, t=0.0)
        elif k == 4 and ti >= 0.1:
            return True
        return False

    def permissive():
        ok = vdc >= a["close"] * math.sqrt(2) * vg and not rl["K_B"]["cmd"]
        return ok and (vg >= 0.9 * vll if "H3" in fixes else True)    # H3: no closing while the grid is out of its band
    t, out = 0.0, None
    while t < t_end and out is None:
        if pl["back_at"] is not None and t >= pl["back_at"]:
            pl["g"], pl["back_at"] = 1.0, None
        g = pl["sag_g"] if t < pl["sag_until"] else pl["g"]
        vg, tap = vll * g, (math.sqrt(2) * vll * g - 2.0) if g > 0 else 0.0
        for k, r in rl.items():                                # relays: pull-in / release, weld, stuck
            want = r["cmd"] if on else 0
            if r["f"] == "stuck":
                r["act"] = 0
            elif not (r["f"] == "weld" and r["act"]) and r["act"] != want and t - r["tc"] >= SM_RELAY_T[k][0 if want else 1]:
                r["act"] = want
        A_ = {k: r["act"] for k, r in rl.items()}
        vdc = pl["vdc"]
        both = A_["K1"] and A_["K2"] and g > 0
        if both and not both_was and vdc < a["close"] * math.sqrt(2) * vg:
            breach.add("S2 AC contactors closed onto a link below the closing permissive (inrush through the body diodes)")
        both_was = both
        G_, I_ = 1 / (a["tau_bl"] / C), -(40.0 / max(vdc, 1.0) if vdc > tap else 0.0)   # the link: conductances and sources, exact
        if A_["K_ACPRE"] and tap > vdc:
            G_, I_ = G_ + 1 / a["R"], I_ + tap / a["R"]
        if A_["K_PRE"]:
            G_, I_ = G_ + 1 / R_pre, I_ + pl["vbx"] / R_pre
        if pl["short"]:
            G_ += 1 / R_sh
        vinf = I_ / G_
        vdc = vinf + (vdc - vinf) * math.exp(-dt * G_ / C)
        if both:
            vdc = max(vdc, math.sqrt(2) * vg - 2.0)            # body diodes
        if A_["K_B"] and pl["vbx"] > 0:
            vdc = pl["vbx"]
        if pwm and on and both:
            tgt = pl["vbx"] if pl["vbx"] >= pol else 750.0
            vdc += max(-1000.0 * dt, min(1000.0 * dt, tgt - vdc))
            vdc = max(vdc, math.sqrt(2) * vg - 2.0)
        pl["vdc"] = vdc
        VC = vg if both else 0.0
        if A_["K_ACPRE"] and A_["K_B"] and tap > vdc + 1.0:
            breach.add("S1 K_ACPRE and K_B closed with the tap above the link: the precharge resistor charges the battery")
        if A_["K_B"] and both and 0 < pl["vbx"] < 1.05 * math.sqrt(2) * vg:
            breach.add("S3 K_B closed onto a battery below the rectification threshold: uncontrolled rectification")
        vin = max(vdc, tap)                                    # the aux and the controller
        t_ok, t_low = (t_ok + dt if vin >= bi else 0.0), (t_low + dt if vin < bo else 0.0)
        if on and t_low > t_hu:
            on, pwm = False, False
            fw.update(s="OFF", t=0.0, k=0)
            trace.append((round(t, 3), "OFF", "aux brown-out: controller reset"))
            if "H5" not in fixes:
                fw.update(att=0, last=-1e9, lock=False)        # as first specified (D-074, before the H1-H5 repairs of D-079): the counter and the lock-out live in RAM
        if not on and t_ok >= t_su:
            on = True
            go("IDLE", "power-up, latch tripped")
        if on:
            st, ti = fw["s"], fw["t"]
            gl = vg < 0.1 * vll
            if st in ("AC_TEST", "AC_PRECHARGE", "AC_CLOSE_K2", "AC_CLOSE_K1") and gl:
                cmd(K_ACPRE=0, K1=0, K2=0, K_PRE=0, K_B=0)
                go("IDLE", "grid lost")
            elif st in ("RECTIFY", "DC_MATCH") and gl:
                pwm = False
                cmd(K1=0, K2=0, K_PRE=0, K_B=0)
                go("IDLE", "grid lost (AC_STOP)")
            elif st == "IDLE":
                cmd(K_ACPRE=0, K1=0, K2=0, K_PRE=0, K_B=0)
                pwm = False
                if fw["lock"] or ("H5" in fixes and fw["att"] >= n_try):   # H5: the count is checked before every attempt
                    why = fw["why"] if fw["lock"] else f"{n_try} precharge attempts without a completed start (resets in between)"
                    fw["lock"] = True
                    go("LOCKOUT", why)
                elif vg >= 0.85 * vll and pl["vbx"] < pol and t - fw["last"] >= t_sp:
                    fw["v0"] = vdc
                    go("AC_TEST")
                elif vg >= 0.85 * vll and pl["vbx"] >= pol and ti > 0.5:
                    out = "DC-start path (battery live)"
            elif st == "AC_TEST":
                if fw["k"] == 0:
                    dead = vdc < a["weld_V"] and VC <= 0.1 * vll
                    if "H2" in fixes and not dead and VC <= 0.1 * vll:   # a link charged by an earlier attempt: it must decay, never rise
                        if ti >= 2.0:
                            dead = vdc <= fw["v0"] * (1 - 0.002) and vdc < tap - 10.0
                        else:
                            fw["t"] += dt
                            t += dt
                            continue
                    if ti >= 0.2:
                        if not dead:
                            fw["lock"] = True
                            go("LOCKOUT", "link or C_f side live with everything open: a welded K_ACPRE / K_PRE / K_B or both contactors")
                        elif "H1" in fixes:
                            go("AC_PRECHARGE")
                            fw["att"], fw["last"] = fw["att"] + 1, t
                            prech.append(t)
                            cmd(K_ACPRE=1)
                        else:
                            fw.update(k=10, t=0.0)
                else:
                    r_ = relay_test(10)
                    if r_:
                        go("AC_PRECHARGE")
                        fw["att"], fw["last"] = fw["att"] + 1, t
                        prech.append(t)
                        cmd(K_ACPRE=1)
            elif st == "AC_PRECHARGE":
                if ti >= a["t_abort"] and vdc < a["abort"] * tap:
                    cmd(K_ACPRE=0)
                    go("RETRY_WAIT", "precharge abort: shorted bank or open path")
                elif tap > 0 and abs(tap - vdc) <= 10.0:
                    cmd(K_ACPRE=0)
                    go("AC_CLOSE_K2")
                elif ti > 3 * a["t10"]:
                    cmd(K_ACPRE=0)
                    go("RETRY_WAIT", "precharge timeout")
            elif st == "AC_CLOSE_K2":
                k = fw["k"]
                if k == 0 and ti >= 0.05:
                    fw.update(k=10 if "H1" in fixes else 1, t=0.0)    # H1: the relay test onto the precharged link first
                elif k >= 10:
                    r_ = relay_test(10)
                    if r_:
                        fw.update(k=1, t=0.0)
                elif k == 1:
                    if permissive():
                        cmd(K2=1)
                        fw.update(k=2, t=0.0)
                    else:
                        go("RETRY_WAIT", "no closing permissive")
                elif k == 2 and ti >= 0.2:
                    go("AC_CLOSE_K1")
            elif st == "AC_CLOSE_K1":
                if fw["k"] == 0:
                    if permissive():
                        cmd(K1=1)
                        fw.update(k=1, t=0.0)
                    else:
                        cmd(K2=0)
                        go("RETRY_WAIT", "no closing permissive")
                elif ti >= 0.2:
                    if VC >= 0.85 * vg and vg > 0:
                        pwm = True
                        go("RECTIFY")
                    else:
                        cmd(K1=0, K2=0)
                        go("RETRY_WAIT", "contactor stuck open (VC does not follow VG)")
            elif st == "RECTIFY":
                vb, floor = pl["vbx"], math.sqrt(2) * vg
                if vb >= pol:
                    ok = vb >= 1.05 * floor
                    if "H4" in fixes and not ok and ti > 0.5:
                        out = "standby: battery below the grid peak, no DC connection (reported)"
                    elif abs(vdc - vb) <= dvw and (ok or "H4" not in fixes):
                        cmd(K_B=1)
                        go("RUN")
                    elif ti > 2.0 and "H4" not in fixes:
                        go("DC_MATCH")
                        cmd(K_PRE=1)
                elif ti > 2.0 and abs(vdc - 750.0) < 5.0:
                    out = "standby on the grid (no battery)"
            elif st == "DC_MATCH":
                if ti >= 1.0:
                    cmd(K_PRE=0, K_B=1)
                    go("RUN", "K_PRE then K_B (as first specified (D-074, before the H1-H5 repairs of D-079))")
            elif st == "RETRY_WAIT":
                cmd(K_ACPRE=0, K1=0, K2=0, K_PRE=0, K_B=0)
                pwm = False
                if fw["att"] >= n_try:
                    fw["lock"] = True
                    go("LOCKOUT", fw["why"] + f" ({n_try} attempts)")
                elif t - fw["last"] >= t_sp:
                    go("IDLE")
            elif st == "LOCKOUT":
                cmd(K_ACPRE=0, K1=0, K2=0, K_PRE=0, K_B=0)
                out = "LOCKOUT: " + fw["why"]
            elif st == "RUN":
                if fw["t"] > 0.3:
                    out = "running (DC contactor closed)"
            fw["t"] += dt
        elif t > 60.0 and g == 0:
            out = "dark: grid absent, the aux down"
        t += dt
    if any(sum(1 for x in prech if y <= x < y + 600.0) > n_try for y in prech):
        breach.add(f"S4 more than {n_try} precharge attempts within 10 min")
    return dict(outcome=out or f"no end within {t_end:g} s (state {fw['s']})", trace=trace, breach=sorted(breach), attempts=len(prech),
                t_end=t, fault=fault, at=at, fixes=sorted(fixes))


def study_sm(P):
    """review R2-14: the coverage of the executable AC-start sequence - every fault at every state where it can strike, for the
    sequence as first specified (D-074, before the H1-H5 repairs of D-079) in pcs_spec and with the holes closed; the table: fault x state -> outcome (and breaches)"""
    states = ("AC_TEST", "AC_PRECHARGE", "AC_CLOSE_K2", "AC_CLOSE_K1", "RECTIFY")
    plan = [("none (battery appears at RECTIFY)", "dc_appears", ("RECTIFY",)), ("none (no battery)", None, ("start",))]
    plan += [(n, f, states) for n, f in (("grid lost", "grid_lost"), ("grid lost for 5 s", "grid_lost_5s"),
                                          ("DC appears (battery 750 V)", "dc_appears"), ("aux brown-out (0.3 s at 0.2 pu)", "brownout"))]
    plan += [("K_ACPRE welded", "weld:K_ACPRE", ("start", "AC_CLOSE_K2")), ("K_ACPRE stuck open", "stuck:K_ACPRE", ("start",)),
             ("K1 welded", "weld:K1", ("start",)), ("K2 welded", "weld:K2", ("start",)), ("K1 stuck open", "stuck:K1", ("start",)),
             ("K2 stuck open", "stuck:K2", ("start",)), ("K_B welded, the battery then appears", "weld:K_B", ("AC_PRECHARGE", "RECTIFY")),
             ("DC-link bank shorted", "short_bank", ("start",)), ("aux brown-out at every precharge entry", "brownouts", ("AC_PRECHARGE",)),
             ("battery below the grid peak (600 V, 460 V grid)", "low_battery", ("AC_CLOSE_K1",))]
    rows = []
    for name, f, ats in plan:
        for at_ in ats:
            r = {v: sm_exec(P, f, at_, fixes=frozenset(SM_FIXES) if v else frozenset()) for v in (False, True)}
            rows.append(dict(fault=name, at=at_, spec=r[False]["outcome"], fixed=r[True]["outcome"], breach_spec=r[False]["breach"],
                             breach_fixed=r[True]["breach"], att_spec=r[False]["attempts"], att_fixed=r[True]["attempts"]))
    h5 = sm_exec(P, "brownouts", "AC_PRECHARGE", fixes=frozenset(SM_FIXES) - {"H5"})       # H5 alone (H2 masks it as first specified (D-074, before the H1-H5 repairs of D-079))
    rows.append(dict(fault="aux brown-out at every precharge entry, all repairs but H5", at="AC_PRECHARGE", spec=h5["outcome"],
                     fixed=rows[-2]["fixed"], breach_spec=h5["breach"], breach_fixed=rows[-2]["breach_fixed"], att_spec=h5["attempts"],
                     att_fixed=rows[-2]["att_fixed"]))
    return dict(rows=rows, states=states, relay_times_s=SM_RELAY_T, fixes=SM_FIXES,
                holes=sorted({b for r in rows for b in r["breach_spec"]} - {b for r in rows for b in r["breach_fixed"]}),
                residual=sorted({b for r in rows for b in r["breach_fixed"]}))


def sm_table(P, G):
    """state | entry condition | exit | outputs | measured quantities | hardware trips behind it (from the drawn boards)"""
    w, c, ov, sy = P["win"], P["cmpss"], P["ov_dc"], P["sync"]
    hw_run = (f"phase window +/-{w['nom']:.0f} A ({w['lo']:.0f}-{w['hi']:.0f} A, {w['t_us']:.1f} us) + CMPSS {c['lo']:.0f}-"
              f"{c['hi']:.0f} A, DESAT per channel, DC OV {ov[1]:.0f}-{ov[2]:.0f} V, OT, heartbeat, ENABLE, RDY")
    return [("IDLE", "power-up (latch set), stop finished, fault cleared", "start: IMD passed, polarity OK, no latch, T in range",
             "all off", "V_DC terminal, V_link, VPE, NTC", "latch power-up tripped, ENABLE, heartbeat"),
            ("DC PRECHARGE", "start", f"|V_link - V_bat| <= 10 V -> K_DC closes, K_PRE opens; timeout {3 * P['t_pre']:.1f} s "
             "(3 x the PCS-PWR 10 V time, ASSUMED factor) -> FAULT",
             "K_PRE", "V_link (VB), V_terminal (VBX), I_DC", "hardware precharge-dV and polarity interlocks, port OC, DC OV"),
            ("AC SYNCHRONISATION", "precharged", f"grid: |dV| <= {sy[0]:.0f} %, |dtheta| <= {sy[1]:.0f} deg, |df| <= 0.1 Hz, "
             "relay test (each contactor alone) passed" + (f", {P['map_close']}" if P["map_close"] else "") +
             " -> K_AC1+K_AC2 close -> GFL; dead bus (VG < 10 %) -> GFM",
             "PWM (GFM on C_f, contactors open), K_DC", "VC1-3, VG1-3 (both sides of the contactors), PLL on VG", hw_run),
            ("GFL RUN", "synced_grid / gfl_cmd (bumpless)", "stop, grid out of window or islanding -> STOP; limit > 200 ms -> STOP; "
             "island_cmd -> GFM", "PWM, K_DC, K_AC", "IL1-3, VC1-3, VG1-3, V_link, I_DC, RCM", hw_run + ", port OC/hold-off"),
            ("GFM RUN", "dead_bus / island_cmd (bumpless)", "gfl_cmd when grid connected; stop; limit > 200 ms -> STOP",
             "PWM, K_DC, K_AC", "same + P/Q from v_C and i_1 - j w C v_C", hw_run + ", port OC/hold-off"),
            ("CONTROLLED STOP", "stop / grid_out / limit_timeout", "current ramped to 0, K_AC open at zero current, PWM off, "
             "K_DC open below its breaking current -> IDLE", "PWM until zero current", "IL1-3, I_DC", hw_run),
            ("FAULT", "any hardware latch source or firmware hazard trip", "clear: every source inactive, cause logged, PWM "
             "commands low -> IDLE", "all off (HOLD may keep K_DC)", "latch status, FLT_N, RDY, OC_N, OVT_N",
             "latch holds PWM, EN, K_PRE, K_AC1/2 low (HEALTHY gating); K_DC kept only by HOLD"),
            ("SERVICE", "service command in IDLE", "exit -> IDLE", "all off; IMD switches, trip-path self-test",
             "everything (calibration, self-test)", "latch held, ENABLE"),
            ("AC_TEST (AC start, section 9d)", "grid_start: grid present, DC side dead, the attempt count and spacing allow",
             f"welded-precharge check: link < {P['acs']['weld_V']:.0f} V or (a retry, H2) decaying at the bleeder rate below the tap peak, "
             "VC dead -> AC_PRECHARGE; else LOCKOUT", "all off", "V_link, VG, VC", "latch tripped at power-up"),
            ("AC_PRECHARGE", "tested", f"|tap - V_link| <= 10 V -> AC_CLOSE_K2; < {100 * P['acs']['abort']:.0f} % of the tap at "
             f"{P['acs']['t_abort'] * 1e3:.0f} ms or > 3 x {P['acs']['t10']:.2f} s -> RETRY_WAIT", "K_ACPRE", "V_link, the tap (VG)",
             "latch drops K_ACPRE"),
            ("AC_CLOSE_K2 / K1", "ac_precharged", "K_ACPRE opens; relay test K2 alone / K1 alone onto the precharged link (H1); K2 then K1, each "
             f"on V_link >= {P['acs']['close']:g} x sqrt2 V_LL,meas with VG inside +/-10 % (H3); VC follows VG -> RECTIFY, else RETRY_WAIT",
             "K_AC2, then K_AC1", "VG, VC, V_link", "latch drops both"),
            ("RECTIFY", "k1_settled", "the bridge raises the link to VBX (or 750 V); K_B closes on |dV| <= the window and VBX >= 1.05 sqrt2 "
             "V_LL,meas (H4) -> GFL; below it standby with a report", "PWM, K_AC", "V_link, VBX, IL1-3", "every running trip"),
            ("RETRY_WAIT / LOCKOUT", "abort, no permissive, stuck contactor", f">= {P['acs']['retry'][1]:.0f} s -> AC_TEST, at most "
             f"{P['acs']['retry'][0]:.0f} attempts, counted across resets (H5), then LOCKOUT until a service reset", "all off",
             "-", "latch")]


def anti_islanding(P):
    return [("passive", "U/f windows on VG1-3 (RMS over one cycle, 1 kHz task) and ROCOF from the PLL integrator filtered over "
             "100-200 ms; vector shift from the PLL angle error", "VG1-3, VC1-3", "non-detection zone when P and Q match the "
             "local load"),
            ("active", "Sandia frequency shift: a chopping fraction cf = cf0 + k (f - f0) on the current reference "
             "(GFL only); a Q perturbation is the alternative", "PLL frequency, IL1-3", "detection 0.2-2 s (from memory), "
             "small THD cost; not simulated, compliance (IEC 62116 test) not claimed"),
            ("not possible", "impedance measurement by injection would need the grid current: there is no grid-side "
             "current sensor (i_2 is only estimated as i_1 - C_f dv_C/dt)", "-", "-")]


def vdc_min(P):
    """lowest DC-link voltage that keeps |u| inside the modulation limit at rated current, grid at +10 %, any PF"""
    out = {}
    for scr in (None, 5.0):
        pl = plant(P, "nom", "grid", scr)
        M = np.linalg.inv(1j * P["w0"] * np.eye(8) - pl["A"])
        xu, xg = M @ pl["Bu"], M @ pl["Bg"] * 1.1 * P["V0"]
        u = max(abs((P["I_rated"] * cx(a) - xg[3]) / xu[3]) for a in np.radians(np.arange(0, 360, 15)))
        out[scr] = u / P["u_frac"]
    return out


# ---------------------------------------------------------------------------------------------------------- 8. hardware
def hardware(P, R):
    """the drawn boards, and the figures stated about them, against the loop: every mismatch found.  Verdicts are computed
    from the results: MISMATCH = the drawn or stated value disagrees with the study; NOTE = no conflict, a consequence to
    act on; OK = checked and consistent"""
    w, sen, pl, hw = P["win"], A("sensor"), R["plan"], []
    nm = lambda d: f"{d['case']} ({scr_name(d['scr'])})"
    peaks = [(f"{d['depth']:g} pu dip at 125 kW ({scr_name(d['scr'])}) onset", d["pk_on"]) for d in R["rt"]["rows"]]
    peaks += [(nm(d), d["pk"]) for d in R["steps"] if d["vdc"] == 750.0]
    peaks += [("GFM terminal short from rated load", R["gfm"]["short"]["pk"])]
    over = sorted(((n, p) for n, p in peaks if p > w["lo"]), key=lambda x: -x[1])
    below = max(((n, p) for n, p in peaks if p <= w["lo"]), key=lambda x: x[1])
    after = max(max(d["pk_dip"], d["pk_rec"]) for d in R["rt"]["rows"])
    old = max((d for d in R["faults"] if d["ff"] == "sogi"), key=lambda d: max(d["pk_dip"], d["pk_rec"]))
    hw.append(("PCS-CTL phase window", f"+/-{w['nom']:.0f} A nominal, worst band {w['lo']:.0f}-{w['hi']:.0f} A (basis "
               f"{P['win_basis'][1]:.3f} x the normal peak {P['win_basis'][2]:.1f} A)",
               "with the firmware limiter of section 4b (ride-through state, PLL freeze, per-sample clamp) every in-dip and recovery "
               f"peak is <= {after:.0f} A; " + (("peaks above the band's low edge (averaged + ripple/2): " + "; ".join(
                   f"{n} {p:.0f} A ({'above the nominal threshold: trips' if p > w['nom'] else 'may trip'})" for n, p in over) +
                f"; highest peak below the band: {below[0]} {below[1]:.0f} A") if over else
               f"every studied peak below the low edge (highest: {below[0]} {below[1]:.0f} A)") +
               f"; without the ride-through state (section 4): {nm(old)} {max(old['pk_dip'], old['pk_rec']):.0f} A",
               (f"OK (the onset measure of section 4c holds every onset at <= {R['rtr']['worst_onset']:.0f} A; the D-077 limiter alone "
                "would trip at a stiff connection - the fallback derating)" if R["rtr"]["adopted"] else
                "MISMATCH (stiff-grid onset: firmware derating, section 4c)") if over else "OK"))
    k1 = next(r for r in R["sweep"] if r["fc"] == 1000.0)
    thd1 = max(r["thd_pct"] for r in R["thd"] if r["ctrl"].startswith("1 kHz") and r["dt"].startswith("compensated, id"))
    bw = {r["scr"]: r["f_bw"] for r in R["table"] if r["damp"] == "passive" and r["cn"] == "nom"}
    kept = sorted(set().union(*(r["harm"] for r in R["sweep"] if r["ok"])))
    never = [h for h in A("harmonics") if h not in kept]
    hw.append(("Current-loop bandwidth statement (risk register E1, guide 09, pcs_spec hand-over)", "'the filter allows a "
               "current-loop bandwidth of up to 1 kHz (below half the lowest resonance)'; resonant terms h5/h7/h11/h13",
               f"crossovers meeting the rule (stiff-grid parameter) {R['band'][0]:.0f}-{R['band'][1]:.0f} Hz, delay-only limit "
               f"{R['f_delay']:.0f} Hz: 1 kHz is the lower edge, not the ceiling; the weak grid lowers the loop (closed-loop "
               f"-3 dB at the design: stiff {bw[None]:.0f} Hz, SCR 5 {bw[5.0]:.0f} Hz); at 1 kHz the harmonic terms kept are "
               f"{', '.join(map(str, k1['harm'])) or 'none'} and THDi reaches {thd1:.1f} % (estimate); "
               + (f"h{'/h'.join(map(str, never))} fit at no crossover that meets the rule" if never else "every named term fits"),
               "MISMATCH" if R["band"][1] > 1000.0 or never else "OK"))
    hw.append(("PCS-CTL ADC plan (design check)", f"'{P['adc_plan'][0]:.0f} conversions per {P['adc_plan'][1]} us on "
               f"{P['adc_plan'][2]:.0f} ADCs = {P['adc_plan'][3]:.0f} %', the PV control spec's plan",
               f"inverter: {pl['conv_per_period']:.1f} conversions per {1e6 / P['fsw']:.2f} us, {pl['busy_pct']:.0f} % busy, last "
               f"loop input {pl['latency_us']:.2f} us after the trigger; VA/VB {pl['k_va']} x per period (every "
               f"{pl['va_interval_us']:.1f} us) for the pin plan's <= {P['va_max_us']:.0f} us OV backup; IL1-3, VC1-3, VA/VB/IB "
               f"and VG1-3 each convert simultaneously on A/B/C: {'yes' if pl['all_simultaneous'] else 'NO'}",
               "OK" if abs(P["adc_plan"][0] - pl["conv_per_period"]) < 1 else "MISMATCH (text; the pins and ADCs support the plan)"))
    hw.append(("TMUX1208 temperature multiplexer", f"{P['tmux_ms']:.0f} ms settling per address, NTC1-8 on one ADC pin",
               f"a full scan takes >= {pl['tmux_scan_ms']:.0f} ms: no loop uses a temperature; derating and the 1 s firmware "
               "layer are slower; the 200 ms overload rests on the current-based junction model; the hardware OT comparators "
               "sit on the NTC nets, not behind the multiplexer", "OK"))
    dts = list(dict.fromkeys(x["dt"] for x in R["thd"]))
    thd = {dt: max(x["thd_pct"] for x in R["thd"] if x["ctrl"] == R["thd_label"] and x["dt"] == dt) for dt in dts}
    thd0 = {dt: max(x["thd_pct"] for x in R["thd"] if x["ctrl"].startswith("design without") and x["dt"] == dt) for dt in dts}
    hw.append(("Dead time at the gates vs THDi < 3 % at rated current", f"{P['dt_gate_ns'][0]:.0f}-{P['dt_gate_ns'][1]:.0f} ns "
               "(PCS-PWR stretch)", "THDi estimate, worst SCR, 900 V, with the harmonic terms: " +
               "; ".join(f"{k} {v:.1f} %" for k, v in thd.items()) + "; without them: " +
               "; ".join(f"{k} {v:.1f} %" for k, v in thd0.items()),
               "OK (the dead time matters only without the harmonic terms)" if max(thd.values()) < 3.0 else "MISMATCH"))
    hw.append(("Dead band in the PCS-CTL firmware table", f"{P['dt_fw_ctl_ns']:.0f} ns",
               f"pcs_spec hand-over {P['dt_spec_ns']:.0f} ns; PCS-PWR stretches any value below {P['dt_floor_ns']:.0f} ns to "
               f"{P['dt_gate_ns'][0]:.0f}-{P['dt_gate_ns'][1]:.0f} ns, so neither reaches the gates (firmware rule in section 9)",
               "MISMATCH (text)" if P["dt_fw_ctl_ns"] != P["dt_spec_ns"] else "OK"))
    ad = R["ad_scan"]
    hw.append(("C_f voltage divider pole (VC1-3, VG1-3)", f"{P['f_vdiv'] / 1e3:.1f} kHz", "capacitor-current active damping "
               f"from dv_C/dt {'meets the rule at some gain' if any(s['ok'] for s in ad) else 'fails at the stiff grid for every gain tried'}"
               f" (best max |pole| {min(s['rho'] for s in ad):.3f}): the R_d-C_d branch is load-bearing; a 30 kHz divider would move "
               f"the stiff 0.1 pu dip peak from {R['divider'][P['f_vdiv']]:.0f} to {R['divider'][30e3]:.0f} A",
               "NOTE (keep the passive branch fitted)"))
    hw.append(("PCS-CTL trip table, CMPSS row", f"'requirement {P['cmpss_req'][0]}-{P['cmpss_req'][1]} A'",
               f"a PV cell-current band left in the inverter's table (the CMPSS backup sits at {P['cmpss']['lo']:.0f}-"
               f"{P['cmpss']['hi']:.0f} A)", "MISMATCH (text)" if P["cmpss_req"][1] < P["cmpss"]["lo"] / 2 else "OK"))
    route = P["routes"]["K_A_M"]
    hw.append(("Pin plan route of K_A_M (AC contactor 1)", f"gen/data/pcs_ctrl_pin_plan.csv: '{route}'",
               "design check and gen/pcs_ctrl.py: HEALTHY only (D-062); FAULT in the state machine relies on it",
               "MISMATCH (text)" if "HOLD" in route else "OK"))
    sel = lambda case, mult: next(d for d in R["dc"]["tsim"] if d["scr"] == 5.0 and d["t_ramp_ms"] == 0.0 and d["case"] == case
                                  and abs(d["Cdc_mF"] / (1e3 * P["Cdc"]) - mult) < 0.01)
    st1, tr1, st4, tr4 = (sel(c, m) for m in (1.0, 4.0) for c in ("DC/DC load 0 -> 125 kW", "DC/DC load 125 kW trips"))
    vtxt = lambda d: f"{d['vmin']:.0f} V min" if d["recovered"] else "bus collapse"
    bad = (not st1["recovered"]) or tr1["vmax"] > P["ov_dc"][1] or P["c_pcs_dab"] > 2 * P["Cdc"]
    hw.append(("DC-link capacitance seen by the DC/DC", f"{P['Cdc'] * 1e3:.2f} mF ({P['Cdc_src']}); sim/dab_control.py assumes "
               f"C_pcs {P['c_pcs_dab'] * 1e3:.0f} mF", f"SCR 5, 750 V, steps: 125 kW DC/DC load -> {vtxt(st1)}; 125 kW DC/DC "
               f"trip -> {tr1['vmax']:.0f} V max (OV band {P['ov_dc'][1]:.0f}-{P['ov_dc'][2]:.0f} V); with 4 x C_dc: {vtxt(st4)}, "
               f"{tr4['vmax']:.0f} V; with the protection in the loop (section 6b) the DC over-voltage trips and the bus is left at "
               f"{max(d['vdc_end'] for d in R['dct']['rows'] if d['trip']):.0f} V; firmware: CV-mode DC/DC power at weak grids "
               + ", ".join(f"{scr_name(d['scr'])} {d['P_max_kW']:.1f} kW" for d in R["dct"]["derating"]),
               "MISMATCH (cross-study; DC/DC power ramp and a coordinated trip needed)" if bad else "OK"))
    vm = R["vdc_min"]
    hw.append(("Lowest full-load DC voltage (REQUIREMENTS AC-02: full load 600-900 V)", "600 V", f"rated current at any PF with "
               f"the grid at +10 % needs {vm[None]:.0f} V (stiff) / {vm[5.0]:.0f} V (SCR 5) ({P['u_src']})" +
               (f"; pcs_spec operating map {P['map_vdc440']:.0f} V (grid impedance not counted)" if P["map_vdc440"] else "") +
               "; the +10 % grid step at 600 V loses current control",
               "MISMATCH (requirement vs map; firmware rule)" if min(vm.values()) > 600.0 else "OK"))
    env = [r for r in R["table"] if r["cn"] == "env" and r["damp"] == "passive"]
    env_ok = all(r["ok"] for r in env)
    hw.append(("L1/L2 L(I) envelope of the inductor specification", f"down to {P['L1_env'] * 1e6:.0f} / {P['L2_env'] * 1e6:.1f} uH"
               " at the trip current", f"a part at the envelope {'still meets' if env_ok else 'FAILS'} the rule (worst PM "
               f"{min(r['pm'] for r in env):.0f} deg, GM {min(min(r['gm'], r['gm_lo']) for r in env):.1f} dB); the designed amorphous "
               f"parts stay >= {P['L1_lo'] * 1e6:.0f} / {P['L2_lo'] * 1e6:.1f} uH", "OK" if env_ok else "MISMATCH"))
    k = P["G_chk"] / sen["G_mV_per_A"]
    t_off = P["trip_t"][0] - P["trip_t"][1] + sen["t_step_us"]
    hw.append(("Phase-current sensor gain vs the PCS-CTL window ladder", f"ladder computed for {P['G_chk']:.1f} mV/A (PCS-CTL "
               f"assumption); frozen {sen['part']} {sen['G_mV_per_A']} mV/A, linear +/-{sen['linear_A']:.0f} A (ASSUMED, section 1)",
               f"the drawn ladder would trip at about {w['lo'] * k:.0f}-{w['hi'] * k:.0f} A (x {k:.2f}), above the "
               f"{P['win_top_req']:.0f} A top the window must keep, gates off at about {w['hi'] * k + P['didt'] * t_off:.0f} A "
               f"against the {P['trip_t'][3]:.0f} A limit; the CMPSS backup is a DAC value (firmware)",
               "MISMATCH (re-value the ladder in gen/pcs_ctrl.py, or a 4.0 mV/A part)" if abs(k - 1) > 0.02 else "OK"))
    slow_ok = all(r["ok"] for r in R["table"] if r["damp"] == "passive" and r["cn"] == "slow")
    i_off = P["trip_t"][2] + P["didt"] * (t_off - P["trip_t"][0])
    hw.append(("Phase-current sensor response", f"PCS-CTL assumed {P['t_sensor_chk'] * 1e6:.1f} us (chain {P['ti_chk'] * 1e6:.2f} us)"
               f"; frozen part {sen['t_step_us']:g} us max step", f"loop: chain {P['ti'] * 1e6:.2f} us nominal; the rule "
               f"{'holds' if slow_ok else 'FAILS'} at {A('slow_sensor'):g} x that (corner 'slow'); trip: gates off "
               f"{t_off:.2f} us after the crossing -> {i_off:.0f} A <= {P['trip_t'][3]:.0f} A (at the drawn gain)",
               "OK" if slow_ok and i_off <= P["trip_t"][3] else "MISMATCH"))
    vb_ = R["vfb"]["steps"]
    des_ = [v for k, v in vb_.items() if k.startswith("design, single")]
    hw.append(("Grid-side current", "not sensed (i_2 estimated as i_1 - C_f dv_C/dt)", "grid forming off-grid with the load-current "
               "feed-forward from that estimate and the over-voltage deadbeat (section 7c): full-load steps "
               f"{min(d['v_min'] for d in des_):.2f} / {max(d['v_max'] for d in des_):.2f} pu at the worst corner, within 10 % after <= "
               f"{max(d['t_v10_ms'] for d in des_):.1f} ms - inside the declared envelope; the first-millisecond excursion is the 2 % C_f "
               "filter's (C_f charges at about 4 V/us from the rated L1 current during the 1.5 T_s and divider latency), which a grid-side "
               "sensor would not remove", "OK (envelope declared, section 7c; bench item)"))
    return hw


# ---------------------------------------------------------------------------------------------------------- outputs
def scr_name(s):
    return "stiff" if s is None else (s if isinstance(s, str) else f"SCR {s:g}")


def plots(P, G, R):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    f = fgrid(P)
    fig, ax = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    for scr in A("scr"):
        L = loop(P, G["K"], plant(P, "nom", "grid", scr), f)
        ax[0].semilogx(f, 20 * np.log10(np.abs(L)), label=scr_name(scr))
        ax[1].semilogx(f, np.degrees(np.angle(L)), label=scr_name(scr))
    for a in ax:
        a.axvline(P["fs"] / 6, color="k", ls=":", lw=1)
        a.grid(True, which="both", alpha=0.3)
    ax[0].axhline(0, color="k", lw=0.8)
    ax[0].set_ylim(-40, 60)
    ax[0].set_ylabel("|L| dB")
    ax[1].set_ylabel("angle L deg")
    ax[1].set_xlabel("Hz (dotted: f_s/6)")
    ax[0].legend()
    ax[0].set_title(f"Current loop gain at the modulator, f_c {G['K']['fc']:.0f} Hz, nominal corner, passive branch (CALCULATED)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "bode_scr.png"), dpi=110)
    plt.close(fig)
    fig, ax = plt.subplots(2, 2, figsize=(11, 7))
    for a, case in zip(ax.ravel(), ("ref step 0 -> rated", "grid -10 %", "grid +10 %", "phase jump 30 deg")):
        for d in R["steps"]:
            if d["case"] == case and d["rec"] is not None:
                r, ls = d["rec"], "-" if d["scr"] is None else "--"
                ip = pll_frame(r)
                a.plot(1e3 * r["t"], ip.real, ls, label=f"i_d {scr_name(d['scr'])}")
                a.plot(1e3 * r["t"], ip.imag, ls, label=f"i_q {scr_name(d['scr'])}")
        a.set_title(case)
        a.set_xlabel("ms")
        a.set_ylabel("A (PLL frame, peak)")
        a.grid(alpha=0.3)
    ax[0, 0].legend(fontsize=8)
    fig.suptitle("Grid-following current loop, 750 V (SIMULATED, sampled averaged model)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "steps.png"), dpi=110)
    plt.close(fig)
    fig, ax = plt.subplots(1, 3, figsize=(14, 4.2), sharey=True)
    for a, case in zip(ax, ("0.5 pu dip", "0.1 pu dip", "unbalanced 0.6/0.3 pu")):
        for d in R["faults"]:
            if d["case"] == case:
                r = d["rec"]
                ab = r["i1"] * np.exp(1j * P["w0"] * r["t"])
                env = np.max(np.abs(np.array([(ab * cx(-2 * math.pi * p / 3)).real for p in range(3)])), axis=0)
                a.plot(1e3 * r["t"], env + P["ripple_pp"] / 2, label=scr_name(d["scr"]))
        for y, lab in ((P["I_lim"], "limit"), (P["win"]["lo"], "window low"), (P["win"]["nom"], "window nom")):
            a.axhline(y, ls=":", color="k", lw=1)
            a.text(1, y + 4, lab, fontsize=7)
        a.set_title(case + ", 125 kW held")
        a.set_xlabel("ms")
        a.grid(alpha=0.3)
    ax[0].set_ylabel("largest phase current + ripple/2, A")
    ax[0].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "faults.png"), dpi=110)
    plt.close(fig)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for s in R["pll"]["sweep"]:
        if s["P"] > 0:
            fp = [r["f_pll"] for r in s["rows"]]
            ax[0].semilogx(fp, [r["pm"] for r in s["rows"]], "o-", label=scr_name(s["scr"]))
            ax[1].semilogx(fp, [min(r["gm"], 60) for r in s["rows"]], "o-", label=scr_name(s["scr"]))
    for a, y, lab in ((ax[0], RULE_PM, "PM deg"), (ax[1], RULE_GM, "GM dB (capped 60)")):
        a.axhline(y, color="r", ls=":")
        a.axvline(G["f_pll"], color="k", ls="--", lw=1)
        a.set_xlabel("PLL bandwidth, Hz")
        a.set_ylabel(lab)
        a.grid(True, which="both", alpha=0.3)
    ax[0].legend()
    fig.suptitle("PLL loop margins vs bandwidth at 125 kW export (loop broken at the PLL frequency; CALCULATED)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "pll_weak_grid.png"), dpi=110)
    plt.close(fig)
    fig, ax = plt.subplots(1, 3, figsize=(14, 4.2))
    r = R["gfm"]["short"]["rec"]
    ax[0].plot(1e3 * r["t"], np.abs(r["i1"]) + P["ripple_pp"] / 2, label="|i_1| + ripple/2")
    ax[0].plot(1e3 * r["t"], np.abs(r["i2"]), label="|i_2| (L2, not sensed)")
    for y in (P["I_lim"], P["win"]["lo"]):
        ax[0].axhline(y, ls=":", color="k")
    ax[0].set_title("GFM off-grid terminal short, 150 ms")
    ax[0].legend(fontsize=8)
    r = R["gfm"]["load_step"]["rec"]
    ax[1].plot(1e3 * r["t"], np.abs(r["vc"]) / P["V0"])
    ax[1].set_title("GFM off-grid 0 -> rated R load: |v_C| pu")
    for d in R["gfm"]["sync"]:
        ax[2].plot(1e3 * d["rec"]["t"], np.abs(d["rec"]["i1"]), label=scr_name(d["scr"]))
    ax[2].set_title(f"close at |dV| {P['sync'][0]:.0f} %, {P['sync'][1]:.0f} deg, then GFM -> GFL")
    ax[2].legend()
    for a in ax:
        a.set_xlabel("ms")
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "gfm.png"), dpi=110)
    plt.close(fig)
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
    for name, r in R["sec"]["rec"].items():
        ax[0].plot(r["t"], np.abs(r["vc"]) / P["V0"], label=name)
        ax[1].plot(r["t"], np.gradient(np.unwrap(r["thv"]), P["T"]) / (2 * math.pi), label=name)
    for a, band in ((ax[0], (0.99, 1.01)), (ax[1], (-0.002 * F0, 0.002 * F0))):
        for y in band:
            a.axhline(y, ls=":", color="k", lw=1)
        a.set_xlabel("s")
        a.grid(alpha=0.3)
        a.legend(fontsize=8)
    ax[0].set_ylim(0.3, 1.3)
    ax[0].set_ylabel("|v_C| pu (clipped at 1.3)")
    ax[0].set_title(f"VF steps, secondary T {R['sec']['T_s']:g} s (SIMULATED)")
    ax[1].set_ylabel("generator frequency - 50 Hz, Hz")
    ax[1].set_title("frequency (+/-0.2 % dotted)")
    for scr in R["rt"]["grids"]:
        rr = [d for d in R["rt"]["rows"] if d["scr"] == scr]
        ax[2].plot([d["depth"] for d in rr], [d["pk"] for d in rr], "o-", label=scr_name(scr))
    ax[2].axhline(P["win"]["lo"], color="r", ls=":", label="window low edge")
    ax[2].set_xlabel("residual voltage, pu")
    ax[2].set_ylabel("largest phase current + ripple/2, A")
    ax[2].set_title("ride-through at 125 kW, firmware limiter (4b)")
    ax[2].grid(alpha=0.3)
    ax[2].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "vf_and_ride_through.png"), dpi=110)
    plt.close(fig)


def plots_r2(P, R):
    """bounded.png: the off-grid full-load steps against the declared envelope, the weak-grid DC-load rejection with the protection,
    the stiff-grid onset variants (review R2-04 / R2-05)"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.4))
    vb, env, t0 = R["vfb"], A("vf_envelope")[0], 0.01
    for name, r in vb["battery_rec"].items():
        tm = 1e3 * (r["t"] - t0)
        sel = (tm > -1) & (tm < 20)
        ax[0].plot(tm[sel], np.abs(r["vc"][sel]) / P["V0"], label=name if name.startswith("D-077") else "design " + name)
    for side in ("over", "under"):
        xs, ys, prev = [], [], 0.0
        for te, v in env[side]:
            te = min(te, 20.0)
            xs += [prev, te]
            ys += [v, v]
            prev = te
        ax[0].plot(xs, ys, "k:", lw=1)
    ax[0].set_xlabel("ms after the step")
    ax[0].set_ylabel("|v_C| pu")
    ax[0].set_title("VF full-load steps, battery (dotted: declared envelope)")
    ax[0].legend(fontsize=7)
    for d in R["dct"]["rows"]:
        r = d["rec"]
        ax[1].plot(1e3 * (r["t"] - R["dct"]["t0"]), r["vdc"], label=d["case_name"][:38])
        if d["trip"]:
            tl = d["tail"]
            ax[1].plot(1e3 * (r["t"][-1] - R["dct"]["t0"] + tl["t"]), tl["vdc"], "--", color=ax[1].lines[-1].get_color())
    for y in (P["ov_soft"][0], P["ov_dc"][1], P["ov_dc"][2], P["aux"]["ov"]):
        ax[1].axhline(y, ls=":", color="k", lw=0.8)
    ax[1].set_xlim(-0.5, 8)
    ax[1].set_xlabel("ms after the DC/DC trip (dashed: gates off, diode tail)")
    ax[1].set_ylabel("V_dc, V")
    ax[1].set_title("SCR 5 DC-load rejection with the protection")
    ax[1].legend(fontsize=6)
    m = R["rtr"]["measures"]
    ax[2].barh(range(len(m)), [v for _, v in m], color=["C3" if v > R["rtr"]["thr"] else "C2" for _, v in m])
    ax[2].set_yticks(range(len(m)))
    ax[2].set_yticklabels([n[:46] for n, _ in m], fontsize=6)
    ax[2].axvline(R["rtr"]["thr"], color="k", ls=":")
    ax[2].set_xlim(300, 500)
    ax[2].set_xlabel("stiff 0 pu onset, A (averaged + ripple/2)")
    ax[2].set_title("onset variants (dotted: window low edge)")
    for a_ in ax:
        a_.grid(alpha=0.3)
    fig.suptitle("Review R2: bounded transients and the stiff-grid onset (SIMULATED, sampled averaged models)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "bounded.png"), dpi=110)
    plt.close(fig)


def clean(o):
    """JSON-safe copy (drops time records)"""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items() if k not in ("rec", "L", "lam", "kv", "ev", "tail", "battery_rec", "trace")}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(o) else float(f"{float(o):.6g}")
    if isinstance(o, (np.integer, int)) and not isinstance(o, bool):
        return int(o)
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (set, frozenset)):
        return sorted(clean(v) for v in o)
    if isinstance(o, complex):
        return [float(f"{o.real:.6g}"), float(f"{o.imag:.6g}")]
    return o


def write_spec(P, G, R):
    K, Kv = G["K"], G["Kv"]
    spec = {"status": "CALCULATED / SIMULATED by sim/pcs_control.py - not measured, not bench-validated",
            "inputs": {"pcs_spec": os.path.relpath(SPEC, ROOT), "magnetics_rev": P["rev"], "C_dc_F": P["Cdc"], "C_dc_basis": P["Cdc_src"],
                       "f_s_Hz": P["fs"], "delay_s": 1.5 * P["T"], "current_chain_delay_s": P["ti"], "vC_divider_Hz": P["f_vdiv"],
                       "tolerances": dict(P["tol"], source="bom/PCS-PWR_BOM.csv (C_f, C_d, R_d lines) and sim/out/magnetics L_tol (L1, L2)")},
            "assumptions": {k: [v[0] if not isinstance(v[0], dict) else {str(a): b for a, b in v[0].items()}, v[1]] for k, v in ASSUME.items()},
            "current_loop": {"structure": "alpha-beta PR on the converter (L1) current; SOGI-filtered C_f-voltage feed-forward "
                                          "re-seeded on large steps; reference = P/Q in the PLL frame + j w C_eq v_d",
                             "f_c_Hz": K["fc"], "feasible_band_Hz": R["band"], "delay_limited_Hz": R["f_delay"], "Kp_ohm": K["Kp"],
                             "Kr1": 2 * K["Kp"] * 2 * math.pi * A("fz_Hz"), "harmonics": K["harm"],
                             "phase_lead_deg": {h: math.degrees(v) for h, v in K["phi"].items()},
                             "Krh": 2 * K["Kp"] * 2 * math.pi * A("sigma_h_Hz"), "margins": R["table"], "ff_variants": R["ff"],
                             "active_damping_scan": R["ad_scan"], "sweep": R["sweep"]},
            "gfm_voltage_loop": {"f_cv_param_Hz": R["gv"]["fcv"], "zero_ratio": R["gv"]["ratio"], "Kpv": Kv["Kp"],
                                 "Krv": 2 * Kv["Kp"] * 2 * math.pi * R["gv"]["fcv"] / R["gv"]["ratio"], "margins": R["gv"]["ev"],
                                 "fastest_meeting_rule": {k: R["gv_fast"][k] for k in ("fcv", "ratio", "pm", "gm", "f_bw_rl")},
                                 "scan": [{k: r[k] for k in ("fcv", "ratio", "ok", "pm", "gm", "rho", "head")} for r in R["gv_rows"]],
                                 "droop": {"mp_rad_s_per_W": G["mp"], "nq_V_per_var": G["nq"], "Zv_ohm": G["Zv"]}},
            "pll": {"f_bw_Hz": G["f_pll"], "kp": G["pll_kp"], "ki": G["pll_ki"], "sweep": R["pll"]["sweep"], "time": R["pll"]["tsim"]},
            "dc_link": {"f_c_Hz": G["f_dc"], "kp_A_per_V": G["kp_dc"], "ki": G["ki_dc"], **R["dc"]},
            "steps": R["steps"], "faults": R["faults"], "divider_whatif": R["divider"], "gfm": R["gfm"], "thd": R["thd"],
            "vdc_min_V": R["vdc_min"],
            "ride_through": {"limiter": {"clamp_A": P["iclamp"], "clamp_basis": "window low edge - ripple/2 - iclamp_margin_A (sampled, "
                                         "ripple-midpoint current)", "dip_limit_A_pk": P["lvrt_ilim"], "lvrt_pu": A("lvrt_pu"),
                                         "hold_s": A("lvrt_hold_s"), "pll_freeze_pu": A("pll_freeze_pu"), "window_low_A": P["win"]["lo"],
                                         "profile_pu_s": P["lvrt_profile"]},
                             **{k: R["rt"][k] for k in ("rows", "clamp_only", "derating", "instant_scan", "scr50")}},
            "vf_secondary": {k: R["sec"][k] for k in ("T_s", "scan", "steps", "ss", "reseed_pu", "can_cycle_s")},
            "vf_accuracy": R["acc"],
            "gfm_voltage_harmonics": {k: R["gfm_harm"][k] for k in ("harm", "phi", "spread", "Kh", "ok_margin", "decay", "decay_need",
                                                                    "ok_decay", "adopted", "pm", "gm")},
            "thdu": dict(R["thdu"], worst={f"{w} {d}": max(r["thdu_pct"] for r in R["thdu"]["rows"] if r["wire"] == w and
                                                            r["loop"] == "PR h1" and r["dead_time"] == d)
                                           for w in ("3W", "4W") for d in dict.fromkeys(r["dead_time"] for r in R["thdu"]["rows"])}),
            "imbalance": R["imb"], "neutral_leg": R["nleg"],
            "firmware": {"sampling": R["plan"], "isr": {"tasks": R["isr"][0], "load": R["isr"][1]},
                         "limits": firmware_limits(P, G, R), "anti_islanding": anti_islanding(P),
                         "state_machine": {"table": sm_table(P, G), "transitions": {f"{a} --{e}-->": b for (a, e), b in SM_T.items()},
                                           "outputs_PWM_KPRE_KDC_KAC": SM_OUT, "verified": R["sm"]}},
            "hardware_mismatches": R["hw"], "agreement_A": R["agree"], "plant_check_rel": R["plant_check"],
            # review R2 (a427981): new keys only - nothing gen/pcs_ctrl.py or sim/compare_megarevo_pcs.py reads is renamed or removed
            "tolerance_regression": R["tol"],
            "protection_chain_used": P["pc"],
            "ride_through_rule": {k: v for k, v in R["rtr"].items()},
            "vf_feedforward": {"chosen": R["vff"], "scan": R["vff_rows"], "vclamp_pu": A("vf_vclamp_pu")},
            "vf_secondary_basis": "computed with the VF firmware of section 7c (load-current feed-forward, over-voltage deadbeat, per-sample "
                                  "clamp) and the paralleling virtual impedance; the D-077 figures (0.380 / 2.170 pu) were the unbounded "
                                  "averaged model without them and are superseded (vf_bounded)",
            "vf_bounded": R["vfb"], "dc_rejection_bounded": R["dct"], "four_wire_coupled": R["n4"],
            "ac_start_state_machine": {"outputs_PWM_KACPRE_KPRE_KDC_KAC2_KAC1": SM_OUT6, "exec": R["smx"]},
            "review_r2": review_r2(P, G, R)}
    spec = clean(spec)
    json.dump(spec, open(os.path.join(OUT, "pcs_control_spec.json"), "w"), indent=1)
    return spec


def review_r2(P, G, R):
    """review R2 of commit a427981 (gen/data/review_r2.csv, the findings of this study): classification and evidence, the reviewer's
    figures (REVIEW_R2, quoted) beside ours (computed in this run)"""
    tol, rr, vb, dc, n4, sm = R["tol"], R["rtr"], R["vfb"], R["dct"], R["n4"], R["smx"]
    rv = REVIEW_R2
    nom = {scr_name(d["scr"]): d["nom_pm"] for d in tol["per_scr"]}
    st = vb["steps"]
    des = [v for k, v in st.items() if k.startswith("design, single module")]
    d77 = {k.split(", ")[-1]: v for k, v in st.items() if k.startswith("D-077")}
    unc = list(vb["uncoordinated"].values())
    trips = [d for d in dc["rows"] if d["trip"]]
    loop = next(d for d in dc["rows"] if not d["trip"])
    sh = next(v for k, v in n4["cases"].items() if k.startswith("line-to-neutral"))
    ub = next(v for k, v in n4["cases"].items() if k.startswith("100 % unbalanced step (phase a rated, b / c open)") and "option" not in k)
    return [
        dict(id="R2-01", classification="Confirmed (a coverage defect; the gap is not an instability)",
             reviewer=f"{rv['R2-01']['cases']} mixed-tolerance cases incl. C_f +/-10 %, all linearly stable; PM " +
                      " / ".join(f"{v:.2f}" for v in rv["R2-01"]["pm_deg"].values()) + " deg at stiff / SCR 20 / 10 / 5 (his figures)",
             ours=f"tolerances read from the drawn BOM and the magnetics files (C_f +/-{tol['tol']['Cf']:.0%}, C_d +/-{tol['tol']['Cd']:.0%}, "
                  f"R_d +/-{tol['tol']['Rd']:.0%}, L1 / L2 +/-{tol['tol']['L1']:.0%}); nominal PM " + " / ".join(f"{v:.2f}" for v in nom.values()) +
                  f" deg (the same); {tol['factorial']['n']} factorial + {tol['random']['n']} random cases: "
                  f"{tol['factorial']['n_pass'] + tol['random']['n_pass']} meet PM >= {RULE_PM:.0f} deg / GM >= {RULE_GM:.0f} dB / poles inside; "
                  f"worst PM {min(tol['factorial']['pm'], tol['random']['pm']):.2f} deg, worst GM {min(tol['factorial']['gm'], tol['random']['gm']):.2f} dB "
                  f"({tol['factorial']['gm_at']}) - below the named corners' figure, the coverage gap made visible; the design is unchanged; "
                  "side effects of the assembly tolerances elsewhere: THDu (8b) "
                  + " / ".join(f"{max(r['thdu_pct'] for r in R['thdu']['rows'] if r['wire'] == w and r['loop'] == 'PR h1'):.2f}" for w in ("3W", "4W"))
                  + " % three- / four-wire (D-077 printed 1.31 / 1.98 % with C_f +/-5 %), still below 3 %",
             change="corner() carries R_d / C_d at the assembly tolerances; the named corners read them; THDu's mismatch pair likewise; "
                    "the regression is a self-check", keys=["tolerance_regression", "inputs.tolerances"]),
        dict(id="R2-04", classification="Confirmed (a model-boundary gap) - closed in the averaged model with the firmware of section 7c "
                                        "and the trip-aware tail; the bench and a switched model remain",
             reviewer=f"{rv['R2-04']['sag_pu']:.3f} pu sag, {rv['R2-04']['overshoot_pu']:.3f} pu overshoot; {rv['R2-04']['dc_rejection_peak_V']:.0f} V "
                      f"weak-grid DC-load rejection against the {rv['R2-04']['ov_band_V'][0]:.0f}-{rv['R2-04']['ov_band_V'][1]:.0f} V OV band",
             ours=f"D-077 configuration reproduced: {d77['0 -> rated']['v_min']:.3f} / {d77['rated -> 0']['v_max']:.3f} pu (unbounded averaged - "
                  f"superseded); bounded design, single module, every corner: dip >= {min(d['v_min'] for d in des):.3f} pu, overshoot <= "
                  f"{max(d['v_max'] for d in des):.3f} pu, within 10 % after <= {max(d['t_v10_ms'] for d in des):.1f} ms and 1 % after <= "
                  f"{max(d['t_v1_ms'] for d in des):.1f} ms, inside the declared envelope and the ITIC-style class; capacitive DC bus: the "
                  f"returned LCL energy {vb['e_ret_J']:.1f} J lifts it to {vb['cap_follow']['vdc_max']:.0f} V, a DC/DC must cut within "
                  f"{vb['t_c_max_ms']:.2f} ms, uncoordinated it trips at {min(u['t_off_ms'] for u in unc):.2f}-{max(u['t_off_ms'] for u in unc):.2f} ms "
                  f"and the bus ends at {min(u['vdc_end'] for u in unc):.0f}-{max(u['vdc_end'] for u in unc):.0f} V; weak-grid DC-load rejection: "
                  f"the loop alone {loop['vmax_loop']:.0f} V (= the reviewer's), with the protection the gates go off at "
                  f"{min(d['v_off'] for d in trips):.0f}-{max(d['v_off'] for d in trips):.0f} V and the diode-bridge dump leaves "
                  f"{min(d['vdc_end'] for d in trips):.0f}-{max(d['vdc_end'] for d in trips):.0f} V (stopped, latched)",
             change="sections 6b and 7c: DC link and DC-side source in the grid-forming loop, the OV comparator / PPB and the gates-off "
                    "diode tail; firmware: load-current feed-forward, over-voltage deadbeat, single-module Z_v 0, DC/DC coordination, "
                    "CV-mode derating at weak grids, the soft limit holding the DC loop",
             keys=["vf_bounded", "dc_rejection_bounded", "vf_feedforward", "vf_secondary_basis"]),
        dict(id="R2-05", classification="Firmware Handled - by a firmware measure that removes the restriction in the averaged model; the "
                                        "enforceable derating rule is specified as the fallback",
             reviewer=f"onset {' / '.join(f'{x:.0f}' for x in rv['R2-05']['onset_A'])} A at {' / '.join(f'{x:g}' for x in rv['R2-05']['residual_pu'])} pu "
                      f"against {rv['R2-05']['trip_edge_A']:.0f} A; pre-fault limits {' / '.join(f'{x:g}' for x in rv['R2-05']['prefault_kW'])} kW "
                      "are restrictions, not ride-through",
             ours=f"the same onsets (section 4b); boundary at rated current SCR {' / '.join(f'{d['scr_b']:.1f}' for d in rr['boundary'])} for "
                  f"{' / '.join(f'{d['depth']:g}' for d in rr['boundary'])} pu (SCR 50 passes with {rr['boundary'][0]['margin_scr50_A']:.1f} A); "
                  f"the ride-through clamp at {rr['clamp_rt_A']:.0f} A with the divider pole inverted holds the stiff onset at "
                  f"{rr['measures'][5][1]:.0f} A, {rr['worst_onset']:.0f} A at the worst corner / instant ({rr['margin_A']:.0f} A margin); "
                  f"fallback: grid-stiffness estimate engaging at SCR {rr['engage_scr']:.1f} ({rr['S_sc_engage_MVA']:.2f} MVA per 125 kW module), "
                  "the derating table above it",
             change="section 4c: the measure (firmware rows), the boundary, the estimator, the table and the declaration; the trip edge "
                    "from pcs_spec protection_chain", keys=["ride_through_rule", "protection_chain_used"]),
        dict(id="R2-13", classification="Confirmed - the coupled four-wire cases the averaged per-phase model can carry are added",
             reviewer="full nonlinear unbalanced behaviour open",
             ours=f"100 % unbalanced step: phase-to-N amplitudes {ub['va']['amp_min']:.3f}-{ub['va']['amp_max']:.3f} pu, N_F {ub['vn_pk']:.2f} pu "
                  f"instantaneous; L-N short: first peak {sh['i_pk']['ia']:.0f} A (phase) / {sh['i_pk']['iN']:.0f} A (N leg) against "
                  f"{P['win']['lo']:.0f} A, firmware trip at {sh['trip_ms']:.0f} ms, the healthy phases >= {min(sh['vb_amp'], sh['vc_amp']):.3f} pu; "
                  "the half-wave step, the GFL <-> GFM transitions with unbalance: section 9c",
             change="section 9c (ph4_run); firmware: DC regulators 0.02 s, N-leg feed-forward option", keys=["four_wire_coupled"]),
        dict(id="R2-14", classification="Confirmed - the AC start is a state of the checked machine and executed with fault injection",
             reviewer="AC start excluded from the checked machine",
             ours=f"{len(SM_STATES)} states, invariants {'all hold' if all(R['sm'].values()) else 'FAIL'}; {len(sm['rows'])} fault cases; as "
                  f"specified the sequence breaches {len(sm['holes'])} safety checks ({'; '.join(h.split(' ', 1)[0] for h in sm['holes'])}) "
                  f"and false-locks after a stuck contactor; with the repairs H1-H5 {'no breach remains' if not sm['residual'] else 'residual: ' + '; '.join(sm['residual'])}",
             change="section 9d; SM_T / SM_OUT6 / verify_sm extended, sm_exec, firmware rows H1-H5", keys=["ac_start_state_machine",
                                                                                                         "firmware.state_machine"])]


def firmware_limits(P, G, R):
    tr = P["tiers"]
    return [("current-reference limiter", f"circular on i_1 ref: {tr['200_ms']:.0f} A rms ({P['I_lim']:.0f} A pk) for <= "
             f"{A('t_lim_s') * 1e3:.0f} ms, then {tr['2_min']:.0f} A rms for <= 2 min, then {tr['continuous']:.0f} A rms; "
             "timer -> controlled stop"),
            ("modulation limiter", f"|u| <= {P['u_frac']:.4f} V_dc,meas ({P['u_src']}); clamping anti-windup on the resonant "
             "terms; refuse to start / stop when V_dc is below the operating map (" + f"{R['vdc_min'][None]:.0f} V at a +10 % grid, "
             "rated current, this study)"),
            ("feed-forward", f"SOGI (k {A('sogi_k'):.2f}) on v_C; re-seed when |v_C - v_ff| > {A('ff_reseed_pu')} pu"),
            ("PLL", f"SRF on VC, {G['f_pll']:.0f} Hz, integrator +/-{A('pll_dw_max_Hz'):.0f} Hz with conditional integration, "
             f"angle slew +/-{A('pll_slew_Hz'):.0f} Hz; add the divider lag atan(w tau_v) = "
             f"{math.degrees(math.atan(P['w0'] * P['tv'])):.2f} deg to the angle; protection frequency from the integrator, filtered"),
            ("P/Q -> current", f"voltage magnitude low-passed at {A('vd_lpf_Hz'):.0f} Hz"),
            ("DC-link loop", f"PI {G['f_dc']:.0f} Hz crossover + measured DC-current feed-forward (mandatory); DC/DC power "
             "ramp <= 125 kW in 20 ms; a DC/DC trip must stop the PCS current in the same frame (coordinated)"),
            ("sampling", "ADC SOC at carrier zero and period, delayed by the current-chain delay "
             f"{P['ti'] * 1e6:.2f} us so the sample sits on the ripple midpoint; 3 simultaneous rounds before the ISR; VA/VB "
             f"{R['plan']['k_va']} x per carrier period (<= {P['va_max_us']:.0f} us apart, ADC-PPB over-voltage backup)"),
            ("dead time", f"program >= {P['dt_floor_ns']:.0f} ns so the firmware value, not the hardware stretch "
             f"({P['dt_gate_ns'][0]:.0f}-{P['dt_gate_ns'][1]:.0f} ns), sets it; or identify each leg's effective dead time at "
             "commissioning and compensate (THDi estimate: section 8)"),
            ("synchronisation", f"close K_AC1/K_AC2 only at |dV| <= {P['sync'][0]:.0f} %, |dtheta| <= {P['sync'][1]:.0f} deg "
             "(pcs_spec), |df| <= 0.1 Hz (assumed), after the relay test"),
            ("ADC configuration", "AGPIOCTRLA GPIO20/21 = 0: B5/B11 read on pins 32/30, pins 48/49 stay digital (TACH3, RCM_TST)")] + \
        fw_rows_new(P, R)


def fw_rows_new(P, R):
    """firmware rows of sections 4b, 7b, 8b-c, 9b (limit / setting, value)"""
    rt, sec, hv, nl, th = R["rt"], R["sec"], R["gfm_harm"], R["nleg"], R["thdu"]
    lo, tr = P["win"]["lo"], [r for r in rt["rows"] if r["trip"]]
    der = {d["depth"]: d for d in rt["derating"]}
    wt = {w: max(r["thdu_pct"] for r in th["rows"] if r["wire"] == w and r["loop"] == "PR h1") for w in ("3W", "4W")}
    mis = max((r for r in th["rows"] if r["mismatch_pct"] > 0), key=lambda r: r["mismatch_pct"])
    ci = [r for r in nl["rows"] if not r["i_ok"]]
    return [
        ("ride-through state (LVRT)", f"entered when the sensed |v_C| < {A('lvrt_pu')[0]} pu, kept {A('lvrt_hold_s') * 1e3:.0f} ms after it "
         f"is back above {A('lvrt_pu')[1]} pu; reference limit {P['lvrt_ilim']:.0f} A pk during it (the hand-over profile's "
         f"{P['lvrt_ilim'] / P['I_rated']:.1f} I_r reactive-current cap) instead of the 200 ms tier; PLL integrator and angle held below "
         f"{A('pll_freeze_pu')} pu (free-running at the pre-dip frequency)"),
        ("per-sample current clamp", f"in the PWM update, per phase: the current one period after the new command, predicted from the "
         f"sensed current and the committed command over the -10 % L1, limited to {P['iclamp']:.0f} A (sampled) = window low edge "
         f"{lo:.0f} A - ripple/2 {P['ripple_pp'] / 2:.1f} A - {A('iclamp_margin_A'):.0f} A; deadbeat correction with unit gain on that "
         f"phase, anti-windup through the PR back-calculation; backstop only - the ride-through state keeps the reference below it"),
        ("stiff-grid deep-dip derating (FALLBACK, section 4c)", ("needed only if the onset measure below is not taken or not confirmed: with "
         f"the D-077 limiter rated current reaches the window at a stiff connection for residual voltages of {max(r['depth'] for r in tr):g} pu "
         f"and below, from SCR {R['rtr']['scr_b']:.1f} stiffer; the grid-stiffness estimate engages the table of section 4c at SCR "
         f"{R['rtr']['engage_scr']:.1f} (stiff: " + ", ".join(f"{d['depth']:g} pu {d['P_max_kW']:.1f} kW" for d in rt["derating"] if d["frac"] < 1) + ")")
         if tr else "none: every studied dip stays below the window"),
        ("VF secondary restoration", f"frequency and C_f-voltage magnitude integrators, time constant {sec['T_s']:g} s (sec_rule); VF only - "
         "off in grid-connected GFM, frozen and ramped out before re-synchronisation; voltage layer re-seeded downwards on a load "
         f"rejection (> {sec['reseed_pu']:g} pu above the present need); paralleled modules agree the restoration terms over the module "
         f"CAN (consensus, {A('can_cycle_s') * 1e3:.0f} ms cycle ASSUMED, not modelled) so the droop's sharing is kept"),
        ("angle generator (VF)", f"32-bit per-unit phase accumulator, increment rounded ({R['acc']['frequency']['accumulator_ppm']:.2f} ppm), "
         "TMU sin/cos of the per-unit angle: the output frequency is the controller oscillator's"),
        ("voltage-loop h5 / h7 resonant terms (VF)", ("adopted" if hv["adopted"] else "not adopted") + f": margin rule met (PM "
         f"{hv['pm']:.0f} deg, GM {hv['gm']:.1f} dB) but their modes decay at " + "/".join(f"{-v:.0f}" for v in hv["decay"].values())
         + f" 1/s against {-hv['decay_need']:.0f} 1/s; THDu without them <= {wt['3W']:.2f} % (three-wire) / {wt['4W']:.2f} % (four-wire), "
         "dead time not compensated"),
        ("zero sequence in VF (THDu option)", f"min-max zero sequence (m > 0.98) excites the L1-C_f resonance through the filters' "
         f"tolerance mismatch: up to {mis['mismatch_pct']:.2f} % ({mis['wire']}, {mis['vdc']:.0f} V); a pure third-harmonic zero sequence "
         f"(same linear range) leaves only its 150 Hz term ({nl['modeB']['resid_150_pct']:.3f} %) - firmware option, not adopted here"),
        ("four-wire per-phase loops", "one C_f-voltage PR per phase on its phase-to-N voltage (N held on M by the N leg): the "
         f"alpha-beta design per axis - margins and bandwidth identical ({nl['phase_ref']['bw']['nl']:.0f} Hz no load, "
         f"{nl['phase_ref']['bw']['rl']:.0f} Hz rated); per-phase amplitude restoration (the secondary voltage layer per phase); "
         "per-phase virtual reactance through a quadrature generator (not simulated); DC-component regulator per phase on the "
         "phase-to-N one-cycle mean (pcs_spec row)"),
        ("four-wire N leg", "the VF cascade in every mode: P-only current loop on IL4 with the phases' K_p and SOGI feed-forward, the "
         "phases' C_f-voltage PR on VGN - VA (reference 0 in mode A; mode B: the min-max zero sequence u_0 as feed-forward to its "
         + (f"modulator and reference); not the grid-following PR alone (max |pole| {max(r['i_rho'] for r in ci):.5f} on the capacitive "
            "N node)" if ci else "modulator and reference); the grid-following PR alone would also meet the rule") + "; N-leg DC regulator: one-cycle mean of VGN - VA -> "
         f"integrator -> offset on its reference (without it a half-wave load's {nl['dc']['I_dc_A']:.0f} A DC shifts N_F by "
         f"{nl['dc']['offset_without_V']:.0f} V through the voltage loop's {nl['dc']['K_pv_S']:.3f} S); the phases' DC regulators "
         "act on the phase-to-N means - independent quantities")] + fw_rows_r2(P, R)


def fw_rows_r2(P, R):
    """firmware rows of review R2 (sections 4c, 6b, 7c, 9c, 9d)"""
    rr, vb, dc, n4, vf = R["rtr"], R["vfb"], R["dct"], R["n4"], R["vff"]
    der = ", ".join(f"{scr_name(d['scr'])} {d['P_max_kW']:.1f} kW" for d in dc["derating"])
    return [
        ("ride-through onset measure (review R2-05; adopted)", f"while the ride-through state runs the per-sample clamp sits at the dip-time "
         f"limit + {A('iclamp_margin_A'):.0f} A = {rr['clamp_rt_A']:.0f} A (sampled) instead of {P['iclamp']:.0f} A, and the clamp predicts on "
         f"the sensed C_f voltage with the divider's known pole inverted (v = (y[k] - a y[k-1]) / (1 - a), a = exp(-T / tau_v)); stiff 0 pu "
         f"onset {rr['measures'][5][1]:.0f} A, worst corner / instant {rr['worst_onset']:.0f} A against the {rr['thr']:.0f} A window edge"),
        ("grid-stiffness estimate (fallback of the onset measure; also the CV-mode rule)", f"at connection a {rr['dq_pu']:g} pu reactive-current "
         "step (a PRBS of it in operation, correlated over 10 s in the 1 kHz task): the terminal-voltage phasor change (VG1-3) over the "
         "estimated grid-current change (i_1 - j w C_eq v_C), SCR = Z_base / |Z|; deterministic error of the method in the model "
         f"{max(abs(e['err_pct']) for e in rr['estimator']):.3f} %; accuracy ASSUMED +/-{100 * rr['acc']:.0f} % (the grid's own voltage variation); "
         f"signal at the engage level {rr['dV_engage_V']:.2f} V = {rr['dV_engage_V'] / rr['lsb_V']:.1f} LSB of the {rr['lsb_V']:.3f} V/LSB channel "
         f"(averaging needed); no valid estimate = treated as stiff"),
        ("VF load-current feed-forward (review R2-04; adopted)", f"i_ref += LPF(i_1 - C_eq (v_C[k] - v_C[k-1]) / T), low-pass {vf['f_io']:.0f} Hz "
         f"(the highest of the scan meeting the rule: PM {vf['pm']:.0f} deg, GM {vf['gm']:.1f} dB at every corner); VF only, not in "
         "grid-connected grid forming"),
        ("VF over-voltage deadbeat (review R2-04; adopted)", f"above {A('vf_vclamp_pu'):g} pu (divider-inverted sample) the inner command is the one "
         "that ends the next period with i_1 at the unfiltered load-current estimate; the voltage PR does not integrate meanwhile; full-load "
         f"rejection {max(v['v_max'] for k, v in vb['steps'].items() if k.startswith('design, single')):.2f} pu at the worst corner instead of "
         f"{next(v['v_max'] for k, v in vb['steps'].items() if k.startswith('D-077') and k.endswith('rated -> 0')):.2f} pu"),
        ("VF virtual impedance", f"single module: 0 (the voltage PR holds the C_f voltage exactly; 1 % after <= "
         f"{max(v['t_v1_ms'] for k, v in vb['steps'].items() if k.startswith('design, single')):.0f} ms); paralleled modules: the droop's "
         f"{A('zv_pu')[0]:g} + j{A('zv_pu')[1]:g} pu for sharing, restored by the secondary layer (section 7b)"),
        ("DC/DC coordination on a DC bus without a battery (VF)", f"an AC load rejection returns {vb['e_ret_J']:.0f} J to the link; a DC/DC feeding "
         f"it must cut its current within {vb['t_c_max_ms']:.2f} ms (a bus-voltage limiter or a shared trip line), else the PCS trips on DC "
         "over-voltage and the island goes dark; the PCS cannot absorb it (no brake chopper drawn)"),
        ("CV mode with DC/DC loads at weak grids (review R2-04)", f"DC/DC load on the link limited so that its trip stays below the "
         f"{P['ov_soft'][0]:.0f} V soft limit: {der} (the grid-stiffness estimate selects it); the soft limit HOLDS the DC-link loop in CV mode "
         "(as drawn it is a controlled stop, which would remove the only sink); a DC over-voltage trip leaves the bus charged for minutes"),
        ("four-wire DC-component regulators", f"time constant {A('dc_reg_T_s'):g} s on the one-cycle means (section 9c: the half-wave step's DC "
         "component returns below 0.5 % Un within the time printed there; 0.2 s takes about twice as long)"),
        ("four-wire N-leg feed-forward (option, not adopted)", "the N leg's current reference + the sum of the phases' load-current estimates: "
         "halves the N-node swing of a 100 % unbalanced step (section 9c); stability shown only in the averaged simulation")] + \
        [(f"AC start repair {h} (review R2-14)", txt) for h, txt in SM_FIXES.items()]


def tab(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(c) for c in r) + " |"
                                                                                    for r in rows]) + "\n"


def g1(x, n=1):
    return "inf" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.{n}f}"


def rep_r2(P, R, spec):
    """report section: the independent review R2 of commit a427981 (this study's findings)"""
    L = ["## Review R2 (independent re-check of commit a427981): findings of this study\n"]
    a = L.append
    rv = REVIEW_R2["R2-01"]
    a("Verified independently first, then classified (Confirmed / Already Fixed / Firmware Handled / Not Applicable / False Finding / "
      "Improvement Recommended); changes only where required, at the root. The reviewer's figures are his, quoted; ours are this run's.\n")
    a(tab(("finding", "classification", "reviewer (his figures)", "this study (computed in this run)", "change", "new keys"),
          [(d["id"], d["classification"], d["reviewer"], d["ours"], d["change"], ", ".join(d["keys"])) for d in spec["review_r2"]]))
    nom = " / ".join(f"{d['nom_pm']:.2f}" for d in R["tol"]["per_scr"])
    a(f"**Independent corroboration (positive evidence, his figures):** the reviewer reproduced the inner current loop on his own model - "
      f"{rv['cases']:,} mixed-tolerance cases including C_f +/-10 %, all linearly stable, phase margins "
      + " / ".join(f"{v:.2f}" for v in rv["pm_deg"].values()) + f" deg at stiff / SCR 20 / 10 / 5 - against this study's nominal {nom} deg "
      "(the D-077 report printed 71.1 / 61.4 / 61.7 / 49.0). The agreement to two decimals is an independent check of the plant, the delay "
      "model and the controller discretisation; it is not a measurement.\n")
    return L


def rep_2b(P, R):
    """report section 2b: the mixed-tolerance regression (review R2-01)"""
    t, L = R["tol"], []
    a = L.append
    f, rn, d = t["factorial"], t["random"], t["delay_s"]
    gmn = min(min(r["gm"], r["gm_lo"]) for r in R["table"] if r["damp"] == "passive" and r["cn"] in CORNERS)
    a("## 2b. Mixed-tolerance regression of the current loop (review R2-01)\n")
    a(f"**Tolerances, read from the assembly (fail closed):** C_f +/-{t['tol']['Cf']:.0%}, C_d +/-{t['tol']['Cd']:.0%}, R_d +/-{t['tol']['Rd']:.0%} "
      f"(the drawn lines of bom/PCS-PWR_BOM.csv, values checked against pcs_spec), L1 and L2 +/-{t['tol']['L1']:.0%} at 0 A (the magnetics "
      "files' L_tol; the low end is the -10 % part's L(I) at the highest current). The study had ASSUMED C_f +/-5 % and left R_d and C_d "
      "nominal in every corner - the defect the reviewer found. The named corners now carry the assembly's tolerances (low = every part "
      "low, high = every part high); the design they select is unchanged (f_c, band, harmonic terms, grid-forming gains).\n")
    a(f"**Regression:** full factorial - L1, L2, C_f, C_d, R_d each at low / nominal / high, the current-chain delay at {d['nom'] * 1e6:.2f} us "
      f"(the frozen sensor's maximum step) and {d['slow'] * 1e6:.2f} us, the four studied grids: 3^5 x 2 x 4 = {f['n']} cases - the reviewer's "
      f"count; plus {rn['n']} seeded random cases inside the same box (seed {rn['seed']}; delay from the typical {d['typ'] * 1e6:.2f} us to "
      f"{d['slow'] * 1e6:.2f} us; 1/SCR uniform on [0, 1/{rn['scr_min']:g}], stiff included). Design gains; margin rule PM >= {RULE_PM:.0f} deg, "
      f"GM >= {RULE_GM:.0f} dB, every closed-loop pole inside the unit circle (CALCULATED, sampled averaged model).\n")
    a(tab(("grid", "nominal PM / GM", "worst PM, at", "worst GM, at", "largest abs(pole)", "cases meeting the rule"),
          [(scr_name(r["scr"]), f"{r['nom_pm']:.2f} deg / {r['nom_gm']:.2f} dB", f"{r['pm']:.2f} deg ({r['pm_at'].rsplit(', ', 1)[0]})",
            f"{r['gm']:.2f} dB ({r['gm_at'].rsplit(', ', 1)[0]})", f"{r['rho']:.6f}", f"{r['n_pass']} of {f['n'] // len(t['per_scr'])}")
           for r in t["per_scr"]]))
    a(f"Factorial: {f['n_pass']} of {f['n']} meet the rule (worst PM {f['pm']:.2f} deg at {f['pm_at']}; worst GM {f['gm']:.2f} dB at {f['gm_at']}); "
      f"random: {rn['n_pass']} of {rn['n']} (worst PM {rn['pm']:.2f} deg, GM {rn['gm']:.2f} dB); no case is unstable (largest |pole| "
      f"{max(f['rho'], rn['rho']):.6f}). The gain-margin minimum sits at a mixed corner ({f['gm_at']}) that neither named corner holds: "
      f"the coverage gap was real; its consequence is a margin {gmn - min(f['gm'], rn['gm']):.2f} dB below the named corners' minimum "
      f"({gmn:.2f} dB), not an instability. The regression is a self-check of the script.\n")
    return L


def rep_4b(P, R):
    """report section 4b: the firmware limiter in ride-through"""
    rt, lo, L = R["rt"], P["win"]["lo"], []
    a = L.append
    a("## 4b. Cycle-by-cycle limiting in ride-through (firmware limiter; D-074 open item)\n")
    a(f"**The limiter (firmware, three layers).** (1) The circular reference limiter of section 4 ({P['I_lim']:.0f} A pk for <= 200 ms). "
      f"(2) A ride-through state: entered when the sensed |v_C| falls below {A('lvrt_pu')[0]} pu, left {A('lvrt_hold_s') * 1e3:.0f} ms "
      f"after it is back above {A('lvrt_pu')[1]} pu; while it runs the reference limit is {P['lvrt_ilim']:.0f} A pk - the hand-over "
      f"profile's {P['lvrt_ilim'] / P['I_rated']:.1f} I_r reactive-current cap (parsed from pcs_spec), not the 200 ms tier - and below "
      f"{A('pll_freeze_pu')} pu the PLL free-runs at its pre-dip frequency (otherwise it locks onto the module's own current through the "
      "grid impedance and the voltage comes back out of phase: without the freeze the 0 pu dip at SCR 50 recovered at 462 A). (3) A "
      f"per-sample predictive clamp in the PWM update at {P['iclamp']:.0f} A (sampled, ripple-midpoint current) = the window's low edge "
      f"{lo:.0f} A - ripple/2 {P['ripple_pp'] / 2:.1f} A - {A('iclamp_margin_A'):.0f} A for the prediction error (sensed C_f voltage "
      "behind its divider, L1 tolerance): per phase, the current one period after the new command is predicted from the sensed current "
      f"and the command already committed; a phase that would leave +/-{P['iclamp']:.0f} A gets the deadbeat command that ends that "
      "period on the clamp (unit gain on that phase; the PR integrators are back-calculated). Dips at 125 kW export with the power "
      "reference held, 150 ms; onset = the first ms; peaks = averaged + ripple/2 (SIMULATED, sampled averaged model).\n")
    a(tab(("grid", "residual pu", "onset A", "in dip A", "recovery A", "in-dip current A pk", "settled to 5 % ms", "modulation limit ms",
           "window"),
          [(scr_name(d["scr"]), f"{d['depth']:g}", f"{d['pk_on']:.0f}", f"{d['pk_dip']:.0f}", f"{d['pk_rec']:.0f}", f"{d['i_dip']:.0f}",
            f"{d['settle_ms']:.1f}", f"{d['usat_ms']:.1f}", "TRIP (onset)" if d["trip"] else "inside") for d in rt["rows"]]))
    co = rt["clamp_only"]
    a("Clamp alone (no ride-through state, reference at the 200 ms tier): " + "; ".join(
        f"{scr_name(d['scr'])} {d['depth']:g} pu: {d['pk_on']:.0f} / {d['pk_dip']:.0f} / {d['pk_rec']:.0f} A (clamp active "
        f"{d['clamp_ms']:.1f} ms)" for d in co) + ". Against section 4 (no clamp) the clamp lowers the SCR 5 in-dip peak but not the "
      "recovery, where the modulation limit is reached; with the ride-through state the reference stays below it and it never acts - "
      "it is the backstop for a reference that would exceed it. The current loop settles onto the dip-time limit within the times in "
      "the table (SCR 5 longer: the modulation limit at the recovery and the weak-grid loop).\n")
    tr = [d for d in rt["rows"] if d["trip"]]
    on = {d["depth"]: d["pk_on"] for d in rt["rows"] if d["scr"] is None}
    a(f"**What this limiter cannot do (D-077 - corrected in section 4c).** The onset peak forms within the first ms, before the C_f divider's lag "
      f"({P['tv'] * 1e6:.0f} us) and the 1.5 T_s delay ({1.5 * P['T'] * 1e6:.1f} us) let any firmware act: on a stiff grid it reaches "
      + ", ".join(f"{v:.0f} A at {k:g} pu" for k, v in on.items()) + f" against the {lo:.0f} A low edge"
      + (f" - rated current trips the window for residual voltages of {max(d['depth'] for d in tr):g} pu and below at "
         + ", ".join(sorted({scr_name(d['scr']) for d in tr})) if tr else "") +
      f"; from SCR 50 down every onset stays inside (SCR 50 current-loop margins: PM {rt['scr50']['pm']:.0f} deg, GM "
      f"{rt['scr50']['gm']:.1f} dB). The study's dip instant is the worst of a 60 deg scan ("
      + ", ".join(f"{x:.0f}" for x in rt["instant_scan"]) + " A). A CMPSS cycle-by-cycle threshold below the window or a faster "
      "divider would be hardware. Section 4c: the clamp itself, set lower while the ride-through state runs and predicting on the "
      "divider-inverted C_f voltage, does act inside the latency chain - the D-077 conclusion that no firmware can was too strong.\n")
    a("**Derating (firmware) at stiff connections.** The onset is affine in the pre-dip current; the pre-dip power that puts it on the "
      "low edge, rounded down and verified by simulation: " + "; ".join(
          f"{d['depth']:g} pu: {d['P_max_kW']:.1f} kW ({100 * d['frac']:.0f} %, onset {d['onset']:.0f} A)" for d in rt["derating"]) + ".\n")
    pr = P["lvrt_profile"]
    ok_grids = [scr_name(s) for s in rt["grids"] if all(not d["trip"] for d in rt["rows"] if d["scr"] == s)]
    a(f"**Ride-through profile** (hand-over, FROM MEMORY: {pr[0][0]:g} pu to {pr[0][1]:g} s, {pr[1][0]:g} pu to {pr[1][1]:g} s, linear to "
      f"{pr[2][0]:g} pu at {pr[2][1]:g} s): during the dip the current is held at {P['lvrt_ilim'] / P['I_rated']:.1f} I_r, inside the continuous tier, so the profile's durations add no "
      "limit and the peaks do not depend on the duration beyond 150 ms (the current is settled). At rated pre-dip current the whole "
      "profile rides through at " + ", ".join(ok_grids) + "; at a stiff connection the points below "
      f"{min((d['depth'] for d in rt['derating'] if d['frac'] >= 1), default=1.0):g} pu need the derating above.\n")
    return L


def rep_4c(P, R):
    """report section 4c: the stiff-grid onset - the firmware measure and the enforceable fallback rule (review R2-05)"""
    rr, pc, L = R["rtr"], P["pc"], []
    a = L.append
    a("## 4c. The stiff-grid onset: a firmware measure, and the enforceable fallback rule (review R2-05)\n")
    a(f"**Trip edge** ({rr['thr_src']}): window band {pc['I_trip_lo']:.0f}-{pc['I_trip_hi']:.1f} A; gates off at up to "
      f"{pc['I_gates_off']:.1f} A {pc['t_resp_us']:.2f} us after the band top at {pc['didt_A_us']:.2f} A/us; commutation admissible to "
      f"{pc['I_screen']:.1f} A (turn-off overshoot {pc['V_os']:.0f} V at {pc['I_os']:.0f} A, limit {pc['V_limit']:.0f} V). An onset that crosses "
      f"the window turns off at no more than its own peak ({rr['I_off_max']:.0f} A, inside the admissible {pc['I_screen']:.0f} A): a failed "
      "ride-through, not a device over-stress.\n")
    a("**Why the onset forms, and what firmware can do (stiff grid, 0 pu, 125 kW, SIMULATED):** when the grid collapses, C_f discharges "
      "through L2 into the fault within a quarter of the L2-C_f ring (about 27 us) and rings below zero, so L1 sees up to twice the "
      "pre-dip voltage; the bridge keeps its committed command for up to two periods (the sample, then the 1.5 T_s update), and the "
      f"divider ({P['f_vdiv'] / 1e3:.1f} kHz pole) shows the controller only part of the collapse at the first sample. Variants:\n")
    a(tab(("variant", "onset A (averaged + ripple/2)", "against the edge"),
          [(n, f"{v:.0f}", "inside" if v <= rr["thr"] else "TRIP") for n, v in rr["measures"]]))
    a(f"The D-077 statement that no firmware can catch the first-millisecond peak was too strong: a reference freeze or an earlier "
      "ride-through entry changes nothing (the reference is not the bottleneck; the state is entered at the first sample either way), "
      "but the per-sample clamp acts inside the latency chain - set to the dip-time limit plus its margin while the ride-through state "
      "runs, and predicting on the C_f voltage with the divider's known pole inverted, it acts at the first update with the true C_f "
      "voltage. A larger virtual impedance in the first millisecond is the same lever as the dip-time limit (it acts through the "
      "reference) - in grid following there is none. The hardware what-ifs (a faster divider, a lag-free measurement) show that the "
      "divider lag is most of the latency; the inversion recovers it in firmware.\n")
    rob = rr["robust"]
    a(tab(("ADOPTED measure, onset A", "0 pu", "0.1 pu", "0.2 pu", "0.3 pu"),
          [(f"corner {cn}", *[f"{v:.0f}" for v in vals]) for cn, vals in rob.items()] +
          [(f"divider pole x{k}", *[f"{v:.0f}" for v in vals], "-") for k, vals in rr["robust_div"].items()]))
    a("Six dip instants over 60 deg at 0 pu: " + "; ".join(f"{cn} " + ", ".join(f"{v:.0f}" for v in vals) + " A" for cn, vals in rr["instant_scan"].items())
      + f". Worst {rr['worst_onset']:.0f} A: {rr['margin_A']:.0f} A below the edge (rule: >= {A('rt_rule')['margin_A']:.0f} A) - "
      + ("adopted." if rr["adopted"] else "NOT adopted.") + " With it the whole ride-through table (five grids x five depths, 150 ms, then "
      "recovery) stays inside: largest onset " + f"{max(d['pk_on'] for d in rr['rows']):.0f} A, largest in-dip / recovery peak "
      f"{max(max(d['pk_dip'], d['pk_rec']) for d in rr['rows']):.0f} A; no derating is needed at any SCR in the averaged model.\n")
    a("**The enforceable fallback rule** (if the measure is not taken, or the switched model / the bench shows less margin) - for the "
      "D-077 limiter:\n")
    a(tab(("residual voltage", "boundary SCR (rated current passes at or below it)", "onset at the boundary A", "margin at SCR 50 A"),
          [(f"{d['depth']:g} pu", f"{d['scr_b']:.1f}", f"{d['onset_b']:.1f}", f"{d['margin_scr50_A']:.1f}") for d in rr["boundary"]]))
    depths = sorted({d["depth"] for d in rr["table"]})
    a(tab(("grid", *[f"P_max at {x:g} pu kW" for x in depths]),
          [(scr_name(s_), *[f"{next(d['P_max_kW'] for d in rr['table'] if d['scr'] == s_ and d['depth'] == x):.1f}" for x in depths])
           for s_ in rr["table_scr"]]))
    a(f"**Grid-stiffness estimate (firmware):** at connection a {rr['dq_pu']:g} pu reactive-current step (in operation a pseudo-random "
      "sequence of it, correlated over about 10 s in the 1 kHz task); |Z_g| = |dV_G / dI_g| from the terminal-voltage phasor (VG1-3) and "
      "the estimated grid current (i_1 - j w C_eq v_C); SCR = Z_base / |Z_g|. Deterministic error of the method on the model's fixed points: "
      + "; ".join(f"{scr_name(e['scr'])} {e['err_pct']:+.3f} %" for e in rr["estimator"]) + f". Accuracy ASSUMED +/-{100 * rr['acc']:.0f} % "
      f"(the grid's own voltage variation dominates; not simulated). The derating engages at an estimated SCR >= {rr['engage_scr']:.1f} "
      f"(= {rr['scr_b']:.1f} / {1 + rr['acc']:g}); no valid estimate = treated as stiff. Signal at that level: {rr['dV_engage_V']:.2f} V = "
      f"{rr['dV_engage_V'] / rr['lsb_V']:.1f} LSB of the {rr['lsb_V']:.3f} V/LSB channel (V/{rr['div']:.0f}) - the averaging is required.\n")
    a(f"**Installation declaration (fallback):** full ride-through of the profile at rated current where the connection's fault level per "
      f"125 kW module is below {rr['S_sc_engage_MVA']:.2f} MVA (SCR {rr['engage_scr']:.1f} with the estimate's tolerance; {rr['S_sc_b_MVA']:.2f} MVA "
      "at the computed boundary); n modules at one point share it: the figure is the point's fault level divided by n. Above it the table "
      "applies. The reactive-current profile (k = 1.5 up to 1.0 I_r) and every connection-code figure stay FROM MEMORY; the standard "
      "texts (EN 50549-1, VDE-AR-N 4105 / 4110, GB/T 34120) are the gate.\n")
    return L


def rep_6b(P, R):
    """report section 6b: the weak-grid DC-load rejection with the protection in the loop (review R2-04)"""
    dc, pc, L = R["dct"], P["pc"], []
    a = L.append
    a("## 6b. Weak-grid DC-load rejection with the protection in the loop (review R2-04)\n")
    a(f"Section 6's case: the inverter holds the link in CV mode at SCR 5, 750 V, and a 125 kW DC/DC load on the link trips at once (the "
      f"measured DC-current feed-forward sees it within the 11 kHz chain). Now in the loop: the per-sample clamp; the firmware soft limit "
      f"({dc['soft'][0]:.0f} V, {dc['soft'][1]:.0f} us; as drawn a controlled stop, or holding the DC-link loop); the hardware comparator at "
      f"either end of its band ({dc['band'][0]:.0f} / {dc['band'][1]:.0f} V, {dc['resp_us']:.0f} us to gates off) with the ADC PPB backup "
      f"({dc['ppb'][0]:.0f}-{dc['ppb'][1]:.0f} V, {dc['ppb'][2]:g} us); after gates off the diode-bridge tail (the legs' diodes return the L1, "
      "L2 and grid-inductance energy into the two halves; the DC/DC has tripped, no DC-side current; RK4 at 1 us). SIMULATED, averaged.\n")
    rows = []
    for d in dc["rows"]:
        if d["trip"]:
            rows.append((d["case_name"], f"{d['vmax_loop']:.0f}", f"{d['t_off_ms']:.2f} ms by the {d['by']} at {d['v_off']:.0f} V, "
                         f"{d['I_off']:.0f} A", f"{d['V_dev']:.0f} V ({'inside' if d['dev_ok'] else 'ABOVE'} {pc['V_limit']:.0f} V)",
                         f"{d['vdc_pk']:.0f} V (halves <= {d['half_pk']:.0f} V)", f"held at {d['vdc_end']:.0f} V; aux "
                         f"{'LOCKED OUT' if d['aux_lockout'] else 'running'}"))
        else:
            rows.append((d["case_name"], f"{d['vmax_loop']:.0f}", "no trip", "-", "-", f"{d['vdc_end']:.0f} V, recovers" if d.get("recovered") else "-"))
    a(tab(("case", "bus peak before gates off V", "gates off", "device at turn-off (bound: the protection chain's OV-corner deck, "
           "or bus + scaled overshoot)", "bus after the diode dump", "then"), rows))
    a(f"The loop alone reproduces the reviewer's {next(d['vmax_loop'] for d in dc['rows'] if not d['trip']):.0f} V - an uninterrupted recovery the "
      "hardware would not allow. With the protection the trajectory is trip-aware: gates off "
      f"{min(d['t_off_ms'] for d in dc['rows'] if d['trip']):.2f}-{max(d['t_off_ms'] for d in dc['rows'] if d['trip']):.2f} ms after the "
      "DC/DC trip, the diodes add the "
      "inductive energy (the SCR 5 grid inductance carries most of it), the AC contactors open at the next current zero, the module stays "
      "latched (no automatic clear after an OV trip) and the bus stays charged for minutes (bleeders, tau about 5 min); every half stays "
      f"below its film's {P['U_N_half']:.0f} V rating and the device's turn-off stays inside the protection chain's over-voltage corner "
      f"({pc['V_pk_ov']:.0f} V at {pc['V_ov_off']:.0f} V / {pc['I_ov']:.0f} A, from pcs_spec). The bus left behind at the band's top edge "
      f"is {P['aux']['ov'] - max(d['vdc_end'] for d in dc['rows'] if d['trip']):.0f} V below the aux's input lock-out ({P['aux']['ov']:.0f} V, "
      "aux75_spec): a thin margin.\n")
    a("**What firmware can and cannot do.** Can: keep the measured DC-current feed-forward (mandatory, section 6); hold the DC-link loop "
      "through the soft limit in CV mode (as drawn the soft limit is a controlled stop, which removes the only sink - no difference here, "
      "the comparator trips first, but it leaves a charged bus where the loop would have recovered below the band); limit the DC/DC power "
      "it serves at weak grids so that a DC/DC trip stays below the soft limit: " + "; ".join(
          f"{scr_name(d['scr'])} {d['P_max_kW']:.1f} kW" for d in dc["derating"]) + " (the grid-stiffness estimate of section 4c selects the "
      "row). Cannot: absorb the energy - no brake chopper is drawn; the bridge cannot reverse the grid current faster than the voltage "
      "headroom over the SCR 5 grid inductance allows; and once the gates are off the diodes deliver the inductive energy whatever the "
      "firmware does. Hardware alternatives (not taken): a battery on the bus, about 4 x the link capacitance (section 6), a chopper.\n")
    return L


def rep_7c(P, R):
    """report section 7c: the bounded off-grid transients and the declared envelope (review R2-04)"""
    vb, L = R["vfb"], []
    a = L.append
    st = vb["steps"]
    a("## 7c. Off-grid (VF) full-load steps with the plant bounded (review R2-04)\n")
    a("**Bounds now in the loop:** the bridge's output within the firmware's circular modulation limit (u_frac x V_dc,meas, inside the "
      "hexagon the legs can reach; the body diodes clamp the switch nodes, not C_f - C_f can ring above V_dc/2 through L1 until its current "
      "reverses into the link); the per-sample clamp; the DC link as a stiff battery (the PCS-P125 baseline) or as its finite capacitance "
      f"({P['Cdc'] * 1e6:.1f} uF, pcs_spec) with a DC-side source model; the hardware DC over-voltage comparator / PPB and, after gates off, "
      "the diode-bridge tail. **Firmware measures (adopted):** the load-current feed-forward i_1 - C_eq dv_C/dt "
      f"(low-pass {vb['f_io']:.0f} Hz: the highest of the scan meeting the margin rule - PM {R['vff']['pm']:.0f} deg, GM {R['vff']['gm']:.1f} dB; "
      + ", ".join(f"{r['f_io']:.0f} Hz GM {r['gm']:.1f} dB" for r in R["vff_rows"] if not r["ok"]) + " fail it), the over-voltage deadbeat above "
      f"{vb['vclamp_pu']:g} pu, and for a single module the virtual impedance at 0 (paralleled modules keep it for sharing).\n")
    rows = []
    for k, v in st.items():
        t1 = (f"{v['t_v1_ms']:.1f} ms" if v["t_v1_ms"] < 1e3 * (v["t_end_s"] - 0.01) - 1.0 else f"not within the {v['t_end_s']:g} s run") \
            if "t_v1_ms" in v else f"{v['t_v1_s']:.3f} s"
        rows.append((k, f"{v['v_min']:.3f}", f"{v['v_max']:.3f}", f"{v['t_v10_ms']:.2f}", t1,
                     ("inside" if v["env"]["ok"] else "OUTSIDE") if "env" in v else "-",
                     ("inside" if v["itic"]["ok"] else "OUTSIDE") if "itic" in v else "-"))
    a(tab(("step, battery on the DC port (SIMULATED, averaged)", "v_C min pu", "v_C max pu", "within 10 % ms", "within 1 %",
           "declared envelope", "ITIC-style class"), rows))
    env, itic = vb["envelope"], vb["itic"]
    fmt = lambda e: "over " + ", ".join(f"{v:g} pu to {t_:g} ms" if math.isfinite(t_) else f"{v:g} pu after" for t_, v in e["over"]) + \
        "; under " + ", ".join(f"{v:g} pu to {t_:g} ms" if math.isfinite(t_) else f"{v:g} pu after" for t_, v in e["under"])
    a(f"|v_C| is the alpha-beta magnitude (for a three-wire load an upper bound of every instantaneous line-to-neutral value). "
      f"**Declared envelope (single module, 100 % linear step, our commitment):** {fmt(env)}. **ITIC (CBEMA) curve style class, FROM "
      f"MEMORY:** {fmt(itic)} - the standard texts are not on file; IEC 62040-3 classification 1 is not claimed (as remembered it is "
      "tighter in the first milliseconds than a 2 % C_f filter allows at a 100 % step). Paralleled modules (virtual impedance on): the same "
      "first milliseconds, then the plateau the secondary layer restores (section 7b) - ITIC style "
      + ("inside" if all(st[k]["itic"]["ok"] for k in st if k.startswith("design, paralleled")) else "OUTSIDE") + ", declared single-module "
      "envelope " + ("inside" if all(st[k]["env"]["ok"] for k in st if k.startswith("design, paralleled")) else "outside (it is not "
      "declared for them)") + ". **The D-077 figures 0.380 / 2.170 pu were the unbounded averaged model without these measures; they "
      "are superseded** (2.17 pu is outside even the ITIC-style 2.0 pu).\n")
    fo, tc, unc = vb["cap_follow"], vb["cap_tc"], vb["uncoordinated"]
    a(f"**DC bus without a battery** (a DC/DC holds it; the module forms the island): with an ideally coordinated, unidirectional source "
      f"(it follows the bridge's draw and absorbs nothing) a full-load rejection returns {vb['e_ret_J']:.1f} J to the link, which rises to "
      f"{fo['vdc_max']:.0f} V; if the source keeps its pre-step current the link reaches the {P['ov_soft'][0]:.0f} V soft limit after "
      f"{vb['t_c_max_ms']:.2f} ms ({tc['vdc_max']:.0f} V at 0.95 x that, {vb['cap_tc_over']['vdc_max']:.0f} V at 1.05 x): **a DC/DC on such a "
      f"bus must cut its current within {vb['t_c_max_ms']:.2f} ms** (its own bus-voltage limiter or a shared trip line - a system rule). "
      "Uncoordinated (it stops only " + f"{A('dcdc_stop_us'):.0f} us after the PCS trips, ASSUMED): " + "; ".join(
          f"comparator at {hw:.0f} V: gates off {u['t_off_ms']:.2f} ms after the step at {u['v_off']:.0f} V, {u['I_off']:.0f} A (device "
          f"{u['V_dev']:.0f} V), the bus ends at {u['vdc_end']:.0f} V (halves <= {u['half_pk']:.0f} V), aux "
          f"{'LOCKED OUT' if u['aux_lockout'] else 'running'}" for hw, u in unc.items()) + " - the island goes dark, the module latches. "
      "The PCS cannot do better alone: in VF there is no grid to export to and no chopper; the energy is the DC/DC's to withhold.\n")
    a("What a switched simulation would add: the PWM ripple on the deadbeat's prediction, the dead-time error at the rejection, the "
      "modulator's minimum pulses at the limit, the C_f zero sequence on M during the transient. The bench: the measured first-millisecond "
      "excursion against the declared envelope at every corner of the drawn parts.\n")
    return L


def rep_7b(P, R):
    """report section 7b: VF secondary restoration and the accuracy statements; four-wire unbalance pointer"""
    sec, acc, L = R["sec"], R["acc"], []
    a = L.append
    v, f = acc["voltage"], acc["frequency"]
    a("## 7b. VF (off-grid) secondary restoration and the accuracy statements\n")
    a(f"**Structure (firmware).** Above the droop + virtual impedance, two slow integrators: the generator's frequency deviation is "
      "integrated into an offset of the droop (d dw_s/dt = -dw/T) and the sensed C_f-voltage magnitude (5 Hz filtered) into an offset of "
      "the voltage set point (d dV_s/dt = (V_n - |v_C|)/T). Steady state: 50 Hz and V_n exactly (integral action); the droop and the "
      "virtual impedance still act on every transient. VF only: in grid-connected grid forming the grid sets f and V, the "
      "integrators would wind up against it - frozen and ramped out before re-synchronisation. The voltage layer is re-seeded "
      f"downwards when it exceeds the virtual-impedance drop of the present output current by > {sec['reseed_pu']:g} pu (load "
      "rejection), never upwards. Paralleled modules share the restoration over the module CAN (a consensus of the offsets, so that "
      "each module's integrator does not pull the droop's sharing apart - stated, not modelled).\n")
    a(f"**Time constant (firmware parameter): {sec['T_s']:g} s.** Linearised island at rated load (the fixed point exists with the "
      "restoration); rule: the restoration modes real, >= " + f"{A('sec_rule')[0]:g} x slower than the nearest primary mode (the 5 Hz "
      f"measurement filters), moving it by <= {100 * A('sec_rule')[1]:.0f} %, and T >= {A('sec_rule')[0]:g} x the {A('can_cycle_s') * 1e3:.0f} ms "
      "CAN cycle; the smallest value meeting it is taken:\n")
    a(tab(("T s", "restoration modes tau s", "real", "separation from the 5 Hz filters", "filter mode moved %", "least-damped mode",
           "rule"),
          [(f"{d['T_s']:g}", " / ".join(f"{x:.2f}" for x in d["tau_s"]), "yes" if d["real"] else "NO", f"{d['sep']:.1f} x",
            f"{d['shift_pct']:.1f}", f"zeta {d['zeta']:.3f} at {d['f_osc']:.0f} Hz", "pass" if d["ok"] else "fail") for d in sec["scan"]]))
    a("The least-damped mode of the primary loops is the same at every T: the restoration does not reach into the droop or the voltage "
      "loop. (The island's free angle - eigenvalue 1 - is excluded: off-grid there is no angle reference.)\n")
    st = sec["steps"]
    fb = 0.002 * F0
    a(tab(("step (SIMULATED)", "v_C min pu", "v_C max pu", "within 10 % ms", "within +/-1 % s", "f min / max Hz", "within +/-0.2 % s",
           "at the end of the run"),
          [(k, f"{s['v_min']:.3f}", f"{s['v_max']:.3f}", f"{s['t_v10_ms']:.1f}", f"{s['t_v1_s']:.3f}" if abs(s["v_end"] - 1) <= 0.01 else
            "not restored", f"{s['f_min']:+.3f} / {s['f_max']:+.3f}", f"{s['t_f_s']:.3f}" if abs(s["f_end"]) <= fb else "not restored",
            f"{s['v_end']:.4f} pu, {s['f_end']:+.4f} Hz at {s['t_end_s']:g} s") for k, s in st.items()]))
    p0 = st["rated -> 0, primary layer only"]
    a(f"Frequency band +/-0.2 % = +/-{fb:.2f} Hz. The dip ({st['0 -> rated']['v_min']:.2f} pu) and the overshoot "
      f"({st['rated -> 0']['v_max']:.2f} pu) are the primary loops' first milliseconds (the restoration is too slow to touch them); from the "
      f"primary layer's own rated-load voltage the same rejection reaches {p0['v_max']:.2f} pu - L1's current charges C_f before the "
      "inner loop turns it; these runs carry the VF firmware of section 7c (load-current feed-forward, over-voltage deadbeat, clamp), "
      "which set those first milliseconds; the re-seed shortens the tail above +1 % from "
      f"{st['rated -> 0, no re-seed']['t_v1_s']:.2f} s to {st['rated -> 0']['t_v1_s']:.2f} s. Paralleled modules (the virtual "
      "impedance on) are shown; a single module runs without it (section 7c: within 1 % in milliseconds). The bounded plant, the "
      "DC link and the declared envelope: section 7c.\n")
    ss = sec["ss"]
    a("Steady state (Newton fixed point with the restoration): " + "; ".join(
        f"{k}: v_C {d['v_C_pu']:.5f} pu, load terminals {d['v_t_pu']:.5f} pu, frequency {d['df_Hz']:+.1e} Hz" for k, d in ss.items())
      + f" - zero error by construction on the sensed C_f magnitude (the divider's 50 Hz attenuation, "
      f"{1e6 * (1 - 1 / math.hypot(1, P['w0'] * P['tv'])):.0f} ppm, remains); the load "
      "terminals sit below it by the L2 drop at rated load (measuring the restoration on VG1-3 instead removes it).\n")
    a(f"**Voltage accuracy (CALCULATED):** regulation residual {v['residual_pct']:.3f} % + sensing floor (PCS-CTL design check, after the "
      f"two-point calibration: ADC share RSS {v['adc_rss_pct']:.2f} % / worst {v['adc_worst_pct']:.2f} %, divider TCR mismatch "
      f"{v['tcr_pct']:.2f} %) = **{v['bound_worst_pct']:.2f} % worst-case sum** ({v['bound_rss_pct']:.2f} % RSS), no load to rated.\n")
    a(f"**Frequency accuracy (CALCULATED):** the output frequency is the angle generator's: oscillator {f['oscillator']} "
      f"+/-{f['oscillator_ppm']:g} ppm (module BOM; covers initial, temperature, supply and load) + ageing {f['ageing_ppm']:g} ppm "
      f"(ASSUMED) + the 32-bit per-unit accumulator's rounded increment {f['accumulator_ppm']:.2f} ppm + the restoration residual "
      f"{f['residual_ppm']:.1e} ppm = **+/-{f['bound_ppm']:.1f} ppm = +/-{f['bound_pct']:.4f} %**. (A float32 radian accumulator, the "
      f"alternative, would add {f['float32_radian_ppm']:.2f} ppm over one second - also negligible.)\n")
    a("Four-wire 100 % unbalanced load: section 9b (per-phase loops, the N node).\n")
    return L


def rep_8b(P, R):
    """report sections 8b (THDu) and 8c (imbalance)"""
    th, im, hv, L = R["thdu"], R["imb"], R["gfm_harm"], []
    a = L.append
    a("## 8b. THDu on a linear balanced load (VF, off-grid; CALCULATED - analytical, not a switched simulation)\n")
    a("Terms: (a) the carrier groups of regular-sampled double-update PWM (sim/pcs_design.py's exact Fourier series, imported) through "
      "the LCL with the load, at the load terminals; (b) the dead-time error with a ripple-aware shape (it is a square wave only where "
      "the current exceeds half the local ripple; at no load it nearly vanishes) through the closed VF loop; (c) wherever a zero "
      "sequence is modulated (three-wire m > 0.98: min-max; four-wire mode B), its 150 Hz family through the filters' tolerance "
      "mismatch (corner pair 'high' against 'low': L1, C_f and the R_d-C_d branch at the assembly's tolerances) and, three-wire, through "
      "the per-channel gain error into the measured alpha-beta. Three-wire = line-to-neutral of a balanced star load (non-triplen "
      "orders); four-wire = phase-to-N (every order; mode A with the N leg at 50 %, its carrier term included; mode B's carrier "
      "approximated with the N leg at zero reference). h2-h50 plus the carrier groups, against the fundamental.\n")
    a(f"Voltage loop: the design (PR at h1) and the option with h5 / h7 resonant terms (lead {', '.join(f'h{h} {v:.0f} deg' for h, v in hv['phi'].items())}, "
      f"K_h {hv['Kh']:.1f} S/s): margin rule met (PM {hv['pm']:.0f} deg, GM {hv['gm']:.1f} dB), but their closed-loop modes decay at "
      + "/".join(f"{-x:.1f}" for x in hv["decay"].values()) + f" 1/s against the {-hv['decay_need']:.1f} 1/s the current loop's rule asks: "
      + ("adopted." if hv["adopted"] else "not adopted - and not needed (below).") + "\n")
    rows = []
    for w in ("3W", "4W"):
        for ld in ("rated", "50 %", "no load"):
            for ln in th["loops"]:
                for dt in dict.fromkeys(r["dead_time"] for r in th["rows"]):
                    sel = [r for r in th["rows"] if r["wire"] == w and r["load"] == ld and r["loop"] == ln and r["dead_time"] == dt]
                    r = max(sel, key=lambda x: x["thdu_pct"])
                    rows.append((w, ld, ln, dt, f"{r['thdu_pct']:.2f}", f"{r['vdc']:.0f} V ({r['mode']})", f"{r['carrier_pct']:.2f}",
                                 f"{r['dead_time_pct']:.2f}", f"{math.hypot(r['leak_pct'], r['mismatch_pct']):.2f}",
                                 "; ".join(f"{n} {v:.2f} %" for n, v in r["dominant"])))
    a(tab(("wire", "load", "voltage loop", "dead time", "THDu % (worst V_dc)", "at", "carrier %", "dead time %", "zero sequence %",
           "dominant terms"), rows))
    pv = {(w, r["vdc"]): max(x["thdu_pct"] for x in th["rows"] if x["wire"] == w and x["vdc"] == r["vdc"] and x["loop"] == "PR h1")
          for w in ("3W", "4W") for r in th["rows"] if r["wire"] == w}
    a("Worst over the load points per DC voltage (design loop, dead time not compensated): " + "; ".join(
        f"{w} {v:.0f} V {x:.2f} %" for (w, v), x in pv.items()) + " - the zero-sequence term exists only where a zero sequence is "
      f"modulated (three-wire below {max(r['m'] * r['vdc'] for r in th['rows'] if r['wire'] == '3W') / 0.98:.0f} V at rated load, where "
      f"m > 0.98; four-wire mode B below {P['fw4']['modeA_from']:.0f} V).\n")
    d = th["dc"]
    a(f"DC component (separately, not part of THDu): the DC-component regulator holds it at its measurement floor, "
      f"{d['dc_V']:.2f} V ({d['dc_pct_Un']:.2f} % of Un; divider TCR mismatch at 750 V, pcs_spec) against the {d['limit_V']:.2f} V "
      "(0.5 % Un) limit; the ADC gain drift of the two channels is the open term of that row. What a switched model would add: the "
      "dead-time error's dependence on the commutation (device capacitance, current-dependent switching times) and the minimum pulse "
      "near m = 1, the ripple's sampling and aliasing in the ADC, the device voltage drops (a resistive term), the interaction of the "
      "carrier sidebands with the loop, and the N leg's own switching in four-wire mode B.\n")
    a("## 8c. Output voltage imbalance on a linear balanced load (CALCULATED)\n")
    b = im["bound"]
    p50 = im["per_freq"]["50 Hz"]
    a(f"The loops regulate the measured voltages exactly at the fundamental (resonant terms at +/-50 / 60 Hz), so the output carries the "
      f"inverse of each channel's gain and phase error. Gain: +/-{im['gain_pct']:.2f} % per channel after the two-point calibration (the "
      "control board's ADC share + divider TCR, every term independent per channel - the reference drift is counted although the three "
      "ADCs may share it). Phase: the drawn anti-alias poles - PCS-PWR divider R_th "
      f"{im['divider']['R_th_ohm'] / 1e3:.2f} k (+/-{100 * im['divider']['tol_R']:g} %) with {im['divider']['C_nF']:g} nF "
      f"(+/-{100 * im['divider']['tol_C']:g} % C0G), PCS-CTL charge bucket {im['bucket']['R_ohm']:.0f} R (+/-{100 * im['bucket']['tol_R']:g} %) "
      f"with {im['bucket']['C_nF']:g} nF (+/-{100 * im['bucket']['tol_C']:g} %): common lag {p50['lag_nom_deg']:.3f} deg at 50 Hz "
      f"(no effect on the displacement), per-channel spread +/-{p50['dphi_deg']:.4f} deg (50 Hz) / "
      f"+/-{im['per_freq']['60 Hz']['dphi_deg']:.4f} deg (60 Hz); VC1-3 convert simultaneously "
      f"({'yes' if im['vc_round_simultaneous'] else 'NO'}: no skew); the software angle generator's float32 constants "
      f"{im['generator_deg']:.1e} deg. Worst over every sign corner, 50 and 60 Hz:\n")
    a(tab(("build", "loop", "amplitude: largest deviation from the mean", "displacement: largest deviation from 120 deg",
           "negative / positive sequence"),
          [("three-wire (line-to-line)", "alpha-beta PR", f"+/-{b['3W']['amp_pct']:.2f} %", f"120 +/-{b['3W']['angle_deg']:.2f} deg",
            f"{b['3W']['vuf_pct']:.2f} %"),
           ("four-wire (phase-to-N)", "per-phase PR", f"+/-{b['4W']['amp_pct']:.2f} %", f"120 +/-{b['4W']['angle_deg']:.3f} deg",
            f"{b['4W']['vuf_pct']:.2f} %")]))
    a("Three-wire: the gain errors rotate the line-to-line phasors (the zero sequence of the measured set is free), so the angle term "
      "there comes from the gains, not the filters. Regulation residual at the fundamental: zero (resonant terms on both sequences / "
      "per phase).\n")
    return L


def rep_9b(P, R):
    """report section 9b: the four-wire neutral leg and the per-phase loops"""
    nl, L = R["nleg"], []
    a = L.append
    a("## 9b. Four-wire: the neutral leg and the per-phase loops (CALCULATED / SIMULATED, linear)\n")
    a("Same parts (asserted from pcs_spec four_wire): L_N = the L1 part, C_fN = C_f, the same R_d-C_d branch, IL4 and VGN through the "
      "same sensor and divider chains. Not the same plant beyond N_F: the N path has no L2 of its own; the neutral current returns "
      "through the load and the phases' L2 to their C_f star on M (off-grid) or through the grid's neutral and the phases' L2 in "
      "parallel (grid-connected, the phases current-controlled: their C_f star is the only return to M). Margins, worst over the "
      "nom / low / high / slow corners (the phases' C_f in the path follow the same tolerance), for the phases' grid-following PR "
      "current controller alone on IL4 and for the VF cascade (P-only inner loop with the phases' K_p and SOGI feed-forward, the "
      "phases' C_f-voltage PR on VGN - VA):\n")
    a(tab(("N-node termination", "PR current loop alone: PM / GM / max |pole|", "VF cascade: PM / GM / max |pole|", "cascade -3 dB Hz"),
          [(r["case"], f"{r['i_pm']:.1f} / {r['i_gm']:.1f} / {r['i_rho']:.5f} ({'pass' if r['i_ok'] else 'FAIL'})",
            f"{r['v_pm']:.1f} / {r['v_gm']:.1f} / {r['v_rho']:.5f} ({'pass' if r['v_ok'] else 'FAIL'})", g1(r["v_bw"], 0))
           for r in nl["rows"]]))
    ph = nl["phase_ref"]
    held = next(r for r in nl["rows"] if "held" in r["case"])
    a(f"The phases for comparison: current loop (grid-connected) worst PM {ph['i_pm']:.1f} deg / GM {ph['i_gm']:.1f} dB; VF voltage loop "
      f"worst PM {ph['v_pm']:.1f} deg / GM {ph['v_gm']:.1f} dB, -3 dB {ph['bw']['nl']:.0f} Hz (no load) / {ph['bw']['rl']:.0f} Hz (rated). "
      "Where the N node sees a resistive path to a held voltage (the loaded phase), it IS the phase plant and the numbers are the "
      f"phases' (PR alone PM {held['i_pm']:.0f} deg; cascade {held['v_pm']:.0f} deg / {held['v_gm']:.1f} dB, {held['v_bw']:.0f} Hz = the "
      "rated-load phase loop). Wherever the N node closes only through capacitors (open terminal, balanced load, grid-connected zero "
      "sequence) the grid-following PR alone has no current path at the fundamental but its own and fails; the N leg therefore runs "
      "the VF cascade in every mode, holding v(N_F - M) - which meets the rule at every termination.\n")
    a("**Per-phase voltage loops (four-wire, mode A):** each phase's C_f voltage against M is its phase-to-N voltage while the N leg "
      "holds N_F on M; the per-phase PR is the alpha-beta design per axis - same margins, same bandwidth "
      f"({ph['bw']['nl']:.0f} Hz no load, {ph['bw']['rl']:.0f} Hz rated); amplitude restoration per phase (the 7b voltage layer on each "
      "phase's RMS).\n")
    ex = nl["excursion"]
    ld = R["sec"]["steps"]["0 -> rated"]
    a(f"**100 % unbalanced load (linear, SIMULATED):** a rated resistive load switched on between a held phase at its peak and N "
      f"draws up to {ex['iN_pk']:.0f} A through the N leg; the N node moves by {ex['pk_pu']:.2f} pu at most and is within 1 % after "
      f"{ex['t1_ms']:.0f} ms, {ex['end_pu']:.1e} pu after 80 ms (the 50 Hz resonant term). The unloaded phases' phase-to-N voltages "
      f"carry that excursion; the loaded phase also its own dip, which per phase is the balanced step's ({ld['v_min']:.2f} pu, section "
      "7b). Steady state: every phase-to-N RMS restored by its own integrator - the imbalance is the sensing bound of 8c. Not "
      "simulated: the per-phase virtual reactance's quadrature generator, the droop seeing one third of the power, the switched N "
      "leg, the combined nonlinear transient.\n")
    mb = nl["modeB"]
    a(f"**Mode B zero-sequence feed-forward:** below the mode-A window the min-max zero sequence u_0 of the phase references (at "
      f"{mb['vdc']:.0f} V and m {mb['m']:.3f}: {mb['u0_150_V']:.0f} V at 150 Hz) is added to all four legs and is the N leg's reference "
      "(feed-forward to its modulator, the voltage loop correcting the rest). It cancels in phase-to-N to the filters' mismatch: at "
      f"opposite tolerance corners {mb['resid_150_pct']:.3f} % at 150 Hz but {mb['resid_hmax_pct']:.2f} % at h{mb['h_max']} near the "
      f"L1-C_f resonance, {mb['resid_pct']:.2f} % over h2-h50 (in the THDu of 8b); a pure third-harmonic zero sequence would leave only "
      "the 150 Hz term (firmware option).\n")
    dc = nl["dc"]
    a(f"**DC-component regulators:** u_0 has no DC ({mb['u0_mean_V']:.0e} V over a period), so mode B does not disturb them. The "
      "voltage PR has no gain at DC beyond K_pv: a half-wave load's "
      f"{dc['I_dc_A']:.0f} A DC through the N leg would shift N_F by {dc['offset_without_V']:.0f} V ({dc['I_dc_A']:.0f} A / "
      f"{dc['K_pv_S']:.3f} S) - so the N leg needs its own DC regulator (one-cycle mean of VGN - VA -> integrator -> offset on its "
      "reference) beside the phases' (one-cycle mean of each phase-to-N voltage): four integrators on four independent DC quantities "
      "(three phase-to-N, one N-to-M), none fighting another. Firmware rows in section 9.\n")
    return L


def rep_9c(P, R):
    """report section 9c: the coupled four-wire cases (review R2-13)"""
    n4, L = R["n4"], []
    a = L.append
    a("## 9c. Four-wire: coupled cases in a per-phase averaged model (review R2-13)\n")
    a(f"A per-phase stationary-frame model (ph4_run): three phase legs and the N leg (L_N = the L1 part into N_F, C_fN + its branch to M), "
      "every load returning to N_F, the grid (where connected) through L_g with its star solid at N_F (ASSUMED); the three-wire design's "
      "blocks applied per phase - the C_f-voltage PR on each phase-to-N voltage with the load-current feed-forward and the over-voltage "
      "deadbeat (section 7c), the reference limiter at the 200 ms tier, the P-only inner loop with the SOGI feed-forward, the per-sample "
      "clamp on the divider-inverted voltage (section 4c) on all four legs; the N leg's cascade of section 9b; DC regulators on the three "
      f"phase-to-N means and on N_F (T {n4['dc_reg_T_s']:g} s); each leg within m_A V_dc/2 (mode A, m_A {n4['m_A']:.4f}); the DC link a battery "
      f"at 750 V; the limit timer trips at {n4['t_lim_s'] * 1e3:.0f} ms. SIMULATED, averaged (+ripple/2 on the current peaks).\n")
    rows = []
    for k, d in n4["cases"].items():
        amp = " / ".join(f"{d[x]['amp_min']:.3f}-{d[x]['amp_max']:.3f}" for x in ("va", "vb", "vc"))
        rows.append((k, amp, f"{max(d[x]['dev_pk'] for x in ('va', 'vb', 'vc')):.2f} ({max(d[x]['dev10_ms'] for x in ('va', 'vb', 'vc')):.1f} ms > 0.1)",
                     f"{d['vn_pk']:.2f}", " / ".join(f"{d['i_pk'][x]:.0f}" for x in ("ia", "ib", "ic", "iN")),
                     "may trip" if d["window"] else "inside", f"{max(d[x]['amp1_ms'] for x in ('va', 'vb', 'vc')):.0f}"))
    a(tab(("case", "phase-to-N amplitude a / b / c pu (one-cycle DFT)", "largest instantaneous deviation pu (time above 0.1)",
           "N_F swing pu", "peaks a / b / c / N A", "window", "all within 1 % after ms"), rows))
    hw = {k: v for k, v in n4["cases"].items() if k.startswith("half-wave")}
    sh = next(v for k, v in n4["cases"].items() if k.startswith("line-to-neutral"))
    a("**Half-wave load** (phase a, " + f"{n4['R_hw']:.3f} ohm through an ideal diode, the declared {P['fw4']['hw_pk_A']:.0f} A peak): "
      + "; ".join(f"regulators T {v['T_dc']:g} s: DC component of phase a's phase-to-N voltage up to {v['va']['dc_pk']:.1f} V, back below "
                  f"{v['dc_limit_V']:.2f} V (0.5 % Un) after {v['t_dc_ms']:.0f} ms, {v['dc_end_V']:+.2f} V at the end; N_F's own DC "
                  f"{v['vn_dc_end_V']:+.1f} V; L_N peak {v['LN_pk']:.0f} A against the part's {v['LN_lim']:.0f} A; DC through the N leg "
                  f"{v['I_dc']:.0f} A" for v in hw.values()) + ". The static declaration holds once the regulators settle; the transient DC "
      "is the regulator's. The declaration's L_N limit ('DC + AC + ripple/2 <= the L1 part's continuous peak') is a continuous figure: "
      "the faster regulator's transient peak " + ("exceeds it by " if max(v["LN_pk"] for v in hw.values()) > P["fw4"]["LN_pk_A"] else
      "stays within it, worst ") + f"{abs(max(v['LN_pk'] for v in hw.values()) - P['fw4']['LN_pk_A']):.0f} A for the regulator's settling time"
      + (f", far below L1's saturation knee ({P['pc']['I_knee']:.0f} A hot, pcs_spec protection_chain)" if P["pc"].get("I_knee") else "")
      + " - a trade-off between the DC component's duration and the L_N peak, firmware parameter.\n")
    a(f"**Line-to-neutral short** (phase a, {A('r_sc_ohm') * 1e3:.0f} mOhm + {A('l_sc_H') * 1e6:.0f} uH to N, from a balanced rated load): the "
      f"first peaks {sh['i_pk']['ia']:.0f} A (phase) / {sh['i_pk']['iN']:.0f} A (N leg) against the window's {P['win']['lo']:.0f} A low "
      "edge - " + ("inside, thin" if not sh["window"] else "AT it: a unit at the band's low edge may trip on the first peak (a hardware trip, "
                   "then the module stops)") + f"; the limiter then holds {sh['ia_hold']:.0f} A (phase) / {sh['iN_hold']:.0f} A (N), the "
      f"healthy phases' phase-to-N amplitudes stay >= {min(sh['vb_amp'], sh['vc_amp']):.3f} pu, and the limit timer trips at "
      + (f"{sh['trip_ms']:.0f} ms" if sh["trip_ms"] else "NOT reached") + " (firmware, gates off; the battery takes the inductors' energy).\n")
    a("The GFM -> GFL row ends 2 % high on phase a by design: the grid sits 5 % high at the permissive edge and grid following "
      "lets the terminals follow it. The short's '1 %' column is the run end (the short is not cleared; the timer trips).\n")
    a("**Boundaries** (what this model does not carry): no switching (ripple added to the peaks; the N leg's switching and its "
      "common-mode coupling not modelled), linear magnetics (L_N and L1 below saturation), the PLL omitted in grid following (the grid's "
      "angle used), no droop, virtual impedance or secondary layer in these four-wire runs (fixed frequency; the per-phase virtual "
      "reactance needs a quadrature generator), mode A only (mode B's zero-sequence feed-forward not run), the grid neutral solid at N_F, "
      "the CM choke's fourth bar not in the N path, an ideal half-wave diode, the DC link stiff. The N-leg feed-forward row is an option "
      "(not adopted: no linear margin check on the coupled plant).\n")
    return L


def rep_9d(P, R):
    """report section 9d: the AC start in the checked machine, executed with fault injection (review R2-14)"""
    sm, L = R["smx"], []
    a = L.append
    a("## 9d. AC start and transfer: the checked machine, executed with fault injection (review R2-14)\n")
    a("The transition table now carries the AC-start states of pcs_spec ac_start (AC_TEST: welded-precharge check and relay test; "
      "AC_PRECHARGE; AC_CLOSE_K2 / AC_CLOSE_K1, one coil at a time; RECTIFY: the bridge raises the link; DC_MATCH: the K_PRE route; "
      "AC_STOP; RETRY_WAIT; LOCKOUT) with outputs PWM, K_ACPRE, K_PRE, K_DC, K_AC2, K_AC1; the exhaustive check over every (state, event) "
      "pair: " + "; ".join(f"{k}: {'holds' if v else 'FAILS'}" for k, v in R["sm"].items()) + ". (While extending it the check found that a "
      "stop out of RECTIFY routed through CONTROLLED STOP would have closed the DC contactor - CONTROLLED STOP holds K_DC; the AC start "
      "has its own AC_STOP.)\n")
    a("**Executed against a plant** (sm_exec, the firmware's slow task): the grid and its tap (line peak - 2 V_F), the battery terminal, "
      "the link (C_dc, bleeders, the aux's draw from the link while it feeds it), K_ACPRE / K_PRE / K_B / K2 / K1 with pull-in and release "
      f"times (ASSUMED: {', '.join(f'{k} {v[0] * 1e3:.0f} / {v[1] * 1e3:.0f} ms' for k, v in sm['relay_times_s'].items())}), VG / VC, the body "
      "diodes, the bridge as a rectifier, the aux's brown-in / brown-out, start-up and hold-up (aux75_spec). Safety checks on the actual "
      "relay states every tick: S1 the precharge resistor charging the battery, S2 an AC close onto a link below the permissive (inrush "
      "through the body diodes), S3 the DC contactor on a battery below the rectification threshold, S4 more than the allowed precharge "
      "attempts in 10 min. Each fault strikes at the entry of the state named; 'as first specified (D-074, before the H1-H5 repairs of D-079)' = pcs_spec read literally, 'repaired' = "
      "with H1-H5:\n")
    a(tab(("fault", "at", "as first specified (D-074, before the H1-H5 repairs of D-079): outcome", "breaches", "repaired: outcome", "breaches"),
          [(r["fault"], r["at"], r["spec"], "; ".join(b.split(" ", 1)[0] for b in r["breach_spec"]) or "-", r["fixed"],
            "; ".join(b.split(" ", 1)[0] for b in r["breach_fixed"]) or "-") for r in sm["rows"]]))
    a("**Sequence holes found (fed back as firmware rows, section 9 table):** " + " ".join(f"**{h}** {t}." for h, t in sm["fixes"].items())
      + " As specified the sequence breaches " + "; ".join(sm["holes"]) + "; with the repairs " +
      ("no safety check is breached in the modelled set." if not sm["residual"] else "still: " + "; ".join(sm["residual"])) + "\n")
    a("**Residual (stated, not modelled):** a K_ACPRE that welds while opening is invisible while the link sits above the tap peak (the "
      "tap's diodes block); it is found by the welded-precharge check at the next start, and with H4 it can never charge the battery (the "
      "battery is above the grid peak whenever K_B is closed); a K_B welded with the battery then appearing is outside this sequence (the "
      "DC-start path's own checks); the relay timings and the 40 W aux draw are ASSUMED until the RFQ coil data exist.\n")
    return L


def write_report(P, G, R, spec, t_run):
    K, L = G["K"], []
    a = L.append
    a("# PCS-P125 control study: current loop, PLL, DC link, grid forming, firmware\n")
    a("**CALCULATED by sim/pcs_control.py - not measured.** Sampled averaged models (no switching ripple in the loop, ideal "
      "devices), linear analyses and sample-by-sample simulations of the firmware structure; nothing is bench-validated. "
      f"Every number below is written by the script (run time {t_run:.0f} s). Re-run: `caffeinate -i .venv/bin/python "
      "sim/pcs_control.py`. Hand-off: `pcs_control_spec.json`. Requirements: REQUIREMENTS AC-01, AC-02, SRC-7; closes the "
      "sampled-loop part of review finding PCM-21 / open item E1.\n")
    worst = lambda rows, k: min(r[k] if k != "gm" else min(r["gm"], r["gm_lo"]) for r in rows)
    pas = [r for r in R["table"] if r["damp"] == "passive" and r["cn"] in CORNERS]
    pl5 = next(s for s in R["pll"]["sweep"] if s["scr"] == 5.0 and s["P"] > 0)
    a("## What the study establishes\n")
    a(f"- **Current loop (grid following):** alpha-beta PR on the converter current (the sensor between L1 and C_f), "
      f"crossover {K['fc']:.0f} Hz on the stiff grid (K_p {K['Kp']:.2f} ohm), resonant terms at h1 + "
      f"{', '.join('h%d' % h for h in K['harm'])}; with the drawn R_d-C_d branch it meets PM >= {RULE_PM:.0f} deg and GM >= "
      f"{RULE_GM:.0f} dB from stiff to SCR {min(s for s in A('scr') if s):g} at every inductance, capacitance and sensor-delay "
      f"corner: worst PM {worst(pas, 'pm'):.1f} deg, worst GM {worst(pas, 'gm'):.1f} dB. The crossover band that meets the rule "
      f"is {R['band'][0]:.0f}-{R['band'][1]:.0f} Hz (delay-only limit {R['f_delay']:.0f} Hz).")
    ns = [r for r in R["table"] if r["damp"] == "none" and r["scr"] is None and r["cn"] in CORNERS]
    nsn = next(r for r in ns if r["cn"] == "nom")
    a(f"- **The passive branch is {'not ' if all(r['ok'] for r in ns) else ''}required:** without it the stiff grid "
      f"{'meets' if all(r['ok'] for r in ns) else 'fails'} the rule (resonance {nsn['f_res'] / 1e3:.2f} kHz against f_s/6 = "
      f"{P['fs'] / 6e3:.2f} kHz; worst PM {min(r['pm'] for r in ns):.1f} deg, largest |pole| {max(r['rho'] for r in ns):.5f}); "
      "capacitor-current active damping from the sensed C_f voltage "
      + ("meets the rule at some gain (section 2)." if any(s["ok"] for s in R["ad_scan"]) else
         f"cannot replace it at any gain tried (the {P['f_vdiv'] / 1e3:.1f} kHz divider pole and the delays make it negative "
         "damping at the stiff-grid resonance)."))
    a(f"- **PLL:** SRF-PLL at {G['f_pll']:.0f} Hz has PM {pl5['pm_design']:.0f} deg / GM {pl5['gm_design']:.1f} dB at SCR 5 and "
      f"125 kW export; that case loses the margin rule above {g1(pl5['f_max'], 0)} Hz and is unstable from about "
      f"{g1(pl5['f_unstable'], 0)} Hz (the PLL-induced negative resistance of the reference rotation).")
    a(f"- **DC link:** held at {G['f_dc']:.0f} Hz crossover with the measured DC-current feed-forward - mandatory: without it a "
      f"constant-power DC/DC load is unstable at every SCR; large steps on the {P['Cdc'] * 1e3:.2f} mF link need a DC/DC power "
      "ramp and a coordinated trip at weak grids (section 6).")
    gv, sh = R["gv"], R["gfm"]["short"]
    bwv = {m["kind"]: m["f_bw"] for m in gv["ev"] if m["cn"] == "nom"}
    a(f"- **Grid forming:** droop + virtual impedance + C_f-voltage PR around a P-only current loop, voltage gains from a "
      f"two-axis scan (crossover parameter {gv['fcv']:.0f} Hz, PR zero at 1/{gv['ratio']:g} of it): closed-loop -3 dB "
      f"{bwv['nl']:.0f} Hz at no load, {bwv['rl']:.0f} Hz at rated load; grid-connected stable from stiff to SCR 5 (least "
      f"damping ratio {min(d['zeta'] for d in R['gfm']['lin']):.2f}); under a terminal short the limiter holds "
      f"{sh['i_hold']:.0f} A pk (limit {P['I_lim']:.0f} A), first peak {sh['pk']:.0f} A "
      f"{'below' if sh['pk'] < P['win']['lo'] else 'INSIDE'} the window band ({P['win']['lo']:.0f}-{P['win']['hi']:.0f} A); "
      "transfer sequence and permissives in section 7.")
    a("- **Firmware:** sampling plan, ISR budget, limits, anti-islanding methods and a protection state machine whose "
      "transition table passes an exhaustive invariant check (section 9).")
    rt, sec, acc, th, im, nl = R["rt"], R["sec"], R["acc"], R["thdu"], R["imb"], R["nleg"]
    trip = [d for d in rt["rows"] if d["trip"]]
    a(f"- **Ride-through (section 4b):** a ride-through state (dip-time limit {P['lvrt_ilim'] / P['I_rated']:.1f} I_r, PLL free-running "
      f"below {A('pll_freeze_pu')} pu) and a per-sample clamp at {P['iclamp']:.0f} A hold every in-dip and recovery peak at <= "
      f"{max(max(d['pk_dip'], d['pk_rec']) for d in rt['rows']):.0f} A from stiff to SCR 5 and 0 to 0.5 pu; "
      + (f"the onset at a stiff connection still reaches {max(d['pk_on'] for d in trip):.0f} A for residual voltages <= "
         f"{max(d['depth'] for d in trip):g} pu with that limiter; section 4c: a ride-through clamp level with the divider pole inverted "
         f"holds it at {R['rtr']['worst_onset']:.0f} A at the worst corner, so no derating is needed (the SCR boundary "
         f"{R['rtr']['scr_b']:.1f}, the estimator and the derating table are the specified fallback)." if trip else
         "no studied onset reaches the window."))
    a(f"- **VF accuracy (section 7b):** secondary restoration (T {sec['T_s']:g} s) returns the voltage within +/-1 % in "
      f"{sec['steps']['0 -> rated']['t_v1_s']:.2f} / {sec['steps']['rated -> 0']['t_v1_s']:.2f} s and the frequency within +/-0.2 % in "
      f"{sec['steps']['0 -> rated']['t_f_s']:.2f} / {sec['steps']['rated -> 0']['t_f_s']:.2f} s after 0 -> rated / rated -> 0 "
      f"(dip {sec['steps']['0 -> rated']['v_min']:.2f} pu, overshoot {sec['steps']['rated -> 0']['v_max']:.2f} pu, averaged model); "
      f"voltage accuracy {acc['voltage']['bound_worst_pct']:.2f} % worst-case sum, frequency +/-{acc['frequency']['bound_ppm']:.0f} ppm.")
    tw = {w: max(r["thdu_pct"] for r in th["rows"] if r["wire"] == w and r["loop"] == "PR h1") for w in ("3W", "4W")}
    a(f"- **THDu (section 8b, analytical):** <= {tw['3W']:.2f} % three-wire, <= {tw['4W']:.2f} % four-wire on a linear balanced load "
      "(worst load point and DC voltage, dead time not compensated); output imbalance (8c) +/-"
      f"{im['bound']['3W']['amp_pct']:.2f} % and 120 +/-{im['bound']['3W']['angle_deg']:.2f} deg three-wire, +/-{im['bound']['4W']['amp_pct']:.2f} % "
      f"and 120 +/-{im['bound']['4W']['angle_deg']:.3f} deg four-wire.")
    a(f"- **Four-wire N leg (section 9b):** the same parts give the same loop only where the N node sees a held phase; on its "
      "capacitive terminations the grid-following PR alone fails, the VF cascade passes everywhere (worst PM "
      f"{min(r['v_pm'] for r in nl['rows']):.0f} deg, GM {min(r['v_gm'] for r in nl['rows']):.1f} dB); a 100 % unbalanced load step "
      f"moves the N node by {nl['excursion']['pk_pu']:.2f} pu for {nl['excursion']['t1_ms']:.0f} ms (linear); coupled nonlinear cases in 9c.")
    tl, vb_, dct_, sm_ = R["tol"], R["vfb"], R["dct"], R["smx"]
    des_ = [v for k, v in vb_["steps"].items() if k.startswith("design, single")]
    a(f"- **Tolerances (2b):** C_f, C_d, R_d, L1, L2 at the drawn assembly's tolerances - {tl['factorial']['n']} factorial + "
      f"{tl['random']['n']} random cases all meet the rule (worst PM {min(tl['factorial']['pm'], tl['random']['pm']):.1f} deg, GM "
      f"{min(tl['factorial']['gm'], tl['random']['gm']):.1f} dB).")
    a(f"- **Bounded transients (6b, 7c):** off-grid full-load steps {min(d['v_min'] for d in des_):.2f} / {max(d['v_max'] for d in des_):.2f} pu at "
      f"the worst corner, within 10 % after <= {max(d['t_v10_ms'] for d in des_):.1f} ms, inside the declared envelope (the D-077 "
      f"0.38 / 2.17 pu superseded); a weak-grid DC-load rejection trips the DC over-voltage and leaves the bus at "
      f"{min(d['vdc_end'] for d in dct_['rows'] if d['trip']):.0f}-{max(d['vdc_end'] for d in dct_['rows'] if d['trip']):.0f} V (trip-aware); "
      f"a DC/DC on a battery-less bus must cut within {vb_['t_c_max_ms']:.2f} ms of an AC load rejection.")
    a(f"- **AC start (9d):** in the checked machine and executed with fault injection: {len(sm_['rows'])} cases, {len(SM_FIXES)} sequence holes "
      "found and repaired (firmware rows H1-H5), " + ("no breach left." if not sm_["residual"] else "residual breaches listed."))
    mm = [h[0] for h in R["hw"] if h[3].startswith("MISMATCH")]
    a(f"- **Drawn hardware:** {len(mm)} mismatches (section 10): " + "; ".join(mm) + ".\n")
    a("## What it does not establish\n")
    a("- No switched model: PWM ripple is added to peaks as +ripple/2, dead time is a voltage disturbance, no device or "
      "sensor nonlinearity. THDi is an estimate from the linear closed loop with assumed background distortion, not a "
      "measurement or a switched simulation; THDu (8b) is analytical in the same sense.")
    a("- No standards text: grid-code limits (ROCOF, phase jump, LVRT, islanding times) are quoted from memory and labelled; "
      "no compliance is claimed, in particular not IEC 62116 anti-islanding (not simulated).")
    a("- Not covered: a switched four-wire model (9c is averaged per phase, its boundaries listed there), parallel units (the "
      "restoration consensus over the module CAN is stated, not modelled), LVRT reactive-current injection profiles (the "
      "ride-through study holds the power reference), the AC start's electrical transients (9d executes the sequence against a "
      "plant at the slow-task rate; the rectifier stage after it is this study's DC-link loop), common-mode/leakage control (the "
      "C_f star on the DC midpoint is common mode only), switching-frequency interactions, ADC noise and quantisation, sensor "
      "offsets.\n")
    L.extend(rep_r2(P, R, spec))
    a("## 1. Inputs and assumptions\n")
    a(f"Read at run time: `{os.path.relpath(SPEC, ROOT)}` (L1 {P['L1'] * 1e6:.0f} uH, C_f {P['Cf'] * 1e6:.0f} uF, L2 "
      f"{P['L2'] * 1e6:.1f} uH, R_d {P['Rd']:.1f} ohm + C_d {P['Cd'] * 1e6:.0f} uF; f_sw {P['fsw'] / 1e3:.0f} kHz, sampling "
      f"{P['fs'] / 1e3:.0f} kHz = double update, delay 1.5 T_s = {1.5e6 * P['T']:.2f} us confirmed; current tiers "
      f"{', '.join(f'{k} {v:g} A' for k, v in P['tiers'].items())}; ripple {P['ripple_pp']:.1f} A pp; C_dc {P['Cdc'] * 1e3:.3f} mF "
      f"= {P['Cdc_src']}; sync permissive {P['sync'][0]:.0f} % / {P['sync'][1]:.0f} deg); magnetics rev {P['rev'][0]}/{P['rev'][1]} "
      f"(L1 R_dc {P['R1'] * 1e3:.2f} mOhm, L2 {P['R2'] * 1e3:.2f} mOhm at 110 C; -10 % parts at the highest current "
      f"{P['L1_lo'] * 1e6:.1f} / {P['L2_lo'] * 1e6:.2f} uH; CM-choke DM leakage {P['Ldm'] * 1e6:.2f} uH added to L2); PCS-CTL / "
      f"PCS-PWR design checks (current chain {P['ti'] * 1e6:.2f} us = the drawn chain {P['ti_chk'] * 1e6:.2f} us with "
      f"PCS-CTL's ASSUMED {P['t_sensor_chk'] * 1e6:.1f} us sensor replaced by the frozen part's {P['t_sensor'] * 1e6:.1f} us "
      "max step (sensor source: " + P["sensor_src"] + "); "
      f"C_f/grid voltage dividers {P['f_vdiv'] / 1e3:.1f} kHz; DC-link divider {P['t_vdc'] * 1e6:.0f} us; DC current "
      f"{P['f_ib'] / 1e3:.1f} kHz; phase window {P['win']['lo']:.0f}-{P['win']['hi']:.0f} A; CMPSS {P['cmpss']['lo']:.0f}-"
      f"{P['cmpss']['hi']:.0f} A; dead time {P['dt_gate_ns'][0]:.0f}-{P['dt_gate_ns'][1]:.0f} ns). Corners (every part at the drawn "
      f"assembly's tolerance, review R2-01: C_f +/-{P['tol']['Cf']:.0%}, C_d +/-{P['tol']['Cd']:.0%}, R_d +/-{P['tol']['Rd']:.0%}): nom; low = -10 % "
      f"parts at the trip current and C_f -5 %; high = +10 % at 0 A and C_f +5 %; slow = low with twice the current-chain "
      "delay; env = the pcs_spec L(I) envelope (sensitivity only). R_dc only: the HF winding resistance would add damping "
      "and is not credited. Stiff = L_g 0.\n")
    a(tab(("assumption", "value", "basis (ASSUMED unless stated)"),
          [(k, v[0] if not isinstance(v[0], dict) else ", ".join(f"{a}: {b}" for a, b in v[0].items()), v[1]) for k, v in ASSUME.items()]))
    a("## 2. Current loop (grid following)\n")
    a("**Structure (chosen):** PR in alpha-beta on the converter current, not PI in dq: it regulates positive and negative "
      "sequence without sequence separation (unbalanced grids, asymmetric faults, the four-wire build later), takes the "
      "harmonic terms of the hand-over directly, needs no w L cross-coupling terms (L1 varies with tolerance), and leaves "
      "the PLL only in the reference path. Feed-forward of the C_f voltage through a SOGI band-pass (fundamental only), "
      "re-seeded on steps > " + f"{A('ff_reseed_pu')} pu; reference = (P - jQ)/(1.5 v_d) + j w C_eq v_d in the PLL frame, circular "
      "limiter, clamping anti-windup at the modulation limit.\n")
    a(f"**Bandwidth rule (stated):** K_p = 2 pi f_c (L1 + L2); the rule (PM >= {RULE_PM:.0f} deg, GM >= {RULE_GM:.0f} dB, closed "
      "loop stable) at every admitted SCR and corner fails at both ends of the f_c sweep: low f_c because the weak grid "
      "pulls the crossover (the loop sees L1 + L2 + L_g below the antiresonance) onto the resonant terms, high f_c because the "
      f"stiff-grid resonance sits just below f_s/6 = {P['fs'] / 6e3:.1f} kHz, where the 1.5 T_s delay adds -90 deg to the "
      "inductor's -90 deg. "
      f"Feasible {R['band'][0]:.0f}-{R['band'][1]:.0f} Hz; chosen = the grid point nearest its geometric centre (the lower on a "
      f"tie): **f_c {K['fc']:.0f} Hz**. Delay-only limit (L plant, no C_f) {R['f_delay']:.0f} Hz. Harmonic terms are kept only if "
      "their lead fits every SCR and their closed-loop mode decays fast enough at every SCR and corner: "
      + ", ".join(f"h{h}: lead {math.degrees(K['phi'].get(h, 0)):.0f} deg, spread {K['spread'][h]:.0f} deg, "
                  f"{'kept' if h in K['harm'] else 'dropped'}" for h in A("harmonics")) + ".\n")
    a(tab(("f_c Hz", "worst PM deg", "worst GM dB", "rule", "harmonic terms"),
          [(f"{r['fc']:.0f}", g1(r["pm"]), g1(r["gm"]), "pass" if r["ok"] else "fail", ",".join(map(str, r["harm"])) or "-")
           for r in R["sweep"]]))
    a(f"Gains: K_p {K['Kp']:.3f} ohm, K_r1 {2 * K['Kp'] * 2 * math.pi * A('fz_Hz'):.1f} ohm/s, K_rh "
      f"{2 * K['Kp'] * 2 * math.pi * A('sigma_h_Hz'):.1f} ohm/s; Tustin with pre-warped poles at 64 kHz.\n")
    a("**Margins at the design gains** (worst over the corners nom/low/high/slow; nominal in brackets; f_res = highest "
      "resonant pole pair of the plant, f_bw = closed-loop -3 dB without the harmonic terms, peak = closed-loop reference "
      "response maximum):\n")
    rows = []
    for damp in ("none", "passive", "active"):
        for scr in A("scr"):
            cs = [r for r in R["table"] if r["damp"] == damp and r["scr"] == scr and r["cn"] in CORNERS]
            nom = next(r for r in cs if r["cn"] == "nom")
            rows.append((damp + (f" (K_ad {R['kad']} ohm)" if damp == "active" else ""), scr_name(scr),
                         f"{g1(nom['f_res'] / 1e3, 2)} ({g1(nom['zeta_res'], 3)})", f"{worst(cs, 'pm'):.1f} [{nom['pm']:.1f}]",
                         f"{worst(cs, 'gm'):.1f} [{min(nom['gm'], nom['gm_lo']):.1f}]", f"{max(r['rho'] for r in cs):.5f}",
                         g1(nom["f_bw"], 0), f"{nom['peak_db']:.1f} @ {nom['f_peak']:.0f}",
                         f"{nom['peak_ig_db']:.1f} @ {nom['f_peak_ig']:.0f}", "pass" if all(r["ok"] for r in cs) else "FAIL"))
    a(tab(("damping", "grid", "f_res kHz (zeta)", "PM deg", "GM dB", "max abs(pole)", "f_bw Hz", "i_1 peak dB @ Hz",
           "i_g peak dB @ Hz", "rule"), rows))
    env = [r for r in R["table"] if r["cn"] == "env" and r["damp"] == "passive"]
    a(f"Sensitivity 'env' (an inductor exactly at pcs_spec's L(I) envelope, {P['L1_env'] * 1e6:.0f} / {P['L2_env'] * 1e6:.1f} uH, "
      "passive branch): " + "; ".join(f"{scr_name(r['scr'])} PM {r['pm']:.0f} deg GM {min(r['gm'], r['gm_lo']):.1f} dB "
                                      f"({'pass' if r['ok'] else 'FAIL'})" for r in env) +
      (" - even a part at the envelope meets the rule." if all(r["ok"] for r in env) else
       " - the envelope is looser than the loop allows; the designed parts are inside it.") + "\n")
    a("Feed-forward variants (nominal corner, design gains, PM deg / GM dB): " + "; ".join(
        f"{ff}: " + ", ".join(f"{scr_name(None if s == 'None' else float(s))} {v['pm']:.0f}/{min(v['gm'], v['gm_lo']):.1f}"
                              for s, v in d.items()) for ff, d in R["ff"].items())
      + ". Full (unfiltered) C_f-voltage feed-forward collapses the weak-grid margins - the band-pass is not optional.\n")
    a("Active-damping scan (capacitor current from dv_C/dt of the sensed C_f voltage, branch not fitted; worst over SCRs and "
      "corners): " + "; ".join(f"K_ad {s['kad']} ohm: PM {s['pm']:.0f} deg, GM {s['gm']:.1f} dB, max |pole| {s['rho']:.3f} "
                              f"({'pass' if s['ok'] else 'fail'})" for s in R["ad_scan"]) + ".\n")
    a("![bode](bode_scr.png)\n")
    L.extend(rep_2b(P, R))
    a("## 3. Step responses (125 kW export, 750 V unless stated)\n")
    win = P["win"]
    a(tab(("case", "grid", "V_dc", "peak (averaged), A", "tier reached", "peak + ripple/2, A", "window", "settling to 5 % of "
           "rated, ms", "overshoot % / deviation A", "modulation limit, ms"),
          [(d["case"], scr_name(d["scr"]), f"{d['vdc']:.0f}", f"{d['pk_avg']:.0f}" if not d["runaway"] else "runaway",
            d["tier"] if not d["runaway"] else "-", f"{d['pk']:.0f}" if not d["runaway"] else f"> {win['hi']:.0f}",
            "TRIP" if d["runaway"] else ("trip possible" if d["pk"] > win["lo"] else "inside"),
            f"{d['settle_ms']:.1f}" if not d["runaway"] else "-",
            (f"{d['overshoot_pct']:.1f} %" if "overshoot_pct" in d else f"{d['dev_A']:.0f} A") if not d["runaway"] else "-",
            f"{d['usat_ms']:.1f}") for d in R["steps"]]))
    a("Tiers as peaks: " + ", ".join(f"{k} {v:g} A rms = {v * math.sqrt(2):.0f} A" for k, v in P["tiers"].items()) +
      f"; window band {win['lo']:.0f}-{win['hi']:.0f} A ('trip possible' = above the band's low edge). 'Deviation' = largest "
      "departure of the PLL-frame current from its final value. At 600 V the +10 % grid is outside the modulation range "
      f"(rated current at any PF needs {R['vdc_min'][None]:.0f} V stiff / {R['vdc_min'][5.0]:.0f} V at SCR 5): the current is "
      "not controlled and the averaged model runs away - in hardware the window trips; firmware must refuse that operating "
      "point (operating map of sim/pcs_design.py).\n")
    a("![steps](steps.png)\n")
    a("## 4. Grid faults and current limiting (power reference held, so the reference saturates)\n")
    a(tab(("case", "grid", "limit A pk", "peak during dip", "peak on recovery", "in limit ms", "modulation limit ms",
           "above window low edge"),
          [(d["case"], scr_name(d["scr"]), f"{d['ilim']:.0f}", f"{d['pk_dip']:.0f}", f"{d['pk_rec']:.0f}", f"{d['lim_ms']:.0f}",
            f"{d['usat_ms']:.1f}", "YES" if d["over_lo"] else "no") for d in R["faults"]]))
    d5 = [d for d in R["faults"] if d["case"] == "0.5 pu dip" and d["ff"] == "sogi"]
    a(f"Peaks include +ripple/2 = {P['ripple_pp'] / 2:.1f} A. **0.5 pu dip at 125 kW export:** the limiter holds the reference "
      f"at {P['I_lim']:.0f} A pk and the converter " + ("stays inside" if all(not d["over_lo"] for d in d5) else "does NOT stay "
      "inside") + f" the window: peaks " + ", ".join(f"{max(d['pk_dip'], d['pk_rec']):.0f} A ({scr_name(d['scr'])})" for d in d5) +
      f" against the {P['win']['lo']:.0f} A low edge of the band. What-if: the stiff 0.1 pu dip with a 30 kHz C_f divider "
      f"{R['divider'][30e3]:.0f} A against {R['divider'][P['f_vdiv']]:.0f} A as drawn.\n")
    a("![faults](faults.png)\n")
    L.extend(rep_4b(P, R))
    L.extend(rep_4c(P, R))
    a("## 5. PLL\n")
    a(tab(("grid", "P kW", f"PM / GM at {G['f_pll']:.0f} Hz", "rule holds up to Hz", "unstable from Hz"),
          [(scr_name(s["scr"]), f"{s['P'] / 1e3:+.0f}", f"{s['pm_design']:.0f} / {g1(s['gm_design'])}", g1(s["f_max"], 0),
            g1(s["f_unstable"], 0) if s["f_unstable"] else "> 400") for s in R["pll"]["sweep"]]))
    c = A("codes")
    a(f"Loop broken at the PLL frequency with every other loop closed (current loop, reference rotation, feed-forward, the "
      f"grid). Time domain at {G['f_pll']:.0f} Hz (limits ASSUMED from memory: phase jump {c['phase_jump_deg']:.0f} deg, "
      f"ROCOF {c['rocof_Hz_s']} Hz/s): " + "; ".join(
          f"{d['case']} {scr_name(d['scr'])}: angle error peak {d['err_pk']:.1f} deg"
          + (f", < 2 deg after {d['settle_ms']:.0f} ms" if "settle_ms" in d else f", ramp error {d['err_end']:.2f} deg "
             f"(type-2 theory {d['err_theory_deg']:.2f})") + f", frequency estimate peak {d['df_pk']:.2f} Hz, phase current "
          f"{d['pk']:.0f} A" for d in R["pll"]["tsim"]) + ". Against the assumed limits: the "
      f"{c['phase_jump_deg']:.0f} deg jump is ridden through (phase current at most "
      f"{max(d['pk'] for d in R['pll']['tsim']):.0f} A against the {P['win']['lo']:.0f} A window edge; the frequency "
      f"estimate peaks at {max(d['df_pk'] for d in R['pll']['tsim']):.2f} Hz, "
      f"{'inside' if max(d['df_pk'] for d in R['pll']['tsim']) < A('pll_dw_max_Hz') else 'AT'} its "
      f"+/-{A('pll_dw_max_Hz'):.0f} Hz clamp) and the {c['rocof_Hz_s']} Hz/s ramp is tracked with "
      f"{max(d['err_end'] for d in R['pll']['tsim'] if 'err_theory_deg' in d):.2f} deg error. The angle error peak exceeds the "
      "jump because the sensed C_f voltage overshoots the grid angle before the PLL moves: " + "; ".join(
          f"{scr_name(d['scr'])}: v_C at {d['v_ang_pk']:.1f} deg {d['t_err_pk_us']:.0f} us after the jump, PLL moved "
          f"{d['pll_moved_pk']:.1f} deg" for d in R["pll"]["tsim"] if "t_err_pk_us" in d) +
      " (stiff: LCL ringing; SCR 5: the current through the grid impedance).\n")
    a("![pll](pll_weak_grid.png)\n")
    a("## 6. DC-link voltage loop (inverter holds V_dc; DC/DC = constant-power source + / load -)\n")
    a(tab(("grid", "V_dc", "DC/DC kW", "DC-current feed-forward", "PM deg", "GM dB", "worst-PM crossover Hz", "max abs(pole)",
           "rule"),
          [(scr_name(d["scr"]), f"{d['vdc']:.0f}", f"{d['pcpl'] / 1e3:+.0f}", "yes" if d["kff"] else "no",
            *((g1(d["pm"]), g1(d["gm"]), g1(d["fc"], 0), g1(d["rho"], 5)) if d["ok"] is not None else ("-",) * 4),
            {True: "pass", False: "FAIL", None: "outside the modulation range"}[d["ok"]]) for d in R["dc"]["lin"]]))
    a(f"C_dc {P['Cdc'] * 1e3:.3f} mF stores {R['dc']['e_link_J']:.0f} J at 750 V (about 1 ms of rated power); the constant-power "
      f"pole at 600 V / 125 kW is {R['dc']['f_rhp_600']:.0f} Hz.\n")
    a(tab(("case", "grid", "ramp ms", "C_dc mF", "V min", "V max", "recovers"),
          [(d["case"], scr_name(d["scr"]), f"{d['t_ramp_ms']:.0f}", f"{d['Cdc_mF']:.2f}",
            *((f"{d['vmin']:.0f}", f"{d['vmax']:.0f}") if d["recovered"] else ("collapse", "-")),
            "yes" if d["recovered"] else "NO") for d in R["dc"]["tsim"]]))
    a("'collapse': the averaged model has no body-diode rectifier floor, so its voltage after a collapse means nothing; "
      "in hardware the bus falls to the rectified grid peak and the DC/DC's undervoltage limit acts.\n")
    L.extend(rep_6b(P, R))
    gv, gf = R["gv"], R["gfm"]
    a("## 7. Grid forming and transfers\n")
    fast = R["gv_fast"]
    a(f"Voltage PR on v_C, K_pv = 2 pi f_cv C_eq, dq-equivalent zero at f_cv / ratio. The rule (PM >= {RULE_PM:.0f} deg, GM >= "
      f"{RULE_GM:.0f} dB, cascade poles inside the unit circle; no load and rated resistive load, every corner) holds on two "
      "islands per ratio, split by a gain-margin notch, so the choice is the pair with the largest headroom "
      f"min(PM - {RULE_PM:.0f}, 5 (GM - {RULE_GM:.0f})): **f_cv {gv['fcv']:.0f} Hz, ratio {gv['ratio']:g}** (K_pv "
      f"{gv['kv']['Kp']:.3f} S, K_rv {2 * gv['kv']['Kp'] * 2 * math.pi * gv['fcv'] / gv['ratio']:.1f} S/s; worst PM "
      f"{gv['pm']:.0f} deg, GM {gv['gm']:.1f} dB). Off-grid: " + "; ".join(
          f"{m['kind']}/{m['cn']} PM {m['pm']:.0f} GM {min(m['gm'], m['gm_lo']):.1f} dB, closed-loop -3 dB {g1(m.get('f_bw'), 0)} Hz"
          for m in gv["ev"] if m["cn"] in ("nom", "low")) + f". Achievable: the fastest pair meeting the rule (f_cv "
      f"{fast['fcv']:.0f} Hz, ratio {fast['ratio']:g}, PM {fast['pm']:.0f} deg, GM {fast['gm']:.1f} dB) reaches "
      f"{fast['f_bw_rl']:.0f} Hz at rated load. The stiffness against a load step is set by K_pv x R_load < 1 (K_pv is capped "
      "by the inner loop), not by the bandwidth: see the load step below.\n")
    a(tab(("zero ratio", "f_cv meeting the rule, Hz", "largest headroom there"),
          [(f"{q:g}", ", ".join(f"{r['fcv']:.0f}" for r in R["gv_rows"] if r["ratio"] == q and r["ok"]) or "none",
            g1(max((r["head"] for r in R["gv_rows"] if r["ratio"] == q and r["ok"]), default=None), 1))
           for q in A("fzv_ratios")]))
    a("Grid-connected poles (P_set 62.5 kW): " + "; ".join(f"{scr_name(d['scr'])}: max abs(pole) {d['rho']:.6f}, least damped "
                                                           f"{d['zeta']:.3f} at {d['f']:.0f} Hz" for d in gf["lin"]) + ".\n")
    ls, sh, isl = gf["load_step"], gf["short"], gf["island"]
    a(f"- Off-grid 0 -> rated resistive load: v_C dips to {ls['vmin']:.2f} pu, within 10 % after {ls['t10_ms']:.0f} ms, 5 % "
      f"after {ls['t5_ms']:.0f} ms, ends at {ls['v_end']:.3f} pu (virtual-resistance drop: a slow secondary restoration is a "
      f"firmware item), frequency {ls['df_end']:+.1f} Hz (droop).")
    a(f"- Off-grid terminal short for 150 ms from rated load: the limiter holds {sh['i_hold']:.0f} A pk; first peak "
      f"{sh['pk']:.0f} A incl. ripple ({'above' if sh['pk'] > P['win']['lo'] else 'below'} the window's low edge "
      f"{P['win']['lo']:.0f} A); L2 carries the C_f discharge, {sh['i2_pk']:.0f} A pk, which no sensor sees; in limit "
      f"{sh['lim_ms']:.0f} ms (firmware trips at {sh['t_fw_trip_ms']:.0f} ms); after clearing v_C overshoots to "
      f"{sh['vmax_rec']:.2f} pu and is within 5 % after {sh['t_rec5_ms']:.0f} ms.")
    for d in gf["sync"]:
        a(f"- Close at the permissive edge ({P['sync'][0]:.0f} %, {P['sync'][1]:.0f} deg), {scr_name(d['scr'])}: peak "
          f"{d['pk_close']:.0f} A incl. ripple (pcs_spec inrush estimate {P['inrush_A']:.0f} A); GFM -> GFL switch deviation "
          f"{d['dev_switch']:.0f} A.")
    for d in isl:
        a(f"- Islanding at SCR 10 with a 62.5 kW local load, inverter at {d['P_kW']:.1f} kW (export to the grid "
          f"{d['export_kW']:.1f} kW): GFL -> GFM switch moves v_C by {100 * d['dv_switch']:.1f} %; when the upstream breaker opens "
          f"v_C peaks at {d['v_pk']:.2f} pu, is {d['v_10ms']:.2f} pu after 10 ms and settles at {d['v_end']:.2f} pu; frequency "
          f"{round(d['df_end'], 2) + 0.0:+.2f} Hz (droop)" + (" - a load rejection: C_f charges before the voltage loop acts, and the set point "
                                              "keeps the virtual-impedance drop of the export (secondary restoration is a "
                                              "firmware item)." if abs(d["export_kW"]) > 1 else " - the planned sequence."))
    a("\nA transfer within 20 ms needs the unit to be grid forming before the grid is lost (switching GFL -> GFM after "
      "detection takes as long as the detection), and a planned transfer ramps the grid exchange to zero first.\n")
    a("**Transfer sequence (state list):**\n")
    a(tab(("state", "entry", "exit / permissive", "outputs", "measured", "hardware behind it"), sm_table(P, G)))
    a("![gfm](gfm.png)\n")
    L.extend(rep_7b(P, R))
    L.extend(rep_7c(P, R))
    a("![bounded](bounded.png)\n")
    a("![vf](vf_and_ride_through.png)\n")
    a("## 8. THDi estimate and dead time (CALCULATED, assumed background distortion)\n")
    rows = []
    for ctl in dict.fromkeys(r["ctrl"] for r in R["thd"]):
        for dt in dict.fromkeys(r["dt"] for r in R["thd"]):
            sel = [r for r in R["thd"] if r["ctrl"] == ctl and r["dt"] == dt]
            rows.append((ctl, dt, f"{max(r['thd_pct'] for r in sel):.2f}", scr_name(max(sel, key=lambda r: r["thd_pct"])["scr"])))
    a(tab(("controller", "dead time", "worst THDi %", "at"), rows))
    a(f"Dead-time error {P['dt_gate_ns'][0]:.0f}-{P['dt_gate_ns'][1]:.0f} ns at 900 V and {P['fsw'] / 1e3:.0f} kHz = "
      f"{900 * P['fsw'] * P['dt_gate_ns'][0] * 1e-9:.1f}-{900 * P['fsw'] * P['dt_gate_ns'][1] * 1e-9:.1f} V average, a square "
      f"wave with the current sign; plus the PWM figure of pcs_spec ({100 * P['pwm_thd']:.2f} %).\n")
    L.extend(rep_8b(P, R))
    pl, isr = R["plan"], R["isr"]
    a("## 9. Firmware requirements\n")
    a(tab(("round", "nets", "pins", "ADC A/B/C"), [(i + 1, ", ".join(r["nets"]), ", ".join(r["pins"]), "/".join(r["adc"] or ("-",)))
                                                for i, r in enumerate(pl["rounds"])]))
    a(f"Rounds 1-3 at both update instants (carrier zero and peak), round 4 once per carrier period, VA/VB {pl['k_va']} times "
      f"per period (every {pl['va_interval_us']:.1f} us: the pin plan's ADC-PPB over-voltage backup needs <= "
      f"{P['va_max_us']:.0f} us), slow channels (NTC multiplexer, NTC9, inlet, RCM, VPE, VBX, IB_H) once per ms: "
      f"{pl['conv_per_period']:.1f} conversions per {1e6 / P['fsw']:.2f} us, {pl['busy_pct']:.0f} % ADC load at "
      f"{pl['t_conv_us']:.2f} us per conversion (PCS-CTL's own figure), last loop input {pl['latency_us']:.2f} us after the "
      "trigger. The RCM rate is a placeholder until the type-B sensor (RFQ) fixes its output bandwidth.\n")
    a(tab((f"ISR at {P['fs'] / 1e3:.0f} kHz", "operations", "cycles", "us", f"% of {1e6 / P['fs']:.3f} us"),
          [(m, v["ops"], f"{v['cycles']:.0f}", f"{v['us']:.2f}", f"{v['pct']:.0f}") for m, v in isr[1].items()]))
    a(f"Cost model ASSUMED: {A('isr_cyc_per_op')} cycles per operation (compiled C, FPU32 + TMU), {A('isr_overhead_cyc')} cycles "
      "of entry/exit, F280039C at 120 MHz; slower tasks (grid protection, RMS, ROCOF, thermal model, anti-islanding) at 1 kHz "
      f"add about 1 %. The CLA can take the {P['fs'] / 1e3:.0f} kHz part.\n")
    a(tab(("limit / setting", "value"), firmware_limits(P, G, R)))
    a(tab(("anti-islanding", "method", "measurements", "note"), anti_islanding(P)))
    sp = read_json(SPEC)
    fw4, acs = sp.get("four_wire"), sp.get("ac_start")
    if fw4 and acs:                                         # sim/pcs_design.py step i (read at run time): text only, no new model
        nom = fw4["window"]["rows"]["nominal"]
        a("Four-wire build and AC start (sim/pcs_design.py step i, read at run time; the N-leg loop: section 9b): the N leg runs in "
          "mode A "
          f"(50 % duty, reference L_N di_N/dt plus a v(N_F - M) loop on VGN - VA; {nom['mode_A']['closing (no load)']:.0f} V DC to "
          f"connect at 230 V) or mode B (the min-max zero sequence on all four legs: the three-wire window, "
          f"{nom['mode_B']['closing (no load)']:.0f} V), the per-phase current loops here take the phase-to-N voltages, the neutral "
          "current is limited to the phase tiers, and a DC-component regulator holds the off-grid output below 0.5 % Un with "
          f"half-wave loads; the grid-start path ({len(acs['sequence'])} steps in pcs_spec ac_start: grid tap boots the controller -> "
          "relay test -> AC precharge -> K2, K1 onto the precharged link -> rectifier start -> DC port) enters this study's DC-link "
          "loop (PI 40 Hz, DC-current feed-forward = 0 with the DC contactor open) as a rectifier; its states, the executable sequence "
          "and the fault coverage: section 9d.\n")
    a("Protection state machine: outputs (PWM, K_ACPRE, K_PRE, K_DC, K_AC2, K_AC1) per state " + ", ".join(f"{s} {v}" for s, v in SM_OUT6.items())
      + ". Exhaustive check over every (state, event) pair: " + "; ".join(f"{k}: {'holds' if v else 'FAILS'}" for k, v in R["sm"].items())
      + ". (The check found that a stop during synchronisation routed through CONTROLLED STOP would have closed the AC "
      "contactors; the transition now goes to IDLE.) Verified = the table, not firmware code.\n")
    L.extend(rep_9b(P, R))
    L.extend(rep_9c(P, R))
    L.extend(rep_9d(P, R))
    a("## 10. The drawn hardware against the loop\n")
    a(tab(("item", "as drawn / stated", "study result", "verdict"), R["hw"]))
    a("## 11. Open items\n")
    a("- A switched (PWM, dead-time, ripple-sampling) simulation of the chosen loops and a THDi figure from it; ADC noise.")
    a(f"- Phase-current sensor: the frozen {A('sensor')['part']} ({A('sensor')['G_mV_per_A']} mV/A) needs the PCS-CTL window "
      f"ladder re-valued (drawn for {P['G_chk']:.1f} mV/A); its 2 us step response is in the loop here; the residual-current "
      "sensor is still a quotation part.")
    a(f"- Ride-through: the onset measure of section 4c holds the stiff onset {R['rtr']['margin_A']:.0f} A below the window in the "
      "averaged model; a switched model and the bench must confirm that margin (the prediction error of the deadbeat clamp), else the "
      "fallback rule (the grid-stiffness estimate, ASSUMED +/-30 %, and the derating table) applies; the ride-through thresholds and the "
      "dip-time cap follow grid-code values quoted from memory.")
    a(f"- DC/DC coordination: power ramp and a shared trip signal; the DAB study's {P['c_pcs_dab'] * 1e3:.0f} mF assumption.")
    a("- Grid forming: the declared envelope (7c) rests on the averaged model with the load-current feed-forward and the "
      "over-voltage deadbeat - a switched simulation and the bench are open; the DC/DC coordination time on a battery-less bus is a "
      "system requirement for the DC/DC firmware; the virtual impedance (0.15 + j0.30 pu) was not re-scanned with the new voltage-loop "
      "gains; paralleled modules' restoration consensus over the CAN is not modelled.")
    a("- PLL: plain SRF on v_C; a positive-sequence (DSOGI) front end for unbalanced grids is a firmware option, not studied.")
    a("- Standards (EN 50549-1, IEC 62116, GB/T 34120) to buy and check against the assumed limits.")
    a("- Four-wire: the per-phase virtual reactance (quadrature generator), a switched model of the coupled cases (9c is averaged), "
      "mode B and the mode A / B hand-over, the L-N short's first peak at the window's low edge (9c); the AC-start relay timings and the "
      "aux draw are ASSUMED until the RFQ coil data exist (9d).")
    a("- VF THDu: the min-max zero sequence near the L1-C_f resonance through the filters' tolerance mismatch is the largest term "
      "at low DC voltage; the third-harmonic zero sequence (firmware option) is not adopted or simulated.\n")
    a("## Files\n")
    a("`sim/pcs_control.py`; `sim/out/pcs_control/`: report.md, pcs_control_spec.json, bode_scr.png, steps.png, faults.png, "
      "pll_weak_grid.png, gfm.png, vf_and_ride_through.png, bounded.png.")
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L) + "\n")


def check_plant(P):
    """independent checks of the plant: (spec) textbook undamped LCL resonance with L_g of each SCR on the stated base
    against pcs_spec lcl.f_res_Hz (sim/pcs_design.py, pure-X grid); (model) the state-space plant without the R_d-C_d
    branch against the textbook value with the CM-choke leakage; (sampled) the sampled plant x z^-1 (ZOH + one-sample
    delay) against the continuous plant x exp(-1.5 s T_s), 50-500 Hz (below every antiresonance of the converter
    current, where a relative error is ill-conditioned; a 1.0 or 2.0 T_s delay would differ by 2.4 % at 500 Hz) -> worst
    relative error of each"""
    e = dict(spec=0.0, model=0.0, sampled=0.0)
    tb = lambda L2, Lg: math.sqrt((P["L1"] + L2 + Lg) / (P["L1"] * (L2 + Lg) * P["Cf"])) / (2 * math.pi)
    s = 2j * np.pi * np.linspace(50.0, 500.0, 40)
    for k, fsp in P["f_res_spec"].items():
        scr = None if k == "stiff" else float(k.split()[-1])
        Lg = 0.0 if scr is None else P["zb"] / scr / math.hypot(1.0, A("grid_rx")) / P["w0"]
        e["spec"] = max(e["spec"], abs(tb(P["L2"], Lg) / fsp - 1))
        if scr in A("scr"):
            pl = plant(P, "nom", "grid", scr, "none")
            e["model"] = max(e["model"], abs(f_res(pl)[0] / tb(P["L2"] + P["Ldm"], pl["Lg"]) - 1))
            Gc = np.array([np.linalg.solve(x * np.eye(8) - pl["A"], pl["Bu"])[5] for x in s]) * np.exp(-1.5 * s * P["T"])
            z = np.exp(s * P["T"])
            e["sampled"] = max(e["sampled"], float(np.max(np.abs(pfr(pl, z)[0] / z / Gc - 1))))
    return e


def self_check(P, G, R):
    """fails loudly if a margin rule or a model consistency check does not hold"""
    assert R["agree"] < 1e-6, ("synchronous-frame simulator vs alpha-beta linear model", R["agree"])
    c = R["plant_check"]
    assert c["spec"] < 0.01 and c["model"] < 0.01 and c["sampled"] < 0.01, ("plant vs pcs_spec / textbook / continuous", c)
    eq = [d["eq_res"] for d in R["steps"] + R["pll"]["sweep"] + R["gfm"]["lin"]] + \
         [d["eq_res"] for d in R["dc"]["lin"] if d["ok"] is not None]
    assert max(eq) < 1e-6, ("Newton equilibrium residual", max(eq))
    for d in R["pll"]["tsim"]:
        if "err_theory_deg" in d:
            assert abs(d["err_end"] - d["err_theory_deg"]) < 0.2 * d["err_theory_deg"], ("PLL ramp error vs type-2 theory", d["scr"])
    sh = R["gfm"]["short"]
    assert abs(sh["i_hold"] / P["I_lim"] - 1) < 0.02, ("grid-forming current limiter under a short", sh["i_hold"])
    for r in R["table"]:
        if r["damp"] == "passive" and r["cn"] in CORNERS:
            assert r["ok"], ("current-loop rule", r["scr"], r["cn"], r["pm"], r["gm"], r["gm_lo"], r["rho"])
    assert not all(r["ok"] for r in R["table"] if r["damp"] == "none" and r["scr"] is None), "report claims the branch is needed"
    for m in R["gv"]["ev"]:
        assert m["pm"] >= RULE_PM and min(m["gm"], m["gm_lo"]) >= RULE_GM and m["rho"] < 1, ("GFM voltage loop", m["kind"], m["cn"])
    for d in R["gfm"]["lin"]:
        assert d["ok"] and d["zeta"] >= A("gfm_zeta_min"), ("grid-connected grid forming: unstable or ill-damped", d["scr"], d["zeta"])
    for s in R["pll"]["sweep"]:
        assert s["pm_design"] >= RULE_PM and s["gm_design"] >= RULE_GM, ("PLL margins at the design bandwidth", s["scr"], s["P"])
        assert s["f_max"] is None or s["f_max"] >= 2 * G["f_pll"], ("PLL bandwidth within half its weak-grid limit", s["scr"])
    for d in R["dc"]["lin"]:
        if d["ok"] is not None:
            assert d["ok"] == (d["kff"] == 1.0 or d["pcpl"] > 0), ("DC-link loop: feed-forward makes every case pass", d)
    assert all(R["sm"].values()), ("state machine invariants", R["sm"])
    for r in R["thd"]:
        if r["ctrl"] == R["thd_label"] and r["dt"].startswith("compensated"):
            assert r["thd_pct"] < 3.0, ("THDi estimate of the design", r["scr"], r["dt"], r["thd_pct"])
    assert R["plan"]["all_simultaneous"] and R["plan"]["busy_pct"] < 50, "ADC plan"
    assert max(v["pct"] for v in R["isr"][1].values()) < 50, "ISR budget"
    # sections 4b, 7b, 8b-c, 9b
    rt, lo = R["rt"], P["win"]["lo"]
    assert all(max(d["pk_dip"], d["pk_rec"]) <= lo for d in rt["rows"]), "ride-through: an in-dip / recovery peak above the window"
    assert all(d["onset"] <= lo for d in rt["derating"]), ("derated pre-dip power does not hold the onset", rt["derating"])
    assert rt["instant_scan"][0] >= max(rt["instant_scan"]) - 1.0, ("the study's dip instant is not the worst of the scan", rt["instant_scan"])
    ons = [d["pk_on"] for d in rt["rows"] if d["scr"] is None]
    assert all(a >= b for a, b in zip(ons, ons[1:])), "stiff-grid onset not monotonic in the residual voltage"
    sec = R["sec"]
    assert all(abs(d["v_C_pu"] - 1) < 1e-4 and abs(d["df_Hz"]) < 1e-6 and d["eq_res"] < 1e-6 for d in sec["ss"].values()), \
        ("VF restoration: the fixed point is not at nominal voltage and frequency", sec["ss"])
    for k in ("0 -> rated", "rated -> 0"):
        s = sec["steps"][k]
        assert s["t_v1_s"] < s["t_end_s"] - 0.3 and s["t_f_s"] < s["t_end_s"] - 0.3, ("VF restoration does not settle in the window", k)
    v = R["acc"]["voltage"]
    assert abs(v["bound_worst_pct"] - v["residual_pct"] - v["sensing_worst_pct"]) < 1e-9, "voltage accuracy bookkeeping"
    for r in R["thdu"]["rows"]:
        tot = math.sqrt(r["carrier_pct"] ** 2 + r["dead_time_pct"] ** 2 + r["leak_pct"] ** 2 + r["mismatch_pct"] ** 2)
        assert abs(tot - r["thdu_pct"]) < 1e-6 * max(1.0, tot), ("THDu terms do not add up", r)
    nl = R["nleg"]
    assert all(r["v_ok"] for r in nl["rows"]), ("N leg: the VF cascade fails the rule at a termination", nl["rows"])
    held = next(r for r in nl["rows"] if "held" in r["case"])
    assert held["i_ok"] and abs(held["v_pm"] - nl["phase_ref"]["v_pm"]) < 2.0, ("N leg with a held phase is not the phase plant", held)
    assert nl["excursion"]["end_pu"] < 0.01 and abs(nl["modeB"]["u0_mean_V"]) < 1e-6, ("N node", nl["excursion"], nl["modeB"])
    # review R2 (sections 2b, 4c, 6b, 7c, 9c, 9d)
    tol = R["tol"]
    assert tol["all_pass"], ("tolerance regression: a mixed corner fails the rule", tol["factorial"], tol["random"])
    rr = R["rtr"]
    assert rr["adopted"] and not any(d["trip"] for d in rr["rows"]), ("ride-through onset measure", rr["worst_onset"], rr["thr"])
    assert all(d["scr_b"] > 50.0 for d in rr["boundary"]), ("fallback boundary below SCR 50", rr["boundary"])
    assert all(d["onset"] <= rr["thr"] for d in rr["table"]), "a derating-table row does not hold the onset"
    assert R["vff"]["ok"], "VF load-current feed-forward: the chosen low-pass fails the rule"
    vb = R["vfb"]
    for k, v in vb["steps"].items():
        if k.startswith("design, single"):
            assert v["env"]["ok"] and v["itic"]["ok"], ("VF bounded step outside the declared envelope / class", k, v["env"], v["itic"])
    assert all(u["trip"] and u["dev_ok"] and u["half_ok"] for u in vb["uncoordinated"].values()), "VF uncoordinated: trip / device / film"
    assert vb["cap_tc"]["vdc_max"] < P["ov_soft"][0] < vb["cap_tc_over"]["vdc_max"], "coordination time does not bracket the soft limit"
    dc6 = max(d["vmax"] for d in R["dc"]["tsim"] if d["scr"] == 5.0 and d["case"].endswith("trips") and abs(d["Cdc_mF"] - 1e3 * P["Cdc"]) < 0.01)
    assert abs(next(d for d in R["dct"]["rows"] if not d["trip"])["vmax_loop"] - dc6) < 2.0, "section 6b's loop-alone case is not section 6's"
    assert all(d["dev_ok"] and d["screen_ok"] and d["half_ok"] for d in R["dct"]["rows"] if d["trip"]), "DC trip: device / screen / film"
    n4 = R["n4"]["cases"]
    sh = next(v for k, v in n4.items() if k.startswith("line-to-neutral"))
    assert sh["trip_ms"] is not None and abs(sh["trip_ms"] - 1e3 * A("t_lim_s")) < 1.0, ("four-wire short: the limit timer", sh["trip_ms"])
    for k, v in n4.items():
        if not k.startswith("line-to-neutral"):
            assert all(abs(v[x]["amp_end"] - 1) < 0.03 for x in ("va", "vb", "vc")), ("four-wire case does not recover", k)
    assert not R["smx"]["residual"], ("AC start: a safety breach remains with the repairs", R["smx"]["residual"])


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    P = load()
    fc, band, sweep, f_delay = design_fc(P)
    K = ctrl(P, fc)
    gv, gv_fast, gv_rows = design_gfm_voltage(P, K)
    vff, vff_rows = design_vf_ff(P, K, gv["kv"])
    G = gains(P, K, gv["kv"], f_io=vff["f_io"])
    R = dict(fc=fc, band=band, sweep=sweep, f_delay=f_delay, gv=gv, gv_fast=gv_fast, gv_rows=gv_rows, agree=agreement(P, G),
             plant_check=check_plant(P), vff=vff, vff_rows=vff_rows)
    R["table"], R["ad_scan"], R["kad"] = table_current(P, K)
    R["tol"] = study_tolerance(P, K)
    R["ff"] = ff_variants(P, K)
    R["steps"], R["faults"], R["divider"] = study_steps(P, G), study_faults(P, G), study_divider(P, G)
    R["pll"], R["dc"], R["gfm"] = study_pll(P, G), study_dc(P, G), study_gfm(P, G)
    R["thd_label"] = "design (h1+" + "+".join(f"h{h}" for h in K["harm"]) + f", {fc:.0f} Hz)"
    R["thd"] = study_thd(P, {R["thd_label"]: K,
                             f"design without harmonic terms ({fc:.0f} Hz)": ctrl(P, fc, harm=()),
                             "1 kHz (risk register)": ctrl(P, 1000.0)})
    R["plan"], R["isr"], R["sm"], R["vdc_min"] = sampling_plan(P), isr_budget(P, K), verify_sm(), vdc_min(P)
    R["rt"], R["sec"] = study_ride_through(P, G), study_secondary(P, G)
    R["rtr"] = study_rt_rule(P, G, R)
    R["vfb"], R["dct"] = study_vf_bounded(P, G, R), study_dc_trip(P, G)
    R["acc"], R["gfm_harm"] = vf_accuracy(P, R["sec"]), design_gfm_harm(P, K, G["Kv"])
    R["thdu"] = study_thdu(P, G, R)
    R["imb"], R["nleg"] = study_imbalance(P, R), study_neutral(P, G, R)
    R["n4"], R["smx"] = study_four_wire(P, G), study_sm(P)
    R["hw"] = hardware(P, R)
    plots(P, G, R)
    plots_r2(P, R)
    spec = write_spec(P, G, R)
    write_report(P, G, R, spec, time.time() - t0)
    self_check(P, G, R)
    pas = [r for r in R["table"] if r["damp"] == "passive" and r["cn"] in CORNERS]
    print(f"current loop f_c {fc:.0f} Hz (band {band[0]:.0f}-{band[1]:.0f}), harmonics {K['harm']}, worst PM "
          f"{min(r['pm'] for r in pas):.1f} deg, GM {min(min(r['gm'], r['gm_lo']) for r in pas):.1f} dB; PLL {G['f_pll']:.0f} Hz; "
          f"GFM f_cv {gv['fcv']:.0f} Hz; mismatches {sum(h[3].startswith('MISMATCH') for h in R['hw'])}")
    print(f"pcs_control self-check passed ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
