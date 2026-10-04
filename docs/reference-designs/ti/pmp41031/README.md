# PMP41031 - 350-1500 V input, 150 W isolated auxiliary supply, two-switch flyback (TI)
Two-switch quasi-resonant flyback, 2 x STW12N170K5 (1700 V Si), UCC28740 with opto feedback, Wurth 750345142 ERL35 transformer (Lm 800 uH), four outputs 24 V / 15 V / -15 V / 8 V. TI: lab use only.
- **Native ratings** (TIDT355, Table 1-1 p.2, Table 2-1 p.4): 350-1500 V DC in, 150 W, 70 kHz at full load; 89.11 % at 600 V, 88.02 % at 1000 V, 86.17 % at 1500 V full load; no-load input 0.78 W at 1000 V.
- **Informs:** AUX-HV sizing and topology (ECO-06, D-021, open owner decision D-022: bootstrap-only 30 W vs self-powered ~80 W).
- **Limitation:** starts at 350 V (PV-C2 asks <= 250 V); Si 1700 V switches; multi-output, no protected 24 V bus interface.
- **What we take from it:** a self-powered module supply (~80-150 W) from 1000-1500 V is a single two-switch flyback stage, and the two-switch arrangement keeps each switch at V_in instead of V_in + V_R. Detail: `docs/requirements/REFERENCE-LESSONS.md`.
- **Files:** test report TIDT355, schematic TIDMBZ3, BOM TIDMBZ4. Not fetched: assembly drawing, PCB layout, Gerber/CAD (layout out of scope).
