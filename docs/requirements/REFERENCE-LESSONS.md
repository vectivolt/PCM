# Lessons from recent TI reference designs (REF-2)

**Requirement:** REQUIREMENTS.md §7 REF-2 — "check out TI new designs like TIDA-010938 … make sure that we have everything".
**Status:** review of 2026-10-04, complete for the 14 designs and 2 application notes listed. **Honesty:** TI's numbers are
TI's lab measurements on their hardware; ours are calculated/simulated (nothing here is bench-validated). A TI design
removes unknowns, it does not set our ratings (REQUIREMENTS.md §6 rule 5).

Verdicts: **ADOPT** = change our design (where is stated) · **CONSIDER** = owner/designer decision, trade-off stated ·
**N/A** = not applicable (why). Only differences are listed; where TI does what we already do, nothing is written.
Citations: TI document id + page of the PDF on file under `docs/reference-designs/ti/<id>/`.

## 1. Designs reviewed

| id | what | doc (date) | relevant to |
|---|---|---|---|
| TIDA-010938 | 10 kW GaN string inverter + bidirectional battery DC/DC (2 × 5 kW PV boost, 2-phase interleaved buck/boost, HERIC inverter) | TIDUF64 Rev C (Jul 2026) + SLLA498 Rev A (Dec 2024) | PV cell, port sensing, IMD inputs |
| TIDA-010957 | 15–30 kW 3-phase+N three-level flying-capacitor converter, 650 V GaN on a 650–900 V link | TIDUFG9 Rev A (Mar 2026) + SDAA195 (Jan 2026) | 3-level PV cell option (D-031) |
| TIDA-010210 | 11 kW 3-phase ANPC, 600 V GaN on an 800 V link, CLB hardware interlocks | TIDUEZ0 Rev A (Mar 2022) | 3-level option, protection |
| PMP41037 | 1 kW 800 V → 12 V DCX with two 650 V GaN half-bridges in series, active voltage balancing | TIDT332 (2024) | stacked options, start-up |
| TIDA-010949 | 600 W GaN four-switch buck-boost PV power optimizer | TIDUF99 (Nov 2024) | PV cell modulation |
| TIDA-010054 | 10 kW SiC DAB (on file, re-read) | TIDUES0 Rev F (Apr 2026) | DAB-D60 |
| PMP23223 | UCC21732 + UCC14xxx-Q1 smart gate driver with bias | TIDT283 (May 2022) | GDRV-HB |
| PMP22817 | UCC5870/71-Q1 + UCC14240-Q1 gate driver and bias, 800 V | TIDT256 Rev A (Mar 2022) | GDRV-HB |
| TIDA-011011 | isolated gate driver + isolated bias for 3.3 kV SiC | TIDUFH7 (Mar 2026) | GDRV-HB (1700/2000 V devices) |
| TIDA-010232 | AFE for DC insulation monitoring (EV charging, solar) | TIDUEZ8 Rev D (Apr 2026) | PV-C5 IMD |
| TIDA-010985 | resistive-bridge IMD for 800 V with large Y capacitance | TIDUFG5 Rev A (Jan 2026) | PV-C5 IMD |
| PMP41009 | 350–1000 V in, 56 W quasi-resonant flyback | TIDT266 (Apr 2022) | AUX-HV |
| PMP41031 | 350–1500 V in, 150 W two-switch flyback, 4 outputs | TIDT355 (Oct 2023) | AUX-HV sizing (D-022) |
| TIDA-010955 | AFE for machine-learning DC arc detection (solar) | TIDUF85 Rev A (Dec 2024) | PV port (arc fault) |

## 2. Per-design findings

### TIDA-010938 — 10 kW GaN string inverter with BESS port (TIDUF64 Rev C, Jul 2026)
**What it is:** two 5 kW PV boost stages (50–500 V string → 400 V nominal / 520 V max link, LMG3522R030 650 V GaN +
C6D20065G SiC Schottky, 130 kHz, Bourns 145451 120 µH), a 10 kW bidirectional battery DC/DC (2-phase interleaved
synchronous half-bridges, 65 kHz per leg at 180°, 30 A, battery 50–500 V, Bourns 145452 200 µH per phase) and a 4.6 kW
HERIC/H-bridge inverter, all on one F280039C/F28P55x referenced to DC− (TIDUF64 p.1, p.5, p.16–18). Measured: boost
99.3 % (350→400 V, p.37–38), battery DC/DC 99.4 % peak (320 V↔400 V, p.39–41), 25–30 ns switching edges (p.37, p.41).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | Phase current by 1.5 mΩ shunt + AMC1302 **at each switch node**, powered from the GaN LDO (TIDUF64 p.19–20) | D-030 removed the switch-node shunt + AMC3302 from PVCELL (71–92 V/ns against a 95 V/ns minimum CMTI) | N/A | Confirms D-030: TI's nodes slew ≈16 V/ns (400 V in 25 ns, p.37), five times slower than our SiC legs |
| 2 | PV-array capacitance to PE "as high as 200 nF/kWp in damp environments" (TIDUF64 p.9; SLLA498 p.5) | `A_C_PV_PE` = 130 nF/kW × 75 kW = 10 µF (`sim/port_design.py`); IMD τ = 4.3 s | **ADOPT** | Use 200 nF/kWp as the worst case: 16.5 µF for PV-P75 (82.5 kWp), 22 µF for PV-P110 → IMD time constant ≈ 7 s instead of 4.3 s (≈ 21 s per state at 3τ) and the Y-capacitor / PE-current budget (port report §8, §10) |
| 3 | Controller referenced to DC−: non-isolated low-side drive, shunt + INA181 and op-amp dividers (TIDUF64 p.1, p.17–18) | PELV control card, reinforced isolation on every HV signal | N/A | 1000 V field-wired module with external comms and the dual-channel safety chain (D-012) on PELV; the isolator saving is small at 75 kW |
| 4 | Isolated CAN / RS-485 with integrated isolated power (ISOW1044, ISOW1412) (TIDUF64 p.1, p.28) | SYS-IO-AUX: ISO1042 / ISO1410 + one SN6505 push-pull + transformer + rectifier per port (4 sets) | CONSIDER | Removes ≈ 4 parts per port; check cost against the SN6505 set and the D-031 Asian alternatives (integrated-power isolated transceivers exist from Chinese makers) |
| 5 | MPPT stage is boost-only; TI states a buck or buck-boost MPPT stage "will be less efficient" (SLLA498 p.3) | buck **and** boost required (PV-10, PV-17) | N/A | Requirement-driven; it supports keeping the both-legs-switching band narrow (D_max 0.948, `sim/out/pv_control` §1) |
| 6 | HV auxiliary rail = non-isolated UCC28710 buck from the 520 V link (TIDUF64 p.22–23) | AUX-HV isolated flyback, 1700 V SiC, 200–1000 V | N/A | 520 V class, no isolation; not usable at 1000 V |

### TIDA-010957 — 3-phase+N three-level flying-capacitor converter, GaN (TIDUFG9 Rev A, Mar 2026; SDAA195, Jan 2026)
**What it is:** 4 flying-capacitor legs (3 phases + N), 16 × LMG3522R030 (650 V GaN, 15 kVA) or LMG3670R010 (25–30 kVA),
DC link 650–900 V (800 V nominal), 62.5 kHz per device / 125 kHz at the inductor (carriers 180° apart), flying capacitor
12 µF 630 V film (TDK B32676G6126K000) + 220 nF ceramic per leg, 87 µH inductors (Bourns MAG-3002584), FC voltage per leg
by AMC0311D across a 3 × 680 kΩ divider, TMCS1126 Hall on every switch-node current with an OC flag per leg, F28P55x
(TIDUFG9 p.1, p.3, p.5–9; BOM SLURB91 p.4, p.9–10). Measured 98.81 % peak at 700 V, 98.42 % at 900 V, junction 105–110 °C
at a 60 °C heatsink (TIDUFG9 p.14–18); switching at 70 kV/µs (SDAA195 p.11).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | 650 V devices only up to a **900 V** link: 450 V per device = 0.69 × V_DS (TIDUFG9 Table 1-1 p.3; SDAA195 p.2) | Gate-0 rule: continuous ≤ 0.67 × V_DSS, peak ≤ 0.85 (pv_tradeoff §6); port 1000 V, OVP 1100 V | CONSIDER (Gate-0 re-run, D-031) | In a 3-level cell on our ports a device blocks 500 V (550 V at OVP): 650 V parts = 0.77 / 0.85 → outside TI's own practice and our rule; 750 V parts = 0.67 / 0.73 → on the rule's edge, admissible only with vendor FIT-vs-V data at 500–550 V; 1200 V parts (candidate B) = 0.42 / 0.46 |
| 2 | **Flying-capacitor pre-charge/clamp network**: Zener string (3 × ZGL41-180A ≈ 540 V) + a few-ohm resistor across each outer switch S1 and S4; with MCU and aux off the rising link would otherwise put the full voltage on the outer devices; measured clamp 520 V; τ = 2 R_PR C_FC (SDAA195 §3.4 p.10, §4.1 p.11–12; SLURB91 p.4) | Candidate B carries "FC precharge before switching" as a cost item only (pv_tradeoff §4); no circuit, not in the transient checks | **ADOPT if B is chosen** | PVCELL: a clamp + pre-charge network across S1/S4 of **both** FC legs (either port can be energised first — PV precharge or battery — and AUX-HV starts at 229 V), sized so an outer device never exceeds 0.85 × V_DSS while its FC is at 0 V; gate enable only after both FCs are inside V/2 ± a window. Add its parts and the clamp energy to the B candidate in `sim/pv_tradeoff.py` |
| 3 | S1/S4 and S2/S3 strictly complementary; a violation shorts C_DC or C_FC and over-volts the other pair (TIDUFG9 §2.2.1 p.7; SDAA195 §2.1 p.4) | 2-level legs: hardware dead-time interlock on GDRV-HB (191–511 ns) | **ADOPT if B is chosen** | Hardware complementary interlock per pair on the gate-driver board (as today, one per pair) **plus** a forbidden-state and FC-out-of-window trip in the F28388D CLB/CMPSS (TIDA-010210 does its multilevel interlocks in the CLB, below) — no firmware-only path |
| 4 | FC voltage of every leg measured by its own isolated amplifier (AMC0311D, 2 V input) (SLURB91 p.9–10) | B priced with 2 isolated V sensors per cell | N/A (already planned) | Same as our B costing; keep it a direct FC measurement, not the difference of two bus readings |
| 5 | Two commutation loops (inner S2-S3-C1, outer S1-S4-C3) + an extra decoupling capacitor C2 to split the inner loop (SDAA195 §3.3 p.9) | 3-level transient check uses one lumped 25 nH outer loop (pv_tradeoff §6) | **ADOPT if B is chosen** | `sim/pv_tradeoff.py` DPT deck for B: model the inner and the outer loop separately (the inner loop sets the S2/S3 overshoot) |
| 6 | Switch-node current by an in-package Hall sensor (TMCS1126, ±120 A, 500 kHz) with a built-in 100 ns OC flag per leg, on legs switching at 70 V/ns (SLURB91 p.10; SDAA195 p.11) | PVCELL i_L: LEM LA 150-P closed-loop around an insulated lead (D-030) — no published dv/dt figure, 1.1 A offset to re-zero | CONSIDER | The 1000 V-class sibling TMCS1123 (on file: 1.3 kV reinforced working voltage, V_IORM 1697 Vpk, CMTI 150 kV/µs at V_CM 1000 V, 250 kHz, 110 ns delay, OC flag 100 ns, 80 A rms; TMCS1123 DS p.1, p.6, p.8) would give a published CMTI and a sensor-level OC layer. Limits: ±96 A range (5 V supply) is below the 96.3 A upper band of the CTRL backup trip and the 109 A saturation point (pv_control §10) — backup trip would move to ≤ 88 A upper band; the 47.5 A rms cell current must then flow through PCB copper into a SOIC-10 (R_IN 0.7 mΩ → 1.6 W, DS p.8; TIDA-010957 needed 3 oz copper for 36 A rms, TIDUFG9 p.21); TI-only (D-031: an Asian in-package Hall needs the same 1.3 kV reinforced rating) |
| 7 | AC-side pre-charge through PTC inrush limiters (Vishay PTCEL13R600LBE, 60 Ω, 500 V) + relays (SLURB91 p.6–7) | 220 Ω Miba RST 200 + thermal cut-off + supervision with 3 attempts / 30 s lock-out (port report §1, §3) | CONSIDER (low) | A PTC is self-protecting against pre-charge into a short or a welded load; needs a ≥ 1000 V DC PTC that absorbs ≈ 180 J per start (PV 4 cells at 1000 V) and repeated attempts into a short (757 J each, port report §1). No such part identified — keep ours |

### TIDA-010210 — 11 kW 3-phase ANPC, GaN (TIDUEZ0 Rev A, Mar 2022)
**What it is:** three-level ANPC inverter/PFC, 4 high-frequency LMG3422R030 (600 V GaN) + 2 line-frequency 650 V Si per
phase, 800 V link ("600-V rated switches in 800-V system"), 100 kHz, shunt (2 mΩ) + AMC3302 current sensing, SN6501
push-pull bias; 98.5–98.62 % measured (TIDUEZ0 p.1, p.8, p.10–11, p.18).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | "Hardware based interlocking protections needed to avoid device overvoltage under all operating conditions" implemented in the C2000 **CLB**, no extra parts (TIDUEZ0 p.1–2) | F28388D CLB unused; trips via CMPSS/PPB/trip zone + GDRV interlock | ADOPT if B is chosen | Same as TIDA-010957 row 3 — the F28388D has CLB tiles; put the 3-level state checks there |
| 2 | Series devices share voltage only if a defined state holds the mid node (Q6 kept on, p.5) | Candidate C (series-stacked split bus) assumes a midpoint balance loop | CONSIDER (only if C is re-opened) | Static sharing of series-connected halves needs an active state or balancing network in every mode including standby |

### PMP41037 — 1 kW 800 V → 12 V serial half-bridge bidirectional DCX, GaN (TIDT332, 2024)
**What it is:** two LMG3622 650 V GaN half-bridges **in series** across a split 760–840 V bus (up to 900 V; 3 × 22 µF
450 V per half), each half measured by its own AMC1311, firmware voltage-balancing loop (`SHB_BALANCING_CTRL`), 1 MΩ
bleeders and 220 V Zeners (BZG03C220) on the power board, LLC run as a DCX at resonance, F280039C; 98.0 % at 634 W
(TIDT332 p.1–2, p.6, p.8; power BOM TIDMCY2 p.1–2).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | Stacked 650 V stages on 800 V: per-half isolated voltage sensing + active balancing loop + passive bleeders/Zener clamps (TIDT332 p.2, p.6; BOM p.1–2) | Candidate C (series-stacked split bus) assumes a midpoint loop; no passive sharing in its costing | ADOPT if C (or any stacked option) is chosen | Passive sharing (bleeder + clamp per half) that holds with the controller off, plus per-half isolated sensing; add the bleeder loss at 1000 V to the candidate's loss model |
| 2 | Start-up procedure ramps the 800 V source at **< 1 V/ms** before the stage runs (TIDT332 p.6, §1.4.3–1.4.4) | Our ports rise through the 220 Ω pre-charge at ≈ 17 V/ms (PV, 3 cells: 1000 V / τ 59 ms) to ≈ 34 V/ms (DAB port 1, τ 30 ms) (port report §1), possibly before AUX-HV (229 V start) has the controller up | **ADOPT** (requirement for the Gate-0 re-run) | Any stacked or flying-capacitor PV cell must hold every device inside the voltage rule by passive means at ≥ 35 V/ms port ramps, in both pre-charge directions; no TI design demonstrates this |
| 3 | Transformer and resonant choke from a Chinese maker (YAXIN Electronic: YXS61041T PQ3230 1 kW, YXS50494T PQ2016 12 µH) in a TI design (BOM p.1–2) | Magnetics vendors open (MAG-2, D-031) | CONSIDER (low) | A data point that a Chinese magnetics house builds TI's HV magnetics; candidate RFQ vendor for AUX-HV T1 and the small chokes |

### TIDA-010949 — 600 W GaN four-switch buck-boost PV optimizer (TIDUF99, Nov 2024)
**What it is:** FSBB with 2 × LMG2100R026 (100 V GaN half-bridge), 80 V / 18 A, 300 kHz, 3.6 µH (Coilcraft SER2013-362MLB),
TMCS1127 Hall, P&O MPPT, PLC + wireless on one C2000; 99.0 % peak in switching mode at 15 A (TIDUF99 p.1, p.12–13; BOM p.1).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | **Stacked-carrier modulation**: one modulator 0–2, buck carrier 0–1.05, boost carrier 0.95–2; both legs switch in the 0.95–1.05 overlap, so buck / buck-boost / boost follow without a mode state machine; implemented by scaling the modulators (M_buck = 0.95 M, M_boost = 0.95 (M − 0.95)) because moving the carrier start costs ≈ 50 % PWM resolution on C2000 (TIDUF99 p.12–13) | Explicit buck / band / boost modes with 0.015 hysteresis, filtered-integrator exit and dead-time feed-forward (pv_control §1–§4) | CONSIDER (firmware) | Same band, simpler firmware, no hysteresis to tune; ours is the simulated one (unknown 150–500 ns dead time). Keep ours unless the bench shows mode chatter; then the stacked carrier is the fallback |

### TIDA-010054 — 10 kW SiC DAB, re-read for DAB-D60 (TIDUES0 Rev F, Apr 2026)
Already cross-checked in `sim/out/dab_design/report.md` §2 (L, n, phase, blocking-capacitor rule, EPS). New points only.

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | Measured efficiency **98.0 % at 10 kW**, 98.8 % peak at 4 kW (text: 97.6 % full load) (TIDUES0 Table 1-1 p.4) | Model 98.51–98.68 % at 800/800 V 60 kW; Wolfspeed measured 97.0–97.6 % (D-015) | **ADOPT** (as evidence) | D-015 / D-017: a second measured DAB on file also stays below 99 % at full load — no reference demonstrates DAB-04; keep "not claimed" and the pessimistic derating map as the guaranteed envelope |
| 2 | Planar transformer with the series inductance integrated (Payton): 24:15, L_σ 34 µH, L_m 720 µH, R_dc 43 / 16 mΩ, 50 W + 15 W at 10 kW (TIDUES0 p.23, p.26) | Wound PM 114/93 N95 + separate 2.12 µH inductor, leakage ±20 % trimmed to ±5 % (dab_design §9) | CONSIDER | MAG-2 RFQ: ask for a planar variant with integrated L (repeatable leakage, cold-plate friendly) next to the wound design; the 1850 V DC PD-free barrier is harder in laminate windings |

### PMP23223 — smart isolated gate driver with bias supply (TIDT283, May 2022)
**What it is:** one-channel board for a SiC or IGBT switch: UCC21732-Q1 (10 A, DESAT through the OC pin, internal
two-level turn-off, CLMPE external clamp, AIN/APWM) with a UCC14240-Q1 bias module from 21–27 V and an LDO for VCC/ENA
(TIDT283 p.1, p.3–4). Rails +18/−4 V (p.10); R_on = R_off = 2.2 Ω (TIDMAI8 p.1); DESAT 5.4 V / 130 ns through 2 ×
STTH112A, not fitted as shipped (TIDT283 p.9; TIDMAI7 p.2). Tested only into a 100 nF capacitor: COM–VEE ripple 252 mV pp,
VDD–VEE 865 mV at 1 kHz (p.5); UCC14240 86.9 °C, UCC21732 80.5 °C at 1.5–1.8 W, no airflow, ambient not stated
(p.10–11). No power device, no double-pulse test, no fault timing.

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | Rails set by two feedback dividers: VDD–VEE 21.9 V (77.7 k/10.0 k), COM–VEE 4.0 V (13.2 k/22.0 k); the board takes any module of the family by changing them; UCC14240 rated 25 V out (TIDMAI7 p.2, TIDMAI8 p.1) | PV +20/−4 V (VDD–VEE 23.79–24.51 V); +20/−5 V refused (up to 25.33 V) | CONSIDER | No TI design goes above 23 V total (+18/−4, +15/−5, +18/−5). Two rails fit under 25 V in our `rails()` model: **+19.9/−4.5 V** (87.6 k/10 k, 8.06 k/10 k → 24.04–24.76 V, VGS on 19.46–20.31 V) or **+18/−5 V** (82.5 k/10 k, 10 k/10 k → 22.78–23.47 V). Re-run `sim/pv_design.py` (E_off ×1.225 at −4 V against R_DS(on) at 18 V) before −4 V is frozen |
| 2 | External Miller clamp: CLMPE → 0 Ω → Si2318CDS N-FET (40 V, 5.6 A) gate-to-VEE, 10 kΩ pull-down, 0.1 µF VEE–COM at the FET (TIDMAI7 p.2, TIDMAI8 p.1) | One ZXTP25040DFH PNP follower per device on CLMPI; die +1.42 V vs V_th,min 1.48 V (D-028, 0.06 V margin) | CONSIDER | PV channel: one N-FET per device from its gate to its own C_VL, held fully on — a resistive path without the PNP's V_EB and base-drive limits. Gain not quantified: simulate it in the PV DPT deck first. Needs a CLMPE-type output (UCC21732, UCC218915); UCC21710 and the D-031 candidates on file (NSI66x1A-Q1 p.3, SiLM5992SH p.20) have an internal clamp pin only → make it a D-031 selection criterion, otherwise keep the PNPs and close D-028 on the bench |
| 3 | AIN/APWM across the barrier, thermistor with 6.19 kΩ in parallel and 10 nF (TIDMAI7 p.2). Bench: 80.33 % at AIN 0.6 V and 40 % at 2.5 V against the datasheet line 88 % → 10 % over 0.6–4.5 V (TIDT283 p.8–9) | DAB module NTC on AIN/APWM; our design check claims ±1.5 % duty = ±3 °C | **ADOPT** | TI's own points are 8–10 duty points low (≈ 0.5 V, ≈ 20 °C at 24 mV/°C), outside the datasheet window, unexplained. GDRV-HB design check and the DAB bench plan: two-point AIN→APWM check per channel before the NTC trip that D-017 relies on is trusted; no ±3 °C claim until then. TI-only feature (no AIN on NSI66x1A or SiLM5992SH) |
| 4 | Internal two-level turn-off listed as a feature (p.1); no level, duration or short-circuit test | Soft turn-off, I_STO ≥ 250 mA: 1.35 µs PV, 1.32 µs DAB (calculated) | N/A | Nothing to compare; UCC21732-only. None of the three driver designs publishes a fault-to-off measurement, so our times stay calculated |

### PMP22817 — SPI gate driver + UCC14240-Q1 bias, 800 V traction (TIDT256 Rev A, Mar 2022)
**What it is:** UCC5870-Q1 (30 A peak, SPI, DESAT, CLAMP, V_CE active clamp, ASC, AI1–AI6) with a UCC14240-Q1 bias module at
+15/−5 V (69.8 k/10.0 k and 10.0 k/10.0 k, 330 pF) fed from a 24 V LM5156-Q1 SEPIC (TIDT256 p.1; TIDMA63 p.1); gate R
3 × 4.3 Ω per side; DESAT 1.00 kΩ + one STTH112A + 100 pF, threshold not stated (TIDMA63 p.1); edges 12/14 ns unloaded,
333/326 ns into 100 nF (p.20). The report defers driver and bias performance to the datasheets (p.5).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | Bias and driver isolation 3 kVrms for 1 min, presented as enough for an 800 V DC bus (TIDT256 p.1); BOM rates the UCC14240 "> 2.5 kVrms" (TIDMA64 p.2) | UCC14241-Q1 reinforced, V_IOWM 1414 V DC, certification "planned" (D-016), 1000 V bus | CONSIDER | TI gives no working voltage or certificate for this family at 800 V, so nothing here closes D-016; ask TI for the VDE 0884-17 / IEC 60747-17 certificate status |
| 2 | **UCC15241-Q1** (ti.com product page, 2026-10-04): VIN 21–27 V (32 V abs max), 2.5 W at T_A ≤ 85 °C, > 2.0 W at 105 °C, 25 V output class, < ±1.3 % regulation, CMTI > 150 kV/µs; reinforced 7071 Vpk (IEC 60747-17), 5000 Vrms UL 1577 and CQC listed as **planned** | UCC14241-Q1: 2.0 W (85 °C) / 1.5 W (105 °C) | CONSIDER | Same 25 V ceiling and ±1.3 % → +20/−5 V still refused; certification also only planned → no D-016 relief. Gain: +25 % power, lifting the 3 × C3M0016120K DAB case (1.66 W) from margin 0.91 to ≥ 1.2 at 105 °C. Pin compatibility unverified (datasheet not on file). TI-only |
| 3 | Bias output overshoots **+0.5 V** at enable, and also on a VIN ramp with ENA held high, 75 mA load (TIDT256 p.19) | +24V_GD switched by CHB_OK with ENA on VCC — TI's VIN-ramp case. DAB VGS on ≤ 15.45 V vs BZX84-B16 knee 15.70 V; PV ≤ 20.55 V vs 21.60 V | **ADOPT** | `gen/gdrv.py` design check: add a +0.5 V start-up term. The DAB rail then reaches 15.95 V, past the B16 knee — harmless only while UVLO/RDY hold the gate low until PG (state it in the check). PV reaches 21.05 V and stays below |
| 4 | Bias thermal: 1.6 W (100 nF, 40 kHz) → UCC14240 73 °C, driver 56 °C, gate R 76 °C at 21–23 °C ambient, no fan (p.14); no efficiency data | Derating by ambient only: 2.0 W (85 °C) / 1.5 W (105 °C); DAB 1.15 W, PV 0.78 W | CONSIDER | ≈ 32 K/W self-heating (our scaling): DAB channel ≈ 97 °C at 60 °C local ambient — OK; the 3 × C3M0016120K option (1.66 W) ≈ 113 °C, beyond the 105 °C line → that option needs UCC15241 or airflow |
| 5 | V_CE active clamp (2 × SMCJ300A drain → VCECLP, FSV340AF into the gate, 100 Ω / 2.2 nF), ASC, AI1–AI6 over SPI (TIDMA63 p.1, TIDMA64 p.1) | Soft turn-off + RC damper; safe state = all off with bias removed (D-012) | N/A | 2 × 300 V TVS would conduct on our 1000 V bus (and on TI's 800 V); our worst normal peak is 1401 V = 0.82 × V_DSS. ASC is a motor safe state; SPI and AI are UCC5870-only |

### TIDA-011011 — isolated gate driver with isolated bias for 3.3 kV SiC (TIDUFH7, Mar 2026)
**What it is:** half-bridge board: 2 × UCC218915-Q1 pre-drivers (2.8 A, DESAT, soft shutdown, CLMPE, ASC; 1.06 kVrms working,
> 200 V/ns) into BUK6D43-40PX / BUK6D23-40EX buffers; 2 × UCC35131-Q1 bias modules, +18/−5 V from 12 V (TIDUFH7 p.1–4;
SLVRC10 p.1–2); gate R per channel 2 × 3.3 Ω on, 2 × 1 Ω off (SLVRC10 p.2; SLURBB9 p.1). Double-pulse on a 2.3 kV / 1.1 mΩ
module at 1.5 kV with 2.5 Ω: ≈ 40 V/ns at turn-off (400 A), ≈ 50 V/ns at turn-on (320 A); plots show ≈ 1.66 kV turn-off
peak (TIDUFH7 p.5–6). No DESAT threshold, blanking or short-circuit test.

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | 2.8 A pre-driver → 120 Ω → P/N-FET buffer (10 kΩ pull-ups/downs); soft shutdown bypasses the buffer: SSD/GATE → 32.4 Ω → gate (SLVRC10 p.2; TIDUFH7 p.3) | DAB R_G,off 0.27 Ω, chosen so the UCC21710 sink stays ≤ 10 A (9.87 A); costs E_off +27 % against the CRD's 0 Ω (`dab_spec.json`) | CONSIDER | DAB channels: a buffer allows R_G,off → 0 Ω and a 2.8–5 A Asian pre-driver (widens D-031). Cost: 2 FETs + 4–5 resistors per channel, extra delay (not stated by TI), soft turn-off needs its own path to the gate |
| 2 | CLMPE → 10 Ω → BUK6D23-40EX N-FET at the gate; the gate steps from ≈ −3.5 V to −4.8 V ≈ 300 ns into turn-off when the clamp engages (SLVRC10 p.2; TIDUFH7 p.5 Fig. 3-4, read from plot) | PNP followers on CLMPI | CONSIDER | Same lesson as PMP23223 #2; the clamp only adds hold-off near VEE, so our timing check (clamp on ≤ 179 ns vs 191 ns dead time) still governs |
| 3 | UCC35131-Q1: VIN 5.5–20 V, 2.0 W at 85 °C, > 5 kVrms, 8.2 mm creepage/clearance; VDD–COM 15.0 k/2.40 k, VEE by one 51 kΩ, BSW + 3.3 µH (TIDUFH7 p.3; SLVRC10 p.1) | UCC14241-Q1 from +24V_GD (21.6–26 V) | N/A | Input range excludes our 24 V rail (each card would need a 12 V stage); total-rail limit and certification not stated; TI-only |
| 4 | Reaches 3.3 kV by referencing the MCU to the DC-link midpoint; parts rated 1050 Vrms working (TIDUFH7 p.2) | PELV control referenced to PE; the barrier holds the full 1000 V (1100 V trip) to earth; 8 mm packages are below the 10 mm reinforced value at PD2 (ECO-10) | N/A | Midpoint referencing halves the barrier stress only where the midpoint is earthed — not our system. The 8.2 mm package does not close the ECO-10 creepage item |

### TIDA-010232 — AFE for insulation monitoring, EV charging and solar (TIDUEZ8 Rev D, Apr 2026)
**What it is:** electric-bridge DC insulation monitor: a 0.1 % thin-film branch per pole (68.1 k for 400 V, 280 k for 800 V;
R_inAMC 120 / 250 Ω) switched to PE one pole at a time by TPSI2140-Q1, read by AMC3330 into a C2000 ADC; two states solve
R_isoP / R_isoN. Measured uncalibrated: 1.5–3.8 % at 40 k / 200 k (400 V), ≤ 6.0 % over 20–200 k (800 V); 1.7 ms per
measurement at negligible C_iso (TIDUEZ8 p.8, p.25–28).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | **Loss-of-PE detection**: an open PE reads as R_iso = ∞; fix with known low-MΩ resistors pole-to-PE on a **separate** PE connection, or a second SSR pair (TIDUEZ8 p.20–21); IEC 61557-8 asks the test function to report connection faults (p.7) | One shared PE net: an open IMD PE lead reads as perfect insulation | **ADOPT** | PV-PORT: separate PE pins for the AUX-divider end and the test-string end; CTRL firmware: no swing between the two states, or AUX ≈ 0 V, = "IMD PE open" fault |
| 2 | Wait ≥ 3τ, τ = (R_iso ∥ R_st)·C_iso, and keep a branch connected < 10 s (TIDUEZ8 p.4, p.7); per state 500 µs settle + 20 interleaved samples averaged (p.25) | τ 4.3 s at 10 µF / 1 MΩ → ≈ 13 s per state, ≈ 26 s per result (≈ 43 s at the 16.5 µF worst case of TIDA-010938 row 2); no time limit set | **ADOPT** (with TIDA-010985 row 1) | Keep R_t; add the prediction of TIDA-010985 and a ≤ 10 s branch-on limit to the CTRL IMD specification |
| 3 | Accuracy falls above 1 MΩ (800 V: up to 12.8 % at 1 M, 48 % at 5 M); firmware clamps results above 1 M to "1 M" (TIDUEZ8 p.27–28) | Calculated 9.9 % at 33 k, 9.8 % at 330 k, 16–17 % at 1 M with IDSS max → end-of-line baseline needed | CONSIDER | Owner sets the PV-C5 warning level; if it is ≤ ≈ 330 k, report "> 1 MΩ" as TI does and drop the EOL baseline |
| 4 | TPSI2140-Q1: 1200 V stand-off, avalanche-rated, survives hipot/surge at ≤ 2 mA (TIDUEZ8 p.11; p.29 says "1.4 kV"); the open switch sees the full pole-PE voltage; built for 400 V, tested at 800 V (p.3, p.26) | C2M1000170D pair (1700 V) + TPSI3050 | N/A | 1200 V is below our 1100 V OVP plus the SPD residual on a pole-PE string — keep ours |

### TIDA-010985 — resistive-bridge IMD for 800 V with large Y capacitance (TIDUFG5 Rev A, Jan 2026)
**What it is:** quasi-balanced bridge R_sN 500 k / R1 200 k / R_sP 500 k permanently across DC+/DC− (24 × 200 k, 0.1 %); two
SSRs tie PE to either tap (ratios 7:5 / 5:7), 1 s per state; RES60A-Q1 12.5 M (1/315) dividers into an MSPM0 on PE ground.
Measured at 1000 V with up to 9 µF: R_iso ≤ 4.4 % (3σ) to 2 MΩ, C_iso ≤ 11.8 %; < 2 s at 4 µF / 1 MΩ (TIDUFG5 p.1–7, p.17;
SLVRBZ5 p.2). The guide names TPSI2240-Q1, the schematic and BOM fit TPSI2140-Q1.

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | **3-sample exponential prediction** at equal 330 ms spacing: V∞ = (V0·V2 − V1²)/(V0 − 2V1 + V2); 1 ms ADC, 128× averaging, 330 predictions averaged; C_iso = τ/R_parallel with τ = −(V0 − V∞)/V′(t0); Vp and Vn sampled simultaneously and solved as a ratio (TIDUFG5 p.8–12, p.19) | Settle-and-wait, no C_iso output | **ADOPT** | CTRL firmware IMD specification (no TI part needed; the F28388D's floating point removes TI's fixed-point caveat, p.11): AUX1/AUX2/VA on separate ADCs from one trigger, C_iso reported, target ≤ 10 s at 10–16.5 µF, proven on the bench |
| 2 | Balanced bridge keeps the pole-PE swing ≤ 10 % of V_DC (IEC 61851-23); 0.85 W continuous, ≤ 2 mA (TIDUFG5 p.3–4, p.7) | Pole-PE test strings: 43 % swing at 1 MΩ, 75 % at ∞; full voltage on the 1700 V SiC pair; ≈ 1 W only while testing | CONSIDER | For 500 k/200 k/500 k at our ports: 12.5 % swing, τ 2.2 s at 1 MΩ / 10 µF (our calc); the open SSR sees ≤ 0.58 V_DC (642 V at 1100 V), so a 1200 V SSR could replace TPSI3050 + 2 SiC (TI-only part). Costs ≈ 1 W continuous and halves the 33 k signal (4.2 V vs 8.0 V at 250 V) |
| 3 | UL 2231-2: ±15 %, < 10 s; EV thresholds fault 100 Ω/V, warning 500 Ω/V (TIDUFG5 p.2–3) | 33 kΩ = 1000 V / 30 mA (EN 62109-2 start-up criterion as we read it; standard not on file); no accuracy or time target | CONSIDER | Owner: write PV-C5 as the IEC 62109-2 start-up insulation check + an optional warning level, ≤ 15 %, ≤ 10 s. 100 Ω/V would trip on large arrays |
| 4 | IMD PE return through jumper J9, marked for removal during the dielectric withstand test (SLVRBZ5 p.3) | Dividers and test strings permanently on PE; open SiC pair rated 1700 V | **ADOPT** | PV-PORT (ECO-10): IMD PE return on a link the hipot opens, or prove C2M1000170D avalanche at (V_hipot − 1.7 kV)/992 kΩ |
| 5 | TI notes that the bridge itself lowers the insulation resistance while it measures (TIDUFG5 p.3) | Each module's test string looks like a fault to every other IMD on a shared bus; SYS-IO-AUX also accepts an external IMD_ALARM | **ADOPT** | System specification (our inference): measure only while port B is open, or allow one active IMD per galvanically connected system and inhibit the others |

### PMP41009 — 350–1000 V input, 14 V / 56 W quasi-resonant flyback (TIDT266, Apr 2022)
**What it is:** UCC28740-Q1 QR flyback with a cascode of two STF2N95K5 (950 V Si, heatsinked); upper gate held at ≈ 700 V by
6 × 200 k + 2 × P6KE350A, 12 V Zener + 1 nF gate-source; Würth Midcom ER28/17 transformer (data not published), 120 V
Schottky, TL431 + FOD817A. Measured 84.6 % at 1000 V / 56 W; switch node 1.51 kV and lower FET 790 V at 1000 V / 4 A,
≈ 34 kHz; no input fuse or surge parts, no short-circuit, overload or surge test (TIDT266 p.1–8; TIDMAD8 p.1).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | Si cascode: 790 V on the lower FET (83 %), ≈ 720 V on the upper (76 %) at 1000 V; the high turns ratio allows a 120 V Schottky (TIDT266 p.8; TIDMAD9 p.1) | One C3M0900170J: 77 % at 1000 V, 1489 V at the OV stop; n = 6 forces a 400 V ultrafast rectifier (1.07 W) | CONSIDER | D-031 cost option: one Asian 1700 V SiC vs two commodity 950–1000 V Si FETs (+ ≈ 0.3 W in the 1.2 MΩ gate string at 1000 V, two heatsinks); the split must be re-proven at 1100 V + surge, and TI's 83 % breaks our 80 % rule |
| 2 | HV start-up through 4 × 300 k into the UCC28740 HV pin, 2 × P6KE300A clamp; minimum input 350 V (TIDMAD8 p.1; TIDT266 p.2) | Depletion-FET start-up, starts at 229 V | N/A | 350 V fails PV-C2 (start ≤ 250 V) |
| 3 | Measured at 1000 V: **1.3 W no-load**, 68.9 % at 6.9 W, 77.7 % at 13.8 W, 82.5 % at 27.9 W, 83.3 % at 34.8 W; 0.3 W no-load at 350 V (TIDT266 p.4) | Calculated at 30 W only: 83.4 % (1000 V), 86.3 % (400 V) | **ADOPT** | `sim/out/aux_hv_design` §9: cite these as a measured plausibility check and add the no-load and 13 W standby input power (night drain of a port-B battery, D-021/D-022) |
| 4 | RCD clamp: 2.2 nF/1500 V film, 2 × 220 k, 2 × RS1MB, 10 Ω (TIDMAD8 p.1) | TVS clamp, 1.66 W | N/A | An RCD clamp level floats with load (TI's node reaches 1.51 kV); a single 1700 V switch needs a fixed clamp level |
| 5 | 2.2 nF / 250 V Y capacitor primary-to-secondary (TIDMAD8 p.1; TIDMAD9 p.1) | No Y capacitor; shield tied to PGND | CONSIDER | EMC pre-compliance decides; if needed, a DNP Y1 position rated for reinforced 1000 V DC — it adds array common-mode current into PELV |

### PMP41031 — 350–1500 V input, 150 W auxiliary supply, two-switch flyback (TIDT355, Oct 2023)
**What it is:** two-switch quasi-resonant flyback, 2 × STW12N170K5 (1700 V Si), UCC28740 + TL431/PC817 feedback, Würth
750345142 ERL35 transformer (L_m 800 µH, Np:Na:Ns 72:4:6:4:4:2), high-side drive through a gate transformer (760301302),
outputs 24 V 3 A / 15 V 3 A / −15 V 0.8 A / 8 V 2 A, 70 kHz at full load; 88.02 % at 1000 V and 86.17 % at 1500 V full
load, 0.78 W no-load input at 1000 V, all parts ≤ 62 °C at 200 LFM (TIDT355 p.2, p.4–5; TIDMBZ3 p.1; TIDMBZ4 p.1).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | **150 W** auxiliary supply for PCS / string inverter / DC charger from 350–1500 V in one stage (TIDT355 p.1–2) | AUX-HV 30 W bootstrap: boots, communicates, shuts down; cannot hold 2 contactors (≈ 12 W) + fans; self-powered operation needs ≈ 80 W (D-022, open) | CONSIDER (D-022) | Evidence that the self-powered option is one stage of ordinary size (176 × 78 mm open frame, 88 % at 1000 V). Its start-up is 350 V, ours must stay ≤ 250 V (PV-C2): a resized AUX-HV keeps our 229 V start and borrows the topology, not the design |
| 2 | Two-switch flyback: each switch blocks only V_in, leakage energy returns to the input, no RCD clamp loss (TIDT355 p.1) | Single 1700 V SiC switch: V_DS 1489 V at the 1114–1170 V OV stop = 87.6 % of rating (aux report §0, §3) | CONSIDER (with D-022) | If AUX-HV is resized for self-powered operation, a two-switch stage puts ≤ 1170 V (69 %) on each 1700 V device and removes the clamp loss; cost: second HV switch, high-side gate transformer, two clamp diodes rated ≥ 1.2 kV. For the 30 W bootstrap as frozen, keep the single switch |

### TIDA-010955 — AFE for machine-learning DC arc detection in solar (TIDUF85 Rev A, Dec 2024)
**What it is:** 4 channels: feed-through CT (Triad CST206-3A, 300 Ω burden = 1 V/A), optional notch (470 µH / 220 nF ≈ 15.7
kHz), ×10 gain, 4th-order 30 kHz high-pass + 4th-order 100 kHz low-pass, 12-bit C2000 ADC at 250 kS/s; AI model on the
F28P55x NPU. By TI's own statement the AFE alone does not meet UL 1699B; no detection time or real-arc results are
published (TIDUF85 p.1–7, p.14, p.22).

| # | TI does (doc p.) | We do | Verdict | Where / trade-off |
|---|---|---|---|---|
| 1 | UL 1699B requires arc-fault protection in PV systems below 1500 V (TIDUF85 p.2); tested with an arc generator and a line-impedance network (p.22); IEC 63027 not cited | No AFCI; not in the roadmap, PV-C3 or ECO-11 | CONSIDER (owner) | Add a candidate PV-C7 "DC arc-fault detection" only for markets that need it (NEC 690.11 / UL 1699B; IEC 63027); action on detection: stop switching, open port A |
| 2 | Arc signatures are a few mA to 200 mA on ≈ 20 A DC with a spectrum up to MHz (TIDUF85 p.2, p.7); the CT blocks DC, works to 16 A DC, ≈ −3 dB in band at 20 A (p.5, p.18) | PV-port current: 100 µΩ shunt + AMC3302 — ≈ 83 mA rms noise in 100 kHz, LSB ≈ 0.31 A (AMC3302 p.8; our calc) | ADOPT if AFCI is adopted | The shunt channel cannot resolve 0.1–0.2 A: an AFCI needs a separate AC-coupled sensor that tolerates 135/180 A DC bias, or per-string CTs; a 16 A CT saturates on our port |
| 3 | 30–100 kHz detection band; the filter must reject the converter's switching frequency (notch) and PLC; op-amp GBW ≥ 100·G·f_c; 250 kS/s (TIDUF85 p.2, p.5, p.7) | Cells at 32 kHz; port ripple at 96 kHz (3 cells) / 128 kHz (4 cells) | ADOPT if AFCI is adopted | Place the band and notches clear of n × 32 kHz; reserve one CTRL-C2000 ADC channel at ≥ 250 kS/s (ECO-02) |
| 4 | ML model trained on labelled lab arcs, run on the F28P55x NPU (TIDUF85 p.5, p.14) | F28388D, no NPU | CONSIDER | Run a classifier on the C28x/CLA or use a spectral detector; an arc lab and the UL 1699B test are outside this project's scope |

## 3. What the TI designs say about a 3-level PV cell with lower-voltage devices

Context: D-007 chose a 2-level four-switch buck-boost with 1700 V SiC; D-031 re-opens it (2-level 1700 V vs 3-level with
1200 V or 650–750 V devices). TI shows no 3-level **DC/DC buck-boost**; its multilevel evidence is DC/AC (TIDA-010957,
TIDA-010210) and one stacked DCX (PMP41037).

**For a 3-level cell**
- Ripple at the inductor is 4× lower than 2-level at the same device frequency (double frequency, half voltage;
  SDAA195 Fig. 3-4 p.6) — consistent with our Gate-0 result (candidate B: one 35 µH, 0.8 kg inductor vs 139–224 µH,
  1.6–1.8 kg for 2-level).
- It works at power: 15 kVA GaN flying-capacitor stage measured 98.4–98.8 % at 700–900 V (TIDUFG9 p.14–18).
- Flying-capacitor pre-charge is solved with cheap passive parts (Zener string + a few ohms across the outer
  devices, SDAA195 §3.4), i.e. the "FC precharge" cost item in our study is small in BOM terms.

**Against lower-voltage (650–750 V) devices on our 1000 V ports**
- Every TI multilevel/stacked design keeps the per-device DC stress at or below ≈ 0.69 × rating: 650 V GaN at ≤ 900 V
  (TIDUFG9 Table 1-1 p.3), 600 V GaN at 800 V (TIDUEZ0 p.8), 2 × 650 V in series at ≤ 900 V (TIDT332 p.1). A 3-level
  cell on our ports (1000 V continuous, 1100 V OVP) puts 500/550 V on each device: **650 V parts → 0.77/0.85 (outside
  TI's practice and our 0.67 rule); 750 V parts → 0.67/0.73 (admissible only with vendor FIT-vs-V data at 500–550 V);
  1200 V parts → 0.42/0.46**. The "650–750 V" variant therefore reduces to 750 V-with-data or 1200 V (candidate B).
- TI's start-up evidence does not cover our case: FC pre-charge is shown from an AC source with a 520 V Zener clamp
  (SDAA195 §4.1) and stacked start-up only with a < 1 V/ms source ramp (TIDT332 p.6). Our ports pre-charge at 17–34 V/ms
  from either side, possibly before the controller runs (AUX-HV starts at 229 V). The passive network has to hold the
  outer devices inside the voltage rule on its own — not yet designed or simulated for candidate B.
- The extra hardware TI needed is all present in our B costing and more: an isolated amplifier per flying capacitor
  (AMC0311D), an FC voltage loop, complementary-pair interlocks plus hardware state protection (CLB, TIDUEZ0 p.2), two
  commutation loops per leg (SDAA195 §3.3). TI's GaN parts carry driver, OC and temperature on-chip; with discrete SiC
  each extra position costs an isolated driver and bias module (8 instead of 4 per cell).
- TI's own non-isolated DC/DC practice at these voltages is 2-level (TIDA-010938 boost and interleaved half-bridges;
  SLLA498 p.3: boost with up to 1200 V devices for three-phase buses).

**Net:** the TI material does not overturn the Gate-0 ranking; it narrows the 3-level option to **1200 V devices**
(candidate B) and adds four conditions for it (pre-charge/clamp network proven at ≥ 35 V/ms ramps in both directions,
direct FC sensing, hardware state interlocks in the CLB, two-loop transient check). Whether B wins is now a cost
question for the D-031 re-run (Asian 1200 V SiC versus Asian 1700/2000 V SiC prices), not a technical one.

## 4. Gaps in our design surfaced by the TI designs

Most important first. "if B/C" = applies only if the D-031 re-run picks the 3-level (B) or stacked (C) PV cell.

| # | Gap | Block | Verdict | Action / where | Source |
|---|---|---|---|---|---|
| 1 | An open IMD PE connection reads as perfect insulation (latent dangerous failure) | PV-PORT, CTRL fw | **ADOPT** | Separate PE pins for the divider and test-string ends; firmware flags "no swing between states / AUX ≈ 0 V" as IMD-PE-open | TIDUEZ8 p.7, p.20–21 |
| 2 | 3-level PV option: 650 V devices are outside TI's own practice on a 1000 V port; 750 V only with FIT data; and the FC/stacked stage must hold every device inside the voltage rule passively during 17–34 V/ms pre-charge ramps from either port with the controller off | PVCELL (D-031 re-run) | CONSIDER / **ADOPT if B/C** | Gate-0 re-run: drop 650 V parts; for B add the Zener+R clamp/pre-charge network across S1/S4 of both FC legs, direct FC sensing, CLB state interlocks and a two-loop DPT check; for C add passive sharing (bleeder + clamp) | TIDUFG9 p.3; SDAA195 §3.3–3.4, §4.1; TIDUEZ0 p.2; TIDT332 p.6 |
| 3 | Several IMDs on one galvanically connected bus see each other's test strings as faults | System spec, SYS-IO-AUX | **ADOPT** | Measure only with port B open, or one active IMD per connected system and inhibit the rest | TIDUFG5 p.3 |
| 4 | IMD path vs the production hipot: dividers and strings permanently on PE | PV-PORT (ECO-10) | **ADOPT** | IMD PE return on a link the hipot opens, or prove C2M1000170D avalanche at the hipot level | SLVRBZ5 p.3 |
| 5 | IMD settling with a large array: 10 µF assumed, 200 nF/kWp worst case (16.5 µF PV-P75, 22 µF PV-P110) → ≈ 43 s per result by settle-and-wait; no response-time target | `sim/port_design.py` A_C_PV_PE, CTRL fw | **ADOPT** | Worst-case 200 nF/kWp; 3-sample exponential prediction + C_iso output; ≤ 10 s per branch; owner sets PV-C5 accuracy/time (IEC 62109-2 check, ≤ 15 %, ≤ 10 s) | TIDUF64 p.9; TIDUFG5 p.8–12; TIDUEZ8 p.4 |
| 6 | Bias-module output overshoots +0.5 V at enable/VIN ramp: DAB rail reaches 15.95 V, past the BZX84-B16 clamp knee | GDRV-HB design check | **ADOPT** | Add the +0.5 V start-up term to `gen/gdrv.py` design check; state that UVLO/RDY hold the gate low until PG | TIDT256 p.19 |
| 7 | AIN/APWM temperature path measured 8–10 duty points (≈ 20 °C) off the datasheet line by TI itself; our ±3 °C claim backs the DAB module NTC trip (D-017) | GDRV-HB, DAB bench plan | **ADOPT** | Two-point AIN→APWM check per channel before the NTC trip is trusted; no ±3 °C claim until then | TIDT283 p.8–9 |
| 8 | Miller hold of the paralleled PV gates has 0.06 V margin (D-028); TI clamps with an N-FET from a CLMPE output | GDRV-HB (PV) | CONSIDER | Simulate an N-FET clamp per device in the PV DPT deck; make a CLMPE-type output a D-031 driver-selection criterion, else close D-028 on the bench | TIDMAI7 p.2; SLVRC10 p.2 |
| 9 | −4 V off-rail costs E_off ×1.225; +19.9/−4.5 V or +18/−5 V fit under the bias module's 25 V | GDRV-HB, `sim/pv_design.py` | CONSIDER | Re-run the PV loss model for both rails before −4 V is frozen | TIDMAI7 p.2 (method); our `rails()` model |
| 10 | No reference on file demonstrates DAB-04: TI's DAB measures 98.0 % at 10 kW (Wolfspeed 97.0–97.6 %) | DAB-D60 (D-015/D-017) | **ADOPT** (evidence) | Keep DAB-04 "not claimed" and the pessimistic derating map as the guaranteed envelope | TIDUES0 p.4 |
| 11 | AUX-HV: no-load and standby input power not calculated; self-powered option (D-022) and switch stress (1489 V = 87.6 % at the OV stop) | AUX-HV (D-021/D-022) | **ADOPT** (calc) / CONSIDER | Add no-load and 13 W standby input to the aux report (TI measured 1.3 W no-load at 1000 V); if D-022 goes self-powered, a two-switch flyback (≤ 69 % stress, 150 W proven) keeping our 229 V start | TIDT266 p.4; TIDT355 p.1–4 |
| 12 | No DC arc-fault detection; our PV-port shunt channel cannot resolve 0.1–0.2 A arc noise | PV-PORT, CTRL (owner) | CONSIDER | Owner: candidate PV-C7 AFCI for markets that need it; if adopted, an AC-coupled sensor tolerant of 135/180 A DC, 30–100 kHz band clear of n × 32 kHz, ADC ≥ 250 kS/s | TIDUF85 p.2–7, p.18 |
| 13 | PVCELL i_L sensor (LA 150-P) has no published dv/dt figure and 1.1 A offset (D-030) | PVCELL | CONSIDER | In-package Hall (TMCS1123-class: 1.3 kV reinforced, CMTI 150 kV/µs, 100 ns OC flag) — needs the backup trip ≤ 88 A and 47.5 A rms through PCB copper; Asian equivalent under D-031 | SLURB91 p.10; TMCS1123 DS p.1, p.6, p.8 |
| 14 | DAB gate: R_G,off 0.27 Ω set by the UCC21710 10 A sink costs E_off +27 % | GDRV-HB (DAB) | CONSIDER | P/N-FET buffer after a 2.8–5 A pre-driver allows R_G,off → 0 Ω and widens the Asian driver choice | SLVRC10 p.2 |
| 15 | Bias module: UCC15241-Q1 gives +25 % power (2.5 W at 85 °C) but the same 25 V ceiling and only planned certification | GDRV-HB (D-016) | CONSIDER | Only needed for the 3 × C3M0016120K DAB option (113 °C at 1.66 W otherwise); D-016 stays open — ask TI for certificate status | ti.com UCC15241-Q1; TIDT256 p.14 |
| 16 | DAB transformer RFQ asks only for a wound PM 114/93 + separate inductor | DAB-D60 (MAG-2) | CONSIDER | Ask for a planar variant with integrated series L as well (repeatable leakage); weigh the 1850 V DC PD-free barrier | TIDUES0 p.23, p.26 |
| 17 | Isolated CAN/RS-485 built from transceiver + SN6505 + transformer per port | SYS-IO-AUX | CONSIDER | Integrated-power isolated transceivers (ISOW1044/ISOW1412 or Asian equivalents) — cost check | TIDUF64 p.28 |
| 18 | FSBB mode logic (buck/band/boost with hysteresis) | CTRL firmware | CONSIDER | Keep; stacked-carrier single-modulator scheme is the fallback if the bench shows mode chatter | TIDUF99 p.12–13 |
| 19 | IMD topology and thresholds: balanced bridge (≤ 12.5 % pole-PE swing, 1200 V SSR) and "> 1 MΩ" reporting would remove the EOL baseline | PV-PORT | CONSIDER | Owner sets the PV-C5 warning level first | TIDUFG5 p.3–4; TIDUEZ8 p.27–28 |
| 20 | AUX-HV switch: Si cascode as a D-031 cost alternative to one 1700 V SiC | AUX-HV | CONSIDER | Only with the split re-proven at 1100 V + surge and ≤ 80 % per device | TIDT266 p.8 |
| 21 | Low: Chinese magnetics maker (YAXIN) in a TI HV design; PTC pre-charge element | MAG-2, port | CONSIDER (low) | RFQ candidate; no ≥ 1000 V DC PTC identified, keep the resistor pre-charge | TIDMCY2 p.1–2; SLURB91 p.6 |

## 5. Not obtained, and designs looked at but not reviewed
- TIDA-010938 software guide (`tida_010938_sw.pdf`, control loops and protection firmware): ships only inside the
  C2000Ware DigitalPower SDK; the public SDK URL returns 404. The design guide has no loop bandwidths, so no TI loop
  numbers could be compared with `sim/out/pv_control` (2.5 kHz current loop, 300 Hz outer loop).
- TI's announced application note "Design Consideration of 3-Level Flying Capacitor Converters" is SDAA195 (filed); no
  TI document gives the FC voltage-loop design numerically.
- No TI reference design of a three-level or flying-capacitor **DC/DC** (buck, boost or buck-boost) at ≥ 5 kW was
  found (ti.com tool search, C2000 DigitalPower SDK solution list, web search 2026-10-04).
- Gerber, layout, CAD and PLECS files of every design: not fetched (PCB layout out of scope).
- Looked at, not reviewed (no lesson for our blocks): TIDM-02002 (6.6 kW CLLLC at 500 kHz, 2019–2022), TIDM-02013
  (7.4 kW GaN OBC), PMP41042 (3.6 kW CLLLC), TIDA-010933 (1.6 kW microinverter), TIDA-010954 (600 W cycloconverter),
  TIDA-010231 (older arc detection, superseded by TIDA-010955), TIDUF88 (BMS insulation monitor on BQ79731),
  TIDA-010966 (300 W series-resonant DAB for pack balancing).

## 6. Wolfspeed firmware packages (2026-10-05)

**Requirement:** REQUIREMENTS.md DAB-08 (our DC/DC controls are cross-checked against references), AC-01 / AC-02 (PCS-P125
designed to the same depth), SRC-7 (hardware / firmware balance). **Owner (2026-10-05):** "take reference from below
firmwares" and "remember we are using IGBTs or SiCs.. they are just for reference... and edge cases". **Status:** three
packages read statically on 2026-10-05 - nothing built, flashed or run; the full extraction, with file:line evidence for
every number, is in `docs/reference-designs/wolfspeed/<design>/FIRMWARE-NOTES.md`. Wolfspeed's device-specific values
(dead times, DESAT tuning, gate timing, current limits tied to their modules) are reference points only and are not
carried into our design. Our side is calculated / simulated, not bench-validated. Verdicts as in §1-5; **every
recommendation below is a recommendation only - adoption is the orchestrator's decision.**
Citation prefixes: `GMA:` = `docs/reference-designs/wolfspeed/crd200da23n-gma/firmware/CRD200DA23N-GMA Firmware [v1.0.0]/`,
`GMB:` = `docs/reference-designs/wolfspeed/crd60dd12n-gmb/firmware/CRD60DD12N-GMB Firmware [V1.1.0]/`,
`K:` = `docs/reference-designs/wolfspeed/crd60dd12n-k/firmware/Wolfspeed_CRD60DD12N-K_Firmware_GUI/CRD60DD12N-K_DCDC_F28377D_V1.00/`.

### 6.1 The three packages

| package | hardware | controller | what the firmware does | protection in firmware | interface |
|---|---|---|---|---|---|
| CRD200DA23N-GMA v1.0.0 (2025-10-27) | 200 kW two-level three-phase inverter, 2300 V SiC modules, 1500 V bus | F28379D, 200 MHz | **open-loop** SPWM sine generator: no current loop, PLL, DC-link loop or grid forming (GMA:main.c:8-15) | none beyond the drivers' DESAT → controller trip zone | 2 x CAN 1 Mbit/s, 1 s cycle, Python GUI |
| CRD60DD12N-GMB v1.1.0 (2026-03-06) | 60 kW DAB, two CBB011M12GM4T full-bridge modules | F280039C, 120 MHz | **open-loop** single phase shift typed into the GUI (GMB:CRD60DD12N-GMB_main.c:11-18) | drivers' DESAT; trip zone for 3 of the 8 driver fault lines | CAN 1 Mbit/s, 1 s cycle, Python GUI |
| CRD-60DD12N-K v1.00 (2022-02-08, linked 2023-03-13) | **60 kW three-phase interleaved LLC, unidirectional - not a DAB** (UG PRD-07229 p1, p8) | F28377D, 200 MHz | closed loop CV / CC / CP, variable frequency + phase shift, topology switching, soft start | tank OCP, output short, output OV (hardware + firmware + lock-out), input OV / UV, ambient OT, offsets, calibration | CAN 125 kbit/s J1939-style, C# GUI |

The GMA and GMB are starting points ("designed as a starting point only"); only the -K has real control and protection.
The edge cases therefore come mainly from the -K and from what the GMA and GMB leave out.

### 6.2 Comparison

**Inverter: CRD200DA23N-GMA against PCS-P125** (ours: `sim/out/pcs_control/pcs_control_spec.json`, report §9,
`hardware/PCS-CTL/outputs/PCS-CTL_design_check.txt` lines 55-77)

| item | Wolfspeed reference (read) | ours (calculated / specified) | assessment |
|---|---|---|---|
| timing | one ePWM1 counter-zero ISR per carrier (20 kHz default) that only advances three sine references; everything else in a 1 s background loop (GMA:main.c:276, :403-421, :575-577) | 64 kHz control ISR (double update at 32 kHz), slow tasks 1 kHz; 3.9-4.8 µs = 25-31 % of the period at 120 MHz (firmware.isr, cost model ASSUMED) | N/A - an open-loop generator needs no budget |
| sampling | ADC forced by software once per second, telemetry only (GMA:main.c:319-348) | SOC at carrier zero and peak + 2.46 µs chain delay, simultaneous rounds, VA / VB 4 x per period (firmware.sampling) | WE HAVE |
| modulation | sine-triangle SPWM, no zero sequence, MF 0-1.023 not clamped; V_LL,rms ≤ 0.612 MF V_dc (GMA:main.c:813, :937; calculated) | min-max zero sequence, \|u\| ≤ 0.5495 V_dc (V_LL,rms ≤ 0.673 V_dc, calculated), clamping anti-windup, refusal below the operating map | WE HAVE (+10 % AC voltage per DC volt) |
| carriers | phases B / C shifted by TBPHS 2/3 and 4/3 TBPRD (GMA:main.c:602, :702) | not stated in pcs_control_spec | CONSIDER (low): state the carrier alignment in the PWM specification - the min-max modulator assumes one common carrier |
| dead time | ePWM dead band, 400 ns default, any value incl. 0 accepted from the bus (GMA:main.c:188, :920, :938) | 300 ns firmware floor, hardware stretch 185-510 ns at the gates, per-leg compensation (check line 65) | WE HAVE (hardware floor); value N/A (device) |
| current loop, PLL, DC link, grid forming | none | αβ PR 1750 Hz + h5 / h7; SRF-PLL 30 Hz, ±5 Hz clamp; DC-link PI 40 Hz + DC-current feed-forward; droop + virtual impedance + voltage PR | WE HAVE - the reference has no numbers to compare |
| over-current | DESAT with soft turn-off on each driver; the three phase faults ANDed → TZ1 one-shot on all six PWM, no software in the path (GMA:GATEDRIVER.c:73-77; main.c:556-568; UG p26) | phase window ±454 A (422-490 A, 2.6 µs) in hardware, CMPSS backup 492-572 A, DESAT per channel, latch | SAME PRINCIPLE (SRC-7); ours has more layers |
| DC OV / UV, AC U / f, islanding, OT | none (values sent once per second) | DC OV 1000-1051 V hardware + PPB backup 1029-1061 V in 33.9 µs; U / f windows, ROCOF, SFS; heatsink and inductor OT with derating | WE HAVE |
| fault-line integrity | active-low fault inputs with internal pull-ups behind RS-422 receivers (fail-safe high, inferred); the driver's RESET / EN is pulled up "to enable" (GMA:GATEDRIVER.c:61-76; UG p19) | 10 kΩ pull-downs on every MCU line into the gating; an unpowered or missing board reads as a trip (check line 38; DR-04) | WE HAVE |
| exceptions, watchdog | watchdog disabled; TI default handlers `ESTOP0; for(;;)` keep the PWM running (GMA:device/device.c:62-64; device/driverlib/interrupt.h:138-223) | watchdog ≤ 10 ms after the ISR ran; heartbeat monoflop 4.7-5.8 ms → latch; clock loss → NMI → one-shot < 6 ms (lines 63, 66-67) | WE HAVE in hardware; R-WS-6 removes the ≤ 5.8 ms window at no cost |
| fault clear | CAN reset pulses the drivers and clears the trip zone; the same frame can re-enable (GMA:main.c:951-974) | clear only with every source inactive, cause logged, PWM commands low; a start event is needed (line 64) | WE HAVE |
| command validation | only f_fund ≤ 500 Hz; f_sw 0 divides by zero, > 65 kHz overflows; ranges enforced by the PC tool only (GMA:main.c:914-946; UG p38) | not specified | GAP → R-WS-1 |
| communication loss | none; last command kept, read once per second (GMA:main.c:276-292) | not specified | GAP in both → R-WS-2 |
| service interface | 1 Hz temperatures, currents, voltages; status echo (GMA:main.c:355-396, :882-902) | not defined (ARCHITECTURE-PCS.md:211, :290: CAN + RS-485, "CAN updates") | CONSIDER → R-WS-12 |
| precharge, contactors, synchronisation | none on the board | DC precharge to ≤ 10 V within 1.0 s; close at \|dV\| ≤ 5 %, \|dθ\| ≤ 5°, \|df\| ≤ 0.1 Hz after a relay test; dead-bus start in GFM (state_machine) | WE HAVE |
| bench method | open loop into a wye inductor with its neutral on the DC midpoint: rated current from a supply that covers only the losses (UG p43-45) | none | CONSIDER → R-WS-12 (factory test mode) |

**DC/DC: CRD60DD12N-GMB and CRD-60DD12N-K against DAB-D60** (ours: `sim/out/dab_design/dab_spec.json`
firmware_requirements FW-DAB-1..10, protection, flux_balance; `sim/out/dab_control/report.md`;
`hardware/DAB60/outputs/DAB60_design_check.txt`)

| item | Wolfspeed reference (read) | ours (calculated / specified) | assessment |
|---|---|---|---|
| timing | GMB: no control ISR, 1 s background; -K: 100 kHz timer ISR not synchronised to the 120-250 kHz PWM, ADC once per switching period (K:source/PWM_Isr.c:9-17; InitPeripherals.c:277-337) | control update every switching period (100 kHz), one-period delay | WE HAVE (synchronous) |
| modulation | GMB: SPS, 50 % legs, manual phase; -K: LLC frequency control, phase shift in the 2-phase full-bridge mode | SPS only where \|V1 − n V2\| ≤ 130 V, TPS look-up table elsewhere, feasibility built in (FW-DAB-1, -4) | DIFFERENT; nothing to adopt for the law |
| phase units and limit | GMB GUI ±90 "deg" = **±180° of the period** (TBPHS = TBPRD φ / 90 in up-down count; GMB:wsinclude/wsepwm.c:105-140) - past the 90° maximum-power point | clamp ±60° of the period (dab_control) | WE HAVE; R-WS-1 puts the service interface under the same clamp |
| phase update | GMB: register writes from the 1 s loop, no slew, no split update, a sign change in two separate writes (wsepwm.c:93-141) | split (volt-second balanced) update + 3° per period slew | WE HAVE |
| start, light load, zero power | GMB: switches at the set phase (default 0°) the moment Logic Enable arrives; -K: on-time-then-frequency soft start with a 480 ms voltage walk-in, dummy load below 2.5 A, on-time floor | gates off below 1.5 kW, back at ≥ 2.5 kW, no zero-phase SPS with mismatch, restart from zero phase with the split update (FW-DAB-3/4/5) | WE HAVE |
| loops | GMB: none; -K: voltage and current incremental PI in parallel, the lower output wins (CV / CC), CP as a current limit; zeros ≈ 54 Hz / 2.1 kHz (calculated); integral boost above 5 V of error; integrators clamped [0, permit] (K:source/PWM_Isr.c:150-198) | CV 400 Hz → per-branch current loop 2 kHz with SPS feed-forward; conditional integration while the CC limit acts | SAME STRUCTURE (min-select vs cascade); nothing to adopt |
| power reversal | GMB: type a negative phase (secondary legs inverted); -K: unidirectional | through the idle band (FW-DAB-5) | WE HAVE |
| flux balance | GMB: none, the transformer current is not even converted (GMB:CRD60DD12N-GMB_main.c:124-126); -K: N/A (resonant capacitor) | flux loop on the averaged transformer current + 5 A / 1 ms DC trip (FW-DAB-7) - **no offset-null rule** (§6.6 C-4) | GAP in ours → R-WS-5 |
| dead time | GMB 200 ns commanded = 166.7 ns programmed (calculated); -K 150 ns, 250 ns in the full-bridge mode for gate ringing, 137 / 153 ns measured (UG p59) | hardware guard 69-188 ns + table 50-300 ns (ZVS transition + 20 ns) | values N/A; per-mode tables endorsed → R-WS-10 |
| over-current | GMB: DESAT per driver, all-PWM one-shot for only 3 of 8 fault lines (§6.6 C-2); -K: tank CT → **positive-side** CMPSS → one-shot on all legs, latched (K:source/InitPeripherals.c:151-200) | ±200 A window (190-210 A) on the transformer current → soft turn-off of all 8 drivers by DESAT-pin injection, 1.47 µs; port OC 115 A | WE HAVE (hardware, both signs, all drivers); R-WS-7 for routing checks |
| output short | -K: output < 100 / 300 V for 20 µs running; < 10 / 20 V at 25 % of the walk-in (K:source/PWM_Isr.c:74-101; DCDC_start.C:405-432) | port OC, DESAT, precharge supervision (a shorted port cannot precharge) | N/A (current layers cover it) |
| over-voltage | GMB: none; -K: output hardware comparator ≈ 0.13 ms, auto-clears after ≈ 1.5 s; firmware 1050 / 550 V after ≈ 0.3 s; lock-out when both agree; input 890 V after 0.5 s | port 1 / 2 hardware 37.5 µs (DAB60), firmware 960 / 920 V cycle by cycle | WE HAVE (faster); lock-out → R-WS-3 |
| UV, OT recovery | -K: UV 600 V (1 s) recovers above 640 V after 3.25 s; OT 79 °C recovers below 70 °C; bus OV recovers below 875 V (K:source/DCDC_Warning.C:46-109) | UV 580 / 380 V; plate OT 77 °C hardware, derating from 67 °C; recovery thresholds and dwell not stated | GAP → R-WS-4 |
| sensor offsets | GMB: DC current sensors nulled at boot, no plausibility window; -K: current **and voltage** channels nulled 1 s after power-up, no start until done (K:source/DCDC_Adc.c:104-132) | PV: re-zero at idle with a limit (guide 05 line 227); PCS: CMPSS DACs from the idle zero; DAB: none | GAP (DAB) → R-WS-5 |
| calibration storage | -K: three copies, 2-of-3 vote with repair, range checks, defaults + status bit, start not inhibited (K:source/DCDC_E2PROM.C, DCDC_E2PROMdataDriver.C) | PV: two-point end-of-line calibration; storage integrity not specified | GAP → R-WS-9 |
| parallel branches | none in either | 1-4 (+1) branches, CAN-B reference, per-branch loops, survivor clamp | WE HAVE |
| communication | GMB: 1 Mbit/s, 1 s cycle; -K: 125 kbit/s, 0.5 / 3 s telemetry, CAN error counters reported, no stop on loss (K:source/DCDC_Warning.C:150-166) | CAN-B reference every 10 ms (dab_control t_can); loss not specified | GAP → R-WS-2 |
| watchdog | GMB: disabled; -K: ≈ 0.84 s, serviced by the background loop only (calculated) | PCS-CTL / PV: ≤ 10 ms, serviced only after the ISR ran; not stated for the DAB controller | CONSIDER → R-WS-6 |
| service modes | -K: a CAN command enters a test mode that skips the protection gating and the short check; raw memory read / write over CAN (K:source/DCDC_CANCOM.c:248-376) | PCS SERVICE = all off, latch held; DAB not stated | negative lesson → R-WS-8 |

### 6.3 Edge cases the references handle that ours does not mention

| # | edge case | reference (evidence) | ours today | recommendation |
|---|---|---|---|---|
| E-1 | repeated restarts after hazard trips | -K clears SHORT and OCP on any OFF frame, so a master can retry into a short without limit (K:source/DCDC_start.C:41-53); but locks out for good when the hardware and firmware OV agree (DCDC_Warning.C:144-147) | contactor / precharge: 3 attempts, then lock-out (PCS-CTL line 77; port_spec); converter trips: no count | R-WS-3 |
| E-2 | recovery of non-latched limits | -K: hysteresis and dwell on every class (bus OV 890 / 875 V; UV 600 / 640 V, 1 s / 3.25 s; OT 79 / 70 °C) | trips defined, recovery not | R-WS-4 |
| E-3 | start before the sensor zeros are known | -K: no start until the offsets are measured (DCDC_start.C:56); GMB: boot-time null of the DC current sensors (GMB:wsinclude/wssensor.c:614-629) | PV / PCS current channels re-zeroed at idle; DAB transformer and branch currents not | R-WS-5 |
| E-4 | corrupted or missing calibration | -K: 3 copies, vote, repair, range check, status bit (K:source/DCDC_E2PROMdataDriver.C:181-300) | not specified | R-WS-9 |
| E-5 | CAN error state | -K: REC or TEC > 10 reported in the status word (K:source/DCDC_CANCOM.c:604-607) | not specified | part of R-WS-2 |
| E-6 | debugger halt and clock failure | GMB: emulation-stop and clock-fail one-shot sources on every ePWM (GMB:CPU1_FLASH/syscfg/board.c:812; TZ4-6 meaning inferred) | clock loss via NMI < 6 ms; heartbeat 5.8 ms | R-WS-6 |
| E-7 | dead time per operating mode | -K: +100 ns in the full-bridge mode "due to the Vgs oscillation issue" (K:source/DCDC_start.C:487-488) | DAB: table per point; PCS: one band | R-WS-10 |
| E-8 | module NTC through the driver's APWM | GMA: eCAP high time over 10 periods at 400 kHz, polynomial (GMA:TEMPERATURE.c:82-100, :175-211) | DAB60 rev B0 reads the module NTC through AIN / APWM, method not stated | R-WS-11 |
| E-9 | operator-facing validation | GMA / GMB GUIs refuse or clamp out-of-range inputs with a message | no service tool defined | R-WS-1, R-WS-12 |
| E-10 | configuration changed only while off; topology change by stop and restart | -K (K:source/DCDC_CANCOM.c:124-140; DCDC_start.C:184-192) | PCS GFL ↔ GFM bumpless by design; build code from EEPROM | N/A - equivalent |
| E-11 | operating limits that follow the input | -K: frequency ceiling from the bus voltage, set-point following the input ratio (DCDC_start.C:118-163; DCDC_cal.C:159-212) | DAB feasibility map; PCS operating-map refusal | N/A - equivalent |
| E-12 | output short seen as missing voltage at start | -K: < 10 / 20 V at 25 % of the walk-in | port OC + DESAT + precharge supervision | N/A |
| E-13 | light-load dummy load | -K: switched below 2.5 A (DCDC_start.C:515-522) | DAB idles with the gates off | N/A (LLC gain at no load) |

### 6.4 Edge cases ours handles that the references do not

Precharge supervision with attempts, lock-out and weld check; contactor sequencing with zero-current opening; AC
synchronisation permissives, relay test and dead-bus start; U / f windows, ROCOF and anti-islanding; current-limit tiers
with timers; DC-link coordination between DAB and PCS (FW-DAB-8..10) including the coordinated trip; flux balance with a
DC trip; light-load idle and the ban on zero-phase SPS with mismatch; look-up-table CRC and per-entry checks at start
(FW-DAB-2); split phase update and slew; parallel-branch sharing and the survivor clamp; hardware latch with heartbeat,
configuration read-back, latch-clear rules, brown-out and clock-loss handling; fail-safe fault lines (an unpowered or
missing board reads as a trip); NTC plausibility at a cold start; insulation check before start; V_dc-below-rectification
handling and the upper DC-link-half monitor (PCS); refusal outside the operating map. None of the three packages has any
of these.

### 6.5 Edge cases neither handles

* **Communication loss** - all three keep running on the last command (GMA:main.c:276-292; GMB:CRD60DD12N-GMB_main.c:85-94;
  K:source/DCDC_Warning.C:150-166, where the OFF action is commented out); ours is silent → R-WS-2.
* **Exception paths with the PWM running** - TI's default handlers spin with the ePWM active (GMA / GMB driverlib
  `interrupt.h` / `interrupt.c`); ours relies on the 5.8 ms heartbeat → R-WS-6.
* **Verification of controller-routed trip paths** - the GMB shows the failure (C-2); ours reads back CMPSS, PPB, TZ and
  dead band but not the X-BAR / digital-compare routing → R-WS-7.
* **Grid reconnection after a grid-caused stop** (PCS) - observation time and power gradient before reconnecting; not in
  the references (GMA is open loop) and not in pcs_control_spec → R-WS-14 (values from memory, standards not on file).

### 6.6 Contradictions and discrepancies found

* **C-1 The -K is not a DAB.** The brief for this review and the SOURCES.csv row of `crd60dd12n-k_firmware-gui.zip`
  call it "60 kW isolated DC/DC (DAB, discrete TO-247 devices)"; the row's URL `.../reference-designs/crd60dd12n-k/`
  returns 404. Evidence: UG PRD-07229 p1 ("60kW three-phase interleaved LLC DC/DC converter"), p8 (C6D20065D Schottky
  rectifiers, unidirectional), p25; firmware: period control of three interleaved half bridges
  (K:INCLUDE/OBC_constant.h:42-65; K:source/PWM_Isr.c:238-265). Correct product page:
  `https://www.wolfspeed.com/products/power/reference-designs/crd-60dd12n-k/`. The SOURCES.csv row was not edited here.
* **C-2 CRD60DD12N-GMB user guide vs its firmware.** UG p39: "The default firmware will quickly disable all the other
  gate drivers using the C2000 trip zone hardware functionality." The SysConfig routes only faults 1P-3P to latched
  one-shot trips; 4P acts (unlatched) on one ePWM output; 1S and 2S go to X-BAR trips no ePWM selects; 3S and 4S go
  nowhere (GMB:CRD60DD12N_GMB.syscfg:213-318, :435-464; CPU1_FLASH/syscfg/board.c:810-960). A DESAT on those five
  switches stops only the faulted channel. Confidence: high for the settings, medium for the consequence (TRM
  conventions not on file).
* **C-3 Our CRD60DD12N-GMB test point** (`sim/dab_devices.py` CRD['test']): (a) the comment calling UG Table 11's
  2.5-5.0° "inconsistent" with Fig. 55 has its cause in the firmware - one GUI degree is two degrees of the period
  (GMB:wsinclude/wsepwm.c:110-111, :124-125), so 5.0° in the GUI is Fig. 55's 10.0° (SPS, 800 / 800 V, 7.5 µH, n = 1:
  22.4 kW at 10°, calculated); our use of Fig. 55 stands. (b) `tdead` 200 ns is the commanded value; the firmware
  programs 20 counts at 120 MHz = 166.7 ns (GMB:wsinclude/wsepwm.c:47-48, wsdefine.h:31; calculated, assumes the dead
  band counts the time-base clock) - a few watts less body-diode loss in the CRD calibration, small against the 46-91 W
  residual gap.
* **C-4 Our own DAB documents disagree on the transformer-current offset**, surfaced by the references' offset-null
  practice: `DAB60_design_check.txt` line 21 gives uncalibrated offsets up to ±4.0 A and states that the firmware flux
  loop nulls the *measured* DC; `sim/out/dab_design/report.md` §8 assumes a 1.5 A residual; FW-DAB-7 has no offset rule.
  A loop that nulls measured DC with an un-nulled offset drives a real DC bias equal to the offset (4 A ≈ 32 mT at the
  report's 12 mT per 1.5 A, calculated) that the measured-DC trip cannot see → R-WS-5.
* **C-5 -K user guide vs firmware:** tank OCP 120 A peak (UG Table 6 p34) vs "Threshold = 100A (peak)"
  (K:source/InitPeripherals.c:185); "one-shot protections that require a system reset" (UG p34) vs an OFF frame clearing
  them (K:source/DCDC_start.C:41-53); CAN identifiers 0x18B2F4E5 / 0x18B3F4E5 / 0x18B8F4E5 / 0x18B9F4E5 (UG p59-61) vs
  0x1CB2F4E5 / 0x1CB3F4E5 / 0x1AB8F4E5 / 0x1AB9F4E5 (K:INCLUDE/ChargeCan.h:18-23; the GUI follows the code); status bit 11
  "1: Power Off" (UG Table 21b p60) vs set when on (K:source/DCDC_CANCOM.c:532-535).
* **C-6 GMA user guide vs firmware:** the GUI ranges of UG p38 are not enforced by the firmware (GMA:main.c:914-946).
  The GMA README ("the firmware was not reviewed here") and the GMB README ("no firmware in the package", "firmware
  access terms unconfirmed") are now out of date; they were not edited by this review.
* **C-7 REQUIREMENTS.md:** no requirement is contradicted. SRC-7 is corroborated - Wolfspeed's own system stop is the
  controller's trip zone fed by the driver fault lines - with the GMB showing why controller-routed protection needs
  per-path verification (R-WS-7). DAB-08's SPS baseline is what the GMB ships.

### 6.7 Recommended firmware-requirement additions (recommendations only)

| ID | requirement wording | applies to | verdict | basis |
|---|---|---|---|---|
| R-WS-1 | Every value received over a bus or a service tool is checked by the controller against limits derived from the hardware ratings before it is used. Out-of-range or inconsistent values (a zero divisor, a field that overflows, a dead time below the hardware guard, a modulation index or a DAB phase beyond the design clamp of ±60° of the switching period) are rejected with a status code and the previous value is kept. Switching frequency and dead time are not writable in normal operation. | PCS, DAB, PV | ADOPT | GMA:main.c:914-946 + UG p38; GMB:wsinclude/wscan.c:97-103, :148-150 + wsepwm.c:105-140; K:source/DCDC_CANCOM.c:143-170 |
| R-WS-2 | Each unit supervises its controlling master. If no valid reference or heartbeat frame arrives for T_comm (ASSUMED: 3 frame periods = 30 ms on the DAB CAN-B; 1-10 s configurable on the PCS energy-management link), a DAB branch or PV module ramps its power to zero at the FW-DAB-8 / normal stop rate and stops; a PCS in grid-following mode ramps P / Q to a configured fallback (default zero) and stops after a second timeout; a PCS forming the grid keeps forming and reports. Bus-off and error-passive are handled the same way and reported in the status word. Restart needs a fresh command after the link is back. | PCS, DAB, PV | ADOPT | all three lack it: GMA:main.c:276-292; GMB:CRD60DD12N-GMB_main.c:85-94; K:source/DCDC_Warning.C:150-166, DCDC_CANCOM.c:604-607 |
| R-WS-3 | Hazard trips (over-current window, DESAT, output short, over-voltage) are counted per class over a rolling window; the third trip of a class within 10 min (ASSUMED) locks the unit out until a local service reset; an over-voltage seen by both the hardware comparator and the firmware limit locks out at once. An OFF or clear command over the bus never clears a lock-out. | PCS, DAB, PV | ADOPT | K:source/DCDC_start.C:41-53; DCDC_Warning.C:144-147 |
| R-WS-4 | Every non-latched limit (soft DC OV / UV, over-temperature stop, grid window) has an explicit recovery threshold, dwell time and restart-rate limit, e.g. UV recovery ≥ 40 V above the trip held ≥ 3 s and OT recovery ≥ 10 K below the trip held ≥ 60 s (ASSUMED values, set per board); a restart goes through the full start sequence. | PCS, DAB, PV | ADOPT | K:source/DCDC_Warning.C:46-109 |
| R-WS-5 | Before the first switching after power-up and at every idle period (both bridges off, magnetising current decayed per FW-DAB-7, port currents below 1 A), the controller nulls the transformer-current and branch-current sensor offsets over ≥ 64 samples; a null outside the chain's worst-case offset (±4.0 A for the DAB60 HOB 130-P chain) is a sensor fault and blocks the start; the flux-balance loop and the DC trip use the nulled value. Voltage channels are never auto-zeroed. | DAB (the voltage rule: all) | ADOPT | GMB:CRD60DD12N-GMB_main.c:62-66, wsinclude/wssensor.c:614-629; K:source/DCDC_Adc.c:104-132, DCDC_start.C:56; ours: C-4 |
| R-WS-6 | Every exception path (illegal-operation trap, NMI, default or unhandled interrupt, CLA fault, stack check) first forces the one-shot trip on all ePWM modules and stops the heartbeat; the clock-fail and emulation-stop one-shot sources are enabled on every ePWM; the DAB controller adopts the PCS-CTL watchdog rule (timeout ≤ 10 ms, serviced only after the control ISR ran). | PCS, DAB, PV | ADOPT (no hardware) | GMA:device/driverlib/interrupt.h:138-223, device/device.c:62-64; GMB:CPU1_FLASH/syscfg/board.c:812; K:source/InitPeripherals.c:96-101, Main.c:111 |
| R-WS-7 | The start-up self-test fires each gate-driver fault line and each comparator trip source on its own (where the hardware allows a test injection) and checks that the one-shot appears on every PWM output; the 10 ms configuration read-back covers the X-BAR multiplexers, the digital-compare selections, the one-shot source lists and the trip actions of every ePWM against a stored table. | PCS, DAB, PV | ADOPT | GMB routing gap (C-2); extends PCS-CTL check line 70 and guide 05 lines 222, 225 |
| R-WS-8 | No mode reachable over a field bus disables a protection. Open-loop or test operation, calibration writes and parameter changes are accepted only in SERVICE (entered with the PWM off, every trip active), with an authorisation (key + local switch or HMI) and a timeout back to OFF. Released firmware has no raw memory read / write over CAN or RS-485 (parameter access through a whitelist with range checks); firmware updates over the bus ("CAN updates", ARCHITECTURE-PCS.md:290) are authenticated. | PCS, DAB, PV | ADOPT | K:source/DCDC_CANCOM.c:248-376; DCDC_start.C:41-73; PWM_Isr.c:77-78 |
| R-WS-9 | Calibration coefficients and the build code are stored redundantly (three copies with 2-of-3 vote and repair, or two copies with a CRC) and range-checked at load; a failed load sets a status bit and blocks the start; calibration writes are accepted only in SERVICE, two-point fits require increasing points, and every write is verified by read-back. | PCS, DAB, PV | ADOPT | K:source/DCDC_E2PROMdataDriver.C:181-300; DCDC_E2PROM.C:17-136; INCLUDE/OBC_constant.h:206-223 |
| R-WS-10 | Dead-band counts are computed from the time-base clock read back at start, never from a constant that assumes a clock; dead time may differ per operating mode (table); the programmed values are verified at the gates at commissioning. | PCS, DAB | CONSIDER | GMB:wsinclude/wsepwm.c:47-48 (0.833 x, calculated); K:source/DCDC_start.C:487-488; UG -K p59 |
| R-WS-11 | A module NTC that reaches the controller as a gate-driver APWM duty is measured by eCAP (period and high time, averaged over ≥ 10 periods); the carrier period and the duty window of the driver are checked, and a reading outside them is a sensor fault treated as hot. | DAB60 rev B0, any AIN / APWM user | CONSIDER | GMA:TEMPERATURE.c:82-100, :175-211; UG GMA p32; §4 row 7 (AIN → APWM accuracy) |
| R-WS-12 | One service interface for PCS, DAB and PV: commands = enable / disable (edge-triggered), stop with ramp, fault clear (accepted only with the enable inactive), mode (GFL / GFM, branch count) and references with limits; status = run state, per-class fault bits with first-fault capture, limit and derating flags, bus error state; telemetry = port voltages, currents, power and temperatures every 0.5 s, fault log on request; a factory test mode (SERVICE only, every trip active) that drives a reactive load open loop at rated current for burn-in. | PCS, DAB, PV | CONSIDER | GMA:main.c:882-931 + UG p37-45; GMB:wsinclude/wscan.c:31-158; K:source/DCDC_CANCOM.c:111-610 |
| R-WS-13 | Static analysis (a MISRA C:2012 subset) and bounds checks on every buffer index shared with an interrupt are part of the firmware build. | all firmware | CONSIDER (low) | GMA:main.c:46, :1053-1057 (eCAP buffer overrun) |
| R-WS-14 | After a grid-caused stop the PCS reconnects only after the grid has stayed inside its voltage and frequency windows for the configured observation time, then ramps its power at the configured gradient (grid-code values, e.g. EN 50549-1 - from memory, the standard is not on file). | PCS | CONSIDER | in neither the references nor pcs_control_spec (state_machine) |

### 6.8 Not read, and limits

* GUI executables: the two Python GUIs (PyInstaller) were listed statically (main-script strings only); their numeric
  limits were taken from the user guides. The -K C# GUI was read from source; its binaries and the USB-CAN Windows
  drivers were not examined. TI libraries (`.lib`) were not examined.
* The -K design files were not fetched; its hardware facts come from the user guide (filed 2026-10-05).
* Statements that depend on TI reference-manual behaviour (TZ4-TZ6 meanings, dead-band clocking, digital-compare
  latching, CMPSS filter timing, watchdog clock) are labelled inferred in the notes; no TRM is on file.
* No loop bandwidth of the -K can be derived (the LLC gain slope is not in the code); the GMA and GMB have no loops.
