# Megarevo PMA0125 / PMA0135 — published specification (competitor target for PCS-P125)

- **Source:** `pma-125-135kw_datasheet_v1-0.pdf` ("PMA inverter module 125-135kW", V1.0, page 2), linked from https://www.megarevo.com/pm-modular-series-pma-model.html
- **Retrieved:** 2026-10-04 (manifest row in `docs/SOURCES.csv`); transcribed 2026-10-05 from the PDF page image.
- Status: competitor's *published* figures, not measured by us. Where the sheet gives one value across both columns it is repeated in both.
- Page 1 "key strengths" (marketing text, not in the table): front maintained and back maintained optional; HMI support 10.1" touch screen / local web / upper computer; independent air duct fan cooling; "well established industrial IGBT power modules"; integrated ground-fault monitoring, residual current monitoring and AC relay automatic checking; off-grid supports unbalanced and half-wave loads; on-grid supports split-phase power control; support for small-power diesel generator grid connection; remote upgrade, integrated local fault recorders. The product web page adds: three-level space vector modulation (SVPWM); midpoint balance with DC-component adjustment; independent and parallel operation of modules.

## Specification table (verbatim; section rows in bold)

| Parameter | PMA0125 | PMA0135 |
|---|---|---|
| **DC data** | | |
| Max. DC continuous power (kW) | 137 | 148 |
| Operating DC voltage range (Vdc) | 590~950(3W+PE)/650~950(3W+N+PE) | 590~950(3W+PE)/650~950(3W+N+PE) |
| Full load DC voltage range (Vdc) | 600~900(3W+PE)/680~900(3W+N+PE) | 600~900(3W+PE)/680~900(3W+N+PE) |
| Max. DC current (A) | ±250 | ±270 |
| Max. DC continuous current (A) | ±230 | ±247 |
| Voltage stabilization accuracy | ±1% | ±1% |
| Current stabilization accuracy | ±2% (Of rated power) | ±2% (Of rated power) |
| **AC data (on-grid)** | | |
| Rated AC power (kW) | 125 | 135 |
| Max. AC power (kVA) | 150 | 162 |
| Max. AC continuous power (kVA) | 137 | 148 |
| Grid type | 3W+PE or 3W+N+PE | 3W+PE or 3W+N+PE |
| Rated AC voltage (Vac) | 400/230 | 400/230 |
| Rated AC current (A) | 180 | 194 |
| Max. AC current (A) | 216 | 233 |
| Max. AC continuous current (A) | 198 | 214 |
| THDi | < 3% (Of rated power) | < 3% (Of rated power) |
| Rated voltage/voltage range (V) | 400±15% (According to load standards) | 400±15% (According to load standards) |
| Rated frequency/frequency range (Hz) | 50±5 / 60±5 (According to load standards) | 50±5 / 60±5 (According to load standards) |
| Adjustable power factor range | >0.99; -1~ +1 | >0.99; -1~ +1 |
| **AC data (off-grid)** | | |
| Rated AC power (kW) | 125 | 135 |
| Max. AC power (kVA) | 150 | 162 |
| Max. AC continuous power (kVA) | 137 | 148 |
| Max. AC current (A) | 216 | 233 |
| Max. AC continuous current (A) | 198 | 214 |
| Rated voltage (V) | L-N:220/230/240; L-L:380/400/415 | L-N:220/230/240; L-L:380/400/415 |
| Rated frequency (Hz) | 50/60 | 50/60 |
| Voltage accuracy | ±1% | ±1% |
| Frequency accuracy (Hz) | 50/60 ± 0.2% | 50/60 ± 0.2% |
| THDu | <3% (Of linear balance load) | <3% (Of linear balance load) |
| DC voltage component | <0.5%Un(Of linear balance load) | <0.5%Un(Of linear balance load) |
| Output voltage imbalance | ±1%; 120 ±1° (Of linear balance load) | ±1%; 120 ±1° (Of linear balance load) |
| Load unbalance | 100% Three-phase unbalanced | 100% Three-phase unbalanced |
| Overload capacity | <110%:Continuous; 110%~<120%:2min; >120%:200ms | <110%:Continuous; 110%~<120%:2min; >120%:200ms |
| **Communication parameters** | | |
| Human-computer interaction | 10.1" Touch screen / Local web/ Upper computer (Optional) | 10.1" Touch screen / Local web/ Upper computer (Optional) |
| Communication interface | Ethernet/RS485/CAN | Ethernet/RS485/CAN |
| Communication with BMS | RS485/CAN (Optional) | RS485/CAN (Optional) |
| Communication with EMS | RS485/Ethernet (Optional) | RS485/Ethernet (Optional) |
| **General** | | |
| Max. efficiency | 98.5% | 98.5% |
| Charge/discharge switching time (ms) | < 20 | < 20 |
| Relative humidity | < 95% (Non-condensing) | < 95% (Non-condensing) |
| Operating temperature range (℃) | -30~+60 (>45 Derating) | -30~+60 (>45 Derating) |
| Storage temperature range (℃) | -40~+70 | -40~+70 |
| Max. operating altitude (m) | 5,000 (>3,000 Derating) | 5,000 (>3,000 Derating) |
| Noise emission (dB) | < 70 | < 70 |
| Over voltage category | DC type II / AC type III | DC type II / AC type III |
| Pollution degree | External PD3; Internal PD2 | External PD3; Internal PD2 |
| Protection degree | IP20 (Power compartment) IP5X (Control compartment) | IP20 (Power compartment) IP5X (Control compartment) |
| Cooling | Intelligent forced air-cooling | Intelligent forced air-cooling |
| DC connector | Quick plug terminal (front maintained), hot plug (quick connector) (back maintained) | Quick plug terminal (front maintained), hot plug (quick connector) (back maintained) |
| AC connector | Quick plug terminal (front maintained), hot plug (quick connector) (back maintained) | Quick plug terminal (front maintained), hot plug (quick connector) (back maintained) |
| Installation style | Rack-mounted (Vertical/horizontal) | Rack-mounted (Vertical/horizontal) |
| Dimension W*D*H (mm) | 690(without mounting ears 650)*700*220 5U | 690(without mounting ears 650)*700*220 5U |
| Weight (kg) | 75 | 75 |

## Derived checks (ours, from the published numbers; not Megarevo statements)

- 216 A × 400 V × √3 = 149.6 kVA and 198 A × 400 V × √3 = 137.2 kVA: the sheet's "Max. AC power 150 kVA" is the 216 A two-minute overload tier and the "137 kVA continuous" is the 198 A tier, so the three tiers in the table are 180 A rated / 198 A continuous (110 %) / 216 A for 2 min (120 %); the "> 120 %: 200 ms" tier has no current figure.
- 125 kW / 230 A = 543 V and 137 kW / 250 A = 548 V: at the 590-650 V window bottoms the DC current, not the power, limits (continuous 230 A at 600 V = 138 kW, i.e. full power is available over the whole full-load window).
- The 2026 catalogue (brochure page 62) states 4,000 m for the same module; the datasheet states 5,000 m. Both give derating above 3,000 m.
- 220 mm is 5U (5 × 44.45 = 222.25 mm).
