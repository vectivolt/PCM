# TIDA-010957 - 15-30 kW three-level flying-capacitor 3-phase+N converter, GaN (TI)
Four flying-capacitor legs, 16 x 650 V GaN (LMG3522R030 for 15 kVA, LMG3670R010 for 25-30 kVA) on a 650-900 V link, 62.5 kHz per device / 125 kHz at the inductor, F28P55x. TI: lab use only.
- **Native ratings** (TIDUFG9 Rev A, Tables 1-1/1-2 p.3): 15 kVA / 21 A rms (25 kVA / 36 A rms), 400-480 V AC, DC 800 V nominal (650-900 V); measured 98.81 % at 700 V, 98.42 % at 900 V (p.14-18).
- **Informs:** the re-opened PV cell topology question (PV-18, D-031): 3-level device voltage class, flying-capacitor pre-charge network, FC sensing and interlocks.
- **Limitation:** DC/AC (not a DC/DC), GaN with integrated drivers, link limited to 900 V for 650 V devices (our ports reach 1000 V, OVP 1100 V).
- **What we take from it:** 650 V parts are not admissible on a 1000 V port; a 3-level cell needs a passive FC pre-charge/clamp network across the outer devices, per-leg FC sensing and hardware state interlocks. Detail: `docs/requirements/REFERENCE-LESSONS.md`.
- **Files:** design guide TIDUFG9 Rev A; schematic archive SLVRBZ7 and BOM archive SLURB91 (PDFs unpacked in `design-files/`); app note SDAA195 (FC design considerations, pre-charge network, linked from the TI page). Not fetched: assembly drawing, PCB layout, Gerber/CAD (layout out of scope).
