"""PVCELL device / magnetics / capacitor data with sources, plus the loss-model functions that use them.

Every number below was read from a PDF under docs/datasheets/ (file + page/figure noted next to it).
Curve values marked "Fig." were read off the plotted curve (pdftoppm render, read by eye, about +-5 % of full scale).
Nothing here is measured by us. Units are SI (V, A, ohm, J, F, H, K/W, s, m, kg) unless a key says otherwise.

Switching-loss model (stated once, used by pv_tradeoff.py and pv_design.py):
  E_x(V, I, Tj) = curve_x(I) * (V / V_ref)**kv_x * (1 + kt_x * (Tj - T_ref)),  x in {on, off, rr}
  curve_x(I) is the datasheet clamped-inductive (double-pulse) curve at (V_ref, T_ref, R_G of the datasheet),
  kv_x is fitted from the two datasheet voltages where a vendor gives two (else an assumption, flagged),
  kt_x from the datasheet energy-vs-temperature figure / table pair.
  E_rr (energy in the recovering body diode of the complementary device) is the vendor value where given
  (Infineon E_fr, Wolfspeed module E_RR); otherwise E_rr = K_RR * Q_rr(I, Tj) * V with K_RR from the vendors that
  publish both (see K_RR below).
  Per hard-switched commutation the dissipated energy is E_on + E_off + E_rr - E_oss: the double-pulse E_off contains
  the energy put into the switching device's own C_oss (dissipated later at its hard turn-on, where the DPT E_on does
  not see it - counted once), and the E_rr/E_fr measurement contains the charge stored in the diode device's C_oss,
  which is returned at the next commutation, so E_oss is subtracted once. Zero-voltage turn-on events cost nothing
  beyond the dead-time diode conduction.
"""
import numpy as np

SEMI = "docs/datasheets/power-semiconductors/"
MAG = "docs/datasheets/magnetics/"
CAP = "docs/datasheets/passives-capacitors/"

# energy in the recovering diode per coulomb of Q_rr per volt, when only Q_rr is published.
# Calibration: Infineon IMYH200R012M1H E_fr/(Q_fr*V) = 1835e-6/(3950e-9*1200) = 0.39 (p6);
# IMYH200R024M1H 1100e-6/(1975e-9*1200) = 0.46 (p6); Wolfspeed CAB011M12FM3 E_RR/(Q_RR*V) = 0.64e-3/(1.85e-6*600)
# = 0.58 (p3).  Mid value used, +-0.1 is the uncertainty.
K_RR = 0.45
# temperature shape of Q_rr when a vendor gives it at one Tj only: s(T) = 0.5 + 0.5*(T-25)/150 (x2 from 25 to 175 C),
# taken from Infineon E_fr 920 -> 1835 uJ for 25 -> 175 C (IMYH200R012M1H p6).  Current shape Q_rr ~ sqrt(I): ASSUMED.
def _qrr_shape(t):
    return 0.5 + 0.5 * (t - 25.0) / 150.0


VSD0 = 2.7  # V, assumed SiC body-diode knee at 100-150 C (used with one datasheet V_SD point to make V_SD(I) linear)

MOSFETS = {
    "C3M0016120K": {
        "vth": 2.5, "gfs": 54.0, "gfs_i": 80.0, "qgd": 65e-9, "qgd_v": 800.0,   # p2
        "mfr": "Wolfspeed", "src": SEMI + "C3M0016120K.pdf", "rev": "Rev. 5, Nov 2025",
        "vdss": 1200, "package": "TO-247-4", "isolated_tab": False, "gen": "Gen3 C3M",
        "vgs_on": 15.0, "vgs_off": -4.0, "rg_ext": 2.5, "rg_int": 2.6,          # p1 note 1, p2 (test R_G)
        "rds25": 16e-3, "rds25_max": 22e-3,                                     # p2, VGS 15 V, 80 A
        "rds_norm": [(25, 1.00), (50, 1.06), (75, 1.15), (100, 1.27), (125, 1.42), (150, 1.60), (175, 1.80)],  # Fig.4 p4
        "rth_jc": 0.23,                                                         # p3
        "qg": 223e-9, "ciss": 6922e-12, "coss": 231e-12, "crss": 13e-12, "c_at": 1000.0,  # p2
        "eoss": [(0, 0), (200, 12e-6), (400, 30e-6), (600, 55e-6), (800, 87e-6), (1000, 127e-6), (1200, 177e-6)],  # Fig.16 p6
        "sw_v": 800.0, "sw_t": 25.0,                                            # Fig.24 p7: 800 V, 25 C, RG 2.5, FWD body diode
        "eon": [(20, 0.47e-3), (40, 0.82e-3), (60, 1.22e-3), (80, 1.65e-3), (100, 2.20e-3), (120, 2.72e-3), (150, 3.75e-3)],
        "eoff": [(20, 0.12e-3), (40, 0.24e-3), (60, 0.42e-3), (80, 0.65e-3), (100, 0.96e-3), (120, 1.30e-3), (150, 2.00e-3)],
        "kv_on": 1.48, "kv_off": 1.30,        # Fig.23 (600 V) vs Fig.24 (800 V) at 50 A and 100 A
        "kt_on": 0.00357, "kt_off": 0.00118,  # Fig.26 p8, 80 A: Eon 1.68 -> 2.58 mJ, Eoff 0.68 -> 0.80 mJ (25 -> 175 C)
        "qrr": 1665e-9, "qrr_i": 80.0, "qrr_t": 175.0,                          # p3, 800 V, 5180 A/us
        "vsd": 4.4, "vsd_i": 40.0,                                              # p3, VGS -4 V, 175 C
        "tf": 13e-9, "tf_i": 80.0,                                              # p2 fall time, 800 V 80 A
        "eon_rg": [(2.5, 1.70e-3), (5.0, 2.0e-3), (10.0, 2.65e-3), (15.0, 3.3e-3), (20.0, 4.0e-3)],   # Fig.25 p8 (800 V, 80 A, 25 C)
        "check": [("on", 800, 80, 175, 2552e-6), ("off", 800, 80, 175, 788e-6)],  # p2 table
    },
    "C3M0021120K": {
        "vth": 2.5, "gfs": 35.0, "gfs_i": 50.0, "qgd": 50e-9, "qgd_v": 800.0,   # p2
        "mfr": "Wolfspeed", "src": SEMI + "C3M0021120K.pdf", "rev": "Rev. 4, Jun 2025",
        "vdss": 1200, "package": "TO-247-4", "isolated_tab": False, "gen": "Gen3 C3M",
        "vgs_on": 15.0, "vgs_off": -4.0, "rg_ext": 2.5, "rg_int": 3.3,          # p2
        "rds25": 21e-3, "rds25_max": 28.8e-3,                                   # p2, VGS 15 V, 50 A
        "rds_norm": [(25, 1.00), (50, 1.05), (75, 1.13), (100, 1.25), (125, 1.39), (150, 1.55), (175, 1.72)],  # Fig.4 p4
        "rth_jc": 0.32,                                                         # p3
        "qg": 162e-9, "ciss": 4818e-12, "coss": 180e-12, "crss": 12e-12, "c_at": 1000.0,  # p2
        "eoss": [(0, 0), (200, 8e-6), (400, 23e-6), (600, 43e-6), (800, 68e-6), (1000, 99e-6), (1200, 139e-6)],  # Fig.16 p6
        "sw_v": 800.0, "sw_t": 25.0,                                            # Fig.24 p7
        "eon": [(10, 0.32e-3), (20, 0.46e-3), (30, 0.61e-3), (40, 0.80e-3), (50, 1.00e-3), (60, 1.22e-3), (70, 1.47e-3), (80, 1.73e-3)],
        "eoff": [(10, 0.07e-3), (20, 0.08e-3), (30, 0.13e-3), (40, 0.20e-3), (50, 0.29e-3), (60, 0.38e-3), (70, 0.49e-3), (80, 0.60e-3)],
        "kv_on": 1.48, "kv_off": 1.30,        # ASSUMED = C3M0016120K fit (same generation; only an 800 V curve read)
        "kt_on": 0.00376, "kt_off": 0.00115,  # Fig.26 p8, 50 A: Eon 1.01 -> 1.58 mJ, Eoff 0.29 -> 0.34 mJ
        "qrr": 928e-9, "qrr_i": 50.0, "qrr_t": 175.0,                           # p3, 800 V, 2600 A/us
        "vsd": 4.2, "vsd_i": 25.0,                                              # p3, 175 C
        "tf": 14e-9, "tf_i": 50.0,                                              # p2
        "check": [("on", 800, 50, 175, 1.58e-3), ("off", 800, 50, 175, 0.34e-3)],
    },
    "C3M0032120K": {
        "vth": 2.5, "gfs": 27.0, "gfs_i": 40.0, "qgd": 34e-9, "qgd_v": 800.0,   # p2 (25 C; 22 S at 175 C)
        "mfr": "Wolfspeed", "src": SEMI + "C3M0032120K.pdf", "rev": "Rev. 6, Sep 2024",
        "vdss": 1200, "package": "TO-247-4", "isolated_tab": False, "gen": "Gen3 C3M",
        "vgs_on": 15.0, "vgs_off": -4.0, "rg_ext": 2.5, "rg_int": 1.7,          # p2
        "rds25": 32e-3, "rds25_max": 43e-3,                                     # p2, VGS 15 V, 40 A
        "rds_norm": [(25, 1.00), (50, 1.02), (75, 1.08), (100, 1.22), (125, 1.38), (150, 1.58), (175, 1.80)],  # Fig.4 p4
        "rth_jc": 0.44,                                                         # p3
        "qg": 118e-9, "ciss": 3357e-12, "coss": 129e-12, "crss": 8e-12, "c_at": 1000.0,
        "eoss": [(0, 0), (200, 7e-6), (400, 19e-6), (600, 34e-6), (800, 53e-6), (1000, 77e-6), (1200, 105e-6)],  # Fig.16 p6
        "sw_v": 800.0, "sw_t": 25.0,                                            # Fig.24 p7
        "eon": [(10, 190e-6), (20, 280e-6), (30, 390e-6), (40, 500e-6), (50, 620e-6), (60, 740e-6)],
        "eoff": [(10, 45e-6), (20, 50e-6), (30, 60e-6), (40, 80e-6), (50, 120e-6), (60, 175e-6)],
        "kv_on": 1.48, "kv_off": 1.30,        # ASSUMED = C3M0016120K fit
        "kt_on": 0.00613, "kt_off": 0.00225,  # p2 table at 175 C vs Fig.24 at 25 C (40 A): Eon 500 -> 955 uJ, Eoff 80 -> 107 uJ (Fig.26 ~85 uJ at 25 C, consistent)
        "qrr": 478e-9, "qrr_i": 40.0, "qrr_t": 175.0,                           # p3, 2250 A/us
        "vsd": 4.2, "vsd_i": 20.0,                                              # p3, 175 C
        "tf": 9e-9, "tf_i": 40.0,
        "check": [("on", 800, 40, 175, 955e-6), ("off", 800, 40, 175, 107e-6)],
    },
    "IMYH200R012M1H": {
        "vth": 4.5, "gfs": 30.0, "gfs_i": 60.0, "qgd": 44e-9, "qgd_v": 1200.0,  # p4
        "mfr": "Infineon", "src": SEMI + "IMYH200R012M1H.pdf", "rev": "Rev. 1.10, 2023-01-16",
        "vdss": 2000, "package": "TO-247-4-PLUS (PG-TO247-4-PLUS-NT14)", "isolated_tab": False, "gen": "CoolSiC M1H 2 kV",
        "vgs_on": 18.0, "vgs_off": -3.0, "rg_ext": 2.0, "rg_int": 3.0,          # p3 Table 3 (15..18 / -5..0 V), p4-5 (test RG 2/2)
        "rds25": 12e-3, "rds25_max": 16.5e-3,                                   # p4, 60 A, 18 V
        "rds_norm": [(25, 1.00), (50, 1.21), (75, 1.46), (100, 1.75), (125, 2.09), (150, 2.50), (175, 2.95)],  # p9 curve (12.2/14.8/17.8/21.3/25.5/30.5/36 mOhm)
        "rth_jc": 0.12, "rth_jc_max": 0.16,                                     # p3 Table 1
        "qg": 246e-9, "ciss": 9700e-12, "coss": 322e-12, "crss": 22e-12, "c_at": 1200.0,  # p4
        "eoss_ref": (1200.0, 216e-6), "eoss_k": 1.6,                            # p4 E_oss at 1200 V; V-shape ASSUMED V^1.6 (C3M Fig.16 shape)
        "sw_v": 1200.0, "sw_t": 175.0,                                          # p10 E=f(ID): 1200 V, 175 C, RG 2 ohm, -2/18 V
        "eon": [(20, 1750e-6), (40, 2800e-6), (60, 3800e-6), (80, 4900e-6), (90, 5450e-6)],
        "eoff": [(20, 350e-6), (40, 750e-6), (60, 1200e-6), (80, 1600e-6), (90, 1850e-6)],
        "err": [(20, 1150e-6 * 0.834), (40, 1700e-6 * 0.834), (60, 1835e-6), (80, 2700e-6 * 0.834), (90, 3000e-6 * 0.834)],  # E_fr curve scaled to p6 table (1835 uJ @ 60 A)
        "kv_on": 1.48, "kv_off": 1.30, "kv_rr": 1.0,  # ASSUMED (no 2nd voltage in datasheet): C3M fit for on/off, E_rr ~ Q_rr*V
        "kt_on": 0.00245, "kt_off": 0.00133, "kt_rr": 0.00332,  # p5/p6: Eon 2400/3800, Eoff 960/1200, Efr 920/1835 uJ (25/175 C)
        "vsd": 3.2, "vsd_i": 60.0,                                              # p6, VGS 0 V, 175 C
        "tf": 27e-9, "tf_i": 60.0,                                              # p5, 175 C
        "check": [("on", 1200, 60, 175, 3800e-6), ("off", 1200, 60, 175, 1200e-6), ("on", 1200, 60, 25, 2400e-6)],
    },
    "IMYH200R024M1H": {
        "vth": 4.5, "gfs": 20.0, "gfs_i": 40.0, "qgd": 22e-9, "qgd_v": 1200.0,  # p4
        "mfr": "Infineon", "src": SEMI + "IMYH200R024M1H.pdf", "rev": "Rev. 1.10",
        "vdss": 2000, "package": "TO-247-4-PLUS (PG-TO247-4-PLUS-NT14)", "isolated_tab": False, "gen": "CoolSiC M1H 2 kV",
        "vgs_on": 18.0, "vgs_off": -3.0, "rg_ext": 2.0, "rg_int": 6.0,          # p4 Table 4 R_G,int 6 ohm (012 has 3 ohm)
        "rds25": 24e-3, "rds25_max": 33e-3,                                     # p4, 40 A, 18 V
        "rds_norm": [(25, 1.00), (50, 1.23), (75, 1.48), (100, 1.79), (125, 2.13), (150, 2.54), (175, 3.00)],  # p9 curve (24/29.5/35.5/43/51/61/72)
        "rth_jc": 0.20, "rth_jc_max": 0.26,                                     # p3
        "qg": 137e-9, "ciss": 4850e-12, "coss": 161e-12, "crss": 11e-12, "c_at": 1200.0,
        "eoss_ref": (1200.0, 109e-6), "eoss_k": 1.6,
        "sw_v": 1200.0, "sw_t": 175.0,                                          # p10 E=f(ID) 175 C 1200 V
        "eon": [(10, 800e-6 * 1.044), (20, 1200e-6 * 1.044), (30, 1600e-6 * 1.044), (40, 2140e-6), (50, 2500e-6 * 1.044), (60, 2950e-6 * 1.044)],
        # E_off: p10 E=f(ID) plot (digitised 148/292/447/614/785/957 uJ); the p5 table gives 435 uJ at 40 A, 30 % lower -
        # the datasheet contradicts itself, the higher (plot) values are used; the p10 E=f(Tvj) plot also reads ~619 uJ at 40 A
        "eoff": [(10, 148e-6), (20, 292e-6), (30, 447e-6), (40, 614e-6), (50, 785e-6), (60, 957e-6)],
        "err": [(10, 520e-6), (20, 720e-6), (30, 920e-6), (40, 1100e-6), (50, 1310e-6), (60, 1500e-6)],
        "kv_on": 1.48, "kv_off": 1.30, "kv_rr": 1.0,
        "kt_on": 0.00308, "kt_off": 0.00054, "kt_rr": 0.00333,                 # p5/p6: 1150/2140, 400/435, 550/1100 uJ
        "vsd": 3.5, "vsd_i": 40.0,
        "tf": 18e-9, "tf_i": 40.0,
        "check": [("on", 1200, 40, 175, 2140e-6), ("off", 1200, 40, 175, 614e-6)],   # E_off check against the p10 plot (see above)
    },
    "MSC035SMA170B4": {
        "vth": 3.2, "vth_min": 1.9, "gfs": None, "gfs_i": 50.0, "qgd": 27e-9, "qgd_v": 850.0,   # p2/p3 (no g_fs printed)
        "vth_175_min": round(1.9 * 2.5 / 3.2, 2),   # DERIVED: p2 min x Fig.1-15 typ ratio 175/25 C (as gen/gdrv.py MSC vth175)
        "vgs_min": -10.0, "vgs_max": 23.0,          # p2 static
        "mfr": "Microchip", "src": SEMI + "MSC035SMA170B4.pdf", "rev": "DS00005160A",
        "vdss": 1700, "package": "TO-247-4", "isolated_tab": False, "gen": "mSiC",
        # gate rail +20/-4 V: UCC14241-Q1 cannot regulate 25 V VDD-VEE (hardware/GDRV-HB/outputs/GDRV-HB_design_check.txt);
        # the datasheet dynamic data are at -5/+20 V (p3) -> vgs_off_ds, and E_off is corrected by eoff_vgs_factor
        "vgs_on": 20.0, "vgs_off": -4.0, "vgs_off_ds": -5.0, "rg_ext": 4.0, "rg_int": 0.85,
        "eoff_vgs_factor": 1.225,   # E_off(-4 V)/E_off(-5 V) at 1000 V / 50 A, calibrated VDMOS with Miller clamp (pv_tradeoff.vgs_factor; pv_design asserts +-3 %)
        "rds25": 35e-3, "rds25_max": 45e-3,                                     # p2, 20 V, 30 A
        "rds_norm": [(25, 1.00), (50, 1.02), (75, 1.09), (100, 1.21), (125, 1.38), (150, 1.57), (175, 1.87)],  # Fig.1-5 p5 (20 V)
        "rth_jc": 0.27, "rth_jc_max": 0.35,                                     # p2
        "qg": 178e-9, "ciss": 3300e-12, "coss": 155e-12, "crss": 13e-12, "c_at": 1000.0,  # p3
        "eoss_ref": (1000.0, 107e-6), "eoss_k": 1.5,  # from Fig.1-8 p5 Q_oss (100 nC @100 V, 320 nC @1000 V): E = n/(n+1)*Q*V, n=0.505
        "sw_v": 1000.0, "sw_t": 25.0,                                           # Fig.1-11/1-12 p6 read at 1000 V, 25 C, RG 4 ohm
        "eon": [(30, 660e-6), (50, 910e-6), (70, 1220e-6)],
        "eoff": [(30, 110e-6), (50, 188e-6), (70, 272e-6)],
        "kv_on": 1.60, "kv_off": 0.80,        # Fig.1-11/1-12: 800 -> 1200 V at 30/50/70 A
        "kt_on": 0.0061, "kt_off": 0.0,       # Fig.1-14 p7: Eon 1440 -> 2540 uJ (25 -> 150 C), Eoff ~flat
        "qrr": 650e-9, "qrr_i": 50.0, "qrr_t": 25.0,                            # p3 Table 1-5 (TJ = 25 C per table header), 1200 V, 8000 A/us
        "vsd": 3.9, "vsd_i": 30.0,                                              # p3, VGS -5 V
        "tf": 17e-9, "tf_i": 50.0,                                              # p3
        "idm": 200.0,                                                           # p2 pulsed drain current (no body-diode surge rating given)
        # 3rd-quadrant body-diode curve at VGS -5..0 V (bold group), read off Fig.1-9 (25 C) and Fig.1-10 (150 C), p6: (A, V)
        "body_iv_25": [(25, 3.4), (50, 3.8), (75, 4.05), (100, 4.3), (150, 4.85)],
        "body_iv_150": [(25, 3.3), (50, 3.6), (75, 3.85), (100, 4.0), (150, 4.75)],
        "eon_rg": [(2.5, 1200e-6), (4.0, 1500e-6), (8.0, 2300e-6), (16.0, 3300e-6)],  # Fig.1-13 p7 (1360 V, 50 A)
        "eoff_rg": [(2.5, 200e-6), (4.0, 245e-6), (8.0, 560e-6), (16.0, 1290e-6)],    # Fig.1-13 p7 (read +-10 %)
        "check": [("on", 1360, 50, 25, 1613e-6), ("off", 1360, 50, 25, 256e-6)],
    },
    "IV2Q17020T4Z": {
            # V_TH p2 (V_GS = V_DS, I_D = 20 mA): min 2.0 / typ 3.0 / max 5.0 V at 25 C; typ 2.2 V at 175 C (no min given at 175 C)
            # vth_175_min DERIVED = 2.2 * (2.0 / 3.0) = 1.47 V (table typ@175 x min25/typ25).  Fig.9 p4 reads 2.09 V at 175 C (typ curve) -> same
            # ratio would give 1.39 V; use 1.39 if the more conservative number is wanted (false-turn-on margin).
            "vth": 3.0, "vth_min": 2.0, "vth_175_min": 1.47, "vth_max": 5.0,
            # g_fs is NOT printed.  DERIVED slope of Fig.8 p4 (V_DS = 20 V, 25 C): 14.7 S @ 20 A, 21 S @ 30 A, 26 S @ 40 A, 32 S @ 50 A, 36.5 S @ 60 A;
            # (V_GS needed for 60 A: 9.2 V at 25 C, 7.4 V at 175 C).  Left None as for MSC035SMA170B4.
            "gfs": None, "gfs_i": 40.0,
            "qgd": 90e-9, "qgd_v": 1200.0,                                        # p2: Q_gd 90 nC (Q_gs 80, Q_g 260) at V_DS 1200 V, I_D 60 A, V_GS -3..18 V; Fig.18 p6 plateau 73..158 nC is consistent
            "mfr": "InventChip", "src": SEMI + "IV2Q17020T4Z.pdf", "rev": "Rev 1.0, Nov. 2025",
            "vdss": 1700, "package": "TO-247-4", "isolated_tab": False, "gen": "Gen2 SiC (IV2Q, AEC-Q101)",
            # p1 pins: 1 Drain, 2 Source, 3 Kelvin Source, 4 Gate.  Tab connection is NOT printed (p8 is a dimension drawing only);
            # isolated_tab False = ASSUMPTION (JEDEC TO-247-4 metal tab, no insulation shown).
            # Gate drive p1: V_GSon recommended 15..18 V, V_GSoff recommended -5..-2 V, "typical -3.5 V".  The R_DS(on) and all dynamic
            # data are at +18 V / -3.5 V, so vgs_on 18 (top of window, lowest R_DS(on)) and vgs_off = vgs_off_ds = -3.5 V -> no E_off V_GS correction.
            "vgs_on": 18.0, "vgs_off": -3.5, "vgs_off_ds": -3.5, "rg_ext": 3.3, "rg_int": 1.8,   # p2: E_sw test R_G(ext) 3.3 ohm (Fig.19-22 p6); R_g 1.8 ohm typ, f = 1 MHz
            # static V_GS limits are MISSING (p1 gives only the transient rating).  ASSUMPTION: static window = recommended window +18 / -5 V
            # (conservative; I_GSS is tested at V_GS -5..+20 V, p2, but that is a leakage test, not a rating).
            "vgs_max": 18.0, "vgs_min": -5.0,
            "vgs_max_tr": 23.0, "vgs_min_tr": -10.0,                              # p1 V_GSmax (transient): -10 .. 23 V, duty < 1 %, pulse width < 200 ns
            "rds25": 20e-3, "rds25_max": 26e-3,                                   # p2: V_GS 18 V, I_D 30 A, 25 C typ 20 / max 26 mOhm (15 V: 25 / 33 mOhm; 175 C: 44 / 46 mOhm typ)
            # Fig.7 p4 (normalised R_DS(on) vs T_j, I_D 30 A, V_GS = 18 V curve, divided by its 25 C node 0.999); Fig.4 p3 absolute 19.9 / 22.3 / 25.0 / 29.2 / 33.4 / 39.0 / 45.0 mOhm
            # for 25..175 C agrees (table 20 mOhm at 25 C, 44 mOhm at 175 C).  The 15 V curve would be 1.00 / 1.08 / 1.19 / 1.33 / 1.50 / 1.72 / 1.97.
            "rds_norm": [(25, 1.00), (50, 1.122), (75, 1.258), (100, 1.467), (125, 1.680), (150, 1.960), (175, 2.262)],
            # p1 thermal data: R_th(j-c) = 0.33 K/W, a single value (Fig.25).  Used for typ AND max (no spread printed).
            "rth_jc": 0.33, "rth_jc_max": 0.33, "zth": True,                      # Fig.25 p7 gives Z_th(t_p) for D = 0.5 ... 0.01 and single pulse; plateau reads 0.319 K/W
            "qg": 260e-9, "ciss": 6300e-12, "coss": 180e-12, "crss": 12e-12, "c_at": 1200.0,   # p2: V_DS 1200 V, f 100 kHz, V_AC 25 mV; Q_g at V_DS 1200 V, I_D 60 A, V_GS -3..18 V
            # Fig.17 p6, read at 200 V steps (axis 0..1400 V, 0..200 uJ).  Table p2 says E_oss = 170 uJ at 1200 V but the curve reads 156 uJ
            # (-8 %); the curve is used (lower E_oss = smaller credit in E_on + E_off + E_rr - E_oss, i.e. conservative).
            "eoss": [(0, 0), (200, 10.4e-6), (400, 29.8e-6), (600, 54.8e-6), (800, 84.3e-6), (1000, 118.8e-6), (1200, 156.4e-6), (1400, 199.1e-6)],
            # Fig.21 p6: E vs I_DS, V_DD 1200 V, R_G(ext) 3.3 ohm, V_GS -3.5/+18 V, L = 200 uH, T_j 25 C, FWD = body diode of an IV2Q17020T4Z.
            # Axis 20..80 A.  E_on = turn-on loss of the DUT as integrated in a double-pulse test with that FWD; the datasheet does NOT state
            # whether the FWD recovery overlap is included, but with a MOSFET body diode as FWD it is part of the DUT's turn-on current (assumed);
            # the FWD's own recovery loss is not in E_on.  Table p2 at 60 A: 2360 / 770 uJ (curve 2314 / 770).
            "sw_v": 1200.0, "sw_t": 25.0,
            "eon": [(20, 1054e-6), (30, 1392e-6), (40, 1722e-6), (50, 2025e-6), (60, 2314e-6), (70, 2593e-6), (80, 2872e-6)],
            "eoff": [(20, 231e-6), (30, 350e-6), (40, 476e-6), (50, 619e-6), (60, 770e-6), (70, 915e-6), (80, 1070e-6)],
            # MISSING: E vs V_DD (all dynamic data are at V_DD = 1200 V only).  ASSUMPTION = MSC035SMA170B4 values 1.6 / 0.8 (nearest 1.7 kV SiC part on file).
            "kv_on": 1.60, "kv_off": 0.80,
            # Fig.22 p6 (E vs T_j, I_D 60 A, V_DD 1200 V, R_G 3.3 ohm, V_GS -3.5/18 V, L 200 uH, FWD = IV2Q17020T4Z): E_on 2314 -> 3371 uJ, E_off 769 -> 703 uJ for 25 -> 175 C
            # (table 2360 -> 3370 and 770 -> 710).  End-to-end linear coefficient (repo convention).  E_on(T) is convex (2581 / 2810 / 3047 uJ at 100 / 125 / 150 C),
            # so the linear model is up to ~10 % high at 100 C (conservative); E_off falls 9 % to 100 C then is flat (kt_off slightly negative).
            "kt_on": 0.00305, "kt_off": -0.00057,
            # p3 reverse-diode table (T_C = 25 C): Q_rr 930 nC, t_rr 40 ns, I_RRM 72 A at I_SD 60 A, V_R 1200 V, di/dt 3 A/ns, R_G(ext) 6.8 ohm, L 200 uH, V_GS -3.5/+18 V.
            # No E_rr / E_fr is given -> pv_devices.e_sw uses K_RR * Q_rr * V.
            "qrr": 930e-9, "qrr_i": 60.0, "qrr_t": 25.0, "trr": 40e-9, "irrm": 72.0,
            # V_SD at the OFF-state gate bias: the table (p3) is at V_GS = 0 V only (3.5 V @ 25 C, 3.3 V @ 175 C, I_SD 30 A).  Fig.12 p5 (175 C), V_GS curves -5 / -2 V:
            # 4.12 / 3.71 V at 30 A; V_GS = -3.5 V (typical off level, exactly half way between the plotted -5 and -2 V) is INTERPOLATED = 3.92 V.  25 C (Fig.11 p5): 4.53 / 4.04 -> 4.29 V.
            # Hot (175 C) value used because VSD0 in pv_devices is the 100-150 C knee.
            "vsd": 3.92, "vsd_i": 30.0,
            "tf": 25e-9, "tf_i": 60.0,                                            # p2: t_f 25 ns at V_DS 1200 V, I_D 60 A, R_G(ext) 3.3 ohm, 25 C (t_d(on) 12, t_r 43, t_d(off) 40 ns)
            "idm": 135.0,                                                         # p1: I_DM 135 A (pulse width limited by SOA and dynamic R_th); I_SM body diode 135 A; no body-diode surge (I2t) rating
            # Third-quadrant body-diode curves at the off-state V_GS, A -> V, read off Fig.11 (25 C) and Fig.12 (175 C), p5, V_GS = -3.5 V INTERPOLATED half way between the -5 and -2 V curves.
            # Axes end at 120 A.  Bounding curves (A: V) for V_GS -5 V / -2 V:
            #   25 C : 20: 4.19/3.66  40: 4.82/4.36  60: 5.29/4.88  90: 5.86/5.51  120: 6.31/6.01   (0 V: 2.92 / 3.81 / 4.44 / 5.17 / 5.76)
            #   175 C: 20: 3.81/3.36  40: 4.38/3.99  60: 4.82/4.45  90: 5.33/5.00  120: 5.74/5.43   (0 V: 2.90 / 3.68 / 4.20 / 4.80 / 5.28)
            "body_iv_25": [(20, 3.92), (40, 4.59), (60, 5.08), (90, 5.69), (120, 6.16)],
            "body_iv_175": [(20, 3.58), (40, 4.18), (60, 4.63), (90, 5.16), (120, 5.59)],
            "body_iv_25_vgs_m5": [(20, 4.19), (40, 4.82), (60, 5.29), (90, 5.86), (120, 6.31)],    # conservative bound (largest V_SD), V_GS = -5 V
            "body_iv_175_vgs_m5": [(20, 3.81), (40, 4.38), (60, 4.82), (90, 5.33), (120, 5.74)],
            # Fig.19 p6: E vs R_G(ext), I_DS 60 A, V_DD 1200 V, V_GS -3.5/18 V, L 200 uH, T_j 25 C, FWD = IV2Q17020T4Z.  Plotted axis is 4..20 ohm;
            # the 3.3 ohm point is the p2 table value (the curve's hidden first node coincides with it: 2364 / 771 uJ).  Nodes 5.1 / 10 / 15 / 20 ohm are the plotted data points.
            "eon_rg": [(3.3, 2360e-6), (5.1, 2701e-6), (10.0, 3729e-6), (15.0, 4710e-6), (20.0, 5496e-6)],
            "eoff_rg": [(3.3, 770e-6), (5.1, 1054e-6), (10.0, 1713e-6), (15.0, 2191e-6), (20.0, 2666e-6)],
            # table p2 switching energies (V_DS 1200 V, I_D 60 A, R_G(ext) 3.3 ohm, V_GS -3.5/18 V, L 200 uH): 25 C 2360 / 770 uJ, 175 C 3370 / 710 uJ
            "check": [("on", 1200, 60, 25, 2360e-6), ("off", 1200, 60, 25, 770e-6), ("on", 1200, 60, 175, 3370e-6), ("off", 1200, 60, 175, 710e-6)],
            # extras
            "t_sc": None,                         # MISSING: no short-circuit withstand time anywhere in the 9 pages.  ASSUMPTION: 0 us credited (desat protection must act on its own).
            "qualification": "AEC-Q101 qualified (p1 Features list; no qualification report on file)",
            "avalanche": None,                    # MISSING: no E_AS / I_AR / UIS rating.  ASSUMPTION: no avalanche credit (clamp every overvoltage).
            "tj_max": 175.0,                      # p1: operating junction -55 .. 175 C, storage -55 .. 175 C
            "id_cont": {25: 94.0, 100: 68.0}, "ptot": 454.0, "is_cont": {25: 96.0, 100: 55.0},   # p1 (V_GS 18 V; I_S at V_GS -2 V, p3); Fig.23/24 p7
    },  # data: inv17/dev_IV2Q17020T4Z.py (Gate-0b extraction)
    "SCT4036KRHR": {
            # threshold: the p2 table prints min 2.8 / max 4.8 V only (no typ); typ is read from Fig.11
            "vth": 3.8,            # Fig.11 p8 read at 25 C: 3.79 V (I_D 11.1 mA, V_DS 10 V)
            "vth_min": 2.8,        # p2 table min, 25 C (note *7: tested after V_GS = +21 V for 100 ms); max 4.8 V
            "vth_175_min": 2.15,   # DERIVED (not given): Fig.11 typ at 175 C = 2.91 V x (min25 / typ25 = 2.8 / 3.79)
            "gfs": 11.0, "gfs_i": 21.0,                  # p3 (V_DS 10 V, I_D 21 A, 25 C, typ only; Fig.12 p8 has g_fs vs I_D)
            "qgd": 24e-9, "qgd_v": 800.0,                # p3 (V_DS 800 V, I_D 21 A, V_GS 18 V)
            "mfr": "ROHM", "src": SEMI + "SCT4036KRHR.pdf", "rev": "Rev.004, 9 Sep 2024 (TSQ50214-SCT4036KRHR)",
            "vdss": 1200, "package": "TO-247-4L", "isolated_tab": False,
            "gen": "ROHM SCT4 series, AEC-Q101 (generation not named in the datasheet)",
            # pinout p1: (1) Drain (2) Power Source (3) Driver Source (4) Gate; driver and power source are NOT exchangeable (p1 note)

            # ---- gate drive.  ROHM RECOMMENDS 0 V OFF (p1 abs-max table: V_GS_off = 0 V, V_GS_on = +15..+18 V).
            # ABSOLUTE NEGATIVE GATE LIMIT = -4 V, both static (V_GSS_DC -4..+21 V) and surge (V_GSS_surge -4..+23 V, t_surge < 300 ns), p1.
            # A -4 V negative rail therefore sits AT the absolute maximum with zero margin for undershoot/ringing, and all
            # switching data (Fig.20-23 p11, p3 table) are at +18/0 V, so there is no datasheet E_off at any other off level.
            "vgs_on": 18.0, "vgs_off": 0.0, "vgs_off_ds": 0.0,   # p1 recommended +15..+18 / 0 V; R_DS(on) at +18 V (p2); E_on/E_off/t_f tested at +18/0 V (p3, Fig.20-23 p11)
            "vgs_max": 21.0, "vgs_min": -4.0,                    # p1 V_GSS_DC (static absolute max)
            "vgs_max_tr": 23.0, "vgs_min_tr": -4.0,              # p1 V_GSS_surge, t_surge < 300 ns (note *5: must be respected esp. with the driver-source pin)
            "rg_ext": 3.3, "rg_int": 1.0,                        # p3 switching-test R_G 3.3 ohm = "External Gate Resistance" (Fig.20-23 p11); p2 gate input resistance R_G 1 ohm typ (1 MHz, open drain)

            "rds25": 36e-3, "rds25_max": 47e-3,                  # p2: V_GS 18 V, I_D 21 A, 25 C (typ 36 / max 47 mOhm); 150 C typ 72 mOhm
            # Fig.14 p9, V_GS 18 V, I_D = 21 A curve (middle of the three), divided by 36 mOhm; curve reads 35.8/40.6/46.7/54.3/63.2/73.7/85.1 mOhm
            # at 25/50/75/100/125/150/175 C (150 C: curve 73.7 vs p2 table 72 mOhm, +2 %)
            "rds_norm": [(25, 1.00), (50, 1.13), (75, 1.30), (100, 1.51), (125, 1.76), (150, 2.05), (175, 2.36)],
            "rth_jc": 0.65, "rth_jc_max": 0.85, "zth": True,     # p2 typ/max (note *9: JESD51-14); Zth curve Fig.3 p5 + 3-stage Foster model p2
            "zth_foster": {"rth": (4.9e-2, 3.0e-1, 3.0e-1), "cth": (8.7e-4, 4.0e-3, 5.2e-2)},   # p2, K/W and Ws/K (sum Rth = 0.649)
            "qg": 91e-9, "ciss": 2335e-12, "coss": 70e-12, "crss": 5e-12, "c_at": 800.0,   # p3 (V_DS 800 V, f 1 MHz, V_GS 0; Q_g at 800 V / 21 A / 18 V); Fig.17 p10 reads 2328/70/5.4 pF at 800 V
            # E_oss: Fig.18 p10 (25 C) read at 100..700 V; the curve leaves the plot top (25 uJ) at 766 V, so the 800 V point is
            # DERIVED from p3 C_o(er) = 84 pF (0..800 V): 0.5*84e-12*800^2 = 26.9 uJ (the curve extrapolates to ~26.6 uJ).  No data above 800 V.
            "eoss": [(0, 0), (100, 1.3e-6), (200, 3.4e-6), (300, 6.2e-6), (400, 9.5e-6), (500, 13.0e-6), (600, 17.2e-6), (700, 21.7e-6), (800, 26.9e-6)],

            # switching energy vs I_D: Fig.22 p11 - V_DD 800 V, R_G(ext) 3.3 ohm, V_GS +18/0 V, L 250 uH, Tvj 25 C (Fig.21-23 same set-up).
            # Freewheeling device = body diode of a device "same type as D.U.T." (Fig.2-1 p12); E_on INCLUDES the diode reverse recovery
            # (p3 table text); parasitics L_sigma 50 nH, C_sigma 10 pF (p3).  Curve spans 5..40 A only.
            "sw_v": 800.0, "sw_t": 25.0,
            "eon": [(5, 132e-6), (10, 165e-6), (15, 198.5e-6), (20, 233.5e-6), (25, 270e-6), (30, 307e-6), (35, 346e-6), (40, 386e-6)],   # Fig.22 p11 as read; 21 A -> 240.7 uJ vs p3 table 239
            # E_off: Fig.22 as read (uJ): 4.0/7.3/16.2/28.3/43.6/62.1/84.0/109.1 at 5..40 A, i.e. 31.0 uJ at 21 A = +19 % over the p3 table (26 uJ).
            # The datasheet contradicts itself: Fig.21 p11 reads 26.3 uJ at 800 V / 21 A (agrees with the table), Fig.23 p11 reads 28.8 uJ at R_G 3.3.
            # The Fig.22 shape is scaled by 0.84 (= 26 / 31.0) so e_sw() reproduces the table value (the brief's 12 % check); the raw curve is +19 % higher.
            "eoff": [(5, 4.0e-6 * 0.84), (10, 7.3e-6 * 0.84), (15, 16.2e-6 * 0.84), (20, 28.3e-6 * 0.84), (25, 43.6e-6 * 0.84),
                     (30, 62.1e-6 * 0.84), (35, 84.0e-6 * 0.84), (40, 109.1e-6 * 0.84)],
            # E vs V_DS: Fig.21 p11 (21 A, 3.3 ohm, 25 C), 200..800 V only.  E_on 72.6 / 146.0 / 237 uJ and E_off 18.4 / 23.1 / 26.3 uJ at 400 / 600 / 800 V:
            # exponent 400->800 V: E_on 1.71, E_off 0.52; 600->800 V: E_on 1.68, E_off 0.45.  Measured at 21 A only; no data above 800 V.
            "kv_on": 1.70, "kv_off": 0.50,
            # MISSING: the datasheet has NO switching energy vs temperature (p3 table and Fig.20-23 are all Tvj = 25 C).
            # ASSUMPTION: kt_on = 0.0061, kt_off = 0.0 (MSC035SMA170B4 values, as the brief prescribes); see GAPS.
            "kt_on": 0.0061, "kt_off": 0.0,
            "qrr": 140e-9, "qrr_i": 21.0, "qrr_t": 25.0,         # p4: I_F 21 A, V_R 800 V, di/dt 3700 A/us, L_sigma 50 nH, 25 C (trr 9.2 ns, Irrm 31 A); no hot Q_rr given
            "vsd": 3.3, "vsd_i": 21.0,                           # p4 table, V_GS 0 V (= recommended off level), 25 C.  Hot: Fig.7/8 read 3.62 V at 21 A, 150 C (HIGHER); see vsd_hot
            "vsd_hot": (3.62, 21.0, 150.0),                      # (V, A, C) Fig.7 p7 V_GS 0 V curve (Fig.8 p7 agrees: 3.59 V) - not a table value
            "tf": 9.6e-9, "tf_i": 21.0,                          # p3 (800 V, 21 A, R_G 3.3 ohm, +18/0 V, 25 C)
            "idm": 84.0,                                         # p1 pulsed drain current I_D,pulse, V_GS = V_GS_on, Tc 25 C (*2: limited by Tvj max); body-diode pulsed 43 A (PW <= 1.5 us, duty <= 5 %), surge 84 A (V_GS 0 V, PW <= 10 us, protective use) p1
            # third-quadrant curves at the off-state V_GS = 0 V (second curve from the left of the V_GS = -4/0/15/18 V set), (|A|, |V|):
            "body_iv_25": [(5, 2.5), (10, 2.75), (20, 3.3), (30, 3.75), (40, 4.2)],        # Fig.5 p6, 25 C (21 A reads 3.35 V; p4 table 3.3 V)
            "body_iv_150": [(5, 2.3), (10, 2.8), (20, 3.55), (30, 4.1), (40, 4.65)],       # Fig.7 p7, 150 C
            # E vs R_G: Fig.23 p11 - 800 V, 21 A, 25 C, +18/0 V, L 250 uH (R_G 2..20 ohm).  Only RATIOS are used by eon_rg_factor/eoff_rg_factor,
            # so E_off is left as read (28.8 uJ at 3.3 ohm, +11 % over the table, see the E_off note above).  E_on leaves the plot top (600 uJ) at 16.4 ohm.
            "eon_rg": [(2.0, 210e-6), (3.3, 243e-6), (5.0, 286e-6), (10.0, 406e-6), (15.0, 517e-6)],
            "eoff_rg": [(2.0, 16.4e-6), (3.3, 28.8e-6), (5.0, 45.2e-6), (10.0, 96.3e-6), (15.0, 151.9e-6), (20.0, 211.6e-6)],
            "check": [("on", 800, 21, 25, 239e-6), ("off", 800, 21, 25, 26e-6)],           # p3 table (800 V, 21 A, 3.3 ohm, +18/0 V, 25 C)

            # ---- extras
            # short-circuit withstand p3 + note *10: typ 4.5 us at V_GS(on) +15 V, typ 4.0 us at +18 V; V_DS <= 800 V, V_DS,peak <= 1200 V,
            # Tvj(start) = 25 C, R_G = 2.2 ohm, SINGLE pulse.  It sits in the "Typ." column (no guaranteed minimum) and only the 25 C start is given.
            "t_sc": 4.0, "t_sc_15v": 4.5,
            "t_sc_cond": "typ, V_GS 18 V (15 V: 4.5 us), V_DS<=800 V, V_DS,peak<=1200 V, Tvj(start) 25 C, R_G 2.2 ohm, single pulse (p3, *10)",
            "qualification": "Qualified to AEC-Q101 (p1 Features 1; title 'Automotive Grade N-channel SiC power MOSFET')",
            "avalanche": None,    # MISSING: no E_AS / avalanche rating in any of the 17 pages
            "tj_max": 175.0,      # p1 virtual junction temperature; storage -40..+175 C
    },  # data: s12a/dev_SCT4036KRHR.py (Gate-0b extraction)
    "IV2Q17040T4Z": {
            # V_TH p2 (V_GS = V_DS, I_D = 10 mA): min 2 / typ 3.1 / max 4.5 V at 25 C; typ 2.26 V at 175 C (no min given at 175 C); Fig.9 p4 reads 3.09 V / 2.26 V (agrees)
            # vth_175_min DERIVED = 2.26 * (2.0 / 3.1) = 1.46 V (table typ@175 x min25/typ25)
            "vth": 3.1, "vth_min": 2.0, "vth_175_min": 1.46, "vth_max": 4.5,
            # g_fs is NOT printed.  DERIVED slope of Fig.8 p4 (V_DS = 20 V, 25 C): 13 S @ 20 A, 19 S @ 30 A, 23 S @ 40 A, 28.5 S @ 50 A, 31.6 S @ 60 A;
            # (V_GS needed for 60 A: 10.1 V at 25 C, 8.3 V at 175 C).  Left None as for MSC035SMA170B4.
            "gfs": None, "gfs_i": 40.0,
            "qgd": 61.6e-9, "qgd_v": 1000.0,                                      # p2: Q_gd 61.6 nC (Q_gs 42.2, Q_g 156.4) at V_DS 1000 V (NOT 1200 V), I_D 30 A, V_GS -3..18 V; Fig.18 p6 plateau 42..104 nC consistent
            "mfr": "InventChip", "src": SEMI + "IV2Q17040T4Z.pdf", "rev": "Rev 1.0, Nov. 2025",
            "vdss": 1700, "package": "TO-247-4", "isolated_tab": False, "gen": "Gen2 SiC (IV2Q, AEC-Q101)",
            # p1 pins: 1 Drain, 2 Source, 3 Kelvin Source, 4 Gate.  Tab connection is NOT printed (p8 is a dimension drawing only);
            # isolated_tab False = ASSUMPTION (JEDEC TO-247-4 metal tab, no insulation shown).
            # Gate drive p1: V_GSon recommended 15..18 V, V_GSoff recommended -5..-2 V, "typical -3.5 V".  The R_DS(on) and all dynamic
            # data are at +18 V / -3.5 V, so vgs_on 18 (top of window, lowest R_DS(on)) and vgs_off = vgs_off_ds = -3.5 V -> no E_off V_GS correction.
            "vgs_on": 18.0, "vgs_off": -3.5, "vgs_off_ds": -3.5, "rg_ext": 3.3, "rg_int": 2.2,   # p2: E_sw test R_G(ext) 3.3 ohm (Fig.19-22 p6); R_g 2.2 ohm typ, f = 1 MHz
            # static V_GS limits are MISSING (p1 gives only the transient rating).  ASSUMPTION: static window = recommended window +18 / -5 V
            # (conservative; I_GSS is tested at V_GS -10..+20 V, p2, but that is a leakage test, not a rating).
            "vgs_max": 18.0, "vgs_min": -5.0,
            "vgs_max_tr": 23.0, "vgs_min_tr": -10.0,                              # p1 V_GSmax (transient): -10 .. 23 V, duty < 1 %, pulse width < 200 ns
            "rds25": 40e-3, "rds25_max": 52e-3,                                   # p2: V_GS 18 V, I_D 30 A, 25 C typ 40 / max 52 mOhm (15 V: 44.7 mOhm typ; 175 C: 82.8 mOhm @ 18 V, 86.3 @ 15 V)
            # Fig.5 p4 (normalised R_DS(on) vs T_j, I_D 30 A, V_GS = 18 V curve); Fig.4 p3 absolute (18 V) 37.2 / 41.7 / 47.2 / 54.0 / 62.2 / 71.9 / 83.0 mOhm for 25..175 C has the same
            # shape.  The 15 V curve would be 1.00 / 1.07 / 1.17 / 1.31 / 1.48 / 1.69 / 1.93.  NOTE: the curves sit 7 % BELOW the table at 25 C (37.2 vs 40 mOhm) but hit the table at
            # 175 C (83.0 vs 82.8), so rds25 (table 40) x norm(175) = 89 mOhm is 7.5 % above the table's 82.8 mOhm hot value (conservative).
            "rds_norm": [(25, 1.00), (50, 1.119), (75, 1.266), (100, 1.449), (125, 1.668), (150, 1.928), (175, 2.226)],
            # p1 thermal data: R_th(j-c) = 0.42 K/W, a single value (Fig.25).  Used for typ AND max (no spread printed).
            "rth_jc": 0.42, "rth_jc_max": 0.42, "zth": True,                      # Fig.25 p7 gives Z_th(t_p) for D = 0.5 ... 0.01 and single pulse; plateau reads 0.41 K/W
            "qg": 156.4e-9, "ciss": 3430e-12, "coss": 113e-12, "crss": 9.75e-12, "c_at": 1000.0,   # p2: V_DS 1000 V (NOT 1200 V), f 100 kHz, V_AC 25 mV; Q_g at V_DS 1000 V, I_D 30 A, V_GS -3..18 V; Fig.16 p5 agrees (3407 / 113 / 9.7 pF at 950 V)
            # Fig.17 p6, read at 100 V steps (axis 0..1000 V, 0..80 uJ); table p2 E_oss = 73.7 uJ at 1000 V (curve 73.2, agrees).  The curve ends at 1000 V (axis limit):
            # pv_devices extrapolates linearly above that (~2 % low at 1200 V, ~4 % low at 1360 V against a V^1.5 law, which is on the conservative side for the E_oss credit).
            "eoss": [(0, 0), (100, 2.2e-6), (200, 6.2e-6), (300, 11.6e-6), (400, 18.2e-6), (500, 25.5e-6), (600, 33.7e-6), (700, 42.6e-6), (800, 52.2e-6), (900, 62.5e-6), (1000, 73.2e-6)],
            # Fig.21 p6: E vs I_DS, V_DD 1200 V, R_G(ext) 3.3 ohm, V_GS -3.5/+18 V, L = 100 uH, T_j 25 C, FWD = body diode of an IV2Q17040T4Z.
            # Axis 20..100 A.  E_on = turn-on loss of the DUT as integrated in a double-pulse test with that FWD; the datasheet does NOT state
            # whether the FWD recovery overlap is included, but with a MOSFET body diode as FWD it is part of the DUT's turn-on current (assumed);
            # the FWD's own recovery loss is not in E_on.  Table p2 at 50 A: 1582 / 305.7 uJ (curve 1559 / 305).  E_off curve has a knee near 80 A (528 -> 636 uJ at 80 -> 88 A).
            "sw_v": 1200.0, "sw_t": 25.0,
            "eon": [(20, 775e-6), (30, 1004e-6), (40, 1300e-6), (50, 1559e-6), (60, 1884e-6), (70, 2170e-6), (80, 2503e-6), (90, 2852e-6), (100, 3380e-6)],
            "eoff": [(20, 124e-6), (30, 171e-6), (40, 232e-6), (50, 305e-6), (60, 376e-6), (70, 457e-6), (80, 528e-6), (90, 664e-6), (100, 777e-6)],
            # MISSING: E vs V_DD (all dynamic data are at V_DD = 1200 V only).  ASSUMPTION = MSC035SMA170B4 values 1.6 / 0.8 (nearest 1.7 kV SiC part on file).
            "kv_on": 1.60, "kv_off": 0.80,
            # Fig.22 p6 (E vs T_j, I_D 50 A, V_DD 1200 V, R_G 3.3 ohm, V_GS -3.5/18 V, L 100 uH, FWD = IV2Q17040T4Z): E_on 1559 -> 2455 uJ, E_off 310 -> 405 uJ for 25 -> 175 C
            # (table 1582 -> 2460 and 305.7 -> 406.6).  End-to-end linear coefficient (repo convention).  E_on(T) is not linear (1881 uJ at 100 C vs 2007 uJ from the linear
            # model = 7 % high, conservative); E_off(T) is concave (377 uJ at 100 C vs 358 uJ from the linear model = 5 % low).
            "kt_on": 0.00383, "kt_off": 0.00207,
            # p3 reverse-diode table (T_C = 25 C): Q_rr 635.5 nC, t_rr 69.5 ns, I_RRM 29 A at I_SD 50 A, V_R 1200 V, di/dt 3000 A/us, R_G(ext) 10 ohm, L 100 uH, V_GS -3.5/+18 V.
            # No E_rr / E_fr is given -> pv_devices.e_sw uses K_RR * Q_rr * V.
            "qrr": 635.5e-9, "qrr_i": 50.0, "qrr_t": 25.0, "trr": 69.5e-9, "irrm": 29.0,
            # V_SD at the OFF-state gate bias: the table (p3) is at V_GS = 0 V only (4.6 V @ 25 C, 4.5 V @ 175 C, I_SD 30 A; Fig.11/12 read 4.64 / 4.51 V, agrees).  Fig.12 p5 (175 C),
            # V_GS curves -5 / -2 V: 5.32 / 4.84 V at 30 A; V_GS = -3.5 V (typical off level, exactly half way between the plotted -5 and -2 V) is INTERPOLATED = 5.08 V.
            # 25 C (Fig.11 p5): 6.09 / 5.39 -> 5.74 V.  Hot (175 C) value used because VSD0 in pv_devices is the 100-150 C knee.  The diode is a lot more resistive than IV2Q17020T4Z's.
            "vsd": 5.08, "vsd_i": 30.0,
            "tf": 12.8e-9, "tf_i": 50.0,                                          # p2: t_f 12.8 ns at V_DS 1200 V, I_D 50 A, R_G(ext) 3.3 ohm, 25 C (t_d(on) 8.6, t_r 32, t_d(off) 23.2 ns)
            "idm": 145.0,                                                         # p1: I_DM 145 A (pulse width limited by SOA and dynamic R_th); I_SM body diode 141.25 A; no body-diode surge (I2t) rating
            # Third-quadrant body-diode curves at the off-state V_GS, A -> V, read off Fig.11 (25 C) and Fig.12 (175 C), p5, V_GS = -3.5 V INTERPOLATED half way between the -5 and -2 V curves.
            # Axes end at 80 A.  Bounding curves (A: V) for V_GS -5 V / -2 V:
            #   25 C : 20: 5.55/4.80  35: 6.32/5.64  50: 6.87/6.28  65: 7.28/6.78  80: 7.60/7.19   (0 V: 3.97 / 4.93 / 5.67 / 6.29 / 6.80)
            #   175 C: 20: 4.86/4.34  35: 5.51/5.05  50: 6.00/5.59  65: 6.41/6.03  80: 6.78/6.42   (0 V: 3.90 / 4.75 / 5.35 / 5.83 / 6.23)
            "body_iv_25": [(20, 5.17), (35, 5.98), (50, 6.57), (65, 7.03), (80, 7.40)],
            "body_iv_175": [(20, 4.60), (35, 5.28), (50, 5.79), (65, 6.22), (80, 6.60)],
            "body_iv_25_vgs_m5": [(20, 5.55), (35, 6.32), (50, 6.87), (65, 7.28), (80, 7.60)],    # conservative bound (largest V_SD), V_GS = -5 V
            "body_iv_175_vgs_m5": [(20, 4.86), (35, 5.51), (50, 6.00), (65, 6.41), (80, 6.78)],
            # Fig.19 p6: E vs R_G(ext), I_DS 50 A, V_DD 1200 V, V_GS -3.5/18 V, L 100 uH, T_j 25 C, FWD = IV2Q17040T4Z.  Plotted axis is 3..20 ohm, so the 3.3 ohm test point is on the curve
            # (1588 / 307 uJ read, table 1582 / 305.7).
            "eon_rg": [(3.3, 1588e-6), (5.0, 1894e-6), (10.0, 2712e-6), (15.0, 3467e-6), (20.0, 4193e-6)],
            "eoff_rg": [(3.3, 307e-6), (5.0, 460e-6), (10.0, 832e-6), (15.0, 1221e-6), (20.0, 1577e-6)],
            # table p2 switching energies (V_DS 1200 V, I_D 50 A, R_G(ext) 3.3 ohm, V_GS -3.5/18 V, L 100 uH): 25 C 1582 / 305.7 uJ, 175 C 2460 / 406.6 uJ
            "check": [("on", 1200, 50, 25, 1582e-6), ("off", 1200, 50, 25, 305.7e-6), ("on", 1200, 50, 175, 2460e-6), ("off", 1200, 50, 175, 406.6e-6)],
            # extras
            "t_sc": None,                         # MISSING: no short-circuit withstand time anywhere in the 9 pages.  ASSUMPTION: 0 us credited (desat protection must act on its own).
            "qualification": "AEC-Q101 qualified (p1 Features list; no qualification report on file)",
            "avalanche": None,                    # MISSING: no E_AS / I_AR / UIS rating.  ASSUMPTION: no avalanche credit (clamp every overvoltage).
            "tj_max": 175.0,                      # p1: operating junction -55 .. 175 C, storage -55 .. 175 C
            "id_cont": {25: 58.0, 100: 42.5}, "ptot": 357.0, "is_cont": {25: 56.7, 100: 33.0},   # p1 (V_GS 18 V; I_S at V_GS -2 V, p3); Fig.23/24 p7
    },  # data: inv17/dev_IV2Q17040T4Z.py (Gate-0b extraction)
    "SG2M035120LJ": {
            "vth": 2.8, "vth_min": 2.3,   # p4 table: min 2.3 / typ 2.8 / max 3.6 V, 25 C, V_DS = V_GS, I_D 9 mA (Fig.11 p7 reads 2.87 V at 25 C)
            # p4 gives only a TYP at 175 C (2.2 V; Fig.11 p7 reads 2.2 V) and no min: DERIVED = 2.2 x (2.3 / 2.8) = 1.81 V
            "vth_175_min": 1.81,
            "gfs": 24.0, "gfs_i": 32.0,   # p4 (V_DS 20 V, I_D 32 A, 25 C; 22 S at 175 C), typ only; Fig.7 p7
            "qgd": 18e-9, "qgd_v": 800.0,                                    # p5 (V_DS 800 V, V_GS -4/+18 V, I_D 32 A); Q_gs 18, Q_g 57 nC
            "mfr": "Sichain (Ningbo)", "src": SEMI + "SG2M035120LJ.pdf", "rev": "V01_03 (Final), 2026-02-02",
            "vdss": 1200, "package": "TO-247-4L", "isolated_tab": False, "gen": "TriQSiC G2",
            # pinout p1 (graphic only, not in the text layer): Pin 1 Drain (= TAB), Pin 2 Power Source, Pin 3 Driver Source, Pin 4 Gate

            # ---- gate drive: recommended operational -4/+18 V (p3 V_GSop); the E/Q/t data are at -4/+18 V (p5, Fig.23-27).
            # Absolute max p3 Table 2: -8/+22 V, BUT Note 1: "when using MOSFET body diode V_GS,max = -4/+22 V" (and p5 Note 2: recommended max
            # -4 V with the SiC body diode).  A bridge/DAB leg conducts the body diode in every dead time, so vgs_min is taken as -4 V (conservative);
            # the printed -8 V applies only if the body diode never conducts.  The recommended -4 V off level is therefore AT the body-diode limit.
            "vgs_on": 18.0, "vgs_off": -4.0, "vgs_off_ds": -4.0,
            "vgs_max": 22.0, "vgs_min": -4.0,
            "vgs_max_tr": 25.0, "vgs_min_tr": -10.0,   # p3 V_GSpulse -10/+25 V, t_p <= 0.5 us, duty < 0.01
            "rg_ext": 2.5, "rg_int": 2.9,              # p5 test R_g 2.5 ohm (Fig.23-27 "R_G(ext)"); p4 R_g,int 2.9 ohm typ (V_AC 25 mV, 1 MHz, open drain)

            "rds25": 33e-3, "rds25_max": 48e-3,        # p4: V_GS 18 V, I_D 32 A, 25 C (typ 33 / max 48 mOhm); 175 C typ 62 mOhm (p4); V_GS 15 V: 39 / 59, 65 at 175 C
            # Fig.6 p6 (V_GS 18 V, I_D 32 A) / its 25 C reading 33.7: 35.9/38.9/43.0/47.8/53.7 mOhm at 50..150 C; Fig.4 p6 (normalised, V_GS 18 V curve)
            # agrees (1.07/1.16/1.28/1.42-1.45/1.60-1.64) and reaches 1.86 at 174 C; p4 table 175 C: 62/33 = 1.88.  NB: the curve has a shallow
            # minimum near 0 C (32.6 mOhm at 0 C, 34.6 at -50 C) - irrelevant above 25 C.
            "rds_norm": [(25, 1.00), (50, 1.07), (75, 1.16), (100, 1.28), (125, 1.42), (150, 1.60), (175, 1.87)],
            "rth_jc": 0.45, "rth_jc_max": 0.56, "zth": True,   # p3 Table 3 (typ / max, no JESD51-14 statement); Zth curve Fig.21 p9 (D = 0.01..0.5 and single pulse); no Foster numbers
            "qg": 57e-9, "ciss": 1843e-12, "coss": 102e-12, "crss": 6.6e-12, "c_at": 1000.0,   # p4-p5 (V_DS 1000 V, V_GS 0, 25 C, V_AC 25 mV, f 100 kHz); Fig.17/18 p8
            # E_oss Fig.16 p8 (read at 200 V steps; p4 prints E_oss 61 uJ typ, which the curve reproduces at 1000 V: 60.8 uJ)
            "eoss": [(0, 0), (200, 5.5e-6), (400, 15.3e-6), (600, 28.3e-6), (800, 43.1e-6), (1000, 61e-6), (1200, 80e-6)],

            # switching energy vs I_D: Fig.24 p9 - V_DD 800 V, R_G 2.5 ohm, V_GS -4/+18 V, T_J 25 C (Fig.23 p9 is the 600 V twin, same set-up).
            # Table 6 p5 (E_on 212 / E_off 48 uJ at 800 V, 32 A, 2.5 ohm, L = 120 uH) matches the curve at 32 A.
            # Freewheeling device = body diode of the complementary MOSFET (Fig.B p12: T1/T2 identical MOSFET symbols with body diodes);
            # the text does not say whether E_on includes the recovery, in this half-bridge it necessarily does.  E_total = E_on + E_off in the plots.
            "sw_v": 800.0, "sw_t": 25.0,
            "eon": [(10, 144e-6), (15, 159e-6), (20, 175e-6), (25, 191e-6), (30, 207e-6), (35, 225e-6), (40, 246e-6), (45, 269e-6),
                    (50, 293e-6), (55, 317e-6), (60, 342e-6)],                 # Fig.24 p9 as read; 32 A -> 214.2 uJ vs table 212
            "eoff": [(10, 29e-6), (15, 33.5e-6), (20, 38e-6), (25, 43e-6), (30, 49e-6), (35, 56e-6), (40, 69e-6), (45, 85e-6),
                     (50, 102e-6), (55, 120e-6), (60, 137e-6)],                # Fig.24 p9 as read; 32 A -> 51.8 uJ (+8 %) vs table 48 (Fig.24 itself reads 51.3)
            # E vs V_DD: Fig.23 (600 V) vs Fig.24 (800 V), 12..58 A: E_on exponent 2.13-2.18 at every current (mean 2.17; 114.2 -> 213.9 uJ at 32 A);
            # E_off exponent 1.69 at 12 A falling to 0.98 at 58 A, 1.28 at the 32 A test current (35.4 -> 51.3 uJ), 1.3 used.
            # Only two voltages (600/800 V) exist: nothing below 600 V or above 800 V.
            "kv_on": 2.17, "kv_off": 1.3,
            # E vs T_J: Fig.26 p10 (800 V, 32 A, R_G 2.5, -4/+18 V), 25 -> 175 C: E_on 213 -> 278 uJ (+30 %, 0.00205 /K, concave); E_off 48.4 -> 49.3 uJ (flat,
            # 0.0001 /K; p1 feature "temperature independent turn-off switching losses"); E_total 262 -> 327 uJ
            "kt_on": 0.00205, "kt_off": 0.0001,
            # Q_rr p5 Table 7: V_R 800 V, I_SD 32 A, di/dt 3370 A/us, V_GS -4 V, T_J = 175 C (the 175 C applies to the t_rr/Q_rr/I_rrm block); t_rr 31 ns, I_rrm 31 A.  No 25 C Q_rr.
            "qrr": 575e-9, "qrr_i": 32.0, "qrr_t": 175.0,
            "vsd": 3.7, "vsd_i": 16.0,                       # p5 Table 7, V_GS -4 V, I_SD 16 A, 25 C (3.3 V at 175 C, same row)
            "vsd_hot": (3.3, 16.0, 175.0),                   # (V, A, C) p5 Table 7 - LOWER than the 25 C value (Fig.9/10 p7 agree: 3.70 / 3.28 V)
            "tf": 7.4e-9, "tf_i": 32.0,                      # p5 Table 6 (800 V, 32 A, R_g 2.5 ohm, -4/+18 V, L 120 uH, 25 C); Fig.27 p10 t_f vs R_G
            "idm": 200.0,                                    # p3 I_D(pulse), t_p = 100 us, limited by T_J,max (Fig.22 SOA p9); no body-diode surge rating; I_S 63 A continuous (V_GS -4 V, Tc 25 C)
            # body-diode curves at the off-state V_GS = -4 V (leftmost of the -4 / -2 / 0 V set), (|A|, |V|):
            "body_iv_25": [(5, 3.2), (16, 3.7), (32, 4.17), (50, 4.65), (100, 5.65)],     # Fig.9 p7, 25 C (16 A reads 3.70, p5 table 3.7)
            "body_iv_175": [(5, 2.8), (16, 3.28), (32, 3.69), (50, 4.05), (100, 4.84)],   # Fig.10 p7, 175 C (16 A reads 3.28, p5 table 3.3)
            # E vs R_G: Fig.25 p10 - 800 V, 32 A, 25 C, -4/+18 V (R_G 2.5..20 ohm); 2.5 ohm reads 212.7 / 49.7 uJ = the p5 table (212 / 48)
            "eon_rg": [(2.5, 213e-6), (5.0, 286e-6), (7.5, 354e-6), (10.0, 419e-6), (15.0, 531e-6), (20.0, 634e-6)],
            "eoff_rg": [(2.5, 49.7e-6), (5.0, 79.6e-6), (7.5, 106e-6), (10.0, 136e-6), (15.0, 192e-6), (20.0, 247e-6)],
            "check": [("on", 800, 32, 25, 212e-6), ("off", 800, 32, 25, 48e-6)],           # p5 Table 6 (800 V, 32 A, 2.5 ohm, -4/+18 V, 25 C)

            # ---- extras
            # MISSING: no short-circuit withstand time anywhere in the 14 pages.  ASSUMPTION: no short-circuit rating - the gate-driver desaturation
            # protection must be designed as for a device that cannot be assumed to survive any short circuit (no number is used).
            "t_sc": None,
            "qualification": "none stated (no AEC-Q101/JEDEC statement); p14 note 8 refers high-reliability uses (transportation, safety, medical ...) to a Sichain representative",
            "avalanche": None,    # MISSING: no E_AS / avalanche rating or statement in the 14 pages
            "tj_max": 175.0,      # p1 Table 1 and p3 Table 2 (operating and storage -55..+175 C)
    },  # data: s12a/dev_SG2M035120LJ.py (Gate-0b extraction)
    "SG2M020170HJ": {
            # Table 4 p4 (T_C 25 C): V_GS(th) min/typ/max 2.5/3.1/4.0 V at V_DS = V_GS, I_D = 24 mA; typ 2.3 V at 175 C (Fig. 11 p7 reads 3.19 / 2.36 V).
            # vth_175_min = 1.85 V is DERIVED = typ@175 C x (min25/typ25) = 2.3 x 2.5/3.1 = 1.855; the datasheet prints no minimum at 175 C.
            "vth": 3.1, "vth_min": 2.5, "vth_175_min": 1.85,
            "gfs": 51.0, "gfs_i": 75.0,                       # p4 Table 4: V_DS 20 V, I_D 75 A, 25 C typ (41 S at 175 C)
            "qgd": 56e-9, "qgd_v": 1200.0,                    # p5 Table 5: V_DS 1200 V, V_GS -4/+18 V, I_D 75 A
            "mfr": "Sichain", "src": SEMI + "SG2M020170HJ.pdf", "rev": "V02_03, 2025-12-30 (Final; p13 revision history: V01_00 2024-03-01 ... V02_03)",
            # mfr long name (p1/p3 footer): Sichain Semiconductor (Ningbo) Co., Ltd. (Chinese name on the datasheet footer)
            "vdss": 1700, "package": "TO-247-4L (outline TO-247-4L-A, p11)", "isolated_tab": False, "gen": "TriQSiC G2",
            # p1 pin symbol: pin 1 = Drain and TAB (not isolated), pin 2 = Power Source, pin 3 = Driver (Kelvin) Source, pin 4 = Gate.
            # Gate drive: VGSop -4/+18 V recommended (p3 Table 2); the dynamic data (Table 6, Figs. 23-27) are at -4/+18 V too, so vgs_off_ds = vgs_off.
            # vgs_max/vgs_min = absolute static -8/+22 V (p3 Table 2; Note 1: with the body diode conducting V_GS must stay within -4/+22 V,
            # Note 2 p5: max recommended V_GS = -4 V while the body diode conducts).  Transient: -10/+25 V for t_p <= 0.5 us, duty < 0.01 (p3).
            "vgs_on": 18.0, "eoff_vgs_factor": 1.1045, "vgs_off": -3.5, "vgs_off_ds": -4.0,
            "vgs_max": 22.0, "vgs_min": -4.0, "vgs_max_tr": 25.0, "vgs_min_tr": -10.0,
            "rg_ext": 2.5, "rg_int": 2.7,                     # p5 Table 6 test R_g 2.5 ohm; p4 Table 4 R_g,int 2.7 ohm typ (1 MHz, 25 mV, open drain)
            "rds25": 20e-3, "rds25_max": 28e-3,               # p4 Table 4: V_GS 18 V, I_D 75 A, 25 C: typ 20 / max 28 mOhm (typ 23 / max 35 mOhm at 15 V; typ 42 mOhm at 175 C, no max)
            # Fig. 4 p6 (normalised R_DS(on), I_D 75 A, V_GS 18 V curve = the upper curve above 25 C, both curves cross at 1.0 at 25 C):
            # 1.11/1.26/1.43/1.63/1.88/2.15; Fig. 6 p6 (absolute, V_GS 18 V) 19.9/21.9/24.8/28.4/32.5/37.2/42.2 mOhm -> 1.00/1.10/1.25/1.43/1.64/1.87/2.13.
            # Average used.  Table gives 42/20 = 2.10 at 175 C.
            "rds_norm": [(25, 1.00), (50, 1.11), (75, 1.25), (100, 1.43), (125, 1.63), (150, 1.87), (175, 2.14)],
            "rth_jc": 0.22, "rth_jc_max": 0.28, "zth": True,  # p3 Table 3 typ 0.22 / max 0.28 K/W; Zth(j-c) curve with duty cycles = Fig. 21 p9 (no Foster/Cauer values printed)
            "qg": 209e-9, "ciss": 5265e-12, "coss": 188e-12, "crss": 7.5e-12, "c_at": 1400.0,   # p4-5 Table 5: V_DS 1400 V, V_GS 0, 100 kHz, 25 mV (Q_g at V_DS 1200 V, -4/+18 V, 75 A)
            # Fig. 16 p8 (E_oss vs V_DS, 0..1700 V, read every 100 V); Table 5 p4 prints E_oss = 189 uJ at 1400 V (curve reads 190)
            "eoss": [(0, 0), (200, 9.8e-6), (400, 27.8e-6), (600, 51.3e-6), (800, 79.8e-6), (1000, 112.7e-6), (1200, 149.6e-6), (1400, 190e-6), (1700, 256e-6)],
            # E vs I: Fig. 24 p9, clamped inductive, V_DD 1200 V, V_GS -4/+18 V, R_G 2.5 ohm, T_J 25 C (the figure plots 30..120 A only).
            # Freewheeling device: the complementary MOSFET of the half-bridge dynamic test circuit (Fig. B p12: T1/T2 drawn as identical MOSFETs
            # with their body diodes, no external SBD) -> E_on INCLUDES the recovery of that body diode at the test T_J (25 C).  No separate
            # E_rr/E_fr is printed.  Table 6 p5 (V_DS 1200 V, V_GS -4/+18 V, I_D 75 A, R_g 2.5 ohm, L = 16.7 uH, T_C 25 C): E_on 2229, E_off 501 uJ.
            # Fig. 24 reads 2183 / 515 uJ at 75 A (-2.1 % / +2.8 %).  Fig. 23 p9 (same, V_DD 1000 V) read for kv:
            #   E_on(30..120 A step 15) = 761 978 1305 1692 2099 2516 2944 uJ;  E_off = 153 195 290 414 549 701 873 uJ.
            "sw_v": 1200.0, "sw_t": 25.0,
            "eon": [(30, 1000e-6), (45, 1264e-6), (60, 1678e-6), (75, 2183e-6), (90, 2713e-6), (105, 3248e-6), (120, 3793e-6)],
            "eoff": [(30, 205e-6), (45, 251e-6), (60, 362e-6), (75, 515e-6), (90, 688e-6), (105, 876e-6), (120, 1084e-6)],
            # kv: Fig. 23 (1000 V) vs Fig. 24 (1200 V): E_on ratio 1.29 (kv 1.38-1.48, 1.40 at 75 A); E_off ratio 1.24-1.25 above 60 A (kv 1.19-1.22,
            # median 1.22; 1.38-1.58 at 30-45 A)
            "kv_on": 1.40, "kv_off": 1.22,
            # kt: Fig. 26 p10 (E vs T_J 25..175 C at V_DD 1200 V, I_D 75 A, R_G 2.5 ohm, V_GS -4/+18 V, same freewheeling body diode):
            # E_on 2248 2194 2159 2136 2116 2100 2094 uJ (25..175 C step 25) -> -6.9 % over 150 K, kt_on = -0.0005 /K (end-point fit -0.00046, least squares -0.00054;
            # NEGATIVE: turn-on energy FALLS with T_J);  E_off 510 516 528 544 561 579 599 uJ -> +17 %, kt_off = +0.0011 /K (end point 0.00116, least squares 0.00105).
            # Valid at 75 A only.
            "kt_on": 0.0, "kt_off": 0.0011,
            "qrr": 1066e-9, "qrr_i": 75.0, "qrr_t": 175.0,    # p5 Table 7: V_R 1200 V, V_GS -4 V, I_D 75 A, di/dt 6770 A/us, T_J 175 C (t_rr 28 ns, I_rrm 65 A); no 25 C value printed
            # V_SD at V_GS -4 V, I_SD 37.5 A: 3.8 V at 25 C, 3.3 V at 175 C (p5 Table 7).  The 175 C point is used (knee V_SD0 of the model is a hot value).
            "vsd": 3.3, "vsd_i": 37.5,
            "tf": 14e-9, "tf_i": 75.0,                        # p5 Table 6: t_f 14 ns at V_DS 1200 V, I_D 75 A, R_g 2.5 ohm, 25 C
            "idm": 347.0,                                     # p3 Table 2: pulsed I_D, t_p = 100 us limited by T_J,max (Fig. 22 p9); no body-diode surge rating printed (I_S 110 A continuous, p5)
            # Body-diode curve at V_GS = -4 V (the leftmost = highest-V_SD curve of the three; arrow check p7), (A, V):
            # Fig. 9 p7 (25 C) and Fig. 10 p7 (175 C).  Cross-check Table 7: 3.78 V / 3.33 V read at 37.5 A vs 3.8 / 3.3 V printed.
            # Figs. 13-15 p8 (3rd quadrant, V_GS 0..18 V) show the same family with the channel turned on.
            "body_iv_25": [(25, 3.55), (50, 3.98), (75, 4.33), (100, 4.62), (150, 5.12)],
            "body_iv_175": [(25, 3.13), (50, 3.50), (75, 3.79), (100, 4.04), (150, 4.45)],
            # E vs R_G: Fig. 25 p10, V_DD 1200 V, I_D 75 A, V_GS -4/+18 V, T_J 25 C.  NOTE: at its R_G = 2.5 ohm point the figure reads
            # E_on 2090 / E_off 578 uJ, i.e. -6 % / +15 % against Table 6 and Figs. 24/26 (E_total 2670 is within 3 %).
            "eon_rg": [(2.5, 2090e-6), (5.0, 2644e-6), (10.0, 3684e-6), (15.0, 4655e-6), (20.0, 5557e-6)],
            "eoff_rg": [(2.5, 578e-6), (5.0, 853e-6), (10.0, 1405e-6), (15.0, 1959e-6), (20.0, 2514e-6)],
            "check": [("on", 1200, 75, 25, 2229e-6), ("off", 1200, 75, 25, 501e-6)],   # p5 Table 6
            # extras
            "t_sc": None,            # MISSING: no short-circuit withstand time in the datasheet (Table 2 / Table 4 have none); the design must assume none
            "avalanche": None,       # MISSING: no E_AS / I_AS / UIS rating printed
            "qualification": "none stated: p1 lists only halogen-free / RoHS; p13 RoHS + REACH statements; p14 item 8 says to consult Sichain for high-reliability applications",
            "tj_max": 175.0,         # p3 Table 2: T_J, T_stg -55 to +175 C; P_D 535 W at T_C 25 C; I_D 110 A (25 C) / 78 A (100 C) at V_GS 18 V
        # Gate-0b design choices (sim/pv_design.py RAILS): off-state rail -3.5 V, inside Note 1/2 (p3/p5: -4 V is the most negative 
        # V_GS while the body diode conducts, as it does in every dead time); vgs_min -4 V for that reason (abs. max -8 V only without
        # body-diode conduction); kt_on 0 instead of the slightly negative Fig.26 value (conservative); E_off at -3.5 V vs the -4 V
        # test: eoff_vgs_factor (calibrated VDMOS, pv_tradeoff.vgs_factor; pv_design asserts it +-3 %)
    },  # data: sic17/dev_SG2M020170HJ.py (Gate-0b extraction)
    "SG2M040170HJ": {
            # Table 4 p4 (T_C 25 C): V_GS(th) min/typ/max 2.5/3.1/4.0 V at V_DS = V_GS, I_D = 12 mA; typ 2.4 V at 175 C (Fig. 11 p7 reads 3.25 / 2.45 V).
            # vth_175_min = 1.94 V is DERIVED = typ@175 C x (min25/typ25) = 2.4 x 2.5/3.1 = 1.935; the datasheet prints no minimum at 175 C.
            "vth": 3.1, "vth_min": 2.5, "vth_175_min": 1.94,
            "gfs": 23.0, "gfs_i": 38.0,                       # p4 Table 4: V_DS 20 V, I_D 38 A, 25 C typ (22 S at 175 C)
            "qgd": 24e-9, "qgd_v": 1200.0,                    # p5 Table 5: V_DS 1200 V, V_GS -4/+18 V, I_D 38 A
            "mfr": "Sichain", "src": SEMI + "SG2M040170HJ.pdf", "rev": "V01_02, 2025-12-30 (Final; p13 revision history: V01_00 2025-01-06, V01_01 2025-09-08)",
            # mfr long name (p1/p3 footer): Sichain Semiconductor (Ningbo) Co., Ltd. (Chinese name on the datasheet footer)
            "vdss": 1700, "package": "TO-247-4L (outline TO-247-4L-A, p11)", "isolated_tab": False, "gen": "TriQSiC G2",
            # p1 pin symbol: pin 1 = Drain and TAB (not isolated), pin 2 = Power Source, pin 3 = Driver (Kelvin) Source, pin 4 = Gate.
            # Gate drive: VGSop -4/+18 V recommended (p3 Table 2); the dynamic data (Table 6, Figs. 23-27) are at -4/+18 V too, so vgs_off_ds = vgs_off.
            # vgs_max/vgs_min = absolute static -8/+22 V (p3 Table 2; Note 1: with the body diode conducting V_GS must stay within -4/+22 V,
            # Note 2 p5: max recommended V_GS = -4 V while the body diode conducts).  Transient: -10/+25 V for t_p <= 0.5 us, duty < 0.01 (p3).
            "vgs_on": 18.0, "eoff_vgs_factor": 1.0985, "vgs_off": -3.5, "vgs_off_ds": -4.0,
            "vgs_max": 22.0, "vgs_min": -4.0, "vgs_max_tr": 25.0, "vgs_min_tr": -10.0,
            "rg_ext": 2.5, "rg_int": 1.4,                     # p5 Table 6 test R_g 2.5 ohm; p4 Table 4 R_g,int 1.4 ohm typ (1 MHz, 25 mV, open drain)
            "rds25": 40e-3, "rds25_max": 52e-3,               # p4 Table 4: V_GS 18 V, I_D 38 A, 25 C: typ 40 / max 52 mOhm (typ 47 / max 65 mOhm at 15 V; typ 88 mOhm at 175 C, no max)
            # Fig. 4 p6 (normalised R_DS(on), I_D 38 A, V_GS 18 V curve = the upper curve above 25 C; both curves cross at ~1.0 at 25 C, read 1.015, divided out):
            # 1.12/1.28/1.47/1.68/1.90/2.15; Fig. 6 p6 (absolute, V_GS 18 V) 40.5/45.4/51.8/59.6/68.1/77.4/87.4 mOhm -> 1.00/1.12/1.28/1.47/1.68/1.91/2.16.  Average used.
            # Table gives 88/40 = 2.20 at 175 C.
            "rds_norm": [(25, 1.00), (50, 1.12), (75, 1.28), (100, 1.47), (125, 1.68), (150, 1.91), (175, 2.16)],
            "rth_jc": 0.31, "rth_jc_max": 0.39, "zth": True,  # p3 Table 3 typ 0.31 / max 0.39 K/W; Zth(j-c) curve with duty cycles = Fig. 21 p9 (no Foster/Cauer values printed)
            "qg": 78e-9, "ciss": 2594e-12, "coss": 102e-12, "crss": 5e-12, "c_at": 1400.0,   # p4-5 Table 5: V_DS 1400 V, V_GS 0, 100 kHz, 25 mV (Q_g at V_DS 1200 V, -4/+18 V, 38 A)
            # Fig. 16 p8 (E_oss vs V_DS, 0..1700 V, read every 100 V); Table 5 p4 prints E_oss = 106 uJ with no test voltage of its own (the V_DS = 1400 V cell covers C_iss/C_oss/C_rss only);
            # the curve reads 104.3 uJ at 1400 V (-1.6 %), so 1400 V is implied
            "eoss": [(0, 0), (200, 5.3e-6), (400, 15.1e-6), (600, 28.0e-6), (800, 43.6e-6), (1000, 61.7e-6), (1200, 82.1e-6), (1400, 104.3e-6), (1700, 140.7e-6)],
            # E vs I: Fig. 24 p9, clamped inductive, V_DD 1200 V, V_GS -4/+18 V, R_G 2.5 ohm, T_J 25 C (the figure plots 20..70 A only).
            # Freewheeling device: the complementary MOSFET of the half-bridge dynamic test circuit (Fig. B p12: T1/T2 drawn as identical MOSFETs
            # with their body diodes, no external SBD) -> E_on INCLUDES the recovery of that body diode at the test T_J (25 C).  No separate
            # E_rr/E_fr is printed.  Table 6 p5 (V_DS 1200 V, V_GS -4/+18 V, I_D 38 A, R_g 2.5 ohm, L = 120 uH, T_C 25 C): E_on 722, E_off 147 uJ.
            # Fig. 24 raw readings (20 25 30 35 40 45 50 55 60 65 70 A): E_on 468 538 608 683 759 835 913 994 1073 1153 1233 uJ -> 728 uJ at 38 A (+0.9 % vs table);
            #   E_off 108 124 137 153 172 197 219 249 278 307 336 uJ -> 165 uJ at 38 A = +12 % ABOVE Table 6 (147), Fig. 25 (147) and Fig. 26 (149).
            # E_off is therefore multiplied by 0.894 (= 147/164.5): shape from Fig. 24, level from the table (same practice as the IMYH200R0xxM1H E_fr / E_on scaling
            # in pv_devices.py).  CONSERVATIVE ALTERNATIVE: drop the factor (E_off at 38 A = +12 %: e_sw interpolating these 10 A spaced points then reads +12.2 % and the 12 % self-check FAILS by 0.2 points).
            # Fig. 23 p9 (same, V_DD 1000 V) read for kv: E_on(20 30 40 50 60 70 A) = 356 463 578 704 836 971 uJ;  E_off = 88 106 139 182 231 285 uJ (raw, unscaled).
            "sw_v": 1200.0, "sw_t": 25.0,
            "eon": [(20, 468e-6), (30, 608e-6), (40, 759e-6), (50, 913e-6), (60, 1073e-6), (70, 1233e-6)],
            "eoff": [(20, 108e-6 * 0.894), (30, 137e-6 * 0.894), (40, 172e-6 * 0.894), (50, 219e-6 * 0.894), (60, 278e-6 * 0.894), (70, 336e-6 * 0.894)],
            # kv: Fig. 23 (1000 V) vs Fig. 24 (1200 V): E_on ratio 1.27-1.32 (kv 1.31 at 70 A ... 1.50 at 20-40 A, mean 1.45); E_off ratio 1.18-1.29 (kv 0.9-1.4, mean 1.15, 1.26 at 38 A)
            "kv_on": 1.45, "kv_off": 1.15,
            # kt: Fig. 26 p10 (E vs T_J 25..175 C at V_DD 1200 V, I_D 38 A, R_G 2.5 ohm, V_GS -4/+18 V, same freewheeling body diode):
            # E_on 723 715 705 695 689 684 680 uJ (25..175 C step 25) -> -5.9 % over 150 K, kt_on = -0.0004 /K (end-point fit -0.00039, least squares -0.00044;
            # NEGATIVE: turn-on energy FALLS with T_J);  E_off 149 149 150 151 153 154 155 uJ -> +3.5 %, kt_off = +0.0002 /K.  Valid at 38 A only.
            "kt_on": 0.0, "kt_off": 0.0002,
            "qrr": 840e-9, "qrr_i": 38.0, "qrr_t": 175.0,     # p5 Table 7: V_R 1200 V, V_GS -4 V, I_SD 38 A, di/dt 2245 A/us, T_J 175 C (t_rr 48 ns, I_rrm 28 A); no 25 C value printed
            # V_SD at V_GS -4 V, I_SD 19 A: 3.7 V at 25 C, 3.2 V at 175 C (p5 Table 7).  The 175 C point is used (knee V_SD0 of the model is a hot value).
            "vsd": 3.2, "vsd_i": 19.0,
            "tf": 11e-9, "tf_i": 38.0,                        # p5 Table 6: t_f 11 ns at V_DS 1200 V, I_D 38 A, R_g 2.5 ohm, 25 C
            "idm": 188.0,                                     # p3 Table 2: pulsed I_D, t_p = 100 us limited by T_J,max (Fig. 22 p9); no body-diode surge rating printed (I_S 66 A continuous, p5)
            # Body-diode curve at V_GS = -4 V (the leftmost = highest-V_SD curve of the three; arrow check p7), (A, V):
            # Fig. 9 p7 (25 C) and Fig. 10 p7 (175 C).  Cross-check Table 7: 3.75 V / 3.28 V read at 19 A vs 3.7 / 3.2 V printed.
            # Figs. 13-15 p8 (3rd quadrant, V_GS 0..18 V) show the same family with the channel turned on.
            "body_iv_25": [(20, 3.77), (40, 4.32), (60, 4.78), (100, 5.49), (130, 5.96)],
            "body_iv_175": [(20, 3.30), (40, 3.76), (60, 4.13), (100, 4.69), (130, 5.05)],
            # E vs R_G: Fig. 25 p10, V_DD 1200 V, I_D 38 A, V_GS -4/+18 V, T_J 25 C, same half-bridge test circuit (Fig. B p12; E_on includes the complementary body-diode
            # recovery); at R_G = 2.5 ohm it reads E_on 723 / E_off 147 uJ = Table 6 (722 / 147).
            "eon_rg": [(2.5, 723e-6), (5.0, 914e-6), (10.0, 1267e-6), (15.0, 1582e-6), (20.0, 1858e-6)],
            "eoff_rg": [(2.5, 147e-6), (5.0, 213e-6), (10.0, 344e-6), (15.0, 475e-6), (20.0, 607e-6)],
            "check": [("on", 1200, 38, 25, 722e-6), ("off", 1200, 38, 25, 147e-6)],   # p5 Table 6
            # extras
            "t_sc": None,            # MISSING: no short-circuit withstand time in the datasheet (Table 2 / Table 4 have none); the design must assume none
            "avalanche": None,       # MISSING: no E_AS / I_AS / UIS rating printed
            "qualification": "none stated: p1 lists only halogen-free / RoHS; p13 RoHS + REACH statements; p14 item 8 says to consult Sichain for high-reliability applications",
            "tj_max": 175.0,         # p3 Table 2: T_J, T_stg -55 to +175 C; P_D 384 W at T_C 25 C; I_D 66 A (25 C) / 47 A (100 C) at V_GS 18 V
        # Gate-0b design choices (sim/pv_design.py RAILS): off-state rail -3.5 V, inside Note 1/2 (p3/p5: -4 V is the most negative 
        # V_GS while the body diode conducts, as it does in every dead time); vgs_min -4 V for that reason (abs. max -8 V only without
        # body-diode conduction); kt_on 0 instead of the slightly negative Fig.26 value (conservative); E_off at -3.5 V vs the -4 V
        # test: eoff_vgs_factor (calibrated VDMOS, pv_tradeoff.vgs_factor; pv_design asserts it +-3 %)
    },  # data: sic17/dev_SG2M040170HJ.py (Gate-0b extraction)
    "IV3Q12035T4Z": {
            "vth": 2.8, "vth_min": 2.0, "vth_175_min": 1.43,          # p2: V_TH min/typ 2.0/2.8 V (V_GS=V_DS, 8 mA, 25 C), typ 2.0 V at 175 C (Fig.9 p4 agrees);
                                                                      # vth_175_min DERIVED = 2.0 (typ@175) x 2.0/2.8 (min25/typ25), the datasheet prints no min@175
            "gfs": None, "gfs_i": 40.0,  # ASSUMPTION (Gate-0b): g_fs missing -> VDMOS +4 V plateau at the 40 A switching-test current, as for MSC035;                                # MISSING p2: no g_fs row.  Info only (not a datasheet value): Fig.8 p4 slope ~23 S at 40 A, 25 C, V_DS 20 V
            "qgd": 21e-9, "qgd_v": 800.0,                              # p2: 800 V, I_D 30 A, V_GS -3..18 V
            "mfr": "InventChip", "src": SEMI + "IV3Q12035T4Z.pdf", "rev": "Rev 1.1, Aug. 2026",
            "vdss": 1200, "package": "TO-247-4", "isolated_tab": False, "gen": "Gen3 (InventChip 3rd-gen SiC MOSFET, p1)",
            # gate drive p1: V_GSon recommended 15...18 V, V_GSoff recommended -5...-2 V "typical -3.5 V"; every dynamic number (p2, Fig.19-22) is at -3.5/+18 V.
            # 18 V chosen: R_ON max (43.8 mOhm) and all switching data are at 18 V (15 V: 45 mOhm typ, +29 %).
            "vgs_on": 18.0, "vgs_off": -3.5, "vgs_off_ds": -3.5,
            "vgs_max": 20.0, "vgs_min": -7.0,                          # p1 V_GSmax(DC) "-7 to 20 V", static
            "vgs_max_tr": 23.0, "vgs_min_tr": -10.0, "vgs_tr_cond": "duty cycle < 1 %, pulse width < 200 ns",   # p1 V_GSmax (transient) -10/+23 V
            "rg_ext": 2.0, "rg_int": 2.5,                              # p2: test R_G(ext) 2.0 ohm; R_g gate input resistance 2.5 ohm at 1 MHz
            "rds25": 35e-3, "rds25_max": 43.8e-3,                      # p2: V_GS 18 V, I_D 30 A, 25 C (typ / max); 15 V: 45 mOhm typ.  Hot typ p2: 57 mOhm (175 C, 18 V)
            # Fig.5 p4 (normalised, V_GS=18 V, I_DS=30 A) read 1.036/1.106/1.203/1.317/1.468; Fig.4 p3 (absolute, 34.7 mOhm at 25 C ->
            # 35.9/38.4/41.6/45.8/51.2/57.0) gives 1.035/1.107/1.199/1.32/1.476/1.643; 175 C point = p2 table 57/35 = 1.629 (Fig.5 reads 1.645)
            "rds_norm": [(25, 1.00), (50, 1.035), (75, 1.105), (100, 1.20), (125, 1.315), (150, 1.47), (175, 1.63)],
            "rth_jc": 0.52, "rth_jc_max": 0.52,                        # p2: one value "0.52 C/W", not labelled typ/max -> used for both (Fig.25 p7 saturates at ~0.5 C/W)
            "zth": True,                                               # Fig.25 p7: Z_thJC vs t_p for D = 0.5/0.3/0.1/0.05/0.02/0.01 and single pulse
            "qg": 83e-9, "ciss": 2082e-12, "coss": 95e-12, "crss": 3e-12, "c_at": 800.0,   # p2: V_DS 800 V, V_GS 0, 100 kHz, 25 mV (Q_g at 800 V, 30 A, -3..18 V; Q_gs 23.5 nC)
            # E_oss: Fig.17 p6 (table p2: 40 uJ at 800 V).  Curve ends at 1000 V (axis end); the model extrapolates linearly beyond (1200 V is NOT plotted)
            "eoss": [(0, 0), (100, 1.8e-6), (200, 5.0e-6), (300, 9.3e-6), (400, 14.3e-6), (500, 20.0e-6), (600, 26.3e-6), (700, 33.3e-6),
                     (800, 40.7e-6), (900, 48.6e-6), (1000, 57.0e-6)],
            "sw_v": 800.0, "sw_t": 25.0,                               # Fig.21 p6: 800 V, 25 C, R_G(ext) 2 ohm, -3.5/+18 V, L 200 uH, FWD = body diode of the same type
            "eon": [(20, 284e-6), (30, 342e-6), (40, 408e-6), (50, 474e-6), (60, 539e-6), (70, 609e-6), (80, 695e-6), (90, 817e-6)],       # Fig.21 E_ON
            "eoff": [(20, 35e-6), (30, 34.5e-6), (40, 43e-6), (50, 60e-6), (60, 85e-6), (70, 113e-6), (80, 141e-6), (90, 162e-6)],        # Fig.21 E_OFF
            "kv_on": 1.6, "kv_off": 0.8,        # ASSUMPTION (MSC035 values): MISSING - every curve and table value is at V_DD = 800 V, no 2nd voltage / E-vs-V figure
            "kt_on": 0.003656, "kt_off": 0.001454,   # chord 25 -> 175 C, p2 table at 40 A / 800 V / 2 ohm: E_on 407.6 -> 631.1 uJ, E_off 43.1 -> 52.5 uJ; Fig.22 p6 agrees at both ends
                                                      # (Fig.22 is convex: E_on 407/423/440/458/500/560/628 uJ at 25/50/75/100/125/150/175 C -> chord is ~13 % high at 100 C, ~11 % at 125 C, ~6 % at 150 C)
            "qrr": 239.1e-9, "qrr_i": 40.0, "qrr_t": 25.0,             # p3: V_GS -3.5/+18 V, I_SD 40 A, V_R 800 V, R_G(ext) 13.5 ohm, L 200 uH, di/dt 3000 A/us, T_C 25 C per table header;
                                                                       # t_rr 25.21 ns, I_RRM 25.1 A.  No hot value given.
            "vsd": 4.93, "vsd_i": 30.0,                                # DERIVED at V_GS -3.5 V, 25 C, 30 A from Fig.11 p5: -2 V curve 4.78 V, -5 V curve 5.08 V, linear interpolation to -3.5 V.
                                                                       # p3 table gives only V_GS = 0 V: 4.3 V (25 C) / 4.0 V (175 C) at 30 A - Fig.11 0 V curve reads 4.32 V (matches).
            "tf": 6.4e-9, "tf_i": 40.0,                                # p2: 800 V, 40 A, R_G(ext) 2 ohm, 25 C
            "idm": 167.0,                                              # p1: I_DM pulsed 167 A, "pulse width limited by SOA" (Fig.25/26); body diode I_SM 167 A pulsed
            # 3rd-quadrant body-diode curve at V_GS -3.5 V (A, V): -2 V and -5 V curves of Fig.11 (25 C) and Fig.12 (175 C), p5, linearly interpolated to -3.5 V.
            # Fig.11: V(-2 V)/V(-5 V) = 4.33/4.67 (20 A) 5.15/5.42 (40 A) 5.75/5.97 (60 A) 6.25/6.46 (80 A) 6.58/6.72 (100 A).
            # Fig.12: V(-2 V)/V(-5 V) = 3.87/4.16 (20 A) 4.58/4.85 (40 A) ~5.10/5.33 (60 A) 5.49/5.70 (80 A) 5.80/6.01 (100 A); the -2 V and 0 V curves cross at ~60 A, so the
            # -2 V curve above 60 A is the mean of the two inner curves (+-0.1 V).  Interpolation uncertainty +-0.1 V (V_SD saturates towards the -5 V curve).
            "body_iv_25": [(20, 4.50), (40, 5.29), (60, 5.86), (80, 6.36), (100, 6.65)],
            "body_iv_175": [(20, 4.02), (40, 4.72), (60, 5.21), (80, 5.60), (100, 5.90)],   # 175 C (the hot curve printed is 175 C, not 150 C)
            "eon_rg": [(2.0, 407.6e-6), (3.0, 432e-6), (5.0, 527e-6), (8.0, 614e-6), (10.0, 673e-6), (12.0, 738e-6), (16.0, 876e-6), (20.0, 1019e-6)],   # Fig.19 p6 (800 V, 40 A, 25 C);
            "eoff_rg": [(2.0, 43.1e-6), (3.0, 58e-6), (5.0, 78e-6), (8.0, 131e-6), (10.0, 164e-6), (12.0, 184e-6), (16.0, 223e-6), (20.0, 282e-6)],      # R_G axis starts at 3 ohm, so the 2 ohm point is the p2 table value (same V, I, Tj); read +-3 %
            "check": [("on", 800, 40, 25, 407.6e-6), ("off", 800, 40, 25, 43.1e-6),
                      ("on", 800, 40, 175, 631.1e-6), ("off", 800, 40, 175, 52.5e-6)],   # p2 table, 800 V, 40 A, 2 ohm, -3.5/+18 V
            # extra keys
            "t_sc": None,                                              # MISSING: no short-circuit withstand in the datasheet. ASSUMPTION: no SC withstand time may be relied on
            "qualification": "AEC-Q101 qualification (p1 Features list; title 'Automotive SiC MOSFET'); a claim only, no report or test grade quoted",
            "avalanche": None,                                         # MISSING: no E_AS / avalanche rating in the datasheet (p1 notes only: 'Surge current data available on request')
            "tj_max": 175.0,                                           # p1: T_J -55...175 C
            "e_on_includes_rr": "not stated; FWD = body diode of the same type (Fig.19/21/22 legends) so physically yes",
    },  # data: s12b/dev_IV3Q12035T4Z.py (Gate-0b extraction)
    "B3M040120Z": {
            "vth": 2.7, "vth_min": 2.3, "vth_175_min": 1.62,          # p2: V_GS(th) min/typ/max 2.3/2.7/3.5 V (V_GS=V_DS, 10 mA); typ 1.9 V at 175 C (Fig.4 p6 reads ~1.85 V);
                                                                      # vth_175_min DERIVED = 1.9 (typ@175) x 2.3/2.7 (min25/typ25); the datasheet prints no min@175
            "gfs": 16.0, "gfs_i": 40.0,                                # p2: 16 S typ, V_DS = 10 V, I_D = 40 A, 25 C
            "qgd": 39e-9, "qgd_v": 800.0,                              # p3: V_DS 800 V, I_D 40 A, V_GS -4/+18 V (Q_gs 27 nC)
            "mfr": "BASiC Semiconductor", "src": SEMI + "B3M040120Z.pdf", "rev": "Rev. 0.2, 2025-12-06",
            "vdss": 1200, "package": "TO-247-4", "isolated_tab": False, "gen": "B3M series (generation not stated in the datasheet)",
            # p2: V_GSop "Recommended Gate-Source Voltage" -4/18 V; the switching tests (p4) are at -4/+18 V
            "vgs_on": 18.0, "vgs_off": -4.0, "vgs_off_ds": -4.0,
            "vgs_max": 22.0, "vgs_min": -10.0,                         # p2: V_GSmax -10/22 V, printed with NO test condition (no static/transient split)
            "vgs_max_tr": None, "vgs_min_tr": None,                    # MISSING: no transient / pulse-width rating.  ASSUMPTION: -10/+22 V is also the limit for spikes (no overshoot margin beyond it)
            "rg_ext": 8.2, "rg_int": 1.3,                              # p4 test R_G(ext) = 8.2 ohm; p3 R_G(int) 1.3 ohm (f 1 MHz, 25 mV)
            "rds25": 40e-3, "rds25_max": 55e-3,                        # p2: V_GS 18 V, I_D 40 A, 25 C typ / max.  Hot p2: 75 mOhm typ (175 C); 15 V: 50 mOhm typ.
                                                                       # Fig.6/7 p7 plot 41.8 mOhm at 25 C / 76.3 mOhm at 175 C (18 V, 40 A): +4.5 % / +1.7 % above the table typ.
            # Fig.5 p7 (normalised, V_GS = 18 V, I_D = 40 A, 25...175 C only; read 1.003/1.098/1.225/1.391/1.600/1.828 at the gridlines)
            "rds_norm": [(25, 1.00), (50, 1.01), (75, 1.10), (100, 1.23), (125, 1.39), (150, 1.60), (175, 1.83)],      # table ratio 75/40 = 1.875 at 175 C (Fig.5 is 2.4 % lower, see GAPS)
            "rth_jc": 0.48, "rth_jc_max": 0.70,                        # p3: R_th(jc) typ 0.48, max 0.70 K/W
            "zth": True,                                               # Fig.26 p12: Z_thjc vs t_p, D = 0.5...0.01 and single pulse
            "qg": 85e-9, "ciss": 1870e-12, "coss": 82e-12, "crss": 6e-12, "c_at": 800.0,   # p3: V_DS 800 V, V_GS 0, 100 kHz, 25 mV; Q_g at 800 V, 40 A, -4/+18 V.  Also C_o(er) 105 pF, C_o(tr) 157 pF (0..800 V)
            # E_oss: Fig.13 p9 (table p3: 33 uJ at 800 V).  The curve ends at 1000 V (axis end); the model extrapolates linearly beyond (1200 V NOT plotted)
            "eoss": [(0, 0), (100, 1.4e-6), (200, 4.0e-6), (300, 7.5e-6), (400, 11.7e-6), (500, 16.4e-6), (600, 21.7e-6), (700, 27.6e-6),
                     (800, 33.9e-6), (900, 40.8e-6), (1000, 48.6e-6)],
            "sw_v": 800.0, "sw_t": 25.0,                               # Fig.19 p10: 800 V, 25 C, R_G(ext) 8.2 ohm, -4/+18 V, L_sigma 50 nH, FWD = body diode (legend 'B3M040120Z')
            "eon": [(10, 224e-6), (20, 357e-6), (30, 502e-6), (40, 654e-6), (50, 830e-6), (60, 1021e-6), (70, 1226e-6), (80, 1458e-6)],    # Fig.19 E_on (includes FWD recovery)
            "eoff": [(10, 34e-6), (20, 60e-6), (30, 109e-6), (40, 171e-6), (50, 253e-6), (60, 345e-6), (70, 439e-6), (80, 551e-6)],       # Fig.19 E_off
            # kv: log-log fit of Fig.19 (800 V) against Fig.17 (600 V), both 25 C: E_on ratio 1.55...1.60 (mean exponent 1.57 over 20-80 A), E_off 0.67...0.89 (mean 0.80 over 30-80 A).
            # At 175 C (Fig.20 vs Fig.18): 1.76 / 0.86 - the exponent grows with Tj, the model carries one value (25 C, the sw_t reference).
            "kv_on": 1.57, "kv_off": 0.80,
            # kt: p4 table, 40 A / 800 V / 8.2 ohm, body-diode FWD: E_on 650 -> 860 uJ (25 -> 175 C), E_off 170 -> 170 uJ; Fig.19 vs Fig.20 gives 0.0021 at 40 A, 0.0017...0.0021 over 20-80 A;
            # Fig.25 p12 body-diode E_on: 650/660/690/741/783/825/860 uJ at 25/50/75/100/125/150/175 C (near-linear above 50 C).
            # With the SiC Schottky FWD (B3D20120H, p4): E_on 580 -> 460 uJ (kt = -0.00138), E_off 170 -> 170 uJ - NOT used here.
            "kt_on": 0.00215, "kt_off": 0.0,
            # Q_rr: p5 gives two points.  25 C: 187 nC (V_R 800 V, I_SD 40 A, di/dt 2800 A/us, t_rr 26 ns, I_rrm 17 A); 175 C: 753 nC (3500 A/us, t_rr 28 ns, I_rrm 39 A).
            # x4.0 rise (the model's _qrr_shape assumes x2): anchored at the HOT point so the 100-175 C range is right (25 C then reads 2x high, conservative).
            "qrr": 753e-9, "qrr_i": 40.0, "qrr_t": 175.0,
            "vsd": 5.0, "vsd_i": 20.0,                                 # p5: V_SD 5.0 V typ at V_GS = -4 V, I_SD = 20 A, 25 C (175 C: 4.3 V) - off-state V_GS is the datasheet's own condition
            "tf": 10e-9, "tf_i": 40.0,                                 # p4: t_f 10 ns (25 C), 9 ns (175 C), 800 V, 40 A, 8.2 ohm
            "idm": 124.0,                                              # p2: I_D,pulse 124 A (t_p limited by T_jmax); body diode I_SD,pulse 125 A (p5)
            # body-diode curves at V_GS = -4 V (the recommended off-state, a plotted curve - no interpolation): Fig.9 p8 (25 C), Fig.10 p8 (175 C); (A, V).  Table checks: 5.0 V / 4.3 V at 20 A.
            "body_iv_25": [(10, 4.34), (20, 5.04), (40, 5.97), (60, 6.66), (100, 7.66)],
            "body_iv_175": [(10, 3.67), (20, 4.28), (40, 5.10), (60, 5.73), (100, 6.69)],    # the hot curve printed is 175 C, not 150 C
            # Fig.21 p11 (800 V, 40 A, 25 C, FWD = body diode): R_G(ext) 5...20 ohm; at 8.2 ohm the figure reads 649 / 168 uJ (table 650 / 170)
            "eon_rg": [(5.0, 503e-6), (8.2, 649e-6), (10.0, 721e-6), (15.0, 925e-6), (20.0, 1126e-6)],
            "eoff_rg": [(5.0, 119e-6), (8.2, 168e-6), (10.0, 198e-6), (15.0, 265e-6), (20.0, 310e-6)],
            "check": [("on", 800, 40, 25, 650e-6), ("off", 800, 40, 25, 170e-6),
                      ("on", 800, 40, 175, 860e-6), ("off", 800, 40, 175, 170e-6)],   # p4 table, body-diode FWD rows, 800 V, 40 A, 8.2 ohm, -4/+18 V
            # extra keys
            "t_sc": None,                                              # MISSING: no short-circuit withstand in the datasheet. ASSUMPTION: no SC withstand time may be relied on
            "qualification": None,                                     # MISSING: no AEC / JEDEC qualification statement in the datasheet (p1 lists 'Avalanche Ruggedness', 'Halogen Free, RoHS')
            "avalanche": {"E_AS": 324e-3, "cond": "T_C 25 C, L = 2 mH, I_AS = 18 A, V_DD = 140 V (p2)"},   # single pulse, 25 C only; p1 Features 'Avalanche Ruggedness'
            "tj_max": 175.0,                                           # p2: T_j -55...175 C
            "e_on_includes_rr": "yes - p4 prints 'Eon includes diode reverse recovery' for the body-diode FWD rows",
    },  # data: s12b/dev_B3M040120Z.py (Gate-0b extraction)
    "SG2M014120LJ": {
            "vth": 2.8, "vth_min": 2.3, "vth_175_min": 1.81,          # p4: V_GS(th) min/typ/max 2.3/2.8/3.6 V (V_DS=V_GS, 26 mA); typ 2.2 V at 175 C (Fig.11 p7 reads 2.28 V);
                                                                      # vth_175_min DERIVED = 2.2 (typ@175) x 2.3/2.8 (min25/typ25); the datasheet prints no min@175
            "gfs": 62.0, "gfs_i": 90.0,                                # p4: 62 S typ, V_DS = 20 V, I_D = 90 A, 25 C (58 S at 175 C)
            "qgd": 42e-9, "qgd_v": 800.0,                              # p5: V_DS 800 V, I_D 90 A, V_GS -4/+18 V (Q_gs 66 nC, Q_g 211 nC)
            "mfr": "Sichain (Ningbo)", "src": SEMI + "SG2M014120LJ.pdf", "rev": "V02_04, 2025.12.25 (Final)",
            "vdss": 1200, "package": "TO-247-4L (TO-247-4L-C drawing p11)", "isolated_tab": False, "gen": "TriQSiC G2",
            # p3 Table 2: V_GSop "Recommended operational values" -4/+18 V; the switching tests (p5, Fig.23-27) are at -4/+18 V
            "vgs_on": 18.0, "vgs_off": -4.0, "vgs_off_ds": -4.0,
            "vgs_max": 22.0, "vgs_min": -8.0,                          # p3: V_GS,max -8/+22 V "absolute maximum values".  Note 1 (p3): when the body diode conducts V_GS,max = -4/+22 V;
                                                                       # note 2 (p5): with the SiC body diode conducting the maximum recommended V_GS is -4 V -> the -4 V off-state is AT the body-diode limit
            "vgs_max_tr": 25.0, "vgs_min_tr": -10.0, "vgs_tr_cond": "t_p <= 0.5 us, D < 0.01",   # p3 V_GSpulse -10/+25 V
            "rg_ext": 2.5, "rg_int": 2.6,                              # p5 test R_g = 2.5 ohm; p4 R_g,int 2.6 ohm (25 mV, 1 MHz, open drain)
            "rds25": 13e-3, "rds25_max": 18e-3,                        # p4: V_GS 18 V, I_D 90 A, 25 C typ / max.  Hot p4: 23.7 mOhm typ (175 C); 15 V: 15 / 21 mOhm, 24.6 mOhm hot
            # Fig.4 p6 (normalised, I_D = 90 A, V_GS = 18 V = the curve that ends highest): 1.076/1.182/1.310/1.462/1.638/1.827 at 50...175 C; table 23.7/13 = 1.823
            "rds_norm": [(25, 1.00), (50, 1.076), (75, 1.18), (100, 1.31), (125, 1.46), (150, 1.64), (175, 1.83)],
            "rth_jc": 0.21, "rth_jc_max": 0.26,                        # p3 Table 3: typ 0.21, max 0.26 C/W
            "zth": True,                                               # Fig.21 p9: Z_th(j-c) vs t_p, D = 0.5...0.01 and single pulse (saturates at ~0.2 C/W)
            "qg": 211e-9, "ciss": 5220e-12, "coss": 262e-12, "crss": 12e-12, "c_at": 1000.0,   # p4 Table 5: capacitances at V_DS = 1000 V (NOT 800 V), V_GS 0, 100 kHz, 25 mV, 25 C; Q_g at 800 V, 90 A
            # E_oss: Fig.16 p8 (table p4: 142 uJ, in the same row group as the 1000 V capacitances; the curve reads 142.5 uJ at 1000 V)
            "eoss": [(0, 0), (100, 4.6e-6), (200, 13.0e-6), (400, 36.2e-6), (600, 66.2e-6), (800, 101.7e-6), (1000, 142.5e-6), (1200, 188.6e-6)],
            "sw_v": 800.0, "sw_t": 25.0,                               # Fig.24 p9: 800 V, 25 C, R_G 2.5 ohm, -4/+18 V (no FWD / L printed in the legend; Table 6: L 16.7 uH)
            "eon": [(10, 334e-6), (20, 412e-6), (30, 505e-6), (40, 604e-6), (50, 707e-6), (60, 808e-6), (70, 906e-6), (90, 1106e-6), (100, 1208e-6), (120, 1421e-6), (140, 1647e-6)],     # Fig.24 E_on
            "eoff": [(10, 28e-6), (20, 55e-6), (30, 96e-6), (40, 145e-6), (50, 210e-6), (60, 285e-6), (70, 365e-6), (90, 541e-6), (100, 640e-6), (120, 855e-6), (140, 1094e-6)],          # Fig.24 E_off
            # kv: log-log of Fig.24 (800 V) against Fig.23 (600 V), both 25 C: E_on 1.60 (30 A) falling to 1.33 (140 A), 1.45 at 90 A and as 30-140 A mean;
            # E_off 1.15...1.34, mean 1.23 over 40-140 A, 1.26 at 90 A.  (The 80 A column of Fig.23/24 is hidden by the curve labels and was not read.)
            "kv_on": 1.45, "kv_off": 1.25,
            # kt: Fig.26 p10 (800 V, 90 A, 2.5 ohm): E_on 1104/1088/1076/1074/1063/1063/1063 uJ and E_off 539/557/570/580/600/614/627 uJ at 25/50/75/100/125/150/175 C
            # -> E_on falls 3.7 % (kt_on = -0.000248 /K), E_off rises 16 % (kt_off = +0.001088 /K).  The table (p5) has the 25 C point only (1103 / 537 uJ).
            "kt_on": -0.000248, "kt_off": 0.001088,
            # Q_rr: p5 Table 7 gives the 175 C point only: 1349 nC at V_R 800 V, V_GS -4 V, I_SD 90 A, di/dt 4279 A/us, t_rr 34 ns, I_rrm 68 A.  No 25 C value.
            "qrr": 1349e-9, "qrr_i": 90.0, "qrr_t": 175.0,
            "vsd": 3.9, "vsd_i": 45.0,                                 # p5 Table 7: V_SD 3.9 V typ at V_GS = -4 V, I_SD = 45 A, 25 C (3.5 V at 175 C)
            "tf": 13e-9, "tf_i": 90.0,                                 # p5 Table 6: t_f 13 ns, 800 V, 90 A, 2.5 ohm, 25 C
            "idm": 491.0,                                              # p3: I_D(pulse) 491 A, t_p = 100 us, limited by T_J,max (Fig.22); no body-diode pulsed rating given (I_S = 128 A continuous, p5)
            # body-diode curves at V_GS = -4 V (the recommended off-state, a plotted curve - no interpolation; it is the leftmost of the three curves in Fig.9/10): Fig.9 p7 (25 C), Fig.10 p7 (175 C); (A, V)
            # Table checks: 3.9 V at 45 A (Fig.9 reads 3.87), 3.5 V at 45 A and 175 C (Fig.10 reads 3.52).
            "body_iv_25": [(20, 3.46), (45, 3.87), (90, 4.54), (150, 5.19), (210, 5.74)],
            "body_iv_175": [(20, 3.06), (45, 3.52), (90, 4.03), (150, 4.64), (210, 5.15)],     # the hot curve printed is 175 C, not 150 C
            # Fig.25 p10 (800 V, 90 A, 25 C): at 2.5 ohm the figure reads 1101 / 544 uJ (table 1103 / 537)
            "eon_rg": [(2.5, 1101e-6), (5.0, 1525e-6), (7.5, 1868e-6), (10.0, 2155e-6), (15.0, 2731e-6), (20.0, 3299e-6)],
            "eoff_rg": [(2.5, 544e-6), (5.0, 844e-6), (7.5, 1121e-6), (10.0, 1375e-6), (15.0, 1880e-6), (20.0, 2386e-6)],
            "check": [("on", 800, 90, 25, 1103e-6), ("off", 800, 90, 25, 537e-6)],   # p5 Table 6, 800 V, 90 A, 2.5 ohm, -4/+18 V, L 16.7 uH (25 C only; no hot table value)
            # extra keys
            "t_sc": None,                                              # MISSING: no short-circuit withstand in the datasheet. ASSUMPTION: no SC withstand time may be relied on
            "qualification": None,                                     # MISSING: no AEC / JEDEC statement.  p14 note 8 only asks customers in high-reliability uses (transportation, medical, ...) to consult Sichain
            "avalanche": None,                                         # MISSING: no E_AS / avalanche rating
            "tj_max": 175.0,                                           # p1 Table 1 / p3: T_J -55...+175 C
            "e_on_includes_rr": "FWD not stated in words; Fig.B p12 shows a MOSFET half-bridge (body-diode FWD) - but see GAPS on E_on(Tj)",
    },  # data: s12b/dev_SG2M014120LJ.py (Gate-0b extraction)
    "CAB011M12FM3": {
        "vth": 2.5, "gfs": 73.0, "gfs_i": 100.0, "qgd": 100e-9, "qgd_v": 800.0,  # p2
        "mfr": "Wolfspeed", "src": SEMI + "CAB011M12FM3.pdf", "rev": "Rev. 6, Jun 2026",
        "vdss": 1200, "package": "FM3 half-bridge module (isolated, pre-applied TIM)", "isolated_tab": True, "gen": "Gen3",
        "half_bridge_module": True,
        "vgs_on": 15.0, "vgs_off": -4.0, "rg_ext": 1.0, "rg_int": 3.2,          # p2
        "rds25": 10.5e-3 + 2.2e-3, "rds25_max": 14.4e-3 + 2.2e-3,               # p2 device + p3 package resistance (2.23/2.06 mOhm)
        "rds_norm": [(25, 1.00), (100, 1.18), (125, 1.31), (150, 1.46), (175, 1.63)],  # Fig.2 p4 at 50 A (device); package R kept proportional (small)
        "rth_jc": 0.428, "rth_includes_tim": True,                              # p2 Rth junction-heatsink with pre-applied TIM
        "qg": 324e-9, "ciss": 10.3e-9, "coss": 0.39e-9, "crss": 30e-12, "c_at": 800.0,
        "eoss_ref": (800.0, 130e-6), "eoss_k": 1.6,  # ESTIMATE: E_oss not tabulated; 0.5*1.1*C_oss(800V)*V^2 (C_o(er)/C_oss=1.1 as C3M0016120K)
        "sw_v": 600.0, "sw_t": 25.0,                                            # Fig.11 p5: 600 V, 25 C, RG 1 ohm
        "eon": [(25, 0.28e-3), (50, 0.63e-3), (100, 1.30e-3), (150, 1.88e-3), (200, 2.40e-3)],
        "eoff": [(25, 0.20e-3), (50, 0.33e-3), (100, 0.73e-3), (150, 1.25e-3), (200, 1.85e-3)],
        "err": [(25, 0.08e-3), (100, 0.17e-3), (200, 0.33e-3)],
        "kv_on": 0.80, "kv_off": 0.50, "kv_rr": 0.30,  # Fig.11 (600 V) vs Fig.12 (800 V): E_on 0.6-1.0, E_off 0.29 (50 A)-0.65 (100 A), E_RR 0.2-0.4
        "kt_on": 0.00094, "kt_off": 0.0, "kt_rr": 0.0230,  # Fig.13 (Eon 1.28 -> 1.43 mJ 25 -> 150 C, Eoff flat), Fig.14 (E_RR 0.165 -> 0.64 mJ)
        "vsd": 4.7, "vsd_i": 100.0,                                             # p3, 150 C
        "didt_off": [(10, 1.0e9), (50, 4.5e9), (100, 8.0e9), (200, 16.5e9)],  # Fig.25 p8, 600 V 25 C RG 1 ohm (A/s)
        "lstray": 11.4e-9,                                                      # p3
        "check": [("on", 600, 100, 150, 1.4e-3), ("off", 600, 100, 150, 0.71e-3), ("rr", 600, 100, 150, 0.64e-3)],
    },
}

# Terrestrial-neutron single-event-burnout data.  Wolfspeed "Designed to Last..." (Nov 2025), Fig.4 p7:
# sea-level FIT/cm2 vs drain voltage for Gen3 1200 V (the C3M generation used here).
COSMIC_GEN3_1200 = {
    "src": SEMI + "Wolfspeed_Designed_to_Last_Even_in_the_Harshest_Environments.pdf, Fig. 4 p7",
    "v_fit_cm2": [(640, 0.01), (680, 0.1), (720, 0.4), (775, 2.8), (830, 10.0), (900, 30.0), (950, 70.0), (1000, 150.0), (1100, 450.0)],
}
# Design rule applied to every device (scaled by V_DSS because no 1.7/2 kV vendor FIT curve is on file):
#   continuous DC blocking  <= 0.67 * V_DSS   (Gen3 1200 V: 800 V -> ~5 FIT/cm2 at sea level on the curve above)
#   peak incl. overshoot    <= 0.85 * V_DSS   (industrial derating practice; ASSUMPTION, not a vendor number)
V_DC_FRAC = 0.67
V_PK_FRAC = 0.85


# ----------------------------------------------------------------------------------------------- magnetics
# Magnetics Inc. Powder Core Catalog 2025 (magnetics/Magnetics-Powder-Core-Catalog-2025.pdf), PDF page numbers:
#   p.12 saturation flux density, p.17 density, p.66 "% initial permeability = 1/(a + b*H^c)" (H in Oe) for
#   E, U & EER cores, p.111 toroid core-loss fits, p.112 E/U/EER core-loss fits, P = a*B^b*f^c (mW/cm3, B = half the
#   AC swing in T, f in kHz), p.199-200 E-core data.  XFlux bulletin (magnetics/XFlux.pdf p.1): 'Shapes' loss
#   700/600/600 mW/cm3 (26/40/60u) at 0.1 T, 50 kHz and toroid fits > 20 kHz.
# LOSS: the catalog E-core fit (p.112) is 1.4-2.3x LOWER than the catalog toroid fit (p.111) and than the XFlux bulletin
# 'Shapes' value for the same material.  The sources disagree, so every material carries both fits and the core-loss
# model uses the HIGHER one at each operating point (pv_tradeoff.core_loss_density).  XFlux shapes fit = bulletin toroid
# fit (>20 kHz) scaled to the bulletin shapes value at 0.1 T / 50 kHz.
POWDER_SRC = MAG + "Magnetics-Powder-Core-Catalog-2025.pdf (p.12 Bsat, p.17 density, p.66 DC bias, p.111/112 loss, p.199-200 E cores); XFlux.pdf p.1"
_KMM_TOR = (113.53, 2.072, 1.379, "catalog p.111 Kool Mu MAX toroids 26-90u")
MATERIALS = {   # key = material-perm; code = part-number letter (00<code><size>LE0<perm>)
    "KoolMuMAX-26": {"code": "Y", "mu": 26, "bsat": 1.0, "rho": 6500, "bias": (0.01, 1.600e-7, 2.000),
                     "loss": [(32.22, 1.988, 1.541, "catalog p.112 E cores"), _KMM_TOR]},
    "KoolMuMAX-40": {"code": "Y", "mu": 40, "bsat": 1.0, "rho": 6500, "bias": (0.01, 3.906e-7, 2.000),
                     "loss": [(32.22, 1.988, 1.541, "catalog p.112 E cores"), _KMM_TOR]},
    "KoolMuMAX-60": {"code": "Y", "mu": 60, "bsat": 1.0, "rho": 6500, "bias": (0.01, 2.575e-6, 1.758),
                     "loss": [(36.25, 1.988, 1.541, "catalog p.112 E cores"), _KMM_TOR]},
    "XFlux-26": {"code": "X", "mu": 26, "bsat": 1.6, "rho": 6900, "bias": (0.01, 1.163e-8, 2.258),
                 "loss": [(290.77, 2.015, 1.194, "catalog p.112 E cores"), (335.0 * 700 / 925, 1.825, 1.332, "XFlux.pdf p.1 shapes 700 @0.1T/50kHz")]},
    "XFlux-40": {"code": "X", "mu": 40, "bsat": 1.6, "rho": 6900, "bias": (0.01, 4.028e-8, 2.250),
                 "loss": [(387.69, 2.015, 1.194, "catalog p.112 E cores"), (332.5 * 600 / 800, 1.845, 1.310, "XFlux.pdf p.1 shapes 600 @0.1T/50kHz")]},
    "XFlux-60": {"code": "X", "mu": 60, "bsat": 1.6, "rho": 6900, "bias": (0.01, 8.129e-8, 2.269),
                 "loss": [(436.16, 2.015, 1.194, "catalog p.112 E cores"), (330.0 * 600 / 680, 1.865, 1.282, "XFlux.pdf p.1 shapes 600 @0.1T/50kHz")]},
}
# E-core sets (two halves).  Window per side: width M, height 2*D.  A_L by permeability (nH/T^2, measured, 2025 catalog p.200).
# Shapes offered in Kool Mu MAX (Y, selector chart p.39) and XFlux (X, p.41); A_L of the 114LE 40/60u and 8044E 40/60u
# grades are catalog values, the selector charts recommend 26u for 130LE/160LE and 40u for 114LE.
ECORES = {
    "8044E": {"A": 80.01, "B": 44.58, "C": 19.81, "D": 34.37, "E": 59.28, "F": 19.81, "M": 19.81,
              "le": 208.0, "Ae": 389.0, "Ve": 80900.0, "AL": {26: 91, 40: 113, 60: 170}},
    "114LE": {"A": 114.30, "B": 46.18, "C": 34.93, "D": 28.60, "E": 79.50, "F": 35.10, "M": 22.20,
              "le": 215.0, "Ae": 1220.0, "Ve": 262000.0, "AL": {26: 235, 40: 335, 60: 445}},
    "130LE": {"A": 130.30, "B": 32.51, "C": 53.85, "D": 22.10, "E": 108.46, "F": 20.02, "M": 44.22,
              "le": 219.0, "Ae": 1080.0, "Ve": 237000.0, "AL": {26: 254}},
    "160LE": {"A": 160.00, "B": 38.10, "C": 39.62, "D": 28.10, "E": 138.18, "F": 19.81, "M": 59.28,
              "le": 273.0, "Ae": 778.0, "Ve": 212000.0, "AL": {26: 180}},
}
CU_RHO20, CU_ALPHA = 1.72e-8, 0.00393       # ohm*m at 20 C, 1/K (annealed copper)

# ----------------------------------------------------------------------------------------------- capacitors
# KEMET C4AQ DC-link PP film (passives-capacitors/C4AQ.pdf, F3114 5/5/2025): ratings table p.13 (500-800 V) and
# p.14 (1100-1500 V); ESR and I_rms at 70 C, 10 kHz, I_rms gives ~30 K hot-spot rise (p.13/14 footnote);
# V_NDC at 70 C hot spot, V_OP85 at 85 C (p.6 voltage table); ripple V_pp <= 0.2*V_NDC (p.7).
C4AQ = {
    "C4AQUEW5450A3BJ": {"C": 45e-6, "vndc": 1300, "vop85": 1100, "esr": 3.1e-3, "irms": 34.0, "esl": 19e-9, "rth": 7.0, "dims_mm": (45, 65, 57.5), "page": 14},
    "C4AQQEW5650A3BJ": {"C": 65e-6, "vndc": 1100, "vop85": 900, "esr": 2.6e-3, "irms": 37.0, "esl": 19e-9, "rth": 7.0, "dims_mm": (45, 65, 57.5), "page": 14},
    "C4AQIEW6100A3BJ": {"C": 100e-6, "vndc": 800, "vop85": 700, "esr": 2.2e-3, "irms": 40.6, "esl": 19e-9, "rth": 7.0, "dims_mm": (45, 65, 57.5), "page": 13},
    "C4AQCEW6130A3BJ": {"C": 130e-6, "vndc": 650, "vop85": 600, "esr": 1.9e-3, "irms": 42.9, "esl": 19e-9, "rth": 7.0, "dims_mm": (45, 65, 57.5), "page": 13},
}
CAP_IRMS_USE = 0.7   # use <= 70 % of the table I_rms (hot-spot rise ~15 K instead of 30 K) - our derating rule
# leg decoupling candidates, same p.14 table (1300 V row): C, ESL, ESR at 10 kHz, Ipkr (= C x dV/dt), I_rms at 70 C; p.5:
# 1.5 x Ipkr is allowed only 1000 times per lifetime (non-repetitive)
C4AQ_DEC = {
    "C4AQUBU4100A1WJ": {"C": 1.0e-6, "vndc": 1300, "vop85": 1100, "esl": 17e-9, "esr": 33.1e-3, "ipkr": 28.0, "irms": 4.2, "dims_mm": (11, 20, 31.5)},
    "C4AQUBU4180A1XJ": {"C": 1.8e-6, "vndc": 1300, "vop85": 1100, "esl": 22e-9, "esr": 19.1e-3, "ipkr": 52.0, "irms": 6.2, "dims_mm": (13, 25, 31.5)},
    "C4AQUBU4220A1YJ": {"C": 2.2e-6, "vndc": 1300, "vop85": 1100, "esl": 24e-9, "esr": 16.0e-3, "ipkr": 63.0, "irms": 7.1, "dims_mm": (14, 28, 31.5)},
    "C4AQUBU4330A11J": {"C": 3.3e-6, "vndc": 1300, "vop85": 1100, "esl": 25e-9, "esr": 11.2e-3, "ipkr": 95.0, "irms": 9.1, "dims_mm": (19, 29, 31.5)},
}


# ----------------------------------------------------------------------------------------------- rectifiers
RECTIFIERS = {
    "VS-60EPS16-M3": {  # Vishay, power-semiconductors/VS-60EPS16-M3.pdf (document 94346, rev. 14-Oct-2022)
        "mfr": "Vishay", "src": SEMI + "VS-60EPS16-M3.pdf", "package": "TO-247AC 2L", "vrrm": 1600.0, "vrsm": 1700.0,   # p1
        "if_av": 60.0, "ifsm_10ms": 950.0, "i2t_10ms": 4525.0,                                                         # p1
        "i2sqrt_t": 45250.0, "i2sqrt_t_range_s": (1e-4, 1e-2),        # p1 'I2 sqrt(t) for fusing, t = 0.1 ms to 10 ms, no voltage reapplied'
        "vf_to_150": 0.74, "rt_150": 3.96e-3, "vfm_60_25": 1.15,      # p2
        "irm_25": 0.1e-3, "irm_150": 1.0e-3,                          # p2 at rated V_RRM
        "rth_jc": 0.35, "rth_ja": 40.0, "mass_g": 6.0,                # p2
        "fig7_150": [(100, 1.3), (200, 1.6), (500, 2.6), (1000, 4.2)],  # Fig.7 p3, T_J 150 C, read off the curve (A, V)
    },
    "WND75P16W6": {     # WeEn Semiconductors (CN), power-semiconductors/WND75P16W6.pdf (Rev.01, 21 September 2026)
        "mfr": "WeEn", "src": SEMI + "WND75P16W6.pdf", "package": "TO247-2L (mounting base = cathode)", "vrrm": 1600.0,   # p1, p2
        "vrsm": 1600.0,   # MISSING: no V_RSM printed (only V_RRM = V_RWM = V_R = 1600 V, p3) - V_RRM used
        "if_av": 75.0, "ifsm_10ms": 1050.0, "i2t_10ms": 5513.0,                                        # p3 Table 5 (Tj init 25 C)
        # Fig.4 p4 (sinusoidal, Tj(init) 25 C, maximum values), read on the log-log grid: (t_p s, I_FSM A); I2t(t_p) =
        # I_FSM^2 t_p / 2 (half sine) -> 0.5 ms 787 A2s, 1 ms 1416 A2s, 10 ms 5513 A2s (= the table value)
        "ifsm_curve": [(1e-5, 1865.0), (1e-4, 1830.0), (5e-4, 1774.0), (1e-3, 1683.0), (2e-3, 1533.0), (1e-2, 1050.0)],
        "vf_to_150": 0.830, "rt_150": 3.1e-3,     # p3 Fig.1/2 power model 'Vo = 0.830 V; Rs = 0.0031 ohm' (maximum values)
        "vfm_75_25": 1.15,                        # p6 Table 7 max at 75 A, 25 C (typ 1.05; 0.96 typ at 150 C)
        "irm_25": 50e-6, "irm_150": 2e-3,         # p6 Table 7 max at V_R 1600 V
        "rth_jc": 0.2, "rth_ja": 40.0,            # p5 Table 6 (R_th(j-mb) max)
        "quality": "IATF 16949 sites, HTRB lab (maker quality page, per sim/data/asia_devices.md); no AEC-Q101 statement; "
                   "no hot surge rating; brand-new datasheet",
    },
    "DD600N16K": {      # Infineon, power-semiconductors/DD600N16K.pdf (rev 3.4, 2025-08-06), ONE ARM of the dual module
        "mfr": "Infineon", "src": SEMI + "DD600N16K.pdf", "package": "60 mm PowerBLOCK, screw terminals, 1400 g",   # p1, p3
        "vrrm": 1600.0, "vrsm": 1700.0, "if_av": 600.0, "ifsm_10ms": 19000.0, "i2t_10ms": 1.8e6,                      # p2 (Tvj max)
        "vf_to_150": 0.75, "rt_150": 0.215e-3, "irm_150": 40e-3,                                                       # p2 max
        "l_arm_est": 5e-9,   # H per arm incl. terminals: NOT in the datasheet; deliberately low ESTIMATE (screw-terminal modules ~10-20 nH)
    },
}


# ----------------------------------------------------------------------------------------------- fans
# P-Q points (m3/h, Pa) read from the datasheet curve at nominal voltage; noise L_pA at 1 m in free air.
FANS = {
    "PFC1224DE-F00": {  # Delta, thermal/PFC1224DE-F00.pdf: p.2 table, p.3 noise condition, p.4 mass, p.5 P-Q curve, p.6 size
        "mfr": "Delta", "src": "docs/datasheets/thermal/PFC1224DE-F00.pdf", "size_mm": (120, 120, 38), "mass_kg": 0.380,
        "v": 24.0, "i_typ": 2.0, "i_max": 2.40, "p_w": 48.0, "p_max_w": 57.6, "rpm": 5500,
        "q_free": 429.6, "dp_max": 351.8,          # 7.16 m3/min, 35.877 mmH2O (p.2)
        "pq": [(0, 351.8), (48, 324), (96, 289), (144, 260), (192, 211), (240, 167), (288, 140), (336, 108), (384, 59), (429.6, 0)],
        "lp_1m": 66.5, "lp_note": "66.5 dB(A) avg (70.5 max) at 1 m from the intake, free air, anechoic (p.2, p.3 note 3)",
        "t_amb": (-10.0, 60.0)},                     # operating range per docs/datasheets/SOURCES.csv note (graphic PDF)
    "4414-2HHP": {      # ebm-papst, thermal/4414-2HHP.pdf: p.1 nominal data, p.2 mechanics, p.4 P-Q curve
        "mfr": "ebm-papst", "src": "docs/datasheets/thermal/4414-2HHP.pdf", "size_mm": (119, 119, 38), "mass_kg": 0.250,
        "v": 24.0, "i_typ": 0.5, "i_max": 0.5, "p_w": 12.0, "p_max_w": 12.0, "rpm": 5000,
        "q_free": 285.0, "dp_max": 188.0,
        "pq": [(0, 188), (25, 168), (50, 150), (75, 128), (100, 108), (112, 94), (150, 86), (200, 64), (250, 28), (285, 0)],
        "lp_1m": 55.0, "lp_note": "L_pA 55 dB(A), L_WA 6.4 B (p.1; distance not printed: L_WA - 11 dB = 53 dB(A) at 1 m, the higher is used)",
        "t_amb": (-20.0, 70.0)},                     # p.1 min./max. ambient temperature
    "4414FNH": {        # ebm-papst, thermal/4414FNH.pdf: p.1 nominal data, p.4 P-Q curve
        "mfr": "ebm-papst", "src": "docs/datasheets/thermal/4414FNH.pdf", "size_mm": (119, 119, 25), "mass_kg": 0.240,
        "v": 24.0, "i_typ": 0.5, "i_max": 0.5, "p_w": 12.0, "p_max_w": 12.0, "rpm": 5400,
        "q_free": 225.0, "dp_max": 200.0,
        "pq": [(0, 200), (25, 175), (50, 150), (75, 128), (100, 104), (108, 98), (150, 85), (175, 68), (200, 36), (225, 0)],
        "lp_1m": 56.0, "lp_note": "L_pA 55 dB(A), L_WA 6.7 B (p.1; L_WA - 11 dB = 56 dB(A) at 1 m is the higher and is used)",
        "t_amb": (-20.0, 70.0)},                     # p.1 min./max. ambient temperature
}
FANS["9GT1224P1S001"] = {   # Sanyo Denki San Ace 120T, thermal/9GT1224P1S001.pdf (catalogue C1152B001): p.484 table, p.485 curves
    "mfr": "Sanyo Denki", "src": "docs/datasheets/thermal/9GT1224P1S001.pdf", "size_mm": (120, 120, 38), "mass_kg": 0.420,
    "v": 24.0, "i_typ": 1.1, "i_max": 1.1, "p_w": 26.4, "p_max_w": 26.4, "rpm": 5600,           # p.484, 100 % PWM duty
    "q_free": 360.0, "dp_max": 270.0,                                                         # 6.0 m3/min, 270 Pa (p.484)
    "pq": [(0, 270), (60, 229), (120, 188), (180, 157), (240, 137), (300, 75), (330, 36), (360, 0)],  # p.485 24 V, 100 %, read off
    "lp_1m": 58.0, "lp_note": "SPL 58 dB(A) at 1 m from the inlet at 100 % duty, 41 dB(A) at 35 % (p.484)",
    "t_amb": (-40.0, 85.0),                                                                   # p.484 operating temperature
    "s_min": 2900.0 / 5600.0, "p_min_w": 5.76, "lp_min": 41.0,     # p.484: lowest specified duty 35 % -> 2900 rpm, 5.76 W, 41 dB(A)
    "life_h": "40000 h L10 at 85 C (162000 h at 40 C)", "pwm": "25 kHz PWM + pulse sensor; open control input = 100 %"}
# ebm-papst P_w is quoted at the nominal point; i = P/V.  The SYS-IO-AUX fan channel limit is 2.4 A (gen/sys_io_aux.py sheet 10).
# Delta AFB/FFB1224SHE-F00 (cost-first build, ARCHITECTURE-COSTFIRST sec. 9): p.2 ratings, p.3 environment, p.5 P-Q at 24 V
# (read off the curve, m3/min -> m3/h x 60, mmH2O -> Pa x 9.807).  No PWM input: speed by supply voltage (7-24 V fan buck).
_MMH2O = 9.807
FANS["AFB1224SHE-F00"] = {
    "mfr": "Delta", "src": "docs/datasheets/thermal/AFB1224SHE-F00.pdf", "size_mm": (120, 120, 38), "mass_kg": 0.256,
    "v": 24.0, "i_typ": 0.50, "i_max": 0.75, "p_w": 12.0, "p_max_w": 18.0, "rpm": 3700,          # p.2
    "q_free": 258.0, "dp_max": 14.5 * _MMH2O,                                                 # 4.300 m3/min, 14.50 mmH2O (p.2)
    "pq": [(q * 60, p * _MMH2O) for q, p in ((0, 14.4), (0.6, 12.0), (1.2, 9.3), (1.8, 7.0), (2.4, 5.4), (3.0, 4.5),
                                             (3.3, 4.0), (3.6, 3.0), (3.9, 1.6), (4.25, 0.0))],
    "lp_1m": 53.0, "lp_note": "53.0 dB(A) typ (56.0 max) at 1 m, free air (p.2)",
    "t_amb": (-10.0, 60.0),                                                                   # p.3 4-1 (storage -40..+75)
    "s_min": 7.0 / 24.0,     # ASSUMPTION: speed ~ supply voltage; p.2 operating voltage 7.0-27.6 V
    "speed_control": "supply voltage 7-24 V (no PWM input), tach"}
FANS["FFB1224SHE-F00"] = {
    "mfr": "Delta", "src": "docs/datasheets/thermal/FFB1224SHE-F00.pdf", "size_mm": (120, 120, 38), "mass_kg": 0.370,
    "v": 24.0, "i_typ": 0.80, "i_max": 1.20, "p_w": 19.2, "p_max_w": 28.8, "rpm": 3600,          # p.2
    "q_free": 290.4, "dp_max": 14.3 * _MMH2O,                                                 # 4.840 m3/min, 14.30 mmH2O (p.2)
    "pq": [(q * 60, p * _MMH2O) for q, p in ((0, 14.2), (0.8, 13.3), (1.6, 11.7), (2.4, 8.8), (2.8, 8.1), (3.2, 8.0),
                                             (3.6, 7.0), (4.0, 5.0), (4.4, 2.4), (4.8, 0.0))],
    "lp_1m": 56.5, "lp_note": "56.5 dB(A) typ (60.5 max) at 1 m, free air (p.2)",
    "t_amb": (-10.0, 60.0),                                                                   # p.3 4-1 (storage -40..+75)
    "s_min": 14.0 / 24.0,    # ASSUMPTION: speed ~ supply voltage; p.2 operating voltage 14.0-26.4 V
    "speed_control": "supply voltage 14-24 V (no PWM input), tach"}


# ----------------------------------------------------------------------------------------------- functions
def _lin(pts, x):
    """piecewise-linear through datasheet points, linear extrapolation at both ends, floored at 0"""
    xs, ys = np.array([p[0] for p in pts], float), np.array([p[1] for p in pts], float)
    x = np.asarray(x, float)
    y = np.interp(x, xs, ys)
    lo = ys[0] + (x - xs[0]) * (ys[1] - ys[0]) / (xs[1] - xs[0])
    hi = ys[-1] + (x - xs[-1]) * (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
    y = np.where(x < xs[0], lo, np.where(x > xs[-1], hi, y))
    return np.maximum(y, 0.0)


def rds(d, tj):
    return d["rds25"] * _lin(d["rds_norm"], tj)


def eoss(d, v):
    v = np.abs(np.asarray(v, float))
    if "eoss" in d:
        return _lin(d["eoss"], v)
    v0, e0 = d["eoss_ref"]
    return e0 * (v / v0) ** d["eoss_k"]


def e_sw(d, kind, v, i, tj):
    """datasheet-scaled energy of one event; kind in on/off/rr; i = commutated current (>=0)"""
    i = np.abs(np.asarray(i, float))
    v = np.abs(np.asarray(v, float))
    if kind == "rr" and "err" not in d:   # Q_rr based estimate
        q = d["qrr"] * np.sqrt(i / d["qrr_i"]) * _qrr_shape(tj) / _qrr_shape(d["qrr_t"])
        return K_RR * q * v
    pts = d["e" + kind]
    kv, kt = d["kv_" + kind], d["kt_" + kind]
    return _lin(pts, i) * (v / d["sw_v"]) ** kv * np.maximum(1.0 + kt * (tj - d["sw_t"]), 0.1)


def eon_rg_factor(d, rg_on):
    """E_on(R_G,on) / E_on(datasheet R_G) from the vendor E-vs-R_G figure; (R ratio)^0.6 where no curve was read (ASSUMPTION)"""
    if rg_on <= d["rg_ext"] + 1e-12:
        return 1.0
    if "eon_rg" in d:
        return float(_lin(d["eon_rg"], rg_on) / _lin(d["eon_rg"], d["rg_ext"]))
    return (rg_on / d["rg_ext"]) ** 0.6


def eoff_rg_factor(d, rg_off):
    """E_off(R_G,off) / E_off(datasheet R_G) from the vendor E-vs-R_G figure; (R ratio)^1.0 where no curve was read (ASSUMPTION)"""
    if abs(rg_off - d["rg_ext"]) < 1e-12:
        return 1.0
    if "eoff_rg" in d:
        return float(_lin(d["eoff_rg"], rg_off) / _lin(d["eoff_rg"], d["rg_ext"]))
    return rg_off / d["rg_ext"]


def vsd(d, i):
    """body-diode drop with the off-state gate bias (dead time); linear through V_SD0 and one datasheet point"""
    rd = (d["vsd"] - VSD0) / d["vsd_i"]
    return VSD0 + rd * np.abs(i)


def didt_off(d, i):
    """turn-off current slope used for the L*di/dt overshoot estimate (A/s)"""
    if "didt_off" in d:
        return _lin(d["didt_off"], np.abs(i))
    return 0.8 * np.abs(i) / d["tf"]  # 10-90 % of I over the datasheet fall time (t_f held at its test value)


def self_check():
    for name, d in MOSFETS.items():
        for kind, v, i, t, ref in d["check"]:
            got = float(e_sw(d, kind, v, i, t))
            assert abs(got / ref - 1) < 0.12, (name, kind, got, ref)
        assert 1.5 < float(_lin(d["rds_norm"], 175)) < 3.2, name
        assert float(eoss(d, d["c_at"])) > 0.3 * 0.5 * d["coss"] * d["c_at"] ** 2, name   # E_oss >= ~C_oss(V) V^2/2
    return True


if __name__ == "__main__":
    self_check()
    for n, d in MOSFETS.items():
        print(f"{n:16s} {d['vdss']} V  Rds(25/125/150) = {rds(d,25)*1e3:5.1f}/{rds(d,125)*1e3:5.1f}/{rds(d,150)*1e3:5.1f} mOhm  "
              f"Eon+Eoff+Err(0.5*Vdss*0.8, 45 A, 125 C) = "
              f"{(e_sw(d,'on',0.4*d['vdss'],45,125)+e_sw(d,'off',0.4*d['vdss'],45,125)+e_sw(d,'rr',0.4*d['vdss'],45,125))*1e3:5.2f} mJ")
    print("pv_devices self-check passed")
