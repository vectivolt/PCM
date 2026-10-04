# Megarevo PMD-75-G3 - 75 kW non-isolated bidirectional DC/DC (MPPT) module
Competitor target for PV-P75/100/110 (REQUIREMENTS section 2): one centralized MPPT, buck and boost between two 250-1000 V ports.
- **Native ratings:** see `spec.md` (verbatim web table, retrieved 2026-10-04): 75 kW rated / 82.5 kW max; 250~1000 V, 550~950 V full load, 135 A on both ports; 99 % max efficiency; forced air; 550*444*133 mm, 30 kg; 485/CAN + BMS; non-isolated.
- **Datasheet:** `pmd-75-g3_datasheet_v1-0.pdf` carries the identical table (checked by script) plus marketing claims (MPPT efficiency up to 99.99 %, protection response <= 3 ms, 2.55 MW/m3).
- **Versus roadmap "MPPT target":** no published number differs; details at the top of `spec.md`.
- **Limitation:** public figures only - no circuit, topology, device or loss data; the teardown items in the roadmap stay open.
