# PMP41037 - 1 kW 800 V to 12 V serial half-bridge bidirectional DCX, GaN (TI)
Two LMG3622 650 V GaN half-bridges in series on the 800 V side (split bus, one AMC1311 per half, active voltage-balancing control), LLC run as DCX at resonance, F280039C. TI: lab use only.
- **Native ratings** (TIDT332 p.1, Table 1-1 p.3): 760-840 V (up to 900 V) to 12 V, 1 kW, +/-84 A; measured 98.0 % at 634 W forward (p.8).
- **Informs:** stacked / series-device options of the PV cell topology question (PV-18, D-031, candidate C); start-up of stacked stages; magnetics sourcing (D-031: transformer and choke from YAXIN Electronic, CN).
- **Limitation:** 1 kW isolated LLC-DCX, not a buck-boost; test procedure ramps the 800 V source at < 1 V/ms (p.6), so start-up under a fast pre-charge is not shown.
- **What we take from it:** a stacked stage needs passive balancing (bleeders + clamps) that holds with the controller off, per-half isolated sensing and a balancing loop; our pre-charge ramps are 17-34 V/ms. Detail: `docs/requirements/REFERENCE-LESSONS.md`.
- **Files:** test report TIDT332; schematic archive TIDMCY1 and BOM archive TIDMCY2 (PDFs unpacked in `design-files/`). Not fetched: assembly drawing, PCB layout, Gerber/CAD (layout out of scope).
