# CRD-25BDA6512N-K - 25 kW bidirectional three-phase T-type inverter / PFC (Wolfspeed)
Single board: C3M0032120K (1200 V, 32 mohm) outer + 2 x C3M0025065K (650 V, 25 mohm) middle switches per phase, flyback aux, TMS320F280039C on TMDSCNCD280039C, 60 kHz, third-harmonic-injection PWM. No GUI.
- **Native ratings** (PRD-08613 Rev 2, Oct 2024, Table 1): DC 670 / 800 / 900 V (inverter input, PFC output); AC 380 / 400 / 480 V L-L; 36 A rms and 25 kW at 800 V; inverter 99 % peak (40 % load) and 98.5 % full-load; PFC 99.25 % at 480 V L-L (50 % load); 320 x 235 x 50 mm.
- **Caveat in the guide:** not tested grid-connected; no grid algorithm in the firmware.
- **Informs:** SiC T-type commutation, layout and gate-drive lessons for GDRV and PV power loops. The AC PCS itself is out of scope here.
- **Limitation (roadmap):** scale current, neutral, filter, cooling; 950 V qualification. Roadmap quotes 650-900 V PFC DC range; the guide's Table 1 minimum is 670 V.
- **Also named on the product page's component hotspots** (not from a file we hold): UCC5350MCDWVR gate driver, ACS733KLATR current sensor, C2M1000170J (1700 V) in the auxiliary flyback.
- **Files:** user guide only. Design files are **MANUAL**: the product page (re-checked 2026-10-04) lists "Design Files for: Main board" under What's Included but has no download link for them (the CRD-60DD12N and XM3 pages do), and "Download Firmware and GUI" is a JavaScript button under "Request Separately". Check the page in a browser or ask Wolfspeed. See SOURCES.csv.
