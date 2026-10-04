# STDES-DABBIDIR - 25 kW bidirectional dual-active-bridge DC/DC (ST)
ACEPACK 2 SiC power modules, STM32G474RE control, 100 kHz; STSW-DABBIDIR firmware exists (not fetched). Roadmap: "exceptionally complete 25 kW DAB manufacturing package".
- **Ratings below are NOT from a downloaded PDF** (st.com resets every curl connection, see below); they come from web-search summaries of data brief DB4879: 800 V nominal in, 400 V nominal out, 25 kW, 100 kHz, 98.4 % peak efficiency. Verify when you open the PDF.
- **Informs:** DAB-D60 control cross-check (DAB-08: soft switching, current/voltage loops, soft-start, protections); STGAP2SICS gate-driver alternate (ECO-05).
- **Limitation (roadmap):** a reproducible 25 kW, 800-to-400 V reference; DAB-D60 is 60 kW with 590-950 V / 400-900 V ports.
- **Status: all five documents are MANUAL** - data brief DB4879, user manual UM3198, test report TN1435 (extra), schematic v2.0, BOM v2.0. st.com's edge closes the HTTP/2 stream for non-browser clients (retried once on 2026-10-04 with plain curl: curl exit 92 again; not circumvented). Direct links are in SOURCES.csv; open them in a browser.

## Firmware package (supplied by the owner)
- `stsw-dabbidir.zip` = ST's STSW-DABBIDIR (manual download from st.com, 2026-10-04, 232,325 bytes), unpacked in `stsw-dabbidir/`: `STSW_DABBIDIR.hex`, `STSW_DABBIDIR.out` (ELF with debug info), `STSW_DABBIDIR.sim`. Binaries only, no source; registered in `SOURCES.csv`. This supersedes "firmware exists (not fetched)" above.
- Static inspection only (nothing was executed): [FIRMWARE-NOTES.md](FIRMWARE-NOTES.md) - target STM32G474 (Cortex-M4F), IAR EWARM 9.20.2, HRTIM PWM at 100 kHz, control structure, decoded parameters, what the binary cannot tell, and a side-by-side with our DAB control (DAB-08, REF-1). The parameters are also in `sim/data/st_dab_firmware_params.csv`.
- The `.sim` is a different build from the `.hex`/`.out` (older time stamp, 2022-11-29; dead time 600 ns vs 400 ns); the notes use the `.hex`/`.out` values.
