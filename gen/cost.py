"""Costed BOM per module at catalogue prices and at a 5,000-unit build, cost per kW, comparison with the owner's price
benchmark (DEL-8, SRC-1...SRC-6, AC-03).

Run:  .venv/bin/python gen/cost.py
In :  bom/<module>_module_BOM.csv  gen/data/prices.csv  gen/data/cost_estimates.csv  gen/data/volume_factors.csv
      gen/data/market_prices.csv  gen/data/costfirst_bom.csv  gen/data/costfirst_pcs_bom.csv (the architect's lists, for comparison)
Out:  bom/<module>_costed_BOM.csv  bom/COST.md          (generated, do not edit)
Exit code 1 if a self-check fails.

Every cost is a looked-up price (prices.csv), a stated estimate (cost_estimates.csv) or an explicit gap: a line with neither is
listed as UNPRICED and carries no cost; it is never counted as zero. The 5,000-unit price of a line is a published price at a break of
1,000 pieces or more (REAL) or the catalogue price times a factor of volume_factors.csv (ASSUMED). Nothing here is a quote or
bench-validated.
"""
import collections
import csv
import fnmatch
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
sys.path.insert(0, HERE)

MODULES = {"PV-P75": [75.0], "PV-P100-110": [100.0, 110.0], "PCS-P125": [125.0],   # cost-first baseline (D-044, D-061)
           "PV-P75-FULL": [75.0], "PV-P100-110-FULL": [100.0, 110.0], "DAB-D60-FULL": [60.0]}   # rated kW (REQUIREMENTS PV-01, PV-15, DAB-01)
MODULE_AMPS = {"PV-P75": 135.0, "PV-P100-110": 180.0, "PCS-P125": 250.0, "PV-P75-FULL": 135.0, "PV-P100-110-FULL": 180.0, "DAB-D60-FULL": 135.0}   # rated port current (PV-05/PV-08, PV-PORT-180, DAB port fuses)
QTY_CAP = 1000          # catalogue price: the break at the highest quantity at or below this many pieces
BUILD_UNITS = 5000      # SRC-6: the volume basis; the annual quantity of a line is BUILD_UNITS x its per-module quantity
EVIDENCE_MIN_BREAK = 1000   # a 5,000-unit price is REAL only if it is a published price at a break of at least this many pieces
# ECB euro foreign-exchange reference rates of 2026-10-02, units per EUR (CNY per USD = 7.5259 / 1.1225 = 6.705)
ECB = {"EUR": 1.0, "USD": 1.1225, "CNY": 7.5259, "GBP": 0.85033, "HKD": 8.8084, "JPY": 176.99, "PLN": 4.3775, "CHF": 0.9279,
       "INR": 108.1245, "KRW": 1513.44, "AUD": 1.6176, "SGD": 1.4366, "CAD": 1.5984, "DKK": 7.4736, "SEK": 11.29, "NOK": 10.8315,
       "CZK": 24.47, "NZD": 2.0002, "MYR": 4.5849, "THB": 37.71}
FX_DATE = "2026-10-02"
# The owner's benchmark (SRC-5, AC-03): Megarevo MPHV 1500 V string PCS, 228 kW, 630 V AC, DC 914-1500 V, sells for 1,500 USD.
BENCH_MARGIN = 0.25         # assumed gross margin of the benchmark's maker (UUGreenPower domestic charging-module margins 20-27 % 2021-H1 2024, nbd.com.cn 2023-08-30,
                            # stockstar.com 2025-05-20; Economic Observer 2026-07-14: industry margin squeezed from 25-30 % to 15-10 %)
BOM_SHARE_OF_COGS = 0.80    # assumed: the BOM is this share of the cost of goods sold (the rest: assembly, test, warranty, depreciation)
BENCH_DC_A_PER_KW = 1.16    # benchmark current per kW on the DC side: 1.09 A/kW at 914 V up to 1.23 A/kW (281 A max) at 228 kW; middle value (ARCHITECTURE-PCS section 12)
CURRENT_SHARE = 0.70        # share of a BOM that scales with current rather than power: semiconductors, inductors, contactors, fuses, busbars, film banks
GROSS_MARGINS = (0.15, 0.25, 0.30)   # margin range for the weaker reference (marketplace listings)
BENCH_MIN_KW = 30           # marketplace rows below this power are not a reference for a 60-110 kW module
TOP_N = 25
DISTRIBUTORS = ("digikey", "digi-key", "mouser", "farnell", "element14", "newark", "rs components", "rs-online", "arrow", "avnet",
                "tme", "rutronik", "distrelec", "findchips", "richardson", "aggregator", "broker", "cytech", "future electronics",
                "webshop", "retailer", "rapid electronics", "onlinecomponents", "verical", "octopart", "ickey", "oemstrade", "master electronics")


def usd(x, cur):
    return x / ECB[cur] * ECB["USD"]


def rank(source):
    """0 = LCSC, 1 = manufacturer site, 2 = distributor / aggregator / broker"""
    s = source.lower()
    return 0 if s.startswith("lcsc") else 2 if any(k in s for k in DISTRIBUTORS) else 1


# ---------------------------------------------------------------------------------------------- reading
def rd(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def load_prices():
    book, by_sku, skipped = collections.defaultdict(list), collections.defaultdict(list), []
    for r in rd(os.path.join(DATA, "prices.csv")):
        try:
            p, cur = float(r["unit_price"]), (r["currency"].strip().upper() or "USD")
            q = int(float(r["qty"])) if r["qty"].strip() else 1
            e = {"src": r["source"], "qty": q, "known": bool(r["qty"].strip()), "usd": usd(p, cur), "sku": r["sku"], "url": r["url"]}
        except (ValueError, KeyError):      # source "none", empty price or an unknown currency: no price on this row
            if r["unit_price"].strip():
                skipped.append("%s (%s %s)" % (r["mpn"], r["unit_price"], r["currency"]))
            continue
        book[r["mpn"].strip().upper()].append(e)
        if r["sku"].strip():
            by_sku[r["sku"].strip().upper()].append(e)
    return book, by_sku, skipped


def load_estimates():
    return {r["key"].strip(): (float(r["unit_cost_usd"]), r["basis"], r["confidence"].strip().lower()) for r in rd(os.path.join(DATA, "cost_estimates.csv"))}


def load_factors():
    return {r["class"].strip(): (float(r["factor"]), r["basis"], r["confidence"].strip().lower()) for r in rd(os.path.join(DATA, "volume_factors.csv"))}


def candidates(rows, cap):
    """one candidate per listing (source + catalogue number): (rank, USD, row) at the highest break <= cap (the lowest break if none)"""
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["src"], r["sku"])].append(r)
    out = []
    for rs in by.values():
        ok = [r for r in rs if r["qty"] <= cap]
        pick = max(ok, key=lambda r: r["qty"]) if ok else min(rs, key=lambda r: r["qty"])
        out.append((rank(pick["src"]), pick["usd"], pick))
    return out


def best_price(rows):
    """Catalogue price: best source = LCSC, then manufacturer, then distributor, cheapest within a class. One refinement of that order: a
    single-piece price (break < 10, or not stated) loses to any source that has a real volume break (>= 100), because a one-off price is
    not what a production buyer pays."""
    c = candidates(rows, QTY_CAP)
    vol = any(x[2]["qty"] >= 100 for x in c)
    return min(c, key=lambda x: (vol and x[2]["qty"] < 10, x[0], x[1]))


# ---------------------------------------------------------------------------------------------- classes and groups
UNITS = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3}


def farads(tok):
    m = re.match(r"^(\d+(?:\.\d+)?)([pnum])", tok)
    return float(m.group(1)) * UNITS[m.group(2)] if m else None


def volts(text):
    v = [float(m.group(1)) * (1000 if m.group(2) == "kV" else 1) for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(kV|V)\b", text)]
    return max(v) if v else None


def generic_class(row):
    """class key of a GENERIC chip resistor / ceramic capacitor line (see cost_estimates.csv class: rows), or None"""
    d, pkg = row["Description"], row["Package"].strip()
    if d.startswith("Resistor"):
        p = re.sub(r"\s+", "", pkg.lower())
        tol = re.search(r"(\d+(?:\.\d+)?)\s*%", d)
        k = "res_%s_%s" % ("melf0204" if "melf" in p else p, "0.1pct" if tol and float(tol.group(1)) <= 0.1 else "1pct")
        k += "_10ppm" if k.endswith("0.1pct") and re.search(r"10\s*ppm", d) else ""
        return "class:" + k + ("_pulse" if "pulse" in d.lower() else "")
    if d.startswith("Capacitor"):
        f, v = farads(row["Value"].split()[0]) or farads(d.split()[1]), volts(row["Value"] + " " + d)
        m = re.search(r"\b(X7R|X5R|X7S|X6S|C0G|NP0|Y5V)\b", d)
        if f and v and m:
            vb = next((n for lim, n in ((10, "10v"), (16, "16v"), (25, "25v"), (50, "50v"), (100, "100v"), (630, "630v"), (1000, "1kv"), (2000, "2kv")) if v <= lim), "3kv")
            cb = next((n for lim, n in ((1.0001e-9, "le1n"), (1.0001e-7, "le100n"), (1.0001e-6, "le1u"), (1.0001e-5, "le10u")) if f <= lim), "gt10u")
            return "class:mlcc_%s_%s_%s_%s" % (pkg, "c0g" if m.group(1) in ("C0G", "NP0") else m.group(1).lower(), vb, cb)
    return None


G_POWER, G_GATE, G_MAG, G_CAP, G_PROT, G_SENSE, G_CTRL, G_AUX, G_THERM, G_CONN, G_RES = (
    "power semiconductors", "gate drive", "magnetics", "capacitors", "protection (fuses, contactors, SPD, TVS)", "sensing",
    "control + communications", "auxiliary supply", "thermal", "connectors + harness", "resistors + other board parts")
GROUPS = [G_POWER, G_GATE, G_MAG, G_CAP, G_PROT, G_SENSE, G_CTRL, G_AUX, G_THERM, G_CONN, G_RES]
LEAD = re.compile(r"^(?:(?:CHASSIS|COLD-PLATE|BUSBAR|RESISTOR|PIPE|DIN-RAIL|OFF-BOARD|CHASSIS-MOUNTED|COLD-PLATE MOUNTED|BUSBAR-MOUNTED|"
                   r"RESISTOR-MOUNTED|PIPE-MOUNTED)[A-Za-z\- ]*?(?:\([a-z ]+\))?:?\s+)*(?:RFQ |CUSTOM )*")
RULES = [   # (group, regex on the descriptor = Description without its leading mounting qualifiers); first match wins
    (G_POWER, r"^(SiC |N-MOSFET 1[2-9]\d\d|IGBT|Rectifier 1[2-9]00|dual rectifier diode module|diode module|full-bridge)"),
    (G_THERM, r"^(Fan |Gravity backflow|Plate-fin|Liquid cold plate|low-level bimetal|bimetal thermostat|reed flow switch|heatsink|Finger guard|TO-247 heatsink INSULATOR)"),
    (G_MAG, r"^(flyback transformer|series inductor|HF power transformer|common-mode choke|power inductor|Power inductor|Push-pull transformer \d|Ferrite bead|bolted busbar link|AUX-T1|Gate-bias transformer|nanocrystalline ring core)"),
    (G_PROT, r"^(fuse|Fuse|TVS|DC terminal|main contactor|SPD|PV surge|DC SPD|Precharge relay|Varistor|Y1 capacitor|2-channel 24 V CAN bus ESD|RS-485 asymmetric TVS|precharge resistor|discharge resistor|DC surge|aR fuse|Isolated 1700 V back-to-back|Thermally protected varistor|Relay|Contactor|[A-Za-z0-9 ]*contactor|Hongfa)|SPD backup fuse|surge arrester|varistor"),
    (G_SENSE, r"^(shunt|Closed-loop Hall|Open-loop|Reinforced isolated amplifier|HV thin-film resistor|HV chip resistor|Thin-film resistor|NTC probe|NTC lug|NTC 10 k|shunt NTC|I2C temperature sensor|Shunt NTC|Current sense resistor|NTC leads)"),
    (G_CONN, r"^(PCB header|Pluggable header|Shrouded box header|Socket strip|Header |cTI-20|Board-to-board|Host header|SDFM expansion|Debug UART|Service jumper|Control connector|PORT connector|HV wire terminal|PCB HV connection|RJ45|\d+-pole|\d+-way|Port A and port B|PORT 1 ONLY|AUX-HV to SYS-IO-AUX|PV-PORT control connector|DAB60 control connector|A_BUS|Fan header|SELV 24 V|REDCUBE|Press-fit|Ring lug)"),
    (G_CAP, r"^(Capacitor|DC-link PP film|PP film|Film capacitor|Aluminium electrolytic|Aluminium polymer|X capacitor film|snubber MLCC|DC-link terminal MLCC)"),
    (G_GATE, r"gate clamp|DESAT|Miller|[Ii]solated (SiC )?gate driver|isolated switch driver|Isolated DC/DC module 24 V in, 2 W|gate-supply|gate supply|each gate|SC trigger|SC booster|booster gate|clamp release|clamp inverter|pass transistor of the per-channel|sense-node clamp|VDD follower|VDD / gate|SiC gate drive|NSI6651"),
    (G_AUX, r"^(Sync(hronous)? buck|Buck |LDO|eFuse|Ideal-diode|Push-pull transformer driver|Current-mode PWM|DC/DC 3 W|Isolated DC/DC 500 mW|Rectifier|Ultrafast rectifier|Avalanche rectifier|Schottky rectifier 60 V|Optocoupler)|bootstrap|start-up"),
    (G_CTRL, r"^(TMS320|TMS|16-ch|Quad RS-422|10/100 Ethernet|Isolated CAN|Isolated (24-60|half-duplex)|Reinforced 2-ch|Dual (comparator|RRIO|zero-drift|4\.5 ns|45 mOhm)|Quad (RRIO|zero-drift|2-input|RS|comparator)|Single (2-input|3-input|Schmitt)|Triple 3-input|D flip-flop|Octal buffer|Window|UV supervisor|Supervisor|Voltage (reference|detector)|Precision reference|Shunt reference|8:1 analog|RS-485 transceiver|256 kbit|EEPROM|F-RAM|16-bit SPI|MEMS oscillator|SPXO|LED|Tact switch|Hex buffer|Retriggerable|C2000|[A-Za-z0-9 ]*MCU)"),
]
BOARD_DEFAULT = {"CTRL-C2000": G_CTRL, "BMU-GW": G_CTRL, "SYS-IO-AUX": G_CTRL, "AUX-HV": G_AUX, "AUX-HV_DAB": G_AUX, "PVCELL-25": G_GATE,
                 "DAB60": G_GATE, "PV-PORT": G_PROT, "PV-PORT-180": G_PROT, "PV-CTL": G_CTRL, "PV-PWR": G_GATE, "PV-PWR-4": G_GATE}
NAMED = (("cell harness", G_CONN), ("port harness", G_CONN), ("coil/feedback harness", G_CONN), ("J801 link plug", G_CONN), ("busbar", G_CONN),
         ("AUX-HV input harness", G_CONN), ("AUX-HV output harness", G_CONN), ("harness set", G_CONN), ("heatsink", G_THERM), ("cold plate", G_THERM),
         ("backflow shutter", G_THERM), ("120 mm wire guard", G_THERM), ("PAD AlN", G_THERM), ("PA66 standoff", G_CONN), ("DC terminal", G_PROT),
         ("Gate-bias transformer", G_MAG), ("NTC 10k lug probe", G_SENSE))


def boards_of(used):
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"([A-Za-z0-9_\-]+): (\d+)", used)}


def group_of(row):
    """function group of a BOM line: Value or descriptor keywords first, then the board that carries most of the line"""
    for pre, g in NAMED:
        if row["Value"].startswith(pre):
            return g
    d = re.sub(r"^((RFQ|CUSTOM|MOUNTED)\s+)+", "", LEAD.sub("", row["Description"]).strip())
    if d.startswith("Resistor"):
        return G_RES
    b = boards_of(row["Used on"])
    dom = max(b, key=b.get) if b else ""
    g = next((gg for gg, rx in RULES if re.search(rx, d)), None)
    if dom in ("AUX-HV", "AUX-HV_DAB") and g in (None, G_POWER, G_CTRL, G_GATE):
        return G_AUX
    return g or BOARD_DEFAULT.get(dom, G_RES)


# ---------------------------------------------------------------------------------------------- pricing a line
def find_estimate(row, module, est):
    mpn, val = row["MPN"].strip(), row["Value"].strip()
    for k in ([mpn + "@" + module, mpn] if mpn else []) + [val + "@" + module, val]:
        if k in est:
            return k, est[k]
    for k in est:
        if ("*" in k or "?" in k) and not k.startswith(("class:", "outside:")) and (fnmatch.fnmatchcase(val, k) or (mpn and fnmatch.fnmatchcase(mpn, k))):
            return k, est[k]
    if row["Sourcing"] == "GENERIC":
        c = generic_class(row)
        if c in est:
            return c, est[c]
    return None


MAG_SMALL = re.compile(r"AUX-T1|AUX-HV-T1|Gate-bias|T_BIAS|GDB-T1")


def volume_class(row):
    """part class of an ESTIMATED line for the factor table volume_factors.csv"""
    v, g = row["Value"], group_of(row)
    if g == G_MAG and (row["Sourcing"] == "CUSTOM" or row["MPN"].startswith("N-C-")):
        return "custom_magnetic_small" if MAG_SMALL.search(v) else "custom_magnetic_large"
    if row["Sourcing"] in ("CUSTOM", "GENERIC"):
        return "custom_harness" if "harness" in v.lower() else "ceramic_pad" if v.startswith("PAD AlN") else "custom_mech"
    return "protection_estimate" if g == G_PROT else "asia_part_estimate"


def short(text, n):
    return text if len(text) <= n else text[:n - 3].rstrip() + "..."


def price_line(row, module, book, by_sku, est, fac):
    """-> dict: status (nopart / priced / estimated / unpriced), catalogue unit price, its source and confidence, and the 5,000-unit
    unit price with its basis (real = a published price at a break >= EVIDENCE_MIN_BREAK, else catalogue price x factor)"""
    q = int(row["Qty"])
    annual = BUILD_UNITS * q
    out = {"status": "unpriced", "unit": None, "src": "UNPRICED", "conf": "none", "unit5": None, "basis5": "UNPRICED", "real5": False, "fclass": ""}
    if row["Sourcing"] == "NOPART":
        out.update(status="nopart", unit=0.0, src="not a purchased part", conf="", unit5=0.0, basis5="not a purchased part")
        return out

    def assumed(unit, cls):
        f = fac.get(cls, (1.0, "no factor row for this class", ""))[0]
        out.update(unit5=unit * f, basis5="ASSUMED x%.2f [%s]" % (f, cls), fclass=cls)

    mpn = row["MPN"].strip().upper()
    if mpn and mpn in book:
        rows = book[mpn]
        rk, price, pick = best_price(rows)
        out.update(status="priced", unit=price, conf="high" if rk < 2 and "snippet" not in pick["src"].lower() else "medium",
                   src="%s %s %s (2026-10-04)%s" % (pick["src"], pick["sku"], "@%d+" % pick["qty"] if pick["known"] else "break not stated", " single-piece price" if pick["qty"] < 10 else ""))
        ev = [c for c in candidates(rows, annual) if c[2]["qty"] >= EVIDENCE_MIN_BREAK]
        if ev:
            _, p5, pk5 = min(ev, key=lambda c: (c[0], c[1]))
            if p5 <= price:
                out.update(unit5=p5, basis5="REAL: %s %s @%d+ break (annual qty %d)" % (pk5["src"], pk5["sku"], pk5["qty"], annual), real5=True)
            else:
                out.update(unit5=price, basis5="REAL: the catalogue price is already the lowest published (%s %s)" % (pick["src"], pick["sku"]), real5=True)
        else:
            assumed(price, "single_piece_list" if pick["qty"] < 10 else "short_table")
        return out
    e = find_estimate(row, module, est)
    if not e:
        return out
    key, (u, basis, conf) = e
    out.update(status="estimated", unit=u, conf=conf, src="estimate %s: %s" % (key, short(basis, 140)))
    m = re.search(r"LCSC (C\d+)", basis) if key.startswith("class:") else None
    ex = [r for r in by_sku.get(m.group(1).upper(), []) if rank(r["src"]) == 0 and r["qty"] <= annual] if m else []
    top = max(ex, key=lambda r: r["qty"]) if ex else None
    if top and top["qty"] >= EVIDENCE_MIN_BREAK:
        out.update(unit5=min(u, top["usd"]), basis5="REAL: class example LCSC %s @%d+ break (annual qty %d)" % (m.group(1), top["qty"], annual), real5=True)
    else:
        assumed(u, "chip_passive_class" if key.startswith("class:") else volume_class(row))
    return out


def outside_items(module, rows, est, fac, n_boards):
    """Items outside the BOM (bare PCBs, assembly, enclosure, test), from the outside: rows of cost_estimates.csv.
    -> [(label, USD catalogue, USD at 5,000 units, assumption)]"""
    def par(name):
        return est.get("outside:%s@%s" % (name, module)) or est.get("outside:" + name)

    def f(cls):
        return fac.get(cls, (1.0,))[0]
    parts = collections.Counter()
    for r in rows:
        if r["Sourcing"] != "NOPART":
            for b, n in boards_of(r["Used on"]).items():
                if b not in ("chassis", "harness"):
                    parts[b] += n
    out, tot_boards = [], 0
    area, pcb_cm2, place, fixed = par("pcb_cm2_per_part"), par("pcb_usd_per_cm2"), par("smt_placement"), par("assembly_fixed_per_board")
    for b in sorted(parts):
        n = n_boards.get(b, 1)
        tot_boards += n
        fixed_pcb, mult = par("pcb:" + b), par("pcb_mult:" + b)
        if fixed_pcb:
            c, note = n * fixed_pcb[0], "%d x %.0f USD per bare board: %s" % (n, fixed_pcb[0], short(fixed_pcb[1], 110))
        elif area and pcb_cm2:
            cm2, k = parts[b] / n * area[0], mult[0] if mult else 1.0
            c, note = n * cm2 * pcb_cm2[0] * k, "%d parts per board x %.2f cm2 = %.0f cm2 x %.3f USD/cm2%s" % (parts[b] / n, area[0], cm2, pcb_cm2[0], " x %g (6-layer heavy copper)" % k if mult else "")
        else:
            continue
        out.append(("bare PCB %s x%d" % (b, n), c, c * f("outside_pcb"), note))
    if place and fixed:
        c = sum(parts.values()) * place[0] + tot_boards * fixed[0]
        out.append(("board assembly (%d placements, %d boards)" % (sum(parts.values()), tot_boards), c, c * f("outside_assembly"),
                    "%.3f USD per placement + %.1f USD per board" % (place[0], fixed[0])))
    for name, label, cls in (("enclosure", "enclosure / sheet metal / packaging", "outside_enclosure"), ("final_assembly_test", "final assembly, test and burn-in", "outside_test")):
        if par(name):
            out.append((label, par(name)[0], par(name)[0] * f(cls), "see cost_estimates.csv outside:" + name))
    return out


def total(lines, key="ext"):
    return sum(ln[key] for ln in lines if ln[key] is not None)


# ---------------------------------------------------------------------------------------------- market prices
def load_market():
    rows = []
    for r in rd(os.path.join(DATA, "market_prices.csv")):
        try:
            p, cur, kw = float(r["price"]), r["currency"].strip().upper(), r["power_kW"].strip()
            if r["unit"].strip() == "per piece" and kw:
                per_kw = usd(p, cur) / float(kw)
            elif r["unit"].strip() == "per W":
                per_kw = usd(p, cur) * 1000
            else:
                continue
        except (ValueError, KeyError):
            continue
        t = r["type"].lower()
        cat = ("benchmark" if "string pcs" in t else "per-watt figure of a report (AC/DC EV module)" if "per watt" in t else
               "bidirectional DC/DC module" if "bidirectional" in t and "dc/dc" in t else "DC/DC module" if "dc/dc" in t else "AC/DC EV-charging module (reference only)")
        rows.append({"maker": r["maker"], "product": r["product"], "kw": float(kw) if kw else None, "type": r["type"], "per_kw": per_kw, "cat": cat,
                     "url": r["url"], "price": "%s %s %s" % (r["price"], r["currency"], r["unit"]), "usd": usd(p, cur) if r["unit"].strip() == "per piece" else None})
    return rows


# ---------------------------------------------------------------------------------------------- main
def main():
    book, by_sku, skipped = load_prices()
    est, fac = load_estimates(), load_factors()
    try:
        from build_all import MODULES as BOARDS      # boards per module, so PCB counts follow the generator
    except Exception:                                # no board list: every board type counts once (COST.md says 'board list unavailable')
        BOARDS = {}
    results = {}
    for module in MODULES:
        rows = rd(os.path.join(REPO, "bom", module + "_module_BOM.csv"))
        lines = []
        for r in rows:
            p = price_line(r, module, book, by_sku, est, fac)
            q = int(r["Qty"])
            unit = None if p["unit"] is None else round(p["unit"], 6)
            unit5 = None if p["unit5"] is None else round(p["unit5"], 6)
            lines.append(dict(p, row=r, unit=unit, unit5=unit5, qty=q, group=group_of(r),
                              ext=None if unit is None else round(unit * q, 4), ext5=None if unit5 is None else round(unit5 * q, 4)))
        with open(os.path.join(REPO, "bom", module + "_costed_BOM.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(list(rows[0].keys()) + ["unit_cost_usd", "ext_cost_usd", "price_source", "confidence", "unit_cost_5k_usd", "basis_5k"])
            for ln in lines:
                w.writerow(list(ln["row"].values()) + ["" if ln["unit"] is None else "%.6f" % ln["unit"], "" if ln["ext"] is None else "%.4f" % ln["ext"], ln["src"], ln["conf"],
                                                       "" if ln["unit5"] is None else "%.6f" % ln["unit5"], ln["basis5"]])
        results[module] = {"rows": rows, "lines": lines, "outside": outside_items(module, rows, est, fac, BOARDS.get(module, {})), "boards_known": module in BOARDS}
    md = report(results, load_market(), skipped, fac)
    open(os.path.join(REPO, "bom", "COST.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    self_check(results, fac)
    for module, res in results.items():
        t, t5, n = total(res["lines"]), total(res["lines"], "ext5"), sum(1 for l in res["lines"] if l["status"] == "unpriced")
        print("%-17s BOM %8.2f USD catalogue, %8.2f USD at 5,000 units (%s)%s" % (module, t, t5, ", ".join("%.1f / %.1f USD per kW at %g kW" % (t / kw, t5 / kw, kw) for kw in MODULES[module]),
                                                                                   "  ** %d UNPRICED line(s), see bom/COST.md **" % n if n else ""))
    print("ALL CHECKS PASSED  ->  bom/COST.md, bom/*_costed_BOM.csv")


# ---------------------------------------------------------------------------------------------- report
def money(x):
    return "%.2f" % x


def label(m):
    return m + (" (old platform, for contrast)" if m.endswith("-FULL") else "")


def report(results, market, skipped, fac):
    md = ["# Cost model - generated by gen/cost.py (do not edit)", "",
          "**Honesty boundary.** No part has been bought or quoted. A *priced* line has a looked-up price (gen/data/prices.csv, URL and date 2026-10-04); an *estimated* line has "
          "a stated engineering basis (gen/data/cost_estimates.csv); an *unpriced* line has neither and is listed, never counted as zero. All prices are single-source "
          "web readings, not contracts. Cost per kW uses the rated power of the product (PV-01, PV-15, DAB-01). The modules PV-P75 and PV-P100-110 are the cost-first "
          "baseline (D-044/D-045: boards PV-PWR / PV-PWR-4 and PV-CTL); the modules ending in -FULL are the roadmap's earlier platform, kept for contrast.", "",
          "**Catalogue price** = the break at the highest quantity <= %d pieces from the best source (LCSC first, then the maker's own site, then distributors, brokers and "
          "aggregators; cheapest within a class; a one-piece price loses to any source that has a volume break). Currencies at the ECB rates of %s (1 EUR = %.4f USD = %.4f CNY, "
          "so 1 USD = %.3f CNY). Chip passives use reel-price class rules; custom parts use engineering estimates (copper and aluminium at LME cash of 2026-10-02: 14,355 and "
          "3,109.5 USD/t). Prices marked 'single-piece price' are catalogue prices for one piece. A price row can belong to an equivalent part of another maker with the same "
          "type designation (its note in prices.csv says so). Confidence: high = LCSC or maker price read from its page; medium = distributor/broker price or an estimate pinned "
          "to a close equivalent; low = engineering estimate." % (QTY_CAP, FX_DATE, ECB["USD"], ECB["CNY"], ECB["CNY"] / ECB["USD"]), "",
          "**Price at a %d-unit build** (SRC-6; the annual quantity of a line is %d x its quantity per module). REAL = a published price of the part at a break of at least %d "
          "pieces and at most the annual quantity (LCSC highest break, TI.com 1ku list price, a distributor's volume tier, or the highest break of the LCSC listing that a "
          "chip-passive class is built on). ASSUMED = the catalogue price times a factor of gen/data/volume_factors.csv, chosen by part class, with its basis written out there. "
          "A REAL price is never above the catalogue price, and the highest published break can still be above what a 5,000-unit buyer would be quoted: no quote exists." % (
              BUILD_UNITS, BUILD_UNITS, EVIDENCE_MIN_BREAK), ""]
    if skipped:
        md += ["Rows of prices.csv skipped for an unknown currency: %s." % "; ".join(skipped[:10]), ""]
    md += ["## Summary", "",
           "| module | rated kW | BOM catalogue USD | USD/kW | BOM at %d units USD | USD/kW | 5k total REAL %% | 5k total ASSUMED %% | catalogue total on engineering estimates %% | unpriced lines | BOM + outside, catalogue USD/kW | BOM + outside, %d units USD/kW |" % (BUILD_UNITS, BUILD_UNITS),
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m, res in results.items():
        lines = res["lines"]
        t, t5 = total(lines), total(lines, "ext5")
        real = sum(l["ext5"] for l in lines if l["ext5"] and l["real5"])
        ou, ou5 = sum(o[1] for o in res["outside"]), sum(o[2] for o in res["outside"])
        eng = sum(l["ext"] for l in lines if l["status"] == "estimated" and not l["src"].startswith("estimate class:"))
        for kw in MODULES[m]:
            md.append("| %s | %g | %s | %.1f | %s | %.1f | %.0f | %.0f | %.0f | %d | %.1f | %.1f |" % (label(m), kw, money(t), t / kw, money(t5), t5 / kw, 100 * real / t5 if t5 else 0,
                                                                                         100 * (t5 - real) / t5 if t5 else 0, 100 * eng / t if t else 0,
                                                                                         sum(1 for l in lines if l["status"] == "unpriced"), (t + ou) / kw, (t5 + ou5) / kw))
    lower = ["%s: %d line(s), %d parts" % (label(m), sum(1 for l in r["lines"] if l["status"] == "unpriced"), sum(l["qty"] for l in r["lines"] if l["status"] == "unpriced"))
             for m, r in results.items() if any(l["status"] == "unpriced" for l in r["lines"])]
    md += [""] + (["Unpriced lines carry no cost, so the totals of these modules are lower bounds: %s (listed under each module)." % "; ".join(lower), ""] if lower else [])
    for m, res in results.items():
        md += module_section(m, res, fac)
    md += benchmark_section(results, market)
    md += pcs_section(results)
    md += reconcile_section(results)
    return md


def module_section(m, res, fac):
    lines = res["lines"]
    t, t5 = total(lines), total(lines, "ext5")
    nonp = [l for l in lines if l["status"] != "nopart"]
    md = ["## %s" % label(m), "", "Total BOM cost **%s USD at catalogue prices = %s**; **%s USD at a %d-unit build = %s**. Lines: %d (%d purchased-part lines; %d NOPART lines such as "
          "test points and net ties are not costed)." % (money(t), " / ".join("%.1f USD per kW at %g kW" % (t / kw, kw) for kw in MODULES[m]), money(t5), BUILD_UNITS,
                                                         " / ".join("%.1f USD per kW at %g kW" % (t5 / kw, kw) for kw in MODULES[m]), len(lines), len(nonp), len(lines) - len(nonp)), ""]
    un = [l for l in lines if l["status"] == "unpriced"]
    md += ["### Evidence behind the catalogue total", "", "| basis | lines | parts | USD | share of the costed total |", "|---|---|---|---|---|"]
    for lab, sel in (("real price of the part (looked up)", [l for l in nonp if l["status"] == "priced"]),
                     ("chip-passive class rule (price of a real LCSC example part of the same class)", [l for l in nonp if l["src"].startswith("estimate class:")]),
                     ("engineering estimate with stated basis", [l for l in nonp if l["status"] == "estimated" and not l["src"].startswith("estimate class:")])):
        val = sum(l["ext"] for l in sel)
        md.append("| %s | %d | %d | %s | %.1f %% |" % (lab, len(sel), sum(l["qty"] for l in sel), money(val), 100 * val / t if t else 0))
    md.append("| no price at all (UNPRICED, not in the total) | %d | %d | not known | not in the total |" % (len(un), sum(l["qty"] for l in un)))
    # 5,000-unit view
    real = [l for l in lines if l["ext5"] and l["real5"]]
    ass = [l for l in lines if l["ext5"] and not l["real5"] and l["status"] != "nopart"]
    r5, a5 = sum(l["ext5"] for l in real), sum(l["ext5"] for l in ass)
    md += ["", "### The %d-unit view" % BUILD_UNITS, "", "| basis of the %d-unit price | lines | USD at %d units | share of the %d-unit total |" % (BUILD_UNITS, BUILD_UNITS, BUILD_UNITS), "|---|---|---|---|",
           "| REAL: a published price at a break >= %d pieces | %d | %s | %.1f %% |" % (EVIDENCE_MIN_BREAK, len(real), money(r5), 100 * r5 / t5 if t5 else 0),
           "| ASSUMED: catalogue price x factor of volume_factors.csv | %d | %s | %.1f %% |" % (len(ass), money(a5), 100 * a5 / t5 if t5 else 0), "",
           "The %d-unit total is %.1f %% of the catalogue total." % (BUILD_UNITS, 100 * t5 / t if t else 0), "",
           "| factor class used | factor | lines | catalogue USD | USD at %d units | confidence |" % BUILD_UNITS, "|---|---|---|---|---|---|"]
    by = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for l in ass:
        by[l["fclass"]][0] += 1
        by[l["fclass"]][1] += l["ext"]
        by[l["fclass"]][2] += l["ext5"]
    for c, (n, a, b) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        md.append("| %s | %s | %d | %s | %s | %s |" % (c, "%.2f" % fac[c][0] if c in fac else "missing", n, money(a), money(b), fac[c][2] if c in fac else "-"))
    # groups
    g = collections.defaultdict(lambda: [0.0, 0.0, 0, 0])
    for l in lines:
        if l["ext"] is not None and l["status"] != "nopart":
            g[l["group"]][0] += l["ext"]
            g[l["group"]][1] += l["ext5"]
            g[l["group"]][2] += 1
            g[l["group"]][3] += 1 if l["status"] != "priced" else 0
    md += ["", "### Split by function group", "", "Groups are assigned by rule from the line's description and the board that carries it (approximate; see group_of in gen/cost.py).", "",
           "| group | catalogue USD | %% of BOM | USD at %d units | lines | of which estimated |" % BUILD_UNITS, "|---|---|---|---|---|---|"]
    for name in sorted(GROUPS, key=lambda n: -g[n][0]):
        md.append("| %s | %s | %.1f | %s | %d | %d |" % (name, money(g[name][0]), 100 * g[name][0] / t if t else 0, money(g[name][1]), g[name][2], g[name][3]))
    ou, ou5 = sum(o[1] for o in res["outside"]), sum(o[2] for o in res["outside"])
    md += ["| **total BOM** | **%s** | 100 | **%s** | %d | |" % (money(t), money(t5), sum(v[2] for v in g.values())),
           "| PCB / mechanics outside the BOM (rough, not in the BOM total) | %s | | %s | | all assumed |" % (money(ou), money(ou5)), ""]
    md += ["### Outside the BOM (rough figures, not part of the BOM total)", ""]
    if not res["boards_known"]:
        md += ["Board list unavailable (gen/build_all.py MODULES not importable): bare PCBs counted as one per board type.", ""]
    md += ["| item | catalogue USD | USD at %d units | assumption (see gen/data/cost_estimates.csv outside: rows) |" % BUILD_UNITS, "|---|---|---|---|"]
    md += ["| %s | %s | %s | %s |" % (n, money(c), money(c5), a) for n, c, c5, a in res["outside"]]
    md += ["| **BOM + outside, rough** | **%s** (%s) | **%s** (%s) | |" % (money(t + ou), " / ".join("%.1f USD per kW at %g kW" % ((t + ou) / kw, kw) for kw in MODULES[m]),
                                                                          money(t5 + ou5), " / ".join("%.1f USD per kW at %g kW" % ((t5 + ou5) / kw, kw) for kw in MODULES[m])), ""]
    md += ["### Top %d cost lines (catalogue)" % TOP_N, "", "| # | BOM item | qty | value / MPN | unit USD | ext USD | %% of BOM | unit USD at %d | basis at %d units | basis (catalogue) | conf |" % (BUILD_UNITS, BUILD_UNITS), "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, l in enumerate(sorted((l for l in lines if l["ext"]), key=lambda l: -l["ext"])[:TOP_N], 1):
        r = l["row"]
        md.append("| %d | %s | %d | %s | %s | %s | %.1f | %s | %s | %s | %s |" % (i, r["Item"], l["qty"], (r["MPN"] or r["Value"])[:34], "%.4g" % l["unit"], money(l["ext"]), 100 * l["ext"] / t,
                                                                            "%.4g" % l["unit5"], l["basis5"][:70].replace("|", "/"), l["src"][:80].replace("|", "/"), l["conf"]))
    md += ["", "### Largest estimates (where the uncertainty sits)", "", "| BOM item | qty | value / MPN | ext USD | % of BOM | conf | basis |", "|---|---|---|---|---|---|---|"]
    for l in sorted((l for l in lines if l["status"] == "estimated"), key=lambda l: -l["ext"])[:8]:
        r = l["row"]
        md.append("| %s | %d | %s | %s | %.1f | %s | %s |" % (r["Item"], l["qty"], (r["MPN"] or r["Value"])[:34], money(l["ext"]), 100 * l["ext"] / t, l["conf"], l["src"][len("estimate "):][:150].replace("|", "/")))
    md += ["", "### Lines with no price", ""]
    if un:
        md += ["| item | qty | value / MPN | sourcing | what is missing |", "|---|---|---|---|---|"]
        for l in un:
            r = l["row"]
            md.append("| %s | %d | %s | %s | no row with a price in prices.csv and no estimate in cost_estimates.csv |" % (r["Item"], l["qty"], (r["MPN"] or r["Value"])[:40], r["Sourcing"]))
    else:
        md += ["None: every purchased-part line has a price or an estimate."]
    return md + [""]


def benchmark_section(results, market):
    bench = next((r for r in market if r["cat"] == "benchmark"), None)
    price, kw = (bench["usd"], bench["kw"]) if bench else (1500.0, 228.0)
    bom_kw = price * (1 - BENCH_MARGIN) * BOM_SHARE_OF_COGS / kw          # benchmark BOM per kW
    share = (1 - BENCH_MARGIN) * BOM_SHARE_OF_COGS
    md = ["## Against the owner's benchmark (SRC-5, AC-03)", "",
          "**Benchmark.** Megarevo MPHV 1500 V string PCS, %g kW, 630 V AC, DC 914-1500 V: selling price %s USD = %.2f USD/kW (the owner's figure of 2026-10-04; source row in gen/data/market_prices.csv: %s). "
          "The benchmark is a *selling price* of a complete AC/DC unit; to compare it with a BOM the following assumptions are used (constants of gen/cost.py):" % (
              kw, "{:,.0f}".format(price), price / kw, bench["url"] if bench else "row missing"), "",
          "- BOM share of a selling price = (1 - %.2f gross margin) x %.2f BOM share of the cost of goods = **%.2f**, so the benchmark's BOM is %s x %.2f = %.0f USD = **%.2f USD/kW**." % (
              BENCH_MARGIN, BOM_SHARE_OF_COGS, share, "{:,.0f}".format(price), share, price * share, bom_kw),
          "- Current adjustment: a DC/DC module carries more amperes per kW than the benchmark (%.2f A/kW on its DC side). %.0f %% of a BOM scales with current (semiconductors, inductors, contactors, fuses, "
          "busbars, film banks), %.0f %% with power: equivalent BOM = benchmark BOM per kW x (%.2f + %.2f x module A/kW / %.2f) x kW (ARCHITECTURE-PCS.md section 12)." % (
              BENCH_DC_A_PER_KW, 100 * CURRENT_SHARE, 100 * (1 - CURRENT_SHARE), 1 - CURRENT_SHARE, CURRENT_SHARE, BENCH_DC_A_PER_KW),
          "- Range: the last column of the first table gives the benchmark-equivalent BOM at a gross margin of %s." % " / ".join("%.0f %%" % (100 * x) for x in GROSS_MARGINS), "",
          "| module | kW | port current A | A per kW | A/kW relative to the benchmark | BOM multiplier | benchmark-equivalent BOM USD | our BOM, catalogue USD | catalogue / equivalent | our BOM at %d units USD | %d units / equivalent | equivalent BOM at margin %s |" % (
              BUILD_UNITS, BUILD_UNITS, " / ".join("%.0f %%" % (100 * x) for x in GROSS_MARGINS)), "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    sell = []
    for m, res in results.items():
        t, t5 = total(res["lines"]), total(res["lines"], "ext5")
        for k in MODULES[m]:
            apk = MODULE_AMPS.get(m, 0) / k
            adj = (1 - CURRENT_SHARE) + CURRENT_SHARE * apk / BENCH_DC_A_PER_KW
            eq = bom_kw * adj * k
            rng = " / ".join("%.0f" % (price * (1 - x) * BOM_SHARE_OF_COGS / kw * adj * k) for x in GROSS_MARGINS)
            md.append("| %s | %g | %g | %.2f | %.2f | %.2f | %.0f | %.0f | %.1fx | %.0f | %.1fx | %s |" % (label(m), k, MODULE_AMPS.get(m, 0), apk, apk / BENCH_DC_A_PER_KW, adj, eq, t, t / eq, t5, t5 / eq, rng))
            sell.append("| %s | %g | %.1f | %.1f | %.2f | %.2f |" % (label(m), k, t / share / k, t5 / share / k, price / kw, price / kw * adj))
    md += ["", "In the terms of the owner's benchmark (a selling price per kW): the BOM of each module divided by the BOM share %.2f is the selling price it would need if it had the benchmark maker's margin "
           "and cost structure. This is a bookkeeping conversion, not a price anyone would quote." % share, "",
           "| module | kW | implied selling price from our BOM, catalogue, USD/kW | implied selling price at %d units, USD/kW | benchmark selling price, USD/kW | benchmark adjusted for current, USD/kW |" % BUILD_UNITS,
           "|---|---|---|---|---|---|"] + sell
    md += ["", "The rows ending in -FULL are the roadmap's earlier platform. Current ratings are the port ratings (PV-05/PV-08 135 A; PV-PORT-180 180 A; DAB port fuses 135 A) taken from the module "
           "designs; where they change, MODULE_AMPS in gen/cost.py changes.", ""]
    cand = [r for r in market if r["cat"] in ("DC/DC module", "bidirectional DC/DC module") and r["kw"] and r["kw"] >= BENCH_MIN_KW]
    if cand:
        v = [r["per_kw"] for r in cand]
        lo, med, hi = min(v), statistics.median(v), max(v)
        base = med * (1 - GROSS_MARGINS[1]) * BOM_SHARE_OF_COGS
        md += ["### Second, weaker reference: marketplace listings", "",
               "%d Made-in-China.com listings of DC/DC and bidirectional DC/DC modules of %d kW or more (export list prices, FOB, smallest quantity tier; not tender or contract prices; mostly isolated 1000 V modules of "
               "40-80 kW for storage or V2G; none is a non-isolated PV tracker; full list in gen/data/market_prices.csv): **%.0f to %.0f USD/kW, median %.0f USD/kW**. At %.0f %% gross margin and a BOM share of "
               "%.0f %% of the cost of goods the median implies a BOM of **%.1f USD/kW**." % (len(cand), BENCH_MIN_KW, lo, hi, med, 100 * GROSS_MARGINS[1], 100 * BOM_SHARE_OF_COGS, base), "",
               "| module | our BOM, catalogue USD/kW | our BOM at %d units USD/kW | implied BOM of the median listing USD/kW |" % BUILD_UNITS, "|---|---|---|---|"]
        for m, res in results.items():
            t, t5 = total(res["lines"]), total(res["lines"], "ext5")
            for k in MODULES[m]:
                md.append("| %s at %g kW | %.1f | %.1f | %.1f |" % (label(m), k, t / k, t5 / k, base))
        md += [""]
    return md


def pcs_section(results):
    """PCS-P125 next to the PV modules: the design study of D-053 (sim/pcs_design.py -> pcs_spec.json 'cost_usd')."""
    path = os.path.join(REPO, "sim", "out", "pcs_design", "pcs_spec.json")
    if not os.path.exists(path):
        return []
    import json
    c = json.load(open(path, encoding="utf-8"))["cost_usd"]
    kw = 125.0          # PCS-P125 rated power (AC-01)
    mods = [m for m in ("PV-P75", "PV-P100-110") if m in results]
    kws = {"PV-P75": 75.0, "PV-P100-110": 100.0}
    cat = [total(results[m]["lines"]) for m in mods] + [c["three_wire"]["catalogue"], c["four_wire"]["catalogue"]]
    c5 = [total(results[m]["lines"], "ext5") for m in mods] + [c["three_wire"]["5k"], c["four_wire"]["5k"]]
    kk = [kws[m] for m in mods] + [kw, kw]
    real = ["%.0f" % (100 * sum(l["ext5"] for l in results[m]["lines"] if l["ext5"] and l["real5"]) / total(results[m]["lines"], "ext5")) for m in mods] \
        + ["%.0f" % (100 * c["evidence_share_5k"]), "not stated"]
    md = ["## DC-to-AC PCS next to the PV module (design study)", "",
          "The PCS (AC-01...AC-03, PCS-P125, %g kW) has a design study and, since D-061 to D-064, drawn three-wire boards costed above as module PCS-P125 (D-063 explains why the boards cost more). This section keeps the study's own figures: they are the study's costed list "
          "`sim/out/pcs_design/pcs_costed_bom.csv` (two-level stage on 1700 V SiC, decision D-053; catalogue and %d-unit columns), **not a BOM of drawn "
          "boards** and not priced line by line by this script. The PV modules are the BOMs of the drawn power and control boards priced by this "
          "script. The earlier three-level estimate (`gen/data/costfirst_pcs_bom.csv`) is withdrawn by D-053." % (kw, BUILD_UNITS), "",
          "| | %s | PCS-P125 3-wire | PCS-P125 4-wire |" % " | ".join(mods), "|---|" + "---|" * len(kk),
          "| status | %s | design study | design study |" % " | ".join("BOM of drawn boards" for _ in mods),
          "| rated power, kW | %s |" % " | ".join("%g" % x for x in kk),
          "| catalogue, USD | %s |" % " | ".join(money(x) for x in cat), "| catalogue, USD/kW | %s |" % " | ".join("%.1f" % (x / k) for x, k in zip(cat, kk)),
          "| at %d units, USD | %s |" % (BUILD_UNITS, " | ".join(money(x) for x in c5)), "| at %d units, USD/kW | %s |" % (BUILD_UNITS, " | ".join("%.1f" % (x / k) for x, k in zip(c5, kk))),
          "| share of the %d-unit figure on real prices, %% | %s |" % (BUILD_UNITS, " | ".join(real)), "",
          "PCS-P125 3-wire by block (catalogue / %d units, USD): %s." % (BUILD_UNITS, "; ".join("%s %s / %s" % (k.lower(), money(v["catalogue"]), money(v["5k"])) for k, v in c["blocks"].items())),
          "The 4-wire option adds a fourth leg, the neutral inductor and a 4-pole disconnect (%s / %s USD). The PCS's real-price share is the study's own "
          "figure; the PV share counts the lines of this BOM with a published break of %d pieces or more." % (
              money(c["four_wire_increment"]["cat"]), money(c["four_wire_increment"]["5k"]), EVIDENCE_MIN_BREAK)]
    assert abs(sum(v["catalogue"] for v in c["blocks"].values()) - c["three_wire"]["catalogue"]) < 1.0, "PCS blocks do not add up to the 3-wire total"
    return md + [""]


# ---------------------------------------------------------------------------------------------- reconciliation with the architect's list
ALIAS = {   # architect's part name -> BOM Value / MPN it stands for (the list and the BOM name some parts differently)
    "FCSA3DS456 (CBB138 DS)": "FCSA3DS456K050H8F9DE3", "FCSA3DS225 (CBB138 DS)": "FCSA3DS225K050IC90BE3", "AlN pad TO-247 (Innovacera class)": "PAD AlN 1 mm (CUSTOM)",
    "4.7n 2000V C0G 2220": "4.7n 2000V", "Al extrusion (CUSTOM)": "heatsink", "TO-247 spring clip": "heatsink", "G-779 class": "heatsink",
    "DC terminal (CUSTOM)": "DC terminal", "STK-HO/A 130": "STK-HO/A 75", "NTC probe 10k B3950 (insulated lug)": "NTC 10k lug probe", "CA-IS3062W": "CA-IS3062VW",
    "IV2Q171R0D7Z / SG2M1K0170J2J": "IV2Q171R0D7Z", "REDCUBE-class press-fit M4": "7461057", "X 2.2u 1300V (RFQ)": "X 2.2u 1300V RFQ", "CNY65B + 12 x 2512": "CNY65B",
    "GDB-T1 (CUSTOM, EP10 class)": "Gate-bias transformer", "AUX-T1 (CUSTOM, EC41/ETD39 class)": "AUX-T1 75 W", "3920 metal-strip 0.2 mOhm (CSS2H-3920 class)": "CSS2H-3920R-L200F",
    "nanocrystalline ring ~63x38x25 mm": "N-C-644025", "RXLG 200 W 220R class": "220R RFQ", "CA-IS3821LW (or LWW, 15 mm)": "CA-IS3821LG", "X9555WV-2x30 + socket": "X9555WV-2x32-6TV01"}


BLOCK_GROUP = (("POWER", G_POWER), ("GATE", G_GATE), ("SENSING", G_SENSE), ("IMD", G_SENSE), ("PORT", G_PROT), ("SURGE", G_PROT), ("AUX", G_AUX),
               ("CONTROL", G_CTRL), ("INTERFACE", G_CTRL), ("THERMAL", G_THERM), ("MECH", G_CONN))


def arch_group(r):
    """function group of a row of the architect's list that pairs with no BOM line: from its words, else from its block (approximate)"""
    if re.search(r"ring|choke|transformer|-T1|inductor", r["part"], re.I):
        return G_MAG
    if re.search(r"thick film|wirewound|RXLG", r["part"], re.I):
        return G_RES
    return next((g for pre, g in BLOCK_GROUP if r["block"].upper().startswith(pre)), G_RES)


def reconcile_section(results):
    path = os.path.join(DATA, "costfirst_bom.csv")
    if not os.path.exists(path) or "PV-P75" not in results:
        return []
    arch = rd(path)
    tot75 = next((float(r["ext_price_usd"]) for r in arch if r["block"] == "TOTAL" and "PV-P75" in r["function"]), None)
    tot100 = next((float(r["ext_price_usd"]) for r in arch if r["block"] == "TOTAL" and "P100" in r["function"]), None)
    nz = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    lines = [l for l in results["PV-P75"]["lines"] if l["status"] not in ("nopart", "unpriced")]       # an unpriced line has no cost to compare
    n_unpriced = sum(1 for l in results["PV-P75"]["lines"] if l["status"] == "unpriced")
    idx = {}
    for i, l in enumerate(lines):
        for k in (l["row"]["MPN"], l["row"]["Value"]):
            if k:
                idx.setdefault(nz(k), []).append(i)

    def find(part):
        key = nz(ALIAS.get(part, part))
        c = idx.get(key) or [i for k, v in idx.items() for i in v if len(k) >= 8 and len(key) >= 8 and (k.startswith(key) or key.startswith(k))] \
            or ([i for k, v in idx.items() for i in v if k.startswith(key)] if len(key) >= 6 and part in ALIAS else [])
        return sorted(set(c))
    pairs = collections.defaultdict(list)       # tuple of BOM line indexes -> rows of the architect's list
    arch_only = []
    for r in arch:
        if r["block"] in ("SUBTOTAL", "TOTAL"):
            continue
        hit = find(r["part"])
        if hit:
            pairs[tuple(hit)].append(r)
        else:
            arch_only.append(r)
    used = {i for k in pairs for i in k}
    bom_only = [l for i, l in enumerate(lines) if i not in used]
    a_tot = sum(float(r["ext_price_usd"]) for r in arch if r["block"] not in ("SUBTOTAL", "TOTAL"))
    t = total(results["PV-P75"]["lines"])
    paired_b = sum(sum(lines[i]["ext"] for i in k) for k in pairs)
    paired_a = sum(float(r["ext_price_usd"]) for rs in pairs.values() for r in rs)
    bo, ao = sum(l["ext"] for l in bom_only), sum(float(r["ext_price_usd"]) for r in arch_only)
    # the same bridge at the 5,000-unit level (the list carries its own unit_price_5k_usd per row)
    a5 = lambda r: float(r["qty"]) * float(r["unit_price_5k_usd"])
    have5 = all(r.get("unit_price_5k_usd") for r in arch if r["block"] not in ("SUBTOTAL", "TOTAL"))
    t5 = total(results["PV-P75"]["lines"], "ext5")
    if have5:
        a_tot5 = sum(a5(r) for r in arch if r["block"] not in ("SUBTOTAL", "TOTAL"))
        paired_b5, paired_a5 = sum(sum(lines[i]["ext5"] for i in k) for k in pairs), sum(a5(r) for rs in pairs.values() for r in rs)
        bo5, ao5 = sum(l["ext5"] for l in bom_only), sum(a5(r) for r in arch_only)
        assert abs(a_tot5 + (paired_b5 - paired_a5) + bo5 - ao5 - t5) < 1e-6, "5k reconciliation does not add up"
    md = ["## Reconciliation of PV-P75 with the architect's list", "",
          "This BOM at catalogue prices: **%s USD**. The architect's costed list `gen/data/costfirst_bom.csv` (PV-P75): **%s USD** (its rows add up to %s). Difference (BOM minus list) **%+.2f USD**. "
          "Rows are paired by part name (a few renamed parts through the ALIAS table of gen/cost.py). The list bundles many small parts into 'class' rows that have no single BOM line (shown as "
          "'list only'); BOM lines the list does not name are 'BOM only'." % (money(t), money(tot75 or a_tot), money(a_tot), t - (tot75 or a_tot)), "",
          "| step | catalogue USD | at %d units USD |" % BUILD_UNITS, "|---|---|---|", "| architect's list, total | %s | %s |" % (money(a_tot), money(a_tot5) if have5 else "n/a"),
          "| change on paired lines (this BOM %s against the list %s) | %+.2f | %s |" % (money(paired_b), money(paired_a), paired_b - paired_a, "%+.2f" % (paired_b5 - paired_a5) if have5 else "n/a"),
          "| plus BOM-only lines (%d lines: parts on the drawn boards that the list does not name) | %+.2f | %s |" % (len(bom_only), bo, "%+.2f" % bo5 if have5 else "n/a"),
          "| minus list-only rows (%d rows: bundled 'class' rows and parts not drawn) | %+.2f | %s |" % (len(arch_only), -ao, "%+.2f" % -ao5 if have5 else "n/a"),
          "| **this BOM, total** | **%s** | **%s** |" % (money(t), money(t5)), ""]
    if have5:
        body = [r for r in arch if r["block"] not in ("SUBTOTAL", "TOTAL") and float(r["unit_price_usd"]) > 0]
        ratios = sorted(float(r["unit_price_5k_usd"]) / float(r["unit_price_usd"]) for r in body)
        big = max(body, key=lambda r: float(r["qty"]) * (float(r["unit_price_usd"]) - float(r["unit_price_5k_usd"])))
        md += ["At %d units the two totals differ by %+.2f USD (this BOM %s, the list %s). 5k / catalogue is %.2f here and %.2f in the list. The list sets a 5k price per row (median ratio %.2f, lowest %.2f; its largest "
               "single reduction is %s: %.2f to %.2f USD per piece, %.0f USD in total); this BOM uses published breaks of 1,000 pieces or more where they exist (mostly at the catalogue price already) and the "
               "class factors of gen/data/volume_factors.csv elsewhere." % (BUILD_UNITS, t5 - a_tot5, money(t5), money(a_tot5), t5 / t, a_tot5 / a_tot, statistics.median(ratios), ratios[0], big["part"],
                                                                         float(big["unit_price_usd"]), float(big["unit_price_5k_usd"]),
                                                                         float(big["qty"]) * (float(big["unit_price_usd"]) - float(big["unit_price_5k_usd"]))), ""]
    ga, gb = collections.defaultdict(float), collections.defaultdict(float)
    for l in lines:
        gb[l["group"]] += l["ext"]
    for k, rs in pairs.items():
        for r in rs:
            ga[lines[k[0]]["group"]] += float(r["ext_price_usd"])
    for r in arch_only:
        ga[arch_group(r)] += float(r["ext_price_usd"])
    md += ["### By function group (list-only rows are placed by their words and block, so the split is approximate)", "", "| group | this BOM USD | architect's list USD | difference |", "|---|---|---|---|"]
    for g in sorted(GROUPS, key=lambda g: -abs(gb[g] - ga[g])):
        md.append("| %s | %s | %s | %+.2f |" % (g, money(gb[g]), money(ga[g]), gb[g] - ga[g]))
    md += ["| **total** | **%s** | **%s** | **%+.2f** |" % (money(t), money(a_tot), t - a_tot), ""]
    rowsd = []
    for k, rs in pairs.items():
        b = sum(lines[i]["ext"] for i in k)
        a = sum(float(r["ext_price_usd"]) for r in rs)
        rowsd.append((b - a, k, rs, b, a))
    rowsd.sort(key=lambda x: -abs(x[0]))
    md += ["### Paired lines with a difference of 1 USD or more", "", "| BOM line | qty BOM / list | unit USD BOM / list | ext USD BOM | ext USD list | difference | basis of the BOM price |", "|---|---|---|---|---|---|---|"]
    for d, k, rs, b, a in rowsd:
        if abs(d) < 1:
            continue
        l = lines[k[0]]
        md.append("| %s | %s / %s | %s / %s | %s | %s | %+.2f | %s |" % ((l["row"]["MPN"] or l["row"]["Value"])[:30], "+".join(str(lines[i]["qty"]) for i in k), "+".join(r["qty"] for r in rs),
                                                                         "%.4g" % l["unit"], "/".join("%.4g" % float(r["unit_price_usd"]) for r in rs), money(b), money(a), d,
                                                                         re.sub(r"^estimate [^:]*: ", "", l["src"])[:90].replace("|", "/")))
    md += ["", "### BOM lines the architect's list does not name (largest 12 of %d)" % len(bom_only), "", "| BOM line | qty | unit USD | ext USD | group |", "|---|---|---|---|---|"]
    for l in sorted(bom_only, key=lambda l: -l["ext"])[:12]:
        md.append("| %s | %d | %s | %s | %s |" % ((l["row"]["MPN"] or l["row"]["Value"])[:34], l["qty"], "%.4g" % l["unit"], money(l["ext"]), l["group"]))
    md += ["", "### Rows of the architect's list without a BOM line (largest 12 of %d)" % len(arch_only), "", "| block | part | qty | ext USD | note |", "|---|---|---|---|---|"]
    for r in sorted(arch_only, key=lambda r: -float(r["ext_price_usd"]))[:12]:
        md.append("| %s | %s | %s | %s | %s |" % (r["block"], r["part"][:40], r["qty"], r["ext_price_usd"], r["price_source"][:80].replace("|", "/")))
    top = [(d, lines[k[0]]) for d, k, rs, b, a in rowsd[:3] if abs(d) >= 1]
    md += ["", "**Which is right.** For the drawn boards, the BOM column: it prices every part that is on the schematics (the list prices some small parts as bundles), and it follows the later source where one exists. "
           "The three largest paired differences are %s. Where the basis of a BOM line reads 'architect's list', the BOM carries the list's estimate over unchanged: the two agree, and the number is only as "
           "good as that estimate (an RFQ that has not been answered)." % "; ".join("%s %+.2f USD" % ((l["row"]["MPN"] or l["row"]["Value"])[:30], d) for d, l in top)]
    if tot100:
        t100 = total(results["PV-P100-110"]["lines"]) if "PV-P100-110" in results else 0
        md += ["", "PV-P100/110: this BOM %s USD, architect's list %s USD, difference %+.2f USD (the list's rows give the PV-P100/110 quantity in their notes; not paired line by line here)." % (money(t100), money(tot100), t100 - tot100)]
    return md + [""]


# ---------------------------------------------------------------------------------------------- self-checks
def self_check(results, fac):
    for module, res in results.items():
        lines, rows = res["lines"], res["rows"]
        assert len(lines) == len(rows) and rows, module + ": line count"
        st = collections.Counter(l["status"] for l in lines)
        assert set(st) <= {"nopart", "priced", "estimated", "unpriced"}, module + ": unknown status"
        for l in lines:
            assert l["row"]["Sourcing"] == "NOPART" or l["status"] != "nopart", module + ": purchased line treated as NOPART: " + l["row"]["Item"]
            assert (l["status"] == "unpriced") == (l["unit"] is None), module + ": unpriced line must have no cost, others must have one"
            assert (l["status"] == "unpriced") == (l["unit5"] is None), module + ": unpriced line must have no 5k price, others must have one"
            assert l["status"] != "unpriced" or l["src"] == "UNPRICED", module + ": unpriced line not marked"
            assert l["unit"] is None or l["unit"] >= 0, module + ": negative price on item " + l["row"]["Item"]
            assert l["unit5"] is None or 0 <= l["unit5"] <= l["unit"] + 1e-9, "%s: item %s has a %d-unit price %s above its catalogue price %s" % (module, l["row"]["Item"], BUILD_UNITS, l["unit5"], l["unit"])
            assert l["qty"] > 0, module + ": qty"
            assert not l["fclass"] or l["fclass"] in fac, "%s: item %s uses the factor class %r that volume_factors.csv does not define" % (module, l["row"]["Item"], l["fclass"])
        # totals equal the sum of the lines as written to the costed CSV
        back = rd(os.path.join(REPO, "bom", module + "_costed_BOM.csv"))
        csv_total = sum(float(r["ext_cost_usd"]) for r in back if r["ext_cost_usd"])
        assert abs(csv_total - total(lines)) < 1e-6, "%s: CSV total %.6f != computed %.6f" % (module, csv_total, total(lines))
        csv5 = sum(float(r["unit_cost_5k_usd"]) * int(r["Qty"]) for r in back if r["unit_cost_5k_usd"])
        assert abs(csv5 - total(lines, "ext5")) < 0.01, "%s: 5k CSV total %.4f != computed %.4f" % (module, csv5, total(lines, "ext5"))
        assert sum(1 for r in back if r["price_source"] == "UNPRICED") == st["unpriced"], module + ": unpriced lines not visible in the CSV"
        assert all(r["ext_cost_usd"] == "" and r["unit_cost_5k_usd"] == "" for r in back if r["price_source"] == "UNPRICED"), module + ": unpriced line carries a cost"
        g = collections.defaultdict(float)
        for l in lines:
            if l["ext"] is not None:
                g[l["group"]] += l["ext"]
        assert abs(sum(g.values()) - total(lines)) < 1e-6, module + ": group split does not add up"
        assert abs(sum(l["ext"] for l in lines if l["status"] == "priced") + sum(l["ext"] for l in lines if l["status"] == "estimated") - total(lines)) < 1e-6, module + ": priced + estimated != total"
        real = sum(l["ext5"] for l in lines if l["ext5"] and l["real5"])
        ass = sum(l["ext5"] for l in lines if l["ext5"] and not l["real5"])
        assert abs(real + ass - total(lines, "ext5")) < 1e-6, module + ": real + assumed != the 5k total"
        assert total(lines, "ext5") <= total(lines) + 1e-6, module + ": the 5k total is above the catalogue total"
        assert set(g) <= set(GROUPS), module + ": unknown group"
        assert all(o[2] <= o[1] + 1e-9 for o in res["outside"]), module + ": an outside-the-BOM item costs more at 5k than at catalogue level"
    assert all(0 < f[0] <= 1 for f in fac.values()), "volume_factors.csv: a factor outside (0, 1] would put a 5,000-unit price above its catalogue price"
    assert 0 < BENCH_MARGIN < 1 and 0 < BOM_SHARE_OF_COGS <= 1 and 0 <= CURRENT_SHARE <= 1 and BENCH_DC_A_PER_KW > 0, "benchmark constants out of range"
    assert os.path.exists(os.path.join(REPO, "bom", "COST.md"))


if __name__ == "__main__":
    main()
