# TIDM-BUCKBOOST-BIDIR - C2000 non-isolated bidirectional buck-boost (TI)
Synchronous buck/boost with voltage control, MPPT input-current control and reverse-direction voltage control on a TMS320F28035 control card; SFRA supported.
- **Native ratings** (TIDU638, Dec 2014, power-stage parameters): input 10-100 V DC, 0-8 A; output 5-100 V DC, 0-8 A; **300 W max**; 250 kHz; > 95 % incl. bias (test report TIDU704: 94.99 % at 42 V in, 34.43 V out, 75.6 W in).
- **Informs:** PV-P75/100/110 control structure (PV-17 bidirectional flow, PV-19 MPPT/buck-boost control basis) and the C2000 software layout for CTRL-C2000.
- **Limitation:** control reference only - 300 W / 100 V against our 75 kW / 1000 V module (250x power, 10x voltage); power stage and sensing do not scale. Firmware is F2803x, not F28388D.
- **Files:** TIDU638 design guide, TIDU704 test report (extra), TIDRCK9 schematic, TIDRCL0 BOM Rev A. Not fetched: Gerber zip, PCB-layout PDF (TIDRCL1; layout is out of scope).
