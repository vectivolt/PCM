"""KiCad 10 hierarchical schematic writer with a block/shelf layout engine.

Connectivity model: every pin gets a short stub ending in a net label (local if the
net stays on one sheet, global if it crosses sheets) or an oriented power symbol.
Adjacent pins of one symbol on the same net share one label through a short bus.
Wires never run between symbols, so the netlist equals the design dictionary; build.py
re-extracts it with kicad-cli and compares.

Every unit is rendered once at the origin to obtain its true extent (body graphics,
pin stubs, labels, power symbols, fields) and then packed; nothing is estimated twice."""
import math
import textwrap
import uuid as _uuid
from collections import defaultdict

import symlib
from sexpr import Sym, dump, find, find_all

G = 2.54
PAPERS = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594)}
_NS = _uuid.UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")
GND_NETS = ("GND", "PE")
LBL = 1.0    # label font size
FIELD = 1.1  # reference/value font size


# --- drafting bounds (decision D046, verification/rendered_review/render_inspection_2026-09-19.md).
# They were conventions the render inspection enforced by eye; a title or a note that exceeds them is
# CLIPPED by the frame, silently, in the PDF a reviewer reads. They are build errors here instead.
# MAX_TITLE bounds "root title - sheet title", which is what the KiCad A3/A2 title-block Title field holds.
# The inspection's own estimate was "<= ~56 characters"; the longest title it actually rendered and passed
# is 62 (G3X sheet 10 "Isolated Control Output"), so 62 is the checked bound and 56 stays the drafting
# target. MAX_BOX_LINE bounds one line of a cover hierarchy-box description (86 mm box, 1.3 mm font).
MAX_TITLE = 62
MAX_BOX_LINE = 85
ROOT_NOTE_WRAP = 280      # A3 cover, 1.5 mm font: ~290 characters per line, ~9 lines fit above the title block


def uid(*parts):
    return str(_uuid.uuid5(_NS, "/".join(str(p) for p in parts)))


def snap(v):
    return round(round(v / G) * G, 4)


def tlen(s, size=LBL):
    return len(s) * size * 0.84 + 0.6


def at(x, y, a=0):
    return [Sym("at"), round(x, 4), round(y, 4), a]


def effects(size=1.27, justify=None, hide=False, bold=False):
    font = [Sym("font"), [Sym("size"), size, size]]
    if bold:
        font.append([Sym("bold"), Sym("yes")])
    e = [Sym("effects"), font]
    if justify:
        e.append([Sym("justify")] + [Sym(j) for j in justify.split()])
    if hide:
        e.append([Sym("hide"), Sym("yes")])
    return e


# ------------------------------------------------------------------ model
class Part:
    def __init__(self, ref, lib_id, value, pins, footprint="", fields=None, unit_rot=None, dnp=False):
        self.ref, self.lib_id, self.value = ref, lib_id, value
        self.pins = {str(k): v for k, v in pins.items()}  # pin number -> net (None = no connect)
        self.footprint = footprint
        self.fields = fields or {}
        self.unit_rot = unit_rot or {}
        self.dnp = dnp
        self.sheet = None
        self.block = None


class Block:
    def __init__(self, title, note=""):
        self.title, self.note = title, note
        self.parts = []


class SheetDef:
    def __init__(self, key, title, description=""):
        self.key, self.title, self.description = key, title, description
        self.blocks = []
        self.uuid = uid("sheet", key)

    def block(self, title, note=""):
        b = Block(title, note)
        self.blocks.append(b)
        return b


class Design:
    def __init__(self, project, title, rev, date):
        self.project, self.title, self.rev, self.date = project, title, rev, date
        self.sheets = []
        self.symbols = {}
        self.power_nets = {}
        self.parts = {}

    def sheet(self, key, title, description=""):
        s = SheetDef(key, title, description)
        self.sheets.append(s)
        return s

    def add(self, sheet, block, part):
        if part.ref in self.parts:
            raise ValueError("duplicate reference " + part.ref)
        part.sheet, part.block = sheet, block
        block.parts.append(part)
        self.parts[part.ref] = part
        return part

    def nets(self):
        n = defaultdict(list)
        for p in self.parts.values():
            for pin, net in p.pins.items():
                if net:
                    n[net].append((p.ref, pin))
        return n

    def net_sheets(self):
        ns = defaultdict(set)
        for p in self.parts.values():
            for net in p.pins.values():
                if net:
                    ns[net].add(p.sheet.key)
        return ns


# ------------------------------------------------------------------ geometry
def _units(sym):
    us = sorted({p.unit for p in symlib.symbol_pins(sym) if p.unit > 0})
    return us or [1]


def _graphics_bbox(sym, unit, rot):
    """Body graphics extent (lib shapes of unit 0 and this unit) in sheet coordinates."""
    base = sym[1]
    pts = []
    for sub in find_all(sym, "symbol"):
        rest = sub[1][len(base) + 1:]
        u = int(rest.split("_")[0]) if rest else 0
        if u not in (0, unit):
            continue
        for g in sub[2:]:
            if not isinstance(g, list) or not g:
                continue
            F = lambda node: (float(node[1]), float(node[2]))
            if g[0] == "rectangle":
                (sx, sy), (ex, ey) = F(find(g, "start")), F(find(g, "end"))
                pts += [(sx, sy), (ex, ey)]
            elif g[0] == "polyline":
                pts += [F(xy) for xy in find_all(find(g, "pts"), "xy")]
            elif g[0] == "circle":
                (cx, cy), r = F(find(g, "center")), float(find(g, "radius")[1])
                pts += [(cx - r, cy - r), (cx + r, cy + r)]
            elif g[0] == "arc":
                pts += [F(find(g, k)) for k in ("start", "mid", "end")]
            elif g[0] == "text":
                ax, ay = F(find(g, "at"))
                w = tlen(g[1], 1.27) / 2
                pts += [(ax - w, ay - 1), (ax + w, ay + 1)]
    if not pts:
        return None
    sp = [symlib.pin_pos(0, 0, rot, None, x, y) for x, y in pts]
    return min(p[0] for p in sp), min(p[1] for p in sp), max(p[0] for p in sp), max(p[1] for p in sp)


def _dirs_for(sym, unit, rot):
    return {p.number: symlib.pin_dir(p.angle, rot, None) for p in symlib.symbol_pins(sym) if p.unit in (0, unit)}


def auto_rotation(design, part, sym):
    """2-pin parts: ground pin points down, power pin up, otherwise pin 1 left."""
    pins = [p for p in symlib.symbol_pins(sym) if p.unit in (0, 1)]
    if len(pins) != 2 or part.unit_rot:
        return part.unit_rot.get(1, 0)
    nets = {p.number: part.pins.get(p.number) for p in pins}
    gnd = [n for n, v in nets.items() if v in GND_NETS]
    pwr = [n for n, v in nets.items() if v in design.power_nets and v not in GND_NETS]
    for rot in (0, 90, 180, 270):
        d = _dirs_for(sym, 1, rot)
        if len(gnd) == 1:
            if d[gnd[0]] == (0, 1):
                return rot
        elif len(pwr) == 1:
            if d[pwr[0]] == (0, -1):
                return rot
        elif pwr and gnd:
            return 0
        elif d.get("1") == (-1, 0):
            return rot
    return 0


# ------------------------------------------------------------------ rendering
class Render:
    """Primitive list + bounding box for one placed unit (coordinates absolute)."""

    def __init__(self):
        self.items = []
        self.box = [1e9, 1e9, -1e9, -1e9]
        self.power = []  # (net, x, y, direction) power symbols, numbered at final write

    def grow(self, x1, y1, x2, y2):
        b = self.box
        b[0], b[1], b[2], b[3] = min(b[0], x1, x2), min(b[1], y1, y2), max(b[2], x1, x2), max(b[3], y1, y2)


def render_unit(design, sheet, part, unit, rot, ox, oy, net_sheets):
    sym = design.symbols[part.lib_id]
    R = Render()
    g = _graphics_bbox(sym, unit, rot)
    if g:
        R.grow(ox + g[0], oy + g[1], ox + g[2], oy + g[3])
    su = uid(sheet.key, part.ref, unit)
    pins = [p for p in symlib.symbol_pins(sym) if p.unit in (0, unit)]
    entries, seen = [], set()
    for p in pins:
        if p.hidden and p.etype == "power_in" and p.number not in part.pins:
            continue
        px, py = symlib.pin_pos(ox, oy, rot, None, p.x, p.y)
        R.grow(px, py, px, py)
        if (px, py) in seen:
            continue
        seen.add((px, py))
        if p.number not in part.pins:
            raise ValueError("%s pin %s (%s) has no net assignment" % (part.ref, p.number, p.name))
        entries.append((p, px, py, symlib.pin_dir(p.angle, rot, None), part.pins[p.number]))
    # group adjacent same-net pins with the same direction
    groups = []
    for key in sorted({(e[4], e[3]) for e in entries if e[4]}, key=str):
        net, d = key
        mem = [e for e in entries if e[4] == net and e[3] == d]
        along = (lambda e: (e[1], e[2])) if d[0] else (lambda e: (e[2], e[1]))  # (fixed coord, running coord)
        mem.sort(key=along)
        cur = [mem[0]]
        for e in mem[1:]:
            a, b = along(cur[-1]), along(e)
            if abs(a[0] - b[0]) < 1e-6 and abs(b[1] - a[1] - G) < 1e-6:
                cur.append(e)
            else:
                groups.append(cur)
                cur = [e]
        groups.append(cur)
    for p, px, py, d, net in entries:
        if net is None:
            R.items.append([Sym("no_connect"), at(px, py)[:3], [Sym("uuid"), uid(su, "nc", p.number)]])
    for grp in groups:
        d, net = grp[0][3], grp[0][4]
        ends = []
        for p, px, py, _, _ in grp:
            ex, ey = round(px + d[0] * G, 4), round(py + d[1] * G, 4)
            R.items.append(_wire(px, py, ex, ey, uid(su, "w", p.number)))
            R.grow(px, py, ex, ey)
            ends.append((ex, ey, p.number))
        for (x1, y1, n1), (x2, y2, n2) in zip(ends, ends[1:]):
            R.items.append(_wire(x1, y1, x2, y2, uid(su, "bus", n1, n2)))
        for ex, ey, n in ends[1:-1]:
            R.items.append([Sym("junction"), at(ex, ey)[:3], [Sym("diameter"), 0], [Sym("color"), 0, 0, 0, 0],
                            [Sym("uuid"), uid(su, "j", n)]])
        ex, ey, n = ends[0]
        if net in design.power_nets:
            R.power.append((net, ex, ey, d))
            _grow_power(R, net, ex, ey, d)
        else:
            angle = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[d]
            glob = len(net_sheets[net]) > 1
            L = tlen(net) + (2.2 if glob else 0.3)
            R.grow(ex, ey, ex + d[0] * L, ey + d[1] * L)
            R.grow(ex - abs(d[1]) * 1.2, ey - abs(d[0]) * 1.2, ex + abs(d[1]) * 1.2, ey + abs(d[0]) * 1.2)
            if glob:
                R.items.append([Sym("global_label"), net, [Sym("shape"), Sym("bidirectional")], at(ex, ey, angle),
                                [Sym("fields_autoplaced"), Sym("yes")],
                                effects(LBL, justify="left" if angle in (0, 90) else "right"),
                                [Sym("uuid"), uid(su, "gl", n)],
                                [Sym("property"), "Intersheetrefs", "${INTERSHEET_REFS}", at(ex, ey, angle),
                                 effects(LBL, hide=True)]])
            else:
                R.items.append([Sym("label"), net, at(ex, ey, angle),
                                effects(LBL, justify="left bottom" if angle in (0, 90) else "right bottom"),
                                [Sym("uuid"), uid(su, "lb", n)]])
    _fields(R, design, part, unit, rot, ox, oy, g)
    return R


def _wire(x1, y1, x2, y2, u):
    return [Sym("wire"), [Sym("pts"), [Sym("xy"), x1, y1], [Sym("xy"), x2, y2]],
            [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]], [Sym("uuid"), u]]


def _power_text_pos(net, x, y, d):
    w = tlen(net)
    if d == (1, 0):
        return x + 3.6 + w / 2, y
    if d == (-1, 0):
        return x - 3.6 - w / 2, y
    if d == (0, -1):
        return x, y - 4.2
    return x, y + 4.4


def _grow_power(R, net, x, y, d):
    tx, ty = _power_text_pos(net, x, y, d)
    w = tlen(net)
    R.grow(x - 1.5, y - 1.5, x + 1.5, y + 1.5)
    R.grow(x + d[0] * 3.0, y + d[1] * 3.0, x, y)
    R.grow(tx - w / 2, ty - 0.8, tx + w / 2, ty + 0.8)


def _fields(R, design, part, unit, rot, ox, oy, g):
    """Reference above / value below for ICs; beside the body for small parts."""
    sym = design.symbols[part.lib_id]
    n_pins = len([p for p in symlib.symbol_pins(sym) if p.unit in (0, unit)])
    units = len(_units(sym))
    ref = part.ref + (chr(64 + unit) if units > 1 else "")
    bx1, by1, bx2, by2 = (ox + g[0], oy + g[1], ox + g[2], oy + g[3]) if g else (ox - 2, oy - 2, ox + 2, oy + 2)
    cx, cy = (bx1 + bx2) / 2, (by1 + by2) / 2
    pp = [symlib.pin_pos(ox, oy, rot, None, p.x, p.y) for p in symlib.symbol_pins(sym) if p.unit in (0, unit)]
    spread_x = max(q[0] for q in pp) - min(q[0] for q in pp) if pp else 0
    spread_y = max(q[1] for q in pp) - min(q[1] for q in pp) if pp else 0
    vert = spread_y > spread_x
    if n_pins <= 4 and vert:
        x = bx2 + 1.2 + max(tlen(ref, FIELD), tlen(part.value, FIELD)) / 2
        pos = [(ref, x, cy - 0.9), (part.value, x, cy + 0.9)]
    elif n_pins <= 4:
        pos = [(ref, cx, by1 - 1.4), (part.value, cx, by2 + 1.4)]
    else:
        pos = [(ref, cx, by1 - 1.6), (part.value, cx, by2 + 1.6)]
    R.fields = pos
    for txt, x, y in pos:
        w = tlen(txt, FIELD)
        R.grow(x - w / 2, y - 0.9, x + w / 2, y + 0.9)
    R.field_angle = 90 if rot in (90, 270) else 0


# ------------------------------------------------------------------ layout
def layout_sheet(design, sheet, net_sheets):
    blocks = []
    for b in sheet.blocks:
        items = []
        for part in b.parts:
            sym = design.symbols[part.lib_id]
            for unit in _units(sym):
                rot = auto_rotation(design, part, sym) if unit == 1 else part.unit_rot.get(unit, 0)
                r = render_unit(design, sheet, part, unit, rot, 0.0, 0.0, net_sheets)
                items.append((part, unit, rot, r.box))
        blocks.append((b, items))
    for paper in ("A3", "A2", "A1"):
        res = _pack(blocks, *PAPERS[paper])
        if res:
            return paper, res
    raise RuntimeError("sheet %s does not fit on A1" % sheet.key)


def _note_size(note):
    if not note:
        return 0.0, 0.0
    lines = note.split("\n")
    return max(len(l) for l in lines) * 1.1 * 0.86 + 4, 3.0 * len(lines) + 3


def _pack(blocks, W, H):
    ml, mt, mr, mb = 12.0, 14.0, 12.0, 36.0
    usable = W - ml - mr
    gap = 5.0
    boxes = []
    for b, items in blocks:
        big = [it for it in items if it[3][3] - it[3][1] > 22 or it[3][2] - it[3][0] > 45]
        small = [it for it in items if it not in big]
        widths = [it[3][2] - it[3][0] for it in items]
        nw, nh = _note_size(b.note)
        title_w = len(b.title) * 1.8 * 0.85 + 6
        row_limit = max(90.0, nw, title_w, sum(it[3][2] - it[3][0] + 4 for it in big) + 6,
                        min(usable, max(widths) * 2.4 + 12) if widths else 0)
        row_limit = min(row_limit, usable)
        placed, x, y, row_h = [], 4.0, 9.0, 0.0
        for it in big + small:
            w, h = it[3][2] - it[3][0], it[3][3] - it[3][1]
            if x + w > row_limit - 2 and x > 4.0:
                x, y, row_h = 4.0, y + row_h + 4.0, 0.0
            placed.append((it, x - it[3][0], y - it[3][1]))
            x += w + 4.5
            row_h = max(row_h, h)
        bw = max(row_limit, max((px + it[3][2] for it, px, py in placed), default=40) + 4, nw, title_w)
        bh = y + row_h + 4.0 + nh
        boxes.append((b, placed, bw, bh))
    out, frames = [], []
    x, y, shelf = ml, mt, 0.0
    for b, placed, bw, bh in boxes:
        if bw > usable:
            return None
        if x + bw > W - mr:
            x, y, shelf = ml, y + shelf + gap, 0.0
        bottom_limit = H - mb if x + bw > W - 190 else H - 10
        if y + bh > bottom_limit:
            return None
        frames.append((b, x, y, bw, bh))
        for (part, unit, rot, box), px, py in placed:
            out.append((part, unit, rot, snap(x + px), snap(y + py)))
        x += bw + gap
        shelf = max(shelf, bh)
    return out, frames


# ------------------------------------------------------------------ writers
def _title_block(design, sheet_title, page_no):
    title = design.title + " - " + sheet_title
    if len(title) > MAX_TITLE:
        raise ValueError("title block overflows: %r is %d characters, the KiCad title field holds %d. "
                         "Shorten the root title or the sheet title (schgen.MAX_TITLE, decision D046)."
                         % (title, len(title), MAX_TITLE))
    return [Sym("title_block"), [Sym("title"), title],
            [Sym("date"), design.date], [Sym("rev"), design.rev],
            [Sym("comment"), 1, getattr(design, "comment1", "")],
            [Sym("comment"), 2, "Schematics authored by %s" % getattr(design, "author", "")],
            [Sym("comment"), 3, "Sheet %s" % page_no],
            [Sym("comment"), 4, getattr(design, "comment4", "")]]


def _power_symbol_item(design, sheet, root_uuid, idx, net, x, y, d):
    lib_id = design.power_nets[net]
    gnd = net in GND_NETS
    if gnd:
        rot = {(0, 1): 0, (1, 0): 90, (0, -1): 180, (-1, 0): 270}[d]
    else:
        rot = {(0, -1): 0, (-1, 0): 90, (0, 1): 180, (1, 0): 270}[d]
    ref = "#PWR%s%03d" % (sheet.key[:2], idx)
    tx, ty = _power_text_pos(net, x, y, d)
    fa = 90 if rot in (90, 270) else 0
    return [Sym("symbol"), [Sym("lib_id"), lib_id], at(x, y, rot), [Sym("unit"), 1],
            [Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), Sym("yes")], [Sym("on_board"), Sym("yes")],
            [Sym("dnp"), Sym("no")], [Sym("uuid"), uid(sheet.key, "pwr", idx)],
            [Sym("property"), "Reference", ref, at(x, y, fa), effects(LBL, hide=True)],
            [Sym("property"), "Value", net, at(tx, ty, fa), effects(LBL)],
            [Sym("property"), "Footprint", "", at(x, y, 0), effects(hide=True)],
            [Sym("pin"), "1", [Sym("uuid"), uid(sheet.key, "pwrpin", idx)]],
            [Sym("instances"), [Sym("project"), design.project,
                                [Sym("path"), "/%s/%s" % (root_uuid, sheet.uuid), [Sym("reference"), ref],
                                 [Sym("unit"), 1]]]]]


def write_sheet(design, sheet, path, root_uuid, page_no):
    net_sheets = design.net_sheets()
    paper, (placements, frames) = layout_sheet(design, sheet, net_sheets)
    body, used = [], set()
    pwr_idx = 0
    for part, unit, rot, x, y in placements:
        r = render_unit(design, sheet, part, unit, rot, x, y, net_sheets)
        used.add(part.lib_id)
        su = uid(sheet.key, part.ref, unit)
        units = len(_units(design.symbols[part.lib_id]))
        props = []
        for i, (txt, fx, fy) in enumerate(r.fields):
            name = "Reference" if i == 0 else "Value"
            props.append([Sym("property"), name, part.ref if i == 0 else part.value, at(fx, fy, r.field_angle),
                          effects(FIELD, bold=(i == 0))])
        props.append([Sym("property"), "Footprint", part.footprint, at(x, y, 0), effects(hide=True)])
        props.append([Sym("property"), "Datasheet", part.fields.get("Datasheet", ""), at(x, y, 0), effects(hide=True)])
        for k, v in part.fields.items():
            if k != "Datasheet":
                props.append([Sym("property"), k, str(v), at(x, y, 0), effects(hide=True)])
        node = [Sym("symbol"), [Sym("lib_id"), part.lib_id], at(x, y, rot), [Sym("unit"), unit],
                [Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), Sym("no") if part.dnp else Sym("yes")],
                [Sym("on_board"), Sym("yes")], [Sym("dnp"), Sym("yes") if part.dnp else Sym("no")],
                [Sym("uuid"), su]] + props
        seen_pins = set()
        for p in symlib.symbol_pins(design.symbols[part.lib_id]):  # all units: KiCad lists every pin per instance
            if p.number not in seen_pins:
                seen_pins.add(p.number)
                node.append([Sym("pin"), p.number, [Sym("uuid"), uid(su, "pin", p.number)]])
        node.append([Sym("instances"), [Sym("project"), design.project,
                                        [Sym("path"), "/%s/%s" % (root_uuid, sheet.uuid),
                                         [Sym("reference"), part.ref], [Sym("unit"), unit]]]])
        body.append(node)
        body += r.items
        for net, ex, ey, d in r.power:
            pwr_idx += 1
            used.add(design.power_nets[net])
            body.append(_power_symbol_item(design, sheet, root_uuid, pwr_idx, net, ex, ey, d))
    for b, x, y, bw, bh in frames:
        body.append([Sym("rectangle"), [Sym("start"), round(x, 2), round(y, 2)],
                     [Sym("end"), round(x + bw, 2), round(y + bh, 2)],
                     [Sym("stroke"), [Sym("width"), 0.254], [Sym("type"), Sym("dash")], [Sym("color"), 72, 72, 160, 1]],
                     [Sym("fill"), [Sym("type"), Sym("none")]], [Sym("uuid"), uid(sheet.key, "frame", b.title)]])
        body.append([Sym("text"), b.title.upper(), at(x + 2, y + 5, 0), effects(1.8, justify="left bottom", bold=True),
                     [Sym("uuid"), uid(sheet.key, "ftitle", b.title)]])
        if b.note:
            # KiCad grows bottom-justified multi-line text upwards from the anchor, so anchor the last line
            body.append([Sym("text"), b.note, at(x + 2, y + bh - 2.5, 0),
                         effects(1.1, justify="left bottom"), [Sym("uuid"), uid(sheet.key, "fnote", b.title)]])
    lib_symbols = [Sym("lib_symbols")]
    for lib_id in sorted(used):
        s = list(design.symbols[lib_id])
        s[1] = lib_id
        lib_symbols.append(s)
    root = [Sym("kicad_sch"), [Sym("version"), 20250114], [Sym("generator"), "eeschema"],
            [Sym("generator_version"), "9.0"], [Sym("uuid"), sheet.uuid], [Sym("paper"), paper],
            _title_block(design, sheet.title, page_no), lib_symbols] + body + [[Sym("embedded_fonts"), Sym("no")]]
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump(root) + "\n")
    return paper


def write_root(design, path, sheet_files, pages):
    root_uuid = uid("root", design.project)
    body = [[Sym("text"), design.title, at(20, 28, 0), effects(5.0, justify="left bottom", bold=True),
             [Sym("uuid"), uid("root", "title")]],
            [Sym("text"), "Rev %s  |  %s  |  Schematics authored by %s  |  %s"
             % (design.rev, design.date, getattr(design, "author", ""), getattr(design, "subtitle", "")),
             at(20, 36, 0), effects(2.0, justify="left bottom"), [Sym("uuid"), uid("root", "sub")]]]
    notes = [l for s in getattr(design, "root_notes", []) for l in textwrap.wrap(s, ROOT_NOTE_WRAP)]
    for i, line in enumerate(notes):
        body.append([Sym("text"), line, at(20, 232 + i * 4, 0), effects(1.5, justify="left bottom"),
                     [Sym("uuid"), uid("root", "note", i)]])
    cols, w, h = 4, 86.0, 26.0
    for i, (s, f) in enumerate(zip(design.sheets, sheet_files)):
        cx, cy = 20 + (i % cols) * (w + 10), 50 + (i // cols) * (h + 18)
        body.append([Sym("sheet"), at(cx, cy)[:3], [Sym("size"), w, h], [Sym("fields_autoplaced"), Sym("yes")],
                     [Sym("stroke"), [Sym("width"), 0.254], [Sym("type"), Sym("solid")]],
                     [Sym("fill"), [Sym("color"), 255, 255, 225, 1]], [Sym("uuid"), s.uuid],
                     [Sym("property"), "Sheetname", "%s %s" % (s.key[:2], s.title), at(cx, cy - 0.7, 0),
                      effects(1.6, justify="left bottom", bold=True)],
                     [Sym("property"), "Sheetfile", f, at(cx, cy + h + 0.6, 0), effects(1.1, justify="left top")],
                     [Sym("instances"), [Sym("project"), design.project,
                                         [Sym("path"), "/" + root_uuid, [Sym("page"), str(pages[i])]]]]])
        if s.description:
            long = [l for l in s.description.split("\n") if len(l) > MAX_BOX_LINE]
            if long:
                raise ValueError("cover box description of sheet %s overflows the 86 mm box: %d characters "
                                 "> %d (schgen.MAX_BOX_LINE, decision D046) - %r"
                                 % (s.key, len(long[0]), MAX_BOX_LINE, long[0]))
            body.append([Sym("text"), s.description, at(cx + 2.5, cy + 4, 0), effects(1.3, justify="left top"),
                         [Sym("uuid"), uid("root", "desc", s.key)]])
    root = [Sym("kicad_sch"), [Sym("version"), 20250114], [Sym("generator"), "eeschema"],
            [Sym("generator_version"), "9.0"], [Sym("uuid"), root_uuid], [Sym("paper"), "A3"],
            _title_block(design, "Cover / Hierarchy", 1), [Sym("lib_symbols")]] + body + \
           [[Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]], [Sym("embedded_fonts"), Sym("no")]]
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump(root) + "\n")
    return root_uuid
