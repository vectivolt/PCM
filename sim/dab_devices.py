"""DAB-D60 device, capacitor, magnetic-material and reference-design data.

Every number carries its source as file + page/figure.  SI units throughout (V, A, ohm, F, H, J, W, s, m),
temperatures in degC.  Table values are copied verbatim.  Curve values marked 'vec' are the PDF vector-data
readings of the independent audit (AUDIT, rows quoted in the comments); curve values marked 'fig' are our own
readings of the PDF rendered at 250-400 dpi (about +/-5 %).  Nothing here is measured by us.
"""
import os

import numpy as np

PS = 'docs/datasheets/power-semiconductors/'
GD = 'docs/datasheets/gate-drivers/'
DS = 'docs/datasheets/power-semiconductors/CBB011M12GM4T.pdf'   # Wolfspeed CBB011M12GM4/GM4T Rev.3, June 2026
UG = 'docs/reference-designs/wolfspeed/crd60dd12n-gmb/crd60dd12n-gmb_user-guide_prd-10001.pdf'  # PRD-10001 Rev.1
WP = 'docs/datasheets/power-semiconductors/Wolfspeed_Designed_to_Last_Even_in_the_Harshest_Environments.pdf'
TI = 'docs/reference-designs/ti/tida-010054/tida-010054_design-guide_tidues0.pdf'   # TIDUES0F, Apr 2026
C4AQ = 'docs/datasheets/passives-capacitors/C4AQ.pdf'            # KEMET F3114_C4AQ 5/5/2025
N95 = 'docs/datasheets/magnetics/N95.pdf'                        # TDK SIFERRIT N95, June 2025
N97 = 'docs/datasheets/magnetics/N97.pdf'                        # TDK SIFERRIT N97, June 2025
PM114 = 'docs/datasheets/magnetics/PM114-93.pdf'                 # TDK PM 114/93 B65733, Oct 2024
UCC = 'docs/datasheets/gate-drivers/UCC21710.pdf'
HO = 'docs/datasheets/sensing/HO-NP_series.pdf'                  # LEM HO 40..150-NP, v9 Jan 2022
AUDIT = 'sim/out/audit/dab_devices_audit.csv'                    # independent vector-data audit of this file
FM3_11 = 'docs/datasheets/power-semiconductors/CAB011M12FM3.pdf'  # Wolfspeed half bridge, Rev.6 June 2026
FM3_16 = 'docs/datasheets/power-semiconductors/CAB016M12FM3.pdf'

# ----------------------------------------------------------------------------------------------------------
# Power module: Wolfspeed CBB011M12GM4T (1200 V, 11 mOhm, full bridge, Gen 4, pre-applied TIM)
# ----------------------------------------------------------------------------------------------------------
MODULE = {
    'mpn': 'CBB011M12GM4T',
    'src': DS,
    'vds_max': 1200.0,                 # p1 key parameters
    'vgs_max': (-10.0, 23.0),          # p1, transient
    'vgs_op': (-4.0, 15.0),            # p1 static -4/+15..18 V; note 1: 15 V +/-5 % recommended
    'id_dc': 100.0,                    # p1, THS=50 C, press-fit/package limit (note 2) -> also the RMS ceiling
    'idm': 400.0,                      # p1
    'tvj_op_max': 150.0,               # p1, 175 C intermittent with reduced life
    'rds_on': {25: 11.0e-3, 150: 17.6e-3, 175: 19.8e-3},   # p2, VGS=15 V, ID=100 A, devices only
    'rds_on_25_max': 14.9e-3,          # p2
    'rds_on_18v': {25: 9.6e-3, 150: 16.8e-3, 175: 19.0e-3},  # p2, VGS=18 V
    'r_pkg_hs': 2.98e-3,               # p3, M1 high side, THS=125 C, note 5: add to RDS(on)
    'r_pkg_ls': 3.18e-3,               # p3, M2 low side
    'r_pkg_tc': 0.0039,                # 1/K, copper TCR - ASSUMPTION (package resistance only given at 125 C)
    'rg_int': 1.4,                     # p2
    'ciss': 10.1e-9, 'coss_800': 0.4e-9, 'crss_800': 36e-12,   # p2, VDS=800 V
    'qg': 405e-9, 'qgs': 180e-9, 'qgd': 96e-9,                # p2, -4/+15 V, 800 V, 100 A
    'rth_jh': 0.429,                   # K/W per FET, junction-to-heatsink with pre-applied TIM, p2 / Fig. 17
    'l_stray': 16.8e-9,                # p3, DC+ to DC-, 10 MHz
    'visol': 3000.0,                   # V AC 50 Hz 1 min, case isolation, p3
    'cti': 200,                        # p3
    'clearance_tt': 5.0e-3, 'clearance_th': 10.0e-3,     # p3 terminal-terminal / terminal-heatsink
    'creepage_tt': 6.3e-3, 'creepage_th': 11.5e-3,       # p3
    'ntc_r25': 5000.0, 'ntc_b25_50': 3380.0,             # p3
    'vsd_100A': {25: 5.8, 175: 5.4},   # p3, body diode VGS=-4 V, ISD=100 A
    'vsd_knee': {25: 2.89, 175: 2.54},  # V_SD at 1 A, Fig. 7 p5 (vec, AUDIT row vsd_knee)
    'qrr': 2.25e-6, 'irrm': 183.0, 'trr': 20.3e-9,       # p3, 600 V, 100 A, 16.4 A/ns, 175 C
    'err_600_100': {25: 0.38e-3, 125: 0.48e-3, 175: 0.66e-3},   # p3
    'eon_600_100': {25: 1.2e-3, 125: 1.1e-3, 175: 1.2e-3},      # p2, RG(on)=1 ohm, RG(off)=0, Lsigma=24 nH
    'eoff_600_100': {25: 0.17e-3, 125: 0.15e-3, 175: 0.12e-3},  # p2
    'dpt_rg_on': 1.0, 'dpt_rg_off': 0.0, 'dpt_lsigma': 24e-9,   # p2 test conditions
    'dpt_driver_note': 'CGD1700HB2M-UNA (UCC21710); 0.7/0.3 ohm driver parasitic not in RG(ext) (p9 note 6)',
    'pd_max_ths50': 292.0,             # p1
}

# Switching energies vs current (Figs. 11, 12 p5, Tvj=25 C, RG(on)=1, RG(off)=0, -4/+15 V, 24 nH) [J] (vec, AUDIT
# rows E_TAB[600]/E_TAB[800]; our earlier by-eye reads were up to 21 % high at 20 A and 13 % high for E_rr at 100 A)
E_I = np.array([20.0, 50.0, 100.0, 150.0, 200.0])
E_TAB = {
    600: {'eon': np.array([0.511, 0.758, 1.187, 1.638, 2.112]) * 1e-3,
          'eoff': np.array([0.035, 0.051, 0.170, 0.403, 0.751]) * 1e-3,
          'err': np.array([0.221, 0.280, 0.370, 0.447, 0.511]) * 1e-3},
    800: {'eon': np.array([0.826, 1.238, 1.951, 2.695, 3.471]) * 1e-3,
          'eoff': np.array([0.112, 0.108, 0.228, 0.506, 0.942]) * 1e-3,
          'err': np.array([0.272, 0.339, 0.441, 0.531, 0.609]) * 1e-3},
}
# E_off vs external gate resistance (Fig. 15 p6, 600 V, 100 A, 25 C) at RG(ext) = 1 ohm (fig): used only to put the
# FM3 datasheet E_off (measured with RG(off) = 1 ohm) on the same basis as ours (RG(off) = 0)
EOFF_RG1_600_100 = 0.31e-3   # Fig. 15 p6 (fig): 0.14 mJ at 0 ohm rising 0.169 mJ/ohm
EOFF_RG_SLOPE = 0.169e-3     # J/ohm, Fig. 15 p6 (fig), 600 V, 100 A, 25 C: added E_off per ohm of external RG(off)
TDOFF_RG_SLOPE = 13e-9       # s/ohm, Fig. 28 p8 (fig): t_d(off) 42 ns at 0 ohm, ~68 ns at 2 ohm
DVDT_OFF_RG = (np.array([0.0, 0.33, 1.0, 2.0]), np.array([60.0, 56.0, 49.0, 38.0]))   # V/ns, Fig. 29 p8 (fig)
SC_GEN4 = {'t_sc': 2.3e-6, 'die': 'EM4E120-025D100 (automotive Gen 4 die, 25 mOhm)', 'src': WP + ' p8',
           'note': 'conditions not stated; the CBB011M12GM4T datasheet publishes no short-circuit withstand time'}
# Temperature factors from the p2/p3 tables (600 V, 100 A), normalised to 25 C
E_TFAC = {'eon': {25: 1.0, 125: 1.1 / 1.2, 175: 1.0},
          'eoff': {25: 1.0, 125: 0.15 / 0.17, 175: 0.12 / 0.17},
          'err': {25: 1.0, 125: 0.48 / 0.38, 175: 0.66 / 0.38}}

# Output capacitance vs VDS, Fig. 9 p5 (0-1200 V), Tvj=25 C (fig).  Fig. 8 (0-200 V) gives ~half the value at
# the same label voltage; it coincides with Fig. 9 if its V axis is read x4 (0.39 nF at '200 V' = table value at
# 800 V), so Fig. 8 is treated as mislabelled and Fig. 9 + the p2 table (0.4 nF at 800 V) are used.
# Values: AUDIT rows COSS_F (vec); 0-10 V is near-vertical in Fig. 9 (+/-20 %, 0.8 % of Q_oss(800 V)).
COSS_V = np.array([0.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0, 300.0, 400.0, 600.0, 800.0, 1000.0, 1200.0])
COSS_F = np.array([11.4, 5.7, 3.9, 2.240, 1.501, 1.054, 0.752, 0.619, 0.541, 0.446, 0.395, 0.363, 0.338]) * 1e-9

# Closed-form fit of the Fig. 9 table for SPICE: Coss = c0 + c1/(1+v/v0)^m (max error 4.9 %, Q(800 V) -3.1 %)
COSS_FIT = (1.7627e-10, 1.1381e-8, 2.3223, 0.67457)


def qoss_fit(v):
    c0, c1, v0, m = COSS_FIT
    return c0 * v + c1 * v0 / (1 - m) * ((1 + np.asarray(v, float) / v0) ** (1 - m) - 1)


# Switching SOA, Fig. 19 p7, Tvj=150 C, L_stray bussing 7.2 nH + module 16.8 nH (vec, AUDIT rows SSOA):
SSOA = {
    'turn_off_i': np.array([0.0, 39.0, 62.0, 100.0, 150.0, 175.0]),        # RG(off) = 0
    'turn_off_v': np.array([1200.0, 1118.0, 1066.0, 975.0, 879.0, 842.0]),
    'turn_on_i': np.array([0.0, 16.0, 39.0, 62.0, 85.0, 180.0]),           # RG(on) = 1
    'turn_on_v': np.array([1200.0, 1152.0, 1082.0, 1011.0, 953.0, 934.0]),
    'l_total': 24e-9, 'tvj': 150.0,
}
# Turn-off di/dt vs current, Fig. 25 p8 (600 V, 25 C, RG(off)=0) (vec, AUDIT row DIDT_OFF): A/ns
DIDT_OFF = (np.array([20.0, 100.0, 200.0]), np.array([2.58, 9.06, 23.68]) * 1e9)

# Cosmic-ray (terrestrial neutron) failure rate, sea level, FIT per cm^2 of die vs drain voltage,
# WP Fig. 4 p7 (fig).  Gen 4 is the CBB011M12GM4T generation.
COSMIC = {
    'gen4_v': np.array([865, 897, 926, 949, 974, 1010, 1047, 1090, 1126, 1168, 1201, 1261.0]),      # vec, AUDIT
    'gen4_fit': np.array([0.01, 0.099, 0.48, 1.49, 3.97, 13.2, 34, 85, 169, 319, 498, 1001.0]),
    'gen3_v': np.array([638, 682, 713, 742, 773, 813, 864, 923, 983, 1064, 1124, 1206.0]),
    'gen3_fit': np.array([0.01, 0.1, 0.46, 1.0, 2.8, 6.9, 20.3, 57, 136, 330, 540, 1000.0]),
    'die_area_cm2': 0.30,   # ASSUMPTION per switch position (not published); ~3 mOhm.cm2 / 11 mOhm, range 0.2-0.4
}

# ----------------------------------------------------------------------------------------------------------
# CRD60DD12N-GMB reference design facts (UG)
# ----------------------------------------------------------------------------------------------------------
CRD = {
    'src': UG,
    'module': 'CBB011M12GM4T x2 (one per bridge)',                      # p6, p8 sec 2.1
    'p_out_max': 60e3, 'vin': (700, 800, 900), 'vout': (400, 800, 900),  # p8 Table 1
    'iout_max': 100.0, 'derate_below_vout': 600.0,                      # p8 Table 1 + footnote 1
    'fsw': 100e3,                                                       # p8 Table 1
    'rth_coldplate_fluid': 0.045,      # K/W per switch position, p8 Table 1 and p51 sec 5.1 (POWERT3ster)
    'coolant': '20 C pre-mixed antifreeze, 9 L/min',                    # p52 sec 5.3.1
    'coolant_flow_lpm': 9.0,
    'bulk_caps': 'KEMET C4AQNEW5650M3BJ 65 uF 1 kV film: 2 primary (130 uF), 3 secondary (195 uF)',  # p23
    'bulk_caps_note': ('C4AQNEW5650M3BJ is NOT in the C4AQ datasheet on file (no N=1 kV voltage code or M release '
                       'code in F3114); the CRD calibration uses the 1100 V sibling C4AQQEW5650A3BJ as a stand-in'),
    'c_bulk_pri': 130e-6, 'c_bulk_sec': 195e-6,
    'snubber': '6 x (6.8 ohm 1.5 W + 2.2 nF 2 kV) RC across each DC link (DC+ to DC-)',   # p27 Fig. 22
    'snubber_overshoot_sim': {'v_bus': 800.0, 'i': 100.0, 'vpk_no_snub': 952.0, 'vpk_snub': 940.0},  # p26 Fig. 21 (fig)
    'sensors': 'LEM HO 120-NP-0100, +/-300 A, 120 Arms (DC and transformer, both sides)',  # p28 sec 3.1.5
    'v_sense': 'AMC3330-Q1 isolated amplifier, 0..2 V diff; V = ADC*0.260978 (1069 V FS)',  # p34-35
    'i_scale': 'I = ADC*0.16320461 - 333.88 (+/-334 A FS)',                                # p36
    'gate_driver': 'UCC21710 + UCC14141-Q1 1.5 W isolated bias, +15/-4 V, DESAT on all 8 positions',  # p33, p38
    'transformer': 'YAGEO/Egston Pulse 81989-01, custom, 202 x 81 x 73 mm, cold-plate cooled',  # p51
    'xfmr_lsigma': 7.5e-6, 'xfmr_lm': 329e-6, 'xfmr_rw': 78e-3, 'xfmr_rc': 1.24,   # p52 Table 9 @100 kHz
    'xfmr_turns_ratio': None,          # NOT published; 1:1 inferred from 800 V / 800 V test point (Table 11)
    'test': {'vin': 800.0, 'vout': 800.0, 'fsw': 100e3, 'tdead': 200e-9, 'rg_on': 1.0, 'rg_off': 0.0,
             'pout': (10e3, 23e3), 'phase_deg': (2.5, 5.0)},   # p53 Table 11
    # Table 11 phase is inconsistent with the guide itself: Fig. 55 p54 shows 10.0 deg at 22.7 kW, which matches
    # Table 9 (7.5 uH, 1:1 inferred) and SPS (10.1 deg).  Table 11 phase is NOT used anywhere (AUDIT row phase_deg).
    'phase_fig55_deg_at_22k7': 10.0,
    # Measured efficiency, p55 Fig. 57 (fig): (kW, %)
    'eff_meas': np.array([[10.8, 97.01], [13.5, 97.35], [16.3, 97.52], [18.3, 97.59], [20.9, 97.63], [22.7, 97.63]]),
    'web_claims': 'product page: 99.2 % peak, 10.5 kW/L, leakage-only (no separate inductor)',  # web page, not in UG
    'hf_caps': {'P': (10, 47e-9, 1000.0), 'S': (13, 47e-9, 1000.0)},  # UG p25 Fig. 19: n x 0.047 uF 1 kV per port
    'firmware': 'open-loop SPS + CAN GUI; firmware separately downloadable',  # p43 sec 4.1
}

# TI TIDA-010054 cross-check (TI design guide)
TIDA = {
    'src': TI,
    'p': 10e3, 'v1': (700, 800), 'v2': (250, 500), 'fsw': 100e3,           # p4 Table 1-1
    'eff': 'peak 98.8 % at 4 kW, 98.0 % at 10 kW (97.6 % quoted in text)',  # p4
    'l_total': 35e-6, 'n': 1.6, 'phase_max_rad': 0.44,                      # p23 Table 2-1
    'c_block_rule': 'C_min = 100/(4 pi^2 fs^2 L): blocking-cap resonance at fs/10 (Eq. 20)',  # p22
    'zvs_note': 'leakage L sized for ZVS only down to 1/2..1/3 load; EPS in software for light load',  # p19, p23
    'gate_driver': 'UCC21710',                                              # p7
}

# Gate driver TI UCC21710 (datasheet)
UCC21710 = {
    'src': UCC,
    'v_octh': (0.63, 0.70, 0.77),      # OC detection threshold V, p9
    't_ocfil': (95e-9, 120e-9, 180e-9),  # OC deglitch, p9
    'i_sto': 0.4,                      # soft turn-off 400 mA, p1/p9
    'tpd': (60e-9, 90e-9, 130e-9), 'pwd_max': 30e-9, 'tsk_pp': 30e-9,  # p10
    'cmti_min': 150e9,                 # V/s, p1/p9
    'v_clmpth': 2.0,                   # Miller clamp threshold above VEE, p9
}

# Current sensor LEM HO 120-NP-0100 (p7)
HO120 = {
    'src': HO, 'ipn': 120.0, 'ipm': 300.0, 'ioe': 0.75, 'tc_ioe': 11.25e-3, 'sens': 6.667e-3,
    'bw': 350e3, 'td90': 2.5e-6, 'ocd': (2.64 * 120, 2.93 * 120, 3.22 * 120),
}

# ----------------------------------------------------------------------------------------------------------
# DC-link capacitors: KEMET C4AQ (PP film, AEC-Q200)
# ----------------------------------------------------------------------------------------------------------
CAPS = {
    # p14 Table 1: C, VNDC(70 C hot spot), ESR(10 kHz, 70 C), Irms(10 kHz, 70 C ambient, dT~30 K), ESL, Rth
    'C4AQQEW5650A3BJ': {'c': 65e-6, 'vndc': 1100.0, 'esr': 2.6e-3, 'irms': 37.0, 'esl': 19e-9, 'rth': 7.0,
                        'ipkr': 717.0, 'size_mm': (45, 65, 57.5), 'src': C4AQ + ' p14'},
    'C4AQUEW5450A3BJ': {'c': 45e-6, 'vndc': 1300.0, 'esr': 3.1e-3, 'irms': 34.0, 'esl': 19e-9, 'rth': 7.0,
                        'ipkr': 596.0, 'size_mm': (45, 65, 57.5), 'src': C4AQ + ' p14'},
    'C4AQSEW5400A3BJ': {'c': 40e-6, 'vndc': 1500.0, 'esr': 3.1e-3, 'irms': 33.7, 'esl': 19e-9, 'rth': 7.0,
                        'ipkr': 562.0, 'size_mm': (45, 65, 57.5), 'src': C4AQ + ' p14'},
}
# Operative voltage derating vs hot-spot temperature, p6: VNDC @70 C, VOP85 @85 C, VOP105 @105 C
CAP_VDERATE = {1100: (1100.0, 900.0, 700.0), 1300: (1300.0, 1100.0, 850.0), 1500: (1500.0, 1200.0, 900.0)}
CAP_LIFE = '100 000 h at VNDC/70 C or VOP85/85 C hot spot; surge 1.5 x VNDC max 10 times (p5)'

# ----------------------------------------------------------------------------------------------------------
# Ferrite: TDK N95 / N97, core PM 114/93
# ----------------------------------------------------------------------------------------------------------
# Relative core loss at 100 kHz vs temperature, sinusoidal, R34 toroids (fig) [kW/m^3]
FERRITE = {
    'N95': {'src': N95 + ' p5 FAL0752-Z', 'T': np.array([25, 60, 80, 100, 120, 140.0]),
            'B': np.array([0.025, 0.05, 0.1, 0.2]),
            'pv': np.array([[3.53, 18.1, 89.5, 438], [2.34, 12.4, 67.8, 373], [1.73, 10.0, 58.3, 347],
                            [1.51, 8.36, 50.5, 341], [1.55, 8.7, 55.5, 381], [1.96, 10.2, 67.2, 452]]) * 1e3,
            # rows 25/100/140 C: vec (AUDIT); rows 60/80/120 C: fig (within 3.1 % per AUDIT)
            'pv_table': {'100k_200mT_25C': 425e3, '100k_200mT_100C': 350e3, '300k_100mT_100C': 410e3},  # p2
            'bsat_100C': 0.410, 'bsat_25C': 0.525, 'mu_i': 3000},                                       # p2
    'N97': {'src': N97 + ' p5 FAL0624-N', 'T': np.array([25, 60, 80, 100, 120, 140.0]),
            'B': np.array([0.025, 0.05, 0.1, 0.2]),
            'pv': np.array([[4.68, 28.1, 137, 611], [2.74, 16.0, 84, 425], [1.82, 10.6, 60, 357],
                            [1.26, 6.82, 43.1, 301], [1.36, 7.5, 51, 365], [1.71, 10.2, 68.3, 472]]) * 1e3,
            # rows 25/100/140 C: vec (AUDIT); others fig (file was +3 % high on average = conservative)
            'pv_table': {'25k_200mT_100C': 45e3, '100k_200mT_100C': 300e3, '300k_100mT_100C': 340e3},   # p2
            'bsat_100C': 0.410, 'bsat_25C': 0.510, 'mu_i': 2300},                                       # p2
}
STEINMETZ_ALPHA = 1.6   # ASSUMPTION for iGSE shape factor; N97 table gives 1.37 (25->100 kHz), N95 ~1.9 (100->300 kHz)

CORE_PM114 = {'src': PM114 + ' p2-3', 'mpn_core': 'B65733A0000R095 (N95, ungapped set)', 'le': 0.200,
              'ae': 1720e-6, 'amin': 1380e-6, 've': 344e-6, 'mass': 1.94, 'al_ungapped': 19.5e-6,
              'aw_bobbin': 1070e-6, 'mlt': 0.210, 'bobbin': 'B65734B1000T001'}


# Alternative Wolfspeed modules on file (design-lever study only; the baseline stays CBB011M12GM4T, DAB-02)
FM3 = {
    'CAB011M12FM3': {'src': FM3_11, 'topology': 'half bridge (2 per full bridge)',
                     'rds_on': {25: 10.5e-3, 150: 16.3e-3, 175: 19.0e-3},          # p2, 15 V, 100 A
                     'rds_on_18v': {25: 9.2e-3, 150: 15.9e-3, 175: 18.3e-3},        # p2
                     'r_pkg': 0.5 * (2.23e-3 + 2.06e-3),                            # p3, TC = 125 C
                     'eon_600_100': 1.3e-3, 'eoff_600_100': 0.71e-3,               # p2, 25 C, RG(on)=RG(off)=1 ohm
                     'err_600_100_125C': 0.48e-3, 'coss_800': 0.39e-9,             # p2/p3
                     'vsd_100A': {25: 5.1, 150: 4.7}, 'rth_jh': 0.428,             # p3, p2
                     'l_stray': 11.4e-9, 'generation': 'not stated in the datasheet'},
    'CAB016M12FM3': {'src': FM3_16, 'topology': 'half bridge (2 per full bridge)',
                     'rds_on': {25: 16.0e-3, 150: 25.6e-3, 175: 28.8e-3},          # p2, 15 V, 80 A
                     'r_pkg': 0.5 * (2.23e-3 + 2.06e-3), 'rth_jh': 0.543,         # p3, p2
                     'eoff_600_80': 0.54e-3, 'eoff_rg': 4.0, 'coss_800': 0.29e-9},  # p2 (RG 4 ohm)
}

# DC-link reverse clamp (one arm of an Infineon PowerBLOCK rectifier module per port), DD600N data sheet rev 3.4
DDM = 'docs/datasheets/power-semiconductors/DD600N16K.pdf'
CLAMP = {'mpn': 'DD600N16K', 'src': DDM, 'vrrm': 1600.0, 'vrsm': 1700.0,              # p2 (16 = 1600 V grade)
         'ifsm_25': 22e3, 'ifsm_hot': 19e3, 'i2t_25': 2.42e6, 'i2t_hot': 1.8e6,       # p2, tP = 10 ms
         'vt0': 0.75, 'rt': 0.215e-3, 'vf_1800': 1.32, 'ir_hot': 40e-3, 'tvj_max': 150.0,  # p2 (Tvj max values)
         'zth_r': (0.0267, 0.0254, 0.01465, 0.00584, 0.00194),                        # p5 Z_thJC per arm, DC
         'zth_tau': (3.0, 0.57, 0.108, 0.00824, 0.000732),
         'visol': 3000.0, 'creepage': 19e-3, 'mass': 1.4, 'package': 'PowerBLOCK 60 mm, M10 terminals',  # p2-p4
         'arm': 'D1: terminal 1 = anode (to DC-), terminal 2 = cathode (to DC+); terminal 3 (D2 anode) unused (p4)'}

# ----------------------------------------------------------------------------------------------------------
# Asian SiC half-bridge modules (class d of sim/data/asia_devices.md).  Curve values: PDF vector-path readings
# (BASiC, ~1 % FS) and 180-dpi image readings (Leapers, ~2-3 % FS) of the module-extraction pass, 2026-10-04.
# BMF008 dynamic curves coincide with the BMF240 curves scaled by 2/3 in current (vendor estimates, not separate
# measurements); both datasheets give typical values only.
# ----------------------------------------------------------------------------------------------------------
_B8 = PS + 'BMF008MR12E2G3.pdf'
_LP = PS + 'DFS08HF12EZA2.pdf'
AMOD = {
    'BMF008MR12E2G3': dict(
        mpn='BMF008MR12E2G3', maker='BASiC Semiconductor (CN)', package='Pcore2 E2B press-fit half bridge + SiC SBD',
        src=_B8, pages='p2-p8, p10 (preliminary Rev 1.0, 2026-08-14)', kind='half_bridge',
        rds_T=np.array([25.0, 75, 100, 125, 150, 175]),
        rds=np.array([7.55, 8.53, 9.21, 9.94, 10.83, 12.29]) * 1e-3,          # chip, VGS 18 V, 130 A, p5 Fig.6
        rds_max_25=10.6e-3, r_pkg=0.53e-3,                                     # chip max p3; R_DD'+SS' p4
        coss_v=np.array([0.1, 1, 5, 10, 20, 50, 100, 200, 400, 600, 800, 1000.0]),
        coss_f=np.array([24600, 20200, 9620, 5650, 3450, 2130, 1515, 1087, 786, 654, 626, 622.0]) * 1e-12,  # Fig.10 p6
        e_i=np.array([6.67, 13.3, 26.7, 40, 53.3, 66.7, 106.7, 133.3, 160, 200, 233.3, 266.7]),
        e_tab={600.0: {'eon': np.array([0.568, 0.687, 0.929, 1.180, 1.436, 1.700, 2.535, 3.126, 3.746, 4.728, 5.596,
                                        6.507]) * 1e-3,                    # p7 Fig.13, 25 C, incl. SBD recovery
                       'eoff': np.array([0.098, 0.115, 0.153, 0.198, 0.249, 0.307, 0.518, 0.691, 0.889, 1.235, 1.567,
                                         1.939]) * 1e-3,
                       'err': np.array([0.034, 0.036, 0.041, 0.046, 0.050, 0.054, 0.066, 0.074, 0.081, 0.091, 0.098,
                                        0.104]) * 1e-3}},
        k_v={'eon': 1.7, 'eoff': 1.0, 'err': 1.0},     # single test voltage: exponents of the incumbent (ASSUMPTION)
        e_T={'eon': ([25.0, 175.0], [1.0, 2.327 / 3.126]), 'eoff': ([25.0, 175.0], [1.0, 0.637 / 0.691]),
             'err': ([25.0, 175.0], [1.0, 0.131 / 0.074])},                    # Fig.13 at 133 A, 175 / 25 C
        e_rg={'eon': (np.array([3.3, 9.3, 15, 22.5, 33]), np.array([3.126, 5.942, 8.352, 11.13, 14.27]) * 1e-3),
              'eoff': (np.array([3.3, 9.3, 15, 22.5, 33]), np.array([0.691, 1.560, 2.364, 3.387, 4.756]) * 1e-3)},
        e_test=dict(v=600.0, i=130.0, rg_on=3.3, rg_off=3.3, vgs=(18, -4), l_sigma=30e-9),   # p3
        t_rg={'td_off': (np.array([3.3, 9.3, 15, 22.5, 33]), np.array([66.8, 135.8, 201.6, 300.6, 490.1])),   # 175 C
              'tf': (np.array([3.3, 9.3, 15, 22.5, 33]), np.array([26.2, 31.5, 36.1, 46.3, 61.3])),
              'td_on': (np.array([3.3, 9.3, 15, 22.5, 33]), np.array([39.1, 74.5, 108.2, 152.5, 214.5]))},  # Fig.16
        vsd_i=np.array([10.0, 25, 50, 100, 150, 200]),                         # SBD + body diode, VGS -5 V (no -4 V
        vsd_v={25: np.array([0.966, 1.084, 1.233, 1.552, 1.875, 2.204]),       # curve), p5 Fig.3 / Fig.4
               175: np.array([1.091, 1.337, 1.706, 2.415, 3.126, 3.848])},
        rth_jc=0.13, rth_ch=0.16, tj_max=175.0, vgs_max=25.0, vgs_min=-10.0, vth_min_25=3.0, vth_175=3.05,
        qg=401e-9, qgs=None, qgd=None, rg_int=0.55, qrr=1.2e-6, i_dm=320.0, l_stray=8e-9,
        i_term_rms=10 * 25.0, i_pos_rms=8 * 25.0, tsc=None, rails=(18.0, -4.0),     # 25 A rms per pin (p4),
                                                                                    # AC 10 / DC+ 8 / DC- 8 pins (p10)
        qualification='none for the part (company page claims AQG324/JEDEC testing); preliminary datasheet',
        open_items=['price (RFQ)', 'final datasheet', 'qualification report', 'short-circuit rating',
                    'R_G limits of the RBSOA (TBD in Fig.17)']),
    'DFS08HF12EZA2': dict(
        mpn='DFS08HF12EZA2', maker='Wuxi Leapers Semiconductor (CN)', package='E2 36-pin half bridge, body diode only',
        src=_LP, pages='pdf p4-p16 (preliminary Ver.B)', kind='half_bridge',
        rds_T=np.array([25.0, 75, 100, 125, 150, 175]),
        rds=np.array([7.50, 8.26, 8.87, 9.74, 10.77, 11.76]) * 1e-3,           # chip, VGS 15 V, 160 A, pdf12 Fig.9
        rds_max_25=7.5e-3 * 10.6 / 7.6, r_pkg=0.53e-3,     # no max / terminal R printed: BASiC E2B ratios ASSUMED
        coss_v=np.array([0.1, 5, 10, 20, 50, 100, 200, 400, 600, 800, 1000.0]),
        coss_f=np.array([12000, 9400, 6400, 2800, 1930, 1370, 984, 685, 580, 543, 515.0]) * 1e-12,  # pdf16 Fig.17
        e_i=np.array([50.0, 100, 150, 160, 200, 250, 300, 350]),
        e_tab={600.0: {'eon': np.array([2.53, 3.58, 4.70, 4.92, 5.91, 7.09, 8.17, 9.27]) * 1e-3,     # pdf14 Fig.14
                       'eoff': np.array([0.73, 0.76, 0.83, 0.83, 0.89, 1.04, 1.26, 1.57]) * 1e-3,
                       'err': np.array([1.08, 1.02, 1.02, 1.02, 1.07, 1.04, 1.08, 1.10]) * 1e-3}},
        k_v={'eon': 1.7, 'eoff': 1.0, 'err': 1.0},
        e_T={'eon': ([25.0, 150.0], [1.0, 5.82 / 4.92]), 'eoff': ([25.0, 150.0], [1.0, 0.64 / 0.83]),
             'err': ([25.0, 150.0], [1.0, 1.86 / 1.02])},                      # Fig.16 / Fig.14 at 160 A
        e_rg={'eon': (np.array([0.4, 1, 2, 2.2, 3, 4, 5, 6, 7, 8]),
                      np.array([2.26, 3.00, 4.57, 4.91, 6.25, 7.90, 9.52, 11.32, 13.09, 14.84]) * 1e-3),
              'eoff': (np.array([0.4, 1, 2, 2.2, 3, 4, 5, 6, 7, 8]),
                       np.array([0.14, 0.34, 0.71, 0.83, 1.10, 1.48, 1.84, 2.28, 2.69, 3.10]) * 1e-3)},  # Fig.13
        e_test=dict(v=600.0, i=160.0, rg_on=2.2, rg_off=2.2, vgs=(15, -4), l_sigma=None),
        t_rg={'td_off': (np.array([2.2, 8.0]), np.array([42.0, 42.0 * 135.8 / 66.8])),   # table point 150 C (pdf7);
              'tf': (np.array([2.2, 8.0]), np.array([11.0, 11.0 * 31.5 / 26.2])),        # R_G slope of the BASiC
              'td_on': (np.array([2.2, 8.0]), np.array([18.0, 18.0 * 74.5 / 39.1]))},    # curves ASSUMED
        vsd_i=np.array([10.0, 25, 50, 100, 150, 200]),
        vsd_v={25: np.array([2.99, 3.26, 3.54, 3.95, 4.27, 4.50]),            # body diode, VGS -4 V, pdf10 Fig.7/8
               175: np.array([2.62, 2.87, 3.11, 3.46, 3.75, 3.94])},
        rth_jc=0.13, rth_ch=0.12, tj_max=175.0, vgs_max=15.0, vgs_min=-4.0, vth_min_25=1.8, vth_175=2.0,
        qg=536e-9, qgs=168e-9, qgd=212e-9, rg_int=1.5, qrr=2.14e-6, i_dm=350.0, l_stray=8e-9,
        i_term_rms=10 * 25.0, i_pos_rms=8 * 25.0, tsc=None, rails=(15.0, -4.0),     # no pin limit printed: the
                                                                                    # BASiC E2B 25 A/pin ASSUMED
        qualification='none stated; preliminary; chip source not stated',
        open_items=['price (RFQ)', 'final datasheet', 'Zth, RBSOA, stray L, pin current limit, terminal R',
                    'static V_GS max +15 V: needs its own +15/-4 V rail']),
}
# ----------------------------------------------------------------------------------------------------------
# Asian 1200 V TO-247-4 discretes (class b of sim/data/asia_devices.md).  Tables and digitised curves with page /
# figure references are in sim/data/dab_discrete_curves.py (datasheet extraction 2026-10-04, curve reads +/-5-10 %,
# its CAVEATS text applies); disc() maps them onto the generic device layer.  Choices made here (ASSUMPTIONS):
#  * energies at 25 C from E(I) at the vendor's gate resistor, scaled to ours by the vendor E(R_G) curve and to Tj by
#    the vendor E(Tj) curve; InventChip has E(I) only at 20 ohm (its table point at 2 ohm fixes the level)
#  * E_on is the vendor's hard-switching figure (includes the opposite device's Q_rr / C_oss energy, CAVEATS) - used
#    only for hard-switched edges; the diode's own recovery loss is estimated as 0.25 Q_rr V (Q_rr ~ sqrt(I))
#  * E_off: BASiC's includes the device's own E_oss (CAVEATS), which ZVS recovers -> E_oss subtracted; InventChip
#    (definition not stated) and Sichain (excludes it) are used as printed
#  * InventChip 3rd quadrant at the -3.5 V rail = mean of its -5 V and -2 V curves; Sichain -4 V curve; BASiC -5 V
#  * lead RMS rating = I_D(T_C = 100 C) of the datasheet (no package limit is printed by any of the three)
#  * short-circuit withstand: none of the three publishes one -> 2.0 us at 800 V / 25 C start ASSUMED for each
# ----------------------------------------------------------------------------------------------------------
def _load_disc_curves():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'dab_discrete_curves.py')
    sp_ = importlib.util.spec_from_file_location('dab_discrete_curves', path)
    m = importlib.util.module_from_spec(sp_)
    sp_.loader.exec_module(m)
    return m


_DC = _load_disc_curves()
DISC_CAVEATS = _DC.CAVEATS
_A = lambda dct: (np.array(sorted(dct), float), np.array([dct[k] for k in sorted(dct)], float))


def disc(mpn, maker, src, pages, e600, e800, e_test, e_rg, e_T, t_rg, vsd, qrr_ref, eoff_incl, rth_max, rg_int_min):
    c = getattr(_DC, mpn)
    rt, rv = _A(c[[k for k in c if k.startswith('rdson_mohm_vs_tj')][0]])
    cv, cf = _A(c['coss_pF_vs_vds_V'])
    ei = np.array(e800['i_A'] if e800 else e600['i_A'], float)
    tab = {}
    for v_, e_ in ((600.0, e600), (800.0, e800)):
        if e_:
            tab[v_] = {'eon': np.array(e_['eon_uJ'], float) * 1e-6, 'eoff': np.array(e_['eoff_uJ'], float) * 1e-6}
    vt = max(tab)
    q_i, q_v = qrr_ref                                     # Q_rr [C] at the reference current [A]
    for v_ in tab:
        tab[v_]['err'] = 0.25 * q_v * np.sqrt(ei / q_i) * v_
    gq = c['gate_charge_curve']
    d = dict(mpn=mpn, maker=maker, package=c['package'], src=src, pages=pages, kind='discrete',
             rds_T=rt, rds=rv * 1e-3, rds_max_25=c['rds_on_mohm']['max_25C_18V'] * 1e-3, r_pkg=0.0,
             coss_v=cv, coss_f=cf * 1e-12, e_i=ei, e_tab=tab, k_v={'eon': 1.6, 'eoff': 1.3, 'err': 1.0},
             e_T=e_T, e_rg=e_rg, e_test=e_test, eoff_includes_eoss=eoff_incl, t_rg=t_rg,
             vsd_i=vsd[0], vsd_v={25: vsd[1], 175: vsd[2]}, rth_jc=rth_max, tj_max=float(c['tj_max_C']),
             vgs_max=float(c['vgs_V']['abs_dc'][1]), vgs_min=float(c['vgs_V']['abs_dc'][0]),
             vth_min_25=c['vgs_th_V']['min_25C'], vth_175=c['vgs_th_V']['typ_175C'],
             qg=gq['qg_at_18V_nC'] * 1e-9, qgs=gq['q_plateau_start_nC'] * 1e-9,
             qgd=(gq['q_plateau_end_nC'] - gq['q_plateau_start_nC']) * 1e-9,
             rg_int=c['rg_int_ohm']['typ'], rg_int_min=rg_int_min, qrr=q_v, i_dm=float(c['i_d_pulse_A']),
             i_rms_lead=float(c['i_d_cont_A']['tc100C']), tsc=2.0e-6, qualification=c['qualification'] or
             'none stated in the datasheet', doc_rev=c['doc_rev'],
             open_items=['qualification report (HTRB/H3TRB/HTGB, threshold drift, body-diode degradation)',
                         'short-circuit withstand at 1000 V / 150 C', 'package / source inductance',
                         'FIT vs DC voltage (cosmic ray)', 'volume price'])
    return d


_ic, _sc, _ba = _DC.IV3Q12013T4Z, _DC.SG2M014120LJ, _DC.B3M013C120Z
_t = lambda c, key, sub=None: {k: (np.array(c[key]['rg_ext_ohm' if 'rg_ext_ohm' in c[key] else 'rg_ohm'], float),
                                   np.array((c[key][sub] if sub else c[key])[k + '_ns'], float))
                               for k in ('td_on', 'td_off', 'tf')}
_rg = lambda c, key, sub=None: {k: (np.array(c[key]['rg_ext_ohm' if 'rg_ext_ohm' in c[key] else 'rg_ohm'], float),
                                    np.array((c[key][sub] if sub else c[key])[k + '_uJ'], float) * 1e-6)
                                for k in ('eon', 'eoff')}
_tj = lambda c, key: {k: (np.array(c[key]['tj_C'], float), np.array(c[key][k + '_uJ'], float) / c[key][k + '_uJ'][0])
                      for k in ('eon', 'eoff')}
_vm5, _vm2 = _ic['vsd_V_vs_isd_A_vgs_m5V'], _ic['vsd_V_vs_isd_A_vgs_m2V']
DISC = {
    'IV3Q12013T4Z': disc('IV3Q12013T4Z', 'InventChip Technology (Shanghai Zhanxin, CN)', PS + 'IV3Q12013T4Z.pdf',
                         'p1-p7 (Rev 1.0, Jun 2024)', None, _ic['esw_vs_id_800V_20ohm_25C'],
                         dict(v=800.0, i=100.0, rg_on=20.0, rg_off=20.0, vgs=(18, -3.5)),
                         _rg(_ic, 'esw_vs_rg_800V_100A_25C'), _tj(_ic, 'esw_vs_tj_800V_100A_20ohm'),
                         _t(_ic, 'tsw_vs_rg_800V_100A_25C'),
                         (np.array([5.0, 10, 25, 50, 75, 100, 140]),
                          0.5 * (_A(_vm5['25C'])[1] + _A(_vm2['25C'])[1]), 0.5 * (_A(_vm5['175C'])[1] + _A(_vm2['175C'])[1])),
                         (100.0, 0.5 * 29 * 48e-9), False, 0.27, 0.5),
    'SG2M014120LJ': disc('SG2M014120LJ', 'SICHAIN Semiconductor (Ningbo, CN)', PS + 'SG2M014120LJ.pdf',
                         'p3-p13 (V02_04, 2025-12-25)', _sc['esw_vs_id_600V_2p5ohm_25C'], _sc['esw_vs_id_800V_2p5ohm_25C'],
                         dict(v=800.0, i=90.0, rg_on=2.5, rg_off=2.5, vgs=(18, -4)),
                         _rg(_sc, 'esw_vs_rg_800V_90A_25C'), _tj(_sc, 'esw_vs_tj_800V_90A_2p5ohm'),
                         _t(_sc, 'tsw_vs_rg_800V_90A_25C'),
                         (_A(_sc['vsd_V_vs_isd_A_vgs_m4V']['25C'])[0], _A(_sc['vsd_V_vs_isd_A_vgs_m4V']['25C'])[1],
                          _A(_sc['vsd_V_vs_isd_A_vgs_m4V']['175C'])[1]),
                         (90.0, 1349e-9), False, 0.26, 2.6),
    'B3M013C120Z': disc('B3M013C120Z', 'BASiC Semiconductor (CN)', PS + 'B3M013C120Z.pdf', 'p2-p12 (Rev 1.0, 2026-05-08)',
                        _ba['esw_vs_id_8p2ohm']['600V_25C'] | {'i_A': _ba['esw_vs_id_8p2ohm']['i_A']},
                        _ba['esw_vs_id_8p2ohm']['800V_25C'] | {'i_A': _ba['esw_vs_id_8p2ohm']['i_A']},
                        dict(v=800.0, i=60.0, rg_on=8.2, rg_off=8.2, vgs=(18, -5)),
                        _rg(_ba, 'esw_vs_rg_800V_60A', '25C'), _tj(_ba, 'esw_vs_tj_800V_60A_8p2ohm'),
                        _t(_ba, 'tsw_vs_rg_800V_60A', '25C'),
                        (_A(_ba['vsd_V_vs_isd_A_vgs_m5V']['25C'])[0], _A(_ba['vsd_V_vs_isd_A_vgs_m5V']['25C'])[1],
                         _A(_ba['vsd_V_vs_isd_A_vgs_m5V']['175C'])[1]),
                        (60.0, 1150e-9), True, 0.20, 1.4),
}
DISC['B3M013C120Z']['open_items'] = DISC['B3M013C120Z']['open_items'] + ['R_th j-c maximum (typ 0.20 only)']

# Insulator for TO-247-4 discretes on the earthed cold plate (D-032: basic HV-PE, >= 5.5 mm overhang at 2000 m).
# Xiamen Innovacera AlN pad 40 x 28 x 1.0 mm (thermal/Innovacera-AlN-thermal-pads.pdf): k >= 170 W/mK (p3),
# 15-20 kV/mm (p3), eps_r 8.8 (p3), sizes p4, USD 1.50 at 1k EXW Dec-2023 list (p7); overhang on a 15.9 x 20.9 mm tab
# 6.05 / 9.55 mm.  Grease Shin-Etsu G-779 (thermal/Shin-Etsu-G-779.pdf p1): 0.10 K cm2/W at 25 um.  Stack (case -
# grease - AlN - grease - plate) over the 3.32 cm2 tab: 0.030 + 0.018 + 0.030 = 0.078 K/W at 25 um; 0.13 K/W at a
# 50 um bond line is used (CALCULATED from those data, no maker Rth for the mounting).  PD / hipot / impulse of the
# assembled stack are NOT stated by the maker: type test required (PD <= 10 pC at 1799 Vpk, 2200 V rms, 6 kV).
INSULATOR = {'mpn': 'Innovacera AlN 40 x 28 x 1.0 mm (no hole, clip-mounted)', 'maker': 'Xiamen Innovacera (CN)',
             'src': 'docs/datasheets/thermal/Innovacera-AlN-thermal-pads.pdf p3, p4, p7',
             'grease': 'Shin-Etsu G-779, docs/datasheets/thermal/Shin-Etsu-G-779.pdf p1',
             'k': 170.0, 't': 1.0e-3, 'eps_r': 8.8, 'size_mm': (40.0, 28.0), 'tab_mm': (15.9, 20.9),
             'overhang_mm': (6.05, 9.55), 'rth_stack': 0.13, 'rth_stack_25um': 0.078,
             'c_tab': 8.854e-12 * 8.8 * 15.9e-3 * 20.9e-3 / 1.0e-3,
             'price': 1.50 + 0.25, 'price_src': 'pad USD 1.50 at 1k (Innovacera list p7, EXW Dec-2023) + clip and '
                                                'grease 0.25 USD (ESTIMATE, no price found)'}

# HF decoupling capacitors at the switches (DR-07).  KEMET (YAGEO, TW) KC-LINK CKC33C473KEGACAUTO 47 nF 1200 V C0G
# 3640 (passives-capacitors/KEMET-KC-LINK.pdf): ripple ~13 A rms at 0.7-1 MHz, ~10.5 A at 100 kHz, TYPICAL curve,
# heatsink-mounted, 150 C cap (p2); DWV 1440 V (p6); -55..+150 C, AEC-Q200 (p1, p3); no dV/dt or peak rating.
# The CRD part KEMET LDEQG2470KA5N00 (PEN film 5040, KEMET-LDE.pdf) is OBSOLETE, 1000 V, ~2.5 A rms at 1 MHz (p5),
# 300 V/us (p3): unusable here.  KEMET R76 1600 V film 47 nF: ~6 A rms but ESL 10-16 nH (KEMET-R76.pdf p19) - not a
# HF decoupling part.  ESR/ESL of the KC-LINK part are not tabulated: 5 mOhm / 1 nH ASSUMED per part (C0G 3640).
HF_CAP = {'mpn': 'CKC33C473KEGACAUTO', 'maker': 'KEMET (YAGEO Group, TW)', 'c': 47e-9, 'v': 1200.0, 'dielectric': 'C0G',
          'case': '3640', 'i_rms_typ_1MHz': 13.0, 'i_rms_rule': 0.7 * 13.0, 'esr': 5e-3, 'esl': 1.0e-9,
          'src': 'docs/datasheets/passives-capacitors/KEMET-KC-LINK.pdf p2, p3, p5, p6',
          'price': 7.9315, 'price_src': 'LCSC C3854232 100+ (5 in stock); Mouser 7.20 USD at 100 (1,698 stock), '
                                        'agent reading 2026-10-04 - not yet in gen/data/prices.csv'}


# Prices used by the option study: (USD per piece at the highest break <= 1000 pcs, source).  Same method as
# gen/cost.py; every row is in gen/data/prices.csv unless marked otherwise.
PRICES = {
    'CBB011M12GM4T': (201.46, 'findchips aggregator (Richardson RFPD), 1+ shown, 2026-10-04 (prices.csv)'),
    'SG2M014120LJ': (7.3119, 'LCSC C52109935 via jlcsearch mirror, 90+, 43 in stock, 2026-10-04 (prices.csv)'),
    'CRXQF17M120G2Z': (6.8115, 'LCSC C41507149, 100+, 640 in stock, 2026-10-04 (prices.csv)'),
    'IV3Q12013T4Z': None,          # RFQ - no public price (asia_devices.md)
    'B3M013C120Z': None,           # RFQ - TME lists it, price not readable
    'BMF008MR12E2G3': None, 'DFS08HF12EZA2': None, 'BMF240R12E2G3': None,
}


# Reverse-clamp candidates (cost re-opened 2026-10-04).  Per candidate: n_par devices in parallel per port (filled by
# the study), V_T0 / r_T per device, surge ratings per device, price per device (highest break <= 1000 pcs).
CLAMP_OPTIONS = {
    'DD600N16K': dict(CLAMP, maker='Infineon (DE)', asian=False, n=1, i2t_10ms=CLAMP['i2t_hot'],
                      ifsm_10ms=CLAMP['ifsm_hot'], price=392.36,
                      price_src='Cytech Systems broker, 100+ (gen/data/prices.csv, 2026-10-04)'),
    'VS-60EPS16-M3': dict(mpn='VS-60EPS16-M3', maker='Vishay (US)', asian=False, src=PS + 'VS-60EPS16-M3.pdf',
                          vrrm=1600.0, vt0=0.74, rt=3.96e-3,                       # p2 table V_F(TO), r_t (Tj max)
                          ifsm_10ms=950.0, i2t_10ms=4525.0, i2sqrt_t=45250.0,      # p1, no voltage reapplied
                          ir_hot=None, rth_jc=0.35, package='TO-247AC 2L', price=2.674,
                          price_src='LCSC C506457, 1000+ (gen/data/prices.csv, 2026-10-04)'),
    'MDC600-16-416F3': dict(mpn='MDC600-16-416F3', maker='Techsem (Hubei TECH, CN)', asian=True,
                            src=PS + 'MDC600-16-416F3.pdf', vrrm=1600.0, vt0=0.75, rt=0.28e-3,   # p1, 150 C max values
                            ifsm_10ms=19.0e3, i2t_10ms=1805e3,             # p1, 150 C, 60 % V_RRM reapplied
                            i2t_curve=(np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10.0]) * 1e-3,
                                       np.array([631, 830, 970, 1117, 1246, 1369, 1492, 1600, 1712, 1804.0]) * 1e3),  # Fig.8
                            zth_r=(0.00192, 0.00444, 0.01339, 0.02981, 0.01530),        # Foster fit of Fig.2 p2
                            zth_tau=(0.000230, 0.00445, 0.0459, 0.259, 1.215),          # (fit by the extraction pass)
                            ir_hot=45e-3, rth_jc=0.065, package='416F3 60 mm module, M10, same footprint family and '
                            'terminal numbering as DD600N (1 = midpoint, 2 = D1 cathode, 3 = D2 anode)',
                            price=None, price_src='RFQ (no public price)'),
    'WND75P16W6': dict(mpn='WND75P16W6', maker='WeEn Semiconductors (CN)', asian=True, src=PS + 'WND75P16W6.pdf',
                       vrrm=1600.0, vt0=0.75, rt=(1.15 - 0.75) / 75.0,   # r_T from V_F max 1.15 V at 75 A (p2) with
                       ifsm_10ms=1050.0, i2t_10ms=5513.0,                 # V_T0 0.75 V ASSUMED (no V_T0/r_T printed)
                       rth_jc=0.2, package='TO-247-2L', price=None, price_src='RFQ (not on LCSC)'),
}


# ----------------------------------------------------------------------------------------------------------
# Small helpers (vectorised)
# ----------------------------------------------------------------------------------------------------------
def rds_on(tj, vgs18=False):
    """Channel RDS(on) [ohm] vs Tj: quadratic through the three p2 table points (VGS 15 V or 18 V)."""
    tab = MODULE['rds_on_18v' if vgs18 else 'rds_on']
    t, r = np.array(list(tab)), np.array(list(tab.values()))
    return np.polyval(np.polyfit(t, r, 2), tj)


def r_pkg(ths):
    """Package resistance per switch position [ohm] (mean of HS/LS) vs heatsink temperature."""
    r125 = 0.5 * (MODULE['r_pkg_hs'] + MODULE['r_pkg_ls'])
    return r125 * (1 + MODULE['r_pkg_tc'] * (np.asarray(ths) - 125.0))


# Body diode V_SD(I) at VGS = -4 V, Fig. 7 p5.  1/5/10/100 A: vec (AUDIT row vsd_knee / vsd_100A: 5.98 and 5.39 V
# at 100 A on the curve, table 5.8/5.4 V); 25/50 A at 25 C and 25 A at 175 C: back-calculated from the audit's stated
# deviation of our former linear model (-10.7 %/-10.0 %/-10.3 %); 50 A at 175 C interpolated with the 25 C shape.
VSD_I = np.array([1.0, 5.0, 10.0, 25.0, 50.0, 100.0])
VSD_V = {25: np.array([2.89, 3.45, 3.78, 4.40, 5.06, 5.98]), 175: np.array([2.54, 3.03, 3.35, 3.93, 4.54, 5.39])}


def vsd(i, tj):
    """Body-diode drop [V] at VGS=-4 V: Fig. 7 table, log-linear in current (linear above 100 A), linear in Tj."""
    i = np.maximum(np.abs(np.asarray(i, float)), 1.0)
    w = np.clip((np.asarray(tj, float) - 25.0) / 150.0, 0, 1)
    out = []
    for t in (25, 175):
        v = np.interp(np.log(np.minimum(i, 100.0)), np.log(VSD_I), VSD_V[t])
        out.append(v + np.maximum(i - 100.0, 0) * (VSD_V[t][-1] - VSD_V[t][-2]) / 50.0)
    return (1 - w) * out[0] + w * out[1]


def _tfac(kind, tj):
    d = E_TFAC[kind]
    return np.interp(tj, list(d), list(d.values()))


def _lin(x, xp, fp):
    """np.interp with linear extrapolation at both ends."""
    x = np.asarray(x, float)
    y = np.interp(x, xp, fp)
    y = np.where(x < xp[0], fp[0] + (x - xp[0]) * (fp[1] - fp[0]) / (xp[1] - xp[0]), y)
    return np.where(x > xp[-1], fp[-1] + (x - xp[-1]) * (fp[-1] - fp[-2]) / (xp[-1] - xp[-2]), y)


def esw(kind, i, v, tj):
    """DPT switching energy [J] for kind in eon/eoff/err at |i| [A], bus v [V], Tj [C].
    Current: linear interpolation of Figs. 11/12 (extrapolated linearly to 0 A and beyond 200 A).
    Voltage: power law between the 600 V and 800 V curves, E ~ V^k, k clamped to 0.7..2 (ASSUMPTION: the
    low-current 600 V reads are small and noisy).  Temperature: p2/p3 table ratios at 600 V/100 A applied
    to all currents (ASSUMPTION)."""
    i = np.abs(np.asarray(i, float))
    e6 = np.maximum(_lin(i, E_I, E_TAB[600][kind]), 1e-6)
    e8 = np.maximum(_lin(i, E_I, E_TAB[800][kind]), 1e-6)
    k = np.clip(np.log(e8 / e6) / np.log(800 / 600), 0.7, 2.0)
    return e8 * (np.asarray(v, float) / 800.0) ** k * _tfac(kind, tj)


def coss(v):
    return np.interp(v, COSS_V, COSS_F)


_VG = np.linspace(0, 1200, 2401)
_QG = np.concatenate([[0], np.cumsum(0.5 * (coss(_VG[1:]) + coss(_VG[:-1])) * np.diff(_VG))])
_EG = np.concatenate([[0], np.cumsum(0.5 * (coss(_VG[1:]) * _VG[1:] + coss(_VG[:-1]) * _VG[:-1]) * np.diff(_VG))])


def qoss(v):
    """Charge stored in C_oss of one switch charged to v [C] (integral of Fig. 9)."""
    return np.interp(v, _VG, _QG)


def eoss(v):
    """Energy stored in C_oss of one switch charged to v [J]."""
    return np.interp(v, _VG, _EG)


def cosmic_fit(v, gen=4, area_cm2=None):
    """Sea-level FIT for one die area at DC blocking voltage v (log-interpolated WP Fig. 4)."""
    vv, ff = (COSMIC['gen4_v'], COSMIC['gen4_fit']) if gen == 4 else (COSMIC['gen3_v'], COSMIC['gen3_fit'])
    a = COSMIC['die_area_cm2'] if area_cm2 is None else area_cm2
    return a * 10 ** np.interp(v, vv, np.log10(ff), left=-6, right=np.log10(ff[-1]))


def pv_sine(material, b_pk, temp):
    """Sinusoidal core-loss density [W/m^3] at 100 kHz: log-log in B (end slopes extrapolated), linear in T."""
    m = FERRITE[material]
    lbB, lP = np.log(m['B']), np.log(m['pv'])                       # lP: (nT, nB)
    lb = np.log(np.clip(np.asarray(b_pk, float), 1e-4, None))
    rows = np.stack([_lin(lb, lbB, lP[j]) for j in range(len(m['T']))], axis=-1)   # (..., nT)
    t = np.clip(np.broadcast_to(np.asarray(temp, float), lb.shape), m['T'][0], m['T'][-1])
    j = np.clip(np.searchsorted(m['T'], t) - 1, 0, len(m['T']) - 2)
    w = (t - m['T'][j]) / (m['T'][j + 1] - m['T'][j])
    r0 = np.take_along_axis(rows, j[..., None], -1)[..., 0]
    r1 = np.take_along_axis(rows, (j + 1)[..., None], -1)[..., 0]
    return np.exp((1 - w) * r0 + w * r1)


# ==========================================================================================================
# Generic device layer (SRC-1..4 device study, 2026-10-04).  One dict per part; every curve value is a reading of
# the filed datasheet (page / figure in the comment, 'fig' = read from the figure, ~+/-5-10 %).  Functions below work
# on any of these dicts; the incumbent CBB011M12GM4T keeps its own functions above (results unchanged).
# ==========================================================================================================


# Isolated gate driver NOVOSENSE NSI6651A (D-035).  Industrial grade NSI6651ASC-DSWR, -40..125 C (NSI66x1A.pdf
# Rev 1.1) is the design basis; the AEC-Q100 grade -Q1SWR (NSI66x1A-Q1.pdf Rev 1.2, -40..150 C) has wider limits
# (t_pd 40-130 ns, PWD 50 ns, DESAT 8.5-10 V, deglitch 100-320 ns, DESAT->OUT 150-360 ns, I_STO 100-570 mA).
NSI6651 = {
    'mpn': 'NSI6651ASC-DSWR', 'src': GD + 'NSI66x1A.pdf', 'src_q1': GD + 'NSI66x1A-Q1.pdf',
    'i_pk': 10.0,                       # A peak source and sink (p1 features; same text Q1 p1)
    'roh': 2.2, 'rol': 0.3,             # ohm output pull-up / pull-down resistance, typ (industrial p7, Q1 p7)
    'roh_eff': 1.0,                     # ohm effective pull-up during switching: N-channel boost in parallel with the
                                        # P-channel (Q1 p20 sec. 8.1 text, no value given) - ASSUMPTION
    'tpd': (70e-9, 80e-9, 110e-9),      # s propagation delay min/typ/max, -40..125 C (industrial p9)
    'pwd': 30e-9,                       # s pulse-width distortion max (industrial p9)
    'tpd_q1': (40e-9, 85e-9, 130e-9), 'pwd_q1': 50e-9,                        # Q1 p10
    'v_desat': (8.5, 9.26, 9.8),        # V DESAT threshold (industrial p8); Q1: 8.5/9.3/10 (p9)
    'i_chg': (430e-6, 500e-6, 600e-6),  # A blanking-capacitor charge current (industrial p8); Q1 350-650 uA (p9)
    't_leb': 200e-9,                    # s leading-edge blanking, typ only, 'not test covered' (Q1 p9)
    't_fil': (150e-9, 200e-9, 265e-9),  # s DESAT deglitch filter (industrial p8); Q1 100-320 ns (p9)
    't_off': (150e-9, 250e-9, 300e-9),  # s DESAT sense to OUT(L) 90 % (industrial p8); Q1 150-360 ns (p9)
    'i_sto': (0.25, 0.40, 0.57),        # A soft turn-off current (industrial p8); Q1 0.10/0.40/0.57 A (p9)
    'cmti': 150e9,                      # V/s min (Q1 p10)
    'en_low': 'RST/EN low pulls OUT low through OUTL (Q1 p28 functional-mode table) - a normal, not soft, turn-off',
    'soft_off_only_on_desat': 'soft turn-off is triggered only by a DESAT fault (Q1 p24 sec. 8.9)',
}
DESIGN_RAIL_NOTE = '+18 / -3.5 V: InventChip Gen3 allows -5 V DC and recommends -3.5..-2 V off; Sichain recommends -4 V'


def _qe_grid(d):
    """Cumulative Q_oss(V) and E_oss(V) grids of one device from its C_oss(V) table (log-log interpolation)."""
    v = np.linspace(0, 1200, 2401)
    lv, lc = np.log(np.maximum(d['coss_v'], 0.1)), np.log(d['coss_f'])
    c = np.exp(np.interp(np.log(np.maximum(v, 0.1)), lv, lc))
    q = np.concatenate([[0], np.cumsum(0.5 * (c[1:] + c[:-1]) * np.diff(v))])
    e = np.concatenate([[0], np.cumsum(0.5 * (c[1:] * v[1:] + c[:-1] * v[:-1]) * np.diff(v))])
    d['_v'], d['_q'], d['_e'] = v, q, e
    return d


def g_qoss(d, v):
    return np.interp(v, d['_v'], d['_q'])


def g_eoss(d, v):
    return np.interp(v, d['_v'], d['_e'])


def g_rds(d, tj):
    """Channel R_DS(on) [ohm] at the on-rail of the design (datasheet typ curve), linear in Tj, extrapolated."""
    return _lin(np.clip(tj, -55.0, 200.0), d['rds_T'], d['rds'])


def g_vsd(d, i, tj):
    """Body-diode (or SBD) drop [V] at the off-rail of the design, log-linear in current, linear in Tj."""
    i = np.maximum(np.abs(np.asarray(i, float)), d['vsd_i'][0])
    w = np.clip((np.asarray(tj, float) - 25.0) / 150.0, 0, 1)
    out = []
    for t in (25, 175):
        v = np.interp(np.log(np.minimum(i, d['vsd_i'][-1])), np.log(d['vsd_i']), d['vsd_v'][t])
        out.append(v + np.maximum(i - d['vsd_i'][-1], 0) * (d['vsd_v'][t][-1] - d['vsd_v'][t][-2]) /
                   (d['vsd_i'][-1] - d['vsd_i'][-2]))
    return (1 - w) * out[0] + w * out[1]


def g_esw(d, kind, i, v, tj, rg_eff=None):
    """Switching energy [J] of ONE device: kind eon/eoff/err at |i| [A], bus v [V], Tj [C].
    Current: the datasheet E(I) reading at its test voltage (linear, extrapolated); voltage: power law between two
    curves if the datasheet has two, else the exponent d['k_v'] (ASSUMPTION stated there); temperature: E(Tj) figure
    ratio; gate resistance: E(R_G) figure ratio between rg_eff (external + driver share, per device) and the test
    R_G (None = no scaling)."""
    i = np.abs(np.asarray(i, float))
    tab = d['e_tab']
    vs = sorted(tab)
    e_hi = np.maximum(_lin(i, d['e_i'], tab[vs[-1]][kind]), 1e-7)
    if len(vs) > 1:
        e_lo = np.maximum(_lin(i, d['e_i'], tab[vs[0]][kind]), 1e-7)
        k = np.clip(np.log(e_hi / e_lo) / np.log(vs[-1] / vs[0]), 0.7, 2.0)
    else:
        k = d['k_v'][kind]
    e = e_hi * (np.asarray(v, float) / vs[-1]) ** k
    tt, ff = d['e_T'].get(kind, ([25.0, 175.0], [1.0, 1.0]))
    e = e * np.interp(tj, tt, ff)
    key = 'eoff' if kind == 'eoff' else 'eon'
    if rg_eff is not None and key in d.get('e_rg', {}):
        rr, ee = d['e_rg'][key]
        e = e * np.interp(rg_eff, rr, ee) / np.interp(d['e_test']['rg_' + key[1:]], rr, ee)
    if kind == 'eoff' and d.get('eoff_includes_eoss'):
        e = np.maximum(e - g_eoss(d, v), 0.1 * e)
    return e


def composite(name, parts, rge_on, rge_off):
    """Per-parameter worst case of several makers' parts at the design gate resistances (design basis 'worse of the
    two makers'): energies are each maker's own curves at its own E(R_G) ratio, then the larger is taken."""
    a = parts[0]
    d = dict(a, mpn=name, maker=' / '.join(p['maker'] for p in parts), src=' + '.join(p['src'] for p in parts))
    d['rds_T'] = np.array([25.0, 75.0, 100.0, 125.0, 150.0, 175.0])
    d['rds'] = np.max([g_rds(p, d['rds_T']) for p in parts], 0)
    d['rds_max_25'] = max(p['rds_max_25'] for p in parts)
    d['coss_v'] = np.array([0.1, 1, 5, 10, 20, 50, 100, 200, 400, 600, 800, 1000.0])
    d['coss_f'] = np.max([np.exp(np.interp(np.log(d['coss_v']), np.log(np.maximum(p['coss_v'], 0.1)),
                                           np.log(p['coss_f']))) for p in parts], 0)
    d['e_i'] = np.array([10.0, 20, 40, 60, 80, 100, 120])
    rg = {'eon': rge_on, 'err': rge_on, 'eoff': rge_off}
    d['e_tab'] = {800.0: {k: np.max([g_esw(p, k, d['e_i'], 800.0, 25.0, rg[k]) for p in parts], 0)
                          for k in ('eon', 'eoff', 'err')}}
    d['k_v'] = {k: max((np.clip(np.log(g_esw(p, k, 100.0, 800.0, 25.0) / g_esw(p, k, 100.0, 600.0, 25.0)) /
                                np.log(800 / 600), 0.7, 2.0)) for p in parts) for k in ('eon', 'eoff', 'err')}
    d['e_T'] = {k: ([25.0, 175.0], [1.0, max(float(np.interp(175.0, *p['e_T'].get(k, ([25, 175], [1, 1]))))
                                             for p in parts)]) for k in ('eon', 'eoff', 'err')}
    d['rg_int_min'] = min(p.get('rg_int_min', p['rg_int']) for p in parts)
    d['eoff_includes_eoss'] = False                 # already subtracted per maker above
    d['e_rg'] = {}
    d['vsd_i'] = np.array([5.0, 10, 25, 50, 75, 100, 150])
    d['vsd_v'] = {t: np.max([g_vsd(p, d['vsd_i'], t) for p in parts], 0) for t in (25, 175)}
    for k in ('rth_jc', 'qg', 'qrr', 'r_pkg', 'l_stray'):
        d[k] = max((p.get(k) or 0.0) for p in parts)
    for k in ('qgs', 'qgd'):                     # not tabulated by every maker: 30 % of Q_g each (ASSUMPTION)
        d[k] = max((p.get(k) or 0.3 * p['qg']) for p in parts)
    for k in ('vth_min_25', 'vth_175', 'tj_max', 'vgs_max', 'i_dm'):
        d[k] = min(p[k] for p in parts)
    for k in ('i_rms_lead', 'i_term_rms'):
        if all(k in p for p in parts):
            d[k] = min(p[k] for p in parts)
    d['rg_int'] = min(p['rg_int'] for p in parts)
    return _qe_grid(d)


for _d in list(AMOD.values()) + list(DISC.values()):
    _qe_grid(_d)


if __name__ == '__main__':
    # self-check: the fits must reproduce the table values they were built from
    assert abs(rds_on(25) - 11.0e-3) < 1e-6 and abs(rds_on(150) - 17.6e-3) < 1e-6
    assert abs(coss(800) - MODULE['coss_800']) / MODULE['coss_800'] < 0.05
    assert abs(esw('eoff', 100, 600, 25) - 0.17e-3) < 0.01e-3          # p2 table 0.17 mJ
    assert abs(esw('err', 100, 800, 25) - 0.447e-3) < 0.02e-3          # Fig. 14 p6: 0.447 mJ
    assert abs(vsd(100, 25) - 5.98) < 0.01 and abs(vsd(25, 25) - 4.40) < 0.01 and vsd(50, 175) < vsd(50, 25)
    assert abs(esw('eon', 100, 600, 25) - MODULE['eon_600_100'][25]) < 0.1e-3
    assert abs(pv_sine('N95', 0.2, 100) - 347e3) < 10e3                  # p2 table 350 kW/m3
    assert abs(pv_sine('N97', 0.2, 100) - FERRITE['N97']['pv_table']['100k_200mT_100C']) < 15e3
    assert 0.5 < cosmic_fit(950, area_cm2=1.0) < 3                       # ~1.7 FIT/cm2 at 950 V (Gen 4)
    assert abs(qoss_fit(800) / qoss(800) - 1) < 0.035                    # SPICE fit vs table
    print(f"Qoss(800)={qoss(800)*1e9:.0f} nC  Eoss(800)={eoss(800)*1e6:.0f} uJ  "
          f"Eoff_DPT(20 A, 800 V)={esw('eoff', 20, 800, 25)*1e6:.0f} uJ  (datasheet inconsistency: Eoff < Eoss)")
    print('dab_devices self-check passed')
