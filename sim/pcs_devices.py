"""PCS-P125 (three-phase bidirectional battery inverter) - data of the devices evaluated, with sources, and the
loss/FIT functions that use them.  Companion of sim/pcs_design.py.

Every number below was read from a PDF under docs/ (file + page/figure next to it).  Curve values marked "Fig." were
read off the plotted curve (pdftoppm render, read by eye, about +-5 % of full scale).  Nothing here is measured by us.
SiC MOSFET data are NOT repeated here: they come from sim/pv_devices.py (MOSFETS), already transcribed with page
citations for the PV and DAB work; this file adds the IGBTs, the SiC Schottky clamp diode, the cosmic-ray data, the
prices read for the PCS and the functions for IGBT/diode losses.  Units SI unless a key says otherwise.

IGBT switching-loss model (stated once, used by pcs_design.py):
  E_x(V, I, Tj, R_G) = curve_x(I) * (V / V_ref)**kv_x * (1 + kt_x * (Tj - T_curve)) * rg_x(R_G),  x in {on, off}
  curve_x(I): the datasheet E-vs-I_C figure (inductive load, the stated V_ref / R_G / T_curve); kt_x from the datasheet
  table pair (25 C / hot) at the test current; rg_x from the E-vs-R_G figure; kv_x = 1.35: ASSUMED - none of these
  datasheets has an E-vs-V_CE figure (1.35 is the usual trench-field-stop exponent, the same for every IGBT here, so it
  does not bias the comparison between them; at V_dc/2 = 475 V it scales a 600 V datasheet point by 0.73).
  E_on as measured includes the extra current of the recovering free-wheeling diode (a same-type co-pack diode in the
  makers' test circuits, NCE p3); the diode's OWN recovery energy E_rec is not printed by any of the four makers (their
  Q_rr is at 100-500 A/us and 25 C, irrelevant to hard switching) -> E_rec = ERR_FRAC * E_on(same V, I, Tj): ASSUMED
  0.35 (range 0.2-0.5 run as a sensitivity in pcs_design.py).
Conduction: V_CE(sat)(I, Tj) and V_F(I, Tj) piecewise-linear through the figure points at two temperatures, linear in
Tj between them and beyond.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pv_devices as pv  # noqa: E402  (SiC MOSFET data, _lin, e_sw, rds, eoss)

SEMI = pv.SEMI
ERR_FRAC = 0.35          # IGBT co-pack diode E_rec / IGBT E_on (ASSUMED, see docstring)
KV_IGBT = 1.35           # IGBT E ~ V^1.35 (ASSUMED, see docstring)

# ----------------------------------------------------------------------------------------------- IGBTs
IGBTS = {
    "CRG40T120AK3SD": {   # CR Micro (Wuxi China Resources Huajing), power-semiconductors/CRMICRO-CRG40T120AK3SD.pdf, 2021V03
        "mfr": "CR Micro (CN)", "src": SEMI + "CRMICRO-CRG40T120AK3SD.pdf", "rev": "2021V03", "package": "TO-247",
        "vces": 1200.0, "ic100": 40.0, "icm": 160.0, "if100": 40.0, "ifm": 160.0,            # p2 limits table
        "tj_max": 150.0, "tj_ovl": 175.0,   # p2: Tvjop -40..175 C with note a2 'overload only: 175 C at duty < 20 %, <= 60 s' -> 150 C continuous
        "rth_jc": 0.45, "rth_jc_d": 0.70,   # p2 thermal table (max)
        # Fig.4 p4 (V_GE 15 V, V_CE vs T_C at 20/40/80 A) + knee where Fig.3 leaves the axis (~0.8 / 0.7 V)
        "vce": {25.0: [(0, 0.80), (20, 1.58), (40, 1.98), (80, 2.55)], 150.0: [(0, 0.70), (20, 1.98), (40, 2.62), (80, 3.62)]},
        # Fig.16 p6 diode forward (table p3: 2.27 typ / 2.8 max at 40 A, 25 C; the figure reads 2.45 - figure used, higher)
        "vf": {25.0: [(0, 1.0), (10, 1.75), (20, 2.05), (40, 2.45), (60, 2.70), (80, 2.90)],
               150.0: [(0, 0.55), (10, 1.25), (20, 1.50), (40, 1.95), (60, 2.30), (80, 2.60)]},
        # Fig.13 p6: 600 V, R_G 10 ohm, V_GE 15 V, 25 C
        "sw_v": 600.0, "sw_rg": 10.0, "sw_t": 25.0,
        "eon": [(20, 0.9e-3), (40, 2.7e-3), (60, 5.8e-3), (80, 9.3e-3), (100, 13.6e-3)],
        "eoff": [(20, 0.7e-3), (40, 1.5e-3), (60, 2.5e-3), (80, 3.6e-3), (100, 4.9e-3)],
        "kt_on": (2.86 - 2.8) / 2.8 / 125.0, "kt_off": (2.0 - 1.5) / 1.5 / 125.0,   # p3 table 25 / 150 C at 600 V, 40 A, 10 ohm
        "eon_rg": [(5, 2.3e-3), (10, 2.7e-3), (20, 3.9e-3), (30, 4.7e-3), (40, 5.4e-3), (50, 6.2e-3)],   # Fig.7 p5 (25 C)
        "eoff_rg": [(5, 1.45e-3), (10, 1.5e-3), (20, 2.0e-3), (30, 2.45e-3), (40, 2.85e-3), (50, 3.25e-3)],
        "qg": 208e-9, "vge_th": (5.0, 6.0, 7.0),          # p3
        "check": [("on", 600, 40, 25, 2.8e-3), ("off", 600, 40, 25, 1.5e-3), ("on", 600, 40, 150, 2.86e-3), ("off", 600, 40, 150, 2.0e-3)],
        "t_sc": None,      # MISSING: no short-circuit withstand time in the 9 pages
        "qualification": "none stated; p9 maker note: 'use below 80 % of the maximum ratings'",
        "die_cm2": 0.39,   # ASSUMED (not published): IGBT4-class current density, 5.86 cm2 / 600 A (COSMIC['si_1200'] calibration) x 40 A
        "lcsc": "C2981189", "price": {1: 3.3969, 1000: 1.9865}, "stock": 4820,
    },
    "CRG40T120BK3SD": {   # CR Micro, power-semiconductors/CRMICRO-CRG40T120BK3SD.pdf, 2021V01 (the part named in costfirst_pcs_bom.csv)
        "mfr": "CR Micro (CN)", "src": SEMI + "CRMICRO-CRG40T120BK3SD.pdf", "rev": "2021V01", "package": "TO-247",
        "vces": 1200.0, "ic100": 40.0, "icm": 160.0, "if100": 40.0, "ifm": 160.0,            # p2
        "tj_max": 150.0, "tj_ovl": 150.0,   # p2: T_J max 150 C, no overload clause
        "rth_jc": 0.45, "rth_jc_d": 0.70,   # p2
        "vce": {25.0: [(0, 0.60), (20, 1.55), (40, 1.92), (80, 2.60)], 150.0: [(0, 0.60), (20, 1.70), (40, 2.30), (80, 3.36)]},  # Fig.4 p4, knee Fig.3
        "vf": {25.0: [(0, 1.0), (10, 1.60), (20, 1.85), (30, 1.98), (40, 2.08), (60, 2.22)],                                     # Fig.18 p6
               150.0: [(0, 0.45), (10, 1.05), (20, 1.30), (30, 1.45), (40, 1.58), (60, 1.78)]},
        "sw_v": 600.0, "sw_rg": 10.0, "sw_t": 25.0,                                                                               # Fig.13 p6
        "eon": [(25, 1.6e-3), (30, 2.2e-3), (40, 3.2e-3), (50, 4.8e-3), (60, 7.3e-3)],
        "eoff": [(25, 0.72e-3), (30, 0.95e-3), (40, 1.35e-3), (50, 1.75e-3), (60, 2.3e-3)],
        "kt_on": (3.49 - 3.3) / 3.3 / 125.0, "kt_off": (1.85 - 1.4) / 1.4 / 125.0,     # p3 table 25 / 150 C
        "eon_rg": [(7, 2.8e-3), (10, 3.3e-3), (20, 4.8e-3), (30, 6.0e-3), (40, 7.4e-3), (50, 9.0e-3)],   # Fig.7 p5 (25 C)
        "eoff_rg": [(7, 1.25e-3), (10, 1.4e-3), (20, 1.9e-3), (30, 2.5e-3), (40, 3.0e-3), (50, 3.8e-3)],
        "qg": 239e-9, "vge_th": (4.5, 5.8, 7.0),
        "check": [("on", 600, 40, 25, 3.3e-3), ("off", 600, 40, 25, 1.4e-3), ("on", 600, 40, 150, 3.49e-3), ("off", 600, 40, 150, 1.85e-3)],
        "t_sc": None, "qualification": "none stated; p9 maker note: 'use below 80 % of the maximum ratings'",
        "die_cm2": 0.39, "lcsc": "C2981191", "price": {1: 3.7174, 960: 2.2468}, "stock": 840,
    },
    "NCE40TD120VT": {     # Wuxi NCE Power, power-semiconductors/NCE-NCE40TD120VT.pdf, V6.2 ('Trench FS II', application: 3-level solar inverter)
        "mfr": "Wuxi NCE Power (CN)", "src": SEMI + "NCE-NCE40TD120VT.pdf", "rev": "V6.2", "package": "TO-247",
        "vces": 1200.0, "ic100": 40.0, "icm": 160.0, "if100": 40.0, "ifm": 160.0,            # p1
        "tj_max": 175.0, "tj_ovl": 175.0,   # p1 operating junction -55..+175 C
        "rth_jc": 0.32, "rth_jc_d": 0.61,   # p2
        # Fig.3 p4 (V_GE 15 V, vs T_J at 20/40/120 A) + Fig.12 p5 (25 C, 60/80 A); table p2 1.70 / 1.95 V at 40 A 25 / 175 C (Fig.3 reads 2.10 at 175 C: figure used)
        "vce": {25.0: [(0, 0.75), (20, 1.23), (40, 1.67), (60, 1.88), (80, 2.10), (120, 2.32)],
                175.0: [(0, 0.65), (20, 1.38), (40, 2.10), (80, 2.63), (120, 3.15)]},
        "vf": {25.0: [(0, 1.0), (20, 1.82), (40, 2.28), (80, 2.85)], 175.0: [(0, 0.6), (20, 1.45), (40, 1.98), (80, 2.65)]},   # Fig.8 p5
        # Fig.15 p6: R_G 10 ohm, T_J 175 C (V_CE not printed on the figure; the p2 table conditions are 600 V)
        "sw_v": 600.0, "sw_rg": 10.0, "sw_t": 175.0,
        "eon": [(20, 2.15e-3), (30, 2.35e-3), (40, 2.75e-3), (50, 3.5e-3), (60, 4.3e-3), (70, 5.2e-3), (80, 6.0e-3)],
        "eoff": [(20, 0.85e-3), (30, 1.25e-3), (40, 1.65e-3), (50, 2.05e-3), (60, 2.5e-3), (70, 2.95e-3), (80, 3.4e-3)],
        "kt_on": (2.85 - 2.2) / 2.85 / 150.0, "kt_off": (1.7 - 1.3) / 1.7 / 150.0,      # Fig.9 p5 (40 A, 10 ohm): 25 -> 175 C
        "eon_rg": [(10, 2.2e-3), (20, 2.75e-3), (30, 3.2e-3), (40, 3.55e-3), (50, 3.7e-3)],    # Fig.13 p6 (40 A)
        "eoff_rg": [(10, 1.30e-3), (20, 1.32e-3), (30, 1.30e-3), (40, 1.33e-3), (50, 1.28e-3)],
        "qg": 298e-9, "vge_th": (4.5, None, 6.0),
        "check": [("on", 600, 40, 175, 2.8e-3), ("off", 600, 40, 175, 1.6e-3), ("on", 600, 40, 25, 2.2e-3), ("off", 600, 40, 25, 1.3e-3)],
        "t_sc": None, "qualification": "none stated (p8 general reliability disclaimer only)",
        "die_cm2": 0.39, "lcsc": "C5124157", "price": {1: 3.7207, 1200: 2.058}, "stock": 368,
        "vces_cold": 0.95,   # Fig.17 p6: V_CES normalised 0.95 at -30 C (0.93 at -40 C) - the blocking margin shrinks when cold
    },
    "CRG50T60AK3SD": {    # CR Micro, power-semiconductors/CRMICRO-CRG50T60AK3SD.pdf, 2022V02 (650 V; the nearest documented part to the
                          # CRG40T65AK5HD of costfirst_pcs_bom.csv, which is not listed on the LCSC mirror)
        "mfr": "CR Micro (CN)", "src": SEMI + "CRMICRO-CRG50T60AK3SD.pdf", "rev": "2022V02", "package": "TO-247",
        "vces": 650.0, "ic100": 50.0, "icm": 150.0, "if100": 50.0, "ifm": 150.0,              # p2
        "tj_max": 150.0, "tj_ovl": 175.0,   # p2 note a2 as the 1200 V part (LCSC lists -40..+150 C)
        "rth_jc": 0.30, "rth_jc_d": 0.42,   # p2
        "vce": {25.0: [(0, 0.70), (25, 1.38), (50, 1.75), (75, 2.10)], 125.0: [(0, 0.60), (25, 1.60), (50, 2.20), (75, 2.75)]},   # Fig.4 p4
        "vf": {25.0: [(0, 0.8), (10, 1.2), (20, 1.45), (30, 1.6), (40, 1.7), (50, 1.75), (60, 1.8)],                              # Fig.15 p6
               125.0: [(0, 0.5), (10, 0.95), (20, 1.15), (30, 1.3), (40, 1.4), (50, 1.5), (60, 1.58)]},
        # Fig.12 p5 (400 V, 25 C) as read: E_on 1.5/2.4/3.6/4.8/6.0, E_off 0.68/1.0/1.44/1.8/2.2 mJ at 30..70 A.  The figure and the p3
        # table disagree at 50 A (3.6 vs 3.18, 1.44 vs 1.54 mJ): the curve SHAPE is kept and scaled to the table (x0.883 / x1.07)
        "sw_v": 400.0, "sw_rg": 10.0, "sw_t": 25.0,
        "eon": [(i, e * 3.18 / 3.6) for i, e in ((30, 1.5e-3), (40, 2.4e-3), (50, 3.6e-3), (60, 4.8e-3), (70, 6.0e-3))],
        "eoff": [(i, e * 1.54 / 1.44) for i, e in ((30, 0.68e-3), (40, 1.0e-3), (50, 1.44e-3), (60, 1.8e-3), (70, 2.2e-3))],
        "kt_on": (3.21 - 3.18) / 3.18 / 125.0, "kt_off": (1.62 - 1.54) / 1.54 / 125.0,   # p3 table 25 / 150 C (400 V, 50 A, 10 ohm)
        "eon_rg": [(5, 2.7e-3), (10, 3.6e-3), (20, 4.4e-3), (30, 5.2e-3), (40, 6.1e-3), (50, 6.9e-3)],   # Fig.6 p4 (400 V, 50 A)
        "eoff_rg": [(5, 1.0e-3), (10, 1.35e-3), (20, 1.85e-3), (30, 2.4e-3), (40, 3.0e-3), (50, 3.7e-3)],
        "qg": 303e-9, "vge_th": (5.3, 6.1, 7.0),
        "check": [("on", 400, 50, 25, 3.18e-3), ("off", 400, 50, 25, 1.54e-3), ("on", 400, 50, 150, 3.21e-3), ("off", 400, 50, 150, 1.62e-3)],
        "t_sc": 10e-6, "t_sc_cond": "V_GE 15 V, V_CE 400 V, T_C 25 C (p2)",
        "qualification": "none stated",
        "die_cm2": 0.30,   # ASSUMED (650 V die area not published)
        "lcsc": "C22363475", "price": {1: 1.694, 1000: 1.0707}, "stock": 929,
    },
    "NCE80TD65BT": {      # Wuxi NCE Power, power-semiconductors/NCE-NCE80TD65BT.pdf, V1.0 (650 V 80 A, fast: the best documented 650 V IGBT found)
        "mfr": "Wuxi NCE Power (CN)", "src": SEMI + "NCE-NCE80TD65BT.pdf", "rev": "V1.0", "package": "TO-247",
        "vces": 650.0, "ic100": 80.0, "icm": 240.0, "if100": 80.0, "ifm": 240.0,             # p1
        "tj_max": 150.0, "tj_ovl": 150.0,   # p1 -55..+150 C
        "rth_jc": 0.32, "rth_jc_d": 1.41,   # p2 - the diode's 1.41 K/W is 4.4x the IGBT's: the diode chip limits the inner T-type position
        "vce": {25.0: [(0, 0.70), (40, 1.38), (80, 1.90), (160, 2.85)], 150.0: [(0, 0.60), (40, 1.75), (80, 2.50), (160, 4.30)]},  # Fig.3 p4
        # Fig.7 p5 (the p2 table, 1.75 V at 80 A 25 C, lies between Fig.7 and Fig.8, which disagree; Fig.7 is the higher, used)
        "vf": {25.0: [(0, 0.65), (10, 1.25), (20, 1.5), (45, 1.75), (80, 2.0), (120, 2.2)],
               150.0: [(0, 0.40), (10, 1.0), (30, 1.5), (60, 1.75), (95, 2.0), (120, 2.1)]},
        # p2 table: E_on 1.43 / E_off 1.45 mJ at 400 V, 80 A, R_G 5 ohm, 0/15 V, 25 C - the ONLY switching data (no E-vs-I, no E-vs-T figure):
        # E proportional to I through the table point = ASSUMED; temperature coefficients ASSUMED = the same maker's NCE40TD120VT (Fig.9)
        "sw_v": 400.0, "sw_rg": 5.0, "sw_t": 25.0,
        "eon": [(0, 0.0), (80, 1.43e-3), (160, 2.86e-3)], "eoff": [(0, 0.0), (80, 1.45e-3), (160, 2.90e-3)],
        "kt_on": (2.85 - 2.2) / 2.2 / 150.0, "kt_off": (1.7 - 1.3) / 1.3 / 150.0,
        "eon_rg": [(5, 1.5e-3), (10, 2.4e-3), (20, 4.2e-3), (30, 5.2e-3), (40, 6.3e-3), (47, 7.7e-3)],   # Fig.9 p5 (80 A)
        "eoff_rg": [(5, 1.45e-3), (10, 1.8e-3), (20, 2.5e-3), (30, 3.2e-3), (40, 3.9e-3), (47, 4.5e-3)],
        "qg": 331e-9, "vge_th": (4.0, 5.0, 6.0),
        "check": [("on", 400, 80, 25, 1.43e-3), ("off", 400, 80, 25, 1.45e-3)],
        "t_sc": 3e-6, "t_sc_cond": "V_GE 15 V, V_CC <= 400 V, Tj <= 150 C, < 1000 short circuits >= 1 s apart (p1)",
        "qualification": "none stated",
        "die_cm2": 0.40,   # ASSUMED
        "lcsc": "C502948", "price": {1: 2.8601, 990: 1.5814}, "stock": 358,
    },
}
TJ_ASSUMED_NOTE = "NCE80TD65BT E(I) proportional and kt borrowed from NCE40TD120VT (no figure) - ASSUMED"
# ----------------------------------------------------------------------------------------------- three-level IGBT modules (owner's input)
# One module per phase (I-type NPC: T1..T4 IGBTs, D1..D4 anti-parallel and D5/D6 clamp diodes inside).  Thermal resistances are
# junction-to-HEATSINK (grease 3.4 W/mK included by the maker) - no pad.  Curves: the p9-13 figures are 70-dpi renders, read coarsely;
# the table points (p1, p4-6) are exact.  Prices: NO Chinese 3-level module price is public (HIITIO, StarPower, Macmic searched;
# StarPower GD200TLQ120L3S is listed by RS, pages blocked) - INDICATIVE figures from Western equivalents: Infineon Easy 3B 1200 V 315 A
# EUR 165.85 at RS Germany (search snippet 2026-10-05, https://de.rs-online.com/web/p/igbt/0351899) and Semikron SEMiX 5 3-level TNPC
# 1200 V 400 A 798.75 USD @1 / 249.61 USD @12 (shop.semikron-danfoss.com, search snippet 2026-10-05); a Chinese pin-compatible module is
# ASSUMED at 0.7 x the Western 1-piece price (catalogue) and 0.45 x (5,000 units).
MODULES = {
    "HCG400FL120E3RA": {   # HIITIO (CN), power-semiconductors/HIITIO-HCG400FL120E3RA.pdf, Rev.B
        "mfr": "Zhejiang HIITIO New Energy (CN)", "src": SEMI + "HIITIO-HCG400FL120E3RA.pdf", "rev": "Rev.B", "package": "Easy 3B (Cu base, Al2O3)",
        "module": True, "circuit": "I-type 3-level NPC; all IGBTs and diodes 1200 V", "western_class": "Infineon Easy 3B 3-level NPC class (not named by HIITIO)",
        "vces": 1200.0, "ic100": 298.0, "icm": 800.0, "if100": 152.0, "ifm": 800.0,      # p4 (I_CDC at T_h 80 C), p5/p6 diodes I_F 152 A
        "tj_max": 150.0, "tj_ovl": 150.0,      # p3 Tvj(op) -40..150 C under switching (Tvj max 175 C, p4)
        "rth_jc": 0.19, "rth_jc_d": 0.26,      # p4-p6 R_thJH per IGBT / per diode, grease 3.4 W/mK (junction to HEATSINK)
        "visol_V": 3000.0, "ls_nH": 25.0, "creepage_mm": 12.7,     # p3
        # p1/p4 table: V_CE(sat) 1.64 / 2.05 V at 400 A (25 / 125 C); p9 Fig. output characteristic (coarse) for the shape
        "vce": {25.0: [(0, 0.75), (100, 1.05), (200, 1.25), (400, 1.64), (600, 1.95), (800, 2.25)],
                125.0: [(0, 0.65), (100, 1.15), (200, 1.45), (400, 2.05), (600, 2.55), (800, 3.0)]},
        # p5/p6 diode V_F 2.15 / 3.00 V at 400 A (25 / 125 C) - both diode types; shape ASSUMED linear from a 0.9 / 0.8 V knee
        "vf": {25.0: [(0, 0.9), (400, 2.15), (800, 3.4)], 125.0: [(0, 0.8), (400, 3.00), (800, 5.2)]},
        # p4 table: E_on 11.41 / 14.46, E_off 20.34 / 23.42 mJ at 600 V, 400 A, R_Gon 5 / R_Goff 1.5 ohm, +15/-8 V, Ls 30 nH (25 / 125 C);
        # p9 Fig. switching losses vs I_C (125 C curves, coarse read)
        "sw_v": 600.0, "sw_rg": None, "sw_t": 125.0,
        "eon": [(100, 4.5e-3), (200, 8.5e-3), (400, 14.46e-3), (600, 19.0e-3), (800, 22.0e-3)],
        "eoff": [(100, 7.0e-3), (200, 13.0e-3), (400, 23.42e-3), (600, 37.0e-3), (800, 51.0e-3)],
        "kt_on": (14.46 - 11.41) / 14.46 / 100.0, "kt_off": (23.42 - 20.34) / 23.42 / 100.0,
        "erec": [(0, 0.0), (400, 1.25e-3), (800, 2.0e-3)], "kt_rec": (1.25 - 1.01) / 1.25 / 100.0,   # p6 D1/D4 E_rec 1.01 / 1.25 mJ at 400 A (shape ASSUMED)
        "check": [("on", 600, 400, 125, 14.46e-3), ("off", 600, 400, 125, 23.42e-3), ("on", 600, 400, 25, 11.41e-3)],
        "qg": 5.5e-6, "t_sc": None, "qualification": "none stated (17 pages: no short-circuit rating, no reliability statement)",
        "die_cm2": 3.9,        # ASSUMED per switch (IGBT4-class current density x 400 A)
        "price": {1: 180.0, 1000: 110.0}, "price_note": "INDICATIVE, no public price: 0.7 / 0.45 x the Western Easy 3B / SEMiX 5 references (see block note)",
    },
    "HCG375FL065E3RC": {   # HIITIO (CN), power-semiconductors/HIITIO-HCG375FL065E3RC.pdf, Rev.B
        "mfr": "Zhejiang HIITIO New Energy (CN)", "src": SEMI + "HIITIO-HCG375FL065E3RC.pdf", "rev": "Rev.B", "package": "Easy 3B (Al2O3 DBC)",
        "module": True, "circuit": "I-type 3-level NPC; all switches and diodes 650 V", "western_class": "Infineon F3L400R07W3S5_B59 class (650 V 400 A 3-level Easy 3B; not named by HIITIO)",
        "vces": 650.0, "ic100": 285.0, "icm": 750.0, "if100": 290.0, "ifm": 750.0,        # p1/p4/p5
        "tj_max": 150.0, "tj_ovl": 150.0, "rth_jc": 0.25, "rth_jc_d": 0.27,             # p3 Tvj(op); p1 R_thJH IGBT / diode
        "visol_V": 3000.0, "ls_nH": 20.0,
        # p4: V_CE(sat) 1.46 / 1.65 V at 375 A (25 / 125 C); shape ASSUMED (knee 0.7 / 0.6 V); p5 V_F 1.45 / 1.53 V at 375 A
        "vce": {25.0: [(0, 0.70), (375, 1.46), (750, 2.10)], 125.0: [(0, 0.60), (375, 1.65), (750, 2.55)]},
        "vf": {25.0: [(0, 0.75), (375, 1.45), (750, 2.0)], 125.0: [(0, 0.60), (375, 1.53), (750, 2.2)]},
        # p4: E_on 4.45 / 6.55, E_off 5.48 / 6.92 mJ at V_CE 400 V, I_C 180 A, R_Gon 7.5 / R_Goff 10 ohm, +15/-8 V, Ls 50 nH (25 / 125 C);
        # E proportional to I through the table point = ASSUMED (figures not read)
        "sw_v": 400.0, "sw_rg": None, "sw_t": 125.0,
        "eon": [(0, 0.0), (180, 6.55e-3), (360, 13.1e-3), (720, 26.2e-3)], "eoff": [(0, 0.0), (180, 6.92e-3), (360, 13.84e-3), (720, 27.7e-3)],
        "kt_on": (6.55 - 4.45) / 6.55 / 100.0, "kt_off": (6.92 - 5.48) / 6.92 / 100.0,
        "erec": [(0, 0.0), (180, 2.08e-3), (720, 8.3e-3)], "kt_rec": (2.08 - 1.2) / 2.08 / 100.0,    # p5 E_rec 1.2 / 2.08 mJ (test current as the IGBT row)
        "check": [("on", 400, 180, 125, 6.55e-3), ("off", 400, 180, 125, 6.92e-3)],
        "qg": 0.975e-6, "t_sc": None, "qualification": "none stated (16 pages)",
        "die_cm2": 2.5, "price": {1: 150.0, 1000: 90.0}, "price_note": "INDICATIVE, no public price (as above)",
    },
}
# filed, screened out (not modelled):
SCREENED_OUT = {
    "CRG40T120AK3S": "CR Micro 1200 V 40 A without full diode (power-semiconductors/CRMICRO-CRG40T120AK3S.pdf, filed by the architect): co-pack diode "
                     "20 A - not usable where the diode carries the phase current (every position of a bidirectional PCS).",
    "CRGMF100T120FSA3": "CR Micro 1200 V 100 A bolt-mount module, LCSC C3018875: 15.60 USD @108 = 0.156 USD per ampere against 0.050 USD/A for "
                        "the CRG40T120AK3SD discrete (1.99 USD @1000 / 40 A); Tj 125 C. No Asian three-level (T-type / NPC) module has a public "
                        "price on LCSC (StarPower GD..., Macmic MMG... searched by part number: not listed) - modules are not cheaper per ampere on "
                        "the evidence available (RFQ)."
}

# ----------------------------------------------------------------------------------------------- SiC Schottky clamp diode
SBD = {
    "YJD112040NQG2": {   # Yangjie (CN), power-semiconductors/YJD112040NQG2.pdf, Rev.1.0 2022-10-25
        "mfr": "Yangjie (CN)", "src": SEMI + "YJD112040NQG2.pdf", "vrrm": 1200.0, "if_135": 51.0, "ifsm": 280.0,   # p1
        "vf_40": {25.0: 1.41, 175.0: 2.02}, "v0": {25.0: 0.90, 175.0: 0.80},   # p2 V_F typ at 40 A; knee ASSUMED (SiC SBD 0.8-0.9 V)
        "qc": 216e-9, "ec_800": 55e-6,      # p2 Q_C, E_C at 800 V (capacitive, no recovery)
        "rth_jc": 0.34, "tj_max": 175.0,    # p2 / p1
        "qualification": "none stated (Rev.1.0); Yangjie is an established listed CN rectifier maker",
        "lcsc": "C20605605", "price": {1: 5.82, 990: 3.626}, "stock": 220,   # gen/data/prices.csv
    },
}

# ----------------------------------------------------------------------------------------------- SiC MOSFETs (from pv_devices)
SIC = {k: pv.MOSFETS[k] for k in ("SG2M035120LJ", "SCT4036KRHR", "SG2M020170HJ", "SG2M040170HJ")}
SIC_PRICE = {   # gen/data/prices.csv (LCSC via the jlcsearch mirror, 2026-10-04); SG2M040170HJ has no public price
    "SG2M035120LJ": {"lcsc": "C52109938", "price": {1: 3.4994, 900: 2.2624}, "stock": 31},
    "SCT4036KRHR": {"lcsc": "C7309657", "price": {1: 5.3074, 990: 3.2504}, "stock": 525},
    "SG2M020170HJ": {"lcsc": "C42456099", "price": {1: 12.10, 90: 9.74}, "stock": 314},
    "SG2M040170HJ": {"lcsc": None, "price": {1: 4.07, 1000: 3.46}, "stock": 0,
                     "note": "ESTIMATE (RFQ): the PV design's figure (costfirst_bom.csv), SG2M020170HJ scaled by R^1.26"},
}
# die area per device, ASSUMED (no maker publishes it): 1200 V SiC ~3.5-4 mOhm.cm2 at 25 C -> 33-36 mOhm ~0.11-0.12 cm2;
# 1700 V ~6 mOhm.cm2 -> 20 mOhm 0.30 cm2, 40 mOhm 0.15 cm2.  Used only for the cosmic-ray FIT.
SIC_DIE_CM2 = {"SG2M035120LJ": 0.12, "SCT4036KRHR": 0.12, "SG2M020170HJ": 0.30, "SG2M040170HJ": 0.15}

# ----------------------------------------------------------------------------------------------- cosmic ray
# Semikron Danfoss AN 17-003 rev 01 (power-semiconductors/SemikronDanfoss-AN17-003-Cosmic-Ray-Failures.pdf):
#  Fig.3 p4: FIT/cm2 at 25 C, sea level, IGBT 12E4 (1200 V): 900 V ~14, 950 V ~35, 1050 V ~800, 1150 V ~3e4; an upper-bound
#   bar at 800 V (~0.4); CAL4F 1200 V diode: 1200 V 0.015, 1260 V 0.5, 1320 V ~1200 (six to seven decades below the IGBT).
#  Table 2 p8: 24 switches (12 x SEMiX603GB12E4p, 600 A) at 50 % duty: 1000 V 11520, 950 V 3360, 900 V 984, 850 V 175, <=800 V 31 FIT
#   -> per switch 480 / 140 / 41 / 7.3 / 1.29 FIT; the UPS example p7 confirms 41 FIT per switch at 900 V.
#   Calibration: 41 FIT = 0.5 x A x 14 FIT/cm2 -> A = 5.86 cm2 per 600 A switch; Table 2 then gives 47 FIT/cm2 at 950 V (Fig.3 reads ~35:
#   the higher is used), 163 at 1000 V, 2.5 at 850 V, 0.44 at 800 V.
#  p6: interpolate log-linearly between points, no extrapolation; temperature factor F(T) = exp(-(Tj - 25)/47.6) (ABB 5SYA 2042,
#   quoted, 'limited accuracy'); altitude A(h) = 2^(h/1000 m) (< 10 % error to 3000 m).
#  p9 section 4.1: 3L NPC with 650 V chips at <= 500 V and 1200 V chips at <= 750 V: 'below 1 FIT/cm2 ... considered immune';
#   SKiiP39MLI07E3V1 below the ~1 FIT measurement limit per switch at 500 V.
#  p10 section 4.2: TNPC vertical (outer) switches block the full DC voltage ~25 % (+-5 %) of the time; horizontal leg as NPC.
# Mitsubishi CMH-10370 (power-semiconductors/Mitsubishi-IGBT-LTDS-note.pdf): qualitative only - failure rate rises with V_CE and
#  altitude, falls with temperature; numbers on request.  No Chinese maker (CR Micro, NCE, Sichain, Yangjie) publishes any.
# Wolfspeed Gen3 1200 V SiC (FIT/cm2 vs V): pv_devices.COSMIC_GEN3_1200 (application brief on file); scaled by V_DSS for other
#  voltage classes and makers - the PV design's assumption.
COSMIC = {
    "si_1200": {"src": "AN 17-003 Fig.3 p4 + Table 2 p8 (calibrated 5.86 cm2/600 A switch)",
                "v_fit_cm2": [(800, 0.44), (850, 2.5), (900, 14.0), (950, 47.0), (1000, 163.0), (1050, 800.0), (1150, 3.0e4)],
                "cm2_per_A": 5.86 / 600.0},
    "si_1200_switch_table": {"src": "AN 17-003 Table 2 p8 / 24 switches (SEMiX603GB12E4p, 50 % duty, 25 C, sea level)",
                             "v_fit": [(800, 31 / 24), (850, 175 / 24), (900, 984 / 24), (950, 3360 / 24), (1000, 11520 / 24)]},
    "si_650_bound_500V": 1.0,   # FIT/cm2, AN 17-003 p9 (upper bound at <= 500 V)
    "sic_gen3_1200": pv.COSMIC_GEN3_1200,
    "rule_vdc_frac": pv.V_DC_FRAC,   # 0.67 x rating continuous (project rule, D-007)
}


def _loglin(pts, v):
    """Semikron's log-linear interpolation; below the first point the first value is returned as an UPPER BOUND (no extrapolation)"""
    vs = np.array([p[0] for p in pts], float)
    fs = np.log(np.array([p[1] for p in pts], float))
    v = np.asarray(v, float)
    return np.exp(np.interp(np.clip(v, vs[0], vs[-1]), vs, fs))


def fit_per_cm2(tech, v, vrated=1200.0):
    """FIT per cm2 at 25 C, sea level, 100 % of the time blocking v.  tech: 'si_1200', 'si_650', 'sic'"""
    if tech == "si_1200":
        return _loglin(COSMIC["si_1200"]["v_fit_cm2"], v)
    if tech == "si_650":
        if np.max(v) > 500.0 + 1e-9:
            raise ValueError("no 650 V cosmic-ray data above 500 V on file")
        return np.full_like(np.asarray(v, float), COSMIC["si_650_bound_500V"])
    if tech == "sic":
        return _loglin(COSMIC["sic_gen3_1200"]["v_fit_cm2"], np.asarray(v, float) * 1200.0 / vrated)
    raise ValueError(tech)


def temp_factor(tj):
    return np.exp(-(np.asarray(tj, float) - 25.0) / 47.6)


def alt_factor(h_m):
    return 2.0 ** (h_m / 1000.0)


# ----------------------------------------------------------------------------------------------- functions
def _curve_t(curves, i, tj):
    """value of a two-temperature figure family at current i and temperature tj (linear in T between/beyond the two)"""
    (t1, c1), (t2, c2) = sorted(curves.items())
    v1, v2 = pv._lin(c1, i), pv._lin(c2, i)
    return v1 + (v2 - v1) * (np.asarray(tj, float) - t1) / (t2 - t1)


def vce(d, i, tj):
    return _curve_t(d["vce"], np.abs(i), tj)


def vf(d, i, tj):
    return _curve_t(d["vf"], np.abs(i), tj)


def e_igbt(d, kind, v, i, tj, rg=None):
    """IGBT event energy, kind 'on' / 'off' / 'rec' (co-pack diode recovery = ERR_FRAC x E_on); i = commutated current"""
    if kind == "rec":
        if "erec" in d:          # the maker's E_rec (modules), linear in V (Q_rr x V) and in T by kt_rec
            return (pv._lin(d["erec"], np.abs(np.asarray(i, float))) * np.abs(np.asarray(v, float)) / d["sw_v"]
                    * np.maximum(1.0 + d.get("kt_rec", 0.0) * (np.asarray(tj, float) - d["sw_t"]), 0.2))
        return ERR_FRAC * e_igbt(d, "on", v, i, tj, rg)
    i = np.abs(np.asarray(i, float))
    pts = d["e" + kind]
    # below the first figure point: proportional to I through the origin (a straight-line extrapolation of these curves would
    # reach zero at 10-15 A for some parts and stay at 80 % of the 20 A value for others; proportional treats every part alike)
    e = np.where(i < pts[0][0], pts[0][1] * i / max(pts[0][0], 1e-9), pv._lin(pts, i))
    e = e * (np.abs(np.asarray(v, float)) / d["sw_v"]) ** KV_IGBT
    e = e * np.maximum(1.0 + d["kt_" + kind] * (np.asarray(tj, float) - d["sw_t"]), 0.2)
    if rg is not None and d.get("sw_rg"):
        pts = d["e" + kind + "_rg"]
        e = e * float(pv._lin(pts, rg) / pv._lin(pts, d["sw_rg"]))
    return e


def vf_sbd(s, i, tj):
    """SiC SBD forward drop: knee V0(T) + r(T) I, both linear in T between the 25 / 175 C table points"""
    tj = np.asarray(tj, float)
    v0 = s["v0"][25.0] + (s["v0"][175.0] - s["v0"][25.0]) * (tj - 25.0) / 150.0
    r25, r175 = (s["vf_40"][25.0] - s["v0"][25.0]) / 40.0, (s["vf_40"][175.0] - s["v0"][175.0]) / 40.0
    r = r25 + (r175 - r25) * (tj - 25.0) / 150.0
    return v0 + r * np.abs(i)


def price(entry, qty):
    """price per piece at the largest catalogue break <= qty (entry = dict with 'price': {break: USD})"""
    br = sorted(entry["price"])
    return entry["price"][max([b for b in br if b <= qty] or [br[0]])]


def part_price(name, qty):
    for tab in (IGBTS, SBD, SIC_PRICE, MODULES):
        if name in tab:
            return price(tab[name], qty)
    raise KeyError(name)


def self_check():
    for name, d in dict(IGBTS, **MODULES).items():
        for kind, v, i, t, ref in d["check"]:
            got = float(e_igbt(d, kind, v, i, t))
            assert abs(got / ref - 1) < 0.12, (name, kind, t, got, ref)
        assert float(vce(d, d["ic100"], 25)) < float(vce(d, d["ic100"], 125)), name       # positive TC at rated current (paralleling)
        assert 1.0 < float(vce(d, d["ic100"], 25)) < 2.5, name
        if not d.get("module"):
            assert float(vf(d, d["if100"], 125)) < float(vf(d, d["if100"], 25)), name      # discrete Si FRD: negative TC at rated current
    s = SBD["YJD112040NQG2"]
    assert abs(float(vf_sbd(s, 40, 25)) - 1.41) < 1e-9 and abs(float(vf_sbd(s, 40, 175)) - 2.02) < 1e-9
    # cosmic: table reproduction and the rule points
    for v, f in COSMIC["si_1200_switch_table"]["v_fit"]:
        cm2 = float(fit_per_cm2("si_1200", v)) * 0.5 * COSMIC["si_1200"]["cm2_per_A"] * 600.0
        assert abs(cm2 / f - 1) < 0.15 or v <= 800, (v, cm2, f)
    assert fit_per_cm2("si_1200", 950) / fit_per_cm2("si_1200", 0.67 * 1200) > 50      # 0.79 vs 0.67 of V_CES: > 50x
    assert fit_per_cm2("sic", 950, 1700) < 0.2                                         # 1700 V SiC at 950 V (scaled): < 0.2 FIT/cm2
    assert abs(float(temp_factor(25)) - 1) < 1e-12 and abs(alt_factor(1000) - 2) < 1e-12
    assert part_price("CRG40T120AK3SD", 5000) == 1.9865 and part_price("SG2M035120LJ", 60) == 3.4994
    return True


if __name__ == "__main__":
    self_check()
    for n, d in IGBTS.items():
        print(f"{n:15s} {d['vces']:.0f} V  Vce(40/{d['ic100']:.0f} A, 125 C) {float(vce(d, d['ic100'], 125)):.2f} V  "
              f"Eon+Eoff+Erec(475 V, {d['ic100']:.0f} A, 125 C) {float(sum(e_igbt(d, k, 475, d['ic100'], 125) for k in ('on', 'off', 'rec')))*1e3:.2f} mJ  "
              f"tsc {d['t_sc']}  {d['price']}")
    for v in (804, 900, 950, 1000):
        print(f"Si 1200 V IGBT at {v} V: {float(fit_per_cm2('si_1200', v)):7.2f} FIT/cm2 | SiC 1700 V: {float(fit_per_cm2('sic', v, 1700)):.3f} | "
              f"SiC 1200 V: {float(fit_per_cm2('sic', v, 1200)):.1f}")
    print("pcs_devices self-check passed")
