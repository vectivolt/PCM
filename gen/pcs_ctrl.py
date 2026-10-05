"""PCS-CTL - control board of the PCS-P125 inverter: an assembly variant of gen/pv_ctrl.py (PV-CTL), same schematic
pattern, its own project, values and BOM (D-061; docs/requirements/ARCHITECTURE-PCS.md section 6; contract PCS_PC +
PCS_X in gen/interfaces.py). This script re-values the PV-CTL module in place (its build functions read these module
values at call time) and builds PCS-CTL; gen/pv_ctrl.py and its outputs are not changed. Nothing is bench-validated.

STAGES DONE / TODO (each DONE stage builds and passes every check):
  1 DONE  IL1-3 windows +/-450 A on the PCS phase-current sensor (RFQ: G 4.0 mV/A around its own Uref 2.5 V, ASSUMED as
          on PCS-PWR): divider 10.0k / 10.0k, Uref ladder 7.68k / 40.2k / 7.15k, ADC stage Rf 2.49k; OV comparators
          1050 V on VB (DC link) and 560 V on VA (lower half); inductor OT ladder for the L1 140 C limit; K_A (= AC
          contactor 1) gated by HEALTHY only; the three SELV fan headers of PV-CTL; three-wire assembly bom/PCS-CTL-3W
  2 DONE  sheet 08: PCS_X socket (X6521FV-2x08, mate of PCS-PWR's box header); K_AC2 = K_AC2_M (GPIO61) AND HEALTHY
          on the 23rd 74LVC08 gate (10k pull-down, inside the latch evaluation); RCM_TST from GPIO21; VG1-3 / VC1-3 on
          100 R / 1 nF, one per ADC (A, B, C); RCM through 10.0k / 13.0k; NTC9 bias 10.0k; NTC1-8 through a TMUX1208 onto
          one ADC pin (MUX_A0-2 = GPIO58-60). IA / IA_H / VAX not read (J_PC pins open). Pin plan
          gen/data/pcs_ctrl_pin_plan.csv; budget asserted: 24 three-wire signals on 21 of the 23 analog pins with VDAC,
          PWM 6 of 16. The stage-1 count '24 of 25' was wrong: the data sheet's 25 channels count B5 / B11 twice (AGPIO
          pins 48 / 49), so 24 signals + VDAC did not fit without the mux (+0.23 USD)
  3 DONE  module PCS-P125 = PCS-PWR + PCS-CTL-3W (the three-wire assembly: neutral-leg comparators not fitted, as
          PV-P75 uses PV-CTL-P75) with fans, guards, harness and standoffs in gen/build_all.py extras; gen/cost.py
          MODULES / MODULE_AMPS; the lines the pricer could not price entered from sim/out/pcs_design/pcs_costed_bom.csv
          as labelled ESTIMATE / RFQ rows (@PCS-P125) in gen/data/cost_estimates.csv.
OPEN: (1) every sensor number of the IL window is an ASSUMPTION for the RFQ sensor (G 4.0 mV/A, Vref 2.475-2.525 V, Voe
      +/-10 mV, X 1 % / 3 % of I_PN 250 A, 1.5 % / 3 % of I_PM 500 A above I_PN, response 1 us; linear to +/-525 A, i.e.
      output swing 0.4-4.6 V - tighter than PCS-PWR's '>= +/-500 A' RFQ line). (2) The window top (495 A)
      rests on the pcs_spec commutation deck at 450 A scaled with current (ESTIMATE: 1050 V + 358 V x 495 / 450 = 1444 V vs
      1445 V); the inverter study has no control_spec, so di/dt, the 600 A limit, response limits, firmware OV (975 V)
      and the 1087 V bus limit are this script's ASSUMPTIONS (PCS_REQ below); the DC OV band is centred so its top is
      the hand-over's 1050 V. (3) The coordinator's brief asked for the DC contactor (K_B)
      gated by HEALTHY only; this build keeps K_B = HEALTHY OR HOLD (the hold-off layer of the DC contactor) and gates K_A
      (AC contactor 1) by HEALTHY only - the item listed by the power board (PCS-PWR OPEN 6); accepted in D-062.
      (4) Four-wire build not drawn (coordinator, D-062): with the NTC mux it counts 22 of 23 analog pins (IL4 keeps its
      CMPSS4 pin, NTC4 / NTC8 are on the mux, VGN would take a spare pin); PCS-PWR leaves VGN open.
      (5) RCM sensor (RFQ on PCS-PWR): output level ASSUMED <= its 5.25 V supply and >= 0.25 mA drive into the 23 kOhm
      divider; the RFQ has to state output range and gain (mA per V) before the firmware thresholds are set.
Usage: .venv/bin/python gen/pcs_ctrl.py
"""
import contextlib
import copy
import io
import math
import os
import re
import sys

import ctrl_c2000
import dcdclib as L
import interfaces as IF
import pv_ctrl as C
import pv_power as PP

C.PROJECT, C.REV, C.DATE = "PCS-CTL", "A0", "2026-10-05"
C.PLAN_CSV = os.path.join(C.HERE, "data", "pcs_ctrl_pin_plan.csv")
C.PWR_TXT = os.path.join(L.REPO, "hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt")
C.P75_SUFFIX = "-3W"                      # three-wire assembly: the phase-4 (neutral leg) comparators not fitted
C.PH4, C.BUILDS, C.P75_DNP = "four-wire only", ("four-wire", "three-wire"), "DNP (three-wire)"
C.TITLE = "PCS-CTL control board"
C.SUBTITLE = "PCS-P125 inverter controller, PV-CTL assembly variant: F280039C on DC-, one trip latch"
C.ROOT_NOTES = ["ARCHITECTURE-PCS section 6; the PV-CTL schematic re-valued by gen/pcs_ctrl.py; PCS_PC / PCS_X levels "
                "as built on PCS-PWR (hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt).",
                "Assembly option: the two TLV9024 marked '%s' are not fitted on the three-wire build "
                "(bom/PCS-CTL-3W_BOM.csv)." % C.PH4,
                "Pin plan: gen/data/pcs_ctrl_pin_plan.csv (from SPRSP61C, checked every build)."]

# ---- phase-current sensor of PCS-PWR (RFQ): ASSUMED values, stated in the design check (OPEN 1)
G_CS, VREF_CS, VOE_CS, T_CS = 4.0e-3, (2.475, 2.525), 10e-3, 1.0e-6
C.SENS = dict(ipn=250.0, ipm=500.0, x25=(0.01, 0.015), xt=0.03)
C.IL_NET = ("10.0k", "10.0k")             # node = IL / 2: +/-500 A -> 0.25-2.25 V at the comparators and the TLV9064
C.IL_FB = ("36.5k", "2.49k")              # ADC = 0.534 IL - 0.205 V: +/-500 A in 0.06-2.20 V
C.ILR_LAD = ("7.68k", "40.2k", "7.15k")   # ILnR - TH_ILn_HI - TH_ILn_LO - GND: +/-455 A nominal
C.LADDER["OV"] = ("2.37k", "11.8k")       # VREF - TH_OV - GND: 1026 V on VB: band top at the 1050 V of the hand-over
C.LADDER["OVA"] = ("5.11k", "11.8k")      # VREF - TH_OVA - GND: 540 V on VA (lower half; midpoint against DC-)
C.LADDER["OTL"] = ("30.9k", "1.87k")      # VREF - TH_OTL - GND: L1 winding about 130 C (its hot-spot limit 140 C)
C.TH_NETS["OVA"] = ("TH_OVA",)
C.CMP_MAIN = [("TH_OVA", "VA", "OVT_N") if c == ("TH_OV", "VA", "OVT_N") else c for c in C.CMP_MAIN]
C.GATES = [("K_A_M", "HEALTHY", "K_A") if g[0] == "K_A_M" else g for g in C.GATES]
assert ("K_B_M", "KGATE", "K_B") in C.GATES, "the DC contactor keeps HEALTHY OR HOLD (OPEN 3)"
I_NORM = 1.2 * 216.0 * 2 ** 0.5 + 30.9     # A: 200 ms overload peak + half ripple (pcs_spec current limit, L1 ripple)
C.IL_LO_FACTOR, C.IL_TOP_MAX, C.I_BK, C.LATCH_I, C.LATCH_VA, C.V_PPB = 1.05, 495.0, 532.0, 520.0, 400.0, 1045.0
C.HOLD_KEEPS = ("K_B",)                   # K_A = AC contactor 1: a trip opens it even with the DC hold-off active
C.IL_NOM_MAX = 500.0                      # the trip sits above I_PN (250 A), inside the measuring range (the error model
#                                           then takes the I_PM terms: 1.5 % + 3 % of 500 A)

# ---- requirements the PV control_spec / cell_spec gave PV-CTL, re-stated for the inverter (ASSUMED, OPEN 2)
DIDT = 1050.0 / 120e-6 / 1e6              # A/us: 1050 V across L1 (120 uH)
I_LIM = 600.0                             # A: L1 near B_sat (1.10 T at 450 A; 1.40-1.56 T at 100-25 C) - ESTIMATE
PCS_REQ = copy.deepcopy(C.CS)
_io = PCS_REQ["hardware_trips"]["inductor_overcurrent"]
_io["didt_max_A_per_us"] = DIDT
_io["local"]["max_response_us"] = (I_LIM - C.IL_TOP_MAX) / DIDT
_io["ctrl_backup"]["max_response_us"] = (I_LIM - 572.0) / DIDT
_io["ctrl_backup"]["band_A"] = [492.0, 572.0]
PCS_REQ["measurement_requirements"]["cell_inductor_current"].update(
    range_A=[-500.0, 500.0], resolution_A_per_LSB=0.5, bandwidth_kHz_min=100.0, group_delay_us_max=4.0,
    comparator_threshold_band_A=[492.0, 572.0])
PCS_REQ["hardware_trips"]["port_overvoltage"]["basis"] = ("bus at gates-off <= 1087 V (pcs_spec deck: 1445 V device limit "
                                                          "less the 358 V turn-off overshoot at 450 A)")
PCS_REQ["hardware_trips"]["firmware_overvoltage_V"] = 975.0
PCS_REQ["hardware_trips"]["port_overvoltage"]["full_current_frozen_case"]["slope_V_per_us"] = 250.0 / 350.0
C.CS = PCS_REQ
C.CELL = {"inductor": {"I_at_50pct_L0_A": I_LIM, "I_peak_normal_max_A": I_NORM}}

# ---- stage 2: the PCS_X header (sheet 08) and the inverter's own pin plan
C.PARTS["J_PCX"] = dict(C.PARTS["J_PC"], mpn="X6521FV-2x08-C85D32", pkg="2x8 2.54 mm female THT",
                        stock=("Connector_Generic", "Conn_02x08_Odd_Even"),
                        desc="Board-to-board socket 2x8 2.54 mm, PCS_X contract (gen/interfaces.py), LIVE")
C.PARTS["TMUX1208"] = ctrl_c2000.PARTS["TMUX1208"]
C.PRICE["X6521FV-2x08-C85D32"] = (0.26, "ESTIMATE (XKB 2x22 0.7208 USD scaled by 8/22 pins)")
C.PRICE["TMUX1208PWR"] = (0.227, "TI.com 1ku (prices.csv)")
C.GATES = C.GATES + [("K_AC2_M", "HEALTHY", "K_AC2")]
# ADC: the 100-pin F280039C has 23 analog pins (its '25 channels' count B5 / B11 twice: AGPIO pins 48 / 49 are the
# alternatives to pins 32 / 30, SPRSP61C 5.4.3). The inverter reads 24 signals + VDAC, so NTC1-8 share one pin through
# a TMUX1208 (as on CTRL-C2000; the hardware OT comparators keep the NTC nets themselves).
C.PC_NC = ("IA", "IA_H", "VAX")           # held at VMID on PCS-PWR: not read here
C.AFE_PC = tuple(n for n in C.AFE_PC if n not in C.PC_NC)
C.ADC_SKIP, C.ADC_MUXED = C.PC_NC, tuple("NTC%d" % k for k in range(1, 9))
C.ADC_NOTE = ("; PCS-CTL: NTC1-8 share one pin through the TMUX1208, IA / IA_H / VAX not read (VMID on PCS-PWR), plus "
              "VG1-3, VC1-3, RCM, NTC9 from PCS_X (budget below)")
C.VREF_XTRA = 3.0 / 10.0e3                # NTC9 bias, probe shorted
RCM_DIV = ("10.0k", "13.0k")              # RCM - RCM_ADC - GND: a sensor output up to its 5.25 V supply reads <= 3.0 V
MUX_A = ("GPIO58", "GPIO59", "GPIO60")    # TMUX1208 A0-A2
B_NCP15 = 3380.0                          # NCP15XH103F03RC B25/50 (Murata catalogue, docs/datasheets/sensing)
C.GATE_NOTE = ("\nPCS-CTL: K_A (AC contactor 1) and K_AC2 (AC contactor 2) = K_x_M AND HEALTHY; only K_B (DC contactor)\n"
               "keeps KGATE (HEALTHY OR HOLD).")
_plan_pv = C.plan


def plan():
    """The PV-CTL plan, IA / IA_H / VAX and NTC1-8 off their pins, plus the PCS_X lines: VG1-3 and VC1-3 one per ADC
    (A, B, C: each set sampled together), RCM, NTC9, the NTC mux; K_AC2_M into the gating, RCM_TST, the mux address."""
    drop = {"IA_ADC", "IA_H_ADC", "VAX_ADC"} | {"NTC%d" % k for k in range(1, 9)}
    P = [r for r in _plan_pv() if r[0] not in drop]
    for net, lab, route in (("VG1_ADC", "A6", "grid L1 at the terminal (ADC-A), VMID + V/1203"),
                            ("VG2_ADC", "B11", "grid L2 at the terminal (ADC-B)"), ("VG3_ADC", "C1", "grid L3 (ADC-C)"),
                            ("VC1_ADC", "A5", "C_f node L1 (ADC-A), VMID + V/1203"), ("VC2_ADC", "B5", "C_f node L2 "
                             "(ADC-B; GPIO20 = TACH3 in GPIO mode)"), ("VC3_ADC", "B0/C11", "C_f node L3 (ADC-C)"),
                            ("RCM_ADC", "A8", "residual current (type-B sensor through 10.0k / 13.0k)"),
                            ("NTC9", "A9", "DC-link bank (NCP15 on PCS-PWR)"),
                            ("NTC_MUX", "A4/B8", "TMUX1208 D: NTC1-8 (heatsink 1-4, L1 windings 5-8)")):
        P.append((net, lab, "ADC", "ain", route))
    P.append(("K_AC2_M", "GPIO61", "GPIO", "out", "AND HEALTHY -> K_AC2 (AC contactor 2, PCS_X)"))
    P.append(("RCM_TST", "GPIO21", "GPIO", "out", "PCS_X RCM_TST: RCM test winding (100k pull-down on PCS-PWR); AGPIO pin "
                                                  "in GPIO mode, B11 stays on pin 30"))
    P += [("MUX_A%d" % i, g, "GPIO", "out", "TMUX1208 A%d (100k pull-down)" % i) for i, g in enumerate(MUX_A)]
    return P


C.plan = plan


def sheet_pcsx(B):
    B.new_sheet("08_pcsx", "PCS_X: grid voltages, RCM, NTC9, K_AC2",
                "2x8 PCS_X contract to PCS-PWR (all LIVE): K_AC2 from the gating (sheet 05),\n"
                "RCM_TST from GPIO21; grid voltages, RCM and NTC9 into the ADC")
    B.block("PCS_X connector (gen/interfaces.py PCS_X; AGND = GND on this board)",
            "VGN (four-wire build) not connected (OPEN 4). K_AC2 = K_AC2_M AND HEALTHY: a trip opens AC contactor 2\n"
            "as AC contactor 1. RCM_TST straight from GPIO21 (100k pull-down on PCS-PWR: open = no test current).")
    B.part("J_PCX", {k: (None if v == "VGN" else v) for k, v in IF.pins(IF.PCS_X, rename={"AGND": "GND"}).items()})
    B.block("Grid and C_f voltages (VMID + V/1203 from OPA2388 followers on PCS-PWR)",
            "100 R / 1 nF C0G charge buckets as VA / VB. VG1-3 on ADC-A/B/C and VC1-3 on ADC-A/B/C: each set\n"
            "sampled together (synchronisation, relay test VG against VC with one contactor closed).")
    for net in ["VG%d" % k for k in (1, 2, 3)] + ["VC%d" % k for k in (1, 2, 3)]:
        B.R(C.ADC_RC[0], net, net + "_ADC")
        B.C(C.ADC_RC[1], net + "_ADC", "GND", diel="C0G", tol="5%")
    B.block("Residual current (type-B sensor, RFQ on PCS-PWR; output level ASSUMED, OPEN 5)",
            "10.0k / 13.0k: a sensor output up to its 5.25 V supply reads <= 3.0 V; 1 nF C0G charge bucket.")
    B.R(RCM_DIV[0], "RCM", "RCM_ADC")
    B.R(RCM_DIV[1], "RCM_ADC", "GND")
    B.C(C.ADC_RC[1], "RCM_ADC", "GND", diel="C0G", tol="5%")
    B.block("NTC9 (DC-link bank) and the NTC mux",
            "NTC9: Murata NCP15 on PCS-PWR, 10.0k from VREF + 100 nF here. TMUX1208: S1-S8 = NTC1-8 (each keeps\n"
            "its 100 nF and its hardware OT / open-probe comparators, sheet 04) -> NTC_MUX, one ADC pin; A0-A2 from\n"
            "GPIO58-60 with 100k pull-downs, EN high. Firmware converts >= 5 ms after an address change.")
    B.R(C.NTC_BIAS["hs"], "VREF", "NTC9", tol="0.1%", note="25 ppm/K")
    B.C("100n", "NTC9", "GND")
    B.part("TMUX1208", {"13": "+3V3A", "2": "+3V3A", "1": "MUX_A0", "16": "MUX_A1", "15": "MUX_A2", "3": None,
                        "14": "GND", "4": "NTC1", "5": "NTC2", "6": "NTC3", "7": "NTC4", "12": "NTC5", "11": "NTC6",
                        "10": "NTC7", "9": "NTC8", "8": "NTC_MUX"})
    B.C("100n", "+3V3A", "GND")
    for i in range(3):
        B.R("100k", "MUX_A%d" % i, "GND", note="address defined in reset")


C.EXTRA_SHEETS = [sheet_pcsx]
_vrange_pv = C.vrange
C.vrange = lambda net: (0.0, 5.25) if net == "RCM" else _vrange_pv(net)    # RCM sensor output (ASSUMED <= its +5V)


def pcs_levels():
    """PCS-PWR's levels at the PCS_PC connector (gen/pcs_power.py as drawn; the sensor values are OPEN 1)."""
    v33 = PP.V33
    vmid = (3.0 * 0.998 * 16.35 / 29.8 * 0.999, 3.0 * 1.002 * 16.35 / 29.8 * 1.001)      # port.lean_refs ladder
    r_sh, sw = 100e-6, 0.05
    lin = lambda g: min((vmid[0] - sw) / (g * r_sh), (v33[0] - sw - vmid[1]) / (g * r_sh))
    return dict(v0=2.5, v0_rng=VREF_CS, g=G_CS, uref=VREF_CS, voe=VOE_CS, rs_ilr=(111.0, 121.0), rs_il=(114.0, 126.0),
                tau_ia=30.1e3 * 470e-12, f_ia=1 / (2 * 3.14159265 * 30.1e3 * 470e-12), vmid=vmid, k_ia=3.0e-3,
                ia_lin=lin(30), k_ih=1.0e-3, ih_lin=lin(10), il_lin=525.0, t_sens=T_CS, t_rc=(25.0 + 101.0) * 1e-9,
                k_div=1 / 1203.0, alloc=[PP.CTRL_LOAD["+24V"], PP.CTRL_LOAD["+5V"], PP.CTRL_LOAD["+3V3"]],
                flt_pullup=True, rdy_pullup_on_pwr=True, hs_max=89.9, sink=75.0, hot=(124.5, 140.0),
                port_oc_on_flt=True, il4_zero=True, ntc_open=True)


C.pwr_levels = pcs_levels


def extra_checks(B):
    """Lines PV-CTL does not have: the lower-half OV comparator, the K_A gating, the sensor assumptions."""
    pw = pcs_levels()
    (th, e), = C.lad_th("OVA")
    vmid = sum(pw["vmid"]) / 2
    v_nom = (th - vmid) / pw["k_div"]
    dv = th * (C.REF_E + e) + C.VIO_CMP + (pw["vmid"][1] - pw["vmid"][0]) / 2
    lo, hi = v_nom - dv / pw["k_div"] - v_nom * 0.0032, v_nom + dv / pw["k_div"] + v_nom * 0.0032
    ok = C.say(lo > 475.0 * 1.05 and hi <= 575.0, "Trip lower-half over-voltage (TLV9024 on VA = V(M - DC-))",
               "%.0f V nominal -> %.0f-%.0f V (divider, VMID, REF, ladder, VIO); above 1.05 x 475 V (half of 950 V) and "
               "<= 1.15 x U_N(85 C) 500 V of the C3D1U147 = 575 V; the upper half is the total minus this (VB 1050 V "
               "trip), its plausibility is firmware (no midpoint control, two-level)" % (v_nom, lo, hi))
    gates = {g[2]: g[1] for g in C.GATES}
    ok &= C.say(gates["K_A"] == gates["K_AC2"] == "HEALTHY" and gates["K_B"] == "KGATE", "Contactor gating (PCS assembly)",
                "K_A = AC contactor 1 and K_AC2 = AC contactor 2 gated by HEALTHY only (a trip opens both, they break at "
                "the next zero current; K_AC2 is in the latch evaluation above); K_B = DC contactor keeps HEALTHY OR HOLD "
                "(hold-off layer; PCS-PWR also holds it in hardware above 0.97-1.03 kA) - OPEN 3")
    C.info("Phase-current sensor ASSUMED (RFQ on PCS-PWR, OPEN 1)", "G %.1f mV/A, Vref %.3f-%.3f V, Voe +/-%.0f mV, X25 1 %% of "
           "I_PN 250 A (1.5 %% of I_PM 500 A above I_PN), X_T 3 %%, response %.1f us; normal peak %.0f A (1.2 x 216 A rms + "
           "ripple), limit %.0f A, di/dt %.2f A/us (ASSUMED)" % (G_CS * 1e3, VREF_CS[0], VREF_CS[1], VOE_CS * 1e3,
                                                                 T_CS * 1e6, I_NORM, I_LIM, DIDT))
    n_fan = sum(1 for p in B.D.parts.values() if p.lib_id.split(":")[1] == "J_FAN")
    ok &= C.say(n_fan == 3, "Fan headers (SELV zone)", "%d x J_FAN on the fan buck: one per heatsink section" % n_fan)
    return ok


def check_adc_budget(P, pins):
    """Three-wire ADC / PWM budget of the inverter plan (asserted) and the PCS_X analog ranges."""
    on_pin = {r[0] for r in P if r[3] == "ain"}
    n_an = sum(1 for d in pins.values() if d["type"] == "i" and any(n[0] in "ABC" and n[1:].isdigit() for n in d["ana"]))
    need = (["IL%d" % k for k in (1, 2, 3)] + ["IB", "IB_H", "VA", "VB", "VBX", "VPE"] +
            ["V%s%d" % (x, k) for x in "GC" for k in (1, 2, 3)] + ["RCM"] + ["NTC%d" % k for k in (1, 2, 3, 5, 6, 7)] +
            ["NTC9", "NTC_IN"])
    read = [s for s in need if s in on_pin or s + "_ADC" in on_pin or (s in C.ADC_MUXED and "NTC_MUX" in on_pin)]
    pwm = int(re.search(r"PC: PWM1-(\d+)", open(C.PWR_TXT).read()).group(1))
    fn = {r[0]: r[2] for r in P}
    pwm_ok = all(fn["PWM%d_M" % k] == "EPWM%d_%s" % ((k + 1) // 2, "AB"[(k - 1) % 2]) for k in range(1, pwm + 1))
    ok = C.say(len(read) == len(need) == 24 and len(on_pin) <= n_an - 1 and pwm == 6 and pwm_ok,
               "ADC / PWM budget, three-wire inverter (pin plan %s)" % os.path.relpath(C.PLAN_CSV, L.REPO),
               "ADC: the %d signals of the three-wire build on %d of the %d analog pins with VDAC (NTC1-8 share one pin "
               "through the TMUX1208; IL4 stays on its CMPSS4 pin; a four-wire build adds VGN on a spare pin: %d of %d, "
               "not drawn - OPEN 4). The stage-1 count '24 of 25' took the data sheet's 25 channels, which count B5 / B11 "
               "twice (AGPIO pins 48 / 49). PWM: %d of 16 (PCS-PWR PWM1-%d = ePWM1-3 A/B, HRPWM)" %
               (len(need), len(on_pin), n_an, len(on_pin) + 1, n_an, pwm, pwm))
    r_ntc9 = lambda t: 10e3 * math.exp(B_NCP15 * (1 / (t + 273.15) - 1 / 298.15))          # noqa: E731
    v9 = [3.0 * r_ntc9(t) / (r_ntc9(t) + 10e3) for t in (-40, 125)]
    v_rcm = 5.25 * C.val(RCM_DIV[1]) * 1.01 / (C.val(RCM_DIV[0]) * 0.99 + C.val(RCM_DIV[1]) * 1.01)
    ok &= C.say(v9[0] < 3.0 - 8 * C.LSB and v9[1] > 8 * C.LSB and v_rcm <= 3.0, "ADC PCS_X: NTC9 and RCM",
                "NTC9 (NCP15XH103, B %.0f K, 10.0k from VREF): -40 C %.3f V, 125 C %.3f V inside 8 LSB..FS-8 LSB; RCM "
                "sensor output ASSUMED <= its 5.25 V supply (RFQ, OPEN 5) -> <= %.3f V with 1 %% resistors (VREFHI "
                "3.0 V); VG / VC as VA (VMID + V/1203, PCS-PWR checks the range)" % (B_NCP15, v9[0], v9[1], v_rcm))
    leak, c_d = 0.5e-6, 85e-12           # TMUX1208 at 3.3 V: ID(ON) +/-500 nA (-40..85 C), CD(ON) 85 pF (SCDS389C 6.7)
    errs = []
    for k, t in (("hs", 85.0), ("ind", 145.0)):
        r_th = 1 / (1 / C.val(C.NTC_BIAS[k]) + 1 / C.r_ntc(t))
        slope = C.ntc_v(t - 0.5, C.NTC_BIAS[k]) - C.ntc_v(t + 0.5, C.NTC_BIAS[k])
        errs.append(leak * r_th / slope)
    ok &= C.say(max(errs) <= 0.5, "NTC mux (TMUX1208, NTC1-8 -> one ADC pin)",
                "on-leakage 0.5 uA x node resistance: %.2f K at a 85 C heatsink, %.2f K at a 145 C winding (firmware "
                "layer); an address change shares %.1f mV with the next node's 100 nF (85 pF x 3.0 V), settled within "
                "5 ms; the hardware OT / open-probe comparators sit on the NTC nets themselves" %
                (errs[0], errs[1], 3.0 * c_d / 100e-9 * 1e3))
    return ok


if __name__ == "__main__":
    B, pins, P, iso, xing = C.build_design()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        passed = C.design_check(B, pins, P, iso)
        passed &= extra_checks(B)
        passed &= check_adc_budget(P, pins)
    print(buf.getvalue(), end="")
    if not passed:
        sys.exit(1)
    rc = L.build(B, domain_of=C.domain_of, isolators=iso, crossings=xing, vrange=C.vrange)
    with open(os.path.join(L.REPO, "hardware", C.PROJECT, "outputs", C.PROJECT + "_design_check.txt"), "w") as f:
        f.write("CALCULATED by gen/pcs_ctrl.py (gen/pv_ctrl.py re-valued) - not measured.\n" + buf.getvalue())
    C.write_p75_bom(B)
    sys.exit(rc)
