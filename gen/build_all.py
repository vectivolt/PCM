"""Build every board, then roll the board BOMs up into one BOM per module.

Usage: .venv/bin/python gen/build_all.py          # build all boards, then the module BOMs
       .venv/bin/python gen/build_all.py --bom    # only re-roll the module BOMs from bom/*_BOM.csv
Exit code 0 only if every board passed every check and every module BOM could be rolled up.
"""
import csv
import json
import os
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))

BOARDS = {   # KiCad project -> generator script (a script may build variants: pv_power -> PV-PWR + PV-PWR-4)
    "PV-PWR": "pv_power.py", "PV-CTL": "pv_ctrl.py",                                     # cost-first PV module (D-044)
    "PCS-PWR": "pcs_power.py",                       # PCS-P125 power board (D-060); module "PCS-P125" once its control board exists
    "BMU-GW": "bmu_gw.py", "CTRL-C2000": "ctrl_c2000.py", "SYS-IO-AUX": "sys_io_aux.py", "GDRV-HB": "gdrv.py",
    "AUX-HV": "aux_hv.py", "PV-PORT": "port.py", "PVCELL-25": "pvcell.py"}               # the roadmap's full platform
# DAB60 (gen/dab60.py) is frozen at its rev B outputs: the generator predates gate drive rev 5 and the re-run DAB
# spec and does not build against them; the DAB is to be redrawn cost-first (ARCHITECTURE-COSTFIRST.md section 16).
FROZEN = {"DAB60": "rev B outputs kept; generator not maintained (superseded by the cost-first DAB, not yet drawn)"}
VARIANTS = {"PV-PWR": ["PV-PWR-4"], "PV-PORT": ["PV-PORT-180"]}                          # extra projects a script builds
COMMON = {"CTRL-C2000": 1, "SYS-IO-AUX": 1, "AUX-HV": 1, "BMU-GW": 1}
MODULES = {  # module -> {board: quantity}
    # the product baseline: cost-first architecture (decision D-044): one power board + one control board
    "PV-P75": {"PV-PWR": 1, "PV-CTL-P75": 1},     # control board with the phase-4 comparators not fitted
    "PV-P100-110": {"PV-PWR-4": 1, "PV-CTL": 1},                     # P100 and P110 differ in rating only
    # the roadmap's full-featured platform, kept as reference (REQUIREMENTS.md PV-15, DAB-02)
    "PV-P75-FULL": dict(COMMON, **{"PVCELL-25": 3, "PV-PORT": 1}),
    "PV-P100-110-FULL": dict(COMMON, **{"PVCELL-25": 4, "PV-PORT-180": 1}),
    # the DAB feeds AUX-HV from port 1 only (decision D-021): same board, port-B input parts not fitted
    "DAB-D60-FULL": {"CTRL-C2000": 1, "SYS-IO-AUX": 1, "AUX-HV_DAB": 1, "BMU-GW": 1, "DAB60": 1},
}
KEY = ["Value", "Description", "Manufacturer", "MPN", "Package", "Sourcing", "Datasheet"]
CELLS = {"PV-P75-FULL": 3, "PV-P100-110-FULL": 4}
PHASES = {"PV-P75": 3, "PV-P100-110": 4}


def extras(module):
    """Chassis items that sit on no board: [(quantity, Value, Description, Manufacturer, MPN, Package, Sourcing,
    Datasheet)]. The fan comes from the thermal design's own output, so the BOM cannot drift from it."""
    if module in PHASES:      # cost-first module: everything else is on the two boards' BOMs (chassis parts included)
        n = PHASES[module]
        rows = {r["part"]: r for r in csv.DictReader(open(os.path.join(REPO, "gen", "data", "costfirst_bom.csv"),
                                                          encoding="utf-8")) if r["block"] in ("THERMAL", "MECH-ELEC")}
        fan = [k for k in rows if "FB1224" in k][0]            # the architecture's fan (rated -10 C: an open risk)
        return [
            (n, fan, "Fan 120 x 120 x 38 mm 24 V with PWM input and tach, one per phase section - RATED -10..+60 C "
             "ONLY: below PV-20's -30 C (open risk, ARCHITECTURE-COSTFIRST.md section 13)", rows[fan]["maker"], fan,
             "chassis", "ORDERABLE", "docs/datasheets/thermal/AFB1224SHE-F00.pdf"),
            (n, "120 mm wire guard", "Finger guard for a 120 mm fan", "", "", "chassis", "GENERIC", ""),
            (4, "busbar (CUSTOM)", "Copper busbar, terminal to board, sized for %d A" % (45 * n), "", "", "chassis",
             "CUSTOM", ""),
            (1, "harness set", "SELV supply lead, coil leads, NTC probe leads, fan leads", "", "", "harness", "CUSTOM", ""),
            (10 if n == 3 else 12, "PA66 standoff", "Board standoff, insulating", "", "", "chassis", "GENERIC", ""),
        ]
    if module in CELLS:
        n = CELLS[module]
        spec = os.path.join(REPO, "sim", "out", "pv_design", "module_spec.json")
        fan = json.load(open(spec))["fan"]
        return [
            (n * fan["count_per_cell"], fan["mpn"], "Fan 119 x 119 x 38 mm 24 V, ordered with PWM speed input and tach output "
             "(options of this type) - one per cell, %g W" % fan["rated_W"], fan["manufacturer"], fan["mpn"], "chassis",
             "RFQ", fan["datasheet"]),
            (n, "heatsink", "Plate-fin aluminium extrusion per cell, 150 x 300 mm base, 30 fins x 60 mm, insulated "
             "TO-247 mounting - geometry in sim/out/pv_design/report.md section 3", "", "", "chassis", "CUSTOM", ""),
            (n, "backflow shutter", "Gravity backflow shutter for a 120 mm fan (one-fan-failed case, "
             "sim/out/pv_design/module_report.md)", "", "", "chassis", "CUSTOM", ""),
            (n, "cell harness", "40-way 2.54 mm IDC ribbon, CTRL-C2000 cell port to PVCELL-25, 1:1", "", "", "harness",
             "CUSTOM", ""),
            (1, "port harness", "26-way 2.54 mm IDC ribbon, CTRL-C2000 to PV-PORT, 1:1", "", "", "harness", "CUSTOM", ""),
            (1, "coil/feedback harness", "PV-PORT control connector to the SYS-IO-AUX DO/DI terminals "
             "(gen/interfaces.py DO, DI)", "", "", "harness", "CUSTOM", ""),
            (1, "busbar set", "A_BUS+, B_BUS+, BUS- between PV-PORT and the cells, sized for %d A" % (45 * n), "", "",
             "chassis", "CUSTOM", ""),
            (1, "J801 link plug", "4-pole 3.81 mm plug with a wire link 1-2 for the SYS-IO-AUX spare trip loop J801: "
             "the PV module has no coolant loop, and safety channel A cannot arm with the loop open", "", "", "harness",
             "CUSTOM", ""),
            (1, "AUX-HV input harness", "Port A and port B terminal-side taps (after the port fuses) to AUX-HV PA+/PA- "
             "and PB+/PB-, 1.5 kV-rated wire", "", "", "harness", "CUSTOM", ""),
            (1, "AUX-HV output harness", "AUX-HV to SYS-IO-AUX, 3-way (gen/interfaces.py AUX)", "", "", "harness",
             "CUSTOM", ""),
        ]
    return [
        (1, "cold plate", "Liquid cold plate for two CBB011M12GM4T modules, transformer and series inductor; "
         "0.045 K/W per switch position at 9 L/min - sim/out/dab_design/report.md section 12", "", "", "chassis",
         "CUSTOM", ""),
        (2, "cell harness", "40-way 2.54 mm IDC ribbon, CTRL-C2000 ports C1 and C2 to the DAB60 bridges, 1:1", "", "",
         "harness", "CUSTOM", ""),
        (1, "port harness", "26-way 2.54 mm IDC ribbon, CTRL-C2000 to DAB60, 1:1", "", "", "harness", "CUSTOM", ""),
        (1, "coil/feedback harness", "DAB60 control connector to the SYS-IO-AUX DO/DI terminals", "", "", "harness",
         "CUSTOM", ""),
        (1, "AUX-HV input harness", "PORT 1 ONLY: port-1 terminal-side tap to AUX-HV PA+/PA-, 1.5 kV-rated wire. "
         "AUX-HV input PB is left open - feeding it from port 2 as well would bridge the DAB's isolation barrier "
         "with two diodes (decision D-021)", "", "", "harness", "CUSTOM", ""),
        (1, "AUX-HV output harness", "AUX-HV to SYS-IO-AUX, 3-way (gen/interfaces.py AUX)", "", "", "harness", "CUSTOM",
         ""),
    ]


def build_boards():
    ok = True
    for project, why in FROZEN.items():
        print("%-12s FROZEN  %s" % (project, why))
    for project, script in BOARDS.items():
        r = subprocess.run([sys.executable, os.path.join(HERE, script)], capture_output=True, text=True, cwd=REPO)
        passed = r.returncode == 0 and all((v + ": ALL CHECKS PASSED") in r.stdout
                                           for v in [project] + VARIANTS.get(project, []))
        ok &= passed
        print("%-12s %s" % (project, "PASS" if passed else "FAIL"))
        if not passed:
            print("  " + "\n  ".join((r.stdout + r.stderr).strip().splitlines()[-8:]))
    return ok


def roll_up(module, boards):
    """Sum the fitted (non-DNP) lines of each board BOM times its quantity; identical parts merge into one line."""
    kept = os.path.join(REPO, "bom", module + "_module_BOM.csv")
    if module in CELLS and os.path.exists(kept):    # frozen platform: its fan came from the earlier thermal run,
        print("%-12s frozen: %s kept" % (module, os.path.relpath(kept, REPO)))    # which module_spec.json no longer holds
        return True
    lines = defaultdict(lambda: defaultdict(int))
    for board, n in boards.items():
        path = os.path.join(REPO, "bom", board + "_BOM.csv")
        if not os.path.exists(path):
            print("%-12s MISSING %s" % (module, os.path.relpath(path, REPO)))
            return False
        for row in csv.DictReader(open(path, encoding="utf-8")):
            if not row["DNP"]:
                lines[tuple(row[k] for k in KEY)][board] += int(row["Qty"]) * n
    for qty, *key in extras(module):
        lines[tuple(key)]["chassis"] += qty
    out = os.path.join(REPO, "bom", module + "_module_BOM.csv")
    rows = sorted(lines.items(), key=lambda kv: (kv[0][5], kv[0][2], kv[0][3], kv[0][0]))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Item", "Qty", "Used on"] + KEY)
        for i, (key, per) in enumerate(rows, 1):
            w.writerow([i, sum(per.values()), "; ".join("%s: %d" % kv for kv in sorted(per.items()))] + list(key))
    total = sum(sum(per.values()) for per in lines.values())
    assert total == sum(q for q, *_ in extras(module)) + sum(
        n * sum(int(r["Qty"]) for r in csv.DictReader(open(os.path.join(REPO, "bom", b + "_BOM.csv"), encoding="utf-8"))
                if not r["DNP"]) for b, n in boards.items()), "roll-up lost parts"
    by_src = defaultdict(int)
    for key, per in lines.items():
        by_src[key[5]] += sum(per.values())
    print("%-12s %d lines, %d parts (%s) -> %s" % (module, len(rows), total,
          ", ".join("%s %d" % kv for kv in sorted(by_src.items())), os.path.relpath(out, REPO)))
    return True


def summary():
    """One line per board from its build report - the same numbers the README table shows."""
    for project in [x for k in BOARDS for x in [k] + VARIANTS.get(k, [])] + list(FROZEN):
        p = os.path.join(REPO, "hardware", project, "outputs", project + "_report.json")
        if os.path.exists(p):
            r = json.load(open(p))
            print("%-12s rev %s  %2d sheets  %4d parts  %4d nets  %s" % (project, r["rev"], r["sheets"], r["parts"],
                  r["nets"], "passed" if r["passed"] else "FAILED"))


if __name__ == "__main__":
    ok = True if "--bom" in sys.argv else build_boards()
    ok &= all([roll_up(m, b) for m, b in MODULES.items()])
    summary()
    sys.exit(0 if ok else 1)
