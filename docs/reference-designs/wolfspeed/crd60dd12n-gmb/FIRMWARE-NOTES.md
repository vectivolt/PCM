# CRD60DD12N-GMB firmware - what the source code shows

Package: `crd60dd12n-gmb_firmware-gui.zip` (supplied by the owner on 2026-10-05, row in `docs/SOURCES.csv`), unpacked in
`firmware/`; its two inner zips are unpacked in place. Traces to REQUIREMENTS.md **DAB-08** (our DAB controls are
cross-checked against references), **§3 basis = CRD60DD12N-GMB**, **SRC-7** and **S-7**. Written 2026-10-05. **Static
reading only:** nothing was built, flashed, run or emulated; the GUI executable was never started. Nothing here is
bench-validated. The devices on this board (CBB011M12GM4T modules, UCC21710 drivers) are Wolfspeed's, not ours; device
values are reference points only (section 8).

Labels: **read** = taken from the source at the file:line given · **calculated** = derived by us (formula given) ·
**inferred** = depends on TI reference-manual or datasheet knowledge that is not on file.
Paths: `F/` = `firmware/CRD60DD12N-GMB Firmware [V1.1.0]/`; `UG` = `crd60dd12n-gmb_user-guide_prd-10001.pdf`
(PRD-10001 Rev. 1, May 2026), cited by PDF page; `CS` = `design-files/CRD60DD12N-GMB V2.0 - Schematic/CRD60DD12N-GMB-CONTROL V2.0 (02-2025) - Schematic.pdf`.

Commands (read-only): `unzip -l` / `unzip -q`, `cat -n` / `grep -n` of every application `.c`, `.h`, `.cla`, `.cmd`,
the SysConfig file `CRD60DD12N_GMB.syscfg` and the generated `CPU1_FLASH/syscfg/board.c`, `board.h`, `clocktree.h`;
`pdftotext -layout` of the UG and the control schematic; a stdlib Python reader of the PyInstaller table of contents of
the GUI executable (main-script strings listed, nothing executed). One generated third-party file of the unpacked tree
(`CPU1_FLASH/syscfg/pinmux.csv`) was deleted again because `.gitignore` admits every `*.csv` below
`docs/reference-designs/`; it remains inside the zip.

---

## 1. What the package contains

| file | bytes | what it is |
|---|---|---|
| `crd60dd12n-gmb_firmware-gui.zip` | 25,701,343 | outer zip, two members dated 2026-03-11 |
| `firmware/CRD60DD12N-GMB Firmware [V1.1.0].zip` | 1,590,695 | CCS project (CCS 12.7.1 project file, compiler 22.6.1.LTS): `CRD60DD12N-GMB_main.c` 162 lines, `CRD60DD12N-GMB_cla.cla` 18, folder `wsinclude/` (wscan, wsepwm, wsgpio, wsmath, wssensor, wsdefine: 2,694 lines), SysConfig file 502 lines and its generated `board.c` 1,278 lines, linker `28003x_cla_flash_lnk.cmd`; built `CPU1_FLASH/CRD60DD12N-GMB.out` (262,708 B, linked 2026-03-11) |
| `firmware/GMB DAB CAN Interface [V1.0.0].zip` | 24,136,719 | `GMB DAB CAN Interface.exe` (PyInstaller, Python 3.9, python-can) + licence |
| left over in the project | - | `CPU1_FLASH/CRD25DA12N-FMC CLA Firmware.out/.map` (another design's build, linked 2024-12-09) and launch files of TI's `cla_ex2_atan` example: the project was cloned from the CRD25DA12N-FMC and a TI CLA example |

Header: "provides only basic code to operate the DAB **open-loop** by manually adjusting the phase shift over a Controller
Area Network (CAN) interface ... a starting point only" (`F/CRD60DD12N-GMB_main.c:11-18`); "V1.0.0 (2026-01-11) Initial
release. V1.1.0 (2026-03-06) Applied sensor gains and calibration" (`:21-22`). UG p43: "basic open-loop single-phase-shift
(SPS) control".

## 2. Controller and timing

| item | value | evidence |
|---|---|---|
| MCU | TMS320F280039C (controlCARD), C28x at 120 MHz; ePWM in the 120 MHz SYSCLK domain | `F/wsinclude/wsdefine.h:31-32`; `F/CPU1_FLASH/syscfg/clocktree.h:55-58`, `:147-175` |
| PWM | 4 ePWM, one per leg: ePWM1/2 primary legs, ePWM3/4 secondary legs; up-down count; TBPRD = 120e6 / f_sw / 2 = **600 at 100 kHz** (calculated); no HRPWM | `F/wsinclude/wsepwm.c:61`; `F/CPU1_FLASH/syscfg/board.c:783`, `:821`, `:859`, `:896` |
| leg waveform | CMPA = TBPRD / 2 (50 %); ePWM2 and ePWM4 have the inverted action qualifier of ePWM1 and ePWM3, so each bridge is a square wave | `F/wsinclude/wsepwm.c:42`; `board.c:793-794`, `:830-831`, `:868-869`, `:905-906` |
| synchronisation | ePWM1 sync-out at counter zero; ePWM3/4 load TBPHS on sync; ePWM2 runs from the common TBCLKSYNC start without phase load | `board.c:784-786`, `:822`, `:860-861`, `:897-898`; `F/CRD60DD12N-GMB_main.c:47-53` |
| switching frequency | 100 kHz default; firmware bounds 10-100 kHz; GUI 80-100 kHz | `wsdefine.h:37`, `:45-46`; UG p46 |
| interrupts | only the four trip-zone interrupts; **no control interrupt, no ADC interrupt**; the CLA task is a `NOP` | `board.c:1185-1201`; `F/CRD60DD12N-GMB_cla.cla:12-14` |
| background loop | fixed **1 s** delay: CAN receive and apply, status frame, telemetry, LED toggles | `F/CRD60DD12N-GMB_main.c:81-134` |
| ADC | software-forced, each reading the mean of 5 conversions, for telemetry only; the AC (transformer) current conversions are commented out | `F/wsinclude/wssensor.c:541-603`; `wsdefine.h:59-61`; `F/CRD60DD12N-GMB_main.c:124-126` |
| dead time | ePWM dead band, rising = falling = `deadTime / 10` counts; default 200 ns → 20 counts | `F/wsinclude/wsepwm.c:47-48`; `wsdefine.h:38` |
| dead time actually programmed | 20 counts x 8.33 ns = **166.7 ns**, i.e. 0.833 x the commanded value - the divide-by-10 assumes a 100 MHz time base (calculated; assumes the dead band counts TBCLK, the reset default; inferred) | as above |
| watchdog | disabled, never enabled | `F/device/device.c:72` |
| exception handlers | TI driverlib default, illegal-operation and NMI handlers: `ESTOP0; for(;;)` - the ePWM keeps switching at the last settings | `F/device/driverlib/interrupt.c:176-250` |

## 3. Modulation and control

No current or voltage loop exists. Power is set by the phase shift the user types into the GUI.

| item | value | evidence |
|---|---|---|
| law | **single phase shift (SPS)**, 50 % legs; no EPS / DPS / TPS, no look-up table | `F/wsinclude/wsepwm.c:93-141`; UG p43 |
| phase command | CAN field = 10 (φ + 90), 0 ... 1800 → φ = −90 ... +90 "deg", 0.1 resolution in the GUI | `F/wsinclude/wscan.c:122`, `:150`, `:157`; `wsdefine.h:49-50`; UG p46 |
| phase to timer | φ ≥ 0: TBPHS = TBPRD x φ / 90 on ePWM3/4; φ < 0: secondary action qualifiers inverted and TBPHS = TBPRD (1 − \|φ\| / 90) | `F/wsinclude/wsepwm.c:105-140` |
| **what one GUI degree is** | the up-down period is 2 TBPRD counts, so TBPHS = TBPRD x φ / 90 is a shift of **2φ degrees of the switching period**; ±90 GUI degrees = ±180° (calculated) | as above; `board.c:783`, `:859`, `:896` |
| check against the UG test | lossless SPS, 800 / 800 V, n = 1 (not published, inferred from the test point), 7.5 µH (UG Table 9 p52), 100 kHz: 11.5 kW at 5°, 22.4 kW at 10° (calculated). UG Table 11 (p53) lists 2.5-5.0° for 10-23 kW, UG Fig. 55 shows 10.0° at 22.7 kW: both agree once the GUI value is doubled | UG p52-54 |
| phase resolution | 1 TBCLK = 8.33 ns = 0.3° of the period = 0.15 GUI degrees, coarser than the GUI's 0.1° (calculated) | `wsepwm.c:111`, `:125` (integer TBPHS) |
| update | from the 1 s background loop, at most once per second: TBPRD, CMPA, dead band (shadow loading disabled), action qualifiers and TBPHS rewritten one register after another while the PWM runs; **no slew limit, no volt-second-balanced (split) update**, a sign change inverts ePWM3 and ePWM4 in two separate writes | `F/CRD60DD12N-GMB_main.c:90-94`; `wsepwm.c:93-141`; `board.c:805-809` |
| start | switching begins at whatever phase is set (default 0°) the moment Logic Enable arrives; no idle band, no soft start, no ramp | `F/wsinclude/wscan.c:131-137`; `wsdefine.h:40` |
| power reversal | by typing a negative phase (sign change as above) | `wsepwm.c:120-134` |
| flux balance / DC offset | none; no blocking capacitor on the board (README) and the transformer current is not converted | `F/CRD60DD12N-GMB_main.c:124-126` |
| light load / ZVS | none | source |
| parallel branches | none | source |

## 4. Protection and the edge cases

### 4.1 Fault classes

| fault class | detection | threshold / latency | reaction | reset | evidence |
|---|---|---|---|---|---|
| desaturation, driver UVLO, bias power-good (each of 8 switches) | UCC21710 DESAT with soft turn-off; on the control board each channel's error line = 4-input AND of the driver FLT, RDY and the inverted bias power-good (SN74HCS21) | driver values (not in firmware) | the faulted switch turns itself off; what the other 7 do depends on the routing of 4.2 | CAN reset: logic disabled, RST/EN low 100 µs → high → 100 µs, trip-zone flags cleared, **all drivers left disabled** until a new Logic Enable | UG p39 §3.2.6; CS sheets "FAULT LOGIC (P)/(S)"; `F/wsinclude/wscan.c:131-137`; `F/wsinclude/wsgpio.c:448-453`, `:949-958` |
| trip-zone interrupt (one-shot sources only) | ePWM1-4 TZ ISR | - | gate-drive buffer disabled (`GATEDRIVE_DISABLE` high) and red LED; TZ flag left set | as above | `F/wsinclude/wsepwm.c:154-169` |
| debugger halt / clock failure | one-shot sources OSHT4-6 enabled on all four ePWM; on C2000, TZ4-TZ6 are the eQEP-error, clock-fail and emulation-stop signals (inferred, TRM not on file) | hardware | all outputs low | as above | `board.c:812`, `:850`, `:887`, `:924` |
| over-current (analog) | none: `DEFAULTOVERCURRENT` 55 A is defined and never used; ADC post-processing limits disabled (4095 / 0) | - | - | - | `wsdefine.h:39`; `board.c:511-518`, `:531-536` |
| over / under-voltage, over-temperature | none (telemetry only) | - | - | - | source |
| communication loss | none; parameters persist; commands are read once per second | - | - | - | `F/CRD60DD12N-GMB_main.c:85-94` |
| power-up | gate-drive buffer disabled (initial value 1), driver RST/EN low, all drivers disabled again after the offset calibration | - | - | - | `F/CRD60DD12N_GMB.syscfg:352-357`, `:381-391`; `F/CRD60DD12N-GMB_main.c:76` |

### 4.2 Which driver fault stops which PWM (decoded from the SysConfig file and `board.c`)

| fault line | GPIO | route | effect on the 8 PWM outputs |
|---|---|---|---|
| 1P, 2P, 3P | 25, 26, 27 | input X-BAR 1-3 → TZ1-TZ3 → one-shot on ePWM1-4 (outputs low) + TZ interrupt | **all 8 off, latched**, buffer disabled by the ISR |
| 4P | 28 | input X-BAR 4 → ePWM X-BAR TRIP4 (inverted) → DCAH / DCAL of ePWM1-4 → DCAEVT1 "DCAH high"; an output action (A forced low) is configured only on ePWM2, and DCAEVT1 is not a one-shot source | ePWM2 A held low while the line is low; **not latched, no interrupt**; ePWM1/3/4 keep the reset-default action (not set in `board.c`) |
| 1S | 29 | input X-BAR 5 → TRIP5 (inverted); no ePWM selects TRIPIN5 | none |
| 2S | 30 | input X-BAR 6 → TRIP7 (inverted); no ePWM selects TRIPIN7 | none |
| 3S, 4S | 41, 44 | input X-BAR 7, 8 only | none |

Evidence: `F/CRD60DD12N_GMB.syscfg:213-298` (ePWM trip settings), `:300-318` (ePWM X-BAR), `:393-423` (fault GPIOs),
`:435-464` (input X-BAR); `F/CPU1_FLASH/syscfg/board.c:810-817`, `:847-855`, `:885-892`, `:922-929`, `:943-960`;
`board.h:442-456`, `:468-490`. Confidence: high for the register settings; medium for the consequences, which use the
C2000 conventions (TZ1-3 = input X-BAR 1-3; DCAEVT1 latches only when selected as a one-shot source) from TI manuals
not on file. **This contradicts UG p39** ("The default firmware will quickly disable all the other gate drivers using
the C2000 trip zone hardware functionality"): for a fault on primary switch 4 or on any secondary switch, only the
faulted channel turns off (its own driver) and the controller keeps switching the other seven until the user reads the
1 s status frame and resets. A full bridge with one switch held off applies a unipolar voltage to a transformer with no
blocking capacitor and no flux control (inferred consequence).

### 4.3 Further edge cases in the code

1. **Bounds are register-type, not physical.** Commands are clamped (silently) to 10-100 kHz, 100-2000 ns and ±90 GUI
   degrees (`wsdefine.h:45-50`; `F/wsinclude/wscan.c:97-103`, `:148-150`). The GUI limits the frequency to 80-100 kHz
   (UG p46), the firmware accepts 10 kHz: SPS power scales with 1 / f, so a given phase at 10 kHz drives ten times the
   current it drives at 100 kHz (calculated). The phase limit allows the region past the SPS maximum-power point (90° of
   the period) up to 180°.
2. **Offset calibration at boot without a plausibility check.** The two DC current sensors are averaged 5 times and the
   difference to code 2048 is written to the ADC post-processing offset (hardware range −512 ... +511 counts) - with
   the comment "Comment if system starts with non-zero current" (`F/CRD60DD12N-GMB_main.c:62-66`;
   `F/wsinclude/wssensor.c:614-629`, `:321-345`). A sensor fault or current flowing at boot becomes the zero. The gain
   line uses its own mid-code: current = 0.163204610 x code − 333.881 A (`wssensor.c:236-238`), so a nulled channel reads
   +0.36 A (2048 x 0.1632 − 333.88, calculated). The source notes the method applies to the AC sensors too (`main.c:64`).
3. **Unused constants that disagree with the code.** `VOLTAGEGAIN` 0.260978, `CURRENTGAIN` 0.163106 and
   `CURRENTOFFSET` 303.46 (`wsdefine.h:66-68`) are not used; the code uses 0.260978022 V and 0.163204610 A / −333.881 A
   (`wssensor.c:178-180`, `:236-238`).
4. **The NTC chain has no window check.** The module NTC (switch-2 driver AIN → APWM, filtered into the ADC, UG p39) is
   converted by a 5th-order polynomial in ADC counts (`wssensor.c:397-403`); no open / short detection, no trip.
5. **Exceptions and the watchdog** - section 2: a trap leaves the bridges switching at the last phase.

## 5. CAN and GUI interface

| item | value | evidence |
|---|---|---|
| bus | one CAN (non-isolated on the board; an isolated adapter is required), 1 Mbit/s: bit timing (11, 0, 5, 2, 2) at 120 MHz = 12 x 10 TQ (calculated); UG Table 8 1,000,000 | `board.c:695`; UG p42, p46 |
| frames | standard IDs: control RX 0x000 and status TX 0x000; temperatures 0x0FF, currents 0x0FE, voltages 0x0FD; RX 0x0FC declared, unused; status and telemetry once per second | `board.h:323-328`; `board.c:707-767`; `wsdefine.h:74-80` |
| command (8 bytes) | f_sw [kHz] 8 bit; dead time [ns] 12 bit; phase 12 bit = 10 (φ + 90); GPIO out 1/2; Logic Enable; Reset Fault(s) | `F/wsinclude/wscan.c:116-158` |
| status | the applied f_sw, dead time, phase; logic state (buffer and all driver enables); 8 fault bits; GPIO in/out | `wscan.c:31-83` |
| telemetry | DC voltage prim/sec [V]; spare ADC 1/2 [counts]; DC current prim/sec [A]; module NTC prim/sec and board ambient [K] | `F/CRD60DD12N-GMB_main.c:104-121`; `wscan.c:171-249` |
| GUI | python-can (PCAN, NI-CAN, NI-XNET); inputs switching frequency, dead time, phase, Logic Enable, two GPIO; on a fault the Send button is disabled until Reset Fault(s); user scaling for two spare ADCs; CSV logging; out-of-range inputs are clamped with a message ("Provided value exceeds maximum. Setting to maximum value of ...") | UG p46-47; GUI script strings |

Command semantics: **Logic Enable** = gate-drive buffer enabled and both bridges' RST/EN high; **Reset** = logic
disabled, drivers reset, trip zones cleared, drivers left disabled (a fresh enable is required). There is no stop ramp.

## 6. What could not be read

* GUI numeric limits: the PyInstaller code object was not disassembled; the UG's ranges were used.
* The power and control schematics give the fault-logic structure but no driver-board values beyond the BOM; the
  driver's DESAT threshold and timing are UCC21710 datasheet values (not in firmware).
* The left-over `CRD25DA12N-FMC CLA Firmware.out` was not examined (another design).

## 7. What we take from it

* **Resolves a note in our model.** `sim/dab_devices.py` (CRD test point) calls UG Table 11's 2.5-5.0° "inconsistent"
  with Fig. 55 and does not use it. The firmware explains it: one GUI degree is two degrees of the switching period, so
  5.0° in the GUI is the 10.0° of Fig. 55. Our use of Fig. 55 stands; the comment's reason can be updated. The same
  test point carries `tdead` 200 ns, while the firmware programs 166.7 ns at the PWM (calculated) - a few watts of
  body-diode loss in the CRD calibration (REFERENCE-LESSONS §6.6).
* **Per-channel fault = FLT ∧ RDY ∧ power-good** in hardware is cheap and catches UVLO and a dead bias supply as well as
  DESAT; our DR-03 trip chain already includes driver RDY/UVLO and +24 V UV.
* **Reset leaves the drivers disabled** until a new enable - the right semantics; ours (latch released only by a
  clean clear, then a start) is equivalent.
* **Boot-time offset null of current sensors** - the method is right, the missing plausibility window is not; it maps to
  a gap in our DAB (transformer-current offset before the flux loop is trusted, R-WS-5).
* **Negative lessons:** controller-routed trip paths need a per-line self-test and a routing read-back (R-WS-7); bounds
  must be physical (R-WS-1); phase interfaces must be in degrees of the switching period with the design clamp;
  exception handlers must force the trip (R-WS-6); communication loss needs a defined reaction (R-WS-2).
* **Cheap hardware trip sources:** listing the clock-fail and emulation-stop one-shot sources on every ePWM costs
  nothing (R-WS-6).

## 8. Not applicable to our devices (reference points only)

| item | Wolfspeed value | why it does not transfer |
|---|---|---|
| dead time | 200 ns commanded / 166.7 ns programmed (`wsdefine.h:38`; `wsepwm.c:47-48`) | CBB011M12GM4T + UCC21710 at R_G,on 1 Ω / R_G,off 0 Ω (UG p53); our DAB uses a hardware guard 69-188 ns plus a ZVS look-up table on our own devices |
| driver reset pulse | RST/EN low 100 µs (`wsgpio.c:448-453`) | UCC21710-specific |
| over-current constant | 55 A, unused (`wsdefine.h:39`) | not a design value of anything |
| sensor scaling | 0.163 A / count (LEM HO 120-NP, ±334 A span), 0.261 V / count (AMC3330 dividers) (`wssensor.c:178-180`, `:236-238`) | board-specific; ours has its own chain (HOB 130-P, AMC3330 on DAB60) |
| NTC polynomial | `wssensor.c:397-403` | CBB011M12GM4T NTC + UCC21710 APWM + RC filter |
| frequency bounds | 10-100 kHz (firmware), 80-100 kHz (GUI) | tied to the CRD transformer (7.5 µH leakage) and module; our 100 kHz is fixed |
| transformer | 7.5 µH leakage, 329 µH magnetising, 1:1 inferred (UG Table 9) | ours: n = 11:12, L = 6.5 µH, L_m 150 µH (D-014) |
