"""CTRL-C2000 - common TMS320F28388D real-time control card (requirements ECO-01, ECO-02, ECO-05, ECO-09).

  MCU      TMS320F28388DZWTS, 337-ball ZWT (pin table extracted from SPRSP14E into gen/data/f28388d_pins.csv)
  ports    C1..C4  40-way CELL contract: 8 PWM RS-422 pairs, FLT receiver (fail-safe), EN driver, RDY, ID, 3 analog, NTC
           J_PORT  26-way PORT contract: 8 analog pairs, ID
           J_SYS   80-way SYS contract to SYS-IO-AUX: +24 V in, CAN/SCI/SPI/I2C, DI/DO, fault latch, fans, Ethernet MDI
  power    +24V -> LMR38020 5 V -> TPS62133 3.3 V -> (PG) TPS62130 1.2 V and TPS7A2033 3.3 V analog -> REF5025E 2.5 V
  pin plan gen/data/ctrl_c2000_pin_plan.csv, generated from the same PLAN table that wires the MCU symbol.
Usage: .venv/bin/python gen/ctrl_c2000.py
"""
import csv
import json
import os
import re
import subprocess
import sys
from itertools import product

import catalog
import dcdclib as L
import interfaces as IF

PROJECT, REV, DATE = "CTRL-C2000", "E0", "2026-10-04"
HERE = os.path.dirname(os.path.abspath(__file__))
MCU_DS = "docs/datasheets/controllers/TMS320F28388D.pdf"
PINS_CSV = os.path.join(HERE, "data", "f28388d_pins.csv")
PLAN_CSV = os.path.join(HERE, "data", "ctrl_c2000_pin_plan.csv")

# ======================================================================== 1. F28388D ZWT pin table (SPRSP14E Rev E)
BALL = re.compile(r"[A-HJ-NPRTUVW]\d{1,2}$")
POWER = {"VDD", "VDDIO", "VDD3VFL", "VDDOSC", "VDDA", "VSS", "VSSA", "VSSOSC"}


def extract_pins():
    """Read the ZWT pin table out of the datasheet PDF. Three sources are cross-checked:
      Figure 6-1 ball map (p.12)            ball -> name for all 337 balls
      Table 6-1 Pin Attributes (p.18-50)    GPIO mux positions + ball, pin types, power-ball lists
      Table 6-5 Test/JTAG/Reset (p.71)      NC1 / NC2 ball names
    Returns rows {ball, name, type, mux}; raises on any disagreement."""
    T = subprocess.run(["pdftotext", "-layout", os.path.join(L.REPO, MCU_DS), "-"], capture_output=True, text=True,
                       check=True).stdout.split("\n")
    # --- Figure 6-1: 19 columns; analog cells are three text lines ("ADCINB1" / row / ",DACOUTC")
    s = next(i for i, l in enumerate(T) if re.match(r"\s+1\s+2\s+3\s+4\s+5\s+6\s+7\s", l))
    e = next(i for i in range(s, len(T)) if "Not to scale" in T[i])
    cols = [(m.start() + len(m.group()) / 2, m.group()) for m in re.finditer(r"\d+", T[s])]
    bmap = {}
    for i in range(s + 1, e):
        m = re.match(r"\s{3,6}([A-HJ-NPRTUVW])\s", T[i])
        if not m:
            continue
        cells = {}
        for j in (i - 1, i, i + 1):
            for t in re.finditer(r"\S+", T[j]):
                if j == i and t.start() < 10:
                    continue
                c = min(cols, key=lambda c: abs(c[0] - t.start() - len(t.group()) / 2))[1]
                cells.setdefault(c, []).append((j, t.group()))
        for c, toks in cells.items():
            bmap[m.group(1) + c] = "".join(t for _, t in sorted(toks))
    assert len(bmap) == 337, "ball map: %d balls" % len(bmap)
    # --- Table 6-1 sections
    a = next(i for i, l in enumerate(T) if l.strip() == "Table 6-1. Pin Attributes")
    g = next(i for i in range(a, len(T)) if T[i].strip() == "GPIO")
    t = next(i for i in range(g, len(T)) if T[i].strip() == "TEST, JTAG, AND RESET")
    p = next(i for i in range(t, len(T)) if T[i].strip() == "POWER AND GROUND")
    z = next(i for i in range(p, len(T)) if T[i].startswith("6.3 Signal Descriptions"))
    col = [l.index("337") for l in T[a:g] if "337" in l and "176" in l][-1]
    gpio, cur = {}, None
    for l in T[g:t]:
        if "337" in l and "176" in l:
            col = l.index("337")
            continue
        m = re.match(r"^ (\S+)\s+(\d+(?:, \d+)*|ALT)\s", l)
        if m and m.group(1).startswith("GPIO") and m.group(2) == "0, 4, 8, 12":
            cur = gpio[m.group(1)] = {"mux": [], "balls": []}
        elif m and cur is not None:
            cur["mux"].append((m.group(2).replace(" ", ""), m.group(1)))
        if cur is not None:
            cur["balls"] += [x.group() for x in re.finditer(r"\S+", l) if BALL.match(x.group()) and abs(x.start() - col) < 8]
    types = {}
    for l in T[a:g] + T[t:p]:
        m = re.match(r"^ ([A-Z][A-Za-z0-9]+)\s.*?\s(I/OD|I/O|I|O)(?:\s|$)", l)
        if m and m.group(1) not in types:
            types[m.group(1)] = m.group(2)
    pwr_balls = {x.rstrip(",") for l in T[p:z] for x in l.split() if BALL.match(x.rstrip(","))}
    nc = {}
    for l in T[z:]:
        m = re.match(r"^ (NC\d)\s.*\s([A-HJ-NPRTUVW]\d{1,2})(?:\s+\d+)?\s*$", l)
        if m:
            nc[m.group(2)] = m.group(1)
        if len(nc) == 2:
            break
    # --- merge + cross-check
    rows = []
    for ball, cell in bmap.items():
        names = cell.split(",")
        name = nc.get(ball, names[0]) if names[0] == "NC" else names[0]
        if name.startswith("GPIO"):
            assert gpio[name]["balls"] == [ball], "%s: ball map %s, Table 6-1 %s" % (name, ball, gpio[name]["balls"])
            mux, typ = ";".join("%s=%s" % pm for pm in gpio[name]["mux"]), "I/O"
        elif name in POWER:
            assert ball in pwr_balls, "%s %s missing from the Table 6-1 power-ball lists" % (ball, name)
            mux, typ = "", "GND" if name.startswith("VSS") else "PWR"
        elif name.startswith("NC"):
            mux, typ = "", "NC"
        else:
            mux, typ = ";".join("ALT=" + n for n in names[1:]), types[name]
        rows.append({"ball": ball, "name": name, "type": typ, "mux": mux})
    assert pwr_balls == {r["ball"] for r in rows if r["type"] in ("PWR", "GND")}, "power-ball sets differ"
    assert len(gpio) == 169 and sorted(nc.values()) == ["NC1", "NC2"]
    return sorted(rows, key=lambda r: (r["ball"][0], int(r["ball"][1:])))


def load_pins():
    """Regenerate gen/data/f28388d_pins.csv from the PDF, then read it back (the symbol is built from the file).
    Without poppler's pdftotext the committed CSV is used as is."""
    try:
        rows = extract_pins()
    except FileNotFoundError:
        print("pdftotext not found - using the committed %s" % os.path.relpath(PINS_CSV, L.REPO))
        return list(csv.DictReader(open(PINS_CSV)))
    with open(PINS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, ["ball", "name", "type", "mux"])
        w.writeheader()
        w.writerows(rows)
    return list(csv.DictReader(open(PINS_CSV)))


def mcu_entry(pins):
    """Catalog entry for the F28388D: 4 function units + 5 GPIO banks, every ball exactly once."""
    etype = {"I/O": "b", "I": "i", "O": "o", "I/OD": "oc", "PWR": "pi", "GND": "pi", "NC": "p"}
    by = {}
    for r in pins:
        by.setdefault(r["name"], []).append(r)

    def P(*names):
        out = []
        for n in names:
            for r in by[n]:
                alt = [m[4:] for m in r["mux"].split(";") if m.startswith("ALT=")]
                out.append("%s %s %s" % (r["ball"], "/".join([n] + alt), etype[r["type"]]))
        return out

    def bank(lo, hi):
        g = P(*["GPIO%d" % i for i in range(lo, hi + 1)])
        return dict(title="GPIO%d-%d" % (lo, hi), left=g[:(len(g) + 1) // 2], right=g[(len(g) + 1) // 2:])

    vss = P("VSS")
    units = [dict(title="SUPPLY", left=P("VDDIO"), right=P("VDD") + [None] + P("VDD3VFL", "VDDOSC")),
             dict(title="GROUND", left=vss[:34], right=vss[34:]),
             dict(title="ANALOG", min_width=50.8, left=P(*["ADCIN%s%d" % (a, i) for a in "AB" for i in range(6)]) + [None] +
                  P(*["ADCINC%d" % i for i in range(2, 6)]) + [None] + P(*["VREFHI" + a for a in "ABCD"]),
                  right=P(*["ADCIND%d" % i for i in range(6)], "ADCIN14", "ADCIN15") + [None] +
                  P(*["VREFLO" + a for a in "ABCD"]) + [None] + P("VDDA", "VSSA")),
             dict(title="SYSTEM", left=P("X1", "X2", "VSSOSC") + [None] + P("XRSn", "ERRORSTS") + [None] +
                  P("FLT1", "FLT2", "NC1", "NC2"), right=P("TCK", "TMS", "TDI", "TDO", "TRSTn"))]
    units += [bank(*b) for b in ((0, 31), (32, 63), (64, 95), (96, 127), (128, 168))]
    used = [s.split()[0] for u in units for side in ("left", "right") for s in u[side] if s]
    assert sorted(used) == sorted(r["ball"] for r in pins), "MCU symbol must carry every ball exactly once"
    return dict(mfr=TI, mpn="F28388DZWTS", prefix="U", pkg="nFBGA-337 (ZWT) 16x16 mm 0.8 mm", ds=MCU_DS, units=units,
                desc="TMS320F28388D dual C28x + CLA + CM Cortex-M4 MCU, 337-ball ZWT, -40..125 C TJ (S)")


# ======================================================================== 2. catalog (pin tables cited per entry)
DS = "docs/datasheets/"
TI = "Texas Instruments"
WE = "Wurth Elektronik"
SAMTEC = "Samtec"
_TPS6213X = {"left": ["11 PVIN pi", "12 PVIN pi", "10 AVIN pi", None, "13 EN i", "9 SS/TR p", "7 FSW i", "8 DEF i"],
             "right": ["1 SW p", "2 SW p", "3 SW p", None, "14 VOS i", "5 FB i", "4 PG oc", None, "6 AGND pi",
                       "15 PGND pi", "16 PGND pi", "17 EP pi"]}   # SLVSAG7F table 6-1 (RGT VQFN-16 + exposed pad)
_OPA4 = {"left": ["3 +INA i", "2 -INA i", None, "5 +INB i", "6 -INB i", None, "10 +INC i", "9 -INC i", None,
                  "12 +IND i", "13 -IND i"],
         "right": ["1 OUTA o", None, None, "7 OUTB o", None, None, "8 OUTC o", None, None, "14 OUTD o", None,
                   "4 V+ pi", "11 V- pi"]}                         # SBOS777D "Pin Functions: OPA2388 and OPA4388" (PW)
PARTS = {
    # ---- power (LMR38020: SNVSC40E table 6-1 DDA SO-8 PowerPAD; values from table 9-1 row 400 kHz / 5 V / 2 A)
    "LMR38020": dict(mfr=TI, mpn="LMR38020FDDAR", prefix="U", pkg="SO-8 PowerPAD (DDA)", ds=DS + "power-supply/LMR38020.pdf",
                     desc="Sync buck 4.2-80 V in, 2 A, FPWM, 1.0 V ref, PG",
                     pins={"left": ["3 VIN pi", "2 EN i", "4 RT/SYNC i"], "right": ["8 SW p", "7 BOOT p", "5 FB i",
                                                                                   "6 PG oc", "1 GND pi", "9 EP pi"]}),
    "TPS62133": dict(mfr=TI, mpn="TPS62133RGTR", prefix="U", pkg="VQFN-16 3x3 (RGT)", ds=DS + "power-supply/TPS62130.pdf",
                     desc="Sync buck 3-17 V in, 3 A, fixed 3.3 V out", pins=_TPS6213X),
    "TPS62130": dict(mfr=TI, mpn="TPS62130RGTR", prefix="U", pkg="VQFN-16 3x3 (RGT)", ds=DS + "power-supply/TPS62130.pdf",
                     desc="Sync buck 3-17 V in, 3 A, adjustable (VFB 0.8 V)", pins=_TPS6213X),
    # TPS7A20: SBVS338H "Pin Functions: X2SON, SOT-23" (DBV)
    "TPS7A2033": dict(mfr=TI, mpn="TPS7A2033PDBVR", prefix="U", pkg="SOT-23-5 (DBV)", ds=DS + "power-supply/TPS7A20.pdf",
                      desc="LDO 300 mA, 3.3 V fixed, low noise, active discharge",
                      pins={"left": ["1 IN pi", "3 EN i", "2 GND pi"], "right": ["5 OUT po", "4 NC nc"]}),
    # TPS26600: SLVSDG2G pin functions (PWP HTSSOP-16, PowerPAD = RTN); 7.5 p7: I(OL) 1.425-1.5-1.575 A at 8 k,
    # I(CB) (MODE open) 0.045-0.073-0.11 A at 120 k and 2.0-2.21-2.4 A at 5.36 k = eq. 4 I(CB) = 12/RILIM(k) - 0.03 A;
    # p9 tCB 4 ms, retry 540 ms; eq. 1/2 I(dVdT) 4-4.7-5.5 uA x GAIN 23.75-24.6-25.5 / CdVdT = OUT slope;
    # UVLO/OVP to RTN = factory 15 V UVLO / 32.6 V OVP (9.3.1/9.3.2); MODE open = circuit breaker + auto-retry (table 1)
    "TPS26600": dict(mfr=TI, mpn="TPS26600PWPR", prefix="U", pkg="HTSSOP-16 PowerPAD (PWP)",
                     ds=DS + "power-supply/TPS26600.pdf", desc="eFuse 4.2-60 V, 150 mR, 0.1-2.23 A adjustable limit",
                     pins={"left": ["1 IN pi", "2 IN pi", "3 UVLO i", "5 OVP i", "6 MODE i", "7 SHDN i", None,
                                    "9 GND pi", "8 RTN pi", "17 PAD pi"],
                           "right": ["15 OUT p", "16 OUT p", None, "11 ILIM p", "12 dVdT p", "10 IMON o", "14 FLT oc",
                                     None, "4 NC nc", "13 NC nc"]}),
    # TPS3703: SBVS249B pin functions (DSE WSON-6): 1 SENSE, 2 VDD, 3 CT, 4 RESET (open drain), 5 GND, 6 MR (100k
    # internal pull-up, may float). Fig 5-1 / table 12-1: A = window, CT open 10 ms; F = UV only, CT open 1 ms /
    # CT to VDD (10k) 20 ms; 4 = +/-4 % window; 330 = 3.30 V, 050 = 0.50 V. 7.5: VIT +/-0.7 % (+/-1 % below 0.8 V).
    "TPS3703A4330": dict(mfr=TI, mpn="TPS3703A4330DSER", prefix="U", pkg="WSON-6 1.5x1.5 (DSE)",
                         ds=DS + "power-supply/TPS3703.pdf", desc="Window supervisor 3.30 V +/-4 %, 0.7 %, 10 ms, MR",
                         pins={"left": ["1 SENSE i", "6 MR i", "3 CT p"], "right": ["4 RESET oc", "2 VDD pi", "5 GND pi"]}),
    "TPS3703F6050": dict(mfr=TI, mpn="TPS3703F6050DSER", prefix="U", pkg="WSON-6 1.5x1.5 (DSE)",
                         ds=DS + "power-supply/TPS3703.pdf", desc="UV supervisor 0.470 V (0.50 V -6 %), 1 %, 20 ms, MR",
                         pins={"left": ["1 SENSE i", "6 MR i", "3 CT p"], "right": ["4 RESET oc", "2 VDD pi", "5 GND pi"]}),
    # ---- clock (ASDMB: Abracon ASDMB rev I p.7 pin table; 3.3 V tr/tf <= 2 ns, 45/55 %, X = -40..105 C, C = +/-50 ppm)
    "OSC25": dict(mfr="Abracon", mpn="ASDMB-25.000MHZ-XC-T", prefix="Y", pkg="2.5x2.0 mm 4-pad", ds=DS + "timing/ASDMB.pdf",
                  desc="MEMS oscillator 25.000 MHz CMOS, 3.3 V, +/-50 ppm, -40..105 C",
                  pins={"left": ["4 VDD pi", "1 ST i", "2 GND pi"], "right": ["3 OUT o"]}),
    # ---- MCU-side logic and line interfaces
    # AM26LV31E: SLLS848C table 4-1 (D SOIC-16); VCC 3.0-3.6 V; |VOD2| >= 2 V into 100 R
    "AM26LV31E": dict(mfr=TI, mpn="AM26LV31EIDR", prefix="U", pkg="SOIC-16 (D)", ds=DS + "isolation-interface/AM26LV31E.pdf",
                      desc="Quad RS-422 line driver, 3.3 V, 32 MHz, -40..85 C",
                      pins={"left": ["1 1A i", "7 2A i", "9 3A i", "15 4A i", None, "4 G i", "12 ~{G} i", None,
                                     "16 VCC pi", "8 GND pi"],
                            "right": ["2 1Y o", "3 1Z o", "6 2Y o", "5 2Z o", "10 3Y o", "11 3Z o", "14 4Y o", "13 4Z o"]}),
    # AM26LV32E: SLLS849E table 5-1 (D SOIC-16); open-circuit fail-safe only (rev E), VIT +/-200 mV
    "AM26LV32E": dict(mfr=TI, mpn="AM26LV32EIDR", prefix="U", pkg="SOIC-16 (D)", ds=DS + "isolation-interface/AM26LV32E.pdf",
                      desc="Quad RS-422 line receiver, 3.3 V, open-circuit fail-safe, -40..85 C",
                      pins={"left": ["2 1A i", "1 1B i", "6 2A i", "7 2B i", "10 3A i", "9 3B i", "14 4A i", "15 4B i"],
                            "right": ["3 1Y t", "5 2Y t", "11 3Y t", "13 4Y t", None, "4 G i", "12 ~{G} i", None,
                                      "16 VCC pi", "8 GND pi"]}),
    # THVD1450: SLLSEY3E pin functions (D SOIC-8); 7.7: |VOD| >= 2 V into 100 R, bus input current <= 125 uA with
    # DE low or VCC = 0, receiver idle-bus fail-safe (VTH+ <= -20 mV -> R high). DE 2M pull-down, RE 2M pull-up.
    "THVD1450": dict(mfr=TI, mpn="THVD1450DR", prefix="U", pkg="SOIC-8 (D)", ds=DS + "isolation-interface/THVD1450.pdf",
                     desc="RS-485 transceiver 3.3-5 V, 50 Mbps, driver enable (EN line driver + readback)",
                     pins={"left": ["4 D i", "3 DE i", "2 ~{RE} i", "1 R o", None, "8 VCC pi", "5 GND pi"],
                           "right": ["6 A b", "7 B b"]}),
    # SN74LVC11A: SCLS993A pin functions (D SOIC-14); Ioff +/-10 uA
    "LVC11": dict(mfr=TI, mpn="SN74LVC11ADR", prefix="U", pkg="SOIC-14 (D)", ds=DS + "isolation-interface/SN74LVC11A.pdf",
                  desc="Triple 3-input AND gate, 1.65-3.6 V, Ioff",
                  pins={"left": ["1 1A i", "2 1B i", "13 1C i", None, "3 2A i", "4 2B i", "5 2C i", None, "9 3A i",
                                 "10 3B i", "11 3C i", None, "14 VCC pi", "7 GND pi"],
                        "right": ["12 1Y o", None, None, None, "6 2Y o", None, None, None, "8 3Y o"]}),
    # SN74LVC1G17: SCES351Y pin functions (DBV SOT-23-5): 1 NC, 2 A, 3 GND, 4 Y, 5 VCC; Ioff
    "LVC1G17": dict(mfr=TI, mpn="SN74LVC1G17DBVR", prefix="U", pkg="SOT-23-5 (DBV)",
                    ds=DS + "isolation-interface/SN74LVC1G17.pdf", desc="Single Schmitt-trigger buffer, Ioff",
                    pins={"left": ["2 A i", "1 NC nc", "5 VCC pi", "3 GND pi"], "right": ["4 Y o"]}),
    # SN74LVC244A: SCAS414AG table 4-1 (DW SOIC-20); Ioff partial-power-down on inputs and outputs
    "LVC244": dict(mfr=TI, mpn="SN74LVC244ADWR", prefix="U", pkg="SOIC-20 (DW)", ds=DS + "isolation-interface/SN74LVC244A.pdf",
                   desc="Octal buffer, 3-state, Ioff (SYS inputs before VDDIO is up)",
                   pins={"left": ["2 1A1 i", "4 1A2 i", "6 1A3 i", "8 1A4 i", "11 2A1 i", "13 2A2 i", "15 2A3 i",
                                  "17 2A4 i", None, "1 ~{1OE} i", "19 ~{2OE} i", None, "20 VCC pi", "10 GND pi"],
                         "right": ["18 1Y1 t", "16 1Y2 t", "14 1Y3 t", "12 1Y4 t", "9 2Y1 t", "7 2Y2 t", "5 2Y3 t",
                                   "3 2Y4 t"]}),
    # ---- analog
    "OPA4388": dict(mfr=TI, mpn="OPA4388IPWR", prefix="U", pkg="TSSOP-14 (PW)", ds=DS + "sensing/OPA4388.pdf",
                    desc="Quad zero-drift RRIO op amp, 10 MHz, 2.5-5.5 V", pins=_OPA4),
    # OPA2388: SBOS777D "Pin Functions: OPA2388 and OPA4388" (D SOIC-8); IQ 2.6 mA max per channel
    "OPA2388": dict(mfr=TI, mpn="OPA2388IDR", prefix="U", pkg="SOIC-8 (D)", ds=DS + "sensing/OPA4388.pdf",
                    desc="Dual zero-drift RRIO op amp, 10 MHz, 2.5-5.5 V",
                    pins={"left": ["3 +INA i", "2 -INA i", None, "5 +INB i", "6 -INB i"],
                          "right": ["1 OUTA o", None, None, "7 OUTB o", None, "8 V+ pi", "4 V- pi"]}),
    # REF5025E: SBOS410O figure 5-2 / table 5-1 (REF50xxEI SOIC-8); VIN >= VOUT + 0.2 V; CL 1-100 uF
    "REF5025E": dict(mfr=TI, mpn="REF5025EIDR", prefix="U", pkg="SOIC-8 (D)", ds=DS + "sensing/REF5025E.pdf",
                     desc="Voltage reference 2.500 V, 0.025 %, 2.5 ppm/C, enable, CL 1-100 uF",
                     pins={"left": ["2 VIN pi", "1 EN i", "4 GND pi"], "right": ["6 VOUT po", "5 NR p", "3 TEMP o",
                                                                               "7 NC nc", "8 DNC nc"]}),
    # TMUX1208: SCDS389C "Pin Functions TMUX1208" (PW TSSOP-16), truth table 1
    "TMUX1208": dict(mfr=TI, mpn="TMUX1208PWR", prefix="U", pkg="TSSOP-16 (PW)", ds=DS + "sensing/TMUX1208.pdf",
                     desc="8:1 analog multiplexer, 1.08-5.5 V, low leakage",
                     pins={"left": ["13 VDD pi", "2 EN i", "1 A0 i", "16 A1 i", "15 A2 i", None, "3 NC nc", "14 GND pi"],
                           "right": ["4 S1 p", "5 S2 p", "6 S3 p", "7 S4 p", "12 S5 p", "11 S6 p", "10 S7 p", "9 S8 p",
                                     None, "8 D p"]}),
    # BAT54S: Diodes DS11005 rev 34-2 p.1 diagram: pin 1 A1, 2 K2, 3 K1/A2 (= KiCad Diode:BAT54S 1 A, 2 K, 3 COM)
    "BAT54S": dict(mfr="Diodes Incorporated", mpn="BAT54S-7-F", prefix="D", pkg="SOT-23", stock=("Diode", "BAT54S"),
                   ds=DS + "protection/BAT54S-Diodes.pdf", desc="Dual series Schottky 30 V 200 mA (ADC input clamp)"),
    # ---- Ethernet (DP83822: SNLS505H table 5-1, RHB VQFN-32; EP = GND)
    "DP83822I": dict(mfr=TI, mpn="DP83822IRHBR", prefix="U", pkg="VQFN-32 5x5 (RHB)",
                     ds=DS + "isolation-interface/DP83822I.pdf", desc="10/100 Ethernet PHY, MII/RMII, -40..85 C",
                     pins={"left": ["2 TX_CLK o", "3 TX_EN i", "4 TX_D0 i", "5 TX_D1 i", "6 TX_D2 i", "7 TX_D3 i", None,
                                    "25 RX_CLK o", "26 RX_DV o", "28 RX_ER o", "27 CRS o", "29 COL b", "30 RX_D0 o",
                                    "31 RX_D1 o", "32 RX_D2 o", "1 RX_D3 o", None, "20 MDC i", "19 MDIO b",
                                    "8 INT/PWDN_N oc", "18 RESET_N i"],
                           "right": ["12 TD_P p", "11 TD_M p", "10 RD_P p", "9 RD_M p", None, "17 LED_0 o", "24 LED_1 b",
                                     None, "23 XI i", "22 XO o", "16 RBIAS p", None, "13 NC nc", "15 NC nc", None,
                                     "21 VDDIO pi", "14 AVD pi", "33 GND pi"]}),
    # ---- housekeeping
    "24LC256": dict(mfr="Microchip", mpn="24LC256T-I/SN", prefix="U", pkg="SOIC-8 (SN)", ds=DS + "controllers/24LC256.pdf",
                    desc="256 kbit I2C EEPROM, 400 kHz, hardware write protect",   # DS20001203Y pin table (SOIC)
                    pins={"left": ["8 VCC pi", "1 A0 i", "2 A1 i", "3 A2 i", "7 WP i", "4 VSS pi"],
                          "right": ["5 SDA b", "6 SCL i"]}),
    # LEDs: Wurth WL-SMCW 150060VS75000 / RS75000 datasheets p.1: VF 2.0 V typ, 2.4 V max at 20 mA; KiCad LED 1 K, 2 A
    "LED_G": dict(mfr=WE, mpn="150060VS75000", prefix="D", pkg="0603", stock=("Device", "LED"),
                  ds=DS + "passives-capacitors/WE-150060VS75000.pdf", desc="LED bright green 0603, VF 2.0 V typ"),
    "LED_R": dict(mfr=WE, mpn="150060RS75000", prefix="D", pkg="0603", stock=("Device", "LED"),
                  ds=DS + "passives-capacitors/WE-150060RS75000.pdf", desc="LED red 0603, VF 2.0 V typ"),
    # WS-TASV 434121025816 datasheet p.1 schematic: two terminals 1-2, 12 V DC 50 mA
    "SW_RST": dict(mfr=WE, mpn="434121025816", prefix="SW", pkg="SMD 6.0x3.8 mm", stock=("Switch", "SW_Push"),
                   ds=DS + "connectors/WE-434121025816.pdf", desc="Tact switch WS-TASV, 12 V 50 mA (manual reset)"),
    # Inductors: WE-LHMI 74437368150 / WE-MAPI 74438356022 datasheets p.1 (L, ISAT 10 %, RDC max, -40..125 C)
    "L15U": dict(mfr=WE, mpn="74437368150", prefix="L", pkg="WE-LHMI", stock=("Device", "L"),
                 ds=DS + "magnetics/WE-74437368150.pdf", desc="Power inductor 15 uH, ISAT(10%) 5.55 A, 45 mR max"),
    "L2U2": dict(mfr=WE, mpn="74438356022", prefix="L", pkg="WE-MAPI 4020", stock=("Device", "L"),
                 ds=DS + "magnetics/WE-74438356022.pdf", desc="Power inductor 2.2 uH, ISAT(10%) 4.05 A, 35 mR max"),
    # WE-CBF 742792641 datasheet p.1: 300 R at 100 MHz, IR 1.5 A (dT 20 K), RDC 0.15 R max, 0603
    "FB": dict(mfr=WE, mpn="742792641", prefix="FB", pkg="0603", stock=("Device", "FerriteBead_Small"),
               ds=DS + "magnetics/WE-742792641.pdf", desc="Ferrite bead 300 R at 100 MHz, 1.5 A, 0.15 R max"),
    "NETTIE": dict(mfr="", mpn="", prefix="NT", pkg="net tie", ds="", sourcing="NOPART", stock=("Device", "NetTie_2"),
                   desc="Net tie: single AGND-GND star point"),
    # ---- connectors (Wurth WR-BHD box headers: 2.54 mm, 3 A, -40..105 C; Samtec TSW/FTSH series catalog sheets)
    # WR-BHD datasheets p.1 (pitch 2.54 mm, 3 A, 250 VAC); IDC 1:1 numbering = KiCad Conn_02xNN_Odd_Even
    "J_CELL": dict(mfr=WE, mpn="61204021621", prefix="J", pkg="WR-BHD 2x20 2.54 mm THT", ds=DS + "connectors/WE-61204021621.pdf",
                   stock=("Connector_Generic", "Conn_02x20_Odd_Even"), desc="Shrouded box header 2x20, CELL contract"),
    "J_PORT": dict(mfr=WE, mpn="61202621621", prefix="J", pkg="WR-BHD 2x13 2.54 mm THT", ds=DS + "connectors/WE-61202621621.pdf",
                   stock=("Connector_Generic", "Conn_02x13_Odd_Even"), desc="Shrouded box header 2x13, PORT contract"),
    "J_SYS": dict(mfr=SAMTEC, mpn="TSW-140-07-G-D", prefix="J", pkg="2x40 2.54 mm THT", ds=DS + "connectors/Samtec-TSW.pdf",
                  stock=("Connector_Generic", "Conn_02x40_Odd_Even"), desc="Board-to-board header 2x40, SYS contract"),
    # cTI-20 pinout: TI SPRU655I table 13 (docs/datasheets/controllers/SPRU655.pdf); FTSH "-K" = keying shroud
    "J_JTAG": dict(mfr=SAMTEC, mpn="FTSH-110-01-L-DV-K", prefix="J", pkg="2x10 1.27 mm SMT keyed",
                   ds=DS + "connectors/Samtec-FTSH.pdf", stock=("Connector_Generic", "Conn_02x10_Odd_Even"),
                   desc="cTI-20 JTAG header 2x10 1.27 mm, keying shroud"),
    "J_UART": dict(mfr=SAMTEC, mpn="TSW-103-07-G-S", prefix="J", pkg="1x3 2.54 mm THT", ds=DS + "connectors/Samtec-TSW.pdf",
                   stock=("Connector_Generic", "Conn_01x03"), desc="Debug UART header 1x3, 3.3 V TTL"),
    "J_SDFM": dict(mfr=SAMTEC, mpn="TSW-110-07-G-D", prefix="J", pkg="2x10 2.54 mm THT", ds=DS + "connectors/Samtec-TSW.pdf",
                   stock=("Connector_Generic", "Conn_02x10_Odd_Even"), desc="SDFM expansion header 2x10 (8 modulators)"),
}


# ======================================================================== 3. pin plan (one table drives symbol + CSV)
# Input X-BAR destinations, SPRSP14E table 6-8 (p.85): only these routes are claimed in the plan.
XBAR = {1: "TZ1 ePWM-XBAR eCAP", 2: "TZ2 ePWM-XBAR eCAP", 3: "TZ3 ePWM-XBAR eCAP", 4: "XINT1 ePWM-XBAR eCAP",
        5: "XINT2 ePWM-XBAR eCAP", 6: "TRIP6 XINT3 ePWM-XBAR eCAP", 13: "XINT4 ePWM-XBAR eCAP",
        14: "XINT5 ePWM-XBAR eCAP", 15: "eCAP", 16: "eCAP"}
XBAR.update({k: "ePWM-XBAR eCAP" for k in range(7, 13)})
ADC = {}   # net -> (pin, function); filled by plan()


def plan():
    """[(net, pin, function, direction, route)]. function = mux signal name, 'GPIO', or ADC/CMPSS names joined by '+'."""
    P = []

    def add(net, pin, fn="GPIO", d="in", route=""):
        P.append((net, pin, fn, d, route))
    for n in range(1, 5):                    # 32 ePWM outputs on the ZWT-only balls GPIO137-168 (mux 1)
        for k in range(1, 9):
            m, b = 4 * (n - 1) + (k + 1) // 2, (k - 1) % 2
            g = (145 + 2 * (m - 1) if m <= 12 else 137 + 2 * (m - 13)) + b
            add("C%d_PWM%d" % (n, k), "GPIO%d" % g, "EPWM%d%s" % (m, "AB"[b]), "out",
                "HRPWM (ePWM1-8): the DAB phase shift at 100 kHz must use C1 and C2, one bridge per port"
                if m <= 8 else "no HRPWM (ePWM9-16)")
    add("I2C_SDA", "GPIO0", "I2CA_SDA", "io", "or CM-I2CA_SDA (mux 9)")
    add("I2C_SCL", "GPIO1", "I2CA_SCL", "io", "or CM-I2CA_SCL (mux 9)")
    add("FLT_LATCH_N", "GPIO2", route="INPUTXBAR1>TZ1 (TZn active low: TRM 26.9.2) one-shot trip of all ePWM")
    for n in range(1, 5):                    # TRM table 17-3: INPUTXBAR2..5 = ePWM X-BAR mux G3/G5/G7/G9 option 1
        add("C%d_FAULT" % n, "GPIO%d" % (2 + n), route="INPUTXBAR%d>ePWM-XBAR mux G%d.1 > TRIP%d > DCAH, TZDCSEL "
            "event on high (TRM 26.11.4; or TRIPOUTINV) - fault is active high" % (1 + n, 2 * n + 1, (4, 5, 7, 8)[n - 1]))
    add("ESTOP_N", "GPIO7", route="INPUTXBAR6>TRIP6 > DC event on low (TRM table 17-1)")
    add("GATE_EN", "GPIO8", route="monitor: channel A; firmware trips on a lasting GATE_EN/CHB_OK disagreement")
    add("CHB_OK", "GPIO22", route="monitor: channel B; firmware trips on a lasting GATE_EN/CHB_OK disagreement")
    for n in range(1, 5):
        add("C%d_EN_MCU" % n, "GPIO%d" % (8 + n), d="out", route="AND GATE_EN AND CHB_OK (74LVC11A) > EN pair; "
            "AND CHB_OK AND C%d_RDY (74LVC11A) = C%d_DRV_EN, enables the PWM and EN line drivers (low = pairs high "
            "impedance)" % (n, n))
        add("C%d_EN_RB" % n, "GPIO%d" % (99 + n), route="EN pair readback (THVD1450 receiver at the CTRL end). "
            "Start-up test with GATE_EN low (latch not yet cleared), CHB_OK, C%d_EN_MCU and RDY high: must read 0; after "
            "FLT_CLR: 1. Commissioning proof test per board: same state, one minimum PWM pulse -> no current on "
            "C%d_AN1. With the drivers disabled it reads the power board's fail-safe bias (0) or an idle line (1)" % (n, n))
    add("FLT_CLR", "GPIO13", d="out")
    add("WDI", "GPIO14", d="out")
    add("PG_24V", "GPIO15")
    add("SPI_SIMO", "GPIO16", "SPIA_SIMO", "out", "or SSIA_TX (mux 11)")
    add("SPI_SOMI", "GPIO17", "SPIA_SOMI", "in", "or SSIA_RX (mux 11)")
    add("SPI_CLK", "GPIO18", "SPIA_CLK", "out", "or SSIA_CLK (mux 11)")
    add("SPI_CS_ADC_N", "GPIO19", "SPIA_STEn", "out", "or SSIA_FSS (mux 11)")
    add("SPI_CS_IO_N", "GPIO20", d="out")
    add("PG_HVAUX", "GPIO21")
    for i in range(4):
        add("FAN_PWM%d" % (i + 1), "GPIO%d" % (24 + i), "OUTPUTXBAR%d" % (i + 1), "out",
            "eCAP%d APWM > Output X-BAR mux G%d.3 ECAP%d_OUT (TRM 24.4, table 17-5)" % (i + 1, 2 * i, i + 1))
    add("SCIA_RX", "GPIO28", "SCIA_RX", "in", "default SCI-boot pins")
    add("SCIA_TX", "GPIO29", "SCIA_TX", "out", "default SCI-boot pins")
    add("SCIC_DE", "GPIO30", d="out")
    add("LED_CPU1_N", "GPIO31", d="out")
    for n in range(1, 5):
        add("C%d_RDY_F" % n, "GPIO%d" % (31 + n))
    add("CANA_RX", "GPIO36", "CANA_RX", "in", "default CAN-boot pins")
    add("CANA_TX", "GPIO37", "CANA_TX", "out", "default CAN-boot pins")
    add("CANB_TX", "GPIO38", "CANB_TX", "out")
    add("CANB_RX", "GPIO39", "CANB_RX", "in")
    add("SCIA_DE", "GPIO40", d="out")
    add("SCIB_DE", "GPIO41", d="out")
    add("DBG_TX", "GPIO42", "UARTA_TX", "out", "CM-UART-A; or SCIA_TX (mux 15)")
    add("DBG_RX", "GPIO43", "UARTA_RX", "in", "CM-UART-A; or SCIA_RX (mux 15)")
    add("LED_CPU2_N", "GPIO44", d="out")
    add("LED_CM_N", "GPIO45", d="out")
    add("LED_FLT_N", "GPIO46", d="out")
    add("EEPROM_WP", "GPIO47", d="out")
    for f in range(2):                       # SDFM1/2 data on GPIO48-63 (mux 7); all clock inputs share the return clock
        for c in range(4):
            add("SD%d_D%d" % (f + 1, c + 1), "GPIO%d" % (48 + 8 * f + 2 * c), "SD%d_D%d" % (f + 1, c + 1))
            add("SD_CLK_RET", "GPIO%d" % (49 + 8 * f + 2 * c), "SD%d_C%d" % (f + 1, c + 1))
    for i in range(8):
        add("DI%d" % (i + 1), "GPIO%d" % (64 + i))
        add("DO%d" % (i + 1), "GPIO%d" % (76 + i), d="out")
    add("BOOT1", "GPIO72", route="boot-mode strap (pin 1), pull-up = flash")
    add("SD_XCLK", "GPIO73", "XCLKOUT", "out", "SDFM modulator clock: CLKSRCCTL3.XCLKOUTSEL = XTAL (25 MHz), "
        "XCLKOUTDIVSEL /2 = 12.5 MHz (TRM 3.7.4; AMC1306-class 5-20 MHz)")
    add("MCAN_TX", "GPIO74", "MCAN_TX", "out")
    add("MCAN_RX", "GPIO75", "MCAN_RX", "in")
    add("BOOT0", "GPIO84", route="boot-mode strap (pin 0), pull-up = flash")
    add("SCIB_TX", "GPIO86", "SCIB_TX", "out")
    add("SCIB_RX", "GPIO87", "SCIB_RX", "in")
    add("SCIC_TX", "GPIO89", "SCIC_TX", "out")
    add("SCIC_RX", "GPIO90", "SCIC_RX", "in")
    for i in range(3):
        add("MUX_A%d" % i, "GPIO%d" % (91 + i), d="out", route="TMUX1208 address")
    add("FAN_TACH4", "GPIO96", "EQEP1_A", "in", "eQEP1 QDECCTL.QSRC=2 up-count frequency mode + unit timer, "
        "PCRM=3, synchronous GPIO qualification (TRM 27.4.1.3, 27.8); never mux EQEP1_B (GPIO97 stays GPIO)")
    for i in range(3):
        add("FAN_TACH%d" % (i + 1), "GPIO%d" % (97 + i), route="INPUTXBAR%d>eCAP%d capture, ECCTL0.INPUTSEL=%d "
            "(TRM table 24-1)" % (13 + i, 5 + i, 12 + i))
    for g, fn, net, d in ((105, "ENET_MDIO_CLK", "ETH_MDC", "out"), (106, "ENET_MDIO_DATA", "ETH_MDIO", "io"),
                          (108, "ENET_MII_INTR", "ETH_INT_N", "in"), (109, "ENET_MII_CRS", "ETH_CRS", "in"),
                          (110, "ENET_MII_COL", "ETH_COL", "in"), (111, "ENET_MII_RX_CLK", "ETH_RX_CLK", "in"),
                          (112, "ENET_MII_RX_DV", "ETH_RX_DV", "in"), (113, "ENET_MII_RX_ERR", "ETH_RX_ER", "in"),
                          (114, "ENET_MII_RX_DATA0", "ETH_RXD0", "in"), (115, "ENET_MII_RX_DATA1", "ETH_RXD1", "in"),
                          (116, "ENET_MII_RX_DATA2", "ETH_RXD2", "in"), (117, "ENET_MII_RX_DATA3", "ETH_RXD3", "in"),
                          (118, "ENET_MII_TX_EN", "ETH_TX_EN", "out"), (120, "ENET_MII_TX_CLK", "ETH_TX_CLK", "in"),
                          (121, "ENET_MII_TX_DATA0", "ETH_TXD0", "out"), (122, "ENET_MII_TX_DATA1", "ETH_TXD1", "out"),
                          (123, "ENET_MII_TX_DATA2", "ETH_TXD2", "out"), (124, "ENET_MII_TX_DATA3", "ETH_TXD3", "out")):
        add(net, "GPIO%d" % g, fn, d, "CM EMAC MII")
    add("ETH_RST_N", "GPIO119", d="out", route="PHY reset (pulled low until firmware releases it)")
    # ---- analog: cell AN1 and port IA/IB/VA/VB on the eight CMPSS positive inputs; VDAC = the 2.5 V reference.
    # Trip routes (TRM table 17-3, ePWM X-BAR mux.option): one TRIP per cell (its FAULT + its AN1 window) and TRIP9
    # for every port-level event; each ePWM ORs its own TRIP with TRIP9 in the DC combinational trip select.
    # Every threshold is set after the firmware start-up null at zero input (gates off, contactors open).
    null = "; start-up null at zero input, then threshold = null +/- d"
    cal = ("; 50 k internal pull-down on this ball (SPRSP14E table 6-1): -0.094 % gain (47 R / 50 k) - calibrate "
           "the gain and store it in the EEPROM (CSR-08)")
    ana = [("C1_AN1", "ADCINA2", "CMPIN1P", "CMPSS1 window > G0.1 > TRIP4 (with C1_FAULT)" + null),
           ("C1_AN2", "ADCINB3", "", ""), ("C1_AN3", "ADCINC3", "", ""),
           ("C2_AN1", "ADCINB2", "CMPIN3P", "CMPSS3 window > G4.1 > TRIP5 (with C2_FAULT)" + null),
           ("C2_AN2", "ADCINC5", "", ""), ("C2_AN3", "ADCIND1", "", ""),
           ("C3_AN1", "ADCINC2", "CMPIN6P", "CMPSS6 window > G10.1 > TRIP7 (with C3_FAULT)" + null),
           ("C3_AN2", "ADCIND3", "", ""), ("C3_AN3", "ADCINA3", "", ""),
           ("C4_AN1", "ADCIND0", "CMPIN7P", "CMPSS7 window > G12.1 > TRIP8 (with C4_FAULT)" + null),
           ("C4_AN2", "ADCINA5", "", ""), ("C4_AN3", "ADCINB4", "", ""),
           ("P_IA", "ADCINA4", "CMPIN2P", "SC: CMPSS2 window > G2.1 > TRIP9 (all cells); OC + open conductor: "
            "ADC-A PPB1 limits > ADCAEVT1 > G0.2 > TRIP9 (all cells)" + null),
           ("P_IB", "ADCINC4", "CMPIN5P", "SC: CMPSS5 window > G8.1 > TRIP9 (all cells); OC + open conductor: "
            "ADC-C PPB2 limits > ADCCEVT2 > G3.3 > TRIP9 (all cells)" + null),
           ("P_VA", "ADCIND2", "CMPIN8P", "OV primary + open: ADC-D PPB1 (VA converted every <= 10 us) > ADCDEVT1 > "
            "G9.3 > TRIP9 (all cells); OV backup (high) and open (low): CMPSS8 > G14.1 > TRIP9 (all cells)" + null),
           ("P_VB", "ADCIN14", "CMPIN4P", "OV primary + open: ADC-C PPB1 (ADCIN14 converted by ADC-C every <= 10 us) "
            "> ADCCEVT1 > G1.3 > TRIP9 (all cells); OV backup (high) and open (low): CMPSS4 > G6.1 > TRIP9 "
            "(all cells)" + null),
           ("P_VAX", "ADCINA0", "", "monitor only" + cal),
           ("P_VBX", "ADCIN15", "", "monitor only (moved off ADCINB0 = VDAC, INT-05)"),
           ("P_AUX1", "ADCINA1", "", "monitor only" + cal), ("P_AUX2", "ADCINB1", "", "monitor only" + cal),
           ("MUX_OUT", "ADCIND4", "", ""),
           ("P_ID", "ADCIND5", "", "supervised continuously: ADC-D PPB2 window over the fitted code's bands > "
            "ADCDEVT2 > G11.3 > TRIP9 (all cells); firmware decodes the readback state (table in trip_band)"),
           ("V24_MON", "ADCINB5", "", "")]
    for net, pin, cmp, route in ana:
        add(net, pin, pin + ("+" + cmp if cmp else ""), "ain", route)
    add("VREF2V5", "ADCINB0", "VDAC", "ain", "CMPSS DAC reference = REF5025E 2.5 V (COMPDACCTL.SELREF = VDAC): "
        "ratiometric with the ADC; 1 uF at the ball (table 6-1); 8 CMPSS x 6 k load = 3.3 mA")
    return P


def check_plan(P, pins):
    """Every claimed function must exist on that ball in Table 6-1; every X-BAR route in Table 6-8; no pin used twice."""
    by = {r["name"]: r for r in pins}
    errs, seen, nets = [], {}, {}
    for net, pin, fn, d, route in P:
        r = by.get(pin)
        if r is None:
            errs.append("%s: no ball named %s" % (net, pin))
            continue
        if pin in seen:
            errs.append("%s used twice (%s, %s)" % (pin, seen[pin], net))
        seen[pin] = net
        if net in nets and net != "SD_CLK_RET":
            errs.append("net %s on two pins" % net)
        nets[net] = pin
        mux = dict((s, p) for p, s in (m.split("=") for m in r["mux"].split(";") if m))
        names = [pin] + [s for s, p in mux.items() if p == "ALT"]
        for f in fn.split("+"):
            ok = (f == "GPIO" and pin.startswith("GPIO")) or f in names or (f in mux and pin.startswith("GPIO"))
            if not ok:
                errs.append("%s: %s does not offer %s" % (net, pin, f))
        m = re.match(r"INPUTXBAR(\d+)>(\S+)", route)
        if m:
            dest = re.sub(r"\d+$", "", m.group(2)) if m.group(2).startswith("eCAP") else m.group(2)   # any eCAP
            if dest not in XBAR[int(m.group(1))].split():
                errs.append("%s: INPUTXBAR%s cannot reach %s" % (net, m.group(1), m.group(2)))
    xb = [int(x) for x in re.findall(r"INPUTXBAR(\d+)", " ".join(p[4] for p in P))]
    if len(xb) != len(set(xb)):
        errs.append("an Input X-BAR channel is used twice")
    if errs:
        raise SystemExit("PIN PLAN FAILED:\n  " + "\n  ".join(errs))
    return {pin: net for net, pin, fn, d, route in P}


def write_plan(P, pins, B, trips):
    """CSV for firmware: signal, ball, pin, mux position, function, direction, route, worst-case hardware-trip bands
    (from check_trips), what it reaches on the board."""
    by = {r["name"]: r for r in pins}
    conn = {}                                    # net -> ["J701.5", ...] for connector pins
    on_board = {}                                # net -> other part pins
    for ref, p in B.D.parts.items():
        for num, net in p.pins.items():
            if net:
                (conn if ref.startswith("J") else on_board).setdefault(net, []).append("%s.%s" % (ref, num))
    far = {"FAULT": ("FLT_P", "FLT_N"), "EN_MCU": ("EN_P", "EN_N"), "EN_RB": ("EN_P", "EN_N"), "RDY_F": ("RDY",)}

    def reaches(net):
        cands = [net, net + "_P", net + "_N", net + "_H", "S_" + net]
        for k, v in far.items():
            if net.endswith(k):
                cands += [net[:-len(k)] + x for x in v]
        if net == "MUX_OUT":
            cands += ["C%d_%s" % (n, x) for n in range(1, 5) for x in ("NTC_P", "ID")]
        hits = sorted({h for c in cands for h in conn.get(c, [])}, key=L.natural)
        if hits:
            return " ".join(hits)
        mcu = [r for r in B.D.parts if B.D.parts[r].lib_id.endswith(":F28388D")][0]
        return "on-board " + " ".join(sorted(h for h in on_board.get(net, []) if not h.startswith(mcu + ".")))
    with open(PLAN_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal", "ball", "pin", "mux_pos", "function", "direction", "route", "trip_band", "reaches"])
        for net, pin, fn, d, route in sorted(P, key=lambda p: L.natural(p[1])):
            r = by[pin]
            mux = dict((s, p) for p, s in (m.split("=") for m in r["mux"].split(";") if m))
            pos = "0" if fn == "GPIO" else mux.get(fn.split("+")[0], "analog")
            w.writerow([net, r["ball"], pin, pos, fn, d, route, trips.get(net, ""), reaches(net)])


# ======================================================================== 4. schematic
RAILS = ["+24V", "+24V_GD", "+5V", "+3V3", "+1V2", "+3V3A", "VREF2V5"]
PREC = "10ppm/K"                 # every 0.1 % resistor (AFE, VDD and supervisor dividers): tolerance + 80 K drift budgeted
P01 = 0.001 + 10e-6 * 80         # worst-case deviation of one such resistor


def val(s):
    """'4.02k' -> 4020.0, '680n' -> 6.8e-07, '732R' -> 732.0, '22u 10V' -> 2.2e-05"""
    m = re.match(r"([\d.]+)([pnumkMR]?)", s)
    return float(m.group(1)) * {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "R": 1, "": 1}[m.group(2)]


# Difference-amplifier gain per connector (Rf, Cf for an anti-alias pole near 490 kHz). Cell pairs are dual-range.
AFE_CELL = ("4.02k", "82p")      # G 0.402: +/-2 V -> 0.45-2.05 V, or 0-3 V single-ended -> 1.25-2.46 V
AFE_PORT = ("5.76k", "56p")      # G 0.576: +/-2 V -> 0.10-2.40 V (port board: isolated amplifiers only)
# INT-12: the cell AN1 (inductor current) transfer is defined here only; AFE, OC budget, notes and pin plan use it.
# PVCELL-25 rev B (gen/pvcell.py): LEM LA 150-P, KN 1:2000 (LA_150-P.pdf p1) into RM 20.0 R, driven as a +1 / -1 pair
# of 10.0k stages around VMID: AN1_P - AN1_N = 2 x RM / KN per primary ampere, common mode 1.25 V, source < 1 R.
CELL_AN1_V_PER_A = 2 * 20.0 / 2000  # 20.0 mV/A at the CELL pins
CELL_AN1_CM = (2.5 * 0.9925 / 2 * 0.999, 2.5 * 1.0075 / 2 * 1.001)   # PVCELL VMID: 2.5 V reference 0.75 % / 2, 0.1 %
CELL_AN1_AFE = AFE_CELL            # CTRL gain 0.402 on AN1 -> 8.04 mV/A at the ADC, 0.0759 A/LSB at 12 bit
CELL_OC_TRIP_A = 91.5              # CMPSS window on AN1: backup above the PVCELL local window (INT-05, rev D)
BIAS = "1.50k"                     # INT-07 open-input bias: AN_P to AGND, AN_N to +3V3A (8 port pairs, 4 cell AN1)
VDD_FB = ("5.23k", "10.0k")        # TPS62130 RFBT / RFBB: 0.8 V x 1.523 = 1.218 V (CSR-01)
VDD_SNS = ("732R", "499R")         # TPS3703F6050 SENSE divider on +1V2 (CSR-01)
CELL_EFUSE = ("8.06k", "680n")     # TPS26600 RILIM, CdVdT per CELL port (CSR-03)
PORT_EFUSE = ("12.1k", "100n")     # TPS26600 RILIM, CdVdT on P_24V (INT-06 / CSR-12; rev E: 0.7 A hold load)
PORT_BOARD_UF = 4.7 + 0.22 + 1.0   # PV-PORT +24V input (gen/port.py 4.7 uF + 220 nF) + this card's 1 uF on P_24V
PORT_24V_DEMAND_A = 0.7            # PV-PORT rev 4 battery port: two hold-closed coils + sensors, up to 200 s
PORT_MARGIN = 1.2                  # I(CB) min >= 1.2 x demand: the demand is a calculated bound and I(CB) min at
#   this RILIM is interpolated between the 5.36 k and 120 k table points (SLVSDG2G p7), not a datasheet figure
TPS2660_RON = 0.25                 # ohm max, -40..125 C (SLVSDG2G 7.5 p8)
CTRL_24V_LOAD_A = 0.35             # this card on +24V (LMR38020 1.2 A x 5 V / 0.9 / 21.6 V; review CSR-12)
SYS_24V_MIN_A = 1.82               # SYS-IO-AUX +24V eFuse, TPS1663 at 9.09 k: 1.82-2.14 A (review CSR-12)
SYS_GD_MIN_A = 4.69                # SYS-IO-AUX +24V_GD eFuse, TPS1663 at 3.57 k: 4.69-5.39 A (review CSR-03)
FIG55_85C = (7.0, 0.35)            # SLVSDG2G figure 55, TA 85 C: >= 0.35 s to thermal shutdown at <= 7 W (graph reading)
TPS2660_ICB = ((12 / 120 - 0.03, 0.045, 0.11), (12 / 5.36 - 0.03, 2.0, 2.4))   # I(CB) (eq. 4 typ, min, max) A, p7


def efuse(parts, c_load, v=26.4, r_load=24.0):
    """TPS26600 worst case for parts = (RILIM, CdVdT): I(CB) band (eq. 4 with RILIM 1 %, spread interpolated between
    the two table points), OUT slope band (eq. 1/2, CdVdT 10 %), ramp time to v, inrush into c_load at the fastest
    slope, and start-up dissipation (eq. 22-24, resistive load r_load = 1 A at 24 V) at the slow and fast corners."""
    (t1, n1, x1), (t2, n2, x2) = TPS2660_ICB
    r = val(parts[0]) / 1e3
    lo, hi = 12 / (r * 1.01) - 0.03, 12 / (r * 0.99) - 0.03
    icb = (n1 + (lo - t1) * (n2 - n1) / (t2 - t1), x1 + (hi - t1) * (x2 - x1) / (t2 - t1))
    c = val(parts[1])
    s = (4.0e-6 * 23.75 / (c * 1.1), 5.5e-6 * 25.5 / (c * 0.9))             # V/s, slow and fast
    pd_t = [(0.5 * v * c_load * k + v * v / (6 * r_load), v / k) for k in s]
    return dict(icb=icb, slope=s, t_ramp=(v / s[1], v / s[0]), inrush=c_load * s[1], pd=max(p for p, t in pd_t),
                pd_t=pd_t)
AFE_NOTE = ("Difference amp per pair: Vadc = VMID 1.25 V + G x (AN_P - AN_N), Rin 10.0k 0.1 %% 10 ppm/K. %s\n"
            "Cf || Rf: anti-alias pole %s. 47 R + 1 nF C0G: ADC charge bucket (ACQPS >= 320 ns at 12 bit).\n"
            "BAT54S clamps the ADC node to 3V3A/AGND. All values calculated, not simulated.")
BIAS_NOTE = ("Open-input bias (INT-07): %s AN_P -> AGND and AN_N -> +3V3A, so an open conductor or ribbon\n"
             "reads far outside the trip window instead of 0 (worst readings: check_trips build line).\n"
             "Loads the isolated amplifier with ~1.3 k (AMC3330/AMC3302 RLOAD >= 1 k); source + cable resistance\n"
             "R gives a gain error R/1.5 k (< 0.07 %% for 1 R), removed by the gain calibration.") % BIAS
OPA_CH = [("3", "2", "1"), ("5", "6", "7"), ("10", "9", "8"), ("12", "13", "14")]   # (+IN, -IN, OUT) A..D


def opamp(B, names, spare):
    """One OPA4388 on +3V3A serving the front ends `names`; an unused channel is a VMID follower."""
    pins = {"4": "+3V3A", "11": "AGND"}
    for i, ch in enumerate(OPA_CH):
        nets = (names[i] + "_NI", names[i] + "_INV", names[i] + "_OA") if i < len(names) else \
            ("VMID", "%s_%s" % (spare, "ABCD"[i]), "%s_%s" % (spare, "ABCD"[i]))
        pins.update(dict(zip(ch, nets)))
    B.part("OPA4388", pins)
    B.C("100n", "+3V3A", "AGND")


def afe(B, name, adc, gain, bias=False):
    """Front end of pair name_P/name_N into ADC net `name`; adc = pin-plan function for the title, gain = (Rf, Cf);
    bias = INT-07 open-input bias resistors at the connector side of the pair."""
    B.block("%s -> %s" % (name, adc.replace("+", " / ")))
    p, n, ni, inv, oa = name + "_P", name + "_N", name + "_NI", name + "_INV", name + "_OA"
    B.R("10.0k", p, ni, tol="0.1%", note=PREC)
    B.R(gain[0], ni, "VMID", tol="0.1%", note=PREC)
    B.R("10.0k", n, inv, tol="0.1%", note=PREC)
    B.R(gain[0], inv, oa, tol="0.1%", note=PREC)
    B.C(gain[1], inv, oa, diel="C0G", tol="5%")
    B.R("47R", oa, name)
    B.C("1n", name, "AGND", diel="C0G", tol="5%")
    B.part("BAT54S", {"1": "AGND", "2": "+3V3A", "3": name})
    if bias:
        B.R(BIAS, p, "AGND")
        B.R(BIAS, n, "+3V3A")


def ratio(rt, rb):
    """(min, max) of 1 + rt/rb for two 0.1 % 10 ppm/K resistors given as value strings"""
    rt, rb = val(rt), val(rb)
    return 1 + rt * (1 - P01) / (rb * (1 + P01)), 1 + rt * (1 + P01) / (rb * (1 - P01))


def supervisor():
    """CSR-01 rows (rail, part, reset-below min, release max, OV min, OV max, regulator min, regulator max), volts.
    MCU SPRSP14E 7.3: VDDIO/VDDA 3.14-3.47 V, VDD 1.14-1.26 V. TPS62130/3 SLVSAG7F p5: VFB 781.6-822.4 mV in PSM
    (785.6-814.4 PWM), the fixed 3.3 V part's divider included; FB leakage <= 100 nA; +/-0.1 % line/load allowance.
    TPS7A2033 SBVS338H: +/-1.5 %. TPS3703 SBVS249B 7.5: VIT +/-0.7 % (+/-1 % below 0.8 V), hysteresis <= 0.8 %
    (0.7 % below 0.8 V) of VIT, SENSE current <= 1.5 uA."""
    rows = []
    for rail, reg in (("+3V3", (3.3 * 0.7816 / 0.8 * 0.999, 3.3 * 0.8224 / 0.8 * 1.001)),
                      ("+3V3A", (3.3 * 0.985, 3.3 * 1.015))):
        uv, ov = 3.3 * 0.96, 3.3 * 1.04                              # TPS3703A4330, SENSE on the rail
        rows.append((rail, "TPS3703A4330", uv * 0.993, uv * 1.007 * 1.008, ov * 0.993, ov * 1.007) + reg)
    kr, ks, leak = ratio(*VDD_FB), ratio(*VDD_SNS), 100e-9 * val(VDD_FB[0])
    reg = (0.7816 * kr[0] * 0.999 - leak, 0.8224 * kr[1] * 1.001 + leak)
    uv = 0.50 * 0.94                                                 # TPS3703F6050: UV only, 0.470 V
    rows.append(("+1V2", "TPS3703F6050", uv * 0.99 * ks[0], uv * 1.01 * 1.007 * ks[1] + 1.5e-6 * val(VDD_SNS[0]),
                 None, None) + reg)
    return rows


def sheet_power(B):
    B.new_sheet("01_power", "Power regulators",
                "+24V -> LMR38020 5 V -> TPS62133 3.3 V; PG chain enables TPS62130 1.2 V\n"
                "and TPS7A2033 3.3 V analog (VDDIO first, then VDD and VDDA)")
    B.block("24 V -> 5 V (LMR38020, 400 kHz)",
            "Table 9-1 row 400 kHz / 5 V / 2 A: L 15 uH, 3x22 uF, RFBT 100k / RFBB 24.9k -> 5.02 V, RT 64.9k.\n"
            "Load (calculated): 3.3 V 1.3 A + 1.2 V 0.48 A + 3.3 V analog 0.1 A -> about 1.2 A at 5 V.\n"
            "EN tied to VIN (datasheet allows). PG_5V enables the 3.3 V converter.\n"
            "Output capacitance check (SNVSC40E, calculated): total on +5V = 3x22 + 2x10 + 1 + 0.3 = 87 uF nominal,\n"
            "96 uF at +10 %: 1.45x the 66 uF design value, inside the 9.2.2.5 limit (10x design or 1000 uF) -> no extra\n"
            "stability study required. Start-up: tSS 4.0 ms (8.3.9) -> 96 uF x 5.02 V / 4 ms = 0.12 A charge current,\n"
            "downstream loads held off by the PG chain, << ILS-LIMIT 1.8 A min; OCP blanking 18 ms -> no hiccup.\n"
            "Inductor: LMIN = 0.25 x 5 V / 400 kHz = 3.1 uH (eq. 11) < 15 uH; ripple 0.64-0.68 A at 21.6-26.4 V in\n"
            "(> 10 % of 2 A); peak 1.54 A (1.62 A at L -20 %) < IHS-LIMIT 2.6 A min < ISAT 5.55 A. Bode not measured.")
    B.part("LMR38020", {"3": "+24V", "2": "+24V", "4": "RT_5V", "8": "SW_5V", "7": "BOOT_5V", "5": "FB_5V",
                        "6": "PG_5V", "1": "GND", "9": "GND"})
    B.C("4.7u", "+24V", "GND", pkg="1210", volt="50V")
    B.C("4.7u", "+24V", "GND", pkg="1210", volt="50V")
    B.C("100n", "+24V", "GND", volt="100V")
    B.R("64.9k", "RT_5V", "GND")
    B.C("100n", "BOOT_5V", "SW_5V")
    B.part("L15U", {"1": "SW_5V", "2": "+5V"})
    for _ in range(3):
        B.C("22u", "+5V", "GND", pkg="1206", volt="16V", tol="10%")
    B.R("100k", "+5V", "FB_5V")
    B.R("24.9k", "FB_5V", "GND")
    B.R("100k", "+5V", "PG_5V")
    B.flag("+5V")
    B.TP("+5V")
    s33, s12 = supervisor()[0], supervisor()[2]
    for title, key, out, en, pg, extra in (
            ("5 V -> 3.3 V digital (TPS62133, 3 A)", "TPS62133", "+3V3", "PG_5V", "PG_3V3", None),
            ("5 V -> 1.2 V core (TPS62130, 3 A)", "TPS62130", "+1V2", "PG_3V3", None, VDD_FB)):
        sw, ss = "SW_" + out[1:], "SS_" + out[1:]
        fb = "FB_" + out[1:] if extra else "GND"
        B.block(title, ("Fixed 3.3 V; FB to AGND per datasheet. FSW low (2.5 MHz), 2.2 uH, 2x22 uF (table 9-1).\n"
                        "SS 3.3 nF (typical application). PG_3V3 releases the 1.2 V and 3.3 V analog rails.\n"
                        "Worst case %.3f-%.3f V (power-save mode, SLVSAG7F p5) inside VDDIO 3.14-3.47 V." % s33[6:]
                        if not extra else
                        "VOUT = 0.8 V x (1 + 5.23k/10.0k) = 1.218 V (eq. 6), 0.1 %% 10 ppm/K: worst case %.4f-%.4f V\n"
                        "(power-save VFB 781.6-822.4 mV, FB leakage, 0.1 %% line/load) inside VDD 1.14-1.26 V (CSR-01).\n"
                        "Enabled by PG_3V3: VDD rises after VDDIO (SPRSP14E table 7-1). VDD bulk on the decoupling sheet."
                        % s12[6:]))
        B.part(key, {"11": "+5V", "12": "+5V", "10": "+5V", "13": en, "9": ss, "7": "GND", "8": "GND",
                     "1": sw, "2": sw, "3": sw, "14": out, "5": fb, "4": pg, "6": "GND", "15": "GND", "16": "GND",
                     "17": "GND"})
        B.C("10u", "+5V", "GND", pkg="1210", volt="25V")
        B.C("100n", "+5V", "GND")
        B.C("3.3n", ss, "GND", tol="10%")
        B.part("L2U2", {"1": sw, "2": out})
        B.C("22u", out, "GND", pkg="0805", volt="10V", tol="10%")
        B.C("22u", out, "GND", pkg="0805", volt="10V", tol="10%")
        if extra:
            B.R(extra[0], out, fb, tol="0.1%", note=PREC)
            B.R(extra[1], fb, "GND", tol="0.1%", note=PREC)
        else:
            B.R("100k", "+3V3", "PG_3V3")
        B.flag(out)
        B.TP(out)
    B.block("5 V -> 3.3 V analog (TPS7A2033)",
            "VDDA, op amps, reference, mux. Enabled by PG_3V3 so VDDA never leads VDDIO.\n"
            "Load (calculated): 26 op-amp channels x 2.6 mA max + IDDA 15 mA + REF and its 4.2 mA load + VBIAS\n"
            "+ 12 open-input bias resistors to +3V3A (16 mA) = about 110 mA of 300 mA.")
    B.part("TPS7A2033", {"1": "+5V", "3": "PG_3V3", "2": "GND", "5": "+3V3A", "4": None})
    B.C("1u", "+5V", "GND", volt="16V")
    B.C("2.2u", "+3V3A", "AGND", volt="16V")
    B.TP("+3V3A")


def sheet_reset(B, mcu):
    B.new_sheet("02_reset", "Supervisors, reset, clock, boot",
                "TPS3703 windows on 3.3 V and 3.3 V analog, UV supervisor on 1.2 V -> XRSn;\n"
                "25 MHz CMOS clock into X1; boot straps GPIO72/GPIO84 (flash by default)")
    rows = ["%-5s %s: trips >= %.4f, releases <= %.4f < regulator min %.4f%s" % (
        r[0], r[1][7:], r[2], r[3], r[6], "; OV %.3f-%.3f > regulator max %.4f V" % (r[4], r[5], r[7]) if r[4] else
        "; regulator max %.4f V" % r[7]) for r in supervisor()]
    B.block("Supply supervisors (open-drain resets wired to XRSn), CSR-01",
            "SPRSP14E 7.10.1.2.4: VDDIO and VDD need an external supervisor (the internal POR trips below range).\n"
            "Worst case at every corner (asserted on each build, MCU window 3.14-3.47 V / 1.14-1.26 V):\n  " +
            "\n  ".join(rows) + "\n"
            "1.2 V is UV-only: a +/-4 % window cannot hold the TPS62130 power-save spread; its maximum is the regulator\n"
            "arithmetic above. Delay: CT open 10 ms (A parts), CT to VDD via 10k 20 ms (F part). VDD = +5V so the\n"
            "outputs stay defined while 3.3 V collapses. XRSn pull-up 10k, 10 nF; SYS_RST_N and the JTAG RESET are\n"
            "open drain on the same node. Reset button on MR of the 3.3 V part (100k internal pull-up).")
    for rail, mr in (("+3V3", "MR_N"), ("+3V3A", None)):
        B.part("TPS3703A4330", {"1": rail, "6": mr, "3": None, "4": "XRSn", "2": "+5V", "5": "GND"})
        B.C("100n", "+5V", "GND")
    B.part("TPS3703F6050", {"1": "SNS_1V2", "6": None, "3": "CT_1V2", "4": "XRSn", "2": "+5V", "5": "GND"})
    B.C("100n", "+5V", "GND")
    B.R("10k", "+5V", "CT_1V2")
    B.R(VDD_SNS[0], "+1V2", "SNS_1V2", tol="0.1%", note=PREC)
    B.R(VDD_SNS[1], "SNS_1V2", "GND", tol="0.1%", note=PREC)
    B.C("10n", "SNS_1V2", "GND")
    B.part("SW_RST", {"1": "MR_N", "2": "GND"})
    B.R("10k", "+3V3", "XRSn")
    B.C("10n", "XRSn", "GND")
    B.TP("XRSn")
    B.block("25 MHz clock (X1)",
            "SPRSP14E 7.10.3.2.1: X1 from an oscillator 10-25 MHz, tr/tf <= 6 ns, 45-55 %; X2 open (figure 7-11).\n"
            "No crystal, so VSSOSC goes to board ground (table 6-12). 33 R source termination.")
    B.part("OSC25", {"4": "+3V3", "1": "+3V3", "2": "GND", "3": "X1_OSC"})
    B.C("10n", "+3V3", "GND")
    B.R("33R", "X1_OSC", "MCU_X1")
    B.block("Boot mode (GPIO72 = pin 1, GPIO84 = pin 0)",
            "Table 8-18: 11 = flash (straps open), 01 = SCI, 10 = CAN, 00 = parallel I/O.\n"
            "Close JP to pull a pin low. th(boot-mode) 1.5 ms; nothing else drives these balls.")
    for net in ("BOOT1", "BOOT0"):
        B.R("10k", "+3V3", net)
        B.part("JP_OPEN", {"1": net, "2": "GND"})


def sheet_mcu(B, mcu):
    B.new_sheet("03_mcu", "TMS320F28388D (ZWT 337-ball)",
                "All 337 balls, pin table extracted from SPRSP14E (gen/data/f28388d_pins.csv);\n"
                "net per ball from the pin plan (gen/data/ctrl_c2000_pin_plan.csv)")
    B.block("U301 TMS320F28388DZWTS",
            "ZWT, not PTP-176: all 32 ePWM outputs sit on ZWT-only balls GPIO137-168 (PTP has them only on GPIO0-31,\n"
            "colliding with SPI/SDFM/CAN/eQEP), and ZWT bonds all 24 ADC inputs (PTP lacks B4, B5, C5, D5).\n"
            "HRPWM exists on ePWM1-8 only = ports C1 and C2. Unused GPIOs: no-connect (table 6-12, firmware pull-up).\n"
            "NC2 (J18) tied to VDDIO as the datasheet recommends; FLT1/FLT2 must stay open.")
    B.part("F28388D", mcu)


def sheet_decoupling(B):
    B.new_sheet("04_decoupling", "MCU supply decoupling",
                "SPRSP14E 7.10.1.5: 0.1 uF per VDDIO/VDD3VFL/VDDOSC ball, 2.2 uF per VDDA ball,\n"
                "~22 uF total on VDD + one 56 R load, 22 uF per VREFHI (16-bit mode)")
    B.block("VDDIO (28 balls) + VDD3VFL (2) + VDDOSC (2): 0.1 uF each", "Configuration 1 of 7.10.1.3.2.")
    for _ in range(32):
        B.C("100n", "+3V3", "GND", pkg="0402", volt="16V")
    B.block("VDD 1.2 V (14 balls)", "14 x 1 uF + 10 uF = 24 uF, 10 % parts: >= 21.6 uF (CVDD TOTAL 20 min / 22 typ, max tolerance 20 %).\n"
                                    "56 R load consumes the VDD3VFL-to-VDD leakage current (table 6-4 note).")
    for _ in range(14):
        B.C("1u", "+1V2", "GND", pkg="0402", volt="6.3V", tol="10%")
    B.C("10u", "+1V2", "GND", pkg="0805", volt="6.3V", tol="10%")
    B.R("56R", "+1V2", "GND", pkg="0805")
    B.block("VDDA, VREFHI and VDAC", "VDDA 2.2 uF per ball to VSSA. VREFHIA-D: 22 uF each to VREFLO (16-bit mode).\n"
                                     "VDAC (ADCINB0, V2) = CMPSS DAC reference: >= 1 uF at the ball (table 6-1).\n"
                                     "All on VREF2V5: 4 x 22 + 1 + 1 uF = 90 uF, 99 uF at +10 %: inside the REF5025E\n"
                                     "1-100 uF range (summed from the design on every build).")
    B.C("2.2u", "+3V3A", "AGND", volt="16V", tol="10%")
    B.C("2.2u", "+3V3A", "AGND", volt="16V", tol="10%")
    for _ in range(4):
        B.C("22u", "VREF2V5", "AGND", pkg="1206", volt="10V", tol="10%")
    B.C("1u", "VREF2V5", "AGND", volt="16V", tol="10%")


def sheet_adcref(B):
    B.new_sheet("05_adcref", "ADC reference, VMID, slow mux",
                "REF5025E 2.5 V for VREFHIA-D; OPA2388 buffers VMID 1.25 V (diff-amp offset)\n"
                "and VBIAS 2.5 V (ID/NTC bias); TMUX1208 for 4 NTC + 4 ID; 24 V monitor; AGND star")
    B.block("2.5 V ADC and CMPSS DAC reference",
            "Powered from +3V3A (VIN >= VOUT + 0.2 V) so VREFHI and VDAC can never exceed VDDA (SPRSP14E 7.11.2.3.5,\n"
            "7.11.3.1.4). Also the CMPSS DAC reference (VDAC, INT-05): thresholds ratiometric with the ADC.\n"
            "Load 4.2 mA of +/-10 mA: VDAC 8 x 6 k (3.3 mA) + VREFHI 4 x 190 uA + VMID divider (asserted).\n"
            "NR 0.1 uF class 1; 1 uF HF at VOUT; VREFHI and VDAC capacitors are on the decoupling sheet.")
    B.part("REF5025E", {"2": "+3V3A", "1": "+3V3A", "4": "AGND", "6": "VREF2V5", "5": "REF_NR", "3": None, "7": None,
                        "8": None})
    B.C("1u", "+3V3A", "AGND", volt="16V")
    B.C("100n", "REF_NR", "AGND", pkg="0805", diel="C0G", tol="5%")
    B.C("1u", "VREF2V5", "AGND", volt="16V", tol="10%")
    B.TP("VREF2V5")
    B.block("VMID and VBIAS buffers",
            "VMID = VREF/2 is ratiometric: a zero differential input reads mid-scale whatever the reference error.\n"
            "VBIAS = VREF buffered, so ID and NTC readings are ratiometric and the reference itself is not loaded.")
    B.R("10.0k", "VREF2V5", "VMID_DIV", tol="0.1%")
    B.R("10.0k", "VMID_DIV", "AGND", tol="0.1%")
    B.C("100n", "VMID_DIV", "AGND")
    B.part("OPA2388", {"3": "VMID_DIV", "2": "VMID", "1": "VMID", "5": "VREF2V5", "6": "VBIAS", "7": "VBIAS",
                       "8": "+3V3A", "4": "AGND"})
    B.C("100n", "+3V3A", "AGND")
    B.TP("VMID")
    B.block("Slow-signal mux -> ADCIND4",
            "S1-S4 = C1-C4 NTC, S5-S8 = C1-C4 ID (24 ADC balls cannot take 20 fast channels + 9 slow ones).\n"
            "Each input has its own 100 nF on the cell sheet; the mux only switches between charged caps.")
    B.part("TMUX1208", {"13": "+3V3A", "2": "+3V3A", "1": "MUX_A0", "16": "MUX_A1", "15": "MUX_A2", "3": None,
                        "14": "AGND", "4": "C1_NTC_P", "5": "C2_NTC_P", "6": "C3_NTC_P", "7": "C4_NTC_P",
                        "12": "C1_ID", "11": "C2_ID", "10": "C3_ID", "9": "C4_ID", "8": "MUX_OUT"})
    B.C("100n", "+3V3A", "AGND")
    B.block("24 V monitor -> ADCINB5", "120k/10k: 24 V reads 1.85 V, full scale 32.5 V; 100 nF filter.\n"
                                       "+24V is live before VDDA: the BAT54S holds the ball at VDDA + VF (7.10.1.4.2).")
    B.R("120k", "+24V", "V24_MON")
    B.R("10.0k", "V24_MON", "AGND")
    B.C("100n", "V24_MON", "AGND")
    B.part("BAT54S", {"1": "AGND", "2": "+3V3A", "3": "V24_MON"})
    B.block("AGND - GND star point", "Single connection of the analog return to board ground (layout: under U301).")
    B.part("NETTIE", {"1": "AGND", "2": "GND"})
    B.flag("AGND")


def sheet_trip(B):
    B.new_sheet("06_trip", "Fault receivers, enable, EN drivers",
                "Cn_EN = GATE_EN AND CHB_OK AND MCU enable (74LVC11A); line drivers on only while\n"
                "CHB_OK, MCU enable and RDY; EN drivers with readback; fail-safe FLT receivers")

    def and3(a_, b_, c_, y_):
        """One 3-input gate per port in two 74LVC11A (SCLS993A); spare gates: inputs to GND, outputs open."""
        for u in range(2):
            pins = {"14": "+3V3", "7": "GND"}
            for g, gate in enumerate([("1", "2", "13", "12"), ("3", "4", "5", "6"), ("9", "10", "11", "8")]):
                n = 3 * u + g + 1
                pins.update(dict(zip(gate, [s % n if "%d" in s else s for s in (a_, b_, c_, y_)])) if n <= 4 else
                            dict(zip(gate, ("GND", "GND", "GND", None))))
            B.part("LVC11", pins)
            B.C("100n", "+3V3", "GND")
    B.block("Hardware gate-enable AND (both safety channels)",
            "Cn_EN = GATE_EN (channel A) AND CHB_OK (channel B) AND Cn_EN_MCU, one 3-input gate per port:\n"
            "firmware alone can never raise an EN pair. Channel B also removes +24V_GD (gate-driver bias) on SYS.\n"
            "10k pull-downs hold each MCU enable low in reset; GATE_EN and CHB_OK are pulled low on the SYS side.\n"
            "Spare gates: inputs to GND, outputs open.")
    and3("GATE_EN", "CHB_OK", "C%d_EN_MCU", "C%d_EN")
    for n in range(1, 5):
        B.R("10k", "C%d_EN_MCU" % n, "GND")
    B.TP("GATE_EN")
    B.TP("CHB_OK")
    B.block("Line-driver enable Cn_DRV_EN = CHB_OK AND Cn_EN_MCU AND Cn_RDY (INT-15)",
            "Drives G of both PWM AM26LV31E of the port (G~ tied high) and DE of its EN driver. With +24V_GD off\n"
            "(CHB_OK low), the port disabled, or that board's driver supplies not good (RDY low: its eFuse tripped or\n"
            "its bias failed) every pair of the port is high impedance, so the drivers no longer back-power an\n"
            "unpowered board: <= 16 x 100 uA (AM26LV31E IOZ, SLLS848C) + 2 x 125 uA (THVD1450 bus input) = 1.85 mA\n"
            "worst case per board instead of ~32 mA, inside every input-current rating; its fail-safe receivers read\n"
            "PWM and EN low, and an unpowered board has no gate bias. RDY_F = the filtered RDY (cell sheets).")
    and3("CHB_OK", "C%d_EN_MCU", "C%d_RDY_F", "C%d_DRV_EN")
    B.block("EN line drivers with readback (C1-C4)",
            "THVD1450 per port: D = Cn_EN, DE = Cn_DRV_EN, A/B = EN_P/EN_N, |VOD| >= 2 V into 100 R (SLLSEY3E 7.7).\n"
            "Receiver always on (RE~ low): R = the pair's state at the CTRL end -> Cn_EN_RB on a spare GPIO, so a\n"
            "stuck AND gate or driver is visible (INT-22; start-up and proof-test sequence in the pin plan).")
    for n in range(1, 5):
        c = "C%d_" % n
        B.part("THVD1450", {"4": c + "EN", "3": c + "DRV_EN", "2": "GND", "1": c + "EN_RB", "8": "+3V3", "5": "GND",
                            "6": c + "EN_P", "7": c + "EN_N"})
        B.C("100n", "+3V3", "GND")
    B.block("FLT receivers (C1-C4), fail-safe",
            "Pair wired swapped (A = FLT_N, B = FLT_P): Cn_FAULT is high = fault. 560R bias + 120R termination\n"
            "put +0.32 V across an open/unplugged pair (> VIT+ 0.2 V) -> fault; the AM26LV32E rev E only has\n"
            "open-circuit (not terminated) fail-safe, hence the external bias. Healthy: driver -2 V -> low.\n"
            "Cn_FAULT -> INPUTXBAR2-5 -> ePWM X-BAR -> TRIP4/5/7/8 (no firmware in the trip path).")
    B.part("AM26LV32E", {"2": "C1_FLT_N", "1": "C1_FLT_P", "6": "C2_FLT_N", "7": "C2_FLT_P", "10": "C3_FLT_N",
                         "9": "C3_FLT_P", "14": "C4_FLT_N", "15": "C4_FLT_P", "3": "C1_FAULT", "5": "C2_FAULT",
                         "11": "C3_FAULT", "13": "C4_FAULT", "4": "+3V3", "12": "GND", "16": "+3V3", "8": "GND"})
    B.C("100n", "+3V3", "GND")
    for n in range(1, 5):
        B.R("120R", "C%d_FLT_P" % n, "C%d_FLT_N" % n)
        B.R("560R", "+3V3", "C%d_FLT_N" % n)
        B.R("560R", "C%d_FLT_P" % n, "GND")
    B.block("Test points", "Gated EN (after the AND) and receiver FAULT outputs, one pair per port.")
    for n in range(1, 5):
        B.TP("C%d_EN" % n)
        B.TP("C%d_FAULT" % n)


def sheet_cell(B, n):
    c = "C%d_" % n
    B.new_sheet("%02d_cell%d" % (6 + n, n), "Cell port C%d" % n,
                "CELL contract 2x20: 8 PWM RS-422 drivers, 3 dual-range analog inputs, RDY, ID,\n"
                "NTC; gate-driver supply +24V_GD through a 1.5 A eFuse (TPS26600)")
    B.block("C%d connector (CELL contract)" % n,
            "Pin map = interfaces.CELL. Pins 1-2 carry +24V_GD only (gate-driver bias, channel B switched).\n"
            "NTC_N returns to AGND here; FLT/EN pairs go to the trip sheet.")
    B.part("J_CELL", IF.pins(IF.CELL, c, rename={"+24V": c + "24V", "NTC_N": "AGND"}))
    B.C("10u", c + "24V", "GND", pkg="1210", volt="50V", tol="10%")
    B.C("100n", c + "24V", "GND", volt="100V")
    e = efuse(CELL_EFUSE, IF.CELL_24V_MAX_UF * 1e-6)
    B.block("+24V_GD eFuse for C%d (<= %.1f A continuous, <= %d uF)" % (n, IF.CELL_24V_MAX_A, IF.CELL_24V_MAX_UF),
            "TPS26600, MODE open: circuit breaker I(CB) %.2f-%.2f A (RILIM %s 1 %%, eq. 4 + table 7.5), 4 ms, 540 ms\n"
            "auto-retry; factory UVLO 15 V / OVP 32.6 V (UVLO, OVP to RTN); SHDN open = on (self-biased 2.7 V).\n"
            "dVdT %s (CSR-03): %.2f-%.2f V/ms, 26.4 V in %.0f-%.0f ms; %d uF draws <= %.3f A, so start-up <= %.2f A per\n"
            "port and %.2f A for four (SYS +24V_GD limit >= 4.69 A). Start-up dissipation <= %.1f W (resistive-load\n"
            "model, eq. 22-24) vs SLVSDG2G figure 55 at 85 C (graph reading, not verified). Asserted on every build.\n"
            "A shorted cell cable trips only its own eFuse. A PTC was rejected: MF-MSMF110 holds only 0.73 A at 60 C."
            % (e["icb"][0], e["icb"][1], CELL_EFUSE[0], CELL_EFUSE[1], e["slope"][0] / 1e3, e["slope"][1] / 1e3,
               e["t_ramp"][0] * 1e3, e["t_ramp"][1] * 1e3, IF.CELL_24V_MAX_UF, e["inrush"],
               IF.CELL_24V_MAX_A + e["inrush"], 4 * (IF.CELL_24V_MAX_A + e["inrush"]), e["pd"]))
    B.part("TPS26600", {"1": "+24V_GD", "2": "+24V_GD", "3": "GND", "5": "GND", "6": None, "7": None, "9": "GND",
                        "8": "GND", "17": "GND", "15": c + "24V", "16": c + "24V", "11": c + "ILIM", "12": c + "DVDT",
                        "10": None, "14": None, "4": None, "13": None})
    B.C("1u", "+24V_GD", "GND", pkg="1206", volt="50V")
    B.R(CELL_EFUSE[0], c + "ILIM", "GND")
    B.C(CELL_EFUSE[1], c + "DVDT", "GND", tol="10%")
    hr = ("ePWM%d-%d have high-resolution PWM: the dual-active bridge (phase shift at 100 kHz) must use ports C1 and\n"
          "C2, one bridge per port." % (4 * n - 3, 4 * n) if n <= 2 else
          "ePWM%d-%d have no high-resolution PWM: buck-boost cells only, not a DAB bridge." % (4 * n - 3, 4 * n))
    B.block("PWM line drivers (ePWM%d-%d)" % (4 * n - 3, 4 * n),
            "PWMk (1 = switch on) -> RS-422 pair, terminated on the power board. 10k pull-downs: ePWM balls are\n"
            "inputs without pull-up during reset/boot (table 6-6), so every gate command is 0 until firmware runs.\n"
            "G = C%d_DRV_EN, G~ high: outputs high impedance unless CHB_OK, C%d_EN_MCU and RDY are high (INT-15).\n"
            % (n, n)
            + hr)
    for u in range(2):
        k0 = 4 * u + 1
        B.part("AM26LV31E", {"1": "%sPWM%d" % (c, k0), "7": "%sPWM%d" % (c, k0 + 1), "9": "%sPWM%d" % (c, k0 + 2),
                             "15": "%sPWM%d" % (c, k0 + 3), "4": c + "DRV_EN", "12": "+3V3", "16": "+3V3", "8": "GND",
                             "2": "%sPWM%d_P" % (c, k0), "3": "%sPWM%d_N" % (c, k0),
                             "6": "%sPWM%d_P" % (c, k0 + 1), "5": "%sPWM%d_N" % (c, k0 + 1),
                             "10": "%sPWM%d_P" % (c, k0 + 2), "11": "%sPWM%d_N" % (c, k0 + 2),
                             "14": "%sPWM%d_P" % (c, k0 + 3), "13": "%sPWM%d_N" % (c, k0 + 3)})
        B.C("100n", "+3V3", "GND")
    for k in range(1, 9):
        B.R("10k", "%sPWM%d" % (c, k), "GND")
    names = ["%sAN%d" % (c, k) for k in range(1, 4)]
    k_adc = CELL_AN1_V_PER_A * val(CELL_AN1_AFE[0]) / 10.0e3
    B.block("Analog front ends AN1-AN3 (OPA4388), dual range",
            AFE_NOTE % ("G = 4.02k/10.0k = 0.402.", "483 kHz") + "\n"
            "Accepts on any AN pair: differential on a 1.24-1.49 V common mode, legs 0.1-2.6 V (ADC span +/-3.1 V),\n"
            "or 0-3.0 V single-ended with AN_N = AGND at the source (temperatures) -> 1.25-2.46 V. Op-amp inputs 0.9-1.75 V\n"
            "(RRIO). Source resistance adds to the 10.0k: keep it <= 100 R (1 %% gain) or calibrate it out.\n"
            "AN1 = inductor current (INT-12, one constant): PVCELL-25 LEM LA 150-P %.1f mV/A, 1.25 V CM, < 1 R source,\n"
            "linear +/-114 A -> %.2f mV/A at the ADC, %.4f A/LSB; CMPSS backup +/-%.1f A. The LA 150-P offset (up to\n"
            "1.3 A) is re-zeroed by firmware at every idle (gates off); trip bands carry only the %.2f A left after it.\n"
            "DAB-D60 (C1/C2): AN1 5.005 mV/A (+/-1.58 V max), AN2 <= 2.79 V, AN3 <= 2.73 V single-ended, 49.9 R\n"
            "sources (+0.5 %% gain). Only AN1 carries the open-input bias. Channel D: VMID follower."
            % (CELL_AN1_V_PER_A * 1e3, k_adc * 1e3, 2.5 / 4096 / k_adc, CELL_OC_TRIP_A, CELL_I_OFFS_A))
    opamp(B, names, c + "SPARE")
    afe(B, names[0], ADC[names[0]], CELL_AN1_AFE, bias=True)
    for name in names[1:]:
        afe(B, name, ADC[name], AFE_CELL)
    B.block("RDY, ID, NTC",
            "RDY: push-pull from the power board (PVCELL-25 rev B, DAB-D60 rev B: LVC1G17, Ioff = high impedance when\n"
            "unpowered) or open drain with its own pull-up; the 100k pull-down here reads not-ready when the cable is\n"
            "unplugged or the board unpowered (INT-14). Levels asserted on every build; 1k/1 nF (1 us) filter into the\n"
            "GPIO and the driver-enable AND. ID: 10.0k from VBIAS (2.5 V), decode table in the pin plan (MUX_OUT).\n"
            "NTC (floating pair): 10.0k from VBIAS, 10k NTC reads 1.25 V at 25 C.")
    B.R("100k", c + "RDY", "GND")
    B.R("1k", c + "RDY", c + "RDY_F")
    B.C("1n", c + "RDY_F", "GND")
    B.R("10.0k", "VBIAS", c + "ID", tol="0.1%", note=PREC)
    B.C("100n", c + "ID", "AGND")
    B.R("10.0k", "VBIAS", c + "NTC_P", tol="0.1%")
    B.C("100n", c + "NTC_P", "AGND")


def sheet_port(B):
    B.new_sheet("11_port", "Port-board analog inputs",
                "PORT contract 2x13: VA, VAX, VB, VBX, IA, IB, AUX1, AUX2 isolated-amplifier pairs,\n"
                "biased open-safe, through difference amp + RC + clamp; ID; +24V out through an eFuse")
    B.block("Port-board connector (PORT contract)",
            "Pin map = interfaces.PORT. P_ID: 10.0k from VBIAS. The port board sums its hold readbacks onto ID: decode\n"
            "table (both codes x 9 states, unpowered, open, short; every band >= %d counts apart) in the pin plan.\n"
            "An unpowered board reads its own band inside the PV-PORT PPB window: firmware decodes it, and in\n"
            "hardware VA/VB read <= -181 V and trip (INT-07, asserted)." % ID_SEP)
    B.part("J_PORT", IF.pins(IF.PORT, "P_", rename={"+24V": "P_24V"}))
    B.R("10.0k", "VBIAS", "P_ID", tol="0.1%", note=PREC)
    B.C("100n", "P_ID", "AGND")
    e = efuse(PORT_EFUSE, PORT_BOARD_UF * 1e-6)
    B.block("P_24V eFuse (INT-06, CSR-12)",
            "Port board: up to %.1f A for up to 200 s (battery port: two hold-closed coils + sensors). TPS26600,\n"
            "MODE open: circuit breaker I(CB) %.3f-%.3f A (RILIM %s 1 %%), so the hold path never trips it; a shorted\n"
            "port board or cable trips only this branch (4 ms, 540 ms retry), and CTRL %.2f A + %.2f A stays below the\n"
            "SYS +24V eFuse (>= %.2f A): the control card keeps running. dVdT %s: %.2f-%.2f V/ms into %.1f uF.\n"
            "Factory UVLO 15 V / OVP 32.6 V; SHDN open. The PTC (0.73 A hold at 60 C) is gone."
            % (PORT_24V_DEMAND_A, e["icb"][0], e["icb"][1], PORT_EFUSE[0], CTRL_24V_LOAD_A, e["icb"][1],
               SYS_24V_MIN_A, PORT_EFUSE[1], e["slope"][0] / 1e3, e["slope"][1] / 1e3, PORT_BOARD_UF))
    B.part("TPS26600", {"1": "+24V", "2": "+24V", "3": "GND", "5": "GND", "6": None, "7": None, "9": "GND",
                        "8": "GND", "17": "GND", "15": "P_24V", "16": "P_24V", "11": "P_ILIM", "12": "P_DVDT",
                        "10": None, "14": None, "4": None, "13": None})
    B.C("1u", "+24V", "GND", pkg="1206", volt="50V")
    B.R(PORT_EFUSE[0], "P_ILIM", "GND")
    B.C(PORT_EFUSE[1], "P_DVDT", "GND", tol="10%")
    B.C("1u", "P_24V", "GND", pkg="1206", volt="50V")
    groups = (["P_VA", "P_VAX", "P_VB", "P_VBX"], ["P_IA", "P_IB", "P_AUX1", "P_AUX2"])
    B.block("Analog front ends (2 x OPA4388)", AFE_NOTE % ("G = 5.76k/10.0k = 0.576: +/-2 V -> 0.10-2.40 V.", "493 kHz")
            + "\n" + BIAS_NOTE + "\nVA, VB, IA, IB sit on CMPSS positive inputs; trip paths and bands in the pin plan.")
    for names in groups:
        opamp(B, names, "P_SPARE")
    for name in groups[0] + groups[1]:
        afe(B, name, ADC[name], AFE_PORT, bias=True)


# SYS -> CTRL push-pull inputs, buffered (8 per 74LVC244A), with the level each one rests at when SYS is unplugged
SYS_IN = [("CANA_RX", "+3V3"), ("CANB_RX", "+3V3"), ("MCAN_RX", "+3V3"), ("SCIA_RX", "+3V3"), ("SCIB_RX", "+3V3"),
          ("SCIC_RX", "+3V3"), ("SPI_SOMI", "GND"), ("PG_24V", "GND")] + [("DI%d" % i, "GND") for i in range(1, 9)] + \
         [("FLT_LATCH_N", "TRIP"), ("GATE_EN", "TRIP"), ("ESTOP_N", "TRIP")] + \
         [("FAN_TACH%d" % i, "GND") for i in range(1, 5)] + [("PG_HVAUX", "GND")]
LVC244_CH = [("2", "18"), ("4", "16"), ("6", "14"), ("8", "12"), ("11", "9"), ("13", "7"), ("15", "5"), ("17", "3")]


def sheet_sys(B):
    B.new_sheet("12_sys", "SYS-IO-AUX connector",
                "80-way SYS contract: +24 V logic, +24V_GD (channel B), CAN, SCI, SPI, I2C, DI/DO,\n"
                "fault latch, GATE_EN, CHB_OK, watchdog, reset, fans, Ethernet MDI + centre tap")
    B.block("SYS connector (SYS contract)",
            "Pin map = interfaces.SYS. SYS_RST_N lands directly on XRSn (open drain both sides).\n"
            "+24V (pins 1-2): this card's regulators and the PORT connector. +24V_GD (pins 3-4): switched by\n"
            "safety channel B on SYS; goes ONLY to the four cell eFuses (cell sheets). The two never meet here.\n"
            "ETH_CT (77): PHY analog supply to the magnetics centre taps (Ethernet sheet).\n"
            "Inputs from SYS arrive as S_<name> and reach the MCU as <name> through the buffers.")
    B.part("J_SYS", IF.pins(IF.SYS, rename=dict({"SYS_RST_N": "XRSn", "CHB_OK": "S_CHB_OK"},
                                                **{n: "S_" + n for n, _ in SYS_IN})))
    for rail in ("+24V", "+24V_GD", "GND"):
        B.flag(rail)
        B.TP(rail)
    B.C("10u", "+24V_GD", "GND", pkg="1210", volt="50V", tol="10%")
    B.C("100n", "+24V_GD", "GND", volt="100V")
    B.block("CHB_OK buffer (channel B, own package)",
            "Schmitt buffer with Ioff, separate from the 74LVC244A that carries GATE_EN, so one package\n"
            "cannot corrupt both safety channels. 10k pull-down: SYS unplugged = channel B not OK.")
    B.part("LVC1G17", {"2": "S_CHB_OK", "1": None, "5": "+3V3", "3": "GND", "4": "CHB_OK"})
    B.C("100n", "+3V3", "GND")
    B.R("10k", "S_CHB_OK", "GND")
    B.block("SYS input buffers (Ioff)",
            "SYS makes its own 3.3 V and may drive these lines before this card's VDDIO is up; SPRSP14E 7.1 allows\n"
            "+/-20 mA total input-clamp current and 7.10.1.4.2 forbids driving pins early. 74LVC244A inputs and\n"
            "outputs are high impedance unpowered (Ioff) and its outputs never exceed VDDIO. tpd ~5 ns.")
    for u in range(3):
        pins = {"1": "GND", "19": "GND", "20": "+3V3", "10": "GND"}
        for (a, y), (net, _) in zip(LVC244_CH, SYS_IN[8 * u:8 * u + 8]):
            pins.update({a: "S_" + net, y: net})
        B.part("LVC244", pins)
        B.C("100n", "+3V3", "GND")
    B.block("Inputs with SYS unplugged",
            "FLT_LATCH_N, GATE_EN, ESTOP_N: 10k to GND, so an unplugged SYS reads as tripped and the gates stay off.\n"
            "Other inputs: 100k to their idle level (CAN/SCI receive high, the rest low).")
    for net, rest in SYS_IN:
        if rest == "TRIP":
            B.R("10k", "S_" + net, "GND")
        elif rest == "GND":
            B.R("100k", "S_" + net, "GND")
        else:
            B.R("100k", "+3V3", "S_" + net)
    B.block("Outputs while the MCU is in reset",
            "GPIOs float in reset: CAN TX recessive, RS-485 DE off, DO and FLT_CLR low, SPI chip selects high.\n"
            "I2C: 2.2k pull-ups here (bus master side); SYS should not add a second pair.")
    for net in ("CANA_TX", "CANB_TX", "MCAN_TX", "SPI_CS_ADC_N", "SPI_CS_IO_N"):
        B.R("10k", "+3V3", net)
    for net in ["SCIA_DE", "SCIB_DE", "SCIC_DE", "FLT_CLR"] + ["DO%d" % i for i in range(1, 9)]:
        B.R("10k", net, "GND")
    B.R("2.2k", "+3V3", "I2C_SDA")
    B.R("2.2k", "+3V3", "I2C_SCL")
    B.TP("FLT_LATCH_N")


def sheet_eth(B):
    B.new_sheet("13_eth", "Ethernet PHY DP83822I",
                "MII to the CM EMAC (GPIO105-124, mux 14), own 25 MHz oscillator, 49.9 R MDI\n"
                "terminations to AVD; MDI pairs, LEDs and the centre-tap supply ETH_CT go to SYS")
    B.block("DP83822I (MII, PHY address 1, auto-neg, auto-MDIX: all default straps)",
            "LED_0 = link, LED_1 = activity (register-configured): 2.49k parallel pull-ups (7.5.2) keep both straps\n"
            "at mode 4 and make the LEDs active-low; SYS sinks its LED current into these pins.\n"
            "RESET_N: internal pull-up 6.75-11.25 k, so 1.0k holds it at <= 0.43 V < VIL 0.8 V while GPIO119 floats\n"
            "(CSR-06); firmware pulses it low >= 10 us after the clock is stable. XI 10k pull-down (9.2.2.1.1.1).")
    B.part("DP83822I", {"2": "ETH_TX_CLK", "3": "ETH_TX_EN", "4": "ETH_TXD0", "5": "ETH_TXD1", "6": "ETH_TXD2",
                        "7": "ETH_TXD3", "25": "ETH_RX_CLK", "26": "ETH_RX_DV", "28": "ETH_RX_ER", "27": "ETH_CRS",
                        "29": "ETH_COL", "30": "ETH_RXD0", "31": "ETH_RXD1", "32": "ETH_RXD2", "1": "ETH_RXD3",
                        "20": "ETH_MDC", "19": "ETH_MDIO", "8": "ETH_INT_N", "18": "ETH_RST_N",
                        "12": "ETH_TXP", "11": "ETH_TXN", "10": "ETH_RXP", "9": "ETH_RXN", "17": "ETH_LED_LINK",
                        "24": "ETH_LED_ACT", "23": "ETH_XI", "22": None, "16": "ETH_RBIAS", "13": None, "15": None,
                        "21": "+3V3", "14": "ETH_AVD", "33": "GND"})
    B.R("4.87k", "ETH_RBIAS", "GND")
    B.R("2.2k", "+3V3", "ETH_MDIO")
    B.R("10k", "+3V3", "ETH_INT_N")
    B.R("1.0k", "ETH_RST_N", "GND")
    B.R("2.49k", "+3V3", "ETH_LED_LINK")
    B.R("2.49k", "+3V3", "ETH_LED_ACT")
    B.block("PHY supplies (VDDIO 3.3 V, AVD via ferrite)",
            "Figure 9-5: 10 nF / 100 nF / 1 uF / 10 uF on VDDIO and on AVD, ferrite bead for EMC (optional, fitted)\n"
            "between the 3.3 V supply and AVD so the RS-422 drivers' supply current stays out of the analog rail.")
    for net in ("+3V3", "ETH_AVD"):
        for v, pkg in (("10n", "0402"), ("100n", "0402"), ("1u", "0603"), ("10u", "0805")):
            B.C(v, net, "GND", pkg=pkg, volt="16V")
    B.part("FB", {"1": "+3V3", "2": "ETH_AVD"})
    B.flag("ETH_AVD")
    B.TP("ETH_AVD")
    B.block("MDI terminations and centre tap (ETH_CT)",
            "Figure 9-2: 49.9 R from each MDI pin to AVD, 1 uF + 0.1 uF at each pair; the magnetics centre taps on\n"
            "SYS must sit at AVD (9.2.1.1: centre-tap supply = AVD): ETH_CT = AVD through the optional EMC ferrite.\n"
            "SYS only decouples ETH_CT. Load (table 9-4, MII link up): magnetics 22 mA; ferrite drop < 4 mV.")
    for net in ("ETH_TXP", "ETH_TXN", "ETH_RXP", "ETH_RXN"):
        B.R("49.9R", net, "ETH_AVD")
    for _ in range(2):
        B.C("1u", "ETH_AVD", "GND", volt="16V")
        B.C("100n", "ETH_AVD", "GND")
    B.part("FB", {"1": "ETH_AVD", "2": "ETH_CT"})
    B.block("PHY 25 MHz clock", "Table 9-1: 25 MHz +/-100 ppm, tr/tf <= 8 ns, 40-60 % -> ASDMB (+/-50 ppm, 2 ns).")
    B.part("OSC25", {"4": "+3V3", "1": "+3V3", "2": "GND", "3": "ETH_OSC"})
    B.C("10n", "+3V3", "GND")
    B.R("33R", "ETH_OSC", "ETH_XI")
    B.R("10k", "ETH_XI", "GND")


def sheet_debug(B):
    B.new_sheet("14_debug", "JTAG, UART, EEPROM, LEDs, SDFM",
                "cTI-20 JTAG, CM debug UART, 24LC256 calibration EEPROM (0x57), status LEDs,\n"
                "ERRORSTS, 8-channel SDFM expansion header (ECO-02 / ECO-09)")
    B.block("JTAG cTI-20 (SPRU655I table 13)",
            "TMS 2.2k pull-up and TRSTn 2.2k pull-down (SPRSP14E table 6-1); TDIS to GND; VTRef via 100 R;\n"
            "RTCK looped to TCK; EMU0/1 4.7k pull-ups (no EMU pins on F2838x); RESET (open drain) on XRSn;\n"
            "EMU2-EMU4 (17-19) grounded as SPRSP14E 7.10.7 requires (CSR-07).")
    B.part("J_JTAG", {"1": "TMS", "2": "TRSTn", "3": "TDI", "4": "GND", "5": "JTAG_VREF", "6": None, "7": "TDO",
                      "8": "GND", "9": "TCK", "10": "GND", "11": "TCK", "12": "GND", "13": "JTAG_EMU0",
                      "14": "JTAG_EMU1", "15": "XRSn", "16": "GND", "17": "GND", "18": "GND", "19": "GND", "20": "GND"})
    B.R("2.2k", "+3V3", "TMS")
    B.R("2.2k", "TRSTn", "GND")
    B.R("100R", "+3V3", "JTAG_VREF")
    B.R("4.7k", "+3V3", "JTAG_EMU0")
    B.R("4.7k", "+3V3", "JTAG_EMU1")
    B.block("Debug UART (CM-UART-A, 3.3 V TTL)", "Pin 1 GND, 2 TX (from MCU), 3 RX (to MCU); 100 R series each way.")
    B.part("J_UART", {"1": "GND", "2": "DBG_TX_H", "3": "DBG_RX_H"})
    B.R("100R", "DBG_TX", "DBG_TX_H")
    B.R("100R", "DBG_RX_H", "DBG_RX")
    B.block("Calibration / serial-number EEPROM",
            "Address 1010111 (A2-A0 high = 0x57) so it cannot collide with a SYS EEPROM at the default 0x50.\n"
            "WP pulled high: write-protected unless firmware drives EEPROM_WP low.")
    B.part("24LC256", {"8": "+3V3", "1": "+3V3", "2": "+3V3", "3": "+3V3", "7": "EEPROM_WP", "4": "GND",
                       "5": "I2C_SDA", "6": "I2C_SCL"})
    B.C("100n", "+3V3", "GND")
    B.R("10k", "+3V3", "EEPROM_WP")
    B.block("Status LEDs and ERRORSTS", "GPIO sinks about 2 mA (680 R). ERRORSTS needs an external pull-down.")
    B.R("1k", "+3V3", "LED_PWR_A")
    B.part("LED_G", {"2": "LED_PWR_A", "1": "GND"})
    for net, key in (("LED_CPU1_N", "LED_G"), ("LED_CPU2_N", "LED_G"), ("LED_CM_N", "LED_G"), ("LED_FLT_N", "LED_R")):
        a = net[:-2] + "_A"
        B.R("680R", "+3V3", a)
        B.part(key, {"2": a, "1": net})
    B.R("10k", "ERRORSTS", "GND")
    B.TP("ERRORSTS")
    B.block("SDFM expansion header (no mating contract yet)",
            "8 modulator data inputs (SD1/SD2 D1-D4) and one clock: XCLKOUT out, returned clock to all\n"
            "eight SDx_Cy inputs. 3.3 V CMOS; for an adjacent sensing board only.")
    B.part("J_SDFM", {"1": "+3V3", "2": "GND", "3": "SD_CLK_OUT", "4": "GND", "5": "SD_CLK_RET", "6": "GND",
                      "7": "SD1_D1", "8": "SD1_D2", "9": "SD1_D3", "10": "SD1_D4", "11": "GND", "12": "GND",
                      "13": "SD2_D1", "14": "SD2_D2", "15": "SD2_D3", "16": "SD2_D4", "17": "GND", "18": "GND",
                      "19": "+3V3", "20": "GND"})
    B.R("33R", "SD_XCLK", "SD_CLK_OUT")


def mcu_pins(pins, P):
    """Ball -> net: power and system balls by name, signal balls from the pin plan, every other GPIO open."""
    net_of = {pin: net for net, pin, fn, d, route in P}
    fixed = {"VDDIO": "+3V3", "VDD3VFL": "+3V3", "VDDOSC": "+3V3", "VDD": "+1V2", "VDDA": "+3V3A", "VSS": "GND",
             "VSSOSC": "GND", "VSSA": "AGND", "X1": "MCU_X1", "X2": None, "XRSn": "XRSn", "ERRORSTS": "ERRORSTS",
             "FLT1": None, "FLT2": None, "NC1": None, "NC2": "+3V3", "TCK": "TCK", "TMS": "TMS", "TDI": "TDI",
             "TDO": "TDO", "TRSTn": "TRSTn"}
    fixed.update({"VREFHI" + a: "VREF2V5" for a in "ABCD"})
    fixed.update({"VREFLO" + a: "AGND" for a in "ABCD"})
    out = {}
    for r in pins:
        n = r["name"]
        out[r["ball"]] = fixed[n] if n in fixed else net_of.get(n)
    return out


def build_design():
    pins = load_pins()
    P = plan()
    check_plan(P, pins)
    ADC.update({net: fn for net, pin, fn, d, route in P if d == "ain"})
    PARTS["F28388D"] = mcu_entry(pins)
    B = L.Builder(PROJECT, "CTRL-C2000 control card", REV, DATE, dict(catalog.PARTS, **PARTS),
                  rails=RAILS, returns=["GND", "AGND"],
                  subtitle="TMS320F28388D common controller for PV-P75/100/110 and DAB-D60",
                  comment1="All interface contracts: gen/interfaces.py (CELL x4, PORT, SYS)",
                  comment4="Not bench-validated. Values marked calculated are not simulated.",
                  root_notes=[
                      "Protection without firmware: FLT_LATCH_N -> INPUTXBAR1 -> TZ1; each cell's FAULT (INPUTXBAR2-5) "
                      "and AN1 CMPSS window -> TRIP4/5/7/8; port OV (ADC PPB on VA/VB, CMPSS backup), open inputs "
                      "(CMPSS low), IA/IB SC (CMPSS) and OC (PPB), P_ID (PPB) -> TRIP9 for every cell; E-stop -> "
                      "INPUTXBAR6 -> TRIP6. CMPSS reference = VDAC = REF5025E; firmware nulls each channel at zero input "
                      "at start-up; trip bands in the pin plan, asserted against the frozen earlier-platform snapshot gen/data/control_spec_platform1.json (D-044). "
                      "EN pair = GATE_EN AND CHB_OK AND MCU enable (74LVC11A); line drivers on only with CHB_OK, MCU "
                      "enable and RDY; EN readback on GPIO100-103. +24V_GD feeds only the cell eFuses.",
                      "Rails: 24 V -> 5 V (LMR38020) -> 3.3 V (TPS62133); its PG enables 1.2 V (TPS62130) and 3.3 V "
                      "analog (TPS7A2033), so VDDIO leads VDD and VDDA (SPRSP14E table 7-1). Three TPS3703 hold XRSn "
                      "(windows on 3.3 V and 3.3 V analog, UV on 1.2 V), every tolerance corner asserted. REF5025E runs "
                      "from the analog rail, so VREFHI and VDAC never exceed VDDA.",
                      "Pin plan with mux positions and trip bands: gen/data/ctrl_c2000_pin_plan.csv (checked against "
                      "SPRSP14E table 6-1 on every build). Pin table: gen/data/f28388d_pins.csv (from the PDF).",
                      "SYS side: inputs via 74LVC244A / 74LVC1G17 (Ioff); I2C pull-ups and EEPROM 0x57 here; "
                      "ETH_CT = PHY AVD; PORT +24V via its own eFuse."])
    sheet_power(B)
    mcu = mcu_pins(pins, P)
    sheet_reset(B, mcu)
    sheet_mcu(B, mcu)
    sheet_decoupling(B)
    sheet_adcref(B)
    sheet_trip(B)
    for n in range(1, 5):
        sheet_cell(B, n)
    sheet_port(B)
    sheet_sys(B)
    sheet_eth(B)
    sheet_debug(B)
    return B, pins, P


def check_gd(B):
    """+24V_GD (channel-B gate-driver supply) may reach only the cell eFuses and their decoupling, never a logic rail."""
    gd = {"+24V_GD"} | {"C%d_24V" % n for n in range(1, 5)}
    logic = {"+24V", "P_24V", "V24_MON", "+5V", "+3V3", "+1V2", "+3V3A"}
    bad = []
    for ref, p in B.D.parts.items():
        nets, key = set(p.pins.values()), p.lib_id.split(":")[1]
        if not ref.startswith("J") and nets & gd and nets & logic:      # connector contacts are separate pins
            bad.append(ref)
        if "+24V_GD" in nets and key not in ("TPS26600", "C", "TP", "PWR_FLAG", "J_SYS"):
            bad.append(ref)
    print("[%s] +24V_GD kept apart from +24V (feeds only the four cell eFuses) - %s"
          % ("FAIL" if bad else "PASS", ", ".join(bad) or "%d parts checked" % len(B.D.parts)))
    return not bad


def say(ok, name, detail):
    print("[%s] %s - %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def check_supervisor():
    """CSR-01: reset at or above the MCU minimum, release below the regulator minimum, OV (window parts) above the
    regulator maximum and at or below the MCU maximum; for the UV-only 1.2 V part the regulator maximum itself."""
    ok = True
    for rail, part, fall, rel, ovmin, ovmax, rmin, rmax in supervisor():
        lo, hi = (1.14, 1.26) if rail == "+1V2" else (3.14, 3.47)
        good = fall >= lo and rel < rmin and (ovmin > rmax and ovmax <= hi if ovmin else rmax <= hi)
        ok &= say(good, "CSR-01 %s supervisor %s" % (rail, part),
                  "trips >= %.4f V (MCU min %.2f), releases <= %.4f V < regulator min %.4f V, %s" % (
                      fall, lo, rel, rmin, "OV %.4f-%.4f V vs regulator max %.4f V, MCU max %.2f" % (ovmin, ovmax, rmax, hi)
                      if ovmin else "regulator max %.4f V <= %.2f (UV-only part)" % (rmax, hi)))
    return ok


# ---- hardware-trip budgets (INT-05): errors at the ADC node that remain after the firmware start-up null at zero input
SPEC = os.path.join(L.REPO, "gen/data/control_spec_platform1.json")   # FROZEN snapshot (2026-10-05) of the control spec this
#   earlier-platform card was designed against; the live sim/out/pv_control/control_spec.json now describes the cost-first
#   PV-CTL chain (D-044, PCM-02 re-base) and no longer carries the AMC3302 / LA 150-P keys this check needs
PORT_SPEC = os.path.join(L.REPO, "sim/out/port_design/port_spec.json")
LSB = 2.5 / 4096                   # 12 bit, VREFHI = VDAC = 2.5 V
ADC_ERR = (5.0, 2.0)               # SPRSP14E 7.11.2.3.6: gain +/-5 LSB at full scale, INL +/-2 LSB (offset nulled)
CMPSS_ERR = (0.02, 16.0)           # 7.11.3.1.3: DAC gain +/-2 % FSR, INL +/-16 LSB (offset +/-25 mV incl. comparator nulled)
CTRL_GAIN = 2 * P01 + 0.0006       # AFE Rf/Rin + REF5025E (0.025 % + 2.5 ppm/K x 80 K + load regulation)
PORT_V_CHAIN = 0.00642             # sim/out/port_design/report.md sec. 4: divider + AMC3330, worst after 1-point cal
# Cell AN1 chain (PVCELL-25 rev B): LA 150-P error 0.5 % + linearity 0.1 % (LA_150-P.pdf p1), RM 0.1 % + 25 ppm/K x
# 60 K, driver 10.0k pairs 0.1 % + 25 ppm/K x 60 K per ratio (gen/pvcell.py; 60 K = the cell's own drift span)
CELL_I_CHAIN = 0.005 + 0.001 + 0.001 + 25e-6 * 60 + 2 * 0.001 + 2 * 25e-6 * 60
# Offset left after the firmware null at every idle (gates off, 0 A): LA 150-P IOM 0.25 mA + IOT 0.30 mA max (p1) x 2000,
# + 4 x OPA4322 VOS drift 6 uV/K x 60 K (SBOS538F p7) at 20 mV/A. IOE (0.10 mA = 0.2 A) is removed by the null.
CELL_I_OFFS_A = (0.25e-3 + 0.30e-3) * 2000 + 4 * 6e-6 * 60 / CELL_AN1_V_PER_A
CELL_NULL_A = 5.0                  # firmware rejects an idle null larger than this (LA 150-P IO total 1.3 A + drivers)
PVCELL_CHECK = os.path.join(L.REPO, "hardware/PVCELL-25/outputs/PVCELL-25_design_check.txt")
CELL_SPEC = os.path.join(L.REPO, "sim/out/pv_design/cell_spec.json")
OV_BACKUP_V, OPEN_V = 1110.0, -120.0   # CMPSS high (backup) and low (open input) on VA/VB
PPB_US = 10.0 + 1.0                # VA/VB converted every <= 10 us, + conversion and X-BAR/TZ chain
CLAMP_V = 0.3                      # assumed lowest clamp of an unpowered isolated-amplifier output (not specified)


def band(x, k, path, chain, offs=0.0):
    """(low, high) magnitude of a trip at |x| (physical units); k = volts at the ADC node per unit."""
    d = abs(x) * k
    e = (ADC_ERR[0] * d / 2.5 + 2 * ADC_ERR[1] + 0.5) * LSB if path == "PPB" else CMPSS_ERR[0] * d + 2 * CMPSS_ERR[1] * LSB
    e = e / k + abs(x) * (CTRL_GAIN + chain) + offs
    return abs(x) - e, abs(x) + e


def open_reading(g, k, vcms=(1.39, 1.49)):
    """Least extreme reading (physical units) of a biased pair per open-source case, over the source common mode
    (default AMC3330/AMC3302 VCMout 1.39-1.49 V), +3V3A 3.25-3.35 V and bias 1 %; ADC node clamped to 0-2.5 V.
    Same network as afe()."""
    rin, rf, out = 10.0e3, g * 10.0e3, {}
    for vcm in vcms:
        for v3a in (3.2505, 3.3495):
            for rb in (val(BIAS) * 0.99, val(BIAS) * 1.01):
                p_open = 1.25 * rb / (rb + rin + rf)

                def n_open(p):
                    return (v3a * rin + (p + (1.25 - p) * rin / (rin + rf)) * rb) / (rin + rb)
                for case, (p, n) in (("P open", (p_open, vcm)), ("N open", (vcm, n_open(vcm))),
                                     ("both open", (p_open, n_open(p_open))), ("unpowered", (0.0, CLAMP_V))):
                    x = (min(max(1.25 + g * (p - n), 0.0), 2.5) - 1.25) / k
                    out[case] = max(out.get(case, -1e9), x)
    return out


def pvcell_an1():
    """AN1 figures as built by gen/pvcell.py (its design-check output): mV/A, linear range A, sensor delay us, local
    OC window (lo, hi) A and its gates-off time us. Raises if PVCELL-25 changes the wording (re-check this board)."""
    t = open(PVCELL_CHECK).read()
    mv, lin, dly = (float(x) for x in re.search(r"([\d.]+) mV/A differential.*?linear \+/-(\d+) A.*?= ([\d.]+) us vs",
                                                t).groups())
    lo, hi, off = (float(x) for x in re.search(r"OC window .*?([\d.]+)-([\d.]+) A with every tolerance.*?gate off "
                                               r"([\d.]+) us", t).groups())
    return dict(mv=mv, lin=lin, delay=dly, local=(lo, hi), off=off)


def check_trips():
    """INT-05 / INT-07: worst-case band of each hardware trip against what the design files need, and every
    open-input reading against those bands. Returns (ok, {net: pin-plan trip text})."""
    cs, ps = json.load(open(SPEC)), json.load(open(PORT_SPEC))
    hw, mr = cs["hardware_trips"], cs["measurement_requirements"]
    ov, oc = hw["port_overvoltage"], hw["port_overcurrent_A"]
    v_lim = float(re.search(r"<= (\d+) V", ov["basis"]).group(1))             # 0.85 x V_DSS incl. ringing
    v_slope = max(float(re.search(r"slope ([\d.]+) V/us", ov["basis"]).group(1)),
                  ov.get("full_current_frozen_case", {}).get("slope_V_per_us", 0.0))      # worst case in the spec
    gp, gc = val(AFE_PORT[0]) / 10e3, val(CELL_AN1_AFE[0]) / 10e3
    kv = ps["divider"]["ratio"] * 2.0 * gp                  # V at the ADC per port volt (AMC3330 gain 2)
    ki = ps["current"]["R_shunt_ohm"] * 41.0 * gp            # per port ampere (AMC3302 gain 41)
    kc = CELL_AN1_V_PER_A * gc                               # per cell ampere
    i_chain = ps["current"]["error_worst_compensated"]["135"]
    ok, trips = True, {}
    fs, res = 1.25 / kv, LSB / ki
    ok &= say(abs(fs / mr["port_voltage_VA_VB"]["adc_full_scale_V"] - 1) < 0.005 and
              abs(res / mr["port_current_IA_IB"]["resolution_A_per_LSB"] - 1) < 0.005,
              "INT-05 port scaling = control_spec", "VA/VB full scale %.1f V, IA/IB %.4f A/LSB" % (fs, res))
    # port voltage: PPB primary, CMPSS high backup, CMPSS/PPB low = open input
    b = band(ov["threshold_V"], kv, "PPB", PORT_V_CHAIN)
    hi_max = v_lim - v_slope * (ov["as_designed_detection_lag_us"] + PPB_US)
    ok &= say(hw["firmware_overvoltage_V"] < b[0] and b[1] <= hi_max, "INT-05 port OV (ADC PPB on VA/VB)",
              "%.0f V -> %.1f-%.1f V; needs > %.0f V (firmware OV) and <= %.1f V (%.0f V at gates-off, %.2f V/us x %.1f us)"
              % (ov["threshold_V"], b[0], b[1], hw["firmware_overvoltage_V"], hi_max, v_lim, v_slope,
                 ov["as_designed_detection_lag_us"] + PPB_US))
    bb = band(OV_BACKUP_V, kv, "CMPSS", PORT_V_CHAIN)
    ok &= say(hw["firmware_overvoltage_V"] < bb[0], "INT-05 port OV backup (CMPSS high on VA/VB)",
              "%.0f V -> %.1f-%.1f V; needs > %.0f V (only if the PPB path fails)" % (OV_BACKUP_V, bb[0], bb[1],
                                                                                     hw["firmware_overvoltage_V"]))
    bo, rv = band(OPEN_V, kv, "CMPSS", PORT_V_CHAIN), open_reading(gp, kv)
    ok &= say(bo[0] > 0 and max(rv.values()) < -bo[1], "INT-07 port open input (CMPSS/PPB low on VA/VB)",
              "%.0f V -> -%.1f..-%.1f V; reads %s" % (OPEN_V, bo[1], bo[0],
                                                     ", ".join("%s %.0f V" % kv_ for kv_ in rv.items())))
    for net in ("P_VA", "P_VB"):
        trips[net] = ("OV %.0f V (PPB): %.1f-%.1f V | OV backup %.0f V (CMPSS high): %.1f-%.1f V | open input %.0f V "
                      "(CMPSS low + PPB low): -%.1f..-%.1f V, an open conductor or unpowered port board reads <= %.0f V"
                      % (ov["threshold_V"], b[0], b[1], OV_BACKUP_V, bb[0], bb[1], OPEN_V, bo[1], bo[0], max(rv.values())))
    # port current: CMPSS SC, PPB OC (also catches one open conductor)
    bs, bc, ri = band(oc["SC"], ki, "CMPSS", i_chain), band(oc["OC"], ki, "PPB", i_chain), open_reading(gp, ki)
    single = max(ri["P open"], ri["N open"])
    ok &= say(bc[1] < bs[0] and bs[1] < ps["current"]["I_linear_A"], "INT-05 port SC (CMPSS on IA/IB)",
              "+/-%.0f A -> %.1f-%.1f A; needs > OC band %.1f A and < %.0f A (AMC3302 linear)"
              % (oc["SC"], bs[0], bs[1], bc[1], ps["current"]["I_linear_A"]))
    ok &= say(cs["limits"]["i_port_A"]["3 cells"] < bc[0] and single < -bc[1] and ri["both open"] < -bs[1],
              "INT-05/07 port OC (ADC PPB on IA/IB)",
              "+/-%.1f A -> %.1f-%.1f A; needs > %.0f A (rated); one open conductor reads <= %.0f A, both <= %.0f A"
              % (oc["OC"], bc[0], bc[1], cs["limits"]["i_port_A"]["3 cells"], single, ri["both open"]))
    for net in ("P_IA", "P_IB"):
        trips[net] = ("SC +/-%.0f A (CMPSS): %.1f-%.1f A | OC +/-%.1f A (PPB): %.1f-%.1f A | one open conductor reads "
                      "<= %.0f A, both <= %.0f A" % (oc["SC"], bs[0], bs[1], oc["OC"], bc[0], bc[1], single, ri["both open"]))
    # cell inductor current: CMPSS backup window on AN1 (PVCELL-25 rev B LA 150-P chain), above the cell's local trip
    pv, cl = pvcell_an1(), json.load(open(CELL_SPEC))
    io, cc = hw["inductor_overcurrent"], mr["cell_inductor_current"]
    didt = io["didt_max_A_per_us"]                                       # A/us, fastest bank-short rise
    t_bk = pv["delay"] + io["ctrl_backup"]["max_response_us"] - cc["group_delay_us_max"]   # sensor + AFE + CMPSS chain
    let = pv["local"][1] + didt * pv["off"]                              # current at the cell's own gates-off
    dev = cl["switch_positions"][0]["parallel"] * cl["bus_reverse_clamp"]["body_diode_share_with_clamp"]["I_DM_A"]
    limit = min(cl["inductor"]["I_at_50pct_L0_A"], dev)                 # what the cell tolerates (cell_spec.json)
    bl = band(CELL_OC_TRIP_A, kc, "CMPSS", CELL_I_CHAIN, CELL_I_OFFS_A)
    pk = bl[1] + didt * t_bk
    legs = (CELL_AN1_CM[0] - pv["lin"] * CELL_AN1_V_PER_A / 2, CELL_AN1_CM[1] + pv["lin"] * CELL_AN1_V_PER_A / 2)
    ok &= say(abs(pv["mv"] / (CELL_AN1_V_PER_A * 1e3) - 1) < 0.005 and LSB / kc <= cc["resolution_A_per_LSB"] * 1.001
              and legs[0] >= 0 and legs[1] <= 3.25, "INT-12 cell AN1 scaling (PVCELL-25 LA 150-P chain)",
              "%.1f mV/A (cell build %.1f), %.2f mV/A at the ADC, %.4f A/LSB vs control_spec %.4f; ADC span +/-%.0f A "
              "covers the linear +/-%.0f A; legs %.2f-%.2f V at 1.25 V CM inside AGND..+3V3A (op-amp inputs %.2f-%.2f V)"
              % (CELL_AN1_V_PER_A * 1e3, pv["mv"], kc * 1e3, LSB / kc, cc["resolution_A_per_LSB"], 1.25 / kc, pv["lin"],
                 legs[0], legs[1], (legs[0] * gc + 1.25) / (1 + gc), (legs[1] * gc + 1.25) / (1 + gc)))
    ok &= say(bl[0] > let and pk * (1 + CELL_I_CHAIN) <= pv["lin"] and pk <= limit and 1.25 / kc >= pv["lin"],
              "INT-05 cell OC backup (CMPSS window on AN1)",
              "+/-%.1f A -> %.1f-%.1f A (residual offset %.2f A after the idle null); (a) > %.1f A let through by the "
              "cell's own trip (%.1f-%.1f A + %.1f A/us x %.2f us), margin %.1f A; peak %.1f + %.1f A/us x %.2f us = "
              "%.1f A: (b) <= %.0f A sensor linear incl. %.1f %% gain, (c) <= %.0f A (50 %% L0 / %.0f A device), margin "
              "%.1f A" % (CELL_OC_TRIP_A, bl[0], bl[1], CELL_I_OFFS_A, let, pv["local"][0], pv["local"][1], didt, pv["off"],
                          bl[0] - let, bl[1], didt, t_bk, pk, pv["lin"], CELL_I_CHAIN * 100, limit, dev, limit - pk))
    rc = open_reading(gc, kc, CELL_AN1_CM)
    single = max(rc["P open"], rc["N open"])
    ok &= say(rc["both open"] < -bl[1] and single < -CELL_NULL_A, "INT-07 cell AN1 open input (1.25 V CM, < 1 R source)",
              "both lines open reads %.0f A (beyond -%.1f A: trips); one line open reads %.0f / %.0f A - inside the "
              "window: a leg can only be pulled to AGND or +3V3A (%.0f / %.0f A at 20 mV/A from 1.25 V), so it is "
              "caught by the idle null check (> %.0f A rejected) and, while switching, by the cell's own OC trip"
              % (rc["both open"], bl[1], rc["P open"], rc["N open"], -CELL_AN1_CM[0] / CELL_AN1_V_PER_A,
                 (CELL_AN1_CM[0] - 3.3495) / CELL_AN1_V_PER_A, CELL_NULL_A))
    for n in range(1, 5):
        trips["C%d_AN1" % n] = ("OC backup +/-%.1f A (CMPSS window): %.1f-%.1f A | both lines open reads <= %.0f A "
                                "(trips); one line open reads <= %.0f A (idle null check, > %.0f A rejected)"
                                % (CELL_OC_TRIP_A, bl[0], bl[1], rc["both open"], single, CELL_NULL_A))
    return ok, trips


def check_efuses():
    """CSR-03 (cell start-up), INT-06 / CSR-12 (port branch) against the SYS-IO-AUX eFuse limits."""
    c, p = efuse(CELL_EFUSE, IF.CELL_24V_MAX_UF * 1e-6), efuse(PORT_EFUSE, PORT_BOARD_UF * 1e-6)
    i1 = IF.CELL_24V_MAX_A + c["inrush"]
    hot = all(pd <= FIG55_85C[0] and 1.3 * t <= FIG55_85C[1] for pd, t in c["pd_t"] + p["pd_t"])
    ok = say(i1 < c["icb"][0] and 4 * i1 < SYS_GD_MIN_A and hot, "CSR-03 cell eFuse start-up (TPS26600 %s, %s)"
             % CELL_EFUSE, "%d uF + %.1f A: %.3f A < I(CB) min %.3f A per port, x4 = %.2f A < %.2f A (SYS +24V_GD); "
             "<= %.1f W for <= %.2f s, x1.3 within %.2f s at %.0f W (fig. 55, 85 C, graph reading)"
             % (IF.CELL_24V_MAX_UF, IF.CELL_24V_MAX_A, i1, c["icb"][0], 4 * i1, SYS_GD_MIN_A, c["pd"],
                c["t_ramp"][1], FIG55_85C[1], FIG55_85C[0]))
    pd = PORT_24V_DEMAND_A ** 2 * TPS2660_RON
    ok &= say(PORT_MARGIN * PORT_24V_DEMAND_A <= p["icb"][0] and CTRL_24V_LOAD_A + p["icb"][1] < SYS_24V_MIN_A
              and pd <= 0.5, "INT-06 port eFuse (TPS26600 %s, %s)" % PORT_EFUSE,
              "port board %.1f A for 200 s: I(CB) min %.3f A = x%.2f (>= x%.1f); %.2f W in RON 250 mR max, below the "
              "breaker so no timer runs; card %.2f A + I(CB) max %.3f A = %.2f A < %.2f A (SYS +24V)"
              % (PORT_24V_DEMAND_A, p["icb"][0], p["icb"][0] / PORT_24V_DEMAND_A, PORT_MARGIN, pd, CTRL_24V_LOAD_A,
                 p["icb"][1], CTRL_24V_LOAD_A + p["icb"][1], SYS_24V_MIN_A))
    return ok


def check_rdy():
    """RDY into 100k to GND, 1k / 1 nF, then a GPIO and a 74LVC11A input (VIH 2.0 V, VIL 0.8 V: SPRSP14E 7.9, SCLS993A).
    Sources: push-pull LVC1G17 on the board's 3.3 V -5 % (VOH >= VCC - 0.1 V at 100 uA, SCES351), or open drain with
    a pull-up up to 4.99k 1 % (VOL <= 0.4 V); unplugged or unpowered (Ioff, dead pull-up rail): only the 100k."""
    vcc, i = 3.3 * 0.95, 3.3 * 1.05 / 100e3 * 0.99
    pp = vcc - 0.1 - 1e3 * i                                        # at RDY_F, after the 1k
    od = vcc * 100e3 * 0.99 / (100e3 * 0.99 + 4.99e3 * 1.01) * (1 - 1e3 / 100e3)
    return say(min(pp, od) >= 2.0 and 0.4 + 1e3 * i <= 0.8, "INT-14 RDY levels at the GPIO / AND input",
               "high: push-pull %.2f V, open drain (4.99k) %.2f V >= 2.0 V; low <= %.2f V <= 0.8 V; unplugged or "
               "unpowered: 0 V (100k) = not ready" % (pp, od, 0.4 + 1e3 * i))


# ---- board ID decode (rev D item 4): every ID input is 10.0k (0.1 %, 10 ppm/K) from VBIAS = the ADC reference
ID_SEP = 8                          # counts between adjacent worst-case bands (threshold mid-gap: >= 4 counts each side)
ID_TOL = 0.001 + 25e-6 * 80         # board-side 0.1 % ID / readback resistors; TCR not given there: 25 ppm/K ASSUMED
ID_TOL_PLAIN = 0.01                 # a plain ID resistor (no readback): any 1 % part
RB_VOH = (3.315 * 0.98 - 0.1, 3.315 * 1.02)   # readback outputs high: port +3V3S (LMR36015) +/-2 %, VOH drop 0.1 V
GND_SHIFT = {"port": PORT_24V_DEMAND_A * 0.021, "cell": 1.0 * 0.018}   # board GND above CTRL AGND: board current
#   x ribbon ground
#   (7 / 8 conductors of 0.5 m 28 AWG + 2 IDC contacts = 21 / 18 mOhm; port board at its hold load in every state) ASSUMED
DAB_CHECK = os.path.join(L.REPO, "hardware/DAB60/outputs/DAB60_design_check.txt")
# DAB60-PORT ID + readback agreed for its next revision (R_id, R_A x2, R_B x2); PV-PORT rev 4 already draws
# 28.0k || 118k x2 || 42.2k x2 (read from port_spec.json, never typed here)
RB_PROPOSAL = {"DAB60-PORT": (3.32e3, 76.8e3, 26.7e3)}


def id_counts(r_id, outs, gnd, tol):
    """Worst-case (lo, hi) ADC counts of an ID input: 10.0k from VBIAS (= VREF, so ratiometric) against r_id to the
    board GND (gnd above AGND) and readback outputs [(R, state)], state 1 = high (RB_VOH), 0 = low, None = unpowered."""
    cs = []
    for s, voh, vr, vg in product(product((-1, 1), repeat=4), RB_VOH, (2.5 * 0.9994, 2.5 * 1.0006), (0.0, gnd)):
        gpu, gid = 1 / (10.0e3 * (1 + s[0] * P01)), 1 / (r_id * (1 + s[1] * tol))
        g = [(1 / (r * (1 + s[2 + (i % 2)] * tol)), st) for i, (r, st) in enumerate(outs)]
        num = gpu * vr + vg * gid + sum(gi * (voh + vg if st else vg) for gi, st in g if st is not None)
        cs.append(4096 * num / (gpu + gid + sum(gi for gi, st in g if st is not None)) / vr)
    e = 4 + 5 * max(cs) / 4096 + 2 + 0.5                       # SPRSP14E 7.11.2.3.6: offset, gain, INL, quantisation
    return min(cs) - e, max(cs) + e


def port_states(r_id, r_a, r_b):
    """{state: band} of a port board: RB1 + RB2 of port A (through r_a) and port B (r_b), 9 sums, plus unpowered."""
    out = {"unpowered": id_counts(r_id, [(r_a, None), (r_b, None)] * 2, GND_SHIFT["port"], ID_TOL)}
    for na, nb in product(range(3), repeat=2):
        outs = [(r_a, int(na > 0)), (r_b, int(nb > 0)), (r_a, int(na > 1)), (r_b, int(nb > 1))]
        out["A%d B%d" % (na, nb)] = id_counts(r_id, outs, GND_SHIFT["port"], ID_TOL)
    return out


def gaps(bands):
    """Sorted [(name, (lo, hi))] and the smallest gap between neighbours, counts."""
    s = sorted(bands.items(), key=lambda x: x[1][0])
    return s, min(b[1][0] - a[1][1] for a, b in zip(s, s[1:]))


def check_id():
    """Board-ID decode tables, one per ID input. P_ID (26-way PORT: port boards only): every RB1 + RB2 state of both
    port codes, each board unpowered (RB outputs Ioff = open), no board (VBIAS) and a short, from the boards' own
    resistors - PV-PORT from sim/out/port_design/port_spec.json (what gen/port.py draws), DAB60-PORT the values agreed
    for its next revision (RB_PROPOSAL). MUX_OUT: the plain cell IDs. Every band must clear every other by ID_SEP.
    An unpowered port board sits inside the PV-PORT window, so the P_ID PPB cannot trip on it: in hardware that rests
    on VA/VB reading below the CMPSS low band (INT-07), asserted here as well. Returns (ok, pin-plan text)."""
    ps = json.load(open(PORT_SPEC))
    rb = ps["readback"]
    port = {"PV-PORT": (rb["R_id_ohm"], rb["R_rb_A_ohm"], rb["R_rb_B_ohm"]), "DAB60-PORT": RB_PROPOSAL["DAB60-PORT"]}
    dab = tuple(val(x) for x in re.search(r"DAB60-PORT ID \+ readback \((\S+) \|\| (\S+) x2 \|\| (\S+) x2\)",
                                          open(DAB_CHECK).read()).groups())
    if dab != port["DAB60-PORT"]:
        print("[INFO] DAB60-PORT build still draws %s || %s x2 || %s x2; the decode uses the agreed next-revision values"
              % tuple("%.3gk" % (x / 1e3) for x in dab))
    ends = {"short": id_counts(1e-3, [], GND_SHIFT["port"], ID_TOL), "open / no board": (4096 - 11.5, 4095.0)}
    tables = {"P_ID": dict(ends), "MUX_OUT": dict(ends, short=id_counts(1e-3, [], GND_SHIFT["cell"], ID_TOL))}
    for c, n in port.items():
        tables["P_ID"].update({"%s %s" % (c, k): v for k, v in port_states(*n).items()})
    for c in ("PVCELL-25", "DAB60-B1", "DAB60-B2"):
        tables["MUX_OUT"][c] = id_counts(val(IF.ID_OHM[c]), [], GND_SHIFT["cell"], ID_TOL_PLAIN)
    ok, text = True, {}
    for net, t in tables.items():
        order, g = gaps(t)
        ok &= say(g >= ID_SEP, "ID decode %s (%d bands)" % (net, len(order)), "worst gap %.0f counts (>= %d) between "
                  "any two bands%s" % (g, ID_SEP, ", both port codes, their unpowered boards, open and short"
                                       if net == "P_ID" else ", cell codes, open and short"))
        text[net] = ("ID decode, ADC counts at 12 bit, worst case (pull-up, reference, ADC, board resistors, ribbon "
                     "ground); band < threshold < next band: " +
                     "".join("%s %.0f-%.0f%s" % (n, b[0], b[1], " < %.0f < " % ((b[1] + q[1][0]) / 2) if q else "")
                             for (n, b), q in zip(order, order[1:] + [None])))
    gp = val(AFE_PORT[0]) / 10e3
    kv = ps["divider"]["ratio"] * 2.0 * gp
    unp, edge = open_reading(gp, kv)["unpowered"], -band(OPEN_V, kv, "CMPSS", PORT_V_CHAIN)[1]
    ok &= say(unp < edge, "ID decode P_ID: unpowered port board in hardware",
              "inside the PV-PORT PPB window, so it trips on VA/VB instead: unpowered reads <= %.0f V < %.1f V "
              "(CMPSS low band, INT-07); firmware also decodes it from the table" % (unp, edge))
    text["P_ID"] += (" | PPB2 window = the fitted code's lowest..highest band: open, short or a foreign code trips; an "
                     "unpowered board is decoded by firmware and trips in hardware on VA/VB (INT-07)")
    return ok, text


# ---- DAB60 rev B bridges on C1/C2 (rev D item 6); figures read from its build output and sim/out/dab_design
DAB_SPEC = os.path.join(L.REPO, "sim/out/dab_design/dab_spec.json")
# HOB 130-P sensitivity 0.75 % + 200 ppm/K x 80 K (HOB-P_series.pdf p9), AMC3330 EG 0.2 % + 45 ppm/K x 80 K
# (SBASA34B 7.10), DAB difference amp 4 x 0.1 %
DAB_I_CHAIN = 0.0075 + 200e-6 * 80 + 0.002 + 45e-6 * 80 + 4 * 0.001
DAB_I_OFFS_A = 0.075e-3 * 80 / 8e-3 + 0.8   # after the idle null: HOB TCUOE drift + magnetic offset IOM (p9), 8 mV/A


def check_dab():
    """DAB60 rev B as the CTRL sees it: AN1 open line beyond the CMPSS window (dab_spec I_xfmr_oc_trip_A, the
    highest DAC setting), AN2/AN3 inside the single-ended range, +24V start-up against the cell eFuse."""
    t = open(DAB_CHECK).read()
    mv = float(re.search(r"AN1 \(item 3b\): ([\d.]+) mV/A differential", t).group(1))
    an = max(float(x) for x in re.search(r"AN2 <= ([\d.]+) V, AN3 <= ([\d.]+) V single-ended", t).groups())
    i24, c24 = (float(x) for x in re.search(r"([\d.]+) A bound .*?input capacitance ([\d.]+) uF", t).groups())
    oc = json.load(open(DAB_SPEC))["protection"]["I_xfmr_oc_trip_A"]
    g = val(AFE_CELL[0]) / 10e3
    b, r = band(oc, mv * 1e-3 * g, "CMPSS", DAB_I_CHAIN, DAB_I_OFFS_A), open_reading(g, mv * 1e-3 * g)
    ok = say(max(r["P open"], r["N open"]) < -b[1], "DAB60 bridge AN1 open line (HOB 130-P + AMC3330, %.3f mV/A)" % mv,
             "CMPSS window +/-%.0f A -> %.1f-%.1f A; open P reads %.0f A, open N %.0f A, both %.0f A: all beyond, so an "
             "open wire trips" % (oc, b[0], b[1], r["P open"], r["N open"], r["both open"]))
    v_adc = 1.25 + val(AFE_CELL[0]) / (10.0e3 + 49.9) * an                  # 49.9 R source adds to the 10.0k
    ok &= say(v_adc <= 2.45, "DAB60 bridge AN2/AN3 (single-ended, 49.9 R)", "<= %.2f V -> ADC <= %.3f V of 2.5 V "
              "(0-3 V range of the dual-range AFE, gain %.4f)" % (an, v_adc, val(AFE_CELL[0]) / (10.0e3 + 49.9)))
    e = efuse(CELL_EFUSE, c24 * 1e-6, r_load=24.0 / i24)
    ok &= say(i24 + e["inrush"] < e["icb"][0] and all(p <= FIG55_85C[0] and 1.3 * s <= FIG55_85C[1] for p, s in e["pd_t"]),
              "DAB60 bridge +24V start-up (cell eFuse %s, %s)" % CELL_EFUSE,
              "%.2f A bound + %.0f uF x %.2f V/ms = %.3f A < I(CB) min %.3f A; start-up <= %.1f W" % (
                  i24, c24, e["slope"][1] / 1e3, i24 + e["inrush"], e["icb"][0], e["pd"]))
    return ok


def check_vref(B):
    """INT-05: VREF2V5 now also feeds VDAC. Capacitance at +tolerance within REF5025E CL 1-100 uF (SBOS410O), load
    within +/-10 mA: VDAC 6 k per active CMPSS (SPRSP14E 7.11.3.1.3) + VREFHI 190 uA per ADC + VMID divider."""
    c = c_max = 0.0
    for ref, p in B.D.parts.items():
        if p.lib_id.endswith(":C") and "VREF2V5" in p.pins.values():
            tol = re.search(r" (\d+)% ", B.bom[ref]["desc"])
            c += val(p.value)
            c_max += val(p.value) * (1 + (int(tol.group(1)) if tol else 20) / 100)
    load = 8 * 2.5 / 6e3 + 4 * 190e-6 + 2.5 / 20e3
    return say(1e-6 <= c and c_max <= 100e-6 and load <= 10e-3, "INT-05 VREF2V5 (ADC + CMPSS DAC reference)",
               "%.1f uF, %.1f uF at tolerance <= 100 uF; load %.1f mA <= 10 mA" % (c * 1e6, c_max * 1e6, load * 1e3))


if __name__ == "__main__":
    B, pins, P = build_design()
    ok_trips, trips = check_trips()
    ok_id, ids = check_id()
    write_plan(P, pins, B, dict(trips, **ids))
    if not all([check_supervisor(), ok_trips, ok_id, check_efuses(), check_dab(), check_rdy(), check_vref(B),
                check_gd(B)]):
        sys.exit(1)
    sys.exit(L.build(B))
