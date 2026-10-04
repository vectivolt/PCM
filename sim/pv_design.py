"""PVCELL-25/27.5 detailed power-stage design of the topology recommended by sim/pv_tradeoff.py (Gate-0, PV-18).

Run:  .venv/bin/python sim/pv_design.py   (runs pv_tradeoff.run() first if sim/out/pv_tradeoff/selection.json is missing)
Out:  sim/out/pv_design/{report.md, cell_spec.json, *.csv, *.png}, ngspice decks sim/spice/pv_fsbb_*.cir, pv_dpt_*.cir
Every value is calculated (datasheet-based analytic model + ngspice VDMOS transient); nothing is measured.
"""
import csv
import json
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pv_devices as dv  # noqa: E402
import pv_tradeoff as tr  # noqa: E402

OUT = os.path.join(HERE, "out", "pv_design")
PORT_CAPS_MIN = 2      # hand-off margin: at least two C4AQ parts per port (ripple <= 50 % of the summed rating)
ETA_DESIGN = 0.992     # corner efficiency target for the hand-off design: 0.2 %-point model margin over the 99.0 % trade floor
TJ_DESIGN = 125.0      # C at 45 C inlet, full power (25 K below the 150 C trade limit)
SPICE_POINTS = [("buck", 900.0, 600.0), ("boost", 600.0, 900.0), ("band", 800.0, 810.0)]
EVT_DERATE = {45.0: 1.0, 50.0: 0.8, 55.0: 0.6, 60.0: 0.4}     # roadmap 'Provisional air-product derating programme'


GDRV_CHECK = os.path.join(os.path.dirname(HERE), "hardware", "GDRV-HB", "outputs", "GDRV-HB_design_check.txt")


GEN = os.path.join(os.path.dirname(HERE), "gen")
GDRV_PRESET = "2 x SG2M040170HJ"          # gen/gdrv.py device preset of the PV primary (D-037)
TURN_ON_DELAY = 65e-9    # s: rev-5 turn-on delay (clamp release) stated by the gate-driver engineer; gdrv's release formula gives
                         # 50 ns - the larger is used for the dead time and the minimum pulse
_GD = None


def gdrv_check():
    """the gate-driver design's numbers for the PV primary, read from gen/gdrv.py's Python objects (rev 5+): the per-device
    record design_check() builds (captured from its frame at return - no printed text is parsed) and its constants"""
    global _GD
    if _GD is not None:
        return _GD
    import contextlib
    import io
    sys.path.insert(0, GEN)
    import gdrv
    cap = {}

    def prof(frame, event, arg):
        if event == "return" and frame.f_code is gdrv.design_check.__code__:
            cap.update(frame.f_locals.get("num", {}))
    sys.setprofile(prof)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            gdrv.design_check()
    finally:
        sys.setprofile(None)
    rec = cap[GDRV_PRESET]
    dev = next(x for x in gdrv.DEVICES if x["name"] == GDRV_PRESET)
    vt, v3, v2 = gdrv.rails(dev["gate_v"])
    dtc = dev.get("deadtime", gdrv.DEADTIME_CLASS[dev["vclass"]])
    ms = gdrv.MILLER_SIM[GDRV_PRESET]
    fw_dt = dev.get("fw_dt", 200e-9)
    _GD = {"von": (round(v2[0], 2), round(v2[1], 2)), "voff": (-round(v3[1], 2), -round(v3[0], 2)),
           "i_src": round(rec["ipk"][0], 2), "i_snk": round(rec["ipk"][1], 2), "r_ks": gdrv.R_KS,
           "trip": (round(rec["trip"][0], 2), round(rec["trip"][1], 2)), "trip_nom": round(0.5 * sum(rec["trip"]), 2),
           "blank": (rec["blank"][0] * 1e9, rec["blank"][1] * 1e9), "t_sc": round(rec["t_sc"] * 1e6, 2), "scwt": None,
           "dt_stretch": f"{gdrv.ohm(dtc[0])}/{gdrv.farad(dtc[1])}", "dt_gates": (rec["dt"][0], rec["dt"][1]), "dt_fw": fw_dt,
           "dt_ext": max(0.0, rec["dt"][2] - fw_dt), "clamp_on": None, "dt_first_on": rec["dt"][0],
           "gd_im": ms["i_m"], "gd_pin": ms["pin_l1"], "gd_die": ms["die_l1"], "die_l0": ms["die_l0"], "miller_dvdt": ms["dvdt"],
           "r_pu": "rev 5: AO3400A clamp MOSFET per gate at the gate-Kelvin pins", "r_sb": gdrv.R_SB_CLASS[dev["vclass"]],
           "r_gate": dev["r_gate"], "gate_v": dev["gate_v"],
           "src": "gen/gdrv.py rev 5 (design_check record + constants via Python), sim/gdrv_miller.py"}
    return _GD


# ------------------------------------------------------------------------------------------------ design choice
FROZEN = {"cand": "A17", "fsw": 32e3, "ripple": 0.80}   # D-007 point (2-level, 32 kHz, ripple 0.80, today's inductor), kept by
# Gate-0b (D-031, sim/out/pv_tradeoff/gate0b.md); the devices are now Asian (SRC-1) and designed for a primary and an alternate
# maker: every check takes the worse of the two.  Reference of the previous rounds: 2 x Microchip MSC035SMA170B4.
DEVICES = {"primary": ("SG2M040170HJ", 2), "alternate": ("MSC035SMA170B4", 2)}   # D-041: Sichain cost device; qualified fallback = the original Microchip part (assembly variant). InventChip fails the false-turn-on check (rev-5 gdrv_miller: IV2Q17020T4Z +4.0 V, IV2Q17040T4Z +4.41 V at the die) - kept in Gate-0b only


RAILS = {"SG2M040170HJ": (18.0, -3.5),      # V on / off per variant: Sichain -4 V is its most negative V_GS with the body diode
         "MSC035SMA170B4": (20.0, -4.0)}    # conducting (Note 1/2); Microchip +20/-4 V as designed before (gen/gdrv.py rails)


def set_dead_time():
    """hardware dead time at the gates from the gate-driver design (same stretch class for both variants) plus the rev-5
    turn-on delay; the minimum pulse keeps 600 ns of complementary on-time (pv_tradeoff)"""
    gd = gdrv_check()
    tr.T_DEAD_MIN, tr.T_DEAD = gd["dt_gates"][0], gd["dt_gates"][1] + TURN_ON_DELAY
    tr.T_MIN_PULSE = tr.T_ON_MIN + 2 * tr.T_DEAD
    return tr.T_DEAD


def choose(role="primary"):
    set_dead_time()
    g = json.load(open(os.path.join(tr.OUT, "gate0b.json")))
    assert g["stay_2L"], "Gate-0b: the best 3-level candidate is > 15 % cheaper - the 2-level cell does not stand (coordinator)"
    dev, npar = DEVICES[role]
    des = tr.build_design(FROZEN["cand"], dev, npar, FROZEN["fsw"], FROZEN["ripple"])
    if tr.IND_DESIGN:            # the magnetics engineer's construction replaces the catalogue-core inductor (rev M1+)
        des["ind"] = tr.ind_ocp_fields(tr.inductor_from_design(), des["i_ocp"])
        des["caps"] = tr.size_caps(des)
    m_on, r = tr.transient_check(des)
    assert m_on is not None, f"{dev}: fails the device peak-voltage rule at every R_G,on step"
    des["rg_on_mult"] = m_on
    des["role"] = role
    # hand-off margin on the port banks: >= PORT_CAPS_MIN parts per port and ripple <= 50 % of the summed rating
    for port in "AB":
        c = des["caps"][port]
        part = dv.C4AQ[c["part"]]
        n = max(PORT_CAPS_MIN, math.ceil(c["irms_worst"] / (0.5 * part["irms"])), c["n"])
        c.update({"n": n, "C": n * part["C"], "irms_rating": n * part["irms"]})
    des["rg_ks"] = gdrv_check()["r_ks"]          # Kelvin-source resistor per device (gate-driver design): in E_on/E_off via R_G
    des["dec"] = DEC_CHOICE
    des["damp"] = damper_table(des)               # RC-damper energy per edge -> loss 'damp' in tr.cell_losses
    res = tr.evaluate(des)
    pick = tr.summarise(des, res)
    pick.update({"des": des, "transient": r, "cost_usd": tr.cost_usd(des)})
    target = ETA_DESIGN if pick["eta_corner_min"] >= ETA_DESIGN else tr.ETA_CORNER_REQ
    return des, FROZEN["cand"], g, pick, target


def device_block(role, des, spec, corners, peak, worst, dpt_w, mil, brc):
    """the key numbers of one device of the pair (cell_spec device_primary / device_alternate)"""
    d = des["d"]
    mc = spec["gate_drive"]["miller_check"]
    return {"role": role, "mpn": des["dev"], "manufacturer": d["mfr"], "parallel": des["npar"], "datasheet": d["src"],
            "revision": d.get("rev"), "qualification": d.get("qualification"), "price_usd": round(tr.price(des["dev"])[0], 2),
            "price_source": tr.price(des["dev"])[1], "rg_on_ohm": round(d["rg_ext"] * des["rg_on_mult"], 2), "rg_off_ohm": d["rg_ext"],
            "eta_corner_min": min(corners[k]["eff"] for k in ("550->950", "950->550", "550->550", "950->950")),
            "eta_corners": {k: round(v["eff"], 5) for k, v in corners.items()}, "eta_peak": round(peak["eff"], 5),
            "Tj_max_45C_C": round(worst["tj"], 1), "VDS_peak_V": round(tr.V_OVP + dpt_w["dv_os"], 0),
            "turn_on_dvdt_V_per_ns": round(dpt_w["dvdt_on"] / 1e9, 0), "miller_die_peak_V": mc["V_GS_die_peak_V"],
            "miller_limit_V": mc["limits_V"]["V_th_min_175C"], "rails_V": list(RAILS[des["dev"]]),
            "dead_time_at_gates_ns": [round(tr.T_DEAD_MIN * 1e9), round(tr.T_DEAD * 1e9)],
            "body_diode_peak_A_in_port_short": brc and
            max(r["body_pk"] for (nc, p, v, k), r in brc["cases"].items() if k), "t_sc_us": d.get("t_sc")}


def gate_spec(spec, drq, gd):
    """gate-drive block for the new devices: what the driver must deliver (NSI6651, D-035); keys kept, values replaced"""
    g = spec["gate_drive"]
    g.update({"driver": "NOVOSENSE NSI6651ASC-Q1 (D-035), one isolated channel per switch position (4 per cell); bias supply open "
                        "(D-035: UCC14241-Q1 documented option)",
              "V_GS_on_V": RAILS[DEVICES["primary"][0]][0], "V_GS_off_V": RAILS[DEVICES["primary"][0]][1],
              "V_GS_on_range_V": list(gd["von"]), "V_GS_off_range_V": list(gd["voff"]),
              "rails_by_variant_V": {m: list(RAILS[m]) for m, _ in DEVICES.values()},
              "sc_booster": {"r_sb_ohm_per_device": gd["r_sb"], "detection_to_off_us": gd["t_sc"],
                             "basis": "gen/gdrv.py rev 5 (primary preset; the fallback needs its own booster sizing)"},
              "rail_range_basis": "REQUIREMENT on the bias design (VDD-VEE 22 V): +-0.5 V on, +-0.3 V off keeps both makers inside their "
                                  "recommended drive and well inside the absolute limits",
              "peak_gate_current_source_A": round(drq["peak_source_A_max"], 2), "peak_gate_current_sink_A": round(drq["peak_sink_A_max"], 2),
              "peak_gate_current_A": round(max(drq["peak_source_A_max"], drq["peak_sink_A_max"]), 2), "driver_rating_A": NSI["i_pk"],
              "gate_power_per_channel_W": round(drq["gate_power_W_per_channel_max"], 3),
              "gate_drive_basis": "sim/pv_design.py drive_requirements(): NSI6651 ROH 2.2 / ROL 0.3 ohm (sim/data/asia_drivers.md), "
                                  "device R_G,int and Q_G from the datasheets, R_KS 0.5 ohm",
              "requirements": drq,
              "hardware_dead_time_required_ns": [round(tr.T_DEAD_MIN * 1e9), round(tr.T_DEAD * 1e9)],
              "rev4_hardware_dead_time_ns": [round(gd["dt_gates"][0] * 1e9), round(gd["dt_gates"][1] * 1e9)],
              "short_circuit": {"withstand_assumed_us": T_SC_ASSUMED * 1e6,
                                "basis": "no Chinese datasheet states one; MSC035SMA170B4 3.1 us typ (1200 V, 20 V), ROHM SCT4036KRHR 4 us - "
                                         "ASSUMPTION to be confirmed by the makers or a SC test",
                                "required_detect_to_off_us_max": T_SC_ASSUMED * 1e6 / 2,
                                "response_us": {k: round(v, 2) for k, v in drq["sc_response_us_worst"].items()},
                                "booster_detection_to_off_us_primary": gd["t_sc"],
                                "verdict": f"primary: rev-5 booster ({gd['r_sb']:.0f} ohm per device) turns off {gd['t_sc']} us after DESAT "
                                           f"detection (<= {T_SC_ASSUMED*1e6/2:.1f} us required); the internal soft turn-off alone would take "
                                           f"{max(drq['sc_response_us_worst'].values()):.2f} us. Fallback (MSC035, +20/-4 V): the booster "
                                           "is not sized for it in gen/gdrv.py rev 5 - open"}})


def protection_update(spec, drq, gd=None):
    """DESAT / short-circuit entries for the NSI6651 (keys kept)"""
    pr = spec["protection"]
    pr.update({"desat_threshold_range_V": list(NSI["desat_th"]), "desat_VDS_threshold_V": 9.26,
               "desat_blanking_ns": round((NSI["leb"] + NSI["filt"]) * 1e9), "desat_blanking_range_ns": [150 + 200, 265 + 200],
               "short_circuit_detect_to_off_us": gd["t_sc"] if gd else round(drq["sc_response_us_worst"]["industrial"], 2),
               "short_circuit_withstand_us_typ": T_SC_ASSUMED * 1e6,
               "desat_basis": "NSI6651 DESAT pin threshold 8.5/9.26/9.8 V, LEB 200 ns + filter 150-265 ns, DESAT to OUT <= 300 ns "
                              "(sim/data/asia_drivers.md); V_DS(on) at the trip current, 175 C: " +
                              ", ".join(f"{v['mpn']} {v['vds_on_at_trip_175C_V']:.1f} V (margin {v['desat_margin']:.1f}x)"
                                        for v in drq["per_device"].values()) +
                              f"; short-circuit withstand ASSUMED {T_SC_ASSUMED*1e6:.1f} us (no Chinese datasheet gives one)"})


def combine_drive(a, b):
    """worst of two drive_requirements results"""
    out = dict(a)
    out["per_device"] = dict(a["per_device"], **b["per_device"])
    for k in ("peak_source_A_max", "peak_sink_A_max", "gate_power_W_per_channel_max"):
        out[k] = max(a[k], b[k])
    out["sc_response_us_worst"] = {g: max(a["sc_response_us_worst"][g], b["sc_response_us_worst"][g]) for g in a["sc_response_us_worst"]}
    out["desat_margin_min"] = min(a["desat_margin_min"], b["desat_margin_min"])
    return out


COST_EST = os.path.join(os.path.dirname(HERE), "gen", "data", "cost_estimates.csv")


def update_inductor_cost_row(ind):
    """rewrite ONLY this design's inductor rows of gen/data/cost_estimates.csv (coordinator: correct only your own rows); with
    the magnetics design file, its cost.cost_estimates_row is written as given"""
    rows = list(csv.reader(open(COST_EST)))
    if ind.get("design"):
        new = next(csv.reader([ind["design"]["cost"]["cost_estimates_row"]]))
        for r in rows:
            if r and r[0] == new[0]:
                r[:] = new
        with open(COST_EST, "w", newline="") as f:
            csv.writer(f).writerows(rows)
        return float(new[1])
    usd = tr.inductor_cost(ind, "Asian")
    basis = (f"ESTIMATE from sim/out/pv_design/cell_spec.json inductor (sim/pv_tradeoff.py inductor_cost, Asian core): Litz {ind['m_cu']:.2f} kg x "
             f"{tr.C_CU_KG:.1f} USD/kg (14.4 Cu at LME cash 14,355 USD/t on 2026-10-02 + 18 drawing and insulation premium) = "
             f"{ind['m_cu'] * tr.C_CU_KG:.0f} + {ind['n_stack']} E-core sets {ind['m_core']:.2f} kg POCO NPC 26 (Kool Mu MAX 26u equivalent, "
             f"sim/data/asian_magnetic_materials.csv) at {tr.C_CORE_KG['Asian']:.0f} USD/kg (ESTIMATE, no quote: ~40 % of the 35 USD/kg Kool "
             f"Mu MAX basis) = {ind['m_core'] * tr.C_CORE_KG['Asian']:.0f} + bobbin, insulation, NTC and terminals 6 + winding labour and test "
             f"12, +15 % scrap and margin = {usd:.0f}; with the Magnetics core {tr.inductor_cost(ind, 'Magnetics'):.0f}; POCO 80/44 E shape "
             f"not confirmed; no quote")
    for r in rows:
        if r and r[0] in ("L_CELL 224uH (CUSTOM)", "L_CELL*"):
            r[1], r[2], r[3] = f"{usd:.0f}", basis if r[0] != "L_CELL*" else "ESTIMATE: pattern fallback for a future L_CELL variant, taken as the 224 uH estimate", "low"
    with open(COST_EST, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    return usd


def merge_worse(spec, other):
    """worse of primary and alternate in every check: efficiencies min, stresses / losses / temperatures max"""
    if not other:
        return
    lo = lambda a, b: min(a, b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a
    hi = lambda a, b: max(a, b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a
    e, eo = spec["efficiency"], other["efficiency"]
    e["peak"] = lo(e["peak"], eo["peak"])
    e["corners"] = {k: lo(v, eo["corners"][k]) for k, v in e["corners"].items()}
    for k, v in spec["worst_case_stresses"].items():
        if k.endswith("rating_V") or "continuous" in k:
            continue
        spec["worst_case_stresses"][k] = hi(v, other["worst_case_stresses"].get(k))
    for blk, keys in (("damper", ("power_per_leg_W_max", "power_per_leg_W_at_trip_1100V", "peak_current_A", "peak_voltage_per_resistor_V",
                                  "energy_per_cycle_uJ_at_trip", "capacitor_voltage_per_element_V")),
                      ("decoupling", ("peak_current_per_cap_A", "peak_current_per_cap_with_recovery_A", "rms_per_cap_A"))):
        for k in keys:
            spec[blk][k] = hi(spec[blk][k], other[blk][k])
    mc, mo = spec["gate_drive"]["miller_check"], other["gate_drive"]["miller_check"]
    for k in ("V_GS_pin_peak_V", "V_GS_die_peak_V", "miller_current_per_device_A_peak", "victim_dvdt_V_per_ns_peak", "edge_dvdt_V_per_ns"):
        mc[k] = hi(mc[k], mo[k])
    mc["fallback_variant"] = {"source": mo["source"], "result": mo["result"]}
    b, bo = spec["bus_reverse_clamp"], other["bus_reverse_clamp"]
    for k in ("per_device_peak_A_max", "per_device_I2t_A2s_max", "per_device_energy_mJ_max"):
        b["body_diode_share_with_clamp"][k] = hi(b["body_diode_share_with_clamp"][k], bo["body_diode_share_with_clamp"][k])
    b["body_diode_share_with_clamp"]["I_DM_A"] = lo(b["body_diode_share_with_clamp"]["I_DM_A"], bo["body_diode_share_with_clamp"]["I_DM_A"])
    spec["worst_of"] = "efficiency, worst_case_stresses, damper, decoupling, gate_drive.miller_check and the clamp's body-diode share " \
                       "are the worse of device_primary and device_alternate"


def cost_block(des, n_clamp=None):
    """USD per cell and per module (power stage of the cell, Gate-0b cost model; module = cells only)"""
    c = tr.cost_usd(des, n_clamp=n_clamp)
    return {"per_cell": {k: round(v, 2) for k, v in c.items()}, "per_module_cells_only": {"3_cells": round(3 * c["total"], 0),
            "4_cells": round(4 * c["total"], 0)}, "basis": "sim/pv_tradeoff.py cost_usd(): gen/data/prices.csv (largest break <= 1000 pcs) "
            "and labelled ESTIMATES (sim/out/pv_tradeoff/gate0b.md); port board, control, fans and enclosure not included"}


# ------------------------------------------------------------------------------------------------ envelope scans
def scan(des, step=50.0, loads=(1.0, 0.5, 0.25, 0.1), t_air=tr.T_AIR):
    vs = np.arange(tr.V_MIN, tr.V_MAX + 0.1, step)
    out = []
    for fr in loads:
        for va in vs:
            for vb in vs:
                p = fr * tr.p_limit(va, vb)
                r = tr.cell_losses(des, va, vb, p, t_air=t_air)
                out.append({"load": fr, "va": va, "vb": vb, "p": p, "eff": r["eff"], "ptot": r["ptot"], "tj": r["tj_max"],
                            "mode": r["mode"], "r": r})
    return out


def stresses(env):
    """worst case per component over the A->B scan; B->A is the mirror (S1<->S3, S2<->S4, port A<->B), so every
    switch position and both ports take the worst of the pair."""
    full = [e for e in env if e["load"] == 1.0]
    w = {"irms_pos": 0.0, "ploss_dev": 0.0, "tj": 0.0, "il_pk": 0.0, "il_rms": 0.0, "ripple": 0.0, "icap": 0.0,
         "p_ind": 0.0, "p_core": 0.0, "p_cu": 0.0}
    at = {}
    for e in full:
        r, st = e["r"], e["r"]["st"]
        keys = [k for k in st if k.startswith("rms2_")]
        irms = math.sqrt(max(st[k] for k in keys))
        cand = {"irms_pos": irms, "ploss_dev": max(r["pdev"].values()) / 1.0, "tj": r["tj_max"], "il_pk": max(abs(st["imax"]), abs(st["imin"])),
                "il_rms": st["irms"], "ripple": st["ripple"], "icap": max(st["icap_rms_A"], st["icap_rms_B"]),
                "p_ind": r["loss"]["core"] + r["loss"]["cu"], "p_core": r["loss"]["core"], "p_cu": r["loss"]["cu"]}
        for k, v in cand.items():
            if v > w[k]:
                w[k] = v
                at[k] = (e["va"], e["vb"], e["p"])
    return w, at


def module_ripple(des, ncell, points=((950, 550), (550, 950), (1000, 500), (500, 1000), (800, 700))):
    """N cells interleaved by 360/N deg on common port banks: port-current AC rms and port voltage ripple"""
    res = []
    for va, vb in points:
        p = tr.p_limit(va, vb)
        w = tr.cell_losses(des, va, vb, p, waveform_only=True)
        n = 4096
        il, ia, ib = tr.sample(w, n)
        for port, sig, v in (("A", ia, va), ("B", ib, vb)):
            tot = sum(np.roll(sig, int(round(k * n / ncell))) for k in range(ncell))
            ac = tot - tot.mean()
            ccell = des["caps"][port]["C"]
            q = np.cumsum(ac) * w["T"] / n
            res.append({"va": va, "vb": vb, "port": port, "irms_ac": float(np.sqrt(np.mean(ac ** 2))),
                        "irms_cell_alone": float(np.sqrt(np.mean((sig - sig.mean()) ** 2))),
                        "dv_pp": float((q.max() - q.min()) / (ncell * ccell)), "v": v, "C_total": ncell * ccell})
    return res


# ------------------------------------------------------------------------------------------------ ngspice FSBB deck
def fsbb_deck(des, va, vb, p, tag, a_fit, rg=None, periods=30, dv_src=0.0, rg_on=None):
    """switching-level FSBB: VDMOS devices (npar in parallel, own gate resistors), floating gate drives with dead time,
    lumped commutation-loop inductance per leg, C4AQ port banks with ESR, linear L = L_inc(I_dc) with DCR.
    Port A = stiff source, port B = source behind 1 ohm (sets the current, damps the LC); started from the analytic
    steady state.  Writes sim/spice/pv_fsbb_<tag>.cir and returns waveforms of the last two periods."""
    d, n, fsw = des["d"], des["npar"], des["fsw"]
    w = tr.cell_losses(des, va, vb, p, waveform_only=True)
    st = tr.wf_stats(w)
    T = 1.0 / fsw
    L, rdc = w["L"], des["ind"]["rdc20"] * (1 + dv.CU_ALPHA * (110 - 20))
    lloop = tr.L_LOOP["2L"]
    td = tr.T_DEAD
    vg_on, vg_off = d["vgs_on"], d["vgs_off"]
    rg = d["rg_ext"] if rg is None else rg
    rg_on = rg if rg_on is None else rg_on
    rs = 2.0
    ib = st["iport_B"]
    vb_src = vb - ib * rs + dv_src
    lines = [f"* PVCELL FSBB switching-level deck '{tag}': V_A={va:.0f} V, V_B={vb:.0f} V, P={p/1e3:.1f} kW, "
             f"{d['mfr']} {des['dev']} x{n} per position, f_sw={fsw/1e3:.1f} kHz, L={L*1e6:.1f} uH. Generated by sim/pv_design.py",
             tr.vdmos_card("dut", d, a_fit),
             f"VA pas 0 {va}", "RA pas pa 5m",
             f"CA pa ca {des['caps']['A']['C']} ic={va}", f"RCA ca 0 {des['caps']['A']['esr'] / des['caps']['A']['n']}",
             f"LlA pa ta {lloop}", f"RdA pa ta {tr.R_DAMP}",
             f"CB pb cb {des['caps']['B']['C']} ic={vb}", f"RCB cb 0 {des['caps']['B']['esr'] / des['caps']['B']['n']}",
             f"LlB pb tb {lloop}", f"RdB pb tb {tr.R_DAMP}", "VSB pb pbr 0", f"RS pbr pbs {rs}", f"VB pbs 0 {vb_src:.3f}",
             f"L1 swa xl {L} ic={st['iavg']:.4f}", f"RL xl swb {rdc:.5f}"]
    lines.append(".model dsteer D(Is=1e-12 N=0.3)")

    def gate(name, drv, g):          # split turn-on / turn-off resistors (diode-steered, = UCC21710 OUTH/OUTL)
        if abs(rg_on - rg) < 1e-9:
            return [f"R{name} {drv} {g} {rg}"]
        return [f"D{name}n {drv} {g}n dsteer", f"R{name}n {g}n {g} {rg_on}", f"D{name}f {g}f {drv} dsteer", f"R{name}f {g} {g}f {rg}"]
    for leg, top, sw, dleg, iout in (("A", "ta", "swa", w["da"], 1.0), ("B", "tb", "swb", w["db"], -1.0)):
        for k in range(n):
            lines += [f"M{leg}t{k} {top} g{leg}t{k} {sw} dut", f"M{leg}b{k} {sw} g{leg}b{k} 0 dut"]
            lines += gate(f"g{leg}t{k}", f"g{leg}td", f"g{leg}t{k}") + gate(f"g{leg}b{k}", f"g{leg}bd", f"g{leg}b{k}")
        if dleg >= 1.0:
            lines += [f"Vg{leg}t g{leg}td {sw} {vg_on}", f"Vg{leg}b g{leg}bd 0 {vg_off}"]
            continue
        c = T / 2
        t_on, t_off = c - dleg * T / 2, c + dleg * T / 2
        tr_ = 10e-9
        if iout > 0:          # current out of the node: top edge defines the node voltage, dead time taken from bottom
            top_on, top_w = t_on, dleg * T
            bot_on, bot_w = t_off + td, (1 - dleg) * T - 2 * td
        else:                 # current into the node: bottom edge defines it, dead time taken from top
            top_on, top_w = t_on + td, dleg * T - 2 * td
            bot_on, bot_w = t_off, (1 - dleg) * T
        lines += [f"Vg{leg}t g{leg}td {sw} PULSE({vg_off} {vg_on} {top_on:.6e} {tr_} {tr_} {top_w - tr_:.6e} {T:.6e})",
                  f"Vg{leg}b g{leg}bd 0 PULSE({vg_off} {vg_on} {bot_on % T:.6e} {tr_} {tr_} {bot_w - tr_:.6e} {T:.6e})"]
    tstop = periods * T
    lines += [".save i(L1) v(swa) v(swb) v(ta) v(tb) v(pa) v(pb) i(VA) i(VSB)",
              f".tran 1n {tstop:.6e} {tstop - 2 * T:.6e} 2n uic", "OPTIONS",
              ".control", "run", "linearize i(L1) v(swa) v(swb) v(ta) v(tb) v(pa) v(pb) i(VA) i(VSB)",
              f"wrdata pv_fsbb_{tag}.dat i(L1) v(swa) v(swb) v(ta) v(tb) v(pa) v(pb) i(VA) i(VSB)",
              "quit", ".endc", ".end"]
    deck = os.path.join(tr.SPICE, f"pv_fsbb_{tag}.cir")
    for opt in tr.SOLVER_OPTIONS:
        open(deck, "w").write("\n".join(lines).replace("OPTIONS", opt) + "\n")
        try:
            tr.ngspice(deck, timeout=3 * tr.SOLVER_TIMEOUT)
            break
        except RuntimeError:
            continue
    else:
        raise RuntimeError(f"no solver setting converged for {deck}")
    t, (il, vswa, vswb, vta, vtb, vpa, vpb, iva, irs) = tr.wrdata(os.path.join(tr.SPICE, f"pv_fsbb_{tag}.dat"))
    span = t[-1] - t[0]
    d_stored = (0.5 * des["caps"]["A"]["C"] * (vpa[-1] ** 2 - vpa[0] ** 2) + 0.5 * des["caps"]["B"]["C"] * (vpb[-1] ** 2 - vpb[0] ** 2)
                + 0.5 * L * (il[-1] ** 2 - il[0] ** 2))
    return {"t": t, "il": il, "vds_Ab": vswa, "vds_At": vta - vswa, "vds_Bb": vswb, "vds_Bt": vtb - vswb, "va": vpa, "vb": vpb,
            "vb_src": vb_src, "rs": rs, "iport_B_target": ib, "iport_B_sim": float(np.trapezoid(irs, t) / (t[-1] - t[0])),
            "pin": -np.trapezoid(iva * vpa, t) / span, "pout": np.trapezoid(irs * vpb, t) / span, "dE_W": d_stored / span,
            "deck": os.path.relpath(deck, os.path.dirname(HERE)), "w_analytic": w, "st_analytic": st}


def spice_compare(des, a_fit, dpt_res, rg, rg_on):
    out = []
    for tag, va, vb in SPICE_POINTS:
        p = tr.p_limit(va, vb)
        s = fsbb_deck(des, va, vb, p, tag, a_fit, rg=rg, rg_on=rg_on)
        # open-loop deck: one correction of the port-B source for the conduction drop, so the run sits at the target current
        s = fsbb_deck(des, va, vb, p, tag, a_fit, rg=rg, rg_on=rg_on, dv_src=-(s["iport_B_target"] - s["iport_B_sim"]) * s["rs"])
        t, il = s["t"], s["il"]
        T = 1.0 / des["fsw"]
        last = t >= t[-1] - T
        rip_sim = il[last].max() - il[last].min()
        va_s, vb_s, i_s = float(np.mean(s["va"][last])), float(np.mean(s["vb"][last])), float(np.mean(il[last]))
        # analytic model re-evaluated at the simulated operating point (same L, same duties)
        w = tr.waveform(va_s, vb_s, p, des["fsw"], s["w_analytic"]["L"], 2)     # ripple depends on V_A, V_B, L, f only
        rip_an = tr.wf_stats(w)["ripple"]
        vmax = {k: float(s[k][last].max()) for k in ("vds_At", "vds_Ab", "vds_Bt", "vds_Bb")}
        # analytic device voltage: blocking port voltage + overshoot.  Hard turn-off device (S1 for leg A, S4 for leg B):
        # L di/dt overshoot, scaled with the turn-off (peak) current.  Complementary device (S2 / S3) during the hard turn-on:
        # C_oss dv/dt ringing, scaled with the blocking voltage (it barely depends on the load current).  Both from the
        # worst-case commutation run.
        sta = tr.wf_stats(s["w_analytic"])
        k_off = dpt_res["dv_os_off"] / dpt_res["i_load"]
        k_on = max(dpt_res["dv_os_on"], 0.0) / dpt_res["vdc"]
        imax = sta["imax"]
        sw_a, sw_b = s["w_analytic"]["da"] < 1, s["w_analytic"]["db"] < 1       # static leg: top on (0 V), bottom blocks
        v_an = {"vds_At": va_s + k_off * imax if sw_a else 0.0, "vds_Ab": va_s * (1 + (k_on if sw_a else 0.0)),
                "vds_Bt": vb_s * (1 + k_on) if sw_b else 0.0, "vds_Bb": vb_s + (k_off * imax if sw_b else 0.0)}
        out.append({"tag": tag, "va": va_s, "vb": vb_s, "i_avg": i_s, "ripple_sim": rip_sim, "ripple_an": rip_an,
                    "vmax_sim": vmax, "vmax_an": v_an, "pin": s["pin"], "pout": s["pout"], "dE_W": s["dE_W"], "deck": s["deck"], "s": s})
    return out


# ------------------------------------------------------------------------------------------------ physical leg (PVR-01/02/03)
# Leg as drawn on PVCELL 03_power: port bulk bank -> 15 nH bus (gen/pvcell.py L_BUS_REQ, layout requirement) -> leg
# decoupling -> rest of the commutation loop -> device pins, with the RC damper (R301-R304, C303-C308) at the pins.
DAMPER = {"r_el": 4.99, "n_r": 2, "c_el": 4.7e-9, "n_c": 2, "c_el_V": 2000.0}
L_BUS_BULK = 15e-9
L_REST = tr.L_LOOP["2L"] - dv.C4AQ_DEC["C4AQUBU4100A1WJ"]["esl"] / 2    # board + devices: the 20 nH loop less the drawn 2 x 1 uF ESL
DEC_OPTIONS = [("C4AQUBU4100A1WJ", 2), ("C4AQUBU4100A1WJ", 4), ("C4AQUBU4220A1YJ", 3), ("C4AQUBU4180A1XJ", 4), ("C4AQUBU4220A1YJ", 4)]
DEC_CHOICE = ("C4AQUBU4220A1YJ", 3)   # result of decoupling_study() (asserted in run()); the damper table uses it
DEC_MARGIN = 1.2                      # peak per capacitor <= Ipkr / 1.2 (VDMOS, Qoss) and <= Ipkr with the recovery surrogate
DEC_TON = (0.6e-6, 1.0e-6, 1.5e-6, 2.2e-6, 3.0e-6)   # on-times: the decoupling-bulk resonance can add to the second edge
DAMP_V = [250.0, 550.0, 800.0, 1000.0, 1100.0]
DAMP_I = [2.0, 20.0, 45.0]            # + the hardware trip current


def leg_net(des, dec=None, c_rr=0.0):
    part, nd = dec or des["dec"]
    p, cb = dv.C4AQ_DEC[part], des["caps"]["A"]
    cbp = dv.C4AQ[cb["part"]]
    return {"c_bulk": cb["C"], "esl_bulk": cbp["esl"] / cb["n"], "esr_bulk": cbp["esr"] / cb["n"], "l_bus": L_BUS_BULK,
            "c_dec": nd * p["C"], "esl_dec": p["esl"] / nd, "esr_dec": p["esr"] / nd, "l_rest": L_REST,
            "r_damp": DAMPER["n_r"] * DAMPER["r_el"], "c_damp": DAMPER["c_el"] / DAMPER["n_c"], "c_rr": c_rr}


def c_rr(des, v):
    """recovery-charge surrogate (ESTIMATE): datasheet Q_rr less Q_oss(1000 V) per device as a linear C charged to v"""
    d = des["d"]
    return des["npar"] * max(d["qrr"] - tr.qoss(d, 1000.0), 0.0) / v


def leg_run(des, v, i, tag, dec=None, crr=0.0, mult=None, deck=None, **kw):
    """physical-leg run: tr.dpt (default) or tr.gate_deck (kw mode=...), calibrated model, design gate resistors"""
    cal = tr.calibrate(des["d"], des["dev"])
    m = des["rg_on_mult"] if mult is None else mult
    lg = leg_net(des, dec, crr)
    for k in range(4):           # ponytail: nudge off solver dead spots at t ~ 0.5 ps (0.2 % current, 2 % ESR per retry)
        try:
            return (deck or tr.dpt)(des["d"], des["npar"], v, i * (1 + 0.002 * k), tr.L_LOOP["2L"], tag, rg=cal["rg_eff"],
                                    a=cal["a"], rg_on=cal["rg_eff"] * m, rks=des["rg_ks"],
                                    leg=dict(lg, esr_dec=lg["esr_dec"] * (1 + 0.02 * k)), **kw)
        except RuntimeError:
            continue
    raise RuntimeError(f"leg deck {tag} did not converge")


def damper_table(des):
    """RC-damper energy per hard turn-on / hard turn-off edge on a (V, I) grid, plus the Miller and decoupling numbers
    of every run (calibrated VDMOS, Qoss only).  Deck sim/spice/pv_dpt_leg.cir is the last run (worst point)."""
    ii = DAMP_I + [des["i_ocp"]]
    tab = {"v": DAMP_V, "i": ii, "e_on": [], "e_off": [], "runs": {}}
    for v in DAMP_V:
        ro, rf = [], []
        for i in ii:
            try:
                r = leg_run(des, v, i, "leg")
            except RuntimeError:          # solver dead spot: filled from the next current of the row below (conservative)
                ro.append(None)
                rf.append(None)
                continue
            ro.append(r["e_damp_on"])
            rf.append(r["e_damp_off"])
            tab["runs"][(v, i)] = {k: r[k] for k in ("e_damp_on", "e_damp_off", "i_damp_pk", "i_dec_pk", "v_pk", "dv_os_off", "dv_os_on",
                                                    "dvdt_on", "vgs_off_peak", "vgs_off_peak_die", "i_miller_pk", "e_on", "deck")}
        for row in (ro, rf):
            for k in range(len(row) - 1, -1, -1):
                if row[k] is None:
                    row[k] = next((x for x in row[k + 1:] if x is not None), None) or next(x for x in row[::-1] if x is not None)
        tab["e_on"].append(ro)
        tab["e_off"].append(rf)
    assert (tr.V_OVP, des["i_ocp"]) in tab["runs"], "damper table: the worst corner must converge"
    return tab


def decoupling_study(des):
    """leg decoupling at the worst commutation (OVP, trip current): first option of DEC_OPTIONS whose peak current per
    capacitor is <= Ipkr / DEC_MARGIN over DEC_TON, <= Ipkr with the recovery surrogate, and whose ESL keeps the loop"""
    v, i = tr.V_OVP, des["i_ocp"]
    esl_max = dv.C4AQ_DEC["C4AQUBU4100A1WJ"]["esl"] / 2
    rows, choice = [], None
    for part, nd in DEC_OPTIONS:
        p = dv.C4AQ_DEC[part]
        rs = []
        for t in DEC_TON:        # a solver dead spot at one on-time drops that point, not the option
            try:
                rs.append(leg_run(des, v, i, "leg_dec", dec=(part, nd), ton=t))
            except RuntimeError:
                continue
        if not rs:
            rows.append({"part": part, "n": nd, "esl_nH": p["esl"] / nd * 1e9, "C_uF": nd * p["C"] * 1e6, "ipkr": p["ipkr"],
                         "pk_cap": float("nan"), "rms_cap": float("nan"), "v_pk": float("nan"), "e_damp": float("nan"),
                         "pk_cap_rr": None, "ok": False, "note": "no convergence"})
            continue
        w = max(rs, key=lambda r: r["i_dec_pk"])
        row = {"part": part, "n": nd, "esl_nH": p["esl"] / nd * 1e9, "C_uF": nd * p["C"] * 1e6, "ipkr": p["ipkr"],
               "pk_cap": w["i_dec_pk"] / nd, "rms_cap": math.sqrt(w["i_dec_sq"] * des["fsw"]) / nd, "v_pk": w["v_pk"],
               "e_damp": w["e_damp_on"] + w["e_damp_off"], "pk_cap_rr": None}
        row["ok"] = row["pk_cap"] <= p["ipkr"] / DEC_MARGIN and p["esl"] / nd <= esl_max + 1e-12
        if row["ok"] and choice is None:
            try:
                rr = leg_run(des, v, i, "leg_dec_rr", dec=(part, nd), crr=c_rr(des, v))
                row["pk_cap_rr"], row["v_pk_rr"] = rr["i_dec_pk"] / nd, rr["v_pk"]
                row["ok"] = row["pk_cap_rr"] <= p["ipkr"]
            except RuntimeError:                 # surrogate run did not converge: the Q_oss result decides, stated
                row["pk_cap_rr"], row["v_pk_rr"], row["note"] = None, None, "Q_rr surrogate run did not converge"
            choice = (part, nd) if row["ok"] else None
        rows.append(row)
    return {"rows": rows, "choice": choice, "esl_max_nH": esl_max * 1e9}


def miller_study(des, gd):
    """false-turn-on check of the held-off device: the gate-driver engineer's rev-5 ngspice result (sim/gdrv_miller.py,
    gen/gdrv.py MILLER_SIM) for the device set it was run for; one physical-leg run here for the device peak and dv/dt"""
    worst = (tr.V_OVP, des["i_ocp"])
    agg = leg_run(des, *worst, "leg_agg")
    w = {"v_pk": agg["v_pk"], "dvdt_on": agg["dvdt_on"], "e_on": agg["e_on"]}
    sim = f"{des['npar']} x {des['dev']}" == GDRV_PRESET
    w.update({"vgs_off_peak": gd["gd_pin"] if sim else None, "vgs_off_peak_die": gd["gd_die"] if sim else None,
              "i_miller_pk": gd["gd_im"] if sim else None, "dvdt": gd["miller_dvdt"] * 1e9 if sim else None,
              "die_ideal_loop": gd["die_l0"] if sim else None,
              "source": "sim/gdrv_miller.py rev 5 (AO3400A clamp loop <= 1 nH), gen/gdrv.py MILLER_SIM" if sim else
                        "NOT simulated with the rev-5 network: gen/gdrv.py rev 5 does not carry this device's rail (its AO3400A "
                        "clamp FET would see 24.6 V > 80 % of 30 V at +20/-4 V); previous round (rev-4 PNP network) die +1.42 V"})
    return {"worst": w, "pts": {worst: w}, "own": {}, "de_on_vic": None, "die_max": w["vgs_off_peak_die"], "pin_max": w["vgs_off_peak"],
            "im_max": w["i_miller_pk"]}


# ------------------------------------------------------------------------------------------------ port short: bus reverse clamp
PORT_REPORT = os.path.join(os.path.dirname(HERE), "sim", "out", "port_design", "report.md")
PORT_SPEC = os.path.join(os.path.dirname(HERE), "sim", "out", "port_design", "port_spec.json")
CLAMP_PART = "WND75P16W6"      # WeEn (CN), SRC-1; the Vishay VS-60EPS16-M3 of the previous round is the reference
L_DEV_CLAMP = 6e-9       # H per TO-247AC 2L rectifier incl. leads, vias and its share of the bank bus (ESTIMATE)
L_LEG_BRANCH = 15e-9     # H, bank terminals -> S1 + S2 body diodes -> bank (ESTIMATE: the 20 nH loop less the bank's own ESL)
CJ_CLAMP = 300e-12       # F per rectifier at low voltage (ESTIMATE, not in the datasheet; needed for the solver)
N_CLAMP_SET = [4, 6, 8, 10, 12, 16]
K_SHARE = 1.2            # worst / mean current of a paralleled rectifier group (ESTIMATE: V_F max/typ 1.15/1.05 at 75 A,
                         # WND75P16W6 p6, ~10 % resistive spread, + 10 % for unequal lead/branch inductance)
L_STRAY_PORT = 10e-9     # H per cell, port-board clamp -> cell bank (laminated bus; deliberately LOW ESTIMATE - cables are 100s of nH)
VT27 = 0.025852


def diode_fit(points):
    """ngspice diode (Is, N, Rs) through datasheet (I, V) points: Rs from the two highest points, N*Vt and Is by least squares"""
    i = np.array([q[0] for q in points], float)
    v = np.array([q[1] for q in points], float)
    rs = (v[-1] - v[-2]) / (i[-1] - i[-2])
    nvt, c0 = np.linalg.lstsq(np.vstack([np.log(i), np.ones_like(i)]).T, v - rs * i, rcond=None)[0]
    isat = math.exp(-c0 / nvt)
    resid = float(np.max(np.abs(nvt * np.log(i / isat) + rs * i - v)))
    return isat, nvt / VT27, rs, resid


def port_loop(ncell):
    """dump loop of the port board for the N-cell module, read from sim/out/port_design (not re-estimated)"""
    rep = open(PORT_REPORT).read()
    ps = json.load(open(PORT_SPEC))
    var = ps["variants"][str(int(ncell * tr.I_MAX))]
    l_loop = float(re.search(r"\| A_L_LOOP \| ([0-9.e+-]+) \|", rep).group(1))
    r_x = float(re.search(r"\| A_R_LOOP_X \| ([0-9.e+-]+) \|", rep).group(1))
    r_fuse = var["fuse_loss_W"] / var["fuse_In_A"] ** 2            # cold fuse link P/In^2, as the port design uses
    m5 = re.search(r"\| PV %d cells \| [0-9.]+ \| [0-9.]+ \| [0-9.]+ \| [0-9.]+ \| \w+ \| [0-9.]+ \| \w+ \| ([0-9.]+) \|" % ncell, rep)
    melt = {160.0: 19e3, 250.0: 80e3}[var["fuse_In_A"]]            # port report 6.6: minimum melting I2t of the 160 / 250 A links
    return {"L": l_loop, "R": r_x + 2 * r_fuse, "r_x": r_x, "r_fuse": r_fuse, "fuse": var["fuse_mpn"], "melt_i2t": melt,
            "L_for_5kA": float(m5.group(1)) * 1e-6 if m5 else None}


def dump_run(des, ncell, port, v0, n_clamp, l_cl=None, l_main=None, tag=None, part=CLAMP_PART, l_stray=None):
    """bolted short at a port terminal: the N cell banks of that port discharge through the port-board loop.  Per-cell
    equivalent of N identical cells (shared loop x N).  Branches across the bank terminals: the leg's two series body-
    diode positions (npar devices each, MSC035 Fig.1-9 fit) behind L_LEG_BRANCH, and the clamp group (n_clamp rectifiers)
    behind l_cl.  l_stray: the clamp sits on the port board instead, shared by the N cells, l_stray (per cell) away from
    each bank.  Writes sim/spice/pv_dump_<tag>.cir."""
    d, n = des["d"], des["npar"]
    lp = port_loop(ncell)
    cap = des["caps"][port]
    cp = dv.C4AQ[cap["part"]]
    c, esr, esl = cap["C"], cp["esr"] / cap["n"], cp["esl"] / cap["n"]
    lm = (lp["L"] if l_main is None else l_main) * ncell
    rm = lp["R"] * ncell
    bi, bn, brs, _ = diode_fit([(2.0, 2.9)] + d["body_iv_25"])
    rc = dv.RECTIFIERS[part]
    l_cl = L_DEV_CLAMP / max(n_clamp, 1) if l_cl is None else l_cl
    ci = 10.0 / math.exp(rc["vf_to_150"] / 0.03)                    # sharp knee at V_F(TO), slope r_t of the p2 table
    m_cl = n_clamp / ncell if l_stray else n_clamp                  # shared port clamp: per-cell equivalent 1/N of it
    tag = tag or f"{ncell}c_{port}_{v0:.0f}V_n{n_clamp}"
    cjo = 0.5 * (d["coss"] - d["crss"]) * (1 + d["c_at"] / 3.0) ** 0.5
    lines = [f"* PVCELL port-{port} terminal short, {ncell} cells, bank {v0:.0f} V, clamp {n_clamp} x {part}"
             f"{' (none)' if n_clamp == 0 else ''}{' on the port board' if l_stray else ''}, per-cell equivalent of the shared loop. "
             f"Generated by sim/pv_design.py",
             f".model dbody D(Is={bi:.4g} N={bn:.4g} Rs={brs:.4g} Cjo={cjo:.4g} Vj=3 M=0.5)",
             f".model dcl D(Is={ci * max(m_cl, 1e-9):.4g} N={0.03 / VT27:.4g} Rs={rc['rt_150'] / max(m_cl, 1e-9):.4g} "
             f"Cjo={CJ_CLAMP * m_cl:.4g} Vj=0.7 M=0.5)",
             f"C1 c1 0 {c} ic={v0}", f"Lesl c1 c2 {esl}", f"Resr c2 bp {esr}"]
    pt = "bp"
    if l_stray:
        pt = "pt"
        lines.append(f"Lst bp pt {l_stray}")
        l_cl = l_cl * ncell
    lines += [f"Lm {pt} m1 {lm}", f"Rm m1 0 {rm}",
             "Vsl 0 lx 0", f"Dlo lx l2 dbody m={n}", f"Dhi l2 l1 dbody m={n}", f"Lleg l1 bp {L_LEG_BRANCH}"]
    vecs = "v(c1) v(bp) i(Lm) i(Lesl) i(Vsl) v(l1)"
    if n_clamp:
        lines += ["Dcl 0 k1 dcl", f"Lcl k1 {pt} {l_cl}"]
        vecs += " i(Lcl) v(k1)"
    tstop = max(1.5e-3, 12 * lm / rm)
    tstep = max(2e-9, tstop / 4e5)                    # <= 4e5 output points whatever the decay time
    lines += [f".save {vecs}", f".tran {tstep:.3g} {tstop:.4g} 0 {max(20e-9, tstop / 4e5):.3g} uic", "OPTIONS", ".control", "run",
              f"linearize {vecs}",
              f"wrdata pv_dump_{tag}.dat {vecs}", "quit", ".endc", ".end"]
    deck = os.path.join(tr.SPICE, f"pv_dump_{tag}.cir")
    for opt in [".options method=gear maxord=2 reltol=2e-3 abstol=1e-6", ".options method=gear reltol=1e-3 abstol=1e-6",
                ".options method=trap trtol=7 abstol=1e-6"]:
        open(deck, "w").write("\n".join(lines).replace("OPTIONS", opt) + "\n")
        try:
            tr.ngspice(deck, timeout=300)
            break
        except RuntimeError:
            continue
    else:
        raise RuntimeError(f"no solver setting converged for {deck}")
    t, vv = tr.wrdata(os.path.join(tr.SPICE, f"pv_dump_{tag}.dat"))
    vc, vbp, im, icap, ileg, vl1 = vv[:6]
    icl, vk1 = (vv[6], vv[7]) if n_clamp else (0 * t, 0 * t)
    idev = ileg / n                                                     # per body diode (npar in parallel per position)
    p_body = np.maximum(ileg, 0) * np.maximum(-vl1, 0)                  # both positions together
    p_cl = np.maximum(icl, 0) * np.maximum(-vk1, 0)
    e_in = 0.5 * c * v0 ** 2
    e_out = (np.trapezoid(im ** 2 * rm + icap ** 2 * esr + p_body + p_cl, t)
             + 0.5 * c * vc[-1] ** 2 + 0.5 * lm * im[-1] ** 2 + 0.5 * esl * icap[-1] ** 2)
    i2t_cl = float(np.trapezoid(icl ** 2, t))
    cum = np.cumsum(np.concatenate([[0.0], 0.5 * (icl[1:] ** 2 + icl[:-1] ** 2) * np.diff(t)]))
    t95 = float(t[np.searchsorted(cum, 0.95 * cum[-1])]) if cum[-1] > 0 else 0.0
    ksh = K_SHARE if (n_clamp > 1 and not l_stray) else 1.0      # worst device of a paralleled group
    return {"ncell": ncell, "port": port, "v0": v0, "n_clamp": n_clamp, "l_cl": l_cl if n_clamp else None, "l_main": lm / ncell,
            "i_total_pk": float(ncell * im.max()), "i_cell_pk": float(im.max()), "vc_min": float(vc.min()), "vbp_min": float(vbp.min()),
            "body_pk": float(idev.max()), "body_i2t": float(np.trapezoid(np.maximum(idev, 0) ** 2, t)),
            "body_e": float(np.trapezoid(p_body, t) / (2 * n)), "clamp_pk": float(icl.max()), "clamp_i2t": i2t_cl, "t95": t95,
            "clamp_dev_pk": ksh * float(icl.max()) / max(m_cl, 1e-9), "clamp_dev_i2t": ksh ** 2 * i2t_cl / max(m_cl, 1e-9) ** 2,
            "part": part, "k_share": ksh,
            "l_stray": l_stray,
            "fuse_i2t": float(np.trapezoid((ncell * im) ** 2, t)), "e_in": e_in, "e_bal": e_out / e_in, "q": math.sqrt(lm / c) / (rm + esr),
            "deck": os.path.relpath(deck, os.path.dirname(HERE)), "t": t, "im": im, "icl": icl, "ileg": ileg, "vc": vc}


def clamp_allowed_i2t(t95, part=None):
    """allowed I2t of one rectifier for a pulse of duration t95: from the I_FSM-vs-t_p curve (half sine: I_FSM^2 t_p / 2,
    log-interpolated) where the datasheet gives one, else the I2 sqrt(t) rating"""
    rc = dv.RECTIFIERS[part or CLAMP_PART]
    if "ifsm_curve" in rc:
        ts, ia = zip(*rc["ifsm_curve"])
        t = min(max(t95, ts[0]), ts[-1])
        i = math.exp(float(np.interp(math.log(t), np.log(ts), np.log(ia))))
        return i * i * t / 2.0
    lo, hi = rc["i2sqrt_t_range_s"]
    return rc["i2sqrt_t"] * math.sqrt(min(max(t95, lo), hi))


def bus_reverse_clamp(des):
    d = des["d"]
    idm = d["idm"]
    cases = {}
    for ncell in (3, 4):
        for port in ("A", "B"):
            for v0 in (1000.0, tr.V_OVP):
                cases[(ncell, port, v0, 0)] = dump_run(des, ncell, port, v0, 0)
    # clamp count: smallest n with the body diodes <= 80 % of I_DM and the clamp I2t <= 50 % of its I2 sqrt(t) rating, worst case
    sweep = {}
    for nc in N_CLAMP_SET:
        sweep[nc] = dump_run(des, 3, "A", tr.V_OVP, nc)
    ok = [nc for nc in N_CLAMP_SET if sweep[nc]["body_pk"] <= 0.8 * idm
          and sweep[nc]["clamp_dev_i2t"] <= 0.5 * clamp_allowed_i2t(sweep[nc]["t95"])]
    n_cl = ok[0] if ok else N_CLAMP_SET[-1]      # none passes: the largest group, flagged by the asserts
    for ncell in (3, 4):
        for port in ("A", "B"):
            for v0 in (1000.0, tr.V_OVP):
                cases[(ncell, port, v0, n_cl)] = dump_run(des, ncell, port, v0, n_cl)
    # branch inductance the chosen group tolerates (body diodes <= I_DM)
    lsw = {}
    for l_cl in (0.25e-9, 0.5e-9, 0.75e-9, 1.0e-9, 1.5e-9, 2.0e-9, 3.0e-9):
        lsw[l_cl] = dump_run(des, 3, "A", tr.V_OVP, n_cl, l_cl=l_cl, tag=f"lsweep_{l_cl*1e12:.0f}pH")
    l_max = max([l for l, r in lsw.items() if r["body_pk"] <= idm], default=0.0)
    # alternative: differential-mode inductance for a 5 kA peak (port design), no clamp
    alt = {}
    for ncell in (3, 4):
        lp = port_loop(ncell)
        if lp["L_for_5kA"]:
            alt[ncell] = dump_run(des, ncell, "A", tr.V_OVP, 0, l_main=lp["L_for_5kA"], tag=f"{ncell}c_dmL_{lp['L_for_5kA']*1e6:.1f}uH")
    # alternative: one DD600N16K arm (the DAB's clamp) per bank, or per port on the port board behind the bus to the cells
    l_arm = dv.RECTIFIERS["DD600N16K"]["l_arm_est"]
    mod = {"bank": dump_run(des, 3, "A", tr.V_OVP, 1, l_cl=l_arm, part="DD600N16K", tag="3c_DD600_bank"),
           "port": dump_run(des, 3, "A", tr.V_OVP, 1, l_cl=l_arm, part="DD600N16K", l_stray=L_STRAY_PORT, tag="3c_DD600_port")}
    return {"cases": cases, "sweep": sweep, "n": n_cl, "lsweep": lsw, "l_max": l_max, "alt": alt, "module": mod}


# ------------------------------------------------------------------------------------------------ main
def run(role="primary", write=True, alt=None):
    os.makedirs(OUT, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    des, rec, sel, pick, target = choose(role)
    d, ind, caps = des["d"], des["ind"], des["caps"]
    n = des["npar"]
    env = scan(des)
    env60 = scan(des, loads=(1.0,), t_air=tr.T_AIR_HOT)
    worst, at = stresses(env)
    corners = {f"{va}->{vb}": tr.cell_losses(des, va, vb, tr.p_limit(va, vb)) for va, vb in tr.FL_POINTS}
    peak = max(env, key=lambda e: e["eff"])
    pk2 = tr.peak_efficiency(des)
    if pk2["eff"] > peak["eff"]:
        peak = pk2
    sweep = tr.ratio_sweep(des)
    # thermal: 60 C inlet at full power and with the EVT derating; heatsink temperature at which Tj hits 150 C
    tj60_full = max(e["tj"] for e in env60)
    tj60_der = max(tr.cell_losses(des, va, vb, EVT_DERATE[60.0] * tr.p_limit(va, vb), t_air=60.0)["tj_max"] for va, vb in tr.FL_POINTS)
    hot = max(corners.values(), key=lambda r: r["tj_max"])
    ts_trip = hot["t_sink"] + (TJ_DESIGN - hot["tj_max"])     # sink temperature at which the hottest device reaches 125 C
    # transient: VDMOS fit + commutation at the worst condition (OVP, hardware trip current)
    cal = tr.calibrate(d, des["dev"])
    a_fit, qgd_fit, rg_eff = cal["a"], cal["qgd"], cal["rg_eff"]
    m_on = des["rg_on_mult"]
    dpt_w = pick["transient"]                      # worst commutation at the chosen turn-on resistor (optimise_checked)
    dpt_w["i"] = des["i_ocp"]
    dpt_ds = tr.dpt(d, 1, d["sw_v"], d["check"][0][2], 15e-9, f"{des['dev']}_datasheet_point", rg=rg_eff, a=a_fit,
                    vgs_off=d.get("vgs_off_ds", d["vgs_off"]))
    gd = gdrv_check()
    f_vgs = tr.vgs_factor(d, des["dev"])
    immun = {}
    for clamp in (True, False):
        for voff in (d.get("vgs_off_ds", d["vgs_off"]), d["vgs_off"]):
            immun[(clamp, voff)] = tr.dpt(d, n, tr.V_OVP, des["i_ocp"], tr.L_LOOP["2L"], f"{des['dev']}x{n}_miller", rg=rg_eff, a=a_fit,
                                          rg_on=rg_eff * m_on, vgs_off=voff, clamp=clamp, rks=des["rg_ks"])
    dec = decoupling_study(des)
    mil = miller_study(des, gd)
    vu = tr.voltage_utilisation(des, dpt_w)
    comp = spice_compare(des, a_fit, dpt_w, rg_eff, rg_eff * m_on)
    mod = {nc: module_ripple(des, nc) for nc in (1, 3, 4)}
    brc = bus_reverse_clamp(des)
    lcurve = [(i, tr.inductor_L(ind, i)) for i in np.linspace(0, 80, 33)]
    i_sat50 = next((float(i) for i in np.arange(0, 400.1, 0.5) if tr.inductor_L(ind, i) < 0.5 * ind["L0"]), None)

    # ---------------------------------------------------------------- self-check (physics)
    for e in env:                                   # loss bookkeeping and energy balance
        r = e["r"]
        assert abs(sum(r["loss"].values()) - r["ptot"]) < 1e-6
        assert abs(r["st"]["pA"] - r["st"]["pB"]) < 1e-6 * max(e["p"], 1.0), "ideal power balance"
        assert abs(r["vs_err"]) < 1e-9 * max(r["st"]["ripple"], 1.0), "volt-second balance"
    rf = tr.cell_losses(des, 900, 600, 20e3)
    rr = tr.cell_losses(des, 600, 900, -20e3)
    assert abs(rf["ptot"] / rr["ptot"] - 1) < 1e-6, "bidirectional mirror symmetry"
    for c in comp:                                  # analytic vs ngspice
        assert abs(c["ripple_sim"] / c["ripple_an"] - 1) < 0.15 or c["ripple_an"] < 2.0, ("ripple", c["tag"], c["ripple_sim"], c["ripple_an"])
        for k, v in c["vmax_sim"].items():
            if c["vmax_an"][k] == 0.0:                       # static top switch: conducting
                assert abs(v) < 5.0, ("static switch should conduct", c["tag"], k, v)
            else:
                assert abs(v / c["vmax_an"][k] - 1) < 0.12, ("device voltage", c["tag"], k, v, c["vmax_an"][k])
        assert c["pout"] > 0 and c["pin"] - c["pout"] - c["dE_W"] > 0, "simulated power flows A->B with positive loss (energy balance)"
    assert worst["il_pk"] < des["i_ocp"] / 1.10, "normal peak current must stay below the hardware trip"
    assert tr.mu_frac(ind["_mat"], ind["turns"] * des["i_ocp"] / ind["le"]) >= tr.MU_FRAC_MIN - 1e-9
    for port in "AB":
        assert worst["icap"] <= dv.CAP_IRMS_USE * caps[port]["irms_rating"] + 1e-9, "port cap ripple rating"
        assert caps[port]["vop85"] >= tr.V_OVP, "port cap voltage at 85 C hot spot"
    assert vu["u_dc"] <= dv.V_DC_FRAC and vu["u_pk"] <= dv.V_PK_FRAC, "device voltage rules"
    assert abs(f_vgs / d.get("eoff_vgs_factor", 1.0) - 1) < 0.03, ("stored E_off gate-voltage factor", f_vgs)
    drq = drive_requirements({role: des}, RAILS, des["fsw"])
    if f"{n} x {des['dev']}" == GDRV_PRESET:     # the gate-driver design's own numbers for its preset
        drq["peak_source_A_max"], drq["peak_sink_A_max"] = gd["i_src"], gd["i_snk"]
    assert max(drq["peak_source_A_max"], drq["peak_sink_A_max"]) <= NSI["i_pk"], "peak gate current within the NSI6651 rating"
    assert immun[(True, d["vgs_off"])]["vgs_off_peak"] < d["vth_min"], "Miller-induced gate voltage at the pin below V_th(min) with the clamp"
    assert immun[(False, d["vgs_off"])]["vgs_off_peak"] > immun[(True, d["vgs_off"])]["vgs_off_peak"], "the clamp lowers the gate peak"
    assert abs(tr.T_DEAD_MIN - gd["dt_gates"][0]) < 1e-12 and abs(tr.T_DEAD - gd["dt_gates"][1] - TURN_ON_DELAY) < 1e-12, "dead time = gdrv"
    assert dec["choice"] is not None, "no leg-decoupling option meets the peak-current rule"
    assert mil["worst"]["v_pk"] <= dv.V_PK_FRAC * d["vdss"], "physical-leg deck: device peak within the 0.85 x V_DSS rule"
    # Miller / own-channel results are design VERDICTS (reported in cell_spec gate_drive.miller_check), not self-check physics
    assert mil["worst"]["v_pk"] > tr.V_OVP, "physical-leg deck returned the device peak"
    for (v_, i_), r_ in des["damp"]["runs"].items():
        assert r_["e_damp_on"] >= 0 and r_["e_damp_off"] >= 0 and r_["i_miller_pk"] > 0
    for k, r_ in brc["module"].items():
        assert r_["body_pk"] > d["idm"], ("DD600N16K arm rejected because the body diodes still exceed I_DM", k, r_["body_pk"])
    assert (d["vgs_on"], d["vgs_off"]) == RAILS[des["dev"]], "device data carry the variant's rail pair"
    assert d.get("vgs_min", -99) <= RAILS[des["dev"]][1] < 0 < RAILS[des["dev"]][0] <= d.get("vgs_max", 99), "rails inside the gate limits"
    assert mod[3][0]["irms_ac"] < mod[1][0]["irms_ac"] * 3, "interleaving reduces ripple vs in-phase sum"
    rc = dv.RECTIFIERS[CLAMP_PART]
    for key, r_ in list(brc["cases"].items()) + list(brc["sweep"].items()) + list(brc["lsweep"].items()) + list(brc["alt"].items()):
        assert abs(r_["e_bal"] - 1) < 0.03, ("port-short energy balance", key, r_["e_bal"])
    for (ncell, port, v0, ncl), r_ in brc["cases"].items():
        if ncl == 0:
            assert r_["body_pk"] > d["idm"] and r_["vc_min"] < 0, "without a clamp the short reverses the bank into the body diodes"
        else:
            assert r_["body_pk"] <= d["idm"], ("body diodes within I_DM with the clamp", ncell, port, v0, r_["body_pk"])
            assert r_["clamp_dev_i2t"] <= 0.5 * clamp_allowed_i2t(r_["t95"]), "clamp I2t within half its I2 sqrt(t) rating"
            assert r_["vbp_min"] < 0 and r_["clamp_pk"] > r_["i_cell_pk"], "clamp carries the freewheel"
    assert rc["vrrm"] >= 1.3 * (tr.V_OVP + dv.C4AQ[caps["A"]["part"]]["esl"] / caps["A"]["n"] * dpt_w["didt"]), "clamp V_RRM margin"
    assert brc["l_max"] >= L_DEV_CLAMP / brc["n"], "estimated clamp-group inductance inside the tolerated value"
    for ncell, r_ in brc["alt"].items():
        assert r_["body_pk"] > d["idm"] and r_["q"] > 0.5, "a 5 kA DM inductance alone still reverses the bank"

    spec = cell_spec(des, rec, worst, at, vu, dpt_w, mod, caps, ind, i_sat50, ts_trip, peak, corners, tj60_full, tj60_der, a_fit, gd)
    spec["bus_reverse_clamp"] = clamp_spec(des, brc, dpt_w)
    leg_spec(spec, des, env, dec, mil, gd, immun)
    gate_spec(spec, drq, gd)
    protection_update(spec, drq, gd)
    own = device_block(role, des, spec, corners, peak, worst, dpt_w, mil, brc)
    own["drive"] = drq["per_device"][role]
    if not write:
        return {"spec": spec, "block": own, "dpt_w": dpt_w, "comp": comp}
    # ---------------------------------------------------------------- outputs
    with open(os.path.join(OUT, "envelope.csv"), "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["load", "va", "vb", "p_W", "eff", "loss_W", "tj_max_C", "mode", "cond", "sw", "dead", "core", "cu", "cap", "gate", "misc", "aux",
                     "damp"])
        for e in env:
            wr.writerow([e["load"], e["va"], e["vb"], f"{e['p']:.0f}", f"{e['eff']:.5f}", f"{e['ptot']:.2f}", f"{e['tj']:.1f}", e["mode"]]
                        + [f"{e['r']['loss'][k]:.2f}" for k in ("cond", "sw", "dead", "core", "cu", "cap", "gate", "misc", "aux", "damp")])
    with open(os.path.join(OUT, "inductor_L_vs_I.csv"), "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["I_A", "L_uH"])
        for i, l in lcurve:
            wr.writerow([f"{i:.1f}", f"{l*1e6:.2f}"])
    with open(os.path.join(OUT, "module_ripple.csv"), "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["cells", "va", "vb", "port", "irms_ac_A", "dv_pp_V", "C_total_uF"])
        for nc, rows in mod.items():
            for r in rows:
                wr.writerow([nc, r["va"], r["vb"], r["port"], f"{r['irms_ac']:.2f}", f"{r['dv_pp']:.3f}", f"{r['C_total']*1e6:.0f}"])

    fig, axs = plt.subplots(1, 3, figsize=(15, 4.6))
    for ax, ld in zip(axs[:2], (1.0, 0.5)):
        e = [x for x in env if x["load"] == ld]
        vs = sorted(set(x["va"] for x in e))
        z = np.array([[next(x["eff"] for x in e if x["va"] == va and x["vb"] == vb) for va in vs] for vb in vs]) * 100
        cs = ax.contourf(vs, vs, z, levels=[97.5, 98.5, 98.8, 99.0, 99.1, 99.2, 99.3, 99.4, 99.5, 99.6, 99.7, 99.8], cmap="viridis")
        ax.contour(vs, vs, z, levels=[99.0], colors="r")
        ax.add_patch(plt.Rectangle((550, 550), 400, 400, fill=False, ec="w", ls="--"))
        ax.set_title(f"efficiency at {ld*100:.0f} % of P_lim (A->B; red 99 %)", fontsize=9)
        ax.set_xlabel("V_A [V]")
        ax.set_ylabel("V_B [V]")
        fig.colorbar(cs, ax=ax)
    e = [x for x in env if x["load"] == 1.0]
    vs = sorted(set(x["va"] for x in e))
    z = np.array([[next(x["tj"] for x in e if x["va"] == va and x["vb"] == vb) for va in vs] for vb in vs])
    cs = axs[2].contourf(vs, vs, z, levels=12, cmap="inferno")
    axs[2].set_title(f"hottest Tj at P_lim, {tr.T_AIR:.0f} C inlet [C]", fontsize=9)
    axs[2].set_xlabel("V_A [V]")
    axs[2].set_ylabel("V_B [V]")
    fig.colorbar(cs, ax=axs[2])
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "efficiency_tj_maps.png"), dpi=120)
    plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(12, 4.2))
    for va, vb in ((950, 550), (550, 950), (800, 800), (700, 900), (1000, 600)):
        ps = np.linspace(0.05, 1.0, 20) * tr.p_limit(va, vb)
        axs[0].plot(ps / 1e3, [tr.cell_losses(des, va, vb, p)["eff"] * 100 for p in ps], label=f"{va}->{vb} V")
    axs[0].axhline(99, color="r", lw=0.5)
    axs[0].set_xlabel("P [kW]")
    axs[0].set_ylabel("efficiency [%]")
    axs[0].legend(fontsize=8)
    axs[0].grid(alpha=0.3)
    axs[1].plot([x[0] for x in lcurve], [x[1] * 1e6 for x in lcurve])
    axs[1].axvline(des["i_ocp"], color="r", ls="--", label=f"hardware trip {des['i_ocp']:.0f} A")
    axs[1].axvline(tr.I_MAX, color="k", ls=":", label="45 A")
    axs[1].set_xlabel("inductor current [A]")
    axs[1].set_ylabel("L [uH]")
    axs[1].legend(fontsize=8)
    axs[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "eff_vs_load_and_L_vs_I.png"), dpi=120)
    plt.close(fig)

    fig, axs = plt.subplots(2, 3, figsize=(15, 7))
    for k, c in enumerate(comp):
        s = c["s"]
        T = 1.0 / des["fsw"]
        last = s["t"] >= s["t"][-1] - 2 * T
        tt = (s["t"][last] - s["t"][last][0]) * 1e6
        axs[0, k].plot(tt, s["il"][last], label="ngspice")
        w = s["w_analytic"]
        ta = np.concatenate([w["t"], w["t"] + T]) * 1e6
        ia = np.concatenate([w["i"], w["i"]])
        shift = tt[np.argmax(s["il"][last][: len(tt) // 2])] - (ta[np.argmax(ia[: len(ia) // 2])] - ta[0])
        axs[0, k].plot(ta - ta[0] + shift, ia, "--", label="analytic (peaks aligned)")
        axs[0, k].set_title(f"{c['tag']}: {c['va']:.0f}->{c['vb']:.0f} V, i_L", fontsize=9)
        axs[0, k].legend(fontsize=7)
        axs[0, k].set_xlabel("t [us]")
        for key in ("vds_At", "vds_Ab", "vds_Bt", "vds_Bb"):
            axs[1, k].plot(tt, s[key][last], lw=0.7, label=key)
        axs[1, k].set_xlabel("t [us]")
        axs[1, k].set_ylabel("V_DS [V]")
        axs[1, k].legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "spice_vs_analytic.png"), dpi=110)
    plt.close(fig)

    merge_worse(spec, alt["spec"] if alt else None)
    if alt:
        gate_spec(spec, combine_drive(drq, alt["spec"]["gate_drive"]["requirements"]), gd)
        protection_update(spec, spec["gate_drive"]["requirements"], gd)
    spec["device_" + role] = own
    if alt:
        spec["device_" + ("alternate" if role == "primary" else "primary")] = alt["block"]
    spec["module_basis_role"] = role if not alt or own["eta_corner_min"] <= alt["block"]["eta_corner_min"] else \
        ("alternate" if role == "primary" else "primary")
    spec["cost_usd"] = cost_block(des, 2 * brc["n"])
    spec["cost_usd"]["inductor_row_written_usd"] = round(update_inductor_cost_row(des["ind"]), 0)
    json.dump(spec, open(os.path.join(OUT, "cell_spec.json"), "w"), indent=1, default=float, allow_nan=False)
    write_report(des, rec, sel, pick, target, env, worst, at, corners, peak, sweep, tj60_full, tj60_der, ts_trip, vu, dpt_w, dpt_ds,
                 comp, mod, spec, a_fit, qgd_fit, rg_eff, gd, f_vgs, immun, brc, dec, mil, alt)
    return spec, comp


def clamp_spec(des, brc, dpt_w):
    d, rc, n = des["d"], dv.RECTIFIERS[CLAMP_PART], brc["n"]
    cl = [r for (nc, p, v, k), r in brc["cases"].items() if k]
    no = [r for (nc, p, v, k), r in brc["cases"].items() if not k]
    worst = max(cl, key=lambda r: r["clamp_dev_i2t"])
    esl_bank = dv.C4AQ[des["caps"]["A"]["part"]]["esl"] / des["caps"]["A"]["n"]
    v_rev = tr.V_OVP + esl_bank * dpt_w["didt"]
    t_hot = 85.0
    i_r = rc["irm_25"] * (rc["irm_150"] / rc["irm_25"]) ** ((t_hot - 25) / 125) * 1000.0 / rc["vrrm"]
    return {
        "function": "reverse-voltage clamp across each cell film bank: a bolted port-terminal short rings the bank below zero; "
                    "without it the leg's two series SiC body diodes carry the freewheel",
        "part": CLAMP_PART, "manufacturer": rc["mfr"], "datasheet": rc["src"], "package": rc["package"],
        "devices_per_bank": n, "banks_per_cell": 2, "per_module": {"3_cells": 3 * 2 * n, "4_cells": 4 * 2 * n},
        "connection": "cathode to the bank's bus +, anode to bus -, all devices of a bank in parallel",
        "placement": f"on the cell's laminated DC bus AT the C4AQ bank terminals (port-A bank and port-B bank), devices spread "
                     f"symmetrically between the two capacitors; a clamp on the port board alone does not protect the cell legs",
        "decision": "one clamp group per cell bank (2 per cell), not one per port: the bus from the port board to each cell bank "
                    "(>= 10 nH even laminated) is in series with a port-board clamp, and the current stored in it closes through the "
                    "cell's body diodes",
        "alternatives_rejected": {
            f"DD600N16K_one_arm_per_port_on_port_board": {
                "per_device_body_peak_A": round(brc["module"]["port"]["body_pk"], 0),
                "per_device_body_I2t_A2s": round(brc["module"]["port"]["body_i2t"], 1), "I_DM_A": d["idm"],
                "assumed": f"{L_STRAY_PORT*1e9:.0f} nH per cell port board -> bank, {dv.RECTIFIERS['DD600N16K']['l_arm_est']*1e9:.0f} nH "
                           f"per arm (both LOW estimates), 3 cells, port A, {tr.V_OVP:.0f} V", "deck": brc["module"]["port"]["deck"]},
            f"DD600N16K_one_arm_per_bank": {
                "per_device_body_peak_A": round(brc["module"]["bank"]["body_pk"], 0),
                "per_device_body_I2t_A2s": round(brc["module"]["bank"]["body_i2t"], 1), "I_DM_A": d["idm"],
                "assumed": f"{dv.RECTIFIERS['DD600N16K']['l_arm_est']*1e9:.0f} nH per arm incl. terminals (NOT in the datasheet; low "
                           f"estimate) vs the tolerated {brc['l_max']*1e9:.2f} nH; also 1.4 kg per module",
                "deck": brc["module"]["bank"]["deck"]},
            "why_the_DAB_differs": "the DAB clamps its own DC-link bank; here every cell bank is a separate bank behind its own bus"},
        "branch_inductance_max_nH": round(brc["l_max"] * 1e9, 2),
        "branch_inductance_assumed_nH": round(L_DEV_CLAMP / n * 1e9, 2),
        "branch_inductance_basis": f"bank terminals -> clamp group -> bank; body diodes <= I_DM {d['idm']:.0f} A up to this value with the "
                                   f"leg branch at {L_LEG_BRANCH*1e9:.0f} nH (ESTIMATE); {L_DEV_CLAMP*1e9:.0f} nH per TO-247 incl. leads (ESTIMATE)",
        "ratings": {"V_RRM_V": rc["vrrm"], "V_RSM_V": rc["vrsm"], "I_FSM_10ms_A": rc["ifsm_10ms"], "I2t_10ms_A2s": rc["i2t_10ms"],
                    "I2sqrt_t_A2sqrt_s": rc.get("i2sqrt_t"), "I_FSM_vs_tp": rc.get("ifsm_curve")},
        "reverse_voltage_V": round(v_rev, 0), "reverse_voltage_margin": round(rc["vrrm"] / v_rev, 2),
        "reverse_voltage_basis": f"OVP {tr.V_OVP:.0f} V + bank ESL {esl_bank*1e9:.1f} nH x {dpt_w['didt']/1e9:.1f} A/ns",
        "surge_worst": {"case": f"{worst['ncell']} cells, port {worst['port']}, {worst['v0']:.0f} V",
                        "clamp_group_peak_kA": round(worst["clamp_pk"] / 1e3, 2), "per_device_peak_A": round(worst["clamp_dev_pk"], 0),
                        "per_device_I2t_A2s": round(worst["clamp_dev_i2t"], 0), "t95_ms": round(worst["t95"] * 1e3, 3),
                        "allowed_I2t_at_t95_A2s": round(clamp_allowed_i2t(worst["t95"]), 0),
                        "I2t_margin": round(clamp_allowed_i2t(worst["t95"]) / worst["clamp_dev_i2t"], 1),
                        "sharing": f"per-device values are the worst device of the group: mean x {K_SHARE} (peak), x {K_SHARE**2:.2f} (I2t)"},
        "body_diode_share_with_clamp": {"per_device_peak_A_max": round(max(r["body_pk"] for r in cl), 0), "I_DM_A": d["idm"],
                                        "per_device_I2t_A2s_max": round(max(r["body_i2t"] for r in cl), 3),
                                        "per_device_energy_mJ_max": round(max(r["body_e"] for r in cl) * 1e3, 2)},
        "without_clamp": {"per_device_peak_A_max": round(max(r["body_pk"] for r in no), 0),
                          "per_device_I2t_A2s_max": round(max(r["body_i2t"] for r in no), 0),
                          "per_device_energy_J_max": round(max(r["body_e"] for r in no), 2),
                          "module_peak_kA": [round(r["i_total_pk"] / 1e3, 1) for r in no]},
        "film_bank_internal_reversal_V": round(min(r["vc_min"] for r in cl), 0),
        "leakage_W_per_module_at_1000V_85C": {"3_cells": round(3 * 2 * n * i_r * 1000.0, 2), "4_cells": round(4 * 2 * n * i_r * 1000.0, 2),
                                              "basis": f"I_RM {rc['irm_25']*1e3} mA (25 C) / {rc['irm_150']*1e3} mA (150 C) at V_RRM, "
                                                       f"exponential in T, linear in V (ESTIMATE between the two datasheet points)"},
        "normal_operation": "permanently reverse-biased (bus >= 240 V): no forward conduction, no recovery event, no switching loss; "
                            f"junction capacitance <= {n} x {CJ_CLAMP*1e12:.0f} pF (ESTIMATE) in parallel with the bank, not across a switch",
        "contactor_weld": "not removed: the first current peak through the contactor flows before the bank voltage crosses zero",
        "decks": sorted(set(r["deck"] for r in cl)),
    }


def damper_leg_power(des, r):
    """RC-damper power per leg (W) at one operating point (cell_losses result): the table energy of each edge x f_sw"""
    p = {"A": 0.0, "B": 0.0}
    for leg, k, kind, iout in tr.events(r["w"]):
        hard = (iout > 0) if kind == "rise" else (iout < 0)
        p[leg] += tr.damper_energy(des["damp"], hard, r["va"] if leg == "A" else r["vb"], abs(iout)) * des["fsw"]
    return p


# NOVOSENSE NSI6651ASC (D-035), sim/data/asia_drivers.md (NSI6651 DS p7-9, Q1 DS p9): output ROH 2.2 / ROL 0.3 ohm, 11/12 A
# peak; Miller clamp threshold 1.5/2.0/2.5 V above VEE2, VEE2 + 0.8 V at 1 A; DESAT 8.5/9.26/9.8 V, LEB 200 ns typ, filter
# 150-265 ns, DESAT to OUT 150-300 ns; soft turn-off 100 (Q1) / 250 (industrial) mA min; delay 70-110 ns, PWD <= 30 ns
NSI = {"roh": 2.2, "rol": 0.3, "i_pk": 11.0, "vclmpth_min": 1.5, "v_clamp_1a": 0.8, "t_dclmp": 50e-9, "desat_th": (8.5, 9.8),
       "leb": 200e-9, "filt": 265e-9, "desat_to_out": 300e-9, "isto_min": {"Q1": 0.10, "industrial": 0.25}}
T_SC_ASSUMED = 2.0e-6    # s: NO Chinese datasheet states a short-circuit withstand time (MSC035SMA170B4 3.1 us typ at 1200 V/20 V,
                         # ROHM SCT4036KRHR 4 us): ASSUMED 2.0 us at 1100 V, +18 V - to be confirmed by the maker or a SC test


def drive_requirements(pair, rails, fsw):
    """what the gate driver must deliver for BOTH devices of the pair (worst of the two), NSI6651 parameters; rails per device"""
    out = {}
    for role, des in pair.items():
        d, n = des["d"], des["npar"]
        von, voff = RAILS[des["dev"]]
        dvg = von - voff
        roff, ron, rks = d["rg_ext"], d["rg_ext"] * des["rg_on_mult"], des.get("rg_ks", 0.5)
        r_off_t = NSI["rol"] + (roff + rks + d["rg_int"]) / n
        r_on_t = NSI["roh"] + (ron + rks + d["rg_int"]) / n
        # clamp engagement after turn-off: gate falls from V_on to VEE + V_CLMPTH(min) - V_F(PMEG, 10 mA) through R_off (QG as
        # one capacitance over the datasheet test swing), + comparator delay (gen/gdrv.py method)
        rds175 = float(dv.rds(d, 175))
        v_on = des["i_ocp"] / n * rds175
        t_sto = {g: n * d["ciss"] * (von - d["vth_min"]) / i for g, i in NSI["isto_min"].items()}
        t_resp = {g: NSI["leb"] + NSI["filt"] + NSI["desat_to_out"] + t for g, t in t_sto.items()}
        out[role] = {"mpn": des["dev"], "peak_source_A": dvg / r_on_t, "peak_sink_A": dvg / r_off_t,
                     "gate_power_W_per_channel": n * d["qg"] * dvg * fsw, "clamp_engaged_after_ns": None,     # rev 5: gen/gdrv.py asserts its clamp timing
                     "vds_on_at_trip_175C_V": v_on, "desat_margin": NSI["desat_th"][0] / v_on,
                     "sc_response_us": {g: t * 1e6 for g, t in t_resp.items()}}
    worst = lambda k: max(v[k] for v in out.values())
    return {"rails_V": {r: list(RAILS[x["dev"]]) for r, x in pair.items()}, "per_device": out,
            "peak_source_A_max": worst("peak_source_A"), "peak_sink_A_max": worst("peak_sink_A"),
            "gate_power_W_per_channel_max": worst("gate_power_W_per_channel"),
            "dead_time_min_required_ns": None,
            "sc_assumed_withstand_us": T_SC_ASSUMED * 1e6,
            "sc_response_us_worst": {g: max(v["sc_response_us"][g] for v in out.values()) for g in NSI["isto_min"]},
            "desat_margin_min": min(v["desat_margin"] for v in out.values())}


def leg_spec(spec, des, env, dec, mil, gd, immun):
    """adds the PVR-01/02/03 results to cell_spec (new keys; existing keys keep their meaning)"""
    d, n, fsw = des["d"], des["npar"], des["fsw"]
    tab, wr = des["damp"], mil["worst"]
    pl = [(max(damper_leg_power(des, e["r"]).values()), e) for e in env]
    p_max, e_max = max(pl, key=lambda x: x[0])
    e_trip = tab["runs"][(tr.V_OVP, des["i_ocp"])]
    i_pk = max(r["i_damp_pk"] for r in tab["runs"].values())
    v_el = i_pk * DAMPER["r_el"]
    part, nd = dec["choice"]
    row = next(r for r in dec["rows"] if (r["part"], r["n"]) == dec["choice"])
    drawn = next(r for r in dec["rows"] if (r["part"], r["n"]) == ("C4AQUBU4100A1WJ", 2))
    p = dv.C4AQ_DEC[part]
    spec["decoupling"]["suggested"] = (f"{nd} x {part} ({p['C']*1e6:.1f} uF / {p['vndc']} V, Ipkr {p['ipkr']:.0f} A, ESL {p['esl']*1e9:.0f} nH, "
                                       f"C4AQ p.14) per leg")
    spec["decoupling"].update({
        "mpn": part, "count_per_leg": nd, "C_per_leg_uF": nd * p["C"] * 1e6, "ESL_per_leg_nH": round(p["esl"] / nd * 1e9, 2),
        "peak_current_per_cap_A": round(row["pk_cap"], 1),
        "peak_current_per_cap_with_recovery_A": None if row["pk_cap_rr"] is None else round(row["pk_cap_rr"], 1),
        "Ipkr_per_cap_A": p["ipkr"], "peak_margin": round(p["ipkr"] / row["pk_cap"], 2), "rms_per_cap_A": round(row["rms_cap"], 2),
        "Irms_70C_per_cap_A": p["irms"], "V_op_85C_V": p["vop85"],
        "basis": f"physical-leg deck (sim/spice/pv_dpt_leg_dec.cir): {tr.V_OVP:.0f} V, {des['i_ocp']:.1f} A trip current, worst of "
                 f"on-times {', '.join(f'{t*1e6:.1f}' for t in DEC_TON)} us (decoupling-bulk resonance); rule: peak <= Ipkr/{DEC_MARGIN} "
                 f"(VDMOS, Q_oss) and <= Ipkr with the Q_rr surrogate, ESL <= {dec['esl_max_nH']:.1f} nH so the {tr.L_LOOP['2L']*1e9:.0f} nH "
                 f"loop holds. Drawn 2 x C4AQUBU4100A1WJ: {drawn['pk_cap']:.0f} A per capacitor vs Ipkr 28 A (PVR-03)",
        "rejected": [f"{r['n']} x {r['part']}: {r['pk_cap']:.0f} A per cap vs Ipkr {r['ipkr']:.0f} A" for r in dec["rows"]
                     if not r["ok"] and r["pk_cap_rr"] is None]})
    spec["damper"] = {
        "function": "RC damper across each leg at the device pins (drawn on PVCELL 03_power: R301-R304, C303-C308); damps the "
                    "commutation-loop ringing",
        "per_leg": f"{DAMPER['n_r']} x {DAMPER['r_el']} ohm in series + {DAMPER['n_c']} x {DAMPER['c_el']*1e9:.1f} nF "
                   f"{DAMPER['c_el_V']:.0f} V C0G in series",
        "R_per_leg_ohm": DAMPER["n_r"] * DAMPER["r_el"], "C_per_leg_nF": DAMPER["c_el"] / DAMPER["n_c"] * 1e9,
        "energy_per_cycle_uJ_at_trip": round((e_trip["e_damp_on"] + e_trip["e_damp_off"]) * 1e6, 0),
        "energy_basis": f"{tr.V_OVP:.0f} V, {des['i_ocp']:.1f} A: hard turn-on {e_trip['e_damp_on']*1e6:.0f} uJ + turn-off "
                        f"{e_trip['e_damp_off']*1e6:.0f} uJ (the loop's Q_oss/commutation energy, nearly independent of the damper C)",
        "power_per_leg_W_max": round(p_max, 2),
        "power_per_leg_W_max_at": {"va": e_max["va"], "vb": e_max["vb"], "p_W": round(e_max["p"], 0), "load": e_max["load"]},
        "power_per_leg_W_at_trip_1100V": round((e_trip["e_damp_on"] + e_trip["e_damp_off"]) * fsw, 2),
        "peak_current_A": round(i_pk, 1), "peak_voltage_per_resistor_V": round(v_el, 0),
        "capacitor_voltage_per_element_V": round((tr.V_OVP + e_trip["dv_os_on"]) / DAMPER["n_c"], 0),
        "required_resistor_rating": {"continuous_W_per_leg_min": round(1.5 * p_max, 1),
                                     "continuous_basis": "1.5 x worst per-leg power at the resistor's mounting temperature "
                                                         "(heatsink-mounted thick-film power resistors; 2 x 1 W 2512 is not enough)",
                                     "pulse_or_working_voltage_per_element_V_min": round(1.25 * v_el, -1),
                                     "energy_per_pulse_per_element_uJ": round(e_trip["e_damp_on"] / DAMPER["n_r"] * 1e6, 0)},
        "in_loss_model": "yes: loss group 'damp' (ngspice energy table per edge, V 250-1100 V x I 2 A-trip, interpolated)",
        "deck": "sim/spice/pv_dpt_leg.cir",
    }
    sw = spec["switching"]
    sw.update({"dead_time_firmware_ns": round(tr.T_DEAD_FW * 1e9), "dead_time_at_gates_ns": [round(tr.T_DEAD_MIN * 1e9), round(tr.T_DEAD * 1e9)],
               "dead_time_basis": f"REQUIRED for the device pair: the Miller clamp must hold before the earliest complementary turn-on "
                                  f"(rev-5 AO3400A clamps, gen/gdrv.py) -> {tr.T_DEAD_MIN*1e9:.0f}-"
                                  f"{tr.T_DEAD*1e9:.0f} ns at the gates with the rev-4 spread ({gd['src']}: stretch {gd['dt_stretch']} gives "
                                  f"{gd['dt_gates'][0]*1e9:.0f}-{gd['dt_gates'][1]*1e9:.0f} ns for MSC035; the gate-driver design must be "
                                  f"re-sized). Firmware dead time {tr.T_DEAD_FW*1e9:.0f} ns. Loss uses the maximum, ZVS completion the minimum",
               "min_pulse_basis": f"{tr.T_ON_MIN*1e9:.0f} ns complementary on-time + 2 x {tr.T_DEAD*1e9:.0f} ns dead time at the gates",
               "body_diode_conduction": {"per_edge_ns_max": round(tr.T_DEAD * 1e9), "edges_per_period_per_switching_leg": 2,
                                         "duty_max_pct": round(2 * tr.T_DEAD * fsw * 100, 2),
                                         "current_per_device_max_A": round(des["i_ocp"] / n, 1),
                                         "note": "hard edge: the full dead time; soft edge: the dead time less the transition"}})
    gdr = spec["gate_drive"]
    w = mil["worst"]
    die = w["vgs_off_peak_die"]
    gdr.update({"kelvin_source_resistor_ohm": gd["r_ks"],
                "miller_clamp": f"rev 5 ({gd['src']}): {gd['r_pu']}; NSI6651 CLAMP drives them; release <= {TURN_ON_DELAY*1e9:.0f} ns "
                                "after OUTH rises - required",
                "miller_check": {
                    "source": w["source"], "worst_corner": {"V": tr.V_OVP, "I_A": round(des["i_ocp"], 1)},
                    "V_GS_pin_peak_V": w["vgs_off_peak"], "V_GS_die_peak_V": die, "V_GS_die_ideal_clamp_loop_V": w["die_ideal_loop"],
                    "miller_current_per_device_A_peak": w["i_miller_pk"],
                    "victim_dvdt_V_per_ns_peak": None if w["dvdt"] is None else round(w["dvdt"] / 1e9, 0),
                    "edge_dvdt_V_per_ns": round(w["dvdt_on"] / 1e9, 0), "clamp_loop_nH_max": 1.0,
                    "limits_V": {"design_target": round(d["vth_175_min"] - 0.5, 2), "V_th_min_175C": d["vth_175_min"], "V_th_min_25C": d["vth_min"]},
                    "result": ("not simulated for this variant" if die is None else
                               f"die {die:+.2f} V vs V_th(min, 175 C) {d['vth_175_min']} V: " +
                               ("within the 0.5 V design margin" if die <= d["vth_175_min"] - 0.5 else
                                "below V_th(min) but inside the 0.5 V margin" if die < d["vth_175_min"] else "FAIL"))}})
    spec["spice"]["leg_deck"] = "sim/spice/pv_dpt_leg.cir"
    spec["efficiency"]["note"] = (f"power stage incl. gate drive, driver bias, sensing and the leg RC dampers; dead time at its "
                                  f"{tr.T_DEAD*1e9:.0f} ns maximum; excludes fans/controller")


def cell_spec(des, rec, worst, at, vu, dpt_w, mod, caps, ind, i_sat50, ts_trip, peak, corners, tj60_full, tj60_der, a_fit, gd):
    d, n = des["d"], des["npar"]
    i_fs = int(math.ceil(1.3 * des["i_ocp"] / 10.0) * 10)
    mpn = des["dev"]
    vpk = max(vu["v_pk"], tr.V_OVP + dpt_w["dv_os"])
    rdc110 = ind["rdc20"] * (1 + dv.CU_ALPHA * 90)
    sw = lambda name, leg, pos: {"name": name, "leg": leg, "position": pos, "mpn": mpn, "manufacturer": d["mfr"],
                                 "parallel": n, "package": d["package"], "datasheet": d["src"],
                                 "gate_resistor_on_ohm": round(d["rg_ext"] * des["rg_on_mult"], 2), "gate_resistor_off_ohm": d["rg_ext"],
                                 "gate_resistor_note": "per device, split outputs (UCC21710 OUTH/OUTL); R_off = datasheet switching-energy "
                                                       "test value; R_on raised to keep the complementary device's turn-on ringing within the "
                                                       "0.85 x V_DSS rule (E_on penalty from the datasheet E-vs-R_G curve is in the losses)"}
    mods = {}
    for nc in (3, 4):
        rows = mod[nc]
        mods[f"{nc}_cells"] = {
            "interleave_deg": 360 / nc, "power_rated_kW": nc * tr.P_RATED / 1e3, "power_max_kW": nc * tr.P_MAX / 1e3,
            "port_current_max_A": nc * tr.I_MAX,
            "port_A_capacitance_total_uF": nc * caps["A"]["C"] * 1e6, "port_B_capacitance_total_uF": nc * caps["B"]["C"] * 1e6,
            "port_cap_count_total": nc * (caps["A"]["n"] + caps["B"]["n"]),
            "port_ripple_current_rms_worst_A": max(r["irms_ac"] for r in rows),
            "port_ripple_voltage_pp_worst_V": max(r["dv_pp"] for r in rows),
            "worst_points": [{"va": r["va"], "vb": r["vb"], "port": r["port"], "irms_ac_A": round(r["irms_ac"], 2), "dv_pp_V": round(r["dv_pp"], 3)} for r in rows],
            "ripple_frequency_kHz": nc * des["fsw"] / 1e3}
    return {
        "title": "PVCELL-25/27.5 power-stage hand-off (calculated, not bench-validated)",
        "generated_by": "sim/pv_design.py", "gate0_study": "sim/out/pv_tradeoff/report.md",
        "topology": "2-level non-inverting four-switch buck-boost (FSBB), common negative rail, bidirectional",
        "gate0_candidate": rec,
        "ratings": {"P_rated_kW": tr.P_RATED / 1e3, "P_max_kW": tr.P_MAX / 1e3, "I_max_A_low_voltage_port": tr.I_MAX,
                    "V_ports_V": [tr.V_MIN, tr.V_MAX], "V_full_power_V": list(tr.V_FL), "P_limit_rule": "min(27.5 kW, 45 A x min(V_A, V_B))"},
        "switch_positions": [sw("S1", "A", "top (port A +)"), sw("S2", "A", "bottom"), sw("S3", "B", "top (port B +)"), sw("S4", "B", "bottom")],
        "switching": {"f_sw_kHz": des["fsw"] / 1e3, "pwm": "centre-aligned, legs A and B on the same carrier",
                      "dead_time_ns": round(tr.T_DEAD_FW * 1e9), "min_pulse_us": round(tr.T_MIN_PULSE * 1e6, 3),
                      "D_max": round(1 - tr.T_MIN_PULSE * des["fsw"], 5),
                      "modes": {"buck": "V_B/V_A <= D_max: leg A switches, S3 on, S4 off",
                                "boost": "V_B/V_A >= 1/D_max: leg B switches, S1 on, S2 off",
                                "band": "otherwise: both legs switch, D_A = D_max*min(1,V_B/V_A), D_B = D_max*min(1,V_A/V_B)"}},
        "gate_drive": {"driver": "TI UCC21710 (D-005), one isolated channel per switch position (4 per cell)",
                       "V_GS_on_V": d["vgs_on"], "V_GS_off_V": d["vgs_off"], "Q_g_per_device_nC": d["qg"] * 1e9,
                       "gate_power_per_channel_W": n * d["qg"] * (d["vgs_on"] - d["vgs_off"]) * des["fsw"],
                       "peak_gate_current_A": max(gd["i_src"], gd["i_snk"]),
                       "peak_gate_current_source_A": gd["i_src"], "peak_gate_current_sink_A": gd["i_snk"], "driver_rating_A": 10.0,
                       "V_GS_on_range_V": list(gd["von"]), "V_GS_off_range_V": list(gd["voff"]),
                       "gate_drive_basis": f"{gd['src']} (UCC14241-Q1 cannot regulate +20/-5 V; peak currents incl. ROH_EFF/ROL)",
                       "miller_clamp": "UCC21710 CLMPI to each gate through PMEG4010CEJ - required (see report section 4)"},
        "inductor": {"L_at_45A_uH": tr.inductor_L(ind, tr.I_MAX) * 1e6, "L0_uH": ind["L0"] * 1e6,
                     "L_tolerance_pct": 8.0, "tolerance_basis": "catalogue A_L +-8 %; DC-bias curve fit spread not included",
                     "L_at_trip_uH": ind["L_ocp"] * 1e6, "I_at_50pct_L0_A": i_sat50, "I_trip_A": round(des["i_ocp"], 1),
                     "I_sat_definition": "current at which L falls to 50 % of L0 (powder core, soft saturation)",
                     "I_rms_max_A": worst["il_rms"], "I_peak_normal_max_A": worst["il_pk"], "ripple_pp_max_A": worst["ripple"],
                     "core": (ind["core"] if ind.get("design") else f"Magnetics {ind['part']} (E-core set), {ind['n_stack']} sets stacked"),
                     "material": ind["material"], "turns": ind["turns"],
                     "conductor": ind.get("conductor") or f"Litz, {ind['a_cu']*1e6:.1f} mm2 copper, insulation for 1.1 kV",
                     "DCR_20C_mOhm": ind["rdc20"] * 1e3, "DCR_110C_mOhm": rdc110 * 1e3, "B_dc_at_45A_T": ind["B_full"],
                     "B_at_trip_T": ind["B_ocp"], "B_sat_T": ind["_mat"]["bsat"], "mass_kg": ind["mass"],
                     "loss_worst_W": worst["p_ind"], "loss_worst_at": at["p_ind"], "core_area_mm2": ind["ae"] * 1e6, "path_mm": ind["le"] * 1e3,
                     "review_corrections": {"source": tr.IND_CORR["source"], "loss_factor_applied": round(tr.IND_CORR["loss"], 3),
                                            "hot_spot_rise_K_at_W": [tr.IND_CORR["hot_rise_K"], tr.IND_CORR["at_W"]],
                                            "MG-06_conductor": "profiled (rectangular) litz or foil (round 10.3 mm2 litz with 1.1 kV insulation "
                                                               "does not fit the 8044E window)",
                                            "MG-07_insulation_class": "H (180 C); hot-spot NTC at the winding centre drives the fan law / derating",
                                            "asian_core": "POCO NPC 26: better DC bias, ~45 % more core loss, no 80 mm E core (toroids only)"},
                     "requirement": {
                         "note": "ELECTRICAL requirement set by the power stage; the construction (conductor, core, insulation, thermal) is the "
                                 "magnetics engineer's (sim/out/magnetics/design_pv_inductor.json, read by sim/pv_tradeoff.py when present)",
                         "L_vs_I": "out/pv_design/inductor_L_vs_I.csv", "L_at_45A_uH_min": round(tr.inductor_L(ind, tr.I_MAX) * 1e6 * 0.92, 1),
                         "L_at_trip_fraction_of_L0_min": tr.MU_FRAC_MIN, "L_tolerance_pct": 8.0,
                         "I_dc_max_A": tr.I_MAX, "I_rms_max_A": round(worst["il_rms"], 1), "I_peak_normal_max_A": round(worst["il_pk"], 1),
                         "I_trip_A": round(des["i_ocp"], 1), "ripple_pp_max_A": round(worst["ripple"], 1),
                         "volt_seconds_max_Vus": round(tr.inductor_L(ind, tr.I_MAX) * worst["ripple"] * 1e6, 0),
                         "ripple_frequency_kHz": des["fsw"] / 1e3, "voltage_across_max_V": tr.V_MAX,
                         "loss_allowed_W": round(worst["p_ind"], 1),
                         "hot_spot_max_C": 155.0, "hot_spot_basis": "class H (180 C) less 25 K; module derating keeps it (sim/pv_module.py T_IND_MAX)",
                         "air_flow_m3h_at_full_load": des["flow"], "air_flow_basis": "per-cell heatsink design flow (pv_tradeoff); the module "
                                                                                     "model gives the real flow per inlet temperature"},
                     "insulation_requirements_IC16": {"winding_to_core_basic": "PD-free at 1910 Vpk (<= 10 pC), AC 2200 V rms 1 min, impulse 6 kV",
                                                      "NTC_to_winding_reinforced": "PD <= 10 pC at 2387 Vpk, AC 4400 V rms 1 min, impulse 8 kV",
                                                      "source": "gen/data/review_insulation.csv IC-16, sim/out/insulation/report.md"},
                     "asian_core_alternative": {"maker": "POCO (CN)", "material": "NPC 26 (catalogue mu 26, Bsat 1.25 T) or NPH-L 26 (1.0 T)",
                                                "replaces": "Magnetics Kool Mu MAX 26u 00Y8044E026",
                                                "data": "sim/data/asian_magnetic_materials.csv (POCO 2026 catalogue p27-30: DC-bias fit, loss fit "
                                                        "printed for 60/75u only)", "open": "an 80/44 E-core shape in NPC 26 is not on file - "
                                                        "confirm the shape or use a POCO toroid (NPC290026 class) with a new winding design"},
                     "cost_usd": ({"total": tr.inductor_cost(ind), "basis": ind["design"]["cost"]["basis"]} if ind.get("design") else
                                  {"Magnetics_core": round(tr.inductor_cost(ind, "Magnetics"), 1), "Asian_core": round(tr.inductor_cost(ind, "Asian"), 1),
                                   "basis": "gen/data/cost_estimates.csv L_CELL basis; core 35 / 15 USD/kg (ESTIMATE, no quote)"}),
                     "construction_source": tr.IND_CORR["source"],
                     "construction": ind["design"]["construction"] if ind.get("design") else f"{ind['part']} x{ind['n_stack']}"},
        "capacitors": {f"port_{p}": {"series": "KEMET C4AQ DC-link PP film", "mpn": caps[p]["part"], "count": caps[p]["n"],
                                       "C_total_uF": caps[p]["C"] * 1e6, "V_rated_70C_V": caps[p]["v_rating"], "V_op_85C_V": caps[p]["vop85"],
                                       "ripple_rating_total_Arms": caps[p]["irms_rating"], "ripple_use_limit_Arms": dv.CAP_IRMS_USE * caps[p]["irms_rating"],
                                       "ripple_worst_Arms": worst["icap"], "ESR_total_mOhm": caps[p]["esr"] / caps[p]["n"] * 1e3,
                                       "datasheet": dv.CAP + "C4AQ.pdf"} for p in "AB"},
        "decoupling": {"requirement": "low-ESL film within each leg's commutation loop so that the loop (cap-S_top-S_bottom) is <= "
                                      f"{tr.L_LOOP['2L']*1e9:.0f} nH", "suggested": "2 x C4AQUBU4100A1WJ (1 uF / 1300 V, C4AQ p.14) per leg"},
        "sensing": {"inductor_current": {"range_A": [-i_fs, i_fs], "bandwidth_kHz_min": 200, "use": "current loop + hardware trip",
                                         "basis": "full scale >= 1.3 x hardware trip, rounded up to 10 A"},
                    "port_A_current": {"range_A": [-60, 60], "bandwidth_kHz_min": 10, "use": "MPPT / power"},
                    "port_B_current": {"range_A": [-60, 60], "bandwidth_kHz_min": 10, "use": "power / BMS limit"},
                    "port_A_voltage": {"range_V": [0, 1200], "use": "MPPT, OVP, mode selection"},
                    "port_B_voltage": {"range_V": [0, 1200], "use": "bus/battery regulation, OVP"},
                    "heatsink_temperature": {"range_C": [-40, 150]}, "inductor_temperature": {"range_C": [-40, 180]}},
        "protection": {"inductor_overcurrent_hw_trip_A": round(des["i_ocp"], 1), "current_limit_avg_A": tr.I_MAX,
                       "overvoltage_hw_trip_port_A_V": tr.V_OVP, "overvoltage_hw_trip_port_B_V": tr.V_OVP,
                       "overvoltage_sw_limit_V": 1050.0, "undervoltage_stop_V": 240.0,
                       "desat_VDS_threshold_V": gd["trip_nom"], "desat_blanking_ns": gd["blank"][1],
                       "desat_threshold_range_V": list(gd["trip"]), "desat_blanking_range_ns": list(gd["blank"]),
                       "short_circuit_detect_to_off_us": gd["t_sc"], "short_circuit_withstand_us_typ": gd["scwt"],
                       "desat_basis": f"V_DS(on) at trip = {des['i_ocp']/n:.1f} A x R_DS(on,175C) {float(dv.rds(d, 175))*1e3:.0f} mOhm = "
                                      f"{des['i_ocp']/n*float(dv.rds(d, 175)):.1f} V per device vs trip {gd['trip'][0]}-{gd['trip'][1]} V; "
                                      f"detect + soft turn-off {gd['t_sc']} us vs {gd['scwt']} us typical withstand (MSC035SMA170B4 p.3); "
                                      f"values from {gd['src']}",
                       "heatsink_overtemperature_trip_C": round(ts_trip - 5, 0),
                       "heatsink_trip_basis": f"heatsink temperature at which the hottest device reaches {TJ_DESIGN:.0f} C at full load, minus 5 K"},
        "worst_case_stresses": {"device_VDS_peak_V": vpk, "device_VDS_rating_V": d["vdss"], "device_VDS_peak_frac": vpk / d["vdss"],
                                "device_VDS_continuous_max_V": tr.V_MAX, "device_Irms_per_position_A": worst["irms_pos"],
                                "device_Irms_per_device_A": worst["irms_pos"] / n, "device_I_turnoff_max_per_device_A": des["i_ocp"] / n,
                                "device_loss_max_per_position_W": worst["ploss_dev"], "Tj_max_45C_C": worst["tj"],
                                "Tj_max_60C_full_power_C": tj60_full, "Tj_max_60C_EVT_derated_C": tj60_der,
                                "turnoff_overshoot_V": dpt_w["dv_os"], "turnoff_didt_A_per_ns": dpt_w["didt"] / 1e9,
                                "inductor_I_peak_A": worst["il_pk"], "inductor_I_rms_A": worst["il_rms"],
                                "port_cap_I_rms_A": worst["icap"], "port_cap_V_max_V": tr.V_OVP},
        "heatsink_insulator": {
            "function": "basic insulation of every TO-247 tab (drain = A+/B+ or a switch node) from the earthed heatsink (D-032, IC-07)",
            "material": tr.PAD["material"], "thickness_mm": tr.PAD["t_m"] * 1e3,
            "size_mm_min": "tab 15.9 x 20.9 mm + 2 x overhang", "overhang_mm_min": 6.5,
            "overhang_basis": "creepage 5.0 mm (PD2) and clearance 7.1 mm at 4000 m (insulation report row 'Basic HV-PE, switch node'): "
                              "6.5 mm surface + 1.0 mm thickness >= 7.1 mm",
            "Rth_cs_K_W": tr.rth_cs(d),
            "Rth_basis": f"AlN {tr.PAD['k_W_mK']:.0f} W/mK x {tr.PAD['t_m']*1e3:.1f} mm, two grease bond lines {tr.PAD['grease_t']*1e6:.0f} um at "
                         f"{tr.PAD['grease_k']} W/mK over 15.5 x 20 mm, + {tr.PAD['contact']} K/W contact allowance; k values are handbook "
                         "values (no pad datasheet on file) - confirm with the supplier's data and a thermal test",
            "mounting": "spring clip on the plastic body (no screw through the tab hole: a PE screw would need its own insulating "
                        "bushing and creepage), clip force per the device maker (TO-247: ~50-80 N, ASSUMED)",
            "tests": "type test on the assembled heatsink: AC 2200 V rms 1 min, impulse 6 kV, PD extinction >= 1528 Vpk with <= 10 pC at "
                     "1910 Vpk (insulation report); at the claimed altitude's pressure for the PD test",
            "requirement_source": "gen/data/review_insulation.csv IC-07; sim/out/insulation/report.md"},
        "switch_node_recurring_peak": {
            "at_1000V_V": round(tr.V_MAX * (1 + max(dpt_w["dv_os_on"], 0.0) / dpt_w["vdc"]), 0),
            "at_1100V_trip_V": round(tr.V_OVP + dpt_w["dv_os"], 0),
            "basis": f"calibrated VDMOS commutation at {dpt_w['vdc']:.0f} V / {dpt_w['i']:.1f} A, R_G,on {dpt_w['rg_on']:.2f} ohm (eff.), "
                     "turn-on ringing scaled with the bus voltage",
            "vs_bias_VIORM_1414Vpk": "UCC14241-Q1 VIORM 1414 Vpk (IC-06): the bias barrier must cover the recurring peak; margin at 1000 V = "
                                     f"{1414 / (tr.V_MAX * (1 + max(dpt_w['dv_os_on'], 0.0) / dpt_w['vdc'])):.2f}"},
        "efficiency": {"peak": peak["eff"], "peak_at": {"va": peak["va"], "vb": peak["vb"], "p_W": peak["p"]},
                       "corners": {k: v["eff"] for k, v in corners.items()},
                       "note": "power stage incl. gate drive, driver bias and sensing; excludes fans/controller"},
        "control_plant": {"L_uH_vs_I": "out/pv_design/inductor_L_vs_I.csv", "C_A_uF": caps["A"]["C"] * 1e6, "C_B_uF": caps["B"]["C"] * 1e6,
                          "R_series_mOhm": (2 * float(dv.rds(d, 100)) / n + rdc110 + tr.R_MISC) * 1e3,
                          "sampling": "sample i_L at the carrier peak/valley (centre-aligned PWM) for the average",
                          "pwm_frequency_kHz": des["fsw"] / 1e3},
        "module": mods,
        "spice": {"fsbb_decks": [f"sim/spice/pv_fsbb_{t}.cir" for t, _, _ in SPICE_POINTS],
                  "dpt_deck": f"sim/spice/{os.path.basename(dpt_w['deck'])}", "vdmos_Cgd_shape_a": a_fit},
        "honesty": "All values calculated from datasheets and assumptions listed in sim/out/pv_design/report.md; nothing measured.",
    }


def leg_report(a, des, gd, immun, dec, mil, spec):
    d = des["d"]
    mc, dp, dc = spec["gate_drive"]["miller_check"], spec["damper"], spec["decoupling"]
    tab, wr = des["damp"], mil["worst"]
    a(f"- **Physical leg deck** (`sim/spice/pv_dpt_leg*.cir`, PVR-01/02/03): port bank {des['caps']['A']['n']} x {des['caps']['A']['part']} "
      f"-> {L_BUS_BULK*1e9:.0f} nH bus (layout requirement of gen/pvcell.py) -> leg decoupling -> {L_REST*1e9:.1f} nH (board + devices) -> "
      f"device pins with the drawn RC damper ({dp['per_leg']}); Kelvin-source {des['rg_ks']} ohm in every gate loop; clamp engaged. "
      f"At {tr.V_OVP:.0f} V / {des['i_ocp']:.1f} A: device peak {wr['v_pk']:.0f} V (lumped deck {tr.V_OVP + spec['worst_case_stresses']['turnoff_overshoot_V']:.0f} V), "
      f"turn-on dv/dt {wr['dvdt_on']/1e9:.0f} V/ns.")
    a(f"- **Miller check (rev-5 gate network, {mc['source']})**: pin {mc['V_GS_pin_peak_V']} V, die {mc['V_GS_die_peak_V']} V "
      f"(ideal clamp loop {mc['V_GS_die_ideal_clamp_loop_V']} V) at {mc['miller_current_per_device_A_peak']} A per device; limits "
      f"{mc['limits_V']}; {mc['result']}. Without any clamp the lumped deck's pin reaches "
      f"{immun[(False, d['vgs_off'])]['vgs_off_peak']:+.2f} V - the clamp stays mandatory.")
    a(f"- **RC damper loss (PVR-02)**: energy per edge from the leg deck, in the loss model as group 'damp'. Per cycle at the trip corner "
      f"{dp['energy_per_cycle_uJ_at_trip']:.0f} uJ ({dp['energy_basis']}) = {dp['power_per_leg_W_at_trip_1100V']} W per leg; worst in normal "
      f"operation {dp['power_per_leg_W_max']} W per leg at {dp['power_per_leg_W_max_at']['va']:.0f}->{dp['power_per_leg_W_max_at']['vb']:.0f} V. "
      f"Peak damper current {dp['peak_current_A']} A = {dp['peak_voltage_per_resistor_V']:.0f} V per {DAMPER['r_el']} ohm element. "
      f"Required: >= {dp['required_resistor_rating']['continuous_W_per_leg_min']} W per leg continuous at the mounting temperature, "
      f">= {dp['required_resistor_rating']['pulse_or_working_voltage_per_element_V_min']:.0f} V per element (the drawn 2 x 1 W 2512, 200 V, "
      f"is not enough).")
    a("\n| E_damp per edge [uJ]: hard turn-on / turn-off | " + " | ".join(f"{i:.0f} A" for i in tab["i"]) + " |")
    a("|---|" + "---|" * len(tab["i"]))
    for v, ro, rf in zip(tab["v"], tab["e_on"], tab["e_off"]):
        a(f"| {v:.0f} V | " + " | ".join(f"{x*1e6:.0f} / {y*1e6:.1f}" for x, y in zip(ro, rf)) + " |")
    a(f"\n- **Leg decoupling (PVR-03)**: {dc['suggested']}: {dc['peak_current_per_cap_A']} A per capacitor (VDMOS, Q_oss), "
      f"{dc['peak_current_per_cap_with_recovery_A']} A with the Q_rr surrogate, vs Ipkr {dc['Ipkr_per_cap_A']:.0f} A; "
      f"{dc['rms_per_cap_A']} A rms per capacitor vs {dc['Irms_70C_per_cap_A']} A; ESL {dc['ESL_per_leg_nH']} nH keeps the loop. "
      f"Rejected: {'; '.join(dc['rejected'])}. {dc['basis']}.\n")
    a("| decoupling per leg | C [uF] | ESL [nH] | peak per cap [A] (Ipkr) | with Q_rr surrogate [A] | rms per cap [A] | device peak [V] |")
    a("|---|---|---|---|---|---|---|")
    for r in dec["rows"]:
        a(f"| {r['n']} x {r['part']} | {r['C_uF']:.1f} | {r['esl_nH']:.1f} | {r['pk_cap']:.0f} ({r['ipkr']:.0f}) | "
          f"{'-' if r['pk_cap_rr'] is None else '%.0f' % r['pk_cap_rr']} | {r['rms_cap']:.2f} | {r['v_pk']:.0f} |")
    a("")


def write_report(des, rec, sel, pick, target, env, worst, at, corners, peak, sweep, tj60_full, tj60_der, ts_trip, vu, dpt_w, dpt_ds,
                 comp, mod, spec, a_fit, qgd_fit, rg_eff, gd, f_vgs, immun, brc, dec, mil, alt=None):
    d, ind, caps, n = des["d"], des["ind"], des["caps"], des["npar"]
    L = []
    a = L.append
    a("# PVCELL-25/27.5 power-stage design\n")
    a("Generated by `sim/pv_design.py`; every number is written by the script. **Calculated, not measured.** "
      "Hand-off file: `cell_spec.json`.\n")
    b2, b3 = sel["best_2L"], sel["best_3L"]
    a(f"Topology: **{tr.CANDIDATES[rec]['label'].strip()}** - kept by Gate-0b with Asian devices and USD cost "
      f"(`sim/out/pv_tradeoff/gate0b.md`, D-031): best 2-level {b2['key']} {b2['cost_usd']:.0f} USD/cell vs best 3-level "
      + (f"{b3['key']} {b3['cost_usd']:.0f} USD/cell" if b3 else "none meeting the rules") + f"; rule: 2-level if <= {tr.DECISION_MARGIN} x.\n")
    a("## 1. Design point\n")
    pr, al = DEVICES["primary"], DEVICES["alternate"]
    a(f"**{des['name']}** (this report: the {des['role']} device) - the D-007 point (2-level, {FROZEN['fsw']/1e3:.0f} kHz, ripple "
      f"{FROZEN['ripple']}, today's inductor). Devices: **primary {pr[1]} x {pr[0]}**, **alternate {al[1]} x {al[0]}**; cell_spec.json "
      f"carries the worse of the two in every check (key 'worst_of') and both in device_primary / device_alternate. Worst corner of "
      f"this device {pick['eta_corner_min']*100:.2f} % (target {ETA_DESIGN*100:.1f} %, floor 99.0 %). D-041: no Chinese alternate passes the "
      f"false-turn-on check (InventChip IV2Q17020T4Z +4.0 V, IV2Q17040T4Z +4.41 V at the die, rev-5 gdrv_miller; single SG2M020170HJ "
      f"+3.96 V) - the qualified fallback is the Microchip part as an assembly variant; Sichain states no qualification (release "
      f"condition).\n")
    a("| item | value |")
    a("|---|---|")
    rows = [("switch positions", f"S1/S2 leg A, S3/S4 leg B, each {n} x {des['dev']} ({d['mfr']}, {d['vdss']} V, {d['package']})"),
            ("switching frequency", f"{des['fsw']/1e3:.1f} kHz, centre-aligned, dead time {tr.T_DEAD_FW*1e9:.0f} ns firmware = "
                                    f"{tr.T_DEAD_MIN*1e9:.0f}-{tr.T_DEAD*1e9:.0f} ns at the gates (UCC21710 interlock stretch), minimum pulse "
                                    f"{tr.T_MIN_PULSE*1e6:.2f} us, D_max {1-tr.T_MIN_PULSE*des['fsw']:.3f}"),
            ("gate drive", f"{d['vgs_on']:+.0f} / {d['vgs_off']:+.0f} V, R_G,on {d['rg_ext']*des['rg_on_mult']:.1f} ohm / R_G,off {d['rg_ext']} ohm per "
                           f"device + {des['rg_ks']} ohm Kelvin-source resistor in both paths (E_on x"
                           f"{dv.eon_rg_factor(d, d['rg_ext']*des['rg_on_mult'] + des['rg_ks']):.2f}, E_off x"
                           f"{dv.eoff_rg_factor(d, d['rg_ext'] + des['rg_ks']):.2f} from the datasheet E-vs-R_G curves), UCC21710 split outputs, 4 channels"),
            ("leg decoupling / damper", f"{des['dec'][1]} x {des['dec'][0]} per leg (PVR-03) / {DAMPER['n_r']} x {DAMPER['r_el']} ohm + "
                                        f"{DAMPER['n_c']} x {DAMPER['c_el']*1e9:.1f} nF per leg as drawn, its loss in the model (PVR-02)"),
            ("inductor", f"{tr.inductor_L(ind, tr.I_MAX)*1e6:.0f} uH at 45 A (L0 {ind['L0']*1e6:.0f} uH, {ind['L_ocp']*1e6:.0f} uH at the "
                         f"{des['i_ocp']:.1f} A trip), {ind['part']} x{ind['n_stack']} ({ind['material']}), {ind['turns']} turns Litz "
                         f"{ind['a_cu']*1e6:.1f} mm2, DCR {ind['rdc20']*1e3:.1f} mOhm @20 C, {ind['mass']:.2f} kg"),
            ("inductor flux", f"B_dc {ind['B_full']:.2f} T at 45 A, {ind['B_ocp']:.2f} T at trip (B_sat {ind['_mat']['bsat']} T); L at trip "
                              f"{ind['mu_ocp']*100:.0f} % of L0; 50 % of L0 at {spec['inductor']['I_at_50pct_L0_A']:.0f} A"),
            ("port A / B capacitors", f"{caps['A']['n']} x {caps['A']['part']} = {caps['A']['C']*1e6:.0f} uF / {caps['B']['n']} x "
                                      f"{caps['B']['part']} = {caps['B']['C']*1e6:.0f} uF (1300 V at 70 C, 1100 V at 85 C hot spot); trade-study "
                                      f"rule gave the minimum, the hand-off uses >= {PORT_CAPS_MIN} per port with ripple <= 50 % of the summed rating"),
            ("hardware current trip", f"{des['i_ocp']:.1f} A = {tr.OCP_MARGIN} x (45 A + half the worst ripple)")]
    for k, v in rows:
        a(f"| {k} | {v} |")
    if spec.get("device_alternate") and spec.get("device_primary"):
        a("\n### Primary vs alternate device (cell_spec takes the worse of the two)\n")
        a("| | primary | alternate |")
        a("|---|---|---|")
        p_, q_ = spec["device_primary"], spec["device_alternate"]
        for lab, k, f in (("device", "mpn", "{}"), ("maker", "manufacturer", "{}"), ("per position", "parallel", "{}"),
                          ("price USD (source)", "price_usd", "{:.2f}"), ("R_G on / off [ohm]", None, None),
                          ("lowest corner eta [%]", "eta_corner_min", "{:.2%}"), ("peak eta", "eta_peak", "{:.2%}"),
                          ("Tj max at 45 C [C]", "Tj_max_45C_C", "{:.0f}"), ("V_DS peak at OVP/trip [V]", "VDS_peak_V", "{:.0f}"),
                          ("turn-on dv/dt [V/ns]", "turn_on_dvdt_V_per_ns", "{:.0f}"), ("Miller: die peak [V]", "miller_die_peak_V", "{:+.2f}"),
                          ("body diode peak in a port short [A]", "body_diode_peak_A_in_port_short", "{:.0f}"),
                          ("qualification", "qualification", "{}")):
            if k is None:
                a(f"| {lab} | {p_['rg_on_ohm']} / {p_['rg_off_ohm']} | {q_['rg_on_ohm']} / {q_['rg_off_ohm']} |")
            else:
                a(f"| {lab} | {f.format(p_[k]) if p_[k] is not None else '-'} | {f.format(q_[k]) if q_[k] is not None else '-'} |")
    a("\n## 2. Efficiency\n")
    a(f"- Peak: **{peak['eff']*100:.2f} %** at {peak['va']:.0f}->{peak['vb']:.0f} V, {peak['p']/1e3:.1f} kW.")
    a("- Full-load points (P = P_lim, 45 C inlet):\n")
    a("| V_A->V_B | P [kW] | mode | eta [%] | loss [W] | cond | sw | dead | core | cu | cap | gate | misc | aux | damp | hottest Tj [C] |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, r in corners.items():
        lo = r["loss"]
        a(f"| {k} | {abs(r['p'])/1e3:.2f} | {r['mode']} | {r['eff']*100:.2f} | {r['ptot']:.0f} | " +
          " | ".join(f"{lo[x]:.1f}" for x in ("cond", "sw", "dead", "core", "cu", "cap", "gate", "misc", "aux", "damp")) + f" | {r['tj_max']:.0f} |")
    full = [e for e in env if e["load"] == 1.0]
    bad = [e for e in full if e["eff"] < 0.99]
    a(f"\n- Over the whole 250-1000 V square at P_lim: min {min(e['eff'] for e in full)*100:.2f} % at "
      f"{min(full, key=lambda e: e['eff'])['va']:.0f}->{min(full, key=lambda e: e['eff'])['vb']:.0f} V; "
      f"{len(bad)}/{len(full)} grid points below 99 %, of which {sum(1 for e in bad if 550 <= e['va'] <= 950 and 550 <= e['vb'] <= 950)} "
      f"inside the 550-950 V window. Below 99 % happens only where the 45 A limit cuts the power (low port voltage) and the other port is high.")
    a(f"- V_A = V_B transition (V_A = 800 V, P_lim): efficiency falls from {max(x['eff'] for x in sweep)*100:.2f} % to "
      f"{min(x['eff'] for x in sweep)*100:.2f} % in the band where both legs hard-switch; inductor ripple there "
      f"{min(x['ripple'] for x in sweep):.1f}-{max(x['ripple'] for x in sweep if x['mode']=='band'):.1f} A.")
    a("- Bidirectional: losses(V_A,V_B,+P) = losses(V_B,V_A,-P) (asserted); maps are A->B.\n")
    a("![maps](efficiency_tj_maps.png)\n\n![load](eff_vs_load_and_L_vs_I.png)\n")
    a("## 3. Thermal\n")
    hs = tr.heatsink_rth(flow_m3h=des["flow"])
    a(f"- Heatsink model (estimate): sink-to-inlet {hs[0]+hs[1]:.3f} K/W (effectiveness-NTU, of which mean air rise {hs[1]:.3f} K/W) at "
      f"{des['flow']:.0f} m3/h ({hs[2]:.1f} m/s; the real fan operating point is in module_report.md); "
      f"R_th,jc {d.get('rth_jc_max', d['rth_jc'])} K/W (datasheet max), case-sink {tr.rth_cs(d)} K/W (insulated, estimate), "
      f"spreading {tr.RTH_SPREAD} K/W.")
    a(f"- Hottest junction at full power: **{worst['tj']:.0f} C** at {tr.T_AIR:.0f} C inlet ({at['tj'][0]:.0f}->{at['tj'][1]:.0f} V, "
      f"{at['tj'][2]/1e3:.1f} kW); {tj60_full:.0f} C at 60 C inlet without derating; {tj60_der:.0f} C with the roadmap EVT derating (40 % at 60 C).")
    a(f"- Heatsink over-temperature trip: {spec['protection']['heatsink_overtemperature_trip_C']:.0f} C (sink temperature at which the hottest "
      f"device reaches the {TJ_DESIGN:.0f} C design limit at full power is {ts_trip:.0f} C).")
    a(f"- Inductor: worst loss {worst['p_ind']:.1f} W (core {worst['p_core']:.1f} W, copper {worst['p_cu']:.1f} W); estimated hot-spot rise "
      f"{ind['dT']:.0f} K at the design point (forced air, h = 25 W/m2K, estimate).\n")
    a("## 4. Device voltage and transient (ngspice)\n")
    a(f"- VDMOS model fitted from the {des['dev']} datasheet: V_th {d['vth']} V, C_iss/C_rss/C_oss at {d['c_at']:.0f} V, Q_gd "
      f"{qgd_fit*1e9:.1f} nC fitted to {d['qgd']*1e9:.0f} nC (C_gd shape a = {a_fit:.3g}), R_DS(on) {d['rds25']*1e3:.0f} mOhm, body diode from "
      f"V_SD/Q_rr; effective external gate resistance {rg_eff:.2f} ohm (datasheet {d['rg_ext']} ohm) fitted so that the simulated E_off at the "
      f"datasheet test point equals the datasheet value. All transient decks use this calibrated model.")
    a(f"- Model check at the datasheet point ({d['sw_v']:.0f} V, {d['check'][0][2]:.0f} A, 25 C, single device, 15 nH): E_off "
      f"{dpt_ds['e_off']*1e6:.0f} uJ vs datasheet-model {float(dv.e_sw(d, 'off', d['sw_v'], d['check'][0][2], 25))*1e6:.0f} uJ; E_on "
      f"{dpt_ds['e_on']*1e6:.0f} uJ vs {float(dv.e_sw(d, 'on', d['sw_v'], d['check'][0][2], 25))*1e6:.0f} uJ (the VDMOS body diode under-predicts "
      f"recovery, so the loss model keeps the datasheet energies; the transient model is used for di/dt and overshoot only).")
    a(f"- Worst commutation: {tr.V_OVP:.0f} V (OVP), {des['i_ocp']:.1f} A (hardware trip), {n} devices, loop {tr.L_LOOP['2L']*1e9:.0f} nH "
      f"with {tr.R_DAMP:.0f} ohm parallel damping (estimates), R_G,on = {des['rg_on_mult']:.1f} x R_G,off: overshoot of the switching device at "
      f"turn-off {dpt_w['dv_os_off']:.0f} V, of the complementary device during hard turn-on {dpt_w['dv_os_on']:.0f} V (turn-on dv/dt "
      f"{dpt_w['dvdt_on']/1e9:.0f} V/ns) -> **{dpt_w['dv_os']:.0f} V**, di/dt {dpt_w['didt']/1e9:.1f} A/ns, dv/dt {dpt_w['dvdt']/1e9:.0f} V/ns -> peak "
      f"{tr.V_OVP + dpt_w['dv_os']:.0f} V = {(tr.V_OVP + dpt_w['dv_os'])/d['vdss']:.2f} x V_DSS (rule <= {dv.V_PK_FRAC}). "
      f"Continuous {tr.V_MAX:.0f} V = {tr.V_MAX/d['vdss']:.2f} x V_DSS (rule <= {dv.V_DC_FRAC}). Deck: `sim/spice/{os.path.basename(dpt_w['deck'])}`.")
    voff_ds = d.get("vgs_off_ds", d["vgs_off"])
    a(f"- **Gate rail {d['vgs_on']:+.0f}/{d['vgs_off']:+.0f} V** (gate-driver design, `{gd['src']}`: on {gd['von'][0]}-{gd['von'][1]} V, off "
      f"{gd['voff'][0]}..{gd['voff'][1]} V; +20/-5 V is outside the UCC14241-Q1 range). Peak gate current {gd['i_src']} A source / "
      f"{gd['i_snk']} A sink per channel (6/4 ohm per device, driver ROH_EFF/ROL included) vs the UCC21710's 10 A: the 10.3 A in the "
      f"previous cell_spec used 25 V and left out the 0.3 ohm pull-down. DESAT {gd['trip_nom']} V nominal ({gd['trip'][0]}-{gd['trip'][1]} V), "
      f"blanking {gd['blank'][0]:.0f}-{gd['blank'][1]:.0f} ns, detect + soft turn-off {gd['t_sc']} us vs {gd['scwt']} us typical withstand.")
    a(f"- What {d['vgs_off']:+.0f} V instead of the datasheet's {voff_ds:+.0f} V changes in the model: (1) turn-off energy x{f_vgs:.3f} "
      f"(calibrated VDMOS at the datasheet point; the stored factor {d.get('eoff_vgs_factor', 1.0)} is asserted against it and applied to "
      f"the datasheet E_off); (2) gate-charge power uses the {d['vgs_on'] - d['vgs_off']:.0f} V swing; (3) dead-time diode drop kept at "
      f"the datasheet's -5 V value (slightly conservative); (4) dv/dt immunity: see the Miller check below.")
    leg_report(a, des, gd, immun, dec, mil, spec)
    a("- Switching-level FSBB decks (VDMOS devices, gate drives with dead time, loop inductance, C4AQ banks, linear L at the operating bias):\n")
    a("| point | V_A->V_B (sim) | i_L avg [A] | ripple sim / analytic [A] | max V_DS sim / analytic: S1, S2, S3, S4 [V] | P_in-P_out-dE/dt sim [W] | deck |")
    a("|---|---|---|---|---|---|---|")
    for c in comp:
        vs_ = ", ".join(f"{c['vmax_sim'][k]:.0f}/{c['vmax_an'][k]:.0f}" for k in ("vds_At", "vds_Ab", "vds_Bt", "vds_Bb"))
        a(f"| {c['tag']} | {c['va']:.0f}->{c['vb']:.0f} | {c['i_avg']:.1f} | {c['ripple_sim']:.2f} / {c['ripple_an']:.2f} | {vs_} | "
          f"{c['pin']-c['pout']-c['dE_W']:.0f} | `{c['deck']}` |")
    a("\nAnalytic device voltage = blocking port voltage + turn-off overshoot scaled with the peak current (S1/S4) or turn-on ringing scaled "
      "with the blocking voltage (S2/S3), both from the worst-case commutation run. "
      "Asserted: ripple within 15 %, every device peak within 12 %. The simulated P_in-P_out uses VDMOS switching (not datasheet energies) "
      "and is shown for information only.\n\n![spice](spice_vs_analytic.png)\n")
    a("## 5. Worst-case stresses (envelope, both directions)\n")
    ws = spec["worst_case_stresses"]
    for k, v in ws.items():
        a(f"- {k}: {v:.1f}" if isinstance(v, float) else f"- {k}: {v}")
    a("\n## 6. Module level (cells interleaved on common port banks)\n")
    a("| cells | interleave | P rated/max [kW] | C per port total [uF] (parts) | worst port ripple current [A rms] | per capacitor [A rms] (use limit) | worst port ripple [V pp] | ripple freq [kHz] |")
    a("|---|---|---|---|---|---|---|---|")
    for nc in (1, 3, 4):
        rows = mod[nc]
        ncap = nc * caps["A"]["n"]
        lim = dv.CAP_IRMS_USE * dv.C4AQ[caps["A"]["part"]]["irms"]
        a(f"| {nc} | {360/nc:.0f} deg | {nc*tr.P_RATED/1e3:.0f}/{nc*tr.P_MAX/1e3:.1f} | {rows[0]['C_total']*1e6:.0f} ({ncap}) | "
          f"{max(r['irms_ac'] for r in rows):.1f} | {max(r['irms_ac'] for r in rows)/ncap:.1f} ({lim:.1f}) | {max(r['dv_pp'] for r in rows):.2f} | {nc*des['fsw']/1e3:.0f} |")
    a("\n## 7. Port-terminal short: bus reverse clamp (cell_spec.json 'bus_reverse_clamp')\n")
    cs = spec["bus_reverse_clamp"]
    lp3, lp4 = port_loop(3), port_loop(4)
    a(f"Loop from sim/out/port_design: L {lp3['L']*1e6:.2f} uH (A_L_LOOP), R {lp3['R']*1e3:.2f} mOhm (135 A port: A_R_LOOP_X + 2 cold "
      f"{lp3['fuse']}) / {lp4['R']*1e3:.2f} mOhm (180 A port, {lp4['fuse']}); bank {des['caps']['A']['n']} x {des['caps']['A']['part']} per "
      f"port per cell (ESR, ESL from C4AQ p.14); body diodes: MSC035SMA170B4 Fig.1-9 fit; bolted short (0 ohm). Decks "
      f"`sim/spice/pv_dump_*.cir`.\n")
    a("| cells | port | bank V | module peak [kA] | Q | clamp | body diode per device: peak [A] / I2t [A2s] / energy | clamp per device: peak [A] / I2t [A2s] (allowed at t95) | bank internal min [V] | fuse I2t [kA2s] (melt) |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for (ncell, port, v0, ncl), r_ in sorted(brc["cases"].items()):
        lp = port_loop(ncell)
        a(f"| {ncell} | {port} | {v0:.0f} | {r_['i_total_pk']/1e3:.1f} | {r_['q']:.1f} | {('%d x ' % ncl + CLAMP_PART) if ncl else 'none'} | "
          f"{r_['body_pk']:.0f} / {r_['body_i2t']:.3g} / {r_['body_e']*1e3:.3g} mJ | " +
          (f"{r_['clamp_dev_pk']:.0f} / {r_['clamp_dev_i2t']:.0f} ({clamp_allowed_i2t(r_['t95']):.0f})" if ncl else "-") +
          f" | {r_['vc_min']:.0f} | {r_['fuse_i2t']/1e3:.0f} ({lp['melt_i2t']/1e3:.0f}) |")
    a(f"\n- **Without a clamp** every SiC device of the shorted port's leg carries up to {cs['without_clamp']['per_device_peak_A_max']:.0f} A "
      f"({cs['without_clamp']['per_device_I2t_A2s_max']:.0f} A2s, {cs['without_clamp']['per_device_energy_J_max']:.1f} J; the datasheet's only "
      f"limit is I_DM {des['d']['idm']:.0f} A pulsed, p.2): the leg is destroyed. The fuse's I2t is reached during the freewheel, not "
      f"before the reversal.")
    a(f"- **Clamp:** {cs['devices_per_bank']} x {CLAMP_PART} ({cs['ratings']['V_RRM_V']:.0f} V, I_FSM {cs['ratings']['I_FSM_10ms_A']:.0f} A, "
      f"I2t {cs['ratings']['I2t_10ms_A2s']:.0f} A2s at 10 ms, I_FSM-vs-t_p curve Fig.4 for shorter pulses, {dv.RECTIFIERS[CLAMP_PART]['src']}) "
      f"in parallel across EACH bank (port A and port B "
      f"of every cell, cathode to bus +): {cs['per_module']['3_cells']} per PV-P75, {cs['per_module']['4_cells']} per PV-P100/110. Chosen as "
      f"the smallest count from {N_CLAMP_SET} with the body diodes <= 80 % of I_DM and the clamp I2t <= 50 % of its rating in the worst case.")
    a(f"- Worst clamp duty: {cs['surge_worst']['case']}: group peak {cs['surge_worst']['clamp_group_peak_kA']} kA (the bank's internal "
      f"ESL resonates with the clamp, so the peak exceeds the loop current), {cs['surge_worst']['per_device_peak_A']:.0f} A and "
      f"{cs['surge_worst']['per_device_I2t_A2s']:.0f} A2s per device vs {cs['surge_worst']['allowed_I2t_at_t95_A2s']:.0f} A2s allowed at "
      f"t95 = {cs['surge_worst']['t95_ms']} ms (I2sqrt(t) x sqrt(t95)): margin {cs['surge_worst']['I2t_margin']}x. Reverse voltage "
      f"{cs['reverse_voltage_V']:.0f} V ({cs['reverse_voltage_basis']}) vs V_RRM {cs['ratings']['V_RRM_V']:.0f} V: margin "
      f"{cs['reverse_voltage_margin']}.")
    a(f"- **Body-diode share with the clamp:** <= {cs['body_diode_share_with_clamp']['per_device_peak_A_max']:.0f} A peak per device "
      f"(I_DM {des['d']['idm']:.0f} A), {cs['body_diode_share_with_clamp']['per_device_I2t_A2s_max']:.3g} A2s, "
      f"{cs['body_diode_share_with_clamp']['per_device_energy_mJ_max']:.3g} mJ: a microsecond transient while the clamp current builds "
      f"up, inside the datasheet's pulsed rating.")
    a(f"- **Where:** {cs['placement']}. Tolerated clamp-branch inductance {cs['branch_inductance_max_nH']} nH (body diodes reach I_DM "
      f"beyond it; sweep below), estimated {cs['branch_inductance_assumed_nH']} nH for {cs['devices_per_bank']} TO-247 in parallel. "
      f"The film capacitors' own ESL makes their internal voltage reverse to {cs['film_bank_internal_reversal_V']:.0f} V for "
      f"microseconds - ask KEMET for the C4AQ reversal allowance.")
    a("\n| clamp branch [nH] | " + " | ".join(f"{l*1e9:.2f}" for l in sorted(brc["lsweep"])) + " |")
    a("|---|" + "---|" * len(brc["lsweep"]))
    a("| body diode peak per device [A] | " + " | ".join(f"{brc['lsweep'][l]['body_pk']:.0f}" for l in sorted(brc["lsweep"])) + " |")
    a(f"\n- Leakage at 1000 V / 85 C: {cs['leakage_W_per_module_at_1000V_85C']['3_cells']} W per PV-P75, "
      f"{cs['leakage_W_per_module_at_1000V_85C']['4_cells']} W per PV-P100/110 ({cs['leakage_W_per_module_at_1000V_85C']['basis']}). "
      f"Normal operation: {cs['normal_operation']}.")
    if brc["alt"]:
        a("- **Alternative, differential-mode inductance** (port design's 5 kA values): " + "; ".join(
            f"{nc} cells {r_['l_main']*1e6:.1f} uH: module peak {r_['i_total_pk']/1e3:.1f} kA but Q = {r_['q']:.0f}, the bank still reverses "
            f"and each body diode carries {r_['body_pk']:.0f} A, {r_['body_i2t']:.0f} A2s" for nc, r_ in brc["alt"].items()) +
          ". Overdamping would need R >= 2 sqrt(L/C) in the 135-180 A path. The inductance only lowers the peak "
          "(contactor weld); it does not replace the clamp, and a 10-15 uH choke at 135-180 A DC is a large part with its own loss.")
    a(f"- Contactor weld: {cs['contactor_weld']} - the port design's weld finding stands.")
    alt_ = cs["alternatives_rejected"]
    a(f"- **One clamp per port (the DAB's DD600N16K arm) does not work here.** On the port board, {L_STRAY_PORT*1e9:.0f} nH per cell "
      f"(low estimate) away from the banks, each SiC device still carries "
      f"{alt_['DD600N16K_one_arm_per_port_on_port_board']['per_device_body_peak_A']:.0f} A "
      f"({alt_['DD600N16K_one_arm_per_port_on_port_board']['per_device_body_I2t_A2s']:.0f} A2s): the current in that bus closes through the "
      f"body diodes whatever the port-board clamp does. One arm per bank: {alt_['DD600N16K_one_arm_per_bank']['per_device_body_peak_A']:.0f} A "
      f"per device - its terminal inductance ({dv.RECTIFIERS['DD600N16K']['l_arm_est']*1e9:.0f} nH estimated, low) is above the "
      f"{brc['l_max']*1e9:.2f} nH the bank tolerates. Decision: {cs['decision']}.")
    a("\n## 8. Sensing and protection (see cell_spec.json)\n")
    for k, v in spec["protection"].items():
        a(f"- {k}: {v}")
    a("\n## 9. Assumptions and open items\n")
    a(f"- Loop inductance {tr.L_LOOP['2L']*1e9:.0f} nH, heatsink geometry/airflow, insulated-mount R_cs, bias/sense power, cost weights: estimates "
      f"(see sim/out/pv_tradeoff/report.md section 6). Overshoot scales with the real layout: re-run the DPT deck with the extracted loop.")
    a(f"- No 1700 V cosmic-ray FIT data on file; the 0.67 x V_DSS rule is scaled from Wolfspeed's Gen3 1200 V curve. Request Microchip FIT vs "
      f"V_DS for {des['dev']} (and the 2000 V alternative IMYH200R024M1H/IMYH200R012M1H, Infineon) before schematic freeze.")
    a(f"- Parallel devices assumed to share current and switching energy equally; layout must be symmetric (Kelvin source, equal gate loops).")
    a(f"- Second source: the Infineon 2 kV variant (candidate A2k in the study) needs different gate voltages (+18/-3 V) and package (TO-247-4-PLUS); "
      f"onsemi NTH4L028N170M1 (1700 V, 28 mOhm) was not on file and is not modelled.")
    a("- Inductor: catalog fits only; build and measure L(I), loss and temperature on a prototype; Litz strand and layer design open.")
    a(f"- Physical-leg decks: VDMOS body diode is capacitive only (Q_oss). The decoupling check adds the datasheet Q_rr less Q_oss(1000 V) "
      f"as a linear capacitance switched in for the turn-on edge (ESTIMATE, conservative: it also lifts the device peak to "
      f"{next((r.get('v_pk_rr') or float('nan') for r in dec['rows'] if (r['part'], r['n']) == dec['choice']), float('nan')):.0f} V); "
      f"the damper table and the Miller check use "
      f"Q_oss only. 15 nH bulk-to-leg bus and 11.5 nH board + devices are layout requirements/estimates; re-run with the extracted layout.")
    a(f"- Clamp alternatives: the {L_STRAY_PORT*1e9:.0f} nH port-board-to-bank bus and the 5 nH DD600N16K arm are deliberately low "
      f"estimates; both only strengthen the per-bank decision.")
    a("- Nothing here is bench-validated.\n")
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    alt = run("alternate", write=False)
    spec, comp = run("primary", write=True, alt=alt)
    for role in ("primary", "alternate"):
        b = spec["device_" + role]
        print(f"{role}: {b['parallel']} x {b['mpn']} ({b['manufacturer']}): corners min {b['eta_corner_min']*100:.2f} %, peak "
              f"{b['eta_peak']*100:.2f} %, Tj {b['Tj_max_45C_C']:.0f} C, V_DS peak {b['VDS_peak_V']:.0f} V, Miller die {b['miller_die_peak_V']} V")
    print(f"worse of both: corners " + ", ".join(f"{k} {v*100:.2f}" for k, v in spec['efficiency']['corners'].items()
                                                if k in ('550->950', '950->550', '550->550', '950->950')) + f", peak {spec['efficiency']['peak']*100:.2f} %")
    print(f"Tj max {spec['worst_case_stresses']['Tj_max_45C_C']:.0f} C, V_DS peak {spec['worst_case_stresses']['device_VDS_peak_V']:.0f} V, "
          f"cost {spec['cost_usd']['per_cell']['total']:.0f} USD per cell")
    for c in comp:
        print(f"  spice {c['tag']}: ripple {c['ripple_sim']:.2f} vs {c['ripple_an']:.2f} A")
    print("pv_design self-check passed")
