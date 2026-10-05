"""PCS-CTL - control board of the PCS-P125 inverter: an assembly variant of gen/pv_ctrl.py (PV-CTL), same schematic
pattern, its own project, values and BOM (D-061; docs/requirements/ARCHITECTURE-PCS.md section 6; contract PCS_PC +
PCS_X in gen/interfaces.py). This script re-values the PV-CTL module in place (its build functions read these module
values at call time) and builds PCS-CTL; gen/pv_ctrl.py and its outputs are not changed. Nothing is bench-validated.

STAGES DONE / TODO (each DONE stage builds and passes every check):
  1 DONE  IL1-3 windows +/-450 A on the PCS phase-current sensor (RFQ: G 4.0 mV/A around its own Uref 2.5 V, ASSUMED as
          on PCS-PWR): divider 10.0k / 10.0k, Uref ladder 7.68k / 40.2k / 7.15k, ADC stage Rf 2.49k; OV comparators
          1050 V on VB (DC link) and 560 V on VA (lower half); inductor OT ladder for the L1 140 C limit; K_A (= AC
          contactor 1) gated by HEALTHY only; the three SELV fan headers of PV-CTL; three-wire assembly bom/PCS-CTL-3W
  2 TODO  PCS_X header into the ADC (VG1-3, VC1-3, RCM, NTC9), K_AC2 through the latch gating, RCM_TST; pin plan
          gen/data/pcs_ctrl_pin_plan.csv with the ADC / PWM budget (the stage-1 build writes the PV-identical plan there).
          Budget (counted, not yet drawn): PWM 6 of 16; ADC: PV plan 22 channels - 6 not used by the three-wire PCS (IA,
          IA_H, VAX, IL4, NTC4, NTC8) + 8 new (VG1-3, VC1-3, RCM, NTC9) = 24 of 25 - holds for three-wire; four-wire
          (IL4, NTC4, NTC8, VGN back) needs 28: does not hold on the F280039C
  3 TODO  module PCS-P125 in gen/build_all.py and gen/cost.py
OPEN: (1) every sensor number of the IL window is an ASSUMPTION for the RFQ sensor (G 4.0 mV/A, Vref 2.475-2.525 V, Voe
      +/-10 mV, X 1 % / 3 % of I_PN 250 A, 1.5 % / 3 % of I_PM 500 A above I_PN, response 1 us; linear to +/-525 A, i.e.
      output swing 0.4-4.6 V - tighter than PCS-PWR's '>= +/-500 A' RFQ line). (2) The window top (495 A)
      rests on the pcs_spec commutation deck at 450 A scaled with current (ESTIMATE: 1050 V + 358 V x 495 / 450 = 1444 V vs
      1445 V); the inverter study has no control_spec, so di/dt, the 600 A limit, response limits, firmware OV (975 V)
      and the 1087 V bus limit are this script's ASSUMPTIONS (PCS_REQ below); the DC OV band is centred so its top is
      the hand-over's 1050 V. (3) The coordinator's brief asked for the DC contactor (K_B)
      gated by HEALTHY only; this build keeps K_B = HEALTHY OR HOLD (the hold-off layer of the DC contactor) and gates K_A
      (AC contactor 1) by HEALTHY only - the item listed by the power board (PCS-PWR OPEN 6).
Usage: .venv/bin/python gen/pcs_ctrl.py
"""
import contextlib
import copy
import io
import os
import sys

import dcdclib as L
import pv_ctrl as C
import pv_power as PP

C.PROJECT, C.REV, C.DATE = "PCS-CTL", "A0", "2026-10-05"
C.PLAN_CSV = os.path.join(C.HERE, "data", "pcs_ctrl_pin_plan.csv")
C.PWR_TXT = os.path.join(L.REPO, "hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt")
C.P75_SUFFIX = "-3W"                      # three-wire assembly: the phase-4 (neutral leg) comparators not fitted

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
    ok &= C.say(gates["K_A"] == "HEALTHY" and gates["K_B"] == "KGATE", "Contactor gating (PCS assembly)",
                "K_A = AC contactor 1 gated by HEALTHY only (a trip opens it at the next zero current); K_B = DC contactor "
                "keeps HEALTHY OR HOLD (hold-off layer; PCS-PWR also holds it in hardware above 0.97-1.03 kA) - OPEN 3")
    C.info("Phase-current sensor ASSUMED (RFQ on PCS-PWR, OPEN 1)", "G %.1f mV/A, Vref %.3f-%.3f V, Voe +/-%.0f mV, X25 1 %% of "
           "I_PN 250 A (1.5 %% of I_PM 500 A above I_PN), X_T 3 %%, response %.1f us; normal peak %.0f A (1.2 x 216 A rms + "
           "ripple), limit %.0f A, di/dt %.2f A/us (ASSUMED)" % (G_CS * 1e3, VREF_CS[0], VREF_CS[1], VOE_CS * 1e3,
                                                                 T_CS * 1e6, I_NORM, I_LIM, DIDT))
    n_fan = sum(1 for p in B.D.parts.values() if p.lib_id.split(":")[1] == "J_FAN")
    ok &= C.say(n_fan == 3, "Fan headers (SELV zone)", "%d x J_FAN on the fan buck: one per heatsink section" % n_fan)
    return ok


if __name__ == "__main__":
    B, pins, P, iso, xing = C.build_design()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        passed = C.design_check(B, pins, P, iso)
        passed &= extra_checks(B)
    print(buf.getvalue(), end="")
    if not passed:
        sys.exit(1)
    rc = L.build(B, domain_of=C.domain_of, isolators=iso, crossings=xing, vrange=C.vrange)
    with open(os.path.join(L.REPO, "hardware", C.PROJECT, "outputs", C.PROJECT + "_design_check.txt"), "w") as f:
        f.write("CALCULATED by gen/pcs_ctrl.py (gen/pv_ctrl.py re-valued) - not measured.\n" + buf.getvalue())
    C.write_p75_bom(B)
    sys.exit(rc)
