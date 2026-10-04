# TIDA-01606 - 11 kW bidirectional three-phase three-level (T-type) inverter/PFC (TI)
SiC T-type AFE with LCL filter and full DQ-domain control; the roadmap's highest-value control/topology reference.
- **Native ratings** (TIDUE53 Rev J, Feb 2025, Table 1-1): 11 kW; 400 V L-L max, 16 A rms; DC input 800 V nominal, 600-900 V range; 50-90 kHz; 98.6 % efficiency; THD < 3 % (front page: < 2.5 %); 2.2 kW/L; 27 x 35 x 5 cm.
- **Devices:** 1200 V SiC outer switches, 650 V SiC middle switch; gate drive UCC21710 (HV) + UCC5350 (middle); isolated sensing TMCS1123, AMC3306M05.
- **Control hardware:** TMS320F28379D (TMDSCNCD28379D) and TMS320F280039C (TMDSCNCD280039C) cards - **not F28388D**.
- **Informs:** CTRL-C2000 ADC/PWM/trip plan (ECO-01/02), GDRV protection (ECO-05), port sensing (ECO-09); control basis for PV and DAB loops.
- **Limitation (roadmap):** 900 V max DC; high-current devices, 950 V envelope, busbars, larger filter, thermal, four-wire neutral and qualification remain ours.
- **Files:** TIDUE53 Rev J design guide, TIDRVS3 Rev H schematic, TIDRVS4 Rev G BOM. Not fetched: assembly drawing and PCB-layout PDFs, Gerber/CAD/PLECS zips.
