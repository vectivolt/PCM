"""DC PORT design calculation: everything between a module's external DC terminals and its internal DC bus
(PV-P75/100/110 ports A and B, DAB-D60 ports 1 and 2). REQUIREMENTS.md PV-03..PV-08, PV-C3..C5, ECO-07/09/10.

    .venv/bin/python sim/port_design.py

Writes sim/out/port_design/{report.md, port_spec.json, *.png, spice_precharge.csv} and the ngspice deck
sim/spice/port_precharge.cir. Every number here is CALCULATED or SIMULATED, nothing is bench-validated. Datasheet
values carry the document and page they were read from; our own assumptions are the A_* constants and are printed in
the report. Two port ratings are sized: 135 A (PV-P75, 3 cells, default PV-PORT build) and 180 A (PV-P100/110,
4 cells; cell_spec.json module/4_cells). Resistor-precharge methodology (energy = C V^2 / 2 in the resistor,
5 tau = 99.3 %) follows TI TIDUF21A sec. 1 eq. (1)-(5) and TIDUFH5 sec. 1 (ECO-07 / D-006).
"""
import itertools
import json
import math
import os
import subprocess

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(REPO, "sim", "out", "port_design")
SPICE_DIR = os.path.join(REPO, "sim", "spice")
DS = "docs/datasheets/"

# =====================================================================================================
# Requirements (REQUIREMENTS.md) and the module facts the cell study publishes (cell_spec.json module/*)
# =====================================================================================================
V_MIN, V_MAX = 250.0, 1000.0          # PV-03 / PV-06 operating window, both ports
C_RANGE = (100e-6, 1e-3)               # brief: design envelope of the bus capacitance per port
V24 = (21.6, 26.45)                    # gen/interfaces.py (D-013): 21.6-26.0 V window, SYS cuts off at 26.10-26.45 V

# =====================================================================================================
# Datasheet values (document, page)
# =====================================================================================================
# TDK HVC43-MC main contactor, HVC43MC.pdf v02 2024-10-22, p2-p3 (type 250A, 24 V coil = B88269X7340C011)
K_ITH = 250.0             # p3 continuous current (250 A type)
K_MAKE_CAP = (20.0, 140.0)      # p3 capacitive make test: 20 V, 140 A, 70 000 operations
K_CUTOFF_1000 = 450.0     # p3 maximum cut-off at 1000 V: 450 A, 1 operation (tau <= 0.3 ms)
K_UIMP = 8000.0           # p3 rated impulse withstand voltage, contact-coil
K_COIL_R, K_COIL_TOL = 96.0, 0.10   # p3 24 V coil, +/-10 % at thermal equilibrium; pick-up <= 18 V, drop-out >= 2 V
K_PICKUP, K_DROPOUT = 18.0, 2.0
K_AUX = (1.0, 24.0, 10e-3, 1.0, 0.5)   # p3 aux contact: 1..24 V, 10 mA .. 1 A, <= 0.5 ohm
K_SC_I, K_SC_T = 5000.0, 5e-3   # HVC43.pdf v10 p3 "short circuit handling (5 ms) 5 kA" - base model; MC sheet silent
K_BREAK_T, K_CLAMP_MIN = 15e-3, 50.0   # HVC43MC p3 break time <= 15 ms; p7 alternative suppressor must clamp >= 50 V
# HVC43.pdf p6 "Current handling capability at 85 C", 250 A type, read by pixel analysis (+/-10 %). Agrees with the
# p3 table (320 A / 10 min, 540 A / 1 min). Above 1.9 kA joined log-log to the 5 kA / 5 ms short-circuit point.
K_CARRY = [(260, 5e4), (290, 2000), (310, 850), (330, 550), (350, 393), (380, 239), (420, 125), (560, 59.2),
           (650, 46.1), (750, 34.8), (900, 23.2), (1100, 13.5), (1300, 8.1), (1500, 5.13), (1700, 2.69), (1900, 1.41),
           (5000, 0.005)]
# Omron G7L-2A-X DC24 precharge relay, G7L-X.pdf (Cat. J216-E1-04) p1-p5
PR_I_RATED_1000 = 25.0    # p1 rated load 25 A at 1000 VDC, two poles in series, normal polarity
PR_T_RELEASE = 30e-3      # p2 release time max
PR_COIL_R, PR_COIL_TOL, PR_OPERATE, PR_RELEASE = 250.0, 0.15, 0.75, 0.10   # p1: 24 V coil, +/-15 %, 75 % / 10 %
# Miba RST 200 pulse resistor, Miba_RST_200_Series.pdf p15 (PA-05: published pulse ratings): 10-300 ohm +/-5 %,
# +/-500 ppm/K, 200 W at 70 C for 120 s, <= 1000 V DC, typical pulse load (initial <= 85 C) 2300 J at tau 0.1 s,
# 3150 J at 0.2 s, 4000 J at 0.5 s; short-time overload 3000 W, 70 C, 3 s; insulation 3.5 kV rms 1 min; ~285 g
RST_P, RST_T_P, RST_VMAX, RST_TOL, RST_TCR, RST_DIEL = 200.0, 120.0, 1000.0, 0.05, 500e-6, 3500.0
RST_PULSE = [(0.1, 2300.0), (0.2, 3150.0), (0.5, 4000.0)]
RST_STO_P, RST_STO_T = 3000.0, 3.0
RST_E_STO = RST_STO_P * RST_STO_T
# Honeywell 3100U00031461 (Honeywell-3100U.pdf, close-on-rise table): closes 82 +/-3 C rising, reopens 71 +/-3 C
# rev F0: RFQ KSD301-class normally-open disc thermostat, 80 +/-5 C close, ~15 K differential (class data; no maker
# datasheet on file - manual item); replaces the Honeywell 3100U00031461 (82 +/-3 C)
TSTAT_CLOSE, TSTAT_OPEN = (75.0, 85.0), (60.0, 70.0)
# Mersen HelioProtection HP10NH, NH-gPV-1000VDC.pdf (DS-LFPVHP10NH-16-0922): p1 1000 VDC, 50 kA at L/R = 1 ms (UL);
# p2 ratings; p3 bases (Uimp 8 kV); pre-arcing curves p4 (NH1) / p5 (NH2) read by pixel analysis (+/-10 % current,
# the sheet's own tolerance). The 160 A NH1 points come from cropped views of p4 (curve marked "160 A").
F_IR_DC = 50e3
F_UIMP_BASE = 8000.0
F160 = [(227, 1500), (364, 150), (397, 100), (590, 15), (643, 10), (1048, 1.48), (1186, 0.78), (1630, 0.1),
        (2075, 0.0107), (4500, 0.001), (9800, 0.0002)]
F250 = [(334, 3000), (524, 300), (971, 30), (1656, 3), (2440, 0.3), (3402, 0.03), (6379, 0.003), (16319, 0.0003)]
# Mersen AN "Temperature De-Rating" p1: I_new = I_rated*sqrt((125-TA)/100) for its LV families; HP10NH not listed
F_DERATE = lambda ta: math.sqrt((125.0 - ta) / 100.0)  # noqa: E731
# Battery ports (source = "battery"). ETI GREEN PROTECT catalogue (ETI-Green-Protect.pdf): NH gBat fuse-link 1000 V
# d.c. (L/R = 1 ms), breaking capacity 30 kA, IEC 60269-7 - table p.104-105 (In, loss at In / 0.7 In, pre-arcing and
# operating I2t), time-current curves p.106-107 read by pixel analysis; temperature derating factor table p.12 (TDF).
# Sinofuse (Asian preference, D-031) publishes no full-range gBat with time-current / I2t data on its site.
ETI_TDF = [(20.0, 1.03), (30.0, 0.97), (40.0, 0.90), (50.0, 0.82), (60.0, 0.73), (70.0, 0.63)]
E200 = [(309, 3000), (366, 1000), (448, 300), (538, 100), (660, 30), (803, 10), (975, 3), (1095, 1), (1206, 0.3),
        (1310, 0.1), (1374, 0.05), (1426, 0.03), (1506, 0.02), (1614, 0.015)]
E250 = [(364, 3000), (436, 1000), (532, 300), (649, 100), (810, 30), (990, 10), (1268, 3), (1452, 1), (1658, 0.3),
        (1834, 0.1), (1922, 0.05), (1986, 0.03), (2033, 0.02), (2067, 0.015)]
BATT = {   # port rating -> battery-port fuse (ETI order code) and the contactor pair
    135: dict(fuse="004110760", name="ETI NH1 gBat 200A 1000V DC", In=200.0, P_In=27.0, P_07=11.0, size="NH1",
              curve=E200, i2t_pre=4400.0, i2t_op=29000.0),
    180: dict(fuse="004110760", name="ETI NH1 gBat 200A 1000V DC", In=200.0, P_In=27.0, P_07=11.0, size="NH1",
              curve=E200, i2t_pre=4400.0, i2t_op=29000.0)}   # 250 A (E250) leaves 384-724 A with the pair: 200 A + limit
E_ICU, E_LR = 30e3, 1e-3                     # ETI 1000 V gBat breaking capacity and its L/R
# ON-BOARD SURGE PROTECTION (rev F0; replaces the DEHN 952515 / Raycap 59.D040 DIN-rail arresters and their backup
# fuses). Per port a Y of three Thinking TVT25751 3-terminal thermally protected varistors: MOV1 pole+ -> M, MOV2
# pole- -> M, MOV3 M -> PE, each pole branch behind its own 10x38 1000 V DC fuse (SPD_FUSE).
# Thinking-TVT-Series.pdf (2026.02): p9 25 mm potting: V1mA 750 V (675-825), 465 VAC / 615 VDC, Vc 1240 V at 150 A,
# Imax 25 kA (8/20, 1 x), Wmax 497 J (2 ms), 710 J (10/1000 us); p14 typical circuit: thermal fuse element in series
# with the disc, monitor lead at their junction (line potential); p12 UL 1449 (E314979, Type 4 component assembly), TUV
# IEC 61051-1/-2 - NO IEC 61643-11/-31 approval and no DC rating of the thermal link; p16 derating, potting 25 mm: 15
# impulses 8/20 at ~9 kA (read +/-15 %). The TVT sheet has no v/i curve: Thinking's TVR20751 'max. clamping voltage'
# curve (Thinking-TVR-Series.pdf p25, pixel analysis; reads 1234 V at 100 A vs the 1240 V table value) is scaled to the
# 25 mm part by the class-current ratio 150 A / 100 A (same 1240 V, TVT p8 vs p9).
MOV_VDC, MOV_V1MA, MOV_IMAX, MOV_W2MS, MOV_I15 = 615.0, 750.0, 25e3, 497.0, 9e3
MOV_VC20 = [(100, 1234.0), (1000, 1579.0), (2000, 1784.0), (3000, 1944.0), (5000, 2182.0)]   # TVR20751, A -> V max
MOV_K25 = 1.5
SPD_IN = 3e3              # declared nominal discharge current of each Y (8/20 us); 15-impulse rating ~9 kA -> 3 x margin
# ETI-Green-Protect.pdf 'CH14x51 gPV 1000V d.c.': 36 A (002637115) 30 kA d.c. at L/R 2 ms, pre-arc I2t 450 A2s
SPD_FUSE = dict(In=36.0, i2t_pre=450.0, icu=30e3, lr=2e-3)
N_MON, R_MON = 12, 82.5e3   # varistor-network monitor chain (2512 thick film) through the three thermal links + CNY65B LED
K_RC = (0.125e-3, 0.25e-3)       # HVC43MC p3: contact resistance at 100 A, typ / max
VARIANTS = {   # port rating -> what changes (cell_spec.json module/3_cells, module/4_cells port_current_max_A)
    135: dict(cells=3, fuse="HP10NH1GPV160", item="E1028288", In=160.0, P_In=23.0, P_07=9.6, size="NH1",
              base="HPBB11PPR", curve=F160, sc_mult=3.0, cmc_A=160, term_A=200),
    180: dict(cells=4, fuse="HP10NH2GPV250", item="Y1037620", In=250.0, P_In=31.0, P_07=12.9, size="NH2",
              base="HPBB21PPR", curve=F250, sc_mult=2.5, cmc_A=200, term_A=250),
}
# TI AMC3330 (SBASA34B) voltage channels, p4 p7 p8
AMC_V_FSR, AMC_V_CLIP, AMC_V_GAIN = 1.0, 1.25, 2.0
AMC_V_VOS, AMC_V_TCVOS = 0.3e-3, 4e-6
AMC_V_EG, AMC_V_TCEG, AMC_V_NL = 0.002, 45e-6, 0.0002
AMC_V_RIN, AMC_V_IIB = 0.1e9, 10e-9
AMC_V_IDD = 43e-3         # p9 max with 1 mA HLDO load
AMC_V_DELAY = 2.6e-6      # p8 VIN -> VOUT signal delay 50-90 %, max (1.6 us typ 50-50 %); output BW >= 300 kHz
AMC_V_NOISE = 250e-6      # p7 output noise, 100 kHz bandwidth, Vrms (differential output, gain 2)
AMC_VIMP = 7700.0         # p7
# TI AMC3302 (SBASA11B) current channel, p6-p8; DIAG low = output invalid / fail-safe (sec 7.3.5)
AMC_I_FSR, AMC_I_CLIP, AMC_I_GAIN = 50e-3, 64e-3, 41.0
AMC_I_VOS, AMC_I_TCVOS, AMC_I_EG, AMC_I_TCEG = 50e-6, 0.5e-6, 0.002, 35e-6
AMC_I_CM = (1.39, 1.44, 1.49)
AMC_I_IDD = 42e-3
AMC_I_VIOSM = 6250.0
# Bourns CSM2F-8518-L100J01 shunt, CSM2F-8518.pdf p1-p2: TCR on test points 125 ppm/K, alloy < 50 ppm/K
SH_R, SH_TOL, SH_TCR, SH_TCR_ALLOY, SH_P, SH_EMF = 100e-6, 0.05, 125e-6, 50e-6, 36.0, 1.5e-6
# TDK B57703M0103A018 shunt NTC, B57703M.pdf: R25 10 k +/-2 %, B25/100 3988 K +/-1 %, test voltage 1000 VAC 1 s
NTC_VTEST = 1000.0
# Vishay TNPV1206 / TNPV1210 e3, TNPW1206 e3 (docs 28881, 28758) p1-p3
TNPV_UMAX, TNPV_P, TNPV_TCR, TNPV_TOL, TNPV_VCR = 700.0, 0.27, 25e-6, 0.001, 1e-6
TNPV1210_UMAX, TNPV1210_P = 1000.0, 0.33
TNPW_TCR, TNPW_TOL = 25e-6, 0.001
# Wolfspeed C2M1000170D (IMD and discharge switch), p1-p2: VGS(th) 2.0-4.0 V at 25 C (2.4 V typ at 150 C), Qgd 12 nC
Q_VDS, Q_IDSS_MAX, Q_IDSS_TYP, Q_QGD = 1700.0, 100e-6, 1e-6, 12e-9
# TI TPSI3050-Q1 (SLVSFJ7D): two-wire EN 6.5..48 V, |dV_EN/dt| >= 65 V/ms (p5), I_EN 1.9 mA typ steady state and
# 27 mA typ at start-up (p8, R_PXFR 7.32 k), C_VDDP 220-330 nF ABSOLUTE in two-wire mode (p5), VIMP 9.23 kV (p6)
TPSI_EN_MIN, TPSI_EN_SLEW, TPSI_I_EN, TPSI_VIMP = 6.5, 65.0, 1.9e-3, 9230.0
TPSI_I_START, TPSI_CVDDP = 27e-3, (220e-9, 330e-9)
C_VDDP, C_VDDP_TOL = 270e-9, 0.05     # our part: 270 nF C0G 5 % (PA-10) - no bias or temperature loss
# Coil drive parts. Vishay IRFL214 (SOT-223) p1-p2: 250 V, +/-20 V, RDS(on) <= 2.0 ohm at 10 V, VGS(th) 2-4 V, EAS 50 mJ.
# Vishay IRFR9214 (DPAK) p1-p2: -250 V, +/-20 V, RDS(on) <= 3.0 ohm at -10 V. TDK S10K30 (SIOV-Leaded-Standard):
# v(1 mA) 47 V +/-10 %, vc 93 V at 5 A. Bourns SMBJ33A: VBR 36.7-40.6 V. Diodes MMBT3904/3906: VCEO 40 V, VEBO 6 V.
LS_VDS, LS_RDS, LS_VTH, LS_EAS = 250.0, 2.0, (2.0, 4.0), 0.050
HS_VDS, HS_RDS = 250.0, 3.0
VAR_V1MA, VAR_TOL, VAR_VC, VAR_IC = 47.0, 0.10, 93.0, 5.0
TVS33_VBR = (36.7, 40.6)
R_GATE_DIV = (10e3, 22e3)    # DO line (or hold supply) -> 10 k -> gate, 22 k gate-GND: 15-18 V gate drive
# SYS-IO-AUX DO clamp (gen/sys_io_aux.py sheet 09): SMAJ36A + SMAJ12A, OUT -14.1..-15.7 V, 0.5 J per turn-off;
# TPS272C45 SLVSF24C p7: VS - VOUT <= 48 V
DO_CLAMP, DO_CLAMP_J, DO_VSOUT_ABS = (-15.7, -14.1), 0.5, 48.0
# TI SN74LVC1G17 SCES351Y Table 5-1 (VCC 3.0 V row, interpolated to 3.315 V): VT+ 1.48-2.09 V, VT- 0.89-1.36 V,
# II +/-5 uA, VOH >= VCC - 0.1 V at 100 uA; LVC1G11/1G32 VIH 2.0 V, inputs 5.5 V tolerant
VT_P, VT_N, LVC_II, LVC_VOH_DROP, LVC_VIH, LVC_VIMAX = (1.48, 2.09), (0.89, 1.36), 5e-6, 0.1, 2.0, 5.5
# TI TLV3502 (SBOS321E) p3 pins, p5: VOS +/-6.5 mV, 6 mV internal hysteresis; TI REF3030E (REF30 family sheet):
# 3.0 V, +/-0.1 %, 15 ppm/K
TLV_VOS, TLV_HYST = 6.5e-3, 6e-3
VREF, VREF_TOL, VREF_TC = 3.0, 0.001, 15e-6
# SYS-IO-AUX DO stage TI TPS272C45 (SLVSF24C) p6-p8: current limit 4.3-6.95 A (RILIM = GND, DO2/DO4), slew 0.45-1.05
# V/us, output clamp VS-49..61 V. DI stage TI ISO1212 (SLLSEY7G) p10-p11 with RSENSE 562 ohm, RTHR 1 k:
# VIL >= 8.7 V, VIH <= 10.95 V, input current 2.05-2.75 mA above VIL
DO_ILIM_MAIN, DO_ILIM_SMALL, DO_SLEW = (0.52, 0.82), (1.58, 2.30), (450.0, 1050.0)   # A (SYS DO_CFG, INT-20), V/ms
DI_VIL, DI_VIH, DI_I = 8.7, 10.95, (2.05e-3, 2.75e-3)
# Vishay VY1 Y capacitor, VY1.pdf (doc 28537) p1-p2: Y1 500 VAC, 1500 VDC, 4.7 nF max
CY, CY_VDC, CY_TOL = 4.7e-9, 1500.0, 0.2
# X capacitor: KEMET C4AQ.pdf p5 allows a surge of 1.5 x VNDC (1950 V) at most 10 times - below the SPD let-through
# (PA-07). The part is now an RFQ specification: 2.2 uF film, 1300 V DC at 70 C, ESR <= 16 mOhm, impulse >= CX_VIMP.
CX, CX_V85, CX_ESR, CX_VIMP, CX_C4AQ_SURGE = 2.2e-6, 1100.0, 16e-3, 4500.0, 1950.0
# TDK S10K30 coil varistor, SIOV-Leaded-Standard.pdf: Wmax (2 ms) 4.4 J; Bourns SMBJ33A: 600 W (10/1000 us)
VAR_WMAX, TVS_PPK = 4.4, 600.0

# =====================================================================================================
# Our assumptions (A_*) - printed in the report
# =====================================================================================================
A = {
    "A_LS_BATT": (3e-6, "battery/bus source inductance incl. cable, worst case (short cable), H"),
    "A_RS_BATT": (0.03, "battery/bus source resistance incl. cable, worst case (stiff), ohm"),
    "A_RS_PV": (1.5, "PV array dynamic resistance near Voc ((Voc-Vmp)/Imp ~ 200 V/135 A), ohm"),
    "A_L_LOOP": (0.5e-6, "busbar + contactor + fuse loop inductance inside the module, H"),
    "A_R_LOOP": (2.0e-3, "loop resistance used for the closing inrush (fuses + contactor + shunt + busbar), ohm"),
    "A_R_LOOP_X": (0.65e-3, "capacitor-dump loop without the two fuses (contactor max 0.25 + shunt 0.1 + busbars), ohm"),
    "A_ESR_BUS": (1.0e-3, "bus film-capacitor bank ESR, ohm"),
    "A_T_FUSE": (65.0, "air at the fuses at full current: fuses in the INLET air path, 60 C inlet (module_spec: full "
                       "power to 60 C) + 5 K from the fuse, base and busbar losses, C"),
    "A_DT_SENSE": (60.0, "temperature excursion of sensing parts away from the 25 C calibration point, K"),
    "A_ISC_PV": (1.25, "PV array short-circuit current at the module terminals / rated port current (sizing rule)"),
    "A_IPSC_BATT": (20e3, "battery prospective short-circuit current at the module terminals (1000 V / 50 mOhm), A"),
    "A_CLEAR_I2T": (2.0, "fuse total-clearing I2t / pre-arc I2t at 1000 V DC (Mersen publishes no DC total I2t)"),
    "A_L_COIL_K": (2.0, "HVC43 coil inductance (not published), H"),
    "A_L_COIL_R": (1.0, "G7L-X coil inductance (not published), H"),
    "A_VF": (0.9, "S2M forward drop at coil current, V"),
    "A_NTC_ERR": (3.0, "shunt temperature error seen by firmware: NTC +/-2 %, B +/-1 %, pad gradient, SYS ADC, K"),
    "A_TCR_RES": (50e-6, "residual shunt TCR after design-level characterisation = alloy TCR limit (copper part "
                         "systematic, measured once on qualification samples), 1/K"),
    "A_C_PV_PE": (16.5e-6, "PV array capacitance to PE, worst case 200 nF/kWp x 82.5 kWp (REFERENCE-LESSONS TIDA-010938 "
                           "row 2; PV-P110 22 uF = A_C_PV_PE_110), F"),
    "A_C_PV_PE_110": (22e-6, "same for PV-P110 (110 kWp), F"),
    "A_T_PRED": (0.33, "IMD 3-sample exponential prediction spacing (TIDUFG5 p8-12), s"),
    "A_VCM_LF": (50.0, "150 Hz common-mode voltage of the DC poles vs PE (3rd-harmonic of a transformerless 400 V "
                       "PCS, ~1/6 of the phase peak), V rms"),
    "A_VCM_HF": (5.0, "residual common-mode voltage at 20 kHz on the module side of the CM choke, V rms"),
    "A_T_DECIDE": (1e-3, "precharge supervision sample/decision latency, s"),
    "A_T_BLANK": (20e-3, "precharge supervision blanking after relay closure, s"),
    "A_ADC_BITS": (12, "CTRL ADC resolution used for the IMD resolution estimate"),
    "A_IEN_MAX": (2.85e-3, "TPSI3050 two-wire steady EN current, max (sheet gives 1.9 mA typ only): 1.5 x typ, A"),
    "A_VD_MIN": (0.8, "timer trigger drop BAS16 + MMBT3906 VEB at ~1 uA, hot, V"),
    "A_VD_MAX": (1.25, "timer trigger drop BAS16 + MMBT3906 VEB at ~1 uA, cold, V"),
    "A_VZ15_LOW": (13.3, "BZX84-C15 at 10-100 uA (avalanche knee below the 13.8 V / 5 mA limit), V"),
    "A_CT_LO": (-0.35, "1 uF X7R 50 V 1206 timer capacitor: tolerance + bias at <= 15 V + temperature, low"),
    "A_CT_HI": (0.10, "same, high"),
    "A_I_LEAK_T": (0.1e-6, "leakage at the timer node (BAS16 reverse, MMBT3906 EB, C), A"),
    "A_VTH_SIC_HOT": (1.6, "C2M1000170D VGS(th) minimum at 150 C (2.0 V min at 25 C, typ falls 0.4 V to 150 C), V"),
    "A_BETA_UA": (30.0, "MMBT3904/3906 current gain at 1-20 uA (sheet: hFE >= 100 at 10 mA)"),
    "A_T_RES": (60.0, "RST 200 housing temperature in normal operation (chassis, inlet air side), C"),
    "A_CTH_RST": (240.0, "RST 200 heat capacity (285 g aluminium housing + element), J/K"),
    "A_DT_NULL": (20.0, "temperature change of the sense parts between the VA/VAX null and the next precharge, K"),
    "A_T_LS_OFF": (5e-6, "Q_LS2 turn-off after its gate supply (hold high-side output) collapses, s"),
    "A_C_OS_TOL": (0.2, "ratio tolerance of two X7R timing capacitors of one type (bias and temperature track)"),
    "A_V3V3": (0.02, "sensor rail +3V3S tolerance (LMR36015 FB 1 % + divider)"),
    "A_RC_MIN": (0.06e-3, "HVC43 contact resistance, lowest (sheet gives typ 0.125 / max 0.25 mOhm only), ohm"),
    "A_R_BRANCH": (0.2e-3, "busbar + lugs of one contactor branch of a pair, ohm (layout symmetric within A_RB_TOL)"),
    "A_RB_TOL": (0.1, "branch busbar resistance mismatch between the two contactors of a pair"),
    "A_T_HOT": (60.0, "inlet air at full current for the 180 A thermal check (module_spec: full power to 60 C), C"),
    "A_V_TRIP_OS": (1144.0, "port voltage peak with a frozen controller at full current (pv_control report), V"),
    "A_I_MAKE_PV": (330.0, "PV port, K_A closing onto an empty bank at Voc: discharge peak of the array / cable capacitance "
                            "into the bank on top of Isc (architecture ~0.5 kA total; not measured), A"),
    "A_L_SPD": (0.3e-6, "loop inductance terminal -> branch fuse -> MOV1 -> M -> MOV3 -> PE link (board layout limit), H"),
    "A_T_DIDT": (4e-6, "8/20 us surge: peak di/dt taken as In / 4 us (twice the 10-90 % front average)"),
    "A_R_RIB": (0.05, "PORT ribbon ground return, 3 conductors + contacts, ohm (ID-line ground shift)"),
}
a = {k: v[0] for k, v in A.items()}

# =====================================================================================================
# Design choices (the schematic uses these via port_spec.json)
# =====================================================================================================
R_PRE = 220.0             # precharge resistor (Miba RST 200, RFQ value)
DV_OK = 10.0              # precharge complete when |V_terminal - V_bus| <= DV_OK (measured after the VA/VAX null)
K_CHK = 0.8               # abort if V_bus < K_CHK * V_term * (1 - exp(-t / tau_max)) after blanking
N_ATTEMPTS = 3            # consecutive precharge attempts before lockout
R_DIS_E, N_DIS = 300.0, 2   # active discharge resistor: 2 x Miba RST 200 300 ohm in series
R_DIS = R_DIS_E * N_DIS
N_BLEED, R_BLEED_E = 10, 47e3   # passive bleeder: 10 x 47 k 2512 1 W thick film
N_DIV, R_DIV_E, R_DIV_B = 6, 1.00e6, 4.99e3   # sense divider: 6 x TNPV1206 1M00 + TNPW1206 4k99
C_DIV_F, C_DIV_TOL = 4.7e-9, 0.05   # divider filter capacitor C0G 5 % (AMC3330 Fig 7-2); was 10 nF, see LAG_MAX
LAG_MAX = 30e-6           # coordinator (pv_control): divider RC + AMC3330 delay <= 30 us for the frozen-controller OV case
N_BIAS, R_BIAS_E = 5, 2.00e6    # discharge MOSFET gate bias string (TNPV1206 2M00)
R_GREF = (1e6, 1e6)       # SiC gate pull-down = timer reference divider (DIS_G -> REF -> BUS-)
R_TIM, C_TIM = 4.7e6, 1e-6     # time limit RC (DIS_G -> T, T -> BUS-)
R_LATCH, R_TSTAT = 1e6, 100e3  # latch base-emitter resistors; thermostat trigger resistor
N_IMD, R_IMD_E = 8, 124e3       # IMD test resistor string per switch (TNPV1210 124k)
R_FB_PULLUP = 1.5e3       # aux-contact wetting / inversion pull-up, 2512 (feeds DI, hold logic and the TPSI EN OR)
R_FB_DIV = (100e3, 25.5e3)     # FB -> 3.3 V logic divider, one per hold channel
R_EN_PD = 100e3           # TPSI EN node pull-down behind the K_DISCH / FB diode-OR
# CM-cancelling threshold network per side, 0.1 % thin film. +IN: r1 from OUTP, r2 to AGND, rt from the TEST driver
# (r2 || rt = r1 keeps the common-mode cancellation); -IN: r1 from OUTN, r3 to REF, r4 to AGND
HOLD_R = dict(r1=49.9e3, r2=118e3, rt=86.6e3, r3=88.7e3, r4=113e3)
R_TST_DIV = (100e3, 15e3)  # DO_SPARE (24 V) -> TEST Schmitt buffer
R_OS, C_OS, C_DLY, R_OS_IN = 47e3, 3.3e-6, 22e-6, 10e3   # one-shot / delay RCs of the proof-test injection
R_HC_DIV = (100e3, 15e3)   # hold high-side output -> readback OR gate
# Readback on PV-PORT: ID line DAC. CTRL pulls ID to 2.5 V through 10.0 k (interfaces.ID_OHM note); the readback
# outputs (3.3 V CMOS) drive R_RB_A (port A bits) and R_RB_B (port B bits) into ID; R_ID_RB to GND keeps 10.0 k.
R_RB_A, R_RB_B, R_ID_RB, ID_PULLUP, ID_VBIAS = 118e3, 42.2e3, 28.0e3, 10.0e3, 2.5   # CTRL-C2000 rev D values
ADC_LSB = ID_VBIAS / 4096                    # CTRL reads ID on a 12-bit ADC referenced to the same 2.5 V
OC_MULT = 1.5             # firmware over-current trip that opens the contactor, x rated current
V_OV_TRIP = 1050.0        # over-voltage trip, both ports


def ensure(cond, msg):
    if not cond:
        raise AssertionError(msg)


# =====================================================================================================
# 0. Bus capacitance: the cell / DAB engineers' published values (+ the 100 uF .. 1 mF design envelope)
# =====================================================================================================
def bus_capacitances():
    """the cell / DAB engineers' published bank capacitances (main() adds the 100 uF .. 1 mF envelope).  Both spec files must exist
    and carry their banks: a missing or emptied file stops the script instead of a silent envelope-only run (PCM-23)"""
    cases, notes = [], []
    p = os.path.join(REPO, "sim/out/pv_design/cell_spec.json")
    ensure(os.path.exists(p), "%s missing: run sim/pv_design.py first" % p)
    mod = json.load(open(p))["module"]
    for k in sorted(mod):
        if "port_A_capacitance_total_uF" in mod[k]:
            c = mod[k]["port_A_capacitance_total_uF"] * 1e-6
            cases.append(("PV %s" % k.replace("_", " "), c))
            notes.append("PV: cell_spec.json module/%s: %.0f uF per port, %.0f A" % (k, c * 1e6, mod[k]["port_current_max_A"]))
    ensure(cases, "%s: no module/*/port_A_capacitance_total_uF - re-run sim/pv_design.py" % p)
    p = os.path.join(REPO, "sim/out/dab_design/dab_spec.json")
    ensure(os.path.exists(p), "%s missing: run sim/dab_design.py first" % p)
    banks = json.load(open(p))["capacitor_banks"]
    ensure(banks, "%s: no capacitor_banks - re-run sim/dab_design.py" % p)
    for port, d in sorted(banks.items()):
        cases.append(("DAB %s" % port, d["C_total_F"]))
        notes.append("DAB: dab_spec.json capacitor_banks/%s: %.0f uF" % (port, d["C_total_F"] * 1e6))
    return cases, notes


# =====================================================================================================
# 1. Precharge: time, energy, supervision, shorted / overloaded bus
# =====================================================================================================
def precharge_run(V, C, R_load=math.inf, tau_max=None, t_out=None, dt=1e-4):
    """RC charge through R_PRE with an optional bus load and the firmware supervision."""
    t, v, E = 0.0, 0.0, 0.0
    open_at, outcome = None, ""
    g = 1 / R_PRE + (0.0 if math.isinf(R_load) else 1 / R_load)
    v_ss, decay = V / R_PRE / g, math.exp(-dt * g / C)      # exact RC step: stable for any load, incl. a short
    while True:
        v_new = v_ss + (v - v_ss) * decay
        E += ((V - v) ** 2 + (V - v_new) ** 2) / 2 / R_PRE * dt
        v = v_new
        t += dt
        if open_at is not None:
            if t >= open_at:
                return outcome, t, E
            continue
        if V - v <= DV_OK:
            return "complete", t, E               # main contactor closes, precharge relay then opens (no current)
        if t > a["A_T_BLANK"] and v < K_CHK * V * (1 - math.exp(-t / tau_max)):
            outcome, open_at = "abort-slow-rise", t + a["A_T_DECIDE"] + PR_T_RELEASE
        elif t >= t_out:
            outcome, open_at = "abort-timeout", t + a["A_T_DECIDE"] + PR_T_RELEASE


def rst_allow(tau):
    """Miba RST 200 single-pulse energy for an exponential pulse of time constant tau (p15 table, log-log; below
    0.1 s extrapolated on the 0.1-0.2 s slope, above 0.5 s held at the 0.5 s value)."""
    (t1, e1), (t2, e2) = RST_PULSE[0], RST_PULSE[1]
    if tau <= t1:
        return e1 * (tau / t1) ** (math.log(e2 / e1) / math.log(t2 / t1))
    return interp_loglog(RST_PULSE, min(tau, RST_PULSE[-1][0]))


def rst_rect_ok(P, t):
    """Rectangular pulse P for t: inside the energy envelope of the short-time overload (<= 3 kW, <= 3 kW x 3 s; a
    lower power for longer at the same energy is milder - more heat reaches the housing), inside 200 W / 120 s, or,
    above 3 kW, inside the pulse rating taken at tau = 2 t (an exponential pulse delivers most energy in tau / 2)."""
    return (P <= RST_STO_P and P * t <= RST_E_STO) or (P <= RST_P and t <= RST_T_P) or P * t <= rst_allow(2 * t)


def precharge(C_cases):
    tau_max = R_PRE * C_RANGE[1]
    t_out = 1.3 * tau_max * math.log(V_MAX / DV_OK)
    r_hi = R_PRE * (1 + RST_TOL) * (1 + RST_TCR * 100)        # +5 % and +100 K self-heating / ambient
    rows = []
    for name, C in C_cases:
        for V in (V_MIN, V_MAX):
            tau = R_PRE * C
            rows.append(dict(case=name, C_uF=C * 1e6, V=V, tau_ms=tau * 1e3, t_ok_s=tau * math.log(V / DV_OK),
                             t_ok_hi_s=r_hi * C * math.log(V / DV_OK), I_pk=V / (R_PRE * (1 - RST_TOL)),
                             P_pk_kW=V * V / (R_PRE * (1 - RST_TOL)) / 1e3, E_J=0.5 * C * V * V,
                             E_allow_J=rst_allow(tau)))
    sweep = []
    for k in [0.0, 0.05, 0.2, 0.5, 1.0, 2.0, 3.0, 3.8, 4.0, 4.2, 5.0, 6.0, 10.0, 30.0, 100.0, math.inf]:
        for C in C_RANGE:
            RL = 1e-3 if k == 0 else (math.inf if math.isinf(k) else k * R_PRE)
            out, tend, E = precharge_run(V_MAX, C, RL, tau_max, t_out, dt=2e-4)
            sweep.append(dict(R_load_over_R=k, C_uF=C * 1e6, outcome=out, t_end_s=tend, E_J=E))
    E_worst = max(s["E_J"] for s in sweep)
    shorted = [s for s in sweep if s["R_load_over_R"] == 0.0][0]
    p_sc = V_MAX ** 2 / R_PRE
    lo, hi = 0.01, 10.0                                        # survival time of a shorted bus (rectangular pulse)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if p_sc * mid <= rst_allow(2 * mid) else (lo, mid)
    # a healthy bus with R_pre at +5 % / +100 K must never be aborted by the slow-rise check (1 mF, worst case)
    tt = np.linspace(a["A_T_BLANK"], t_out, 400)
    margin = min((1 - np.exp(-tt / (r_hi * C_RANGE[1]))) / (K_CHK * (1 - np.exp(-tt / tau_max))))
    return dict(tau_max=tau_max, t_out=t_out, rows=rows, sweep=sweep, E_worst=E_worst, shorted=shorted,
                survival_shorted_s=lo, shorted_ok=rst_rect_ok(p_sc, shorted["t_end_s"]),
                E_allow=rst_allow(RST_PULSE[-1][0]), attempts_E=N_ATTEMPTS * E_worst, r_hi=r_hi, chk_margin=margin,
                lockout_s=N_ATTEMPTS * E_worst / (0.5 * RST_P))


# =====================================================================================================
# 2. Contactor closing inrush at the residual difference DV_OK (damped series RLC)
# =====================================================================================================
def rlc_peak(dV, R, L, C):
    a_ = R / (2 * L)
    w0 = 1 / math.sqrt(L * C)
    if a_ < w0:
        wd = math.sqrt(w0 * w0 - a_ * a_)
        tp = math.atan(wd / a_) / wd
        return dV / (L * wd) * math.exp(-a_ * tp) * math.sin(wd * tp), tp
    s1, s2 = -a_ + math.sqrt(a_ * a_ - w0 * w0), -a_ - math.sqrt(a_ * a_ - w0 * w0)
    tp = math.log(s2 / s1) / (s1 - s2)
    return dV / (L * (s1 - s2)) * (math.exp(s1 * tp) - math.exp(s2 * tp)), tp


def inrush(C_cases):
    rows = []
    for name, C in C_cases:
        ib, tb = rlc_peak(DV_OK, a["A_RS_BATT"] + a["A_R_LOOP"] + a["A_ESR_BUS"], a["A_LS_BATT"] + a["A_L_LOOP"], C)
        ip, _ = rlc_peak(DV_OK, a["A_RS_PV"] + a["A_R_LOOP"], 10e-6 + a["A_L_LOOP"], C)
        r_x = a["A_R_LOOP"] + a["A_ESR_BUS"] + CX_ESR / 2
        ix, _ = rlc_peak(DV_OK, r_x, a["A_L_LOOP"], 2 * CX * C / (2 * CX + C))   # terminal X caps dump first
        bound = ib + ix * math.exp(-tb * r_x / (2 * a["A_L_LOOP"]))          # X-cap ring still alive at the peak
        rows.append(dict(case=name, C_uF=C * 1e6, I_batt=ib, t_peak_us=tb * 1e6, I_pv=ip, I_xcap=ix, I_bound=bound))
    return rows


# =====================================================================================================
# 3. Discharge: passive bleeder and fail-safe active discharge
# =====================================================================================================
def time_limit():
    """HV-side one-shot that ends an active discharge (PA-03 / INT-01). DIS_G (SiC gate, 15 V zener) charges C_TIM
    through R_TIM; a PNP compares V(C) with REF = DIS_G x R_GREF ratio and, at V(C) > REF + V_d, fires the
    MMBT3906/MMBT3904 latch that clamps DIS_G to ~0.75 V until K_DISCH re-energises (TPSI3050 -> BSS138 resets it).
    The threshold is ratiometric to DIS_G, so the zener tolerance largely cancels."""
    eta = [R_GREF[1] * (1 + s * 0.01) / (R_GREF[0] * (1 - s * 0.01) + R_GREF[1] * (1 + s * 0.01)) for s in (-1, 1)]
    vals = []
    for vg in (a["A_VZ15_LOW"], 15.6):
        for vd in (a["A_VD_MIN"], a["A_VD_MAX"]):
            for e in eta:
                for leak in (0.0, a["A_I_LEAK_T"]):
                    for c in (1 + a["A_CT_LO"], 1 + a["A_CT_HI"]):
                        for r in (0.99, 1.01):
                            v_inf = vg - leak * R_TIM * r
                            v_th = e * vg + vd
                            vals.append(R_TIM * r * C_TIM * c * math.log(v_inf / (v_inf - v_th)))
    i_bias_min = (V_MIN - 0.75) / (N_BIAS * R_BIAS_E)
    i_shunt = 0.75 / sum(R_GREF) + 2 * 0.65 / R_LATCH            # what the latched state must still feed
    i_hold = 2 * 0.65 / R_LATCH * (1 + 1 / a["A_BETA_UA"])          # loop gain >= 1 with both base-emitter shunts
    return dict(t_min=min(vals), t_max=max(vals), t_nom=R_TIM * C_TIM * math.log(1 / (0.5 - 1.0 / 15.0)),
                v_latched=0.75, vth_sic=a["A_VTH_SIC_HOT"], i_avail_uA=(i_bias_min - i_shunt) * 1e6,
                i_hold_uA=i_hold * 1e6, i_tstat_uA=(13.8 - 0.65) / R_TSTAT * 1e6)


def discharge(C_cases, tl):
    r_div, r_bias = N_DIV * R_DIV_E + R_DIV_B, N_BIAS * R_BIAS_E + sum(R_GREF)
    r_pass = 1 / (1 / (N_BLEED * R_BLEED_E) + 1 / r_div + 1 / r_bias)
    r_dis_hi = R_DIS * (1 + RST_TOL)
    r_act = 1 / (1 / r_dis_hi + 1 / r_pass)
    rows = [dict(case=n, C_uF=C * 1e6, t_passive_s=r_pass * C * math.log(V_MAX / 60.0),
                 t_active_s=r_act * C * math.log(V_MAX / 60.0), E_J=0.5 * C * V_MAX ** 2,
                 E_per_R_J=0.5 * C * V_MAX ** 2 / N_DIS, E_allow_J=rst_allow(R_DIS * C)) for n, C in C_cases]
    t_dis_max = max(r["t_active_s"] for r in rows)
    r_dis_lo = R_DIS * (1 - RST_TOL)
    cases = []                                       # worst-case energy per RST 200 against its ratings
    for name, v, r_src in (("normal stop: bus capacitor only (1 mF envelope)", V_MAX, None),
                           ("source live, discharge runs to the time limit (welded/held contactor, 24 V lost, or "
                            "TPSI/BSS138 failed)", V_MAX, 0.0),
                           ("same at 1100 V (above the 1050 V OV trip)", 1100.0, 0.0),
                           ("welded precharge relay: source -> R_pre -> bus -> R_dis, to the time limit", V_MAX, R_PRE)):
        if r_src is None:
            e = 0.5 * C_RANGE[1] * v * v / N_DIS
            p, t = v * v / r_dis_lo / N_DIS, R_DIS * C_RANGE[1] / 2
            ok = e <= rst_allow(R_DIS * C_RANGE[1])
        else:
            i = v / (r_src * (1 - RST_TOL) + r_dis_lo) if r_src else v / r_dis_lo
            p, t = i * i * r_dis_lo / N_DIS, tl["t_max"]
            e = p * t
            ok = rst_rect_ok(p, t)
        cases.append(dict(case=name, V=v, P_per_R_W=p, t_s=t, E_per_R_J=e, ok=ok))
    e_tstat = [(TSTAT_CLOSE[0] - a["A_T_RES"]) * a["A_CTH_RST"], (TSTAT_CLOSE[1] - a["A_T_RES"]) * a["A_CTH_RST"]]
    p_live = cases[1]["P_per_R_W"]
    return dict(r_passive=r_pass, P_passive_W=V_MAX ** 2 / r_pass, P_bleed_elem_W=(V_MAX / N_BLEED) ** 2 / R_BLEED_E,
                V_bleed_elem=V_MAX / N_BLEED, P_dis_pk_W=V_MAX ** 2 / r_dis_lo, I_dis_pk=V_MAX / r_dis_lo, rows=rows,
                t_dis_max=t_dis_max, cases=cases, e_tstat=e_tstat, t_tstat=[e / p_live for e in e_tstat],
                e_miller_mJ=0.5 * V_MAX * (V_MAX / r_dis_lo) * Q_QGD / ((V_MAX - 15) / (N_BIAS * R_BIAS_E)) * 1e3)


# =====================================================================================================
# 4. HV sense divider + AMC3330 error budget (VA / VAX / VB / VBX, AUX1 / AUX2 use the same divider)
# =====================================================================================================
def divider():
    r_top = N_DIV * R_DIV_E
    rb_eff = 1 / (1 / R_DIV_B + 1 / AMC_V_RIN)
    k = rb_eff / (r_top + rb_eff)
    budget = {}
    for V in (V_MIN, V_MAX):
        e = {
            "resistor tolerance (ratio, 0.1 % parts)": TNPV_TOL + TNPW_TOL,
            "TCR mismatch 25+25 ppm/K over A_DT_SENSE": (TNPV_TCR + TNPW_TCR) * a["A_DT_SENSE"],
            "VCR < 1 ppm/V at the element voltage": TNPV_VCR * V / N_DIV,
            "AMC3330 gain error (25 C)": AMC_V_EG,
            "AMC3330 gain drift 45 ppm/K": AMC_V_TCEG * a["A_DT_SENSE"],
            "AMC3330 nonlinearity": AMC_V_NL,
            "AMC3330 offset 0.3 mV (input)": AMC_V_VOS / k / V,
            "AMC3330 offset drift 4 uV/K": AMC_V_TCVOS * a["A_DT_SENSE"] / k / V,
            "input bias 10 nA x 4.99 k": AMC_V_IIB * R_DIV_B / k / V,
        }
        cal = ("resistor tolerance (ratio, 0.1 % parts)", "AMC3330 gain error (25 C)", "AMC3330 offset 0.3 mV (input)")
        budget[V] = dict(items=e, worst=sum(e.values()), rss=math.sqrt(sum(x * x for x in e.values())),
                         worst_cal=sum(v for kk, v in e.items() if kk not in cal),
                         rss_cal=math.sqrt(sum(v * v for kk, v in e.items() if kk not in cal)))
    # dynamics and noise of the filter (the DC budget above does not depend on C)
    r_th = 1 / (1 / R_DIV_B + 1 / r_top)
    tau = {c: r_th * c for c in (10e-9, C_DIV_F)}                   # rev 2 (10 nF) vs now
    kt = 1.380649e-23 * 300.0
    m3 = json.load(open(os.path.join(REPO, "sim/out/pv_design/cell_spec.json")))["module"]["3_cells"]   # fail closed (PCM-23)
    rip_f, rip_pp = m3["ripple_frequency_kHz"] * 1e3, m3["port_ripple_voltage_pp_worst_V"]
    att = {c: 1 / math.sqrt(1 + (2 * math.pi * rip_f * t) ** 2) for c, t in tau.items()}
    return dict(k=k, gain_V_per_V=AMC_V_GAIN * k, v_fs=AMC_V_FSR / k, v_clip=AMC_V_CLIP / k, elem_v=V_MAX / N_DIV,
                elem_v_clip=AMC_V_CLIP / k / N_DIV, elem_p=(V_MAX / N_DIV) ** 2 / R_DIV_E,
                p_total=V_MAX ** 2 / (r_top + R_DIV_B), fc_hz=1 / (2 * math.pi * r_th * C_DIV_F),
                v_at_ovtrip=V_OV_TRIP * k, budget=budget, r_th=r_th, tau=tau,
                lag=r_th * C_DIV_F * (1 + C_DIV_TOL) + AMC_V_DELAY, lag_old=tau[10e-9] + AMC_V_DELAY,
                noise_amc=AMC_V_NOISE / AMC_V_GAIN / k, ktc={c: math.sqrt(kt / c) / k for c in tau},
                rip_f=rip_f, rip_pp=rip_pp, rip={c: rip_pp * x for c, x in att.items()},
                ramp_err={c: 0.5e6 * t for c, t in tau.items()})


# =====================================================================================================
# 5. Current sense: shunt + AMC3302, with and without NTC compensation of the shunt TCR
# =====================================================================================================
def current_sense(Ir):
    i_lin, i_clip = AMC_I_FSR / SH_R, AMC_I_CLIP / SH_R
    common = {"AMC3302 gain drift 35 ppm/K": AMC_I_TCEG * a["A_DT_SENSE"],
              "AMC3302 offset drift 0.5 uV/K": AMC_I_TCVOS * a["A_DT_SENSE"] / (Ir * SH_R),
              "thermal EMF 1.5 uV/K x 5 K sense-point difference": SH_EMF * 5 / (Ir * SH_R)}
    plain = dict({"shunt TCR 125 ppm/K on test points": SH_TCR * a["A_DT_SENSE"]}, **common)
    comp = dict({"residual TCR after NTC compensation (A_TCR_RES)": a["A_TCR_RES"] * a["A_DT_SENSE"],
                 "shunt temperature error A_NTC_ERR x 125 ppm/K": a["A_NTC_ERR"] * SH_TCR}, **common)
    cal = {"shunt tolerance 5 %": SH_TOL, "AMC3302 gain error": AMC_I_EG, "AMC3302 offset 50 uV": AMC_I_VOS / (Ir * SH_R)}
    return dict(Ir=Ir, i_lin=i_lin, i_clip=i_clip, v_at_imax=Ir * SH_R, P_shunt=Ir ** 2 * SH_R, oc=OC_MULT * Ir,
                sc=VARIANTS[int(Ir)]["sc_mult"] * Ir, plain=plain, comp=comp, cal=cal,
                worst_plain=sum(plain.values()), worst_comp=sum(comp.values()),
                rss_comp=math.sqrt(sum(v * v for v in comp.values())), worst_uncal=sum(plain.values()) + sum(cal.values()))


# =====================================================================================================
# 6. Fault coordination: fuse, contactor, hold-closed interlock; battery and PV sources
# =====================================================================================================
def interp_loglog(curve, x):
    xs, ys = np.log([p[0] for p in curve]), np.log([p[1] for p in curve])
    return float(np.exp(np.interp(math.log(x), xs, ys)))


def t_fuse(curve, I):
    """Pre-arc time; inf below the curve (non-fusing), constant-I2t beyond the last point."""
    if I < curve[0][0]:
        return math.inf
    if I > curve[-1][0]:
        return curve[-1][1] * (curve[-1][0] / I) ** 2
    return interp_loglog(curve, I)


def t_carry(I):
    if I <= K_ITH:
        return math.inf
    if I < K_CARRY[0][0]:
        return K_CARRY[0][1]
    if I > K_CARRY[-1][0]:
        return K_CARRY[-1][1] * (K_CARRY[-1][0] / I) ** 2
    return interp_loglog(K_CARRY, I)


OPEN = 1e15


def i_trip(r1, r2, rt, r1n, r3, r4, vref, cm, vos, eg=0.0, sh=0.0, ofs=0.0, vt=0.0):
    """Current at which one side of a hold channel trips. +IN = (OUTP/r1 + vt/rt) / (1/r1 + 1/r2 + 1/rt),
    -IN = (OUTN/r1n + VREF/r3) / (1/r1n + 1/r3 + 1/r4); OUTP/N = cm +/- Vd/2 (AMC3302). vt = TEST driver level."""
    g1, g2, gt = 1 / r1, 1 / r2, 1 / rt
    kp, kt = g1 / (g1 + g2 + gt), gt / (g1 + g2 + gt)
    g1n, g3, g4 = 1 / r1n, 1 / r3, 1 / r4
    aa, bb = g1n / (g1n + g3 + g4), g3 / (g1n + g3 + g4)
    if kp + aa < 1e-9:
        return math.inf
    vd = (bb * vref + vos - kt * vt - (kp - aa) * cm) * 2 / (kp + aa)   # AMC differential output at the trip point
    if abs(vd) > AMC_I_CLIP * AMC_I_GAIN:                               # beyond the AMC3302 clip: never reached
        return math.inf if vd > 0 else -math.inf
    return (vd / (AMC_I_GAIN * (1 + eg)) - ofs) / (SH_R * (1 + sh))


def hold_threshold(vt=0.0):
    """Over-current comparator of the hold-closed interlock (both channels, both sides: same network). +IN = k*OUTP,
    -IN = a*OUTN + b*VREF with r2 || rt = r1 and r3 || r4 = r1n, so the AMC3302 common mode (1.39-1.49 V) cancels.
    Returns nominal and worst-case band of the trip current; vt > 0 = proof-test injection active."""
    r = HOLD_R
    nom = i_trip(r["r1"], r["r2"], r["rt"], r["r1"], r["r3"], r["r4"], VREF, AMC_I_CM[1], 0, vt=vt)
    vals = []
    t = 1e-3                                                           # 0.1 % thin-film resistors
    for s in itertools.product((-1, 1), repeat=10):
        vals.append(i_trip(r["r1"] * (1 + s[0] * t), r["r2"] * (1 + s[1] * t), r["rt"] * (1 + s[9] * t),
                           r["r1"] * (1 + s[2] * t), r["r3"] * (1 + s[3] * t), r["r4"] * (1 + s[4] * t),
                           VREF * (1 + s[5] * (VREF_TOL + VREF_TC * a["A_DT_SENSE"])),
                           AMC_I_CM[0] if s[6] < 0 else AMC_I_CM[2], s[7] * (TLV_VOS + TLV_HYST / 2),
                           s[8] * (AMC_I_EG + AMC_I_TCEG * a["A_DT_SENSE"]), vt=vt * (1 + s[9] * 0.03)))
    lo, hi = min(vals), max(vals)
    ofs = (AMC_I_VOS + AMC_I_TCVOS * a["A_DT_SENSE"]) / SH_R
    if vt:                                                              # at I = 0 only gain-free terms matter
        return dict(nom=nom, lo=lo - ofs, hi=hi + ofs)
    lo, hi = lo / (1 + SH_TOL) - ofs, hi / (1 - SH_TOL) + ofs
    return dict(nom=nom, lo=lo, hi=hi, vd_nom=nom * SH_R * AMC_I_GAIN)


def proof_test(hold):
    """DO_SPARE = TEST. Per port four Schmitt-buffered signals inject a calibrated offset (r_t into +IN): T (TEST)
    -> channel 1 side A; T_d (T through 47 k / 22 uF) -> channel 1 side B; one-shot(T rising) -> channel 2 side A;
    one-shot(T_d rising) -> channel 2 side B. No single fault can trip both channels for longer than a one-shot.
    Readback per port: RB1 = OC1 OR (hold high-side output present), RB2 = OC2; firmware reads RB1 + RB2."""
    vcc = 3.315
    v_on = (vcc * (1 - a["A_V3V3"]) - LVC_VOH_DROP, vcc * (1 + a["A_V3V3"]))
    vt_nom = vcc - 0.05
    test = hold_threshold(vt=vt_nom)
    test_hi = max(hold_threshold(vt=v)["hi"] for v in v_on)          # must trip at I = 0: hi < 0
    r = HOLD_R
    base = dict(r1=r["r1"], r2=r["r2"], rt=r["rt"], r1n=r["r1"], r3=r["r3"], r4=r["r4"], vref=VREF)
    faults = [("none (healthy)", {}), ("r1 OUTP -> +IN open", dict(r1=OPEN)), ("r2 +IN -> AGND open", dict(r2=OPEN)),
              ("r2 +IN -> AGND short", dict(r2=1e-3)), ("rt TEST -> +IN open", dict(rt=OPEN)),
              ("r1n OUTN -> -IN open", dict(r1n=OPEN)), ("r3 -IN -> REF open", dict(r3=OPEN)),
              ("r4 -IN -> AGND open", dict(r4=OPEN)), ("r4 -IN -> AGND short", dict(r4=1e-3)),
              ("REF3030E output 0 V", dict(vref=0.0)), ("REF3030E output at 3.3 V", dict(vref=vcc))]
    rows = []
    vos = TLV_VOS + TLV_HYST / 2
    for name, f in faults:
        p = dict(base, **f)
        it_c, itt_c = [], []
        for cm, sv, sr in itertools.product((AMC_I_CM[0], AMC_I_CM[2]), (-1, 1), (-1, 1)):
            vref = p["vref"] * (1 + sr * VREF_TOL) if "vref" not in f else p["vref"]
            args = (p["r1"], p["r2"], p["rt"], p["r1n"], p["r3"], p["r4"], vref, cm, sv * vos)
            it_c.append(i_trip(*args))
            itt_c += [i_trip(*args, vt=v) for v in v_on]
        it = i_trip(p["r1"], p["r2"], p["rt"], p["r1n"], p["r3"], p["r4"], p["vref"], AMC_I_CM[1], 0.0)
        itt = i_trip(p["r1"], p["r2"], p["rt"], p["r1n"], p["r3"], p["r4"], p["vref"], AMC_I_CM[1], 0.0, vt=vt_nom)
        if name.startswith("none"):
            found = "-"
        elif max(it_c) < 0:
            found = "at rest (RB = 1 with no current)"
        elif min(itt_c) > 0:
            found = "proof test (side does not trip under TEST)"
        elif max(itt_c) > 0:
            found = "proof test, marginal (only in part of the tolerance corners)"
        elif max(it_c) < 135.0:
            found = "in operation (RB = 1 at |I| > %.0f A, below rated current)" % max(it_c)
        elif max(it_c) < hold["lo"]:
            found = "not found: trips early (%.0f A) - harmless alone, the hold needs both channels" % it
        else:
            found = "NOT FOUND"
        rows.append(dict(fault=name, I_trip=it, I_trip_test=itt, found=found))
    # injection timing (Schmitt thresholds, input leakage through the RC resistors, 20 % capacitor ratio tolerance)
    vl = LVC_II * (R_OS + R_OS_IN)
    t_os = [R_OS * C_OS * (1 - a["A_C_OS_TOL"] / 2) * math.log((v_on[0] + vl) / (VT_N[1] + vl)),
            R_OS * C_OS * (1 + a["A_C_OS_TOL"] / 2) * math.log((v_on[1] - vl) / (VT_N[0] - vl))]
    t_up = [R_OS * C_DLY * (1 - a["A_C_OS_TOL"] / 2) * math.log((v_on[1] + vl) / (v_on[1] + vl - VT_P[0])),
            R_OS * C_DLY * (1 + a["A_C_OS_TOL"] / 2) * math.log((v_on[0] - vl) / (v_on[0] - vl - VT_P[1]))]
    t_dn = [R_OS * C_DLY * (1 - a["A_C_OS_TOL"] / 2) * math.log((v_on[0] + vl) / (VT_N[1] + vl)),
            R_OS * C_DLY * (1 + a["A_C_OS_TOL"] / 2) * math.log((v_on[1] - vl) / (VT_N[0] - vl))]
    windows = [("W1", "T rises", "ch1 A + ch2 A", 2, "%.2f-%.2f s" % tuple(t_os),
                "ch2 side A; contactor closed: K_x_MAIN off -> must stay closed (both chains)"),
               ("W2", "one-shot ends", "ch1 A", 1, "until T_d rises (%.2f-%.2f s)" % tuple(t_up),
                "ch1 side A; contactor closed: K_x_MAIN off -> must OPEN (channel 2 blocks)"),
               ("W3", "T_d rises", "ch1 A+B, ch2 B", 2, "%.2f-%.2f s" % tuple(t_os),
                "ch2 side B; contactor closed: K_x_MAIN off -> must stay closed"),
               ("W4", "one-shot ends", "ch1 A+B", 1, "until firmware drops TEST", "-"),
               ("W5", "T falls", "ch1 B", 1, "%.2f-%.2f s" % tuple(t_dn),
                "ch1 side B; contactor closed: K_x_MAIN off -> must OPEN"),
               ("idle", "T_d falls", "none", 0, "-", "RB1 = RB2 = 0 at rest (catches a stuck hold high side)")]
    # PV-PORT readback DAC on the ID line (CTRL-C2000 rev D values): 3 levels per port (RB1 + RB2), port A on R_RB_A,
    # port B on R_RB_B. With both hold channels of a port on (RB = 2) its hold coils draw current from the PORT +24V,
    # which returns over the ribbon ground and lifts the port-board ground under the ID network.
    i_hold = {"A": 0.30, "B": 0.60}                      # PV-PORT: port A one HVC43 coil, port B (battery) a pair
    lv = []
    for na in range(3):
        for nb in range(3):
            for coils in (False, True):
                if coils and na < 2 and nb < 2:
                    continue
                vg = a["A_R_RIB"] * (i_hold["A"] * (na == 2) + i_hold["B"] * (nb == 2)) if coils else 0.0
                band = []
                for voh in v_on:
                    for s in itertools.product((-1, 1), repeat=4):
                        ra, rb, rid, rpu = (R_RB_A * (1 + s[0] * 1e-3), R_RB_B * (1 + s[1] * 1e-3),
                                            R_ID_RB * (1 + s[2] * 1e-3), ID_PULLUP * (1 + s[3] * 1e-3))
                        g = 1 / rpu + 1 / rid + 2 / ra + 2 / rb
                        band.append((ID_VBIAS / rpu + voh * (na / ra + nb / rb) + vg * (g - 1 / rpu)) / g)
                lv.append(dict(A=na, B=nb, coils=coils, lo=min(band), hi=max(band)))
    lv.sort(key=lambda d: d["lo"])
    gaps = [(lv[i + 1]["lo"] - lv[i]["hi"], lv[i], lv[i + 1]) for i in range(len(lv) - 1)
            if (lv[i]["A"], lv[i]["B"]) != (lv[i + 1]["A"], lv[i + 1]["B"])]
    gap_off = min(g for g, p_, q_ in gaps if not p_["coils"] and not q_["coils"])
    gap_on = min(g for g, p_, q_ in gaps)
    r_eff = 1 / (1 / R_ID_RB + 2 / R_RB_A + 2 / R_RB_B)
    return dict(test=test, test_hi=test_hi, v_on=v_on, shift=hold["nom"] - test["nom"], rows=rows, t_os=t_os,
                t_up=t_up, t_dn=t_dn, order_ok=t_os[1] < t_up[0], windows=windows, levels=lv, gap_off=gap_off,
                gap_on=gap_on, r_eff=r_eff, v_id=ID_VBIAS * r_eff / (r_eff + ID_PULLUP),
                v_unpowered=ID_VBIAS * R_ID_RB / (R_ID_RB + ID_PULLUP))


READBACK_R = {   # CTRL-C2000 rev D values: R_id to GND, port-A readback pair, port-B readback pair; hold-coil currents
    "PV-PORT": dict(r_id=28.0e3, r_a=118e3, r_b=42.2e3, i_hold_a=0.30, i_hold_b=0.60),
    "DAB60-PORT": dict(r_id=3.32e3, r_a=76.8e3, r_b=26.7e3, i_hold_a=0.60, i_hold_b=0.60)}


def readback_states(code):
    """Every readback state (n_A, n_B = RB1 + RB2 of port A / B) of a port board on the CTRL ID input: nominal voltage,
    band over V_OH (3.315 V +/-2 %, -0.1 V), 0.1 % resistors and 0.1 % pull-up, and - when a port's hold is on (n = 2) -
    the ribbon-ground lift by its hold-coil current. Plus the unpowered board (outputs high-Z, Ioff)."""
    r = READBACK_R[code]
    vcc = 3.315
    v_on = (vcc * (1 - a["A_V3V3"]) - LVC_VOH_DROP, vcc * (1 + a["A_V3V3"]))
    out = []
    for na in range(3):
        for nb in range(3):
            g0 = 1 / ID_PULLUP + 1 / r["r_id"] + 2 / r["r_a"] + 2 / r["r_b"]
            nom = (ID_VBIAS / ID_PULLUP + vcc * (na / r["r_a"] + nb / r["r_b"])) / g0
            vg = a["A_R_RIB"] * (r["i_hold_a"] * (na == 2) + r["i_hold_b"] * (nb == 2))
            band = []
            for voh in v_on:
                for sg in itertools.product((-1, 1), repeat=4):
                    ra, rb, rid, rpu = (r["r_a"] * (1 + sg[0] * 1e-3), r["r_b"] * (1 + sg[1] * 1e-3),
                                        r["r_id"] * (1 + sg[2] * 1e-3), ID_PULLUP * (1 + sg[3] * 1e-3))
                    g = 1 / rpu + 1 / rid + 2 / ra + 2 / rb
                    for lift in {0.0, vg}:
                        band.append((ID_VBIAS / rpu + voh * (na / ra + nb / rb) + lift * (g - 1 / rpu)) / g)
            out.append(dict(nA=na, nB=nb, V_nom=nom, V_lo=min(band), V_hi=max(band)))
    out.sort(key=lambda d: d["V_nom"])
    gap = min(out[i + 1]["V_lo"] - out[i]["V_hi"] for i in range(len(out) - 1))
    unp = [ID_VBIAS * r["r_id"] * (1 + x * 1e-3) / (r["r_id"] * (1 + x * 1e-3) + ID_PULLUP * (1 - x * 1e-3)) for x in (-1, 1)]
    r_eff = 1 / (1 / r["r_id"] + 2 / r["r_a"] + 2 / r["r_b"])
    return dict(states=out, gap_V=gap, gap_counts=gap / ADC_LSB, unpowered_V=[min(unp), max(unp)], R_eff_ohm=r_eff)


def coordination(Ir, hold, C_cases, v=None, share=1.0):
    """share < 1: a pair of contactors in parallel, each carrying at most share x I (battery ports)."""
    v = v or VARIANTS[int(Ir)]
    curve = v["curve"]
    rows, bad = [], []

    def judge(I):
        """(t_fuse, t_fuse_worst, t_contactor, verdict, state) with state ok / marginal / gap."""
        tf, tw, tk = t_fuse(curve, I), t_fuse(curve, 0.9 * I), t_carry(share * I)   # tw: fuse at -10 % current
        if I <= hold["lo"]:
            return tf, tw, tk, "contactor breaks (OC trip / E-stop, <= %.0f A)" % K_CUTOFF_1000, "ok"
        i2t_let = share ** 2 * min(a["A_CLEAR_I2T"] * I * I * tw, v.get("i2t_op", math.inf)) if tw < K_SC_T else 0.0
        if i2t_let > K_SC_I ** 2 * K_SC_T:
            return tf, tw, tk, "GAP: fuse let-through %.0f kA2s > contactor 125 kA2s" % (i2t_let / 1e3), "gap"
        held = "held until the fuse clears" if I > hold["hi"] else "contactor breaks or is held until the fuse clears"
        if math.isinf(tk):                     # each contactor of a pair at or below its continuous rating
            return tf, tw, tk, held + " (contactor current <= Ith)", "ok"
        if tw < tk:
            return tf, tw, tk, held, "ok"
        if tw < 1.1 * tk and tf < tk:
            return tf, tw, tk, "MARGINAL: -10 %% fuse overlaps the 85 C carry curve by %.0f %%" % (100 * (tw / tk - 1)), \
                "marginal"
        return tf, tw, tk, "GAP: fuse slower than the contactor carry limit", "gap"

    grid = sorted(set([1.2 * Ir, OC_MULT * Ir, 250.0, 300.0, round(hold["lo"]), 400.0, round(hold["hi"]), 450.0,
                       500.0, 560.0, 650.0, 750.0, 900.0, 1100.0, 1300.0, 1500.0, 1700.0, 2000.0, 3000.0, 5000.0,
                       1e4, a["A_IPSC_BATT"]]))
    for I in grid:
        if I < 1.2 * Ir:
            continue
        tf, tw, tk, verdict, state = judge(I)
        rows.append(dict(I=I, t_fuse=tf, t_fuse_worst=tw, t_contactor=tk, verdict=verdict, ok=state == "ok"))
    marginal = []
    for I in np.arange(1.2 * Ir, max(a["A_IPSC_BATT"], E_ICU if share < 1 else 0), 2.0):   # 2 A scan
        state = judge(I)[4]
        (bad if state == "gap" else marginal if state == "marginal" else []).append(float(I))
    gap = (min(bad), max(bad)) if bad else None
    marg = (min(marginal), max(marginal)) if marginal else None
    i_pv = a["A_ISC_PV"] * Ir
    pv = dict(I_max=i_pv, t_fuse=t_fuse(curve, i_pv), breaks=i_pv <= min(hold["lo"], K_CUTOFF_1000))
    r_fuse = v["P_In"] / v["In"] ** 2
    r_loop = a["A_R_LOOP_X"] + a["A_ESR_BUS"] + 2 * r_fuse
    melt = v.get("i2t_pre") or min(i * i * t for i, t in curve if t <= 3e-3)
    dump = []
    for name, C in C_cases:
        E = 0.5 * C * V_MAX ** 2
        ipk, _ = rlc_peak(V_MAX, r_loop, a["A_L_LOOP"], C)
        i2t = E / r_loop
        let = min(i2t, a["A_CLEAR_I2T"] * melt) if i2t > melt else i2t
        dump.append(dict(case=name, C_uF=C * 1e6, E_J=E, I_pk_kA=ipk / 1e3, I2t_kA2s=i2t / 1e3, fuse_melts=i2t > melt,
                         contactor_I2t_kA2s=let / 1e3, weld_risk=ipk > K_SC_I,
                         L_for_5kA_uH=C * (V_MAX / K_SC_I) ** 2 * 1e6))
    gap_req = None
    if gap:
        span = np.logspace(math.log10(gap[0]), math.log10(gap[1]), 200)
        gap_req = dict(lo=gap[0], hi=gap[1], t_at_lo=t_carry(gap[0]), t_at_2k=t_carry(2000.0), t_at_hi=t_carry(gap[1]),
                       t_max=min(t_carry(I) for I in span))
    return dict(Ir=Ir, v=v, rows=rows, gap=gap, marginal=marg, gap_req=gap_req, pv=pv, dump=dump, melt_kA2s=melt / 1e3,
                r_loop=r_loop, i_derated=v["In"] * F_DERATE(a["A_T_FUSE"]),
                p_fuse=v["P_07"] * (Ir / (0.7 * v["In"])) ** 2, nonfusing=1.13 * v["In"])


DT_FUSE = a["A_T_FUSE"] - a["A_T_HOT"]     # local rise of the fuse air above the inlet air


def tdf(ta):
    """ETI temperature derating factor (catalogue p.12 table), linear between the tabulated points."""
    return float(np.interp(ta, [p[0] for p in ETI_TDF], [p[1] for p in ETI_TDF]))


def battery_ports(hold, C_cases, cd):
    """source = "battery" (battery rack or stiff DC bus). ETI 1000 V gBat port fuses, a PAIR of HVC43 contactors in
    parallel (the pair doubles the overload carry so that a fuse sized for the port current still clears every
    battery-fed current above the hold band before either contactor's carry limit), Raycap ProTec T2-1500DCGU-3Y with
    its own 200 A gBat backup fuses at the terminals. Also the 180 A thermal check at 60 C (D-020)."""
    rb = a["A_R_BRANCH"]
    hi, lo = K_RC[1] + rb * (1 + a["A_RB_TOL"]), a["A_RC_MIN"] + rb * (1 - a["A_RB_TOL"])
    share = hi / (hi + lo)                                            # worst branch of the pair
    out = {}
    for Ir, f in BATT.items():
        v = dict(f, fuse=f["name"], size=f["size"], base="ETI NH base 1000 V DC", cmc_A=VARIANTS[Ir]["cmc_A"],
                 term_A=VARIANTS[Ir]["term_A"], sc_mult=VARIANTS[Ir]["sc_mult"])
        t_amb = a["A_T_FUSE"]
        pair = coordination(float(Ir), hold, C_cases, v=v, share=share)
        single = coordination(float(Ir), hold, C_cases, v=v, share=1.0)   # degraded: one contactor failed open
        out[Ir] = dict(v=v, t_amb=t_amb, derated=f["In"] * tdf(t_amb), derated60=f["In"] * tdf(a["A_T_HOT"] + DT_FUSE),
                       p_fuse=f["P_In"] * (Ir / f["In"]) ** 2, pair=pair, single=single, share=share,
                       p_k=(share * Ir) ** 2 * K_RC[1], i_k=share * Ir)
    # coil drive and hold current with two coils per port (SYS-IO-AUX DO, PORT +24V)
    i_gate = V24[1] / sum(R_GATE_DIV)
    coil = dict(i_do=2 * cd["i0"] + i_gate, i_hold=2 * cd["i0"], i_low=cd["i0"])
    # thermal at 180 A, 60 C inlet: both port types
    t180 = dict(pv_fuse=VARIANTS[180]["In"] * F_DERATE(a["A_T_FUSE"]), pv_fuse_W=VARIANTS[180]["P_In"] * (180 / 250) ** 2,
                pv_k_W=180 ** 2 * K_RC[1], shunt_W=180 ** 2 * SH_R, pv135_fuse60=VARIANTS[135]["In"] * F_DERATE(a["A_T_FUSE"]))
    # port current limit versus inlet air (fuse air = inlet + DT_FUSE), every source and rating: firmware limit and the
    # input of the PV module simulation (port_spec "port_current_limit_vs_inlet_C")
    inlet = [25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0]
    lim = {}
    for Ir in (135, 180):
        lim["pv/%d" % Ir] = [min(Ir, VARIANTS[Ir]["In"] * F_DERATE(t + DT_FUSE)) for t in inlet]
        lim["battery/%d" % Ir] = [min(Ir, BATT[Ir]["In"] * tdf(t + DT_FUSE)) for t in inlet]
    return dict(ports=out, share=share, coil=coil, t180=t180, inlet=inlet, limit=lim,
                cable_k2s2=115.0 ** 2 * (70.0 ** 2))         # 70 mm2 Cu PVC (k = 115) vs fuse let-through


# =====================================================================================================
# 7. Coil / feedback interface against SYS-IO-AUX (TPS272C45 DO stage, ISO1212 DI)
# =====================================================================================================
def v_var(i, tol=0.0):
    """S10K30 clamping voltage at current i: power law through v(1 mA) and vc(5 A) of SIOV-Leaded-Standard."""
    return VAR_V1MA * (1 + tol) * (i / 1e-3) ** (math.log(VAR_VC / VAR_V1MA) / math.log(VAR_IC / 1e-3))


def demag(v_clamp, i0, i1, L, R):
    """Time for a coil current to fall i0 -> i1 against a clamp v_clamp(i) plus the coil's own i R."""
    ii = np.linspace(i1, i0, 2000)
    return float(np.trapezoid(L / (np.array([v_clamp(i) for i in ii]) + ii * R), ii))


def coil_drive():
    """Two switches in every coil-current path (PA-01, PA-02, INT-03): K_x_MAIN feeds the coil through S2M AND gates
    the low-side IRFL214 Q_LS1; the hold path = IRFR9214 high side (channel 1) + IRFL214 low side Q_LS2 (channel 2),
    whose gate is supplied from the high side's output, so Q_LS2 can conduct only while the hold high side is on.
    The S10K30 sits across the coil and is the only flyback path."""
    vlo, vhi = V24
    rk_lo, rk_hi = K_COIL_R * (1 - K_COIL_TOL), K_COIL_R * (1 + K_COIL_TOL)
    i0 = (vhi - 0.7) / rk_lo                                         # largest coil current (cold coil, low VF)
    rds_hot = LS_RDS * 1.8
    i_do = (vlo - a["A_VF"]) / (rk_hi + rds_hot)                     # DO path, hot coil, hot Q_LS1
    i_hold = (vlo - a["A_VF"]) / (rk_hi + rds_hot + HS_RDS * 1.8)   # hold path: + IRFR9214 hot
    v_do_off = [v * sum(R_GATE_DIV) / R_GATE_DIV[1] for v in LS_VTH]    # DO level at which Q_LS1 opens
    coil_p_off = v_do_off[1] - 0.7                                    # highest COIL+ when the DO path opens
    coil_p_hold = vhi - 0.1 * HS_RDS - 0.7                            # COIL+ while fed by the hold high side
    vv = (v_var(i0, -VAR_TOL), v_var(i0, VAR_TOL))
    i_drop = K_DROPOUT / K_COIL_R
    L, R = a["A_L_COIL_K"], K_COIL_R
    t_var = demag(lambda i: v_var(i, -VAR_TOL), i0, i_drop, L, R)
    t_ref = demag(lambda i: K_CLAMP_MIN, i0, i_drop, L, R)
    t_doc = demag(lambda i: -DO_CLAMP[1] + a["A_VF"], i0, i_drop, L, R)
    e_coil = 0.5 * L * i0 ** 2
    vs_out = vhi - DO_CLAMP[0]
    pre_clamp = (TVS33_VBR[0] + 0.7, TVS33_VBR[1] + a["A_VF"])
    ir_hi = vhi / (PR_COIL_R * (1 - PR_COIL_TOL))                   # relay: no series diode
    states = [
        dict(state="K_x_MAIN off, hold inactive (normal opening)", opens="Q_LS1 (gate = K_x_MAIN)",
             path="varistor", v_coil="%.0f-%.0f" % vv, v_ds=coil_p_off + vv[1], v_do=0.0, e_do_mJ=0.0),
        dict(state="hold ends, channel 2 first (DO already off)", opens="Q_LS2",
             path="varistor", v_coil="%.0f-%.0f" % vv, v_ds=coil_p_hold + vv[1], v_do=0.0, e_do_mJ=0.0),
        dict(state="hold ends, channel 1 first", opens="Q_HS, then Q_LS2 (gate supply gone, <= %.0f us)"
             % (a["A_T_LS_OFF"] * 1e6), path="SYS clamp for <= %.0f us, then varistor" % (a["A_T_LS_OFF"] * 1e6),
             v_coil="%.0f-%.0f" % vv, v_ds=vv[1] + 1.0, v_do=DO_CLAMP[0],
             e_do_mJ=i0 * -DO_CLAMP[0] * a["A_T_LS_OFF"] * 1e3),
        dict(state="K_x_MAIN off while the hold holds (E-stop in a fault)", opens="none: coil stays fed by Q_HS + Q_LS2",
             path="no flyback; S2M on K_x_MAIN reverse-biased (%.1f V)" % coil_p_hold, v_coil="-", v_ds=0.0,
             v_do=0.0, e_do_mJ=0.0),
        dict(state="K_x_PRE off (precharge relay)", opens="Q_LSP (gate = K_x_PRE)",
             path="S2M + SMBJ33A across the coil", v_coil="%.1f-%.1f" % pre_clamp,
             v_ds=v_do_off[1] + pre_clamp[1], v_do=0.0, e_do_mJ=0.0)]
    faults = [
        ("Q_LS1 or Q_LS2 drain-source short", "no: still needs K_x_MAIN or the hold high side",
         "opening through the SYS clamp: %.0f mJ <= %.1f J, OUT >= %.1f V, VS-VOUT %.1f V <= %.0f V; demag %.1f ms "
         "instead of %.1f ms" % (e_coil * 1e3, DO_CLAMP_J, DO_CLAMP[0], vs_out, DO_VSOUT_ABS, t_doc * 1e3,
                                  t_var * 1e3), "proof test, contactor closed, channel-1-only window: stays closed"),
        ("Q_HS (IRFR9214) short, or channel-1 AND/BSS138 stuck on", "no: still needs Q_LS1 (DO) or Q_LS2 (channel 2)",
         "none", "readback at rest: RB1 = 1 (hold high-side output present)"),
        ("channel-2 driver stuck on (Q_LS2 follows the hold high side)", "no: still needs Q_HS (channel 1)", "none",
         "proof test, channel-1-only window: stays closed"),
        ("S2M in the K_x_MAIN feed short", "no", "hold current back-feeds K_x_MAIN and turns Q_LS1 on",
         "proof test, channel-1-only window: stays closed"),
        ("S2M in the hold feed or any hold switch open", "no (hold lost)", "none",
         "proof test, both-channel window: contactor drops"),
        ("S10K30 open", "no", "Q_LS avalanche at >= %.0f V: ~%.0f mJ > EAS %.0f mJ -> may fail short (row 1)"
         % (LS_VDS, e_coil * LS_VDS / (LS_VDS - coil_p_hold) * 1e3, LS_EAS * 1e3), "after it fails: row 1"),
        ("K_x_MAIN shorted to another DO line in the harness", "yes, from 24V_DO", "normal",
         "still cut by both E-stop supply switches (the harness has no unswitched 24 V conductor)")]
    return dict(i0=i0, i_do=i_do, v_pick=i_do * rk_hi, i_hold=i_hold, v_hold=i_hold * rk_hi, v_do_off=v_do_off,
                vv=vv, v_var_drop=v_var(i_drop, -VAR_TOL), t_var=t_var, t_ref=t_ref, t_doc=t_doc, e_coil=e_coil,
                vs_out=vs_out, pre_clamp=pre_clamp, ir_hi=ir_hi, states=states, faults=faults,
                v_ds_max=max(s["v_ds"] for s in states), v_gs_max=vhi * R_GATE_DIV[1] / sum(R_GATE_DIV),
                v_gs_min=(vlo - 1.0) * R_GATE_DIV[1] / sum(R_GATE_DIV), v_hs_gs=vhi / 2,
                v_hs_ds=vhi - DO_CLAMP[0] + a["A_VF"],          # hold output dragged to the SYS clamp (fault case)
                e_do_max_mJ=max(s["e_do_mJ"] for s in states))


def interface(cd):
    vlo, vhi = V24
    rows = []
    i_gate = vhi / sum(R_GATE_DIV)
    rows.append(dict(coil="HVC43 main (K_x_MAIN, DO2/DO4)", I_A="%.2f-%.2f" % (cd["i_do"], cd["i0"] + i_gate),
                     V_coil_min=cd["v_pick"], pickup="<= %.0f V" % K_PICKUP, E_off_mJ=cd["e_coil"] * 1e3,
                     clamp="S10K30 across the coil, only flyback path", limit="%.2f-%.2f A" % DO_ILIM_MAIN,
                     ok=cd["v_pick"] > K_PICKUP and cd["i0"] + i_gate < DO_ILIM_MAIN[0] and cd["e_coil"] < VAR_WMAX))
    rr_hi = PR_COIL_R * (1 + PR_COIL_TOL)
    ir_lo = (vlo - LS_RDS * 1.8 * 0.1) / rr_hi
    rows.append(dict(coil="G7L-X precharge (K_x_PRE, DO1/DO3)", I_A="%.3f-%.3f" % (ir_lo, cd["ir_hi"] + i_gate),
                     V_coil_min=ir_lo * rr_hi, pickup="<= %.0f V (75 %%)" % (24 * PR_OPERATE),
                     E_off_mJ=0.5 * a["A_L_COIL_R"] * cd["ir_hi"] ** 2 * 1e3,
                     clamp="S2M + SMBJ33A across the coil (Omron p5)", limit="%.2f-%.2f A" % DO_ILIM_SMALL,
                     ok=ir_lo * rr_hi > 24 * PR_OPERATE and cd["ir_hi"] < DO_ILIM_SMALL[0]
                     and 24 <= cd["pre_clamp"][0] and cd["pre_clamp"][1] <= 48))
    i_pd = vhi / 47e3
    rows.append(dict(coil="discharge drive (K_DISCH, DO5): 2 x TPSI3050 two-wire via BAS16",
                     I_A="%.4f-%.3f (start %.0f mA each)" % (2 * TPSI_I_EN, 2 * a["A_IEN_MAX"] + 2 * i_pd + 2 * vhi / R_EN_PD,
                                                            TPSI_I_START * 1e3),
                     V_coil_min=vlo - 1.0, pickup=">= %.1f V EN" % TPSI_EN_MIN, E_off_mJ=0.0,
                     clamp="none needed", limit="%.2f-%.2f A" % DO_ILIM_SMALL,
                     ok=vlo - 1.0 > TPSI_EN_MIN and DO_SLEW[0] >= TPSI_EN_SLEW))
    rows.append(dict(coil="proof-test input (DO_SPARE, DO8): 2 x 100 k / 15 k", I_A="%.4f" % (2 * vhi / sum(R_TST_DIV)),
                     V_coil_min=vlo * R_TST_DIV[1] / sum(R_TST_DIV), pickup=">= %.2f V (VT+)" % VT_P[1], E_off_mJ=0.0,
                     clamp="none", limit="%.2f-%.2f A" % DO_ILIM_SMALL,
                     ok=vlo * R_TST_DIV[1] / sum(R_TST_DIV) > VT_P[1] and vhi * R_TST_DIV[1] / sum(R_TST_DIV) < LVC_VIMAX))
    rows.append(dict(coil="IMD switches (IMD_SW_P DO6, IMD_SW_N DO7): TPSI3050 two-wire, 2 SiC each",
                     I_A="%.4f-%.4f" % (TPSI_I_EN + vlo / 47e3, a["A_IEN_MAX"] + i_pd), V_coil_min=vlo,
                     pickup=">= %.1f V EN" % TPSI_EN_MIN, E_off_mJ=0.0, clamp="none needed",
                     limit="%.2f-%.2f A" % DO_ILIM_MAIN, ok=vlo > TPSI_EN_MIN))
    # FB: +24V -- 1.5 k -- FB -- harness (own GND return) -- ISO1212 (2.05-2.75 mA above VIL); NC mirror contact
    # FB -> GND. Loads on FB when the contactor is closed: ISO1212, two 100 k / 25.5 k logic dividers, and - only
    # while K_DISCH is off (E-stop with the contactor held or welded) - the TPSI3050 EN through BAS16 + 100 k EN
    # pull-down.
    r_d = sum(R_FB_DIV)
    k_d = R_FB_DIV[1] / r_d
    v_cl_min = vlo - R_FB_PULLUP * (DI_I[1] + 2 * vlo / r_d)                   # contactor closed, K_DISCH on
    v_estop_min = (vlo - R_FB_PULLUP * (DI_I[1] + 2 * vlo / r_d + a["A_IEN_MAX"] + vlo / R_EN_PD))
    v_cl_max = vhi - R_FB_PULLUP * (DI_I[0] + 2 * vhi / r_d * 0.98)
    i_wet = (vlo / R_FB_PULLUP, vhi / R_FB_PULLUP)
    v_lo = i_wet[1] * K_AUX[4]
    fb = dict(V_DI_closed=(v_cl_min, v_cl_max), V_estop_min=v_estop_min, V_DI_open=v_lo, I_wet=i_wet,
              V_aux_open=v_cl_max, P_pullup=vhi ** 2 / R_FB_PULLUP, V_logic=(v_estop_min * k_d * 0.99, v_cl_max * k_d * 1.01),
              V_en_estop=v_estop_min - 0.8,
              ok=min(v_cl_min, v_estop_min) > DI_VIH and v_lo < DI_VIL and i_wet[0] >= K_AUX[2]
              and v_cl_max <= K_AUX[1] and v_estop_min * k_d * 0.99 > LVC_VIH and v_cl_max * k_d * 1.01 < LVC_VIMAX
              and v_estop_min - 0.8 > TPSI_EN_MIN)
    return dict(rows=rows, fb=fb, slew_ok=DO_SLEW[0] >= TPSI_EN_SLEW)


# =====================================================================================================
# 8. Insulation-resistance measurement (two-state switched-resistor bridge)
# =====================================================================================================
G_T = 1 / (N_IMD * R_IMD_E)
G_A = 1 / (N_DIV * R_DIV_E + R_DIV_B)       # AUX1 (PE-BUS-) and AUX2 (PE-A+) dividers are fixed paths to PE


def pe_voltage(V, g_ip, g_in, sp, sn, g_a1=G_A, g_a2=G_A, g_t=G_T, g_lp=0.0, g_ln=0.0):
    gp = g_ip + g_a2 + (g_t if sp else g_lp)
    gn = g_in + g_a1 + (g_t if sn else g_ln)
    return V * gp / (gp + gn)


def imd_solve(V1, u1, V2, u2, g_a1=G_A, g_a2=G_A, g_t=G_T):
    """State P (SW_P closed) and state N (SW_N closed) -> g_ip, g_in. Returns R_iso = 1/(g_ip+g_in)."""
    A_ = np.array([[V1 - u1, -u1], [V2 - u2, -u2]])
    b_ = np.array([u1 * g_a1 - (V1 - u1) * (g_a2 + g_t), u2 * (g_a1 + g_t) - (V2 - u2) * g_a2])
    g_ip, g_in = np.linalg.solve(A_, b_)
    return 1 / (g_ip + g_in), g_ip, g_in


def imd():
    k_div = R_DIV_B / (N_DIV * R_DIV_E + R_DIV_B)
    lsb = 2 * AMC_V_CLIP / k_div / 2 ** a["A_ADC_BITS"]
    res = {}
    for V in (V_MIN, V_MAX):
        for R_true in (10e3, 33e3, 100e3, 330e3, 1e6, 3.3e6, 10e6):
            for split in (0.5, 0.0, 1.0):
                g_ip, g_in = split / R_true, (1 - split) / R_true
                for leak in (Q_IDSS_TYP / Q_VDS, Q_IDSS_MAX / Q_VDS):
                    for s_g, s_o, s_t, s_a in ((1, 1, 1, 1), (-1, 1, -1, 1), (1, -1, 1, -1), (-1, -1, -1, -1)):
                        gt, ga = G_T * (1 + s_t * TNPV_TOL), G_A * (1 + s_a * 0.002)
                        u1 = pe_voltage(V, g_ip, g_in, 1, 0, ga, ga, gt, 0, leak)
                        u2 = pe_voltage(V, g_ip, g_in, 0, 1, ga, ga, gt, leak, 0)
                        gain = 1 + s_g * (AMC_V_EG + AMC_V_TCEG * a["A_DT_SENSE"])
                        off = s_o * (AMC_V_VOS + AMC_V_TCVOS * a["A_DT_SENSE"]) / k_div
                        Vm = V * (1 + s_g * 0.003)
                        R_est, _, _ = imd_solve(Vm, u1 * gain + off + lsb / 2, Vm, u2 * gain + off - lsb / 2)
                        key = (V, R_true, "typ" if leak < 1e-8 else "max")
                        res[key] = max(res.get(key, 0.0), abs((R_est - R_true) / R_true) if R_est > 0 else math.inf)
    dU = {V: {R: V * G_T / (G_T + 2 * G_A + 1 / R) for R in (33e3, 1e6)} for V in (V_MIN, V_MAX)}
    tau = {c: (c + 4 * CY) / (G_T + 2 * G_A + 1 / 1e6) for c in (a["A_C_PV_PE"], a["A_C_PV_PE_110"])}
    # PE-open check (lesson 1): PE_T open -> the strings move nothing: swing 0; PE_D open -> both dividers float at
    # V/2 (+/-), no swing. A healthy system swings >= dU(10 k) even at 10 kOhm; below that one AUX reads ~0 V.
    pe_open = dict(swing_V=0.0, aux_V=V_MIN / 2, min_healthy_swing_V=V_MIN * G_T / (G_T + 2 * G_A + 1 / 10e3))
    return dict(R_T=1 / G_T, R_A=1 / G_A, lsb_V=lsb, err=res, dU=dU, tau=tau, pe_open=pe_open,
                settle_s={c: 3 * t for c, t in tau.items()}, pred_s=3 * a["A_T_PRED"] + 0.5,
                tau_s=tau[a["A_C_PV_PE"]], I_test_mA=V_MAX * G_T * 1e3,
                P_test_W=V_MAX ** 2 * G_T, P_elem_W=(V_MAX / N_IMD) ** 2 / R_IMD_E, elem_v=V_MAX / N_IMD,
                threshold=V_MAX / 30e-3, span=AMC_V_CLIP / k_div)


# =====================================================================================================
# 9. Surge protection level vs device ratings, 10. Y capacitance vs touch / leakage current
# =====================================================================================================
def precharge_null(dv, ir):
    """INT-19: |V_term - V_bus| <= DV_OK is judged on VAX - VA (VBX - VB): two channels. Firmware nulls their
    mismatch (gain ratio at the operating voltage) every time the main contactor is closed at low current, so only
    what drifts between that null and the next precharge remains."""
    k = dv["k"]
    res = {}
    for V in (V_MIN, V_MAX):
        per = ((TNPV_TCR + TNPW_TCR) + AMC_V_TCEG) * a["A_DT_NULL"] + AMC_V_NL + \
            AMC_V_TCVOS * a["A_DT_NULL"] / k / V
        res[V] = dict(worst_V=2 * per * V, rss_V=math.sqrt(2) * per * V, raw_V=2 * dv["budget"][V]["worst_cal"] * V)
    real = [r for r in ir if r["C_uF"] < 500]
    i10 = max(r["I_bound"] for r in real)
    dv_real = DV_OK + res[V_MAX]["worst_V"]
    return dict(res=res, dv_real=dv_real, i_inrush=i10 * dv_real / DV_OK, i10=i10,
                i_raw=i10 * (DV_OK + res[V_MAX]["raw_V"]) / DV_OK)


# Hongfa HFE85V-300M/1000 (Hongfa-HFE85V-300M.pdf, 2024 Rev 1.00): p101 300 A at 85 C, coil 60 W for 0.2 s then
# 4.3 W (built-in economiser, step drive only), 1 Form B aux, dielectric coil-contacts and contacts-aux 3000 V AC 1 min;
# p104 breaking 1000 V: 300 A x 100, 500 A x 10, 800 A x 1; p105 functional (130 C) carry curve below
HF85 = [(450, 400.0), (600, 150.0), (1000, 23.0), (1200, 12.0)]


def hongfa(lo):
    """Smallest carry/pre-arc margin of one HFE85V-300M against the 200 A gBat (-10 % current) from band start lo to
    1.2 kA (log-log, first segment extrapolated below 450 A)."""
    def tk(i):
        seg = [(p_, q_) for p_, q_ in zip(HF85, HF85[1:]) if p_[0] <= i <= q_[0]] or [(HF85[0], HF85[1])]
        (i1, t1), (i2, t2) = seg[0]
        return t1 * (i / i1) ** (math.log(t2 / t1) / math.log(i2 / i1))
    return min(tk(i) / interp_loglog(E200, 0.9 * i) for i in range(int(lo), 1201, 2))


def v_mov(i):
    """TVT25751 maximum clamping voltage at i (8/20 us): Thinking TVR20751 curve at i / MOV_K25 (the 25 mm part reaches
    the same 1240 V at 1.5 x the class current, TVT p8 vs p9)."""
    return interp_loglog(MOV_VC20, i / MOV_K25)


def surge():
    """Sec. 9 (IC-01/02/03/12/13). Effective protection level of the on-board Y at In: two varistors in series (pole-PE
    = MOV1 + MOV3, pole-pole = MOV1 + MOV2) plus L di/dt of the loop. Insulation requirement per D-032 / insulation
    report sec. 3.1: Up,eff <= 4.0 kV (preferred series) -> basic 4 kV, reinforced one step up = 6 kV; functional
    pole-pole >= 1.1 x Up,eff. Without the credit: basic 6 kV, reinforced 8 kV."""
    v1, v_l = v_mov(SPD_IN), a["A_L_SPD"] * SPD_IN / a["A_T_DIDT"]
    up = 2 * v1 + v_l
    req = dict(reinforced=6000.0, basic=4000.0, functional=1.1 * up)
    rows = [("AMC3330 VIMP (SBASA34B p7)", AMC_VIMP, "reinforced"),
            ("AMC3302 VIOSM (SBASA11B p7; no VIMP given)", AMC_I_VIOSM, "reinforced"),
            ("TPSI3050-Q1 VIMP (SLVSFJ7D p6)", TPSI_VIMP, "reinforced"),
            ("CNY65B VIOTM (CNY65.pdf p1)", 12000.0, "reinforced"),
            ("HVC43 coil/aux-main Uimp 8 kV, as derated to 4000 m (insulation report IC-05)", 6600.0, "reinforced"),
            ("fuse bases HPBB11PPR / HPBB21PPR / ETI NH Uimp (HP10NH p2-p3)", F_UIMP_BASE, "basic"),
            ("Miba RST 200 insulation 3.5 kV rms x 1.41 (p15)", RST_DIEL * math.sqrt(2), "basic"),
            ("Y capacitor VY1 class Y1 (IEC 60384-14 Y1 = 8 kV impulse)", 8000.0, "basic"),
            ("IMD string 8 x TNPV1210 1000 V (Umax, continuous)", N_IMD * TNPV1210_UMAX, "basic"),
            ("X capacitors: RFQ impulse rating (PA-07, IC-10)", CX_VIMP, "functional"),
            ("sense divider 6 x TNPV1206 700 V (Umax, continuous)", N_DIV * TNPV_UMAX, "functional")]
    rows = [dict(item=n, rating_V=r, need=k, req_V=req[k], margin=r / req[k]) for n, r, k in rows]
    i2t = SPD_IN ** 2 * 14e-6
    vx = a["A_V_TRIP_OS"]
    mon = dict(i_min=(V_MIN - 1.2) / (N_MON * R_MON), i_max=(vx - 1.2) / (N_MON * R_MON), p_max=vx ** 2 / (N_MON * R_MON),
               v_elem=vx / N_MON, v_elem_surge=v1 / (N_MON / 2), p_elem=(vx / N_MON) ** 2 / R_MON)
    return dict(Up=up, v1=v1, v_l=v_l, req=req, rows=rows, uc=2 * MOV_VDC, i2t=i2t, mon=mon,
                fuse_ratio=SPD_FUSE["i2t_pre"] / i2t, i15_ratio=MOV_I15 / SPD_IN, v_first_fault=a["A_V_TRIP_OS"] / 2,
                imd_avalanche_mA=(up - Q_VDS) / (N_IMD * R_IMD_E) * 1e3)


def ycap():
    c_mod = 2 * 2 * CY * (1 + CY_TOL)
    i_lf, i_hf = 2 * math.pi * 150 * c_mod * a["A_VCM_LF"], 2 * math.pi * 20e3 * c_mod * a["A_VCM_HF"]
    return dict(C_port=c_mod / 2, C_module=c_mod, I_150Hz_mA=i_lf * 1e3, I_20kHz_mA=i_hf * 1e3,
                I_total_mA=math.hypot(i_lf, i_hf) * 1e3, V_dc_stress=V_MAX / CY_VDC,
                I_array_mA=2 * math.pi * 150 * a["A_C_PV_PE"] * a["A_VCM_LF"] * 1e3)


# =====================================================================================================
# 11. ngspice: precharge + main-contactor closing transient (stiff battery source, 1 mF envelope)
# =====================================================================================================
def spice(C):
    t_close = R_PRE * C * math.log(V_MAX / DV_OK)
    data = os.path.join(OUT, "spice_precharge.txt")         # raw wrdata (tens of MB) - deleted after parsing
    deck = os.path.join(SPICE_DIR, "port_precharge.cir")
    txt = """* port_precharge.cir - generated by sim/port_design.py (do not edit). Precharge through R_PRE, then the main
* contactor closes when V_term - V_bus = DV_OK. Stiff battery source (worst case for inrush).
.param vbat={V} rs={Rs} ls={Ls} rpre={Rp} cbus={C} esr={Esr} lloop={Ll} rloop={Rl} cx={Cx} esrx={Ex} tclose={tc}
VBAT src 0 DC {{vbat}}
RS src s1 {{rs}}
LS s1 term {{ls}}
CX1 term x1 {{cx}} IC={V}
RX1 x1 0 {{esrx}}
CX2 term x2 {{cx}} IC={V}
RX2 x2 0 {{esrx}}
* precharge relay (closes at t = 0) + resistor
SPRE term p1 ctlpre 0 swrel
RPRE p1 bus {{rpre}}
* main contactor (closes at tclose) + busbar loop
SMAIN term m1 ctlmain 0 swrel
VIK m1 m2 DC 0
LLOOP m2 m3 {{lloop}}
RLOOP m3 bus {{rloop}}
* bus capacitor bank
CBUS bus b1 {{cbus}} IC=0
RESR b1 0 {{esr}}
VPRE ctlpre 0 PWL(0 0 1u 1)
VMAIN ctlmain 0 PWL(0 0 {{tclose}} 0 {{tclose+1e-6}} 1)
.model swrel sw vt=0.5 vh=0.1 ron=1m roff=1e9
.tran 10u {te} 0 2u uic
.control
run
wrdata {data} v(bus) i(VIK) v(term)
.endc
.end
""".format(V=V_MAX, Rs=a["A_RS_BATT"], Ls=a["A_LS_BATT"], Rp=R_PRE, C=C, Esr=a["A_ESR_BUS"], Ll=a["A_L_LOOP"],
           Rl=a["A_R_LOOP"] - 1e-3, Cx=CX, Ex=CX_ESR, tc=t_close, te=t_close + 3e-3, data=os.path.basename(data))
    os.makedirs(SPICE_DIR, exist_ok=True)
    open(deck, "w").write(txt)
    r = subprocess.run(["ngspice", "-b", deck], capture_output=True, text=True, cwd=OUT)
    ensure(r.returncode == 0 and os.path.exists(data), "ngspice failed:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    ver = [ln for ln in subprocess.run(["ngspice", "-v"], capture_output=True, text=True).stdout.splitlines()
           if "ngspice-" in ln]
    d = np.loadtxt(data)
    os.remove(data)
    t, vbus, ik, vterm = d[:, 0], d[:, 1], d[:, 3], d[:, 5]
    keep = (t >= t_close - 1e-4) | (np.diff(np.floor(t / 1e-3), prepend=-1) > 0)   # ~1 ms grid + the closing window
    np.savetxt(os.path.join(OUT, "spice_precharge.csv"), d[keep][:, [0, 1, 3, 5]], delimiter=",", fmt="%.6g",
               header="t_s,v_bus_V,i_contactor_A,v_term_V", comments="")
    after = t >= t_close
    return dict(deck=os.path.relpath(deck, REPO), t_close=t_close, t_dv_spice=float(t[np.argmax(vterm - vbus <= DV_OK)]),
                i_inrush_spice=float(np.max(np.abs(ik[after]))), t=t, vbus=vbus, ik=ik,
                ngspice=(ver[0].strip("* ") if ver else "ngspice"))


# =====================================================================================================
# plots, report, spec
# =====================================================================================================
def plots(pc, sp, co, im, hold, C_cases):
    fig, ax = plt.subplots(2, 2, figsize=(12, 8.5))
    t = np.linspace(0, 1.5, 600)
    for name, C in C_cases:
        ax[0, 0].plot(t, V_MAX * (1 - np.exp(-t / (R_PRE * C))), label="%s %.0f uF" % (name, C * 1e6))
    ax[0, 0].plot(sp["t"], sp["vbus"], "k--", lw=1, label="ngspice 1 mF")
    ax[0, 0].plot(t, K_CHK * V_MAX * (1 - np.exp(-t / pc["tau_max"])), "r:", label="abort below (K_CHK curve)")
    ax[0, 0].axvline(pc["t_out"], color="r", lw=0.8)
    ax[0, 0].set(xlabel="t (s)", ylabel="V_bus (V)", title="Precharge at 1000 V, R = %.0f ohm" % R_PRE)
    ax[0, 0].legend(fontsize=6)
    sw = [s for s in pc["sweep"] if s["C_uF"] == C_RANGE[1] * 1e6 and not math.isinf(s["R_load_over_R"])]
    ax[0, 1].semilogx([max(s["R_load_over_R"], 0.01) for s in sw], [s["E_J"] for s in sw], "o-")
    ax[0, 1].axhline(pc["E_allow"], color="r", label="RST 200 pulse rating at tau 0.5 s = %.0f J" % pc["E_allow"])
    ax[0, 1].set(xlabel="bus load R_L / R_pre (0.01 = shorted)", ylabel="energy in R_pre (J)",
                 title="Fault during precharge, 1 mF, 1000 V")
    ax[0, 1].legend(fontsize=7)
    m = sp["t"] >= sp["t_close"] - 50e-6
    ax[1, 0].plot((sp["t"][m] - sp["t_close"]) * 1e6, sp["ik"][m])
    ax[1, 0].set(xlabel="t after main contactor closes (us)", ylabel="contactor current (A)",
                 title="Closing inrush at dV = %.0f V (ngspice, battery source)" % DV_OK)
    for Ir, c in co.items():
        cv = c["v"]["curve"]
        ii = np.logspace(math.log10(cv[0][0]), math.log10(cv[-1][0]), 100)
        ax[1, 1].loglog(ii, [t_fuse(cv, i) for i in ii], label="%s pre-arc (%d A port)" % (c["v"]["fuse"], Ir))
        ax[1, 1].loglog(ii / 0.9, [t_fuse(cv, i) for i in ii], ":", lw=0.8)
    kk = np.logspace(math.log10(290), math.log10(5000), 60)
    ax[1, 1].loglog(kk, [t_carry(i) for i in kk], "k", label="HVC43-250A carry 85 C (p6)")
    ax[1, 1].axvspan(hold["lo"], hold["hi"], color="orange", alpha=0.3, label="hold threshold band")
    ax[1, 1].axvline(K_CUTOFF_1000, color="g", ls=":", label="contactor cut-off 450 A @1000 V")
    ax[1, 1].set(xlabel="current (A)", ylabel="time (s)", title="Fuse / contactor coordination (dotted = fuse -10 %)",
                 ylim=(1e-4, 1e4))
    ax[1, 1].legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "port_design.png"), dpi=110)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for V in (V_MIN, V_MAX):
        for lk in ("typ", "max"):
            rs = sorted({k[1] for k in im["err"] if k[0] == V and k[2] == lk})
            ax.loglog(rs, [100 * im["err"][(V, r, lk)] for r in rs], "o-", label="%.0f V, IDSS %s" % (V, lk))
    ax.axvline(33e3, color="r", ls=":")
    ax.axvline(1e6, color="g", ls=":")
    ax.set(xlabel="true insulation resistance (ohm)", ylabel="worst-case estimate error (%)",
           title="IMD two-state bridge, R_t = %.0f k" % (im["R_T"] / 1e3))
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "imd_error.png"), dpi=110)
    plt.close(fig)


def md_table(rows, cols, fmt):
    s = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    for r in rows:
        s += "| " + " | ".join(f.format(r[c]) for c, f in zip(cols, fmt)) + " |\n"
    return s


def band_txt(b):
    return "%.0f" % b[0] if b[1] - b[0] < 1 else "%.0f-%.0f" % b


def report(cn, pc, ir, dc, dv, cs, co, hold, itf, im, sg, yc, sp, cd, tl, pt, pn, bp):  # noqa: C901
    irmax = [r for r in ir if r["C_uF"] == C_RANGE[1] * 1e6][0]
    c135, c180 = co[135], co[180]
    L = ["# DC port design - calculation report", "",
         "Generated by `sim/port_design.py`. **Calculated / simulated, not bench-validated.** Requirements: "
         "PV-03..PV-08, PV-C3..C5, ECO-07, ECO-09, ECO-10. Two ratings: **135 A** (PV-P75, 3 cells, default "
         "PV-PORT build, `port(..., rating=135)`) and **180 A** (PV-P100/110, 4 x 45 A, `rating=180`).", "",
         "## Inputs", ""]
    L += ["- " + n for n in cn]
    L += ["- Design envelope also evaluated: 100 uF and 1 mF per port.", "",
          "### Assumptions (ours, not datasheet)", "", "| id | value | meaning |", "|---|---|---|"]
    L += ["| %s | %g | %s |" % (k, v[0], v[1]) for k, v in A.items()]
    L += ["", "## 1 Precharge (ECO-07: resistor + relay) - unchanged for both ratings", "",
          "R_pre = %.0f ohm, **Miba RST 200** pulse resistor (chassis, M5; RFQ for the value - the series spans "
          "10-300 ohm, +/-5 %%, 500 ppm/K, <= 1000 V DC precharge voltage, published pulse ratings: PA-05). Complete "
          "when |V_term - V_bus| <= %.0f V (measured after the VA/VAX null, sec. 7.5); supervision aborts if V_bus < "
          "%.1f x V_term x (1 - exp(-t/tau_max)) after %.0f ms blanking (tau_max = R_pre x 1 mF = %.0f ms) or at "
          "t_out = %.2f s. With R_pre at +5 %% and +100 K (%.0f ohm) a healthy 1 mF bus stays >= %.2f x the abort "
          "curve, and t_ok stays <= %.3f s < t_out." % (R_PRE, DV_OK, K_CHK, a["A_T_BLANK"] * 1e3,
                                                         pc["tau_max"] * 1e3, pc["t_out"], pc["r_hi"], pc["chk_margin"],
                                                         max(r["t_ok_hi_s"] for r in pc["rows"])), "",
          md_table(pc["rows"], ["case", "C_uF", "V", "tau_ms", "t_ok_s", "I_pk", "P_pk_kW", "E_J", "E_allow_J"],
                   ["{}", "{:.0f}", "{:.0f}", "{:.1f}", "{:.3f}", "{:.2f}", "{:.2f}", "{:.0f}", "{:.0f}"]),
          "E_allow = Miba single-pulse rating at tau = R_pre C (2300 J at 0.1 s, 3150 J at 0.2 s, 4000 J at 0.5 s; "
          "below 0.1 s extrapolated on the 0.1-0.2 s slope).", "",
          "Fault during precharge at 1000 V (load across the bus; 0 = shorted bus), incl. %.0f ms decision and %.0f ms "
          "relay release:" % (a["A_T_DECIDE"] * 1e3, PR_T_RELEASE * 1e3), "",
          md_table([s for s in pc["sweep"] if s["R_load_over_R"] in (0.0, 1.0, 3.8, 4.2, 10.0, math.inf)],
                   ["R_load_over_R", "C_uF", "outcome", "t_end_s", "E_J"], ["{:g}", "{:.0f}", "{}", "{:.3f}", "{:.0f}"]),
          "- Shorted bus: %.2f kW rectangular in R_pre; the RST 200 survives %.2f s (rating at tau = 2t); the "
          "slow-rise check opens the relay after %.3f s (%.0f J). Worst fault %.0f J/attempt, %d attempts = %.0f J "
          "(<= %.0f J, 0.5 s rating; short-time overload 3 kW x 3 s = %.0f J), then %d s lock-out (cooling at half "
          "the 200 W / 120 s rating)." % (V_MAX ** 2 / R_PRE / 1e3, pc["survival_shorted_s"], pc["shorted"]["t_end_s"],
                                          pc["shorted"]["E_J"], pc["E_worst"], N_ATTEMPTS, pc["attempts_E"],
                                          pc["E_allow"], RST_E_STO, math.ceil(pc["lockout_s"] / 10) * 10),
          "- G7L-2A-X breaks %.2f A at 1000 V in normal polarity (25 A rated, p1); reverse polarity is rated only to "
          "600 V (p2), so precharge is always terminal -> bus. Miba rates 'typical' pulse loads only, from <= 85 C "
          "initial temperature - qualification test still required." % (V_MAX / (R_PRE * (1 - RST_TOL))), "",
          "## 2 Main contactor closing inrush at dV = %.0f V" % DV_OK, "",
          md_table(ir, ["case", "C_uF", "I_batt", "t_peak_us", "I_pv", "I_xcap", "I_bound"],
                   ["{}", "{:.0f}", "{:.0f}", "{:.0f}", "{:.1f}", "{:.1f}", "{:.0f}"]),
          "TDK capacitive make test: %.0f A at %.0f V, 70 000 operations (HVC43MC p3); TDK asks for >= 98 %% precharge "
          "(p11). With the real capacitances (135-360 uF) the bound stays at or below %.0f A. ngspice (1 mF envelope): "
          "%.0f A vs %.0f A calculated." % (K_MAKE_CAP[1], K_MAKE_CAP[0],
                                             max(r["I_bound"] for r in ir if r["C_uF"] < 500), sp["i_inrush_spice"],
                                             irmax["I_bound"]), "",
          "## 3 Discharge (PA-03, INT-01) - same for both ratings", "",
          "Passive %d x 47 k 2512 = %.0f k, with the VA divider and gate-bias string %.0f k: %.2f W at 1000 V, %.0f V / "
          "%.3f W per element. Active (K_DISCH de-energised = discharging): R_dis = %d x %.0f ohm **Miba RST 200** in "
          "series (RFQ value) via C2M1000170D, %.0f W peak (%.2f A). Turn-on through the 10 M bias string: Miller "
          "energy in the SiC <= %.0f mJ at 1000 V." % (N_BLEED, N_BLEED * R_BLEED_E / 1e3, dc["r_passive"] / 1e3,
                                                         dc["P_passive_W"], dc["V_bleed_elem"], dc["P_bleed_elem_W"], N_DIS,
                                                         R_DIS_E, dc["P_dis_pk_W"], dc["I_dis_pk"], dc["e_miller_mJ"]), "",
          md_table(dc["rows"], ["case", "C_uF", "t_passive_s", "t_active_s", "E_J", "E_per_R_J", "E_allow_J"],
                   ["{}", "{:.0f}", "{:.0f}", "{:.2f}", "{:.0f}", "{:.0f}", "{:.0f}"]),
          "Three independent protections against a discharge into a live source:", "",
          "1. **Hardware inhibit while the main contactor is closed** (INT-01): each port's TPSI3050 has its own EN, "
          "fed by K_DISCH OR FB_x_MAIN through two BAS16. FB is +24V through %.1f k whenever the mirror contact is open "
          "(contactor closed - also welded), so an E-stop, a K_DISCH wire break or a SYS DO5 failure cannot start the "
          "discharge of a closed port: EN stays >= %.1f V (needs %.1f V). FB keeps an already-running TPSI3050 on "
          "(1.9 mA); it cannot start one (27 mA start-up) - SYS energises K_DISCH first at every power-up, so SYS may "
          "now cut K_DISCH unconditionally." % (R_FB_PULLUP / 1e3, itf["fb"]["V_en_estop"], TPSI_EN_MIN),
          "2. **Time limit** on the HV side (works without any 24 V): DIS_G charges %.1f uF through %.1f M; a MMBT3906 "
          "compares it with DIS_G/2 and fires a MMBT3906/MMBT3904 latch that clamps the gate to ~%.2f V (SiC VGS(th) "
          ">= %.1f V hot). Limit **%.2f-%.2f s** (zener at low current, divider, VBE + diode, leakage, X7R -35/+10 %%) "
          "vs the longest needed discharge %.2f s (1 mF envelope, R_dis +5 %%). Latch holding: %.1f uA available at "
          "250 V vs ~%.1f uA needed; reset when K_DISCH re-energises (BSS138 shorts DIS_G)." %
          (C_TIM * 1e6, R_TIM / 1e6, tl["v_latched"], tl["vth_sic"], tl["t_min"], tl["t_max"], dc["t_dis_max"],
           tl["i_avail_uA"], tl["i_hold_uA"]),
          "3. **Thermal cut-off**: Honeywell 3100U00031461 (closes on rise at 82 +/-3 C, reopens at %.0f-%.0f C) on one "
          "RST 200 housing, "
          "wired DIS_G -> 100 k -> latch trigger (%.0f uA): it fires the same latch, so it acts on the HV side too. "
          "It closes after %.1f-%.1f kJ into a housing that starts at %.0f C (A_CTH_RST, adiabatic) = %.1f-%.1f s of "
          "live-source discharge: it stops repeated activations (e.g. an intermittent K_DISCH) that the per-event time "
          "limit allows. It sits at HV: mount it on the housing over an insulating pad giving **basic HV-PE insulation "
          "(1000 V DC working, OVC II)**; its contact carries uA (no dry-circuit rating published)." %
          (TSTAT_OPEN[0], TSTAT_OPEN[1], tl["i_tstat_uA"], dc["e_tstat"][0] / 1e3, dc["e_tstat"][1] / 1e3, a["A_T_RES"], dc["t_tstat"][0],
           dc["t_tstat"][1]), "",
          "Worst-case energy per RST 200 (ratings: pulse table; short-time overload 3000 W for 3 s = %.0f J; 1000 V "
          "DC max):" % RST_E_STO, "",
          md_table(dc["cases"], ["case", "V", "P_per_R_W", "t_s", "E_per_R_J", "ok"],
                   ["{}", "{:.0f}", "{:.0f}", "{:.2f}", "{:.0f}", "{}"]),
          "Firmware verifies the VA decay at every shutdown (a stuck or latched-off discharge shows as a slow decay).", "",
          "## 4 Voltage channels (VA, VAX, VB, VBX, AUX1, AUX2): divider + AMC3330", "",
          "6 x TNPV1206 1.00 M + 4.99 k: 1 V = %.0f V, clip %.0f V; %.0f V and %.1f mW per element at 1000 V." %
          (dv["v_fs"], dv["v_clip"], dv["elem_v"], dv["elem_p"] * 1e3), "",
          "**Input filter %.1f nF C0G 5 %% (was 10 nF)** on every divider (VA, VAX, VB, VBX and the IMD AUX1/AUX2): "
          "R_th %.3f k -> tau %.1f us (%.2f kHz). Voltage-sensing lag = tau (+5 %%) + AMC3330 delay (%.1f us max, "
          "50-90 %%) = **%.1f us** (was %.1f us; <= %.0f us asked by the control simulation for the frozen-controller "
          "over-voltage case). Ramp lag error at 0.5 V/us: %.1f V (was %.1f V). TI's rule (filter corner >= 10x below "
          "the 20 MHz modulator clock) holds. Noise referred to the bus: the AMC3330's own %.0f uVrms (100 kHz) = "
          "**%.2f V rms** dominates and is not changed by the filter (it arises after it); divider kT/C %.1f mV rms "
          "(was %.1f mV); converter ripple (%.0f kHz, %.2f V pp worst, cell_spec) passes as %.0f mV pp (was %.0f mV). "
          "So the MPPT's 500 kS/s oversampling (and the CTRL anti-alias stage) see the same noise as before; the DC "
          "error budget below does not depend on the capacitor." %
          (C_DIV_F * 1e9, dv["r_th"] / 1e3, dv["tau"][C_DIV_F] * 1e6, dv["fc_hz"] / 1e3, AMC_V_DELAY * 1e6,
           dv["lag"] * 1e6, dv["lag_old"] * 1e6, LAG_MAX * 1e6, dv["ramp_err"][C_DIV_F], dv["ramp_err"][10e-9],
           AMC_V_NOISE * 1e6, dv["noise_amc"], dv["ktc"][C_DIV_F] * 1e3, dv["ktc"][10e-9] * 1e3, dv["rip_f"] / 1e3,
           dv["rip_pp"], dv["rip"][C_DIV_F] * 1e3, dv["rip"][10e-9] * 1e3), "",
          "| error term | at 250 V | at 1000 V |", "|---|---|---|"]
    for kk in dv["budget"][V_MAX]["items"]:
        L.append("| %s | %.3f %% | %.3f %% |" % (kk, 100 * dv["budget"][V_MIN]["items"][kk], 100 * dv["budget"][V_MAX]["items"][kk]))
    for lab, key in (("worst, uncalibrated", "worst"), ("RSS, uncalibrated", "rss"),
                     ("worst after 1-point cal", "worst_cal"), ("RSS after cal", "rss_cal")):
        L.append("| **%s** | %.3f %% | %.3f %% |" % (lab, 100 * dv["budget"][V_MIN][key], 100 * dv["budget"][V_MAX][key]))
    L += ["", "## 5 Current channels (IA, IB): CSM2F-8518 100 uOhm + AMC3302, shunt NTC compensation", ""]
    for Ir, c in cs.items():
        L.append("- %d A port: %.1f mV at %d A, shunt %.2f W (36 W rating); linear +/-%.0f A, clip +/-%.0f A; OC trip "
                 "%.0f A, SC trip %.0f A - both inside the linear range." % (Ir, c["v_at_imax"] * 1e3, Ir, c["P_shunt"],
                                                                            c["i_lin"], c["i_clip"], c["oc"], c["sc"]))
    L += ["", "| error term (after EOL gain + offset calibration) | 135 A | 180 A |", "|---|---|---|"]
    for kk in cs[135]["comp"]:
        L.append("| %s | %.3f %% | %.3f %% |" % (kk, 100 * cs[135]["comp"][kk], 100 * cs[180]["comp"][kk]))
    L += ["| **worst, NTC-compensated** | %.2f %% | %.2f %% |" % (100 * cs[135]["worst_comp"], 100 * cs[180]["worst_comp"]),
          "| worst without compensation (TCR 125 ppm/K) | %.2f %% | %.2f %% |" % (100 * cs[135]["worst_plain"],
                                                                               100 * cs[180]["worst_plain"]), "",
          "A TDK B57703M0103A018 NTC (10 k, metal tag) is clamped to each shunt's copper next to the alloy and wired to "
          "a 2-pin header for SYS-IO-AUX's NTC inputs. Its own test voltage is only 1000 VAC / 1 s (B57703M), so the "
          "mounting must add an insulating interface that gives **reinforced** insulation for 1000 V DC working, OVC II "
          "(the shunt sits on BUS-). Firmware compensates R_shunt(T) with the test-point TCR characterised once on "
          "qualification samples (A_TCR_RES is the residual).", "",
          "## 6 Fault coordination: fuses, contactor, hold-closed interlock", "",
          "### 6.1 Fuse selection per rating (Mersen HP10NH gPV 1000 VDC, one per pole)", "",
          "| rating | fuse | In | derated at %.0f C | loss per link at rating | non-fusing 1.13 In |" % a["A_T_FUSE"],
          "|---|---|---|---|---|---|"]
    for Ir, c in co.items():
        L.append("| %d A | %s (%s, base %s) | %.0f A | %.0f A | %.1f W | %.0f A |" %
                 (Ir, c["v"]["fuse"], c["v"]["size"], c["v"]["base"], c["v"]["In"], c["i_derated"], c["p_fuse"],
                  c["nonfusing"]))
    L += ["", "PV-class ports (source=\"pv\"). Derating uses Mersen's LV formula (HP10NH is not in the AN's list - "
          "confirm with Mersen) at the fuse air temperature A_T_FUSE: the fuses sit in the inlet air path and see the "
          "inlet (module_spec: full power to 60 C) plus 5 K from their own, the base and the busbar losses. The 160 A "
          "NH1 link of the 135 A port carries 135 A to 45 C inlet (PV-20 full-power point) and is followed above that "
          "by the firmware current limit of 6.7 (port_current_limit_vs_inlet_C). A PV source cannot reach the hold "
          "band, so the fuse size is set by heat alone (6.5); battery-type ports are in 6.7.", "",
          "### 6.2 Hold-closed interlock (hardware rule)", "",
          "Rule: above the contactor's breaking capability (%.0f A at 1000 V, once) the contactor must not open until "
          "the current is cleared. Each port has **two independent channels** (own REF3030E, own 0.1 %% network, own "
          "TLV3502, OR and AND gate) on the shared IA/IB AMC3302 outputs. Network per side: +IN = 49.9 k from OUTP, "
          "118 k to AGND, 86.6 k from the proof-test driver (118 k || 86.6 k = 49.9 k keeps the common-mode "
          "cancellation); -IN = 49.9 k from OUTN, 88.7 k to REF, 113 k to AGND. HOLD = OC AND FB_x_MAIN (closed) AND "
          "DIAG (valid). Channel 1 drives the IRFR9214 high side from the port +24V, channel 2 the IRFL214 low side "
          "(sec. 7). Trip current %.0f A nominal, worst-case band **%.0f-%.0f A** per channel (shunt 5 %%, AMC "
          "gain/offset, REF, resistors, comparator offset + hysteresis): below 450 A and above every OC trip "
          "(%.0f / %.0f A). The hold needs BOTH channels (2oo2); a disagreement near the threshold opens the contactor "
          "below 450 A." % (K_CUTOFF_1000, hold["nom"], hold["lo"], hold["hi"], cs[135]["oc"], cs[180]["oc"]), "",
          "E-stop during a fault: both safety channels cut the contactor supply (DO stage), not the port +24V, so the "
          "gates stop at once and the hold path keeps the coil energised while |I| is above the threshold; the contactor "
          "opens at zero current after the fuse has cleared. The hold cannot close an open contactor (needs FB = "
          "closed) and drops out on a sensor fault (DIAG). Longest hold = fuse clearing at the low end of the band: "
          "%.0f s (battery-port 200 A gBat, -10 %% curve). Residual: loss of ALL 24 V with > 450 A (see 6.6); a stuck "
          "AMC3302 output with DIAG valid disables both channels and the CTRL SC trip together (common sensor)." %
          t_fuse(BATT[135]["curve"], 0.9 * hold["lo"]), "",
          "### 6.3 / 6.4 PV-class fuse against a stiff source (reference only)", "",
          "A gPV link sized for heat and a single HVC43 leave the battery-fed band open (135 A: %s; 180 A: %s). That is "
          "why a port that faces a battery or a stiff DC bus must be built with source=\"battery\" (6.7): it is not a "
          "permitted configuration of a PV-class port." % ("gap %.0f-%.0f A" % c135["gap"] if c135["gap"] else "none",
                                                            "gap %.0f-%.0f A" % c180["gap"] if c180["gap"] else "none"), "",
          "### 6.5 PV source (current-limited)", "",
          "A PV array cannot exceed about A_ISC_PV x rated current at the module terminals: %.0f A (135 A port) / %.0f A "
          "(180 A port). Both are below the hold band and below 450 A, so the main contactor always breaks a PV-fed "
          "fault (OC trip or E-stop). The port fuse never clears a PV-fed fault (below its 1.13 In non-fusing current: "
          "%.0f / %.0f A) and is not meant to: it protects the module's cabling against reverse current from the module "
          "side (bus capacitors, port B). Array-side faults are the job of the string fuses in the PV combiner "
          "(IEC 62548)." % (c135["pv"]["I_max"], c180["pv"]["I_max"], c135["nonfusing"], c180["nonfusing"]), "",
          "### 6.6 Bus-capacitor dump into a short at the port terminals (real capacitances)", "",
          "Loop = contactor + shunt + busbars (A_R_LOOP_X) + 2 fuse links (cold, P/In^2) + ESR; L = A_L_LOOP. Fuse "
          "minimum melting I2t: %.0f kA2s (160 A link), %.0f kA2s (250 A link); kA2s = 10^3 A2s." %
          (c135["melt_kA2s"], c180["melt_kA2s"]), ""]
    for Ir, c in co.items():
        L += ["%d A port (R_loop %.2f mOhm):" % (Ir, c["r_loop"] * 1e3), "",
              md_table(c["dump"], ["case", "C_uF", "E_J", "I_pk_kA", "I2t_kA2s", "fuse_melts", "contactor_I2t_kA2s",
                                   "weld_risk", "L_for_5kA_uH"],
                       ["{}", "{:.0f}", "{:.0f}", "{:.1f}", "{:.1f}", "{}", "{:.1f}", "{}", "{:.1f}"])]
    real = [d for c in co.values() for d in c["dump"] if d["C_uF"] < 500]
    L += ["Weld risk **remains** at the real capacitances: peak %.0f-%.0f kA against the 5 kA short-circuit rating "
          "(HVC43 p3, 'may weld, no fire'); the energy side is bounded (I2t through the contactor <= %.0f kA2s < 125 "
          "kA2s). A welded contact is caught by the FB mirror contact and the VA/VAX check at the next opening. "
          "Removing the risk needs %.0f-%.0f uH of differential-mode inductance between bus capacitor and terminals "
          "(e.g. the CM choke's leakage) - not adopted: it forms a DM filter resonance with the X capacitors that the "
          "cell designer must agree. A short inside the module between the X capacitors and the contactor bypasses the "
          "fuses; the bus capacitor then dumps through the contactor only (same peak, energy <= C V^2/2)." %
          (min(d["I_pk_kA"] for d in real), max(d["I_pk_kA"] for d in real),
           max(d["contactor_I2t_kA2s"] for d in real), min(d["L_for_5kA_uH"] for d in real),
           max(d["L_for_5kA_uH"] for d in real)), "",
          "### 6.7 Battery-type port (port(..., source=\"battery\")): ETI gBat + HVC43 pair + Raycap SPD", "",
          "A battery rack or stiff DC bus. **Prospective short-circuit current: %.0f kA assumed, <= %.0f kA allowed at "
          "L/R <= %.0f ms** (the ETI 1000 V gBat breaking capacity; was <= 50 kA with the gPV link). Port fuse: ETI NH gBat "
          "1000 V d.c. (IEC 60269-7). Contactors: **two HVC43-250A in parallel**, coils in parallel on K_x_MAIN, one "
          "S10K30 per coil, each low-side switch doubled. Worst branch carries %.3f of the port current (HVC43 contact "
          "resistance %.2f-%.2f mOhm, A_R_BRANCH %.2f mOhm +/-%.0f %%). The last contactor of the pair to open breaks "
          "alone, so the hold band stays %.0f-%.0f A." % (a["A_IPSC_BATT"] / 1e3, E_ICU / 1e3, E_LR * 1e3,
                                                          bp["share"], a["A_RC_MIN"] * 1e3, K_RC[1] * 1e3,
                                                          a["A_R_BRANCH"] * 1e3, 100 * a["A_RB_TOL"], hold["lo"], hold["hi"]),
          "", "| rating | fuse (ETI) | derated at T | loss at rating | per contactor | pair: battery-fed band | one "
              "contactor failed open | dump I2t through a contactor |", "|---|---|---|---|---|---|---|---|"]
    for Ir, o in bp["ports"].items():
        pr, sg_ = o["pair"], o["single"]
        L.append("| %d A | %s (%s) | %.0f A at %.0f C fuse air | %.1f W | %.0f A, %.1f W | %s | gap %.0f-%.0f A: "
                 "FB = both closed trips the port | %.1f kA2s |" %
                 (Ir, o["v"]["name"], BATT[Ir]["fuse"], o["derated"], o["t_amb"], o["p_fuse"], o["i_k"],
                  o["p_k"], "**coordinated** (2 A scan to %.0f kA)" % (E_ICU / 1e3) if not pr["gap"] and not pr["marginal"]
                  else "**GAP %.0f-%.0f A** (fuse -10 %%, worst sharing)" % pr["gap"] if pr["gap"]
                  else "marginal %.0f-%.0f A" % pr["marginal"], sg_["gap"][0] if sg_["gap"] else 0,
                  sg_["gap"][1] if sg_["gap"] else 0, max(d["contactor_I2t_kA2s"] for d in pr["dump"])))
    c_, t_ = bp["coil"], bp["t180"]
    L += ["", "- **Surge protection**: the on-board varistor Y of sec. 9 at both ports (same parts); its 36 A branch fuses "
          "break 30 kA at L/R 2 ms (>= the %.0f kA / 1 ms the port fuse allows)." % (E_ICU / 1e3),
          "- **Coil drive with the pair**: K_x_MAIN carries up to %.2f A (two cold coils at 26.45 V + gate divider) - "
          "**above the 0.52 A minimum of SYS DO2/DO4 (ILIM 28.7 k)**: a battery port needs ILIM 10 k (1.58-2.30 A) on its "
          "DO (PV-PORT: DO4 K_B_MAIN; requirement on SYS-IO-AUX). Hold path %.2f A from the PORT +24V (+ ~0.1 A logic): "
          "CTRL's port eFuse must allow 0.7 A for 200 s. Each IRFL214 carries %.2f A." % (c_["i_do"], c_["i_hold"], c_["i_low"]),
          "- **Feedback with the pair**: each mirror contact has its own 1.5 k pull-up; FB_x_MAIN (the SYS DI) = both "
          "closed (two BAS16, a failed-open contactor is seen at the next close), the TPSI3050 EN and both hold channels "
          "use 'any closed' (two BAS16), so a single welded contactor still inhibits the discharge and keeps the hold "
          "armed; a single weld is found by VA vs VAX after opening.",
          "- **Cable / busbar**: 70 mm2 Cu PVC withstands k2S2 = %.0f MA2s against <= %.0f kA2s fuse let-through. Reverse "
          "dump of the bus capacitor into a terminal short: see the last column (<= 125 kA2s)." %
          (bp["cable_k2s2"] / 1e6, max(BATT[r]["i2t_op"] for r in BATT) / 1e3),
          "- **Heat at 60 C inlet (fuse air 65 C) and D-020** - every port, fuse air = inlet + 5 K:", "",
          "| port | fuse | derated at 65 C | current limit vs inlet 25 ... 60 C (port_spec "
          "port_current_limit_vs_inlet_C) |", "|---|---|---|---|",
          "| pv / 135 A | %s 160 A gPV | %.0f A (Mersen AN) | %s |" %
          (VARIANTS[135]["fuse"], VARIANTS[135]["In"] * F_DERATE(a["A_T_FUSE"]),
           " / ".join("%.0f" % x for x in bp["limit"]["pv/135"])),
          "| pv / 180 A | %s | %.0f A | 180 A flat |" % (VARIANTS[180]["fuse"], t_["pv_fuse"]),
          "| battery / 135 A | ETI 200 A gBat 1000 V | %.0f A (ETI TDF) | 135 A flat |" % bp["ports"][135]["derated"],
          "| battery / 180 A | ETI 200 A gBat 1000 V (the 250 A link leaves 384-724 A open with the pair) | %.0f A | %s |"
          % (bp["ports"][180]["derated"], " / ".join("%.0f" % x for x in bp["limit"]["battery/180"])), "",
          "**Item 3 (fuse heat)**: resolved by the firmware current limit vs inlet air for the two ports whose link "
          "would otherwise run above its derated rating - PV 135 A (135 A to 45 C, %.0f A at 60 C; a 200 A NH2 gPV "
          "would remove it but changes the default part that DAB60 cross-checks) and battery 180 A. "
          "**D-020 is closed by the 200 A gBat + HVC43 pair (coordinated over the whole band at 180 A) together with that "
          "limit**: the 180 A battery port carries 180 A up to 35 C inlet, then the limit above "
          "(164 A at 45 C, 136 A at 60 C) - the 200 A link would otherwise run above its derated rating. PV-P100/110 "
          "therefore reaches 110 kW at 45 C only for port-B voltages >= %.0f V. Closing it without a limit needs a "
          "contactor that carries 250-450 A for >= 30 min with an auxiliary contact and a <= 0.5 A coil: Hongfa "
          "HFE82V-600 has the curve (800 A 20 min, 1000 A 5 min, 8 kA 10 ms) but a 50 W economised coil and no auxiliary "
          "contact; HFE82V-300C has a 6 W coil but no auxiliary contact and 450 A only for 5 min. Shunt %.2f W, HVC43 "
          "%.1f W each at 180 A." % (bp["limit"]["pv/135"][-1], 110e3 / bp["limit"]["battery/180"][4], t_["shunt_W"],
                                     bp["ports"][180]["p_k"]), "",
          "## 7 Coil drive, feedback and proof test against SYS-IO-AUX (TPS272C45 DO, ISO1212 DI)", "",
          "### 7.1 Coil drive: two switches in every coil-current path (PA-01, PA-02, INT-03)", "",
          "Main contactor: K_x_MAIN -> S2M -> coil+; coil- -> **Q_LS1** IRFL214 (250 V) to GND, gate = K_x_MAIN "
          "through 10 k / 22 k. Hold path: port +24V -> **Q_HS** IRFR9214 (-250 V, channel 1) -> S2M -> coil+; coil- "
          "-> **Q_LS2** IRFL214 (channel 2), whose gate is fed from Q_HS's output through 10 k / 22 k and clamped off "
          "by channel 2 - so Q_LS2 can only conduct while Q_HS conducts. The **S10K30 sits directly across the coil "
          "and is the only flyback path**. Precharge relay: K_x_PRE -> coil+; coil- -> Q_LSP IRFL214 (gate = "
          "K_x_PRE); S2M + SMBJ33A directly across the coil. Coil current %.2f-%.2f A (incl. 10 k / 22 k); coil at "
          "pick-up >= %.1f V (needs <= %.0f V) through S2M + hot Q_LS1; hold path holds at >= %.1f V. Gate drive "
          "%.1f-%.1f V (VGS max 20 V); Q_HS VGS %.1f V (10 k / 10 k), |VDS| <= %.0f V even with its output dragged to "
          "the SYS clamp (was the PA-02 failure of the -40 V DMP4065S)." %
          (cd["i_do"], cd["i0"], cd["v_pick"], K_PICKUP, cd["v_hold"], cd["v_gs_min"], cd["v_gs_max"], cd["v_hs_gs"],
           cd["v_hs_ds"]),
          "",
          "Every turn-off (L_coil = 2 H assumed, A_L_COIL_K; S10K30 v(i) from v(1 mA) 47 V +/-10 % and vc 93 V at "
          "5 A):", "",
          md_table(cd["states"], ["state", "opens", "path", "v_coil", "v_ds", "v_do", "e_do_mJ"],
                   ["{}", "{}", "{}", "{}", "{:.0f}", "{:.1f}", "{:.3f}"]),
          "- The varistor is the lowest-voltage path in every normal state: once the low side that carries the coil "
          "current opens, the only other loop is a low-side avalanche (>= %.0f V) - Q_LS1 opens with K_x_MAIN "
          "(K_x_MAIN is at %.1f-%.1f V when it does, so the DO never goes negative) and Q_LS2 cannot outlive Q_HS. "
          "Highest V_DS %.0f V = %.0f %% of 250 V." % (LS_VDS, cd["v_do_off"][0], cd["v_do_off"][1], cd["v_ds_max"],
                                                      100 * cd["v_ds_max"] / LS_VDS),
          "- SYS TPS272C45: K_x_MAIN never goes below 0 V in normal operation; in the one transient (channel 1 drops "
          "first) and in every single-fault case it is held by SYS's SMAJ12A + SMAJ36A at >= %.1f V: VS - VOUT <= "
          "%.1f V (abs max %.0f V), clamp energy <= %.0f mJ per turn-off (SYS rates %.1f J)." %
          (DO_CLAMP[0], cd["vs_out"], DO_VSOUT_ABS, cd["e_coil"] * 1e3, DO_CLAMP_J),
          "- Opening time: the S10K30 clamps at >= %.1f V down to the drop-out current (%.0f mA at the 2 V drop-out), "
          "i.e. at or above TDK's 50 V minimum over the whole demagnetisation, so the HVC43 break time stays within "
          "TDK's <= %.0f ms (p3, p7). Demagnetisation estimate: %.1f ms (varistor, -10 %%) vs %.1f ms with an ideal "
          "50 V clamp; %.1f ms through the SYS clamp (only after a shorted low side - found by the proof test)." %
          (cd["v_var_drop"], 1e3 * K_DROPOUT / K_COIL_R, K_BREAK_T * 1e3, cd["t_var"] * 1e3, cd["t_ref"] * 1e3,
           cd["t_doc"] * 1e3),
          "- G7L-X: clamp %.1f-%.1f V = 1.6-1.7 x 24 V (Omron p5: 1-2 x coil voltage, as for its release time)." %
          cd["pre_clamp"], "",
          "Single faults (main contactor):", "",
          "| fault | coil energised by itself? | effect at turn-off | found by |", "|---|---|---|---|"]
    L += ["| %s | %s | %s | %s |" % f for f in cd["faults"]]
    L += ["", "### 7.2 DO / DI loads", "",
          md_table(itf["rows"], ["coil", "I_A", "V_coil_min", "pickup", "E_off_mJ", "clamp", "limit", "ok"],
                   ["{}", "{}", "{:.1f}", "{}", "{:.0f}", "{}", "{}", "{}"]),
          "DO2/DO4 limit 0.52-0.82 A (ILIM 28.7 k) is set on SYS-IO-AUX (INT-20, closed there). Coil inductances are "
          "not published (A_L_COIL_*). DO slew 0.45-1.05 V/us meets the TPSI3050 EN edge rule (>= 65 V/ms).", "",
          "### 7.3 Feedback FB_x_MAIN (INT-02)", "",
          "Module-internal PELV signal (interfaces.DI): SYS-IO-AUX puts the field ground of DI4/DI5 on GND and each FB "
          "pin of J_CTRL has **its own GND return pin** beside it (SYS J702: FB_A, GND, FB_B, GND).", "",
          "| FB_x_MAIN (ISO1212 RSENSE 562 ohm, RTHR 1 k) | value |", "|---|---|",
          "| contactor CLOSED, K_DISCH on: voltage at the DI | %.1f-%.1f V (VIH <= %.2f V) |" %
          (itf["fb"]["V_DI_closed"][0], itf["fb"]["V_DI_closed"][1], DI_VIH),
          "| contactor CLOSED, K_DISCH off (E-stop, FB also feeds the TPSI3050 EN) | >= %.1f V (VIH <= %.2f V); "
          "EN >= %.1f V |" % (itf["fb"]["V_estop_min"], DI_VIH, itf["fb"]["V_en_estop"]),
          "| hold-logic inputs (100 k / 25.5 k per channel) | %.2f-%.2f V (VIH 2.0 V, max 5.5 V) |" % itf["fb"]["V_logic"],
          "| contactor OPEN (mirror NC closed) | %.3f V (VIL >= %.1f V), 0 mA |" % (itf["fb"]["V_DI_open"], DI_VIL),
          "| aux contact wetting / voltage when open | %.1f-%.1f mA (>= 10 mA) / <= %.1f V (<= 24 V) |" %
          (itf["fb"]["I_wet"][0] * 1e3, itf["fb"]["I_wet"][1] * 1e3, itf["fb"]["V_aux_open"]),
          "| 1.5 k pull-up dissipation (contactor open) | %.2f W (2512, 1 W) |" % itf["fb"]["P_pullup"],
          "| logic seen by the controller | **high = contactor closed** (NC mirror contact inverted by the pull-up) |",
          "", "### 7.4 Proof test of the hold (PA-04)", "",
          "Input: DO_SPARE (DO8) = TEST, 100 k / 15 k -> SN74LVC1G17 per port. Four injection signals (each into +IN "
          "through 86.6 k): T -> channel 1 side A; T_d (47 k / 22 uF delay) -> channel 1 side B; one-shot of T (3.3 uF "
          "/ 47 k) -> channel 2 side A; one-shot of T_d -> channel 2 side B. The injection shifts the threshold by "
          "%.0f A, so a healthy side trips at zero current (worst case under TEST: trips below %.0f A, i.e. at 0 A) "
          "and a side whose threshold has moved above ~%.0f A does not. No single fault (TEST stuck, DO8 shorted, one "
          "buffer stuck) trips both channels for longer than one one-shot, so TEST cannot create a hold." %
          (pt["shift"], pt["test_hi"], pt["shift"]), "",
          "Readback (spare line of an existing connector): per port RB1 = OC1 OR hold-high-side-output, RB2 = OC2. "
          "PV-PORT sums them onto the **ID line** with the CTRL-C2000 rev D values: ID %.1f k to GND, port A outputs "
          "through 2 x %.0f k, port B through 2 x %.1f k (0.1 %%, push-pull 3.3 V CMOS), together %.2f k = the PV-PORT "
          "code. Nine states, %.3f-%.3f V; worst gap between neighbouring states over V_OH, resistor and pull-up "
          "tolerances **%.1f counts** (%.1f mV, 2.5 V / 4096), **%.1f counts with the hold coils on** (ribbon ground "
          "A_R_RIB x 0.3 A port A / 0.6 A port B pair); unpowered port board %.3f V. The card decodes the port ID with "
          "its own port-only table, so the old +/-165 mV window no longer applies." %
          (R_ID_RB / 1e3, R_RB_A / 1e3, R_RB_B / 1e3, pt["r_eff"] / 1e3, pt["levels"][0]["lo"], pt["levels"][-1]["hi"],
           pt["gap_off"] / ADC_LSB, pt["gap_off"] * 1e3, pt["gap_on"] / ADC_LSB, pt["v_unpowered"]), "",
          "Sequence at every start: pass 1 with the contactors open (readback only), pass 2 after precharge with the "
          "contactor closed at zero current (K_x_MAIN pulsed off for <= 50 ms inside each window, FB watched):", "",
          "| window | starts at | injected | RB1+RB2 | length | checks |", "|---|---|---|---|---|---|"]
    L += ["| %s | %s | %s | %d | %s | %s |" % w for w in pt["windows"]]
    L += ["", "Network faults of one side (I_trip without / with TEST, how they are found):", "",
          md_table(pt["rows"], ["fault", "I_trip", "I_trip_test", "found"], ["{}", "{:.0f}", "{:.0f}", "{}"]),
          "Also found: comparator / OR output stuck low (proof test) or high (at rest); AND gate or driver stuck "
          "(sec. 7.1 table); TEST path broken (no response); readback resistor or output open (wrong level). "
          "**Not found**: %s (the injection alone still trips the side, which is then dead in service); a stuck "
          "AMC3302 output with DIAG valid (common to both channels and the CTRL SC trip); the DIAG input of the AND "
          "gates stuck high; an open S10K30 (until it destroys a low side). **Found only in part of the tolerance "
          "corners**: %s." % (", ".join(r["fault"] for r in pt["rows"] if r["found"] == "NOT FOUND"),
                              ", ".join(r["fault"] for r in pt["rows"] if "marginal" in r["found"])), "",
          "### 7.5 Precharge-complete criterion (INT-19)", "",
          "VAX - VA after the 1-point calibration alone: +/-%.1f V at 1000 V (raw), inrush up to %.0f A. Firmware nulls "
          "the VA/VAX (VB/VBX) gain mismatch whenever the main contactor is closed with |I| < OC and stores it; the "
          "residual is the drift until the next precharge (A_DT_NULL = %.0f K: TCR 50 ppm/K, AMC gain 45 ppm/K, "
          "nonlinearity, offset drift): **+/-%.1f V worst / %.1f V RSS at 1000 V, +/-%.2f V at 250 V**. Real |dV| at "
          "completion <= %.1f V -> inrush <= %.0f A (TDK make test %.0f A). Before a null exists (first start, sensor "
          "exchange) firmware completes only on settling: VA changes < 1 V over one tau_max (%.0f ms); the physical "
          "difference is then <= the bleeder current x R_pre (%.1f V) + %.1f V." %
          (pn["res"][V_MAX]["raw_V"], pn["i_raw"], a["A_DT_NULL"], pn["res"][V_MAX]["worst_V"],
           pn["res"][V_MAX]["rss_V"], pn["res"][V_MIN]["worst_V"], pn["dv_real"], pn["i_inrush"], K_MAKE_CAP[1],
           pc["tau_max"] * 1e3, V_MAX / dc["r_passive"] * R_PRE, 1 / (1 - math.exp(-1))), "",
          "## 8 Insulation-resistance measurement (PV-C5)", "",
          "Each test switch is a **common-source pair of C2M1000170D** driven by one TPSI3050 (SLVSFJ7D two-wire AC "
          "switch, PA-09): it blocks both polarities, so a pole below PE (dark PV array with port B live) no longer "
          "leaks through a body diode into the 'open' string.", "",
          "R_t = %d x 124 k = %.0f k per switch; AUX dividers %.3f M. State P: (V1 - u1)(G_ip + G_a + G_t) = u1 (G_in + "
          "G_a); state N: (V2 - u2)(G_ip + G_a) = u2 (G_in + G_a + G_t); R_iso = 1/(G_ip + G_in). Swing u1-u2: 33 k -> "
          "%.1f V at 250 V; 1 M -> %.0f V. Worst error: 33 k %.1f %%, 1 M %.1f %% (IDSS typ) / %.0f %% (IDSS max -> "
          "EOL baseline). tau %.1f s." % (N_IMD, im["R_T"] / 1e3, im["R_A"] / 1e6, im["dU"][V_MIN][33e3],
                                          im["dU"][V_MIN][1e6],
                                          100 * max(im["err"][(V, 33e3, lk)] for V in (V_MIN, V_MAX) for lk in ("typ", "max")),
                                          100 * im["err"][(V_MIN, 1e6, "typ")], 100 * im["err"][(V_MIN, 1e6, "max")],
                                          im["tau_s"]), "",
          "**Rev F0 (REFERENCE-LESSONS 1-5, IC-11)**: array capacitance sized at 200 nF/kWp: %.1f uF (PV-P75) / %.1f uF "
          "(PV-P110) -> tau %.1f / %.1f s at 1 MOhm; settle-and-wait 3 tau = %.0f / %.0f s per state (two states per "
          "result) - over the 10 s branch-on limit, so CTRL uses the 3-sample exponential prediction (samples %.2f s "
          "apart, V_inf = (V0 V2 - V1^2)/(V0 - 2 V1 + V2), TIDUFG5): about %.1f s per state, %.1f s per result, C_iso "
          "as a by-product. **Open PE**: dividers return on PE_D (own M4 bond), test strings, MOV3 and Y capacitors on "
          "PE_T (removable strap, opened for the hipot); an open PE_T shows no swing between the states, an open PE_D "
          "shows AUX1 = -AUX2 = V/2 and no swing - a healthy system swings >= %.1f V even at 10 kOhm (%.0f LSB), so "
          "CTRL flags 'IMD PE open' instead of reading infinite insulation (firmware rule, CTRL spec). **One active "
          "monitor per DC system**: the module disables its own by keeping IMD_SW_P/N de-energised (strings open, "
          "fail-safe); it still loads each pole with the 6.005 M dividers, which the active monitor must count (system "
          "spec). **IC-11**: the switch islands and TPSI3050 secondaries are HV nets; the PE-side SiC of each pair is the "
          "HV-PE crossing." % (a["A_C_PV_PE"] * 1e6, a["A_C_PV_PE_110"] * 1e6, im["tau"][a["A_C_PV_PE"]],
                               im["tau"][a["A_C_PV_PE_110"]], im["settle_s"][a["A_C_PV_PE"]],
                               im["settle_s"][a["A_C_PV_PE_110"]], a["A_T_PRED"], im["pred_s"], 2 * im["pred_s"],
                               im["pe_open"]["min_healthy_swing_V"], im["pe_open"]["min_healthy_swing_V"] / im["lsb_V"]), "",
          "## 9 Surge protection (PV-C4) - on-board varistor Y (rev F0)", "",
          "Per port: MOV1 pole+ -> M, MOV2 pole- -> M, MOV3 M -> PE_T, Thinking TVT25751 (25 mm, thermal link, monitor "
          "lead); each pole branch behind an ETI CH14x51 gPV 36 A 1000 V DC fuse (30 kA at L/R 2 ms) in PCB clips. "
          "**Effective level at In = %.0f A (8/20): 2 x %.0f V + L di/dt %.0f V = %.0f V pole-PE and pole-pole** (<= "
          "4.0 kV, a preferred-series value). Varistor curve: Thinking TVR20751 maximum clamping curve (TVR p25, read by "
          "pixel analysis) scaled to the 25 mm part by the class-current ratio 1.5 - **calculated, not measured**. "
          "Continuous: 2 x 615 V = %.0f V >= %.0f V trip overshoot, pole-pole and, after a first earth fault, pole-PE "
          "(each varistor <= %.0f V). In/Imax in IEC 61643-31 terms: **In 3 kA** (TVT p16 potting curve allows 15 "
          "impulses at ~9 kA: %.1f x), **Imax 25 kA** once (TVT p9) - no 61643-31 type test exists for this circuit; "
          "IEC 61643-32 may ask In >= 5 kA where an installer Type 2 is not fitted (open). Branch fuse pre-arc I2t "
          "450 A2s vs %.0f A2s of one In impulse (%.1f x); Imax may open it, which the monitor reports. "
          "**Thermal disconnect on DC: Thinking states UL 1449 (Type 4 component assembly) and IEC 61051 only - no DC "
          "rating of the thermal link**; a certifier must accept that a slowly degrading varistor below the 36 A fuse "
          "is cleared by an AC-tested thermal link at <= 615 V DC per link (each varistor sees <= half the pole voltage), "
          "or require the IEC 61643-31 end-of-life tests on the assembled board." % (
              SPD_IN, sg["v1"], sg["v_l"], sg["Up"], sg["uc"], a["A_V_TRIP_OS"], sg["v_first_fault"], sg["i15_ratio"],
              sg["i2t"], sg["fuse_ratio"]), "",
          "**Monitoring (IC-12, condition 4 of insulation report sec. 3.1)**: pole+ -> branch fuse -> MOV1 link -> "
          "monitor lead -> 6 x 82.5 k -> CNY65B LED -> MOV3 link -> M -> 6 x 82.5 k -> MOV2 link -> pole-: every link "
          "and both fuses in one series loop. LED %.2f mA at 250 V, %.2f mA at the trip overshoot (%.2f W per port); "
          "%.0f V per 2512 element (%.0f V during an In surge). The opto output is ORed into RB1 (ID-line readback): "
          "RB1 = 1 at rest = hold channel 1 stuck OR varistor network open -> CTRL inhibits the start / stops (judged "
          "only above 200 V port voltage: the PV port is dark at night). CTR at 0.25 mA not in the CNY65 sheet "
          "(10 mA only): verify." % (sg["mon"]["i_min"] * 1e3, sg["mon"]["i_max"] * 1e3, sg["mon"]["p_max"],
                                       sg["mon"]["v_elem"], sg["mon"]["v_elem_surge"]), "",
          "Insulation requirement per D-032 with this credit (IC-13: the old table compared with Up itself):", "",
          md_table(sg["rows"], ["item", "rating_V", "need", "req_V", "margin"], ["{}", "{:.0f}", "{}", "{:.0f}", "{:.2f}"]),
          "**IC-02 / IC-03 rely on this case**: AMC3302 VIOSM 6250 V and AMC3330 VIMP 7700 V meet reinforced only "
          "because Up,eff <= 4.0 kV (asserted). IMD switch avalanche current during an In surge on an open string: "
          "%.1f mA." % sg["imd_avalanche_mA"],
          "**Contactor re-evaluation (rev 6 item 3)**: one Hongfa HFE85V-300M/1000 per port against the 200 A gBat: "
          "carry/pre-arc margin %.2f from today's band (382 A) - not coordinated; %.2f from a band moved to 530-600 A, "
          "which its 800 A / 1000 V single break allows and the AMC3302 range (640 A clip) still covers. It has a "
          "1 Form B aux contact and a 60 W / 0.2 s pull-in (built-in economiser, 4.3 W hold). **Not adopted: coil-contact "
          "and aux-contact dielectric 3000 V AC 1 min (p101) < the 4243-4400 V rms reinforced test (IC-05)** - usable "
          "only with supplementary insulation in the coil and aux circuits (an isolated coil supply for 60 W / 0.2 s). "
          "HFE82V-600 (4000 V AC, no aux, polarised) and HFE82V-300C (3000 V AC, no aux) fail the same rule. TDK HVC43 "
          "(4400 V AC, Uimp 8 kV, mirror contact) stays: 1 on the PV port, 2 on the battery port." % (hongfa(382), hongfa(530)),
          "- **X capacitors (PA-07, IC-10)**: RFQ 2.2 uF film, 1300 V DC at 70 C, ESR <= 16 mOhm, impulse >= %.0f V "
          ">= 1.1 x Up,eff pole-pole (%.0f V), leads at the varistor network." % (CX_VIMP, 1.1 * sg["Up"]),
          "- **Isolator creepage (PA-08)**: AMC3330 / AMC3302 (>= 8 mm) and TPSI3050 (>= 8.5 mm) are below the 10 mm "
          "that reinforced insulation needs at 1000 V DC, PD2, material group I. Board requirement: **conformal "
          "coating to PD1 (IEC 60664-3)** over every HV-PELV isolator footprint and its HV-side copper, then the "
          "packages' own creepage suffices; clearance for the 8 kV reinforced impulse at 3000 m (x1.14) to be confirmed "
          "with the SPD-limited overvoltage under ECO-10.", "",
          "## 10 Y capacitance vs touch / leakage current", "",
          "2 x VY1 4.7 nF (+20 %%) per port: %.1f nF per module; PE current %.2f mA at 150 Hz, %.1f mA at 20 kHz (filter "
          "return path); array %.0f mA under the same assumption. DC stress %.0f %% of 1500 V." %
          (yc["C_module"] * 1e9, yc["I_150Hz_mA"], yc["I_20kHz_mA"], yc["I_array_mA"], 100 * yc["V_dc_stress"]), "",
          "## 11 ngspice vs calculation (precharge + contactor closing, 1 mF envelope, battery source)", "",
          "Deck `%s` (%s). dV <= %.0f V reached at %.4f s (calc) vs %.4f s (ngspice); inrush %.1f A (calc bound) vs "
          "%.1f A (ngspice)." % (sp["deck"], sp["ngspice"], DV_OK, sp["t_close"], sp["t_dv_spice"], irmax["I_bound"],
                                 sp["i_inrush_spice"]), "",
          "## 12 180 A variant (PV-P100/110) - what changes", "",
          "| item | 135 A | 180 A |", "|---|---|---|",
          "| fuse (each pole) | %s, derated %.0f A | %s, derated %.0f A |" % (c135["v"]["fuse"], c135["i_derated"],
                                                                         c180["v"]["fuse"], c180["i_derated"]),
          "| fuse loss per link | %.1f W | %.1f W |" % (c135["p_fuse"], c180["p_fuse"]),
          "| contactor HVC43-250A-24MC | %.0f %% of Ith | %.0f %% of Ith |" % (100 * 135 / K_ITH, 100 * 180 / K_ITH),
          "| shunt (unchanged part) | %.1f mV, %.2f W | %.1f mV, %.2f W |" % (cs[135]["v_at_imax"] * 1e3, cs[135]["P_shunt"],
                                                                          cs[180]["v_at_imax"] * 1e3, cs[180]["P_shunt"]),
          "| OC / SC trip | %.0f / %.0f A | %.0f / %.0f A (SC 2.5x to stay in the +/-500 A linear range) |" %
          (cs[135]["oc"], cs[135]["sc"], cs[180]["oc"], cs[180]["sc"]),
          "| CM choke / terminals (CUSTOM spec) | %d A / %d A | %d A / %d A |" % (c135["v"]["cmc_A"], c135["v"]["term_A"],
                                                                              c180["v"]["cmc_A"], c180["v"]["term_A"]),
          "| precharge, discharge, bleeder, dividers, IMD | sized on V and C | unchanged (360 uF: precharge %.2f s, "
          "active discharge %.1f s) |" % (R_PRE * 360e-6 * math.log(V_MAX / DV_OK),
                                           1 / (1 / R_DIS + 1 / dc["r_passive"]) * 360e-6 * math.log(V_MAX / 60)),
          "| battery-fed fault band | covered in the module%s | %s |" % (
              "" if not c135["marginal"] else " (marginal at %s A)" % band_txt(c135["marginal"]),
              "covered" if not c180["gap"] else "NOT covered %.0f A-%.0f kA with HVC43: larger contactor (HVC50-600B, "
              "pending TDK data) or source-side protection" % (c180["gap"][0], c180["gap"][1] / 1e3)), "",
          "## 13 Requirements on the sources and on other boards", "",
          "- **PV array (port A)**: Isc at the module terminals <= 1.25 x rated port current (<= %.0f A / %.0f A); "
          "string fuses in the combiner per IEC 62548; array cabling rated for the port fuse's let-through." %
          (c135["pv"]["I_max"], c180["pv"]["I_max"]),
          "- **Battery / DC bus (port B)**: prospective short-circuit current <= %.0f kA with L/R <= 1 ms at the module "
          "terminals (gPV breaking rating, p1), otherwise a gBat link (IEC 60269-7). 135 A port: nothing else - the "
          "module clears the whole band itself. 180 A port with HVC43: the source must clear everything above %.0f A "
          "within the contactor carry curve (sec. 6.4) - realistic only with the larger contactor. Double fault (all "
          "24 V lost while > 450 A flows): the contactor opens beyond its rating - the rack protection must clear it "
          "and the contactor is replaced afterwards." % (F_IR_DC / 1e3, c180["gap"][0] if c180["gap"] else 0),
          "- **SYS-IO-AUX**: K_DISCH energised before any K_x_PRE / K_x_MAIN at power-up (the port block then inhibits "
          "the discharge of a closed port by itself, so K_DISCH may be cut unconditionally); DO2/DO4 >= 0.30 A "
          "(0.52-0.82 A set); DO8 (DO_SPARE) = proof-test input, >= %.1f mA; FB DI field return on GND with one return "
          "pin per FB line; DO stages keep the SMAJ12A + SMAJ36A clamp (used only in fault cases); NTC inputs for the "
          "two shunt NTCs (10 k, B 3988 K)." % (1e3 * 2 * V24[1] / sum(R_TST_DIV)),
          "- **CTRL-C2000**: precharge supervision as sec. 1 with the VA/VAX null of sec. 7.5; the proof test of sec. "
          "7.4 at every start (both passes) and RB1+RB2 vs the measured current in operation; decode the ID-line "
          "readback levels (sec. 7.4) and widen its ID window to +/-165 mV; verify the VA decay at every shutdown; open "
          "a main contactor only below 450 A (hardware enforces it anyway); OC trip %.0f / %.0f A, SC comparator "
          "%.0f / %.0f A on IA/IB; shunt TCR compensation from the NTC; the PORT +24V pins must supply up to 0.6 A while "
          "both hold paths are active." % (cs[135]["oc"], cs[180]["oc"], cs[135]["sc"], cs[180]["sc"]),
          "- **Layout (ECO-10, no dimensions here)**: HV <-> PELV reinforced, 1000 V DC working, OVC II, PD2 inside, "
          "conformal coating to PD1 over the isolator footprints (sec. 9); HV <-> PE basic + bonding; fuses in the "
          "inlet air path; NTC insulating interface reinforced; the R_dis thermostat on a basic-insulating pad.", ""]
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L))


def spec(pc, dv, cs, co, hold, itf, im, cn, dc, cd, tl, pt, pn, bp, sg):
    return {
        "meta": {"generated_by": "sim/port_design.py", "status": "CALCULATED / SIMULATED - not bench-validated",
                 "bus_capacitance_sources": cn},
        "contactor": {"mpn": "B88269X7340C011", "type": "TDK HVC43-250A-24MC", "ds": DS + "protection/HVC43MC.pdf",
                      "bidirectional": "yes - 'main terminals without polarity (bi-directional)', HVC43MC p2",
                      "coil_V": 24, "coil_ohm": K_COIL_R, "coil_suppressor": "B72210S0300K101 (S10K30)",
                      "aux": "mirror contact NC (COM pos 1, NC pos 4); 10 mA min -> pull-up R_fb"},
        "variants": {str(Ir): {"fuse_mpn": c["v"]["fuse"], "fuse_item": c["v"]["item"], "fuse_In_A": c["v"]["In"],
                               "fuse_size": c["v"]["size"], "fuse_base": c["v"]["base"],
                               "fuse_derated_A": round(c["i_derated"], 1), "fuse_loss_W": round(c["p_fuse"], 1),
                               "cmc_rating_A": c["v"]["cmc_A"], "terminal_rating_A": c["v"]["term_A"],
                               "OC_trip_A": cs[Ir]["oc"], "SC_trip_A": cs[Ir]["sc"],
                               "battery_gap_A": c["gap"], "battery_marginal_A": c["marginal"]}
                     for Ir, c in co.items()},
        "sources": {   # per-source parts (port(..., source=)); "variants" above stays the PV data
            "pv": {str(Ir): {"fuse_mpn": c["v"]["fuse"], "fuse_mfr": "Mersen", "fuse_In_A": c["v"]["In"],
                             "n_contactors": 1, "contactor_mpn": "B88269X7340C011",
                             "coordinated": "PV-fed currents only (<= %.0f A, below the hold band)" % c["pv"]["I_max"]}
                   for Ir, c in co.items()},
            "battery": {str(Ir): {"fuse_mpn": BATT[Ir]["fuse"], "fuse_mfr": "ETI", "fuse_name": BATT[Ir]["name"],
                                  "fuse_In_A": BATT[Ir]["In"], "fuse_derated_A": round(o["derated"], 1),
                                  "fuse_derated_60C_A": round(o["derated60"], 1), "fuse_loss_W": round(o["p_fuse"], 1),
                                  "n_contactors": 2, "contactor_mpn": "B88269X7340C011", "share": round(bp["share"], 3),
                                  "coordinated": not o["pair"]["gap"] and not o["pair"]["marginal"],
                                  "gap_A": o["pair"]["gap"], "single_contactor_gap_A": o["single"]["gap"],
                                  "Ipsc_assumed_A": a["A_IPSC_BATT"], "Ipsc_allowed_A": E_ICU, "L_R_max_s": E_LR,
                                  "DO_current_A": round(bp["coil"]["i_do"], 3), "hold_current_A": round(bp["coil"]["i_hold"], 3)}
                        for Ir, o in bp["ports"].items()}},
        "precharge": {"R_ohm": R_PRE, "R_mpn": "Miba RST 200 220R (RFQ)", "R_tol": RST_TOL, "relay_mpn": "G7L-2A-X DC24",
                      "dV_ok_V": DV_OK, "k_check": K_CHK, "t_blank_s": a["A_T_BLANK"], "tau_max_s": pc["tau_max"],
                      "t_out_s": round(pc["t_out"], 3), "max_attempts": N_ATTEMPTS,
                      "lockout_s": math.ceil(pc["lockout_s"] / 10) * 10, "E_worst_J": round(pc["E_worst"], 1),
                      "dV_residual_after_null_V": round(pn["res"][V_MAX]["worst_V"], 2),
                      "I_inrush_at_residual_A": round(pn["i_inrush"], 1)},
        "discharge": {"R_ohm": R_DIS, "R_elem_ohm": R_DIS_E, "n_R": N_DIS, "R_mpn": "Miba RST 200 300R (RFQ)",
                      "switch": "C2M1000170D", "driver": "TPSI3050QDWZRQ1",
                      "bias_string": [N_BIAS, R_BIAS_E], "bleeder_string": [N_BLEED, R_BLEED_E], "R_pxfr_ohm": 7.32e3,
                      "C_vddp_F": C_VDDP, "R_en_pd_ohm": R_EN_PD, "R_gate_ref_ohm": list(R_GREF),
                      "R_timer_ohm": R_TIM, "C_timer_F": C_TIM, "R_latch_ohm": R_LATCH, "R_tstat_ohm": R_TSTAT,
                      "t_limit_s": [round(tl["t_min"], 2), round(tl["t_max"], 2)],
                      "thermostat_mpn": "", "E_per_R_worst_J": round(max(c["E_per_R_J"] for c in dc["cases"]))},
        "coil": {"low_side": "IRFL214TRPbF", "hold_high_side": "IRFR9214TRPbF", "R_gate_div_ohm": list(R_GATE_DIV),
                 "V_ds_max_V": round(cd["v_ds_max"], 1), "t_demag_ms": round(cd["t_var"] * 1e3, 2),
                 "t_demag_ref50V_ms": round(cd["t_ref"] * 1e3, 2), "E_coil_mJ": round(cd["e_coil"] * 1e3, 1)},
        "proof_test": {"R_tst_div_ohm": list(R_TST_DIV), "R_os_ohm": R_OS, "C_os_F": C_OS, "C_dly_F": C_DLY,
                       "R_os_in_ohm": R_OS_IN, "shift_A": round(pt["shift"], 1), "one_shot_s": [round(x, 3) for x in pt["t_os"]],
                       "delay_up_s": [round(x, 3) for x in pt["t_up"]], "delay_down_s": [round(x, 3) for x in pt["t_dn"]],
                       "coverage": {r["fault"]: r["found"] for r in pt["rows"]}},
        "port_current_limit_vs_inlet_C": {
            "inlet_C": bp["inlet"], "fuse_air_C": [t + DT_FUSE for t in bp["inlet"]],
            "note": "firmware port-current limit = min(rating, fuse In x derating at inlet + 5 K): pv/135 above 45 C, "
                    "battery/180 above 35 C inlet; CTRL must enforce it, sim/pv_module.py and the Megarevo comparison read it",
            **{k: [round(x, 1) for x in v] for k, v in bp["limit"].items()}},
        "readback_codes": {c: {"states": [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in d.items()}
                                          for d in rb_["states"]],
                               "gap_counts": round(rb_["gap_counts"], 1), "unpowered_V": [round(x, 5) for x in rb_["unpowered_V"]],
                               "R_eff_ohm": round(rb_["R_eff_ohm"], 1), "R_ohm": READBACK_R[c],
                               "pullup_ohm": ID_PULLUP, "vbias_V": ID_VBIAS, "adc_lsb_V": ADC_LSB}
                           for c, rb_ in ((c, readback_states(c)) for c in READBACK_R)},
        "readback": {"R_rb_A_ohm": R_RB_A, "R_rb_B_ohm": R_RB_B, "R_id_ohm": R_ID_RB, "R_id_effective_ohm": round(pt["r_eff"]),
                     "R_hc_div_ohm": list(R_HC_DIV), "levels_V": [[round(d["lo"], 4), round(d["hi"], 4)] for d in pt["levels"]],
                     "gap_counts": round(pt["gap_off"] / ADC_LSB, 1), "gap_counts_coils_on": round(pt["gap_on"] / ADC_LSB, 1)},
        "divider": {"n_top": N_DIV, "R_top_elem_ohm": R_DIV_E, "R_top_mpn": "TNPV12061M00BEEA", "R_bot_ohm": R_DIV_B,
                    "R_bot_mpn": "TNPW12064K99BEEA", "C_filter_F": C_DIV_F, "C_filter_tol": C_DIV_TOL, "ratio": dv["k"],
                    "V_full_scale": dv["v_fs"], "lag_s": dv["lag"], "amc_delay_s": AMC_V_DELAY},
        "current": {"shunt_mpn": "CSM2F-8518-L100J01", "R_shunt_ohm": SH_R, "amp": "AMC3302DWE", "R_filter_ohm": 10.0,
                    "C_filter_F": 10e-9, "I_linear_A": cs[135]["i_lin"], "I_clip_A": cs[135]["i_clip"],
                    "ntc_mpn": "B57703M0103A018", "error_worst_compensated": {str(k): v["worst_comp"] for k, v in cs.items()}},
        "hold": {"I_trip_nom_A": round(hold["nom"], 1), "I_trip_band_A": [round(hold["lo"], 1), round(hold["hi"], 1)],
                 "channels": 2, "R1_ohm": HOLD_R["r1"], "R2_ohm": HOLD_R["r2"], "Rt_ohm": HOLD_R["rt"],
                 "R3_ohm": HOLD_R["r3"], "R4_ohm": HOLD_R["r4"], "vref": "REF3030EAIDBZR",
                 "comparator": "TLV3502AIDCNR", "or": "SN74LVC1G32DBVR", "and3": "SN74LVC1G11DBVR",
                 "switch": "IRFR9214TRPbF", "R_fb_div_ohm": list(R_FB_DIV), "R_diag_pullup_ohm": 47e3},
        "spd": {"type": "on-board Y per port: MOV1 pole+ -> M, MOV2 pole- -> M, MOV3 M -> PE_T (IC-01/IC-12)",
                "mov_mpn": "TVT25751KFKG", "mov_mfr": "Thinking Electronic", "fuse_mpn": "002637115", "fuse_mfr": "ETI",
                "clip_mpn": "006710340", "monitor_opto": "CNY65B", "n_mon": N_MON, "R_mon_ohm": R_MON,
                "In_A": SPD_IN, "Imax_A": MOV_IMAX, "Up_eff_pe_V": round(sg["Up"]), "Up_eff_pp_V": round(sg["Up"]),
                "Uc_V": sg["uc"], "credit": "Up,eff <= 4.0 kV -> reinforced 6 kV, basic 4 kV (D-032 sec. 3.1)"},
        "cm_choke": {"to_be_set_by": "magnetics design (sim/out/magnetics/design_port_cm_choke.json, MG-10)",
                     "I_cm_LF_A_rms": [round(2 * math.pi * 150 * c * a["A_VCM_LF"], 2) for c in (a["A_C_PV_PE"], a["A_C_PV_PE_110"])],
                     "f_LF_Hz": 150, "V_cm_LF_V_rms": a["A_VCM_LF"], "V_cm_HF_V_rms": a["A_VCM_HF"],
                     "I_cm_HF_max_A_rms": 0.1, "Z_cm_min_ohm": round(a["A_VCM_HF"] / 0.1), "f_HF_band_Hz": [16e3, 100e3],
                     "basis": "HF CM current of the module into the PV array capacitance (16.5-22 uF, 0.1-0.5 ohm at 16-100 kHz) "
                              "<= 0.1 A rms: a third of the 300 mA continuous residual-current level of IEC 62109-2, so the "
                              "PCS / array RCMU does not see the DC/DC switching; |Z_cm| >= 50 ohm from 16 kHz = about 40 dB "
                              "below the no-choke current; the choke must NOT saturate with the LF current above "
                              "(transformerless PCS) - A_VCM_HF is an assumption, CISPR 11 not assessed",
                     "insulation_IC09": "basic winding-core and winding-winding: 1000 V DC, impulse >= 6 kV (no surge "
                                        "credit), AC 2200 V rms 60 s, creepage >= 5.0 mm PD2 / 16.0 mm PD3"},
        "x_cap": {"mpn": "RFQ", "C_F": CX, "per_port": 2, "V_dc": 1300, "ESR_max_ohm": CX_ESR, "V_impulse_min": CX_VIMP},
        "y_cap": {"mpn": "VY1472M63Y5UQ63V0", "C_F": CY, "per_port": 2},
        "imd": {"n_string": N_IMD, "R_elem_ohm": R_IMD_E, "R_elem_mpn": "TNPV1210124KBEEA", "R_t_ohm": im["R_T"],
                "threshold_ohm": im["threshold"], "settle_s_per_state": round(5 * im["tau_s"], 1),
                "C_pv_F": [a["A_C_PV_PE"], a["A_C_PV_PE_110"]], "settle_3tau_s": [round(x, 1) for x in im["settle_s"].values()],
                "prediction_s_per_state": round(im["pred_s"], 2), "pe_returns": {"dividers": "PE_D", "strings": "PE_T"},
                "passive_load_ohm_per_pole": im["R_A"], "pe_open_rule": "no swing between states -> IMD PE open"},
        "control": {"R_fb_pullup_ohm": R_FB_PULLUP, "R_do_pulldown_ohm": 47e3, "coil_series_diode": "S2M-13-F",
                    "en_or_diode": "BAS16", "OV_trip_V": V_OV_TRIP, "fb_ok": itf["fb"]["ok"],
                    "coils_ok": all(r["ok"] for r in itf["rows"])},
        "sensor_supply": {"regulator": "LMR36015ARNXR", "Vout": 3.315, "R_fbt_ohm": 100e3, "R_fbb_ohm": 43.2e3,
                          "L": "74439346100 (10 uH)", "I_load_max_A": 6 * AMC_V_IDD + 2 * AMC_I_IDD},
    }


# =====================================================================================================
# 13. LEAN PORT (D-044, docs/requirements/ARCHITECTURE-COSTFIRST.md sec. 6 and 8): controller on BUS-, one contactor
#     per port, aR fuses on the battery port only, passive bleeders. The cases above stay as the full-protection port.
# =====================================================================================================
# Hongfa HFE82V-300C/1000-24 (Hongfa-HFE82V-300C.pdf, 2024 Rev 1.00): p1 300 A at 85 C (100 mm2); carry 450 A 5 min,
# 600 A 2 min, 900 A 30 s, 1000 A 25 s; 1000 V breaking 200 ops at +300 A and 200 ops at -300 A; 'Breaking: 1 op (Steady
# 1500 A, contact voltage 1000 VDC, no fire, no explosion, 0.6 s on)'; making 7.5e4 ops at 140 A / 20 VDC only; coil
# 24 V 6 W, pick-up <= 18 V, release <= 10 ms, no polarity on load or coil; coil-contact 3000 V AC. p2 breaking-capability
# curve at 1000 V (resistive, L/R <= 1 ms, 23 C, 'for reference only'); p3 endurance curve (functional 130 C, 85 C
# ambient; >= 2 kA 'likely bonded'; safe-circuit points)
KL = dict(ith=300.0, i_norm=300.0, n_norm=200, once=1500.0, make=(140.0, 20.0), release=10e-3, coil_w=6.0, weld=2000.0,
          brk=[(10, 45000), (50, 5000), (300, 200), (500, 12), (1500, 1)],
          carry=[(450, 300.0), (600, 120.0), (900, 30.0), (1000, 25.0), (2000, 4.0)],
          sc=[(5000, 0.35), (6000, 0.15), (7000, 0.06), (8000, 0.006)])
# Hongfa HPE501/000B100-250 (Hongfa-HPE501-1000Vdc.pdf, release 20250729): p1 aR, 1000 V DC, breaking 5 In - 50 kA at
# 1000 V DC (no L/R stated), pre-arc I2t 5270 A2s, clearing I2t 85.2 kA2s, loss <= 76 W at In / <= 36 W at 0.7 In; p2
# pre-arc curve (+/-10 % in current), 250 A curve read by pixel analysis (dashed = outside the breaking range); p2 long-
# term current <= 75 % In; p3 Kt above 40 C (read by pixel analysis)
FA = dict(In=250.0, i_min=1250.0, icu=50e3, i2t_pre=5270.0, i2t_clr=85.2e3, p07=36.0,
          curve=[(792, 10), (866, 5), (947, 3), (1013, 2), (1059, 1.5), (1150, 1), (1230, 0.7), (1296, 0.5), (1365, 0.3),
                 (1396, 0.25), (1433, 0.2), (1556, 0.1), (1702, 0.05), (1932, 0.02), (2178, 0.01), (2595, 5e-3),
                 (3486, 3e-3), (5643, 2e-3), (12502, 1e-3)],
          kt=[(40, 0.907), (50, 0.852), (60, 0.798), (65, 0.772), (70, 0.744), (80, 0.689)])
LEAN = dict(r_sh=100e-6, sh_tol=0.01, sh_tcr=150e-6, dT_sh=80.0, g_hold=10.0, r_tol=0.001, ref_tol=0.002,
            ref_tc=15e-6, cmp_vos=6.5e-3, i_hold=1000.0, i_oc=400.0, v_pol=200.0, dv_win=10.0, g_dv=20.0,
            n_bl=8, r_bl=73.2e3, r_bl_tol=0.01, c_tol=0.10, c_bank={3: 315e-6, 4: 360e-6}, label_min={3: 10, 4: 15},
            r_pre=220.0, t_relay=30e-3, isc_pv={135: 1.25 * 135, 180: 1.25 * 180})
# c_bank = battery-side film bank as drawn (D-056: PV-PWR rev A2 7 x 45 uF, PV-PWR-4 8 x 45 uF); the bleeder went from 8 x 82.5 k
# to 8 x 73.2 k with the seventh capacitor (82.5 k: 10.2 min worst case); gen/pv_power.py adds the leg decoupling on the board


# HFE82V-300C coil (Hongfa-HFE82V-300C.pdf p1 table at 23 C, 24 V coil): pick-up <= 18 V, drop-out >= 2 V, 6 W
# (-> 96 ohm); NO holding voltage, NO coil-resistance tolerance, NO maximum coil voltage stated. p5 'Pick-up / Drop-out
# Voltage Curve' (12 V sample coil, 'sampling values for reference only'), read by pixel analysis and scaled x2:
# pick-up 13.0 V (-39 C) .. 18.6 V (59 C), drop-out 4.3 V (-12 C) .. 5.2 V (77 C).
KC = dict(v_rated=24.0, r23=96.0, r_tol=0.10, alpha=0.00393, v_pu=18.0, v_do=2.0, t_op=30e-3,
          pu_sample=lambda T: 2 * (6.52 + 0.0283 * (T + 38.8)), do_sample=lambda T: 2 * (2.14 + 0.00526 * (T + 12.0)))
# economiser (rev F1): coil return through R_h = 4 x 37.4 ohm 2512 (1 W each), bypassed during pull-in by a PMV30ENEA
# whose gate is an RC differentiator from the coil feed (C_t 1 uF, R_t 56 k, BZX84-C15 clamp, forward-clamps at -0.7 V
# so every fall of the feed resets it). IRFR9214 high side 3.0 ohm at 25 C (IRFR9214.pdf p1), x1.7 hot (assumed).
ECO = dict(r_h=4 * 37.4, p_rh=4 * 1.0, c_t=1e-6, r_t=56e3, v_z=15.0, vth=(1.0, 2.5), r_hs=3.0 * 1.7, v_min=21.0,
           v_max=V24[1], t_amb_hot=70.0, t_hold_rise=10.0, t_cold=-40.0)


def lean(sg):
    """Lean port: coordination of HFE82V-300C/1000 + HPE501 250 A aR, interlock and hold-off thresholds, precharge,
    bleeders, PV-port contactor duty. Returns the numbers, the report lines and the port_spec 'lean' block."""
    L, c = LEAN, KL
    tp = lambda i: interp_loglog(FA["curve"], i) if i >= FA["curve"][0][0] else math.inf    # noqa: E731 pre-arc, s
    tk = lambda i: interp_loglog(c["carry"], i)                                               # noqa: E731 carry, s
    n_brk = lambda i: interp_loglog(c["brk"], i)                                             # noqa: E731 ops at 1000 V
    # hold-off band: shunt 1 % + TCR over dT, two 0.1 % gain resistors, reference, comparator offset at the threshold
    v_thr = L["i_hold"] * L["r_sh"] * L["g_hold"]
    tol = L["sh_tol"] + L["sh_tcr"] * L["dT_sh"] + 2 * L["r_tol"] + L["ref_tol"] + L["ref_tc"] * 60 + L["cmp_vos"] / v_thr
    band = (L["i_hold"] * (1 - tol), L["i_hold"] * (1 + tol))
    oc = (L["i_oc"] * (1 - tol), L["i_oc"] * (1 + tol))
    # installation band: contactor held (>= band low) up to the fuse's minimum breaking current; the fuse must not melt
    # there, so upstream must clear before the fastest pre-arc in the band (top of band, curve 10 % to the left)
    t_req = tp(FA["i_min"] / 0.9)
    inst = dict(i_lo=band[0], i_hi=FA["i_min"], t_s=t_req, ipsc=FA["icu"], lr=1e-3)
    m_hold = tk(FA["i_min"]) / t_req                       # contactor carries the band until upstream clears
    m_fuse = tk(FA["i_min"]) / tp(FA["i_min"] / 1.1)       # above 5 In: slow fuse vs contactor carry
    m_sc = min(i * i * t for i, t in c["sc"]) / FA["i2t_clr"]
    n_at_band = n_brk(band[1])
    kt65 = interp_loglog(FA["kt"], a["A_T_FUSE"])
    fuse = {Ir: dict(lenient=FA["In"] * min(0.75, kt65), strict=FA["In"] * 0.75 * kt65,
                     loss_W=FA["p07"] * (Ir / (0.7 * FA["In"])) ** 2) for Ir in (135, 180)}
    # precharge (battery port) and closing inrush at the worst admitted dV
    k = 1 / 1203.0
    e_div = 2 * TNPV_TOL + 2 * TNPV_TCR * 40          # two strings, ratio + tracking
    e_dv = e_div * V_MAX + 2 * 0.001 * (V_MAX * k) / k + L["cmp_vos"] / L["g_dv"] / k + 0.01 * L["dv_win"]
    dv_band = (L["dv_win"] - e_dv, L["dv_win"] + e_dv)
    e_pol = e_div * L["v_pol"] + L["cmp_vos"] / k + L["ref_tol"] * 1.65 / k
    pol_band = (L["v_pol"] - e_pol, L["v_pol"] + e_pol)
    pre = {}
    for nph, C in L["c_bank"].items():
        tau = L["r_pre"] * C
        i_pk, _ = rlc_peak(dv_band[1], a["A_RS_BATT"] + a["A_R_LOOP"] + a["A_ESR_BUS"], a["A_LS_BATT"] + a["A_L_LOOP"], C)
        p_sh = V_MAX ** 2 / L["r_pre"]
        pre[nph] = dict(tau=tau, t_done=tau * math.log(V_MAX / DV_OK), E=0.5 * C * V_MAX ** 2, i_pk=i_pk, P_short=p_sh,
                        E_short=p_sh * (tau * 1.1 + L["t_relay"]))      # abort at 1.1 tau (+ relay release)
        R = (L["n_bl"] * L["r_bl"] * (1 + L["r_bl_tol"])) * 6.005e6 / (L["n_bl"] * L["r_bl"] * (1 + L["r_bl_tol"]) + 6.005e6)
        pre[nph]["t60_min"] = R * C * (1 + L["c_tol"]) * math.log(a["A_V_TRIP_OS"] / 60.0) / 60
        pre[nph]["P_bleed_W"] = V_MAX ** 2 / (L["n_bl"] * L["r_bl"])
        pre[nph]["t60_800k_min"] = (808e3 * 6.005e6 / (808e3 + 6.005e6)) * C * (1 + L["c_tol"]) * \
            math.log(a["A_V_TRIP_OS"] / 60.0) / 60
    pv = {Ir: dict(isc=isc, brk_margin=c["i_norm"] / isc, carry=c["ith"] / Ir) for Ir, isc in L["isc_pv"].items()}
    # coil economiser: resistances (tolerance not stated: +/-10 % assumed), guaranteed pick-up scales with R(T)
    E, K = ECO, KC
    rc = lambda T, s_=0: K["r23"] * (1 + s_ * K["r_tol"]) * (1 + K["alpha"] * (T - 23))   # noqa: E731
    i_pu = K["v_pu"] / (K["r23"] * (1 - K["r_tol"]))          # guaranteed pick-up current (lowest R unit)
    t_hot = E["t_amb_hot"]
    pull = dict(cold=(E["v_min"] - i_pu * E["r_hs"]) / (K["v_pu"] * rc(E["t_cold"], -1) / K["r23"] / (1 - K["r_tol"])),
                hot_guar=(E["v_min"] - i_pu * E["r_hs"]) / (K["v_pu"] * rc(t_hot, 1) / (K["r23"] * (1 + K["r_tol"]))),
                hot_typ=(E["v_min"] - K["pu_sample"](t_hot) / rc(t_hot) * E["r_hs"]) / K["pu_sample"](t_hot))
    pull["v_needed_hot"] = K["v_pu"] * (1 + K["alpha"] * (t_hot - 23)) + i_pu * E["r_hs"]
    pull["t_guar_max"] = 23 + ((E["v_min"] - i_pu * E["r_hs"]) / K["v_pu"] - 1) / K["alpha"]
    t_hc = t_hot + E["t_hold_rise"]
    r_hot = rc(t_hc, 1)
    v_hold = E["v_min"] * r_hot / (r_hot + E["r_h"])
    i_hold = E["v_min"] / (r_hot + E["r_h"])
    i_do_s = K["do_sample"](t_hc) / rc(t_hc)                  # sample drop-out current (reference only)
    hold = dict(v_coil=v_hold, m_release=v_hold / (K["v_do"] * rc(t_hc, 1) / K["r23"]), m_sample=i_hold / i_do_s)
    rcold = rc(E["t_cold"], -1)
    i_max = E["v_max"] / (rcold + E["r_h"])
    hold.update(P24=24.0 ** 2 / (K["r23"] + E["r_h"]), P_coil24=(24.0 / (K["r23"] + E["r_h"])) ** 2 * K["r23"],
                P_max=E["v_max"] * i_max, P_rh=i_max ** 2 * E["r_h"], rh_load=i_max ** 2 * E["r_h"] / E["p_rh"],
                P_full24=24.0 ** 2 / K["r23"], P_full_max=E["v_max"] ** 2 / rcold)
    tau = E["r_t"] * E["c_t"]
    win = [tau * math.log(E["v_z"] / v) for v in (E["vth"][1], E["vth"][0])]
    v_rel = i_do_s * (r_hot + E["r_h"])                       # supply at which a held coil may release (sample)
    dip = E["v_min"] - v_rel
    t_redo = tau * math.log(max(dip - 0.7, 1e-3) / E["vth"][1]) if dip - 0.7 > E["vth"][1] else 0.0
    # D-050: the hardware polarity / dV enables and the port OC trip stay; firmware duplicates them as a second layer
    # (gen/port.py defaults interlocks='full', oc_trip=True; the D-049 options remain but are not used). Tolerable OC latency: the aR link must not melt before the contactor has opened, for every current
    # the contactor still opens (250 A .. hold-band top); fuse curve 10 % to the left; release 10 ms + 10 ms arcing
    # (assumed); factor 2 margin. Below 792 A the published curve ends at 10 s (dashed) - taken as 10 s.
    t_rel = c["release"] + 10e-3
    t_m = min(min(tp(i / 0.9), 10.0) for i in range(250, int(band[1]) + 1, 2))
    t_m95 = min(min(tp(i / 0.9), 10.0) for i in range(250, 951, 2))
    fw = dict(polarity_enable_V=[round(x) for x in pol_band], dV_enable_V=[round(x, 1) for x in dv_band],
              port_oc_trip_A=[round(x) for x in oc], oc_latency_max_s=round((t_m - t_rel) / 2, 3),
              oc_latency_max_to_950A_s=round((t_m95 - t_rel) / 2, 3),
              rules=["close K_A / K_B only while the terminal-side divider reads >= the polarity enable (both ports)",
                     "close K_B only after precharge with |V_bank - V_term| <= the dV enable; open K_PRE afterwards",
                     "port OC trip from the ADC limit on IA / IB (IM path) -> trip latch -> contactor off, within the latency "
                     "(second layer behind the hardware OC window)",
                     "the hold-off comparator stays in hardware: above 0.97-1.03 kA a closed contactor is held regardless"],
              basis="D-050: firmware duplicates the hardware interlocks and OC trip; bands = the hardware comparators' "
                    "worst-case windows; latency from the HPE501 250 A melting curve (to 0.95 kA, and to the hold-band top)")
    # shunt amplifier filters (rev F1): IM 30.1 k || 470 pF, IH (hold-off + OC windows) 10.0 k || 1 nF of its own
    fc_im, fc_ih = 1 / (2 * math.pi * 30.1e3 * 470e-12), 1 / (2 * math.pi * 10.0e3 * 1e-9)
    n_ih = 7e-9 * 11 * math.sqrt(math.pi / 2 * fc_ih)            # OPA2388 7 nV/rtHz x noise gain 11, rms at IH
    filt = dict(fc_im=fc_im, fc_ih=fc_ih, t90_ih=2.3 / (2 * math.pi * fc_ih), noise_ih_A=n_ih / (10 * L["r_sh"]),
                oc_margin_sigma=(L["i_oc"] - oc[0]) * 10 * L["r_sh"] / n_ih)
    # shared reference (rev F1): one REF3030 ladder + one VMID follower for both ports. VMID load change from the
    # other port's dividers (2 x 0.19 mA) and shunt-amplifier reference legs, through an assumed 0.1 ohm follower output
    # impedance -> shift referred to the polarity check and to the IH path
    dv_mid = 0.1 * 2 * a["A_V_TRIP_OS"] / 6.005e6
    shared = dict(dV_pol=dv_mid * 1203.0, dI_ih=dv_mid / (10 * L["r_sh"]))
    # contactor per source type: the PV port only makes / breaks the array current (no fuse, no precharge)
    i_make_pv = a["A_I_MAKE_PV"]
    cont = {"HFE82V": dict(mpn="HFE82V-300C/1000-24-H-C5-1", ith_85C=c["ith"], break_1000V_both=c["i_norm"],
                           n_break=c["n_norm"], make_published=list(c["make"]), sc_I2t_min=min(i * i * t for i, t in c["sc"]))}
    per_src = {"pv": "HFE82V", "battery": "HFE82V"}
    pv_make = {Ir: dict(dV_max=V_MAX, i_make_est=L["isc_pv"][Ir] + i_make_pv,
                        covered=False) for Ir in L["isc_pv"]}
    eco = dict(pull=pull, hold=hold, window=win, dip_release_V=v_rel, t_redo=t_redo, i_pu=i_pu, P_qb_pull=(E["v_max"] / rcold) ** 2 * 0.03)
    res = dict(band=band, oc=oc, tol=tol, inst=inst, m_hold=m_hold, m_fuse=m_fuse, m_sc=m_sc, n_at_band=n_at_band,
               fuse=fuse, kt65=kt65, dv_band=dv_band, pol_band=pol_band, pre=pre, pv=pv,
               t_pre_open=tp(band[1] / 0.9), m_oc=tp(band[1] / 0.9) / 0.03, eco=eco, fw=fw, filt=filt, shared=shared, cont=cont, per_src=per_src,
               pv_make=pv_make)
    res["lines"] = [
        "", "## 13 Lean port (D-044) - one contactor per port, aR fuses on the battery port, passive bleeders", "",
        "Parts: Hongfa HFE82V-300C/1000-24 (p1-p3) and HPE501/000B100-250 aR (p1-p3), both read here; the rev-6 varistor "
        "network unchanged (sec. 9: Up,eff %.0f V). **Calculated, not tested.**" % sg["Up"],
        "", "| item | value | basis |", "|---|---|---|",
        "| hold-off band (contactor held closed above) | %.0f-%.0f A | 1.0 kA nominal on the low-gain shunt path: shunt 1 %%, "
        "TCR 150 ppm/K (CSS2H L200 not stated; assumed) x 80 K, 2 x 0.1 %%, reference, comparator 6.5 mV at %.2f V = +/-%.1f %% |" % (band[0], band[1], v_thr, 100 * tol),
        "| contactor openings at the band top (1000 V) | %.1f | log-log between 500 A x 12 and 1500 A x 1 (p2, L/R <= 1 ms) |" % n_at_band,
        "| port over-current trip (new latch source) | %.0f-%.0f A | same path; up to the band top the contactor opens: aR pre-arc "
        "there %.2f s (curve 10 %% left) vs trip + release 30 ms (x%.0f) - the link never sees its partial range |" %
        (oc[0], oc[1], res["t_pre_open"], res["m_oc"]),
        "| installation band | %.0f-%.0f A within %.2f s | contactor held, aR link below its 5 In breaking range; fastest pre-arc "
        "at 5 In with the curve 10 %% left |" % (inst["i_lo"], inst["i_hi"], t_req),
        "| contactor carry vs that time | x%.0f | carry at 1.25 kA (p3 functional) / %.2f s |" % (m_hold, t_req),
        "| above 5 In: contactor carry vs slow fuse | x%.1f | carry at 1.25 kA / pre-arc at 1.25 kA with the curve 10 %% right |" % m_fuse,
        "| short circuit to 50 kA: contactor I2t vs fuse clearing I2t | x%.1f | 8 kA x 6 ms (p3) / 85.2 kA2s (p1); >= 2 kA the "
        "contacts are 'likely bonded' - weld check blocks restart |" % m_sc,
        "| HPE501 250 A at %.0f C fuse air | Kt %.3f: %.0f A (In x min(0.75, Kt)) / %.0f A (0.75 x Kt x In) | 135 A: both; "
        "180 A: lenient reading only; loss %.0f W (135 A) / %.0f W (180 A) per link |" % (a["A_T_FUSE"], kt65,
                                                                                    fuse[135]["lenient"], fuse[135]["strict"],
                                                                                    fuse[135]["loss_W"], fuse[180]["loss_W"]),
        "| reverse-polarity enable (VBX, VAX) | %.0f-%.0f V | 200 V nominal; divider 2 x 0.1 %% + 25 ppm/K, comparator 6.5 mV, "
        "mid-scale reference 0.2 %% |" % pol_band,
        "| precharge dV enable | %.1f-%.1f V | 10 V window on a G = 20 difference amplifier of the two dividers |" % dv_band,
    ] + ["| precharge %d phases (%.0f uF) | tau %.0f ms, dV <= 10 V after %.2f s, %.0f J per attempt; closing peak %.0f A at "
         "%.1f V | shorted bank: %.1f kW, %.0f J to the abort at 1.1 tau + relay |" %
         (n, L["c_bank"][n] * 1e6, d["tau"] * 1e3, d["t_done"], d["E"], d["i_pk"], dv_band[1], d["P_short"] / 1e3, d["E_short"])
         for n, d in pre.items()] + [
        "| bleeder %d phases | %.1f min to 60 V worst case (%d x %.1f k, label %d min); 800 k: %.1f min | R +1 %%, C +10 %%, from "
        "%.0f V, divider in parallel; %.2f W at 1000 V |" % (n, d["t60_min"], L["n_bl"], L["r_bl"] / 1e3, L["label_min"][n], d["t60_800k_min"],
                                                          a["A_V_TRIP_OS"], d["P_bleed_W"]) for n, d in pre.items()] + [
        "| contactor coil economiser (both ports) | hold %.2f W at 24 V (coil %.2f W), %.2f W max at 26.45 V; full drive "
        "%.1f / %.1f W | R_h 4 x 37.4 ohm 2512 bypassed for %.0f-%.0f ms by a PMV30ENEA (RC from the coil feed) |" %
        (eco["hold"]["P24"], eco["hold"]["P_coil24"], eco["hold"]["P_max"], eco["hold"]["P_full24"], eco["hold"]["P_full_max"],
         1e3 * eco["window"][0], 1e3 * eco["window"][1]),
        "| hold at 21.0 V, coil %.0f C (+10 %%) | %.1f V on the coil: x%.1f over must-release (2 V scaled), x%.2f over the "
        "SAMPLE drop-out (p5, reference only) | R_h %.2f W = %.0f %% of 4 W |" % (ECO["t_amb_hot"] + ECO["t_hold_rise"],
                                                                                 eco["hold"]["v_coil"], eco["hold"]["m_release"],
                                                                                 eco["hold"]["m_sample"], eco["hold"]["P_rh"],
                                                                                 100 * eco["hold"]["rh_load"]),
        "| pull-in at 21.0 V | coldest x%.2f (guaranteed); %.0f C x%.2f guaranteed / x%.2f typical (sample) | guaranteed only to "
        "%.0f C at 21.0 V; %.1f V needed at %.0f C (incl. %.1f ohm hot high side) |" % (
            eco["pull"]["cold"], ECO["t_amb_hot"], eco["pull"]["hot_guar"], eco["pull"]["hot_typ"], eco["pull"]["t_guar_max"],
            eco["pull"]["v_needed_hot"], ECO["t_amb_hot"], ECO["r_hs"]),
        "| re-pull-in after a dip | a held coil may release below %.1f V (sample); recovering from there gives a %.0f ms "
        "bypass pulse (operate <= 30 ms) | any fall of the feed resets the RC |" % (eco["dip_release_V"], 1e3 * eco["t_redo"])] + [
        "| firmware second layer (D-050) | polarity enable %d-%d V; dV enable %.1f-%.1f V "
        "before K_B closes; OC trip %d-%d A within %.2f s (%.2f s if only to 0.95 kA) | latency = (fastest aR melting "
        "250 A .. band top, curve 10 %% left, - 20 ms release) / 2 |" % (fw["polarity_enable_V"][0], fw["polarity_enable_V"][1],
                                                                        fw["dV_enable_V"][0], fw["dV_enable_V"][1],
                                                                        fw["port_oc_trip_A"][0], fw["port_oc_trip_A"][1],
                                                                        fw["oc_latency_max_s"], fw["oc_latency_max_to_950A_s"])] + [
        "| shunt amplifier filters (rev F1) | IM %.1f kHz (470 pF, >= 10 kHz control need); IH %.1f kHz on its OWN 1 nF "
        "(hold-off and OC windows unchanged): 90 %% in %.0f us, noise %.2f A rms = %.0f sigma below the OC trip | "
        "OPA2388 chopping frequency not stated in its sheet (ripple-reduction loop) |" % (filt["fc_im"] / 1e3, filt["fc_ih"] / 1e3,
                                                                                    1e6 * filt["t90_ih"], filt["noise_ih_A"],
                                                                                    filt["oc_margin_sigma"]),
        "| one reference / VMID for both ports (rev F1) | other-port load moves the shared VMID by <= %.0f uV: %.2f V at "
        "the polarity check, %.2f A on IH | static coupling only; a reference fault disables both ports' windows (common "
        "cause, accepted under D-044 'no dual channel') |" % (1e6 * dv_mid, shared["dV_pol"], shared["dI_ih"])] + [
        "| PV port contactor (rev F1) | HFE82V-300C kept: no smaller Hongfa 1000 V part with published data (HFE82V at "
        "1000 V: 300C / 400C / 600; HFE85V-150 is a 750 V part); worst closing allowed: empty bank at Voc %.0f V (no "
        "precharge, polarity interlock only) -> make about %.0f / %.0f A (135 / 180 A port, estimate) vs published make "
        "140 A at 20 V only - NOT covered | Hongfa to confirm (R-04) |" % (V_MAX, pv_make[135]["i_make_est"],
                                                                           pv_make[180]["i_make_est"])] + [
        "| PV port %d A: array Isc %.0f A | break margin x%.2f (300 A x 200 ops, both directions), carry x%.2f | no fuse; make at "
        "1000 V not published |" % (Ir, d["isc"], d["brk_margin"], d["carry"]) for Ir, d in pv.items()] + [
        "",
        "**Installation requirement (battery / DC-bus side):** upstream protection interrupts any current of %.2f-%.2f kA "
        "into the battery port within %.2f s; prospective short-circuit current <= 50 kA at the module terminals with L/R "
        "<= 1 ms (the contactor's breaking data are at L/R <= 1 ms; Hongfa states no L/R for the HPE501's 50 kA); the cable "
        "to the module is protected upstream." % (inst["i_lo"] / 1e3, inst["i_hi"] / 1e3, t_req),
        "**Not supported by the published data:** '1.5 kA once' is a no-fire/no-explosion test (p1), not a rated "
        "interruption with the contactor still usable; the 1.0 kA rule rests on an interpolation (%.1f openings) at L/R <= "
        "1 ms - not at the architecture's 3 ms; making at 1000 V (K_A closing on an empty bank) - only 140 A / 20 V is "
        "published; the HPE501's L/R, let-through peak and DC curve conditions; the 180 A port's fuse loading (needs Hongfa's "
        "reading of Kt vs the 75 %% rule). **Disagree:** 800 kOhm bleeders reach 60 V only after %.1f min worst case (3 "
        "phases) - %d x %.1f k gives %.1f min; and a port over-current trip (%.0f A) is needed, or a battery-fed fault "
        "of 250 A-0.95 kA reaches the aR link's partial range." % (n_at_band, pre[3]["t60_800k_min"], L["n_bl"], L["r_bl"] / 1e3,
                                                                  pre[3]["t60_min"], L["i_oc"])]
    res["spec"] = {
        "contactor": {"mpn": "HFE82V-300C/1000-24-H-C5-1", "mfr": "Hongfa", "I_open_normal_max_A": c["i_norm"],
                      "hold_off_band_A": [round(x) for x in band], "openings_at_band_top": round(n_at_band, 1),
                      "break_once_published_A": c["once"], "coil_W": c["coil_w"], "release_s": c["release"]},
        "battery_fuse": {"mpn": "HPE501/000B100-250", "mfr": "Hongfa", "In_A": FA["In"], "I_min_break_A": FA["i_min"],
                         "I2t_pre_A2s": FA["i2t_pre"], "I2t_clear_A2s": FA["i2t_clr"], "Kt_65C": round(kt65, 3),
                         "I_allowed_65C_A": {"lenient": round(fuse[135]["lenient"]), "strict": round(fuse[135]["strict"])},
                         "loss_W_per_link": {str(Ir): round(f["loss_W"], 1) for Ir, f in fuse.items()}},
        "installation": {"band_A": [round(inst["i_lo"]), round(inst["i_hi"])], "clear_within_s": round(t_req, 3),
                         "Ipsc_max_A": FA["icu"], "L_R_max_s": inst["lr"]},
        "port_oc_trip_A": [round(x) for x in oc], "polarity_enable_V": [round(x) for x in pol_band],
        "precharge": {"R_ohm": L["r_pre"], "dV_enable_V": [round(x, 1) for x in dv_band],
                      "per_phases": {str(n): {k2: round(v, 3) for k2, v in d.items()} for n, d in pre.items()}},
        "bleeder": {"n": L["n_bl"], "R_elem_ohm": L["r_bl"], "label_min": L["label_min"]},
        "shunt": {"R_ohm": L["r_sh"], "build": "2 x 200 uOhm 3920 metal strip in parallel, BUS-", "G_hold_path": L["g_hold"]},
        "pv_port": {str(Ir): {k2: round(v, 2) for k2, v in d.items()} for Ir, d in pv.items()},
        "varistor_network": "unchanged: port_spec spd", "firmware_requirements": fw,
        "contactors": cont, "contactor_per_source": per_src,
        "pv_make": {str(k): v for k, v in pv_make.items()},
        "shunt_filters_Hz": {"IM": round(fc_im), "IH": round(fc_ih)}, "shared_reference": True,
        "coil_economiser": {"R_hold_ohm": ECO["r_h"], "pull_in_window_s": [round(x, 3) for x in eco["window"]],
                            "pull_in_W_at_24V": round(eco["hold"]["P_full24"], 2), "hold_W_at_24V": round(eco["hold"]["P24"], 2),
                            "hold_W_max": round(eco["hold"]["P_max"], 2), "hold_coil_V_at_21V_hot": round(eco["hold"]["v_coil"], 2),
                            "pull_in_supply_needed_V_at_70C": round(eco["pull"]["v_needed_hot"], 2),
                            "pull_in_guaranteed_to_C_at_21V": round(eco["pull"]["t_guar_max"], 1),
                            "precharge_relay": "no economiser: energised < 1 s per attempt"}}
    return res


def lean_checks(r):
    L = LEAN
    ensure(r["band"][0] >= 950 and r["band"][1] <= 1050 and r["n_at_band"] >= 1.0,
           "lean: hold-off band inside 0.95-1.05 kA and at least one published-curve opening at its top")
    ensure(r["m_hold"] > 2 and r["m_fuse"] > 2 and r["m_sc"] > 2 and r["m_oc"] > 10,
           "lean: contactor carry vs installation time / slow fuse, short-circuit I2t, OC trip vs aR partial range")
    ensure(r["fuse"][135]["strict"] >= 135, "lean: HPE501 250 A must carry 135 A at the fuse air under the strict reading")
    ensure(0 < r["pol_band"][0] and r["pol_band"][1] <= 250 and r["dv_band"][1] <= 20,
           "lean: polarity enable above 0 V and <= 250 V; precharge dV enable <= 20 V")
    ensure(all(d["t60_min"] <= L["label_min"][n] and d["t_done"] < 1.0 for n, d in r["pre"].items()),
           "lean: bleeder 60 V within the label time; precharge done within 1 s")
    f_ = r["filt"]
    ensure(f_["fc_im"] >= 10e3 and f_["t90_ih"] < 1e-3 and f_["oc_margin_sigma"] > 100 and r["shared"]["dI_ih"] < 1.0,
           "lean: IM bandwidth >= 10 kHz, IH response and noise at the OC trip, shared-reference coupling")
    for src, k in r["per_src"].items():         # coordination per source type
        cc = r["cont"][k]
        ensure(cc["ith_85C"] >= 1.5 * 180 and (src == "battery" or cc["break_1000V_both"] >= 1.2 * max(LEAN["isc_pv"].values())),
               "lean: %s-port contactor carry / array-current break at 1000 V" % src)
    ensure(r["fw"]["oc_latency_max_s"] >= 0.1, "lean: firmware OC trip must have a usable latency budget")
    e = r["eco"]
    ensure(e["hold"]["m_release"] >= 2.0 and e["hold"]["m_sample"] >= 1.4 and e["hold"]["rh_load"] <= 0.6
           and e["hold"]["P_max"] <= 3.5 and e["window"][0] >= 3 * KC["t_op"] and e["t_redo"] >= 1.5 * KC["t_op"],
           "lean: economiser hold margin (must-release, sample), R_h load, hold power, pull-in window, re-pull-in after a dip")
    ensure(e["pull"]["cold"] >= 1.2 and e["pull"]["hot_typ"] >= 1.0,
           "lean: pull-in at the coldest coil (guaranteed) and at the hottest (typical) at 21.0 V")
    ensure(all(d["brk_margin"] >= 1.2 and d["carry"] >= 1.5 for d in r["pv"].values()),
           "lean: PV contactor breaks the array Isc with 20 % margin and carries the rating")


# =====================================================================================================
# 14. PCS-P125 DC PORT (PCM-15): HIITIO HCHVF1000-400A-38R per pole + Hongfa HFE82V-300C, the lean port's hold-off and OC windows
# =====================================================================================================
# HIITIO HCHVF1000-400A-38R (protection/HIITIO-HCHVF1000-Series.pdf, V.20240903010): p1 aR 1000 V DC, breaking 50 kA at tau 2.5
# (+0.5) ms; p2 400 A row: melting / clearing I2t 43,083 / 289,502 A2s 'average @ 50 kA / 1000 V DC', 112 W.  p4 time-current
# curves and the cut-off chart are VECTOR paths in the PDF: the 400 A curve (stroke 0.824 0.376 0.0706, legend '400A') and the 400 A
# cut-off line (stroke 0.149 0.267 0.471) read from the page content stream; axes = the plot clip boxes (100..100,000 A x
# 0.001..1000 s; 100..100,000 A x 100..100,000 A).  The curve is dashed up to 1.19 kA: taken as outside the breaking range (as the
# HPE501's dashed part), and as the PRE-ARC time (the sheet does not say).  The cut-off chart's x axis is 'prospective symmetrical RMS'
# (AC terms): read here as the DC prospective current, ASSUMED.
F400_BEZ = [((181.45, 877.16), (199.68, 810.82), (211.71, 747.1), (236.14, 678.16)),        # dashed part
            ((236.14, 678.16), (260.57, 609.22), (297.08, 507.4), (328.03, 463.51)),
            ((328.03, 463.51), (358.47, 420.32), (406.43, 422.78), (421.81, 414.79))]
F400_AX = (112.44, 344.58, 388.68, 383.64)          # clip box x0, width, y0, height: 3 decades of A from 100 A, 6 of s from 1 ms
F400 = dict(In=400.0, i2t_melt=43083.0, i2t_clr=289502.0, icu=50e3, tau=2.5e-3, cut=((2150.0, 4651.0), (70e3, 11.0e3)))
# Hongfa HFE82V-300C (Hongfa-HFE82V-300C.pdf) p5 'Endurance Capacity Curve' (85 C, >= 100 mm2; room temperature above 2 kA):
# functional (130 C) and safe-operation (180 C) curves up to 2 kA, then the short-circuit capacity curve; notes 6-8: >= 2 kA the
# contacts are likely bonded, >= 6 kA likely to bounce ('may explode ... if the fuse cannot be fused in time'), >= 8 kA severe bounce
K_SC_CAP = [(3000, 1.5), (5000, 0.35), (6000, 0.15), (7000, 0.06), (8000, 0.006), (10000, 0.0015)]
K_FUN = [(450, 300.0), (600, 120.0), (900, 30.0), (1000, 25.0), (2000, 2.5)] + K_SC_CAP
K_SAFE = [(450, 500.0), (600, 200.0), (900, 65.0), (1000, 50.0), (2000, 4.0)] + K_SC_CAP
K_BOUNCE = (6000.0, 8000.0)


def f400_curve():
    """(I, t) points of the 400 A curve from its PDF vector path, sorted; and the first solid-line current (I_min)"""
    x0, w, y0, h = F400_AX
    pts = []
    for k, (p0, p1, p2, p3) in enumerate(F400_BEZ):
        for t in np.linspace(0.0, 1.0, 81):
            x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0]
            y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1]
            if y0 <= y <= y0 + h:
                pts.append((10 ** (2 + 3 * (x - x0) / w), 10 ** (-3 + 6 * (y - y0) / h)))
    pts = sorted(set(pts))
    x_min = F400_BEZ[1][0][0]
    return pts, 10 ** (2 + 3 * (x_min - x0) / w)


def pcs_port(ln):
    """PCM-15: coordination of the PCS-PWR DC port (HCHVF1000-400A-38R per pole, HFE82V-300C, hold-off 0.97-1.03 kA, OC 387-413 A)
    over the battery's prospective current range (the lean port's installation basis: <= 50 kA, L/R <= 1 ms; the fuse's own rating
    basis tau 2.5 ms).  Bands: where the contactor breaks, where nothing clears quickly, where the fuse clears before the contactor's
    limit, and where it does not (time and peak).  CALCULATED from data-sheet curves; nothing tested."""
    pts, i_min = f400_curve()
    tp = lambda i: interp_loglog(pts, i) if pts[0][0] <= i <= pts[-1][0] else (math.inf if i < pts[0][0] else  # noqa: E731
                                                                              pts[-1][1] * (pts[-1][0] / i) ** 2)
    tk = lambda c, i: interp_loglog(c, i) if i <= c[-1][0] else c[-1][1] * (c[-1][0] / i) ** 2              # noqa: E731
    band, oc = ln["band"], ln["oc"]
    (c0, p0), (c1, p1) = F400["cut"]
    cut = lambda i: i if i <= c0 else 10 ** (math.log10(p0) + (math.log10(p1) - math.log10(p0)) *            # noqa: E731
                                              (math.log10(i) - math.log10(c0)) / (math.log10(c1) - math.log10(c0)))

    def adiabatic_peak(ip, tau):
        """current when the melting I2t is reached for i = ip (1 - exp(-t/tau)) - a lower bound of the DC peak"""
        e = lambda t: ip ** 2 * (t - 2 * tau * (1 - math.exp(-t / tau)) + tau / 2 * (1 - math.exp(-2 * t / tau)))  # noqa: E731
        lo, hi = 0.0, 1.0
        for _ in range(80):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if e(mid) < F400["i2t_melt"] else (lo, mid)
        return ip * (1 - math.exp(-lo / tau))
    rows, gaps = [], []
    for i in np.logspace(math.log10(band[0]), math.log10(F400["icu"]), 400):
        slow, fun, safe = tp(i / 1.1), tk(K_FUN, i), tk(K_SAFE, i)
        pk = min(i, cut(i))
        rows.append(dict(I=float(i), t_fuse_slow=slow, t_fun=fun, t_safe=safe, ok=bool(i >= i_min and slow <= safe), peak=pk))
    bad = [r["I"] for r in rows if not r["ok"]]
    for r in rows:
        if not r["ok"] and (not gaps or r["I"] > gaps[-1][1] * 1.02):
            gaps.append([r["I"], r["I"]])
        elif not r["ok"]:
            gaps[-1][1] = r["I"]
    lo_gap, hi_gap = gaps[0], (gaps[1] if len(gaps) > 1 else None)
    ok = [r for r in rows if r["ok"]]
    n_brk = lambda i: interp_loglog(KL["brk"], i)                                                          # noqa: E731
    t_req = min(r["t_fun"] for r in rows if lo_gap[0] <= r["I"] <= lo_gap[1])
    pk50 = {f"tau {t * 1e3:.1f} ms": adiabatic_peak(F400["icu"], t) for t in (1e-3, F400["tau"])}
    i_bounce = [min((r["I"] for r in rows if r["peak"] >= b), default=math.inf) for b in K_BOUNCE]
    res = dict(i_min=i_min, band=band, oc=oc, gap_low=lo_gap, gap_high=hi_gap, coordinated=(ok[0]["I"], ok[-1]["I"]) if ok else None,
               n_break_at_band_top=n_brk(band[1]), t_fuse_fast_at_band_top=tp(band[1] / 0.9), t_req=t_req,
               peak_chart_50kA=cut(F400["icu"]), peak_adiabatic_50kA=pk50, i_bounce=i_bounce,
               k_i2t_10kA=10000.0 ** 2 * 0.0015, k_i2t_8kA=8000.0 ** 2 * 0.006)
    hg = ("%.1f-%.0f kA" % (hi_gap[0] / 1e3, hi_gap[1] / 1e3)) if hi_gap else "none"
    res["lines"] = [
        "", "## 14 PCS-P125 DC port (PCM-15): HIITIO HCHVF1000-400A-38R per pole + HFE82V-300C, lean port windows", "",
        "Both parts read here (fuse p1, p2, p4 vector paths; contactor p1, p4, p5).  **Calculated from data-sheet curves, not tested.**  "
        "The design intent (D-019 / D-050): the contactor never interrupts a fault above the hold-off band - it is held and the fuse clears.",
        "", "| band | current | what clears it | verdict | basis |", "|---|---|---|---|---|",
        "| port over-current window -> contactor opens | %.0f-%.0f A up to the hold-off band %.0f-%.0f A | the contactor breaks, "
        "<= %.1f openings at the band top (1000 V, L/R <= 1 ms, p4 'reference only') | ok; the fast fuse (curve 10 %% left) needs "
        "%.0f s at the band top against 30 ms trip + release | KL brk, F400 curve |" % (oc[0], oc[1], band[0], band[1],
                                                                                     res["n_break_at_band_top"],
                                                                                     res["t_fuse_fast_at_band_top"]),
        "| hold-off band to where the fuse is faster than the contactor | %.2f-%.2f kA | NOTHING quickly: contactor held, fuse below "
        "its breaking range (dashed to %.2f kA) or slower than the contactor's 180 C curve | **installation requirement**: upstream "
        "clears within the contactor's 130 C curve, %.1f s at the band top | slow fuse (curve 10 %% right) vs p5 safe curve |"
        % (lo_gap[0] / 1e3, lo_gap[1] / 1e3, i_min / 1e3, t_req),
        "| fuse clears first | %s | the 400 A link (pre-arc %.2f s ... %.3f s) | coordinated; >= 2 kA the contacts are likely bonded "
        "(weld check: firmware sees the bank follow the terminal) | slow fuse <= p5 safe curve |"
        % (("%.1f-%.1f kA" % (res["coordinated"][0] / 1e3, res["coordinated"][1] / 1e3)) if ok else "none",
           tp(res["coordinated"][0] / 1.1) if ok else math.nan, tp(res["coordinated"][1] / 1.1) if ok else math.nan),
        "| above that, to the fuse's 50 kA | %s | the fuse, but after the contactor's short-circuit capacity (8 kA 6 ms, 10 kA 1.5 ms); "
        "let-through peak >= 6 kA from %.1f kA, >= 8 kA from %.1f kA prospective (bounce; Hongfa: may explode if the fuse is late) | "
        "**NOT coordinated**: installation requirement | p4 curve 10 %% right vs p5 capacity; cut-off chart |"
        % (hg, i_bounce[0] / 1e3, i_bounce[1] / 1e3),
        "| current-limiting region | 50 kA prospective | peak %.1f kA (cut-off chart, AC terms) / %.1f-%.1f kA (DC at the melting "
        "I2t, tau 2.5 / 1 ms); clearing I2t %.0f kA2s (average) | contactor at >= 8 kA: %.0f kA2s (8 kA 6 ms), %.0f kA2s (10 kA 1.5 ms) "
        "- the I2t comparison of rev A0 (289.5 <= 384 kA2s) used the 8 kA point only | p2, p4, p5 |"
        % (res["peak_chart_50kA"] / 1e3, pk50["tau 2.5 ms"] / 1e3, pk50["tau 1.0 ms"] / 1e3, F400["i2t_clr"] / 1e3,
           res["k_i2t_8kA"] / 1e3, res["k_i2t_10kA"] / 1e3),
        "", "**Installation requirement (PCS-P125 battery side, CALCULATED):** the battery-side protection must interrupt any current of "
        "%.2f-%.2f kA into the PCS DC port within %.1f s (the contactor's 130 C curve at the band top; 25 s at 1 kA), and either limit "
        "the battery's prospective short-circuit current at the PCS DC terminals to <= %.1f kA (L/R <= 1 ms) or interrupt currents "
        "above it within the contactor's short-circuit capacity (6 ms at 8 kA, 1.5 ms at 10 kA) - the PCS's own 400 A links protect the "
        "contactor only between %s.  The fuse data are averages from a chart with no tolerance band (+/-10 %% in current assumed); its "
        "high-current tail (2.5-6 ms at 20-50 kA) is not consistent with the tabulated 43 kA2s melting I2t (adiabatic melting at 50 kA "
        "takes < 1 ms) - an RFQ / test item." % (lo_gap[0] / 1e3, lo_gap[1] / 1e3, t_req, (hi_gap[0] if hi_gap else F400["icu"]) / 1e3,
                                                 ("%.1f-%.1f kA" % (res["coordinated"][0] / 1e3, res["coordinated"][1] / 1e3)) if ok else "no range")]
    res["spec"] = {
        "fuse": {"mpn": "HCHVF1000-400A-38R", "mfr": "Zhejiang HIITIO", "In_A": F400["In"], "I_min_break_A_read": round(i_min),
                 "I2t_melt_A2s": F400["i2t_melt"], "I2t_clear_A2s": F400["i2t_clr"], "Icu_A": F400["icu"], "tau_s": F400["tau"]},
        "contactor": {"mpn": "HFE82V-300C/1000-24-H-C5-1", "sc_capacity": K_SC_CAP, "bounce_A": list(K_BOUNCE)},
        "oc_band_A": [round(x) for x in oc], "hold_off_band_A": [round(x) for x in band],
        "contactor_breaks_up_to_A": round(band[1]), "openings_at_band_top": round(res["n_break_at_band_top"], 1),
        "installation_band_A": [round(lo_gap[0]), round(lo_gap[1])], "installation_clear_within_s": round(t_req, 2),
        "coordinated_A": [round(x) for x in res["coordinated"]] if ok else None,
        "not_coordinated_above_A": round(hi_gap[0]) if hi_gap else None, "Ipsc_max_for_coordination_A": round(hi_gap[0]) if hi_gap else F400["icu"],
        "peak_at_50kA_A": {"cut_off_chart": round(res["peak_chart_50kA"]), **{k: round(v) for k, v in pk50.items()}},
        "bounce_from_prospective_A": [round(x) for x in i_bounce],
        "statement": res["lines"][-1].replace("**Installation requirement (PCS-P125 battery side, CALCULATED):** ", "")}
    return res


def pcs_port_checks(p):
    ensure(F400_BEZ[0][3] == F400_BEZ[1][0] and F400_BEZ[1][3] == F400_BEZ[2][0], "PCS fuse curve: path segments must join")
    ensure(1100.0 <= p["i_min"] <= 1300.0 and p["band"][1] < p["i_min"], "PCS fuse: dashed/solid boundary above the hold-off band")
    ensure(p["n_break_at_band_top"] >= 1.0 and p["t_fuse_fast_at_band_top"] > 100 * 0.03,
           "PCS port: the contactor must still open once at the hold-off band top before the fuse melts")
    ensure(p["gap_low"][0] <= p["band"][0] * 1.001, "PCS port: the installation band starts at the hold-off band")


def main():
    os.makedirs(OUT, exist_ok=True)
    found, cn = bus_capacitances()
    C_cases = sorted([("envelope min", C_RANGE[0]), ("envelope max", C_RANGE[1])] + found, key=lambda c: c[1])
    real = [c for c in C_cases if not c[0].startswith("envelope")]          # never empty: bus_capacitances() fails closed
    pc = precharge(C_cases)
    ir = inrush(C_cases)
    tl = time_limit()
    dc = discharge(C_cases, tl)
    dv = divider()
    cs = {Ir: current_sense(float(Ir)) for Ir in VARIANTS}
    hold = hold_threshold()
    co = {Ir: coordination(float(Ir), hold, real) for Ir in VARIANTS}
    cd = coil_drive()
    bp = battery_ports(hold, real, cd)
    itf = interface(cd)
    pt = proof_test(hold)
    pn = precharge_null(dv, ir)
    im = imd()
    sg = surge()
    yc = ycap()
    sp = spice(C_RANGE[1])
    plots(pc, sp, co, im, hold, C_cases)
    report(cn, pc, ir, dc, dv, cs, co, hold, itf, im, sg, yc, sp, cd, tl, pt, pn, bp)
    ln = lean(sg)
    pp = pcs_port(ln)
    open(os.path.join(OUT, "report.md"), "a").write("\n".join(ln["lines"] + pp["lines"]) + "\n")
    js = spec(pc, dv, cs, co, hold, itf, im, cn, dc, cd, tl, pt, pn, bp, sg)
    js["lean"] = ln["spec"]
    js["pcs_port"] = pp["spec"]
    pcs_port_checks(pp)
    json.dump(js, open(os.path.join(OUT, "port_spec.json"), "w"), indent=1)
    lean_checks(ln)

    # ------------------------------------------------------------------ self-check
    irmax = [r for r in ir if r["C_uF"] == C_RANGE[1] * 1e6][0]
    ensure(all(r["t_ok_hi_s"] < pc["t_out"] for r in pc["rows"]), "precharge (R_pre +5 %, hot) must beat the time-out")
    ensure(pc["chk_margin"] > 1.0, "slow-rise check would abort a healthy bus with R_pre at its upper limit")
    ensure(all(r["E_J"] <= r["E_allow_J"] for r in pc["rows"]), "precharge pulse above the RST 200 rating")
    ensure(pc["attempts_E"] <= pc["E_allow"], "N attempts at worst-case fault energy exceed the RST 200 rating")
    ensure(V_MAX <= RST_VMAX, "RST 200 precharge voltage limit")
    ensure(pc["shorted"]["outcome"] == "abort-slow-rise" and pc["shorted"]["t_end_s"] < pc["survival_shorted_s"]
           and pc["shorted_ok"], "shorted bus must be aborted by the slow-rise check before the resistor's limit")
    ensure(all(s["outcome"] != "complete" for s in pc["sweep"] if 0 < s["R_load_over_R"] < 30),
           "an overloaded bus must never be reported as precharged")
    ensure(V_MAX / R_PRE < PR_I_RATED_1000, "precharge relay current above its 1000 V rating")
    ensure(max(r["I_bound"] for r in ir) <= 1.05 * K_MAKE_CAP[1], "closing inrush above the TDK make test current")
    ensure(abs(sp["t_dv_spice"] - sp["t_close"]) / sp["t_close"] < 0.03, "ngspice precharge time differs > 3 %")
    ensure(abs(sp["i_inrush_spice"] - irmax["I_bound"]) / irmax["I_bound"] < 0.10, "ngspice inrush differs > 10 %")
    ensure(max(r["t_active_s"] for r in dc["rows"]) < 5.0, "active discharge slower than 5 s at the worst C")
    ensure(all(r["E_per_R_J"] <= r["E_allow_J"] for r in dc["rows"]), "normal discharge above the RST 200 pulse rating")
    ensure(all(c["ok"] for c in dc["cases"]), "a discharge fault case exceeds the RST 200 ratings")
    ensure(tl["t_min"] >= 1.3 * dc["t_dis_max"], "time limit could cut a healthy discharge short")
    ensure(tl["v_latched"] < tl["vth_sic"] - 0.5 and tl["i_avail_uA"] > 3 * tl["i_hold_uA"], "discharge latch")
    ensure(dc["e_tstat"][0] > 2 * max(r["E_per_R_J"] for r in dc["rows"]), "thermostat would trip on a normal discharge")
    ensure(TPSI_CVDDP[0] <= C_VDDP * (1 - C_VDDP_TOL) and C_VDDP * (1 + C_VDDP_TOL) <= TPSI_CVDDP[1], "C_VDDP")
    ensure(cd["v_ds_max"] <= 0.8 * LS_VDS and cd["v_gs_max"] <= 20 and cd["v_gs_min"] >= 10 and cd["v_hs_gs"] <= 20
           and cd["v_hs_ds"] <= 0.8 * HS_VDS,
           "coil switch voltages")
    ensure(cd["v_var_drop"] >= K_CLAMP_MIN and cd["t_var"] <= cd["t_ref"], "varistor below TDK's 50 V clamp rule")
    ensure(cd["vs_out"] <= DO_VSOUT_ABS and cd["e_coil"] <= DO_CLAMP_J and cd["e_do_max_mJ"] < 1.0
           and cd["e_coil"] < VAR_WMAX, "SYS DO stage pushed outside its rating")
    ensure(cd["v_hold"] > 2 * K_DROPOUT and cd["v_pick"] > K_PICKUP, "coil voltage")
    ensure(pt["test_hi"] < -5.0 and hold["lo"] > 0, "proof test: a healthy side must trip at 0 A only under TEST")
    ensure(pt["order_ok"], "proof test: the channel-2 one-shot must end before the channel-1 side-B delay")
    ensure(pt["gap_off"] >= 20 * ADC_LSB and pt["gap_on"] >= 10 * ADC_LSB and abs(pt["r_eff"] / 10.0e3 - 1) < 0.002,
           "ID-line readback levels (counts between neighbouring states, code 10.0 k)")
    ensure({r["fault"] for r in pt["rows"] if r["found"] == "NOT FOUND"} <= {"r1 OUTP -> +IN open"},
           "proof test coverage regressed")
    ensure(pn["i_inrush"] <= K_MAKE_CAP[1], "inrush at the precharge criterion after the VA/VAX null")
    b135, b180 = bp["ports"][135], bp["ports"][180]
    ensure(all(not o["pair"]["gap"] and not o["pair"]["marginal"] for o in bp["ports"].values()),
           "battery ports (135 and 180 A) must be coordinated with the contactor pair")
    ensure(b135["single"]["gap"] is not None, "degraded single-contactor case must be flagged (FB both-closed trip)")
    ensure(bp["ports"][135]["derated"] >= 135, "135 A battery fuse at the fuse air temperature")
    ensure(all(all(l_ <= In * (tdf(t + DT_FUSE) if k.startswith("battery") else F_DERATE(t + DT_FUSE)) + 1e-9
                   for t, l_ in zip(bp["inlet"], bp["limit"][k]))
               for k, In in (("pv/135", VARIANTS[135]["In"]), ("pv/180", VARIANTS[180]["In"]),
                             ("battery/135", BATT[135]["In"]), ("battery/180", BATT[180]["In"]))),
           "current limit must stay inside the derated fuse rating at every inlet temperature")
    ensure(min(bp["limit"]["pv/180"]) == 180 and min(bp["limit"]["battery/135"]) == 135
           and bp["limit"]["pv/135"][4] == 135 and bp["limit"]["battery/180"][2] == 180,
           "full current to 45 C inlet on the PV-135 port and to 35 C on the battery-180 port; the rest full range")
    ensure(sg["Up"] <= 4000.0 and sg["uc"] >= a["A_V_TRIP_OS"] and sg["v_first_fault"] <= MOV_VDC,
           "IC-01/IC-12: varistor Y must stay <= 4.0 kV at In and carry the trip overshoot (also after a first earth fault)")
    ensure(SPD_FUSE["icu"] >= E_ICU and SPD_FUSE["lr"] >= E_LR and sg["fuse_ratio"] >= 2.0 and sg["i15_ratio"] >= 2.0,
           "branch fuse: breaking capacity, surge withstand at In; varistor 15-impulse rating vs In")
    ensure(bp["t180"]["pv_fuse"] >= 180 and bp["t180"]["shunt_W"] < SH_P, "180 A PV port at 60 C")
    ensure(max(d["contactor_I2t_kA2s"] for o in bp["ports"].values() for d in o["pair"]["dump"]) * 1e3
           < K_SC_I ** 2 * K_SC_T, "capacitor dump through one contactor of a pair")
    ensure(bp["cable_k2s2"] > 100 * max(BATT[r]["i2t_op"] for r in BATT), "cable / busbar I2t")
    ensure(bp["coil"]["i_low"] < 0.5, "IRFL214 current per device (0.5 A at 100 C)")
    for c in READBACK_R:
        rs = readback_states(c)
        ensure(rs["gap_counts"] >= 19.7 and abs(rs["R_eff_ohm"] / {"PV-PORT": 10.0e3, "DAB60-PORT": 2.49e3}[c] - 1) < 0.005,
               "%s readback states must stay >= 19.7 counts apart (coils on) on the ID code" % c)
    ensure(dv["elem_v"] < 0.5 * TNPV_UMAX and dv["budget"][V_MAX]["rss_cal"] < 0.01, "voltage channel")
    ensure(dv["lag"] <= LAG_MAX and dv["fc_hz"] < 20e6 / 10, "voltage-sensing lag (divider RC + AMC3330 delay)")
    for Ir in VARIANTS:
        ensure(cs[Ir]["i_lin"] > cs[Ir]["sc"] > cs[Ir]["oc"], "current range %d A" % Ir)
        ensure(cs[Ir]["worst_comp"] <= 0.01, "compensated current error above 1 %% at %d A" % Ir)
        ensure(co[Ir]["i_derated"] >= Ir or min(bp["limit"]["pv/%d" % Ir]) < Ir,
               "PV fuse derated below the %d A rating without a published current limit" % Ir)
        ensure(co[Ir]["pv"]["breaks"], "a PV-fed fault must stay inside the contactor's breaking capability")
        ensure(cs[Ir]["oc"] < hold["lo"], "OC trip must lie below the hold band (%d A)" % Ir)
    ensure(hold["hi"] < K_CUTOFF_1000, "hold threshold band reaches the contactor cut-off")
    for Ir in VARIANTS:                    # PV-class ports: a stiff source is not allowed (source="battery" instead)
        ensure(co[Ir]["gap"] is None or co[Ir]["gap"][0] >= hold["lo"] - 2,
               "%d A PV port: below the hold band the contactor must still break every current" % Ir)
    ensure(itf["fb"]["ok"] and all(r["ok"] for r in itf["rows"]) and itf["slew_ok"], "coil / feedback interface")
    ensure(max(im["err"][(V, 33e3, lk)] for V in (V_MIN, V_MAX) for lk in ("typ", "max")) < 0.15, "IMD 33 k")
    ensure(im["err"][(V_MIN, 1e6, "typ")] < 0.25, "IMD 1 M with typical leakage")
    ensure(all(r["margin"] >= 1.0 for r in sg["rows"]), "IC-13: a barrier is below its coordinated requirement")
    ensure(im["pe_open"]["min_healthy_swing_V"] >= 3 * im["lsb_V"] and im["pred_s"] <= 10.0,
           "IMD: an open PE must be distinguishable from any healthy state; prediction within the 10 s branch limit")
    ensure(yc["V_dc_stress"] < 0.7, "Y capacitor DC stress above 70 %")
    print("report -> %s" % os.path.relpath(os.path.join(OUT, "report.md"), REPO))
    print("precharge %.0f ohm; hold band %.0f-%.0f A; 135 A gap %s; 180 A gap %s; I error comp %.2f / %.2f %%" %
          (R_PRE, hold["lo"], hold["hi"], co[135]["gap"], co[180]["gap"], 100 * cs[135]["worst_comp"],
           100 * cs[180]["worst_comp"]))
    print("battery port: 135/180 A coordinated (pair, share %.3f, 200 A gBat); 180 A limit vs inlet %s" %
          (bp["share"], [round(x) for x in bp["limit"]["battery/180"]]))
    print("SELF-CHECK PASSED")


if __name__ == "__main__":
    main()
