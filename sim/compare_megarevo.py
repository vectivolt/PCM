"""PV-P75 (cost-first build: power board PV-PWR + control board PV-CTL) against the Megarevo PMD-75-G3, row by row.

Every "ours" value is read from a file another script wrote (design hand-off JSON, board design check, board build
report) or from the architecture specification's accuracy table, so the comparison cannot drift from the design.
Megarevo's column is the published table saved in docs/reference-designs/megarevo/pmd-75-g3/spec.md. Ours is
CALCULATED / SIMULATED, theirs is PUBLISHED - neither column is a measurement of ours.

Usage: .venv/bin/python sim/compare_megarevo.py   ->  sim/out/compare_megarevo/report.md + comparison.csv
Exit code 1 while a source file is missing (row = PENDING), a board build is failing, or a row is BELOW.
"""
import csv
import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(REPO, "sim", "out", "compare_megarevo")
SPEC = "docs/reference-designs/megarevo/pmd-75-g3/spec.md"
F = {"module": "sim/out/pv_design/module_spec.json", "cell": "sim/out/pv_design/cell_spec.json",
     "port": "sim/out/port_design/port_spec.json", "control": "sim/out/pv_control/control_spec.json",
     "aux": "sim/out/aux_hv_design/aux75_spec.json", "arch": "docs/requirements/ARCHITECTURE-COSTFIRST.md",
     "ctl_check": "hardware/PV-CTL/outputs/PV-CTL_design_check.txt"}
BOARDS = ["PV-PWR", "PV-CTL"]      # the cost-first PV-P75 (D-044 / D-045); the earlier eight-board platform is frozen


def megarevo():
    """{(section, parameter): value} from the saved published table."""
    rows, section = {}, ""
    for line in open(os.path.join(REPO, SPEC), encoding="utf-8"):
        m = re.match(r"\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*$", line)
        if not m or set(m.group(1)) <= set("-: "):
            continue
        if m.group(1).startswith("**"):
            section = m.group(1).strip("* ")
        elif m.group(1) != "Parameter":
            rows[(section, m.group(1))] = m.group(2)
    assert len(rows) >= 30, "published table not found in " + SPEC
    return rows


def load(key):
    p = os.path.join(REPO, F[key])
    if not os.path.exists(p):
        return None
    return json.load(open(p)) if p.endswith(".json") else open(p, encoding="utf-8").read()


def find(d, *words):
    """First numeric leaf of a nested dict whose path contains every word (case-insensitive). The control hand-off
    file is written by another script; this keeps the comparison tolerant of its layout."""
    hits = []

    def walk(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, path + "/" + str(k))
        elif isinstance(x, (int, float)) and not isinstance(x, bool) and all(w in path.lower() for w in words):
            hits.append((path, x))
    walk(d, "")
    return hits[0] if hits else (None, None)


def pct(x):
    return "%.2f %%" % (100 * x)


def main():
    mg = megarevo()
    mod, cell, port, ctl, aux, arch, chk = (load(k) for k in ("module", "cell", "port", "control", "aux", "arch", "ctl_check"))
    rows = []      # (section, parameter, megarevo, ours, verdict, evidence)

    def add(key, ours, verdict, evidence):
        rows.append((key[0], key[1], mg[key], ours, verdict, evidence))

    def need(src, name, *keys):
        for key in keys if src is None else ():
            add(key, "source not built yet", "PENDING", F[name])
        return src is not None

    if need(mod, "module", ("DC Data", "Rated Power (kW)")):
        M, C, T = mod["modules"]["PV-P75"], mod["costfirst"]["modules"]["PV-P75"], mod["costfirst"]["trip_band"]
        ev = F["module"]
        add(("DC Data", "Rated Power (kW)"), "%g (3 interleaved phases on one power board)" % M["P_rated_kW"],
            "MEETS" if M["P_rated_kW"] >= 75 else "BELOW", ev)
        add(("DC Data", "Max. Power (kW)"), "%g" % M["P_max_kW"], "MEETS" if M["P_max_kW"] >= 82.5 else "BELOW", ev)
        rng = "%g~%g" % tuple(M["V_ports_V"])
        full = "%g~%g" % tuple(M["V_full_power_V"])
        for sec in ("PV Data", "Output Data"):
            vr = "Operating Voltage Range (V)" if sec == "PV Data" else "Voltage Range (V)"
            add((sec, vr), rng, "MEETS" if M["V_ports_V"] == [250.0, 1000.0] else "BELOW", ev)
            add((sec, "Full-Load Voltage Range (V)"), full, "MEETS" if M["V_full_power_V"] == [550.0, 950.0] else "BELOW", ev)
            add((sec, "Max. Operating Current (A)"), "%g (3 x 45 A)" % M["I_port_max_A"],
                "MEETS" if M["I_port_max_A"] >= 135 else "BELOW", ev)
        eta = M["efficiency_peak_megarevo_comparable"]
        at = M["efficiency_peak_at"]
        worst = min(M["efficiency_corners_45C"].values())
        add(("General Data", "Max. Efficiency"),
            "%s peak at %g->%g V, %g kW (everything inside the module: phases, ports, auxiliary supply, contactor coils, "
            "fans); %s at the worst full-power corner, 45 C inlet" % (pct(eta), at["va"], at["vb"], at["p_kW"], pct(worst)),
            "BETTER" if eta > 0.99 else "MEETS" if eta >= 0.99 else "BELOW", ev)
        d = M["derating_fraction_of_P_max"]
        sea, pess, t = d["sea level"], M["derating_pessimistic_sea_level"], d["inlet_C"]
        cold = mod["fan"]["ambient_rated_C"][0]          # the fan's rated minimum ambient sets the cold limit
        add(("General Data", "Operating Temperature Range (℃)"),
            "cold side: the fan is rated to %+d C (requirement and Megarevo: -30 C); the minimum rating of every other "
            "part has not been audited one by one. Hot side: full %g kW to %d C inlet at sea level, %d %% at 60 C "
            "(nominal thermal model; %d %% with the pessimistic model)"
            % (cold, M["P_max_kW"], max(c for c, f in zip(t, sea) if f >= 1.0), round(100 * sea[-1]), round(100 * pess[-1])),
            "MEETS" if sea[t.index(45)] >= 1.0 and cold <= -30 else "BELOW", ev)
        alt = dict(zip(M["altitude_m"], M["derating_fraction_45C_vs_altitude"]))
        full3 = [c for c, f in zip(t, d["3000 m"]) if f >= 0.995]
        margin = {"inductor hot spot": C["inductor_hot_spot_full_power_C"], "heatsink": C["t_sink_full_power_C"]}
        lim = min(margin, key=lambda k: margin[k]["trip_low_C"] - margin[k]["45C"])
        add(("General Data", "Operating Altitude (m)"),
            "derates with altitude: at 45 C inlet %d %% at 2000 m and %d %% at 3000 m; full power at 3000 m only %s. "
            "The %s is the limit (%.1f C against a %.1f C trip at sea level, 45 C inlet)"
            % (round(100 * alt[2000]), round(100 * alt[3000]),
               "up to about %d C inlet" % max(full3) if full3 else "below 25 C inlet", lim, margin[lim]["45C"], margin[lim]["trip_low_C"]),
            "MEETS" if alt[3000] >= 1.0 else "BELOW", ev)
        add(("General Data", "Noise Level (dB)"),
            "%.1f dB(A) @1 m at full power, 45 C inlet (fan datasheet sound data + 3 dB installation estimate)"
            % M["noise_dBA_1m_full_45C"], "BETTER" if M["noise_dBA_1m_full_45C"] < 65 else "BELOW", ev)
        fan = mod["fan"]
        add(("General Data", "Cooling Method"),
            "forced air, %d x %s %s on one earthed heatsink, speed set through the fan supply from the heatsink and "
            "inductor NTCs; one fan failed still holds %d %% at 45 C"
            % (fan["count_per_module"], fan["manufacturer"], fan["mpn"], round(100 * M["one_fan_failed_fraction_45C"]["no flap"])),
            "MEETS", ev)
        sb = C["standby_W"]
        hi, mid = sb["V"].index(1000), sb["V"].index(600)
        add(("Other Data", "Standby Power Consumption (W)"),
            "%.1f with both contactors open, %.1f with both held closed and the gates off, at 1000 V (%.1f / %.1f at 600 V; "
            "estimate)" % (sb["contactors_open_W"][hi], sb["contactors_held_W"][hi], sb["contactors_open_W"][mid], sb["contactors_held_W"][mid]),
            "MEETS" if sb["contactors_held_W"][hi] < 20 else "BELOW", ev)
        bank = C["port_film_bank"]["B"]
        add(("Protection Data", "Open-Circuit Protection (Both Ports)"),
            "full-power load rejection (calculated): the %g-%g V hardware over-voltage trip has the gates off %g us after "
            "the crossing; the battery-side film bank needs %d capacitors to stay under the device voltage limit and the "
            "drawn board has %d" % (T["ov_trip_V"][0], T["ov_trip_V"][1], T["ov_response_us"], bank["parts_min"], bank["parts_today"]),
            "MEETS" if bank["parts_today"] >= bank["parts_min"] else "BELOW", ev)

    if need(cell, "cell", ("Protection Data", "Over/Under Voltage Protection (Both Ports)")) and mod:
        P, T, H = cell["protection"], mod["costfirst"]["trip_band"], mod["costfirst"]["heatsink"]
        add(("Protection Data", "Over/Under Voltage Protection (Both Ports)"),
            "hardware trip %g-%g V (discrete comparators, latched) and firmware limit %g V on each port, stop below %g V"
            % (T["ov_trip_V"][0], T["ov_trip_V"][1], P["overvoltage_sw_limit_V"], P["undervoltage_stop_V"]), "MEETS",
            F["cell"] + ", " + F["module"])
        add(("Protection Data", "Overtemperature Protection"),
            "hardware trips: heatsink %g-%g C, inductor %g-%g C; firmware derating below them"
            % tuple(H["trip_band_C"] + H["inductor_trip_band_C"]), "MEETS", F["module"])
        add(("Protection Data", "Isolation Method"), "non-isolated, common negative rail (%s)" % cell["topology"].split(",")[0],
            "MEETS", F["cell"])
        if port:
            lean = port["lean"]
            dev = T["per_device"]["primary"]
            add(("Protection Data", "Overcurrent Protection (Both Ports)"),
                "per phase: discrete hardware trip %g-%g A on the inductor current, controller comparator backup %g-%g A; "
                "battery port: hardware over-current trip %g-%g A"
                % tuple(dev["hardware"]["band_A"] + dev["backup"]["band_A"] + lean["port_oc_trip_A"]), "MEETS",
                F["module"] + ", " + F["port"])
            add(("Protection Data", "Short-Circuit Protection"),
                "gate-driver DESAT at %g V, gates off %.2f us after detection (the %g us device withstand is an assumption: "
                "no maker publishes one); %g A aR fuse on each battery pole; the contactor is held closed above %g A so the "
                "fuse clears first; faults of %g-%g A must be cleared upstream within %.2f s (installation requirement); "
                "the PV port has no fuse (current-limited source)"
                % (P["desat_VDS_threshold_V"], P["short_circuit_detect_to_off_us"], P["short_circuit_withstand_us_typ"],
                   lean["battery_fuse"]["In_A"], lean["contactor"]["hold_off_band_A"][0], lean["installation"]["band_A"][0],
                   lean["installation"]["band_A"][1], lean["installation"]["clear_within_s"]),
                "MEETS", F["cell"] + ", " + F["port"])

    if need(port, "port", ("Protection Data", "Lightning Protection")):
        add(("Protection Data", "Lightning Protection"),
            "monitored varistor network on the board, three %s %s per port (pole to midpoint twice, midpoint to PE): "
            "effective level %g V pole-to-PE at %g kA (calculated), %g kA maximum"
            % (port["spd"]["mov_mfr"], port["spd"]["mov_mpn"], port["spd"]["Up_eff_pe_V"], port["spd"]["In_A"] / 1e3,
               port["spd"]["Imax_A"] / 1e3), "MEETS", F["port"])
        add(("Protection Data", "Insulation Impedance Detection"),
            "switched %.0f kOhm test strings pole-to-PE through isolated solid-state relays on the power board; %.1f kOhm "
            "threshold (1000 V / 30 mA)" % (port["imd"]["R_t_ohm"] / 1e3, port["imd"]["threshold_ohm"] / 1e3), "MEETS", F["port"])

    acc = (("Other Data", "Voltage Accuracy"), ("Other Data", "Current Accuracy"))
    if need(arch, "arch", *acc) and need(chk, "ctl_check", *acc):
        v = re.search(r"\| VA, VB[^\n]*?≤ ([\d.]+) %", arch)
        i = re.search(r"\| port currents[^\n]*?≤ ([\d.]+) % at 135 A", arch)
        a = re.search(r"ADC accuracy[^\n]*?worst ([\d.]+) %", chk)
        assert v and i and a, "accuracy figures not found in %s / %s" % (F["arch"], F["ctl_check"])
        tail = ("after the two-point calibration; a design budget, not an end-to-end calculation on the drawn boards "
                "(the control board's ADC share checks at %s %% worst)" % a.group(1))
        add(acc[0], "<= %s %% (0.1 %% dividers, ADC and reference) %s" % (v.group(1), tail),
            "MEETS" if float(v.group(1)) < 1.0 else "BELOW", F["arch"] + ", " + F["ctl_check"])
        add(acc[1], "<= %s %% at 135 A (shunt in the negative rail, temperature compensated) %s" % (i.group(1), tail),
            "MEETS" if float(i.group(1)) < 1.0 else "BELOW", F["arch"] + ", " + F["ctl_check"])

    if need(ctl, "control", ("PV Data", "MPPT Tracking Accuracy")):
        path, eta = find(ctl, "mppt", "static")
        if eta is None:
            path, eta = find(ctl, "static", "eff")
        assert eta is not None, "no static MPPT efficiency found in " + F["control"]
        eta = eta / 100.0 if eta > 1.5 else eta
        add(("PV Data", "MPPT Tracking Accuracy"), "%.3f %% static (simulated), key %s" % (100 * eta, path),
            "MEETS" if eta >= 0.999 else "BELOW", F["control"])
        add(("DC Data", "MPPT Channels"), "1 centralised tracker over the 3 interleaved phases", "MEETS", F["control"])
    if need(aux, "aux", ("PV Data", "PV Start-up Voltage (V)")):
        lo, hi = min(aux["startup"]["brown_in_V"]), max(aux["startup"]["brown_in_V"])
        add(("PV Data", "PV Start-up Voltage (V)"), "auxiliary supply starts at %.0f-%.0f V; the converter runs from 250 V" % (lo, hi),
            "MEETS" if hi <= 250 else "BELOW", F["aux"])

    reports = {b: os.path.join(REPO, "hardware", b, "outputs", b + "_report.json") for b in BOARDS}
    built = {b: json.load(open(p)) if os.path.exists(p) else None for b, p in reports.items()}
    ctl_ok = "MEETS" if built["PV-CTL"] and built["PV-CTL"]["passed"] else "PENDING"
    add(("Communication Data", "Communication"),
        "isolated CAN and isolated RS-485 on the control board, behind the module's one reinforced barrier", ctl_ok, "gen/pv_ctrl.py")
    add(("Communication Data", "BMS Interface"),
        "over the same isolated CAN / RS-485 port (the cost-first build has no separate gateway card)", ctl_ok, "gen/pv_ctrl.py")
    na = "not assessed: mechanics and enclosure are outside this project's scope (schematic + BOM + simulation)"
    for key in (("General Data", "Relative Humidity"), ("General Data", "IP Rating"), ("General Data", "Dimensions L*W*H (mm)"),
                ("General Data", "Weight (kg)"), ("General Data", "Mounting Method")):
        add(key, na, "NOT ASSESSED", "docs/requirements/REQUIREMENTS.md section 1")

    missing = sorted(set(mg) - {(r[0], r[1]) for r in rows})
    assert not missing or any(r[4] == "PENDING" for r in rows), "published rows without a comparison: %s" % missing
    order = list(mg)
    rows.sort(key=lambda r: order.index((r[0], r[1])))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "comparison.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["section", "parameter", "megarevo_pmd_75_g3_published", "pv_p75_calculated", "verdict", "evidence"])
        w.writerows(rows)
    count = {v: sum(r[4] == v for r in rows) for v in ("BETTER", "MEETS", "BELOW", "NOT ASSESSED", "PENDING")}
    bad_boards = [b for b, r in built.items() if not (r and r["passed"])]
    L = ["# PV-P75 (cost-first build) compared with Megarevo PMD-75-G3", "",
         "Generated by `sim/compare_megarevo.py`. **Megarevo's column is what it publishes; ours is calculated or "
         "simulated from the design files named in the evidence column. Nothing of ours is bench-validated.**", "",
         "Result: %d better, %d meets, %d below, %d not assessed, %d pending, out of %d published rows."
         % (count["BETTER"], count["MEETS"], count["BELOW"], count["NOT ASSESSED"], count["PENDING"], len(mg)), "",
         "| Parameter | Megarevo PMD-75-G3 (published) | PV-P75 (calculated) | Verdict | Evidence |", "|---|---|---|---|---|"]
    section = None
    for sec, par, theirs, ours, verdict, ev in rows:
        if sec != section:
            L.append("| **%s** | | | | |" % sec)
            section = sec
        L.append("| %s | %s | %s | **%s** | `%s` |" % (par, theirs, ours, verdict, ev.replace(", ", "`, `")))
    L += ["", "## Boards behind the PV-P75 column", "", "| Board | Rev | Parts | Nets | Build checks |", "|---|---|---|---|---|"]
    for b in BOARDS:
        r = built[b]
        L.append("| %s | %s | %s | %s | %s |" % ((b, r.get("rev", "-"), r["parts"], r["nets"], "all passed" if r["passed"] else "FAILING")
                                                  if r else (b, "-", "-", "-", "not built yet")))
    bom = os.path.join(REPO, "bom", "PV-P75_module_BOM.csv")
    if os.path.exists(bom):
        lines = list(csv.DictReader(open(bom, encoding="utf-8")))
        L += ["", "Module BOM: %d lines, %d parts (`bom/PV-P75_module_BOM.csv`); cost in `bom/COST.md`." % (len(lines), sum(int(r["Qty"]) for r in lines))]
    L += ["", "## Reading this table", "",
          "- **BETTER / MEETS** compare a calculated figure of ours with a published figure of theirs. A calculated "
          "99.5 % and a published 99 % are not the same kind of evidence.",
          "- **BELOW** rows are the price of the cost-first build or open work, not rounding: each is listed with its "
          "remedy in `docs/guide/12-risks-and-open-items.md`.",
          "- **NOT ASSESSED** rows need the mechanical design, which this project does not contain.",
          "- Decisions and their open conditions are in `docs/requirements/DECISIONS.md`; the assumptions behind the "
          "module figures are in `sim/out/pv_design/module_report.md`."]
    open(os.path.join(OUT, "report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("PV-P75 vs PMD-75-G3: " + ", ".join("%d %s" % (n, v.lower()) for v, n in count.items()))
    for r in rows:
        if r[4] in ("BELOW", "PENDING"):
            print("  %-8s %s / %s: %s" % (r[4], r[0], r[1], r[3][:110]))
    if bad_boards:
        print("  boards not built or failing: " + ", ".join(bad_boards))
    assert sum(count.values()) == len(rows) == len(mg)
    return 1 if count["BELOW"] or count["PENDING"] or bad_boards else 0


if __name__ == "__main__":
    sys.exit(main())
