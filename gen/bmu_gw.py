"""BMU-GW - BMS gateway daughtercard (requirement ECO-08): an isolated CAN FD port and an isolated RS-485 port
between the controller's logic-level CAN-A / UART and the battery BMS bus. It is a gateway, not a cell monitor.

  host side  (PELV)  J101 from CTRL-C2000 / SYS-IO-AUX: 5 V, 3.3 V, CAN TX/RX, RS-485 D/R/DE/RE
  barrier            ISO1042 (CAN FD), ISO1410 (RS-485), SN6505B + 750315371 push-pull transformer (unregulated 5 V,
                     1:1.1 - the SN6505B datasheet table 9-3 "5 V -> 5 V, no LDO" configuration)
  field side (BMS)   J102: CANH, CANL, RS-485 A/B, isolated ground, shield; TVS on both buses, terminations by solder jumper
Usage: .venv/bin/python gen/bmu_gw.py
"""
import sys

import catalog
import dcdclib as L

PROJECT, REV, DATE = "BMU-GW", "B0", "2026-10-04"
DS = "docs/datasheets/connectors/"
CATALOG = dict(catalog.PARTS, **{   # connectors from Asian makers with the drawing on file (gen/data/alternates.csv)
    "J_HOST": dict(mfr="XKB Connection", mpn="X6521WV-2x05H-C60D30", prefix="J", pkg="2x5 2.54 mm THT",
                   ds=DS + "XKB-X6521W.pdf", stock=("Connector_Generic", "Conn_02x05_Odd_Even"),
                   desc="Host header 2x5, 2.54 mm, -40..+105 C (mates X6521FV-2x05 on SYS-IO-AUX)"),
    "J_FIELD": dict(mfr="Cixi Kefa Electronic", mpn="KF2EDGR-3.81-6P", prefix="J", pkg="pluggable header 6-pos 3.81 mm",
                    ds=DS + "KEFA-KF2EDGR-3.81.pdf", stock=("Connector_Generic", "Conn_01x06"),
                    desc="Pluggable header 6-pos 3.81 mm right angle, 300 V 8 A, -40..+105 C, BMS field wiring "
                         "(plug KF2EDGK-3.81-6P)"),
})
BMS_NETS = {"5V_BMS", "GND_BMS", "XF_S1", "XF_S2", "CAN_H", "CAN_L", "CAN_TH", "CAN_TMID", "RS485_A", "RS485_B",
            "RS485_TA", "SHIELD"}


def domain_of(net):
    return "BMS" if net in BMS_NETS else "PELV"


VDC = {"5V": (0, 5.5), "3V3": (0, 3.6), "5V_BMS": (0, 5.5), "GND": (0, 0), "GND_BMS": (0, 0), "SHIELD": (-60, 60),
       "CAN_TX": (0, 3.6), "CAN_RX": (0, 3.6), "RS485_D": (0, 3.6), "RS485_R": (0, 3.6), "RS485_DE": (0, 3.6),
       "RS485_RE_N": (0, 3.6)}       # DC range of each net for the stress check; bus and transformer nets have none


def build_design():
    B = L.Builder(PROJECT, "BMU-GW BMS gateway", REV, DATE, CATALOG, rails=["5V", "3V3", "5V_BMS"],
                  returns=["GND", "GND_BMS"], subtitle="Isolated CAN FD + RS-485 between CTRL-C2000 and the battery BMS",
                  comment1="Barrier: 5 kVrms reinforced isolators; bias transformer 2500 VAC test (functional/basic)",
                  comment4="Not bench-validated. PELV = controller side, BMS = field side.",
                  root_notes=["The field side (GND_BMS) floats with the BMS communication ground; only U-isolators and "
                              "the bias transformer cross the barrier (checked on every build).",
                              "The isolated 5 V is unregulated (5 V x 1.1 minus one Schottky drop): 4.8-5.5 V over a "
                              "5 V +/-5 % input, inside the 4.5-5.5 V VCC2 window of ISO1042. R preload keeps it bounded "
                              "at no load."])
    iso = set()
    B.new_sheet("01_gateway", "Isolated CAN + RS-485",
                "Host header, push-pull isolated 5 V, ISO1042 CAN FD,\nISO1410 RS-485, TVS, terminations, field connector")

    B.block("Host header (PELV)", "From CTRL-C2000 / SYS-IO-AUX.\nDE and ~RE pulled low: driver off, receiver on at reset.")
    B.part("J_HOST", {"1": "5V", "2": "3V3", "3": "GND", "4": "GND", "5": "CAN_TX", "6": "CAN_RX", "7": "RS485_D",
                      "8": "RS485_R", "9": "RS485_DE", "10": "RS485_RE_N"})
    for net in ("5V", "3V3", "GND"):
        B.flag(net)
    B.C("10u", "5V", "GND", pkg="0805", volt="16V")
    B.R("10k", "CAN_TX", "3V3")          # recessive while the host is in reset
    B.R("10k", "RS485_DE", "GND")
    B.R("10k", "RS485_RE_N", "GND")

    B.block("Isolated 5 V bias", "SN6505B push-pull, 750315371 1:1.1, full-wave Schottky.\nCLK low = internal 420 kHz clock.")
    B.part("SN6505B", {"2": "5V", "5": "5V", "6": "GND", "4": "GND", "1": "XF_D1", "3": "XF_D2"})
    B.C("100n", "5V", "GND")
    B.C("10u", "5V", "GND", pkg="0805", volt="16V")
    iso.add(B.part("WE750315371", {"1": "XF_D1", "2": "5V", "3": "XF_D2", "6": "XF_S1", "5": "GND_BMS", "4": "XF_S2"}).ref)
    B.part("PMEG4010CEJ", {"2": "XF_S1", "1": "5V_BMS"})
    B.part("PMEG4010CEJ", {"2": "XF_S2", "1": "5V_BMS"})
    B.C("10u", "5V_BMS", "GND_BMS", pkg="0805", volt="16V")
    B.C("100n", "5V_BMS", "GND_BMS")
    B.R("1k", "5V_BMS", "GND_BMS", note="5 mA preload")
    B.flag("5V_BMS")
    B.flag("GND_BMS")

    B.block("Isolated CAN FD", "Split termination 2 x 60.4R + 4.7n, fitted by closing JP.")
    iso.add(B.part("ISO1042", {"1": "3V3", "3": "CAN_TX", "5": "CAN_RX", "4": None, "6": None, "7": None, "2": "GND",
                               "8": "GND", "16": "5V_BMS", "11": "5V_BMS", "13": "CAN_H", "12": "CAN_L", "14": None,
                               "15": "GND_BMS", "10": "GND_BMS", "9": "GND_BMS"}).ref)
    B.C("100n", "3V3", "GND")
    B.C("100n", "5V_BMS", "GND_BMS")
    B.C("100n", "5V_BMS", "GND_BMS")
    B.part("JP_OPEN", {"1": "CAN_H", "2": "CAN_TH"})
    B.R("60.4R", "CAN_TH", "CAN_TMID", pkg="0805")
    B.R("60.4R", "CAN_TMID", "CAN_L", pkg="0805")
    B.C("4.7n", "CAN_TMID", "GND_BMS", volt="100V")
    B.part("ESD2CAN24", {"1": "CAN_H", "2": "CAN_L", "3": "GND_BMS"})

    B.block("Isolated RS-485", "120R termination fitted by closing JP.\nReceiver is fail-safe (open/short/idle) inside ISO1410.")
    iso.add(B.part("ISO1410", {"1": "3V3", "6": "RS485_D", "5": "RS485_DE", "4": "RS485_RE_N", "3": "RS485_R", "7": None,
                               "2": "GND", "8": "GND", "16": "5V_BMS", "12": "RS485_A", "13": "RS485_B", "10": None,
                               "11": None, "14": None, "15": "GND_BMS", "9": "GND_BMS"}).ref)
    B.C("100n", "3V3", "GND")
    B.C("100n", "5V_BMS", "GND_BMS")
    B.part("JP_OPEN", {"1": "RS485_A", "2": "RS485_TA"})
    B.R("120R", "RS485_TA", "RS485_B", pkg="0805")
    B.part("SM712", {"1": "RS485_A", "2": "RS485_B", "3": "GND_BMS"})

    B.block("Field connector (BMS)", "Shield is RC-bonded to the isolated ground.")
    B.part("J_FIELD", {"1": "CAN_H", "2": "CAN_L", "3": "GND_BMS", "4": "RS485_A", "5": "RS485_B", "6": "SHIELD"})
    B.R("1M", "SHIELD", "GND_BMS", pkg="1206")
    B.C("4.7n", "SHIELD", "GND_BMS", pkg="1206", volt="1kV")
    return B, iso


if __name__ == "__main__":
    B, iso = build_design()
    sys.exit(L.build(B, domain_of=domain_of, isolators=iso, vrange=VDC.get))
