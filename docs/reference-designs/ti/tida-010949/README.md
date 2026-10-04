# TIDA-010949 - 600 W GaN four-switch buck-boost solar power optimizer (TI)
Module-level optimizer: FSBB with 2 x LMG2100R026 (100 V GaN half-bridge), 300 kHz, 3.6 uH, TMCS1127 Hall sensing, P&O MPPT, PLC + wireless on one C2000. TI: lab use only.
- **Native ratings** (TIDUF99, p.1, Table 3-1 p.12): 80 V in / 80 V out, 18 A; 99.0 % peak in switching mode at 15 A, 99.5 % in short (bypass) mode.
- **Informs:** PV cell modulation of the four-switch buck-boost (PV-16, PV-10), MPPT (PV-02).
- **Limitation:** 600 W / 80 V; nothing transfers in rating.
- **What we take from it:** stacked-carrier FSBB modulation (single modulator, no mode state machine) as the firmware fallback if our buck/band/boost mode logic chatters on the bench. Detail: `docs/requirements/REFERENCE-LESSONS.md`.
- **Files:** design guide TIDUF99, schematic TIDMDJ2, BOM TIDMDJ3. Not fetched: assembly drawing, PCB layout, Gerber/CAD (layout out of scope).
