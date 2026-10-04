# Megarevo PMD-75-G3 — published specification (competitor target for PV-P75/100/110)

- **Source:** https://www.megarevo.com/pm-modular-series-pmd-dcdc-module.html (page title "PMD-G3 DCDC Module")
- **Retrieved:** 2026-10-04 (page HTML 106,533 bytes, sha256 `aded29e66b0de18b8ddd448b2249405441db74aacf34580e5e199dbecb00f32a`; the page and its HTTP headers carry no "last updated" date)
- **Cross-check:** the datasheet linked from that page, `pmd-75-g3_datasheet_v1-0.pdf` ("75kW PMD-G3 module V1.0"), contains every one of the 35 table rows below verbatim (compared by script after Unicode NFKC normalisation).
- Status: competitor's *published* figures, not measured by us.

## Differences against the roadmap section "MPPT target" (00-roadmap-source.md, lines 175-197)

**No published number differs.** All 15 roadmap rows match the page (rated 75 kW, max 82.5 kW, 1 centralized MPPT, 250~1000 V / 550~950 V on both ports, 135 A on both ports, non-isolated, 99 %, forced air, 550x444x133 mm, 30 kg, 485/CAN + BMS). Wording-only differences:

- Roadmap "Maximum PV current 135 A" is the page row "Max. Operating Current (A) = 135" under *PV Data*; the page does not say "maximum PV current". Same for the output port.
- Roadmap "RS485, CAN; BMS interface" is page "Communication = 485, CAN" and "BMS Interface = Supported" (the "RS" prefix is the roadmap's reading).
- Roadmap "Temperature-controlled forced air" is page "Temperature-Controlled Smart Forced Air".

Published rows the roadmap table does **not** carry (new information, all in the table below): MPPT tracking accuracy >=99.9 %; PV start-up voltage 250 V; operating temperature -30~60 degC with derating above 45 degC; altitude derating above 3000 m; <=95 % RH non-condensing; IP20; noise <=65 dB at 1 m; cabinet fixed installation; voltage and current accuracy <1 % at 100 % Pn; standby <20 W; protection list (over/under-voltage and overcurrent on both ports, open-circuit, over-temperature, lightning, insulation-impedance detection, short-circuit).
The datasheet PDF adds marketing claims that are *not* in the table: "World's First Bidirectional Buck-Boost Capability", MPPT efficiency up to 99.99 %, protection response <=3 ms, power density 2.55 MW/m3, voltage range 250-1000 VDC (input/output), sampling error <1 %.

Consequences for REQUIREMENTS.md (not edited here, owner's decision): PV-20 (ambient -30..+60 degC, full power to 45 degC) is currently tagged *A (PMA family figure)*; the PMD page states the same figures for the PMD itself, so it could be re-tagged *R*. PV-11 (>=99 % peak) equals, not beats, Megarevo's "Max. Efficiency 99%".

## Derived checks (ours, from the published numbers; not Megarevo statements)

- 75 kW / 135 A = 555.6 V and 82.5 kW / 135 A = 611.1 V. Below 611 V the 82.5 kW maximum is current-limited (this is PV-22). At the lower edge of the "full-load" window, 550 V x 135 A = 74.25 kW, i.e. 99 % of rated: the 550 V edge is rounded, not exact.
- Envelope 0.550 x 0.444 x 0.133 m = 0.03248 m3; 82.5 kW / 0.03248 m3 = 2.54 MW/m3, which is the datasheet's "2.55 MW/m3" (so that claim uses maximum power and outer dimensions). Rated 75 kW gives 2.31 MW/m3.
- 133 mm is 3U (3 x 44.45 = 133.35 mm), consistent with the roadmap's "3U-class".

## Specification table (verbatim from the page; section rows in bold)

| Parameter | PMD-75-G3 |
|---|---|
| **DC Data** | |
| Rated Power (kW) | 75 |
| Max. Power (kW) | 82.5 |
| MPPT Channels | 1 (Centralized 75kW Design) |
| **PV Data** | |
| Operating Voltage Range (V) | 250~1000 |
| Full-Load Voltage Range (V) | 550~950 |
| Max. Operating Current (A) | 135 |
| MPPT Tracking Accuracy | ≥99.9% |
| PV Start-up Voltage (V) | 250 |
| **Output Data** | |
| Voltage Range (V) | 250~1000 |
| Full-Load Voltage Range (V) | 550~950 |
| Max. Operating Current (A) | 135 |
| **Protection Data** | |
| Over/Under Voltage Protection (Both Ports) | Supported |
| Overcurrent Protection (Both Ports) | Supported |
| Open-Circuit Protection (Both Ports) | Supported |
| Overtemperature Protection | Supported |
| Lightning Protection | Supported |
| Insulation Impedance Detection | Supported |
| Short-Circuit Protection | Supported |
| Isolation Method | Non-Isolated |
| **General Data** | |
| Max. Efficiency | 99% |
| Operating Temperature Range (℃) | -30~60 (>45 Derating) |
| Operating Altitude (m) | ＞3000 Derating |
| Relative Humidity | ≤95% (Non-condensing) |
| IP Rating | IP20 |
| Noise Level (dB) | ≤65 @1m |
| Cooling Method | Temperature-Controlled Smart Forced Air |
| Dimensions L*W*H (mm) | 550*444*133 |
| Weight (kg) | 30 |
| Mounting Method | Cabinet Fixed Installation |
| **Communication Data** | |
| Communication | 485, CAN |
| BMS Interface | Supported |
| **Other Data** | |
| Voltage Accuracy | ＜1%@100%Pn |
| Current Accuracy | ＜1%@100%Pn |
| Standby Power Consumption (W) | ＜20 |
