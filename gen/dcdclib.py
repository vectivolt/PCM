"""Shared build/verify toolkit for every DC-DC board (cut down from Meter/generators/meterlib.py).

A board script declares its design with `Builder` and calls `build()`, which
  writes the KiCad 10 project        hardware/<board>/<board>.kicad_sch + sheets/
  runs kicad-cli ERC                 no error, no unwaived warning
  proves netlist identity            kicad-cli's exported netlist == the Python design table
  checks isolation domains           no part joins two domains unless it is a declared isolator
  checks part stress                 jellybean capacitor voltage and resistor power against the nets' DC ranges
  renders the PDF                    hardware/<board>/outputs/<board>_schematic.pdf
  writes the BOM                     bom/<board>_BOM.csv  (every line: manufacturer + MPN + datasheet, or GENERIC/RFQ/CUSTOM)

Catalog entry (one per orderable part), a plain dict:
  mfr, mpn, desc, pkg, prefix        manufacturer, orderable part number, description, package, reference prefix
  ds                                 datasheet: repo-relative path under docs/datasheets/ (preferred) or an https URL
  stock=("Device", "D_Schottky")     use a KiCad stock symbol (its own pin numbers; `python gen/dcdclib.py Device:D_Schottky` lists them)
  pins={"left": [...], "right": [...]}   or build a rectangular symbol; a pin is "number name type", None leaves a gap
  units=[{...}, {...}]               multi-unit symbol (each dict like `pins`, plus optional "title")
  sourcing                           ORDERABLE (default) | RFQ | CUSTOM | NOPART
Pin types: i input, o output, b bidirectional, t tri_state, p passive, pi power_in, po power_out,
           oc open_collector, oe open_emitter, nc no_connect.
Footprints are deliberately not assigned (PCB layout is out of scope, decision D-003): `pkg` is a BOM field only.

Usage: python gen/dcdclib.py Device:D_Schottky     # print the pin table of a KiCad stock symbol
"""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import schgen  # noqa: E402
import symlib  # noqa: E402
from schgen import Design, Part  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, ".."))
KCLI = os.environ.get("KICAD_CLI") or "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
LIB = "dcdc"
AUTHOR = "Chinmoy Bhuyan"
ETYPE = {"i": "input", "o": "output", "b": "bidirectional", "t": "tri_state", "p": "passive", "pi": "power_in",
         "po": "power_out", "oc": "open_collector", "oe": "open_emitter", "nc": "no_connect"}
NO_MPN = ("GENERIC", "RFQ", "CUSTOM", "NOPART")   # sourcing classes that do not need mfr + MPN + datasheet
BASE = {"R": dict(stock=("Device", "R"), prefix="R"), "C": dict(stock=("Device", "C"), prefix="C"),
        "TP": dict(stock=("Connector", "TestPoint"), prefix="TP", mfr="", mpn="", desc="Test point", pkg="TP", ds="",
                   sourcing="NOPART")}


def run(cmd, cwd=REPO, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        raise SystemExit("FAILED %s\n%s%s" % (" ".join(cmd), r.stdout, r.stderr))
    return r


class Results(list):
    def add(self, name, ok, detail):
        self.append({"check": name, "result": "pass" if ok else "fail", "detail": detail})
        print("[%s] %s - %s" % ("PASS" if ok else "FAIL", name, detail))


# ------------------------------------------------------------------ design builder
class Builder:
    """rails / returns: nets drawn as power symbols (returns point down). Sheet keys must start 'NN_'."""

    def __init__(self, project, title, rev, date, catalog, rails, returns, subtitle="", comment1="", comment4="",
                 root_notes=None):
        self.D = Design(project, title, rev, date)
        self.D.author, self.D.subtitle, self.D.comment1, self.D.comment4 = AUTHOR, subtitle, comment1, comment4
        self.D.root_notes = root_notes or []
        self.catalog = dict(BASE, **catalog)
        self.rails, self.returns = list(rails), list(returns)
        self.no, self.sheet, self.blk = 0, None, None
        self.count = defaultdict(int)
        self.bom = {}
        self.flagged = set()

    def new_sheet(self, key, title, desc=""):
        assert re.match(r"\d\d_", key), "sheet key must start with a two-digit number: " + key
        self.no += 1
        self.sheet = self.D.sheet(key, title, desc)
        self.count = defaultdict(int)

    def block(self, title, note=""):
        self.blk = self.sheet.block(title, note)

    def ref(self, prefix):
        self.count[prefix] += 1
        assert self.count[prefix] < 100, "more than 99 %s on sheet %s - split the sheet" % (prefix, self.sheet.key)
        return "%s%d%02d" % (prefix, self.no, self.count[prefix])

    def _add(self, ref, sym, value, pins, meta, dnp=False, rot=None):
        fields = {"Datasheet": meta["ds"], "Description": meta["desc"], "Manufacturer": meta["mfr"], "MPN": meta["mpn"],
                  "Package": meta["pkg"], "Sourcing": meta["sourcing"]}
        p = Part(ref, LIB + ":" + sym, value, pins, fields=fields, unit_rot=rot, dnp=dnp)
        self.D.add(self.sheet, self.blk, p)
        self.bom[ref] = dict(meta, value=value, dnp=dnp)
        return p

    def part(self, key, pins, ref=None, value=None, rot=None, dnp=False):
        m = self.catalog[key]
        meta = dict(desc=m["desc"], mfr=m["mfr"], mpn=m["mpn"], pkg=m.get("pkg", ""), ds=m.get("ds", ""),
                    sourcing=m.get("sourcing", "ORDERABLE"))
        return self._add(ref or self.ref(m["prefix"]), key, value or m["mpn"] or m["desc"], pins, meta, dnp, rot)

    def R(self, value, n1, n2, pkg="0603", tol="1%", note="", dnp=False, ref=None):
        """Jellybean chip resistor, fully specified by its description (any AVL vendor)."""
        desc = " ".join(x for x in ("Resistor", value, tol, pkg, note) if x)
        return self._add(ref or self.ref("R"), "R", value, {"1": n1, "2": n2},
                         dict(desc=desc, mfr="", mpn="", pkg=pkg, ds="", sourcing="GENERIC"), dnp)

    def C(self, value, n1, n2, pkg="0603", volt="50V", diel="X7R", dnp=False, ref=None, tol=""):
        """Jellybean MLCC, fully specified by its description (any AVL vendor)."""
        desc = " ".join(x for x in ("Capacitor", value, tol, volt, diel, pkg) if x)
        return self._add(ref or self.ref("C"), "C", "%s %s" % (value, volt), {"1": n1, "2": n2},
                         dict(desc=desc, mfr="", mpn="", pkg=pkg, ds="", sourcing="GENERIC"), dnp)

    def TP(self, net):
        return self.part("TP", {"1": net}, value="TP")

    def flag(self, net):
        """PWR_FLAG: tells ERC this net is driven (rails that enter through a connector or a passive).
        Safe to call twice for one net (reusable blocks do): the second call adds nothing."""
        if net in self.flagged:
            return None
        self.flagged.add(net)
        self.count["FLG"] += 1
        return self._add("#FLG%d%02d" % (self.no, self.count["FLG"]), "PWR_FLAG", "PWR_FLAG", {"1": net},
                         dict(desc="ERC power flag", mfr="", mpn="", pkg="", ds="", sourcing="NOPART"))


# ------------------------------------------------------------------ symbols + KiCad project
def _unit(u):
    return dict(u, **{side: [None if s is None else (s.split()[0], s.split()[1], ETYPE[s.split()[2]]) for s in u[side]]
                      for side in ("left", "right", "top", "bottom") if side in u})


def make_symbols(B):
    """Symbols for the catalog keys this design uses, plus PWR_FLAG and one power symbol per rail/return."""
    syms = {"PWR_FLAG": symlib.kicad_symbol("power", "PWR_FLAG", "PWR_FLAG")}
    for key in sorted({p.lib_id.split(":")[1] for p in B.D.parts.values()} - {"PWR_FLAG"}):
        m = B.catalog[key]
        if "stock" in m:
            syms[key] = symlib.kicad_symbol(m["stock"][0], m["stock"][1], key)
        else:
            syms[key] = symlib.custom_ic(key, [_unit(u) for u in m.get("units") or [m["pins"]]], ref=m["prefix"],
                                         value=m["mpn"], datasheet=m.get("ds", ""), description=m["desc"])
    for net in B.rails:
        syms["PWR_" + net] = symlib.power_symbol("PWR_" + net, net, "up")
    for net in B.returns:
        syms["PWR_" + net] = symlib.power_symbol("PWR_" + net, net, "gnd")
    return syms


# kicad-cli otherwise runs with KiCad's defaults, which IGNORE "global label only appears once" - the one rule
# that catches a one-character net-name typo in a design where every net is drawn as labels.
ERC_SEVERITIES = {"single_global_label": "error", "similar_labels": "error", "isolated_pin_label": "error",
                  "four_way_junction": "warning", "footprint_filter": "ignore", "simulation_model": "ignore",
                  "footprint_link_issues": "ignore"}   # footprints are not assigned (D-003)


def write_project(B, hw):
    D, project = B.D, B.D.project
    syms = make_symbols(B)
    D.symbols = {LIB + ":" + k: v for k, v in syms.items()}
    D.power_nets = {net: LIB + ":PWR_" + net for net in B.rails + B.returns}
    schgen.GND_NETS = tuple(B.returns)
    os.makedirs(os.path.join(hw, "sheets"))
    symlib.write_library(os.path.join(hw, project + ".kicad_sym"), list(syms.values()))
    json.dump({"meta": {"filename": project + ".kicad_pro", "version": 3},
               "schematic": {"page_layout_descr_file": "", "legacy_lib_dir": "", "legacy_lib_list": []},
               "sheets": [[schgen.uid("root", project), "Root"]] + [[s.uuid, s.title] for s in D.sheets],
               "text_variables": {"REV": D.rev},
               "erc": {"erc_exclusions": [], "meta": {"version": 0}, "rule_severities": ERC_SEVERITIES}},
              open(os.path.join(hw, project + ".kicad_pro"), "w"), indent=2)
    open(os.path.join(hw, "sym-lib-table"), "w").write(
        '(sym_lib_table\n  (version 7)\n  (lib (name "%s")(type "KiCad")(uri "${KIPRJMOD}/%s.kicad_sym")(options "")'
        '(descr "DC-DC platform symbols, board %s"))\n)\n' % (LIB, project, project))
    files = ["sheets/%s.kicad_sch" % s.key for s in D.sheets]
    root_uuid = schgen.write_root(D, os.path.join(hw, project + ".kicad_sch"), files, [i + 2 for i in range(len(files))])
    for i, (s, f) in enumerate(zip(D.sheets, files)):
        schgen.write_sheet(D, s, os.path.join(hw, f), root_uuid, i + 2)
    for f in files + [project + ".kicad_sch"]:
        run([KCLI, "sch", "upgrade", "--force", f], cwd=hw)


# ------------------------------------------------------------------ checks
def check_erc(json_path, R, waivers=None):
    """waivers: {violation type: reason}. A waiver silences WARNINGS of that type only - never an error."""
    waivers = waivers or {}
    errors, warnings, unwaived = [], [], []
    for sh in json.load(open(json_path)).get("sheets", []):
        for v in sh.get("violations", []):
            rec = "%s %s: %s" % (sh["path"], v["type"], v["description"][:70])
            if v["severity"] == "error":
                errors.append(rec)
            else:
                warnings.append(rec)
                if v["type"] not in waivers:
                    unwaived.append(rec)
    R.add("ERC (kicad-cli, all severities)", not errors and not unwaived,
          "%d errors, %d warnings (%d waived) - %s" % (len(errors), len(warnings), len(warnings) - len(unwaived),
                                                       "; ".join((errors + unwaived)[:6]) or "clean"))


def export_graph(xml_path):
    """{net name: {(ref, pin)}} and the component set from KiCad's XML netlist, power/flag symbols removed."""
    root = ET.parse(xml_path).getroot()
    nets = {}
    for n in root.find("nets"):
        if n.get("name").startswith("unconnected-"):   # KiCad's pseudo-net for a pin carrying a no-connect flag
            continue
        nodes = {(x.get("ref"), x.get("pin")) for x in n.findall("node") if not x.get("ref").startswith("#")}
        if nodes:
            nets[n.get("name").split("/")[-1]] = nodes
    return nets, {c.get("ref") for c in root.find("components") if not c.get("ref").startswith("#")}


def check_netlist_identity(D, xml_path, R):
    nets, comps = export_graph(xml_path)
    by_nodes = {frozenset(v): k for k, v in nets.items()}
    errors, checked = [], 0
    for net, nodes in D.nets().items():
        nodes = frozenset((r, p) for r, p in nodes if not r.startswith("#"))
        if not nodes:
            continue
        checked += 1
        name = by_nodes.get(nodes)
        if name is None:
            errors.append("%s: node set differs" % net)
        elif name != net:
            errors.append("%s exported as %s" % (net, name))
    missing = sorted(r for r in D.parts if not r.startswith("#") and r not in comps)
    if missing:
        errors.append("components missing: %s" % missing)
    if len(nets) != checked:
        errors.append("export has %d nets, design %d" % (len(nets), checked))
    R.add("Netlist identity (kicad-cli XML export == design table)", not errors,
          "%d nets, %d components identical" % (checked, len(comps)) if not errors else "; ".join(errors[:5]))


def check_domains(B, R, domain_of, isolators, crossings=()):
    """No part may touch nets of two isolation domains unless it is a declared isolator (barrier part) or a
    declared crossing (a part that deliberately bridges domains without being a barrier, e.g. a Y capacitor)."""
    bad = []
    for p in B.D.parts.values():
        doms = sorted({domain_of(n) for n in p.pins.values() if n})
        p.fields["Domain"] = ("ISOLATOR" if p.ref in isolators else "CROSSING" if p.ref in crossings else ",".join(doms))
        B.bom[p.ref]["domain"] = p.fields["Domain"]
        if len(doms) > 1 and p.ref not in isolators and p.ref not in crossings:
            bad.append("%s joins %s" % (p.ref, "+".join(doms)))
    unused = sorted(r for r in set(isolators) | set(crossings) if r not in B.D.parts)
    R.add("Isolation domains (only declared isolators/crossings bridge two domains)", not bad and not unused,
          "; ".join(bad[:6] + ["declared but absent: %s" % unused] * bool(unused)) or
          "%d isolators, %d crossings, %d other parts each in one domain"
          % (len(isolators), len(crossings), len(B.D.parts) - len(isolators) - len(crossings)))


R_PKG = {"0402": (0.0625, 50), "0603": (0.1, 75), "0805": (0.125, 150), "1206": (0.25, 200), "1210": (0.5, 200),
         "2010": (0.75, 200), "2512": (1.0, 200), "MELF 0204": (0.25, 200), "MELF 0207": (1.0, 350)}   # W at 70 C, V working
UNIT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "R": 1.0, "k": 1e3, "M": 1e6, "": 1.0}


def number(text):
    """'4.99k' -> 4990.0, '100R' -> 100.0, '2kV' -> 2000.0, '50V' -> 50.0; None if it is not a plain value."""
    m = re.fullmatch(r"([0-9.]+)([pnumRkM]?)[VFR]?", text.replace("kV", "k").strip())
    return float(m.group(1)) * UNIT[m.group(2)] if m else None


def check_stress(B, R, vrange):
    """Voltage and power derating of the jellybean parts (B.R / B.C), from the DC range of the two nets they join.
    vrange(net) -> (vmin, vmax) in volts against the reference of that net's own domain, or None for a net with no
    DC range (switching node, gate, pulse) - parts on such nets are counted as not assessed and stay with the board's
    own design check. Rules: MLCC applied voltage <= 80 % of rated; chip resistor dissipation <= 60 % of the package
    rating and voltage <= the package working voltage.
    # ponytail: DC ranges only - pulse load, ripple current and temperature derating stay in each design_check()."""
    bad, done, skipped = [], 0, 0
    for p in B.D.parts.values():
        kind = p.lib_id.split(":")[1]
        if kind not in ("R", "C") or p.dnp or p.fields.get("Domain") in ("ISOLATOR", "CROSSING"):
            continue
        a, b = (vrange(n) for n in (p.pins["1"], p.pins["2"]))
        if a is None or b is None:
            skipped += 1
            continue
        v = max(abs(a[1] - b[0]), abs(a[0] - b[1]))
        if kind == "C":
            rated = number(p.value.split()[-1])
            if rated is None:
                skipped += 1
                continue
            done += 1
            if v > 0.8 * rated:
                bad.append("%s %s sees %.1f V (> 80 %% of its rating)" % (p.ref, p.value, v))
        else:
            ohm, pkg = number(p.value), R_PKG.get(p.fields["Package"])
            if not ohm or not pkg:
                skipped += 1
                continue
            done += 1
            if v * v / ohm > 0.6 * pkg[0] or v > pkg[1]:
                bad.append("%s %s %s sees %.1f V = %.3f W (limit %.3f W, %d V)" % (p.ref, p.value, p.fields["Package"], v,
                                                                                   v * v / ohm, 0.6 * pkg[0], pkg[1]))
    R.add("Stress: jellybean capacitor voltage <= 80 %, resistor power <= 60 % and working voltage", not bad,
          "; ".join(bad[:8]) + (" (+%d more)" % (len(bad) - 8) if len(bad) > 8 else "") if bad else
          "%d parts inside the rules, %d on nets without a DC range (left to the design check)" % (done, skipped))


def write_bom(B, path, R):
    groups = defaultdict(list)
    for ref, m in B.bom.items():
        if not ref.startswith("#"):
            groups[(m["mfr"], m["mpn"], m["value"], m["desc"], m["pkg"], m["sourcing"], m["ds"], m["dnp"])].append(ref)
    bad = []
    rows = sorted(groups.items(), key=lambda kv: (kv[0][5] in NO_MPN, natural(kv[1][0])))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Item", "Qty", "References", "Value", "Description", "Manufacturer", "MPN", "Package", "Sourcing",
                    "Datasheet", "DNP"])
        for i, ((mfr, mpn, value, desc, pkg, sourcing, ds, dnp), refs) in enumerate(rows, 1):
            refs.sort(key=natural)
            w.writerow([i, len(refs), " ".join(refs), value, desc, mfr, mpn, pkg, sourcing, ds, "DNP" if dnp else ""])
            ds_ok = ds.startswith("http") or (ds and os.path.exists(os.path.join(REPO, ds)))
            if sourcing not in NO_MPN and not (mfr and mpn and ds_ok):
                bad.append("%s (%s): %s" % (refs[0], mpn or value, "no mfr/MPN" if not (mfr and mpn) else "datasheet missing: " + ds))
    n = sum(len(v) for v in groups.values())
    R.add("BOM: every line has manufacturer + MPN + datasheet on file, or is GENERIC/RFQ/CUSTOM", not bad,
          "; ".join(bad[:6]) or "%d lines, %d parts -> %s" % (len(rows), n, os.path.relpath(path, REPO)))


def write_symbol_pins(B, path):
    """The pin table of every orderable part as drawn - the input of the independent pin audit (gen/pin_audit.py)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["key", "mfr", "mpn", "pkg", "ds", "pin", "name", "type"])
        for lib_id, sym in sorted(B.D.symbols.items()):
            m = B.catalog.get(lib_id.split(":")[1])
            if m and m.get("mpn") and m.get("sourcing", "ORDERABLE") not in NO_MPN:
                for p in sorted(symlib.symbol_pins(sym), key=lambda p: natural(p.number)):
                    w.writerow([lib_id.split(":")[1], m["mfr"], m["mpn"], m.get("pkg", ""), m.get("ds", ""), p.number,
                                p.name, p.etype])


def natural(ref):
    """Sort key: numbers numerically, text alphabetically, and the two never compared with each other."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.findall(r"\d+|\D+", ref)]


# ------------------------------------------------------------------ pipeline
def build(B, waivers=None, domain_of=None, isolators=(), crossings=(), vrange=None):
    """Write, verify and render one board. Returns a process exit code (0 = every check passed)."""
    project = B.D.project
    hw = os.path.join(REPO, "hardware", project)
    out = os.path.join(hw, "outputs")
    shutil.rmtree(hw, ignore_errors=True)   # hardware/<board>/ is generated output only
    R = Results()
    if domain_of:
        check_domains(B, R, domain_of, set(isolators), set(crossings))   # first: it stamps the Domain field
    if vrange:
        check_stress(B, R, vrange)
    write_project(B, hw)
    os.makedirs(out)
    root = project + ".kicad_sch"
    erc_json, xml = os.path.join(out, project + "_erc.json"), os.path.join(out, project + "_netlist.xml")
    run([KCLI, "sch", "erc", "--format", "json", "--severity-all", "-o", erc_json, root], cwd=hw, check=False)
    run([KCLI, "sch", "export", "netlist", "--format", "kicadxml", "-o", xml, root], cwd=hw)
    run([KCLI, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", os.path.join(out, project + ".net"), root], cwd=hw)
    for f in (xml, os.path.join(out, project + ".net")):     # kicad-cli writes the absolute source path: keep it relative
        t = open(f, encoding="utf-8").read()
        open(f, "w", encoding="utf-8").write(t.replace(REPO + os.sep, ""))
    check_erc(erc_json, R, waivers)
    check_netlist_identity(B.D, xml, R)
    pdf = os.path.join(out, project + "_schematic.pdf")
    run([KCLI, "sch", "export", "pdf", "-o", pdf, root], cwd=hw)
    R.add("Schematic PDF rendered", os.path.getsize(pdf) > 10000, os.path.relpath(pdf, REPO))
    write_bom(B, os.path.join(REPO, "bom", project + "_BOM.csv"), R)
    write_symbol_pins(B, os.path.join(out, project + "_symbol_pins.csv"))
    ok = all(r["result"] == "pass" for r in R)
    json.dump({"project": project, "rev": B.D.rev, "date": B.D.date, "sheets": len(B.D.sheets),
               "parts": len([r for r in B.D.parts if not r.startswith("#")]), "nets": len(B.D.nets()),
               "erc_waivers": waivers or {}, "passed": ok, "checks": R},
              open(os.path.join(out, project + "_report.json"), "w"), indent=2)
    print("%s: %s" % (project, "ALL CHECKS PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        lib, name = arg.split(":")
        print(arg)
        for p in symlib.symbol_pins(symlib.kicad_symbol(lib, name, name)):
            print("  pin %-4s %-12s %s" % (p.number, p.name, p.etype))
