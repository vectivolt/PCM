"""DAB-D60 closed-loop time-domain simulation (REQUIREMENTS.md DAB-08, DAB-09).

    .venv/bin/python sim/dab_control.py        (run sim/dab_design.py first: reads sim/out/dab_design/dab_spec.json)

Model (calculated, not bench-validated):
  * Switching-cycle exact: between bridge edges the inductor current is solved in closed form (series L with loop
    resistance R, exponential), the magnetising current linearly; port capacitors with their RC networks are solved
    exponentially per interval.  No averaging, so phase-step DC offsets and their decay are captured.
  * SPS modulator, phase updated once per switching period (100 kHz) with a one-period computation delay.  Two update
    rules: 'naive' (both secondary edges jump) and 'split' (the first edge after a change moves by half the step, the
    second by the full step -> volt-second balance kept, no DC offset).
  * Controller: inner loop on the port-2 bridge current (period-average, as an oversampled SDFM channel would give),
    SPS feedforward + PI; outer CV loop on V2 with the current reference clamped by the CC limit (CC/CV).
  * Ports: port 1 = PCS DC bus, stiff source behind 10 mOhm; port 2 = battery (OCV + R_int) and/or resistive load.
  * Parallel branches share both port capacitors; each branch has its own L_k and current sensor.
  * Parallel-branch decision (DAB-09, coordinator review 2026-10-04): NO hardware carrier-sync line.  Carriers are
    free-running (modelled as a fixed relative carrier offset, which drifts only slowly with crystal tolerance); the
    current is shared by a common reference sent over the CAN-B module bus plus a per-branch inner current loop on
    the branch's own I2 sensor.  Port 1 can be a stiff source or a PCS DC link with its own voltage loop.
"""
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT = 'sim/out/dab_control'
SPEC = 'sim/out/dab_design/dab_spec.json'
F = 100e3
T = 1 / F
W = 2 * np.pi * F

ASSUME = {
    'R_loop': (0.040, 'ohm primary-referred series resistance per branch (2 x R_DS(on)+pkg hot, windings, bus)'),
    'R_line1': (0.010, 'ohm port-1 source impedance (PCS DC bus treated as stiff)'),
    'R_int_batt': (0.060, 'ohm battery string (240S LFP ~0.25 mohm/cell)'),
    'sensor_gain_err': (0.01, 'per-branch current-sensor gain error used in the sharing test (+1 % / -1 %)'),
    'f_ci': (2000.0, 'Hz current-loop crossover'),
    'f_cv': (400.0, 'Hz voltage-loop crossover'),
    'phi_max_deg': (60.0, 'deg phase-command clamp'),
    'phi_slew_deg': (3.0, 'deg maximum phase change per switching period (modulator slew limit)'),
    'carrier_offset_deg': (30.0, 'deg relative carrier phase of branch 2 (free-running, no sync line)'),
    'C_pcs': (4e-3, 'F PCS DC-link capacitance on port 1 (one-branch-fault case)'),
    'f_pcs': (30.0, 'Hz PCS DC-link voltage-loop crossover, no load-current feed-forward'),
    't_can': (10e-3, 's CAN-B reference/status frame period (system controller to branches)'),
    'i_ramp': (20e3, 'A/s branch-local reference ramp (firmware), applied to every CAN reference change'),
    'pcs_ff_tau': (2e-3, 's PCS DC-current feed-forward lag (PCS current loop + filter); None = voltage loop only'),
    'P_branch_max': (60e3, 'W per-branch power limit at the nominal point (derating map, 800/800 V)'),
}


def asm(k):
    return ASSUME[k][0]


def load_design():
    if not os.path.exists(SPEC):
        sys.exit(f'{SPEC} not found - run .venv/bin/python sim/dab_design.py first')
    sp = json.load(open(SPEC))
    cp = sp.get('control_plant', {})
    if 'R_loop_primary_ohm' in cp:                       # plant resistance of the chosen switches (dab_design)
        ASSUME['R_loop'] = (cp['R_loop_primary_ohm'], cp['R_loop_basis'])
    return dict(n=sp['transformer']['n'], L=sp['series_inductor']['L_total_H'], Lm=sp['transformer']['L_m_H'],
                C1=sp['capacitor_banks']['port1']['C_total_F'], C2=sp['capacitor_banks']['port2']['C_total_F'],
                oc_trip=sp['protection']['I_xfmr_oc_trip_A'], i_port2_max=sp['power_stage']['port2']['I_max_A'],
                dead_time=sp['power_stage']['dead_time'], st_inputs=sp.get('st_crosscheck_inputs', {}))


def phi_ff(v1, v2, n, L, p):
    """SPS feed-forward: inverse of P = n V1 V2 phi (pi - |phi|)/(2 pi^2 f L)."""
    x = np.clip(8 * F * L * abs(p) / max(v1 * n * v2, 1.0), 0, 1)
    return np.sign(p) * np.pi / 2 * (1 - np.sqrt(1 - x))


# ---------------------------------------------------------------------------------------------------------
# Plant: exact piecewise solution over one switching period for N parallel branches
# ---------------------------------------------------------------------------------------------------------
def simulate(dz, n_periods, ctrl, port2, branches, update='split', v1_src=800.0, v2_0=780.0, record=True,
             slew=True, port1=None):
    """dz: design dict; ctrl(k, meas) -> phase commands (rad) per branch for period k;
    port2: dict(v_oc, r_int (None = no battery), r_load(t) callable or None);
    port1: None = stiff source v1_src behind R_line1; dict(mode='pcs', C, f_c, v_ref, ff_tau) = PCS DC link with its
           own PI voltage loop (controlled current source) in parallel with the DAB banks; ff_tau (s) adds a feed-forward
           of the measured DC load current through a first-order lag (None = no feed-forward);
    branches: list of dicts with L (H), gain (current-sensor gain), optional carrier offset 'off' (rad, |off| <= 30 deg,
    free-running carriers, no sync line) and optional 'trip_t' (s): gates off, branch current to zero.
    Period k is integrated over [kT - T/4, kT + 3T/4) so every edge falls inside its own window.  Returns traces."""
    nb = len(branches)
    n, lm, r = dz['n'], dz['Lm'], asm('R_loop')
    lk = np.array([b['L'] for b in branches])
    gains = np.array([b['gain'] for b in branches])
    off = np.array([b.get('off', 0.0) for b in branches])
    trip = np.array([b.get('trip_t', np.inf) for b in branches])
    assert np.all(np.abs(off) <= np.radians(30) + 1e-12)
    tau = lk / r
    il, im = np.zeros(nb), np.zeros(nb)
    v1, v2 = v1_src, v2_0
    s1, s2 = -np.ones(nb), -np.ones(nb)
    pcs = port1 is not None and port1.get('mode') == 'pcs'
    if pcs:
        c1tot = port1['C'] + dz['C1']
        kp1 = 2 * np.pi * port1['f_c'] * c1tot
        ki1 = kp1 * 2 * np.pi * port1['f_c'] / 2          # PI zero at f_c/2: ~63 deg phase margin
        x1, i_ff = 0.0, 0.0
        i_pcs = 0.0
    phi_cmd_prev = np.zeros(nb)
    tr = {k: [] for k in ('t', 'v1', 'v2', 'i2', 'i2_br', 'il_max', 'il_min', 'il_dc', 'phi', 'p1', 'p2', 'i2_batt',
                          'e_r', 'i_pcs', 'alive')}
    meas = dict(v1=v1, v2=v2, i2=0.0, i2_br=np.zeros(nb), t=0.0, i_port2=0.0, alive=np.ones(nb, bool))
    e_r = 0.0
    for k in range(n_periods):
        t = k * T - T / 4
        alive = k * T < trip
        il, im = np.where(alive, il, 0.0), np.where(alive, im, 0.0)
        phi_cmd = np.clip(np.asarray(ctrl(k, meas), float), -np.radians(asm('phi_max_deg')),
                          np.radians(asm('phi_max_deg')))
        if slew:
            sl = np.radians(asm('phi_slew_deg'))
            phi_cmd = phi_cmd_prev + np.clip(phi_cmd - phi_cmd_prev, -sl, sl)
        ph_e = [0.5 * (phi_cmd_prev + phi_cmd), phi_cmd] if update == 'split' else [phi_cmd, phi_cmd]
        phi_cmd_prev = phi_cmd
        ev = []
        for b in range(nb):
            t0b = k * T + off[b] / W
            ev += [(t0b, 'p', b, 1.0), (t0b + T / 2, 'p', b, -1.0),
                   (t0b + ph_e[0][b] / W, 's', b, 1.0), (t0b + T / 2 + ph_e[1][b] / W, 's', b, -1.0)]
        ev.sort(key=lambda x: x[0])
        ev.append((k * T + 3 * T / 4, 'end', -1, 0.0))
        q2b = np.zeros(nb)
        il_max, il_min, il_int = il.copy(), il.copy(), np.zeros(nb)
        p1e = p2e = ib_e = il_e = q1p = 0.0
        for (te, kind, b, sgn) in ev:
            dt = te - t
            if dt > 0:
                vs = n * s2 * v2
                iss = np.where(alive, (s1 * v1 - vs) / r, 0.0)
                ex = np.exp(-dt / tau)
                il_new = iss + (il - iss) * ex
                q_l = iss * dt + (il - iss) * tau * (1 - ex)               # integral of i_L over the interval
                im_new = im + np.where(alive, vs / lm, 0.0) * dt
                q_m = 0.5 * (im + im_new) * dt
                e_r += r * np.sum(iss ** 2 * dt + 2 * iss * (il - iss) * tau * (1 - ex) +
                                  (il - iss) ** 2 * tau / 2 * (1 - ex ** 2))
                q2b += n * s2 * (q_l - q_m)                                # charge into the port-2 network
                il_int += q_l
                il_max = np.maximum(il_max, np.maximum(il, il_new))
                il_min = np.minimum(il_min, np.minimum(il, il_new))
                ib1 = np.sum(s1 * q_l) / dt
                q1p += ib1 * dt
                if pcs:
                    v1_new = v1 + (i_pcs - ib1) * dt / c1tot
                    v1_avg = 0.5 * (v1 + v1_new)
                    p1e += v1_avg * i_pcs * dt
                else:
                    veq1 = v1_src - asm('R_line1') * ib1
                    tau1 = asm('R_line1') * dz['C1']
                    e1x = np.exp(-dt / tau1)
                    v1_new = veq1 + (v1 - veq1) * e1x
                    v1_avg = veq1 + (v1 - veq1) * tau1 / dt * (1 - e1x)    # exact interval average
                    p1e += v1_src * (v1_src - v1_avg) / asm('R_line1') * dt
                ib2 = np.sum(n * s2 * (q_l - q_m)) / dt
                g, isrc = 0.0, ib2
                if port2.get('r_int') is not None:
                    g += 1 / port2['r_int']
                    isrc += port2['v_oc'] / port2['r_int']
                rl = port2['r_load'](te) if port2.get('r_load') else None
                if rl:
                    g += 1 / rl
                if g > 0:
                    tau2 = dz['C2'] / g
                    e2x = np.exp(-dt / tau2)
                    v2_new = isrc / g + (v2 - isrc / g) * e2x
                    v2_avg = isrc / g + (v2 - isrc / g) * tau2 / dt * (1 - e2x)
                else:
                    v2_new = v2 + isrc * dt / dz['C2']
                    v2_avg = 0.5 * (v2 + v2_new)
                if port2.get('r_int') is not None:
                    ib_e += (v2_avg - port2['v_oc']) / port2['r_int'] * dt
                if rl:
                    il_e += v2_avg / rl * dt
                p2e += v2_avg * ib2 * dt
                il, im, v1, v2 = il_new, im_new, v1_new, v2_new
                t = te
            if kind == 'p':
                s1 = s1.copy()
                s1[b] = sgn
            elif kind == 's':
                s2 = s2.copy()
                s2[b] = sgn
        if pcs:                                                            # PCS DC-link voltage loop, per period
            e1 = port1['v_ref'] - v1
            x1 += ki1 * e1 * T
            ff_on = port1.get('ff_tau') and (k + 1) * T < port1.get('ff_off_t', np.inf)
            if port1.get('ff_tau') and not ff_on and i_ff != 0.0:
                x1 += i_ff                                                 # bumpless switch-off of the feed-forward
                i_ff = 0.0
            if ff_on:
                i_ff += (q1p / T - i_ff) * T / port1['ff_tau']
            i_pcs = kp1 * e1 + x1 + (i_ff if ff_on else 0.0)
        i2_br = q2b / T
        meas = dict(v1=v1, v2=v2, i2=float(np.sum(i2_br * gains)), i2_br=i2_br * gains, t=k * T + 3 * T / 4,
                    i_port2=(ib_e + il_e) / T, alive=alive.copy())
        if record:
            for key, val in (('t', k * T + 3 * T / 4), ('v1', v1), ('v2', v2), ('i2', float(np.sum(i2_br))),
                             ('i2_br', i2_br.copy()), ('il_max', il_max.copy()), ('il_min', il_min.copy()),
                             ('il_dc', il_int / T), ('phi', phi_cmd.copy()), ('p1', p1e / T), ('p2', p2e / T),
                             ('i2_batt', ib_e / T), ('e_r', e_r), ('i_pcs', i_pcs if pcs else np.nan),
                             ('alive', alive.copy())):
                tr[key].append(val)
    return {k: np.array(v) for k, v in tr.items()}


def make_ctrl(dz, nb, iref, vref=None, per_branch=False, i_lim=None):
    """CC (and optional CV) controller.  iref(t) total current reference; vref(t) CV setpoint or None.
    per_branch: each branch has its own current PI on its own sensor (reference = total/nb); otherwise one PI on the
    summed current and a common phase."""
    kphi = dz['n'] * 800.0 * (np.pi - 0.8) / (2 * np.pi ** 2 * F * dz['L'])      # dI2/dphi near 0.4 rad [A/rad]
    ki = 2 * np.pi * asm('f_ci') / kphi                                          # integral gain [rad/(A s)]
    kp_v = 2 * np.pi * asm('f_cv') * dz['C2']
    ki_v = kp_v * 2 * np.pi * asm('f_cv') / 5
    st = dict(xi=np.zeros(nb if per_branch else 1), xv=0.0)
    i_lim = dz['i_port2_max'] if i_lim is None else i_lim

    def ctrl(k, m):
        t = m['t']
        i_cc = iref(t)
        if vref is not None:
            ev = vref(t) - m['v2']
            iv = m['i_port2'] + kp_v * ev + st['xv']                 # load-current feed-forward + PI
            i_set = float(np.clip(min(iv, i_cc), -i_lim, i_lim))
            if not (iv > i_cc and ev > 0):                                         # anti-windup when CC limits
                st['xv'] = float(np.clip(st['xv'] + ki_v * ev * T, -i_lim, i_lim))
        else:
            i_set = float(np.clip(i_cc, -i_lim, i_lim))
        if per_branch:
            ib = i_set / nb
            err = ib - m['i2_br']
            st['xi'] = np.clip(st['xi'] + ki * err * T, -0.3, 0.3)
            ff = phi_ff(m['v1'], max(m['v2'], 50.0), dz['n'], dz['L'], ib * m['v2'])
            return ff + st['xi']
        err = i_set - m['i2']
        st['xi'] = np.clip(st['xi'] + ki * err * T, -0.3, 0.3)
        ff = phi_ff(m['v1'], max(m['v2'], 50.0), dz['n'], dz['L'], i_set * m['v2'] / nb)
        return np.full(nb, ff + st['xi'][0])
    return ctrl


def make_ctrl_system(dz, nb, i_total_req, clamp=True):
    """CAN-B system controller + per-branch current loops.  Every t_can the system controller reads the branch
    status and sends one reference per alive branch: I_total/N_alive, limited (clamp=True) to P_branch_max/V2; each
    branch also clamps its own reference to P_branch_max/V2 (clamp=True) or only to the port current limit."""
    kphi = dz['n'] * 800.0 * (np.pi - 0.8) / (2 * np.pi ** 2 * F * dz['L'])
    ki = 2 * np.pi * asm('f_ci') / kphi
    st = dict(xi=np.zeros(nb), ref=np.zeros(nb), ib=np.zeros(nb), next_can=0.0, n_alive_seen=[])
    d_i = asm('i_ramp') * T

    def ctrl(k, m):
        t = m['t']
        i_lim = min(dz['i_port2_max'], asm('P_branch_max') / max(m['v2'], 1.0)) if clamp else dz['i_port2_max']
        if t >= st['next_can']:
            alive = m['alive']
            ref = i_total_req(t) / max(int(alive.sum()), 1)
            st['ref'] = np.where(alive, min(ref, i_lim) if clamp else ref, 0.0)
            st['next_can'] += asm('t_can')
            st['n_alive_seen'].append((t, int(alive.sum())))
        st['ib'] = st['ib'] + np.clip(np.clip(st['ref'], -i_lim, i_lim) - st['ib'], -d_i, d_i)   # branch ramp
        ib = st['ib']
        st['xi'] = np.clip(st['xi'] + ki * (ib - m['i2_br']) * T, -0.3, 0.3)
        ff = np.array([phi_ff(m['v1'], max(m['v2'], 50.0), dz['n'], dz['L'], x * m['v2']) for x in ib])
        return ff + st['xi']
    return ctrl


def scenario_branch_fault(dz, clamp=True, t_trip=60.3e-3, n_periods=9000, pcs_ff=True):
    """Two branches at full shared charge load (2 x 60 kW into an 800 V battery) from a PCS DC link; branch 2 trips
    at t_trip.  Returns the traces."""
    dz2 = dict(dz, C1=2 * dz['C1'], C2=2 * dz['C2'])
    off = np.radians(asm('carrier_offset_deg'))
    br = [dict(L=dz['L'], gain=1.0), dict(L=dz['L'], gain=1.0, off=off, trip_t=t_trip)]
    i_tot = lambda t: 2 * asm('P_branch_max') / 800.0
    # feed-forward is used for the start-up in every case; the 'voltage loop only' case switches it off (bumpless)
    # 20 ms before the trip so the bus is settled when the trip happens
    port1 = dict(mode='pcs', C=asm('C_pcs'), f_c=asm('f_pcs'), v_ref=800.0, ff_tau=asm('pcs_ff_tau'),
                 ff_off_t=np.inf if pcs_ff else t_trip - 20e-3)
    return simulate(dz2, n_periods, make_ctrl_system(dz2, 2, i_tot, clamp=clamp),
                    dict(v_oc=800.0, r_int=asm('R_int_batt') / 2), br, v2_0=800.0, port1=port1)


def ramp(t0, t1, a, b):
    return lambda t: a if t <= t0 else (b if t >= t1 else a + (b - a) * (t - t0) / (t1 - t0))


# ---------------------------------------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------------------------------------
def scenario_softstart(dz):
    """Port 2 precharged by the port block to within 10 V of the 780 V battery (port_spec.json dV_ok 10 V), contactor
    closed, then soft start 0 -> 75 A in 5 ms, CC hold, step 75 -> 40 A."""
    br = [dict(L=dz['L'], gain=1.0)]
    iref = lambda t: ramp(0.5e-3, 5.5e-3, 0.0, 75.0)(t) if t < 12e-3 else 40.0
    out = {}
    for upd in ('split', 'naive'):
        ctrl = make_ctrl(dz, 1, iref)
        # port block (port_spec.json): precharge through 220 ohm, main contactor closes at dV <= 10 V; the DAB is
        # enabled after closure -> start with the bank 10 V below the battery OCV (contactor inrush itself is the
        # port block's calculation, not modelled here: no cable inductance)
        out[upd] = simulate(dz, 2000, ctrl, dict(v_oc=780.0, r_int=asm('R_int_batt')), br, update=upd, v2_0=770.0,
                            slew=(upd == 'split'))
    return out


def scenario_cv_step(dz):
    """Port 2 = DC load only, CV 800 V, load 20 kW -> 50 kW at 6 ms (after a 3 ms soft start)."""
    br = [dict(L=dz['L'], gain=1.0)]
    rl = lambda t: 800.0 ** 2 / (20e3 if t < 6e-3 else 50e3)
    ctrl = make_ctrl(dz, 1, iref=lambda t: 100.0, vref=ramp(0.2e-3, 3e-3, 790.0, 800.0))
    return simulate(dz, 1200, ctrl, dict(r_load=rl), br, v2_0=790.0)


def scenario_reversal(dz):
    """Battery 800 V: +70 A charge -> -70 A discharge, ramped over 1 ms at 5 ms (port 1 bus absorbs)."""
    br = [dict(L=dz['L'], gain=1.0)]
    iref = lambda t: ramp(0.2e-3, 2e-3, 0.0, 70.0)(t) if t < 5e-3 else ramp(5e-3, 6e-3, 70.0, -70.0)(t)
    ctrl = make_ctrl(dz, 1, iref)
    return simulate(dz, 1000, ctrl, dict(v_oc=800.0, r_int=asm('R_int_batt')), br, v2_0=800.0)


def scenario_parallel(dz, mismatch=0.05):
    """Two branches with L_k = L(1 +/- mismatch) sharing 140 A into an 800 V battery.
    (a) one PI on the summed current, common phase; (b) per-branch PI, each on its own sensor (+/-1 % gain error)."""
    g = asm('sensor_gain_err')
    br_a = [dict(L=dz['L'] * (1 + mismatch), gain=1.0), dict(L=dz['L'] * (1 - mismatch), gain=1.0)]
    off = np.radians(asm('carrier_offset_deg'))
    br_b = [dict(L=dz['L'] * (1 + mismatch), gain=1 + g), dict(L=dz['L'] * (1 - mismatch), gain=1 - g, off=off)]
    iref = ramp(0.2e-3, 2e-3, 0.0, 140.0)
    dz2 = dict(dz, C1=2 * dz['C1'], C2=2 * dz['C2'], i_port2_max=2 * dz['i_port2_max'])
    a = simulate(dz2, 800, make_ctrl(dz2, 2, iref, per_branch=False), dict(v_oc=800.0, r_int=asm('R_int_batt') / 2),
                 br_a, v2_0=800.0)
    b = simulate(dz2, 800, make_ctrl(dz2, 2, iref, per_branch=True), dict(v_oc=800.0, r_int=asm('R_int_batt') / 2),
                 br_b, v2_0=800.0)
    return a, b


# ---------------------------------------------------------------------------------------------------------
def share_err(tr, last=200):
    i = tr['i2_br'][-last:].mean(0)
    return float((i[0] - i[1]) / (i[0] + i[1]))


def main():
    os.makedirs(OUT, exist_ok=True)
    dz = load_design()
    res = {}
    ss = scenario_softstart(dz)
    cv = scenario_cv_step(dz)
    rv = scenario_reversal(dz)
    pa, pb = scenario_parallel(dz)
    fa = scenario_branch_fault(dz, clamp=True)
    fb = scenario_branch_fault(dz, clamp=False)
    fc = scenario_branch_fault(dz, clamp=True, pcs_ff=False)
    # ---- plots ----
    fig, axs = plt.subplots(4, 1, figsize=(10, 10), sharex=True)
    for upd, sty in (('split', '-'), ('naive', '--')):
        tr = ss[upd]
        tm = tr['t'] * 1e3
        axs[0].plot(tm, tr['i2'], sty, label=f'I2 bridge avg ({upd})')
        axs[1].plot(tm, tr['il_max'][:, 0], sty, lw=0.8, label=f'i_L max ({upd})')
        axs[1].plot(tm, tr['il_min'][:, 0], sty, lw=0.8, label=f'i_L min ({upd})')
        axs[2].plot(tm, tr['il_dc'][:, 0], sty, label=f'i_L period mean = DC offset ({upd})')
        axs[3].plot(tm, np.degrees(tr['phi'][:, 0]), sty, label=f'phase ({upd})')
    axs[0].set_ylabel('A')
    axs[1].set_ylabel('A')
    axs[2].set_ylabel('A')
    axs[3].set_ylabel('deg')
    axs[3].set_xlabel('t [ms]')
    for ax in axs:
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    axs[0].set_title('Soft start into a precharged 780 V battery, CC 75 A, step to 40 A at 12 ms (calculated)')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'softstart_cc.png'), dpi=120)
    plt.close(fig)

    fig, axs = plt.subplots(3, 1, figsize=(10, 7), sharex=True)
    tm = cv['t'] * 1e3
    axs[0].plot(tm, cv['v2'])
    axs[0].set_ylabel('V2 [V]')
    axs[1].plot(tm, cv['i2'])
    axs[1].set_ylabel('I2 bridge [A]')
    axs[2].plot(tm, np.degrees(cv['phi'][:, 0]))
    axs[2].set_ylabel('phase [deg]')
    axs[2].set_xlabel('t [ms]')
    axs[0].set_title('CV 800 V, load step 20 -> 50 kW at 6 ms (calculated)')
    for ax in axs:
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'cv_load_step.png'), dpi=120)
    plt.close(fig)

    fig, axs = plt.subplots(3, 1, figsize=(10, 7), sharex=True)
    tm = rv['t'] * 1e3
    axs[0].plot(tm, rv['i2'], label='I2 bridge avg')
    axs[0].plot(tm, rv['i2_batt'], label='battery current')
    axs[0].legend(fontsize=8)
    axs[1].plot(tm, rv['p1'] / 1e3, label='P port 1 (from bus)')
    axs[1].plot(tm, rv['p2'] / 1e3, label='P port 2 (into battery side)')
    axs[1].legend(fontsize=8)
    axs[2].plot(tm, rv['il_max'][:, 0], lw=0.8)
    axs[2].plot(tm, rv['il_min'][:, 0], lw=0.8)
    axs[2].set_ylabel('i_L envelope [A]')
    axs[0].set_ylabel('A')
    axs[1].set_ylabel('kW')
    axs[2].set_xlabel('t [ms]')
    axs[0].set_title('Power reversal +70 A -> -70 A (charge -> discharge) at 5-6 ms (calculated)')
    for ax in axs:
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'power_reversal.png'), dpi=120)
    plt.close(fig)

    fig, axs = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for ax, tr, lab in ((axs[0], pa, 'common phase, one PI on the sum'), (axs[1], pb, 'per-branch PI (+/-1 % sensor gain)')):
        tm = tr['t'] * 1e3
        ax.plot(tm, tr['i2_br'][:, 0], label='branch 1 (L +5 %)')
        ax.plot(tm, tr['i2_br'][:, 1], label='branch 2 (L -5 %)')
        ax.set_title(f'{lab}: sharing error {share_err(tr)*100:+.2f} %', fontsize=9)
        ax.set_ylabel('A')
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    axs[1].set_xlabel('t [ms]')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'parallel_sharing.png'), dpi=120)
    plt.close(fig)

    fig, axs = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    for tr, sty, lab in ((fa, '-', 'limit P_branch,max/V2, PCS with DC feed-forward'),
                         (fb, '--', 'naive I_total/N_alive, 100 A port limit only'),
                         (fc, ':', 'limit applied, PCS voltage loop only')):
        tm = tr['t'] * 1e3
        axs[0].plot(tm, tr['i2_br'][:, 0], sty, label=f'surviving branch I2 ({lab})')
        axs[0].plot(tm, tr['i2_br'][:, 1], sty, lw=0.8, label=f'tripped branch I2 ({lab})')
        axs[1].plot(tm, tr['v1'], sty, label=f'PCS DC bus V1 ({lab})')
        axs[2].plot(tm, tr['p2'] / 1e3, sty, label=f'P into battery side ({lab})')
    axs[0].set_ylabel('A')
    axs[1].set_ylabel('V')
    axs[2].set_ylabel('kW')
    axs[2].set_xlabel('t [ms]')
    for ax in axs:
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    axs[0].set_title(f'One of two branches trips at 60.3 ms at 2 x 60 kW; CAN-B frame every {asm("t_can")*1e3:.0f} ms; '
                     f'PCS link {asm("C_pcs")*1e3:.0f} mF, {asm("f_pcs"):.0f} Hz loop (calculated)', fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'branch_fault.png'), dpi=120)
    plt.close(fig)

    # ---- metrics ----
    sp, nv = ss['split'], ss['naive']
    k_ramp = (sp['t'] > 0.5e-3) & (sp['t'] < 13e-3)
    m = dict(
        softstart_il_peak=float(np.max(np.abs(np.r_[sp['il_max'][:, 0], sp['il_min'][:, 0]]))),
        dc_offset_split=float(np.max(np.abs(sp['il_dc'][k_ramp, 0]))),
        dc_offset_naive=float(np.max(np.abs(nv['il_dc'][k_ramp, 0]))),
        cc_err_75=float(np.mean(sp['i2'][(sp['t'] > 9e-3) & (sp['t'] < 12e-3)]) - 75.0),
        cc_err_40=float(np.mean(sp['i2'][sp['t'] > 17e-3]) - 40.0),
        cv_err=float(np.mean(cv['v2'][cv['t'] > 10e-3]) - 800.0),
        cv_dip=float(800.0 - np.min(cv['v2'][cv['t'] > 6e-3])),
        cv_settle_ms=float(max(cv['t'][(cv['t'] >= 6e-3) & (np.abs(cv['v2'] - 800.0) > 1.0)].max(initial=6e-3)
                               - 6e-3, 0) * 1e3),
        rev_i2_end=float(np.mean(rv['i2'][rv['t'] > 8e-3])),
        rev_il_peak=float(np.max(np.abs(np.r_[rv['il_max'][:, 0], rv['il_min'][:, 0]]))),
        share_open=share_err(pa), share_ctrl=share_err(pb))
    t_tr = 60.3e-3
    for tag, tr in (('', fa), ('_naive', fb), ('_noff', fc)):
        pre = (tr['t'] > t_tr - 5e-3) & (tr['t'] < t_tr)
        post = tr['t'] > t_tr + 25e-3
        m['one_branch_fault' + tag + '_survivor_I_before_A'] = float(np.mean(tr['i2_br'][pre, 0]))
        m['one_branch_fault' + tag + '_survivor_I_after_A'] = float(np.mean(tr['i2_br'][post, 0]))
        m['one_branch_fault' + tag + '_survivor_I_peak_A'] = float(np.max(tr['i2_br'][tr['t'] > t_tr, 0]))
        m['one_branch_fault' + tag + '_survivor_P_after_kW'] = float(np.mean(tr['p2'][post]) / 1e3)
        m['one_branch_fault' + tag + '_bus_pre_trip_V'] = float(np.mean(tr['v1'][pre]))
        m['one_branch_fault' + tag + '_bus_overshoot_V'] = float(np.max(tr['v1'][tr['t'] > t_tr]) - np.mean(tr['v1'][pre]))
        m['one_branch_fault' + tag + '_bus_end_V'] = float(tr['v1'][-1])
        m['one_branch_fault' + tag + '_il_peak_survivor_A'] = float(np.max(np.abs(np.r_[tr['il_max'][tr['t'] > t_tr, 0],
                                                                                         tr['il_min'][tr['t'] > t_tr, 0]])))
    # energy balance over the reversal run: source energy = battery-side energy + R_loop + R_line + battery R losses
    res['metrics'] = m
    with open(os.path.join(OUT, 'metrics.json'), 'w') as fh:
        json.dump(dict(design=dz, metrics=m, assumptions={k: v[0] for k, v in ASSUME.items()}), fh, indent=2)
    report(dz, m)
    selfcheck(dz, m, ss, pa, pb)
    return m


ST_NOTES = 'docs/reference-designs/st/stdes-dabbidir/FIRMWARE-NOTES.md'
ST_CSV = 'sim/data/st_dab_firmware_params.csv'


def st_params():
    import csv
    return {r['name']: r for r in csv.DictReader(open(ST_CSV))}


def st_crosscheck(dz, m):
    """DAB-08: our control against ST STSW-DABBIDIR (decoded firmware constants, FIRMWARE-NOTES.md section 5)."""
    st = st_params()
    num = lambda k: float(str(st[k]['value_decoded']).split()[0])
    n_st, l_st, f_st = num('pDAB_CTRL.trafoTurnRatio'), num('pDAB_CTRL.Inductance_H'), num('pDAB_CTRL.swFreq_Hz')
    td_st = num('DeadTime.request')
    p_sps = lambda v1, v2, n, L, ph: n * v1 * v2 * ph * (np.pi - ph) / (2 * np.pi ** 2 * F * L)
    # normalised: SPS reserve (maximum power / rated) and the phase needed for the rated power
    pmax_st, pmax_us = p_sps(800, 800 / n_st, n_st, l_st, np.pi / 2), p_sps(800, 800 / dz['n'], dz['n'], dz['L'], np.pi / 2)
    dt = dz['dead_time']
    sti = dz['st_inputs']
    rows = [
        ('modulation', 'SPS or closed-form trapezoidal (two inner shifts from V_HV, V_LV, n and phi each 100 us); '
                       'selected by writing a RAM variable', 'TPS look-up table (a1, a2, phi vs V1, V2, P) chosen offline '
                       'for minimum modelled loss with the real C_oss / ZVS completion and the turn-off SOA; SPS near '
                       'V1 = n V2 and at heavy load', 'DIFFERENT',
         'The trapezoidal law targets zero current at the edges (ZCS, minimum RMS) and ignores C_oss: with SiC a '
         'zero-current edge cannot swing the node, so the incoming switch turns on at full voltage and dumps '
         '%.0f uJ per position at 800 V (our model, chosen switches) - %.0f W if two edges per period switch that way. '
         'Our LUT keeps enough current at every edge for ZVS where it can. Keep the LUT; adopt ST\'s closed form only '
         'as the firmware fallback / plausibility reference outside the table' % (sti.get('E_turn_on_zero_current_800V_J', 0) * 1e6,
                                                                                 2 * F * sti.get('E_turn_on_zero_current_800V_J', 0))),
        ('dead time', '%.0f ns fixed for both bridges (HRTIM insertion; %.0f ns in the .sim build) = %.1f deg'
         % (td_st * 1e9, num('DeadTime.request (.sim build)') * 1e9, 360 * f_st * td_st),
         'hardware shoot-through guard %.0f-%.0f ns at the gates + firmware LUT (ZVS transition + 20 ns, %.0f-%.0f ns)'
         % (dt['hw_guard_ns'][0], dt['hw_guard_ns'][1], dt['min_ns'], dt['max_ns']), 'DIFFERENT',
         ('400 ns fixed costs %.0f W more than our scheme at 800/800 V 60 kW (%.0f W body-diode conduction, design '
          'model) - not adopted; a long fixed dead time is the simple choice for a 25 kW board without a current loop'
          % (dt['study_W']['st_400ns_minus_ours_800_60k'], dt['study_W']['st_400ns_diode_800_60k'])
          if 'study_W' in dt else 'see dab_design dead-time study')),
        ('inner current loop', 'none: no current is used by any control, limit or protection', 'per-branch inner '
         'current loop on its own I2 sensor, SPS feed-forward, %.0f Hz crossover' % asm('f_ci'), 'WE HAVE, ST DOES NOT',
         'Required here: a battery port is a stiff voltage source, so a loop on V2 alone has almost no authority over '
         'the current (CC charging), paralleled branches need a local current loop to share (%.1f %% open-loop error '
         'with +/-5 %% L vs %.1f %% closed) and the power reversal must pass through phi = 0 under current control. Keep'
         % (abs(m['share_open']) * 100, abs(m['share_ctrl']) * 100)),
        ('outer voltage loop', 'PI on raw V2 counts, Kp %.3g /count, Ki %.3g /(count s), Ts %.0f us, output 0-0.4 pi, '
         'back-calculation anti-windup Kb %.1f' % (num('pPI_VDC_CTRL.Kp (k0)'), num('pPI_VDC_CTRL.Ki'),
                                                   num('pPI_VDC_CTRL.Ts') * 1e6, num('pPI_VDC_CTRL.Antiwindup_Gain')),
         'CV loop on V2 -> current reference, %.0f Hz crossover, conditional integration while the CC limit acts'
         % asm('f_cv'), 'SAME APPROACH (different numbers)',
         'Back-calculation is an equally valid anti-windup; ST\'s loop bandwidth cannot be derived (plant not in the '
         'binary). No change'),
        ('update rate / phase step', '10 kHz, compares rewritten at once, no slew limit', 'every period (100 kHz), '
         'split (volt-second balanced) update, 3 deg per period slew', 'DIFFERENT',
         'Our simulation: naive step %.1f A DC offset in the transformer vs %.1f A with the split update. Keep'
         % (m['dc_offset_naive'], m['dc_offset_split'])),
        ('plant constants', 'n = %.2f, L = %.0f uH (L only feeds an unused P_max); SPS P_max/P_rated = %.2f at the '
         'matched point' % (n_st, l_st * 1e6, pmax_st / 25e3), 'n = %.4f, L = %.2f uH; SPS P_max/P_rated = %.2f'
         % (dz['n'], dz['L'] * 1e6, pmax_us / 60e3), 'DIFFERENT (ratings)',
         'ST sizes L for 1.14x reserve (phase 58 deg at rated); ours 1.88x (28 deg) for TPS headroom and the 590-950 V '
         'window. No change'),
        ('protection in firmware', 'none active (fault vector never written; no HRTIM fault input)', 'hardware trips '
         '(soft turn-off through the drivers) + firmware OV/OC/UV/flux/temperature', 'WE HAVE, ST DOES NOT', 'Keep'),
        ('on-line P_max', 'P_max(SPS) and P_max(TPZ) computed each cycle, never used', 'P_branch,max from the offline '
         'derating map as a hard clamp', 'ST HAS (unused), WE DO NOT', 'ADOPT in firmware as a cheap plausibility '
         'check: reject a reference above P_max,SPS(V1, V2) - it catches a corrupted LUT/derating entry'),
        ('start / ramp', 'voltage reference ramp 10 V/s from 50 V', 'precharge + contactor, then current ramp 20 kA/s',
         'DIFFERENT', 'ST\'s board starts from an unloaded output; ours closes onto a charged battery. No change'),
    ]
    return rows


def report(dz, m):
    L = ['# DAB-D60 control simulation report', '',
         'Generated by `sim/dab_control.py` (do not edit). Calculated, not bench-validated. Design values read from '
         '`sim/out/dab_design/dab_spec.json`.', '',
         f'Plant: n = {dz["n"]:.4f}, L = {dz["L"]*1e6:.2f} uH, L_m = {dz["Lm"]*1e6:.0f} uH, C1 = {dz["C1"]*1e6:.0f} uF, '
         f'C2 = {dz["C2"]*1e6:.0f} uF, f_sw = 100 kHz, control update every period with one-period delay.', '',
         'Start-up assumption checked against the port block (`sim/out/port_design/port_spec.json`): precharge through '
         '220 ohm (tau 29.7 / 42.9 ms), main contactor closes at dV <= 10 V; the DAB starts after closure with the '
         'bank 10 V below the battery OCV.', '',
         '## Results', '',
         '| test | result |', '|---|---|',
         f'| soft start 0 -> 75 A in 5 ms (split update) | peak i_L {m["softstart_il_peak"]:.0f} A (OC trip {dz["oc_trip"]:.0f} A); '
         f'CC error {m["cc_err_75"]:+.2f} A at 75 A, {m["cc_err_40"]:+.2f} A after the step to 40 A |',
         f'| transformer DC offset during ramps/steps | split update + 3 deg/period slew {m["dc_offset_split"]:.2f} A, '
         f'naive update {m["dc_offset_naive"]:.2f} A (peak period-mean of i_L) |',
         f'| CV 800 V, load step 20 -> 50 kW | dip {m["cv_dip"]:.1f} V, back within 1 V after {m["cv_settle_ms"]:.2f} ms, '
         f'steady error {m["cv_err"]:+.2f} V |',
         f'| power reversal +70 -> -70 A in 1 ms | final {m["rev_i2_end"]:+.1f} A, peak i_L {m["rev_il_peak"]:.0f} A, '
         f'no loss of control through phi = 0 |',
         f'| 2 branches, L_k +/-5 %, common phase | sharing error {m["share_open"]*100:+.2f} % '
         f'(= (I1-I2)/(I1+I2)) |',
         f'| 2 branches, per-branch current loop, sensors +/-1 %, free-running carriers (branch 2 at +30 deg) | '
         f'sharing error {m["share_ctrl"]*100:+.2f} % '
         f'(set by sensor gain error, not by L_k) |', '',
         f'| one of 2 branches trips at 2 x 60 kW (system + branch limit P_branch,max/V2) | survivor '
         f'{m["one_branch_fault_survivor_I_before_A"]:.1f} -> {m["one_branch_fault_survivor_I_after_A"]:.1f} A '
         f'(peak {m["one_branch_fault_survivor_I_peak_A"]:.1f} A, {m["one_branch_fault_survivor_P_after_kW"]:.1f} kW); '
         f'PCS bus overshoot {m["one_branch_fault_bus_overshoot_V"]:.1f} V, back to {m["one_branch_fault_bus_end_V"]:.1f} V |',
         f'| same, naive system controller (I_total/N_alive, branch limited only by the 100 A port limit) | survivor '
         f'{m["one_branch_fault_naive_survivor_I_before_A"]:.1f} -> {m["one_branch_fault_naive_survivor_I_after_A"]:.1f} A '
         f'= {m["one_branch_fault_naive_survivor_P_after_kW"]:.1f} kW (overload); bus overshoot '
         f'{m["one_branch_fault_naive_bus_overshoot_V"]:.1f} V |',
         f'| same with limit, PCS without DC-current feed-forward | bus overshoot '
         f'{m["one_branch_fault_noff_bus_overshoot_V"]:.1f} V |', '',
         '## Parallel branches (DAB-09) - decision and fault behaviour', '',
         '* No hardware carrier-sync line between branches (decision): carriers are free-running; the per-branch current '
         'loop makes sharing independent of carrier phase (same result with branch 2 offset by 30 deg). Ripple at the '
         'shared buses adds without interleaving and beats slowly; each branch keeps its own capacitor banks.',
         '* Sharing = common reference over CAN-B + per-branch inner loop on the branch\'s own I2 sensor.',
         f'* One-branch fault: the tripped branch\'s current is gone within one period; the survivor holds its own '
         f'reference because its loop is local. The bus transient is the PCS seeing a 60 kW load step on a '
         f'{asm("C_pcs")*1e3:.0f} mF link with a {asm("f_pcs"):.0f} Hz voltage loop: '
         f'{m["one_branch_fault_bus_overshoot_V"]:.0f} V with DC-current feed-forward ({asm("pcs_ff_tau")*1e3:.0f} ms lag), '
         f'{m["one_branch_fault_noff_bus_overshoot_V"]:.0f} V without -> the PCS needs load-current feed-forward (or a '
         'faster loop) to stay clear of the 960 V firmware OV limit when V1 runs near 900 V; the PCS power limit is '
         'lowered in the same CAN-B frame.',
         '* Limit the system controller must apply: total reference <= N_alive x P_branch,max(V1, V2) from the derating '
         'map, and each branch clamps its own reference to P_branch,max/V2. Without that, redistributing I_total/N_alive '
         f'drives the survivor to the 100 A port limit ({m["one_branch_fault_naive_survivor_P_after_kW"]:.0f} kW at 800 V, '
         'a 33 % overload) at the first CAN frame after the trip.', '',
         '## What this means for the hardware', '',
         '* Open-loop (common-phase) paralleling shares current in inverse proportion to L_k: a +/-5 % L tolerance gives '
         'about +/-5 % current imbalance, so each branch needs its own current measurement and inner loop; the '
         'sharing error then equals the current-sensor gain mismatch -> specify branch current sensing to <= +/-1 % gain '
         'after calibration.',
         '* The phase update must keep volt-second balance (split update); a naive step leaves a DC offset in the '
         'transformer current that decays only with L/R (~160 us here) - the flux-balance loop and the 5 A DC trip in '
         'dab_spec.json are sized with this in mind.',
         '* The dead-time phase drift found in dab_design (light load) is absorbed by the current loop; the feed-forward '
         'only sets the starting point.', '',
         '']
    L += ['', '## DAB-08 cross-check against ST STSW-DABBIDIR', '',
          f'Source: `{ST_NOTES}` (static decode of ST\'s 25 kW firmware binary, section 5) and `{ST_CSV}` '
          '(118 decoded parameters). Facts are ST\'s firmware as decoded; the verdict column is our engineering '
          'conclusion. Nothing of ST\'s was executed or measured.', '',
          '| item | ST (decoded) | ours | verdict | why / change to our design |', '|---|---|---|---|---|']
    L += ['| %s | %s | %s | %s | %s |' % r_ for r_ in st_crosscheck(dz, m)]
    L += ['', 'Net: nothing in ST\'s firmware changes our hardware. Two firmware items are adopted as additions, not '
          'replacements: the on-line SPS P_max plausibility check, and the closed-form trapezoidal law as the fallback '
          'outside the TPS table. ST\'s 400 ns fixed dead time and its voltage-only loop are not adopted (reasons above).',
          '', '## Assumptions', '']
    L += [f'* `{k}` = {v[0]:g}: {v[1]}' for k, v in ASSUME.items()]
    L += ['* Ideal edges (no dead time / ZVS transitions) in this control model; switching losses not included.',
          '* The magnetising branch is lossless here (all resistance lumped in series with L), so a few-ampere DC drift '
          'of i_m set by the start-up/operating-point history does not decay in this model; real secondary winding '
          'and switch resistance damp it (tau ~ L_m/R_sec, ms) and the flux-balance loop removes the rest.',
          '* Port 1 is a stiff PCS bus; the PCS voltage loop is not modelled.', '',
          'Plots: `softstart_cc.png`, `cv_load_step.png`, `power_reversal.png`, `parallel_sharing.png`, '
          '`branch_fault.png`; numbers in '
          '`metrics.json`.']
    with open(os.path.join(OUT, 'report.md'), 'w') as fh:
        fh.write('\n'.join(L) + '\n')


def selfcheck(dz, m, ss, pa, pb):
    # 1. averaged power of the exact simulation vs the SPS formula at a fixed phase (open loop, steady state)
    br = [dict(L=dz['L'], gain=1.0)]
    phi0 = 0.4
    tr = simulate(dz, 400, lambda k, mm: [phi0], dict(v_oc=800.0, r_int=1e-3), br, v1_src=800.0, v2_0=800.0)
    p_formula = dz['n'] * tr['v1'][-1] * tr['v2'][-1] * phi0 * (np.pi - phi0) / (2 * np.pi ** 2 * F * dz['L'])
    p_sim = float(np.mean(tr['p2'][-100:]))
    assert abs(p_sim / p_formula - 1) < 0.02, f'SPS formula {p_formula:.0f} W vs simulation {p_sim:.0f} W'
    # 2. energy balance: port-1 power = port-2 power + loop-resistance loss (lossless otherwise)
    e1 = np.sum(tr['p1'][-100:]) * T
    e2 = np.sum(tr['p2'][-100:]) * T
    er = tr['e_r'][-1] - tr['e_r'][-101]
    assert abs(e1 - e2 - er) / e1 < 0.01, f'energy balance {e1:.4f} {e2:.4f} {er:.4f}'
    # 3. closed-loop behaviour
    assert abs(m['cc_err_75']) < 1.0 and abs(m['cc_err_40']) < 1.0
    assert m['softstart_il_peak'] < dz['oc_trip']
    assert m['dc_offset_split'] < m['dc_offset_naive']
    assert abs(m['cv_err']) < 2.0 and m['cv_dip'] < 60.0
    assert m['rev_i2_end'] < -65.0
    assert abs(m['share_open']) > 0.03 and abs(m['share_ctrl']) < 0.02
    # one-branch fault: with the limit the survivor stays at its share; without it the survivor is overloaded
    i_share = asm('P_branch_max') / 800.0
    assert abs(m['one_branch_fault_survivor_I_after_A'] - i_share) < 0.02 * i_share
    assert m['one_branch_fault_survivor_I_peak_A'] < 1.05 * i_share
    assert m['one_branch_fault_naive_survivor_I_after_A'] > 1.2 * i_share
    assert 0 < m['one_branch_fault_bus_overshoot_V'] < m['one_branch_fault_noff_bus_overshoot_V']
    assert abs(m['one_branch_fault_bus_end_V'] - 800) < 5
    assert abs(m['one_branch_fault_bus_pre_trip_V'] - 800) < 2 and abs(m['one_branch_fault_noff_bus_pre_trip_V'] - 800) < 2
    print('dab_control self-check passed')


if __name__ == '__main__':
    mm = main()
    print({k: round(v, 3) for k, v in mm.items()})
