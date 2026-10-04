# PMP41009 - 350-1000 V input, 14 V / 56 W quasi-resonant flyback (TI)
QR flyback with a two-MOSFET cascode (2 x STF2N95K5 950 V Si; upper gate held by 6 x 200 k and 2 x P6KE350A TVS), UCC28740-Q1, TL431 + FOD817A, RCD clamp, Wurth Midcom ER28/17 transformer, 120 V Schottky output. Test report only, no design calculations. TI: lab use only.
- **Native ratings** (TIDT266, Table 1-1, p.4, p.8): 350-1000 V in, 14 V / 4 A; 84.6 % at 1000 V / 56 W, 85.5-86.8 % at 350 V; no-load input 0.3 W (350 V) / 1.3 W (1000 V); measured switch node 1.51 kV and lower FET 790 V at 1000 V / 4 A.
- **Informs:** AUX-HV (ECO-06, D-021) efficiency/standby cross-check, the D-031 switch re-sourcing (single 1700 V SiC vs Si cascode), the D-022 standby budget, MAG-1 (core size only).
- **Limitation:** starts at 350 V (PV-C2 needs <= 250 V); no input fuse or surge parts, no short-circuit, overload or surge test published; transformer turns, Lp, gap not published; lower FET at 83 % of its rating at 1000 V (above our 80 % rule).
- **What we take from it:** measured efficiency and no-load figures to check our calculated AUX-HV, and the cascode as a D-031 cost option (detail in docs/requirements/REFERENCE-LESSONS.md).
- **Files:** test report TIDT266, schematic TIDMAD8, BOM TIDMAD9. Not fetched: assembly drawing, PCB layout, Gerber/CAD (layout out of scope).
