"""PVCELL-25 - one 25 kW (27.5 kW max) non-isolated bidirectional buck-boost power cell (REQUIREMENTS.md PV-01..PV-22):
3 cells = PV-P75, 4 cells = PV-P100/110, interleaved. Decisions D-007 (topology), D-012 (safety chain), D-016 (bias),
D-031..D-042 (rev C: Asian sourcing, Sichain devices, NOVOSENSE driver, insulation D-032).

  power  2-level four-switch buck-boost with a common negative: leg A = S1/S2 on port A, leg B = S3/S4 on port B,
         each switch 2 x SG2M040170HJ; port film banks, per-leg film decoupling + RC damper, bleeders; off-board
         CUSTOM inductor on studs between the switch nodes; bus studs A_BUS+ / B_BUS+ / BUS- to PV-PORT (gen/port.py)
  drive  4 x gdrv.channel() rev 5: NSI6651ASC-Q1 + UCC14241-Q1 at +18/-3.5 V, Ron/Roff per device, AO3400A Miller
         clamps, SC booster, per-leg interlock, DESAT; driver logic side at +5V (thresholds specified at 5 V only)
  sense  AN1 inductor current (LEM LA 150-P closed-loop Hall, 20 mV/A diff.), AN2 heatsink and AN3 winding temperature (0-2.5 V),
         NTC pair = second heatsink NTC for CTRL-C2000
  trip   local inductor OC window + heatsink OT -> latch -> PWM gated, EN_DRV low, FLT; DESAT in the drivers
  CELL   40-way interfaces.CELL: fail-safe RS-422 receivers (open / unpowered pair = gate off), FLT driver, RDY, ID
Every component value is read from sim/out/pv_design/cell_spec.json at build time; design_check() re-checks the
values against the spec and the datasheets on every build. Usage: .venv/bin/python gen/pvcell.py
"""
import contextlib
import io
import json
import math
import os
import re
import sys

import catalog
import ctrl_c2000
import dcdclib as L
import gdrv
import interfaces as IF
import port

PROJECT, REV, DATE = "PVCELL-25", "C0", "2026-10-04"
DS = "docs/datasheets/"
TI = "Texas Instruments"
SPEC = json.load(open(os.path.join(L.REPO, "sim", "out", "pv_design", "cell_spec.json")))


def spec(*path):
    """cell_spec.json value at `path`; a missing key stops the build, so the drawing cannot drift from the design."""
    v = SPEC
    for k in path:
        try:
            v = v[k]
        except (KeyError, IndexError, TypeError):
            raise SystemExit("cell_spec.json has no %s - re-run sim/pv_design.py" % "/".join(map(str, path)))
    return v


def deck():
    """L_loop, R_damp, V and I of the spec's worst-case commutation deck (cell_spec spice/dpt_deck)."""
    path = os.path.join(L.REPO, spec("spice", "dpt_deck"))
    v = {t[0]: float(t[3]) for t in (line.split() for line in open(path)) if len(t) >= 4 and t[0] in
         ("Lloop", "Rdamp", "Vdc", "Iload")}
    if len(v) != 4:
        raise SystemExit("%s: Lloop/Rdamp/Vdc/Iload not found" % path)
    return v


def report_dvdt():
    """Worst switch-node dv/dt (V/ns, turn-on at OVP and the trip current) from the power-stage report, section 4:
    cell_spec.json does not carry it."""
    path = os.path.join(L.REPO, "sim", "out", "pv_design", "report.md")
    m = re.search(r"turn-on dv/dt (\d+(?:\.\d+)?) V/ns", open(path).read())
    if not m:
        raise SystemExit("%s: 'turn-on dv/dt <n> V/ns' not found" % path)
    return float(m.group(1))


IND = spec("inductor")
DECK = deck()
LEG = {t[0]: float(t[3]) for t in (ln.split() for ln in open(os.path.join(L.REPO, spec("spice", "leg_deck"))))
       if len(t) >= 4 and t[0] in ("Lbus", "Lloop", "Ldc", "Rdmp", "Cdmp")}
assert len(LEG) == 5, "leg deck: Lbus/Lloop/Ldc/Rdmp/Cdmp not found"

# ------------------------------------------------------------------------------------------------ datasheet limits
DEV = dict(mpn="SG2M040170HJ", vdss=1700.0, id100=47.0, idm=188.0, tj=175.0, vgs=(-8.0, 22.0), t_off=52e-9)
#   Sichain SG2M040170HJ V01_02 Table 2 p3 (VDS, ID at TC 100 C, ID(pulse), VGS,max abs -8/+22 V, VGSop -4/+18 V,
#   TJ), Table 6 p5 (td(off) 41 + tf 11 ns at 1200 V, 38 A, Rg 2.5 ohm)
C45 = dict(c=45e-6, vndc=1300.0, vop85=1100.0, irms=34.0, esl=19e-9)        # KEMET F3114_C4AQ p14 C4AQUEW5450A3BJ
C22 = dict(c=2.2e-6, vndc=1300.0, vop85=1100.0, irms=7.1, esl=24e-9, ipkr=63.0)   # p14 C4AQUBU4220A1YJ
WND = dict(vrrm=1600.0, ifsm=1050.0, i2t=5513.0,       # WND75P16W6 Table 3 p3 (10 ms sine, Tj(init) 25 C)
           ifsm_tp=((1e-5, 1865.0), (1e-4, 1830.0), (5e-4, 1774.0), (1e-3, 1683.0), (2e-3, 1533.0), (1e-2, 1050.0)))
#   ifsm_tp: Fig. 4 p4 read off the graph (the points cell_spec carries); allowed I2t(tp) = IFSM(tp)^2 x tp / 2
C4AQ_OV = 1.15                       # p5: 1.15 x VNDC for 30 min/day (IEC 61071 overvoltage)
CRCW = dict(p70=1.5, t_max=155.0, umax=500.0, e_1us=500e-6)   # Vishay CRCW-HP e3 doc 20043 p1 (2512: P70, film 155 C,
#   Umax 500 V); p5 single-pulse curve: 2512-HP >= 500 W at 1 us (GRAPH READING, conservative) -> >= 500 uJ
T_BOARD = 85.0                       # board temperature at the dampers and bleeders: ASSUMED (no PCB thermal model)
AMC3330_DELAY = 2.1e-6               # SBASA34B signal delay 50-50 % max (port-board VA/VB channel)
LA150 = dict(kn=2000.0, ipm=212.0, rm_max150=78.0, rm_max212=30.0, td10=0.3e-6, td90=0.5e-6, bw_1db=150e3, err=0.005,
             lin=0.001, io=(0.10e-3, 0.25e-3, 0.30e-3), ic=10e-3, uc=(14.25, 15.75), basic=1000.0, reinf=600.0,
             ud=4.3e3, uni=8e3, ut=1.3e3, creep=8.0, t_cond=100.0)
#   LEM LA 150-P (24 Jul 2024, v5) p1: KN 1:2000, IPM +/-212 A, RM max at 85 C / +/-15 V 78 ohm (150 A) and 30 ohm (212 A),
#   tD10 < 300 ns, tD90 < 500 ns (100 A/us), BW -1 dB 150 kHz, error 0.5 %, linearity 0.1 %, IOE/IOM/IOT 0.10/0.25/0.30 mA,
#   IC 10 mA + IS, UC +/-15 V +/-5 %; p3: Ud 4.3 kV, UNi 8 kV, Ut 1.3 kV, 8 mm, basic 1000 V / reinforced 600 V (EN 50178,
#   OV 3, PD2); p4: aperture 13.5 x 10 mm, primary conductor <= 100 C
RS3 = dict(vin=(18.0, 36.0), vout=15.0, acc=0.02, iout=0.100, eff=0.83, iq=0.020, cmax=1000e-6, ta_full=71.0,
           derate=((71.0, 1.00), (85.0, 0.30)))   # p4 derating graph: 100 % to 71 C, linear to 30 % at 85 C (end of range)
#   RECOM RS3-2415D (REV 4/2024) p1: 18-36 V in, +/-15 V +/-100 mA, 83 %, +/-1000 uF; p3: +/-2 %, 20 mA quiescent at 24 V,
#   input fuse required (note 6); p4: full load to 71 C, derating to 30 % at 85 C (graph)
UMF = dict(i_n=0.5, v_dc=125.0, t=(-55.0, 125.0))   # SCHURTER UMF 250 p1 (125 VDC, -55..125 C), p3 0.5 A 3405.0163.24
OPA2 = dict(swing=0.1, i_load=5e-3, ro=90.0)          # OPA4322/OPA2322 SBOS538F p8: RO 90 ohm; swing 30 mV at 10 k (p8),
#   0.1 V at <= 5 mA from Fig 14 (GRAPH READING, conservative)
TNPV = dict(umax=1000.0, p70=0.33, t_film=125.0)   # Vishay TNPV e3 doc 28881 p1-2 (TNPV1210, general mode)
NTC = dict(r25=10e3, tol=0.02, b=3988.0, b_tol=0.01, t_cat=125.0, dth=3e-3)   # TDK B57703M p2-3 (B57703M0103A018)
RT8016 = [(-40, 33.65), (-35, 24.26), (-30, 17.7), (-25, 13.04), (-20, 9.707), (-15, 7.293), (-10, 5.533), (-5, 4.232),
          (0, 3.265), (5, 2.539), (10, 1.99), (15, 1.571), (20, 1.249), (25, 1.0), (30, 0.8057), (35, 0.6531),
          (40, 0.5327), (45, 0.4369), (50, 0.3603), (55, 0.2986), (60, 0.2488), (65, 0.2083), (70, 0.1752),
          (75, 0.1481), (80, 0.1258), (85, 0.1072), (90, 0.09177), (95, 0.07885), (100, 0.068), (105, 0.05886),
          (110, 0.05112), (115, 0.04454), (120, 0.03893), (125, 0.03417), (130, 0.03009), (135, 0.02654),
          (140, 0.02348), (145, 0.02083), (150, 0.01853), (155, 0.01653)]       # B57703M p4, R/T curve 8016 (RT/R25)
UCC14 = dict(uv_rise=(19.0, 21.0), uv_fall=(17.1, 18.9), vin=(21.0, 27.0), i_nl=0.035, i_fl=0.250, p_fl=1.5, cmti=150.0)
#   UCC14241-Q1 SLUSF09A p6 (VIN ROC), p8 (IVIN no load / full load 60 mA x 25 V, UVLOP rising/falling), p10 (CMTI)
NSI = gdrv.NSI                       # NSI6651ASC-Q1 data as gdrv reads it (NSI66x1A-Q1 Rev 1.1)
V5 = (0.985 * (1 + 99e3 / 25.149e3), 1.015 * (1 + 101e3 / 24.651e3))   # +5V: LMR36015 VFB 0.985-1.015 V (SNVSB49D
#   p6), 100k/24.9k 1 % -> 4.86-5.17 V: the logic side of the 4 drivers (NSI6651 VCC1 3.0-5.5 V, ROC p4)
LVC17_5V = dict(vtm=((4.5, 1.51, 1.97), (5.5, 1.88, 2.40)), tpd=(0.9e-9, 4.4e-9))   # SN74LVC1G17 SCES351Y p6 VT-
#   (min, max at VCC 4.5 / 5.5 V), p7 tpd at 5 V +/-0.5 V (-40..85 C, the row gdrv uses at 3.3 V)
AHCT = dict(vih=2.0, voh=4.4, tpd=(1e-9, 8e-9), icc=20e-6, dicc=1.5e-3)   # SN74AHCT08 SCLS237P p4 (VIH, VOH at 4.5 V,
#   -50 uA), p5 tPLH/tPHL 1-8 ns at 15 pF -40..85 C, ICC 20 uA + delta-ICC 1.5 mA per 3.4 V input; AHCT1G08 SCLS315S p3/p5
T_DRV = NSI["tprop"][2] + AHCT["tpd"][1]   # 5 V level stage + NSI6651 propagation (max), command to gate
DT_R = 4.02e3                       # interlock stretch R (100p C0G, gdrv channel), centred in cell_spec's window at 5 V
VCLASS, R_SB = 1700, gdrv.R_SB_CLASS[1700]   # DESAT / dead-time class and SC-booster resistor per device (gdrv rev 5)
V_PEAK = spec("switch_node_recurring_peak", "at_1100V_trip_V")   # IC-06: asserted <= VIORM inside gdrv.channel()


def stretch5(r, c):
    """gdrv.stretch() re-evaluated at this board's 5 V logic supply (gdrv's is fixed to 3.3 V): RC to the SN74LVC1G17
    VT- (SCES351Y p6 rows, interpolated in VCC), R 1 %, C 5 %, BAT54 VF as gdrv. Returns (t_min, t_max)."""
    def vtm(v, i):
        (v1, *a), (v2, *b) = LVC17_5V["vtm"]
        return a[i] + (b[i] - a[i]) * (v - v1) / (v2 - v1)
    t_min = min(r * 0.99 * c * 0.95 * math.log((v - gdrv.BAT54_VF[1]) / vtm(v, 1)) for v in V5)
    t_max = max(r * 1.01 * (c * 1.05 + gdrv.LVC1G17["ci"]) * math.log((v - gdrv.BAT54_VF[0]) / vtm(v, 0)) for v in V5)
    return t_min, t_max
RX = dict(vit=0.2, ri=4e3, icc=17e-3, tpd=26e-9)       # AM26LV32E SLLS849E p5 (VIT, ri min, ICC), p6 (tPHL)
TX = dict(voh=2.4, vol=0.4, i_test=20e-3, ioff=100e-6)  # AM26LV31E SLLS848C p5 (VOH/VOL at 20 mA, IO(OFF))
TLV = dict(vos=6.5e-3, tpd=6.4e-9, iq=5e-3)             # TLV3502 SBOS321E p5 (VOS, IQ per channel), p6 (tpd)
OPA = dict(vos=2e-3, iq=1.75e-3)                        # OPA4322 SBOS538F p7 (VOS), p8 (IQ per channel)
LM40 = dict(v=2.5, tol=0.0075, irmin=65e-6, imax=15e-3)  # LM4040 SNOS633N 5.8 p12 (A grade, -40..85 C)
TPS37 = dict(vit_f=(0.387, 0.400), vit_r=(0.396, 0.404), tpd=18e-6)   # TPS3710 SBVS271A p5, p6
T_LOGIC = 6e-9            # LVC08 / LVC11 4.1 ns, LVC1G74 5.9 ns max at 3.3 V (SCAS283W, SCLS993A, SCES794G p6-7)
V24 = (21.6, 26.4)        # D-013 module 24 V window; eFuse 150 mOhm (TPS26600) + cable 0.105 ohm (0.5 m, ASSUMED)

# ------------------------------------------------------------------------------------------------ catalog
MAG = json.load(open(os.path.join(L.REPO, "sim", "out", "magnetics", "design_pv_inductor.json")))   # rev M1 construction
MAG_ROW = MAG["cost"]["cost_estimates_row"].split(",")[0]          # BOM value = the costed row (gen/data/cost_estimates.csv)
assert MAG["revision"] in ("M1", "M2") and MAG["windings"][0]["turns"] == IND["turns"] and \
    abs(float(re.search(r"(\d+)uH", MAG_ROW).group(1)) / IND["L_at_45A_uH"] - 1) <= IND["L_tolerance_pct"] / 100, "L_CELL"
_lv = {x["kind"]: x for x in MAG["insulation"]["levels"]}          # B = winding-core basic, R = NTC-winding reinforced
L_DESC = ("OFF-BOARD CUSTOM power inductor rev %s (sim/out/magnetics/design_pv_inductor.json; winding-house sheet "
          "sim/out/magnetics/spec_pv_inductor.md; electrical requirement sim/out/pv_design/cell_spec.json): %s. L0 %.0f uH "
          "+/-%.0f %%, %.0f uH nom / %.0f uH min at 45 A (>= %.1f uH required), L(trip %.1f A)/L0 %.2f; ripple %.1f App at "
          "%.0f kHz; R_dc %.1f mOhm at 110 C; loss %.1f W core + %.1f W copper (45 A); hot spot %.0f C (<= %.0f C); %.2f kg. "
          "Embedded NTC 10 k 2 %% B25/100 3988 K (R/T 8016). INSULATION (IC-16): %s. Winding-core BASIC (U_rp %.0f V): "
          "PD <= 10 pC at %.0f Vpk (extinction >= %.0f Vpk), AC %.0f Vrms, impulse %.0f V; NTC-winding REINFORCED: PD <= 10 pC "
          "at %.0f Vpk, AC %.0f Vrms, impulse %.0f V. Routine: %s. Type: %s. Core and clamp bonded to PE. LEAD A (through the "
          "LA 150-P on the cell board): flexible insulated lead, SUPPLEMENTARY insulation for 1000 V DC working, type-tested "
          "to the basic levels above, <= 10 mm outer size for the 13.5 x 10 mm aperture, <= 100 C there; copper screen over "
          "the aperture length, pigtail to PE (pin SCR)" % (
              MAG["revision"], MAG["construction"], MAG["electrical"]["L0_H"] * 1e6, IND["L_tolerance_pct"],
              MAG["electrical"]["L_at_45A_nom_H"] * 1e6, MAG["electrical"]["L_at_45A_min_H"] * 1e6,
              MAG["electrical"]["L_at_45A_req_H"] * 1e6, IND["I_trip_A"], MAG["electrical"]["L_trip_over_L0"],
              MAG["electrical"]["ripple_pp_A"], MAG["electrical"]["f_ripple_Hz"] / 1e3,
              MAG["windings"][0]["R_dc_110C_ohm"] * 1e3, MAG["verification"]["own"]["P_core_W"],
              MAG["verification"]["own"]["P_cu_W"], MAG["thermal"]["T_hotspot_C"], MAG["thermal"]["T_hotspot_max_C"],
              MAG["mass_kg"], MAG["insulation"]["system"], _lv["B"]["u_rp"], _lv["B"]["pd_test"], _lv["B"]["pd_ext"],
              _lv["B"]["ac"], _lv["B"]["imp"], _lv["R"]["pd_test"], _lv["R"]["ac"], _lv["R"]["imp"],
              MAG["insulation"]["routine_tests"], MAG["insulation"]["type_tests"]))
HS_PAD = spec("heatsink_insulator")                               # IC-07 / D-032 pad specification
PARTS = {
    # Heatsink insulator per TO-247 tab (IC-07): CUSTOM to the cell_spec specification; pin 1 = tab (drain), 2 = heatsink
    "PAD_ALN": dict(mfr="", mpn="", prefix="P", pkg="ceramic pad, %s" % HS_PAD["size_mm_min"], ds="", sourcing="CUSTOM",
                    desc=("TO-247 heatsink INSULATOR, basic HV-PE (IC-07, D-032): %s, size %s, overhang >= %.1f mm "
                          "(%s); Rth c-s %.2f K/W (%s). MOUNTING: %s. TESTS: %s" % (
                              HS_PAD["material"], HS_PAD["size_mm_min"], HS_PAD["overhang_mm_min"],
                              HS_PAD["overhang_basis"], HS_PAD["Rth_cs_K_W"], HS_PAD["Rth_basis"], HS_PAD["mounting"],
                              HS_PAD["tests"])),
                    pins={"left": ["1 TAB p"], "right": ["2 HS p"]}),
    # Sichain SG2M040170HJ V01_02 (2025-12-30) p1 pinout figure: pin 1 + TAB drain, pin 2 power source, pin 3 driver
    # (Kelvin) source, pin 4 gate; outline TO-247-4L-A p11. Ratings p3 (see DEV above). No qualification stated (p1/p13).
    "SG2M040170HJ": dict(mfr="Sichain", mpn="SG2M040170HJ", prefix="Q", pkg="TO-247-4L (tab = drain)",
                         ds=DS + "power-semiconductors/SG2M040170HJ.pdf",
                         desc="SiC MOSFET 1700 V 40 mOhm, TO-247-4L with driver source, VGS -8/+22 V abs, -4/+18 V op; "
                              "no qualification stated (Sichain p14: consult for high reliability). Mount on PAD_ALN",
                         pins={"left": ["4 G i", "3 SS p"], "right": ["1 D p", "5 TAB p", "2 S p"]}),
    # KEMET F3114_C4AQ (5/5/2025) p14: 1300 V class (VNDC 1300 V at 70 C hot spot, VOP85 1100 V), two terminals
    "C4AQ_45U": dict(mfr="KEMET", mpn="C4AQUEW5450A3BJ", prefix="C", pkg="radial 4-lead 57.5x45x65 mm",
                     stock=("Device", "C"), ds=DS + "passives-capacitors/C4AQ.pdf",
                     desc="DC-link PP film 45 uF 5 %, 1300 VDC (1100 V at 85 C), 34 Arms, 3.1 mOhm, 19 nH"),
    "C4AQ_2U2": dict(mfr="KEMET", mpn="C4AQUBU4220A1YJ", prefix="C", pkg="radial 2-lead 27.5 mm",
                     stock=("Device", "C"), ds=DS + "passives-capacitors/C4AQ.pdf",
                     desc="PP film 2.2 uF 5 %, 1300 VDC (1100 V at 85 C), 63 A peak, 7.1 Arms, 24 nH (leg decoupling)"),
    "L_CELL": dict(mfr="", mpn="", prefix="L", pkg="off-board, 2 power lugs + NTC lead", ds="", sourcing="CUSTOM",
                   desc=L_DESC, pins={"left": ["1 A p", None, "3 NTC1 p", "5 SCR p"], "right": ["2 B p", None, "4 NTC2 p"]}),
    # TDK B57703M (Jan 2018) p2-3: B57703M0103A018 10 k 2 %, B25/100 3988 K 1 %, 200 mm PTFE leads, metal tag,
    # test voltage 1000 V AC, category 55/125/56; two leads, no polarity (KiCad Thermistor_NTC 1/2)
    "B57703M": dict(mfr="TDK Electronics", mpn="B57703M0103A018", prefix="RT", pkg="M703 metal-tag probe, 200 mm leads",
                    stock=("Device", "Thermistor_NTC"), ds=DS + "sensing/B57703M.pdf",
                    desc="NTC probe 10 k 2 %, B25/100 3988 K, metal tag screwed to the heatsink, 125 C category"),
    # XKB X6511W sheet p1 (docs/datasheets/connectors/XKB-X6511W.pdf): 1 row, pins 1-2; 3 A, 250 V, -40..105 C.
    # gen/data/alternates.csv ADOPT (DROP-IN for Samtec TSW-102-07-G-S): signal only (NTC, PELV)
    "J_NTC": dict(mfr="XKB Connection", mpn="X6511WV-02H-C60D30", prefix="J", pkg="1x2 2.54 mm THT",
                  ds=DS + "connectors/XKB-X6511W.pdf", stock=("Connector_Generic", "Conn_01x02"),
                  desc="Header 1x2 2.54 mm, inductor winding NTC (PELV)"),
    # OPA4322 SBOS538F p6 Pin Functions (PW TSSOP-14)
    "OPA4322": dict(mfr=TI, mpn="OPA4322AIPWR", prefix="U", pkg="TSSOP-14 (PW)", ds=DS + "sensing/OPA4322.pdf",
                    desc="Quad RRIO CMOS op amp 20 MHz, 10 V/us, 1.8-5.5 V",
                    pins={"left": ["3 +INA i", "2 -INA i", None, "5 +INB i", "6 -INB i", None, "10 +INC i", "9 -INC i",
                                   None, "12 +IND i", "13 -IND i"],
                          "right": ["1 OUTA o", None, None, "7 OUTB o", None, None, "8 OUTC o", None, None, "14 OUTD o",
                                    None, "4 V+ pi", "11 V- pi"]}),
    # TLV3502 SBOS321E p3 Pin Functions (D SOIC-8), push-pull outputs
    "TLV3502": dict(mfr=TI, mpn="TLV3502AID", prefix="U", pkg="SOIC-8 (D)", ds=DS + "sensing/TLV3502.pdf",
                    desc="Dual 4.5 ns rail-to-rail comparator, push-pull outputs, 2.7-5.5 V",
                    pins={"left": ["1 +INA i", "2 -INA i", None, "3 +INB i", "4 -INB i"],
                          "right": ["7 OUTA o", None, "6 OUTB o", None, "8 V+ pi", "5 V- pi"]}),
    # LM4040 SNOS633N Table 4-1 p4 (DBZ: 1 cathode, 2 anode, 3 float or anode)
    "LM4040": dict(mfr=TI, mpn="LM4040AIM3-2.5/NOPB", prefix="D", pkg="SOT-23 (DBZ)", ds=DS + "sensing/LM4040.pdf",
                   desc="Shunt reference 2.500 V 0.1 % (0.75 % -40..85 C), 65 uA-15 mA",
                   pins={"left": ["1 K p"], "right": ["2 A p", "3 NC p"]}),
    # TPS3710 SBVS271A Table 4-1 p3 (DDC SOT-23-6: 1 OUT, 2/4/6 GND, 3 SENSE, 5 VDD); OUT low below UVLO (p5)
    "TPS3710": dict(mfr=TI, mpn="TPS3710DDCR", prefix="U", pkg="SOT-23-6 (DDC)", ds=DS + "power-supply/TPS3710.pdf",
                    desc="Voltage detector 400 mV 1 %, open-drain output, 1.8-18 V",
                    pins={"left": ["5 VDD pi", "3 SENSE i"],
                          "right": ["1 OUT oc", None, "2 GND pi", "4 GND pi", "6 GND pi"]}),
    # WeEn WND75P16W6 Rev.01 (21 Sep 2026) Table 2 p2 pinning: 1 K cathode, 2 A anode, mb mounting base = cathode
    # (drawn as pin 3 MB); limiting values Table 3 p3; IFSM vs tp Fig. 4 p4
    "WND75P16W6": dict(mfr="WeEn Semiconductors", mpn="WND75P16W6", prefix="D", pkg="TO247-2L (mb = cathode)",
                       ds=DS + "power-semiconductors/WND75P16W6.pdf",
                       desc="Rectifier 1600 V 75 A, IFSM 1050 A (10 ms), I2t 5513 A2s (film-bank reverse clamp)",
                       pins={"left": ["1 K p", "3 MB p"], "right": ["2 A p"]}),
    # LEM LA 150-P p4 Connection: + (+UC), M (measure output, IS -> RM -> 0 V), - (-UC); LEM prints no pin numbers:
    # 1/2/3 = + / M / - here. P = the insulated inductor lead through the 13.5 x 10 mm aperture (no pin on the part).
    "LA150P": dict(mfr="LEM", mpn="LA 150-P", prefix="CS", pkg="PCB mount 37 x 33 x 16 mm, aperture 13.5 x 10 mm",
                   ds=DS + "sensing/LA_150-P.pdf",
                   desc="Closed-loop Hall current transducer, IPN 150 A, IPM +/-212 A, 1:2000, tD90 < 0.5 us, basic 1000 V",
                   pins={"left": ["P P p"], "right": ["1 +UC pi", "2 M o", "3 -UC pi"]}),
    # RECOM RS3 p5 pinning (Dual): 1 -Vin, 2 +Vin, 3 CTRL (open = on, no low state allowed), 5 NC, 6 +Vout, 7 Com, 8 -Vout
    "RS3_2415D": dict(mfr="RECOM", mpn="RS3-2415D", prefix="PS", pkg="SIP8", ds=DS + "power-supply/RS3.pdf",
                      desc="DC/DC 3 W regulated, 18-36 V in, +/-15 V +/-100 mA (LA 150-P supply; isolation not used)",
                      pins={"left": ["2 +VIN pi", "1 -VIN pi", "3 CTRL i", "5 NC nc"],
                            "right": ["6 +VOUT po", "7 COM pi", "8 -VOUT po"]}),
    "UMF05": dict(mfr="SCHURTER", mpn="3405.0163.24", prefix="F", pkg="SMD 3 x 10.1 mm", stock=("Device", "Fuse"),
                  ds=DS + "protection/UMF250.pdf", desc="Fuse UMF 250 0.5 A quick-acting, 125 VDC, -55..125 C"),
    # OPA2322 SBOS538F p5 Pin Functions (D/DGK): 1 OUT A, 2 -IN A, 3 +IN A, 4 V-, 5 +IN B, 6 -IN B, 7 OUT B, 8 V+
    "OPA2322": dict(mfr=TI, mpn="OPA2322AIDGKR", prefix="U", pkg="VSSOP-8 (DGK)", ds=DS + "sensing/OPA4322.pdf",
                    desc="Dual RRIO CMOS op amp 20 MHz, 10 V/us, 1.8-5.5 V",
                    pins={"left": ["3 +INA i", "2 -INA i", None, "5 +INB i", "6 -INB i"],
                          "right": ["1 OUTA o", None, None, "7 OUTB o", None, "8 V+ pi", "4 V- pi"]}),
    # SN74LVC1G08 (Rev AA) p3 Pin Functions (DBV): 1 A, 2 B, 3 GND, 4 Y, 5 VCC
    "LVC1G08": dict(mfr=TI, mpn="SN74LVC1G08DBVR", prefix="U", pkg="SOT-23-5 (DBV)",
                    ds=DS + "isolation-interface/SN74LVC1G08.pdf", desc="Single 2-input AND gate, Ioff",
                    pins={"left": ["1 A i", "2 B i", "5 VCC pi", "3 GND pi"], "right": ["4 Y o"]}),
    # Rubycon ZLH catalog (docs/datasheets/passives-capacitors/Rubycon-ZLH.pdf) standard sizes: 50 V 100 uF 8x11.5,
    # 724 mA, 0.074 ohm at 20 C / 100 kHz, -40..105 C, +/-20 %; part number = voltage + ZLH + uF + M + EFC + case
    "CB100": dict(mfr="Rubycon", mpn="50ZLH100MEFC8X11.5", prefix="C", pkg="radial 8x11.5 mm",
                  stock=("Device", "C_Polarized"),
                  ds=DS + "passives-capacitors/Rubycon-ZLH.pdf", desc="Aluminium electrolytic 100 uF 20 % 50 V 105 C low Z"),
    # SN74LVC1G74 SCES794G p4 Pin Functions (DCU VSSOP-8: 1 CLK, 2 D, 3 /Q, 4 GND, 5 Q, 6 /CLR, 7 /PRE, 8 VCC)
    "LVC1G74": dict(mfr=TI, mpn="SN74LVC1G74DCUR", prefix="U", pkg="VSSOP-8 (DCU)",
                    ds=DS + "isolation-interface/SN74LVC1G74.pdf",
                    desc="D flip-flop with preset and clear, Ioff (fault latch)",
                    pins={"left": ["7 ~{PRE} i", "6 ~{CLR} i", "2 D i", "1 CLK i"],
                          "right": ["5 Q o", "3 ~{Q} o", None, "8 VCC pi", "4 GND pi"]}),
}
PARTS["AHCT08"] = dict(mfr=TI, mpn="SN74AHCT08DR", prefix="U", pkg="SOIC-14 (D)",   # SCLS237P Table 5-1 p3 (D)
                       ds=DS + "isolation-interface/SN74AHCT08.pdf",
                       desc="Quad 2-input AND gate, 5 V, TTL inputs (VIH 2.0 V): 3.3 V -> 5 V level for the NSI6651 inputs",
                       pins={"left": ["1 1A i", "2 1B i", "4 2A i", "5 2B i", "9 3A i", "10 3B i", "12 4A i", "13 4B i",
                                      None, "14 VCC pi", "7 GND pi"], "right": ["3 1Y o", "6 2Y o", "8 3Y o", "11 4Y o"]})
PARTS["AHCT1G08"] = dict(mfr=TI, mpn="SN74AHCT1G08DBVR", prefix="U", pkg="SOT-23-5 (DBV)",   # SCLS315S Table 4-1 p3
                         ds=DS + "isolation-interface/SN74AHCT1G08.pdf",
                         desc="Single 2-input AND gate, 5 V, TTL inputs: EN_DRV -> EN_GD at the NSI6651 RST/EN level",
                         pins={"left": ["1 A i", "2 B i", "5 VCC pi", "3 GND pi"], "right": ["4 Y o"]})
REUSED = dict({k: ctrl_c2000.PARTS[k] for k in ("J_CELL", "AM26LV31E", "AM26LV32E", "LVC11", "NETTIE")},
              **{k: port.PARTS[k] for k in ("STUD", "TNPV_124K", "LMR36015", "WE_L10U", "PE_BOND")})
REUSED["LMR36015"] = dict(REUSED["LMR36015"], desc="Buck 4.2-60 V in, 1.5 A, 400 kHz (cell logic 3.3 V / 5 V)")
# gen/data/alternates.csv ADOPT (DROP-IN for Wurth 61204021621): XKB X9555WV sheet p1, 2x20 2.54 mm shrouded THT, standard
# odd/even IDC numbering (pin 1 triangle) = the same Conn_02x20_Odd_Even symbol: the CELL contract pinout is unchanged
REUSED["J_CELL"] = dict(REUSED["J_CELL"], mfr="XKB Connection", mpn="X9555WV-2x20-6TV01", pkg="2x20 2.54 mm box header THT",
                        ds=DS + "connectors/XKB-X9555WV.pdf",
                        desc="Shrouded box header 2x20 2.54 mm, CELL contract (3 A, 250 V, -40..125 C)")
CATALOG = dict(catalog.PARTS, **gdrv.PARTS, **REUSED, **PARTS)


# ------------------------------------------------------------------------------------------------ helpers
def val(s):
    """'4.99k' / '6R' / '2.2u' -> float."""
    s = s.split()[0]
    return float(s[:-1]) * {"R": 1, "k": 1e3, "M": 1e6, "p": 1e-12, "n": 1e-9, "u": 1e-6}[s[-1]]


def r_ntc(t):
    """NTC R/R25 at t (C): log-linear in the B57703M R/T table, B-model beyond 155 C (inductor NTC rated 200 C)."""
    if t >= RT8016[-1][0]:
        return RT8016[-1][1] * math.exp(NTC["b"] * (1 / (t + 273.15) - 1 / (RT8016[-1][0] + 273.15)))
    for (t1, r1), (t2, r2) in zip(RT8016, RT8016[1:]):
        if t1 <= t <= t2:
            return r1 * (r2 / r1) ** ((t - t1) / (t2 - t1))
    raise ValueError(t)


def t_ntc(ratio):
    """Inverse of r_ntc by bisection over -40..200 C."""
    lo, hi = -40.0, 200.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if r_ntc(mid) > ratio else (lo, mid)
    return lo


def vid(a, b, rt, ia=0.0, ib=0.0):
    """V_A - V_B of a receiver pair: a/b = [(R, V source)] on each line, rt across the pair, ia/ib injected currents."""
    g = 1 / rt
    ga, gb = g + sum(1 / r for r, _ in a), g + sum(1 / r for r, _ in b)
    ja, jb = ia + sum(v / r for r, v in a), ib + sum(v / r for r, v in b)
    det = ga * gb - g * g
    return ((ja * gb + g * jb) - (ga * jb + g * ja)) / det


def e96(x):
    """Nearest E96 value."""
    d = 10 ** math.floor(math.log10(x))
    return float("%.3g" % min((round(10 ** (i / 96), 2) * d for i in range(97)), key=lambda v: abs(math.log(v / x))))


# ------------------------------------------------------------------------------------------------ design inputs
POS = {"S1": ("A_BUS+", "SW_A", 2), "S2": ("SW_A", "BUS-", 1),       # switch: drain, power source, complementary PWM
       "S3": ("B_BUS+", "SW_B", 4), "S4": ("SW_B", "BUS-", 3)}       # PWMk of interfaces.CELL commands Sk
N_PAR = spec("switch_positions", 0, "parallel")
R_GATE = (spec("switch_positions", 0, "gate_resistor_on_ohm"), spec("switch_positions", 0, "gate_resistor_off_ohm"))
GATE_V = (spec("gate_drive", "V_GS_on_V"), -spec("gate_drive", "V_GS_off_V"))   # a gdrv.GATE_V key, or gdrv refuses it
DEC_N, DEC_MPN = spec("decoupling", "count_per_leg"), spec("decoupling", "mpn")
I_TRIP = spec("protection", "inductor_overcurrent_hw_trip_A")
T_OT = spec("protection", "heatsink_overtemperature_trip_C")
V_OVP = spec("protection", "overvoltage_hw_trip_port_A_V")
F_SW = spec("switching", "f_sw_kHz") * 1e3
DVDT_MAX = spec("device_primary", "turn_on_dvdt_V_per_ns")

# Values drawn on this board; the ones that implement a spec number are computed from it (E96) and asserted
RM = 20.0                            # LA 150-P measuring resistor: V_M = RM x IP / 2000 = 10 mV/A (<= 30 ohm: +/-212 A)
K_M, K_AN1 = RM / LA150["kn"], 2 * RM / LA150["kn"]   # V/A at V_M (and AN1_P - VMID), V/A at the AN1 pair
I_LIN_REQ = 110.0                    # rev B brief: linear >= +/-110 A (CTRL backup trip 88 A peaks at 105-107 A)
CTRL_SPEC = json.load(open(os.path.join(L.REPO, "sim", "out", "pv_control", "control_spec.json")))
R_BIAS, R_TERM = 470.0, 120.0        # RS-422 fail-safe bias per line, termination
R_NTC_BIAS = 10.0e3                  # heatsink: 2.5 V - Rf - T_HS - NTC - AGND; 10 k so an open probe is told from -40 C
R_L_BIAS = 1.00e3                    # winding: 2.5 V - Rf - T_L - NTC - AGND (hot-end resolution; open = firmware check)
OPEN_DIV = (1.47e3, 100e3)           # REF-Ra-TH_OPEN-Rb-AGND: T_HS above TH_OPEN = probe open (or colder than ~-50 C)
OC_DIV = (e96(10e3 * (LM40["v"] / (2 * I_TRIP * K_M) - 1)), 10.0e3)   # REF-R1-TH_HI-R2-VMID-R2-TH_LO-R1-AGND
OT_DIV = (e96(10e3 * R_NTC_BIAS / (NTC["r25"] * r_ntc(T_OT))), 10.0e3)               # REF-Ra-TH_OT-Rb-AGND
OC_RC = (220.0, 1e-9)                # comparator input filter
UV_DIV = (499e3, 10.0e3)             # +24V-R1-SENSE-R2-GND, TPS3710
S15_DIV = (309e3, 10.0e3)            # +15V-R1-SENSE-R2-AGND, TPS3710 (LA 150-P works from +/-12 V -5 %)
DAMP = spec("damper")                # per leg: 2 elements in series, each DAMP_PAR x CRCW2512-HP in parallel; 2 x C0G
DAMP_SER, DAMP_PAR = 2, 3
DAMP_R = e96(DAMP["R_per_leg_ohm"] / DAMP_SER * DAMP_PAR)          # one CRCW2512-HP resistor
DAMP_C = (DAMP["C_per_leg_nF"] * 1e-9 * 2, 2)                       # value, number in series
# Vishay CRCW-HP e3 (doc 20043) p1 + p3 part numbering: CRCW2512 + value + F (1 %) + K (100 ppm/K) + EG (E67) + HP
CATALOG["R_DAMP"] = dict(mfr="Vishay", mpn="CRCW2512%sFKEGHP" % ("%.1f" % DAMP_R).replace(".", "R"), prefix="R",
                         pkg="2512", stock=("Device", "R"), ds=DS + "passives-capacitors/CRCW-HP.pdf",
                         desc="Pulse-proof thick film 2512 %.1f ohm 1 %%, 1.5 W at 70 C, 500 V (leg damper)" % DAMP_R)
BLEED = 8                            # TNPV1210 124k elements per bank
L_BUS_REQ = LEG["Lbus"]              # layout requirement: bulk bank to leg loop = the leg deck's Lbus


# ------------------------------------------------------------------------------------------------ design check (1/2)
# Assembly variant (rev C item 2): Microchip MSC035SMA170B4 x 2 at +20/-4 V on this schematic, BOM bom/PVCELL-25_MSC_BOM.csv.
# HOOK ONLY: gdrv rev 5 cannot drive it (AO3400A clamp FET at the 24 V rail span, booster not sized for this device);
# gdrv rev 6 brings the 40 V-class clamp FET, the Microchip booster value and its Miller run (r_sb None = from gdrv).
VARIANT_MSC = dict(mpn="MSC035SMA170B4", mfr="Microchip Technology", ds=DS + "power-semiconductors/MSC035SMA170B4.pdf",
                   gate_v=(20, 4), r_gate=(6.0, 4.0), r_sb=None, vclass=1700, bom="bom/PVCELL-25_MSC_BOM.csv")


def calc():
    """Numbers behind the schematic: asserted against cell_spec.json and the datasheets. Returns ({key: line}, N)."""
    out, N = {}, {}
    say = lambda k, s, *a: out.__setitem__(k, s % a)
    assert abs(t_ntc(r_ntc(87.0)) - 87.0) < 0.01 and r_ntc(25) == 1.0 and e96(4019.0) == 4020.0, "helper self-check"
    assert abs(vid([(1e3, 1.0)], [(1e3, 0.0)], 1e3) - 1 / 3) < 1e-12, "helper self-check"

    # ---- power devices vs the spec stresses
    pos = spec("switch_positions")
    assert [(p["name"], p["leg"]) for p in pos] == [("S1", "A"), ("S2", "A"), ("S3", "B"), ("S4", "B")], "switch map"
    for p in pos:
        assert p["mpn"] == PARTS["SG2M040170HJ"]["mpn"] and p["parallel"] == N_PAR, "device / parallel count drift"
        assert (p["gate_resistor_on_ohm"], p["gate_resistor_off_ohm"]) == R_GATE, "gate resistors differ per position"
    st = spec("worst_case_stresses")
    assert st["device_VDS_rating_V"] == DEV["vdss"], "spec V_DSS != datasheet"
    assert st["device_VDS_peak_V"] <= 0.85 * DEV["vdss"] and st["device_VDS_continuous_max_V"] <= 0.67 * DEV["vdss"]
    assert st["device_Irms_per_device_A"] <= DEV["id100"] and st["device_I_turnoff_max_per_device_A"] <= DEV["idm"]
    assert max(st["Tj_max_45C_C"], st["Tj_max_60C_full_power_C"]) <= 125.0 <= DEV["tj"]
    say("dev", "Devices %d x 2 x SG2M040170HJ: V_DS peak %.0f V = %.2f x V_DSS (<= 0.85), continuous %.0f V = %.2f x "
        "(<= 0.67); %.1f A rms per device vs I_D(100 C) %.0f A; turn-off %.1f A vs IDM %.0f A; Tj %.0f C (45 C) / %.0f C "
        "(60 C) <= 125 C", len(pos), st["device_VDS_peak_V"], st["device_VDS_peak_V"] / DEV["vdss"],
        st["device_VDS_continuous_max_V"], st["device_VDS_continuous_max_V"] / DEV["vdss"], st["device_Irms_per_device_A"],
        DEV["id100"], st["device_I_turnoff_max_per_device_A"], DEV["idm"], st["Tj_max_45C_C"], st["Tj_max_60C_full_power_C"])

    # ---- gate drive: gdrv rev 5 owns the channel and asserts its preset; here: preset = this board, rails, DESAT, dead time
    if GATE_V not in gdrv.GATE_V:
        raise SystemExit("cell_spec gate rail %s: %s" % (GATE_V, gdrv.REFUSED.get(GATE_V, "not a gdrv.GATE_V rail")))
    vt, v3, v2 = gdrv.rails(GATE_V)
    gv = gdrv.GATE_V[GATE_V]
    sc = gdrv.SC40                                         # gdrv's preset of exactly this device set
    assert (sc["name"], sc["gate_v"], sc["vclass"], sc["n"], sc["r_gate"], sc["r_sb"], sc["fsw"][0]) == \
        ("%d x %s" % (N_PAR, DEV["mpn"]), GATE_V, VCLASS, N_PAR, R_GATE, R_SB, F_SW), "channel args != gdrv preset"
    with contextlib.redirect_stdout(io.StringIO()):
        g_out, _ = gdrv.design_check()                     # asserts bias, peak current, DESAT, booster, Miller hold
    gline = lambda key: g_out[key + sc["name"]].strip()
    near = lambda x, y, tol: all(abs(a - b) <= tol for a, b in zip(x, y))
    on_w, off_w = spec("gate_drive", "V_GS_on_range_V"), spec("gate_drive", "V_GS_off_range_V")
    assert DEV["vgs"][0] + 3.0 <= -v3[1] and v2[1] <= DEV["vgs"][1] - 3.0, "gate rails within 3 V of the VGS abs max"
    assert near(v2, on_w, 0.01) and near((-v3[1], -v3[0]), off_w, 0.01), "VGS ranges != cell_spec (= gdrv rails)"
    (tr_lo, tr_hi), tr_nom, (bl_lo, bl_hi) = gdrv.desat_numbers(gdrv.DESAT_CLASS[VCLASS])
    assert near((NSI["vdesat"][0], NSI["vdesat"][2]), spec("protection", "desat_threshold_range_V"), 0.01) and \
        abs(NSI["vdesat"][1] - spec("protection", "desat_VDS_threshold_V")) <= 0.01, "DESAT pin threshold != cell_spec"
    v_on = spec("device_primary", "drive", "vds_on_at_trip_175C_V")
    assert tr_lo >= 1.5 * v_on, "DESAT trip below 1.5 x V_DS(on) at the trip current, 175 C"
    assert sc["t_resp"] <= spec("gate_drive", "short_circuit", "required_detect_to_off_us_max") * 1e-6
    cmti = min(NSI["cmti"], UCC14["cmti"])
    assert cmti >= 1.2 * DVDT_MAX, "driver / bias CMTI below 1.2 x switch-node dv/dt"
    say("gate", "Gate drive (gdrv rev 5 preset '%s' = this board): NSI6651ASC-Q1 + UCC14241-Q1 +%g/-%g V: VGS on "
        "%.2f-%.2f V, off -%.2f..-%.2f V = cell_spec, vs "
        "SG2M040170HJ -8/+22 V abs, -4/+18 V op (p3); Ron/Roff %g/%g ohm + %g ohm Kelvin R per device; DESAT pin "
        "%.2f/%.2f/%.2f V = cell_spec, trip at V_DS %.2f-%.2f V vs V_DS(on) %.2f V at the trip current, 175 C (x%.1f); "
        "blanking %.0f-%.0f ns; CMTI %.0f V/ns vs %.0f V/ns turn-on dv/dt (cell_spec) = x%.2f (THIN)",
        sc["name"], GATE_V[0], GATE_V[1], v2[0], v2[1], v3[0], v3[1],
        R_GATE[0], R_GATE[1], gdrv.R_KS, NSI["vdesat"][0], NSI["vdesat"][1], NSI["vdesat"][2], tr_lo, tr_hi, v_on,
        tr_lo / v_on, bl_lo * 1e9, bl_hi * 1e9, cmti, DVDT_MAX, cmti / DVDT_MAX)
    for k in ("ipk", "sc", "boost", "mclamp"):
        say("gd_" + k, "  gdrv: %s", gline(k))
    say("duty", "100 %% duty: no bootstrap anywhere - each channel has its own UCC14241-Q1 isolated DC/DC (continuous, "
        "SLUSF09A) and the NSI6651 output follows IN+ with no on-time limit (functional modes 8.12 p28); buck keeps S3 "
        "and boost keeps S1 on indefinitely, DESAT stays armed while on")
    vm = VARIANT_MSC
    vt_m = gdrv.rails(vm["gate_v"])[0]
    gv_m = gdrv.GATE_V[vm["gate_v"]]
    say("variant", "Variant %s x %d at +%g/-%g V (gate %g/%g ohm; bias dividers %s/%s and %s/%s instead of %s/%s and %s/%s; "
        "DESAT class %d as built): NOT BUILT (stopped, D-044) - with gdrv rev 6 the clamp FET sees VDD-VEE %.2f-%.2f V vs "
        "0.8 x %.0f V = %.1f V; build, Miller run and BOM still to do (hook VARIANT_MSC)",
        vm["mpn"], N_PAR, vm["gate_v"][0], vm["gate_v"][1], vm["r_gate"][0], vm["r_gate"][1], gdrv.ohm(gv_m["fbvdd"][0]),
        gdrv.ohm(gv_m["fbvdd"][1]), gdrv.ohm(gv_m["fbvee"][0]), gdrv.ohm(gv_m["fbvee"][1]), gdrv.ohm(gv["fbvdd"][0]),
        gdrv.ohm(gv["fbvdd"][1]), gdrv.ohm(gv["fbvee"][0]), gdrv.ohm(gv["fbvee"][1]), vm["vclass"], vt_m[0], vt_m[1],
        gdrv.AO["vds"], 0.8 * gdrv.AO["vds"])
    t_lo, t_hi = stretch5(DT_R, 100e-12)
    skew = NSI["skew"] + AHCT["tpd"][1] - AHCT["tpd"][0]   # driver tprop spread + PWD, AHCT08 gate-to-gate
    dt = ((t_lo + LVC17_5V["tpd"][0] - skew) * 1e9, (t_hi + LVC17_5V["tpd"][1] + skew) * 1e9)
    w = spec("switching", "dead_time_at_gates_ns")
    assert w[0] <= dt[0] and dt[1] <= w[1], "hardware dead time outside cell_spec's window"
    t_pmin = spec("switching", "min_pulse_us")
    assert abs(t_pmin - (0.6 + 2 * w[1] * 1e-3)) <= 0.002, "min pulse != 600 ns + 2 x dead time max"
    assert abs(spec("switching", "D_max") - (1 - t_pmin * 1e-6 * F_SW)) <= 0.001, "D_max != 1 - t_min x f_sw"
    say("dt", "Dead time at the gates: interlock stretch %s/100p at +5V (SN74LVC1G17 VT- 5 V rows, NSI6651 skew %.0f ns, "
        "AHCT08 %.0f ns) = %.0f-%.0f ns inside cell_spec %g-%g ns (gdrv: 3.4k at 3.3 V = 181-521 ns, the AO3400A clamps "
        "on <= 85 ns after turn-off); firmware DT >= %.0f ns is never extended; "
        "min pulse 0.6 + 2 x %.3f = %.3f us, D_max 1 - %.3f us x %.0f kHz = %.4f = cell_spec", gdrv.ohm(DT_R),
        NSI["skew"] * 1e9, (AHCT["tpd"][1] - AHCT["tpd"][0]) * 1e9, dt[0], dt[1], w[0], w[1],
        (t_hi + LVC17_5V["tpd"][1]) * 1e9, w[1] * 1e-3, t_pmin, t_pmin, F_SW / 1e3, spec("switching", "D_max"))

    # ---- 24 V budget: gate bias from first principles (gdrv rail data), logic from datasheet maxima
    qg_f = spec("gate_drive", "Q_g_per_device_nC") * 1e-9 * N_PAR * F_SW
    p_gate = qg_f * vt[1]
    p_spec = spec("device_primary", "drive", "gate_power_W_per_channel")
    assert abs(p_gate - p_spec) <= 0.05 * p_gate, "gate power != spec"
    p_rlim = gdrv.rlim_check(GATE_V, v2[1], v3[1], qg_f, N_PAR)[7]
    p_bias = (p_gate + NSI["icc2"] * vt[1] + NSI["ichg"][2] * v2[1] + N_PAR * v2[1] ** 2 / 10e3   # = gdrv rev 5 terms
              + vt[1] ** 2 / sum(gv["fbvdd"]) + v3[1] ** 2 / sum(gv["fbvee"]) + p_rlim)
    i_bias = UCC14["i_nl"] + (UCC14["i_fl"] - UCC14["i_nl"]) * p_bias / UCC14["p_fl"]
    vcc = 3.315 * 1.05
    i_ref = (vcc - LM40["v"] * (1 - LM40["tol"])) / (120.0 * 0.99)
    # 2 receivers, FLT driver into the CTRL termination (25 mA, ASSUMED), 6 op amps + the AN1 bias load (2 x 2.4 mA),
    # 4 comparators, reference, 5 idle bias networks, RDY / DRV_FLT_N pull-ups
    i_3v3 = (2 * RX["icc"] + 0.025 + 6 * OPA["iq"] + 2 * 2.4e-3 + 4 * TLV["iq"] + i_ref
             + 5 * vcc / (2 * R_BIAS + R_TERM) / 0.99)
    # +5V: 4 x NSI6651 ICC1 (p6 max), AHCT08 + AHCT1G08 ICC + delta-ICC with all 10 inputs at 3.3 V, 4 PWM pull-downs and
    # 4 PG pull-ups (10k) in the channels, RDY (2.00k) and DRV_FLT_N (4.99k) pull-ups
    i_5v = 4 * NSI["icc1"] + 2 * AHCT["icc"] + 10 * AHCT["dicc"] + 8 * V5[1] / 10e3 + V5[1] / 2e3 + V5[1] / 4.99e3
    p_15 = RS3["vout"] * (2 * LA150["ic"] + I_LIN_REQ / LA150["kn"])     # LA 150-P at the linear limit, one rail
    i_s15 = RS3["iq"] + p_15 / RS3["eff"] / UCC14["vin"][0]
    i_24 = 4 * i_bias + (vcc * i_3v3 + V5[1] * i_5v) / 0.70 / UCC14["vin"][0] + i_s15
    assert i_24 <= IF.CELL_24V_MAX_A, "+24V current above the CELL contract"
    v24_cell = V24[0] - i_24 * (0.150 + 0.105)
    assert v24_cell >= max(UCC14["vin"][0], UCC14["uv_rise"][1]), "+24V at the cell below the UCC14241-Q1 window"
    N.update(i_24=i_24, p_bias=p_bias, i_bias=i_bias, i_3v3=i_3v3, i_5v=i_5v)
    say("p24", "+24V: bias %.2f W per channel (gate %.3f W at VDD-VEE max, spec %.3f W) -> %.0f mA per "
        "UCC14241-Q1 (linear between the p8 maxima 35 mA / 250 mA at 1.5 W; Fig 10-20 typ ~75 mA); logic %.0f mA at 3.3 V "
        "+ %.0f mA at 5 V (datasheet maxima, bucks 70 %% assumed); LA 150-P supply %.0f mA at 110 A -> %.2f A total <= "
        "%.1f A (interfaces.CELL_24V_MAX_A); %.2f V at the cell (21.6 V - eFuse - 0.5 m cable) >= UCC14241-Q1 VIN min / "
        "UVLO rising max %.0f V", p_bias, p_gate, p_spec, i_bias * 1e3, i_3v3 * 1e3, i_5v * 1e3, i_s15 * 1e3, i_24,
        IF.CELL_24V_MAX_A, v24_cell, UCC14["uv_rise"][1])
    r1, r2 = UV_DIV
    fall = (TPS37["vit_f"][0] * (1 + r1 * 0.999 / (r2 * 1.001)), TPS37["vit_f"][1] * (1 + r1 * 1.001 / (r2 * 0.999)))
    rise = (TPS37["vit_r"][0] * (1 + r1 * 0.999 / (r2 * 1.001)), TPS37["vit_r"][1] * (1 + r1 * 1.001 / (r2 * 0.999)))
    assert fall[0] >= UCC14["uv_fall"][1] + 0.5 and rise[1] <= v24_cell - 0.5, "TPS3710 window vs bias UVLO / 24 V"
    N.update(uv_fall=fall, uv_rise=rise)
    r1, r2 = S15_DIV
    s15 = (TPS37["vit_f"][0] * (1 + r1 * 0.999 / (r2 * 1.001)), TPS37["vit_f"][1] * (1 + r1 * 1.001 / (r2 * 0.999)))
    s15r = TPS37["vit_r"][1] * (1 + r1 * 1.001 / (r2 * 0.999))
    assert s15[0] >= 12.0 * 0.95 and s15r <= RS3["vout"] * (1 - RS3["acc"]) - 0.5, "+15 V monitor window"
    N["s15_fall"] = s15
    r1, r2 = UV_DIV
    say("uv", "+24V supervisor TPS3710 (%s/%s 0.1 %%): RDY low below %.2f-%.2f V, released above %.2f-%.2f V; UCC14241-Q1 "
        "UVLO falling %.1f-%.1f V, so every driver is disabled (gates at VEE) before its bias collapses",
        gdrv.ohm(r1), gdrv.ohm(r2), fall[0], fall[1], rise[0], rise[1], *UCC14["uv_fall"])

    # ---- RS-422 fail-safe: open / unpowered pair must read LOW (gate off), a driven pair must read cleanly
    vlo = 3.315 * 0.95
    rb, rt, ri = R_BIAS * 1.01, R_TERM * 0.99, RX["ri"]
    open_ = vid([(rb, 0.0), (ri, vlo)], [(rb, vlo), (ri, 0.0)], rt)
    unpowered = vid([(rb, 0.0), (ri, vlo)], [(rb, vlo), (ri, 0.0)], rt, TX["ioff"], -TX["ioff"])
    r_oh, r_ol = (vlo - TX["voh"]) / TX["i_test"], TX["vol"] / TX["i_test"]
    rb_ = R_BIAS * 0.99
    high = vid([(r_oh, vlo), (rb_, 0.0), (ri, 0.0)], [(r_ol, 0.0), (rb_, vcc), (ri, vcc)], rt)
    assert max(open_, unpowered) <= -RX["vit"] - 0.05 and high >= RX["vit"] + 1.0, "RS-422 fail-safe bias"
    N.update(vid_open=open_, vid_unpowered=unpowered, vid_high=high)
    say("failsafe", "RS-422 pair (%s to GND on _P, %s to 3V3 on _N, %s across; ri 4 k to the adverse rail, 3V3 -5 %%): "
        "open %.3f V, CTRL unpowered (AM26LV31E IO(OFF) 100 uA adverse) %.3f V, both < VIT- -0.2 V -> LOW = gate off; "
        "driven high %.2f V >= +0.2 V. RDY_CELL push-pull (SN74LVC1G17); dead board = Ioff into the card's 100 k "
        "pull-down = not ready (INT-14)", gdrv.ohm(R_BIAS), gdrv.ohm(R_BIAS), gdrv.ohm(R_TERM), open_, unpowered, high)

    # ---- inductor current sensor: LA 150-P around the screened, insulated inductor lead; RM; differential driver
    cc = CTRL_SPEC["measurement_requirements"]["cell_inductor_current"]
    bk = CTRL_SPEC["hardware_trips"]["inductor_overcurrent"]["ctrl_backup"]
    didt = CTRL_SPEC["hardware_trips"]["inductor_overcurrent"]["didt_max_A_per_us"] * 1e6
    i_sat = IND["I_at_50pct_L0_A"]
    vmid = (LM40["v"] * (1 - LM40["tol"]) / 2 * 0.999, LM40["v"] * (1 + LM40["tol"]) / 2 * 1.001)
    lin = min(LA150["ipm"], (vmid[0] - OPA2["swing"]) / K_M, (3.315 * 0.95 - OPA2["swing"] - vmid[1]) / K_M)
    assert RM <= LA150["rm_max212"] and lin >= max(I_LIN_REQ, bk["peak_A"], max(abs(x) for x in cc["range_A"])), \
        "AN1 linear range below +/-110 A / the CTRL backup peak"
    bw3 = LA150["bw_1db"] / math.sqrt(10 ** 0.1 - 1)                   # first-order -3 dB from the -1 dB point
    t_drv = 1 / (2 * math.pi * 20e6 / 2)                               # OPA4322 / OPA2322 at noise gain 2
    t_sense = LA150["td90"] + t_drv
    t_bk = t_sense + (bk["max_response_us"] - cc["group_delay_us_max"]) * 1e-6   # + CTRL AFE + CMPSS chain
    pk_bk = bk["band_A"][1] + didt * t_bk
    assert bw3 >= cc["bandwidth_kHz_min"] * 1e3 and t_sense <= cc["group_delay_us_max"] * 1e-6, "sensor BW / delay"
    assert pk_bk <= i_sat, "CTRL backup trip peak above the 50 %-L0 current"
    lsb = 2.5 / 4096 / (K_AN1 * 0.402)                                 # CTRL-C2000 AFE gain 0.402, 12 bit, 2.5 V
    assert lsb <= cc["resolution_A_per_LSB"] * 1.001, "AN1 resolution at the CTRL ADC"
    i_drv = max((vmid[1] + K_M * lin) / 1.50e3, 3.315 * 1.05 / 1.50e3)  # CTRL open-input bias 1.50 k per line
    assert i_drv <= OPA2["i_load"], "AN1 drivers loaded beyond the 0.1 V swing assumption"
    i_rail = LA150["ic"] + lin / LA150["kn"]
    assert RS3["vout"] * (1 - RS3["acc"]) >= LA150["uc"][0] and i_rail <= RS3["iout"], "LA 150-P supply"
    assert IND["I_rms_max_A"] <= 150.0 and LA150["basic"] >= 1000.0, "LA 150-P current / basic insulation"
    off = (LA150["io"][1] + LA150["io"][2]) * LA150["kn"]
    # RS3-2415D thermal load (temp audit): average output power at the highest continuous inductor current (45 A) vs
    # the p4 curve at the board temperature; the graph's "output load" is the module's 3 W (both rails together)
    i_avg = spec("protection", "current_limit_avg_A")
    p_avg = RS3["vout"] * (2 * LA150["ic"] + i_avg / LA150["kn"] + RS3["vout"] / sum(S15_DIV))
    (t1, f1), (t2, f2) = RS3["derate"]
    f_ok = f1 + (f2 - f1) * (T_BOARD - t1) / (t2 - t1)
    frac = p_avg / (2 * RS3["vout"] * RS3["iout"])
    assert T_BOARD <= t2 and frac <= f_ok, "RS3-2415D above its derating curve at the board temperature"
    say("rs3", "+/-15 V RS3-2415D at a %.0f C board (ASSUMED): derating curve p4 allows %.0f %% of 3 W (full load only to 71 C; "
        "85 C is the end of its range, no headroom above); load %.2f W average at %.0f A (LA 150-P IC 2 x %.0f mA + IS "
        "%.1f mA, monitor) = %.1f %% -> x%.2f; the + rail alone carries %.1f mA = %.0f %% of its 100 mA; the 110 A transient "
        "(%.0f mA) lasts us-ms (not thermal)", T_BOARD, f_ok * 100, p_avg, i_avg, LA150["ic"] * 1e3,
        i_avg / LA150["kn"] * 1e3, frac * 100, f_ok / frac, (LA150["ic"] + i_avg / LA150["kn"]) * 1e3,
        (LA150["ic"] + i_avg / LA150["kn"]) / RS3["iout"] * 100, i_rail * 1e3)
    N.update(lin=lin, t_sense=t_sense, pk_bk=pk_bk, lsb=lsb, bw3=bw3, i_drv=i_drv, off=off)
    say("isense", "AN1 = LEM LA 150-P (closed-loop Hall, 1:2000) around inductor lead A + RM %s 0.1 %% + OPA4322/OPA2322 "
        "driver: %.1f mV/A differential, CM %.2f V, source < 1 ohm (closed loop, RO 90 ohm open loop) -> CTRL %.4f A/LSB "
        "(req %.4f); linear +/-%.0f A (driver swing; LA 150-P +/-%.0f A) vs >= %.0f A and the CTRL backup peak %.1f A; "
        "BW -1 dB 150 kHz -> -3 dB %.0f kHz first-order (INFERRED, LEM gives no -3 dB) vs %.0f; delay tD90 0.5 + driver "
        "%.2f = %.2f us vs %.2f us -> CTRL backup (%.1f A + %.1f A/us x %.2f us) peak %.1f A vs 50 %%-L0 %.0f A "
        "(AMC3302 was %.0f A); drives the CTRL 1.50 k bias with <= %.1f mA; +/-15 V rails %.0f mA <= 100 mA (RS3-2415D). "
        "dv/dt: no isolation data link; the lead's earthed screen keeps the switch-node %.0f V/ns off the transducer "
        "(no datasheet dv/dt figure - BENCH TEST); insulation = lead (supplementary, CUSTOM spec) + LA 150-P basic 1000 V. "
        "Offset after a zero at idle: IOM + IOT max %.1f A (typ IOT 0.10 mA = 0.2 A) vs 0.3 A required - PARTIAL "
        "(firmware re-zero at every idle)", gdrv.ohm(RM),
        K_AN1 * 1e3, sum(vmid) / 2, lsb, cc["resolution_A_per_LSB"], lin, LA150["ipm"], I_LIN_REQ, bk["peak_A"], bw3 / 1e3,
        cc["bandwidth_kHz_min"], t_drv * 1e6, t_sense * 1e6, cc["group_delay_us_max"], bk["band_A"][1], didt / 1e6,
        t_bk * 1e6, pk_bk, i_sat, bk["peak_A_amc3302_as_drawn"], i_drv * 1e3, i_rail * 1e3, DVDT_MAX, off)

    # ---- OC window comparator on AN1_P = VMID + RM x IP / 2000: threshold tolerance and response time
    a, b = OC_DIV
    v_th = LM40["v"] * b / (2 * (a + b))
    i_nom = v_th / K_M
    gain_err = (LM40["tol"] + 0.002 + LA150["err"] + LA150["lin"] + 0.001 + 25e-6 * 60 + 0.002)
    offs = 2 * OPA["vos"] + TLV["vos"] + sum(LA150["io"]) * RM
    i_lo, i_hi = (v_th - offs) / K_M / (1 + gain_err), (v_th + offs) / K_M * (1 + gain_err)
    i_pk = IND["I_peak_normal_max_A"]
    assert abs(i_nom - I_TRIP) <= 0.005 * I_TRIP, "OC threshold != spec trip"
    assert i_lo >= 1.05 * i_pk and i_hi < bk["band_A"][0], "OC trip vs the normal peak / the CTRL backup band"
    slope = max(V_OVP / (IND["L_at_trip_uH"] * 1e-6), didt)
    t_rc = OC_RC[0] * OC_RC[1]
    t_det = t_sense + t_rc + TLV["tpd"] + 3 * T_LOGIC
    t_pwm = t_det + T_LOGIC + T_DRV + DEV["t_off"]
    t_en = t_det + NSI["t_rst"][1] + T_DRV + DEV["t_off"]
    t_req = 0.3 * I_TRIP / slope
    i_off = i_hi + slope * t_pwm
    assert t_pwm <= t_req and t_en <= t_req, "local OC trip slower than the spec allows"
    assert i_off <= min(lin, i_sat, 1.3 * I_TRIP), "current at turn-off outside the sensor / inductor limits"
    N.update(i_trip=(i_nom, i_lo, i_hi), t_pwm=t_pwm, t_en=t_en, t_req=t_req, i_off=i_off)
    say("oc", "OC window (TLV3502 on AN1_P, thresholds VMID +/- %.4f V from %s/%s): +/-%.1f A nominal = spec %.1f A, "
        "%.1f-%.1f A with every tolerance (ref 0.75 %%, LEM 0.5 %% + 0.1 %%, RM, networks, offsets %.1f mV incl. LEM "
        "0.65 mA) vs normal peak %.1f A (x%.2f) and below the CTRL backup band %.1f A. Response: LA 150-P + driver %.2f + "
        "RC %.2f + logic -> PWM gated, gate off %.2f us; EN path (TRSTFIL 0.8 us) %.2f us; allowed %.2f us (30 %% "
        "sensing margin at %.1f A/us) -> %.1f A at turn-off", v_th, gdrv.ohm(a), gdrv.ohm(b), i_nom, I_TRIP, i_lo, i_hi,
        offs * 1e3, i_pk, i_lo / i_pk, bk["band_A"][0], t_sense * 1e6, t_rc * 1e6, t_pwm * 1e6, t_en * 1e6, t_req * 1e6,
        slope / 1e6, i_off)

    # ---- heatsink OT comparator and the two 0-2.5 V temperature channels
    ra, rb_ot = OT_DIV
    ratio = R_NTC_BIAS * rb_ot / ra / NTC["r25"]
    t_nom = t_ntc(ratio)
    corners = []
    for kn in (1 - NTC["tol"], 1 + NTC["tol"]):
        for kv in (-1, 1):
            v = LM40["v"] * rb_ot / (ra + rb_ot) + kv * TLV["vos"]
            rr = R_NTC_BIAS * v / (LM40["v"] - v) / (NTC["r25"] * kn)
            t0 = t_ntc(rr)
            kb = math.exp(NTC["b_tol"] * NTC["b"] * abs(1 / (t0 + 273.15) - 1 / 298.15))
            corners += [t_ntc(rr * kb), t_ntc(rr / kb)]
    # band limit: the upper corner must stay inside the spec's 5 K margin (heatsink_trip_basis) with 1 K to spare
    assert abs(t_nom - T_OT) <= 0.5 and T_OT - 4.0 <= min(corners) and max(corners) <= T_OT + 4.0, "OT threshold"
    v_t = lambda t, rf=R_NTC_BIAS, k=1.0: LM40["v"] * NTC["r25"] * k * r_ntc(t) / (rf + NTC["r25"] * k * r_ntc(t))
    hs, lt = spec("sensing", "heatsink_temperature", "range_C"), spec("sensing", "inductor_temperature", "range_C")
    assert v_t(min(hs[0], lt[0])) <= 3.0 and v_t(max(hs[1], lt[1]), R_L_BIAS) >= 0.0, "AN2/AN3 outside 0-3 V"
    # open-probe window (PVR-08): coldest valid reading (-40 C, NTC +2 %, B +1 %) < TH_OPEN band < open (= 2.5 V)
    kb40 = math.exp(NTC["b_tol"] * NTC["b"] * (1 / 233.15 - 1 / 298.15))
    v_cold = v_t(hs[0], k=(1 + NTC["tol"]) * kb40)
    th_open = LM40["v"] * OPEN_DIV[1] / sum(OPEN_DIV)
    band = (th_open * (1 - 0.002) - TLV["vos"], th_open * (1 + 0.002) + TLV["vos"])
    assert v_cold < band[0] - 0.01 and band[1] < LM40["v"] - 0.01, "open-NTC window overlaps -40 C or the open level"
    t_lock = t_ntc(R_NTC_BIAS * band[0] / (LM40["v"] - band[0]) / NTC["r25"])
    i_ref_min = (3.315 * 0.95 - LM40["v"] * (1 + LM40["tol"])) / (120.0 * 1.01)
    i_ref_load = (LM40["v"] / (R_NTC_BIAS + NTC["r25"] * r_ntc(hs[1])) + LM40["v"] / (R_L_BIAS + NTC["r25"] * r_ntc(lt[1]))
                  + LM40["v"] / (2 * sum(OC_DIV)) + LM40["v"] / sum(OT_DIV) + LM40["v"] / sum(OPEN_DIV))
    i_ref_max = (3.315 * 1.05 - LM40["v"] * (1 - LM40["tol"])) / (120.0 * 0.99)
    assert i_ref_min >= i_ref_load + LM40["irmin"] and i_ref_max <= LM40["imax"], "LM4040 operating current"
    p_self = (LM40["v"] / 2) ** 2 / min(R_NTC_BIAS, R_L_BIAS)
    N.update(t_ot=(t_nom, min(corners), max(corners)), an2=((hs[0], v_t(hs[0])), (hs[1], v_t(hs[1]))),
             i_ref=(i_ref_load, i_ref_max), open_win=(v_cold, band, t_lock))
    say("ot", "Heatsink OT: 2.5 V - %s - NTC B57703M - AGND vs %s/%s from the same reference: trips at %.1f C nominal "
        "(spec %.0f C), %.1f-%.1f C with NTC 2 %% / B 1 %% / comparator offset; AN2 %.2f V at %.0f C ... %.3f V at %.0f C, "
        "AN3 (1 k bias) %.3f V at %.0f C; OPEN PROBE (PVR-08): T_HS above %.3f-%.3f V (TLV3502 B, %s/%s) trips like OT; "
        "coldest valid reading %.3f V (-40 C, NTC +2 %%, B +1 %%), open 2.500 V, lock-out below about %.0f C; winding NTC "
        "open = firmware plausibility (AN3 = 2.5 V); NTC self-heating <= %.1f mW (%.1f K in still air); LM4040 "
        "%.1f-%.1f mA for a %.1f mA load + 65 uA. NOTE: probe category 125 C < spec range %.0f C",
        gdrv.ohm(R_NTC_BIAS), gdrv.ohm(ra), gdrv.ohm(rb_ot), t_nom, T_OT, min(corners), max(corners),
        v_t(hs[0]), hs[0], v_t(hs[1]), hs[1], v_t(lt[1], R_L_BIAS), lt[1], band[0], band[1], gdrv.ohm(OPEN_DIV[0]),
        gdrv.ohm(OPEN_DIV[1]), v_cold, t_lock, p_self * 1e3, p_self / NTC["dth"], i_ref_min * 1e3, i_ref_max * 1e3,
        i_ref_load * 1e3, hs[1])

    # ---- film banks, decoupling, damper, bleeders
    vmax = st["port_cap_V_max_V"]
    for name in ("port_A", "port_B"):
        c = spec("capacitors", name)
        assert c["mpn"] == PARTS["C4AQ_45U"]["mpn"] and abs(c["count"] * C45["c"] - c["C_total_uF"] * 1e-6) < 1e-9
        assert (c["V_rated_70C_V"], c["V_op_85C_V"]) == (C45["vndc"], C45["vop85"]) and vmax <= C45["vop85"]
        assert c["ripple_worst_Arms"] <= c["ripple_use_limit_Arms"] <= c["count"] * C45["irms"], "bank ripple"
    cnt = spec("capacitors", "port_A", "count")
    say("bank", "Port banks %d x C4AQUEW5450A3BJ each = %.0f uF: %.0f V max vs VOP85 %.0f V (85 C hot spot) / VNDC %.0f V; "
        "ripple %.1f A rms <= use limit %.1f <= %d x %.0f A", cnt, spec("capacitors", "port_A", "C_total_uF"), vmax,
        C45["vop85"], C45["vndc"], spec("capacitors", "port_A", "ripple_worst_Arms"),
        spec("capacitors", "port_A", "ripple_use_limit_Arms"), cnt, C45["irms"])
    dc = spec("decoupling")
    assert DEC_MPN == PARTS["C4AQ_2U2"]["mpn"] and vmax <= C22["vop85"] == dc["V_op_85C_V"], "decoupling part / voltage"
    assert dc["Ipkr_per_cap_A"] == C22["ipkr"] and abs(C22["esl"] / DEC_N - dc["ESL_per_leg_nH"] * 1e-9) < 0.1e-9
    assert 1.2 * dc["peak_current_per_cap_A"] <= C22["ipkr"] and dc["peak_current_per_cap_with_recovery_A"] <= C22["ipkr"]
    assert dc["rms_per_cap_A"] <= C22["irms"] and C22["esl"] / DEC_N <= 8.5e-9, "decoupling rms / ESL (spec basis 8.5 nH)"
    say("dec", "Leg decoupling %d x %s (cell_spec, PVR-03): %.1f nH per leg (<= 8.5); peak %.1f A per capacitor (x1.2 = "
        "%.1f) and %.1f A with recovery vs Ipkr %.0f A (C4AQ p14); %.2f A rms vs %.1f A; bulk bank <= %.0f nH from the leg "
        "(leg deck Lbus, LAYOUT REQUIREMENT)", DEC_N, DEC_MPN, C22["esl"] / DEC_N * 1e9, dc["peak_current_per_cap_A"],
        1.2 * dc["peak_current_per_cap_A"], dc["peak_current_per_cap_with_recovery_A"], C22["ipkr"], dc["rms_per_cap_A"],
        C22["irms"], L_BUS_REQ * 1e9)
    rq = DAMP["required_resistor_rating"]
    r_leg, c_leg = DAMP_SER * DAMP_R / DAMP_PAR, DAMP_C[0] / DAMP_C[1]
    p_part = CRCW["p70"] * (CRCW["t_max"] - T_BOARD) / (CRCW["t_max"] - 70.0)       # linear derating 70 -> 155 C
    p_leg = DAMP_SER * DAMP_PAR * p_part
    assert abs(r_leg - DAMP["R_per_leg_ohm"]) <= 0.01 * r_leg and abs(c_leg - LEG["Cdmp"]) < 1e-12, "damper value"
    assert abs(LEG["Rdmp"] - DAMP["R_per_leg_ohm"]) < 0.01 and abs(c_leg * 1e9 - DAMP["C_per_leg_nF"]) < 0.01
    assert p_leg >= rq["continuous_W_per_leg_min"] >= 1.4 * DAMP["power_per_leg_W_max"], "damper resistor power"
    assert CRCW["umax"] >= rq["pulse_or_working_voltage_per_element_V_min"] >= DAMP["peak_voltage_per_resistor_V"]
    assert CRCW["e_1us"] >= rq["energy_per_pulse_per_element_uJ"] * 1e-6 / DAMP_PAR, "damper pulse energy"
    assert DAMP["capacitor_voltage_per_element_V"] <= 0.5 * 2000.0, "damper C0G element voltage"
    say("damp", "Damper per leg (cell_spec damper, PVR-02; = leg deck %s ohm + %s): %d x (%d x %s CRCW2512-HP in parallel) "
        "= %.2f ohm + %d x %s 2 kV C0G in series. Power %.2f W (normal) / %.2f W (trip corner) per leg vs %.1f W rated at "
        "a %.0f C board (%d parts x %.2f W derated from 1.5 W at 70 C, CRCW-HP p1) >= required %.1f W; element voltage "
        "%.0f V peak vs Umax %.0f V >= required %.0f V; pulse %.0f uJ per element = %.0f uJ per part vs >= %.0f uJ at "
        "1 us (CRCW-HP p5 graph reading); C0G %.0f V per element (2 kV)", LEG["Rdmp"], gdrv.farad(LEG["Cdmp"]),
        DAMP_SER, DAMP_PAR, gdrv.ohm(DAMP_R), r_leg, DAMP_C[1], gdrv.farad(DAMP_C[0]), DAMP["power_per_leg_W_max"],
        DAMP["power_per_leg_W_at_trip_1100V"], p_leg, T_BOARD, DAMP_SER * DAMP_PAR, p_part,
        rq["continuous_W_per_leg_min"], DAMP["peak_voltage_per_resistor_V"], CRCW["umax"],
        rq["pulse_or_working_voltage_per_element_V_min"], rq["energy_per_pulse_per_element_uJ"],
        rq["energy_per_pulse_per_element_uJ"] / DAMP_PAR, CRCW["e_1us"] * 1e6, DAMP["capacitor_voltage_per_element_V"])
    N["p_damp"] = p_leg
    bc, rt = spec("bus_reverse_clamp"), spec("bus_reverse_clamp", "ratings")
    sw = bc["surge_worst"]
    assert bc["part"] == PARTS["WND75P16W6"]["mpn"] and bc["datasheet"] == PARTS["WND75P16W6"]["ds"], "clamp part"
    assert (rt["V_RRM_V"], rt["I_FSM_10ms_A"], rt["I2t_10ms_A2s"]) == (WND["vrrm"], WND["ifsm"], WND["i2t"]) and \
        [tuple(x) for x in rt["I_FSM_vs_tp"]] == list(WND["ifsm_tp"]), "clamp ratings != datasheet"
    tp = sw["t95_ms"] * 1e-3
    (t1, i1), (t2, i2) = [(a, b) for a, b in zip(WND["ifsm_tp"], WND["ifsm_tp"][1:]) if a[0] <= tp <= b[0]][0]
    i_tp = i1 + (i2 - i1) * math.log(tp / t1) / math.log(t2 / t1)
    i2t_ok = i_tp ** 2 * tp / 2
    assert abs(i2t_ok - sw["allowed_I2t_at_t95_A2s"]) <= 0.02 * i2t_ok and sw["per_device_I2t_A2s"] * 2 <= i2t_ok
    assert bc["reverse_voltage_V"] * 1.2 <= WND["vrrm"], "clamp reverse voltage margin below 1.2"
    assert bc["body_diode_share_with_clamp"]["per_device_peak_A_max"] <= DEV["idm"], "body diode share above IDM"
    say("clamp", "Bank reverse clamp (cell_spec bus_reverse_clamp): %d x %s per bank, %d banks; reverse %.0f V vs VRRM %.0f V "
"(x%.2f); worst surge %s: %.0f A, %.0f A2s per device vs %.0f A2s allowed at t95 %.3f ms (IFSM %.0f A at tp, "
        "Fig. 4 p4) = x%.1f (peak vs IFSM at 10 us %.0f A: x%.2f, I2t is the rating); SiC body diodes keep %.0f A peak vs IDM %.0f A (without clamp %.0f A); branch "
        "inductance <= %.1f nH (LAYOUT REQUIREMENT)", bc["devices_per_bank"], bc["part"], bc["banks_per_cell"],
        bc["reverse_voltage_V"], WND["vrrm"], WND["vrrm"] / bc["reverse_voltage_V"], sw["case"],
        sw["per_device_peak_A"], sw["per_device_I2t_A2s"], i2t_ok, sw["t95_ms"], i_tp,
        i2t_ok / sw["per_device_I2t_A2s"], WND["ifsm_tp"][0][1], WND["ifsm_tp"][0][1] / sw["per_device_peak_A"],
        bc["body_diode_share_with_clamp"]["per_device_peak_A_max"], DEV["idm"],
        bc["without_clamp"]["per_device_peak_A_max"], bc["branch_inductance_max_nH"])
    r_bl = BLEED * 124e3
    c_bank = cnt * C45["c"] + DEC_N * C22["c"]
    t60 = r_bl * c_bank * math.log(V_OVP / 60.0)
    p_el, p_el_max = V_OVP ** 2 / r_bl / BLEED, TNPV["p70"] * (TNPV["t_film"] - 85.0) / (TNPV["t_film"] - 70.0)
    assert V_OVP / BLEED <= 0.5 * TNPV["umax"] and p_el <= p_el_max and t60 <= 300.0, "bleeder"
    say("bleed", "Bleeder per bank %d x TNPV1210 124k = %s: each element %.0f V at %.0f V (Umax %.0f V), %.0f mW vs %.0f mW "
        "(0.33 W derated to an 85 C board, ASSUMED); %.0f uF bank to 60 V in %.0f s (<= 5 min label time, ASSUMED target); "
        "%.1f W per cell at 1000 V", BLEED, gdrv.ohm(r_bl), V_OVP / BLEED, V_OVP, TNPV["umax"], p_el * 1e3, p_el_max * 1e3,
        c_bank * 1e6, t60, 2 * 1000.0 ** 2 / r_bl)

    # ---- bus over-voltage: local comparator or not (load rejection into the module's bank)
    m3 = spec("module", "3_cells")
    slew = m3["port_current_max_A"] / (m3["port_B_capacitance_total_uF"] * 1e-6)
    d = port.S["divider"]
    tau = (d["n_top"] * d["R_top_elem_ohm"] * d["R_bot_ohm"] / (d["n_top"] * d["R_top_elem_ohm"] + d["R_bot_ohm"])
           * d["C_filter_F"])
    t_path = tau + AMC3330_DELAY + 5.76e3 * 56e-12 + 0.1e-6 + RX["tpd"] + T_LOGIC + T_DRV
    e_l = 3 * 0.5 * IND["L_at_45A_uH"] * 1e-6 * spec("ratings", "I_max_A_low_voltage_port") ** 2
    v_bus = V_OVP + slew * t_path
    v_bus += e_l / (m3["port_B_capacitance_total_uF"] * 1e-6 * v_bus)
    v_dev = v_bus * (1 + st["turnoff_overshoot_V"] / V_OVP)
    assert v_dev <= 0.85 * DEV["vdss"] and v_bus <= C4AQ_OV * C45["vndc"], "bus OV through the CTRL path"
    N.update(v_bus=v_bus, v_dev=v_dev, t_path=t_path, slew=slew)
    say("ovp", "Bus OV, no local comparator: load rejection %.0f A into %.0f uF = %.2f V/us; CTRL path (port-board divider "
        "RC %.1f us + AMC3330 2.1 us + AFE + CMPSS/X-BAR 0.1 us ASSUMED + RS-422 + driver) %.1f us -> %.0f V incl. the "
        "inductor energy of 3 cells; device %.0f V = %.3f x V_DSS (<= 0.85), film %.3f x VOP85 (<= %.2f x VNDC 30 min/day). "
        "A local divider + isolated comparator would save ~%.0f V and add a second HV-PELV barrier per port",
        m3["port_current_max_A"], m3["port_B_capacitance_total_uF"], slew / 1e6, tau * 1e6, t_path * 1e6, v_bus, v_dev,
        v_dev / DEV["vdss"], v_bus / C45["vop85"], C4AQ_OV, slew * tau)
    return out, N


# ------------------------------------------------------------------------------------------------ schematic
def bias_pair(B, name):
    """Fail-safe receiver end of an RS-422 pair: P to GND, N to 3V3, termination across (open pair = LOW)."""
    B.R(gdrv.ohm(R_BIAS), name + "_P", "GND", note="fail-safe bias")
    B.R(gdrv.ohm(R_BIAS), name + "_N", "+3V3", note="fail-safe bias")
    B.R(gdrv.ohm(R_TERM), name + "_P", name + "_N", pkg="1206", note="termination (1206: full 3.3 V swing = 0.1 W)")


def build_design():
    _, N = calc()
    B = L.Builder(PROJECT, "PVCELL-25 power cell", REV, DATE, CATALOG, rails=["+24V", "+3V3", "+5V"], returns=["GND", "AGND"],
                  subtitle="25 kW (27.5 kW max) bidirectional 4-switch buck-boost cell, 2 x 4 x SG2M040170HJ",
                  comment1="HV-PELV reinforced 1000 V DC (ECO-10); barriers listed on the cover",
                  comment4="Not bench-validated. Values: sim/out/pv_design/cell_spec.json; checks: design_check()")
    hv = {"A_BUS+", "B_BUS+", "BUS-", "SW_A", "SW_B"}
    gd, iso, xing = {}, set(), set()

    # ---------------------------------------------------------------- 01 CELL connector + supplies
    B.new_sheet("01_cell", "CELL connector, logic supply, ID",
                "40-way CELL contract to CTRL-C2000, +24V = safety-switched +24V_GD;\n"
                "LMR36015 3.3 V + 5 V (driver logic side), TPS3710 on RDY, ID 4.99k, AGND tie")
    B.block("CELL connector (interfaces.CELL)",
            "Ribbon 1:1 to a CTRL-C2000 cell port. AN2_N/AN3_N = AGND here (single-ended 0-2.5 V, AN_N tied at the\n"
            "source per the CTRL AFE). NTC_P/N float to the second heatsink NTC (biased on CTRL). PWM5-8 unused:\n"
            "120R only. +24V: %.2f A of %.1f A worst case (design_check); <= %d uF on the rail." %
            (N["i_24"], IF.CELL_24V_MAX_A, IF.CELL_24V_MAX_UF))
    B.part("J_CELL", IF.pins(IF.CELL, rename={"AN2_N": "AGND", "AN3_N": "AGND", "RDY": "RDY_CELL"}))
    for net in ("+24V", "GND", "AGND"):
        B.flag(net)
    B.part("CB100", {"1": "+24V", "2": "GND"})
    B.C("10u", "+24V", "GND", pkg="1210", volt="50V")
    B.C("100n", "+24V", "GND", volt="100V")
    B.R(IF.ID_OHM[PROJECT], "ID", "AGND", tol="0.1%", note="board ID (interfaces.ID_OHM)")
    for k in range(5, 9):
        B.R(gdrv.ohm(R_TERM), "PWM%d_P" % k, "PWM%d_N" % k, pkg="1206", note="unused pair, termination only")

    for rail, r_bot, text in (
            ("+3V3", "43.2k", "24 V -> 3.315 V (100k/43.2k, SNVSB49D 10.2, as PV-PORT). Runs down to 4.2 V input, so the "
                              "logic keeps\nevery gate disabled while +24V collapses. Load <= %.0f mA (datasheet maxima)."
                              % (N["i_3v3"] * 1e3)),
            ("+5V", "24.9k", "24 V -> 5.02 V (100k/24.9k): VCC1 of the four NSI6651 and the AHCT input stage - the driver\n"
                             "input thresholds are specified at VCC1 = 5 V only (p7). Load <= %.0f mA (datasheet maxima)."
                             % (N["i_5v"] * 1e3))):
        t = rail[1:]
        B.block("Logic supply %s (LMR36015, 400 kHz)" % rail, text)
        B.part("LMR36015", {"2": "+24V", "10": "+24V", "9": "+24V", "3": None, "1": "GND", "11": "GND", "12": "SW_" + t,
                            "4": "BOOT_" + t, "7": "FB_" + t, "8": None, "5": "VCC_" + t, "6": "GND"})
        B.C("4.7u", "+24V", "GND", pkg="1206", volt="50V")
        B.C("220n", "+24V", "GND", volt="50V")
        B.C("100n", "BOOT_" + t, "SW_" + t)
        B.C("1u", "VCC_" + t, "GND", volt="16V")
        B.part("WE_L10U", {"1": "SW_" + t, "2": rail})
        B.R("100k", rail, "FB_" + t)
        B.R(r_bot, "FB_" + t, "GND")
        B.C("22u", rail, "GND", pkg="1206", volt="10V")
        B.C("22u", rail, "GND", pkg="1206", volt="10V")
        B.flag(rail)
        B.TP(rail)

    B.block("+24V supervisor (TPS3710) on RDY",
            "OUT is open drain on the RDY line: RDY low below %.2f-%.2f V, released above %.2f-%.2f V. That is above\n"
            "the UCC14241-Q1 UVLO (17.1-18.9 V falling): EN_DRV drops and every driver pulls its gates to VEE\n"
            "while the bias is still regulating -> clean turn-off when the safety chain removes +24V_GD (D-012)."
            % (N["uv_fall"][0], N["uv_fall"][1], N["uv_rise"][0], N["uv_rise"][1]))
    B.part("TPS3710", {"5": "+3V3", "3": "UV_SNS", "1": "RDY", "2": "GND", "4": "GND", "6": "GND"})
    B.C("100n", "+3V3", "GND")
    B.R(gdrv.ohm(UV_DIV[0]), "+24V", "UV_SNS", tol="0.1%")
    B.R(gdrv.ohm(UV_DIV[1]), "UV_SNS", "GND", tol="0.1%")
    B.C("1n", "UV_SNS", "GND")
    B.block("AGND - GND star point", "Analog return (LA 150-P M / RM, +/-15 V COM, op amps, comparators, reference,\nNTCs, ID) "
                                     "meets GND only here.")
    B.part("NETTIE", {"1": "AGND", "2": "GND"})

    # ---------------------------------------------------------------- 02 RS-422 interface + fault latch
    B.new_sheet("02_logic", "Fail-safe RS-422, fault latch",
                "AM26LV32E receivers with external fail-safe bias (open = gate off),\n"
                "FLT driver, OC/OT fault latch, EN_DRV, PWM gating")
    B.block("PWM1-4 receivers (fail-safe)",
            "A = PWMk_P, B = PWMk_N. %s P->GND, %s N->3V3, %s across: an open, unplugged or unpowered pair reads\n"
            "%.2f V / %.2f V worst case (< VIT- -0.2 V) -> output LOW = gate off. The receiver's own open-input\n"
            "fail-safe (output HIGH) never applies: the bias always defines the pair. Driven high: %.1f V."
            % (gdrv.ohm(R_BIAS), gdrv.ohm(R_BIAS), gdrv.ohm(R_TERM), N["vid_open"], N["vid_unpowered"], N["vid_high"]))
    B.part("AM26LV32E", {"2": "PWM1_P", "1": "PWM1_N", "6": "PWM2_P", "7": "PWM2_N", "10": "PWM3_P", "9": "PWM3_N",
                         "14": "PWM4_P", "15": "PWM4_N", "3": "PWM1_RX", "5": "PWM2_RX", "11": "PWM3_RX",
                         "13": "PWM4_RX", "4": "+3V3", "12": "GND", "16": "+3V3", "8": "GND"})
    B.C("100n", "+3V3", "GND")
    for k in range(1, 5):
        bias_pair(B, "PWM%d" % k)
    B.block("EN receiver (fail-safe)", "Same bias: open EN = LOW = drivers disabled. Spare receivers: A low, B high.")
    B.part("AM26LV32E", {"2": "EN_P", "1": "EN_N", "3": "EN_RX", "6": "GND", "7": "+3V3", "5": None, "10": "GND",
                         "9": "+3V3", "11": None, "14": "GND", "15": "+3V3", "13": None, "4": "+3V3", "12": "GND",
                         "16": "+3V3", "8": "GND"})
    B.C("100n", "+3V3", "GND")
    bias_pair(B, "EN")
    B.block("FLT driver",
            "FLT_P = FLT_OK, FLT_N = /FLT_OK. CTRL receives the pair swapped with its own bias: healthy = -2 V\n"
            "= no fault; this board unpowered = AM26LV31E outputs Hi-Z (SLLS848C) -> CTRL bias -> fault.")
    B.part("AM26LV31E", {"1": "FLT_OK", "7": "GND", "9": "GND", "15": "GND", "4": "+3V3", "12": "GND", "16": "+3V3",
                         "8": "GND", "2": "FLT_P", "3": "FLT_N", "6": None, "5": None, "10": None, "11": None, "14": None,
                         "13": None})
    B.C("100n", "+3V3", "GND")
    B.block("Fault latch, gate enable, FLT, RDY",
            "PRE_N = OCP_OK AND OCN_OK AND OT_WIN_OK (low on fault) sets the latch: FAULT_N low until EN drops; with EN\n"
            "low AND a fault still present both /PRE and /CLR are low (Q = /Q = H, Table 8-1), so FLT_OK = DRV_FLT_N AND\n"
            "RDY AND FAULT_N AND PRE_N reports the LIVE fault too (PVR-07 / INT-17); EN rising re-sets the latch.\n"
            "EN_DRV = EN_RX AND RDY AND FAULT_N drives every RST/EN. RDY (wired-AND: drivers, bias PG, +24V and +15V\n"
            "monitors) reaches the CELL pin through a push-pull SN74LVC1G17: actively high when ready, low otherwise;\n"
            "unpowered = Ioff, the card's 100 k pull-down reads not ready (INT-14).")
    B.part("LVC1G74", {"7": "PRE_N", "6": "EN_RX", "2": "GND", "1": "GND", "5": None, "3": "FAULT_N", "8": "+3V3",
                       "4": "GND"})
    B.C("100n", "+3V3", "GND")
    B.part("LVC11", {"1": "EN_RX", "2": "RDY", "13": "FAULT_N", "12": "EN_DRV", "3": "DRV_FLT_N", "4": "RDY",
                     "5": "FAULT_N", "6": "FLT_PRE", "9": "OCP_OK", "10": "OCN_OK", "11": "OT_WIN_OK", "8": "PRE_N",
                     "14": "+3V3", "7": "GND"})
    B.C("100n", "+3V3", "GND")
    B.part("LVC1G08", {"1": "FLT_PRE", "2": "PRE_N", "4": "FLT_OK", "5": "+3V3", "3": "GND"})
    B.C("100n", "+3V3", "GND")
    B.part("SN74LVC1G17", {"1": None, "2": "RDY", "3": "GND", "4": "RDY_CELL", "5": "+3V3"})
    B.C("100n", "+3V3", "GND")
    B.R("2.00k", "+5V", "RDY", note="RDY wired-AND pull-up to the drivers' VCC1 (+5V lost = not ready)")
    B.C("100p", "RDY", "GND", diel="C0G", tol="5%")
    B.R("4.99k", "+5V", "DRV_FLT_N", note="driver FLT wired-OR pull-up to VCC1 (+5V lost = fault)")
    B.C("100p", "DRV_FLT_N", "GND", diel="C0G", tol="5%")
    B.R("10k", "EN_DRV", "GND", note="EN_DRV low with the gate unpowered")
    B.block("Fail-safe off: the three loss cases",
            "1 CABLE UNPLUGGED: no +24V -> no gate bias, no logic: every gate sits at 0 V on its 10k gate-source resistor\n"
            "  (VGS(th) >= 1.9 V). With +24V still present, an open pair reads %.2f V -> PWM and EN LOW.\n"
            "2 CTRL UNPOWERED, or rev C card not enabling (it tri-states its PWM/EN drivers unless CHB_OK, its enable and\n"
            "  our RDY are high, INT-15): driver outputs Hi-Z (<= 100 uA, SLLS848C p5) -> %.2f V -> LOW. In reset the card\n"
            "  drives 0 (10k on its ePWM balls, EN = GATE_EN AND CHB_OK AND MCU enable).\n"
            "3 +3V3 ABSENT: LVC/AM26 Ioff -> 10k pull-downs hold EN_DRV low -> AHCT: EN_GD, PWMk low; RDY_CELL Hi-Z -> card\n"
            "  reads 0 V; FLT Hi-Z = fault. +5V ABSENT: NSI6651 VCC1 UVLO -> OUT low (p28), UCC14241-Q1 ENA = 0 -> bias off,\n"
            "  RDY / DRV_FLT_N lose their pull-ups (to +5V) -> not ready / fault. +24V REMOVED (D-012 channel B): RDY low at\n"
            "  >= %.1f V -> EN_DRV and PWM low while bias regulates."
            % (N["vid_open"], N["vid_unpowered"], N["uv_fall"][0]))
    for net in ("EN_DRV", "FLT_OK", "FAULT_N", "RDY", "PRE_N"):
        B.TP(net)
    B.block("PWM gating + 5 V input level (fast path)",
            "PWMk = PWMk_RX AND EN_DRV, EN_GD = EN_DRV, both at +5V: the NSI6651 input thresholds are specified at\n"
            "VCC1 = 5 V only (VINH <= 3.5 V, p7), so VCC1 = +5V and the AHCT gates (VIH 2.0 V) lift the 3.3 V logic.\n"
            "A trip removes the commands within ~20 ns, ahead of the RST/EN filter (0.48-0.8 us). +5V or +3V3 absent:\n"
            "EN_DRV / PWMk pulled down, AHCT unpowered -> 0, NSI6651 VCC1 UVLO -> OUT low (functional modes p28).")
    B.part("AHCT08", {"1": "PWM1_RX", "2": "EN_DRV", "3": "PWM1", "4": "PWM2_RX", "5": "EN_DRV", "6": "PWM2",
                      "9": "PWM3_RX", "10": "EN_DRV", "8": "PWM3", "12": "PWM4_RX", "13": "EN_DRV", "11": "PWM4",
                      "14": "+5V", "7": "GND"})
    B.C("100n", "+5V", "GND")
    B.part("AHCT1G08", {"1": "EN_DRV", "2": "EN_DRV", "4": "EN_GD", "5": "+5V", "3": "GND"})
    B.C("100n", "+5V", "GND")
    B.R("10k", "EN_GD", "GND", note="pull-down: unpowered gate = drivers disabled")

    # ---------------------------------------------------------------- 03 power stage
    B.new_sheet("03_power", "Power stage, decoupling, dampers",
                "Leg A: S1/S2 on port A, leg B: S3/S4 on port B, 2 x SG2M040170HJ each;\n"
                "film decoupling and RC damper per leg at the device pins")
    for leg, (top, bot), bus, sw in (("A", ("S1", "S2"), "A_BUS+", "SW_A"), ("B", ("S3", "S4"), "B_BUS+", "SW_B")):
        B.block("Leg %s: %s (top) / %s (bottom), %d x SG2M040170HJ each" % (leg, top, bot, N_PAR),
                "Kelvin source (pin 3) of each device returns to its own driver COM (gate-drive sheet).\n"
                "Each tab on its own %s pad (P...: basic insulation HV-PE, IC-07; spec in the BOM). LAYOUT (IC-22):\n"
                "milled slot between drain pin 1 and pins 2-4. SYMMETRY: equal drain/source busbar paths per switch."
                % HS_PAD["material"].split(",")[0])
        for s in (top, bot):
            drain, source, _ = POS[s]
            for dev in "ab"[:N_PAR]:
                xing.add(B.part("SG2M040170HJ", {"1": drain, "5": drain, "2": source, "3": "KS_%s%s" % (s, dev),
                                                   "4": "G_%s%s" % (s, dev)}).ref)
                iso.add(B.part("PAD_ALN", {"1": drain, "2": "PE"}, value="PAD AlN %g mm (CUSTOM)" % HS_PAD["thickness_mm"]).ref)
        d1, d2, d3 = ("DMP_%s%d" % (leg, i) for i in (1, 2, 3))
        hv |= {d1, d2, d3}
        B.block("Leg %s decoupling + RC damper" % leg,
                "%d x %s at the %s pins: commutation loop cap-top-bottom <= %.0f nH, bulk bank <= %.0f nH from the\n"
                "leg (LAYOUT REQUIREMENTS, leg deck). Damper (PVR-02): %.2f W normal / %.2f W trip corner per leg into\n"
                "%d x %d CRCW2512-HP (%.1f W rated at %.0f C, 500 V); damper loop at the device pins <= 3 nH."
                % (DEC_N, DEC_MPN, "/".join((top, bot)), LEG["Ldc"] * 1e9 + LEG["Lloop"] * 1e9, L_BUS_REQ * 1e9,
                   DAMP["power_per_leg_W_max"], DAMP["power_per_leg_W_at_trip_1100V"], DAMP_SER, DAMP_PAR,
                   N["p_damp"], T_BOARD))
        for _ in range(DEC_N):
            B.part("C4AQ_2U2", {"1": bus, "2": "BUS-"})
        B.C(gdrv.farad(DAMP_C[0]), bus, d1, pkg="2220", volt="2000V", diel="C0G", tol="5%")
        B.C(gdrv.farad(DAMP_C[0]), d1, d2, pkg="2220", volt="2000V", diel="C0G", tol="5%")
        for n1, n2 in ((d2, d3), (d3, "BUS-")):
            for _ in range(DAMP_PAR):
                B.part("R_DAMP", {"1": n1, "2": n2}, value=gdrv.ohm(DAMP_R))

    # ---------------------------------------------------------------- 04 banks, bleeders, terminals, inductor
    B.new_sheet("04_bus", "Film banks, bleeders, inductor",
                "Port A/B C4AQ banks + TNPV bleeders, bus studs to PV-PORT,\n"
                "off-board CUSTOM inductor on studs SW_A / SW_B, winding NTC header")
    for p, bus in (("A", "A_BUS+"), ("B", "B_BUS+")):
        c = spec("capacitors", "port_" + p)
        B.block("Port %s film bank + bleeder" % p,
                "%d x %s = %.0f uF, ripple %.1f of %.1f A rms (use limit). Bleeder %d x TNPV1210 124k: %.0f V and\n"
                "%.0f mW per element at %.0f V; bank to 60 V in <= 5 min. Busbar to the leg <= %.0f nH."
                % (c["count"], c["mpn"], c["C_total_uF"], c["ripple_worst_Arms"], c["ripple_use_limit_Arms"], BLEED,
                   V_OVP / BLEED, V_OVP ** 2 / (BLEED * 124e3) / BLEED * 1e3, V_OVP, L_BUS_REQ * 1e9))
        for _ in range(c["count"]):
            B.part("C4AQ_45U", {"1": bus, "2": "BUS-"})
        nodes = [bus] + ["BL%s%d" % (p, i) for i in range(1, BLEED)] + ["BUS-"]
        hv |= set(nodes)
        for n1, n2 in zip(nodes, nodes[1:]):
            B.part("TNPV_124K", {"1": n1, "2": n2})
        bc = spec("bus_reverse_clamp")
        B.block("Port %s bank reverse clamp (%d x %s)" % (p, bc["devices_per_bank"], bc["part"]),
                "A bolted terminal short rings the bank below zero; these carry the freewheel instead of the SiC body\n"
                "diodes. Cathode to %s, anode to BUS-, in parallel. LAYOUT REQUIREMENT: on the laminated bus AT the\n"
                "C4AQ terminals, spread symmetrically between the capacitors, bank -> group -> bank <= %.1f nH." % (
                    bus, bc["branch_inductance_max_nH"]))
        for _ in range(bc["devices_per_bank"]):
            B.part("WND75P16W6", {"1": bus, "3": bus, "2": "BUS-"})
    B.block("Bus terminals to PV-PORT", "REDCUBE M3 studs, 100 A each: A_BUS+, B_BUS+, BUS- (net names as gen/port.py).")
    for net in ("A_BUS+", "B_BUS+", "BUS-"):
        B.part("STUD", {"1": net})
    B.block("Inductor (off-board CUSTOM) + terminals",
            "%.0f uH at %.0f A (L0 %.0f uH), %d turns on %s; full spec in the part description / BOM.\n"
            "Studs SW_A (lead A through the LA 150-P) and SW_B. Winding NTC (reinforced to the winding) on J."
            % (IND["L_at_45A_uH"], spec("ratings", "I_max_A_low_voltage_port"), IND["L0_uH"], IND["turns"],
               IND["core"].split(" (")[0]))
    iso.add(B.part("L_CELL", {"1": "SW_A", "2": "SW_B", "3": "T_L", "4": "AGND", "5": "PE"},
                   value=MAG_ROW).ref)
    B.part("STUD", {"1": "SW_A"})
    B.part("STUD", {"1": "SW_B"})
    B.part("J_NTC", {"1": "T_L", "2": "AGND"})
    B.part("PE_BOND", {"1": "PE"}, value="PE bond M4 (lead screen, heatsink)")

    # ---------------------------------------------------------------- 05 sensing + local trips
    B.new_sheet("05_sense", "Inductor current, temperatures, trips",
                "AN1 = LEM LA 150-P + driver, AN2/AN3 = heatsink/winding temperature,\n"
                "OC window + heatsink OT comparators, 2.5 V reference, NTC pass-through")
    B.block("AN1: inductor current (LEM LA 150-P around inductor lead A)",
            "Lead A of L_CELL (insulated, screen earthed in the aperture) passes through the transducer; positive = SW_A ->\n"
            "inductor -> SW_B. IS = IP/2000 into RM %s to AGND: %.0f mV/A. Linear +/-%.0f A, delay <= %.2f us; CTRL backup\n"
            "peak %.1f A (< %.0f A). LAYOUT: lead <= 100 C at the transducer (LA 150-P p4), RM and the +/-15 V return at\n"
            "one AGND point next to the transducer; no other conductor through the aperture." % (
                gdrv.ohm(RM), K_M * 1e3, N["lin"], N["t_sense"] * 1e6, N["pk_bk"], IND["I_at_50pct_L0_A"]))
    iso.add(B.part("LA150P", {"P": "SW_A", "1": "+15V", "2": "IM", "3": "-15V"}).ref)
    B.R(gdrv.ohm(RM), "IM", "AGND", pkg="1206", tol="0.1%", note="RM, 25 ppm/K thin film")
    B.C("100n", "+15V", "AGND", volt="50V")
    B.C("100n", "-15V", "AGND", volt="50V")
    B.block("AN1 differential driver (%.0f mV/A, CM %.2f V)" % (K_AN1 * 1e3, LM40["v"] / 2),
            "OPA4322 A: AN1_P = VMID + V_M (gain 2 on (V_M + VMID)/2); OPA2322 A: AN1_N = VMID - V_M (inverting, + at\n"
            "VMID/2). Direct drive (< 1 ohm closed loop) of the ribbon and the CTRL 1.50 k open-input bias (<= %.1f mA).\n"
            "CTRL AFE 0.402 -> %.4f A/LSB. An unplugged cable reads far negative on CTRL (its bias)." % (
                N["i_drv"] * 1e3, N["lsb"]))
    B.R("10.0k", "IM", "AP_NI", tol="0.1%")
    B.R("10.0k", "VMID", "AP_NI", tol="0.1%")
    B.R("10.0k", "AP_INV", "AGND", tol="0.1%")
    B.R("10.0k", "AN1_P", "AP_INV", tol="0.1%")
    B.part("OPA2322", {"3": "AN_REF", "2": "AN_INV", "1": "AN1_N", "5": "VMID", "6": "OPB_SPARE", "7": "OPB_SPARE",
                       "8": "+3V3", "4": "AGND"})
    B.C("100n", "+3V3", "AGND")
    B.R("10.0k", "IM", "AN_INV", tol="0.1%")
    B.R("10.0k", "AN_INV", "AN1_N", tol="0.1%")
    B.R("10.0k", "VMID", "AN_REF", tol="0.1%")
    B.R("10.0k", "AN_REF", "AGND", tol="0.1%")
    B.C("100n", "AN_REF", "AGND")
    B.block("+/-15 V for the LA 150-P (RS3-2415D)",
            "From +24V through a 0.5 A quick fuse (RECOM requires one). Isolation of the module not used: COM = AGND.\n"
            "TPS3710 holds RDY low below %.1f-%.1f V on +15 V: a dead sensor supply never reads as 0 A while enabled.\n"
            "-15 V is not monitored separately (one converter): firmware checks AN1 plausibility." % N["s15_fall"])
    B.part("UMF05", {"1": "+24V", "2": "+24V_S"})
    B.flag("+24V_S")
    B.C("4.7u", "+24V_S", "GND", pkg="1206", volt="50V")
    B.part("RS3_2415D", {"2": "+24V_S", "1": "GND", "3": None, "5": None, "6": "+15V", "7": "AGND", "8": "-15V"})
    B.C("4.7u", "+15V", "AGND", pkg="1206", volt="25V")
    B.C("4.7u", "-15V", "AGND", pkg="1206", volt="25V")
    B.part("TPS3710", {"5": "+3V3", "3": "S15_SNS", "1": "RDY", "2": "GND", "4": "GND", "6": "GND"})
    B.C("100n", "+3V3", "GND")
    B.R(gdrv.ohm(S15_DIV[0]), "+15V", "S15_SNS", tol="0.1%")
    B.R(gdrv.ohm(S15_DIV[1]), "S15_SNS", "AGND", tol="0.1%")
    B.C("1n", "S15_SNS", "AGND")
    B.block("OC window comparator +/-%.1f A (both polarities)" % I_TRIP,
            "TLV3502 on AN1_P through %s/%s: OCP_OK low above TH_HI, OCN_OK low below TH_LO -> latch. Trip %.1f-%.1f A\n"
            "(all tolerances); gate off %.2f us after the crossing (PWM path), %.2f us via EN; allowed %.2f us.\n"
            "OPA4322 B: VMID buffer for the AN1 driver." % (gdrv.ohm(OC_RC[0]), gdrv.farad(OC_RC[1]), N["i_trip"][1],
                                                            N["i_trip"][2], N["t_pwm"] * 1e6, N["t_en"] * 1e6,
                                                            N["t_req"] * 1e6))
    B.part("OPA4322", {"3": "AP_NI", "2": "AP_INV", "1": "AN1_P", "5": "VMID_DIV", "6": "VMID", "7": "VMID",
                       "10": "T_HS", "9": "AN2_BUF", "8": "AN2_BUF", "12": "T_L", "13": "AN3_BUF", "14": "AN3_BUF",
                       "4": "+3V3", "11": "AGND"})
    B.C("100n", "+3V3", "AGND")
    B.R(gdrv.ohm(OC_RC[0]), "AN1_P", "OC_SE")
    B.C(gdrv.farad(OC_RC[1]), "OC_SE", "AGND", diel="C0G", tol="5%")
    a, b = OC_DIV
    for n1, n2, r in (("REF2V5", "TH_HI", a), ("TH_HI", "VMID_DIV", b), ("VMID_DIV", "TH_LO", b), ("TH_LO", "AGND", a)):
        B.R(gdrv.ohm(r), n1, n2, tol="0.1%")
    for net in ("TH_HI", "TH_LO", "VMID_DIV"):
        B.C("10n", net, "AGND")
    B.part("TLV3502", {"1": "TH_HI", "2": "OC_SE", "3": "OC_SE", "4": "TH_LO", "7": "OCP_OK", "6": "OCN_OK",
                       "8": "+3V3", "5": "AGND"})
    B.C("100n", "+3V3", "AGND")
    B.TP("OC_SE")
    B.block("2.5 V reference", "LM4040 A grade from 3V3 through 120R: %.1f mA worst load + 65 uA, <= %.1f mA total (15 mA\n"
                               "max). Feeds the OC thresholds, the OT threshold and both NTC dividers (OT is ratiometric)."
            % (N["i_ref"][0] * 1e3, N["i_ref"][1] * 1e3))
    B.part("LM4040", {"1": "REF2V5", "2": "AGND", "3": "AGND"})   # pin 3 to the anode (SNOS633N Table 4-1, PVR-09)
    B.R("120R", "+3V3", "REF2V5")
    B.C("1u", "REF2V5", "AGND", volt="16V")
    B.TP("REF2V5")
    B.block("AN2 heatsink temperature + OT trip %.0f C" % T_OT,
            "2.5 V - %s - T_HS - NTC (B57703M, screwed next to the hottest device) - AGND: %.2f V at %.0f C ... %.3f V at\n"
            "%.0f C, buffered (OPA4322 C) to AN2_P via 47R. TLV3502 A: low above %.1f C (%.1f-%.1f C); B: low when T_HS\n"
            "> TH_OPEN (open probe reads 2.5 V; -40 C reads %.3f V) -> OT_WIN_OK = OT_OK AND NTC_OK -> latch (PVR-08)."
            % (gdrv.ohm(R_NTC_BIAS), N["an2"][0][1], N["an2"][0][0], N["an2"][1][1], N["an2"][1][0], N["t_ot"][0],
               N["t_ot"][1], N["t_ot"][2], N["open_win"][0]))
    B.part("B57703M", {"1": "T_HS", "2": "AGND"})
    B.R(gdrv.ohm(R_NTC_BIAS), "REF2V5", "T_HS", tol="0.1%")
    B.C("100n", "T_HS", "AGND")
    B.R("47R", "AN2_BUF", "AN2_P")
    B.R(gdrv.ohm(OT_DIV[0]), "REF2V5", "TH_OT", tol="0.1%")
    B.R(gdrv.ohm(OT_DIV[1]), "TH_OT", "AGND", tol="0.1%")
    B.C("10n", "TH_OT", "AGND")
    B.R(gdrv.ohm(OPEN_DIV[0]), "REF2V5", "TH_OPEN", tol="0.1%")
    B.R(gdrv.ohm(OPEN_DIV[1]), "TH_OPEN", "AGND", tol="0.1%")
    B.C("10n", "TH_OPEN", "AGND")
    B.part("TLV3502", {"1": "T_HS", "2": "TH_OT", "3": "TH_OPEN", "4": "T_HS", "7": "OT_OK", "6": "NTC_OK", "8": "+3V3",
                       "5": "AGND"})
    B.C("100n", "+3V3", "AGND")
    B.part("LVC1G08", {"1": "OT_OK", "2": "NTC_OK", "4": "OT_WIN_OK", "5": "+3V3", "3": "GND"})
    B.C("100n", "+3V3", "GND")
    B.block("AN3 inductor winding temperature",
            "2.5 V - %s - T_L - winding NTC (inside L_CELL, header on sheet 04) - AGND: the NTC lead next to the %.0f V/ns\n"
            "winding ends on AGND and the 100 nF T_L node, never on the reference. Buffered (OPA4322 D) to AN3_P via 47R;\n"
            "monitoring only (no local trip in the spec). Open NTC reads 2.5 V like -40 C: firmware plausibility check\n"
            "(winding colder than the heatsink at load = fault). Fan law and hot-spot limit in firmware."
            % (gdrv.ohm(R_L_BIAS), DVDT_MAX))
    B.R(gdrv.ohm(R_L_BIAS), "REF2V5", "T_L", tol="0.1%")
    B.C("100n", "T_L", "AGND")
    B.R("47R", "AN3_BUF", "AN3_P")
    B.block("NTC pair: second heatsink NTC", "Floating pair, biased and read on CTRL-C2000 (10.0k from 2.5 V).")
    B.part("B57703M", {"1": "NTC_P", "2": "NTC_N"})

    # ---------------------------------------------------------------- 06-09 gate drive, one channel per sheet
    for no, s in enumerate(POS, 6):
        drain, _, other = POS[s]
        B.new_sheet("%02d_gd_%s" % (no, s.lower()), "Gate drive %s (%s)" % (s, "leg " + ("A" if s in ("S1", "S2") else "B")),
                    "gdrv.channel() rev 5: NSI6651ASC-Q1 + UCC14241-Q1 +%g/-%g V,\n%g/%g ohm + Kelvin R per device, AO3400A "
                    "clamps,\nSC booster %gR, interlock with PWM%d, DESAT to the drain" % (GATE_V + R_GATE + (R_SB, other)))
        ch = gdrv.channel(B, s, pwm="PWM%d" % int(s[1]), en="EN_GD", flt_n="DRV_FLT_N", rdy="RDY",
                          gate=tuple("G_%s%s" % (s, d) for d in "ab"[:N_PAR]),
                          source=tuple("KS_%s%s" % (s, d) for d in "ab"[:N_PAR]), drain=drain,
                          vdd="VDD_" + s, vee="VEE_" + s, interlock="PWM%d" % other, vin="+24V", vcc="+5V",
                          gnd="GND", gate_v=GATE_V, r_on=R_GATE[0], r_off=R_GATE[1], vclass=VCLASS, r_sb=R_SB,
                          v_peak=V_PEAK, deadtime=(DT_R, 100e-12))
        ms = gdrv.MILLER_SIM["%d x %s" % (N_PAR, DEV["mpn"])]
        B.block("%s: layout rules + Miller margin" % s,
                "SYMMETRY: gate and Kelvin of %sa/%sb as coupled pairs of equal length (+/-10 %%) from the COM_%s star; Ron,\n"
                "Roff, Kelvin R, 10k, Zeners at each device; power sources joined only by the busbar. CLAMP LOOP: each\n"
                "AO3400A + C_VL at its device's gate-Kelvin pins, loop <= %.0f nH. IC-22: milled slot between the drain\n"
                "pin 1 and pins 2-4 of each TO-247-4L; DESAT string >= 2.5 mm pad gap per diode; board coated to PD1.\n"
                "Miller hold (gdrv rev 5, ngspice 1100 V / 72.4 A, %.0f V/ns): die %+.2f V vs VGS(th) min %.2f V at 175 C,\n"
                "margin %.2f V (rev 4 PNP: %+.2f V) - BENCH TEST (double pulse, hot, minimum-threshold devices)."
                % (s, s, s, gdrv.L_CLAMP_MAX * 1e9, ms["dvdt"], ms["die_l1"], gdrv.SC40["vth175"],
                   gdrv.SC40["vth175"] - ms["die_l1"], ms["rev4"]))
        gd.update({net: "GD_" + s for net in ch.sec})
        iso |= set(ch.iso)
        xing |= set(ch.xing)
    return B, hv, gd, iso, xing


# ------------------------------------------------------------------------------------------------ design check (2/2)
def check_drawing(B, out, N):
    """The drawing equals the spec: counts, MPNs and values read back from the built design."""
    say = lambda k, s, *a: out.__setitem__(k, s % a)
    parts = list(B.D.parts.values())
    key = lambda p: p.lib_id.split(":")[1]
    for s, (drain, source, _) in POS.items():
        devs = [p for p in parts if key(p) == "SG2M040170HJ" and p.pins["1"] == drain and p.pins["2"] == source]
        assert len(devs) == N_PAR, "%s: %d devices drawn" % (s, len(devs))
        for dev in devs:
            g = dev.pins["4"]
            ron = [val(p.value) for p in parts if key(p) == "R" and set(p.pins.values()) == {"OUTH_" + s, g}]
            roff = [val(p.value) for p in parts if key(p) == "R" and set(p.pins.values()) == {"OUTL_" + s, g}]
            assert (ron, roff) == ([R_GATE[0]], [R_GATE[1]]), "%s gate resistors %s/%s != spec" % (g, ron, roff)
    for p_, bus in (("port_A", "A_BUS+"), ("port_B", "B_BUS+")):
        n45 = sum(1 for p in parts if key(p) == "C4AQ_45U" and set(p.pins.values()) == {bus, "BUS-"})
        n1 = sum(1 for p in parts if key(p) == "C4AQ_2U2" and set(p.pins.values()) == {bus, "BUS-"})
        assert (n45, n1) == (spec("capacitors", p_, "count"), DEC_N), "%s bank drawn %d + %d" % (p_, n45, n1)
    for bus in ("A_BUS+", "B_BUS+"):
        n = sum(1 for p in parts if key(p) == "WND75P16W6" and p.pins == {"1": bus, "3": bus, "2": "BUS-"})
        assert n == spec("bus_reverse_clamp", "devices_per_bank"), "%s reverse clamp drawn %d" % (bus, n)
    ids = [p.value for p in parts if key(p) == "R" and set(p.pins.values()) == {"ID", "AGND"}]
    assert ids == [IF.ID_OHM[PROJECT]], "ID resistor"
    on24 = [p for p in parts if key(p) in ("C", "CB100") and set(p.pins.values()) == {"+24V", "GND"}]
    c24 = sum(val(p.value) if key(p) == "C" else 100e-6 for p in on24)
    c_eff = sum(0.5 * val(p.value) if key(p) == "C" else 0.8 * 100e-6 for p in on24)   # MLCC at 24 V / ZLH -20 %
    assert c24 * 1.2 <= IF.CELL_24V_MAX_UF * 1e-6, "+24V capacitance above the CELL contract"
    t_hold = (N["uv_fall"][0] - UCC14["uv_fall"][1]) * c_eff / N["i_24"]
    assert t_hold >= 2 * (TPS37["tpd"] + NSI["t_rst"][1] + T_DRV), "+24V hold-up after the UV trip"
    say("draw", "Drawing = spec: %d x %d SG2M040170HJ, Ron/Roff %g/%g ohm on every gate, banks %d + %d decoupling per "
        "port, ID %s; +24V capacitance %.1f uF (+20 %% <= %d uF); hold-up from the UV trip to the bias UVLO >= %.0f us "
        "(MLCC 50 %% at 24 V ASSUMED, ZLH -20 %%, %.2f A) vs %.0f us needed (TPS3710 tpd 18 us typ)", len(POS), N_PAR,
        R_GATE[0], R_GATE[1], spec("capacitors", "port_A", "count"), DEC_N, IF.ID_OHM[PROJECT], c24 * 1e6,
        IF.CELL_24V_MAX_UF, t_hold * 1e6, N["i_24"], 2 * (TPS37["tpd"] + NSI["t_rst"][1] + T_DRV) * 1e6)


def cover(N):
    """Cover-sheet notes, one A3 line each (<= ~230 characters so they clear the title block)."""
    ms = gdrv.MILLER_SIM["%d x %s" % (N_PAR, DEV["mpn"])]
    lines = [
        "CALCULATED, not measured: design_check() asserts every number on each build (outputs/%s_design_check.txt); "
        "component values are read from sim/out/pv_design/cell_spec.json." % PROJECT,
        "Local trips: inductor OC +/-%.1f A (%.1f-%.1f A), gates off %.2f us (PWM) / %.2f us (EN) vs %.2f us allowed; "
        "heatsink OT %.1f C (%.1f-%.1f C), open probe trips; DESAT per driver; CTRL backup peak %.1f A." % (
            N["i_trip"][0], N["i_trip"][1], N["i_trip"][2], N["t_pwm"] * 1e6, N["t_en"] * 1e6, N["t_req"] * 1e6,
            N["t_ot"][0], N["t_ot"][1], N["t_ot"][2], N["pk_bk"]),
        "AN1 = LEM LA 150-P on inductor lead A: %.1f mV/A differential, CM 1.25 V, < 1 ohm, linear +/-%.0f A, %.2f us, "
        "%.4f A/LSB at the CTRL. Bus OV: no local comparator (CTRL path %.0f us -> %.0f V, %.3f x V_DSS)." % (
            K_AN1 * 1e3, N["lin"], N["t_sense"] * 1e6, N["lsb"], N["t_path"] * 1e6, N["v_bus"], N["v_dev"] / DEV["vdss"]),
        "Fail-safe off: open, unplugged, unpowered or tri-stated pairs read <= %.2f V = LOW; RDY push-pull; +24V removal "
        "drops RDY at >= %.1f V; +3V3 or +5V loss -> drivers off (VCC1 UVLO / EN low). No bootstrap: 100 %% duty OK." % (
            max(N["vid_open"], N["vid_unpowered"]), N["uv_fall"][0]),
        "Domains: PELV, HV, GD_S1..S4, PE. Isolators: NSI6651ASC-Q1 + UCC14241-Q1 x 4, LA 150-P, L_CELL. CONFORMAL COATING "
        "to PD1 (IEC 60664-3) over every HV-PELV barrier part and the power stage (IC-17, IC-22). 1000 V DC (ECO-10).",
        "LAYOUT: loop <= 20 nH, bank <= %.0f nH from each leg, damper loop <= 3 nH, clamp branch <= %.1f nH, Miller clamp "
        "loop <= 1 nH, drain-pin slot. BENCH: Miller margin %.2f V, LA 150-P dv/dt, damper power, pad PD test." % (
            L_BUS_REQ * 1e9, spec("bus_reverse_clamp", "branch_inductance_max_nH"), gdrv.SC40["vth175"] - ms["die_l1"]),
    ]
    assert max(len(x) for x in lines) <= 235, "cover note too long for one A3 line"
    return lines


def design_check(B):
    """Every number behind this board, asserted on each build against cell_spec.json and the datasheets (calc), then
    the built drawing read back against the spec (check_drawing). Returns ({key: line}, numbers)."""
    lines, N = calc()
    check_drawing(B, lines, N)
    return lines, N


# ------------------------------------------------------------------------------------------------ main
WAIVERS = {"ground_pin_not_ground": "NSI6651ASC pin 3 is named GND2 in its datasheet (p3) but is the isolated driver-side "
                                    "reference = the COM_Sx star of the devices' Kelvin sources (floating gate-drive "
                                    "domain), deliberately not the board GND (same waiver as gen/gdrv.py GDRV-HB)"}


def vranges():
    """DC net ranges (V, against the net's own domain reference) for dcdclib's part-stress check: gdrv.net_ranges() per
    channel with its logic side moved to +5V, plus this board's nets. Switching / gate / pulse nets stay unranged."""
    r = {}
    for s in POS:
        r.update(gdrv.net_ranges(s, GATE_V, "VDD_" + s, "VEE_" + s, tuple("KS_%s%s" % (s, d) for d in "ab"[:N_PAR]),
                                 gnd="GND", vcc="+5V", vin="+24V"))
        r.update({"%s_%s" % (x, s): (0.0, V5[1]) for x in ("DTS", "DTB", "PG")})
    l3, l5, ref, im = (0.0, 3.47), (0.0, V5[1]), (0.0, LM40["v"] * (1 + LM40["tol"])), I_TRIP * K_M
    r.update({"+24V": V24, "+24V_S": V24, "+3V3": (3.135, 3.47), "+5V": V5, "GND": (0.0, 0.0), "AGND": (0.0, 0.0),
              "PE": (0.0, 0.0), "+15V": (14.7, 15.3), "-15V": (-15.3, -14.7), "VCC_3V3": (0.0, 5.5), "VCC_5V": (0.0, 5.5),
              "FB_3V3": (0.985, 1.015), "FB_5V": (0.985, 1.015), "UV_SNS": (0.0, V24[1] * UV_DIV[1] / sum(UV_DIV)),
              "S15_SNS": (0.0, 15.3 * S15_DIV[1] / sum(S15_DIV)), "A_BUS+": (0.0, V_OVP), "B_BUS+": (0.0, V_OVP),
              "BUS-": (0.0, 0.0), "IM": (-im, im)})
    r.update({n: l3 for n in ["EN_DRV", "EN_P", "EN_N", "ID", "AN_INV", "AN_REF", "AP_INV", "AP_NI", "VMID_DIV", "OC_SE"] +
              ["PWM%d_%s" % (k, x) for k in range(1, 9) for x in "PN"]})
    r.update({n: l5 for n in ("EN_GD", "DRV_FLT_N", "RDY", "PWM1", "PWM2", "PWM3", "PWM4")})
    r.update({n: ref for n in ("AN1_P", "AN1_N", "AN2_P", "AN3_P", "VMID", "TH_HI", "TH_LO", "TH_OT", "TH_OPEN", "T_HS",
                               "T_L")})   # AN2_BUF/AN3_BUF unranged: the 47R between buffer and line carries <= 1.7 mA
    r["REF2V5"] = (LM40["v"] * (1 - LM40["tol"]), ref[1])
    return r


def domain_for(hv, gd):
    return lambda net: "HV" if net in hv else "PE" if net == "PE" else gd.get(net, "PELV")


if __name__ == "__main__":
    B, hv, gd, iso, xing = build_design()
    lines, N = design_check(B)
    B.D.root_notes = cover(N)
    for line in lines.values():
        print(line)
    rc = L.build(B, waivers=WAIVERS, domain_of=domain_for(hv, gd), isolators=iso, crossings=xing, vrange=vranges().get)
    with open(os.path.join(L.REPO, "hardware", PROJECT, "outputs", PROJECT + "_design_check.txt"), "w") as f:
        f.write("CALCULATED by gen/pvcell.py design_check() - not measured, not bench-validated.\n")
        f.write("\n".join(lines.values()) + "\n")
    sys.exit(rc)
