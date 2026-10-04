"""Symbol handling: copy verified KiCad library symbols, build custom
rectangular IC symbols from datasheet pin tables, compute pin geometry."""
import copy
import os
import math
from sexpr import parse, dump, Sym, find, find_all

KICAD_SYM_DIR = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
G = 2.54  # grid

_lib_cache = {}


def _load_lib(lib):
    if lib not in _lib_cache:
        path = os.path.join(KICAD_SYM_DIR, lib + ".kicad_sym")
        tree = parse(open(path, encoding="utf-8").read())
        _lib_cache[lib] = {s[1]: s for s in find_all(tree, "symbol")}
    return _lib_cache[lib]


def _prop(sym, name):
    for p in find_all(sym, "property"):
        if p[1] == name:
            return p
    return None


def kicad_symbol(lib, name, new_name):
    """Return a flattened copy of lib:name renamed to new_name (no lib prefix)."""
    syms = _load_lib(lib)
    src = copy.deepcopy(syms[name])
    ext = find(src, "extends")
    if ext is not None:
        base = kicad_symbol(lib, ext[1], new_name)
        # override properties from derived symbol
        for p in find_all(src, "property"):
            bp = _prop(base, p[1])
            if bp is not None:
                base[base.index(bp)] = p
            else:
                base.append(p)
        return base
    src[1] = new_name
    for sub in find_all(src, "symbol"):
        suffix = sub[1][len(name):]
        sub[1] = new_name + suffix
    return src


def set_property(sym, name, value, hide=True):
    p = _prop(sym, name)
    if p is None:
        p = [Sym("property"), name, value, [Sym("at"), 0, 0, 0],
             [Sym("hide"), Sym("yes")],
             [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]]
        # insert after last property
        idx = max(i for i, x in enumerate(sym) if isinstance(x, list) and x and x[0] == "property") + 1
        sym.insert(idx, p)
    else:
        p[2] = value


# ---------------------------------------------------------------- pins
class Pin:
    __slots__ = ("number", "name", "etype", "x", "y", "angle", "length", "unit", "hidden")

    def __init__(self, number, name, etype, x, y, angle, length, unit, hidden=False):
        self.number, self.name, self.etype = number, name, etype
        self.x, self.y, self.angle, self.length = x, y, angle, length
        self.unit, self.hidden = unit, hidden


def symbol_pins(sym):
    """All pins of a (flattened) symbol: unit 0 = common to all units."""
    pins = []
    base = sym[1]
    for sub in find_all(sym, "symbol"):
        rest = sub[1][len(base) + 1:]
        unit = int(rest.split("_")[0]) if rest else 0
        for p in find_all(sub, "pin"):
            at = find(p, "at")
            ln = find(p, "length")
            nm = find(p, "name")
            nu = find(p, "number")
            hidden = find(p, "hide") is not None
            pins.append(Pin(str(nu[1]), str(nm[1]), str(p[1]), float(at[1]), float(at[2]),
                            int(float(at[3])) if len(at) > 3 else 0,
                            float(ln[1]) if ln else 2.54, unit, hidden))
    return pins


def pin_pos(sx, sy, rot, mirror, px, py):
    """Schematic coordinate of a pin connection point.
    Library Y is up, schematic Y is down; rot is counter-clockwise degrees."""
    x, y = px, py
    if mirror == "y":
        x = -x
    elif mirror == "x":
        y = -y
    r = math.radians(rot)
    xr = x * math.cos(r) - y * math.sin(r)
    yr = x * math.sin(r) + y * math.cos(r)
    return round(sx + xr, 4), round(sy - yr, 4)


def pin_dir(angle, rot, mirror):
    """Unit vector (schematic coords) pointing from the pin connection point
    away from the symbol body."""
    a = (angle + 180) % 360  # pin 'at' angle points into body; reverse it
    dx, dy = round(math.cos(math.radians(a))), round(math.sin(math.radians(a)))
    if mirror == "y":
        dx = -dx
    elif mirror == "x":
        dy = -dy
    r = math.radians(rot)
    xr = dx * math.cos(r) - dy * math.sin(r)
    yr = dx * math.sin(r) + dy * math.cos(r)
    return round(xr), -round(yr)


# ------------------------------------------------------- custom symbols
def _txt(size=1.27):
    return [Sym("effects"), [Sym("font"), [Sym("size"), size, size]]]


def _pin(num, name, etype, x, y, ang, length=G, hidden=False):
    p = [Sym("pin"), Sym(etype), Sym("line"), [Sym("at"), x, y, ang], [Sym("length"), length]]
    if hidden:
        p.append([Sym("hide"), Sym("yes")])
    p += [[Sym("name"), name, _txt()], [Sym("number"), num, _txt()]]
    return p


def _property(name, value, x, y, hide=False, justify=None):
    p = [Sym("property"), name, value, [Sym("at"), x, y, 0]]
    if hide:
        p.append([Sym("hide"), Sym("yes")])
    eff = _txt()
    if justify:
        eff.append([Sym("justify")] + [Sym(j) for j in justify.split()])
    p.append(eff)
    return p


def custom_ic(name, units, ref="U", value=None, footprint="", datasheet="", description="",
              keywords="", unit_names=None, pin_len=2 * G):
    """units: list of dicts {left:[(num,name,etype)|None], right:[...], top:[], bottom:[], title:str}
    None entries create an empty slot (visual grouping gap)."""
    sym = [Sym("symbol"), name,
           [Sym("pin_names"), [Sym("offset"), 0.762]],
           [Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), Sym("yes")], [Sym("on_board"), Sym("yes")]]
    tallest = 0
    geoms = []
    for u in units:
        nl, nr = len(u.get("left", [])), len(u.get("right", []))
        nt, nb = len(u.get("top", [])), len(u.get("bottom", []))
        rows = max(nl, nr, 1)
        cols = max(nt, nb, 0)
        maxname_l = max([len(p[1]) for p in u.get("left", []) if p] + [0])
        maxname_r = max([len(p[1]) for p in u.get("right", []) if p] + [0])
        width_min = (maxname_l + maxname_r) * 1.27 * 0.62 + 4 * G
        w = max(u.get("min_width", 0), width_min, (cols + 1) * G)
        w = math.ceil(w / (2 * G)) * 2 * G
        h = (rows + 1) * G
        h = math.ceil(h / (2 * G)) * 2 * G
        geoms.append((w, h, rows, cols))
        tallest = max(tallest, h)
    w0, h0 = geoms[0][0], geoms[0][1]
    sym.append(_property("Reference", ref, -w0 / 2, h0 / 2 + 1.27, justify="left bottom"))
    sym.append(_property("Value", value or name, -w0 / 2, -h0 / 2 - 1.27, justify="left top"))
    sym.append(_property("Footprint", footprint, 0, -h0 / 2 - 3.81, hide=True))
    sym.append(_property("Datasheet", datasheet, 0, -h0 / 2 - 6.35, hide=True))
    sym.append(_property("Description", description, 0, -h0 / 2 - 8.89, hide=True))
    if keywords:
        sym.append(_property("ki_keywords", keywords, 0, 0, hide=True))
    for ui, u in enumerate(units, start=1):
        w, h, rows, cols = geoms[ui - 1]
        body = [Sym("symbol"), "%s_%d_1" % (name, ui),
                [Sym("rectangle"), [Sym("start"), -w / 2, h / 2], [Sym("end"), w / 2, -h / 2],
                 [Sym("stroke"), [Sym("width"), 0.254], [Sym("type"), Sym("default")]],
                 [Sym("fill"), [Sym("type"), Sym("background")]]]]
        if u.get("title"):
            body.append([Sym("text"), u["title"], [Sym("at"), 0, h / 2 - 1.27, 0],
                         [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27], [Sym("bold"), Sym("yes")]]]])
        top_y = (rows - 1) / 2.0 * G
        top_y = math.floor(top_y / G) * G  # keep on grid
        for i, p in enumerate(u.get("left", [])):
            if p:
                body.append(_pin(p[0], p[1], p[2], -w / 2 - pin_len, top_y - i * G, 0, pin_len))
        for i, p in enumerate(u.get("right", [])):
            if p:
                body.append(_pin(p[0], p[1], p[2], w / 2 + pin_len, top_y - i * G, 180, pin_len))
        left_x = -math.floor((cols - 1) / 2.0) * G
        for i, p in enumerate(u.get("top", [])):
            if p:
                body.append(_pin(p[0], p[1], p[2], left_x + i * G, h / 2 + pin_len, 270, pin_len))
        for i, p in enumerate(u.get("bottom", [])):
            if p:
                body.append(_pin(p[0], p[1], p[2], left_x + i * G, -h / 2 - pin_len, 90, pin_len))
        sym.append(body)
    return sym


def power_symbol(name, net, style="up"):
    """Global power-net symbol (KiCad 'power' flag). style: up | gnd."""
    sym = [Sym("symbol"), name, [Sym("power")],
           [Sym("pin_numbers"), [Sym("hide"), Sym("yes")]],
           [Sym("pin_names"), [Sym("offset"), 0], [Sym("hide"), Sym("yes")]],
           [Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), Sym("yes")], [Sym("on_board"), Sym("yes")],
           _property("Reference", "#PWR", 0, -3.81 if style == "gnd" else 3.81, hide=True),
           _property("Value", net, 0, -3.556 if style == "gnd" else 3.556),
           _property("Footprint", "", 0, 0, hide=True),
           _property("Datasheet", "", 0, 0, hide=True),
           _property("Description", "Power rail " + net, 0, 0, hide=True)]
    g = [Sym("symbol"), name + "_0_1"]
    if style == "gnd":
        g.append([Sym("polyline"), [Sym("pts"), [Sym("xy"), 0, 0], [Sym("xy"), 0, -1.27], [Sym("xy"), 1.27, -1.27],
                                    [Sym("xy"), 0, -2.54], [Sym("xy"), -1.27, -1.27], [Sym("xy"), 0, -1.27]],
                  [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]], [Sym("fill"), [Sym("type"), Sym("none")]]])
    else:
        g.append([Sym("polyline"), [Sym("pts"), [Sym("xy"), -0.762, 1.27], [Sym("xy"), 0, 2.54]],
                  [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]], [Sym("fill"), [Sym("type"), Sym("none")]]])
        g.append([Sym("polyline"), [Sym("pts"), [Sym("xy"), 0, 0], [Sym("xy"), 0, 2.54]],
                  [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]], [Sym("fill"), [Sym("type"), Sym("none")]]])
        g.append([Sym("polyline"), [Sym("pts"), [Sym("xy"), 0, 2.54], [Sym("xy"), 0.762, 1.27]],
                  [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]], [Sym("fill"), [Sym("type"), Sym("none")]]])
    sym.append(g)
    sym.append([Sym("symbol"), name + "_1_1",
                [Sym("pin"), Sym("power_in"), Sym("line"), [Sym("at"), 0, 0, 90 if style == "up" else 270],
                 [Sym("length"), 0], [Sym("hide"), Sym("yes")],
                 [Sym("name"), net, _txt()], [Sym("number"), "1", _txt()]]])
    return sym


def write_library(path, symbols):
    root = [Sym("kicad_symbol_lib"), [Sym("version"), 20251024], [Sym("generator"), "kicad_symbol_editor"],
            [Sym("generator_version"), "10.0"]] + symbols
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump(root) + "\n")
