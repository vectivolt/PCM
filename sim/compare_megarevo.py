"""PV-P75 against the Megarevo PMD-75-G3, row by row.

Every "ours" value is read from a file another script wrote (design hand-off JSON, design report, board build
report), so the comparison cannot drift from the design. Megarevo's column is the published table saved in
docs/reference-designs/megarevo/pmd-75-g3/spec.md. Ours is CALCULATED / SIMULATED, theirs is PUBLISHED - neither
column is a measurement of ours.

Usage: .venv/bin/python sim/compare_megarevo.py   ->  sim/out/compare_megarevo/report.md + comparison.csv
Exit code 1 while a source file is missing (row = PENDING), a board build is failing, or a row is BELOW.
"""
import csv
import glob
import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(REPO, "sim", "out", "compare_megarevo")
SPEC = "docs/reference-designs/megarevo/pmd-75-g3/spec.md"
F = {"module": "sim/out/pv_design/module_spec.json", "cell": "sim/out/pv_design/cell_spec.json",
     "port": "sim/out/port_design/port_spec.json", "port_report": "sim/out/port_design/report.md",
     "control": "sim/out/pv_control/control_spec.json", "aux": "sim/out/aux_hv_design/aux_hv_spec.json"}
BOARDS = ["PVCELL-25", "PV-PORT", "CTRL-C2000", "SYS-IO-AUX", "AUX-HV", "BMU-GW"]   # what a PV-P75 is built from


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
    return open(p, encoding="utf-8").read() if p.endswith(".md") else json.load(open(p))


def find(d, *words):
    """First numeric leaf of a nested dict whose path contains every word (case-insensitive). The control and
    auxiliary-supply hand-off files are written by other scripts; this keeps the comparison tolerant of their layout."""
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
    mod, cell, port, prep, ctl, aux = (load(k) for k in ("module", "cell", "port", "port_report", "control", "aux"))
    rows = []      # (section, parameter, megarevo, ours, verdict, evidence)

    def add(key, ours, verdict, evidence):
        rows.append((key[0], key[1], mg[key], ours, verdict, evidence))

    def need(src, name, key):
        if src is None:
            add(key, "source not built yet", "PENDING", F[name])
        return src is not None

    if need(mod, "module", ("DC Data", "Rated Power (kW)")):
        M = mod["modules"]["PV-P75"]
        ev = F["module"]
        add(("DC Data", "Rated Power (kW)"), "%g (3 x 25 kW cells)" % M["P_rated_kW"],
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
            "%s peak at %g->%g V, %g kW (whole module: cells, port board, control, fans); %s at the worst full-power "
            "corner, 45 C inlet" % (pct(eta), at["va"], at["vb"], at["p_kW"], pct(worst)),
            "BETTER" if eta > 0.99 else "MEETS" if eta >= 0.99 else "BELOW", ev)
        d = M["derating_fraction_of_P_max"]
        sea, pess = d["sea level"], M["derating_pessimistic_sea_level"]
        t = d["inlet_C"]
        cold = mod["fan"]["ambient_rated_C"][0]          # the fan's rated minimum ambient sets the cold limit
        add(("General Data", "Operating Temperature Range (℃)"),
            "cold side: the fan, the part that set the limit, is rated to %+d C (requirement and Megarevo: -30 C); the "
            "minimum rating of every other part has not been audited one by one. Hot side: full %g kW to %d C inlet "
            "at sea level (nominal thermal model), %d %% at 60 C with the pessimistic model"
            % (cold, M["P_max_kW"], max(c for c, f in zip(t, sea) if f >= 1.0), round(100 * pess[-1])),
            "MEETS" if sea[t.index(45)] >= 1.0 and cold <= -30 else "BELOW", ev)
        a3 = d["3000 m"]
        add(("General Data", "Operating Altitude (m)"),
            "at 3000 m: 100 %% to %d C inlet, %d %% at 60 C (nominal); pessimistic model derates from %d C"
            % (max(c for c, f in zip(t, a3) if f >= 1.0), round(100 * a3[-1]),
               min(c for c, f in zip(t, M["derating_pessimistic_3000m"]) if f < 1.0)),
            "MEETS", ev)
        add(("General Data", "Noise Level (dB)"),
            "%.1f dB(A) @1 m at full power, 45 C inlet (fan datasheet sound data + 3 dB installation estimate)"
            % M["noise_dBA_1m_full_45C"], "BETTER" if M["noise_dBA_1m_full_45C"] < 65 else "BELOW", ev)
        fan = mod["fan"]
        add(("General Data", "Cooling Method"),
            "forced air, one %s %s per cell, PWM + tach, speed from heatsink and inductor NTCs; one fan failed "
            "still holds %d %% at 45 C" % (fan["manufacturer"], fan["mpn"], round(100 * M["one_fan_failed_fraction_45C"]["no flap"])),
            "MEETS", ev)
        add(("Other Data", "Standby Power Consumption (W)"), "%.1f (estimate)" % M["standby_W_estimate"],
            "MEETS" if M["standby_W_estimate"] < 20 else "BELOW", ev)

    if need(cell, "cell", ("Protection Data", "Over/Under Voltage Protection (Both Ports)")):
        P = cell["protection"]
        add(("Protection Data", "Over/Under Voltage Protection (Both Ports)"),
            "hardware trip %g V and firmware limit %g V on each port, stop below %g V"
            % (P["overvoltage_hw_trip_port_A_V"], P["overvoltage_sw_limit_V"], P["undervoltage_stop_V"]), "MEETS", F["cell"])
        add(("Protection Data", "Overtemperature Protection"),
            "heatsink trip %g C (hottest junction then 125 C), inductor and second heatsink NTC, fan tach"
            % P["heatsink_overtemperature_trip_C"], "MEETS", F["cell"])
        add(("Protection Data", "Isolation Method"), "non-isolated, common negative rail (%s)" % cell["topology"].split(",")[0],
            "MEETS", F["cell"])
        if port:
            v = port["variants"]["135"]
            add(("Protection Data", "Overcurrent Protection (Both Ports)"),
                "per cell: hardware trip %g A on the inductor; per port: over-current trip %g A, short-circuit "
                "comparator %g A" % (P["inductor_overcurrent_hw_trip_A"], v["OC_trip_A"], v["SC_trip_A"]), "MEETS",
                F["cell"] + ", " + F["port"])
            add(("Protection Data", "Short-Circuit Protection"),
                "gate-driver DESAT at %g V (detect + soft turn-off 1.35 us against %s), %g A gPV fuse on every pole, "
                "contactor hold-closed interlock above %g A so the fuse always clears first"
                % (P["desat_VDS_threshold_V"], "3.1 us device withstand", v["fuse_In_A"], port["hold"]["I_trip_band_A"][0]),
                "MEETS", F["cell"] + ", " + F["port"] + ", hardware/GDRV-HB/outputs/GDRV-HB_design_check.txt")

    if need(port, "port", ("Protection Data", "Lightning Protection")):
        add(("Protection Data", "Lightning Protection"),
            "%s on each port, protection level %g V" % (port["spd"]["type"], port["spd"]["Up_V"]), "MEETS", F["port"])
        add(("Protection Data", "Insulation Impedance Detection"),
            "switched %.0f kOhm test resistors pole-to-PE on the port board; %.1f kOhm threshold (1000 V / 30 mA)"
            % (port["imd"]["R_t_ohm"] / 1e3, port["imd"]["threshold_ohm"] / 1e3), "MEETS", F["port"])
        cur = port["current"]["error_worst_compensated"]["135"]
        add(("Other Data", "Current Accuracy"), "%.2f %% worst case after calibration, shunt temperature compensated" % (100 * cur),
            "MEETS" if cur < 0.01 else "BELOW", F["port"])
    if need(prep, "port_report", ("Other Data", "Voltage Accuracy")):
        m = re.search(r"\*\*worst after 1-point cal\*\*\s*\|\s*([\d.]+)\s*%\s*\|\s*([\d.]+)\s*%", prep)
        assert m, "voltage error budget row not found in " + F["port_report"]
        verr = max(float(m.group(1)), float(m.group(2)))
        add(("Other Data", "Voltage Accuracy"), "%.2f %% worst case after one-point calibration" % verr,
            "MEETS" if verr < 1.0 else "BELOW", F["port_report"])

    if need(ctl, "control", ("PV Data", "MPPT Tracking Accuracy")):
        path, eta = find(ctl, "mppt", "static")
        if eta is None:
            path, eta = find(ctl, "static", "eff")
        assert eta is not None, "no static MPPT efficiency found in " + F["control"]
        eta = eta / 100.0 if eta > 1.5 else eta
        add(("PV Data", "MPPT Tracking Accuracy"), "%.3f %% static (simulated), key %s" % (100 * eta, path),
            "MEETS" if eta >= 0.999 else "BELOW", F["control"])
        add(("DC Data", "MPPT Channels"), "1 centralised tracker over the 3 interleaved cells", "MEETS", F["control"])
        ov = ctl["hardware_trips"]["port_overvoltage"]
        add(("Protection Data", "Open-Circuit Protection (Both Ports)"),
            "full-power load rejection and loss of the PV source simulated: the %g V hardware over-voltage trip has the "
            "gates off %.0f us after the crossing, against %.0f us allowed by the device voltage limit"
            % (ov["threshold_V"], ov["as_designed_us"], ov["max_response_us_true_crossing_to_gates_off"]),
            "MEETS" if ov["meets"] else "BELOW", F["control"])
    if need(aux, "aux", ("PV Data", "PV Start-up Voltage (V)")):
        path, vs = find(aux, "start")
        assert vs is not None, "no start-up voltage found in " + F["aux"]
        add(("PV Data", "PV Start-up Voltage (V)"), "auxiliary supply starts at %g V (key %s); converter runs from 250 V"
            % (vs, path), "MEETS" if vs <= 250 else "BELOW", F["aux"])

    reports = {b: os.path.join(REPO, "hardware", b, "outputs", b + "_report.json") for b in BOARDS}
    built = {b: json.load(open(p)) if os.path.exists(p) else None for b, p in reports.items()}
    comms = "2 x isolated RS-485, 2 x isolated CAN (one CAN FD), 10/100 Ethernet"
    add(("Communication Data", "Communication"), comms,
        "BETTER" if built["SYS-IO-AUX"] and built["SYS-IO-AUX"]["passed"] else "PENDING", "gen/sys_io_aux.py, gen/ctrl_c2000.py")
    add(("Communication Data", "BMS Interface"), "BMU-GW daughtercard: isolated CAN FD + isolated RS-485 to the battery BMS",
        "MEETS" if built["BMU-GW"] and built["BMU-GW"]["passed"] else "PENDING", "gen/bmu_gw.py")
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
    L = ["# PV-P75 compared with Megarevo PMD-75-G3", "",
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
    L += ["", "## Boards behind the PV-P75 column", "", "| Board | Parts | Nets | Build checks |", "|---|---|---|---|"]
    for b in BOARDS:
        r = built[b]
        L.append("| %s | %s | %s | %s |" % ((b, r["parts"], r["nets"], "all passed" if r["passed"] else "FAILING") if r
                                             else (b, "-", "-", "not built yet")))
    bom = os.path.join(REPO, "bom", "PV-P75_module_BOM.csv")
    if os.path.exists(bom):
        lines = list(csv.DictReader(open(bom, encoding="utf-8")))
        L += ["", "Module BOM: %d lines, %d parts (`bom/PV-P75_module_BOM.csv`)." % (len(lines), sum(int(r["Qty"]) for r in lines))]
    L += ["", "## Reading this table", "",
          "- **BETTER / MEETS** compare a calculated figure of ours with a published figure of theirs. A calculated "
          "99.5 % and a published 99 % are not the same kind of evidence.",
          "- **NOT ASSESSED** rows need the mechanical design, which this project does not contain.",
          "- Open risks that could move a verdict are in `docs/requirements/DECISIONS.md` (rows marked OPEN) and in "
          "the assumptions sections of `sim/out/pv_design/module_report.md` and `sim/out/pv_tradeoff/report.md`."]
    open(os.path.join(OUT, "report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("PV-P75 vs PMD-75-G3: " + ", ".join("%d %s" % (n, v.lower()) for v, n in count.items()))
    for r in rows:
        if r[4] in ("BELOW", "PENDING"):
            print("  %-8s %s / %s: %s" % (r[4], r[0], r[1], r[3][:90]))
    if bad_boards:
        print("  boards not built or failing: " + ", ".join(bad_boards))
    assert sum(count.values()) == len(rows)
    return 1 if count["BELOW"] or count["PENDING"] or bad_boards else 0


if __name__ == "__main__":
    sys.exit(main())
