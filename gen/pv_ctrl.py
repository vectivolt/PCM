"""PV-CTL - cost-first control board of PV-P75 / PV-P100 / PV-P110 (decision D-044,
docs/requirements/ARCHITECTURE-COSTFIRST.md sections 0, 1.2, 2, 5, 6, 7, 10).

  LIVE zone (GND = AGND = BUS-, up to 1000 V above earth: hazardous live)
    MCU      TI F280039CSPZR (100-pin PZ). Pin table read from SPRSP61C on every build: Figure 5-1 (pin -> name) is
             cross-checked against Table 5-1 (GPIO mux positions, analog / CMPSS functions, power and test pins).
             Pin plan: gen/data/pv_ctrl_pin_plan.csv (written from the same table that wires the symbol).
    supply   VDDIO / VDDA from the power board's +3V3 (PC connector, so the power board's 3.3 V signals and this MCU
             share one rail and one power-up order); VDD from the internal VREG; TPS3828-33 reset + 1.6 s watchdog.
    analog   one REF3030E 3.0 V (from +3V3A) = VREFHI = VDAC = NTC bias = trip ladders (ratiometric); IL1-4 (5 V
             STK-HO/A 75 signals, 2.5 V + 10.667 mV/A) divided to ILn_P and buffered (TLV9064, offset from VREF); the
             power board's VMID-centred signals straight into the ADC through 100 R / 1 nF.
    trips    7 x TLV9024 on +3V3 (open drain): IL windows (+/-74 A, one ladder per phase from that sensor's own Uref,
             ILnR on PC), VA / VB OV (1075 V; this and the rest from VREF ladders), NTC
             OT (heatsink 87.9 C, inductor 150 C) and open probe; the phase-4 pair is an assembly option (not fitted
             on PV-P75, where IL4 = 0 V and NTC4 / NTC8 are open). Port over-current (387-413 A) comes from the
             power board on FLT_N; CMPSS1-4 (IL) and CMPSS4 / PPB (IB) are firmware-set backups. One latch
             (74LVC1G74, set-dominant) set by any source through a 74LVC07 wired-AND (also FLT_N, RDY, ENABLE,
             74LVC1G123 heartbeat monoflop cleared by XRSn), cleared only by a firmware CLK edge while no source is
             active. 6 x 74LVC08 gate the 16 PWM lines, EN, K_PRE and STATUS with HEALTHY; K_A / K_B with HEALTHY OR
             HOLD (a trip never commands a held contactor open); fan PWM with BIAS_EN. +24V / +5V of PC unused.
  barrier B1 (reinforced): CA-IS3050W (CAN, 12.8 kV), CA-IS3082WNX (RS-485), 2 x CA-IS3821LG + CA-IS3820LG (ENABLE
             in, STATUS out, fan speed out, 3 tachs in), CA-IS3842HW (Ethernet UART, DI1). Conformal coating PD1 over the
             B1 zone is mandatory.
  SELV zone  SELV 12-36 V in (AUX-T1 reinforced winding), TPS54360B 5 V and TPS54360B fan supply (7-24 V, set by the
             isolated PWM), 3 fan headers, CAN / RS-485 field protection, stop (chainable) / ready / DI1 / relay terminals,
             Ethernet bridge option (sheet NN_eth).
Nothing here is bench-validated: every number in design_check() is calculated.
Usage: .venv/bin/python gen/pv_ctrl.py
Rev A1 (2026-10-05, D-056): IL trip thresholds from each sensor's Uref (ILnR, PV-PWR rev A2): 68.9-79.9 A, was 66.2-79.9 A.
PCM review (same revision letter, values re-calculated): every TLV9024 band now carries the common-mode error of its comparator (CMRR >= 50
dB at 3.3 V, risk C9) and the data-sheet propagation delay at the overdrive the ramp builds: IL window 67.9-80.5 A (the 68.5 A floor is
NOT met: see the [OPEN] item of the design check), OV ladder 2.05k / 11.3k (1074 V nominal, 1039-1110 V); fan supply contract with PV-PWR;
firmware table row for the fans' cold-start rule (module_spec.json).
Rev A2 (2026-10-06, Megarevo PMA parity): (1) Ethernet for the EMS (Modbus TCP) on the SELV side: WCH CH9121T UART-to-Ethernet
bridge (10/100, MAC + PHY + TCP/IP) + HanRun HR913550AE RJ45 (magnetics, LEDs, -40..85 C) + SRV05-4 + TPS7A2033 3.3 V; the UART
crosses B1 on a CA-IS3842HW (4-ch, VIOSM 8 kV) on SCIB (GPIO56/57). FITTED on the PCS variants, NOT FITTED (footprints only) on
PV-CTL / PV-CTL-P75 (variant from PROJECT). The W5500 alternative is costed in the design check. (2) Recorder flash GD25Q32E 4 MB
on SPIA (GPIO18/54/55, CS GPIO37) replaces the I2C EEPROM (fault records, two firmware images, calibration). Pins: cJTAG only
(TDI/TDO = GPIO35/37 as GPIO), TACH1/2 to GPIO35/34, the run LED on WD_OK (no GPIO), the MCU reads PRE_N instead of OC_N and
OVT_N (GPIO17; GPIO52 = DI1). (3) SELV I/O: stop input with loop-through (chain), ready permissive in series with it, DI1
(battery fault / BMS contact, 5 V wetting) through the CA-IS3842HW, changeover relay (ready / fault) on the STATUS channel.
(4) Altitude: B1 isolator ratings parsed from the data sheets; the 15 mm (WW) set costs more than 1 USD per board: 2000 m kept.
"""
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict

import catalog
import dcdclib as L
import interfaces as IF
import port
import sys_io_aux

PROJECT, REV, DATE = "PV-CTL", "A2", "2026-10-06"
HERE = os.path.dirname(os.path.abspath(__file__))
MCU_DS = "docs/datasheets/controllers/TMS320F28003x.pdf"
PLAN_CSV = os.path.join(HERE, "data", "pv_ctrl_pin_plan.csv")
DS = "docs/datasheets/"
TI = "Texas Instruments"
CA = "Chipanalog"


# ============================================================================ 1. F280039C PZ pin table (SPRSP61C)
def _pdf(*args):
    return subprocess.run(["pdftotext"] + list(args) + [os.path.join(L.REPO, MCU_DS), "-"], capture_output=True,
                          text=True, check=True).stdout


def extract_pins():
    """{pin: {"label", "names", "mux": {fn: pos}, "ana": set, "type"}} from SPRSP61C.
    Figure 5-1 (p.11, raw text) gives pin -> label; Table 5-1 (layout text) gives each GPIO's PZ pin and mux table,
    each analog group's ADC / CMPSS / AIO names and the power / test pins. Every pair of sources must agree."""
    raw = _pdf("-f", "11", "-l", "11", "-raw")
    s = raw.index("same pinout.", raw.index("Figure 5-5 shows")) + len("same pinout.")
    tok, fig, i = raw[s:raw.index("A. Only the GPIO")].split(), {}, 0
    while i < len(tok) - 1:
        if tok[i].isdigit() and not tok[i + 1].isdigit():
            fig[int(tok[i])] = tok[i + 1]
            i += 2
        else:
            i += 1
    assert sorted(fig) == list(range(1, 101)), "Figure 5-1: pins missing"
    T = _pdf("-layout").split("\n")
    a = next(i for i, l in enumerate(T) if l.strip() == "Table 5-1. Pin Attributes")
    an = next(i for i in range(a, len(T)) if T[i].strip() == "ANALOG")
    g = next(i for i in range(an, len(T)) if T[i].strip() == "GPIO")
    t = next(i for i in range(g, len(T)) if T[i].strip() == "TEST, JTAG, AND RESET")
    z = next(i for i in range(t, len(T)) if T[i].startswith("5.3 Signal Descriptions"))
    # ---- GPIO section: PZ column located per page from the header ("100 ... PIN", next line "80 PN 64 PMQ ...")
    gpio, cur, cols = {}, None, None
    for i in range(g, t):
        l = T[i]
        if "Table 5-1" in l or "SIGNAL NAME" in l:
            continue
        m = re.search(r"\b100\b", l)
        if m and "PIN" in l:
            h = re.search(r"(80) PN\s+(64) PMQ\s+(64) PM\s+(48) PT", T[i + 1])
            cols = dict(PZ=m.start() + 1.5, **{k: h.start(j) + 1 for k, j in (("PN", 1), ("PMQ", 2), ("PM", 3), ("PT", 4))})
            continue
        m = re.match(r"^ (GPIO\d+|AIO\d+)\s+0, 4, 8, 12\s", l)
        if m:
            cur = gpio.setdefault(m.group(1), {"mux": {}, "pz": []})
        m2 = re.match(r"^ (\S+)\s+(\d+(?:, \d+)*)(?=\s)", l)
        skip = m2.span(2) if m2 else (0, 0)
        if m2 and cur is not None and not m:
            cur["mux"][m2.group(1)] = m2.group(2).replace(" ", "")
        if cur is not None and cols:
            for x in re.finditer(r"\b\d+\b", l):
                if skip[0] <= x.start() < skip[1] or x.start() > cols["PT"] + 6:
                    continue
                c = x.start() + len(x.group()) / 2
                if min(cols, key=lambda k: abs(cols[k] - c)) == "PZ" and abs(cols["PZ"] - c) <= 3:
                    cur["pz"].append(int(x.group()))
    assert all(len(d["pz"]) <= 1 for d in gpio.values()), "Table 5-1: a GPIO with two PZ pins"
    # ---- analog section: groups start at an ADC name; split when the next ADC name belongs to another pin
    lab = {p: set(v.replace("/", ",").split(",")) for p, v in fig.items()}
    adcn = re.compile(r"^[ABC]\d+$")
    pin_of = lambda n: {p for p in lab if n in lab[p]}           # noqa: E731
    groups, cur_g = [], None
    for l in T[an + 1:g]:
        m = re.match(r"^ ([A-Z][A-Z0-9_]+)\s", l)
        if not m or "Table" in l:
            continue
        nm = m.group(1)
        if adcn.match(nm) and (cur_g is None or not any(adcn.match(x) for x in cur_g[-1:])
                                or not (pin_of(nm) & set.intersection(*[pin_of(x) for x in cur_g if adcn.match(x)]))):
            cur_g = []
            groups.append(cur_g)
        if cur_g is not None:
            cur_g.append(nm)
    ana = defaultdict(set)
    for grp in groups:
        adcs = [n for n in grp if adcn.match(n)]
        if not adcs:
            continue
        pins = set.intersection(*[pin_of(n) for n in adcs])
        gp = [n for n in grp if n.startswith("GPIO")]
        if len(pins) > 1:                                 # B5 / B11: dedicated pin (AIO) or the AGPIO pin (GPIOnn)
            pins = {p for p in pins if any(x.startswith("GPIO") for x in lab[p]) == bool(gp)}
        if not pins:
            assert grp[0] == "A15", "analog group without a PZ pin: %s" % grp    # A15 is not bonded on PZ (Table 6-10)
            continue
        assert len(pins) == 1, "analog group maps to %s: %s" % (pins, grp)
        ana[pins.pop()].update(grp)
    # ---- power / test pins: every figure pin of the name must sit in that name's PZ block of Table 5-1
    blk = "\n".join(T[t:z])
    out = {}
    for p, label in fig.items():
        names = label.replace("/", ",").split(",")
        d = {"label": label.replace(",", "/"), "names": names, "mux": {}, "ana": ana.get(p, set())}
        gp = [n for n in names if n.startswith("GPIO")]
        if gp:
            e = gpio[gp[0]]
            assert e["pz"] == [p], "%s: figure pin %d, Table 5-1 %s" % (gp[0], p, e["pz"])
            d["mux"] = e["mux"]
            d["type"] = "b"
        elif adcn.match(names[0]) or names[0] in ("B3",):
            assert set(n for n in names if adcn.match(n)) <= d["ana"], "pin %d: %s not in Table 5-1" % (p, label)
            d["type"] = "i"
        else:
            nm = names[0]
            if nm in ("VREFHI", "VREFLO"):
                row = next(l for l in T[an:g] if re.match(r"^ %s\s+\d" % nm, l))
                assert str(p) in re.findall(r"\d+", row)[:2], "%s pin %d" % (nm, p)
            else:
                rows = [l for l in blk.split("\n") if re.match(r"^\s*%s(\s|$)" % nm, l)]
                assert rows, "%s missing from Table 5-1" % nm
                k = blk.split("\n").index(rows[0])
                near = " ".join(blk.split("\n")[k - 1:k + 2])
                assert re.search(r"\b%d\b" % p, near), "%s pin %d not in Table 5-1 (%s)" % (nm, p, near[:80])
            d["type"] = {"XRSn": "oc", "TCK": "i", "TMS": "b", "VREGENZ": "i", "VREFHI": "i",
                         "VREFLO": "i"}.get(nm, "pi")
        out[p] = d
    assert sum(1 for d in out.values() if any(n.startswith("GPIO") for n in d["names"])) == 55
    return out


def mcu_entry(pins):
    """Catalog entry: SUPPLY, ANALOG and two GPIO units; every pin exactly once."""
    def P(sel):
        return ["%d %s %s" % (p, d["label"], d["type"]) for p, d in sorted(pins.items()) if sel(d)]
    gp = [(int(re.search(r"GPIO(\d+)", d["label"]).group(1)), p) for p, d in pins.items() if "GPIO" in d["label"]]
    gp.sort()
    g = ["%d %s %s" % (p, pins[p]["label"], pins[p]["type"]) for _, p in gp]
    units = [dict(title="SUPPLY", left=P(lambda d: d["names"][0] in ("VDDIO", "VDDA", "VDD", "VREGENZ", "XRSn",
                                                                     "TCK", "TMS")),
                  right=P(lambda d: d["names"][0] in ("VSS", "VSSA"))),
             dict(title="ANALOG", min_width=40.64,
                  left=P(lambda d: d["type"] == "i" and d["names"][0][0] in "ABC" and d["names"][0] != "B3"),
                  right=P(lambda d: d["names"][0] in ("B3", "VREFHI", "VREFLO"))),
             dict(title="GPIO A", left=g[:14], right=g[14:28]), dict(title="GPIO B", left=g[28:42], right=g[42:])]
    used = [int(s.split()[0]) for u in units for side in ("left", "right") for s in u[side]]
    assert sorted(used) == list(range(1, 101)), "MCU symbol must carry every pin once: %s" % sorted(
        set(range(1, 101)) ^ set(used))
    return dict(mfr=TI, mpn="F280039CSPZR", prefix="U", pkg="LQFP-100 (PZ) 14x14 mm", ds=MCU_DS, units=units,
                desc="TMS320F280039C C28x + CLA 120 MHz, 384 kB flash, 16 ePWM, 3 x 12-bit ADC, 4 CMPSS, DCAN + "
                     "MCAN, 100-pin PZ, -40..125 C")


# ============================================================================ 2. catalog (pin tables cited per entry)
def ic(mfr, mpn, pkg, ds, desc, left, right, prefix="U"):
    return dict(mfr=mfr, mpn=mpn, prefix=prefix, pkg=pkg, ds=DS + ds, desc=desc, pins={"left": left, "right": right})


SIO, PRT = sys_io_aux.CATALOG, port.PARTS
PARTS = {k: SIO[k] for k in ("SN74LVC08A", "SN74LVC1G74", "2N7002BK", "LED_G", "LED_R", "MH", "TPS7A2033")}
PARTS.update({k: PRT[k] for k in ("REF3030E", "LVC1G32", "BAS16")})
PARTS.update({
    # CA-IS382x v1.06 section 6 pin figures (SOIC8-WB "G"): 3820 = 2 forward, 3821 = 1 forward + 1 reverse;
    # section 4 ordering table: suffix L = default output LOW when the input side is unpowered or the input open
    "CA_IS3821": ic(CA, "CA-IS3821LG", "SOIC-8 WB (G) 5.85x7.5 mm, 8 mm CLR/CPG", "isolation-interface/CA-IS382x.pdf",
                    "Reinforced 2-ch digital isolator 1 fwd + 1 rev, default LOW, 5.7 kVrms, VIOSM 8 kV",
                    ["1 VDDA pi", "2 VI1 i", "3 VO2 o", "4 GNDA pi"], ["8 VDDB pi", "7 VO1 o", "6 VI2 i", "5 GNDB pi"]),
    "CA_IS3820": ic(CA, "CA-IS3820LG", "SOIC-8 WB (G) 5.85x7.5 mm, 8 mm CLR/CPG", "isolation-interface/CA-IS382x.pdf",
                    "Reinforced 2-ch digital isolator 2 fwd, default LOW, 5.7 kVrms, VIOSM 8 kV",
                    ["1 VDDA pi", "2 VI1 i", "3 VI2 i", "4 GNDA pi"], ["8 VDDB pi", "7 VO1 o", "6 VO2 o", "5 GNDB pi"]),
    # CA-IS305x v1.10 table 6-1 (CA-IS3050W, SOIC16-WB): VCC1 2.5-5.5 V logic, VCC2 4.5-5.5 V bus side; NC 4, 5, 11, 14
    "CA_IS3050W": ic(CA, "CA-IS3050W", "SOIC-16 WB (W)", "isolation-interface/CA-IS305x.pdf",
                     "Isolated CAN transceiver 1 Mbps, 5 kVrms, VIMP 9.8 kV / VIOSM 12.8 kV, 3.3 V logic, 5 V bus side",
                     ["1 VCC1 pi", "6 TXD i", "3 RXD o", "4 NC nc", "5 NC nc", "2 GND1 pi", "7 GND1 pi", "8 GND1 pi"],
                     ["16 VCC2 pi", "13 CANH b", "12 CANL b", "11 NC nc", "14 NC nc", "9 GND2 pi", "10 GND2 pi",
                      "15 GND2 pi"]),
    # CA-IS308x v1.10 table 6-3 (CA-IS3082WNX): pin 7 is listed both as GNDA and as NC -> tied to GNDA (safe for both)
    "CA_IS3082WNX": ic(CA, "CA-IS3082WNX", "SOIC-16 WB (W)", "isolation-interface/CA-IS308x.pdf",
                       "Isolated half-duplex RS-485 transceiver 500 kbps, 5 kVrms, VIOSM 8 kV, fail-safe receiver",
                       ["1 VDDA pi", "6 DI i", "5 DE i", "4 RE i", "3 RO o", "2 GNDA pi", "7 GNDA/NC p", "8 GNDA pi"],
                       ["16 VDDB pi", "12 A b", "13 B b", "10 NC nc", "11 NC nc", "14 NC nc", "9 GNDB pi",
                        "15 GNDB pi"]),
    # TLV902x SNOSDA3H table 4-3 (TLV90x4, D/PW/DYY figure 4-5): IN1-/IN1+ (4/5) -> OUT1 (2), IN2 (6/7) -> OUT2 (1),
    # IN3 (8/9) -> OUT3 (14), IN4 (10/11) -> OUT4 (13); TLV9024 = open-drain outputs; inputs to (V+) + 0.2 V
    "TLV9024": ic(TI, "TLV9024PWR", "TSSOP-14 (PW)", "sensing/TLV9024.pdf",
                  "Quad comparator, open drain, rail-to-rail input, VOS +/-2 mV (-40..125 C), 30 uA/ch, 1.65-5.5 V",
                  ["5 IN1+ i", "4 IN1- i", "7 IN2+ i", "6 IN2- i", "9 IN3+ i", "8 IN3- i", "11 IN4+ i", "10 IN4- i"],
                  ["2 OUT1 oc", "1 OUT2 oc", "14 OUT3 oc", "13 OUT4 oc", None, "3 V+ pi", "12 V- pi"]),
    # TLV906xS SBOS839N table 5-5 (TLV9064, TSSOP column)
    "TLV9064": ic(TI, "TLV9064IPWR", "TSSOP-14 (PW)", "sensing/TLV9064.pdf",
                  "Quad RRIO CMOS op amp 10 MHz, 1.8-5.5 V", ["3 IN1+ i", "2 IN1- i", "5 IN2+ i", "6 IN2- i",
                                                              "10 IN3+ i", "9 IN3- i", "12 IN4+ i", "13 IN4- i"],
                  ["1 OUT1 o", "7 OUT2 o", "8 OUT3 o", "14 OUT4 o", None, "4 V+ pi", "11 V- pi"]),
    # SN74LVC07A SCAS595W pin functions (D/DB/NS/PW)
    "LVC07": ic(TI, "SN74LVC07APWR", "TSSOP-14 (PW)", "isolation-interface/SN74LVC07A.pdf",
                "Hex buffer, open-drain outputs, 5 V tolerant inputs, Ioff (trip wired-AND)",
                ["1 1A i", "3 2A i", "5 3A i", "9 4A i", "11 5A i", "13 6A i", None, "14 VCC pi", "7 GND pi"],
                ["2 1Y oc", "4 2Y oc", "6 3Y oc", "8 4Y oc", "10 5Y oc", "12 6Y oc"]),
    # SN74LVC1G123 SCES586E table 4-1 (DCU): Cext only to the capacitor, Rext/Cext to the capacitor and resistor
    "LVC1G123": ic(TI, "SN74LVC1G123DCUR", "VSSOP-8 (DCU)", "isolation-interface/SN74LVC1G123.pdf",
                   "Retriggerable monostable, Schmitt inputs (heartbeat watchdog)",
                   ["1 A i", "2 B i", "3 ~{CLR} i", "8 VCC pi", "4 GND pi"], ["5 Q o", "6 Cext p", "7 Rext/Cext p"]),
    # TPS382x SBVS..., table 5-1 (TPS3828, DBV): open-drain RESET, MR may float, WDI floating = internal pulses
    "TPS3828": ic(TI, "TPS3828-33DBVR", "SOT-23-5 (DBV)", "power-supply/TPS3823.pdf",
                  "Supervisor 2.93 V + watchdog 1.6 s, open-drain reset 200 ms", ["5 VDD pi", "4 WDI i", "3 ~{MR} i"],
                  ["1 ~{RESET} oc", "2 GND pi"]),
    # REF30 SBVS032K section 5 (DBZ): 1 IN, 2 OUT, 3 GND
    "REF3030": ic(TI, "REF3030AIDBZR", "SOT-23-3 (DBZ)", "sensing/REF3030.pdf",
                  "Voltage reference 3.0 V 0.2 % 75 ppm/K, 25 mA, VIN >= VOUT + 50 mV (ADC / CMPSS / NTC reference)",
                  ["1 IN pi", "3 GND pi"], ["2 OUT po"]),
    # GigaDevice GD25Q32E rev 1.1 table 1 (SOP8 208 mil, p.6): 1 CS#, 2 SO (IO1), 3 WP# (IO2), 4 VSS, 5 SI (IO0), 6 SCLK,
    # 7 HOLD# (IO3), 8 VCC; QE = 0 as shipped (WP# and HOLD# active, 'QE bit'); grade I = -40..85 C (rev A2: replaces the I2C EEPROM)
    "GD25Q32E": ic("GigaDevice", "GD25Q32ESIGR", "SOP-8 208 mil", "controllers/GD25Q32E.pdf",
                   "SPI NOR flash 32 Mbit (4 MB), 133 MHz, 100k cycles, 20 y retention, -40..85 C (fault recorder, images)",
                   ["8 VCC pi", "1 CS# i", "6 SCLK i", "5 SI b", "4 VSS pi"], ["2 SO b", "3 WP# b", "7 HOLD# b"]),
    # CA-IS384x v1.05 table 6-1 (SOIC16 WB and WWB, one pinout): CA-IS3842 = 2 forward (A -> B: VI1 / VI2 -> VO1 / VO2) + 2 reverse
    # (B -> A: VI3 / VI4 -> VO3 / VO4); ENA / ENB output enables pulled up inside (tied high here); suffix H = default output HIGH
    "CA_IS3842": ic(CA, "CA-IS3842HW", "SOIC-16 WB (W) 10.3x7.5 mm, 8 mm CLR/CPG", "isolation-interface/CA-IS384x.pdf",
                    "Reinforced 4-ch digital isolator 2 fwd + 2 rev, default HIGH, 5.7 kVrms, VIOSM 8 kV",
                    ["1 VDDA pi", "3 VI1 i", "4 VI2 i", "5 VO3 o", "6 VO4 o", "7 ENA i", "2 GNDA pi", "8 GNDA pi"],
                    ["16 VDDB pi", "14 VO1 o", "13 VO2 o", "12 VI3 i", "11 VI4 i", "10 ENB i", "9 GNDB pi", "15 GNDB pi"]),
    # WCH CH9121 data sheet V2.6 table 4-1 (CH9121T, TSSOP20) and figure 9-1 (reference circuit): 50 ohm MDI termination and the
    # load capacitors of a 12 pF crystal are inside; the magnetics' centre taps go to GND through 1 uF, never to a supply
    "CH9121T": ic("WCH (Nanjing Qinheng)", "CH9121T", "TSSOP-20", "isolation-interface/WCH-CH9121.pdf",
                  "Ethernet-UART transparent bridge, 10/100 MAC + PHY + TCP/IP, 2 UART, VCC33 3.2-3.4 V, -40..85 C",
                  ["18 VCC33 pi", "8 VCCIO pi", "3 VDDK p", "4 XI i", "5 XO o", "13 RXD1 i", "14 TXD1 o", "15 CTS i",
                   "16 TNOW1/RTS o", "11 CFG i", "17 GND pi"],
                  ["1 TXP b", "2 TXN b", "19 RXP b", "20 RXN b", "6 LINK/RESET b", "7 ACT o", "9 TXD2 o", "10 RXD2 i",
                   "12 TNOW2 o"]),
    # HanRun HR913550AE (rev 00, image-only, 4 pp): p.1 schematic - 1 TD+, 2 TD-, 3 RD+, 6 RD-, 4 / 5 the TX / RX centre taps (printed
    # 'P4' / 'P5' only), 7 NC, 8 CHS GND (4 x 75 R Bob-Smith + 1000 pF / 2 kV inside), LEDs 9 -> 10 green, 12 -> 11 yellow (anode
    # first); p.3 PCB layout: two shield legs, unnumbered (drawn as SH); p.4 -40..+85 C; isolation 2250 V DC UTP side to chip side
    "HR913550AE": ic("HanRun (Zhongshan HanRun Elec)", "HR913550AE", "RJ45 THT right angle, 1:1 magnetics, 2 LEDs",
                     "connectors/HanRun-HR913550AE.pdf",
                     "RJ45 10/100BASE-T, integrated magnetics + Bob-Smith, green / yellow LED, -40..+85 C, 2250 V DC",
                     ["1 TD+ p", "2 TD- p", "4 P4 p", "3 RD+ p", "6 RD- p", "5 P5 p", "7 NC nc", "8 CHS_GND p"],
                     ["9 LEDG_A p", "10 LEDG_K p", "12 LEDY_A p", "11 LEDY_K p", None, "SH SHIELD p"], prefix="J"),
    # MSKSEMI SRV05-4 p.1 'Pin Configuration and Functions' (SOT-23-6): 1 IO1, 2 GND, 3 IO2, 4 IO3, 5 Vcc, 6 IO4; CJ 0.6-0.8 pF
    "SRV05": ic("MSKSEMI", "SRV05-4", "SOT-23-6", "protection/MSKSEMI-SRV05-4.pdf",
                "ESD array 4 lines + rail, VRWM 5 V, CJ <= 0.8 pF, IEC 61000-4-2 +/-12 kV contact (Ethernet MDI, chip side)",
                ["1 IO1 p", "3 IO2 p", "4 IO3 p", "6 IO4 p"], ["5 Vcc p", "2 GND p"], prefix="D"),
    # YXC YSX321SL family sheet (X322525MOB4SI = 25 MHz, 12 pF, +/-10 ppm, +/-20 ppm over -40..85 C, LCSC C9006): p.1 'Top View
    # Crystal Connection': 1 and 3 crystal, 2 and 4 GND (KiCad Crystal_GND24)
    "XTAL25": dict(mfr="YXC Crystal Oscillators", mpn="X322525MOB4SI", prefix="Y", pkg="SMD 3225 4-pad",
                   stock=("Device", "Crystal_GND24"), ds=DS + "timing/YXC-X322525MOB4SI.pdf",
                   desc="Crystal 25 MHz 12 pF +/-10 ppm, +/-20 ppm -40..85 C, ESR <= 50 ohm (CH9121T)"),
    # Hongfa HFD27 (2023 rev 1.00) p.3 wiring diagram (bottom view): coil 1-16; pole 1 COM 4 / NC 6 / NO 8; pole 2 COM 13 / NC 11 /
    # NO 9; p.2 sensitive type 005-S: 5 V, 125 ohm +/-10 %, pick-up <= 3.75 V, drop-out >= 0.5 V at 23 C; p.1 -40..85 C, 2 A 30 V DC
    "HFD27": ic("Hongfa", "HFD27/005-S", "DIP-16 relay 20.2x10.2 mm", "protection/Hongfa-HFD27.pdf",
                "Signal relay 2 Form C, 5 V 200 mW coil (125 ohm), 2 A 30 V DC / 1 A 125 V AC, 1500 V AC coil-contact, -40..85 C",
                ["1 COIL1 p", "16 COIL2 p"], ["4 COM1 p", "6 NC1 p", "8 NO1 p", "13 COM2 p", "11 NC2 p", "9 NO2 p"],
                prefix="K"),
    # Epson SG-210STF 25.000000 MHz L (X1G0041710033xx) terminal table: 1 /ST, 2 GND, 3 OUT, 4 VCC; +/-50 ppm all-in
    "OSC25": ic("Seiko Epson", "X1G0041710033", "SMD 2.5x2.0 mm 4-pad", "timing/Epson-SG-210STF.pdf",
                "SPXO SG-210STF 25.000000 MHz L, CMOS 1.6-3.63 V, +/-50 ppm, -40..85 C", ["4 VCC pi", "1 ~{ST} i",
                                                                                        "2 GND pi"], ["3 OUT o"]),
    # TPS54360B SNVSB93 pin functions (DDA): 9 = thermal pad (GND)
    "TPS54360B": ic(TI, "TPS54360BDDAR", "HSOIC-8 PowerPAD (DDA)", "power-supply/TPS54360B.pdf",
                    "Buck 4.5-60 V in, 3.5 A, current mode, EN 1.2 V threshold, 0.8 V 1 % reference",
                    ["2 VIN pi", "3 EN i", "4 RT/CLK i", "5 FB i", "6 COMP o"], ["1 BOOT p", "8 SW p", "7 GND pi",
                                                                               "9 PAD pi"]),
    # MDD SS52-SS5200 datasheet (SMA): cathode band; 60 V 5 A (KiCad D_Schottky 1 K, 2 A)
    "SS56": dict(mfr="MDD (Microdiode Semiconductor)", mpn="SS56", prefix="D", pkg="SMA (DO-214AC)",
                 stock=("Device", "D_Schottky"), ds=DS + "power-semiconductors/MDD-SS5x.pdf",
                 desc="Schottky rectifier 60 V 5 A (buck catch diode)"),
    # Cjiang FXL datasheet rows FXL0840-330-M (33 uH, DCR 156 mOhm, Isat 3.3/3.5 A, Irms 3.0/3.5 A) and FXL0530-220-M
    "L33U": dict(mfr="SZ Cjiang Technology", mpn="FXL0840-330-M", prefix="L", pkg="8.0x8.0x4.0 mm molded",
                 stock=("Device", "L"), ds=DS + "magnetics/Cjiang-FXL.pdf",
                 desc="Power inductor 33 uH 20 %, Isat 3.3 A, Irms 3.0 A, 156 mOhm (fan supply)"),
    "L22U": dict(mfr="SZ Cjiang Technology", mpn="FXL0530-220-M", prefix="L", pkg="5.4x5.2x3.0 mm molded",
                 stock=("Device", "L"), ds=DS + "magnetics/Cjiang-FXL.pdf",
                 desc="Power inductor 22 uH 20 %, Isat 2.0 A, Irms 1.5 A, 248 mOhm (SELV 5 V)"),
    # MDD SM712 data sheet p.1 'Schematic & Pin Configuration' (SOT-23 top view): 1 I/O 1, 2 I/O 2, 3 GND (the common pin; PCM pin audit)
    "SM712": dict(mfr="MDD (Microdiode Semiconductor)", mpn="SM712", prefix="D", pkg="SOT-23",
                  ds=DS + "protection/MDD-SM712.pdf", desc="RS-485 asymmetric TVS array -7/+12 V",
                  pins={"left": ["1 IO1 p", "2 IO2 p"], "right": ["3 GND p"]}),
    "ESD2CAN24": catalog.PARTS["ESD2CAN24"],
    "JP_OPEN": catalog.PARTS["JP_OPEN"],
    # MDD SMBJ series (MDD-SMBJ-series.pdf p.3 table): SMBJ36A VRWM 36 V, VBR 40.0-44.2 V, VC 58.1 V, IPP 10.4 A; SMBJ58CA bidirectional
    # VRWM 58 V, VBR 64.4-71.2 V, VC 93.6 V, IPP 6.5 A (the same JEDEC values as the Bourns sheet); cathode band = unidirectional (p.1).
    # MDD primary (LCSC C114001 / C114007, in stock), Bourns = alternate not on LCSC (sim/data/lcsc_semis.md P25/P26, 2026-10-05)
    "SMBJ36A": dict(mfr="MDD (Microdiode Semiconductor)", mpn="SMBJ36A", prefix="D", pkg="SMB", stock=("Device", "D_Zener"),
                    ds=DS + "protection/MDD-SMBJ-series.pdf", desc="TVS 600 W unidirectional, VRWM 36 V (SELV 24 V input)"),
    "SMBJ58CA": dict(mfr="MDD (Microdiode Semiconductor)", mpn="SMBJ58CA", prefix="D", pkg="SMB", stock=("Device", "D_TVS"),
                     ds=DS + "protection/MDD-SMBJ-series.pdf", desc="TVS 600 W bidirectional, VRWM 58 V (SELV 0 V to PE)"),
    # Nexperia BZX84 series rev 7 table 2: 1 anode, 2 n.c., 3 cathode; C5V1 = 4.8-5.4 V
    "BZX84C5V1": dict(mfr="Nexperia", mpn="BZX84-C5V1,215", prefix="D", pkg="SOT-23", ds=DS + "protection/BZX84.pdf",
                      desc="Zener 5.1 V 5 % 250 mW (ENABLE input clamp)", pins={"left": ["1 A p"],
                                                                              "right": ["3 K p", "2 NC nc"]}),
    # UniOhm chip resistor array spec (4D03: 4 isolated elements, terminals 1-8, 2-7, 3-6, 4-5; 1/16 W, 50 V)
    "RPACK10K": dict(mfr="Uniroyal Electronics (UniOhm)", mpn="4D03WGJ0103T5E", prefix="RN", pkg="0603x4 concave",
                     stock=("Device", "R_Pack04"), ds=DS + "passives-capacitors/UniOhm-resistor-array.pdf",
                     desc="Resistor array 4 x 10k 5 % 1/16 W 50 V, isolated (pull-downs on MCU command lines)"),
    # Murata NTC catalog row NCP15XH103F03RC: 10 k 1 %, B25/50 3380 K 1 %, 0402
    "NTC_SMD": dict(mfr="Murata", mpn="NCP15XH103F03RC", prefix="RT", pkg="0402", stock=("Device", "Thermistor_NTC"),
                    ds=DS + "sensing/Murata-NCP-NTC.pdf", desc="NTC 10 k 1 % B25/50 3380 K (inlet air, on board)"),
    # Fenghua CBW160808U301T (gen/data/alternates.csv ADOPT): 300 R at 100 MHz, 0603
    "FB300": dict(mfr="Guangdong Fenghua Advanced Technology", mpn="CBW160808U301T", prefix="FB", pkg="0603",
                  stock=("Device", "FerriteBead_Small"), ds=DS + "magnetics/Fenghua-CBW.pdf",
                  desc="Ferrite bead 300 R at 100 MHz (VDDA pi filter)"),
    # ---- connectors (XKB / Kefa sheets on file; numbering 1..N, odd/even for two-row parts)
    "J_PC": dict(mfr="XKB Connection", mpn="X6521FV-2x32-C85D32", prefix="J", pkg="2x32 2.54 mm female THT",
                 ds=DS + "connectors/XKB-X6521F.pdf", stock=("Connector_Generic", "Conn_02x32_Odd_Even"),
                 desc="Board-to-board socket 2x32 2.54 mm, PC contract (gen/interfaces.py), LIVE"),
    "J_JTAG": dict(mfr="XKB Connection", mpn="X1270WVS-2x10B-9TV01", prefix="J", pkg="2x10 1.27 mm SMT",
                   ds=DS + "connectors/XKB-X1270WVS.pdf", stock=("Connector_Generic", "Conn_02x10_Odd_Even"),
                   desc="cTI-20 JTAG header 1.27 mm - HAZARDOUS LIVE: isolated debug probe only (>= 1000 V DC)"),
    "J_FAN": dict(mfr="XKB Connection", mpn="X6511WV-03H-C60D30", prefix="J", pkg="1x3 2.54 mm THT",
                  ds=DS + "connectors/XKB-X6511W.pdf", stock=("Connector_Generic", "Conn_01x03"),
                  desc="Fan header 1x3 2.54 mm, 3 A: 1 +V, 2 0 V, 3 tach (SELV)"),
    "J_KF2": dict(mfr="Cixi Kefa Electronic", mpn="KF2EDGR-3.81-2P", prefix="J", pkg="pluggable 2-pos 3.81 mm R/A",
                  ds=DS + "connectors/KEFA-KF2EDGR-3.81.pdf", stock=("Connector_Generic", "Conn_01x02"),
                  desc="Pluggable header 2-pos 3.81 mm 300 V 8 A (plug KF2EDGK-3.81-2P): SELV 24 V input"),
    "J_KF6": dict(mfr="Cixi Kefa Electronic", mpn="KF2EDGR-3.81-6P", prefix="J", pkg="pluggable 6-pos 3.81 mm R/A",
                  ds=DS + "connectors/KEFA-KF2EDGR-3.81.pdf", stock=("Connector_Generic", "Conn_01x06"),
                  desc="Pluggable header 6-pos 3.81 mm (plug KF2EDGK-3.81-6P): bus or stop loop in/out + 0 V + screen (SELV)"),
    "J_KF8": dict(mfr="Cixi Kefa Electronic", mpn="KF2EDGR-3.81-8P", prefix="J", pkg="pluggable 8-pos 3.81 mm R/A",
                  ds=DS + "connectors/KEFA-KF2EDGR-3.81.pdf", stock=("Connector_Generic", "Conn_01x08"),
                  desc="Pluggable header 8-pos 3.81 mm (plug KF2EDGK-3.81-8P): relay NO / COM / NC, DI1, ready permissive, 0 V "
                       "(SELV)"),
})


# ============================================================================ 3. pin plan
# Input X-BAR destinations, SPRSP61C table 5-8: INPUT1-3 -> TZ1-3, INPUT4/5/6 -> XINT1/2/3, INPUT13/14 -> XINT4/5,
# every INPUT -> eCAP; Output X-BAR sources include ECAPxOUT (figure 5-7)
XBAR = {1: "TZ1", 2: "TZ2", 3: "TZ3", 4: "XINT1", 5: "XINT2", 6: "XINT3", 13: "XINT4", 14: "XINT5"}
PWM_GPIO = list(range(16))                          # PWMk = EPWM((k+1)//2) A/B on GPIO(k-1), mux 1


def plan():
    """[(net, pin label, function, direction, route)]; function = mux name, 'GPIO', or analog names joined by '+'."""
    P = []

    def add(net, gpio_or_label, fn="GPIO", d="in", route=""):
        P.append((net, gpio_or_label, fn, d, route))
    for k in range(1, 17):
        m, ab = (k + 1) // 2, "AB"[(k - 1) % 2]
        add("PWM%d_M" % k, "GPIO%d" % (k - 1), "EPWM%d_%s" % (m, ab), "out",
            "phase %d leg %s %s side -> 74LVC08 AND HEALTHY -> PWM%d (PC); HRPWM; ePWM%d" %
            ((k + 3) // 4, "AB"[((k - 1) // 2) % 2], "high" if k % 2 else "low", k, m))
    add("TRIP", "GPIO16", route="INPUTXBAR1>TZ1: one-shot trip of every ePWM (latch Q, 1 = tripped)")
    add("PRE_N", "GPIO17", route="INPUTXBAR4>XINT1: any trip source active now (wired-AND of OC_N, OVT_N, FLT_N, RDY, STOP_OK, "
        "WD_OK); rev A2: read instead of OC_N and OVT_N, which comparator tripped comes from the ADC and the recorder")
    add("SPI_CLK", "GPIO18", "SPIA_CLK", "out", "recorder flash SCLK (X2 pin; X1 takes the single-ended clock)")
    add("TACH3", "GPIO20", "EQEP1_A", "in", "eQEP1 up-count + unit timer = fan 3 speed (AGPIO pin: GPIO mode, B5 on "
        "pin 32)")
    add("K_A_M", "GPIO22", d="out", route="AND (HEALTHY OR HOLD) -> K_A")
    add("K_B_M", "GPIO23", d="out", route="AND (HEALTHY OR HOLD) -> K_B")
    add("LATCH_CLR", "GPIO24", d="out", route="latch CLK: rising edge clears only while PRE_N is high; 10k pull-up = "
        "boot-mode pin 1 high (flash boot)")
    add("K_PRE_M", "GPIO25", d="out", route="AND HEALTHY -> K_PRE")
    add("FAN_PWM_M", "GPIO26", "OUTPUTXBAR3", "out", "eCAP1 APWM > Output X-BAR 3 (ECAP1OUT) -> AND BIAS_EN -> "
        "isolated fan speed; 0 % = fans off")
    add("HEARTBEAT", "GPIO27", d="out", route="ISR toggles >= 500 Hz: 74LVC1G123 B (5 ms monoflop) + TPS3828 WDI (1.6 s)")
    add("SCI_RX", "GPIO28", "SCIA_RX", "in", "RS-485 RO; default SCI-boot pin")
    add("SCI_TX", "GPIO29", "SCIA_TX", "out", "RS-485 DI; default SCI-boot pin")
    add("RS485_DE", "GPIO30", d="out", route="driver enable (RE tied low: receiver always on)")
    add("EN_M", "GPIO31", d="out", route="AND HEALTHY -> EN")
    add("CAN_TX", "GPIO32", "CANA_TX", "out", "DCAN boot option 1 (BOOTDEF 0x22); 10k pull-up = boot-mode pin 0 high")
    add("CAN_RX", "GPIO33", "CANA_RX", "in", "DCAN boot option 1")
    add("TACH2", "GPIO34", route="INPUTXBAR8>eCAP3 capture (fan 2 period); rev A2: the run LED moved to WD_OK")
    add("TACH1", "GPIO35", route="INPUTXBAR7>eCAP2 capture (fan 1 period); TDI pin as GPIO: debug is cJTAG (TMS / TCK) only")
    add("FLASH_CS_N", "GPIO37", d="out", route="recorder flash CS# (10k pull-up: deselected in reset); TDO pin as GPIO (cJTAG)")
    add("BIAS_EN", "GPIO40", d="out", route="PC BIAS_EN (gate-bias converters on)")
    add("IMD_SW1", "GPIO41", d="out", route="PC IMD_SW1")
    add("IMD_SW2", "GPIO44", d="out", route="PC IMD_SW2")
    add("HOLD", "GPIO47", route="PC HOLD: contactor held closed by the power board (never open it while high)")
    add("MOV_OK", "GPIO48", route="PC MOV_OK: varistor monitor loop intact")
    add("STATUS_M", "GPIO49", d="out", route="AND HEALTHY -> isolated STATUS output")
    add("FLT_N", "GPIO50", route="INPUTXBAR5>XINT2: driver fault (diagnosis)")
    add("RDY_M", "GPIO51", route="INPUTXBAR14>XINT5: gate supplies ready (diagnosis; 1k from RDY)")
    add("DI1", "GPIO52", route="INPUTXBAR6>XINT3: DI1 battery fault / BMS contact (CA-IS3842HW VO3, 1 = contact closed)")
    add("STOP_OK", "GPIO53", route="INPUTXBAR13>XINT4: stop loop and ready permissive closed (diagnosis)")
    add("SPI_SIMO", "GPIO54", "SPIA_SIMO", "out", "recorder flash SI")
    add("SPI_SOMI", "GPIO55", "SPIA_SOMI", "in", "recorder flash SO")
    add("ETH_TXD", "GPIO56", "SCIB_TX", "out", "Ethernet bridge UART: CA-IS3842HW VI1 -> CH9121T RXD1")
    add("ETH_RXD", "GPIO57", "SCIB_RX", "in", "Ethernet bridge UART: CH9121T TXD1 -> CA-IS3842HW VO4")
    add("OSC_CLK", "GPIO19", "X1", "in", "single-ended 25 MHz clock (SG-210STF)")
    # analog: IL1-4 on one CMPSS each (H and L positive inputs on the same pin); IB on a CMPSS4 pin as well
    ana = [("IL1_ADC", "A2/B6/C9", "CMP1_HP0+CMP1_LP0", "CMPSS1 window = IL1 backup > ePWM X-BAR TRIP4 (all ePWM)"),
           ("IL2_ADC", "A10/B1/C10", "CMP2_HP3+CMP2_LP3", "CMPSS2 window = IL2 backup > TRIP4"),
           ("IL3_ADC", "A14/B14/C4", "CMP3_HP4+CMP3_LP4", "CMPSS3 window = IL3 backup > TRIP4"),
           ("IL4_ADC", "B4/C8", "CMP4_HP0+CMP4_LP0", "CMPSS4 window = IL4 backup (4 phases) > TRIP4; 0 V on PV-P75"),
           ("IB_ADC", "A7/C3", "CMP4_HP1+CMP4_LP1", "3 phases: CMPSS4 window on IB (port OC backup, HPMXSEL/LPMXSEL "
            "= 1); 4 phases: ADC PPB limits only"),
           ("IA_ADC", "A0/B15/C15/DACA_OUT", "", "port OC backup: ADC PPB limits > ADCxEVT > TRIP5"),
           ("VA_ADC", "A11/B10/C0", "", "OV backup: ADC-A PPB on every conversion (<= 10 us) > TRIP5"),
           ("VB_ADC", "A1/B7/DACB_OUT", "", "OV backup: ADC-B PPB (<= 10 us) > TRIP5"),
           ("VAX_ADC", "A6", "", "terminal side A (bipolar around VMID)"),
           ("VBX_ADC", "B2/C6", "", "terminal side B (bipolar around VMID)"),
           ("VPE_ADC", "A3/B9/C7", "", "insulation monitor (PE vs BUS-)"),
           ("IA_H_ADC", "B12/C2", "", "port A current, low gain (beyond +/-450 A, hold-off plausibility)"),
           ("IB_H_ADC", "A12/C5", "", "port B current, low gain"),
           ("NTC1", "C1", "", "heatsink section 1"), ("NTC2", "B11", "", "heatsink section 2"),
           ("NTC3", "B5", "", "heatsink section 3"), ("NTC4", "A5", "", "heatsink section 4 (open on PV-P75)"),
           ("NTC5", "A4/B8", "", "inductor 1"), ("NTC6", "A8", "", "inductor 2"), ("NTC7", "A9", "", "inductor 3"),
           ("NTC8", "B0/C11", "", "inductor 4 (open on PV-P75)"), ("NTC_IN", "C14", "", "inlet air (on board)"),
           ("VREF", "B3/VDAC", "VDAC", "CMPSS DAC reference = REF3030E (COMPDACCTL.SELREF = VDAC)")]
    for net, lab, cmp_, route in ana:
        add(net, lab, "ADC" + ("+" + cmp_ if cmp_ else ""), "ain", route)
    return P


def check_plan(P, pins):
    """Every function on its pin in Table 5-1; no pin twice; X-BAR inputs within table 5-8."""
    by_label = {d["label"]: p for p, d in pins.items()}
    by_gpio = {n: p for p, d in pins.items() for n in d["names"] if n.startswith("GPIO")}
    errs, seen, out = [], {}, {}
    for net, key, fn, d, route in P:
        p = by_gpio.get(key, by_label.get(key))
        if p is None:
            errs.append("%s: no pin %s" % (net, key))
            continue
        if p in seen:
            errs.append("pin %d used twice (%s, %s)" % (p, seen[p], net))
        seen[p] = net
        e = pins[p]
        for f in fn.split("+"):
            if f == "GPIO":
                ok = key.startswith("GPIO")
            elif f == "ADC":
                ok = any(re.match(r"^[ABC]\d+$", n) for n in e["ana"])
            elif f in ("X1", "TDI", "TDO"):
                ok = f in e["names"] or f in e["mux"]
            else:
                ok = f in e["mux"] or f in e["ana"] or f in e["names"]
            if not ok:
                errs.append("%s: pin %d (%s) does not offer %s" % (net, p, e["label"], f))
        for x in re.findall(r"INPUTXBAR(\d+)>(\w+)", route):
            if int(x[0]) in XBAR and x[1] != XBAR[int(x[0])]:
                errs.append("%s: INPUTXBAR%s cannot reach %s" % (net, x[0], x[1]))
        out[p] = net
    xb = [int(x) for x in re.findall(r"INPUTXBAR(\d+)", " ".join(r[4] for r in P))]
    if len(xb) != len(set(xb)):
        errs.append("an Input X-BAR channel is used twice")
    if errs:
        raise SystemExit("PIN PLAN FAILED:\n  " + "\n  ".join(errs))
    return out


# ============================================================================ 4. schematic
RAILS = ["+3V3", "+3V3A", "VDD12", "VREF", "S_24V", "S_5V", "S_3V3"]
RETURNS = ["GND", "S_GND", "PE"]
PH4 = "PV-P100/110 only"          # value tag of the phase-4 comparators (not fitted on PV-P75: bom/PV-CTL-P75_BOM.csv)
# assembly-variant knobs (gen/pcs_ctrl.py re-values them; these defaults leave PV-CTL unchanged)
PC_NC = ()                        # PC pins left open on this board
AFE_PC = ("VA", "VB", "VAX", "VBX", "VPE", "IA", "IB", "IA_H", "IB_H")   # power-board analog signals into the ADC
EXTRA_SHEETS = []                 # sheet functions drawn after 07_selv
GATE_NOTE = ""                    # appended to the gating block text
ADC_SKIP, ADC_MUXED, ADC_NOTE, VREF_XTRA = (), (), "", 0.0   # PC analog not read / read through a mux; extra VREF load
TITLE = "PV-CTL control board"
SUBTITLE = "Cost-first PV-P75/100/110 controller: F280039C on BUS-, one trip latch, one SELV barrier"
ROOT_NOTES = None                 # None = the PV-CTL cover notes (build_design)
BUILDS, P75_DNP = ("PV-P100/110", "PV-P75"), "DNP (PV-P75)"   # names: all phases fitted / phase 4 not fitted


def mcu_pins(pins, P):
    """Pin -> net: power and system pins by name, signal pins from the plan, unused GPIOs open."""
    plan_net = {}
    by_label = {d["label"]: p for p, d in pins.items()}
    by_gpio = {n: p for p, d in pins.items() for n in d["names"] if n.startswith("GPIO")}
    for net, key, fn, d, route in P:
        plan_net[by_gpio.get(key, by_label.get(key))] = net
    fixed = {"VDDIO": "+3V3", "VDDA": "+3V3A", "VDD": "VDD12", "VSS": "GND", "VSSA": "GND", "VREGENZ": "GND",
             "XRSn": "XRSn", "TCK": "TCK", "TMS": "TMS", "VREFHI": "VREF", "VREFLO": "GND"}
    return {str(p): fixed.get(d["names"][0], plan_net.get(p)) for p, d in pins.items()}


def sheet_supply(B):
    B.new_sheet("01_supply", "PC connector, supply, reset",
                "2x32 PC contract to the power board (all LIVE): only +3V3 is used (200 mA\n"
                "allocated by PV-PWR); TPS3828 reset + 1.6 s watchdog")
    B.block("PC connector (gen/interfaces.py PC; AGND = GND on this board)",
            "LIVE: GND = AGND = BUS-; nothing on this connector leaves the enclosure. Only +3V3 comes across (the former\n"
            "+24V / +5V pins carry the sensor references IL1R-IL4R since PV-PWR rev A2). +3V3 feeds the MCU, so the\n"
            "power board's 3.3 V signals and VDDIO share one rail and one power-up order (no back-powering).")
    pc = {k: (None if v in PC_NC else v) for k, v in IF.pins(IF.PC, rename={"AGND": "GND"}).items()}
    B.part("J_PC", pc)
    for net in ("+3V3", "GND"):
        B.flag(net)
    B.C("10u", "+3V3", "GND", pkg="0805", volt="10V", tol="10%")
    B.block("Reset and watchdog (TPS3828-33)",
            "RESET (open drain) holds XRSn below 2.93 V and for 200 ms after; no WDI edge for 0.9-2.5 s -> reset.\n"
            "XRSn 4.7k pull-up and 10 nF (SPRSP61C table 5-1: 2.2-10k, <= 100 nF). XRSn also clears the heartbeat\n"
            "monoflop, so every MCU reset sets the trip latch. +5V / +24V supervision: on the power board (RDY).")
    B.part("TPS3828", {"5": "+3V3", "4": "HEARTBEAT", "3": None, "1": "XRSn", "2": "GND"})
    B.C("100n", "+3V3", "GND")
    B.R("4.7k", "+3V3", "XRSn")
    B.C("10n", "XRSn", "GND")


def sheet_mcu(B, mcu):
    B.new_sheet("02_mcu", "F280039C, clock, debug, recorder flash",
                "TMS320F280039C (100-pin PZ), decoupling, 25 MHz SPXO, cTI-20 header in cJTAG mode\n"
                "(LIVE), 4 MB recorder flash, boot straps, command pull-downs")
    B.block("F280039CSPZR", "Pins as gen/data/pv_ctrl_pin_plan.csv (checked against SPRSP61C table 5-1 on every\n"
                            "build). VREGENZ = GND: internal 1.2 V VREG; VDD only decoupled.")
    B.part("F280039C", mcu)
    B.block("Decoupling (SPRSP61C 6.12.1.5.1)",
            "VDDIO 4 x 100 nF (>= 0.1 uF per pin); VDDA 2.2 uF + 100 nF behind a 300 R bead (pi filter allowed by\n"
            "6.12.1.4.1); VDD 2 x 10 uF + 2 x 100 nF = 20.2 uF (10-26.8 uF total, internal VREG, 10 % parts).")
    for _ in range(4):
        B.C("100n", "+3V3", "GND")
    B.part("FB300", {"1": "+3V3", "2": "+3V3A"})
    B.C("2.2u", "+3V3A", "GND", pkg="0603", volt="10V", tol="10%")
    B.C("100n", "+3V3A", "GND")
    for v in ("10u", "10u", "100n", "100n"):
        B.C(v, "VDD12", "GND", pkg="0805" if v == "10u" else "0603", volt="10V" if v == "10u" else "50V", tol="10%")
    B.flag("VDD12")
    B.flag("+3V3A")
    B.block("25 MHz clock (X1 single-ended)", "SG-210STF +/-50 ppm (CAN bit timing; CAN boot needs the external clock).\n"
                                              "f(X1) 10-25 MHz, tr/tf <= 6 ns (SPRSP61C 6.12.3.2.1).")
    B.part("OSC25", {"4": "+3V3", "1": "+3V3", "2": "GND", "3": "OSC_CLK"})
    B.C("100n", "+3V3", "GND")
    B.block("JTAG cTI-20 in cJTAG mode - HAZARDOUS LIVE (isolated probe only)",
            "Label: 'HAZARDOUS VOLTAGE - up to 1000 V DC to earth - isolated debug probe only'. cJTAG (IEEE 1149.7,\n"
            "TMS + TCK; SPRSP61C 6.12.6): TDI (3) / TDO (7) open, GPIO35 / 37 are TACH1 / flash CS#. TMS 2.2k pull-up;\n"
            "TRST (2) open; EMU0/1 4.7k; RESET (15) on XRSn. Field update over Ethernet / RS-485 / CAN: no live access.")
    B.part("J_JTAG", {"1": "TMS", "2": None, "3": None, "4": "GND", "5": "JTAG_VREF", "6": None, "7": None,
                      "8": "GND", "9": "TCK", "10": "GND", "11": "TCK", "12": "GND", "13": "JTAG_EMU0",
                      "14": "JTAG_EMU1", "15": "XRSn", "16": "GND", "17": "GND", "18": "GND", "19": "GND", "20": "GND"})
    B.R("2.2k", "+3V3", "TMS")
    B.R("100R", "+3V3", "JTAG_VREF", pkg="1206")              # survives a VTref short to GND
    B.R("4.7k", "+3V3", "JTAG_EMU0")
    B.R("4.7k", "+3V3", "JTAG_EMU1")
    B.block("Recorder flash: fault records, two firmware images, calibration, serial number, event log",
            "GD25Q32E 4 MB on SPIA (SCLK GPIO18, SI GPIO54, SO GPIO55, CS# GPIO37). CS# 10k pull-up: deselected in reset\n"
            "and boot (TDO floats until firmware owns it). HOLD# 10k up. WP# 10k up + write-protect strap JP to GND (closed\n"
            "at end of line with SRP0 = 1: status register and the golden-image blocks locked). Replaces the I2C EEPROM.")
    B.part("GD25Q32E", {"8": "+3V3", "1": "FLASH_CS_N", "6": "SPI_CLK", "5": "SPI_SIMO", "4": "GND", "2": "SPI_SOMI",
                        "3": "FLASH_WP_N", "7": "FLASH_HOLD_N"})
    B.C("100n", "+3V3", "GND")
    B.R("10k", "+3V3", "FLASH_CS_N")
    B.R("10k", "+3V3", "FLASH_HOLD_N")
    B.R("10k", "+3V3", "FLASH_WP_N")
    B.part("JP_OPEN", {"1": "FLASH_WP_N", "2": "GND"})
    B.block("Boot straps", "Flash boot = GPIO24 high and GPIO32 high at reset (SPRSP61C table 7-8): 10k pull-ups on\n"
                           "LATCH_CLR (GPIO24) and CAN_TX (GPIO32, also recessive while the MCU is in reset).")
    B.R("10k", "+3V3", "LATCH_CLR")
    B.R("10k", "+3V3", "CAN_TX")
    pd = ["PWM%d_M" % k for k in range(1, 17)] + ["EN_M", "K_A_M", "K_B_M", "K_PRE_M", "STATUS_M", "FAN_PWM_M",
                                                  "HEARTBEAT"]
    xtra = [g[0] for g in GATES if g[0] not in pd]                  # assembly variants (PCS-CTL: K_AC2_M)
    pd += xtra
    n_pd = len(pd)
    pd += ["GND"] * (-len(pd) % 4)
    B.block("Command pull-downs (%d x 4 x 10k arrays)" % (len(pd) // 4),
            "Every MCU output that ends in an AND gate here is low while the MCU is in reset or unprogrammed (GPIOs\n"
            "high impedance): 16 PWM, EN, K_A, K_B, K_PRE, STATUS, FAN_PWM, HEARTBEAT%s (%d lines, %d spare element%s).\n"
            "BIAS_EN, IMD_SW1/2 go straight to PC: pulled down on the power board (10k / 100k, PV-PWR)." %
            ("".join(", " + x[:-2] for x in xtra), n_pd, len(pd) - n_pd, "" if len(pd) - n_pd == 1 else "s"))
    for i in range(0, len(pd), 4):
        n = pd[i:i + 4]
        B.part("RPACK10K", {"1": n[0], "2": n[1], "3": n[2], "4": n[3], "8": "GND", "7": "GND", "6": "GND", "5": "GND"},
               value="4 x 10k")
    B.block("Run LED on WD_OK (rev A2: GPIO34 freed; WD_OK from sheet 05)",
            "Green, lit while the heartbeat monoflop holds WD_OK high (control ISR running, no MCU reset): the hardware's\n"
            "own view of 'firmware alive'. 680 R, ~2 mA from the 74LVC1G123 output (IOH 24 mA at 3.0 V).")
    B.R("680R", "WD_OK", "LED_RUN_A")
    B.part("LED_G", {"2": "LED_RUN_A", "1": "GND"})


# ---- analog front end (as built on PV-PWR: hardware/PV-PWR/outputs/PV-PWR_design_check.txt "PC:" lines)
IL_NET = ("10.0k", "29.4k")      # ILn -> R1 -> ILn_P -> R2 -> GND: a = 0.7462 (comparator node, op-amp + input)
IL_FB = ("36.5k", "10.0k")       # Rg (ILn_N -> VREF), Rf (ILn_N -> ILn_B): K = 0.274 -> G = a (1 + K) = 0.951,
#                                  zero 2.5 V -> 1.555 V at the ADC (3.0 V range)
ADC_RC = ("100R", "1n")          # power-board op-amp outputs -> ADC pin (charge bucket, 1.6 MHz)
IL_RC = ("47R", "1n")            # TLV9064 stage -> ADC pin
IL_NODE_C = "47p"                # ILn_P: comparator / op-amp input filter
NTC_BIAS = {"hs": "10.0k", "ind": "4.99k", "inlet": "10.0k"}   # from VREF (3.0 V); heatsink NTC1-4, inductor NTC5-8
HS_NTC, IND_NTC = (1, 2, 3, 4), (5, 6, 7, 8)


def sheet_afe(B):
    B.new_sheet("03_afe", "Reference and analog front ends",
                "REF3030E 3.0 V = VREFHI = VDAC = NTC bias = trip thresholds; IL1-4 gain stages;\n"
                "power-board signals into the ADC through 100 R / 1 nF; 8 NTC biases + inlet NTC")
    B.block("3.0 V reference (REF3030E from +3V3A)",
            "Feeds VREFHI (pins 24/25, >= 2.2 uF), VDAC (pin 16, >= 1 uF), the NTC biases, the IL offset and the\n"
            "threshold ladders of sheet 04: ADC, CMPSS and hardware trips are ratiometric to one reference. It runs\n"
            "from VDDA, so it never sits above VDDA (SPRSP61C 6.12.1.4.2). Needs +3V3 >= 3.20 V (REF30E dropout).")
    B.part("REF3030E", {"1": "+3V3A", "2": "VREF", "3": "GND"})
    B.C("100n", "+3V3A", "GND")
    B.C("2.2u", "VREF", "GND", pkg="0603", volt="10V", tol="10%")
    B.C("1u", "VREF", "GND", pkg="0603", volt="10V", tol="10%")
    B.C("100n", "VREF", "GND")
    B.block("Inductor-current front ends IL1-IL4 (TLV9064 gain stages)",
            "ILn = 2.5 V + 10.667 mV/A (STK-HO/A 75 on PV-PWR, 0.5-4.5 V, not ratiometric) -> 10.0k / 29.4k 0.1 %\n"
            "-> ILn_P (47 pF, a = 0.746): trip window (sheet 04) and the + input; Rg 36.5k to VREF, Rf 10.0k: ADC =\n"
            "1.555 V + 10.14 mV/A (0.072 A/LSB, -153..+142 A) -> 47 R / 1 nF -> ADC + CMPSS. IL4 = 0 V on PV-P75.")
    opa = {"4": "+3V3A", "11": "GND"}
    for n, (pp, nn, oo) in zip(range(1, 5), (("3", "2", "1"), ("5", "6", "7"), ("10", "9", "8"), ("12", "13", "14"))):
        pn, nn_, b_, a = "IL%d_P" % n, "IL%d_N" % n, "IL%d_B" % n, "IL%d_ADC" % n
        B.R(IL_NET[0], "IL%d" % n, pn, tol="0.1%", note="25 ppm/K")
        B.R(IL_NET[1], pn, "GND", tol="0.1%", note="25 ppm/K")
        B.C(IL_NODE_C, pn, "GND", diel="C0G", tol="5%")
        B.R(IL_FB[0], nn_, "VREF", tol="0.1%", note="25 ppm/K")
        B.R(IL_FB[1], nn_, b_, tol="0.1%", note="25 ppm/K")
        B.R(IL_RC[0], b_, a)
        B.C(IL_RC[1], a, "GND", diel="C0G", tol="5%")
        opa.update({pp: pn, nn: nn_, oo: b_})
    B.part("TLV9064", opa)
    B.C("100n", "+3V3A", "GND")
    B.block("Power-board analog signals (VMID 1.641-1.651 V per port, not on PC)",
            "VA/VB/VAX/VBX/VPE = VMID + V/1203; IA/IB = VMID + 3.0 mV/A (+/-478 A); IA_H/IB_H = VMID + 1.0 mV/A.\n"
            "Anti-alias poles are on the power board; here 100 R / 1 nF C0G as the ADC charge bucket. The trip\n"
            "comparators take the PC net itself (no ADC kick-back on them).")
    for net in AFE_PC:
        B.R(ADC_RC[0], net, net + "_ADC")
        B.C(ADC_RC[1], net + "_ADC", "GND", diel="C0G", tol="5%")
    B.block("NTC inputs (10 k B25/100 3988 K to AGND + 100 nF on PV-PWR, biased here from VREF)",
            "NTC1-4 heatsink sections (10.0k), NTC5-8 inductor windings (4.99k), 100 nF each. Open probe = 3.0 V:\n"
            "trips through the open-probe comparators (sheet 04); short = 0 V: trips as over-temperature.")
    for n in range(1, 9):
        B.R(NTC_BIAS["hs" if n in HS_NTC else "ind"], "VREF", "NTC%d" % n, tol="0.1%", note="25 ppm/K")
        B.C("100n", "NTC%d" % n, "GND")
    B.block("Inlet-air NTC (on board)", "Murata NCP15XH103F03RC, 10.0k from VREF; ADC only (fan law, derating).")
    B.part("NTC_SMD", {"1": "NTC_IN", "2": "GND"})
    B.R(NTC_BIAS["inlet"], "VREF", "NTC_IN", tol="0.1%", note="25 ppm/K")
    B.C("100n", "NTC_IN", "GND")


# ---- trip thresholds: ladders from VREF, values chosen to centre each band (design_check asserts them)
LADDER = {"OV": ("2.05k", "11.3k"),              # VREF - TH_OV - GND: 1075 V nominal (VMID 1.646 V + V/1203); was 2.10k / 11.8k (1084 V): moved
#                                                  # 8.9 V down so that, with the TLV9024 common-mode error (+9.7 V), the band top stays 1110 V
          "OTH": ("102k", "10.0k"),              # VREF - TH_OTH - GND: heatsink 87.9 C (10.0k bias)
          "OTL": ("30.9k", "1.15k"),             # VREF - TH_OTL - GND: inductor 149.9 C (4.99k bias)
          "OPEN": ("115R", "13.7k")}             # VREF - TH_OPEN - GND: 2.975 V, open probe (any NTC)
# rev A1: IL window per phase from the sensor's own reference (ILnR = Uref on PC): ILnR - 715R - TH_ILn_HI - 19.1k -
# TH_ILn_LO - 20.0k - GND, 1 M from VREF to TH_ILn_LO (a dead sensor, IL = ILR = 0 V, still trips): +74.2 / -74.4 A nominal
ILR_LAD, ILR_PU = ("715R", "19.1k", "20.0k"), "1M"
# comparator channels: (in+, in-, output net); output low = fault
CMP_MAIN = ([c for n in (1, 2, 3) for c in (("TH_IL%d_HI" % n, "IL%d_P" % n, "OC_N"), ("IL%d_P" % n, "TH_IL%d_LO" % n, "OC_N"))] +
            [("TH_OV", "VA", "OVT_N"), ("TH_OV", "VB", "OVT_N")] +
            [("NTC%d" % n, "TH_OTH" if n in HS_NTC else "TH_OTL", "OVT_N") for n in (1, 2, 3, 5, 6, 7)] +
            [("TH_OPEN", "NTC%d" % n, "OVT_N") for n in (1, 2, 3, 5, 6, 7)])
CMP_PH4 = [("TH_IL4_HI", "IL4_P", "OC_N"), ("IL4_P", "TH_IL4_LO", "OC_N"), ("NTC4", "TH_OTH", "OVT_N"),
           ("TH_OPEN", "NTC4", "OVT_N"), ("NTC8", "TH_OTL", "OVT_N"), ("TH_OPEN", "NTC8", "OVT_N")]


TH_NETS = {"OV": ("TH_OV",), "OTH": ("TH_OTH",), "OTL": ("TH_OTL",),
           "OPEN": ("TH_OPEN",)}


def ladder(B, key, nets):
    r = LADDER[key]
    chain = ["VREF"] + list(nets) + ["GND"]
    for v, a_, b_ in zip(r, chain, chain[1:]):
        B.R(v, a_, b_, tol="0.1%", note="25 ppm/K")
    for n in nets:
        B.C("10n", n, "GND")


def comparators(B, chans, value=None):
    """chans: (in+, in-, out) per comparator, 4 per TLV9024; spare channels: in+ GND, in- VREF, output open."""
    pins_ = (("5", "4", "2"), ("7", "6", "1"), ("9", "8", "14"), ("11", "10", "13"))
    refs = []
    for i in range(0, len(chans), 4):
        d = {"3": "+3V3", "12": "GND"}
        for (pp, nn, oo), ch in zip(pins_, chans[i:i + 4] + [("GND", "VREF", None)] * 4):
            d.update({pp: ch[0], nn: ch[1], oo: ch[2]})
        refs.append(B.part("TLV9024", d, value=value).ref)
        B.C("100n", "+3V3", "GND")
    return refs


def sheet_trip(B):
    B.new_sheet("04_trip", "Hardware trip comparators",
                "7 x TLV9024 on +3V3 (open drain, no firmware): IL windows from each sensor's Uref;\n"
                "VA / VB over-voltage, NTC over-temperature and open probe from VREF ladders")
    B.block("IL threshold ladders (0.1 %, 25 ppm/K), one per phase from that sensor's Uref (ILnR on PC)",
            "ILnR - 715R - TH_ILn_HI - 19.1k - TH_ILn_LO - 20.0k - GND: the window follows the sensor's own zero (Vref\n"
            "2.48-2.52 V is no longer an error term; Voe +/-10 mV is). 1 M from VREF to TH_ILn_LO: with the sensor dead\n"
            "(IL = ILR = 0 V) TH_ILn_LO stays ~30 mV above ILn_P and the phase trips. Phase 4: fitted on every build.")
    for n in range(1, 5):
        chain = ["IL%dR" % n, "TH_IL%d_HI" % n, "TH_IL%d_LO" % n, "GND"]
        for v, a_, b_ in zip(ILR_LAD, chain, chain[1:]):
            B.R(v, a_, b_, tol="0.1%", note="25 ppm/K")
        B.R(ILR_PU, "VREF", "TH_IL%d_LO" % n, note="dead-sensor pull-up")
        for t in ("HI", "LO"):
            B.C("10n", "TH_IL%d_%s" % (n, t), "GND")
    B.block("Threshold ladders (0.1 %, 25 ppm/K, from VREF)",
            "TH_OV: VA/VB 1075 V; TH_OTH: heatsink 87.9 C (PV-PWR: <= 89.9 C); TH_OTL: inductor 150 C;\n"
            "TH_OPEN 2.975 V: an NTC node above it is an open probe (cold limit -30 C).")
    for key, nets in TH_NETS.items():
        ladder(B, key, nets)
    B.block("Phases 1-3 and the port voltages (fitted on every build)",
            "OC_N (wired-OR, 10k to +3V3): ILn_P above TH_ILn_HI or below TH_ILn_LO, n = 1-3. OVT_N (10k): VA, VB above\n"
            "TH_OV; NTC1-3 (heatsink) below TH_OTH, NTC5-7 (inductor) below TH_OTL; any of them above TH_OPEN.\n"
            "Port over-current (387-413 A) arrives on FLT_N from the power board's own comparators (PV-PWR).")
    comparators(B, CMP_MAIN)
    B.R("10k", "+3V3", "OC_N")
    B.R("10k", "+3V3", "OVT_N")
    B.block("Phase 4 - ASSEMBLY OPTION: fitted on PV-P100/110 only",
            "IL4 window, NTC4 / NTC8 over-temperature and open probe. Not fitted on PV-P75 (bom/PV-CTL-P75_BOM.csv),\n"
            "where IL4 = 0 V and NTC4 / NTC8 are open: nothing then pulls OC_N / OVT_N for phase 4. Fitted on a\n"
            "4-phase build, a dead IL4 sensor (0 V) or an open NTC4 / NTC8 trips (fail-safe). Firmware cross-checks.")
    comparators(B, CMP_PH4, value="TLV9024PWR (%s)" % PH4)


GATES = ([("PWM%d_M" % k, "HEALTHY", "PWM%d" % k) for k in range(1, 17)] +
         [("EN_M", "HEALTHY", "EN"), ("K_PRE_M", "HEALTHY", "K_PRE"), ("K_A_M", "KGATE", "K_A"),
          ("K_B_M", "KGATE", "K_B"), ("STATUS_M", "HEALTHY", "STATUS_L"), ("FAN_PWM_M", "BIAS_EN", "FAN_PWM_L")])
WD_RC = ("49.9k", "100n")        # 74LVC1G123 Rext (to VCC) / Cext C0G: tw ~ Rext x Cext = 4.99 ms


def sheet_latch(B):
    B.new_sheet("05_latch", "Trip latch, watchdog, gating",
                "74LVC07 wired-AND of every trip source -> PRE_N -> 74LVC1G74 latch (no firmware);\n"
                "heartbeat monoflop; 74LVC08 gating of 16 PWM, EN, K_A, K_B, K_PRE, STATUS, FAN")
    B.block("Trip sources -> PRE_N (open-drain wired-AND, 4.7k to +3V3)",
            "74LVC07 (Ioff, 5 V tolerant): OC_N, OVT_N, FLT_N (driver DESAT/UVLO + port OC of PV-PWR), RDY (gate supplies,\n"
            "+5V, live 24 V on PV-PWR), STOP_OK (ENABLE), WD_OK (heartbeat and MCU reset). Any low -> PRE_N low ->\n"
            "latch set asynchronously (Q = TRIP = 1). The MCU reads PRE_N on GPIO17 (rev A2, diagnosis only).")
    B.part("LVC07", {"1": "OC_N", "3": "OVT_N", "5": "FLT_N", "9": "RDY", "11": "STOP_OK", "13": "WD_OK",
                     "2": "PRE_N", "4": "PRE_N", "6": "PRE_N", "8": "PRE_N", "10": "PRE_N", "12": "PRE_N",
                     "14": "+3V3", "7": "GND"})
    B.C("100n", "+3V3", "GND")
    B.R("4.7k", "+3V3", "PRE_N")
    B.block("The latch (74LVC1G74, set-dominant)",
            "PRE = PRE_N, CLR tied high, D = GND, CLK = LATCH_CLR (GPIO24). A firmware rising edge loads 0 (clears)\n"
            "only while PRE_N is high (no source active); with any source active PRE wins (Table 8-1). Q = TRIP\n"
            "(red LED, INPUTXBAR1 -> TZ1), /Q = HEALTHY (gates). Unpowered: Ioff -> HEALTHY reads 0 (100k).")
    B.part("SN74LVC1G74", {"2": "GND", "1": "LATCH_CLR", "7": "PRE_N", "6": "+3V3", "4": "GND", "8": "+3V3",
                           "5": "TRIP", "3": "HEALTHY"})
    B.C("100n", "+3V3", "GND")
    B.R("100k", "HEALTHY", "GND", note="HEALTHY = 0 while the latch is unpowered")
    B.part("LED_R", {"2": "TRIP", "1": "LED_TRIP_K"})
    B.R("1.0k", "LED_TRIP_K", "GND")
    B.block("Heartbeat watchdog (74LVC1G123)",
            "B = HEARTBEAT (rising edges from the control ISR, >= 500 Hz), A = GND, CLR = XRSn. Q = WD_OK stays high\n"
            "while edges come faster than tw = %s x %s (4.7-5.8 ms); no edge, a stuck pin or any MCU reset (XRSn low)\n"
            "-> WD_OK low -> latch. Rext to VCC, Cext between Cext and Rext/Cext (SCES586E table 4-1)." % WD_RC)
    B.part("LVC1G123", {"1": "GND", "2": "HEARTBEAT", "3": "XRSn", "8": "+3V3", "4": "GND", "5": "WD_OK",
                        "6": "WD_C", "7": "WD_RC"})
    B.C("100n", "+3V3", "GND")
    B.R(WD_RC[0], "+3V3", "WD_RC", tol="1%")
    B.C(WD_RC[1], "WD_C", "WD_RC", pkg="1206", volt="50V", diel="C0G", tol="5%")
    B.block("Contactor gate: KGATE = HEALTHY OR HOLD (74LVC1G32)",
            "A trip drops K_PRE at once, but K_A / K_B only while HOLD is low: while the power board holds a contactor\n"
            "(port current above its breaking limit) this board never commands it open; the power board's coil\n"
            "interlock enforces the same. Closing still needs the MCU command AND (HEALTHY OR HOLD).")
    B.part("LVC1G32", {"1": "HEALTHY", "2": "HOLD", "5": "+3V3", "4": "KGATE", "3": "GND"})
    B.C("100n", "+3V3", "GND")
    B.block("Gating: %d x 74LVC08 (%d gates used, %d spare with inputs grounded)" %
            (-(-len(GATES) // 4), len(GATES), -len(GATES) % 4),
            "PWMn = PWMn_M AND HEALTHY (16), EN = EN_M AND HEALTHY, K_PRE = K_PRE_M AND HEALTHY, K_A/K_B = K_x_M AND\n"
            "KGATE, STATUS_L = STATUS_M AND HEALTHY, FAN_PWM_L = FAN_PWM_M AND BIAS_EN (PV-PWR rule: fans only while the\n"
            "gate bias loads the aux supply). The power board pulls every PC input low (10k / 100k)." + GATE_NOTE)
    pins_ = (("1", "2", "3"), ("4", "5", "6"), ("9", "10", "8"), ("12", "13", "11"))
    g = GATES + [("GND", "GND", None)] * (-len(GATES) % 4)
    for i in range(0, len(g), 4):
        d = {"14": "+3V3", "7": "GND"}
        for (a_, b_, y), (x1, x2, out) in zip(pins_, g[i:i + 4]):
            d.update({a_: x1, b_: x2, y: out})
        B.part("SN74LVC08A", d)
        B.C("100n", "+3V3", "GND")
    B.block("Power-board status lines (PV-PWR as built)",
            "FLT_N: open drain on PV-PWR, pulled up HERE (4.99k, PV-PWR recommendation). RDY: open drain pulled up on\n"
            "PV-PWR (no pull-up here, 100k pull-down allowed), to the MCU via 1k. HOLD push-pull, MOV_OK open drain\n"
            "with 10k on PV-PWR: 100k pull-downs define an unplugged cable (not held / monitor open).")
    B.R("4.99k", "+3V3", "FLT_N")
    B.R("100k", "RDY", "GND")
    B.R("1.0k", "RDY", "RDY_M")
    B.R("100k", "HOLD", "GND")
    B.R("100k", "MOV_OK", "GND")
    for net in ("PRE_N", "TRIP", "HEALTHY", "WD_OK"):
        B.TP(net)


def sheet_iso(B, iso, xing):
    B.new_sheet("06_iso", "Barrier B1: isolators, CAN, RS-485",
                "Reinforced barrier B1: CA-IS3050W CAN, CA-IS3082WNX RS-485, 2 x CA-IS3821LG,\n"
                "CA-IS3820LG, CA-IS3842HW (Ethernet UART, DI1); bus TVS, jumper terminations (SELV)")
    B.block("Barrier B1 rules (insulation_spec 'Reinforced HV-PELV, DC pole')",
            "Every part on this sheet that touches LIVE and SELV is a declared isolator: VIOSM >= 8 kV, VISO >= 5 kVrms,\n"
            "VIOWM >= 1414 V DC, CLR/CPG 8 mm. 8 mm < 10 mm (PD2 creepage) -> conformal coating PD1 over the B1 zone\n"
            "is MANDATORY; routed slot under every isolator; SELV copper >= 10 mm from LIVE copper elsewhere.")
    B.block("Isolated CAN (BMS / PCS), CA-IS3050W",
            "Logic side +3V3 (ICC1 <= 3.6 mA), bus side S_5V (ICC2 <= 73 mA dominant): no live +5V needed. Split\n"
            "termination 2 x 60.4 R + 4.7 nF by JP; ESD2CAN24 to SELV 0 V. Terminal: CANH, CANL, 0 V, CANH, CANL\n"
            "(in / out), shield to PE.")
    iso.add(B.part("CA_IS3050W", {"1": "+3V3", "6": "CAN_TX", "3": "CAN_RX", "4": None, "5": None, "2": "GND",
                                  "7": "GND", "8": "GND", "16": "S_5V", "13": "S_CANH", "12": "S_CANL", "11": None,
                                  "14": None, "9": "S_GND", "10": "S_GND", "15": "S_GND"}).ref)
    B.C("100n", "+3V3", "GND")
    B.C("100n", "S_5V", "S_GND")
    B.C("1u", "S_5V", "S_GND", volt="10V", tol="10%")
    B.part("ESD2CAN24", {"1": "S_CANH", "2": "S_CANL", "3": "S_GND"})
    B.part("JP_OPEN", {"1": "S_CANH", "2": "S_CAN_TH"})
    B.R("60.4R", "S_CAN_TH", "S_CAN_TM", pkg="0805")
    B.R("60.4R", "S_CAN_TM", "S_CANL", pkg="0805")
    B.C("4.7n", "S_CAN_TM", "S_GND", volt="100V", tol="10%")
    xing.add(B.part("J_KF6", {"1": "S_CANH", "2": "S_CANL", "3": "S_GND", "4": "S_CANH", "5": "S_CANL", "6": "PE"}).ref)
    B.block("Isolated RS-485, CA-IS3082WNX",
            "Logic side +3V3; bus side S_5V. DE from the MCU (internal pull-down: driver off in reset), RE tied low\n"
            "(receiver always on, fail-safe). 120 R by JP; SM712 to SELV 0 V. Terminal: A, B, 0 V twice, shield.")
    iso.add(B.part("CA_IS3082WNX", {"1": "+3V3", "6": "SCI_TX", "5": "RS485_DE", "4": "GND", "3": "SCI_RX",
                                    "2": "GND", "7": "GND", "8": "GND", "16": "S_5V", "12": "S_RSA", "13": "S_RSB",
                                    "10": None, "11": None, "14": None, "9": "S_GND", "15": "S_GND"}).ref)
    B.C("1u", "+3V3", "GND", volt="10V", tol="10%")
    B.C("100n", "+3V3", "GND")
    B.C("1u", "S_5V", "S_GND", volt="10V", tol="10%")
    B.C("100n", "S_5V", "S_GND")
    B.part("SM712", {"1": "S_RSA", "2": "S_RSB", "3": "S_GND"})
    B.part("JP_OPEN", {"1": "S_RSA", "2": "S_RS_TA"})
    B.R("120R", "S_RS_TA", "S_RSB", pkg="0805")
    xing.add(B.part("J_KF6", {"1": "S_RSA", "2": "S_RSB", "3": "S_GND", "4": "S_RSA", "5": "S_RSB", "6": "PE"}).ref)
    B.block("Digital isolators (default LOW = safe)",
            "3821 #1: STATUS_L -> S_STATUS_G (out), S_EN -> STOP_OK (in: SELV side unpowered or open = STOP).\n"
            "3821 #2: FAN_PWM_L -> S_FAN_PWM (out; unpowered = fans off), S_TACH1_I -> TACH1. 3820 (A side SELV):\n"
            "S_TACH2_I, S_TACH3_I -> TACH2, TACH3. 100 nF per side.")
    iso.add(B.part("CA_IS3821", {"1": "+3V3", "2": "STATUS_L", "3": "STOP_OK", "4": "GND", "8": "S_5V",
                                 "7": "S_STATUS_G", "6": "S_EN", "5": "S_GND"}).ref)
    iso.add(B.part("CA_IS3821", {"1": "+3V3", "2": "FAN_PWM_L", "3": "TACH1", "4": "GND", "8": "S_5V",
                                 "7": "S_FAN_PWM", "6": "S_TACH1_I", "5": "S_GND"}).ref)
    iso.add(B.part("CA_IS3820", {"1": "S_5V", "2": "S_TACH2_I", "3": "S_TACH3_I", "4": "S_GND", "8": "+3V3",
                                 "7": "TACH2", "6": "TACH3", "5": "GND"}).ref)
    for _ in range(3):
        B.C("100n", "+3V3", "GND")
        B.C("100n", "S_5V", "S_GND")
    B.block("Ethernet UART and DI1 across B1: CA-IS3842HW (rev A2, fitted on every variant)",
            "A side LIVE (+3V3): VI1 <- ETH_TXD (SCIB TX), VO4 -> ETH_RXD (SCIB RX), VO3 -> DI1 (GPIO52), VI2 spare (GND).\n"
            "B side SELV (S_5V): VO1 -> 1.0k / 2.0k -> CH9121T RXD1 (3.3 V I/O), VI4 <- CH9121T TXD1, VI3 <- S_DI1 (sheet 07).\n"
            "Default HIGH: the UART lines idle high with either side unpowered or the bridge not fitted (PV-CTL).")
    iso.add(B.part("CA_IS3842", {"1": "+3V3", "3": "ETH_TXD", "4": "GND", "5": "DI1", "6": "ETH_RXD", "7": "+3V3",
                                 "2": "GND", "8": "GND", "16": "S_5V", "14": "S_ETH_RXD5", "13": None, "12": "S_DI1",
                                 "11": "S_ETH_TXD", "10": "S_5V", "9": "S_GND", "15": "S_GND"}).ref)
    B.C("100n", "+3V3", "GND")
    B.C("100n", "S_5V", "S_GND")


FAN_FB = ("100k", "2.87k", "15.0k")   # TPS54360B fan supply: Rtop (S_FAN_V-FB), Rbot (FB-S_GND), Rinj (S_FAN_VF-FB)
FAN_PWM_RC = ("4.7k", "2.2u")         # S_FAN_PWM -> S_FAN_VF (10 ms)
FAN_COMP = ("13.3k", "15n")           # COMP to GND: R + C (fco ~15 kHz at 24 V / 2.1 A, Cout 20 uF)
FAN_RT = "324k"                       # 300 kHz
V5_FB = ("53.6k", "10.2k")            # SELV 5 V: 0.8 V x (1 + 53.6/10.2) = 5.0 V
V5_COMP = ("2.15k", "150n")
V5_RT = "200k"                        # 500 kHz
N_FAN = 3
EN_DIV = ("33k", "10k", "100n")       # ENABLE input divider (ON >= 8.8 V, OFF <= 3.4 V) + debounce C
DI_NET = ("1.0k", "10k", "10k", "100n")   # DI1: wetting from S_5V, pull-down, series, filter C (rev A2)
RELAY = dict(r_coil=125.0, tol=0.10, v_pick=3.75, v_drop=0.5, t_amb=(-40.0, 85.0))   # HFD27/005-S, Hongfa HFD27 p.1-2 (23 C)
S5_DESIGN = 0.5                        # A: SELV 5 V design allocation (rev A2: was 0.3 A; L22U 1.5 A, TPS54360B >= 4.5 A limit)
S24 = (12.0, 36.0)                    # SELV input range: cross-regulated fan winding (PV-PWR / aux75_spec)
# ---- fan supply (PCM-26): ONE contract with the power board. gen/pv_power.py imports these functions, prints the SELV minimum at its worst
# cross-regulation point (aux75_spec) and what this buck passes from it, and applies the resulting airflow in its thermal line; this board
# re-derives that value from its drawn parts and checks the power board's printed line (hardware/PV-PWR*/outputs), so the two design checks
# cannot state different fan voltages. Airflow is taken proportional to the fans' speed and the speed proportional to the supply voltage
# (ASSUMED: the 7 V = 29 % speed law of sim/pv_module.py; the Delta data sheet gives 7.0-27.6 V operating and 3700 rpm at 24 V).
FAN_V_RATED = 24.0                    # V: the fans' rated voltage (Delta AFB / FFB1224SHE-F00 p.1)
FAN_W = {"PV-P75": 3 * 12.0, "PV-P100/110": 3 * 17.0}   # W at full speed: 3 x AFB1224SHE-F00 (Delta p.1: 24 V, 0.50 A typ / 0.75 A max);
#                                                      PV-P100/110: 3 x FFB1224SHE-F00 capped at 17 W each (sim/pv_module.py FAN_P_CAP)
# TPS54360B in dropout (SNVSB93 7.3.4 and equations 1 / 43: the high side stays on until BOOT-SW < 2.1 V, the effective duty "approaches
# 100 %", 0.99 in the equation):  V_out = D (V_in - I R_DS(on)) - (1 - D) V_F - I R_dc.
FAN_BUCK = dict(d=0.99, rds=0.24, vf=0.70, dcr25=0.156, t_board=85.0, r_pcb=0.010)
#   R_DS(on) 0.24 ohm (ESTIMATE): Fig. 1 (6.7), BOOT-SW = 3 V curve (the dropout operating point) 0.12 ohm at 25 C (the data sheet's own
#   example in 8.2.2.10) to 0.20 ohm at Tj = 150 C, x 190 / 155 mOhm (the 6.5 maximum over the BOOT-SW = 6 V curve at 150 C) for the
#   tolerance 8.2.2.10 asks to include; V_F 0.70 V: SS56 at 5 A, max (MDD SS5x p.1; it conducts 1 % of the period); R_dc: FXL0840-330-M
#   156 mOhm MAX at 25 C (Cjiang-FXL.pdf) x (1 + 0.00393 (85 - 25)) copper at the board's 85 C + 10 mOhm trace (ESTIMATE)
EN_V_MAX, EN_MARGIN = 1.3, 0.02       # TPS54360B EN threshold maximum (SNVSB93 6.5) and the margin the running command keeps above it


def fan_vf(dty, vfb=0.8, rf=None, v5=5.0):
    """S_FAN_VF (V): the 5 V PWM through 4.7k / 2.2 uF against the FB injection resistor, FB at vfb"""
    rf, rinj = val(FAN_PWM_RC[0]) if rf is None else rf, val(FAN_FB[2])
    return (dty * v5 / rf + vfb / rinj) / (1 / rf + 1 / rinj)


def fan_vout(dty):
    """fan buck set point (V) at PWM duty dty (nominal parts): higher duty = lower voltage"""
    rt, rbot, rinj = (val(x) for x in FAN_FB)
    return 0.8 + rt * (0.8 / rbot + (0.8 - fan_vf(dty)) / rinj)


def v5_range():
    """SELV 5 V set point over the 1 % divider and the 0.792-0.808 V reference (TPS54360B)"""
    return (0.792 * (1 + val(V5_FB[0]) * 0.99 / (val(V5_FB[1]) * 1.01)), 0.808 * (1 + val(V5_FB[0]) * 1.01 / (val(V5_FB[1]) * 0.99)))


def fan_set_range(dty):
    """(lowest, highest) buck set point in V and the lowest S_FAN_VF in V at PWM duty dty with the buck running (FB at its set point),
    over the corners of 1 % Rtop / Rbot / Rinj / PWM series resistor, the 0.792-0.808 V reference and the SELV 5 V range (64 corners)"""
    rt0, rb0, ri0 = (val(x) for x in FAN_FB)
    rf0, out, vfs = val(FAN_PWM_RC[0]), [], []
    for kt in (0.99, 1.01):
        for kb in (0.99, 1.01):
            for ki in (0.99, 1.01):
                for kf in (0.99, 1.01):
                    for vfb in (0.792, 0.808):
                        for v5 in v5_range():
                            vf = (dty * v5 / (rf0 * kf) + vfb / (ri0 * ki)) / (1 / (rf0 * kf) + 1 / (ri0 * ki))
                            out.append(vfb + rt0 * kt * (vfb / (rb0 * kb) + (vfb - vf) / (ri0 * ki)))
                            vfs.append(vf)
    return min(out), max(out), min(vfs)


def fan_d_on():
    """lowest duty that keeps EN above its 1.3 V worst-case threshold (FB = 0 V while off): the START command"""
    return next(x / 1000 for x in range(1001) if fan_vf(x / 1000, 0.0) >= EN_V_MAX)


def fan_d_full():
    """100 % speed command once the buck runs: the lowest duty (0.005 steps) whose lowest EN voltage over the corners stays 20 mV above the
    1.3 V threshold maximum (FB is then at its set point, not at 0 V) - the highest set point the EN pin allows"""
    return next(x / 200 for x in range(201) if fan_set_range(x / 200)[2] >= EN_V_MAX + EN_MARGIN)


def fan_r_dc():
    return FAN_BUCK["dcr25"] * (1 + 0.00393 * (FAN_BUCK["t_board"] - 25.0)) + FAN_BUCK["r_pcb"]


def fan_ceiling(vin, i):
    """largest voltage (V) the fan buck can pass from S_24V = vin (V) at the fan current i (A): its dropout limit"""
    b = FAN_BUCK
    return b["d"] * (vin - i * b["rds"]) - (1 - b["d"]) * b["vf"] - i * fan_r_dc()


def fan_vin_for(v, i):
    """S_24V (V) at which the buck can just pass v (V) at i (A): fan_ceiling inverted"""
    b = FAN_BUCK
    return (v + (1 - b["d"]) * b["vf"] + i * fan_r_dc()) / b["d"] + i * b["rds"]


def buck(B, vin, vout, en, rt, fb, comp, lkey, tag):
    """TPS54360B buck: BOOT 100 nF, SS56 catch diode, RT, COMP R + C; FB divider drawn by the caller."""
    sw, bt, cmp_ = "S_%s_SW" % tag, "S_%s_BT" % tag, "S_%s_COMP" % tag
    B.part("TPS54360B", {"2": vin, "3": en, "4": "S_%s_RT" % tag, "5": fb, "6": cmp_, "1": bt, "8": sw,
                         "7": "S_GND", "9": "S_GND"})
    B.C("2.2u", vin, "S_GND", pkg="1206", volt="50V", tol="10%")
    B.C("100n", vin, "S_GND", pkg="0603", volt="50V")
    B.R(rt, "S_%s_RT" % tag, "S_GND")
    B.C("100n", bt, sw)
    B.part("SS56", {"1": sw, "2": "S_GND"})
    B.part(lkey, {"1": sw, "2": vout})
    B.R(comp[0], cmp_, "S_%s_CZ" % tag)
    B.C(comp[1], "S_%s_CZ" % tag, "S_GND", tol="10%")


def sheet_selv(B, iso, xing):
    B.new_sheet("07_selv", "SELV supply, fans, stop, DI1, relay",
                "SELV 24 V input (AUX-T1 reinforced winding, 12-36 V), TPS54360B 5 V and fan supply,\n"
                "3 fan headers + tach, stop (chainable) / ready / DI1 terminals, relay, SELV 0 V to PE")
    B.block("SELV input (2-pin, double-insulated leads, >= 10 mm from LIVE copper)",
            "AUX-T1 fan winding, cross-regulated: 12-36 V (sags to ~15 V without gate-bias load, PV-PWR). SMBJ36A\n"
            "clamp (36 V working), 2 x 10 uF 50 V. Both bucks have no under-voltage lock-out above 4.5 V.")
    B.part("J_KF2", {"1": "S_24V", "2": "S_GND"})
    B.flag("S_24V")
    B.flag("S_GND")
    B.part("SMBJ36A", {"1": "S_24V", "2": "S_GND"})
    B.C("10u", "S_24V", "S_GND", pkg="1210", volt="50V", tol="10%")
    B.C("10u", "S_24V", "S_GND", pkg="1210", volt="50V", tol="10%")
    B.block("SELV 5 V (TPS54360B, 500 kHz)", "CAN and RS-485 bus sides, SELV side of the three digital isolators;\n"
                                            "EN floating = on (internal pull-up). 22 uH, 2 x 22 uF.")
    buck(B, "S_24V", "S_5V", None, V5_RT, "S_V5_FB", V5_COMP, "L22U", "V5")
    B.R(V5_FB[0], "S_5V", "S_V5_FB", tol="1%")
    B.R(V5_FB[1], "S_V5_FB", "S_GND", tol="1%")
    B.C("22u", "S_5V", "S_GND", pkg="1206", volt="10V", tol="10%")
    B.C("22u", "S_5V", "S_GND", pkg="1206", volt="10V", tol="10%")
    B.flag("S_5V")
    B.block("Fan supply (TPS54360B, 300 kHz): speed by voltage, passes what the winding gives",
            "S_FAN_PWM (isolated, 0/5 V) -> 4.7k / 2.2 uF -> S_FAN_VF: EN via 10k (off below 1.1-1.3 V: duty below\n"
            "~25 %%, static low or a dead live side = fans OFF) and current injection into FB through %s: higher duty =\n"
            "lower voltage (24 V ... 7 V); at a low input it runs in dropout. Rtop %s / Rbot %s." % (FAN_FB[2], FAN_FB[0],
                                                                                                      FAN_FB[1]))
    buck(B, "S_24V", "S_FAN_V", "S_FAN_EN", FAN_RT, "S_FAN_FB", FAN_COMP, "L33U", "FAN")
    B.R(FAN_FB[0], "S_FAN_V", "S_FAN_FB", tol="1%")
    B.R(FAN_FB[1], "S_FAN_FB", "S_GND", tol="1%")
    B.R(FAN_FB[2], "S_FAN_VF", "S_FAN_FB", tol="1%")
    B.R(FAN_PWM_RC[0], "S_FAN_PWM", "S_FAN_VF")
    B.C(FAN_PWM_RC[1], "S_FAN_VF", "S_GND", volt="10V", tol="10%")
    B.R("10k", "S_FAN_VF", "S_FAN_EN")
    B.C("10u", "S_FAN_V", "S_GND", pkg="1210", volt="50V", tol="10%")
    B.C("10u", "S_FAN_V", "S_GND", pkg="1210", volt="50V", tol="10%")
    B.C("100n", "S_FAN_V", "S_GND", pkg="0603", volt="50V")
    for n in range(1, N_FAN + 1):
        B.block("Fan %d" % n, "1 +V, 2 0 V, 3 tach: 10k to S_5V, 10k + 1 nF (10 us) to the isolator input.")
        B.part("J_FAN", {"1": "S_FAN_V", "2": "S_GND", "3": "S_TACH%d" % n})
        B.R("10k", "S_5V", "S_TACH%d" % n)
        B.R("10k", "S_TACH%d" % n, "S_TACH%d_I" % n)
        B.C("1n", "S_TACH%d_I" % n, "S_GND")
    B.block("Stop input (hardware, chainable) and ready permissive",
            "J_STOP 1 S_EN_SRC (S_24V via 10k: dry-contact supply for ONE module), 2 / 3 stop line in / out (loop-through:\n"
            "chain a cabinet 24 V stop line module to module), 4 / 5 0 V in / out, 6 screen to PE. J_IO 6-7: ready permissive\n"
            "in series (wire link if unused) -> S_EN_IN -> 33k / 10k, 5.1 V clamp, 100 nF -> isolator -> STOP_OK -> latch\n"
            "(no firmware): open stop contact, open permissive, broken wire or lost SELV = gates off.")
    xing.add(B.part("J_KF6", {"1": "S_EN_SRC", "2": "S_EN_BUS", "3": "S_EN_BUS", "4": "S_GND", "5": "S_GND",
                              "6": "PE"}).ref)
    B.R("10k", "S_24V", "S_EN_SRC", pkg="1206")
    B.R(EN_DIV[0], "S_EN_IN", "S_EN", pkg="0805")
    B.R(EN_DIV[1], "S_EN", "S_GND")
    B.C(EN_DIV[2], "S_EN", "S_GND")
    B.part("BZX84C5V1", {"3": "S_EN", "1": "S_GND", "2": None})
    B.block("Relay output (ready / fault) and DI1 (battery fault / BMS contact)",
            "STATUS channel -> 2N7002BK -> HFD27/005-S coil from S_5V, BAS16 across it: energised = healthy (NO closed); a\n"
            "trip, stop, lost SELV or live side = NC closed. Both poles in parallel; SELV circuits only (<= 30 V AC / 60 V DC).\n"
            "J_IO 4 = 5 V wetting (1.0k from S_5V), 5 = DI1 (10k down, 10k + 100 nF = 1 ms, 5.1 V clamp) -> CA-IS3842HW VI3.")
    B.part("J_KF8", {"1": "S_RLY_NO", "2": "S_RLY_COM", "3": "S_RLY_NC", "4": "S_DI1_SRC", "5": "S_DI1_IN",
                     "6": "S_EN_BUS", "7": "S_EN_IN", "8": "S_GND"})
    B.part("2N7002BK", {"1": "S_STATUS_G", "3": "S_RLY_K", "2": "S_GND"})
    B.R("100k", "S_STATUS_G", "S_GND")
    B.part("HFD27", {"1": "S_5V", "16": "S_RLY_K", "4": "S_RLY_COM", "13": "S_RLY_COM", "6": "S_RLY_NC",
                     "11": "S_RLY_NC", "8": "S_RLY_NO", "9": "S_RLY_NO"})
    B.part("BAS16", {"1": "S_RLY_K", "3": "S_5V", "2": None})
    B.R(DI_NET[0], "S_5V", "S_DI1_SRC")
    B.R(DI_NET[1], "S_DI1_IN", "S_GND", pkg="0805")
    B.R(DI_NET[2], "S_DI1_IN", "S_DI1")
    B.C(DI_NET[3], "S_DI1", "S_GND")
    B.part("BZX84C5V1", {"3": "S_DI1", "1": "S_GND", "2": None})
    B.block("SELV 0 V to PE (SELV may be earthed by the installer: PELV)",
            "1 M || 4.7 nF 1 kV plus SMBJ58CA: a common-mode surge on the field cables is clamped to PE; a solid\n"
            "PELV bond by the installer is harmless. Plated M3 hole = PE / shield bond near the terminals.")
    xing.add(B.R("1M", "S_GND", "PE", pkg="1206").ref)
    xing.add(B.C("4.7n", "S_GND", "PE", pkg="1206", volt="1kV", tol="10%").ref)
    xing.add(B.part("SMBJ58CA", {"1": "S_GND", "2": "PE"}).ref)
    B.part("MH", {"1": "PE"})
    B.flag("PE")


def eth_fitted():
    """The Ethernet bridge (sheet NN_eth) is an assembly option: fitted on the inverter variants (PCS-CTL, PCS-CTL-3W), not fitted
    on PV-CTL / PV-CTL-P75 - read from the project name at build time, as the PV-P75 / three-wire option is"""
    return PROJECT.startswith("PCS")


ETH_LDO_R = "10R"                     # S_5V -> TPS7A2033 IN: takes part of the LDO's drop (its SOT-23 would reach ~122 C at an 85 C board)


def sheet_eth(B, xing):
    dnp = not eth_fitted()
    B.new_sheet("%02d_eth" % (8 + len(EXTRA_SHEETS)), "Ethernet bridge (EMS, Modbus TCP)",
                "WCH CH9121T UART-to-Ethernet bridge on the SELV side, HR913550AE RJ45 with magnetics,\n"
                "SRV05-4, TPS7A2033 3.3 V. %s" % ("FITTED on this variant." if not dnp else
                                                    "NOT FITTED on PV-CTL / PV-CTL-P75: footprints only (DNP)."))
    B.block("SELV 3.3 V: TPS7A2033 from S_5V through %s" % ETH_LDO_R,
            "VCC33 needs 3.2-3.4 V (CH9121 V2.6 table 7-2): +/-1.5 %% LDO. The %s 1206 takes ~1 V of the drop so the\n"
            "LDO stays below 125 C at an 85 C board (design check). EN tied to IN. 1 uF in, 10 uF + 100 nF out." % ETH_LDO_R)
    B.R(ETH_LDO_R, "S_5V", "S_ETH_5V", pkg="1206", dnp=dnp)
    B.flag("S_ETH_5V")
    B.part("TPS7A2033", {"1": "S_ETH_5V", "3": "S_ETH_5V", "2": "S_GND", "5": "S_3V3", "4": None}, dnp=dnp)
    B.C("1u", "S_ETH_5V", "S_GND", volt="16V", tol="10%", dnp=dnp)
    B.C("10u", "S_3V3", "S_GND", pkg="0805", volt="10V", tol="10%", dnp=dnp)
    B.C("100n", "S_3V3", "S_GND", dnp=dnp)
    B.block("CH9121T (CH9121 V2.6 figure 9-1)",
            "Port 1 (RXD1 / TXD1) to SCIB through the CA-IS3842HW (sheet 06); port 2, CTS, TNOW open. 25 MHz 12 pF crystal, no\n"
            "load C (inside); VDDK 1 uF; power-on reset inside. Straps (pull-ups inside): CFG low at power-up = serial set-up\n"
            "(end-of-line test point); LINK/RESET low for 2 s = factory reset - its LED path holds it high. UART 1.0k / 2.0k.")
    B.part("CH9121T", {"18": "S_3V3", "8": "S_3V3", "3": "S_ETH_VDDK", "4": "S_ETH_XI", "5": "S_ETH_XO",
                       "13": "S_ETH_RXD", "14": "S_ETH_TXD", "15": None, "16": None, "11": "S_ETH_CFG", "17": "S_GND",
                       "1": "S_ETH_TXP", "2": "S_ETH_TXN", "19": "S_ETH_RXP", "20": "S_ETH_RXN", "6": "S_ETH_LINK",
                       "7": "S_ETH_ACT", "9": None, "10": None, "12": None}, dnp=dnp)
    B.C("1u", "S_ETH_VDDK", "S_GND", volt="10V", tol="10%", dnp=dnp)
    B.C("100n", "S_3V3", "S_GND", dnp=dnp)
    B.part("XTAL25", {"1": "S_ETH_XI", "3": "S_ETH_XO", "2": "S_GND", "4": "S_GND"}, dnp=dnp)
    B.R("1.0k", "S_ETH_RXD5", "S_ETH_RXD", dnp=dnp)
    B.R("2.0k", "S_ETH_RXD", "S_GND", dnp=dnp)
    B.TP("S_ETH_CFG")
    B.block("RJ45 with magnetics (HR913550AE), MDI protection, link / activity LEDs",
            "Chip side straight to the CH9121T (50 R inside: no 49.9 R); centre taps P4 / P5 to S_GND through 1 uF (maker:\n"
            "no supply on them). Bob-Smith 4 x 75 R + 1000 pF / 2 kV inside, CHS GND and the shield legs to PE (as the bus\n"
            "terminals' screen pins). SRV05-4 on the chip side (0.8 pF). LEDs: green = link, yellow = activity, 470 R.")
    xing.add(B.part("HR913550AE", {"1": "S_ETH_TXP", "2": "S_ETH_TXN", "4": "S_ETH_TCT", "3": "S_ETH_RXP",
                                   "6": "S_ETH_RXN", "5": "S_ETH_RCT", "7": None, "8": "PE", "9": "S_ETH_LINK_A",
                                   "10": "S_ETH_LINK", "12": "S_ETH_ACT_A", "11": "S_ETH_ACT", "SH": "PE"}, dnp=dnp).ref)
    B.C("1u", "S_ETH_TCT", "S_GND", volt="16V", tol="10%", dnp=dnp)
    B.C("1u", "S_ETH_RCT", "S_GND", volt="16V", tol="10%", dnp=dnp)
    B.part("SRV05", {"1": "S_ETH_TXP", "3": "S_ETH_TXN", "4": "S_ETH_RXP", "6": "S_ETH_RXN", "5": "S_3V3",
                     "2": "S_GND"}, dnp=dnp)
    B.R("470R", "S_3V3", "S_ETH_LINK_A", dnp=dnp)
    B.R("470R", "S_3V3", "S_ETH_ACT_A", dnp=dnp)


def domain_of(net):
    if net == "PE":
        return "PE"
    return "SELV" if net.startswith("S_") else "LIVE"


def build_design():
    pins = extract_pins()
    P = plan()
    check_plan(P, pins)
    PARTS["F280039C"] = mcu_entry(pins)
    B = L.Builder(PROJECT, TITLE, REV, DATE, PARTS, rails=RAILS, returns=RETURNS, subtitle=SUBTITLE,
                  comment1="LIVE zone = BUS- (hazardous live); SELV zone behind reinforced barrier B1",
                  comment4="Not bench-validated. All bands and budgets calculated in design_check().",
                  root_notes=ROOT_NOTES or [
                      "ARCHITECTURE-COSTFIRST sections 6.1 (trip sources -> one latch), 10 (two zones); PC "
                      "levels as built on PV-PWR (hardware/PV-PWR/outputs/PV-PWR_design_check.txt).",
                      "Assembly option: the two TLV9024 marked '%s' are not fitted on PV-P75 "
                      "(bom/PV-CTL-P75_BOM.csv)." % PH4,
                      "Assembly option: the Ethernet bridge (sheet 08_eth) is NOT FITTED on PV-CTL / PV-CTL-P75 (DNP); fitted on "
                      "PCS-CTL. Debug header in cJTAG mode only.",
                      "Pin plan: gen/data/pv_ctrl_pin_plan.csv (from SPRSP61C, checked every build)."])
    iso, xing = set(), set()
    sheet_supply(B)
    sheet_mcu(B, mcu_pins(pins, P))
    sheet_afe(B)
    sheet_trip(B)
    sheet_latch(B)
    sheet_iso(B, iso, xing)
    sheet_selv(B, iso, xing)
    for f in EXTRA_SHEETS:
        f(B)
    sheet_eth(B, xing)
    return B, pins, P, iso, xing


# ============================================================================ 5. design check (calculated, not tested)
CS = json.load(open(os.path.join(L.REPO, "sim/out/pv_control/control_spec.json")))
CELL = json.load(open(os.path.join(L.REPO, "sim/out/pv_design/cell_spec.json")))
MOD = json.load(open(os.path.join(L.REPO, "sim/out/pv_design/module_spec.json")))      # cold-start rule (PCM-17); stamped in the PV-PWR check
PSL = json.load(open(os.path.join(L.REPO, "sim/out/port_design/port_spec.json")))["lean"]
INS = json.load(open(os.path.join(L.REPO, "sim/out/insulation/insulation_spec.json")))
PWR_TXT = os.path.join(L.REPO, "hardware/PV-PWR/outputs/PV-PWR_design_check.txt")
LSB = 3.0 / 4096
REF_E = 0.001 + 20e-6 * 115 + 100e-6 + 15e-6 * 7 + 50e-6     # REF3030E (SBVS032K 6.5): initial, box drift -30..85 C,
#                                                             hysteresis, load regulation (7 mA), long term
R01 = 0.001 + 25e-6 * 60                                    # one 0.1 % 25 ppm/K resistor over +/-60 K
R01_LOW_TCR = 0.001 + 10e-6 * 60                            # the same with a 10 ppm/K part (a remedy considered in the IL window item)
LADDER_TERMS = ("r1", "r2", "ra", "rb", "rc")               # the resistors of the IL divider and ladder: the only terms a value change touches
VIO_CMP = 2e-3                                              # TLV9024 VOS -40..125 C (SNOSDA3H 5.7), IB 5 pA; specified at VCM = (V-) = 0 V
# TLV9024 common-mode error (PCM-19, risk C9): CMRR >= 60 dB at VS = 5 V and >= 50 dB at VS = 1.8 V over (V-) - 0.2 V ... (V+) + 0.2 V and
# -40..125 C (SNOSDA3H 5.7). The comparators run from +3V3 (3.24-3.43 V): between the two specified supplies and not specified itself, so
# the LOWER guarantee is used. dV_os = (V_cm - V_cm,spec) / CMRR is added to every comparator band (the VOS spec is at 0 V common mode).
VCM_SPEC, CMRR_MIN_DB, V_CM_MAX = 0.0, 50.0, 3.24 + 0.2     # V, dB, V ((V+) min + 0.2 V input range)
# TLV9024 propagation delay, high to low (the trip direction of every comparator here), VS = 3.3 V, the slowest of the -40..125 C curves
# (125 C), read off SNOSDA3H Fig. 5-19 (+-10 ns): (overdrive mV, ns). Typical curve, no maximum is published: x T_CMP_FACTOR ASSUMED.
TPD_HL = ((5, 458), (10, 350), (20, 248), (25, 215), (50, 148), (70, 125), (100, 110), (200, 90), (500, 82), (1000, 82))
T_CMP_FACTOR = 2.0
T_LOGIC = 6e-9                                              # LVC07 / LVC1G74 / LVC08 per stage at 3.3 V (max)
T_PB = 8e-9 + 110e-9 + 52e-9                                # power board: AHCT1G08 + NSI6651 tprop max (gdrv.NSI)
#                                                             + SG2M040170HJ turn-off (gen/pvcell.py DEV)
CMPSS = dict(gain=0.02, inl=16, t=60e-9, filt=5 / 120e6)    # SPRSP61C 6.13.5.3: DAC gain 2 % FSR, INL 16 LSB, comparator;
#                                                             digital filter 5 SYSCLK (window 5, threshold 4) ASSUMED
T_TZ = 25e-9                                                # ePWM trip-zone action
IL_TOP_MAX = 79.9            # A: IL window top cap (D-056); the device peak at the top vs 0.85 V_DSS is cell_spec trip_band_costfirst (printed by the window's open item)
# variant knobs (gen/pcs_ctrl.py sets them for the inverter; the PV values here)
IL_LO_FACTOR, I_BK, LATCH_I, P75_SUFFIX, LATCH_VA, V_PPB = 1.10, 91.5, 85.0, "-P75", 800.0, 1100.0
HOLD_KEEPS = ("K_A", "K_B")                                 # contactor commands a trip keeps while HOLD is high
IL_NOM_MAX = None                                           # nominal IL trip limit (None = the sensor's I_PN)
# STK-HO/A 75 (Sinomags STK-HO-A.pdf sec. 3): Voff 2.48-2.52 V; X @ 25 C +/-1 % of Ipn (+/-1.5 % of Ipm above Ipn);
# X_TRange -40..105 C +/-3 % of Ipn (of Ipm above Ipn): Voe drift, gain drift and linearity vs the 25 C fitted gain
SENS = dict(ipn=75.0, ipm=187.5, x25=(0.01, 0.015), xt=0.03)
RT8016 = [(-40, 33.65), (-30, 17.7), (-20, 9.707), (-10, 5.533), (0, 3.265), (10, 1.99), (20, 1.249), (25, 1.0),
          (30, 0.8057), (40, 0.5327), (50, 0.3603), (60, 0.2488), (70, 0.1752), (80, 0.1258), (85, 0.1072),
          (90, 0.09177), (95, 0.07885), (100, 0.068), (110, 0.05112), (120, 0.03893), (130, 0.03009), (140, 0.02348),
          (145, 0.02083), (150, 0.01853), (155, 0.01653)]   # TDK B57703M p4, R/T curve 8016 (RT / R25), B25/100 3988 K
NTC_TOL = {"hs": (0.01, 0.01), "ind": (0.02, 0.01)}         # R25, B: PV-PWR heatsink probe 1 %; inductor NTC 2 % (pvcell)
T_AMB_MIN = -30.0                                           # PV-20


def say(ok, name, detail):
    print("[%s] %s - %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def info(name, detail):
    print("[INFO] %s - %s" % (name, detail))


def open_item(name, detail):
    """a design-margin rule that is NOT met: printed loudly with its shortfall, does not fail the build (the hard limits stay in say())"""
    print("[OPEN] %s - %s" % (name, detail))


def val(s):
    return L.number(s)


def cm_err(v_cm, db=None):
    """TLV9024 common-mode offset error (V): |V_cm - V_cm,spec| / CMRR at the comparator's input common mode v_cm; asserts that the
    node sits inside the specified range (V-) - 0.2 V ... (V+) min + 0.2 V (outside it the CMRR is not specified)"""
    assert -0.2 <= v_cm <= V_CM_MAX, "comparator input at %.3f V is outside the TLV9024 common-mode range" % v_cm
    return abs(v_cm - VCM_SPEC) / 10 ** ((CMRR_MIN_DB if db is None else db) / 20)


def tpd_hl(od):
    """TLV9024 typical propagation delay (s), high to low, at an input overdrive od (V): log-linear through TPD_HL, clamped to its ends"""
    od_mv = min(max(od * 1e3, TPD_HL[0][0]), TPD_HL[-1][0])
    for (o1, t1), (o2, t2) in zip(TPD_HL, TPD_HL[1:]):
        if o1 <= od_mv <= o2:
            return (t1 + (t2 - t1) * math.log(od_mv / o1) / math.log(o2 / o1)) * 1e-9


def t_cmp(slope):
    """comparator delay (s) used for an input that ramps at `slope` V/s through the threshold: the data-sheet delay at the overdrive the
    ramp has built up by the time the output switches (fixed point t = tpd(slope t)), x T_CMP_FACTOR; also returns (typical delay, overdrive V)"""
    t = 0.3e-6
    for _ in range(80):
        t = 0.5 * (t + tpd_hl(slope * t))
    return T_CMP_FACTOR * t, t, slope * t


def r_ntc(t):
    for (t1, r1), (t2, r2) in zip(RT8016, RT8016[1:]):
        if t1 <= t <= t2:
            return 10e3 * r1 * (r2 / r1) ** ((t - t1) / (t2 - t1))
    raise ValueError(t)


def t_ntc(r):
    lo, hi = -40.0, 155.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if r_ntc(mid) > r else (lo, mid)
    return lo


def pwr_text(path):
    """the power board's design-check text, refused when it is missing or was built from other spec files than the ones on disk now (the
    'Built from' line that gen/pv_power.py writes: sha256 of every spec file it read): PV-CTL never reads a stale copy"""
    rel = os.path.relpath(path, L.REPO)
    if not os.path.exists(path):
        raise SystemExit("%s is missing: build it first with .venv/bin/python gen/pv_power.py" % rel)
    t = open(path).read()
    m = re.search(r"^Built from \(sha256, first 16 hex\): (.*)$", t, re.M)
    if not m:
        raise SystemExit("%s has no 'Built from' line: rebuild it with .venv/bin/python gen/pv_power.py" % rel)
    stale = []
    for item in m.group(1).split("; "):
        f, h = item.rsplit(" ", 1)
        if not os.path.exists(os.path.join(L.REPO, f)):
            raise SystemExit("%s: input %s is missing" % (rel, f))
        if hashlib.sha256(open(os.path.join(L.REPO, f), "rb").read()).hexdigest()[:16] != h:
            stale.append(f)
    if stale:
        raise SystemExit("%s is stale (built from other versions of %s): rebuild it with .venv/bin/python gen/pv_power.py" %
                         (rel, ", ".join(stale)))
    return t


def pwr_need(pat, t, what):
    m = re.search(pat, t)
    if not m:
        raise SystemExit("%s not found in %s: regenerate it with .venv/bin/python gen/pv_power.py" %
                         (what, os.path.relpath(PWR_TXT, L.REPO)))
    return m


def pwr_levels():
    """The power board's as-built PC levels, parsed from its design-check output (gen/pv_power.py)."""
    t = pwr_text(PWR_TXT)
    il = pwr_need(r"IL1-3 OUT: ([\d.]+) V \(([\d.]+)-([\d.]+)\) \+ ([\d.]+) mV/A", t, "the IL1-3 level").groups()
    ia = pwr_need(r"IA, IB OUT: OPA2388, VMID ([\d.]+)-([\d.]+) V \+ ([\d.]+) mV/A.*?linear \+/-(\d+) A; IA_H, IB_H: "
                  r"VMID \+ ([\d.]+) mV/A, \+/-(\d+) A", t, "the IA / IB level").groups()
    sn = pwr_need(r"linear \+/-([\d.]+) A >= range.*?delay ([\d.]+) us \+ RC ([\d.]+) us", t, "the sensor chain line").groups()
    ur = pwr_need(r"IL1R-\dR OUT: sensor reference Uref ([\d.]+)-([\d.]+) V \(STK pin 4; ILn - ILnR = [\d.]+ \+/- "
                  r"([\d.]+) mV.*?source (\d+)-(\d+) ohm", t, "the ILnR level").groups()
    rs = pwr_need(r"each IL line into [\d.]+ kOhm: source (\d+)-(\d+) ohm", t, "the IL source resistance").groups()
    bw = pwr_need(r"IA / IB bandwidth \(lean_shunt feedback ([\d.]+)k \|\| ([\d.]+)p\): ([\d.]+) kHz", t, "the IA / IB bandwidth").groups()
    return dict(v0=float(il[0]), v0_rng=(float(il[1]), float(il[2])), g=float(il[3]) * 1e-3,
                uref=(float(ur[0]), float(ur[1])), voe=float(ur[2]) * 1e-3, rs_ilr=(float(ur[3]), float(ur[4])),
                rs_il=(float(rs[0]), float(rs[1])), tau_ia=float(bw[0]) * 1e3 * float(bw[1]) * 1e-12, f_ia=float(bw[2]) * 1e3,
                vmid=(float(ia[0]), float(ia[1])), k_ia=float(ia[2]) * 1e-3, ia_lin=float(ia[3]),
                k_ih=float(ia[4]) * 1e-3, ih_lin=float(ia[5]), il_lin=float(sn[0]), t_sens=float(sn[1]) * 1e-6,
                t_rc=float(sn[2]) * 1e-6,
                k_div=1.0 / float(pwr_need(r"VA, VB OUT \(bank side\): VMID \+ V/(\d+)", t, "the VA / VB divider").group(1)),
                alloc=[float(x) * 1e-3 for x in
                       pwr_need(r"control board allocation (\d+) / (\d+) / (\d+) mA", t, "the control board allocation").groups()],
                flt_pullup="control board MUST pull up to its 3.3 V (4.99k recommended)" in t,
                rdy_pullup_on_pwr=bool(re.search(r"RDY OUT: open drain.*?pulled up HERE", t)),
                hs_max=float(pwr_need(r"heatsink OT trip must be <= ([\d.]+) C", t, "the heatsink OT limit").group(1)),
                sink=float(pwr_need(r"-> sink ([\d.]+) C at 45 C inlet", t, "the full-load sink temperature").group(1)),
                hot=tuple(float(x) for x in pwr_need(r"hot spot (\d+) C <= (\d+) C", t, "the inductor hot spot").groups()),
                port_oc_on_flt="port OC (live)" in t and "open drain, 12 NSI6651 /FLT" in t,
                il4_zero="IL4 = 0 V on PV-P75" in t and "IL4R = 0 V on PV-P75" in t,
                ntc_open="NTC4 / NTC8 open on PV-P75" in t)


def pwr_fan_contract(path):
    """the power board's fan supply contract (gen/pv_power.py 'Fan supply contract' line): the SELV minimum at its worst cross-regulation
    point, what the fan buck then passes, at which current, the voltage the thermal model's fans need, and the resulting airflow ratio
    against the limit that the power board's thermal line holds (inside its thermal margin)"""
    m = re.search(r"Fan supply contract: S_24V >= ([\d.]+) V at the worst cross-regulation point, fan buck passes >= ([\d.]+) V at "
                  r"([\d.]+) A, fans need ([\d.]+) V \(airflow x([\d.]+), limit x([\d.]+)\)", pwr_text(path))
    if not m:
        raise SystemExit("no 'Fan supply contract' line in %s: regenerate it with .venv/bin/python gen/pv_power.py" %
                         os.path.relpath(path, L.REPO))
    return dict(zip(("s_min", "v_pass", "i", "v_need", "flow", "flow_min"), map(float, m.groups())))


def lad_th(key):
    """Nominal tap voltages of a ladder and the worst ratio error of each tap (resistor tolerance + drift)."""
    r = [val(x) for x in LADDER[key]]
    return [(3.0 * sum(r[i:]) / sum(r), (1 - sum(r[i:]) / sum(r)) * 2 * R01) for i in range(1, len(r))]


def ntc_v(t, bias, kr=1.0, kb=1.0):
    r = r_ntc(t) * kr * math.exp((kb - 1) * 3988.0 * (1 / (t + 273.15) - 1 / 298.15))
    return 3.0 * r / (val(bias) + r)


def thermal_band(bias, th_key, tol):
    """OT trip temperature band: NTC R25 / B tolerance, bias and ladder (0.1 %), VIO; ratiometric to VREF."""
    th, err = lad_th(th_key)[0]
    rb, temps = val(bias), []
    for kr in (-1, 1):
        for kb in (-1, 1):
            for kv in (-1, 1):
                v = th * (1 + kv * err) + kv * (VIO_CMP + cm_err(th))
                r = rb * (1 + kv * R01) * v / (3.0 - v)
                t0 = t_ntc(r / (1 + kr * tol[0]))
                f = math.exp(kb * tol[1] * 3988.0 * (1 / (t0 + 273.15) - 1 / 298.15))
                temps.append(t_ntc(r / (1 + kr * tol[0]) / f))
    return t_ntc(rb * th / (3.0 - th)), min(temps), max(temps)


def il_numbers(pw):
    a = val(IL_NET[1]) / (val(IL_NET[0]) + val(IL_NET[1]))
    k = val(IL_FB[1]) / val(IL_FB[0])
    rth = val(IL_NET[0]) * val(IL_NET[1]) / (val(IL_NET[0]) + val(IL_NET[1]))
    c_node = val(IL_NODE_C) + 5e-12 + 2 * 3e-12              # + op-amp and comparator inputs (ASSUMED 5 / 3 pF)
    return dict(a=a, k=k, g_adc=a * (1 + k) * pw["g"], z_adc=a * (1 + k) * pw["v0"] - 3.0 * k, rth=rth, tau=rth * c_node,
                g_cmp=a * pw["g"], z_cmp=a * pw["v0"])


def sens_err(i, cal):
    """STK-HO/A 75 error at |i| (A): X_TRange, + X @ 25 C when not calibrated (% of Ipn, of Ipm above Ipn); offsets apart"""
    big = abs(i) > SENS["ipn"]
    return (SENS["xt"] + (0 if cal else SENS["x25"][big])) * (SENS["ipm"] if big else SENS["ipn"])


def il_nodes(p):
    """TH_ILn_HI / TH_ILn_LO of the rev A1 ladder: ILnR (source rsr) - ra - HI - rb - LO - rc - GND, rpu from VREF to LO"""
    gt, gb, gc, gp = 1 / (p["rsr"] + p["ra"]), 1 / p["rb"], 1 / p["rc"], 1 / p["rpu"]
    a11, a22, b1, b2 = gt + gb, gb + gc + gp, gt * p["uref"], gp * p["vref"]
    det = a11 * a22 - gb * gb
    return (b1 * a22 + gb * b2) / det, (a11 * b2 + gb * b1) / det


def il_trip(p, hi):
    """|I| at which the IL comparator of one direction switches: ILn = Uref + Voe + G I through the PV-PWR source rsi
    into the 10.0k / 29.4k node, against the ladder tap (comparator offset towards a later trip when vio > 0)"""
    h, lo = il_nodes(p)
    e = p["vio"] + p.get("vcm", 0.0)
    v = h + e if hi else lo - e
    return abs((v * (p["r1"] + p["r2"] + p["rsi"]) / p["r2"] - p["uref"] - p["voe"]) / p["g"])


def il_window(pw, cmrr_db=None, r01=None):
    """Both trip directions: (nominal, low, high, {term: +/- A}); worst case = linear sum of every term's swing. 'vcm' = the TLV9024
    common-mode error (CMRR >= cmrr_db, default CMRR_MIN_DB) at that comparator's own input common mode (TH_ILn_HI / TH_ILn_LO);
    r01 = tolerance of one ladder / divider resistor (default R01)"""
    r01 = R01 if r01 is None else r01
    lad = [val(x) for x in ILR_LAD]
    nom = dict(uref=sum(pw["uref"]) / 2, voe=0.0, g=pw["g"], r1=val(IL_NET[0]), r2=val(IL_NET[1]), ra=lad[0], rb=lad[1],
               rc=lad[2], rsi=sum(pw["rs_il"]) / 2, rsr=sum(pw["rs_ilr"]) / 2, vio=0.0, vcm=0.0, vref=3.0, rpu=val(ILR_PU))
    tol = dict(uref=(pw["uref"][1] - pw["uref"][0]) / 2, voe=pw["voe"], vio=VIO_CMP, vref=3.0 * REF_E,
               rsi=(pw["rs_il"][1] - pw["rs_il"][0]) / 2, rsr=(pw["rs_ilr"][1] - pw["rs_ilr"][0]) / 2,
               rpu=0.016 * nom["rpu"], **{k: r01 * nom[k] for k in LADDER_TERMS})
    out = []
    h0, l0 = il_nodes(nom)
    for hi in (True, False):
        i0, terms = il_trip(nom, hi), {}
        for k, d in dict(tol, vcm=cm_err(h0 if hi else l0, cmrr_db)).items():
            terms[k] = abs(il_trip(dict(nom, **{k: nom[k] + d}), hi) - il_trip(dict(nom, **{k: nom[k] - d}), hi)) / 2
        terms["X"] = sens_err(i0, False)
        di = sum(terms.values())
        out.append((i0, i0 - di, i0 + di, terms, h0 if hi else l0))
    dead = il_nodes(dict(nom, uref=0.0, vref=3.0 * (1 - REF_E), rpu=nom["rpu"] * 1.016))[1]   # sensor dead: IL = ILR = 0
    return out, dead


def il_band(pw, cmrr_db=None, r01=None):
    """(bottom, top) of the IL window over both directions"""
    band, _ = il_window(pw, cmrr_db, r01)
    return min(b[1] for b in band), max(b[2] for b in band)


def check_trips(pw):
    """Every hardware trip: band (all tolerances) and response against control_spec / cell_spec / PV-PWR as built."""
    ok, rows = True, []
    hw, mr = CS["hardware_trips"], CS["measurement_requirements"]
    io = hw["inductor_overcurrent"]
    didt = io["didt_max_A_per_us"] * 1e6
    i_lim, i_pk = CELL["inductor"]["I_at_50pct_L0_A"], CELL["inductor"]["I_peak_normal_max_A"]
    n = il_numbers(pw)
    s_err = sens_err
    band, dead = il_window(pw)
    lo_l, hi_l = min(b[1] for b in band), max(b[2] for b in band)
    tw = {k: max(b[3][k] for b in band) for k in band[0][3]}
    t_c, t_typ, od_c = t_cmp(didt * n["g_cmp"])
    t_loc = pw["t_sens"] + pw["t_rc"] + n["tau"] + t_c + 3 * T_LOGIC + T_PB
    i_bk = I_BK
    d = i_bk * n["g_adc"]
    db = (CMPSS["gain"] * d + 2 * CMPSS["inl"] * LSB + d * REF_E) / n["g_adc"] + i_bk * 2 * 25e-6 * 60 + s_err(i_bk, True)
    lo_b, hi_b = i_bk - db, i_bk + db
    t_bk = (pw["t_sens"] + pw["t_rc"] + n["tau"] + val(IL_RC[0]) * val(IL_RC[1]) + 0.1e-6 + CMPSS["t"] + CMPSS["filt"] +
            T_TZ + T_PB)
    hard = (hi_l < lo_b and hi_l + didt * t_loc <= i_lim and lo_l > i_pk and t_loc <= io["local"]["max_response_us"] * 1e-6 and
            hi_l * 1.05 < pw["il_lin"] and dead > 2 * VIO_CMP and max(b[0] for b in band) <= (IL_NOM_MAX or SENS["ipn"]))
    ok &= say(hard, "Trip IL local window (TLV9024 on ILn_P, both directions, thresholds from each sensor's Uref)",
              "+%.1f / -%.1f A nominal -> %.1f-%.1f A, %.1f %% above the normal peak %.1f A. Worst-case terms (A, larger "
              "direction): sensor X 1 %% + 3 %% of Ipn %.2f, Voe %.2f, Uref 2.48-2.52 V %.2f (only the offset part scales with "
              "it), 10.0k/29.4k divider %.2f, Uref ladder %.2f, comparator VOS %.2f, TLV9024 common-mode error %.2f (+) / %.2f (-) "
              "(CMRR >= %.0f dB, the lower of its 5 V / 1.8 V guarantees; V_cm %.2f / %.2f V = %.1f / %.1f mV; it scales with the "
              "signal, so no divider value changes it), PV-PWR sources (R_out / R_ref + 100R) %.2f, VREF + 1 M pull-up %.2f; no "
              "hysteresis (the latch holds the trip). Hard conditions (pass / fail): lowest threshold above the normal peak, top "
              "below the backup %.1f A, dead sensor (IL = ILR = 0 V): TH_LO %.0f mV above the node -> trips, sensor linear to %.0f "
              "A, gates off within the control_spec limit. Gates off %.2f us (sensor %.2f + PV-PWR RC %.2f + node %.2f + "
              "comparator %.2f [TLV9024 %.0f ns typ at the %.0f mV overdrive that the %.1f A/us ramp builds, SNOSDA3H Fig. 5-19 "
              "at 3.3 V / 125 C, x%.0f ASSUMED because no maximum is published; was 1.0 us ASSUMED] + logic + driver) <= %.2f us "
              "(control_spec) -> %.1f A <= %.0f A (50 %% L0)"
              % (band[0][0], band[1][0], lo_l, hi_l, 100 * (lo_l / i_pk - 1), i_pk, tw["X"], tw["voe"], tw["uref"],
                 tw["r1"] + tw["r2"], tw["ra"] + tw["rb"] + tw["rc"], tw["vio"], band[0][3]["vcm"], band[1][3]["vcm"],
                 CMRR_MIN_DB, band[0][4], band[1][4], 1e3 * cm_err(band[0][4]), 1e3 * cm_err(band[1][4]), tw["rsi"] + tw["rsr"],
                 tw["vref"] + tw["rpu"], lo_b, dead * 1e3, pw["il_lin"], t_loc * 1e6, pw["t_sens"] * 1e6, pw["t_rc"] * 1e6,
                 n["tau"] * 1e6, t_c * 1e6, t_typ * 1e9, od_c * 1e3, didt * 1e-6,
                 T_CMP_FACTOR, io["local"]["max_response_us"], hi_l + didt * t_loc, i_lim))
    corridor = IL_TOP_MAX - IL_LO_FACTOR * i_pk
    sh_lo, sh_hi = IL_LO_FACTOR * i_pk - lo_l, hi_l - IL_TOP_MAX
    if max(sh_lo, sh_hi) > 1e-9:
        # the corridor [1.10 x normal peak, top cap] against the band: no resistor value changes the terms below, so no ladder fits
        w_max = max(sum(b[3].values()) for b in band)
        fixed = max(sum(v for k, v in b[3].items() if k not in LADDER_TERMS) for b in band)
        x_sens = max(b[3]["X"] + b[3]["voe"] + b[3]["uref"] for b in band)
        alt = []
        for db_, r01_, tag in ((CMRR_MIN_DB, R01, "CMRR %.0f dB, 25 ppm/K (as drawn)" % CMRR_MIN_DB),
                               (60.0, R01, "CMRR 60 dB, 25 ppm/K"), (CMRR_MIN_DB, R01_LOW_TCR, "CMRR %.0f dB, 10 ppm/K" % CMRR_MIN_DB),
                               (60.0, R01_LOW_TCR, "CMRR 60 dB, 10 ppm/K")):
            lo_a, hi_a = il_band(pw, db_, r01_)
            alt.append("%s: %.1f-%.1f A (%.2f A per side beyond the corridor after re-centring)" %
                       (tag, lo_a, hi_a, max(0.5 * (hi_a - lo_a) - 0.5 * corridor, 0.0)))
        phys = ""
        if "device_primary" in CELL:        # the physical consequence at this band top (cell_spec trip_band_costfirst, calculated)
            r_ = CELL["device_primary"]["trip_band_costfirst"]["hardware"]
            phys = ("; at this top the gates-off peak is %.1f A against %.0f A (50 %% L0) and the device peak %.0f V against %.0f V "
                    "(cell_spec trip_band_costfirst), so the margin the top cap protects is not used up" %
                    (hi_l + didt * t_loc, i_lim, r_["v_pk_V"], r_["v_pk_limit_V"]))
        open_item("Trip IL local window - design-margin rules NOT met at the guaranteed CMRR (risk C9)",
                  "needs bottom >= %.1f A (%.2f x normal peak %.1f A) and top <= %.1f A (corridor %.1f A, half-width %.2f A) but the "
                  "band is %.1f-%.1f A (half-width %.2f A, already centred: +%.1f / -%.1f A nominal): %.2f A short at the bottom, "
                  "%.2f A over at the top, the top still %.1f A below the backup %.1f A; the hard conditions hold%s. NO ladder or "
                  "divider value restores the corridor: the terms that no resistor value changes (sensor X + Voe + Uref %.2f A, "
                  "comparator VOS and TLV9024 common-mode error %.2f A, PV-PWR sources, VREF + pull-up) add up to %.2f A against "
                  "the corridor's half-width. Variants (calculated): %s. Closes with: the CMRR measured on sample parts at 3.3 V "
                  "over -40..125 C, 10 ppm/K divider and ladder resistors, a better sensor, an end-of-line threshold trim (D-058), "
                  "or the top cap raised toward the backup's %.1f A (the data-sheet comparator delay already lowers the gates-off "
                  "peak by %.1f A against the earlier 1.0 us)"
                  % (IL_LO_FACTOR * i_pk, IL_LO_FACTOR, i_pk, IL_TOP_MAX, corridor, 0.5 * corridor, lo_l, hi_l, w_max, band[0][0],
                     band[1][0], max(sh_lo, 0.0), max(sh_hi, 0.0), lo_b - hi_l, lo_b, phys, x_sens,
                     max(b[3]["vio"] + b[3]["vcm"] for b in band), fixed, "; ".join(alt), lo_b, didt * (1.0e-6 - t_c)))
    if "device_primary" in CELL:               # the PV cell_spec (an assembly variant with its own CELL has no device blocks): a missing record is an error
        rec = CELL["device_primary"]["trip_band_costfirst"]["hardware"]
        ok &= say([round(lo_l, 1), round(hi_l, 1)] == rec["band_A"] and abs(rec["response_us"] - t_loc * 1e6) < 0.006,
                  "Record: cell_spec trip_band_costfirst (sim/pv_design.py TRIP_HW, read by sim/pv_module.py) = this window",
                  "cell_spec %s A, %.2f us; this board %.1f-%.1f A, %.2f us%s" % (rec["band_A"], rec["response_us"], lo_l, hi_l,
                                                                                  t_loc * 1e6, "" if [round(lo_l, 1), round(hi_l, 1)]
                                                                                  == rec["band_A"] and abs(rec["response_us"] - t_loc * 1e6) < 0.006
                                                                                  else " - update TRIP_HW and re-run sim/pv_design.py"))
    bk_req = mr["cell_inductor_current"]["comparator_threshold_band_A"]            # control_spec: the band the backup layer is held to
    ok &= say(lo_b > hi_l and hi_b + didt * t_bk <= i_lim and t_bk <= io["ctrl_backup"]["max_response_us"] * 1e-6 and
              bk_req[0] - 0.05 <= lo_b and hi_b <= bk_req[1] + 0.05,
              "Trip IL backup (CMPSS1-4 windows on ILn_ADC, DAC set after the idle null and calibration)",
              "+/-%.1f A -> %.1f-%.1f A (DAC 2 %% + 2 x 16 LSB, VREF, resistor drift, sensor X over temperature) inside the "
              "control_spec band %.1f-%.1f A; gates off %.2f us <= %.2f us (control_spec) -> %.1f A <= %.0f A" %
              (i_bk, lo_b, hi_b, *bk_req, t_bk * 1e6, io["ctrl_backup"]["max_response_us"], hi_b + didt * t_bk, i_lim))
    rows += [("IL1-4 local window", "+%.1f/-%.1f A" % (band[0][0], band[1][0]), "%.1f-%.1f A" % (lo_l, hi_l),
              "%.2f us" % (t_loc * 1e6), ">= %.1f A, <= %.1f A, <= %.2f us" % (IL_LO_FACTOR * i_pk, IL_TOP_MAX,
                                                                         io["local"]["max_response_us"])),
             ("IL1-4 CMPSS backup", "+/-%.1f A (DAC)" % i_bk, "%.1f-%.1f A" % (lo_b, hi_b), "%.2f us" % (t_bk * 1e6),
              "%.1f-%.1f A, <= %.2f us" % (*bk_req, io["ctrl_backup"]["max_response_us"]))]
    # ---- port over-current: PV-PWR comparators on FLT_N (primary); CMPSS4 on IB (3 phases) / ADC PPB (backup)
    want = PSL["port_oc_trip_A"]
    tau_ia = pw["tau_ia"]                                             # gen/port.py lean_shunt output pole (PV-PWR as built)
    t_ip_ppb = tau_ia + 31.25e-6 / 2 + 0.3e-6 + T_TZ + T_PB           # ramp lag + conversion twice per PWM period
    t_ip_cmp = tau_ia + 0.1e-6 + CMPSS["t"] + CMPSS["filt"] + T_TZ + T_PB
    d_ip = 400.0 * pw["k_ia"]
    chain = 0.01 + 150e-6 * 80 + 2 * 0.001                           # shunt 1 %, TCR, gain resistors (port_design LEAN)
    db_ip = (CMPSS["gain"] * d_ip + 2 * CMPSS["inl"] * LSB + d_ip * REF_E) / pw["k_ia"] + 400.0 * chain
    oc_req = hw["port_overcurrent_A"]["OC_band_A"]                                 # control_spec: the band the control study works with
    ok &= say(pw["port_oc_on_flt"] and 400 + db_ip < pw["ia_lin"] and [round(x) for x in want] == [round(x) for x in oc_req],
              "Trip port over-current (PV-PWR comparators -> FLT_N; backup here)",
              "primary %d-%d A (= control_spec %d-%d A) on the power board's own window (lean_shunt IH path) arrives on FLT_N "
              "with no firmware; backup: CMPSS4 on IB (3 phases) or ADC PPB on IA / IB, +/-400 A after the idle null -> "
              "%.0f-%.0f A, inside the %d A linear range of IA" % (want[0], want[1], oc_req[0], oc_req[1], 400 - db_ip, 400 + db_ip,
                                                                   pw["ia_lin"]))
    rows += [("port OC (PV-PWR, on FLT_N)", "+/-400 A", "%d-%d A" % tuple(want), "%.2f us (logic)" % (3 * T_LOGIC * 1e6 +
                                                                                                   T_PB * 1e6),
              "%d-%d A (control_spec)" % tuple(oc_req)),
             ("port OC backup", "+/-400 A (CMPSS4 on IB / PPB)", "%.0f-%.0f A" % (400 - db_ip, 400 + db_ip),
              "%.0f us PPB / %.0f us CMPSS4" % (t_ip_ppb * 1e6, t_ip_cmp * 1e6), "-")]
    # ---- port over-voltage on VA / VB
    ov = hw["port_overvoltage"]
    v_lim = float(re.search(r"<= (\d+) V", ov["basis"]).group(1))
    (th_ov, e_ov), = lad_th("OV")
    vmid = sum(pw["vmid"]) / 2
    v_nom = (th_ov - vmid) / pw["k_div"]
    e_div = 2 * 0.001 + 2 * 25e-6 * 40                                # 6 x 1 M ARHV06 + 4.99k (port_design)
    dv = th_ov * (REF_E + e_ov) + VIO_CMP + cm_err(th_ov) + (pw["vmid"][1] - pw["vmid"][0]) / 2
    lo_v, hi_v = v_nom - dv / pw["k_div"] - v_nom * e_div, v_nom + dv / pw["k_div"] + v_nom * e_div
    tau_div = 4.99e3 * 6e6 / (6e6 + 4.99e3) * 4.7e-9                   # lean_dividers 6 M / 4.99k / 4.7 nF
    t_ov = T_CMP_FACTOR * tpd_hl(5e-3)                                # delay at the 5 mV overdrive of the build-up model below
    pv_ = mr["port_voltage_VA_VB"]
    # the two ramps the lag is held to: the full-power load rejection (design ramp 0.27 V/us: 75 A into the 270 uF bank of the earlier
    # build; the control study now quotes 0.22 V/us at 82.5 kW into 334.8 uF - the slower ramp adds 4.6 us of build-up, inside the limit) and
    # the frozen-current case of control_spec; each against its own lag limit
    worst, lag_w, lag_ok = 0.0, 0.0, True
    for slope, lag_max in ((0.27, pv_["ov_detection_lag_us_max"]), (ov["full_current_frozen_case"]["slope_V_per_us"],
                                                                    pv_["ov_detection_lag_us_max_full_current_case"])):
        lag = tau_div + 5e-3 / (slope * 1e6 * pw["k_div"]) + t_ov + 3 * T_LOGIC + T_PB
        worst, lag_w, lag_ok = max(worst, hi_v + slope * 1e6 * lag), max(lag_w, lag), lag_ok and lag <= lag_max * 1e-6
    ok &= say(worst <= v_lim and lag_ok,
              "Trip port over-voltage (TLV9024 on VA and VB)",
              "%.0f V nominal -> %.0f-%.0f V (divider %.1f %%, VMID %.3f-%.3f V, REF, ladder, VIO, TLV9024 common-mode error "
              "%.1f V at V_cm %.2f V, CMRR >= %.0f dB); <= %.0f V at gates-off: band top + slope x lag = %.0f V; lag %.0f us "
              "(RC %.1f us + 5 mV overdrive build-up + comparator %.2f us [TLV9024 %.0f ns typ at 5 mV, SNOSDA3H Fig. 5-19, "
              "x%.0f ASSUMED]) <= %.1f us (full power) / %.1f us (frozen current), control_spec" %
              (v_nom, lo_v, hi_v, e_div * 100, pw["vmid"][0], pw["vmid"][1], cm_err(th_ov) / pw["k_div"], th_ov, CMRR_MIN_DB, v_lim,
               worst, lag_w * 1e6, tau_div * 1e6, t_ov * 1e6, tpd_hl(5e-3) * 1e9, T_CMP_FACTOR, pv_["ov_detection_lag_us_max"],
               pv_["ov_detection_lag_us_max_full_current_case"]))
    if "device_primary" in CELL:
        rec_ov = CELL["ov_trip_costfirst"]
        ok &= say([round(lo_v), round(hi_v)] == rec_ov["band_V"] and abs(rec_ov["response_us"] - lag_w * 1e6) < 1.0,
                  "Record: cell_spec ov_trip_costfirst (sim/pv_design.py OV_HW, read by sim/pv_module.py bank study) = this band",
                  "cell_spec %s V, %.0f us; this board %.0f-%.0f V, %.1f us%s" % (rec_ov["band_V"], rec_ov["response_us"], lo_v, hi_v,
                                                                                 lag_w * 1e6, "" if [round(lo_v), round(hi_v)] ==
                                                                                 rec_ov["band_V"] else
                                                                                 " - update OV_HW and re-run sim/pv_design.py"))
    if lo_v <= hw["firmware_overvoltage_V"]:
        open_item("Trip port over-voltage - band bottom below the firmware OV (risk C9)",
                  "the band bottom %.0f V is %.1f V below the firmware's %.0f V controlled stop: in that corner the hardware latch "
                  "trips first (a latched trip instead of a stop, no loss of protection); the cause is the TLV9024 common-mode "
                  "error (%.1f V at the port); with CMRR 60 dB it would be %.0f V" %
                  (lo_v, hw["firmware_overvoltage_V"] - lo_v, hw["firmware_overvoltage_V"], cm_err(th_ov) / pw["k_div"],
                   lo_v + (cm_err(th_ov) - cm_err(th_ov, 60.0)) / pw["k_div"]))
    rows.append(("port OV VA / VB", "%.0f V" % v_nom, "%.0f-%.0f V" % (lo_v, hi_v), "%.0f us" % (lag_w * 1e6),
                 "<= %.0f V at gates-off (fw %.0f V), <= %.1f us (control_spec)" %
                 (v_lim, hw["firmware_overvoltage_V"], pv_["ov_detection_lag_us_max_full_current_case"])))
    # ---- over-temperature and open probe
    n_h, lo_h, hi_h = thermal_band(NTC_BIAS["hs"], "OTH", NTC_TOL["hs"])
    ok &= say(hi_h <= pw["hs_max"] and lo_h > pw["sink"] + 5, "Trip heatsink over-temperature (NTC1-4)",
              "%.1f C nominal -> %.1f-%.1f C (NTC 1 %% / B 1 %%, bias + ladder 0.1 %%, VIO; ratiometric to VREF); PV-PWR: "
              "<= %.1f C (Tj 107-110 C) and above the %.1f C full-load sink (+5 K)" % (n_h, lo_h, hi_h, pw["hs_max"],
                                                                                     pw["sink"]))
    n_l, lo_ll, hi_ll = thermal_band(NTC_BIAS["ind"], "OTL", NTC_TOL["ind"])
    ok &= say(lo_ll > pw["hot"][0] and hi_ll < pw["hot"][1], "Trip inductor over-temperature (NTC5-8)",
              "%.1f C nominal -> %.1f-%.1f C: above the %.0f C full-load hot spot and below the %.0f C limit (PV-PWR rev "
              "M2); the architecture's 145 C would trip at full load" % (n_l, lo_ll, hi_ll, *pw["hot"]))
    (th_o, e_o), = lad_th("OPEN")
    o_lo, o_hi = th_o * (1 - e_o) - VIO_CMP - cm_err(th_o), th_o * (1 + e_o) + VIO_CMP + cm_err(th_o)
    cold = {k: ntc_v(T_AMB_MIN, NTC_BIAS[k], 1 + NTC_TOL[k][0], 1 + NTC_TOL[k][1]) for k in ("hs", "ind")}
    lock = {k: t_ntc(val(NTC_BIAS[k]) * o_lo / (3.0 - o_lo) / (1 + NTC_TOL[k][0])) for k in ("hs", "ind")}
    ok &= say(max(cold.values()) < o_lo and o_hi < 3.0 * (1 - 1e-4), "Trip open NTC probe (all 8 channels)",
              "TH_OPEN %.4f-%.4f V: an open probe reads VREF (3.000 V) and trips; the coldest valid reading at %.0f C "
              "(R25 and B at their limits) is %.3f V (heatsink) / %.3f V (inductor); readings colder than %s / %s C "
              "also trip (cold lock-out, below PV-20)" % (o_lo, o_hi, T_AMB_MIN, cold["hs"], cold["ind"],
                                                          *("%.0f" % t if t > -39.9 else "-40 (table end)"
                                                            for t in (lock["hs"], lock["ind"]))))
    rows += [("OT heatsink NTC1-4", "%.1f C" % n_h, "%.1f-%.1f C" % (lo_h, hi_h), "thermal (RC 1 ms)", "<= 89.9 C (PV-PWR)"),
             ("OT inductor NTC5-8", "%.1f C" % n_l, "%.1f-%.1f C" % (lo_ll, hi_ll), "thermal", "145 C arch / 155 C max"),
             ("open NTC probe", "> %.3f V" % th_o, "%.3f-%.3f V" % (o_lo, o_hi), "thermal", "PV-PWR request")]
    # ---- heartbeat watchdog (74LVC1G123: tw = K Rext Cext, K 1.0-1.1 from the 10k / 0.1 uF row, SCES586E)
    tw = (val(WD_RC[0]) * 0.99 * val(WD_RC[1]) * 0.95, val(WD_RC[0]) * 1.01 * val(WD_RC[1]) * 1.05 * 1.1)
    ok &= say(2e-3 < tw[0] and tw[1] <= 6e-3, "Trip heartbeat watchdog (74LVC1G123 + TPS3828)",
              "monoflop %.1f-%.1f ms vs a 2 ms ISR edge period (500 Hz) and the architecture's 5 ms; TPS3828 resets the "
              "MCU after 0.9-2.5 s; XRSn low clears the monoflop (every reset trips)" % (tw[0] * 1e3, tw[1] * 1e3))
    rows.append(("watchdog (heartbeat)", "5 ms", "%.1f-%.1f ms" % (tw[0] * 1e3, tw[1] * 1e3), "= tw", "5 ms"))
    # ---- external stop (CA-IS382x VIT+ 2.0 V / VIT- 0.8 V; divider, 5.1 V clamp, RC)
    kd = (val(EN_DIV[0]) + val(EN_DIV[1])) / val(EN_DIV[1])
    v_on, v_off = 2.0 * kd * 1.02, 0.8 * kd * 0.98
    v_contact = S24[0] * (val(EN_DIV[0]) + val(EN_DIV[1])) / (val(EN_DIV[0]) + val(EN_DIV[1]) + 10e3 * 1.01)
    tau = val(EN_DIV[0]) * val(EN_DIV[1]) / (val(EN_DIV[0]) + val(EN_DIV[1])) * val(EN_DIV[2])
    t_stop = tau * math.log(5.4 / 0.8)
    ok &= say(v_on <= 15.0 and v_off >= 3.0 and v_contact >= v_on and t_stop <= 2e-3,
              "Trip external stop (ENABLE, default-low CA-IS3821LG)",
              "ON >= %.1f V (a 24 V PLC output: ON >= 15 V), OFF <= %.1f V; the dry contact fed from S_24V >= %.0f V gives "
              "%.1f V; open contact, cut wire or lost SELV -> STOP_OK low within %.2f ms (RC from the 5.1 V clamp to "
              "0.8 V) + 12 ns isolator, no firmware" % (v_on, v_off, S24[0], v_contact, t_stop * 1e3))
    rows += [("external stop ENABLE", "open contact", "ON >= %.1f V / OFF <= %.1f V" % (v_on, v_off),
              "%.2f ms" % (t_stop * 1e3), "<= 2 ms"),
             ("FLT_N (DESAT, UVLO, port OC)", "low", "logic", "%.0f ns + PV-PWR" % (3 * T_LOGIC * 1e9),
              "%.2f us (driver, control_spec desat)" % hw["desat"]["off_after_detect_us"]),
             ("RDY (gate supplies, +5V, 24 V)", "low", "logic", "%.0f ns" % (3 * T_LOGIC * 1e9),
              "<= %.1f us (control_spec desat)" % hw["desat"]["required_detect_to_off_us"]),
             ("3.3 V supervisor / MCU reset", "2.93 V", "2.88-3.00 V (TPS3828)", "via XRSn -> WD_OK", "-")]
    # ---- firmware layer: ADC PPB limit on every VA / VB conversion (<= 10 us), after the hardware comparator
    v_ppb, t_conv = V_PPB, 10e-6
    dv_ppb = v_ppb * (e_div + REF_E) + (pw["vmid"][1] - pw["vmid"][0]) / 2 / pw["k_div"] + 2 * LSB / pw["k_div"]
    lag_p = tau_div + t_conv + 0.3e-6 + T_TZ + T_PB
    end_p = max(v_ppb + dv_ppb + sl * 1e6 * lag_p for sl in (0.27, ov["full_current_frozen_case"]["slope_V_per_us"]))
    ok &= say(end_p <= v_lim and lag_p <= mr["port_voltage_VA_VB"]["ov_detection_lag_us_max"] * 1e-6,
              "Backup port over-voltage (ADC PPB on VA_ADC / VB_ADC, firmware-set)",
              "%.0f V -> %.0f-%.0f V (divider, REF, VMID +/-5 mV, 2 LSB), converted every %.0f us: lag %.1f us <= "
              "%.1f us, %.0f V at gates-off <= %.0f V (also in the frozen-current case)" %
              (v_ppb, v_ppb - dv_ppb, v_ppb + dv_ppb, t_conv * 1e6, lag_p * 1e6,
               mr["port_voltage_VA_VB"]["ov_detection_lag_us_max"], end_p, v_lim))
    if v_ppb - dv_ppb <= hi_v - 30:
        open_item("Backup port over-voltage - ordering against the hardware band top (risk C9)",
                  "the backup band bottom %.0f V is more than 30 V below the hardware band top %.0f V (%.1f V short): the backup "
                  "can trip before the hardware comparator in the corner where the comparator sits at the top of its band; both "
                  "still act before the %.0f V limit (the firmware level cannot be raised: %.0f V at gates-off against %.0f V)" %
                  (v_ppb - dv_ppb, hi_v, hi_v - 30 - (v_ppb - dv_ppb), v_lim, end_p, v_lim))
    rows.append(("port OV backup (ADC PPB)", "%.0f V" % v_ppb, "%.0f-%.0f V" % (v_ppb - dv_ppb, v_ppb + dv_ppb),
                 "%.1f us" % (lag_p * 1e6), "<= %.0f V at gates-off, <= %.1f us (control_spec)" %
                 (v_lim, pv_["ov_detection_lag_us_max_full_current_case"])))
    return ok, rows


def check_adc(pins, P, pw):
    """Scaling, full scale, resolution and filter corner of every ADC channel against control_spec (section 5)."""
    ok, mr = True, CS["measurement_requirements"]
    n = il_numbers(pw)
    poles = [1e6, 1 / (2 * math.pi * pw["t_rc"]), 1 / (2 * math.pi * n["tau"]),
             1 / (2 * math.pi * val(IL_RC[0]) * val(IL_RC[1]))]
    f_bw = 1 / math.sqrt(sum(1 / f ** 2 for f in poles))
    t_d = pw["t_sens"] + pw["t_rc"] + n["tau"] + val(IL_RC[0]) * val(IL_RC[1])
    req = mr["cell_inductor_current"]
    fs = ((3.0 - n["z_adc"]) / n["g_adc"], n["z_adc"] / n["g_adc"])
    ok &= say(min(fs) >= req["range_A"][1] and LSB / n["g_adc"] <= req["resolution_A_per_LSB"] and
              f_bw >= req["bandwidth_kHz_min"] * 1e3 and t_d <= req["group_delay_us_max"] * 1e-6 and
              n["a"] * 4.5 <= 3.234 + 0.2,
              "ADC IL1-4 (x %.4f node, TLV9064 G %.3f, offset from VREF)" % (n["a"], n["a"] * (1 + n["k"])),
              "%.2f mV/A around %.3f V: -%.0f..+%.0f A (need +/-%.0f), %.4f A/LSB (need <= %.4f), -3 dB %.0f kHz (need >= "
              "%.0f), delay %.2f us (<= %.2f); sensor 4.5 V -> node %.2f V (TLV9024 inputs to V+ + 0.2 V), op-amp output "
              "clamped by +3V3A = VDDA" % (n["g_adc"] * 1e3, n["z_adc"], fs[1], fs[0], req["range_A"][1],
                                          LSB / n["g_adc"], req["resolution_A_per_LSB"], f_bw / 1e3,
                                          req["bandwidth_kHz_min"], t_d * 1e6, req["group_delay_us_max"], n["a"] * 4.5))
    rv = mr["port_voltage_VA_VB"]
    vm = pw["vmid"]
    fs_v = (3.0 - vm[1]) / pw["k_div"]
    res_v = LSB / pw["k_div"]
    n_os = 500e3 / (CS["sampling"]["outer_loop_kHz"] * 1e3)
    tau_div = 4.99e3 * 6e6 / (6e6 + 4.99e3) * 4.7e-9
    ok &= say(fs_v >= rv["range_V"][1] and res_v / math.sqrt(n_os) <= rv["resolution_V_per_LSB"] and
              1 / (2 * math.pi * tau_div) >= rv["bandwidth_kHz_min"] * 1e3, "ADC VA / VB (VMID + V/%.0f)" % (1 / pw["k_div"]),
              "full scale %.0f V (need %.0f); %.3f V/LSB, %.3f V/LSB with the 500 kS/s oversampling of the sampling plan "
              "(%.0f samples per outer-loop period, need <= %.3f); PV-PWR pole %.1f kHz (need >= %.0f); this board's "
              "RC %.1f MHz" % (fs_v, rv["range_V"][1], res_v, res_v / math.sqrt(n_os), n_os, rv["resolution_V_per_LSB"],
                               1 / (2 * math.pi * tau_div) / 1e3, rv["bandwidth_kHz_min"],
                               1 / (2 * math.pi * val(ADC_RC[0]) * val(ADC_RC[1])) / 1e6))
    ok &= say(vm[0] / pw["k_div"] >= 1144 and fs_v >= 1144, "ADC VAX / VBX / VPE (bipolar around VMID)",
              "-%.0f..+%.0f V covers +/-1144 V (OV overshoot): a reversed terminal and a PE fault are readable" %
              (vm[0] / pw["k_div"], fs_v))
    ri = mr["port_current_IA_IB"]
    fs_i = min(3.0 - vm[1], vm[0]) / pw["k_ia"]
    fs_h = min(3.0 - vm[1], vm[0]) / pw["k_ih"]
    ok &= say(LSB / pw["k_ia"] <= ri["resolution_A_per_LSB"] and fs_h >= ri["range_A"][1],
              "ADC IA / IB (%.1f mV/A) and IA_H / IB_H (%.1f mV/A)" % (pw["k_ia"] * 1e3, pw["k_ih"] * 1e3),
              "IA %.3f A/LSB (need <= %.3f), readable to +/-%.0f A in the 3.0 V range; IA_H to +/-%.0f A covers the +/-%.0f A "
              "requirement and the hold-off band" % (LSB / pw["k_ia"], ri["resolution_A_per_LSB"], fs_i, fs_h,
                                                     ri["range_A"][1]))
    ok &= say(pw["f_ia"] >= ri["bandwidth_kHz_min"] * 1e3, "IA / IB bandwidth (PV-PWR as built)",
              "gen/port.py lean_shunt pole %.1f kHz >= %.0f kHz (control_spec, CPL feed-forward), read from %s (closed there "
              "with 470 pF); this board's RC is 1.6 MHz" % (pw["f_ia"] / 1e3, ri["bandwidth_kHz_min"],
                                                            os.path.relpath(PWR_TXT, L.REPO)))
    terms = [20e-6 * 115, 100e-6, 50e-6, 5 / 4096, 2 / 4096]  # REF3030E box drift, hysteresis, long term; ADC
    #                                                          gain residual (whole 5 LSB kept), INL 2 LSB
    worst, rss = sum(terms), math.sqrt(sum(t * t for t in terms))
    ok &= say(rss <= 0.003 and worst + 0.0015 <= 0.01, "ADC accuracy, this board's share (after the 2-point calibration)",
              "RSS %.2f %% <= 0.30 %% (architecture section 6: ADC / reference 0.3 %%), worst %.2f %% (REF3030E drift "
              "%.2f %%, ADC gain 5 LSB + INL 2 LSB); + PV-PWR divider TCR mismatch 0.15 %% = %.2f %% < 1 %%" %
              (rss * 100, worst * 100, terms[0] * 100, (worst + 0.0015) * 100))
    for grp, k, rng in ((HS_NTC, "hs", (-40, 150)), (IND_NTC, "ind", (-40, 155))):
        v = [ntc_v(t, NTC_BIAS[k]) for t in rng]
        t_res = LSB / abs(ntc_v(rng[1] - 5, NTC_BIAS[k]) - ntc_v(rng[1] - 4, NTC_BIAS[k]))
        ok &= say(v[0] < 3.0 - 8 * LSB and v[1] > 8 * LSB, "ADC NTC%d-%d (%s bias from VREF, ratiometric)" %
                  (grp[0], grp[-1], NTC_BIAS[k]), "%.0f C -> %.3f V, %.0f C -> %.3f V (inside 8 LSB..FS-8 LSB); %.2f K/LSB "
                                                  "near the hot end" % (rng[0], v[0], rng[1], v[1], t_res))
    return ok


def logic_model(B, analog, exclude=()):
    """Trip chain evaluated from the drawn parts (pin -> net): comparators from the voltages they see (node values
    computed from the drawn resistor values), open-drain wired-AND nets with their pull-ups, the set-dominant
    74LVC1G74 and the AND / OR gates. Returns evaluate(inputs, q) -> (nets, q) and clk(nets, q) -> q."""
    parts = {r: p for r, p in B.D.parts.items() if r not in exclude}
    gates, od, pull, ff, mono, stop, res = [], defaultdict(list), {}, None, None, None, []
    for p in parts.values():
        key, pn = p.lib_id.split(":")[1], p.pins
        if key == "R":
            res.append((val(p.value), pn["1"], pn["2"]))
            for x, y in ((pn["1"], pn["2"]), (pn["2"], pn["1"])):
                if y == "+3V3":
                    pull[x] = 1
                elif y == "GND" and x not in pull:
                    pull[x] = 0
        elif key == "RPACK10K":
            for i in "1234":
                pull.setdefault(pn[i], 0)
        elif key == "SN74LVC08A":
            gates += [("and", pn[a], pn[b_], pn[y]) for a, b_, y in (("1", "2", "3"), ("4", "5", "6"), ("9", "10", "8"),
                                                                       ("12", "13", "11")) if pn[y]]
        elif key == "LVC1G32":
            gates.append(("or", pn["1"], pn["2"], pn["4"]))
        elif key == "LVC07":
            for a, y in (("1", "2"), ("3", "4"), ("5", "6"), ("9", "8"), ("11", "10"), ("13", "12")):
                od[pn[y]].append(("buf", pn[a]))
        elif key == "TLV9024":
            for pp, nn, oo in (("5", "4", "2"), ("7", "6", "1"), ("9", "8", "14"), ("11", "10", "13")):
                if pn[oo]:
                    od[pn[oo]].append(("cmp", pn[pp], pn[nn]))
        elif key == "TPS3828":
            od[pn["1"]].append(("src", "3V3 supervisor / watchdog"))
        elif key == "SN74LVC1G74":
            ff = dict(pre=pn["7"], clr=pn["6"], d=pn["2"], clk=pn["1"], q=pn["5"], qn=pn["3"])
        elif key == "LVC1G123":
            mono = dict(a=pn["1"], b=pn["2"], clr=pn["3"], q=pn["5"])
        elif key == "CA_IS3821" and pn["3"] == "STOP_OK":
            stop = pn["3"]
    od[mono["clr"]].append(("src", "MCU internal reset"))

    adj, cache = defaultdict(list), {}
    for r, a, b_ in res:
        adj[a].append((r, b_))
        adj[b_].append((r, a))
    cmp_in = {x for drv in od.values() for d_ in drv if d_[0] == "cmp" for x in d_[1:]}

    def solve(a_in):
        """Node voltages of the resistor networks behind the comparator inputs (Gauss-Seidel on the drawn values;
        VREF, GND, the rails and the PC analog inputs are the sources)."""
        key = tuple(sorted(a_in.items()))
        if key not in cache:
            fixed = dict({"VREF": 3.0, "GND": 0.0, "+3V3": 3.3, "+3V3A": 3.3}, **a_in)
            comp, todo = set(), [x for x in cmp_in if x not in fixed]
            while todo:
                x = todo.pop()
                if x not in comp:
                    comp.add(x)
                    todo += [o for r, o in adj[x] if o not in fixed]
            assert all(adj[x] for x in comp), "undriven comparator input"
            v = dict(fixed, **{x: 0.0 for x in comp})
            for _ in range(2000):
                dmax = 0.0
                for x in comp:
                    g = sum(1 / r for r, o in adj[x])
                    new = sum(v[o] / r for r, o in adj[x]) / g
                    dmax, v[x] = max(dmax, abs(new - v[x])), new
                if dmax < 1e-12:
                    break
            cache[key] = v
        return cache[key]

    def evaluate(inp, q):
        a_in = dict(analog, **{k: v for k, v in inp.items() if k in analog})
        n = {"+3V3": 1, "GND": 0}
        n.update({k: v for k, v in inp.items() if k not in analog})
        v = solve(a_in).__getitem__
        for _ in range(8):
            for net, drv in od.items():
                low = False
                for d_ in drv:
                    if d_[0] == "src":
                        low |= bool(inp.get("SRC:" + d_[1]))
                    elif d_[0] == "buf":
                        low |= n.get(d_[1]) == 0
                    else:
                        low |= v(d_[1]) < v(d_[2])
                n[net] = 0 if low else pull.get(net)
            n[mono["q"]] = int(bool(inp.get("heartbeat")) and n.get(mono["clr"]) == 1 and n.get(mono["a"]) == 0)
            n[stop] = int(bool(inp.get("enable_closed")))
            if n.get(ff["pre"]) == 0:
                q = 1
            n[ff["q"]], n[ff["qn"]] = q, 1 - q
            for kind, a, b_, y in gates:
                va, vb = n.get(a, pull.get(a)), n.get(b_, pull.get(b_))
                n[y] = (va and vb) if kind == "and" else (va or vb)
        return n, q

    def clk(n, q):
        return 0 if n.get(ff["pre"]) == 1 and n.get(ff["clr"]) == 1 and n.get(ff["d"]) == 0 else q
    return evaluate, clk


def check_latch(B, pw):
    """For every trip source, both builds: gates off with firmware still commanding, held after the source clears,
    cleared only by a firmware CLK edge while no source is active, HOLD keeps K_A/K_B; PV-P75 (phase-4 comparators
    not fitted) does not trip on IL4 = 0 V or open NTC4 / NTC8, PV-P100 does."""
    vm = sum(pw["vmid"]) / 2
    il = lambda amps: pw["v0"] + pw["g"] * amps                   # noqa: E731
    base = {"IL%d" % k: il(0) for k in range(1, 5)}
    base.update({"IL%dR" % k: pw["v0"] for k in range(1, 5)})       # sensor references (Uref, rev A1 trip ladders)
    base.update({"VA": vm + LATCH_VA * pw["k_div"], "VB": vm + 800 * pw["k_div"]})
    base.update({"NTC%d" % k: ntc_v(60 if k in HS_NTC else 100, NTC_BIAS["hs" if k in HS_NTC else "ind"])
                 for k in range(1, 9)})
    outs = [g[2] for g in GATES if g[2] != "FAN_PWM_L"]            # every gated output except the fan (BIAS_EN)
    fw = dict({g[0]: 1 for g in GATES}, BIAS_EN=1)
    healthy = dict(fw, RDY=1, HOLD=0, heartbeat=1, enable_closed=1)
    ph4 = {r for r, p in B.D.parts.items() if PH4 in str(p.value)}
    errs, n_cases = [], 0
    for build, excl, phases in ((BUILDS[0], (), 4), (BUILDS[1], ph4, 3)):
        an = dict(base)
        if phases == 3:
            an.update({"IL4": 0.0, "IL4R": 0.0, "NTC4": 3.0, "NTC8": 3.0})
        ev, clk = logic_model(B, an, excl)
        nets, q = ev(healthy, 1)
        nets, q = ev(healthy, clk(nets, q))
        if q != 0 or any(nets[o] != 1 for o in outs):
            errs.append("%s: healthy state does not release (%s)" % (build, [o for o in outs if nets[o] != 1][:3]))
        for q0 in (0, 1):
            nets, q = ev(dict(healthy, **{"SRC:3V3 supervisor / watchdog": 1}), q0)
            if q != 1 or any(nets[o] for o in outs):
                errs.append("%s: power-up (latch %d) not tripped" % (build, q0))
        cases = [("SRC:3V3 supervisor / watchdog", 1), ("SRC:MCU internal reset", 1), ("FLT_N", 0), ("RDY", 0),
                 ("heartbeat", 0), ("enable_closed", 0), ("VA", vm + 1150 * pw["k_div"]),
                 ("VB", vm + 1150 * pw["k_div"])]
        for k in range(1, phases + 1):
            cases += [("IL%d" % k, il(LATCH_I)), ("IL%d" % k, il(-LATCH_I)), ("IL%d" % k, 0.0),
                      ("IL%d dead sensor" % k, {"IL%d" % k: 0.0, "IL%dR" % k: 0.0})]
        for k in [x for x in range(1, 9) if phases == 4 or x not in (4, 8)]:
            hs = k in HS_NTC
            cases += [("NTC%d" % k, ntc_v(92 if hs else 155, NTC_BIAS["hs" if hs else "ind"])), ("NTC%d" % k, 3.0),
                      ("NTC%d" % k, 0.0)]
        for key, v_ in cases:
            n_cases += 1
            bad = dict(healthy, **(v_ if isinstance(v_, dict) else {key: v_}))
            nets, q1 = ev(bad, 0)
            if q1 != 1 or any(nets[o] for o in outs):
                errs.append("%s %s=%s: gates not off" % (build, key, v_))
                continue
            nets, q2 = ev(bad, clk(nets, q1))
            if q2 != 1:
                errs.append("%s %s: cleared while active" % (build, key))
            nets, q3 = ev(healthy, q2)
            if q3 != 1 or any(nets[o] for o in outs):
                errs.append("%s %s: not held after the source cleared" % (build, key))
            nets, q4 = ev(healthy, clk(nets, q3))
            if q4 != 0:
                errs.append("%s %s: not clearable" % (build, key))
            nh, _ = ev(dict(bad, HOLD=1), 1)
            if not (all(nh[k_] == 1 for k_ in HOLD_KEEPS) and nh["K_PRE"] == 0 and
                    not any(nh["PWM%d" % k] for k in range(1, 17))):
                errs.append("%s %s with HOLD: contactor commanded open or gates on" % (build, key))
        nf, _ = ev(dict(healthy, BIAS_EN=0), 0)
        if nf["FAN_PWM_L"]:
            errs.append("%s: fans enabled with BIAS_EN low" % build)
    return say(not errs, "Latch logic evaluated from the drawn netlist (%d fault cases, 2 builds)" % n_cases,
               "; ".join(errs[:4]) if errs else
               "every source - ILn +/-85 A, ILn 0 V, ILn = ILnR = 0 V (dead sensor), VA/VB 1150 V, NTC hot, open and "
               "shorted, FLT_N, RDY, "
               "ENABLE open, heartbeat stop, 3.3 V supervisor, MCU reset: all 16 PWM, EN, K_PRE, K_A, K_B, STATUS low with "
               "firmware still commanding them; a CLK edge while active is ignored; held after the source clears; "
               "released only by the next CLK edge; with HOLD a trip keeps %s; power-up always tripped; %s "
               "(%s not fitted) runs with IL4 = IL4R = 0 V and NTC4/NTC8 open; fans need BIAS_EN" %
               ("/".join(HOLD_KEEPS), BUILDS[1], ", ".join(sorted(ph4))))


# B1 isolators: (data sheet in docs/datasheets/isolation-interface, package column, the insulation table's columns in order).
# CLR / CPG / VIOWM (DC) / VIOTM / VIOSM / VISO are read from that table on every build (iso_rating); the WW rows are the
# altitude candidates (15 mm), the same data sheets
ISO_DS = {"CA-IS3050W": ("CA-IS305x.pdf", "G/W", ("U", "G/W", "WG")), "CA-IS3050WG": ("CA-IS305x.pdf", "WG", ("U", "G/W", "WG")),
          "CA-IS3082WNX": ("CA-IS308x.pdf", "W", ("W",)),
          "CA-IS3821LG": ("CA-IS382x.pdf", "G/W", ("G/W", "WW")), "CA-IS3820LG": ("CA-IS382x.pdf", "G/W", ("G/W", "WW")),
          "CA-IS3822LWW": ("CA-IS382x.pdf", "WW", ("G/W", "WW")), "CA-IS3820LWW": ("CA-IS382x.pdf", "WW", ("G/W", "WW")),
          "CA-IS3842HW": ("CA-IS384x.pdf", "W", ("W", "WW")), "CA-IS3842HWW": ("CA-IS384x.pdf", "WW", ("W", "WW"))}
# altitude: the 15 mm (WW) counterpart of each drawn B1 part (CA-IS308x v1.10 has no WW RS-485: a SELV transceiver + 3 WW channels)
WW_OF = {"CA-IS3050W": ["CA-IS3050WG"], "CA-IS3082WNX": ["CA-IS3831LWW", "RS-485 transceiver (SELV, non-isolated)"],
         "CA-IS3821LG": ["CA-IS3822LWW"], "CA-IS3820LG": ["CA-IS3820LWW"], "CA-IS3842HW": ["CA-IS3842HWW"]}
ALT_PRICE = {   # USD at 1000 pieces; LCSC 2026-10-06 (python read of the product pages) or ESTIMATE with its basis - candidates, not drawn
    "CA-IS3050WG": (0.7159 * 0.90 / 0.54, "ESTIMATE: not on LCSC; CA-IS3050W x 1.67 (CA-IS3820LWW 0.90 / CA-IS3820HW 0.54)"),
    "CA-IS3831LWW": (1.03, "ESTIMATE: CA-IS3831HWW LCSC C20598892 1.03 (the L variant is not listed)"),
    "RS-485 transceiver (SELV, non-isolated)": (0.15, "ESTIMATE (3485 class)"),
    "CA-IS3822LWW": (0.90, "LCSC C20598889 @1000 (CA-IS3821LWW C20598887 1.1325, stock 0)"),
    "CA-IS3820LWW": (0.90, "LCSC C20598885 @1000"),
    "CA-IS3842HWW": (1.12, "ESTIMATE: CA-IS3840LWW LCSC C20598893 1.12 (the 3842 WW is not listed)"),
    "4-ch WW (CA-IS384x)": (1.12, "CA-IS3840LWW LCSC C20598893 @1000")}
ALT_FACTOR = {3000: 1.14, 4000: 1.29, 5000: 1.48}       # IEC 60664-1 table A.2 (the factors of sim/insulation.py, from memory)
REJECTED = {"CA-IS3062W/VW (CAN + DC-DC)": "VIOSM 8000 Vpk (meets) but needs up to 125 mA from the live +5V (PV-PWR "
                                             "allocates 50 mA): replaced by CA-IS3050W (12.8 kV) on S_5V",
            "CA-IS374x (4-ch)": "VIOSM 7070 Vpk < 8 kV (CA-IS374x v1.05)",
            "CA-IS3092W (RS-485 + DC-DC)": "VIOSM 6250 Vpk < 8 kV (CA-IS309x v1.02)"}


def check_reset_state(B, P):
    """Default off while the MCU is in reset, booting or unprogrammed (SPRSP61C table 5-9 and table 6-3)."""
    t = _pdf("-layout")
    pu_off = re.search(r"\n\s*GPIOx\s+Pullup disabled\s+Pullup disabled\(1\)\s+Application defined", t) is not None
    boot_pu = [int(x) for x in re.search(r"Boot ROM enables pullups on GPIO(\d+) to GPIO(\d+)", t).groups()]
    rst = dict(re.findall(r"\n\s*(POR|BOR|XRS Pin|WDRS|NMIWDRS|SYSRS \(Debugger Reset\)|SCCRESET)\s+(?:Yes|No)\s+"
                          r"(?:Yes|No)\s+(?:Yes|No)\s+(\S+)", t))
    rpu = re.search(r"RPULLUP\s+Weak pullup resistance\s+(\d+)\s+(\d+)\s+(\d+)", t).groups()
    outs = [(net, key) for net, key, fn, d, r in P if d == "out"]
    pulled = {p.pins[k] for p in B.D.parts.values() if p.lib_id.endswith(":RPACK10K") for k in "1234"}
    gate_in = {x for x, _, _ in GATES} | {"HEARTBEAT"}
    bad = [net for net, key in outs if net in gate_in and net not in pulled]
    unbonded = [key for net, key in outs if key.startswith("GPIO") and boot_pu[0] <= int(key[4:]) <= boot_pu[1]]
    ok = pu_off and len(rst) == 7 and all(v == "Hi-Z" for v in rst.values()) and not bad and not unbonded
    v_err = 3.3 * 10 / (10 + int(rpu[0]))
    return say(ok, "Default off in reset / boot / unprogrammed (SPRSP61C tables 5-9, 6-3)",
               "GPIO pull-ups disabled at reset and during boot (the boot ROM enables them only on GPIO%d-%d, not "
               "bonded out); IOs Hi-Z for %s; every MCU line into the gating (%d) has a 10k pull-down, so the AND "
               "inputs are low in any reset; SYSRS / SCCRESET do not drive XRSn but stop the heartbeat (latch within "
               "5.8 ms). Firmware rule: never enable a pull-up on a command pin (RPULLUP %s-%s kOhm against 10k would "
               "give %.2f V > VIL 0.8 V of the LVC08)%s" % (boot_pu[0], boot_pu[1], ", ".join(rst), len(gate_in),
                                                           rpu[0], rpu[2], v_err, "; " + str(bad + unbonded) if not ok
                                                           else ""))


def firmware_table(rows):
    """What the firmware must configure and do ON TOP of the hardware (second layer, not a replacement)."""
    r = {x[0]: x for x in rows}
    il, ip, ov = r["IL1-4 CMPSS backup"], r["port OC backup"], r["port OV backup (ADC PPB)"]
    dt = CS["dead_time"]["firmware_dead_band_ns"]
    return [
        ("IL backup, per phase", "CMPSS1-4 H+L on the pin plan's mux index, DAC from VDAC (= VREF); CTRIPH|CTRIPL -> "
         "ePWM X-BAR TRIP4 -> DCAEVT1 -> one-shot on all ePWM", "%s, DAC = idle zero +/-%.1f A" % (il[2], I_BK),
         "digital filter 5 of 5 SYSCLK", il[3], "idle: DACH below / DACL above the zero -> OST flag must set; restore"),
        ("port OC backup", "ADC PPB on IA (ADC-A), IB (ADC-C), converted at carrier zero and peak, offset = idle zero; "
         "PV-P75: CMPSS4 window on IB -> TRIP5 -> one-shot", ip[2] + " (primary: PV-PWR on FLT_N)", "1 conversion",
         ip[3], "PPB limit inside the idle reading -> ADCEVT -> OST flag; restore"),
        ("port OV backup", "ADC PPB on VA (ADC-A), VB (ADC-B), every <= 10 us -> TRIP5 -> one-shot", ov[2] +
         " (hardware %s first)" % r["port OV VA / VB"][2], "1 conversion", ov[3], "as above"),
        ("OV / UV soft limits", "ADC, controlled stop (no latch)",
         "%.0f V (control_spec)" % CS["hardware_trips"]["firmware_overvoltage_V"], "outer loop",
         "%.0f us" % (1e3 / CS["sampling"]["outer_loop_kHz"]), "-"),
        ("over-temperature, 2nd layer", "ADC NTC1-8 + inlet: derating, controlled stop below the hardware band",
         "heatsink <= 85 C (hw %s), inductor <= 145 C (hw %s)" % (r["OT heatsink NTC1-4"][2], r["OT inductor NTC5-8"][2]),
         "1 s mean", "s",
         "cold start: every NTC within 10 K of the inlet NTC; open / short flagged (hw trips anyway)"),
        ("PRE_N / FLT_N / RDY / STOP_OK / DI1", "GPIO inputs (X-BAR -> XINT), read and logged before any clear. Trip cause: FLT_N, "
         "RDY or STOP_OK low -> that source; PRE_N low with those high and the heartbeat running -> an on-board comparator (IL "
         "window, port OV, NTC OT / open probe), which one from the ADC and the recorder (rev A2: OC_N / OVT_N are not read); DI1 "
         "= battery fault / BMS contact (NO / NC set by parameter) -> controlled stop, K_B open after the current is low",
         "-", "3-sample qualification", "-", "BIAS_EN low -> RDY low (latch sets); BIAS_EN high -> RDY high"),
        ("heartbeat", "GPIO27 toggled in the control ISR (period <= 2 ms)", "monoflop 4.7-5.8 ms; TPS3828 0.9-2.5 s",
         "-", "-", "stop toggling 10 ms -> TRIP must set; then clear"),
        ("latch clear", "GPIO24 rising edge only with every source inactive, OST flags cleared, PWM commands low, cause in "
         "the recorder flash; no automatic clear after a watchdog reset, OC or OV trip", "-", "-", "-",
         "power-up: TRIP = 1 must be read before the first clear"),
        ("dead time", "ePWM dead-band generator (the drivers' cross-wired inputs still block an overlap)",
         "%.0f ns rising and falling" % dt, "-", "-", "read back"),
        ("internal watchdog", "WD on from boot, serviced from the background loop only after the ISR ran; NMI watchdog "
         "on", "timeout <= 10 ms (firmware)", "-", "-", "-"),
        ("clock loss", "missing-clock detect (PLL limp) + DCC on X1 vs INTOSC2 -> NMI: force OST on all ePWM, stop the "
         "heartbeat; unhandled -> NMI-watchdog reset", "-", "-", "< 6 ms to the latch", "DCC running before PWM start"),
        ("brown-out", "keep the I/O BOR enabled (2.81-3.0 V); TPS3828 2.88-3.00 V -> XRSn: IOs Hi-Z, latch set",
         "-", "-", "-", "-"),
        ("watchdog reset", "WDRS / NMIWDRS drive XRSn: IOs Hi-Z, monoflop cleared, latch set; reboot reads the reset "
         "cause, logs it, stays tripped until commanded", "-", "-", "-", "-"),
        ("configuration lock", "after init: lock GPIO mux / direction and the X-BAR selections where the device offers a "
         "lock; command pins without pull-up; CMPSS, PPB, TZ, dead band read back every 10 ms -> trip on mismatch",
         "-", "-", "10 ms", "-"),
        ("PV-P75 build", "phase 4 idle: ePWM7/8 held by a forced one-shot, IL4 / NTC4 / NTC8 ignored, CMPSS4 on IB; "
         "the phase-4 TLV9024 pair is not fitted (assembly option)", "-", "-", "-", "build code from the recorder flash"),
        ("Ethernet (EMS, Modbus TCP; fitted on the PCS variants)", "SCIB %d 8-N-1 (LSPCLK %.0f MHz) to the CH9121T in TCP SERVER "
         "mode, port 502: the bridge forwards the TCP payload byte for byte, so the Modbus server parses Modbus TCP (MBAP + PDU), "
         "or RTU framing when the EMS uses RTU over TCP; one register map for RS-485 and Ethernet (firmware hand-over); IP / port "
         "written by the MCU through the CH9121 serial configuration (CFG test point pulled low once at end of line to enable the "
         "serial handshake); the TSSOP-20 cannot disable the maker's LAN set-up tool (no CFGEN pin): every write to the module "
         "is authorised in firmware; clients per port not stated by WCH (ASSUMED one)" % (SCI_BAUD, SCI_CLK / 1e6), "-", "-",
         "-", "end of line: link, loop-back through the EMS port"),
        ("fault recorder (GD25Q32E)", "32 channels in a RAM ring at the decimated rate (100 ms) + a 2 ms full-rate snapshot; "
         "trigger = TRIP or a firmware fault; 100 ms after the trigger; written into the next pre-erased slot of the flash ring "
         "with time stamp and cause; calibration, serial number and event log in two copies with CRC (R-WS-9)", "-", "-",
         "write <= 0.2 s", "read-back of the last record header at start"),
        ("firmware upgrade", "authenticated dual image: the new image is staged in the recorder flash over Ethernet, RS-485 "
         "or CAN, signature + CRC checked, copied by a bootloader in a DCSM-protected sector; the golden image stays for "
         "roll-back; only in SERVICE with authorisation and timeout (R-WS-8)", "-", "-", "-", "image CRC at every boot"),
        ("module address, ports", "module address by software over the module CAN (no DIP); port roles as the design check "
         "(module bus = CAN, BMS = RS-485 or CAN, EMS = Ethernet or RS-485, PC tool 9600-8-N-1 on RS-485); relay = STATUS_M "
         "(healthy) through the latch gating", "-", "-", "-", "-"),
        ("firmware practice adopted from the Wolfspeed firmware cross-check (docs/requirements/REFERENCE-LESSONS.md section 6, R-WS-1..9)",
         "range-checked bus/service inputs (switching frequency and dead time not writable in operation); ramp to zero and stop on "
         "communication loss (ASSUMED 1 s on CAN); lock-out after the third hazard trip of a class within 10 min (ASSUMED) and at once "
         "when hardware and firmware both see an over-voltage; explicit recovery threshold, dwell and restart-rate limit per non-latched "
         "limit; every exception path forces the one-shot trip and stops the heartbeat, clock-fail and emulation-stop trips enabled; no "
         "bus-reachable mode disables a protection, parameter writes only in SERVICE with authorisation and timeout",
         "-", "-", "-", "self-test fires each fault line alone; the 10 ms read-back covers the trip routing; calibration stored redundantly "
         "and range-checked, a failed load blocks the start")] + cold_start_rows()


def cold_start_rows():
    """PCM-17: the fans' cold-start rule, worded and valued by sim/pv_module.py (module_spec.json), not copied here. PV-CTL only: the
    inverter's fans and its power limits are PCS-CTL's own"""
    if PROJECT != "PV-CTL":
        return []
    k = MOD["costfirst"]["cold_start"]
    pol, st = k["policy"], k["policy"]["fans_start_regardless_of_inlet_C"]
    tbl = "; ".join("%s %s kW at %s C inlet" % (n, " / ".join("%.1f" % t["kW"] for t in m["table"]),
                                                " / ".join("%.0f" % t["inlet_C"] for t in m["table"])) for n, m in k["modules"].items())
    return [("fans, cold start (PCM-17)", "FAN_PWM from the inlet NTC (NTC_IN): " + "; ".join(k["firmware_rule"]),
             "inlet %.0f C (fan rating); passive power %s (ESTIMATE +-50 %%, module_spec.json); start regardless of the inlet: heatsink "
             "NTC %.0f C, inductor NTC %.0f C" % (pol["fan_rated_min_inlet_C"], tbl, st["heatsink_NTC"], st["inductor_NTC"]), "-", "-",
             "-")]


_ISO_CACHE = {}


def iso_rating(mpn):
    """CLR, CPG (mm), VIOWM (V DC), VIOTM, VIOSM (Vpk), VISO (Vrms) of one isolator, read from the insulation table of its data sheet:
    each row's numbers (footnote digits and out-of-range numbers dropped), one value spread over every column when the cell is merged"""
    f, col, cols = ISO_DS[mpn]
    if f not in _ISO_CACHE:
        _ISO_CACHE[f] = subprocess.run(["pdftotext", "-layout", os.path.join(L.REPO, DS, "isolation-interface", f), "-"],
                                       capture_output=True, text=True, check=True).stdout
    t = _ISO_CACHE[f]
    lines = t.split("\n")
    a = next(i for i, ln in enumerate(lines) if re.match(r"\s*CLR\s", ln))
    z = next(i for i in range(a, len(lines)) if re.match(r"\s*VISO\s", lines[i]))
    tbl = lines[a:z + 4]

    def row(key, lo, hi, unit):
        i = next(k for k, ln in enumerate(tbl) if re.match(r"\s*%s\b" % key, ln))
        ln = next(x for x in tbl[i:] if re.search(r"\b%s\b" % unit, x))
        v = [float(x) for x in re.findall(r"(?<![\w.])>?(\d+(?:\.\d+)?)(?![\w.])", ln) if lo <= float(x) <= hi]
        v = v * len(cols) if len(v) == 1 else v
        assert len(v) == len(cols), "%s %s row not read: %s" % (f, key, ln.strip())
        return v[cols.index(col)]
    return dict(clr=row("CLR", 2, 40, "mm"), cpg=row("CPG", 2, 40, "mm"), viowm_dc=row("VIOWM", 500, 2e4, "VDC"),
                viotm=row("VIOTM", 500, 2e4, "VPK"), viosm=row("VIOSM", 500, 2e4, "VPK"), viso=row("VISO", 500, 2e4, "VRMS"),
                src="%s v%s" % (f[:-4], re.search(r"Version\s*(\d+\.\d+)", t).group(1)))


def b1_clearances(req):
    """{altitude m: clearance mm} for B1 without and with the port-SPD credit; 3000 / 4000 m from insulation_spec, 5000 m from the
    table A.2 factor on the 2000 m value (insulation_spec stops at 4000 m), rounded up to 0.1 mm as sim/insulation.py does"""
    up = lambda x: math.ceil(x * 10 - 1e-9) / 10                                          # noqa: E731
    for alt in (3000, 4000):                       # the factor table reproduces the spec's own values
        assert up(req["cl_2000"] * ALT_FACTOR[alt]) == req["cl_%d" % alt], "ALT_FACTOR vs insulation_spec"
    plain = {2000: req["cl_2000"], 3000: req["cl_3000"], 4000: req["cl_4000"], 5000: up(req["cl_2000"] * ALT_FACTOR[5000])}
    credit = {2000: req["cl_good_2000"], 3000: req["cl_good_3000"], 4000: req["cl_good_4000"],
              5000: up(req["cl_good_2000"] * ALT_FACTOR[5000])}
    return plain, credit


def alt_reach(clr, table):
    """highest altitude (m) of the table whose clearance the part meets; 0 if none"""
    return max([a for a, need in table.items() if clr >= need], default=0)


def check_barrier(B, iso):
    req = next(r for r in INS["requirements"] if r["label"].startswith("Reinforced HV-PELV, DC pole"))
    plain, credit = b1_clearances(req)
    ok, parts, drawn = True, [], []
    for ref in sorted(iso):
        m = B.bom[ref]["mpn"]
        r = iso_rating(m)
        drawn.append((ref, m, r))
        ok &= (r["viosm"] >= req["imp"] and r["viso"] >= req["ac"] and r["viowm_dc"] >= req["u_tov"] and
               r["clr"] >= max(8.0, req["cl_2000"]) and r["cpg"] >= max(8.0, req["cr_pd1"]))
        parts.append("%s %s (VIOSM %d, VISO %d, VIOWM %d VDC, %.0f/%.0f mm, %s)" % (ref, m, r["viosm"], r["viso"],
                                                                                r["viowm_dc"], r["clr"], r["cpg"],
                                                                                r["src"]))
    alt = min(alt_reach(r["clr"], plain) for _, _, r in drawn)
    alt_c = min(alt_reach(r["clr"], credit) for _, _, r in drawn)
    ok = say(ok and alt >= 2000, "Barrier B1 ratings (%d isolators, read from the data sheets) vs insulation_spec '%s'" %
             (len(iso), req["label"][:34]),
             "need impulse %.0f V, AC %.0f Vrms 60 s, working %.0f V DC at the OV trip, clearance >= %.1f mm (2000 m), "
             "creepage >= 8 mm: %s. Creepage 8 mm < %.1f mm (PD2): conformal coating to PD1 (>= %.1f mm) over the B1 "
             "zone is MANDATORY. ALTITUDE THE BARRIER REACHES: %d m (clearance %.1f / %.1f / %.1f / %.1f mm needed at 2000 / "
             "3000 / 4000 / 5000 m; %d m with a port SPD credit, which needs Up,eff <= %.1f kV - not given by the drawn SPD)" %
             (req["imp"], req["ac"], req["u_tov"], req["cl_2000"], "; ".join(parts), req["cr_pd2"], req["cr_pd1"], alt,
              plain[2000], plain[3000], plain[4000], plain[5000], alt_c, INS["spd_max_up_eff_for_8mm"]["3000"] / 1e3))
    info("Rejected B1 candidates", "; ".join("%s: %s" % kv for kv in REJECTED.items()))
    return ok & check_altitude(drawn, plain, credit, alt, alt_c, req)


def check_altitude(drawn, plain, credit, alt, alt_c, req):
    """Task 3 (owner, 2026-10-05: the PMA is rated to 4000-5000 m with derating above 3000 m): what limits the rating, what the
    15 mm parts would cost, the decision. Thermal derating from module_spec; the clearance, not the thermal model, sets the ceiling."""
    ww = {}
    for _, m, _ in drawn:
        for c in WW_OF[m]:
            ww[c] = iso_rating(c) if c in ISO_DS else None
    cost_now = sum(PRICE[m][0] for _, m, _ in drawn)
    cost_ww = sum(ALT_PRICE[c][0] for _, m, _ in drawn for c in WW_OF[m])
    # consolidated alternative: every digital channel and the RS-485 onto 4-ch WW parts, CAN on CA-IS3050WG
    ch = 3 + 6 + 3                                       # STATUS, FAN, ETH_TX + STOP, TACH1-3, ETH_RX, DI1 + RS-485 TX / DE / RX
    cost_cons = (math.ceil(ch / 4) * ALT_PRICE["4-ch WW (CA-IS384x)"][0] + ALT_PRICE["CA-IS3050WG"][0] +
                 ALT_PRICE["RS-485 transceiver (SELV, non-isolated)"][0])
    ww_alt = min(alt_reach(r["clr"], plain) for r in ww.values() if r)
    mod = MOD["modules"]
    thermal = "; ".join("%s %s %% at %s m" % (n, " / ".join("%.1f" % (100 * f) for f in m["derating_fraction_45C_vs_altitude"]),
                                               " / ".join("%d" % a for a in m["altitude_m"])) for n, m in mod.items())
    t_alt = max(max(m["altitude_m"]) for m in mod.values())
    fit = cost_ww - cost_now <= 1.0 or cost_cons - cost_now <= 1.0
    ok = say(not fit and alt == 2000, "Altitude rating (task 3, calculated) - DECLARED %d m" % alt,
             "BARRIER: %d m (B1 isolators %.1f mm minimum clearance). THERMAL MODEL (module_spec, 45 C inlet, PV only; the inverter has "
             "no altitude model): %s - it reaches %d m with derating. DECLARED CEILING: %d m, set by the clearance, not the thermal "
             "model. The 15 mm (WW) parts exist and reach %d m (read from the same data sheets: %s); RS-485 has no integrated part "
             ">= 9.2 mm (CA-IS308x v1.10: W only; NOVOSENSE / 2Pai searched, none found), so a SELV transceiver + 3 WW channels. "
             "Cost per board (1 ku): drawn B1 set %.2f USD; part for part WW %.2f USD (+%.2f); consolidated on 4-ch WW parts (%d "
             "channels) %.2f USD (+%.2f): %s the ~1 USD rule -> %s. Cheapest other route: a port SPD with Up,eff <= %.1f kV and its "
             "own altitude rating (module decision, power board): the 8 mm parts then reach %d m (%.1f / %.1f mm needed at 3000 / "
             "4000 m)%s" %
             (alt, min(r["clr"] for _, _, r in drawn), thermal, t_alt, alt, ww_alt,
              ", ".join("%s %.0f/%.0f mm VIOSM %d" % (c, r["clr"], r["cpg"], r["viosm"]) for c, r in ww.items() if r),
              cost_now, cost_ww, cost_ww - cost_now, ch, cost_cons, cost_cons - cost_now, "within" if fit else "above",
              "fit them and rate 3000 m" if fit else "NOT fitted, the boards stay rated 2000 m",
              INS["spd_max_up_eff_for_8mm"]["3000"] / 1e3, alt_c, credit[3000], credit[4000],
              " - the rule now says FIT: re-decide and draw the WW set" if fit else ""))
    aux = json.load(open(os.path.join(L.REPO, "sim/out/aux_hv_design/aux75_spec.json")))["transformer_requirement"]["insulation"][
        "primary + live + shield - SELV"]
    info("Altitude - what is missing for 3000 m (and 4000 / 5000 m)",
         "(1) B1 isolators: the WW set above (+%.2f USD per board at least) or the SPD credit; (2) AUX-T1 (power board, custom): its "
         "specification already lists clearance %s mm at 2000 / 3000 / 4000 m (aux75_spec); the drawing must state the rated altitude, "
         "clearance >= %.1f mm and the sea-level impulse type test %.1f kV for 3000 m (%.1f mm / %.1f kV for 4000 m; %.1f mm for "
         "5000 m), PD <= 10 pC routine as now; (3) layout: every SELV connector (fans, stop / IO, CAN, RS-485, Ethernet) and the SELV "
         "harness >= %.1f mm clearance from LIVE copper and >= %.1f mm creepage (PD2) - the architecture's 10 mm keep-out holds to "
         "3000 m, 4000 m needs %.1f mm; (4) module level, not this board: power board B2 %.1f / %.1f mm at 3000 / 4000 m, the port SPDs' "
         "own altitude ratings (Raycap 59.D040: 3000 m max; DEHN 952515 not stated), contactors, fuses and fans not assessed" %
         (min(cost_ww, cost_cons) - cost_now, aux["clearance_mm"], plain[3000], req["imp_test_3000"] / 1e3, plain[4000],
          req["imp_test_4000"] / 1e3, plain[5000], plain[3000], req["cr_pd2"], plain[4000],
          next(r for r in INS["requirements"] if r["label"].startswith("Basic HV-PE, DC pole"))["cl_3000"],
          next(r for r in INS["requirements"] if r["label"].startswith("Basic HV-PE, DC pole"))["cl_4000"]))
    return ok


# ---- rev A2: Ethernet bridge, recorder flash, SELV I/O (calculated from the data sheets named; ASSUMED where marked)
CH9121 = dict(i_typ=76.2e-3, k_max=1.3, vcc=(3.2, 3.4), rx_tol=0.02, f_tol_ppm=40.0, f_rec_ppm=30.0)
#   CH9121 V2.6 table 7-3: 76.2 mA typical with 100BASE-TX traffic, all currents incl. the magnetics (no maximum published: x1.3
#   ASSUMED); table 7-2 VCC33 3.2-3.4 V; 6.1.1 receive tolerance <= 2 %; table 7-4 crystal 24.999-25.001 MHz, 'within 30 ppm' advised
XTAL_PPM = (10.0, 20.0, 3.0)          # X322525MOB4SI: +/-10 ppm at 25 C, +/-20 ppm over -40..85 C, aging +/-3 ppm/year (YSX321SL p.1)
LED_VF = (1.8, 2.8)                   # HR913550AE p.2 LED table (V at 20 mA)
RDS_DRV = 3.0                         # ohm: 2N7002BK at VGS >= 4.47 V (CA-IS3821 VOH at 5 V, 4 mA) - ASSUMED bound
SCI_CLK, SCI_BAUD = 60e6, 115200      # firmware: SCIB from LSPCLK = SYSCLK / 2 (LOSPCP), 115.2 kbit/s on both ends of the bridge
T_BOARD = 85.0                        # C: the board temperature the fan buck already uses (FAN_BUCK t_board)
REC = dict(pre=0.100, post=0.100, ch=32, nbytes=2, ram=24 * 1024, img=384 * 1024, n_img=2, cal=2 * 64 * 1024, page=256,
           t_page=0.5e-3, flash=4 * 1024 * 1024)
#   pre / post / channels: the PMA's recorder (manual p.10, the coordinator's bar); ram: pre-trigger buffer budget of the F280039C's
#   69 KB RAM (ASSUMED share); img: one image = the whole 384 KB flash (SPRSP61C 1); two images (new + golden) and two 64 KB
#   calibration sectors (A / B, CRC); GD25Q32E: 256 B pages, 0.5 ms typical page program, 4 MB
ETH_B = [   # option B (W5500 on SPI), parts beyond what option A also needs (RJ45, LDO, TVS); WIZnet reference schematic
    #         'W5500-Ref-RJ45WithMag' (docs.wiznet.io, 2015-09-25): USD at 1 ku
    ("W5500 (LCSC C32843 @1000)", 1, 1.8261), ("25 MHz crystal + 2 x 18 pF + 1 M", 1, 0.0584 + 3 * 0.002),
    ("EXRES1 12.4k 1 %, 1V2O 10 nF, TOCAP 4.7 uF", 3, 0.015), ("AVDD / VDD 7 x 100 nF + 2 x 10 uF + bead", 10, 0.06),
    ("TX 2 x 49.9 R + 10 R + 22 nF, RX 2 x 6.8 nF + 2 x 49.9 R + 10 nF", 9, 0.02), ("LED 2 x 330 R", 2, 0.002),
    ("RSTn 10k + 100 nF", 2, 0.004), ("74LVC1G125 + 100 nF: MISO off the shared SPI bus (ESTIMATE)", 2, 0.05),
    ("second isolator for SCSn (CA-IS3821, ESTIMATE as LG)", 1, 0.45), ("its 2 x 100 nF", 2, 0.004)]
ASM_USD = 0.02                        # USD per placement (ARCHITECTURE-COSTFIRST section 10)


def s3_load():
    """S_3V3 maximum (A): CH9121T, both LEDs fully on, the LDO's ground current at load (SBVS338H: <= 2 mA at 300 mA)"""
    return CH9121["i_typ"] * CH9121["k_max"] + 2 * (3.4 - LED_VF[0]) / 470.0 + 2e-3


def s5_budget():
    """S_5V without the Ethernet option, the option's share, the S_3V3 load (A) and the itemised rows (mA): data-sheet maxima, the
    relay coil at -40 C with -10 % resistance (its largest current)"""
    v5 = v5_range()[1]
    coil = v5 / (RELAY["r_coil"] * (1 - RELAY["tol"]) * (1 + 0.00393 * (RELAY["t_amb"][0] - 23.0)))
    rows = [("CAN bus side", 73.0), ("transceiver quiescent (rev A1 item)", 6.8), ("RS-485 into 54 R", 3600 / 54),
            ("3 x CA-IS382x", 15.0), ("CA-IS3842HW", 7.1), ("tach pull-ups", 3 * 5000 / 20e3), ("stop divider", 5000 / 43e3),
            ("DI1 wetting", 1e3 * v5 / (val(DI_NET[0]) + val(DI_NET[1]))), ("relay coil at -40 C", 1e3 * coil)]
    i3 = s3_load()
    eth = i3 + v5 / 3.0e3
    return 1e-3 * sum(r[1] for r in rows), eth, i3, rows + [("Ethernet option", 1e3 * eth)]


def check_ethernet(B):
    """Task 1: Ethernet for the EMS. The two implementations against each other (cost, parts, barrier, pins), the drawn one as built."""
    ok = True
    no = next(i for i, s in enumerate(B.D.sheets, 1) if s.key.endswith("_eth"))          # refs are <prefix><sheet><nn>
    eth = [r for r, m in B.bom.items() if not r.startswith("#") and m["sourcing"] != "NOPART" and
           int(re.fullmatch(r"[A-Z]+(\d+)\d\d", r).group(1)) == no]
    cost_a = sum(generic_price(B.bom[r]["desc"], B.bom[r]["value"]) if B.bom[r]["sourcing"] == "GENERIC" else
                 PRICE[B.bom[r]["mpn"]][0] for r in eth)
    common = sum(PRICE[x][0] for x in ("HR913550AE", "TPS7A2033PDBVR", "SRV05-4")) + 0.002 + 0.012 + 0.012 + 0.002
    cost_b = common + sum(c for _, _, c in ETH_B)                 # common: RJ45, LDO + 10 R + 1 / 10 uF + 100 nF, TVS (7 parts)
    n_b = 7 + sum(n for _, n, _ in ETH_B)
    vb = v5_range()
    i3 = s3_load()
    r_d = val(ETH_LDO_R)
    p_ldo = (vb[1] - r_d * i3 - 3.3 * 0.985) * i3
    tj, tj_no = T_BOARD + 187.1 * p_ldo, T_BOARD + 187.1 * (vb[1] - 3.3 * 0.985) * i3
    head = vb[0] - r_d * i3 - 3.3 * 1.015
    p_r = r_d * i3 ** 2
    brr = round(SCI_CLK / (8 * SCI_BAUD) - 1)
    err = SCI_CLK / ((brr + 1) * 8) / SCI_BAUD - 1
    ppm = XTAL_PPM[0] + XTAL_PPM[1]
    v_rx = vb[1] * 2.0 * 1.01 / (1.0 * 0.99 + 2.0 * 1.01)        # isolator VOH <= VDDB = S_5V, 1 % divider
    ok &= say(cost_a + len(eth) * ASM_USD < cost_b + n_b * ASM_USD and 3.3 * 0.985 >= CH9121["vcc"][0] and
              3.3 * 1.015 <= CH9121["vcc"][1] and tj <= 125.0 and head >= 0.145 and p_r <= 0.6 * 0.25 and
              abs(err) <= CH9121["rx_tol"] / 2 and ppm <= CH9121["f_rec_ppm"] and v_rx <= 3.3 * 0.985 + 0.4,
              "Ethernet for the EMS (task 1) - option A drawn: CH9121T UART bridge, %s" %
              ("FITTED on this variant" if eth_fitted() else "NOT FITTED on PV-CTL / PV-CTL-P75 (DNP, footprints only)"),
              "A (CH9121T 10/100 transparent TCP/UDP, maker's MAC, HR913550AE, TPS7A2033, SRV05-4, crystal, UART across B1 on 2 "
              "channels of the CA-IS3842HW that DI1 needs anyway, 2 MCU pins SCIB GPIO56/57): %d parts, %.2f USD BOM + %.2f USD "
              "assembly = %.2f USD. B (W5500 on the recorder flash's SPI bus with a 74LVC1G125 on MISO, WIZnet reference circuit, a "
              "second isolator for SCSn, 1 MCU pin, no MAC of its own: an IEEE address block or a locally administered address): %d "
              "parts, %.2f + %.2f = %.2f USD. On the BOM alone B is %.2f USD cheaper; with the placements A is cheaper by %.2f USD, "
              "and A has 1 barrier part instead of 2 (both meet the B1 rule: CA-IS382x / 384x VIOSM 8 kV), its own MAC and the "
              "maker's reference circuit (CH9121 V2.6 fig. 9-1); B saves one MCU pin -> A. "
              "CH9120 (10 Mbit only, 2.83 USD, LCSC stock 0) not taken. As drawn: VCC33 %.2f-%.2f V in 3.2-3.4 V; S_3V3 %.0f mA "
              "(CH9121T 76.2 mA typ x%.1f ASSUMED, LEDs, LDO); TPS7A2033 Tj %.0f C at a %.0f C board (%.0f C without the %s "
              "dropper, limit 125 C; RthJA 187.1 K/W), headroom %.2f V >= 0.145 V dropout, dropper %.0f mW (1206); SCIB %d bit/s from "
              "%.0f MHz LSPCLK, BRR %d -> %+.2f %% (bridge tolerates 2 %%); crystal +/-%.0f ppm (25 C + temperature) <= the maker's "
              "30 ppm (+ aging 3 ppm/year: OPEN at end of life against the 40 ppm table limit); isolator to RXD1 through 1.0k / 2.0k "
              "<= %.2f V (<= VCCIO min + 0.4 V abs max); MDI straight (50 R inside), centre taps 1 uF to S_GND, SRV05-4 0.8 pF; straps: CFG on an end-of-line test point, LINK/RESET held high by its LED path (2 s low = factory reset); "
              "RJ45 -40..+85 C, 2250 V DC magnetics; CHS GND (Bob-Smith 1000 pF / 2 kV) and shield legs to PE like the bus "
              "terminals' screens; one more B1 part adds ~2 pF across B1 (CA-IS384x CIO): micro-amp touch current, D-048 measures "
              "unchanged" %
              (len(eth), cost_a, len(eth) * ASM_USD, cost_a + len(eth) * ASM_USD, n_b, cost_b, n_b * ASM_USD, cost_b + n_b * ASM_USD,
               cost_a - cost_b, cost_b + n_b * ASM_USD - cost_a - len(eth) * ASM_USD, 3.3 * 0.985, 3.3 * 1.015, i3 * 1e3, CH9121["k_max"], tj,
               T_BOARD, tj_no, ETH_LDO_R, head, p_r * 1e3, SCI_BAUD, SCI_CLK / 1e6, brr, 100 * err, ppm, v_rx))
    return ok


def check_recorder():
    """Task 2 and the coordinator's bar (PMA: 100 ms before + 100 ms after the trigger, 32 channels, several records)."""
    f_ctrl = CS["sampling"]["outer_loop_kHz"] * 1e3                  # every channel converted once per PWM period (control_spec)
    full = (REC["pre"] + REC["post"]) * f_ctrl * REC["ch"] * REC["nbytes"]
    ram_full = REC["pre"] * f_ctrl * REC["ch"] * REC["nbytes"]
    dec = math.ceil(ram_full / REC["ram"])                            # decimation that fits the pre-trigger buffer into RAM
    f_rec = f_ctrl / dec
    ev = (REC["pre"] + REC["post"]) * f_rec * REC["ch"] * REC["nbytes"]
    free = REC["flash"] - REC["n_img"] * REC["img"] - REC["cal"]
    n_ev, n_full = int(free // ev), int(free // full)
    t_wr = math.ceil(ev / REC["page"]) * REC["t_page"]                  # the whole event, into a pre-erased slot
    # the task's own sizing: 50 events x 20 ms x 16 channels at the control rate + one image
    task = 50 * 0.020 * f_ctrl * 16 * REC["nbytes"] + REC["img"]
    return say(n_ev >= 50 and task <= REC["flash"] and free > 0,
               "Recorder flash GD25Q32E 4 MB (task 2, calculated)",
               "the task's sizing (50 events x 20 ms x 16 ch x 2 B at %.0f kHz + one image) %.2f MB <= 4 MB. The PMA bar (100 ms before "
               "+ 100 ms after, 32 ch): at the control rate %.0f kHz one event is %.0f kB and its pre-trigger buffer %.0f kB - more than "
               "the F280039C's 69 KB RAM, so the pre-trigger record runs at %.1f kHz (every %d-th period; %.0f kB of RAM, ASSUMED budget "
               "%.0f kB), %.0f kB per event; beside two images (%d kB each: new + golden) and two 64 kB calibration sectors %.2f MB stay "
               "free = %d events (%d at the full control rate - the RAM, not the flash, is the limit); writing one event takes %.2f s "
               "(pages 0.5 ms typ; slots erased ahead). The PMA publishes no rate or count. The fast trips are seen by the hardware latch; the recorder adds a 2 ms full-rate snapshot from RAM "
               "(firmware). Replaces the 32 kB I2C EEPROM (calibration, serial number, event log now in the flash, two copies + CRC, "
               "R-WS-9)" %
               (f_ctrl / 1e3, task / 2 ** 20, f_ctrl / 1e3, full / 1024, ram_full / 1024, f_rec / 1e3, dec,
                REC["pre"] * f_rec * REC["ch"] * REC["nbytes"] / 1024, REC["ram"] / 1024, ev / 1024, REC["img"] // 1024,
                free / 2 ** 20, n_ev, n_full, t_wr))


def check_io(B):
    """The coordinator's PMA interface bar (manual pp. 10-21, 29): what existed, what rev A2 adds, the port roles - declared."""
    ok = True
    v5 = v5_range()
    kd = (val(EN_DIV[0]) + val(EN_DIV[1])) / val(EN_DIV[1])
    v_on = 2.0 * kd * 1.02                                             # ON threshold at the terminal (as the stop trip line)
    r_in = val(EN_DIV[0]) + val(EN_DIV[1])
    n_src = max(n for n in range(1, 10) if S24[0] * (r_in / n) / (r_in / n + 10e3 * 1.01) >= v_on)
    i_mod = S24[1] / r_in
    v_di = v5[0] * val(DI_NET[1]) / (val(DI_NET[0]) + val(DI_NET[1]))
    t_di = val(DI_NET[2]) * val(DI_NET[3]) + val(DI_NET[0]) * val(DI_NET[1]) / (val(DI_NET[0]) + val(DI_NET[1])) * val(DI_NET[3])
    hot = 1 + 0.00393 * (RELAY["t_amb"][1] - 23.0)
    v_pick = RELAY["v_pick"] * hot                                      # pick-up rises with the coil resistance (copper)
    i_hot = v5[0] / (RELAY["r_coil"] * (1 + RELAY["tol"]) * hot)
    v_coil = v5[0] - i_hot * RDS_DRV
    ok &= say(n_src >= 1 and v_di >= 2.0 * 1.1 and v_coil >= v_pick and t_di <= 5e-3,
              "SELV I/O (PMA interface bar)",
              "STOP (existed): ENABLE -> default-low CA-IS3821LG -> STOP_OK -> 74LVC07 -> the latch: gates, EN, K_PRE, K_A/K_B "
              "(unless held), STATUS off in hardware, no firmware (trip table). Rev A2: J_STOP 2 / 3 stop line in / out and 4 / 5 0 V "
              "in / out (loop-through) to chain a cabinet 24 V stop line (each module %.2f mA at 36 V); the S_EN_SRC dry-contact "
              "supply serves %d module (ON >= %.1f V at S_24V >= %.0f V); READY permissive (PMA rocker p.13) = J_IO 6-7 in series "
              "(wire link if unused): open = stop in hardware - the firmware sees one STOP_OK for both. DI1 battery fault / BMS "
              "contact (PMA BAT_FAULT): J_IO 4 = 5 V wetting via 1.0k, 5 = input, 10k down, 10k + 100 nF (%.1f ms), 5.1 V clamp -> "
              "CA-IS3842HW -> GPIO52 (XINT3): closed contact %.2f V >= 2.2 V (VIT+ 2.0 V + 10 %%); NO / NC meaning set in firmware. "
              "Relay (PMA NO / COM / NC): HFD27/005-S on the STATUS channel through the 2N7002BK, energised = healthy, NC on any "
              "trip, stop or supply loss; pick-up at %.0f C %.2f V <= coil %.2f V (S_5V %.2f V - %.0f mA x %.0f ohm ASSUMED); "
              "SELV contacts only (<= 30 V AC / 60 V DC, 2 A 30 V DC). A firmware-read DI2 would need a 56th GPIO (the PCS plan "
              "uses 55 of 55: OPEN, e.g. drop the HOLD read-back, which the firmware can mirror from IB_H)" %
              (i_mod * 1e3, n_src, v_on, S24[0], t_di * 1e3, v_di, RELAY["t_amb"][1], v_pick, v_coil, v5[0], i_hot * 1e3,
               RDS_DRV))
    can_j = sum(1 for p in B.D.parts.values() if p.lib_id.endswith(":JP_OPEN") and "S_CAN_TH" in p.pins.values())
    rs_j = sum(1 for p in B.D.parts.values() if p.lib_id.endswith(":JP_OPEN") and "S_RS_TA" in p.pins.values())
    info("Terminations and addressing (existed)",
         "CAN: split 2 x 60.4 R + 4.7 nF switched in by a solder jumper (%d); RS-485: 120 R by a solder jumper (%d); open = not "
         "terminated (the two bus ends close theirs; fail-safe receiver). Selectable today, no DIP added. Module address: by "
         "software over the module CAN (firmware row), no address DIP" % (can_j, rs_j))
    info("Port roles (firmware / configuration, declared)",
         "module bus (paralleling, carrier synchronisation by CAN time-stamping, software addressing) = the one CAN; BMS = RS-485 "
         "(Modbus RTU) or, when the module is not paralleled, CAN; EMS = Ethernet (Modbus TCP, port 502, %s) or RS-485; HMI / "
         "local PC tool = Modbus TCP over the Ethernet bridge or the RS-485 at 9600-8-N-1 (the HMI panel and its 12 V supply are a "
         "cabinet accessory, not added; no web server: the bridge is transparent); upgrade = authenticated dual-image (signed, "
         "CRC, staging slot + golden image in the recorder flash, bootloader in a DCSM-protected sector) over Ethernet / RS-485 / "
         "CAN, only in SERVICE with authorisation (R-WS-8 / 9). One Ethernet port, not the PMA's two: a daisy chain is the "
         "cabinet's switch. No hardware sync pair: a second isolated pair across B1 is not fitted (one more isolator ~%.2f USD, a SELV "
         "differential transceiver and 2 GPIO the PCS plan does not have). Where it fails: a CAN-only BMS whose bit rate or identifiers cannot share the module bus "
         "while the modules run in parallel -> BMS on RS-485 needed (the EMS then on Ethernet); on PV-CTL (Ethernet not fitted) "
         "fit the Ethernet option first" % ("fitted" if eth_fitted() else "not fitted on PV-CTL", PRICE["CA-IS3842HW"][0]))
    adc = {r[0] for r in plan()}
    info("Insulation and leakage telemetry (PMA p.29)",
         "insulation resistance: VPE_ADC %s with the IMD switches IMD_SW1 / IMD_SW2 on both variants; leakage (residual) current: "
         "RCM_ADC on the PCS variant only (PCS_X, %s here); PV-P75/100/110 has no residual-current sensor (DC/DC: insulation "
         "monitoring only)" % ("on the ADC" if "VPE_ADC" in adc else "MISSING", "on the ADC" if "RCM_ADC" in adc else
                               "not on this variant"))
    return ok


def check_budget(B, pw):
    """Loads per rail (data sheet maxima where given) against the PV-PWR allocation and the local regulators."""
    ok = True
    used = {n for p in B.D.parts.values() for n in p.pins.values() if n}
    vref = (3 * 130e-6 + 4 * 3.0 / 6e3 + sum(3.0 / (val(NTC_BIAS["hs" if k in HS_NTC else "ind"]) + r_ntc(150))
                                             for k in range(1, 9)) + 3.0 / val(NTC_BIAS["inlet"]) +
            sum(3.0 / sum(val(x) for x in v) for v in LADDER.values()) + 4 * (3.0 - 1.9) / val(IL_FB[0]) +
            4 * 3.0 / val(ILR_PU)) + VREF_XTRA
    ok &= say(vref <= 10e-3, "Budget VREF (REF3030E, +/-10 mA)", "%.1f mA: VREFHI 3 x 130 uA, VDAC 4 CMPSS x 6 kOhm min, "
              "NTC biases at 150 C, ladders, IL offset resistors" % (vref * 1e3))
    # rev A2: the I2C EEPROM (2 mA) out; the recorder flash at its program / erase maximum (GD25Q32E ICC4-6 25 mA, -40..85 C), the
    # CA-IS3842HW live side (IDDA DC-signal maximum 7.1 mA at 5 V, CA-IS384x 7.9.1) and the three flash pull-ups in
    i33 = (106e-3 + 2.5e-3 + vref + 37e-6 + 4 * 0.75e-3 + 7 * 4 * 35e-6 + 3 * 2 * 2.5e-3 + 7.6e-3 + 3.6e-3 + 2.2e-3 +
           25e-3 + 7.1e-3 + 2e-3 + 1.3e-3 + 0.7e-3 + 2 * 0.33e-3 + 0.66e-3 + 0.7e-3 + 1.4e-3 + 3 * 3.3 / 10e3)
    ok &= say(i33 <= pw["alloc"][2] and "+5V" not in used and "+24V" not in used,
              "Budget live rails (PV-PWR allocation +24V %.0f / +5V %.0f / +3V3 %.0f mA)" % tuple(x * 1e3 for x in pw["alloc"]),
              "+3V3 <= %.0f mA = %.0f %% (F280039C 106 + 2.5 mA flash-program worst, REF3030E %.1f mA, TLV9064, 7 x TLV9024, "
              "isolator live sides incl. the CA-IS3842HW 7.1 mA, RS-485 VDDA 7.6 mA, CAN VCC1 3.6 mA, SPXO, recorder flash "
              "25 mA program / erase maximum (the I2C EEPROM's 2 mA is gone), LEDs, pull-ups); +5V and +24V not used" %
              (i33 * 1e3, 100 * i33 / pw["alloc"][2], vref * 1e3))
    s5, s5_eth, i3, rows5 = s5_budget()
    fan = {"PV-P75": 3 * 12.0, "PV-P100/110": 3 * 17.0}
    ifan = max(fan.values()) / 24.0
    s5_max = s5 + (s5_eth if eth_fitted() else 0.0)
    ok &= say(s5 + s5_eth <= S5_DESIGN and ifan + 0.5 * 0.64 <= 3.0 and ifan + 0.3 < 4.5,
              "Budget SELV rails", "S_5V %.0f mA without / %.0f mA with the Ethernet option (this variant: %.0f mA; %s) <= %.1f A "
              "design (rev A2, was 0.3 A: L22U Irms 1.5 A, TPS54360B limit >= 4.5 A); S_FAN_V %.2f A at 3 x 17 W (PV-P100/110) "
              "<= FXL0840-330 Irms 3.0 A with ripple, TPS54360B current limit >= 4.5 A, SS56 5 A" %
              (s5 * 1e3, (s5 + s5_eth) * 1e3, s5_max * 1e3, ", ".join("%s %.1f" % kv for kv in rows5), S5_DESIGN, ifan))
    p_selv = max(fan.values()) / 0.90 + 5.0 * s5_max / 0.80
    info("Budget SELV input (AUX-T1 winding)", "<= %.1f W (fans %.0f / %.0f W at 90 %%, 5 V branch %.1f W: %.1f W without / "
         "%.1f W with the Ethernet option, was 1.0 W before rev A2); architecture section 3: 39.6 / 56.1 W; input range %.0f-%.0f V "
         "(the power board), both bucks rated 60 V, no under-voltage lock-out above 4.5 V" %
         (p_selv, fan["PV-P75"], fan["PV-P100/110"], 5.0 * s5_max / 0.8, 5.0 * s5 / 0.8, 5.0 * (s5 + s5_eth) / 0.8, *S24))
    # the power boards carry 0.6 W for this board's SELV logic (gen/pv_power.py SELV_W, gen/pcs_power.py p_selv: ARCHITECTURE section 3)
    rt = json.load(open(os.path.join(L.REPO, "sim/out/aux_hv_design/aux75_spec.json")))["ratings"]
    logic = 5.0 * s5_max / 0.80
    rows_a = []
    for b, n_ph in (("PV-P75", 3), ("PV-P100/110", 4)):
        r_ = next(v for k, v in rt.items() if k.startswith("%d phases" % n_ph))
        rows_a.append((b, FAN_W[b] * 1.1 + logic, r_["selv_W"]))
    if logic > 0.6:
        open_item("SELV winding allocation on the power board (stacked maxima)",
                  "this board's SELV logic is %.2f W (%s; rev A1 was already 1.0 W), the power boards carry 0.6 W (gen/pv_power.py "
                  "SELV_W, gen/pcs_power.py p_selv). Against aux75_spec: %s. The aux hardware is one design (its 4-phase row %.1f W); "
                  "the power-board owners raise the 0.6 W term to >= %.2f W and %s" %
                  (logic, "Ethernet fitted" if eth_fitted() else "Ethernet not fitted",
                   "; ".join("%s fans x1.1 + logic %.1f W vs %.1f W%s" % (b, ld, cap, " OVER by %.2f W" % (ld - cap) if ld > cap else "")
                             for b, ld, cap in rows_a), rows_a[1][2], logic,
                   "re-allocate the PV-P75 row (fans at 100 % and the relay coil cold do not coincide: the fans stay off below "
                   "-10 C inlet)" if any(ld > cap for _, ld, cap in rows_a) else "keep the rows"))
    rt, rbot, rinj = (val(x) for x in FAN_FB)
    d_on, d_full = fan_d_on(), fan_d_full()
    v_hi, v_lo, v_hys = fan_vout(d_on), fan_vout(1.0), 0.8 + rt * (0.8 / rbot + (0.8 - 1.1) / rinj)
    s_lo, s_hi, _ = fan_set_range(d_on)
    f_lo, f_hi, vf_lo = fan_set_range(d_full)
    contract, c_ok, v_ceil = [], True, []
    for b, path in (("PV-P75", PWR_TXT), ("PV-P100/110", PWR_TXT.replace("PV-PWR", "PV-PWR-4"))) if PROJECT == "PV-CTL" else ():
        c = pwr_fan_contract(path)           # the power board's own line; this board re-derives what its buck passes from it
        v_pass = fan_ceiling(c["s_min"], c["i"])
        flow = min(1.0, v_pass / c["v_need"])
        v_ceil.append(v_pass)
        c_ok &= (abs(v_pass - c["v_pass"]) < 0.015 and abs(c["i"] - FAN_W[b] / c["v_need"]) < 0.005 and
                 abs(flow - c["flow"]) < 0.001 and flow >= c["flow_min"] and c["s_min"] >= S24[0])
        contract.append("%s: S_24V >= %.2f V at the worst cross-regulation point -> the buck passes >= %.2f V at %.2f A (%.0f %% of "
                        "the %.2f V the thermal model's fans run at: airflow x%.3f, limit x%.3f; the power board prints %.2f V, x%.3f; "
                        "24.00 V itself would need S_24V >= %.2f V)" % (b, c["s_min"], v_pass, c["i"], 100 * flow, c["v_need"], flow,
                                                                         c["flow_min"], c["v_pass"], c["flow"],
                                                                         fan_vin_for(FAN_V_RATED, c["i"])))
    sp_ok = f_lo >= max(v_ceil, default=0.0) and vf_lo >= EN_V_MAX + EN_MARGIN       # the dropout ceiling, not the set point, limits
    ok &= say(23.0 <= v_hi <= 25.2 and 6.0 <= v_lo <= 8.5 and fan_vf(0.0, 0.0) < 1.1 and c_ok and sp_ok,
              "Fan supply transfer (isolated PWM)",
              "duty 0 = off (EN %.2f V < 1.1 V); START from duty <= %.2f (EN %.1f V worst, FB = 0 while off) at %.1f V nominal "
              "(%.1f-%.1f V over the 1 %% resistors, 0.792-0.808 V reference and the SELV 5 V range, 64 corners), %.1f V at duty "
              "1.0 (architecture: 7-24 V). 100 %% speed command once the buck runs: duty %.3f (EN >= %.2f V over the corners) -> set "
              "point %.1f-%.1f V, always above what the dropout lets through, so the dropout ceiling limits (firmware trims the duty "
              "on the tach; the 17 W cap of PV-P100/110 is a speed limit). Below its input the buck runs in dropout (TPS54360B: D "
              "%.2f, R_DS(on) %.2f ohm hot max, %.0f mOhm choke at 85 C, SS56) and passes S_24V less the drop (the earlier 'S_24V >= "
              "25.6 V' was set point + 1.5 V, ASSUMED). Contract with the power board - %s. Firmware commands 0 or >= %.2f (inside "
              "the EN hysteresis the output can reach %.1f V); FAN_PWM gated by BIAS_EN" %
              (fan_vf(0.0, 0.0), d_on, EN_V_MAX, v_hi, s_lo, s_hi, v_lo, d_full, vf_lo, f_lo, f_hi, FAN_BUCK["d"], FAN_BUCK["rds"],
               1e3 * fan_r_dc(),
               "; ".join(contract) if contract else "not checked for this variant (its power board declares none)", d_on, v_hys))
    v5 = v5_range()
    ok &= say(4.5 <= v5[0] and v5[1] <= 5.5, "SELV 5 V set point", "%.2f-%.2f V inside CA-IS3050 VCC2 4.5-5.5 V, "
              "CA-IS3082 VDDB 3.0-5.5 V, CA-IS382x 2.5-5.5 V" % v5)
    for tag, vo, io_, cout, rc, cc, fsw in (("SELV 5 V", 5.0, S5_DESIGN, 44e-6, V5_COMP[0], V5_COMP[1], 500e3),
                                           ("fan 24 V", 24.0, 2.13, 20e-6, FAN_COMP[0], FAN_COMP[1], 300e3)):
        fp = io_ / (2 * math.pi * vo * cout)
        fco = val(rc) * 12 * 0.8 * 350e-6 / (2 * math.pi * cout * vo)
        fz = 1 / (2 * math.pi * val(rc) * val(cc))
        pm = min(90 + math.degrees(math.atan(fco / fz) - math.atan(fco / f) - math.atan(fco / (fsw / 2)))
                 for f in (fp, 1e-3))                                     # full load and no load
        ok &= say(fco < fsw / 8 and pm >= 45, "TPS54360B %s loop" % tag,
                  "crossover %.1f kHz (< fsw/8 = %.0f kHz), zero %.0f Hz, modulator pole %.0f Hz at full load; phase "
                  "margin >= %.0f deg from full load to no load (single-pole current-mode model + fsw/2 sampling lag; "
                  "SNVSB93 eq. 44-49, gmps 12 A/V, gmea 350 uA/V); calculated, not simulated" %
                  (fco / 1e3, fsw / 8e3, fz, fp, pm))
    return ok


def check_interface(B, pw):
    """This board against the PV-PWR as-built PC levels."""
    pins_of = defaultdict(set)
    for ref, p in B.D.parts.items():
        for num, net in p.pins.items():
            if net:
                pins_of[net].add((p.lib_id.split(":")[1], p.value, tuple(sorted(p.pins.values(), key=str))))
    def pull(net, rail):
        return [v for k, v, ns in pins_of[net] if k == "R" and rail in ns]
    errs = []
    if not (pw["flt_pullup"] and pull("FLT_N", "+3V3") == ["4.99k"]):
        errs.append("FLT_N pull-up")
    if not pw["rdy_pullup_on_pwr"] or pull("RDY", "+3V3"):
        errs.append("RDY must have no pull-up here")
    if any(val(v) < 100e3 for v in pull("RDY", "GND")):
        errs.append("RDY pull-down < 100k")
    drive = re.search(r"control drives >= ([\d.]+) V at ([\d.]+) mA", open(PWR_TXT).read())
    if not drive or float(drive.group(1)) > 2.4 or float(drive.group(2)) > 12:   # SN74LVC08A: VOH >= 2.4 V at -12 mA,
        errs.append("PWM / EN drive level")                                         # VCC 3.0 V (SCAS283W electrical characteristics)
    if not (pw["il4_zero"] and pw["ntc_open"]):
        errs.append("PV-PWR no longer states IL4 = IL4R = 0 V / NTC4, NTC8 open on PV-P75")
    return say(not errs, "Interface PC = PV-PWR as built (%s)" % os.path.relpath(PWR_TXT, L.REPO),
               "; ".join(errs) if errs else
               "PWM / EN / K_x from LVC08 (VOH >= 2.4 V at 12 mA; PV-PWR needs 2.4 V at 0.4 mA); IL 2.50 V (%.2f-%.2f) + "
               "%.3f mV/A, IA %.1f / IA_H %.1f mV/A on VMID %.3f-%.3f V, VA V/%.0f; FLT_N pulled up "
               "here 4.99k; RDY pulled up on PV-PWR (100k pull-down here); HOLD push-pull, MOV_OK open drain (100k "
               "pull-downs); only +3V3 on PC (IL1R-IL4R on the former +24V / +5V pins), within the allocation; IL4 = 0 V "
               "and NTC4/NTC8 open on PV-P75 handled "
               "by the assembly option" % (pw["v0_rng"][0], pw["v0_rng"][1], pw["g"] * 1e3, pw["k_ia"] * 1e3,
                                           pw["k_ih"] * 1e3, pw["vmid"][0], pw["vmid"][1], 1 / pw["k_div"]))


def check_resources(pins, P):
    """F280039C: PWM pins for 3 and 4 phases, ADC channels, CMPSS inputs, capture units, GPIO count."""
    ok, by = True, {}
    for p, d in pins.items():
        for nm in d["names"]:
            by[nm] = p
        by[d["label"]] = p
    plan_ = {net: (by[key], fn) for net, key, fn, d, r in P}
    for ph in (3, 4):
        bad = []
        for k in range(1, 4 * ph + 1):
            p, fn = plan_["PWM%d_M" % k]
            m = (k + 1) // 2
            leg = 2 * ((k - 1) // 4) + 1 + ((k - 1) // 2) % 2
            if fn != "EPWM%d_%s" % (m, "AB"[(k - 1) % 2]) or fn not in pins[p]["mux"] or m != leg:
                bad.append(k)
        ok &= say(not bad, "F280039C PWM pins, %d phases" % ph,
                  "PWM1-%d on GPIO0-%d = ePWM1-%d A/B (mux 1, HRPWM on all eight): phase p = ePWM(2p-1) leg A + "
                  "ePWM(2p) leg B on one carrier%s" % (4 * ph, 4 * ph - 1, 2 * ph, "; bad: %s" % bad if bad else ""))
    ana = [(net, plan_[net][0], plan_[net][1]) for net, key, fn, d, r in P if d == "ain"]
    chans = {net: sorted(nm for nm in pins[p]["ana"] if re.match(r"^[ABC]\d+$", nm)) for net, p, fn in ana}
    analog_pins = [p for p, d in pins.items() if d["type"] == "i" and d["names"][0][0] in "ABC"]
    pc_an = [nm for nm in IF.PC if re.match(r"^(IL\d|IA|IB|IA_H|IB_H|VA|VB|VAX|VBX|VPE|NTC\d)$", nm) and
             nm not in ADC_SKIP]
    covered = all(a in ADC_MUXED or any(net in (a, a + "_ADC") for net, p, fn in ana) for a in pc_an)
    ok &= say(covered and all(chans.values()) and len({p for _, p, _ in ana}) == len(ana) <= len(analog_pins),
              "F280039C ADC channels", "%d of the %d analog pins of the PZ package: all %d PC analog signals, the inlet "
              "NTC and VDAC, each on its own pin with an ADC channel (e.g. %s)%s" %
              (len(ana), len(analog_pins), len(pc_an), ", ".join("%s=%s" % (k, "/".join(c)) for k, c in
                                                                 list(chans.items())[:4]), ADC_NOTE))
    cm = {}
    for net, p, fn in ana:
        for h in [x for x in fn.split("+") if "_HP" in x]:
            lp = h.replace("_HP", "_LP")
            cm[net] = (h.split("_")[0], h in pins[p]["ana"] and lp in fn and lp in pins[p]["ana"])
    il = [cm["IL%d_ADC" % k] for k in range(1, 5)]
    ok &= say(all(x[1] for x in il) and sorted(x[0] for x in il) == ["CMP1", "CMP2", "CMP3", "CMP4"] and
              cm["IB_ADC"] == ("CMP4", True), "F280039C CMPSS inputs",
              "IL1-4 on CMPSS1-4 (H and L positive inputs with the same mux index on the pin = window); IB on a CMPSS4 "
              "pin (port OC backup on PV-P75 where CMPSS4 is free)")
    gp_used = sum(1 for net, key, fn, d, r in P if key.startswith("GPIO"))
    gp_all = sum(1 for d in pins.values() if any(nm.startswith("GPIO") for nm in d["names"]))
    free = sorted((d["label"] for p, d in pins.items() if "GPIO" in d["label"] and
                   not any(key in d["names"] for _, key, _, _, _ in P)), key=L.natural)
    ok &= say(gp_used <= gp_all, "F280039C GPIO / peripherals",
              "%d of %d GPIO pins used (incl. X1 and GPIO35 / 37, the TDI / TDO pins in cJTAG mode), free: %s; DCAN on GPIO32/33 "
              "(boot option 1), SCIA on GPIO28/29 (SCI boot, RS-485), SCIB on GPIO56/57 (Ethernet bridge), SPIA on GPIO18/54/55 "
              "+ CS# GPIO37 (recorder flash), eCAP1 APWM + eCAP2/3 capture (3 of 3), eQEP1 (1 of 2), INPUTXBAR 1, 4-8, 13, 14. "
              "Rev A2 pins came from: the I2C EEPROM (GPIO56/57), cJTAG (GPIO35/37), the run LED moved to WD_OK (GPIO34), "
              "PRE_N read instead of OC_N + OVT_N (GPIO52), X2 (GPIO18, the only pin free in both plans before)"
              % (gp_used, gp_all, ", ".join(free) or "none"))
    if gp_used == gp_all:
        open_item("F280039C GPIO - no spare pin on this variant",
                  "%d of %d used (the four-wire inverter build needed none: its N-leg PWM7/8 sit on the already routed ePWM4 A/B, GPIO6/7); "
                  "a later addition needs a pin from the plan: the HOLD read-back (GPIO47, the firmware can mirror it from IB_H) or the "
                  "PCS-only lines (TMUX address GPIO58-60, RCM_TST GPIO21)" % (gp_used, gp_all))
    t_soc = 0.4e-6 + 11 / 60e6
    socs = 2 * 4 + 2 * 31.25 / 10 + 2 + 14 * 32 / 1000
    info("ADC load", "%.0f conversions per 31.25 us on 3 ADCs = %.0f %% (sampling plan of control_spec); the CLA "
         "budget at 120 MHz is the architecture's R-14" % (socs, 100 * socs * t_soc / (3 * 31.25e-6)))
    return ok


def write_plan(B, pins, P, rows):
    """gen/data/pv_ctrl_pin_plan.csv: all 100 pins - data-sheet name, net, function, mux position, route, trip band,
    and what the net reaches on the board."""
    trip = {"IL%d_ADC" % k: [rows[1]] for k in range(1, 5)}
    trip.update({"IA_ADC": [rows[3]], "IB_ADC": [rows[3]], "VA_ADC": [rows[13]], "VB_ADC": [rows[13]],
                 "PRE_N": rows, "STOP_OK": [rows[9]], "FLT_N": [rows[2], rows[10]], "RDY_M": [rows[11]], "TRIP": rows})
    reach = defaultdict(list)
    for ref, p in B.D.parts.items():
        for num, net in p.pins.items():
            if net and not ref.startswith("#"):
                reach[net].append("%s.%s" % (ref, num))
    mcu = next(r for r, p in B.D.parts.items() if p.lib_id.endswith(":F280039C"))
    by = {}
    for net, key, fn, d, r in P:
        by[next(pp for pp, dd in pins.items() if key in dd["names"] or key == dd["label"])] = (net, fn, d, r)
    with open(PLAN_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pin", "datasheet_name", "net", "function", "mux_pos", "direction", "route", "trip_band", "reaches"])
        for p in sorted(pins):
            d = pins[p]
            net = B.D.parts[mcu].pins.get(str(p)) or ""
            net, fn, dr, route = by.get(p, (net, "", "", "unused (no connect)" if not net else
                                            "supply / ground" if net in RAILS + RETURNS else "dedicated pin"))
            pos = d["mux"].get(fn.split("+")[0], "analog" if dr == "ain" else ("0" if fn == "GPIO" else ""))
            far = sorted((x for x in reach.get(net, []) if not x.startswith(mcu + ".")), key=L.natural)
            w.writerow([p, d["label"], net, fn, pos, dr, route,
                        " | ".join("%s: %s (%s)" % (t[0], t[2], t[3]) for t in trip.get(net, [])),
                        "rail, %d pins" % len(far) if net in RAILS + RETURNS else " ".join(far)])


PRICE = {"F280039CSPZR": (4.395, "TI.com 1ku (prices.csv)"), "CA-IS3050W": (0.7159, "LCSC C970940 @1000"),
         "CA-IS3082WNX": (0.5119, "LCSC C7422548 @1000"), "CA-IS3821LG": (0.45, "ESTIMATE (architecture; not on LCSC)"),
         "CA-IS3820LG": (0.45, "ESTIMATE (architecture; not on LCSC)"), "TLV9024PWR": (0.25, "ESTIMATE (TI class)"),
         "TLV9064IPWR": (0.1813, "LCSC C779410 @4000"), "SN74LVC07APWR": (0.15, "ESTIMATE (LVC08 0.179)"),
         "SN74LVC08APWR": (0.179, "LCSC C465737 @4000"), "SN74LVC1G74DCUR": (0.187, "TI 1ku (prices.csv)"),
         "SN74LVC1G32DBVR": (0.036, "LCSC C10096 @9000"), "SN74LVC1G123DCUR": (0.163, "ESTIMATE (LCSC (LX) variant)"),
         "TPS3828-33DBVR": (0.19, "ESTIMATE (LCSC DBVT tier)"), "REF3030EAIDBZR": (0.45, "ESTIMATE"),
         "GD25Q32ESIGR": (0.4294, "LCSC C2832998 @1000"), "X1G0041710033": (0.262, "LCSC C70560 @1000"),
         "CA-IS3842HW": (0.60, "LCSC C20598875 @1000"), "CH9121T": (2.4845, "LCSC C45354919 @1008"),
         "HR913550AE": (0.8166, "LCSC C192706 @1000"), "SRV05-4": (0.0204, "LCSC C2836319 @600"),
         "X322525MOB4SI": (0.0584, "LCSC C9006 @500"), "TPS7A2033PDBVR": (0.099, "TI.com 1ku (prices.csv)"),
         "HFD27/005-S": (0.6162, "LCSC C23911 @1000"), "BAS16": (0.0078, "LCSC C79997 @1000"),
         "KF2EDGR-3.81-8P": (0.21, "LCSC C441188 @500 0.1146 + plug ESTIMATE"),
         "TPS54360BDDAR": (0.4686, "LCSC C524806 @1000"), "SS56": (0.035, "LCSC C65009 @1000"),
         "FXL0840-330-M": (0.12, "ESTIMATE"), "FXL0530-220-M": (0.08, "ESTIMATE"), "SM712": (0.021, "LCSC (mirror)"),
         "ESD2CAN24DBZRQ1": (0.118, "TI 1ku (prices.csv)"), "SMBJ36A": (0.03, "ESTIMATE (SMBJ33A 0.029)"),
         "SMBJ58CA": (0.035, "ESTIMATE"), "BZX84-C5V1,215": (0.04, "ESTIMATE (BZX84-C15 0.040)"),
         "2N7002BK,215": (0.0425, "LCSC C282405 @100"), "4D03WGJ0103T5E": (0.0065, "LCSC C29718"),
         "NCP15XH103F03RC": (0.0163, "LCSC C77131 @1500"), "CBW160808U301T": (0.0086, "LCSC C139179 @1500"),
         "150060VS75000": (0.05, "ESTIMATE"), "150060RS75000": (0.05, "ESTIMATE"),
         "X6521FV-2x32-C85D32": (0.40, "ESTIMATE (XKB 2x40 class)"), "X1270WVS-2x10B-9TV01": (0.2685, "LCSC C5147196"),
         "X6511WV-03H-C60D30": (0.0471, "LCSC C706875 @4000"), "KF2EDGR-3.81-2P": (0.08, "ESTIMATE incl. plug"),
         "KF2EDGR-3.81-6P": (0.16, "LCSC C441186 @500 + plug ESTIMATE")}


def generic_price(desc, value):
    if desc.startswith("Resistor"):
        return 0.012 if "0.1%" in desc else (0.002 if "1206" in desc or "0805" in desc else 0.0008)
    if "C0G" in desc and ("1206" in desc or "100n" in value):
        return 0.03
    if "1210" in desc:
        return 0.08
    if "22u" in value or "1kV" in desc:
        return 0.03
    if "10u" in value or "2.2u" in value or "1u" in value:
        return 0.012
    return 0.002


def check_cost(B):
    tot, est, lines, dnp, n_dnp = 0.0, 0.0, defaultdict(float), 0.0, 0
    ph4 = 0.0
    for ref, m in B.bom.items():
        if ref.startswith("#") or m["sourcing"] == "NOPART":
            continue
        if m["sourcing"] == "GENERIC":
            p = generic_price(m["desc"], m["value"])
        else:
            p, src = PRICE[m["mpn"]]
        if m["dnp"]:                                  # not-fitted option (the Ethernet bridge on PV-CTL): not in the board total
            dnp, n_dnp = dnp + p, n_dnp + 1
            continue
        if m["sourcing"] == "GENERIC":
            lines["passives (GENERIC)"] += p
            est += p
        else:
            lines[m["mpn"]] += p
            est += p if src.startswith("ESTIMATE") else 0.0
            ph4 += p if PH4 in str(m["value"]) else 0.0
        tot += p
    n = sum(1 for r, m in B.bom.items() if not r.startswith("#") and not m["dnp"])
    big = sorted(lines.items(), key=lambda kv: -kv[1])[:6]
    info("Board cost (1 ku, catalogue / estimate)", "%s %.2f USD, %s %.2f USD for %d fitted parts (%.2f USD of it "
         "estimated)%s; architecture CONTROL + INTERFACE 17.29 USD; largest: %s" %
         (BUILDS[0], tot, BUILDS[1], tot - ph4, n, est, "; Ethernet option NOT FITTED here: %d parts, %.2f USD if fitted" %
          (n_dnp, dnp) if n_dnp else "", ", ".join("%s %.2f" % kv for kv in big)))
    return tot


ETH_DNP = "DNP (Ethernet option: fitted on PCS-CTL only)"


def write_p75_bom(B):
    """The board BOM's not-fitted Ethernet rows say why; then the PV-P75 assembly variant: the phase-4 comparators not fitted as
    well (same schematic, same netlist)."""
    src = os.path.join(L.REPO, "bom", PROJECT + "_BOM.csv")
    rows = list(csv.reader(open(src)))
    rows = rows[:1] + [r[:10] + [ETH_DNP] if r[10] == "DNP" else r for r in rows[1:]]
    with open(src, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    out = [rows[0]]
    for r in rows[1:]:
        if PH4 in r[3]:
            r = r[:10] + [P75_DNP]
        out.append(r)
    with open(os.path.join(L.REPO, "bom", PROJECT + P75_SUFFIX + "_BOM.csv"), "w", newline="") as f:
        csv.writer(f).writerows(out)


def design_check(B, pins, P, iso):
    pw = pwr_levels()
    ok, rows = check_trips(pw)
    ok &= check_adc(pins, P, pw)
    ok &= check_latch(B, pw)
    ok &= check_barrier(B, iso)
    ok &= check_budget(B, pw)
    ok &= check_interface(B, pw)
    ok &= check_resources(pins, P)
    ok &= check_reset_state(B, P)
    ok &= check_ethernet(B)
    ok &= check_recorder()
    ok &= check_io(B)
    check_cost(B)
    print("Trip table (as built):")
    for r in rows:
        print("  %-30s %-30s band %-28s response %-22s requirement %s" % r)
    print("Firmware on top of the hardware (second layer; for control_spec.json):")
    print("  item | peripheral and setting | threshold / band | filter | latency | start-up self-test")
    for r in firmware_table(rows):
        print("  " + " | ".join(r))
    write_plan(B, pins, P, rows)
    return ok


def vrange(net):
    """DC range against the net's own domain reference (LIVE: GND = BUS-, SELV: S_GND) for the part-stress check;
    None = no DC range (switch node, bootstrap, bus line) or a filter node without DC current (ADC inputs)."""
    fixed = {"GND": (0, 0), "S_GND": (0, 0), "PE": (0, 0), "+3V3": (0, 3.63), "+3V3A": (0, 3.63), "VDD12": (0, 1.32),
             "VREF": (2.99, 3.01),  # steady state (ladder taps rise with it at power-up)
             "JTAG_VREF": (3.0, 3.63), "S_24V": S24, "S_EN_SRC": (0, 36.0), "S_EN_IN": (0, 36.0), "S_EN_BUS": (0, 36.0),
             "S_5V": (0, 5.5), "S_FAN_V": (0, 27.0), "S_EN": (0, 5.4), "S_3V3": (0, 3.4), "S_ETH_VDDK": (0, 1.5),
             "S_ETH_TCT": (0, 3.4), "S_ETH_RCT": (0, 3.4), "S_ETH_LINK_A": (1.5, 3.4), "S_ETH_ACT_A": (1.5, 3.4)}
    if net in fixed:
        return fixed[net]
    if net == "S_ETH_5V":                                       # behind the 10 R dropper: its load is the design check's line
        return None
    th = {n: lad_th(k)[i][0] for k, ns in TH_NETS.items() for i, n in enumerate(ns)}
    if net in th:                                               # ladder taps from VREF
        return (th[net] * 0.98, min(3.01, th[net] * 1.02))
    if net.endswith("_ADC") or re.search(r"_(SW|BT)$", net) or net.startswith(("S_CAN", "S_RS")):
        return None
    if re.fullmatch(r"IL\d", net):
        return (0, 5.0)
    if re.search(r"_(FB|RT)$", net):
        return (0, 1.0)
    if net.startswith("S_"):
        return (0, 5.5)
    return (0, 3.63)


if __name__ == "__main__":
    import contextlib
    import io
    B, pins, P, iso, xing = build_design()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        passed = design_check(B, pins, P, iso)
    print(buf.getvalue(), end="")
    if not passed:
        sys.exit(1)
    rc = L.build(B, domain_of=domain_of, isolators=iso, crossings=xing, vrange=vrange)
    with open(os.path.join(L.REPO, "hardware", PROJECT, "outputs", PROJECT + "_design_check.txt"), "w") as f:
        f.write("CALCULATED by gen/pv_ctrl.py design_check(), not measured.\n" + buf.getvalue())
    write_p75_bom(B)
    sys.exit(rc)
