# CRD-60DD12N-K firmware - what the source code shows

Package: `crd60dd12n-k_firmware-gui.zip` (supplied by the owner on 2026-10-05, row in `docs/SOURCES.csv`), unpacked by
the owner in `firmware/`. Traces to REQUIREMENTS.md **DAB-08** (cross-check of our DC/DC controls), **SRC-7** and **S-7**.
Written 2026-10-05. **Static reading only:** nothing was built, flashed, run or emulated; the C# GUI and the USB-CAN
drivers were never started. Nothing here is bench-validated.

**Identity first.** This is **not a dual active bridge.** The user guide on file (PRD-07229 Rev. 1, March 2023, p1)
and the product page name it a "60 kW three-phase interleaved LLC DC/DC converter": three half-bridge LLC legs on the
input side, SiC Schottky full-bridge rectifiers (C6D20065D) on the output side, unidirectional, 120-250 kHz (UG p8,
p25). The firmware agrees: the actuator is the switching period of three interleaved half-bridges (section 3), and there
is no output-side switch to phase-shift. Its value for DAB-D60 is in protection, sequencing, calibration and the service
interface, not in the modulation law. Its devices (C3M0040120K / C3M0032120K, UCC5350MC drivers) are Wolfspeed's, not
ours; device values are reference points only (section 8).

Labels: **read** = taken from the source at the file:line given · **calculated** = derived by us (formula given) ·
**inferred** = depends on TI reference-manual or datasheet knowledge that is not on file.
Paths: `F/` = `firmware/Wolfspeed_CRD60DD12N-K_Firmware_GUI/CRD60DD12N-K_DCDC_F28377D_V1.00/`;
`G/` = `firmware/Wolfspeed_CRD60DD12N-K_Firmware_GUI/CRD60DD12N-K GUI/CRD30DD12N-K_CAN_GUI_V1.00/ECanTest/`;
`UG` = `crd60dd12n-k_user-guide_prd-07229.pdf`, cited by PDF page. Line numbers count the files as shipped (CRLF).

Commands (read-only): `find`, `file`, `cat -n` / `grep -n` (after stripping CR) of every file in `source/` and `INCLUDE/`,
the CPU-timer and GPIO files of `source_device_support/`, the `.map` header; `iconv` + `grep` of the C# GUI sources;
`pdftotext -layout` of the UG and of the GUI setup slides shipped in the package.

---

## 1. What the package contains

| item | content | evidence |
|---|---|---|
| outer zip | 19,385,929 B, 398 files | `docs/SOURCES.csv` |
| firmware | CCS project `CRD60DD12N-K_DCDC_F28377D_V1.00` (CCS 10.1 era, compiler 18.1.1.LTS in the project file): `source/` 15 files (`PWM_Isr.c`, `DCDC_start.C`, `DCDC_Warning.C`, `DCDC_Adc.c`, `DCDC_cal.C`, `DCDC_CANCOM.c`, `DCDC_initial.C`, `DCDC_E2PROM*.C`, `InitPeripherals.c`, `CLA_Isr.cla`, CAN drivers), `INCLUDE/` 13 headers, TI device-support files, libraries (IQmath, Flash API, SFO for HRPWM); built `Debug/CRD60DD12N-K_DCDC_F28377D_V1.00.out` (744,492 B, linked 2023-03-13 with linker v20.2.1) | `F/source/Main.c:1-9`; `.ccsproject`; `.map` header |
| header | "Processor: TMS320F28377D ... Version: V1.0 ... Date: 8-Feb-2022 ... CPU Frequency: 200MHz" | `F/source/Main.c:4-9` |
| GUI | C# WinForms source + binaries, written for the 30 kW predecessor CRD-30DD12N-K; works only with GCAN USBCAN-I adapters at 125 kbit/s (setup slides, Mar 2022, in the package); Windows USB-CAN drivers | `G/frmM.cs`; `CRD30DD12N-K GUI and Setup introduction Mar 1 2022.pdf` p5, p8 |
| heritage | the project carries OBC / PFC remnants: `OBC_*.h` file names, CLA variables for a PFC (`F/INCLUDE/Shared.h:32-65`), synchronous-rectifier dead-time constants (`F/INCLUDE/OBC_constant.h:23-34`), the auxiliary-power-board schematic is titled "6.6kW Bi-direction OBC for EV charger - Aux Primary" (UG p23) | as cited |

## 2. Controller and timing

| item | value | evidence |
|---|---|---|
| MCU | TMS320F28377D, CPU1 only, 200 MHz; ePWM time base 100 MHz (10 ns per count) | `F/source/Main.c:4-9`; `F/INCLUDE/OBC_constant.h:14` |
| PWM | ePWM1 = master time base (up count, sync-out at zero, ADC trigger); ePWM4 / ePWM7 = sync relays for legs B / C (sync-out at CMPB = 120° / 240° of the period); legs on ePWM2/3 (A), 5/6 (B), 8/9 (C), one ePWM per switch, up-down, HRPWM period and edges | `F/source/InitPeripherals.c:611-644`, `:650-687`, `:466-560` |
| switching frequency | variable, Ts in 10 ns counts: 250 kHz maximum (400 counts), minimum from a bus-voltage-dependent ceiling (below) | `F/INCLUDE/OBC_constant.h:42-65` |
| control interrupt | CPU Timer 2, **100 kHz** (period 1999 + 1 cycles at 200 MHz), runs from RAM; **not synchronised to the PWM** | `F/source_device_support/F2837xD_CpuTimers.c:107`; `F/source/PWM_Isr.c:9-17`; `F/source/InitPeripherals.c:51` |
| inside the ISR | an 8-step sub-scheduler (filters every 80 µs, 80 µs time base), short check, hardware-OVP debounce, filters, voltage and current PI, actuator writes | `F/source/PWM_Isr.c:29-273` |
| background | 5 ms Timer 0 scheduler: watchdog kick, 10 ms protections, 5 ms measurements, 30 ms reference shaping, CAN | `F/source/Main.c:81-150`; `F2837xD_CpuTimers.c:71` |
| ADC | all channels started by ePWM1 SOCA at its period (one conversion set per switching period), 150 ns window; the 100 kHz ISR reads the newest results asynchronously; no oversampling in the loop (2-sample means) | `F/source/InitPeripherals.c:277-337`, `:700-702`; `F/source/PWM_Isr.c:104-147` |
| computation delay | not compensated; period / compare registers load from shadow at counter zero / period | `F/source/InitPeripherals.c:468`, `:494`, `:506-513` |
| dead time | ePWM dead band (rising edge on each switch's own ePWM), **150 ns** (15 counts) in 3-phase half-bridge mode, **+100 ns = 250 ns in 2-phase full-bridge mode** "to avoid the shoot through risk, due to the Vgs oscillation issue in this Mode"; measured at the gates 137 / 153 ns (UG Table 18) | `F/INCLUDE/OBC_constant.h:15-38`; `F/source/InitPeripherals.c:527-532`; `F/source/DCDC_start.C:484-491`; UG p59 |
| watchdog | enabled at boot (WDCR 0x2F), serviced only by the 5 ms background scheduler; timeout ≈ 0.84 s (256 x 512 x 64 / 10 MHz, calculated with the C2000 watchdog clock, inferred) | `F/source/InitPeripherals.c:96-101`; `F/source/Main.c:63-64`, `:111`; `F/INCLUDE/A_macro.h:137` |

## 3. Control structure

| item | value | evidence |
|---|---|---|
| loops | output-voltage PI and output-current PI computed in parallel every 10 µs; the **lower output wins** (CV or CC); constant power is a current limit = P_set / max(V_out, 200 V) (CP) | `F/source/PWM_Isr.c:150-196`; `F/source/DCDC_cal.C:69-109` |
| PI form | incremental: u += K2 e(k) + K3 e(k−1), Q16, output in switching-period counts | `F/source/PWM_Isr.c:164-180` |
| voltage loop | K2 1469, K3 −1464 → K_p 0.0223, K_i T 7.6e-5 (period counts per ADC count), integral zero ≈ 54 Hz (calculated); error clamped to ±215 counts (≈ ±94 V, calculated); above 11 counts (≈ 5 V) of error K2 grows by 7 per count, up to x287 of the integral gain (calculated) - a large-signal integral boost | `F/source/DCDC_initial.C:65-67`; `F/source/PWM_Isr.c:153-169`; `F/INCLUDE/OBC_constant.h:82` |
| current loop | K2 1700, K3 −1500 → K_p 0.0229, integral zero ≈ 2.1 kHz (calculated); error clamped to ±40 counts (≈ ±3.2 A, calculated) | `F/source/DCDC_initial.C:68-69`; `F/source/PWM_Isr.c:171-180`; `F/INCLUDE/OBC_constant.h:81` |
| anti-windup | both integrators clamped to [0, permit]; the tracking clamps of the unselected loop are commented out, so the idle loop sits at its clamp and a CV ↔ CC change starts from there (inferred consequence) | `F/source/PWM_Isr.c:169`, `:180`, `:197-198` |
| loop bandwidth | not derivable: the plant gain d(V_out)/d(Ts) of the LLC is not in the code | - |
| actuator, 3-phase half bridge | the PI output is the switching period, clamped [250 kHz, ceiling]; legs B / C fixed at 120° / 240°; each switch's on-time = min(Ts / 4, PI / 4) per half period, so at the frequency ceiling the on-time shrinks below 50 % (light-load duty control); in normal run an on-time ≤ 16 counts is forced to the 2-count minimum ("Mosfet duty cycle=0") | `F/source/PWM_Isr.c:210-249`, `:260-273` |
| actuator, 2-phase full bridge | selected when the set-point is ≤ 250 V (back above 260 V): leg C off, phase shift between legs A and B = PI x 180° / Ts at the frequency ceiling, then frequency control at 180° (hybrid); current limited to 0.66 x rated, power to 40 kW | `F/source/PWM_Isr.c:250-257`; `F/source/DCDC_start.C:174-223`; `F/source/DCDC_cal.C:76-86`, `:117-120`; UG p10 |
| frequency ceiling vs bus | lowest frequency 175 kHz up to 720 V of bus, interpolated to 120 kHz at 850 V, clamped 101-250 kHz, slewed 2 counts per background pass; 115 kHz in test mode | `F/source/DCDC_start.C:118-163`; `F/INCLUDE/OBC_constant.h:49-63` |
| set-point vs bus | between 660 V and 840 V of bus the output set-point is replaced by V_bus x 0.411 (parallel) or x 0.822 (series): the LLC runs at its resonance ratio and the PFC stage is expected to set the bus to the battery | `F/source/DCDC_cal.C:159-212`; `F/INCLUDE/OBC_constant.h:40`; UG p10-11, p27 |
| reference slew | voltage 0.29 V per 18 ms (≈ 16 V/s), current 0.59 A per 30 ms (≈ 20 A/s), power 300 W per 30 ms (10 kW/s) (calculated) | `F/source/DCDC_cal.C:22-122`; `F/INCLUDE/OBC_constant.h:199-201` |
| soft start | every 0.8 ms (10 ticks of the 80 µs timer; the constant is named `TIME711US`): PI output ceiling +1 count from 5 to ceiling + 30 (on-time grows at 250 kHz, then the frequency falls) and voltage reference + 1/600 of target (480 ms walk-in); normal run when both are complete | `F/source/DCDC_start.C:4-7`, `:366-403` |
| light load | a switched dummy load on during soft start, then on below 2.5 A and off above 3.5 A of output current; two MOSFETs balance the output capacitor halves in series mode at light load (UG) | `F/source/DCDC_start.C:472`, `:515-522`; `F/INCLUDE/A_macro.h:122-123`; UG p25 |
| configuration changes | series / parallel accepted only while off; topology change = stop and restart through PROTECTION | `F/source/DCDC_CANCOM.c:124-140`; `F/source/DCDC_start.C:54-73`, `:184`, `:192` |

## 4. Protection and the edge cases

### 4.1 Fault classes (thresholds read; UG Table 6 p34 agrees except where noted)

| fault class | detection | threshold, filter, latency | reaction | latch / recovery | evidence |
|---|---|---|---|---|---|
| resonant-tank over-current (per leg) | current transformer → CMPSS6/7/8 **high side only**, digital filter (window 10, threshold 7, sample clock /3); → ePWM X-BAR TRIP7 → DCAEVT1 one-shot on the six leg ePWMs (outputs low) + ePWM1 TZ interrupt | DAC 2600 counts, code comment "Threshold = 100A (peak)" - **UG Table 6 says 120 A peak**; filter ≈ 0.1-0.15 µs (inferred) | hardware one-shot; ISR forces the outputs low, sets OCP, disables its own interrupt | latched; cleared by an OFF command (state STAND_BY) and re-armed by the next soft-start init | `F/source/InitPeripherals.c:151-200`, `:562-568`, `:591-605`; `F/source/PWM_Isr.c:288-310`; `F/source/DCDC_start.C:41-53`, `:303-329` |
| output short, running | raw output voltage ≤ 216 counts (≈ 95 V, parallel) or ≤ 647 counts (≈ 284 V, series) on 2 consecutive 10 µs passes, only in NORMAL_RUN and not in test mode | ≈ 20 µs + ADC age (calculated) | outputs forced low, SHORT | latched until OFF | `F/source/PWM_Isr.c:74-101`; `F/INCLUDE/OBC_constant.h:135-136` |
| output short, at start | once the voltage walk-in passes 25 % of target: filtered output < 20 V (series) / 10 V (parallel) | per 0.8 ms step | outputs low, SHORT | latched until OFF | `F/source/DCDC_start.C:405-432` |
| output over-voltage, hardware | comparator behind an optocoupler → GPIO94 (6-sample input qualification at 2.55 µs) → ISR debounce > 10 passes | ≈ 15 µs + 110 µs ≈ 0.13 ms (calculated) | outputs low, HWDCOV | **auto-clears** after ≈ 1.5 s low: the counter is incremented twice per pass, so the coded 300,000 is reached in 150,000 passes (calculated) | `F/source_device_support/F2837xD_Gpio.c:128-131`; `F/source/PWM_Isr.c:108-131` |
| output over-voltage, firmware | V_out > 1050 V (series) / 550 V (parallel) for 15 x 10 ms on a ≈ 150 ms averaged value | ≈ 0.3 s (calculated) | PROTECTION (PWM off) | auto-recovers 60 V lower after 150 ms | `F/source/DCDC_Warning.C:112-141` |
| output over-voltage lock-out | hardware and firmware OV flags set at the same time | - | HVSDLOCK | **never cleared in code: power cycle** | `F/source/DCDC_Warning.C:144-147`; `F/source/DCDC_start.C:63` |
| input (bus) over-voltage | > 890 V for 50 x 10 ms | 0.5 s | PROTECTION | auto, below 875 V after 10 ms | `F/source/DCDC_Warning.C:51-67` |
| input under-voltage | < 600 V for 100 x 10 ms; also no start at ≤ 350 V | 1 s | PROTECTION | auto, above 640 V held 3.25 s | `F/source/DCDC_Warning.C:69-84`; `F/source/DCDC_start.C:55` |
| ambient over-temperature | control-board air sensor (10 mV/°C, −50 °C offset) > 79 °C for 100 ms | 0.1 s after a ≈ 150 ms average | PROTECTION | auto, below 70 °C for 100 ms | `F/source/DCDC_Warning.C:89-109`; `F/source/DCDC_Adc.c:275-305`; `F/INCLUDE/OBC_constant.h:145-146` |
| sensor offsets not yet measured | 1 s after power-up, 64 samples of output current, output voltage and bus voltage | ≈ 1.3 s | no start until done | - | `F/source/DCDC_Adc.c:104-132`; `F/source/DCDC_start.C:56` |
| calibration data | EEPROM magic word and range checks on every coefficient, three copies with 2-of-3 vote and repair, verified writes | at boot | defaults + CALIBRFAIL status bit; **start is not inhibited** | - | `F/source/DCDC_E2PROM.C:17-136`; `F/source/DCDC_E2PROMdataDriver.C:181-300`; `F/INCLUDE/OBC_constant.h:206-223`; `F/source/DCDC_start.C:54-66` |
| CAN | error counters REC or TEC > 10 → status bit 0; no frame for 40 s → CAN re-initialised only (the OFF action is commented out); any received frame resets the timer | 40 s | **converter keeps running** | - | `F/source/DCDC_CANCOM.c:43`, `:604-607`; `F/source/DCDC_Warning.C:150-166` |
| current / power limits | CC at the set current (≤ 100 A series, 200 A parallel, 0.66 x in full bridge), CP ≤ 60 kW (40 kW full bridge); "a precaution with limited accuracy" | - | regulation, not a trip | - | `F/source/DCDC_cal.C:69-122`; UG p34 |
| not present | input over-current, output over-current trip, device / heatsink temperature, fan supervision, DESAT (UCC5350MC has none), auxiliary-supply monitoring, inrush limiting ("no current inrush limiter for either port", UG p38) | - | - | - | source; UG p25, p38 |

### 4.2 State machine

STAND_BY (OFF command: PWM and dummy load off, **SHORT and OCP cleared**, topology reset to half bridge) → PROTECTION
(any of: bus ≤ 350 V, offsets not measured, output OV hardware or firmware, bus OV / UV, ambient OT, SHORT, HVSDLOCK, a
restart request, OCP) → POWERON → SOFTSTART_INIT → SOFTSTART → NORMAL_RUN. From PROTECTION the unit restarts by itself as
soon as every condition is clear (`F/source/DCDC_start.C:37-107`; `F/INCLUDE/OBC_main.h:88-96`).

### 4.3 Edge cases in the code

1. **An OFF frame clears the "one-shot" faults.** UG p34 says the tank OCP and the short-circuit protection "require a
   system reset", but any OFF command clears SHORT and OCP and the next ON restarts (`F/source/DCDC_start.C:41-53`):
   a remote master can retry into a short without limit.
2. **Over-voltage confirmed twice locks out** until power is cycled (HVSDLOCK) - the one lock-out rule in the three
   packages.
3. **Non-latched faults recover with hysteresis and dwell** (bus OV 890 / 875 V, UV 600 / 640 V with 1 s / 3.25 s, OT
   79 / 70 °C) and restart automatically through soft start.
4. **Test mode removes the protections.** A CAN command (0xC0 / 0x51) sets TestMode; with it the PROTECTION gating and
   the running short check are skipped and the PC sets the switching period directly (`F/source/DCDC_CANCOM.c:248-305`;
   `F/source/DCDC_start.C:41-73`; `F/source/PWM_Isr.c:77-78`).
5. **Raw memory access over CAN.** Command 0xC1 reads or writes any 16- or 32-bit address, without mode check or
   authentication (`F/source/DCDC_CANCOM.c:331-376`).
6. **Calibration over CAN at any time**, gains clamped (`F/source/DCDC_CANCOM.c:378-452`), stored as above.
7. **Voltage channels are auto-zeroed at power-up.** The output-voltage offset is taken from the reading 1 s after
   power-up (`F/source/DCDC_Adc.c:113-121`; `F/source/PWM_Isr.c:104`): an output still charged at power-up (a battery or
   a slowly discharging bank) would become the zero. The UG start procedure avoids it by sequencing the supplies
   (aux first, UG p38).
8. **No reaction to communication loss** beyond the CAN re-initialisation after 40 s.
9. **Slow input over-voltage**: 0.5 s filter on the bus; the bus-OVP ADC channel is wired but marked "Reserved"
   (`F/INCLUDE/A_macro.h:72`; `F/source/InitPeripherals.c:332-334`).
10. **The watchdog guards only the background loop**; a stalled 100 kHz ISR is not detected while the background runs.
11. **One-sided over-current comparator** on an AC tank current: a fault is seen on the next positive peak, up to half a
    period later (2-4 µs at 250-120 kHz, calculated).
12. **The set-point is silently replaced** inside the 660-840 V bus band; the applied value is only visible in telemetry.
13. **A bit-rate setup error stops the CPU** (`ESTOP0`) during initialisation (`F/source/DCAN_Driver.c:119-123`).

## 5. CAN and GUI interface

| item | value | evidence |
|---|---|---|
| bus | CAN 2.0B, 29-bit identifiers in a J1939-like layout (priority, command, destination, source), **125 kbit/s**, isolated on the board | `F/source/DCAN_Driver.c:113-114`; `F/INCLUDE/ChargeCan.h:80-100`; UG p27, p29 |
| control command 0x18A5E5F4 | byte 0 series (0) / parallel (1), accepted only while off; byte 1 OFF (0) / ON (1); power [1 W] clamped 100 W-60 kW; voltage [0.1 V] clamped 500-1000 V (series) / 200-500 V (parallel), then replaced by the bus ratio inside 660-840 V; current [0.1 A] clamped 1 A to 100 / 200 A; no reply | `F/source/DCDC_CANCOM.c:111-229`; UG p29, p62 |
| query 0x18A8E5F4 | replies with limits and versions on 0x1AB8F4E5 / 0x1AB9F4E5 (code) - **UG Tables 22-23 (p61) list 0x18B8F4E5 / 0x18B9F4E5** | `F/source/DCDC_CANCOM.c:231-246`; `F/INCLUDE/ChargeCan.h:22-23` |
| service commands | 0xC0 open loop / test mode / topology / on-off, reply 0x18D0F4E5; 0xC1 memory read / write, reply 0x18D1F4E5; 0xAB calibration, reply 0x18BBF4E5 | `F/source/DCDC_CANCOM.c:248-452`; `F/INCLUDE/ChargeCan.h:11-26` |
| periodic frames | every 0.5 s: output V [0.1 V], output I [0.1 A], bus V [0.1 V], output P [1 W] (0x1CB2F4E5); run state + status + control flags (0x18B4F4E5); status word + flags + state + CV/CC/CP (0x1CB3F4E5); every 3 s: ambient temperature [0.1 °C, +50 °C bias] (0x18B0F4E5). **UG Tables 19-21 give the first and third as 0x18B2F4E5 / 0x18B3F4E5**; the GUI uses the code's identifiers | `F/source/DCDC_CANCOM.c:61-88`, `:465-506`; `G/frmM.cs:459-565`; UG p59-60 |
| status word | bit 15 always 1, 14 short, 13 tank OCP, 12 bus UV, 11 **on** (UG Table 21b: "1: Power Off" - inverted; firmware and GUI agree with each other), 10 calibration error, 9 full-bridge topology, 8 parallel, 7 output OV / lock-out, 6 bus OV, 4 ambient OT, 0 CAN error | `F/source/DCDC_CANCOM.c:508-610`; `G/frmM.cs:542-565`; UG p60 |
| GUI behaviour | sends the command only when "Send to DCDC" is pressed, plus a keep-alive frame 0x1801E5F4 every 250 ms (the firmware ignores its content; any frame resets the 40 s timer); shows the run states Standby / Protection / Power On / Soft-start Init / Soft-start / Normal @3ph Half Bridge / Normal @ Full Bridge | `G/frmM.cs:1173-1189`, `:1213-1263`, `:698-729`; `G/frmM.Designer.cs:1489`; UG p27 |

## 6. What could not be read

* The GUI binaries, the USB-CAN Windows drivers and the TI libraries (`LIB/*.lib`) were not examined; the GUI C# source
  was read instead.
* The design files (schematics, BOM) of the -K were not fetched; hardware statements come from the UG.
* The LLC gain curve d(V_out)/d(Ts) is not in the code, so loop crossovers cannot be computed.

## 7. What we take from it

* **The most complete protection set of the three packages**, with explicit latch / auto-recover classes, hysteresis and
  dwell times, a start inhibit until the sensor offsets are known, and an over-voltage lock-out. Our PCS and DAB
  specifications define the trips but not their recovery rules or trip counting (REFERENCE-LESSONS §6, R-WS-3, R-WS-4).
* **Calibration storage done properly**: three copies, 2-of-3 vote with repair, range checks, defaults with a status bit
  (R-WS-9) - with one fix: a unit with failed calibration should not start.
* **Mode-dependent dead time** (+100 ns in the full-bridge mode for gate ringing) supports keeping dead time a table
  entry per operating mode (our DAB look-up table already is).
* **Input-dependent operating limits** (frequency ceiling from the bus voltage, set-point following the input) are the
  LLC's version of our DAB feasibility map and PCS operating-map refusal - same principle, nothing to add.
* **System coordination idea:** the -K expects the front end to move the bus with the battery so the DC/DC stays at its
  resonance ratio; our DAB design report reaches the same conclusion for SPS (run the PCS DC bus near n V2).
* **Negative lessons, made requirements in REFERENCE-LESSONS §6:** no protection-bypassing test mode and no raw memory
  access on the field bus (R-WS-8); OFF must not clear a latched hazard trip without a count (R-WS-3); a defined reaction
  to communication loss (R-WS-2); never auto-zero a voltage channel (R-WS-5); the watchdog must prove the control ISR ran
  (already our rule); windowed (both-sign) over-current comparators on AC currents (already ours).

## 8. Not applicable to our devices (reference points only)

| item | Wolfspeed value | why it does not transfer |
|---|---|---|
| dead times | 150 ns half bridge, 250 ns full bridge (`OBC_constant.h:36`; `DCDC_start.C:487`) | 2 x C3M0040120K per position, UCC5350MC on R15P21503D +15 / −3 V (UG p25) |
| tank OCP | DAC 2600 counts ≈ 100-120 A peak (`InitPeripherals.c:185`; UG p34) | current-transformer ratio and tank of this design |
| resonant tank | L_r 7.5 µH, C_r 102 nF, L_m 30 µH (UG p25): f_r ≈ 182 kHz (calculated); frequency limits 101-250 kHz and the 175 kHz ceiling below 720 V | LLC-specific; our DAB is SPS / TPS at a fixed 100 kHz |
| output ratio | 0.411 (parallel) / 0.822 (series) of the bus (`OBC_constant.h:40`) | transformer 12:10:10 (UG p25) |
| short thresholds | < 100 V / < 300 V running, < 10 V / < 20 V at start (`PWM_Isr.c:75`; `DCDC_start.C:411`, `:419`) | output ranges 200-500 V / 500-1000 V of this design |
| loop gains | voltage K2 1469 / K3 −1464, current 1700 / −1500 (`DCDC_initial.C:65-69`) | plant-specific (LLC gain, output capacitors 12 film + 3 electrolytic, UG p25) |
| ambient OT | 79 / 70 °C on a control-board air sensor (`OBC_constant.h:145-146`) | board and cooling specific; ours uses heatsink / plate / winding NTCs |
| dummy load | 2.5 / 3.5 A (`DCDC_start.C:515-522`) | LLC light-load gain; our DAB idles with the gates off below 1.5 kW (FW-DAB-3) |
| full-bridge derating | 0.66 x rated current, 40 kW (`DCDC_cal.C:76-86`, `:117-120`) | this tank in 2-phase operation |
