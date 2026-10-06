"""PCS-CTL - control board of the PCS-P125 inverter: an assembly variant of gen/pv_ctrl.py (PV-CTL), same schematic
pattern, its own project, values and BOM (D-061; docs/requirements/ARCHITECTURE-PCS.md section 6; contract PCS_PC +
PCS_X in gen/interfaces.py). This script re-values the PV-CTL module in place (its build functions read these module
values at call time) and builds PCS-CTL; gen/pv_ctrl.py and its outputs are not changed. Nothing is bench-validated.

STAGES DONE / TODO (each DONE stage builds and passes every check):
  1 DONE  IL1-3 windows +/-450 A on the PCS phase-current sensor (since PCM-20 the Sinomags STK-250HO/4, 3.2 mV/A around its
          own Uref pin; rev A0 assumed an RFQ 4.0 mV/A part): divider 10.0k / 10.0k, Uref ladder re-valued (C.ILR_LAD),
          ADC stage Rf 2.49k; OV comparators
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
  4 DONE  four-wire assembly PCS-CTL-4W (with PCS-PWR-4W; module PCS-P125-4W): every part fitted (bom/PCS-CTL-4W_BOM.csv) -
          PWM7 / PWM8 = the N leg, the IL4 window and NTC4 / NTC8 comparators, NTC4 / NTC8 through the TMUX1208, VGN from PCS_X
          on pin 21 (B12/C2; 100R / 1 nF + 1 M pull-down: 0 V = a three-wire power board); PWM16 = K_ACPRE, the AC precharge relay
          command of both power-board builds (GPIO15 as a GPIO, gated AND HEALTHY). One pin plan for both builds (the routing does
          not differ): 22 of 23 analog pins with VDAC, PWM 6 / 8 of 16 - both budgets printed
REVIEW (PCM-04 / 05 / 06 / 14 / 20 and the control study's record errors): the IL window, ADC scaling and trip response on the
      STK-250HO/4's data-sheet values (parsed from PCS-PWR's design check, error terms of its p13); the inverter's firmware limits
      (pcs_spec handover firmware_limits) in the firmware table, incl. the upper DC-link half (VB - VA) by firmware; the
      inverter's own ADC plan (sim/out/pcs_control/pcs_control_spec.json firmware.sampling), dead band, CMPSS row and K_A route.
OPEN: (1) closed (PCM-20): the sensor is the STK-250HO/4; what stays open is the maker's missing working-voltage / PD / dv/dt
      statement and qualification (PCS-PWR design check). (2) The window top (495 A)
      rests on the pcs_spec commutation deck at 450 A scaled with current (ESTIMATE: 1050 V + 358 V x 495 / 450 = 1444 V vs
      1445 V); the inverter study has no control_spec, so di/dt, the 600 A limit, response limits, firmware OV (975 V)
      and the 1087 V bus limit are this script's ASSUMPTIONS (PCS_REQ below); the DC OV band is centred so its top is
      the hand-over's 1050 V. (3) The coordinator's brief asked for the DC contactor (K_B)
      gated by HEALTHY only; this build keeps K_B = HEALTHY OR HOLD (the hold-off layer of the DC contactor) and gates K_A
      (AC contactor 1) by HEALTHY only - the item listed by the power board (PCS-PWR OPEN 6); accepted in D-062.
      (4) closed (stage 4): the four-wire assembly is drawn; 22 of 23 analog pins (one spare, pin 23); four fans on three
      fan headers (Y-harness on header 3, fan 4's tach unread).
      (5) RCM sensor (RFQ on PCS-PWR): output level ASSUMED <= its 5.25 V supply and >= 0.25 mA drive into the 23 kOhm
      divider; the RFQ has to state output range and gain (mA per V) before the firmware thresholds are set.
Usage: .venv/bin/python gen/pcs_ctrl.py
"""
import contextlib
import copy
import csv
import hashlib
import io
import json
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
PWR4_TXT = os.path.join(L.REPO, "hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt")   # the four-wire power board
PLAN4_CSV = os.path.join(C.HERE, "data", "pcs_ctrl_pin_plan_4w.csv")   # the four-wire view of the same routing (written each build)
PLAN4_ROUTE = {   # what a line means on the four-wire build (the routing itself does not change)
    "IL4_ADC": "FOUR-WIRE: N-leg current IL4 (STK-250HO/4 on PCS-PWR-4W) - ADC + CMPSS4 window = IL4 backup > TRIP4",
    "NTC_MUX": "FOUR-WIRE: TMUX1208 D: NTC1-4 heatsink sections a, b, c, n; NTC5-7 L1 windings, NTC8 the L_N winding",
    "VGN_ADC": "FOUR-WIRE: N terminal VGN (VMID + V/1203): N-leg loop VGN - VA, relay test of the N pole, DC-component regulator",
    "PWM7_M": "FOUR-WIRE: N leg high side (GPIO6 = ePWM4 A, HRPWM) -> 74LVC08 AND HEALTHY -> PWM7 (PC)",
    "PWM8_M": "FOUR-WIRE: N leg low side (GPIO7 = ePWM4 B, HRPWM) -> 74LVC08 AND HEALTHY -> PWM8 (PC)",
    "PWM16_M": "K_ACPRE (both builds): AC precharge relay command, GPIO15 as a GPIO -> 74LVC08 AND HEALTHY -> PWM16 (PC)"}
PCS_SPEC_PATH = os.path.join(L.REPO, "sim", "out", "pcs_design", "pcs_spec.json")
PORT_SPEC_PATH = os.path.join(L.REPO, "sim", "out", "port_design", "port_spec.json")
PS = json.load(open(PCS_SPEC_PATH))         # firmware limits and the upper-half analysis (PCM-04/05/06/14)
C.P75_SUFFIX = "-3W"                      # three-wire assembly: the phase-4 (neutral leg) comparators not fitted
C.PH4, C.BUILDS, C.P75_DNP = "four-wire only", ("four-wire", "three-wire"), "DNP (three-wire)"
C.TITLE = "PCS-CTL control board"
C.SUBTITLE = "PCS-P125 inverter controller, PV-CTL assembly variant: F280039C on DC-, one trip latch"
C.ROOT_NOTES = ["ARCHITECTURE-PCS section 6; the PV-CTL schematic re-valued by gen/pcs_ctrl.py; PCS_PC / PCS_X levels "
                "as built on PCS-PWR (hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt).",
                "Assembly option: the two TLV9024 marked '%s' are not fitted on the three-wire build "
                "(bom/PCS-CTL-3W_BOM.csv); the four-wire build fits every part (bom/PCS-CTL-4W_BOM.csv)." % C.PH4,
                "Pin plan: gen/data/pcs_ctrl_pin_plan.csv (from SPRSP61C, checked every build)."]

# ---- phase-current sensor of PCS-PWR: Sinomags STK-250HO/4 (PCM-20). Gain, Uref, Voe, source resistances, range and step
# response are parsed from PCS-PWR's design check (pcs_levels); the window's error terms are the same data sheet's
# (sensing/Sinomags-STK-HO-4.pdf Ver 1.0, printed p13): Voe drift +/-10 mV and gain drift +/-1 % over -40..105 C, gain error
# +/-0.5 % at 25 C (factory trim), linearity +/-0.5 % of I_PM. The maker's accuracy (+/-1 % / +/-3 % of I_PN) is stated at I_PN
# (its X_TRange formula divides by V_FS); the windows sit at 1.8-2.1 x I_PN, so the terms are summed at the trip current instead.
STK4_ERR = dict(voe_t=10e-3, err_g=0.005, g_t=0.01, lin=0.005)
C.SENS = dict(ipn=250.0, ipm=625.0)
C.IL_NET = ("10.0k", "10.0k")             # node = IL / 2: +/-625 A -> 0.25-2.25 V at the comparators and the TLV9064
C.IL_FB = ("36.5k", "2.49k")              # ADC = 0.534 IL - 0.205 V: +/-625 A in 0.06-2.20 V


def sens_err(i, cal):
    """STK-250HO/4 error (A) at |i|, offsets apart (Voe is the window's own term): Voe drift + gain drift x |i| + linearity x
    I_PM over -40..105 C, plus the 25 C gain error when not calibrated (replaces PV-CTL's STK-HO/A 75 model)"""
    g = pcs_levels()["g"]
    return STK4_ERR["voe_t"] / g + (STK4_ERR["g_t"] + (0.0 if cal else STK4_ERR["err_g"])) * abs(i) + STK4_ERR["lin"] * C.SENS["ipm"]


C.sens_err = sens_err
C.ILR_LAD = ("11.8k", "32.4k", "11.0k")   # ILnR - TH_ILn_HI - TH_ILn_LO - GND: +/-455 A nominal at the STK-250HO/4's 3.2 mV/A
#                                           (PCM-20; rev A0 7.68k / 40.2k / 7.15k for an assumed 4.0 mV/A would trip near 570 A)
C.LADDER["OV"] = ("2.43k", "11.8k")       # VREF - TH_OV - GND: 1013 V on VB: band top at the 1050 V of the hand-over (re-centred:
#                                           PV-CTL's comparator model now carries the TLV9024 common-mode error, 9.5 V; 2.37k gave 1060 V)
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
PCS_REQ["dead_time"]["firmware_dead_band_ns"] = PS["handover"]["control_engineer"]["sampling"]["dead_time_ns"]   # pcs_spec, not PV
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
              "VG1-3, VC1-3, VGN, RCM, NTC9 from PCS_X (budget below)")
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
    P = [r if r[0] != "K_A_M" else r[:4] + ("AND HEALTHY -> K_A (AC contactor 1: HEALTHY only, D-062; HOLD keeps only K_B)",)
         for r in _plan_pv() if r[0] not in drop]
    for net, lab, route in (("VG1_ADC", "A6", "grid L1 at the terminal (ADC-A), VMID + V/1203"),
                            ("VG2_ADC", "B11", "grid L2 at the terminal (ADC-B)"), ("VG3_ADC", "C1", "grid L3 (ADC-C)"),
                            ("VC1_ADC", "A5", "C_f node L1 (ADC-A), VMID + V/1203"), ("VC2_ADC", "B5", "C_f node L2 "
                             "(ADC-B; GPIO20 = TACH3 in GPIO mode)"), ("VC3_ADC", "B0/C11", "C_f node L3 (ADC-C)"),
                            ("RCM_ADC", "A8", "residual current (type-B sensor through 10.0k / 13.0k)"),
                            ("NTC9", "A9", "DC-link bank (NCP15 on PCS-PWR)"),
                            ("NTC_MUX", "A4/B8", "TMUX1208 D: NTC1-8 (heatsink 1-4, L1 windings 5-8)")):
        P.append((net, lab, "ADC", "ain", route))
    P.append(("VGN_ADC", "B12/C2", "ADC", "ain", "N terminal (four-wire: N-leg loop VGN - VA, relay test of the N pole, DC-component "
                                                 "regulator; ADC-B / C); 0 V on a three-wire power board (1 M pull-down: variant code)"))
    P = [r if r[0] not in PCS_ROUTE else r[:4] + (PCS_ROUTE[r[0]],) for r in P]
    P.append(("K_AC2_M", "GPIO61", "GPIO", "out", "AND HEALTHY -> K_AC2 (AC contactor 2, PCS_X)"))
    P.append(("RCM_TST", "GPIO21", "GPIO", "out", "PCS_X RCM_TST: RCM test winding (100k pull-down on PCS-PWR); AGPIO pin "
                                                  "in GPIO mode, B11 stays on pin 30"))
    P += [("MUX_A%d" % i, g, "GPIO", "out", "TMUX1208 A%d (100k pull-down)" % i) for i, g in enumerate(MUX_A)]
    return P


C.plan = plan
# PCS meaning of PV-CTL lines (the PV plan names them by PV phase): the four-wire N leg and the AC precharge relay command
PCS_ROUTE = {"PWM7_M": "four-wire N leg high side -> 74LVC08 AND HEALTHY -> PWM7 (PC); HRPWM; ePWM4 A",
             "PWM8_M": "four-wire N leg low side -> 74LVC08 AND HEALTHY -> PWM8 (PC); HRPWM; ePWM4 B",
             "PWM16_M": ("K_ACPRE: AC precharge relay command (GPIO15 as a GPIO output, both builds) -> 74LVC08 AND HEALTHY -> PWM16 "
                         "(PC) -> PCS-PWR relay driver")}


def sheet_pcsx(B):
    B.new_sheet("08_pcsx", "PCS_X: grid voltages, RCM, NTC9, K_AC2",
                "2x8 PCS_X contract to PCS-PWR (all LIVE): K_AC2 from the gating (sheet 05),\n"
                "RCM_TST from GPIO21; grid voltages, RCM and NTC9 into the ADC")
    B.block("PCS_X connector (gen/interfaces.py PCS_X; AGND = GND on this board)",
            "VGN = the N terminal of PCS-PWR-4W (left open by the three-wire PCS-PWR). K_AC2 = K_AC2_M AND HEALTHY: a trip\n"
            "opens AC contactor 2 as AC contactor 1. RCM_TST straight from GPIO21 (100k pull-down on PCS-PWR: open = no test current).")
    B.part("J_PCX", IF.pins(IF.PCS_X, rename={"AGND": "GND"}))
    B.block("Grid and C_f voltages (VMID + V/1203 from OPA2388 followers on PCS-PWR)",
            "100 R / 1 nF C0G charge buckets as VA / VB. VG1-3 on ADC-A/B/C and VC1-3 on ADC-A/B/C: each set\n"
            "sampled together (synchronisation, relay test VG against VC with one contactor closed). VGN (four-wire N terminal)\n"
            "on ADC-B/C, 1 M to GND: 0 V marks a three-wire power board.")
    for net in ["VG%d" % k for k in (1, 2, 3)] + ["VC%d" % k for k in (1, 2, 3)] + ["VGN"]:
        B.R(C.ADC_RC[0], net, net + "_ADC")
        B.C(C.ADC_RC[1], net + "_ADC", "GND", diel="C0G", tol="5%")
    B.R("1M", "VGN_ADC", "GND", note="VGN = 0 V without a four-wire power board (variant code); 0.01 % load on the follower")
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


_PW = {}


def pcs_levels():
    """PCS-PWR's levels at the PCS_PC connector (gen/pcs_power.py as drawn); the phase-current sensor's numbers are parsed from
    its design check (the STK-250HO/4 line), so the two boards cannot state different sensors (PCM-20 / PCM-23)."""
    if not _PW:
        rel = os.path.relpath(C.PWR_TXT, L.REPO)
        if not os.path.exists(C.PWR_TXT):
            raise SystemExit("%s missing: build gen/pcs_power.py first" % rel)
        m = re.search(r"STK-250HO/4: ([\d.]+) mV/A around Uref ([\d.]+)-([\d.]+) V \(pin 4\), Voe \+/-([\d.]+) mV, R_out ([\d.]+)-"
                      r"([\d.]+) ohm, R_ref ([\d.]+)-([\d.]+) ohm, linear \+/-([\d.]+) A, response ([\d.]+) us max", open(C.PWR_TXT).read())
        if not m:
            raise SystemExit("%s states no STK-250HO/4 sensor line: rebuild gen/pcs_power.py" % rel)
        g, u0, u1, voe, ro0, ro1, rr0, rr1, ipm, t_res = map(float, m.groups())
        assert ipm == C.SENS["ipm"], "sensor range vs this board's error model"
        v33 = PP.V33
        vmid = (3.0 * 0.998 * 16.35 / 29.8 * 0.999, 3.0 * 1.002 * 16.35 / 29.8 * 1.001)      # port.lean_refs ladder
        r_sh, sw = 100e-6, 0.05
        lin = lambda g_: min((vmid[0] - sw) / (g_ * r_sh), (v33[0] - sw - vmid[1]) / (g_ * r_sh))   # noqa: E731
        _PW.update(v0=2.5, v0_rng=(u0, u1), g=g * 1e-3, uref=(u0, u1), voe=voe * 1e-3, rs_ilr=(rr0 + 99.0, rr1 + 101.0),
                   rs_il=(ro0 + 99.0, ro1 + 101.0), tau_ia=30.1e3 * 470e-12, f_ia=1 / (2 * 3.14159265 * 30.1e3 * 470e-12),
                   vmid=vmid, k_ia=3.0e-3, ia_lin=lin(30), k_ih=1.0e-3, ih_lin=lin(10), il_lin=ipm, t_sens=t_res * 1e-6,
                   t_rc=(ro1 + 101.0) * 1e-9, k_div=1 / 1203.0, alloc=[PP.CTRL_LOAD["+24V"], PP.CTRL_LOAD["+5V"], PP.CTRL_LOAD["+3V3"]],
                   flt_pullup=True, rdy_pullup_on_pwr=True, hs_max=89.9, sink=75.0, hot=(124.5, 140.0),
                   port_oc_on_flt=True, il4_zero=True, ntc_open=True)
    return dict(_PW)


C.pwr_levels = pcs_levels


def pcs_pwr_current():
    """PCM-23: the PCS-PWR design check this board reads (C.PWR_TXT) must exist, be built from the present pcs_spec / port_spec, and
    still state the levels pcs_levels() assumes - otherwise stop instead of using a missing or stale copy"""
    now = tuple(hashlib.sha256(open(p, "rb").read()).hexdigest()[:16] for p in (PCS_SPEC_PATH, PORT_SPEC_PATH))
    for path in (C.PWR_TXT, PWR4_TXT):                               # both builds of gen/pcs_power.py (PCS-PWR, PCS-PWR-4W)
        rel = os.path.relpath(path, L.REPO)
        if not os.path.exists(path):
            raise SystemExit("%s missing: build gen/pcs_power.py first" % rel)
        h = re.search(r"pcs_spec\.json sha256 ([0-9a-f]{16}); port_spec\.json sha256 ([0-9a-f]{16})", open(path).read())
        if not h or h.groups() != now:
            raise SystemExit("%s is stale (not built from the present pcs_spec.json / port_spec.json): rebuild gen/pcs_power.py first" % rel)
    rel = os.path.relpath(C.PWR_TXT, L.REPO)
    t = open(C.PWR_TXT).read()
    pw = pcs_levels()
    vm = re.search(r"VMID ([\d.]+)-([\d.]+) V", t)
    if not (vm and (float(vm.group(1)), float(vm.group(2))) == tuple(round(v, 3) for v in pw["vmid"]) and "V(DC+)/1203" in t
            and "100R + 1 nF (0.13 us)" in t and "shunt 2 x 200 uOhm" in t):
        raise SystemExit("%s no longer states the levels pcs_levels() assumes (VMID band, 1/1203 dividers, IL source RC, shunt): "
                         "update gen/pcs_ctrl.py" % rel)


CTRL_SPEC_PATH = os.path.join(L.REPO, "sim", "out", "pcs_control", "pcs_control_spec.json")


def pwr_dead_time():
    """(hardware dead time at the gates, min / max ns; firmware value below which the hardware stretches) from PCS-PWR's check"""
    m = re.search(r"Dead time at the gates .*?: (\d+)-(\d+) ns >= .*?a firmware dead band below (\d+) ns is extended by the hardware",
                  open(C.PWR_TXT).read())
    if not m:
        raise SystemExit("%s states no 'Dead time at the gates' line: rebuild gen/pcs_power.py" % C.PWR_TXT)
    return tuple(float(x) for x in m.groups())


def adc_plan():
    """the inverter's own ADC sampling plan (control study, sim/out/pcs_control/pcs_control_spec.json firmware.sampling) - read,
    not retyped; fail closed without it"""
    if not os.path.exists(CTRL_SPEC_PATH):
        raise SystemExit("%s missing: run sim/pcs_control.py (the inverter's ADC plan)" % os.path.relpath(CTRL_SPEC_PATH, L.REPO))
    s = json.load(open(CTRL_SPEC_PATH))["firmware"]["sampling"]
    t_per = 1e3 / C.CS["sampling"]["outer_loop_kHz"]
    assert s["va_interval_us"] <= 10.0 and s["all_simultaneous"], "inverter ADC plan: VA / VB every <= 10 us, rounds simultaneous"
    rounds = " / ".join("+".join(n.replace("_ADC", "") for n in r["nets"]) for r in s["rounds"])
    return ("inverter plan (sim/out/pcs_control/pcs_control_spec.json firmware.sampling, read at build time): %.1f conversions per "
            "%.2f us on 3 ADCs = %.0f %% busy at %.2f us per conversion; rounds %s, each converted simultaneously on ADC-A/B/C; VA / VB "
            "%d x per period (every %.1f us <= the 10 us the ADC-PPB over-voltage backup needs); last loop input %.2f us after the "
            "trigger; NTC1-8 through the TMUX1208 in %.0f ms per scan; the CLA budget at 120 MHz is the architecture's R-14"
            % (s["conv_per_period"], t_per, s["busy_pct"], s["t_conv_us"], rounds, s["k_va"], s["va_interval_us"], s["latency_us"],
               s["tmux_scan_ms"]))


_info_pv = C.info


def info(name, detail):
    """PV-CTL's info lines, with the inverter's own ADC plan instead of the PV control_spec's"""
    _info_pv(name, adc_plan() if name == "ADC load" else detail)


C.info = info
_trips_pv = C.check_trips


def check_trips(pw):
    """PV-CTL's trip checks; the CMPSS backup row carries the inverter's requirement (PCS_REQ), not the PV cell band"""
    ok, rows = _trips_pv(pw)
    bk = C.CS["hardware_trips"]["inductor_overcurrent"]["ctrl_backup"]
    req = "%.1f-%.1f A, <= %.2f us (inverter, PCS_REQ)" % (bk["band_A"][0], bk["band_A"][1], bk["max_response_us"])
    assert sum(1 for r in rows if r[0] == "IL1-4 CMPSS backup") == 1, "PV-CTL trip table changed: re-check the CMPSS row"
    return ok, [r[:4] + (req,) if r[0] == "IL1-4 CMPSS backup" else r for r in rows]


C.check_trips = check_trips
_fw_pv = C.firmware_table


def firmware_table(rows):
    """PV-CTL's second-layer table with the inverter's values in the rows that carried PV numbers (CMPSS DAC, soft OV limit, dead
    band, three-wire build), plus the inverter's own limits (pcs_spec handover firmware_limits)"""
    r = {x[0]: x for x in rows}
    il = r["IL1-4 CMPSS backup"]
    hw_lo, hw_hi, fw_floor = pwr_dead_time()
    fw_dt = C.CS["dead_time"]["firmware_dead_band_ns"]
    fix = {
        "IL backup, per phase": lambda x: (x[0], x[1], "%s, DAC = idle zero +/-%.1f A" % (il[2], C.I_BK), x[3], x[4], x[5]),
        "port OC backup": lambda x: (x[0], x[1].replace("PV-P75:", "three-wire:"), x[2].replace("PV-PWR", "PCS-PWR"), x[3], x[4],
                                     x[5]),
        "OV / UV soft limits": lambda x: (x[0], x[1], "%.0f V (PCS_REQ firmware OV, ASSUMED)" % C.CS["hardware_trips"]["firmware_overvoltage_V"],
                                          x[3], x[4], x[5]),
        "dead time": lambda x: (x[0], "ePWM dead-band generator set to %.0f ns (pcs_spec hand-over) - a floor only: PCS-PWR's RC stretch "
                                "extends any firmware value below %.0f ns, so the dead time at the gates is the hardware's %.0f-%.0f ns "
                                "(PCS-PWR design check); dead-time compensation uses that band (identified per leg), not the "
                                "firmware value" % (fw_dt, fw_floor, hw_lo, hw_hi),
                                "%.0f-%.0f ns at the gates (firmware %.0f ns, stretched)" % (hw_lo, hw_hi, fw_dt), x[3], x[4], x[5]),
        "PV-P75 build": lambda x: ("three-wire build (PCS-CTL-3W)", x[1], x[2], x[3], x[4], x[5])}
    out = [fix[x[0]](x) if x[0] in fix else x for x in _fw_pv(rows)]
    assert sum(1 for x in _fw_pv(rows) if x[0] in fix) == len(fix), "PV-CTL firmware table changed: re-check the inverter rows"
    return out + [(x["item"], x["peripheral"], x["threshold"], x["filter"], x["latency"], x["self_test"])
                  for x in PS["handover"]["firmware_limits"]]


C.firmware_table = firmware_table


def extra_checks(B):
    """Lines PV-CTL does not have: the lower-half OV comparator, the upper half by firmware, the K_A gating, the sensor."""
    pw = pcs_levels()
    (th, e), = C.lad_th("OVA")
    vmid = sum(pw["vmid"]) / 2
    v_nom = (th - vmid) / pw["k_div"]
    dv = th * (C.REF_E + e) + C.VIO_CMP + C.cm_err(th) + (pw["vmid"][1] - pw["vmid"][0]) / 2
    lo, hi = v_nom - dv / pw["k_div"] - v_nom * 0.0032, v_nom + dv / pw["k_div"] + v_nom * 0.0032
    ok = C.say(lo > 475.0 * 1.05 and hi <= 575.0, "Trip lower-half over-voltage (TLV9024 on VA = V(M - DC-))",
               "%.0f V nominal -> %.0f-%.0f V (divider, VMID, REF, ladder, VIO, TLV9024 common-mode error); above 1.05 x 475 V "
               "(half of 950 V) and <= 1.15 x U_N(85 C) 500 V of the C3D1U147 = 575 V; the upper half: next line" % (v_nom, lo, hi))
    uh = PS["dc_link"]["upper_half"]
    smp = json.load(open(CTRL_SPEC_PATH))["firmware"]["sampling"]
    t_per = 1e3 / C.CS["sampling"]["outer_loop_kHz"]
    ok &= C.say(not uh["hardware_needed"] and abs(uh["outer_loop_us"] - t_per) < 0.01 and smp["va_interval_us"] <= uh["conversion_us"],
                "Upper DC-link half (VB - VA): firmware limit, no comparator (pcs_spec dc_link upper_half, PCM-04)",
                "%s. Latency basis = this board: outer loop %.2f us, VA / VB converted every %.1f us (<= the %.0f us assumed) -> "
                "<= %.0f us; limit (firmware table below): %s" % (uh["why"], t_per, smp["va_interval_us"], uh["conversion_us"],
                                                                   uh["latency_us"], uh["firmware_limit"]["threshold"]))
    gates = {g[2]: g[1] for g in C.GATES}
    plan_k_a = {r[0]: r[4] for r in C.plan()}["K_A_M"]
    ok &= C.say(gates["K_A"] == gates["K_AC2"] == "HEALTHY" and gates["K_B"] == "KGATE" and "HOLD ->" not in plan_k_a,
                "Contactor gating (PCS assembly)",
                "K_A = AC contactor 1 and K_AC2 = AC contactor 2 gated by HEALTHY only (a trip opens both, they break at "
                "the next zero current; K_AC2 is in the latch evaluation above); K_B = DC contactor keeps HEALTHY OR HOLD "
                "(hold-off layer; PCS-PWR also holds it in hardware above 0.97-1.03 kA) - OPEN 3; pin plan K_A_M: '%s'" % plan_k_a)
    n = C.il_numbers(pw)
    ps = PS["phase_current_sensor"]
    rng = (-n["z_adc"] / n["g_adc"], (3.0 - n["z_adc"]) / n["g_adc"])
    e_cal, e_unc = sens_err(C.IL_NOM_MAX * 0.9, True), sens_err(C.IL_NOM_MAX * 0.9, False)
    assert abs(pw["g"] - ps["gain_mV_per_A"] * 1e-3) < 1e-9 and pw["il_lin"] == ps["linear_range_A"], "PCS-PWR check vs pcs_spec sensor"
    C.info("Phase-current sensor %s (PCS-PWR; data sheet printed p13, PCM-20)" % ps["mpn"],
           "%.1f mV/A around its Uref pin %.2f-%.2f V, Voe +/-%.0f mV, linear +/-%.0f A, step response %.1f us max + PCS-PWR RC "
           "%.2f us, BW %.0f kHz typ (the ADC line's -3 dB is this board's RC chain only); error at %.0f A: %.1f A after the "
           "2-point calibration / %.1f A without (Voe drift %.0f mV, gain drift %.0f %%, gain error %.1f %%, linearity %.1f %% of "
           "I_PM; Voe separately) - the window terms above; ADC %.4f A/LSB, %.0f..%.0f A in 0..3.0 V (the sensor is linear to "
           "+/-%.0f A); normal peak %.0f A (1.2 x 216 A rms + ripple), L1 limit %.0f A, di/dt %.2f A/us (ASSUMED)" %
           (pw["g"] * 1e3, pw["uref"][0], pw["uref"][1], pw["voe"] * 1e3, pw["il_lin"], pw["t_sens"] * 1e6, pw["t_rc"] * 1e6,
            ps["bandwidth_Hz"] / 1e3, C.IL_NOM_MAX * 0.9, e_cal, e_unc, STK4_ERR["voe_t"] * 1e3, STK4_ERR["g_t"] * 100,
            STK4_ERR["err_g"] * 100, STK4_ERR["lin"] * 100, C.LSB / n["g_adc"], rng[0], rng[1], pw["il_lin"], I_NORM, I_LIM, DIDT))
    n_fan = sum(1 for p in B.D.parts.values() if p.lib_id.split(":")[1] == "J_FAN")
    ok &= C.say(n_fan == 3, "Fan headers (SELV zone)", "%d x J_FAN on the fan buck: one per heatsink section (four-wire: fans 3 and 4 "
                "on header 3 through a Y-harness - the fan buck is the PV-P100 design for four fans; fan 4's tach is not read, a "
                "stopped fan 4 shows as NTC4 rising and the heatsink OT ladder trips)" % n_fan)
    t4 = open(PWR4_TXT).read()
    ph4 = sorted(r for r, p in B.D.parts.items() if C.PH4 in str(p.value))
    ok &= C.say(("PWM16_M", "HEALTHY", "PWM16") in C.GATES and gates["K_A"] == gates["K_AC2"] == "HEALTHY" and len(ph4) == 2
                and "FOUR-WIRE AC port: K1 + K2 4-pole" in t4 and "PC: PWM1-8" in t4,
                "Four-wire assembly PCS-CTL-4W (with PCS-PWR-4W; bom/PCS-CTL-4W_BOM.csv: every part fitted)",
                "PWM7 / PWM8 = the N leg (ePWM4 A/B, gated AND HEALTHY like every PWM line); the IL4 window and NTC4 / NTC8 "
                "over-temperature comparators fitted (%s, '%s' on the three-wire build); NTC4 / NTC8 on the TMUX1208; VGN read on pin 21; "
                "the 4-pole contactors' coils keep the 3-pole lines K_A (AC contactor 1) and K_AC2 (AC contactor 2), gated by HEALTHY; "
                "PWM16 = K_ACPRE (the AC precharge relay of both power-board builds) gated by HEALTHY: a trip or a dead controller drops "
                "it; the latch evaluation above covers four phases (build '%s') and three (build '%s'); trip table rows 'IL1-4' are the "
                "four-wire build's" % (", ".join(ph4), C.P75_DNP, C.BUILDS[0], C.BUILDS[1]))
    return ok


def check_adc_budget(P, pins):
    """ADC / PWM budget of the inverter plan for both builds (asserted; one pin plan serves both - the routing does not differ) and
    the PCS_X analog ranges."""
    on_pin = {r[0] for r in P if r[3] == "ain"}
    n_an = sum(1 for d in pins.values() if d["type"] == "i" and any(n[0] in "ABC" and n[1:].isdigit() for n in d["ana"]))
    need = (["IL%d" % k for k in (1, 2, 3)] + ["IB", "IB_H", "VA", "VB", "VBX", "VPE"] +
            ["V%s%d" % (x, k) for x in "GC" for k in (1, 2, 3)] + ["RCM"] + ["NTC%d" % k for k in (1, 2, 3, 5, 6, 7)] +
            ["NTC9", "NTC_IN"])
    need4 = need + ["IL4", "NTC4", "NTC8", "VGN"]                       # four-wire: the N leg's current, heatsink, L_N; N terminal
    got = lambda lst: [s for s in lst if s in on_pin or s + "_ADC" in on_pin or (s in C.ADC_MUXED and "NTC_MUX" in on_pin)]  # noqa
    read, read4 = got(need), got(need4)
    pwm = int(re.search(r"PC: PWM1-(\d+)", open(C.PWR_TXT).read()).group(1))
    pwm4 = int(re.search(r"PC: PWM1-(\d+)", open(PWR4_TXT).read()).group(1))
    fn = {r[0]: r[2] for r in P}
    pwm_ok = all(fn["PWM%d_M" % k] == "EPWM%d_%s" % ((k + 1) // 2, "AB"[(k - 1) % 2]) for k in range(1, pwm4 + 1))
    ok = C.say(len(read) == len(need) == 24 and len(read4) == len(need4) == 28 and len(on_pin) <= n_an - 1 and pwm == 6
               and pwm4 == 8 and pwm_ok and "VGN_ADC" in on_pin,
               "ADC / PWM budget, three- and four-wire inverter (one pin plan %s)" % os.path.relpath(C.PLAN_CSV, L.REPO),
               "THREE-WIRE: the %d signals on %d of the %d analog pins with VDAC, VGN read as the variant code (0 V); FOUR-WIRE: "
               "%d signals on the same %d pins - IL4 on its CMPSS4 pin (B4/C8), NTC4 (heatsink n) and NTC8 (L_N) through the "
               "TMUX1208, VGN on pin 21 (B12/C2, the PV plan's IA_H pin): D-064's '22 of 23 by count' holds; one spare analog pin "
               "left (pin 23, A0/B15/C15). The stage-1 count '24 of 25' took the data sheet's 25 channels, which count B5 / B11 "
               "twice (AGPIO pins 48 / 49). PWM: three-wire %d of 16, four-wire %d of 16 (PCS-PWR-4W PWM1-%d = ePWM1-4 A/B, the N "
               "leg on ePWM4); PWM16 = K_ACPRE on both builds (GPIO15 as a GPIO, latch-gated)" %
               (len(need), len(on_pin), n_an, len(need4), len(on_pin), pwm, pwm4, pwm4))
    rows = list(csv.reader(open(C.PLAN_CSV)))                        # written by the PV-CTL check above: the board as drawn
    i_net, i_rt = rows[0].index("net"), rows[0].index("route")
    with open(PLAN4_CSV, "w", newline="") as f:
        csv.writer(f).writerows(rows[:1] + [r[:i_rt] + [PLAN4_ROUTE.get(r[i_net], r[i_rt])] + r[i_rt + 1:] for r in rows[1:]])
    by = {r[i_net]: r for r in rows[1:]}
    gp = {k: by[k][1] for k in ("PWM7_M", "PWM8_M", "PWM16_M", "HOLD") if k in by}
    ok &= C.say(gp.get("PWM7_M") == "GPIO6" and gp.get("PWM8_M") == "GPIO7" and gp.get("PWM16_M") == "GPIO15" and "HOLD" in gp
                and all(k in by for k in PLAN4_ROUTE),
                "Four-wire pin plan (%s, the same routing as %s)" % (os.path.relpath(PLAN4_CSV, L.REPO), os.path.relpath(C.PLAN_CSV, L.REPO)),
                "PWM7 / PWM8 take GPIO6 / GPIO7 (ePWM4 A / B, pins %s / %s) - routed through the latch gating on every PCS-CTL already, so "
                "the four-wire build frees no pin and gives nothing up (GPIO 55 of 55 on both builds; the HOLD read-back stays on %s); "
                "its additions are signals on existing pins: IL4 (B4/C8, CMPSS4), NTC4 / NTC8 through the TMUX1208, VGN on the analog "
                "pin 21 (B12/C2); K_ACPRE on GPIO15 (PWM16) on both builds" % (by["PWM7_M"][0], by["PWM8_M"][0], gp.get("HOLD")))
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
    pcs_pwr_current()
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
    full = os.path.join(L.REPO, "bom", C.PROJECT + "_BOM.csv")       # four-wire assembly: every part fitted (the board BOM's lines)
    rows = list(csv.reader(open(full)))
    assert all(not r[10] for r in rows[1:]), "PCS-CTL: a not-fitted line on the full board BOM - re-check the four-wire assembly"
    with open(os.path.join(L.REPO, "bom", C.PROJECT + "-4W_BOM.csv"), "w", newline="") as f:
        csv.writer(f).writerows(rows)
    sys.exit(rc)
