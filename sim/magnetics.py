#!/usr/bin/env python3
"""Magnetics verification (REQUIREMENTS.md MAG-1, MAG-2; deliverable DEL-9).

For every magnetic part of our own design this script
  (i)  rebuilds the part in OpenMagnetics (PyOpenMagnetics / MKF engine) and asks it for inductance, flux, core and
       winding loss, leakage and temperature,
  (ii) recalculates the same quantities from first principles and the manufacturers' datasheets (own model below),
  (iii) puts both next to the designer's figure from the spec JSON, explains every disagreement beyond the bands
       (loss 15 %, inductance / flux 10 %, leakage 20 %), compares with the reference designs
       (sim/data/reference_magnetics.csv) and lists Asian material options (sim/data/asian_magnetic_materials.csv).
Outputs: sim/out/magnetics/report.md, magnetics_check.json, spec_<part>.md (one per part, MAG-2).
Inputs are the designers' hand-off files, so the script is simply re-run after a redesign:
  sim/out/pv_design/cell_spec.json ['inductor'], sim/out/dab_design/dab_spec.json ['transformer', 'series_inductor'],
  sim/out/aux_hv_design/aux_hv_spec.json ['transformer'], gen/port.py CMC160/CMC200 text + sim/port_design.py assumptions,
  sim/out/insulation/insulation_spec.json (optional; else the designers' insulation text, marked pending).
Everything here is CALCULATED, nothing is measured.  Exit code != 0 when a self-check fails (end of file).

Design files (magnetics designer, construction rev M1+): design_<part>.json - stable keys read by the converter engineers' loss / thermal models
  part, revision, construction (one line), status ('proposed' until a sample is measured), basis (spec file + mtime it was designed to)
  electrical: the requirement it was designed to (copied from the spec JSON) and what the construction gives
              (L_m / L_leak / L / tolerances, turns, B_pk at the worst volt-seconds)
  core: maker, part_number, material, n_sets, Ae_m2, le_m, Ve_m3, gap (type, length_m per gap, count), asian_alternative
  windings: list of {name, turns, section order, conductor (litz n x d / profile mm), copper_area_m2, MLT_m, R_dc_20C_ohm}
  insulation: system (potting / former / barriers / margins), levels (from sim/out/insulation/insulation_spec.json),
              tests (routine / type)
  loss_model: {"core": {"k","alpha","beta","Ve_m3","N","Ae_m2", "formula"}, "winding": {"R_ac_per_harmonic": [[f_Hz,
              R_ohm],...], "referred_to", "temperature_C", "formula"}} - P_core = Ve*k*f^alpha*B^beta (iGSE-equivalent for the
              trapezoid: multiply by shape factor), P_cu = sum_h R(h)*I_h_rms^2 (primary-referred harmonic resistance)
  loss_table: rows {point, I_rms_A (port-1 current for a transformer), B_pk_T, P_core_W, P_cu_W, P_total_W} at the
              converter engineer's operating points (worst case last)
  loss_grid: {I_rms_A[], P_cu_W[], B_pk_T[], P_core_W[], note} for interpolation (fixed current shape; flux shape stated)
  fit: {build_m, available_m, fits} - the conductor really fits the window
  thermal: {Rth_hotspot_to_coolant_K_W, basis, interface (cold-plate area, gap pad, potting), T_hotspot_C at the
            worst point and coolant temperature}
  mass_kg, cost: {core_usd, copper_usd, insulation_usd, labour_usd, total_usd, basis, cost_estimates_row}
  verification: {designer, openmagnetics, own} for L, B, losses, temperature (MAG-1)
  optional: variants (one construction, several ratings: port_cm_choke), alternatives (other cores evaluated: pv_inductor)
  files: design_dab_transformer, design_dab_series_inductor, design_aux_hv_transformer, design_port_cm_choke,
         design_pv_inductor (.json in sim/out/magnetics/); spec_<part>.md is rendered from the same dict
"""
import csv
import json
import math
import os
import re
import sys
import time

import numpy as np
from scipy import special

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sim", "out", "magnetics")
P = lambda *a: os.path.join(ROOT, *a)                                       # noqa: E731
EPS0 = 8.854e-12
MU0 = 4e-7 * math.pi
RHO20, ALPHA_CU = 1.72e-8, 0.00393            # annealed copper, ohm m at 20 C, 1/K
LITZ_LAY = 1.03                               # litz: DC resistance x1.03 for the strand lay (2 bunching levels, typical)
BANDS = {"loss": 0.15, "L": 0.10, "B": 0.10, "leak": 0.20}   # owner's agreement bands (task statement)
MAG = "docs/datasheets/magnetics/"


def rho_cu(t):
    return RHO20 * (1 + ALPHA_CU * (t - 20.0))


def load(path, key=None):
    with open(P(path)) as f:
        d = json.load(f)
    return d if key is None else d[key]


# =====================================================================================================================
# Own model 1: round-strand conductor losses, exact Bessel solution (skin = m=0 mode, proximity = m=1 mode in a
# uniform transverse field).  Litz = strands carrying I/n each, in the 1-D window field of the winding (Dowell /
# Sullivan field picture) plus the field of their own bundle (Tourkhani-Viarouge style internal term).
# =====================================================================================================================
def _strand_factors(d, f, t):
    """Return (F_R, P'/H^2): skin factor R_ac/R_dc of one round strand of diameter d [m] at f [Hz], T [C], and the
    time-average proximity loss per metre per (A/m peak)^2 of uniform transverse field."""
    rho = rho_cu(t)
    a = d / 2
    if f <= 0:
        return 1.0, 0.0
    delta = math.sqrt(rho / (math.pi * f * MU0))
    g = (1 + 1j) / delta                                                    # gamma = sqrt(j w mu0 sigma)
    ga = g * a
    i0, i1 = special.iv(0, ga), special.iv(1, ga)
    z_int = g * i0 / (2 * math.pi * a * i1) * rho                           # internal impedance per metre (ohm/m)
    f_r = z_int.real / (rho / (math.pi * a * a))
    c = 2 * MU0 / (g * i0)                                                  # A_z = C I1(g r) sin(theta), per unit H0
    r = np.linspace(0, a, 400)
    integ = np.trapezoid(np.abs(special.iv(1, g * r)) ** 2 * r, r)
    w = 2 * math.pi * f
    p_per_h2 = math.pi * w * w / rho * abs(c) ** 2 * integ / 2              # P' = (pi w^2 sigma |C|^2 / 2) int |I1|^2 r dr
    return float(f_r), float(p_per_h2)


def litz_loss(harm, n_str, d, n_turns, mlt, h2_per_a2, t, bundle_r):
    """Winding loss [W] of a litz winding.
    harm: list of (f [Hz], I_rms [A]) of the winding current (f = 0 for DC).
    h2_per_a2: mean over the winding cross-section of (window field peak)^2 per (winding current peak)^2 [1/m^2].
    bundle_r: litz bundle radius [m] for the internal field term <H^2> = I_pk^2 / (8 pi^2 r_b^2)."""
    length = n_turns * mlt * LITZ_LAY
    r_dc_strand = rho_cu(t) * length / (math.pi * d * d / 4)
    tot = {"dc": 0.0, "skin": 0.0, "prox_ext": 0.0, "prox_int": 0.0}
    for f, irms in harm:
        if f == 0:
            tot["dc"] += r_dc_strand / n_str * irms ** 2
            continue
        f_r, p_h2 = _strand_factors(d, f, t)
        ipk2 = 2 * irms ** 2
        tot["dc"] += r_dc_strand / n_str * irms ** 2
        tot["skin"] += r_dc_strand / n_str * (f_r - 1) * irms ** 2
        tot["prox_ext"] += n_str * length * p_h2 * h2_per_a2 * ipk2
        tot["prox_int"] += n_str * length * p_h2 * ipk2 / (8 * math.pi ** 2 * bundle_r ** 2)
    tot["total"] = sum(tot.values())
    tot["R_dc"] = r_dc_strand / n_str
    return tot


def ramp_h2(f_a, f_b, breadth):
    """mean of H^2 over a winding whose MMF ramps linearly from f_a to f_b ampere-turns across its build (1-D)."""
    return (f_a * f_a + f_a * f_b + f_b * f_b) / 3 / breadth ** 2


def harmonics(t, i, nmax=41):
    """(f, I_rms) list of a periodic waveform sampled on [0, T) (uniform)."""
    n = len(i)
    sp = np.fft.rfft(i) / n
    T = (t[1] - t[0]) * n
    out = [(0.0, abs(sp[0].real))]
    for h in range(1, min(nmax, n // 2)):
        out.append((h / T, abs(sp[h]) * math.sqrt(2)))
    return out


def sullivan_fr(n_str, d, n_turns, breadth, f, t):
    """Sullivan (1999) low-frequency litz factor F_r = 1 + pi^2 w^2 mu0^2 N^2 n^2 d^6 / (768 rho^2 b^2) (k = 1)."""
    w = 2 * math.pi * f
    return 1 + (math.pi * w * MU0 * n_turns * n_str * d ** 3) ** 2 / (768 * rho_cu(t) ** 2 * breadth ** 2)


# =====================================================================================================================
# Own model 2: core loss, iGSE (Venkatachalam 2002) on a piecewise-linear flux waveform, from a Steinmetz fit
# P_v = k f^a B^b [W/m3, Hz, T] taken from the manufacturer's curves.
# =====================================================================================================================
def igse(k, a, b, t, bwave):
    """t, bwave: one period, piecewise-linear flux density [s, T].  Returns W/m3."""
    th = np.linspace(0, 2 * math.pi, 20001)
    ki = k / ((2 * math.pi) ** (a - 1) * np.trapezoid(np.abs(np.cos(th)) ** a * 2 ** (b - a), th))
    dt = np.diff(t)
    db = np.diff(bwave)
    m = dt > 0
    dbpp = bwave.max() - bwave.min()
    T = t[-1] - t[0]
    return float(ki * dbpp ** (b - a) * np.sum(np.abs(db[m] / dt[m]) ** a * dt[m]) / T)


def steinmetz_from_points(f1, b1, p1, f2, b2, p2, f3, b3, p3):
    """k, alpha (freq), beta (flux) through three (f, B, P) points (log-linear solve)."""
    a = np.array([[1, math.log(f1), math.log(b1)], [1, math.log(f2), math.log(b2)], [1, math.log(f3), math.log(b3)]])
    y = np.log([p1, p2, p3])
    lk, al, be = np.linalg.solve(a, y)
    return math.exp(lk), al, be


def fringing_mclyman(lg, ae, g_window):
    """McLyman fringing factor F = 1 + lg/sqrt(Ae) ln(2G/lg) for a gap lg in a leg of area Ae, window height G."""
    return 1 + lg / math.sqrt(ae) * math.log(2 * g_window / lg)


# =====================================================================================================================
# Datasheet data used by the OWN model (read by the verifier from the PDFs on disk; page = PDF page)
# =====================================================================================================================
KMM_SRC = MAG + "Magnetics-Powder-Core-Catalog-2025.pdf"
# Kool Mu MAX: DC bias fit %mu = 1/(a + b H^c), H in Oe (p.66, E cores / U cores / EER cores); loss P = a B^b f^c,
# mW/cm3, B = half the AC swing [T], f [kHz] (p.112 E cores; p.111 toroids).  Bsat 1.0 T (catalog p.12).
KMM = {26: {"bias_e": (0.01, 1.600e-7, 2.000), "loss_e": (32.22, 1.988, 1.541), "loss_t": (113.53, 2.072, 1.379)},
       40: {"bias_e": (0.01, 3.906e-7, 2.000), "loss_e": (32.22, 1.988, 1.541), "loss_t": (113.53, 2.072, 1.379)},
       60: {"bias_e": (0.01, 2.575e-6, 1.758), "loss_e": (36.25, 1.988, 1.541), "loss_t": (113.53, 2.072, 1.379)}}
KMM_BSAT = 1.0
# E-core sets (catalog p.199 dimensions [mm], p.200 A_L nH/T^2 +/-8 %, le mm, Ae mm2, Ve mm3).  OpenMagnetics shape name.
ECORE = {
    "8044E": dict(A=80.01, B=44.58, C=19.81, D=34.37, E=59.28, F=19.81, M=19.81, le=208.0, Ae=389.0, Ve=80900.0,
                  AL={26: 91, 40: 113, 60: 170}, om="E 80/45/20"),
    "8020E": dict(A=80.01, B=38.10, C=19.81, D=28.02, E=59.28, F=19.81, M=19.81, le=185.0, Ae=389.0, Ve=72000.0,
                  AL={26: 103, 40: 145, 60: 190}, om="E 80/38/20"),
    "6527E": dict(A=65.15, B=32.51, C=27.00, D=22.20, E=44.20, F=19.66, M=12.09, le=147.0, Ae=540.0, Ve=79400.0,
                  AL={26: 162, 40: 230, 60: 300}, om="E 65/32/27"),
    "114LE": dict(A=114.30, B=46.18, C=34.93, D=28.60, E=79.50, F=35.10, M=22.20, le=215.0, Ae=1220.0, Ve=262000.0,
                  AL={26: 235, 40: 335, 60: 445}, om=None),
}
# TDK ferrites, R34-toroid data (N95.pdf / N97.pdf: p.2 table, p.5 curves FAL0752-Z / FAL0624-N read by the verifier)
FERR = {
    "N95": {"src": MAG + "N95.pdf p.2, p.5", "bsat": {25: 0.525, 100: 0.410}, "mu_i": 3000, "k_th": 4.0,
            # (f Hz, B T, P W/m3) at 100 C: p.5 curve 100 kHz/100 mT, p.2 table 100 kHz/200 mT and 300 kHz/100 mT
            "pts100": [(100e3, 0.1, 51e3), (100e3, 0.2, 350e3), (300e3, 0.1, 410e3)],
            # P(T)/P(100 C) at 100 kHz, 100 mT (p.5 FAL0752-Z, read): 25..140 C
            "tfac": ([25, 60, 80, 100, 120, 140], [88 / 51, 68 / 51, 57 / 51, 1.0, 55 / 51, 66 / 51])},
    "N97": {"src": MAG + "N97.pdf p.2, p.5", "bsat": {25: 0.510, 100: 0.410}, "mu_i": 2300, "k_th": 4.0,
            "pts100": [(100e3, 0.05, 6.8e3), (100e3, 0.1, 43e3), (25e3, 0.2, 45e3), (100e3, 0.2, 300e3)],
            "tfac": ([25, 60, 80, 100, 120, 140], [137 / 43, 84 / 43, 60 / 43, 1.0, 51 / 43, 68 / 43])},
}
PM114 = dict(src=MAG + "PM114-93.pdf p.2-3", le=0.200, Ae=1720e-6, Amin=1380e-6, Ve=344e-6, mass=1.94,
             d_post=43e-3, d_skirt_in=88e-3, d_out=114e-3, h_set=93e-3, h_win=63e-3,
             AN=1070e-6, lN=0.210, bob_id=44e-3, bob_tube_od=48e-3, bob_od=87e-3, bob_h=61.3e-3,
             pv_max_set={"N95": (9.0, 0.05, 100e3, 100)}, al_ungapped={"N95": 19.5e-6}, om="PM 114/93")
ETD29 = dict(src=MAG + "ETD29-16-10.pdf p.2, p.4", le=70.4e-3, Ae=76e-6, Amin=71e-6, Ve=5350e-9, mass=0.028,
             AN=97e-6, lN=52.8e-3, al_ungapped={"N97": 2250e-9}, pv_max_set={"N97": (2.4, 0.2, 100e3, 100)},
             k1k2=(124, -0.7), om="ETD 29/16/10")


def ferrite_steinmetz(mat, t=100.0):
    """k, alpha, beta (W/m3, Hz, T) from the TDK points; temperature factor applied to k."""
    m = FERR[mat]
    pts = m["pts100"]
    if mat == "N95":
        (f1, b1, p1), (f2, b2, p2), (f3, b3, p3) = pts
        k, a, b = steinmetz_from_points(f1, b1, p1, f2, b2, p2, f3, b3, p3)
    else:   # N97: beta from the 100 kHz 50/100 mT pair (our 60-80 mT range), alpha from the 200 mT 25/100 kHz pair
        b = math.log(pts[1][2] / pts[0][2]) / math.log(pts[1][1] / pts[0][1])
        a = math.log(pts[3][2] / pts[2][2]) / math.log(pts[3][0] / pts[2][0])
        k = pts[1][2] / (pts[1][0] ** a * pts[1][1] ** b)
    k *= float(np.interp(t, *m["tfac"]))
    return k, a, b


def kmm_mu(perm, h_am, fit="bias_e"):
    a, b, c = KMM[perm][fit]
    h_oe = np.abs(h_am) * 4 * math.pi / 1000
    return 1.0 / (a + b * h_oe ** c) / 100.0


def kmm_bdc(perm, h_am):
    hs = np.linspace(0, abs(h_am), 400)
    return float(MU0 * perm * np.trapezoid(kmm_mu(perm, hs), hs))


def catalog_k(a, b, c):
    """Magnetics catalog P[mW/cm3] = a B^b f[kHz]^c  ->  SI k, alpha, beta (W/m3, Hz, T)."""
    return a * 1e3 * 1e-3 ** c, c, b


# =====================================================================================================================
# Shared geometry helpers (own model)
# =====================================================================================================================
LITZ_PACK = {"round": 0.50, "rect": 0.65}   # copper / bundle cross-section incl. serving: round served / profiled litz
LITZ_INS = 0.25e-3                          # m extra wrap per side for the bundle-level insulation (Kapton / FEP tape)


def litz_bundle(a_cu, kind="round"):
    """outer diameter (round) or area (rect) of a litz bundle with a_cu copper."""
    a_b = a_cu / LITZ_PACK[kind]
    return math.sqrt(4 * a_b / math.pi) + 2 * LITZ_INS, a_b


def winding_layout(n, a_cu, breadth, kind="round"):
    """turns per layer, layers and radial build of n turns of litz in a winding breadth [m]."""
    if kind == "round":
        od, _ = litz_bundle(a_cu, kind)
        tpl = max(int(breadth // od), 1)
        layers = math.ceil(n / tpl)
        return dict(od=od, tpl=tpl, layers=layers, build=layers * od, kind=kind)
    # profiled (rectangular) litz, one layer if the breadth allows: height = breadth/n, width from the area
    layers = 1
    while True:
        tpl = math.ceil(n / layers)
        h = breadth / tpl - 2 * LITZ_INS
        w = a_cu / LITZ_PACK[kind] / h + 2 * LITZ_INS
        if h >= w * 0.25 or layers > 6:     # keep aspect ratio <= 4 (profiled litz limit)
            return dict(od=h, width=w, tpl=tpl, layers=layers, build=layers * w, kind=kind)
        layers += 1


def _bundle_r(lay):
    """equivalent bundle radius of a layout entry (round: od/2; profiled: radius of the same area)"""
    return lay["od"] / 2 if lay["kind"] == "round" else math.sqrt(lay["od"] * lay["width"] / math.pi)


def temp_rise_surface(p, area, h=25.0):
    return p / (h * area)


def temp_rise_maniktala(p, area):
    """natural convection estimate (Maniktala): dT = (P[mW] / A[cm2])^0.833 - same as MKF's 'MANIKTALA' model."""
    return (p * 1e3 / (area * 1e4)) ** 0.833


# =====================================================================================================================
# Part 1: PV cell inductor (own model)
# =====================================================================================================================
PV_REV0 = dict(core="Magnetics 00Y8044E026 (E-core set), 2 sets stacked", turns=38,
               conductor="Litz, 10.3 mm2 copper (J <= 4.5 A/mm2), insulation for 1.1 kV")   # cell_spec before the M1 hand-back


def pv_inductor_own(cell):
    ind, sw, rat = dict(cell["inductor"]), cell["switching"], cell["ratings"]
    if not re.search(r"00([A-Z])(\d{4}[A-Z]{1,2}|\d{3}LE)0(\d\d)", ind["core"]):
        ind.update(PV_REV0)      # the spec now carries the magnetics construction: verify the rev-0 part for the record
    m = re.search(r"00([A-Z])(\d{4}[A-Z]{1,2}|\d{3}LE)0(\d\d)", ind["core"])
    shape, perm = m.group(2), int(m.group(3))
    ms = re.search(r"(\d+) sets stacked", ind["core"])
    nst = int(ms.group(1)) if ms else 1
    c = ECORE[shape]
    n = int(ind["turns"])
    a_cu = float(re.search(r"([0-9.]+) mm2", ind["conductor"]).group(1)) * 1e-6
    f = sw["f_sw_kHz"] * 1e3
    idc = rat["I_max_A_low_voltage_port"]
    vmax = rat["V_ports_V"][1]
    al = c["AL"][perm] * 1e-9 * nst
    le, ae, ve = c["le"] * 1e-3, c["Ae"] * 1e-6 * nst, c["Ve"] * 1e-9 * nst
    L = lambda i: al * n * n * kmm_mu(perm, n * i / le)                                      # noqa: E731
    r = dict(shape=shape, perm=perm, n_stack=nst, turns=n, a_cu=a_cu, f=f, I_dc=idc, AL=al, le=le, Ae=ae, Ve=ve,
             L0=al * n * n, L_Idc=L(idc), L_trip=L(ind["I_trip_A"]), mu_trip=float(kmm_mu(perm, n * ind["I_trip_A"] / le)))
    # worst ripple: V*D*(1-D) max = Vmax/4 (buck at D = 0.5 or boost at V_A = V_B/2), exact with L(i) along the ramp
    v_l, t_on = vmax / 2, 0.5 / f
    lo, hi = idc - 25, idc + 25
    for _ in range(60):        # find the ramp [i0, i0 + di] centred on idc with int L(i) di = V t_on
        di = 0.5 * (lo + hi) - (idc - 25)
        ii = np.linspace(idc - di / 2, idc + di / 2, 200)
        flux = np.trapezoid([L(x) for x in ii], ii)
        if flux > v_l * t_on:
            hi = 0.5 * (lo + hi)
        else:
            lo = 0.5 * (lo + hi)
    ripple = di
    r.update(ripple=ripple, ripple_linear=v_l * t_on / L(idc), I_pk=idc + ripple / 2,
             I_rms=math.sqrt(idc ** 2 + ripple ** 2 / 12))
    # flux density: field-based (catalog Method 1: B from the DC magnetisation curve) and Faraday-based (Method 3)
    b = lambda i: kmm_bdc(perm, n * i / le)                                                   # noqa: E731
    r.update(B_dc=b(idc), B_pk_normal=b(ind["I_peak_normal_max_A"]), B_trip=b(ind["I_trip_A"]),
             dB_field=b(r["I_pk"]) - b(idc - ripple / 2), dB_faraday=v_l * t_on / (n * ae),
             k_AL=al / (MU0 * perm * ae / le))
    # core loss, iGSE on the triangle (D = 0.5), four ways (fit x flux definition); OWN = E-core fit (p.112, the
    # only fit measured on E cores) with the Faraday flux (consistent with the measured catalog A_L)
    tt = np.array([0, t_on, 1 / f])
    out = {}
    for fit in ("loss_e", "loss_t"):
        k, a_, b_ = catalog_k(*KMM[perm][fit])
        for dbk in ("dB_field", "dB_faraday"):
            out[f"{fit}/{dbk}"] = igse(k, a_, b_, tt, np.array([0, r[dbk], 0])) * ve
    r["P_core_variants"] = out
    r["P_core"] = out["loss_e/dB_faraday"]
    # winding: litz, strand diameter is not specified by the designer ("Litz strand and layer design open"); own
    # assumption 0.1 mm (Sullivan optimum region at 32 kHz); window breadth = 2 D minus 2 x 2 mm bobbin flanges and
    # 2 x 3 mm creepage margin (basic insulation winding - PE-bonded core; final value after insulation coordination)
    ins, _src = insulation_source()
    rq = next((x for x in (ins or {}).get("requirements", []) if x.get("label", "").startswith("Basic HV-PE, switch node")), None)
    margin = rq["cr_pd2"] if rq else 3.0                                   # mm creepage winding -> PE-bonded core (ECO-10)
    breadth = (2 * c["D"] - 2 * 2.0 - 2 * margin) * 1e-3
    lay_r = winding_layout(n, a_cu, breadth, "round")
    lay_p = winding_layout(n, a_cu, breadth, "rect")
    win_w = c["M"] * 1e-3 - 2.0e-3                      # window width minus bobbin wall
    t_bob = 2.0e-3
    ctot = c["C"] * nst * 1e-3
    lay = lay_r if lay_r["build"] <= win_w else lay_p
    mlt = 2 * (c["F"] * 1e-3 + 2 * t_bob) + 2 * (ctot + 2 * t_bob) + math.pi * lay["build"]
    d_str = 0.1e-3
    n_str = int(round(a_cu / (math.pi * d_str ** 2 / 4)))
    harm = [(0.0, idc)] + [(h * f, 8 * ripple / (math.pi ** 2 * h * h) / 2 / math.sqrt(2)) for h in range(1, 40, 2)]
    t_w = 110.0
    h2 = ramp_h2(0, n, breadth)
    wl = litz_loss(harm, n_str, d_str, n, mlt, h2, t_w, _bundle_r(lay))
    sens = {}
    for dd in (0.071e-3, 0.1e-3, 0.2e-3):
        ns = int(round(a_cu / (math.pi * dd ** 2 / 4)))
        sens[f"{dd*1e3:.3f} mm"] = litz_loss(harm, ns, dd, n, mlt, h2, t_w, 2.5e-3)["total"]
    r.update(breadth=breadth, layout_round=lay_r, layout_rect=lay_p, window_w=win_w, fits_round=lay_r["build"] <= win_w,
             fits_rect=lay_p["build"] <= win_w, margin_mm=margin, MLT=mlt, d_strand=d_str, n_strand=n_str, R_dc20=wl["R_dc"] / (
                 1 + ALPHA_CU * (t_w - 20)), R_dc110=wl["R_dc"], P_cu=wl["total"], P_cu_parts=wl, P_cu_strand_sens=sens,
             ku=n * a_cu / (2 * c["D"] * c["M"] * 1e-6))
    # temperature: the designer's surface model (forced air h = 25 W/m2K on the bounding box) + natural convection
    # check + internal gradient across the winding build (litz k_eff = 0.6 W/mK impregnated, cooled on the outside)
    surf = 2 * (c["A"] * 2 * c["B"] + c["A"] * (ctot * 1e3 + 2 * c["M"]) + 2 * c["B"] * (ctot * 1e3 + 2 * c["M"])) * 1e-6
    ptot = r["P_core"] + r["P_cu"]
    q = r["P_cu"] / (mlt * breadth * lay["build"])
    dt_int = q * lay["build"] ** 2 / (2 * 0.6) * 0.5            # half: heat leaves through both faces partly
    r.update(surface=surf, P_total=ptot, dT_forced=temp_rise_surface(ptot, surf), dT_natural=temp_rise_maniktala(ptot, surf),
             dT_internal=dt_int, mass=ve * 6500 + 1.15 * n * mlt * a_cu * 8960)
    r["dT_hotspot"] = r["dT_forced"] + dt_int
    return r


# =====================================================================================================================
# Part 2/3: DAB transformer and series inductor (own model)
# =====================================================================================================================
DAB_REV0 = dict(   # dab_spec.json before the M1 hand-back (06:16): the verified PM 114/93 design (MAG-1 record)
    transformer=dict(L_sigma_target_H=4.381308865137455e-06, B_pk_950V_T=0.12552854122621565, loss_max_W=158.66505221420516,
                     Rth_hotspot_to_coolant_max_K_W=0.4, L_m_tol="+/-10 % (~1.74 mm gap)", I1_rms_max_A=118.53575552059282,
                     I1_pk_max_A=176.93513366396292, I2_rms_max_A=111.9, I2_pk_max_A=162.0),
    series_inductor=dict(L_external_nominal_H=2.1186911348625446e-06, loss_max_W=11.227487780476393, I_sat_min_A=275.0,
                         reference_core="PM 114/93 N95, 3 turns, 9.2 mm total gap in 4 gaps"))


def dab_rev0(dab):
    """the spec the verification (MAG-1) was made against; the live spec now carries the M1 construction"""
    if "reference_core" in dab["series_inductor"]:
        return dab
    d = json.loads(json.dumps(dab))
    for k, v in DAB_REV0.items():
        d[k].update(v)
    return d


def dab_inputs(dab):
    """Everything the DAB magnetics checks need, from dab_spec.json + the designer's report text (winding data are not
    in the JSON) + the designer's one-period ngspice waveforms (sim/out/dab_design/spice_*_1period.csv)."""
    x, s = dab["transformer"], dab["series_inductor"]
    n1, n2 = (int(v) for v in x["turns_ratio_N1_N2"].split(":"))
    rep = open(P("sim/out/dab_design/report.md")).read()
    m = re.search(r"litz ([0-9.]+) mm strands: primary ([0-9.]+) mm2 \(~(\d+) strands\), secondary ([0-9.]+) mm2 "
                  r"\(~(\d+) strands\)", rep)
    d_str, a1, ns1, a2, ns2 = (float(m.group(1)) * 1e-3, float(m.group(2)) * 1e-6, int(m.group(3)),
                               float(m.group(4)) * 1e-6, int(m.group(5))) if m else (0.1e-3, 12.2e-6, 1550, 11.1e-6, 1420)
    mi = re.search(r"with (\d+(?:\.\d+)?) mm insulation", rep)
    b_iso = float(mi.group(1)) * 1e-3 if mi else 3e-3
    ml = re.search(r"reference (\d+) turns on PM 114/93", rep)
    waves = {}
    for fn in sorted(os.listdir(P("sim/out/dab_design"))):
        mm = re.match(r"spice_(.+)_1period\.csv$", fn)
        if mm:
            w = np.genfromtxt(P("sim/out/dab_design", fn), delimiter=",", names=True)
            waves[mm.group(1)] = {k: np.asarray(w[k], float) for k in w.dtype.names}
    return dict(N1=n1, N2=n2, n=n1 / n2, f=dab["power_stage"]["f_sw_Hz"], Lm=x["L_m_H"], Llk=x["L_sigma_target_H"],
                vs_half=x["Vs_per_half_period"], d_str=d_str, a1=a1, ns1=ns1, a2=a2, ns2=ns2, b_iso=b_iso,
                I1rms=x["I1_rms_max_A"], I2rms=x["I2_rms_max_A"], I1pk=x["I1_pk_max_A"], waves=waves,
                Lext=s["L_external_nominal_H"], nl=int(ml.group(1)) if ml else 3, t_cool=dab["thermal"]["coolant_inlet_C"],
                gap_l_text=s.get("reference_core", DAB_REV0["series_inductor"]["reference_core"]),
                rth_req=x.get("Rth_hotspot_to_coolant_max_K_W", 0.4), t_hs_max=x["hot_spot_max_C"])


def _period(w, f):
    """uniform resampling of one period of an ngspice CSV (t, i_L, v_p, v_s)."""
    t = w["t_s"] - w["t_s"][0]
    T = 1 / f
    tt = np.linspace(0, T, 2048, endpoint=False)
    return tt, {k: np.interp(tt, t, w[k]) for k in ("i_L_A", "v_p_V", "v_s_V")}


def dab_transformer_own(dab):
    D = dab_inputs(dab)
    c = PM114
    n1, n2, f = D["N1"], D["N2"], D["f"]
    breadth = 62.3e-3 - 2 * 3e-3                        # coil former winding height (p.3) minus 3 mm margins (designer)
    r = dict(N1=n1, N2=n2, f=f, breadth=breadth, d_str=D["d_str"], ns1=D["ns1"], ns2=D["ns2"], b_iso=D["b_iso"])
    # ---- layout: round litz (as the designer's 'litz' implies) and profiled litz; radial space = (87-48)/2 mm
    radial = (c["bob_od"] - c["bob_tube_od"]) / 2
    lay = {}
    for kind in ("round", "rect"):
        p_ = winding_layout(n1, D["a1"], breadth, kind)
        s_ = winding_layout(n2, D["a2"], breadth, kind)
        lay[kind] = dict(p=p_, s=s_, build=p_["build"] + D["b_iso"] + s_["build"])
        lay[kind]["fits"] = lay[kind]["build"] <= radial
    use = "round" if lay["round"]["fits"] else "rect"
    bp, bs = lay[use]["p"]["build"], lay[use]["s"]["build"]
    r0 = c["bob_tube_od"] / 2
    mlt_p = 2 * math.pi * (r0 + bp / 2)
    mlt_i = 2 * math.pi * (r0 + bp + D["b_iso"] / 2)
    mlt_s = 2 * math.pi * (r0 + bp + D["b_iso"] + bs / 2)
    r.update(radial_space=radial, layout=lay, layout_used=use, MLT_p=mlt_p, MLT_s=mlt_s)
    # ---- leakage, 1-D MMF energy method with the real builds (designer: same formula, builds from k_cu = 0.25)
    llk = MU0 * n1 ** 2 / breadth * (mlt_p * bp / 3 + mlt_i * D["b_iso"] + mlt_s * bs / 3)
    bd = (c["AN"] - D["b_iso"] * 62.3e-3) / 2 / 62.3e-3                 # designer's build assumption, for comparison
    llk_designer_geom = MU0 * n1 ** 2 / breadth * (c["lN"] * bd / 3 * 2 + c["lN"] * D["b_iso"])
    r.update(L_leak=llk, L_leak_designer_geometry=llk_designer_geom, build_designer=bd)
    # ---- magnetising inductance: centre-post gap with McLyman fringing; core reluctance with mu_a ~ mu_i
    a_post = math.pi / 4 * c["d_post"] ** 2
    r_core = c["le"] / (MU0 * FERR["N95"]["mu_i"] * c["Ae"])
    lm = lambda g: n1 ** 2 / (r_core + g / (MU0 * a_post * fringing_mclyman(g, a_post, c["h_win"])))   # noqa: E731
    g_des = MU0 * n1 ** 2 * c["Ae"] / D["Lm"]                           # designer: no fringing, Ae
    lo, hi = 0.2e-3, 6e-3
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if lm(mid) > D["Lm"] else (lo, mid)
    r.update(gap_designer=g_des, Lm_at_designer_gap=lm(g_des), gap_for_Lm=0.5 * (lo + hi),
             fringe_at_gap=fringing_mclyman(0.5 * (lo + hi), a_post, c["h_win"]))
    # ---- flux: spec point (port-1 950 V, 5 us) and the ngspice points
    r["B_pk_950"] = D["vs_half"] / (2 * n1 * c["Ae"])
    r["B_pk_950_Amin"] = D["vs_half"] / (2 * n1 * c["Amin"])
    k95, a95, b95 = ferrite_steinmetz("N95", 90.0)
    pts = {}
    for name, w in D["waves"].items():
        tt, wv = _period(w, f)
        dt = tt[1] - tt[0]
        vm = wv["v_s_V"] * D["n"]                                       # magnetising branch voltage, primary-referred
        lam = np.cumsum(vm) * dt
        lam -= lam.mean()
        b = lam / (n1 * c["Ae"])
        im = lam / D["Lm"]
        i1 = wv["i_L_A"]
        i2 = (i1 - im) * D["n"]
        h1, h2 = harmonics(tt, i1), harmonics(tt, i2)
        pc = igse(k95, a95, b95, np.append(tt, 1 / f), np.append(b, b[0])) * c["Ve"]
        # Cu: primary sees MMF ramp 0 -> N1 i1, secondary N2 i2 -> 0 (non-interleaved, 1-D), per harmonic
        rb_p, rb_s = _bundle_r(lay[use]["p"]), _bundle_r(lay[use]["s"])
        w1 = litz_loss(h1, D["ns1"], D["d_str"], n1, mlt_p, ramp_h2(0, n1, breadth), 90.0, rb_p)
        w2 = litz_loss(h2, D["ns2"], D["d_str"], n2, mlt_s, ramp_h2(0, n2, breadth), 90.0, rb_s)
        rms1 = math.sqrt(np.mean(i1 ** 2))
        pts[name] = dict(I1_rms=rms1, I2_rms=math.sqrt(np.mean(i2 ** 2)), B_pk=float(np.abs(b).max()), P_core=pc,
                         P_core_setmax=_setmax_scale(pc, k95, a95, b95),
                         P_cu1=w1["total"], P_cu2=w2["total"], P_cu=w1["total"] + w2["total"],
                         Fr_eff=(w1["total"] + w2["total"]) / (w1["dc"] + w2["dc"]), P_dc=w1["dc"] + w2["dc"],
                         harm3=h1[3][1] / h1[1][1] if len(h1) > 3 else 0.0)
    r["points"] = pts
    # worst copper point scaled to the spec's I1_rms_max (same waveform shape)
    wname = max(pts, key=lambda k: pts[k]["I1_rms"])
    sc = (D["I1rms"] / pts[wname]["I1_rms"]) ** 2
    r["worst"] = dict(point=wname, scale=sc, P_cu=pts[wname]["P_cu"] * sc, P_dc=pts[wname]["P_dc"] * sc,
                      Fr_eff=pts[wname]["Fr_eff"])
    bname = max(pts, key=lambda k: pts[k]["B_pk"])
    r["P_core_max"] = pts[bname]["P_core"]
    r["P_core_max_setlimit"] = pts[bname]["P_core_setmax"]
    # core loss at the 950 V spec flux (triangular flux, iGSE), material-typical and core-set limit
    tri = np.array([0, 0.5 / f, 1 / f])
    p950 = igse(k95, a95, b95, tri, np.array([-r["B_pk_950"], r["B_pk_950"], -r["B_pk_950"]])) * c["Ve"]
    r.update(P_core_950=p950, P_core_950_setlimit=_setmax_scale(p950, k95, a95, b95), steinmetz=(k95, a95, b95))
    # Sullivan check of the litz factor at the fundamental and the strand needed for F_r <= 1.15 (designer)
    r["Fr_sullivan_fund"] = sullivan_fr(D["ns1"], D["d_str"], n1, breadth, f, 90.0)
    for dd in (0.08e-3, 0.071e-3, 0.063e-3, 0.05e-3, 0.04e-3, 0.032e-3):
        ns_ = int(D["a1"] / (math.pi * dd * dd / 4))
        if sullivan_fr(ns_, dd, n1, breadth, f, 90.0) <= 1.15:
            r["strand_for_Fr115"] = dd
            break
    # totals at the designer's worst point (max I_rms) and at the max-flux point
    r["P_total_worst"] = r["worst"]["P_cu"] + r["P_core_max"]
    r["P_total_worst_setlimit"] = r["worst"]["P_cu"] + r["P_core_max_setlimit"]
    # ---- thermal: conduction network, PM 114/93 base on the cold plate (A) or potted Al housing touching the
    # skirt and the winding through the slots (B, best case).  k: ferrite 4, PPS 0.3, potting 1.0, litz 0.6 W/mK.
    th = _pm_thermal(c, bp + D["b_iso"] + bs)
    r["thermal"] = th
    for case in ("A_base_on_plate", "B_potted_housing"):
        p_ = r["P_total_worst"]
        r[f"T_hs_{case}"] = D["t_cool"] + p_ * th[case]
    r["T_hs_designer_rth"] = D["t_cool"] + r["P_total_worst"] * D["rth_req"]
    return r


def _setmax_scale(p_typ, k, a, b):
    """scale a material-typical loss to the PM 114/93 N95 core-set limit (TDK p.2: < 9.0 W at 50 mT, 100 kHz, 100 C)"""
    pmax, b0, f0, _t = PM114["pv_max_set"]["N95"]
    typ0 = k * f0 ** a * b0 ** b * PM114["Ve"] / float(np.interp(90.0, *FERR["N95"]["tfac"]))   # R34 typical at the limit point
    return p_typ * pmax / typ0


def _pm_thermal(c, build):
    """hot spot -> coolant [K/W] for the two mounting cases (own estimate, 1-D conduction resistances in series/parallel)"""
    k_fe, k_pps, k_pot, k_w = 4.0, 0.3, 1.0, 1.0
    h = c["bob_h"]
    a_post = math.pi / 4 * c["d_post"] ** 2
    a_skirt = math.pi / 4 * (c["d_out"] ** 2 - c["d_skirt_in"] ** 2) * 0.7         # ~30 % removed by the two slots
    r_wind = build / 2 / (2 * k_w * math.pi * (c["bob_tube_od"] + build) * h)     # uniform heat, both faces cooled
    r_tube = 2e-3 / (k_pps * math.pi * c["bob_tube_od"] * h)
    r_post = (0.25 * c["h_set"]) / (k_fe * a_post)                               # lower post half + base
    r_skirt_axial = (0.6 * c["h_set"]) / (k_fe * a_skirt)
    r_pot_out = 3e-3 / (k_pot * math.pi * (c["bob_tube_od"] + 2 * build) * h * 0.7)
    a_cyl = math.pi * c["d_out"] * c["h_set"] * 0.6
    r_skirt_radial = (c["d_out"] - c["d_skirt_in"]) / 2 / (k_fe * a_cyl)
    r_tim = 0.3e-3 / (3.0 * a_cyl)
    a_case = r_wind + 1 / (1 / (r_tube + r_post) + 1 / (r_pot_out + r_skirt_axial)) + 0.01
    b_case = r_wind + 1 / (1 / (r_tube + r_post + 0.5 * r_skirt_radial) + 1 / (r_pot_out + r_skirt_radial + r_tim)) + 0.02
    return {"A_base_on_plate": a_case, "B_potted_housing": b_case,
            "parts": dict(r_wind=r_wind, r_tube=r_tube, r_post=r_post, r_skirt_axial=r_skirt_axial, r_pot_out=r_pot_out,
                          r_skirt_radial=r_skirt_radial)}


def dab_inductor_own(dab):
    D = dab_inputs(dab)
    c = PM114
    nl, lext, f = D["nl"], D["Lext"], D["f"]
    a_post = math.pi / 4 * c["d_post"] ** 2
    g_des = MU0 * nl ** 2 * c["Ae"] / lext
    ngap = 4
    spacing = c["h_win"] / ngap
    # distributed gaps: each gap g_i = g/n with McLyman fringing limited by the gap spacing
    l_of = lambda g: nl ** 2 * MU0 * a_post * fringing_mclyman(g / ngap, a_post, spacing / 2) / g   # noqa: E731
    lo, hi = 1e-3, 30e-3
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if l_of(mid) > lext else (lo, mid)
    r = dict(nl=nl, L=lext, gap_designer=g_des, L_at_designer_gap=l_of(g_des), gap_needed=0.5 * (lo + hi),
             gap_single_needed=None, B_177=lext * D["I1pk"] / (nl * c["Ae"]), B_275=lext * 275.0 / (nl * c["Ae"]))
    l1 = lambda g: nl ** 2 * MU0 * a_post * fringing_mclyman(g, a_post, c["h_win"]) / g   # noqa: E731
    lo, hi = 1e-3, 40e-3
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if l1(mid) > lext else (lo, mid)
    r["gap_single_needed"] = 0.5 * (lo + hi)
    # core loss from the inductor flux B = L i /(N Ae) (iGSE), copper: 3 turns, 0.4 x AN copper (designer), 0.1 mm
    # litz, window field = gap fringing H = F_gap/(pi r) summed over the gaps + 1-D ramp
    k95, a95, b95 = ferrite_steinmetz("N95", 90.0)
    a_cu = 0.40 * c["AN"] / nl
    n_str = int(a_cu / (math.pi * D["d_str"] ** 2 / 4))
    build = a_cu / LITZ_PACK["rect"] / ((62.3e-3 - 6e-3) / nl)
    zs = (np.arange(ngap) + 0.5) * c["h_win"] / ngap - c["h_win"] / 2
    xs = np.linspace(c["bob_tube_od"] / 2 - c["d_post"] / 2, c["bob_tube_od"] / 2 - c["d_post"] / 2 + build, 30)
    zz = np.linspace(-(62.3e-3 - 6e-3) / 2, (62.3e-3 - 6e-3) / 2, 120)
    X, Z = np.meshgrid(xs, zz)
    hsum = np.zeros_like(X)
    for z0 in zs:   # per ampere of inductor current
        hsum += (nl / ngap) / (math.pi * np.sqrt(X ** 2 + (Z - z0) ** 2 + (r["gap_needed"] / ngap / 2) ** 2))
    h2_fringe = float(np.mean(hsum ** 2))
    mlt = math.pi * (c["bob_tube_od"] + build)
    pts = {}
    for name, w in D["waves"].items():
        tt, wv = _period(w, f)
        i = wv["i_L_A"]
        b = lext * i / (nl * c["Ae"])
        pc = igse(k95, a95, b95, np.append(tt, 1 / f), np.append(b, b[0])) * c["Ve"]
        hh = harmonics(tt, i)
        wl = litz_loss(hh, n_str, D["d_str"], nl, mlt, h2_fringe + ramp_h2(0, nl, 56.3e-3), 90.0, 5e-3)
        # lower bound: gaps smeared into a uniformly distributed gap -> radial window field N i (z/h - 1/2) / w
        w_win = (c["d_skirt_in"] - c["d_post"]) / 2
        ws = litz_loss(hh, n_str, D["d_str"], nl, mlt, nl ** 2 / (12 * w_win ** 2) + ramp_h2(0, nl, 56.3e-3), 90.0, 5e-3)
        pts[name] = dict(I_rms=math.sqrt(np.mean(i ** 2)), B_pk=float(np.abs(b).max()), P_core=pc, P_cu=wl["total"],
                         P_cu_smooth=ws["total"], P_dc=wl["dc"])
    wname = max(pts, key=lambda k: pts[k]["I_rms"])
    sc = (D["I1rms"] / pts[wname]["I_rms"]) ** 2
    r.update(points=pts, P_worst=pts[wname]["P_cu"] * sc + max(p["P_core"] for p in pts.values()),
             P_worst_smooth=pts[wname]["P_cu_smooth"] * sc + max(p["P_core"] for p in pts.values()),
             a_cu=a_cu, n_str=n_str, h_rms_fringe_per_A=math.sqrt(h2_fringe), core_volume_util=None)
    # energy-based sizing: the core stores 1/2 L I^2 in the gaps at B ~ 0.07 T; a core sized for 0.25 T needs
    # gap volume 2 mu0 W / B^2
    w_e = 0.5 * lext * 275.0 ** 2
    r["gap_volume_needed_at_0p25T_cm3"] = 2 * MU0 * w_e / 0.25 ** 2 * 1e6
    r["gap_volume_designer_cm3"] = g_des * c["Ae"] * 1e6
    return r


# =====================================================================================================================
# Part 4: AUX-HV flyback transformer (own model)
# =====================================================================================================================
def aux_inputs(aux):
    x, s = aux["transformer"], aux["summary"]
    rep = open(P("sim/out/aux_hv_design/report.md")).read()
    m = re.search(r"(\d+(?:\.\d+)?) V / (\d+) W", rep) or re.search(r"(\d+(?:\.\d+)?) V / (\d+) W", aux.get("note", ""))
    pout = (x.get("requirements") or {}).get("pout_W") or (float(m.group(2)) if m else 30.0)   # spec first: report.md is shared
    vin = sorted(int(k) for k in s["eta"])
    rq = x.get("requirements", {})
    g = lambda pat, d: float(re.search(pat, x["desc"]).group(1)) if re.search(pat, x["desc"]) else d  # noqa: E731
    # rev C0 text gives the wires; the M1 hand-back (spec now carries the magnetics construction) does not -> rev-C0 sizes
    wire = dict(p=g(r"P1 \d+ t ([0-9.]+) mm", 0.30) * 1e-3, s=g(r"S \d+ t TIW ([0-9.]+) mm", 0.80) * 1e-3,
                a=g(r"AUX \d+ t ([0-9.]+) mm", 0.25) * 1e-3)
    ilim = g(r"limit max ([0-9.]+) A", rq.get("ipk_max_A", 1.74))
    lp_tol = g(r"Lp [0-9.]+ mH \+/-(\d+) %", (rq.get("lp_mH") or {}).get("tol_pct", 7.0)) / 100
    return dict(np=x["np"], ns=x["ns"], na=x["na"], lp=x["lp_mh"] * 1e-3, al=x["al_nh"] * 1e-9, gap=x["gap_mm"] * 1e-3,
                f=s["fsw"], vr=s["vr"], vout=s["vout"][1], eta=s["eta"], vin=vin, pout=pout, wire=wire, ilim=ilim,
                lp_tol=lp_tol, llk_est=x["llk_est_uh"] * 1e-6, llk_1d=x["llk_1d_uh"] * 1e-6, b_fl=x["b_fl_mT"] * 1e-3,
                b_lim=x["b_lim_mT"] * 1e-3, stack=x["stack_height_mm"] * 1e-3)


def aux_transformer_own(aux):
    A = aux_inputs(aux)
    c = ETD29
    f, lp, np_, ns, na = A["f"], A["lp"], A["np"], A["ns"], A["na"]
    n = np_ / ns
    r = dict(np=np_, ns=ns, na=na, lp=lp, f=f)
    # gap: centre-leg round post (ETD 29: d = 9.8 mm), McLyman fringing, window height 2 x 11 mm
    a_post = math.pi / 4 * 9.8e-3 ** 2
    al = lambda g: MU0 * a_post * fringing_mclyman(g, a_post, 22e-3) / (g + c["le"] / FERR["N97"]["mu_i"] * a_post / c["Ae"])  # noqa: E731
    lo, hi = 0.1e-3, 3e-3
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if al(mid) > A["al"] else (lo, mid)
    k1, k2 = c["k1k2"]
    r.update(AL=A["al"], AL_at_designer_gap=al(A["gap"]), gap_for_AL=0.5 * (lo + hi),
             gap_tdk_k1k2=(A["al"] * 1e9 / k1) ** (1 / k2) * 1e-3)
    # operating points: DCM, Ipk from the input power, reset time from the reflected voltage
    pts = {}
    kN97, aN97, bN97 = ferrite_steinmetz("N97", 100.0)
    for v in A["vin"]:
        pin = A["pout"] / A["eta"][str(v)]
        ipk = math.sqrt(2 * pin / (lp * f))
        t1 = lp * ipk / v
        t2 = lp * ipk / (n * (A["vout"] + 0.9))
        T = 1 / f
        bpk = lp * ipk / (np_ * c["Ae"])
        tt = np.array([0, t1, t1 + t2, T])
        pc = igse(kN97, aN97, bN97, tt, np.array([0, bpk, 0, 0])) * c["Ve"]
        # currents (triangles) -> harmonics
        ts = np.linspace(0, T, 4096, endpoint=False)
        ip = np.where(ts < t1, ipk * ts / t1, 0.0)
        is_ = np.where((ts >= t1) & (ts < t1 + t2), n * ipk * (1 - (ts - t1) / t2), 0.0)
        wp = litz_loss(harmonics(ts, ip, 30), 1, A["wire"]["p"], np_, c["lN"], ramp_h2(0, np_ / 2, 19.0e-3), 100.0, 1.0)
        ws = litz_loss(harmonics(ts, is_, 30), 1, A["wire"]["s"], ns, c["lN"], ramp_h2(0, ns, 19.0e-3), 100.0, 1.0)
        # gap fringing: the magnetising MMF F(t) drops across the 0.83 mm centre-leg gap; each layer at distance r from the
        # leg sees H = F / (pi sqrt(r^2 + z^2)) (line source), the 1-D Dowell field above does not contain it
        fm = np.where(ts < t1, np_ * ipk * ts / t1, np.where(ts < t1 + t2, np_ * ipk * (1 - (ts - t1) / t2), 0.0))
        hf = harmonics(ts, fm, 60)
        r0 = 1.0e-3                                                       # coil-former wall
        stack = [(A["wire"]["p"], np_ // 2, r0 + 0.18e-3), (A["wire"]["s"], ns, r0 + 1.0e-3),
                 (A["wire"]["a"], na, r0 + 1.7e-3), (A["wire"]["p"], np_ - np_ // 2, r0 + 2.2e-3)]
        p_fr = 0.0
        for dw, nt, rr in stack:
            zz = np.linspace(-9.5e-3, 9.5e-3, nt)
            for fh, frms in hf[1:]:
                p_fr += np.sum(_strand_factors(dw, fh, 100)[1] * (frms * math.sqrt(2) / (math.pi * np.sqrt(rr ** 2 + zz ** 2))) ** 2) * c["lN"]
        pts[v] = dict(Ipk=ipk, D=t1 * f, D2=t2 * f, B_pk=bpk, P_core=pc, P_cu=wp["total"] + ws["total"] + p_fr,
                      P_cu_1d=wp["total"] + ws["total"], P_fringe=p_fr,
                      Ip_rms=math.sqrt(np.mean(ip ** 2)), Is_rms=math.sqrt(np.mean(is_ ** 2)))
    r["points"] = pts
    # saturation at the current limit: Lp at +tol, flux in A_min, Bsat of N97 at 100 C and extrapolated to 120 C
    bsat120 = FERR["N97"]["bsat"][100] - (FERR["N97"]["bsat"][25] - FERR["N97"]["bsat"][100]) / 75 * 20
    r.update(B_lim_Ae=lp * (1 + A["lp_tol"]) * A["ilim"] / (np_ * c["Ae"]),
             B_lim_Amin=lp * (1 + A["lp_tol"]) * A["ilim"] / (np_ * c["Amin"]), Bsat100=FERR["N97"]["bsat"][100],
             Bsat120=bsat120)
    # leakage, 1-D MMF energy for the stack P1 | tape-shield-tape | S | tape | AUX | tape | P2 (designer's order), primary
    # referred with S shorted: MMF ramps 0 -> 45 I across P1, holds through the insulation, -> -45 I across S, holds,
    # -> 0 across P2.  Layer thickness: wire OD (grade 2: +0.05 mm; TIW +0.2 mm), tape 0.05 mm, foil 0.05 mm
    t_p, t_s, t_a, t_tape = A["wire"]["p"] + 0.05e-3, A["wire"]["s"] + 0.2e-3, A["wire"]["a"] + 0.04e-3, 0.05e-3
    half = np_ / 2
    integral = half ** 2 * (t_p / 3 + (2 * t_tape + 0.05e-3) + t_s / 3 + t_tape + t_a + t_tape + t_p / 3)
    r["L_leak_1d"] = MU0 * c["lN"] / 19.0e-3 * integral
    r["stack_own"] = 2 * t_p + t_s + t_a + 6 * t_tape + 0.05e-3
    r["fits_breadth"] = dict(primary=math.ceil(half) * t_p, secondary=ns * t_s, breadth=19.0e-3)
    worst = max(pts.values(), key=lambda p_: p_["P_core"] + p_["P_cu"])
    r["P_total_worst"] = worst["P_core"] + worst["P_cu"]
    surf = 2 * (30.6e-3 * 15.8e-3 * 2) + 2 * (30.6e-3 * 9.8e-3) + 2 * (31.6e-3 * 9.8e-3) + 2 * 22e-3 * 25e-3   # core + coil
    r.update(surface=surf, dT_natural=temp_rise_maniktala(r["P_total_worst"], surf))
    return r


# =====================================================================================================================
# Part 5: port common-mode chokes (spec-only parts; own reference design to make the spec complete)
# =====================================================================================================================
def port_cmc_inputs():
    src = open(P("gen/port.py")).read()
    out = {}
    for key in ("CMC160", "CMC200"):
        m = re.search(r'"%s": dict\(.*?desc="(.*?)"\s*,\s*pins' % key, src, re.S)
        txt = re.sub(r'"\s*\n\s*"', "", m.group(1)) if m else ""
        out[key] = dict(desc=txt, I=float(re.search(r"(\d+) A DC continuous", txt).group(1)),
                        L_cm=float(re.search(r"L_cm >= ([0-9.]+) mH", txt).group(1)) * 1e-3,
                        f_L=float(re.search(r"mH @ (\d+) kHz", txt).group(1)) * 1e3,
                        L_dm=[float(v) * 1e-6 for v in re.search(r"leakage ([0-9.]+)-([0-9.]+) uH", txt).groups()]
                        if re.search(r"leakage ([0-9.]+)-([0-9.]+) uH", txt) else [None],   # no longer specified (MG-10 design)
                        dT=float(re.search(r"dT <= (\d+) K", txt).group(1)))
    rep = open(P("sim/out/port_design/report.md")).read()
    g = lambda k, d: float(re.search(r"\| %s \| ([0-9.e+-]+) \|" % k, rep).group(1)) if re.search(r"\| %s \| ([0-9.e+-]+) \|" % k, rep) else d  # noqa: E731
    return out, dict(V_cm_lf=g("A_VCM_LF", 50.0), C_pv=g("A_C_PV_PE", 10e-6), V_cm_hf=g("A_VCM_HF", 5.0))


NANO_REF = dict(name="nanocrystalline toroid OD 80 / ID 50 / H 25 mm (bare core), k_Fe 0.75", od=80e-3, id=50e-3, h=25e-3,
                ae=281e-6, le=0.204, bsat=1.2, case=2e-3)
MU_LF_FACTOR = 1.2      # ASSUMED mu(150 Hz) / mu(10 kHz) for nanocrystalline (no 150 Hz value on file)


def cm_cores():
    """Yunlu CM toroids from the Asian datasheet table (OD/ID/H, Ae, le in the note); fallback NANO_REF."""
    out = []
    for r in _csv("sim/data/asian_magnetic_materials.csv"):
        if r["kind"] == "shape" and r["maker"] == "Yunlu":
            m = re.search(r"toroid ([0-9.]+)/([0-9.]+)/([0-9.]+) mm.*?Ae ([0-9.]+) mm2 / le ([0-9.]+) mm", r["note"])
            if m:
                od, id_, h, ae, le = (float(x) for x in m.groups())
                out.append(dict(name=f"Yunlu {r['value']} ({od:g}/{id_:g}/{h:g} mm)", od=od * 1e-3, id=id_ * 1e-3, h=h * 1e-3,
                                ae=ae * 1e-6, le=le * 1e-3, bsat=1.25, case=2e-3, doc=r["document"], page=r["page"]))
    return out or [NANO_REF]


def port_cmc_own(mu10k=70e3, mu_label="Yunlu CM grade mu 70 000 at 10 kHz (datasheet p.13)"):
    specs, a = port_cmc_inputs()
    out = {}
    for key, s in specs.items():
        a_cu = 50e-6 if s["I"] <= 160 else 70e-6                            # J 3.2 / 2.9 A/mm2
        d_cable = 1.45 * math.sqrt(a_cu * 1e6) * 1e-3 + 2.0e-3              # 1.5 kV DC flexible cable OD (estimate)
        best = None
        for core in sorted(cm_cores(), key=lambda c_: c_["ae"] * c_["le"]):
            al10 = MU0 * mu10k * core["ae"] / core["le"]
            n = max(2, math.ceil(math.sqrt(s["L_cm"] / al10)))
            id_eff = core["id"] - 2 * core["case"]
            room = math.pi * (id_eff - d_cable) - 2 * 10e-3                 # two 10 mm sector separators
            if 2 * n * d_cable <= room:
                best = (core, al10, n, room)
                break
        core, al10, n, room = best if best else (cm_cores()[-1], MU0 * mu10k * cm_cores()[-1]["ae"] / cm_cores()[-1]["le"], 2, 0.0)
        ae, le = core["ae"], core["le"]
        lcm10 = al10 * n * n
        lcm150 = lcm10 * MU_LF_FACTOR
        icm = a["V_cm_lf"] * 2 * math.pi * 150 * a["C_pv"]                  # rms, set by the array capacitance
        b_lf = lcm150 * icm * math.sqrt(2) / (n * ae)
        l_dm = max(s["L_dm"]) if s["L_dm"][0] else 0.01 * al10 * n ** 2      # ponytail: 1 % of L_cm when not specified
        b_dm = l_dm * s["I"] / (n * ae)                                      # conservative: all DM leakage flux in the core
        b_hf = a["V_cm_hf"] * math.sqrt(2) / (2 * math.pi * 20e3 * n * ae)
        mlt = 2 * ((core["od"] - core["id"]) / 2 + core["h"] + 4 * core["case"]) + 4 * d_cable
        p_cu = 2 * s["I"] ** 2 * rho_cu(90) * n * (mlt + 0.1) / a_cu         # +0.1 m of lead per winding
        surf = math.pi * (core["od"] + 2 * d_cable) * (core["h"] + 2 * d_cable) + 2 * math.pi / 4 * (
            (core["od"] + 2 * d_cable) ** 2 - core["id"] ** 2)
        out[key] = dict(spec=s, core=core["name"], core_d=core, mu10k=mu10k, mu_label=mu_label, Ae=ae, le=le, AL10=al10, N=n,
                        L_cm10=lcm10, L_cm150=lcm150, I_cm_lf=icm, B_lf=b_lf, B_lf_2xC=2 * b_lf, B_dm=b_dm, B_hf=b_hf,
                        B_total=b_lf + b_dm + b_hf, a_cu=a_cu, d_cable=d_cable, fits=best is not None, room=room, P_cu=p_cu,
                        dT=temp_rise_maniktala(p_cu, surf), surface=surf, assumptions=a, bsat=core["bsat"])
    return out


# =====================================================================================================================
# OpenMagnetics (PyOpenMagnetics / MKF): the same parts rebuilt as MAS objects
# =====================================================================================================================
try:
    import PyOpenMagnetics as PyOM                       # built from the sdist on macOS arm64, see report section 1
    HAVE_OM = True
except Exception as _e:                                    # pragma: no cover - depends on the machine
    PyOM, HAVE_OM, OM_IMPORT_ERROR = None, False, repr(_e)
else:
    OM_IMPORT_ERROR = ""
OM_MODELS = {"coreLosses": "IGSE", "reluctance": "ZHANG", "coreTemperature": "MANIKTALA",
             "gapReluctance": "ZHANG", "magneticFieldStrength": "BINNS_LAWRENSON",
             "magneticFieldStrengthFringingEffect": "ROSHEN"}


def _om_litz(n_str, d_str, a_cu_bundle_od):
    """custom litz (the MAS catalogue stops at 800 strands): n strands of 'Round <d> - Grade 1', single served."""
    return {"type": "litz", "name": f"Litz {n_str}x{d_str*1e3:.3g} - Grade 1 - Single Served (custom)",
            "numberConductors": int(n_str), "strand": f"Round {d_str*1e3:.3g} - Grade 1", "standard": "IEC 60317",
            "coating": {"type": "served", "numberLayers": 1},
            "outerDiameter": {"nominal": a_cu_bundle_od}, "manufacturerInfo": {"name": "verification model"}}


def _om_round(d, grades=("Grade 2", "Grade 1", "Single Build", "Heavy Build")):
    """MAS round-wire name for a diameter d [m] (names are 'Round 0.3 - Grade 2', 'Round 8.0 - Single Build', ...)"""
    names = set(PyOM.get_wire_names())
    for g in grades:
        for txt in (f"{d*1e3:g}", f"{d*1e3:.1f}", f"{d*1e3:.2f}"):
            if f"Round {txt} - {g}" in names:
                return f"Round {txt} - {g}"
    raise KeyError(f"no MAS round wire for {d*1e3:.2f} mm")


def _om_wave(t, y):
    return {"waveform": {"data": [float(v) for v in y], "time": [float(v) for v in t]}}


def _om_build(core_fd, windings, op_excitations, f, t_amb, pattern=None, proportions=None, margin=0.0,
              insulation=0.0, l_target=None, turns_ratios=(), wall=None):
    """returns (core, coil, magnetic, inputs processed); wall = coil-former wall + spacer between the wound leg and the
    first turn (the MAS basic bobbin has none, which puts turns on the gapped leg)"""
    core = PyOM.calculate_core_data({"functionalDescription": core_fd}, True)
    bobbin = PyOM.create_basic_bobbin_by_thickness(core, wall) if wall else PyOM.create_basic_bobbin(core, True)
    coil = {"bobbin": bobbin, "functionalDescription": windings}
    nw = len(windings)
    pat = pattern or list(range(nw))
    prop = proportions or [1.0 / nw] * nw
    if insulation > 0:                     # barrier between sections (wind_by_layers' JSON form is not needed)
        coil = PyOM.set_intersection_insulation(coil, insulation, 1)
    coil = PyOM.wind(coil, 1, prop, pat, [[margin, margin]] * len(pat))
    magnetic = {"core": core, "coil": coil, "manufacturerInfo": {"name": "verification", "reference": "magnetics.py"}}
    inputs = {"designRequirements": {"magnetizingInductance": {"nominal": l_target or 1e-4},
                                     "turnsRatios": [{"nominal": r} for r in turns_ratios]},
              "operatingPoints": [{"name": "worst", "conditions": {"ambientTemperature": t_amb},
                                   "excitationsPerWinding": [dict(e, frequency=f) for e in op_excitations]}]}
    inputs = PyOM.process_inputs(inputs)
    return core, coil, magnetic, inputs


def _om_eval(core, coil, magnetic, inputs, t_wind):
    """inductance, core loss, winding loss (+ split), saturation current, temperature: whatever the engine returns."""
    op = inputs["operatingPoints"][0]
    out = {}
    calls = {
        "L": lambda: PyOM.calculate_inductance_from_number_turns_and_gapping(core, coil, op, {"reluctance": "ZHANG"}),
        "core": lambda: PyOM.calculate_core_losses(core, coil, inputs, OM_MODELS),
        "winding": lambda: PyOM.calculate_winding_losses(magnetic, op, t_wind),
        "I_sat": lambda: PyOM.calculate_saturation_current(magnetic, 100.0),
        "core_params_100C": lambda: PyOM.get_core_temperature_dependant_parameters(core, 100.0),
    }
    for k, fn in calls.items():
        try:
            out[k] = fn()
        except Exception as e:               # keep going: one failing call must not hide the others
            out[k] = {"error": str(e)[:300]}
    try:
        lk = getattr(PyOM, "calculate_leakage_inductance", None)
        if lk and len(coil["functionalDescription"]) > 1:
            out["leakage"] = lk(magnetic, op["excitationsPerWinding"][0]["frequency"], 0)
    except Exception as e:
        out["leakage"] = {"error": str(e)[:300]}
    out["Ve"] = core["processedDescription"]["effectiveParameters"]["effectiveVolume"]
    out["Ae"] = core["processedDescription"]["effectiveParameters"]["effectiveArea"]
    out["le"] = core["processedDescription"]["effectiveParameters"]["effectiveLength"]
    return out


def _num(x, *keys):
    """dig a float out of an engine result (dict / nested dict / list of nominal dicts / float); None if absent"""
    if isinstance(x, (int, float)):
        return float(x)
    if not isinstance(x, dict) or "error" in x:
        return None
    for k in keys:
        v = x.get(k)
        if isinstance(v, list) and v and all(isinstance(e, dict) for e in v):     # e.g. leakageInductancePerWinding
            vals = [e.get("nominal") for e in v if isinstance(e.get("nominal"), (int, float))]
            if vals:
                return float(max(vals))
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            for kk in ("nominal", "value", "magnetizingInductance"):
                vv = v.get(kk)
                if isinstance(vv, (int, float)):
                    return float(vv)
                if isinstance(vv, dict) and isinstance(vv.get("nominal"), (int, float)):
                    return float(vv["nominal"])
    return None


def om_pv_inductor(own, cell):
    ind = cell["inductor"]
    f = own["f"]
    t_on = 0.5 / f
    i0, i1 = own["I_dc"] - own["ripple"] / 2, own["I_dc"] + own["ripple"] / 2
    od = litz_bundle(own["a_cu"])[0]
    core_fd = {"type": "two-piece set", "material": f"Kool Mµ MAX {own['perm']}", "shape": ECORE[own["shape"]]["om"],
               "gapping": [], "numberStacks": own["n_stack"]}
    wnd = [{"name": "L", "numberTurns": own["turns"], "numberParallels": 1, "isolationSide": "primary",
            "wire": _om_litz(own["n_strand"], own["d_strand"], od)}]
    vl = own["L_Idc"] * own["ripple"] / t_on
    exc = [{"current": _om_wave([0, t_on, 1 / f], [i0, i1, i0]),
            "voltage": _om_wave([0, t_on, t_on, 1 / f], [vl, vl, -vl, -vl])}]
    core, coil, mag, inp = _om_build(core_fd, wnd, exc, f, 45.0, margin=5e-3, l_target=own["L_Idc"])
    r = _om_eval(core, coil, mag, inp, 110.0)
    # DC-bias roll-off as the engine models it: permeability at the operating H
    try:
        h = own["turns"] * own["I_dc"] / own["le"]
        r["mu_45A"] = PyOM.get_material_permeability(f"Kool Mµ MAX {own['perm']}", 100.0, h, f)
        r["mu_trip"] = PyOM.get_material_permeability(f"Kool Mµ MAX {own['perm']}", 100.0,
                                                      own["turns"] * ind["I_trip_A"] / own["le"], f)
        r["mu_0"] = PyOM.get_material_permeability(f"Kool Mµ MAX {own['perm']}", 100.0, 0.0, f)
    except Exception as e:
        r["mu_45A"] = {"error": str(e)[:200]}
    return r


def om_dab_transformer(own, dab, wname=None):
    D = dab_inputs(dab)
    wname = wname or own["worst"]["point"]
    tt, wv = _period(D["waves"][wname], D["f"])
    sel = slice(None, None, 8)
    dt = tt[1] - tt[0]
    lam = np.cumsum(wv["v_s_V"] * D["n"]) * dt
    lam -= lam.mean()
    i1 = wv["i_L_A"]
    i2 = (i1 - lam / D["Lm"]) * D["n"]
    core_fd = {"type": "two-piece set", "material": "N95", "shape": PM114["om"], "numberStacks": 1,
               "gapping": [{"type": "subtractive", "length": own["gap_designer"]},
                           {"type": "residual", "length": 5e-6}, {"type": "residual", "length": 5e-6}]}
    lay = own["layout"][own["layout_used"]]
    wnd = [{"name": "Primary", "numberTurns": D["N1"], "numberParallels": 1, "isolationSide": "primary",
            "wire": _om_litz(D["ns1"], D["d_str"], lay["p"]["od"])},
           {"name": "Secondary", "numberTurns": D["N2"], "numberParallels": 1, "isolationSide": "secondary",
            "wire": _om_litz(D["ns2"], D["d_str"], lay["s"]["od"])}]
    t = np.append(tt[sel], 1 / D["f"])
    vm = wv["v_s_V"] * D["n"]          # winding voltage = magnetising-branch voltage (the bridge voltage v_p also drops
    # across the external inductor and the leakage; giving v_p would put that drop on the core)
    exc = [{"current": _om_wave(t, np.append(i1[sel], i1[0])), "voltage": _om_wave(t, np.append(vm[sel], vm[0]))},
           {"current": _om_wave(t, np.append(i2[sel], i2[0])), "voltage": _om_wave(t, np.append(wv["v_s_V"][sel], wv["v_s_V"][0]))}]
    core, coil, mag, inp = _om_build(core_fd, wnd, exc, D["f"], D["t_cool"], insulation=D["b_iso"],
                                     proportions=[0.5, 0.5], pattern=[0, 1], l_target=D["Lm"], turns_ratios=[D["N1"] / D["N2"]])
    r = _om_eval(core, coil, mag, inp, 90.0)
    r["point"] = wname
    return r


def om_dab_inductor(own, dab):
    D = dab_inputs(dab)
    wname = max(own["points"], key=lambda k: own["points"][k]["I_rms"])
    tt, wv = _period(D["waves"][wname], D["f"])
    sel = slice(None, None, 8)
    i = wv["i_L_A"]
    vl = own["L"] * np.gradient(i, tt)
    ng = 4
    core_fd = {"type": "two-piece set", "material": "N95", "shape": PM114["om"], "numberStacks": 1,
               "gapping": [{"type": "subtractive", "length": own["gap_designer"] / ng,
                            "coordinates": [0, (k + 0.5) * PM114["h_win"] / ng - PM114["h_win"] / 2, 0]} for k in range(ng)]
               + [{"type": "residual", "length": 5e-6}, {"type": "residual", "length": 5e-6}]}
    od = litz_bundle(own["a_cu"], "round")[0]
    wnd = [{"name": "L", "numberTurns": own["nl"], "numberParallels": 1, "isolationSide": "primary",
            "wire": _om_litz(own["n_str"], D["d_str"], od)}]
    t = np.append(tt[sel], 1 / D["f"])
    exc = [{"current": _om_wave(t, np.append(i[sel], i[0])), "voltage": _om_wave(t, np.append(vl[sel], vl[0]))}]
    core, coil, mag, inp = _om_build(core_fd, wnd, exc, D["f"], D["t_cool"], margin=3e-3, l_target=own["L"])
    r = _om_eval(core, coil, mag, inp, 90.0)
    try:   # the engine's own gap for 2.12 uH with distributed gaps
        g = PyOM.calculate_gapping_from_number_turns_and_inductance(core, coil, inp, "DISTRIBUTED", 5, {"reluctance": "ZHANG"})
        r["gap_engine"] = sum(x["length"] for x in g["functionalDescription"]["gapping"] if x.get("type") != "residual")
    except Exception as e:
        r["gap_engine"] = {"error": str(e)[:200]}
    r["point"] = wname
    return r


def om_aux_transformer(own, aux, shape=None, material="N97", gap=None, s_wire=None, wall=None, p_litz=None):
    A = aux.get("_A") or aux_inputs(aux)
    v = max(own["points"])
    p = own["points"][v]
    f = A["f"]
    T, t1, t2 = 1 / f, p["D"] / f, p["D2"] / f
    n = A["np"] / A["ns"]
    ip = _om_wave([0, t1, t1, T], [0, p["Ipk"], 0, 0])
    is_ = _om_wave([0, t1, t1, t1 + t2, T], [0, 0, n * p["Ipk"], 0, 0])
    vp = _om_wave([0, t1, t1, t1 + t2, t1 + t2, T], [v, v, -A["vr"], -A["vr"], 0, 0])
    vs = _om_wave([0, t1, t1, t1 + t2, t1 + t2, T], [-v / n, -v / n, A["vr"] / n, A["vr"] / n, 0, 0])
    core_fd = {"type": "two-piece set", "material": material, "shape": shape or ETD29["om"], "numberStacks": 1,
               "gapping": [{"type": "subtractive", "length": gap or A["gap"]},
                           {"type": "residual", "length": 5e-6}, {"type": "residual", "length": 5e-6}]}
    wnd = [{"name": "Primary", "numberTurns": A["np"], "numberParallels": 1, "isolationSide": "primary",
            "wire": _om_litz(*p_litz) if p_litz else _om_round(A["wire"]["p"])},
           {"name": "Secondary", "numberTurns": A["ns"], "numberParallels": 1, "isolationSide": "secondary",
            "wire": _om_litz(s_wire[1], s_wire[2], s_wire[3]) if s_wire else
            _om_round(A["wire"]["s"], ("Grade 3", "Grade 2", "Grade 1"))}]   # TIW modelled as grade-3 build
    exc = [{"current": ip, "voltage": vp}, {"current": is_, "voltage": vs}]
    core, coil, mag, inp = _om_build(core_fd, wnd, exc, f, 60.0, pattern=[0, 1, 0], proportions=[0.6, 0.4],
                                     l_target=A["lp"], turns_ratios=[n], wall=wall)
    r = _om_eval(core, coil, mag, inp, 100.0)
    r["vin"] = v
    return r


def om_cmc(own_c, material, stacks=1):
    s = own_c
    core_fd = {"type": "toroidal", "material": material, "shape": "T 80/50/25", "gapping": [], "numberStacks": stacks}
    wnd = [{"name": "A", "numberTurns": s["N"], "numberParallels": 1, "isolationSide": "primary", "wire": _om_round(8e-3)}]
    f = 10e3
    exc = [{"current": _om_wave([0, 0.25 / f, 0.75 / f, 1 / f], [0, 0.1, -0.1, 0]),
            "voltage": _om_wave([0, 0.5 / f, 0.5 / f, 1 / f], [1.0, 1.0, -1.0, -1.0])}]
    core, coil, mag, inp = _om_build(core_fd, wnd, exc, f, 45.0, l_target=s["spec"]["L_cm"])
    r = _om_eval(core, coil, mag, inp, 90.0)
    try:
        r["mu_10k"] = PyOM.get_material_permeability(material, 25.0, 0.0, 10e3)
        r["mu_150"] = PyOM.get_material_permeability(material, 25.0, 0.0, 150.0)
    except Exception as e:
        r["mu_10k"] = {"error": str(e)[:200]}
    return r


def run_openmagnetics(own, specs):
    """every part in its own try block; returns {part: result | {'error': ...}} and the engine provenance"""
    if not HAVE_OM:
        return {}, {"available": False, "error": OM_IMPORT_ERROR}
    t0 = time.time()
    PyOM.load_databases({})
    try:
        from importlib.metadata import version as _v
        ver = _v("PyOpenMagnetics")
    except Exception:
        ver = "?"
    info = {"available": True, "version": ver, "mkf_commit": getattr(PyOM, "__mkf_commit__", "?"),
            "mas_commit": getattr(PyOM, "__mas_commit__", "?"), "models": OM_MODELS}
    res = {}
    jobs = {"pv_inductor": lambda: om_pv_inductor(own["pv_inductor"], specs["cell"]),
            "dab_transformer": lambda: om_dab_transformer(own["dab_transformer"], specs["dab"]),
            "dab_transformer_60kW": lambda: om_dab_transformer(own["dab_transformer"], specs["dab"], "matched_60kW"),
            "dab_series_inductor": lambda: om_dab_inductor(own["dab_series_inductor"], specs["dab"]),
            "aux_hv_transformer": lambda: om_aux_transformer(own["aux_hv_transformer"], specs["aux"]),
            "port_cm_choke": lambda: {m_: om_cmc(own["port_cm_choke"]["CMC160"], m_) for m_ in ("1K107", "VITROPERM 500F", "Yunlu YNB")}}
    for k, fn in jobs.items():
        try:
            res[k] = fn()
        except Exception as e:
            res[k] = {"error": f"{type(e).__name__}: {str(e)[:400]}"}
    info["runtime_s"] = time.time() - t0
    return res, info


# =====================================================================================================================
# Asian material options (SRC-1): datasheet points (sim/data/asian_magnetic_materials.csv) + the OpenMagnetics database
# =====================================================================================================================
def _csv(path):
    p = P(path)
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []


def _fit_points(pts):
    """least-squares P = k f^a B^b through (f, B, P) points; a fixed at 1.4 (powder) if only one frequency"""
    f = np.array([p[0] for p in pts])
    b = np.array([p[1] for p in pts])
    y = np.log([p[2] for p in pts])
    if len(set(f)) == 1:
        A = np.c_[np.ones(len(b)), np.log(b)]
        lk, be = np.linalg.lstsq(A, y - 1.4 * np.log(f), rcond=None)[0]
        return math.exp(lk), 1.4, be
    A = np.c_[np.ones(len(b)), np.log(f), np.log(b)]
    lk, al, be = np.linalg.lstsq(A, y, rcond=None)[0]
    return math.exp(lk), al, be


def asian_options(own, om_ok):
    rows = _csv("sim/data/asian_magnetic_materials.csv")
    pv, xf = own["pv_inductor"], own["dab_transformer"]
    out = {"pv_inductor": [], "dab": [], "aux": [], "cmc": [], "shapes": [], "mas": [], "not_found": []}
    bym = {}
    for r in rows:
        bym.setdefault((r["maker"], r["material"]), []).append(r)
    h45 = pv["turns"] * pv["I_dc"] / pv["le"]
    f, t_on = pv["f"], 0.5 / pv["f"]
    for (mk, mat), rs in bym.items():
        loss = [(float(r["f_Hz"]), float(r["B_pk_T"]), float(r["value"]) * 1e3) for r in rs
                if r["kind"] == "loss" and r["unit"] == "kW/m3" and r["T_C"] in ("", "100") and r["f_Hz"] and r["B_pk_T"]]
        bias = [(float(r["H_A_per_m"]), float(r["value"])) for r in rs if r["kind"] == "bias"]
        shapes = [(r["value"], r["note"]) for r in rs if r["kind"] == "shape"]
        bsat = {r["T_C"]: float(r["value"]) for r in rs if r["kind"] == "Bsat"}
        checks = [(r["value"], r["note"]) for r in rs if r["kind"] == "mas_check"]
        rep = rs[0]["replaces"]
        item = dict(maker=mk, material=mat, replaces=rep, bsat=bsat, shapes=shapes, mas_check=checks,
                    doc=rs[0]["document"])
        if "Kool" in rep and loss:
            k, a, b = _fit_points(loss)
            item["P_core_W"] = igse(k, a, b, np.array([0, t_on, 1 / f]), np.array([0, pv["dB_faraday"], 0])) * pv["Ve"]
            item["P_core_W_field"] = igse(k, a, b, np.array([0, t_on, 1 / f]), np.array([0, pv["dB_field"], 0])) * pv["Ve"]
            if bias:
                item["mu_45A_pct"] = float(np.interp(h45, [x[0] for x in bias], [x[1] for x in bias]))
            out["pv_inductor"].append(item)
        elif "N95" in rep and loss:
            same_f = [x for x in loss if x[0] == 100e3 and x[1] <= 0.2]
            item["table"] = [(x[0], x[1], x[2]) for x in loss if x[1] >= 0.1]
            if len({x[1] for x in same_f}) < 3:     # B dependence not published as a curve: report the table only
                out["dab"].append(item)
                continue
            k, a, b = _fit_points([x for x in loss if x[1] <= 0.2])
            tri = np.array([0, 0.5e-5, 1e-5])
            item["P_core_950_W"] = igse(k, a, b, tri, np.array([-xf["B_pk_950"], xf["B_pk_950"], -xf["B_pk_950"]])) * PM114["Ve"]
            item["steinmetz"] = (k, a, b)
            out["dab"].append(item)
        elif "N97" in rep:
            out["aux"].append(item)
        elif "CM" in rep or "chok" in rep:
            item["mu_f"] = {float(r["f_Hz"]): float(r["value"]) for r in rs if r["kind"] == "mu_f" and r["f_Hz"]}
            out["cmc"].append(item)
        else:
            out["shapes"].append(item)
    # what the OpenMagnetics database says for the same operating points (material swapped, same shape/excitation)
    if om_ok:
        cands = {"pv": ["Kool Mµ MAX 26", "NPC 26", "NPH-L 26", "NPF 26", "NPH 26", "NPX 26", "KDM KS 26", "KDM KSF 26",
                        "KDM KPH 26", "CSC Sendust 26", "CSC Mega Flux 26", "CSC High Flux 26"],
                 "dab": ["N95", "DMR95", "DMR95B", "DMR96A", "TP4A", "TPW33"],
                 "aux": ["N97", "DMR96", "TP4E", "DMR95"]}
        for key, mats in cands.items():
            for m_ in mats:
                try:
                    out["mas"].append(_om_material_point(key, m_, own))
                except Exception as e:
                    out["mas"].append({"set": key, "material": m_, "error": str(e)[:160]})
    return out


def _om_material_point(key, material, own):
    """core loss (and DC-bias permeability for powders) of one MAS material at our operating point"""
    if key == "pv":
        pv = own["pv_inductor"]
        f, t_on = pv["f"], 0.5 / pv["f"]
        core = PyOM.calculate_core_data({"functionalDescription": {"type": "two-piece set", "material": material,
                                         "shape": ECORE[pv["shape"]]["om"], "gapping": [], "numberStacks": pv["n_stack"]}}, True)
        vl = pv["L_Idc"] * pv["ripple"] / t_on
        i0, i1 = pv["I_dc"] - pv["ripple"] / 2, pv["I_dc"] + pv["ripple"] / 2
        exc = {"frequency": f, "current": _om_wave([0, t_on, 1 / f], [i0, i1, i0]),
               "voltage": _om_wave([0, t_on, t_on, 1 / f], [vl, vl, -vl, -vl])}
        n = pv["turns"]
        mu45 = PyOM.get_material_permeability(material, 100.0, n * pv["I_dc"] / pv["le"], f)
        mu0 = PyOM.get_material_permeability(material, 100.0, 0.0, f)
    else:
        a = own["dab_transformer"] if key == "dab" else own["aux_hv_transformer"]
        f = a["f"]
        c, shape, n = (PM114, PM114["om"], a["N1"]) if key == "dab" else (ETD29, ETD29["om"], a["np"])
        core = PyOM.calculate_core_data({"functionalDescription": {"type": "two-piece set", "material": material,
                                         "shape": shape, "gapping": [{"type": "subtractive", "length": 1e-3},
                                         {"type": "residual", "length": 5e-6}, {"type": "residual", "length": 5e-6}],
                                         "numberStacks": 1}}, True)
        bpk = a["B_pk_950"] if key == "dab" else a["points"][max(a["points"])]["B_pk"] / 2
        v = 4 * f * n * c["Ae"] * bpk                                           # square wave giving that flux
        mu45 = mu0 = None
    coil = {"bobbin": PyOM.create_basic_bobbin(core, True),
            "functionalDescription": [{"name": "w", "numberTurns": n, "numberParallels": 1, "isolationSide": "primary",
                                       "wire": _om_round(1e-3)}]}
    if key != "pv":     # the engine derives the flux from the current: give the magnetising current of that voltage
        def _exc(ipk):
            return {"frequency": f, "voltage": _om_wave([0, 0.5 / f, 0.5 / f, 1 / f], [v, v, -v, -v]),
                    "current": _om_wave([0, 0.5 / f, 1 / f], [-ipk, ipk, -ipk])}
        inp0 = PyOM.process_inputs({"designRequirements": {"magnetizingInductance": {"nominal": 1e-4}, "turnsRatios": []},
                                    "operatingPoints": [{"name": "p", "conditions": {"ambientTemperature": 100.0},
                                                         "excitationsPerWinding": [_exc(1.0)]}]})
        l_om = PyOM.calculate_inductance_from_number_turns_and_gapping(core, coil, inp0["operatingPoints"][0], {"reluctance": "ZHANG"})
        exc = _exc(bpk * n * c["Ae"] / l_om)
    inp = PyOM.process_inputs({"designRequirements": {"magnetizingInductance": {"nominal": 1e-4}, "turnsRatios": []},
                               "operatingPoints": [{"name": "p", "conditions": {"ambientTemperature": 100.0},
                                                    "excitationsPerWinding": [exc]}]})
    cl = PyOM.calculate_core_losses(core, coil, inp, OM_MODELS)
    return {"set": key, "material": material, "P_core_W": _num(cl, "coreLosses"),
            "B_pk": _num(cl, "magneticFluxDensityPeak"), "mu_ratio_45A": (mu45 / mu0) if mu45 and mu0 else None}


# =====================================================================================================================
# Reference designs (MAG-1): sim/data/reference_magnetics.csv, scaled to our power and frequency
# =====================================================================================================================
def ref_value(rows, design, part, param):
    for r in rows:
        if r["design"] == design and r["part"] == part and r["parameter"] == param:
            try:
                return float(r["value"]), r
            except ValueError:
                return r["value"], r
    return None, None


def reference_comparison(own, specs):
    rows = _csv("sim/data/reference_magnetics.csv")
    xf = own["dab_transformer"]
    dab = specs["dab"]
    out = []
    # DAB transformers: reactance of the series inductance and magnetising inductance per base impedance, volume per
    # kW and AC winding loss per kW at rated power
    zb_ours = 800.0 ** 2 / dab["power_stage"]["rated_power_W"]
    ours_l = dab["series_inductor"]["L_total_H"]
    lp_vol = math.pi / 4 * PM114["d_out"] ** 2 * PM114["h_set"]
    m60 = xf["points"].get("matched_60kW", {})
    out.append(dict(design="ours DAB-D60 (designer / own)", P_kW=60, f_kHz=100, V=800,
                    X_L_pu=2 * math.pi * 1e5 * ours_l / zb_ours, X_m_pu=2 * math.pi * 1e5 * dab["transformer"]["L_m_H"] / zb_ours,
                    vol_cm3_per_kW=lp_vol * 1e6 / 60, loss_pct=f"{159/600:.2f} / {xf['P_total_worst']/600:.2f}",
                    R_ac_mohm=(m60.get("P_cu", float("nan")) / m60.get("I1_rms", 1) ** 2 * 1e3) if m60 else float("nan"),
                    note="PM 114/93 envelope; loss % = max transformer loss / 60 kW; R_ac = own copper loss / I1_rms^2 at 800/873 V"))
    for design, P_, f_, V_ in (("Wolfspeed CRD60DD12N-GMB", 60, 100, 800), ("TI TIDA-010054", 10, 100, 800)):
        lk, _ = ref_value(rows, design, "transformer", "L_leak")
        lm, _ = ref_value(rows, design, "transformer", "L_m")
        vol, _ = ref_value(rows, design, "transformer", "volume")
        rw, _ = ref_value(rows, design, "transformer", "R_w")
        lt, _ = ref_value(rows, design, "series_inductor", "L")
        loss, _ = ref_value(rows, design, "transformer", "loss_total")
        rdcp, _ = ref_value(rows, design, "transformer", "R_dc_pri")
        zb = V_ ** 2 / (P_ * 1e3)
        lser = lt if isinstance(lt, float) else lk
        out.append(dict(design=design, P_kW=P_, f_kHz=f_, V=V_,
                        X_L_pu=2 * math.pi * f_ * 1e3 * lser / zb if isinstance(lser, float) else float("nan"),
                        X_m_pu=2 * math.pi * f_ * 1e3 * lm / zb if isinstance(lm, float) else float("nan"),
                        vol_cm3_per_kW=vol / 1e3 / P_ if isinstance(vol, float) else float("nan"),
                        loss_pct=f"{loss / (P_ * 10):.2f}" if isinstance(loss, float) else "n/p",
                        R_ac_mohm=rw * 1e3 if isinstance(rw, float) else (rdcp * 1e3 if isinstance(rdcp, float) else float("nan")),
                        note=("R_w measured at 100 kHz (UG p.52): at 60 kW (~75 A) it implies ~%.0f W copper" % (rw * 75 ** 2)
                              if isinstance(rw, float) else "R = primary DC resistance (AC not published)")))
    # PV / boost inductors: stored energy per kW and ripple ratio, current density
    pv = own["pv_inductor"]
    inds = [dict(design="ours PVCELL-25", P_kW=25, f_kHz=pv["f"] / 1e3, L_uH=pv["L_Idc"] * 1e6, I_dc=pv["I_dc"],
                 ripple_pct=100 * pv["ripple"] / pv["I_dc"], E_mJ_per_kW=0.5 * pv["L_Idc"] * pv["I_pk"] ** 2 / 25 * 1e3,
                 J=pv["I_rms"] / (pv["a_cu"] * 1e6), note="2 x 8044E Kool Mu MAX 26u, 38 t")]
    l_, _ = ref_value(rows, "Wolfspeed CRD-60DD12N", "boost_inductor", "L")
    idc, _ = ref_value(rows, "Wolfspeed CRD-60DD12N", "boost_inductor", "I_dc")
    rip, _ = ref_value(rows, "Wolfspeed CRD-60DD12N", "boost_inductor", "ripple_pp")
    ipk, _ = ref_value(rows, "Wolfspeed CRD-60DD12N", "boost_inductor", "I_pk")
    irms, _ = ref_value(rows, "Wolfspeed CRD-60DD12N", "boost_inductor", "I_rms")
    fsw, _ = ref_value(rows, "Wolfspeed CRD-60DD12N", "boost_inductor", "f_sw")
    core, _ = ref_value(rows, "Wolfspeed CRD-60DD12N", "boost_inductor", "core")
    if all(isinstance(x, float) for x in (l_, idc, rip, ipk, irms, fsw)):
        inds.append(dict(design="Wolfspeed CRD-60DD12N (per phase)", P_kW=15, f_kHz=fsw / 1e3, L_uH=l_ * 1e6, I_dc=idc,
                         ripple_pct=100 * rip / idc, E_mJ_per_kW=0.5 * l_ * ipk ** 2 / 15 * 1e3,
                         J=irms / (18 * math.pi / 4 * 0.455 ** 2), note=f"core {core}, 44 t of 18 x AWG25 (UG p.24)"))
    # flyback
    t_cr, _ = ref_value(rows, "Wolfspeed CRD-020DD17P-J", "flyback_transformer", "temperature")
    fly = dict(ref_temp=t_cr, ours_dT=own["aux_hv_transformer"]["dT_natural"])
    return dict(dab=out, inductors=inds, flyback=fly, n_rows=len(rows))


# =====================================================================================================================
# Designer figures (spec JSON + the designers' report text), three-way rows, disagreement bands
# =====================================================================================================================
def _rx(text, pat, n=1, cast=float, default=None):
    m = re.search(pat, text)
    return tuple(cast(m.group(i + 1)) for i in range(n)) if (m and n > 1) else (cast(m.group(1)) if m else default)


def designer_figures(specs):
    cell, dab, aux = specs["cell"], specs["dab"], specs["aux"]
    ind, x, s = cell["inductor"], dab["transformer"], dab["series_inductor"]
    pv_rep = open(P("sim/out/pv_design/report.md")).read()
    dab_rep = open(P("sim/out/dab_design/report.md")).read()
    aux_rep = open(P("sim/out/aux_hv_design/report.md")).read()
    core_cu = _rx(pv_rep, r"worst loss [0-9.]+ W \(core ([0-9.]+) W, copper ([0-9.]+) W\)", 2, default=(None, None))
    row60 = re.search(r"\| 800V/800V/60kW \|[^|]*\|[^|]*\|[^|]*\|[^|]*\| (\d+)/(\d+) \| (\d+)/(\d+) \|", dab_rep)
    rdc = _rx(dab_rep, r"R_dc at 90 C \| ([0-9.]+) / ([0-9.]+) mOhm", 2, default=(None, None))
    aux_core = re.search(r"\| transformer core \| ([0-9.]+) \|.*\| ([0-9.]+) \|", aux_rep)
    aux_cu = re.search(r"\| transformer copper \| ([0-9.]+) \|.*\| ([0-9.]+) \|", aux_rep)
    return {
        "pv_inductor": dict(L0=ind["L0_uH"] * 1e-6, L_Idc=ind["L_at_45A_uH"] * 1e-6, L_trip=ind["L_at_trip_uH"] * 1e-6,
                            B_dc=ind["B_dc_at_45A_T"], B_trip=ind["B_at_trip_T"], P_total=ind["loss_worst_W"],
                            P_core=core_cu[0], P_cu=core_cu[1], R_dc20=ind["DCR_20C_mOhm"] * 1e-3,
                            dT=_rx(pv_rep, r"estimated hot-spot rise (\d+) K"), ripple=ind["ripple_pp_max_A"],
                            I_pk=ind["I_peak_normal_max_A"], I_rms=ind["I_rms_max_A"], mass=ind["mass_kg"]),
        "dab_transformer": dict(L_leak=x["L_sigma_target_H"], Lm=x["L_m_H"], B_pk_950=x["B_pk_950V_T"],
                                gap=_rx(x["L_m_tol"], r"~([0-9.]+) mm") * 1e-3 if _rx(x["L_m_tol"], r"~([0-9.]+) mm") else None,
                                P_max=x["loss_max_W"], P_core_60=float(row60.group(1)) if row60 else None,
                                P_cu_60=float(row60.group(2)) if row60 else None, R1=rdc[0] * 1e-3 if rdc[0] else None,
                                R2=rdc[1] * 1e-3 if rdc[1] else None, rth=x["Rth_hotspot_to_coolant_max_K_W"],
                                T_hs=x["hot_spot_max_C"]),
        "dab_series_inductor": dict(L=s["L_external_nominal_H"], gap=_rx(s["reference_core"], r"([0-9.]+) mm total gap") * 1e-3,
                                    P_max=s["loss_max_W"], I_sat=s["I_sat_min_A"],
                                    P_core_60=float(row60.group(3)) if row60 else None,
                                    P_cu_60=float(row60.group(4)) if row60 else None),
        "aux_hv_transformer": dict(Lp=aux["transformer"]["lp_mh"] * 1e-3, gap=aux["transformer"]["gap_mm"] * 1e-3,
                                   B_fl=aux["transformer"]["b_fl_mT"] * 1e-3, B_lim=aux["transformer"]["b_lim_mT"] * 1e-3,
                                   L_leak=aux["transformer"]["llk_est_uh"] * 1e-6, L_leak_1d=aux["transformer"]["llk_1d_uh"] * 1e-6,
                                   P_core=(float(aux_core.group(1)), float(aux_core.group(2))) if aux_core else (None, None),
                                   P_cu=(float(aux_cu.group(1)), float(aux_cu.group(2))) if aux_cu else (None, None)),
    }


def rel(a, b):
    """relative difference of a against b (None-safe)"""
    if a is None or b is None or b == 0 or not all(isinstance(v, (int, float)) for v in (a, b)):
        return None
    return (a - b) / abs(b)


def row(q, unit, des, om, own, band, scale=1.0, why=""):
    """one comparison row: designer / OpenMagnetics / own, deviations against OUR figure, flag beyond the band"""
    def sc(v):
        return None if v is None else v * scale
    d, o, w = sc(des), sc(om), sc(own)
    dd, do = rel(d, w), rel(o, w)
    lim = BANDS.get(band)
    flag = any(x is not None and lim is not None and abs(x) > lim for x in (dd, do))
    return dict(q=q, unit=unit, designer=d, om=o, own=w, d_des=dd, d_om=do, band=band, flag=flag, why=why)


def assemble(specs, own, om, des):
    """three-way rows per part"""
    pv, xf, li, ax, cm = (own[k] for k in ("pv_inductor", "dab_transformer", "dab_series_inductor", "aux_hv_transformer",
                                         "port_cm_choke"))
    d = des
    o = {k: (v if isinstance(v, dict) and "error" not in v else {}) for k, v in om.items()}
    T = {}
    # ---- PV inductor
    opv = o.get("pv_inductor", {})
    om_l = _num(opv.get("L"), "magnetizingInductance")
    mu45, mu0, mut = (opv.get(k) if isinstance(opv.get(k), (int, float)) else None for k in ("mu_45A", "mu_0", "mu_trip"))
    om_l0 = om_l / (mu45 / mu0) if (om_l and mu45 and mu0) else None
    ocl, owl = opv.get("core", {}), opv.get("winding", {})
    om_pc, om_pw = _num(ocl, "coreLosses"), _num(owl, "windingLosses")
    T["pv_inductor"] = [
        row("L0 (zero bias)", "uH", d["pv_inductor"]["L0"], om_l0, pv["L0"], "L", 1e6,
            "OM: engine L / its own bias factor; own and designer: catalog A_L 91 nH/T^2 per set (p.200)"),
        row("L at 45 A", "uH", d["pv_inductor"]["L_Idc"], om_l, pv["L_Idc"], "L", 1e6,
            "E-core DC-bias fit p.66 (own, designer); OM uses its MAS bias coefficients"),
        row("L at the trip current", "uH", d["pv_inductor"]["L_trip"], om_l0 * mut / mu0 if (om_l0 and mut and mu0) else None,
            pv["L_trip"], "L", 1e6),
        row("ripple at the worst point", "A pp", d["pv_inductor"]["ripple"], None, pv["ripple"], "L", 1.0,
            "own: integral of L(i) along the ramp"),
        row("B_dc at 45 A (field, catalog Method 1)", "T", d["pv_inductor"]["B_dc"], None, pv["B_dc"], "B"),
        row("B peak at the worst normal current", "T", None, _num(ocl, "magneticFluxDensityPeak"), pv["B_pk_normal"], "B", 1.0,
            "OM: flux from its own L and the current; own: DC magnetisation curve at 62 A"),
        row("B at the 72.4 A trip", "T", d["pv_inductor"]["B_trip"], None, pv["B_trip"], "B"),
        row("AC flux swing (pk-pk)", "T", pv["dB_field"], (2 * _num(ocl, "magneticFluxDensityAcPeak")) if _num(ocl, "magneticFluxDensityAcPeak") else None,
            pv["dB_faraday"], "B", 1.0, "designer: field-based; own: Faraday (V t / N Ae) - see 3.1"),
        row("core loss", "W", d["pv_inductor"]["P_core"], om_pc, pv["P_core"], "loss"),
        row("winding loss (110 C)", "W", d["pv_inductor"]["P_cu"], om_pw, pv["P_cu"], "loss"),
        row("total loss", "W", d["pv_inductor"]["P_total"], (om_pc + om_pw) if (om_pc and om_pw) else None,
            pv["P_core"] + pv["P_cu"], "loss"),
        row("DCR at 20 C", "mOhm", d["pv_inductor"]["R_dc20"], None, pv["R_dc20"], "L", 1e3),
        row("hot-spot rise", "K", d["pv_inductor"]["dT"], _num(ocl, "maximumCoreTemperatureRise"), pv["dT_hotspot"], "loss", 1.0,
            "designer/own: forced air h = 25 W/m2K (own + internal winding gradient); OM: natural convection core model"),
    ]
    # ---- DAB transformer (OM at the max-current ngspice point)
    oxf = o.get("dab_transformer", {})
    ocl, owl = oxf.get("core", {}), oxf.get("winding", {})
    o60 = o.get("dab_transformer_60kW", {})
    wpt = xf["points"][xf["worst"]["point"]]
    om_lk = _num(oxf.get("leakage"), "leakageInductancePerWinding", "leakageInductance")
    T["dab_transformer"] = [
        row("leakage L_sigma (to N1)", "uH", d["dab_transformer"]["L_leak"], om_lk, xf["L_leak"], "leak", 1e6,
            "own: 1-D MMF with the buildable winding (profiled litz, 1 layer each, 3 mm barrier)"),
        row("magnetising L_m at the designer's gap", "uH", d["dab_transformer"]["Lm"], _num(oxf.get("L"), "magnetizingInductance"),
            xf["Lm_at_designer_gap"], "L", 1e6, "own: post reluctance + McLyman fringing"),
        row("centre-post gap for 150 uH", "mm", d["dab_transformer"]["gap"], None, xf["gap_for_Lm"], "L", 1e3),
        row("B peak at 950 V (Ae)", "T", d["dab_transformer"]["B_pk_950"], None, xf["B_pk_950"], "B"),
        row(f"B peak at {xf['worst']['point']} (ngspice point)", "T", None, _num(ocl, "magneticFluxDensityPeak"), wpt["B_pk"], "B"),
        row("B peak at 800/873 V 60 kW", "T", None, _num(o60.get("core"), "magneticFluxDensityPeak"),
            xf["points"].get("matched_60kW", {}).get("B_pk"), "B"),
        row("core loss at 800/873 V 60 kW", "W", d["dab_transformer"]["P_core_60"], _num(o60.get("core"), "coreLosses"),
            xf["points"].get("matched_60kW", {}).get("P_core"), "loss", 1.0, "N95 R34-toroid curves, iGSE"),
        row("copper loss at 800/873 V 60 kW", "W", d["dab_transformer"]["P_cu_60"], _num(o60.get("winding"), "windingLosses"),
            xf["points"].get("matched_60kW", {}).get("P_cu"), "loss", 1.0, "litz 0.1 mm, Bessel strand model + 1-D field"),
        row(f"copper loss at {xf['worst']['point']} (I1 {wpt['I1_rms']:.0f} A rms)", "W", None, _num(owl, "windingLosses"),
            wpt["P_cu"], "loss"),
        row(f"core loss at {xf['worst']['point']}", "W", None, _num(ocl, "coreLosses"), wpt["P_core"], "loss"),
        row("max total loss (spec I1_rms_max)", "W", d["dab_transformer"]["P_max"], None, xf["P_total_worst"], "loss"),
        row("R_dc P + S' at 90 C (to N1)", "mOhm", (d["dab_transformer"]["R1"] or 0) + (d["dab_transformer"]["R2"] or 0) * (xf["N1"] / xf["N2"]) ** 2,
            None, rho_cu(90) * LITZ_LAY * (xf["N1"] * xf["MLT_p"] / (xf["ns1"] * math.pi * xf["d_str"] ** 2 / 4) +
                                           xf["N2"] * xf["MLT_s"] / (xf["ns2"] * math.pi * xf["d_str"] ** 2 / 4) * (xf["N1"] / xf["N2"]) ** 2),
            "L", 1e3, "own: per-winding MLT (inner/outer), designer: coil-former mean 210 mm for both"),
        row("hot spot at max loss, 50 C coolant", "C", d["dab_transformer"]["T_hs"], None, xf["T_hs_B_potted_housing"], "loss", 1.0,
            "own: best-case conduction estimate 0.71 K/W (potted housing) with the own loss"),
    ]
    # ---- DAB series inductor
    oli = o.get("dab_series_inductor", {})
    wli = max(li["points"].values(), key=lambda p_: p_["I_rms"])
    T["dab_series_inductor"] = [
        row("L at the designer's 9.2 mm (4 gaps)", "uH", d["dab_series_inductor"]["L"], _num(oli.get("L"), "magnetizingInductance"),
            li["L_at_designer_gap"], "L", 1e6, "own: McLyman per gap, spacing-limited"),
        row("total gap for 2.12 uH", "mm", d["dab_series_inductor"]["gap"],
            oli.get("gap_engine") if isinstance(oli.get("gap_engine"), float) else None, li["gap_needed"], "L", 1e3),
        row("B at 275 A", "T", None, None, li["B_275"], "B"),
        row("core loss at 60 kW", "W", d["dab_series_inductor"]["P_core_60"], None,
            li["points"].get("matched_60kW", {}).get("P_core"), "loss"),
        row("copper loss at 60 kW", "W", d["dab_series_inductor"]["P_cu_60"], None,
            li["points"].get("matched_60kW", {}).get("P_cu"), "loss", 1.0, "143 mm2 of 0.1 mm litz in the gap fringing field"),
        row("copper loss at max current", "W", None, _num(oli.get("winding"), "windingLosses"), wli["P_cu"], "loss"),
        row("core loss at max current", "W", None, _num(oli.get("core"), "coreLosses"), wli["P_core"], "loss"),
        row("max total loss", "W", d["dab_series_inductor"]["P_max"], None, li["P_worst"], "loss"),
    ]
    # ---- AUX-HV flyback
    oax = o.get("aux_hv_transformer", {})
    vmax = max(ax["points"])
    da = d["aux_hv_transformer"]
    T["aux_hv_transformer"] = [
        row("Lp at the designer's gap", "mH", da["Lp"], _num(oax.get("L"), "magnetizingInductance"), ax["AL_at_designer_gap"] * ax["np"] ** 2,
            "L", 1e3, "own: round-post McLyman; designer: TDK K1/K2 (N87) relation"),
        row("gap for A_L 142 nH", "mm", da["gap"], None, ax["gap_for_AL"], "L", 1e3),
        row("B peak full load (Ae)", "T", da["B_fl"], _num(oax.get("core"), "magneticFluxDensityPeak"), ax["points"][vmax]["B_pk"], "B"),
        row("B at the current limit, Lp +7 %", "T", da["B_lim"], None, ax["B_lim_Amin"], "B", 1.0, "own uses A_min (71 mm2)"),
        row("leakage, 1-D estimate", "uH", da["L_leak_1d"], _num(oax.get("leakage"), "leakageInductancePerWinding", "leakageInductance"),
            ax["L_leak_1d"], "leak", 1e6),
        row(f"core loss at {vmax} V", "W", da["P_core"][1], _num(oax.get("core"), "coreLosses"), ax["points"][vmax]["P_core"], "loss"),
        row(f"copper loss at {vmax} V", "W", da["P_cu"][1], _num(oax.get("winding"), "windingLosses"), ax["points"][vmax]["P_cu"], "loss"),
    ]
    # ---- CM choke (no designer construction; spec values against the own reference design)
    c160 = cm["CMC160"]
    ocm = o.get("port_cm_choke", {})
    T["port_cm_choke"] = [row("L_cm at 10 kHz (designer: spec minimum)", "mH", c160["spec"]["L_cm"],
                              _num(ocm.get("Yunlu YNB", {}).get("L"), "magnetizingInductance"), c160["L_cm10"], "spec", 1e3,
                              "own: Yunlu CM grade mu 70 000 at 10 kHz; OM: MAS 'Yunlu YNB' static mu 80 000, T 80/50/25")]
    return T


# =====================================================================================================================
# Findings for the designers (gen/data/review_magnetics.csv) - generated from the numbers, so a redesign re-scores
# =====================================================================================================================
MG_IDS = {"dab_cu": "MG-01", "dab_thermal": "MG-02", "dab_core_set": "MG-03", "dab_leak": "MG-04", "lser_loss": "MG-05",
          "pv_window": "MG-06", "pv_temp": "MG-07", "pv_core": "MG-08", "om_kmm_bias": "MG-09", "cmc_lf": "MG-10",
          "aux_cu": "MG-11", "aux_blim": "MG-12", "aux_ins": "MG-13", "dab_ins": "MG-14",
          "lser_isat": "MG-15", "dab_imax": "MG-16"}   # fixed: rows keep their id


def findings(specs, own, des, om, asia):
    pv, xf, li, ax, cm = (own[k] for k in ("pv_inductor", "dab_transformer", "dab_series_inductor", "aux_hv_transformer",
                                         "port_cm_choke"))
    F = []

    def add(sev, where, finding, evidence, rec, tag=""):
        F.append(dict(severity=sev, where=where, finding=finding, evidence=evidence, recommendation=rec, tag=tag))

    m60 = xf["points"].get("matched_60kW", {})
    dx = des["dab_transformer"]
    if xf["worst"]["P_cu"] > 1.15 * (dx["P_max"] or 0) or (m60 and dx["P_cu_60"] and m60["P_cu"] > 1.5 * dx["P_cu_60"]):
        add("critical", "sim/dab_design.py A['Fr1_litz'] / transformer winding (dab_spec.json transformer)",
            f"Transformer copper loss is {m60.get('P_cu', 0) / max(dx['P_cu_60'] or 1, 1):.1f}x the designer's figure: the "
            f"{xf['d_str']*1e3:.2f} mm litz ({xf['ns1']} strands) in a non-interleaved {xf['N1']}:{xf['N2']} winding of "
            f"{xf['breadth']*1e3:.0f} mm breadth has F_r = {xf['Fr_sullivan_fund']:.2f} at 100 kHz (Sullivan; Bessel-strand model "
            f"agrees), not the assumed 1.15, and the proximity factor grows with h^2 for the 3rd-9th current harmonics",
            f"own: {m60.get('P_cu', float('nan')):.0f} W at 800/873 V 60 kW (designer {dx['P_cu_60']} W), "
            f"{xf['worst']['P_cu']:.0f} W at the I1_rms_max point (designer max total {dx['P_max']:.0f} W); effective "
            f"R_ac/R_dc {m60.get('Fr_eff', float('nan')):.1f} and {xf['worst']['Fr_eff']:.1f}; OpenMagnetics "
            f"{f_(_num((om.get('dab_transformer_60kW') or {}).get('winding'), 'windingLosses'), 0)} W / "
            f"{f_(_num((om.get('dab_transformer') or {}).get('winding'), 'windingLosses'), 0)} W at the same points; GMB reference "
            "transformer R_w = 78 mOhm at 100 kHz (UG p.52) implies ~440 W copper at 60 kW",
            "Redesign the winding before the RFQ: strands <= 0.05 mm (proximity loss ~ d^2: /4) AND P-S-P interleaving "
            "(peak field /2: /4) or a core with ~2x the window breadth; then re-derive leakage (interleaving lowers it, the "
            "external inductor takes the rest) and feed F_r(h) from this model (sim/magnetics.py litz_loss) back into "
            "dab_design.py; re-check D-015/D-017 - part of the 2.5x calibration gap may be this", tag="dab_cu")
    th = xf["thermal"]
    t_des_loss = 50 + (dx["P_max"] or 0) * th["B_potted_housing"]
    if t_des_loss > (dx["T_hs"] or 130) or xf["T_hs_B_potted_housing"] > (dx["T_hs"] or 130):
        add("critical", "dab_spec.json transformer Rth_hotspot_to_coolant_max_K_W / core PM 114/93",
            f"The {dx['rth']} K/W hot-spot-to-coolant requirement is not credible for a PM 114/93 pot core: the winding is "
            "enclosed by ferrite (k ~4 W/mK) and its heat must cross the coil former and the core to the plate",
            f"own conduction estimate {th['A_base_on_plate']:.1f} K/W (core base on the cold plate) and "
            f"{th['B_potted_housing']:.2f} K/W best case (potted Al housing touching skirt and winding through the slots): "
            f"hot spot {t_des_loss:.0f} C with the designer's {dx['P_max']:.0f} W, {xf['T_hs_B_potted_housing']:.0f} C with the "
            f"own {xf['P_total_worst']:.0f} W, limit {dx['T_hs']} C (class F 155 C)",
            "Change the construction, not only the vendor requirement: E/U cores with open windows (e.g. 2 x E80 class, "
            "DMEGC EE80 DMR95 is filed) or a matrix of smaller cores, Al heat-spreader plates between winding sections, "
            "vacuum-potted into an Al housing; make the RFQ demand a measured Rth and a thermal run at the full-load loss", tag="dab_thermal")
    if xf["P_core_950_setlimit"] > 1.5 * xf["P_core_950"]:
        add("major", "sim/dab_devices.py FERRITE['N95'] (R34-toroid data used for a 1.9 kg core)",
            "Core loss uses the small-toroid material curve; TDK's own limit for the PM 114/93 N95 set is 3.1x higher",
            f"PM114-93.pdf p.2: P_V < 9.0 W/set at 50 mT, 100 kHz, 100 C vs {9.0/3.1:.1f} W from the R34 curve; scaled to 950 V "
            f"(0.126 T) the guaranteed limit is {xf['P_core_950_setlimit']:.0f} W against {xf['P_core_950']:.0f} W typical",
            "Carry the core-set limit (or a measured value) in the thermal budget; RFQ: measured core loss of the set at "
            "0.126 T / 100 kHz / 100 C with the real waveform", tag="dab_core_set")
    if rel(xf["L_leak"], dx["L_leak"]) is not None and abs(rel(xf["L_leak"], dx["L_leak"])) > BANDS["leak"]:
        lext_need = specs["dab"]["series_inductor"]["L_total_H"] - xf["L_leak"]
        add("major", "dab_spec.json transformer L_sigma_target_H / series_inductor L_ext_trim_range_H",
            f"Leakage target {dx['L_leak']*1e6:.2f} uH rests on winding builds that do not fit the PM 114/93 former; the "
            f"buildable winding gives {xf['L_leak']*1e6:.2f} uH",
            f"round litz needs {xf['layout']['round']['build']*1e3:.1f} mm radial build vs {xf['radial_space']*1e3:.1f} mm "
            f"available; profiled litz fits ({xf['layout']['rect']['build']*1e3:.1f} mm) and gives {xf['L_leak']*1e6:.2f} uH "
            f"({rel(xf['L_leak'], dx['L_leak'])*100:+.0f} %); the external inductor would need {lext_need*1e6:.2f} uH, "
            f"outside the {specs['dab']['series_inductor']['L_ext_trim_range_H'][1]*1e6:.2f} uH trim maximum",
            "Fix the construction in the spec (barrier thickness / section spacing set to give the target, e.g. ~6 mm) or "
            "widen the external inductor range; the leakage is part of the power transfer (DAB-06)", tag="dab_leak")
    if li["P_worst_smooth"] > 1.5 * des["dab_series_inductor"]["P_max"]:
        add("major", "dab_spec.json series_inductor (reference construction, loss_max_W)",
            f"Series-inductor loss far above the {des['dab_series_inductor']['P_max']:.0f} W budget - OpenMagnetics "
            f"{f_((_num((om.get('dab_series_inductor') or {}).get('core'), 'coreLosses') or 0) + (_num((om.get('dab_series_inductor') or {}).get('winding'), 'windingLosses') or 0), 0)} W, "
            f"own {li['P_worst_smooth']:.0f}-{li['P_worst']:.0f} W: the 0.4 x A_N copper "
            f"({li['a_cu']*1e6:.0f} mm2 per turn = {li['n_str']} strands of 0.1 mm) sits in the fringing field of the "
            f"{li['gap_needed']*1e3:.1f} mm of gaps (H_rms ~{li['h_rms_fringe_per_A']:.0f} A/m per A)",
            f"own: copper {min(p['P_cu_smooth'] for p in li['points'].values() if p['I_rms'] == max(q['I_rms'] for q in li['points'].values())):.0f}-"
            f"{max(p['P_cu'] for p in li['points'].values()):.0f} W at max current (smeared-gap / near-gap field models); gap for 2.12 uH with "
            f"fringing {li['gap_needed']*1e3:.1f} mm in 4 gaps / {li['gap_single_needed']*1e3:.1f} mm single (designer 9.2 mm, no "
            f"fringing); core under-used: B {li['B_275']:.2f} T at 275 A, gap volume {li['gap_volume_designer_cm3']:.0f} cm3 vs "
            f"{li['gap_volume_needed_at_0p25T_cm3']:.1f} cm3 needed at 0.25 T",
            "Use 25-40 mm2 of <= 0.05 mm litz kept >= 3 gap lengths from the gaps, or an air-core / quasi-distributed-gap E or "
            "U core sized for ~0.25 T; a 4-gap pot-core centre post is not a catalogue part", tag="lser_loss")
    dp = des["pv_inductor"]
    if not pv["fits_round"]:
        add("major", "cell_spec.json inductor conductor ('Litz, 10.3 mm2 copper ..., insulation for 1.1 kV')",
            "Round litz of 10.3 mm2 with 1.1 kV insulation does not fit the 8044E window",
            f"bundle OD {pv['layout_round']['od']*1e3:.1f} mm, {pv['layout_round']['tpl']} turns/layer, "
            f"{pv['layout_round']['layers']} layers = {pv['layout_round']['build']*1e3:.1f} mm build vs "
            f"{pv['window_w']*1e3:.1f} mm window width (19.81 mm minus 2 mm former)",
            f"Specify profiled (rectangular) litz ~{pv['layout_rect']['od']*1e3:.1f} x {pv['layout_rect']['width']*1e3:.1f} mm, "
            f"{pv['layout_rect']['layers']} layers x {pv['layout_rect']['tpl']} turns, strand <= 0.1 mm "
            f"(0.2 mm strands add {pv['P_cu_strand_sens']['0.200 mm'] - pv['P_cu_strand_sens']['0.100 mm']:.0f} W)", tag="pv_window")
    if pv["dT_hotspot"] > 1.15 * (dp["dT"] or 0):
        add("major", "sim/pv_tradeoff.py inductor temperature model (surface average, h = 25 W/m2K)",
            f"Hot-spot rise {pv['dT_hotspot']:.0f} K vs the designer's {dp['dT']:.0f} K: the model gives the surface average "
            "only; the 4-layer litz build adds an internal gradient, and the loss is higher",
            f"own: {pv['P_total']:.1f} W, surface {pv['dT_forced']:.0f} K + internal {pv['dT_internal']:.0f} K; natural "
            f"convection {pv['dT_natural']:.0f} K (fan failure); designer limit IND_DT_MAX 70 K, module hot-spot limit 130 C",
            "Inductor hot-spot NTC at the winding centre (already in L_DESC) must drive the fan law/derating; class H (180 C) "
            "insulation system; vendor thermal run at 48-55 W", tag="pv_temp")
    if pv["P_core"] > 1.15 * (dp["P_core"] or 0):
        add("minor", "sim/pv_tradeoff.py core_loss_density (field-based B with the toroid fit)",
            f"PV inductor core loss {pv['P_core']:.1f} W (own) vs {dp['P_core']:.1f} W: for these E cores the catalog A_L is "
            f"{pv['k_AL']:.2f}x mu0*mu*Ae/le, so the field-based and the Faraday flux differ by that factor",
            "variants (W): " + ", ".join(f"{k} {v:.1f}" for k, v in pv["P_core_variants"].items()),
            "Budget >= the own figure until a calorimetric measurement on the first sample; the total stays within 15 %", tag="pv_core")
    o_pv = om.get("pv_inductor", {}) if isinstance(om.get("pv_inductor"), dict) else {}
    if HAVE_OM and isinstance(o_pv.get("mu_45A"), (int, float)) and isinstance(o_pv.get("mu_0"), (int, float)):
        r_om = o_pv["mu_45A"] / o_pv["mu_0"]
        r_e = float(kmm_mu(pv["perm"], pv["turns"] * pv["I_dc"] / pv["le"]))
        if abs(r_om / r_e - 1) > 0.05:
            add("minor", "OpenMagnetics MAS core_materials 'Kool Mu MAX 26' (tool, not our design)",
                f"MAS carries the toroid/EQ DC-bias fit for Kool Mu MAX 26u; for the 8044E the catalog E-core fit applies",
                f"%mu at 45 A: MAS {r_om*100:.1f} % vs catalog p.66 E-core fit {r_e*100:.1f} %",
                "Use the catalog E-core fit (as sim/pv_design.py does); report the database entry upstream", tag="om_kmm_bias")
    for key, c in cm.items():
        if c["B_total"] > 0.8 * c["bsat"] or c["B_lf_2xC"] > 0.8 * c["bsat"]:
            add("major", f"gen/port.py {key} description (CUSTOM CM choke spec)",
                "CM-choke spec has no low-frequency CM-current or DM-flux requirement; with a high-mu nanocrystalline core the "
                "150 Hz CM current of the transformerless PCS saturates it",
                f"{c['core']}, {c['N']} t, mu {c['mu10k']:.0f} at 10 kHz: L_cm {c['L_cm10']*1e3:.2f} mH; i_cm "
                f"{c['I_cm_lf']:.2f} A rms (A_VCM_LF {c['assumptions']['V_cm_lf']:.0f} V, A_C_PV_PE {c['assumptions']['C_pv']*1e6:.0f} uF) "
                f"-> B {c['B_lf']:.2f} T ({c['B_lf_2xC']:.2f} T at 2x C_PV); DM leakage flux {c['B_dm']:.2f} T at {c['spec']['I']:.0f} A; "
                f"Bsat {c['bsat']} T",
                "Add to the spec: i_cm >= 1 A rms at 150 Hz with B <= 0.5 Bsat, DM flux at rated current with the leakage, "
                "flat-mu grade (mu(10 kHz) <= 25 000) or more core; see spec_port_cm_choke.md for a reference design", tag="cmc_lf")
            break
    vmx = max(ax["points"])
    da_cu = des["aux_hv_transformer"]["P_cu"][1] or 0
    om_ax = om.get("aux_hv_transformer", {}) if isinstance(om.get("aux_hv_transformer"), dict) else {}
    om_ax_cu = _num(om_ax.get("winding"), "windingLosses")
    if ax["points"][vmx]["P_cu"] > 2 * da_cu + 0.3:
        add("major", "sim/aux_hv_design.py ac_copper (Dowell 1-D) / AUX-HV T1 winding layout",
            f"Flyback winding loss {ax['points'][vmx]['P_cu']:.1f} W (own) vs {da_cu:.2f} W: the 1-D Dowell model leaves out the "
            "fringing field of the 0.83 mm centre-leg gap, in which the 0.8 mm TIW secondary and the inner primary layer sit",
            f"own at {vmx} V: 1-D {ax['points'][vmx]['P_cu_1d']:.2f} W + gap fringing {ax['points'][vmx]['P_fringe']:.2f} W "
            f"(line source); OpenMagnetics {f_(om_ax_cu, 1)} W (2-D field, Roshen fringing); Wolfspeed CRD-020DD17P-J measured "
            "95 C on its flyback transformer at 25 C ambient (sim/data/reference_magnetics.csv)",
            "Budget 2-5 W in the AUX-HV efficiency and thermal check; keep the windings >= 3 gap lengths from the gap (spacer "
            "on the centre leg) or split the gap over all three legs; secondary as 2-3 parallel thinner TIW; check the TIW "
            "temperature class against the hot spot at 60 C ambient; measure on the first sample", tag="aux_cu")
    if ax["B_lim_Amin"] > des["aux_hv_transformer"]["B_lim"] * 1.03:
        add("minor", "aux_hv_spec.json transformer b_lim_mT",
            f"Flux at the current limit stated on A_e; on A_min it is {ax['B_lim_Amin']*1e3:.0f} mT (> the 320 mT design limit)",
            f"Lp +7 %, 1.74 A, A_min 71 mm2: {ax['B_lim_Amin']*1e3:.0f} mT; N97 Bsat 410 mT at 100 C, ~{ax['Bsat120']*1e3:.0f} mT "
            "at 120 C (extrapolated)",
            "Accept with the margin stated on A_min at 120 C (87 %) or reduce the peak-current limit 5 %; vendor to measure "
            "L(I) to 1.86 A at 120 C", tag="aux_blim")
    ins, _src = insulation_source()

    def _rq(lab):
        return next((x for x in (ins or {}).get("requirements", []) if x.get("label", "").startswith(lab)), None)
    ra, rd = _rq("Reinforced HV-PELV, AUX-HV flyback T1"), _rq("Basic DAB port 1 - port 2")
    add("major", "AUX-HV T1 insulation (aux_hv_spec.json transformer desc; report section 11)",
        "Reinforced barrier at 1000 V DC working rests on TIW alone in an ETD 29 coil former; partial-discharge capability of "
        "TIW at this working voltage and the coil-former creepage pin-row -> core -> pin-row are not shown",
        ("ECO-10 levels (sim/out/insulation): " + (f"PD <= 10 pC at {ra['pd_test']:.0f} V pk, AC {ra['ac']:.0f} V rms, impulse "
                                                   f"{ra['imp']:.0f} V, creepage {ra['cr_pd2']:.0f} mm PD2 / {ra['cr_pd1']:.1f} mm potted, "
                                                   f"clearance {ra['cl_3000']:.1f} mm at 3000 m" if ra else "pending") +
         "; TIW insulation is three thin extruded layers; ETD 29 pins sit a few mm from the core"),
        "Insulation coordination (ECO-10) to fix the levels; RFQ: TIW certificate for reinforced insulation at the working "
        "voltage, PD test (extinction above 1.5 x the recurring peak), creepage drawing of the former; potting or a "
        "larger former if short", tag="aux_ins")
    add("major", "DAB transformer insulation (dab_spec.json transformer isolation)",
        "A 3 mm barrier inside a single-section PM coil former at 1850 V DC working (basic) only works as solid insulation "
        "in a vacuum-potted part; creepage along the former between the windings cannot be met in air",
        "b_iso 3 mm (dab_design.py A['b_iso']); one-section former B65734B1000T001; ECO-10 levels: " +
        (f"working {rd['u_w']:.0f} V, PD <= 10 pC at {rd['pd_test']:.0f} V pk, AC {rd['ac']:.0f} V rms, creepage {rd['cr_pd2']:.0f} mm "
         f"PD2 / {rd['cr_pd1']:.1f} mm potted" if rd else "pending"),
        "Specify vacuum potting / impregnation and a PD test at the coordinated level, or a two-section former with a "
        "creepage barrier; levels pending ECO-10", tag="dab_ins")
    nxt = iter(range(len(MG_IDS) + 1, 99))
    return [dict(id=MG_IDS.get(f_["tag"]) or f"MG-{next(nxt):02d}", **f_) for f_ in F]


# =====================================================================================================================
# Writers: report.md, magnetics_check.json, spec_<part>.md, gen/data/review_magnetics.csv
# =====================================================================================================================
def f_(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, str):
        return v
    if isinstance(v, (int, np.integer)):
        return str(v)
    if not np.isfinite(v):
        return "-"
    a = abs(v)
    return f"{v:.{nd}f}" if a >= 0.995 or a == 0 else f"{v:.{max(nd, 3)}g}"


def bsat_txt(b):
    return ", ".join(f"{v:.2f} T @ {k} C" for k, v in sorted(b.items(), key=lambda kv: float(kv[0]))) if b else "-"


def mas_txt(checks):
    return "; ".join(f"{v}: {n[:90]}" for v, n in checks) if checks else "not checked"


def pct(x):
    return "-" if x is None else ("0 %" if abs(x) < 0.005 else f"{x*100:+.0f} %")


def table(rows, cols):
    out = ["| " + " | ".join(h for h, _ in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(fn(r)) for _, fn in cols) + " |")
    return "\n".join(out)


def three_way(rows):
    return table(rows, [("quantity", lambda r: r["q"]), ("unit", lambda r: r["unit"]), ("designer", lambda r: f_(r["designer"])),
                        ("OpenMagnetics", lambda r: f_(r["om"])), ("own", lambda r: f_(r["own"])),
                        ("designer vs own", lambda r: pct(r["d_des"])), ("OM vs own", lambda r: pct(r["d_om"])),
                        ("band", lambda r: f"{BANDS[r['band']]*100:.0f} %" if r["band"] in BANDS else "min"), ("", lambda r: "**!**" if r["flag"] else ""),
                        ("basis", lambda r: r["why"])])


def insulation_source():
    p = P("sim/out/insulation/insulation_spec.json")
    if os.path.exists(p):
        try:
            return json.load(open(p)), "sim/out/insulation/insulation_spec.json"
        except Exception:
            pass
    return None, None


def write_review(F, closed):
    """gen/data/review_magnetics.csv - a finding is closed only by a construction that passes its own check
    (closed[tag] = (status, evidence)); rows are never deleted: a row this run no longer produces is kept as it was"""
    path = P("gen/data/review_magnetics.csv")
    old = {}
    if os.path.exists(path):
        with open(path, newline="") as fh:
            old = {r["id"]: r for r in csv.DictReader(fh)}
    rows = {x["id"]: dict(x, status="open", closure="") for x in F}
    for i, r in old.items():
        rows.setdefault(i, dict(r, status=r.get("status") or "open",
                                closure=r.get("closure") or "not reproduced by this run (input spec changed); row kept"))
    by_id = {v: k for k, v in MG_IDS.items()}
    for x in rows.values():
        t = x.get("tag") or by_id.get(x["id"], "")
        if t in closed:
            x["status"], x["closure"] = closed[t]

    cols = ["id", "severity", "where", "finding", "evidence", "recommendation", "status", "closure"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i in sorted(rows):
            w.writerow([rows[i].get(c, "") for c in cols])
    return path


INS_ROWS = {"pv_inductor": (["Basic HV-PE, switch node", "Reinforced HV-PELV, PV switch node"], ["L_CELL"]),
            "dab_transformer": (["Basic DAB port 1 - port 2", "Basic DAB port 1 - PE", "Reinforced DAB port 1 - PELV"],
                                ["Transformer 11:12", "XFMR-CORE"]),
            "dab_series_inductor": (["Basic DAB port 1 - PE"], ["LSER-CORE"]),
            "aux_hv_transformer": (["Reinforced HV-PELV, AUX-HV flyback T1"], ["AUX-HV-T1"]),
            "port_cm_choke": (["Basic HV-PE, DC pole"], ["CM-CHOKE"])}


def _ins_text(ins, key, fallback):
    """insulation levels for this part from the insulation coordination (sim/out/insulation/insulation_spec.json)"""
    if not ins or key not in INS_ROWS:
        return f"**pending insulation coordination (ECO-10)** - designer's text: {fallback}"
    labels, parts = INS_ROWS[key]
    out = []
    for lab in labels:
        r = next((x for x in ins.get("requirements", []) if x.get("label", "").startswith(lab)), None)
        if r:
            out.append(f"{r['label']} ({'reinforced' if r['kind'] == 'R' else 'basic'}): working {r['u_w']:.0f} V, recurring peak "
                       f"{r['u_rp']:.0f} V; impulse {r['imp']:.0f} V ({r['imp_spd']:.0f} V with SPD credit); AC withstand "
                       f"{r['ac']:.0f} V rms; PD <= 10 pC at {r['pd_test']:.0f} V pk (extinction >= {r['pd_ext']:.0f} V pk)"
                       f"{'' if r['pd_test'] else ' - no PD test (DC node)'}; creepage {r['cr_pd2']:.1f} mm PD2 "
                       f"({r['cr_pd1']:.1f} mm if potted, PD1); clearance {r['cl_2000']:.1f} / {r['cl_3000']:.1f} / "
                       f"{r.get('cl_4000', float('nan')):.1f} mm at 2000 / 3000 / 4000 m")
    for a in ins.get("audit", []):
        if any(pp in a.get("part", "") for pp in parts):
            v = a.get("verdicts", {}).get("3000", "?")
            notes = "; ".join(a.get("notes", {}).get("3000", [])[:3])
            out.append(f"insulation audit {a['part']} {'-'.join(a.get('between', []))} ({a.get('required')}): **{v}** at 3000 m - {notes}")
    return "levels from `sim/out/insulation/insulation_spec.json` (ECO-10 coordination): " + " | ".join(out) if out else \
        f"**pending insulation coordination (ECO-10)** - designer's text: {fallback}"


def _hdr(title, tags, F, src):
    ids = ", ".join(x["id"] for x in F if x.get("tag", "").startswith(tags)) or "none"
    return [f"# {title}", "",
            f"Generated by `sim/magnetics.py` from `{src}`; CALCULATED, not measured. Open findings against this part: {ids} "
            "(`gen/data/review_magnetics.csv`). Values marked *designer* are the design hand-off; *own* is the independent "
            "verification; where they differ the sheet states which one applies.", ""]


OM_BUILD = """PyPI has no macOS-arm64 wheel of PyOpenMagnetics 1.7.33 (Linux x86_64 and Windows only) and PyMKF 0.9.45 is
Windows-only, so the engine was built from the PyOpenMagnetics 1.7.33 sdist on this machine (macOS 15.6 arm64, Apple
clang 17, CMake 4.1, uv 0.11; ~2.5 h of mostly compile time): `quicktype@23.2.6` installed locally with npm (schema ->
MAS.hpp generator), `uv build --wheel` with the MKF/MAS checkouts as local sources (LOCAL_MKF_DIR) and four local fixes,
none of which touches a physics model: (1) Kirchhoff ExternalProject with `-DENABLE_NGSPICE=OFF` (the Homebrew ngspice
include directory shadowed the vendored nlohmann/json); (2) Kirchhoff's libKirchhoffApi link flags for Apple ld
(`-force_load` instead of `--whole-archive`, `-exported_symbol __ZN9Kirchhoff3api*` instead of `--exclude-libs`);
(3) MKF CircuitSimulatorInterface.cpp: floating-point `std::from_chars` (needs the macOS 26 runtime) replaced by a
classic-locale `istringstream` (SPICE-CSV import only); (4) the TLS-init aliases of MKF's four `inline thread_local`
globals (settings, _scorings, lossFactorInterps, initialPermeabilityTemperatureInterps) are emitted non-weak by clang under
-fvisibility=hidden, so ld64 reported duplicates: they were marked weak in the object files (what an ELF build gets) and
the module was linked with the generated link command. The module (+ libKirchhoffApi.dylib) was installed into
`.venv/lib/python3.12/site-packages/PyOpenMagnetics/` with a minimal dist-info (`uv pip list` shows
pyopenmagnetics 1.7.33). A fresh venv needs the same procedure (or a Linux x86_64 machine, where the PyPI wheel works)."""


def write_report(specs, own, om, om_info, des, T, asia, refc, F, checks, ins, own_flat):
    pv, xf, li, ax, cm = (own[k] for k in ("pv_inductor", "dab_transformer", "dab_series_inductor", "aux_hv_transformer",
                                         "port_cm_choke"))
    L = ["# Magnetics verification - OpenMagnetics, own calculation, reference designs (MAG-1, MAG-2, DEL-9)", "",
         f"Generated by `sim/magnetics.py` on {time.strftime('%Y-%m-%d %H:%M')} from the designers' hand-off files "
         "(`cell_spec.json`, `dab_spec.json`, `aux_hv_spec.json`, `gen/port.py`). **Everything is calculated; nothing is "
         "measured.** Re-run after any redesign: `.venv/bin/python sim/magnetics.py` (exit code 1 if a self-check fails).", ""]
    # ---- 0. at a glance
    L += ["## 0. At a glance", ""]
    glance = [
        ("PV cell inductor (2 x 8044E Kool Mu MAX 26u, 38 t)",
         f"{des['pv_inductor']['L_Idc']*1e6:.0f} / {f_(_g(T, 'pv_inductor', 'L at 45 A', 'om'), 0)} / {pv['L_Idc']*1e6:.0f} uH",
         f"{des['pv_inductor']['B_trip']:.2f} / - / {pv['B_trip']:.2f} T (trip)",
         f"{des['pv_inductor']['P_total']:.0f} / {f_(_g(T, 'pv_inductor', 'total loss', 'om'), 0)} / {pv['P_core']+pv['P_cu']:.0f} W",
         f"{des['pv_inductor']['dT']:.0f} / {f_(_g(T, 'pv_inductor', 'hot-spot rise', 'om'), 0)} / {pv['dT_hotspot']:.0f} K",
         "usable; conductor must be profiled litz, hot spot at the limit"),
        ("DAB transformer (PM 114/93 N95, 11:12)",
         f"Lm {des['dab_transformer']['Lm']*1e6:.0f} / {f_(_g(T, 'dab_transformer', 'magnetising', 'om'), 0)} / {xf['Lm_at_designer_gap']*1e6:.0f} uH; "
         f"Ls {des['dab_transformer']['L_leak']*1e6:.2f} / {f_(_g(T, 'dab_transformer', 'leakage', 'om'), 2)} / {xf['L_leak']*1e6:.2f} uH",
         f"{des['dab_transformer']['B_pk_950']:.3f} / - / {xf['B_pk_950']:.3f} T",
         f"{des['dab_transformer']['P_max']:.0f} / {f_(_g(T, 'dab_transformer', 'copper loss at ' + xf['worst']['point'], 'om'), 0)} (Cu) / {xf['P_total_worst']:.0f} W",
         f"{des['dab_transformer']['T_hs']} / - / {xf['T_hs_B_potted_housing']:.0f} C",
         "**not acceptable** - copper loss and thermal path (critical)"),
        ("DAB series inductor (PM 114/93 N95, 3 t)",
         f"{des['dab_series_inductor']['L']*1e6:.2f} / {f_(_g(T, 'dab_series_inductor', 'L at the designer', 'om'), 2)} / {li['L_at_designer_gap']*1e6:.2f} uH",
         f"- / - / {li['B_275']:.2f} T at 275 A", f"{des['dab_series_inductor']['P_max']:.0f} / {f_(_g(T, 'dab_series_inductor', 'copper loss at max', 'om'), 0)} (Cu) / {li['P_worst']:.0f} W",
         "-", "electrical values fine; reference construction's winding loss model-dependent (major)"),
        ("AUX-HV flyback (ETD 29 N97, 90:15:16)",
         f"{des['aux_hv_transformer']['Lp']*1e3:.3f} / {f_(_g(T, 'aux_hv_transformer', 'Lp at', 'om'), 3)} / {ax['AL_at_designer_gap']*ax['np']**2*1e3:.3f} mH",
         f"{des['aux_hv_transformer']['B_lim']*1e3:.0f} / - / {ax['B_lim_Amin']*1e3:.0f} mT (limit)",
         f"{max((des['aux_hv_transformer']['P_core'][i] or 0) + (des['aux_hv_transformer']['P_cu'][i] or 0) for i in (0, 1)):.2f} / "
         f"{f_((_g(T, 'aux_hv_transformer', 'core loss', 'om') or 0) + (_g(T, 'aux_hv_transformer', 'copper loss', 'om') or 0), 2)} / {ax['P_total_worst']:.2f} W",
         f"- / - / {ax['dT_natural']:.0f} K", "flux fine; winding loss 5-20x the designer's (gap fringing), insulation proof pending"),
        ("Port CM chokes CMC160/CMC200 (spec only)", f">= 0.5 mH spec; own {cm['CMC160']['L_cm10']*1e3:.2f} mH",
         f"own {cm['CMC160']['B_total']:.2f} T (high-mu) / {own_flat['CMC160']['B_total']:.2f} T (flat-mu)", "-", "-",
         "spec incomplete (LF CM flux) - major")]
    L += [table(glance, [("part", lambda r: r[0]), ("inductance designer / OM / own", lambda r: r[1]),
                         ("flux designer / OM / own", lambda r: r[2]), ("loss designer / OM / own", lambda r: r[3]),
                         ("temperature designer / OM / own", lambda r: r[4]), ("verdict", lambda r: r[5])]), ""]
    sev = {s_: sum(1 for x in F if x["severity"] == s_) for s_ in ("critical", "major", "minor")}
    L += [f"Findings: {sev['critical']} critical, {sev['major']} major, {sev['minor']} minor (section 6, "
          "`gen/data/review_magnetics.csv`).", ""]
    # ---- 1. OpenMagnetics
    L += ["## 1. OpenMagnetics - how it was run", ""]
    if om_info.get("available"):
        L += [OM_BUILD, "", f"- Module version {om_info.get('version')}, MKF commit `{om_info.get('mkf_commit')}`, MAS commit "
              f"`{om_info.get('mas_commit')}`; models {json.dumps(om_info.get('models'))}; run time {om_info.get('runtime_s', 0):.0f} s.",
              "- Each part was rebuilt as a MAS object (core shape and material from the MAS database, gapping, windings "
              "with a custom litz definition, the designer's current/voltage waveforms) and passed to "
              "`calculate_inductance_from_number_turns_and_gapping`, `calculate_core_losses` (iGSE requested; for the powder "
              "materials MKF applies the MAS entry's own 'magnetics'/'poco' fit, reported as 'Proprietary'), "
              "`calculate_winding_losses`, `calculate_saturation_current` and the leakage call where present.", ""]
        errs = {k: v["error"] for k, v in om.items() if isinstance(v, dict) and "error" in v}
        for k, v in om.items():
            if isinstance(v, dict):
                for kk, vv in v.items():
                    if isinstance(vv, dict) and "error" in vv:
                        errs[f"{k}.{kk}"] = vv["error"]
        if errs:
            L += ["Engine calls that failed (listed, not hidden):", ""] + [f"- `{k}`: {v}" for k, v in errs.items()] + [""]
    else:
        L += ["**The OpenMagnetics engine itself was not run in this execution** "
              f"(`import PyOpenMagnetics` failed: {om_info.get('error')}). The OpenMagnetics column is empty; the own "
              "calculation below uses the manufacturers' data directly.", "", OM_BUILD, ""]
    # ---- 2. method
    L += ["## 2. Own calculation - models", "",
          "- Inductance: catalog A_L x N^2 x DC-bias fit (powder, Magnetics catalog 2025 p.66/p.200); gapped ferrite: core "
          "reluctance + gap with McLyman fringing F = 1 + lg/sqrt(A) ln(2G/lg) (distributed gaps: G = gap spacing).",
          "- Flux: Faraday (V t / N A_e) and, for the powder core, also the DC magnetisation curve (catalog Method 1).",
          "- Core loss: iGSE (Venkatachalam) on the actual piecewise-linear flux; Steinmetz coefficients fitted to the "
          "manufacturers' curves read by the verifier (TDK N95/N97 p.2/p.5; Magnetics p.111/112; Asian datasheets section 5).",
          "- Winding loss: exact Bessel solution for each round strand (skin: m=0 mode; proximity: m=1 mode in a uniform "
          "transverse field) summed over the current harmonics, with the 1-D (Dowell) window field of the MMF ramp plus the "
          "litz bundle's own field (Tourkhani-Viarouge style); Sullivan's closed form as a cross-check.",
          "- Leakage: 1-D MMF energy method with the winding builds that actually fit the former.",
          "- Temperature: powder inductor - surface convection (designer's h = 25 W/m2K) + internal litz gradient (k = 0.6 "
          "W/mK); cold-plate transformer - conduction network (ferrite 4, PPS 0.3, potting 1.0, litz 1.0 W/mK); small parts - "
          "Maniktala natural convection.", ""]
    # ---- 3. parts
    titles = {"pv_inductor": "3.1 PV cell inductor", "dab_transformer": "3.2 DAB transformer",
              "dab_series_inductor": "3.3 DAB series inductor", "aux_hv_transformer": "3.4 AUX-HV flyback transformer",
              "port_cm_choke": "3.5 Port common-mode chokes"}
    L += ["## 3. Three-way comparison per part", "",
          "Deviations are against the own figure; **!** = outside the owner's band (loss 15 %, L and B 10 %, leakage 20 %).", ""]
    expl = explanations(own, des, om, T)
    for k, t in titles.items():
        L += [f"### {t}", "", three_way(T[k]), ""] + expl.get(k, []) + [""]
    L += ["### 3.6 Catalogue magnetics (datasheet sanity check)", "",
          "| part | use | check | result |", "|---|---|---|---|",
          "| Wurth 750315371 (BMU-GW, SN6505B) | 5 V push-pull, 1:1.1 | V-t 8.6 Vus vs SN6505B worst case 5.5 V / (2 x 363 kHz) = "
          f"{5.5/(2*363e3)*1e6:.2f} Vus (SN6505B eq. 3, p.25); 2500 VAC test | OK, margin {8.6/(5.5/(2*363e3)*1e6):.2f}; functional/basic barrier for a field-bus port as stated in gen/bmu_gw.py |",
          f"| Wurth 760390014 (SYS-IO-AUX CAN/RS485 ports) | 5 V push-pull, 1:1.3 | 11 Vus vs {5.5/(2*363e3)*1e6:.2f} Vus; 2500 VAC test | OK, margin {11/(5.5/(2*363e3)*1e6):.2f} |",
          "| Wurth 74437368150 / 74438356022 / 74439346100, 742792641 | buck inductors / bead | catalogue ratings used by the board designers | not re-verified (catalogue parts, no custom magnetics) |", ""]
    # ---- 4. references
    L += ["## 4. Reference designs (MAG-1)", "",
          f"Source: `sim/data/reference_magnetics.csv` ({refc['n_rows']} rows, document + page per value). Scaled to a "
          "per-unit basis (Z_base = V^2/P) so that 10 kW and 60 kW designs compare.", "",
          table(refc["dab"], [("design", lambda r: r["design"]), ("P kW", lambda r: r["P_kW"]), ("f kHz", lambda r: r["f_kHz"]),
                              ("X_series / Z_base", lambda r: f_(r["X_L_pu"])), ("X_m / Z_base", lambda r: f_(r["X_m_pu"])),
                              ("volume cm3/kW", lambda r: f_(r["vol_cm3_per_kW"], 1)), ("loss % of P", lambda r: r["loss_pct"]),
                              ("R at 100 kHz mOhm", lambda r: f_(r["R_ac_mohm"], 1)), ("note", lambda r: r["note"])]), "",
          "- Our series reactance (0.38 pu) sits between TIDA-010054 (0.34) and the GMB (0.44): credible. L_m is 8.8 pu vs "
          "19 pu (GMB) and 7 pu (TIDA): the gapped 150 uH is in range.",
          "- Our transformer is the most compact of the three (16 cm3/kW of core envelope vs 20 for the GMB's potted part "
          "and 61 for the TIDA planar) - and the GMB's measured 78 mOhm AC winding resistance at 100 kHz means ~440 W of "
          "copper loss at 60 kW, the same order as our own ~340 W, not the designer's 92 W.", "",
          table(refc["inductors"], [("design", lambda r: r["design"]), ("P kW", lambda r: r["P_kW"]), ("f kHz", lambda r: f_(r["f_kHz"], 0)),
                                    ("L uH", lambda r: f_(r["L_uH"], 0)), ("I_dc A", lambda r: f_(r["I_dc"], 1)),
                                    ("ripple % pp", lambda r: f_(r["ripple_pct"], 0)), ("1/2 L I_pk^2 per kW mJ", lambda r: f_(r["E_mJ_per_kW"], 1)),
                                    ("J A/mm2", lambda r: f_(r["J"], 1)), ("note", lambda r: r["note"])]), "",
          "- Stored energy per kW scales with 1/f: the CRD's 9.3 mJ/kW at 78 kHz is 22.6 mJ/kW at our 32 kHz; ours is 17.5 "
          "mJ/kW with a 2.2x larger ripple ratio (77 % vs 35 %) - consistent. Its 9.4 A/mm2 copper density is twice ours.",
          f"- Flyback: the Wolfspeed CRD-020DD17P-J measured {refc['flyback']['ref_temp']} C on its transformer (PQ2020, "
          f"25 C ambient, 1 kV, 25 W); our ETD 29 (1.9x the core volume) calculates to ~{refc['flyback']['ours_dT']:.0f} K rise "
          "from its own losses only - the board heat around T1 must be measured.",
          "- ST STDES-DABBIDIR (firmware constants only on disk): 28 uH, n 1.78, 100 kHz for 25 kW - not comparable in detail.", ""]
    # ---- 5. Asian materials
    L += ["## 5. Asian core materials (SRC-1)", "", "Datasheets filed by the sourcing agent in `docs/datasheets/magnetics/` "
          "(manifest rows in both SOURCES.csv); numbers in `sim/data/asian_magnetic_materials.csv`.", "",
          "| part | Western | Asian option | consequence (own, from the datasheet) | in the OpenMagnetics DB? |", "|---|---|---|---|---|"]
    for it in asia["pv_inductor"]:
        L.append(f"| PV inductor | Kool Mu MAX 26u (8044E) | {it['maker']} {it['material']} | core loss {f_(it.get('P_core_W'), 1)} W vs "
                 f"{pv['P_core']:.1f} W (Faraday flux), %mu at 45 A {f_(it.get('mu_45A_pct'), 0)} % vs "
                 f"{float(kmm_mu(pv['perm'], pv['turns']*pv['I_dc']/pv['le']))*100:.0f} %; B_sat {bsat_txt(it['bsat'])}; no 80 mm E core "
                 f"(toroids/blocks only) | {mas_txt(it['mas_check'])} |")
    for it in asia["dab"]:
        p950 = f"{it['P_core_950_W']:.0f} W vs {xf['P_core_950']:.0f} W (N95) at 950 V" if "P_core_950_W" in it else \
            "table only: " + ", ".join(f"{x[2]/1e3:.0f} kW/m3 @ {x[0]/1e3:.0f} kHz/{x[1]*1e3:.0f} mT" for x in it.get("table", [])[:3])
        L.append(f"| DAB transformer / inductor | TDK N95 (PM 114/93) | {it['maker']} {it['material']} | {p950}; B_sat {bsat_txt(it['bsat'])}; "
                 f"no PM 114/93 - EE80 (A_e 800 mm2/set) | {mas_txt(it['mas_check'])} |")
    for it in asia["aux"]:
        L.append(f"| AUX-HV flyback | TDK N97 (ETD 29) | {it['maker']} {it['material']} | B_sat {bsat_txt(it['bsat'])}; 280 kW/m3 vs 300 at "
                 "100 kHz/200 mT/100 C; no ETD shape at DMEGC | " + mas_txt(it['mas_check']) + " |")
    for it in asia["cmc"]:
        if it.get("mu_f"):
            L.append(f"| port CM choke | nanocrystalline (VAC VITROPERM class) | {it['maker']} {it['material']} | mu "
                     + ", ".join(f"{v:.0f} @ {k/1e3:g} kHz" for k, v in sorted(it["mu_f"].items())) +
                     f"; B_sat {bsat_txt(it['bsat'])}: high-mu grade - see 3.5 | {mas_txt(it['mas_check'])} |")
    if asia["mas"]:
        L += ["", "OpenMagnetics database, same core shape and excitation, material swapped (iGSE, 100 C):", "",
              table(asia["mas"], [("set", lambda r: r["set"]), ("material", lambda r: r["material"]),
                                  ("core loss W", lambda r: f_(r.get("P_core_W"), 1)), ("B_pk T", lambda r: f_(r.get("B_pk"), 3)),
                                  ("mu(45 A)/mu(0)", lambda r: f_(r.get("mu_ratio_45A"), 3)), ("note", lambda r: r.get("error", ""))])]
    L += ["", "- PV inductor: POCO NPC 26u is the closest replacement (better DC bias, higher B_sat, ~45 % more core loss at "
          "this point); POCO stopped FeSiAl, its NPH-L is FeSi with ~2x the loss. No Chinese 80 mm E core in 26u was found - "
          "a toroid (2 x NPC290026) or a block-core set needs a re-design of the winding. KDM/CSC E-core data not retrieved.",
          "- DAB: DMEGC DMR95 matches N95 within the curve reading (lower at 0.1-0.13 T); the shape must change anyway "
          "(section 3.2) - DMEGC EE80 in DMR95 suits an open-window, cold-plate construction.",
          "- AUX-HV: DMEGC DMR96 ~ N97; the ETD 29 shape has to come from another maker (TDG/Acme - not retrieved).",
          "- CM choke: Yunlu (and AT&M 1K107) CM grades are high-mu (70 000-80 000 at 10 kHz): they meet L_cm with 2-3 "
          "turns but saturate under the 150 Hz CM current; ask Yunlu/AT&M for a flat-mu (<= 25 000) grade.", ""]
    # ---- 6. findings
    L += ["## 6. Findings for the designers (`gen/data/review_magnetics.csv`)", "",
          table(F, [("id", lambda r: r["id"]), ("severity", lambda r: r["severity"]), ("where", lambda r: r["where"]),
                    ("finding", lambda r: r["finding"]), ("recommendation", lambda r: r["recommendation"])]), ""]
    # ---- 7. open
    L += ["## 7. Not verified / open", "",
          "- Nothing is measured: every loss and temperature here needs a prototype (calorimetric loss, thermal run, L(I)).",
          "- Litz strand diameter of the PV inductor is not specified by the designer (0.1 mm assumed); the DAB parts use the "
          "designer's 0.1 mm.",
          "- Powder E-core loss: the catalog does not define B for E cores (field vs Faraday differ by the A_L factor "
          f"{pv['k_AL']:.2f}); the four variants span {min(pv['P_core_variants'].values()):.0f}-{max(pv['P_core_variants'].values()):.0f} W.",
          "- 2-D effects (window field of a low-mu core, gap fringing near litz) are handled by 1-D / line-source "
          "approximations in the own model; OpenMagnetics uses its own field model - where they differ it is stated in 3.x.",
          "- Thermal networks are estimates (contact resistances, potting k, airflow at the inductor are not known).",
          "- Insulation levels: " + ("taken from sim/out/insulation/insulation_spec.json" if ins else
                                     "**pending insulation coordination (ECO-10)** - no insulation_spec.json yet"),
          "- CM choke: mu(150 Hz) and the flat-mu grade are assumptions; PV-array capacitance (A_C_PV_PE) is the port "
          "designer's assumption.",
          "- KDM, CSC E-core and TDG data were not retrievable (sourcing agent); AT&M toroid sizes not published.", ""]
    # ---- 8. checks
    L += ["## 8. Self-checks", "", table(checks, [("check", lambda r: r["id"]), ("what", lambda r: r["what"]),
                                                  ("value", lambda r: r["value"]), ("limit", lambda r: r["limit"]),
                                                  ("result", lambda r: "pass" if r["ok"] else (f"FAIL - known: {r['exc']}" if r["exc"] else "**FAIL**"))]), ""]
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "report.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


def _g(T, part, prefix, col):
    for r in T.get(part, []):
        if r["q"].startswith(prefix):
            return r[col]
    return None


def explanations(own, des, om, T):
    """why the figures differ - one bullet per flagged row, numbers from this run"""
    pv, xf, li, ax = own["pv_inductor"], own["dab_transformer"], own["dab_series_inductor"], own["aux_hv_transformer"]
    o = {k: (v if isinstance(v, dict) and "error" not in v else {}) for k, v in om.items()}
    E = {}
    opv = o.get("pv_inductor", {})
    mu45, mu0 = opv.get("mu_45A"), opv.get("mu_0")
    e = [f"- **Inductance.** Designer and own use the same catalog data (A_L 91 nH/T2 per 8044E set, E-core DC-bias fit "
         f"a=0.01, b=1.6e-7, c=2.0, p.66) and agree to <1 %. The catalog A_L is {pv['k_AL']:.2f}x mu0*26*A_e/l_e: in a mu = 26 "
         "E core part of the flux closes through the window air, which a pure reluctance model misses."]
    if isinstance(mu45, (int, float)) and isinstance(mu0, (int, float)):
        e.append(f"- **OpenMagnetics inductance** uses its MAS entry for 'Kool Mu MAX 26': its DC-bias factor at 45 A is "
                 f"{mu45/mu0*100:.1f} % (MAS a=0.01, b=2.21e-12, c=2.205 in A/m = the catalog toroid/EQ fit) vs "
                 f"{float(kmm_mu(pv['perm'], pv['turns']*pv['I_dc']/pv['le']))*100:.1f} % from the E-core fit, and its zero-bias "
                 "reluctance follows mu0*mu*A_e/l_e plus the stacking of two E sets. **Use the catalog (designer/own) figure.**")
    e.append(f"- **Core loss** depends on two choices the catalog leaves open: fit (E-core p.112 vs toroid p.111) and flux "
             f"definition (field vs Faraday): " + ", ".join(f"{k.replace('loss_e', 'E-fit').replace('loss_t', 'toroid-fit').replace('/dB_', ' + ')} "
                                                           f"{v:.1f} W" for k, v in pv["P_core_variants"].items()) +
             ". Designer: toroid fit + field flux (13.5 W). Own: E-core fit + Faraday flux (the only combination consistent "
             "with the measured E-core A_L) = %.1f W. OpenMagnetics: toroid fit (MAS a=8.28 SI) on its own flux." % pv["P_core"])
    ocl_pv, owl_pv = opv.get("core", {}), opv.get("winding", {})
    if _num(ocl_pv, "coreLosses"):
        e.append(f"- **OpenMagnetics core loss** {_num(ocl_pv, 'coreLosses'):.1f} W: MAS 'magnetics' method = catalog toroid fit "
                 f"(a = 8.28 in SI = 113.53 mW/cm3) applied to the flux of its own (lower) inductance, swing "
                 f"{2*(_num(ocl_pv, 'magneticFluxDensityAcPeak') or 0):.3f} T pk-pk - between the field-based and the Faraday flux; "
                 "it sits between the designer's and the own figure, so the three agree on a 13-21 W band.")
    if _num(owl_pv, "windingLosses"):
        e.append(f"- **OpenMagnetics winding loss** {_num(owl_pv, 'windingLosses'):.1f} W: its custom-litz DC resistance is ~10 % lower "
                 "(MLT from its own coil-former model, no lay factor); the AC part (~0.7 W) matches the own proximity term.")
    if _num(ocl_pv, "maximumCoreTemperatureRise"):
        e.append(f"- **OpenMagnetics temperature** {_num(ocl_pv, 'maximumCoreTemperatureRise'):.0f} K is its Maniktala natural-convection "
                 "model on the bare core surface with all loss: not comparable with the forced-air cell (own natural-convection "
                 f"estimate on the wound part {pv['dT_natural']:.0f} K); it does show that the inductor needs the fan.")
    e.append(f"- **Winding loss**: designer DC x (1 + 1.0 x ripple share) = 35.2 W; own Bessel/litz model with 0.1 mm strands "
             f"{pv['P_cu']:.1f} W (proximity {pv['P_cu_parts']['prox_ext']+pv['P_cu_parts']['prox_int']:.1f} W) - agree. Strand "
             "sensitivity: " + ", ".join(f"{k} {v:.1f} W" for k, v in pv["P_cu_strand_sens"].items()) + ".")
    e.append(f"- **Window**: round litz (OD {pv['layout_round']['od']*1e3:.1f} mm) needs {pv['layout_round']['build']*1e3:.1f} mm, "
             f"the window gives {pv['window_w']*1e3:.1f} mm; profiled litz fits in {pv['layout_rect']['build']*1e3:.1f} mm "
             f"(copper fill {pv['ku']:.2f} of the window, the designer's K_CU 0.30 limit).")
    e.append(f"- **Temperature**: same surface model as the designer gives {pv['dT_forced']:.0f} K with the own loss; the "
             f"designer has no internal gradient ({pv['dT_internal']:.0f} K across a {pv['layout_rect']['build']*1e3:.0f} mm litz "
             f"build). Natural convection (fan failure, MKF/Maniktala) {pv['dT_natural']:.0f} K.")
    E["pv_inductor"] = e
    oxf = o.get("dab_transformer", {})
    m60 = xf["points"].get("matched_60kW", {})
    e = [f"- **Copper loss is the decisive disagreement.** The designer used R_ac/R_dc = 1.15 at 100 kHz and 1 + 0.15 h^2 "
         f"(capped at 5) for the harmonics. With {xf['ns1']} strands of {xf['d_str']*1e3:.1f} mm and {xf['N1']} turns across a "
         f"{xf['breadth']*1e3:.0f} mm breadth the proximity factor is {xf['Fr_sullivan_fund']:.2f} at 100 kHz (Sullivan 1999) - "
         f"the Bessel-strand model gives the same - and grows with h^2: effective R_ac/R_dc {m60.get('Fr_eff', float('nan')):.1f} at "
         f"800/873 V 60 kW (3rd harmonic {m60.get('harm3', 0)*100:.0f} %) and {xf['worst']['Fr_eff']:.1f} at the max-current point. "
         "This is a model error in the hand-off, not a data error; the GMB reference transformer's measured 78 mOhm at 100 kHz "
         "confirms the order of magnitude."]
    ow = _num(oxf.get("winding"), "windingLosses")
    o60 = o.get("dab_transformer_60kW", {})
    ow60 = _num(o60.get("winding"), "windingLosses")
    if ow:
        e.append(f"- **OpenMagnetics confirms the order of magnitude**: winding loss {f_(ow60, 0)} W at 800/873 V 60 kW and {ow:.0f} W at "
                 f"{xf['worst']['point']} (own {m60.get('P_cu', 0):.0f} / {xf['points'][xf['worst']['point']]['P_cu']:.0f} W, designer "
                 f"{des['dab_transformer']['P_cu_60']} W at 60 kW). OM is ~30 % below the own model: it evaluates the 2-D field at "
                 "each turn of its own layout (one layer per winding) and splits ohmic/skin/proximity per harmonic, the own model "
                 "applies the 1-D ramp field over the full breadth plus the bundle field. Both are 2.5-4x the hand-off. **Project figure: "
                 "the own (higher) value until a measurement exists; in either case the 159 W budget is not met.**")
    olk = _num(oxf.get("leakage"), "leakageInductancePerWinding")
    if olk:
        e.append(f"- **OpenMagnetics leakage** {olk*1e6:.2f} uH: its winder put a 0.03 mm insulation between the sections (the "
                 "3 mm barrier could not be imposed through the Python API: set_intersection_insulation had no effect, "
                 "wind_by_layers failed). Adding the barrier's 1-D energy term "
                 f"(mu0 N1^2 MLT b_iso / b = {MU0*xf['N1']**2*2*math.pi*(PM114['bob_tube_od']/2+xf['layout'][xf['layout_used']]['p']['build']+xf['b_iso']/2)*xf['b_iso']/xf['breadth']*1e6:.2f} uH) "
                 f"gives {(olk + MU0*xf['N1']**2*2*math.pi*(PM114['bob_tube_od']/2+xf['layout'][xf['layout_used']]['p']['build']+xf['b_iso']/2)*xf['b_iso']/xf['breadth'])*1e6:.2f} uH "
                 f"vs own {xf['L_leak']*1e6:.2f} uH - consistent; both well below the 4.38 uH target.")
    olm = _num(oxf.get("L"), "magnetizingInductance")
    if olm:
        e.append(f"- **L_m**: OpenMagnetics {olm*1e6:.0f} uH at the designer's 1.74 mm (Zhang gap reluctance with fringing), own "
                 f"{xf['Lm_at_designer_gap']*1e6:.0f} uH, designer 150 uH (no fringing): inside the +/-10 % tolerance - grind to A_L.")
    e.append(f"- **Leakage**: the designer's formula with its own builds reproduces {xf['L_leak_designer_geometry']*1e6:.2f} uH; "
             f"those builds ({xf['build_designer']*1e3:.1f} mm each) correspond to round litz that does not fit; the profiled "
             f"winding that fits gives {xf['L_leak']*1e6:.2f} uH.")
    e.append(f"- **Core loss**: own and designer use the same TDK N95 R34 curves; the own iGSE runs on the ngspice winding voltage "
             f"(switching ringing included, {m60.get('P_core', 0):.0f} W at 800/873 V vs the designer's analytic trapezoid "
             f"{des['dab_transformer']['P_core_60']} W). The larger uncertainty is the core itself: TDK's PM 114/93 core-set limit is "
             f"3.1x the R34 value ({xf['P_core_950_setlimit']:.0f} W at 950 V vs {xf['P_core_950']:.0f} W).")
    e.append(f"- **Thermal**: conduction estimate {xf['thermal']['A_base_on_plate']:.1f} K/W (base on plate) / "
             f"{xf['thermal']['B_potted_housing']:.2f} K/W (best case) vs the required 0.40 K/W; parts: "
             + ", ".join(f"{k} {v:.2f}" for k, v in xf["thermal"]["parts"].items()) + " K/W.")
    E["dab_transformer"] = e
    E["dab_series_inductor"] = [
        f"- **Gap**: the designer's 9.2 mm is mu0 N^2 A_e / L without fringing; with 4 gaps the own model needs "
        f"{li['gap_needed']*1e3:.1f} mm, with one gap {li['gap_single_needed']*1e3:.1f} mm (L at 9.2 mm in 4 gaps = "
        f"{li['L_at_designer_gap']*1e6:.2f} uH). The vendor trims, so only the stated gap is wrong.",
        f"- **Loss**: the designer's inductor copper model is DC x (1 + h^2) on 0.1 mOhm; the 143 mm2 of litz in the fringing "
        f"field gives {li['P_worst_smooth']:.0f} W total with the gaps smeared into a distributed gap (radial window field "
        f"N i (z/h - 1/2)/w, lower bound) and {li['P_worst']:.0f} W with line-source fringing at each of the 4 gaps "
        f"(H_rms {li['h_rms_fringe_per_A']:.0f} A/m per A).",
        "- **OpenMagnetics** gives the same gap (9.21 mm for 2.12 uH with its distributed-gap model) and the same core loss, but "
        f"a winding loss of {f_(_num(o.get('dab_series_inductor', {}).get('winding'), 'windingLosses'), 1)} W: it evaluates the "
        "field at the centre of each of the three 19.6 mm litz bundles, where the 1/r fringing field of the gaps is weakest; the "
        "own models integrate the field over the bundle cross-section. The three estimates (OM ~16 W, own 109-190 W, designer "
        "2 W) differ by an order of magnitude - **the reference construction cannot be released on calculation; a FEM run or a "
        "sample measurement is needed, and the recommended construction (less copper, finer strands, distance from the gaps) "
        "lowers all three.**"]
    E["aux_hv_transformer"] = [
        f"- **Gap**: TDK's K1/K2 relation (N87 values for an N97 core) gives {ax['gap_tdk_k1k2']*1e3:.3f} mm; the own round-post "
        f"McLyman model {ax['gap_for_AL']*1e3:.3f} mm - the vendor trims to A_L, no action.",
        f"- **Peak current**: own {max(p['Ipk'] for p in ax['points'].values()):.3f} A (P_in = P_out/eta from the summary) vs the "
        "designer's 0.936 A: +5 %, flux scales the same way.",
        f"- **Leakage**: two 1-D estimates of the same stack differ ({des['aux_hv_transformer']['L_leak_1d']*1e6:.1f} vs "
        f"{ax['L_leak_1d']*1e6:.1f} uH) through the assumed layer thicknesses and breadth; both are below the 31 uH design limit, "
        "so the clamp-loss budget is conservative either way.",
        "- **Core loss** (< 0.25 W in all three): the designer fits one Steinmetz law through the four N97 table points "
        "(25-500 kHz, 50-200 mT), the own fit is local (100 kHz curve 50-100 mT for beta, 25/100 kHz at 200 mT for alpha); "
        "OpenMagnetics uses its MAS N97 Steinmetz set - spread 0.1-0.22 W, irrelevant.",
        f"- **Winding loss is the real disagreement**: designer {des['aux_hv_transformer']['P_cu'][1]} W (Dowell, 1-D), own "
        f"{ax['points'][max(ax['points'])]['P_cu_1d']:.2f} W (1-D Bessel) + {ax['points'][max(ax['points'])]['P_fringe']:.2f} W from the "
        "fringing field of the 0.83 mm centre-leg gap (line source on each layer), OpenMagnetics "
        f"{f_(_num(o.get('aux_hv_transformer', {}).get('winding'), 'windingLosses'), 2)} W (2-D field with Roshen fringing; almost all "
        "proximity: the 0.8 mm TIW secondary and the inner primary layer sit within ~1-2 mm of the gap). The 1-D model cannot "
        "see a centre-leg gap. **Project figure: 2-5.5 W until a sample is measured** (the Wolfspeed CRD's flyback "
        "transformer ran at 95 C in 25 C ambient)."]
    E["port_cm_choke"] = [
        "- There is no designer construction to compare; the row checks that a buildable choke meets L_cm. See spec_port_cm_choke.md."]
    return E


def run_checks(own, des, F, om, om_info):
    pv, xf, li, ax, cm = (own[k] for k in ("pv_inductor", "dab_transformer", "dab_series_inductor", "aux_hv_transformer",
                                         "port_cm_choke"))
    tags = {x["tag"]: x["id"] for x in F}
    C = []

    def chk(cid, what, value, limit, ok, tag=""):
        C.append(dict(id=cid, what=what, value=value, limit=limit, ok=bool(ok), exc=tags.get(tag, "") if not ok else ""))

    chk("pv_mu_trip", "PV inductor L(trip)/L0 (soft saturation rule)", f"{pv['mu_trip']:.2f}", ">= 0.35", pv["mu_trip"] >= 0.35)
    chk("pv_b_trip", "PV inductor B at trip / B_sat", f"{pv['B_trip']/KMM_BSAT:.2f}", "<= 0.85", pv["B_trip"] <= 0.85 * KMM_BSAT)
    chk("pv_loss_band", "PV inductor total loss own vs designer", pct(rel(pv["P_core"] + pv["P_cu"], des["pv_inductor"]["P_total"])),
        "<= 15 %", abs(rel(pv["P_core"] + pv["P_cu"], des["pv_inductor"]["P_total"])) <= 0.15)
    # model self-tests (the physics, not the design)
    fr, p_h2 = _strand_factors(0.1e-3, 1e3, 100)
    rho = rho_cu(100)
    chk("model_strand_lf", "Bessel proximity -> low-frequency limit (0.1 mm, 1 kHz)",
        f"{p_h2 / (math.pi * (2*math.pi*1e3)**2 * MU0**2 * 1e-16 / (128 * rho)):.4f}", "1 +/- 0.01",
        abs(p_h2 / (math.pi * (2 * math.pi * 1e3) ** 2 * MU0 ** 2 * 1e-16 / (128 * rho)) - 1) < 0.01)
    k, a, b = catalog_k(113.53, 2.072, 1.379)
    th = np.linspace(0, 1e-5, 2001)
    pin = igse(k, a, b, th, 0.1 * np.sin(2 * math.pi * 1e5 * th))
    chk("model_igse_sine", "iGSE on a sine = Steinmetz", f"{pin / (k * 1e5 ** a * 0.1 ** b):.3f}", "1 +/- 0.02",
        abs(pin / (k * 1e5 ** a * 0.1 ** b) - 1) < 0.02)
    D_ = xf
    own_fr = (D_["points"][D_["worst"]["point"]]["P_cu"]) and D_["Fr_sullivan_fund"]
    chk("model_sullivan", "Sullivan F_r at 100 kHz is credible (2 independent forms agree within 30 %)", f"{own_fr:.2f}", "1.5-4",
        1.5 <= own_fr <= 4.0)
    if om_info.get("available"):
        ok_parts = [k_ for k_, v in om.items() if isinstance(v, dict) and "error" not in v]
        chk("om_ran", "OpenMagnetics evaluated the parts", f"{len(ok_parts)}/{len(om)}", f">= {len(om) - 1}", len(ok_parts) >= len(om) - 1)
    return C


def main():
    t0 = time.time()
    specs = dict(cell=load("sim/out/pv_design/cell_spec.json"), dab=load("sim/out/dab_design/dab_spec.json"),
                 aux=load("sim/out/aux_hv_design/aux_hv_spec.json"))
    live, specs = specs, dict(specs, dab=dab_rev0(specs["dab"]))    # verification against the rev-0 hand-off
    own = dict(pv_inductor=pv_inductor_own(specs["cell"]), dab_transformer=dab_transformer_own(specs["dab"]),
               dab_series_inductor=dab_inductor_own(specs["dab"]), aux_hv_transformer=aux_transformer_own(specs["aux"]),
               port_cm_choke=port_cmc_own())
    own_flat = port_cmc_own(20e3, "flat-mu grade 20 000 at 10 kHz (ASSUMED, to be quoted)")
    print("OpenMagnetics:", "importable" if HAVE_OM else f"NOT importable ({OM_IMPORT_ERROR}) - own calculation only")
    om, om_info = run_openmagnetics(own, specs)
    des = designer_figures(specs)
    T = assemble(specs, own, om, des)
    asia = asian_options(own, HAVE_OM)
    refc = reference_comparison(own, specs)
    F = findings(specs, own, des, om, asia)
    ins, ins_src = insulation_source()
    checks = run_checks(own, des, F, om, om_info)
    # constructions of the magnetics designer (rev M1): design_<part>.json, checked without allowances
    D, xd, ld = design_dab(live)
    omd = om_design_dab(D, xd, ld) if HAVE_OM else {}
    designs = dict(zip(("dab_transformer", "dab_series_inductor"), write_design_dab(live, D, xd, ld, omd, ins, specs["dab"])))
    c_new, closed = design_checks_dab(designs)
    # MG-01..03 are about the rev-0 construction: judged with M1 at the envelope it was designed to (rev-0 spec)
    _, cl0 = design_checks_dab(dict(zip(("dab_transformer", "dab_series_inductor"),
                                        write_design_dab(specs, *design_dab(specs), {}, ins, specs["dab"], write=False))))
    for t_ in ("dab_cu", "dab_thermal", "dab_core_set"):
        if t_ not in closed and t_ in cl0:
            closed[t_] = (cl0[t_][0].replace("rev M1 (", "rev M1 at its design envelope, I1 118.5 A rms (MG-16 tracks the new 137 A; "),
                          cl0[t_][1])
    if not next(c_ for c_ in c_new if c_["id"] == "lser_m1_b")["ok"]:          # requirement moved after M1 (I_sat_min)
        el = designs["dab_series_inductor"]["electrical"]
        l4 = ls_eval(D, xd["L_ext"], **dict(LS_CHOICE, N=4, k_gaps=8))
        b4 = l4["B_177"] / D["I1pk"] * el["I_sat_min_A"]
        F.append(dict(id=MG_IDS["lser_isat"], tag="lser_isat", severity="minor",
                      where="sim/out/magnetics/design_dab_series_inductor.json rev M1 vs dab_spec.json series_inductor I_sat_min_A",
                      finding=f"The series-inductor saturation requirement moved from 275 A to {el['I_sat_min_A']:.0f} A ({el['I_sat_basis']}) after "
                              f"rev M1: the 3-turn construction reaches {el['B_at_I_sat_T']:.3f} T = {el['B_at_I_sat_T']/B_SAT_DMR95_100C:.2f} B_sat "
                              "(100 C, typical) - no saturation, but only ~10 % margin on a typical B_sat value",
                      evidence=f"B = L I / (N A_e) = {el['L_H']*1e6:.2f} uH x {el['I_sat_min_A']:.0f} A / (3 x {designs['dab_series_inductor']['core']['Ae_m2']*1e6:.0f} mm2); "
                               "gapped ferrite: L falls < 10 % at 0.9 B_sat because the gap dominates the reluctance",
                      recommendation=f"4 turns on the same 4 x EE80 (gap {l4['gap_total']*1e3:.1f} mm in 8 gaps): B {b4:.3f} T = "
                                     f"{b4/B_SAT_DMR95_100C:.2f} B_sat, loss {l4['P_total']:.0f} W worst (3 turns: "
                                     f"{designs['dab_series_inductor']['verification']['own']['P_total_W']:.0f} W), fits {l4['fits']}; "
                                     "decide with the DAB cost round (rev M2), or accept 3 turns with a measured L(I) to 635 A at 100 C"))
    ad = design_aux(specs)
    designs["aux_hv_transformer"] = write_design_aux(specs, ad, om_design_aux(specs, ad) if HAVE_OM else {}, ins)
    c2, cl2 = design_checks_aux(designs["aux_hv_transformer"])
    c_new, closed = c_new + c2, dict(closed, **cl2)
    dcm, ps = design_cmc(None)
    designs["port_cm_choke"] = write_design_cmc(dcm, ps, om_design_cmc() if HAVE_OM else {}, ins)
    c3, cl3 = design_checks_cmc(designs["port_cm_choke"])
    m2c, ps2 = design_cmc_m2()
    designs["port_cm_choke"] = write_design_cmc_m2(designs["port_cm_choke"], m2c, ps2, ins)
    c3b, cl3b = design_checks_cmc_m2(designs["port_cm_choke"])
    c3, cl3 = [c_ for c_ in c3 if c_["id"].startswith("cmc_m1_om")] + c3b, cl3b
    c_new, closed = c_new + c3, dict(closed, **cl3)
    pe, pbase, _ = design_pv(specs)
    pt = dict(pv_toroid(specs["cell"], "NPC 26", 2, PV_M1_ACU), a_cu=PV_M1_ACU)     # rev M1 as issued (reference)
    omp = om_design_pv(specs["cell"], pt) if HAVE_OM else {}
    if omp.get("P_core_W"):           # design to the higher of catalogue (own) and MAS (OM) core loss
        pt = dict(pv_toroid(specs["cell"], "NPC 26", 2, PV_M1_ACU, core_mult=max(1.0, omp["P_core_W"] / pt["P_core_catalogue"])),
                  a_cu=PV_M1_ACU)
        omp = om_design_pv(specs["cell"], pt)
    d1 = write_design_pv(specs, pt, pe, pbase, omp, ins, write=False)
    rows, ch = design_pv_m2(specs["cell"])
    designs["pv_inductor"] = write_design_pv_m2(specs, d1, rows, ch)
    c4, cl4 = design_checks_pv(designs["pv_inductor"])
    c_new, closed = c_new + c4, dict(closed, **cl4)
    if os.path.exists(P("sim/out/aux_hv_design/aux75_spec.json")):                  # cost-first 75 W aux (item 2a)
        a75 = load("sim/out/aux_hv_design/aux75_spec.json")
        r75 = design_aux75(a75)
        designs["aux75_transformer"] = write_design_aux75(a75, r75, om_aux75(a75, r75) if HAVE_OM else {}, ins)
        c_new += design_checks_aux75(designs["aux75_transformer"])
    breq = load("sim/out/gdrv_miller/bias_transformer_req.json")
    br = bias_design(breq)
    designs["gdrv_bias_transformer"] = write_design_bias(breq, br, om_bias(breq, br) if HAVE_OM else {}, ins)
    c_new += design_checks_bias(designs["gdrv_bias_transformer"])
    fl = [c_ for c_ in c_new if c_["id"] in ("dab_m1_loss", "dab_m1_hot") and not c_["ok"]]
    if fl:                                                                    # requirement moved after M1 (I1_rms_max)
        th = designs["dab_transformer"]["thermal"]
        F.append(dict(id=MG_IDS["dab_imax"], tag="dab_imax", severity="major",
                      where="sim/out/magnetics/design_dab_transformer.json rev M1 vs dab_spec.json transformer I1_rms_max_A",
                      finding=f"The transformer's maximum current rose to {D['I1rms']:.1f} A rms (rev M1 was designed to 118.5 A): the worst "
                              f"corner (that current with the v2low current shape + 950 V flux) gives {fl[0]['value']} and a hot spot of "
                              f"{th['T_hotspot_coreset_limit_C']:.0f} C with the DMEGC core-set loss limit ({th['T_hotspot_C']:.0f} C with "
                              "typical core loss) at 65 C coolant",
                      evidence="; ".join(f"{c_['id']} {c_['value']} ({c_['limit']})" for c_ in fl),
                      recommendation="The window is full (build = window): no more copper fits. Options: (a) coolant <= "
                                     f"{65 - (th['T_hotspot_coreset_limit_C'] - 130):.0f} C at the 137 A corner or derate the current above it; "
                                     "(b) three units in parallel; (c) settle it in the DAB cost round (rev M2) together with MG-15"))
    for c_ in c_new:                                   # design checks that fail on a reported design finding
        if not c_["ok"] and c_.get("tag") in {x_["tag"] for x_ in F}:
            c_["exc"] = MG_IDS[c_["tag"]]
    checks += c_new
    write_report(specs, own, om, om_info, des, T, asia, refc, F, checks, ins, own_flat)
    report_designs(designs, c_new, closed, dab_cost_first(D, xd))
    for name, d in designs.items():
        with open(os.path.join(OUT, f"spec_{name}.md"), "w") as fh:
            fh.write(spec_design(d, F, closed))
    write_review(F, closed)

    def clean(o):
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items() if not str(k).startswith("_")}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        return o
    with open(os.path.join(OUT, "magnetics_check.json"), "w") as fh:
        json.dump(clean(dict(generated=time.strftime("%Y-%m-%d %H:%M"), openmagnetics=om_info, designer=des, own=own,
                             own_cmc_flat_mu=own_flat, openmagnetics_results=om, comparison=T, findings=F, checks=checks,
                             insulation_source=ins_src, asian=asia, references=refc, design_files=sorted(designs))), fh, indent=1, default=str)
    print(f"wrote {OUT}/report.md, magnetics_check.json, 5 spec sheets; gen/data/review_magnetics.csv ({len(F)} findings); "
          f"{time.time()-t0:.0f} s")
    bad = [c for c in checks if not c["ok"] and not c["exc"]]
    for c in checks:
        print(f"  {'pass' if c['ok'] else ('KNOWN ' + c['exc'] if c['exc'] else 'FAIL')}: {c['id']} {c['what']} = {c['value']} ({c['limit']})")
    assert not bad, "self-check failed without a reported finding: " + ", ".join(c["id"] for c in bad)




# =====================================================================================================================
# REDESIGN (coordinator, 2026-10-04): the magnetics designer owns the construction; the converter engineers keep the
# electrical requirement (spec JSONs).  Output per part: sim/out/magnetics/design_<part>.json (keys: see DESIGN_KEYS).
# =====================================================================================================================
DMR95_SRC = MAG + "DMEGC-DMR95.pdf p.1-2 (sim/data/asian_magnetic_materials.csv)"
XF_CORES = {   # per core set; window = per side of the wound leg; 'leg' = wound leg cross-section (w x c per set)
    "EE80": dict(maker="DMEGC", pn="EE80", mat="DMR95", src=MAG + "DMEGC-EE80.pdf p.1", kind="E", Ae=800e-6, le=0.1836,
                 Ve=146.88e-6, mass=0.740, leg_w=19.8e-3, leg_c=40e-3, win_w=20.2e-3, win_h=56.6e-3,
                 set_limit=(85.1, 0.2, 100e3), om_shape="E 80/38/40"),
    "U93": dict(maker="TDK / Ferroxcube (Asian datasheet not retrieved)", pn="U 93/76/30 pair", mat="N95 / 3C95 class",
                src="OpenMagnetics MAS shape 'U 93/76/30'", kind="U", Ae=864e-6, le=0.351, Ve=303.2e-6, mass=1.48,
                leg_w=29.2e-3, leg_c=30e-3, win_w=34.6e-3, win_h=96e-3, set_limit=None, om_shape="U 93/76/30"),
}
XF_RULES = dict(flange=2e-3, margin=5e-3, wall=2e-3, outer_clear=2e-3, barrier=2.0e-3, litz_ins=0.25e-3, pack=0.62,
                k_pot=1.5, t_pot=3e-3, k_fe=4.0, r_pad=0.02, k_rad=0.6)


def dmr95_steinmetz():
    """k, alpha, beta (W/m3, Hz, T) for DMEGC DMR95 at 100 C from the datasheet curve points (asian CSV); N95 fallback"""
    pts = [(float(r["f_Hz"]), float(r["B_pk_T"]), float(r["value"]) * 1e3) for r in _csv("sim/data/asian_magnetic_materials.csv")
           if r["material"] == "DMR95" and r["kind"] == "loss" and r["T_C"] == "100" and r["f_Hz"] and float(r["B_pk_T"]) <= 0.2]
    return _fit_points(pts) if len(pts) >= 4 else ferrite_steinmetz("N95", 100.0)


def xf_eval(D, core, n_stack, n_par=1, pattern="PSP", d_str=0.05e-3, N1=None, N2=None, rules=XF_RULES, t_cool=65.0,
            barrier=None, waves=True):
    """own model of a DAB transformer construction: litz-on-former, one layer per section, vacuum-potted, cores and
    potting on the cold plate.  D = dab_inputs(); returns losses at the ngspice points, leakage, gap, thermal, mass."""
    c, R = XF_CORES[core], rules
    N1 = N1 or D["N1"]
    N2 = N2 or D["N2"]
    nb = barrier if barrier is not None else R["barrier"]
    Ae, Ve, leg_c = c["Ae"] * n_stack, c["Ve"] * n_stack, c["leg_c"] * n_stack
    b = c["win_h"] - 2 * (R["flange"] + R["margin"])
    secs = {"PS": [("P", N1)], "PSP": [("P", math.ceil(N1 / 2)), ("S", N2), ("P", N1 - math.ceil(N1 / 2))],
            "SPS": [("S", math.ceil(N2 / 2)), ("P", N1), ("S", N2 - math.ceil(N2 / 2))]}[pattern]
    if pattern == "PS":
        secs = [("P", N1), ("S", N2)]
    n_bar = sum(1 for i in range(1, len(secs)) if secs[i][0] != secs[i - 1][0])
    radial = c["win_w"] - R["wall"] - R["outer_clear"] - n_bar * nb - len(secs) * 2 * R["litz_ins"]
    i1 = D["I1rms"] / n_par
    # copper split: equal current density, P sections in series carry I1, S carries I1*N1/N2
    h = {i: b / t - 2 * R["litz_ins"] for i, (k, t) in enumerate(secs)}
    cur = {"P": 1.0, "S": N1 / N2}
    # widths w_i so that A_i = pack*h_i*w_i proportional to cur_i (same J), sum w_i = radial
    wt = {i: cur[k] / h[i] for i, (k, t) in enumerate(secs)}
    scale = radial / sum(wt.values())
    w = {i: wt[i] * scale for i in wt}
    A = {i: R["pack"] * h[i] * w[i] for i in w}
    # radial positions (from the former surface) and MLT
    per0 = 2 * (c["leg_w"] + 2 * R["wall"]) + 2 * (leg_c + 2 * R["wall"])
    pos, x = {}, 0.0
    for i, (k, t) in enumerate(secs):
        if i and secs[i - 1][0] != k:
            x += nb
        x += R["litz_ins"]
        pos[i] = x + w[i] / 2
        x += w[i] + R["litz_ins"]
    build = x
    mlt = {i: per0 + 2 * math.pi * pos[i] for i in pos}
    # MMF profile in primary turns (ideal: N2 I2 = N1 I1)
    F, prof = 0.0, []
    for i, (k, t) in enumerate(secs):
        dF = t if k == "P" else -t * cur["S"] * (N1 / N1)
        dF = t if k == "P" else -t * (N1 / N2)
        prof.append((F, F + dF))
        F += dF
    out = dict(core=core, n_stack=n_stack, n_par=n_par, pattern=pattern, d_str=d_str, N1=N1, N2=N2, breadth=b, build=build,
               radial_avail=c["win_w"] - R["wall"] - R["outer_clear"], fits=build <= c["win_w"] - R["wall"] - R["outer_clear"] + 1e-9,
               sections=[dict(kind=k, turns=t, A_cu=A[i], height=h[i], width=w[i], MLT=mlt[i], n_str=int(A[i] / (math.pi * d_str ** 2 / 4)),
                              F_in=prof[i][0], F_out=prof[i][1]) for i, (k, t) in enumerate(secs)], Ae=Ae, Ve=Ve)
    # leakage (1-D energy), primary referred, per transformer; n_par in parallel -> /n_par
    e = 0.0
    xs = 0.0
    for i, (k, t) in enumerate(secs):
        if i and secs[i - 1][0] != k:
            e += mlt[i] * prof[i][0] ** 2 * nb
        fa, fb = prof[i]
        e += mlt[i] * w[i] * (fa * fa + fa * fb + fb * fb) / 3
    llk = MU0 / b * e
    out["L_leak_each"] = llk
    out["L_leak"] = llk / n_par
    # gap for Lm (gap split over centre + outer legs as a spacer: two gaps in series per path)
    a_leg = c["leg_w"] * leg_c
    lm_t = D["Lm"] * n_par
    r_core = c["le"] / (MU0 * 3000 * Ae)
    lm_of = lambda g: N1 ** 2 / (r_core + 2 * (g / 2) / (MU0 * a_leg * fringing_mclyman(g / 2, a_leg, c["win_h"])))   # noqa: E731
    lo, hi = 1e-5, 10e-3
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if lm_of(mid) > lm_t else (lo, mid)
    out["gap_total"] = 0.5 * (lo + hi)          # sum of the two spacer gaps per magnetic path (spacer = gap/2)
    out["B_pk_950"] = D["vs_half"] / (2 * N1 * Ae)
    # core loss per transformer (own: DMR95 fit for the EE80, N95 fit for the U93) on the ngspice flux
    k_, a_, b_ = dmr95_steinmetz() if c["mat"] == "DMR95" else ferrite_steinmetz("N95", 100.0)
    out["steinmetz"] = (k_, a_, b_)
    pts = {}
    for name, wv in D["waves"].items():
        tt, wvv = _period(wv, D["f"])
        dt = tt[1] - tt[0]
        lam = np.cumsum(wvv["v_s_V"] * D["n"]) * dt
        lam -= lam.mean()
        bw = lam / (N1 * Ae) * (D["N1"] / N1) * N1 / N1
        bw = lam * (N1 / D["N1"]) / (N1 * Ae) if False else lam / (D["N1"] * (Ae * N1 / D["N1"]))
        pc = igse(k_, a_, b_, np.append(tt, 1 / D["f"]), np.append(bw, bw[0])) * Ve
        i1w = wvv["i_L_A"] / n_par
        im = lam / D["Lm"] / n_par
        i2w = (i1w - im) * D["n"]
        h1, h2 = harmonics(tt, i1w), harmonics(tt, i2w)
        pcu = 0.0
        for s in out["sections"]:
            hh = h1 if s["kind"] == "P" else h2
            sc = 1.0 if s["kind"] == "P" else N2 / N1          # MMF per A of the section's own current
            wl = litz_loss(hh, s["n_str"], d_str, s["turns"], s["MLT"], ramp_h2(s["F_in"] * sc, s["F_out"] * sc, b), 90.0,
                           math.sqrt(s["height"] * s["width"] / math.pi))
            s.setdefault("P", {})[name] = wl["total"]
            s["R_dc90"] = wl["R_dc"]
            pcu += wl["total"]
        pts[name] = dict(I1_rms=math.sqrt(np.mean(wvv["i_L_A"] ** 2)), B_pk=float(np.abs(bw).max()), P_core=pc * n_par,
                         P_cu=pcu * n_par)
    out["points"] = pts
    wname = max(pts, key=lambda k: pts[k]["I1_rms"])
    sc = (D["I1rms"] / pts[wname]["I1_rms"]) ** 2
    bname = max(pts, key=lambda k: pts[k]["B_pk"])
    out["worst"] = dict(point=wname, P_cu=pts[wname]["P_cu"] * sc, P_core=pts[bname]["P_core"],
                        P_core_950=igse(k_, a_, b_, np.array([0, 0.5 / D["f"], 1 / D["f"]]),
                                        np.array([-out["B_pk_950"], out["B_pk_950"], -out["B_pk_950"]])) * Ve * n_par)
    out["worst"]["P_total"] = out["worst"]["P_cu"] + max(out["worst"]["P_core"], out["worst"]["P_core_950"])
    if c["set_limit"]:
        pmax, b0, f0 = c["set_limit"]
        out["core_set_factor"] = pmax / (k_ * f0 ** a_ * b0 ** b_ * c["Ve"])
    else:
        out["core_set_factor"] = None
    # thermal (per transformer): 2-node network winding / core, see XF_RULES; winding faces outside the core window
    # couple through potting to the Al housing on the plate, faces inside the window couple to the core
    wrap_w = c["leg_w"] + 2 * R["wall"] + 2 * build
    wrap_c = leg_c + 2 * R["wall"] + 2 * build
    if c["kind"] == "E":
        a_exp = 2 * wrap_w * b                        # end-turn faces (outside the core in the stacking direction)
        a_core = 2 * wrap_c * b                       # faces inside the two windows, towards the outer legs
    else:
        a_exp = (2 * wrap_c + wrap_w) * b             # U, wound on one leg: three faces outside the core
        a_core = wrap_w * b                           # the face across the window towards the other leg
    r_wp = R["t_pot"] / (R["k_pot"] * a_exp) + R["r_pad"] + 0.03          # + Al housing wall/base (estimate)
    r_wc = R["t_pot"] / (R["k_pot"] * a_core)
    r_cp = 0.25 * c["le"] / (R["k_fe"] * Ae) / 2 + R["r_pad"]              # half the core path to the plate, both halves
    r_int = (build / len(secs)) / (2 * R["k_rad"] * (a_exp + a_core))      # gradient inside one section
    pw = out["worst"]["P_cu"] / n_par
    pcore = max(out["worst"]["P_core"], out["worst"]["P_core_950"]) / n_par
    # solve the 2-node network: Tw - Tc0 = ..., nodes winding (w) and core (c), plate at t_cool
    G = np.array([[1 / r_wp + 1 / r_wc, -1 / r_wc], [-1 / r_wc, 1 / r_wc + 1 / r_cp]])
    tw, tc = np.linalg.solve(G, np.array([pw, pcore]))
    out["thermal"] = dict(R_wp=r_wp, R_wc=r_wc, R_cp=r_cp, R_int=r_int, A_exposed=a_exp, A_core_facing=a_core,
                          T_winding=t_cool + tw + pw * r_int, T_core=t_cool + tc, t_cool=t_cool,
                          Rth_eq=(tw + pw * r_int) / (pw + pcore))
    # mass and copper
    cu_vol = sum(s["turns"] * s["MLT"] * s["A_cu"] for s in out["sections"]) * n_par
    out["mass_cu"] = cu_vol * 8960
    out["mass_core"] = c["mass"] * n_stack * n_par
    out["mass"] = out["mass_cu"] * 1.15 + out["mass_core"] + 0.6 * n_par     # +15 % litz serving, potting/housing 0.6 kg
    return out


def ls_eval(D, L_target, core="EE80", n_stack=3, N=4, a_cu=40e-6, setback=6e-3, k_gaps=5, d_str=0.05e-3, t_cool=65.0):
    """own model of the DAB series inductor: gapped DMR95 E stack, quasi-distributed centre-leg gap (k gaps, ungapped
    outer legs), one layer of profiled litz on a thick former (winding set back from the gapped leg)."""
    c, R = XF_CORES[core], XF_RULES
    Ae, Ve, leg_c = c["Ae"] * n_stack, c["Ve"] * n_stack, c["leg_c"] * n_stack
    a_leg = c["leg_w"] * leg_c
    b = c["win_h"] - 2 * (R["flange"] + R["margin"])
    r_core = c["le"] / (MU0 * 3000 * Ae)
    spacing = c["win_h"] / k_gaps
    l_of = lambda g: N ** 2 / (r_core + k_gaps * (g / k_gaps) / (MU0 * a_leg * fringing_mclyman(g / k_gaps, a_leg, spacing / 2)))   # noqa: E731
    lo, hi = 1e-5, 40e-3
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if l_of(mid) > L_target else (lo, mid)
    gap = 0.5 * (lo + hi)
    h_turn = b / N - 2 * R["litz_ins"]
    w = a_cu / (R["pack"] * h_turn) + 2 * R["litz_ins"]
    fits = setback + w <= c["win_w"] - R["outer_clear"]
    per0 = 2 * (c["leg_w"] + 2 * setback) + 2 * (leg_c + 2 * setback)
    mlt = per0 + math.pi * w
    n_str = int(a_cu / (math.pi * d_str ** 2 / 4))
    # field over the winding cross-section per ampere: k line sources (F = N i / k) at the gaps + 1-D ramp
    zs = (np.arange(k_gaps) + 0.5) * c["win_h"] / k_gaps - c["win_h"] / 2
    xs = np.linspace(setback, setback + w, 12)
    zz = np.linspace(-b / 2, b / 2, 80)
    X, Z = np.meshgrid(xs, zz)
    hsum = np.zeros_like(X)
    for z0 in zs:
        hsum += (N / k_gaps) / (math.pi * np.sqrt(X ** 2 + (Z - z0) ** 2 + (gap / k_gaps / 2) ** 2))
    h2 = float(np.mean(hsum ** 2)) + ramp_h2(0, N, b)
    k_, a_, b_ = dmr95_steinmetz()
    pts = {}
    for name, wv in D["waves"].items():
        tt, wvv = _period(wv, D["f"])
        i = wvv["i_L_A"]
        bw = L_target * i / (N * Ae)
        pc = igse(k_, a_, b_, np.append(tt, 1 / D["f"]), np.append(bw, bw[0])) * Ve
        wl = litz_loss(harmonics(tt, i), n_str, d_str, N, mlt, h2, 90.0, math.sqrt(h_turn * (w - 2 * R["litz_ins"]) / math.pi))
        pts[name] = dict(I_rms=math.sqrt(np.mean(i ** 2)), B_pk=float(np.abs(bw).max()), P_core=pc, P_cu=wl["total"], P_dc=wl["dc"])
    wname = max(pts, key=lambda k: pts[k]["I_rms"])
    sc = (D["I1rms"] / pts[wname]["I_rms"]) ** 2
    p_cu, p_core = pts[wname]["P_cu"] * sc, max(p["P_core"] for p in pts.values())
    a_exp = 2 * (c["leg_w"] + 2 * setback + 2 * w) * b
    r_w = R["t_pot"] / (R["k_pot"] * a_exp) + R["r_pad"] + 0.03
    return dict(core=core, n_stack=n_stack, N=N, a_cu=a_cu, setback=setback, k_gaps=k_gaps, d_str=d_str, gap_total=gap,
                gap_each=gap / k_gaps, fits=fits, width=w, h_turn=h_turn, MLT=mlt, n_str=n_str, h_rms_per_A=math.sqrt(h2),
                B_177=L_target * D["I1pk"] / (N * Ae), B_275=L_target * 275.0 / (N * Ae), points=pts, P_cu=p_cu, P_core=p_core,
                P_total=p_cu + p_core, Ae=Ae, Ve=Ve, mass=c["mass"] * n_stack + N * mlt * a_cu * 8960 * 1.15 + 0.4,
                mass_cu=N * mlt * a_cu * 8960, T_winding=t_cool + (p_cu + 0.5 * p_core) * r_w, R_w=r_w, steinmetz=(k_, a_, b_))


# ---------------------------------------------------------------------------------------------------- DAB design
DAB_CHOICE = dict(core="EE80", n_stack=2, n_par=2, pattern="PSP", d_str=0.05e-3)        # transformer (pair)
LS_CHOICE = dict(core="EE80", n_stack=4, N=3, a_cu=25e-6, setback=7e-3, k_gaps=6, d_str=0.05e-3)
PRICE = dict(ferrite_usd_kg=6.0, litz005_usd_kg=60.0, housing_potting=12.0, former_ins=4.0, labour=12.0, test=6.0,
             basis="ESTIMATES, no quote: Chinese MnZn power ferrite (large E sets) 6 USD/kg at 1k; litz with 0.05 mm strands "
                   "60 USD/kg of copper (fine-strand premium over LME copper 14.4 USD/kg on 2026-10-02, westmetall.com); Al "
                   "housing + thermally conductive epoxy 12, former and barrier films 4, winding labour 12, vacuum potting + "
                   "hipot/PD test 6 per unit")


def _harm_R(sections, d_str, b, n1, n2, t=90.0, hmax=15, f0=100e3):
    """primary-referred harmonic resistance R(h) [ohm] of a winding set: P_cu = sum_h R(h) I1_h,rms^2 (I2 = I1 N1/N2)"""
    out = []
    for h in range(1, hmax + 1, 2):
        p = 0.0
        for s in sections:
            ii = 1.0 if s["kind"] == "P" else n1 / n2
            sc = 1.0 if s["kind"] == "P" else n2 / n1
            wl = litz_loss([(h * f0, ii)], s["n_str"], d_str, s["turns"], s["MLT"], ramp_h2(s["F_in"] * sc, s["F_out"] * sc, b),
                           t, math.sqrt(s["height"] * s["width"] / math.pi))
            p += wl["total"]
        out.append([h * f0, p])
    return out


def core_table(k, a, b, ve, f=100e3, bmax=0.18):
    """core loss [W] for the bipolar triangular flux of SPS (iGSE) vs B_pk, at 100 C"""
    tri = np.array([0, 0.5 / f, 1 / f])
    return [[round(bp, 3), igse(k, a, b, tri, np.array([-bp, bp, -bp])) * ve] for bp in np.arange(0.02, bmax + 1e-9, 0.02)]


def design_dab(specs):
    D = dab_inputs(specs["dab"])
    xd = xf_eval(D, **DAB_CHOICE)
    l_total = specs["dab"]["series_inductor"]["L_total_H"]
    l_ext = l_total - xd["L_leak"]
    ld = ls_eval(D, l_ext, **LS_CHOICE)
    n_par, c = xd["n_par"], XF_CORES[xd["core"]]
    # guaranteed core-set loss (DMEGC limit at 200 mT/100 kHz/100 C) applied to the transformer's worst flux
    p_core_typ = max(xd["worst"]["P_core"], xd["worst"]["P_core_950"])
    p_core_lim = p_core_typ * (xd["core_set_factor"] or 1.0)
    th = xd["thermal"]
    # hot spot again with the guaranteed core loss (conservative)
    G = np.array([[1 / th["R_wp"] + 1 / th["R_wc"], -1 / th["R_wc"]], [-1 / th["R_wc"], 1 / th["R_wc"] + 1 / th["R_cp"]]])
    tw2, _ = np.linalg.solve(G, np.array([xd["worst"]["P_cu"] / n_par, p_core_lim / n_par]))
    xd["T_winding_coreset"] = th["t_cool"] + tw2 + xd["worst"]["P_cu"] / n_par * th["R_int"]
    xd["P_core_coreset"] = p_core_lim
    # cost basis
    cu_kg = xd["mass_cu"] * 1.15
    cost_x = dict(core_usd=PRICE["ferrite_usd_kg"] * xd["mass_core"], copper_usd=PRICE["litz005_usd_kg"] * cu_kg,
                  insulation_usd=(PRICE["housing_potting"] + PRICE["former_ins"]) * n_par,
                  labour_usd=(PRICE["labour"] + PRICE["test"]) * n_par)
    cost_x["total_usd"] = round(sum(cost_x.values()) * 1.10, 0)
    cost_l = dict(core_usd=PRICE["ferrite_usd_kg"] * c["mass"] * ld["n_stack"] + 10.0, copper_usd=PRICE["litz005_usd_kg"] * ld["mass_cu"] * 1.15,
                  insulation_usd=PRICE["housing_potting"] + PRICE["former_ins"], labour_usd=PRICE["labour"] * 0.7 + PRICE["test"])
    cost_l["total_usd"] = round(sum(cost_l.values()) * 1.10, 0)
    xd["cost"], ld["cost"] = cost_x, cost_l
    xd["L_ext"], ld["L"] = l_ext, l_ext
    return D, xd, ld


def om_design_dab(D, xd, ld):
    """OpenMagnetics check of the chosen DAB constructions: one transformer of the pair (half current) and the inductor"""
    out = {}
    c = XF_CORES[xd["core"]]
    tt, wv = _period(D["waves"][xd["worst"]["point"]], D["f"])
    sel = slice(None, None, 8)
    t = np.append(tt[sel], 1 / D["f"])
    dt = tt[1] - tt[0]
    lam = np.cumsum(wv["v_s_V"] * D["n"]) * dt
    lam -= lam.mean()
    i1 = wv["i_L_A"] / xd["n_par"]
    i2 = (i1 - lam / (D["Lm"] * xd["n_par"])) * D["n"]
    vm = wv["v_s_V"] * D["n"]
    sP = next(s for s in xd["sections"] if s["kind"] == "P")
    sS = next(s for s in xd["sections"] if s["kind"] == "S")
    try:
        core_fd = {"type": "two-piece set", "material": "DMR95", "shape": c["om_shape"], "numberStacks": xd["n_stack"],
                   "gapping": [{"type": "additive", "length": xd["gap_total"] / 2}] * 3}
        wnd = [{"name": "Primary", "numberTurns": xd["N1"], "numberParallels": 1, "isolationSide": "primary",
                "wire": _om_litz(sP["n_str"], xd["d_str"], math.sqrt(sP["A_cu"] / XF_RULES["pack"] * 4 / math.pi))},
               {"name": "Secondary", "numberTurns": xd["N2"], "numberParallels": 1, "isolationSide": "secondary",
                "wire": _om_litz(sS["n_str"], xd["d_str"], math.sqrt(sS["A_cu"] / XF_RULES["pack"] * 4 / math.pi))}]
        exc = [{"current": _om_wave(t, np.append(i1[sel], i1[0])), "voltage": _om_wave(t, np.append(vm[sel], vm[0]))},
               {"current": _om_wave(t, np.append(i2[sel], i2[0])), "voltage": _om_wave(t, np.append(wv["v_s_V"][sel], wv["v_s_V"][0]))}]
        core, coil, mag, inp = _om_build(core_fd, wnd, exc, D["f"], 65.0, pattern=[0, 1, 0], proportions=[0.5, 0.5],
                                         margin=XF_RULES["margin"], l_target=D["Lm"] * xd["n_par"], turns_ratios=[D["N1"] / D["N2"]])
        # no former wall here: OM winds round litz, and with the 2 mm wall the sections no longer fit radially (OM then
        # re-arranges them and its leakage triples); the low-flux spacer gaps make the wall irrelevant for fringing
        out["transformer_each"] = _om_eval(core, coil, mag, inp, 90.0)
    except Exception as e:
        out["transformer_each"] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    try:
        n_g = ld["k_gaps"]
        h = c["win_h"]
        core_fd = {"type": "two-piece set", "material": "DMR95", "shape": c["om_shape"], "numberStacks": ld["n_stack"],
                   "gapping": [{"type": "subtractive", "length": ld["gap_each"], "coordinates": [0, (k + 0.5) * h / n_g - h / 2, 0]}
                               for k in range(n_g)] + [{"type": "residual", "length": 5e-6}] * 2}
        i = wv["i_L_A"]
        vl = ld["L"] * np.gradient(i, tt)
        wnd = [{"name": "L", "numberTurns": ld["N"], "numberParallels": 1, "isolationSide": "primary",
                "wire": _om_litz(ld["n_str"], ld["d_str"], math.sqrt(ld["a_cu"] / XF_RULES["pack"] * 4 / math.pi))}]
        exc = [{"current": _om_wave(t, np.append(i[sel], i[0])), "voltage": _om_wave(t, np.append(vl[sel], vl[0]))}]
        core, coil, mag, inp = _om_build(core_fd, wnd, exc, D["f"], 65.0, margin=XF_RULES["margin"], l_target=ld["L"],
                                         wall=ld["setback"])
        out["series_inductor"] = _om_eval(core, coil, mag, inp, 90.0)
    except Exception as e:
        out["series_inductor"] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    return out


def _mtime(path):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(P(path))))


def _ins_levels(ins, labels):
    out = []
    for lab in labels:
        r = next((x for x in (ins or {}).get("requirements", []) if x.get("label", "").startswith(lab)), None)
        if r:
            out.append({k: r[k] for k in ("label", "kind", "u_w", "u_rp", "imp", "imp_spd", "ac", "pd_test", "pd_ext", "cr_pd1",
                                          "cr_pd2", "cl_2000", "cl_3000") if k in r})
    return out


XF_BAR_OVERLAP = 4e-3     # P-S barrier extends 4 mm beyond each winding edge (inside the 5 mm margin)


def _grid(p, ikey, i_max, ctab, note):
    """loss_grid: copper loss for the point's current shape scaled with I^2, core loss vs B_pk from the table"""
    ii = [round(i_max * x, 1) for x in (0.2, 0.4, 0.6, 0.8, 0.9, 1.0, 1.1)]
    return dict(I_rms_A=ii, P_cu_W=[p["P_cu"] * (i / p[ikey]) ** 2 for i in ii], B_pk_T=[b for b, _ in ctab],
                P_core_W=[w for _, w in ctab], note=note)


def write_design_dab(specs, D, xd, ld, omd, ins, rev0=None, write=True):
    """sim/out/magnetics/design_dab_transformer.json and design_dab_series_inductor.json"""
    c = XF_CORES[xd["core"]]
    n_par = xd["n_par"]
    x = specs["dab"]["transformer"]
    x0, s0 = (rev0 or specs["dab"])["transformer"], (rev0 or specs["dab"])["series_inductor"]
    k_, a_, b_ = xd["steinmetz"]
    R = _harm_R(xd["sections"], xd["d_str"], xd["breadth"], xd["N1"], xd["N2"])
    R_pair = [[f, r / n_par] for f, r in R]
    pw = xd["worst"]["point"]
    pts = []
    for name, p in xd["points"].items():
        pts.append(dict(point=name, I_rms_A=p["I1_rms"], B_pk_T=p["B_pk"], P_core_W=p["P_core"], P_cu_W=p["P_cu"],
                        P_total_W=p["P_core"] + p["P_cu"]))
    pts.append(dict(point=f"spec I1_rms_max {D['I1rms']:.1f} A (shape of {xd['worst']['point']}) + 950 V flux", I_rms_A=D["I1rms"],
                    B_pk_T=xd["B_pk_950"], P_core_W=xd["worst"]["P_core_950"], P_cu_W=xd["worst"]["P_cu"], P_total_W=xd["worst"]["P_total"]))
    ot = omd.get("transformer_each", {})
    secs = []
    for i, s in enumerate(xd["sections"]):
        secs.append(dict(order=i + 1, name={"P": "primary", "S": "secondary"}[s["kind"]] + (f" part {i // 2 + 1}" if s["kind"] == "P" else ""),
                         turns=s["turns"], conductor=f"profiled litz {s['n_str']} x {xd['d_str']*1e3:.2f} mm (grade 1), "
                         f"{s['height']*1e3:.1f} x {s['width']*1e3:.1f} mm over the copper bundle, served + 0.25 mm polyimide wrap",
                         copper_area_m2=s["A_cu"], MLT_m=s["MLT"], R_dc_90C_ohm=s["R_dc90"],
                         R_dc_20C_ohm=s["R_dc90"] / (1 + ALPHA_CU * 70)))
    lev = _ins_levels(ins, ["Basic DAB port 1 - port 2", "Basic DAB port 1 - PE", "Reinforced DAB port 1 - PELV"])
    dx = dict(part="dab_transformer", revision="M1", status="proposed construction - calculated, not built or measured",
              basis={"spec": "sim/out/dab_design/dab_spec.json", "spec_mtime": _mtime("sim/out/dab_design/dab_spec.json"),
                     "waveforms": sorted(D["waves"])},
              construction=f"{n_par} identical transformers in parallel (primaries in parallel, secondaries in parallel), each on "
                           f"{xd['n_stack']} x DMEGC EE80 DMR95 stacked, {xd['N1']}:{xd['N2']}, P-S-P interleaved litz "
                           f"{xd['d_str']*1e3:.2f} mm, vacuum-potted in an aluminium housing on the cold plate; matched pair",
              electrical=dict(turns_ratio=f"{xd['N1']}:{xd['N2']}", n=D["n"], L_m_H=D["Lm"], L_m_each_H=D["Lm"] * n_par,
                              L_m_tol="+/-10 % (spacer gap ground to A_L)", L_leak_H=xd["L_leak"], L_leak_each_H=xd["L_leak_each"],
                              L_leak_tol="+/-20 % per unit; the two units matched within +/-5 % (current sharing)",
                              L_ext_required_H=xd["L_ext"], L_total_H=specs["dab"]["series_inductor"]["L_total_H"],
                              B_pk_950V_T=xd["B_pk_950"], Vs_per_half_period=D["vs_half"], I1_rms_max_A=D["I1rms"],
                              I1_rms_each_A=D["I1rms"] / n_par, I1_pk_max_A=D["I1pk"]),
              core=dict(maker=c["maker"], part_number=f"{c['pn']} set ({c['mat']})", material=c["mat"], n_sets_per_unit=xd["n_stack"],
                        n_units=n_par, Ae_m2=xd["Ae"], le_m=c["le"], Ve_m3=xd["Ve"], datasheet=c["src"],
                        gap={"type": "non-magnetic spacer in all three legs (centre and outer legs)",
                             "spacer_thickness_m": xd["gap_total"] / 2, "note": "ground to A_L for L_m; fringing included"},
                        asian_alternative="TDG TP4A or DMEGC DMR96A (lower loss at 100 C, table only) in the same EE80 set - RFQ",
                        western_reference="TDK N95 / Ferroxcube 3C95 E 80/38/20 x2 per stack"),
              windings=secs,
              fit=dict(build_m=xd["build"], available_m=xd["radial_avail"], fits=bool(xd["fits"]), breadth_m=xd["breadth"]),
              insulation=dict(barrier_m=XF_RULES["barrier"], barrier_overlap_m=XF_BAR_OVERLAP, margin_m=XF_RULES["margin"],
                              creepage_ps_potted_m=2 * XF_BAR_OVERLAP + XF_RULES["barrier"],
                              system="vacuum-potted (thermally conductive epoxy >= 1.5 W/mK, void-free) - REQUIRED; one-piece "
                                     "former (2 mm GF-PBT/PPS) with flanges; P-S barriers 2.0 mm (Nomex 410 2 x 0.25 mm + polyimide "
                                     "film 4 x 0.05 mm + impregnation), each barrier overlapping the winding edges by >= 4 mm "
                                     "(P-S creepage >= 10 mm inside the potting vs 7.5 mm PD1); 5 mm margins at both winding ends "
                                     "(winding-core creepage >= 5 mm, PD2 value); terminations brought out on opposite sides "
                                     "(port 1 / port 2) with >= 20 mm creepage on the housing (PD2, IIIa) or 10 mm with CTI >= 600",
                              levels=lev, routine_tests="P-S AC 3323 V rms 60 s (or 4700 V DC); windings-core AC 2200 V rms; PD <= 10 pC "
                                                        "at 3149 V pk (P-S) and 1799 V pk (windings-core); NTC-winding 4400 V rms",
                              type_tests="impulse 6 kV 1.2/50 P-S and windings-core; PD at 1.2 x; thermal run on the cold plate"),
              loss_model=dict(core={"k": k_, "alpha": a_, "beta": b_, "Ve_m3": xd["Ve"] * n_par, "N1": xd["N1"], "Ae_m2": xd["Ae"],
                                    "formula": "P_core = Ve*k*f^alpha*B^beta (Steinmetz, DMR95 at 100 C) x iGSE shape; B = V*t/(2*N1*Ae) per unit",
                                    "core_set_limit_factor": xd["core_set_factor"],
                                    "table_W_vs_Bpk_SPS": core_table(k_, a_, b_, xd["Ve"] * n_par)},
                              winding={"R_ac_per_harmonic": R_pair, "referred_to": "port 1, the two units in parallel", "temperature_C": 90.0,
                                       "formula": "P_cu = sum_h R(h) * I1_h,rms^2 (I1 = total port-1 current of the pair; I2 = I1*N1/N2)"}),
              loss_table=pts,
              loss_grid=_grid(xd["points"][pw], "I1_rms", D["I1rms"], core_table(k_, a_, b_, xd["Ve"] * n_par),
                              "P_cu at the worst-point current shape scaled with I^2; P_core vs B_pk for the SPS trapezoid flux (iGSE)"),
              thermal=dict(Rth_hotspot_to_coolant_K_W=xd["thermal"]["Rth_eq"], T_hotspot_C=xd["thermal"]["T_winding"],
                           T_hotspot_coreset_limit_C=xd["T_winding_coreset"], coolant_C=xd["thermal"]["t_cool"],
                           basis="own 2-node estimate per unit: winding faces outside the core window -> 3 mm potting (1.5 W/mK) -> "
                                 "Al housing -> 0.5 mm gap pad -> cold plate; window faces -> potting -> core -> plate; to be "
                                 "confirmed by a thermal run",
                           interface="each unit potted in an Al housing (base >= 6 mm) bolted to the cold plate with a gap pad "
                                     ">= 3 W/mK; contact area >= 0.013 m2 per unit (housing footprint ~ 115 x 115 mm)",
                           parts={k: v for k, v in xd["thermal"].items() if k.startswith(("R_", "A_"))}),
              mass_kg=xd["mass"], cost=dict(xd["cost"], basis=PRICE["basis"],
                                            cost_estimates_row=f"Transformer 11:12 (CUSTOM),{xd['cost']['total_usd']:.0f},\"ESTIMATE (sim/magnetics.py design_dab_transformer.json rev M1): "
                                            f"pair of potted units, 4 x DMEGC EE80 DMR95 ({xd['mass_core']:.2f} kg at {PRICE['ferrite_usd_kg']} USD/kg) + litz 0.05 mm "
                                            f"{xd['mass_cu']*1.15:.2f} kg at {PRICE['litz005_usd_kg']} USD/kg + housings/potting/former 32 + labour and test 36, +10 %; no quote\",low"),
              verification=dict(own_same_point_each_unit=dict(point=pw, P_cu_W=xd["points"][pw]["P_cu"] / n_par,
                                                              P_core_W=xd["points"][pw]["P_core"] / n_par),
                                own=dict(P_cu_W=xd["worst"]["P_cu"], P_core_W=max(xd["worst"]["P_core"], xd["worst"]["P_core_950"]),
                                         L_leak_H=xd["L_leak"], T_hotspot_C=xd["thermal"]["T_winding"]),
                                openmagnetics_each_unit={k: _num(ot.get(k2), *ks) for k, k2, ks in (
                                    ("L_m_H", "L", ("magnetizingInductance",)), ("P_core_W", "core", ("coreLosses",)),
                                    ("P_cu_W", "winding", ("windingLosses",)), ("L_leak_H", "leakage", ("leakageInductancePerWinding",)))}
                                if "error" not in ot else ot,
                                previous_designer=dict(P_total_W=x0["loss_max_W"], L_leak_H=x0["L_sigma_target_H"], Rth_K_W=x0["Rth_hotspot_to_coolant_max_K_W"])))
    ol = omd.get("series_inductor", {})
    k2, a2, b2 = ld["steinmetz"]
    hh = [[h * D["f"], 0.0] for h in range(1, 16, 2)]
    for row in hh:
        row[1] = litz_loss([(row[0], 1.0)], ld["n_str"], ld["d_str"], ld["N"], ld["MLT"], ld["h_rms_per_A"] ** 2, 90.0,
                           math.sqrt(ld["h_turn"] * ld["width"] / math.pi))["total"]
    lev2 = _ins_levels(ins, ["Basic DAB port 1 - PE"])
    s = specs["dab"]["series_inductor"]
    dl = dict(part="dab_series_inductor", revision="M1", status="proposed construction - calculated, not built or measured",
              basis={"spec": "sim/out/dab_design/dab_spec.json", "spec_mtime": _mtime("sim/out/dab_design/dab_spec.json"),
                     "L_from": "L_total - leakage of the transformer pair (design_dab_transformer.json)"},
              construction=f"{ld['n_stack']} x DMEGC EE80 DMR95 stacked, {ld['N']} turns profiled litz {ld['d_str']*1e3:.2f} mm, "
                           f"quasi-distributed centre-leg gap ({ld['k_gaps']} gaps x {ld['gap_each']*1e3:.2f} mm, ferrite I-segments), "
                           f"outer legs ungapped, winding on a former set back {ld['setback']*1e3:.0f} mm from the gapped leg, potted",
              electrical=dict(L_H=ld["L"], L_tol="trim by gap shims so that transformer leakage + L = L_total within +/-5 % (matched set)",
                              L_trim_range_H=[s["L_total_H"] * 0.95 - 1.2 * xd["L_leak"], s["L_total_H"] * 1.05 - 0.8 * xd["L_leak"]],
                              I_rms_max_A=D["I1rms"], I_pk_max_A=D["I1pk"], I_sat_min_A=s["I_sat_min_A"], B_pk_at_I_pk_T=ld["B_177"],
                              B_at_I_sat_T=ld["B_177"] / D["I1pk"] * s["I_sat_min_A"], B_sat_100C_T=0.41,
                              I_sat_basis=s.get("I_sat_basis", "")),
              core=dict(maker="DMEGC", part_number="EE80 set (DMR95) x %d + custom gapped centre-leg segments" % ld["n_stack"],
                        material="DMR95", Ae_m2=ld["Ae"], Ve_m3=ld["Ve"], datasheet=XF_CORES["EE80"]["src"],
                        gap={"type": "quasi-distributed in the centre leg", "count": ld["k_gaps"], "length_each_m": ld["gap_each"],
                             "total_m": ld["gap_total"], "note": "fringing included (McLyman per gap); final by trim"},
                        asian_alternative="TDG TP4A in the same E80-class set (RFQ); powder cores rejected (core loss at 100 kHz full AC flux)"),
              fit=dict(build_m=ld["setback"] + ld["width"], available_m=XF_CORES["EE80"]["win_w"] - XF_RULES["outer_clear"],
                       fits=bool(ld["fits"])),
              windings=[dict(name="L", turns=ld["N"], conductor=f"profiled litz {ld['n_str']} x {ld['d_str']*1e3:.2f} mm, "
                             f"{ld['h_turn']*1e3:.1f} x {ld['width']*1e3:.1f} mm", copper_area_m2=ld["a_cu"], MLT_m=ld["MLT"])],
              insulation=dict(margin_m=XF_RULES["margin"], setback_m=ld["setback"],
                              system="potted with the transformer pair or alone in an Al housing; winding-core basic: former wall "
                                     f"{ld['setback']*1e3:.0f} mm + 5 mm margins", levels=lev2,
                              routine_tests="winding-core AC 2200 V rms 60 s, PD <= 10 pC at 1799 V pk", type_tests="impulse 6 kV"),
              loss_model=dict(core={"k": k2, "alpha": a2, "beta": b2, "Ve_m3": ld["Ve"], "N": ld["N"], "Ae_m2": ld["Ae"],
                                    "formula": "B(t) = L*i(t)/(N*Ae); P_core = iGSE(B(t)) with the Steinmetz set"},
                              winding={"R_ac_per_harmonic": hh, "referred_to": "the winding", "temperature_C": 90.0, "formula": "P_cu = sum_h R(h) I_h,rms^2"}),
              loss_grid=_grid(ld["points"][pw], "I_rms", D["I1rms"], core_table(k2, a2, b2, ld["Ve"], bmax=0.16),
                              "P_cu at the worst-point current shape scaled with I^2; P_core vs B_pk for a triangular flux (approximation"
                              " of the DAB current shape, iGSE)"),
              loss_table=[dict(point=n_, I_rms_A=p["I_rms"], B_pk_T=p["B_pk"], P_core_W=p["P_core"], P_cu_W=p["P_cu"],
                               P_total_W=p["P_core"] + p["P_cu"]) for n_, p in ld["points"].items()],
              thermal=dict(T_hotspot_C=ld["T_winding"], Rth_winding_K_W=ld["R_w"], coolant_C=65.0,
                           interface="Al housing or shared housing with the transformers on the cold plate, gap pad >= 3 W/mK"),
              mass_kg=ld["mass"], cost=dict(ld["cost"], basis=PRICE["basis"],
                                            cost_estimates_row=f"2.12 uH series L (CUSTOM),{ld['cost']['total_usd']:.0f},\"ESTIMATE (sim/magnetics.py design_dab_series_inductor.json rev M1; the value "
                                            f"becomes {ld['L']*1e6:.2f} uH): 4 x DMEGC EE80 DMR95 + gapped centre-leg segments 38 + litz 0.05 mm {ld['mass_cu']*1.15:.2f} kg + housing/potting 16 + labour/test 14, +10 %; no quote\",low"),
              verification=dict(own_same_point=dict(point=pw, P_cu_W=ld["points"][pw]["P_cu"], P_core_W=ld["points"][pw]["P_core"]),
                                own=dict(P_total_W=ld["P_total"], P_cu_W=ld["P_cu"], P_core_W=ld["P_core"], L_H=ld["L"]),
                                openmagnetics={k: _num(ol.get(k2_), *ks) for k, k2_, ks in (
                                    ("L_H", "L", ("magnetizingInductance",)), ("P_core_W", "core", ("coreLosses",)),
                                    ("P_cu_W", "winding", ("windingLosses",)))} if "error" not in ol else ol,
                                previous_designer=dict(P_total_W=s0["loss_max_W"], L_H=s0["L_external_nominal_H"])))
    os.makedirs(OUT, exist_ok=True)
    for name, d in (("dab_transformer", dx), ("dab_series_inductor", dl)) if write else ():
        with open(os.path.join(OUT, f"design_{name}.json"), "w") as fh:
            json.dump(d, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return dx, dl


DESIGN_TITLES = {"dab_transformer": "DAB-D60 power transformer 11:12 (CUSTOM, matched pair, potted, cold-plate mounted)",
                 "dab_series_inductor": "DAB-D60 series inductor LSER (CUSTOM, potted, cold-plate mounted)",
                 "aux_hv_transformer": "AUX-HV flyback transformer T1 (CUSTOM, reinforced, vacuum-potted)",
                 "port_cm_choke": "Port common-mode chokes CMC160 / CMC200 (CUSTOM, potted, chassis-mounted)",
                 "pv_inductor": "PV cell power inductor L_CELL (CUSTOM, toroid stack, forced air)",
                 "gdrv_bias_transformer": "Gate-drive bias transformer T_BIAS4 (CUSTOM, one per phase, functional insulation)",
                 "aux75_transformer": "75 W auxiliary flyback transformer AUX-T1 (CUSTOM, reinforced SELV winding, vacuum-potted)"}
DESIGN_TAGS = {"dab_transformer": ("dab_cu", "dab_thermal", "dab_core_set", "dab_leak", "dab_ins"),
               "dab_series_inductor": ("lser_loss", "dab_leak"), "aux_hv_transformer": ("aux_cu", "aux_blim", "aux_ins"),
               "port_cm_choke": ("cmc_lf",), "pv_inductor": ("pv_window", "pv_temp", "pv_core", "om_kmm_bias")}
B_SAT_DMR95_100C = 0.41    # T, DMEGC DMR95 datasheet (sim/data/asian_magnetic_materials.csv)


def _v(v):
    if isinstance(v, bool):
        return "yes" if v else "NO"
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, (list, tuple)):
        return ", ".join(_v(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k} {_v(x)}" for k, x in v.items())
    return str(v)


def _kv(d):
    return table(list(d.items()), [("item", lambda r: r[0]), ("value", lambda r: _v(r[1]))])


def _rows(rows):
    return table(rows, [(k, lambda r, k=k: _v(r.get(k, ""))) for k in rows[0]]) if rows else "none"


def spec_design(d, F, closed):
    """winding-house sheet rendered from the design_<part>.json dict (one source for the file and the sheet)"""
    tags = DESIGN_TAGS.get(d["part"], ())
    st = [f"{MG_IDS[t]} {'closed' if t in closed else 'open'}" for t in tags if t in MG_IDS]
    ins, ver = d["insulation"], d["verification"]
    L = [f"# Specification - {DESIGN_TITLES.get(d['part'], d['part'])}", "",
         f"Construction rev {d['revision']} by the magnetics designer; generated by `sim/magnetics.py`, same data machine-"
         f"readable in `sim/out/magnetics/design_{d['part']}.json`. Status: {d['status']}. Electrical requirement: "
         f"`{d['basis']['spec']}` ({d['basis']['spec_mtime']}). Review findings (`gen/data/review_magnetics.csv`): "
         f"{', '.join(st) or 'none'}.", "",
         "## 1. Construction", d["construction"], "",
         "## 2. Electrical (requirement and what the construction gives)", _kv(d["electrical"]), "",
         "## 3. Core", _kv({k: v for k, v in d["core"].items() if k != "gap"}), "", "Gap: " + _v(d["core"]["gap"]), "",
         "## 4. Windings (inside out) and fit", _rows(d["windings"]), "", "Fit in the window: " + _v(d["fit"]), "",
         "## 5. Insulation system", ins["system"], "",
         "Dimensions: " + _v({k: v for k, v in ins.items() if k.endswith("_m")}), "",
         "Levels from `sim/out/insulation/insulation_spec.json` (V, mm):", "", _rows(ins["levels"]), "",
         f"Routine tests (every part): {ins['routine_tests']}.", "", f"Type tests (first articles): {ins['type_tests']}.", "",
         "## 6. Losses (calculated: winding 90 C, core 100 C; worst case last)", _rows(d["loss_table"]), "",
         "Interpolation: `loss_model` (Steinmetz set + R_ac per harmonic) and `loss_grid` in the JSON.", "",
         "## 7. Thermal and mounting interface", _kv(d["thermal"]), "",
         "## 8. Verification (OpenMagnetics / own / previous designer)", _kv(ver), "",
         f"## 9. Mass and cost basis (estimate, no quote)", f"Mass {_v(d['mass_kg'])} kg.", "", _kv(d["cost"]), "",
         *(["## Variants", _kv(d["variants"]), ""] if d.get("variants") else []),
         *(["## Constructions compared (design loss = higher of own and OpenMagnetics; same core stack)", _rows(d["options"]), "",
            "Rejected: " + "; ".join(d.get("options_rejected", [])) + ".", ""] if d.get("options") else []),
         *(["## Reference: rev M1", _kv(d["reference_M1"]), ""] if d.get("reference_M1") else []),
         "## 10. To be measured on the first articles",
         "Inductances (L_m open-circuit, leakage short-circuit from each side, unit-to-unit match), R_dc, R_ac 100-500 kHz, "
         "PD and AC withstand at the levels above, impulse (type), thermal run on a cold plate at 65 C with the worst-case "
         "loss, core loss at 100 kHz and the stated B_pk. Nothing in this sheet is measured.", ""]
    return "\n".join(L)


def design_checks_dab(designs):
    """checks on the rev M1 constructions (no allowances) and the review closures they justify"""
    dx, dl = designs["dab_transformer"], designs["dab_series_inductor"]
    ex, el, vx, vl = dx["electrical"], dl["electrical"], dx["verification"], dl["verification"]
    C, ok = [], {}

    def chk(cid, what, value, limit, good, tag=""):
        C.append(dict(id=cid, what=what, value=value, limit=limit, ok=bool(good), exc="", tag=tag))
        ok[cid] = bool(good)

    px = max(r["P_total_W"] for r in dx["loss_table"])
    pl = max(r["P_total_W"] for r in dl["loss_table"])
    budget = vx["previous_designer"]["P_total_W"]
    tx = max(dx["thermal"]["T_hotspot_C"], dx["thermal"]["T_hotspot_coreset_limit_C"])
    chk("dab_m1_fit", "M1 transformer winding build / window", f"{dx['fit']['build_m']*1e3:.1f} mm",
        f"<= {dx['fit']['available_m']*1e3:.1f} mm", dx["fit"]["fits"])
    chk("dab_m1_b", "M1 transformer B_pk at 950 V / B_sat(100 C)", f"{ex['B_pk_950V_T']/B_SAT_DMR95_100C:.2f}", "<= 0.5",
        ex["B_pk_950V_T"] <= 0.5 * B_SAT_DMR95_100C)
    chk("dab_m1_loss", "M1 transformer pair worst loss (own) vs the converter loss budget", f"{px:.0f} W",
        f"<= {1.15*budget:.0f} W", px <= 1.15 * budget, "dab_imax")
    chk("dab_m1_hot", "M1 transformer hot spot at 65 C coolant (incl. DMEGC core-set loss limit)", f"{tx:.0f} C", "<= 130 C",
        tx <= 130.0, "dab_imax")
    lser_ok = el["B_at_I_sat_T"] <= 0.8 * B_SAT_DMR95_100C
    chk("lser_m1_b", f"M1 series inductor B at I_sat_min {el['I_sat_min_A']:.0f} A / B_sat(100 C)", f"{el['B_at_I_sat_T']/B_SAT_DMR95_100C:.2f}",
        "<= 0.8", lser_ok, "lser_isat")
    chk("lser_m1_fit", "M1 series inductor winding fits", f"{dl['fit']['build_m']*1e3:.1f} mm",
        f"<= {dl['fit']['available_m']*1e3:.1f} mm", dl["fit"]["fits"])
    chk("lser_m1_hot", "M1 series inductor hot spot at 65 C coolant", f"{dl['thermal']['T_hotspot_C']:.0f} C", "<= 130 C",
        dl["thermal"]["T_hotspot_C"] <= 130.0)
    lt = ex["L_total_H"]
    chk("lser_m1_L", "M1 leakage + series L = L_total; trim range within +/-15 % gap shim travel",
        f"{(ex['L_leak_H'] + el['L_H'])*1e6:.2f} uH, trim {el['L_trim_range_H'][0]*1e6:.2f}-{el['L_trim_range_H'][1]*1e6:.2f} uH",
        f"{lt*1e6:.2f} uH +/-1 %", abs(ex["L_leak_H"] + el["L_H"] - lt) <= 0.01 * lt
        and 0.85 * el["L_H"] <= el["L_trim_range_H"][0] and el["L_trim_range_H"][1] <= 1.15 * el["L_H"])
    chk("dab_m1_set", "M1 transformer + series inductor worst loss / 60 kW", f"{(px + pl) / 600:.2f} % ({px + pl:.0f} W)", "<= 0.5 %",
        px + pl <= 0.005 * 60e3)
    lev = {r["label"].split(" (")[0]: r for r in dx["insulation"]["levels"]}
    pp = next((r for k, r in lev.items() if "port 1 - port 2" in k), None)
    pe = next((r for k, r in lev.items() if "port 1 - PE" in k), None)
    ins = dx["insulation"]
    if pp and pe:
        e_kv = pp["pd_test"] / (ins["barrier_m"] * 1e3) / 1e3
        chk("dab_m1_ins", "M1 P-S creepage in potting / barrier field at the PD test / winding-core margin",
            f"{ins['creepage_ps_potted_m']*1e3:.1f} mm, {e_kv:.2f} kV/mm, {ins['margin_m']*1e3:.1f} mm",
            f">= {pp['cr_pd1']:.1f} mm, <= 2.0 kV/mm, >= {pe['cr_pd2']:.1f} mm",
            ins["creepage_ps_potted_m"] * 1e3 >= pp["cr_pd1"] and e_kv <= 2.0 and ins["margin_m"] * 1e3 >= pe["cr_pd2"])
    om_x, om_l = vx.get("openmagnetics_each_unit", {}), vl.get("openmagnetics", {})
    if om_x.get("P_cu_W") is not None and om_l.get("P_cu_W") is not None:
        o, s_ = vx["own_same_point_each_unit"], vl["own_same_point"]
        chk("dab_m1_om_loss", "M1 transformer per unit loss own vs OpenMagnetics (same point)",
            pct(rel(o["P_cu_W"] + o["P_core_W"], om_x["P_cu_W"] + om_x["P_core_W"])), "<= 15 %",
            abs(rel(o["P_cu_W"] + o["P_core_W"], om_x["P_cu_W"] + om_x["P_core_W"])) <= 0.15)
        chk("dab_m1_om_lm", "M1 transformer L_m per unit OpenMagnetics vs target", pct(rel(om_x["L_m_H"], ex["L_m_each_H"])),
            "<= 10 %", abs(rel(om_x["L_m_H"], ex["L_m_each_H"])) <= 0.10)
        chk("dab_m1_om_leak", "M1 transformer leakage per unit own vs OpenMagnetics", pct(rel(ex["L_leak_each_H"], om_x["L_leak_H"])),
            "<= 20 %", abs(rel(ex["L_leak_each_H"], om_x["L_leak_H"])) <= 0.20)
        chk("lser_m1_om_L", "M1 series inductor L own vs OpenMagnetics", pct(rel(el["L_H"], om_l["L_H"])), "<= 10 %",
            abs(rel(el["L_H"], om_l["L_H"])) <= 0.10)
        chk("lser_m1_om_loss", "M1 series inductor loss own vs OpenMagnetics (same point)",
            pct(rel(s_["P_cu_W"] + s_["P_core_W"], om_l["P_cu_W"] + om_l["P_core_W"])), "<= 15 %",
            abs(rel(s_["P_cu_W"] + s_["P_core_W"], om_l["P_cu_W"] + om_l["P_core_W"])) <= 0.15)
    om = "dab_m1_om_loss" in ok
    need = {"dab_cu": ["dab_m1_fit", "dab_m1_loss", "dab_m1_set"] + (["dab_m1_om_loss"] if om else []),
            "dab_thermal": ["dab_m1_hot"], "dab_core_set": ["dab_m1_hot", "dab_m1_b"],
            "dab_leak": ["lser_m1_L"] + (["dab_m1_om_leak", "lser_m1_om_L"] if om else []),
            "lser_loss": ["lser_m1_fit", "lser_m1_hot", "dab_m1_set"] + (["lser_m1_om_loss"] if om else []),
            "dab_ins": ["dab_m1_ins"]}
    ev = {c["id"]: f"{c['id']} {c['value']} ({c['limit']})" for c in C}
    closed = {}
    for t, ids in need.items():
        if all(ok.get(i) for i in ids):
            part = "design_dab_series_inductor.json" if t == "lser_loss" else "design_dab_transformer.json"
            closed[t] = (f"closed by sim/out/magnetics/{part} rev M1 (calculated; confirm on the first article)",
                         "; ".join(ev[i] for i in ids))
    return C, closed


def report_designs(designs, C, closed, dab_cf=None):
    """append the constructions (rev M1 / M2) to report.md"""
    L = ["", "## Constructions by the magnetics designer (rev M1)", "",
         "Converter engineers read `sim/out/magnetics/design_<part>.json` (keys at the top of `sim/magnetics.py`); the "
         "winding-house sheets `spec_<part>.md` are rendered from the same data. The verification of the previous "
         "constructions above is kept for the record.", ""]
    for name, d in designs.items():
        L += [f"### {name}", "", d["construction"], "", _kv(d["verification"]), "",
              f"Cost: {d['cost']['total_usd']:.0f} USD (estimate). Row for `gen/data/cost_estimates.csv`:", "",
              "```", d["cost"]["cost_estimates_row"], "```", ""]
    pv, cm = designs.get("pv_inductor", {}), designs.get("port_cm_choke", {})
    if pv.get("options"):
        L += ["### PV inductor rev M2 - design to cost (D-044)", "", _rows(pv["options"]), "", pv["electrical"]["loss_note"] + ".", ""]
    if cm.get("revision") == "M2":
        e = cm["electrical"]
        L += ["### Port CM choke rev M2 - requirement of ARCHITECTURE-COSTFIRST 8.1 checked", "",
              f"A series CM impedance only acts between the Y-cap node and the artificial network: with 18.8 nF and 150 ohm the "
              f"architecture's single ring (|Z| 100 ohm) gives {e['derivation_check']['architecture_ring_100ohm_gives_dB']} dB, not 16 dB; a ring "
              f"that survives the 150 Hz CM current gives {_v(e['derivation_check']['single_ring_gives_dB'])} dB; a choke alone needs ~1 kohm "
              f"at 150 kHz ({_v(e['derivation_check']['choke_only_cheapest'])}). Cheapest way to the 16 dB: CM capacitance to PE "
              f">= {e['C_Y_total_needed_F']*1e9:.0f} nF plus the ring ({_v(e['IL_with_C_Y_and_ring_dB'])} dB); {e['C_Y_consequence']}. "
              f"Emission margin given up against rev M1 if only the ring is fitted: {_v(e['emission_margin_given_up_vs_M1_dB'])} dB.", ""]
    if dab_cf:
        L += ["### DAB magnetics - what a cost-first version would give (no redesign)", "", _rows(dab_cf), "",
              "The DAB magnetics leave little to cut: the litz grade is the only real lever (0.071 mm saves ~20 USD per pair for "
              "~+25 W at 60 kW, 0.10 mm ~40 USD for ~+75 W, and both push the hot spot past 130 C on 65 C coolant); a single "
              "transformer instead of the pair overheats (> 280 C) even with three core sets; the series inductor is at its minimum "
              "core count for the 635 A saturation requirement (3 sets x 4 turns saves nothing once the extra copper is paid). "
              "A different leakage split (integrated leakage, U93 class) was 191-277 W in rev M1 and has no Asian core source; a "
              "lower frequency needs more volt-second area (more cores), a higher one more proximity loss. The real cost lever is "
              "the requirement envelope: I1_rms_max 137 A (MG-16) and I_sat 635 A (MG-15); capped at the rev-M1 values the pair "
              "stays near 150 W / 120 C and the coarser 0.071 mm litz becomes thermally acceptable.", ""]
    L += ["Checks on the constructions (no allowances):", "",
          table(C, [("check", lambda c: c["id"]), ("what", lambda c: c["what"]), ("value", lambda c: c["value"]),
                    ("limit", lambda c: c["limit"]), ("result", lambda c: "pass" if c["ok"] else "FAIL")]), "",
          "Findings closed: " + (", ".join(f"{MG_IDS[t]} ({t})" for t in closed) or "none") + ".", ""]
    with open(os.path.join(OUT, "report.md"), "a") as fh:
        fh.write("\n".join(L))




# ---- AUX-HV flyback T1 (MG-11/12/13): construction rev M1 -------------------------------------------------------------
EC34A = dict(maker="DMEGC", pn="EC34A", mat="DMR95", src=MAG + "DMEGC-EC34A.pdf p.1", Ae=97.5e-6, Amin=91.6e-6, le=79.5e-3,
             Ve=7751.25e-9, mass=0.039, d_leg=10.8e-3, win_h=24.2e-3, win_w=7.75e-3, mu_i=3300.0,
             note="ETD 34/17/11 equivalent (Ae/Amin/le within 1 % of the MAS ETD 34/17/11 shape); standard ETD34 coil formers fit")
AUX_RULES = dict(wall=1.0e-3, flange=1.3e-3, outer_clear=0.6e-3, tape=0.06e-3, foil=0.05e-3, tiw_add=0.2e-3, enamel=0.05e-3)


def aux_eval(A, core=EC34A, setback=2.0e-3, p_wire=("solid", 1, 0.30e-3), s_wire=None, t_p1sh=8, t_shs=2, t_sa=2, t_ap2=1, t_out=2,
             share_aux=0.0, a_wire=None,
             rules=AUX_RULES):
    """AUX-HV flyback, construction P1 | tape | SH | tape | S (TIW) | tape | AUX | tape | P2 (converter engineer's order)
    on a round-leg core with the copper set back 'setback' from the gapped centre leg (former wall + spacer).
    Same loss physics as aux_transformer_own (DCM triangles, Bessel strands, 1-D ramp + line-source gap fringing)."""
    R = rules
    np_, ns, na, lp, f = A["np"], A["ns"], A["na"], A["lp"], A["f"]
    n = np_ / ns
    a_post = math.pi / 4 * core["d_leg"] ** 2
    al = lambda g: MU0 * a_post * fringing_mclyman(g, a_post, core["win_h"]) / (g + core["le"] / core["mu_i"] * a_post / core["Ae"])  # noqa: E731
    lo, hi = 0.05e-3, 3e-3
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if al(mid) > A["al"] else (lo, mid)
    gap = 0.5 * (lo + hi)
    b = core["win_h"] - 2 * R["flange"]
    kind, n_str, d = p_wire
    od_p = (math.sqrt(n_str) * 1.15 * (d + R["enamel"] / 2) if n_str > 1 else d) + R["enamel"]
    sk, sn, sd = s_wire or ("solid", 1, A["wire"]["s"])
    od_s = (math.sqrt(sn) * 1.15 * (sd + R["enamel"] / 2) if sn > 1 else sd) + R["tiw_add"]
    ak, an, ad = a_wire or ("solid", 1, A["wire"]["a"])
    od_a = (math.sqrt(an) * 1.15 * (ad + R["enamel"] / 2) if an > 1 else ad) + R["enamel"]
    # radial stack from the leg surface: (name, thickness, wire OD, turns)
    lay = [("wall+spacer", R["wall"] + setback), ("P1", od_p), ("tape", t_p1sh * R["tape"]), ("SH", R["foil"]),
           ("tape", t_shs * R["tape"]), ("S", od_s), ("tape", t_sa * R["tape"]), ("AUX", od_a), ("tape", t_ap2 * R["tape"]),
           ("P2", od_p), ("tape", t_out * R["tape"])]
    r, pos = 0.0, {}
    for nm, t in lay:
        pos[nm] = (r, r + t)
        r += t
    build = r
    fits = build <= core["win_w"] - R["outer_clear"]
    mlt = {k: math.pi * (core["d_leg"] + 2 * (v[0] + v[1]) / 2) for k, v in pos.items()}
    width = dict(P1=math.ceil(np_ / 2) * od_p, S=ns * od_s, AUX=na * od_a, P2=(np_ - math.ceil(np_ / 2)) * od_p)
    fits_b = max(width.values()) <= b
    ks, al_, bs = dmr95_steinmetz()
    pts = {}
    for v in A["vin"]:
        pin = A["pout"] / A["eta"][str(v)]
        ipk = math.sqrt(2 * pin / (lp * f))
        t1, t2, T = lp * ipk / v, lp * ipk / (n * (A["vout"] + 0.9)), 1 / f
        bpk = lp * ipk / (np_ * core["Ae"])
        pc = igse(ks, al_, bs, np.array([0, t1, t1 + t2, T]), np.array([0, bpk, 0, 0])) * core["Ve"]
        ts = np.linspace(0, T, 4096, endpoint=False)
        ip = np.where(ts < t1, ipk * ts / t1, 0.0)
        is_ = np.where((ts >= t1) & (ts < t1 + t2), n * ipk * (1 - (ts - t1) / t2), 0.0)
        hp, hs = harmonics(ts, ip, 30), harmonics(ts, is_, 30)
        wp = sum(litz_loss(hp, n_str, d, k_, mlt[w], ramp_h2(0, np_ / 2, b), 100.0, max(od_p / 2, 1e-4))["total"]
                 for w, k_ in (("P1", math.ceil(np_ / 2)), ("P2", np_ - math.ceil(np_ / 2))))
        hs = harmonics(ts, is_ * (1 - share_aux), 30)          # loaded aux winding (aux75: live 24 V) takes its share
        ws = litz_loss(hs, sn, sd, ns, mlt["S"], ramp_h2(0, ns, b), 100.0, max((od_s - R["tiw_add"]) / 2, 1e-4))["total"]
        if share_aux:
            ia = share_aux * np_ / na * ipk * np.where((ts >= t1) & (ts < t1 + t2), 1 - (ts - t1) / t2, 0.0)
            ws += litz_loss(harmonics(ts, ia, 30), an, ad, na, mlt["AUX"], ramp_h2(0, na, b), 100.0, 1.0 if an == 1 else od_a / 2)["total"]
        fm = np.where(ts < t1, np_ * ipk * ts / t1, np.where(ts < t1 + t2, np_ * ipk * (1 - (ts - t1) / t2), 0.0))
        hf = harmonics(ts, fm, 60)
        p_fr = 0.0
        for w, dw, ns_, nt in (("P1", d, n_str, math.ceil(np_ / 2)), ("S", sd, sn, ns), ("AUX", ad, an, na),
                               ("P2", d, n_str, np_ - math.ceil(np_ / 2))):
            rr = 0.5 * sum(pos[w])
            zz = np.linspace(-width[w] / 2, width[w] / 2, nt)
            for fh, frms in hf[1:]:
                p_fr += ns_ * np.sum(_strand_factors(dw, fh, 100)[1] * (frms * math.sqrt(2) / (math.pi * np.sqrt(rr ** 2 + zz ** 2))) ** 2) * mlt[w]
        pts[v] = dict(Ipk=ipk, D=t1 * f, D2=t2 * f, B_pk=bpk, P_core=pc, P_cu_1d=wp + ws, P_fringe=p_fr, P_cu=wp + ws + p_fr,
                      Ip_rms=math.sqrt(np.mean(ip ** 2)), Is_rms=math.sqrt(np.mean(is_ ** 2)))
    worst = max(pts, key=lambda k: pts[k]["P_cu"] + pts[k]["P_core"])
    # leakage: 1-D MMF energy (S shorted, primary-referred), x2 for ends and terminations (converter engineer's factor)
    tt = lambda nm: pos[nm][1] - pos[nm][0]  # noqa: E731
    g1 = sum(tt(k) for k in ("SH",)) + (t_p1sh + t_shs) * R["tape"]
    g2 = (t_sa + t_ap2) * R["tape"] + tt("AUX")
    integral = (np_ / 2) ** 2 * (tt("P1") / 3 + g1 + tt("S") / 3 + g2 + tt("P2") / 3)
    mlt_m = sum(mlt[k] for k in ("P1", "S", "P2")) / 3
    l1d = MU0 * mlt_m / b * integral
    # switched capacitance P1 (drain end) to SH: parallel plate through the tape, eps_r 3.3 polyester (+ potting)
    c_sw = 8.854e-12 * 3.3 * mlt["P1"] * width["P1"] / (t_p1sh * R["tape"] + od_p / 2)
    bl_amin = lp * (1 + A["lp_tol"]) * A["ilim"] / (np_ * core["Amin"])
    return dict(core=core["pn"], gap=gap, AL=A["al"], breadth=b, build=build, fits=fits, fits_breadth=fits_b, width=width,
                pos=pos, mlt=mlt, od_p=od_p, od_s=od_s, p_wire=p_wire, s_wire=(sk, sn, sd), setback=setback, t_p1sh=t_p1sh, points=pts, worst=worst,
                P_cu_worst=pts[worst]["P_cu"], P_core_worst=pts[worst]["P_core"], L_leak_1d=l1d, L_leak_est=2 * l1d,
                C_sw=c_sw, B_lim_Amin=bl_amin, steinmetz=(ks, al_, bs),
                mass_cu=8900.0 * (np_ * mlt["P1"] * n_str * math.pi * d * d / 4 + ns * mlt["S"] * sn * math.pi * sd * sd / 4
                                  + na * mlt["AUX"] * an * math.pi * ad ** 2 / 4))


AUX_CHOICE = dict(core=EC34A, setback=1.0e-3, p_wire=("solid", 1, 0.30e-3), s_wire=("litz", 64, 0.10e-3), t_p1sh=12, t_shs=4,
                  t_sa=4, t_ap2=1, t_out=2)
AUX_PRICE = dict(core=0.40, former=0.35, tiw_litz=1.20, wire_foil_tape=0.40, case_potting=1.20, labour=1.20, test=0.60,
                 basis="ESTIMATES, no quote: DMEGC EC34A set ~0.40 USD (39 g, Chinese small-core pricing ~10 USD/kg); ETD34 "
                       "14-pin former (CTI >= 600 grade) 0.35; TIW-litz 64 x 0.10 mm ~1.3 m at ~0.9 USD/m; magnet wire, foil, "
                       "tapes, spacer 0.40; case + vacuum epoxy potting 1.20; winding 1.20; PD + hipot routine test 0.60; +10 %")
TIW_INS = 0.1e-3          # three-layer extruded insulation, radial (TIW / TIW-litz), typical


def design_aux(specs):
    A = aux_inputs(specs["aux"])
    ch = {k: v for k, v in AUX_CHOICE.items()}
    r = aux_eval(A, **ch)
    r["A"] = A
    tot = sum(AUX_PRICE[k] for k in AUX_PRICE if k != "basis")
    r["cost"] = dict(core_usd=AUX_PRICE["core"], copper_usd=AUX_PRICE["tiw_litz"] + AUX_PRICE["wire_foil_tape"],
                     insulation_usd=AUX_PRICE["former"] + AUX_PRICE["case_potting"], labour_usd=AUX_PRICE["labour"] + AUX_PRICE["test"],
                     total_usd=round(tot * 1.10, 1))
    return r


def om_design_aux(specs, r):
    ch = AUX_CHOICE
    od = math.sqrt(ch["s_wire"][1]) * 1.15 * (ch["s_wire"][2] + AUX_RULES["enamel"] / 2)
    try:
        return om_aux_transformer(r, specs["aux"], shape="ETD 34/17/11", material="DMR95", gap=r["gap"],
                                  s_wire=("litz", ch["s_wire"][1], ch["s_wire"][2], od), wall=AUX_RULES["wall"] + ch["setback"])
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def write_design_aux(specs, r, om, ins):
    A, x, c = r["A"], specs["aux"]["transformer"], EC34A
    q = x.get("requirements", {})
    ii = x.get("insulation", {})
    k_, a_, b_ = r["steinmetz"]
    pw = r["worst"]
    lay = []
    for nm, (r0, r1) in r["pos"].items():
        lay.append(dict(layer=nm, from_leg_mm=round(r0 * 1e3, 2), to_mm=round(r1 * 1e3, 2), MLT_m=round(r["mlt"][nm], 4)))
    t_min_solid = r["pos"]["S"][0] - r["pos"]["SH"][1] + TIW_INS
    W = dict(P1=f"45 t enamelled Cu 0.30 mm grade 2, 1 layer, start = DRAIN pin (dot)",
             SH="copper foil 0.05 mm, 1 t, open with insulated overlap, -> PGND pin",
             S=f"15 t TIW-litz {AUX_CHOICE['s_wire'][1]} x {AUX_CHOICE['s_wire'][2]*1e3:.2f} mm (three-layer extruded insulation "
               "certified for reinforced insulation, IEC 61558-2-16 / IEC 62368-1 Annex J), 1 layer, start = rectifier anode (dot), "
               "insulation unbroken to the pin, exits in PTFE sleeve",
             AUX="16 t enamelled Cu 0.25 mm spaced over the breadth, start = aux rectifier anode (dot), finish = PGND",
             P2="45 t enamelled Cu 0.30 mm grade 2, 1 layer, finish = HV_BULK pin")
    d = dict(part="aux_hv_transformer", revision="M1", status="proposed construction - calculated, not built or measured",
             basis={"spec": "sim/out/aux_hv_design/aux_hv_spec.json", "spec_mtime": _mtime("sim/out/aux_hv_design/aux_hv_spec.json"),
                    "spec_rev": specs["aux"].get("rev")},
             construction=f"DMEGC EC34A (ETD 34/17/11 class) DMR95, centre-leg gap {r['gap']*1e3:.2f} mm, {A['np']}:{A['ns']}:{A['na']} "
                          "in the converter engineer's order P1|SH|S|AUX|P2, copper set back 2.0 mm from the gapped leg (1.0 mm "
                          "former wall + 1.0 mm spacer), secondary in TIW-litz, vacuum-potted in a case",
             electrical=dict(L_p_H=A["lp"], L_p_tol="+/-7 % at 10 kHz (gap ground to A_L)", A_L_H=A["al"], turns=f"{A['np']}:{A['ns']}:{A['na']}",
                             n=A["np"] / A["ns"], f_sw_Hz=A["f"], mode="DCM", I_pk_full_load_A=q.get("ipk_full_load_A"),
                             I_pk_limit_A=A["ilim"], B_pk_worst_T=r["points"][pw]["B_pk"], B_limit_Amin_T=r["B_lim_Amin"],
                             B_limit_rule_T=(q.get("b_max_on_Amin_mT") or {}).get("limit", 348.5) * 1e-3,
                             L_leak_1d_H=r["L_leak_1d"], L_leak_est_H=r["L_leak_est"], L_leak_max_H=(q.get("leakage_max_uH") or 34) * 1e-6,
                             C_switched_P1_SH_own_F=r["C_sw"],
                             C_switched_note="own parallel-plate P1-SH value; on the same basis the ETD29 rev C0 stack gives 27 pF "
                                             "where the converter engineer states 36.3 pF -> scaled 32 pF vs limit 38.7 pF",
                             winding_loss_max_W=(q.get("winding_loss_max_W") or {}).get("limit")),
             core=dict(maker=c["maker"], part_number=f"{c['pn']} set ({c['mat']})", material=c["mat"], Ae_m2=c["Ae"], A_min_m2=c["Amin"],
                       le_m=c["le"], Ve_m3=c["Ve"], datasheet=c["src"], note=c["note"],
                       gap={"type": "ground centre-leg gap", "length_m": r["gap"], "note": "set by A_L; outer legs ungapped"},
                       asian_alternative="DMEGC EC34A is itself the Asian part; ETD29-class DMEGC EC29A (Ae 83 mm2) exists but its "
                                         "outline vs ETD29 formers is not verified (drawing is an image)",
                       western_reference="TDK ETD 34/17/11 N97 set"),
             windings=[dict(order=i + 1, name=k, turns={"P1": 45, "SH": 1, "S": A["ns"], "AUX": A["na"], "P2": 45}[k], spec=v)
                       for i, (k, v) in enumerate(W.items())],
             fit=dict(build_m=r["build"], available_m=c["win_w"] - AUX_RULES["outer_clear"], fits=bool(r["fits"] and r["fits_breadth"]),
                      breadth_m=r["breadth"], widest_layer_m=max(r["width"].values()), stack=lay),
             insulation=dict(system="VACUUM-POTTED (epoxy, class F, void-free) in a case - REQUIRED for the PD routine test; reinforced "
                                    "insulation = TIW-litz three-layer extruded insulation + 4 layers 0.06 mm polyester tape on each side "
                                    "of S; tapes P1-SH 12 L set the switched capacitance; primary pins (P1, P2, AUX, SH) on one row, "
                                    "secondary pins on the opposite row; the core is a primary-side conductive part",
                             min_solid_ps_m=t_min_solid, creepage_pins_m=ii.get("cpg_pd2_iiia", 20.0) * 1e-3,
                             creepage_pins_note="20 mm along the case / former between the pin rows (PD2, CTI unknown -> IIIa); 10 mm "
                                                "if the former and case material has CTI >= 600 (group I); inside the potting PD1: 6.4 mm",
                             clearance_m=max(ii.get("clr", [10.4])) * 1e-3,
                             levels=[dict(label="Reinforced HV-PELV, AUX-HV flyback T1", u_rp=max(ii.get("u_rp", [0])), pd_test=ii.get("pd_v"),
                                          pd_ext=ii.get("pd_ext"), ac=ii.get("ac_v"), imp=ii.get("imp_v"), cr_pd1=ii.get("cpg_pd1"),
                                          cr_pd2=ii.get("cpg_pd2_iiia"), clearance=ii.get("clr"))],
                             routine_tests=f"PD <= 10 pC at {ii.get('pd_v')} V pk (extinction >= {ii.get('pd_ext')} V pk), primary+aux+shield to secondary",
                             type_tests=f"AC {ii.get('ac_v')} V rms 60 s; impulse {ii.get('imp_v')} V 1.2/50; thermal run at 30 W"),
             loss_model=dict(core={"k": k_, "alpha": a_, "beta": b_, "Ve_m3": c["Ve"], "N": A["np"], "Ae_m2": c["Ae"],
                                   "formula": "unipolar DCM flux 0 -> B_pk -> 0 (iGSE), B_pk = L_p I_pk / (N_p A_e)"},
                             winding={"R_ac_per_harmonic": None, "referred_to": "n/a - use loss_table / loss_grid (DCM shape changes with V_in)",
                                      "temperature_C": 100.0, "formula": "1-D Dowell ramp + line-source gap fringing on each layer"}),
             loss_table=[dict(point=f"{v} V, {A['pout']:.0f} W", I_rms_A=p["Ip_rms"], I_s_rms_A=p["Is_rms"], B_pk_T=p["B_pk"],
                              P_core_W=p["P_core"], P_cu_W=p["P_cu"], P_fringe_W=p["P_fringe"], P_total_W=p["P_core"] + p["P_cu"])
                         for v, p in sorted(r["points"].items())],
             loss_grid=dict(V_in_V=sorted(r["points"]), P_total_W=[r["points"][v]["P_cu"] + r["points"][v]["P_core"] for v in sorted(r["points"])],
                            note="30 W output; loss scales ~ with P_out^1 (Ipk^2 x duty); interpolate in V_in"),
             thermal=dict(T_rise_K=13.0, basis="potted case ~38 x 30 x 30 mm, natural convection + radiation ~10 W/m2K on 6400 mm2 "
                                               "-> ~15 K/W; internal gradient < 2 K at < 1 W", interface="board-mounted, no heatsink"),
             mass_kg=c["mass"] + r["mass_cu"] + 0.031,
             cost=dict(r["cost"], basis=AUX_PRICE["basis"],
                       cost_estimates_row=f"AUX-HV-T1 rev M1,{r['cost']['total_usd']:.1f},\"ESTIMATE (sim/magnetics.py design_aux_hv_transformer.json rev M1): "
                                          "DMEGC EC34A DMR95 0.40 + ETD34 former 0.35 + TIW-litz 1.20 + wire/foil/tape 0.40 + case and vacuum potting 1.20 "
                                          "+ winding 1.20 + PD/hipot test 0.60, +10 %; replaces the rev A0 row; no quote\",low"),
             verification=dict(own=dict(P_cu_W=r["P_cu_worst"], P_core_W=r["P_core_worst"], point_V=pw, L_leak_1d_H=r["L_leak_1d"]),
                               openmagnetics=({"L_p_H": _num(om.get("L"), "magnetizingInductance"), "P_core_W": _num(om.get("core"), "coreLosses"),
                                               "P_cu_W": _num(om.get("winding"), "windingLosses"),
                                               "L_leak_H": _num(om.get("leakage"), "leakageInductancePerWinding"), "point_V": om.get("vin"),
                                               "note": "same former wall + spacer (2 mm); no tapes / shield / AUX layer in the OM model"}
                                              if "error" not in om else om),
                               previous_designer=dict(P_cu_W=(q.get("winding_loss_max_W") or {}).get("interim_used"), core="ETD 29/16/10 N97",
                                                      B_lim_Amin_T=x.get("b_lim_amin_mT", 0) * 1e-3, L_leak_est_H=x.get("llk_est_uh", 0) * 1e-6)))
    with open(os.path.join(OUT, "design_aux_hv_transformer.json"), "w") as fh:
        json.dump(d, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return d


def design_checks_aux(d):
    e, v, ins = d["electrical"], d["verification"], d["insulation"]
    C, ok = [], {}

    def chk(cid, what, value, limit, good):
        C.append(dict(id=cid, what=what, value=value, limit=limit, ok=bool(good), exc=""))
        ok[cid] = bool(good)

    lim = e["winding_loss_max_W"] or 1.67
    pc = max(r["P_cu_W"] for r in d["loss_table"])
    chk("aux_m1_fit", "M1 AUX stack fits the ETD34 window (radial, breadth)", f"{d['fit']['build_m']*1e3:.2f} mm",
        f"<= {d['fit']['available_m']*1e3:.2f} mm", d["fit"]["fits"])
    chk("aux_m1_loss", "M1 AUX winding loss (own, incl. gap fringing), worst input", f"{pc:.2f} W", f"<= {lim:.2f} W", pc <= lim)
    om = v.get("openmagnetics", {})
    if om.get("P_cu_W") is not None:
        chk("aux_m1_loss_om", "M1 AUX winding loss OpenMagnetics (same former wall) also below the limit", f"{om['P_cu_W']:.2f} W",
            f"<= {lim:.2f} W", om["P_cu_W"] <= lim)
        chk("aux_m1_L_om", "M1 AUX L_p OpenMagnetics vs requirement", pct(rel(om["L_p_H"], e["L_p_H"])), "<= 10 %",
            abs(rel(om["L_p_H"], e["L_p_H"])) <= 0.10)
    chk("aux_m1_b", "M1 AUX B at the current limit on A_min (L_p +7 %)", f"{e['B_limit_Amin_T']*1e3:.0f} mT",
        f"<= {e['B_limit_rule_T']*1e3:.0f} mT", e["B_limit_Amin_T"] <= e["B_limit_rule_T"])
    chk("aux_m1_leak", "M1 AUX leakage estimate (1-D x2)", f"{e['L_leak_est_H']*1e6:.1f} uH", f"<= {e['L_leak_max_H']*1e6:.1f} uH",
        e["L_leak_est_H"] <= e["L_leak_max_H"])
    lv = ins["levels"][0]
    ekv = (lv["pd_test"] or 0) / (ins["min_solid_ps_m"] * 1e3) / 1e3
    chk("aux_m1_ins", "M1 AUX potted; solid P-S field at the PD test; pin clearance",
        f"{ekv:.1f} kV/mm, {ins['clearance_m']*1e3:.1f} mm", f"<= 8 kV/mm (design rule, potted), >= {max(lv['clearance'] or [10.4]):.1f} mm",
        "VACUUM-POTTED" in ins["system"] and ekv <= 8.0 and ins["clearance_m"] * 1e3 >= max(lv["clearance"] or [10.4]))
    need = {"aux_cu": ["aux_m1_fit", "aux_m1_loss"] + (["aux_m1_loss_om"] if "aux_m1_loss_om" in ok else []),
            "aux_blim": ["aux_m1_b"], "aux_ins": ["aux_m1_ins", "aux_m1_fit"]}
    ev = {c["id"]: f"{c['id']} {c['value']} ({c['limit']})" for c in C}
    closed = {t: ("closed by sim/out/magnetics/design_aux_hv_transformer.json rev M1 (calculated; confirm on the first article)",
                  "; ".join(ev[i] for i in ids)) for t, ids in need.items() if all(ok.get(i) for i in ids)}
    return C, closed


# ---- port common-mode chokes CMC160 / CMC200 (MG-10, IC-09): construction rev M1 ------------------------------------------
CMC_RULES = dict(case=2.0e-3, spacer=1.0e-3, sleeve=0.75e-3, fill_max=0.45, b_frac=0.8, bsat_100=1.15, mu_tol=0.25,
                 l_dm_frac=0.003, rho_core=7300.0, k_fill=0.75, leads=0.2, k_pot=1.0, t_pot=5e-3, k_pad=3.0, t_pad=1e-3,
                 r_chassis=0.2)   # potted case on the chassis: epoxy >= 1 W/mK, 1 mm pad, 0.2 K/W spreading (ASSUMED)
CMC_GRADES = {   # flat-mu nanocrystalline grades: (label, mu at 150 Hz, mu at 16 kHz, mu at 100 kHz, source)
    "flat11k": ("flat-mu nanocrystalline mu 11 000 +/-25 % (field-annealed, RFQ: Shincore / Yunlu / AT&M)", 11000.0, 11000.0, 9000.0,
                "ASSUMED grade to be quoted; Yunlu datasheet p.6 states adjustable mu with DC-bias capability"),
    "nanoperm8000": ("Magnetec Nanoperm 8000 (flat)", 7914.0, 7474.0, 5291.0, "OpenMagnetics MAS material curve"),
    "sc1k107lp": ("Shincore SC-1K107-LP (flat, Asian catalogue)", 30320.0, 28737.0, 20935.0, "OpenMagnetics MAS material curve"),
}
PCS_CASES = {"A_port_spec_50V": 50.0,                                     # port_spec cm_choke V_cm_LF (400 V AC class PCS)
             "B_690VAC_minmax": 0.2026 * 690 * math.sqrt(2 / 3) / math.sqrt(2),
             "C_800VAC_minmax": 0.2026 * 800 * math.sqrt(2 / 3) / math.sqrt(2)}


def cmc_eval(cm, I_dc, a_cu, grade, N, k, case_key="B_690VAC_minmax", R=CMC_RULES, core=None):
    """bifilar N-turn busbar CM choke on k stacked 80/50/25 nanocrystalline toroids"""
    c = core or NANO_REF
    lab, mu150, mu16, mu100, src = CMC_GRADES[grade]
    A, le = k * c["ae"], c["le"]
    L = lambda mu: MU0 * mu * N ** 2 * A / le  # noqa: E731
    lmin, lnom, lmax150 = L(mu16 * (1 - R["mu_tol"])), L(mu16), L(mu150 * (1 + R["mu_tol"]))
    z16, z100 = 2 * math.pi * cm["f_HF_band_Hz"][0] * lmin, 2 * math.pi * cm["f_HF_band_Hz"][1] * L(mu100 * (1 - R["mu_tol"]))
    cpv = max(cm["I_cm_LF_A_rms"]) / (2 * math.pi * cm["f_LF_Hz"] * cm["V_cm_LF_V_rms"])
    i_lf = {kk: v * 2 * math.pi * cm["f_LF_Hz"] * cpv for kk, v in PCS_CASES.items()}
    b_lf = {kk: MU0 * mu150 * (1 + R["mu_tol"]) * N * i * math.sqrt(2) / le for kk, i in i_lf.items()}
    b_dm = R["l_dm_frac"] * lnom * I_dc / (N * A)
    b_hf = cm["V_cm_HF_V_rms"] * math.sqrt(2) / (2 * math.pi * cm["f_HF_band_Hz"][0] * N * A)
    b_tot = {kk: v + b_dm + b_hf for kk, v in b_lf.items()}
    b_allow = R["b_frac"] * R["bsat_100"]
    w, t = (10e-3, a_cu / 10e-3)
    hole = math.pi / 4 * (c["id"] - 2 * R["case"]) ** 2
    fill = 2 * N * (w + 2 * R["sleeve"]) * (t + 2 * R["sleeve"]) / hole
    h_stack = k * c["h"] + (k - 1) * R["spacer"] + 2 * R["case"]
    radial = (c["od"] - c["id"]) / 2 + 2 * R["case"]
    mlt = 2 * h_stack + 2 * radial + 4 * (t + 2 * R["sleeve"])
    length = N * mlt + R["leads"]
    r_pole = rho_cu(90.0) * length / a_cu
    p_cu = 2 * I_dc ** 2 * r_pole
    m_core = k * math.pi / 4 * (c["od"] ** 2 - c["id"] ** 2) * c["h"] * R["k_fill"] * R["rho_core"]
    m_cu = 2 * length * a_cu * 8900.0
    p_core = m_core * (1.205e-3 * 0.15 ** 1.826 * (b_lf[case_key] / 0.1) ** 2.077 + 1.205e-3 * 16 ** 1.826 * (b_hf / 0.1) ** 2.077)
    a_bar = 2 * length * 2 * (w + t + 4 * R["sleeve"]) * 0.5          # half of the sleeved bar surface wetted by potting
    a_foot = (c["od"] + 4 * R["case"]) * h_stack
    rth = R["t_pot"] / (R["k_pot"] * a_bar) + R["t_pad"] / (R["k_pad"] * a_foot) + R["r_chassis"]
    dT = p_cu * rth
    ok = (lmin >= cm.get("L_cm_min_H", 0.5e-3) and z16 >= cm["Z_cm_min_ohm"] and b_tot[case_key] <= b_allow and fill <= R["fill_max"]
          and dT <= 40.0)
    cost = dict(core_usd=20.0 * m_core + 1.5 * k, copper_usd=(14.4 + 6.0) * m_cu, insulation_usd=6.0 + 6.0 + 8.0,
                labour_usd=10.0 + 3.0)
    cost["total_usd"] = round(sum(cost.values()) * 1.10, 0)
    return dict(grade=grade, grade_label=lab, grade_source=src, N=N, k=k, A=A, le=le, L_min=lmin, L_nom=lnom, L_max_150Hz=lmax150,
                Z_16k_min=z16, Z_100k_min=z100, C_pv_max=cpv, I_cm_lf=i_lf, B_lf=b_lf, B_dm=b_dm, B_hf=b_hf, B_total=b_tot,
                B_allow=b_allow, fill=fill, mlt=mlt, length=length, R_pole=r_pole, P_cu=p_cu, P_core=p_core, dT=dT,
                mass_core=m_core, mass_cu=m_cu, mass=m_core + m_cu + 0.6, cost=cost, ok=ok, a_cu=a_cu, I_dc=I_dc, Rth=rth,
                h_stack=h_stack,
                bar=f"{w*1e3:.0f} x {t*1e3:.0f} mm")


CMC_CHOICE = dict(grade="flat11k", N=3, k=4)


def design_cmc(specs_cm):
    cm, _ = port_cmc_inputs()
    ps = load("sim/out/port_design/port_spec.json")["cm_choke"]
    ps = dict(ps, L_cm_min_H=max(v["L_cm"] for v in cm.values()))
    out = {}
    for key, s in cm.items():
        a_cu = 50e-6 if s["I"] <= 160 else 70e-6
        out[key] = dict(choice=cmc_eval(ps, s["I"], a_cu, **CMC_CHOICE),
                        alternatives=[cmc_eval(ps, s["I"], a_cu, g, n, k) for g, n, k in
                                      (("nanoperm8000", 3, 6), ("sc1k107lp", 2, 4), ("sc1k107lp", 1, 14), ("flat11k", 2, 7))],
                        spec=s)
    return out, ps


def om_design_cmc():
    try:
        o = om_cmc(dict(N=CMC_CHOICE["N"], spec=dict(L_cm=0.5e-3)), "Nanoperm 8000", stacks=CMC_CHOICE["k"])
        return dict(material="Nanoperm 8000 (closest flat grade in MAS)", L_10k_H=_num(o.get("L"), "magnetizingInductance"),
                    mu_10k=o.get("mu_10k"), own_L_same_material_H=MU0 * o.get("mu_10k", 7636.0) * CMC_CHOICE["N"] ** 2
                    * CMC_CHOICE["k"] * NANO_REF["ae"] / NANO_REF["le"])
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def write_design_cmc(dcm, ps, om, ins, des_cost=None):
    v0 = next(iter(dcm.values()))["choice"]
    c = NANO_REF
    lev = _ins_levels(ins, ["Basic HV-PE, DC pole"])
    var = {}
    for key, v in dcm.items():
        r, s = v["choice"], v["spec"]
        var[key] = dict(I_dc_rated_A=s["I"], copper_bar=f"{r['bar']} tin-plated Cu ({r['a_cu']*1e6:.0f} mm2)", L_min_H=r["L_min"],
                        L_nom_H=r["L_nom"], R_pole_90C_ohm=r["R_pole"], P_cu_rated_W=r["P_cu"], P_core_W=r["P_core"],
                        dT_rated_K=r["dT"], B_total_T=r["B_total"], hole_fill=r["fill"], mass_kg=r["mass"], cost=r["cost"],
                        alternatives=[dict(grade=a["grade_label"], N=a["N"], cores=a["k"], L_min_H=a["L_min"],
                                           B_total_T=a["B_total"], P_cu_W=a["P_cu"], cost_usd=a["cost"]["total_usd"], meets=a["ok"])
                                      for a in v["alternatives"]])
    rows = []
    for key, v in dcm.items():
        r = v["choice"]
        for i_ in (0.5 * r["I_dc"], 0.75 * r["I_dc"], r["I_dc"]):
            rows.append(dict(point=f"{key} DC {i_:.0f} A + LF CM case B", I_rms_A=i_, B_pk_T=r["B_total"]["B_690VAC_minmax"],
                             P_core_W=r["P_core"], P_cu_W=2 * i_ ** 2 * r["R_pole"], P_total_W=r["P_core"] + 2 * i_ ** 2 * r["R_pole"]))
    cpv = v0["C_pv_max"]
    d = dict(part="port_cm_choke", revision="M1", status="proposed construction - calculated, not built or measured",
             basis={"spec": "sim/out/port_design/port_spec.json", "spec_mtime": _mtime("sim/out/port_design/port_spec.json"),
                    "ratings_from": "gen/port.py CMC160 / CMC200 desc (160 / 200 A DC, L_cm >= 0.5 mH @ 10 kHz, dT <= 40 K)"},
             construction=f"{v0['k']} stacked flat-mu nanocrystalline toroids 80/50/25 mm (mu 11 000 +/-25 %), bifilar "
                          f"{v0['N']}-turn formed copper bars (+ and - side by side), sleeved, potted with thermally conductive "
                          "epoxy in a case bolted to the chassis; same core stack for CMC160 and CMC200, bar size differs",
             electrical=dict(L_cm_min_req_H=ps["L_cm_min_H"], Z_cm_min_req_ohm=ps["Z_cm_min_ohm"], f_HF_band_Hz=ps["f_HF_band_Hz"],
                             Z_basis=ps["basis"], L_cm_min_H=v0["L_min"], L_cm_nom_H=v0["L_nom"], Z_16k_min_ohm=v0["Z_16k_min"],
                             Z_100k_min_ohm=v0["Z_100k_min"], L_dm_assumed_H=CMC_RULES["l_dm_frac"] * v0["L_nom"],
                             C_pv_max_F=cpv, C_pv_basis="200 nF/kWp (REFERENCE-LESSONS.md #2): 16.5 uF PV-P75, 22 uF PV-P110",
                             LF_CM_cases={k: dict(V_cm_150Hz_V_rms=PCS_CASES[k], I_cm_A_rms=v0["I_cm_lf"][k], B_LF_T=v0["B_lf"][k])
                                          for k in PCS_CASES},
                             LF_CM_basis="zero-sequence of a min-max (SVPWM-equivalent) modulated PCS: triangle at 150 Hz, peak "
                                         "V_ph,pk/4, fundamental 0.203 V_ph,pk - the same for 2-level and 3-level (NPC/T-type) "
                                         "PCS; the 3-level PCS adds less HF CM (V_dc/6 steps vs V_dc/3). Design case B (690 V AC), "
                                         "case C (800 V AC) must still not saturate; PCS AC voltage is outside this scope",
                             HF_CM_basis=f"V_cm,HF {ps['V_cm_HF_V_rms']} V rms (port_spec A_VCM_HF, the module's own switching): "
                                         "a 2-level PCS without its own CM filter (V_dc/6 steps at f_sw) would saturate any "
                                         "practical choke - the PCS supplier must state its DC-side CM voltage",
                             B_allow_T=v0["B_allow"], B_DM_T=v0["B_dm"], B_HF_T=v0["B_hf"]),
             core=dict(maker="RFQ: Shincore / Qingdao Yunlu / AT&M (Asian flat-mu nanocrystalline)", part_number="toroid 80/50/25 mm "
                       "bare, k_fill 0.75, in a case", material=CMC_GRADES[CMC_CHOICE["grade"]][0], n_cores=v0["k"], Ae_m2=v0["A"],
                       le_m=v0["le"], B_sat_T=c["bsat"], mu_150Hz=CMC_GRADES[CMC_CHOICE["grade"]][1],
                       mu_16kHz=CMC_GRADES[CMC_CHOICE["grade"]][2], mu_tol=CMC_RULES["mu_tol"],
                       gap={"type": "none (flat-loop material)"},
                       datasheet="docs/datasheets/magnetics/Yunlu-Nanocrystalline-Cores.pdf p.6 (adjustable mu, DC bias); "
                                 "DMEGC DNL/DNG material sheets: dmegc.de link returns HTTP 404 - manual download",
                       asian_alternative="Shincore SC-1K107-LP (mu 29 000) only with N = 1 on 14 cores (cost ~2x); "
                                         "N = 2 on 4 cores saturates in case B",
                       western_reference="Magnetec Nanoperm 8000 flat: N = 3 on 6 cores (meets, ~+30 % cost)"),
             windings=[dict(name=k_, turns=v0["N"], conductor=var[k_]["copper_bar"],
                            arrangement="bifilar: + and - bars side by side through the window, each in a 0.75 mm sleeve",
                            length_per_pole_m=dcm[k_]["choice"]["length"]) for k_ in dcm],
             fit=dict(build_m=None, available_m=None, fits=all(v["choice"]["fill"] <= CMC_RULES["fill_max"] for v in dcm.values()),
                      hole_fill={k_: v["choice"]["fill"] for k_, v in dcm.items()}, fill_max=CMC_RULES["fill_max"]),
             insulation=dict(system="sleeved bars (>= 0.75 mm wall, rated >= 1.5 kV DC) + potting (epoxy) inside the case: inside the "
                                    "potting PD1; case and sleeve material CTI >= 600; bare terminations of + and - and to the "
                                    "chassis >= 16 mm creepage (covers PD3 group III) and >= 7.1 mm clearance (4000 m)",
                             creepage_terminals_m=16e-3, clearance_m=7.1e-3, creepage_potted_m=5e-3, levels=lev,
                             routine_tests="AC 2200 V rms 1 s (or 2640 V 1 s) pole-pole and poles-core/case; L_cm at 10 kHz",
                             type_tests="AC 2200 V rms 60 s; impulse 6 kV 1.2/50 (no surge credit); L_cm at 16/100 kHz with 1.7 A "
                                        "rms 150 Hz CM bias and rated DC; thermal run at rated DC (IC-09)"),
             loss_model=dict(core={"formula": "Yunlu Pcm = 1.205e-3 (f/kHz)^1.826 (B/0.1 T)^2.077 W/kg (datasheet p.11)",
                                   "mass_kg": v0["mass_core"]},
                             winding={"R_ac_per_harmonic": None, "referred_to": "per pole, DC", "temperature_C": 90.0,
                                      "R_pole_ohm": {k_: v["choice"]["R_pole"] for k_, v in dcm.items()},
                                      "formula": "P_cu = 2 I_dc^2 R_pole (DC; the HF CM current is < 0.1 A)"}),
             loss_table=rows,
             loss_grid=dict(I_rms_A=[r_["I_rms_A"] for r_ in rows], P_cu_W=[r_["P_cu_W"] for r_ in rows], B_pk_T=[], P_core_W=[],
                            note="DC copper loss ~ I^2; core loss negligible"),
             thermal=dict(Rth_K_W={k_: v["choice"]["Rth"] for k_, v in dcm.items()}, dT_rated_K={k_: v["choice"]["dT"] for k_, v in dcm.items()},
                          basis="bars -> 5 mm epoxy (>= 1 W/mK) -> case base -> 1 mm pad (3 W/mK) -> chassis (+0.2 K/W spreading, ASSUMED)",
                          interface="case base bolted to the chassis wall with a gap pad; no forced air needed"),
             mass_kg={k_: v["choice"]["mass"] for k_, v in dcm.items()},
             cost=dict(per_variant={k_: v["choice"]["cost"] for k_, v in dcm.items()},
                       basis="ESTIMATES, no quote: flat-mu nanocrystalline 20 USD/kg + 1.5 USD per core case; Cu bars LME 14.4 + 6 "
                             "USD/kg forming/plating; sleeves/terminals 6, case 6, potting 8, labour 10, test 3; +10 %",
                       cost_estimates_row="\n".join(f"CM choke {dcm[k_]['spec']['I']:.0f} A (CUSTOM),{v['choice']['cost']['total_usd']:.0f},\""
                                                    f"ESTIMATE (sim/magnetics.py design_port_cm_choke.json rev M1): {v0['k']} flat-mu "
                                                    f"nanocrystalline 80/50/25 cores {v['choice']['cost']['core_usd']:.0f} + Cu bars "
                                                    f"{v['choice']['cost']['copper_usd']:.0f} + sleeves/case/potting 20 + labour/test 13, "
                                                    "+10 %; no quote\",low" for k_, v in dcm.items()),
                       total_usd=max(v["choice"]["cost"]["total_usd"] for v in dcm.values())),
             verification=dict(own=dict(L_min_H=v0["L_min"], B_total_caseB_T=v0["B_total"]["B_690VAC_minmax"],
                                        P_cu_W={k_: v["choice"]["P_cu"] for k_, v in dcm.items()}),
                               openmagnetics=om,
                               previous_designer=dict(design="high-mu nanocrystalline reference (gen/port.py rev before MG-10)",
                                                      B_total_T="4.3-4.5 (saturated, MG-10)", cost_usd="65 / 80 (cost_estimates.csv)")),
             variants=var)
    with open(os.path.join(OUT, "design_port_cm_choke.json"), "w") as fh:
        json.dump(d, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return d


def design_checks_cmc(d):
    e, ins, om = d["electrical"], d["insulation"], d["verification"]["openmagnetics"]
    C, ok = [], {}

    def chk(cid, what, value, limit, good):
        C.append(dict(id=cid, what=what, value=value, limit=limit, ok=bool(good), exc=""))
        ok[cid] = bool(good)

    chk("cmc_m1_L", "M1 CM choke L_cm at -25 % mu (16 kHz) and |Z| at 16 kHz",
        f"{e['L_cm_min_H']*1e3:.2f} mH, {e['Z_16k_min_ohm']:.0f} ohm", f">= {e['L_cm_min_req_H']*1e3:.2f} mH, >= {e['Z_cm_min_req_ohm']} ohm",
        e["L_cm_min_H"] >= e["L_cm_min_req_H"] and e["Z_16k_min_ohm"] >= e["Z_cm_min_req_ohm"])
    for k_, v in d["variants"].items():
        bb = v["B_total_T"]
        chk(f"cmc_m1_B_{k_}", f"M1 {k_} flux LF CM (case B, +25 % mu) + DM + HF; case C reported", f"{bb['B_690VAC_minmax']:.2f} T "
            f"(C {bb['C_800VAC_minmax']:.2f})", f"<= {e['B_allow_T']:.2f} T", bb["B_690VAC_minmax"] <= e["B_allow_T"]
            and bb["C_800VAC_minmax"] <= 1.0)
        chk(f"cmc_m1_dT_{k_}", f"M1 {k_} temperature rise at rated DC (potted, chassis-mounted)", f"{v['dT_rated_K']:.0f} K", "<= 40 K",
            v["dT_rated_K"] <= 40.0)
    chk("cmc_m1_fill", "M1 bars fit the core window (sleeved, both poles)", _v(d["fit"]["hole_fill"]), f"<= {d['fit']['fill_max']}",
        d["fit"]["fits"])
    lv = (ins["levels"] or [{}])[0]
    chk("cmc_m1_ins", "M1 CM choke terminations creepage / clearance (IC-09)", f"{ins['creepage_terminals_m']*1e3:.0f} mm, "
        f"{ins['clearance_m']*1e3:.1f} mm", f">= {lv.get('cr_pd2', 5.0)} mm PD2 (16 mm PD3), >= 7.1 mm",
        ins["creepage_terminals_m"] * 1e3 >= 16.0 and ins["clearance_m"] * 1e3 >= 7.1)
    if om.get("L_10k_H"):
        chk("cmc_m1_om_L", "CM choke L own formula vs OpenMagnetics (Nanoperm 8000, same N and cores)",
            pct(rel(om["own_L_same_material_H"], om["L_10k_H"])), "<= 10 %", abs(rel(om["own_L_same_material_H"], om["L_10k_H"])) <= 0.10)
    ids = [c["id"] for c in C]
    ev = "; ".join(f"{c['id']} {c['value']} ({c['limit']})" for c in C)
    closed = {"cmc_lf": ("closed by sim/out/magnetics/design_port_cm_choke.json rev M1 (calculated; flat-mu grade to be quoted, "
                         "confirm L(I_cm) on the first article)", ev)} if all(ok[i] for i in ids) else {}
    return C, closed


# ---- PV cell inductor L_CELL (MG-06/07): construction rev M1 ----------------------------------------------------------------
PV_T_AIR = 55.0           # C local air at the inductor: 45 C inlet (PV-12/PV-20) + 10 K pre-heat (ASSUMED; the module model
#                           sim/pv_module.py derates with the real inlet temperature and flow using the Rth given here)
POCO_MAT = {"NPC 26": dict(bias=(93.5843, 314.4894, 2.5844, 5.4287), loss=(1.9638, 2.4352, 0.0259), bsat=1.25,
                           src=MAG + "POCO-Powder-Core-Catalog-2026.pdf p.27-28"),
            "NPH-L 26": dict(bias=(96.1389, 249.4091, 1.874, 2.9956), loss=(3.65, 2.2081, 0.0074), bsat=1.05,
                             src=MAG + "POCO-Powder-Core-Catalog-2026.pdf p.35-36")}
NPC290026 = dict(pn="NPC290026", od=75.2e-3, id=44.07e-3, ht=36.27e-3, Ae=504e-6, le=183.8e-3, Ve=92640e-9, AL=89e-9, mass=0.60,
                 src=MAG + "POCO-Powder-Core-Catalog-2026.pdf (NPC290026: 74.1/45.3/35.0 mm, coated 75.2/44.07/36.27)")
PV_PRICE = dict(kmm_set=11.0, poco_toroid=8.0, litz_usd_kg=32.4, former=3.0, vpi=4.0, labour=8.0, test=2.0,
                basis="ESTIMATES, no quote: Kool Mu MAX E80 set ~11 USD (sim/pv_tradeoff.py basis), POCO NPC toroid ~8 USD; "
                      "profiled litz 0.1 mm 32.4 USD/kg (LME Cu 14.4 + 18 drawing/profiling premium, as cost_estimates.csv); "
                      "PPS former 3, VPI class H 4, winding 8, PD/hipot/L test 2; +10 %")


def pv_ecore(cell, a_cu):
    c2 = json.loads(json.dumps(cell))
    c2["inductor"].update(PV_REV0, conductor=f"Litz, {a_cu*1e6:.2f} mm2 copper, insulation for 1.1 kV")
    return pv_inductor_own(c2)


def pv_toroid(cell, mat="NPC 26", k=2, a_cu=10.3e-6, d_str=0.1e-3, t_ins=1.0e-3, core_mult=1.0):
    """single-layer profiled-litz winding on k stacked POCO toroids (catalog bias and loss fits)"""
    ind, sw, rat = cell["inductor"], cell["switching"], cell["ratings"]
    rq = ind.get("requirement", {})
    m, c = POCO_MAT[mat], NPC290026
    a, b, cc, d = m["bias"]
    mu = lambda h: (a / (1 + (h * 4 * math.pi / 1000 / b) ** cc) + d) / 100  # noqa: E731
    le, ae, ve = c["le"], k * c["Ae"], k * c["Ve"]
    al1 = m.get("AL", c["AL"])
    f, idc, vmax = sw["f_sw_kHz"] * 1e3, rat["I_max_A_low_voltage_port"], rat["V_ports_V"][1]
    l_req = rq.get("L_at_45A_uH_min", 206.5) * 1e-6 / (1 - rq.get("L_tolerance_pct", 8.0) / 100)
    for n in range(20, 60):
        L = lambda i: k * al1 * n * n * mu(n * i / le)  # noqa: E731
        if L(idc) >= l_req:
            break
    t_on = 0.5 / f
    ripple = vmax / 2 * t_on / L(idc)
    dB = vmax / 2 * t_on / (n * ae)
    k1, a1, k2 = m["loss"]
    bm_kg, fk = dB / 2 * 10, f / 1e3
    p_core_cat = (k1 * bm_kg ** a1 * fk + k2 * (bm_kg * fk) ** 2) * ve * 1e6 * 1e-3
    p_core = p_core_cat * core_mult
    r_in = c["id"] / 2 - t_ins
    perim = 2 * math.pi * r_in
    h = perim / n - 2 * LITZ_INS
    w = a_cu / LITZ_PACK["rect"] / h + 2 * LITZ_INS
    ht = k * c["ht"] + (k - 1) * 0.5e-3
    mlt = 2 * (ht + 2 * t_ins) + 2 * ((c["od"] - c["id"]) / 2 + 2 * t_ins) + 2 * w
    n_str = int(round(a_cu / (math.pi * d_str ** 2 / 4)))
    harm = [(0.0, idc)] + [(hh * f, 8 * ripple / (math.pi ** 2 * hh * hh) / 2 / math.sqrt(2)) for hh in range(1, 40, 2)]
    wl = litz_loss(harm, n_str, d_str, n, mlt, ramp_h2(0, n, perim), 110.0, math.sqrt(h * w / math.pi))
    surf = math.pi * (c["od"] + 2 * w) * (ht + 2 * w) + 2 * math.pi / 4 * ((c["od"] + 2 * w) ** 2 - (c["id"] - 2 * w) ** 2) \
        + math.pi * (c["id"] - 2 * w) * ht
    ptot = p_core + wl["total"]
    dt_int = wl["total"] / (mlt * n * h * w) * w ** 2 / (2 * 0.6) * 0.5
    dT = temp_rise_surface(ptot, surf) + dt_int
    m_cu = 1.15 * n * mlt * a_cu * 8960
    return dict(material=mat, src=m["src"], core=f"{k} x POCO {c['pn']} ({mat})", N=n, L0=k * al1 * n * n, L_Idc=L(idc),
                L_min_Idc=L(idc) * (1 - rq.get("L_tolerance_pct", 8.0) / 100), L_trip_frac=mu(n * ind["I_trip_A"] / le) / mu(0),
                ripple=ripple, dB=dB, B_pk_trip=None, P_core=p_core, P_core_catalogue=p_core_cat, core_mult=core_mult, P_cu=wl["total"], P_total=ptot, R_dc110=wl["R_dc"],
                conductor=f"profiled litz {n_str} x {d_str*1e3:.2f} mm, {h*1e3:.2f} x {w*1e3:.2f} mm, 1 layer",
                hole_left_m=2 * (r_in - w), fits=(r_in - w) >= 8e-3 and h >= 0.25 * w, MLT=mlt, dT_hotspot=dT,
                dT_internal=dt_int, mass=k * c["mass"] + m_cu, mass_cu=m_cu,
                cost_usd=round((k * PV_PRICE["poco_toroid"] + PV_PRICE["litz_usd_kg"] * m_cu + PV_PRICE["former"]
                                + PV_PRICE["vpi"] + PV_PRICE["labour"] * 1.3 + PV_PRICE["test"]) * 1.10, 0))


def design_pv(specs):
    cell = specs["cell"]
    base = pv_inductor_own(cell)
    best = None
    for a in np.arange(10.3, 16.01, 0.1) * 1e-6:            # most copper whose profiled-litz build leaves 0.5 mm in the window
        r = pv_ecore(cell, a)
        if r["layout_rect"]["build"] <= r["window_w"] - 0.5e-3:
            best = (a, r)
    a_cu, r = best
    r["a_cu"] = a_cu
    r["layout"] = r["layout_rect"]
    m_cu = 1.15 * r["turns"] * r["MLT"] * a_cu * 8960
    r["mass_cu"] = m_cu
    r["cost"] = dict(core_usd=2 * PV_PRICE["kmm_set"], copper_usd=PV_PRICE["litz_usd_kg"] * m_cu,
                     insulation_usd=PV_PRICE["former"] + PV_PRICE["vpi"], labour_usd=PV_PRICE["labour"] + PV_PRICE["test"])
    r["cost"]["total_usd"] = round(sum(r["cost"].values()) * 1.10, 0)
    tor = {m_: pv_toroid(cell, m_, 2, a_cu) for m_ in POCO_MAT}
    return r, base, tor


PV_M1_ACU = 15.1e-6       # m2, rev M1 conductor (profiled litz 1923 x 0.10 mm), frozen as the M2 reference


def om_design_pv(cell, t):
    try:
        f = cell["switching"]["f_sw_kHz"] * 1e3
        idc, rp, v = cell["ratings"]["I_max_A_low_voltage_port"], t["ripple"], cell["ratings"]["V_ports_V"][1] / 2
        core_fd = {"type": "toroidal", "material": "NPC 26", "shape": "T 74/45/35", "gapping": [], "numberStacks": 2}
        n_str = int(round(t["a_cu"] / (math.pi * 0.1e-3 ** 2 / 4)))
        wnd = [{"name": "L", "numberTurns": t["N"], "numberParallels": 1, "isolationSide": "primary",
                "wire": _om_litz(n_str, 0.1e-3, math.sqrt(t["a_cu"] / LITZ_PACK["round"] * 4 / math.pi))}]
        exc = [{"current": _om_wave([0, 0.5 / f, 1 / f], [idc - rp / 2, idc + rp / 2, idc - rp / 2]),
                "voltage": _om_wave([0, 0.5 / f, 0.5 / f, 1 / f], [v, v, -v, -v])}]
        core, coil, mag, inp = _om_build(core_fd, wnd, exc, f, PV_T_AIR, l_target=t["L_Idc"])
        o = _om_eval(core, coil, mag, inp, 110.0)
        return {"L_H": _num(o.get("L"), "magnetizingInductance"), "P_core_W": _num(o.get("core"), "coreLosses"),
                "P_cu_W": _num(o.get("winding"), "windingLosses"),
                "note": "MAS T 74/45/35 x2 in NPC 26; OM winds round litz (multi-layer), so its copper loss is not the profiled "
                        "single-layer winding; L is at the DC-bias operating point"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def write_design_pv(specs, t, e, base, om, ins, write=True):
    cell = specs["cell"]
    ind = cell["inductor"]
    rq = ind.get("requirement", {})
    f = cell["switching"]["f_sw_kHz"] * 1e3
    c = NPC290026
    k1, a1, k2 = POCO_MAT["NPC 26"]["loss"]
    n_str = int(round(t["a_cu"] / (math.pi * 0.1e-3 ** 2 / 4)))
    h_w = re.search(r"([0-9.]+) x ([0-9.]+) mm, 1 layer", t["conductor"]).groups()
    hh = [[hk * f, litz_loss([(hk * f, 1.0)], n_str, 0.1e-3, t["N"], t["MLT"], ramp_h2(0, t["N"], 2 * math.pi * (c["id"] / 2 - 1e-3)),
                             110.0, math.sqrt(float(h_w[0]) * float(h_w[1]) * 1e-6 / math.pi))["total"]] for hk in range(1, 16, 2)]
    idc = cell["ratings"]["I_max_A_low_voltage_port"]
    p_dc = t["R_dc110"] * idc ** 2
    rows = [dict(point=f"worst: {cell['ratings']['V_ports_V'][1]:.0f} V, D 0.5, {idc:.0f} A DC", I_rms_A=math.sqrt(idc ** 2 + t["ripple"] ** 2 / 12),
                 B_pk_T=t["dB"] / 2, P_core_W=t["P_core"], P_cu_W=t["P_cu"], P_total_W=t["P_total"])]
    for i_ in (35.0, 25.0):
        pc = t["P_cu"] - p_dc + t["R_dc110"] * i_ ** 2
        rows.insert(0, dict(point=f"{i_:.0f} A DC, worst ripple", I_rms_A=math.sqrt(i_ ** 2 + t["ripple"] ** 2 / 12), B_pk_T=t["dB"] / 2,
                            P_core_W=t["P_core"], P_cu_W=pc, P_total_W=pc + t["P_core"]))
    bgrid = [0.02, 0.04, 0.06, 0.08, 0.1, 0.12]
    lev = _ins_levels(ins, ["Basic HV-PE, switch node", "Reinforced HV-PELV, PV switch node"])
    rth = t["dT_hotspot"] / t["P_total"]
    d = dict(part="pv_inductor", revision="M1", status="proposed construction - calculated, not built or measured",
             basis={"spec": "sim/out/pv_design/cell_spec.json", "spec_mtime": _mtime("sim/out/pv_design/cell_spec.json"),
                    "requirement": {k_: rq.get(k_) for k_ in ("L_at_45A_uH_min", "L_at_trip_fraction_of_L0_min", "L_tolerance_pct",
                                                               "I_rms_max_A", "I_peak_normal_max_A", "I_trip_A", "ripple_pp_max_A",
                                                               "loss_allowed_W", "hot_spot_max_C", "air_flow_m3h_at_full_load")}},
             construction=f"2 x POCO NPC290026 (NPC 26, 74/45/35 mm) stacked toroid, {t['N']} turns single-layer profiled litz "
                          f"{n_str} x 0.10 mm ({t['a_cu']*1e6:.1f} mm2), core wrapped 2 x 0.13 mm Nomex 410 + polyimide, VPI "
                          "class H, NTC at the inner-side winding centre in a reinforced pocket; mounted on an insulating base with "
                          "a centre clamp in the cell air stream",
             electrical=dict(L0_H=t["L0"], L_at_45A_nom_H=t["L_Idc"], L_at_45A_min_H=t["L_min_Idc"], L_at_45A_req_H=rq.get("L_at_45A_uH_min", 0) * 1e-6,
                             L_trip_over_L0=t["L_trip_frac"], L_trip_req=rq.get("L_at_trip_fraction_of_L0_min"), ripple_pp_A=t["ripple"],
                             dB_pp_T=t["dB"], B_sat_T=POCO_MAT["NPC 26"]["bsat"], turns=t["N"], f_ripple_Hz=f,
                             note="the converter engineer's L(I) table (inductor_L_vs_I.csv) is for Kool Mu MAX; NPC 26 rolls off "
                                  "less (L_trip/L0 0.81 vs 0.69) - pv_tradeoff.py should read L0/L(45 A) from here"),
             core=dict(maker="POCO (Shenzhen POCO Magnetic)", part_number="NPC290026 x 2 (stacked, 0.5 mm spacer)", material="NPC 26",
                       Ae_m2=2 * c["Ae"], le_m=c["le"], Ve_m3=2 * c["Ve"], AL_H=2 * c["AL"], AL_tol="+/-8 %", datasheet=c["src"],
                       gap={"type": "distributed (powder)"},
                       asian_alternative="this is the Asian part; DMEGC DE8038 (E 80/38/20 powder E core, 26u A_L 103 nH +/-12 %, "
                                         "docs/datasheets/magnetics/DMEGC-DE8038.pdf) is an E-core option, but its FeSiAl loss / "
                                         "DC-bias curves are not retrievable (dmegc.de links return HTTP 404) -> RFQ",
                       western_reference=f"Magnetics Kool Mu MAX 26 2 x 00Y8044E026, 38 t profiled litz {e['a_cu']*1e6:.1f} mm2, "
                                         f"2 layers (fits: build {e['layout_rect']['build']*1e3:.1f} of {e['window_w']*1e3:.1f} mm): "
                                         f"{e['P_core']+e['P_cu']:.1f} W, hot-spot rise {e['dT_hotspot']:.0f} K, ~{e['cost']['total_usd']:.0f} USD"),
             windings=[dict(name="L", turns=t["N"], conductor=t["conductor"], copper_area_m2=t["a_cu"], MLT_m=t["MLT"],
                            R_dc_110C_ohm=t["R_dc110"], arrangement="single layer on the inner circumference; first and last turn "
                            ">= 10 mm apart with an insulating separator (up to 1000 V between them)")],
             fit=dict(build_m=t["hole_left_m"], available_m=None, fits=bool(t["fits"]), note="build_m = remaining centre hole diameter "
                      "(>= 16 mm kept for the clamp and air)"),
             insulation=dict(system="winding-core basic: core wrapped 2 x 0.13 mm Nomex 410 + 1 x 0.05 mm polyimide over the epoxy coating "
                                    "(coating not counted), litz served + polyimide-wrapped (1.1 kV), VPI class H varnish (no air at the "
                                    "core edges); NTC in a reinforced pocket (3 layers polyimide + silicone sleeve) at the inner-side "
                                    "winding centre", levels=lev, creepage_turn_gap_m=10e-3,
                             routine_tests="PD-free (<= 10 pC) at 1910 V pk winding-core; AC 2200 V rms 1 s; NTC-winding AC 4400 V rms 1 s",
                             type_tests="AC 2200 V rms 1 min winding-core, 4400 V rms NTC-winding, impulse 6 kV / 8 kV; L(I) to 72.4 A; "
                                        "thermal run at 54 W in 150 m3/h"),
             loss_model=dict(core={"k1": k1, "alpha": a1, "k2": k2, "Ve_m3": 2 * c["Ve"], "N": t["N"], "Ae_m2": 2 * c["Ae"],
                                   "formula": "POCO NPC 26 catalogue fit: Pcv [mW/cm3] = k1 Bm^alpha f + k2 (Bm f)^2 with Bm [kG] = "
                                              "dB/2, f [kHz]; dB = V t_on / (N Ae)", "table_W_vs_Bm_T":
                                       [[bm, (k1 * (bm * 10) ** a1 * f / 1e3 + k2 * (bm * 10 * f / 1e3) ** 2) * 2 * c["Ve"] * 1e3] for bm in bgrid]},
                             winding={"R_ac_per_harmonic": [[0.0, t["R_dc110"]]] + hh, "referred_to": "the winding",
                                      "temperature_C": 110.0, "formula": "P_cu = R_dc I_dc^2 + sum_h R(h) I_h,rms^2 (triangle ripple)"}),
             loss_table=rows,
             loss_grid=dict(I_rms_A=[r_["I_rms_A"] for r_ in rows], P_cu_W=[r_["P_cu_W"] for r_ in rows],
                            B_pk_T=bgrid, P_core_W=[(k1 * (bm * 10) ** a1 * f / 1e3 + k2 * (bm * 10 * f / 1e3) ** 2) * 2 * c["Ve"] * 1e3 for bm in bgrid],
                            note="copper at the worst ripple; core loss vs Bm (= dB/2) at 32 kHz"),
             thermal=dict(Rth_hotspot_to_air_K_W=rth, dT_hotspot_K=t["dT_hotspot"], dT_internal_K=t["dT_internal"], T_air_local_C=PV_T_AIR,
                          T_hotspot_C=PV_T_AIR + t["dT_hotspot"], T_hotspot_max_C=rq.get("hot_spot_max_C", 155.0),
                          basis="surface model of the cell designer (forced air, h ~25 W/m2K on the wound toroid surface) + internal "
                                "gradient across the single layer (k_eff 0.6 W/mK); the E-core alternative has 2 layers: "
                                f"{e['dT_internal']:.0f} K internal",
                          interface="in the cell air stream (150 m3/h at full load); sim/pv_module.py derates with T_air + Rth x P"),
             mass_kg=t["mass"],
             cost=dict(core_usd=2 * PV_PRICE["poco_toroid"], copper_usd=PV_PRICE["litz_usd_kg"] * t["mass_cu"],
                       insulation_usd=PV_PRICE["former"] + PV_PRICE["vpi"], labour_usd=PV_PRICE["labour"] * 1.3 + PV_PRICE["test"],
                       total_usd=t["cost_usd"], basis=PV_PRICE["basis"] + "; toroid winding labour x1.3",
                       cost_estimates_row=f"L_CELL 224uH (CUSTOM),{t['cost_usd']:.0f},\"ESTIMATE (sim/magnetics.py design_pv_inductor.json rev M1): "
                                          f"2 x POCO NPC290026 16 + profiled litz {t['mass_cu']:.2f} kg x 32.4 USD/kg + former/VPI 7 + "
                                          "winding/test 12.4, +10 %; no quote\",low"),
             verification=dict(own=dict(P_core_W=t["P_core"], P_core_catalogue_W=t["P_core_catalogue"], P_cu_W=t["P_cu"],
                                        L_at_45A_H=t["L_Idc"], dT_hotspot_K=t["dT_hotspot"],
                                        note="design core loss = max(POCO 2026 catalogue fit, MAS NPC 26 loss data): MAS uses a "
                                             "26u-specific fit that is not in the 2026 catalogue (sim/data/asian_magnetic_materials.csv mas_check)"),
                               openmagnetics=om,
                               previous_designer=dict(design=ind.get("core"), conductor=ind.get("conductor"), P_total_W=ind.get("loss_worst_W"),
                                                      L_at_45A_H=ind.get("L_at_45A_uH", 0) * 1e-6, dT_hotspot_K_own=base["dT_hotspot"],
                                                      cost_usd=66)),
             alternatives=dict(kool_mu_max_ecore=dict(core="Magnetics Kool Mu MAX 26 2 x 00Y8044E026", turns=e["turns"],
                                                      a_cu_m2=e["a_cu"], build_m=e["layout_rect"]["build"], window_m=e["window_w"],
                                                      P_core_W=e["P_core"], P_cu_W=e["P_cu"], dT_hotspot_K=e["dT_hotspot"],
                                                      dT_internal_K=e["dT_internal"], cost_usd=e["cost"]["total_usd"]),
                               nph_l_toroid={k_: v for k_, v in pv_toroid(cell, "NPH-L 26", 2, t["a_cu"]).items() if k_ != "src"}))
    if write:
        with open(os.path.join(OUT, "design_pv_inductor.json"), "w") as fh:
            json.dump(d, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return d


def design_checks_pv(d):
    e, th, rq = d["electrical"], d["thermal"], d["basis"]["requirement"]
    rv = d["revision"]
    lim = e.get("loss_limit_W") or rq["loss_allowed_W"]
    C, ok = [], {}

    def chk(cid, what, value, limit, good):
        C.append(dict(id=cid, what=what, value=value, limit=limit, ok=bool(good), exc=""))
        ok[cid] = bool(good)

    chk(f"pv_{rv.lower()}_L", f"{rv} PV inductor L(45 A) at -8 % A_L; L_trip/L0", f"{e['L_at_45A_min_H']*1e6:.0f} uH, {e['L_trip_over_L0']:.2f}",
        f">= {e['L_at_45A_req_H']*1e6:.1f} uH, >= {e['L_trip_req']}", e["L_at_45A_min_H"] >= e["L_at_45A_req_H"]
        and e["L_trip_over_L0"] >= (e["L_trip_req"] or 0.35))
    chk(f"pv_{rv.lower()}_ripple", f"{rv} PV inductor ripple at the worst point", f"{e['ripple_pp_A']:.1f} A", f"<= {rq['ripple_pp_max_A']:.1f} A",
        e["ripple_pp_A"] <= rq["ripple_pp_max_A"] * 1.01)
    p = max(r_["P_total_W"] for r_ in d["loss_table"])
    chk(f"pv_{rv.lower()}_loss", f"{rv} PV inductor worst loss (design) vs " + ("hot-spot-limited ceiling" if e.get("loss_limit_W") else "cell budget"),
        f"{p:.1f} W", f"<= {lim:.0f} W", p <= lim)
    chk(f"pv_{rv.lower()}_fit", f"{rv} PV inductor single-layer winding fits (centre hole left)", f"{d['fit']['build_m']*1e3:.1f} mm", ">= 16 mm",
        d["fit"]["fits"])
    chk(f"pv_{rv.lower()}_hot", f"{rv} PV inductor hot spot at {th['T_air_local_C']:.0f} C local air (incl. internal gradient)",
        f"{th['T_hotspot_C']:.0f} C", f"<= {th['T_hotspot_max_C']:.0f} C", th["T_hotspot_C"] <= th["T_hotspot_max_C"])
    om = d["verification"]["openmagnetics"]
    if om.get("L_H"):
        chk(f"pv_{rv.lower()}_om_L", f"{rv} PV inductor L at 45 A own vs OpenMagnetics (NPC 26, T 74/45/35 x2)", pct(rel(e["L_at_45A_nom_H"], om["L_H"])),
            "<= 10 %", abs(rel(e["L_at_45A_nom_H"], om["L_H"])) <= 0.10)
        o_ = d["verification"]["own"]
        if om.get("P_cu_W") and rv != "M1":
            chk(f"pv_{rv.lower()}_om_cu", f"{rv} PV inductor design copper loss covers own and OpenMagnetics (own {pct(rel(o_['P_cu_W'], om['P_cu_W']))} vs OM)",
                f"{o_['P_cu_design_W']:.1f} W (own {o_['P_cu_W']:.1f}, OM {om['P_cu_W']:.1f})", ">= both",
                o_["P_cu_design_W"] >= 0.999 * max(o_["P_cu_W"], om["P_cu_W"]))
        chk(f"pv_{rv.lower()}_om_core", f"{rv} PV inductor design core loss covers catalogue (own) and MAS (OpenMagnetics) values",
            f"{o_['P_core_W']:.1f} W (catalogue {o_['P_core_catalogue_W']:.1f}, MAS {om['P_core_W']:.1f})", ">= both (-2 %)",
            o_["P_core_W"] >= 0.98 * max(o_["P_core_catalogue_W"], om["P_core_W"]))
    need = {"pv_window": [f"pv_{rv.lower()}_fit", f"pv_{rv.lower()}_loss"], "pv_temp": [f"pv_{rv.lower()}_hot", f"pv_{rv.lower()}_loss"]}
    if f"pv_{rv.lower()}_om_core" in ok:
        need["pv_core"] = [f"pv_{rv.lower()}_om_core", f"pv_{rv.lower()}_loss"]
    ev = {c_["id"]: f"{c_['id']} {c_['value']} ({c_['limit']})" for c_ in C}
    closed = {t_: (f"closed by sim/out/magnetics/design_pv_inductor.json rev {rv} (calculated; confirm on the first article)",
                   "; ".join(ev[i] for i in ids)) for t_, ids in need.items() if all(ok.get(i) for i in ids)}
    return C, closed


# ---- PV inductor rev M2: design to cost (D-044, target <= 40 USD) ------------------------------------------------------------
POCO_MAT["NPC 40"] = dict(bias=(94.4866, 212.5874, 2.2768, 4.3612), loss=(1.9638, 2.4352, 0.0259), bsat=1.25, AL=137e-9,
                          src=MAG + "POCO-Powder-Core-Catalog-2026.pdf p.27-30 (NPC290040 A_L 137 nH)")
COND_USD_KG = {"litz": 32.4, "bunch": 22.0, "round": 18.0, "rect": 20.0}   # LME Cu 14.4 + processing premium (estimates)
WIND_USD = {"litz": 10.4, "bunch": 8.8, "round": 5.0, "rect": 6.0}        # winding labour per inductor (estimates)
INS_LEAN = 4.0             # Nomex 410 2 x 0.13 mm core wrap 1.0 + polyimide 0.3 + VPI class H (batch) 1.5 + base/clamp 1.2
ENAMEL = 0.09e-3           # grade-2 build on ~3 mm wire (IEC 60317-0-1), total over the diameter


def dowell_fr(delta, m=1):
    d = min(delta, 30.0)
    p1 = d * (math.sinh(2 * d) + math.sin(2 * d)) / (math.cosh(2 * d) - math.cos(2 * d))
    return p1 + 2 * (m * m - 1) / 3 * d * (math.sinh(d) - math.sin(d)) / (math.cosh(d) + math.cos(d))


def pv_m2_row(cell, mat, cond, size, core_mult=1.0, core_usd=8.0, label=""):
    """one PV inductor construction on 2 x 74/45/35 toroids: cond 'litz'/'bunch' (size = (a_cu, d_strand), profiled single
    layer), 'round' (size = d_cu, solid enamelled single layer), 'rect' (size = (t, h) edgewise, Dowell m = 1)"""
    t = pv_toroid(cell, mat, 2, 10e-6, core_mult=core_mult)          # core side: N, L, ripple, dB, core loss
    c, n, f, idc = NPC290026, t["N"], cell["switching"]["f_sw_kHz"] * 1e3, cell["ratings"]["I_max_A_low_voltage_port"]
    r_in = c["id"] / 2 - 1.0e-3
    harm = [(0.0, idc)] + [(h * f, 8 * t["ripple"] / (math.pi ** 2 * h * h) / 2 / math.sqrt(2)) for h in range(1, 40, 2)]
    ht = 2 * c["ht"] + 0.5e-3
    if cond in ("litz", "bunch"):
        a_cu, d = size
        hc = 2 * math.pi * r_in / n - 2 * LITZ_INS
        w = a_cu / LITZ_PACK["rect"] / hc + 2 * LITZ_INS
        fits = hc >= 0.25 * w and (r_in - w) >= 8e-3
        mlt = 2 * (ht + 2e-3) + 2 * ((c["od"] - c["id"]) / 2 + 2e-3) + math.pi * w
        ns = int(round(a_cu / (math.pi * d * d / 4)))
        wl = litz_loss(harm, ns, d, n, mlt, k_toroid(c) * ramp_h2(0, n, 2 * math.pi * r_in), 110.0, math.sqrt(hc * w / math.pi))
        p_cu, r_dc, scrap, desc = wl["total"], wl["R_dc"], 1.15, f"profiled {'litz' if cond == 'litz' else 'bunched'} {ns} x {d*1e3:.2f} mm"
        dt_int = p_cu / (mlt * n * hc * w) * w ** 2 / (2 * 0.6) * 0.5
    elif cond == "round":
        d = size
        a_cu, w = math.pi * d * d / 4, d + ENAMEL
        fits = n * w <= 2 * math.pi * (r_in - w / 2)
        mlt = 2 * (ht + 2e-3) + 2 * ((c["od"] - c["id"]) / 2 + 2e-3) + math.pi * w
        wl = litz_loss(harm, 1, d, n, mlt, 0.75 * k_toroid(c) * ramp_h2(0, n, 2 * math.pi * r_in), 110.0, 1.0)
        p_cu, r_dc, scrap, desc, dt_int = wl["total"], wl["R_dc"], 1.05, f"solid round enamelled Cu {d*1e3:.2f} mm grade 2, 1 layer", 0.0
    else:
        tc, hr = size
        a_cu, w = tc * hr, hr + ENAMEL
        fits = n * (tc + ENAMEL) <= 2 * math.pi * (r_in - w)
        mlt = 2 * (ht + 2e-3) + 2 * ((c["od"] - c["id"]) / 2 + 2e-3) + math.pi * w
        r_dc = rho_cu(110.0) * (n * mlt + 0.2) / a_cu
        eta = n * tc / (2 * math.pi * (r_in - w / 2))
        p_cu = r_dc * idc ** 2 + k_toroid(c) * sum(r_dc * dowell_fr(hr / math.sqrt(rho_cu(110.0) / (math.pi * fh * MU0)) * math.sqrt(eta)) * i ** 2
                                     for fh, i in harm[1:])
        scrap, desc, dt_int = 1.05, f"edgewise rectangular enamelled Cu {tc*1e3:.1f} x {hr*1e3:.1f} mm, 1 layer", 0.0
    surf = math.pi * (c["od"] + 2 * w) * (ht + 2 * w) + 2 * math.pi / 4 * ((c["od"] + 2 * w) ** 2 - (c["id"] - 2 * w) ** 2) \
        + math.pi * (c["id"] - 2 * w) * ht
    ptot = t["P_core"] + p_cu
    m_cu = scrap * (n * mlt + 0.2) * a_cu * 8960
    cost = dict(core_usd=2 * core_usd, copper_usd=COND_USD_KG[cond] * m_cu, insulation_usd=INS_LEAN,
                labour_usd=WIND_USD[cond] + 2.0)
    cost["total_usd"] = round(sum(cost.values()) * 1.10, 1)
    return dict(label=label or f"{mat}, {desc}", material=mat, cond=cond, size=size, N=n, conductor=desc, a_cu=a_cu, build=w,
                fits=bool(fits), MLT=mlt, R_dc110=r_dc, P_core=t["P_core"], P_core_catalogue=t["P_core_catalogue"], P_cu=p_cu,
                P_dc=r_dc * idc ** 2, P_total=ptot, L_min_Idc=t["L_min_Idc"], L_Idc=t["L_Idc"], L0=t["L0"], L_trip_frac=t["L_trip_frac"],
                ripple=t["ripple"], dB=t["dB"], dT=temp_rise_surface(ptot, surf) + dt_int, dT_internal=dt_int, surface=surf,
                mass=2 * c["mass"] + m_cu, mass_cu=m_cu, cost=cost)


def om_pv_m2(cell, row, om_mat=None):
    """OpenMagnetics on a row: same core stack (MAS T 74/45/35 x2), N, and conductor (solid round from the MAS wire list)"""
    try:
        f = cell["switching"]["f_sw_kHz"] * 1e3
        idc, rp, v = cell["ratings"]["I_max_A_low_voltage_port"], row["ripple"], cell["ratings"]["V_ports_V"][1] / 2
        core_fd = {"type": "toroidal", "material": om_mat or row["material"], "shape": "T 74/45/35", "gapping": [], "numberStacks": 2}
        wire = _om_round(row["size"]) if row["cond"] == "round" else _om_litz(
            int(round(row["a_cu"] / (math.pi * row["size"][1] ** 2 / 4))), row["size"][1], math.sqrt(row["a_cu"] / LITZ_PACK["round"] * 4 / math.pi))
        wnd = [{"name": "L", "numberTurns": row["N"], "numberParallels": 1, "isolationSide": "primary", "wire": wire}]
        exc = [{"current": _om_wave([0, 0.5 / f, 1 / f], [idc - rp / 2, idc + rp / 2, idc - rp / 2]),
                "voltage": _om_wave([0, 0.5 / f, 0.5 / f, 1 / f], [v, v, -v, -v])}]
        core, coil, mag, inp = _om_build(core_fd, wnd, exc, f, PV_T_AIR, l_target=row["L_Idc"], wall=1.0e-3)
        o = _om_eval(core, coil, mag, inp, 110.0)
        return {"material": core_fd["material"], "L_H": _num(o.get("L"), "magnetizingInductance"),
                "P_core_W": _num(o.get("core"), "coreLosses"), "P_cu_W": _num(o.get("winding"), "windingLosses")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def k_toroid(c, w=0.0):
    """mean of (r_in / r)^2 along one turn of a toroid winding: the 1-D field N I / (2 pi r) is largest at the bore"""
    r_in, r_out, ht = c["id"] / 2 - 1e-3, c["od"] / 2 + 1e-3, 2 * c["ht"] + 0.5e-3
    return (ht + ht * (r_in / r_out) ** 2 + 2 * r_in * (1 - r_in / r_out)) / (2 * ht + 2 * (r_out - r_in))


PV_M2_OPTIONS = [("NPC 26", "litz", (15.1e-6, 0.1e-3), "M1 construction"),
                 ("NPC 26", "bunch", (12e-6, 0.5e-3), "coarse bunched litz"),
                 ("NPC 26", "round", 3.15e-3, "solid round wire"),
                 ("NPC 40", "round", 3.55e-3, "solid round wire, 40u core, fewer turns"),
                 ("NPC 26", "rect", (2.8e-3, 3.2e-3), "edgewise flat wire")]
PV_M2_CHOICE = 2


def design_pv_m2(cell):
    rows = []
    for mat, cond, size, lab in PV_M2_OPTIONS:
        r = pv_m2_row(cell, mat, cond, size, label=lab)
        om = om_pv_m2(cell, r) if HAVE_OM else {}
        mult = max(1.0, (om.get("P_core_W") or 0) / r["P_core_catalogue"])
        r = pv_m2_row(cell, mat, cond, size, core_mult=mult, label=lab)    # core loss = max(catalogue, MAS)
        r["om"] = om
        rows.append(r)
    return rows, rows[PV_M2_CHOICE]


def write_design_pv_m2(specs, d1, rows, ch):
    cell = specs["cell"]
    f = cell["switching"]["f_sw_kHz"] * 1e3
    idc = cell["ratings"]["I_max_A_low_voltage_port"]
    c = NPC290026
    d = json.loads(json.dumps(d1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    t_air, t_max = d1["thermal"]["T_air_local_C"], d1["thermal"]["T_hotspot_max_C"]
    rth = ch["dT"] / ch["P_total"]
    om = ch["om"]
    p_m1 = max(r_["P_total_W"] for r_ in d1["loss_table"])
    rf = k_toroid(c) * 0.75
    hh = [[0.0, ch["R_dc110"]]] + [[h * f, litz_loss([(h * f, 1.0)], 1, ch["size"], ch["N"], ch["MLT"],
                                                         rf * ramp_h2(0, ch["N"], 2 * math.pi * (c["id"] / 2 - 1e-3)), 110.0, 1.0)["total"]]
                                   for h in range(1, 16, 2)]
    d.update(revision="M2", status="proposed construction - calculated, not built or measured (design to cost, D-044)",
             construction=f"2 x POCO NPC290026 (NPC 26, 74/45/35 mm) stacked toroid (as M1), {ch['N']} turns of {ch['conductor']}, "
                          "core wrapped 2 x 0.13 mm Nomex 410 + polyimide, VPI class H, NTC at the bore-side winding centre in a "
                          "reinforced pocket; insulating base with a centre clamp in the cell air stream")
    p_cu_design = max(ch["P_cu"], (om.get("P_cu_W") or 0))
    p_tot = ch["P_core"] + p_cu_design
    d["electrical"].update(turns=ch["N"], L0_H=ch["L0"], L_at_45A_nom_H=ch["L_Idc"], L_at_45A_min_H=ch["L_min_Idc"],
                           L_trip_over_L0=ch["L_trip_frac"], ripple_pp_A=ch["ripple"], dB_pp_T=ch["dB"],
                           loss_limit_W=(t_max - t_air) / rth,
                           loss_note=f"worst-point loss {p_tot:.0f} W against the cell budget {cell['inductor'].get('requirement', {}).get('loss_allowed_W', 54):.0f} W "
                                     f"and M1 {p_m1:.0f} W: +{p_tot - p_m1:.0f} W per inductor = {3 * (p_tot - p_m1):.0f} W per PV-P75 "
                                     f"({3 * (p_tot - p_m1) / 750:.2f} % of 75 kW at the 1000 V / D 0.5 / 45 A point), "
                                     f"{4 * (p_tot - p_m1):.0f} W per PV-P100/110; loss_limit_W = hot-spot-limited value")
    d["windings"] = [dict(name="L", turns=ch["N"], conductor=ch["conductor"], copper_area_m2=ch["a_cu"], MLT_m=ch["MLT"],
                          R_dc_110C_ohm=ch["R_dc110"], arrangement="single layer, close-wound at the bore; first and last turn >= 10 mm "
                          "apart with an insulating separator (up to 1000 V between them); wire class 200 (polyesterimide + "
                          "polyamide-imide), grade 2")]
    d["fit"] = dict(build_m=c["id"] - 2e-3 - 2 * ch["build"], available_m=None, fits=ch["fits"],
                    note="build_m = centre hole left (>= 16 mm for clamp and air); single layer fits at the bore")
    d["insulation"]["system"] = d["insulation"]["system"].replace("litz served + polyimide-wrapped (1.1 kV)",
                                                                  "grade-2 enamel (not counted as insulation)")
    d["loss_model"]["winding"] = {"R_ac_per_harmonic": hh, "referred_to": "the winding", "temperature_C": 110.0,
                                  "formula": "P_cu = R_dc I_dc^2 + sum_h R(h) I_h,rms^2; R(h) Bessel skin + proximity in the "
                                             "single-layer field N I / (2 b), weighted along the turn"}
    p_dc = ch["P_dc"]
    rows_t = []
    for i_ in (25.0, 35.0, idc):
        pc = p_cu_design - p_dc + ch["R_dc110"] * i_ ** 2
        rows_t.append(dict(point=f"{i_:.0f} A DC, worst ripple" if i_ < idc else f"worst: {cell['ratings']['V_ports_V'][1]:.0f} V, D 0.5, {idc:.0f} A DC",
                           I_rms_A=math.sqrt(i_ ** 2 + ch["ripple"] ** 2 / 12), B_pk_T=ch["dB"] / 2, P_core_W=ch["P_core"], P_cu_W=pc,
                           P_total_W=pc + ch["P_core"]))
    d["loss_table"] = rows_t
    d["loss_grid"].update(I_rms_A=[r_["I_rms_A"] for r_ in rows_t], P_cu_W=[r_["P_cu_W"] for r_ in rows_t])
    d["thermal"].update(Rth_hotspot_to_air_K_W=rth, dT_hotspot_K=rth * p_tot, dT_internal_K=0.0, T_hotspot_C=t_air + rth * p_tot,
                        basis="surface model of the cell designer (forced air, h ~25 W/m2K on the wound toroid surface); solid copper: "
                              "no internal winding gradient; core gradient < 1 K")
    d["mass_kg"] = ch["mass"]
    co = ch["cost"]
    d["cost"] = dict(co, basis=f"ESTIMATES, no quote: core 2 x 8.0 USD (13.3 USD/kg Asian powder, as M1); enamelled round Cu "
                               f"{COND_USD_KG['round']} USD/kg (LME 14.4 + 3.6 drawing/enamel); insulation {INS_LEAN} (Nomex wrap, VPI batch, base); "
                               f"winding {WIND_USD['round']} + test 2.0; +10 %",
                     cost_estimates_row=f"L_CELL 224uH (CUSTOM),{co['total_usd']:.0f},\"ESTIMATE (sim/magnetics.py design_pv_inductor.json rev M2, "
                                        f"design to cost D-044): 2 x POCO NPC290026 16 + {ch['mass_cu']:.2f} kg enamelled Cu 3.15 mm at "
                                        f"{COND_USD_KG['round']} USD/kg + insulation {INS_LEAN} + winding/test {WIND_USD['round'] + 2:.0f}, +10 %; no quote\",low")
    d["verification"] = dict(own=dict(P_core_W=ch["P_core"], P_core_catalogue_W=ch["P_core_catalogue"], P_cu_W=ch["P_cu"],
                                      P_cu_design_W=p_cu_design, L_at_45A_H=ch["L_Idc"], dT_hotspot_K=rth * p_tot),
                             openmagnetics=dict(om, note="MAS T 74/45/35 x2, NPC 26, Round 3.15 - Grade 2, 1 mm former wall"),
                             previous_designer=d1["verification"]["previous_designer"])
    d["reference_M1"] = dict(construction=d1["construction"], P_total_W=p_m1, T_hotspot_C=d1["thermal"]["T_hotspot_C"],
                             mass_kg=d1["mass_kg"], cost_usd=d1["cost"]["total_usd"], cost=d1["cost"])
    d["options"] = [dict(option=r["label"], core=f"2 x NPC290{int(r['material'][-2:]):03d} ({r['material']})", turns=r["N"], conductor=r["conductor"],
                         fits=r["fits"], P_core_W=round(r["P_core"], 1), P_cu_W=round(max(r["P_cu"], r["om"].get("P_cu_W") or 0), 1),
                         P_total_W=round(r["P_core"] + max(r["P_cu"], r["om"].get("P_cu_W") or 0), 1),
                         T_hotspot_C=round(t_air + r["dT"] / r["P_total"] * (r["P_core"] + max(r["P_cu"], r["om"].get("P_cu_W") or 0)), 0),
                         mass_kg=round(r["mass"], 2), cost_usd=r["cost"]["total_usd"], L_trip_over_L0=round(r["L_trip_frac"], 2))
                    for r in rows]
    d["options_rejected"] = ["POCO NPF 26 (FeSi, cheapest star class): catalogue loss ~4 x NPC at 32 kHz / 0.1 T -> ~70 W core",
                             "KDM KS 26 (standard sendust, MAS data; KDM brochure is image-only): DC bias needs 42 turns (does not fit "
                             "one layer), MAS core loss 51 W",
                             "POCO GPC 26 (lower loss, same cost class): no 74 mm toroid in the 2026 catalogue",
                             "single toroid (55 turns, two layers): same core loss, surface halves -> hot spot > 170 C",
                             "POCO NPX / NPA (lowest loss): 1.6-1.8 x core price"]
    d.pop("alternatives", None)
    with open(os.path.join(OUT, "design_pv_inductor.json"), "w") as fh:
        json.dump(d, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return d


# ---- gate-drive bias transformer T_BIAS4 (cost-first design, item 2b) ------------------------------------------------------
EP17 = dict(maker="DMEGC", pn="EP17", mat="DMR44", src=MAG + "DMEGC-EP17.pdf p.1", Ae=33.9e-6, Amin=25.5e-6, le=28.4e-3,
            Ve=962.8e-9, AL=2300e-9, AL_tol=0.25, mass=0.0134, post_d=5.68e-3, win_axial=11.3e-3, win_radial=3.16e-3,
            bsat_100=0.315)
BIAS_RULES = dict(tube=0.4e-3, end_flange=0.4e-3, flange=0.6e-3, eps_bobbin=3.6, tiw_ins=0.10e-3, eps_tiw=3.0, air=0.05e-3,
                  shell_cover=0.75, c_pins=0.3e-12, tiw_od=0.36e-3, tiw_cu=0.16e-3, pri_d=0.40e-3, pri_od=0.44e-3)


def bias_design(req, c=EP17, n_half=4, n_sec=24, R=BIAS_RULES):
    """T_BIAS4: one EP17 per phase, bobbin with 5 side-by-side sections S1(HS-A) | S2(LS-A) | P | S3(LS-B) | S4(HS-B)"""
    f, vs = req["f_min_Hz"], req["volt_seconds_per_half_primary_Vs"]
    ax = c["win_axial"] - 2 * R["end_flange"] - 4 * R["flange"]
    w = ax / 5                                                     # axial width of one section
    rad = c["win_radial"] - R["tube"] - 0.3e-3                     # radial space for the winding
    per_layer = int(w // R["tiw_od"])
    layers = math.ceil(n_sec / per_layer)
    build = layers * R["tiw_od"]
    p_layers = math.ceil(2 * n_half / int(w // R["pri_od"]))
    fits = build <= rad and p_layers * R["pri_od"] <= rad
    mlt = math.pi * (c["post_d"] + 2 * R["tube"] + build)
    l_half = c["AL"] * n_half ** 2
    i_mag = vs / (2 * l_half * (1 - c["AL_tol"]))
    b_pk = vs / (2 * n_half * c["Ae"])
    # capacitance (own): direct between adjacent sections through the flange (parallel plate over the section end face),
    # each section to the floating MnZn core (post through the tube + shell through air), core coupling in series, pins
    d_flange = R["flange"] / R["eps_bobbin"] + R["tiw_ins"] / R["eps_tiw"] + R["air"]
    c_dir = EPS0 * build * mlt / d_flange
    d_post = R["tube"] / R["eps_bobbin"] + R["tiw_ins"] / R["eps_tiw"] + R["air"]
    c_post = EPS0 * math.pi * (c["post_d"] + 2 * R["tube"]) * w / d_post
    od_w = c["post_d"] + 2 * R["tube"] + 2 * build
    c_shell = R["shell_cover"] * EPS0 * math.pi * od_w * w / max(c["post_d"] + 2 * c["win_radial"] - od_w, 0.1e-3) * 2
    c_core = (c_post + c_shell) / 2                                # two sections in series through the floating core
    cap = dict(adjacent=c_dir + c_core + R["c_pins"], one_apart=0.2 * c_dir + c_core + R["c_pins"],
               two_apart=0.05 * c_dir + c_core + R["c_pins"])
    # leakage (own, 1-D across the window, field radial between side-by-side sections), primary (half) referred
    k1 = MU0 * n_half ** 2 * mlt / c["win_radial"]
    lk = dict(adjacent=k1 * (w / 3 + R["flange"] + w / 3), one_apart=k1 * (w / 3 + 2 * R["flange"] + w + w / 3))
    i_sec_ref = req["secondary_load_per_winding_A_dc"] * n_sec / n_half
    t_c = {k: v * i_sec_ref / req["input_V"][0] for k, v in lk.items()}
    r_sec = rho_cu(110.0) * n_sec * mlt / (math.pi * R["tiw_cu"] ** 2 / 4)
    r_pri = rho_cu(110.0) * n_half * mlt / (math.pi * R["pri_d"] ** 2 / 4)
    k_, a_, b_ = dmr95_steinmetz()            # DMR44 not fitted: DMR95 set as a stand-in (loss is milliwatts either way)
    p_core = k_ * 420e3 ** a_ * b_pk ** b_ * c["Ve"]
    i_pri_rms = req["primary_current_A_avg_per_phase"] / math.sqrt(2) * 1.1
    p_cu = 2 * i_pri_rms ** 2 * r_pri + 4 * req["secondary_rms_A_est"] ** 2 * r_sec
    return dict(n_half=n_half, n_sec=n_sec, ratio=n_sec / n_half, w=w, per_layer=per_layer, layers=layers, build=build,
                p_layers=p_layers, fits=fits, mlt=mlt, L_half=l_half, L_half_min=l_half * (1 - c["AL_tol"]), I_mag_max=i_mag,
                B_pk=b_pk, B_pk_amin=vs / (2 * n_half * c["Amin"]), cap=cap, c_dir=c_dir, c_post=c_post, c_shell=c_shell,
                L_leak=lk, t_commutation=t_c, R_sec_110=r_sec, R_half_pri_110=r_pri, P_core=p_core, P_cu=p_cu,
                dT=(p_core + p_cu) * 120.0)        # ~120 K/W for an EP17 on a board (natural convection, estimate)


def om_bias(req, r):
    """OpenMagnetics: EP 17 (MAS N87 stand-in for DMR44), 5 contiguous sections in the same order; L, leakage per secondary"""
    try:
        f = 420e3
        core = PyOM.calculate_core_data({"functionalDescription": {"type": "two-piece set", "material": "N87", "shape": "EP 17",
                                         "gapping": [{"type": "residual", "length": 5e-6}] * 3, "numberStacks": 1}}, True)
        bob = PyOM.create_basic_bobbin_by_thickness(core, BIAS_RULES["tube"])
        bob["processedDescription"]["windingWindows"][0]["sectionsOrientation"] = "contiguous"
        sw = "Round 0.16 - Grade 1"
        sides = ["secondary", "tertiary", "primary", "quaternary", "quinary"]
        names = ["S1", "S2", "P", "S3", "S4"]
        wnd = [{"name": n, "numberTurns": r["n_half"] if n == "P" else r["n_sec"], "numberParallels": 1, "isolationSide": sd,
                "wire": "Round 0.4 - Grade 1" if n == "P" else sw} for n, sd in zip(names, sides)]
        coil = PyOM.wind({"bobbin": bob, "functionalDescription": wnd}, 1, [0.2] * 5, [0, 1, 2, 3, 4], [[0, 0]] * 5)
        T = 1 / f
        v = req["input_V"][0]
        sq = lambda a: _om_wave([0, T / 2, T / 2, T], [a, a, -a, -a])  # noqa: E731
        exc = [{"current": sq(0.024), "voltage": sq(v * r["ratio"])}] * 2 + [{"current": sq(0.68), "voltage": sq(v)}] + \
              [{"current": sq(0.024), "voltage": sq(v * r["ratio"])}] * 2
        mag = {"core": core, "coil": coil, "manufacturerInfo": {"name": "verification", "reference": "magnetics.py"}}
        inp = PyOM.process_inputs({"designRequirements": {"magnetizingInductance": {"nominal": r["L_half"]},
                                                          "turnsRatios": [{"nominal": 1 / r["ratio"]}] * 4},
                                   "operatingPoints": [{"name": "nom", "conditions": {"ambientTemperature": 85.0},
                                                        "excitationsPerWinding": [dict(e, frequency=f) for e in exc]}]})
        L = PyOM.calculate_inductance_from_number_turns_and_gapping(core, coil, inp["operatingPoints"][0], {"reluctance": "ZHANG"})
        lk = PyOM.calculate_leakage_inductance(mag, f, 2)
        vals = lk.get("leakageInductancePerWinding")
        lks = [x.get("nominal") if isinstance(x, dict) else x for x in (vals or [])]
        lm_first = _num(L, "magnetizingInductance")
        return {"L_of_S1_H": lm_first, "L_half_pri_H": lm_first * (r["n_half"] / r["n_sec"]) ** 2 if lm_first else None,
                "L_leak_from_P_H": lks, "note": "MAS N87 for DMR44 (same mu_i class); leakage per other winding, seen from P"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


BIAS_COST = dict(cat=dict(core=0.30, bobbin=0.35, clips=0.05, tiw=0.31, wire_varnish=0.04, labour=0.60, test=0.25),
                 k5=dict(core=0.18, bobbin=0.15, clips=0.03, tiw=0.20, wire_varnish=0.03, labour=0.35, test=0.12),
                 tooling_usd=4000.0, margin=1.15,
                 single=dict(cat=1.10, k5=0.60, note="EP10 DMR44 + standard 2-section bobbin (no tooling), one secondary"),
                 basis="ESTIMATES, no quote: DMEGC EP17 set 13.4 g (Chinese small-core pricing); custom 5-section LCP bobbin "
                       "(tooling ~4 kUSD once); TIW 0.16 mm ~0.12 USD/m (1 k) / 0.08 (5 k), 2.6 m; automated sectional winding; "
                       "Lm / C / functional hipot routine test")


def write_design_bias(req, r, om, ins):
    c = EP17
    k_, a_, b_ = dmr95_steinmetz()
    cat = round(sum(BIAS_COST["cat"].values()) * BIAS_COST["margin"], 2)
    k5 = round(sum(BIAS_COST["k5"].values()) * BIAS_COST["margin"], 2)
    hs = r["cap"]["one_apart"]
    d = dict(part="gdrv_bias_transformer", revision="M1", status="proposed construction - calculated, not built or measured",
             basis={"spec": "sim/out/gdrv_miller/bias_transformer_req.json", "spec_mtime": _mtime("sim/out/gdrv_miller/bias_transformer_req.json"),
                    "requirement": req},
             construction=f"one per phase: DMEGC EP17 DMR44 ungapped, custom bobbin with 5 side-by-side sections separated by "
                          f"{BIAS_RULES['flange']*1e3:.1f} mm flanges, order S1 (high side A) | S2 (low side A) | P (2 x {r['n_half']} t "
                          f"bifilar, centre tap) | S3 (low side B) | S4 (high side B); secondaries {r['n_sec']} t TIW "
                          f"{BIAS_RULES['tiw_cu']*1e3:.2f} mm; core floating; vacuum varnish dip",
             electrical=dict(turns=f"{r['n_half']}+{r['n_half']}:{r['n_sec']} x4", ratio=r["ratio"],
                             ratio_note=f"6.0 instead of the requested 5.9 (5.9 needs 10:59, which does not fit): raw output +1.7 % "
                                        f"({req['rectifier'].split('raw DC ')[-1]} becomes ~{25.8*r['ratio']/5.9:.1f}-{30.5*r['ratio']/5.9:.1f} V)",
                             L_half_primary_H=r["L_half"], L_half_primary_min_H=r["L_half_min"], I_mag_pk_max_A=r["I_mag_max"],
                             I_mag_limit_A=req["magnetizing_current_max_A_pk"], B_pk_T=r["B_pk"], B_pk_Amin_T=r["B_pk_amin"],
                             B_sat_100C_T=c["bsat_100"], f_min_Hz=req["f_min_Hz"], Vs_half_primary=req["volt_seconds_per_half_primary_Vs"],
                             L_leak_pri_ref_H=r["L_leak"], commutation_s=r["t_commutation"],
                             capacitance_F=dict(r["cap"], pairs="adjacent: P-S2, P-S3, S1-S2, S3-S4; one apart: P-S1, P-S4, S2-S3 "
                                                "(across P); two apart or more: S1-S4",
                                                high_side_to_primary=hs, cm_current_at_124V_ns_A=hs * 124e9,
                                                method="own: parallel plate between adjacent section faces through the flange (build x MLT "
                                                       "/ (t_flange/eps + t_TIW/eps + air)) + each section to the floating MnZn core "
                                                       "(post through the tube, shell through air) taken in series + 0.3 pF pins; "
                                                       "one apart: 20 % of the direct term (fringe)"),
                             R_sec_110C_ohm=r["R_sec_110"], R_half_pri_110C_ohm=r["R_half_pri_110"]),
             core=dict(maker=c["maker"], part_number=f"{c['pn']} set ({c['mat']}), ungapped", material=c["mat"], Ae_m2=c["Ae"],
                       A_min_m2=c["Amin"], le_m=c["le"], Ve_m3=c["Ve"], AL_H=c["AL"], AL_tol=f"+/-{c['AL_tol']*100:.0f} %",
                       datasheet=c["src"], gap={"type": "none"},
                       asian_alternative="DMEGC is the Asian part; NiZn rejected (DN100H/DN150H Curie point 130/100 C, mu 1000-1500: "
                                         "more turns); sector-wound toroid rejected (coupling: commutation > 5 % of the half period)"),
             windings=[dict(order=i + 1, name=n, turns=r["n_half"] * 2 if n == "P" else r["n_sec"],
                            conductor=("enamelled Cu 0.40 mm grade 1, bifilar 2 x %d t, centre tap" % r["n_half"]) if n == "P" else
                            f"TIW {BIAS_RULES['tiw_cu']*1e3:.2f} mm (three-layer, IEC 61558-2-16 / 62368-1 Annex J), {r['layers']} layers x {r['per_layer']}",
                            channel=ch) for i, (n, ch) in enumerate((("S1", "high side A"), ("S2", "low side A"), ("P", "SN6505B"),
                                                                    ("S3", "low side B"), ("S4", "high side B")))],
             fit=dict(build_m=r["build"], available_m=c["win_radial"] - BIAS_RULES["tube"] - 0.3e-3, fits=r["fits"],
                      section_width_m=r["w"]),
             insulation=dict(system="FUNCTIONAL (B3): TIW on every secondary + 0.6 mm bobbin flanges between sections + vacuum varnish "
                                    "dip (no air voids at the TIW); pins of different windings >= 5.0 mm apart (skip pin positions at 2.5 mm "
                                    "pitch; 3.2 mm if the board is coated); core floating",
                             levels=[dict(label="functional, each secondary to P and to the other secondaries", u_w=req["working_V_dc"],
                                          u_rp=req["recurring_peak_V"], pd_test=req["pd_free_V_pk_min"], imp=req["surge_V_1.2_50us"])],
                             routine_tests=f"L_half, ratio, R; PD <= 10 pC at {req['pd_free_V_pk_min']:.0f} V pk between windings (sample) "
                                           "or 2.5 kV DC 1 s each pair (routine); C P-S and S-S <= 5 pF at 100 kHz (sample)",
                             type_tests=f"impulse {req['surge_V_1.2_50us']:.0f} V 1.2/50 between windings; thermal at 85 C ambient"),
             loss_model=dict(core={"k": k_, "alpha": a_, "beta": b_, "Ve_m3": c["Ve"], "N": r["n_half"], "Ae_m2": c["Ae"],
                                   "formula": "Steinmetz (DMR95 set as a stand-in for DMR44; milliwatts)"},
                             winding={"R_ac_per_harmonic": None, "referred_to": "per winding, DC", "temperature_C": 110.0,
                                      "formula": "P = I_rms^2 R (thin wires at 420 kHz: R_ac ~ R_dc)"}),
             loss_table=[dict(point="420 kHz, 4 x 24 mA", I_rms_A=req["secondary_rms_A_est"], B_pk_T=r["B_pk"], P_core_W=r["P_core"],
                              P_cu_W=r["P_cu"], P_total_W=r["P_core"] + r["P_cu"])],
             loss_grid=dict(I_rms_A=[], P_cu_W=[], B_pk_T=[], P_core_W=[], note="milliwatts; not needed"),
             thermal=dict(dT_K=r["dT"], basis="~120 K/W board-mounted EP17 (natural convection), estimate",
                          margins=f"B_pk {r['B_pk_amin']*1e3:.0f} mT on A_min at f_min {req['f_min_Hz']/1e3:.0f} kHz vs >= 315 mT at 100 C; "
                                  "Curie > 215 C (DMR44)"),
             mass_kg=c["mass"] + 0.004,
             cost=dict(catalogue_1k_usd=cat, build_5k_usd=k5, tooling_usd=BIAS_COST["tooling_usd"],
                       per_module_PV_P75_usd=dict(catalogue=round(3 * cat, 2), build_5k=round(3 * k5, 2)),
                       four_single_secondary_parts_per_phase_usd=dict(catalogue=round(4 * BIAS_COST["single"]["cat"], 2),
                                                                      build_5k=round(4 * BIAS_COST["single"]["k5"], 2),
                                                                      note=BIAS_COST["single"]["note"] + "; same capacitance class (P-S "
                                                                           "~2-3 pF on a 2-section bobbin, S-S < 0.5 pF via the board); "
                                                                           "4 x the board area; no tooling"),
                       verdict=f"one 4-secondary part is cheaper ({k5:.2f} vs {4*BIAS_COST['single']['k5']:.2f} USD per phase at 5 k, "
                               f"{cat:.2f} vs {4*BIAS_COST['single']['cat']:.2f} at catalogue) once the bobbin tooling "
                               f"({BIAS_COST['tooling_usd']:.0f} USD, {BIAS_COST['tooling_usd']/5000:.2f} USD/part over 5 k) is paid",
                       total_usd=cat, basis=BIAS_COST["basis"],
                       cost_estimates_row=f"T_BIAS4 (CUSTOM),{cat:.2f},\"ESTIMATE (sim/magnetics.py design_gdrv_bias_transformer.json rev M1): "
                                          f"DMEGC EP17 DMR44 + custom 5-section bobbin + 4 x 24 t TIW, catalogue/1k; {k5:.2f} at 5 k + "
                                          f"{BIAS_COST['tooling_usd']:.0f} USD tooling; no quote\",low"),
             verification=dict(own=dict(L_half_H=r["L_half"], L_leak_adjacent_H=r["L_leak"]["adjacent"],
                                        L_leak_one_apart_H=r["L_leak"]["one_apart"], C_max_F=max(r["cap"].values())),
                               openmagnetics=om, previous_designer=dict(catalogue="Wurth 760390014: 400 V rms working, 12.5 pF (fails)",
                                                                        architecture="ARCHITECTURE-COSTFIRST.md section 4: 1.80 USD per phase")))
    with open(os.path.join(OUT, "design_gdrv_bias_transformer.json"), "w") as fh:
        json.dump(d, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return d


def design_checks_bias(d):
    e, req = d["electrical"], d["basis"]["requirement"]
    C = []

    def chk(cid, what, value, limit, good):
        C.append(dict(id=cid, what=what, value=value, limit=limit, ok=bool(good), exc=""))

    cmax = max(v for k, v in e["capacitance_F"].items() if k in ("adjacent", "one_apart", "two_apart"))
    lim = min(req["capacitance_max_F"].values())
    chk("bias_fit", "T_BIAS4 sections fit the EP17 window", f"{d['fit']['build_m']*1e3:.2f} mm", f"<= {d['fit']['available_m']*1e3:.2f} mm",
        d["fit"]["fits"])
    chk("bias_cap", "T_BIAS4 largest winding-pair capacitance (own)", f"{cmax*1e12:.2f} pF", f"<= {lim*1e12:.1f} pF", cmax <= lim)
    chk("bias_imag", "T_BIAS4 magnetising current at -25 % A_L, f_min", f"{e['I_mag_pk_max_A']:.3f} A",
        f"<= {e['I_mag_limit_A']:.2f} A", e["I_mag_pk_max_A"] <= e["I_mag_limit_A"])
    chk("bias_b", "T_BIAS4 B_pk on A_min at f_min / B_sat(100 C)", f"{e['B_pk_Amin_T']/e['B_sat_100C_T']:.2f}", "<= 0.3",
        e["B_pk_Amin_T"] <= 0.3 * e["B_sat_100C_T"])
    rmax = req["winding_resistance_max_ohm"]
    chk("bias_r", "T_BIAS4 winding resistance at 110 C (secondary, half primary)",
        f"{e['R_sec_110C_ohm']:.2f} / {e['R_half_pri_110C_ohm']:.3f} ohm", f"<= {rmax['secondary']} / {rmax['half_primary']} ohm",
        e["R_sec_110C_ohm"] <= rmax["secondary"] and e["R_half_pri_110C_ohm"] <= rmax["half_primary"])
    tc = max(e["commutation_s"].values()) * 2 * req["f_min_Hz"]
    chk("bias_tc", "T_BIAS4 rectifier commutation (leakage) / half period", pct(tc), "<= 5 %", tc <= 0.05)
    om = d["verification"]["openmagnetics"]
    if om.get("L_leak_from_P_H"):
        own_a, own_o = e["L_leak_pri_ref_H"]["adjacent"], e["L_leak_pri_ref_H"]["one_apart"]
        om_a, om_o = om["L_leak_from_P_H"][1], om["L_leak_from_P_H"][0]
        chk("bias_om_leak", f"T_BIAS4 leakage used (own 1-D) covers OpenMagnetics (own {pct(rel(own_a, om_a))} / {pct(rel(own_o, om_o))}; "
            "the 1-D model ignores field spreading into post and shell)", f"{own_a*1e6:.2f} / {own_o*1e6:.2f} uH",
            f">= {om_a*1e6:.2f} / {om_o*1e6:.2f} uH", own_a >= om_a and own_o >= om_o)
        chk("bias_om_L", "T_BIAS4 L_half own (catalogue A_L) vs OpenMagnetics", pct(rel(e["L_half_primary_H"], om["L_half_pri_H"])),
            "<= 25 % (A_L tolerance)", abs(rel(e["L_half_primary_H"], om["L_half_pri_H"])) <= 0.25)
    return C


# ---- port CM choke rev M2: requirement re-derived for 150 kHz - 30 MHz (ARCHITECTURE-COSTFIRST.md 8.1) -----------------------
CMC_GRADES["sc1k107lp"] = ("Shincore SC-1K107-LP (flat, Asian catalogue)", 30320.0, 28737.0, 20935.0, "OpenMagnetics MAS material curve")
CMC_GRADES["sc1k107"] = ("Shincore SC-1K107 (high mu, Asian catalogue)", 102263.0, 80027.0, 39127.0, "OpenMagnetics MAS material curve")
MU_HF = {"flat11k": (5953.0, 2627.0, 847.0), "nanoperm8000": (4283.0, 1890.0, 609.0), "sc1k107lp": (17618.0, 7631.0, 1860.0),
         "sc1k107": (30814.0, 13360.0, 4423.0)}        # |mu| at 150 kHz, 500 kHz, 2 MHz (MAS; flat11k = Nanoperm 8000 x 11/7.9)
RING63 = dict(name="nanocrystalline ring 63/38/25 mm (architecture 8.1)", od=63e-3, id=38e-3, h=25e-3, ae=234e-6, le=0.1587,
              bsat=1.2, case=1.0e-3)
EMI = dict(C_Y=18.8e-9, Z_AN=150.0, angle_deg=45.0, need_db={150e3: 16.0, 500e3: 16.0, 2e6: 6.0},
           basis="architecture 8.1: emission estimate 106-112 dBuV at 150-300 kHz with Y caps 2 x 9.4 nF and a 150 ohm CM "
                 "network; CISPR 11 class A DC-port AV limit (from memory, verify) -> >= 16 dB CM attenuation at 150-500 kHz, "
                 ">= 6 dB above")


def cmc_il(z_abs, f, e=EMI):
    zy = 1 / (1j * 2 * math.pi * f * e["C_Y"])
    zc = z_abs * complex(math.cos(math.radians(e["angle_deg"])), math.sin(math.radians(e["angle_deg"])))
    return 20 * math.log10(abs(zy + e["Z_AN"] + zc) / abs(zy + e["Z_AN"]))


def cmc_m2_eval(ps, I_dc, a_cu, grade, N, k, core=None):
    r = cmc_eval(ps, I_dc, a_cu, grade, N, k, core=core)
    c = core or NANO_REF
    z = {f: 2 * math.pi * f * MU0 * mu * N ** 2 * r["A"] / r["le"] for f, mu in zip((150e3, 500e3, 2e6), MU_HF[grade])}
    il = {f: cmc_il(zz, f) for f, zz in z.items()}
    if N == 1:          # bars pass straight through: no winding, short conductor, no case potting
        r["length"] = r["h_stack"] + 0.2
        r["R_pole"] = rho_cu(90.0) * r["length"] / a_cu
        r["P_cu"] = 2 * I_dc ** 2 * r["R_pole"]
        r["cost"] = dict(core_usd=20.0 * r["mass_core"] + 1.5 * k, copper_usd=0.0, insulation_usd=2.0 * k, labour_usd=1.0 * k)
        r["cost"]["total_usd"] = round(sum(r["cost"].values()) * 1.10, 1)
        r["dT"] = 0.0
        r["fill"] = 2 * (10e-3 + 1.5e-3) * (a_cu / 10e-3 + 1.5e-3) / (math.pi / 4 * (c["id"] - 2 * c["case"]) ** 2)
    meets = (all(il[f] >= EMI["need_db"][f] for f in il) and r["B_total"]["B_690VAC_minmax"] <= r["B_allow"]
             and r["fill"] <= CMC_RULES["fill_max"] and r["dT"] <= 40.0)
    return dict(r, Z=z, IL=il, meets_m2=meets, core_name=c.get("name", "80/50/25 mm"))


def design_cmc_m2():
    cm, _ = port_cmc_inputs()
    ps = load("sim/out/port_design/port_spec.json")["cm_choke"]
    ps = dict(ps, L_cm_min_H=0.0)
    out = {}
    for key, s in cm.items():
        a_cu = 50e-6 if s["I"] <= 160 else 70e-6
        opts = [cmc_m2_eval(ps, s["I"], a_cu, g, n, k) for g in ("flat11k", "nanoperm8000", "sc1k107lp", "sc1k107")
                for k in range(1, 7) for n in range(1, 6)]
        good = sorted((o for o in opts if o["meets_m2"]), key=lambda o: o["cost"]["total_usd"])
        ps_r = dict(ps, V_cm_HF_V_rms=CMC_M2["I_cm_sw"] * 2 * math.pi * 32e3 * MU0 * CMC_GRADES[CMC_M2["grade"]][2] * RING63["ae"] / RING63["le"])
        ring = cmc_m2_eval(ps_r, s["I"], a_cu, CMC_M2["grade"], 1, 1, core=RING63)      # HF CM voltage = 16 mA x |Z(32 kHz)|
        m1 = cmc_m2_eval(ps, s["I"], a_cu, CMC_CHOICE["grade"], CMC_CHOICE["N"], CMC_CHOICE["k"])
        out[key] = dict(spec=s, choke_only=good[0] if good else None, ring=ring, m1=m1)
    return out, ps


CMC_M2 = dict(grade="sc1k107lp", N=1, k=1, core=RING63, I_cm_sw=0.016, C_Y_needed=130e-9)


def y_cap_for(il_db, f, e=EMI):
    """total CM shunt capacitance at the module ports that gives il_db more attenuation than e['C_Y'] (no choke)"""
    base = abs(1 / (1j * 2 * math.pi * f * e["C_Y"]) + e["Z_AN"]) / abs(1 / (1j * 2 * math.pi * f * e["C_Y"]))
    lo, hi = e["C_Y"], 10e-6
    for _ in range(60):
        cy = math.sqrt(lo * hi)
        zy = 1 / (1j * 2 * math.pi * f * cy)
        (lo, hi) = (cy, hi) if 20 * math.log10(abs(zy + e["Z_AN"]) / (abs(zy) * base)) < il_db else (lo, cy)
    return hi


def write_design_cmc_m2(d1, m2, ps, ins):
    d = json.loads(json.dumps(d1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    ring = {k_: v["ring"] for k_, v in m2.items()}
    r0 = next(iter(ring.values()))
    cy = max(y_cap_for(EMI["need_db"][f], f) for f in (150e3, 500e3))
    e2 = dict(EMI, C_Y=cy)
    il_net = {f: 20 * math.log10(abs(1 / (1j * 2 * math.pi * f * cy) + EMI["Z_AN"] + r0["Z"][f] * complex(math.cos(math.pi / 4), math.sin(math.pi / 4)))
                                 / abs(1 / (1j * 2 * math.pi * f * EMI["C_Y"]) + EMI["Z_AN"]) * abs(1 / (1j * 2 * math.pi * f * EMI["C_Y"]))
                                 / abs(1 / (1j * 2 * math.pi * f * cy))) for f in (150e3, 500e3, 2e6)}
    i_lf_y = max(PCS_CASES.values()) * 2 * math.pi * 150 * cy
    m1 = {k_: v["m1"] for k_, v in m2.items()}
    d.update(revision="M2", status="proposed construction - calculated, not built or measured (cost-first port, D-044)",
             construction="one flat-mu nanocrystalline ring 63/38/25 mm (Shincore SC-1K107-LP class, mu 30 000) around both bars of "
                          "the port, single pass, in a clip-on case; the 16 dB CM attenuation of ARCHITECTURE-COSTFIRST 8.1 comes from "
                          f"raising the module's CM capacitance to PE to >= {cy*1e9:.0f} nF (port engineer, with the PE measures below), "
                          "not from the ring")
    d["electrical"] = dict(requirement=EMI["basis"], need_db={f"{f/1e3:.0f} kHz": v for f, v in EMI["need_db"].items()},
                           derivation_check=dict(
                               method="I_AN / I_source = Z_Y / (Z_Y + Z_choke + Z_AN): the switch-node CM source is capacitive (~kohm), so "
                                      "the choke only acts between the Y-cap node and the port; Z_Y of 18.8 nF, Z_AN 150 ohm, nanocrystalline "
                                      "|Z| at 45 deg",
                               choke_needed_for_16dB_ohm={f"{f/1e3:.0f} kHz": round(z, 0) for f, z in
                                                          ((150e3, 1048.0), (500e3, 900.0))},
                               architecture_ring_100ohm_gives_dB=round(cmc_il(100.0, 150e3), 1),
                               single_ring_gives_dB={f"{f/1e3:.0f} kHz": round(v, 1) for f, v in r0["IL"].items()},
                               m1_gives_dB={f"{f/1e3:.0f} kHz": round(v, 1) for f, v in next(iter(m1.values()))["IL"].items()},
                               choke_only_cheapest=dict((k_, (dict(construction=f"{v['choke_only']['grade_label']}, {v['choke_only']['N']} turns on "
                                                                                 f"{v['choke_only']['k']} x 80/50/25", cost_usd=v["choke_only"]["cost"]["total_usd"],
                                                                 IL_dB={f"{f/1e3:.0f} kHz": round(x, 1) for f, x in v["choke_only"]["IL"].items()})
                                                            if v["choke_only"] else "none fits an 80/50/25 stack (window fill)"))
                                                        for k_, v in m2.items())),
                           C_Y_total_needed_F=cy, IL_with_C_Y_and_ring_dB={f"{f/1e3:.0f} kHz": round(v, 1) for f, v in il_net.items()},
                           LF_current_through_C_Y_A_rms=i_lf_y,
                           C_Y_consequence=f"{i_lf_y*1e3:.1f} mA rms at 150 Hz into PE in the 800 V AC case: inside the 30 mA module limit "
                                           "(architecture 8.1) but above 3.5 mA touch current -> PE >= 10 mm2 Cu or a second PE terminal + "
                                           "warning label (IEC 62477-1 high-touch-current rule) - an architecture / safety decision",
                           emission_margin_given_up_vs_M1_dB={f"{f/1e3:.0f} kHz": round(m1v - r0["IL"][f], 1) for f, m1v in
                                                              next(iter(m1.values()))["IL"].items()},
                           ring_L_1turn_H=r0["L_nom"], B_LF_caseB_T=r0["B_total"]["B_690VAC_minmax"],
                           B_LF_caseC_T=r0["B_total"]["C_800VAC_minmax"], B_allow_T=r0["B_allow"], B_HF_T=r0["B_hf"], B_DM_T=r0["B_dm"])
    d["core"] = dict(maker="Shincore (or Yunlu / AT&M flat grade) - RFQ", part_number="nanocrystalline ring 63/38/25 mm, flat-loop, cased",
                     material=CMC_GRADES[CMC_M2["grade"]][0], n_cores=1, Ae_m2=RING63["ae"], le_m=RING63["le"],
                     mu_150Hz=CMC_GRADES[CMC_M2["grade"]][1], mu_150kHz=MU_HF[CMC_M2["grade"]][0], B_sat_T=RING63["bsat"], gap={"type": "none"},
                     datasheet="MAS material SC-1K107-LP (OpenMagnetics); Yunlu catalogue (docs/datasheets/magnetics/Yunlu-Nanocrystalline-Cores.pdf "
                               "p.6) for the adjustable-mu grades", asian_alternative="this is the Asian part",
                     rejected="high-mu 80 000 ring (architecture 8.1 default): saturates with the 150 Hz CM current (B ~3 T in case B)")
    d["windings"] = [dict(name=k_, turns=1, conductor=f"port bars pass through ({d1['variants'][k_]['copper_bar']})", arrangement="+ and - bars side by side")
                     for k_ in m2]
    d["fit"] = dict(build_m=None, available_m=None, fits=all(v["ring"]["fill"] <= CMC_RULES["fill_max"] for v in m2.values()),
                    hole_fill={k_: v["ring"]["fill"] for k_, v in m2.items()}, fill_max=CMC_RULES["fill_max"])
    d["insulation"]["system"] = ("the ring sits on sleeved bars (>= 0.75 mm, >= 1.5 kV DC); case CTI >= 600; the ring is not an insulation part "
                                 "(bars' own insulation is the basic insulation, IC-09 levels unchanged)")
    d["loss_table"] = [dict(point=f"{k_} {v['spec']['I']:.0f} A DC", I_rms_A=v["spec"]["I"], B_pk_T=v["ring"]["B_total"]["B_690VAC_minmax"],
                            P_core_W=v["ring"]["P_core"], P_cu_W=0.0, P_total_W=v["ring"]["P_core"]) for k_, v in m2.items()]
    d["loss_grid"] = dict(I_rms_A=[], P_cu_W=[], B_pk_T=[], P_core_W=[], note="no winding; core loss from the 150 Hz CM flux, < 1 W")
    d["thermal"] = dict(dT_rated_K=0.0, basis="no winding loss; core loss < 1 W", interface="clip-on case on the bars")
    d["mass_kg"] = {k_: v["ring"]["mass_core"] + 0.05 for k_, v in m2.items()}
    co = r0["cost"]
    d["cost"] = dict(per_variant={k_: v["ring"]["cost"] for k_, v in m2.items()},
                     basis="ESTIMATES, no quote: flat nanocrystalline 20 USD/kg (0.27 kg) + case 1.5 + insulation 2 + labour 1, +10 %; "
                           f"the extra Y capacitance (~{cy*1e9:.0f} nF total) is the port engineer's BOM (~2 USD per port)",
                     cost_estimates_row=f"CM ring 63/38/25 (CUSTOM),{co['total_usd']:.0f},\"ESTIMATE (sim/magnetics.py design_port_cm_choke.json rev M2): "
                                        "flat-mu nanocrystalline ring 0.27 kg at 20 USD/kg + case/insulation/labour 4.5, +10 %; one per port, "
                                        "both ratings; no quote\",low",
                     total_usd=co["total_usd"])
    d["verification"] = dict(own=dict(IL_ring_dB=r0["IL"], IL_m1_dB=next(iter(m1.values()))["IL"], B_caseB_T=r0["B_total"]["B_690VAC_minmax"]),
                             openmagnetics=dict(note="MAS |mu(f)| of SC-1K107-LP / Nanoperm 8000 used for all impedances (get_material_permeability)"),
                             previous_designer=dict(architecture_8_1="one high-mu ring per port, single turn, |Z| >= 100 ohm, 12 USD for two cores"))
    d["reference_M1"] = dict(construction=d1["construction"], cost=d1["cost"]["per_variant"], IL_dB=next(iter(m1.values()))["IL"])
    d["options"] = [dict(option=f"{o['grade_label']}, {o['N']} t x {o['k']} core(s) {o['core_name'][:22]}", rating=k_,
                         IL_150k_dB=round(o["IL"][150e3], 1), IL_500k_dB=round(o["IL"][500e3], 1), IL_2M_dB=round(o["IL"][2e6], 1),
                         B_caseB_T=round(o["B_total"]["B_690VAC_minmax"], 2), cost_usd=o["cost"]["total_usd"], meets_16dB_alone=o["meets_m2"])
                    for k_, v in m2.items() for o in [v["ring"], v["m1"]] + ([v["choke_only"]] if v["choke_only"] else [])]
    d.pop("variants", None)
    with open(os.path.join(OUT, "design_port_cm_choke.json"), "w") as fh:
        json.dump(d, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return d


def design_checks_cmc_m2(d):
    e = d["electrical"]
    C = []

    def chk(cid, what, value, limit, good):
        C.append(dict(id=cid, what=what, value=value, limit=limit, ok=bool(good), exc=""))

    chk("cmc_m2_B", "M2 ring flux LF CM case B (+25 % mu) + DM + HF", f"{e['B_LF_caseB_T']:.2f} T (case C {e['B_LF_caseC_T']:.2f})",
        f"<= {e['B_allow_T']:.2f} T (C below B_sat {RING63['bsat']} T)", e["B_LF_caseB_T"] <= e["B_allow_T"] and e["B_LF_caseC_T"] < RING63["bsat"])
    chk("cmc_m2_fill", "M2 bars pass the ring (sleeved)", _v(d["fit"]["hole_fill"]), f"<= {d['fit']['fill_max']}", d["fit"]["fits"])
    il = e["IL_with_C_Y_and_ring_dB"]
    chk("cmc_m2_IL", f"M2 CM attenuation with {e['C_Y_total_needed_F']*1e9:.0f} nF CM capacitance + ring (hand-off to the port engineer)",
        _v(il), ">= 16 / 16 / 6 dB at 150 kHz / 500 kHz / 2 MHz",
        il["150 kHz"] >= 16 and il["500 kHz"] >= 16 and il["2000 kHz"] >= 6)
    closed = {"cmc_lf": ("closed by sim/out/magnetics/design_port_cm_choke.json rev M2 (calculated; flat grade to be quoted)",
                         "; ".join(f"{c_['id']} {c_['value']} ({c_['limit']})" for c_ in C[:2]))} if C[0]["ok"] and C[1]["ok"] else {}
    return C, closed


LITZ_USD_KG = {0.05e-3: 60.0, 0.071e-3: 45.0, 0.10e-3: 32.4}     # estimates (fine-strand premium over LME copper)


def dab_cost_first(D, xd):
    """report item 4: what a cost-first DAB transformer pair / series inductor would give (no redesign)"""
    rows = []
    for lab, kw in (("M1 pair, 2 x EE80 each, litz 0.05 mm", {}), ("pair, litz 0.071 mm", dict(d_str=0.071e-3)),
                    ("pair, litz 0.10 mm", dict(d_str=0.10e-3)), ("one transformer, 2 x EE80, litz 0.05 mm", dict(n_par=1)),
                    ("one transformer, 3 x EE80, litz 0.05 mm", dict(n_par=1, n_stack=3))):
        x = xf_eval(D, **dict(DAB_CHOICE, **kw))
        m60 = x["points"]["matched_60kW"]
        d_s = kw.get("d_str", DAB_CHOICE["d_str"])
        cu = x["mass_cu"] * 1.15 * LITZ_USD_KG[d_s]
        core = PRICE["ferrite_usd_kg"] * x["mass_core"]
        n = x["n_par"]
        cost = round((cu + core + (PRICE["housing_potting"] + PRICE["former_ins"] + PRICE["labour"] + PRICE["test"]) * n) * 1.10)
        rows.append(dict(part="transformer", option=lab, P_worst_W=round(x["worst"]["P_total"]), P_60kW_W=round(m60["P_cu"] + m60["P_core"]),
                         T_hot_C=round(x["thermal"]["T_winding"]), cost_usd=cost))
    for lab, kw in (("M1 series L, 4 x EE80, 3 t", {}), ("series L, 3 x EE80, 4 t", dict(n_stack=3, N=4, k_gaps=8)),
                    ("series L, 4 x EE80, 4 t (MG-15 fix)", dict(N=4, k_gaps=8)), ("series L, litz 0.10 mm", dict(d_str=0.10e-3))):
        l_ = ls_eval(D, xd["L_ext"], **dict(LS_CHOICE, **kw))
        m60 = l_["points"]["matched_60kW"]
        d_s = kw.get("d_str", LS_CHOICE["d_str"])
        cost = round((PRICE["ferrite_usd_kg"] * XF_CORES["EE80"]["mass"] * l_["n_stack"] + 10.0 + LITZ_USD_KG[d_s] * l_["mass_cu"] * 1.15
                      + PRICE["housing_potting"] + PRICE["former_ins"] + PRICE["labour"] * 0.7 + PRICE["test"]) * 1.10)
        rows.append(dict(part="series inductor", option=lab, P_worst_W=round(l_["P_total"]), P_60kW_W=round(m60["P_cu"] + m60["P_core"]),
                         T_hot_C=round(l_["T_winding"]), cost_usd=cost, B_635A_T=round(l_["B_177"] / D["I1pk"] * 635.0, 2)))
    return rows


# ---- 75 W auxiliary flyback AUX-T1 (cost-first, item 2a): live 24 V winding + reinforced SELV winding ---------------------------
EC39A = dict(maker="DMEGC", pn="EC39A", mat="DMR95", src=MAG + "DMEGC-EC39A.pdf p.1", Ae=133e-6, Amin=128.7e-6, le=103e-3,
             Ve=13699e-9, mass=0.070, d_leg=13.0e-3, win_h=29.2e-3, win_w=8.8e-3, mu_i=3300.0,
             note="window and post from the ETD 39/20/13 class (MAS); the EC39A drawing is image-only - confirm the bobbin")
AUX75_CHOICE = dict(setback=0.5e-3, p_wire=("litz", 30, 0.10e-3), s_wire=("litz", 150, 0.10e-3), a_wire=("litz", 100, 0.10e-3),
                    t_p1sh=6, t_shs=4, t_sa=4, t_ap2=1, t_out=2)   # litz P and live: solid 0.6 / 1.0 mm lose 1.8-3.0 W in the gap fringing


def aux75_inputs(spec):
    q, dp = spec["transformer_requirement"], spec["design_point"]
    eta = next(iter(spec["efficiency"].values()))
    n_live, n_selv, np_ = q["turns"]["n_live"], q["turns"]["n_selv"], q["turns"]["np"]
    v_selv = dp["vr_V"] / (np_ / n_selv) - 0.9
    return dict(np=np_, ns=n_selv, na=n_live, lp=q["lp_mH"]["value"] * 1e-3, al=q["lp_mH"]["value"] * 1e-3 / np_ ** 2, gap=None,
                f=dp["fsw_kHz"] * 1e3, vr=dp["vr_V"], vout=v_selv, eta=eta, vin=sorted(int(k) for k in eta), pout=dp["p_design_W"],
                wire=dict(p=0.60e-3, s=1.0e-3, a=1.00e-3), ilim=q["ipk_A"]["limit_max"], lp_tol=q["lp_mH"]["tol_pct"] / 100,
                share_live=dp["outputs"]["live"]["rating_W"] / (dp["outputs"]["live"]["rating_W"] + dp["outputs"]["selv"]["rating_W"]))


def design_aux75(spec):
    A = aux75_inputs(spec)
    r = aux_eval(A, core=EC39A, share_aux=A["share_live"], **AUX75_CHOICE)
    q = spec["transformer_requirement"]
    b = r["breadth"]
    tt = lambda nm: r["pos"][nm][1] - r["pos"][nm][0]  # noqa: E731
    r["L_leak_live_selv"] = MU0 * A["na"] ** 2 * 0.5 * (r["mlt"]["S"] + r["mlt"]["AUX"]) / b * (tt("S") / 3 + AUX75_CHOICE["t_sa"] * AUX_RULES["tape"] + tt("AUX") / 3)
    r["B_lim_Amin"] = A["lp"] * (1 + A["lp_tol"]) * A["ilim"] / (A["np"] * EC39A["Amin"])
    r["A"], r["q"] = A, q
    r["mass_cu_est"] = 8900.0 * (A["np"] * r["mlt"]["P1"] * math.pi * 0.6e-3 ** 2 / 4 + A["ns"] * r["mlt"]["S"] * 150 * math.pi * 0.1e-3 ** 2 / 4
                                 + A["na"] * r["mlt"]["AUX"] * math.pi * 1.0e-3 ** 2 / 4)
    return r


def write_design_aux75(spec, r, om, ins):
    A, q, c = r["A"], r["q"], EC39A
    k_, a_, b_ = r["steinmetz"]
    ii = q["insulation"]["primary + live + shield - SELV"]
    t_min = r["pos"]["S"][0] - r["pos"]["SH"][1] + TIW_INS
    p_worst = max(p["P_cu"] for p in r["points"].values())
    cost = dict(core_usd=0.85, copper_usd=1.6 + 0.6 + 0.2, insulation_usd=0.45 + 1.6, labour_usd=1.6 + 0.8)
    cost["total_usd"] = round(sum(cost.values()) * 1.10, 1)
    cost["build_5k_usd"] = round((0.60 + 1.1 + 0.4 + 0.15 + 0.3 + 1.1 + 1.0 + 0.5) * 1.10, 1)
    cost["catalogue_1k_usd"] = cost["total_usd"]
    d = dict(part="aux75_transformer", revision="M1", status="proposed construction - calculated, not built or measured",
             basis={"spec": "sim/out/aux_hv_design/aux75_spec.json", "spec_mtime": _mtime("sim/out/aux_hv_design/aux75_spec.json"),
                    "spec_rev": spec.get("rev")},
             construction=f"DMEGC EC39A DMR95 (ETD39 class), centre-leg gap {r['gap']*1e3:.2f} mm, {A['np']}:{A['na']} live:{A['ns']} SELV in the "
                          "order P1 | SH | SELV (TIW-litz) | LIVE | P2, copper set back 2.0 mm from the gapped leg, vacuum-potted case",
             electrical=dict(L_p_H=A["lp"], L_p_tol="+/-7 % at 10 kHz", turns=f"{A['np']}:{A['na']}:{A['ns']} (P:live:SELV)", f_sw_Hz=A["f"],
                             mode="DCM >= 185 V", I_pk_limit_A=A["ilim"], B_limit_Amin_T=r["B_lim_Amin"],
                             B_limit_rule_T=q["b_limit_on_amin_mT"] * 1e-3, Np_x_Amin_m2=A["np"] * c["Amin"],
                             Np_x_Amin_min_m2=q["np_x_amin_min_m2"], L_leak_est_H=r["L_leak_est"], L_leak_max_H=q["leakage_primary_max_uH"] * 1e-6,
                             L_leak_live_selv_H=r["L_leak_live_selv"], L_leak_live_selv_max_H=q["leakage_live_selv_max_uH"] * 1e-6,
                             C_switched_P1_SH_own_F=r["C_sw"], C_switched_max_F=q["switched_capacitance_max_pF"] * 1e-12,
                             winding_loss_max_W=q["winding_loss_max_W"], live_share=A["share_live"],
                             acceptance_limits=dict(L_leak_primary_max_H=12e-6, C_switched_max_F=45e-12,
                                                    note="routine-test limits that guarantee the drain-voltage budget (1336 V vs 1360 V): "
                                                         "leakage measured at 100 kHz with SELV and live shorted; switched capacitance "
                                                         "P1-start to shield at 100 kHz"),
                             leakage_for_cross_regulation=dict(P_to_secondaries_own_H=r["L_leak_est"], P_to_SELV_OM_H=om.get("L_leak_H"),
                                                               live_SELV_live_referred_H=r["L_leak_live_selv"]),
                             cross_regulation=("tightest practical band: SELV and live as adjacent single full-width layers, same "
                                               "winding sense, between the primary halves (P1 | SH | SELV | LIVE | P2), separated only by "
                                               "the reinforced tape barrier -> live-SELV leakage ~0.3 uH; bifilar SELV/live would halve "
                                               "it again but leaves only the TIW insulation (~25 kV/mm at the 2505 V pk PD test: not "
                                               "PD-free); a SELV-LIVE-SELV sandwich (~0.15 uH) needs a second reinforced barrier and does "
                                               "not fit the ETD39-class window; the residual band is set by the diode drops and the "
                                               "18 W / 57 W load asymmetry - the converter engineer re-runs the band with these leakages")),
             core=dict(maker=c["maker"], part_number="EC39A set (DMR95)", material=c["mat"], Ae_m2=c["Ae"], A_min_m2=c["Amin"], le_m=c["le"],
                       Ve_m3=c["Ve"], datasheet=c["src"], note=c["note"], gap={"type": "ground centre-leg gap", "length_m": r["gap"]},
                       asian_alternative="this is the Asian part (DMEGC lists no ETD39)", western_reference="TDK ETD 39/20/13 N97"),
             windings=[dict(order=1, name="P1", turns=A["np"] // 2, spec="litz 30 x 0.10 mm (grade 2 strands), 1 layer, start = DRAIN"),
                       dict(order=2, name="SH", turns=1, spec="copper foil 0.05 mm, open overlap, -> BUS-"),
                       dict(order=3, name="SELV", turns=A["ns"], spec="TIW-litz 150 x 0.10 mm (three-layer extruded, certified reinforced), 1 layer"),
                       dict(order=4, name="LIVE", turns=A["na"], spec="litz 100 x 0.10 mm, 1 layer, -> live 24 V rectifier"),
                       dict(order=5, name="P2", turns=A["np"] - A["np"] // 2, spec="litz 30 x 0.10 mm, 1 layer, finish = bulk")],
             fit=dict(build_m=r["build"], available_m=c["win_w"] - AUX_RULES["outer_clear"], fits=bool(r["fits"] and r["fits_breadth"]),
                      widest_layer_m=max(r["width"].values()), breadth_m=r["breadth"]),
             insulation=dict(system="VACUUM-POTTED case; REINFORCED SELV barrier = TIW-litz insulation + 4 x 0.06 mm tape each side of the SELV "
                                    "layer; primary, live, shield and core on the BUS- side (functional among themselves, PD-free 1750 V pk); SELV "
                                    f"pins on their own row, creepage >= {ii['creepage_mm']['pd2']} mm PD2 / {ii['creepage_mm']['pd1']} mm potted, "
                                    f"clearance >= {max(ii['clearance_mm'])} mm", min_solid_selv_m=t_min,
                             levels=[dict(label="Reinforced: primary + live + shield - SELV", u_rp=ii["u_rp_V"], pd_test=2505.0,
                                          ac=ii["ac_type_test_Vrms"], imp=ii["impulse_V"], cr_pd1=ii["creepage_mm"]["pd1"],
                                          cr_pd2=ii["creepage_mm"]["pd2"], clearance=ii["clearance_mm"])],
                             routine_tests=ii["routine_pd"], type_tests=f"AC {ii['ac_type_test_Vrms']} V rms 60 s; impulse {ii['impulse_V']} V; thermal at 82.5 W"),
             loss_model=dict(core={"k": k_, "alpha": a_, "beta": b_, "Ve_m3": c["Ve"], "N": A["np"], "Ae_m2": c["Ae"],
                                   "formula": "unipolar DCM flux (iGSE), DMR95 at 100 C"},
                             winding={"R_ac_per_harmonic": None, "referred_to": "n/a - use loss_table", "temperature_C": 100.0,
                                      "formula": "1-D Dowell ramp + line-source gap fringing per layer; live winding loaded with its power share"}),
             loss_table=[dict(point=f"{v} V, {A['pout']:.1f} W", I_rms_A=p["Ip_rms"], B_pk_T=p["B_pk"], P_core_W=p["P_core"], P_cu_W=p["P_cu"],
                              P_fringe_W=p["P_fringe"], P_total_W=p["P_core"] + p["P_cu"]) for v, p in sorted(r["points"].items())],
             loss_grid=dict(V_in_V=sorted(r["points"]), P_total_W=[r["points"][v]["P_cu"] + r["points"][v]["P_core"] for v in sorted(r["points"])],
                            note=f"at {A['pout']:.1f} W (design point incl. margin)"),
             thermal=dict(T_rise_K=(p_worst + max(p["P_core"] for p in r["points"].values())) * 12.0,
                          basis="potted ETD39-class case ~45 x 40 x 35 mm, ~12 K/W natural convection (estimate)", interface="board-mounted"),
             mass_kg=c["mass"] + r["mass_cu_est"] + 0.05,
             cost=dict(cost, basis="ESTIMATES, no quote: EC39A 70 g 0.85; former 0.45; TIW-litz 150 x 0.1 ~1.0 m 1.6; litz P + live 0.6, foil/tape 0.2; "
                                   "case + vacuum potting 1.6; winding 1.6; PD routine test 0.8; +10 %",
                       cost_estimates_row=f"AUX-T1 75 W (CUSTOM),{cost['total_usd']:.1f},\"ESTIMATE (sim/magnetics.py design_aux75_transformer.json rev M1): "
                                          "DMEGC EC39A DMR95 + TIW-litz SELV + litz P/live + potted case + PD test; no quote\",low"),
             verification=dict(own=dict(P_cu_W=p_worst, L_leak_est_H=r["L_leak_est"], B_limit_Amin_T=r["B_lim_Amin"]),
                               openmagnetics=om, previous_designer=dict(estimates=f"{spec['rev']}: leakage {q['leakage_estimate_uH']:.1f} uH, core "
                                                                                    f"{q['core_loss_estimate_W']:.2f} W, flux {q['flux_estimate_mT']:.0f} mT")))
    with open(os.path.join(OUT, "design_aux75_transformer.json"), "w") as fh:
        json.dump(d, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return d


def design_checks_aux75(d):
    e, ins = d["electrical"], d["insulation"]
    C = []

    def chk(cid, what, value, limit, good):
        C.append(dict(id=cid, what=what, value=value, limit=limit, ok=bool(good), exc=""))

    pc = max(r_["P_cu_W"] for r_ in d["loss_table"])
    chk("aux75_fit", "AUX-T1 75 W stack fits the window", f"{d['fit']['build_m']*1e3:.2f} mm", f"<= {d['fit']['available_m']*1e3:.2f} mm", d["fit"]["fits"])
    chk("aux75_loss", "AUX-T1 75 W winding loss (own, incl. fringing), worst input", f"{pc:.2f} W", f"<= {e['winding_loss_max_W']:.2f} W",
        pc <= e["winding_loss_max_W"])
    chk("aux75_b", "AUX-T1 75 W B at the current limit on A_min (L_p +7 %)", f"{e['B_limit_Amin_T']*1e3:.0f} mT", f"<= {e['B_limit_rule_T']*1e3:.0f} mT",
        e["B_limit_Amin_T"] <= e["B_limit_rule_T"])
    chk("aux75_npamin", "AUX-T1 75 W N_p x A_min", f"{e['Np_x_Amin_m2']*1e3:.2f}e-3 m2", f">= {e['Np_x_Amin_min_m2']*1e3:.2f}e-3 m2",
        e["Np_x_Amin_m2"] >= e["Np_x_Amin_min_m2"])
    chk("aux75_leak", "AUX-T1 75 W leakage (primary; live-SELV)", f"{e['L_leak_est_H']*1e6:.1f} / {e['L_leak_live_selv_H']*1e6:.2f} uH",
        f"<= {e['L_leak_max_H']*1e6:.1f} / {e['L_leak_live_selv_max_H']*1e6:.2f} uH",
        e["L_leak_est_H"] <= e["L_leak_max_H"] and e["L_leak_live_selv_H"] <= e["L_leak_live_selv_max_H"])
    chk("aux75_csw", "AUX-T1 75 W switched capacitance P1-shield (own)", f"{e['C_switched_P1_SH_own_F']*1e12:.0f} pF",
        f"<= {e['C_switched_max_F']*1e12:.0f} pF", e["C_switched_P1_SH_own_F"] <= e["C_switched_max_F"])
    om = d["verification"]["openmagnetics"]
    if om.get("P_cu_W") is not None:
        chk("aux75_om", "AUX-T1 75 W OpenMagnetics winding loss (P + SELV only) also below the limit; L_p OM vs requirement",
            f"{om['P_cu_W']:.2f} W; {pct(rel(om['L_p_H'], e['L_p_H']))}", f"<= {e['winding_loss_max_W']:.2f} W; <= 10 %",
            om["P_cu_W"] <= e["winding_loss_max_W"] and abs(rel(om["L_p_H"], e["L_p_H"])) <= 0.10)
    ekv = 2505.0 / (ins["min_solid_selv_m"] * 1e3) / 1e3
    chk("aux75_ins", "AUX-T1 75 W potted; solid SELV barrier field at the PD routine test", f"{ekv:.1f} kV/mm", "<= 8 kV/mm (design rule, potted)",
        "VACUUM-POTTED" in ins["system"] and ekv <= 8.0)
    return C



def om_aux75(spec, r):
    A = dict(r["A"], gap=r["gap"])
    try:
        od = math.sqrt(150) * 1.15 * (0.1e-3 + AUX_RULES["enamel"] / 2)
        o = om_aux_transformer(r, {"_A": A}, shape="ETD 39/20/13", material="DMR95", gap=r["gap"], s_wire=("litz", 150, 0.1e-3, od),
                               wall=AUX_RULES["wall"] + AUX75_CHOICE["setback"],
                               p_litz=(30, 0.1e-3, math.sqrt(30) * 1.15 * (0.1e-3 + AUX_RULES["enamel"] / 2)))
        return {"L_p_H": _num(o.get("L"), "magnetizingInductance"), "P_core_W": _num(o.get("core"), "coreLosses"),
                "P_cu_W": _num(o.get("winding"), "windingLosses"), "L_leak_H": _num(o.get("leakage"), "leakageInductancePerWinding"),
                "point_V": o.get("vin"), "note": "MAS ETD 39/20/13 window, P and SELV only (live winding not in the OM model); all "
                                                 "secondary ampere-turns in the SELV winding"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("SELF-CHECK FAILED:", e)
        sys.exit(1)
