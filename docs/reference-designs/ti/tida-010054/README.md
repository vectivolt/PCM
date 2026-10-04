# TIDA-010054 - 10 kW bidirectional dual-active-bridge DC/DC (TI)
Single-phase DAB, SiC MOSFETs, planar transformer, TMS320F280039 control, UCC21710 gate drive, isolated V/I sensing. TI: lab use only.
- **Native ratings** (TIDUES0 Rev F, Table 1-1): in 700-800 V DC; out 250-500 V DC (front page: secondary 350-500 V under single-phase shift, 250-500 V under extended phase shift); 10 kW max, 26 A max; 100 kHz; peak 98.8 % at 4 kW, 98.0 % at 10 kW; > 2 kW/L.
- **Informs:** DAB-D60 control cross-check (DAB-08, single-phase-shift baseline), GDRV UCC21710 usage (ECO-05), isolated sensing (ECO-09).
- **Limitation:** 10 kW / 800 V class, not 60 kW or 950 V; roadmap uses it only to reduce control risk. Controller is F280039, not our F28388D.
- **Doc inconsistencies (Rev F):** peak efficiency 98.7 % (front page) vs 98.8 % (Table 1-1); full-load 98.0 % (table) vs 97.6 % (sentence above the table).
- **Files:** design guide TIDUES0 Rev F, schematic TIDRZW0 Rev C, BOM TIDRZW1 Rev B. Not fetched: assembly drawing, PCB layout, Gerber/CAD/PLECS zips (not requested or not PDF).
