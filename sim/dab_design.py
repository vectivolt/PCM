"""DAB-D60 power-stage design (REQUIREMENTS.md section 3): turns ratio, series inductance, ZVS map with the real
C_oss of CBB011M12GM4T, currents, losses/efficiency/thermal map, magnetics and capacitor specification, 950 V
verdict, ngspice cross-check and the schematic hand-off sim/out/dab_design/dab_spec.json.

    .venv/bin/python sim/dab_design.py

Model conventions (all calculated, nothing bench-validated):
  * n = N1/N2. Series inductance L (leakage + external) lumped on the primary side; magnetising inductance Lm
    across the ideal transformer after L, so Lm sees the secondary voltage referred to the primary.
  * Bridge voltages are 2- or 3-level square waves: v_p = V1 on (a1, pi), v_s' = n*V2 on (phi+a2, phi+pi), half-
    wave antisymmetric.  a1 = a2 = 0 is single-phase shift (SPS); a2 = 0 extended (EPS); a1 = a2 dual (DPS);
    a1 != a2 triple phase shift (TPS).  phi > 0 = power from port 1 (DC bus) to port 2 (battery).
  * Currents are exact piecewise-linear functions (no time stepping); P, RMS, peaks are exact segment integrals.
  * ZVS: each leg transition is integrated in the charge domain with the module's C_oss(V) (Fig. 9), a node
    capacitance, the inductor energy and the opposing bridge voltage.  Incomplete transitions dissipate the
    residual capacitive energy; wrong-polarity transitions are hard switched with DPT E_on + E_rr.
"""
import csv
import json
import os
import subprocess
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dab_devices as dev  # noqa: E402

OUT = 'sim/out/dab_design'
SPICE_DIR = 'sim/spice'
F = 100e3                      # DAB-03
W = 2 * np.pi * F
PI = np.pi

# ---------------------------------------------------------------------------------------------------------
# Assumptions (A-*) that are ours, not from a datasheet.  Each one is printed in report.md.
# ---------------------------------------------------------------------------------------------------------
A = {
    'T_coolant_in': (50.0, 'C, worst-case DVT inlet (roadmap liquid-cooling table: tests at 25/35/45/50 C)'),
    'coolant_flow': (9.0, 'L/min 50/50 glycol, same flow as the CRD Rth measurement (UG p52)'),
    'coolant_rho_cp': (1065 * 3400.0, 'J/(m3 K), 50/50 glycol-water handbook value'),
    'Rth_hf': (0.045, 'K/W per switch position, coldplate-to-fluid, measured by Wolfspeed with ONE position heated'),
    'k_xheat': (0.45, 'extra rise from the 3 neighbouring positions of the same module (0.15 each) - not measured'),
    'Tj_max_design': (140.0, 'C design ceiling (datasheet Tvj,op max 150 C, 10 K margin)'),
    'I_pos_rms_max': (90.0, 'A per switch position (100 A package/press-fit limit, p1 note 2, 10 % margin)'),
    'c_node': (150e-12, 'F per leg: AC-node-to-coldplate + winding self capacitance (not measured)'),
    't_dead_min': (50e-9, 's, lower bound of the adaptive dead time'),
    't_dead_max': (300e-9, 's, upper bound of the adaptive dead time'),
    't_dead_margin': (20e-9, 's added to the predicted ZVS transition time (firmware look-up table)'),
    't_dead_fixed': (100e-9, 's, fixed dead time used for the comparison case'),
    'L_loop': (24e-9, 'H commutation loop (7.2 nH bussing + 16.8 nH module) = datasheet SSOA basis (Fig. 19)'),
    'k_cu_window': (0.25, 'copper fraction of the PM 114/93 bobbin window, litz + HV insulation'),
    'Fr1_litz': (2.52, 'litz AC factor at 100 kHz of the reference winding (magnetics verification MG-01: 1548 x '
                       '0.1 mm, non-interleaved; Sullivan / Bessel strand model); harmonics R(h) = Rdc (1 + 1.52 h^2), '
                       'no cap'),
    'T_xfmr': (90.0, 'C core/winding temperature used for magnetics loss (liquid-cooled like the CRD, UG p51)'),
    'Rth_xfmr': (None, 'K/W transformer hot spot to coolant: NOT DEFINED until the magnetics design file exists '
                       '(MG-02: 0.40 K/W is not credible for the PM 114/93 pot core); the maps carry no transformer '
                       'thermal limit and the spec states the Rth the construction needs'),
    'T_xfmr_hs_max': (130.0, 'C transformer hot-spot limit used in the derating maps (class F insulation, margin)'),
    'b_iso': (3.0e-3, 'm radial insulation between windings (basic insulation, final value after ECO-10)'),
    'R_bus': (0.5e-3, 'ohm per port: busbar/PCB/terminals/contactor/fuse, DC'),
    'R_ac_bus': (0.3e-3, 'ohm AC interconnect transformer-inductor-bridges'),
    'vgs_on': (15.0, 'V turn-on gate voltage (datasheet note 1 recommendation)'),
    'alt_altitude_factor': (2.0, 'neutron flux multiplier per 1000 m altitude (JESD89A-style rule of thumb)'),
    'xfmr_dc_resid': (1.5, 'A residual DC magnetising current with flux-balance control (HO 120-NP offset+drift)'),
    'gap_l_max': (10e-3, 'm total air gap of the external inductor, split into >= 4 distributed gaps'),
    'L_bus': (7.2e-9, 'H bank-to-module-terminal bussing (DS Fig. 19 test condition), part of L_loop'),
    'L_mod': (16.8e-9, 'H module internal DC+ to DC- stray (DS p3), between the terminal parts and the dies'),
    'snub': ((6, 6.8, 2.2e-9), 'RC snubbers per bridge at the module terminals (n, R ohm, C F), CRD UG Fig. 22'),
    'hf_caps': ((10, 47e-9, 10e-3, 1.0e-9), 'HF ceramics per bridge at the terminals (n, C, ESR, ESL each), CRD UG '
                                            'Fig. 19 count/value; ESR/ESL typical 1812-2220 MLCC values (assumed)'),
    'RG_off': (0.27, 'ohm external turn-off gate resistor: smallest E24 value keeping the UCC21710 sink <= 10 A with '
                     'VDD-VEE 19.41 V max, ROL 0.3 + RG(int) 1.4 ohm typ and -1 % resistor tolerance (gen/gdrv.py rule)'),
    'driver_skew': (30e-9, 's worst-case gate-path skew (UCC21710 t_sk-pp p10) for the open-loop flux-walk case'),
    'I_term_frac': (0.9, 'fraction of a terminal / lead RMS rating allowed (DR-01: 90 A of the GM4 100 A AC pins)'),
    't_dead_hw_tol': (0.15, 'tolerance of the logic-side guard delay element (comparator + 1 % R / C0G RC, or a '
                            'specified-threshold Schmitt input); the drawn LVC1G17 stretcher is far wider (104-354 ns)'),
    'k_sw_share': (1.25, 'switching-energy factor of the faster of paralleled discretes (threshold spread, no V_th '
                         'matching; Kelvin-source drive) - ASSUMPTION, confirm by DPT on a paralleled pair'),
    'Rth_cp_dev': (0.07, 'K/W cold plate to fluid per TO-247 insulator footprint (~8.6 cm2): the CRD 0.045 K/W per '
                         'module position (~13 cm2) scaled by area - ASSUMPTION'),
    'k_x_dev': (0.30, 'extra cold-plate rise from neighbouring discretes (not measured)'),
    'c_node_wind': (100e-12, 'F per leg: winding self capacitance + harness (module-internal part replaced by the '
                             'insulator capacitance of the low-side tabs for discretes)'),
    'L_loop_disc': ({2: 15e-9, 3: 13e-9}, 'H commutation loop of the discrete layout per paralleling count: the '
                                          'Infineon IMZA120R020M1H TO-247-4 DPT loop is 15 nH (DS p5) - ASSUMPTION '
                                          'that our two-device layout matches it and three devices reach 13 nH'),
    'k_os': (1.63, 'overshoot factor of the capacitive-commutation rule for discretes (dv_os): ngspice die '
                    'overshoots at 950 V / 119 A and 238 A (277 / 689 V, first scratch run of this round) divided by '
                    '1.47 (deck vs datasheet on the GM4) = 188 / 469 V = 1.63 / 0.91 x the I^2 law - the larger is used'),
    'xfmr_unit_mismatch': (0.05, 'current-sharing error between the two paralleled transformer units = their leakage '
                                 'matching tolerance (design_dab_transformer.json: matched within +/-5 %); the hotter '
                                 'unit carries (1 + 0.05)^2 of half the copper loss'),
    'k_clamp_share': (1.2, 'current-sharing factor of paralleled clamp rectifiers on one busbar (V_F spread)'),
    't_trip_logic': (100e-9, 's local trip comparator + logic + injection into the driver DESAT pins (board budget)'),
    'k_coss_hyst': (0.10, 'C_oss hysteresis (soft-switching) loss per switch per period as a fraction of E_oss(800 V) - '
                          'literature order of magnitude for 1200 V SiC, no data for these parts (ASSUMPTION)'),
    'c_desat': (15e-12, 'F at the DESAT pin: pin + two US1M in series + layout, no external C_blk (ASSUMPTION)'),
    'desat_vds_trip': ((5.52, 7.01, 8.28), 'V device V_DS at the DESAT trip for n = 2 US1M + 1.5 kOhm (asia_drivers.md '
                                           'sec. 7.2, calculated there with the NSI limits)'),
    'tsc_hot_factor': (0.7, 'short-circuit withstand at 150 C start relative to 25 C (ASSUMPTION; no maker data)'),
    't_dead_hw': ((0.0, 0.0), 's (min, max) gate-level window of the hardware shoot-through guard; set by the DR-02 '
                              'decision in dead_time_decision()'),
}


def a(key):
    return A[key][0]


# Magnetics corrections from the independent verification (MAG-1; gen/data/review_magnetics.csv when forwarded).
# Factors multiply the modelled losses; 1.0 = our own calculation unchanged.
MAG_CORR = dict(k_core_xfmr=1.0, k_cu_xfmr=1.0, k_core_ind=1.0, k_cu_ind=1.0, src='none applied yet')
# Independent ('own') figures of the magnetics verification for the PM 114/93 reference construction at 800 V / 873 V
# 60 kW (sim/out/magnetics/report.md sec. 3.2, 3.3; findings MG-01, MG-03, MG-05).  Used until the magnetics
# engineer's design files sim/out/magnetics/design_dab_transformer.json / design_dab_series_inductor.json exist.
MAG_IND = dict(xfmr_cu_W=337.22, xfmr_core_W=18.09, ind_cu_W=163.08, ind_core_W=5.17, v1=800.0, v2=800.0 * 12 / 11,
               p=60e3, check_v2low=dict(v1=700.0, v2=400.0, p=37.5e3, xfmr_cu_W=400.81, xfmr_core_W=2.09),
               xfmr_core_set_limit_factor=3.1,
               src='sim/out/magnetics/report.md sec. 3.2 / 3.3 (own model = independent figures, 2026-10-04)')
MAG_FILES = ('sim/out/magnetics/design_dab_transformer.json', 'sim/out/magnetics/design_dab_series_inductor.json')
MAGD = None        # magnetics design data (the two files above) once they exist


def MAG_SRC():
    return MAGD['src'] if MAGD else 'independent figures of the magnetics verification (' + MAG_IND['src'] + ')'


def _r_h(w, h):
    """Harmonic resistance R(h) from the design file's winding block (R_ac_per_harmonic: [f Hz, R ohm] pairs or
    dicts); above the table R ~ f^2."""
    tab = w.get('R_ac_per_harmonic') or w.get('R_harmonic_primary_referred_pair_ohm_90C') or w['R_harmonic_ohm_90C']
    tab = [(t['f_Hz'], t['R_ohm']) if isinstance(t, dict) else t for t in tab]
    f_, r_ = np.array(tab, float).T
    fh = np.asarray(h, float) * F
    return np.where(fh <= f_[-1], np.interp(fh, f_, r_), r_[-1] * (fh / f_[-1]) ** 2)


def _c_alpha(al):
    return float(np.mean(np.abs(np.cos(np.linspace(0, 2 * PI, 20001)[:-1])) ** al))


def mag_losses(o, s, h, hl, a2, v2):
    """Transformer pair and series inductor losses from the magnetics design files: Steinmetz (k, alpha, beta) with
    the iGSE waveform factor, harmonic winding resistance R(h) at 90 C (formulas as stated in the files)."""
    ct, cl = MAGD['xfmr']['loss_model']['core'], MAGD['ind']['loss_model']['core']
    al = ct['alpha']
    b_pk = o['n'] * v2 * (PI - a2) / (2 * W * ct['N1'] * ct['Ae_m2'])
    shape = ((PI - a2) / PI) * (2 / (PI - a2)) ** al / _c_alpha(al)
    p_core = shape * ct['k'] * F ** al * b_pk ** ct['beta'] * ct['Ve_m3']
    p_cu = np.sum(hl * _r_h(MAGD['xfmr']['loss_model']['winding'], h), -1)
    al_l = cl['alpha']
    bl = MAGD['ind']['electrical']['L_H'] * s['ILpk'] / (cl['N'] * cl['Ae_m2'])
    pl_core = cl['k'] * F ** al_l * bl ** cl['beta'] * cl['Ve_m3'] * vl_alpha_mean(o, al_l) / \
        np.maximum((W * o['L'] * s['ILpk']) ** al_l * _c_alpha(al_l), 1e-30)
    pl_cu = np.sum(hl * _r_h(MAGD['ind']['loss_model']['winding'], h), -1)
    return p_core, p_cu, pl_core, pl_cu, b_pk, bl


def calibrate_magnetics(D):
    """Scale our magnetics loss terms so that they reproduce the independent figures at the reference point (SPS,
    800 / 873 V, 60 kW); the harmonic law F_r(h) = 1 + (F_r1 - 1) h^2 keeps the shape over the map."""
    global MAGD
    if all(os.path.exists(f) for f in MAG_FILES):
        MAGD = dict(xfmr=json.load(open(MAG_FILES[0])), ind=json.load(open(MAG_FILES[1])))
        MAGD['src'] = ('magnetics design files rev %s / %s (%s; %s)' % (MAGD['xfmr']['revision'], MAGD['ind']['revision'],
                                                                     MAG_FILES[0], MAG_FILES[1]))
        MAG_CORR['src'] = MAGD['src']
        return MAG_CORR
    MAGD = None
    for k in ('k_core_xfmr', 'k_cu_xfmr', 'k_core_ind', 'k_cu_ind'):
        MAG_CORR[k] = 1.0
    m = MAG_IND
    r = evaluate([m['v1']], [m['v2']], [m['p']], D, iters=1, mod=MOD_GM4)
    MAG_CORR.update(k_cu_xfmr=m['xfmr_cu_W'] / r['p_cu'][0], k_core_xfmr=m['xfmr_core_W'] / r['p_core'][0],
                    k_cu_ind=m['ind_cu_W'] / r['pl_cu'][0], k_core_ind=m['ind_core_W'] / r['pl_core'][0],
                    src=m['src'])
    c = m['check_v2low']
    r2 = evaluate([c['v1']], [c['v2']], [c['p']], D, iters=1, mod=MOD_GM4)
    MAG_CORR['check_v2low_cu_W'] = (float(r2['p_cu'][0]), c['xfmr_cu_W'])
    return MAG_CORR


# ---------------------------------------------------------------------------------------------------------
# Exact piecewise-linear waveform engine
# ---------------------------------------------------------------------------------------------------------
def _G(x, al):
    """Integral over [0, x] of the unit 3-level wave w (0 on (0,al), +1 on (al,pi), 0 on (pi,pi+al), -1 after)."""
    x = np.mod(x, 2 * PI)
    return np.clip(x - al, 0, PI - al) - np.clip(x - PI - al, 0, PI - al)


def _w(x, al):
    x = np.mod(x, 2 * PI)
    return np.where((x > al) & (x < PI), 1.0, np.where(x > PI + al, -1.0, 0.0))


def currents(o, th):
    """Inductor current i_L and magnetising current i_m (primary-referred) at angle th."""
    v2p = o['n'] * o['v2']
    s = lambda t: _G(t - o['phi'], o['a2']) - _G(-o['phi'], o['a2'])
    f = o['v1'] * _G(th, o['a1']) - v2p * s(th)
    fpi = o['v1'] * _G(PI, o['a1']) - v2p * s(PI)
    il = (f - fpi / 2) / (W * o['L'])                 # half-wave symmetry fixes the offset: i(pi) = -i(0)
    im = (v2p * s(th) - v2p * s(PI) / 2) / (W * o['Lm'])
    return il, im


def _ex(o):
    return {k: np.asarray(v, float)[..., None] for k, v in o.items()}


def stats(o):
    """Exact half-period segment integrals: power, DC and RMS currents, capacitor ripple, peaks."""
    shp = np.broadcast(*[np.asarray(v) for v in o.values()]).shape
    z = np.zeros(shp)
    bp = np.sort(np.stack([z, z + o['a1'], z + np.mod(o['phi'], PI), z + np.mod(o['phi'] + o['a2'], PI), z + PI],
                          -1), -1)
    t0, t1 = bp[..., :-1], bp[..., 1:]
    dt, tm = t1 - t0, 0.5 * (t0 + t1)
    e = _ex(o)
    il0, im0 = currents(e, t0)
    il1, im1 = currents(e, t1)
    is0, is1 = il0 - im0, il1 - im1
    w1, w2 = _w(tm, e['a1']), _w(tm - e['phi'], e['a2'])
    lin = lambda p, q: 0.5 * (p + q) * dt
    sq = lambda p, q: (p * p + p * q + q * q) / 3 * dt
    n = np.asarray(o['n'], float)
    r = {'I1': np.sum(w1 * lin(il0, il1), -1) / PI}
    r['P'] = o['v1'] * r['I1']
    r['I2'] = n * np.sum(w2 * lin(is0, is1), -1) / PI
    r['IL'] = np.sqrt(np.sum(sq(il0, il1), -1) / PI)
    r['Is'] = n * np.sqrt(np.sum(sq(is0, is1), -1) / PI)
    r['Im'] = np.sqrt(np.sum(sq(im0, im1), -1) / PI)
    r['I1rms'] = np.sqrt(np.sum(w1 ** 2 * sq(il0, il1), -1) / PI)
    r['I2rms'] = n * np.sqrt(np.sum(w2 ** 2 * sq(is0, is1), -1) / PI)
    r['IC1'] = np.sqrt(np.maximum(r['I1rms'] ** 2 - r['I1'] ** 2, 0))
    r['IC2'] = np.sqrt(np.maximum(r['I2rms'] ** 2 - r['I2'] ** 2, 0))
    r['ILpk'] = np.max(np.abs(np.concatenate([il0, il1], -1)), -1)
    r['Ispk'] = n * np.max(np.abs(np.concatenate([is0, is1], -1)), -1)
    r['Impk'] = np.max(np.abs(np.concatenate([im0, im1], -1)), -1)
    return r


def p_sps(v1, v2, n, L, phi):
    """Textbook SPS power (lossless, Lm ignored): n V1 V2 phi (pi-|phi|) / (2 pi^2 f L)."""
    return v1 * n * v2 * phi * (PI - np.abs(phi)) / (2 * PI ** 2 * F * L)


def phi_sps(v1, v2, n, L, p):
    """Inverse of p_sps on |phi| <= pi/2; nan where the power is not reachable."""
    x = 8 * F * L * np.abs(p) / (v1 * n * v2)
    with np.errstate(invalid='ignore'):
        return np.sign(p) * PI / 2 * (1 - np.sqrt(np.where(x <= 1, 1 - x, np.nan)))


def solve_phi(o, p, ngrid=48, nbis=22):
    """Outer phase shift for target power p (signed) with the inner shifts a1, a2 of o; nan if unreachable."""
    sgn = np.where(np.asarray(p) >= 0, 1.0, -1.0)
    shp = np.broadcast(*[np.asarray(v) for v in o.values()], np.asarray(p)).shape
    grid = np.linspace(0, PI / 2, ngrid)
    e = {k: np.broadcast_to(np.asarray(v, float), shp)[..., None] for k, v in o.items()}
    e['phi'] = (np.broadcast_to(sgn, shp)[..., None]) * grid
    pg = np.broadcast_to(sgn, shp)[..., None] * stats(e)['P']
    tgt = np.abs(np.broadcast_to(np.asarray(p, float), shp))
    above = pg >= tgt[..., None]
    k = np.argmax(above, -1)
    found = above.any(-1) & (k > 0)
    lo = grid[np.maximum(k - 1, 0)]
    hi = grid[k]
    lo = np.where(tgt <= 0, 0.0, lo)
    hi = np.where(tgt <= 0, 0.0, hi)
    ob = {kk: np.broadcast_to(np.asarray(v, float), shp) for kk, v in o.items()}
    for _ in range(nbis):
        mid = 0.5 * (lo + hi)
        ob['phi'] = np.broadcast_to(sgn, shp) * mid
        pm = np.broadcast_to(sgn, shp) * stats(ob)['P']
        lo, hi = np.where(pm < tgt, mid, lo), np.where(pm < tgt, hi, mid)
    phi = np.broadcast_to(sgn, shp) * 0.5 * (lo + hi)
    return np.where(found | (tgt <= 0), phi, np.nan)


# ---------------------------------------------------------------------------------------------------------
# ZVS transition model with the real C_oss(V)
# ---------------------------------------------------------------------------------------------------------
SG = np.linspace(0.0, 1.0, 81)


def _cumtrapz(y, x):
    return np.concatenate([np.zeros(y.shape[:-1] + (1,)), np.cumsum(0.5 * (y[..., 1:] + y[..., :-1]) *
                                                                     np.diff(x, axis=-1), -1)], -1)


def zvs_transition(iz, vb, nref, vl0, eps, kappa, L, t_max, cq=1.0, cn=1.0, qf=None, c_node=None):
    """Charge-domain integration of one bridge transition, starting at the turn-off instant.
    iz   : current available to swing the leg(s), primary-referred, >0 = correct (ZVS) polarity
    vb   : bus voltage of the switching bridge (actual); nref: 1 primary, n secondary
    vl0  : inductor voltage while the node is still clamped; eps = -1 primary / +1 secondary event
    kappa: legs switching together (2 = SPS full bridge, 1 = single leg of EPS/TPS)
    A wrong-polarity current (iz < 0) first flows in the outgoing body diode; if eps*vl0 > 0 it recovers through
    zero after t0 = L|iz|/(eps vl0) and the swing starts from zero current with the remaining dead time.
    Returns t_tr (s from turn-off, inf if not completed by t_max), v_res (V across the incoming switch at t_max or
    at the stall point), i_end (A referred at the end of a completed swing).  Side outputs on the function object:
    t_half (time to half swing) and t0 (diode-conduction time before the swing)."""
    iz = np.asarray(iz, float)
    gain = np.asarray(eps, float) * np.asarray(vl0, float)
    t0 = np.where(iz >= 0, 0.0, np.where(gain > 0, np.asarray(L, float) * np.abs(iz) / np.maximum(gain, 1e-9), np.inf))
    t_left = np.asarray(t_max, float) - t0
    vb_ = np.asarray(vb, float)[..., None]
    v = vb_ * SG
    qf = (lambda x: cq * dev.qoss(x)) if qf is None else qf          # C_oss charge of one switch position
    cnd = a('c_node') if c_node is None else c_node
    q = qf(v) + qf(vb_) - qf(vb_ - v) + cn * cnd * v                  # charge into one leg
    en = _cumtrapz(v, q)                                                         # integral of v dq of one leg
    qr = q / np.asarray(nref, float)[..., None]
    iz0 = np.maximum(iz, 0.0)[..., None]
    wgt = 0.5 * np.asarray(L, float)[..., None] * iz0 ** 2 + gain[..., None] * qr - \
        np.asarray(kappa, float)[..., None] * en
    ok0 = (t_left > 0)[..., None]
    alive = np.cumprod(np.concatenate([ok0, (wgt[..., 1:] > 0) & ok0], -1), -1).astype(bool)
    i = np.sqrt(np.maximum(2 * wgt / np.asarray(L, float)[..., None], 0))
    di = 0.5 * (i[..., 1:] + i[..., :-1])
    dt = np.where(alive[..., 1:], np.diff(qr, axis=-1) / np.maximum(di, 1e-9), np.inf)
    t = np.concatenate([np.zeros(dt.shape[:-1] + (1,)), np.cumsum(dt, -1)], -1)
    reached = alive & (t <= t_left[..., None])
    kmax = SG.size - 1 - np.argmax(reached[..., ::-1], -1)                         # last reached grid point
    kmax = np.where(reached.any(-1), kmax, 0)
    done = kmax == SG.size - 1
    v_res = np.asarray(vb, float) * (1 - SG[kmax])
    t_tr = np.where(done, t0 + t[..., -1], np.inf)
    i_end = np.where(done, i[..., -1], 0.0)
    half = SG.size // 2
    zvs_transition.t_half = np.where(reached[..., half], t0 + t[..., half], np.inf)
    zvs_transition.t0 = t0
    return t_tr, np.where(done, 0.0, v_res), i_end


def e_cap_turn_on(v_res, vb, cq=1.0, cn=1.0, qf=None, ef=None, c_node=None):
    """Energy dissipated when a switch turns on with v_res still across it (own C_oss dump plus recharging the
    complementary switch from the bus), plus the node capacitance.  cq/cn: C_oss and node-capacitance multipliers;
    qf/ef: Q_oss/E_oss functions of one switch position (default: cq x the incumbent's curves)."""
    qf = (lambda x: cq * dev.qoss(x)) if qf is None else qf
    ef = (lambda x: cq * dev.eoss(x)) if ef is None else ef
    cnd = a('c_node') if c_node is None else c_node
    return ef(v_res) + (qf(vb) - qf(vb - v_res)) * vb - (ef(vb) - ef(vb - v_res)) + 0.5 * cn * cnd * v_res ** 2


def events(o):
    """The four switching events of one half period (legs A, B primary; C, D secondary)."""
    v2p = o['n'] * o['v2']
    d = 1e-9
    vp = lambda th: o['v1'] * _w(th, o['a1'])
    vs = lambda th: v2p * _w(th - o['phi'], o['a2'])
    th = {'A': o['a1'] + 0 * o['phi'], 'B': 0 * o['phi'], 'C': o['phi'] + o['a2'], 'D': o['phi'] + 0 * o['a2']}
    ev = {}
    for k in 'ABCD':
        il, im = currents(o, th[k])
        vl0 = vp(th[k] - d) - vs(th[k] - d)
        if k in 'AB':
            merged = np.asarray(o['a1']) == 0
            iz = -il
            ev[k] = dict(iz=iz, vb=o['v1'] + 0 * iz, nref=1.0 + 0 * iz, vl0=vl0, eps=-1.0 + 0 * iz,
                         kappa=np.where(merged, 2.0, 1.0) + 0 * iz,
                         weight=(np.where(merged, 0.0, 1.0) if k == 'B' else 1.0) + 0 * iz)
        else:
            merged = np.asarray(o['a2']) == 0
            iz = il - im
            ev[k] = dict(iz=iz, vb=o['v2'] + 0 * iz, nref=o['n'] + 0 * iz, vl0=vl0, eps=1.0 + 0 * iz,
                         kappa=np.where(merged, 2.0, 1.0) + 0 * iz,
                         weight=(np.where(merged, 0.0, 1.0) if k == 'D' else 1.0) + 0 * iz)
    return ev


# ---------------------------------------------------------------------------------------------------------
# Magnetics: transformer on TDK PM 114/93 N95 + external gapped inductor (reference implementation for the RFQ)
# ---------------------------------------------------------------------------------------------------------
MU0 = 4e-7 * PI
ALPHA = dev.STEINMETZ_ALPHA
C_ALPHA = float(np.mean(np.abs(np.cos(np.linspace(0, 2 * PI, 20001)[:-1])) ** ALPHA))   # mean |cos|^alpha


def rho_cu(t):
    return 1.72e-8 * (1 + 0.00393 * (t - 20.0))


def design_magnetics(n, L, Lm, N1):
    """Turns, resistances and leakage of the PM 114/93 transformer plus the external inductor."""
    c = dev.CORE_PM114
    N2 = int(round(N1 / n))
    hw = 0.0623 - 2 * 3e-3                       # bobbin winding height (p3 drawing 62.3 mm) minus 3 mm margins
    aw_cu = a('k_cu_window') * c['aw_bobbin'] / 2   # copper area per winding
    rdc1 = rho_cu(a('T_xfmr')) * N1 * c['mlt'] / (aw_cu / N1)
    rdc2 = rho_cu(a('T_xfmr')) * N2 * c['mlt'] / (aw_cu / N2)
    build = (c['aw_bobbin'] - a('b_iso') * 0.0623) / 2 / 0.0623      # radial build of each winding
    llk = MU0 * N1 ** 2 * c['mlt'] * (a('b_iso') + 2 * build / 3) / hw
    lext = L - llk
    # external inductor: same core family, gapped; turns = minimum core+copper loss at 800 V/800 V/60 kW
    # subject to B <= 0.25 T at the 220 A protection peak (SPS trapezoid, iGSE factor (phi/pi)(2/phi)^a/c_a)
    phi = phi_sps(800.0, 800.0, n, L, 60e3)
    ipk = 800.0 * phi / (W * L)
    nmin = int(np.ceil(lext * 220.0 / (0.25 * c['ae'])))
    best = None
    for nl in range(max(nmin, 1), 13):
        if MU0 * nl ** 2 * c['ae'] / lext > a('gap_l_max'):
            break
        b = lext * ipk / (nl * c['ae'])
        pc = dev.pv_sine('N95', b, a('T_xfmr')) * c['ve'] * (phi / PI) * (2 / phi) ** ALPHA / C_ALPHA
        rdc_l = rho_cu(a('T_xfmr')) * nl * c['mlt'] / (0.40 * c['aw_bobbin'] / nl)
        pt = pc + 2.0 * (60e3 / 800.0 * 1.12) ** 2 * rdc_l
        if best is None or pt < best[0]:
            best = (pt, nl, rdc_l)
    _, nl, rdc_l = best
    gap = MU0 * nl ** 2 * c['ae'] / lext
    gap_m = MU0 * N1 ** 2 * c['ae'] / Lm                                  # transformer gap for Lm
    return dict(n=N1 / N2, n_target=n, N1=N1, N2=N2, L=L, Llk=llk, Lext=lext, Lm=Lm, ae=c['ae'], ve=c['ve'],
                rdc1=rdc1, rdc2=rdc2, nl=nl, gap_l=gap, rdc_l=rdc_l, gap_m=gap_m, aw_cu=aw_cu,
                lm_ungapped=N1 ** 2 * c['al_ungapped'])


def _harm_rms2(o, nh=31, m=256):
    """Squared RMS of the odd harmonics 1..nh of i_L and i_s' (primary-referred), shape (..., nh//2+1)."""
    th = np.linspace(0, 2 * PI, m, endpoint=False)
    il, im = currents(_ex(o), th)
    sp_l = np.fft.rfft(il, axis=-1) / m
    sp_s = np.fft.rfft(il - im, axis=-1) / m
    h = np.arange(1, nh + 1, 2)
    return h, 2 * np.abs(sp_l[..., h]) ** 2, 2 * np.abs(sp_s[..., h]) ** 2


def _fr(h, fr1, cap):
    return np.minimum(1 + (fr1 - 1) * h ** 2, cap)


def vl_alpha_mean(o, alpha=None):
    """Mean of |v_L|^alpha over the period (exact, piecewise constant) for the inductor iGSE."""
    al = ALPHA if alpha is None else alpha
    shp = np.broadcast(*[np.asarray(v) for v in o.values()]).shape
    z = np.zeros(shp)
    bp = np.sort(np.stack([z, z + o['a1'], z + np.mod(o['phi'], PI), z + np.mod(o['phi'] + o['a2'], PI), z + PI],
                          -1), -1)
    dt, tm = np.diff(bp, axis=-1), 0.5 * (bp[..., 1:] + bp[..., :-1])
    e = _ex(o)
    vl = e['v1'] * _w(tm, e['a1']) - e['n'] * e['v2'] * _w(tm - e['phi'], e['a2'])
    return np.sum(np.abs(vl) ** al * dt, -1) / PI


# ---------------------------------------------------------------------------------------------------------
# Loss, thermal and efficiency evaluation (vectorised over operating points)
# ---------------------------------------------------------------------------------------------------------
CAP1 = ('C4AQUEW5450A3BJ', 3)      # port 1 (590-950 V): 1300 V class, see cap_banks()
CAP2 = ('C4AQQEW5650A3BJ', 3)      # port 2 (400-900 V): 1100 V class


# Switch-option profiles ('mod').  kind 'gm4' = the incumbent CBB011M12GM4T with its own curves (dev.* functions,
# results identical to the previous model; the FM3 / x2 levers scale them by table ratios).  kind 'generic' = a part
# of the device layer (dab_devices g_* functions) with npar devices per switch position and a thermal path 'module'
# (R_th j-h per position + cold plate per position) or 'discrete' (R_th j-c + insulator stack + cold-plate footprint
# per device; the hottest device of a position carries the current-sharing factors).
MOD_GM4 = dict(name='1 x CBB011M12GM4T per bridge', kind='gm4', npar=1, modules_per_bridge=1, rds=None, rds18=None,
               r_pkg125=None, k_eon=1.0, k_eoff=1.0, k_err=1.0, k_coss=1.0, k_vsd=1.0, rth_jh=dev.MODULE['rth_jh'],
               thermal='module', i_term_rms=dev.MODULE['id_dc'], l_bus=None, short='GM4')
MOD_GM4x2 = dict(MOD_GM4, name='2 x CBB011M12GM4T in parallel per bridge', npar=2, modules_per_bridge=2)
_f3 = dev.FM3['CAB011M12FM3']
MOD_FM3 = dict(MOD_GM4, name='2 x CAB011M12FM3 half bridges per bridge', modules_per_bridge=2, rds=_f3['rds_on'],
               rds18=_f3['rds_on_18v'], r_pkg125=_f3['r_pkg'],
               k_eon=_f3['eon_600_100'] / dev.MODULE['eon_600_100'][25],
               k_eoff=_f3['eoff_600_100'] / dev.EOFF_RG1_600_100,          # same RG(off) = 1 ohm basis
               k_err=_f3['err_600_100_125C'] / dev.MODULE['err_600_100'][125],
               k_coss=_f3['coss_800'] / dev.MODULE['coss_800'], k_vsd=_f3['vsd_100A'][25] / dev.MODULE['vsd_100A'][25],
               rth_jh=_f3['rth_jh'])
DESIGN = MOD_GM4          # replaced by the option chosen in device_options() (main)
OPTIONS = []
COST_OTHER = {   # USD per module and basis for the parts whose cost basis comes from the sourcing study
    'transformer': (220.0, 'unchanged estimate (costed BOM)'), 'series inductor': (130.0, 'unchanged estimate'),
    'cold plate': (120.0, 'unchanged estimate'), 'coolant flow switch': (131.0, 'unchanged estimate'),
    'coolant thermostat': (69.0, 'unchanged estimate')}
DESIGN_RAIL = (18.0, -3.5)   # V gate rails of the Asian options (dab_devices.DESIGN_RAIL_NOTE)


def _rds(mod, tj, vgs18):
    if mod['rds'] is None:
        return dev.rds_on(tj, vgs18)
    tab = mod['rds18'] if vgs18 else mod['rds']
    t, r = np.array(list(tab)), np.array(list(tab.values()))
    return np.polyval(np.polyfit(t, r, 2), tj)


def _rpkg(mod, ths):
    if mod['r_pkg125'] is None:
        return dev.r_pkg(ths)
    return mod['r_pkg125'] * (1 + dev.MODULE['r_pkg_tc'] * (np.asarray(ths) - 125.0))


def _qe(mod):
    """Q_oss and E_oss functions of one switch position and the node capacitance of one leg [F]."""
    n = mod['npar']
    if mod['kind'] == 'gm4':
        cq = n * mod['k_coss']
        return (lambda x: cq * dev.qoss(x)), (lambda x: cq * dev.eoss(x)), n * a('c_node')
    d = mod['d']
    return (lambda x: n * dev.g_qoss(d, x)), (lambda x: n * dev.g_eoss(d, x)), mod['c_node']


def _e_pos(mod, kind, ipos, vb, tj):
    """Switching energy [J] of one switch position (npar devices) at the position current ipos [A]."""
    n = mod['npar']
    idie = ipos / n
    if mod['kind'] == 'gm4':
        if kind == 'eoff':
            e_rg = dev.EOFF_RG_SLOPE * a('RG_off') * (idie / 100.0) * (vb / 600.0)   # Fig. 15, scaled I x V
            return n * mod['k_eoff'] * (dev.esw('eoff', idie, vb, tj) + e_rg)
        return n * mod['k_' + kind] * dev.esw(kind, idie, vb, tj)
    return n * dev.g_esw(mod['d'], kind, idie, vb, tj, mod['rge_off'] if kind == 'eoff' else mod['rge_on'])


def _vsd_pos(mod, ipos, tj):
    if mod['kind'] == 'gm4':
        return mod['k_vsd'] * dev.vsd(ipos / mod['npar'], tj)
    return dev.g_vsd(mod['d'], ipos / mod['npar'], tj)


def _rpos(mod, tj, ths, vgs18):
    """Conduction resistance of one switch position [ohm] (npar devices treated as equal: the mismatch only moves
    loss between them, see share())."""
    if mod['kind'] == 'gm4':
        return (_rds(mod, tj, vgs18) + _rpkg(mod, ths)) / mod['npar']
    return (dev.g_rds(mod['d'], tj) + mod['d'].get('r_pkg', 0.0)) / mod['npar']


def share(mod):
    """Hottest-device factors for npar paralleled devices: (current factor, conduction-loss factor, switching-loss
    factor).  R_DS(on) spread: one device at R(1-dr), the others at R(1+dr) with dr = (R_max/R_typ - 1)/2 of the
    worse maker (half the guaranteed range on each side, no electrothermal self-balancing credited); switching:
    mod['k_sw'] (threshold spread, ASSUMPTION stated in the option table)."""
    n = mod['npar']
    if mod['kind'] == 'gm4' or n == 1:
        return 1.0, 1.0, 1.0
    dr = mod['dr']
    s = n / (1 + (n - 1) * (1 - dr) / (1 + dr))
    return s, s * s * (1 - dr), mod['k_sw']


def evaluate(v1, v2, p, D, a1=0.0, a2=0.0, tdead=None, t_in=None, vgs18=False, cap1=CAP1, cap2=CAP2,
             iters=3, xfmr_override=None, mod=None, extra=None, k_cu=1.0, hw=None):
    """Steady state at OUTPUT power p (signed; + = port 1 -> port 2).  tdead=None: adaptive dead time = firmware LUT
    (predicted ZVS transition + margin, 50-300 ns) but never shorter than the hardware shoot-through guard, whose
    WORST-CASE (longest) gate-level value hw[1] is used for the losses (DR-02); a number = fixed dead time, no guard.
    mod: switch-option profile (None = DESIGN); extra = (k0 [W], k1 [W/W], 'modules' | 'magnetics'): pessimistic
    unexplained loss k0 + k1*|p| added to the efficiency and to the heat of the chosen components; k_cu scales the
    magnetics copper loss; hw = (min, max) guard window [s] (None = the option's, else A['t_dead_hw'])."""
    mod = DESIGN if mod is None else mod
    if mod['kind'] == 'pair':                       # worse of the two makers at every point
        kw_ = dict(a1=a1, a2=a2, tdead=tdead, t_in=t_in, vgs18=vgs18, cap1=cap1, cap2=cap2, iters=iters,
                   xfmr_override=xfmr_override, extra=extra, k_cu=k_cu, hw=hw)
        rs = [evaluate(v1, v2, p, D, mod=m_, **kw_) for m_ in mod['members']]
        worse = np.where(np.nan_to_num(rs[1]['ploss'], nan=np.inf) > np.nan_to_num(rs[0]['ploss'], nan=np.inf), 1, 0)
        out = {k: (np.where(worse == 1, rs[1][k], rs[0][k]) if np.ndim(rs[0][k]) == np.ndim(worse) and
                   np.shape(rs[0][k]) == np.shape(worse) else rs[0][k]) for k in rs[0]}
        for k in ('tj1', 'tj2', 'ths1', 'ths2', 'idev1', 'idev2', 'ioff1', 'ioff2', 't_xfmr', 'vres1', 'vres2', 'q_cool'):
            out[k] = np.maximum(rs[0][k], rs[1][k])
        out['zvs1'], out['zvs2'] = rs[0]['zvs1'] & rs[1]['zvs1'], rs[0]['zvs2'] & rs[1]['zvs2']
        out['eta'] = np.minimum(rs[0]['eta'], rs[1]['eta'])
        out['ploss'] = np.maximum(rs[0]['ploss'], rs[1]['ploss'])
        return out
    t_in = a('T_coolant_in') if t_in is None else t_in
    v1, v2, p, a1, a2 = np.broadcast_arrays(*[np.asarray(x, float) for x in (v1, v2, p, a1, a2)])
    mcp = a('coolant_flow') / 60e3 * a('coolant_rho_cp')
    rhf = a('Rth_hf') * (1 + a('k_xheat'))
    npar = mod['npar']
    npos = 4 * npar                                          # switch positions (or devices) sharing a bridge's loss
    qf, ef, cnd = _qe(mod)
    k_i, k_c, k_s = share(mod)
    hw = (mod.get('hw_dt') or a('t_dead_hw')) if hw is None else hw
    l_bus = mod.get('l_bus') or a('L_bus')
    tj1 = np.full(v1.shape, 90.0)
    tj2 = tj1.copy()
    ths1, ths2 = tj1 - 20, tj2 - 20
    ploss = 0.0 * v1
    p_x = 0.0 * v1 if extra is None else extra[0] + extra[1] * np.abs(p)
    alloc = None if extra is None else extra[2]
    t_max = max(a('t_dead_max'), hw[1]) if tdead is None else tdead
    sps = bool(np.all(a1 == 0) and np.all(a2 == 0))
    for _ in range(iters):
        ptr = p + np.sign(p) * 0.5 * ploss                     # transferred power through the transformer
        o = dict(v1=v1, v2=v2, n=D['n'], L=D['L'], Lm=D['Lm'], a1=a1, a2=a2, phi=0.0)
        o['phi'] = phi_sps(v1, v2, D['n'], D['L'], ptr) if sps else solve_phi(o, ptr)
        o['phi'] = np.broadcast_to(o['phi'], v1.shape)
        s = stats(o)
        # --- switching events ---
        ev = events(o)
        psw = {1: 0.0 * v1, 2: 0.0 * v1}
        pdio = {1: 0.0 * v1, 2: 0.0 * v1}
        pring = {1: 0.0 * v1, 2: 0.0 * v1}
        zvs = {1: np.ones(v1.shape, bool), 2: np.ones(v1.shape, bool)}
        ioff = {1: 0.0 * v1, 2: 0.0 * v1}
        vres_max = {1: 0.0 * v1, 2: 0.0 * v1}
        for k, e in ev.items():
            br = 1 if k in 'AB' else 2
            tj = tj1 if br == 1 else tj2
            t_tr, v_res, i_end = zvs_transition(e['iz'], e['vb'], e['nref'], e['vl0'], e['eps'], e['kappa'],
                                                D['L'], t_max, qf=qf, c_node=cnd)
            t0 = zvs_transition.t0
            ia = np.abs(e['iz']) * e['nref']                     # actual current of the switch position
            ie = i_end * e['nref']
            hard = (e['iz'] <= 0) & (t0 >= t_max)               # wrong polarity, no recovery in the dead time
            rec = (e['iz'] < 0) & ~hard                          # recovers through zero, then swings
            done = np.isfinite(t_tr)
            if tdead is None:
                td = np.where(done, np.clip(t_tr + a('t_dead_margin'), a('t_dead_min'), a('t_dead_max')),
                              np.where(hard, a('t_dead_min'), a('t_dead_max')))
                td = np.maximum(td, hw[1])                       # hardware guard: worst-case gate-level window
            else:
                td = np.full(v1.shape, tdead)
            eoff = np.where(hard | rec, 0.0, _e_pos(mod, 'eoff', ia, e['vb'], tj))
            eon = np.where(hard, _e_pos(mod, 'eon', ia, e['vb'], tj) + _e_pos(mod, 'err', ia, e['vb'], tj),
                           np.where(done, 0.0, e_cap_turn_on(v_res, e['vb'], qf=qf, ef=ef, c_node=cnd)))
            vs_ = lambda i_: _vsd_pos(mod, i_, tj)
            edio = np.where(hard, vs_(ia) * ia * td,
                            np.where(done, vs_(ie) * ie * np.maximum(td - np.where(done, t_tr, 0), 0), 0)
                            + np.where(rec, vs_(ia / 2) * ia / 2 * np.minimum(t0, td), 0))
            psw[br] = psw[br] + 2 * F * e['weight'] * e['kappa'] * (eoff + eon + edio)
            pdio[br] = pdio[br] + 2 * F * e['weight'] * e['kappa'] * edio
            # bridge DC-side current steps by kappa*I at each event: 0.5 L_bus dI^2 rings down in the terminal network
            pring[br] = pring[br] + 2 * F * e['weight'] * 0.5 * l_bus * (e['kappa'] * ia) ** 2
            zvs[br] &= (e['weight'] == 0) | done
            ioff[br] = np.maximum(ioff[br], np.where(hard | rec, 0.0, ia) * (e['weight'] > 0))
            vres_max[br] = np.maximum(vres_max[br], np.where(hard, e['vb'], v_res) * (e['weight'] > 0))
        # --- conduction (npar devices per position) ---
        r1, r2 = _rpos(mod, tj1, ths1, vgs18), _rpos(mod, tj2, ths2, vgs18)
        pc1, pc2 = 2 * s['IL'] ** 2 * r1, 2 * s['Is'] ** 2 * r2
        # --- transformer (MAG_CORR: correction factors from the magnetics verification, default 1) ---
        b_pk = D['n'] * v2 * (PI - a2) / (2 * W * D['N1'] * D['ae'])
        f_shape = ((PI - a2) / PI) * (2 / (PI - a2)) ** ALPHA / C_ALPHA
        p_core = MAG_CORR['k_core_xfmr'] * f_shape * dev.pv_sine('N95', b_pk, a('T_xfmr')) * D['ve']
        h, hl, hs = _harm_rms2(o)
        fr = _fr(h, a('Fr1_litz'), 1e9)
        p_cu = MAG_CORR['k_cu_xfmr'] * k_cu * (np.sum(hl * fr, -1) * D['rdc1'] + np.sum(hs * fr, -1) * D['n'] ** 2 *
                                               D['rdc2'])
        if xfmr_override is not None:                            # CRD calibration: measured Rw / Rc at 100 kHz,
            p_cu = xfmr_override['rw'] * np.sum(hl * fr / fr[0], -1)   # harmonics grown with our F_r(h) law (estimate)
            p_core = s['Im'] ** 2 * xfmr_override['rc']
        # --- external inductor ---
        bl = D['Lext'] * s['ILpk'] / (D['nl'] * D['ae'])
        pl_core = MAG_CORR['k_core_ind'] * dev.pv_sine('N95', bl, a('T_xfmr')) * D['ve'] * vl_alpha_mean(o) / \
            np.maximum((W * D['L'] * s['ILpk']) ** ALPHA * C_ALPHA, 1e-30)
        # gap-fringing proximity loss ~ sum I_h^2 h^2 (fits the independent model at both of its points, MG-05)
        pl_cu = MAG_CORR['k_cu_ind'] * k_cu * np.sum(hl * h ** 2, -1) * D['rdc_l']
        if xfmr_override is not None:
            pl_core, pl_cu = 0 * v1, 0 * v1
        elif MAGD is not None:                                   # magnetics design files (replace our model)
            p_core, p_cu, pl_core, pl_cu, b_pk, bl = mag_losses(o, s, h, hl, a2, v2)
            p_cu, pl_cu = k_cu * p_cu, k_cu * pl_cu
        # --- capacitors, interconnect ---
        c1, c2 = dev.CAPS[cap1[0]], dev.CAPS[cap2[0]]
        p_cap = s['IC1'] ** 2 * c1['esr'] / cap1[1] + s['IC2'] ** 2 * c2['esr'] / cap2[1]
        p_bus = a('R_bus') * (s['I1'] ** 2 + s['I2'] ** 2) + a('R_ac_bus') * s['IL'] ** 2
        # --- thermal: coolant through bridge 2, bridge 1, then the magnetics plate ---
        pm1, pm2 = pc1 + psw[1], pc2 + psw[2]
        hm = 0.5 * p_x if alloc == 'modules' else 0.0 * v1     # pessimistic extra heat per bridge
        hx = p_x if alloc == 'magnetics' else 0.0 * v1          # ... or in the transformer
        q1, q2 = pm1 + hm, pm2 + hm
        tc2 = t_in + 0.5 * q2 / mcp
        tc1 = t_in + (q2 + 0.5 * q1) / mcp
        if mod['thermal'] == 'module':
            ths1, ths2 = tc1 + q1 / npos * rhf, tc2 + q2 / npos * rhf
            tj1, tj2 = ths1 + q1 / npos * mod['rth_jh'], ths2 + q2 / npos * mod['rth_jh']
        else:                                                    # discretes: hottest device of the bridge
            kq1 = (pc1 * k_c + psw[1] * k_s + hm) / np.maximum(q1, 1e-9)
            kq2 = (pc2 * k_c + psw[2] * k_s + hm) / np.maximum(q2, 1e-9)
            ths1 = tc1 + q1 / npos * (1 + mod['k_x']) * mod['rth_cp']
            ths2 = tc2 + q2 / npos * (1 + mod['k_x']) * mod['rth_cp']
            tj1 = ths1 + q1 / npos * kq1 * (mod['d']['rth_jc'] + mod['rth_ins'])
            tj2 = ths2 + q2 / npos * kq2 * (mod['d']['rth_jc'] + mod['rth_ins'])
        ploss = pm1 + pm2 + p_core + p_cu + pl_core + pl_cu + p_cap + p_bus + p_x + pring[1] + pring[2]
    t_cool_x = t_in + (q1 + q2) / mcp
    if MAGD is not None and xfmr_override is None:            # hot spots per the design files (per unit / inductor)
        nu = MAGD['xfmr']['core']['n_units']
        t_xfmr = np.maximum(t_cool_x + ((p_core + hx) / nu + p_cu / nu * (1 + a('xfmr_unit_mismatch')) ** 2) *
                            MAGD['xfmr']['thermal']['Rth_hotspot_to_coolant_K_W'],
                            t_cool_x + (pl_core + pl_cu) * MAGD['ind']['thermal']['Rth_winding_K_W'])
    elif a('Rth_xfmr'):
        t_xfmr = t_cool_x + (p_core + p_cu + hx) * a('Rth_xfmr')
    else:
        t_xfmr = np.full(np.shape(t_cool_x), np.nan)
    eta = np.abs(p) / np.maximum(np.abs(p) + ploss, 1e-9)
    return dict(v1=v1, v2=v2, p=p, phi=o['phi'], a1=a1, a2=a2, **s, pc1=pc1, pc2=pc2, psw1=psw[1], psw2=psw[2],
                p_core=p_core, p_cu=p_cu, pl_core=pl_core, pl_cu=pl_cu, p_cap=p_cap, p_bus=p_bus, ploss=ploss,
                eta=eta, tj1=tj1, tj2=tj2, ths1=ths1, ths2=ths2, zvs1=zvs[1], zvs2=zvs[2], ioff1=ioff[1],
                ioff2=ioff[2], vres1=vres_max[1], vres2=vres_max[2], b_pk=b_pk, b_l=bl,
                ipos1=s['IL'] / np.sqrt(2) / npar, ipos2=s['Is'] / np.sqrt(2) / npar, p_xfmr=p_core + p_cu,
                idev1=s['IL'] / np.sqrt(2) / npar * k_i, idev2=s['Is'] / np.sqrt(2) / npar * k_i,
                p_ind=pl_core + pl_cu, p_extra=p_x, t_xfmr=t_xfmr, t_cool_x=t_cool_x, pdio1=pdio[1], pdio2=pdio[2],
                q_cool=q1 + q2 + p_core + p_cu + hx + pl_core + pl_cu, p_ring1=pring[1], p_ring2=pring[2])


# ---------------------------------------------------------------------------------------------------------
# Design selection: turns ratio n and total series inductance L
# ---------------------------------------------------------------------------------------------------------
WIN_V1 = np.array([600.0, 700.0, 800.0, 900.0])        # DAB-10 full-load window
WIN_V2 = np.array([700.0, 800.0, 900.0])               # DAB-11 full-power window
LM_DEFAULT = 300e-6


def window_points(powers=(20e3, 40e3, 60e3)):
    v1, v2, p = np.meshgrid(WIN_V1, WIN_V2, np.asarray(powers, float), indexing='ij')
    return v1.ravel(), v2.ravel(), p.ravel()


def dv_os(i, l_loop=None, mod=None, v=800.0):
    """Turn-off overshoot at the dies [V] for the switch-position turn-off current i at bus voltage v.  Incumbent:
    implied by DS Fig. 19 (1200 V minus the V_DD limit at I_off, vector data), scaled from the 24 nH test loop to
    l_loop.  Other parts (no switching-SOA curve published): in a ZVS turn-off the channel stops within the delay
    and the leg current commutates capacitively, di/dt = I / t_swing with t_swing = Q_leg(v) / I, so
    dV = k_os x L_loop x I^2 / Q_leg(v), Q_leg = 2 Q_oss,position(v) + C_node v; k_os calibrated to the ngspice decks
    divided by their known harshness against a datasheet SOA (1.47 on the GM4, see I_CH_MAX)."""
    mod = DESIGN if mod is None else mod
    if mod['kind'] == 'gm4':
        l_ = a('L_loop') if l_loop is None else l_loop
        return (1200.0 - dev._lin(i, dev.SSOA['turn_off_i'], dev.SSOA['turn_off_v'])) * l_ / dev.SSOA['l_total']
    l_ = mod['l_loop'] if l_loop is None else l_loop
    qf, _, cn = _qe(mod)
    q_leg = 2 * qf(np.asarray(v, float)) + cn * np.asarray(v, float)
    return a('k_os') * l_ * np.asarray(i, float) ** 2 / q_leg


def current_ok(r, mod=None):
    """Current rules: incumbent / modules - per-position RMS <= I_pos_rms_max and the AC-terminal RMS (the whole
    transformer current flows through each AC terminal, DR-01) <= I_term_frac x the terminal rating; discretes - the
    hottest paralleled device's RMS (sharing factor) <= I_term_frac x its lead rating."""
    mod = DESIGN if mod is None else mod
    il = np.maximum(r['IL'], r['Is'])
    if mod['thermal'] == 'module':
        i_pos_lim = a('I_pos_rms_max') if mod['kind'] == 'gm4' else a('I_term_frac') * mod['i_pos_rms']
        return (np.maximum(r['ipos1'], r['ipos2']) <= i_pos_lim) & \
            (il / mod['npar'] <= a('I_term_frac') * mod['i_term_rms'])          # npar modules in parallel
    return np.maximum(r['idev1'], r['idev2']) <= a('I_term_frac') * mod['d']['i_rms_lead']


def constraints_ok(r, mod=None):
    """Per-point feasibility at full power: Tj, current rules, SPS phase reserve, turn-off SSOA."""
    ssoa = ssoa_ok(r, mod=mod)
    return ((np.maximum(r['tj1'], r['tj2']) <= a('Tj_max_design')) & current_ok(r, mod) &
            (np.abs(r['phi']) <= np.radians(60)) & ssoa & np.isfinite(r['phi']))


def sweep_n_L(ns=np.round(np.arange(0.84, 1.161, 0.02), 3), ls=np.arange(4.0, 10.01, 0.5) * 1e-6, N1=10):
    v1, v2, p = window_points()
    full = p == 60e3
    res = []
    for n in ns:
        for L in ls:
            D = design_magnetics(n, L, LM_DEFAULT, N1)
            D['n'] = n                                    # continuous n for the electrical sweep
            r = evaluate(v1, v2, p, D)
            ok = constraints_ok(r)[full]
            res.append(dict(n=n, L=L, J=float(np.mean(r['ploss'])), feasible=bool(ok.all()),
                            n_bad=int((~ok).sum()), worst_tj=float(np.max(np.maximum(r['tj1'], r['tj2'])[full])),
                            eta_min_20_60=float(np.min(r['eta'])), zvs_frac=float(np.mean(r['zvs1'] & r['zvs2']))))
    return res


# ---------------------------------------------------------------------------------------------------------
# Modulation: best of SPS / EPS / DPS / TPS by exhaustive grid over the inner shifts
# ---------------------------------------------------------------------------------------------------------
TPS_GRID = np.radians([0, 5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 90])


def ssoa_ok(r, l_loop=None, mod=None):
    """Turn-off SOA: V_bus + dv_os(I_off) <= 1200 V on both bridges (incumbent: datasheet Fig. 19)."""
    return np.maximum(r['v1'] + dv_os(r['ioff1'], l_loop, mod, r['v1']),
                      r['v2'] + dv_os(r['ioff2'], l_loop, mod, r['v2'])) <= 1200.0


def best_modulation(v1, v2, p, D, grid=TPS_GRID, **kw):
    """Minimum-loss inner shifts (a1, a2) per point among those that respect the turn-off SSOA."""
    v1, v2, p = np.broadcast_arrays(*[np.asarray(x, float).ravel() for x in (v1, v2, p)])
    g1, g2 = np.meshgrid(grid, grid, indexing='ij')
    g1, g2 = g1.ravel(), g2.ravel()
    r = evaluate(v1[:, None], v2[:, None], p[:, None], D, a1=g1[None, :], a2=g2[None, :], **kw)
    cost = np.where(np.isfinite(r['ploss']) & np.isfinite(r['phi']), r['ploss'], np.inf)
    cost = np.where(ssoa_ok(r, mod=kw.get('mod')), cost, cost + 1e6)   # SSOA violation only if unavoidable
    k = np.argmin(cost, -1)
    pick = {key: (val[np.arange(len(k)), k] if np.ndim(val) == 2 else val) for key, val in r.items()}
    return pick


TPS_COARSE = np.radians([0, 15, 30, 50, 75])


def sweep_n_L_tps(ns=(0.90, 0.94, 0.97, 1.00, 1.03, 1.06), ls=np.arange(5.0, 9.01, 0.5) * 1e-6, N1=10):
    """Selection with the best modulation at every point (coarse TPS grid).  Objective: mean loss over the
    full-power window at 20/40/60 kW; feasibility counted at 60 kW (Tj, RMS, SSOA)."""
    v1, v2, p = window_points()
    full = p == 60e3
    res = []
    for n in ns:
        for L in ls:
            D = design_magnetics(n, L, LM_DEFAULT, N1)
            D['n'] = n
            r = best_modulation(v1, v2, p, D, grid=TPS_COARSE)
            ok = (constraints_ok(r) | ~full) & ssoa_ok(r)
            res.append(dict(n=float(n), L=float(L), J=float(np.mean(r['ploss'])), n_bad=int((~ok).sum()),
                            bad=[(float(v1[i]), float(v2[i]), float(p[i])) for i in np.where(~ok)[0]],
                            worst_tj=float(np.max(np.maximum(r['tj1'], r['tj2']))),
                            eta_min=float(np.min(r['eta'])), eta_mean=float(np.mean(r['eta']))))
    return res


def select_turns(pairs=((9, 10), (10, 11), (11, 12), (12, 13), (13, 14), (14, 15), (10, 10), (11, 11)),
                 ls=(5.5e-6, 6.0e-6, 6.5e-6)):
    """Realisable N1:N2 near the electrical optimum; full magnetics (core+copper) included in the objective."""
    v1, v2, p = window_points()
    full = p == 60e3
    out = []
    for n1, n2 in pairs:
        for L in ls:
            D = design_magnetics(n1 / n2, L, LM_DEFAULT, n1)
            if D['Lext'] <= 0.3e-6:
                continue
            r = best_modulation(v1, v2, p, D, grid=TPS_COARSE)
            ok = (constraints_ok(r) | ~full) & ssoa_ok(r)
            bpk950 = max(950.0, D['n'] * 900.0) / (4 * F * n1 * D['ae'])
            out.append(dict(N1=n1, N2=n2, n=n1 / n2, L=L, J=float(np.mean(r['ploss'])), n_bad=int((~ok).sum()),
                            bpk950=bpk950, eta_mean=float(np.mean(r['eta'])), llk=D['Llk'], lext=D['Lext']))
    return out


def crd_calibration():
    """Our loss model run with the CRD60DD12N-GMB configuration vs the measured curve (UG Fig. 57)."""
    D = design_magnetics(1.0, dev.CRD['xfmr_lsigma'], dev.CRD['xfmr_lm'], 10)
    D['n'] = 1.0
    pm = dev.CRD['eff_meas'][:, 0] * 1e3
    r = evaluate(800.0, 800.0, pm, D, tdead=dev.CRD['test']['tdead'], t_in=20.0,
                 cap1=('C4AQQEW5650A3BJ', 2), cap2=('C4AQQEW5650A3BJ', 3),
                 xfmr_override={'rw': dev.CRD['xfmr_rw'], 'rc': dev.CRD['xfmr_rc']}, mod=MOD_GM4)
    return pm, dev.CRD['eff_meas'][:, 1] / 100, r


def calibration_gap(R, D):
    """DAB-04 honesty: what the model of the CRD lacked, quantified at the CRD test points (800 / 800 V, 10.8-22.7 kW,
    UG Fig. 57).  The CRD model already used the measured transformer R_w = 78 mOhm at 100 kHz for the fundamental;
    this round adds the harmonics (our F_r(h) law applied to the measured R_w - an estimate).  The remaining gap is the
    'residual' that the calibrated claim carries."""
    pm, em, rc = R['crd']
    meas = pm * (1 / em - 1)
    fund = dev.CRD['xfmr_rw'] * rc['IL'] ** 2
    terms = {
        'dead_time_body_diode_W': rc['pdio1'] + rc['pdio2'],               # in the model (CRD 200 ns, fixed)
        'ring_down_hf_caps_snubbers_W': rc['p_ring1'] + rc['p_ring2'],       # in the model
        'xfmr_copper_fundamental_W': fund,                                   # in the model since D-015
        'xfmr_copper_harmonics_W': rc['p_cu'] - fund,                        # added this round (estimate)
        'coss_hysteresis_W': 8 * a('k_coss_hyst') * float(dev.eoss(800.0)) * F + 0 * pm,   # NOT modelled
        'gate_drive_if_counted_W': 8 * (dev.MODULE['qg'] * 19.0 * F + 0.5) + 0 * pm,      # outside the HV ports
        'sensing_if_counted_W': 1.5 + 0 * pm,                                               # outside the HV ports
    }
    gap_old = meas - (rc['ploss'] - terms['xfmr_copper_harmonics_W'])
    resid = meas - rc['ploss']
    return dict(p_W=pm.tolist(), measured_loss_W=meas.tolist(), model_loss_W=rc['ploss'].tolist(),
                gap_before_this_round_W=gap_old.tolist(), residual_gap_W=resid.tolist(),
                **{k: np.asarray(v).tolist() for k, v in terms.items()},
                share_explained_by_harmonics=float(np.mean(terms['xfmr_copper_harmonics_W'] / gap_old)),
                share_with_hysteresis_and_aux=float(np.mean((terms['xfmr_copper_harmonics_W'] +
                                                             terms['coss_hysteresis_W'] +
                                                             terms['gate_drive_if_counted_W'] +
                                                             terms['sensing_if_counted_W']) / gap_old)))


# ---------------------------------------------------------------------------------------------------------
# Supporting studies
# ---------------------------------------------------------------------------------------------------------
I_PORT_MAX = {1: 102.0, 2: 100.0}   # A: port-1 60 kW at 590 V; port-2 100 A like CRD Table 1 (UG p8)


def study_lm(n1, n2, L, lms=(150e-6, 300e-6, 600e-6, 1200e-6)):
    v1, v2, p = window_points((10e3, 20e3, 40e3, 60e3))
    out = []
    for lm in lms:
        D = design_magnetics(n1 / n2, L, lm, n1)
        r = evaluate(v1, v2, p, D)
        light = p <= 20e3
        b_dc = lm * a('xfmr_dc_resid') / (n1 * D['ae'])
        out.append(dict(lm=lm, J=float(np.mean(r['ploss'])), zvs_light_sps=float(np.mean((r['zvs1'] & r['zvs2'])[light])),
                        im_rms_800=float(r['Im'][(v1 == 800) & (v2 == 800)][0]), b_dc_resid=b_dc,
                        gap_mm=MU0 * n1 ** 2 * D['ae'] / lm * 1e3))
    return out


def study_dead_time(D):
    """DR-02: the same design with the dead time owned by firmware only, by the decided hardware guard (+ LUT), by
    the guard as drawn on DAB60 rev B, and fixed values (ST STSW-DABBIDIR 400 ns, CRD 200 ns)."""
    v1, v2, p = window_points((10e3, 20e3, 40e3, 60e3))
    out = {}
    cases = (('firmware LUT only (50-300 ns, no guard)', None, (0.0, 0.0)),
             ('LUT + decided hardware guard', None, DESIGN.get('hw_dt') or a('t_dead_hw')),
             ('LUT + guard as drawn on DAB60 rev B (104-354 ns)', None, (104e-9, 354e-9)),
             ('fixed 200 ns (CRD firmware)', 200e-9, None),
             ('fixed 400 ns (ST STSW-DABBIDIR)', 400e-9, None))
    for name, td, hw in cases:
        r = best_modulation(v1, v2, p, D, grid=TPS_COARSE, tdead=td, hw=hw)
        e8 = evaluate([800.0, 800.0], [800.0, 800.0], [60e3, 30e3], D, tdead=td, hw=hw)
        out[name] = dict(J=float(np.mean(r['ploss'])), eta_mean=float(np.mean(r['eta'])),
                         zvs=float(np.mean(r['zvs1'] & r['zvs2'])), loss_800_60k_W=float(e8['ploss'][0]),
                         p_diode_800_60k_W=float(e8['pdio1'][0] + e8['pdio2'][0]),
                         loss_800_30k_W=float(e8['ploss'][1]))
    return out


def study_flux_walk(D, L_rloop_extra=0.0):
    """DC magnetising current from gate-timing asymmetry, with and without countermeasures."""
    r_sw = float(_rpos(DESIGN, 100.0, 60.0, False))
    r_loop = 2 * r_sw + D['rdc1'] + D['rdc_l'] + a('R_ac_bus') + L_rloop_extra
    out = []
    for skew in (1e-9, 5e-9, 10e-9, a('driver_skew')):
        vdc = 950.0 * 2 * skew * F                    # bridge DC voltage from a pulse-width error
        idc = vdc / r_loop
        out.append(dict(skew_ns=skew * 1e9, vdc=vdc, idc_open_loop=idc,
                        b_dc_open_loop=D['Lm'] * idc / (D['N1'] * D['ae']),
                        b_dc_ungapped=D['lm_ungapped'] * min(idc, 1.0) / (D['N1'] * D['ae'])))
    # blocking capacitor (TI TIDA-010054 Eq. 20 rule) and control residual
    cb_min = 100 / (4 * PI ** 2 * F ** 2 * D['L'])
    b_ctrl = D['Lm'] * a('xfmr_dc_resid') / (D['N1'] * D['ae'])
    b_ac = max(950.0, D['n'] * 900.0) / (4 * F * D['N1'] * D['ae'])
    return dict(r_loop=r_loop, cases=out, cb_min=cb_min, b_dc_ctrl=b_ctrl, b_ac_950=b_ac,
                bsat_100=dev.FERRITE['N95']['bsat_100C'])


def blocking_cap_bank(D, il_rms_max, il_pk):
    """Size the optional series blocking-capacitor bank (primary side) for comparison."""
    c = dev.CAPS['C4AQUEW5450A3BJ']
    n_cap = int(np.ceil(il_rms_max / (0.8 * c['irms'])))
    cb = n_cap * c['c']
    q_half = il_rms_max * 0.95 / (2 * F)             # charge per half period, near-flat-top current
    return dict(mpn='C4AQUEW5450A3BJ', n=n_cap, c=cb, dv_pp=q_half / cb, p_esr=il_rms_max ** 2 * c['esr'] / n_cap,
                cb_min_ti=100 / (4 * PI ** 2 * F ** 2 * D['L']),
                f_res=1 / (2 * PI * np.sqrt(D['L'] * cb)), volume_l=n_cap * 45 * 65 * 57.5e-6)


def cosmic_table():
    rows = []
    a_die = dev.COSMIC['die_area_cm2']
    for v in (800.0, 850.0, 900.0, 950.0, 1000.0):
        f4 = dev.cosmic_fit(v, 4, 2 * a_die)            # 4 switches, each blocking 50 % of the time
        f3 = dev.cosmic_fit(v, 3, 2 * a_die)
        rows.append(dict(v=v, fit_gen4_bridge_sea=float(f4), fit_gen3_bridge_sea=float(f3),
                         fit_gen4_bridge_2000m=float(f4 * a('alt_altitude_factor') ** 2)))
    return rows


def ssoa_current_limit(v, l_loop=None, mod=None):
    ig = np.linspace(0, 600, 6001)
    return float(np.interp(1200.0 - v, dv_os(ig, l_loop, mod, v), ig))


def derating_map(D, v1s, v2s, p_levels=np.arange(10e3, 60.01e3, 2.5e3), sign=1.0, **kw):
    """Largest power (<= 60 kW) meeting Tj, per-position RMS, SSOA and port-current limits, best modulation."""
    V1, V2 = np.meshgrid(v1s, v2s, indexing='ij')
    pmax = np.full(V1.shape, np.nan)
    alive = np.ones(V1.shape, bool)
    why = np.full(V1.shape, '', dtype=object)
    for pl in p_levels:
        r = best_modulation(V1.ravel(), V2.ravel(), sign * pl + 0 * V1.ravel(), D, grid=TPS_COARSE, **kw)
        ok_t = (np.maximum(r['tj1'], r['tj2']) <= a('Tj_max_design'))
        ok_x = ~(r['t_xfmr'] > a('T_xfmr_hs_max'))            # no limit while the construction is pending
        ok_i = current_ok(r, kw.get('mod'))
        ok_s = ssoa_ok(r, mod=kw.get('mod'))
        ok_p = (np.abs(r['I1']) <= I_PORT_MAX[1]) & (np.abs(r['I2']) <= I_PORT_MAX[2]) & np.isfinite(r['phi'])
        ok = (ok_t & ok_i & ok_s & ok_p & ok_x).reshape(V1.shape)
        reason = np.where(~ok_p, 'port current', np.where(~ok_s, 'SSOA', np.where(~ok_t, 'Tj',
                          np.where(~ok_x, 'magnetics hot spot', np.where(~ok_i, 'RMS', ''))))).reshape(V1.shape)
        why = np.where(alive & ~ok & (why == ''), reason, why)
        alive &= ok
        pmax = np.where(alive, pl, pmax)
    return V1, V2, pmax, why


# ---------------------------------------------------------------------------------------------------------
# ngspice switching-level cross-check
# ---------------------------------------------------------------------------------------------------------
# Channel model of the decks: current limit I_CH_MAX x gate ramp (0 -> 1 in TR_G), i.e. 10 A/ns channel di/dt like
# DS Fig. 25 at 100 A.  Calibrated in a 24 nH DPT: 600 V/100 A turn-off overshoot ~330 V vs ~225 V implied by DS
# Fig. 19, i.e. the decks are harsher than the datasheet (conservative for snubber stress and die overshoot).
I_CH_MAX, TR_G = 250.0, 25e-9
T_D0 = 50e-9          # all gate edges delayed: no commutation at t = 0 while the charge-defined C_oss settle


def coss_fit(mod):
    """Closed-form C_oss(V) of one switch position for SPICE: c0 + c1/(1+v/v0)^m (incumbent: the audited fit)."""
    if mod['kind'] == 'gm4':
        c0, c1, v0, m = dev.COSS_FIT
        return c0 * mod['npar'], c1 * mod['npar'], v0, m
    if 'coss_fit' not in mod:
        from scipy.optimize import curve_fit
        v = np.concatenate([np.linspace(0, 20, 81), np.linspace(25, 1000, 196)])
        qf = _qe(mod)[0]
        c = np.gradient(qf(v), v) * 1e9                       # nF: keeps the optimiser's tolerances meaningful
        f = lambda x, c0, c1, v0, m: c0 + c1 / (1 + x / v0) ** m
        p_, _ = curve_fit(f, v, c, p0=(c[-1], c[0], 5.0, 0.8), bounds=([0, 0, 0.5, 0.3], [10, 1e3, 200, 1.5]),
                          sigma=np.maximum(c, 0.05), maxfev=20000)
        mod['coss_fit'] = (p_[0] * 1e-9, p_[1] * 1e-9, float(p_[2]), float(p_[3]))
        q_fit = p_[0] * 800 + p_[1] * p_[2] / (1 - p_[3]) * ((1 + 800 / p_[2]) ** (1 - p_[3]) - 1)
        mod['coss_fit_q800_err'] = float(q_fit * 1e-9 / qf(800.0) - 1)
    return mod['coss_fit']


def spice_deck(name, v1, v2, D, phi, tdead, r_on, periods=24, step=1e-9, snub=None, hf='default', model='ideal',
               mod=None):
    """Write sim/spice/dab_<name>.cir: SPS DAB, 8 switch subcircuits (S-switch + body diode + nonlinear C_oss
    charge fit of Fig. 9), per bridge: film bank -> L_bus 7.2 nH -> module terminals (RC snubbers + HF ceramics)
    -> L_mod 16.8 nH -> dies (DS Fig. 19 split of the 24 nH loop); ideal transformer (E/F) with Lm after the lumped
    series inductance; stiff DC sources behind the banks.  snub = (n, R, C) per bridge, hf = (n, C, ESR, ESL) or None."""
    mod = DESIGN if mod is None else mod
    c0, c1, v0, m = coss_fit(mod)
    l_mod = a('L_mod') if mod['kind'] == 'gm4' else mod['l_loop']      # terminals (HF caps) -> dies
    bank = {1: dev.CAPS[CAP1[0]], 2: dev.CAPS[CAP2[0]]}
    T = 1 / F
    tph = (phi / (2 * PI)) * T % T
    o = dict(v1=v1, v2=v2, n=D['n'], L=D['L'], Lm=D['Lm'], a1=0.0, a2=0.0, phi=phi)
    il0, im0 = currents(o, 0.0)
    pw = T / 2 - tdead - 1e-9
    sn = a('snub') if snub is None else snub
    hf = a('hf_caps') if hf == 'default' else hf
    hf = None if model == 'ideal' else hf
    qexpr = (f"{c0:.5g}*V(d,s) + {c1 * v0 / (1 - m):.6g}*(pwr(1+max(V(d,s),0)/{v0:.6g},{1 - m:.6g})-1)"
             f" + {c1:.5g}*min(V(d,s),0)")
    if model == 'ideal':        # power-flow / ZVS cross-check: ideal switch, nonlinear C_oss, 24 nH at the dies
        sw = ['S1 d s g 0 swm', 'D1 s d dbody', f"Cq d s Q='{qexpr}'"]
        mdl = [f'.model swm sw(vt=0.5 vh=0.1 ron={r_on:.5g} roff=1e8)']
        tr_g, t_d0 = 1e-9, 0.0
    else:                       # snubber/overshoot study: finite channel di/dt, charge-equivalent linear C_oss
        sw = [f"Bch d s I='({I_CH_MAX:g}*V(g)+1e-6)*tanh(V(d,s)/({r_on:.5g}*({I_CH_MAX:g}*V(g)+1e-6))) + V(d,s)*1e-8'",
              'D1 s d dbody', 'Cq d s {cq}']
        mdl = []
        tr_g, t_d0 = TR_G, T_D0

    def port(k, gnd):
        if model == 'ideal':    # validated topology: whole loop before the die node, terminal parts at the dies
            return [f'Lb{k} k{k} bt{k} {a("L_loop"):.4g}', f'Vtie{k} bt{k} bp{k} 0',
                    f'Rsn{k} bt{k} sn{k} {sn[1] / sn[0]:.6g}', f'Csn{k} sn{k} {gnd} {sn[0] * sn[2]:.6g}']
        q = [f'Lb{k} k{k} bt{k} {a("L_bus"):.4g}', f'Lm{k} bt{k} bp{k} {l_mod:.4g}',
             f'Rsn{k} bt{k} sn{k} {sn[1] / sn[0]:.6g}', f'Csn{k} sn{k} {gnd} {sn[0] * sn[2]:.6g}']
        if hf:
            q += [f'Lhf{k} bt{k} hf{k} {hf[3] / hf[0]:.4g}', f'Rhf{k} hf{k} hc{k} {hf[2] / hf[0]:.4g}',
                  f'Chf{k} hc{k} {gnd} {hf[0] * hf[1]:.6g}']
        return q

    lines = [
        f'* DAB-D60 switching-level SPS deck "{name}" - generated by sim/dab_design.py, do not edit',
        f'* V1={v1:.0f} V V2={v2:.0f} V n={D["n"]:.4f} L={D["L"]*1e6:.3f} uH Lm={D["Lm"]*1e6:.0f} uH '
        f'phi={np.degrees(phi):.3f} deg tdead={tdead*1e9:.0f} ns Ron={r_on*1e3:.2f} mohm',
        f'* snubber per bridge {sn[0]} x ({sn[1]} ohm + {sn[2]*1e9:.2f} nF); HF ceramics '
        + (f'{hf[0]} x {hf[1]*1e9:.0f} nF' if hf else 'none'),
        '.subckt sic d s g cq=1n', *sw, '.ends sic', *mdl,
        '.model dbody d(is=1e-9 n=5 rs=0.025)',
        f'Vp1 p1 0 DC {v1:.6g}', 'Vs1 p1 p1a 0', 'Rs1 p1a k1 1m',
        *([f'Ck1 k1 0 135u'] if model == 'ideal' else
          [f'Rk1 k1 kr1 {bank[1]["esr"] / CAP1[1]:.4g}', f'Lk1 kr1 kc1 {bank[1]["esl"] / CAP1[1]:.4g}',
           f'Ck1 kc1 0 {CAP1[1] * bank[1]["c"]:.4g}']), *port(1, '0'),
        *[f'XS{j} {dd} {ss} {gg} sic cq={_qe(mod)[0](v1) / v1:.5g}' for j, dd, ss, gg in
          ((1, 'bp1', 'na', 'g1'), (2, 'na', '0', 'g2'), (3, 'bp1', 'nb', 'g2'), (4, 'nb', '0', 'g1'))],
        'Vil na na1 0', f'Lk na1 nx {D["L"]:.6g} IC={il0:.6g}', f'Lmag nx nb {D["Lm"]:.6g} IC={im0:.6g}',
        f'Etr nx nxb nc nd {D["n"]:.6g}', 'Vtr nxb nb 0', f'Ftr nd nc Vtr {D["n"]:.6g}',
        'Rg s0 0 1meg', f'Vp2 p2 s0 DC {v2:.6g}', 'Vs2 k2 p2a 0', 'Rs2 p2a p2 1m',
        *([f'Ck2 k2 s0 195u'] if model == 'ideal' else
          [f'Rk2 k2 kr2 {bank[2]["esr"] / CAP2[1]:.4g}', f'Lk2 kr2 kc2 {bank[2]["esl"] / CAP2[1]:.4g}',
           f'Ck2 kc2 s0 {CAP2[1] * bank[2]["c"]:.4g}']),
        *port(2, 's0'),
        *[f'XS{j} {dd} {ss} {gg} sic cq={_qe(mod)[0](v2) / v2:.5g}' for j, dd, ss, gg in
          ((5, 'bp2', 'nc', 'g5'), (6, 'nc', 's0', 'g6'), (7, 'bp2', 'nd', 'g6'), (8, 'nd', 's0', 'g5'))],
        f'Vg1 g1 0 PULSE(0 1 {t_d0 + tdead:.6g} {tr_g:g} {tr_g:g} {pw - tr_g:.6g} {T:.6g})',
        f'Vg2 g2 0 PULSE(1 0 {t_d0:.6g} {tr_g:g} {tr_g:g} {T / 2 + tdead - tr_g:.6g} {T:.6g})',
        f'Vg5 g5 0 PULSE(0 1 {t_d0 + tph + tdead:.6g} {tr_g:g} {tr_g:g} {pw - tr_g:.6g} {T:.6g})',
        f'Vg6 g6 0 PULSE(1 0 {t_d0 + tph:.6g} {tr_g:g} {tr_g:g} {T / 2 + tdead - tr_g:.6g} {T:.6g})',
        f'.ic V(k1)={v1:.6g} V(bt1)={v1:.6g} V(bp1)={v1:.6g} V(sn1)={v1:.6g} V(p1a)={v1:.6g} V(na)=0 '
        f'V(nb)={v1:.6g} V(k2)={v2:.6g} V(bt2)={v2:.6g} V(bp2)={v2:.6g} V(sn2)={v2:.6g} V(p2a)={v2:.6g} '
        f'V(nc)=0 V(nd)={v2:.6g} V(nx)={v1 - D["n"] * v2:.6g}'
        + (f' V(hf1)={v1:.6g} V(hc1)={v1:.6g} V(hf2)={v2:.6g} V(hc2)={v2:.6g}' if hf else '')
        + ('' if model == 'ideal' else f' V(kr1)={v1:.6g} V(kc1)={v1:.6g} V(kr2)={v2:.6g} V(kc2)={v2:.6g}'),
        '.options method=gear reltol=1e-3 abstol=1e-6 vntol=1e-4 chgtol=1e-15 itl4=100',
        f'.tran {step:.3g} {periods * T:.6g} {(periods - 5) * T:.6g} {step:.3g} uic',
        '.control', 'run',
        f'wrdata {os.path.join(OUT, "spice_" + name + ".txt")} i(Vil) V(na,nb) V(nc,nd) '
        'V(bp1,na) V(na) V(bp2,nc) V(nc) i(Vs1) i(Vs2) V(g1) V(g5) V(bp1) V(bt1,sn1) V(bt2,sn2) V(bp2,s0) V(bt1) '
        'V(bt2,s0)' + (' V(hf1,hc1) V(hf2,hc2)' if hf else ''),
        'quit', '.endc', '.end']
    path = os.path.join(SPICE_DIR, f'dab_{name}.cir')
    with open(path, 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
    return path


def run_spice(path):
    res = subprocess.run(['ngspice', '-b', path], capture_output=True, text=True, timeout=900)
    if res.returncode != 0 or 'aborted' in res.stdout or 'aborted' in res.stderr:
        raise RuntimeError(f'ngspice failed on {path}:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}')
    return res


def spice_compare(name, v1, v2, p, D, tdead, tj=80.0, snub=None, hf='default', keep_csv=True, model='ideal'):
    """Run one deck and compare power, inductor current and switch voltage at turn-on with the analytic model."""
    phi = float(phi_sps(v1, v2, D['n'], D['L'], p))
    r_on = float(_rpos(DESIGN, tj, 60.0, False))
    path = spice_deck(name, v1, v2, D, phi, tdead, r_on, snub=snub, hf=hf, model=model)
    td0 = 0.0 if model == 'ideal' else T_D0
    try:
        run_spice(path)
    except RuntimeError:          # 'timestep too small' at a diode edge: retry once with a different step grid
        path = spice_deck(name, v1, v2, D, phi, tdead, r_on, snub=snub, hf=hf, model=model, step=0.7e-9)
        run_spice(path)
    dat = np.loadtxt(os.path.join(OUT, f'spice_{name}.txt'))
    t = dat[:, 0]
    col = lambda k: dat[:, 2 * k + 1]
    cols = [col(k) for k in range(dat.shape[1] // 2)]
    (il, vp, vs, vds1, vds2, vds5, vds6, i1, i2, g1, g5, vdie1, vsn1, vsn2, vdie2, vterm1, vterm2) = cols[:17]
    T = 1 / F
    m = t >= t[-1] - 4 * T                                     # last 4 periods
    tt = t[m]
    avg = lambda y: np.trapezoid(y[m], tt) / (tt[-1] - tt[0])
    p1, p2 = v1 * avg(i1), v2 * avg(i2)
    il_rms = np.sqrt(avg(il ** 2))
    il_pk = np.max(np.abs(il[m]))
    # switch voltage at the gate turn-on instants of the last period (S1 primary, S5 secondary)
    t_on1 = t[-1] - 4 * T + np.arange(4) * T + tdead + td0
    tph = (phi / (2 * PI)) * T % T
    t_on5 = t[-1] - 4 * T + np.arange(4) * T + tph + tdead + td0
    v_on1 = np.max(np.interp(t_on1, t, vds1))
    v_on5 = np.max(np.interp(t_on5, t, vds5))
    vpk1 = np.max(vdie1[m])                                     # bridge-1 die-level DC peak (bus + overshoot)
    sn = a('snub') if snub is None else snub
    p_r = [float(np.trapezoid(x[m] ** 2, t[m]) / (t[m][-1] - t[m][0]) / (sn[1] / sn[0]) / sn[0]) for x in (vsn1, vsn2)]
    vpk = [float(np.max(vdie1[m])), float(np.max(vdie2[m]))]
    vterm = [float(np.max(vterm1[m]) - np.min(vterm1[m])), float(np.max(vterm2[m]) - np.min(vterm2[m]))]
    hf_ = (a('hf_caps') if hf == 'default' else hf) if model != 'ideal' else None
    hfc = None
    if hf_ and len(cols) >= 19:                                 # HF capacitors: current per part from the ESR drop
        i_hf = [x / (hf_[2] / hf_[0]) for x in cols[17:19]]
        hfc = dict(i_rms_per_cap=[float(np.sqrt(np.trapezoid(x[m] ** 2, t[m]) / (t[m][-1] - t[m][0]))) / hf_[0]
                                  for x in i_hf],
                   i_pk_per_cap=[float(np.max(np.abs(x[m]))) / hf_[0] for x in i_hf])
        hfc['p_per_cap_W'] = [x ** 2 * hf_[2] for x in hfc['i_rms_per_cap']]
    m1 = t >= t[-1] - T                                         # keep one period, 10 ns grid, delete the raw dump
    tg = np.arange(t[m1][0], t[-1], 10e-9)
    np.savetxt(os.path.join(OUT, f'spice_{name}_1period.csv'),
               np.column_stack([tg - tg[0], np.interp(tg, t, il), np.interp(tg, t, vp), np.interp(tg, t, vs),
                                np.interp(tg, t, vds1), np.interp(tg, t, vds5)]),
               delimiter=',', header='t_s,i_L_A,v_p_V,v_s_V,vds_S1_V,vds_S5_V', comments='', fmt='%.6g')
    os.remove(os.path.join(OUT, f'spice_{name}.txt'))
    if not keep_csv:
        os.remove(os.path.join(OUT, f'spice_{name}_1period.csv'))
    # analytic prediction including the dead-time edge delay: a bridge whose swing does not reach half voltage
    # within the dead time switches at the END of the dead time, otherwise at the half-swing time
    phi_eff, d_a, d_c = phi, 0.0, 0.0
    for _ in range(8):
        o = dict(v1=v1, v2=v2, n=D['n'], L=D['L'], Lm=D['Lm'], a1=0.0, a2=0.0, phi=phi_eff)
        ev = events(o)
        vres, delay = {}, {}
        for k, th_off in (('A', -W * d_a), ('C', phi - W * d_a)):      # turn-off instants, effective frame
            e = ev[k]
            il_, im_ = currents(o, th_off)
            iz = -il_ if k == 'A' else il_ - im_
            qf_, _, cn_ = _qe(DESIGN)
            _, vr, _ = zvs_transition(iz, e['vb'], e['nref'], e['vl0'], e['eps'], e['kappa'], D['L'], tdead,
                                      qf=qf_, c_node=cn_)
            th_ = float(zvs_transition.t_half)
            vres[k] = float(vr)
            delay[k] = th_ if np.isfinite(th_) else tdead
        d_a, d_c = delay['A'], delay['C']
        phi_eff = phi + W * (d_c - d_a)
    s = stats(o)
    th = np.linspace(0, 2 * PI, 721)
    il_an, _ = currents(o, th)
    return dict(name=name, v1=v1, v2=v2, p_target=p, phi_deg=np.degrees(phi), phi_eff_deg=np.degrees(phi_eff),
                tdead_ns=tdead * 1e9,
                p_analytic=float(s['P']), p1_spice=p1, p2_spice=p2, il_rms_an=float(s['IL']), il_rms_sp=il_rms,
                il_pk_an=float(s['ILpk']), il_pk_sp=il_pk, vres_pri_an=vres['A'], vres_pri_sp=v_on1,
                vres_sec_an=vres['C'], vres_sec_sp=v_on5, v_bus1_pk=vpk1, p_snub_res=p_r, v_die_pk=vpk,
                v_term_pp=vterm, hf=hfc,
                wave=dict(t=(tt - tt[0] - td0) * 1e6, il=il[m], vp=vp[m], vs=vs[m], th=th / (2 * PI) * T * 1e6,
                          il_an=il_an))


# ---------------------------------------------------------------------------------------------------------
# Main: design, maps, specifications, outputs
# ---------------------------------------------------------------------------------------------------------
MAP_V1 = np.array([600.0, 700.0, 800.0, 900.0, 950.0])
MAP_V2 = np.arange(400.0, 900.1, 50.0)
MAP_P = np.array([2.5, 5, 7.5, 10, 15, 20, 30, 40, 50, 60]) * 1e3
TPS_MAP = np.radians([0, 10, 20, 30, 40, 50, 60, 75, 90])


def _csv(path, rows):
    with open(path, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (f'{v:.6g}' if isinstance(v, (float, np.floating)) else v) for k, v in r.items()})


def efficiency_map(D):
    V1, V2, P, S = np.meshgrid(MAP_V1, MAP_V2, MAP_P, np.array([1.0, -1.0]), indexing='ij')
    v1, v2, p = V1.ravel(), V2.ravel(), (P * S).ravel()
    out = {}
    chunk = 250
    for i in range(0, v1.size, chunk):
        r = best_modulation(v1[i:i + chunk], v2[i:i + chunk], p[i:i + chunk], D, grid=TPS_MAP)
        for k, v in r.items():
            out.setdefault(k, []).append(np.atleast_1d(v))
    best = {k: np.concatenate(v) for k, v in out.items()}
    sps = evaluate(v1, v2, p, D)
    ok = (np.maximum(best['tj1'], best['tj2']) <= a('Tj_max_design')) & ssoa_ok(best) & \
        current_ok(best) & np.isfinite(best['phi']) & \
        (np.abs(best['I1']) <= I_PORT_MAX[1]) & (np.abs(best['I2']) <= I_PORT_MAX[2])
    return best, sps, ok


def zvs_sps_grid(D, v1, sign=1.0):
    v2 = np.linspace(400, 900, 51)
    p = np.linspace(1e3, 60e3, 60)
    V2, P = np.meshgrid(v2, p, indexing='ij')
    r = evaluate(v1 + 0 * V2, V2, sign * P, D, iters=1)
    return V2, P, r


def main():
    global DESIGN, OPTIONS
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(SPICE_DIR, exist_ok=True)
    R = {}
    # 0. device option study (SRC-1..4) on the frozen magnetics, then the whole design with the chosen option
    D0 = design_magnetics(FROZEN['N1'] / FROZEN['N2'], FROZEN['L'], FROZEN['Lm'], FROZEN['N1'])
    calibrate_magnetics(D0)                         # independent magnetics figures (MG-01/03/05) until the design files
    OPTIONS = build_options()
    rows, R['opt_eval'] = device_options(D0)
    _csv(os.path.join(OUT, 'device_options.csv'), [{k: (v if not isinstance(v, list) else ' / '.join(map(str, v)))
                                                     for k, v in x.items()} for x in rows])
    pick, cand = choose_option(rows)
    R['options'], R['pick'], R['cand'] = rows, pick, cand
    DESIGN = [o for o in OPTIONS if o['short'] == pick['option']][0]
    A['t_dead_hw'] = (DESIGN['hw_dt'], 's (min, max) gate-level window of the hardware shoot-through guard of the '
                                       'chosen devices and driver (DR-02 decision, dead_time_window())')
    # 1. selection of n and L (continuous sweep with best modulation), then integer turns
    sweep = sweep_n_L_tps()
    _csv(os.path.join(OUT, 'selection_n_L.csv'), [{k: v for k, v in s.items() if k != 'bad'} for s in sweep])
    best_cont = min(sweep, key=lambda s: (s['n_bad'], s['J']))
    turns = select_turns()
    _csv(os.path.join(OUT, 'selection_turns.csv'), turns)
    tsel = min(turns, key=lambda s: (s['n_bad'], s['J']))
    # the design is frozen by DECISIONS.md D-014; the sweep only re-checks that it is still near the optimum
    N1, N2, L = FROZEN['N1'], FROZEN['N2'], FROZEN['L']
    fz = [t for t in turns if t['N1'] == N1 and t['N2'] == N2 and abs(t['L'] - L) < 1e-9][0]
    R['frozen'] = dict(J=fz['J'], n_bad=fz['n_bad'], J_opt=tsel['J'], opt=(tsel['N1'], tsel['N2'], tsel['L']))
    # 2. magnetising inductance and dead time
    lms = study_lm(N1, N2, L)
    _csv(os.path.join(OUT, 'study_lm.csv'), lms)
    v1w, v2w, pw = window_points((10e3, 20e3, 40e3, 60e3))
    jlm = {}
    for lm in (150e-6, 300e-6):
        Dt = design_magnetics(N1 / N2, L, lm, N1)
        jlm[lm] = float(np.mean([best_modulation(v1w, v2w, sg * pw, Dt, grid=TPS_COARSE)['ploss']
                                 for sg in (1, -1)]))
    # tie (< 1 % loss difference) -> the lower Lm halves the DC flux from a given residual DC current
    R['lm_rule_choice'] = 150e-6 if jlm[150e-6] <= 1.01 * jlm[300e-6] else 300e-6
    D = design_magnetics(N1 / N2, L, FROZEN['Lm'], N1)
    if MAGD is not None:                            # leakage / external split of the magnetics design
        D['Llk'], D['Lext'] = MAGD['xfmr']['electrical']['L_leak_H'], MAGD['ind']['electrical']['L_H']
    dts = study_dead_time(D)
    R.update(best_cont=best_cont, tsel=tsel, lm_study=lms, jlm=jlm, D=D, dead_time=dts)

    # 3. maps
    best, sps, ok = efficiency_map(D)
    rows = []
    for i in range(best['v1'].size):
        rows.append(dict(V1=best['v1'][i], V2=best['v2'][i], P_out=best['p'][i], feasible=int(ok[i]),
                         a1_deg=np.degrees(best['a1'][i]), a2_deg=np.degrees(best['a2'][i]),
                         phi_deg=np.degrees(best['phi'][i]), eta=best['eta'][i], P_loss=best['ploss'][i],
                         IL_rms=best['IL'][i], IL_pk=best['ILpk'][i], Is_rms=best['Is'][i],
                         I_pos1_rms=best['ipos1'][i], I_pos2_rms=best['ipos2'][i], I_off1=best['ioff1'][i],
                         I_off2=best['ioff2'][i], zvs1=int(best['zvs1'][i]), zvs2=int(best['zvs2'][i]),
                         Tj1=best['tj1'][i], Tj2=best['tj2'][i], P_cond=best['pc1'][i] + best['pc2'][i],
                         P_sw=best['psw1'][i] + best['psw2'][i], P_xfmr=best['p_xfmr'][i],
                         P_ind=best['p_ind'][i], P_cap=best['p_cap'][i], P_bus=best['p_bus'][i],
                         IC1=best['IC1'][i], IC2=best['IC2'][i], B_pk=best['b_pk'][i],
                         eta_SPS=sps['eta'][i], zvs1_SPS=int(sps['zvs1'][i]), zvs2_SPS=int(sps['zvs2'][i])))
    _csv(os.path.join(OUT, 'efficiency_map.csv'), rows)
    R['map'] = dict(best=best, sps=sps, ok=ok)
    # derating (forward and reverse)
    v1d = np.array([590.0, 600, 650, 700, 750, 800, 850, 900, 950])
    v2d = MAP_V2
    der = {}
    for sg, name in ((1.0, 'charge'), (-1.0, 'discharge')):
        V1g, V2g, pmax, why = derating_map(D, v1d, v2d, sign=sg)
        der[name] = (V1g, V2g, pmax, why)
    drows = []
    for name, (V1g, V2g, pmax, why) in der.items():
        for i in range(V1g.shape[0]):
            for j in range(V1g.shape[1]):
                drows.append(dict(direction=name, V1=V1g[i, j], V2=V2g[i, j], P_max=pmax[i, j],
                                  limited_by=why[i, j] or ('-' if pmax[i, j] >= 60e3 else 'below 10 kW')))
    _csv(os.path.join(OUT, 'derating_map.csv'), drows)
    R['derating'] = der
    # derating sensitivity: incumbent - 18 nH effective loop (CRD Fig. 21 suggests ~1.5 V/A at 100 A); discretes -
    # the commutation loop one third LARGER than assumed (the layout is not designed yet)
    if DESIGN['kind'] == 'gm4':
        A['L_loop'] = (18e-9, A['L_loop'][1])
        der18 = derating_map(D, v1d, v2d, sign=1.0)
        A['L_loop'] = (24e-9, A['L_loop'][1])
    else:
        mods = [DESIGN] + DESIGN.get('members', [])
        l0 = DESIGN['l_loop']
        for m_ in mods:
            m_['l_loop'] = 4 / 3 * l0
        der18 = derating_map(D, v1d, v2d, sign=1.0)
        for m_ in mods:
            m_['l_loop'] = l0
    R['derating18'] = der18
    stress(R, D)
    specs(R, D)
    verdict_950(R, D)
    R['crd'] = crd_calibration()
    sensitivities(R, D)
    pessimistic(R, D, v1d, v2d)
    R['levers'] = design_levers(D)
    # HF capacitors (DR-07): smallest count of KC-LINK parts whose simulated current per part at the highest turn-off
    # current point meets the rule (one deck per candidate), then the full terminal-network study with that count
    pts = snub_points(D, R)
    _, v1x, v2x, px = [q for q in pts if q[0] == 'ioffmax'][0]
    for nhf in range(8, 65, 4):
        A['hf_caps'] = ((nhf, dev.HF_CAP['c'], dev.HF_CAP['esr'], dev.HF_CAP['esl']), A['hf_caps'][1])
        q = spice_compare(f'hfsize_{nhf}', v1x, v2x, px, D, 150e-9, hf='default', keep_csv=False, model='snub')
        os.remove(os.path.join(SPICE_DIR, f'dab_hfsize_{nhf}.cir'))
        if max(q['hf']['i_rms_per_cap']) <= dev.HF_CAP['i_rms_rule']:
            break
    R['snub'] = snubber_study(D, R)
    hfr = [q for q in R['snub'] if q['cfg'] == 'rc_hf']
    R['hf'] = dict(n=nhf, mpn=dev.HF_CAP['mpn'], I_rms_per_cap_max_A=max(q['I_hf_rms_per_cap_A'] for q in hfr),
                   I_pk_per_cap_max_A=max(q['I_hf_pk_per_cap_A'] for q in hfr),
                   P_per_cap_max_W=max(q['P_hf_per_cap_W'] for q in hfr), I_rms_rule_A=dev.HF_CAP['i_rms_rule'],
                   rule='<= 70 %% of the typical 13 A rms (1 MHz, heatsinked) of the KC-LINK curve (%s); ESR %.0f '
                        'mOhm / ESL %.1f nH per part ASSUMED' % (dev.HF_CAP['src'], dev.HF_CAP['esr'] * 1e3,
                                                                 dev.HF_CAP['esl'] * 1e9),
                   v_rating=dev.HF_CAP['v'], src=dev.HF_CAP['src'])
    _csv(os.path.join(OUT, 'snubber_study.csv'), R['snub'])
    _csv(os.path.join(OUT, 'design_levers.csv'), [{k: v for k, v in q.items()} for q in R['levers']])
    R['spice'] = spice_points(D)
    plots(R, D)
    spec = handoff(R, D)
    report(R, D, spec)
    selfcheck(R, D)
    return R, D


FROZEN = dict(N1=11, N2=12, L=6.5e-6, Lm=150e-6, src='docs/requirements/DECISIONS.md D-014')


def window_grid(powers):
    v1, v2, p = np.meshgrid(WIN_V1, np.array([700.0, 750, 800, 850, 900]), np.asarray(powers, float), indexing='ij')
    return v1.ravel(), v2.ravel(), p.ravel()


def pessimistic(R, D, v1d, v2d):
    """Calibration-gap case: the fitted unexplained CRD loss k0 + k1*P added at every point, all of it either in
    the two modules or in the transformer."""
    k0, k1 = R['sens']['crd_gap_fit']
    V1b, V2b, pb, _ = R['derating']['charge']
    base_full = [(V1b[i, 0], V2b[0, j]) for i in range(V1b.shape[0]) for j in range(V2b.shape[1])
                 if pb[i, j] >= 60e3 and 600 <= V1b[i, 0] <= 900 and V2b[0, j] >= 700]
    fv1 = np.array([q[0] for q in base_full])
    fv2 = np.array([q[1] for q in base_full])
    out = {'k0_W': k0, 'k1_W_per_kW': k1 * 1e3, 'base_full_power_points': len(base_full)}
    for alloc in ('modules', 'magnetics'):
        ex = (k0, k1, alloc)
        der = derating_map(D, v1d, v2d, sign=1.0, extra=ex)
        V1g, V2g, pmax, why = der
        r60 = best_modulation(fv1, fv2, 60e3 + 0 * fv1, D, grid=TPS_COARSE, extra=ex)
        ok60 = (np.maximum(r60['tj1'], r60['tj2']) <= a('Tj_max_design')) & ~(r60['t_xfmr'] > a('T_xfmr_hs_max'))
        e8 = evaluate([800.0, 800.0], [800.0, 800.0], [30e3, 60e3], D, extra=ex)
        q_max = float(np.max(r60['q_cool']))
        res = dict(Tj_max_C=float(np.max(np.maximum(r60['tj1'], r60['tj2']))),
                   Tj_max_at=[float(fv1[np.argmax(np.maximum(r60['tj1'], r60['tj2']))]),
                              float(fv2[np.argmax(np.maximum(r60['tj1'], r60['tj2']))])],
                   xfmr_hot_spot_max_C=float(np.max(r60['t_xfmr'])),
                   full_power_points_surviving=[[float(a_), float(b_)] for a_, b_, o_ in zip(fv1, fv2, ok60) if o_],
                   full_power_points_lost=[[float(a_), float(b_)] for a_, b_, o_ in zip(fv1, fv2, ok60) if not o_],
                   eta_800_800={'30kW': float(e8['eta'][0]), '60kW': float(e8['eta'][1])},
                   coolant_heat_W_max_60kW=q_max,
                   flow_for_5K_lpm=q_max / (a('coolant_rho_cp') * 5.0) * 60e3)
        # fixes
        if alloc == 'modules':
            fix_t = None
            for t_in in np.arange(50.0, 9.9, -2.5):
                rr = best_modulation(fv1, fv2, 60e3 + 0 * fv1, D, grid=TPS_COARSE, extra=ex, t_in=t_in)
                if np.all(np.maximum(rr['tj1'], rr['tj2']) <= a('Tj_max_design')):
                    fix_t = float(t_in)
                    break
            up = {'D2W': 'D3W', 'GM4': None}.get(DESIGN.get('short'))
            upm = next((o_ for o_ in OPTIONS if o_.get('short') == up), None) if up else None
            upm = MOD_GM4x2 if DESIGN['kind'] == 'gm4' else upm
            ninl = (f'coolant inlet would have to be <= {fix_t:.0f} C' if fix_t is not None else
                    'no coolant inlet >= 10 C is enough')
            if upm is not None:
                r2x = best_modulation(fv1, fv2, 60e3 + 0 * fv1, D, grid=TPS_COARSE, extra=ex, mod=upm)
                upt = f'{upm["name"]} (Tj max {np.max(np.maximum(r2x["tj1"], r2x["tj2"])):.0f} C at full power) or '
            else:
                upt = ''
            res['fix'] = (f'{ninl} (impractical: below dew point in most sites); practical fixes: {upt}'
                          f'derate to the pessimistic map (60 kW kept at {int(np.sum(ok60))} of {len(fv1)} '
                          f'base full-power window points)')
            res['coolant_inlet_for_full_power_C'] = fix_t
        else:
            rth_need = (a('T_xfmr_hs_max') - r60['t_cool_x']) / np.maximum(r60['p_xfmr'] + r60['p_extra'], 1e-9)
            res['Rth_xfmr_needed_K_W'] = float(np.min(rth_need))
            res['fix'] = (f'transformer hot-spot-to-coolant Rth <= {np.min(rth_need):.3f} K/W would be needed '
                          f'(MG-02: 0.71 K/W is the best case of the reference pot core) - not credible: if the gap is in the '
                          f'magnetics the transformer must be redesigned for lower loss; otherwise derate per the '
                          f'pessimistic map')
        res['derating_charge_W'] = {f'V1={int(V1g[i, 0])}': {f'V2={int(V2g[0, j])}':
                                    (None if np.isnan(pmax[i, j]) else float(pmax[i, j]))
                                    for j in range(V2g.shape[1])} for i in range(V1g.shape[0])}
        res['_map'] = der
        out[alloc] = res
    R['pess'] = out


def design_levers(D):
    """What it would take to exceed 98.8 % above 20 kW over the whole full-power window (model)."""
    v1, v2, p = window_grid((20e3, 30e3, 40e3, 50e3, 60e3))
    cases = [('baseline: 1 x CBB011M12GM4T per bridge (DAB-02)', MOD_GM4, {}, 2),
             ('gate drive +18 V (same modules)', MOD_GM4, {'vgs18': True}, 2),
             ('coolant inlet 25 C instead of 50 C', MOD_GM4, {'t_in': 25.0}, 2),
             ('magnetics copper loss -50 % (larger transformer/inductor)', MOD_GM4, {'k_cu': 0.5}, 2),
             ('2 x CBB011M12GM4T in parallel per bridge', MOD_GM4x2, {}, 4),
             ('2 x CAB011M12FM3 half bridges per bridge', MOD_FM3, {}, 4),
             ('2 x GM4 per bridge + 18 V + Cu -50 % + 25 C inlet', MOD_GM4x2, {'vgs18': True, 'k_cu': 0.5,
                                                                              't_in': 25.0}, 4)]
    rows = []
    for name, mod, kw, nmod in cases:
        r = best_modulation(v1, v2, p, D, grid=TPS_COARSE, mod=mod, **kw)
        ok = (np.maximum(r['tj1'], r['tj2']) <= a('Tj_max_design')) & ssoa_ok(r, mod=mod) & np.isfinite(r['eta'])
        e8 = best_modulation([800.0], [800.0], [60e3], D, grid=TPS_COARSE, mod=mod, **kw)
        i = int(np.argmin(np.where(np.isfinite(r['eta']), r['eta'], 1.0)))
        rows.append(dict(lever=name, modules_total=nmod, eta_800_800_60kW=float(e8['eta'][0]),
                         loss_800_800_60kW_W=float(e8['ploss'][0]), eta_worst_window=float(r['eta'][i]),
                         worst_point=f'{v1[i]:.0f}/{v2[i]:.0f} V {p[i]/1e3:.0f} kW',
                         frac_window_ge_98p8=float(np.mean(r['eta'] >= 0.988)),
                         Tj_max_C=float(np.max(np.maximum(r['tj1'], r['tj2']))), all_ok=bool(ok.all())))
    rows.append(dict(lever='CAB016M12FM3 half bridges (16 mOhm)', modules_total=4, eta_800_800_60kW=float('nan'),
                     loss_800_800_60kW_W=float('nan'), eta_worst_window=float('nan'), worst_point='-',
                     frac_window_ge_98p8=float('nan'), Tj_max_C=float('nan'), all_ok=False))
    return rows


# ---------------------------------------------------------------------------------------------------------
# Device option study (REQUIREMENTS section 7, SRC-1..4).  Decision rule given by the coordinator (2026-10-04):
# the cheapest option that meets the junction-temperature and current rules with a primary AND an alternate Asian
# maker and whose efficiency is not worse than today's; designed for the worse of the two makers.
# ---------------------------------------------------------------------------------------------------------
E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5,
       8.2, 9.1]


def gate_r_off(parts, npar, drv, rail):
    """Smallest E24 R_G,off per device that keeps the driver sink current <= its rating at the highest rail
    (bias +1.3 %), the lowest internal gate resistance of the makers and a -1 % resistor."""
    v = (rail[0] - rail[1]) * 1.013
    rgi = min(p_['rg_int'] for p_ in parts)
    for r in E24 + [x * 10 for x in E24]:
        if v / (drv['rol'] + (0.99 * r + rgi) / npar) <= drv['i_pk']:
            return r
    raise ValueError('no gate resistor found')


def sw_times(parts, rge_on, rge_off):
    """Worst switching times of the makers at the effective per-device gate resistances [s]: (t_d(off) max,
    t_f max, t_d(on) min); datasheet times-vs-R_G figures at their test current, 25 C, +10 % for hot (ASSUMPTION)."""
    tdo = max(np.interp(rge_off, *p_['t_rg']['td_off']) for p_ in parts) * 1.1e-9
    tf = max(np.interp(rge_off, *p_['t_rg']['tf']) for p_ in parts) * 1.1e-9
    tdn = min(np.interp(rge_on, *p_['t_rg']['td_on']) for p_ in parts) * 1e-9
    return tdo, tf, tdn


def dead_time_window(td_off, tf, td_on, skew, tol=None):
    """DR-02 decision: the hardware guard at the gates must cover the outgoing device's turn-off (t_d(off) + t_f, hot)
    minus the incoming device's own turn-on delay plus a 20 ns margin; the logic-side delay element adds the driver
    delay mismatch on both ends and has a +/-tol tolerance.  Returns the gate-level (min, max) window [s]."""
    tol = a('t_dead_hw_tol') if tol is None else tol
    g_min = td_off + tf - td_on + 20e-9
    s_min = g_min + skew
    s_max = s_min * (1 + tol) / (1 - tol)
    return (g_min, s_max + skew)


def make_opt(tag, parts, npar, thermal, maker_roles, cost, **kw):
    """Switch-option profile from one maker's part, or - for two makers - a 'pair' whose every operating point is the
    WORSE of the two makers (higher loss, temperatures, currents; ZVS only if both have it), with one gate design and
    one dead-time guard valid for both and a per-parameter worst-case part ('d') for the scalar checks."""
    drv, rail = dev.NSI6651, DESIGN_RAIL
    mpb = kw.pop('modules_per_bridge', 1)
    rg_off = gate_r_off(parts, npar, drv, rail)
    rg_on = kw.pop('rg_on', max(rg_off, 2.0))
    rge_on, rge_off = rg_on + drv['roh_eff'] * npar, rg_off + drv['rol'] * npar
    tdo, tf, tdn = sw_times(parts, rge_on, rge_off)
    hw = dead_time_window(tdo, tf, tdn, drv['tpd'][2] - drv['tpd'][0])

    def one(ps, d):
        names = ' / '.join(p_['mpn'] for p_ in ps)
        o = dict(name=(f'{npar} x {names} per switch' if thermal == 'discrete' else
                       f'{names} (2 half bridges per bridge)'),
                 short=tag, kind='generic', d=d, parts=ps, npar=npar, thermal=thermal, roles=maker_roles,
                 rg_on=rg_on, rg_off=rg_off, rge_on=rge_on, rge_off=rge_off, td_off=tdo, tf=tf, td_on=tdn, hw_dt=hw,
                 dr=max((p_['rds_max_25'] / float(dev.g_rds(p_, 25.0)) - 1) / 2 for p_ in ps),
                 k_sw=a('k_sw_share') if npar > 1 else 1.0, cost=cost, modules_per_bridge=mpb, l_bus=None)
        if thermal == 'discrete':
            o.update(rth_ins=dev.INSULATOR['rth_stack'], rth_cp=a('Rth_cp_dev'), k_x=a('k_x_dev'),
                     c_node=a('c_node_wind') + npar * dev.INSULATOR['c_tab'], l_loop=a('L_loop_disc')[npar],
                     i_term_rms=None)
        else:
            o.update(rth_jh=max(p_['rth_jc'] + p_['rth_ch'] for p_ in ps), c_node=a('c_node'),
                     l_loop=max(p_['l_stray'] for p_ in ps) + a('L_bus'), i_term_rms=min(p_['i_term_rms'] for p_ in ps),
                     i_pos_rms=min(p_['i_pos_rms'] for p_ in ps))
        o.update(kw)
        return o
    if len(parts) == 1:
        return one(parts, parts[0])
    o = one(parts, dev.composite(tag, parts, rge_on, rge_off))
    o['kind'] = 'pair'
    o['members'] = [one([p_], p_) for p_ in parts]
    for m in o['members']:
        m['dr'] = o['dr']                          # one sharing assumption for the board, worst maker's spread
    return o


def build_options():
    """The option set of the study: (iv) incumbent, (i) two and (ii) three discretes per switch from each maker and as
    the per-parameter worst case of the primary and alternate maker (the design basis, tag ending 'W'), (iii) the
    Asian half-bridge modules.  Cost per bridge = power semiconductors + their insulators at the highest price break
    <= 1000 pcs; drivers and bias are the same count (4 per bridge) in every option and are left out."""
    DI, DSC, DB = dev.DISC['IV3Q12013T4Z'], dev.DISC['SG2M014120LJ'], dev.DISC['B3M013C120Z']
    gm4 = dict(MOD_GM4, short='GM4', roles='incumbent (today)',
               hw_dt=dead_time_window((42e-9 + dev.TDOFF_RG_SLOPE * a('RG_off')) * 1.1, 0.8 * 100 / 9.06e9 * 1.1,
                                      20e-9, dev.UCC21710['tsk_pp']),
               cost=dict(usd_per_bridge=dev.PRICES['CBB011M12GM4T'][0],
                         basis='1 x CBB011M12GM4T, ' + dev.PRICES['CBB011M12GM4T'][1]))
    ins = dev.INSULATOR
    opts = [gm4]
    for npar in (2, 3):
        for tag, parts, roles in ((f'D{npar}-IC', [DI], 'InventChip (primary)'),
                                  (f'D{npar}-SC', [DSC], 'Sichain (alternate)'),
                                  (f'D{npar}-BA', [DB], 'BASiC (third source, not in the decision)'),
                                  (f'D{npar}W', [DI, DSC], 'design basis: worst of InventChip and Sichain')):
            known = [dev.PRICES.get(p_['mpn']) for p_ in parts]
            unit = max((k[0] for k in known if k), default=None)
            cost = dict(usd_per_bridge=None if unit is None else 4 * npar * (unit + ins['price']),
                        basis=('%d x %s at %.2f USD + insulator %.2f USD; ' % (4 * npar, ' / '.join(
                            p_['mpn'] for p_ in parts), unit, ins['price']) if unit else 'RFQ: ') +
                        '; '.join('%s: %s' % (p_['mpn'], dev.PRICES[p_['mpn']][1] if dev.PRICES.get(p_['mpn'])
                                               else 'no public price (RFQ)') for p_ in parts))
            opts.append(make_opt(tag, parts, npar, 'discrete', roles, cost))
    MB, ML = dev.AMOD['BMF008MR12E2G3'], dev.AMOD['DFS08HF12EZA2']
    for tag, parts, roles in (('M-BA', [MB], 'BASiC module (primary)'), ('M-LP', [ML], 'Leapers module (alternate)'),
                              ('MW', [MB, ML], 'design basis: worst of BASiC and Leapers modules')):
        opts.append(make_opt(tag, parts, 1, 'module', roles,
                             dict(usd_per_bridge=None, basis='RFQ: no public price for either module'),
                             modules_per_bridge=2))
    return opts


def _opt_eval(o, D):
    """Window (600-900 / 700-900 V, 20-60 kW) and light-load grids with best modulation for one option."""
    v1, v2, p = window_grid((20e3, 30e3, 40e3, 50e3, 60e3))
    r = best_modulation(v1, v2, p, D, grid=TPS_COARSE, mod=o)
    full = p == 60e3
    ok_t = np.maximum(r['tj1'], r['tj2']) <= a('Tj_max_design')
    ok_i = current_ok(r, o)
    ok_s = ssoa_ok(r, mod=o)
    e8 = best_modulation([800.0, 800.0], [800.0, 800.0], [60e3, 30e3], D, grid=TPS_COARSE, mod=o)
    vl1, vl2, pl = np.meshgrid([700.0, 800.0, 900.0], [700.0, 800.0, 900.0], [5e3, 10e3, 15e3, 20e3, 30e3],
                               indexing='ij')
    rl = best_modulation(vl1.ravel(), vl2.ravel(), pl.ravel(), D, grid=TPS_COARSE, mod=o)
    zv = evaluate(800.0 + 0 * np.arange(12), 800.0, np.linspace(2.5e3, 30e3, 12), D, mod=o, iters=1)
    zvs_min = [float(pp) for pp, z1, z2 in zip(np.linspace(2.5e3, 30e3, 12), zv['zvs1'], zv['zvs2']) if z1 and z2]
    return dict(r=r, full=full, ok=ok_t & ok_i & ok_s, ok_t=ok_t, ok_i=ok_i, ok_s=ok_s, e8=e8, rl=rl,
                zvs_min_800_W=min(zvs_min) if zvs_min else float('nan'))


def device_options(D):
    """Evaluate every option with the same rules, dead-time treatment and cooling; pick by the decision rule."""
    rows, evals = [], {}
    for o in OPTIONS:
        q = _opt_eval(o, D)
        evals[o['short']] = q
        r, full = q['r'], q['full']
        rows.append(dict(option=o['short'], description=o['name'], makers=o['roles'], npar=o['npar'],
                         devices_per_bridge=4 * o['npar'] if o['thermal'] == 'discrete' else o['modules_per_bridge'],
                         eta_800_800_60kW=float(q['e8']['eta'][0]), loss_800_800_60kW_W=float(q['e8']['ploss'][0]),
                         eta_800_800_30kW=float(q['e8']['eta'][1]), eta_peak_light=float(np.nanmax(q['rl']['eta'])),
                         eta_mean_window=float(np.mean(r['eta'])), eta_min_window=float(np.min(r['eta'])),
                         frac_window_ge_98p8=float(np.mean(r['eta'] >= 0.988)),
                         Tj_max_window_C=float(np.max(np.maximum(r['tj1'], r['tj2']))),
                         Tj_max_full_C=float(np.max(np.maximum(r['tj1'], r['tj2'])[full])),
                         full_power_points_ok=int(q['ok'][full].sum()), full_power_points=int(full.sum()),
                         tj_rule_ok=bool(q['ok_t'][full].all()), current_rule_ok=bool(q['ok_i'][full].all()),
                         soa_ok=bool(q['ok_s'][full].all()),
                         I_dev_or_term_max_A=float(np.max(np.maximum(r['idev1'], r['idev2'])) if o['thermal'] ==
                                                   'discrete' else np.max(np.maximum(r['IL'], r['Is'])) / o['npar']),
                         dead_time_hw_ns=[round(x * 1e9) for x in (o.get('hw_dt') or a('t_dead_hw'))],
                         p_body_diode_800_60kW_W=float(q['e8']['pdio1'][0] + q['e8']['pdio2'][0]),
                         zvs_min_power_800_800_W=q['zvs_min_800_W'],
                         cost_usd_per_bridge=o['cost']['usd_per_bridge'], cost_basis=o['cost']['basis']))
    inc = [x for x in rows if x['option'] == 'GM4'][0]
    for x in rows:               # decision rule = Tj and current (coordinator); the SOA limits power (derating map)
        x['rules_ok'] = x['tj_rule_ok'] and x['current_rule_ok']
        x['eta_not_worse'] = (x['eta_mean_window'] >= inc['eta_mean_window'] - 1e-4 and
                              x['eta_800_800_60kW'] >= inc['eta_800_800_60kW'] - 1e-4)
    return rows, evals


def choose_option(rows):
    """Decision rule: among the design-basis options (two makers combined) that meet the rules and are not less
    efficient than the incumbent, the cheapest with a known price; RFQ-only options cannot be ranked on cost.  If no
    option is efficient enough, the rule cannot be met: the rule-compliant design-basis option with the highest
    window-mean efficiency is taken and the shortfall is reported (decision_rule_met False)."""
    basis = [x for x in rows if x['option'].endswith('W') and x['rules_ok']]
    cand = [x for x in basis if x['eta_not_worse']]
    priced = [x for x in cand if x['cost_usd_per_bridge'] is not None]
    if priced:
        pick = dict(min(priced, key=lambda x: x['cost_usd_per_bridge']), decision_rule_met=True)
    elif cand:
        pick = dict(cand[0], decision_rule_met=True)
    else:
        pick = dict(max(basis, key=lambda x: x['eta_mean_window']), decision_rule_met=False)
    for x in rows:
        if x['option'] == pick['option']:
            x.update(pick)
    return pick, cand


def _win(b):
    return (b['v1'] >= 600) & (b['v1'] <= 900) & (b['v2'] >= 700) & (b['v2'] <= 900)


def stress(R, D):
    b, ok = R['map']['best'], R['map']['ok']
    # worst-case stresses over every feasible point of the map (both directions)
    k = ok
    st = {q: float(np.max(np.abs(b[q][k]))) for q in ('IL', 'ILpk', 'Is', 'Ispk', 'ipos1', 'ipos2', 'idev1', 'idev2',
                                                      'ioff1',
                                                      'ioff2', 'IC1', 'IC2', 'b_pk', 'p_xfmr', 'p_ind', 'Im',
                                                      'Impk')}
    st['tj_max'] = float(np.max(np.maximum(b['tj1'], b['tj2'])[k]))
    i = int(np.argmax(np.where(k, np.maximum(b['tj1'], b['tj2']), -np.inf)))       # NaN-safe
    st['tj_max_at'] = (float(b['v1'][i]), float(b['v2'][i]), float(b['p'][i]))
    st['vds_pk'] = float(np.max(np.maximum(b['v1'] + dv_os(b['ioff1'], v=b['v1']),
                                           b['v2'] + dv_os(b['ioff2'], v=b['v2']))[k]))
    st['t_xfmr_max'] = float(np.max(b['t_xfmr'][k]))
    st['q_cool_max'] = float(np.max(b['q_cool'][k]))
    st['I1_max'] = float(np.max(np.abs(b['I1'][k])))
    st['I2_max'] = float(np.max(np.abs(b['I2'][k])))
    # efficiency summary
    m20 = ok & (np.abs(b['p']) >= 20e3)
    w = _win(b)
    st['eta_peak'] = float(np.max(b['eta'][ok]))
    j = int(np.argmax(np.where(ok, b['eta'], -np.inf)))
    st['eta_peak_at'] = (float(b['v1'][j]), float(b['v2'][j]), float(b['p'][j]))
    st['eta_min_20_win'] = float(np.min(b['eta'][m20 & w]))
    st['below_988_win'] = [(float(b['v1'][i]), float(b['v2'][i]), float(b['p'][i]), float(b['eta'][i]))
                           for i in np.where(m20 & w & (b['eta'] < 0.988))[0]]
    st['n_20_win'] = int((m20 & w).sum())
    st['infeasible_win_60k'] = [(float(b['v1'][i]), float(b['v2'][i]), float(b['p'][i]))
                                for i in np.where(~ok & w & (np.abs(b['p']) == 60e3))[0]]
    # nominal points
    nom = evaluate([800.0, 800.0, 800.0, 800.0], [800.0, 800.0, 800 / D['n'], 800 / D['n']],
                   [60e3, 30e3, 60e3, 30e3], D)
    st['nominal'] = {f'{int(nom["v1"][q])}V/{nom["v2"][q]:.0f}V/{nom["p"][q]/1e3:.0f}kW':
                     {kk: float(np.asarray(nom[kk])[q]) for kk in ('eta', 'ploss', 'pc1', 'pc2', 'psw1', 'psw2',
                                                                  'p_core', 'p_cu', 'pl_core', 'pl_cu', 'p_cap',
                                                                  'p_bus', 'tj1', 'tj2', 'IL', 'ILpk', 'phi')}
                     for q in range(4)}
    R['stress'] = st


def _ripple_pp(o, n_cap_c):
    """Peak-peak DC-link voltage ripple of each bank from the exact bridge DC-side current (bank only)."""
    th = np.linspace(0, 2 * PI, 2048, endpoint=False)
    e = _ex(o)
    il, im = currents(e, th)
    w1, w2 = _w(th, e['a1']), _w(th - e['phi'], e['a2'])
    out = []
    for i_br, c in ((w1 * il, n_cap_c[0]), (w2 * (il - im) * o['n'], n_cap_c[1])):
        ic = i_br - np.mean(i_br, -1, keepdims=True)
        q = np.cumsum(ic, -1) * (1 / F) / th.size
        out.append((np.max(q, -1) - np.min(q, -1)) / c)
    return out


def specs(R, D):
    st = R['stress']
    sp = {}
    # capacitor banks: count by RMS (<= 0.8 x Irms rating at 10 kHz/70 C) and ripple (<= 1 % p-p)
    b, ok = R['map']['best'], R['map']['ok']
    banks = {}
    for port, mpn, ic_key, vmax in ((1, 'C4AQUEW5450A3BJ', 'IC1', 950.0), (2, 'C4AQQEW5650A3BJ', 'IC2', 900.0)):
        c = dev.CAPS[mpn]
        n_rms = int(np.ceil(st[ic_key] / (0.8 * c['irms'])))
        nn = max(n_rms, 2)
        while True:
            o = dict(v1=b['v1'][ok], v2=b['v2'][ok], n=D['n'], L=D['L'], Lm=D['Lm'], a1=b['a1'][ok],
                     a2=b['a2'][ok], phi=b['phi'][ok])
            rp = _ripple_pp(o, (nn * c['c'], nn * c['c']))[port - 1]
            vref = (b['v1'] if port == 1 else b['v2'])[ok]
            if np.max(rp / vref) <= 0.01 or nn >= 8:
                break
            nn += 1
        icap = st[ic_key] / nn
        t_hot = 60.0 + icap ** 2 * c['esr'] * c['rth']          # 60 C local air (A) + self-heating
        vd = dev.CAP_VDERATE[int(c['vndc'])]
        v_allow = float(np.interp(t_hot, [70.0, 85.0, 105.0], vd))
        banks[port] = dict(mpn=mpn, n=nn, c=nn * c['c'], v_rating=c['vndc'], v_max=vmax, i_rms_bank=st[ic_key],
                           i_rms_per_cap=icap, i_rms_rating=c['irms'], ripple_pp_max=float(np.max(rp)),
                           ripple_pct=float(np.max(rp / vref) * 100), t_hotspot=t_hot, v_allow_at_hotspot=v_allow,
                           esl=c['esl'], esr=c['esr'], src=c['src'])
    sp['caps'] = banks
    # transformer
    vs = max(950.0, D['n'] * 900.0) / (2 * F)                                   # V*s per half period
    strand = 0.1e-3
    sp['xfmr'] = dict(n=D['n'], N1=D['N1'], N2=D['N2'], L_sigma_est=D['Llk'], Lm=D['Lm'], gap_m=D['gap_m'],
                      vs_half=vs, b_pk_950=max(950.0, D['n'] * 900.0) / (4 * F * D['N1'] * D['ae']),
                      b_pk_min_section=max(950.0, D['n'] * 900.0) / (4 * F * D['N1'] * dev.CORE_PM114['amin']),
                      i1_rms=st['IL'], i2_rms=st['Is'], i1_pk=st['ILpk'], i2_pk=st['Ispk'],
                      cu_per_turn_1=D['aw_cu'] / D['N1'], cu_per_turn_2=D['aw_cu'] / D['N2'],
                      strands_1=int(D['aw_cu'] / D['N1'] / (PI / 4 * strand ** 2)),
                      strands_2=int(D['aw_cu'] / D['N2'] / (PI / 4 * strand ** 2)),
                      rdc1=D['rdc1'], rdc2=D['rdc2'], p_max=st['p_xfmr'],
                      dT=st['p_xfmr'] * a('Rth_xfmr') if a('Rth_xfmr') else None, lm_ungapped=D['lm_ungapped'])
    sp['ind'] = dict(L=D['Lext'], N=D['nl'], gap=D['gap_l'], i_rms=st['IL'], i_pk=st['ILpk'],
                     i_sat=1.25 * 220.0, b_at_220A=D['Lext'] * 220.0 / (D['nl'] * D['ae']), p_max=st['p_ind'],
                     rdc=D['rdc_l'])
    R['specs'] = sp


def verdict_950(R, D):
    v = {}
    v['cosmic'] = cosmic_table()
    v['ioff_limit'] = {f'{int(x)}V': {'24nH': float(ssoa_current_limit(x)),
                                      '18nH': float(ssoa_current_limit(x, 18e-9 if DESIGN['kind'] == 'gm4' else
                                                                       0.75 * DESIGN['l_loop']))}
                       for x in (800.0, 900.0, 950.0, 1000.0)}
    V1g, V2g, pmax, why = R['derating']['charge']
    i950 = list(V1g[:, 0]).index(950.0)
    v['pmax_950'] = {int(V2g[i950, j]): float(pmax[i950, j]) for j in range(V2g.shape[1])}
    V1h, V2h, pmax18, _ = R['derating18']
    v['pmax_950_18nH'] = {int(V2h[i950, j]): float(pmax18[i950, j]) for j in range(V2h.shape[1])}
    b, ok = R['map']['best'], R['map']['ok']
    m = ok & (b['v1'] == 950.0)
    hard = m & ~b['zvs1']
    v['hard_on_950_max_current'] = float(np.max(b['ioff2'][hard])) if hard.any() else 0.0
    v['overshoot_crd_fig21'] = dev.CRD['snubber_overshoot_sim']
    R['v950'] = v
    if DESIGN['kind'] != 'gm4':
        c950 = [c for c in v['cosmic'] if c['v'] == 950.0][0]
        R['v950_text'] = (
            f"ACCEPTABLE WITH DERATING, cosmic-ray risk NOT quantified: (1) cosmic ray: neither {DESIGN['parts'][0]['mpn']} "
            f"nor {DESIGN['parts'][-1]['mpn']} has a published FIT-vs-DC-voltage curve; 950 V is "
            f"{950 / 1200 * 100:.0f} % of V_DSS. For scale only, Wolfspeed's curves give {c950['fit_gen4_bridge_sea']:.1f} "
            f"FIT (Gen 4) to {c950['fit_gen3_bridge_sea']:.0f} FIT (Gen 3) per bridge at 950 V at sea level for "
            f"{dev.COSMIC['die_area_cm2']} cm2 per switch - the makers' curve is a release condition. (2) switching "
            f"SOA: no switching-SOA curve for the discretes; with the assumed {DESIGN['l_loop'] * 1e9:.0f} nH loop and "
            f"t_f {DESIGN['tf'] * 1e9:.0f} ns the turn-off current at 950 V must stay below "
            f"{v['ioff_limit']['950V']['24nH']:.0f} A per switch position (V_DS,peak <= 1200 V); P_max at 950 V: "
            f"{v['pmax_950'][900]/1e3:.1f} kW at V2=900 V, {v['pmax_950'][800]/1e3:.1f} kW at 800 V, "
            f"{v['pmax_950'][700]/1e3:.1f} kW at 700 V. (3) the loop inductance is the critical assumption: verify by DPT.")
        return
    c950 = [c for c in v['cosmic'] if c['v'] == 950.0][0]
    c1000 = [c for c in v['cosmic'] if c['v'] == 1000.0][0]
    R['v950_text'] = (
        f"ACCEPTABLE WITH DERATING, not at full power: (1) cosmic ray: Gen 4 1200 V at 950 V DC is about "
        f"{c950['fit_gen4_bridge_sea']:.1f} FIT per bridge at sea level ({c950['fit_gen4_bridge_2000m']:.0f} FIT at 2000 m; "
        f"die area {dev.COSMIC['die_area_cm2']} cm2/switch ASSUMED), rising ~6x per 50 V "
        f"({c1000['fit_gen4_bridge_sea']:.1f} FIT at 1000 V); Gen 3 would be {c950['fit_gen3_bridge_sea']:.0f} FIT -> Gen 4 "
        f"only. (2) switching SOA (DS Fig. 19, 24 nH, Tvj 150 C): at 950 V the turn-off current must stay below "
        f"{v['ioff_limit']['950V']['24nH']:.0f} A ({v['ioff_limit']['950V']['18nH']:.0f} A if the loop is 18 nH), so "
        f"port 1 above 900 V is power-limited (P_max at 950 V: {v['pmax_950'][900]/1e3:.1f} kW at V2=900 V, "
        f"{v['pmax_950'][800]/1e3:.1f} kW at 800 V, {v['pmax_950'][700]/1e3:.1f} kW at 700 V). (3) hard turn-on above "
        f"~85 A is outside the turn-on SOA at 950 V -> ZVS/TPS mandatory there. Rule used: V_DS,peak = V_bus + "
        f"dV(I_off) x L_loop/24 nH <= 1200 V with dV(I) = 1200 V - V_DD,max(I) from DS Fig. 19 (vector data, "
        f"~2.1-2.4 V/A) and FIT from WP Fig. 4. A 1700 V device is not needed if the port-1 hardware OV trip is "
        f"<= 1000 V and the loop inductance is verified <= 24 nH by DPT.")


def sensitivities(R, D):
    pts = ([800.0, 800.0], [800.0, 800.0], [60e3, 30e3])
    base = evaluate(*pts, D)
    g18 = evaluate(*pts, D, vgs18=True)
    cold = evaluate(*pts, D, t_in=25.0)
    Dl = dict(D)
    Dl['L'] = D['L'] * 1.05
    phi = phi_sps(800.0, 800.0, D['n'], D['L'], 60e3)
    pl = p_sps(800.0, 800.0, D['n'], Dl['L'], phi)
    gap = dev.CRD['eff_meas']
    pm, em, rc = R['crd']
    extra = pm * (1 / em - 1) - rc['ploss']                    # unexplained CRD loss [W] at 800/800 V
    k1, k0 = np.polyfit(pm, extra, 1)
    if k1 < 0:            # residual falls with power: a negative slope must not be extrapolated to 60 kW
        k0, k1 = float(np.max(extra)), 0.0
    adj = lambda p, loss: np.abs(p) / (np.abs(p) + loss + k0 + k1 * np.abs(p))
    R['sens'] = dict(vgs18_deta=(g18['eta'] - base['eta']).tolist(), coolant25_tj=np.maximum(cold['tj1'], cold['tj2']).tolist(),
                     tj_base=np.maximum(base['tj1'], base['tj2']).tolist(), dP_L5=float(pl / 60e3 - 1),
                     crd_gap_fit=(float(k0), float(k1)), eta_base=base['eta'].tolist(),
                     eta_crd_adjusted=adj(np.array([60e3, 30e3]), base['ploss']).tolist(), gap_pts=extra.tolist(),
                     unused=gap.shape)


SNUB_ALT = (6, 6.8, 2.2e-9)


def snub_points(D, R):
    """The four terminal-network study points: highest-voltage full-power point, highest turn-off current (SPS at
    full power, SOA-feasible), and two hard-switched light-load points."""
    V1g, V2g, pmax, _ = R['derating']['charge']
    cand = [(V1g[i, 0], V2g[0, j], pmax[i, j]) for i in range(V1g.shape[0]) for j in range(V2g.shape[1])
            if np.isfinite(pmax[i, j]) and pmax[i, j] >= 50e3]
    cv1, cv2, cp = (np.array(x, float) for x in zip(*cand))
    rs = evaluate(cv1, cv2, cp, D, iters=1)                       # SPS at full power, SOA-feasible only
    okk = np.isfinite(rs['phi']) & ssoa_ok(rs)
    k = int(np.argmax(np.where(okk, np.maximum(rs['ioff1'], rs['ioff2']), -1)))
    return [('v1max', 950.0, 900.0, float(pmax[list(V1g[:, 0]).index(950.0), list(V2g[0]).index(900.0)])),
            ('ioffmax', float(cv1[k]), float(cv2[k]), float(cp[k])),
            ('hard_b1', 800.0, 900.0, 10e3), ('hard_b2', 950.0, 850.0, 5e3)]


def snubber_study(D, R):
    """Terminal snubber resistor dissipation and die-level overshoot at the worst SPS points (snubber-model decks),
    with and without the HF capacitors, plus the analytic bus ring-down power f*L_bus*sum(dI^2) per bridge."""
    pts = snub_points(D, R)
    rows = []
    for name, v1, v2, p in pts:
        for cfg, hf in (('rc_only', None), ('rc_hf', 'default')):
            q = spice_compare(f'snub_{name}_{cfg}', v1, v2, p, D, 150e-9, hf=hf, keep_csv=False, model='snub')
            r1 = evaluate([v1], [v2], [p], D, iters=1)
            rows.append(dict(point=name, cfg=cfg, V1=v1, V2=v2, P=p, P_res1_W=q['p_snub_res'][0],
                             P_res2_W=q['p_snub_res'][1], V_die1_pk=q['v_die_pk'][0], V_die2_pk=q['v_die_pk'][1],
                             V_term1_pp=q['v_term_pp'][0], V_term2_pp=q['v_term_pp'][1],
                             P_ring1_an=float(r1['p_ring1'][0]), P_ring2_an=float(r1['p_ring2'][0]),
                             I_off1=float(r1['ioff1'][0]), I_off2=float(r1['ioff2'][0]),
                             P_spice=float(0.5 * (q['p1_spice'] + q['p2_spice'])),
                             I_hf_rms_per_cap_A=max(q['hf']['i_rms_per_cap']) if q['hf'] else float('nan'),
                             I_hf_pk_per_cap_A=max(q['hf']['i_pk_per_cap']) if q['hf'] else float('nan'),
                             P_hf_per_cap_W=max(q['hf']['p_per_cap_W']) if q['hf'] else float('nan')))
    return rows


PORT_LOOP = dict(L=0.5e-6, R=3.45e-3, fuse_melt_i2t=19e3,
                 src='sim/port_design.py A_L_LOOP 0.5 uH; sim/out/port_design/report.md sec. 6.6: 135 A port R_loop '
                     '3.45 mOhm (contactor, shunt, busbars, two cold fuse links, bank ESR), 160 A link melting I2t '
                     '19 kA2s')


def _clamp_table(clamp, mod=None):
    """Bus reversal voltage vs loop current flowing from DC- to DC+ through the bridge's two series body-diode pairs
    (two legs in parallel, the design device's 3rd-quadrant curve at the off-rail, 25 C; below 1 A per position the
    diodes are taken as off) and, if fitted, the reverse clamp (n devices in parallel) at the bank.
    Returns grids (i_total, V, i_clamp, i_module)."""
    mod = DESIGN if mod is None else mod
    vg = np.linspace(0.0, 400.0, 40001)
    ii = np.logspace(0, 5, 4001)
    vpos = _vsd_pos(mod, ii, 25.0)
    ip = np.interp(vg / 2, vpos, ii)
    im = 2 * np.where(vg / 2 >= _vsd_pos(mod, 1.0, 25.0), ip, 0.0)
    ic = clamp['n'] * np.maximum(vg - clamp['vt0'], 0.0) / clamp['rt'] if clamp else 0.0 * vg
    tot = im + ic
    keep = np.concatenate([[True], np.diff(tot) > 0])
    return tot[keep], vg[keep], ic[keep], im[keep]


def reverse_clamp_study(R, D, cl=None, mod=None, dt=10e-9, t_end=1.0e-3):
    """Bolted short at a port terminal: the film bank rings through the port loop (0.5 uH, 3.45 mOhm) and below zero
    the reversal is clamped by the bridge body diodes and/or the reverse clamp (cl: a CLAMP_OPTIONS entry with 'n'
    devices in parallel; sharing between paralleled discretes k_clamp_share).  No fuse arc is credited."""
    out = {}
    mod = DESIGN if mod is None else mod
    cl = dict(dev.CLAMP_OPTIONS['DD600N16K'], n=1) if cl is None else cl
    ks = 1.0 if cl['n'] == 1 else a('k_clamp_share')
    for k in (1, 2):
        c = R['specs']['caps'][k]['c'] + a('hf_caps')[0] * a('hf_caps')[1]
        v_trip = OVX[f'port{k}']['trip_V'][2]
        res = {}
        for case, clamp in (('module_only', None), ('with_clamp', cl)):
            tg, vgr, icg, img = _clamp_table(clamp, mod)
            v, i, t, i2t, i2t_c, i2t_m = v_trip, 0.0, 0.0, 0.0, 0.0, 0.0
            i_pk, i_zero, ic_pk, im_pk, t_zero, e_c, t_melt = 0.0, None, 0.0, 0.0, None, 0.0, None
            while t < t_end:
                if v > 0:                                   # phase 1: bank discharges into the short
                    di = (v - PORT_LOOP['R'] * i) / PORT_LOOP['L'] * dt
                    v -= i / c * dt
                    i += di
                    if v <= 0:
                        i_zero, t_zero = i, t
                else:                                       # phase 2: reversal clamped, loop current decays
                    vc = float(np.interp(i, tg, vgr))
                    ic, im = float(np.interp(i, tg, icg)), float(np.interp(i, tg, img))
                    i -= (vc + PORT_LOOP['R'] * i) / PORT_LOOP['L'] * dt
                    i2t_c += ic ** 2 * dt
                    i2t_m += (im / 2 / mod['npar']) ** 2 * dt
                    e_c += vc * ic * dt
                    ic_pk, im_pk = max(ic_pk, ic), max(im_pk, im / 2 / mod['npar'])
                    if i <= 0.01 * i_zero:                  # decayed to 1 % of the reversal current
                        break
                i2t += i ** 2 * dt
                if t_melt is None and i2t >= PORT_LOOP['fuse_melt_i2t']:
                    t_melt = t
                i_pk = max(i_pk, i)
                t += dt
            d = dict(V0=v_trip, I_pk_A=i_pk, I_at_reversal_A=i_zero, t_reversal_us=t_zero * 1e6,
                     t_clamped_us=(t - t_zero) * 1e6, I_per_body_diode_pk_A=im_pk, I2t_per_body_diode_A2s=i2t_m,
                     fuse_melt_t_us=None if t_melt is None else t_melt * 1e6)
            if clamp:
                tz = t - t_zero
                i2t_dev = i2t_c * (ks / cl['n']) ** 2
                cap = (cl['i2sqrt_t'] * np.sqrt(tz) if cl.get('i2sqrt_t') else cl['i2t_10ms'] * np.sqrt(tz / 10e-3))
                d.update(I_clamp_pk_A=ic_pk, I_clamp_pk_per_device_A=ic_pk * ks / cl['n'], I2t_clamp_A2s=i2t_c,
                         I2t_per_clamp_device_A2s=i2t_dev, I2t_capability_at_event_A2s=float(cap),
                         I2t_margin_at_event=float(cap / max(i2t_dev, 1e-9)), E_clamp_J=e_c,
                         V_clamp_pk_V=float(cl['vt0'] + cl['rt'] * ic_pk * ks / cl['n']),
                         i2t_margin_10ms_hot=cl['i2t_10ms'] / max(i2t_dev, 1e-9))
                if 'zth_r' in cl:
                    zth = sum(r_ * (1 - np.exp(-tz / ta)) for r_, ta in zip(cl['zth_r'], cl['zth_tau']))
                    d['dTj_clamp_K'] = e_c / max(tz, 1e-9) * zth
            res[case] = d
        mo = res['module_only']
        i_dm = dev.MODULE['idm'] if mod['kind'] == 'gm4' else mod['d']['i_dm']
        res['module_overstress_factor'] = mo['I_per_body_diode_pk_A'] / i_dm
        res['body_diode_pk_vs_IDM'] = res['with_clamp']['I_per_body_diode_pk_A'] / i_dm
        res['V_RRM_margin'] = cl['vrrm'] / OVX[f'port{k}']['V_peak']
        res['V_body_pair_at_1A_V'] = {'25C': float(2 * _vsd_pos(mod, mod['npar'] * 1.0, 25.0)),
                                      '175C': float(2 * _vsd_pos(mod, mod['npar'] * 1.0, 175.0))}
        if cl.get('ir_hot'):
            res['leakage_at_Vmax_mA'] = {'Tvj_150C_datasheet_max': cl['ir_hot'] * 1e3,
                                         'estimate_60C': cl['ir_hot'] * 1e3 / 2 ** 9}
        out[f'port{k}'] = res
    return out


def clamp_options(R, D):
    """Cost-driven re-selection of the reverse clamp: every candidate at the smallest parallel count that keeps
    (a) I2t per device >= 2 x below its capability scaled to the event duration (I^2 sqrt(t) rule), and (b) the
    SiC body-diode peak per device <= 10 % of the device's pulsed rating; cost per module (two ports)."""
    rows = []
    for key, base in dev.CLAMP_OPTIONS.items():
        found = None
        for n in ([1] if key in ('DD600N16K', 'MDC600-16-416F3', 'SDD600N16BT') else range(2, 41, 2)):
            cl = dict(base, n=n)
            q = reverse_clamp_study(R, D, cl=cl, dt=50e-9)
            ok = all(q[f'port{k}']['with_clamp']['I2t_margin_at_event'] >= 2.0 and
                     q[f'port{k}']['body_diode_pk_vs_IDM'] <= 0.10 for k in (1, 2))
            if ok:
                found = (n, q)
                break
        if found is None:
            rows.append(dict(part=key, maker=base['maker'], asian=base['asian'], n_per_port=None, feasible=False,
                             cost_usd_per_module=None, price_src=base['price_src']))
            continue
        n, q = found
        w = [q[f'port{k}']['with_clamp'] for k in (1, 2)]
        rows.append(dict(part=key, maker=base['maker'], asian=base['asian'], n_per_port=n, feasible=True,
                         I2t_margin_at_event_min=min(x['I2t_margin_at_event'] for x in w),
                         V_clamp_pk_V=max(x['V_clamp_pk_V'] for x in w),
                         body_diode_pk_per_device_A=max(x['I_per_body_diode_pk_A'] for x in w),
                         body_diode_pk_vs_IDM=max(q[f'port{k}']['body_diode_pk_vs_IDM'] for k in (1, 2)),
                         I_pk_per_clamp_device_A=max(x['I_clamp_pk_per_device_A'] for x in w),
                         cost_usd_per_module=(None if base['price'] is None else 2 * n * base['price']),
                         price_src=base['price_src'], package=base.get('package', base.get('pkg', ''))))
    return rows


INS_SRC = 'sim/out/insulation/report.md sec. 3 table (D-032, IC-08)'
INSULATION = {
    'port1_to_port2': {'class': 'basic (simple separation) - D-032 resolves the former "pending"',
                       'working_voltage_V_dc': 1850, 'recurring_peak_V': 2099, 'frequency_kHz': 100,
                       'PD_test_Vpk': 3149, 'PD_max_pC': 10, 'AC_test_V_rms': 3323, 'DC_test_V': 4700,
                       'impulse_V': 6000, 'creepage_mm': {'PD2 group I (CTI >= 600)': 10.0, 'PD2 group IIIa': 20.0,
                                                          'PD1 (coated)': 7.5},
                       'clearance_mm_2000_3000_4000m': [5.5, 6.3, 7.1], 'src': INS_SRC},
    'port1_winding_to_core_PE': {'class': 'basic', 'working_voltage_V_dc': 950, 'recurring_peak_V': 1199,
                                 'PD_test_Vpk': 1799, 'PD_max_pC': 10, 'AC_test_V_rms': 2200, 'DC_test_V': 3111,
                                 'impulse_V': 6000, 'creepage_mm_PD2_group_I': 5.0,
                                 'clearance_mm_2000_3000_4000m': [5.5, 6.3, 7.1], 'src': INS_SRC},
    'port2_winding_to_core_PE': {'class': 'basic', 'working_voltage_V_dc': 900, 'recurring_peak_V': 1149,
                                 'PD_test_Vpk': round(1.5 * 1149), 'PD_max_pC': 10, 'AC_test_V_rms': 2200,
                                 'DC_test_V': 3111, 'impulse_V': 6000, 'creepage_mm_PD2_group_I': 5.0,
                                 'clearance_mm_2000_3000_4000m': [5.5, 6.3, 7.1],
                                 'src': INS_SRC + ' (PD test 1.5 x recurring peak, same rule as port 1)'},
    'winding_to_NTC_PELV': {'class': 'reinforced', 'PD_test_Vpk': 2249, 'PD_max_pC': 10, 'AC_test_V_rms': 4400,
                            'impulse_V': 8000, 'src': INS_SRC},
}


def efficiency_claim(R):
    """DAB-04 statement: model and calibrated (model + the unexplained CRD loss k0 + k1*P at every point) figures."""
    b, ok = R['map']['best'], R['map']['ok']
    k0, k1 = R['sens']['crd_gap_fit']
    cal = np.abs(b['p']) / (np.abs(b['p']) + b['ploss'] + k0 + k1 * np.abs(b['p']))
    w20 = ok & _win(b) & (np.abs(b['p']) >= 20e3)
    i8 = ok & (b['v1'] == 800) & (b['v2'] == 800) & (np.abs(b['p']) == 60e3)
    out = dict(model_peak=float(np.max(b['eta'][ok])), cal_peak=float(np.max(cal[ok])),
               model_min_ge20kW_window=float(np.min(b['eta'][w20])), cal_min_ge20kW_window=float(np.min(cal[w20])),
               model_800_800_60kW=float(np.max(b['eta'][i8])), cal_800_800_60kW=float(np.max(cal[i8])),
               cal_frac_window_ge_98p8=float(np.mean(cal[w20] >= 0.988)), k0_W=k0, k1_W_per_W=k1)
    out['dab04_peak_met'] = out['cal_peak'] >= 0.992
    out['dab04_20kW_met'] = out['cal_min_ge20kW_window'] > 0.988
    out['statement'] = ('DAB-04 (99.2 %% peak, > 98.8 %% above 20 kW) is %s. Claimed (calibrated to Wolfspeed\'s measured '
                        'CRD loss, the model + %.0f W + %.1f W/kW): peak %.2f %%, %.2f %% at 800/800 V 60 kW, minimum '
                        '%.2f %% above 20 kW in the full-power window (%.0f %% of those points >= 98.8 %%). Model alone '
                        '(not claimed): peak %.2f %%, %.2f %% at 800/800 V 60 kW, minimum %.2f %%.'
                        % ('MET' if out['dab04_peak_met'] and out['dab04_20kW_met'] else 'NOT MET',
                           k0, k1 * 1e3, out['cal_peak'] * 100, out['cal_800_800_60kW'] * 100,
                           out['cal_min_ge20kW_window'] * 100, out['cal_frac_window_ge_98p8'] * 100,
                           out['model_peak'] * 100, out['model_800_800_60kW'] * 100,
                           out['model_min_ge20kW_window'] * 100))
    return out


def temperature_trips(R, D):
    """DR-06: hardware over-temperature trips.  Discretes have no internal NTC: one metal-tag NTC on the cold plate
    between the hottest positions of each bridge (local plate temperature = ths in the model) feeds a comparator into
    the trip chain.  Trip level = Tj limit for a trip (Tj max - 10 K) minus the largest modelled plate-to-junction
    offset at full power with the pessimistic extra loss in the switches; nuisance-free if the normal worst plate
    temperature stays >= 5 K below it."""
    k0, k1 = R['sens']['crd_gap_fit']
    v1, v2, p = window_grid((60e3,))
    rn = best_modulation(v1, v2, p, D, grid=TPS_COARSE)
    rp = best_modulation(v1, v2, p, D, grid=TPS_COARSE, extra=(k0, k1, 'modules'))
    tjl = (DESIGN['d']['tj_max'] if DESIGN['kind'] != 'gm4' else dev.MODULE['tvj_op_max']) - 10.0
    off_n = float(np.max(np.maximum(rn['tj1'] - rn['ths1'], rn['tj2'] - rn['ths2'])))
    off_p = float(np.max(np.maximum(rp['tj1'] - rp['ths1'], rp['tj2'] - rp['ths2'])))
    ths_n = float(np.max(np.maximum(rn['ths1'], rn['ths2'])))
    t_upper = float(np.floor(tjl - off_p))                       # Tj-based ceiling for a plate trip
    t_trip = float(min(np.ceil(ths_n + 20.0), t_upper))           # coolant-loss / local-heating level, nuisance-free
    o_ = DESIGN
    vis = (o_['rth_cp'] * (1 + o_['k_x']) / (o_['rth_cp'] * (1 + o_['k_x']) + o_['d']['rth_jc'] + o_['rth_ins'])
           if o_['kind'] != 'gm4' else None)
    return dict(sensor='metal-tag NTC (B57703M class) on the cold plate between the two hottest positions of each '
                       'bridge, PELV-insulated probe, hardware comparator into the bridge trip chain (fails "hot": '
                       'an open NTC must read as over-temperature)',
                tj_limit_for_trip_C=tjl, offset_plate_to_tj_normal_K=off_n, offset_plate_to_tj_pessimistic_K=off_p,
                plate_trip_C=t_trip, plate_firmware_derate_from_C=t_trip - 10.0, plate_max_normal_C=ths_n,
                nuisance_margin_K=t_trip - ths_n, plate_trip_Tj_based_ceiling_C=t_upper,
                plate_sees_fraction_of_device_rise=vis,
                limitation=('a plate NTC sees only %.0f %% of a device\'s own temperature rise (R_th plate / (plate + '
                            'j-c + insulator)): it detects coolant loss and gross local heating, not a single device\'s '
                            'loss excursion - those are caught by DESAT / OC / ZVS supervision in firmware' %
                            (100 * vis) if vis else 'module NTC'),
                transformer={'sensor': 'NTC embedded at the winding hot spot by the vendor (reinforced to PELV, IC-08)',
                             'hardware_trip_C': 140.0, 'firmware_derate_from_C': a('T_xfmr_hs_max'),
                             'basis': 'hot-spot design limit %.0f C (class F 155 C with margin)' % a('T_xfmr_hs_max')},
                coolant={'inlet_derate_above_C': a('T_coolant_in'), 'thermostat_opens_C': 77.0,
                         'flow_trip_lpm': 6.0})


def protection_numbers(R, D, mod=None):
    """DR-03 (trip turn-off and peak), DR-05 (DESAT budget against an ASSUMED short-circuit withstand), DR-06 (trip
    temperatures from the modelled sensor-to-junction offsets), DR-04 (cross-trip time between the bridges)."""
    mod = DESIGN if mod is None else mod
    drv, d, n = dev.NSI6651, mod['d'], mod['npar']
    st = R['stress']
    oc = float(np.ceil(1.15 * st['ILpk'] / 10) * 10)
    band = (oc - 10.0, oc + 10.0)
    slope = (1000.0 + D['n'] * 950.0) / D['L']            # A/s: both bridges at their hardware OV trip, opposing
    q_down = n * (d['qg'] - d['qgs'] - d['qgd'])            # gate charge from +18 V down to the Miller plateau end
    t_inj = a('t_trip_logic') + drv['t_fil'][2] + drv['t_off'][2]
    t_ramp = q_down / drv['i_sto'][0]
    t_resp = t_inj + t_ramp
    # PWM-bounded peak: the largest SPS current the modulator can command with the 60 deg phase clamp at the trip
    # voltages (a control fault cannot push the current beyond it without a hardware fault)
    vv1, vv2 = np.meshgrid([590.0, 800.0, 1000.0], [400.0, 700.0, 950.0], indexing='ij')
    o = dict(v1=vv1.ravel(), v2=vv2.ravel(), n=D['n'], L=D['L'], Lm=D['Lm'], a1=0.0, a2=0.0, phi=np.radians(60))
    i_pwm = float(np.max(stats(o)['ILpk']))
    i_pk = band[1] + slope * t_resp                          # fault peak: steepest slope for the whole response
    t_fall = n * 0.5 * d['qgs'] / drv['i_sto'][0]           # current fall: V_GS from plateau to ~V_th (half Q_gs)
    dv_soft = mod['l_loop'] * i_pk / t_fall
    i_sat_l = dev.FERRITE['N95']['bsat_100C'] * D['nl'] * D['ae'] / D['Lext']
    t_hard = 0.78e-6                                         # drawn DAB60 rev B local OC path to the gate (review DR-03)
    i_hard = band[1] + slope * t_hard
    out = dict(
        oc_trip_A=oc, oc_band_A=list(band), slope_A_per_us=slope * 1e-6,
        mechanism=('every hardware trip (local OC window, port OV, DC flux, +24V UV, driver RDY/UVLO, cross-trip '
                   'from the other bridge, over-temperature) injects into the DESAT pin of all 8 NSI6651 drivers '
                   '(diode-OR, >= 10 V from each driver\'s own VCC2 through a switch referenced to that driver\'s '
                   'GND2: one isolated trip channel per driver, reinforced like the driver itself), so every switch '
                   'turns off through the driver soft turn-off (I_STO >= 0.25 A, industrial grade). RST/EN low alone '
                   'is a normal fast turn-off (NSI datasheet p28) and is used only AFTER the soft turn-off, '
                   '>= 3 us later, to latch the state'),
        t_inject_us=t_inj * 1e6, t_soft_ramp_us=t_ramp * 1e6, t_response_us=t_resp * 1e6,
        I_peak_fault_A=i_pk, I_peak_pwm_bounded_A=i_pwm, I_per_device_peak_A=i_pk / n * share(mod)[0],
        I_DM_device_A=d['i_dm'], soft_turn_off_fall_ns=t_fall * 1e9, overshoot_soft_V=dv_soft,
        V_peak_soft_V=1000.0 + dv_soft, inductor_I_sat_100C_A=i_sat_l,
        hard_path_as_drawn={'t_us': t_hard * 1e6, 'I_peak_A': i_hard, 'V_peak_V': 1000.0 + float(dv_os(i_hard, v=1000.0))},
        ov_trip_turn_off={'I_A': OVX['port1']['I_off_SSOA_limit_at_peak_A'] if 'port1' in OVX else None})
    out['inductor_note'] = ('series inductor saturates at about %.0f A (0.41 T, 100 C) against a fault peak of %.0f A: '
                            'the I_sat_min requirement is raised to %.0f A (1.1 x fault peak)'
                            % (i_sat_l, i_pk, 1.1 * i_pk)) if i_sat_l < 1.1 * i_pk else 'inductor I_sat covers the peak'
    # DR-05: DESAT budget (blanking from the internal LEB and the DESAT-pin capacitance; no external C_blk)
    blank = drv['t_leb'] * 1.25 + a('c_desat') * drv['v_desat'][2] / drv['i_chg'][0]
    t_gate = blank + drv['t_fil'][2] + drv['t_off'][2]
    t_off_sc = t_gate + t_ramp
    tsc = {p_['mpn']: p_['tsc'] for p_ in mod['parts']}
    tsc_hot_1000 = {k: v * 800.0 / 1000.0 * a('tsc_hot_factor') for k, v in tsc.items()}
    out['desat'] = dict(blanking_ns_max=blank * 1e9, to_gate_falling_ns_max=t_gate * 1e9,
                        to_plateau_end_ns_max=t_off_sc * 1e9, tsc_assumed_us_800V_25C=tsc,
                        tsc_assumed_us_1000V_150C={k: round(v * 1e6, 2) for k, v in tsc_hot_1000.items()},
                        required_ns=0.5 * min(tsc_hot_1000.values()) * 1e9,
                        margin=min(tsc_hot_1000.values()) / t_off_sc,
                        v_ds_trip_V=[round(x, 2) for x in a('desat_vds_trip')],
                        i_trip_A_hot_min=a('desat_vds_trip')[0] / (float(dev.g_rds(d, 175.0)) / n),
                        i_trip_A_cold_max=a('desat_vds_trip')[2] / (float(dev.g_rds(d, 25.0)) / n))
    return out


COSTED_BOM = 'bom/DAB-D60_costed_BOM.csv'
MY_LINES = {   # costed-BOM value/MPN -> group (the parts this design owns; port parts are the port engineer's)
    'CBB011M12GM4T': 'switches', 'DD600N16K': 'reverse clamp', 'Transformer 11:12 (CUSTOM)': 'transformer',
    '2.12 uH series L (CUSTOM)': 'series inductor', '47n 1.5kV C0G': 'HF capacitors', '1.1n 2kV C0G': 'snubber',
    '13.6R 3W': 'snubber', 'cold plate': 'cold plate', 'VK315M0P10PI31': 'coolant flow switch',
    '3106U': 'coolant thermostat'}


def cost_before():
    """USD per module of this design's parts in the costed BOM (gen/cost.py run of 2026-10-04)."""
    out = {}
    for r_ in csv.DictReader(open(COSTED_BOM)):
        key = next((k for k in MY_LINES if k in (r_['MPN'], r_['Value'])), None)
        if key:
            out[MY_LINES[key]] = out.get(MY_LINES[key], 0.0) + float(r_['ext_cost_usd'])
    return out


def cost_after(R):
    """Same groups for this design (USD per module) with the basis of each line."""
    pick = R['pick']
    cl = R['clamp_pick']
    hf, sn = R['hf'], R['snb_cost']
    out = {'switches': (2 * pick['cost_usd_per_bridge'], pick['cost_basis']),
           'reverse clamp': (cl['cost_usd_per_module'], '%s x %d per port, %s' % (cl['part'], cl['n_per_port'],
                                                                                   cl['price_src'])),
           'HF capacitors': (2 * hf['n'] * dev.HF_CAP['price'], '%d x %s per bridge, %s' % (hf['n'], dev.HF_CAP['mpn'],
                                                                                          dev.HF_CAP['price_src'])),
           'snubber': sn}
    out.update(COST_OTHER)
    if MAGD is not None:
        out['transformer'] = (MAGD['xfmr']['cost']['total_usd'], MAGD['xfmr']['cost']['basis'][:140])
        out['series inductor'] = (MAGD['ind']['cost']['total_usd'], MAGD['ind']['cost']['basis'][:140])
    return out


def snubber_decision(R):
    """Resistor rating from the study: worst simulated value and the map-wide worst ring-down power times the largest
    simulated share that reaches the RC resistors (HF ceramics fitted)."""
    n, r_ohm, c_f = a('snub')
    hfr = [q for q in R['snub'] if q['cfg'] == 'rc_hf']
    share = max(max(q['P_res1_W'] * n / max(q['P_ring1_an'], 1e-3) if q['P_ring1_an'] > 1 else 0,
                    q['P_res2_W'] * n / max(q['P_ring2_an'], 1e-3) if q['P_ring2_an'] > 1 else 0) for q in hfr)
    b, ok = R['map']['best'], R['map']['ok']
    pr_max = float(np.max(np.maximum(b['p_ring1'], b['p_ring2'])[ok]))
    worst_sim = max(max(q['P_res1_W'], q['P_res2_W']) for q in hfr)
    worst = max(worst_sim, share * pr_max / n)
    for p_rate, nn in ((1.5, n), (3.0, n), (3.0, 2 * n)):
        if worst * n / nn <= 0.5 * p_rate:
            break
    per = dict(n=nn, r=r_ohm * nn / n, c=c_f * n / nn, p_rate=p_rate, worst_W=worst * n / nn, share=share,
               p_ring_max=pr_max, worst_sim=worst_sim)
    no_hf = max(max(q['P_res1_W'], q['P_res2_W']) for q in R['snub'] if q['cfg'] == 'rc_only')
    per['no_hf_worst_W'] = no_hf
    return per


def spice_points(D):
    pts = [('matched_60kW', 800.0, 800.0 / D['n'], 60e3, 150e-9),
           ('v2low_94A', 700.0, 400.0, 37.5e3, 150e-9),
           ('light_6kW', 800.0, 800.0, 6e3, 150e-9)]
    out = []
    for name, v1, v2, p, td in pts:
        out.append(spice_compare(name, v1, v2, p, D, td))
    return out


def plots(R, D):
    b, ok = R['map']['best'], R['map']['ok']
    # 1. power transfer vs phase (SPS)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ph = np.linspace(0, PI / 2, 200)
    for v2 in (400.0, 600.0, 800.0 / D['n'], 900.0):
        ax.plot(np.degrees(ph), p_sps(800.0, v2, D['n'], D['L'], ph) / 1e3, label=f'V1=800 V, V2={v2:.0f} V')
    for v1, v2 in ((600.0, 700.0), (950.0, 900.0)):
        ax.plot(np.degrees(ph), p_sps(v1, v2, D['n'], D['L'], ph) / 1e3, '--', label=f'V1={v1:.0f} V, V2={v2:.0f} V')
    ax.axhline(60, color='k', lw=0.8)
    ax.set(xlabel='outer phase shift phi [deg] (360 deg = one 10 us period)', ylabel='P [kW]',
           title=f'SPS power transfer, n={D["N1"]}:{D["N2"]}, L={D["L"]*1e6:.2f} uH (calculated)')
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'power_vs_phase.png'), dpi=130)
    plt.close(fig)
    # 2. ZVS map with SPS (real Coss, adaptive dead time up to 300 ns)
    fig, axs = plt.subplots(2, 3, figsize=(13, 7.5), sharex=True, sharey=True)
    for row, sg in enumerate((1.0, -1.0)):
        for col, v1 in enumerate((600.0, 800.0, 950.0)):
            V2, P, r = zvs_sps_grid(D, v1, sg)
            z = np.where(np.isfinite(r['phi']), (~r['zvs1']).astype(int) + 2 * (~r['zvs2']).astype(int), np.nan)
            ax = axs[row, col]
            ax.set_xlim(400, 900)
            ax.contourf(V2, P / 1e3, z, levels=[-0.5, 0.5, 1.5, 2.5, 3.5],
                        colors=['#d9f2d9', '#f4b183', '#9dc3e6', '#c00000'])
            ax.axvline(v1 / D['n'], color='k', ls='--', lw=0.8)
            ax.set_title(f'V1={v1:.0f} V, {"charge (1->2)" if sg > 0 else "discharge (2->1)"}', fontsize=9)
            if row == 1:
                ax.set_xlabel('V2 [V]')
            if col == 0:
                ax.set_ylabel('|P| [kW]')
    fig.suptitle('SPS ZVS map: green = both bridges ZVS, orange = bridge 1 hard/partial, blue = bridge 2, '
                 'red = both; white = beyond SPS power limit; dashed: V1 = n*V2 (calculated)', fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'zvs_map_sps.png'), dpi=130)
    plt.close(fig)
    # 3. efficiency map (best modulation), charge direction
    sh = (MAP_V1.size, MAP_V2.size, MAP_P.size, 2)
    eta = np.where(ok, b['eta'], np.nan).reshape(sh)
    fig, axs = plt.subplots(1, MAP_V1.size, figsize=(19, 4.2), sharey=True)
    for i, v1 in enumerate(MAP_V1):
        ax = axs[i]
        cs = ax.contourf(MAP_V2, MAP_P / 1e3, eta[i, :, :, 0].T * 100, levels=[96, 97, 98, 98.5, 98.8, 99, 99.1,
                                                                             99.2, 99.3, 99.4],
                         cmap='viridis', extend='both')
        ax.contour(MAP_V2, MAP_P / 1e3, eta[i, :, :, 0].T * 100, levels=[98.8, 99.2], colors=['w', 'r'],
                   linewidths=1)
        bad = ~ok.reshape(sh)[i, :, :, 0]
        vv, pp = np.meshgrid(MAP_V2, MAP_P / 1e3, indexing='ij')
        ax.plot(vv[bad], pp[bad], 'kx', ms=5)
        ax.set(title=f'V1 = {v1:.0f} V', xlabel='V2 [V]')
        if i == 0:
            ax.set_ylabel('P out [kW]')
    fig.colorbar(cs, ax=axs, label='efficiency [%] (white 98.8, red 99.2)')
    fig.suptitle('Efficiency map, best of SPS/EPS/DPS/TPS, charge direction, coolant 50 C; x = outside Tj/SOA/'
                 'current limits (calculated, CRD calibration gap not included)', fontsize=9)
    fig.savefig(os.path.join(OUT, 'efficiency_map.png'), dpi=120, bbox_inches='tight')
    plt.close(fig)
    # 4. derating map
    V1g, V2g, pmax, why = R['derating']['charge']
    fig, ax = plt.subplots(figsize=(9, 5))
    im = ax.pcolormesh(V2g[0], V1g[:, 0], pmax / 1e3, shading='nearest', cmap='RdYlGn', vmin=0, vmax=60)
    for i in range(V1g.shape[0]):
        for j in range(V1g.shape[1]):
            val = pmax[i, j]
            ax.text(V2g[0, j], V1g[i, 0], '-' if np.isnan(val) else f'{val/1e3:.0f}', ha='center', va='center',
                    fontsize=7)
    ax.add_patch(plt.Rectangle((675, 575), 250, 350, fill=False, ec='b', lw=1.5))
    ax.set(xlabel='V2 battery [V]', ylabel='V1 DC bus [V]',
           title='Max power [kW] (charge), limits: Tj<=140 C @50 C coolant, I_pos<=90 A, SSOA 24 nH, I_port; '
                 'blue = DAB-10/11 full-power window')
    ax.title.set_fontsize(8)
    fig.colorbar(im, label='P_max [kW]')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'derating_map.png'), dpi=130)
    plt.close(fig)
    # 5. loss breakdown
    nom = R['stress']['nominal']
    keys = [('pc1', 'cond. bridge 1'), ('pc2', 'cond. bridge 2'), ('psw1', 'sw+diode b1'), ('psw2', 'sw+diode b2'),
            ('p_core', 'xfmr core'), ('p_cu', 'xfmr Cu'), ('pl_core', 'L core'), ('pl_cu', 'L Cu'),
            ('p_cap', 'caps'), ('p_bus', 'bus/interconnect')]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    bottom = np.zeros(len(nom))
    names = list(nom)
    for k, lab in keys:
        vals = np.array([nom[nm][k] for nm in names])
        ax.bar(names, vals, bottom=bottom, label=lab)
        bottom += vals
    for i, nm in enumerate(names):
        ax.text(i, bottom[i] + 5, f'{nom[nm]["eta"]*100:.2f} %', ha='center', fontsize=8)
    ax.set(ylabel='loss [W]', title='Loss breakdown at nominal points (calculated)')
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'loss_breakdown.png'), dpi=130)
    plt.close(fig)
    # 6. CRD calibration
    pm, em, rc = R['crd']
    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.plot(pm / 1e3, em * 100, 'o-', label='Wolfspeed measured (UG Fig. 57)')
    ax.plot(pm / 1e3, rc['eta'] * 100, 's--', label='this model, CRD configuration')
    ax.set(xlabel='P out [kW]', ylabel='efficiency [%]', title='CRD60DD12N-GMB 800 V -> 800 V: model vs measurement')
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'crd_calibration.png'), dpi=130)
    plt.close(fig)
    # 7. ngspice comparison
    fig, axs = plt.subplots(1, len(R['spice']), figsize=(15, 4))
    for ax, sp in zip(axs, R['spice']):
        wv = sp['wave']
        T = 1e6 / F
        mk = wv['t'] <= T
        ax.plot(wv['t'][mk], wv['il'][mk], label='ngspice i_L')
        ax.plot(wv['th'], wv['il_an'], '--', label='analytic i_L')
        ax2 = ax.twinx()
        ax2.plot(wv['t'][mk], wv['vp'][mk], color='0.6', lw=0.7, label='v_p')
        ax2.plot(wv['t'][mk], wv['vs'][mk], color='0.8', lw=0.7, label='v_s')
        ax.set(title=f'{sp["name"]}: {sp["v1"]:.0f}/{sp["v2"]:.0f} V, {sp["p_target"]/1e3:.0f} kW',
               xlabel='t [us]', ylabel='i_L [A]')
        ax.legend(fontsize=7, loc='lower left')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'spice_vs_analytic.png'), dpi=130)
    plt.close(fig)


PORT_SPEC = 'sim/out/port_design/port_spec.json'


def port_numbers():
    """The port block is the authority for precharge, fuses and contactors (gen/port.py writes port_spec.json)."""
    ps = json.load(open(PORT_SPEC))
    v = ps['variants']['135']
    return dict(R_ohm=ps['precharge']['R_ohm'], dV_ok_V=ps['precharge']['dV_ok_V'], fuse_mpn=v['fuse_mpn'],
                fuse_In_A=v['fuse_In_A'], contactor=ps['contactor']['type'])


def ov_excursion(R, D, t_resp=63e-6, trip=((972.0, 986.0, 1000.0), (924.0, 937.0, 950.0))):
    """Board-local OV trips: bus peak after the 63 us path with the full port current flowing into the bank
    (load rejection), plus the leakage energy dumped when the gates go off."""
    out = {}
    sp = R['specs']['caps']
    for k, c in ((1, sp[1]['c']), (2, sp[2]['c'])):
        slew = I_PORT_MAX[k] / c
        e_l = 0.5 * D['L'] * R['stress']['ILpk'] ** 2
        vpk = trip[k - 1][2] + slew * t_resp
        vpk += e_l / (c * vpk)
        lim = ssoa_current_limit(vpk)
        out[f'port{k}'] = dict(trip_V=list(trip[k - 1]), response_us=t_resp * 1e6, slew_V_per_us=slew * 1e-6,
                               V_peak=vpk, I_off_SSOA_limit_at_peak_A=lim,
                               cap_V_rating=sp[k]['v_rating'], cap_V_allowed_hot_spot=sp[k]['v_allow_at_hotspot'])
    out['verdict'] = ('DC: inside the module 1200 V and both capacitor banks (port 1 1300 V class, port 2 %.0f V allowed '
                      'at its hot spot). Switching: while the bus is above ~950 V the turn-off SOA allows only %.0f A '
                      '(port 1 at %.0f V) vs up to 113 A at full load, so the firmware OV (960/920 V) must set the '
                      'phase to zero within one switching period (cycle-by-cycle via CMPSS/trip zone) - the 63 us '
                      'hardware path is the backup only'
                      % (sp[2]['v_allow_at_hotspot'], out['port1']['I_off_SSOA_limit_at_peak_A'],
                         out['port1']['V_peak']))
    return out


def handoff(R, D):
    global SNB, PORT, OVX
    SNB, PORT, OVX = snubber_decision(R), port_numbers(), ov_excursion(R, D)
    R['prot'] = protection_numbers(R, D)
    R['ttrip'] = temperature_trips(R, D)
    R['clamps'] = clamp_options(R, D)
    priced = [c for c in R['clamps'] if c['feasible'] and c['cost_usd_per_module'] is not None]
    cp = min(priced, key=lambda c: c['cost_usd_per_module'])
    asian = [c for c in R['clamps'] if c['feasible'] and c['asian']]
    cp['why'] = ('cheapest priced option that keeps >= 2x I2t margin at the event duration and the SiC body diodes '
                 'below 10 %% of their pulse rating (%.0f USD per module vs %.0f USD for 2 x DD600N16K); Asian '
                 'alternate to quote at <= this price: %s' % (cp['cost_usd_per_module'], 2 * dev.CLAMP_OPTIONS[
                     'DD600N16K']['price'], ', '.join('%s x %d' % (c['part'], c['n_per_port']) for c in asian) or 'none'))
    R['clamp_pick'] = cp
    R['rclamp'] = reverse_clamp_study(R, D, cl=dict(dev.CLAMP_OPTIONS[cp['part']], n=cp['n_per_port']))
    R['calib'] = calibration_gap(R, D)
    R['claim'] = efficiency_claim(R)
    qf, ef, cn = _qe(DESIGN)
    R['e_zcs_800'] = float(e_cap_turn_on(800.0, 800.0, qf=qf, ef=ef, c_node=cn))
    rp = lambda t_: float(_rpos(DESIGN, t_, t_ - 20.0, False))
    if MAGD is not None:          # DC resistances of the magnetics design (pair in parallel, inductor from R(h) at 0)
        w_ = {w['name']: w['R_dc_90C_ohm'] for w in MAGD['xfmr']['windings']}
        r_x = (sum(v for k, v in w_.items() if k.startswith('primary')) + D['n'] ** 2 * w_['secondary']) / \
            MAGD['xfmr']['core']['n_units']
        r_l = float(np.array(MAGD['ind']['loss_model']['winding']['R_ac_per_harmonic'], float)[0, 1])
    else:
        r_x, r_l = D['rdc1'] + D['n'] ** 2 * D['rdc2'], D['rdc_l']
    R['r_loop'] = 2 * rp(125.0) + D['n'] ** 2 * 2 * rp(125.0) + r_x + r_l + a('R_ac_bus')
    sb = cost_before()
    R['snb_cost'] = (sb.get('snubber', 0.0), 'unchanged network (24 R + 24 C per module), costed-BOM estimates')
    aft = cost_after(R)
    R['cost'] = dict(before_USD=sb, after_USD={k: v[0] for k, v in aft.items()}, after_basis={k: v[1] for k, v in
                                                                                              aft.items()},
                     before_total=sum(sb.values()), after_total=sum(v[0] for v in aft.values() if v[0] is not None),
                     note='this design\'s parts only (switches, clamp, magnetics, HF caps, snubbers, cold plate, '
                          'coolant flow switch and thermostat); port parts are reported by the port engineer')
    st, sp, v9 = R['stress'], R['specs'], R['v950']
    fw = study_flux_walk(D)
    bc = blocking_cap_bank(D, st['IL'], st['ILpk'])
    oc_trip = float(np.ceil(1.15 * st['ILpk'] / 10) * 10)
    spec = {
        'meta': {'design': 'DAB-D60 power stage', 'generated_by': 'sim/dab_design.py',
                 'status': 'CALCULATED / SIMULATED - not bench-validated', 'requirements': 'REQUIREMENTS.md sec. 3',
                 'reference': 'Wolfspeed CRD60DD12N-GMB (PRD-10001 Rev.1), TI TIDA-010054 (TIDUES0F)'},
        'power_stage': {'topology': 'dual active bridge, two full bridges, one module per bridge',
                        'f_sw_Hz': F, 'rated_power_W': 60e3,
                        'port1': {'V_operating': [590, 950], 'V_full_load': [600, 900], 'I_max_A': I_PORT_MAX[1]},
                        'port2': {'V_operating': [400, 900], 'V_full_power_min': 700, 'I_max_A': I_PORT_MAX[2]},
                        'modulation': 'SPS near V1 = n*V2 and at heavy load; EPS/DPS/TPS from an offline LUT '
                                      '(a1, a2, phi vs V1, V2, P) elsewhere - see efficiency_map.csv columns a1/a2',
                        'dead_time': {'mode': 'adaptive LUT: predicted ZVS transition time + 20 ns',
                                      'min_ns': a('t_dead_min') * 1e9, 'max_ns': a('t_dead_max') * 1e9,
                                      'fallback_fixed_ns': a('t_dead_fixed') * 1e9,
                                      'note': 'set in the C2000 ePWM dead-band/HRPWM; no hardware dead-time insertion',
                                      'lut_rule': 'dead time = t_d(off)(RG_off) - t_d(on) + predicted ZVS transition '
                                                  '+ 20 ns; RG_off 0.27 ohm adds ~%.1f ns of t_d(off) (DS Fig. 28)'
                                                  % (dev.TDOFF_RG_SLOPE * a('RG_off') * 1e9)}},
        'modules': {'bridge1_port1': {'mpn': 'CBB011M12GM4T', 'rating': '1200 V 11 mOhm full bridge, Gen 4, pre-applied TIM'},
                    'bridge2_port2': {'mpn': 'CBB011M12GM4T', 'rating': '1200 V 11 mOhm full bridge, Gen 4, pre-applied TIM'},
                    'datasheet': dev.DS},
        'gate_drive': {'driver': 'TI UCC21710 (one per switch, 8 total)',
                       'bias': 'TI UCC14241-Q1 isolated, 24 V input, regulated +15/-4 V, reinforced 1414 V DC (D-016); '
                               '1.15 W per switch (gen/gdrv.py design_check)',
                       'P_bias_per_switch_W': 1.15,
                       'VGS_on_V': a('vgs_on'), 'VGS_off_V': -4.0, 'RG_on_ext_ohm': 1.0, 'RG_off_ext_ohm': a('RG_off'),
                       'RG_note': ('RG_on 1 ohm as CRD (UG Table 11). RG_off %.2f ohm (was 0 ohm in the CRD): smallest E24 '
                                   'value with the UCC21710 sink <= 10 A at VDD-VEE 19.41 V max, ROL 0.3 + RG(int) 1.4 ohm '
                                   'typ, -1 %% resistor -> %.2f A; source %.2f A. Cost: E_off +%.0f %% at 100 A/800 V '
                                   '(DS Fig. 15, +0.169 mJ/ohm at 600 V/100 A), dv/dt_off ~%.0f V/ns (Fig. 29, was ~60), '
                                   'di/dt_off unchanged (Fig. 29 flat) so overshoot/SSOA unchanged; t_d(off) +%.1f ns '
                                   '(Fig. 28). Separate on/off paths; Miller clamp used'
                                   % (a('RG_off'), 19.41 / (0.3 + 0.99 * a('RG_off') + 1.4), 19.41 / (0.7 + 1.0 + 1.4),
                                      100 * dev.EOFF_RG_SLOPE * a('RG_off') * (800 / 600) / dev.esw('eoff', 100, 800, 25),
                                      float(np.interp(a('RG_off'), *dev.DVDT_OFF_RG)),
                                      dev.TDOFF_RG_SLOPE * a('RG_off') * 1e9)),
                       'I_sink_peak_max_A': 19.41 / (0.3 + 0.99 * a('RG_off') + 1.4),
                       'I_source_peak_max_A': 19.41 / (0.7 + 1.0 + 1.4),
                       'dvdt_off_V_per_ns': float(np.interp(a('RG_off'), *dev.DVDT_OFF_RG)),
                       'Qg_C': dev.MODULE['qg'], 'P_gate_per_switch_W': dev.MODULE['qg'] * 19 * F,
                       'CMTI_min_V_per_ns': 150, 'desat': {'V_DS_trip_V': 7.0, 'blanking_ns': 300,
                                                          'response_total_ns_max': 1320,
                                                          'response_basis': 'gen/gdrv.py worst case: blanking 367 + '
                                                                            'tOCOFF 400 + soft turn-off 552 ns '
                                                                            '(typ 0.86 us)',
                                                          'soft_turn_off_A': 0.4,
                                                          't_sc_assumed_us': 2.0,
                                                          't_sc_basis': 'ASSUMPTION: the CBB011M12GM4T datasheet gives no '
                                                                        'tSC; Wolfspeed states 2.3 us for its Gen 4 '
                                                                        'automotive die EM4E120-025D100 (WP p8, '
                                                                        'conditions not stated); 2.0 us assumed at '
                                                                        '<= 950 V, VGS 15 V -> 1.32 us is 66 % of it. '
                                                                        'MUST be confirmed by Wolfspeed (open item)',
                                                          'note': 'UCC21710 OC pin 0.7 V (p9) with external DESAT network '
                                                                  '(p27); 7 V = ~340 A at Tj 150 C; short-circuit '
                                                                  'withstand of GM4 not published -> confirm by SC test'}},
        'transformer': {'turns_ratio_N1_N2': f'{D["N1"]}:{D["N2"]}', 'n': D['n'],
                        'L_sigma_target_H': D['Llk'], 'L_sigma_tol': '+/-20 % (design target, referred to N1)',
                        'L_m_H': D['Lm'], 'L_m_tol': f'+/-10 % (gapped, ~{D["gap_m"] * 1e3:.2f} mm total)',
                        'core': 'TDK PM 114/93 N95 (B65733A0000R095 ground to gap) + bobbin B65734B1000T001 - reference only',
                        'Vs_per_half_period': sp['xfmr']['vs_half'], 'B_pk_950V_T': sp['xfmr']['b_pk_950'],
                        'I1_rms_max_A': sp['xfmr']['i1_rms'], 'I2_rms_max_A': sp['xfmr']['i2_rms'],
                        'I1_pk_max_A': sp['xfmr']['i1_pk'], 'I2_pk_max_A': sp['xfmr']['i2_pk'],
                        'loss_max_W': sp['xfmr']['p_max'], 'hot_spot_max_C': 130,
                        'Rth_hotspot_to_coolant_max_K_W': a('Rth_xfmr'),
                        'isolation': {'class': 'basic (pending system decision)', 'working_voltage_basis_V_dc': 1850,
                                      'hipot_and_PD_levels': 'TBD by ECO-10 insulation coordination',
                                      'PD_max_pC': 10},
                        'cooling': 'cold plate'},
        'series_inductor': {'L_total_H': D['L'], 'L_total_tol': '+/-5 % (leakage + external, measured as a matched set)',
                            'L_external_nominal_H': D['Lext'], 'L_ext_trim_range_H': [max(D['L'] - 1.2 * D['Llk'], 0.5e-6),
                                                                                        D['L'] - 0.8 * D['Llk']],
                            'position': 'primary (port-1) side, in series with the transformer',
                            'I_rms_max_A': sp['ind']['i_rms'], 'I_pk_max_A': sp['ind']['i_pk'],
                            'I_sat_min_A': sp['ind']['i_sat'], 'loss_max_W': sp['ind']['p_max'],
                            'reference_core': f'PM 114/93 N95, {D["nl"]} turns, {D["gap_l"]*1e3:.1f} mm total gap in >=4 distributed gaps'},
        'flux_balance': {'countermeasure': 'firmware flux-balance loop on the averaged transformer current (DC-coupled '
                                           'sensor) + gapped transformer (Lm 150 uH) + hardware trip on |I_dc|',
                         'trip_I_dc_A': 5.0, 'trip_time_ms': 1.0,
                         'B_dc_with_control_T': fw['b_dc_ctrl'], 'open_loop_5ns_skew_I_dc_A': fw['cases'][1]['idc_open_loop'],
                         'blocking_cap_alternative': {'fitted': False, 'bank': f'{bc["n"]} x {bc["mpn"]} ({bc["c"]*1e6:.0f} uF)',
                                                      'C_min_TI_rule_F': bc['cb_min_ti'], 'loss_W': bc['p_esr'],
                                                      'volume_L': bc['volume_l'],
                                                      'provision': 'bolted link in the primary AC bus so a blocking-cap module can be added'}},
        'capacitor_banks': {f'port{k}': {'mpn': v['mpn'], 'manufacturer': 'KEMET', 'qty': v['n'], 'C_total_F': v['c'],
                                        'V_rating_V': v['v_rating'], 'V_max_operating_V': v['v_max'],
                                        'I_ripple_rms_bank_A': v['i_rms_bank'], 'I_ripple_rms_per_cap_A': v['i_rms_per_cap'],
                                        'I_rms_rating_per_cap_A': v['i_rms_rating'], 'ripple_pp_V': v['ripple_pp_max'],
                                        'hot_spot_est_C': v['t_hotspot'], 'V_allowed_at_hot_spot_V': v['v_allow_at_hotspot'],
                                        'datasheet': v['src']} for k, v in sp['caps'].items()},
        'dc_link_snubber_hf': {'per_bridge': '%d x (%g ohm %g W 2512 + %g nF C0G 2 kV 2220) RC across DC+/DC- at the '
                                             'module terminals (CRD UG Fig. 22 network, resistor rating from '
                                             'snubber_study.csv)' % (SNB['n'], round(SNB['r'], 2), SNB['p_rate'],
                                                                    round(SNB['c'] * 1e9, 2)),
                               'mpn': 'RFQ',
                               'note': 'port 1 caps must be >= 1.5 kV for 950 V + overshoot; CRD used 2 kV. Resistors: '
                                       'pulse-rated thick film, wide-terminal 2512 with a thermal path to the plane. '
                                       'Values keep the simulated CRD-equivalent network (R/n, n*C); nearest standard '
                                       'parts 13.7 ohm (E96) and 1.1 nF (E24) change it by < 1 %',
                               'hf_caps_per_bridge': '10 x 47 nF C0G >= 1.5 kV (1812/2220) across DC+/DC- at the module '
                                                     'terminals, next to the RC network (CRD UG Fig. 19 count; the CRD '
                                                     'parts are 1 kV - too low for our 950 V port). MPN RFQ',
                               'P_per_resistor_worst_W': SNB['worst_W'],
                               'P_per_resistor_without_hf_caps_W': SNB['no_hf_worst_W'],
                               'P_ring_max_W_per_bridge': SNB['p_ring_max'],
                               'ring_down_rule': 'each bridge commutation reverses its DC current (dI = 2 I_sw in SPS, '
                                                 'I_sw per leg in TPS); 0.5 L_bus dI^2 rings down in the terminal '
                                                 'network twice per period (P = f L_bus sum dI^2), shared by RC '
                                                 'resistors (%.0f %% worst simulated), HF-cap ESR, film-bank ESR and '
                                                 'copper' % (100 * SNB['share']),
                               'L_bus_target_nH': 'design <= 7.2 (DS Fig. 19 basis; all numbers here use 7.2); every '
                                                  'nH less cuts the ring-down power proportionally - measure by DPT',
                               'study': 'sim/out/dab_design/snubber_study.csv (ngspice, conservative switch model)'},
        'sensing': {'V1': {'range_V': [0, 1200], 'note': 'isolated amplifier (AMC3330 class as CRD)'},
                    'V2': {'range_V': [0, 1100]},
                    'I1_dc': {'range_A': [-150, 150], 'sensor': 'LEM HO 120-NP class (+/-300 A, as CRD)'},
                    'I2_dc': {'range_A': [-150, 150], 'sensor': 'LEM HO 120-NP class'},
                    'I_xfmr': {'range_A': [-250, 250], 'peak_operating_A': st['ILpk'],
                               'bandwidth_Hz_min': 1e6,
                               'note': 'DC-coupled (flux balance); LEM HOB 130-P meets range and bandwidth (+/-250 A, '
                                       '1 MHz, t_D90 <= 200 ns, HOB-P p9); HO 120-NP (CRD) at 350 kHz/2.5 us would not',
                               'sensor': 'LEM HOB 130-P',
                               'range_check': ('+/-250 A covers: peak operating %.0f A, transients in dab_control up to '
                                               '%.0f A with the split phase update (a naive phase step would add ~47 A), '
                                               'the OC trip 210 A with its 200-220 A tolerance (220/250 = 88 %% of '
                                               'range) - required range >= 1.1 x 220 = 242 A'
                                               % (st['ILpk'], st['ILpk'] + 4.3))},
                    'NTC': {'part': 'module NTC 5 kOhm B25/50 3380', 'readout': 'UCC21710 AIN/APWM (as CRD)'},
                    'coolant': 'inlet temperature + flow switch'},
        'protection': {'I_xfmr_oc_trip_A': oc_trip,
                       'I_xfmr_oc_trip_vs_V': ('min(%.0f A, I where V_bus,max + dV(I) = 1150 V, dV from DS Fig. 19) '
                                               'for the higher-voltage bridge' % oc_trip),
                       'V1_ov_fw_V': 960, 'V1_ov_hw_V': 1000, 'V2_ov_fw_V': 920, 'V2_ov_hw_V': 950,
                       'V1_uv_V': 580, 'V2_uv_V': 380, 'I1_oc_A': 115, 'I2_oc_A': 115,
                       'module_ntc_trip_C': 110, 'coolant_inlet_derate_C': 50, 'coolant_flow_trip_lpm': 6,
                       'xfmr_dc_trip_A': 5.0, 'desat': 'see gate_drive.desat',
                       'v1_above_900V': 'power limited by derating map (switching SOA)',
                       'I_xfmr_oc_trip_band_A': [200, 220],
                       'I_xfmr_oc_vdep_implementation': 'the voltage-dependent limit needs the bus voltage: CTRL CMPSS '
                                                        'on AN1 (firmware-set DAC), not the local board comparator',
                       'local_ov_board': OVX},
        'precharge': {'port1': {'C_F': sp['caps'][1]['c'], 'V_max': 950, 'E_J': 0.5 * sp['caps'][1]['c'] * 950 ** 2,
                                'R_pre_ohm': PORT['R_ohm'], 'I_inrush_max_A': 950 / PORT['R_ohm'],
                                'time_to_98pct_s': 4 * PORT['R_ohm'] * sp['caps'][1]['c'],
                                't_dV_ok_s': PORT['R_ohm'] * sp['caps'][1]['c'] * np.log(950 / PORT['dV_ok_V']),
                                'close_main_when_dV_below_V': PORT['dV_ok_V']},
                      'port2': {'C_F': sp['caps'][2]['c'], 'V_max': 900, 'E_J': 0.5 * sp['caps'][2]['c'] * 900 ** 2,
                                'R_pre_ohm': PORT['R_ohm'], 'I_inrush_max_A': 900 / PORT['R_ohm'],
                                'time_to_98pct_s': 4 * PORT['R_ohm'] * sp['caps'][2]['c'],
                                't_dV_ok_s': PORT['R_ohm'] * sp['caps'][2]['c'] * np.log(900 / PORT['dV_ok_V']),
                                'close_main_when_dV_below_V': PORT['dV_ok_V']},
                      'authority': 'sim/out/port_design/port_spec.json (gen/port.py): precharge R and relay, fuses, '
                                   'contactors, hold-closed interlock and discharge are specified there; the values '
                                   'above are copied from it for the DAB bank sizes, not chosen here',
                      'note': 'resistor + contactor per ECO-07 on both ports (port block); the DAB is enabled only after '
                              'the port block reports precharge complete and the main contactor closed'},
        'worst_case_stresses': {'module_V_peak_V': st['vds_pk'], 'module_V_peak_rule': 'V_bus + dV(I_off) from the '
                                'DS Fig. 19 turn-off SOA curve (24 nH loop) at the worst feasible point',
                                'P_ring_max_W_per_bridge': SNB['p_ring_max'], 'I_xfmr_rms_A': st['IL'], 'I_xfmr_pk_A': st['ILpk'],
                                'I_sec_rms_A': st['Is'], 'I_sec_pk_A': st['Ispk'], 'I_switch_rms_A': max(st['ipos1'], st['ipos2']),
                                'I_turn_off_max_bridge1_A': st['ioff1'], 'I_turn_off_max_bridge2_A': st['ioff2'],
                                'Tj_max_C': st['tj_max'], 'Tj_max_at': st['tj_max_at'], 'I_cap1_rms_A': st['IC1'],
                                'I_cap2_rms_A': st['IC2'], 'I1_dc_max_A': st['I1_max'], 'I2_dc_max_A': st['I2_max']},
        'thermal': {'coolant_inlet_C': a('T_coolant_in'), 'flow_lpm': a('coolant_flow'),
                    'Rth_jh_K_W': dev.MODULE['rth_jh'], 'Rth_hf_K_W': a('Rth_hf'), 'cross_heating': a('k_xheat'),
                    'flow_order': 'port-2 module first (UG p49), then port-1 module, then transformer/inductor plate'},
        'parallel_branch_interface': {
            'branches': '1..4 (+1 for N+1), DAB-09',
            'per_branch_hardware': 'own port block on both ports per sim/out/port_design/port_spec.json (DAB ports use '
                                   'its <= 135 A variant: %s %.0f A gPV fuse, %s contactors with hold-closed interlock, '
                                   '%.0f ohm precharge, active discharge), own current sensors'
                                   % (PORT['fuse_mpn'], PORT['fuse_In_A'], PORT['contactor'], PORT['R_ohm']),
            'current_sharing': 'common current/power reference over the CAN-B module bus + per-branch inner current '
                               'loop on its own I2 sensor; sharing error = sensor gain mismatch (1 %% with +/-1 %% '
                               'sensors); without per-branch loops a +/-5 %% L_k spread gives +/-%.1f %% (dab_control)'
                               % (abs(R['sens']['dP_L5']) * 100),
            'sync': 'NONE (decision): no hardware carrier-sync line; branch carriers free-running (crystal tolerance '
                    'only), so ripple at the shared buses beats slowly instead of interleaving',
            'fault': 'faulted branch: gates off (driver soft turn-off), its own contactors open; the others keep their '
                     'own reference; the system controller learns the trip over CAN-B and applies the limit below',
            'signals': ['CAN-B module bus (ECO-02): references, status, fault report',
                        'ENABLE from SYS-IO-AUX safety chain (per branch)'],
            'one_branch_fault': {
                'system_limit_rule': 'total reference <= N_alive x P_branch,max(V1, V2) from derating_charge_W; '
                                     'every branch also clamps its own reference to P_branch,max/V2 so a late or '
                                     'wrong CAN reference cannot overload the survivor',
                'can_latency_assumed_ms': 10, 'pcs_must_be_told': 'new power limit, same CAN frame',
                'simulation': 'sim/out/dab_control/metrics.json (one_branch_fault_*)'}},
        'derating_charge_W': {f'V1={int(V1)}': {f'V2={int(V2)}': (None if np.isnan(P) else float(P))
                                                for V2, P in zip(R['derating']['charge'][1][i], R['derating']['charge'][2][i])}
                              for i, V1 in enumerate(R['derating']['charge'][0][:, 0])},
        'verdict_950V': R['v950_text'] if 'v950_text' in R else None,
        'pessimistic': {'basis': 'loss model + unexplained CRD loss %.0f W + %.1f W/kW (fit of UG Fig. 57 vs our model '
                                 'of the CRD) added at every point; two bounding allocations' %
                                 (R['pess']['k0_W'], R['pess']['k1_W_per_kW']),
                        **{k: {kk: vv for kk, vv in v.items() if not kk.startswith('_')}
                           for k, v in R['pess'].items() if k in ('modules', 'magnetics')}},
        'design_levers': {'note': 'model; baseline stays 1 x CBB011M12GM4T per bridge (DAB-02); owner decision only',
                          'rows': R['levers']},
        'dc_link_reverse_clamp': {
            **{f'port{k}': {'mpn': R['clamp_pick']['part'], 'manufacturer': R['clamp_pick']['maker'],
                            'qty': R['clamp_pick']['n_per_port'],
                            'arrangement': ('%d devices in parallel on one busbar pair, cathodes to DC+, anodes to DC-, '
                                            'at the bank terminals (port side of the bridge bussing); sharing factor '
                                            '%.1f assumed' % (R['clamp_pick']['n_per_port'], a('k_clamp_share'))
                                            if R['clamp_pick']['n_per_port'] > 1 else
                                            'one arm, cathode to DC+, anode to DC-, at the bank terminals'),
                            'V_RRM_V': dev.CLAMP_OPTIONS[R['clamp_pick']['part']]['vrrm'],
                            'V_bus_peak_V': OVX[f'port{k}']['V_peak'],
                            'V_RRM_margin': R['rclamp'][f'port{k}']['V_RRM_margin'],
                            'fault': {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                                      for kk, vv in R['rclamp'][f'port{k}']['with_clamp'].items()},
                            'without_clamp': {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                                              for kk, vv in R['rclamp'][f'port{k}']['module_only'].items()},
                            'module_overstress_without_clamp_x_IDM':
                                R['rclamp'][f'port{k}']['module_overstress_factor'],
                            'leakage_mA': R['rclamp'][f'port{k}'].get('leakage_at_Vmax_mA', 'see datasheet'),
                            'datasheet': dev.CLAMP_OPTIONS[R['clamp_pick']['part']]['src']} for k in (1, 2)},
            'basis': 'bolted short at the port terminals; loop per ' + PORT_LOOP['src'] + '; bank charged to the '
                     'hardware OV trip maximum; no fuse-arc credit; body diodes per the device 3rd-quadrant curves',
            'normal_operation': 'reverse-biased by the DC bus at all times: no conduction, no recovery events, '
                                'junction capacitance in parallel with the bank - no effect on switching',
            'mounting': 'insulated mounting to basic insulation HV-PE (D-032) if on the earthed plate; fault energy '
                        'only - no heatsink needed'},
        'audit': {'file': dev.AUDIT, 'status': 'E_TAB, C_oss, SSOA, di/dt, cosmic, ferrite curves replaced by the '
                                               'audit vector-data values; V_SD now a Fig. 7 table'},
    }
    spec = spec_update(spec, R, D)
    with open(os.path.join(OUT, 'dab_spec.json'), 'w') as fh:
        json.dump(spec, fh, indent=2, default=lambda o: o.tolist() if hasattr(o, 'tolist') else str(o))
    return spec


def magnetics_requirements(R, D):
    """Electrical requirements for the magnetics design (the magnetics engineer designs to these): operating points
    over the feasible map with currents, harmonics and volt-seconds."""
    b, ok = R['map']['best'], R['map']['ok']
    idx = {'max I_L rms': int(np.argmax(np.where(ok, b['IL'], -1))),
           'max I_L peak': int(np.argmax(np.where(ok, b['ILpk'], -1))),
           'max I_s rms': int(np.argmax(np.where(ok, b['Is'], -1)))}
    pts = list(idx.items())
    for name, v1, v2, p in (('nominal 800 / 873 V 60 kW', 800.0, 800.0 / D['n'], 60e3),
                            ('800 / 800 V 60 kW', 800.0, 800.0, 60e3), ('800 / 800 V 10 kW', 800.0, 800.0, 10e3),
                            ('950 / 900 V derating corner', 950.0, 900.0, None)):
        pts.append((name, (v1, v2, p)))
    out = []
    for name, q in pts:
        if isinstance(q, int):
            v1, v2, p, a1, a2 = (float(b[k][q]) for k in ('v1', 'v2', 'p', 'a1', 'a2'))
        else:
            v1, v2, p = q
            if p is None:
                V1g, V2g, pmax, _ = R['derating']['charge']
                p = float(pmax[list(V1g[:, 0]).index(v1), list(V2g[0]).index(v2)])
            r_ = best_modulation([v1], [v2], [p], D, grid=TPS_COARSE)
            a1, a2 = float(r_['a1'][0]), float(r_['a2'][0])
        o = dict(v1=v1, v2=v2, n=D['n'], L=D['L'], Lm=D['Lm'], a1=a1, a2=a2, phi=0.0)
        o['phi'] = float(solve_phi(dict(o), p)) if (a1 or a2) else float(phi_sps(v1, v2, D['n'], D['L'], p))
        st_ = stats(o)
        h, hl, hs = _harm_rms2({k: np.atleast_1d(v) for k, v in o.items()})
        out.append(dict(point=name, V1_V=v1, V2_V=v2, P_W=p, a1_deg=np.degrees(a1), a2_deg=np.degrees(a2),
                        phi_deg=np.degrees(o['phi']), I_L_rms_A=float(st_['IL']), I_L_pk_A=float(st_['ILpk']),
                        I_sec_rms_A_secondary_side=float(st_['Is']), I_m_pk_A=float(st_['Impk']),
                        I_L_harmonics_rms_A={int(k): float(np.sqrt(x)) for k, x in zip(h, hl[0])},
                        winding_Vs_half_period_primary_referred=float(D['n'] * v2 * (PI - a2) / W),
                        f_sw_Hz=F))
    return out


def spec_update(spec, R, D):
    """dab_spec.json changes of the 2026-10-04 round: same keys (gen/dab60.py and the control card read them), values
    for the chosen devices, and the new keys device_primary / device_alternate / cost_usd and the review answers."""
    o, d, pk = DESIGN, DESIGN['d'], R['pick']
    prim, alt = o['parts'][0], o['parts'][1]
    prot, tt, hf, cl = R['prot'], R['ttrip'], R['hf'], R['clamp_pick']
    st = R['stress']
    dev_txt = lambda p_: {'mpn': p_['mpn'], 'maker': p_['maker'], 'package': p_['package'], 'datasheet': p_['src'],
                          'pages': p_['pages'], 'rds_on_25C_typ_max_mohm': [p_['rds'][0] * 1e3, p_['rds_max_25'] * 1e3],
                          'rth_jc_K_W': p_['rth_jc'], 'qualification': p_['qualification'],
                          'price': (dev.PRICES[p_['mpn']] if dev.PRICES.get(p_['mpn']) else 'RFQ - no public price'),
                          'gate_rails_V': list(DESIGN_RAIL), 'tsc_assumed_us': p_['tsc'] * 1e6,
                          'open_items': p_['open_items']}
    spec['device_primary'] = dev_txt(prim)
    spec['device_alternate'] = dev_txt(alt)
    spec['device_design_basis'] = ('per-parameter worst case of the primary and alternate maker (R_DS(on), E_on/E_off at '
                                   'the design R_G, C_oss, V_SD, R_th, Q_g maximum; V_th, lead rating, pulse rating '
                                   'minimum) - every number in this file is for that composite')
    spec['power_stage']['topology'] = ('dual active bridge, two full bridges of %d paralleled TO-247-4 SiC MOSFETs per '
                                       'switch position (%d per bridge) on ceramic insulators on the earthed cold plate'
                                       % (o['npar'], 4 * o['npar']))
    for b in (1, 2):
        spec['modules']['bridge%d_port%d' % (b, b)] = {
            'mpn': '%d x %s per switch, %d per bridge (alternate %s)' % (o['npar'], prim['mpn'], 4 * o['npar'],
                                                                         alt['mpn']),
            'rating': '1200 V %.1f mOhm typ discretes, TO-247-4 Kelvin source' % (prim['rds'][0] * 1e3)}
    spec['modules']['datasheet'] = [prim['src'], alt['src']]
    spec['modules']['insulator'] = {k: dev.INSULATOR[k] for k in ('mpn', 'maker', 'src', 'grease', 'overhang_mm',
                                                                   'rth_stack', 'c_tab')}
    spec['modules']['insulator']['requirement'] = INSULATION['port1_winding_to_core_PE']
    spec['modules']['paralleling'] = {'R_DS_spread_dr': o['dr'], 'current_factor_hottest': share(o)[0],
                                      'conduction_loss_factor': share(o)[1], 'switching_loss_factor': share(o)[2],
                                      'rule': 'hottest device RMS <= %.0f %% of its lead rating (%.0f A)'
                                              % (a('I_term_frac') * 100, d['i_rms_lead']),
                                      'layout': 'each device its own R_G,on / R_G,off and Kelvin source return; '
                                                'Miller clamp per device (PNP follower as D-028); matched V_th '
                                                'bin recommended (sharing factor assumes none)'}
    dt = spec['power_stage']['dead_time']
    dt.update({'mode': 'hardware shoot-through guard (fixed window, worst case used in every loss figure) + '
                       'firmware LUT on top (predicted ZVS transition + 20 ns, 50-300 ns)',
               'hw_guard_ns': [o['hw_dt'][0] * 1e9, o['hw_dt'][1] * 1e9],
               'hw_guard_basis': 'min = t_d(off) %.0f ns + t_f %.0f ns (hot, design R_G,off) - t_d(on) %.0f ns + 20 ns; '
                                 'logic-side delay element +/-%.0f %% plus the NSI6651 delay mismatch %.0f ns on both '
                                 'ends' % (o['td_off'] * 1e9, o['tf'] * 1e9, o['td_on'] * 1e9,
                                           a('t_dead_hw_tol') * 100, (dev.NSI6651['tpd'][2] - dev.NSI6651['tpd'][0]) * 1e9),
               'note': 'DR-02 decision: the hardware guard owns the minimum, firmware owns the rest; the drawn '
                       'GDRV stretcher (104-354 ns) must be replaced by a %.0f-%.0f ns gate-level window'
                       % (o['hw_dt'][0] * 1e9, o['hw_dt'][1] * 1e9),
               'study': R['dead_time']})
    ours = R['dead_time']['LUT + decided hardware guard']
    stc = R['dead_time']['fixed 400 ns (ST STSW-DABBIDIR)']
    drawn = R['dead_time']['LUT + guard as drawn on DAB60 rev B (104-354 ns)']
    dt['study_W'] = {'st_400ns_minus_ours_800_60k': stc['loss_800_60k_W'] - ours['loss_800_60k_W'],
                     'st_400ns_diode_800_60k': stc['p_diode_800_60k_W'],
                     'drawn_minus_ours_800_60k': drawn['loss_800_60k_W'] - ours['loss_800_60k_W']}
    g = spec['gate_drive']
    v = (DESIGN_RAIL[0] - DESIGN_RAIL[1]) * 1.013
    g.update({'driver': 'NOVOSENSE NSI6651ASC-DSWR industrial grade (one per switch position, 8 total; D-035). The '
                        '-Q1 grade fails the DESAT and dead-time budgets below (I_STO 100 mA min, t_pd 40-130 ns)',
              'bias': 'TI UCC14241-Q1 isolated, 24 V input, regulated %+.0f/%+.1f V (VDD-VEE %.1f V, COM-VEE %.1f V, '
                      'both in its 18-25 V / >= 2.5 V range), reinforced 1414 V DC (D-016)' % (
                          DESIGN_RAIL[0], DESIGN_RAIL[1], DESIGN_RAIL[0] - DESIGN_RAIL[1], -DESIGN_RAIL[1]),
              'VGS_on_V': DESIGN_RAIL[0], 'VGS_off_V': DESIGN_RAIL[1],
              'RG_on_ext_ohm': o['rg_on'], 'RG_off_ext_ohm': o['rg_off'], 'RG_per': 'per device (npar = %d)' % o['npar'],
              'RG_note': ('per device: R_G,off %.2g ohm = smallest E24 value keeping the NSI6651 sink <= 10 A at VDD-VEE '
                          '%.2f V, ROL 0.3 ohm, R_G,int %.1f ohm (lowest maker) and -1 %% resistor; R_G,on %.2g ohm '
                          '(hard-switched edges only; ZVS edges are not affected)' % (o['rg_off'], v, d['rg_int'],
                                                                                       o['rg_on'])),
              'I_sink_peak_max_A': v / (dev.NSI6651['rol'] + (0.99 * o['rg_off'] + d['rg_int']) / o['npar']),
              'I_source_peak_max_A': v / (dev.NSI6651['roh'] + (0.99 * o['rg_on'] + d['rg_int']) / o['npar']),
              'dvdt_off_V_per_ns': float(0.8 * 800.0 / (o['tf'] * 1e9) * 0.5),
              'Qg_C': o['npar'] * d['qg'], 'P_gate_per_switch_W': o['npar'] * d['qg'] * (DESIGN_RAIL[0] - DESIGN_RAIL[1]) * F,
              'P_bias_per_switch_W': o['npar'] * d['qg'] * (DESIGN_RAIL[0] - DESIGN_RAIL[1]) * F + 0.35})
    g['desat'].update({'V_DS_trip_V': prot['desat']['v_ds_trip_V'][1], 'blanking_ns': prot['desat']['blanking_ns_max'],
                       'response_total_ns_max': prot['desat']['to_plateau_end_ns_max'],
                       'response_basis': 'NSI6651 industrial: LEB 200 ns (+25 %%) + C_pin %.0f pF x 9.8 V / 430 uA + '
                                         'deglitch 265 + DESAT->OUT 300 ns + soft turn-off to the plateau at 0.25 A'
                                         % (a('c_desat') * 1e12),
                       'soft_turn_off_A': dev.NSI6651['i_sto'][0], 't_sc_assumed_us': min(
                           prot['desat']['tsc_assumed_us_1000V_150C'].values()),
                       't_sc_basis': ('ASSUMPTION per device (no Chinese maker publishes one): %s us at <= 800 V / 25 C '
                                      'start, x 800/1000 x %.1f for 1000 V / 150 C; ROHM SCT4018KR, the only Asian part '
                                      'with a rating, states 4.0 us typ at 18 V / 800 V (p3)' % (
                                          prot['desat']['tsc_assumed_us_800V_25C'], a('tsc_hot_factor'))),
                       'margin_vs_assumed_tsc': prot['desat']['margin'],
                       'residual_risk': 'withstand unknown for both makers; a short-circuit test on samples at 950 V / '
                                        '150 C is a release condition, and the makers must supply t_SC'})
    pr = spec['protection']
    pr.update({'I_xfmr_oc_trip_A': prot['oc_trip_A'], 'I_xfmr_oc_trip_band_A': prot['oc_band_A'],
               'trip_turn_off': {k: v_ for k, v_ in prot.items() if k not in ('desat',)},
               'I_xfmr_oc_vdep_implementation': ('NOT on the CTRL CMPSS: AN1 passes the AMC3330 (1.6-2.6 us) and acts '
                                                 'after the local trip. The local hardware window is the only fast '
                                                 'OC protection; with soft turn-off its level need not depend on the '
                                                 'bus voltage (overshoot %.0f V)' % prot['overshoot_soft_V']),
               'module_ntc_trip_C': None, 'trip_temperatures': tt,
               'bridge_cross_trip': {'required_us': 1.0,
                                     'rule': 'any trip, RDY/UVLO loss, missing +24 V or an unplugged/unpowered bridge '
                                             'board stops BOTH bridges in hardware within 1 us (common TRIP_N/LATCH '
                                             'between the two bridge logic blocks, pull-down to "fault" on the '
                                             'receiving side); a stopped bridge must never face a switching one for '
                                             'more than one edge',
                                     'basis': 'with one bridge stopped the other drives L against the diode bridge: '
                                              'current rises at up to %.0f A/us (1000 V - n x 400 V across 6.5 uH)'
                                              % ((1000.0 - D['n'] * 400.0) / D['L'] * 1e-6)}})
    tr = spec['transformer']
    for k in ('core', 'Rth_hotspot_to_coolant_max_K_W', 'B_pk_950V_T'):
        tr.pop(k, None)
    tr.update({'L_m_tol': '+/-10 %', 'L_sigma_target_H': D['Llk'],
               'L_split_note': ('only the total series inductance (leakage + external) %.2f uH +/-5 %% is an electrical '
                                'requirement; the split %.2f + %.2f uH is the magnetics design\'s (%s)'
                                % (D['L'] * 1e6, D['Llk'] * 1e6, D['Lext'] * 1e6, MAG_SRC())),
               'construction': 'designed by the magnetics engineer: sim/out/magnetics/design_dab_transformer.json '
                               '(the PM 114/93 reference construction and the 0.40 K/W requirement are withdrawn, '
                               'MG-01..04, MG-14)',
               'loss_max_W': R['stress']['p_xfmr'],
               'loss_basis': MAG_SRC() + ' - loss model read by sim/dab_design.py (Steinmetz + iGSE, R(h) at 90 C)',
               'hot_spot_max_C': a('T_xfmr_hs_max'),
               'Rth_needed_note': 'the construction must keep the hot spot <= %.0f C at 50 C coolant inlet with its own '
                                  'loss at the worst map point' % a('T_xfmr_hs_max'),
               'electrical_requirements': magnetics_requirements(R, D)})
    tr['isolation'] = {'port1_to_port2': INSULATION['port1_to_port2'],
                       'primary_winding_to_core': INSULATION['port1_winding_to_core_PE'],
                       'secondary_winding_to_core': INSULATION['port2_winding_to_core_PE'],
                       'winding_to_NTC': INSULATION['winding_to_NTC_PELV'],
                       'class': 'basic port 1 - port 2 (simple separation) at 1850 V DC; basic each winding to the '
                                'earthed core / cold plate', 'PD_max_pC': 10}
    si = spec['series_inductor']
    si.pop('reference_core', None)
    si.update({'L_external_nominal_H': D['Lext'],
               'L_ext_trim_range_H': (MAGD['ind']['electrical']['L_trim_range_H'] if MAGD else
                                      [D['L'] - 1.2 * D['Llk'], D['L'] - 0.8 * D['Llk']]),
               'construction': 'designed by the magnetics engineer: sim/out/magnetics/design_dab_series_inductor.json',
               'loss_max_W': R['stress']['p_ind'],
               'loss_basis': MAG_SRC()})
    si['isolation'] = dict(INSULATION['port1_winding_to_core_PE'], dvdt_V_per_ns=57,
                           note='winding at the bridge-1 switch node, core and bracket on the earthed cold plate')
    si['I_sat_min_A'] = max(si['I_sat_min_A'], float(np.ceil(1.1 * prot['I_peak_fault_A'] / 5) * 5))
    si['I_sat_basis'] = '1.1 x the trip fault peak (DR-03, soft turn-off path) at 100 C'
    sn = spec['dc_link_snubber_hf']
    sn['hf_caps_per_bridge'] = ('%d x 47 nF C0G >= 1.2 kV (3640) KEMET KC-LINK CKC33C473KEGACAUTO across DC+/DC- at '
                                'the switch positions, spread over the two legs' % hf['n'])
    sn['hf_caps'] = hf
    sn['mpn'] = 'resistor/RC capacitor RFQ; HF caps %s (KEMET/YAGEO)' % dev.HF_CAP['mpn']
    rc = spec['dc_link_reverse_clamp']
    for k in (1, 2):
        rc[f'port{k}'].update({'mpn': cl['part'], 'manufacturer': cl['maker'], 'qty': cl['n_per_port']})
    rc['options'] = R['clamps']
    rc['choice'] = '%s x %d per port (%s): %s' % (cl['part'], cl['n_per_port'], cl['maker'], cl['why'])
    th = spec['thermal']
    th.update({'Rth_jc_K_W': d['rth_jc'], 'Rth_insulator_stack_K_W': o['rth_ins'], 'Rth_cp_per_device_K_W': o['rth_cp'],
               'cross_heating': o['k_x'], 'Rth_jh_K_W': None,
               'note': 'discretes: R_th j-c + AlN/grease stack + cold-plate footprint per device; hottest device '
                       'carries the sharing factors'})
    ws = spec['worst_case_stresses']
    ws['module_V_peak_rule'] = ('V_bus + L_loop x 0.8 I_off / t_f at the worst feasible point (L_loop %.0f nH ASSUMED for '
                                'the discrete layout, t_f %.0f ns at the design R_G,off, hot)' % (o['l_loop'] * 1e9,
                                                                                                    o['tf'] * 1e9))
    ws['I_device_rms_hottest_A'] = max(st['idev1'], st['idev2'])
    hs = {q['point']: q for q in R['snub'] if q['cfg'] == 'rc_hf'}
    spec['power_stage']['hard_switching'] = {
        'die_peak_V_ngspice': {k: max(q['V_die1_pk'], q['V_die2_pk']) for k, q in hs.items()},
        'rule': 'no hard turn-on at >= 800 V: TPS LUT keeps ZVS; firmware blocks SPS below the LUT\'s ZVS boundary and '
                'checks the LUT integrity at start; the decks overstate the overshoot (~1.5x on the GM4) - DPT on the '
                'real layout with R_G,on is a release condition'}
    spec['device_options'] = {'rows': R['options'], 'choice': pk['option'], 'rule': 'cheapest option meeting Tj and '
                              'current rules with primary AND alternate Asian maker, efficiency not worse than the '
                              'incumbent (window mean and 800/800 V 60 kW), designed for the worse maker',
                              'csv': 'sim/out/dab_design/device_options.csv'}
    spec['cost_usd'] = R['cost']
    spec['efficiency_claim'] = R['claim']
    spec['calibration_gap'] = R['calib']
    spec['control_plant'] = {'R_loop_primary_ohm': R['r_loop'], 'R_loop_basis': 'ohm primary-referred: 2 x R_pos(125 C) '
                             'bridge 1 + n^2 x 2 x R_pos(125 C) bridge 2 + transformer + inductor R_dc + AC bus '
                             '(dab_design.py)'}
    spec['st_crosscheck_inputs'] = {'E_turn_on_zero_current_800V_J': R['e_zcs_800']}
    spec['meta']['round'] = ('2026-10-04 Asian device re-selection (SRC-1..4) and DAB60 review findings DR-01..07, '
                             'IC-08, cost re-opening')
    return spec


def _fmt_pt(t):
    return f'{t[0]:.0f} V/{t[1]:.0f} V/{t[2]/1e3:+.0f} kW'


def _f(x, fmt='%.1f', none='RFQ'):
    return none if x is None or (isinstance(x, float) and np.isnan(x)) else fmt % x


def report_devices(R, D, spec, w):
    """Section 0: the SRC-1..4 device study, the decision and how each DAB60 review finding was resolved."""
    pick, rows = R['pick'], R['options']
    w('## 0. Asian device re-selection and review findings (2026-10-04, REQUIREMENTS.md section 7, D-031)')
    w('')
    w('Decision rule (coordinator): the cheapest option that meets the junction-temperature and current rules with a '
      'primary AND an alternate Asian maker and whose efficiency is not worse than today\'s; designed for the worse of '
      'the two makers (options tagged W are the per-parameter worst case of both makers). Same magnetics (D-014), same '
      'cooling (50 C inlet, 9 L/min), same rules (Tj <= %.0f C, terminal/lead RMS <= %.0f %% of rating, V_DS peak '
      '<= 1200 V), dead time = each option\'s own hardware guard window + the firmware LUT. All numbers calculated.'
      % (a('Tj_max_design'), a('I_term_frac') * 100))
    w('')
    w('| option | makers | dev/bridge | eta 800/800 60 kW | loss W | eta 30 kW | peak eta (light) | eta mean window '
      '| Tj max C (60 kW) | full-power pts ok | rules | eta not worse | dead time hw ns | ZVS from (800/800) kW | '
      'USD/bridge |')
    w('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for x in rows:
        w(f'| {x["option"]} | {x["makers"]} | {x["devices_per_bridge"]} | {x["eta_800_800_60kW"]*100:.2f} % | '
          f'{x["loss_800_800_60kW_W"]:.0f} | {x["eta_800_800_30kW"]*100:.2f} % | {x["eta_peak_light"]*100:.2f} % | '
          f'{x["eta_mean_window"]*100:.2f} % | {x["Tj_max_full_C"]:.0f} | {x["full_power_points_ok"]}/'
          f'{x["full_power_points"]} | {"yes" if x["rules_ok"] else "NO"} | {"yes" if x["eta_not_worse"] else "no"} | '
          f'{x["dead_time_hw_ns"][0]}-{x["dead_time_hw_ns"][1]} | {_f(x["zvs_min_power_800_800_W"] / 1e3, "%.1f", "-")}'
          f' | {_f(x["cost_usd_per_bridge"], "%.0f")} |')
    w('')
    w(f'**Choice: {pick["option"]} - {DESIGN["name"]}** ({pick["makers"]}); decision rule met: '
      f'{"yes" if pick["decision_rule_met"] else "NO - see below"}; cost basis: {pick["cost_basis"]}. '
      f'Gate rails {DESIGN_RAIL[0]:+.0f} / {DESIGN_RAIL[1]:+.1f} V ({dev.DESIGN_RAIL_NOTE}); R_G,on '
      f'{DESIGN.get("rg_on", 0):g} ohm and R_G,off {DESIGN.get("rg_off", 0):g} ohm per device (NSI6651 sink <= 10 A).')
    w('')
    up = next((x for x in rows if x['option'] == pick['option'].replace('2', '3')), None)
    w('Reading the table: the incumbent row is today\'s module re-evaluated with the same dead-time treatment, the '
      'same magnetics and the DR-01 AC-pin rule; it fails Tj and its 100 A AC pins. Options W are the design basis: every '
      'point is the worse of the two makers, with one gate design and one dead-time guard for both. "rules" = the '
      'decision rule (Tj and current); the turn-off SOA (V_DS peak <= 1200 V with the overshoot) is applied as a power '
      'limit in the derating map, as D-014 did for the incumbent above 900 V. Modules cannot be ranked on cost (no '
      'public price).')
    if up is not None and up is not pick and pick['full_power_points_ok'] < pick['full_power_points']:
        w(f'Consequence of the choice: {pick["option"]} keeps full power at {pick["full_power_points_ok"]} of '
          f'{pick["full_power_points"]} full-power window points (the rest are SOA-limited, derating map); {up["option"]} '
          f'keeps all {up["full_power_points_ok"]} for {_f(up["cost_usd_per_bridge"] and 2 * (up["cost_usd_per_bridge"] - pick["cost_usd_per_bridge"]), "+%.0f")} '
          f'USD per module and runs cooler (Tj {up["Tj_max_full_C"]:.0f} C) - owner\'s choice if the full window is required.')
    w('')
    prot, tt, hf, cl, cal, cost = R['prot'], R['ttrip'], R['hf'], R['clamp_pick'], R['claim'], R['cost']
    dt = R['dead_time']
    w('### 0.1 Review findings DR-01..07 and IC-08 - how each is resolved (calculated)')
    w('')
    w('| finding | resolution | numbers |')
    w('|---|---|---|')
    w(f'| DR-01 AC terminal current | discretes: no shared AC pin; each device lead carries its own share; rule = '
      f'hottest device <= {a("I_term_frac")*100:.0f} % of its lead rating (sharing factor {share(DESIGN)[0]:.2f} from the '
      f'R_DS(on) spread); the AC-node copper must carry I_L | I_L,rms max {R["stress"]["IL"]:.1f} A (AC node, '
      f'layout); hottest device {max(R["stress"]["idev1"], R["stress"]["idev2"]):.1f} A vs '
      f'{DESIGN["d"]["i_rms_lead"]:.0f} A lead rating; incumbent: 18+ map points above its 100 A pins |')
    lut = dt['firmware LUT only (50-300 ns, no guard)']
    ours = dt['LUT + decided hardware guard']
    drawn = dt['LUT + guard as drawn on DAB60 rev B (104-354 ns)']
    w(f'| DR-02 dead time | hardware guard owns the minimum, firmware LUT the rest; guard window at the gates '
      f'{DESIGN["hw_dt"][0]*1e9:.0f}-{DESIGN["hw_dt"][1]*1e9:.0f} ns (worst case used in every loss figure) | '
      f'800/800 V 60 kW loss {ours["loss_800_60k_W"]:.0f} W (body diode {ours["p_diode_800_60k_W"]:.0f} W); '
      f'firmware-only {lut["loss_800_60k_W"]:.0f} W; guard as drawn (104-354 ns) {drawn["loss_800_60k_W"]:.0f} W '
      f'(+{drawn["loss_800_60k_W"]-ours["loss_800_60k_W"]:.0f} W) |')
    w(f'| DR-03 trip turn-off | {prot["mechanism"]} | response {prot["t_response_us"]:.2f} us; fault peak '
      f'{prot["I_peak_fault_A"]:.0f} A (PWM-bounded {prot["I_peak_pwm_bounded_A"]:.0f} A), {prot["I_per_device_peak_A"]:.0f} A '
      f'per device vs I_DM {prot["I_DM_device_A"]:.0f} A; overshoot {prot["overshoot_soft_V"]:.0f} V -> '
      f'{prot["V_peak_soft_V"]:.0f} V; as drawn (fast turn-off after 0.78 us): {prot["hard_path_as_drawn"]["I_peak_A"]:.0f} A, '
      f'{prot["hard_path_as_drawn"]["V_peak_V"]:.0f} V; {prot["inductor_note"]} |')
    ds = prot['desat']
    tsc0 = ', '.join(f'{k} {v*1e6:.1f}' for k, v in ds['tsc_assumed_us_800V_25C'].items())
    tsc1 = min(ds['tsc_assumed_us_1000V_150C'].values())
    w(f'| DR-05 short circuit | t_SC ASSUMED per device (no maker publishes one): {tsc0} us at 800 V / 25 C start, '
      f'x 0.8 x {a("tsc_hot_factor")} -> {tsc1:.2f} us at 1000 V / 150 C. Required: gate falling within 50 % of that, '
      f'i.e. a withstand of >= 2 x {ds["to_gate_falling_ns_max"]/1e3:.2f} = {2*ds["to_gate_falling_ns_max"]/1e3:.1f} us at '
      f'1000 V / 150 C to be confirmed by the makers or a test | NSI6651 industrial worst case: blanking '
      f'{ds["blanking_ns_max"]:.0f} ns, gate falling after {ds["to_gate_falling_ns_max"]:.0f} ns, Miller plateau left after '
      f'{ds["to_plateau_end_ns_max"]:.0f} ns: {tsc1*1e3/ds["to_gate_falling_ns_max"]:.2f}x the assumed hot withstand - NOT '
      f'enough; residual risk: a leg shoot-through at 1000 V / 150 C may destroy the devices (fuse clears) |')
    w(f'| DR-06 over-temperature | plate NTC per bridge between the hottest positions into the hardware trip chain at '
      f'{tt["plate_trip_C"]:.0f} C (normal worst plate + 20 K; Tj-based ceiling {tt["plate_trip_Tj_based_ceiling_C"]:.0f} C '
      f'from Tj limit {tt["tj_limit_for_trip_C"]:.0f} C minus the {tt["offset_plate_to_tj_pessimistic_K"]:.0f} K offset at the '
      f'pessimistic loss), firmware derate from {tt["plate_firmware_derate_from_C"]:.0f} C, fails hot; transformer and '
      f'inductor NTC trip {tt["transformer"]["hardware_trip_C"]:.0f} C | {tt["limitation"]} |')
    w('| DR-04 bridge link | both bridges stop in hardware within 1 us on any trip of either; an unpowered or missing '
      'board reads as a trip | current rise with one bridge stopped up to %.0f A/us |'
      % ((1000.0 - D['n'] * 400.0) / D['L'] * 1e-6))
    w(f'| DR-07 HF capacitors | {hf["n"]} x {dev.HF_CAP["mpn"]} (47 nF C0G 1200 V, KEMET/YAGEO) per bridge, sized by '
      f'the simulated current; the CRD film part (LDEQ) is obsolete and rated ~2.5 A rms | {hf["I_rms_per_cap_max_A"]:.1f} '
      f'A rms per part (rule {hf["I_rms_rule_A"]:.1f} A), {hf["I_pk_per_cap_max_A"]:.0f} A peak, '
      f'{hf["P_per_cap_max_W"]:.2f} W per part (ESR assumed) |')
    hs = {q['point']: q for q in R['snub'] if q['cfg'] == 'rc_hf'}
    w(f'| die overshoot (ngspice, harsh channel model) | full power at 950 V: {hs["v1max"]["V_die1_pk"]:.0f} V; highest '
      f'turn-off current point {hs["ioffmax"]["V1"]:.0f}/{hs["ioffmax"]["V2"]:.0f} V: {hs["ioffmax"]["V_die1_pk"]:.0f} V; SPS '
      f'HARD turn-on at light load (800/900 V 10 kW, 950/850 V 5 kW): {hs["hard_b1"]["V_die1_pk"]:.0f} / '
      f'{hs["hard_b2"]["V_die2_pk"]:.0f} V | the decks overstate the GM4 datasheet overshoot by ~1.5x; even so hard '
      f'turn-on at >= 800 V must not happen in operation: the TPS LUT keeps ZVS there and firmware must block SPS at '
      f'light load (LUT integrity check); DPT with the real R_G,on and layout is a release condition |')
    w('| IC-08 magnetics insulation | levels written into transformer.isolation and series_inductor.isolation of '
      'dab_spec.json | port 1 - port 2 basic 1850 V DC, PD <= 10 pC at 3149 Vpk, 3323 V rms, 6 kV; windings to core '
      'basic, PD at 1799 / 1724 Vpk, 2200 V rms |')
    w('')
    w('### 0.2 Reverse clamp re-opened on cost')
    w('')
    w('| part | maker | n per port | I2t margin at the event | V clamp V | SiC body-diode peak per device A '
      '| USD per module | price source |')
    w('|---|---|---|---|---|---|---|---|')
    for c in R['clamps']:
        if c['feasible']:
            w(f'| {c["part"]} | {c["maker"]} | {c["n_per_port"]} | {c["I2t_margin_at_event_min"]:.1f} | '
              f'{c["V_clamp_pk_V"]:.1f} | {c["body_diode_pk_per_device_A"]:.0f} | {_f(c["cost_usd_per_module"], "%.0f")} '
              f'| {c["price_src"]} |')
        else:
            w(f'| {c["part"]} | {c["maker"]} | no count <= 40 meets both rules | - | - | - | - | {c["price_src"]} |')
    w('')
    w(f'Choice: {cl["part"]} x {cl["n_per_port"]} per port - {cl["why"]}.')
    w('')
    w('### 0.3 Cost of this design\'s parts (USD per module; port parts are the port engineer\'s)')
    w('')
    w('| group | before (costed BOM) | after | basis |')
    w('|---|---|---|---|')
    for k in cost['after_USD']:
        w(f'| {k} | {cost["before_USD"].get(k, 0):.0f} | {_f(cost["after_USD"][k], "%.0f")} | {cost["after_basis"][k]} |')
    w(f'| **total** | **{cost["before_total"]:.0f}** | **{cost["after_total"]:.0f}** | |')
    w('')
    w('### 0.4 Efficiency against DAB-04 (calibrated)')
    w('')
    w(cal['statement'])
    w('')
    cg = R['calib']
    w('What the model lacked against Wolfspeed\'s CRD measurement (800/800 V, 10.8-22.7 kW, UG Fig. 57): the gap was '
      '%.0f-%.0f W. Already modelled before this round: dead-time body-diode conduction %.0f-%.0f W, HF-capacitor/snubber '
      'ring-down %.0f-%.0f W and the transformer copper loss at the measured 78 mOhm for the fundamental %.0f-%.0f W '
      '(so MG-01 does not by itself explain the CRD gap). Added this round: the harmonics of that copper loss, %.0f-%.0f W '
      '(our F_r(h) law applied to the measured R_w - an estimate): %.0f %% of the gap. Not modelled: C_oss hysteresis '
      '~%.0f W (ASSUMPTION %.0f %% of E_oss per cycle), gate drive %.0f W and sensing %.1f W if Wolfspeed counted its 12 V '
      'input (with these %.0f %%). Residual %.0f-%.0f W is carried as a constant %.0f W in the calibrated claim.' % (
          min(cg['gap_before_this_round_W']), max(cg['gap_before_this_round_W']),
          min(cg['dead_time_body_diode_W']), max(cg['dead_time_body_diode_W']), min(cg['ring_down_hf_caps_snubbers_W']),
          max(cg['ring_down_hf_caps_snubbers_W']), min(cg['xfmr_copper_fundamental_W']),
          max(cg['xfmr_copper_fundamental_W']), min(cg['xfmr_copper_harmonics_W']), max(cg['xfmr_copper_harmonics_W']),
          cg['share_explained_by_harmonics'] * 100, cg['coss_hysteresis_W'][0], a('k_coss_hyst') * 100,
          cg['gate_drive_if_counted_W'][0], cg['sensing_if_counted_W'][0], cg['share_with_hysteresis_and_aux'] * 100,
          min(cg['residual_gap_W']), max(cg['residual_gap_W']), R['sens']['crd_gap_fit'][0]))
    w('')


def report(R, D, spec):
    st, sp, v9, sens = R['stress'], R['specs'], R['v950'], R['sens']
    pm, em, rc = R['crd']
    tsel, bc = R['tsel'], R['best_cont']
    nom = st['nominal']
    k_nom = list(nom)
    L = []
    w = L.append
    w('# DAB-D60 power-stage design report')
    w('')
    w('Generated by `sim/dab_design.py` (do not edit by hand). Everything here is CALCULATED or SIMULATED; nothing is '
      'bench-validated. Sources: `sim/dab_devices.py` (every datasheet number with file + page/figure).')
    w('')
    report_devices(R, D, spec, w)
    w('## 1. Summary')
    w('')
    w(f'* **Turns ratio n = N1:N2 = {D["N1"]}:{D["N2"]} = {D["n"]:.4f}**, **total series inductance L = {D["L"]*1e6:.2f} uH** '
      f'(transformer leakage target {D["Llk"]*1e6:.2f} uH + external {D["Lext"]*1e6:.2f} uH), **L_m = {D["Lm"]*1e6:.0f} uH** (gapped). '
      f'Frozen by DECISIONS.md D-014. Re-check with the audited device data: the frozen choice costs '
      f'{R["frozen"]["J"]-R["frozen"]["J_opt"]:+.1f} W mean loss ({(R["frozen"]["J"]/R["frozen"]["J_opt"]-1)*100:+.2f} %) '
      f'against the current sweep optimum {R["frozen"]["opt"][0]}:{R["frozen"]["opt"][1]}, {R["frozen"]["opt"][2]*1e6:.1f} uH '
      f'(continuous optimum n = {bc["n"]:.2f}, L = {bc["L"]*1e6:.1f} uH), so D-014 stands.')
    w('* **Refinement round (2026-10-04):** device curves replaced by the independent audit\'s PDF vector-data values '
      '(E_on at 600 V was up to 21 % high, E_rr at 800 V up to 13 % high, C_oss ~3 % high), V_SD now a Fig. 7 table '
      '(the linear model was up to 11 % low at 25-50 A), switching SOA now the Fig. 19 polyline, transformer hot spot '
      'added as a derating limit (withdrawn 2026-10-04, MG-02). Pessimistic case, design levers and the '
      'one-branch-fault simulation added.')
    w(f'* **Model peak efficiency {st["eta_peak"]*100:.2f} %** at {_fmt_pt(st["eta_peak_at"])} (target 99.2 %). '
      f'Inside the full-power window, {len(st["below_988_win"])} of {st["n_20_win"]} feasible points at >= 20 kW fall '
      f'below 98.8 % (minimum {st["eta_min_20_win"]*100:.2f} %), listed in section 5.')
    w(f'* **Calibration warning:** the same model run with the CRD60DD12N-GMB configuration predicts '
      f'{rc["eta"][0]*100:.2f}-{rc["eta"][-1]*100:.2f} % at 10.8-22.7 kW, Wolfspeed measured {em[0]*100:.2f}-{em[-1]*100:.2f} % '
      f'(UG p55 Fig. 57). Measured loss is {np.min(pm*(1/em-1)/rc["ploss"]):.1f}-{np.max(pm*(1/em-1)/rc["ploss"]):.1f}x the '
      f'model. The 99.2 % / >98.8 % figures are web-page claims; the user guide only shows 97.0-97.6 %. '
      f'If the unexplained CRD loss ({sens["crd_gap_fit"][0]:.0f} W + {sens["crd_gap_fit"][1]*1e3:.1f} W/kW, fitted) is intrinsic, '
      f'our 800 V/800 V efficiency becomes {sens["eta_crd_adjusted"][0]*100:.2f} % at 60 kW and {sens["eta_crd_adjusted"][1]*100:.2f} % at 30 kW: '
      f'**neither target would be met**. Resolving this gap is the first bench task.')
    w(f'* **Worst junction temperature {st["tj_max"]:.0f} C** at {_fmt_pt(st["tj_max_at"])} with 50 C coolant inlet (design '
      f'ceiling 140 C, datasheet 150 C).')
    pm_, pg_ = R['pess']['modules'], R['pess']['magnetics']
    w(f'* **Pessimistic case (CRD gap {R["pess"]["k0_W"]:.0f} W + {R["pess"]["k1_W_per_kW"]:.1f} W/kW added everywhere):** '
      f'800/800 V efficiency {pm_["eta_800_800"]["60kW"]*100:.2f} % at 60 kW, {pm_["eta_800_800"]["30kW"]*100:.2f} % at 30 kW. '
      f'If the loss is in the modules: Tj up to {pm_["Tj_max_C"]:.0f} C, full power survives at '
      f'{len(pm_["full_power_points_surviving"])} of {R["pess"]["base_full_power_points"]} base full-power window points; '
      f'fix: {pm_["fix"]}. If it is in the transformer: hot spot {pg_["xfmr_hot_spot_max_C"]:.0f} C, full power at '
      f'{len(pg_["full_power_points_surviving"])} points; {pg_["fix"]}. Section 5b.')
    w(f'* **950 V on 1200 V devices:** {R["v950_text"]}')
    w(f'* **Full-power window misses** (DAB-10 x DAB-11 at 60 kW, either direction): '
      + (', '.join(_fmt_pt(t) for t in st['infeasible_win_60k']) or 'none') + '. Root cause: switching SOA of the '
      'bridge on the 900 V side (turn-off current too high at that voltage). This challenges assumption DAB-10/11: '
      'either accept derating at the window corners, or run the PCS DC bus at V1 ~ n*V2 (then d ~ 1, ZVS everywhere).')
    w('* **ZVS:** with SPS, bridge 1 loses ZVS at light load when V1 < n*V2 and bridge 2 when V1 > n*V2 '
      '(`zvs_map_sps.png`). EPS/DPS/TPS from an offline LUT restores most of it and is required for light load and '
      'for the 950 V operating range; SPS alone is adequate only near V1 = n*V2 above ~30 % load.')
    w('* **DC flux walk:** open loop is not viable (a 5 ns pulse-width error gives ~%.0f A DC). Baseline: firmware flux-balance '
      'loop + gapped transformer + hardware trip; series blocking capacitor kept as a bolted-link provision.'
      % spec['flux_balance']['open_loop_5ns_skew_I_dc_A'])
    w('')
    w('## 2. Basis: what the reference design really is')
    w('')
    w(f'* Module: **CBB011M12GM4T** (1200 V, 11 mOhm, full bridge, Gen 4, pre-applied TIM) on both bridges - UG p6, p8 sec 2.1; '
      'roadmap is correct.')
    w('* CRD Table 1 (UG p8): 60 kW, Vin 700/800/900 V, Vout 400/800/900 V, Iout <= 100 A, 100 kHz, coldplate-fluid 0.045 '
      'K/W per switch position; power derated below 600 V out (= 100 A limit). Our map reproduces this.')
    w('* Transformer: YAGEO/Egston 81989-01 (UG p51-52 Table 9): L_sigma 7.5 uH, L_m 329 uH, R_w 78 mOhm, R_c 1.24 ohm at '
      '100 kHz, 202x81x73 mm, cold-plate cooled. **Turns ratio not published** (1:1 inferred from the 800 V/800 V test).')
    w('* Test (UG Table 11): 800 V -> 800 V, dead time 200 ns, RG(on) 1 ohm, RG(off) 0 ohm, 10-23 kW. The Table 11 phase '
      'column (2.5-5.0 deg) contradicts the guide itself: Fig. 55 shows 10.0 deg at 22.7 kW, which matches Table 9 '
      f'(7.5 uH) and our SPS model ({np.degrees(rc["phi"][-1]):.1f} deg). Table 11 phase is not used and not combined with '
      'Table 9.')
    w('* CRD bulk capacitor C4AQNEW5650M3BJ (UG p23, "1 kV") is not in the C4AQ datasheet on file (no N voltage code or M '
      'release code); our calibration run uses the 1100 V sibling C4AQQEW5650A3BJ as a stand-in.')
    w('* Snubbers: 6 x (6.8 ohm + 2.2 nF 2 kV) RC across each DC link (UG Fig. 22); DPT simulation 800 V/100 A: 952 V peak '
      'without, 940 V with snubber (UG Fig. 21) = ~150 V overshoot.')
    w('* TI TIDA-010054 cross-check (TIDUES0F): 10 kW, 100 kHz, L = 35 uH, n = 1.6, phase <= 0.44 rad, DC-blocking caps sized '
      'C >= 100/(4 pi^2 f^2 L) (Eq. 20), leakage sized for ZVS only down to 1/2-1/3 load, EPS in firmware - same conclusions '
      'as ours on light-load ZVS.')
    w('* **ST STDES-DABBIDIR / STSW-DABBIDIR cross-check: OPEN.** The documents could not be downloaded (st.com '
      'bot-blocked); nothing in this report is checked against them.')
    w('* Datasheet inconsistencies found: (a) Fig. 8 C_oss is half of Fig. 9 at the same label voltage; it matches Fig. 9 if '
      'its axis is read x4 -> Fig. 9 + table used. (b) DPT E_off(20 A, 800 V) = 0.10 mJ < E_oss(800 V) = 0.17 mJ from Fig. 9, '
      'so DPT E_off is used as the full ZVS turn-off loss (no E_oss subtraction).')
    w('')
    w('## 3. Design choices')
    w('')
    w('| quantity | value | why |')
    w('|---|---|---|')
    w(f'| n = N1:N2 | {D["N1"]}:{D["N2"]} ({D["n"]:.4f}) | centres d = n V2/V1 on the 600-900 V x 700-900 V window; `selection_turns.csv` |')
    w(f'| L total | {D["L"]*1e6:.2f} uH +/-5 % | min mean loss; 60 kW at every window point except the SOA-limited corners |')
    w(f'| leakage / external | {D["Llk"]*1e6:.2f} / {D["Lext"]*1e6:.2f} uH | leakage estimated from PM 114/93 geometry with '
      f'{a("b_iso")*1e3:.0f} mm insulation; external inductor trims the set to +/-5 % |')
    w(f'| L_m | {D["Lm"]*1e6:.0f} uH +/-10 % | loss tie with 300 uH ({R["jlm"][150e-6]:.1f} vs {R["jlm"][300e-6]:.1f} W mean); '
      'halves DC flux per amp of residual DC current |')
    w('| dead time | adaptive 50-300 ns (transition + 20 ns) | mean loss: ' + ', '.join(
        f'{k} {v["J"]:.0f} W' for k, v in R['dead_time'].items()) + ' |')
    w(f'| B_pk at 950 V | {sp["xfmr"]["b_pk_950"]*1e3:.0f} mT (A_e), {sp["xfmr"]["b_pk_min_section"]*1e3:.0f} mT (A_min) | '
      'N95 B_sat 410 mT at 100 C |')
    w('')
    w('## 4. Power transfer, modulation, ZVS')
    w('')
    w('SPS power P = n V1 V2 phi (pi - |phi|) / (2 pi^2 f L) (`power_vs_phase.png`). ZVS is evaluated per leg transition by '
      'integrating the inductor energy against the real C_oss(V) charge of the module (Fig. 9), a 150 pF node capacitance and '
      'the opposing bridge voltage. Incomplete transitions dissipate the residual capacitive energy; wrong-polarity '
      'transitions are hard switched (DPT E_on + E_rr). `zvs_map_sps.png` shows SPS; `efficiency_map.csv` gives the chosen '
      'a1/a2/phi and ZVS flags of the best modulation at every point.')
    w('')
    w('## 5. Losses and efficiency (calculated, coolant 50 C)')
    w('')
    w('| point | eta | loss W | cond b1/b2 | sw b1/b2 | xfmr core/Cu | L core/Cu | caps | bus | Tj1/Tj2 C |')
    w('|---|---|---|---|---|---|---|---|---|---|')
    for k in k_nom:
        q = nom[k]
        w(f'| {k} | {q["eta"]*100:.2f} % | {q["ploss"]:.0f} | {q["pc1"]:.0f}/{q["pc2"]:.0f} | {q["psw1"]:.0f}/{q["psw2"]:.0f} | '
          f'{q["p_core"]:.0f}/{q["p_cu"]:.0f} | {q["pl_core"]:.0f}/{q["pl_cu"]:.0f} | {q["p_cap"]:.0f} | {q["p_bus"]:.0f} | '
          f'{q["tj1"]:.0f}/{q["tj2"]:.0f} |')
    w('')
    w(f'Points in the full-power window at >= 20 kW below 98.8 % (model, best modulation): ' +
      ('; '.join(f'{_fmt_pt(t[:3])}: {t[3]*100:.2f} %' for t in st['below_988_win']) or 'none') + '.')
    w('')
    w('CRD calibration (`crd_calibration.png`): model vs measured at 800 V/800 V:')
    w('')
    w('| P kW | measured % | model % | measured loss W | model loss W |')
    w('|---|---|---|---|---|')
    for i in range(len(pm)):
        w(f'| {pm[i]/1e3:.1f} | {em[i]*100:.2f} | {rc["eta"][i]*100:.2f} | {pm[i]*(1/em[i]-1):.0f} | {rc["ploss"][i]:.0f} |')
    w('')
    w(f'Sensitivities: VGS 18 V instead of 15 V changes 60/30 kW efficiency by {sens["vgs18_deta"][0]*100:+.2f}/'
      f'{sens["vgs18_deta"][1]*100:+.2f} points; coolant 25 C instead of 50 C lowers Tj from {sens["tj_base"][0]:.0f} to '
      f'{sens["coolant25_tj"][0]:.0f} C at 60 kW; +5 % L changes power at fixed phase by {sens["dP_L5"]*100:.1f} %.')
    w('')
    w('## 5b. Pessimistic case: the calibration gap taken as real')
    w('')
    pz = R['pess']
    w(f'Loss model + {pz["k0_W"]:.0f} W + {pz["k1_W_per_kW"]:.1f} W/kW (fit of the CRD measured-minus-model loss, UG '
      f'Fig. 57) added at every operating point, 50 C coolant inlet, {a("coolant_flow"):.0f} L/min. Two bounding '
      'allocations of where the heat goes:')
    w('')
    w('| quantity | baseline model | all extra loss in the 2 modules | all extra loss in the transformer |')
    w('|---|---|---|---|')
    b8 = st['nominal']
    w(f'| eta 800/800 V 60 / 30 kW | {b8["800V/800V/60kW"]["eta"]*100:.2f} / {b8["800V/800V/30kW"]["eta"]*100:.2f} % | '
      f'{pz["modules"]["eta_800_800"]["60kW"]*100:.2f} / {pz["modules"]["eta_800_800"]["30kW"]*100:.2f} % | '
      f'{pz["magnetics"]["eta_800_800"]["60kW"]*100:.2f} / {pz["magnetics"]["eta_800_800"]["30kW"]*100:.2f} % |')
    w(f'| worst Tj at 60 kW in the full-power window | {st["tj_max"]:.0f} C | {pz["modules"]["Tj_max_C"]:.0f} C | '
      f'{pz["magnetics"]["Tj_max_C"]:.0f} C |')
    w('| transformer hot spot | not evaluated: construction pending (MG-02) | - | - |')
    w(f'| coolant heat at 60 kW (max) / flow for 5 K rise | {st["q_cool_max"]:.0f} W / '
      f'{st["q_cool_max"]/(a("coolant_rho_cp")*5)*60e3:.1f} L/min | {pz["modules"]["coolant_heat_W_max_60kW"]:.0f} W / '
      f'{pz["modules"]["flow_for_5K_lpm"]:.1f} L/min | {pz["magnetics"]["coolant_heat_W_max_60kW"]:.0f} W / '
      f'{pz["magnetics"]["flow_for_5K_lpm"]:.1f} L/min |')
    w(f'| full-power points kept (of {pz["base_full_power_points"]}) | all | '
      f'{len(pz["modules"]["full_power_points_surviving"])} | {len(pz["magnetics"]["full_power_points_surviving"])} |')
    w('')
    w(f'* Modules allocation - points that lose full power: ' + (', '.join(f'{q[0]:.0f}/{q[1]:.0f} V' for q in
      pz['modules']['full_power_points_lost']) or 'none') + f'. Fix: {pz["modules"]["fix"]}.')
    w(f'* Transformer allocation - points that lose full power: ' + (', '.join(f'{q[0]:.0f}/{q[1]:.0f} V' for q in
      pz['magnetics']['full_power_points_lost']) or 'none') + f'. Fix: {pz["magnetics"]["fix"]}.')
    w('* Coolant flow is not the constraint in either case (a few L/min for 5 K); the local thermal resistances are.')
    w('')
    for alloc in ('modules', 'magnetics'):
        V1g, V2g, pmax, why = pz[alloc]['_map']
        w(f'Pessimistic derating map, extra loss in the {alloc} [kW] (rows V1, columns V2):')
        w('')
        w('| V1 \\\\ V2 | ' + ' | '.join(f'{x:.0f}' for x in V2g[0]) + ' |')
        w('|---|' + '---|' * V2g.shape[1])
        for i in range(V1g.shape[0]):
            w(f'| {V1g[i,0]:.0f} | ' + ' | '.join('-' if np.isnan(x) else f'{x/1e3:.1f}' for x in pmax[i]) + ' |')
        w('')
    w('## 5c. Design levers toward > 98.8 % above 20 kW (model, owner decision only; baseline unchanged)')
    w('')
    w('| lever | modules | eta 800/800 V 60 kW (best modulation) | loss W | worst window point (>= 20 kW) | share of window >= 98.8 % | Tj max C |')
    w('|---|---|---|---|---|---|---|')
    for q in R['levers']:
        if np.isnan(q['eta_800_800_60kW']):
            w(f'| {q["lever"]} | {q["modules_total"]} | not evaluated: R_DS(on) 25.6 mOhm at 150 C (+45 % vs GM4) | - | - | - | - |')
            continue
        w(f'| {q["lever"]} | {q["modules_total"]} | {q["eta_800_800_60kW"]*100:.2f} % | {q["loss_800_800_60kW_W"]:.0f} | '
          f'{q["eta_worst_window"]*100:.2f} % ({q["worst_point"]}) | {q["frac_window_ge_98p8"]*100:.0f} % | {q["Tj_max_C"]:.0f} |')
    w('')
    w('These are the incumbent-module levers of the D-017 study, re-run with today\'s model for reference; the Asian '
      'device option study in section 0 supersedes them.')
    w('')
    w('FM3 rows use the CAB011M12FM3 table values (R_DS(on), package resistance, E_on, R_th) with our GM4 curve shapes; its '
      'E_off (0.71 mJ at RG(off) 1 ohm) is ~2.3x GM4 at the same gate resistance (DS Fig. 15), which outweighs the 11 % '
      'lower on-resistance. The FM3 datasheet does not state the MOSFET generation; if it is Gen 3, its 950 V cosmic-ray '
      'rate is ~50x Gen 4 (section 7). The window is V1 600-900 V x V2 700-900 V at 20-60 kW; the model calibration gap '
      'applies to every row.')
    w('')
    w('## 6. Derating map (`derating_map.png`, `derating_map.csv`)')
    w('')
    V1g, V2g, pmax, why = R['derating']['charge']
    w('Max charge power [kW] (rows V1, columns V2), limits: Tj <= 140 C at 50 C coolant, per-switch RMS <= 90 A, '
      'switching SOA (Fig. 19 polyline, 24 nH), port currents 102/100 A, transformer hot spot 130 C at 0.40 K/W:')
    w('')
    w('| V1 \\ V2 | ' + ' | '.join(f'{x:.0f}' for x in V2g[0]) + ' |')
    w('|---|' + '---|' * V2g.shape[1])
    for i in range(V1g.shape[0]):
        w(f'| {V1g[i,0]:.0f} | ' + ' | '.join('-' if np.isnan(x) else f'{x/1e3:.1f}' for x in pmax[i]) + ' |')
    w('')
    w('Reading: full power is available for V2 >= 650-700 V while V1 stays within ~0.75..1.1 x n V2; below ~600 V of V2 the '
      '100 A port-2 limit and Tj derate toward 35-37.5 kW at 400 V, in line with the CRD statement. Discharge is within '
      '2.5 kW of charge everywhere.')
    w('')
    w('## 7. 950 V verdict')
    w('')
    w('| V DC | Gen 4 FIT/bridge sea level | Gen 4 FIT/bridge 2000 m | Gen 3 FIT/bridge sea level |')
    w('|---|---|---|---|')
    for c in v9['cosmic']:
        w(f'| {c["v"]:.0f} | {c["fit_gen4_bridge_sea"]:.2g} | {c["fit_gen4_bridge_2000m"]:.2g} | {c["fit_gen3_bridge_sea"]:.2g} |')
    w('')
    w('Turn-off current limit from DS Fig. 19 (Tvj 150 C, RG(off) 0): ' + ', '.join(
        f'{k}: {v["24nH"]:.0f} A (24 nH) / {v["18nH"]:.0f} A (18 nH)' for k, v in v9['ioff_limit'].items()) + '.')
    w('')
    w(R['v950_text'])
    w('')
    w(f'Margin note: the derating boundary is placed ON the datasheet switching-SOA line (no extra margin); the worst '
      f'feasible point reaches {st["vds_pk"]:.0f} V estimated V_DS peak. Each 50 V of margin costs about one 2.5-5 kW '
      f'derating step in the 850-950 V rows; the 24 nH loop is an assumption to be measured by DPT.')
    w('')
    w('## 8. DC-bias / flux-walk countermeasure')
    w('')
    fw = study_flux_walk(D)
    w(f'Primary DC loop resistance {fw["r_loop"]*1e3:.1f} mOhm. Open-loop DC current from a pulse-width error: ' + ', '.join(
        f'{c["skew_ns"]:.0f} ns -> {c["idc_open_loop"]:.0f} A ({c["b_dc_open_loop"]*1e3:.0f} mT)' for c in fw['cases']) +
      f'. B_ac at 950 V = {fw["b_ac_950"]*1e3:.0f} mT, B_sat(100 C) = 410 mT.')
    bcb = spec['flux_balance']['blocking_cap_alternative']
    w(f'* Control (baseline): average transformer current -> PI -> HRPWM duty trim of one primary leg; residual = sensor '
      f'offset/drift {a("xfmr_dc_resid")} A -> B_dc {fw["b_dc_ctrl"]*1e3:.0f} mT with L_m {D["Lm"]*1e6:.0f} uH '
      f'(an ungapped core, L_m {D["lm_ungapped"]*1e3:.1f} mH, would give {D["lm_ungapped"]*a("xfmr_dc_resid")/(D["N1"]*D["ae"])*1e3:.0f} mT).')
    w(f'* Blocking capacitor (alternative, TI style): {bcb["bank"]}, TI rule C >= {bcb["C_min_TI_rule_F"]*1e6:.0f} uF, '
      f'{bcb["loss_W"]:.0f} W, {bcb["volume_L"]:.2f} L - kept as a bolted-link provision, not fitted.')
    w('')
    w('## 9. Transformer and series inductor (Gate-0 magnetics RFQ)')
    w('')
    x, ind = sp['xfmr'], sp['ind']
    rows = [('turns ratio', f'{D["N1"]}:{D["N2"]} (n = {D["n"]:.4f}); reference design N1 = {D["N1"]}, N2 = {D["N2"]}'),
            ('L_sigma (referred to N1, secondary shorted, 100 kHz)', f'{D["Llk"]*1e6:.2f} uH target +/-20 %'),
            ('L_m (100 kHz, open secondary)', f'{D["Lm"]*1e6:.0f} uH +/-10 %'),
            ('volt-seconds per half period', f'{x["vs_half"]*1e3:.2f} mVs (950 V x 5 us)'),
            ('construction', 'designed by the magnetics engineer (sim/out/magnetics/design_dab_transformer.json); the '
                             'PM 114/93 reference construction is withdrawn (MG-01..04, MG-14)'),
            ('I_rms max (primary / secondary)', f'{x["i1_rms"]:.0f} / {x["i2_rms"]:.0f} A'),
            ('I_pk max (primary / secondary)', f'{x["i1_pk"]:.0f} / {x["i2_pk"]:.0f} A'),
            ('loss used in the maps', f'{MAG_SRC()}; max over the map {x["p_max"]:.0f} W'),
            ('insulation', 'port 1 - port 2 basic (simple separation) at 1850 V DC: PD <= 10 pC at 3149 Vpk, 3323 V rms, '
                           '6 kV impulse; each winding to the earthed core basic (PD at 1799 / 1724 Vpk, 2200 V rms, 6 kV); '
                           'winding NTC reinforced (D-032, IC-08; dab_spec transformer.isolation)'),
            ('interwinding capacitance', '<= 300 pF target (common-mode current), measure'),
            ('temperature', 'hot spot <= 130 C at 50 C coolant, class F (155 C) or better, cold-plate mounted'),
            ('external inductor', f'leakage + external = {D["L"]*1e6:.2f} uH +/-5 % (split set by the magnetics design; '
                                  f'MG-04: the buildable reference winding gives 3.08 uH leakage); I_rms {ind["i_rms"]:.0f} A, '
                                  f'I_pk {ind["i_pk"]:.0f} A, no saturation to the trip fault peak (dab_spec); loss used in '
                                  f'the maps: {MAG_SRC()}, max {ind["p_max"]:.0f} W'),
            ('acceptance test', 'L_sigma, L_m, L_total of the matched set at 100 kHz; R_ac at 100/300/500 kHz; loss at '
                                '800 V/60 kW in a DAB or calorimeter; hipot; PD; thermal run'),
            ('second source', 'same drawing and acceptance test to two vendors (roadmap Magnetics sourcing)')]
    w('| item | requirement |')
    w('|---|---|')
    for k, v in rows:
        w(f'| {k} | {v} |')
    w('')
    w('## 10. Port capacitor banks')
    w('')
    w('| port | part | qty | C | V rating | ripple bank / per cap A rms | rating A | ripple V pp | hot spot C | V allowed at hot spot |')
    w('|---|---|---|---|---|---|---|---|---|---|')
    for k, v in sp['caps'].items():
        w(f'| {k} | KEMET {v["mpn"]} | {v["n"]} | {v["c"]*1e6:.0f} uF | {v["v_rating"]:.0f} V | {v["i_rms_bank"]:.0f} / '
          f'{v["i_rms_per_cap"]:.1f} | {v["i_rms_rating"]:.0f} | {v["ripple_pp_max"]:.1f} | {v["t_hotspot"]:.0f} | '
          f'{v["v_allow_at_hotspot"]:.0f} V vs {v["v_max"]:.0f} V |')
    w('')
    w('Port 1 uses the 1300 V C4AQ (1100 V parts would be limited to ~81 C hot spot at 950 V by the p6 derating). '
      'Ripple assumes the bank takes all switching ripple (no credit for the PCS/battery side).')
    w('')
    w('## 11. ngspice switching-level cross-check (`sim/spice/dab_*.cir`, `spice_vs_analytic.png`)')
    w('')
    w('| deck | V1/V2 V | P target kW | phi cmd/eff deg | P analytic kW | P1/P2 ngspice kW | i_L rms an/sp A | i_L pk an/sp A | v at turn-on pri an/sp V | sec an/sp V | bus-1 peak V |')
    w('|---|---|---|---|---|---|---|---|---|---|---|')
    for q in R['spice']:
        w(f'| dab_{q["name"]} | {q["v1"]:.0f}/{q["v2"]:.0f} | {q["p_target"]/1e3:.1f} | {q["phi_deg"]:.2f}/{q["phi_eff_deg"]:.2f} | {q["p_analytic"]/1e3:.2f} | '
          f'{q["p1_spice"]/1e3:.2f}/{q["p2_spice"]/1e3:.2f} | {q["il_rms_an"]:.1f}/{q["il_rms_sp"]:.1f} | '
          f'{q["il_pk_an"]:.1f}/{q["il_pk_sp"]:.1f} | {q["vres_pri_an"]:.0f}/{q["vres_pri_sp"]:.0f} | '
          f'{q["vres_sec_an"]:.0f}/{q["vres_sec_sp"]:.0f} | {q["v_bus1_pk"]:.0f} |')
    w('')
    w('Decks: ideal S-switch with R_DS(on)(80 C) + package, body diode (V_SD ~ 3.6 V at 20 A, 5.8 V at 100 A), nonlinear '
      'C_oss charge fit of Fig. 9, 24 nH loop + CRD snubber per bridge, ideal transformer with L_m. Negative "v at turn-on" '
      '= body diode conducting = ZVS. Tolerances asserted: power 3 %, RMS 5 %, peak 7 %, turn-on voltage 15 % of bus.')
    w('')
    w('**Dead-time phase drift (found by this comparison):** a bridge that does not complete its swing within the dead '
      'time changes polarity only when the incoming switch turns on, so its edge moves by up to one dead time (150 ns = '
      '5.4 deg). At 800 V/800 V and 6 kW the commanded 2.4 deg becomes ~7-8 deg effective and SPS would transfer ~3x the '
      'intended power. The analytic column above includes this edge delay; the controller must close the power/current '
      'loop (never open-loop phase tables) and the TPS LUT must be built with the same edge model.')
    w('')
    w('## 12. Assumptions (ours, not datasheet values)')
    w('')
    for k, (v, why_) in A.items():
        w(f'* `{k}` = {v if isinstance(v, (tuple, dict, type(None))) else format(v, "g")}: {why_}')
    w(f'* die area per switch position for cosmic-ray FIT = {dev.COSMIC["die_area_cm2"]} cm2 (not published).')
    w('* Switching energies: DS Figs. 11/12 interpolated in current, power law in voltage (exponent clamped 0.7-2), '
      'temperature factors from the p2/p3 tables applied at all currents. iGSE alpha = 1.6.')
    w('* Litz AC factor and ferrite curves at 100 kHz only; magnetics temperature fixed at 90 C.')
    w('')
    w('## 13. Open items')
    w('')
    w('* Explain the 2.4-2.5x loss gap against the CRD measurement (bench: calorimetric loss split, transformer AC '
      'resistance with real current shape, eddy loss in the cold plate).')
    w('* Real loop inductance by DPT on our layout (sets the 900-950 V derating).')
    w('* Short-circuit withstand of the chosen devices: not published by either maker - see section 0.1 (DR-05).')
    w('* Bus ring-down (0.5 L_bus dI^2 per commutation): measure L_bus by DPT and the HF-capacitor / snubber-resistor '
      'temperatures at the full-power corners before release.')
    w('* Insulation coordination (ECO-10) to freeze hipot/PD/creepage of the transformer.')
    w('* Magnetics vendor confirmation of leakage tolerance and loss; second source.')
    w('* ST STDES-DABBIDIR cross-check (open: download bot-blocked).')
    w('* Which allocation of the CRD calibration gap is real (section 5b decides between a cooling fix and a transformer '
      'redesign).')
    w('')
    w('## 15. Board-review round (DAB60 drawn from dab_spec.json)')
    w('')
    sd = SNB
    w('**1. HF snubber dissipation** (`snubber_study.csv`, ngspice decks `sim/spice/dab_snub_*.cir`). The decks split the '
      '24 nH loop as in DS Fig. 19 (7.2 nH bank-to-terminals, 16.8 nH inside the module) and use a finite-di/dt '
      'channel (10 A/ns, calibrated: 600 V/100 A DPT overshoot ~330 V vs ~225 V implied by Fig. 19, i.e. conservative). '
      'Physics: every commutation reverses the bridge DC current; 0.5 L_bus dI^2 rings down in the terminal network '
      f'(map-wide worst {sd["p_ring_max"]:.0f} W per bridge, now in the loss model). Without HF ceramics the RC resistors '
      f'take most of it ({sd["no_hf_worst_W"]:.1f} W per 1.5 W resistor at the worst simulated point); with the CRD\'s 10 x '
      f'47 nF ceramics (missing from our earlier spec) they take <= {sd["share"]*100:.0f} %. Worst resistor '
      f'{sd["worst_W"]:.2f} W -> {sd["n"]} x {sd["r"]:.2f} ohm {sd["p_rate"]:g} W (2512) + {sd["c"]*1e9:.2f} nF per bridge '
      f'(<= 50 % rating). Overshoot it achieves: terminal ringing and die peaks in the table below; the die-level peak is '
      'set by the module\'s internal 16.8 nH and is governed by the datasheet SOA (derating map), not by the RC.')
    w('')
    w('| point | network | V1/V2/P | res. W port 1 / 2 | terminal ringing p-p V | die peak V (model) | ring-down W b1/b2 |')
    w('|---|---|---|---|---|---|---|')
    for q in R['snub']:
        w(f'| {q["point"]} | {q["cfg"]} | {q["V1"]:.0f}/{q["V2"]:.0f} V/{q["P"]/1e3:.0f} kW | {q["P_res1_W"]:.2f} / '
          f'{q["P_res2_W"]:.2f} | {q["V_term1_pp"]:.0f} / {q["V_term2_pp"]:.0f} | {q["V_die1_pk"]:.0f} / '
          f'{q["V_die2_pk"]:.0f} | {q["P_ring1_an"]:.0f} / {q["P_ring2_an"]:.0f} |')
    w('')
    w(f'**2. Gate resistors.** RG_off = {a("RG_off"):.2f} ohm (E24; sink {19.41/(0.3+0.99*a("RG_off")+1.4):.2f} A max at '
      'VDD-VEE 19.41 V, ROL 0.3 + RG(int) 1.4 ohm typ, -1 % resistor; 0 ohm gave 11.4 A). E_off rises by 0.169 mJ/ohm at '
      '600 V/100 A (DS Fig. 15, scaled with I and V in the model); dv/dt_off ~60 -> ~57 V/ns (Fig. 29); di/dt_off is flat '
      'in RG (Fig. 29) so overshoot and the SOA derating are unchanged; t_d(off) +3.5 ns goes into the dead-time LUT. '
      'Maps re-run: see sections 1, 5, 6.')
    w('')
    w('**3. DESAT timing.** NSI6651 industrial grade: see section 0.1 (DR-05) and dab_spec gate_drive.desat.')
    w('')
    w('**4. Bias.** UCC14241-Q1 (D-016), 24 V in, regulated +18 / -3.5 V; load per switch in dab_spec '
      'gate_drive.P_bias_per_switch_W.')
    w('')
    pn = PORT
    w(f'**5. Ports.** Authority = `sim/out/port_design/port_spec.json`: {pn["R_ohm"]:.0f} ohm precharge, close at dV <= '
      f'{pn["dV_ok_V"]:.0f} V, {pn["fuse_mpn"]} {pn["fuse_In_A"]:.0f} A gPV fuses, {pn["contactor"]} contactors with '
      'hold-closed interlock. dab_spec precharge values now copied from it (time to dV <= 10 V: '
      f'{pn["R_ohm"]*R["specs"]["caps"][1]["c"]*np.log(95):.2f} s port 1, {pn["R_ohm"]*R["specs"]["caps"][2]["c"]*np.log(90):.2f} s '
      'port 2). dab_control starts the DAB after contactor closure with the bank 10 V below the battery - consistent.')
    w('')
    w('**6. Transformer current sensor.** LEM HOB 130-P: +/-250 A, 1 MHz, t_D90 <= 200 ns (HOB-P p9) - meets the >= 1 '
      'MHz requirement; range is enough: peak operating 177 A, control transients <= 182 A with the split phase update, '
      'OC trip 210 A (200-220 A) = 88 % of range; required >= 242 A. A naive (unsplit) phase step would add ~47 A and '
      'reach the trip band at the corners - the split update is mandatory.')
    w('')
    ox = OVX
    w(f'**7. Local OV trips** (board: port 1 972-1000 V, port 2 924-950 V, 63 us to the gates), load rejection with '
      f'the full port current into the bank: port 1 {ox["port1"]["slew_V_per_us"]:.2f} V/us -> {ox["port1"]["V_peak"]:.0f} V '
      f'peak, port 2 {ox["port2"]["slew_V_per_us"]:.2f} V/us -> {ox["port2"]["V_peak"]:.0f} V peak. {ox["verdict"]}.')
    w('')
    rc = R['rclamp']
    w('**8. DC-link reverse clamp** (coordinator addendum). Bolted short at a port terminal, bank at the hardware OV '
      f'trip maximum, loop {PORT_LOOP["L"]*1e6:.1f} uH / {PORT_LOOP["R"]*1e3:.2f} mOhm (port design), no fuse-arc credit:')
    w('')
    w('| port | case | I_pk kA | I at reversal kA | clamped us | per body diode pk A | body-diode I2t A2s | clamp pk kA | clamp I2t kA2s | clamp V_F pk V | I2t margin per device |')
    w('|---|---|---|---|---|---|---|---|---|---|---|')
    for k in (1, 2):
        for case in ('module_only', 'with_clamp'):
            q = rc[f'port{k}'][case]
            w(f'| {k} | {case} | {q["I_pk_A"]/1e3:.1f} | {q["I_at_reversal_A"]/1e3:.1f} | {q["t_clamped_us"]:.0f} | '
              f'{q["I_per_body_diode_pk_A"]:.0f} | {q["I2t_per_body_diode_A2s"]:.3g} | '
              + (f'{q["I_clamp_pk_A"]/1e3:.1f} | {q["I2t_clamp_A2s"]/1e3:.1f} | {q["V_clamp_pk_V"]:.2f} | {q["I2t_margin_at_event"]:.1f} |'
                 if case == 'with_clamp' else '- | - | - | - |'))
    w('')
    cl, c1, c2 = R['clamp_pick'], rc['port1'], rc['port2']
    w(f'Without a clamp each switch position\'s body diodes would carry {c1["module_only"]["I_per_body_diode_pk_A"]/1e3:.1f} / '
      f'{c2["module_only"]["I_per_body_diode_pk_A"]/1e3:.1f} kA per device (ports 1 / 2), {c1["module_overstress_factor"]:.0f} / '
      f'{c2["module_overstress_factor"]:.0f} x the device pulse rating (no body-diode surge rating is published) - destroyed. '
      f'Fix: {cl["part"]} x {cl["n_per_port"]} per port across the bank (cathode to DC+): V_RRM margin '
      f'{c1["V_RRM_margin"]:.2f} / {c2["V_RRM_margin"]:.2f} x the OV-trip bus peak; I2t margin at the event duration '
      f'{c1["with_clamp"]["I2t_margin_at_event"]:.1f} / {c2["with_clamp"]["I2t_margin_at_event"]:.1f}; clamp voltage '
      f'{c1["with_clamp"]["V_clamp_pk_V"]:.1f} V, body-diode peak {c1["body_diode_pk_vs_IDM"]*100:.0f} / '
      f'{c2["body_diode_pk_vs_IDM"]*100:.0f} % of the device pulse rating. Never conducts in normal operation. Options '
      'and cost: section 0.2.')
    w('')
    w('## 14. Files')
    w('')
    w('`selection_n_L.csv`, `selection_turns.csv`, `study_lm.csv`, `efficiency_map.csv`, `derating_map.csv`, '
      '`design_levers.csv`, `snubber_study.csv`, '
      '`power_vs_phase.png`, `zvs_map_sps.png`, `efficiency_map.png`, `derating_map.png`, `loss_breakdown.png`, '
      '`crd_calibration.png`, `spice_vs_analytic.png`, `spice_*.txt`, `dab_spec.json`.')
    with open(os.path.join(OUT, 'report.md'), 'w') as fh:
        fh.write('\n'.join(L) + '\n')


def selfcheck(R, D):
    # analytic SPS power vs exact segment integration, lossless balance, phase solver
    for v1, v2, p in ((800.0, 800.0, 60e3), (600.0, 900.0, 30e3), (950.0, 700.0, -40e3)):
        phi = phi_sps(v1, v2, D['n'], D['L'], p)
        o = dict(v1=v1, v2=v2, n=D['n'], L=D['L'], Lm=D['Lm'], a1=0.0, a2=0.0, phi=phi)
        s = stats(o)
        assert abs(s['P'] / p - 1) < 1e-6, 'SPS power formula vs exact integration'
        assert abs(v2 * s['I2'] / s['P'] - 1) < 1e-6, 'lossless power balance port 1 vs port 2'
        o.pop('phi')
        assert abs(float(solve_phi(dict(o, phi=0.0), p)) - phi) < np.radians(0.05), 'phase solver'
    # loss bookkeeping and energy balance
    r = evaluate([800.0], [800.0], [60e3], D)
    parts = r['pc1'] + r['pc2'] + r['psw1'] + r['psw2'] + r['p_core'] + r['p_cu'] + r['pl_core'] + r['pl_cu'] + \
        r['p_cap'] + r['p_bus'] + r['p_ring1'] + r['p_ring2']
    assert abs(parts[0] - r['ploss'][0]) < 1e-6 and 0.95 < r['eta'][0] < 1.0
    # ngspice vs analytic
    for q in R['spice']:
        pav = 0.5 * (q['p1_spice'] + q['p2_spice'])
        assert abs(pav / q['p_analytic'] - 1) < 0.03, f'ngspice power {q["name"]}'
        assert abs(q['il_rms_sp'] / q['il_rms_an'] - 1) < 0.05, f'ngspice RMS {q["name"]}'
        assert abs(q['il_pk_sp'] / q['il_pk_an'] - 1) < 0.07, f'ngspice peak {q["name"]}'
        for side, vb in (('pri', q['v1']), ('sec', q['v2'])):
            an, spv = q[f'vres_{side}_an'], max(q[f'vres_{side}_sp'], 0.0)
            assert abs(an - spv) <= 0.15 * vb, f'ngspice ZVS residual {q["name"]} {side}: {an:.0f} vs {spv:.0f}'
    # the model must stay more optimistic than the CRD measurement (documents the calibration gap)
    pm, em, rc = R['crd']
    assert np.all(rc['eta'] > em)
    # nominal point is inside the derating map at full power
    V1g, V2g, pmax, _ = R['derating']['charge']
    assert pmax[list(V1g[:, 0]).index(800.0), list(V2g[0]).index(800.0)] == 60e3
    # D-014 frozen design still within 3 % of the re-run optimum
    assert R['frozen']['J'] <= 1.03 * R['frozen']['J_opt'], 'D-014 design drifted from the optimum'
    # pessimistic case is worse than the baseline and its loss bookkeeping adds up
    eta60 = R['stress']['nominal']['800V/800V/60kW']['eta']
    for alloc in ('modules', 'magnetics'):
        assert R['pess'][alloc]['eta_800_800']['60kW'] < eta60
    rp = evaluate([800.0], [800.0], [60e3], D, extra=(R['pess']['k0_W'], R['pess']['k1_W_per_kW'] / 1e3, 'modules'))
    rb = evaluate([800.0], [800.0], [60e3], D)
    assert rp['ploss'][0] - rb['ploss'][0] > R['pess']['k0_W'] + R['pess']['k1_W_per_kW'] * 60 - 1e-6
    # baseline transformer hot spot respects its own limit wherever the map allows the power
    assert not R['stress']['t_xfmr_max'] > a('T_xfmr_hs_max')
    # reverse clamp: body diodes overstressed without it, protected with it; clamp inside its ratings
    for k in (1, 2):
        q = R['rclamp'][f'port{k}']
        assert q['module_overstress_factor'] > 1.0, 'without the clamp the body diodes exceed their pulse rating'
        assert q['body_diode_pk_vs_IDM'] <= 0.10 and q['with_clamp']['I2t_margin_at_event'] >= 2.0
        assert q['V_RRM_margin'] >= 1.5
    # snubber sizing respects 50 % of the resistor rating; gate sink current within the driver's 10 A
    assert SNB['worst_W'] <= 0.5 * SNB['p_rate'] + 1e-9
    assert 19.41 / (0.3 + 0.99 * a('RG_off') + 1.4) <= 10.0                       # incumbent (UCC21710)
    o = DESIGN
    if o['kind'] != 'gm4':
        v = (DESIGN_RAIL[0] - DESIGN_RAIL[1]) * 1.013
        assert v / (dev.NSI6651['rol'] + (0.99 * o['rg_off'] + o['d']['rg_int']) / o['npar']) <= dev.NSI6651['i_pk']
        # the design basis is never better than either maker it covers
        for p_ in o['parts']:
            mk = make_opt('chk', [p_], o['npar'], o['thermal'], '', o['cost'])
            r_m = evaluate([800.0], [800.0], [60e3], D, mod=mk)
            r_w = evaluate([800.0], [800.0], [60e3], D)
            assert r_w['ploss'][0] >= r_m['ploss'][0] - 1e-6, f'composite better than {p_["mpn"]}'
        assert DESIGN['hw_dt'][1] > DESIGN['hw_dt'][0] > 0
    # the magnetics reader reproduces the design files' own loss tables (same formulas, two of their points)
    if MAGD is not None:
        lt = {q['point']: q for q in MAGD['xfmr']['loss_table']}
        for name, v1, v2, p in (('matched_60kW', 800.0, 800.0 / D['n'], 60e3), ('v2low_94A', 700.0, 400.0, 37.5e3)):
            rr = evaluate([v1], [v2], [p], D, iters=1, mod=MOD_GM4)
            assert abs(rr['p_core'][0] + rr['p_cu'][0] - lt[name]['P_total_W']) <= 0.1 * lt[name]['P_total_W'], name
    # decision rule held: the chosen option meets the rules and is not less efficient than the incumbent
    assert R['pick']['rules_ok'] and R['pick']['eta_not_worse']
    # HF capacitors inside their current rule; trip peak inside the raised inductor saturation requirement
    assert R['hf']['I_rms_per_cap_max_A'] <= dev.HF_CAP['i_rms_rule'] * 1.0001
    assert R['prot']['V_peak_soft_V'] < 1200.0
    # ring-down bookkeeping: analytic f*L_bus*dI^2 at 800/800 V 60 kW SPS
    rr = evaluate([800.0], [800.0], [60e3], D, iters=1)
    i0 = rr['ioff1'][0]
    assert abs(rr['p_ring1'][0] - F * a('L_bus') * (2 * i0) ** 2) / rr['p_ring1'][0] < 0.02
    # two GM4 modules per bridge must lose less than one
    lv = {q['lever']: q for q in R['levers']}
    assert lv['2 x CBB011M12GM4T in parallel per bridge']['loss_800_800_60kW_W'] < \
        lv['baseline: 1 x CBB011M12GM4T per bridge (DAB-02)']['loss_800_800_60kW_W']
    print('dab_design self-check passed')


if __name__ == '__main__':
    R_, D_ = main()
    st_ = R_['stress']
    print(f"n = {D_['N1']}:{D_['N2']}, L = {D_['L']*1e6:.2f} uH (leakage {D_['Llk']*1e6:.2f} + external "
          f"{D_['Lext']*1e6:.2f}), Lm = {D_['Lm']*1e6:.0f} uH; peak eta {st_['eta_peak']*100:.2f} %; Tj max "
          f"{st_['tj_max']:.0f} C; outputs in {OUT}")
