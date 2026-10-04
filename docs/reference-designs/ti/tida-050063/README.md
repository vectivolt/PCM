# TIDA-050063 - high-voltage solid-state-relay active precharge (TI)
No-MCU active precharge of a large DC link: TPSI3052-Q1 (5 kVrms reinforced isolation, integrated isolated bias) drives an external SiC MOSFET/IGBT; runs from 5 V.
- **Native ratings** (TIDUF21 Rev A): up to 800 V systems; 4 A average / 8 A peak-to-peak charge current; 2 mF to 800 V in 400 ms. Bench-tested with E3M0075120K (1200 V) at 400 V and 800 V and E3M0060065K (650 V) at 400 V.
- **Informs:** precharge sizing and protection for ECO-07. The hardware itself is not copied: D-006 is resistor + precharge contactor.
- **Limitation (roadmap):** 800 V reference - not a 950 V drop-in; switch stage and insulation must change.
- **Files:** TIDUF21 design guide, TIDMB87 schematic, TIDMB88 BOM, SSZTD93 technical article (extra, precharge method). The ACTIVE-PRECHARGE-CALC .xlsx and Gerber/CAD zips were not fetched (not PDF).
