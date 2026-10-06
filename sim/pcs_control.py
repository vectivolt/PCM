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
V_LL, P_N, F0 = 400.0, 125e3, 50.0          # REQUIREMENTS AC-02
RULE_PM, RULE_GM = 40.0, 6.0               # margin rule of the study brief (every admitted SCR, every corner)
R_OPEN = 1e4                               # ohm: an open contactor / open terminal in the linear plant

ASSUME = {
    "grid_rx": (0.1, "grid R/X behind the point of connection (Thevenin, SCR on the 400 V / 125 kW base)"),
    "scr": ((None, 20.0, 10.0, 5.0), "SCRs studied (None = stiff, L_g 0); pcs_spec admits '5 .. stiff'"),
    "cf_tol": (0.05, "C_f tolerance (MKP film, a request-for-quotation part)"),
    "l_high": (1.10, "+10 % inductance corner at 0 A (magnetics tolerance +/-10 %)"),
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
    P.update(L1=flt["L1"], L2=flt["L2"], Cf=flt["Cf"], Rd=flt["Rd"], Cd=flt["Cd"],
             Ldm=ec["L_nom_H"] * (float(pct.group(1)) if pct else 0.3) / 100,
             R1=mg["l1"]["loss_model"]["winding"]["R_dc_110C_ohm"], R2=mg["l2"]["loss_model"]["winding"]["R_dc_110C_ohm"],
             L1_lo=min(e1["L_inc_at_minus10pct_part_H"].values()), L2_lo=min(e2["L_inc_at_minus10pct_part_H"].values()),
             L1_hi=max(e1["L_inc_vs_I_H"].values()) * A("l_high"), L2_hi=max(e2["L_inc_vs_I_H"].values()) * A("l_high"),
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
        next(k for k in fw4["window"]["windows_at_400_230V"] if "mode A" in k)]["full_load_from"])
    ow = sp["declarations"]["operating_windows"]["3W+PE"]
    P["vf_vdc"] = (ow["full_load_from"], 750.0, ow["operating_to"])     # VF DC voltages for THDu (window ends + nominal)
    return P


# ---------------------------------------------------------------------------------------------------------- plant
_PL = {}


def corner(P, cn):
    """(L1, L2 + DM leakage, C_f, current-chain delay) of an inductance / capacitance / sensor corner"""
    tol, ti, dm = A("cf_tol"), P["ti"], P["Ldm"]
    return {"nom": (P["L1"], P["L2"] + dm, P["Cf"], ti),
            "low": (P["L1_lo"], P["L2_lo"] + dm, P["Cf"] * (1 - tol), ti),
            "high": (P["L1_hi"], P["L2_hi"] + dm, P["Cf"] * (1 + tol), ti),
            "slow": (P["L1_lo"], P["L2_lo"] + dm, P["Cf"] * (1 - tol), ti * A("slow_sensor")),
            "env": (P["L1_env"], P["L2_env"] + dm, P["Cf"] * (1 - tol), ti)}[cn]


def plant(P, cn="nom", kind="grid", scr=None, damp="passive", RL=None):
    """per-phase plant, states i1 vC vD i2 ig yi yv yg (yi, yv, yg = sensed i1, v_Cf, v_terminal), discretised.
    kind: grid (L2 + L_g to the source), open (contactors open, grid on the terminals), load (grid + local load RL at the
    terminals), nl (island, no load), rl (island, load RL, default 1 pu), short (terminal short)"""
    key = (cn, kind, scr, damp, RL, P["tv"], P["ti"])
    if key in _PL:
        return _PL[key]
    L1, L2, Cf, ti = corner(P, cn)
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
        g = 1 / P["Rd"]
        Ac[1, 1], Ac[1, 2], Ac[2, 1], Ac[2, 2] = -g / Cf, g / Cf, g / P["Cd"], -g / P["Cd"]
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
CK = ("xp", "d", "xr", "xf", "xv")                          # complex states (synchronous frame)
RK = ("th", "xi", "vd", "thv", "pf", "qf", "vdc", "vdcm", "iem", "xdc", "dws", "dvs", "vmf", "lvt")


def gains(P, K, Kv, f_pll=None, f_dc=None):
    """controller set: current PR (grid following), P-only inner loop + voltage PR (grid forming), PLL, DC link, droop"""
    T = P["T"]
    f_pll, f_dc = f_pll or A("f_pll_Hz"), f_dc or A("f_dc_Hz")
    wn = 2 * math.pi * f_pll / 2.058                          # -3 dB of a type-2 loop with zeta 0.707 = 2.058 w_n
    kp_dc = 2 * math.pi * f_dc * P["Cdc"]
    Af = K["yv"][0]
    return dict(K=K, Kin=p_only(K), Kv=Kv, f_pll=f_pll, pll_kp=2 * 0.707 * wn, pll_ki=wn * wn, reseed=True,
                ff_ss=np.linalg.inv(np.eye(len(Af)) - P["R"] * Af) if len(Af) else None,
                dwmax=2 * math.pi * A("pll_dw_max_Hz"), dwslew=2 * math.pi * A("pll_slew_Hz"), a_vd=lp(A("vd_lpf_Hz"), T), f_dc=f_dc, kp_dc=kp_dc,
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
    Pref, Qref = inp.get("P", 0.0), inp.get("Q", 0.0)
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
            vm1 = abs(yv) / P["V0"]                           # level arms a hold timer; the dip-time limit applies while it runs
            N["lvt"] = (A("lvrt_hold_s") if vm1 < A("lvrt_pu")[0] else X["lvt"] if vm1 < A("lvrt_pu")[1] else max(0.0, X["lvt"] - T))
            if X["lvt"] > 0:
                ilim = min(ilim, S["lvrt"])
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
        if brk == "gfmv":
            o["brk"], iu = iu.real, S["ext"] + 1j * iu.imag
        o["lim"] = abs(iu) > S["ilim"]
        iref = iu * S["ilim"] / abs(iu) if o["lim"] else iu
        N["xv"] = R * (Av @ X["xv"] + Bv * (ev + (iref - iu) / G["Kv"]["Kp"]))
        Kc = G["Kin"]
        o["dwv"], o["s"] = dwv, s
    e = iref - yi                                             # current controller with clamping anti-windup
    Ar, Br, Cr, Dr = Kc["res"]
    Af, Bf, Cf_, Df = Kc["yv"]
    uu = Kc["Kp"] * e + (Cr @ X["xr"] + Dr * e if len(Br) else 0) + (Cf_ @ X["xf"] if len(Bf) else 0) + Df * yv
    if brk == "cur":
        o["brk"], uu = uu.real, S["ext"] + 1j * uu.imag
    uc, o["clamp"] = iclamp(P, S["iclamp"], X, yi, yv, uu, k) if S.get("iclamp") else (uu, False)
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
    if S.get("dc"):                                           # DC link with a constant-power DC/DC
        pc = 1.5 * (dact * xp[0].conjugate()).real
        iext = S["pcpl"] * (X["vdc"] / S["vdc_ref"]) ** S.get("k_cpl", 0.0) / X["vdc"]   # k 0 = constant power
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
    return ("xp", "d", "xf", "xv", "thv", "pf", "qf") + (("dws", "dvs", "vmf") if S.get("sec") else ())


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
             dws=G["mp"] * (s.real - Pop) if sec else 0.0, dvs=0.0, vmf=abs(yv), lvt=0.0)
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
        if ev:
            ev(t, S, X)
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
def study_ride_through(P, G):
    """the firmware current limiter in ride-through (D-074 open item: a 0.1 pu dip at 125 kW reached 465 A against the 426 A low
    edge of the hardware window).  Layers: the circular reference limiter of section 4; the ride-through state (sensed |v_C|
    below lvrt_pu arms a hold timer: reference limit = the hand-over profile's dip-time cap, PLL free-running below
    pll_freeze_pu); the per-sample predictive clamp in the PWM update at the window low edge - ripple/2 - margin.  Dips at
    125 kW export, power reference held, 150 ms: five depths x five grids with every layer; the clamp alone at stiff and SCR 5;
    the stiff-grid onset against the pre-dip power (derating); the study's dip instant against a 60 deg scan"""
    c, T, lo = A("codes"), P["T"], P["win"]["lo"]
    t0, n1 = 0.01, int(round(1e-3 / P["T"]))

    def dip(depth, scr, Pop=P_N, layers="all", ts=t0, dur=None, tail=0.1):
        dur = c["dip_s"] if dur is None else dur
        S, X = setup(P, G, "gfl", scr, Pop=Pop, iclamp=P["iclamp"], lvrt=P["lvrt_ilim"] if layers == "all" else None)
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
            d["trip"] = d["pk"] > lo
        return d
    grids, depths = (None, 50.0) + tuple(s for s in A("scr") if s), (0.0, 0.1, 0.2, 0.3, 0.5)
    rows = [dip(d, s) for s in grids for d in depths]
    clamp_only = [dip(d, s, layers="clamp") for s in (None, 5.0) for d in (0.1, 0.3, 0.5)]
    der = []                                       # the stiff-grid onset is affine in the pre-dip current: solve for the power
    for d in depths:                               # that puts it on the window's low edge, round down, verify
        on = next(r["pk_on"] for r in rows if r["scr"] is None and r["depth"] == d)
        if on <= lo:
            der.append(dict(depth=d, P_max_kW=P_N / 1e3, frac=1.0, onset=on))
            continue
        p2 = 0.6 * P_N
        on2 = dip(d, None, Pop=p2, dur=0.003, tail=0.0)["pk_on"]
        pm = math.floor((p2 + (P_N - p2) * (lo - on2) / (on - on2)) / 500.0) * 500.0
        for _ in range(10):                        # nearly affine: step down 0.5 kW until the simulated onset is inside
            chk = dip(d, None, Pop=pm, dur=0.003, tail=0.0)["pk_on"]
            if chk <= lo:
                break
            pm -= 500.0
        der.append(dict(depth=d, P_max_kW=pm / 1e3, frac=pm / P_N, onset=chk))
    scan = [dip(depths[0], None, ts=t0 + j / F0 / 36, dur=0.003, tail=0.0)["pk_on"] for j in range(6)]
    m50 = [evaluate(P, G["K"], cn, "grid", 50.0, full=False) for cn in CORNERS]
    return dict(rows=rows, clamp_only=clamp_only, derating=der, instant_scan=scan, grids=grids, depths=depths,
                scr50=dict(pm=min(e["pm"] for e in m50), gm=min(min(e["gm"], e["gm_lo"]) for e in m50), ok=all(e["ok"] for e in m50)))


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
        S, X = setup(P, G, "gfm", kind="rl", sec="reseed", sec_T=Ts)
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
        S, X = setup(P, G, "gfm", kind=k0_, sec=sec, sec_T=Ts if sec else None, Pop=Pop)
        X, _ = equil(P, G, S, X)
        r, _ = run(P, G, S, X, tend, lambda t, S_, X_: S_.update(pl=plant(P, "nom", k1_)) if t >= ts else None)
        k = int(ts / T)
        vm, fv, tt = np.abs(r["vc"][k:]) / P["V0"], np.gradient(np.unwrap(r["thv"]), T)[k:] / (2 * math.pi), r["t"][k:]
        return dict(v_min=float(vm.min()), v_max=float(vm.max()), t_v10_ms=1e3 * settle(tt, vm, 1.0, 0.10, ts),
                    t_v1_s=settle(tt, vm, 1.0, 0.01, ts), f_min=float(fv.min()), f_max=float(fv.max()),
                    t_f_s=settle(tt, fv, 0.0, 0.002 * F0, ts), v_end=float(vm[-1]), f_end=float(fv[-1]), pk=ripple_peak(P, r),
                    t_end_s=tend), r
    p_l = 1.5 * P["V0"] ** 2 / P["zb"]
    steps, recs = {}, {}
    for name, a in (("0 -> rated", ("nl", "rl", "reseed", 2.5)), ("rated -> 0", ("rl", "nl", "reseed", 2.5)),
                    ("rated -> 0, no re-seed", ("rl", "nl", True, 2.5)), ("rated -> 0, primary layer only", ("rl", "nl", False, 0.3, p_l))):
        steps[name], recs[name] = stp(*a)
    ss = {}
    for kind, name in (("nl", "no load"), ("rl", "rated resistive")):
        S, X = setup(P, G, "gfm", kind=kind, sec="reseed", sec_T=Ts)
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


def lc_v(P, f, RL=None, term=False, L=None, C=None):
    """continuous per-phase transfer converter voltage -> C_f voltage (term: -> load terminals) with the R_d-C_d branch, L2 + DM
    leakage, R_dc and a resistive load RL (None: no load; L, C override L1, C_f)"""
    w = 2 * np.pi * np.asarray(f, float)
    zl1 = P["R1"] + 1j * w * (L or P["L1"])
    zc = 1 / (1j * w * (C or P["Cf"]) + 1 / (P["Rd"] + 1 / (1j * w * P["Cd"])))
    if RL is None:
        return zc / (zl1 + zc)
    zo = P["R2"] + 1j * w * (P["L2"] + P["Ldm"]) + RL
    zp = zc * zo / (zc + zo)
    return zp / (zl1 + zp) * (RL / zo if term else 1.0)


def zs_mismatch(P, u0, hs):
    """zero sequence u0 (one fundamental period, volts) applied to legs whose L1-C_f filters sit at opposite tolerance corners
    (+10 % at 0 A with C_f +5 % against the -10 % part at the trip current with C_f -5 %; unloaded, R_d-C_d included): the
    difference of the two responses per order h, and u0's amplitude per order"""
    U0 = np.abs(np.fft.rfft(u0) / len(u0) * 2)[hs]
    dH = np.abs(lc_v(P, hs * F0, L=P["L1_hi"], C=P["Cf"] * (1 + A("cf_tol"))) - lc_v(P, hs * F0, L=P["L1_lo"], C=P["Cf"] * (1 - A("cf_tol"))))
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
    L1, _, Cf, ti = corner(P, cn)
    Ac, Bu, Bi, Bs, g = np.zeros((8, 8)), np.zeros(8), np.zeros(8), np.zeros(8), 1 / P["Rd"]
    Ac[0, 0], Ac[0, 1], Bu[0] = -P["R1"] / L1, -1 / L1, 1 / L1
    Ac[1, 0], Ac[1, 1], Ac[1, 2], Bi[1] = 1 / Cf, -g / Cf, g / Cf, -1 / Cf
    Ac[2, 1], Ac[2, 2] = g / P["Cd"], -g / P["Cd"]
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
        L2c = corner(P, cn)[1]
        if case == "open":
            return nplant(P, cn)
        if case == "balanced":
            return nplant(P, cn, dm + L2c / 3, (P["R2"] + zb) / 3, 3 * P["Cf"], P["Rd"] / 3, 3 * P["Cd"])
        if case == "unbalanced":
            return nplant(P, cn, dm + L2c, P["R2"] + zb, P["Cf"], P["Rd"], P["Cd"])
        if case == "unbalanced, held":
            return nplant(P, cn, dm + L2c, P["R2"] + zb, src=True)
        x = P["zb"] / scr / math.hypot(1.0, A("grid_rx")) if scr else 0.0
        return nplant(P, cn, dm + (L2c + x / P["w0"]) / 3, (P["R2"] + A("grid_rx") * x) / 3, 3 * P["Cf"], P["Rd"] / 3, 3 * P["Cd"])
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


SM_STATES = ("IDLE", "PRECHARGE", "SYNC", "GFL", "GFM", "STOP", "FAULT", "SERVICE")
SM_OUT = {"IDLE": (0, 0, 0, 0), "PRECHARGE": (0, 1, 0, 0), "SYNC": (1, 0, 1, 0), "GFL": (1, 0, 1, 1), "GFM": (1, 0, 1, 1),
          "STOP": (1, 0, 1, 1), "FAULT": (0, 0, 0, 0), "SERVICE": (0, 0, 0, 0)}          # PWM, K_PRE, K_DC, K_AC1+2
SM_T = {("IDLE", "start"): "PRECHARGE", ("IDLE", "service"): "SERVICE", ("PRECHARGE", "precharged"): "SYNC",
        ("PRECHARGE", "timeout"): "FAULT", ("PRECHARGE", "stop"): "IDLE", ("SYNC", "synced_grid"): "GFL",
        ("SYNC", "dead_bus"): "GFM", ("SYNC", "relay_fail"): "FAULT", ("SYNC", "stop"): "IDLE",
        ("GFL", "island_cmd"): "GFM", ("GFM", "gfl_cmd"): "GFL", ("GFL", "grid_out"): "STOP", ("GFM", "grid_out"): "GFM",
        ("GFL", "stop"): "STOP", ("GFM", "stop"): "STOP", ("GFL", "limit_timeout"): "STOP", ("GFM", "limit_timeout"): "STOP",
        ("STOP", "stopped"): "IDLE", ("FAULT", "clear"): "IDLE", ("SERVICE", "exit"): "IDLE"}
SM_EVENTS = sorted({e for _, e in SM_T} | {"hw_trip", "fw_trip"})
for _s in SM_STATES:                                         # every state: hardware latch and firmware hazard trip -> FAULT
    SM_T[(_s, "hw_trip")] = SM_T[(_s, "fw_trip")] = "FAULT"


def verify_sm():
    """exhaustive check of the transition table over every (state, event) pair: invariants and reachability"""
    nxt = lambda s, e: SM_T.get((s, e), s)
    inv = {"I1 hw_trip and fw_trip lead to FAULT from every state": all(nxt(s, "hw_trip") == nxt(s, "fw_trip") == "FAULT" for s in SM_STATES),
           "I2 FAULT: PWM off, precharge and AC contactors open": SM_OUT["FAULT"][0] == SM_OUT["FAULT"][1] == SM_OUT["FAULT"][3] == 0,
           "I3 AC contactors close only out of SYNC on a permissive": all(
               not (SM_OUT[nxt(s, e)][3] and not SM_OUT[s][3]) or (s == "SYNC" and e in ("synced_grid", "dead_bus"))
               for s in SM_STATES for e in SM_EVENTS),
           "I4 DC contactor closes only out of PRECHARGE on 'precharged'": all(
               not (SM_OUT[nxt(s, e)][2] and not SM_OUT[s][2]) or (s, e) == ("PRECHARGE", "precharged")
               for s in SM_STATES for e in SM_EVENTS),
           "I5 PWM only in SYNC, GFL, GFM, STOP": all(SM_OUT[s][0] == (s in ("SYNC", "GFL", "GFM", "STOP")) for s in SM_STATES),
           "I6 FAULT is left only by 'clear', to IDLE": all(nxt("FAULT", e) in ("FAULT", "IDLE") and (nxt("FAULT", e) == "FAULT" or e == "clear") for e in SM_EVENTS)}

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
             "everything (calibration, self-test)", "latch held, ENABLE")]


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
               "MISMATCH (stiff-grid onset: firmware derating, section 4b)" if over else "OK"))
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
               f"{tr4['vmax']:.0f} V", "MISMATCH (cross-study; DC/DC power ramp and a coordinated trip needed)" if bad else "OK"))
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
    ls, st = R["gfm"]["load_step"], R["sec"]["steps"]
    hw.append(("Grid-side current", "not sensed (i_2 estimated as i_1 - C_f dv_C/dt)", f"grid forming off-grid: a 0 -> rated "
               f"resistive step dips v_C to {ls['vmin']:.2f} pu, within 10 % after {ls['t10_ms']:.0f} ms; a rated -> 0 step overshoots to "
               f"{st['rated -> 0']['v_max']:.2f} pu with the restoration ({st['rated -> 0, primary layer only']['v_max']:.2f} pu from the "
               f"primary layer's {ls['v_end']:.2f} pu; averaged model, first 0.2 ms: L1's current charges C_f before the inner loop turns it); a "
               "load-current feed-forward needs a measured output current (the estimate contains the inner loop's own current)",
               "NOTE (design gap if a stiff dynamic off-grid voltage is required: three grid-side sensors; the published rows are static)"))
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


def clean(o):
    """JSON-safe copy (drops time records)"""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items() if k not in ("rec", "L", "lam", "kv", "ev")}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(o) else float(f"{float(o):.6g}")
    if isinstance(o, (np.integer, int)) and not isinstance(o, bool):
        return int(o)
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, complex):
        return [float(f"{o.real:.6g}"), float(f"{o.imag:.6g}")]
    return o


def write_spec(P, G, R):
    K, Kv = G["K"], G["Kv"]
    spec = {"status": "CALCULATED / SIMULATED by sim/pcs_control.py - not measured, not bench-validated",
            "inputs": {"pcs_spec": os.path.relpath(SPEC, ROOT), "magnetics_rev": P["rev"], "C_dc_F": P["Cdc"], "C_dc_basis": P["Cdc_src"],
                       "f_s_Hz": P["fs"], "delay_s": 1.5 * P["T"], "current_chain_delay_s": P["ti"], "vC_divider_Hz": P["f_vdiv"]},
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
            "hardware_mismatches": R["hw"], "agreement_A": R["agree"], "plant_check_rel": R["plant_check"]}
    spec = clean(spec)
    json.dump(spec, open(os.path.join(OUT, "pcs_control_spec.json"), "w"), indent=1)
    return spec


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
        ("stiff-grid deep-dip derating", ("the onset peak forms in the first ms, before the sensing chain and 1.5 T_s let any firmware act: "
         f"at a stiff connection rated current reaches the window for residual voltages of {max(r['depth'] for r in tr):g} pu and below; "
         "where ride-through below that is required at a connection stiffer than SCR 50, limit the continuous current: "
         + ", ".join(f"to {d['depth']:g} pu {d['P_max_kW']:.1f} kW ({100 * d['frac']:.0f} %)" for d in rt["derating"] if d["frac"] < 1))
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
         "act on the phase-to-N means - independent quantities")]


def tab(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(c) for c in r) + " |"
                                                                                    for r in rows]) + "\n"


def g1(x, n=1):
    return "inf" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.{n}f}"


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
    a(f"**What firmware cannot do.** The onset peak forms within the first ms, before the C_f divider's lag "
      f"({P['tv'] * 1e6:.0f} us) and the 1.5 T_s delay ({1.5 * P['T'] * 1e6:.1f} us) let any firmware act: on a stiff grid it reaches "
      + ", ".join(f"{v:.0f} A at {k:g} pu" for k, v in on.items()) + f" against the {lo:.0f} A low edge"
      + (f" - rated current trips the window for residual voltages of {max(d['depth'] for d in tr):g} pu and below at "
         + ", ".join(sorted({scr_name(d['scr']) for d in tr})) if tr else "") +
      f"; from SCR 50 down every onset stays inside (SCR 50 current-loop margins: PM {rt['scr50']['pm']:.0f} deg, GM "
      f"{rt['scr50']['gm']:.1f} dB). The study's dip instant is the worst of a 60 deg scan ("
      + ", ".join(f"{x:.0f}" for x in rt["instant_scan"]) + " A). A CMPSS cycle-by-cycle threshold below the window or a faster "
      "divider would be hardware (out of this firmware-only scope).\n")
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
      "inner loop turns it (no measured output current, section 10); the re-seed shortens the tail above +1 % from "
      f"{st['rated -> 0, no re-seed']['t_v1_s']:.2f} s to {st['rated -> 0']['t_v1_s']:.2f} s. Averaged model: above the DC-link half "
      "the switched bridge's diodes would clamp sooner; the published accuracy rows are static.\n")
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
      "mismatch (corner pair: +10 % L at 0 A / C_f +5 % against the -10 % part at the trip current / C_f -5 %) and, three-wire, through "
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
         f"{max(d['depth'] for d in trip):g} pu (latency-bound, before any firmware acts): derating "
         + ", ".join(f"{d['P_max_kW']:.1f} kW at {d['depth']:g} pu" for d in rt["derating"] if d["frac"] < 1) + "." if trip else
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
      f"moves the N node by {nl['excursion']['pk_pu']:.2f} pu for {nl['excursion']['t1_ms']:.0f} ms.")
    mm = [h[0] for h in R["hw"] if h[3].startswith("MISMATCH")]
    a(f"- **Drawn hardware:** {len(mm)} mismatches (section 10): " + "; ".join(mm) + ".\n")
    a("## What it does not establish\n")
    a("- No switched model: PWM ripple is added to peaks as +ripple/2, dead time is a voltage disturbance, no device or "
      "sensor nonlinearity. THDi is an estimate from the linear closed loop with assumed background distortion, not a "
      "measurement or a switched simulation; THDu (8b) is analytical in the same sense.")
    a("- No standards text: grid-code limits (ROCOF, phase jump, LVRT, islanding times) are quoted from memory and labelled; "
      "no compliance is claimed, in particular not IEC 62116 anti-islanding (not simulated).")
    a("- Not covered: the four-wire build only linearly (section 9b: N-leg loop margins and the N-node excursion; no switched N "
      "leg, no nonlinear unbalanced transient), parallel units (the restoration consensus over the module CAN is stated, not "
      "modelled), LVRT reactive-current injection profiles (the ride-through study holds the power reference), the AC-side "
      "start-up path as a state of the checked machine (drawn: grid tap and AC precharge, pcs_spec ac_start; the rectifier stage "
      "after it is this study's DC-link loop), common-mode/leakage control (the C_f star on the DC midpoint is common mode only), "
      "switching-frequency interactions, ADC noise and quantisation, sensor offsets.\n")
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
      f"{P['cmpss']['hi']:.0f} A; dead time {P['dt_gate_ns'][0]:.0f}-{P['dt_gate_ns'][1]:.0f} ns). Corners: nom; low = -10 % "
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
          "loop (PI 40 Hz, DC-current feed-forward = 0 with the DC contactor open) as a rectifier. The state table above does not "
          "yet carry the AC-start states (section 11).\n")
    a("Protection state machine: outputs (PWM, K_PRE, K_DC, K_AC1+2) per state " + ", ".join(f"{s} {v}" for s, v in SM_OUT.items())
      + ". Exhaustive check over every (state, event) pair: " + "; ".join(f"{k}: {'holds' if v else 'FAILS'}" for k, v in R["sm"].items())
      + ". (The check found that a stop during synchronisation routed through CONTROLLED STOP would have closed the AC "
      "contactors; the transition now goes to IDLE.) Verified = the table, not firmware code.\n")
    L.extend(rep_9b(P, R))
    a("## 10. The drawn hardware against the loop\n")
    a(tab(("item", "as drawn / stated", "study result", "verdict"), R["hw"]))
    a("## 11. Open items\n")
    a("- A switched (PWM, dead-time, ripple-sampling) simulation of the chosen loops and a THDi figure from it; ADC noise.")
    a(f"- Phase-current sensor: the frozen {A('sensor')['part']} ({A('sensor')['G_mV_per_A']} mV/A) needs the PCS-CTL window "
      f"ladder re-valued (drawn for {P['G_chk']:.1f} mV/A); its 2 us step response is in the loop here; the residual-current "
      "sensor is still a quotation part.")
    a("- Ride-through: the stiff-grid onset of deep dips (section 4b) is latency-bound - the firmware derating at stiff "
      "connections or a trip; a CMPSS cycle-by-cycle threshold below the window or a faster C_f divider would be hardware "
      "decisions; the ride-through state's thresholds and the dip-time cap follow grid-code values quoted from memory.")
    a(f"- DC/DC coordination: power ramp and a shared trip signal; the DAB study's {P['c_pcs_dab'] * 1e3:.0f} mF assumption.")
    a("- Grid forming without a grid-side current sensor: full-load steps dip to "
      f"{R['sec']['steps']['0 -> rated']['v_min']:.2f} pu and overshoot to {R['sec']['steps']['rated -> 0']['v_max']:.2f} pu "
      "(section 7b, averaged model; the published rows are static); the virtual impedance (0.15 + j0.30 pu) was not re-scanned "
      "with the new voltage-loop gains; paralleled modules' restoration consensus over the CAN is not modelled.")
    a("- PLL: plain SRF on v_C; a positive-sequence (DSOGI) front end for unbalanced grids is a firmware option, not studied.")
    a("- Standards (EN 50549-1, IEC 62116, GB/T 34120) to buy and check against the assumed limits.")
    a("- Four-wire: the per-phase virtual reactance (quadrature generator), the 100 % unbalanced transient as one nonlinear / "
      "switched simulation (section 9b is linear), the mode A / B hand-over; the AC-start states (grid tap, AC precharge, closing "
      "onto the precharged link, rectifier start) in the checked state table (pcs_spec ac_start, firmware rows of the hand-over).")
    a("- VF THDu: the min-max zero sequence near the L1-C_f resonance through the filters' tolerance mismatch is the largest term "
      "at low DC voltage; the third-harmonic zero sequence (firmware option) is not adopted or simulated.\n")
    a("## Files\n")
    a("`sim/pcs_control.py`; `sim/out/pcs_control/`: report.md, pcs_control_spec.json, bode_scr.png, steps.png, faults.png, "
      "pll_weak_grid.png, gfm.png, vf_and_ride_through.png.")
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


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    P = load()
    fc, band, sweep, f_delay = design_fc(P)
    K = ctrl(P, fc)
    gv, gv_fast, gv_rows = design_gfm_voltage(P, K)
    G = gains(P, K, gv["kv"])
    R = dict(fc=fc, band=band, sweep=sweep, f_delay=f_delay, gv=gv, gv_fast=gv_fast, gv_rows=gv_rows, agree=agreement(P, G),
             plant_check=check_plant(P))
    R["table"], R["ad_scan"], R["kad"] = table_current(P, K)
    R["ff"] = ff_variants(P, K)
    R["steps"], R["faults"], R["divider"] = study_steps(P, G), study_faults(P, G), study_divider(P, G)
    R["pll"], R["dc"], R["gfm"] = study_pll(P, G), study_dc(P, G), study_gfm(P, G)
    R["thd_label"] = "design (h1+" + "+".join(f"h{h}" for h in K["harm"]) + f", {fc:.0f} Hz)"
    R["thd"] = study_thd(P, {R["thd_label"]: K,
                             f"design without harmonic terms ({fc:.0f} Hz)": ctrl(P, fc, harm=()),
                             "1 kHz (risk register)": ctrl(P, 1000.0)})
    R["plan"], R["isr"], R["sm"], R["vdc_min"] = sampling_plan(P), isr_budget(P, K), verify_sm(), vdc_min(P)
    R["rt"], R["sec"] = study_ride_through(P, G), study_secondary(P, G)
    R["acc"], R["gfm_harm"] = vf_accuracy(P, R["sec"]), design_gfm_harm(P, K, G["Kv"])
    R["thdu"] = study_thdu(P, G, R)
    R["imb"], R["nleg"] = study_imbalance(P, R), study_neutral(P, G, R)
    R["hw"] = hardware(P, R)
    plots(P, G, R)
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
