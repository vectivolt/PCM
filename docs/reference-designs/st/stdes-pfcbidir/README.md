# STDES-PFCBIDIR - 15 kW three-phase three-level bidirectional AFE (ST)
Data brief only (lower priority). STM32G474RET3, SiC, 100 kHz; STSW-PFCBIDIR firmware exists (not fetched).
- **Ratings (web-search summary of the ST data brief; NOT verified against a PDF):** 800 V DC nominal, 400 V AC / 50 Hz, 15 kW, efficiency > 98 %.
- **Informs:** AFE control cross-check only; the AC-side PCS is out of scope for this repo. (written before D-046 / D-053 brought the PCS-P125 inverter into scope; the AFE cross-check now informs the PCS control study, sim/out/pcs_design/crosscheck_wolfspeed.md and docs/guide/09-pcs-p125.md)
- **Limitation (roadmap):** high-current power stage and product packaging are not covered.
- **Status:** the data brief is MANUAL (st.com closes the connection for curl; retried once on 2026-10-04: same). Direct link in SOURCES.csv.
