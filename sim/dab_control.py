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
  * Light-load policy (review PCM-12, dab_spec firmware_requirements FW-DAB-3..5): a branch idles with its gates off
    while |P_ref| < P_off and switches again at |P_ref| >= P_on (hysteresis, read from dab_spec.json).
  * Plant identity (review PCM-09): n, L, L_m and the banks are the D-014 values (common to the drawn board DAB60 rev B
    and the study); R_loop, the OC trip and the dead-time guard are those of the STUDY devices in dab_spec.json.  The
    model is SPS with ideal edges, so it is valid only near V1 = n V2 (|V1 - n V2| <= ~130 V); the TPS operation at a
    large mismatch is not time-domain simulated here.
  * PCS-P125 link (coordinator addendum, PCS control study): port 1 can be the inverter's own DC link (C_dc, its PI
    loop, feed-forward lag, OV trip and UV stop read from sim/out/pcs_design/pcs_spec.json and
    sim/out/pcs_control/pcs_control_spec.json at run time): either the PCS holds V_dc (grid following, stiff grid -
    an ideal current source behind its loop; weak-grid limits stay the PCS study's) or the DAB holds it (CV on port 1,
    off-grid) with the inverter as a constant-power load.
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
    'R_loop': (None, 'ohm primary-referred series resistance per branch - REQUIRED input, read from dab_spec.json '
                     'control_plant (no built-in substitute, PCM-23)'),
    'R_line1': (0.010, 'ohm port-1 source impedance (PCS DC bus treated as stiff)'),
    'R_int_batt': (0.060, 'ohm battery string (240S LFP ~0.25 mohm/cell)'),
    'sensor_gain_err': (0.01, 'per-branch current-sensor gain error used in the sharing test (+1 % / -1 %)'),
    'f_ci': (2000.0, 'Hz current-loop crossover'),
    'f_cv': (400.0, 'Hz voltage-loop crossover'),
    'phi_max_deg': (60.0, 'deg phase-command clamp'),
    'phi_slew_deg': (3.0, 'deg maximum phase change per switching period (modulator slew limit)'),
    'carrier_offset_deg': (30.0, 'deg relative carrier phase of branch 2 (free-running, no sync line)'),
    'C_pcs': (4e-3, 'F GENERIC PCS DC-link capacitance on port 1 (one-branch-fault case) - not PCS-P125, whose link is '
                    'read from pcs_spec.json in the PCS-P125 link cases'),
    'f_pcs': (30.0, 'Hz PCS DC-link voltage-loop crossover, no load-current feed-forward'),
    't_can': (10e-3, 's CAN-B reference/status frame period (system controller to branches)'),
    'i_ramp': (20e3, 'A/s branch-local reference ramp (firmware), applied to every CAN reference change'),
    'pcs_ff_tau': (2e-3, 's PCS DC-current feed-forward lag (PCS current loop + filter); None = voltage loop only'),
    'v_bus_pcs': (750.0, 'V PCS-P125 DC-link operating point of the link cases (the PCS control study\'s V_dc)'),
    'dpdt_pcs': (6.25e6, 'W/s largest DC/DC power ramp into the PCS-P125 link: the PCS control study\'s rule (125 kW in '
                         '20 ms) - the system total, so per branch /N_alive (ASSUMED allocation)'),
    'p_pre_cv': (0.1, 'inverter load before / after the off-grid load step, fraction of P_branch,max (ASSUMED)'),
    'f_cv_max': (500.0, 'Hz highest CV crossover the cascade allows: f_ci / 4 with the one-period delay and the 3 deg per '
                        'period slew (design rule, ASSUMED)'),
    'P_branch_max': (None, 'W per-branch power limit at the nominal point - REQUIRED input, read from dab_spec.json '
                           'derating_charge_W at V1 = V2 = 800 V'),
}


def asm(k):
    return ASSUME[k][0]


def load_design():
    """Plant from dab_spec.json.  Every value is required: a missing key means the file predates sim/dab_design.py
    (stale) and the run stops instead of substituting a built-in value (PCM-23)."""
    if not os.path.exists(SPEC):
        sys.exit(f'{SPEC} not found - run .venv/bin/python sim/dab_design.py first')
    sp = json.load(open(SPEC))

    def get(path):
        x = sp
        for k in path.split('/'):
            if not isinstance(x, dict) or k not in x or x[k] is None:
                sys.exit(f'{SPEC} has no {path} - it is stale; rerun .venv/bin/python sim/dab_design.py')
            x = x[k]
        return x
    ASSUME['R_loop'] = (get('control_plant/R_loop_primary_ohm'), get('control_plant/R_loop_basis'))
    ASSUME['P_branch_max'] = (get('derating_charge_W/V1=800/V2=800'), 'W per-branch power limit at 800/800 V, '
                                                                     'dab_spec.json derating_charge_W')
    get('power_stage/dead_time/study_W')
    return dict(n=get('transformer/n'), L=get('series_inductor/L_total_H'), Lm=get('transformer/L_m_H'),
                C1=get('capacitor_banks/port1/C_total_F'), C2=get('capacitor_banks/port2/C_total_F'),
                oc_trip=get('protection/I_xfmr_oc_trip_A'), i_port2_max=get('power_stage/port2/I_max_A'),
                dead_time=get('power_stage/dead_time'), st_inputs=get('st_crosscheck_inputs'),
                e_zcs_800=get('st_crosscheck_inputs/E_turn_on_zero_current_800V_J'),
                p_idle=(get('power_stage/light_load/P_off_W'), get('power_stage/light_load/P_on_W')),
                pre=dict(R1=get('precharge/port1/R_pre_ohm'), R2=get('precharge/port2/R_pre_ohm'),
                         dV=get('precharge/port2/close_main_when_dV_below_V')),
                study=get('assembly/study/id'), drawn=get('assembly/drawn_board/id'),
                v1_ov_hw=get('protection/V1_ov_hw_V'), ov_resp=get('protection/local_ov_board/port1/response_us') * 1e-6)


def phi_ff(v1, v2, n, L, p):
    """SPS feed-forward: inverse of P = n V1 V2 phi (pi - |phi|)/(2 pi^2 f L)."""
    x = np.clip(8 * F * L * abs(p) / max(v1 * n * v2, 1.0), 0, 1)
    return np.sign(p) * np.pi / 2 * (1 - np.sqrt(1 - x))


# ---------------------------------------------------------------------------------------------------------
# Plant: exact piecewise solution over one switching period for N parallel branches
# ---------------------------------------------------------------------------------------------------------
def simulate(dz, n_periods, ctrl, port2, branches, update='split', v1_src=800.0, v2_0=780.0, record=True,
             slew=True, port1=None):
    """dz: design dict; ctrl(k, meas) -> phase commands (rad) per branch for period k, NaN = gates off (idle);
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
    pcs = port1 is not None and port1.get('mode') in ('pcs', 'cpl')
    cpl = pcs and port1['mode'] == 'cpl'                  # inverter as a constant-power load, the DAB holds V1
    if pcs:
        c1tot = port1['C'] + dz['C1']
        kp1 = 2 * np.pi * port1.get('f_c', 0.0) * port1.get('kp_C', c1tot)     # gain on the PCS's own C if given
        ki1 = kp1 * 2 * np.pi * port1.get('f_c', 0.0) / port1.get('zero_ratio', 2.0)   # default zero f_c/2 (~63 deg)
        x1, i_ff = 0.0, 0.0
        i_pcs = 0.0
    phi_cmd_prev = np.zeros(nb)
    tr = {k: [] for k in ('t', 'v1', 'v2', 'i2', 'i2_br', 'il_max', 'il_min', 'il_dc', 'phi', 'p1', 'p2', 'i2_batt',
                          'e_r', 'i_pcs', 'alive')}
    meas = dict(v1=v1, v2=v2, i2=0.0, i2_br=np.zeros(nb), t=0.0, i_port2=0.0, alive=np.ones(nb, bool))
    e_r = 0.0
    for k in range(n_periods):
        t = k * T - T / 4
        raw = np.asarray(ctrl(k, meas), float)
        on = np.isfinite(raw)                         # idle branch: gates off, its current freewheels to zero
        alive = (k * T < trip) & on
        il, im = np.where(alive, il, 0.0), np.where(alive, im, 0.0)
        phi_cmd = np.clip(np.where(on, raw, 0.0), -np.radians(asm('phi_max_deg')), np.radians(asm('phi_max_deg')))
        if slew:
            sl = np.radians(asm('phi_slew_deg'))
            phi_cmd = phi_cmd_prev + np.clip(phi_cmd - phi_cmd_prev, -sl, sl)
        phi_cmd = np.where(on, phi_cmd, 0.0)          # a branch leaving idle starts from zero phase
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
                    if cpl:
                        i_pcs = -port1['p_load'](t) / max(v1, 1.0)
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
        if pcs and not cpl:                                                # PCS DC-link voltage loop, per period
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


def idle_gate(st, p_ref, dz):
    """FW-DAB-3: gates off while |P_ref| < P_off, on again at |P_ref| >= P_on (st['on'] holds the state)."""
    p_off, p_on = dz['p_idle']
    st['on'] = abs(p_ref) >= (p_off if st['on'] else p_on)
    if not st['on']:
        st['xi'][:] = 0.0                              # no integrator wind-up while the gates are off
        st['idle'] += 1
    return st['on']


def make_ctrl(dz, nb, iref, vref=None, per_branch=False, i_lim=None, idle=True, bus=None):
    """CC (and optional CV) controller.  iref(t) total current reference; vref(t) CV setpoint or None.
    per_branch: each branch has its own current PI on its own sensor (reference = total/nb); otherwise one PI on the
    summed current and a common phase.  idle: light-load policy (idle_gate) on the total reference.
    bus: dict(vref(t), C, f_cv, i0) = CV on PORT 1 (the DAB holds the PCS DC link, off-grid): PI on V1 without load
    feed-forward (the DAB does not measure the inverter's current) -> current into the bus -> port-2 reference by power
    balance; same gain rule as the port-2 CV loop; i0 = integrator start (the pre-load current)."""
    kphi = dz['n'] * 800.0 * (np.pi - 0.8) / (2 * np.pi ** 2 * F * dz['L'])      # dI2/dphi near 0.4 rad [A/rad]
    ki = 2 * np.pi * asm('f_ci') / kphi                                          # integral gain [rad/(A s)]
    kp_v = 2 * np.pi * asm('f_cv') * dz['C2']
    ki_v = kp_v * 2 * np.pi * asm('f_cv') / 5
    st = dict(xi=np.zeros(nb if per_branch else 1), xv=0.0, on=False, idle=0)
    i_lim = dz['i_port2_max'] if i_lim is None else i_lim
    if bus is not None:
        kp_b = 2 * np.pi * bus['f_cv'] * bus['C']
        ki_b = kp_b * 2 * np.pi * bus['f_cv'] / 5
        st['xv'] = bus.get('i0', 0.0)

    def ctrl(k, m):
        t = m['t']
        i_cc = iref(t)
        if bus is not None:
            ev = bus['vref'](t) - m['v1']
            i1 = kp_b * ev + st['xv']                                      # current into the bus [A]
            i_set = float(np.clip(-i1 * m['v1'] / max(m['v2'], 50.0), -i_lim, i_lim))
            if abs(i_set) < i_lim:                                         # anti-windup at the port limit
                st['xv'] += ki_b * ev * T
        elif vref is not None:
            ev = vref(t) - m['v2']
            iv = m['i_port2'] + kp_v * ev + st['xv']                 # load-current feed-forward + PI
            i_set = float(np.clip(min(iv, i_cc), -i_lim, i_lim))
            if not (iv > i_cc and ev > 0):                                         # anti-windup when CC limits
                st['xv'] = float(np.clip(st['xv'] + ki_v * ev * T, -i_lim, i_lim))
        else:
            i_set = float(np.clip(i_cc, -i_lim, i_lim))
        if idle and not idle_gate(st, i_set * m['v2'], dz):
            return np.full(nb, np.nan)
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
    ctrl.st = st
    return ctrl


def make_ctrl_system(dz, nb, i_total_req, clamp=True):
    """CAN-B system controller + per-branch current loops.  Every t_can the system controller reads the branch
    status and sends one reference per alive branch: I_total/N_alive, limited (clamp=True) to P_branch_max/V2; each
    branch also clamps its own reference to P_branch_max/V2 (clamp=True) or only to the port current limit."""
    kphi = dz['n'] * 800.0 * (np.pi - 0.8) / (2 * np.pi ** 2 * F * dz['L'])
    ki = 2 * np.pi * asm('f_ci') / kphi
    st = dict(xi=np.zeros(nb), ref=np.zeros(nb), ib=np.zeros(nb), next_can=0.0, n_alive_seen=[], on=np.zeros(nb, bool),
              idle=0)
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
        st['on'] = np.abs(ib) * m['v2'] >= np.where(st['on'], dz['p_idle'][0], dz['p_idle'][1])   # FW-DAB-3 per branch
        st['idle'] += int(np.sum(~st['on']))
        st['xi'] = np.where(st['on'], np.clip(st['xi'] + ki * (ib - m['i2_br']) * T, -0.3, 0.3), 0.0)
        ff = np.array([phi_ff(m['v1'], max(m['v2'], 50.0), dz['n'], dz['L'], x * m['v2']) for x in ib])
        return np.where(st['on'], ff + st['xi'], np.nan)
    ctrl.st = st
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
        out[upd] = simulate(dz, 2000, ctrl, dict(v_oc=780.0, r_int=asm('R_int_batt')), br, update=upd,
                            v2_0=780.0 - dz['pre']['dV'], slew=(upd == 'split'))
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


def scenario_mismatch_zero(dz, v1=950.0, v2=400.0, n_periods=300):
    """PCM-12: both bridges switching in SPS at zero phase with V1 = 950 V and a 400 V battery - what the idle rule
    (FW-DAB-3/4) prevents: the whole mismatch current circulates.  Open loop, idle off."""
    br = [dict(L=dz['L'], gain=1.0)]
    return simulate(dz, n_periods, lambda k, m: [0.0], dict(v_oc=v2, r_int=asm('R_int_batt')), br, v1_src=v1,
                    v2_0=v2, slew=False)


PCS_SPEC = 'sim/out/pcs_design/pcs_spec.json'
PCS_CTRL = 'sim/out/pcs_control/pcs_control_spec.json'


def load_pcs_link():
    """PCS-P125 DC link as the DAB sees it on port 1, read at run time (coordinator addendum; no built-in substitute,
    PCM-23): C_dc (pcs_spec dc_link.C_total_uF), the DC over-voltage trip (pcs_spec handover limits V_dc_trip_V), the
    UV stop = the lowest V_dc for rated current at the nominal 400 V grid and at +10 % (pcs_spec operating_map, PF rows),
    the PCS DC-link PI (pcs_control_spec dc_link f_c_Hz, PI zero ratio) and its measured-DC-current feed-forward lag
    (its current-loop crossover + sampling delay)."""
    d = {}
    for path, producer in ((PCS_SPEC, 'sim/pcs_design.py'), (PCS_CTRL, 'sim/pcs_control.py')):
        if not os.path.exists(path):
            sys.exit(f'{path} not found - run {producer} first (PCS-P125 link cases)')
        d[path] = json.load(open(path))

    def get(path, keys):
        x = d[path]
        for k in keys.split('/'):
            if not isinstance(x, dict) or k not in x:
                sys.exit(f'{path} has no {keys} - it is stale; rerun its script')
            x = x[k]
        return x
    vmin = get(PCS_SPEC, 'operating_map/vdc_min_at_rated_current_V')
    uv = {g: max(v for rk, row in vmin.items() if rk.startswith(g) for k, v in row.items() if k.startswith('PF'))
          for g in ('400 V', '440 V')}
    return dict(C=get(PCS_SPEC, 'dc_link/C_total_uF') * 1e-6,
                ov=get(PCS_SPEC, 'handover/control_engineer/limits/V_dc_trip_V'), uv_nom=uv['400 V'], uv_hi=uv['440 V'],
                f_c=get(PCS_CTRL, 'dc_link/f_c_Hz'), zero=get(PCS_CTRL, 'assumptions/dc_zero_ratio')[0],
                ff_tau=1.0 / (2 * np.pi * get(PCS_CTRL, 'current_loop/f_c_Hz')) + get(PCS_CTRL, 'inputs/delay_s'))


T_EV = 5e-3          # s: load / reference event of the PCS-P125 link cases
T_TRIP = 15e-3       # s: trip instant of the PCS-P125 link trip cases


def scenario_pcs_link(dz, pl, case, t_ramp=0.0, f_cv=None, n_periods=2500):
    """Coordinator addendum (PCS control study): port 1 = the PCS-P125 DC link at v_bus_pcs, its C_dc in parallel with the
    DAB's own bank; one DAB branch at P_branch,max from an 800 V battery.
    'gfl_step' / 'gfl_trip_out' / 'gfl_trip_in': the PCS holds V_dc (its PI + measured DC-current feed-forward, stiff
    grid, gain on its own C_dc as in the PCS study); the DAB is current-controlled: 0 -> P into the bus at T_EV (ramp
    t_ramp), or the DAB at P into (out) / out of (in) the bus, reached at the dpdt_pcs rule, trips at T_TRIP.
    'cv_step' / 'cv_dump': the DAB holds V_dc (CV on port 1, off-grid); the inverter is a constant-power load stepping
    p_pre_cv -> 1 (-> back) x P at T_EV (ramp t_ramp)."""
    v_bus, v_bat, p = asm('v_bus_pcs'), 800.0, asm('P_branch_max')
    br = [dict(L=dz['L'], gain=1.0)]
    batt = dict(v_oc=v_bat, r_int=asm('R_int_batt'))
    if case.startswith('gfl'):
        port1 = dict(mode='pcs', C=pl['C'], kp_C=pl['C'], f_c=pl['f_c'], zero_ratio=pl['zero'], v_ref=v_bus,
                     ff_tau=pl['ff_tau'])
        if case == 'gfl_step':
            iref = ramp(T_EV, T_EV + max(t_ramp, 1e-9), 0.0, -p / v_bat)
        else:
            sgn = -1.0 if case == 'gfl_trip_out' else 1.0
            iref = ramp(1e-3, 1e-3 + p / asm('dpdt_pcs'), 0.0, sgn * p / v_bat)
            br = [dict(L=dz['L'], gain=1.0, trip_t=T_TRIP)]
        return simulate(dz, n_periods, make_ctrl(dz, 1, iref), batt, br, v1_src=v_bus, v2_0=v_bat, port1=port1)
    lo, hi = asm('p_pre_cv') * p, p
    a_, b_ = (lo, hi) if case == 'cv_step' else (hi, lo)
    port1 = dict(mode='cpl', C=pl['C'], p_load=ramp(T_EV, T_EV + max(t_ramp, 1e-9), a_, b_))
    bus = dict(vref=lambda t: v_bus, C=pl['C'] + dz['C1'], f_cv=f_cv or asm('f_cv'), i0=a_ / v_bus)
    return simulate(dz, n_periods, make_ctrl(dz, 1, lambda t: dz['i_port2_max'], bus=bus), batt, br, v1_src=v_bus,
                    v2_0=v_bat, port1=port1)


def pcs_link_metrics(dz, pl, lk):
    """Bus excursions of the PCS-P125 link cases against the inverter's band; analytic trip timings."""
    v_bus, p = asm('v_bus_pcs'), asm('P_branch_max')
    out = {}
    for name, tr in lk.items():
        t_ev = T_TRIP if 'trip' in name else T_EV
        k = tr['t'] > t_ev - 2 * T
        out[name] = dict(v_min=float(np.min(tr['v1'][k])), v_max=float(np.max(tr['v1'][k])),
                         v_end=float(np.mean(tr['v1'][tr['t'] > tr['t'][-1] - 1e-3])))
        out[name]['inside_band'] = bool(pl['uv_nom'] <= out[name]['v_min'] and out[name]['v_max'] <= pl['ov'])
        out[name]['recovers'] = bool(abs(out[name]['v_end'] - v_bus) < 5.0)
    c_bus, i_full = pl['C'] + dz['C1'], p / v_bus
    dip = {f: v_bus - out[n]['v_min'] for f, n in ((asm('f_cv'), 'cv_step'), (asm('f_cv_max'), 'cv_step_fmax'))}
    m_hi = v_bus - pl['uv_hi']
    out['analytic'] = dict(
        C_bus_F=c_bus, I_full_A=i_full,
        t_to_uv_after_dab_trip_offgrid_ms=c_bus * (v_bus - pl['uv_nom']) / i_full * 1e3,
        t_to_ov_after_dab_trip_regen_ms=c_bus * (pl['ov'] - v_bus) / i_full * 1e3,
        f_cv_needed_for_uv_hi_Hz=asm('f_cv') * dip[asm('f_cv')] / m_hi if dip[asm('f_cv')] > m_hi else asm('f_cv'),
        v_set_min_for_step_at_present_f_cv_V=pl['uv_hi'] + dip[asm('f_cv')],
        dpdt_rule_W_per_s=asm('dpdt_pcs'), t_full_power_at_rule_ms=p / asm('dpdt_pcs') * 1e3)
    v1_hw, t_ov = dz['v1_ov_hw'], dz['ov_resp']
    out['analytic'].update(pcs_trip_dab_feeding_peak_V=v1_hw + i_full / c_bus * t_ov,
                           pcs_trip_dab_feeding_t_to_dab_ov_ms=c_bus * (v1_hw - v_bus) / i_full * 1e3)
    return out


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
    mz = scenario_mismatch_zero(dz)
    pl = load_pcs_link()
    lk = {'gfl_step': scenario_pcs_link(dz, pl, 'gfl_step'),
          'gfl_ramp': scenario_pcs_link(dz, pl, 'gfl_step', t_ramp=asm('P_branch_max') / asm('dpdt_pcs')),
          'gfl_trip_out': scenario_pcs_link(dz, pl, 'gfl_trip_out'),
          'gfl_trip_in': scenario_pcs_link(dz, pl, 'gfl_trip_in'),
          'cv_step': scenario_pcs_link(dz, pl, 'cv_step'),
          'cv_step_fmax': scenario_pcs_link(dz, pl, 'cv_step', f_cv=asm('f_cv_max')),
          'cv_dump': scenario_pcs_link(dz, pl, 'cv_dump')}
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
    # light-load policy (PCM-12): idle periods, restart offsets, and the zero-phase SPS case the policy prevents
    k_rv = (rv['t'] > 5e-3) & (rv['t'] < 7e-3)
    last = mz['t'] > mz['t'][-1] - 1e-3
    m.update(softstart_idle_periods=int(np.sum(~sp['alive'][:, 0])),
             rev_idle_periods=int(np.sum(~rv['alive'][k_rv, 0])),
             rev_dc_offset=float(np.max(np.abs(rv['il_dc'][k_rv, 0]))),
             mz_V1=950.0, mz_V2=400.0,
             mz_il_pk=float(np.max(np.abs(np.r_[mz['il_max'][last, 0], mz['il_min'][last, 0]]))),
             mz_il_half_pp=float(np.max(mz['il_max'][last, 0]) - np.min(mz['il_min'][last, 0])) / 2,
             mz_il_dc=float(np.mean(mz['il_dc'][last, 0])),
             mz_il_pk_closed_form=abs(950.0 - dz['n'] * 400.0) / (4 * F * dz['L']),
             mz_p2_kW=float(np.mean(mz['p2'][last]) / 1e3))
    m['pcs_link'] = pcs_link_metrics(dz, pl, lk)
    m['pcs_link']['inputs'] = dict(pl, v_bus=asm('v_bus_pcs'), C_dab_F=dz['C1'], f_cv=asm('f_cv'), f_cv_max=asm('f_cv_max'))
    fig, axs = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for name, tr in lk.items():
        axs[0 if name.startswith('gfl') else 1].plot(tr['t'] * 1e3, tr['v1'], lw=0.9, label=name)
    for ax, ttl in zip(axs, ('PCS holds V_dc (stiff grid), DAB current-controlled', 'DAB holds V_dc (CV, off-grid), '
                                                                                    'inverter = constant-power load')):
        for v_, ls in ((pl['ov'], '--'), (pl['uv_nom'], ':'), (pl['uv_hi'], '-.')):
            ax.axhline(v_, color='k', ls=ls, lw=0.7)
        ax.set(ylabel='V_dc [V]', title=ttl)
        ax.legend(fontsize=7, ncol=4)
        ax.grid(alpha=0.3)
    axs[1].set_xlabel('t [ms]')
    fig.suptitle('PCS-P125 DC link %.0f uF + DAB %.0f uF, one branch at %.0f kW (calculated); OV trip --, UV stop 400 V '
                 'grid :, +10 %% -.' % (pl['C'] * 1e6, dz['C1'] * 1e6, asm('P_branch_max') / 1e3), fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'pcs_link.png'), dpi=120)
    plt.close(fig)
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
    if not os.path.exists(ST_CSV):
        sys.exit(f'{ST_CSV} not found - it is the decoded ST firmware table of the DAB-08 cross-check (kept in git)')
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
    e_zcs = dz['e_zcs_800']
    rows = [
        ('modulation', 'SPS or closed-form trapezoidal (two inner shifts from V_HV, V_LV, n and phi each 100 us); '
                       'selected by writing a RAM variable', 'TPS look-up table (a1, a2, phi vs V1, V2, P) chosen offline '
                       'for minimum modelled loss with the real C_oss / ZVS completion and the turn-off SOA; SPS near '
                       'V1 = n V2 and at heavy load', 'DIFFERENT',
         'The trapezoidal law targets zero current at the edges (ZCS, minimum RMS) and ignores C_oss: with SiC a '
         'zero-current edge cannot swing the node, so the incoming switch turns on at full voltage and dumps '
         '%.0f uJ per position at 800 V (our model, study switches) - %.0f W if two edges per period switch that way. '
         'Our LUT keeps enough current at every edge for ZVS where it can. Keep the LUT; adopt ST\'s closed form only '
         'as the plausibility reference, never as a modulation outside the table (outside it the gates stay off, '
         'FW-DAB-1)' % (e_zcs * 1e6, 2 * F * e_zcs)),
        ('dead time', '%.0f ns fixed for both bridges (HRTIM insertion; %.0f ns in the .sim build) = %.1f deg'
         % (td_st * 1e9, num('DeadTime.request (.sim build)') * 1e9, 360 * f_st * td_st),
         'hardware shoot-through guard %.0f-%.0f ns at the gates + firmware LUT (ZVS transition + 20 ns, %.0f-%.0f ns)'
         % (dt['hw_guard_ns'][0], dt['hw_guard_ns'][1], dt['min_ns'], dt['max_ns']), 'DIFFERENT',
         '400 ns fixed costs %.0f W more than our scheme at 800/800 V 60 kW (%.0f W body-diode conduction, design '
         'model) - not adopted; a long fixed dead time is the simple choice for a 25 kW board without a current loop'
         % (dt['study_W']['st_400ns_minus_ours_800_60k'], dt['study_W']['st_400ns_diode_800_60k'])),
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


def pcs_link_report(dz, q):
    """Report section of the PCS-P125 link cases (coordinator addendum) with the DAB-side firmware requirements."""
    pl, an = q['inputs'], q['analytic']
    rows = (('gfl_step', 'PCS (stiff grid)', 'DAB 0 -> %.0f kW into the bus, step' % (asm('P_branch_max') / 1e3)),
            ('gfl_ramp', 'PCS (stiff grid)', 'the same at the ramp rule (%.1f ms)' % an['t_full_power_at_rule_ms']),
            ('gfl_trip_out', 'PCS (stiff grid)', 'DAB feeding %.0f kW into the bus trips' % (asm('P_branch_max') / 1e3)),
            ('gfl_trip_in', 'PCS (stiff grid)', 'DAB taking %.0f kW out of the bus trips' % (asm('P_branch_max') / 1e3)),
            ('cv_step', 'DAB, CV %.0f Hz' % asm('f_cv'), 'inverter load %.0f -> %.0f kW, step'
             % (asm('p_pre_cv') * asm('P_branch_max') / 1e3, asm('P_branch_max') / 1e3)),
            ('cv_step_fmax', 'DAB, CV %.0f Hz' % asm('f_cv_max'), 'the same'),
            ('cv_dump', 'DAB, CV %.0f Hz' % asm('f_cv'), 'inverter load %.0f -> %.0f kW, step (load dump)'
             % (asm('P_branch_max') / 1e3, asm('p_pre_cv') * asm('P_branch_max') / 1e3)))
    L = ['## PCS-P125 DC link (coordinator addendum, PCS control study)', '',
         f'Read at run time: C_dc {pl["C"]*1e6:.1f} uF (`{PCS_SPEC}` dc_link.C_total_uF) in parallel with the DAB\'s own '
         f'{pl["C_dab_F"]*1e6:.0f} uF; PCS DC over-voltage trip {pl["ov"]:.0f} V (handover limits); UV stop = the lowest '
         f'V_dc for rated current {pl["uv_nom"]:.0f} V at the 400 V grid, {pl["uv_hi"]:.0f} V at +10 % (operating_map); '
         f'PCS DC-link PI {pl["f_c"]:.0f} Hz with its zero at f_c / {pl["zero"]:g} and the measured DC-current '
         f'feed-forward, lag {pl["ff_tau"]*1e6:.0f} us (its current loop + sampling delay, `{PCS_CTRL}`). Bus at '
         f'{pl["v_bus"]:.0f} V, one branch from an 800 V battery. The PCS is an ideal current source behind its loop '
         '(stiff grid): the weak-grid case (SCR 5) is the PCS study\'s (a 125 kW DC/DC trip reaches 1098 V there) and is '
         'not reproduced here. Calculated, not bench-validated.', '',
         '| case | V_dc held by | event | V min | V max | inside UV stop (400 V grid) .. OV trip | back to the setpoint |',
         '|---|---|---|---|---|---|---|']
    for k, who, ev in rows:
        r = q[k]
        L.append(f'| {k} | {who} | {ev} | {r["v_min"]:.0f} | {r["v_max"]:.0f} | {"yes" if r["inside_band"] else "NO"} | '
                 f'{"yes" if r["recovers"] else "NO"} |')
    st, sf = q['cv_step'], q['cv_step_fmax']
    L += ['', f'Off-grid (the DAB forms the bus): a full-power step dips the bus by {pl["v_bus"] - st["v_min"]:.0f} V at '
          f'the present {asm("f_cv"):.0f} Hz CV crossover ({pl["v_bus"] - sf["v_min"]:.0f} V at {asm("f_cv_max"):.0f} Hz, '
          f'the cascade\'s ceiling) - the {pl["C"]*1e3:.2f} mF link is not too small for the present loop at the 400 V '
          f'grid ({st["v_min"]:.0f} V against {pl["uv_nom"]:.0f} V). At a +10 % grid the stop is {pl["uv_hi"]:.0f} V: it '
          + (f'needs about {an["f_cv_needed_for_uv_hi_Hz"]:.0f} Hz (dip ~ 1/f_cv, calculated; '
             + ('within' if an['f_cv_needed_for_uv_hi_Hz'] <= asm('f_cv_max') else 'above')
             + f' the {asm("f_cv_max"):.0f} Hz ceiling) or - the cheap rule - a setpoint >= '
             f'{an["v_set_min_for_step_at_present_f_cv_V"]:.0f} V in off-grid operation.'
             if an['f_cv_needed_for_uv_hi_Hz'] > asm('f_cv') else 'is met as well.'),
          '', f'A DAB trip while it forms the bus at full load: the bus reaches the UV stop after '
          f'{an["t_to_uv_after_dab_trip_offgrid_ms"]:.2f} ms ({an["C_bus_F"]*1e3:.2f} mF, {an["I_full_A"]:.0f} A, '
          f'calculated); with the inverter regenerating at full power it reaches the OV trip after '
          f'{an["t_to_ov_after_dab_trip_regen_ms"]:.2f} ms. A PCS trip while the DAB feeds the bus: the DAB\'s own port-1 '
          f'OV trip ({dz["v1_ov_hw"]:.0f} V, {dz["ov_resp"]*1e6:.0f} us path, dab_spec) is reached after '
          f'{an["pcs_trip_dab_feeding_t_to_dab_ov_ms"]:.2f} ms and the bus peaks at '
          f'{an["pcs_trip_dab_feeding_peak_V"]:.0f} V, below the PCS\'s {pl["ov"]:.0f} V - the DAB stops first.', '',
          '**DAB-side firmware requirements for the PCS-P125 link** (dab_spec firmware_requirements FW-DAB-8..10):', '',
          f'* FW-DAB-8 power ramp: every change of the DAB power reference into a link the PCS holds (start, stop, step, '
          f'reversal) at <= {an["dpdt_rule_W_per_s"]/1e6:.2f} kW/ms in total - the PCS study\'s rule, '
          f'{an["t_full_power_at_rule_ms"]:.1f} ms for {asm("P_branch_max")/1e3:.0f} kW - shared as /N_alive between '
          f'branches (system controller); the branch firmware\'s own {asm("i_ramp")/1e3:.0f} A/ms ramp '
          f'({asm("i_ramp")*800/1e6:.0f} kW/ms at 800 V) is only the local limit. On a stiff grid even a step stays inside '
          f'the band ({q["gfl_step"]["v_min"]:.0f}-{q["gfl_step"]["v_max"]:.0f} V); the rule is for the weak grid (PCS '
          f'study, SCR 5: collapse without it).',
          '* FW-DAB-9 stops and trips: a non-hazard stop ramps the power to zero at the FW-DAB-8 rate before the gates go off; '
          'a hazard trip gates off at once and reports on the module bus, and the PCS takes the step out of its reference '
          f'through its measured DC current (feed-forward lag {pl["ff_tau"]*1e6:.0f} us: '
          f'{q["gfl_trip_out"]["v_min"]:.0f} / {q["gfl_trip_in"]["v_max"]:.0f} V here on a stiff grid). Who opens what: the '
          'tripped DAB gates off first; its port contactors open only after its current is zero; the PCS keeps its DC '
          'contactor closed and holds the link; on a weak grid the PCS study\'s coordinated trip (PCS current cut in the '
          'same control frame) is the PCS-side requirement, not the DAB\'s.',
          '* FW-DAB-10 off-grid bus forming (CV on port 1, the inverter grid-forming): CV crossover '
          f'{asm("f_cv"):.0f} Hz, no load feed-forward (the DAB does not measure the inverter current); when the grid is '
          f'above nominal either a setpoint >= {an["v_set_min_for_step_at_present_f_cv_V"]:.0f} V or a crossover >= '
          f'{an["f_cv_needed_for_uv_hi_Hz"]:.0f} Hz; the DAB\'s port-1 OV trip '
          f'({dz["v1_ov_hw"]:.0f} V) stays below the PCS\'s {pl["ov"]:.0f} V so that on a PCS trip the DAB stops first '
          '(above); the PCS stops on its own UV detection when the DAB trips.', '']
    return L


def report(dz, m):
    L = ['# DAB-D60 control simulation report', '',
         'Generated by `sim/dab_control.py` (do not edit). Calculated, not bench-validated. Design values read from '
         '`sim/out/dab_design/dab_spec.json`.', '',
         f'Plant: n = {dz["n"]:.4f}, L = {dz["L"]*1e6:.2f} uH, L_m = {dz["Lm"]*1e6:.0f} uH, C1 = {dz["C1"]*1e6:.0f} uF, '
         f'C2 = {dz["C2"]*1e6:.0f} uF, f_sw = 100 kHz, control update every period with one-period delay.', '',
         f'**Plant identity (review PCM-09).** n, L, L_m and the banks are the D-014 values, common to the drawn board '
         f'{dz["drawn"]} and the study; R_loop {asm("R_loop")*1e3:.1f} mOhm, the OC trip {dz["oc_trip"]:.0f} A and the '
         f'dead-time guard are those of the study devices ({dz["study"]}). The plant is SPS with ideal edges and no '
         'device model: the results are valid near V1 = n V2 (|V1 - n V2| <= ~130 V, zero-phase circulating peak '
         '<= 50 A) for either device set, and they are not acceptance evidence for the drawn board.', '',
         f'Start-up assumption from the port block (dab_spec.json precharge, copied there from '
         f'`sim/out/port_design/port_spec.json`): precharge through {dz["pre"]["R1"]:.0f} / {dz["pre"]["R2"]:.0f} ohm (tau '
         f'{dz["pre"]["R1"]*dz["C1"]*1e3:.1f} / {dz["pre"]["R2"]*dz["C2"]*1e3:.1f} ms), main contactor closes at dV <= '
         f'{dz["pre"]["dV"]:.0f} V; the DAB starts after closure with the bank {dz["pre"]["dV"]:.0f} V below the battery '
         'OCV.', '',
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
         '## Light load and voltage mismatch (review PCM-12)', '',
         f'Policy (dab_spec firmware_requirements FW-DAB-3..5): gates off while |P_ref| < {dz["p_idle"][0]/1e3:.1f} kW, '
         f'switching again at |P_ref| >= {dz["p_idle"][1]/1e3:.1f} kW; a branch leaving idle restarts from zero phase with '
         'the split update and the 3 deg per period slew. Applied in every scenario above.', '',
         '| test | result |', '|---|---|',
         f'| soft start from idle with both ports live (800 V bus, {780 - dz["pre"]["dV"]:.0f} V bank, 780 V battery) | '
         f'gates off for the first '
         f'{m["softstart_idle_periods"]} periods of the ramp, then peak i_L {m["softstart_il_peak"]:.0f} A, DC offset '
         f'{m["dc_offset_split"]:.2f} A (split update) |',
         f'| power reversal +70 -> -70 A through zero (800 / 800 V) | gates off for {m["rev_idle_periods"]} periods inside '
         f'the idle band, restart DC offset {m["rev_dc_offset"]:.2f} A, peak i_L {m["rev_il_peak"]:.0f} A, final '
         f'{m["rev_i2_end"]:+.1f} A |',
         f'| what the idle rule prevents: SPS at zero phase, both bridges switching, {m["mz_V1"]:.0f} V bus / '
         f'{m["mz_V2"]:.0f} V battery | i_L peak {m["mz_il_pk"]:.0f} A against the {dz["oc_trip"]:.0f} A OC trip, at '
         f'{m["mz_p2_kW"]:+.2f} kW transferred: half peak-to-peak {m["mz_il_half_pp"]:.0f} A = the closed form '
         f'{m["mz_il_pk_closed_form"]:.1f} A (stiff ports, lossless), plus a {m["mz_il_dc"]:+.0f} A DC offset that the bank '
         'ripple drives through R_loop |', '',
         'Not simulated here (SPS plant): the start from idle on a TPS entry at a large V1 / (n V2); the TPS LUT\'s '
         'circulating current at the corners is in the design report, section 0.5.', '',
         ] + pcs_link_report(dz, m['pcs_link']) + [
         '## Parallel branches (DAB-09) - decision and fault behaviour', '',
         '* No hardware carrier-sync line between branches (decision): carriers are free-running; the per-branch current '
         'loop makes sharing independent of carrier phase (same result with branch 2 offset by 30 deg). Ripple at the '
         'shared buses adds without interleaving and beats slowly; each branch keeps its own capacitor banks.',
         '* Sharing = common reference over CAN-B + per-branch inner loop on the branch\'s own I2 sensor.',
         f'* One-branch fault: the tripped branch\'s current is gone within one period; the survivor holds its own '
         f'reference because its loop is local. The bus transient is a GENERIC PCS (not PCS-P125: its 0.41 mF link is the '
         f'section above) seeing a 60 kW load step on a {asm("C_pcs")*1e3:.0f} mF link with a {asm("f_pcs"):.0f} Hz voltage '
         f'loop: '
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
          'replacements: the on-line SPS P_max plausibility check, and the closed-form trapezoidal law as a plausibility '
          'reference - not as a fallback modulation: its zero-current edges are hard turn-ons with SiC, which the turn-on '
          'rule forbids above 600 V, so outside the LUT\'s feasible set the gates stay off (FW-DAB-1). ST\'s 400 ns fixed '
          'dead time and its voltage-only loop are not adopted (reasons above).',
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
    # light-load policy (PCM-12): the reversal passes through the idle band and restarts inside the trip window; the
    # zero-phase SPS case matches the closed form |V1 - n V2| T / (4 L) and transfers no power
    assert m['rev_idle_periods'] > 0 and m['softstart_idle_periods'] > 0
    assert m['rev_il_peak'] < dz['oc_trip'] and m['rev_dc_offset'] < 0.1 * dz['oc_trip']
    # (the half peak-to-peak is the closed form; the peak adds the DC offset the bank ripple drives through R_loop)
    assert abs(m['mz_il_half_pp'] / m['mz_il_pk_closed_form'] - 1) < 0.05 and abs(m['mz_p2_kW']) < 1.0
    # PCS-P125 link (coordinator addendum): the ramp rule keeps the bus inside the band and below the step's excursion,
    # the faster CV loop dips less, every case returns to the setpoint, and on a PCS trip the DAB's OV trip acts first
    q = m['pcs_link']
    assert q['gfl_ramp']['inside_band'] and all(q[k]['recovers'] for k in q if k not in ('analytic', 'inputs'))
    assert q['gfl_ramp']['v_max'] - q['gfl_ramp']['v_min'] <= q['gfl_step']['v_max'] - q['gfl_step']['v_min'] + 1e-6
    assert q['cv_step_fmax']['v_min'] >= q['cv_step']['v_min'] - 1e-6
    assert q['analytic']['pcs_trip_dab_feeding_peak_V'] < q['inputs']['ov'], 'DAB OV trip does not act before the PCS'
    print('dab_control self-check passed')


if __name__ == '__main__':
    mm = main()
    print({k: round(v, 3) for k, v in mm.items() if isinstance(v, (int, float))})
