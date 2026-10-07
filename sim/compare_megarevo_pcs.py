"""PCS-P125 (three-wire, 3W+PE) and PCS-P125-4W (four-wire, 3W+N+PE) against the Megarevo PMA0125, row by row.

The inverter counterpart of sim/compare_megarevo.py. Every "ours" value is read from a file another script wrote (power-stage
design hand-off JSON, control-study JSON, the board design checks, the module BOMs, the cost table) - never typed here - so the
comparison cannot drift from the design. Megarevo's column is the published table saved in
docs/reference-designs/megarevo/pma/spec-pma0125.md (its PMA0125 column; PMA0135 is ignored). Ours is CALCULATED / SIMULATED,
theirs is PUBLISHED - neither column is a measurement of ours.

Two assemblies of one module: PCS-P125 (PCS-PWR + PCS-CTL-3W, grid type 3W+PE) and PCS-P125-4W (PCS-PWR-4W + PCS-CTL-4W, grid type
3W+N+PE). One verdict per published row: the better of the two assemblies where the row applies to both; a row only the four-wire
build can meet says so in the three-wire cell. A row with two sub-windows (the DC voltage ranges, one per grid type) takes the
worst sub-window, each sub-window the best assembly that covers it.

The loader fails closed: a missing key, or a sentence a generator no longer writes, raises with the file and the key; board design
checks built from another pcs_spec.json / port_spec.json raise. A missing source FILE makes its rows PENDING (as in the PV script).

Usage: .venv/bin/python sim/compare_megarevo_pcs.py   ->  sim/out/compare_megarevo_pcs/report.md + comparison.csv
Exit code 1 while a source file is missing, a row has no computed counterpart (PENDING), a board build is failing, or a row is BELOW.
"""
import csv
import functools
import hashlib
import json
import math
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(REPO, "sim", "out", "compare_megarevo_pcs")
SPEC = "docs/reference-designs/megarevo/pma/spec-pma0125.md"
COLUMN = "PMA0125"
F = {"spec": "sim/out/pcs_design/pcs_spec.json", "design": "sim/out/pcs_design/report.md",
     "ctrl": "sim/out/pcs_control/pcs_control_spec.json", "ctrl_md": "sim/out/pcs_control/report.md", "port": "sim/out/port_design/port_spec.json",
     "pwr": "hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt", "pwr4": "hardware/PCS-PWR-4W/outputs/PCS-PWR-4W_design_check.txt",
     "ctl": "hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt", "bom": "bom/PCS-P125_module_BOM.csv",
     "bom4": "bom/PCS-P125-4W_module_BOM.csv", "cost": "bom/COST.md", "pv": "sim/out/pv_design/module_spec.json",
     "audit": "gen/data/temp_audit.csv"}
BOARDS = [("PCS-PWR", "PCS-P125"), ("PCS-PWR-4W", "PCS-P125-4W"), ("PCS-CTL", "both (assemblies PCS-CTL-3W / PCS-CTL-4W)")]
DC, ON, OFF, COM, GEN = "DC data", "AC data (on-grid)", "AC data (off-grid)", "Communication parameters", "General"
RANK = {"BETTER": 4, "MEETS": 3, "PENDING": 2, "BELOW": 1, "NOT ASSESSED": 0}
NA = "not assessed: mechanics and enclosure are outside this project's scope (schematic + BOM + simulation)"
S, MG, ROWS = {}, {}, []      # loaded sources, the published table, the row builders


# ----------------------------------------------------------------------------------------- published table and source access
def megarevo():
    """{(section, parameter): value} of the PMA0125 column of the saved published table, in table order."""
    rows, section, col = {}, "", None
    for line in open(os.path.join(REPO, SPEC), encoding="utf-8"):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        if cells[0] == "Parameter":
            assert COLUMN in cells, "no %s column in %s" % (COLUMN, SPEC)
            col = cells.index(COLUMN)
        elif cells[0].startswith("**"):
            section = cells[0].strip("* ")
        else:
            assert col is not None, "published table header not found in " + SPEC
            rows[(section, cells[0])] = cells[col]
    assert len(rows) >= 50, "published table not found in " + SPEC
    return rows


def load(key):
    p = os.path.join(REPO, F[key])
    if not os.path.exists(p):
        return None
    if p.endswith(".json"):
        return json.load(open(p, encoding="utf-8"))
    if p.endswith(".csv"):
        return list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    return open(p, encoding="utf-8").read()


def K(name, d, *path):
    """d[path...]; a missing key stops the run naming the file and the full path (fail closed)."""
    x = d
    for p in path:
        try:
            x = x[p]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("%s: key %s missing at '%s' - rebuild that file or update sim/compare_megarevo_pcs.py"
                               % (F[name], "/".join(map(str, path)), p)) from None
    return x


def grab(name, pattern, text=None):
    """groups of the first match of pattern in a text source; a sentence the generator no longer writes stops the run."""
    m = re.search(pattern, S[name] if text is None else text)
    if not m:
        raise RuntimeError("%s: expected text not found (regex %r) - the generator changed its wording: update "
                           "sim/compare_megarevo_pcs.py" % (F[name], pattern))
    return m.groups()


def named(name, d, prefix, where):
    """the value of the first key of d that starts with prefix (the generators name some keys with their assumptions)."""
    for k, v in d.items():
        if k.startswith(prefix):
            return v
    raise RuntimeError("%s: no key starting '%s' in %s - rebuild that file or update sim/compare_megarevo_pcs.py" % (F[name], prefix, where))


def pubv(section, prefix):
    hit = [v for (s, p), v in MG.items() if s == section and p.startswith(prefix)]
    assert len(hit) == 1, "%s / %s matches %d published rows" % (section, prefix, len(hit))
    return hit[0]


def nums(s):
    return [float(x.replace(",", "")) for x in re.findall(r"[-+]?\d[\d,]*\.?\d*", s)]


def reaches(ours, pub):
    """ours >= the published figure, compared at the published figure's own precision (the sheet prints 150 kVA for
    216 A x 400 V x sqrt 3 = 149.6 kVA)."""
    m = re.search(r"\d\.(\d+)", pub)
    return round(ours, len(m.group(1)) if m else 0) >= nums(pub)[0]


def windows(s):
    w = {m.group(3): (float(m.group(1)), float(m.group(2))) for m in re.finditer(r"(\d+)~(\d+)\(([^)]+)\)", s)}
    assert len(w) == 2, "published window row not understood: " + s
    return w


def at(pts, y):
    """x at which the piecewise-linear curve through the sorted (x, y) points reaches y; None outside the points."""
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if y0 <= y <= y1:
            return x0 + (x1 - x0) * (y - y0) / (y1 - y0)
    return None


def worst(*v):
    return min(v, key=RANK.get)


def best(*v):
    return max(v, key=RANK.get)


def E(*names):
    return ", ".join(F[n] for n in names)


def row(section, prefix, *src):
    """register the builder of the published row (section, parameter starting prefix); src = the source files it reads."""
    def deco(fn):
        ROWS.append((section, prefix, src, fn))
        return fn
    return deco


# ------------------------------------------------------------------------------------------- what the rows read from the sources
def vac():
    return nums(pubv(ON, "Rated AC voltage"))[0]


@functools.cache
def tiers():
    """name -> (A rms, kVA at the rated AC voltage): amperes from the filter's current table, kVA from the declarations."""
    cur = K("spec", S["spec"], "inductors", "L1", "current", "fundamental_rms_A")
    kva = K("spec", S["spec"], "declarations", "kva_tiers_400V")
    out = {}
    for name in ("rated", "continuous", "2_min", "200_ms"):
        a = K("spec", cur, name)
        hit = [v for k, v in kva.items() if abs(float(grab("spec", r"([\d.]+) A$", k)[0]) - a) < 0.05]
        assert len(hit) == 1, "%s: no kVA tier for %g A in declarations/kva_tiers_400V" % (F["spec"], a)
        assert abs(hit[0] - math.sqrt(3) * vac() * a / 1e3) < 0.05, "%s: kVA tier of %g A is not sqrt 3 x %g V x A" % (F["spec"], a, vac())
        out[name] = (a, hit[0])
    return out


@functools.cache
def adm():
    return K("spec", S["spec"], "operating_map", "admissible")


@functools.cache
def dcport():
    """the DC port as the power board's check states it."""
    m = grab("pwr", r"HFE82V-300C (\d+) A at 85 C vs (\d+) A port \(largest study DC current (\d+) A\)[^\n]*?"
                    r"(\d+) A continuous = (\d+) % of In \(<= (\d+) %\)")
    d = dict(zip(("contactor", "port", "study", "cont", "use", "rule"), map(float, m)))
    d["fuse"] = float(grab("pwr", r"HCHVF1000-(\d+)A-\d+R per pole")[0])
    shunt = grab("spec", r"([\d.]+) mV at ([\d.]+) A", K("spec", S["spec"], "ports_and_common_mode", "dc_port", "shunt"))
    assert float(shunt[1]) == d["port"], "%s and %s disagree on the DC port current" % (F["spec"], F["pwr"])
    d["mV"] = float(shunt[0])
    d["oc"] = K("spec", S["spec"], "ports_and_common_mode", "dc_port", "oc_trip_A")
    d["hold"] = K("spec", S["spec"], "ports_and_common_mode", "dc_port", "hold_off_threshold_A")
    return d


@functools.cache
def win():
    """DC windows per grid type (calculated at the rated AC voltage); the competitor figures pcs_spec stored must equal the table's."""
    ow = K("spec", S["spec"], "declarations", "operating_windows")
    w = dict(pe=K("spec", ow, "3W+PE"), a=named("spec", ow, "3W+N+PE, mode A", "operating_windows"),
             b=named("spec", ow, "3W+N+PE, mode B", "operating_windows"),
             po=windows(pubv(DC, "Operating DC voltage range")), pf=windows(pubv(DC, "Full load DC voltage range")))
    for k, g in (("pe", "3W+PE"), ("a", "3W+N+PE"), ("b", "3W+N+PE")):
        if [float(x) for x in w[k]["competitor"]] != [*w["po"][g], *w["pf"][g]]:
            raise RuntimeError("%s: the competitor windows stored in declarations/operating_windows differ from %s" % (F["spec"], SPEC))
    # the four-wire baseline: mode A where the phase peak fits, mode B below it, so the window starts at the lower of the two
    w["lo4"], w["fl4"] = min(w["a"]["operating_from"], w["b"]["operating_from"]), min(w["a"]["full_load_from"], w["b"]["full_load_from"])
    cross = grab("pwr4", r"OPERATING WINDOWS PER GRID TYPE[^\n]*?3W\+PE: operating (\d+)-(\d+) V, full load (\d+)-(\d+) V")
    if [round(w["pe"][k]) for k in ("operating_from", "operating_to", "full_load_from", "full_load_to")] != [int(x) for x in cross]:
        raise RuntimeError("%s and %s disagree on the 3W+PE window" % (F["spec"], F["pwr4"]))
    return w


def grid_for(dc_lo, mode):
    """the grid voltage up to which a DC link of dc_lo volts still reaches the map's limit (interpolated between computed rows)."""
    pts = []
    for k, v in K("spec", S["spec"], "operating_map", "vdc_min_at_rated_current_V").items():
        ac = float(grab("spec", r"^(\d+) V 50 Hz$", k)[0]) if k.endswith("50 Hz") else None
        if ac:
            pts.append((ac, max(x for n, x in v.items() if n.startswith("PF 0, Q > 0")) if mode == "full"
                        else named("spec", v, "closing permissive", k)))
    return at(sorted(pts), dc_lo)


@functools.cache
def precision():
    sp = K("spec", S["spec"], "declarations", "stabilized_precision")
    vb, cb = named("spec", sp["voltage"], "b:", "stabilized_precision/voltage"), named("spec", sp["current"], "b:", "stabilized_precision/current")
    r = dict(rss_v=max(x["rss_pct"] for x in vb.values()), rss_i=max(x["rss_pct_of_rated"] for x in cb.values()),
             worst_v=K("spec", sp, "worst_b_max_pct", "voltage"), worst_i=K("spec", sp, "worst_b_max_pct", "current"),
             ok_v=K("spec", sp, "meets_rss_b", "voltage"), ok_i=K("spec", sp, "meets_rss_b", "current"),
             volts=" / ".join(vb), conv=next(k for k in sp["voltage"] if k.startswith("b:")))
    assert abs(r["worst_v"] - max(x["worst_pct"] for x in vb.values())) < 1e-6, "%s: worst_b_max_pct/voltage is not the table's maximum" % F["spec"]
    a = grab("ctl", r"ADC accuracy, this board's share[^\n]*?RSS ([\d.]+) %[^\n]*?worst ([\d.]+) %[^\n]*?mismatch ([\d.]+) % = ([\d.]+) %")
    r.update(zip(("adc_rss", "adc_worst", "tcr", "adc_total"), map(float, a)))
    return r


@functools.cache
def effic():
    e = K("spec", S["spec"], "thermal_and_losses", "efficiency")
    in_window = []                 # the map's inverter points at DC voltages the 3W+PE window reaches at the rated grid
    for k, curve in e["map"].items():
        d, v = grab("spec", r"^(inverter|rectifier) (\d+) V$", k)
        if d == "inverter" and float(v) >= win()["pe"]["operating_from"]:
            in_window += [(x, float(v), float(load)) for load, x in curve.items()]
    best_in = max(in_window)
    unc = float(grab("design", r"model uncertainty of about \+-([\d.]+)")[0])
    return dict(basis=grab("design", r"Efficiency \(calculated, ([^)]*)\)")[0], peak=100 * e["peak"], at=e["peak_at"],
                full750=100 * e["full_load_750V"], worst=100 * e["full_load_worst"],
                inwin=100 * best_in[0], inwin_v=best_in[1], inwin_load=best_in[2], unc=unc)


@functools.cache
def thermal():
    t = K("spec", S["spec"], "thermal_and_losses")
    pop = named("spec", K("spec", t, "sharing_population", "populations"), "acceptance rule", "sharing_population/populations")
    return dict(frac=K("spec", t, "derating_current_frac"), tj45=t["tj_110pct_45C"], tj60=t["tj_110pct_60C"],
                limit=K("spec", pop, "tiers", "60C 110 %", "limit_C"), lim2=K("spec", pop, "tiers", "45C 120 % 2 min", "limit_C"),
                lim200=K("spec", pop, "tiers", "45C 200 ms", "limit_C"), derated=K("spec", t, "sharing_population", "derated_60C_A"),
                tj2=max(o["tj_120pct_2min_C"] for o in K("spec", t, "overload")), tj200=max(o["tj_200ms_C"] for o in K("spec", t, "overload")),
                cfm=K("spec", S["spec"], "declarations", "airflow"), hs=K("spec", t, "heatsink"))


@functools.cache
def fourwire():
    fw = K("spec", S["spec"], "four_wire")
    ub, dl = K("spec", fw, "unbalance"), K("spec", fw, "dc_link")
    return dict(L_N=K("spec", fw, "filter", "L_N_uH"), C_fN=K("spec", fw, "filter", "C_fN_uF"), per_phase=ub["per_phase_kW_at_230V"],
                nleg=ub["n_leg"], tj_lim=ub["tj_limits_C"], cap_max=dl["per_cap_max_A"], cap_lim=dl["per_cap_limit_A"],
                lf750=max(c["battery_lf_A_rms"] for c in dl["cases"] if c["vdc"] == 750.0), half=K("spec", fw, "half_wave"),
                hf=K("spec", fw, "grid_hf"), rows=K("spec", fw, "window", "rows"))


@functools.cache
def thdi():
    des = [t for t in K("ctrl", S["ctrl"], "thd") if t["ctrl"].startswith("design (h1+h5+h7")]
    assert des, "%s: no THDi entries of the design controller" % F["ctrl"]
    per = {dt: max(t["thd_pct"] for t in des if t["dt"] == dt) for dt in {t["dt"] for t in des}}
    bg = K("ctrl", S["ctrl"], "assumptions", "bg_harm_pct")[0]
    return dict(worst=max(per.values()), nocomp=named("ctrl", per, "no compensation", "thd"), leg=named("ctrl", per, "compensated, identified", "thd"),
                pwm=100 * max(s["THD_all_pwm"] for s in K("spec", S["spec"], "lcl", "spectrum")),
                bg=" / ".join("%g" % bg[h] for h in ("5", "7", "11", "13")), scr=sorted({t["scr"] for t in des if t["scr"]}))


def has_key(obj, text):
    """True if any dict key anywhere in obj contains text (case-insensitive): a generator that starts to compute a figure names it."""
    if isinstance(obj, dict):
        return any(text in str(k).lower() or has_key(v, text) for k, v in obj.items())
    return isinstance(obj, list) and any(has_key(v, text) for v in obj)


def bom_row(name, mpn):
    hit = [r for r in S[name] if r["MPN"] == mpn]
    assert len(hit) == 1, "%s: %d lines with MPN %s (one expected)" % (F[name], len(hit), mpn)
    return hit[0]


@functools.cache
def fans():
    """(mpn, count three-wire, count four-wire, rated cold C, rated hot C): the fan named by the heatsink model, counted in the BOMs."""
    n, mpn = grab("spec", r"(\d+) x (\S+)", K("spec", S["spec"], "thermal_and_losses", "heatsink", "fans"))
    air = K("spec", S["spec"], "declarations", "airflow", "sections")
    c3, c4 = int(bom_row("bom", mpn)["Qty"]), int(bom_row("bom4", mpn)["Qty"])
    assert (c3, c4) == (air["three-wire"], air["four-wire"]) and int(n) == c3, "fan counts of the BOMs and pcs_spec differ"
    lo, hi = map(float, grab("bom", r"RATED (-?\d+)\.\.\+?(\d+) C", bom_row("bom", mpn)["Description"]))
    return mpn, c3, c4, lo, hi


@functools.cache
def clock_ppm():
    return float(grab("bom", r"\+/-(\d+) ppm", bom_row("bom", "X1G0041710033")["Description"])[0])


@functools.cache
def audit():
    """the rule of gen/temp_audit.py on the two module BOMs: orderable / RFQ parts with a data sheet against gen/data/temp_audit.csv."""
    rows = {(r["mfr"], r["mpn"]): r for r in S["audit"]}
    out = {}
    for n in ("bom", "bom4"):
        parts = {(r["Manufacturer"], r["MPN"] or r["Value"]) for r in S[n] if r["Sourcing"] in ("ORDERABLE", "RFQ") and r["Datasheet"].strip()}
        gone = [p for p in parts if p not in rows]
        out[n] = (len(parts), len(gone), any(p[1] == fans()[0] for p in gone))
    return out


@functools.cache
def mass_floor():
    """known masses only (L1, L2, CM choke, heatsink sections; the neutral inductor taken as an L1): a lower bound, not the weight."""
    sp = S["spec"]
    l1, l2 = (K("spec", sp, "inductors", x, "selected_part", "mass_kg") for x in ("L1", "L2"))
    cm, hs = K("spec", sp, "inductors", "AC_CM_choke", "mass"), K("spec", sp, "thermal_and_losses", "heatsink", "mass_kg_per_section")
    n = K("spec", sp, "declarations", "airflow", "sections")
    three = 3 * l1 + 3 * l2 + cm + n["three-wire"] * hs
    return three, three + l1 + (n["four-wire"] - n["three-wire"]) * hs


def bom_has(name, text):
    return any(text in r["Value"] for r in S[name])


def cost_rows():
    """module -> (catalogue USD, 5,000-unit USD, unpriced lines) from the summary table of bom/COST.md, columns found by name."""
    sec = S["cost"].split("## Summary", 1)[1].split("\n## ", 1)[0]
    head = next(l for l in sec.splitlines() if "BOM catalogue USD" in l)
    col = {c.strip(): i for i, c in enumerate(head.strip().strip("|").split("|"))}
    out = {}
    for line in sec.splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and c[0] in ("PCS-P125", "PCS-P125-4W"):
            out[c[0]] = (float(c[col["BOM catalogue USD"]]), float(c[col["BOM at 5000 units USD"]]), int(c[col["unpriced lines"]]))
    assert len(out) == 2, "%s: PCS-P125 / PCS-P125-4W rows not found in the summary table" % F["cost"]
    return out


# ------------------------------------------------------------------------------------------------------------------------ DC data
@row(DC, "Max. DC continuous power", "spec", "pwr", "pwr4")
def _(pub):
    p = max(r["P_kW"] for v in adm().values() for a in v.values() for f in a.values() for m, r in f.items() if m == "PF 1 inverter")
    (ca, ck), d, w, fw = tiers()["continuous"], dcport(), win(), fourwire()
    c3 = (f"{p:.1f} kW: the operating map's DC limit, reached at the 110 % tier ({ca:g} A x {vac():g} V x sqrt 3 = {ck:.1f} kVA); the DC port "
          f"carries {d['cont']:g} A continuous (aR link at {d['use']:g} % of In, rule <= {d['rule']:g} %) = "
          f"{d['cont'] * w['pe']['full_load_from'] / 1e3:.0f} kW at {w['pe']['full_load_from']:.0f} V, the lowest full-load voltage")
    v = "MEETS" if reaches(p, pub) else "BELOW"
    c4 = c3 + f"; the fourth leg adds no DC-side limit (DC-link capacitor ripple <= {fw['cap_max']:.1f} A against {fw['cap_lim']:.1f} A)"
    return v, c3, v, c4, E("spec", "pwr", "pwr4")


def window_row(kind):
    """kind 'operating' or 'full_load'; the verdict is the worse of the two grid types, each taken from the assembly that covers it."""
    w = win()
    pub = w["po"] if kind == "operating" else w["pf"]
    f4 = w["lo4"] if kind == "operating" else w["fl4"]
    pe, a, b = w["pe"], w["a"], w["b"]
    lo3, hi3 = pe[kind + "_from"], pe[kind + "_to"]
    v3 = "MEETS" if lo3 <= pub["3W+PE"][0] and hi3 >= pub["3W+PE"][1] else "BELOW"
    v4 = "MEETS" if f4 <= pub["3W+N+PE"][0] and b[kind + "_to"] >= pub["3W+N+PE"][1] else "BELOW"
    head = float(grab("design", r"without it they are ([\d.]+) % lower")[0]) / 100       # the map's 5 % loop headroom, as the report states it
    g = grid_for(pub["3W+PE"][0], "close" if kind == "operating" else "full")
    what = "the closing permissive" if kind == "operating" else "the DC voltage rated current needs at the worst power factor"
    near = (f"; a {pub['3W+PE'][0]:.0f} V DC link " + ("closes only onto grids up to" if kind == "operating" else "carries rated current only on grids up to")
            + f" {g:.0f} V AC (interpolated)") if g else ""
    c3 = (f"3W+PE: {lo3:.0f}~{hi3:.0f} V against {pub['3W+PE'][0]:.0f}~{pub['3W+PE'][1]:.0f} V ({lo3:.1f} V is {what} at {vac():g} V / 50 Hz, calculated; "
          f"without the map's loop headroom it would still be {lo3 * (1 - head):.0f} V){near}; 3W+N+PE: not available on this assembly (four-wire build)")
    miss = "above" if a[kind + "_from"] > pub["3W+N+PE"][0] else "within"
    c4 = (f"3W+N+PE: {f4:.0f}~{b[kind + '_to']:.0f} V against {pub['3W+N+PE'][0]:.0f}~{pub['3W+N+PE'][1]:.0f} V, {'met' if v4 == 'MEETS' else 'not met'}, with the neutral "
          f"leg in mode B (zero sequence on four legs: 150 Hz common-mode voltage to N / earth, installation limit on the battery's earth capacitance); mode A "
          f"(sinusoidal, no 150 Hz) starts at {a[kind + '_from']:.0f} V, {miss} the published {pub['3W+N+PE'][0]:.0f} V; the 3W+PE grid type is the "
          f"three-wire assembly's (PCS-P125 cell)")
    return v3, c3, v4, c4, E("spec", "pwr4", "design"), worst(v3, v4)


@row(DC, "Operating DC voltage range", "spec", "pwr4", "design")
def _(pub):
    return window_row("operating")


@row(DC, "Full load DC voltage range", "spec", "pwr4", "design")
def _(pub):
    return window_row("full_load")


@row(DC, "Max. DC current", "spec", "pwr")
def _(pub):
    d = dcport()
    c = (f"{d['port']:g} A port, both directions (inverter and rectifier maps): contactor {d['contactor']:g} A at 85 C, a {d['fuse']:g} A aR link in each pole, shunt "
         f"{d['mV']:g} mV at {d['port']:g} A, over-current trip {d['oc']:g} A, contactor hold-off {d['hold']:g} A; the largest DC current of the "
         f"operating map is {d['study']:g} A")
    v = "MEETS" if reaches(d["port"], pub) else "BELOW"
    return v, c, v, c, E("spec", "pwr")


@row(DC, "Max. DC continuous current", "spec", "pwr")
def _(pub):
    d = dcport()
    c = (f"{d['cont']:g} A continuous: the {d['fuse']:g} A aR link at {d['use']:g} % of In (rule <= {d['rule']:g} %), contactor {d['contactor']:g} A at 85 C; "
         f"the largest DC current of the operating map is {d['study']:g} A")
    v = "MEETS" if reaches(d["cont"], pub) else "BELOW"
    return v, c, v, c, E("spec", "pwr")


def accuracy(pub, what, rss, worst_, ok):
    """verdict and cell of a stabilization-accuracy row: met by RSS (the board checks' convention), the worst-case sum reported beside it."""
    p, bound = precision(), nums(pub)[0]
    c = (f"RSS {rss:.2f} % (worst of {p['volts']} V DC), worst-case linear sum {worst_:.2f} % - calculated through the measurement chain of the {what}, "
         f"convention {p['conv']}; the regulation itself adds nothing at DC; the control board's ADC share checks at RSS {p['adc_rss']:.2f} % / worst "
         f"{p['adc_worst']:.2f} % (+ {p['tcr']:.2f} % divider TCR = {p['adc_total']:.2f} %)")
    if worst_ > bound:
        c += " - met by RSS only: the worst-case sum is above the published figure (not guaranteed)"
    v = "MEETS" if ok and rss <= bound else "BELOW"
    return v, c, v, c, E("spec", "ctl")


@row(DC, "Voltage stabilization accuracy", "spec", "ctl")
def _(pub):
    p = precision()
    return accuracy(pub, "DC voltage", p["rss_v"], p["worst_v"], p["ok_v"])


@row(DC, "Current stabilization accuracy", "spec", "ctl")
def _(pub):
    p = precision()
    return accuracy(pub, "DC current, as % of the rated current at that voltage (= of rated power)", p["rss_i"], p["worst_i"], p["ok_i"])


# --------------------------------------------------------------------------------------------------------- AC data, on-grid
def tier_row(tier, unit, kind):
    """tier = rated / continuous / 2_min / 200_ms; unit 'kVA' or 'A'; kind 'on' or 'off' (the off-grid cell adds the load acceptance)."""
    def build(pub):
        t = tiers()
        a, s = t[tier]
        ours = s if unit == "kVA" else a
        label = {"rated": "the rated current", "continuous": "110 % continuous", "2_min": "120 % for 2 min", "200_ms": "200 ms"}[tier]
        lim = f"; the map's S limit is {K('spec', S['spec'], 'operating_map', 'S_max_kVA'):g} kVA" if tier == "2_min" and unit == "kVA" else ""
        c = f"{ours:.1f} kVA = {a:g} A at {vac():g} V ({label}{lim})" if unit == "kVA" else f"{a:g} A ({label}; {s:.1f} kVA at {vac():g} V)"
        if tier == "rated" and unit == "kVA":
            c = (f"{s:.1f} kVA at the {a:g} A rating and {vac():g} V; {nums(pub)[0]:g} kW needs "
                 f"{K('spec', S['spec'], 'operating_map', 'rated_power_at_400V_A'):.1f} A, inside the {t['continuous'][0]:g} A continuous tier")
        if tier == "2_min" and unit == "kVA":
            c += "; the published figure is itself this tier"
        if kind == "off":
            c += f"; resistive load: {K('spec', S['spec'], 'declarations', 'offgrid_load_acceptance', 'resistive')}"
        v = "MEETS" if reaches(ours, pub) else "BELOW"
        return v, c, v, c, E("spec")
    return build


for _sec, _kind in ((ON, "on"), (OFF, "off")):
    for _pfx, _tier, _unit in (("Rated AC power", "rated", "kVA"), ("Max. AC power", "2_min", "kVA"),
                               ("Max. AC continuous power", "continuous", "kVA"), ("Max. AC current", "2_min", "A"),
                               ("Max. AC continuous current", "continuous", "A")) + ((("Rated AC current", "rated", "A"),) if _kind == "on" else ()):
        row(_sec, _pfx, "spec")(tier_row(_tier, _unit, _kind))


@row(ON, "Grid type", "spec", "bom", "bom4")
def _(pub):
    fw = fourwire()
    assert bom_has("bom4", "AC terminal N") and not bom_has("bom", "AC terminal N"), "the BOMs no longer differ by the N terminal"
    assert "4-pole for four-wire" in K("spec", S["spec"], "ports_and_common_mode", "ac_contactor", "arrangement") and bom_has("bom4", "4P"), \
        "four-wire AC contactors are no longer four-pole in pcs_spec / the 4W BOM"
    c3 = "3W+PE only: no neutral terminal, N leg or fourth AC pole (module BOM); 3W+N+PE needs the four-wire assembly PCS-P125-4W"
    c4 = (f"3W+N+PE: four-pole AC contactors, N terminal in the BOM, neutral leg with L_N {fw['L_N']:.0f} uH and C_fN {fw['C_fN']:.0f} uF on its own "
          f"heatsink section; the C_f star stays on the DC midpoint")
    return "MEETS", c3, "MEETS", c4, E("spec", "bom", "bom4")


@row(ON, "Rated AC voltage", "spec")
def _(pub):
    ln = K("spec", S["spec"], "four_wire", "window", "rows", "nominal", "V_LN")
    ok = abs(ln / nums(pub)[1] - 1) < 0.005 and nums(pub)[0] == vac()
    v = "MEETS" if ok else "BELOW"
    return (v, f"{vac():g} V line-to-line (operating map); no neutral terminal, so no {nums(pub)[1]:g} V line-to-neutral on this assembly", v,
            f"{vac():g} V line-to-line / {ln:.1f} V line-to-neutral (the four-wire window table's nominal row)", E("spec"))


@row(ON, "THDi", "ctrl", "ctrl_md", "spec")
def _(pub):
    open_item("A switched (PWM, dead-time, ripple-sampling) simulation of the chosen loops and a THDi figure from it")
    t, fw = thdi(), fourwire()
    c3 = (f"<= {t['worst']:.2f} % worst (control-study estimate, SCR {min(t['scr']):g}..stiff: PR current loop, 1,750 Hz crossover with h5 / h7 terms, "
          f"510 ns dead time uncompensated, ASSUMED grid background {t['bg']} % at h5 / h7 / h11 / h13); {t['leg']:.2f} % with the dead time compensated per "
          f"leg; the PWM spectrum alone {t['pwm']:.2f} %; not a switched simulation")
    c4 = (c3 + f"; same phase legs and filter; the carrier group above h50 reaches {fw['hf']['950 V 4W stiff']['pct_of_rated']:.2f} % of the rated peak "
          f"on a stiff grid (three-wire {fw['hf']['950 V 3W stiff']['pct_of_rated']:.2f} %), the N-leg loop is not studied")
    v = "MEETS" if t["worst"] < nums(pub)[0] else "BELOW"
    return v, c3, v, c4, E("ctrl", "spec")


@row(ON, "Rated voltage/voltage range", "spec")
def _(pub):
    nom, pct = nums(pub)[:2]
    lo, hi = f"{nom * (1 - pct / 100):g} V 50 Hz", f"{nom * (1 + pct / 100):g} V 50 Hz"
    vm = K("spec", S["spec"], "operating_map", "vdc_min_at_rated_current_V")
    wlo, whi = (max(x for n, x in vm[k].items() if n.startswith("PF 0, Q > 0")) for k in (lo, hi))
    rows = fourwire()["rows"]
    fl, fh = rows[f"-{pct:g} %"], rows[f"+{pct:g} %"]
    c3 = (f"{nom * (1 - pct / 100):g}~{nom * (1 + pct / 100):g} V AC computed (operating map); rated current needs at least {wlo:.0f} V DC at "
          f"{nom * (1 - pct / 100):g} V AC and {whi:.0f} V DC at {nom * (1 + pct / 100):g} V AC (worst power factor): the +{pct:g} % end is reachable only "
          f"with a DC link above {whi:.0f} V")
    c4 = (f"{fl['V_LN']:.1f}~{fh['V_LN']:.1f} V line-to-neutral computed; rated current from {fl['mode_B']['PF 0, Q > 0 (over-excited, current lags)']:.0f} V "
          f"(-{pct:g} %) to {fh['mode_B']['PF 0, Q > 0 (over-excited, current lags)']:.0f} V DC (+{pct:g} %) in mode B, "
          f"{fl['mode_A']['PF 0, Q > 0 (over-excited, current lags)']:.0f} / {fh['mode_A']['PF 0, Q > 0 (over-excited, current lags)']:.0f} V in mode A")
    return "MEETS", c3, "MEETS", c4, E("spec")


@row(ON, "Rated frequency/frequency range", "spec", "ctrl")
def _(pub):
    clamp = K("ctrl", S["ctrl"], "assumptions", "pll_dw_max_Hz")[0]
    vm = K("spec", S["spec"], "operating_map", "vdc_min_at_rated_current_V")
    f1, f2 = nums(pub)[0], nums(pub)[2]
    both = all(f"{vac():g} V {f:g} Hz" in vm for f in (f1, f2))
    v = "MEETS" if both and clamp >= nums(pub)[1] else "BELOW"
    c = (f"the operating map is computed at {f1:g} and {f2:g} Hz only; the PLL integrator clamp is +/-{clamp:g} Hz "
         f"({f1 - clamp:g}~{f1 + clamp:g} / {f2 - clamp:g}~{f2 + clamp:g} Hz, control study); grid-code trip windows are firmware parameters "
         f"(EN 50549-1 class, FROM MEMORY)")
    return v, c, v, c, E("spec", "ctrl")


@row(ON, "Adjustable power factor range", "spec")
def _(pub):
    rec = K("spec", S["spec"], "operating_map", "admissible", "750", f"{vac():g}", "50 Hz")
    s = {m: math.hypot(r["P_kW"], r["Q_kvar"]) for m, r in rec.items()}
    top = max(s.values())
    ok = len(s) == 4 and min(s.values()) >= 0.99 * top
    qcf = K("spec", S["spec"], "lcl", "Qcf_var", f"{vac():g} V") / 1e3
    p = tiers()["rated"][1]
    c = (f"PF -1..+1 at full continuous current: at 750 V DC and {vac():g} V the inverter, rectifier, over-excited and under-excited corners of the "
         f"operating map are all {min(s.values()):.1f}-{top:.1f} kVA; the filter capacitors' {qcf:.2f} kvar alone leave PF {p / math.hypot(p, qcf):.4f} at "
         f"rated power before the current loop compensates it (grid current for PF = i_L1 - C_f dv/dt)")
    v = "MEETS" if ok and p / math.hypot(p, qcf) > nums(pub)[0] else "BELOW"
    return v, c, v, c, E("spec")


# -------------------------------------------------------------------------------------------------------- AC data, off-grid
@row(OFF, "Rated voltage", "spec")
def _(pub):
    ll = [float(x) for x in re.search(r"L-L:([\d/]+)", pub).group(1).split("/")]
    ln = [float(x) for x in re.search(r"L-N:([\d/]+)", pub).group(1).split("/")]
    vm = K("spec", S["spec"], "operating_map", "vdc_min_at_rated_current_V")
    acs = sorted(float(grab("spec", r"^(\d+) V", k)[0]) for k in vm if k.endswith("50 Hz"))
    inside = acs[0] <= min(ll) and max(ll) <= acs[-1]
    nxt = next(a for a in acs if a >= max(ll))
    need = max(x for n, x in vm[f"{nxt:g} V 50 Hz"].items() if n.startswith("PF 0, Q > 0"))
    v = "MEETS" if inside else "BELOW"
    c3 = (f"line-to-line {'/'.join(f'{x:g}' for x in ll)} V: set points inside the computed {acs[0]:g}~{acs[-1]:g} V range (rated current needs at most "
          f"{need:.0f} V DC up to {nxt:g} V, the next computed row; not computed at {min(ll):g} / {max(ll):g} V themselves); no neutral, so no "
          f"line-to-neutral {'/'.join(f'{x:g}' for x in ln)} V output")
    c4 = c3.split("; no neutral")[0] + f"; line-to-neutral {'/'.join(f'{x:g}' for x in ln)} V through the N leg (the same set points per phase)"
    return v, c3, v, c4, E("spec")


@row(OFF, "Rated frequency", "spec", "bom")
def _(pub):
    f1, f2 = nums(pub)[:2]
    vm = K("spec", S["spec"], "operating_map", "vdc_min_at_rated_current_V")
    ok = all(f"{vac():g} V {f:g} Hz" in vm for f in (f1, f2))
    v = "MEETS" if ok else "BELOW"
    c = f"{f1:g} and {f2:g} Hz both computed (operating map); the controller clock is a +/-{clock_ppm():g} ppm oscillator (module BOM)"
    return v, c, v, c, E("spec", "bom")


def open_item(phrase):
    """the control study's own list of what it does not do; the claim made in a cell must still be there."""
    grab("ctrl_md", re.escape(phrase))


@functools.cache
def vf():
    """the control study's VF (off-grid) results: the secondary restoration, the accuracy statements, THDu, imbalance."""
    c = S["ctrl"]
    return dict(sec=K("ctrl", c, "vf_secondary"), acc=K("ctrl", c, "vf_accuracy"), thdu=K("ctrl", c, "thdu"), imb=K("ctrl", c, "imbalance"))


def vf_steps():
    st = K("ctrl", vf()["sec"], "steps")
    return K("ctrl", st, "0 -> rated"), K("ctrl", st, "rated -> 0"), K("ctrl", vf()["sec"], "T_s")


@row(OFF, "Voltage accuracy", "ctrl", "ctl")
def _(pub):
    v, p = K("ctrl", vf()["acc"], "voltage"), precision()
    if (v["adc_rss_pct"], v["adc_worst_pct"], v["tcr_pct"]) != (p["adc_rss"], p["adc_worst"], p["tcr"]):
        raise RuntimeError("%s and %s disagree on the control board's sensing floor: re-run sim/pcs_control.py" % (F["ctrl"], F["ctl"]))
    up, dn, ts = vf_steps()
    c = (f"{v['bound_worst_pct']:.2f} % worst-case sum ({v['bound_rss_pct']:.2f} % RSS), calculated: regulation residual {v['residual_pct']:.3f} % "
         f"(VF secondary restoration, T {ts:g} s - zero by construction on the sensed C_f voltage; the L2 drop at rated load remains) + the control "
         f"board's sensing floor {v['sensing_worst_pct']:.2f} % (ADC share RSS {p['adc_rss']:.2f} % / worst {p['adc_worst']:.2f} % + {p['tcr']:.2f} % "
         f"divider TCR); steady state, no load to rated; dynamic (simulated, averaged model): a 0 -> rated step dips to {up['v_min']:.2f} pu and is "
         f"within 1 % after {up['t_v1_s']:.2f} s, a rated -> 0 step overshoots to {dn['v_max']:.2f} pu and is within 1 % after {dn['t_v1_s']:.2f} s")
    v3 = "MEETS" if v["bound_worst_pct"] <= nums(pub)[0] else "BELOW"
    return v3, c, v3, c + "; four-wire: per-phase loops, each phase-to-N restored by its own integrator, the same floor", E("ctrl", "ctl")


@row(OFF, "Frequency accuracy", "ctrl", "bom")
def _(pub):
    fq = K("ctrl", vf()["acc"], "frequency")
    if fq["oscillator_ppm"] != clock_ppm():
        raise RuntimeError("%s and %s disagree on the controller oscillator: re-run sim/pcs_control.py" % (F["ctrl"], F["bom"]))
    up, _, ts = vf_steps()
    c = (f"+/-{fq['bound_pct']:.4f} % (+/-{fq['bound_ppm']:.0f} ppm), calculated: the output frequency is the angle generator's - the controller "
         f"oscillator +/-{fq['oscillator_ppm']:g} ppm (module BOM) + ageing {fq['ageing_ppm']:g} ppm (ASSUMED) + the 32-bit phase accumulator "
         f"{fq['accumulator_ppm']:.2f} ppm; the VF secondary restoration (T {ts:g} s) returns the droop's offset to zero; dynamic (simulated): "
         f"after a 0 -> rated step the frequency is within +/-0.2 % again after {up['t_f_s']:.2f} s (lowest {up['f_min']:+.2f} Hz)")
    v = "MEETS" if fq["bound_pct"] <= nums(pub)[-1] else "BELOW"
    return v, c, v, c, E("ctrl", "bom")


@row(OFF, "THDu", "ctrl", "ctrl_md")
def _(pub):
    open_item("A switched (PWM, dead-time, ripple-sampling) simulation of the chosen loops and a THDi figure from it")
    t = vf()["thdu"]
    rows = [r for r in K("ctrl", t, "rows") if r["loop"] == "PR h1"]

    def worst(w, comp):
        sel = [r for r in rows if r["wire"] == w and r["dead_time"].startswith("compensated") == comp]
        assert sel, "%s: no THDu rows for %s" % (F["ctrl"], w)
        return max(sel, key=lambda r: r["thdu_pct"])
    lim, dc = nums(pub)[0], K("ctrl", t, "dc")
    out = []
    for w in ("3W", "4W"):
        n, cp = worst(w, False), worst(w, True)
        out.append(("MEETS" if n["thdu_pct"] < lim else "BELOW",
                    f"<= {n['thdu_pct']:.2f} % worst (rated / 50 % / no load, {', '.join('%.0f' % x for x in sorted({r['vdc'] for r in rows}))} V DC; "
                    f"{n['load']} load at {n['vdc']:.0f} V, dead time {n['dead_time']}), {cp['thdu_pct']:.2f} % with it compensated per leg - "
                    f"calculated, analytical (PWM carrier groups through the LCL, the dead-time error through the closed VF loop, a modulated zero "
                    f"sequence through the filters' tolerance mismatch; largest term: {n['dominant'][0][0]} {n['dominant'][0][1]:.2f} %), not a "
                    f"switched simulation; DC component held at {dc['dc_V']:.2f} V ({dc['dc_pct_Un']:.2f} % Un) by the DC regulator"))
    (v3, c3), (v4, c4) = out
    return v3, c3, v4, c4, E("ctrl")


@row(OFF, "DC voltage component", "spec", "ctl")
def _(pub):
    grab("ctl", r"DC-component regulator \(VF, half-wave loads\) \| one-cycle mean of each phase-to-N voltage")
    h = K("spec", S["spec"], "declarations", "offgrid_load_acceptance", "half_wave_four_wire")
    c4 = (f"firmware DC-component regulator on the one-cycle mean of each phase-to-neutral voltage; its measurement floor is {h['dc_measurement_divider_V']:.2f} V "
          f"(divider TCR mismatch at 750 V, calculated) against the limit of {h['dc_component_limit_V']:.2f} V; the ADC gain drift of the two channels is an "
          f"open term (pair VG_x and VGN on one ADC module); the regulator itself is not simulated")
    c3 = "same dividers and regulator on the line-to-line means (firmware row); the budget in the right-hand cell is the four-wire phase-to-neutral case, not repeated here"
    v4 = "MEETS" if h["dc_measurement_divider_V"] < h["dc_component_limit_V"] else "BELOW"
    return "PENDING", c3, v4, c4, E("spec", "ctl")


@row(OFF, "Output voltage imbalance", "ctrl")
def _(pub):
    im = vf()["imb"]
    b, amp, ang = K("ctrl", im, "bound"), nums(pub)[0], nums(pub)[2]
    fr = K("ctrl", im, "per_freq")
    dphi = max(K("ctrl", fr, k, "dphi_deg") for k in fr)
    terms = (f"the output carries the per-channel gain error after the two-point calibration (+/-{K('ctrl', im, 'gain_pct'):.2f} %, every term "
             f"independent - conservative) and the anti-alias filters' tolerance (+/-{dphi:.3f} deg per channel, the drawn values); VC1-3 sampled "
             f"simultaneously; worst of 50 / 60 Hz")
    cells = []
    for w, what in (("3W", "line-to-line, the alpha-beta PR"), ("4W", "phase-to-N, one PR per phase")):
        x = K("ctrl", b, w)
        cells.append(("MEETS" if x["amp_pct"] <= amp and x["angle_deg"] <= ang else "BELOW",
                      f"+/-{x['amp_pct']:.2f} % and 120 +/-{x['angle_deg']:.{2 if w == '3W' else 3}f} deg ({what}; negative sequence "
                      f"{x['vuf_pct']:.2f} %), calculated: the loop regulates the measured voltages exactly at the fundamental, so " + terms))
    (v3, c3), (v4, c4) = cells
    return v3, c3, v4, c4, E("ctrl")


@row(OFF, "Load unbalance", "spec", "pwr4")
def _(pub):
    fw = fourwire()
    assert "100 % unbalance" in S["pwr4"], F["pwr4"] + " no longer discusses the 100 % unbalance"
    assert any("100 % unbalanced load (four-wire only)" in r for r in K("spec", S["spec"], "handover", "firmware_requirements")), \
        F["spec"] + ": the four-wire-only statement for the 100 % unbalanced load is gone"
    cases = [grab("spec", r"^(\d+) A, (\d+) V, (\d+) C$", n) for n in fw["nleg"]]
    desc = " / ".join(f"{x:g}" for x in sorted({float(c[0]) for c in cases})) + " A, " + " / ".join(f"{x:g}" for x in sorted({float(c[1]) for c in cases})) \
        + " V, " + " / ".join(f"{x:g}" for x in sorted({float(c[2]) for c in cases})) + " C"
    ok, hot = True, []
    for name, r in fw["nleg"].items():
        lim = fw["tj_lim"][0] if name.startswith("198 A") else fw["tj_lim"][1]       # continuous tier 150 C, 2-min tier 165 C
        ok &= r["tj_max_C"] <= lim
        hot.append(r["tj_max_C"])
    ok &= fw["cap_max"] <= fw["cap_lim"]
    pp = fw["per_phase"]
    c4 = (f"100 % unbalance = one phase at its own tier with I_N = I ({pp['rated']:.1f} / {pp['continuous']:.1f} / {pp['2_min']:.1f} kW per phase at 230 V, rated / "
          f"continuous / 2 min); neutral leg Tj <= {max(hot):.0f} C over its {len(hot)} cases ({desc}) against {fw['tj_lim'][0]:g} / "
          f"{fw['tj_lim'][1]:g} C; DC-link capacitor ripple <= {fw['cap_max']:.1f} A against {fw['cap_lim']:.1f} A; {fw['lf750']:.0f} A rms at 100 Hz into the "
          f"battery at 750 V (installation item); half-wave load <= {fw['half']['I_pk_limit_A']:.0f} A peak per phase; the N leg runs the "
          f"off-grid cascade with its own DC regulator (control study D-077: a 100 % unbalanced load step moves the N node by 0.57 pu for 20 ms; "
          f"the per-phase virtual reactance is not modelled)")
    c3 = "not possible: no neutral path (firmware requirement: 100 % unbalanced load is four-wire only)"
    return "BELOW", c3, "MEETS" if ok else "BELOW", c4, E("spec", "pwr4")


@row(OFF, "Overload capacity", "spec")
def _(pub):
    t, th, fw = tiers(), thermal(), fourwire()
    lim_pk = K("spec", S["spec"], "declarations", "offgrid_load_acceptance", "crest_factor", "limiter_peak_A")
    pct = [float(x) for x in re.findall(r"(\d+)%", pub)]
    ra = t["rated"][0]
    p_cont, p_2, p_200 = 100 * t["continuous"][0] / ra, 100 * t["2_min"][0] / ra, 100 * t["200_ms"][0] / ra
    ok = p_cont >= pct[0] and p_2 >= pct[2] and p_200 > pct[3] and th["tj2"] <= th["lim2"] and th["tj200"] <= th["lim200"]
    c3 = (f"continuous {t['continuous'][0]:g} A = {p_cont:.0f} % ({t['continuous'][1]:.1f} kVA); 2 min {t['2_min'][0]:g} A = {p_2:.0f} % ({t['2_min'][1]:.1f} kVA); "
          f"200 ms {t['200_ms'][0]:g} A = {p_200:.0f} % (current limiter {lim_pk:.0f} A peak); worst Tj {th['tj2']:.0f} C (2 min) / {th['tj200']:.0f} C (200 ms) at 45 C inlet "
          f"against {th['lim2']:g} / {th['lim200']:g} C; at 60 C inlet the 200 ms tier derates to {th['derated']['200 ms']:.0f} A")
    c4 = c3 + f"; neutral leg {max(r['tj_max_C'] for r in fw['nleg'].values()):.0f} C at worst over its {len(fw['nleg'])} cases (its 200 ms tier is not computed)"
    v = "MEETS" if ok else "BELOW"
    return v, c3, v, c4, E("spec")


# ---------------------------------------------------------------------------------------------------- communication parameters
@row(COM, "Human-computer interaction", "ctl")
def _(pub):
    assert "cabinet accessory" in S["ctl"] and "no web server" in S["ctl"], F["ctl"] + " no longer states the HMI scope"
    c = ("not assessed: the 10.1-inch touch panel is a cabinet accessory outside this project's scope (control board check: not added; no web server, the Ethernet "
         "bridge is transparent); the upper-computer route is drawn: Modbus TCP over the Ethernet bridge or the RS-485 service port")
    return "NOT ASSESSED", c, "NOT ASSESSED", c, E("ctl") + ", docs/requirements/REQUIREMENTS.md section 1"


@row(COM, "Communication interface", "ctl", "bom", "bom4")
def _(pub):
    grab("ctl", r"\[PASS\] Ethernet for the EMS[^\n]*?FITTED on this variant")
    grab("ctl", r"One Ethernet port, not the PMA's two: a daisy chain is the cabinet's switch")
    parts = [("Ethernet", "CH9121T"), ("Ethernet", "HR913550AE"), ("RS-485", "CA-IS3082WNX"), ("CAN", "CA-IS3050W")]
    for n in ("bom", "bom4"):
        for _, mpn in parts:
            bom_row(n, mpn)
    want = [x.replace("RS485", "RS-485") for x in re.findall(r"Ethernet|RS485|CAN", pub)]
    c = ("Ethernet (CH9121T 10/100 bridge + RJ45, FITTED, one port - the published module has two, a daisy chain is the cabinet's switch), isolated RS-485 "
         "(CA-IS3082WNX) and isolated CAN (CA-IS3050W), all behind the module's reinforced barrier; parts present in both module BOMs")
    v = "MEETS" if all(w in c for w in want) else "BELOW"
    return v, c, v, c, E("ctl", "bom", "bom4")


def roles(pub, who):
    g = dict(zip(("bms", "ems"), grab("ctl", r"Port roles \(firmware / configuration, declared\)[^\n]*?BMS = ([^;]*); EMS = ([^;]*);")))
    want = [x.replace("RS485", "RS-485") for x in re.findall(r"Ethernet|RS485|CAN", pub)]
    ok = all(w in g[who] for w in want)
    return ("MEETS" if ok else "BELOW"), g[who]


@row(COM, "Communication with BMS", "ctl")
def _(pub):
    v, g = roles(pub, "bms")
    c = (f"BMS = {g} (declared port role); where it fails: a CAN-only BMS whose bit rate or identifiers cannot share the module bus while modules run in "
         f"parallel needs the RS-485 port (the EMS then on Ethernet)")
    return v, c, v, c, E("ctl")


@row(COM, "Communication with EMS", "ctl")
def _(pub):
    v, g = roles(pub, "ems")
    c = f"EMS = {g} (declared port role)"
    return v, c, v, c, E("ctl")


# ------------------------------------------------------------------------------------------------------------------------ general
@row(GEN, "Max. efficiency", "spec", "design", "pwr4")
def _(pub):
    e = effic()
    op = win()["pe"]["operating_from"]
    out = (f"; {e['at']['vdc']:g} V is below the {op:.0f} V closing permissive at {vac():g} V AC, so inside the 3W+PE window the map's best point is "
           f"{e['inwin']:.2f} % ({e['inwin_v']:g} V, {100 * e['inwin_load']:.0f} % load)") if e["at"]["vdc"] < op else ""
    c3 = (f"{e['peak']:.2f} % peak (inverter, {e['at']['vdc']:g} V DC, {100 * e['at']['load']:.0f} % load; {e['basis']}; everything in the module: devices, "
          f"inductors, contactors, fuses, auxiliary supply, fans){out}; margin of the in-window point {e['inwin'] - nums(pub)[0]:.2f} points against the "
          f"study's model uncertainty of about +/-{e['unc']:g}; full load {e['full750']:.2f} % at 750 V, {e['worst']:.2f} % at the worst corner")
    c4 = ("not separately computed: the efficiency map is the three-wire power stage; the fourth leg (switching of its ripple, L_N), the fourth fan and "
          "one more gate-bias phase are not in the map")
    v = "BETTER" if e["inwin"] > nums(pub)[0] else "MEETS" if e["inwin"] >= nums(pub)[0] else "BELOW"
    return v, c3, "PENDING", c4, E("spec", "design")


@row(GEN, "Charge/discharge switching time", "spec", "ctrl")
def _(pub):
    fr = next(r for r in K("spec", S["spec"], "handover", "firmware_requirements") if r.startswith("charge <-> discharge transfer"))
    ramp, _stiff, _scr5, total, cv = map(float, grab("spec", r"ramp <= (\d+) ms \(ASSUMED setting\)[^(]*\(control study: stiff ([\d.]+) ms, "
                                                   r"SCR 5 ([\d.]+) ms\) -> <= ([\d.]+) ms; CV: 40 Hz crossover, about (\d+) ms", fr))
    tr = K("spec", S["spec"], "declarations", "transfer_ms_control_study")
    steps = {s["scr"]: s["settle_ms"] for s in K("ctrl", S["ctrl"], "steps") if s["case"] == "ref step 0 -> rated"}
    assert abs(steps[None] - tr["stiff"]) < 1e-9 and abs(steps[5.0] - tr["SCR 5"]) < 1e-9, "control study and pcs_spec disagree on the step settling"
    assert round(ramp + tr["SCR 5"], 1) == total, "%s: the transfer row's total is not ramp + settling" % F["spec"]
    lim = nums(pub)[0]
    c = (f"<= {ramp + tr['SCR 5']:.1f} ms at SCR 5 ({ramp:g} ms reference ramp ASSUMED + {tr['SCR 5']:.1f} ms current-loop settling to 5 % for a 0 -> rated "
         f"step, control study); {ramp + tr['stiff']:.1f} ms on a stiff grid; CV mode about {cv:g} ms (estimate); a full reversal is a 2 x step, not simulated")
    v = "MEETS" if ramp + tr["SCR 5"] < lim else "BELOW"
    return v, c, v, c, E("spec", "ctrl")


@row(GEN, "Relative humidity")
def _(pub):
    return "NOT ASSESSED", NA, "NOT ASSESSED", NA, "docs/requirements/REQUIREMENTS.md section 1"


@row(GEN, "Operating temperature range", "spec", "bom", "bom4", "audit")
def _(pub):
    lo, hi = nums(pub)[:2]
    derate = nums(pub)[2]
    mpn, c3n, c4n, fl, fh = fans()
    th, t = thermal(), tiers()
    frac = th["frac"][f"{hi:g}C"]
    holds = min(frac.values()) >= t["continuous"][0] / t["rated"][0] - 1e-9
    two = "holds" if th["derated"]["120 % 2 min"] >= t["2_min"][0] - 1e-9 else f"derates to {th['derated']['120 % 2 min']:.0f} A"
    _env = K("spec", S["spec"], "declarations", "environment")
    assert ("the fans rated" in _env) or ("fans off (rated -10 C)" in _env), F["spec"] + ": the environment declaration no longer lists the fans' rating"
    a = audit()
    fw = fourwire()
    n60 = fw["nleg"]["198 A, 900 V, 60 C"]["tj_max_C"]
    c = (f"cold side: the {c3n} ({c4n} on the four-wire build) x {mpn} fans are rated {fl:+g}~{fh:+g} C (module BOM) against the {lo:+g} C of the published module (since D-081 pcs_spec declares full operation from -10 C inlet only; -30..-10 C is standby or the "
         f"passive-cooling table, 20 / 15 / 8 kVA at 600 V DC and -30 / -20 / -10 C for the three-wire build, ESTIMATE; -30 C at power needs a cold-rated fan, RFQ open); the temperature audit has no row for {a['bom'][1]} of {a['bom'][0]} orderable parts of the module "
         f"BOM{', the fans among them' if a['bom'][2] else ''}, so the cold side of those is unaudited. Hot side: the 110 % tier ({t['continuous'][0]:g} A) holds "
         f"to {hi:g} C inlet with the hottest device at {th['tj60']:.0f} C against {th['limit']:g} C - no derating where the published module derates above "
         f"{derate:g} C - provided the R_DS(on) acceptance rule is applied (production requirement); the fan is at its {fh:g} C limit; the 2-min tier {two}, "
         f"the 200 ms tier derates to {th['derated']['200 ms']:.0f} A")
    v = "MEETS" if fl <= lo and holds and th["tj60"] <= th["limit"] else "BELOW"
    return v, c, v, c + f"; neutral leg {n60:.0f} C at 198 A, 900 V, {hi:g} C", E("spec", "bom", "bom4", "audit")


@row(GEN, "Storage temperature range", "spec", "audit")
def _(pub):
    env = K("spec", S["spec"], "declarations", "environment")
    assert "ASSUMED" in grab("spec", r"(storage [^,]*\([^)]*\))", env)[0], F["spec"] + ": the storage range is no longer labelled ASSUMED"
    c = (f"not assessed: the declared {pub.replace('~', '..')} C is ASSUMED in pcs_spec (the parts' storage ratings are to be confirmed); the temperature audit "
         f"(gen/data/temp_audit.csv) holds operating limits only, no storage ratings")
    return "NOT ASSESSED", c, "NOT ASSESSED", c, E("spec", "audit")


@row(GEN, "Max. operating altitude", "ctl")
def _(pub):
    m, = grab("ctl", r"ALTITUDE THE BARRIER REACHES: (\d+) m")
    clr, at_m = grab("ctl", r"ALTITUDE THE BARRIER REACHES: \d+ m \(clearance ([\d. /]+) mm needed at ([\d /]+) m")
    assert "the inverter has no altitude model" in S["ctl"], F["ctl"] + " no longer says the inverter has no altitude model"
    pub_m = nums(pub)[0]
    need = dict(zip(at_m.split(" / "), clr.split(" / ")))[f"{pub_m:.0f}"]
    ww, = grab("ctl", r"part for part WW [\d.]+ USD \(\+([\d.]+)\)")
    cons, = grab("ctl", r"consolidated on 4-ch WW parts \(\d+ channels\) [\d.]+ USD \(\+([\d.]+)\)")
    assert "AUX-T1" in S["ctl"], F["ctl"] + " no longer discusses the AUX-T1 clearance"
    b1, = grab("ctl", r"B1 isolators ([\d.]+) mm minimum clearance")
    c = (f"{m} m: the control board's isolation barrier B1 gives {b1} mm ({clr} mm are needed at {at_m} m); the inverter has no altitude derating model "
         f"(power-board clearances, port SPD ratings, contactors, fuses and fans are not assessed); {pub_m:.0f} m needs {need} mm: the 15 mm (WW) isolator set "
         f"(+{cons} to +{ww} USD per control board; the check does not fit it) and the AUX-T1 transformer of the power board drawn for it")
    v = "MEETS" if float(m) >= pub_m else "BELOW"
    return v, c, v, c, E("ctl")


@row(GEN, "Noise emission", "spec", "pv", "bom", "bom4")
def _(pub):
    mpn, c3n, c4n, _lo, _hi = fans()
    pvf = K("pv", S["pv"], "fan")
    assert pvf["mpn"] == mpn, "%s: the PV module uses another fan (%s) than the inverter (%s)" % (F["pv"], pvf["mpn"], mpn)
    base = K("pv", S["pv"], "modules", "PV-P75", "noise_dBA_1m_all_fans_100pct")
    n0 = pvf["count_per_module"]
    lim = nums(pub)[0]
    l3, l4 = base + 10 * math.log10(c3n / n0), base + 10 * math.log10(c4n / n0)
    c = (f"{{l:.1f}} dB(A) at 1 m with all {{n}} fans at full speed: ESTIMATE by analogy, not computed for the inverter - the PV-P75 fan model "
         f"({n0} x the same {mpn}, data-sheet sound data + a 3 dB installation estimate, free field) scaled by 10 log10(fans / {n0}); published: < {lim:g} dB, "
         f"distance and weighting not stated")
    v = "BETTER" if max(l3, l4) < lim else "MEETS" if l3 < lim else "BELOW"
    return v, c.format(l=l3, n=c3n), v, c.format(l=l4, n=c4n), E("pv", "bom", "bom4")


@row(GEN, "Over voltage category", "spec")
def _(pub):
    env = K("spec", S["spec"], "declarations", "environment")
    dc, ac = grab("spec", r"OVC DC (I+) / AC (I+)", env)
    pdc, pac = grab("spec", r"DC type (I+) / AC type (I+)", pub)
    v = "MEETS" if (dc, ac) == (pdc, pac) else "BELOW"
    c = f"DC type {dc} / AC type {ac}: the declared design basis of the insulation coordination (pcs_spec), not a certification"
    return v, c, v, c, E("spec")


@row(GEN, "Pollution degree", "spec", "ctl")
def _(pub):
    env = K("spec", S["spec"], "declarations", "environment")
    ext, inn = grab("spec", r"pollution degree external (\d) / internal (\d)", env)
    pub_ext, pub_int = grab("spec", r"External PD(\d); Internal PD(\d)", pub)
    grab("ctl", r"conformal coating to PD1 \(>= [\d.]+ mm\) over the B1 zone is MANDATORY")
    v = "MEETS" if (ext, inn) == (pub_ext, pub_int) else "BELOW"
    c = (f"external PD{ext} / internal PD{inn}: the declared design basis (pcs_spec); the control board's barrier needs conformal coating to PD1 over the B1 zone "
         f"(its check: mandatory)")
    return v, c, v, c, E("spec", "ctl")


@row(GEN, "Protection degree")
def _(pub):
    return "NOT ASSESSED", NA, "NOT ASSESSED", NA, "docs/requirements/REQUIREMENTS.md section 1"


@row(GEN, "Cooling", "spec", "bom", "bom4")
def _(pub):
    mpn, c3n, c4n, _lo, _hi = fans()
    th = thermal()
    assert any("fan speed control" in r for r in K("spec", S["spec"], "handover", "firmware_requirements")), F["spec"] + ": no fan speed control requirement"
    grab("ctl", r"Fan supply transfer \(isolated PWM\)")
    cfm, hs = th["cfm"], th["hs"]
    c = (f"forced air, {{n}} x {mpn} (one per heatsink section: {cfm['m3h_per_section']:.0f} m3/h at {hs['dp_Pa']:.0f} Pa), fan speed control (firmware "
         f"requirement; isolated PWM fan supply on the control board, NTCs on every heatsink section, the L1 windings and the DC-link bank); {{cfm:.0f}} CFM = "
         f"{{kw:.2f}} CFM/kW against the published module's {cfm['competitor_CFM_per_kW']:g} CFM/kW; hottest device {th['tj45']:.0f} C at 110 %, 45 C inlet "
         f"(calculated)")
    return ("MEETS", c.format(n=c3n, cfm=cfm["CFM"]["three-wire"], kw=cfm["CFM_per_kW_125kW"]["three-wire"]),
            "MEETS", c.format(n=c4n, cfm=cfm["CFM"]["four-wire"], kw=cfm["CFM_per_kW_125kW"]["four-wire"]), E("spec", "bom", "bom4"))


def connector_row(prefix):
    def build(pub):
        def cell(name):
            rows = [r for r in S[name] if prefix in r["Value"] and r["Sourcing"] == "CUSTOM"]
            assert rows, "%s: no custom '%s' lines" % (F[name], prefix)
            pkg, amps = {r["Package"] for r in rows}, {grab(name, r"(\d+) A", r["Value"])[0] for r in rows}
            return (f"not assessed: connectors are mechanics outside this project's scope; the module BOM draws {len(rows)} chassis-mounted custom "
                    f"{'/'.join(sorted(pkg))} terminals ({'/'.join(sorted(amps))} A), not quick-plug / hot-plug connectors")
        return "NOT ASSESSED", cell("bom"), "NOT ASSESSED", cell("bom4"), E("bom", "bom4") + ", docs/requirements/REQUIREMENTS.md section 1"
    return build


row(GEN, "DC connector", "bom", "bom4")(connector_row("DC terminal"))
row(GEN, "AC connector", "bom", "bom4")(connector_row("AC terminal"))


@row(GEN, "Installation style")
def _(pub):
    return "NOT ASSESSED", NA, "NOT ASSESSED", NA, "docs/requirements/REQUIREMENTS.md section 1"


@row(GEN, "Dimension")
def _(pub):
    return "NOT ASSESSED", NA, "NOT ASSESSED", NA, "docs/requirements/REQUIREMENTS.md section 1"


@row(GEN, "Weight", "spec")
def _(pub):
    m3, m4 = mass_floor()
    c = lambda m, extra="": (f"{NA}; the masses the design does record (L1, L2, CM choke, heatsink sections{extra}) already sum to {m:.0f} kg of the published "
                             f"{nums(pub)[0]:g} kg - a lower bound, not the module weight")
    return ("NOT ASSESSED", c(m3), "NOT ASSESSED", c(m4, ", the neutral inductor taken as an L1"),
            E("spec") + ", docs/requirements/REQUIREMENTS.md section 1")


# ------------------------------------------------------------------------------------------------------------------------- main
def stale_guard():
    """the power-board checks must come from the present pcs_spec.json / port_spec.json (as gen/pcs_ctrl.py insists)."""
    now = tuple(hashlib.sha256(open(os.path.join(REPO, F[k]), "rb").read()).hexdigest()[:16] for k in ("spec", "port"))
    for n in ("pwr", "pwr4"):
        h = re.search(r"pcs_spec\.json sha256 ([0-9a-f]{16}); port_spec\.json sha256 ([0-9a-f]{16})", S[n])
        if not h or h.groups() != now:
            raise RuntimeError("%s is stale (not built from the present pcs_spec.json / port_spec.json): rebuild gen/pcs_power.py first" % F[n])


def main():
    MG.update(megarevo())
    S.update({k: load(k) for k in F})
    todo = {}
    for section, prefix, src, fn in ROWS:
        keys = [k for k in MG if k[0] == section and k[1].startswith(prefix)]
        assert len(keys) == 1, "%s / %s matches %d published rows" % (section, prefix, len(keys))
        assert keys[0] not in todo, "published row mapped twice: %s" % (keys[0],)
        todo[keys[0]] = (src, fn)
    unmapped = [k for k in MG if k not in todo]
    assert not unmapped, "published rows without a comparison: %s" % unmapped
    if all(S[k] is not None for k in ("spec", "port", "pwr", "pwr4")):
        stale_guard()
    if S["ctrl"] is not None and S["spec"] is not None:         # the control study ran on this DC link
        c_dc = K("ctrl", S["ctrl"], "inputs", "C_dc_F") * 1e6
        c_eq = K("spec", S["spec"], "ports_and_common_mode", "precharge", "C_eq_uF")
        if abs(c_dc - c_eq) > 0.5:
            raise RuntimeError("%s used a %.1f uF DC link, pcs_spec has %.1f uF: re-run sim/pcs_control.py" % (F["ctrl"], c_dc, c_eq))
    rows = []      # (section, parameter, published, ours 3W, ours 4W, verdict, evidence)
    for key in MG:
        src, fn = todo[key]
        gone = [F[n] for n in src if S[n] is None]
        if gone:
            rows.append((key[0], key[1], MG[key], "source not built yet", "source not built yet", "PENDING", ", ".join(gone)))
            continue
        try:
            res = fn(MG[key])
        except (KeyError, IndexError, TypeError) as e:       # a direct subscript of a source that K() did not guard
            raise RuntimeError("row %s / %s: %s %s missing or changed in one of its sources (%s) - rebuild them or update "
                               "sim/compare_megarevo_pcs.py" % (key[0], key[1], type(e).__name__, e, E(*src))) from None
        v3, c3, v4, c4, ev = res[:5]
        rows.append((key[0], key[1], MG[key], c3, c4, res[5] if len(res) > 5 else best(v3, v4), ev))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "comparison.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["section", "parameter", "megarevo_pma0125_published", "pcs_p125_calculated", "pcs_p125_4w_calculated", "verdict", "evidence"])
        w.writerows(rows)
    count = {v: sum(r[5] == v for r in rows) for v in ("BETTER", "MEETS", "BELOW", "NOT ASSESSED", "PENDING")}
    built = {}
    for b, _ in BOARDS:
        p = os.path.join(REPO, "hardware", b, "outputs", b + "_report.json")
        built[b] = json.load(open(p)) if os.path.exists(p) else None
    bad_boards = [b for b, r in built.items() if not (r and r["passed"])]
    cost = cost_rows() if S["cost"] is not None else {}
    study = {}
    if S["spec"] is not None:
        c = K("spec", S["spec"], "cost_usd")
        study = {"PCS-P125": (c["three_wire"]["catalogue"], c["three_wire"]["5k"]), "PCS-P125-4W": (c["four_wire"]["catalogue"], c["four_wire"]["5k"])}
    L = ["# PCS-P125 (three-wire) and PCS-P125-4W (four-wire) compared with Megarevo PMA0125", "",
         "Generated by `sim/compare_megarevo_pcs.py`. **Megarevo's column is what it publishes; ours is calculated or simulated from the design files named in "
         "the evidence column. Nothing of ours is bench-validated.** Two assemblies of one module: PCS-P125 (grid type 3W+PE) and PCS-P125-4W (3W+N+PE); one "
         "verdict per row, the better of the two where the row applies to both.", "",
         "Result: %d better, %d meets, %d below, %d not assessed, %d pending, out of %d published rows."
         % (count["BETTER"], count["MEETS"], count["BELOW"], count["NOT ASSESSED"], count["PENDING"], len(MG)), "",
         "| Parameter | Megarevo PMA0125 (published) | PCS-P125 (calculated) | PCS-P125-4W (calculated) | Verdict | Evidence |", "|---|---|---|---|---|---|"]
    section = None
    esc = lambda x: x.replace("|", "\\|")
    compact = lambda a, b: "as PCS-P125" if a == b else "as PCS-P125" + b[len(a):] if b.startswith(a) else b
    for sec, par, theirs, o3, o4, verdict, ev in rows:
        if sec != section:
            L.append("| **%s** | | | | | |" % sec)
            section = sec
        L.append("| %s | %s | %s | %s | **%s** | `%s` |" % (esc(par), esc(theirs), esc(o3), esc(compact(o3, o4)), verdict, ev.replace(", ", "`, `")))
    cost_lines, cost_print = [], []
    if cost and study:
        cost_lines = ["", "## Module cost (not a comparison row)", "",
                      "| Module | Design study, catalogue / 5,000 units (USD) | Drawn BOM, catalogue / 5,000 units (USD) | Unpriced lines in the drawn BOM |",
                      "|---|---|---|---|"]
        for m in ("PCS-P125", "PCS-P125-4W"):
            cost_lines.append("| %s | %.0f / %.0f | %.0f / %.0f | %d |" % (m, *study[m], *cost[m]))
            cost_print.append("  module cost %s: design study %.0f / %.0f USD, drawn BOM %.0f / %.0f USD (catalogue / 5,000 units; %d unpriced lines)" % (m, *study[m], *cost[m]))
        cost_lines += ["", "Design study = `cost_usd` of `%s` (catalogue / 5,000 units, nothing quoted or bought); drawn BOM = the module BOMs priced by `gen/cost.py` "
                       "(`%s`, summary table); the drawn totals are lower bounds, the unpriced lines carry no cost." % (F["spec"], F["cost"])]
    L += cost_lines
    L += ["", "## Boards behind the PCS-P125 columns", "", "| Board | Used by | Rev | Parts | Nets | Build checks |", "|---|---|---|---|---|---|"]
    for b, used in BOARDS:
        r = built[b]
        L.append("| %s | %s | %s | %s | %s | %s |" % ((b, used, r.get("rev", "-"), r["parts"], r["nets"], "all passed" if r["passed"] else "FAILING")
                                                      if r else (b, used, "-", "-", "-", "not built yet")))
    boms = []
    for n, m in (("bom", "PCS-P125"), ("bom4", "PCS-P125-4W")):
        if S[n] is not None:
            boms.append("%s: %d lines, %d parts (`%s`)" % (m, len(S[n]), sum(int(r["Qty"]) for r in S[n]), F[n]))
    if boms:
        L += ["", "Module BOMs - " + "; ".join(boms) + "; cost in `bom/COST.md`."]
    L += ["", "## Reading this table", "",
          "- **BETTER / MEETS** compare a calculated figure of ours with a published figure of theirs; a calculated figure and a published one are not the same "
          "kind of evidence. kVA and kW rows are compared at the published figure's own precision (the published kVA figures are rounded current tiers).",
          "- **BELOW** rows are the price of the cost-first build or open work, not rounding. **PENDING** rows have no computed counterpart yet: the evidence "
          "column names the file that should produce it. The design's risks are in `docs/guide/12-risks-and-open-items.md`, the control study's open items in "
          "`sim/out/pcs_control/report.md` section 11.",
          "- **NOT ASSESSED** rows need the mechanical design, which this project does not contain, or an audit that has not been done.",
          "- Where a row applies to both assemblies the verdict is the better of the two; a row only the four-wire build can meet says so in the three-wire cell. "
          "The two DC voltage rows hold one window per grid type: the verdict is the worse window.",
          "- Decisions and their open conditions are in `docs/requirements/DECISIONS.md`; the assumptions behind the module figures are in "
          "`sim/out/pcs_design/report.md` and `sim/out/pcs_control/report.md`."]
    open(os.path.join(OUT, "report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("Result: %d better, %d meets, %d below, %d not assessed, %d pending, out of %d published rows (PCS-P125 + PCS-P125-4W vs PMA0125)"
          % (count["BETTER"], count["MEETS"], count["BELOW"], count["NOT ASSESSED"], count["PENDING"], len(MG)))
    for r in rows:
        if r[5] in ("BELOW", "PENDING"):
            print("  %-8s %s / %s: %s" % (r[5], r[0], r[1], r[3][:110]))
    if bad_boards:
        print("  boards not built or failing: " + ", ".join(bad_boards))
    for line in cost_print:
        print(line)
    assert sum(count.values()) == len(rows) == len(MG)
    return 1 if count["BELOW"] or count["PENDING"] or bad_boards else 0


if __name__ == "__main__":
    sys.exit(main())
