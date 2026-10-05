# CRD200DA23N-GMA firmware - what the source code shows

Package: `crd200da23n-gma_firmware-gui.zip` (supplied by the owner on 2026-10-05, row in `docs/SOURCES.csv`), unpacked in
`firmware/`; its two inner zips are unpacked in place. Traces to REQUIREMENTS.md **AC-01 / AC-02** (the PCS is designed
to the depth of the DC/DC; this is the reference named for PCS-P125 in decision D-053), **SRC-7** (hardware / firmware
balance) and **S-7** (reference library). Written 2026-10-05. **Static reading only:** nothing was built, flashed, run or
emulated; the GUI executable was never started. Nothing here is bench-validated. The owner's framing applies: Wolfspeed's
devices are not ours (we use our own SiC or IGBT selection), so every device-specific value below is a reference point,
not a design value for us (section 7).

Labels: **read** = taken from the source at the file:line given · **calculated** = derived by us (formula given) ·
**inferred** = depends on TI reference-manual or datasheet knowledge that is not on file.
Paths: `F/` = `firmware/CRD200DA23N-GMA Firmware [v1.0.0]/`; `UG` = `crd200da23n-gma_user-guide_prd-09623.pdf`
(PRD-09623 Rev. 2, Feb 2026), cited by PDF page.

Commands (read-only): `unzip -l` / `unzip -q` of the inner zips, `cat -n` / `grep -n` of every `.c`, `.h` and `.cmd` file
outside the TI driver library, `strings` on the `.map` and `.ccsproject`, `pdftotext -layout` of the UG and of the XM3
controller schematic on file (`../crd250da12e-xm3/design-files/Controller/XM3 CONTROLLER V1R2 SCHEMATIC.pdf`), and a
short Python reader of the PyInstaller table of contents of the GUI executable (stdlib `struct` / `zlib` only; the main
script's code object was decompressed and its string constants listed, nothing was executed).

---

## 1. What the package contains

| file | bytes | what it is |
|---|---|---|
| `crd200da23n-gma_firmware-gui.zip` | 10,487,852 | outer zip, two members dated 2025-11-10 |
| `firmware/CRD200DA23N-GMA Firmware [v1.0.0].zip` | 808,646 | Code Composer Studio project: 7 application `.c` files + headers, TI device files, C2000Ware driverlib (sources and `.lib`), linker file `2837xD_FLASH_lnk_cpu1.cmd`, a built `CPU1_FLASH/CRD200DA23N-GMA.out` (148,204 B, linked 2025-11-10 with TI linker v22.6.1, `.map`) |
| `firmware/CRD200DA23N-GMA GUI [v1.0.0].zip` | 9,706,313 | `GMA Inverter CAN Interface.exe` (PyInstaller, Python 3.9, python-can 3.3.4 per the licence file) + licence text |

Application source, lines: `main.c` 1304, `GATEDRIVER.c` 396, `CANSetup.c` 292, `TEMPERATURE.c` 286, `Analog.c` 145,
`Current.c` 125, `Voltage.c` 96, headers 296. Header comment: "V1.0 (October 27, 2025) - Initial release. Based on XM3
controller code V1.3" (`F/main.c:19-20`). Purpose stated in the source: "basic code required to operate the reference
design as an **open-loop inverter** ... designed as a starting point only" (`F/main.c:8-15`; UG p37 "open-loop
three-phase sine pulse width modulation (SPWM)").

## 2. Controller and timing

| item | value | evidence |
|---|---|---|
| MCU | TMS320F28379D on the LAUNCHXL-F28379D LaunchPad, CPU1 only | UG p35; `.ccsproject` target `TMS320F28377D` family |
| clocks | SYSCLK 200 MHz; ePWM time base 100 MHz (10 ns per count, `100e6` in the period formula) | `F/device/device.h:120`, `:145`; `F/main.c:489` |
| PWM | up-down count, TBPRD = 100e6 / f_sw / 2: **2500 counts at the 20 kHz default** (calculated); CMPA shadow load at counter zero | `F/main.c:187`, `:489`, `:505`, `:514-516` |
| switching frequency | 20 kHz default; set over CAN in whole kHz (8-bit field) | `F/main.c:187`, `:918`, `:936` |
| carriers of the three phases | phase B TBPHS = 2/3 TBPRD, phase C = 4/3 TBPRD (the comment says 1/3 and 2/3); ePWM1 is the sync master | `F/main.c:491`, `:602`, `:702`, `:493` |
| control interrupt | ePWM1 at counter zero, **every carrier period** (20 kHz); it only advances the three sine references | `F/main.c:575-577`, `:403-421` |
| everything else | background loop with a fixed **1 s** delay: CAN receive, fault poll, telemetry, ADC | `F/main.c:276` |
| ADC | 12 bit, prescaler /4, **software-forced once per second**, sample window 15 SYSCLK; no PWM-synchronous sampling, no oversampling | `F/Analog.c:29-31`, `:74-87`; `F/main.c:319-348` |
| computation delay | not handled (open loop); new CMPA takes effect at the next counter zero | `F/main.c:514-516` |
| dead time | **ePWM dead-band only**, rising and falling delay = `DEAD_TIME` counts; default 40 = **400 ns**; the code relies on the dead band alone (no interlock on the driver board is mentioned in the UG) | `F/main.c:188`, `:542-551` |
| watchdog | disabled in `Device_init()` and never enabled | `F/device/device.c:62-64` |
| floating point | FPU32, CLA support compiled in but unused, relaxed FP mode | `F/.cproject:36-37`, `:136` |

## 3. Modulation and control

There is **no closed loop**: no current loop, no PLL, no DC-link loop, no grid forming or droop. The firmware is an
open-loop three-phase sine generator for an inductive test load.

| item | value | evidence |
|---|---|---|
| modulation | plain sine-triangle SPWM: duty = (MF sin θ + 1) / 2, CMPA = duty x TBPRD; **no zero-sequence injection** | `F/main.c:813`, `:825` |
| angle step | θ += 2π f_fund / f_sw per carrier period, wrapped at 2π; the ratio f_sw / f_fund is an **integer division** of two `uint16_t` | `F/main.c:798`, `:814-820` |
| frequency error of that division | 20000 / 300 = 66 → 303.03 Hz instead of 300 Hz (calculated) | `F/main.c:798` |
| modulation factor | MF = ID / 1000 with a 10-bit field: 0 ... 1.023; **no clamp** (above 1.0 the compare exceeds TBPRD and pulses drop) | `F/main.c:919`, `:937` |
| linear output | V_LL,rms ≤ 0.612 MF V_dc (√3 / (2√2), calculated) | - |
| defaults | MF 0.01, 300 Hz, 20 kHz, 400 ns, logic disabled | `F/main.c:187-196` |
| fundamental limit | 500 Hz (`MAXFUNDAMENTAL`) - the only bound in the firmware | `F/main.c:47`, `:944-946` |

Test use (UG p43-45): 1000 V bus (30 kW supply), a 129 µH wye inductor with its neutral on the DC-link midpoint, 167 A rms,
20 kHz, 400 ns, 300 Hz, R_G(ext) 0 Ω - the supply only covers the losses (circulating reactive power).

## 4. Protection and the edge cases

| fault class | detection | threshold / latency | reaction | reset | evidence |
|---|---|---|---|---|---|
| desaturation (each switch) | on the CGD1700HB2M-UNA driver board (UCC21710), soft turn-off of the tripped channel; fault output differential FAULT-P/N | driver-board values (blanking capacitors 120 pF in this design, UG p25); not in firmware | tripped channel off; the three phase faults are ANDed in hardware into GLOBAL-FAULT → GPIO15 → input X-BAR 1 → **TZ1 one-shot: all six PWM outputs forced low** (hardware, no software in the path) | CAN `RESET` bit: RST/EN low 2 µs, high 1 µs, low; TZ one-shot flags cleared; FAULT1-3 cleared | UG p19 Table 7, p26 §3.2.2; `F/GATEDRIVER.c:73-77`, `:313-319`, `:385-396`; `F/main.c:556-568`, `:951-969` |
| fault status for the user | GPIO6/7/8 (per phase) and GPIO15 (global) polled | once per **1 s** | logic enable cleared, drivers disabled (RST/EN low), FAULT1-3 latched for the status frame | as above | `F/main.c:297-307` |
| over-current (analog) | none - the ±800 A current inputs are read once per second for telemetry only | - | - | - | `F/Current.c:31-33` |
| DC over / under-voltage | none (telemetry only, 0-1710 V scale) | - | - | - | `F/Voltage.c:46-48` |
| AC voltage / frequency | none (telemetry only, ±1616 V scale) | - | - | - | `F/Voltage.c:30-32` |
| over-temperature | none - module NTCs and the board NTC are telemetry only | - | - | - | `F/TEMPERATURE.c:152-161`, `:277-286` |
| watchdog / exceptions | watchdog off; the TI default, illegal-operation and NMI handlers are `ESTOP0; for(;;)` - **the ePWM keeps running with the last compare values** while the CPU spins | - | - | power cycle | `F/device/device.c:62-64`; `F/device/driverlib/interrupt.h:138-165`, `:187-194`, `:215-223` |
| communication loss | none: the converter keeps the last parameters indefinitely; commands are read once per second | - | - | - | `F/main.c:276-292` |
| precharge, contactors, synchronisation, grid loss | not on the board and not in the firmware | - | - | - | UG p1 caveat; source |

Edge cases visible in the code (these are what the owner asked about; each is a lesson, not a model):

1. **Parameters from the bus are trusted.** Only the fundamental is clamped (500 Hz). A switching-frequency field of 0
   divides by zero in `100e6/SWITCHING_FREQ/2` and in `SWITCHING_FREQ/FUND_FREQ`; a field above 65 kHz overflows the
   `uint16_t` (66 kHz → 464 Hz, calculated); a dead-time field of 0 gives **no dead time**; MF up to 1.023 is accepted
   (`F/main.c:118-139`, `:914-946`). The GUI enforces 1-50 kHz, MF 0-1000, 100-2000 ns, 1-500 Hz and refuses invalid
   packets ("Invalid ...; Invalid packet; not sent", strings of the GUI script) - **the protection lives in the PC tool,
   not in the device** (UG p38).
2. **Reset and enable in one frame.** The reset branch clears the faults and then `GD_ALL_OCControl(LEN1)` acts on the
   same packet, so a frame carrying RESET = 1 and LEN = 1 re-enables the drivers at once; the UG asks the operator to
   disable the logic and lower MF first, the firmware does not enforce it (`F/main.c:951-974`; UG p39).
3. **Open cable reads healthy (inferred).** The fault inputs use internal pull-ups and active-low logic
   (`F/GATEDRIVER.c:61-76`, `:333-339`); on the XM3 controller the FAULT pairs arrive through AM26LV32E receivers and an
   SN74LVC1G11 AND (controller schematic V1R2). An RS-422 receiver of that type outputs high on open inputs (datasheet
   not on file), so an unplugged gate-driver cable would read "no fault". On the driver side RESET/EN has an on-board
   10 kΩ pull-up - "pull up or leave floating to enable" (UG p19 Table 7): a floating enable means **enabled**.
4. **Thermistor without plausibility.** The module NTC reaches the controller as the UCC21710 AIN→APWM duty (10-88 %
   at 400 kHz, UG p32), measured by eCAP high time, averaged over 10 periods and passed through a 5th-order polynomial
   (`F/TEMPERATURE.c:82-100`, `:175-180`, `:209-211`). An open or shorted NTC, or a missing signal (buffer left at 0
   → duty 0 → −174,667 °C, calculated), is never flagged.
5. **Buffer overrun.** The eCAP buffers have 10 entries but the index wraps only when it exceeds 10, so entries 10 and 11
   are written past the end of each array (`F/main.c:46`, `:108-113`, `:1053-1057`, `:1078-1082`, `:1103-1107`).
6. **The fault response is hardware, the bookkeeping is slow.** The trip zone acts within the gate logic; the software
   sees the fault up to 1 s later. The TZ interrupts are registered but never enabled in the PIE (`F/main.c:169-171`,
   `:225-229`), so nothing in software reacts faster than the 1 s loop.
7. **Safe defaults at power-up.** Logic enable 0 and the drivers held in reset (RST/EN low) until a CAN command; MF
   0.01; the ±15 V sensor rails are switched on only after the driver reset (`F/main.c:183-184`, `:194-202`,
   `:240-253`). The PWM itself free-runs from boot (`F/main.c:206-220`).

## 5. CAN and GUI interface

| item | value | evidence |
|---|---|---|
| buses | CAN-A (isolated, DB9 J9) and CAN-B (not isolated), both serviced identically | UG p15; `F/main.c:281-292`, `:313-314`, `:389-396` |
| bit rate | 1 Mbit/s: bit-timing (prescaler 19, TSEG1 5, TSEG2 2) at the 200 MHz CAN clock = 20 x 10 TQ (calculated); UG Table 10 bit rate 1,000,000 | `F/CANSetup.c:95`, `:200`; UG p37 |
| frames | standard 11-bit: command RX on ID 0x000, status TX on ID 0x000, temperatures 0x0FF, currents 0x0FE, voltages 0x0FD; once per second | `F/CANSetup.c:118-181` |
| command packet (8 bytes, bit-packed) | f_sw 8 bit [kHz]; MF x 1000 10 bit; dead time 12 bit [ns]; fundamental 10 bit [Hz]; power-supply enables PSEN1-3 (decoded, "NOT USED"); logic enables LEN1-3 (only LEN1 used, it enables all drivers); FAULT1-3; RESET | `F/main.c:914-931`; `F/CANSetup.c:14-28` |
| status packet | echo of the applied f_sw, MF, dead time, f_fund, PSEN, LEN, FAULT1-3, RESET | `F/main.c:882-902` |
| telemetry | module NTC A/B/C and board NTC [K]; currents A/B/C/EXT [A]; voltages A/B/C and DC [V]; 16-bit signed, big-endian | `F/main.c:355-382` |
| GUI | python-can; inputs f_sw 1-50 kHz, MF 0-1000, dead time 100-2000 ns, f_fund 1-500 Hz, Logic Enable toggle, Reset toggle; a raw-packet log window | UG p37-39; GUI script strings |

Command semantics: **logic enable** = all six drivers enabled (RST/EN high) or disabled; **reset** = driver reset pulse +
trip-zone clear, auto-cleared after sending; there is no stop / ramp-down - disabling the logic stops switching at once.

## 6. What could not be read

* The GUI is a compiled PyInstaller executable: only its table of contents and the string constants of the main script
  were listed; numeric limits inside the code object were taken from the UG instead.
* The control board is the XM3 controller with modified dividers (UG p30-31); its exact V1.1 schematic is not in this
  package - the XM3 V1R2 schematic of the CRD250DA12E-XM3 folder was used for the fault-receiver path (inferred match).
* No closed-loop code exists, so loop bandwidths, PLL and DC-link figures do not exist to be compared.

## 7. What we take from it

* **The protection split matches SRC-7.** Desaturation and soft turn-off live in each gate driver; the system-wide stop
  is the controller's own trip zone fed by an AND of the driver fault lines, with no software in the path
  (`F/GATEDRIVER.c:73-77`; `F/main.c:556-568`). Our PCS-CTL latch and the DAB's all-driver DESAT injection (DR-03) are
  the same idea taken further.
* **A bench method worth copying:** open-loop SPWM into a wye inductor with its neutral on the DC midpoint runs the stage
  at rated current from a supply that only covers losses (UG p43-45). A factory burn-in / end-of-line mode of this kind
  for PCS-P125 (all trips active, service mode only) is a candidate - REFERENCE-LESSONS §6, R-WS-12.
* **Gate-driver NTC via APWM read by eCAP** (period and high time, 10-period average) is cheaper and less noisy than an RC
  filter into the ADC - worth using where our drivers carry the module NTC on AIN/APWM, **with** a duty-window check
  (R-WS-11).
* **Negative lessons, adopted as requirements in REFERENCE-LESSONS §6:** bus parameters must be validated in the device
  (R-WS-1); exception handlers must force the trip (R-WS-6); a fault clear must not re-enable in the same frame
  (already our rule); communication loss needs a defined reaction (R-WS-2).

## 8. Not applicable to our devices (reference points only)

| item | Wolfspeed value | why it does not transfer |
|---|---|---|
| dead time | 400 ns default (`F/main.c:188`), tested 400 ns (UG p45) | set for CAB5R0A23GM4T (2300 V, 5 mΩ) with the CGD1700HB2M-UNA at R_G(ext) 0 Ω; ours follows our own switch and driver (PCS: 300 ns firmware floor, 185-510 ns at the gates) |
| DESAT tuning | blanking capacitors raised from 56 pF to 120 pF "to accommodate the CAB5R0A23GM4T and the high DC-bus voltage" (UG p25) | module- and bus-specific |
| driver reset timing | RST/EN low > 1000 ns, reset on the rising edge (UG p19; `F/GATEDRIVER.c:306-319`) | UCC21710-specific; our drivers have their own reset rules |
| NTC transfer | duty 10-88 % and the 5th-order polynomial (`F/TEMPERATURE.c:209-211`; UG p32) | CAB5R0A23GM4T NTC + UCC21710 bias network |
| sensing scales | ±800 A (external sensor option), ±1616 V AC, 0-1710 V DC (`F/Current.c:32`; `F/Voltage.c:31`, `:47`) | 1500 V-class board with external sensors |
| switching frequency | 20 kHz default, 50 kHz GUI maximum | 2300 V module losses at a 1500 V bus; PCS-P125 runs 32 kHz on its own devices |
