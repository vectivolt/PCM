# CRD-60DD12N-K - 60 kW three-phase interleaved LLC DC/DC converter, unidirectional (Wolfspeed)
Three interleaved half-bridge LLC legs with two discrete 1200 V SiC MOSFETs per position (C3M0040120K, or C3M0032120K
for the high-efficiency version, TO-247-4), six transformers and six full-bridge SiC Schottky rectifiers (C6D20065D)
that are linked in series or in parallel for the output range; TMS320F28377D controller with closed-loop firmware and a
CAN GUI; forced air. **It is not a dual active bridge** and it cannot reverse power. The folder name `crd60dd12n-k`
follows the firmware package; Wolfspeed's part number is CRD-60DD12N-K. All figures below are Wolfspeed's, from user
guide PRD-07229 Rev. 1 (March 2023), cited by PDF page; nothing here is ours or re-measured.
- **Native ratings** (Table 2 p12, Tables 1a/1b p10): input 650 / 800 / 870 V (min / typ / max), 94 A; output 200-500 V
  with the rectifiers in parallel (200 A; 130 A in the two-phase full-bridge mode used below 250 V) or 500-1000 V in
  series (100 A); 60 kW; 120-250 kHz; output ripple ±2 %; power density 4.83 kW/L (p8). Measured efficiency
  (C3M0040120K version, Table 2): peak 98.5 % (850 V in, 708 V / 34 A out, series), full load 96.2-98.3 % depending on
  the voltages; tables of 20-100 % load on p41-46.
- **Operating concept** (p10-11, p27): the converter is the DC/DC stage of a unidirectional off-board charger; the PFC
  front end is expected to move the input bus with the battery voltage so that the LLC stays near its resonance ratio
  (output = 0.411 x input in parallel, 0.822 x in series for 660-840 V of bus); the output set-point is accepted only at
  the band edges (650-660 V and 840-870 V). Below 250 V output the controller restarts as a two-phase full bridge with
  combined frequency and phase-shift control. Not for direct battery connection: no charging algorithm (p8, p12).
- **Power stage** (p25): input film capacitors (4), output 12 film + 3 electrolytic capacitors; six transformers on
  three-leg gapped PQ6562 ferrite cores, 12:10:10; resonant tank per phase L_m 30 µH, L_r 7.5 µH, C_r 102 nF
  (f_r ≈ 182 kHz, calculated); a current transformer per resonant tank; two MOSFETs (Q64, Q65) balance the output
  capacitor halves in series mode at no or light load. No inrush limiter on either port (p38).
- **Gate drive and control** (p25-26): UCC5350MCQDQ1 drivers (Miller clamp, no desaturation detection), each on a RECOM
  R15P21503D isolated supply (+15 / −3 V); the control board is referenced to the input DC−; output voltage and its
  OVP / UVP signals cross to the controller through optocouplers; a 14.5 V auxiliary board makes four isolated rails
  (Table 4). Measured dead time at the gates 137 / 153 ns (Table 18 p59).
- **Protections** (Table 6 p34): output OVP > 1050 V (series) / > 550 V (parallel); input OVP > 890 V, UVP < 600 V;
  output short < 300 V (series) / < 100 V (parallel); resonant-tank OCP 120 A peak; power limit 60 kW ± 1.5 kW;
  current limit 100 / 133 / 200 A ± 1.5 A by output range. Tank OCP and short-circuit protection are described as
  one-shot protections that need a system reset - the firmware clears them with an OFF command (FIRMWARE-NOTES §4.3).
- **Communication** (p27-29, p59-62): CAN 2.0B at 125 kbit/s with 29-bit identifiers; one control frame (mode, on / off,
  power, voltage, current), periodic status and telemetry every 0.5 s and 3 s; the C# GUI needs a GCAN USBCAN-I adapter.
- **Firmware and GUI package** (supplied by the owner, unpacked in `firmware/`): CCS project V1.00 for the F28377D
  (linked 2023-03-13) with full source, and the C# GUI source and binaries written for the 30 kW predecessor
  CRD-30DD12N-K. Read and summarised in [FIRMWARE-NOTES.md](FIRMWARE-NOTES.md).
- **Informs:** the DAB-D60 and PCS-P125 firmware requirements through its protection classes, recovery rules,
  calibration storage, start inhibits and service interface (REFERENCE-LESSONS §6); the PCS ↔ DC/DC coordination idea of
  a bus that follows the battery. It does **not** inform the DAB modulation law, power reversal, flux balance or
  parallel-branch sharing.
- **Limitation:** LLC, unidirectional, diode output stage, 650-870 V input (our DAB port 1 is 590-950 V); discrete
  Wolfspeed devices and a resonant tank that are not ours; a laboratory evaluation board "not designed to meet any
  industrial, technical, or safety standards" (p1).
- **Files:** user guide `crd60dd12n-k_user-guide_prd-07229.pdf` (plain download from assets.wolfspeed.com on
  2026-10-05, link from the product page https://www.wolfspeed.com/products/power/reference-designs/crd-60dd12n-k/ -
  the address without the hyphen after "crd" returns 404); the firmware / GUI zip and its unpacked folder. See
  SOURCES.csv. The design-files archive (https://assets.wolfspeed.com/uploads/2023/03/crd-60dd12n-k_all_design_files.zip:
  main, controller, driver, auxiliary-power and resonant-capacitor boards) was not fetched.
- **Related, not filed:** "Design Challenges and Considerations of Wolfspeed 60kW LLC Converter" and the 60 kW LLC
  marketing brief (both linked from the product page); the 30 kW predecessor CRD-30DD12N-K (user guide PRD-05777).
