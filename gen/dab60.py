"""DAB60 - power assembly of DAB-D60: 60 kW, 100 kHz isolated bidirectional dual active bridge (REQUIREMENTS.md
DAB-01..12, ECO-05/07/09/10; decisions D-012, D-014, D-015, D-016, D-021). Schematic + BOM only, no layout.

  bridge 1 / 2  CBB011M12GM4T full-bridge module, film DC link, DD600N16K reverse clamp, HF RC snubbers + terminal
                ceramics, four gdrv.channel() UCC21710 drivers (interlock per leg, module NTC on AIN/APWM), CELL connector
                to CTRL-C2000 port C1 / C2: RS-422 receivers with fail-safe bias, FLT driver, driven RDY, ID; local trip
                latch + PWM gating, +24V under-voltage, 5 V / 3.3 V from the CELL +24V; HOB 130-P transformer-current
                sensor on the HV side (UCC12050 supply, AMC3330 -> AN1, ISO7720F OC trips), DC-flux window on AN1,
                AN2 module temperature (APWM), AN3 winding / coolant temperature (NTC)
  magnetics     transformer 11:12 and series inductor (CUSTOM, spec text from dab_spec.json), bolted DC-block link
  ports         port.port() twice: port 1 = PORT tag A (HV1), port 2 = tag B (HV2); AUX-HV tap on port 1 only
  io            PORT connector + ID readback DAC, 14-way SYS-IO-AUX harness, sensor supply, port OV comparators,
                coolant loop to SYS-IO-AUX J801
Every design value is read from sim/out/dab_design/dab_spec.json at build time; design_check() asserts the drawn
values against it and against the datasheets. Nothing here is bench-validated.
Usage: .venv/bin/python gen/dab60.py
"""
import contextlib
import io
import json
import math
import os
import re
import sys
from itertools import product

import catalog
import ctrl_c2000 as CTRL
import dcdclib as L
import gdrv
import interfaces as IF
import port
import sys_io_aux as SYS

PROJECT, REV, DATE = "DAB60", "B0", "2026-10-04"
DS = "docs/datasheets/"
TI = "Texas Instruments"
SPEC_PATH = os.path.join(L.REPO, "sim", "out", "dab_design", "dab_spec.json")
if not os.path.exists(SPEC_PATH):
    raise SystemExit("run .venv/bin/python sim/dab_design.py first (it writes %s)" % os.path.relpath(SPEC_PATH, L.REPO))
SPEC = json.load(open(SPEC_PATH))


def spec(path):
    """dab_spec.json value at 'a/b/c'. A missing key stops the build: the schematic must not drift from the design."""
    v = SPEC
    for k in path.split("/"):
        if not isinstance(v, dict) or k not in v:
            raise SystemExit("dab_spec.json has no '%s' - rerun sim/dab_design.py or update gen/dab60.py" % path)
        v = v[k]
    return v


def parse(pattern, path):
    m = re.match(pattern, spec(path))
    if not m:
        raise SystemExit("dab_spec.json '%s' = %r no longer matches %r" % (path, spec(path), pattern))
    return m.groups()


# ======================================================================== design values (all from dab_spec.json)
F_SW = spec("power_stage/f_sw_Hz")
GATE_V = (int(spec("gate_drive/VGS_on_V")), int(-spec("gate_drive/VGS_off_V")))     # key of gdrv.GATE_V
RG = (spec("gate_drive/RG_on_ext_ohm"), spec("gate_drive/RG_off_ext_ohm"))
_s = parse(r"(\d+) x \(([\d.]+) ohm ([\d.]+) W (\d{4}) \+ ([\d.]+) nF (\w+) ([\d.]+) kV (\d{4})\)",
           "dc_link_snubber_hf/per_bridge")
SNB = dict(n=int(_s[0]), r=float(_s[1]), p=float(_s[2]), r_pkg=_s[3], c=float(_s[4]) * 1e-9, diel=_s[5],
           v=float(_s[6]) * 1e3, c_pkg=_s[7])
_h = parse(r"(\d+) x ([\d.]+) nF (\w+) >= ([\d.]+) kV \(([\w/]+)\)", "dc_link_snubber_hf/hf_caps_per_bridge")
HFC = dict(n=int(_h[0]), c=float(_h[1]) * 1e-9, diel=_h[2], v=float(_h[3]) * 1e3, pkg=_h[4])
_b = parse(r"(\d+) x (\w+) \(([\d.]+) uF\)", "flux_balance/blocking_cap_alternative/bank")
BLOCK = dict(n=int(_b[0]), mpn=_b[1], c=float(_b[2]) * 1e-6)
N1, N2 = (int(x) for x in parse(r"(\d+):(\d+)$", "transformer/turns_ratio_N1_N2"))
OC_TRIP = spec("protection/I_xfmr_oc_trip_A")
DC_TRIP = spec("protection/xfmr_dc_trip_A")
DC_TIME = spec("flux_balance/trip_time_ms") * 1e-3
OV_HW = {1: spec("protection/V1_ov_hw_V"), 2: spec("protection/V2_ov_hw_V")}
OV_FW = {1: spec("protection/V1_ov_fw_V"), 2: spec("protection/V2_ov_fw_V")}
FLOW_TRIP = spec("protection/coolant_flow_trip_lpm")

# Fixed choices of this board (not in the spec), each justified in design_check()
HOB = dict(sens=8e-3, uref=(2.48, 2.52), ipm=250.0, ipn=130.0, bw=1e6, td90=200e-9, uoe=5e-3, tcuoe=0.075e-3,
           iom=0.8, es=0.0075, tcs=200e-6, ic=26e-3)                                   # HOB-P series p9 (HOB 130-P)
HOB.update(rout=30.0, rref=(130.0, 300.0), uc_min=4.6)                     # p9: Uout / Uref series R, UC >= 4.6 V
K_OC = 0.5                    # Uout divider in front of the HV-side OC comparator (20.0k / 20.0k)
# AN1 (HV side): two difference amplifiers (OPA4388 A/B) drive the AMC3330 inputs symmetrically around ~0.5 V; each +
# leg = R1 from its input + Ru (from Uref_B) / Rg (to HGND), each - leg = R3 = R1 from the other input, R2 feedback.
AN1_R = dict(r1=10.0e3, r3=10.0e3, r2=1.56e3, ru=7.87e3, rg=1.96e3)
AMC = dict(gain=2.0, vcm_out=(1.39, 1.49), vcm_in=(-0.925, 0.725), vin_lin=1.0, rout=0.2, bw=(300e3, 375e3),
           idd=41e-3, viowm=1700.0, failsafe=-2.57)    # AMC3330 SBASA34B p5 (VCM at |VIN| = 1 V), p6-7, p8
G_DC, R_DC, C_DC = 16.0, 100e3, 620e-12   # PELV DC-flux difference amp on AN1: 1.60M / 100k, 1.60M || 620 pF C0G
TAU_DC = G_DC * R_DC * C_DC
AN3 = {1: dict(rb=1.0e3, what="transformer winding hot spot (NTC embedded by the magnetics vendor)", t=(25, 130, 155)),
       2: dict(rb=3.65e3, what="coldplate / coolant inlet (B57703M probe)", t=(-20, 50, 90))}
UV_DIV = (100e3, 2.00e3)      # +24V -> TPS3710 SENSE
BIAS = (470.0, 120.0)         # RS-422 fail-safe bias resistors (each side) and termination
LOOP_I, LOOP_V = (2.05e-3, 2.75e-3), 30.0   # J801 wetting (sys_io_aux.py: ISO1211 limiter), 24V_OR bound ASSUMED
CONTACTS = {"VK3": (1.0, 48.0, None), "TSTAT": (0.5, 50.0, "gold WE-1, low-level")}   # A, V, minimum (datasheets above)
CLAMP = dict(vrrm=1600.0, ifsm=19e3, i2t=1.8e6)   # DD600N16K: Infineon DD600N rev 3.4 p2 (IFSM, I2t at Tvj max, 10 ms)
E96 = [round(10 ** (i / 96), 2) for i in range(96)]
E192 = [round(10 ** (i / 192), 2) for i in range(192)]


def e96(x):
    d = 10 ** math.floor(math.log10(x))
    return round(min((v * d for v in E96 + [10.0]), key=lambda r: abs(r - x)), 3)


def drv(a, c, uref, r):
    """One AN1 driver output: + leg r1 from a plus ru (from Uref_B) / rg (to HGND), - leg r3 from c, r2 feedback."""
    rth, vth = r["ru"] * r["rg"] / (r["ru"] + r["rg"]), uref * r["rg"] / (r["ru"] + r["rg"])
    return (1 + r["r2"] / r["r3"]) * (a * rth + vth * r["r1"]) / (r["r1"] + rth) - r["r2"] / r["r3"] * c


def an1(uout, uref, ra=None, rb=None):
    """(VIN, VCM, INP, INN) at the AMC3330 input for HOB Uout / Uref (buffered); ra / rb = resistors of driver A / B."""
    pa, pb = drv(uout, uref, uref, ra or AN1_R), drv(uref, uout, uref, rb or AN1_R)
    return pa - pb, (pa + pb) / 2, pa, pb


def an1_currents(uout, uref):
    """Currents in the r1, ru and r3 legs of drivers A and B at Uout (their sum bounds the draw on H5V)."""
    r, out = AN1_R, []
    rth, vth = r["ru"] * r["rg"] / (r["ru"] + r["rg"]), uref * r["rg"] / (r["ru"] + r["rg"])
    for a, c in ((uout, uref), (uref, uout)):
        vp = (a * rth + vth * r["r1"]) / (r["r1"] + rth)
        out += [(a - vp) / r["r1"], (uref - vp) / r["ru"], (c - vp) / r["r3"]]
    return out


AN1_K = AMC["gain"] * an1(2.5 + HOB["sens"], 2.5)[0]          # V/A at AN1 (differential)


def oc_ladder():
    """HV-side REF3030E 3.0 V -> OC_H -> OC_L -> HGND (about 1 mA, E96): taps at K_OC x (2.5 V +/- 8 mV/A x OC_TRIP)."""
    taps = [K_OC * (2.5 + HOB["sens"] * OC_TRIP), K_OC * (2.5 - HOB["sens"] * OC_TRIP)]
    edges = [3.0] + taps + [0.0]
    return [e96((a - c) * 1e3) for a, c in zip(edges, edges[1:])]


def dc_ladder():
    """PELV 5V_B -> DC_H -> MID -> DC_L -> GND, outer 2.10k: half window = G_DC x AN1_K x DC_TRIP (ratiometric to 5 V)."""
    w = G_DC * AN1_K * DC_TRIP
    rm = e96(w * 2 * 2.10e3 / (5.0 - 2 * w))
    return [2.10e3, rm, rm, 2.10e3]


def ov_pair(v_bus):
    """R1/R2 of the matched differential divider (E192: 0.1 % thin film comes in E192 steps, E96 ratios are too coarse),
    largest ratio with a trip at Vd = VREF_OV x R1/R2 <= the AMC3330 output at v_bus."""
    vd = 2.0 * port.S["divider"]["ratio"] * v_bus             # AMC3330 gain 2 (SBASA34B p6) after the 6 M / 4.99k divider
    best = None
    for r2 in (round(v * 1e5) for v in E192):
        x = vd / VREF_OV * r2
        d = 10 ** math.floor(math.log10(x))
        r1 = round(max(v * d for v in E192 + [10.0] if v * d <= x), 3)
        if best is None or r1 / r2 > best[0] / best[1]:
            best = (r1, r2)
    return best


def ov_trip(r1, r2, e):
    """Bus voltage at which the OV comparator output falls. e = signs of the errors (AMC3330 gain, AMC3330 offset,
    port divider, REF3020E, R1, R2, R1', R2'); 0 = nominal. AMC3330 SBASA34B p6: gain +/-0.2 % + 45 ppm/C, offset
    +/-0.3 mV + 4 uV/C; divider 7 x 0.1 %; REF30E +/-0.1 % + 15 ppm/C; 0.1 % resistors; 100 K swing; OPA2388 VOS
    (7.5 uV) neglected. X = VP a2/(a1+a2), Y = VN b2/(b1+b2) + VREF b1/(b1+b2), VP/VN = 1.44 V +/- Vd/2: OK while Y > X."""
    ratio = port.S["divider"]["ratio"] * (1 + e[2] * 0.002)
    g = 2.0 * (1 + e[0] * (0.002 + 45e-6 * 100))
    vos = e[1] * (0.3e-3 + 4e-6 * 100)
    vref = VREF_OV * (1 + e[3] * (0.001 + 15e-6 * 100))
    a1, a2, b1, b2 = (r * (1 + s * 1e-3) for r, s in zip((r1, r2, r1, r2), e[4:8]))
    f = lambda vd: (1.44 - vd / 2) * b2 / (b1 + b2) + vref * b1 / (b1 + b2) - (1.44 + vd / 2) * a2 / (a1 + a2)
    lo_, hi_ = 0.0, 2.5
    for _ in range(40):
        mid = (lo_ + hi_) / 2
        lo_, hi_ = (mid, hi_) if f(mid) > 0 else (lo_, mid)
    return (lo_ / g - vos) / ratio


def ov_band(r1, r2):
    """(absolute min, nominal, absolute max, min relative to the firmware trip): the firmware OV reads the same AMC3330,
    so for 'hardware above firmware' only the comparator path counts (gain, offset and divider errors are common)."""
    full = [ov_trip(r1, r2, e) for e in product((-1, 1), repeat=8)]
    rel = [ov_trip(r1, r2, (0, 0, 0) + e) for e in product((-1, 1), repeat=5)]
    return min(full), ov_trip(r1, r2, (0,) * 8), max(full), min(rel)


def ov_choice(k):
    """Highest E192 pair whose worst-case trip stays <= the spec hardware OV value."""
    target = OV_HW[k]
    for _ in range(5):
        pair = ov_pair(target)
        band = ov_band(*pair)
        if band[2] <= OV_HW[k]:
            return pair
        target *= OV_HW[k] / band[2] * (1 - 1e-4)
    raise SystemExit("no E192 divider keeps the port %d OV trip below %g V" % (k, OV_HW[k]))


VREF_OV = 2.048               # REF3020E on +3V3S (REF30E needs VIN >= VOUT + 0.2 V: a 3.0 V part does not fit 3.3 V)
OC_LAD, DC_LAD = oc_ladder(), dc_ladder()
OV_R = {1: ov_choice(1), 2: ov_choice(2)}


# ======================================================================== catalog (pin tables cited per entry)
# KEMET C4AQ (F3114_C4AQ 5/5/2025) p14 Table 1 rows + operative derating table; p3 size E/B = 4 leads, two per
# electrode (S1 20.3 mm); KEMET does not number the leads: 1-2 = one electrode, 3-4 = the other (non-polar).
C4AQ = {"C4AQUEW5450A3BJ": dict(key="C4AQ_45U", c=45e-6, vndc=1300.0, v85=1100.0, v105=850.0, esr=3.1e-3, irms=34.0),
        "C4AQQEW5650A3BJ": dict(key="C4AQ_65U", c=65e-6, vndc=1100.0, v85=900.0, v105=700.0, esr=2.6e-3, irms=37.0)}


def _c4aq(mpn):
    r = C4AQ[mpn]
    return dict(mfr="KEMET", mpn=mpn, prefix="C", pkg="radial box 57.5 x 45 x 65 mm, 4 leads (S 52.5, S1 20.3)",
                ds=DS + "passives-capacitors/C4AQ.pdf", pins={"left": ["1 A p", "2 A p"], "right": ["3 B p", "4 B p"]},
                desc="DC-link PP film %g uF, %g VDC at 70 C hot spot (%g V at 85 C, %g V at 105 C), ESR %.1f mOhm, Irms "
                     "%.1f A (10 kHz, dT 30 K)" % (r["c"] * 1e6, r["vndc"], r["v85"], r["v105"], r["esr"] * 1e3, r["irms"]))


MOD_PINS = {"left": ["DC+.%d DC+ p" % k for k in range(1, 9)] + [None] + ["DC-1.%d DC-1 p" % k for k in range(1, 5)] +
                    [None] + ["DC-2.%d DC-2 p" % k for k in range(1, 5)],
            "right": ["AC1.%d AC1 p" % k for k in range(1, 5)] + [None] + ["AC2.%d AC2 p" % k for k in range(1, 5)] +
                     [None] + ["%s %s p" % (x, x) for x in ("G1", "S1", "G2", "S2", "G3", "S3", "G4", "S4")] +
                     [None, "T1 T1 p", "T2 T2 p"]}


def magnetics_desc():
    t, s = "transformer/", "series_inductor/"
    iso = spec(t + "isolation")
    xf = ("CUSTOM HF power transformer (COLD-PLATE MOUNTED), N1:N2 = %d:%d (n %.4f); L_sigma %.2f uH %s; L_m %.0f uH %s; "
          "%.2f mVs per half period, B_pk %.0f mT at 950 V; I_rms %.1f / %.1f A, I_pk %.1f / %.1f A (prim / sec); loss "
          "<= %.0f W; hot spot <= %.0f C (coolant %.0f C); insulation %s, working-voltage basis %.0f V DC, PD <= %.0f pC "
          "at the ECO-10 level (hipot/PD: %s); reference core %s; embedded NTC 10 k +/-2 %%, R/T 8016 (B25/100 3988 K, "
          "as TDK B57703M0103) at the winding hot spot, leads TH1/TH2 reinforced to PELV. Full RFQ: "
          "sim/out/dab_design/report.md sec. 9"
          % (N1, N2, spec(t + "n"), spec(t + "L_sigma_target_H") * 1e6, spec(t + "L_sigma_tol"), spec(t + "L_m_H") * 1e6,
             spec(t + "L_m_tol"), spec(t + "Vs_per_half_period") * 1e3, spec(t + "B_pk_950V_T") * 1e3,
             spec(t + "I1_rms_max_A"), spec(t + "I2_rms_max_A"), spec(t + "I1_pk_max_A"), spec(t + "I2_pk_max_A"),
             spec(t + "loss_max_W"), spec(t + "hot_spot_max_C"), spec("thermal/coolant_inlet_C"), iso["class"],
             iso["working_voltage_basis_V_dc"], iso["PD_max_pC"], iso["hipot_and_PD_levels"], spec(t + "core")))
    trim = spec(s + "L_ext_trim_range_H")
    ls = ("CUSTOM series inductor (COLD-PLATE MOUNTED), %s: L %.2f uH nominal, trim %.2f-%.2f uH so that leakage + L = "
          "%.2f uH %s; I_rms %.1f A, I_pk %.1f A, no saturation to %.0f A; loss <= %.1f W; reference %s"
          % (spec(s + "position"), spec(s + "L_external_nominal_H") * 1e6, trim[0] * 1e6, trim[1] * 1e6,
             spec(s + "L_total_H") * 1e6, spec(s + "L_total_tol"), spec(s + "I_rms_max_A"), spec(s + "I_pk_max_A"),
             spec(s + "I_sat_min_A"), spec(s + "loss_max_W"), spec(s + "reference_core")))
    bc = spec("flux_balance/blocking_cap_alternative")
    link = ("CUSTOM bolted busbar link in the primary AC bus, FITTED: %s. Remove to insert the DC-blocking capacitor "
            "module (%s, not fitted; C_min %.0f uF by the TI rule, %.1f W)" % (bc["provision"], bc["bank"],
                                                                            bc["C_min_TI_rule_F"] * 1e6, bc["loss_W"]))
    return xf, ls, link


XF_DESC, LS_DESC, LINK_DESC = magnetics_desc()
PARTS = {
    # Wolfspeed CBB011M12GM4T Rev 3 (June 2026) p10 "Schematic and Pin Out": DC+ 8 press-fit pins, DC-1 4, DC-2 4,
    # AC1 4, AC2 4, G1-G4 / S1-S4 (Kelvin) and T1/T2 (NTC) one each; switch 1 = high side of AC1, 2 = low side of AC1
    # (source DC-1), 3 = high side of AC2, 4 = low side of AC2 (source DC-2). Wolfspeed does not number the pins:
    # numbered <terminal>.<k> here. p1 VDS 1200 V, VGS(op) -4/+15 V; p3 Visol 3 kV AC, NTC 5 k / B25/50 3380.
    "CBB011M12GM4T": dict(mfr="Wolfspeed", mpn="CBB011M12GM4T", prefix="U", pkg="WolfPACK GM4 press-fit 62.8 x 56.7 mm",
                          ds=DS + "power-semiconductors/CBB011M12GM4T.pdf", pins=MOD_PINS,
                          desc="COLD-PLATE MOUNTED SiC full-bridge module 1200 V 11 mOhm Gen 4, pre-applied TIM, NTC 5 k"),
    "C4AQ_45U": _c4aq("C4AQUEW5450A3BJ"),
    "C4AQ_65U": _c4aq("C4AQQEW5650A3BJ"),
    # snubber parts: dab_spec gives values and "mpn": "RFQ" (CRD UG Fig. 22 footprints 2512 / 2220)
    "R_SNB": dict(mfr="", mpn="", prefix="R", pkg=SNB["r_pkg"], ds="", sourcing="RFQ", stock=("Device", "R"),
                  desc="RFQ snubber resistor %g ohm 1 %% %g W %s thick film, pulse rated" % (SNB["r"], SNB["p"], SNB["r_pkg"])),
    "C_SNB": dict(mfr="", mpn="", prefix="C", pkg=SNB["c_pkg"], ds="", sourcing="RFQ", stock=("Device", "C"),
                  desc="RFQ snubber MLCC %g nF 5 %% %s %g kV %s" % (SNB["c"] * 1e9, SNB["diel"], SNB["v"] / 1e3, SNB["c_pkg"])),
    "C_HF": dict(mfr="", mpn="", prefix="C", pkg=HFC["pkg"], ds="", sourcing="RFQ", stock=("Device", "C"),
                 desc="RFQ DC-link terminal MLCC %g nF %s >= %g kV DC %s" % (HFC["c"] * 1e9, HFC["diel"], HFC["v"] / 1e3,
                                                                          HFC["pkg"])),
    # LEM HOB-P series Version 3 (5 Jul 2023): p12 connection 1 +UC, 2 GND, 3 Uout, 4 Uref, 5 NC, primary jumper
    # 6-9 -> 10-13 (arrow = positive current); p9 HOB 130-P ratings; p4 insulation (basic 1000 V / reinforced 600 V).
    "HOB130P": dict(mfr="LEM", mpn="HOB 130-P", prefix="U", pkg="THT 38 x 22 mm, primary jumper 60 uOhm",
                    ds=DS + "sensing/HOB-P_series.pdf",
                    desc="Open-loop Hall current transducer DC-1 MHz, IPN 130 A rms, IPM +/-250 A, 8 mV/A on Uref 2.5 V, "
                         "tD90 <= 200 ns; insulation basic 1000 V (p4) - used inside HV1/HV2, secondary on the bridge DC-",
                    pins={"left": ["6 IP+ p", "7 IP+ p", "8 IP+ p", "9 IP+ p", None, "10 IP- p", "11 IP- p", "12 IP- p",
                                   "13 IP- p"],
                          "right": ["1 +UC pi", "3 UOUT o", "4 UREF o", "5 NC nc", None, "2 GND pi"]}),
    # TI REF30/REF30E SBVS032K: Fig 5-1 DBZ (1 IN, 2 OUT, 3 GND) p3; REF30xxE +/-0.1 %, 15 ppm/C, VIN >= VOUT + 0.2 V,
    # load +/-10 mA (6.3, 6.5); orderable REF3020EAIDBZR (package addendum). TLV3502 / REF3030E: port.PARTS.
    "REF3020E": dict(mfr=TI, mpn="REF3020EAIDBZR", prefix="U", pkg="SOT-23-3 (DBZ)", ds=DS + "sensing/REF3030.pdf",
                     desc="Voltage reference 2.048 V +/-0.1 %, 15 ppm/C, VIN 2.25-5.75 V",
                     pins={"left": ["1 IN pi"], "right": ["2 OUT po", "3 GND pi"]}),
    "XFMR": dict(mfr="", mpn="", prefix="T", pkg="cold-plate mounted, PM 114/93 class", ds="", sourcing="CUSTOM",
                 desc=XF_DESC, pins={"left": ["P1 P1 p", None, None, "P2 P2 p", None, "TH1 NTC p", "TH2 NTC p"],
                                     "right": ["S1 S1 p", None, None, "S2 S2 p"]}),
    "LSER": dict(mfr="", mpn="", prefix="L", pkg="cold-plate mounted", ds="", sourcing="CUSTOM", stock=("Device", "L"),
                 desc=LS_DESC),
    "LINK": dict(mfr="", mpn="", prefix="JL", pkg="bolted busbar link", ds="", sourcing="CUSTOM",
                 stock=("Jumper", "Jumper_2_Bridged"), desc=LINK_DESC),
    # TDK B57703M (Jan 2018) p2: B57703M0103A019 = 10 k, R/T 8016, B25/100 3988 K, 500 mm AWG 26; +/-2 %, 3 mW/K,
    # 1000 V AC tag test, 55/125/56; R/T table p4
    "B57703M": dict(mfr="TDK Electronics", mpn="B57703M0103A019", prefix="RT", pkg="metal-tag probe, 500 mm leads",
                    ds=DS + "sensing/B57703M.pdf", stock=("Device", "Thermistor_NTC"),
                    desc="COLD-PLATE MOUNTED NTC probe 10 k +/-2 %, B25/100 3988 K (R/T 8016), tag test 1000 V AC"),
    # SIKA VKS/VK3 datasheet 06/2026 V1.1: p1 contact closes at increasing flow, 1 A / 48 VDC / 20 W; p2 set points
    # (DN 15 G1/2: OFF 3.2-4.2 l/min water 20 C); p5 article VK3 15 M0P10 PI31; special set points on request (p2)
    "VK3": dict(mfr="SIKA", mpn="VK315M0P10PI31", prefix="S", pkg="G1/2 brass tee DN 15, 1.5 m PVC cable",
                ds=DS + "sensing/SIKA-VK3.pdf", sourcing="RFQ", stock=("Switch", "SW_Reed"),
                desc="PIPE-MOUNTED reed flow switch, closes at increasing flow. RFQ: factory special set point OFF at "
                     "%.0f l/min decreasing (50/50 glycol, 50 C); standard DN 15 opens at 3.2-4.2 l/min water" % FLOW_TRIP),
    # Honeywell Precision & High Reliability Thermostats 009048-3 p12-13: 3106 series SPST snap-action bimetal, WE-1 gold
    # alloy cross-point contacts for low-level circuits, 500 mA / 50 Vdc (100k cycles), 25 mOhm, 1250 Vac terminal-case;
    # set point, tolerance (+/-2.8 C class, Table 19) and bracket by order. (3100U REDI-TEMP: silver, 5 A - not dry-circuit)
    "TSTAT": dict(mfr="Honeywell", mpn="3106U", prefix="S", pkg="hermetic, flange bracket by order", sourcing="RFQ",
                  ds=DS + "thermal/Honeywell-precision-thermostats.pdf", stock=("Switch", "SW_SPST"),
                  desc="COLD-PLATE MOUNTED low-level bimetal thermostat SPST, gold WE-1 contacts, 500 mA / 50 Vdc; RFQ set "
                       "point: closed when cool, opens 77 +/-2.8 C rising, automatic reset"),
    # Infineon DD600N rev 3.4 (2025-08-06): p4 circuit - terminal 1 = D1 anode + D2 cathode, 2 = D1 cathode, 3 = D2 anode;
    # p2 VRRM 1600 V (16), IFSM 19 kA / I2t 1.8 MA2s at Tvj max 10 ms, VISOL 3.0 kV 1 min, base plate basic insulation
    "DD600N16K": dict(mfr="Infineon Technologies Bipolar", mpn="DD600N16K", prefix="D",
                      pkg="60 mm industrial module, insulated base plate, screw terminals (12 Nm)",
                      ds=DS + "power-semiconductors/DD600N16K.pdf",
                      desc="CHASSIS-MOUNTED dual rectifier diode module 1600 V 600 A, one arm used as DC-link reverse clamp",
                      pins={"left": ["1 A1K2 p"], "right": ["2 K1 p", "3 A2 p"]}),
    # TI UCC12050 SNVSB38D: p3 Table 5-1 (DVE-16): 1 EN, 2 GNDP, 3 VINP, 4 SYNC, 5 SYNC_OK (open drain), 6-8 NC (to
    # GNDP), 9/15/16 GNDS, 10-12 NC (to GNDS), 13 SEL (= VISO: 5.0 V), 14 VISO; p5 VIOWM 1697 VDC reinforced; p7 VISO
    # 4.7-5.3 V at 55 mA; p11 Fig 6-8 IOUT max; 10 uF on VINP and VISO; orderable UCC12050DVE
    "UCC12050": dict(mfr=TI, mpn="UCC12050DVE", prefix="U", pkg="SO-16 wide (DVE), creepage > 8 mm",
                     ds=DS + "power-supply/UCC12050.pdf",
                     desc="Isolated DC/DC 500 mW, 5 V -> 5.0 V (SEL = VISO), reinforced VIOWM 1697 VDC / VIOTM 7071 Vpk",
                     pins={"left": ["3 VINP pi", "1 EN i", "4 SYNC i", "5 SYNC_OK oc", None, "2 GNDP pi", "6 NC p",
                                    "7 NC p", "8 NC p"],
                           "right": ["14 VISO po", "13 SEL i", None, None, None, "15 GNDS pi", "9 GNDS pi", "16 GNDS pi",
                                     "10 NC p", "11 NC p", "12 NC p"]}),
    # TI ISO7720 SLLSEP3G: p4 Fig 5-1 (DW-16): 1/7 GND1, 3 VCC1, 4 INA, 5 INB, 2/6/8 NC, 9/16 GND2, 14 VCC2, 13 OUTA,
    # 12 OUTB, 10/11/15 NC; p10 VIOWM 2121 VDC (DW-16, reinforced); p17-18 tPLH/tPHL <= 18.5 ns; p14 ICC1 <= 4.2 mA,
    # ICC2 <= 2.1 mA (F, inputs high); F suffix = default output LOW; orderable ISO7720FDWR (package addendum)
    "ISO7720F": dict(mfr=TI, mpn="ISO7720FDWR", prefix="U", pkg="SOIC-16 wide (DW), creepage 8 mm",
                     ds=DS + "isolation-interface/ISO7721.pdf",
                     desc="Dual digital isolator 2/0, default output LOW, reinforced VIOWM 2121 VDC, tpd <= 18.5 ns",
                     pins={"left": ["3 VCC1 pi", "4 INA i", "5 INB i", None, "1 GND1 pi", "7 GND1 pi", "2 NC nc",
                                    "6 NC nc", "8 NC nc"],
                           "right": ["14 VCC2 pi", "13 OUTA o", "12 OUTB o", None, "16 GND2 pi", "9 GND2 pi", "10 NC nc",
                                     "11 NC nc", "15 NC nc"]}),
}
PARTS.update({   # same MPN and pin table as port.PARTS, description of the use here
    "LMR36015_5V": dict(port.PARTS["LMR36015"], desc="Buck 4.2-60 V in, 1.5 A, 400 kHz: bridge logic 5 V from the CELL +24V"),
    "REF3030E_LAD": dict(port.PARTS["REF3030E"], desc="Voltage reference 3.0 V 0.1 % 15 ppm/K: trip ladder and NTC excitation"),
    "TLV3502_WIN": dict(port.PARTS["TLV3502"], desc="Dual 4.5 ns rail-to-rail comparator, push-pull: OC / DC-flux windows")})
CTRL_KEYS = ("AM26LV31E", "AM26LV32E", "OPA2388", "OPA4388", "J_CELL", "TPS7A2033")  # pin tables cited in ctrl_c2000.py
SYS_KEYS = ("TPS3710", "SN74LVC1G74", "SN74LVC1G08", "SN74LVC08A", "J_MC4")         # pin tables cited in sys_io_aux.py


def _merge(*dicts):
    out = {}
    for d in dicts:
        dup = set(out) & set(d) - set(catalog.PARTS)
        assert not dup, "catalog key defined twice: %s" % sorted(dup)
        out.update(d)
    return out


CATALOG = _merge(catalog.PARTS, gdrv.PARTS, port.PARTS, {k: CTRL.PARTS[k] for k in CTRL_KEYS},
                 {k: SYS.CATALOG[k] for k in SYS_KEYS}, PARTS)


# ======================================================================== numbers used in notes and in the check
def vid_undriven(vcc, i_leak):
    """Worst-case VP - VN of a received pair nobody drives: P to GND and N to vcc through BIAS[0] (+1 %), BIAS[1]
    (-1 %) between, AM26LV32E ri min 4 k (SLLS849E p5) from P to vcc and from N to GND (the corner that shrinks VID),
    i_leak from an unpowered driver into P and out of N (AM26LV31E IO(OFF) <= 100 uA, SLLS848C p5)."""
    g1, gt, gr = 1 / (BIAS[0] * 1.01), 1 / (BIAS[1] * 0.99), 1 / 4e3
    G = g1 + gt + gr
    a, c = vcc * gr + i_leak, vcc * g1 - i_leak             # VP G - VN gt = a ; VN G - VP gt = c
    return (a - c) / (G + gt)


VID_OPEN, VID_CTRL_OFF = vid_undriven(3.135, 0.0), vid_undriven(3.135, 100e-6)


def uv_band():
    """+24V falling/rising trip of TPS3710 (VIT- 387-400 mV, VIT+ 396-404 mV, SBVS271A 5.5) with 0.1 % dividers."""
    r1, r2 = UV_DIV
    k = lambda e: (r1 * (1 + e) + r2 * (1 - e)) / (r2 * (1 - e))
    return (0.387 * k(-0.001), 0.400 * k(0.001)), (0.396 * k(-0.001), 0.404 * k(0.001))


UV_FALL, UV_RISE = uv_band()


def miller(roff):
    """Die gate-source of an OFF switch while its leg partner turns on, gdrv.design_check() 'mgm4' model (CLMPI direct:
    VEE + 0.5 V at 1 A + 0.6 ohm, in parallel with Roff - 1 % + ROL; I_M = Crss(800 V) x dv/dt; die = pin + I_M RG(int))
    at the given Roff. [(dv/dt V/ns, I_M A, die V)] at the dab_spec edge rate and at the module's ~80 V/ns maximum."""
    U, G, v3 = gdrv.UCC21710, gdrv.GM4, gdrv.rails(GATE_V)[1]
    r_r, out = roff * 0.99 + U["rol"], []
    for dvdt in (spec("gate_drive/dvdt_off_V_per_ns"), gdrv.DVDT):
        im = G["crss"] * dvdt * 1e9
        ic = (r_r * im - U["vclmpi_1a"] + U["rclmpi"]) / (U["rclmpi"] + r_r)
        out.append((dvdt, im, -v3[0] + r_r * (im - ic) + im * G["rg"]))
    return out

# ID-line readback DAC (PV-PORT method, sim/port_design.py proof_test()): CTRL pulls ID to 2.5 V through 10.0 k; each
# port's RB1 / RB2 (3.3 V CMOS, high 3.149-3.381 V) drives ID through R_A / R_B; 0.1 % parts.
RB = dict(r_id=2.61e3, r_a=402e3, r_b=133e3, pullup=10.0e3, vbias=2.5, v_on=(3.315 * 0.98 - 0.1, 3.315 * 1.02))


def id_levels():
    """(levels [(lo, hi)] sorted, smallest gap, R_eff, V unpowered) of the nine RB1+RB2 sums of port A and port B."""
    lv = []
    for na, nb in product(range(3), repeat=2):
        band = []
        for voh, s in product(RB["v_on"], product((-1, 1), repeat=4)):
            ra, rb, rid, rpu = (x * (1 + e * 1e-3) for x, e in zip((RB["r_a"], RB["r_b"], RB["r_id"], RB["pullup"]), s))
            band.append((RB["vbias"] / rpu + voh * (na / ra + nb / rb)) / (1 / rpu + 1 / rid + 2 / ra + 2 / rb))
        lv.append((min(band), max(band)))
    lv.sort()
    gap = min(b[0] - a[1] for a, b in zip(lv, lv[1:]))
    r_eff = 1 / (1 / RB["r_id"] + 2 / RB["r_a"] + 2 / RB["r_b"])
    return lv, gap, r_eff, RB["vbias"] * RB["r_id"] / (RB["r_id"] + RB["pullup"])


# ======================================================================== one bridge (5 sheets)
def bridge(B, b, n0):
    """Bridge b on sheets n0..n0+4. Returns dict(iso, xing, gd {net: domain}, hv {nets}, module ref)."""
    p, t = "B%d_" % b, "B%d" % b
    dcp, dcn, aca, acb = "P%d_BUS+" % b, "P%d_BUS-" % b, p + "ACA", p + "ACB"
    v24, v5, v33 = "+24V_B%d" % b, "5V_B%d" % b, "3V3_B%d" % b
    c_in = {1: ("XP_A", "primary"), 2: ("XS_A", "secondary")}[b]
    iso, xing, gd = set(), set(), {}
    cap = spec("capacitor_banks/port%d" % b)
    ck = C4AQ[cap["mpn"]]["key"]

    # ---------------------------------------------------------------- power stage
    B.new_sheet("%02d_b%d_power" % (n0, b), "Bridge %d power stage" % b,
                "CBB011M12GM4T, port %d (HV%d), %d x %s DC link,\n"
                "DD600N16K reverse clamp, %d RC snubbers + %d ceramics, AC out" % (b, b, cap["qty"], cap["mpn"], SNB["n"],
                                                                                  HFC["n"]))
    B.block("CBB011M12GM4T full-bridge module (port %d side)" % b,
            "Switch 1/2 = leg A (AC1), 3/4 = leg B (AC2). Kelvin sources S1-S4 go to the drivers only (COM).\n"
            "NTC T1/T2 is read by the switch-2 driver (AIN referenced to S2), as on the CRD. Drains of 1/3 = DC+.")
    pins = {"DC+.%d" % k: dcp for k in range(1, 9)}
    pins.update({"DC-%d.%d" % (g, k): dcn for g in (1, 2) for k in range(1, 5)})
    pins.update({"AC1.%d" % k: aca for k in range(1, 5)})
    pins.update({"AC2.%d" % k: acb for k in range(1, 5)})
    for sw, (g, s) in (("H1", ("G1", "S1")), ("L1", ("G2", "S2")), ("H2", ("G3", "S3")), ("L2", ("G4", "S4"))):
        pins.update({g: "G_" + t + sw, s: "KS_" + t + sw})
    pins.update({"T1": p + "NTC", "T2": "KS_" + t + "L1"})
    mod = B.part("CBB011M12GM4T", pins)
    xing.add(mod.ref)
    B.block("DC link %d x %s = %.0f uF" % (cap["qty"], cap["mpn"], cap["C_total_F"] * 1e6),
            "Bank ripple %.0f A rms, %.1f A per capacitor (rating %.0f A); hot spot %.0f C -> %.0f V allowed vs %.0f V.\n"
            "Ratings vs stresses: design_check()." % (cap["I_ripple_rms_bank_A"], cap["I_ripple_rms_per_cap_A"],
                                                     cap["I_rms_rating_per_cap_A"], cap["hot_spot_est_C"],
                                                     cap["V_allowed_at_hot_spot_V"], cap["V_max_operating_V"]))
    for _ in range(cap["qty"]):
        B.part(ck, {"1": dcp, "2": dcp, "3": dcn, "4": dcn})
    cl = spec("dc_link_reverse_clamp/port%d" % b)
    B.block("DC-link reverse clamp (%s, one arm)" % cl["mpn"],
            "Cathode (2) to DC+, anode (1) to DC- at the bank terminals; terminal 3 unused. Reverse-biased in operation.\n"
            "Port short: carries %.1f kA / %.1f kA2s after the bank reverses (I2t %.1f MA2s) instead of the module body\n"
            "diodes. CHASSIS-MOUNTED, insulated base plate (basic, to PE frame)."
            % (cl["fault"]["I_clamp_pk_A"] / 1e3, cl["fault"]["I2t_clamp_A2s"] / 1e3, CLAMP["i2t"] / 1e6))
    B.part(cl["mpn"], {"1": dcn, "2": dcp, "3": None})
    B.block("HF RC snubbers %d x (%g ohm %g W + %g nF) and %d x %g nF terminal ceramics"
            % (SNB["n"], SNB["r"], SNB["p"], SNB["c"] * 1e9, HFC["n"], HFC["c"] * 1e9),
            "Across DC+/DC- at the module pins (CRD UG Fig. 22 / 19 networks). Spread over both legs in layout;\n"
            "resistors: pulse-rated wide-terminal 2512 with a thermal path to the plane (dab_spec %.2f W worst)."
            % spec("dc_link_snubber_hf/P_per_resistor_worst_W"))
    for k in range(SNB["n"]):
        mid = "%sSNB%d" % (p, k + 1)
        B.part("C_SNB", {"1": dcp, "2": mid}, value="%gn %gkV %s" % (SNB["c"] * 1e9, SNB["v"] / 1e3, SNB["diel"]))
        B.part("R_SNB", {"1": mid, "2": dcn}, value="%gR %gW" % (SNB["r"], SNB["p"]))
    for _ in range(HFC["n"]):
        B.part("C_HF", {"1": dcp, "2": dcn}, value="%gn %gkV %s" % (HFC["c"] * 1e9, HFC["v"] / 1e3, HFC["diel"]))
    hv = {dcp, dcn, aca, acb} | {"%sSNB%d" % (p, k + 1) for k in range(SNB["n"])}

    # ---------------------------------------------------------------- gate drive, one leg per sheet
    drain = {"H1": dcp, "L1": aca, "H2": dcp, "L2": acb}
    for leg, (hs, ls) in ((1, ("H1", "L1")), (2, ("H2", "L2"))):
        B.new_sheet("%02d_b%d_gate%s" % (n0 + leg, b, "ab"[leg - 1]), "Bridge %d gate drive leg %s" % (b, "AB"[leg - 1]),
                    "UCC21710 + UCC14241-Q1, +%d/-%d V, Ron/Roff %g/%g ohm, DESAT,\n"
                    "interlock %s<->%s%s" % (GATE_V[0], GATE_V[1], RG[0], RG[1], hs, ls,
                                             "; module NTC on the L1 driver AIN/APWM" if leg == 1 else ""))
        (d1, i1, v1), (d2, i2, v2) = miller(RG[1])
        g80 = miller(gdrv.GM4["r_gate"][1])[1][2]
        B.block("Miller margin (gdrv rev 4 model, Roff %g ohm, CLMPI direct)" % RG[1],
                "Die VGS of the OFF switch while its partner turns on: %+.2f V at %.1f V/ns (dab_spec, I_M %.1f A), %+.2f V\n"
                "at %.0f V/ns (module max, %.1f A) vs VGS(th) 1.44 V at 175 C: fine at the DAB rate, marginal at 80 V/ns\n"
                "(gdrv's own case, Roff %g ohm: %+.2f V). Crss(800 V) understates the low-voltage Cgd: confirm by DPT."
                % (v1, d1, i1, v2, d2, i2, gdrv.GM4["r_gate"][1], g80))
        for sw, other in ((hs, ls), (ls, hs)):
            tag = t + sw
            ntc = p + "NTC" if sw == "L1" else None
            ch = gdrv.channel(B, tag, pwm=p + "PWM_" + sw, en=p + "EN_DRV", flt_n=p + "DRV_FLT_N", rdy=p + "RDY_L",
                              gate="G_" + tag, source="KS_" + tag, drain=drain[sw], vdd="VDD_" + tag,
                              vee="VEE_" + tag, interlock=p + "PWM_" + other, ntc=ntc, apwm=p + "APWM" if ntc else None,
                              vin=v24, vcc=v33, gnd="GND", gate_v=GATE_V, r_on=RG[0], r_off=RG[1])
            iso |= set(ch.iso)
            xing |= set(ch.xing)
            gd.update({net: "GD_" + tag for net in ch.sec})

    # ---------------------------------------------------------------- CELL interface, trip logic, local supplies
    B.new_sheet("%02d_b%d_if" % (n0 + 3, b), "Bridge %d CELL interface + logic" % b,
                "CELL C%d: PWM1-4 / EN RS-422 in (fail-safe), FLT, RDY, ID %s\n"
                "trip latch, PWM gating, +24V UV, 5 V / 3.3 V from the CELL +24V" % (b, IF.ID_OHM["DAB60-B%d" % b]))
    B.block("CELL connector -> CTRL-C2000 port C%d (HRPWM ePWM%d-%d)" % (b, 4 * b - 3, 4 * b - 2),
            "interfaces.CELL. PWM1/2 = leg A high/low, PWM3/4 = leg B high/low. PWM5-8 and NTC_P/N not used (open).\n"
            "AN2_N / AN3_N = AGND (single-ended 0-3 V). +24V = +24V_GD from the CTRL eFuse (safety channel B):\n"
            "this board draws <= %.1f A and presents <= %.0f uF to it (design_check)." % (IF.CELL_24V_MAX_A, IF.CELL_24V_MAX_UF))
    cell = IF.pins(IF.CELL, p, rename={"+24V": v24, "AN2_N": "AGND", "AN3_N": "AGND"})
    unused = {p + x for x in ("NTC_P", "NTC_N")} | {"%sPWM%d_%s" % (p, k, s) for k in range(5, 9) for s in "PN"}
    B.part("J_CELL", {k: (None if v in unused else v) for k, v in cell.items()})
    B.flag(v24)
    B.R(IF.ID_OHM["DAB60-B%d" % b], p + "ID", "GND", tol="0.1%", note="board ID DAB60-B%d (interfaces.ID_OHM)" % b)
    B.block("RS-422 receivers with fail-safe bias (PWM1-4, EN)",
            "Each pair: P %s to GND, N %s to 3.3 V, %s across (= termination). AM26LV32E: VID <= -0.2 V -> output LOW.\n"
            "Cable unplugged / pair open: VID <= %.3f V worst case (3.135 V, 1 %%, ri 4 k) -> LOW = gate off.\n"
            "CTRL unpowered: its AM26LV31E outputs are high-Z (IO(OFF) <= 100 uA) -> VID <= %.3f V -> LOW = off.\n"
            "This board's 3.3 V absent: receivers, gates and UCC21710 VCC dead, UCC14241 ENA low -> no gate bias;\n"
            "PWM/EN nets held low by 10 k. With the whole CELL cable out this board has no +24V at all."
            % (gdrv.ohm(BIAS[0]), gdrv.ohm(BIAS[0]), gdrv.ohm(BIAS[1]), VID_OPEN, VID_CTRL_OFF))
    rx = {"2": p + "PWM1_P", "1": p + "PWM1_N", "6": p + "PWM2_P", "7": p + "PWM2_N", "10": p + "PWM3_P",
          "9": p + "PWM3_N", "14": p + "PWM4_P", "15": p + "PWM4_N", "3": p + "PWMR1", "5": p + "PWMR2",
          "11": p + "PWMR3", "13": p + "PWMR4", "4": v33, "12": "GND", "16": v33, "8": "GND"}
    B.part("AM26LV32E", rx)
    B.C("100n", v33, "GND")
    B.part("AM26LV32E", {"2": p + "EN_P", "1": p + "EN_N", "3": p + "EN_RX", "6": None, "7": None, "10": None,
                         "9": None, "14": None, "15": None, "5": None, "11": None, "13": None, "4": v33, "12": "GND",
                         "16": v33, "8": "GND"})
    B.C("100n", v33, "GND")
    for pair in ["PWM%d" % k for k in range(1, 5)] + ["EN"]:
        B.R(gdrv.ohm(BIAS[0]), p + pair + "_P", "GND")
        B.R(gdrv.ohm(BIAS[1]), p + pair + "_P", p + pair + "_N")
        B.R(gdrv.ohm(BIAS[0]), v33, p + pair + "_N")
    B.block("FLT driver, RDY (active high), driver fault / ready lines",
            "FLT_OK high = healthy -> FLT_P high (CTRL receiver wired swapped: high = fault); unpowered = high-Z = FAULT\n"
            "at the CTRL's bias. RDY_L = wired-AND of the 4 UCC21710 RDY (VCC/VDD UVLO, 4.99k). CELL RDY is DRIVEN high by\n"
            "an LVC08 gate (RDY_L & UV_OK, 100R) - the CTRL pulls it down with 100k and tri-states its PWM/EN drivers\n"
            "while RDY is low (INT-15); no 3.3 V here = output low = not ready.")
    B.part("AM26LV31E", {"1": p + "FLT_OK", "7": "GND", "9": "GND", "15": "GND", "4": v33, "12": "GND", "16": v33,
                         "8": "GND", "2": p + "FLT_P", "3": p + "FLT_N", "6": None, "5": None, "10": None, "11": None,
                         "14": None, "13": None})
    B.C("100n", v33, "GND")
    for net in (p + "DRV_FLT_N", p + "RDY_L"):
        B.R("4.99k", net, v33)
        B.C("100p", net, "GND", diel="C0G", tol="5%")
    B.R("100R", p + "RDY_DRV", p + "RDY", note="RDY driven actively (CTRL 100k pull-down)")
    B.block("Trip latch, enables, FLT (no firmware in the path; INT-17, INT-18)",
            "TRIP_N = OC_H & OC_L & DC_H & DC_L & OV_OK & UV_OK -> LVC1G74 PRE: LATCH_OK low until the CTRL takes EN low\n"
            "(CLR). LR_OK = LATCH_OK & RDY_L. EN_DRV = EN_RX & LR_OK -> RST/EN of the 4 drivers (<= 0.8 us filter, clears\n"
            "DESAT latches). RUN = EN_DRV & DRV_FLT_N gates every PWM (fast path ~0.25 us): a trip, a driver UVLO, a\n"
            "+24V dip or a DESAT anywhere turns the whole bridge off. FLT_OK = LR_OK & DRV_FLT_N & TRIP_N: DESAT, driver\n"
            "UVLO, local OC / DC-flux / OV / +24V UV; TRIP_N direct, so a standing trip shows while EN is low (PRE = CLR = L\n"
            "gives Q_N = H). RDY_DRV = RDY_L & UV_OK. Gates: U_a OC/DC/I/IV, U_b TRIP/LR/EN/RUN, U_c FLT/RDY + 1 spare.")
    and4 = lambda nets: {"1": nets[0], "2": nets[1], "3": nets[2], "4": nets[3], "5": nets[4], "6": nets[5],
                         "9": nets[6], "10": nets[7], "8": nets[8], "12": nets[9], "13": nets[10], "11": nets[11],
                         "14": v33, "7": "GND"}
    q = lambda *names: [x if x in ("GND", "OV_OK", None) else p + x for x in names]
    for gates in (q("OCH_OK", "OCL_OK", "OC_OK", "DCH_OK", "DCL_OK", "DC_OK", "OC_OK", "DC_OK", "I_OK", "I_OK", "OV_OK",
                    "IV_OK"),
                  q("IV_OK", "UV_OK", "TRIP_N", "LATCH_OK", "RDY_L", "LR_OK", "EN_RX", "LR_OK", "EN_DRV", "EN_DRV",
                    "DRV_FLT_N", "RUN"),
                  q("LR_OK", "DRV_FLT_N", "H_OK", "H_OK", "TRIP_N", "FLT_OK", "RDY_L", "UV_OK", "RDY_DRV", "GND", "GND",
                    None)):
        B.part("SN74LVC08A", and4(gates))
        B.C("100n", v33, "GND")
    B.part("SN74LVC1G74", {"2": "GND", "1": "GND", "7": p + "TRIP_N", "6": p + "EN_RX", "4": "GND", "8": v33,
                           "5": None, "3": p + "LATCH_OK"})
    B.C("100n", v33, "GND")
    B.part("SN74LVC08A", and4(q("PWMR1", "RUN", "PWM_H1", "PWMR2", "RUN", "PWM_L1", "PWMR3", "RUN", "PWM_H2", "PWMR4",
                                "RUN", "PWM_L2")))
    B.C("100n", v33, "GND")
    B.R("10k", p + "EN_DRV", "GND", note="EN pull-down: unpowered logic = drivers disabled")
    B.TP(p + "EN_DRV")
    B.TP(p + "TRIP_N")
    B.block("+24V under-voltage (TPS3710)",
            "Trips at %.2f-%.2f V falling (%.2f-%.2f V rising, 100k/2.00k 0.1 %%): above the UCC14241 UVLO\n"
            "(17.1-18.9 V falling) and below the 21.6 V system minimum, so a vanishing +24V_GD turns every gate off\n"
            "actively (PWM gating + EN, latched) while the gate bias is still regulated. tpd(HL) 18 us." % (UV_FALL + UV_RISE))
    B.part("TPS3710", {"5": v33, "3": p + "UVS", "2": "GND", "4": "GND", "6": "GND", "1": p + "UV_OK"})
    B.R(gdrv.ohm(UV_DIV[0]), v24, p + "UVS", tol="0.1%")
    B.R(gdrv.ohm(UV_DIV[1]), p + "UVS", "GND", tol="0.1%")
    B.C("1n", p + "UVS", "GND", tol="10%")
    B.R("10k", p + "UV_OK", v33)
    B.C("100n", v33, "GND")
    B.block("Local 5 V (LMR36015) and 3.3 V (TPS7A2033) from the CELL +24V",
            "SNVSB49D table 10-3 (5 V, 400 kHz): 10 uH, 2 x 22 uF, 100k / 24.9k -> 5.02 V. EN tied to VIN.\n"
            "5 V: UCC12050 (HV-side sensor), AMC3330, PELV op amps / comparator / reference; 3.3 V: receivers,\n"
            "logic, ISO7720F side 2, UCC21710 VCC, bias ENA.")
    B.part("LMR36015_5V", {"2": v24, "10": v24, "9": v24, "3": None, "1": "GND", "11": "GND", "12": p + "SW",
                        "4": p + "BOOT", "7": p + "FB5", "8": None, "5": p + "VCC5", "6": "GND"})
    B.C("4.7u", v24, "GND", pkg="1206", volt="50V")
    B.C("220n", v24, "GND", volt="50V")
    B.C("100n", p + "BOOT", p + "SW")
    B.C("1u", p + "VCC5", "GND", volt="16V")
    B.part("WE_L10U", {"1": p + "SW", "2": v5})
    B.R("100k", v5, p + "FB5")
    B.R("24.9k", p + "FB5", "GND")
    for _ in range(2):
        B.C("22u", v5, "GND", pkg="1206", volt="10V")
    B.flag(v5)
    B.part("TPS7A2033", {"1": v5, "3": v5, "2": "GND", "5": v33, "4": None})
    B.C("1u", v5, "GND", volt="16V")
    B.C("2.2u", v33, "GND", volt="16V")
    B.TP(v33)

    # ---------------------------------------------------------------- sensing: HV-side sensor, AN1, OC (HV), DC (PELV)
    h5, hg, hvd = p + "H5V", p + "HGND", "HV%d" % b
    hi, lo = taps_amps()
    B.new_sheet("%02d_b%d_sense" % (n0 + 4, b), "Bridge %d sensing" % b,
                "HOB 130-P %s current, HV%d side -> AN1 (AMC3330),\n"
                "OC +/-%.0f A (ISO7720F), |I_dc| %.0f A on AN1, AN2/AN3 temperatures" % (c_in[1], b, OC_TRIP, DC_TRIP))
    B.block("Insulation: the HOB 130-P is NOT the PELV barrier (basic 1000 V only)",
            "Its secondary lives on the HV%d side, referenced to the bridge DC- (%s, one 0R Kelvin tie at the bank), so\n"
            "primary-secondary sees only functional stress. HV%d -> PELV crosses three reinforced parts: UCC12050 power\n"
            "(VIOWM 1697 VDC), AMC3330 AN1 (1700 VDC), ISO7720F OC trips (2121 VDC). ECO-10: reinforced creepage at\n"
            "these footprints (8 mm packages < 10 mm at PD2 / 1000 V: PD1 coating or slots, as PV-PORT)." % (b, dcn, b))
    iso.add(B.part("UCC12050", {"1": v5, "2": "GND", "3": v5, "4": "GND", "5": None, "6": "GND", "7": "GND",
                                "8": "GND", "9": hg, "10": hg, "11": hg, "12": hg, "13": h5, "14": h5, "15": hg,
                                "16": hg}).ref)
    B.C("10u", v5, "GND", pkg="0805", volt="16V")
    B.C("100n", v5, "GND")
    B.C("10u", h5, hg, pkg="0805", volt="16V")
    B.C("100n", h5, hg)
    B.R("0R", hg, dcn, pkg="0805", note="HV-side sensor ground: single Kelvin tie to the bank DC- terminal")
    B.flag(hg)
    B.block("Transformer %s current: HOB 130-P (DC-1 MHz, +/-250 A, 8 mV/A)" % c_in[1],
            "Primary jumper in the AC path %s -> %s (positive = out of leg A). LEM p12 filter 10R / 4.7n on Uout.\n"
            "OPA4388 D buffers Uout (Rout 30 ohm), C buffers Uref (130-300 ohm)." % (aca, c_in[0]))
    B.part("HOB130P", dict({"1": h5, "2": hg, "3": p + "IOUT", "4": p + "IREF", "5": None},
                           **{str(k): aca for k in range(6, 10)}, **{str(k): c_in[0] for k in range(10, 14)}))
    hv.add(c_in[0])
    B.C("100n", h5, hg)
    B.C("100n", p + "IREF", hg)
    B.R("10R", p + "IOUT", p + "IOUTF")
    B.C("4.7n", p + "IOUTF", hg, diel="C0G", tol="5%")
    r, (v0, cm0, _, _) = AN1_R, an1(2.5, 2.5)
    B.block("AN1 driver -> AMC3330 (+/-1 V in, gain 2, 1.44 V CM out, 0.2 ohm)",
            "OPA4388 A: INP = Vcm + k(Uout - Uref), B: INN = Vcm - k(Uout - Uref): R1 = R3 = %s, R2 = %s, + leg divider\n"
            "%s / %s from Uref_B (Vcm %.3f V, R_th ~ R2). VIN = %.4f (Uout - Uref) -> AN1 = %.3f mV/A differential:\n"
            "0 A = 0 V, +/-%.2f V at +/-250 A, +/-%.2f V at the HOB rails; CTRL G 0.402 -> %.2f-%.2f V at its ADC."
            % (gdrv.ohm(r["r1"]), gdrv.ohm(r["r2"]), gdrv.ohm(r["ru"]), gdrv.ohm(r["rg"]), cm0,
               an1(2.5 + 1.0, 2.5)[0], AN1_K * 1e3, AN1_K * HOB["ipm"], AMC["gain"] * an1(5.0, 2.5)[0],
               1.25 - 0.402 * AMC["gain"] * an1(5.0, 2.5)[0], 1.25 + 0.402 * AMC["gain"] * an1(5.0, 2.5)[0]))
    B.part("OPA4388", {"3": p + "DAP", "2": p + "DAN", "1": p + "INP", "5": p + "DBP", "6": p + "DBN", "7": p + "INN",
                       "10": p + "IREF", "9": p + "UREF_B", "8": p + "UREF_B", "12": p + "IOUTF", "13": p + "UOUT_B",
                       "14": p + "UOUT_B", "4": h5, "11": hg})
    B.C("100n", h5, hg)
    for a_in, c_in_, np_, nn_, out in ((p + "UOUT_B", p + "UREF_B", p + "DAP", p + "DAN", p + "INP"),
                                       (p + "UREF_B", p + "UOUT_B", p + "DBP", p + "DBN", p + "INN")):
        B.R(gdrv.ohm(r["r1"]), a_in, np_, tol="0.1%")
        B.R(gdrv.ohm(r["ru"]), p + "UREF_B", np_, tol="0.1%")
        B.R(gdrv.ohm(r["rg"]), np_, hg, tol="0.1%")
        B.R(gdrv.ohm(r["r3"]), c_in_, nn_, tol="0.1%")
        B.R(gdrv.ohm(r["r2"]), nn_, out, tol="0.1%")
    amc_dom = {}
    iso.add(port._amc(B, "AMC3330", p + "INP", p + "INN", hg, p + "AN1_P", p + "AN1_N", v5, "GND", amc_dom, hvd,
                      p + "AMC"))
    B.block("Over-current window +/-%.0f A, local and fast (HV side -> ISO7720F -> TRIP_N)" % OC_TRIP,
            "REF3030E ladder %s (0.1 %%) on H5V: trips at %+.1f / %+.1f A on Uout/2 (20.0k/20.0k + 10 pF). TLV3502 out\n"
            "HIGH = OK -> ISO7720F (default LOW: a dead HV side reads TRIP) -> OCH_OK / OCL_OK. ~%.2f us to the gate."
            % ("/".join(gdrv.ohm(x) for x in OC_LAD), hi[0], lo[0], t_oc() * 1e6))
    B.part("REF3030E_LAD", {"1": h5, "2": p + "HVREF", "3": hg})
    B.C("100n", h5, hg)
    B.C("1u", p + "HVREF", hg, volt="16V")
    nodes = [p + "HVREF", p + "OCH", p + "OCL", hg]
    for x, a_, c_ in zip(OC_LAD, nodes, nodes[1:]):
        B.R(gdrv.ohm(x), a_, c_, tol="0.1%")
    B.R("20.0k", p + "IOUTF", p + "IXD", tol="0.1%")
    B.R("20.0k", p + "IXD", hg, tol="0.1%")
    B.C("10p", p + "IXD", hg, diel="C0G", tol="5%")
    B.part("TLV3502_WIN", {"1": p + "OCH", "2": p + "IXD", "7": p + "OCH_H", "3": p + "IXD", "4": p + "OCL",
                           "6": p + "OCL_H", "8": h5, "5": hg})
    B.C("100n", h5, hg)
    iso.add(B.part("ISO7720F", {"1": hg, "2": None, "3": h5, "4": p + "OCH_H", "5": p + "OCL_H", "6": None, "7": hg,
                                "8": None, "9": "GND", "10": None, "11": None, "12": p + "OCL_OK", "13": p + "OCH_OK",
                                "14": v33, "15": None, "16": "GND"}).ref)
    B.C("100n", h5, hg)
    B.C("100n", v33, "GND")
    hv |= {h5, hg, p + "IOUT", p + "IOUTF", p + "IREF", p + "UREF_B", p + "UOUT_B", p + "DAP", p + "DAN", p + "DBP",
           p + "DBN", p + "INP", p + "INN", p + "HVREF", p + "OCH", p + "OCL", p + "IXD", p + "OCH_H",
           p + "OCL_H"} | set(amc_dom)
    B.block("DC-flux window +/-%.0f A on AN1 (PELV, tau %.2f ms)" % (DC_TRIP, TAU_DC * 1e3),
            "OPA2388 A: MID + %g x LPF(AN1_P - AN1_N) (100k / 1.60M || 620p C0G); B buffers MID. 5V_B ladder %s\n"
            "(0.1 %%, ratiometric): trips at %+.2f / %+.2f A. TLV3502 out LOW = trip -> TRIP_N. AMC3330 fail-safe\n"
            "(-2.57 V, HV side dead) and an open AN1 line trip it too." % (G_DC, "/".join(gdrv.ohm(x) for x in DC_LAD),
                                                                           hi[1], lo[1]))
    nodes = [v5, p + "DCH", p + "MID", p + "DCL", "GND"]
    for x, a_, c_ in zip(DC_LAD, nodes, nodes[1:]):
        B.R(gdrv.ohm(x), a_, c_, tol="0.1%")
    B.C("100n", p + "MID", "GND")
    B.part("OPA2388", {"3": p + "DCP", "2": p + "DCN", "1": p + "DCX", "5": p + "MID", "6": p + "MID_B",
                       "7": p + "MID_B", "8": v5, "4": "GND"})
    B.C("100n", v5, "GND")
    B.R(gdrv.ohm(R_DC), p + "AN1_P", p + "DCP", tol="0.1%")
    B.R(gdrv.ohm(G_DC * R_DC), p + "DCP", p + "MID_B", tol="0.1%")
    B.C(gdrv.farad(C_DC), p + "DCP", p + "MID_B", diel="C0G", tol="5%")
    B.R(gdrv.ohm(R_DC), p + "AN1_N", p + "DCN", tol="0.1%")
    B.R(gdrv.ohm(G_DC * R_DC), p + "DCN", p + "DCX", tol="0.1%")
    B.C(gdrv.farad(C_DC), p + "DCN", p + "DCX", diel="C0G", tol="5%")
    B.part("TLV3502_WIN", {"1": p + "DCH", "2": p + "DCX", "7": p + "DCH_OK", "3": p + "DCX", "4": p + "DCL",
                           "6": p + "DCL_OK", "8": v5, "5": "GND"})
    B.C("100n", v5, "GND")
    B.block("AN2 module temperature, AN3 %s (PELV)" % AN3[b]["what"].split(" (")[0],
            "AN2: APWM (L1 driver, 400 kHz, duty 88-10 %% for AIN 0.6-4.5 V) -> 10.0k / 100k / 1 uF (tau 10 ms) ->\n"
            "follower: 0.9 x 3.3 V x duty, 0.2-2.8 V. AN3: REF3030E 3.0 V -> 100R -> NTC -> %s -> GND, follower, 0-%.2f V.\n"
            "Outputs 49.9R (CTRL: <= 100 R source), single-ended on AGND." % (gdrv.ohm(AN3[b]["rb"]),
                                                                            3.0 * AN3[b]["rb"] / (AN3[b]["rb"] + 100)))
    B.part("REF3030E_LAD", {"1": v5, "2": p + "VREF", "3": "GND"})
    B.C("100n", v5, "GND")
    B.C("1u", p + "VREF", "GND", volt="16V")
    B.R("10.0k", p + "APWM", p + "AN2F")
    B.R("100k", p + "AN2F", "GND")
    B.C("1u", p + "AN2F", "GND", volt="16V")
    B.part("OPA2388", {"3": p + "AN2F", "2": p + "AN2B", "1": p + "AN2B", "5": p + "AN3S", "6": p + "AN3B",
                       "7": p + "AN3B", "8": v5, "4": "GND"})
    B.C("100n", v5, "GND")
    B.R("49.9R", p + "AN2B", p + "AN2_P")
    B.R("49.9R", p + "AN3B", p + "AN3_P")
    B.R("100R", p + "VREF", p + "AN3H")
    B.C("100n", p + "AN3H", "GND")
    B.R(gdrv.ohm(AN3[b]["rb"]), p + "AN3S", "GND", tol="0.1%")
    B.C("100n", p + "AN3S", "GND")
    if b == 2:
        B.part("B57703M", {"1": p + "AN3H", "2": p + "AN3S"})
    return dict(iso=iso, xing=xing, gd=gd, hv=hv, mod=mod.ref)


def taps_amps():
    """(high, low) thresholds in amps, [OC, DC], from the drawn ladders: OC = Uout/2 against the HV-side REF3030E
    ladder (Uref 2.5 V nominal), DC = MID +/- the 5 V ladder half window over G_DC x AN1_K."""
    t = sum(OC_LAD)
    to_a = lambda u: (u / K_OC - 2.5) / HOB["sens"]
    w = 5.0 * DC_LAD[1] / sum(DC_LAD) / (G_DC * AN1_K)
    return (to_a(3.0 * (OC_LAD[1] + OC_LAD[2]) / t), w), (to_a(3.0 * OC_LAD[2] / t), -w)


def t_oc():
    """Current step -> gate off: HOB tD90 200 ns, Uout RC (Rout 30 + 10 ohm) x 4.7 nF, divider 10 k x (10 + 2) pF,
    TLV3502 12 ns, ISO7720F 18.5 ns, 8 LVC08 gates (4.1 ns) + LVC1G74 5.9 ns, 10 ns routing, UCC21710 IN deglitch
    60 ns + tPD 130 ns."""
    return (HOB["td90"] + (HOB["rout"] + 10) * 4.7e-9 + 10e3 * 12e-12 + 12e-9 + 18.5e-9 + 8 * 4.1e-9 + 5.9e-9 + 10e-9
            + 60e-9 + 130e-9)


# ======================================================================== magnetics, connectors, protection
def magnetics(B, n):
    B.new_sheet("%02d_magnetics" % n, "Transformer, inductor, DC-block link",
                "Transformer %d:%d and series inductor (CUSTOM, cold plate),\n"
                "bolted link = DC-blocking capacitor position (bank not fitted)" % (N1, N2))
    t, si = "transformer/", "series_inductor/"
    B.block("Primary AC path (HV1): leg A - HOB - link - L - P1 / P2 - leg B",
            "Link FITTED. Provision: unbolt it and fit the blocking bank %s (DNP below, %.0f uF).\n"
            "Firmware flux-balance loop on AN1 + gapped core (L_m %.0f uH) + hardware |I_dc| trip is the baseline.\n"
            "Series L (CUSTOM): %.2f uH nominal, trimmed %.2f-%.2f uH so that leakage + L = %.2f uH %s;\n"
            "%.0f A rms, %.0f A peak, no saturation to %.0f A, <= %.0f W."
            % (spec("flux_balance/blocking_cap_alternative/bank"), BLOCK["c"] * 1e6, spec(t + "L_m_H") * 1e6,
               spec(si + "L_external_nominal_H") * 1e6, spec(si + "L_ext_trim_range_H")[0] * 1e6,
               spec(si + "L_ext_trim_range_H")[1] * 1e6, spec(si + "L_total_H") * 1e6, spec(si + "L_total_tol"),
               spec(si + "I_rms_max_A"), spec(si + "I_pk_max_A"), spec(si + "I_sat_min_A"), spec(si + "loss_max_W")))
    B.part("LINK", {"1": "XP_A", "2": "XP_B"}, value="DC-block link (CUSTOM)")
    for _ in range(BLOCK["n"]):
        B.part(C4AQ[BLOCK["mpn"]]["key"], {"1": "XP_A", "2": "XP_A", "3": "XP_B", "4": "XP_B"}, dnp=True)
    B.part("LSER", {"1": "XP_B", "2": "XP_C"}, value="%.2f uH series L (CUSTOM)" % (spec(si + "L_external_nominal_H") * 1e6))
    iso_ = spec(t + "isolation")
    B.block("Transformer %d:%d (HV1 / HV2 barrier, CUSTOM)" % (N1, N2),
            "P1/S1 = dotted ends. Secondary: leg A of bridge 2 - HOB - S1 / S2 - leg B of bridge 2. TH1/TH2: embedded\n"
            "NTC -> bridge 1 AN3. L_sigma %.2f uH %s, L_m %.0f uH %s, %.0f / %.0f A rms, %.0f / %.0f A peak, <= %.0f W,\n"
            "hot spot <= %.0f C. Insulation %s on a %.0f V DC working basis; hipot/PD from ECO-10, PD <= %.0f pC.\n"
            "Full text in the part description and report.md sec. 9."
            % (spec(t + "L_sigma_target_H") * 1e6, spec(t + "L_sigma_tol").split(" (")[0], spec(t + "L_m_H") * 1e6,
               spec(t + "L_m_tol").split(" (")[0], spec(t + "I1_rms_max_A"), spec(t + "I2_rms_max_A"),
               spec(t + "I1_pk_max_A"), spec(t + "I2_pk_max_A"), spec(t + "loss_max_W"), spec(t + "hot_spot_max_C"),
               iso_["class"], iso_["working_voltage_basis_V_dc"], iso_["PD_max_pC"]))
    xf = B.part("XFMR", {"P1": "XP_C", "P2": "B1_ACB", "S1": "XS_A", "S2": "B2_ACB", "TH1": "B1_AN3H", "TH2": "B1_AN3S"},
                value="Transformer %d:%d (CUSTOM)" % (N1, N2))
    return xf.ref, {"XP_A", "XP_B", "XP_C"}


AUX_TAP = {"A_X+": ("AUX-HV PA+ tap (port 1 only)", False), "P1_BUS-": ("P1_BUS- stud = AUX-HV PA-", False),
           "B_X+": ("NOT FITTED - no AUX-HV tap on port 2 (D-021)", True)}


def aux_hv_taps(B):
    """D-021 / INT-11: AUX-HV is fed from port 1 only. port.port() puts a terminal-side stud on <tag>_X+ of every port
    (no option to leave it out): port 1's becomes the AUX-HV PA+ tap, PA- = port 1's P1_BUS- stud; port 2's is DNP."""
    studs = {p.pins["1"]: p for p in B.D.parts.values() if p.lib_id.endswith(":STUD")}
    for net, (value, dnp) in AUX_TAP.items():
        studs[net].value, studs[net].dnp = value, dnp
        B.bom[studs[net].ref].update(value=value, dnp=dnp)
    return {net: studs[net].ref for net in AUX_TAP}


def io_sheet(B, n, readback):
    """PORT connector, SYS-IO-AUX harness, sensor supply, port OV comparators, coolant loop.
    readback = [(rb1, rb2)] of port A and port B (port.port() nets)."""
    S = port.S
    lv, _, r_eff, v_unp = id_levels()
    B.new_sheet("%02d_io" % n, "Connectors, port OV trip, coolant loop",
                "PORT 26-way + ID readback, 14-way SYS-IO-AUX, +3V3S, AUX-HV tap,\n"
                "port OV comparators -> OV_OK, coolant loop -> SYS-IO-AUX J801")
    B.block("PORT connector (CTRL-C2000), ID with hold readback",
            "interfaces.PORT: VA/VAX/IA = port 1 (tag A), VB/VBX/IB = port 2 (tag B). AUX1/AUX2 not used (no IMD).\n"
            "ID: %s || readback = %.3f k (code %s). RB1/RB2 of port A via %s, of port B via %s: nine levels\n"
            "%.3f-%.3f V within +/-165 mV of the code (PVCELL-25 window starts at 0.667 V); unpowered %.3f V."
            % (gdrv.ohm(RB["r_id"]), r_eff / 1e3, IF.ID_OHM["DAB60-PORT"], gdrv.ohm(RB["r_a"]), gdrv.ohm(RB["r_b"]),
               lv[0][0], lv[-1][1], v_unp))
    pins = IF.pins(IF.PORT)
    B.part("J_PORT", {k: None if v.startswith("AUX") else v for k, v in pins.items()})
    B.R(gdrv.ohm(RB["r_id"]), "ID", "GND", tol="0.1%", note="board ID DAB60-PORT with the readback network")
    for (rb1, rb2), r in zip(readback, (RB["r_a"], RB["r_b"])):
        B.R(gdrv.ohm(r), rb1, "ID", tol="0.1%", note="hold readback")
        B.R(gdrv.ohm(r), rb2, "ID", tol="0.1%", note="hold readback")
    for net in ("+24V", "GND", "AGND"):
        B.flag(net)
    B.block("SYS-IO-AUX harness (coils, contactor feedback, proof test)",
            "port.CTRL_PINS, 14-way, the PV-PORT harness: DO_SPARE = hold-logic proof test of both ports.\n"
            "IMD_SW_P/N not used on DAB60 (no insulation measurement): open.")
    B.part("J_CTRL", {str(i): (None if x.startswith("IMD") else x) for i, x in enumerate(port.CTRL_PINS, 1)})
    tap = aux_hv_taps(B)
    B.block("AUX-HV feed: port 1 ONLY (D-021, INT-11)",
            "AUX-HV PA+ = %s on A_X+ (terminal side of port 1: fuse + EMI filter, ahead of the contactor),\n"
            "PA- = %s on P1_BUS-. AUX-HV PB+/PB- stay open. Port 2 stud %s on B_X+ is DNP: no port-2 tap exists, so\n"
            "HV1 and HV2 never meet in the AUX-HV diode OR. Consequence: no black start from port 2 without cabinet 24 V."
            % (tap["A_X+"], tap["P1_BUS-"], tap["B_X+"]))
    ss = S["sensor_supply"]
    B.block("Sensor supply +3V3S (port_spec.json sensor_supply)",
            "%s 400 kHz, 10 uH, %s / %s -> %.3f V for 4 x AMC3330 + 2 x AMC3302 + port hold logic + OV comparator\n"
            "(SNVSB49D Fig 10-1: 4.7 uF + 220 nF in, 100 nF boot, 1 uF VCC, 2 x 22 uF out). AGND joins GND only here."
            % (ss["regulator"], gdrv.ohm(ss["R_fbt_ohm"]), gdrv.ohm(ss["R_fbb_ohm"]), ss["Vout"]))
    B.part("LMR36015", {"2": "+24V", "10": "+24V", "9": "+24V", "3": None, "1": "GND", "11": "GND", "12": "SS_SW",
                        "4": "SS_BOOT", "7": "SS_FB", "8": None, "5": "SS_VCC", "6": "GND"})
    B.C("4.7u", "+24V", "GND", pkg="1206", volt="50V")
    B.C("220n", "+24V", "GND", volt="50V")
    B.C("100n", "SS_BOOT", "SS_SW")
    B.C("1u", "SS_VCC", "GND", volt="16V")
    B.part("WE_L10U", {"1": "SS_SW", "2": "+3V3S"})
    B.R(gdrv.ohm(ss["R_fbt_ohm"]), "+3V3S", "SS_FB")
    B.R(gdrv.ohm(ss["R_fbb_ohm"]), "SS_FB", "GND")
    for _ in range(2):
        B.C("22u", "+3V3S", "GND", pkg="1206", volt="10V")
    B.flag("+3V3S")
    B.R("0R", "AGND", "GND", note="single AGND-GND tie")
    B.part("PE_BOND", {"1": "PE"}, value="PE bond M4")
    B.flag("PE")

    b1, b2 = ov_band(*OV_R[1]), ov_band(*OV_R[2])
    B.block("Port over-voltage comparators (both bridges trip, latched there)",
            "Matched dividers on the AMC3330 outputs: OK while VA_P - VA_N < 2.048 V x R1/R2 (common mode rejected);\n"
            "OPA2388 as comparator (VOS 7.5 uV; inputs differ by mV at the trip point). Port 1 trips at %.0f V (%.0f-%.0f V),\n"
            "port 2 at %.0f V (%.0f-%.0f V): worst case <= the spec %.0f / %.0f V, above the firmware %.0f / %.0f V.\n"
            "Response %.1f us (divider 4.99k || 4.7 nF + AMC3330 %.1f us, OPA2388 overload recovery 10 us, logic):\n"
            "load-rejection peak %.0f / %.0f V. OV_OK low = trip; 10k pull-down: no +3V3S (PORT cable out) trips both."
            % (b1[1], b1[0], b1[2], b2[1], b2[0], b2[2], OV_HW[1], OV_HW[2], OV_FW[1], OV_FW[2], ov_response() * 1e6,
               S["divider"]["lag_s"] * 1e6, ov_peak(1, ov_response(), b1[2]), ov_peak(2, ov_response(), b2[2])))
    B.part("REF3020E", {"1": "+3V3S", "2": "OV_REF", "3": "GND"})
    B.C("100n", "+3V3S", "GND")
    B.C("1u", "OV_REF", "GND", volt="16V")
    for k, tag in ((1, "A"), (2, "B")):
        r1, r2 = OV_R[k]
        x, y = "OV%d_X" % k, "OV%d_Y" % k
        B.R(gdrv.ohm(r1), "V%s_P" % tag, x, tol="0.1%")
        B.R(gdrv.ohm(r2), x, "GND", tol="0.1%")
        B.R(gdrv.ohm(r1), "V%s_N" % tag, y, tol="0.1%")
        B.R(gdrv.ohm(r2), y, "OV_REF", tol="0.1%")
        B.C("100p", x, "GND", diel="C0G", tol="5%")
        B.C("100p", y, "GND", diel="C0G", tol="5%")
    B.part("OPA2388", {"3": "OV1_Y", "2": "OV1_X", "1": "OV1_OK", "5": "OV2_Y", "6": "OV2_X", "7": "OV2_OK",
                       "8": "+3V3S", "4": "GND"})
    B.C("100n", "+3V3S", "GND")
    B.part("SN74LVC1G08", {"1": "OV1_OK", "2": "OV2_OK", "3": "GND", "5": "+3V3S", "4": "OV_OK"})
    B.C("100n", "+3V3S", "GND")
    B.R("10k", "OV_OK", "GND", note="OV monitor unpowered = trip")
    B.TP("OV_OK")

    B.block("Coolant loop -> SYS-IO-AUX J801 spare trip input (ISO1211, 1:1 harness)",
            "Flow switch (closed while flow > %.0f l/min) in series with the cold-plate thermostat (opens at 77 C):\n"
            "loop open = channel-A hardware trip on SYS-IO-AUX (latched there, no firmware). SYS wets the loop from 24V_OR\n"
            "via 2.2k into the ISO1211 limiter: %.2f-%.2f mA, <= %.0f V open. Contacts: reed (VK3, 1 A / 48 V) and gold\n"
            "WE-1 (3106U, 500 mA / 50 V, low-level rated). Pins 3/4 (SYS GND) unused here." % ((FLOW_TRIP,) + LOOP_I +
                                                                                              (LOOP_V,)))
    B.part("J_MC4", {"1": "TRIP_SRC", "2": "TRIP_IN", "3": None, "4": None})
    B.part("VK3", {"1": "TRIP_SRC", "2": "COOL_MID"}, value="flow switch %.0f l/min (RFQ)" % FLOW_TRIP)
    B.part("TSTAT", {"1": "COOL_MID", "2": "TRIP_IN"}, value="3106U 77 C (RFQ)")


# ======================================================================== checks
def ov_response():
    """port_spec divider lag (4.99 k || 4.7 nF +5 % + AMC3330 delay), OPA2388 overload recovery tOR 10 us typ (OPA4388
    SBOS777 p6), LVC1G08 4 ns, bridge logic + driver 0.25 us."""
    return port.S["divider"]["lag_s"] + 10e-6 + 4e-9 + 0.25e-6


def ov_peak(k, t_resp, v_trip):
    """Bus peak after a load rejection, as sim/dab_design.py ov_excursion(): full port current into the bank for t_resp,
    then the series-L energy 0.5 L I_pk^2 dumped into it when the gates go off."""
    o, c = spec("protection/local_ov_board/port%d" % k), spec("capacitor_banks/port%d/C_total_F" % k)
    v = v_trip + o["slew_V_per_us"] * 1e6 * t_resp
    return v + 0.5 * spec("series_inductor/L_total_H") * spec("series_inductor/I_pk_max_A") ** 2 / (c * v)


def cap_value(v):
    m = re.match(r"([\d.]+)([pnu])", v)
    return float(m[1]) * {"p": 1e-12, "n": 1e-9, "u": 1e-6}[m[2]]


def bias_power():
    """Isolated bias load per channel (UCC14241-Q1), the same terms as gdrv.design_check() for the module at F_SW."""
    gv = gdrv.GATE_V[GATE_V]
    vt, v3, v2 = gdrv.rails(GATE_V)
    qg = gdrv.GM4["qg"]
    p_rlim = gdrv.rlim_check(GATE_V, v2[1], v3[1], qg * F_SW, 1)[7]
    e_sw = qg * vt[1] * F_SW
    p = (e_sw + gdrv.UCC21710["ivddq"] * vt[1] + v2[1] * (v2[1] - 3.0) / gv["desat"]["r1"] + v2[1] ** 2 / 10e3
         + vt[1] ** 2 / sum(gv["fbvdd"]) + v3[1] ** 2 / sum(gv["fbvee"]) + p_rlim + v2[1] ** 2 / gv["ntc_div"][0])
    return e_sw, p, vt, v3, v2


def logic_eval(B, b, ins, latch):
    """Levels of every gate output of bridge b's drawn SN74LVC08A gates + LVC1G74 (Q_N = LATCH_OK) for the input levels
    ins (net -> 0/1) and the previous latch state; the netlist itself is evaluated, so a miswired gate shows up here."""
    p, gates = "B%d_" % b, []
    for prt in B.D.parts.values():
        if prt.lib_id.endswith(":SN74LVC08A") and prt.pins["14"] == "3V3_B%d" % b:
            gates += [(prt.pins[a], prt.pins[c], prt.pins[y]) for a, c, y in
                      (("1", "2", "3"), ("4", "5", "6"), ("9", "10", "8"), ("12", "13", "11")) if prt.pins[y]]
    v = dict(ins, GND=0, **{p + "LATCH_OK": latch})
    for _ in range(len(gates) + 2):
        for a, c, y in gates:
            v[y] = v[a] & v[c] if a in v and c in v else v.get(y, 1)
        v[p + "LATCH_OK"] = 1 if not v[p + "EN_RX"] else 0 if not v.get(p + "TRIP_N", 1) else v[p + "LATCH_OK"]
    assert all(a in v and c in v for a, c, _ in gates), "undriven gate input"
    return v


def logic_check(B, b):
    """Contract cases (interfaces.CELL FLT / RDY, INT-15/17/18) on the drawn logic. Returns the fault list."""
    p = "B%d_" % b
    ok = {p + x: 1 for x in ("OCH_OK", "OCL_OK", "DCH_OK", "DCL_OK", "UV_OK", "RDY_L", "DRV_FLT_N", "EN_RX", "PWMR1",
                             "PWMR2", "PWMR3", "PWMR4")}
    ok["OV_OK"] = 1
    out = lambda v, *k: tuple(v[p + x] for x in k)
    v = logic_eval(B, b, ok, 1)
    assert out(v, "FLT_OK", "EN_DRV", "RUN", "RDY_DRV", "PWM_H1", "PWM_L2") == (1,) * 6, "healthy bridge not running"
    faults = [k for k in ok if not k.endswith(("EN_RX", "PWMR1", "PWMR2", "PWMR3", "PWMR4"))]
    for f in faults:
        for en in (1, 0):
            v = logic_eval(B, b, dict(ok, **{f: 0, p + "EN_RX": en}), 1)
            assert out(v, "FLT_OK", "RUN", "PWM_H1", "PWM_L1", "PWM_H2", "PWM_L2") == (0,) * 6, "%s, EN %d" % (f, en)
            assert v[p + "RDY_DRV"] == (f not in (p + "RDY_L", p + "UV_OK")), "RDY follows only RDY_L & UV_OK"
    trip = dict(ok, **{p + "OCH_OK": 0})
    v = logic_eval(B, b, ok, logic_eval(B, b, trip, 1)[p + "LATCH_OK"])        # trip gone, EN still high: latched
    assert out(v, "FLT_OK", "EN_DRV", "RUN", "RDY_DRV") == (0, 0, 0, 1), "trip not latched"
    v = logic_eval(B, b, ok, logic_eval(B, b, dict(ok, **{p + "EN_RX": 0}), 0)[p + "LATCH_OK"])   # EN low clears
    assert out(v, "FLT_OK", "RUN") == (1, 1), "EN low does not clear the latch"
    return [f[len(p):] if f.startswith(p) else f for f in faults]


def design_check(B):
    """Asserts the drawn values against dab_spec.json and the datasheets; returns ({key: line}, cover lines)."""
    out, notes = {}, []
    say = lambda k, s, *a: out.__setitem__(k, s % a)
    bom = {r: m for r, m in B.bom.items() if not r.startswith("#")}
    parts = B.D.parts

    def count(mpn, nets=None):
        return sum(1 for r, m in bom.items() if m["mpn"] == mpn and not m["dnp"] and
                   (nets is None or set(parts[r].pins.values()) <= nets))

    # ---- 1. drawn == spec
    for b in (1, 2):
        cap = spec("capacitor_banks/port%d" % b)
        assert spec("modules/bridge%d_port%d/mpn" % (b, b)) == PARTS["CBB011M12GM4T"]["mpn"]
        n = count(cap["mpn"], {"P%d_BUS+" % b, "P%d_BUS-" % b})
        assert n == cap["qty"], "bridge %d DC link: %d x %s drawn, spec %d" % (b, n, cap["mpn"], cap["qty"])
    snb_r = [m for m in bom.values() if m["desc"].startswith("RFQ snubber resistor")]
    snb_c = [m for m in bom.values() if m["desc"].startswith("RFQ snubber MLCC")]
    assert len(snb_r) == len(snb_c) == 2 * SNB["n"] and all(m["value"].startswith("%gR" % SNB["r"]) for m in snb_r)
    ron = [m["value"] for m in bom.values() if "Ron," in m["desc"]]
    roff = [m["value"] for m in bom.values() if "Roff," in m["desc"]]
    assert len(ron) == len(roff) == 8 and set(ron) == {gdrv.ohm(RG[0])} and set(roff) == {gdrv.ohm(RG[1])}, \
        "gate resistors drawn %s / %s, spec %g / %g ohm" % (sorted(set(ron)), sorted(set(roff)), *RG)
    blk = [r for r, m in bom.items() if m["mpn"] == BLOCK["mpn"] and m["dnp"]]
    assert len(blk) == BLOCK["n"] and abs(BLOCK["n"] * C4AQ[BLOCK["mpn"]]["c"] - BLOCK["c"]) < 1e-9
    assert "%d:%d" % (N1, N2) in PARTS["XFMR"]["desc"]
    say("spec", "Drawn = dab_spec.json: 2 x CBB011M12GM4T; DC link 3 x C4AQUEW5450A3BJ (port 1) / 3 x C4AQQEW5650A3BJ "
        "(port 2); %d x (%g ohm %g W + %g nF %s %g kV) per bridge; Ron/Roff %g/%g ohm (8 each); VGS +%d/-%d V; "
        "transformer %d:%d, L_sigma %.2f uH, L_m %.0f uH, series L %.2f uH (CUSTOM text built from the spec); "
        "blocking bank %d x %s DNP; f_sw %.0f kHz", SNB["n"], SNB["r"], SNB["p"], SNB["c"] * 1e9, SNB["diel"],
        SNB["v"] / 1e3, RG[0], RG[1], GATE_V[0], GATE_V[1], N1, N2, spec("transformer/L_sigma_target_H") * 1e6,
        spec("transformer/L_m_H") * 1e6, spec("series_inductor/L_external_nominal_H") * 1e6, BLOCK["n"], BLOCK["mpn"],
        F_SW / 1e3)
    assert spec("gate_drive/driver").startswith("TI UCC21710") and gdrv.PARTS["UCC21710"]["mpn"].startswith("UCC21710")
    assert spec("gate_drive/bias").startswith("TI UCC14241-Q1") and gdrv.PARTS["UCC14241"]["mpn"].startswith("UCC14241")
    hw, PS = spec("parallel_branch_interface/per_branch_hardware"), port.S
    assert all(spec("precharge/port%d/R_pre_ohm" % k) == PS["precharge"]["R_ohm"] for k in (1, 2))
    assert PS["variants"]["135"]["fuse_mpn"] in hw and PS["contactor"]["type"] in hw and "port_spec.json" in spec(
        "precharge/authority")
    say("spec2", "   Port blocks = port_spec.json (dab_spec defers to it): precharge %.0f ohm %s (dab_spec %.0f ohm), fuses %s "
        "%.0f A, contactors %s, all drawn by port.port() at the 135 A rating; gate bias UCC14241-Q1 (dab_spec text)",
        PS["precharge"]["R_ohm"], PS["precharge"]["R_mpn"], spec("precharge/port1/R_pre_ohm"),
        PS["variants"]["135"]["fuse_mpn"], PS["variants"]["135"]["fuse_In_A"], PS["contactor"]["type"])

    # ---- 2. DC-link capacitors and snubbers vs the spec stresses (C4AQ p14 + derating table)
    for b in (1, 2):
        cap, r = spec("capacitor_banks/port%d" % b), C4AQ[spec("capacitor_banks/port%d/mpn" % b)]
        t = cap["hot_spot_est_C"]
        v_ok = r["vndc"] if t <= 70 else r["vndc"] + (r["v85"] - r["vndc"]) * (t - 70) / 15 if t <= 85 else \
            r["v85"] + (r["v105"] - r["v85"]) * (t - 85) / 20
        v_max = spec("power_stage/port%d/V_operating" % b)[1]
        ov = OV_HW[b]
        assert abs(cap["qty"] * r["c"] - cap["C_total_F"]) < 1e-9 and r["vndc"] == cap["V_rating_V"]
        assert v_max <= v_ok and ov <= v_ok, "port %d DC link: %g V / OV %g V vs %g V allowed at %.0f C" % (b, v_max, ov, v_ok, t)
        assert cap["I_ripple_rms_per_cap_A"] <= r["irms"] and r["irms"] == cap["I_rms_rating_per_cap_A"]
        assert cap["ripple_pp_V"] <= 0.2 * r["vndc"]
        say("cap%d" % b, "Port %d DC link %d x %s: %.0f V max (OV trip %.0f V) vs %.0f V allowed at the %.0f C hot spot "
            "(C4AQ p14 derating); ripple %.1f A rms/cap vs %.0f A (%.0f %%); %.2f V pp vs 0.2 x VNDC", b, cap["qty"],
            cap["mpn"], v_max, ov, v_ok, t, cap["I_ripple_rms_per_cap_A"], r["irms"],
            100 * cap["I_ripple_rms_per_cap_A"] / r["irms"], cap["ripple_pp_V"])
    for b in (1, 2):
        cl, f = spec("dc_link_reverse_clamp/port%d" % b), spec("dc_link_reverse_clamp/port%d/fault" % b)
        pn = lambda r, k: parts[r].pins.get(k)
        ref = [r for r, m in bom.items() if m["mpn"] == cl["mpn"] and pn(r, "1") == "P%d_BUS-" % b and
               pn(r, "2") == "P%d_BUS+" % b and not pn(r, "3")]
        assert len(ref) == cl["qty"] == 1 and cl["V_RRM_V"] == CLAMP["vrrm"], "bridge %d clamp: cathode (2) to DC+" % b
        assert f["I2t_clamp_A2s"] * 1.25 <= CLAMP["i2t"] and f["I_clamp_pk_A"] <= CLAMP["ifsm"]
        say("clamp%d" % b, "Port %d reverse clamp %s %s: terminal 2 (cathode) = P%d_BUS+, 1 (anode) = P%d_BUS-, 3 open; VRRM "
            "%.0f V vs %.0f V spec peak (x %.2f); port short %.1f kA peak / %.1f kA2s vs IFSM %.0f kA / I2t %.1f MA2s at Tvj "
            "max", b, cl["mpn"], ref[0], b, b, CLAMP["vrrm"], cl["V_bus_peak_V"], CLAMP["vrrm"] / cl["V_bus_peak_V"],
            f["I_clamp_pk_A"] / 1e3, f["I2t_clamp_A2s"] / 1e3, CLAMP["ifsm"] / 1e3, CLAMP["i2t"] / 1e6)
    v_pk, v1 = spec("worst_case_stresses/module_V_peak_V"), spec("power_stage/port1/V_operating")[1]
    assert SNB["v"] >= 1.5 * v_pk and HFC["v"] >= 1.5e3 and HFC["v"] >= 1.25 * v_pk, "terminal capacitor voltage"
    p_res = spec("dc_link_snubber_hf/P_per_resistor_worst_W")
    assert p_res <= 0.5 * SNB["p"], "snubber resistor above 50 % of its rating"
    for b in (1, 2):
        nets = {"P%d_BUS+" % b, "P%d_BUS-" % b} | {"B%d_SNB%d" % (b, k + 1) for k in range(SNB["n"])}
        rfq =[sum(1 for r, m in bom.items() if m["desc"] == PARTS[k]["desc"] and set(parts[r].pins.values()) <= nets)
               for k in ("C_SNB", "R_SNB", "C_HF")]
        assert rfq == [SNB["n"], SNB["n"], HFC["n"]], "bridge %d terminal network drawn %s" % (b, rfq)
    say("snb", "Terminal network per bridge = dab_spec: %d x (%g ohm %g W %s + %g nF %s %g kV %s) + %d x %g nF %s >= %g kV; "
        "capacitors %.0f V vs module V_DS peak %.0f V (x %.2f); resistor %.2f W worst (dab_spec ngspice study, %.1f W without "
        "the HF caps) vs %g W (%.0f %%)", SNB["n"], SNB["r"], SNB["p"], SNB["r_pkg"], SNB["c"] * 1e9, SNB["diel"],
        SNB["v"] / 1e3, SNB["c_pkg"], HFC["n"], HFC["c"] * 1e9, HFC["diel"], HFC["v"] / 1e3, min(SNB["v"], HFC["v"]),
        v_pk, min(SNB["v"], HFC["v"]) / v_pk, p_res, spec("dc_link_snubber_hf/P_per_resistor_without_hf_caps_W"),
        SNB["p"], 100 * p_res / SNB["p"])

    # ---- 3. gate drive: gdrv's own block checks (its presets), then this board's values with gdrv's helpers
    with contextlib.redirect_stdout(io.StringIO()):
        g_lines, _ = gdrv.design_check()
    out["gdrv"] = "gdrv.design_check() passed (block presets): " + " | ".join(
        v.strip() for k, v in g_lines.items() if k.startswith(("rails15", "desat" + gdrv.GM4["name"], "sc" + gdrv.GM4["name"])))
    e_sw, p_bias, vt, v3, v2 = bias_power()
    U = gdrv.UCC21710
    assert abs(sum(v2) / 2 - GATE_V[0]) <= 0.05 * GATE_V[0] and abs(sum(v3) / 2 - GATE_V[1]) <= 0.05 * GATE_V[1]
    r_on_t, r_off_t = U["roh_eff"] + RG[0] + gdrv.GM4["rg"], U["rol"] + RG[1] + gdrv.GM4["rg"]
    i_src, i_snk = vt[1] / r_on_t, vt[1] / r_off_t
    p_drv = U["ivddq"] * vt[1] + U["ivccq"] * 3.465 + 0.5 * e_sw * (U["roh_eff"] / r_on_t + U["rol"] / r_off_t)
    tj = 125.0 + U["psi_jb"] * p_drv
    p_ron = 0.5 * e_sw * RG[0] / r_on_t
    assert tj <= U["tj_max"] and p_ron <= 0.5
    assert gdrv.UCC14241["p_85c"] / p_bias >= 1.2 and gdrv.UCC14241["p_105c"] / p_bias >= 1.25
    assert abs(e_sw - spec("gate_drive/P_gate_per_switch_W")) <= 0.05 * spec("gate_drive/P_gate_per_switch_W")
    assert abs(p_bias - spec("gate_drive/P_bias_per_switch_W")) <= 0.01, "bias load %.3f W vs dab_spec" % p_bias
    say("gate", "Gate rails (gdrv.rails): VGS on %.2f-%.2f V / off -%.2f..-%.2f V (spec +%g/%g V). CBB011M12GM4T at %.0f kHz: "
        "Qg %.0f nC x %.2f V = %.3f W gate (spec %.3f W), bias load %.2f W vs UCC14241 2.0 W @85 C (x %.2f) / 1.5 W @105 C "
        "(x %.2f); driver %.2f W, TJ %.0f C at a 125 C board; Ron %.2f W", v2[0], v2[1], v3[0], v3[1],
        spec("gate_drive/VGS_on_V"), spec("gate_drive/VGS_off_V"), F_SW / 1e3, gdrv.GM4["qg"] * 1e9, vt[1], e_sw,
        spec("gate_drive/P_gate_per_switch_W"), p_bias, 2.0 / p_bias, 1.5 / p_bias, p_drv, tj, p_ron)
    i_snk_spec = vt[1] / (U["rol"] + RG[1] * 0.99 + gdrv.GM4["rg"])
    assert i_snk_spec <= U["i_pk"] and abs(i_snk_spec - spec("gate_drive/I_sink_peak_max_A")) <= 0.01
    assert abs(i_src - spec("gate_drive/I_source_peak_max_A")) <= 0.05 * i_src
    say("ipk", "   peak gate current source %.2f A / sink %.2f A at VDD-VEE %.2f V max, Roff -1 %% (dab_spec %.2f / %.2f A) "
        "vs UCC21710 %.0f A (ROH_EFF 0.7 / ROL 0.3 typ + RG(int) 1.4 ohm)", i_src, i_snk_spec, vt[1],
        spec("gate_drive/I_source_peak_max_A"), spec("gate_drive/I_sink_peak_max_A"), U["i_pk"])
    mm, m_g = miller(RG[1]), miller(gdrv.GM4["r_gate"][1])
    g_m = re.search(r"die \(RG\(int\) [\d.]+ ohm\) ([-\d.]+) / ([-\d.]+) V", g_lines["mgm4" + gdrv.GM4["name"]])
    assert g_m and all(abs(float(g_m[k + 1]) - m_g[k][2]) < 0.006 for k in (0, 1)), "miller() != gdrv mgm4 model"
    assert mm[0][2] <= gdrv.GM4["vth175"] - 0.5 and abs(mm[0][0] - gdrv.DAB_DVDT) < 0.1
    say("miller", "   Miller hold (gdrv mgm4 model at Roff %g ohm): die %+.2f V at %.1f V/ns (dab_spec) / %+.2f V at %.0f V/ns "
        "vs VGS(th) %.2f V at 175 C -> fine at the DAB rate (margin %.2f V), marginal at 80 V/ns (%.2f V); gdrv's own "
        "case Roff %g ohm: %+.2f / %+.2f V (reproduced). Note on every gate sheet", RG[1], mm[0][2], mm[0][0], mm[1][2],
        mm[1][0], gdrv.GM4["vth175"], gdrv.GM4["vth175"] - mm[0][2], gdrv.GM4["vth175"] - mm[1][2],
        gdrv.GM4["r_gate"][1], m_g[0][2], m_g[1][2])
    (vlo, vhi), (tlo, thi), i_min = gdrv.desat_numbers(gdrv.GATE_V[GATE_V]["desat"], v2)
    d = spec("gate_drive/desat")
    assert vlo <= d["V_DS_trip_V"] <= vhi and tlo <= d["blanking_ns"] * 1e-9 <= thi
    for b, key in ((1, "I_turn_off_max_bridge1_A"), (2, "I_turn_off_max_bridge2_A")):
        i_off = spec("worst_case_stresses/" + key)
        v_on = i_off * gdrv.GM4["rds"][1]
        assert vlo >= 1.25 * v_on, "bridge %d DESAT could trip at the spec turn-off peak" % b
        say("desat%d" % b, "DESAT bridge %d: trip V_DS %.1f-%.1f V (spec %.1f V), blanking %.0f-%.0f ns (spec %.0f); on-state at "
            "the spec turn-off peak %.0f A, 175 C = %.2f V -> margin x %.2f", b, vlo, vhi, d["V_DS_trip_V"], tlo * 1e9,
            thi * 1e9, d["blanking_ns"], i_off, v_on, vlo / v_on)
    vth_typ = 2.5                                              # CBB011M12GM4T p2 VGS(th) typ 2.5 V (min 1.8 V) at 25 C
    t_typ = 0.5 * (tlo + thi) + 270e-9 + gdrv.GM4["ciss"] * (GATE_V[0] - vth_typ) / U["i_sto"][1]
    t_max = thi + U["t_ocoff"][1] + gdrv.GM4["ciss"] * (v2[1] - gdrv.GM4["vth_min"]) / U["i_sto"][0]
    t_sc = d["t_sc_assumed_us"] * 1e-6
    assert t_max <= d["response_total_ns_max"] * 1e-9 + 5e-9 and t_max <= 0.7 * t_sc
    say("desat_t", "   DESAT response typ %.2f us, worst case %.3f us (blank max + tOCOFF 400 ns + soft turn-off at ISTO "
        "250 mA) vs dab_spec <= %.2f us and %.0f %% of the ASSUMED %.1f us withstand (dab_spec t_sc_assumed_us; GM4 tSC not "
        "published - Wolfspeed to confirm)", t_typ * 1e6, t_max * 1e6, d["response_total_ns_max"] / 1e3,
        100 * t_max / t_sc, t_sc * 1e6)

    # ---- 4. transformer current sensing: range, insulation arrangement, HV-side supply, AN1, OC and DC trips
    rng, band = spec("sensing/I_xfmr/range_A"), spec("protection/I_xfmr_oc_trip_band_A")
    i_pk = max(spec("transformer/I1_pk_max_A"), spec("transformer/I2_pk_max_A"))     # primary sensor / secondary sensor
    i_rms = max(spec("transformer/I1_rms_max_A"), spec("transformer/I2_rms_max_A"))
    assert HOB["bw"] >= spec("sensing/I_xfmr/bandwidth_Hz_min") and HOB["ipm"] >= max(abs(x) for x in rng)
    assert HOB["ipm"] >= 1.1 * band[1] and i_rms <= HOB["ipn"] and spec("sensing/I_xfmr/sensor").endswith(PARTS["HOB130P"]["mpn"])
    say("sensor", "Transformer current HOB 130-P: +/-%.0f A, %.1f MHz, tD90 %.0f ns = dab_spec range %+.0f..%+.0f A, >= %.1f MHz; "
        ">= 1.1 x the %.0f A top of the OC band (x %.2f); I_pk %.0f A (x %.2f), I_rms %.0f A vs IPN %.0f A. Primary "
        "jumper heating at %.0f A rms / 100 kHz not specified - UNVERIFIED (thermal test on the real busbar)", HOB["ipm"],
        HOB["bw"] / 1e6, HOB["td90"] * 1e9, rng[0], rng[1], spec("sensing/I_xfmr/bandwidth_Hz_min") / 1e6, band[1],
        HOB["ipm"] / band[1], i_pk, HOB["ipm"] / i_pk, i_rms, HOB["ipn"], i_rms)
    v_iso = max(OV_HW.values()) * 1.0
    viowm = {"UCC12050": 1697.0, "AMC3330": AMC["viowm"], "ISO7720F": 2121.0}
    assert min(viowm.values()) >= max(1000.0, v_iso), "HV-PELV barrier part below 1000 V DC / the OV trip"
    for b in (1, 2):
        hob = [r for r, m in bom.items() if m["mpn"] == PARTS["HOB130P"]["mpn"] and parts[r].pins["2"] == "B%d_HGND" % b]
        tie = [r for r, prt in parts.items() if set(prt.pins.values()) == {"B%d_HGND" % b, "P%d_BUS-" % b}]
        bar = sorted(m["mpn"] for r, m in bom.items() if "B%d_HGND" % b in parts[r].pins.values() and
                     ("GND" in parts[r].pins.values() or "3V3_B%d" % b in parts[r].pins.values()))
        assert len(hob) == 1 and len(tie) == 1 and bar == ["AMC3330DWE", "ISO7720FDWR", "UCC12050DVE"], (b, bar)
    say("insul", "Insulation (item 4): HOB 130-P secondary on the HV side of each bridge (H5V / HGND, HGND = DC- by one 0R): "
        "its basic 1000 V barrier carries only functional stress. HV1/HV2 -> PELV only through reinforced parts: UCC12050 "
        "VIOWM %.0f VDC, AMC3330 %.0f VDC, ISO7720F %.0f VDC vs 1000 V DC working (OV trip <= %.0f V); checked per bridge "
        "from the netlist. Layout (ECO-10): reinforced creepage at those 3 footprints per bridge (8 mm bodies)", viowm["UCC12050"],
        viowm["AMC3330"], viowm["ISO7720F"], v_iso)
    r_oc = 3.0 / sum(OC_LAD)
    i_h5 = (HOB["ic"] + 4 * 2.6e-3 + 50e-6 + r_oc + 2 * 5e-3 + 4.2e-3 + 4.5 / 40e3 +
            sum(abs(x) for x in an1_currents(4.5, 2.52)))
    assert i_h5 <= 55e-3 and i_h5 <= 64e-3 and 4.7 >= HOB["uc_min"]
    say("h5v", "HV-side 5.0 V (UCC12050, SEL = VISO): load <= %.1f mA (HOB 26, OPA4388 4 x 2.6, TLV3502 2 x 5, ISO7720F ICC1 "
        "4.2, REF3030E + ladder %.2f, networks %.2f at +250 A) vs 55 mA where VISO = 4.7-5.3 V (>= HOB UC 4.6 V) and "
        "64 mA max at 85 C / VINP 4.5 V (Fig 6-8)", i_h5 * 1e3, (50e-6 + r_oc) * 1e3,
        (4.5 / 40e3 + sum(abs(x) for x in an1_currents(4.5, 2.52))) * 1e3)
    k_a = AN1_K
    vins = [an1(u, ur)[0] for u in (0.0, 5.0) for ur in HOB["uref"]]
    vcms = [an1(u, ur)[1] for u in (0.0, 0.5, 2.5, 4.5, 5.0) for ur in HOB["uref"]]
    outs = [x for u in (0.0, 5.0) for ur in HOB["uref"] for x in an1(u, ur)[2:]]
    an_max = AMC["gain"] * max(abs(v) for v in vins)
    assert max(abs(v) for v in vins) <= AMC["vin_lin"] and AMC["vcm_in"][0] <= min(vcms) and max(vcms) <= AMC["vcm_in"][1]
    assert min(outs) >= 0.1 and max(outs) <= 4.7 - 0.1 and an_max <= 2.0
    bias, rf, rin = float(CTRL.BIAS.rstrip("k")) * 1e3, float(CTRL.AFE_CELL[0][:-1]) * 1e3, 10e3
    v_p = 1.25 * bias / (bias + rin + rf)                     # open P: 1.50k to AGND against the AFE (to VMID 1.25 V)
    v_inv = (AMC["vcm_out"][1] * rf + 1.25 * rin) / (rin + rf)
    v_n = (3.2 / bias + v_inv / rin) / (1 / bias + 1 / rin)    # open N: 1.50k to +3V3A (3.2 V min) against the AFE
    open_p, open_n = (v_p - AMC["vcm_out"][0]) / k_a, (AMC["vcm_out"][1] - v_n) / k_a    # at 0 A, smallest magnitudes
    assert min(abs(open_p), abs(open_n)) > band[1], "an open AN1 line at 0 A could read inside the trip window"
    mis = 0.0
    for side, k in product((0, 1), ("r1", "r3", "r2", "ru", "rg")):
        rr = dict(AN1_R, **{k: AN1_R[k] * 1.001})
        mis += abs(an1(2.5, 2.5, *((rr, None) if side == 0 else (None, rr)))[0])
    say("an1", "AN1 (item 3b): %.3f mV/A differential (VIN = %.4f x (Uout - Uref), AMC3330 gain 2): +/-%.3f V at +/-250 A, "
        "+/-%.2f V at the HOB rails (<= +/-2 V contract, AMC linear +/-1 V in: max %.3f V); CM %.2f-%.2f V (AMC3330 "
        "VCMout), source 0.2 ohm; AMC input CM %.3f-%.3f V (%.3f..%.3f V allowed at |VIN| 1 V); driver outputs %.2f-%.2f V "
        "on 4.7 V min. CTRL (G 0.402, 1.50k bias): open P reads <= %.0f A, open N <= %.0f A at 0 A (window +/-%.0f A); "
        "0 A offset from "
        "0.1 %% mismatch <= %.2f A. Bandwidth 300-375 kHz (AMC3330) - the 1 MHz path is the local OC trip",
        k_a * 1e3, an1(3.5, 2.5)[0], k_a * HOB["ipm"], an_max, max(abs(v) for v in vins), AMC["vcm_out"][0],
        AMC["vcm_out"][1], min(vcms), max(vcms), AMC["vcm_in"][0], AMC["vcm_in"][1], min(outs), max(outs), open_p,
        open_n, OC_TRIP, mis / (k_a / AMC["gain"]))
    (oc_h, dc_h), (oc_l, dc_l) = taps_amps()
    assert abs(oc_h - OC_TRIP) <= 0.02 * OC_TRIP and abs(-oc_l - OC_TRIP) <= 0.02 * OC_TRIP
    assert abs(dc_h - DC_TRIP) <= 0.05 * DC_TRIP and abs(-dc_l - DC_TRIP) <= 0.05 * DC_TRIP
    assert "CMPSS" in spec("protection/I_xfmr_oc_vdep_implementation")
    err_a = (HOB["uref"][1] - 2.5 + HOB["uoe"]) / HOB["sens"] + 2 * 6.5e-3 / HOB["sens"]
    oc_band = (OC_TRIP * (1 - HOB["es"] - HOB["tcs"] * 80) - err_a, OC_TRIP * (1 + HOB["es"] + HOB["tcs"] * 80) + err_a)
    say("oc", "OC window, HV side (REF3030E ladder %s, 0.1 %%): trips at %+.1f / %+.1f A (dab_spec +/-%.0f A, band %.0f-%.0f A); "
        "worst case %.0f-%.0f A (sensitivity +/-0.75 %% + 200 ppm/K x 80 K, Uref/offset +/-25 mV, VOS 6.5 mV); to the gate "
        "%.2f us (tD90 0.20, Uout RC incl. Rout 30 ohm 0.19, divider 0.12, TLV3502 + ISO7720F 0.03, 8 gates + latch 0.04, "
        "driver 0.19). The voltage-dependent limit stays on the CTRL CMPSS (dab_spec)", "/".join(gdrv.ohm(x) for x in OC_LAD),
        oc_h, oc_l, OC_TRIP, band[0], band[1], oc_band[0], oc_band[1], t_oc() * 1e6)
    t_dc = -TAU_DC * math.log(1 - DC_TRIP / (2 * DC_TRIP))
    ripple_a = spec("transformer/I1_pk_max_A") / (2 * math.pi * F_SW * TAU_DC)
    ofs = ((HOB["uoe"] + HOB["tcuoe"] * 80) / HOB["sens"] + HOB["iom"] + (0.3e-3 + 4e-6 * 100) / (k_a / AMC["gain"])
           + mis / (k_a / AMC["gain"]) + 1.49 * 4e-3 / (1 + G_DC) / k_a + 6.5e-3 / (G_DC * k_a))
    assert t_dc <= DC_TIME and ofs < DC_TRIP
    say("dc", "DC-flux window on AN1 (PELV, G %g, ladder %s): %+.2f / %+.2f A of measured DC (dab_spec %.0f A), tau %.2f ms -> "
        "%.2f ms for a %.0f A step (dab_spec %.1f ms); 100 kHz residue %.2f A. Uncalibrated offsets up to +/-%.1f A (HOB "
        "offset + drift + magnetic, AMC3330, 0.1 %% mismatch, CMRR, VOS) shift the window - the firmware flux loop nulls "
        "the measured DC, this trip is the backstop", G_DC, "/".join(gdrv.ohm(x) for x in DC_LAD), dc_h, dc_l, DC_TRIP,
        TAU_DC * 1e3, t_dc * 1e3, 2 * DC_TRIP, DC_TIME * 1e3, ripple_a, ofs)

    # ---- 5. port OV comparators
    t_ov = ov_response()
    assert abs(port.S["divider"]["C_filter_F"] - 4.7e-9) < 1e-12
    for k in (1, 2):
        band, v_op = ov_band(*OV_R[k]), spec("power_stage/port%d/V_operating" % k)[1]
        o = spec("protection/local_ov_board/port%d" % k)
        assert band[2] <= OV_HW[k] and band[3] > OV_FW[k] and band[0] > v_op, \
            "port %d OV band %s vs fw %g / hw %g / operating %g V" % (k, band, OV_FW[k], OV_HW[k], v_op)
        assert abs(ov_peak(k, o["response_us"] * 1e-6, o["trip_V"][2]) - o["V_peak"]) < 0.5, "V_peak formula != dab_design"
        v_pk = ov_peak(k, t_ov, band[2])
        assert v_pk <= o["V_peak"] and v_pk <= o["cap_V_allowed_hot_spot"] and v_pk * 1.25 <= CLAMP["vrrm"]
        say("ov%d" % k, "Port %d OV (OPA2388 comparator, R1/R2 %s/%s, REF3020E): trips at %.1f V nominal, %.1f-%.1f V worst "
            "case incl. AMC3330 gain/offset and divider (<= spec hw %.0f V); >= %.1f V relative to the firmware trip %.0f V "
            "(same AMC3330); above the %.0f V operating max; response %.1f us (divider %.1f incl. AMC3330 + OPA2388 tOR 10 + "
            "logic 0.25); load-rejection peak %.0f V at %.2f V/us (dab_spec %.0f V at %.0f us) vs bank %.0f V, DD600N16K "
            "%.0f V", k, gdrv.ohm(OV_R[k][0]), gdrv.ohm(OV_R[k][1]), band[1], band[0], band[2], OV_HW[k], band[3],
            OV_FW[k], v_op, t_ov * 1e6, port.S["divider"]["lag_s"] * 1e6, v_pk, o["slew_V_per_us"], o["V_peak"],
            o["response_us"], o["cap_V_allowed_hot_spot"], CLAMP["vrrm"])

    # ---- 6. fail-safe RS-422 and supervision of +24V
    assert VID_OPEN <= -0.25 and VID_CTRL_OFF <= -0.25, "fail-safe bias leaves less than 50 mV margin"
    assert UV_FALL[0] > 18.9 + 0.5 and UV_RISE[1] < 21.6, "UV trip vs UCC14241 UVLO / 21.6 V system minimum"
    say("failsafe", "Fail-safe pairs (%s/%s/%s): open VID <= %.3f V, CTRL unpowered (100 uA IO(OFF)) <= %.3f V vs -0.2 V "
        "receiver threshold -> LOW = off. +24V UV %.2f-%.2f V falling (UCC14241 UVLO 17.1-18.9 V), %.2f-%.2f V rising "
        "(system min 21.6 V)", gdrv.ohm(BIAS[0]), gdrv.ohm(BIAS[1]), gdrv.ohm(BIAS[0]), VID_OPEN, VID_CTRL_OFF,
        UV_FALL[0], UV_FALL[1], UV_RISE[0], UV_RISE[1])

    faults = [logic_check(B, b) for b in (1, 2)][0]
    say("logic", "Logic (drawn netlist evaluated, both bridges): FLT low, RUN and all 4 PWM low for each of %s, with EN high "
        "and with EN low (standing fault visible, INT-17); RDY_L (driver UVLO) in FLT, EN_DRV and RUN (INT-18); a trip "
        "stays latched until EN low; CELL RDY driven high = RDY_L & UV_OK only (CTRL INT-15 enable)", ", ".join(faults))

    # ---- 7. CELL +24V budget (interfaces.CELL_24V_MAX_A / _UF)
    i_ucc = 5.3 * i_h5 / (0.45 * 4.9)          # UCC12050 input: Fig 6-12 ~51 % at 50 mA (25 C), 45 % ASSUMED
    i5 = (i_ucc + AMC["idd"] + 2 * 5e-3 + 2 * 2 * 2.6e-3 + 50e-6 + 5.0 / sum(DC_LAD) + 3e-3)      # 5 V loads (max)
    i33 = (2 * 17e-3 + 30e-3 + 5 * 3.465 / (2 * BIAS[0] * 0.99 + BIAS[1] * 0.99) + 4 * 4e-3 + 4 * 18e-6 + 2 * 0.7e-3
           + 4 * 0.35e-3 + 4 * 0.35e-3 + 0.35e-3 + 1e-3 + 2.1e-3 + 0.35e-3 + 3.465 / 100e3)   # 3.3 V loads
    i24_logic = 5.02 * (i5 + i33) / (0.85 * 21.6)
    i24_typ = 4 * p_bias / (0.52 * 21.6) + i24_logic                                       # UCC14241 Fig 10-17 eff ~52 %
    i24_max = 4 * 0.25 * p_bias / 1.5 + i24_logic                                          # IVIN 250 mA @ 1.5 W, scaled
    caps = {}
    for b in (1, 2):
        v24 = "+24V_B%d" % b
        caps[b] = sum(cap_value(bom[r]["value"]) for r, prt in parts.items() if r in bom and bom[r]["desc"].startswith(
            "Capacitor") and v24 in prt.pins.values())
    assert i24_max <= IF.CELL_24V_MAX_A and max(caps.values()) * 1e6 <= IF.CELL_24V_MAX_UF
    say("cell24", "CELL +24V per bridge: %.2f A typ / %.2f A bound (4 x UCC14241 at %.2f W: eff ~52 %% typ Fig 10-17, 250 mA max "
        "at 1.5 W scaled to the load - ASSUMED proportional; logic %.0f mA at 5 V incl. UCC12050 + AMC3330, %.0f mA at "
        "3.3 V) vs %.1f A; input capacitance %.0f uF vs %.0f uF", i24_typ, i24_max, p_bias, i5 * 1e3, i33 * 1e3, IF.CELL_24V_MAX_A,
        max(caps.values()) * 1e6, IF.CELL_24V_MAX_UF)

    lv, gap, r_eff, v_unp = id_levels()
    code = lambda r: RB["vbias"] * r / (r + RB["pullup"])
    v_id, v_next = code(2.49e3), code(4.99e3)
    assert IF.ID_OHM["DAB60-PORT"] == "2.49k" and abs(r_eff / 2.49e3 - 1) < 0.005 and gap >= 3e-3
    assert max(abs(lv[0][0] - v_id), abs(lv[-1][1] - v_id), abs(v_unp - v_id)) <= 0.165 and lv[-1][1] < v_next - 0.165
    say("id", "DAB60-PORT ID + readback (%s || %s x2 || %s x2): R_eff %.3f k (code 2.49k, %+.2f %%); nine levels %.3f-%.3f V, "
        "gap >= %.1f mV, within +/-165 mV of %.3f V and below the PVCELL-25 window (%.3f V); unpowered %.3f V",
        gdrv.ohm(RB["r_id"]), gdrv.ohm(RB["r_a"]), gdrv.ohm(RB["r_b"]), r_eff / 1e3, 100 * (r_eff / 2.49e3 - 1),
        lv[0][0], lv[-1][1], gap * 1e3, v_id, v_next - 0.165, v_unp)

    studs = {r: parts[r].pins["1"] for r, m in bom.items() if m["mpn"] == port.PARTS["STUD"]["mpn"]}
    fitted = sorted(n for r, n in studs.items() if not bom[r]["dnp"])
    assert fitted == ["A_X+", "P1_BUS+", "P1_BUS-", "P2_BUS+", "P2_BUS-"] and len(studs) == 6, "AUX-HV: port 1 only"
    say("auxhv", "AUX-HV (D-021, INT-11): terminal-side tap on port 1 only - fitted studs %s; port 2's B_X+ stud %s DNP",
        ", ".join(fitted), [r for r, n in studs.items() if n == "B_X+"][0])
    assert all(LOOP_I[1] <= i and LOOP_V <= v for i, v, _ in CONTACTS.values())
    say("loop", "Coolant loop on J801 (ISO1211 %.2f-%.2f mA, <= %.0f V open, 24V_OR bound ASSUMED): VK3 reed %.0f A / %.0f V - "
        "no minimum current stated (UNVERIFIED, reed contacts are normally dry-circuit capable); thermostat 3106U %.1f A / "
        "%.0f V, %s contacts (3100U silver replaced); both RFQ (set points)", LOOP_I[0] * 1e3, LOOP_I[1] * 1e3, LOOP_V,
        CONTACTS["VK3"][0], CONTACTS["VK3"][1], CONTACTS["TSTAT"][0], CONTACTS["TSTAT"][1], CONTACTS["TSTAT"][2])

    # ---- 8. analog outputs vs the CTRL-C2000 cell front end (CTRL.AFE_CELL)
    g = float(CTRL.AFE_CELL[0][:-1]) * 1e3 / 10e3
    adc = [1.25 + g * an_max * s_ for s_ in (-1, 1)]
    an2 = 100e3 / 111e3 * 0.895 * 3.465
    an3 = 3.0 * AN3[1]["rb"] / (AN3[1]["rb"] + 100.0)
    assert 0 <= min(adc) and max(adc) <= 2.5 and an2 <= 3.0 and an3 <= 3.0
    say("an", "AN1 at the CTRL (G %.3f): ADC %.2f-%.2f V at the HOB rails; AN2 <= %.2f V, AN3 <= %.2f V single-ended on AGND "
        "(CTRL 0-3 V range), 49.9 ohm sources", g, adc[0], adc[1], an2, an3)
    rt = lambda tc: 10e3 * {-20: 9.707, 25: 1.0, 50: 0.3603, 90: 0.09177, 130: 0.03009, 155: 0.01653}[tc]   # B57703M p4
    for b in (1, 2):
        vs = [3.0 * AN3[b]["rb"] / (rt(tc) + 100 + AN3[b]["rb"]) for tc in AN3[b]["t"]]
        say("an3_%d" % b, "AN3 bridge %d (%s, Rb %s): %s", b, AN3[b]["what"], gdrv.ohm(AN3[b]["rb"]),
            ", ".join("%.2f V at %d C" % (v, tc) for v, tc in zip(vs, AN3[b]["t"])))
    for line in out.values():
        print(line)
    o1, o2 = ov_band(*OV_R[1]), ov_band(*OV_R[2])
    notes = [
        "CALCULATED, not bench-validated. Values from sim/out/dab_design/dab_spec.json, checked by design_check() "
        "(outputs/DAB60_design_check.txt). Bridge 1 -> CTRL-C2000 C1, bridge 2 -> C2 (HRPWM). Ports via port.port(): "
        "port 1 = tag A (HV1), port 2 = tag B (HV2).",
        "Fail-safe off: received pairs biased %s/%s/%s -> undriven VID <= %.2f V (receiver -0.2 V) = LOW = off; no 3.3 V "
        "-> UCC14241 ENA low, PWM/EN pulled low; +24V_GD below %.1f V -> EN_DRV and PWM forced low before the bias UVLO."
        % (gdrv.ohm(BIAS[0]), gdrv.ohm(BIAS[1]), gdrv.ohm(BIAS[0]), max(VID_OPEN, VID_CTRL_OFF), UV_FALL[0]),
        "Local trips, latched until EN low (no firmware): transformer OC %+.0f/%+.0f A (%.2f us), |I_dc| %.1f A (tau 1 ms), "
        "port OV %.0f / %.0f V (%.0f us), +24V UV, driver UVLO; DESAT gates all PWMs of its bridge. FLT reports all, also "
        "with EN low; RDY is driven." % (oc_h, oc_l, t_oc() * 1e6, DC_TRIP, o1[1], o2[1], ov_response() * 1e6),
        "AN1 = %.3f mV/A on 1.44 V CM (AMC3330, 0.2 ohm). Domains: PELV, HV1, HV2, PE + 8 gate drivers. Isolators: UCC21710, "
        "UCC14241-Q1, UCC12050, AMC3330, ISO7720F, transformer, port AMC/TPSI, contactors." % (AN1_K * 1e3),
        "ECO-10 (layout): HV1/HV2-PELV reinforced >= 1000 V DC at every isolator footprint (8 mm parts: PD1 coating or "
        "slots); the HOB 130-P sits inside HV1/HV2 (functional). HV1-HV2 via the transformer, 1850 V DC basis."]
    return out, notes


# ======================================================================== build
def build_design():
    B = L.Builder(PROJECT, "DAB60 power assembly", REV, DATE, CATALOG,
                  rails=["+24V", "+3V3S", "+24V_B1", "5V_B1", "3V3_B1", "B1_H5V", "+24V_B2", "5V_B2", "3V3_B2",
                         "B2_H5V"],
                  returns=["GND", "AGND", "PE", "B1_HGND", "B2_HGND"],
                  subtitle="DAB-D60 60 kW 100 kHz isolated DAB: 2 x CBB011M12GM4T, 11:12 transformer, two DC ports",
                  comment1="Interfaces: CELL x2 (C1, C2), PORT, SYS-IO-AUX harness + coolant loop (gen/interfaces.py)",
                  comment4="Not bench-validated. Domains PELV, HV1, HV2, PE, GD_B<b><sw> (8 gate drivers).")
    b1 = bridge(B, 1, 1)
    b2 = bridge(B, 2, 6)
    xf, mag_hv = magnetics(B, 11)
    pa = port.port(B, "A", "P1_BUS+", "P1_BUS-", 12, hv="HV1")
    pb = port.port(B, "B", "P2_BUS+", "P2_BUS-", 14, hv="HV2")
    io_sheet(B, 16, [(p["nets"]["rb1"], p["nets"]["rb2"]) for p in (pa, pb)])
    gd = dict(b1["gd"], **b2["gd"])
    hv1 = set(b1["hv"]) | mag_hv | {n for n, d in pa["domains"].items() if d == "HV1"}
    hv2 = set(b2["hv"]) | {n for n, d in pb["domains"].items() if d == "HV2"}
    assert not (hv1 & hv2) and not ((hv1 | hv2) & set(gd)), "HV1 / HV2 / gate-driver nets overlap"

    def domain_of(net):
        return gd.get(net) or ("HV1" if net in hv1 else "HV2" if net in hv2 else "PE" if net == "PE" else "PELV")
    iso = b1["iso"] | b2["iso"] | pa["isolators"] | pb["isolators"] | {xf}
    xing = b1["xing"] | b2["xing"] | pa["crossings"] | pb["crossings"]
    return B, domain_of, iso, xing


if __name__ == "__main__":
    B, domain_of, iso, xing = build_design()
    lines, notes = design_check(B)
    B.D.root_notes = notes
    rc = L.build(B, waivers=port.WAIVERS, domain_of=domain_of, isolators=iso, crossings=xing)
    with open(os.path.join(L.REPO, "hardware", PROJECT, "outputs", PROJECT + "_design_check.txt"), "w") as f:
        f.write("CALCULATED by gen/dab60.py design_check() - not measured, not bench-validated.\n")
        f.write("\n".join(lines.values()) + "\n")
    sys.exit(rc)
