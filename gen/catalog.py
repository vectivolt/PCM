"""Shared, datasheet-verified parts used by more than one board. Entry format: see dcdclib.py.

Every pin table here was checked against the PDF named in `ds` (page given in the comment). A board script
uses these with `CATALOG = dict(catalog.PARTS, **{board-only parts})`.
"""
DS = "docs/datasheets/"
TI = "Texas Instruments"

PARTS = {
    # ---- isolated communication (ISO1042: SLLSF09F fig 4-1 DW-16; ISO1410: SLLSF22I fig 5-2 half-duplex DW-16)
    "ISO1042": dict(mfr=TI, mpn="ISO1042DWR", prefix="U", pkg="SOIC-16W (DW)", ds=DS + "isolation-interface/ISO1042.pdf",
                    desc="Isolated CAN FD transceiver, 5 kVrms reinforced, +/-70 V bus fault protection",
                    pins={"left": ["1 VCC1 pi", "3 TXD i", "5 RXD o", None, "4 NC nc", "6 NC nc", "7 NC nc", None,
                                   "2 GND1 pi", "8 GND1 pi"],
                          "right": ["16 VCC2 pi", "11 VCC2 pi", "13 CANH b", "12 CANL b", None, "14 NC nc", None,
                                    "15 GND2 pi", "10 GND2 pi", "9 GND2 pi"]}),
    "ISO1410": dict(mfr=TI, mpn="ISO1410DWR", prefix="U", pkg="SOIC-16W (DW)", ds=DS + "isolation-interface/ISO1410.pdf",
                    desc="Isolated half-duplex RS-485 transceiver, 500 kbps, 5 kVrms reinforced",
                    pins={"left": ["1 VCC1 pi", "6 D i", "5 DE i", "4 ~{RE} i", "3 R o", None, "7 NC nc", None,
                                   "2 GND1 pi", "8 GND1 pi"],
                          "right": ["16 VCC2 pi", "12 A b", "13 B b", None, "10 NC nc", "11 NC nc", "14 NC nc", None,
                                    "15 GND2 pi", "9 GND2 pi"]}),
    # ---- isolated bias (SN6505B: SLLSEP9I table 5-1 DBV-6; transformer: WE 750315371 schematic, 1:1.1, 2500 VAC test)
    "SN6505B": dict(mfr=TI, mpn="SN6505BDBVR", prefix="U", pkg="SOT-23-6 (DBV)", ds=DS + "power-supply/SN6505B.pdf",
                    desc="Push-pull transformer driver, 1 A, 420 kHz, soft start",
                    pins={"left": ["2 VCC pi", "5 EN i", "6 CLK i", "4 GND pi"], "right": ["1 D1 oc", None, "3 D2 oc"]}),
    "WE750315371": dict(mfr="Wurth Elektronik", mpn="750315371", prefix="T", pkg="SMD 6-pin 8.3x12.6 mm",
                        ds=DS + "magnetics/WE-750315371.pdf",
                        desc="Push-pull transformer 1:1.1 CT:CT, 72 uH, 8.6 Vus, 2500 VAC test, AEC-Q200",
                        pins={"left": ["1 P1 p", "2 PCT p", "3 P2 p"], "right": ["6 S1 p", "5 SCT p", "4 S2 p"]}),
    "PMEG4010CEJ": dict(mfr="Nexperia", mpn="PMEG4010CEJ", prefix="D", pkg="SOD-323F", stock=("Device", "D_Schottky"),
                        ds="https://www.nexperia.com/product/PMEG4010CEJ", desc="Schottky rectifier 40 V 1 A low Vf"),
    # ---- bus protection (ESD2CAN24-Q1: table 4-1; CDSOT23-SM712: pins 1,2 = lines, 3 = common, -7/+12 V working)
    "ESD2CAN24": dict(mfr=TI, mpn="ESD2CAN24DBZRQ1", prefix="D", pkg="SOT-23 (DBZ)", ds=DS + "protection/ESD2CAN24-Q1.pdf",
                      desc="2-channel 24 V CAN bus ESD/TVS diode", pins={"left": ["1 IO1 p", "2 IO2 p"], "right": ["3 GND p"]}),
    "SM712": dict(mfr="Bourns", mpn="CDSOT23-SM712", prefix="D", pkg="SOT-23", ds=DS + "protection/CDSOT23-SM712.pdf",
                  desc="RS-485 asymmetric TVS array, -7 V / +12 V working", pins={"left": ["1 IO1 p", "2 IO2 p"], "right": ["3 COM p"]}),
    "JP_OPEN": dict(mfr="", mpn="", prefix="JP", pkg="solder jumper", ds="", sourcing="NOPART",
                    stock=("Jumper", "SolderJumper_2_Open"), desc="Solder jumper, open by default"),
}
