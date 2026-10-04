# TIDA-010210 - 11 kW three-phase ANPC inverter/PFC, GaN (TI)
Three-level ANPC: 4 high-frequency LMG3422R030 (600 V GaN) + 2 line-frequency 650 V Si per phase, 100 kHz, shunt + AMC3302 current sensing, multilevel interlocks in the C2000 CLB. TI: lab use only.
- **Native ratings** (TIDUEZ0 Rev A, p.1, p.8): 11 kW, 800 V DC nominal (600-800 V tested; "up to 1000 V" in the overview), 400 V AC; measured 98.5-98.62 % (p.18).
- **Informs:** 3-level PV cell option (PV-18, D-031): hardware state interlocks in the CLB, voltage sharing of series devices.
- **Limitation:** 2021-2022 DC/AC design, GaN; no DC/DC stage.
- **What we take from it:** put any 3-level forbidden-state protection in hardware (F28388D CLB), never firmware-only. Detail: `docs/requirements/REFERENCE-LESSONS.md`.
- **Files:** design guide TIDUEZ0 Rev A; schematic archive TIDM837 (3 PDFs) and BOM archive TIDM838 (3 xls), unpacked in `design-files/`. Not fetched: assembly drawing, PCB layout, Gerber/CAD (layout out of scope).
