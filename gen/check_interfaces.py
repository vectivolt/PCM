"""Cross-board interface check: every cable of PV-P75, PV-P100/110 and DAB-D60, from the exported netlists.

Ground truth = hardware/<board>/outputs/<board>_netlist.xml (pin numbers, pin functions, KiCad pin types) and the
contracts in gen/interfaces.py. For every cable it proves
  (a) pin map    each end carries the contract signal on the same pin (board prefixes / renames declared in module())
  (b) direction  tracing through series resistors, ferrites, fuses, diodes, contacts, the slow-signal mux and the
                 other cables of the module, exactly one end drives each signal and the other end reads it: two
                 push-pull outputs = fight, no driver, no reader; a deliberate spare is listed as such
  (c) power      each supply pin is sourced at exactly one end and loaded at the other; return pins are returns
The SYS-IO-AUX DO/DI <-> port-board control harness is drawn nowhere: it is built here by name (interfaces.DO / DI,
GND to GND), as gen/build_all.py describes it, and its isolated DI inputs must get their field return through it.
Prints one line per cable and one per problem; exit 1 on any problem.
Usage: .venv/bin/python gen/check_interfaces.py [-v]      (-v: the endpoints found at both ends of every pin)
"""
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import interfaces as IF  # noqa: E402

DRIVE, OPEN, READ = {"output", "tri_state", "power_out"}, {"open_collector", "open_emitter"}, {"input", "power_in"}
PASSIVE_DRIVERS = {("TPS272C45D", "VOUT1"), ("TPS272C45D", "VOUT2")}          # high-side switch outputs drawn passive
LOADS = {("HVC43MC", "COIL+"), ("G7L2AX", "COIL"), ("RJ45", "LEDG_K"), ("RJ45", "LEDY_K"), ("RJ45", "CTD"),
         ("RJ45", "CRD")}                                                     # coils, LEDs, magnetics centre taps
CONTACTS = {("HVC43MC", "AUX_NC")}                                            # dry contact to GND = open drain
SOURCES = {("TPS26600", "OUT"), ("TPS16630", "OUT"), ("LMR36015", "SW"), ("LMR36015_5V", "SW"), ("LMR38020", "SW"),
           ("TPS62133", "SW"), ("TPS62130", "SW"), ("VS-8ETU04S", "K")}       # supply outputs drawn passive
SERIES = {"R", "FB", "L", "F", "NT", "D", "S", "RT"}                          # 2-terminal parts a signal passes through
SUPPLY_SERIES = {"FB", "L", "F", "NT"}                                        # ... and a supply (plus 0R)
MUX = {"TMUX1208": "D"}                                                       # analog switch: an S pin reaches D
MDI = {"ETH_TXP": ("TD_P", "TD+"), "ETH_TXN": ("TD_M", "TD-"), "ETH_RXP": ("RD_P", "RD+"), "ETH_RXN": ("RD_M", "RD-")}


class Board:
    """One exported netlist: parts, nets (short names), pin -> net, rails (power symbols) and the MCU pin plan."""

    def __init__(self, name, xml_text=None):
        path = os.path.join(REPO, "hardware", name, "outputs", name + "_netlist.xml")
        root = ET.fromstring(xml_text) if xml_text else ET.parse(path).getroot()
        self.name, self.part, self.value, self.nets, self.net_of = name, {}, {}, {}, {}
        for c in root.find("components"):
            self.part[c.get("ref")], self.value[c.get("ref")] = c.find("libsource").get("part"), c.findtext("value")
        for n in root.find("nets"):
            net = n.get("name") if n.get("name").startswith("unconnected-") else n.get("name").split("/")[-1]
            self.nets[net] = []
            for x in n.findall("node"):
                ref, pin, f = x.get("ref"), x.get("pin"), x.get("pinfunction") or ""
                func = f[:-len(pin) - 1] if f.endswith("_" + pin) else f          # KiCad writes '<name>_<pin>'
                self.nets[net].append((ref, pin, func, x.get("pintype")))
                if not net.startswith("unconnected-"):
                    self.net_of[(ref, pin)] = net
        sym = open(os.path.join(REPO, "hardware", name, name + ".kicad_sym")).read()   # power symbols = the rails
        self.rails = {r for r in re.findall(r'\(symbol "PWR_([^"]+?)"', sym) if r != "FLAG" and not re.search(r"_\d_\d$", r)}
        self.mcu, self.plan = None, {}
        mcu = [r for r, p in self.part.items() if p == "F28388D"]
        if mcu:                                                                # GPIO balls are typed I/O: use the plan
            self.mcu = mcu[0]
            kind = {"in": "input", "ain": "input", "out": "output", "io": "bidirectional"}
            for row in csv.DictReader(open(os.path.join(HERE, "data", "ctrl_c2000_pin_plan.csv"))):
                self.plan[row["ball"]] = kind[row["direction"]]

    def is_return(self, net):
        return net in self.rails and ("GND" in net or net == "PE")

    def pins_of(self, ref):
        return {pin: net for (r, pin), net in self.net_of.items() if r == ref}

    def classify(self, ref, pin, func, ptype):
        """drive / od / read / bidir from the KiCad pin type (plus the few outputs, loads and contacts drawn passive)."""
        part = self.part.get(ref, "")
        if ref == self.mcu:
            ptype = self.plan.get(pin, ptype)
        if ptype in DRIVE or (part, func) in PASSIVE_DRIVERS:
            return "drive"
        if ptype in OPEN or (part, func) in CONTACTS or (ref[0] == "Q" and func == "D"):
            return "od"
        if ptype in READ or (part, func) in LOADS or (ref[0] == "Q" and func == "G"):
            return "read"
        return "bidir" if ptype == "bidirectional" else None

    def far_end(self, ref, pin, prefixes):
        """Net at the other terminal of a 2-terminal part whose designator prefix is in `prefixes`, else None."""
        if re.match(r"[A-Z]+", ref).group() not in prefixes:
            return None
        pins = self.pins_of(ref)
        return [n for p, n in pins.items() if p != pin][0] if len(pins) == 2 else None


class System:
    """Board instances of one module and its cables; a cable row = (pin at end a, pin at end b, signal, kind)."""

    def __init__(self, boards):
        self.board, self.cables, self.mate = boards, [], {}

    def add(self, name, a, ref_a, b, ref_b, rows, spare=()):
        cab = dict(name=name, a=a, ref_a=ref_a, b=b, ref_b=ref_b, rows=rows, spare=set(spare))
        self.cables.append(cab)
        for pa, pb, sig, kind in rows:
            if pa and pb:
                self.mate[(a, ref_a, pa)], self.mate[(b, ref_b, pb)] = (b, ref_b, pb), (a, ref_a, pa)
        return cab

    def trace(self, inst, net, cab, supply=False):
        """Endpoints (inst, ref, pin, func, kind) reachable from (inst, net) without crossing cable `cab`.
        Signal mode stops at the first net with an active pin; a resistor to a rail is recorded as pu / pd, a part
        to another pin of `cab` as term, an unmodelled connector (field terminal, JTAG) as ext.
        supply=True follows fuses, ferrites, inductors, net ties, 0R and cables, and records sources and loads."""
        own = {(cab["a"], cab["ref_a"]), (cab["b"], cab["ref_b"])}
        cab_nets = {(i, n) for i, r in own for n in self.board[i].pins_of(r).values()}
        start, ends, seen, todo = (inst, net), [], {(inst, net)}, [(inst, net)]
        if net in self.board[inst].rails and not supply:          # pin tied to a rail: judge() treats it as driven
            return []
        while todo:
            i, n = todo.pop()
            b = self.board[i]
            if (i, n) != start and (b.is_return(n) or (n in b.rails and not supply)):
                continue
            nodes = b.nets.get(n, [])
            if supply:
                act = [(i, r, p, f, "source") for r, p, f, t in nodes if t == "power_out" or (b.part[r], f) in SOURCES]
                act += [(i, r, p, f, "load") for r, p, f, t in nodes if t == "power_in" or (b.part[r], f) in LOADS]
            else:
                act = [(i, r, p, f, b.classify(r, p, f, t)) for r, p, f, t in nodes]
                act = [e for e in act if e[4]]
            ends += act
            if act and (i, n) != start and not supply:
                continue
            for r, p, f, t in nodes:
                if (i, r) in own:
                    continue
                if (i, r, p) in self.mate:                                     # another cable of the module
                    mi, mr, mp = self.mate[(i, r, p)]
                    far = [(mi, self.board[mi].net_of.get((mr, mp)))]
                elif r.startswith("J"):
                    ends.append((i, r, p, f, "ext"))
                    continue
                elif b.part[r] in MUX and not supply:                          # S pin -> common D pin of the mux
                    far = [(i, m) for q, m in b.pins_of(r).items() if [x for x in b.nets[m] if x[:3] == (r, q, MUX[b.part[r]])]]
                else:
                    zero = b.value.get(r) == "0R"
                    one = b.far_end(r, p, SUPPLY_SERIES | ({"R"} if zero else set())) if supply else b.far_end(r, p, SERIES)
                    if one is None or (i, one) in seen:
                        continue
                    if not supply and one in b.rails:                  # pull-up / pull-down (or a clamp: ignored)
                        if r[0] == "R":
                            ends.append((i, r, p, f, "pd" if b.is_return(one) else "pu"))
                        continue
                    if not supply and (i, one) in cab_nets:            # termination / element to a partner pin
                        ends.append((i, r, p, f, "term"))
                        continue
                    far = [(i, one)]
                for x in far:
                    if x[1] and x not in seen:
                        seen.add(x)
                        todo.append(x)
        return ends


def kinds(ends, *ks):
    return [e for e in ends if e[4] in ks]


def show(ends):
    return ", ".join(sorted({"%s:%s.%s(%s)" % (i, r, f or p, k) for i, r, p, f, k in ends})) or "nothing"


def judge(s, cab, pa, pb, sig, kind):
    """(verdict, endpoints at a, endpoints at b); verdict = None (ok), 'spare' (declared, unused) or a problem."""
    A, B, a, b = s.board[cab["a"]], s.board[cab["b"]], cab["a"], cab["b"]
    na, nb = A.net_of.get((cab["ref_a"], pa)), B.net_of.get((cab["ref_b"], pb))
    if na is None or nb is None:
        return ("spare" if sig in cab["spare"] else "%s: not connected at %s" % (sig, a if na is None else b)), [], []
    if kind == "return":
        bad = [x for x, brd, n in ((a, A, na), (b, B, nb)) if not brd.is_return(n)]
        return ("%s: return pin is not a return at %s" % (sig, bad[0]) if bad else None), [], []
    if kind.startswith("power"):
        if A.is_return(na) or B.is_return(nb):
            return "%s: supply pin is a return at %s" % (sig, a if A.is_return(na) else b), [], []
        ea, eb = s.trace(a, na, cab, supply=True), s.trace(b, nb, cab, supply=True)
        src = [x for x, e in ((a, ea), (b, eb)) if kinds(e, "source")]
        want, load = (a, eb) if kind == "power>" else (b, ea)
        if src != [want]:
            return "%s: supplied by %s, expected %s only" % (sig, " and ".join(src) or "neither end", want), ea, eb
        return (None if kinds(load, "load") else "%s: no load at the receiving end" % sig), ea, eb
    if kind == "mdi":                                     # transformer-coupled pair: check the pin functions
        fa, fb = MDI[sig]
        ok = any(f == fa for r, p, f, t in A.nets[na]) and any(f == fb for r, p, f, t in B.nets[nb])
        return (None if ok else "%s: not PHY %s <-> magnetics %s" % (sig, fa, fb)), [], []
    ea, eb = s.trace(a, na, cab), s.trace(b, nb, cab)
    tied = {a: [(a, "rail", "", na, "drive")] if na in A.rails else [], b: [(b, "rail", "", nb, "drive")] if nb in B.rails else []}
    e = {a: ea, b: eb}
    push = {x: kinds(e[x], "drive") + tied[x] for x in (a, b)}
    if kind == "sense":                                   # b: element to a return / the partner; a: bias + reader
        if not (kinds(eb, "pd", "term") or tied[b]):
            return "%s: no sensing element at %s" % (sig, b), ea, eb
        ok = tied[a] or (kinds(ea, "read") and kinds(ea, "pu", "drive"))
        return (None if ok else "%s: no bias + reader at %s" % (sig, a)), ea, eb
    if kind == "loop":                                    # b joins the loop pins through contacts; a wets and reads
        if not kinds(eb, "term"):
            return "%s: loop not closed at %s" % (sig, b), ea, eb
        return (None if kinds(ea, "pu", "read") else "%s: loop not wetted / read at %s" % (sig, a)), ea, eb
    if push[a] and push[b]:
        return "%s: two outputs fight: %s | %s" % (sig, show(push[a]), show(push[b])), ea, eb
    if kind in ("bus", "od"):
        both = ea + eb
        if kind == "od" and push[a] + push[b]:
            return "%s: push-pull output on an open-drain line: %s" % (sig, show(push[a] + push[b])), ea, eb
        if not kinds(both, "drive", "od", "bidir"):
            return "%s: nothing drives" % sig, ea, eb
        if kind == "od" and not kinds(both, "pu"):
            return "%s: open-drain line without a pull-up" % sig, ea, eb
        return (None if kinds(both, "read", "bidir") else "%s: nothing reads" % sig), ea, eb
    src, dst = (a, b) if kind.startswith(">") else (b, a)
    sources = push[src] + kinds(e[src], "od", "bidir")
    if kind.endswith("field") and kinds(e[src], "pu"):    # open-collector field device on a terminal of the src board
        sources += kinds(e[src], "ext")
    if not sources:
        wrong = push[dst] + kinds(e[dst], "od")
        return "%s: %s" % (sig, "driven from the wrong end: " + show(wrong) if wrong else "nothing drives"), ea, eb
    if kinds(sources, "od") and not push[src] and not kinds(ea + eb, "pu"):
        return "%s: open-drain driver without a pull-up" % sig, ea, eb
    if not kinds(e[dst], "read", "bidir"):
        verdict = "spare" if sig in cab["spare"] else "%s: nothing reads at %s (driven by %s)" % (sig, dst, show(sources))
        return verdict, ea, eb
    return None, ea, eb


def find_conn(board, part, expect):
    """The instance of connector `part` whose pins carry most of the expected nets (a swapped pin is still found)."""
    cands = [r for r, p in board.part.items() if p == part]
    return max(cands, key=lambda r: sum(board.net_of.get((r, p)) == n for p, n in expect.items()))


def contract(s, name, table, a, part_a, b, part_b, kind, pre_a="", ren_a=None, pre_b="", ren_b=None, nc_b=(), spare=()):
    """A 1:1 cable on an interfaces.py table. Returns (cable, pin-map problems = check a)."""
    exp_a, exp_b = IF.pins(table, pre_a, ren_a), IF.pins(table, pre_b, ren_b)
    ra, rb = find_conn(s.board[a], part_a, exp_a), find_conn(s.board[b], part_b, exp_b)
    probs = []
    for p, sig in enumerate(table, 1):
        p = str(p)
        for x, ref, exp in ((a, ra, exp_a[p]), (b, rb, None if sig in nc_b else exp_b[p])):
            got = s.board[x].net_of.get((ref, p))
            if got != exp:
                probs.append("pin %s %s: %s:%s carries %s, contract says %s" % (p, sig, x, ref, got or "no connection",
                                                                            exp or "not connected (declared spare)"))
    rows = [(str(p), str(p), sig, kind(sig)) for p, sig in enumerate(table, 1)]
    return s.add(name, a, ra, b, rb, rows, spare=spare), probs


def harness(s, sysi, porti):
    """SYS-IO-AUX DO/DI terminals <-> port-board 12-way control connector, matched by name. Returns (cables, problems)."""
    S, P = s.board[sysi], s.board[porti]
    jc = [r for r, p in P.part.items() if p == "J_CTRL"][0]
    terms = {}                                                     # DO / DI name -> [(terminal ref, pin)] on SYS
    for r, part in S.part.items():
        if part in ("J_MC4", "J_MC10"):
            for p, n in S.pins_of(r).items():
                key = n[4:] if n.startswith("DIF_") else n
                if key in IF.DO or key in IF.DI:
                    terms.setdefault(key, []).append((r, p))
    blocks = sorted({t[0] for v in terms.values() for t in v})
    gnd = [(r, p) for r in blocks for p, n in sorted(S.pins_of(r).items()) if n == "GND"]
    port_pins, rows, probs = P.pins_of(jc), [], []
    for p, n in sorted(port_pins.items(), key=lambda x: int(x[0])):
        hit = gnd[:1] if n == "GND" else terms.get(n, [])
        if len(hit) != 1:
            probs.append("%s:%s.%s %s: %d matching SYS-IO-AUX terminal pins" % (porti, jc, p, n, len(hit)))
            continue
        if n == "GND":
            gnd.pop(0)
        rows.append((hit[0][0], hit[0][1], p, n, "return" if n == "GND" else ">" if n in IF.DO else "<"))
    spare = [d for d in IF.DO if d not in port_pins.values() and d in terms]   # DO with no port pin: spare there
    rows += [(terms[d][0][0], terms[d][0][1], None, d, ">") for d in spare]
    cabs = [s.add("HARN", sysi, r, porti, jc, [(q, p, sig, k) for rr, q, p, sig, k in rows if rr == r], spare=spare)
            for r in sorted({x[0] for x in rows})]
    linked = {S.net_of[(r, q)] for r, q, p, sig, k in rows if k == "return"}   # SYS nets the harness joins to the port
    for cab in cabs:
        for q, p, sig, k in cab["rows"]:
            if k != "<":
                continue
            for u in {e[1] for e in s.trace(sysi, S.net_of[(cab["ref_a"], q)], cab) if S.part.get(e[1]) == "ISO1212"}:
                ret = {S.net_of[(u, x)] for x, n in S.pins_of(u).items() if [y for y in S.nets[n] if y[:3] == (u, x, "FGND1") or
                                                                              y[:3] == (u, x, "FGND2")]}
                if not ret & linked:                                # an isolated sinking input needs its field return
                    probs.append("%s: %s.IN returns to %s, which the harness does not join to %s (it joins %s): the "
                                 "input current has no return" % (sig, u, "/".join(sorted(ret)), porti, "/".join(linked)))
    return cabs, probs


# ---------------------------------------------------------------------------------------------------- modules
def k_cell(sig):                                             # a = CTRL-C2000 cell port, b = power board
    if sig == "+24V":
        return "power>"
    if sig in ("GND", "AGND"):
        return "return"
    return ">" if sig.startswith(("PWM", "EN_")) else "<" if sig.startswith(("FLT_", "RDY", "AN")) else "sense"


def k_port(sig):                                             # a = CTRL-C2000, b = port board
    return "power>" if sig == "+24V" else "return" if sig in ("GND", "AGND") else "sense" if sig == "ID" else "<"


SYS_OUT = {"CANA_TX", "CANB_TX", "MCAN_TX", "SCIA_TX", "SCIA_DE", "SCIB_TX", "SCIB_DE", "SCIC_TX", "SCIC_DE", "SPI_CLK",
           "SPI_SIMO", "SPI_CS_ADC_N", "SPI_CS_IO_N", "FLT_CLR", "WDI", "ETH_LED_LINK", "ETH_LED_ACT"}
SYS_IN = ["CANA_RX", "CANB_RX", "MCAN_RX", "SCIA_RX", "SCIB_RX", "SCIC_RX", "SPI_SOMI", "PG_24V", "FLT_LATCH_N",
          "GATE_EN", "ESTOP_N", "PG_HVAUX"] + ["FAN_TACH%d" % i for i in range(1, 5)] + ["DI%d" % i for i in range(1, 9)]


def k_sys(sig):                                              # a = CTRL-C2000, b = SYS-IO-AUX
    special = {"+24V": "power<", "+24V_GD": "power<", "ETH_CT": "power>", "GND": "return", "I2C_SDA": "bus",
               "I2C_SCL": "bus", "SYS_RST_N": "od"}
    if sig in special or sig in MDI:
        return special.get(sig, "mdi")
    if sig.startswith("FAN_TACH"):
        return "<field"
    return ">" if sig in SYS_OUT or sig.startswith(("DO", "FAN_PWM")) else "<"


def k_gw(sig):                                               # a = SYS-IO-AUX, b = BMU-GW
    return {"5V": "power>", "3V3": "power>", "GND": "return", "CAN_RX": "<", "RS485_R": "<"}.get(sig, ">")


def k_aux(sig):                                              # a = AUX-HV, b = SYS-IO-AUX
    return {"+24V_HVAUX": "power>", "GND": "return"}.get(sig, ">")


PWM_SPARE = ["PWM%d_%s" % (k, x) for k in range(5, 9) for x in "PN"]          # 4 x 2 commands per port, 4 x 1 used


def module(name, boards):
    """Board instances and cables of one module (gen/build_all.py MODULES): System + [(cables, pin-map problems)]."""
    cells = {"PV-P75": 3, "PV-P100-110": 4}.get(name, 0)
    port = "PV-PORT" if cells else "DAB60"
    inst = {n: boards[n] for n in ("CTRL-C2000", "SYS-IO-AUX", "BMU-GW", "AUX-HV", port)}
    inst.update({"PVCELL-25#%d" % k: boards["PVCELL-25"] for k in range(1, cells + 1)})
    s, groups = System(inst), []
    sys_ren = dict({"SYS_RST_N": "XRSn", "CHB_OK": "S_CHB_OK"}, **{n: "S_" + n for n in SYS_IN})
    gw_ren = {"CAN_TX": "CANA_TX", "CAN_RX": "CANA_RX", "RS485_D": "SCIC_TX", "RS485_R": "SCIC_RX", "RS485_DE": "SCIC_DE",
              "RS485_RE_N": "GND"}
    for cab, probs in (
            contract(s, "SYS", IF.SYS, "CTRL-C2000", "J_SYS", "SYS-IO-AUX", "J_SYS", k_sys, ren_a=sys_ren),
            contract(s, "GW", IF.GW, "SYS-IO-AUX", "J_GW", "BMU-GW", "J_HOST", k_gw, ren_a=gw_ren),
            contract(s, "AUX", IF.AUX, "AUX-HV", "J_OUT", "SYS-IO-AUX", "J_PC5_3", k_aux, ren_b={"PG_HVAUX": "PG_HVAUX_IN"})):
        groups.append(([cab], probs))
    aux = [x for x in IF.PORT if x.startswith("AUX")] if not cells else []         # no insulation measurement on DAB60
    cab, probs = contract(s, "PORT", IF.PORT, "CTRL-C2000", "J_PORT", port, "J_PORT", k_port, pre_a="P_",
                          ren_a={"+24V": "P_24V"}, nc_b=aux, spare=aux)
    groups.append(([cab], probs))
    for k in range(1, (cells or 2) + 1):
        b, nc = ("PVCELL-25#%d" % k, []) if cells else (port, PWM_SPARE + ["NTC_P", "NTC_N"])
        pre_b, ren_b = ("", {}) if cells else ("B%d_" % k, {"+24V": "+24V_B%d" % k})
        cab, probs = contract(s, "C%d" % k, IF.CELL, "CTRL-C2000", "J_CELL", b, "J_CELL", k_cell, pre_a="C%d_" % k,
                              ren_a={"+24V": "C%d_24V" % k, "NTC_N": "AGND"}, pre_b=pre_b,
                              ren_b=dict(ren_b, AN2_N="AGND", AN3_N="AGND"), nc_b=nc, spare=PWM_SPARE + nc)
        groups.append(([cab], probs))
    if not cells:                                     # DAB60 coolant loop -> SYS-IO-AUX J801 spare trip loop, 1:1
        cab, probs = contract(s, "LOOP", ["TRIP_SRC", "TRIP_IN", "GND", "GND"], "SYS-IO-AUX", "J_MC4", port, "J_MC4",
                              lambda x: "return" if x == "GND" else "loop", nc_b=["GND"], spare=["GND"])
        groups.append(([cab], probs))
    groups.append(harness(s, "SYS-IO-AUX", port))
    return s, groups


def check(name, boards, verbose=False):
    """[(cable line, pins checked, spares, problems)] for one module."""
    s, groups = module(name, boards)
    out = []
    for cabs, probs in groups:
        probs, spares, n = list(probs), [], 0
        for cab in cabs:
            for pa, pb, sig, kind in cab["rows"]:
                verdict, ea, eb = judge(s, cab, pa, pb, sig, kind) if pa and pb else ("spare", [], [])
                n += bool(pa and pb)
                if verdict == "spare":
                    spares.append(sig)
                elif verdict:
                    probs.append("pin %s/%s %s" % (pa, pb, verdict))
                if verbose:
                    print("      %-5s %-13s %-7s %s  ||  %s" % (pa, sig, kind, show(ea), show(eb)))
        c = cabs[0]
        ref_a = "+".join(x["ref_a"] for x in cabs)
        out.append(("%-4s %s:%s <-> %s:%s" % (c["name"], c["a"], ref_a, c["b"], c["ref_b"]), n, sorted(set(spares)), probs))
    return out


def self_check():
    """A copy of the BMU-GW netlist with host pins 5 and 6 (CAN_TX / CAN_RX) swapped must fail (a) and (b)."""
    text = open(os.path.join(REPO, "hardware", "BMU-GW", "outputs", "BMU-GW_netlist.xml")).read()
    ref = [r for r, p in Board("BMU-GW").part.items() if p == "J_HOST"][0]
    p5, p6 = 'ref="%s" pin="5"' % ref, 'ref="%s" pin="6"' % ref
    swapped = text.replace(p5, "@@").replace(p6, p5).replace("@@", p6)
    assert swapped != text
    boards = load()
    good = {l[0][:2]: l[3] for l in check("PV-P75", boards)}["GW"]
    bad = {l[0][:2]: l[3] for l in check("PV-P75", dict(boards, **{"BMU-GW": Board("BMU-GW", swapped)}))}["GW"]
    new = [p for p in bad if p not in good]
    assert any(p.startswith("pin 5 CAN_TX") for p in new), new                         # (a) pin map
    assert any("fight" in p or "nothing drives" in p or "wrong end" in p for p in new), new   # (b) direction
    sysb = boards["SYS-IO-AUX"]
    assert sysb.is_return("GND") and not sysb.is_return("3V3") and sysb.classify("U905", "1", "VOUT1", "passive") == "drive"


def load():
    names = ("CTRL-C2000", "SYS-IO-AUX", "BMU-GW", "AUX-HV", "PV-PORT", "PVCELL-25", "DAB60")
    return {n: Board(n) for n in names}


def main():
    self_check()
    boards, total, verbose = load(), 0, "-v" in sys.argv
    for mod in ("PV-P75", "PV-P100-110", "DAB-D60"):
        for line, n, spares, probs in check(mod, boards, verbose):
            total += len(probs)
            print("%-11s %-62s %2d pins %s%s" % (mod, line, n, "ok" if not probs else "%d PROBLEM(S)" % len(probs),
                                                "; spare: " + " ".join(spares) if spares else ""))
            for p in probs:
                print("    PROBLEM %s" % p)
    print("%d problem(s)" % total)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
