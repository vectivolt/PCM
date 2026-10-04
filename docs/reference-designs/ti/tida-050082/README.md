# TIDA-050082 - high-voltage positive-rail active precharge (TI)
Updated buck-style precharge: TPSI31P1-Q1 isolated driver, hysteretic inductor-current control, positive-rail MOSFET or IGBT; expands on TIDA-050063.
- **Native ratings** (TIDUFH5): charges 2 mF from 0 V to 800 V within 400 ms; 4.5 A average / 10 A peak-to-peak charge current; 5 kVrms reinforced isolation; integrated isolated bias for the high-side rail.
- **Informs:** ECO-07 precharge sizing only (D-006).
- **Limitation (roadmap):** again an 800 V reference; power devices and insulation must change for 950 V.
- **Files:** TIDUFH5 design guide, SLVRC09 schematic, SLURBB5 BOM. Calculator .xlsx files and Gerber/CAD zips not fetched (not PDF).
