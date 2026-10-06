"""AUX-HV - HV bootstrap auxiliary supply (REQUIREMENTS ECO-06, candidate PV-C2): design calculation, ngspice
switching-level cross-check and the schematic hand-off sim/out/aux_hv_design/aux_hv_spec.json (read by gen/aux_hv.py).

    .venv/bin/python sim/aux_hv_design.py

Topology: single-switch flyback, fixed 65 kHz, DCM at every input voltage and full load, peak current mode
(UCC28C59-Q1: UVLO 16/12.5 V for SiC, internal T flip-flop -> duty < 48 %), 1700 V SiC C3M0900170J, TVS clamp
(BYG10Y slow diode + 3 x SMCJ70A), secondary-side regulation through a reinforced optocoupler (ATL431 + CNY65B,
"fast lane" type II, R-C feed-forward against integrator wind-up) with an auxiliary-winding over-voltage loop on the
controller's own error amplifier as a backup, 3-FET depletion cascode HV start-up (BSS126) on a compensated resistive
tap string that also feeds the line brown-in detector (TPS3710) and the input over-voltage lockout, VDD from the aux
winding through a BC817 follower, TPS3700 window detector driving PG_HVAUX actively high through a Schmitt buffer.

Rev C (sourcing policy D-031, SYS-IO-AUX rev E feed, insulation D-032 / IC-15, IC-17):
  switch    InventChip IV2Q171R0D7Z (AEC-Q101), alternate Sichain SG2M1K0170J2J (same TO-263-7 pin-out); losses,
            thermal, gate drive and start-up computed for both, the design uses the worse value of each
  VDD       follower reference BZX84-A18 (gate 15.5-18.1 V: inside the 15-18 V recommended on-voltage of both
            Asian parts), clamp BZX84-B20
  SYS rev E LM74800 feed with a 1 k + 2.2 uF HGATE ramp and the hardware load inhibit: black start, cabinet
            hand-over and the 200 s port-hold case re-run against hardware/SYS-IO-AUX/outputs/SYS-IO-AUX_aux_feed.json
  T1        coordinated insulation levels (PD test on every unit, 4400 V rms, 8 kV), core insulation, 2 tapes S-P
Rev B (reviews AX-01..05, INT-08..13; SYS-IO-AUX rev D):
  input     per port 2 x 22 R pulse-rated wirewound AHEAD of the 1 A / 1000 VDC fuse (fault current <= 25 A, so the
            fuse never sees the port prospective current), BYG10Y OR, 2 x 3.3 uF / 1300 V film (RC surge absorber)
  OV lock   TLV3202 channel 1 on the compensated string tap: stops switching above ~1.13 kV (port SPD let-through)
  foldback  aux-plateau detector + 2 x 2N7002: adds 10 nF on RT/CT below ~8 V output (fsw / 11): no current staircase
  overload  TLV3202 channel 2 + RC on COMP: latch-off -> hiccup after 0.1-0.2 s above the 30 W level
  PG        active-high push-pull (SN74LVC1G17 with Ioff, 3.3 V Zener rail); dead / unplugged reads low (SYS 100k)
  builds    PV: everything fitted. DAB-D60: port-B input parts not fitted (port 1 only, D-021)

Everything here is CALCULATED or SIMULATED, nothing is measured. Our assumptions are tagged A-n and printed in
report.md; datasheet numbers carry (document, page) in the comment next to them.
"""
import csv
import json
import math
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
from scipy.optimize import brentq, fsolve
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out", "aux_hv_design")
SPICE_DIR = os.path.join(HERE, "spice")
MU0 = 4e-7 * math.pi
T_AMB = 60.0                                        # module internal ambient (PV-20)

# ============================================================================================ specification (brief)
VIN_MIN, VIN_MAX, VIN_TR = 200.0, 1000.0, 1100.0   # regulate 200-1000 V continuous, survive 1100 V transient
VIN_START_MAX = 250.0                               # start at or below (PV-C2 / competitor PV start-up voltage)
VOUT, POUT = 22.8, 30.0                             # set point below 24 V so the cabinet supply carries the load
IOUT = POUT / VOUT
RIPPLE_MAX, REG_TOL, ETA_MIN = 0.240, 0.03, 0.80
DERATE = {"cont": 0.80, "trans": 0.90}              # our rule: V_DS(pk) <= 80 % V(BR)DSS at 1000 V, <= 90 % at 1100 V
VIN_TABLE = [200.0, 400.0, 600.0, 800.0, 1000.0]

# ============================================================================================ datasheet data
# UCC28C59-Q1 = UCC28C5x-Q1 family, TI SLUSEV2C (docs/datasheets/power-supply/UCC28C58-Q1.pdf)
UCC = dict(vddon=(14.8, 16.0, 17.2), vddoff=(12.0, 12.5, 13.0), hyst_min=2.8,       # p8 UVLO, C58/C59 rows
           istart=75e-6, ivdd=(1.3e-3, 2.0e-3), dmax=(0.47, 0.48),                  # p8
           vref=(4.82, 5.0, 5.18), vfb=(2.45, 2.50, 2.55),                          # p7 total variation
           ea_src=(0.5e-3, 1.0e-3), ea_sink_min=2e-3, voh_drop=0.2,                 # p7 (no max source given; VOH >= VREF-0.2)
           acs=(2.75, 3.0, 3.15), vcs=(0.9, 1.0, 1.1), comp_off=1.15, td=(35e-9, 70e-9),   # p7 current sense
           idis=(7.2e-3, 8.4e-3, 9.5e-3), osc_hi=2.5, osc_lo=0.7,                    # p7 discharge; p20 thresholds
           vdd_abs=30.0, vdd_roc=28.0, analog_abs=6.3)                               # p5, p6
# 1700 V SiC switch (rev C, D-031): both candidates pin-compatible with the rev B C3M0900170J (1 G, 2 Kelvin source,
# 3-7 source, tab drain). R at VGS 15 V (our gate 15.5-18.1 V); 'hot' = 175 C typical, max scaled by max/typ at 25 C.
# vth_hot: typical at 150 C read off the curve; vth_min_hot = vth_min(25 C) shifted by the typical drop (A-30).
DEVICES = {
    # InventChip IV2Q171R0D7Z Rev1.2 Dec 2023 (docs/datasheets/power-semiconductors/IV2Q171R0D7Z.pdf): p1 1700 V,
    # VGS transient -10..23 V, recommended on 15-18 V / off -5..-2 V, IDM 15.7 A, RthJC 2.05 K/W, Tj 175 C, AEC-Q101,
    # pins p1; p2 VTH 1.8/3.0/4.5 V (25 C), RON @15 V 950/1250 mOhm (25 C), 1450 (175 C), Ciss 285, Coss 15.3, Crss
    # 2.2 pF @1000 V, Eoss 11 uJ @1000 V, Qg 16.5 nC, Rg 13 R, EOFF 17.0 uJ @1000 V 2 A RG(ext) 10 R; p4 Fig. 9 VTH
    # typ 2.2 V @150 C, Fig. 8 transfer 2 A at ~7.9 V (175 C; VTH 2.1 V); p6 Fig. 17 Eoss curve (read off, scaled
    # to the p2 table point), Fig. 21 EOFF ~16 uJ @ 1 A
    "IV2Q171R0D7Z": dict(maker="InventChip", vbr=1700.0, vgs_rec=(15.0, 18.0), vgs_abs=(-10.0, 23.0), idm=15.7,
                         rds25=(0.950, 1.250), rds175_typ=1.450, qg=16.5e-9, rg_int=13.0, ciss=285e-12, crss=2.2e-12,
                         co_tr=15.3e-12, eoff=(16e-6, 1000.0, 1.0, 10.0), rth_jc=2.05, tj_max=175.0,
                         vth=(1.8, 3.0), vth_hot=2.2, k_ch=2.0 / (7.9 - 2.1) ** 2, qual="AEC-Q101 (datasheet p1)",
                         eoss_v=(0, 200, 400, 600, 800, 1000), eoss_j=(0, 1.08e-6, 2.6e-6, 4.75e-6, 7.55e-6, 11.0e-6),
                         eoss_exp=1.69, ds="IV2Q171R0D7Z.pdf p1, p2, p4 Fig. 9, p6 Fig. 17/21"),
    # Sichain SG2M1K0170J2J V01_03 2026-06-25 (docs/datasheets/power-semiconductors/SG2M1K0170J2J.pdf): p3 VGS max
    # -8/+22 V, pulse -10/+25 V, recommended -4 / +15..18 V, ID(pulse) 11.4 A, RthJC 2.2 typ / 2.8 max K/W, Tj 175 C;
    # p4 VGS(th) 2.5/3.1/4.0 V (25 C), RDS(on) @15 V 1190/1700 mOhm (25 C), 2200 (175 C), Rg,int 19 R, Ciss 109,
    # Coss 8.7, Crss 1.4 pF @1400 V, Eoss 7.5 uJ @1400 V; p5 Qg 7.3 nC, EOFF 1.2 uJ @1200 V 2 A Rg 2.5 R; p7 Fig. 11
    # VTH typ 2.45 V @150 C, Fig. 7 transfer 1 A at ~7.5 V (175 C; VTH 2.3 V); p8 Fig. 16 Eoss curve (read off),
    # Fig. 18 Coss ~9.5 pF @1000 V; pins p1. No
    # qualification statement in the datasheet.
    "SG2M1K0170J2J": dict(maker="Sichain", vbr=1700.0, vgs_rec=(15.0, 18.0), vgs_abs=(-8.0, 22.0), idm=11.4,
                          rds25=(1.190, 1.700), rds175_typ=2.200, qg=7.3e-9, rg_int=19.0, ciss=109e-12, crss=1.4e-12,
                          co_tr=9.5e-12, eoff=(1.2e-6, 1200.0, 2.0, 2.5), rth_jc=2.8, tj_max=175.0,
                          vth=(2.5, 3.1), vth_hot=2.45, k_ch=1.0 / (7.5 - 2.3) ** 2, qual="none stated in the datasheet",
                          eoss_v=(0, 200, 400, 600, 800, 1000, 1200, 1400, 1600),
                          eoss_j=(0, 0.4e-6, 1.1e-6, 2.0e-6, 3.0e-6, 4.3e-6, 5.8e-6, 7.5e-6, 9.4e-6),
                          eoss_exp=1.6, ds="SG2M1K0170J2J.pdf p1, p3-p5, p7 Fig. 11, p8 Fig. 16/18"),
}
SW_MAIN, SW_ALT = "IV2Q171R0D7Z", "SG2M1K0170J2J"
for _d in DEVICES.values():
    _d["vth_min_hot"] = _d["vth"][0] - (_d["vth"][1] - _d["vth_hot"])           # A-30
MOS = None                       # set below to the device with the larger switch loss (design for the worse)
# Vishay VS-8ETU04S-M3 doc 96388 (output rectifier): p1 VRRM 400 V, VF 0.94 V typ @ 8 A 150 C, 1.19/1.30 @ 8 A 25 C;
# p2 Fig. 1 read off at 1 A: ~0.62 V (150 C), ~0.95 V (25 C); p2 RthJC 2.0 K/W; p3 Fig. 3 CT ~17 pF @ 200 V
DOUT = dict(vrrm=400.0, hot=(0.62, 0.94), cold=(0.95, 1.19), cold_max8=1.30, rth_jc=2.0, cj=17e-12, tj_max=175.0)
# Vishay BYG10Y doc 88957: p1 VRRM 1600 V, IF(AV) 1.5 A, IFSM 30 A; p2 VF <= 1.1 V @ 1 A, IR <= 10 uA @ 100 C,
# trr <= 4 us (slow, as TI SLUSEV2C p31 recommends for the clamp), RthJA 125 K/W on 50 mm2 Cu
BYG = dict(vrrm=1600.0, ifav=1.5, ifsm=30.0, vf1=1.1, ir100=10e-6, rth_ja=125.0)
# Bourns SMCJ series (docs/datasheets/protection/SMCJ_Bourns.pdf): p2 SMCJ70A VBR 77.8-86.0 V @ 1 mA, VRWM 70 V,
# VC 113 V @ IPP 13.3 A (10/1000 us); p1 1500 W, PM(AV) 5 W @ TL = 75 C (=> RthJL 15 K/W to 150 C), TJ max 150 C, SMC.
# Rev C1: 3 x SMCJ70A (233-258 V) replace 2 x SMCJ100A (222-246 V): the design M1 transformer has 21 % more leakage;
# three packages share the clamp heat (A-9) for +12 V of drain stress
TVS = dict(vbr=(77.8, 86.0), vc=113.0, ipp=13.3, pm_av=5.0, tl=75.0, n=3, tj_max=150.0, rth_board=40.0, rth_jl=15.0)
# Diodes Inc US1x DS16008 (US1M.pdf) p2: US1G VRRM 400 V, IO 1 A, VF <= 1.3 V @ 1 A, trr <= 50 ns
US1G = dict(vrrm=400.0, vf=1.3)
# Infineon BSS126 Rev 2.1: p1 VDS 600 V, VGS +/-20 V, pins 1 G 2 S 3 D; p2 VGS(th) -2.7/-2.0/-1.6 V @ 8 uA,
# IDSS >= 7 mA @ VGS 0, RthJA 250 K/W (minimal footprint), ID(off) <= 10 uA @ 125 C
BSS = dict(vds=600.0, vgs_max=20.0, vth=(-2.7, -2.0, -1.6), idss_min=7e-3, rth_ja=250.0, tj_max=150.0)
# TI TPS3710 SBVS271A p5 / TPS3700 SBVS187G p6: VIT+ 396/400/404 mV, VIT- 387/394.5/400 mV, I(SENSE) <= 25 nA
DET = dict(vitp=(0.396, 0.400, 0.404), vitn=(0.387, 0.3945, 0.400), isense=25e-9, vdd=(1.8, 18.0), vdd_abs=20.0)
# TI ATL431 (ATL431.pdf) p4 B grade: Vref 2.487/2.500/2.512 V; VI(dev) <= 15 mV (BI, -40..85 C, chosen because the
# CNY65 already limits the board to 85 C); Iref <= 150 nA; Imin <= 35 uA; dVref/dVKA <= 2 mV/V (36-10 V); p3 VKA <= 36 V
ATL = dict(vref=(2.487, 2.500, 2.512), vdev=0.015, iref=150e-9, imin=35e-6, dvka=2e-3, vka_max=36.0)
# Nexperia BC817-25 (BC817.pdf): p2 Table 3 pins 1 B, 2 E, 3 C; p3 VCEO 45 V, VEBO 5 V; p5 hFE 160-400 @ 100 mA;
# VBE drops ~2 mV/K. BZX84-B16 (BZX84.pdf p5 Table 8): VZ 15.70-16.30 V @ 5 mA, rdif 40 R @ 5 mA, 200 R @ 1 mA
BC817 = dict(vceo=45.0, vebo=5.0, hfe_min=160, vbe=(0.60, 0.72))
# Rev C: BZX84-A18 (BZX84.pdf p6 Table 8: 17.82-18.18 V @ 5 mA, SZ +12.4..+16.0 mV/K, rdif 45 R @ 5 mA) as the
# follower reference so that VGS stays inside 15-18 V for both Asian switches
ZREG = (17.82, 18.18)
ZREG_SZ = (12.4e-3, 16.0e-3)
T_LOCAL = (-40.0, 85.0)                               # primary control area, local (A-28)
# Nexperia BAT54 (BAT54.pdf) p4 Fig. 2 (typical) reverse current at VR 15 V: ~1 uA @ 25 C, ~42 uA @ 85 C, ~330 uA @ 125 C
BAT54 = dict(ir25=1e-6, ir85=42e-6, ir125=330e-6)
# Nexperia BAS16 (BAS16.pdf) p2: IR <= 30 nA @ 25 V (25 C), <= 30 uA @ 25 V (Tj 150 C); Table 3 SOT23 1 A, 2 n.c., 3 K.
# Used as the overload-latch diode on the high-impedance timer node (a BAT54 there would leak ~20 uA at 85 C)
BAS16 = dict(ir25=30e-9, ir150=30e-6)
# Nexperia BZX84 (BZX84.pdf) p5 Table 8: B3V3 3.23-3.37 V @ 5 mA, rdif 95 R @ 5 mA / 600 R @ 1 mA; B6V8 6.66-6.94 V
# @ 5 mA, SZ +1.2..+4.5 mV/K; p9 Fig. 8 (typ, 25 C): 6V8 reverse current ~1 uA at 6.0 V, ~10 uA at 6.6 V
Z33 = dict(vz=(3.23, 3.37), rdif5=95.0, rdif1=600.0)
Z68 = dict(vz=(6.66, 6.94), vz_ua=(6.0, 6.9))      # vz_ua: our band at 3-7 uA (Fig. 8 typ, B grade, -40..85 C; A-27)
# Nexperia 2N7002BK (2N7002BK.pdf) p6: VGS(th) 1.1-2.1 V @ 250 uA; Ciss 33/50 pF typ/max, Coss 7 pF typ (VDS 10 V)
N7002 = dict(vth=(1.1, 2.1), ciss=(33e-12, 50e-12), coss=7e-12)
# TI TLV3202 (TLV3202.pdf): p3 DGK pins 1 1OUT, 2 1IN-, 3 1IN+, 4 GND, 5 2IN+, 6 2IN-, 7 2OUT, 8 VCC; p4 VS 2.7-5.5 V;
# p5 (VCC 5 V): VIO <= 5 mV, input hysteresis 1.2 mV, VCM -0.2..VCC+0.2 V, VOL <= 225 mV @ 4 mA sink, VOH >= VCC-170 mV
# @ 4 mA source, ISC >= 40 mA sink, IQ <= 65 uA per channel, tPD <= 55 ns (20 mV overdrive)
TLV = dict(vos=5e-3, hys=1.2e-3, vol=0.225, voh_drop=0.170, isc=40e-3, iq=65e-6, tpd=55e-9, vcc=(2.7, 5.5))
# TI SN74LVC1G17 (SN74LVC1G17.pdf): p1 Ioff, ICC <= 10 uA; p3 DBV pins 1 NC, 2 A, 3 GND, 4 Y, 5 VCC; p5 VCC 1.65-5.5 V;
# p6 VOH >= VCC - 0.1 V @ IOH -100 uA
LVC = dict(vcc_min=1.65, voh_drop=0.1, icc=10e-6)
# TI LM7480-Q1 (LM7480-Q1.pdf, the SYS-IO-AUX rev D feed-3 controller): p5 V(UVLOF) 1.091-1.159 V falling; p7 EN low ->
# HGATE off 3-6 us, enable -> DGATE on 98-270 us. Divider 100k / 6.34k 0.1 % from gen/sys_io_aux.py AUX_DIV["en"]
LM748 = dict(uvlof=(1.091, 1.159), t_off=6e-6, t_on=270e-6, div=106.34 / 6.34)
# SYS-IO-AUX rev D (coordinator, 2026-10-04): AUX-HV feed = LM74800 + back-to-back FETs; feed UVLO 19.98-21.32 V;
# feed OV cut-off 23.97-25.57 V, LATCHED until AUX-HV is power-cycled (re-arm 21.88-23.39 V); held off while a
# cabinet feed is above 20.5-21.1 V; at cabinet drop-out the feed picks up ~34 W within 0.5 ms and the bus must stay
# above 19.07 V (hand-over margin 0.34 V); 2 mF electrolytic bus; window 21.6-26.0 V; bus OV cut-off 26.13-26.42 V;
# PG_HVAUX 100k pull-down. Loads (INT-09 / D-026, fans off by default): 13 W standby + comms; eFuse UVLO 16.1/15.0 V
SYS = dict(c_bus=2.0e-3, esr_bus=0.05, win=(21.6, 26.0), ov_cut=(26.13, 26.42), uv_trip=(18.3, 19.1),
           feed_uvlo=(19.98, 21.32), feed_ov=(23.97, 25.57), feed_rearm=(21.88, 23.39), prio=(20.5, 21.1),
           handover_p=34.0, handover_t=0.5e-3, handover_min=19.07, handover_bus=19.07 + 0.34,
           efuse_on=16.1, efuse_off=15.0, p_standby=13.0, p_hi=POUT, p_logic=0.5, pd_pg=100e3)
SYS["feed_uvlof"] = (LM748["uvlof"][0] * LM748["div"] * 0.998, LM748["uvlof"][1] * LM748["div"] * 1.002)
# SYS-IO-AUX rev E (final for the feed): hardware/SYS-IO-AUX/outputs/SYS-IO-AUX_aux_feed.json (calculated by
# gen/sys_io_aux.py). Replaces the rev D numbers above wherever both exist.
FEED_JSON = os.path.join(REPO, "hardware", "SYS-IO-AUX", "outputs", "SYS-IO-AUX_aux_feed.json")
SYSE = json.load(open(FEED_JSON))
SYS.update(feed_uvlo=tuple(SYSE["feed"]["uvlo_rising_V"]), feed_uvlof=tuple(SYSE["feed"]["uvlo_falling_V"]),
           feed_ov=tuple(SYSE["feed"]["ov_rising_V"]), feed_rearm=tuple(SYSE["feed"]["ov_rearm_V"]),
           prio=tuple(SYSE["feed"]["cabinet_healthy_rising_V"]), cab_lost=tuple(SYSE["feed"]["cabinet_lost_falling_V"]),
           c_bus_range=tuple(SYSE["hgate"]["bus_capacitance_F"]), ramp=tuple(v * 1e3 for v in SYSE["hgate"]["ramp_V_per_ms"]),
           vgs_on=SYSE["hgate"]["VGS_on_V"], t_hg_en=SYSE["handover"]["t_hgate_enabled_s"],
           t_drv=270e-6 + 110e-9 * 7.9 / 1.3e-3,                                     # SYS check_ramp T_DRV
           logic_uvlo=SYSE["handover"]["logic_uvlo_V"], handover_min=SYSE["handover"]["logic_uvlo_V"],
           p_logic=SYSE["load_post_inhibit"]["rows"][0][1] + SYSE["load_post_inhibit"]["rows"][1][1],
           p_feed=SYSE["load_post_inhibit"]["rows"][2][1], p_ctrl=SYSE["load_post_inhibit"]["rows"][3][1],
           i_port=0.6, i_port2=0.7, t_port=SYSE["load_post_inhibit"]["port_hold_s"],
           p_total=SYSE["load_post_inhibit"]["total_W"], p_noport=SYSE["load_post_inhibit"]["without_port_hold_W"],
           ef_r=(1.176 * (1 + 100 / 8.06), 1.224 * (1 + 100 / 8.06)), ef_f=(1.09 * (1 + 100 / 8.06), 1.15 * (1 + 100 / 8.06)),
           sys_black=SYSE["black_start"], sys_hand=SYSE["handover"])
SYS["p_standby"] = SYS["p_noport"]                    # ~14 W post-inhibit without a port holding its contactors
SYS["p_hold2"] = SYS["p_logic"] + SYS["p_feed"] + SYS["p_ctrl"] + SYS["i_port2"] * 23.15   # 30.1 W, <= 200 s
# Vishay CNY65 doc 83540 Rev 2.4: p1 VIORM 1800 Vpk, DTI >= 3 mm, CTR B-bin 100-200 % @ IF 10 mA (p3);
# p2 Tamb -55..+85 C, IF <= 75 mA, VCEO 32 V; p4 creepage/clearance >= 14 mm, CTI 200 (group IIIa), VIOTM 12 kV;
# p5 Fig. 10/11: typical CTR at IF 1-2 mA is ~0.6 x the 10 mA value; Fig. 8: NCTR 0.88 @ -30 C, 0.92 @ 75 C;
# p2 cut-off 110 kHz with RL = 100 R (no RL = kOhm figure given)
OPTO = dict(viorm=1800.0, dti=3.0, creep=14.0, cti=200, ctr10=(1.0, 2.0), lowif=0.6, temp=(0.88, 1.0),
            fc_100r=110e3, tamb_max=85.0, if_max=75e-3)
# TDK ETD 29/16/10 (ETD29-16-10.pdf) p2: Ae 76.0 mm2, Amin 71.0, le 70.4 mm, Ve 5350 mm3; N87 gap/AL formula
# AL = 124 * s^-0.7 nH (p3, 0.1 < s < 2 mm); p4 coil former B66359B1013T001: winding width >= 19.4 mm, AN 97 mm2,
# lN 52.8 mm, tube <= 11.8 mm, flange 21.8 mm, 13 pins, pin rows 25.4 mm apart, GFR PBT class F 155 C
CORE = dict(ae=76.0e-6, amin=71.0e-6, le=70.4e-3, ve=5350e-9, bw=19.4e-3, an=97e-6, ln=52.8e-3, tube=11.8e-3,
            flange=21.8e-3, k1=124.0, k2=-0.7)
# TDK N97 (N97.pdf) p2: Bs 410 mT @ 100 C; Pv @ 100 C: 45 kW/m3 (25 kHz, 200 mT), 300 (100 kHz, 200 mT),
# 340 (300 kHz, 100 mT), 205 (500 kHz, 50 mT)
N97 = dict(bs100=0.410, pv=[(25e3, 0.2, 45e3), (100e3, 0.2, 300e3), (300e3, 0.1, 340e3), (500e3, 0.05, 205e3)])
# Rev C (D-031): Jianghai PCV1VVF101MB70FV-WE3, PC HVF "VF" series (Jianghai-JE26-Polymer.pdf p16-17): 100 uF 35 V,
# ESR <= 30 mOhm @ 100 kHz 20 C, ripple 2.8 A rms @ 100 kHz <= 105 C, x0.70 for 10-100 kHz, 3000 h @ 105 C, surge
# 1.15 x UR, 8 x 6.7 mm SMD (replaces Wurth 875115655003: same ESR / ripple, 5000 h).
# Rev C1: PCV1HVF101MB12FV-WE3, same series (p17): 100 uF 50 V, ESR <= 25 mOhm, 3.8 A rms, 8 x 12.2 mm - the M1
# transformer's leakage moves the INT-08 backup band top to ~31 V, above 85 % of 35 V
CPOL = dict(c=100e-6, v=50.0, esr=0.025, irip=3.8 * 0.70, life_h=3000.0)
# Schurter ASO 0090.1001 (ASO-0090.1001.pdf): p1 1000 VDC gPV, 20 kA; p3 1 A, 602 mW @ In, melting I2t 0.554 A2s
# (at 20 kA), time-current curve: 1 A link melts in 10 ms at ~3.2 A; p4 note 1: 20 kA @ 1000 VDC, L/R < 2 ms
FUSE = dict(vdc=1000.0, i_n=1.0, p_in=0.602, i2t=0.554, ibreak=20e3)
# Vishay Draloric AC10 (AC-AC-AT-AC-NI.pdf): p1 10 W @ 40 C / 8.4 W @ 70 C, 0.22-560 R; p2 part number AC10000002209JAB00
# = 22 R 5 %, packaging AB; p6 AC10 adiabatic pulse energy ~2.6 Ws/Ohm at 22 R (read off, single pulse <= 10 ms);
# p7 AC10 peak pulse voltage 4500 V for ti <= 0.2 ms, ~4300 V at 1 ms (read off); p12 L 44 mm, D 8 mm
RLIM = dict(r=22.0, n=2, p70=8.4, e_per_ohm=2.6, vpk=4500.0, vpk_1ms=4300.0)
# Rev C (D-031): Jianghai FCSA3DS335, CBB 138 DS series (Jianghai-JE26-Film.pdf p28 series data, p30 table): 3.3 uF,
# UR 1300 V DC at <= 70 C hotspot / 1100 V at <= 85 C, peak current 264 A, dV/dt 80 V/us, ESR 15.2 mOhm, Ls 25 nH,
# 32 x 28 x 18 mm, pitch 27.5 mm; p28: voltage between terminals 1.5 x UR (20 C, 10 s), life >= 100 000 h at 70 C,
# IEC 61071 (replaces KEMET C4AQUBU4330A11J: same 1300 V / 3.3 uF / 27.5 mm, 29 V/us, 95 A)
CBULK = dict(c=3.3e-6, n=2, v70=1300.0, v85=1100.0, surge_k=1.5, dvdt=80.0, ipk=264.0)
# DEHN 952515 port SPD (sim/port_design.py SPD_UP, from the DEHN datasheet): protection level Up <= 4 kV, Ucpv 1000 V
SPD = dict(up=4000.0, ucpv=1000.0)
# Vishay CRHV2512 doc 68002: p1 1 W, 3000 V max working, 10M-1G +/-1 %; p2 VCR 10 ppm/V (10M-999M)
CRHV = dict(vmax=3000.0, p=1.0, vcr=10e-6)
# Nexperia BZX84-B20 (BZX84.pdf p6 Table 8): VZ 19.60-20.40 V @ 5 mA, SZ +14.4..+18.0 mV/K, rdif 55 R @ 5 mA
ZVDD = (19.60, 20.40)
ZVDD_SZ = (14.4e-3, 18.0e-3)

# ============================================================================================ assumptions (ours)
A = {
    "A-1": "Leakage inductance = 2.0 x the 1-D MMF estimate of the winding stack (terminations, uneven layers); "
           "3.0 x for the stress and clamp-power worst case. Not measured: a transformer sample is required.",
    "A-2": "Primary winding switched capacitance: parallel-plate estimate (eps_r 3.2) of the drain-end layer P1 to "
           "the PGND shield and of P2 to the aux layer, each weighted by the mean square of its own swing along the "
           "layer (P1 1.0 -> 0.5, P2 0.5 -> 0 of the drain swing).",
    "A-3": "TVS breakdown rises +0.1 %/K (no coefficient on the Bourns sheet); hot case = 100 C junction.",
    "A-4": "40 V drain overshoot above the TVS level: BYG10Y forward recovery (not specified) + clamp-loop inductance.",
    "A-5": "Output-rectifier ringing with the RC snubber adds 25 % to the ideal reverse voltage.",
    "A-6": "Aux tracking for the backup loop is CALCULATED (sec. 7): aux plateau = Na/Ns x (Vout + rectifier VF(i) + "
           "i x Rsec), US1G detector drop 0.55-0.80 V at mA, and 25-75 % of the primary clamp spike (Vclamp - VR) x "
           "Na/Np appearing on the aux for the leakage-reset time.",
    "A-7": "Optocoupler CTR at IF 1-5 mA for the B bin: 0.6 x (1.0..2.0), x 0.88..1.0 temperature, x 0.7 end of life; "
           "opto pole estimated as 110 kHz x 100 R / R_pullup (scaled from the only datasheet cut-off figure).",
    "A-8": "UCC28C59 error-amplifier source current up to 2 mA (datasheet gives min 0.5 / typ 1 mA, no max).",
    "A-9": "Thermal resistance to ambient through board copper: TO-263-7 and D2PAK 35 K/W, SMC clamp TVS 40 K/W lead-"
           "to-ambient on >= 4 cm2 2-oz copper per device (LAYOUT REQUIREMENT), transformer 21 K/W (53 x Ve[cm3]^-0.54 "
           "empirical) - estimates for a 2-oz board with local pours.",
    "A-10": "MLCC capacitance under DC bias: 10 uF/50 V X7R 1210 keeps 70 % at 15 V and 50 % at 23 V.",
    "A-11": "SiC turn-off loss = datasheet EOFF scaled linearly with V and I and with total gate resistance.",
    "A-12": "Gapped-core AL tolerance +/-7 % (ground gap, vendor-trimmed) for the inductance window.",
    "A-13": "BSS126 square law I = K (VGS - VGS(th))^2 with K from IDSS(min) 7 mA at VGS = 0 for each VGS(th).",
    "A-14": "DCM turn-on happens at a random point of the drain ring: mean V^2 = Vin^2 + 0.25 VR^2.",
    "A-15": "Copper 100 C; primary 0.30 mm grade-2 wire OD 0.342 mm; TIW 0.80 mm conductor, OD 1.00 mm; "
            "polyester tape 0.06 mm per layer.",
    "A-16": "VDD regulator: BZX84-B16 runs at ~0.7 mA, 0.3 V below its 5 mA value; BAT54 0.25-0.40 V at 5 mA.",
    "A-17": "Loads behind the SYS-IO-AUX rev E feed (D-034, hardware inhibit while AUX-HV is the source; "
            "SYS-IO-AUX_aux_feed.json): SYS logic + BMU-GW 6.24 W constant power above 6 V; CTRL 7.56 W constant power "
            "and the port board 0.6 A (0.7 A with two battery-port hold coils) above the +24V eFuse UVLO "
            "(15.77-16.41 V rising, 14.61-15.42 V falling); feed parts 0.145 W directly on the AUX output. Total "
            "27.8 W at 23.15 V (30.1 W with 0.7 A, <= 200 s), 13.9 W without a port hold.",
    "A-18": "VDD consumption during start-up: UCC28C59 2 mA (max) + 1.25 x gate charge + COMP pull-up + 2 mA EA "
            "source (A-8) + LINE_OK/soft-start/start-up-disable/reference/comparator loads; slowest soft start uses "
            "the 0.5 mA EA minimum.",
    "A-19": "Fuse clearing I2t at 1000 VDC for the resistor-limited (<= 25 A) fault = 2 x the 20 kA melting I2t "
            "(arc included); repetitive inrush pulses kept <= 20 % of the melting I2t (our derating rule, the "
            "Schurter sheet has no pulse-life curve).",
    "A-20": "Port-SPD let-through modelled as a rectangular differential residual of Up = 4 kV for 20 us on top of the "
            "operating voltage (pessimistic: the real residual is a short peak), plus the review cases 2.75 kV / 20 us "
            "and 2 kV / 50 us; the whole residual appears across the AUX-HV input.",
    "A-21": "Minimum on-time in a short 0.25-0.40 us (CS filter + tD + SiC turn-off, review AX-03); secondary reset "
            "voltage in a hard short = output-rectifier VF at the limit current (hot), no wiring drop credited.",
    "A-22": "Clamp spike seen by the aux winding = 50 % (range 25-75 %) of (Vclamp - VR) x Na/Np for the "
            "leakage-reset time: the aux sits between S and P2 and links the P1-S leakage field only.",
    "A-23": "Compensated tap string: C_STR 100 pF +/-5 % C0G, bottom capacitors +/-5 % C0G; CRHV self-capacitance "
            "negligible; lockout response 1.0 us (1k/220p filter 0.22 us + TLV3202 tPD + COMP discharge + tD + gate).",
    "A-24": "COMP-to-CS offset 1.15 V +/-0.10 V (SLUSEV2C p7 gives a typical value only).",
    "A-25": "SYS feed switch (LM74800 rev E) modelled as SYS does: enabled T_DRV (0.94 ms) after the AUX output "
            "passes the UVLO rising level (or 1.08 ms after cabinet loss), HGATE then ramps at 16-63 V/s from the bus "
            "level and the bus follows 3.0 V below it (source follower) until the FET is fully on; opens 6 us after "
            "the AUX output falls below the UVLO falling level; latched OV above 23.97 V.",
    "A-26": "Overload thermal: two-node model per device - junction-to-lead/case Rth (datasheet) follows the "
            "instantaneous power within the 0.1-0.2 s timer on-time, board Rth (A-9) follows the hiccup average.",
    "A-27": "BZX84-B6V8 at 3-7 uA (foldback threshold): 6.0-6.9 V (Fig. 8 typical 6.3-6.6 V, B grade, -40..85 C).",
    "A-28": "Primary control area (controller, VDD follower, TLV3202 timer) <= 85 C local, which the CNY65 next to it "
            "needs anyway (its rating): BAT54 leakage 42 uA typ, BAS16 <= 1 uA; 125 C shown as a check.",
    "A-29": "SiC turn-off loss (replaces A-11): the channel overlap only. A datasheet EOFF larger than EOSS at its "
            "test voltage includes the Coss charge (stored, dissipated later at turn-on and counted there), so the "
            "channel part is EOFF - EOSS(test V), not scaled down with current (InventChip Fig. 21 is flat 1-2 A); a "
            "datasheet EOFF below EOSS is taken as the channel part (scaled with V and I). Plus the Miller term A-31.",
    "A-30": "Threshold at temperature: the 25 C minimum shifted by the typical drop to 150 C read off the maker's "
            "curve (IV2Q 1.8 - 0.8 = 1.0 V; SG2M 2.5 - 0.65 = 1.85 V); no maker states a hot minimum.",
    "A-32": "Transformer winding loss, interim (MG-11): 2.3 W at full load (the magnetics check's independent figure "
            "with gap fringing, 2.1-2.3 W; OpenMagnetics 5.6 W), scaled with Ipk^2 at other loads and counted on the "
            "secondary side (more magnetising power - conservative for the current limit). Rev C1: used only to "
            "state the winding-loss requirement (t1_budget); the losses come from A-33.",
    "A-33": "Rev C1 transformer = magnetics design rev M1 (design_aux_hv_transformer.json): core loss from its own "
            "iGSE table, copper from its own table x the OpenMagnetics / own ratio at 1000 V (the higher model), both "
            "interpolated in V_in and scaled with Ipk^2 (the file's note: ~P_out); leakage 2 / 3 x its 1-D value "
            "(A-1); switched capacitance = its P1-SH value x this model's / its value on the rev C0 stack (36.3 / 27 "
            "pF); thermal resistance = its temperature rise / loss (potted case, natural convection).",
    "A-31": "0 V turn-off (UCC28C59 output, no negative rail): the drain rise induces Crss x dV/dt x R_off on the "
            "gate (R_off = R_G + Rg,int + 15 R driver pull-down max); above VTH(min, hot) the channel carries "
            "k (VG - VTH)^2 (k from the maker's 175 C transfer curve), solved self-consistently with dV/dt = "
            "(Ipk - I_ch) / (Coss + C_W); its energy is added to the turn-off loss.",
}

# ============================================================================================ helpers
E96 = [1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30, 1.33, 1.37, 1.40, 1.43, 1.47, 1.50,
       1.54, 1.58, 1.62, 1.65, 1.69, 1.74, 1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
       2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09, 3.16, 3.24, 3.32, 3.40, 3.48, 3.57,
       3.65, 3.74, 3.83, 3.92, 4.02, 4.12, 4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
       5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32, 7.50, 7.68, 7.87, 8.06, 8.25, 8.45,
       8.66, 8.87, 9.09, 9.31, 9.53, 9.76]
E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]


def e96(x):
    d = 10 ** math.floor(math.log10(x))
    return min((v * d for v in E96 + [10.0]), key=lambda v: abs(math.log(v / x)))


def e96_up(x):
    """Smallest E96 value >= x."""
    d = 10 ** math.floor(math.log10(x))
    return min(v * d for v in E96 + [10.0] if v * d >= x * 0.9999)


def e12_down(x):
    """Largest E12 value <= x."""
    d = 10 ** math.floor(math.log10(x))
    return max(v * d for v in [e / 10 for e in E12] + E12 if v * d <= x * 1.0001)


def ohm(v):
    return "%gM" % (v / 1e6) if v >= 1e6 else "%gk" % round(v / 1e3, 4) if v >= 1e3 else "%gR" % v


def farad(v):
    return "%gu" % round(v * 1e6, 4) if v >= 1e-6 else "%gn" % round(v * 1e9, 4) if v >= 1e-9 else "%gp" % round(v * 1e12, 3)


def eoss(v, dev=None):
    """Coss stored energy at v: the maker's curve, extrapolated above its last point with E ~ V^eoss_exp."""
    d = dev or MOS
    if v <= d["eoss_v"][-1]:
        return float(np.interp(v, d["eoss_v"], d["eoss_j"]))
    return d["eoss_j"][-1] * (v / d["eoss_v"][-1]) ** d["eoss_exp"]


def rds(tj, worst=False, dev=None):
    d = dev or MOS
    typ = d["rds25"][0] * (1 + (d["rds175_typ"] / d["rds25"][0] - 1) * (tj - 25) / 150)
    return typ * (d["rds25"][1] / d["rds25"][0] if worst else 1.0)


def switch_loss(vin, vr, ipk, ip_rms, fs, dev, tj=125.0, c_w=None):
    """Switch dissipation (W): conduction (max R at tj), DCM turn-on (Eoss + winding C at a random point of the
    ring, A-14), turn-off channel overlap (A-29) and the Miller term of 0 V turn-off (A-31). c_w: winding C
    (default: this board's T1)."""
    c_w = C_W if c_w is None else c_w
    v_on2 = vin ** 2 + 0.25 * vr ** 2
    e_on = eoss(math.sqrt(v_on2), dev) + 0.5 * c_w * v_on2
    v_off = vin + vr
    e_ds, v_t, i_t, rg_t = dev["eoff"]
    r_off = R_G + dev["rg_int"] + 15.0
    if e_ds > eoss(v_t, dev):
        e_ch = (e_ds - eoss(v_t, dev)) * v_off / v_t
    else:
        e_ch = e_ds * v_off / v_t * ipk / i_t * r_off / (dev["rg_int"] + rg_t)
    c_node = dev["co_tr"] + c_w
    i_ch = 0.0                                  # A-31: self-consistent Miller-induced channel current
    for _ in range(60):
        dvdt = (ipk - i_ch) / c_node
        vg = r_off * dev["crss"] * dvdt
        i_new = dev["k_ch"] * max(vg - dev["vth_min_hot"], 0.0) ** 2
        i_ch = 0.5 * (i_ch + min(i_new, ipk))
    dvdt = (ipk - i_ch) / c_node
    e_mil = 0.5 * v_off * i_ch * v_off / dvdt
    return dict(cond=ip_rms ** 2 * rds(tj, True, dev), on=e_on * fs, off=(e_ch + e_mil) * fs, e_ch=e_ch, e_mil=e_mil,
                dvdt=dvdt, vg_ind=r_off * dev["crss"] * dvdt, i_ch=i_ch)


def dout_model(tj=125.0):
    """VS-8ETU04S-M3 forward voltage V = V0 + Rd*I, typical, interpolated between the 25 C and 150 C curves."""
    def line(pts):
        rd = (pts[1] - pts[0]) / 7.0
        return pts[0] - rd, rd
    v0h, rdh = line(DOUT["hot"])
    v0c, rdc = line(DOUT["cold"])
    k = (tj - 25.0) / 125.0
    return v0c + (v0h - v0c) * k, rdc + (rdh - rdc) * k


def steinmetz():
    """Least-squares fit Pv = k f^a B^b to the four N97 table points (100 C)."""
    f, b, p = (np.array(c) for c in zip(*N97["pv"]))
    m = np.column_stack([np.ones(len(f)), np.log(f), np.log(b)])
    (lk, a, bb), *_ = np.linalg.lstsq(m, np.log(p), rcond=None)
    return math.exp(lk), a, bb


def dowell(delta, m):
    """Dowell AC/DC resistance ratio for a layer with penetration ratio delta and MMF ratio m."""
    if delta < 1e-6:
        return 1.0
    d2 = 2 * delta
    t1 = (math.sinh(d2) + math.sin(d2)) / (math.cosh(d2) - math.cos(d2))
    t2 = (math.sinh(delta) - math.sin(delta)) / (math.cosh(delta) + math.cos(delta))
    return delta * (t1 + 2 * (m * m - 1) / 3 * t2)


def corners(d):
    """Every combination of the (lo, hi) pairs in d -> list of dicts (tolerance stacking)."""
    out = [{}]
    for k, v in d.items():
        out = [dict(o, **{k: x}) for o in out for x in v]
    return out


# ============================================================================================ design choices
FS = 65e3                       # nominal switching frequency (set by RT/CT below)
NP, NS, NA = 90, 15, 16         # turns: primary (2 x 45 sandwich), secondary (TIW), auxiliary (> NS: VDD from a
                                # follower regulator, so the aux takes VDD over below the SYS eFuse UVLO of 16.1 V)
N = NP / NS
V_DCM_MIN = 185.0               # full load + 10 % stays DCM down to this input with Lp at +7 %
P_DESIGN = 1.10 * POUT          # power that the current limit must still deliver at worst-case tolerances
ETA_GUESS = 0.85
AL_TOL = 0.07                   # A-12
B_LIMIT = 0.32                  # T at the highest possible current-limit trip, 100 C (N97 Bs 0.41 T), on A_e (rev A)
B_LIMIT_AMIN = 0.85 * 0.410     # rev C (MG-12): the same trip on A_min, <= 0.85 x N97 Bs(100 C); vendor measures L(I)
# Transformer winding loss (MG-11, sim/out/magnetics/report.md): the 1-D Dowell model misses the fringing field of the
# centre-leg gap. Rev C0 interim: 2.3 W at full load, scaled with Ipk^2 (A-32) - now used only by t1_budget(), which
# states the winding-loss requirement. Rev C1: the magnetics design rev M1 is final and supplies T1 (A-33).
T1_CU_FL, T1_IPK_REF = 2.3, 0.936
T1_DESIGN_FILE = os.path.join(HERE, "out", "magnetics", "design_aux_hv_transformer.json")
with open(T1_DESIGN_FILE) as _f:
    T1_DESIGN = json.load(_f)
T1_EL, T1_CORE = T1_DESIGN["electrical"], T1_DESIGN["core"]
T1_V = T1_DESIGN["loss_grid"]["V_in_V"]
assert [r["point"] for r in T1_DESIGN["loss_table"]] == ["%d V, 30 W" % v for v in T1_V], "T1 loss table layout"
_T1_VER = T1_DESIGN["verification"]
T1_K_OM = max(_T1_VER["openmagnetics"]["P_cu_W"] / _T1_VER["own"]["P_cu_W"], 1.0)   # A-33: the higher copper model
T1_CU_V = [r["P_cu_W"] * T1_K_OM for r in T1_DESIGN["loss_table"]]
T1_FE_V = [r["P_core_W"] for r in T1_DESIGN["loss_table"]]      # own iGSE, above OpenMagnetics' figure
T1_IPK_D = T1_EL["I_pk_full_load_A"]
T1_RTH = T1_DESIGN["thermal"]["T_rise_K"] / max(T1_DESIGN["loss_grid"]["P_total_W"])   # potted case (~15 K/W)
B_LIMIT_AMIN = T1_EL["B_limit_rule_T"]   # the design's rule for DMR95 (0.85 x Bs at 100 C)
RCS_EACH = 1.5                  # 2 x 1.5 R in parallel
RCSF, CCSF = 1.0e3, 150e-12     # CS filter (no internal blanking in UCC28C5x)
N_TAPE_PS = 8                   # tape layers between the drain-end primary layer and the shield (capacitance)
N_TAPE_S = 2                    # rev C (IC-15): tape layers on each side of the TIW secondary (PD-free margin)
C_T = 1.0e-9                    # oscillator capacitor (C0G 5 %); RT is solved for 130 kHz oscillator
R_G = 10.0                      # gate resistor
R_PU = 2.2e3                    # COMP pull-up to VREF (opto load resistor)
C_P = 2.2e-9                    # COMP pole capacitor
R_BIAS = 4.7e3                  # resistor across the opto LED (ATL431 minimum current)
R_LED = 560.0                   # fast-lane resistor (sets the mid-band loop gain with CTR and R_PU)
R_FBB = 10.0e3                  # FB divider lower resistor (aux OVP backup), 0.1 %
RS_START = 4.7e3                # start-up current-source resistor
C_VDD_EACH, N_CVDD = 10e-6, 8   # 8 x 10 uF/50 V X7R 1210 on VDD (A-10: 70 % at 15 V); MLCC only: no leakage
C_AUX_EACH, N_CAUX = 10e-6, 2   # aux reservoir ahead of the follower
R_ZREG = 10e3                   # follower Zener bias from the aux reservoir
R_AUX = 22.0                    # aux rectifier series resistor (peak-charging limit, ~2 x VDD current pulsed)
R_VDDE = 10e3                   # AX-05: sinks the BAT54 leakage at the follower emitter (keeps V_EB < 5 V)
C_SS = 2.2e-6                   # soft start capacitor
R_SS = 100e3
VIN_ON_T, VIN_OFF_T = 225.0, 180.0   # brown-in / brown-out targets
R_STR = 10e6                    # each of the 3 CRHV2512 sections of the start-up / sense string
C_STR = 100e-12                 # string compensation, one per section (1 kV C0G)
R_PULL_LINE = 47e3              # LINE_OK pull-up to VREF
PG_UV, PG_OV = 21.3, 25.5       # window detector targets (falling UV, rising OV), V: outside load-step excursions
N_COUT = 3                      # polymer capacitors
C_MLCC_OUT = (2, 10e-6)         # 2 x 10 uF 50 V X7R 1210 at the output
C_SN, R_SN = 68e-12, 150.0      # output rectifier RC snubber
TJ = 125.0                      # junction temperature used for losses
# ---- rev B
R_LIM_EACH, N_LIM = RLIM["r"], RLIM["n"]   # per port, ahead of the fuse
C_BULK_EACH, N_BULK = CBULK["c"], CBULK["n"]
C_HF = (2, 22e-9 * 0.6)         # 2 x 22 nF 2 kV X7R 1812 at the switching loop (60 % at 1 kV)
R_REF = 4.7e3                   # primary ATL431 2.5 V reference bias from VREF
R_OVH1, R_OVH2 = 10e3, 1.0e6    # OV comparator hysteresis (reference side)
R_LSF, C_LSF = 1.0e3, 220e-12   # OV comparator input filter
OV_MARGIN = 5.0                 # V: lowest lockout above the 1100 V transient (keeps regulating through it)
COMP_M = 0.85                   # string bottom compensated to 85 % (under-compensated: fast surges trip early)
C_TX = 10e-9                    # foldback capacitor on RT/CT (C0G 5 %)
R_AF1, C_AF, R_AF2 = 4.7e3, 470e-12, 22e3   # foldback aux-plateau detector (rev C: x2.2 impedance, same taus - R_AF2 power)
R_QG, R_FBK = 1.0e6, 47e3       # Q_INV gate hold (the Zener's forward path discharges it in a short), Q_FB pull-up
T_ON_MIN = (0.25e-6, 0.40e-6)   # A-21
SPIKE_FRAC = (0.25, 0.50, 0.75)  # A-22
R_TB, C_TMR_OPTS = 100e3, (1.0e-6, 1.5e-6, 2.2e-6, 2.7e-6, 3.3e-6)   # overload timer (low impedance: diode leakage)
C_TMR_TOL = (0.8, 1.1)          # X7R 10 % plus DC bias at <= 5 V (our allowance)
T_TRIP_MIN = 0.085              # s: shortest allowed overload trip (black start into 2 mF needs < 80 % of it)
R_SSG, R_SSG_PD = 1.0e3, 100e3  # soft-start crowbar gate drive
R_PGR, R_PGW, R_PGO = 10e3, 10e3, 1.0e3    # PG 3.3 V Zener rail (rev C 10k: part stress), open-drain pull-up, output R
R_FF, C_FF = 33e3, 39e-9        # R-C feed-forward from the output to the ATL431 REF (wind-up / start-up overshoot)
R_PATH = 0.05                   # A-25
VFD_US1G = (0.55, 0.80)         # A-6
IFB_MAX = 2e-6                  # SLUSEV2C p7: FB input bias current <= 2 uA (sourcing)


def osc(rt, ct=C_T, vref=5.0, idis=UCC["idis"][1]):
    """UCC28C5x oscillator: RT charges CT from 0.7 V to 2.5 V, the 8.4 mA sink discharges it (SLUSEV2C p20, p7)."""
    tc = rt * ct * math.log((vref - UCC["osc_lo"]) / (vref - UCC["osc_hi"]))
    td = ct * (UCC["osc_hi"] - UCC["osc_lo"]) / (idis - (vref - 1.6) / rt)
    return 1.0 / (tc + td)


C_T_EFF = C_T + N7002["coss"]   # Q_FB off-state capacitance in series with C_TX adds to C_T


def solve_rt():
    lo, hi = 2e3, 100e3
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if osc(mid, C_T_EFF) > 2 * FS else (lo, mid)
    return e96(math.sqrt(lo * hi))


R_T = solve_rt()
FS_NOM = osc(R_T, C_T_EFF) / 2
# the oscillator thresholds are derived from VREF (SLUSEV2C p26), so VREF cancels; the IC's own spread is taken from
# the p7 test point (50.5-57 kHz around 53 kHz over temperature), plus RT 1 % and CT 5 %
FS_RANGE = (FS_NOM * 50.5 / 53.0 / (1.01 * 1.05), FS_NOM * 57.0 / 53.0 / (0.99 * 0.95))
F_FOLD = osc(R_T, C_T_EFF + C_TX) / 2
F_FOLD_RANGE = (F_FOLD * 50.5 / 53.0 / (1.01 * 1.05), F_FOLD * 57.0 / 53.0 / (0.99 * 0.95))

# ============================================================================================ transformer
VF_SEC_NOM = 0.75               # first guess, refined by the operating-point solver
VR_NOM = N * (VOUT + VF_SEC_NOM)
LP_BCM = 1.0 / (2 * (P_DESIGN / 0.82) * FS_NOM * (1 / V_DCM_MIN + 1 / VR_NOM) ** 2)
LP = LP_BCM / (1 + AL_TOL)
AL = LP / NP ** 2
GAP = (CORE["k1"] / (AL * 1e9)) ** (1 / -CORE["k2"]) * 1e-3     # N87 relation used as N97 estimate (vendor trims)
LP_RANGE = (LP * (1 - AL_TOL), LP * (1 + AL_TOL))
LS, LA = LP / N ** 2, LP * (NA / NP) ** 2
RCS = RCS_EACH / 2


def stack():
    """Winding stack from the bobbin outwards (A-15): (name, copper thickness, insulation after it, turns, MMF role)."""
    return [("P1", 0.300e-3, 0.042e-3 + N_TAPE_PS * 0.06e-3, NP // 2, "p"),
            ("SH", 0.035e-3, N_TAPE_S * 0.06e-3, 0, "flat"),
            ("S", 0.80e-3, 0.10e-3 + N_TAPE_S * 0.06e-3, NS, "s"),
            ("AUX", 0.25e-3, 0.04e-3 + 0.06e-3, NA, "flat"),
            ("P2", 0.300e-3, 0.042e-3 + 0.10e-3, NP - NP // 2, "p")]


def leakage_estimate():
    """1-D MMF integral over the stack (secondary shorted, primary current 1 A, all referred to NP)."""
    x, mmf, llk = 0.0, 0.0, 0.0
    bw = 15.4e-3                        # wound width of the primary layers (45 x 0.342 mm)
    first_ins = 0.10e-3 + 0.042e-3      # tube tape + P1 enamel (TIW insulation lumped into S insulation)
    x += first_ins
    for name, cu, ins, turns, role in stack():
        n = 400
        dx = cu / n
        for k in range(n):
            xx = x + (k + 0.5) * dx
            if role == "p":
                m = mmf + (NP / 2) * (k + 0.5) / n
            elif role == "s":
                m = mmf - NP * (k + 0.5) / n
            else:
                m = mmf
            llk += MU0 * m * m * math.pi * (CORE["tube"] + 2 * xx) / bw * dx
        if role == "p":
            mmf += NP / 2
        elif role == "s":
            mmf -= NP
        x += cu
        llk += MU0 * mmf * mmf * math.pi * (CORE["tube"] + 2 * (x + ins / 2)) / bw * ins
        x += ins
    return llk, x


LLK_1D_C0, STACK_H_C0 = leakage_estimate()              # own rev C0 ETD 29 stack, for the report
LLK_1D_REVB = 12.06e-6                                   # rev B stack (1 tape each side of S), for the report
LLK_1D = T1_EL["L_leak_1d_H"]    # rev C1: 1-D value of the design M1 stack (same method)
LLK = 2.0 * LLK_1D               # A-1 nominal
LLK_WC = 3.0 * LLK_1D            # A-1 worst case
STACK_H = T1_DESIGN["fit"]["build_m"]


def winding_capacitance():
    """A-2: parallel-plate capacitance of each primary layer to its quiet neighbour, weighted by the mean square of
    the layer's own swing (linear along the layer, a -> b of the drain swing: (a^2 + ab + b^2) / 3)."""
    eps = 3.2 * 8.854e-12
    d_in = 0.042e-3 + N_TAPE_PS * 0.06e-3                # P1 (drain end, 1.0 -> 0.5) to the PGND shield
    c_in = eps * math.pi * (CORE["tube"] + 2 * 0.25e-3) * 15.4e-3 / d_in
    d_out = 0.042e-3 + 0.06e-3                           # P2 (0.5 -> 0, bulk end) to the aux layer
    c_out = eps * math.pi * (CORE["tube"] + 2 * 1.9e-3) * 15.4e-3 / d_out
    return c_in * (1 + 0.5 + 0.25) / 3 + c_out * 0.25 / 3, c_in


C_W_C0, C_SHIELD = winding_capacitance()                # own rev C0 ETD 29 stack
# rev C1: the design's own P1-SH value scaled by this model / the magnetics model on the same rev C0 stack
# (36.3 / 27 pF, design file electrical.C_switched_note)
C_W = T1_EL["C_switched_P1_SH_own_F"] * C_W_C0 / 27e-12
assert abs(T1_EL["L_p_H"] / LP - 1) < 0.01 and T1_EL["turns"] == "%d:%d:%d" % (NP, NS, NA), "T1 design vs this model"
AL, GAP = T1_EL["A_L_H"], T1_CORE["gap"]["length_m"]

# ============================================================================================ operating point
STEIN = steinmetz()


def igse_pv(db, d, d2, f):
    """iGSE core loss density (W/m3) for the DCM flux waveform: rise in d*T, fall in d2*T, flat otherwise."""
    k, a, b = STEIN
    th = np.linspace(0, 2 * math.pi, 4001)
    ki = k / ((2 * math.pi) ** (a - 1) * np.trapezoid(np.abs(np.cos(th)) ** a, th) * 2 ** (b - a))
    t = 1 / f
    return ki / t * ((db / (d * t)) ** a * d * t + (db / (d2 * t)) ** a * d2 * t) * db ** (b - a)


def ac_copper(ipk, d, d2, f, is_pk):
    """DC + Dowell AC copper loss of primary and secondary from the Fourier series of the real waveforms."""
    rho = 1.72e-8 * (1 + 0.00393 * 75)
    t = np.linspace(0, 1 / f, 4096, endpoint=False)
    tp = (t * f) % 1
    ip = np.where(tp < d, ipk * tp / d, 0.0)
    isec = np.where((tp >= d) & (tp < d + d2), is_pk * (1 - (tp - d) / d2), 0.0)
    out = {}
    for name, wave, turns, dia, eta, m, mlt in (
            ("pri", ip, NP, 0.30e-3, 0.30 / 0.342, 1.0, math.pi * (CORE["tube"] + 2 * 1.2e-3)),
            ("sec", isec, NS, 0.80e-3, 0.80 / 1.00, 0.5, math.pi * (CORE["tube"] + 2 * 0.9e-3))):
        rdc = turns * mlt * rho / (math.pi * dia * dia / 4)
        spec = np.fft.rfft(wave) / len(wave)
        p = rdc * abs(spec[0]) ** 2
        for h in range(1, 60):
            delta = (math.pi / 4) ** 0.75 * dia / math.sqrt(rho / (math.pi * h * f * MU0)) * math.sqrt(eta)
            p += rdc * 2 * abs(spec[h]) ** 2 * dowell(delta, m)
        out[name] = (p, rdc)
    return out


def operating_point(vin, pout=POUT, fs=FS_NOM, lp=LP, llk=LLK, tj=TJ, dev=None):
    """Full DCM solution at one input voltage. Returns a dict of currents, duties and the loss breakdown (W)."""
    v0, rd = dout_model(tj)
    i_secbias = SEC_BIAS_I
    io = pout / VOUT + i_secbias
    p_aux = V_AUX_NOM * AUX_LOAD_I            # everything VDD draws, taken at the aux reservoir (follower drop included)
    ipk = math.sqrt(2 * (pout / ETA_GUESS) / (lp * fs))
    p_clamp_mag, p_mag, cu_extra = 0.0, 1.0, 0.0
    for _ in range(80):
        # magnetising current left after the clamp interval, then shared by secondary and aux (same core voltage)
        im0 = ipk * math.sqrt(max(1 - p_clamp_mag / p_mag, 0.5))
        is_pk = N * im0 * (1 - p_aux / max(p_mag - p_clamp_mag, 1e-9))
        vs = VOUT + v0 + rd * is_pk / 2 + cu_extra / io          # A-32 excess loss as a secondary series drop
        d = (lp + llk) * ipk * fs / vin
        d2 = lp * im0 * fs / (N * vs)
        p_d = d2 * (v0 * is_pk / 2 + rd * is_pk ** 2 / 3)
        cu = ac_copper(ipk, d, d2, fs, is_pk)
        if T1_DESIGN is None:
            cu_t1 = T1_CU_FL * (ipk / T1_IPK_REF) ** 2                  # A-32 interim: gap fringing included
        else:                                                            # A-33: design M1 table, ~P_out (its note)
            cu_t1 = float(np.interp(vin, T1_V, T1_CU_V)) * (ipk / T1_IPK_D) ** 2
        cu_extra = max(cu_t1 - cu["pri"][0] - cu["sec"][0], 0.0)        # the excess counted on the secondary side
        cu["sec"] = (cu["sec"][0] + cu_extra, cu["sec"][1])
        is_rms = is_pk * math.sqrt(d2 / 3)
        icap = math.sqrt(max(is_rms ** 2 - io ** 2, 0.0))
        esr = CPOL["esr"] / N_COUT
        p_esr = icap ** 2 * esr
        vr = N * (VOUT + v0 + rd * is_pk)
        vc = clamp_v(ipk, hot=False)
        p_lk = 0.5 * llk * ipk ** 2 * fs
        p_clamp_mag = p_lk * vr / (vc - vr)
        p_mag = VOUT * io + p_d + cu["sec"][0] + p_esr + p_aux + p_clamp_mag
        new = math.sqrt(2 * p_mag / (lp * fs))
        if abs(new - ipk) < 1e-10:
            break
        ipk = new
    ip_rms = ipk * math.sqrt(d / 3)
    if T1_DESIGN is None:
        db = lp * ipk / (NP * CORE["ae"])
        p_core = igse_pv(db, d, d2, fs) * CORE["ve"]
    else:       # ponytail: table clamps at 1000 V (1100 V is a transient, ~+0.01 W)
        db = lp * ipk / (NP * T1_CORE["Ae_m2"])
        p_core = float(np.interp(vin, T1_V, T1_FE_V)) * (ipk / T1_IPK_D) ** 2
    sl = switch_loss(vin, vr, ipk, ip_rms, fs, dev or MOS, tj)          # A-14, A-29, A-31
    i_in = None
    loss = {
        "switch conduction": sl["cond"],
        "switch turn-on (C_oss + winding C)": sl["on"],
        "switch turn-off": sl["off"],
        "TVS clamp": p_lk * vc / (vc - vr),
        "output rectifier": p_d,
        "rectifier RC snubber": C_SN * (VOUT + vin / N) ** 2 * fs,
        "transformer core": p_core,
        "transformer copper": cu["pri"][0] + cu["sec"][0],
        "current-sense resistors": ip_rms ** 2 * RCS,
        "output capacitor ESR": p_esr,
        "controller + bias (VDD)": p_aux,
        "secondary bias (ATL431, opto, PG, preload)": VOUT * i_secbias,
        "HV divider string": vin ** 2 / (3 * R_STR),
    }
    p_in = pout + sum(loss.values())
    for _ in range(20):                                      # input path: 2 x R_LIM, fuse, two OR diodes
        i_in = (p_in + loss.get("input path", 0)) / vin
        loss["input path"] = i_in ** 2 * (R_LIM_EACH * N_LIM + FUSE["p_in"] / FUSE["i_n"] ** 2) + 2 * 0.9 * i_in
        p_in = pout + sum(loss.values())
    return dict(vin=vin, ipk=ipk, d=d, d2=d2, dcm=(d + d2) < 0.98, is_pk=is_pk, ip_rms=ip_rms, is_rms=is_rms,
                icap=icap, vr=vr, vc=vc, db=db, p_mag=p_mag, p_aux=p_aux, io=io, vs=vs, p_d=p_d, cu=cu,
                p_in=p_in, eta=pout / p_in, loss=loss, i_in=i_in, p_lk=p_lk, p_clamp_mag=p_clamp_mag, p_esr=p_esr,
                fs=fs, lp=lp, pout=pout)


def clamp_v(i, hot=False, worst=False, tvs=None):
    """Voltage across the TVS stack at current i (A-3: hot = 100 C junction; worst = VBR max + datasheet slope)."""
    t = tvs or TVS
    vbr = t["vbr"][1] if worst else sum(t["vbr"]) / 2
    if hot:
        vbr *= 1 + 0.001 * (100 - 25)
    rd = (t["vc"] - t["vbr"][1]) / t["ipp"]
    return t["n"] * (vbr + i * rd)


# secondary bias currents (computed with the component values below, used in the operating point)
SEC_BIAS_I = 0.0
AUX_LOAD_I = 0.0


# ============================================================================================ secondary components
def feedback_divider():
    """E96 0.1 % pair for the ATL431 (R_LO 10-20 k): the pair closest to 22.8 V; returns (R_UP, R_LO, Vout)."""
    best = None
    for r_lo in [v * 1e4 for v in E96 if v <= 2.0]:
        r_up = e96(r_lo * (VOUT / ATL["vref"][1] - 1))
        v = ATL["vref"][1] * (1 + r_up / r_lo) + 30e-9 * r_up          # Iref typ 30 nA (ATL431 p4)
        if best is None or abs(v - VOUT) < abs(best[2] - VOUT):
            best = (r_up, r_lo, v)
    return best


R_UP, R_LO_SEL, VOUT_SET = feedback_divider()


def output_tolerance():
    """Set point over ATL431 B grade (initial + temperature), 0.1 % resistors, Iref, cathode-voltage effect."""
    lo = hi = None
    for vref in (ATL["vref"][0] - ATL["vdev"], ATL["vref"][2] + ATL["vdev"]):
        for k in (0.999, 1.001):
            for iref in (0.0, ATL["iref"]):
                for dvka in (-4 * ATL["dvka"], 4 * ATL["dvka"]):
                    v = (vref + dvka) * (1 + R_UP * k / (R_LO_SEL / k)) + iref * R_UP * k
                    lo = v if lo is None else min(lo, v)
                    hi = v if hi is None else max(hi, v)
    return lo, hi


VOUT_LO, VOUT_HI = output_tolerance()


def pg_window():
    """TPS3700 string Vout - RA - UV - RB - OV - RC - GND: solve RA, RB, RC (E96, 0.1 %), then the threshold spread."""
    rc = 10.0e3
    # OV rising: V_OV = VIT+ * (RA+RB+RC)/RC ; UV falling: V_UV = VIT- * (RA+RB+RC)/(RB+RC)
    tot = PG_OV / DET["vitp"][1] * rc
    rb_rc = tot * DET["vitn"][1] / PG_UV
    rb = e96(rb_rc - rc)
    ra = e96(tot - rb - rc)
    tot = ra + rb + rc
    k = 0.001
    uv_fall = (DET["vitn"][0] * (tot * (1 - k)) / ((rb + rc) * (1 + k)), DET["vitn"][2] * (tot * (1 + k)) / ((rb + rc) * (1 - k)))
    uv_rise = (DET["vitp"][0] * (tot * (1 - k)) / ((rb + rc) * (1 + k)), DET["vitp"][2] * (tot * (1 + k)) / ((rb + rc) * (1 - k)))
    ov_rise = (DET["vitp"][0] * (tot * (1 - k)) / (rc * (1 + k)), DET["vitp"][2] * (tot * (1 + k)) / (rc * (1 - k)))
    ov_fall = (DET["vitn"][0] * (tot * (1 - k)) / (rc * (1 + k)), DET["vitn"][2] * (tot * (1 + k)) / (rc * (1 - k)))
    return dict(ra=ra, rb=rb, rc=rc, uv_fall=uv_fall, uv_rise=uv_rise, ov_rise=ov_rise, ov_fall=ov_fall)


PG = pg_window()
R_PRELOAD = 10.0e3


def pg_drive():
    """INT-13: TPS3700 + SN74LVC1G17 on a 3.3 V Zener rail from the output; PG_HVAUX actively high when in window."""
    i_load = 10e-6 + LVC["icc"] + 3.4 / R_PGW + 3.4 / (SYS["pd_pg"] + R_PGO)       # TPS3700 + buffer + pull-up + SYS
    out = {}
    for vout, vz5 in ((VOUT_LO, Z33["vz"][0]), (VOUT_HI, Z33["vz"][1])):
        iz = (vout - vz5) / R_PGR - i_load
        vz = vz5 - 0.5 * math.log(5e-3 / max(iz, 1e-4))      # rdif ~ 0.5 V / I between 1 and 5 mA (600 R / 95 R)
        out[vout] = (iz, vz)
    rail = (min(v[1] for v in out.values()), max(v[1] for v in out.values()))
    i_out = rail[1] / (SYS["pd_pg"] + R_PGO)
    v_high = (rail[0] - LVC["voh_drop"] - i_out * R_PGO, rail[1] - (rail[1] / (SYS["pd_pg"] + R_PGO)) * R_PGO)
    p_r = (VOUT_HI - rail[0]) ** 2 / R_PGR
    return dict(rail=rail, iz_min=min(v[0] for v in out.values()), v_high=v_high, p_r=p_r, i_out=i_out,
                vout_on=LVC["vcc_min"] + 1e-3 * R_PGR)


PGD = pg_drive()


def opto_point():
    """DC operating point of LED, ATL431 and COMP: R_LED from the worst-case LED current the loop may need."""
    vcomp_fl = UCC["comp_off"] + UCC["acs"][1] * RCS * OP_NOM_IPK
    ctr_lo = OPTO["lowif"] * OPTO["ctr10"][0] * OPTO["temp"][0] * 0.7          # A-7
    ctr_hi = OPTO["lowif"] * OPTO["ctr10"][1] * OPTO["temp"][1]
    ctr_typ = OPTO["lowif"] * 1.5
    ic_max = 2.0e-3 + (UCC["vref"][1] - UCC["comp_off"]) / R_PU                  # A-8 + pull-up at zero duty
    if_max = ic_max / ctr_lo
    vf = 1.25
    r_led_max = (VOUT_LO - vf - 2.5 - 0.5) / (if_max + vf / R_BIAS)
    r_led = R_LED
    ic_typ = UCC["ea_src"][1] + (UCC["vref"][1] - vcomp_fl) / R_PU
    if_typ = ic_typ / ctr_typ
    vka_typ = VOUT - (if_typ + 1.1 / R_BIAS) * r_led - 1.1
    if_min = (UCC["ea_src"][0] + (UCC["vref"][1] - UCC["comp_off"] - UCC["acs"][2] * UCC["vcs"][2]) / R_PU) / ctr_hi
    vka_min = VOUT_LO - (if_max + vf / R_BIAS) * r_led - vf
    return dict(vcomp_fl=vcomp_fl, ctr=(ctr_lo, ctr_typ, ctr_hi), ic_max=ic_max, if_max=if_max, r_led=r_led,
                r_led_max=r_led_max, if_typ=if_typ, vka_typ=vka_typ, if_min=if_min, vka_min=vka_min,
                i_atl_min=if_min + 1.0 / R_BIAS)


OP_NOM_IPK = 0.97                 # first pass; refined after the operating point (used for COMP level only)
SEC_BIAS_I = (VOUT / (R_UP + R_LO_SEL) + VOUT / R_PRELOAD + VOUT / (PG["ra"] + PG["rb"] + PG["rc"])
              + (VOUT - 3.3) / R_PGR + 2.5e-3)     # + LED/ATL431 branch ~2.5 mA; PG 3.3 V rail
# VDD: aux winding (NA > NS) -> US1G -> R_AUX -> aux reservoir V_AUX -> BC817 follower (base on BZX84-B16 via R_ZREG)
# -> BAT54 (blocks VDD from reverse-biasing the 5 V base-emitter junction at start-up) -> VDD; R_VDDE emitter to PGND
V_AUX_NOM = NA / NS * (VOUT + 0.75) - 0.8
# VDD = gate drive: Zener (with its tempco over the local range, A-28) - bias drop - VBE (-2 mV/K) - BAT54 (A-16)
VDD_REG = (ZREG[0] + ZREG_SZ[0] * (T_LOCAL[0] - 25) - 0.3 - (BC817["vbe"][1] - 0.002 * (T_LOCAL[0] - 25)) - 0.40,
           (ZREG[0] + ZREG[1]) / 2 - 0.3 - 0.66 - 0.32,
           ZREG[1] + ZREG_SZ[1] * (T_LOCAL[1] - 25) - 0.3 - (BC817["vbe"][0] - 0.002 * (T_LOCAL[1] - 25)) - 0.25)
# VDD clamp (BZX84-B20): only the start-up source (<= 0.43 mA, below the 5 mA test current) can push VDD into it
VDD_CLAMP = (ZVDD[0] + ZVDD_SZ[0] * (T_LOCAL[0] - 25), ZVDD[1] + ZVDD_SZ[1] * (T_LOCAL[1] - 25))
I_REF = (5.0 - 2.5) / R_REF                                           # primary ATL431 reference
I_VREF_X = I_REF + 2 * TLV["iq"] + 5.0 / R_FBK                        # rev B loads on VREF
QG_MAX = max(d["qg"] for d in DEVICES.values())                       # design for the worse gate charge
AUX_LOAD_I = (UCC["ivdd"][0] + 1.0e-3 + 0.5e-3 + 5.0 / R_PULL_LINE + 0.15e-3 + QG_MAX * FS_NOM
              + (V_AUX_NOM - 16.0) / R_ZREG + I_VREF_X + (VDD_REG[1] + 0.3) / R_VDDE)
SWCMP = {k: {v: operating_point(v, dev=d) for v in VIN_TABLE + [VIN_TR]} for k, d in DEVICES.items()}
SW_PWR = {k: sum(o[VIN_MAX]["loss"][n] for n in ("switch conduction", "switch turn-on (C_oss + winding C)",
                                                  "switch turn-off")) for k, o in SWCMP.items()}
SW_DESIGN = max(SW_PWR, key=SW_PWR.get)                                # the hotter switch sets every loss figure
MOS = DEVICES[SW_DESIGN]
OPS = {v: operating_point(v) for v in VIN_TABLE + [VIN_TR]}
OP_NOM_IPK = OPS[600.0]["ipk"]
OPT = opto_point()


# ============================================================================================ stresses
ETA_MAG = POUT / OPS[200.0]["p_mag"]                                  # magnetising power -> output (loss model)
ETA_MAG_WC = POUT / operating_point(200.0, llk=LLK_WC)["p_mag"]       # with the A-1 worst leakage (more to the clamp)


def current_limit():
    i_lim_min = UCC["vcs"][0] / (RCS * 1.01)
    i_lim_max = UCC["vcs"][2] / (RCS * 0.99)
    delay = UCC["td"][1] + RCSF * CCSF * 1.2
    i_over = VIN_TR * delay / LP_RANGE[0]
    # output power the limit delivers (Lp min, fs min, Ipk min, DCM energy x magnetising-to-output efficiency of the
    # loss model with the worst leakage). Rev A applied the end-to-end 0.82 x 0.94 here, which double-counted the
    # primary-side losses (it reported 34 W for the same circuit).
    p_min = 0.5 * LP_RANGE[0] * i_lim_min ** 2 * FS_RANGE[0] * ETA_MAG_WC
    p_typ = 0.5 * LP * (UCC["vcs"][1] / RCS) ** 2 * FS_NOM * ETA_MAG
    p_max = 0.5 * LP_RANGE[1] * (i_lim_max + i_over) ** 2 * FS_RANGE[1]
    return dict(min=i_lim_min, max=i_lim_max, over=i_over, max_eff=i_lim_max + i_over, p_min=p_min, p_typ=p_typ,
                p_max=p_max, delay=delay)


ILIM = current_limit()
B_FL = LP * OPS[200.0]["ipk"] / (NP * T1_CORE["Ae_m2"])
B_LIM = LP_RANGE[1] * ILIM["max_eff"] / (NP * T1_CORE["Ae_m2"])
B_LIM_AMIN = LP_RANGE[1] * ILIM["max_eff"] / (NP * T1_CORE["A_min_m2"])


def vds_peak(vin, i, hot, worst, overshoot=True):
    return vin + clamp_v(i, hot=hot, worst=worst) + (40.0 if overshoot else 0.0)      # A-4


VDS = dict(cont=vds_peak(VIN_MAX, OPS[VIN_MAX]["ipk"], True, True),
           trans=vds_peak(VIN_TR, ILIM["max_eff"], True, True),
           cont_nom=vds_peak(VIN_MAX, OPS[VIN_MAX]["ipk"], False, False, False))
V_DOUT = (VOUT + VIN_TR / N) * 1.25                                    # A-5
V_DAUX = (NA / NS * (VOUT + 0.8) + VIN_TR * NA / NP) * 1.25
V_DCLAMP = VIN_TR + clamp_v(ILIM["max_eff"], True, True)
V_CLAMP_MIN = clamp_v(0.0) * (TVS["vbr"][0] / (sum(TVS["vbr"]) / 2))
V_GS = VDD_REG[2]                                                       # VDD = gate drive high level (max)


# ============================================================================================ aux tracking / backup loop
def aux_sense(vout, pout, r_s, c_s, r_load, vfd, frac, tj):
    """INT-08: average voltage on the aux sense capacitor (US1G -> r_s -> c_s, r_load = FB divider) in periodic
    steady state, for one operating point. Waveform per cycle: on-time (diode off), clamp spike (frac of the
    primary leakage-reset voltage, A-22) on top of the plateau Na/Ns (Vout + VF(i) + i Rsec), linear secondary
    current decay, then zero. Exact exponential steps; periodic solution by the secant method on the cycle map."""
    op = operating_point(600.0, max(pout, 0.05), tj=tj)
    v0, rd = dout_model(tj)
    rsec = op["cu"]["sec"][1] + CPOL["esr"] / N_COUT
    t = 1 / FS_NOM
    t_p = op["d2"] * t
    v_cl = clamp_v(op["ipk"]) - op["vr"]
    t_sp = LLK * op["ipk"] / max(v_cl, 1.0)
    n_st = 120
    dt = t_p / n_st
    steps = []
    for k in range(n_st):
        tt = (k + 0.5) * dt
        i_s = op["is_pk"] * (1 - tt / t_p)
        v = NA / NS * (vout + v0 + rd * i_s + i_s * rsec) + (frac * v_cl * NA / NP if tt < t_sp else 0.0)
        steps.append(v - vfd)
    t_rest = t - t_p
    tau_c = r_s * c_s * r_load / (r_s + r_load)
    k_c = r_load / (r_s + r_load)

    def cycle(vc):
        area = 0.0
        for va in steps:
            if va > vc:
                vinf = va * k_c
                vn = vinf + (vc - vinf) * math.exp(-dt / tau_c)
            else:
                vn = vc * math.exp(-dt / (r_load * c_s))
            area += 0.5 * (vc + vn) * dt
            vc = vn
        tau_d = r_load * c_s
        vend = vc * math.exp(-t_rest / tau_d)
        area += vc * tau_d * (1 - math.exp(-t_rest / tau_d))
        return vend, area / t

    a, b = 0.8 * steps[0], 0.95 * steps[0]
    fa, fb = cycle(a)[0] - a, cycle(b)[0] - b
    for _ in range(30):
        if abs(fb - fa) < 1e-12:
            break
        c = b - fb * (b - a) / (fb - fa)
        a, fa = b, fb
        b, fb = c, cycle(c)[0] - c
        if abs(fb) < 1e-6:
            break
    return cycle(b)[1]


def backup_design():
    """Pick R_AUXS / C_AUXS (narrowest band) and R_FBT (0.1 %, E96) so that the lowest backup level stays >= 0.3 V
    above the highest regulation level. Band = min/max over load 0.35-30 W, rectifier 25/125 C, spike 25-75 %,
    US1G drop, VFB 2.45-2.55 V, FB bias current and 0.1 % resistors. Each corner is reduced to the aux-sense
    average at 24.5 V and its slope per volt of output (the relation is linear over the band)."""
    best = None
    loads = (0.35, 3.0, SYS["p_standby"], POUT)
    for r_s, c_s in ((470.0, 10e-9), (1.0e3, 10e-9), (1.0e3, 4.7e-9), (2.2e3, 4.7e-9)):
        span = []                                   # (load, aux-sense average at 24.5 V, slope per V)
        for p in loads:
            for tj in (25.0, 125.0):
                for frac in (SPIKE_FRAC[0], SPIKE_FRAC[2]):
                    for vfd in VFD_US1G:
                        va = aux_sense(24.5, p, r_s, c_s, 110e3, vfd, frac, tj)
                        vb = aux_sense(25.5, p, r_s, c_s, 110e3, vfd, frac, tj)
                        span.append((p, va, vb - va))

        def band(r_fbt, sub):
            r_par = r_fbt * R_FBB / (r_fbt + R_FBB)
            lo = min(24.5 + ((UCC["vfb"][0] - IFB_MAX * r_par) * (1 + r_fbt * 0.999 / (R_FBB * 1.001)) - va) / sl
                     for p, va, sl in sub)
            hi = max(24.5 + (UCC["vfb"][2] * (1 + r_fbt * 1.001 / (R_FBB * 0.999)) - va) / sl for p, va, sl in sub)
            return lo, hi
        best_r = None
        for r_fbt in sorted(set(e96(R_FBB * k / 100) for k in range(800, 1300))):
            lo, hi = band(r_fbt, span)
            if lo >= VOUT_HI + 0.3 and (best_r is None or lo < best_r[1]):
                best_r = (r_fbt, lo, hi)
        if best_r and (best is None or best_r[2] - best_r[1] < best[3][2] - best[3][1]):
            best = (r_s, c_s, span, best_r, band)
    r_s, c_s, span, (r_fbt, vlo, vhi), band = best
    nom = [24.5 + (UCC["vfb"][1] * (1 + r_fbt / R_FBB) - va) / sl for p, va, sl in span]
    loaded = band(r_fbt, [x for x in span if x[0] >= SYS["p_standby"]])
    return dict(r_s=r_s, c_s=c_s, r_fbt=r_fbt, band=(vlo, vhi), nom=(min(nom), max(nom)), loaded=loaded,
                fb_normal_margin=vlo - VOUT_HI)


BK = backup_design()
R_FBT, R_AUXS, C_AUXS = BK["r_fbt"], BK["r_s"], BK["c_s"]
BACKUP_BAND = BK["band"]
VOUT_BACKUP = 0.5 * (BK["nom"][0] + BK["nom"][1])


# ============================================================================================ start-up / VDD
def startup():
    """3 x BSS126 cascode on Vin/3 taps; bottom FET self-biased by RS_START (A-13)."""
    res = {}
    for name, vth in (("min", BSS["vth"][2]), ("max", BSS["vth"][0])):
        k = BSS["idss_min"] / vth ** 2
        lo, hi = 0.0, abs(vth) / RS_START
        for _ in range(80):
            i = (lo + hi) / 2
            vgs = -i * RS_START
            f = k * (vgs - vth) ** 2 - i if vgs > vth else -i
            lo, hi = (i, hi) if f > 0 else (lo, i)
        res[name] = (lo + hi) / 2
    i_min, i_max = res["min"], res["max"]
    c_vdd = N_CVDD * C_VDD_EACH * 0.7                                    # A-10
    leak = BAT54["ir85"]                                                 # A-28: follower blocking diode at 85 C
    t_start = (c_vdd * UCC["vddon"][2] / (i_min - UCC["istart"] - leak), c_vdd * UCC["vddon"][1] / ((i_min + i_max) / 2 - 50e-6))
    t_start_125 = c_vdd * UCC["vddon"][2] / max(i_min - UCC["istart"] - BAT54["ir125"], 1e-9)
    p_fet = {v: v / 3 * i_max for v in (VIN_MAX, VIN_TR)}
    # A-18: worst VDD drain while the converter starts (no help from the aux winding yet)
    i_hold = (UCC["ivdd"][1] + 1.25 * QG_MAX * FS_RANGE[1] + UCC["vref"][2] / R_PU + 2.0e-3 + 5.0 / R_PULL_LINE
              + 5.0 / R_SS + 5.0 / 110e3 + 13e-6 + I_VREF_X + leak)
    t_hold = c_vdd * UCC["hyst_min"] / i_hold                          # VDDON -> VDDOFF with the smallest hysteresis
    t_recharge = (c_vdd * UCC["hyst_min"] / (i_max - UCC["istart"]),                 # fastest: smallest hysteresis
                  c_vdd * (UCC["vddon"][2] - UCC["vddoff"][0]) / (i_min - UCC["istart"] - leak))
    duty = (i_min / (i_min + i_hold), i_max / (i_max + UCC["ivdd"][0] + 1.0e-3))     # hiccup: converter-on share
    p_fet_hiccup = VIN_MAX / 3 * i_max                                  # start-up is on while VDD recharges
    v_e = (BAT54["ir85"] * R_VDDE, BAT54["ir125"] * R_VDDE)            # AX-05 emitter voltage, VAUX = 0
    return dict(i_min=i_min, i_max=i_max, c_vdd=c_vdd, t_start=t_start, t_start_125=t_start_125, p_fet=p_fet,
                duty=duty, t_run=t_hold, p_fet_hiccup=p_fet_hiccup, dtj=p_fet_hiccup * BSS["rth_ja"], i_hold=i_hold,
                t_hold=t_hold, v_fet=VIN_TR / 3 + 3.0, t_recharge=t_recharge, v_e=v_e)


SU = startup()


# ============================================================================================ input: fuse, R, C, surge
C_BULK_TOT = N_BULK * C_BULK_EACH + C_HF[0] * C_HF[1]
R_IN = R_LIM_EACH * N_LIM


def input_network():
    """AX-01 / AX-02: hot plug, resistor-limited fuse fault, port surges into the RC absorber."""
    c, r = C_BULK_TOT, R_IN
    tau = r * c
    i_pk = VIN_TR / r
    i2t = i_pk ** 2 * r * c / 2
    e_hot = 0.5 * c * VIN_TR ** 2
    i_fault = VIN_TR / r                                  # short anywhere behind the resistors: <= 25 A at the fuse
    i2t_clear = 2 * FUSE["i2t"]                           # A-19
    e_fault = R_LIM_EACH * i2t_clear                      # per resistor
    e_cap = RLIM["e_per_ohm"] * R_LIM_EACH
    surges = []
    for vres, tp, name in ((SPD["up"], 20e-6, "SPD Up 4 kV / 20 us (A-20)"), (2750.0, 20e-6, "review 2.75 kV / 20 us"),
                           (2000.0, 50e-6, "review 2 kV / 50 us")):
        for v0 in (VIN_MAX, VIN_TR):
            vb = v0 + (vres - v0) * (1 - math.exp(-tp / tau))
            i0 = (vres - v0) / r
            surges.append(dict(name=name, v0=v0, vres=vres, tp=tp, vbulk=vb, i0=i0, dvdt=i0 / c, v_r=(vres - v0) / N_LIM,
                               i2t=i0 ** 2 * tp, e_r=i0 ** 2 * R_LIM_EACH * tp, i_cap=i0 / N_BULK))
    t_bleed_string = 3 * R_STR * c * math.log(VIN_TR / 60.0)
    t_bleed_su = c * (VIN_OFF_T - 60.0) / (SU["i_min"] * 0.9)          # start-up source cycling after brown-out
    return dict(tau=tau, i_pk=i_pk, i2t=i2t, e_hot=e_hot, e_hot_r=e_hot / N_LIM, i_fault=i_fault, i2t_clear=i2t_clear,
                e_fault=e_fault, e_cap=e_cap, surges=surges, c=c, r=r, t_bleed_string=t_bleed_string,
                t_bleed_su=t_bleed_su, corner_hz=1 / (2 * math.pi * tau),
                p_r_200=OPS[200.0]["i_in"] ** 2 * R_LIM_EACH)


INP = input_network()
SURGE_MAX = max(s["vbulk"] for s in INP["surges"] if s["v0"] == VIN_MAX)
SURGE_MAX_TR = max(s["vbulk"] for s in INP["surges"])


# ============================================================================================ string: brown-in + OV lockout
def taps(vin, r1, r2, rh, high, vref=5.0, kt=1.0, k1=1.0, k2=1.0, kh=1.0):
    """Node voltages (LSO, LS) of HV - 3 x R_STR - LSO - R_D1 - LS - R_D2 - PGND, with R_HYS from LINE_OK (high:
    pulled to VREF through R_PULL_LINE, low: at PGND). CRHV voltage coefficient included (A: VCR at Vin/3)."""
    rt = 3 * R_STR * kt * (1 - CRHV["vcr"] * vin / 3)
    a1, a2, ah = r1 * k1, r2 * k2, rh * kh
    rx, vx = (ah + R_PULL_LINE, vref) if high else (ah, 0.0)
    # G matrix for [LSO, LS]
    g11, g12 = 1 / rt + 1 / a1, -1 / a1
    g21, g22 = -1 / a1, 1 / a1 + 1 / a2 + 1 / rx
    b1, b2 = vin / rt, vx / rx
    det = g11 * g22 - g12 * g21
    return (b1 * g22 - g12 * b2) / det, (g11 * b2 - g21 * b1) / det


def vin_for(fun, target, lo=50.0, hi=2000.0):
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if fun(mid) < target else (lo, mid)
    return 0.5 * (lo + hi)


def ov_ref(high_out, vref, vr25, vos):
    """TLV3202 channel 1 non-inverting input: 2.5 V reference through R_OVH1, hysteresis R_OVH2 from 1OUT."""
    vo = vref if high_out else 0.0
    return vr25 + (vo - vr25) * R_OVH1 / (R_OVH1 + R_OVH2) + vos


def string_design():
    """Solve R_D1, R_D2, R_HYS for brown-in 225 V, brown-out 180 V and the OV lockout, then the tolerance bands."""
    def solve(v_ov):
        def eqs(x):
            r1, r2, rh = np.exp(x)
            on = taps(VIN_ON_T, r1, r2, rh, False)[1] - DET["vitp"][1]
            off = taps(VIN_OFF_T, r1, r2, rh, True)[1] - DET["vitn"][1]
            ov = taps(v_ov, r1, r2, rh, True)[0] - ov_ref(True, 5.0, ATL["vref"][1], 0.0)
            return [on / DET["vitp"][1], off / DET["vitn"][1], ov / 2.5]
        x = fsolve(eqs, np.log([13e3, 53e3, 3.3e6]), xtol=1e-12)
        return [e96(v) for v in np.exp(x)]

    def bands(r1, r2, rh):
        on, off, ov, rel = [], [], [], []
        tol = dict(kt=(0.99, 1.01), k1=(0.999, 1.001), k2=(0.999, 1.001), kh=(0.99, 1.01), vref=(UCC["vref"][0], UCC["vref"][2]))
        for c in corners(tol):
            for i in (0, 2):
                on.append(vin_for(lambda v: taps(v, r1, r2, rh, False, **c)[1], DET["vitp"][i]))
                off.append(vin_for(lambda v: taps(v, r1, r2, rh, True, **c)[1], DET["vitn"][i]))
            for vr25 in (ATL["vref"][0] - ATL["vdev"], ATL["vref"][2] + ATL["vdev"]):
                for vos in (-TLV["vos"], TLV["vos"]):
                    ov.append(vin_for(lambda v: taps(v, r1, r2, rh, True, **c)[0], ov_ref(True, c["vref"], vr25, vos)))
                    rel.append(vin_for(lambda v: taps(v, r1, r2, rh, True, **c)[0], ov_ref(False, c["vref"], vr25, vos)))
        nom = dict(on=vin_for(lambda v: taps(v, r1, r2, rh, False)[1], DET["vitp"][1]),
                   off=vin_for(lambda v: taps(v, r1, r2, rh, True)[1], DET["vitn"][1]),
                   ov=vin_for(lambda v: taps(v, r1, r2, rh, True)[0], ov_ref(True, 5.0, 2.5, 0.0)),
                   rel=vin_for(lambda v: taps(v, r1, r2, rh, True)[0], ov_ref(False, 5.0, 2.5, 0.0)))
        return dict(on=(min(on), nom["on"], max(on)), off=(min(off), nom["off"], max(off)),
                    ov=(min(ov), nom["ov"], max(ov)), rel=(min(rel), nom["rel"], max(rel)))

    v_ov = VIN_TR + 30.0
    for _ in range(6):
        r1, r2, rh = solve(v_ov)
        b = bands(r1, r2, rh)
        v_ov += (VIN_TR + OV_MARGIN) - b["ov"][0] + 0.5
        if b["ov"][0] >= VIN_TR + OV_MARGIN:
            break
    # compensation: R_D1 C_D1 = R_D2 C_D2 = COMP_M x R_STR C_STR (E12 C0G, rounded down = under-compensated)
    tau = R_STR * C_STR
    c2 = e12_down(COMP_M * tau / r2)
    c1 = e12_down(r2 * c2 / r1 * 1.0001)
    ratios = []
    for kt in (0.95, 1.05):
        for kb in (0.95, 1.05):
            ct = C_STR / 3 * kt
            cb = 1 / (1 / (c1 * kb) + 1 / (c2 * kb)) + C_LSF
            hf = ct / (ct + cb)
            dc = taps(VIN_TR, r1, r2, rh, True)[0] / VIN_TR
            ratios.append(hf / dc)
    k_hf = (min(ratios), max(ratios))
    fast_max = b["ov"][2] / k_hf[0]
    return dict(r1=r1, r2=r2, rh=rh, c1=c1, c2=c2, k_hf=k_hf, fast_max=fast_max, **b)


STR = string_design()
LINE = dict(on=STR["on"], off=STR["off"])
R_D1, R_D2, R_HYS, C_D1, C_D2 = STR["r1"], STR["r2"], STR["rh"], STR["c1"], STR["c2"]


def ov_lockout():
    """Surge timing: lockout response (A-23), V_DS at the moment switching stops, then the RC-limited bulk peak."""
    t_resp = 1.0e-6
    dvdt = max(s["dvdt"] for s in INP["surges"])
    v_stop = max(STR["ov"][2], STR["fast_max"] + dvdt * t_resp)       # slow swell: DC level; fast surge: early trip + ramp
    vds_stop = v_stop + clamp_v(ILIM["max_eff"], True, True) + 40.0
    vds_locked = SURGE_MAX_TR                                      # switch off and demagnetised: V_DS = V_bulk
    v_fet = SURGE_MAX_TR / 3                                       # start-up cascode (off) per FET
    swell = 1500.0                                                 # review: a slow 1.5 kV swell passes fully
    return dict(t_resp=t_resp, dvdt=dvdt, v_stop=v_stop, vds_stop=vds_stop, vds_locked=vds_locked, v_fet=v_fet,
                swell=swell, swell_vds=swell / MOS["vbr"], swell_fet=swell / 3 / BSS["vds"],
                swell_c=swell / CBULK["v70"], vds_1100=vds_peak(STR["ov"][2], ILIM["max_eff"], True, True))


OVL = ov_lockout()


# ============================================================================================ foldback (AX-03)
def af1_steady(v_plateau, t_p, t_cycle, spike=None):
    """Stage-1 detector peak in periodic steady state: charge through R_AF1 toward v*k during t_p (and/or a spike),
    discharge through R_AF2 otherwise. spike = (amplitude, duration) replaces the plateau when given."""
    k = R_AF2 / (R_AF1 + R_AF2)
    tau_c = R_AF1 * R_AF2 / (R_AF1 + R_AF2) * C_AF
    tau_d = R_AF2 * C_AF
    v_in, t_in = (spike if spike else (v_plateau, t_p))
    b = math.exp(-t_in / tau_c)
    a = math.exp(-(t_cycle - t_in) / tau_d)
    return v_in * k * (1 - b) / (1 - a * b), a


def foldback():
    """Staircase condition, folded frequency, detector levels (light load, short-circuit spike), threshold window."""
    v0h, rdh = dout_model(150.0)
    i_s = N * ILIM["max_eff"]
    v_sec = v0h + rdh * i_s                                           # A-21: reset voltage in a hard short (hot)
    rows = []
    for vin in (600.0, VIN_MAX, VIN_TR):
        for ton in T_ON_MIN:
            up = vin * ton / LP_RANGE[0]
            dn_n = N * v_sec * (1 / FS_RANGE[1] - ton) / LP_RANGE[0]
            dn_f = N * v_sec * (1 / F_FOLD_RANGE[1] - ton) / LP_RANGE[0]
            rows.append(dict(vin=vin, ton=ton, up=up, dn_n=dn_n, dn_f=dn_f))
    f_safe = N * v_sec / (T_ON_MIN[1] * (VIN_TR + N * v_sec))
    v_on = VIN_TR * T_ON_MIN[1] / (N * (1 / FS_RANGE[1] - T_ON_MIN[1])) - 0.9    # Vout below which a staircase starts
    th = (Z68["vz_ua"][0] + N7002["vth"][0], Z68["vz_ua"][1] + N7002["vth"][1])  # stage-1 level that turns Q_INV on
    k = R_AF2 / (R_AF1 + R_AF2)
    v_fold = tuple((t / k + vfd) * NS / NA - 0.9 for t, vfd in zip(th, VFD_US1G))
    # light load: the detector must stay above the threshold while the converter runs every cycle
    light = []
    for p in (0.5, 1.0, 3.0, SYS["p_standby"], POUT):
        op = operating_point(600.0, p)
        vpl = NA / NS * (VOUT_LO + 0.9) - VFD_US1G[1]
        pk, a = af1_steady(vpl, op["d2"] / FS_RANGE[1], 1 / FS_RANGE[1])
        g = pk - Z68["vz_ua"][1]                                       # gate peak (worst high Zener)
        gap = (1 - op["d2"]) / FS_RANGE[0]
        # the gate holds through R_QG but cannot stay above AF1 + 0.6 V (Zener forward path)
        g_min = min(g * math.exp(-gap / (R_QG * N7002["ciss"][0])), pk * math.exp(-gap / (R_AF2 * C_AF)) + 0.6)
        light.append(dict(p=p, af1=pk, g_min=g_min, hold=g_min >= N7002["vth"][1]))
    # short circuit at the normal frequency before the foldback acts: spike only (A-22)
    v_cl = clamp_v(ILIM["max_eff"], True, True) - N * v_sec
    t_sp = LLK_WC * ILIM["max_eff"] / v_cl
    spike = {f: af1_steady(0, 0, 1 / FS_RANGE[1], (f * v_cl * NA / NP - VFD_US1G[0], t_sp))[0] for f in SPIKE_FRAC}
    # engagement after a hard short: detector + gate decay from the full-load level to the threshold, + FBK rise
    pk_fl = af1_steady(NA / NS * (VOUT_HI + 1.0) - VFD_US1G[0], OPS[600.0]["d2"] / FS_NOM, 1 / FS_NOM)[0]
    # after the output collapses AF1 decays through R_AF2 and pulls the gate down with it (Zener forward); Q_INV off
    # below Vth, then FBK rises through R_FBK into the Q_FB gate
    t_eng = R_AF2 * C_AF * math.log(pk_fl / (N7002["vth"][0] - 0.6)) + 3 * R_FBK * N7002["ciss"][1]
    p_fold = (0.5 * LP_RANGE[0] * ILIM["min"] ** 2 * F_FOLD_RANGE[0] * 0.82,
              0.5 * LP_RANGE[1] * ILIM["max_eff"] ** 2 * F_FOLD_RANGE[1])
    i_out_sc = p_fold[1] / v_sec                                       # rectifier current in a hard short (folded)
    return dict(v_sec=v_sec, rows=rows, f_safe=f_safe, v_on=v_on, th=th, v_fold=v_fold, light=light, spike=spike,
                t_sp=t_sp, t_eng=t_eng, cycles_eng=t_eng * FS_RANGE[1], p_fold=p_fold, i_out_sc=i_out_sc,
                p_d_sc=p_fold[1] * 0.95)


FB = foldback()


# ============================================================================================ overload timer (AX-04)
VOFF = (UCC["comp_off"] - 0.10, UCC["comp_off"] + 0.10)               # A-24


def comp_for(p_mag, lp, fs, acs, voff, rcs):
    return voff + acs * rcs * math.sqrt(2 * p_mag / (lp * fs))


def overload_design():
    """COMP-level timer: never trips at 30 W in the worst corner; trips after t_trip once COMP sits higher."""
    p_mag30 = max(OPS[v]["p_mag"] for v in VIN_TABLE)
    comp_rated = comp_for(p_mag30, LP_RANGE[0], FS_RANGE[0], UCC["acs"][2], VOFF[1], RCS * 1.01)
    v_trip = (ATL["vref"][0] - ATL["vdev"] - TLV["vos"], ATL["vref"][2] + ATL["vdev"] + TLV["vos"])
    target = comp_rated + 0.10
    r_ta = e96_up(R_TB * (target / v_trip[0] * 1.01 / 0.99 - 1))
    k = (R_TB * 0.99 / (r_ta * 1.01 + R_TB * 0.99), R_TB / (r_ta + R_TB), R_TB * 1.01 / (r_ta * 0.99 + R_TB * 1.01))
    comp_trip = (v_trip[0] / k[2], ATL["vref"][1] / k[1], v_trip[1] / k[0])
    comp_max = (UCC["vref"][0] - UCC["voh_drop"], UCC["vref"][2])     # COMP high in current limit (opto off)
    r_par = r_ta * R_TB / (r_ta + R_TB)

    def t_trip(c, kk, vt, vc):
        fin = kk * vc
        return math.inf if fin <= vt else -r_par * c * math.log(1 - vt / fin)
    c_tmr = None
    for c in C_TMR_OPTS:
        if t_trip(c * C_TMR_TOL[0], k[2], v_trip[0], comp_max[1]) >= T_TRIP_MIN:
            c_tmr = c
            break
    tt = (t_trip(c_tmr * C_TMR_TOL[0], k[2], v_trip[0], comp_max[1]), t_trip(c_tmr, k[1], 2.5, 5.0),
          t_trip(c_tmr * C_TMR_TOL[1], k[0], v_trip[1], comp_max[0]))
    # BAS16 latch diode reverse leakage (2OUT low, COMPF ~2.4 V) pulls the timer node down: error at 85 / 125 C,
    # exponential between the datasheet 25 C and 150 C maxima
    leak = [BAS16["ir25"] * (BAS16["ir150"] / BAS16["ir25"]) ** ((t - 25.0) / 125.0) for t in (85.0, 125.0)]
    leak_err = [i * r_par / v_trip[0] for i in leak]
    # trip power in three corners: P = 0.5 Lp fs ((COMP_trip - Voff) / (Acs Rcs))^2, limited by the CS clamp
    def p_at(comp, lp, fs, acs, voff, vcs, rcs):
        ipk = min((comp - voff) / (acs * rcs), vcs / rcs)
        return 0.5 * lp * ipk ** 2 * fs, ipk
    p30 = POUT / p_mag30
    trip = dict(min=p_at(comp_trip[0], LP_RANGE[0], FS_RANGE[0], UCC["acs"][2], VOFF[1], UCC["vcs"][0], RCS * 1.01),
                typ=p_at(comp_trip[1], LP, FS_NOM, UCC["acs"][1], UCC["comp_off"], UCC["vcs"][1], RCS),
                max=p_at(comp_trip[2], LP_RANGE[1], FS_RANGE[1], UCC["acs"][0], VOFF[0], UCC["vcs"][2], RCS * 0.99))
    trip = {k_: dict(p_mag=v[0], ipk=v[1], pout=v[0] * p30) for k_, v in trip.items()}
    return dict(r_ta=r_ta, c_tmr=c_tmr, k=k, comp_rated=comp_rated, comp_trip=comp_trip, comp_max=comp_max,
                v_trip=v_trip, t_trip=tt, r_par=r_par, trip=trip, compf_rated=comp_rated * k[2], leak_err=leak_err)


OVLD = overload_design()
R_TA, C_TMR = OVLD["r_ta"], OVLD["c_tmr"]


def device_temps(op, scale_tvs=1.0, avg=1.0, inst=None, sw=None):
    """Junction / hot-spot temperatures at T_AMB from one operating point (A-9, A-26). avg = hiccup duty; inst =
    the instantaneous operating point during the on-time (adds P_on x Rth junction-to-lead/case)."""
    o = op
    sw = sw or SW_DESIGN
    rows = []
    for name, p, r_board, r_jc, lim, key in (
            (sw, o["loss"]["switch conduction"] + o["loss"]["switch turn-on (C_oss + winding C)"] + o["loss"]["switch turn-off"], 35.0, DEVICES[sw]["rth_jc"], DEVICES[sw]["tj_max"], "sw"),
            ("VS-8ETU04S-M3", o["p_d"], 35.0, DOUT["rth_jc"], DOUT["tj_max"], "d"),
            ("SMCJ70A (each)", o["loss"]["TVS clamp"] * scale_tvs / TVS["n"], TVS["rth_board"], TVS["rth_jl"], TVS["tj_max"], "tvs"),
            ("T1 transformer", o["loss"]["transformer core"] + o["loss"]["transformer copper"], T1_RTH, 0.0, 155.0, "t1")):
        p_on = p if inst is None else {"sw": inst["loss"]["switch conduction"] + inst["loss"]["switch turn-on (C_oss + winding C)"] + inst["loss"]["switch turn-off"],
                                         "d": inst["p_d"], "tvs": inst["loss"]["TVS clamp"] * scale_tvs / TVS["n"],
                                         "t1": inst["loss"]["transformer core"] + inst["loss"]["transformer copper"]}[key]
        t = T_AMB + p * avg * r_board + (p_on if inst is not None else p) * r_jc
        rows.append(dict(name=name, p=p * avg, p_on=p_on, t=t, lim=lim))
    return rows


def overload_thermal():
    """AX-04: temperatures at the rating, just below the timer threshold, and in hiccup at the highest limit."""
    out = {}
    out["30 W, 1000 V, typical"] = device_temps(OPS[VIN_MAX])
    out["30 W, 1000 V, worst leakage (A-1)"] = device_temps(operating_point(VIN_MAX, llk=LLK_WC))
    out["30 W, 1000 V, alternate switch %s" % SW_ALT] = device_temps(SWCMP[SW_ALT][VIN_MAX], sw=SW_ALT)
    out["%.1f W port hold (<= %d s), 1000 V, worst leakage" % (SYS["p_hold2"], SYS["t_port"])] = device_temps(
        operating_point(VIN_MAX, SYS["p_hold2"], llk=LLK_WC))
    out["%.1f W port hold, 1100 V transient, worst leakage" % SYS["p_hold2"]] = device_temps(
        operating_point(VIN_TR, SYS["p_hold2"], llk=LLK_WC))
    tr = OVLD["trip"]
    out["just below trip, typical (%.0f W)" % tr["typ"]["pout"]] = device_temps(operating_point(VIN_MAX, tr["typ"]["pout"]))
    op_max = operating_point(VIN_MAX, tr["max"]["pout"], fs=FS_RANGE[1], lp=LP_RANGE[1])
    out["just below trip, high corner (%.0f W)" % tr["max"]["pout"]] = device_temps(op_max)
    # hiccup at the highest current limit: on for the slowest trip, off for the fastest VDD recharge
    p_on_out = min(ILIM["p_max"] * 0.85, 0.5 * LP_RANGE[1] * ILIM["max_eff"] ** 2 * FS_RANGE[1] * 0.85)
    op_lim = operating_point(VIN_MAX, p_on_out, fs=FS_RANGE[1], lp=LP_RANGE[1])
    t_off = 0.04 + SU["t_recharge"][0]
    duty = OVLD["t_trip"][2] / (OVLD["t_trip"][2] + t_off)
    out["hiccup at the highest limit (%.0f W on, duty %.0f %%)" % (p_on_out, duty * 100)] = device_temps(op_lim, avg=duty, inst=op_lim)
    return out, dict(p_on=p_on_out, duty=duty, t_off=t_off, dcm=op_lim["dcm"])


OVT, HIC = overload_thermal()


def t1_budget():
    """Largest transformer winding loss at full load (A-32 scaling) that still meets (a) >= 80 % efficiency at
    400-800 V with the design switch and (b) a lowest-corner current limit above the SYS rev E black-start peak
    (SYS-IO-AUX_aux_feed.json, at its 23.15 V). Bisection on T1_CU_FL; the global is restored afterwards."""
    global T1_CU_FL, T1_DESIGN
    keep, design = T1_CU_FL, T1_DESIGN
    T1_DESIGN = None                                  # the requirement is stated on the A-32 scaling
    p_peak_sys = max(SYS["sys_black"][c]["p_peak"] for c in ("fast", "slow"))

    def ok(cu):
        global T1_CU_FL
        T1_CU_FL = cu
        eta_ok = all(operating_point(v)["eta"] >= ETA_MIN for v in (400.0, 600.0, 800.0))
        eta_mag_wc = POUT / operating_point(200.0, llk=LLK_WC)["p_mag"]
        p_lim = 0.5 * LP_RANGE[0] * (UCC["vcs"][0] / (RCS * 1.01)) ** 2 * FS_RANGE[0] * eta_mag_wc
        return eta_ok, p_lim >= p_peak_sys
    out = {}
    for i, name in ((0, "efficiency >= 80 % at 400-800 V"), (1, "lowest current limit >= SYS black-start peak %.1f W" % p_peak_sys)):
        lo, hi = 0.1, 3.0
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if ok(mid)[i] else (lo, mid)
        out[name] = lo
    T1_CU_FL, T1_DESIGN = keep, design
    return out


T1_BUDGET = t1_budget()
T1_CU_MAX = min(T1_BUDGET.values())
# switched winding capacitance that keeps the design switch at <= 150 C at 1000 V (turn-on loss 0.5 C_W V^2 fs)
_V_ON2 = VIN_MAX ** 2 + 0.25 * OPS[VIN_MAX]["vr"] ** 2
C_W_MAX = C_W + ((150.0 - T_AMB) / (35.0 + MOS["rth_jc"]) - SW_PWR[SW_DESIGN]) / (0.5 * _V_ON2 * FS_NOM)


def light_load_input(vin, p_ext, dev=None):
    """Input power at light load. Above the minimum pulse the converter runs every cycle (operating_point); below
    it, it skips: pulses of the minimum on-time (T_ON_MIN, 0.25 us: CS filter + tD) at the rate the magnetising
    power needs, each paying the full turn-on loss at Vin (the ring has decayed), the channel turn-off, the leakage
    energy and the gate charge. Fixed losses (string, bias) as in the loss model."""
    dev = dev or MOS
    op = operating_point(vin, max(p_ext, 0.01), dev=dev)
    ipk_min = vin * T_ON_MIN[0] / LP
    if op["ipk"] >= ipk_min:
        return dict(p_in=op["p_in"], mode="every cycle", rate=FS_NOM, op=op)
    e_p = 0.5 * LP * ipk_min ** 2
    rate = op["p_mag"] / e_p
    sl = switch_loss(vin, 0.0, ipk_min, 0.0, 1.0, dev)              # per pulse (fs = 1)
    e_on = eoss(vin, dev) + 0.5 * C_W * vin ** 2
    per_pulse = e_on + sl["e_ch"] + sl["e_mil"] + 0.5 * LLK * ipk_min ** 2 + QG_MAX * VDD_REG[1]
    p_in = op["p_mag"] + rate * per_pulse + vin ** 2 / (3 * R_STR) + op["loss"]["input path"]
    return dict(p_in=p_in, mode="skip, %.1f k pulses/s" % (rate / 1e3), rate=rate, op=op)


STBY = {v: dict(noload=light_load_input(v, SYS["p_feed"]), standby=light_load_input(v, SYS["p_noport"]))
        for v in VIN_TABLE}

# Stress of the jellybean parts the generator's DC-range check cannot assess (switching nodes, correlated taps):
# rules as gen/dcdclib.check_stress - capacitor <= 80 % of rated voltage, resistor <= 60 % of package power
PART_STRESS = [
    ("C_STR 100p 1kV: string section at the surge peak (%.0f V)" % (SURGE_MAX_TR / 3), SURGE_MAX_TR / 3 <= 0.8 * 1000),
    ("R_SN 150R 2512 at 1100 V (%.2f W)" % (C_SN * (VOUT + VIN_TR / N) ** 2 * FS_RANGE[1]),
     C_SN * (VOUT + VIN_TR / N) ** 2 * FS_RANGE[1] <= 0.6 * 1.0),
    ("C_SN 68p 500V (%.0f V)" % V_DOUT, V_DOUT <= 0.8 * 500),
    ("R_CS 1.5R 1206 each (%.0f mW at 30 W / 200 V; %.0f mW hiccup average at the highest limit)" % (
        OPS[200.0]["ip_rms"] ** 2 / 4 * RCS_EACH * 1e3, (ILIM["max_eff"] * math.sqrt(0.5 / 3)) ** 2 / 4 * RCS_EACH * HIC["duty"] * 1e3),
     max(OPS[200.0]["ip_rms"] ** 2, (ILIM["max_eff"] * math.sqrt(0.5 / 3)) ** 2 * HIC["duty"]) / 4 * RCS_EACH <= 0.6 * 0.25),
    ("R_LED 560R 0805 at the largest LED current (%.0f mW)" % ((OPT["if_max"] + 1.1 / R_BIAS) ** 2 * R_LED * 1e3),
     (OPT["if_max"] + 1.1 / R_BIAS) ** 2 * R_LED <= 0.6 * 0.125),
    ("R_G 10R 0603 gate charge (%.0f mW)" % (QG_MAX * VDD_REG[2] * FS_RANGE[1] * R_G / (R_G + MOS["rg_int"]) * 1e3),
     QG_MAX * VDD_REG[2] * FS_RANGE[1] * R_G / (R_G + MOS["rg_int"]) <= 0.6 * 0.1),
    ("R_GS 10k 0603 (%.0f mW)" % (VDD_REG[2] ** 2 / 10e3 * 0.5 * 1e3), VDD_REG[2] ** 2 / 10e3 * 0.5 <= 0.6 * 0.1),
    ("R_AUX 22R 0603 aux charging (%.0f mW)" % ((2 * SU["i_hold"]) ** 2 * R_AUX * 4 * 1e3), (2 * SU["i_hold"]) ** 2 * R_AUX * 4 <= 0.6 * 0.1),
    ("R_SU_S 4.7k 0603 start-up current (%.1f mW)" % (SU["i_max"] ** 2 * RS_START * 1e3), SU["i_max"] ** 2 * RS_START <= 0.6 * 0.1),
]

# ============================================================================================ output capacitor
C_OUT = N_COUT * CPOL["c"] + C_MLCC_OUT[0] * C_MLCC_OUT[1] * 0.5


def ripple(op):
    c = C_OUT                                                               # A-10
    esr = CPOL["esr"] / N_COUT
    t_off = (1 - op["d2"]) / FS_NOM
    dq = op["io"] * t_off                                                   # charge the caps supply alone
    v_esr = op["is_pk"] * esr
    v_c = dq / c
    return dict(c=c, esr=esr, v_esr=v_esr, v_c=v_c, v_pp=v_esr + v_c, icap=op["icap"], icap_each=op["icap"] / N_COUT)


RIP = {v: ripple(OPS[v]) for v in VIN_TABLE}

# ============================================================================================ control loop
C_Z = None
LIGHT = {v: operating_point(v, 0.1 * POUT) for v in (VIN_MIN, 600.0, VIN_MAX)}
STANDBY = {v: operating_point(v, SYS["p_standby"]) for v in (VIN_MIN, 600.0, VIN_MAX)}
R_C_EA, C_C_EA, C_HF_EA = 22e3, 150e-9, 1.5e-9


def plant(f, op, load, c_ext=0.0):
    """DCM peak-current-mode flyback, v_out / v_comp. load: 'R' resistive, 'P' constant power; c_ext = SYS bus."""
    s = 2j * math.pi * f
    ipk = op["ipk"]
    gpk = 1 / (UCC["acs"][1] * RCS)
    io = op["io"]
    k = 2 * io / ipk
    g_load = (io / VOUT) * (1 if load == "R" else -1)
    esr = CPOL["esr"] / N_COUT
    zc = esr + 1 / (s * C_OUT)
    y = 1 / zc + io / VOUT + g_load
    if c_ext:
        y = y + 1 / (SYS["esr_bus"] + 1 / (s * c_ext))
    zl = 1 / y
    hf = 1 / (1 + s / (2 * math.pi * FS_NOM / math.pi))
    return gpk * k * zl * hf


def comp_opto(f, ctr, fopto, cz):
    """Fast lane + ATL431 integrator + R-C feed-forward (C_FF/C_Z proportional term rolled off by R_FF C_FF)."""
    s = 2j * math.pi * f
    k_fl = ctr * R_PU / (OPT["r_led"] + 25.0)
    return (k_fl * (1 + 1 / (s * cz * R_UP) + (C_FF / cz) / (1 + s * R_FF * C_FF))
            / (1 + s * R_PU * C_P) / (1 + s / (2 * math.pi * fopto)))


def margins(tf, f):
    mag = np.abs(tf)
    ph = np.unwrap(np.angle(tf)) * 180 / math.pi
    i = np.where((mag[:-1] >= 1) & (mag[1:] < 1))[0]
    if len(i) == 0:
        return None, None, None
    i = i[0]
    fc = f[i]
    pm = 180 + ph[i]
    while pm > 180:
        pm -= 360
    j = np.where(ph[i:] <= -180)[0]
    gm = -20 * math.log10(mag[i + j[0]]) if len(j) else 99.0
    return fc, pm, gm


def loop_corners(cz, f):
    rows = []
    fopto_pess = OPTO["fc_100r"] * 100.0 / R_PU
    for vin in (VIN_MIN, 600.0, VIN_MAX):
        for frac, opl in ((1.0, OPS[vin]), (SYS["p_standby"] / POUT, STANDBY[vin]), (0.1, LIGHT[vin])):
            for load in ("R", "P"):
                for ctr_name, ctr in (("lo", OPT["ctr"][0]), ("typ", OPT["ctr"][1]), ("hi", OPT["ctr"][2])):
                    for fo_name, fo in (("pess", fopto_pess), ("typ", 3 * fopto_pess)):
                        for bus in (0.0, SYS["c_bus"]):
                            t = plant(f, opl, load, bus) * comp_opto(f, ctr, fo, cz)
                            fc, pm, gm = margins(t, f)
                            rows.append(dict(vin=vin, load=frac, kind=load, ctr=ctr_name, fopto=fo_name, bus=bus,
                                             fc=fc, pm=pm, gm=gm))
    return rows


def loop_design():
    """Integrator capacitor C_Z: the smallest E6 value (fastest recovery after a load step) whose worst phase margin
    over all corners is >= 50 deg."""
    global C_Z
    f = np.logspace(0, 5.3, 3000)
    for cz in (v * 1e-9 for v in (22, 33, 47, 68, 100, 150, 220)):
        rows = loop_corners(cz, f)
        if min((r["pm"] if r["pm"] is not None else -99) for r in rows) >= 50.0:
            break
    C_Z = cz
    # backup loop (opto open): EA type II, COMP <- FB, sensing the aux winding through D, R_AUXS / C_AUXS
    back = []
    for vin in (VIN_MIN, VIN_MAX):
        for frac in (1.0, 0.1):
            opl = OPS[vin] if frac == 1.0 else LIGHT[vin]
            s = 2j * math.pi * f
            zf = (R_C_EA + 1 / (s * C_C_EA)) / (1 + s * C_HF_EA * (R_C_EA + 1 / (s * C_C_EA)))
            sense = NA / NS / (1 + s * R_AUXS * C_AUXS)
            t = plant(f, opl, "R") * zf / R_FBT * sense
            back.append(dict(vin=vin, load=frac, **dict(zip(("fc", "pm", "gm"), margins(t, f)))))
    return rows, back, f


LOOP, BACKUP, FREQ = loop_design()


def load_step(c_ext, esr_ext, ctr, fopto, i0=0.05, i1=None):
    """Averaged time-domain step of the output current i0 -> i1 (constant current) with the real opto loop: ATL431
    integrator + R-C feed-forward, fast lane, opto pole, COMP node, DCM power source. Returns (min Vout after the
    step, max after release)."""
    i1 = IOUT if i1 is None else i1
    c1, r1 = C_OUT, CPOL["esr"] / N_COUT
    lp, fs, eta = LP, FS_NOM, 0.92
    vf = 1.1

    def ipk_for(p):
        return math.sqrt(2 * p / (lp * fs * eta))
    ipk0 = ipk_for(VOUT_SET * (i0 + SEC_BIAS_I) + AUX_LOAD_I * V_AUX_NOM)
    vcomp = UCC["comp_off"] + UCC["acs"][1] * RCS * ipk0
    ic = UCC["ea_src"][1] + (UCC["vref"][1] - vcomp) / R_PU
    i_led = ic / ctr
    vk = VOUT_SET - (i_led + vf / R_BIAS) * OPT["r_led"] - vf
    v = vc1 = vc2 = VOUT_SET
    vcff = VOUT_SET - ATL["vref"][1]
    dt, t = 0.25e-6, 0.0
    t_step, t_rel, t_end = 2e-3, 22e-3, 42e-3
    vmin, vmax = v, v
    wo = 2 * math.pi * fopto
    while t < t_end:
        iload = (i1 if t_step <= t < t_rel else i0) + SEC_BIAS_I
        ipk = min(max((vcomp - UCC["comp_off"]) / (UCC["acs"][1] * RCS), 0.0), UCC["vcs"][1] / RCS)
        i_src = 0.5 * lp * ipk ** 2 * fs * eta / v - AUX_LOAD_I * V_AUX_NOM / v
        i_net = i_src - iload
        if c_ext:
            v = (i_net + vc1 / r1 + vc2 / esr_ext) / (1 / r1 + 1 / esr_ext)
            vc2 += (v - vc2) / esr_ext / c_ext * dt
        else:
            v = vc1 + i_net * r1
        vc1 += (v - vc1) / r1 / c1 * dt
        i_r = max((v - vk - vf) / OPT["r_led"], 0.0)
        led = max(i_r - vf / R_BIAS, 0.0)
        ic += (ctr * led - ic) * wo * dt
        vcomp += (UCC["ea_src"][1] + (UCC["vref"][1] - vcomp) / R_PU - ic * min(1.0, max(vcomp, 0.0) / 0.1)) / C_P * dt
        vcomp = min(max(vcomp, 0.0), UCC["vref"][1])
        i_ff = (v - vcff - ATL["vref"][1]) / R_FF
        vcff += i_ff / C_FF * dt
        vk += -((v - ATL["vref"][1]) / R_UP - ATL["vref"][1] / R_LO_SEL + i_ff) / C_Z * dt
        vk = min(max(vk, ATL["vref"][1]), v)
        if t_step <= t < t_rel:
            vmin = min(vmin, v)
        elif t >= t_rel:
            vmax = max(vmax, v)
        t += dt
    return vmin, vmax


def steps():
    out = []
    fo = OPTO["fc_100r"] * 100.0 / R_PU
    for bus in (0.0, SYS["c_bus"]):
        for name, ctr in (("lo", OPT["ctr"][0]), ("typ", OPT["ctr"][1]), ("hi", OPT["ctr"][2])):
            vmin, vmax = load_step(bus, SYS["esr_bus"], ctr, fo)
            out.append(dict(bus=bus, ctr=name, vmin=vmin, vmax=vmax))
    return out


STEP = steps()


# ============================================================================================ system model (SYS rev E)
def corner_set(name):
    """Parameter corners of the averaged system model. 'min': least power, slowest soft start, earliest timer trip,
    latest feed connection, fastest HGATE ramp into the largest bus (most current); 'max': most power, fastest
    start, slowest ramp into the smallest bus; 'typ'."""
    kk = OVLD["k"]
    sys_fast = dict(ramp=SYS["ramp"][1], c_bus=SYS["c_bus_range"][1])
    sys_slow = dict(ramp=SYS["ramp"][0], c_bus=SYS["c_bus_range"][0])
    sys_mid = dict(ramp=math.sqrt(SYS["ramp"][0] * SYS["ramp"][1]), c_bus=sum(SYS["c_bus_range"]) / 2)
    if name == "min":
        return dict(lp=LP_RANGE[0], fs=FS_RANGE[0], ff=F_FOLD_RANGE[0], ilim=ILIM["min"], acs=UCC["acs"][2],
                    voff=VOFF[1], ea=UCC["ea_src"][0], css=C_SS * 1.2, ctr=OPT["ctr"][0], eta=ETA_MAG_WC,
                    uvlo_r=SYS["feed_uvlo"][1], uvlo_f=SYS["feed_uvlof"][1], k=kk[2], ctmr=C_TMR * C_TMR_TOL[0],
                    vtrip=OVLD["v_trip"][0], vfold=FB["v_fold"][1], **sys_fast)
    if name == "max":
        return dict(lp=LP_RANGE[1], fs=FS_RANGE[1], ff=F_FOLD_RANGE[1], ilim=ILIM["max_eff"], acs=UCC["acs"][0],
                    voff=VOFF[0], ea=2.0e-3, css=C_SS * 0.8, ctr=OPT["ctr"][2], eta=ETA_MAG,
                    uvlo_r=SYS["feed_uvlo"][0], uvlo_f=SYS["feed_uvlof"][0], k=kk[0], ctmr=C_TMR * C_TMR_TOL[1],
                    vtrip=OVLD["v_trip"][1], vfold=FB["v_fold"][0], **sys_slow)
    return dict(lp=LP, fs=FS_NOM, ff=F_FOLD, ilim=UCC["vcs"][1] / RCS, acs=UCC["acs"][1], voff=UCC["comp_off"],
                ea=UCC["ea_src"][1], css=C_SS, ctr=OPT["ctr"][1], eta=ETA_MAG, uvlo_r=sum(SYS["feed_uvlo"]) / 2,
                uvlo_f=sum(SYS["feed_uvlof"]) / 2, k=kk[1], ctmr=C_TMR, vtrip=2.5, vfold=sum(FB["v_fold"]) / 2,
                **sys_mid)


def bus_load(v, efuse_on, i_port):
    """SYS-IO-AUX rev E post-inhibit load on the bus (A-17): logic constant power above 6 V, CTRL constant power and
    the port current behind the +24V eFuse."""
    i = SYS["p_logic"] / v if v > 6.0 else 0.0
    if efuse_on:
        i += SYS["p_ctrl"] / max(v, 1.0) + i_port
    return i


def avg_sim(kind, corner, i_port=None, t_end=0.30, dt=2e-6, cabinet_ok=False):
    """Averaged time-domain model of AUX-HV + the SYS-IO-AUX rev E feed (LM74800, HGATE ramp, A-25) + bus (A-17).
    kind 'start': controller just reached VDDON, output and bus at 0 V; the feed enables at its UVLO unless the
    cabinet holds it off (cabinet_ok). kind 'handover': AUX-HV idles at its set point with the feed held off; at
    t0 the cabinet is lost at its falling threshold, the bus decays under the inhibited load, HGATE is enabled
    1.08 ms later and ramps from the bus level. COMP is quasi-static (its 33 kHz pole is far above the loop)."""
    c = corner_set(corner)
    i_port = SYS["i_port"] if i_port is None else i_port
    lp, fs, ff, ilim, acs, voff, ea = c["lp"], c["fs"], c["ff"], c["ilim"], c["acs"], c["voff"], c["ea"]
    rcs = RCS
    ctr, wo = c["ctr"], 2 * math.pi * OPTO["fc_100r"] * 100.0 / R_PU
    vref, vf_led = 5.0, 1.1
    c_a, c_b, ramp, vgs_on = C_OUT, c["c_bus"], c["ramp"], SYS["vgs_on"]
    vddon, vddoff = UCC["vddoff"][2] + UCC["hyst_min"], UCC["vddoff"][2]
    i_hold, c_vdd = SU["i_hold"], SU["c_vdd"]
    p_aux = AUX_LOAD_I * V_AUX_NOM
    k, ctmr, vtrip, r_par = c["k"], c["ctmr"], c["vtrip"], R_TA * R_TB / (R_TA + R_TB)
    vca = vcb = 0.0
    vss = vcomp = ic = 0.0
    vk = ATL["vref"][1]
    vcff = 0.0
    compf = 0.0
    latch, on, vdd = False, True, vddon
    en, vg, t_en, t_off, ov_latched, efuse, merged = False, None, 0.0, 0.0, False, False, False
    fold_wait, idle, t0 = 0.0, 1.0, 0.0
    if kind == "handover":                     # idle at the set point: loop states at their no-load operating point
        ic = ea + (vref - voff) / R_PU
        vca, vss, vcomp, vcff = VOUT_SET, vref, voff, VOUT_SET - ATL["vref"][1]
        vk = VOUT_SET - vf_led - (ic / ctr + vf_led / R_BIAS) * OPT["r_led"]
        t0 = 0.02
        vcb, efuse = SYS["cab_lost"][0], True
    res = dict(vmax=0.0, vmin_after=99.0, bus_min=99.0, compf_max=0.0, vdd_min=vddon, trip=False, t_settle=None,
               t_trip=None, ov_latched=False, t_take=None, v_take=None, t_ramp=None, uvlo=False, p_peak=0.0, t_over=0.0,
               t_cond=None, trace=[])
    n = int(t_end / dt)
    rec = max(1, int(2e-4 / dt))
    for step in range(n):
        t = step * dt
        # ---------------------------------------------------------------- controller
        if on and not latch:
            vfree = vref + R_PU * (ea - ic) if ic < ea + vref / R_PU else 0.1 * (vref / R_PU + ea) / ic
            vfree = min(max(vfree, 0.0), vref - UCC["voh_drop"] + 0.2)
            if vfree > vss + 0.35:
                i_ss = ea + (vref - (vss + 0.35)) / R_PU - ic
                vcomp = vss + 0.35
            else:
                i_ss = 0.0
                vcomp = vfree
            vss += ((vref - vss) / R_SS + max(i_ss, 0.0)) / c["css"] * dt
            vss = min(vss, vref)
            compf += ((vcomp - compf) / R_TA - compf / R_TB) / ctmr * dt
            if compf >= vtrip:
                latch = True
                if res["t_trip"] is None:
                    res["t_trip"] = t - t0
            ipk = min(max((vcomp - voff) / (acs * rcs), 0.0), ilim)
        else:
            vcomp = 0.0
            ipk = 0.0
            if not on:
                compf -= compf / (r_par * ctmr) * dt
        # foldback: below the output threshold, or one folded period after an idle spell (detector decayed)
        if ipk <= 0.0:
            idle += dt
        fold = vca < c["vfold"]
        if ipk > 0.0 and idle > 60e-6:
            fold_wait += dt
            if fold_wait < 1 / ff:
                ipk = 0.0
            else:
                idle, fold_wait = 0.0, 0.0
        elif ipk > 0.0:
            idle = 0.0
        f = ff if fold else fs
        p = 0.5 * lp * ipk ** 2 * f * c["eta"]
        v_follow = (NA / NS * (vca + 0.75) - 0.8 - 2 * i_hold * R_AUX - (UCC["ivdd"][1] / BC817["hfe_min"]) * R_ZREG
                    - BC817["vbe"][1] - 0.40)
        supported = on and v_follow >= vdd - 0.05             # the follower takes VDD over (BAT54 conducts)
        if supported:
            p -= p_aux
            if res["t_take"] is None:
                res["t_take"], res["v_take"] = t, vca
        i_src = min(max(p, 0.0) / max(vca, 0.3), N * ipk) if ipk > 0 else 0.0
        if p < 0:
            i_src = p / max(vca, 0.3)
        # ---------------------------------------------------------------- VDD
        if on:
            if supported:
                vdd = max(vdd, min(VDD_REG[0], v_follow))
            else:
                vdd -= i_hold / c_vdd * dt
            if vdd <= vddoff:
                on, latch = False, False
                vss = vcomp = 0.0
                res["uvlo"] = True
        else:
            vdd += (SU["i_min"] - UCC["istart"] - BAT54["ir85"]) / c_vdd * dt
            if vdd >= vddon:
                on = True
        res["vdd_min"] = min(res["vdd_min"], vdd)
        # ---------------------------------------------------------------- secondary loop (ATL431, fast lane, opto)
        i_ff = (vca - vcff - ATL["vref"][1]) / R_FF
        vcff += i_ff / C_FF * dt
        vk += -((vca - ATL["vref"][1]) / R_UP - ATL["vref"][1] / R_LO_SEL + i_ff) / C_Z * dt
        vk = min(max(vk, ATL["vref"][1]), max(vca, ATL["vref"][1]))
        i_r = max((vca - vk - vf_led) / OPT["r_led"], 0.0)
        led = max(i_r - vf_led / R_BIAS, 0.0)
        ic += (ctr * led - ic) * min(wo * dt, 1.0)
        # ---------------------------------------------------------------- feed (LM74800 + HGATE ramp) and bus
        i_own = SEC_BIAS_I + max(vca - 11.4, 0.0) / 2.2e3 + vca / 106.34e3 + vca / 105.23e3 + 0.495e-3
        if vcb >= SYS["ef_r"][0]:
            efuse = True
        elif vcb < SYS["ef_f"][1]:
            efuse = False
        i_bl = bus_load(vcb, efuse, i_port) if vcb > 1.0 else 0.0
        if kind == "start" and not en and not ov_latched and not cabinet_ok:
            t_en = t_en + dt if vca > c["uvlo_r"] else 0.0
            if t_en >= SYS["t_drv"]:
                en, vg = True, vcb
        if kind == "handover" and not en and t >= t0 + SYS["t_hg_en"]:
            en, vg = True, vcb
        i_fet = 0.0
        if en:
            vg += ramp * dt
            if merged or vg - vgs_on >= vca:                     # FET fully on: AUX output and bus are one node
                merged = True
                i_fet = i_bl
            elif vg - vgs_on > vcb:                              # source follower: the bus follows the gate
                if res["t_cond"] is None:
                    res["t_cond"] = t - t0
                i_fet = c_b * ramp + i_bl                        # the FET carries the ramp current + the load
                vcb = vg - vgs_on
            else:
                vcb -= i_bl / c_b * dt
            t_off = t_off + dt if vca < c["uvlo_f"] else 0.0
            if t_off >= LM748["t_off"]:
                en, vg, t_off, merged = False, None, 0.0, False
            if vca > SYS["feed_ov"][0]:
                ov_latched, en, vg, merged = True, False, None, False
        elif kind == "handover" and t < t0:
            vcb = SYS["cab_lost"][0]                             # the cabinet still holds the bus
        else:
            vcb = max(vcb - i_bl / c_b * dt, 0.0)
        if merged:
            vca += (i_src - i_own - i_fet) / (c_a + c_b) * dt
            vcb = vca
        else:
            vca += (i_src - i_own - i_fet) / c_a * dt
        vca = max(vca, 0.0)
        # ---------------------------------------------------------------- bookkeeping
        p_aux_out = vca * (i_own - SEC_BIAS_I + i_fet)
        if kind != "handover" or t >= t0:
            res["vmax"] = max(res["vmax"], vca)
            if p_aux_out > res["p_peak"]:
                res["p_peak"], res["t_peak"] = p_aux_out, t - t0
            res["t_over"] += dt if p_aux_out > OVLD["trip"]["min"]["pout"] else 0.0
        res["compf_max"] = max(res["compf_max"], compf)
        res["trip"] = res["trip"] or latch
        if kind == "handover" and t >= t0:
            res["bus_min"] = min(res["bus_min"], vcb)
            res["vmin_after"] = min(res["vmin_after"], vca)
        if res["t_settle"] is None and (kind == "start" or t > t0 + 2e-3):
            if cabinet_ok and vca >= VOUT_LO - 0.05:
                res["t_settle"] = t
            elif not cabinet_ok and merged and vcb >= VOUT_LO - 0.1:
                res["t_settle"] = t - t0
        if kind == "start" and res["t_ramp"] is None and vca >= 0.97 * VOUT_SET:
            res["t_ramp"] = t
        if step % rec == 0:
            res["trace"].append((t, vca, vcb if (en or kind == "handover") else float("nan"), vdd, vcomp, compf))
    res["ov_latched"] = ov_latched
    res["v_end"], res["bus_end"] = vca, vcb
    return res


SIM = {}


def system_runs():
    """SYS-IO-AUX rev E cases: start with the cabinet present (feed held off: overshoot, VDD takeover), black start
    (one HGATE ramp) and cabinet hand-over, both with the 27.8 W post-inhibit load (0.6 A port) and with the
    30.1 W two-coil port (0.7 A)."""
    for corner in ("min", "typ", "max"):
        SIM[("alone", corner)] = avg_sim("start", corner, cabinet_ok=True, t_end=0.10)
        SIM[("black", corner)] = avg_sim("start", corner, t_end=1.9, dt=4e-6)
        SIM[("handover", corner)] = avg_sim("handover", corner, t_end=0.55, dt=4e-6)
    SIM[("black07", "min")] = avg_sim("start", "min", SYS["i_port2"], t_end=1.9, dt=4e-6)
    SIM[("handover07", "min")] = avg_sim("handover", "min", SYS["i_port2"], t_end=0.55, dt=4e-6)


system_runs()
BUS = SIM[("black", "min")]

# ============================================================================================ insulation requirement
INSULATION = [
    "Barrier: AUX-HV primary (all HV nets, referenced to the OR negative PGND) <-> PELV secondary (GND, +24V_HVAUX). "
    "Only T1 (transformer) and U-opto (CNY65B) cross it; no Y capacitor is fitted.",
    "Working voltage across the barrier: 1000 V DC continuous, 1100 V transient (port voltage, any port pole may be "
    "earthed); repetitive peak at the primary drain end up to the V_DS peak above PGND. Insulation class: REINFORCED, "
    "pollution degree 2 inside the enclosure, overvoltage category II (DC), altitude per PV-C6 (> 3000 m derating).",
    "Creepage (IEC 62477-1 / IEC 60664-1, reinforced = 2 x basic) - indicative values to be confirmed by the insulation "
    "coordination of ECO-10, the standards are not in this repository: PD2 at 1000 V: material group I about 2 x 5.0 mm, "
    "group II about 2 x 7.1 mm, group IIIa/b about 2 x 10 mm.",
    "Clearance: reinforced insulation is dimensioned for the rated impulse voltage one step above the basic value for "
    "a 1000 V DC OVC II circuit (indicative 8 kV -> about 8 mm in inhomogeneous field at <= 2000 m), multiplied by the "
    "altitude factor (about 1.14 at 3000 m, 1.29 at 4000 m) - again to be fixed by ECO-10.",
    "Optocoupler CNY65B (datasheet p4): clearance >= 14 mm, creepage >= 14 mm, insulation thickness >= 3 mm, CTI 200 "
    "(group IIIa), VIORM 1800 Vpk, VIOTM 12 kV. If PD2/group IIIa reinforced creepage (indicative 20 mm) applies, the "
    "package surface alone is short: coat the opto area to PD1 per IEC 60664-3 or confirm with the certifier.",
    "Transformer T1: reinforced insulation by triple-insulated wire on the secondary (certified for reinforced "
    "insulation at >= 1000 V DC working voltage), TIW insulation continuous to the pin, primary and secondary on "
    "opposite pin rows of coil former B66359B1013T001 (rows 25.4 mm apart), dielectric type/routine test per IEC "
    "62477-1 for the working voltage above.",
    "PCB: keep HV primary copper and PELV copper apart by the reinforced creepage/clearance above, measured along the "
    "surface and through air; a slot under T1 and U-opto is allowed for clearance but does not replace the component's "
    "own creepage. Functional HV spacing inside the primary: the port-to-port OR (two ports may differ by up to "
    "1100 V) and the resistor chain ahead of each fuse (up to 4 kV surge across a 2-resistor chain).",
]


# ============================================================================================ ngspice cross-check
def diode_fit():
    v0, rd = dout_model(TJ)
    vt27 = 0.02585
    n_d = 1.6 * 0.0343 / vt27                     # fitted: N*Vt(125 C) = 54.9 mV
    v1, v8 = v0 + rd * 1.0, v0 + rd * 8.0
    rs = (v8 - v1 - n_d * vt27 * math.log(8.0)) / 7.0
    isat = 1.0 / math.exp((v1 - rs) / (n_d * vt27))
    return isat, n_d, rs


def spice_deck(vin, wave, mode="normal"):
    """mode: 'normal' (regulated full load), 'short' / 'short_nofold' (output shorted at 0.3 ms, foldback active /
    disabled), 'surge' (4 kV / 20 us residual at the port at 0.3 ms, OV lockout active)."""
    op = OPS[vin]
    isat, n_d, rs = diode_fit()
    k = 0.9995
    llk_x = LLK - LP * (1 - k * k)
    t_sc = 0.3e-3
    t_end = {"normal": 6.0e-3, "short": 3.0e-3, "short_nofold": 1.5e-3, "surge": 1.2e-3}[mode]
    t_meas = {"normal": t_end - 1e-3, "short": t_end - 0.8e-3, "short_nofold": t_end - 0.3e-3, "surge": t_sc}[mode]
    vcomp0 = UCC["comp_off"] + UCC["acs"][1] * RCS * op["ipk"]
    vbr_typ = sum(TVS["vbr"]) / 2
    rd_tvs = (TVS["vc"] - TVS["vbr"][1]) / TVS["ipp"]
    ctr = OPT["ctr"][1]
    fopto = 3 * OPTO["fc_100r"] * 100.0 / R_PU
    ic_led = (UCC["ea_src"][1] + (5.0 - vcomp0) / R_PU) / ctr
    vk0 = VOUT_SET - (ic_led + 1.1 / R_BIAS) * OPT["r_led"] - 1.1
    if mode == "surge":
        vsrc = "Vin hvin 0 pwl(0 {vin} %g {vin} %g %g %g %g %g {vin})" % (
            t_sc, t_sc + 1e-6, SPD["up"], t_sc + 20e-6, SPD["up"], t_sc + 21e-6)
    else:
        vsrc = "Vin hvin 0 {vin}"
    lines = [
        "* AUX-HV flyback, Vin = %g V, mode %s - written by sim/aux_hv_design.py (do not edit)" % (vin, mode),
        "* Behavioural UCC28C59: RT/CT oscillator (discharge latch, 8.4 mA), T flip-flop (every 2nd cycle, D < 48 %%),",
        "* PWM D-latch reset by the CS comparator (1.15 V offset / gain 3 / 1.0 V clamp, tD 70 ns); C3M0900170J as",
        "* switch + Co(tr); transformer from the calculation; TVS clamp; ATL431 + CNY65B loop; foldback detector;",
        "* compensated tap string + TLV3202 OV lockout. Linear core: saturation is NOT modelled.",
        ".param vin=%g" % vin,
        vsrc,
        "Rinr hvin bulk %g" % R_IN,
        "Cbulk bulk 0 %g" % (N_BULK * C_BULK_EACH),
        "Chf bulk 0 %g" % (C_HF[0] * C_HF[1]),
        "Llk bulk pm %g" % llk_x,
        "Vip pm pm2 0",
        "Lp pm2 drain %g" % LP,
        "Ls 0 sec %g" % LS,
        "La 0 aux %g" % LA,
        "K1 Lp Ls La %g" % k,
        "Cw drain 0 %g" % C_W,
        "Coss drain src %g" % MOS["co_tr"],
        "S1 drain src gate 0 swmod",
        ".model swmod sw(vt=7.5 vh=0.5 ron=%g roff=1e9)" % rds(TJ),
        "Rcs src 0 %g" % RCS,
        "Dcl drain clmp dclamp",
        ".model dclamp d(is=1e-12 n=1.5 rs=0.2 cjo=10p bv=2000)",
        "Dtvs bulk clmp dtvs",
        ".model dtvs d(is=1e-12 n=1.5 bv=%g ibv=1m rs=%g cjo=100p)" % (TVS["n"] * vbr_typ, TVS["n"] * rd_tvs),
        "Dout sec out dout",
        ".model dout d(is=%g n=%g rs=%g cjo=110p vj=1 m=0.35 tt=0)" % (isat, n_d, rs),
        "Rsn sec snm %g" % R_SN,
        "Csn snm out %g" % C_SN,
        "Resr out pol %g" % (CPOL["esr"] / N_COUT),
        "Cpol pol 0 %g" % (N_COUT * CPOL["c"]),
        "Cml out 0 %g" % (C_MLCC_OUT[0] * C_MLCC_OUT[1] * 0.5),
        "Rload out 0 %g" % (VOUT_SET ** 2 / POUT),
        "Daux aux vddr daux",
        ".model daux d(is=1e-9 n=1.8 rs=0.3 cjo=20p)",
        "Raux vddr vdd %g" % R_AUX,
        "Cvdd vdd 0 %g" % (N_CVDD * C_VDD_EACH * 0.7),
        "Rvdd vdd 0 %g" % (15.0 / AUX_LOAD_I),
        "* --- oscillator: RT from VREF, CT (+ C_TX through Q_FB), discharge latch hi 2.5 V / lo 0.7 V, 8.4 mA sink",
        "Vref5 vref 0 5",
        "Rt vref rtct %g" % R_T,
        "Ct rtct 0 %g" % C_T,
        "Sfb rtct ctx fbk 0 swfb",
        ".model swfb sw(vt=1.6 vh=0.1 ron=3 roff=1e10)",
        "Cqfb rtct ctx %g" % N7002["coss"],
        "Ctx ctx 0 %g" % C_TX,
        "Bhi ohi 0 v = v(rtct) > %g ? 1 : 0" % UCC["osc_hi"],
        "Blo olo 0 v = v(rtct) < %g ? 1 : 0" % (UCC["osc_lo"] + 0.02),
        "Bdis rtct 0 i = v(dis) * %g * min(max((v(rtct) - %g) / 0.05, 0), 1)" % (UCC["idis"][1], UCC["osc_lo"]),
        "Ven en 0 1",
        "abr1 [ohi olo en] [dhi dlo den] adcb",
        ".model adcb adc_bridge(in_low=0.4 in_high=0.6)",
        "alat2 dhi dlo den NULL NULL ddis ddisn srl",
        ".model srl d_srlatch(sr_delay=1n enable_delay=1n set_delay=1n reset_delay=1n)",
        "abd2 [ddis] [dis] dacb1",
        ".model dacb1 dac_bridge(out_low=0 out_high=1 t_rise=1n t_fall=1n)",
        "atff den ddis NULL NULL dtq dtqn tff",
        ".model tff d_tff(clk_delay=1n)",
        "adly ddisn dclk dly",
        ".model dly d_buffer(rise_delay=10n fall_delay=10n)",
        "* --- PWM latch: set at the end of an enabled discharge, reset by CS comparator (tD) or the next discharge",
        "Rcsf src cs %g" % RCSF,
        "Ccsf cs 0 %g" % CCSF,
        "Bcmp rstc 0 v = (v(cs) > min(max((v(comp)-%g)/%g, 0), %g)) ? 1 : 0" % (UCC["comp_off"], UCC["acs"][1], UCC["vcs"][1]),
        "abr2 [rstc] [drc0] adcb",
        "atd drc0 drc tdl",
        ".model tdl d_buffer(rise_delay=%g fall_delay=1n)" % UCC["td"][1],
        "aor [drc ddis] drst or2",
        ".model or2 d_or(rise_delay=1n fall_delay=1n)",
        "adff dtq dclk NULL drst dq dqn dff",
        ".model dff d_dff(clk_delay=1n reset_delay=1n)",
        "abd [dq] [q] dacb",
        ".model dacb dac_bridge(out_low=0 out_high=15 t_rise=20n t_fall=20n)",
        "Rgx q gate %g" % R_G,
        "Cgx gate 0 200p",
        "* --- COMP node: EA source current (FB below 2.5 V), pull-up to VREF, pole capacitor, opto collector",
        "Iea vref comp %g" % UCC["ea_src"][1],
        "Dea comp vref dclp",
        ".model dclp d(is=1e-12 n=1)",
        "Rpu vref comp %g" % R_PU,
        "Cp comp 0 %g" % C_P,
        "Bopt comp 0 i = v(ctrf) * tanh(v(comp)/0.1)",
        "Bctr 0 ctrf i = %g * i(vled)" % ctr,
        "Rctr ctrf 0 1",
        "Cctr ctrf 0 %g" % (1 / (2 * math.pi * fopto)),
        "* --- secondary: ATL431 (ideal shunt, 1 S), fast lane R_LED, LED + R_bias, divider, integrator, R-C feed-forward",
        "Rled out la %g" % OPT["r_led"],
        "Rbias la k %g" % R_BIAS,
        "Dled la lk dled",
        ".model dled d(is=1e-14 n=1.6)",
        "Vled lk k 0",
        "Rup out fbs %g" % R_UP,
        "Rlo fbs 0 %g" % R_LO_SEL,
        "Cz k fbs %g" % C_Z,
        "Rff out ffm %g" % R_FF,
        "Cff ffm fbs %g" % C_FF,
        "Batl k 0 i = max(0, 1.0*(v(fbs)-%g)) * (v(k) > 1 ? 1 : v(k))" % ATL["vref"][1],
        "* --- foldback: aux -> US1G -> 2.2k -> AF1 (1n, 10k) -> 6V8 Zener -> Q_INV gate (330k) -> FBK (100k to VREF)",
        "Daf aux afr daux",
        "Raf1 afr af1 %g" % R_AF1,
        "Caf af1 0 %g" % C_AF,
        "Raf2 af1 0 %g" % R_AF2,
        "Dzf qg af1 dzf",
        ".model dzf d(is=1e-12 n=1.5 bv=%g ibv=5u rs=100)" % (sum(Z68["vz_ua"]) / 2),
        "Rqg qg 0 %g" % R_QG,
        "Cqg qg 0 %g" % N7002["ciss"][0],
    ]
    if mode == "short_nofold":
        lines += ["Rfbk fbk 0 1k"]
    else:
        lines += ["Sinv fbk 0 qg 0 swinv",
                  ".model swinv sw(vt=1.6 vh=0.05 ron=5 roff=1e9)",
                  "Rfbk vref fbk %g" % R_FBK,
                  "Cfbk fbk 0 %g" % N7002["ciss"][0]]
    if mode.startswith("short"):
        lines += ["Vsc scc 0 pwl(0 0 %g 0 %g 1)" % (t_sc, t_sc + 50e-9),
                  "Ssc out scm scc 0 swsc",
                  ".model swsc sw(vt=0.5 vh=0.1 ron=0.02 roff=1e9)",
                  "Rscw scm 0 0.03"]
    if mode == "surge":
        lines += ["* --- compensated tap string + TLV3202 channel 1 (OV lockout pulls COMP low through a BAT54)",
                  "Rtop bulk lso %g" % (3 * R_STR),
                  "Ctop bulk lso %g" % (C_STR / 3),
                  "Rd1 lso ls %g" % R_D1,
                  "Cd1 lso ls %g" % C_D1,
                  "Rd2 ls 0 %g" % R_D2,
                  "Cd2 ls 0 %g" % C_D2,
                  "Rlsf lso lsof %g" % R_LSF,
                  "Clsf lsof 0 %g" % C_LSF,
                  "Bov ovt 0 v = v(lsof) > %g ? 1 : 0" % ov_ref(True, 5.0, 2.5, 0.0),
                  "abr3 [ovt] [dov0] adcb",
                  "atov dov0 dov tpd",
                  ".model tpd d_buffer(rise_delay=%g fall_delay=%g)" % (TLV["tpd"], TLV["tpd"]),
                  "abd3 [dov] [ovq] dacb1",
                  "Sov comp ovd ovq 0 swov",
                  ".model swov sw(vt=0.5 vh=0.1 ron=25 roff=1e10)",
                  "Vovd ovd 0 0.45"]
    ic_parts = "v(out)=%g v(pol)=%g v(comp)=%g v(k)=%g v(fbs)=%g v(vdd)=%g v(bulk)=%g v(rtct)=1 v(ctx)=1 v(af1)=17 " \
               "v(qg)=10 v(ffm)=%g" % (VOUT_SET, VOUT_SET, vcomp0, vk0, ATL["vref"][1], V_AUX_NOM, vin, VOUT_SET)
    if mode != "short_nofold":
        ic_parts += " v(fbk)=0"
    if mode == "surge":
        ic_parts += " v(lso)=%g v(ls)=%g v(lsof)=%g" % tuple(list(taps(vin, R_D1, R_D2, R_HYS, True)) +
                                                             [taps(vin, R_D1, R_D2, R_HYS, True)[0]])
    lines += [
        ".ic " + ic_parts,
        ".options method=gear reltol=1e-3 abstol=1e-9 vntol=1e-5 itl4=100",
        ".tran 20n %g 0 20n" % t_end,
        ".control",
        "run",
        "let pin = -v(hvin)*i(vin)",
        "let pout = v(out)*v(out)/%g" % (VOUT_SET ** 2 / POUT),
        "meas tran vout_avg avg v(out) from=%g to=%g" % (t_meas, t_end),
        "meas tran vout_max max v(out) from=%g to=%g" % (t_meas, t_end),
        "meas tran vout_min min v(out) from=%g to=%g" % (t_meas, t_end),
        "meas tran vds_max max v(drain) from=%g to=%g" % (t_meas, t_end),
        "meas tran ipk_max max i(vip) from=%g to=%g" % (t_meas, t_end),
        "meas tran pin_avg avg pin from=%g to=%g" % (t_meas, t_end),
        "meas tran pout_avg avg pout from=%g to=%g" % (t_meas, t_end),
        "meas tran fbk_max max v(fbk) from=%g to=%g" % (t_meas, t_end),
        "meas tran comp_avg avg v(comp) from=%g to=%g" % (t_meas, t_end),
        "meas tran cs_max max v(cs) from=%g to=%g" % (t_meas, t_end),
        "meas tran tper trig v(gate) val=7.5 rise=2 targ v(gate) val=7.5 rise=3 from=%g" % t_meas,
    ]
    if mode.startswith("short"):
        lines += ["meas tran ipk_all max i(vip) from=%g to=%g" % (t_sc, t_end),
                  "meas tran vds_all max v(drain) from=%g to=%g" % (t_sc, t_end),
                  "meas tran ipk_early max i(vip) from=%g to=%g" % (t_sc, t_sc + 0.3e-3)]
    if mode == "surge":
        lines += ["meas tran bulk_max max v(bulk) from=%g to=%g" % (t_sc, t_end),
                  "meas tran vds_all max v(drain) from=%g to=%g" % (t_sc, t_end),
                  "meas tran t_lock when v(ovq)=0.5 rise=1",
                  "meas tran out_min min v(out) from=%g to=%g" % (t_sc, t_end),
                  "meas tran ipk_all max i(vip) from=%g to=%g" % (t_sc, t_end)]
    sig = "v(drain) i(vip) v(out) v(fbk) v(bulk) v(comp)"
    lines += ["wrdata %s %s" % (wave, sig), "quit", ".endc", ".end"]
    return "\n".join(lines) + "\n"


def run_spice(vin, mode="normal"):
    tag = "%dV" % int(vin) if mode == "normal" else "%dV_%s" % (int(vin), mode)
    path = os.path.join(SPICE_DIR, "aux_hv_%s.cir" % tag)
    wave = os.path.join(tempfile.gettempdir(), "aux_hv_wave_%s.txt" % tag)
    with open(path, "w") as fh:
        fh.write(spice_deck(vin, wave, mode))
    r = subprocess.run(["ngspice", "-b", path], capture_output=True, text=True, timeout=3600, cwd=REPO)
    if os.path.exists(wave):
        d = np.loadtxt(wave)
        if mode == "normal":                      # keep only the last three switching periods (raw dump is ~5 MB)
            d = d[d[:, 0] >= d[-1, 0] - 3 / FS_NOM]
        else:                                     # decimate to 0.2 us
            keep = np.concatenate([[True], np.diff(np.floor(d[:, 0] / 0.2e-6)) > 0])
            d = d[keep]
        np.savetxt(os.path.join(OUT, "spice_wave_%s.csv" % tag), d[:, [0, 1, 3, 5, 7, 9, 11]], delimiter=",",
                   fmt="%.6g", header="t_s,v_drain_V,i_primary_A,v_out_V,v_fbk_V,v_bulk_V,v_comp_V", comments="")
        os.remove(wave)
    meas = {}
    for line in (r.stdout + r.stderr).splitlines():
        m = re.match(r"^\s*(\w+)\s*=\s*([-+0-9.eE]+)", line)
        if m:
            try:
                meas[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    need = ("vout_avg", "vds_max", "ipk_max")
    if not all(k in meas for k in need):
        raise RuntimeError("ngspice failed on %s:\n%s\n%s" % (path, r.stdout[-3000:], r.stderr[-3000:]))
    return meas


# ============================================================================================ outputs
def spec_json(sc):
    v = {}

    def put(key, value, why, **kw):
        v[key] = dict(value=value, why=why, **kw)
    s = INP
    put("F_PORT", "1A", "per-port fuse 1000 VDC gPV behind %s: fault current <= %.0f A (breaking 20 kA not needed), "
        "hot-plug I2t %.3f A2s = %.0f %% of melting" % (ohm(R_IN), s["i_fault"], s["i2t"], s["i2t"] / FUSE["i2t"] * 100))
    put("R_LIM", ohm(R_LIM_EACH), "%d per port AHEAD of the fuse (AC10 pulse-rated): fuse-clearing %.0f J each vs ~%.0f J; "
        "surge %.0f V each vs 4.5 kV" % (N_LIM, s["e_fault"], s["e_cap"], max(x["v_r"] for x in s["surges"])), n=N_LIM)
    put("C_BULK", "3.3u 1300V", "%d x film = RC surge absorber with %s: 4 kV / 20 us residual -> %.0f V (UR 1300 V); "
        "filter corner %.0f Hz" % (N_BULK, ohm(R_IN), SURGE_MAX, s["corner_hz"]), n=N_BULK)
    put("C_HF", "22n", "HV MLCC at the switching loop", volt="2kV", diel="X7R", pkg="1812", n=C_HF[0])
    put("R_STR", ohm(R_STR), "3 sections: start-up gate taps + line sense + OV tap; %.0f V each at 1100 V (CRHV2512 3 kV)" % (VIN_TR / 3), n=3)
    put("C_STR", farad(C_STR), "string compensation (tau %.1f ms per section)" % (R_STR * C_STR * 1e3), volt="1kV", diel="C0G",
        pkg="1206", tol="5%", n=3)
    put("R_D1", ohm(R_D1), "string bottom LSO-LS: OV lockout trip %.0f V (%.0f-%.0f), release %.0f V" % (
        STR["ov"][1], STR["ov"][0], STR["ov"][2], STR["rel"][1]), tol="0.1%")
    put("C_D1", farad(C_D1), "compensation (%.0f %% of the string time constant)" % (C_D1 * R_D1 / (R_STR * C_STR) * 100),
        volt="50V", diel="C0G", pkg="1206", tol="5%")
    put("R_D2", ohm(R_D2), "string bottom LS-PGND: brown-in %.0f V (%.0f-%.0f), brown-out %.0f V (%.0f-%.0f)" % (
        LINE["on"][1], LINE["on"][0], LINE["on"][2], LINE["off"][1], LINE["off"][0], LINE["off"][2]), tol="0.1%")
    put("C_D2", farad(C_D2), "compensation (%.0f %%)" % (C_D2 * R_D2 / (R_STR * C_STR) * 100), volt="50V", diel="C0G",
        pkg="0805", tol="5%")
    put("R_HYS", ohm(R_HYS), "brown-in hysteresis from LINE_OK")
    put("R_LINE_PU", ohm(R_PULL_LINE), "LINE_OK pull-up to VREF")
    put("R_LSF", ohm(R_LSF), "OV comparator input filter")
    put("C_LSF", farad(C_LSF), "OV comparator input filter (0.22 us)", volt="50V", diel="C0G")
    put("R_REF", ohm(R_REF), "primary ATL431 2.5 V reference bias from VREF (%.2f mA)" % (I_REF * 1e3))
    put("R_OVH1", ohm(R_OVH1), "OV reference series resistor (hysteresis)")
    put("R_OVH2", ohm(R_OVH2), "OV hysteresis from 1OUT: release %.0f V" % STR["rel"][1])
    put("R_SU_S", ohm(RS_START), "start-up current %.2f-%.2f mA" % (SU["i_min"] * 1e3, SU["i_max"] * 1e3))
    put("R_SU_G", "1M", "start-up gate bias to VDD")
    put("R_SU_EN", "10k", "VREF -> start-up disable FET gate")
    put("R_SU_EN_PD", "100k", "start-up disable FET gate pull-down")
    put("R_T", ohm(R_T), "oscillator %.1f kHz -> fsw %.1f kHz (%.1f-%.1f)" % (2 * FS_NOM / 1e3, FS_NOM / 1e3, FS_RANGE[0] / 1e3, FS_RANGE[1] / 1e3))
    put("C_T", farad(C_T), "oscillator capacitor", volt="50V", diel="C0G", tol="5%")
    put("C_TX", farad(C_TX), "foldback: switched onto RT/CT below %.1f-%.1f V output -> fsw %.1f kHz (%.1f-%.1f)" % (
        FB["v_fold"][0], FB["v_fold"][1], F_FOLD / 1e3, F_FOLD_RANGE[0] / 1e3, F_FOLD_RANGE[1] / 1e3), volt="50V",
        diel="C0G", pkg="0805", tol="5%")
    put("R_FBK", ohm(R_FBK), "foldback FET gate pull-up to VREF")
    put("R_AF1", ohm(R_AF1), "aux-plateau detector charge (tau %.1f us, rejects the clamp spike)" % (
        R_AF1 * R_AF2 / (R_AF1 + R_AF2) * C_AF * 1e6))
    put("C_AF", farad(C_AF), "aux-plateau detector", volt="50V", diel="C0G")
    put("R_AF2", ohm(R_AF2), "aux-plateau detector discharge (tau %.0f us)" % (R_AF2 * C_AF * 1e6))
    put("R_QG", ohm(R_QG), "Q_INV gate hold / pull-down")
    put("C_VREF", "1u", "VREF bypass (SLUSEV2C 9.2.2.10)", volt="25V")
    put("C_VDD", farad(C_VDD_EACH), "%.0f uF effective: VDD hold-up %.1f ms" % (SU["c_vdd"] * 1e6, SU["t_hold"] * 1e3),
        volt="50V", pkg="1210", n=N_CVDD)
    put("C_AUX", farad(C_AUX_EACH), "aux reservoir ahead of the VDD follower", volt="50V", pkg="1210", n=N_CAUX)
    put("R_ZREG", ohm(R_ZREG), "follower Zener bias: VDD = gate drive %.2f-%.2f V (BZX84-A18, -40..85 C)" % (
        VDD_REG[0], VDD_REG[2]), pkg="1206")
    put("R_VDDE", ohm(R_VDDE), "AX-05: BAT54 leakage sink, V_EB %.1f V at 85 C / %.1f V at 125 C (< 5 V)" % SU["v_e"])
    put("R_AUX", ohm(R_AUX), "aux rectifier series resistor: limits peak charging by the leakage spike")
    put("R_AUXS", ohm(R_AUXS), "aux sense filter (backup loop)")
    put("C_AUXS", farad(C_AUXS), "aux sense filter", volt="50V")
    put("R_FBT", ohm(R_FBT), "backup loop: Vout %.2f-%.2f V if the opto loop fails" % BACKUP_BAND, tol="0.1%")
    put("R_FBB", ohm(R_FBB), "FB divider bottom", tol="0.1%")
    put("R_EA", ohm(R_C_EA), "backup loop type II zero resistor")
    put("C_EA", farad(C_C_EA), "backup loop integrator", volt="25V")
    put("C_EAHF", farad(C_HF_EA), "backup loop HF pole", volt="50V", diel="C0G")
    put("R_PU", ohm(R_PU), "COMP pull-up = opto load resistor")
    put("C_P", farad(C_P), "COMP pole %.1f kHz" % (1 / (2 * math.pi * R_PU * C_P) / 1e3), volt="50V", diel="C0G")
    put("R_SS", ohm(R_SS), "soft start charge")
    put("C_SS", farad(C_SS), "soft start ~%.1f ms" % (C_SS * 3.3 / 1.5e-3 * 1e3), volt="16V")
    put("R_TA", ohm(R_TA), "overload timer from COMP: trips when COMP > %.2f-%.2f V (%.0f-%.0f W out)" % (
        OVLD["comp_trip"][0], OVLD["comp_trip"][2], OVLD["trip"]["min"]["pout"], OVLD["trip"]["max"]["pout"]))
    put("R_TB", ohm(R_TB), "overload timer divider bottom")
    put("C_TMR", farad(C_TMR), "overload timer: %.0f-%.0f ms at full COMP" % (OVLD["t_trip"][0] * 1e3, OVLD["t_trip"][2] * 1e3),
        volt="25V", pkg="1206", tol="10%")
    put("R_SSG", ohm(R_SSG), "soft-start crowbar gate drive from 2OUT")
    put("R_SSG_PD", ohm(R_SSG_PD), "soft-start crowbar gate pull-down")
    put("R_G", ohm(R_G), "gate resistor")
    put("R_GS", "10k", "gate-source pull-down")
    put("R_CS", ohm(RCS_EACH), "2 in parallel = %.3f R: limit %.2f-%.2f A (+%.2f A delay)" % (RCS, ILIM["min"], ILIM["max"], ILIM["over"]),
        pkg="1206", note="0.25 W", n=2)
    put("R_CSF", ohm(RCSF), "CS RC filter (no internal blanking)")
    put("C_CSF", farad(CCSF), "CS RC filter", volt="50V", diel="C0G")
    put("R_SN", ohm(R_SN), "output rectifier snubber: %.2f W at 1100 V" % (C_SN * (VOUT + VIN_TR / N) ** 2 * FS_NOM), pkg="2512", note="1 W")
    put("C_SN", farad(C_SN), "output rectifier snubber", volt="500V", diel="C0G", pkg="1206")
    put("C_OUT_MLCC", "10u", "output HF", volt="50V", pkg="1210", n=C_MLCC_OUT[0])
    put("C_OUT_POL", "100u 35V", "polymer bulk: %.0f mV pk-pk ripple at 600 V (calc), %.2f A rms each" % (
        RIP[600.0]["v_pp"] * 1e3, RIP[600.0]["icap_each"]), n=N_COUT)
    put("R_PRELOAD", ohm(R_PRELOAD), "output preload / bleed", pkg="1206")
    put("R_LED", ohm(OPT["r_led"]), "fast-lane: LED current up to %.1f mA needed (R <= %.2fk), %.0f mW at that current" % (
        OPT["if_max"] * 1e3, OPT["r_led_max"] / 1e3, (OPT["if_max"] + 1.1 / R_BIAS) ** 2 * OPT["r_led"] * 1e3), pkg="0805")
    put("R_BIAS", ohm(R_BIAS), "ATL431 minimum current across the LED")
    put("R_UP", ohm(R_UP), "Vout set point %.3f V nominal, %.2f-%.2f V" % (VOUT_SET, VOUT_LO, VOUT_HI), tol="0.1%")
    put("R_LO", ohm(R_LO_SEL), "ATL431 divider bottom", tol="0.1%")
    put("C_Z", farad(C_Z), "integrator zero %.0f Hz" % (1 / (2 * math.pi * R_UP * C_Z)), volt="50V")
    put("R_FF", ohm(R_FF), "feed-forward to REF: no start-up / hand-over overshoot (max %.2f V vs SYS OV 23.97 V)" % sc["vmax"])
    put("C_FF", farad(C_FF), "feed-forward to REF", volt="50V")
    put("R_PG_A", ohm(PG["ra"]), "PG window: UV falling %.2f-%.2f V, OV rising %.2f-%.2f V" % (*PG["uv_fall"], *PG["ov_rise"]), tol="0.1%")
    put("R_PG_B", ohm(PG["rb"]), "PG window middle", tol="0.1%")
    put("R_PG_C", ohm(PG["rc"]), "PG window bottom", tol="0.1%")
    put("C_PGF", "1n", "PG divider filter: %.1f ms with the %s string" % (PG["ra"] * 1e-9 * 1e3, ohm(PG["ra"])), volt="50V")
    put("R_PGR", ohm(R_PGR), "PG 3.3 V Zener rail from the output (%.0f mW)" % (PGD["p_r"] * 1e3), pkg="1206")
    put("C_PGR", "100n", "PG rail decoupling", volt="16V")
    put("R_PGW", ohm(R_PGW), "TPS3700 open-drain pull-up to the PG rail")
    put("R_PGO", ohm(R_PGO), "PG_HVAUX series resistor (high %.2f-%.2f V into the SYS 100k pull-down)" % PGD["v_high"])
    put("C_DEC", "100n", "local decoupling (VDD pin, TPS3710/TLV3202 supply)", volt="50V")
    tr = transformer_spec()
    n_parts = dict(per_port=dict(R_LIM=N_LIM, F_PORT=1, OR_DIODES=2, STUDS=2))
    return dict(start_up_voltage_V=round(LINE["on"][2], 1),          # first 'start' key: worst-case (highest) turn-on
                start_up_voltage_nominal_V=round(LINE["on"][1], 1),
                stop_voltage_V=[round(x, 1) for x in LINE["off"]],
                project="AUX-HV", rev="C1", generated_by="sim/aux_hv_design.py", note="CALCULATED, not bench-validated",
                switch=dict(design=SW_DESIGN, main=SW_MAIN, alternate=SW_ALT,
                            compare={k: dict(loss_1000V_W=SW_PWR[k], tj_1000V_C=T_AMB + SW_PWR[k] * (35.0 + DEVICES[k]["rth_jc"]),
                                             eta={str(int(v)): SWCMP[k][v]["eta"] for v in VIN_TABLE},
                                             qualification=DEVICES[k]["qual"]) for k in DEVICES},
                            vgs_V=[VDD_REG[0], VDD_REG[2]]),
                rating=dict(continuous_W=POUT, short_W=math.floor(ILIM["p_min"]), short_ms=OVLD["t_trip"][0] * 1e3,
                            post_inhibit_W=SYS["p_total"], port_hold_W=SYS["p_hold2"], port_hold_s=SYS["t_port"],
                            # black07 (0.7 A port load during black start) cannot happen - contactors open; kept apart
                            black_start_peak_W=max(SIM[k]["p_peak"] for k in SIM if k[0] == "black"),
                            handover_peak_W=max(SIM[k]["p_peak"] for k in SIM if k[0].startswith("handover")),
                            timer_node_peak_V=max(SIM[k]["compf_max"] for k in SIM if k[0] != "black07"),
                            timer_trip_min_V=OVLD["v_trip"][0],
                            stress_black_start_0A7=dict(p_peak_W=SIM[("black07", "min")]["p_peak"],
                                                        trip=bool(SIM[("black07", "min")]["trip"]),
                                                        note="not possible in service: port contactors open")),
                standby={str(int(v)): dict(no_load_W=STBY[v]["noload"]["p_in"], standby_in_W=STBY[v]["standby"]["p_in"],
                                           standby_out_W=SYS["p_noport"]) for v in VIN_TABLE},
                builds=dict(PV="all parts fitted (both ports)",
                            DAB="port-B input parts not fitted: 2 studs, %d x R_LIM, fuse, 2 OR diodes (port 1 only, "
                                "D-021)" % N_LIM, counts=n_parts),
                values=v, transformer=tr,
                summary=dict(fsw=FS_NOM, f_fold=F_FOLD, lp=LP, llk_est=LLK, np=NP, ns=NS, na=NA, vr=OPS[600.0]["vr"],
                             vds_cont=VDS["cont"], vds_trans=VDS["trans"], eta={str(int(k)): OPS[k]["eta"] for k in VIN_TABLE},
                             vout=(VOUT_LO, VOUT_SET, VOUT_HI), backup_band=BACKUP_BAND,
                             ov_lockout=STR["ov"], surge_bulk_max=SURGE_MAX,
                             timer_ms=[x * 1e3 for x in OVLD["t_trip"]], trip_w=[OVLD["trip"][c]["pout"] for c in ("min", "typ", "max")],
                             sys_bus_takeover_ms=(SIM[("alone", "min")]["t_take"] or 0) * 1e3,
                             aux_takeover_V=SIM[("alone", "min")]["v_take"], vdd_hold_ms=SU["t_hold"] * 1e3,
                             overshoot_max=sc["vmax"], handover_bus_min=sc["bus_min"],
                             step_vmin_with_bus=min(s["vmin"] for s in STEP if s["bus"]),
                             step_vmin_alone=min(s["vmin"] for s in STEP if not s["bus"])))


# Insulation coordination for T1 (D-032, sim/out/insulation/report.md, finding IC-15): reinforced HV-PELV at a
# 1000 V DC working voltage with the switch-node recurring peak on the primary
T1_INS = dict(u_rp=None, pd_v=2461.0, pd_ext=1968.0, pd_q=10.0, ac_v=4400.0, ac_t=60.0, imp_v=8000.0,
              clr=(8.0, 9.2, 10.4), cpg_pd2_iiia=20.0, cpg_pd1=6.4)


def transformer_spec():
    """Rev C1: T1 is the magnetics design rev M1 (T1_DESIGN); the description is taken from it, the electrical
    requirements stay this converter's."""
    T1_INS["u_rp"] = (VDS["cont"], VDS["trans"])
    lv, ins = T1_DESIGN["insulation"]["levels"][0], T1_DESIGN["insulation"]
    assert (lv["pd_test"], lv["pd_ext"], lv["ac"], lv["imp"], lv["cr_pd1"], lv["cr_pd2"], tuple(lv["clearance"])) == (
        T1_INS["pd_v"], T1_INS["pd_ext"], T1_INS["ac_v"], T1_INS["imp_v"], T1_INS["cpg_pd1"], T1_INS["cpg_pd2_iiia"],
        T1_INS["clr"]), "T1 design insulation levels vs IC-15"
    desc = ("CUSTOM flyback transformer AUX-HV T1 rev %s (%s; build to the winding sheet "
            "sim/out/magnetics/spec_aux_hv_transformer.md). %s. Core %s %s, %s, gap %.2f mm (A_L %.0f nH). Lp %.3f mH "
            "%s; leakage (S shorted) estimate %.1f uH, <= %.1f uH. Windings in order: %s. Insulation: %s. Creepage: "
            "%s; clearance >= %.1f mm. ROUTINE test (every unit): %s. TYPE tests: %s. Working voltage 1000 V DC, "
            "recurring peak %.0f V at %.0f kHz (%.0f V at the 1100 V trip). Peak primary current %.2f A (limit max "
            "%.2f A), %.0f mT on A_min at the limit."
            % (T1_DESIGN["revision"], T1_DESIGN["status"], T1_DESIGN["construction"], T1_CORE["maker"],
               T1_CORE["part_number"], T1_CORE["note"].split(";")[0], GAP * 1e3, AL * 1e9, T1_EL["L_p_H"] * 1e3,
               T1_EL["L_p_tol"], T1_EL["L_leak_est_H"] * 1e6, T1_EL["L_leak_max_H"] * 1e6,
               "; ".join("%s: %s" % (x["name"], x["spec"]) for x in T1_DESIGN["windings"]), ins["system"],
               ins["creepage_pins_note"], ins["clearance_m"] * 1e3, ins["routine_tests"], ins["type_tests"],
               VDS["cont"], FS_NOM / 1e3, VDS["trans"], OPS[200.0]["ipk"], ILIM["max_eff"], B_LIM_AMIN * 1e3))
    req = dict(
        lp_mH=dict(value=LP * 1e3, tol_pct=AL_TOL * 100, at="10 kHz"),
        turns=dict(np=NP, ns=NS, na=NA, n=N, aux_ratio=NA / NS,
                   note="ratios are the requirement (n = Np/Ns = 6.0, Na/Ns = 16/15: VDD headroom and the backup "
                        "loop); absolute turns may change with the core"),
        frequency_kHz=[FS_RANGE[0] / 1e3, FS_NOM / 1e3, FS_RANGE[1] / 1e3], mode="DCM at every input and full load",
        vin_V=dict(min=VIN_MIN, max=VIN_MAX, transient=VIN_TR), pout_W=POUT,
        ipk_full_load_A=max(OPS[v]["ipk"] for v in VIN_TABLE), ipk_max_A=ILIM["max_eff"],
        b_max_on_Amin_mT=dict(limit=B_LIMIT_AMIN * 1e3, at_A=ILIM["max_eff"], lp="+7 %", design_m1=B_LIM_AMIN * 1e3,
                              rule="<= 0.85 x Bs(100 C); vendor measures L(I) to 1.86 A at 120 C (MG-12)"),
        leakage_max_uH=T1_EL["L_leak_max_H"] * 1e6,
        switched_capacitance_max_pF=C_W_MAX * 1e12,
        winding_loss_max_W=dict(limit=T1_CU_MAX, at="30 W out, full load, any input", budget=T1_BUDGET,
                                design_m1=max(T1_CU_V), note="design M1 at 30 W: %.2f W own / %.2f W OpenMagnetics "
                                "(the higher is used)" % (_T1_VER["own"]["P_cu_W"], _T1_VER["openmagnetics"]["P_cu_W"])),
        core_loss_W=max(OPS[v]["loss"]["transformer core"] for v in VIN_TABLE),
        insulation=dict(T1_INS),
        changed_by_asian_switch="switched winding capacitance <= %.0f pF (was 40 pF): the IV2Q171R0D7Z turn-on loss "
                                "(its Eoss + 0.5 C_W V^2) sets Tj %.0f C at 1000 V; nothing else changes" % (
                                    C_W_MAX * 1e12, T_AMB + SW_PWR[SW_DESIGN] * (35.0 + MOS["rth_jc"])))
    return dict(desc=desc, core="%s %s" % (T1_CORE["maker"], T1_CORE["part_number"]), rev=T1_DESIGN["revision"],
                cost_usd=T1_DESIGN["cost"]["total_usd"], mpn="AUX-HV-T1 rev %s" % T1_DESIGN["revision"],
                al_nh=AL * 1e9, gap_mm=GAP * 1e3,
                lp_mh=LP * 1e3, llk_est_uh=LLK * 1e6, llk_1d_uh=LLK_1D * 1e6, np=NP, ns=NS, na=NA,
                stack_height_mm=STACK_H * 1e3, b_fl_mT=B_FL * 1e3, b_lim_mT=B_LIM * 1e3, b_lim_amin_mT=B_LIM_AMIN * 1e3,
                c_w_pf=C_W * 1e12, insulation=dict(T1_INS), requirements=req,
                design_file=os.path.relpath(T1_DESIGN_FILE, REPO), design_file_present=os.path.exists(T1_DESIGN_FILE))


# IC-15 check of the winding build against the coordinated levels: rev C1 takes the design M1 construction
T1_CHECK = ("design M1 (%s): %s. Creepage %s; clearance >= %.1f mm. The rev C0 build (round TIW, impregnated) is "
            "superseded. Whether the build is PD-free at %.0f Vpk is decided by the routine test on every unit - no TIW "
            "datasheet gives a PD inception voltage for this construction."
            % (T1_DESIGN["status"], T1_DESIGN["insulation"]["system"], T1_DESIGN["insulation"]["creepage_pins_note"],
               T1_DESIGN["insulation"]["clearance_m"] * 1e3, T1_INS["pd_v"]))


# ============================================================================================ AUX75 (D-044)
# Second named case (ARCHITECTURE-COSTFIRST.md sec. 3, R-13): the cost-first module's ONLY supply, on the power board,
# fed from both ports' terminal taps (A_T+, B_T+) against BUS-. Reused from the 30 W case without change: the input
# block per tap, start-up cascode, compensated string with brown-in / OV lockout, TLV3202 OV lockout + overload timer,
# oscillator, gate drive, CS filter, soft start, foldback detector, VDD follower (now fed by the live 24 V). New:
# transformer AUX-T1 (primary, shield, live 24 V winding on BUS-, reinforced SELV 24 V winding; no aux winding), FB
# divider on the live 24 V into the UCC28C59 error amplifier (no opto, no shunt regulator), TPS3700 window on the live
# 24 V (status + output-OV stop), 6 x SMCJ33A clamp, hold-up bank. Transformer figures are estimates (A-41..A-47)
# until the magnetics design sim/out/magnetics/design_aux75_transformer.json exists.
# Rev A3 (one design for every module, PCS-P125 three- and four-wire included): live 30 W continuous / 48 W for 1 s (the
# four-wire inverter needs 26.2 W stacked + 15 % and a 42.2 W pull-in peak + 14 %; the pull-in window is 100-152 ms), SELV
# 64.2 W unchanged -> 94.2 W continuous, 112.2 W for 1 s. Same EC39A core, switch, clamp, timer and input block; turns 50:9:10
# (n 5.56, VR 137 V instead of 148 V: the clamp's share of each cycle falls, and one more live / SELV turn keeps Np x A_min);
# Lp kept at 0.485 mH (the rev A0-A2 value: DCM to 185 V at 82.5 W with Lp + 7 % and n = 6), the gap re-ground for 50 turns;
# current sense 3 x 0.866 R (E96). The Lp window is narrow (calculated with this file's model): Lp <= 0.489 mH keeps the
# flux at the highest limit >= 5 % below the rule, Lp >= 0.482 mH lets the lowest limit deliver the 112.2 W peak.
A75_DESIGN_FILE = os.path.join(HERE, "out", "magnetics", "design_aux75_transformer.json")
A75 = dict(vout=24.0, pout=30.0 + 62.0 + 2.2, p_live=30.0, p_selv=62.0 + 2.2, p_live_peak=48.0, np=50, nl=9, ns=10, lp=0.485e-3,
           vin_on=210.0, vin_off=185.0,       # brown-in / -out targets: start <= 220 V at the worst corner
           rth_sw=20.0,                       # A-44: switch tab on >= 12 cm2 2-oz Cu both sides + vias (board)
           c_w=40e-12,                        # A-43: switched winding capacitance assumed (= requirement)
           r_p=0.9, r_l=0.04, r_s=0.02,       # A-46: effective winding resistances (AC, fringing, 100 C)
           rth_t1=9.5,                        # A-47: potted ETD 39-class case, natural convection
           r_lim=20.0,                        # rev A1: 2 x 20 R per tap (AC10, E24) - the smallest value inside AX-01/02
           ae=125e-6, amin=123e-6, ve=11.5e-6)  # A-45: ETD 39-class core (the magnetics design chooses)
# Rev A2: the power board's real loads (PV-PWR design check): live = 5 V / 3.3 V converters (gate bias 3.0 W per phase,
# control board 1.5 W) 13.6 W (3 phases) / 17 W (4 phases) while switching, + 2 x 1.5-2.5 W economised coils; 2 x 6 W
# pull-in and the 2.3 W precharge relay for <= 1 s; SELV = fans 40 W (3 phases) / 62 W (4 phases) + SELV_LOGIC_W for the control
# board's SELV zone (isolator SELV sides, CAN / RS-485 transceivers, ready relay, Ethernet bridge option: PV-CTL 1.44 W stacked
# maximum with the bridge not fitted, 2.13 W with it fitted (PCS-CTL); 2.2 W allocated so one aux design covers every variant, D-075).
SELV_LOGIC_W = 2.2               # W on the SELV rail for the control board's SELV logic (was 0.6 W 'communication' before D-075)
SELV_LOGIC_STBY_W = 1.44         # W in standby, bridge not fitted (the SELV logic does not sleep; +0.7 W with the bridge fitted)
# One design for both: rated for 4 phases. The module switches only from 250 V up (PV-03 / PV-06).
A75_LOAD = {"PV-P75 (3 phases), full power, fans 100 %": (20.0, 40.0 + SELV_LOGIC_W),       # (live, SELV) W
            "PV-P100/110 (4 phases), full power, fans 100 %": (24.0, 62.0 + SELV_LOGIC_W),
            "rating": (A75["p_live"], A75["p_selv"])}
A75_COIL_HOLD = 1.5              # W per contactor in standby: the economiser's low end - module standby < 20 W needs it
A75_STBY = {"PV-P75": (13.6 - 3 * 3.0 + 2 * A75_COIL_HOLD, SELV_LOGIC_STBY_W + 0.1),     # gates off, coils held, fans off
            "PV-P100/110": (17.0 - 4 * 3.0 + 2 * A75_COIL_HOLD, SELV_LOGIC_STBY_W + 0.1)}
A75_FANS = {"3 phases": 40.0, "4 phases": 62.0}       # W at full speed (+ SELV_LOGIC_W on the SELV rail)
A75_LIVE_PTS = (2.0, 6.7, 13.6, 17.0, 20.0, 24.0, 30.0)    # W on the live 24 V (2 / 6.7: gates off; 13.6 / 17: gates on)
A75_RULE = {"3 phases": 13.6, "4 phases": 17.0}       # live load while the gates switch (the firmware's condition)
V_FAN_FULL = 24.5                # A-52: fans at 24 V through the fan buck (dropout <= 0.5 V assumed)
V_FAN_BUCK = (12.0, 36.0)        # the fan buck's input range (control board)
V_OP_MIN = 250.0                 # PV-03 / PV-06: the module switches (gates on, full load) only from here up
A75_OTHER_STBY = 2.5 + 1.0 + 2.2        # W: bleeders + dividers + varistor monitor loops (sec. 3, not this block)
A75_HOLD = dict(p=11.0, t=0.040, v_end=8.0)
# Bourns SMCJ series (SMCJ_Bourns.pdf) p2: SMCJ33A VBR 36.7-40.6 V @ 1 mA, VRWM 33 V, VC 53.3 V @ IPP 28.1 A; p1 as
# SMCJ70A (1500 W, PM(AV) 5 W @ TL 75 C, TJ <= 150 C). Rev A1: six in series (220-244 V, >= 1.4 x VR) instead of
# 4 x SMCJ54A: lower clamp for the drain margin, the clamp heat over six packages
TVS75 = dict(TVS, vbr=(36.7, 40.6), vc=53.3, ipp=28.1, n=6)
A75_FF = dict(r=10.0e6)          # rev A1: CRHV2512 10 M from HV_BULK into CS - offset Vin x R_CSF / R_FF (line FF)
T_TRIP_MIN75 = 0.030             # rev A2: shortest overload trip (start-up into the hold-up bank checked)
A75_LLK_ROOM = 1.2               # rev A1: leakage requirement = 1.2 x the design's estimate, measured on every unit
A75_LLS_ROOM = 1.5               # rev A3: live-SELV leakage requirement = 1.5 x the design's estimate (A-42's factor; was the
#                                  requirement copied back from the design file, 0.70 uH = 2.1 x the M1 estimate)
# Rubycon ZLH (Rubycon-ZLH.pdf) p2: 35ZLH1500MEFC12.5X30 1500 uF 35 V, 3.45 A rms (105 C, 100 kHz), Z 0.013 R (20 C,
# 100 kHz); p1 +/-20 %, 10000 h @ 105 C (D >= 10 mm)
CHOLD = dict(c=1500e-6, v=35.0, tol=0.20, irip=3.45, z=0.013, n=2)
A75_CAP = dict(live_pol=1, selv_pol=3, mlcc=(2, 10e-6))     # polymer PCV1HVF101MB12FV-WE3 count per output, MLCC
A75_FB = dict(rb=10.0e3)                                      # FB divider bottom, 0.1 %


def a75_design():
    """Design point: Lp and turns from A75 (rev A3 comment there); transformer estimates until the magnetics design exists."""
    x = dict(A75)
    n = x["np"] / x["nl"]
    v0, rd = dout_model(TJ)
    vr = n * (x["vout"] + v0 + rd * 2.0)
    p_design = max(1.10 * x["pout"], x["p_live_peak"] + x["p_selv"])     # rev A2: what the lowest limit must deliver
    lp = x["lp"]
    # A-41: primary leakage (both secondaries shorted): the M1 1-D value scaled by turns^2, an ETD 39-class mean turn
    # and breadth (x 1.2 / (25.5 / 21.6)) and one more secondary layer (x 1.2); nominal 2 x, worst 3 x 1-D (A-1)
    llk_1d = T1_EL["L_leak_1d_H"] * (x["np"] / NP) ** 2 * 1.2 / (25.5 / 21.6) * 1.2
    # A-42: live-to-SELV leakage referred to the live winding: 1-D over the reinforced gap (0.45 mm) and the two
    # layers (0.6 + 1.6 mm), mean turn 72 mm, breadth 25.5 mm, x 2
    l_ls = 2 * MU0 * x["nl"] ** 2 * 0.072 * (0.45e-3 + (0.6e-3 + 1.6e-3) / 3) / 25.5e-3
    rt = A75_FB["rb"] * (x["vout"] / UCC["vfb"][1] - 1)
    rt = min((e96(rt * k) for k in (0.98, 1.0, 1.02)), key=lambda r: abs(UCC["vfb"][1] * (1 + r / A75_FB["rb"]) - x["vout"]))
    k = 0.001
    vset = (UCC["vfb"][0] * (1 + rt * (1 - k) / (A75_FB["rb"] * (1 + k))), UCC["vfb"][1] * (1 + rt / A75_FB["rb"]),
            UCC["vfb"][2] * (1 + rt * (1 + k) / (A75_FB["rb"] * (1 - k))))
    x.update(n=n, vr=vr, p_design=p_design, lp=lp, lp_range=(lp * (1 - AL_TOL), lp * (1 + AL_TOL)),
             llk_1d=llk_1d, llk=2 * llk_1d, llk_wc=3 * llk_1d, l_ls=l_ls, l_ls_wc=1.5 * l_ls, r_fbt=rt, vset=vset,
             i_bias=(UCC["ivdd"][1] + QG_MAX * FS_RANGE[1] + I_VREF_X + 5.0 / R_PULL_LINE + 5.0 / R_SS
                     + (x["vout"] - ZREG[0]) / R_ZREG + vset[1] / (rt + A75_FB["rb"]) + (x["vout"] - Z33["vz"][0]) / R_PGR))
    if os.path.exists(A75_DESIGN_FILE):              # A-51: the magnetics design replaces A-41..A-43, A-45, A-47
        with open(A75_DESIGN_FILE) as fh:
            t = json.load(fh)
        e, cr = t["electrical"], t["core"]
        assert abs(e["L_p_H"] / lp - 1) < 0.01 and e["turns"].startswith("%d:%d:%d " % (x["np"], x["nl"], x["ns"])), e["turns"]
        x.update(llk=e["L_leak_est_H"], llk_wc=A75_LLK_ROOM * e["L_leak_est_H"], l_ls=e["L_leak_live_selv_H"],
                 l_ls_wc=A75_LLS_ROOM * e["L_leak_live_selv_H"], c_w=e["C_switched_P1_SH_own_F"] * C_W_C0 / 27e-12,   # basis as A-33
                 ae=cr["Ae_m2"], amin=cr["A_min_m2"], ve=cr["Ve_m3"],
                 rth_t1=t["thermal"]["T_rise_K"] / t["loss_grid"]["P_total_W"][t["loss_grid"]["V_in_V"].index(1000)], design=t)
    return x


def a75_cycle(x, vin, ipk, v_live, v_selv, tj=TJ, ncut=300):
    """One DCM cycle after turn-off (A-48): magnetising inductance, primary leakage into the TVS clamp, live and SELV
    branches (T-model leakage, winding R, rectifier V0 + rd i), all referred to the live winding; explicit Euler with
    the magnetising voltage solved at each step. Returns charges (C) and energies (J) per cycle."""
    n, a = x["n"], x["ns"] / x["nl"]
    v0, rd = dout_model(tj)
    lm = x["lp"] / n ** 2
    l_sec = x["l_ls"] / 2
    l_p = max(x["llk"] / n ** 2 - l_sec / 2, 0.1 * x["llk"] / n ** 2)
    rd_t = (TVS75["vc"] - TVS75["vbr"][1]) / TVS75["ipp"]
    vcl0 = TVS75["n"] * sum(TVS75["vbr"]) / 2 * (1 + 0.001 * 75) + BYG["vf1"]
    br = [(l_p, vcl0 / n, TVS75["n"] * rd_t / n ** 2), (l_sec, v_live + v0, rd + x["r_l"]),
          (l_sec, (v_selv + v0) / a, (rd + x["r_s"]) / a ** 2)]
    def deriv(cur):
        act = [k for k in range(3) if cur[k] > 0]
        for _ in range(4):
            vm = sum((br[k][1] + br[k][2] * cur[k]) / br[k][0] for k in act) / (1 / lm + sum(1 / br[k][0] for k in act))
            new = [k for k in range(3) if cur[k] > 0 or (k > 0 and vm > br[k][1])]
            if new == act:
                break
            act = new
        return [(vm - br[k][1] - br[k][2] * cur[k]) / br[k][0] if k in act else 0.0 for k in range(3)]
    i = [n * ipk, 0.0, 0.0]
    q, s2 = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    dt0 = lm * n * ipk / (v_live + v0) / ncut
    t, t_cl = 0.0, None
    while i[0] + i[1] + i[2] > 1e-6 * n * ipk and t < 2.0 / FS_RANGE[0]:
        dt = dt0 / 20 if i[0] > 0 else dt0                    # the clamp interval is ~3 % of the reset: finer steps
        k1 = deriv(i)
        ip = [max(i[k] + k1[k] * dt, 0.0) for k in range(3)]
        k2 = deriv(ip)
        i1 = [max(i[k] + 0.5 * (k1[k] + k2[k]) * dt, 0.0) for k in range(3)]     # Heun
        for k in range(3):
            q[k] += 0.5 * (i[k] + i1[k]) * dt
            s2[k] += 0.5 * (i[k] ** 2 + i1[k] ** 2) * dt
        i = i1
        t += dt
        if t_cl is None and i[0] <= 0:
            t_cl = t
    qs, s2s = q[2] / a, s2[2] / a ** 2
    return dict(t_off=t, t_cl=t_cl or t, q_l=q[1], q_s=qs, s2_l=s2[1], s2_s=s2s, l_p=l_p * n ** 2,
                e_cl=br[0][1] * q[0] + br[0][2] * s2[0], e_dl=v0 * q[1] + rd * s2[1], e_ds=v0 * qs + rd * s2s,
                e_rl=x["r_l"] * s2[1], e_rs=x["r_s"] * s2s, e_out=v_live * q[1] + v_selv * qs)


def a75_point(x, vin, p_live, p_selv, v_live=None, tj=TJ, dev=None, strict=True, **over):
    """Steady state (A-48): Ipk - or below the minimum on-time the pulse rate - and the SELV voltage such that each
    rectifier's charge per second equals its load; the live 24 V is regulated. Returns currents and the loss table."""
    x = dict(x, **over)
    v_live = v_live or x["vset"][1]
    dev = dev or DEVICES[SW_MAIN]
    i_l = p_live / v_live + x["i_bias"]
    t_min = T_ON_MIN[1]
    ipk_min = vin * t_min / (x["lp"] + x["llk"])
    i_s = lambda vs: p_selv / vs

    def live_ipk(vs):
        """Ipk that gives the live rectifier its charge at this SELV voltage (None: the live diode never conducts)."""
        hi = 4.0 * math.sqrt(2 * (p_live + p_selv + 1.0) / (x["lp"] * FS_NOM))
        if a75_cycle(x, vin, hi, v_live, vs, tj)["q_l"] * FS_NOM < i_l:
            return None
        return brentq(lambda ip: a75_cycle(x, vin, ip, v_live, vs, tj)["q_l"] * FS_NOM - i_l, 1e-3, hi, xtol=1e-6)

    def r_selv(vs):                      # > 0: the SELV output gets more charge than its load takes -> vs rises
        ip = live_ipk(vs)
        if ip is None:
            return 1.0
        if ip >= ipk_min:
            c, f = a75_cycle(x, vin, ip, v_live, vs, tj), FS_NOM
        else:
            c = a75_cycle(x, vin, ipk_min, v_live, vs, tj)
            f = i_l / max(c["q_l"], 1e-15)
        return c["q_s"] * f / i_s(vs) - 1
    def solve(lo, hi):
        vs = brentq(r_selv, lo, hi, xtol=1e-5)
        ipk = live_ipk(vs)
        skip = ipk < ipk_min
        if skip:
            ipk = ipk_min
        c = a75_cycle(x, vin, ipk, v_live, vs, tj)
        f = i_l / c["q_l"] if skip else FS_NOM
        return vs, ipk, skip, c, f, max(abs(c["q_l"] * f / i_l - 1), abs(c["q_s"] * f * vs / max(p_selv, 1e-3) - 1))
    vs, ipk, skip, c, f, err = solve(0.5 * v_live, 2.0 * v_live)
    if err >= 2e-3:
        # r_selv steps to 1.0 where the live diode never conducts; brentq can land on that step instead of the root
        # (seen in the overload search with the SELV logic at 1.54 W). Scan for a continuous sign change and refine in it.
        grid = [v_live * (0.5 + 1.5 * i / 60) for i in range(61)]
        vals = [r_selv(v) for v in grid]
        for (va, ra), (vb, rb) in zip(zip(grid, vals), zip(grid[1:], vals[1:])):
            if ra != 1.0 and rb != 1.0 and ra * rb <= 0:
                vs, ipk, skip, c, f, err = solve(va, vb)
                break
    # beyond the DCM / CCM boundary (d + d2 > 1) the cycle model has no steady state and the balance cannot close; the
    # overload search probes such points on purpose (strict=False) and reads bal_err as "infeasible"
    assert err < 2e-3 or not strict, ("a75 balance", vin, p_live, p_selv)
    t_on = (x["lp"] + x["llk"]) * ipk / vin
    d, d2 = t_on * f, c["t_off"] * f
    ip_rms = ipk * math.sqrt(d / 3)
    sl = switch_loss(vin, x["vr"], ipk, ip_rms, f, dev, tj, c_w=x["c_w"])
    k, al, be = T1_DESIGN["loss_model"]["core"]["k"], T1_DESIGN["loss_model"]["core"]["alpha"], T1_DESIGN["loss_model"]["core"]["beta"]
    db = x["lp"] * ipk / (x["np"] * x["ae"])
    th = np.linspace(0, 2 * math.pi, 2001)
    ki = k / ((2 * math.pi) ** (al - 1) * np.trapezoid(np.abs(np.cos(th)) ** al, th) * 2 ** (be - al))
    p_core = ki * ((db / t_on) ** al * t_on + (db / c["t_off"]) ** al * c["t_off"]) * db ** (be - al) * f * x["ve"]
    il_rms, is_rms = math.sqrt(c["s2_l"] * f), math.sqrt(c["s2_s"] * f)
    esr_l = 1 / (CHOLD["n"] / CHOLD["z"] + A75_CAP["live_pol"] / CPOL["esr"])
    esr_s = CPOL["esr"] / A75_CAP["selv_pol"]
    loss = {"switch conduction": sl["cond"], "switch turn-on (C_oss + winding C)": sl["on"], "switch turn-off": sl["off"],
            "TVS clamp": c["e_cl"] * f, "live rectifier": c["e_dl"] * f, "SELV rectifier": c["e_ds"] * f,
            "rectifier RC snubbers": C_SN * ((v_live + vin / x["n"]) ** 2 + (vs + vin * x["ns"] / x["np"]) ** 2) * f,
            "transformer core": p_core, "transformer copper": ip_rms ** 2 * x["r_p"] + (c["e_rl"] + c["e_rs"]) * f,
            "current-sense resistors": ip_rms ** 2 * x["rcs"] if "rcs" in x else 0.0,
            "output capacitor ESR": max(il_rms ** 2 - i_l ** 2, 0) * esr_l + max(is_rms ** 2 - (p_selv / vs) ** 2, 0) * esr_s,
            "controller, VDD follower, FB, status": x["i_bias"] * v_live, "HV divider string": vin ** 2 / (3 * R_STR),
            "CS feed-forward resistor": vin ** 2 / A75_FF["r"]}
    p_out = p_live + p_selv
    p_in = p_out + sum(loss.values())
    for _ in range(20):
        i_in = p_in / vin
        loss["input path"] = i_in ** 2 * (x.get("r_lim", R_LIM_EACH) * N_LIM + FUSE["p_in"] / FUSE["i_n"] ** 2) + 0.9 * i_in
        p_in = p_out + sum(loss.values())
    e_mag = 0.5 * (x["lp"] + c["l_p"]) * ipk ** 2
    return dict(vin=vin, p_live=p_live, p_selv=p_selv, v_live=v_live, v_selv=vs, ipk=ipk, f=f, skip=skip, d=d, d2=d2, bal_err=err,
                dcm=d + d2 < 0.98 or skip, ip_rms=ip_rms, il_rms=il_rms, is_rms=is_rms, i_l=i_l, loss=loss, p_in=p_in,
                eta=p_out / p_in, i_in=p_in / vin, e_err=(c["e_cl"] + c["e_dl"] + c["e_ds"] + c["e_rl"] + c["e_rs"]
                                                       + c["e_out"]) / e_mag - 1, db=db, sw=sl, t_on=t_on)


def a75_temps(x, op, dev_name, hic=None):
    """Temperatures at T_AMB (A-9 board estimates; switch per A-44). hic = (duty) for the hiccup two-node case."""
    dev = DEVICES[dev_name]
    p_sw = op["loss"]["switch conduction"] + op["loss"]["switch turn-on (C_oss + winding C)"] + op["loss"]["switch turn-off"]
    p_tvs = op["loss"]["TVS clamp"] / TVS75["n"]
    duty = hic or 1.0
    rows = [(dev_name, p_sw, x["rth_sw"], dev["rth_jc"], dev["tj_max"]),
            ("VS-8ETU04S-M3 live", op["loss"]["live rectifier"], 35.0, DOUT["rth_jc"], DOUT["tj_max"]),
            ("VS-8ETU04S-M3 SELV", op["loss"]["SELV rectifier"], 35.0, DOUT["rth_jc"], DOUT["tj_max"]),
            ("SMCJ33A (each)", p_tvs, TVS75["rth_board"], TVS75["rth_jl"], TVS75["tj_max"]),
            ("AUX-T1", op["loss"]["transformer core"] + op["loss"]["transformer copper"], x["rth_t1"], 0.0, 130.0)]
    return [dict(name=nm, p=p * duty, t=T_AMB + p * duty * rb + p * rj, lim=lim) for nm, p, rb, rj, lim in rows]


def a75_ff(vin, k=1.0):
    """CS offset of the line feed-forward (R_FF into the CS node, R_CSF to the sense resistor), CRHV voltage coefficient."""
    return vin * RCSF / (A75_FF["r"] * k * (1 - CRHV["vcr"] * vin / 1.0) + RCSF)


A75_TD = (UCC["td"][0] + RCSF * CCSF * 0.8, UCC["td"][1] + RCSF * CCSF * 1.2)     # CS delay incl. the filter


def a75_ilim(vin, vcs, rcs, td, lp, kff):
    """Peak current at the CS threshold: (vcs - feed-forward offset) / Rcs + delay overshoot Vin td / Lp."""
    return (vcs - a75_ff(vin, kff)) / rcs + vin * td / lp


def a75_limit(x):
    """Current sense: 3 equal resistors (rev A3: E96) so that the lowest limit still delivers the design power (the 1 s peak)
    at every input (feed-forward included); the highest limit over the whole input range (rev A1: no longer rises with Vin)."""
    lp_min, lp_max = x["lp_range"]
    op = a75_point(dict(x, rcs=0.33), 200.0, x["p_live"], x["p_selv"], llk=x["llk_wc"])
    eta_wc = x["pout"] / (0.5 * x["lp"] * op["ipk"] ** 2 * FS_NOM)
    vins = (V_OP_MIN, 400.0, 600.0, 800.0, VIN_MAX)

    def p_low(rcs):
        return min(0.5 * lp * a75_ilim(v, UCC["vcs"][0], rcs * 1.01, A75_TD[0], lp, 0.99) ** 2 * FS_RANGE[0] * eta_wc
                   for v in vins for lp in (lp_min, lp_max))
    r_each = max(r for r in sorted({round(e * k, 3) for e in E96 for k in (0.1, 1.0)})
                 if 0.68 <= r <= 1.3 and p_low(r / 3) >= x["p_design"])
    rcs = r_each / 3
    i_hi = {v: a75_ilim(v, UCC["vcs"][2], rcs * 0.99, A75_TD[1], lp_min, 1.01) for v in vins + (VIN_TR,)}
    i_max = max(i_hi.values())
    i_lo = {v: a75_ilim(v, UCC["vcs"][0], rcs * 1.01, A75_TD[0], lp_max, 0.99) for v in vins}
    over_noff = VIN_TR * A75_TD[1] / lp_min
    return dict(r_each=r_each, rcs=rcs, min=min(i_lo.values()), max=UCC["vcs"][2] / (rcs * 0.99), max_eff=i_max,
                i_hi=i_hi, i_lo=i_lo, over=i_max - UCC["vcs"][2] / (rcs * 0.99), eta_wc=eta_wc, p_min=p_low(rcs),
                p_max=0.5 * lp_max * i_max ** 2 * FS_RANGE[1],
                max_eff_noff=UCC["vcs"][2] / (rcs * 0.99) + over_noff)


def a75_comp(x, lim, p_mag, vin, lp, fs, acs, voff, rcs, td, kff):
    """COMP level that makes the cycle deliver p_mag (threshold current = Ipk - delay overshoot; feed-forward offset)."""
    ipk = math.sqrt(2 * p_mag / (lp * fs))
    return voff + acs * (rcs * (ipk - vin * td / lp) + a75_ff(vin, kff))


def a75_timer(x, lim):
    """COMP-level overload timer (same circuit as AX-04): the threshold sits above the COMP level of the 1 s peak (rating
    + contactor pull-in) in the worst corner at any input (feed-forward and delay included), so the pull-in never starts it;
    trips in current limit; shortest trip >= T_TRIP_MIN75 (the start-up into the hold-up bank is checked)."""
    p_hold = x["p_live_peak"] + x["p_selv"]
    p_mag = p_hold / lim["eta_wc"]
    lp_min, lp_max = x["lp_range"]
    comp_rated = max(a75_comp(x, lim, p_mag, v, lp, FS_RANGE[0], UCC["acs"][2], VOFF[1], lim["rcs"] * 1.01, A75_TD[0], 0.99)
                     for v in (V_OP_MIN, 600.0, VIN_MAX) for lp in (lp_min, lp_max))
    v_trip = (ATL["vref"][0] - ATL["vdev"] - TLV["vos"], ATL["vref"][2] + ATL["vdev"] + TLV["vos"])
    r_ta = e96_up(R_TB * ((comp_rated + 0.10) / v_trip[0] * 1.01 / 0.99 - 1))
    k = (R_TB * 0.99 / (r_ta * 1.01 + R_TB * 0.99), R_TB / (r_ta + R_TB), R_TB * 1.01 / (r_ta * 0.99 + R_TB * 1.01))
    comp_trip = (v_trip[0] / k[2], ATL["vref"][1] / k[1], v_trip[1] / k[0])
    comp_max = (UCC["vref"][0] - UCC["voh_drop"], UCC["vref"][2])
    r_par = r_ta * R_TB / (r_ta + R_TB)

    def t_trip(c, kk, vt, vc):
        fin = kk * vc
        return math.inf if fin <= vt else -r_par * c * math.log(1 - vt / fin)
    c_tmr = next(c for c in (0.47e-6, 0.68e-6, 1.0e-6) + C_TMR_OPTS
                 if t_trip(c * C_TMR_TOL[0], k[2], v_trip[0], comp_max[1]) >= T_TRIP_MIN75)
    tt = (t_trip(c_tmr * C_TMR_TOL[0], k[2], v_trip[0], comp_max[1]), t_trip(c_tmr, k[1], 2.5, 5.0),
          t_trip(c_tmr * C_TMR_TOL[1], k[0], v_trip[1], comp_max[0]))

    def p_at(comp, vin, lp, fs, acs, voff, vcs, rcs, td, kff):     # output power at a COMP level (CS clamp included)
        i_thr = min((comp - voff) / acs, vcs) - a75_ff(vin, kff)
        return 0.5 * lp * (i_thr / rcs + vin * td / lp) ** 2 * fs * lim["eta_wc"]
    trip = [min(p_at(comp_trip[0], v, lp_min, FS_RANGE[0], UCC["acs"][2], VOFF[1], UCC["vcs"][0], lim["rcs"] * 1.01, A75_TD[0], 0.99)
                for v in (V_OP_MIN, VIN_MAX)),
            p_at(comp_trip[1], 600.0, x["lp"], FS_NOM, UCC["acs"][1], UCC["comp_off"], UCC["vcs"][1], lim["rcs"], sum(A75_TD) / 2, 1.0),
            max(p_at(comp_trip[2], v, lp_max, FS_RANGE[1], UCC["acs"][0], VOFF[0], UCC["vcs"][2], lim["rcs"] * 0.99, A75_TD[1], 1.01)
                for v in (V_OP_MIN, VIN_MAX, VIN_TR))]
    return dict(r_ta=r_ta, c_tmr=c_tmr, t_trip=tt, comp_rated=comp_rated, comp_trip=comp_trip, k=k, v_trip=v_trip,
                comp_max=comp_max, trip_w=tuple(trip), r_par=r_par, p_hold=p_hold)


def a75_string(x):
    """The 30 W string_design() for this case's brown-in / brown-out targets (globals swapped and restored)."""
    global VIN_ON_T, VIN_OFF_T
    keep = VIN_ON_T, VIN_OFF_T
    VIN_ON_T, VIN_OFF_T = x["vin_on"], x["vin_off"]
    try:
        return string_design()
    finally:
        VIN_ON_T, VIN_OFF_T = keep


def a75_window(x):
    """TPS3700 on the live 24 V: OUTB (UV) = status, OUTA (OV) pulls SS low (output-OV stop, auto-restart)."""
    uv, ov, rc = 21.5, x["vset"][2] + 1.8, 10.0e3
    tot = ov / DET["vitp"][1] * rc
    rb = e96(tot * DET["vitn"][1] / uv - rc)
    ra = e96(tot - rb - rc)
    tot, k = ra + rb + rc, 0.001
    return dict(ra=ra, rb=rb, rc=rc,
                uv_fall=(DET["vitn"][0] * tot * (1 - k) / ((rb + rc) * (1 + k)), DET["vitn"][2] * tot * (1 + k) / ((rb + rc) * (1 - k))),
                uv_rise=(DET["vitp"][0] * tot * (1 - k) / ((rb + rc) * (1 + k)), DET["vitp"][2] * tot * (1 + k) / ((rb + rc) * (1 - k))),
                ov_rise=(DET["vitp"][0] * tot * (1 - k) / (rc * (1 + k)), DET["vitp"][2] * tot * (1 + k) / (rc * (1 - k))))


def a75_loop(x, lim, r_ea, c_ea, c_hf):
    """FB divider into the UCC28C59 error amplifier (type II), DCM current-mode plant with the live and referred SELV
    capacitance (A-49); crossover and phase margin over load and line."""
    c_eq = (CHOLD["n"] * CHOLD["c"] * (1 - CHOLD["tol"]) + A75_CAP["live_pol"] * CPOL["c"] + A75_CAP["mlcc"][0] * A75_CAP["mlcc"][1] * 0.6
            + (A75_CAP["selv_pol"] * CPOL["c"] + A75_CAP["mlcc"][0] * A75_CAP["mlcc"][1] * 0.6) * (x["ns"] / x["nl"]) ** 2)
    esr = 1 / (CHOLD["n"] / CHOLD["z"] + A75_CAP["live_pol"] / CPOL["esr"])
    f = np.logspace(0, 5, 2000)
    s = 2j * math.pi * f
    rows = []
    for p in (0.1 * x["pout"], 0.5 * x["pout"], x["pout"]):
        for vin in (200.0, 1000.0):
            v = x["vset"][1]
            ipk = math.sqrt(2 * p / 0.85 / (x["lp"] * FS_NOM))
            dp = x["lp"] * ipk * FS_NOM / (UCC["acs"][1] * lim["rcs"]) * 0.85      # dP/dCOMP
            r_eq = v ** 2 / p
            g = (r_eq / (2 * v) * dp) * (1 + s * esr * c_eq) / (1 + s * r_eq * c_eq / 2)
            zf = r_ea + 1 / (s * c_ea)
            ea = (zf / (1 + s * c_hf * zf)) / x["r_fbt"]
            lg = g * ea
            k = np.where(np.abs(lg) < 1)[0]
            if len(k) == 0:
                rows.append(dict(p=p, vin=vin, fc=None, pm=None))
                continue
            fc = f[k[0]]
            rows.append(dict(p=p, vin=vin, fc=fc, pm=180 + math.degrees(np.angle(lg[k[0]]))))
    return dict(rows=rows, c_eq=c_eq, esr=esr, r_ea=r_ea, c_ea=c_ea, c_hf=c_hf)


def a75_startup(x, lim, tmr, c_ss=C_SS):
    """First switching after VDD reaches UVLO-on: soft start on COMP, rail charge into the hold-up bank, VDD decay
    until the follower takes over, timer node (A-50). Loads behind the status output start at UV-rise."""
    c_vdd = N_CVDD * C_VDD_EACH * 0.7
    i_hold = x["i_bias"] - (x["vout"] - ZREG[0]) / R_ZREG - x["vset"][1] / (x["r_fbt"] + A75_FB["rb"]) - (x["vout"] - Z33["vz"][0]) / R_PGR
    wnd = a75_window(x)
    p_on = sum(A75_LOAD["rating"])          # rev A3: the continuous rating (every build's loads are inside it)
    res = {}
    for corner, vddon, ea, vcs, eta, kc, td, lp in (
            ("slow", UCC["vddon"][0], UCC["ea_src"][0], UCC["vcs"][0], lim["eta_wc"], 1 + CHOLD["tol"], A75_TD[0], x["lp_range"][1]),
            ("fast", UCC["vddon"][2], UCC["ea_src"][1], UCC["vcs"][2], 0.9, 1 - CHOLD["tol"], A75_TD[1], x["lp_range"][0])):
        c_out = (CHOLD["n"] * CHOLD["c"] * kc + A75_CAP["live_pol"] * CPOL["c"]
                 + A75_CAP["selv_pol"] * CPOL["c"] * (x["ns"] / x["nl"]) ** 2)
        for vin in (x["vin_on"], VIN_MAX):
            dt, t, vss, v, vdd, compf, take, on, t_set, vmax, cmax = 20e-6, 0.0, 0.0, 0.0, vddon, 0.0, None, False, None, 0.0, 0.0
            while t < 1.0:
                vss += ((UCC["vref"][1] - vss) / R_SS + ea) / c_ss * dt
                on = on or v >= wnd["uv_rise"][1]
                p_load = (p_on if on else 0.6) + x["i_bias"] * v
                comp = min(vss + 0.35, UCC["vref"][2])
                if v >= x["vset"][1] - 1e-6:               # regulating: the error amplifier sets COMP for the load
                    comp = min(comp, a75_comp(x, lim, p_load / eta, vin, lp, FS_NOM, UCC["acs"][1], UCC["comp_off"],
                                              lim["rcs"], td, 1.0))
                i_thr = min(max(comp - UCC["comp_off"], 0) / UCC["acs"][1], vcs) - a75_ff(vin)
                ipk = i_thr / lim["rcs"] + vin * td / lp if i_thr > 0 else 0.0
                p = 0.5 * lp * ipk ** 2 * FS_NOM * eta
                v = math.sqrt(max(v * v + 2 * (p - p_load) / c_out * dt, 0.0))
                if v >= x["vset"][1]:
                    v = x["vset"][1]
                vmax = max(vmax, v)
                vdd = max(vdd - i_hold / c_vdd * dt, v - 1.0) if take is None else min(v - 1.0, VDD_REG[1])
                if take is None and v - 1.0 >= vdd:
                    take = t
                compf += ((comp * tmr["k"][2] - compf) / (tmr["r_par"] * tmr["c_tmr"] * C_TMR_TOL[0])) * dt
                cmax = max(cmax, compf)
                if t_set is None and v >= x["vset"][0]:
                    t_set = t
                if vdd < vddon - UCC["hyst_min"] and take is None:
                    break
                t += dt
            r = res.setdefault(corner, dict(compf_max=0.0, t_take=0.0, t_settle=0.0, vdd_min=99.0, vmax=0.0))
            r.update(compf_max=max(r["compf_max"], cmax), vdd_min=min(r["vdd_min"], vdd), vmax=max(r["vmax"], vmax),
                     t_take=None if take is None or r["t_take"] is None else max(r["t_take"], take),
                     t_settle=None if t_set is None or r["t_settle"] is None else max(r["t_settle"], t_set))
    return dict(res, i_hold=i_hold, t_hold=c_vdd * UCC["hyst_min"] / i_hold, p_on=p_on)


def a75_xreg(x):
    """Rev A2: the SELV voltage as a function of the two loads (no minimum-load stage): live 2-24 W x fans 10 / 50 / 100 %
    of the 3- and 4-phase fan power, at the inputs where that load occurs (gates on only >= 250 V), nominal and limit
    leakage, cold and hot rectifiers; the SELV voltage scales with the live set-point tolerance. Rev A3: the live 1 s peak with
    the fans at 10 % (the lightest SELV load) is included in the SELV maximum (the pull-in lifts the SELV)."""
    k_lo, k_hi = x["vset"][0] / x["vset"][1], x["vset"][2] / x["vset"][1]
    table = {}
    for ph, pf in A75_FANS.items():
        for pl in A75_LIVE_PTS:
            for frac in (0.10, 0.50, 1.00):
                vs = []
                for vin in ((V_OP_MIN, VIN_MAX) if pl >= min(A75_RULE.values()) else (200.0, VIN_MAX)):
                    for over in ({}, dict(llk=x["llk_wc"], l_ls=x["l_ls_wc"])):
                        for tj in (25.0, 125.0):
                            vs.append(a75_point(x, vin, pl, frac * pf + SELV_LOGIC_W, tj=tj, **over)["v_selv"])
                table[(ph, pl, frac)] = (min(vs) * k_lo, max(vs) * k_hi)
    allv = [v for t in table.values() for v in t]
    peak = max(a75_point(x, vin, x["p_live_peak"], 0.10 * min(A75_FANS.values()) + SELV_LOGIC_W, tj=tj, **over)["v_selv"] * k_hi
               for vin in (V_OP_MIN, VIN_MAX) for over in ({}, dict(llk=x["llk_wc"], l_ls=x["l_ls_wc"])) for tj in (25.0, 125.0))
    rule = {ph: min(table[(ph, pl, 1.0)][0] for pl in A75_LIVE_PTS if pl >= A75_RULE[ph]) for ph in A75_FANS}
    off = [v for (ph, pl, fr), t in table.items() if pl < min(A75_RULE.values()) for v in t]
    return dict(table=table, live=(x["vset"][0], x["vset"][2]), selv=(min(allv), max(allv + [peak])), rule=rule,
                gates_off=(min(off), max(off)), selv_peak=peak)


def a75_rlim(i_in_200):
    """Rev A1 (task 3): smaller series resistors per tap? The AX-01 / AX-02 rules of input_network() for each value:
    hot-plug I2t <= 20 % of the fuse melting I2t, fuse-clearing energy <= 60 % of the resistor rating, fault current,
    resistor surge voltage, film surge voltage / dV/dt / current - and the loss at 200 V, 75 W."""
    rows = []
    for r in (22.0, 20.0, 18.0, 15.0, 10.0):
        rt = N_LIM * r
        tau = rt * C_BULK_TOT
        sv = [v0 + (vres - v0) * (1 - math.exp(-tp / tau)) for vres, tp in ((SPD["up"], 20e-6), (2750.0, 20e-6), (2000.0, 50e-6))
              for v0 in (VIN_MAX, VIN_TR)]
        i0 = (SPD["up"] - VIN_MAX) / rt
        chk = dict(i2t=VIN_TR ** 2 * C_BULK_TOT / (2 * rt) <= 0.2 * FUSE["i2t"],
                   clearing=2 * FUSE["i2t"] <= 0.6 * RLIM["e_per_ohm"], fault=VIN_TR / rt <= 0.01 * FUSE["ibreak"],
                   r_surge=(SPD["up"] - VIN_MAX) / N_LIM <= 0.7 * RLIM["vpk"], film_v=max(sv[0::2]) <= CBULK["v70"]
                   and max(sv) <= CBULK["surge_k"] * CBULK["v70"], film_dvdt=i0 / C_BULK_TOT * 1e-6 <= CBULK["dvdt"],
                   film_i=i0 / N_BULK <= CBULK["ipk"])
        rows.append(dict(r=r, i2t_pct=VIN_TR ** 2 * C_BULK_TOT / (2 * rt) / FUSE["i2t"] * 100, film_v=max(sv[0::2]),
                         loss_200=i_in_200 ** 2 * (rt + FUSE["p_in"] / FUSE["i_n"] ** 2) + 0.9 * i_in_200,
                         p_each=i_in_200 ** 2 * r, ok=all(chk.values()), fails=[k for k, v in chk.items() if not v]))
    return rows


def aux75():
    x = a75_design()
    lim = a75_limit(x)
    x["rcs"] = lim["rcs"]
    if "design" in x:            # A-51: scale the A-46 winding resistances to the design's copper (OpenMagnetics, the higher)
        t = x["design"]
        i = t["loss_grid"]["V_in_V"].index(1000)
        p_cu = max(t["loss_table"][i]["P_cu_W"], t["verification"]["openmagnetics"]["P_cu_W"])    # both at 1000 V
        op = a75_point(x, 1000.0, 82.5 * x["p_live"] / x["pout"], 82.5 * x["p_selv"] / x["pout"])
        k = p_cu / op["loss"]["transformer copper"]
        x.update(r_p=x["r_p"] * k, r_l=x["r_l"] * k, r_s=x["r_s"] * k, p_cu_design=p_cu, k_r=k)
        lim = a75_limit(x)
        x["rcs"] = lim["rcs"]
    rated = lambda v: (x["p_live"], x["p_selv"]) if v >= V_OP_MIN else (A75_STBY["PV-P100/110"][0], x["p_selv"])
    vt = sorted(VIN_TABLE + [V_OP_MIN])                  # 200 V: gates off (module below its 250 V range), fans 100 %
    ops = {v: a75_point(x, v, *rated(v)) for v in vt + [VIN_TR]}
    loads = {name: {v: a75_point(x, v, *pl) for v in vt if v >= V_OP_MIN} for name, pl in A75_LOAD.items() if name != "rating"}
    stby = {name: {v: a75_point(x, v, *pl) for v in VIN_TABLE} for name, pl in A75_STBY.items()}
    stby_hi = {v: a75_point(x, v, A75_STBY["PV-P100/110"][0] + 2 * (2.5 - A75_COIL_HOLD), SELV_LOGIC_STBY_W + 0.1) for v in (800.0, VIN_MAX)}
    sw = {k: {v: ops[v] if k == SW_MAIN else a75_point(x, v, *rated(v), dev=DEVICES[k]) for v in vt} for k in DEVICES}
    temps = {k: {v: a75_temps(x, sw[k][v], k) for v in vt} for k in DEVICES}
    wc = {v: a75_point(x, v, *rated(v), llk=x["llk_wc"]) for v in (V_OP_MIN, 1000.0)}
    temps_wc = {v: a75_temps(x, wc[v], SW_MAIN) for v in (V_OP_MIN, 1000.0)}
    tmr = a75_timer(x, lim)
    # worst overload (rev A1, in the self-check): highest current-limit corner (Lp +7 %, fs max, CS threshold max,
    # longest delay, feed-forward included) at 1000 V, the overload on either output, on for the slowest trip, off for
    # the fastest VDD recharge; temperatures as the hiccup average (two-node model, A-26)
    # rev A2: at a fixed peak current the energy per cycle is largest at the DCM/CCM boundary (CCM: 1/2 Lp (Ipk^2 -
    # Iv^2)), so the overload cannot exceed the power at which the cycle fills the period - p_on is capped there
    duty = tmr["t_trip"][2] / (tmr["t_trip"][2] + 0.04 + SU["t_recharge"][0])
    stb = A75_STBY["PV-P75"]
    xo = dict(x, lp=x["lp_range"][1])
    split = (lambda p: (stb[0], p - stb[0]), lambda p: (p - stb[1], stb[1]))
    hops = []
    for sp in split:
        lo, hi = x["pout"], lim["p_max"] * 0.85
        feasible = lambda o: o["bal_err"] < 2e-3 and o["d"] + o["d2"] <= 1.0      # DCM steady state exists
        if not feasible(a75_point(xo, VIN_MAX, *sp(hi), llk=x["llk_wc"], strict=False)):
            for _ in range(14):
                mid = 0.5 * (lo + hi)
                o = a75_point(xo, VIN_MAX, *sp(mid), llk=x["llk_wc"], strict=False)
                lo, hi = (mid, hi) if feasible(o) else (lo, mid)
        hops.append(a75_point(xo, VIN_MAX, *sp(lo if lo > x["pout"] else hi), llk=x["llk_wc"]))
    p_on = max(h["p_live"] + h["p_selv"] for h in hops)
    hic = [max(rs, key=lambda r: r["t"]) for rs in zip(*(a75_temps(x, h, SW_MAIN, hic=duty) for h in hops))]
    st = a75_string(x)
    wnd = a75_window(x)
    r_ea = e96(4.0 * x["r_fbt"])
    loop = a75_loop(x, lim, r_ea, e12_down(1 / (2 * math.pi * r_ea * 120.0)), e12_down(1 / (2 * math.pi * r_ea * 3500.0)))
    su = a75_startup(x, lim, tmr)
    xr = a75_xreg(x)
    # drain: TVS at its own worst junction temperature (leakage at the requirement limit), VBR max, + 40 V (A-4)
    t_tvs = max(r["t"] for r in temps_wc[VIN_MAX] if r["name"].startswith("SMCJ"))
    rd_t = (TVS75["vc"] - TVS75["vbr"][1]) / TVS75["ipp"]
    v_cl = lambda i: TVS75["n"] * (TVS75["vbr"][1] * (1 + 0.001 * (t_tvs - 25)) + i * rd_t)
    vds = dict(cont=VIN_MAX + v_cl(ops[VIN_MAX]["ipk"]) + 40.0, trans=VIN_TR + v_cl(lim["max_eff"]) + 40.0,
               clamp_min=TVS75["n"] * TVS75["vbr"][0], t_tvs=t_tvs)
    v_rect = (xr["selv"][1] + VIN_TR * x["ns"] / x["np"]) * 1.25
    c_hold = CHOLD["n"] * CHOLD["c"] * (1 - CHOLD["tol"])
    t_hold = 0.5 * c_hold * ((x["vset"][0] - 0.3) ** 2 - A75_HOLD["v_end"] ** 2) / A75_HOLD["p"]
    b_lim = x["lp_range"][1] * lim["max_eff"] / (x["np"] * x["amin"])
    rlim_p = max(o["i_in"] for o in ops.values()) ** 2 * x["r_lim"]
    # switched winding capacitance that keeps the hotter switch 10 % of its 90 K rise below 150 C at 1000 V (A-44 board)
    p_sw = {k: sum(sw[k][VIN_MAX]["loss"][n] for n in ("switch conduction", "switch turn-on (C_oss + winding C)", "switch turn-off"))
            for k in DEVICES}
    t_sw_lim = 150.0 - 0.1 * (150.0 - T_AMB)
    c_w_max = min(x["c_w"] + ((t_sw_lim - T_AMB) / (x["rth_sw"] + DEVICES[k]["rth_jc"]) - p_sw[k])
                  / (0.5 * (VIN_MAX ** 2 + 0.25 * x["vr"] ** 2) * FS_NOM) for k in DEVICES)
    margins = dict(drain_1000V=1 - vds["cont"] / (DERATE["cont"] * MOS["vbr"]), drain_1100V=1 - vds["trans"] / (DERATE["trans"] * MOS["vbr"]),
                   flux=1 - b_lim / B_LIMIT_AMIN, tvs_rise=1 - (t_tvs - T_AMB) / (TVS75["tj_max"] - T_AMB),
                   drain_floor=VIN_MAX + 1.4 * x["vr"] * (TVS75["vbr"][1] / TVS75["vbr"][0]) * (1 + 0.001 * 75) + 40.0)
    return dict(x=x, lim=lim, ops=ops, loads=loads, stby=stby, sw=sw, temps=temps, wc=wc, temps_wc=temps_wc, tmr=tmr,
                hic=hic, hic_duty=duty, hic_pon=p_on, hic_ops=hops, string=st, window=wnd, loop=loop, su=su, xreg=xr,
                vds=vds, v_rect=v_rect, t_hold=t_hold, c_hold=c_hold, b_lim=b_lim, rlim_p=rlim_p, p_sw=p_sw,
                c_w_max=c_w_max, t_sw_lim=t_sw_lim, margins=margins, rlim=a75_rlim(max(o["i_in"] for o in ops.values())), vt=vt,
                i_in_max=max(ops.values(), key=lambda o: o["i_in"]),     # where the input resistors' loss is rated
                stby_hi=stby_hi,
                design_file_present=os.path.exists(A75_DESIGN_FILE))


A75_REUSED = ("F_PORT", "R_LIM", "C_BULK", "C_HF", "R_STR", "C_STR", "R_LINE_PU", "R_LSF", "C_LSF", "R_REF", "R_OVH1",
              "R_OVH2", "R_SU_S", "R_SU_G", "R_SU_EN", "R_SU_EN_PD", "R_T", "C_T", "C_TX", "R_FBK", "R_AF1", "C_AF",
              "R_AF2", "R_QG", "C_VREF", "C_VDD", "R_ZREG", "R_VDDE", "R_SS", "C_SS", "R_TB", "R_SSG", "R_SSG_PD",
              "R_G", "R_GS", "R_CSF", "C_CSF", "R_SN", "C_SN", "R_PGR", "C_PGR", "C_PGF", "C_DEC")
# catalog parts of the block (gen/aux_hv.py CATALOG keys) and their counts; gen asserts that it draws exactly these
A75_PARTS = {"AC10_20R": 4, "FUSE_HV": 2, "BYG10Y": 3, "C4AQ_3U3": 2, "CRHV_10M": 4, "BSS126": 3, "2N7002BK": 4,
             "TPS3710": 1, "TLV3202": 1, "ATL431": 1, "BAT54": 4, "BAS16": 1, "BAT54S": 1, "UCC28C59": 1,
             "BZX84-B20": 1, "US1G": 1, "BZX84-B6V8": 1, "SW1700": 1, "SMCJ33A": 6, "AUXT1_75": 1, "VS-8ETU04S": 2,
             "CHOLD1500": 2, "CPOL100": 4, "BC817-25": 1, "BZX84-A18": 1, "TPS3700": 1, "BZX84-B3V3": 1}


def a75_values(R, v30):
    """Every component value of the block: the 30 W entries of the reused circuit, then this case's own."""
    x, st, tmr, wnd, lp = R["x"], R["string"], R["tmr"], R["window"], R["loop"]
    v = {k: dict(v30[k], why="(30 W case, unchanged) " + v30[k]["why"]) for k in A75_REUSED}

    def put(key, value, why, base=None, **kw):
        v[key] = dict(dict(v30[base]) if base else {}, value=value, why=why, **kw)
    put("R_D1", ohm(st["r1"]), "string tap, brown-in %.0f-%.0f V, brown-out %.0f-%.0f V" % (st["on"][0], st["on"][2], st["off"][0], st["off"][2]), "R_D1")
    put("R_D2", ohm(st["r2"]), "string bottom (same solve)", "R_D2")
    put("R_HYS", ohm(st["rh"]), "brown-in hysteresis (same solve); OV lockout %.0f-%.0f V" % (st["ov"][0], st["ov"][2]), "R_HYS")
    put("C_D1", farad(st["c1"]), "string compensation, under-compensated as the 30 W case", "C_D1")
    put("C_D2", farad(st["c2"]), "string compensation", "C_D2")
    put("R_CS", ohm(R["lim"]["r_each"]), "3 in parallel = %s (E96): lowest limit %.2f A delivers %.1f W (>= the %.1f W 1 s "
        "peak) at every input; highest %.2f A with the line feed-forward (%.2f A without)" % (
            ohm(R["lim"]["rcs"]), R["lim"]["min"], R["lim"]["p_min"], x["p_design"], R["lim"]["max_eff"], R["lim"]["max_eff_noff"]),
        "R_CS", n=3)
    put("R_FF", ohm(A75_FF["r"]), "line feed-forward HV_BULK -> CS (CRHV2512): CS offset %.0f mV at 1000 V cancels most of "
        "the %.0f-%.0f ns CS-delay overshoot, so the highest current limit no longer rises with the input" % (
            a75_ff(VIN_MAX) * 1e3, A75_TD[0] * 1e9, A75_TD[1] * 1e9), part="CRHV_10M", n=1)
    rl = [r for r in R["rlim"] if r["r"] == x["r_lim"]][0]
    put("R_LIM", ohm(x["r_lim"]), "2 per tap AHEAD of the fuse (AC10 pulse-rated, 20 R = the smallest E24 value inside AX-01 / "
        "02: hot-plug I2t %.1f %% of the fuse melting I2t (rule 20 %%), film surge %.0f V); %.2f W each at the highest input "
        "current (%.2f A, %.0f V)" % (rl["i2t_pct"], rl["film_v"], rl["p_each"], R["i_in_max"]["i_in"], R["i_in_max"]["vin"]),
        "R_LIM", n=N_LIM, part="AC10_20R")
    put("R_FBT", ohm(x["r_fbt"]), "live 24 V into FB: %.2f / %.2f / %.2f V (VFB 2.45-2.55 V, 0.1 %%)" % x["vset"], pkg="0603",
        tol="0.1%")
    put("R_FBB", ohm(A75_FB["rb"]), "FB divider bottom", pkg="0603", tol="0.1%")
    put("R_EA", ohm(lp["r_ea"]), "type II: mid-band gain %.1f; crossover %.0f-%.0f Hz, phase margin >= %.0f deg" % (
        lp["r_ea"] / x["r_fbt"], min(r["fc"] for r in lp["rows"]), max(r["fc"] for r in lp["rows"]), min(r["pm"] for r in lp["rows"])))
    put("C_EA", farad(lp["c_ea"]), "type II zero %.0f Hz" % (1 / (2 * math.pi * lp["r_ea"] * lp["c_ea"])), diel="C0G" if lp["c_ea"] <= 10e-9 else "X7R")
    put("C_EAHF", farad(lp["c_hf"]), "type II pole %.0f Hz" % (1 / (2 * math.pi * lp["r_ea"] * lp["c_hf"])), diel="C0G")
    put("R_TA", ohm(tmr["r_ta"]), "timer divider: trips above %.0f / %.0f / %.0f W (no trip at the %.1f W 1 s peak)" % (
        *tmr["trip_w"], tmr["p_hold"]), "R_TA")
    put("C_TMR", farad(tmr["c_tmr"]), "timer %.0f-%.0f ms" % (tmr["t_trip"][0] * 1e3, tmr["t_trip"][2] * 1e3), "C_TMR")
    put("R_PG_A", ohm(wnd["ra"]), "TPS3700 window on the live 24 V: UV falls %.2f-%.2f V (status), OV rises %.2f-%.2f V "
        "(SS pulled low)" % (*wnd["uv_fall"], *wnd["ov_rise"]), "R_PG_A")
    put("R_PG_B", ohm(wnd["rb"]), "window middle", "R_PG_B")
    put("R_PG_C", ohm(wnd["rc"]), "window bottom", "R_PG_C")
    put("C_LIVE_MLCC", "10u", "live 24 V HF bypass", volt="50V", diel="X7R", pkg="1210", n=A75_CAP["mlcc"][0])
    put("C_SELV_MLCC", "10u", "SELV 24 V HF bypass", volt="50V", diel="X7R", pkg="1210", n=A75_CAP["mlcc"][0])
    return v


def a75_nets(R):
    """DC range of each block net against its own reference (live side: BUS-, SELV side: SELV 0 V); switching nodes,
    rectified windings and the HV string taps have no entry (rated in this file)."""
    vdd = 20.4 + 0.018 * 60
    v_live = R["window"]["ov_rise"][1] + 0.5
    v_selv = R["xreg"]["selv"][1] + 1.0
    return {"BUS-": (0, 0), "HV_BULK": (0, 1100), "VREF": (0, 5.2), "VDD": (0, vdd), "VDD_E": (0, vdd), "ZB": (0, 19.2),
            "FBK": (0, 5.2), "CTXD": (0, 2.6), "RTCT": (0, 2.6), "FB": (0, 2.6), "EA_M": (0, 5.2), "COMP": (0, 5.2),
            "SS": (0, 5.2), "LINE_OK": (0, 5.2), "LSO": (0, 2.7), "LS": (0, 2.2), "LSOF": (0, 2.7), "VR25": (0, 2.6),
            "OVREF": (0, 2.6), "OVL_N": (0, 5.2), "COMPF": (0, 5.2), "OLATCH": (0, 5.2), "SSG": (0, 5.2), "SU_EN": (0, 5.2),
            "SU_G": (0, vdd), "AF1": (0, 1.07 * (v_live + 1.0)), "QIG": (0, 1.07 * (v_live + 1.0)), "LIVE": (0, v_live),
            "PG_UV": (0, 2.6), "PG_OV": (0, 2.6), "PG3V3": (0, 3.4), "PG_OVN": (0, 3.4), "STATUS": (0, 5.5),
            "SELV_P": (0, v_selv), "SELV_N": (0, 0)}


def a75_spec(R, v30):
    x, lim = R["x"], R["lim"]
    trow = lambda rows: {r["name"]: dict(t_C=r["t"], p_W=r["p"], limit_C=r["lim"]) for r in rows}
    ins = T1_DESIGN["insulation"]["levels"][0]
    u_rp = R["vds"]["cont"]
    req = dict(
        turns=dict(np=x["np"], n_live=x["nl"], n_selv=x["ns"], ratio_p_live=x["n"], ratio_selv_live=x["ns"] / x["nl"],
                   note="ratios are the requirement; absolute turns may change with the core if Np x A_min holds"),
        lp_mH=dict(value=x["lp"] * 1e3, tol_pct=AL_TOL * 100, at="10 kHz"),
        frequency_kHz=[FS_RANGE[0] / 1e3, FS_NOM / 1e3, FS_RANGE[1] / 1e3],
        mode="DCM at the %.1f W rating from %.0f V and at the gates-off load from 200 V (Lp nominal, self-check)" % (x["pout"], V_OP_MIN),
        ipk_A=dict(full_load=R["ops"][VIN_MAX]["ipk"], limit_max=lim["max_eff"]),
        volt_seconds_max_Vs=x["lp_range"][1] * lim["max_eff"],
        np_x_amin_min_m2=x["lp_range"][1] * lim["max_eff"] / B_LIMIT_AMIN, design_np_x_amin_m2=x["np"] * x["amin"],
        b_limit_on_amin_mT=B_LIMIT_AMIN * 1e3, flux_estimate_mT=R["b_lim"] * 1e3,
        rms_A=dict(primary=max(R["ops"][v]["ip_rms"] for v in VIN_TABLE), live=max(R["ops"][v]["il_rms"] for v in VIN_TABLE),
                   selv=max(R["ops"][v]["is_rms"] for v in VIN_TABLE)),
        leakage_primary_max_uH=x["llk_wc"] * 1e6, leakage_estimate_uH=x["llk"] * 1e6,
        leakage_note="measured on every unit at 100 kHz, live and SELV shorted (routine); every check of this case runs at "
                     "the limit: TVS %.0f C (rating 150), drain %.0f V" % (R["vds"]["t_tvs"], R["vds"]["cont"]),
        leakage_live_selv_max_uH=x["l_ls_wc"] * 1e6,
        switched_capacitance_max_pF=R["c_w_max"] * 1e12, switched_capacitance_design_pF=x["c_w"] * 1e12,
        switched_capacitance_max_pF_magnetics_basis=R["c_w_max"] * 1e12 * 27e-12 / C_W_C0,
        switched_capacitance_note="limit keeps the hotter switch <= %.0f C at 1000 V (10 %% of its rise below 150 C) with "
                                  "the A-44 board; the magnetics P1-SH value x %.3f = this model's basis (as A-33)" % (
                                      R["t_sw_lim"], C_W_C0 / 27e-12),
        np_x_amin_min_m2_10pct=x["lp_range"][1] * lim["max_eff"] / (0.9 * B_LIMIT_AMIN),
        winding_loss_max_W=max(1.5, x.get("p_cu_design", 0.0) * 1.1), core_loss_estimate_W=max(R["ops"][v]["loss"]["transformer core"] for v in VIN_TABLE),
        shield="copper-foil shield between the primary and both secondaries, to BUS- (as M1)",
        insulation={"primary - live 24 V (both on BUS-)": dict(
                        kind="functional (B3)", pd_free_Vpk=max(1750.0, 1.25 * u_rp),
                        note="the live winding sits at BUS-; the primary swings to the drain peak"),
                    "primary + live + shield - SELV": dict(
                        kind="REINFORCED (B1, insulation_spec 'Reinforced HV-PELV, AUX-HV flyback T1' levels)",
                        u_rp_V=u_rp, routine_pd="<= %.0f pC at %.0f Vpk on every unit (extinction >= %.0f Vpk); M1's 2505 Vpk covers it" % (
                            10, 1.875 * u_rp, 1.5 * u_rp),
                        ac_type_test_Vrms=ins["ac"], impulse_V=ins["imp"], creepage_mm=dict(pd1=ins["cr_pd1"], pd2=ins["cr_pd2"]),
                        clearance_mm=ins["clearance"],
                        construction="SELV winding in TIW-litz with tapes as the M1 rules; vacuum-potted case; core = "
                                     "conductive part on the BUS- side")},
        thermal="potted case; total transformer loss <= %.1f W at the %.1f W rating (copper %.1f W + core %.1f W), rise <= 40 K" % (
            max(1.5, x.get("p_cu_design", 0.0) * 1.1) + max(R["ops"][v]["loss"]["transformer core"] for v in VIN_TABLE), x["pout"],
            max(1.5, x.get("p_cu_design", 0.0) * 1.1), max(R["ops"][v]["loss"]["transformer core"] for v in VIN_TABLE)))
    sw = {k: {str(int(v)): dict(loss_W=sum(R["sw"][k][v]["loss"][n] for n in ("switch conduction", "switch turn-on (C_oss + winding C)", "switch turn-off")),
                                tj_C=[r for r in R["temps"][k][v] if r["name"] == k][0]["t"]) for v in VIN_TABLE} for k in DEVICES}
    return dict(project="AUX75 (AUX-T1, 75 W module supply)", rev="A3", generated_by="sim/aux_hv_design.py (case AUX75)",
                note="CALCULATED, not bench-validated; transformer values are estimates (A-41..A-47) until "
                     "sim/out/magnetics/design_aux75_transformer.json exists",
                basis="docs/requirements/ARCHITECTURE-COSTFIRST.md sec. 3 (D-044)",
                design_point=dict(fsw_kHz=FS_NOM / 1e3, lp_mH=x["lp"] * 1e3, n=x["n"], vr_V=x["vr"], ipk_A=R["ops"][200.0]["ipk"],
                                  duty_200V=R["ops"][200.0]["d"], duty_1000V=R["ops"][VIN_MAX]["d"], p_design_W=x["p_design"],
                                  outputs=dict(live=dict(V=x["vset"], rating_W=x["p_live"], ref="BUS-", regulated="FB divider"),
                                               selv=dict(rating_W=x["p_selv"], ref="SELV 0 V", regulated="cross-regulated"))),
                switch=dict(primary=SW_MAIN, alternate=SW_ALT, per_device=sw, board_rth_KW=x["rth_sw"]),
                transformer_requirement=req, values=a75_values(R, v30), parts=A75_PARTS,
                ratings={"3 phases (PV-P75)": dict(live_W=x["p_live"], live_peak_W=x["p_live_peak"],     # rev A3: one design, one live rating
                                                   selv_W=A75_LOAD["PV-P75 (3 phases), full power, fans 100 %"][1],
                                                   total_W=x["p_live"] + A75_LOAD["PV-P75 (3 phases), full power, fans 100 %"][1]),
                         "4 phases (PV-P100/110)": dict(live_W=x["p_live"], live_peak_W=x["p_live_peak"], selv_W=x["p_selv"],
                                                        total_W=x["pout"]),
                         "design (one design for both)": dict(continuous_W=x["pout"], peak_1s_W=x["p_live_peak"] + x["p_selv"],
                                                              current_limit_min_W=lim["p_min"], from_V=V_OP_MIN,
                                                              below_250V="gates off: live <= %.1f W, fans any" % A75_STBY["PV-P100/110"][0])},
                efficiency={"rating %.0f + %.1f W (200 V: gates off, fans 100 %%)" % (x["p_live"], x["p_selv"]):
                            {str(int(v)): R["ops"][v]["eta"] for v in R["vt"]},
                            **{k: {str(int(v)): o["eta"] for v, o in d.items()} for k, d in R["loads"].items()}},
                losses_1000V=R["ops"][VIN_MAX]["loss"], losses_200V=R["ops"][200.0]["loss"],
                temperatures={**{"rating, %d V" % v: trow(R["temps"][SW_MAIN][v]) for v in VIN_TABLE},
                              **{"rating, %d V, %s" % (v, SW_ALT): trow(R["temps"][SW_ALT][v]) for v in VIN_TABLE},
                              "rating, 1000 V, worst leakage": trow(R["temps_wc"][VIN_MAX]),
                              "hiccup at the highest limit (STRESS, see open)": trow(R["hic"])},
                regulation=dict(live_V=R["xreg"]["live"], selv_V_all=R["xreg"]["selv"], selv_V_gates_off=R["xreg"]["gates_off"],
                                selv_table_V={"%s, live %.1f W, fans %d %%" % (ph, pl, fr * 100): list(v)
                                              for (ph, pl, fr), v in R["xreg"]["table"].items()},
                                fan_full_speed_needs_V=V_FAN_FULL, fan_buck_input_V=V_FAN_BUCK,
                                rule="full fan speed is guaranteed while the live 24 V carries >= %.1f W (3 phases) / %.0f W "
                                     "(4 phases), i.e. while the gate bias is on and the converter switches: SELV >= %.1f / "
                                     "%.1f V at 100 %% fans (needs %.1f V); with the gates off the SELV stays %.1f-%.1f V (fan buck "
                                     "%.0f-%.0f V) but full fan speed is not guaranteed" % (
                                         A75_RULE["3 phases"], A75_RULE["4 phases"], R["xreg"]["rule"]["3 phases"],
                                         R["xreg"]["rule"]["4 phases"], V_FAN_FULL, *R["xreg"]["gates_off"], *V_FAN_BUCK)),
                standby={**{k: {str(int(v)): dict(aux_in_W=o["p_in"], module_W=o["p_in"] + A75_OTHER_STBY) for v, o in d.items()}
                            for k, d in R["stby"].items()},
                         "condition": "gates off, fans off, both contactors held at <= %.1f W each (economiser); with 2 x 2.5 W "
                                      "the 4-phase module draws %.1f W at 1000 V" % (A75_COIL_HOLD, R["stby_hi"][VIN_MAX]["p_in"] + A75_OTHER_STBY)},
                startup=dict(vdd_charge_s=list(SU["t_start"]), after_first_switching_ms={k: R["su"][k]["t_settle"] * 1e3 for k in ("slow", "fast")},
                             takeover_ms={k: R["su"][k]["t_take"] * 1e3 for k in ("slow", "fast")}, vdd_holdup_ms=R["su"]["t_hold"] * 1e3,
                             brown_in_V=R["string"]["on"], brown_out_V=R["string"]["off"], ov_lockout_V=R["string"]["ov"]),
                holdup=dict(c_uF=R["c_hold"] * 1e6, t_ms=R["t_hold"] * 1e3, load_W=A75_HOLD["p"], v_end=A75_HOLD["v_end"]),
                protection=dict(current_limit_A=[lim["min"], lim["max_eff"]], current_limit_W=[lim["p_min"], lim["p_max"]],
                                timer_ms=[t * 1e3 for t in R["tmr"]["t_trip"]], timer_trip_W=R["tmr"]["trip_w"],
                                vds_V=R["vds"], live_ov_stop_V=R["window"]["ov_rise"], status_uv_V=R["window"]["uv_fall"]),
                nets=a75_nets(R), design_file=os.path.relpath(A75_DESIGN_FILE, REPO), design_file_present=R["design_file_present"],
                transformer=dict(rev=x["design"]["revision"], construction=x["design"]["construction"],
                                 mpn="AUX-T1 75 W (CUSTOM)", cost_usd=x["design"]["cost"]["catalogue_1k_usd"],
                                 cost_estimates_row=x["design"]["cost"]["cost_estimates_row"]) if "design" in x else None,
                margins=dict(R["margins"], drain_1000V_V=R["vds"]["cont"], drain_1100V_V=R["vds"]["trans"],
                             flux_mT=R["b_lim"] * 1e3, flux_limit_mT=B_LIMIT_AMIN * 1e3, tvs_C_at_leakage_limit=R["vds"]["t_tvs"],
                             note="10 %% on the drain at 1000 V is not reachable: VR >= %.0f V (400 V SELV rectifier at 80 %%) "
                                  "and the clamp >= 1.4 x VR put the floor at %.0f V (ideal clamp, VBR spread and 100 C)" % (
                                      x["vr"], R["margins"]["drain_floor"])),
                overload_case=dict(p_on_W=R["hic_pon"], duty=R["hic_duty"], cycle_fill=max(h["d"] + h["d2"] for h in R["hic_ops"]),
                                   temperatures={r["name"]: dict(t_C=r["t"], limit_C=r["lim"]) for r in R["hic"]},
                                   current_limit_A=[lim["min"], lim["max_eff"]], without_feedforward_A=lim["max_eff_noff"]),
                input_resistors=[{k: v for k, v in r.items()} for r in R["rlim"]],
                assumptions={"A-41": "primary leakage = M1 1-D value scaled (turns^2, ETD 39-class turn / breadth, one more "
                                     "layer); nominal 2 x, worst 3 x 1-D (A-1)",
                             "A-42": "live-SELV leakage: 1-D over the reinforced gap and both layers, x 2",
                             "A-43": "switched winding capacitance 40 pF (the requirement is the limit)",
                             "A-44": "switch on >= 8 cm2 2-oz Cu both sides with vias: 25 K/W to the 60 C ambient",
                             "A-45": "ETD 39-class core (Ae 125, A_min 123 mm2, Ve 11.5 cm3) in DMR95 (M1 Steinmetz)",
                             "A-46": "effective winding resistances (AC, fringing, 100 C): primary 0.9, live 0.04, SELV 0.02 R",
                             "A-47": "transformer 9.5 K/W (potted ETD 39-class case, natural convection)",
                             "A-48": "two-output DCM cycle: T-model leakage, rectifier V0 + rd i, clamp V(i); Heun steps",
                             "A-49": "loop: DCM current-mode plant with the live and referred SELV capacitance, R load",
                             "A-50": "start-up: soft start on COMP, rail charge into the hold-up bank, VDD decay, timer node"})


def a75_report(R, spec):
    x = R["x"]
    L = ["", "## 15. Case AUX75: the 75 W module supply (D-044, ARCHITECTURE-COSTFIRST.md sec. 3)", "",
         "Second named case of this file (`aux75_spec.json`). Same controller, input block per tap, start-up, string, "
         "OV lockout, overload timer, foldback, VDD follower and soft start as the 30 W case; the live 24 V (on BUS-) "
         "regulates through a divider into FB (no opto, no shunt regulator); a reinforced SELV winding feeds fans and "
         "communication. Transformer figures are ESTIMATES (A-41..A-47) until `%s` exists; then they are read from it." % spec["design_file"],
         "", "| item | value |", "|---|---|"]
    w = L.append
    m, hc = spec["margins"], spec["overload_case"]
    w("| rev A1 | transformer = magnetics design %s (A-51); line feed-forward R_FF into CS; clamp 6 x SMCJ33A; timer %.0f-%.0f ms; "
      "input 2 x 20 R per tap |" % (spec["transformer"]["rev"] if spec["transformer"] else "-", R["tmr"]["t_trip"][0] * 1e3, R["tmr"]["t_trip"][2] * 1e3))
    w("| worst overload (in the self-check) | %.0f W on at 1000 V, highest limit corner (%.2f A; %.2f A without feed-forward), "
      "timer %.0f ms, duty %.0f %%, leakage at its limit: %s |" % (
          hc["p_on_W"], hc["current_limit_A"][1], hc["without_feedforward_A"], R["tmr"]["t_trip"][2] * 1e3, hc["duty"] * 100,
          ", ".join("%s %.0f/%.0f C" % (k, v["t_C"], v["limit_C"]) for k, v in hc["temperatures"].items())))
    w("| margins at the worst corner | drain %.0f V at 1000 V (%.1f %% below 1360 V), %.0f V at 1100 V (%.1f %% below 1530 V); "
      "flux %.0f mT on A_min (%.1f %% below %.0f mT); TVS %.0f C at the leakage limit; %s |" % (
          m["drain_1000V_V"], m["drain_1000V"] * 100, m["drain_1100V_V"], m["drain_1100V"] * 100, m["flux_mT"], m["flux"] * 100,
          m["flux_limit_mT"], m["tvs_C_at_leakage_limit"], m["note"]))
    w("| input resistors per tap (AX-01/02 rules) | %s |" % "; ".join(
        "2 x %.0f R: I2t %.1f %%, %.1f W at %.0f V (%.2f W each)%s" % (r["r"], r["i2t_pct"], r["loss_200"], R["i_in_max"]["vin"],
                                                                   r["p_each"], "" if r["ok"] else " FAILS " + "/".join(r["fails"]))
        for r in R["rlim"][:4]))
    w("| rev A3 (re-rated, one design for PV-P75, PV-P100/110, PCS-P125 three- and four-wire) | turns %d:%d:%d (n %.2f, VR %.0f V; "
      "rev A2 48:8:9, 148 V), Lp %.3f mH kept, current sense 3 x %s (E96, rev A2 3 x 0.91 R); the same core, switch, clamp, timer "
      "capacitor and input block; live-SELV leakage requirement %.2f uH = %.1f x the design's estimate (A-42's factor) |" % (
          x["np"], x["nl"], x["ns"], x["n"], x["vr"], x["lp"] * 1e3, ohm(R["lim"]["r_each"]), x["l_ls_wc"] * 1e6, A75_LLS_ROOM))
    w("| ratings (one design) | live %.0f W continuous, %.0f W for 1 s (every build; the 3-phase row carries the same live rating), "
      "SELV %.1f W (3 phases: %.1f W allocation) -> %.1f W continuous, %.1f W for 1 s, from %.0f V (PV-03/06); lowest current "
      "limit %.1f W; SELV at the live peak with the fans at 10 %%: <= %.1f V |" % (
          x["p_live"], x["p_live_peak"], x["p_selv"], A75_LOAD["PV-P75 (3 phases), full power, fans 100 %"][1], x["pout"],
          x["p_live_peak"] + x["p_selv"], V_OP_MIN, R["lim"]["p_min"], R["xreg"]["selv_peak"]))
    w("| design point | %.1f kHz, Lp %.3f mH +/-7 %%, Np:N_live:N_selv %d:%d:%d (n %.1f, VR %.0f V), Ipk %.2f A at %.1f W, D %.2f at 250 V |" % (
        FS_NOM / 1e3, x["lp"] * 1e3, x["np"], x["nl"], x["ns"], x["n"], x["vr"], R["ops"][V_OP_MIN]["ipk"], x["pout"], R["ops"][V_OP_MIN]["d"]))
    w("| efficiency at %.1f W | %s %% at %s V (200 V: gates off, fans 100 %%) |" % (
        x["pout"], " / ".join("%.1f" % (R["ops"][v]["eta"] * 100) for v in R["vt"]), " / ".join("%d" % v for v in R["vt"])))
    for k, d in R["loads"].items():
        w("| efficiency, %s | %s %% |" % (k, " / ".join("%.1f" % (o["eta"] * 100) for o in d.values())))
    for k in DEVICES:
        w("| %s at the rating | %.2f W / Tj %.0f C at 1000 V, %.2f W / %.0f C at 200 V (board %.0f K/W, A-44) |" % (
            k, spec["switch"]["per_device"][k]["1000"]["loss_W"], spec["switch"]["per_device"][k]["1000"]["tj_C"],
            spec["switch"]["per_device"][k]["200"]["loss_W"], spec["switch"]["per_device"][k]["200"]["tj_C"], x["rth_sw"]))
    for nm, rows in (("rating, 1000 V", R["temps"][SW_MAIN][VIN_MAX]), ("rating, 1000 V, worst leakage", R["temps_wc"][VIN_MAX]),
                     ("hiccup, highest limit (stress)", R["hic"])):
        w("| temperatures, %s | %s |" % (nm, ", ".join("%s %.0f C%s" % (r["name"], r["t"], " (over %.0f)" % r["lim"] if r["t"] > r["lim"] else "") for r in rows)))
    w("| live 24 V band | %.2f-%.2f V (set point; FB divider 0.1 %%) |" % R["xreg"]["live"])
    for ph in A75_FANS:
        w("| SELV, %s (fans 10 / 50 / 100 %% of %.0f W) | %s |" % (ph, A75_FANS[ph], "; ".join(
            "live %.1f W: %s V" % (pl, " / ".join("%.1f-%.1f" % R["xreg"]["table"][(ph, pl, fr)] for fr in (0.1, 0.5, 1.0)))
            for pl in A75_LIVE_PTS)))
    w("| firmware rule | %s |" % spec["regulation"]["rule"])
    for k, d in R["stby"].items():
        w("| standby %s (aux in / module) | %s W |" % (k, " / ".join("%.1f/%.1f" % (o["p_in"], o["p_in"] + A75_OTHER_STBY) for o in d.values())))
    w("| start-up | VDD charge %.1f-%.1f s after brown-in (30 W network), then %.0f-%.0f ms to regulation; follower takes VDD at "
      "%.0f-%.0f ms (VDD hold-up %.0f ms) |" % (SU["t_start"][1], SU["t_start"][0], R["su"]["fast"]["t_settle"] * 1e3,
                                                R["su"]["slow"]["t_settle"] * 1e3, R["su"]["fast"]["t_take"] * 1e3,
                                                R["su"]["slow"]["t_take"] * 1e3, R["su"]["t_hold"] * 1e3))
    w("| brown-in / out, OV lockout | %.0f-%.0f V / %.0f-%.0f V, %.0f-%.0f V |" % (R["string"]["on"][0], R["string"]["on"][2],
        R["string"]["off"][0], R["string"]["off"][2], R["string"]["ov"][0], R["string"]["ov"][2]))
    w("| hold-up (live, %.0f W to %.0f V) | %.0f ms with %.0f uF (-20 %%) |" % (A75_HOLD["p"], A75_HOLD["v_end"], R["t_hold"] * 1e3, R["c_hold"] * 1e6))
    w("| current limit / timer | %.2f-%.2f A (%.0f-%.0f W); timer %.0f-%.0f ms, trip above %.0f / %.0f / %.0f W |" % (
        R["lim"]["min"], R["lim"]["max_eff"], R["lim"]["p_min"], R["lim"]["p_max"], R["tmr"]["t_trip"][0] * 1e3,
        R["tmr"]["t_trip"][2] * 1e3, *R["tmr"]["trip_w"]))
    w("| V_DS | %.0f V at 1000 V (limit %.0f), %.0f V at 1100 V (limit %.0f); clamp %d x SMCJ33A >= %.0f V vs VR %.0f V |" % (
        R["vds"]["cont"], DERATE["cont"] * MOS["vbr"], R["vds"]["trans"], DERATE["trans"] * MOS["vbr"], TVS75["n"], R["vds"]["clamp_min"],
        x["vr"]))
    w("| loop | crossover %s Hz, phase margin %s deg |" % (", ".join("%.0f" % r["fc"] for r in R["loop"]["rows"][::2]),
                                                          ", ".join("%.0f" % r["pm"] for r in R["loop"]["rows"][::2])))
    w("")
    w("Assumptions of this case: A-41 leakage (M1 1-D value scaled: %.1f uH nominal, %.1f uH worst), A-42 live-SELV leakage "
      "%.2f uH, A-43 switched capacitance %.0f pF (limit %.0f pF), A-44 switch board %.0f K/W (>= 8 cm2 2-oz Cu both sides "
      "with vias), A-45 ETD 39-class core, A-46 winding resistances %.2f / %.3f / %.3f R, A-47 transformer %.1f K/W, A-48 "
      "two-output cycle model, A-49 loop with the referred SELV capacitance, A-50 start-up model." % (
          x["llk"] * 1e6, x["llk_wc"] * 1e6, x["l_ls"] * 1e6, x["c_w"] * 1e12, R["c_w_max"] * 1e12, x["rth_sw"], x["r_p"], x["r_l"],
          x["r_s"], x["rth_t1"]))
    w("")
    sh = R["stby_hi"]
    w("Open (AUX75 rev A3): module standby < 20 W needs the contactor economiser at <= %.1f W per coil (2 x 2.5 W: %.1f W "
      "module at 1000 V); the flux margin at the highest current limit is %.1f %% (10 %% needs Np x A_min >= %.0f mm2, the design "
      "has %.0f: a larger core); the drain margin is %.1f %%; a continuous load between %.1f W and the timer threshold (%.0f W at "
      "the high corner) is not stopped by this block; the switch needs the A-44 copper area (%.0f K/W); the input path costs %.1f W "
      "at 250 V, full load. Rev A3: AUX-T1 is re-wound (%d:%d:%d, gap re-ground for Lp %.3f mH) - a new sample set is needed "
      "(Lp, both leakages, switched capacitance, PD); the Lp window that holds both the flux margin and the peak delivery with "
      "3 x %s is about +/-0.5 %% of the nominal (the +/-7 %% build tolerance is inside the checks); the E96 sense resistor value "
      "must be confirmed in the chosen low-ohm series." % (
          A75_COIL_HOLD, sh[VIN_MAX]["p_in"] + A75_OTHER_STBY, R["margins"]["flux"] * 100,
          x["lp_range"][1] * R["lim"]["max_eff"] / (0.9 * B_LIMIT_AMIN) * 1e6, x["np"] * x["amin"] * 1e6,
          R["margins"]["drain_1000V"] * 100, x["p_live_peak"] + x["p_selv"], R["tmr"]["trip_w"][2], x["rth_sw"],
          R["ops"][V_OP_MIN]["loss"]["input path"], x["np"], x["nl"], x["ns"], x["lp"] * 1e3, ohm(R["lim"]["r_each"])))
    return L


def a75_selfcheck(R):
    x = R["x"]
    for v, op in R["ops"].items():
        assert abs(op["e_err"]) < 0.02, ("AUX75 cycle energy balance", v, op["e_err"])
        assert op["dcm"] and op["d"] < UCC["dmax"][0], ("AUX75 DCM / duty", v)
    assert R["lim"]["p_min"] >= x["p_design"], "AUX75 current limit"
    assert R["vds"]["cont"] <= DERATE["cont"] * MOS["vbr"] and R["vds"]["trans"] <= DERATE["trans"] * MOS["vbr"], "AUX75 V_DS"
    assert R["vds"]["clamp_min"] >= 1.4 * x["vr"], "AUX75 clamp vs VR"
    assert R["v_rect"] <= 0.8 * DOUT["vrrm"], "AUX75 rectifier reverse voltage"
    assert R["b_lim"] <= B_LIMIT_AMIN, "AUX75 flux at the highest limit (A_min)"
    for k in DEVICES:
        for v in (200.0, 1000.0):
            assert all(r["t"] <= (150.0 if r["name"] in DEVICES else r["lim"]) for r in R["temps"][k][v]), ("AUX75 temps", k, v)
        assert DEVICES[k]["idm"] >= 1.5 * R["lim"]["max_eff"], ("AUX75 switch IDM", k)
    assert all(r["t"] <= r["lim"] for r in R["temps_wc"][VIN_MAX]), "AUX75 temperatures, worst leakage"
    assert x["c_w"] <= R["c_w_max"], "AUX75 switched capacitance"
    assert R["string"]["on"][2] <= 220.0 and R["string"]["ov"][0] >= VIN_TR + OV_MARGIN - 0.1, "AUX75 brown-in / OV lockout"
    assert R["window"]["uv_fall"][1] < x["vset"][0] - 1.0 and R["window"]["ov_rise"][0] > x["vset"][2] + 1.0, "AUX75 window"
    assert all(r["pm"] is not None and r["pm"] >= 45 for r in R["loop"]["rows"]), "AUX75 loop margin"
    for c in ("slow", "fast"):
        s = R["su"][c]
        assert s["t_take"] is not None and s["t_take"] <= 0.7 * R["su"]["t_hold"], ("AUX75 VDD takeover", c)
        assert s["t_settle"] is not None and s["compf_max"] <= 0.98 * R["tmr"]["v_trip"][0], ("AUX75 start-up / timer", c)
    assert R["tmr"]["t_trip"][0] >= T_TRIP_MIN75, "AUX75 timer"
    assert R["t_hold"] >= A75_HOLD["t"], "AUX75 hold-up"
    # standby < 20 W (ARCHITECTURE-COSTFIRST, Megarevo PMD-75-G3 figure) is asserted for PV-P75, the module it is compared with;
    # the 4-phase module carries the same stacked-maxima SELV logic (D-075: 1.44 W in standby, was 0.6 W) and its 1000 V held case
    # is printed instead of asserted (20.0 W at the stack of maxima, R-18: measure on the prototype)
    assert all(o["p_in"] + A75_OTHER_STBY < 20.0 for o in R["stby"]["PV-P75"].values()), "AUX75 module standby (PV-P75)"
    for k, d in R["stby"].items():
        worst = max(o["p_in"] + A75_OTHER_STBY for o in d.values())
        if worst >= 20.0:
            print("[NOTE] AUX75 standby %s: %.2f W at the worst input voltage with the contactors held (stack of maxima; the "
                  "< 20 W figure is met by PV-P75 and at typical values; R-18 measures it)" % (k, worst))
    assert R["rlim_p"] <= 0.6 * RLIM["p70"], "AUX75 input resistor power"
    for ph in A75_FANS:                       # rev A2: the firmware rule and the fan buck's input range
        assert R["xreg"]["rule"][ph] >= V_FAN_FULL, ("AUX75 full fan speed at the gates-on live load", ph, R["xreg"]["rule"][ph])
    assert V_FAN_BUCK[0] <= R["xreg"]["selv"][0] and R["xreg"]["selv"][1] <= V_FAN_BUCK[1], ("AUX75 SELV vs fan buck", R["xreg"]["selv"])
    assert R["xreg"]["selv"][1] <= 0.8 * CPOL["v"], "AUX75 SELV maximum vs the 50 V polymers"
    assert R["lim"]["p_min"] >= x["p_live_peak"] + x["p_selv"], "AUX75 lowest current limit below the 4-phase peak"
    # rev A1: the worst overload case inside every part's rating; margins handed to the magnetics design
    assert all(r["t"] <= r["lim"] for r in R["hic"]), ("AUX75 worst overload (highest limit, longest timer, hiccup)", R["hic"])
    assert max(h["d"] + h["d2"] for h in R["hic_ops"]) <= 1.10, "AUX75 overload point too far into CCM for the DCM cycle model"
    # rev A3: the lowest current limit delivers the 112.2 W 1 s peak; on the EC39A core (50 turns) the flux at the
    # highest limit keeps >= 5 % below the 0.85 x Bs rule (A1 had 13 %); 10 % needs A_min >= the spec's 10 % figure
    assert R["margins"]["flux"] >= 0.05, ("AUX75 flux margin", R["margins"]["flux"])
    assert R["margins"]["tvs_rise"] >= 0.10 and R["c_w_max"] >= x["c_w"], "AUX75 TVS / switched-capacitance margin at the limits"
    assert x["np"] * x["amin"] >= x["lp_range"][1] * R["lim"]["max_eff"] / (0.95 * B_LIMIT_AMIN), "AUX75 Np x A_min"
    assert R["lim"]["max_eff"] < R["lim"]["max_eff_noff"] and all(i >= R["lim"]["min"] for i in R["lim"]["i_lo"].values()), "AUX75 FF"
    ok = [r for r in R["rlim"] if r["ok"]]
    assert min(r["r"] for r in ok) == x["r_lim"] and [r for r in R["rlim"] if r["r"] == x["r_lim"]][0]["ok"], "AUX75 R_LIM choice"
    if R["design_file_present"]:
        assert "design" in x and abs(x["c_w"] / (x["design"]["electrical"]["C_switched_P1_SH_own_F"] * C_W_C0 / 27e-12) - 1) < 1e-9


def plots(spice):
    os.makedirs(OUT, exist_ok=True)
    fig, ax = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
    for r, c in ((dict(vin=600.0, frac=1.0, load="R", ctr=OPT["ctr"][1]), "C0"),
                 (dict(vin=600.0, frac=1.0, load="P", ctr=OPT["ctr"][0]), "C1"),
                 (dict(vin=200.0, frac=SYS["p_standby"] / POUT, load="P", ctr=OPT["ctr"][2]), "C3"),
                 (dict(vin=200.0, frac=0.1, load="R", ctr=OPT["ctr"][2]), "C2")):
        opl = OPS[r["vin"]] if r["frac"] == 1.0 else operating_point(r["vin"], POUT * r["frac"])
        t = plant(FREQ, opl, r["load"]) * comp_opto(FREQ, r["ctr"], OPTO["fc_100r"] * 100 / R_PU, C_Z)
        lab = "%g V, %d %% %s load, CTR %.2f" % (r["vin"], r["frac"] * 100, "resistive" if r["load"] == "R" else "const-power", r["ctr"])
        ax[0].semilogx(FREQ, 20 * np.log10(np.abs(t)), c, label=lab)
        ax[1].semilogx(FREQ, np.unwrap(np.angle(t)) * 180 / math.pi, c)
    ax[0].axhline(0, color="k", lw=0.5)
    ax[1].axhline(-180, color="k", lw=0.5)
    ax[0].set_ylabel("loop gain (dB)")
    ax[1].set_ylabel("phase (deg)")
    ax[1].set_xlabel("frequency (Hz)")
    ax[0].legend(fontsize=7)
    ax[0].set_title("AUX-HV opto loop (calculated, pessimistic opto pole)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "loop_bode.png"), dpi=110)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    names = list(OPS[600.0]["loss"].keys())
    bottom = np.zeros(len(VIN_TABLE))
    for nme in names:
        vals = np.array([OPS[v]["loss"][nme] for v in VIN_TABLE])
        ax.bar([str(int(v)) for v in VIN_TABLE], vals, bottom=bottom, label=nme)
        bottom += vals
    ax.set_ylabel("loss (W) at 30 W out")
    ax.set_xlabel("input voltage (V)")
    ax.legend(fontsize=6, ncol=2)
    ax2 = ax.twinx()
    ax2.plot([str(int(v)) for v in VIN_TABLE], [OPS[v]["eta"] * 100 for v in VIN_TABLE], "k-o")
    ax2.set_ylabel("efficiency (%)")
    ax.set_title("AUX-HV loss breakdown (calculated)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "losses.png"), dpi=110)
    plt.close(fig)
    if spice:
        fig, ax = plt.subplots(3, 1, figsize=(8, 7), sharex=True)
        for vin in (200.0, 600.0, 1000.0):
            d = np.loadtxt(os.path.join(OUT, "spice_wave_%dV.csv" % int(vin)), delimiter=",", skiprows=1)
            t = (d[:, 0] - d[0, 0]) * 1e6
            ax[0].plot(t, d[:, 1], label="%d V" % vin)
            ax[1].plot(t, d[:, 2])
            ax[2].plot(t, d[:, 3])
        ax[0].set_ylabel("v_DS (V)")
        ax[1].set_ylabel("i_primary (A)")
        ax[2].set_ylabel("v_out (V)")
        ax[2].set_xlabel("time (us), last 3 cycles")
        ax[0].legend(fontsize=7)
        ax[0].set_title("AUX-HV ngspice switching waveforms (full load)")
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, "spice_waveforms.png"), dpi=110)
        plt.close(fig)
        fig, ax = plt.subplots(3, 1, figsize=(8, 7.5), sharex=True)
        for mode, col, lab in (("short_nofold", "C3", "foldback disabled"), ("short", "C0", "with foldback")):
            d = np.loadtxt(os.path.join(OUT, "spice_wave_1000V_%s.csv" % mode), delimiter=",", skiprows=1)
            ax[0].plot(d[:, 0] * 1e3, d[:, 2], col, lw=0.6, label=lab)
            ax[1].plot(d[:, 0] * 1e3, d[:, 1], col, lw=0.6)
            ax[2].plot(d[:, 0] * 1e3, d[:, 3], col, lw=0.8)
        ax[0].axhline(ILIM["max_eff"], color="k", ls=":", lw=0.8)
        ax[0].set_ylabel("i_primary (A)")
        ax[1].set_ylabel("v_DS (V)")
        ax[2].set_ylabel("v_out (V)")
        ax[2].set_xlabel("time (ms); output shorted at 0.3 ms, Vin 1000 V")
        ax[0].legend(fontsize=7)
        ax[0].set_title("AUX-HV hard output short in ngspice (linear core, saturation not modelled)")
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, "spice_short.png"), dpi=110)
        plt.close(fig)
        d = np.loadtxt(os.path.join(OUT, "spice_wave_1000V_surge.csv"), delimiter=",", skiprows=1)
        fig, ax = plt.subplots(3, 1, figsize=(8, 7), sharex=True)
        ax[0].plot(d[:, 0] * 1e3, d[:, 5], "C1", label="HV_BULK")
        ax[0].plot(d[:, 0] * 1e3, d[:, 1], "C0", lw=0.5, label="drain")
        ax[0].axhline(DERATE["trans"] * MOS["vbr"], color="r", ls=":", lw=0.8)
        ax[0].legend(fontsize=7)
        ax[1].plot(d[:, 0] * 1e3, d[:, 6], "C2")
        ax[2].plot(d[:, 0] * 1e3, d[:, 3], "C3")
        ax[0].set_ylabel("V")
        ax[1].set_ylabel("COMP (V)")
        ax[2].set_ylabel("v_out (V)")
        ax[2].set_xlabel("time (ms); 4 kV / 20 us port residual at 0.3 ms on 1000 V")
        ax[0].set_title("AUX-HV input surge with the OV lockout (ngspice)")
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, "spice_surge.png"), dpi=110)
        plt.close(fig)
    fig, ax = plt.subplots(3, 1, figsize=(8, 8))
    for i, (key, title) in enumerate(((("alone", "min"), "start, feed held off (cabinet present), low corner"),
                                      (("black", "min"), "black start: one HGATE ramp into the SYS bus, %.1f W, low corner" % SYS["p_total"]),
                                      (("handover", "min"), "cabinet hand-over, %.1f W, low corner" % SYS["p_total"]))):
        tr = np.array(SIM[key]["trace"])
        ax[i].plot(tr[:, 0] * 1e3, tr[:, 1], label="AUX-HV output")
        ax[i].plot(tr[:, 0] * 1e3, tr[:, 2], label="SYS bus (feed closed)")
        ax[i].plot(tr[:, 0] * 1e3, tr[:, 3], label="VDD")
        ax[i].plot(tr[:, 0] * 1e3, tr[:, 5] * 10, label="timer node x10")
        ax[i].axhline(SYS["feed_ov"][0], color="r", ls=":", lw=0.8)
        ax[i].set_title(title, fontsize=9)
        ax[i].set_ylabel("V")
    ax[0].legend(fontsize=7, ncol=2)
    ax[2].set_xlabel("time (ms)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "system_start.png"), dpi=110)
    plt.close(fig)


def fmt_loss_table():
    rows = ["| loss (W) at 30 W out | " + " | ".join("%d V" % v for v in VIN_TABLE) + " |",
            "|---|" + "---|" * len(VIN_TABLE)]
    for name in OPS[600.0]["loss"]:
        rows.append("| %s | " % name + " | ".join("%.2f" % OPS[v]["loss"][name] for v in VIN_TABLE) + " |")
    rows.append("| **total loss** | " + " | ".join("%.2f" % (OPS[v]["p_in"] - POUT) for v in VIN_TABLE) + " |")
    rows.append("| **efficiency** | " + " | ".join("**%.1f %%**" % (OPS[v]["eta"] * 100) for v in VIN_TABLE) + " |")
    return rows


SOURCING = [
    ("1700 V SiC switch", "Wolfspeed C3M0900170J-TR", "InventChip IV2Q171R0D7Z; alternate Sichain SG2M1K0170J2J",
     "both pin-compatible, both fit (sec. 9); IV2Q is AEC-Q101 and the hotter one, so it sets the design; SG2M states no "
     "qualification; neither is on the LCSC mirror (RFQ)"),
    ("input film 3.3 uF 1300 V (x2)", "KEMET C4AQUBU4330A11J", "Jianghai FCSA3DS335K050ID90BE3",
     "same 3.3 uF / 1300 V (70 C) / 27.5 mm; IEC 61071, 80 V/us and 264 A vs 29 V/us and 95 A; not on the LCSC mirror, "
     "maker's distribution (Jianghai Europe)"),
    ("output polymer 100 uF (x3)", "Wurth 875115655003 (35 V)", "Jianghai PCV1HVF101MB12FV-WE3 (50 V, rev C1)",
     "25 vs 30 mOhm, 3.8 vs 2.8 A; 50 V because the M1 leakage lifts the backup band to ~31 V (rev C: "
     "PCV1VVF101MB70FV-WE3 35 V); 3000 h vs 5000 h at 105 C - at ~65 C and a fifth of the ripple rating not "
     "life-limiting; RFQ"),
    ("PWM controller", "TI UCC28C59QDRQ1", "keep (candidate ROHM BD28C59FJ-LB, JP)",
     "ROHM has the same 16 / 12.5 V UVLO and 50 % duty (industrial rank); adopting it means re-deriving the COMP offset / "
     "gain, CS delay and oscillator data the loop, current limit and timer use - next rev"),
    ("dual comparator", "TI TLV3202AIDGKR", "keep (candidate 3PEAK TP1942-SR)",
     "TP1942: same pin-out, VOS 3 mV, 46 uA, LCSC C248572 - but VOL is specified only at 1 mA; the OV lockout must sink "
     "~4.3 mA out of COMP below 1.15 V (TLV3202: <= 0.225 V at 4 mA)"),
    ("shunt reference (x2)", "TI ATL431BIDBZR", "keep (candidate SGMICRO SGM431B)",
     "SGM431B Imin 0.7 mA, Iref 2 uA, VI(dev) 25 mV: widens the 22.8 V set point by ~1 % and needs twice the bias - the "
     "low-power fast-lane loop relies on ATL431's 35 uA / 150 nA"),
    ("window / line detectors", "TI TPS3700DDCR", "keep (candidates SGMICRO SGM882 / SGM883)",
     "pin-compatible, 400 mV reference, but 27.5 mV internal hysteresis moves the PG release to ~22.8 V (above the "
     "22.40 V regulation minimum) and the brown-in window - needs a redesign of both windows"),
    ("line detector", "TI TPS3710DDCR", "keep (candidate SGMICRO SGM883)", "as above"),
    ("PG buffer", "TI SN74LVC1G17DBVR", "keep", "0.08 USD; the UMW copy on LCSC was not checked for Ioff"),
    ("rectifiers", "Vishay BYG10Y-E3/TR", "keep (candidate TSC S1YH, AEC-Q101)",
     "Taiwan Semiconductor datasheets found; 1600 V avalanche / IFSM / slow recovery for the clamp not checked in rev C"),
    ("aux rectifiers", "Diodes US1G-13-F", "keep (candidate TSC US1GH)", "not checked in rev C (cents)"),
    ("output rectifier", "Vishay VS-8ETU04S-M3", "keep (candidate TSC SFAS806GH)",
     "600 V superfast, higher VF; 8 A FRED VF / trr comparison not done in rev C"),
    ("clamp TVS (x3)", "Bourns SMCJ100A (x2)", "Bourns SMCJ70A (x3), rev C1 (candidates TSC SMCJ70AH, JSCJ)",
     "rev C1: the M1 transformer's leakage needs the clamp heat spread over three packages ; Asian "
     "equivalents and an LCSC listing for SMCJ70A not checked"),
    ("small signal", "Nexperia BAT54 / BAT54S / BAS16 / BZX84 / BC817 / 2N7002BK", "keep",
     "cents; the design uses their hot-leakage, B/A-grade tolerance and 6V8 low-current curves (JSCJ / LRC / TSC "
     "equivalents exist on LCSC, not checked against those curves); Nexperia is owned by the Chinese Wingtech group"),
    ("depletion FET", "Infineon BSS126H6327XTSA2", "keep", "no Asian 600 V depletion SOT-23 found quickly"),
    ("pulse resistor (x4)", "Vishay AC10000002209JAB00", "keep",
     "no Asian cemented wirewound datasheet with a single-pulse energy curve found (24 J fuse-clearing energy)"),
    ("HV string resistor (x3)", "Vishay CRHV2512AF10M0FKFB", "keep", "3 kV working voltage at 2512 not confirmed for "
     "the Asian HV chip series looked at"),
    ("fuse (x2)", "Schurter 0090.1001", "keep", "no Asian 1 A 1000 VDC gPV PCB fuse with I2t and time-current curve (as D-033)"),
    ("T1 core / bobbin", "TDK ETD 29 N97 + B66359B1013T001", "DMEGC EC34A DMR95 (magnetics design M1, rev C1)",
     "ETD 34 class, potted case; core ~0.40 USD, whole T1 estimated 5.9 USD (cost_estimates.csv, no quote)"),
    ("optocoupler", "Vishay CNY65B", "keep", "no Asian reinforced optocoupler with >= 14 mm and a granted certificate found"),
    ("terminals / connector", "Wurth 7461057, Phoenix 1720479", "keep", "mechanical; the connector mates with SYS-IO-AUX"),
]


def price_of(mpn):
    """Lowest-quantity price on file in gen/data/prices.csv (any source) for an MPN prefix, as text."""
    path = os.path.join(REPO, "gen", "data", "prices.csv")
    best = None
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["mpn"].split(",")[0] == mpn.split(",")[0] and r["unit_price"]:
                q = int(r["qty"] or 1)
                if best is None or q < best[0]:
                    best = (q, float(r["unit_price"]), r["source"].split(" ")[0])
    return "%.2f USD (%s)" % (best[1], best[2]) if best else "-"


def report(spice, comp, sc):
    L = []
    w = L.append
    w("# AUX-HV design report, rev C1 (CALCULATED / SIMULATED - not bench-validated)")
    w("")
    w("Written by `sim/aux_hv_design.py`. Requirement: ECO-06 (HV bootstrap supply), candidate PV-C2 (start <= 250 V). "
      "Schematic values: `aux_hv_spec.json` -> `gen/aux_hv.py`. Rev C: sourcing policy D-031 (Asian switch and parts, "
      "sec. 9 and 14), the final SYS-IO-AUX rev E feed (D-034, sec. 6), the insulation coordination D-032 / IC-15 / "
      "IC-17 (sec. 2, 11), no-load and standby power (sec. 9). Rev C1: the magnetics design rev M1 of T1 (A-33). "
      "Rev B (reviews AX-01..05, INT-08..13) in sec. 0b.")
    w("")
    hic_r = [v for k, v in OVT.items() if k.startswith("hiccup")][0]
    w("## 0. Rev C1 changes (T1 = magnetics design rev %s)" % T1_DESIGN["revision"])
    w("| item | change | numbers |")
    w("|---|---|---|")
    w("| T1 | %s; replaces the rev C0 ETD 29 / N97 interim build | loss at 30 W %.2f-%.2f W (copper x%.2f: "
      "OpenMagnetics), %.0f C at 60 C; leakage %.1f uH (worst %.1f uH, A-1; rev C0 %.1f uH); switched capacitance "
      "%.1f pF vs %.1f pF limit; %.0f mT on A_min at the highest trip vs %.0f mT; cost estimate %.1f USD |" % (
          T1_DESIGN["construction"].split(",")[0], min(OPS[v]["loss"]["transformer core"] + OPS[v]["loss"]["transformer copper"] for v in VIN_TABLE),
          max(OPS[v]["loss"]["transformer core"] + OPS[v]["loss"]["transformer copper"] for v in VIN_TABLE), T1_K_OM,
          T_AMB + T1_RTH * max(OPS[v]["loss"]["transformer core"] + OPS[v]["loss"]["transformer copper"] for v in VIN_TABLE),
          LLK * 1e6, LLK_WC * 1e6, 2 * LLK_1D_C0 * 1e6, C_W * 1e12, C_W_MAX * 1e12, B_LIM_AMIN * 1e3,
          B_LIMIT_AMIN * 1e3, T1_DESIGN["cost"]["total_usd"]))
    w("| efficiency | interim 2.3 W winding loss replaced by the design | %s at 200-1000 V: 80 %% target at 400-800 V "
      "%s |" % (" / ".join("%.1f" % (OPS[v]["eta"] * 100) for v in VIN_TABLE),
                "met" if all(OPS[v]["eta"] >= ETA_MIN for v in (400.0, 600.0, 800.0)) else "**missed**"))
    w("| drain clamp | 2 x SMCJ100A -> 3 x SMCJ70A (the M1 leakage is +21 %%: with two TVS the worst-leakage and "
      "hiccup cases reach ~155 / ~164 C) | TVS %.0f C at 30 W, %.0f C worst leakage, %.0f C in hiccup (limit %.0f C, "
      "Bourns TJ max); V_DS %.0f V at 1000 V (limit %.0f V), %.0f V at 1100 V (limit %.0f V) |" % (
          OVT["30 W, 1000 V, typical"][2]["t"], OVT["30 W, 1000 V, worst leakage (A-1)"][2]["t"], hic_r[2]["t"],
          TVS["tj_max"], VDS["cont"], DERATE["cont"] * MOS["vbr"], VDS["trans"], DERATE["trans"] * MOS["vbr"]))
    w("| output polymer | 35 V -> 50 V (PCV1HVF101MB12FV-WE3, same series, 100 uF): the larger leakage spike on the "
      "aux winding widens the INT-08 backup band | backup band %.2f-%.2f V (rev C0 23.5-28.4 V) vs %.1f V (85 %% of "
      "50 V); SYS-IO-AUX's feed input must tolerate it |" % (BACKUP_BAND[0], BACKUP_BAND[1], 0.85 * CPOL["v"]))
    w("| rating | same 30 W continuous | %.0f W for <= %.0f ms; overload timer %.0f-%.0f ms |" % (
        math.floor(ILIM["p_min"]), OVLD["t_trip"][0] * 1e3, OVLD["t_trip"][0] * 1e3, OVLD["t_trip"][2] * 1e3))
    w("")
    w("## 0a. Rev C changes")
    w("| item | change | numbers |")
    w("|---|---|---|")
    w("| switch | %s (primary, AEC-Q101) / %s (alternate, same pin-out); design takes the worse figure | switch loss "
      "at 1000 V %.2f / %.2f W, Tj %.0f / %.0f C; efficiency at 1000 V %.1f / %.1f %% |" % (
          SW_MAIN, SW_ALT, SW_PWR[SW_MAIN], SW_PWR[SW_ALT], T_AMB + SW_PWR[SW_MAIN] * (35 + DEVICES[SW_MAIN]["rth_jc"]),
          T_AMB + SW_PWR[SW_ALT] * (35 + DEVICES[SW_ALT]["rth_jc"]), SWCMP[SW_MAIN][VIN_MAX]["eta"] * 100,
          SWCMP[SW_ALT][VIN_MAX]["eta"] * 100))
    w("| gate drive | follower reference BZX84-B16 -> BZX84-A18, VDD clamp B18 -> B20 | VGS %.1f-%.1f V over %d..%d C "
      "(both makers recommend 15-18 V); hold-up %.1f ms with %.1f nC |" % (
          VDD_REG[0], VDD_REG[2], T_LOCAL[0], T_LOCAL[1], SU["t_hold"] * 1e3, QG_MAX * 1e9))
    real = [k for k in SIM if k[0] != "black07"]          # a black start has the port contactors open (0.6 A max)
    w("| SYS rev E feed | black start one ramp, hand-over within 6 ms, 27.8 / 30.15 W load | black start <= %.1f W, "
      "hand-over <= %.1f W (30.15 W case incl.), timer node <= %.2f V vs %.2f V lowest trip, bus >= %.2f V; rating 30 W "
      "continuous stays. Stress case black start with 0.7 A held coils (not possible: contactors open): %s |" % (
          max(SIM[k]["p_peak"] for k in real if k[0].startswith("black")),
          max(SIM[k]["p_peak"] for k in real if k[0].startswith("handover")),
          max(SIM[k]["compf_max"] for k in real), OVLD["v_trip"][0],
          min(SIM[k]["bus_min"] for k in real if k[0].startswith("handover")),
          "hiccup in the lowest corner" if SIM[("black07", "min")]["trip"] else "no trip"))
    w("| T1 insulation | coordinated levels in the T1 spec (rev C0: %d tapes each side of the TIW, impregnation; "
      "rev C1: the design M1 build, sec. 11) | PD <= 10 pC at 2461 Vpk, 4400 V rms 60 s, 8 kV |" % N_TAPE_S)
    w("| T1 magnetics (MG-11/12/13) | rev C0: interim 2.3 W winding loss; requirements handed to the magnetics design "
      "| winding loss <= %.2f W; rev C1 numbers in sec. 0 |" % T1_CU_MAX)
    w("| standby | no-load / standby input power added | %.2f-%.2f W no load, %.1f-%.1f W at %.1f W standby |" % (
        min(STBY[v]["noload"]["p_in"] for v in VIN_TABLE), max(STBY[v]["noload"]["p_in"] for v in VIN_TABLE),
        min(STBY[v]["standby"]["p_in"] for v in VIN_TABLE), max(STBY[v]["standby"]["p_in"] for v in VIN_TABLE),
        SYS["p_noport"]))
    w("| sourcing | Asian parts where the datasheet covers the circuit's needs | sec. 14 |")
    w("| part stress | new `vrange` check in gen/aux_hv.py (VDC table) + this calculation for switching nodes | rev B "
      "values over the limit: R_AF2 10k 0603 and R_ZREG 10k 0603 (101 mW each, limit 60 mW), R_PGR 4.7k 1206 (176 mW, limit 150 mW) -> "
      "22k (with 470 pF, same time constants), 1206, 10k; %d switching / tap parts checked here: %s |" % (
          len(PART_STRESS), "all inside" if all(ok for _, ok in PART_STRESS) else "FAIL"))
    w("")
    w("## 0b. Rev B resolutions (kept)")
    s4 = [x for x in INP["surges"] if x["v0"] == VIN_MAX]
    w("| item | resolution | numbers |")
    w("|---|---|---|")
    w("| AX-01 surge | %d x %s per port + %d x 3.3 uF film absorb the SPD residual (no clamp can stand off 1100 V and "
      "clamp <= 1.25 kV); TLV3202 OV lockout stops switching | HV_BULK %s V (4 kV/20 us, 2.75 kV/20 us, 2 kV/50 us from "
      "1000 V); lockout %.0f-%.0f V (release %.0f V); V_DS at the stop %.0f V (%.1f %%), afterwards = HV_BULK |" % (
          N_LIM, ohm(R_LIM_EACH), N_BULK, " / ".join("%.0f" % x["vbulk"] for x in s4), STR["ov"][0], STR["ov"][2],
          STR["rel"][1], OVL["vds_stop"], OVL["vds_stop"] / 17))
    w("| AX-02 port-B fuse | resistors AHEAD of each fuse: fault current <= %.0f A, so the 20 kA rating is irrelevant "
      "for the 50 kA port | clearing %.0f J per AC10 vs ~%.0f J pulse rating |" % (INP["i_fault"], INP["e_fault"], INP["e_cap"]))
    w("| AX-03 staircase | foldback: aux-plateau detector adds C_TX on RT/CT below %.1f-%.1f V output | fsw %.1f -> %.1f kHz "
      "(max %.1f, staircase-free up to %.1f kHz at 1100 V); ngspice sec. 10 |" % (
          FB["v_fold"][0], FB["v_fold"][1], FS_NOM / 1e3, F_FOLD / 1e3, F_FOLD_RANGE[1] / 1e3, FB["f_safe"] / 1e3))
    w("| AX-04 overload | COMP-level timer (TLV3202 ch 2) latches SS low -> hiccup | trips above %.0f / %.0f / %.0f W "
      "(min/typ/high corner) after %.0f-%.0f ms; never below the 30 W need (COMP %.2f V < %.2f V) |" % (
          OVLD["trip"]["min"]["pout"], OVLD["trip"]["typ"]["pout"], OVLD["trip"]["max"]["pout"], OVLD["t_trip"][0] * 1e3,
          OVLD["t_trip"][2] * 1e3, OVLD["comp_rated"], OVLD["comp_trip"][0]))
    w("| INT-08 backup band | aux sense %s / %s, R_FBT %s (0.1 %%), computed tracking budget (rev A: flat +/-5 %%) | "
      "%.2f-%.2f V, %.2f-%.2f V loaded >= 13 W; above 23.97 V the SYS feed OV isolates it before the 26.13 V bus "
      "cut-off |" % (ohm(R_AUXS), farad(C_AUXS), ohm(R_FBT), *BACKUP_BAND, *BK["loaded"]))
    w("| INT-13 PG | TPS3700 -> SN74LVC1G17 (Ioff) on a 3.3 V Zener rail, 1k series | high %.2f-%.2f V; dead / unplugged = "
      "0 V via the SYS 100k |" % PGD["v_high"])
    w("| INT-11 DAB | same board, port-B input parts not fitted | bom/AUX-HV_DAB_BOM.csv |")
    w("| INT-09/10 | 13 W standby case in loop and start-up; hand-over 34 W | sec. 6 |")
    w("| AX-05 | %s from VDD_E to PGND (100k would let 330 uA at 125 C lift V_E to 33 V) | V_EB %.1f / %.1f V at 85 / 125 C |" % (
        ohm(R_VDDE), *SU["v_e"]))
    w("| SYS rev E | output in 22.40-23.15 V; overshoots below the latched feed OV | feed UVLO max %.2f V; max overshoot %.2f V "
      "(margin %.2f V to 23.97 V); hand-over bus min %.2f V (> %.2f V logic UVLO) |" % (
          SYS["feed_uvlo"][1], sc["vmax"], SYS["feed_ov"][0] - sc["vmax"], sc["bus_min"], SYS["logic_uvlo"]))
    w("")
    w("## 1. Topology and controller")
    w("- Single-switch flyback, fixed %.1f kHz (UCC28C59-Q1: oscillator %.1f kHz halved by the internal T flip-flop, "
      "duty hard-limited to 47-48 %%), DCM at full load down to %.0f V with Lp at +%d %%, peak current mode." % (
          FS_NOM / 1e3, 2 * FS_NOM / 1e3, V_DCM_MIN, AL_TOL * 100))
    w("- Why: one 1700 V SiC switch (rev C: %s / %s, the topology of Wolfspeed CRD-020DD17P-J) covers 200-1100 V "
      "without a high-side driver; DCM gives a first-order loop and no output-rectifier recovery; the 48 %% duty "
      "limit removes slope compensation; the 16/12.5 V UVLO keeps the SiC gate above 12 V." % (SW_MAIN, SW_ALT))
    w("- Regulation: secondary ATL431 + CNY65B optocoupler (reinforced, VIORM 1800 Vpk), R-C feed-forward %s + %s from "
      "the output to the ATL431 REF so that the integrator cannot wind up during start-up or the cabinet hand-over. "
      "Backup: the controller's own error amplifier regulates the auxiliary winding at %.2f-%.2f V output if the opto "
      "loop opens." % (ohm(R_FF), farad(C_FF), *BACKUP_BAND))
    w("")
    w("| Vin | Ipk A | D | D2 | D + D2 | mode | efficiency |")
    w("|---|---|---|---|---|---|---|")
    for v in VIN_TABLE:
        o = OPS[v]
        w("| %d V | %.3f | %.3f | %.3f | %.3f | %s | %.1f %% |" % (v, o["ipk"], o["d"], o["d2"], o["d"] + o["d2"],
                                                            "DCM" if o["dcm"] else "CCM", o["eta"] * 100))
    w("")
    w("## 2. Transformer (CUSTOM, magnetics design rev %s: %s %s) - turns and Lp unchanged" % (
        T1_DESIGN["revision"], T1_CORE["maker"], T1_CORE["part_number"]))
    tr = transformer_spec()
    w("| item | value |")
    w("|---|---|")
    for k, v in (("Np / Ns / Na", "%d / %d / %d (n = %.2f, aux ratio %.3f)" % (NP, NS, NA, N, NA / NS)),
                 ("Lp", "%.3f mH +/-%d %% (BCM boundary %.3f mH at %.0f V, %.0f W)" % (LP * 1e3, AL_TOL * 100, LP_BCM * 1e3, V_DCM_MIN, P_DESIGN)),
                 ("AL / gap", "%.0f nH / %.2f mm (design %s)" % (AL * 1e9, GAP * 1e3, T1_DESIGN["revision"])),
                 ("reflected voltage VR", "%.0f V (600 V, full load)" % OPS[600.0]["vr"]),
                 ("peak / RMS primary current", "%.3f A / %.3f A at 200 V; %.3f A / %.3f A at 1000 V" % (
                     OPS[200.0]["ipk"], OPS[200.0]["ip_rms"], OPS[1000.0]["ipk"], OPS[1000.0]["ip_rms"])),
                 ("peak / RMS secondary current", "%.2f A / %.2f A" % (OPS[600.0]["is_pk"], OPS[600.0]["is_rms"])),
                 ("flux density", "%.0f mT pk at full load, %.0f mT at the highest current-limit trip on A_e (limit %.0f), %.0f mT on A_min (limit %.0f)" % (
                     B_FL * 1e3, B_LIM * 1e3, B_LIMIT * 1e3, B_LIM_AMIN * 1e3, B_LIMIT_AMIN * 1e3)),
                 ("winding stack height", "%.2f mm of %.2f mm window" % (STACK_H * 1e3, T1_DESIGN["fit"]["available_m"] * 1e3)),
                 ("leakage (A-1)", "1-D estimate %.1f uH -> design %.1f uH, worst %.1f uH" % (LLK_1D * 1e6, LLK * 1e6, LLK_WC * 1e6)),
                 ("switched winding capacitance (A-2, A-33)", "%.1f pF (rev C0 ETD 29 stack %.1f pF)" % (C_W * 1e12, C_W_C0 * 1e12)),
                 ("transformer loss / temperature (A-33)", "%.2f-%.2f W at 30 W, %.1f K/W -> %.0f C at 60 C" % (
                     min(OPS[v]["loss"]["transformer core"] + OPS[v]["loss"]["transformer copper"] for v in VIN_TABLE),
                     max(OPS[v]["loss"]["transformer core"] + OPS[v]["loss"]["transformer copper"] for v in VIN_TABLE), T1_RTH,
                     T_AMB + T1_RTH * max(OPS[v]["loss"]["transformer core"] + OPS[v]["loss"]["transformer copper"] for v in VIN_TABLE)))):
        w("| %s | %s |" % (k, v))
    w("")
    w("**Magnetics review (MG-11/12/13, sim/out/magnetics/report.md) and the electrical requirements handed to the T1 "
      "construction (aux_hv_spec.json transformer.requirements):**")
    w("")
    rq = tr["requirements"]
    w("| requirement | value | status |")
    w("|---|---|---|")
    w("| Lp | %.3f mH +/-%.0f %% | agrees (magnetics check 1.137 / 1.236 mH) |" % (rq["lp_mH"]["value"], rq["lp_mH"]["tol_pct"]))
    w("| turns ratio n / aux ratio | %.2f / %.4f (Np %d, Ns %d, Na %d) | design M1: same |" % (
        rq["turns"]["n"], rq["turns"]["aux_ratio"], NP, NS, NA))
    w("| peak current full load / highest trip | %.2f / %.2f A | - |" % (rq["ipk_full_load_A"], rq["ipk_max_A"]))
    w("| flux at the highest trip on A_min (MG-12) | <= %.0f mT (0.85 x Bs 100 C) | design M1: %.0f mT - meets, vendor to "
      "measure L(I) to 1.86 A at 120 C |" % (rq["b_max_on_Amin_mT"]["limit"], rq["b_max_on_Amin_mT"]["design_m1"]))
    w("| winding loss at 30 W (MG-11) | <= %.2f W (efficiency >= 80 %% at 400-800 V: <= %.2f W; current limit above the "
      "SYS black-start peak: <= %.2f W) | design M1: %.2f W (%s) - meets |" % (
          rq["winding_loss_max_W"]["limit"], *rq["winding_loss_max_W"]["budget"].values(),
          rq["winding_loss_max_W"]["design_m1"], rq["winding_loss_max_W"]["note"]))
    w("| switched winding capacitance | <= %.1f pF (the Asian switch's turn-on loss; was 40) | design M1: %.1f pF |" % (
        rq["switched_capacitance_max_pF"], C_W * 1e12))
    w("| leakage (S shorted) | <= %.1f uH | design M1: %.1f uH (estimate) |" % (rq["leakage_max_uH"], LLK * 1e6))
    w("| insulation (MG-13 / IC-15) | PD <= 10 pC at %.0f Vpk on every unit, %.0f V rms 60 s, %.0f V impulse, creepage / "
      "clearance as sec. 11 | design M1: levels carried, potted |" % (T1_INS["pd_v"], T1_INS["ac_v"], T1_INS["imp_v"]))
    w("")
    w("- The Asian switch changes one requirement: the switched winding capacitance budget falls from 40 pF to %.0f pF "
      "(its larger Eoss uses the turn-on loss margin). Rev C1 reads the design file `%s` (rev %s) for every T1 "
      "figure (A-33)." % (rq["switched_capacitance_max_pF"], tr["design_file"], T1_DESIGN["revision"]))
    w("")
    w("Winding specification (also the T1 description in the schematic): " + tr["desc"])
    w("")
    w("## 3. Voltage stress (rule: V_DS <= %d %% of 1700 V at 1000 V continuous, <= %d %% at 1100 V transient)" % (
        DERATE["cont"] * 100, DERATE["trans"] * 100))
    w("| node | value | limit | use |")
    w("|---|---|---|---|")
    w("| V_DS 1000 V, full load, TVS hot + VBR max + 40 V overshoot | %.0f V | %.0f V | %.1f %% |" % (VDS["cont"], DERATE["cont"] * 1700, VDS["cont"] / 17))
    w("| V_DS 1100 V, at the highest current-limit trip | %.0f V | %.0f V | %.1f %% |" % (VDS["trans"], DERATE["trans"] * 1700, VDS["trans"] / 17))
    w("| V_DS at the highest OV-lockout level (%.0f V) + 1 us surge ramp, highest trip | %.0f V | %.0f V | %.1f %% |" % (
        STR["ov"][2], OVL["vds_stop"], DERATE["trans"] * 1700, OVL["vds_stop"] / 17))
    w("| V_DS locked out at the surge peak (= HV_BULK) | %.0f V | 1700 V | %.1f %% |" % (OVL["vds_locked"], OVL["vds_locked"] / 17))
    w("| clamp (TVS stack) minimum vs VR | %.0f V vs %.0f V | > 1.4 x VR | %.2f x |" % (V_CLAMP_MIN, OPS[600.0]["vr"], V_CLAMP_MIN / OPS[600.0]["vr"]))
    w("| clamp diode BYG10Y reverse | %.0f V | 1600 V | %.0f %% |" % (V_DCLAMP, V_DCLAMP / 16))
    w("| output rectifier VS-8ETU04S (+25 %% ring) | %.0f V | 400 V | %.0f %% |" % (V_DOUT, V_DOUT / 4))
    w("| aux rectifier US1G (+25 %% ring) | %.0f V | 400 V | %.0f %% |" % (V_DAUX, V_DAUX / 4))
    w("| start-up FET BSS126 (each of 3), at the surge peak | %.0f V | 600 V | %.0f %% |" % (OVL["v_fet"], OVL["v_fet"] / 6))
    w("| HV string CRHV2512 (each of 3), at the surge peak | %.0f V | 3000 V | %.0f %% |" % (OVL["v_fet"], OVL["v_fet"] / 30))
    w("| input OR diodes BYG10Y | %.0f V | 1600 V | %.0f %% |" % (VIN_TR, VIN_TR / 16))
    w("| bulk film C4AQ, surge peak | %.0f V | VNDC 1300 V @ 70 C | %.0f %% |" % (SURGE_MAX, SURGE_MAX / 13))
    w("| AC10 input resistor, surge (each of %d) | %.0f V pulse | 4500 V (<= 0.2 ms) | %.0f %% |" % (
        N_LIM, max(x["v_r"] for x in INP["surges"]), max(x["v_r"] for x in INP["surges"]) / 45))
    w("| gate drive (VDD) | %.1f V nominal, Zener clamp 17.6-18.4 V | +12..+18 V static, +20 V transient | - |" % V_GS)
    w("")
    w("## 4. Input: fuses, current-limiting resistors, surge absorber, OV lockout (AX-01, AX-02)")
    w("- Per port: stud -> %d x %s AC10 (pulse-rated wirewound) -> 1 A / 1000 VDC gPV fuse -> BYG10Y OR -> HV_BULK; "
      "%d x 3.3 uF / 1300 V film (C4AQ) + 2 x 22 nF 2 kV. Tau %.0f us, filter corner %.0f Hz." % (
          N_LIM, ohm(R_LIM_EACH), N_BULK, INP["tau"] * 1e6, INP["corner_hz"]))
    w("- AX-02: any fault behind the resistors (shorted film, MLCC, OR diode or switch path) is limited to %.0f A, so "
      "the fuse interrupts %.0f A at 1000 VDC instead of the port's 50 kA (its 20 kA rating is no longer the limit). "
      "Clearing energy per resistor %.0f J (A-19) vs ~%.0f J AC10 single-pulse capability (p6, read off) = %.0f %%. "
      "The stud-to-resistor copper is the port conductor itself and is protected like the harness by the module's "
      "port fuse. A 22 R AC05 (rev A part) would only take ~11 J." % (
          INP["i_fault"], INP["i_fault"], INP["e_fault"], INP["e_cap"], INP["e_fault"] / INP["e_cap"] * 100))
    w("- Hot plug at 1100 V: %.0f A peak, I2t %.3f A2s = %.0f %% of the melting I2t (A-19 limit 20 %%), %.2f J per "
      "resistor. Operating loss at 200 V full load %.2f W per resistor (8.4 W rating)." % (
          INP["i_pk"], INP["i2t"], INP["i2t"] / FUSE["i2t"] * 100, INP["e_hot_r"], INP["p_r_200"]))
    w("- Surges (A-20, switch locked out):")
    w("")
    w("| residual | from | HV_BULK peak | dV/dt | peak current | per resistor | fuse I2t |")
    w("|---|---|---|---|---|---|---|")
    for x in INP["surges"]:
        w("| %s | %.0f V | %.0f V | %.1f V/us | %.0f A | %.0f V, %.1f J | %.3f A2s |" % (
            x["name"], x["v0"], x["vbulk"], x["dvdt"] * 1e-6, x["i0"], x["v_r"], x["e_r"], x["i2t"]))
    w("")
    w("- Why no clamp on HV_BULK: a TVS or MOV that must not conduct at the 1100 V transient clamps at >= 1.4-1.6 kV "
      "at tens of amps, so it cannot hold <= 1.25 kV; the RC absorber keeps HV_BULK at %.0f V (1300 V film rating, "
      "dV/dt %.0f V/us vs 80 V/us, %.0f A per film vs 264 A) and the OV lockout makes the switch see only HV_BULK. "
      "The review's slow 1.5 kV swell is beyond the port rating (SPD Ucpv 1000 V): switched off, V_DS = 1500 V (%.0f %%), "
      "cascode 500 V per FET (%.0f %%), film 1.15 x VNDC (inside the 1.5 x VNDC surge rating, max 10 events)." % (
          SURGE_MAX, max(x["dvdt"] for x in s4) * 1e-6, max(x["i_cap"] for x in s4), OVL["swell_vds"] * 100, OVL["swell_fet"] * 100))
    w("- OV lockout: compensated string (3 x %s || %s, R_D1 %s || %s, R_D2 %s || %s, under-compensated: fast-step "
      "ratio %.2f-%.2f x DC, so a surge trips earlier) -> 1k / 220 pF -> TLV3202 1IN-; 1IN+ = primary ATL431 2.5 V "
      "via 10k, 1M hysteresis from 1OUT; 1OUT low pulls COMP below the 1.15 V offset through a BAT54. Trip %.0f V "
      "(%.0f-%.0f), release %.0f V (%.0f-%.0f): above the 1100 V transient, so AUX-HV keeps regulating through it. "
      "Response %.1f us (A-23); at %.1f V/us HV_BULK is %.0f V when switching stops -> V_DS %.0f V (%.1f %% <= 90 %%)." % (
          ohm(R_STR), farad(C_STR), ohm(R_D1), farad(C_D1), ohm(R_D2), farad(C_D2), STR["k_hf"][0], STR["k_hf"][1],
          STR["ov"][1], STR["ov"][0], STR["ov"][2], STR["rel"][1], STR["rel"][0], STR["rel"][2], OVL["t_resp"] * 1e6,
          OVL["dvdt"] * 1e-6, OVL["v_stop"], OVL["vds_stop"], OVL["vds_stop"] / 17))
    w("- Input capacitors fall below 60 V %.1f s after disconnection while the start-up source cycles (calculated); "
      "through the string alone %.0f s (warning label required)." % (INP["t_bleed_su"], INP["t_bleed_string"]))
    w("")
    w("## 5. Short circuit and overload (AX-03, AX-04)")
    w("- Staircase (A-21): per cycle at the current limit the minimum on-time adds Vin x t_on / Lp, a hard short resets "
      "only n x VF = %.1f V reflected. At %.1f kHz (highest fsw) the reset loses above Vout %.1f V at 1100 V:" % (
          N * FB["v_sec"], FS_RANGE[1] / 1e3, FB["v_on"]))
    w("")
    w("| Vin | t_on min | rise per cycle | reset per cycle (fsw max) | reset per cycle (folded, max) |")
    w("|---|---|---|---|---|")
    for r in FB["rows"]:
        w("| %d V | %.2f us | +%.3f A | -%.3f A %s | -%.3f A %s |" % (r["vin"], r["ton"] * 1e6, r["up"], r["dn_n"],
                                                               "(staircase)" if r["dn_n"] < r["up"] else "",
                                                               r["dn_f"], "(staircase)" if r["dn_f"] < r["up"] else "(bounded)"))
    w("")
    w("- Foldback: AUX -> US1G -> %s -> %s || %s (tau %.1f us charge / %.0f us discharge) -> BZX84-B6V8 -> 2N7002 "
      "(Q_INV, gate %s) -> FBK (%s to VREF) -> 2N7002 (Q_FB) switches %s onto RT/CT: fsw %.1f kHz (%.1f-%.1f), "
      "staircase-free up to %.1f kHz. Engages below %.1f-%.1f V output (threshold %.1f-%.1f V on the detector, A-27) "
      "within ~%.0f us (%.1f cycles) of a hard short; holds off at light load down to ~1 W (detector %.1f V at 3 W). "
      "Clamp spike on the aux (A-22, 25/50/75 %%) lifts the detector only to %.1f / %.1f / %.1f V. Folded power "
      "%.1f-%.1f W magnetising." % (
          ohm(R_AF1), farad(C_AF), ohm(R_AF2), R_AF1 * R_AF2 / (R_AF1 + R_AF2) * C_AF * 1e6, R_AF2 * C_AF * 1e6,
          ohm(R_QG), ohm(R_FBK), farad(C_TX), F_FOLD / 1e3, F_FOLD_RANGE[0] / 1e3, F_FOLD_RANGE[1] / 1e3,
          FB["f_safe"] / 1e3, FB["v_fold"][0], FB["v_fold"][1], FB["th"][0], FB["th"][1], FB["t_eng"] * 1e6,
          FB["cycles_eng"], [x for x in FB["light"] if x["p"] == 3.0][0]["af1"], *[FB["spike"][f] for f in SPIKE_FRAC],
          FB["p_fold"][0], FB["p_fold"][1]))
    w("- In a sustained short the aux cannot hold VDD (folded spikes no longer pump VAUX), VDD runs down in %.0f ms "
      "and the start-up recharges it in %.2f-%.2f s: hiccup with %.0f-%.0f %% on-share; rectifier %.1f W during the "
      "on-time (hard short, folded, high corner), %.2f W average -> %.0f C." % (
          SU["t_hold"] * 1e3, SU["t_recharge"][0], SU["t_recharge"][1], SU["t_hold"] / (SU["t_hold"] + SU["t_recharge"][1]) * 100,
          SU["t_hold"] / (SU["t_hold"] + SU["t_recharge"][0]) * 100, FB["p_d_sc"],
          FB["p_d_sc"] * SU["t_hold"] / (SU["t_hold"] + SU["t_recharge"][0]),
          T_AMB + FB["p_d_sc"] * SU["t_hold"] / (SU["t_hold"] + SU["t_recharge"][0]) * 35 + FB["p_d_sc"] * DOUT["rth_jc"]))
    w("- Overload timer: COMP -> %s -> COMPF (%s || %s to PGND) -> TLV3202 2IN+, 2IN- = 2.5 V; 2OUT latches through a "
      "BAS16 (low leakage: %.1f / %.1f %% threshold error at 85 / 125 C, datasheet maxima; a BAT54 would leak ~20 uA "
      "at 85 C here) and crowbars SS "
      "with a 2N7002 until VDD reaches UVLO (latch reset), then the start-up source restarts it. "
      "COMP at 30 W in the worst corner (Lp/fs min, Acs max, offset +0.1 V, A-24) is %.2f V; trip at %.2f / %.2f / "
      "%.2f V COMP = %.0f / %.0f / %.0f W output (lowest / typical / highest corner). In current limit COMP sits at "
      "%.2f-%.2f V and the timer trips after %.0f / %.0f / %.0f ms; hiccup off-time >= %.2f s." % (
          ohm(R_TA), ohm(R_TB), farad(C_TMR), OVLD["leak_err"][0] * 100, OVLD["leak_err"][1] * 100,
          OVLD["comp_rated"], *OVLD["comp_trip"],
          OVLD["trip"]["min"]["pout"], OVLD["trip"]["typ"]["pout"], OVLD["trip"]["max"]["pout"],
          *OVLD["comp_max"], *[x * 1e3 for x in OVLD["t_trip"]], SU["t_recharge"][0]))
    w("- **Overload limit (state for SYS-IO-AUX): continuous load <= 30 W. The supply never trips below %.0f W; above "
      "%.0f-%.0f W (corner-dependent) or in current limit it delivers for %.0f-%.0f ms and then hiccups.**" % (
          OVLD["trip"]["min"]["pout"], OVLD["trip"]["min"]["pout"], OVLD["trip"]["max"]["pout"],
          OVLD["t_trip"][0] * 1e3, OVLD["t_trip"][2] * 1e3))
    w("")
    w("Temperatures at the limit (T_amb %.0f C, A-9, A-26):" % T_AMB)
    w("")
    w("| case | switch (%s unless named) | VS-8ETU04S | SMCJ70A (each) | T1 |" % SW_DESIGN)
    w("|---|---|---|---|---|")
    for case, rows in OVT.items():
        w("| %s | " % case + " | ".join("%.0f C (%.2f W)%s" % (r["t"], r["p"], " **over %d C**" % r["lim"] if r["t"] > r["lim"] else "")
                                      for r in rows) + " |")
    w("")
    w("- Rated 30 W (also with the A-1 worst leakage), the typical-corner trip level and hiccup at the highest limit "
      "stay inside every rating. Continuous load just below the trip in the high-tolerance corner (%.0f W) does not: "
      "the COMP level bounds the peak current to only ~+/-10 %% (offset, gain), so between 30 W and that level AUX-HV "
      "relies on the 30 W load contract (D-022)." % OVLD["trip"]["max"]["pout"])
    w("")
    w("## 6. Start-up, SYS-IO-AUX rev E feed, black start, hand-over (D-034)")
    w("- Start-up current (3 x BSS126 cascode, bottom FET self-biased by %s): %.2f-%.2f mA (A-13). VDD %.0f uF "
      "effective. First start %.1f s (typ) / %.1f s (worst, follower BAT54 leaking 42 uA at 85 C) after the input "
      "passes the brown-in level. At 125 C that BAT54 would leak ~330 uA, more than the lowest start-up current: "
      "LAYOUT REQUIREMENT - keep the VDD follower (BC817, BAT54) away from T1, the SiC switch and the clamp, local "
      "<= 85 C (A-28). VDD hold-up %.1f ms with the %.1f nC gate charge of %s (design for the worse)." % (
          ohm(RS_START), SU["i_min"] * 1e3, SU["i_max"] * 1e3, SU["c_vdd"] * 1e6, SU["t_start"][1], SU["t_start"][0],
          SU["t_hold"] * 1e3, QG_MAX * 1e9, max(DEVICES, key=lambda k: DEVICES[k]["qg"])))
    w("- Model: AUX-HV averaged (loop, soft start, current limit, foldback, overload timer, VDD) + the rev E feed as "
      "SYS models it (`%s`): UVLO %.2f-%.2f V rising / %.2f-%.2f V falling, latched OV %.2f V, HGATE ramp %.0f-%.0f "
      "V/s into %.2f-%.2f mF, source follower %.1f V below the gate (A-25); post-inhibit load A-17. Corners: 'min' "
      "= least AUX-HV power, slowest soft start, earliest timer, fastest ramp into the largest bus; 'max' = the "
      "opposite with the slowest ramp into the smallest bus." % (
          os.path.relpath(FEED_JSON, REPO), *SYS["feed_uvlo"], *SYS["feed_uvlof"], SYS["feed_ov"][0], SYS["ramp"][0],
          SYS["ramp"][1], SYS["c_bus_range"][0] * 1e3, SYS["c_bus_range"][1] * 1e3, SYS["vgs_on"]))
    w("")
    w("| case | corner | aux takes VDD | output max / min | bus min | AUX-HV peak | time > %.0f W | timer node peak (trip %.2f-%.2f V) | settled | result |" % (
        OVLD["trip"]["min"]["pout"], *OVLD["v_trip"]))
    w("|---|---|---|---|---|---|---|---|---|---|")
    names = {"alone": "start, cabinet present (feed held off)", "black": "black start, %.1f W" % SYS["p_total"],
             "black07": "black start, port 0.7 A (%.1f W)" % SYS["p_hold2"],
             "handover": "cabinet hand-over, %.1f W" % SYS["p_total"],
             "handover07": "hand-over, port 0.7 A (%.1f W)" % SYS["p_hold2"]}
    for key in (("alone", "min"), ("alone", "typ"), ("alone", "max"), ("black", "min"), ("black", "typ"), ("black", "max"),
                ("black07", "min"), ("handover", "min"), ("handover", "typ"), ("handover", "max"), ("handover07", "min")):
        r = SIM[key]
        ok = not r["ov_latched"] and not r["uvlo"] and not r["trip"]
        w("| %s | %s | %s | %.2f / %s V | %s | %.1f W | %.0f ms | %.2f V | %s | %s |" % (
            names[key[0]], key[1], "%.1f ms at %.1f V" % (r["t_take"] * 1e3, r["v_take"]) if key[0] in ("alone", "black", "black07") else "-",
            r["vmax"], "%.2f" % r["vmin_after"] if r["vmin_after"] < 90 else "-",
            "%.2f V" % r["bus_min"] if r["bus_min"] < 90 else "-", r["p_peak"], r["t_over"] * 1e3, r["compf_max"],
            "%.2f s" % r["t_settle"] if r["t_settle"] is not None else "no",
            "ok" if ok else ("feed OV latched" if r["ov_latched"] else "hiccup")))
    w("")
    sb, sh = SYS["sys_black"], SYS["sys_hand"]
    w("- SYS-IO-AUX's own numbers (rev E json): black start %.1f-%.1f W peak, %.0f-%.0f ms above 34 W, ramp %.2f-%.2f s; "
      "hand-over bus minimum %.2f-%.2f V, %.1f-%.1f W peak, conduction after %.1f-%.1f ms. This model: black start "
      "%.1f W peak (min corner) for %.0f ms above the lowest timer threshold %.1f W, the timer node reaches %.2f V "
      "against its lowest trip level %.2f V - no trip, %.0f %% margin; hand-over bus minimum %.2f V, AUX-HV output "
      "never below %.2f V (feed UVLO falling max %.2f V). **Confirmed**: the SYS figures hold; the differences "
      "(~0.7 W lower peak) come from the AUX-HV output voltage (22.77 V here, 23.15 V assumed there)." % (
          sb["slow"]["p_peak"], sb["fast"]["p_peak"], sb["slow"]["t_over34"] * 1e3, sb["fast"]["t_over34"] * 1e3,
          sb["fast"]["t_ramp"], sb["slow"]["t_ramp"], sh["slow"]["vmin"], sh["fast"]["vmin"], sh["slow"]["p_peak"],
          sh["fast"]["p_peak"], sh["slow"]["t_cond"] * 1e3, sh["fast"]["t_cond"] * 1e3, SIM[("black", "min")]["p_peak"],
          SIM[("black", "min")]["t_over"] * 1e3, OVLD["trip"]["min"]["pout"], SIM[("black", "min")]["compf_max"],
          OVLD["v_trip"][0], (1 - SIM[("black", "min")]["compf_max"] / OVLD["v_trip"][0]) * 100,
          min(SIM[(k, c)]["bus_min"] for k in ("handover", "handover07") for c in ("min", "typ", "max") if (k, c) in SIM),
          min(SIM[(k, c)]["vmin_after"] for k in ("handover", "handover07") for c in ("min", "typ", "max") if (k, c) in SIM),
          SYS["feed_uvlof"][1]))
    w("- **Rating: 30 W continuous at 60 C ambient stays the right rating.** The worst post-inhibit load is %.1f W "
      "(%.1f W of it only while a port holds its contactors, <= %d s; %.1f W otherwise) and %.2f W for <= %d s with "
      "two battery-port hold coils - thermally the same point as 30 W (sec. 5: the switch loss does not depend on "
      "the load in fixed-frequency DCM). The black-start / hand-over peaks (<= %.1f W, above 35 W for <= %.0f ms) "
      "do not trip the overload timer (node <= %.2f V vs %.2f V). State it to SYS-IO-AUX as: 30 W continuous, %.0f W "
      "(lowest current limit) for <= %.0f ms, the timer ends anything longer." % (
          SYS["p_total"], SYS["i_port"] * 23.15, SYS["t_port"], SYS["p_noport"], SYS["p_hold2"], SYS["t_port"],
          max(SIM[k]["p_peak"] for k in SIM if k[0] not in ("alone", "black07")),
          max(SIM[k]["t_over"] for k in SIM if k[0] not in ("alone", "black07")) * 1e3,
          max(SIM[k]["compf_max"] for k in SIM if k[0] != "black07"), OVLD["v_trip"][0],
          math.floor(ILIM["p_min"]), OVLD["t_trip"][0] * 1e3))
    w("- The timer margin is thin in the all-tolerances-low corner: the %.2f W hand-over peaks the timer node at "
      "%.0f %% of its lowest trip level, and a black start with that port load (not possible: contactors open) "
      "hiccups. Continuous load above %.1f W is ended by the timer - SYS-IO-AUX's hardware inhibit (D-034) is what "
      "keeps the load below 30 W." % (
          SYS["p_hold2"], SIM[("handover07", "min")]["compf_max"] / OVLD["v_trip"][0] * 100,
          OVLD["trip"]["min"]["pout"]))
    w("- Light load (feed held off, only the SYS feed parts on the output): AUX-HV skips cycles at high line; the "
      "foldback may engage between bursts (harmless). No-load and standby input power: sec. 9.")
    w("- PG_HVAUX dips low for the few ms of a hand-over sag below 21.3 V: firmware should qualify it over >= 50 ms.")
    w("")
    w("## 7. Output set point, regulation, backup band (INT-08) and PG (INT-13)")
    w("- ATL431BI + %s / %s (0.1 %%): %.3f V nominal, %.2f-%.2f V worst case (spec +/-3 %% = %.2f-%.2f V); inside "
      "SYS window 21.6-26.0 V, above the feed UVLO (max %.2f V) by %.2f V, below the latched feed OV (min %.2f V) by "
      "%.2f V." % (ohm(R_UP), ohm(R_LO_SEL), VOUT_SET, VOUT_LO, VOUT_HI, VOUT * (1 - REG_TOL), VOUT * (1 + REG_TOL),
                   SYS["feed_uvlo"][1], VOUT_LO - SYS["feed_uvlo"][1], SYS["feed_ov"][0], SYS["feed_ov"][0] - VOUT_HI))
    sb = [s for s in STEP if s["bus"]]
    sa = [s for s in STEP if not s["bus"]]
    w("- Full 0 -> 30 W step (CTR low/typ/high): minimum %.2f/%.2f/%.2f V with the 2 mF bus, %.2f/%.2f/%.2f V "
      "stand-alone; overshoot on release <= %.2f V (below the latched feed OV %.2f V)." % (
          *[s["vmin"] for s in sb], *[s["vmin"] for s in sa], max(s["vmax"] for s in STEP), SYS["feed_ov"][0]))
    w("- Backup loop (opto failed): aux sense US1G + %s / %s into %s / %s (0.1 %%). Tracking budget calculated over load "
      "0.35-30 W, rectifier 25-125 C, clamp spike 25-75 %% (A-22), US1G 0.55-0.80 V and VFB 2.45-2.55 V: "
      "**%.2f-%.2f V**; %.2f-%.2f V while loaded >= 13 W through the feed, i.e. below the 26.13 V bus cut-off "
      "whenever AUX-HV carries the module (nominal VFB: %.2f-%.2f V). Load is the "
      "dominant term (the peak detector droops at light load, the rectifier VF rises at full load: +/-6 %%), then VFB "
      "+/-2 %% - aux sensing cannot do better. The low end stays %.2f V above the regulation band. Idling unloaded "
      "(feed held off) the top reaches the value above, but the latched SYS feed OV (23.97-25.57 V) refuses such a "
      "feed, so the bus cut-off can no longer be reached through AUX-HV and the cabinet feeds carry on (INT-08); PG "
      "flags OV above %.2f V." % (ohm(R_AUXS), farad(C_AUXS), ohm(R_FBT), ohm(R_FBB), *BACKUP_BAND, *BK["loaded"], *BK["nom"],
                            BK["fb_normal_margin"], PG["ov_rise"][0]))
    w("- PG_HVAUX (INT-13): TPS3700 OUTA/OUTB (wired) pull PGW low outside %.2f-%.2f V (UV falling) / %.2f-%.2f V (OV); "
      "PGW has %s to a 3.3 V rail (%s + BZX84-B3V3 from the output, %.2f-%.2f V); SN74LVC1G17 (Schmitt, Ioff) drives "
      "PG_HVAUX through %s: high %.2f-%.2f V into the SYS 100k pull-down. AUX-HV dead or the cable unplugged: the "
      "buffer is unpowered (Ioff) and SYS reads 0 V; output below ~%.1f V: buffer supply below its 1.65 V minimum, "
      "the TPS3700 holds PGW low as soon as it runs (1.8 V)." % (
          *PG["uv_fall"], *PG["ov_rise"], ohm(R_PGW), ohm(R_PGR), *PGD["rail"], ohm(R_PGO), *PGD["v_high"], PGD["vout_on"]))
    w("")
    w("## 8. Control loop (A-7, A-8)")
    good = [r for r in LOOP if r["pm"] is not None]
    pm_min = min(r["pm"] for r in good)
    worst = min(good, key=lambda r: r["pm"])
    w("- Fast-lane type II + feed-forward: K = CTR x R_PU / R_LED (%s / %s), integrator zero %.0f Hz (C_Z %s), "
      "feed-forward C_FF/C_Z = %.1f rolled off at %.0f Hz, COMP pole %.1f kHz, opto pole %.1f kHz (pessimistic, A-7)." % (
          ohm(R_PU), ohm(OPT["r_led"]), 1 / (2 * math.pi * R_UP * C_Z), farad(C_Z), C_FF / C_Z, 1 / (2 * math.pi * R_FF * C_FF),
          1 / (2 * math.pi * R_PU * C_P) / 1e3, OPTO["fc_100r"] * 100 / R_PU / 1e3))
    w("- %d corners (Vin 200/600/1000 V, 100 %%/43 %% (13 W)/10 %% load, resistive/constant-power, CTR low/typ/high, opto "
      "pole pessimistic/typical, with/without the 2 mF bus): crossover %.0f-%.0f Hz, phase margin >= %.0f deg (worst: "
      "%g V, %d %% %s load, CTR %s, opto %s), gain margin >= %.0f dB." % (
          len(good), min(r["fc"] for r in good), max(r["fc"] for r in good), pm_min, worst["vin"], worst["load"] * 100,
          worst["kind"], worst["ctr"], worst["fopto"], min(r["gm"] for r in good)))
    w("- Backup (aux) loop with %s + %s / %s on COMP-FB: crossover %.0f-%.0f Hz, phase margin >= %.0f deg." % (
        ohm(R_C_EA), farad(C_C_EA), farad(C_HF_EA), min(b["fc"] for b in BACKUP), max(b["fc"] for b in BACKUP), min(b["pm"] for b in BACKUP)))
    w("- Optocoupler DC point: LED %.1f mA typ, up to %.1f mA (R_LED <= %.2fk), ATL431 >= %.2f mA, cathode >= %.1f V." % (
        OPT["if_typ"] * 1e3, OPT["if_max"] * 1e3, OPT["r_led_max"] / 1e3, OPT["i_atl_min"] * 1e3, OPT["vka_min"]))
    w("")
    w("## 9. Losses and efficiency at 30 W out (target >= 80 %% at 400-800 V), design switch %s" % SW_DESIGN)
    for r in fmt_loss_table():
        w(r)
    w("")
    w("**Switch comparison (both Asian candidates, same circuit; the design takes the worse of each figure):**")
    w("")
    w("| | %s (%s) | %s (%s) |" % (SW_MAIN, DEVICES[SW_MAIN]["maker"], SW_ALT, DEVICES[SW_ALT]["maker"]))
    w("|---|---|---|")
    rows = [("qualification", lambda k, d: d["qual"]),
            ("R_DS(on) @15 V: 25 C typ / max, 175 C typ", lambda k, d: "%.2f / %.2f / %.2f ohm" % (d["rds25"][0], d["rds25"][1], d["rds175_typ"])),
            ("Eoss at 1000 V / Coss / Crss", lambda k, d: "%.1f uJ / %.1f pF / %.1f pF" % (eoss(1000.0, d) * 1e6, d["co_tr"] * 1e12, d["crss"] * 1e12)),
            ("Qg / Rg,int", lambda k, d: "%.1f nC / %.0f R" % (d["qg"] * 1e9, d["rg_int"])),
            ("VTH min 25 C / min hot (A-30)", lambda k, d: "%.2f / %.2f V" % (d["vth"][0], d["vth_min_hot"])),
            ("switch loss 600 V / 1000 V / 1100 V", lambda k, d: "%.2f / %.2f / %.2f W" % tuple(
                sum(SWCMP[k][v]["loss"][n] for n in ("switch conduction", "switch turn-on (C_oss + winding C)", "switch turn-off"))
                for v in (600.0, VIN_MAX, VIN_TR))),
            ("of which Miller (0 V off, A-31) at 1000 V, full load / max limit", lambda k, d: "%.2f / %.2f uJ (VG %.2f / %.2f V)" % (
                switch_loss(VIN_MAX, OPS[VIN_MAX]["vr"], OPS[VIN_MAX]["ipk"], 0, 1, d)["e_mil"] * 1e6,
                switch_loss(VIN_MAX, OPS[VIN_MAX]["vr"], ILIM["max_eff"], 0, 1, d)["e_mil"] * 1e6,
                switch_loss(VIN_MAX, OPS[VIN_MAX]["vr"], OPS[VIN_MAX]["ipk"], 0, 1, d)["vg_ind"],
                switch_loss(VIN_MAX, OPS[VIN_MAX]["vr"], ILIM["max_eff"], 0, 1, d)["vg_ind"])),
            ("Tj at 1000 V, 60 C (35 K/W board + RthJC)", lambda k, d: "%.0f C" % (T_AMB + SW_PWR[k] * (35.0 + d["rth_jc"]))),
            ("efficiency 400 / 600 / 800 / 1000 V", lambda k, d: " / ".join("%.1f %%" % (SWCMP[k][v]["eta"] * 100) for v in (400.0, 600.0, 800.0, VIN_MAX))),
            ("gate drive VDD %.1f-%.1f V vs recommended on" % (VDD_REG[0], VDD_REG[2]), lambda k, d: "%.0f-%.0f V, abs %+.0f..%+.0f V" % (*d["vgs_rec"], *d["vgs_abs"])),
            ("pulsed drain current vs highest limit %.2f A" % ILIM["max_eff"], lambda k, d: "%.1f A" % d["idm"])]
    for name, f in rows:
        w("| %s | %s | %s |" % (name, f(SW_MAIN, DEVICES[SW_MAIN]), f(SW_ALT, DEVICES[SW_ALT])))
    w("")
    w("- Both fit: V_DS is set by the clamp (sec. 3, unchanged, both are 1700 V parts), the pin-out is the same "
      "(TO-263-7: 1 gate, 2 Kelvin source, 3-7 source, tab drain), the 15.5-18.1 V gate drive (BZX84-A18 follower, "
      "rev C) is inside both recommended on-voltage windows. Neither maker's recommended negative off-voltage is "
      "available from the UCC28C59 single-rail output: the 0 V off is checked as the Miller term (A-31) - a single-"
      "switch flyback has no second switch whose dv/dt could turn it on, and the self-induced gate voltage only adds "
      "a small channel current during the drain rise. %s runs hotter (larger Eoss, the datasheet EOFF residual, "
      "lower threshold), so it sets the design figures; %s is the cooler alternate but its datasheet states no "
      "qualification (SRC-2 needs the maker's reliability report before it is used)." % (SW_MAIN, SW_ALT))
    w("")
    w("**No-load and standby input power** (TI reference review: PMP41009 measured 1.3 W no-load at 1000 V, 0.3 W at "
      "350 V; PMP41031 0.78 W at 1000 V - both quasi-resonant):")
    w("")
    w("| Vin | no load (feed held off: SYS feed parts %.2f W) | standby %.1f W (post-inhibit, no port hold) |" % (
        SYS["p_feed"], SYS["p_noport"]))
    w("|---|---|---|")
    for v in VIN_TABLE:
        nl, sb = STBY[v]["noload"], STBY[v]["standby"]
        w("| %d V | %.2f W (%s) | %.1f W in, %.1f %% |" % (v, nl["p_in"], nl["mode"], sb["p_in"], SYS["p_noport"] / sb["p_in"] * 100))
    w("")
    w("- Night drain of a port-B battery feeding an idle AUX-HV (D-021/D-022): %.1f-%.1f W, i.e. %.0f-%.0f Wh per day; "
      "the 1700 V switch's turn-on loss (Eoss + winding capacitance at Vin) is most of it at high line." % (
          min(STBY[v]["noload"]["p_in"] for v in VIN_TABLE), max(STBY[v]["noload"]["p_in"] for v in VIN_TABLE),
          24 * min(STBY[v]["noload"]["p_in"] for v in VIN_TABLE), 24 * max(STBY[v]["noload"]["p_in"] for v in VIN_TABLE)))
    w("")
    w("## 10. ngspice (`sim/spice/aux_hv_*.cir`; behavioural RT/CT oscillator, T flip-flop, PWM latch; linear core)")
    w("| Vin | fsw calc / sim kHz | Ipk calc / sim A | V_DS pk calc / sim V | Vout calc / sim V | ripple sim mV | eff calc / sim % |")
    w("|---|---|---|---|---|---|---|")
    for c in comp:
        w("| %d V | %.1f / %.1f | %.3f / %.3f | %.0f / %.0f | %.3f / %.3f | %.0f | %.1f / %.1f |" % (
            c["vin"], FS_NOM / 1e3, c["fsw_s"] / 1e3, c["ipk_c"], c["ipk_s"], c["vds_c"], c["vds_s"], c["vo_c"], c["vo_s"],
            c["rip_s"] * 1e3, c["eta_c"] * 100, c["eta_s"] * 100))
    w("Tolerances asserted: fsw +/-3 %, Ipk +/-10 %, V_DS +/-5 %, Vout +/-1 %; foldback node FBK stays low at full load.")
    w("")
    a, b, sg = spice["short_nofold"], spice["short"], spice["surge"]
    w("**Hard output short at 1000 V** (30 mOhm + 20 mOhm switch at 0.3 ms, from full load; `aux_hv_1000V_short*.cir`, "
      "`spice_short.png`):")
    w("")
    w("| | peak primary current | V_DS peak | last 0.3-0.8 ms: Ipk / fsw |")
    w("|---|---|---|---|")
    w("| foldback disabled | %.2f A (climbs cycle by cycle past the limit; settles only where the model's diode "
      "drop resets it - a real core saturates first) | %.0f V | %.2f A / %.1f kHz |" % (
        a["ipk_all"], a["vds_all"], a["ipk_max"], 1 / a["tper"] / 1e3))
    w("| with foldback | %.2f A (first %.1f ms: %.2f A) | %.0f V | %.2f A / %.1f kHz |" % (
        b["ipk_all"], 0.3, b["ipk_early"], b["vds_all"], b["ipk_max"], 1 / b["tper"] / 1e3))
    w("")
    w("**Surge at 1000 V** (4 kV / 20 us at the port, `aux_hv_1000V_surge.cir`, `spice_surge.png`): HV_BULK peak "
      "%.0f V (calc %.0f V), lockout at %.1f us after the surge start, V_DS peak %.0f V (%.1f %%), output dip to %.2f V." % (
          sg["bulk_max"], SURGE_MAX, (sg.get("t_lock", 0) - 0.3e-3) * 1e6, sg["vds_all"], sg["vds_all"] / 17, sg["out_min"]))
    w("")
    w("## 11. Insulation requirement for the layout (not PCB dimensions)")
    for s in INSULATION:
        w("- " + s)
    w("- **Conformal coating (IC-17, D-032): the board is conformally coated to pollution degree 1 (IEC 60664-3 "
      "type 1) over the HV-PELV barrier parts - T1, the CNY65B and their HV copper.** Coating does not reduce "
      "clearance.")
    w("- **T1 (IC-15):** %s" % T1_CHECK)
    w("")
    w("## 12. Assumptions (ours)")
    for k, v in A.items():
        w("- **%s** %s" % (k, v))
    w("")
    w("## 13. Not verified / open")
    w("- Leakage, winding capacitance, the aux tracking and the clamp spike on the aux (A-22) are estimates: build a "
      "T1 sample (design M1), measure Lp, Llk, Cpri, PD and the aux waveform; check the foldback detector and the "
      "backup band (%.1f-%.1f V, driven by the leakage) against the real spike. Own and OpenMagnetics copper loss "
      "differ by x%.1f and leakage by x%.0f - the sample decides." % (BACKUP_BAND[0], BACKUP_BAND[1], T1_K_OM,
                                                                     T1_EL["L_leak_est_H"] / _T1_VER["openmagnetics"]["L_leak_H"]))
    w("- %s: %.0f C junction at 1000 V / 60 C with the A-9 board estimate (35 K/W): little margin to our 150 C "
      "limit - confirm the copper area in layout; %s's datasheet states no qualification." % (
          SW_DESIGN, T_AMB + SW_PWR[SW_DESIGN] * (35.0 + MOS["rth_jc"]), SW_ALT))
    w("- The SiC threshold at temperature (A-30), the datasheet EOFF interpretation (A-29) and the 0 V turn-off "
      "Miller term (A-31) are estimates from the makers' curves; measure the turn-off on a bench at 1000 V hot.")
    w("- Short at 1100 V on a T1 sample (saturation is not in the ngspice model): the first %.1f cycles before the "
      "foldback engages still climb (sec. 10)." % FB["cycles_eng"])
    w("- Continuous load between 30 W and the timer threshold is not prevented by AUX-HV in every corner (the COMP "
      "level only bounds the peak current to ~+/-10 %); SYS-IO-AUX's hardware inhibit (D-034) holds it <= 30.2 W.")
    w("- CNY65 local ambient must stay <= 85 C (its rating); the module ambient is 60 C (PV-20).")
    w("- DAB-D60 build: port B is not fitted, so the port-to-port isolation is untouched; the DAB cannot black-start "
      "from port 2 (battery) without cabinet 24 V (accepted in D-021).")
    w("- The EA source current maximum is not specified (A-8); the opto is sized for 2 mA.")
    w("")
    w("## 14. Sourcing (D-031, SRC-1..4)")
    w("")
    w("| part | old | new (rev C) | why / why not | price (gen/data/prices.csv, 1 pc) |")
    w("|---|---|---|---|---|")
    for part, old, new, why in SOURCING:
        w("| %s | %s | %s | %s | %s |" % (part, old, new, why, price_of(old.split(" ")[-1]) if new.startswith("keep") else
                                         "%s (old) / RFQ (new)" % price_of(old.split(" ")[-1])))
    w("")
    with open(os.path.join(OUT, "report.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


# ============================================================================================ main + self-check
def sim_summary():
    over = max(r["vmax"] for k, r in SIM.items() if k[0] in ("alone", "black", "handover", "black30"))
    over = max(over, max(s["vmax"] for s in STEP if s["bus"]))       # load release while feeding the bus
    bus_min = min(SIM[("handover", c)]["bus_min"] for c in ("min", "typ", "max"))
    return dict(vmax=over, bus_min=bus_min)


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(SPICE_DIR, exist_ok=True)
    spice, comp = {}, []
    for vin in (200.0, 600.0, 1000.0):
        m = run_spice(vin)
        spice[vin] = m
        op = OPS[vin]
        comp.append(dict(vin=vin, ipk_c=op["ipk"], ipk_s=m["ipk_max"], vds_c=vin + clamp_v(op["ipk"]), vds_s=m["vds_max"],
                         vo_c=VOUT_SET, vo_s=m["vout_avg"], rip_s=m["vout_max"] - m["vout_min"], eta_c=op["eta"],
                         eta_s=m["pout_avg"] / m["pin_avg"], fsw_s=1 / m["tper"], fbk=m["fbk_max"]))
        print("ngspice %4d V: fsw %.1f/%.1f kHz  Ipk %.3f/%.3f A  Vds %.0f/%.0f V  Vout %.3f/%.3f V  ripple %.0f mV  "
              "eta %.1f/%.1f %%" % (vin, FS_NOM / 1e3, 1e-3 / m["tper"], op["ipk"], m["ipk_max"], vin + clamp_v(op["ipk"]),
                                    m["vds_max"], VOUT_SET, m["vout_avg"], (m["vout_max"] - m["vout_min"]) * 1e3,
                                    op["eta"] * 100, m["pout_avg"] / m["pin_avg"] * 100))
    for mode in ("short_nofold", "short", "surge"):
        spice[mode] = run_spice(1000.0, mode)
        print("ngspice 1000 V %s: %s" % (mode, ", ".join("%s %.4g" % kv for kv in sorted(spice[mode].items()))))
    sc = sim_summary()
    plots(spice)
    spec = spec_json(sc)
    spec["spice"] = dict(normal=comp, short_nofold=spice["short_nofold"], short=spice["short"], surge=spice["surge"])
    spec["system_model"] = {"%s/%s" % k: {kk: vv for kk, vv in v.items() if kk != "trace"} for k, v in SIM.items()}
    with open(os.path.join(OUT, "aux_hv_spec.json"), "w") as fh:
        json.dump(spec, fh, indent=2)
    report(spice, comp, sc)
    r75 = aux75()                                            # second case: the 75 W module supply (D-044)
    s75 = a75_spec(r75, spec["values"])
    with open(os.path.join(OUT, "aux75_spec.json"), "w") as fh:
        json.dump(s75, fh, indent=2)
    with open(os.path.join(OUT, "report.md"), "a") as fh:
        fh.write("\n".join(a75_report(r75, s75)) + "\n")
    for v in VIN_TABLE:
        print("%5d V: Ipk %.3f A  D %.3f  D2 %.3f  eta %.1f %%  loss %.2f W" % (
            v, OPS[v]["ipk"], OPS[v]["d"], OPS[v]["d2"], OPS[v]["eta"] * 100, OPS[v]["p_in"] - POUT))
    selfcheck(comp, spice, sc)
    a75_selfcheck(r75)
    print("AUX75: %s %% at 200 (gates off) / 250-1000 V, SELV %.1f-%.1f V (gates off %.1f-%.1f V)" % (
        " / ".join("%.1f" % (r75["ops"][v]["eta"] * 100) for v in r75["vt"]), *r75["xreg"]["selv"], *r75["xreg"]["gates_off"]))
    print("aux_hv_design: ALL SELF-CHECKS PASSED (30 W case and AUX75) -> %s" % os.path.relpath(OUT, REPO))


def selfcheck(comp, spice, sc):
    for v in VIN_TABLE + [VIN_TR]:
        op = OPS[v]
        # energy balance 1: input = output + every loss (bookkeeping closes)
        assert abs(op["p_in"] - POUT - sum(op["loss"].values())) < 1e-9, v
        # energy balance 2: magnetising energy per cycle = what the secondary side, aux and clamp take from it
        e_mag = 0.5 * LP * op["ipk"] ** 2 * FS_NOM
        assert abs(e_mag / op["p_mag"] - 1) < 1e-6, ("magnetising energy", v)
        # charge balance: rectifier average current = load + secondary bias (independent of the energy route)
        is_avg = 0.5 * op["is_pk"] * op["d2"]
        assert abs(is_avg / op["io"] - 1) < 0.03, ("charge balance", v, is_avg, op["io"])
        assert op["dcm"] and op["d"] < UCC["dmax"][0], ("DCM / duty", v)
    if T1_DESIGN is None:       # interim winding loss (A-32): the 80 % target is a requirement on the T1 construction
        assert T1_CU_MAX >= 1.2 and T1_CU_FL > T1_CU_MAX, ("T1 winding-loss budget", T1_CU_MAX)
    else:                       # rev C1: the design M1 transformer against the requirements handed to it
        for v in (400.0, 600.0, 800.0):
            assert OPS[v]["eta"] >= ETA_MIN, ("efficiency target", v)
        assert max(T1_CU_V) <= T1_CU_MAX and C_W <= C_W_MAX, ("T1 winding loss / switched capacitance", C_W)
        assert LLK <= T1_EL["L_leak_max_H"] * 1.001 and B_LIM_AMIN <= T1_EL["B_limit_rule_T"], "T1 leakage / flux"
    assert VDS["cont"] <= DERATE["cont"] * MOS["vbr"], "V_DS at 1000 V above 80 %"
    assert VDS["trans"] <= DERATE["trans"] * MOS["vbr"], "V_DS at 1100 V above 90 %"
    assert V_CLAMP_MIN >= 1.4 * OPS[600.0]["vr"], "clamp too close to the reflected voltage"
    assert V_DOUT <= 0.8 * DOUT["vrrm"] and V_DAUX <= 0.8 * US1G["vrrm"] and V_DCLAMP <= 0.9 * BYG["vrrm"], "rectifier stress"
    assert SU["v_fet"] <= 0.7 * BSS["vds"] and VIN_TR / 3 <= 0.5 * CRHV["vmax"], "start-up string stress"
    assert B_LIM <= B_LIMIT and B_LIM_AMIN <= B_LIMIT_AMIN, "flux at the highest current-limit trip (A_e, A_min)"
    assert ILIM["p_min"] >= P_DESIGN, "current limit cannot deliver 33 W at worst-case tolerances"
    assert T1_DESIGN["fit"]["fits"] and STACK_H <= T1_DESIGN["fit"]["available_m"], "winding stack vs coil former"
    assert UCC["vddoff"][2] + 1.0 <= VDD_REG[0] and VDD_REG[2] <= VDD_CLAMP[0] - 0.5, "VDD window vs UVLO / clamp"
    for k, d in DEVICES.items():                                          # gate drive inside both makers' windows
        assert d["vgs_rec"][0] - 0.05 <= VDD_REG[0] and VDD_REG[2] <= d["vgs_rec"][1] + 0.15, ("gate drive", k, VDD_REG)
        assert VDD_CLAMP[1] <= d["vgs_abs"][1], ("VDD clamp above the gate maximum", k, VDD_CLAMP)
        assert T_AMB + SW_PWR[k] * (35.0 + d["rth_jc"]) <= 150.0, ("switch junction at 1000 V", k)
        assert d["idm"] >= 1.5 * ILIM["max_eff"] and d["vbr"] >= MOS["vbr"], ("switch rating", k)
        assert T1_DESIGN is None or all(SWCMP[k][v]["eta"] >= ETA_MIN for v in (400.0, 600.0, 800.0)), k
    assert V_AUX_NOM - VDD_REG[2] >= 2.0 and V_AUX_NOM + 4.0 <= BC817["vceo"], "follower headroom"
    assert LINE["on"][2] <= VIN_START_MAX and LINE["off"][2] < VIN_MIN, "brown-in / brown-out window"
    assert VOUT * (1 - REG_TOL) <= VOUT_LO and VOUT_HI <= VOUT * (1 + REG_TOL), "set-point tolerance outside +/-3 %"
    assert VOUT * (1 - 0.02) <= VOUT_LO and VOUT_HI <= VOUT * (1 + 0.02), "SYS assumes +/-2 %"
    assert PG["uv_rise"][1] < VOUT_LO and PG["uv_fall"][1] < VOUT_LO - 0.5, "PG UV window"
    assert VOUT_HI + 0.8 < PG["ov_rise"][0] and PG["ov_rise"][1] < SYS["win"][1], "PG OV window"
    assert all(RIP[v]["v_pp"] <= 0.5 * RIPPLE_MAX for v in VIN_TABLE), "ripple calc above half the spec"
    assert all(RIP[v]["icap_each"] <= CPOL["irip"] for v in VIN_TABLE), "polymer ripple current"
    assert OPT["r_led"] <= OPT["r_led_max"] and OPT["vka_min"] >= 2.5 and OPT["i_atl_min"] >= 2 * ATL["imin"], "opto DC point"
    assert OPT["if_max"] <= 0.5 * OPTO["if_max"], "LED current"
    assert all(r["pm"] is not None and r["pm"] >= 45 and r["gm"] >= 10 for r in LOOP), "opto loop margins"
    assert all(b["pm"] is not None and b["pm"] >= 45 for b in BACKUP), "backup loop margin"
    assert SU["i_min"] > 2 * UCC["istart"] and SU["t_start"][0] <= 8.0, "start-up current / first-start time"
    assert SU["dtj"] <= 60.0, "start-up FET heating in a sustained short"
    # ---- SYS-IO-AUX rev D contract
    assert VOUT_LO >= SYS["feed_uvlo"][1] + 0.5, "output too close to the feed UVLO"
    assert VOUT_HI <= SYS["feed_ov"][0] - 0.5 and VOUT_LO >= SYS["win"][0] + 0.5, "regulation band vs feed OV / window"
    assert sc["vmax"] <= SYS["feed_ov"][0] - 0.3, ("start-up / hand-over overshoot too close to the latched feed OV", sc)
    assert max(s["vmax"] for s in STEP if s["bus"]) <= SYS["feed_ov"][0] - 0.3, "load-release overshoot vs feed OV"
    step_bus = min(s["vmin"] for s in STEP if s["bus"])
    assert step_bus >= SYS["win"][0] + 0.2, "load step"
    for corner in ("min", "typ", "max"):
        a, b, h = SIM[("alone", corner)], SIM[("black", corner)], SIM[("handover", corner)]
        assert a["t_take"] is not None and not a["uvlo"] and not a["trip"] and not a["ov_latched"], ("start alone", corner)
        assert a["t_take"] <= 0.7 * SU["t_hold"], ("aux takeover vs VDD hold-up", corner, a["t_take"])
        assert b["t_settle"] is not None and not b["uvlo"] and not b["trip"] and not b["ov_latched"], ("black start", corner)
        assert b["compf_max"] <= 0.98 * OVLD["v_trip"][0], ("timer margin in the black start", corner, b["compf_max"])
        assert h["bus_min"] >= SYS["handover_min"] + 0.3 and h["vmin_after"] > SYS["feed_uvlof"][1] + 0.5, ("hand-over", corner)
        assert not h["trip"] and h["compf_max"] <= 0.98 * OVLD["v_trip"][0], ("hand-over timer", corner)
        assert T1_DESIGN is None or (b["p_peak"] <= ILIM["p_min"] and h["p_peak"] <= ILIM["p_min"]), ("peak", corner)
    # 0.7 A two-coil port: held contactors exist only at a hand-over (a black start has them open) - checked there;
    # the black start with 0.7 A is reported as a stress case
    for key in (("handover07", "min"),):
        r = SIM[key]
        assert not r["trip"] and not r["uvlo"] and not r["ov_latched"] and r["compf_max"] <= 0.98 * OVLD["v_trip"][0], key
    hold = [v for k, v in OVT.items() if "port hold (<=" in k][0]
    assert all(r["t"] <= r["lim"] for r in hold), ("30.15 W port hold thermal", hold)
    assert SYS["p_hold2"] <= OVLD["trip"]["min"]["pout"] - 2.0, "port-hold load too close to the lowest timer level"
    assert all(STBY[v]["noload"]["p_in"] <= 2.0 for v in VIN_TABLE), "no-load input power"
    for name, ok in PART_STRESS:                      # parts on nets without a DC range in gen/aux_hv.py's VDC table
        assert ok, ("part stress (design check)", name)
    assert all(not SIM[("handover", c)]["ov_latched"] and not SIM[("handover", c)]["uvlo"] for c in ("min", "typ", "max"))
    assert SIM[("handover", "typ")]["t_settle"] is not None and not SIM[("handover", "typ")]["trip"], "hand-over recovery"
    # ---- AX-01 / AX-02 input
    assert INP["i2t"] <= 0.2 * FUSE["i2t"] and INP["e_fault"] <= 0.6 * INP["e_cap"], "fuse / resistor coordination"
    assert INP["i_fault"] <= 0.01 * FUSE["ibreak"], "fuse breaking current"
    assert max(x["v_r"] for x in INP["surges"]) <= 0.7 * RLIM["vpk"], "resistor surge voltage"
    assert SURGE_MAX <= CBULK["v70"] and SURGE_MAX_TR <= CBULK["surge_k"] * CBULK["v70"], "bulk film surge voltage"
    assert max(x["dvdt"] for x in INP["surges"]) * 1e-6 <= CBULK["dvdt"], "film dV/dt"
    assert max(x["i_cap"] for x in INP["surges"]) <= CBULK["ipk"], "film peak current"
    assert STR["ov"][0] >= VIN_TR + OV_MARGIN - 0.1 and STR["rel"][0] > VIN_MAX, "OV lockout vs 1100 V transient"
    assert OVL["vds_stop"] <= DERATE["trans"] * MOS["vbr"], "V_DS when the OV lockout stops switching"
    assert OVL["vds_locked"] <= DERATE["trans"] * MOS["vbr"] and OVL["v_fet"] <= 0.8 * BSS["vds"], "locked-out stress"
    assert STR["k_hf"][0] >= 1.0, "string must not be over-compensated (late surge trip)"
    # ---- AX-03 foldback
    assert all(r["dn_f"] > r["up"] for r in FB["rows"]), "foldback frequency too high for a staircase-free short"
    assert F_FOLD_RANGE[1] <= 0.8 * FB["f_safe"], "folded frequency margin"
    assert FB["v_fold"][0] >= FB["v_on"] + 0.5 and FB["v_fold"][1] <= 12.0, "foldback threshold window"
    assert FB["spike"][SPIKE_FRAC[2]] <= FB["th"][0] - 0.5, "clamp spike would hold the foldback off"
    assert all(x["af1"] >= FB["th"][1] + 0.5 for x in FB["light"] if x["p"] >= 1.0), "foldback at light load"
    # ---- AX-04 overload
    assert OVLD["comp_trip"][0] >= OVLD["comp_rated"] + 0.05, "timer would trip at the 30 W rating"
    assert OVLD["k"][0] * OVLD["comp_max"][0] >= 1.05 * OVLD["v_trip"][1], "timer must trip in current limit"
    assert OVLD["t_trip"][0] >= T_TRIP_MIN and OVLD["t_trip"][2] <= 0.3, "timer duration"
    assert OVLD["leak_err"][0] <= 0.03, "latch-diode leakage moves the timer threshold (control area <= 85 C)"
    rated = OVT["30 W, 1000 V, typical"]
    assert all(r["t"] <= r["lim"] for r in rated), "temperatures at the 30 W rating"
    hic = [v for k, v in OVT.items() if k.startswith("hiccup")][0]
    assert all(r["t"] <= r["lim"] for r in hic), ("hiccup temperatures", hic)
    # ---- INT-08 / INT-13 / AX-05
    assert BACKUP_BAND[0] >= VOUT_HI + 0.3 and BACKUP_BAND[1] <= 0.85 * CPOL["v"], "backup band"
    assert PGD["v_high"][0] >= 2.4 and PGD["v_high"][1] <= 3.6 and PGD["iz_min"] >= 1e-3, "PG drive"
    assert SU["v_e"][1] < BC817["vebo"], "AX-05 emitter voltage at 125 C"
    # ---- ngspice vs calculation
    for c in comp:
        assert abs(c["fsw_s"] / FS_NOM - 1) <= 0.03, ("ngspice fsw", c)
        assert abs(c["ipk_s"] / c["ipk_c"] - 1) <= 0.10, ("ngspice Ipk", c)
        assert abs(c["vds_s"] / c["vds_c"] - 1) <= 0.05, ("ngspice V_DS", c)
        assert abs(c["vo_s"] / c["vo_c"] - 1) <= 0.01, ("ngspice Vout", c)
        assert c["rip_s"] <= RIPPLE_MAX, ("ngspice ripple", c)
        assert c["eta_s"] >= c["eta_c"] - 0.01, ("ngspice efficiency below the loss model", c)
        assert c["fbk"] < 1.0, ("foldback engaged at full load", c)
    a, b, sg = spice["short_nofold"], spice["short"], spice["surge"]
    i_typ = UCC["vcs"][1] / RCS
    assert a["ipk_max"] >= 1.4 * i_typ and a["cs_max"] > 1.2 * UCC["vcs"][2], ("no staircase without foldback", a)
    assert b["ipk_all"] <= 1.3 * i_typ + ILIM["over"] and b["ipk_max"] <= i_typ + ILIM["over"] + 0.05, ("fold", b)
    assert b["ipk_max"] <= 1.1 * ILIM["max_eff"] and b["tper"] >= 0.8 / F_FOLD, ("foldback does not bound the short", b)
    assert b["vds_max"] <= DERATE["trans"] * MOS["vbr"], ("V_DS in the folded short", b)
    assert abs(sg["bulk_max"] / SURGE_MAX - 1) <= 0.05 and sg["vds_all"] <= DERATE["trans"] * MOS["vbr"], ("surge", sg)


if __name__ == "__main__":
    main()
