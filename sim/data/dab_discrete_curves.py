"""Datasheet data of the three 1200 V 13 mOhm TO-247-4 candidates for the DAB, transcribed by a helper agent
(tables + curves read from the figures at pixel level, about +/-5-10 %; page references in the comments).
Typical values only; nothing measured. Caveats are listed at the end of this file. Not imported by anything yet -
the DAB engineer moves what is needed into sim/dab_devices.py."""
IV3Q12013T4Z = {  # InventChip, 1200 V 13.5 mOhm Gen3, TO247-4 (Kelvin source)
    # ---------------- A. table values ----------------
    'doc_rev': 'Rev1.0, Jun. 2024',  # p1-p9 header/footer (PDF metadata created 2025-10-28)
    'qualification': 'AEC-Q101 qualified',  # p1 Features
    'package': 'TO247-4 (JEDEC TO247 var. AD); no package/source inductance stated',  # p1, p8
    'v_dss_V': 1200,  # p1 table (VGS=0 V, ID=100 uA)
    'i_d_cont_A': {'tc25C': 147, 'tc100C': 106, 'vgs_V': 18, 'package_limit_stmt': None},  # p1 table
    'i_d_pulse_A': 367,  # p1 table (pulse width limited by SOA)
    'i_s_pulse_A': 367,  # p1 table (limited by SOA and dynamic Rth)
    'i_s_cont_A': {'tc25C': 104, 'tc100C': 62, 'vgs_V': -2},  # p3 table
    'p_tot_W': 553,  # p1 table (Tc=25 C)
    'tj_max_C': 175,  # p1 table (Tj, Tstg -55..175 C)
    'rth_jc_K_W': {'value': 0.27, 'typ_or_max': 'not specified'},  # p1 table
    'rds_on_mohm': {'typ_25C_18V': 13.5, 'max_25C_18V': 17.0, 'typ_175C_18V': 22.5, 'typ_25C_15V': 17.0, 'typ_175C_15V': 24.5, 'id_test_A': 60},  # p2 table
    'vgs_th_V': {'min_25C': 2.0, 'typ_25C': 2.8, 'max_25C': 4.0, 'typ_175C': 2.0, 'min_175C': None, 'cond': 'VGS=VDS, ID=20 mA'},  # p2 table
    'vgs_V': {'rec_on': (15, 18), 'rec_off': (-3.5, -2), 'abs_dc': (-5, 20), 'abs_transient': (-10, 23), 'transient_cond': 'duty<1 %, tp<200 ns'},  # p1 table
    'cap_pF': {'ciss': 4774, 'coss': 211, 'crss': 8.1, 'vds_V': 800, 'cond': 'VGS=0, f=100 kHz, VAC=25 mV'},  # p2 table
    'eoss_uJ': {'value': 88, 'vds_V': 800},  # p2 table (VDS taken from the shared C condition cell)
    'gate_charge_nC': {'qg': 187, 'qgs': 66, 'qgd': 68, 'cond': 'VDS=800 V, ID=100 A, VGS=-3..+18 V'},  # p2 table
    'rg_int_ohm': {'min': 0.5, 'typ': 2.3, 'max': 5.0, 'cond': 'f=1 MHz'},  # p2 table
    'sw_25C': {'eon_uJ': 1480, 'eoff_uJ': 430, 'td_on_ns': 22, 'tr_ns': 33, 'td_off_ns': 32, 'tf_ns': 9.5},  # p2 table
    'sw_cond': {'vdd_V': 800, 'id_A': 100, 'rg_ext_ohm': 2.0, 'vgs_V': (-3.5, 18), 'l_load_uH': 200, 'l_stray_nH': None, 'tj_C': 25},  # p2 table
    'eon_includes_diode_rr': 'not stated; FWD=3Q12013T4Z (same part) in figure legends => implied',  # p2 table, p6 Fig.19-22
    'sw_hot': None,  # p2 table has no hot switching data (see Fig.22)
    'vsd_V': {'typ_25C': 3.3, 'typ_175C': 3.0, 'max': None, 'isd_A': 30, 'vgs_V': 0},  # p3 table
    'reverse_recovery': {'trr_ns': 48, 'qrr_nC': 289, 'irrm_A': 29, 'isd_A': 100, 'vr_V': 800, 'didt_A_per_us': 3000, 'rg_ext_ohm': 10, 'l_uH': 200, 'vgs_V': (-3.5, 18), 'tj_C': None},  # p3 table (Tj not stated; header Tc=25 C)
    'short_circuit_rating': None,  # not given
    'avalanche_rating': None,  # not given
    # ---------------- B. curves (read from figure, about +/-5-10 %) ----------------
    'rdson_norm_vs_tj_18V_60A': {25: 1.00, 50: 1.045, 75: 1.12, 100: 1.21, 125: 1.35, 150: 1.50, 175: 1.67},  # p4 Fig.5 (read from figure, ~+/-5-10 %)
    'rdson_mohm_vs_tj_18V_60A': {25: 13.4, 50: 14.0, 75: 15.0, 100: 16.4, 125: 18.2, 150: 20.0, 175: 22.4},  # p3 Fig.4 (read from figure, ~+/-5-10 %)
    'vgs_th_V_vs_tj': {25: 2.80, 175: 1.97},  # p4 Fig.9 (read from figure)
    'coss_pF_vs_vds_V': {4: 4400, 5: 3600, 10: 2500, 20: 1800, 50: 840, 100: 590, 200: 407, 400: 293, 600: 240, 800: 209, 1000: 194},  # p5 Fig.16 (read from figure; 0.6 px/V: <=20 V +/-20-30 %, 0-3 V unreadable, ~5000-9000 pF at the axis)
    'crss_pF_vs_vds_V': {3: 750, 5: 350, 10: 180, 20: 105, 50: 25.5, 100: 18.0, 200: 13.3, 400: 10.0, 600: 8.6, 800: 7.8, 1000: 7.6},  # p5 Fig.16 (read from figure; steep step ~100->40 pF at 23-27 V)
    'ciss_pF_vs_vds': 4800,  # p5 Fig.16 (flat ~4700-4900 pF, 10-1000 V)
    'eoss_uJ_vs_vds_V': {100: 4.4, 200: 11.5, 400: 32.1, 600: 57.8, 800: 89.9, 1000: 125.1},  # p6 Fig.17 (read from figure, ~+/-5 %)
    'esw_vs_id_800V_20ohm_25C': {  # p6 Fig.21 (read from figure; VGS -3.5/+18 V, L=200 uH, FWD=same part; only VDD=800 V and only RG=20 ohm plotted)
        'i_A':     [20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160],  # p6 Fig.21
        'eon_uJ':  [1030, 1420, 1820, 2230, 2640, 3030, 3450, 3800, 4140, 4450, 4720, 4960, 5120, 5260, 5350],  # p6 Fig.21
        'eoff_uJ': [310, 470, 670, 910, 1170, 1440, 1700, 1990, 2260, 2520, 2770, 2950, 3240, 3460, 3660],  # p6 Fig.21
        'etot_uJ': [1330, 1870, 2480, 3130, 3800, 4490, 5130, 5780, 6410, 6980, 7490, 7970, 8360, 8720, 9000]},  # p6 Fig.21
    'esw_vs_rg_800V_100A_25C': {  # p6 Fig.19 (read from figure; VGS -3.5/+18 V, L=200 uH, FWD=same part)
        'rg_ext_ohm': [2, 5, 7, 10, 12, 15, 17, 20, 22, 25, 27, 30, 32, 35, 37, 40, 42],  # p6 Fig.19
        'eon_uJ':  [1520, 2040, 2370, 2870, 3220, 3680, 3950, 4200, 4440, 4850, 5070, 5330, 5550, 5900, 6150, 6520, 6770],  # p6 Fig.19
        'eoff_uJ': [450, 740, 920, 1220, 1460, 1820, 2040, 2340, 2550, 2850, 3030, 3300, 3500, 3780, 3960, 4260, 4460],  # p6 Fig.19
        'etot_uJ': [1970, 2770, 3270, 4090, 4660, 5480, 5940, 6530, 7010, 7670, 8050, 8640, 9040, 9660, 10100, 10780, 11200]},  # p6 Fig.19
    'esw_vs_tj_800V_100A_20ohm': {  # p6 Fig.22 (read from figure; VGS -3.5/+18 V, L=200 uH)
        'tj_C': [25, 50, 75, 100, 125, 150, 175],  # p6 Fig.22
        'eon_uJ': [4180, 4090, 4000, 3930, 3990, 4120, 4270],  # p6 Fig.22
        'eoff_uJ': [2340, 2310, 2280, 2250, 2240, 2300, 2310],  # p6 Fig.22
        'etot_uJ': [6540, 6420, 6300, 6180, 6230, 6430, 6590]},  # p6 Fig.22
    'tsw_vs_rg_800V_100A_25C': {  # p6 Fig.20 (read from figure; td_on crosses td_off at ~12 ohm, td_on crosses tr at ~5 ohm)
        'rg_ext_ohm': [2, 7, 12, 17, 22, 27, 32, 37, 42],  # p6 Fig.20
        'td_on_ns': [23, 45, 73, 106, 134, 162, 185, 210, 232],  # p6 Fig.20
        'tr_ns': [33, 40, 46, 50, 56, 63, 71, 79, 86],  # p6 Fig.20
        'td_off_ns': [33, 53, 73, 91, 112, 132, 149, 167, 184],  # p6 Fig.20
        'tf_ns': [10, 16, 22, 28, 36, 42, 47, 53, 60]},  # p6 Fig.20
    'vsd_V_vs_isd_A_vgs_m5V': {'25C': {5: 3.58, 10: 3.91, 25: 4.53, 50: 5.19, 75: 5.68, 100: 6.08, 140: 6.61},  # p5 Fig.11 (read from figure; plot ends at 140 A)
                               '175C': {5: 3.12, 10: 3.46, 25: 4.08, 50: 4.70, 75: 5.14, 100: 5.48, 140: 5.91}},  # p5 Fig.12
    'vsd_V_vs_isd_A_vgs_m2V': {'25C': {5: 2.87, 10: 3.24, 25: 3.92, 50: 4.64, 75: 5.17, 100: 5.59, 140: 6.18},  # p5 Fig.11 (read from figure)
                               '175C': {5: 2.43, 10: 2.77, 25: 3.52, 50: 4.26, 75: 4.79, 100: 5.20, 140: 5.71}},  # p5 Fig.12
    'vsd_V_vs_isd_A_vgs_0V': {'25C': {50: 3.88, 100: 5.02}, '175C': {50: 3.72, 100: 4.98}},  # p5 Fig.11/12 (read from figure)
    'zth_K_W_single_pulse_vs_t_s': {1e-6: 3.0e-4, 1e-5: 2.1e-3, 1e-4: 1.24e-2, 1e-3: 5.9e-2, 1e-2: 0.152, 1e-1: 0.260},  # p7 Fig.25 (read from figure; plateau 0.27)
    'gate_charge_curve': {'vgs_start_V': -3, 'plateau_V': (10.1, 12.4), 'q_plateau_start_nC': 66, 'q_plateau_end_nC': 134, 'qg_at_18V_nC': 187, 'cond': 'VDS=800 V, ID=100 A, 25 C'},  # p6 Fig.18 (read from figure; sloped plateau ~0.034 V/nC)
    'id_derating_A_vs_tc_C': {-55: 147, 22: 147, 50: 135, 75: 122, 100: 106, 125: 87, 150: 62, 174: 0},  # p7 Fig.23 (read from figure; flat at 147 A up to Tc ~22 C)
    'soa': {'type': 'FBSOA only; Tc/single-pulse conditions not printed', 'i_flat_A': 367, 'i_at_1100V_A': {'10us': 64, '100us': 12, '1ms': 2.4, '10ms': 0.9}, 'dc_line': None, 'rbsoa_or_clamped_inductive': None},  # p7 Fig.26 (read from figure, log axes ~+/-10-15 %)
}

SG2M014120LJ = {  # Sichain TriQSiC G2, 1200 V 13 mOhm, TO-247-4L
    # ---------------- A. table values ----------------
    'doc_rev': 'V02_04, 2025-12-25 (Final)',  # p1-p14 footer, p13 revision history
    'qualification': None,  # p1 (only 'halogen free, RoHS'); p14: contact Sichain for high-reliability/transportation use
    'package': 'TO-247-4L (drawing TO-247-4L-C); no package/source inductance stated',  # p1, p11
    'v_dss_V': 1200,  # p3 Table 2 (VGS=0, ID=100 uA); V(BR)DSS min 1200 p4 Table 4
    'i_d_cont_A': {'tc25C': 155, 'tc100C': 109, 'vgs_V': 18, 'package_limit_stmt': None},  # p3 Table 2
    'i_d_pulse_A': 491,  # p3 Table 2 (tp=100 us, limited by Tj,max)
    'i_s_cont_A': {'tc25C': 128, 'vgs_V': -4},  # p5 Table 7
    'p_tot_W': 577,  # p3 Table 2 (Tc=25 C, Tj=175 C)
    'tj_max_C': 175,  # p3 Table 2 (Tj, Tstg -55..175 C)
    'rth_jc_K_W': {'typ': 0.21, 'max': 0.26},  # p3 Table 3
    'rds_on_mohm': {'typ_25C_18V': 13, 'max_25C_18V': 18, 'typ_175C_18V': 23.7, 'typ_25C_15V': 15, 'max_25C_15V': 21, 'typ_175C_15V': 24.6, 'id_test_A': 90},  # p4 Table 4
    'vgs_th_V': {'min_25C': 2.3, 'typ_25C': 2.8, 'max_25C': 3.6, 'typ_175C': 2.2, 'min_175C': None, 'cond': 'VDS=VGS, ID=26 mA'},  # p4 Table 4
    'vgs_V': {'rec_op': (-4, 18), 'abs_dc': (-8, 22), 'abs_dc_when_body_diode_used': (-4, 22), 'abs_transient': (-10, 25), 'transient_cond': 'tp<=0.5 us, D<0.01'},  # p3 Table 2 + Note 1, p5 Note 2
    'cap_pF': {'ciss': 5220, 'coss': 262, 'crss': 12, 'vds_V': 1000, 'cond': 'VGS=0, 25 C, VAC=25 mV, f=100 kHz'},  # p4 Table 5
    'eoss_uJ': {'value': 142, 'vds_V': 1000},  # p4 Table 5 (VDS from shared condition cell; Fig.16 confirms)
    'gate_charge_nC': {'qg': 211, 'qgs': 66, 'qgd': 42, 'cond': 'VDS=800 V, ID=90 A, VGS=-4/+18 V'},  # p5 table (cont. of Table 5)
    'rg_int_ohm': {'typ': 2.6, 'cond': 'VAC=25 mV, f=1 MHz, open drain'},  # p4 Table 4
    'sw_25C': {'eon_uJ': 1103, 'eoff_uJ': 537, 'td_on_ns': 18, 'tr_ns': 29, 'td_off_ns': 56, 'tf_ns': 13},  # p5 Table 6
    'sw_cond': {'vdd_V': 800, 'id_A': 90, 'rg_ohm': 2.5, 'rg_ext_or_int': 'not labelled in table (Fig.25 caption: RG(ext))', 'vgs_V': (-4, 18), 'l_load_uH': 16.7, 'l_stray_nH': None, 'tj_C': 25},  # p5 Table 6
    'eon_includes_diode_rr': 'not stated; test circuit is a half-bridge with the same MOSFET as freewheeler => implied',  # p12 Fig.B
    'sw_hot': None,  # p5 Table 6 has no hot switching data (see Fig.26)
    'vsd_V': {'typ_25C': 3.9, 'typ_175C': 3.5, 'max': None, 'isd_A': 45, 'vgs_V': -4},  # p5 Table 7
    'reverse_recovery': {'trr_ns': 34, 'qrr_nC': 1349, 'irrm_A': 68, 'isd_A': 90, 'vr_V': 800, 'didt_A_per_us': 4279, 'vgs_V': -4, 'tj_C': 175},  # p5 Table 7 (no 25 C values given)
    'short_circuit_rating': None,  # not given
    'avalanche_rating': None,  # not given
    # ---------------- B. curves (read from figure, about +/-5-10 %) ----------------
    'rdson_norm_vs_tj_18V_90A': {25: 1.00, 50: 1.073, 75: 1.18, 100: 1.31, 125: 1.46, 150: 1.635, 175: 1.82},  # p6 Fig.4 (read from figure, ~+/-5-10 %)
    'rdson_mohm_vs_tj_18V_90A': {25: 13.2, 50: 14.1, 75: 15.4, 100: 17.0, 125: 18.9, 150: 21.2, 175: 23.6},  # p6 Fig.6 (read from figure, ~+/-5-10 %)
    'vgs_th_V_vs_tj': {25: 2.96, 175: 2.28},  # p7 Fig.11 (read from figure)
    'coss_pF_vs_vds_V': {0.6: 7400, 1: 6100, 5: 3600, 10: 2450, 20: 1690, 50: 970, 100: 672, 200: 472, 400: 332, 600: 272, 800: 262, 1000: 262},  # p8 Fig.17 (0-200 V, 11.5 px/V) + Fig.18 (0-1200 V) (read from figure; 0.6 V = lowest resolvable)
    'crss_pF_vs_vds_V': {0.5: 2150, 1: 1150, 5: 290, 10: 92, 20: 60, 50: 32.5, 100: 23.7, 200: 17.9, 400: 13.6, 600: 11.8, 800: 11.8, 1000: 12.2},  # p8 Fig.17/18 (read from figure)
    'ciss_pF_vs_vds': {'10-100V': 5330, '800-1000V': 5225},  # p8 Fig.17/18 (read from figure)
    'eoss_uJ_vs_vds_V': {100: 4.3, 200: 12.8, 400: 35.9, 600: 66.0, 800: 101.5, 1000: 142.4, 1200: 187.2},  # p8 Fig.16 (read from figure, ~+/-5 %)
    'esw_vs_id_600V_2p5ohm_25C': {  # p9 Fig.23 (read from figure; VGS -4/+18 V; curves start at 10 A)
        'i_A':     [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140],  # p9 Fig.23
        'eon_uJ':  [202, 254, 319, 390, 458, 528, 594, 660, 729, 803, 880, 960, 1042, 1119],  # p9 Fig.23
        'eoff_uJ': [31, 47, 70, 103, 144, 194, 247, 309, 376, 447, 527, 609, 697, 781],  # p9 Fig.23
        'etot_uJ': [235, 300, 391, 488, 603, 720, 842, 973, 1106, 1254, 1404, 1567, 1734, 1904]},  # p9 Fig.23
    'esw_vs_id_800V_2p5ohm_25C': {  # p9 Fig.24 (read from figure; VGS -4/+18 V)
        'i_A':     [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140],  # p9 Fig.24
        'eon_uJ':  [335, 412, 506, 604, 709, 809, 908, 1006, 1104, 1208, 1315, 1423, 1537, 1643],  # p9 Fig.24
        'eoff_uJ': [30, 55, 93, 146, 212, 286, 364, 450, 544, 639, 741, 856, 971, 1087],  # p9 Fig.24
        'etot_uJ': [366, 470, 600, 741, 921, 1092, 1273, 1455, 1649, 1848, 2058, 2277, 2507, 2727]},  # p9 Fig.24
    'esw_vs_rg_800V_90A_25C': {  # p10 Fig.25 (read from figure; VGS -4/+18 V)
        'rg_ohm': [2.5, 5, 7.5, 10, 12.5, 15, 17.5, 20],  # p10 Fig.25
        'eon_uJ': [1113, 1524, 1867, 2153, 2439, 2726, 3013, 3290],  # p10 Fig.25
        'eoff_uJ': [547, 844, 1117, 1374, 1626, 1878, 2131, 2371],  # p10 Fig.25
        'etot_uJ': [1657, 2365, 2981, 3536, 4065, 4605, 5140, 5665]},  # p10 Fig.25
    'esw_vs_tj_800V_90A_2p5ohm': {  # p10 Fig.26 (read from figure)
        'tj_C': [25, 50, 75, 100, 125, 150, 175],  # p10 Fig.26
        'eon_uJ': [1105, 1087, 1077, 1070, 1066, 1066, 1059],  # p10 Fig.26
        'eoff_uJ': [536, 551, 564, 582, 596, 613, 628],  # p10 Fig.26
        'etot_uJ': [1641, 1638, 1641, 1648, 1662, 1676, 1683]},  # p10 Fig.26
    'tsw_vs_rg_800V_90A_25C': {  # p10 Fig.27 (read from figure; td_on crosses tr at ~4.5 ohm)
        'rg_ohm': [2.5, 5, 7.5, 10, 12.5, 15, 17.5, 20],  # p10 Fig.27
        'td_on_ns': [18.7, 34.7, 50, 63.4, 75.7, 87.5, 99.2, 110.4],  # p10 Fig.27
        'tr_ns': [29.0, 31.0, 31.9, 33.6, 36.4, 39.6, 43.0, 46.5],  # p10 Fig.27
        'td_off_ns': [56.8, 76.3, 95.2, 113.5, 131.9, 150.5, 169.1, 186.9],  # p10 Fig.27
        'tf_ns': [13.5, 18.4, 23.0, 27.0, 31.3, 35.3, 39.3, 43.0]},  # p10 Fig.27
    'vsd_V_vs_isd_A_vgs_m4V': {'25C': {5: 2.99, 10: 3.19, 25: 3.57, 50: 4.01, 75: 4.36, 100: 4.66, 150: 5.18, 200: 5.65, 250: 6.07},  # p7 Fig.9 (read from figure; plot to 270 A)
                               '175C': {5: 2.65, 10: 2.83, 25: 3.19, 50: 3.59, 75: 3.90, 100: 4.17, 150: 4.65, 200: 5.06, 250: 5.45}},  # p7 Fig.10
    'vsd_V_vs_isd_A_vgs_0V': {'25C': {50: 3.38, 100: 4.19}, '175C': {50: 3.23, 100: 3.87}},  # p7 Fig.9/10 (read from figure)
    'zth_K_W_single_pulse_vs_t_s': {1e-6: 2.3e-3, 1e-5: 6.7e-3, 1e-4: 2.11e-2, 1e-3: 6.5e-2, 1e-2: 0.152, 1e-1: 0.209},  # p9 Fig.21 (read from figure; plateau 0.21 = typ Rth)
    'gate_charge_curve': {'vgs_start_V': -4, 'plateau_V': (7.5, 9.1), 'q_plateau_start_nC': 66, 'q_plateau_end_nC': 108, 'qg_at_18V_nC': 210, 'cond': 'VDS=800 V, ID=90 A, 25 C'},  # p7 Fig.12 (read from figure)
    'id_derating_A_vs_tc_C': {-50: 155, 25: 155, 50: 141, 75: 126, 100: 109, 125: 88, 150: 57, 175: 0},  # p9 Fig.19 (read from figure; flat at 155 A up to Tc=25 C)
    'soa': {'type': 'FBSOA, Tc=25 C, Tj=175 C, single pulse', 'i_flat_A': 491, 'i_at_1180V_A': {'10us': 15, '100us': 4.9, '1ms': 1.6, '10ms': 0.68, 'DC': 0.49}, 'rbsoa_or_clamped_inductive': None},  # p9 Fig.22 (read from figure, log axes ~+/-10 %)
}

B3M013C120Z = {  # BASiC, 1200 V 13.5 mOhm, TO-247-4 (Kelvin source), Ag-sintered
    # ---------------- A. table values ----------------
    'doc_rev': 'Rev. 1.0, 2026-05-08 (PDF metadata title says Rev_0_3)',  # p1-p14 footer, p14 revision history
    'qualification': None,  # p1 (only 'halogen free, RoHS'; 'avalanche ruggedness' claimed, no rating)
    'package': 'TO-247-4 (pin1 D, pin2 power S, pin3 Kelvin S, pin4 G); no package/source inductance stated',  # p1, p13
    'v_dss_V': 1200,  # p2 table (VGS=0, ID=100 uA)
    'i_d_cont_A': {'tc25C': 180, 'tc100C': 127, 'vgs_V': 18, 'package_limit_stmt': None},  # p2 table
    'i_d_pulse_A': 360,  # p2 table (tp limited by Tjmax; note 'verified by design')
    'i_s_cont_A': {'tc25C': 130, 'tc100C': 78, 'vgs_V': -5},  # p5 table
    'i_s_pulse_A': 315,  # p5 table
    'p_tot_W': 750,  # p2 table (Tc=25 C, Tj=175 C)
    'tj_max_C': 175,  # p2 table (Tj, Tstg -55..175 C)
    'rth_jc_K_W': {'typ': 0.20, 'max': None},  # p3 table
    'rds_on_mohm': {'typ_25C_18V': 13.5, 'max_25C_18V': 17.5, 'typ_175C_18V': 23, 'typ_25C_15V': 16.5, 'id_test_A': 60},  # p2 table
    'vgs_th_V': {'min_25C': 2.3, 'typ_25C': 2.7, 'max_25C': 3.5, 'typ_175C': 1.9, 'min_175C': None, 'cond': 'VGS=VDS, ID=23 mA, after 1 ms pulse at VGS=20 V'},  # p2 table
    'vgs_V': {'rec_op': (-5, 18), 'abs_dc': (-10, 22), 'abs_transient': (-12, 24), 'transient_cond': 't<300 ns'},  # p2 table
    'cap_pF': {'ciss': 5200, 'coss': 215, 'crss': 14, 'vds_V': 800, 'cond': 'VGS=0, f=100 kHz, VAC=25 mV'},  # p3 table
    'eoss_uJ': {'value': 90, 'vds_V': 800},  # p3 table
    'co_er_pF': 280, 'co_tr_pF': 430,  # p3 table (VGS=0, 0<VDS<800 V)
    'gate_charge_nC': {'qg': 225, 'qgs': 66, 'qgd': 92, 'cond': 'VDS=800 V, ID=60 A, VGS=-5/+18 V'},  # p3 table
    'rg_int_ohm': {'typ': 1.4, 'cond': 'f=1 MHz, VAC=25 mV'},  # p3 table
    'sw_cond': {'vdd_V': 800, 'id_A': 60, 'rg_ext_ohm': 8.2, 'vgs_V': (-5, 18), 'l_stray_nH': 50, 'l_load_uH': None},  # p4 table
    'eon_includes_diode_rr': 'yes (stated) with FWD=body diode at VGS=-5 V; *_sbd rows use FWD=B4D40120H SiC SBD',  # p4 table
    'sw_25C': {'td_on_ns': 19, 'tr_ns': 37, 'td_off_ns': 80, 'tf_ns': 16, 'eon_uJ': 1200, 'eoff_uJ': 530, 'eon_sbd_uJ': 1010, 'eoff_sbd_uJ': 590},  # p4 table
    'sw_175C': {'td_on_ns': 15, 'tr_ns': 40, 'td_off_ns': 99, 'tf_ns': 18, 'eon_uJ': 1490, 'eoff_uJ': 600, 'eon_sbd_uJ': 880, 'eoff_sbd_uJ': 660},  # p4 table
    'vsd_V': {'typ_25C': 4.0, 'typ_175C': 3.5, 'max': None, 'isd_A': 30, 'vgs_V': -5},  # p5 table
    'reverse_recovery': {'25C': {'trr_ns': 19, 'qrr_nC': 390, 'irrm_A': 35, 'didt_A_per_us': 3100}, '175C': {'trr_ns': 34, 'qrr_nC': 1150, 'irrm_A': 52, 'didt_A_per_us': 3600}, 'isd_A': 60, 'vr_V': 800, 'vgs_V': -5},  # p5 table
    'short_circuit_rating': None,  # not given
    'avalanche_rating': None,  # not given (p1 claims ruggedness only)
    # ---------------- B. curves (read from figure, about +/-5-10 %) ----------------
    'rdson_norm_vs_tj_18V_60A': {25: 1.00, 50: 1.02, 75: 1.086, 100: 1.19, 125: 1.33, 150: 1.50, 175: 1.69},  # p7 Fig.5 (read from figure, ~+/-5 %)
    'rdson_mohm_vs_tj_18V_60A': {25: 13.1, 50: 13.35, 75: 14.2, 100: 15.6, 125: 17.4, 150: 19.6, 175: 22.05},  # p7 Fig.6 (read from figure, ~+/-5 %)
    'rdson_mohm_vs_id_18V': {'25C': {60: 12.8, 100: 13.15, 200: 14.1}, '175C': {60: 22.4, 100: 22.8, 200: 25.0}},  # p7 Fig.7 (read from figure)
    'vgs_th_V_vs_tj': {25: 2.73, 175: 1.92},  # p6 Fig.4 (read from figure)
    'coss_pF_vs_vds_V': {4: 4200, 5: 3400, 10: 2750, 20: 1850, 30: 1070, 50: 826, 100: 590, 200: 422, 400: 303, 600: 248, 800: 217, 1000: 199},  # p7 Fig.8 (read from figure; 1.6 px/V: <4 V unreadable (curve on frame, >=5000 pF at 0 V); step ~2000->1300 pF at 19-25 V, +/-15 %)
    'crss_pF_vs_vds_V': {5: 650, 10: 390, 20: 235, 25: 56, 50: 35.3, 100: 26.8, 200: 21.0, 400: 16.8, 600: 14.9, 800: 13.8, 1000: 13.1},  # p7 Fig.8 (read from figure; vertical drop ~250->60 pF at 20-22 V)
    'ciss_pF_vs_vds': {'25-100V': 5330, '800-1000V': 5200},  # p7 Fig.8 (read from figure)
    'eoss_uJ_vs_vds_V': {100: 4.1, 200: 11.2, 400: 31.6, 600: 58.2, 800: 90.1, 1000: 125.9},  # p9 Fig.13 (read from figure, ~+/-5 %)
    'esw_vs_id_8p2ohm': {  # p10 Fig.17-20 (read from figure; VGS -5/+18 V, Lsigma=50 nH, FWD=same part)
        'i_A': [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120],  # p10 Fig.17-20
        '600V_25C':  {'eon_uJ': [262, 358, 459, 561, 664, 773, 887, 998, 1120, 1237, 1364, 1487], 'eoff_uJ': [68, 87, 136, 205, 288, 399, 509, 616, 741, 867, 987, 1132], 'etot_uJ': [329, 447, 595, 767, 954, 1173, 1396, 1615, 1862, 2108, 2354, 2619]},  # p10 Fig.17
        '600V_175C': {'eon_uJ': [265, 382, 515, 644, 776, 913, 1066, 1207, 1365, 1511, 1659, 1841], 'eoff_uJ': [67, 92, 151, 227, 317, 420, 562, 692, 830, 958, 1102, 1277], 'etot_uJ': [333, 477, 664, 872, 1093, 1335, 1630, 1900, 2194, 2471, 2763, 3118]},  # p10 Fig.18
        '800V_25C':  {'eon_uJ': [395, 546, 700, 864, 1022, 1191, 1375, 1565, 1753, 1960, 2169, 2345], 'eoff_uJ': [109, 135, 197, 297, 399, 527, 673, 836, 991, 1156, 1344, 1495], 'etot_uJ': [506, 683, 901, 1161, 1419, 1718, 2050, 2399, 2747, 3114, 3513, 3843]},  # p10 Fig.19
        '800V_175C': {'eon_uJ': [414, 609, 821, 1037, 1253, 1479, 1721, 1967, 2209, 2477, 2744, 2981], 'eoff_uJ': [107, 135, 216, 330, 447, 602, 753, 935, 1109, 1307, 1502, 1684], 'etot_uJ': [514, 747, 1037, 1370, 1702, 2081, 2470, 2900, 3316, 3786, 4247, 4665]}},  # p10 Fig.20
    'esw_vs_rg_800V_60A': {  # p11 Fig.21 (25 C), Fig.22 (175 C) (read from figure; plotted range 5-20 ohm only)
        'rg_ext_ohm': [5, 6, 7, 8.2, 10, 12, 15, 17.5, 20],  # p11 Fig.21/22
        '25C':  {'eon_uJ': [903, 986, 1079, 1190, 1364, 1538, 1794, 1998, 2195], 'eoff_uJ': [390, 433, 478, 527, 617, 702, 826, 921, 1007], 'etot_uJ': [1297, 1419, 1557, 1719, 1981, 2243, 2622, 2921, 3200]},  # p11 Fig.21
        '175C': {'eon_uJ': [1181, 1265, 1366, 1479, 1628, 1791, 2006, 2200, 2394], 'eoff_uJ': [465, 506, 549, 602, 700, 796, 931, 1037, 1138], 'etot_uJ': [1644, 1771, 1916, 2081, 2328, 2587, 2937, 3236, 3531]}},  # p11 Fig.22
    'esw_vs_tj_800V_60A_8p2ohm': {  # p12 Fig.25 (read from figure)
        'tj_C': [25, 50, 75, 100, 125, 150, 175],  # p12 Fig.25
        'eon_uJ': [1190, 1175, 1187, 1232, 1303, 1372, 1476], 'eoff_uJ': [532, 544, 552, 562, 573, 579, 601], 'etot_uJ': [1720, 1717, 1737, 1793, 1876, 1950, 2077],  # p12 Fig.25 (FWD = body diode)
        'eon_sbd_uJ': [1009, 983, 955, 933, 914, 897, 882], 'eoff_sbd_uJ': [593, 601, 615, 636, 646, 658, 664], 'etot_sbd_uJ': [1604, 1581, 1568, 1567, 1558, 1554, 1543]},  # p12 Fig.25 (FWD = B4D40120H)
    'tsw_vs_rg_800V_60A': {  # p11 Fig.23 (25 C), Fig.24 (175 C) (read from figure; td_on/tf overlap <8 ohm at 25 C and 15-17.5 ohm at 175 C, +/-2 ns)
        'rg_ext_ohm': [5, 6, 7, 8.2, 10, 12, 15, 17.5, 20],  # p11 Fig.23/24
        '25C':  {'td_on_ns': [13.9, 15.6, 17.3, 18.7, 23.6, 28.7, 36.5, 42.9, 49.1], 'tr_ns': [29.4, 31.4, 34.0, 37.4, 43.2, 48.0, 52.8, 56.4, 59.2], 'td_off_ns': [63.0, 67.2, 72.5, 78.7, 86.9, 97.3, 113.3, 125.3, 135.9], 'tf_ns': [13.3, 14.4, 15.6, 17.2, 19.5, 21.1, 24.3, 26.8, 29.3]},  # p11 Fig.23
        '175C': {'td_on_ns': [12.6, 12.8, 13.7, 15.1, 16.3, 18.6, 25, 29.5, 35.6], 'tr_ns': [33.1, 35.2, 37.4, 39.6, 43.2, 46.7, 50.6, 53.6, 56.3], 'td_off_ns': [78.5, 83.7, 89.9, 97.7, 109.4, 122.7, 141.8, 158.3, 174.2], 'tf_ns': [14.1, 15.4, 16.5, 17.9, 20.5, 22.8, 25.5, 29, 30.6]}},  # p11 Fig.24
    'vsd_V_vs_isd_A_vgs_m5V': {'25C': {5: 3.20, 10: 3.44, 25: 3.91, 50: 4.43, 75: 4.83, 100: 5.18, 150: 5.76, 200: 6.26, 250: 6.69},  # p8 Fig.9 (read from figure)
                               '175C': {5: 2.79, 10: 3.03, 25: 3.47, 50: 3.94, 75: 4.31, 100: 4.62, 150: 5.15, 200: 5.61, 250: 6.00}},  # p8 Fig.10
    'vsd_V_vs_isd_A_vgs_0V': {'25C': {50: 3.37, 100: 4.35}, '175C': {50: 3.25, 100: 4.03}},  # p8 Fig.9/10 (read from figure)
    'zth_K_W_single_pulse_vs_t_s': {1e-6: 1.45e-3, 1e-5: 4.2e-3, 1e-4: 1.29e-2, 1e-3: 5.05e-2, 1e-2: 0.138, 1e-1: 0.198},  # p12 Fig.26 (read from figure; plateau 0.200)
    'gate_charge_curve': {'vgs_start_V': -5, 'plateau_V': (7.6, 11.2), 'q_plateau_start_nC': 66, 'q_plateau_end_nC': 159, 'qg_at_18V_nC': 224, 'cond': 'VDS=800 V, ID=60 A, 25 C'},  # p9 Fig.16 (read from figure)
    'id_derating_A_vs_tc_C': {-55: 180, 25: 180, 50: 165, 75: 147, 100: 128, 125: 104, 150: 74, 175: 0},  # p9 Fig.15 (read from figure; flat at 180 A up to Tc=25 C)
    'soa': {'type': 'FBSOA, Tc=25 C, Tj<=175 C, single pulse', 'i_flat_A': 360, 'i_at_1150V_A': {'1us': 90, '10us': 31, '100us': 10, '1ms': 2.6, '10ms': 0.94, '100ms': 0.65}, 'rbsoa_or_clamped_inductive': None},  # p12 Fig.27 (read from figure, log axes ~+/-10 %)
}

CAVEATS = """
- Curve reads about +/-5-10 %; the steep low-voltage part of the Coss / Crss curves +/-15-30 %. Only Sichain has a 0-200 V
  capacitance plot; InventChip and BASiC cannot be read below about 4 V.
- Integrating the digitised Coss reproduces each table Eoss within 2-5 % (InventChip 88.6 vs 88 uJ at 800 V, Sichain 103 vs
  101.5 uJ, BASiC 92 vs 90 uJ).
- Test conditions differ: gate resistor InventChip 2 ohm (table) but 20 ohm in its energy-vs-current / -temperature curves,
  Sichain 2.5 ohm, BASiC 8.2 ohm; load inductor InventChip 200 uH, Sichain 16.7 uH, BASiC states only 50 nH stray.
- Eoff is defined differently per vendor (Sichain's is far below its Eoss, BASiC's is about its Eoss, i.e. it includes the
  energy stored in the device's own Coss, which ZVS recovers). Eon is a hard turn-on number including the opposite device's
  Qrr and Coss energy - it does not represent ZVS turn-on in the DAB.
- InventChip: Qrr 289 nC does not fit 0.5 x Irrm x trr (about 700 nC) and its Tj is not stated; the single-pulse Zth at
  1-10 us is 5-8x lower than the other two parts (treat as optimistic); no body-diode curve at the recommended -3.5 V.
- Sichain: Vth curve about 0.1-0.15 V above the table typical; diode recovery only at 175 C; "Rg = 2.5 ohm" not labelled
  external or internal; gate must stay at -4 V or above whenever the body diode conducts; no qualification statement.
- BASiC: curves show RDS(on) 3-5 % below the table typical; only a typical Rth; PDF metadata says Rev 0.3.
- None of the three states package / source inductance, a short-circuit withstand time, avalanche energy or a
  reverse-bias SOA. Only InventChip claims AEC-Q101.
"""
