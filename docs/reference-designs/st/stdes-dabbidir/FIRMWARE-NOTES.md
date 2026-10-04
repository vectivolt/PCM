# STSW-DABBIDIR firmware - what static inspection of the binary shows

Package: `stsw-dabbidir.zip` and the unpacked `stsw-dabbidir/` (this folder). Traces to REQUIREMENTS.md **REF-1**
(package filed and used) and **DAB-08** (our DAB control cross-checked against ST STSW-DABBIDIR).
Written 2026-10-04. **Static inspection only: the binary was never executed, flashed or emulated.** Nothing here is
bench-validated and no ST document (UM3198, DB4879, TN1435) was available while this was written - see section 4 for
what the binary cannot tell.

How to read the labels:

* **decoded** = read from instructions, literal pools, DWARF or ELF headers.
* **calculated** = derived by us from decoded values (formula given).
* **inferred** = depends on HAL / reference-manual knowledge that is not in the binary.
* Confidence: **high** = value read directly and its role is unambiguous in the code; **medium** = value read directly,
  role or unit depends on HAL / RM knowledge; **low** = indirect.

Method (all read-only): `arm-none-eabi-readelf / nm / objdump / objcopy / size`, `strings`, Python with
pyelftools 0.33 (DWARF 4), and throw-away scripts that annotated the disassembly with literal-pool values and
emulated float32 arithmetic. Scratch outputs (symbol dumps, a 650 kB disassembly, JSON dumps) were deleted afterwards.
The build account name that appears inside the embedded Windows paths is replaced by `<user>` here.
Commands (no execution of the target):

```
B=docs/reference-designs/st/stdes-dabbidir/stsw-dabbidir
arm-none-eabi-readelf -h -A -S -l -W $B/STSW_DABBIDIR.out            # header, ARM attributes, sections
arm-none-eabi-nm -S -n $B/STSW_DABBIDIR.out                          # 265 named functions / variables
arm-none-eabi-objdump -d -M force-thumb $B/STSW_DABBIDIR.out         # disassembly ($t / $d mapping symbols are present)
arm-none-eabi-objcopy --dump-section .comment=comment.bin $B/STSW_DABBIDIR.out tmp.out   # IAR compiler / linker command lines
arm-none-eabi-objcopy -I ihex -O binary $B/STSW_DABBIDIR.hex hex.bin                      # then cmp with the ELF load segment
arm-none-eabi-objdump -D -b binary -m arm -M force-thumb --adjust-vma=0x08000000 sim.bin  # the .sim image (26-byte header removed)
.venv/bin/python (pyelftools 0.33): compile units, DW_TAG_subprogram, DW_TAG_variable (DW_OP_addr), struct / enum / typedef layouts
```

Method of decoding: every `vldr` / `ldr` literal in the application functions was resolved against the ELF bytes and printed as hex / float; float32 arithmetic was emulated with numpy to reproduce truncations (period 54,400, dead time 136, thresholds); the call graph was built from `bl` / `b` targets and literal-pool function pointers; the `.hex` / `.sim` comparison normalised addresses before diffing.
`pyelftools` was added to `requirements.txt`.

---

## 1. What the three files are

| file | bytes | what it is |
|---|---|---|
| `stsw-dabbidir.zip` | 232,325 | the ST download. One folder `STDES-DABBIDIR_FW/` holding one inner zip, `STDES-DABBIDIR_FW_e4cba0973e5fa51be94953244d34025dfd22538f.zip` (232,699 B, name ends in 40 hex digits - probably a git commit id of ST's repository, inferred). The inner zip holds exactly the three files below; the unpacked copies are byte-identical to it (checked in scratch). |
| `STSW_DABBIDIR.hex` | 112,075 | Intel HEX of the flash image: 39,827 bytes from 0x08000000 to 0x08009B92 (2,490 data records, extended-linear-address 0x0800, start-address record 0x080099D9). Byte-identical to the load segment of the ELF (`objcopy -O binary` of both, `cmp`). |
| `STSW_DABBIDIR.out` | 738,952 | ELF32 ARM executable, statically linked, DWARF 4 debug info plus IAR sections, not stripped (2,248 symbols). Linked from IAR objects; no source code. |
| `STSW_DABBIDIR.sim` | 39,730 | IAR "simple-code" flash image (magic `7F 49 41 52` = `\x7fIAR`, 26-byte header with load address 0x08000000 and length 0x9B13 = 39,699 B, the image, 5 trailer bytes). **It is NOT the same build as the .hex/.out** - see 1.5. Identification of the container is by magic and extension (medium). |

SHA-256 (the same values are in `docs/SOURCES.csv` for the zip):

```
6cac7582ea4feb3fdbdf7e129129a439f021b484dd91552a3368792be226765e  stsw-dabbidir.zip
a847b33acb9bd75b7cfea66bb5b5b3049ac536154b46f587eb106333472c548f  STSW_DABBIDIR.hex
19586e88afa4d0ccfd85c3f77c77169275959facc8a01e5aa04a8bf5cb38423d  STSW_DABBIDIR.out
85e1ad941f3ed87d08df74755f9b62ffa0bb8cbc60890d6b9188054fdf6264ab  STSW_DABBIDIR.sim
```

### 1.1 Target MCU and core (decoded)

| item | value | evidence |
|---|---|---|
| core | Arm **Cortex-M4F**: `Tag_CPU_arch v7E-M`, Thumb-2, `Tag_FP_arch VFPv4-D16`, `Tag_ABI_HardFP_use: SP only` (FPv4-SP-D16, single-precision FPU); compiler options `--cpu=Cortex-M4 --fpu=VFPv4_sp` | `readelf -A`, compiler command line in `.comment` |
| family | **STM32G474** (G4 category 4): compile define `STM32G474xx`, startup file `startup_stm32g474xx.s`, linker config `stm32g474xx_flash.icf`, HRTIM1 / ADC1-2 / DAC1-2 / DMA1 / FPU in use, and the vector table has 118 words = 16 system + 102 IRQs, which is the STM32G474 layout (inferred from the CMSIS `IRQn_Type` list: the used slots - DMA1 ch1-5, ADC1_2, TIM2/3/6/7 - land on the matching peripherals; unused IRQs point at 4-byte dummy stubs, the AES slot is empty) | `.comment`, vector table |
| exact part | the binary only says `STM32G474xx`; the flash size / package letter is not in it. `README.md` (from web summaries) says STM32G474RE; the image (38.9 KiB flash, 4.2 kB RAM including the 1 kB stack) fits any G474. | |
| clock tree | HSI16 (no crystal) -> PLL: M = /4, N = x85, R = /2 -> **170 MHz** SYSCLK = HCLK = PCLK1 = PCLK2; flash latency 4, regulator boost; ADC12 kernel clock = SYSCLK | `SystemClock_Config` 0x080020EA, `HAL_ADC_MspInit` |
| entry | ELF entry 0x080099D9 = `__iar_program_start`; reset vector 0x080099F5 = start-up code that calls `SystemInit` (0x080098F8) then `__iar_program_start` -> `__iar_init_vfp` (FPU enable) -> `?main` -> `main` (0x08001F34) | disassembly |

### 1.2 Toolchain (decoded from `.comment`, which the IAR linker fills with every compiler and linker command line)

* **IAR Embedded Workbench for Arm 9.20.2**: `IAR ANSI C/C++ Compiler V9.20.2.320/W64 for ARM`, `IAR ELF Linker V9.20.2.320/W64 for ARM`
  (install path `C:\Program Files\IAR Systems\Embedded Workbench 9.0\arm`), DLib runtime (`__dlib_version 6`, full configuration
  `DLib_Config_Full.h`).
* Compiler: `--debug --endian=little --cpu=Cortex-M4 -e --fpu=VFPv4_sp -Ohs` (optimise for high speed), `-D USE_HAL_DRIVER -D STM32G474xx`.
* Linker: `--config EWARM/stm32g474xx_flash.icf --semihosting --entry __iar_program_start --vfe --redirect _Printf=_PrintfFullNoMb --redirect _Scanf=_ScanfFullNoMb`.
  `--vfe` (virtual function elimination) plus the section garbage collector is why modules that were compiled but never called are absent from the image (see 2.1).
* HAL: STM32G4xx HAL driver (HAL_TIM, TIM_Ex, GPIO, CORTEX, RCC, RCC_Ex, PWR_Ex, ADC, ADC_Ex, DAC, DAC_Ex, DMA, HRTIM, I2C, I2C_Ex, UART, UART_Ex, FLASH, FLASH_Ex) generated by STM32CubeMX (the `MX_*_Init` functions).
* The IDE project / target name is `STSW_DC2DCDAB` (`EWARM\STSW_DC2DCDAB\{Exe,Obj,List}`); the shipped files were renamed to STSW_DABBIDIR.

### 1.3 Build date (what is and is not known)

IAR does not embed a build time stamp and the image carries no version string. The only dates are the ZIP member times
(local time of the packager, zone unknown): `.hex` and `.out` **2022-11-30 17:46**, `.sim` **2022-11-29 10:46**, folder and inner zip 2022-11-30 17:48.
ST's product page (`https://www.st.com/en/embedded-software/stsw-dabbidir.html`) was tried once for the release number and date; the fetch timed out
(consistent with the access problem recorded in `README.md`), so **the package version and release date are unknown** and nothing here depends on them.

### 1.4 Memory map (decoded, `readelf -S`, `size -A`)

| section | address | bytes | content |
|---|---|---|---|
| `A0` | 0x08000000 | 472 (0x1D8) | vector table (initial SP 0x20001098, 117 vectors) |
| `P1 ro` | 0x080001D8 | 39,355 (0x99BB) | code and constants: `Font11x18` table 3,420 B at 0x080001D8, HAL_TIM / main / DPC code from 0x08000F34, HAL, interrupt handlers (0x080097FC), runtime support and the IAR init tables up to 0x08009B92 |
| `P2 rw` | 0x20000000 | 56 | initialised data (HAL tick variables, `pFlash`, `SystemCoreClock`, the font descriptor); the initialisers are IAR-packbits packed in 27 bytes at 0x08009B78 and were decoded (2.6) |
| `P2 zi` | 0x20000038 | 3,164 (0xC5C) | zero-initialised data: **every ST control variable lives here** (`DAB` 432 B at 0x20000440, `DAB_ADC` 20 B, `taTimeoutList` 56 B, HAL handles, 1 kB SSD1306 frame buffer) |
| `P2 ui` | 0x20000C98 | 1,024 | `CSTACK` (stack, ends at 0x20001098) |
| debug / IAR | - | `.debug_info` 221,998, `.debug_line` 149,090, `.comment` 104,203, ... | not loaded |

Flash image 39,827 B; static RAM 4,244 B (including stack). No heap section, no CAN / USB / Ethernet / FDCAN code.

**Consequence for parameter extraction:** only 56 bytes of RAM are initialised from flash, so the ST control parameters are **not** `.data` initial values:
they are immediates and literal-pool constants inside the initialisation functions (`main`, `DPC_*_Init`), which is where they were decoded.

### 1.5 The `.sim` is a different build, with an older time stamp (decoded by instruction-level diff)

The `.sim` image is 128 bytes smaller (39,699 vs 39,827 B); code addresses from the actuator functions (0x080042B4) onwards are lower, by 0x80 from
`DPC_PI_Init` on (so the vector table entries differ by 0x80). Comparing the two images instruction by instruction with addresses normalised, the application
code (0x08001F34-0x08004EC8) differs only in:

| item | `.hex` / `.out` (11-30) | `.sim` (11-29) |
|---|---|---|
| dead-time request | 4.0e-7 s -> 136 counts = **400 ns** | 6.0e-7 s -> 204 counts = **600 ns** |
| ADC trigger compare (`DPC_ADC_TrigSet`) | 40,800 counts (0.75 T) | 30,800 counts (0.566 T) |
| `DPC_ACT_OutEnable / OutDisable` | one `HAL_HRTIM_WaveformOutputStart/Stop` call per output | one call with the mask 0xFF |

Besides these, `DPC_ACT_Init`, `DPC_ACT_DAB_Conf_DeadTime` and `DPC_ACT_Calc_DeadTime` differ only in register allocation and literal-pool offsets (same operations).
Everything else compared (0x080001D8-0x08001F34 byte-identical; the HAL / runtime tail differs only by branch offsets) is the same.
The `.sim` has no symbols, so only this diff was possible. **All parameter values below are those of the `.hex`/`.out` build** unless stated.

---

## 2. Software structure

### 2.1 Source tree (from DWARF compile units, the line-number tables and the linker command line)

DWARF 4, 474 compile units (IAR emits one per function group) from 44 distinct source names; the full compile list in `.comment` has 54 compiler invocations.
Directories (build tree `C:\Users\<user>\Documents\GIT\FW\DPC_STM32\EVAL\`):

```
EVAL\
  STSW_STDES_DAB\                      ST's application project (IDE target STSW_DC2DCDAB)
    Src\   main.c adc.c dac.c dma.c gpio.c hrtim.c i2c.c tim.c usart.c stm32g4xx_hal_msp.c stm32g4xx_it.c
           system_stm32g4xx.c DPC_Application.c            (DPC_Application.c: compiled and linked, no code or debug info survives)
    Inc\   DPC_Application.h DPC_Application_Conf.h DPC_Lib_Conf.h main.h adc.h dac.h dma.h gpio.h hrtim.h i2c.h tim.h usart.h
           stm32g4xx_hal_conf.h stm32g4xx_it.h
    EWARM\ startup_stm32g474xx.s, stm32g474xx_flash.icf
    Drivers\STM32G4xx_HAL_Driver\{Src,Inc}, Drivers\CMSIS\...
  CONVERTER_LIBRARY_STM32\             ST "digital power control" (DPC) library, shared across converters
    Scr\   (sic - the directory is spelled "Scr")
           DPC_Actuator.c DPC_adc_converter.c DPC_Datacollector.c DPC_Faulterror.c DPC_FSM.c DPC_Loopctrl.c
           DPC_MISC_Display.c DPC_MISC_fonts.c DPC_Miscellaneous.c DPC_Pid.c DPC_Timeout.c
           (compiled and linked but removed entirely by the linker: DPC_Math.c DPC_PLL.c DPC_Telemetry.c DPC_Transforms.c)
    inc\   the matching headers plus DPC_LUT.h, DPC_Math.h, DPC_PLL.h, DPC_Transforms.h
```

The library also defines PFC-side types that this build does not use (`PFC_CTRL_t`, `Run_PFC_Mode`, grid relays, PLL and abc/dq transform headers, `INRUSH_STRUCT`,
`BURST_STRUCT`), i.e. it is generic across ST's digital-power reference designs (inferred); the DAB application reuses its FSM, PI, ADC, actuator, timeout and
fault modules and adds `DPC_LPCNTRL_DAB_*` and `DPC_ACT_*DAB*`. The compile-time constants live in `DPC_Application_Conf.h` / `DPC_Lib_Conf.h`;
preprocessor macros are not in the DWARF (`.debug_macinfo` holds only include nesting), so macro names are unknown and values were taken from the code.

### 2.2 ST application functions by purpose

**105** functions with DWARF belong to ST's own code (as opposed to 121 HAL functions with DWARF, plus CMSIS `SystemInit` and the libc/IAR runtime without DWARF):
47 in the DPC library (5,556 B), 15 in `main.c` (1,544 B), 43 CubeMX-generated peripheral / interrupt glue. Address, size in bytes.

| purpose | functions (file) |
|---|---|
| **state machine / sequencing** | `DPC_FSM_Application` 08004CF8 (158), `DPC_FSM_State_Set` 08004D96, `DPC_FSM_State_Init` 08004D9C (DPC_FSM.c); `DPC_FSM_WAIT_Func` 0800214E, `_IDLE_` 08002182, `_INIT_` 080021CE, `_START_` 08002222, `_RUN_` 0800224C, `_STOP_` 08002288, `_ERROR_` 080022B8, `_FAULT_Func` 080022E4 (main.c); timeouts `DPC_TO_Init` 08004DAC, `DPC_TO_Set` 08004DD8, `DPC_TO_Check` 08004DF8, `DPC_TO_Relase` 08004E14, `TimeoutMng` 08004E2C (DPC_Timeout.c); `main` 08001F34 (438) |
| **modulation / PWM update** (HRTIM) | `DPC_ACT_Init` 08004424 (378), `DPC_ACT_TpzPWM_PS_Init` 0800459E, `DPC_ACT_DAB_TpzPWM_PS_Update` 08004624 (298), `DPC_ACT_Calc_DeadTime` 080042B4, `DPC_ACT_DAB_Conf_DeadTime` 080042C8, `DPC_ACT_OutEnable` 080043C8, `DPC_ACT_OutDisable` 0800437E, `DPC_ACT_HRTIM_OutDisable` 08004334, `DPC_ACT_SetFault` 0800441A (DPC_Actuator.c); `DPC_LPCNTRL_DAB_TpzPWM_DutyTime_Calc` 08004A0C (510), `DPC_LPCNTRL_DAB_ModulatorSelector` 08004C5C, `DPC_LPCNTRL_DAB_MaxPowerCalc` 08004C10 (DPC_Loopctrl.c); `MX_HRTIM1_Init` 08003050 (952), `HAL_HRTIM_MspInit/MspPostInit` (hrtim.c) |
| **control loops** | `DPC_PI_Init` 080047F0, `DPC_PI` 0800485C (DPC_Pid.c); `DPC_LPCNTRL_DAB_Init` 08004900, `_Vset` 0800493C, `_Vramp` 08004958, `_Mode` 0800499C (DPC_Loopctrl.c); the control ISR body `HAL_TIM_PeriodElapsedCallback` 08002320 (258, main.c); `DPC_MISC_APPL_Timer_Init` 08003FEC, `DPC_MISC_Appl_Timer_Start` 0800401C (DPC_Miscellaneous.c) |
| **ADC handling** | `DPC_ADC_Init` 0800424E, `DPC_ADC_TrigSet` 080042A6, `ADC2Phy_DAB_ProcessData` 0800419C, `ADC2RAW_DAB_ProcessData` 0800422E (DPC_adc_converter.c); `DATA_Acquisition_from_DMA` 080057B0, `Read_DAB` 080057D0 (DPC_Datacollector.c); `DPC_MISC_Analog_Start` 0800245E, `HAL_ADC_ConvCpltCallback` 08002422 (main.c); `MX_ADC1_Init`, `MX_ADC2_Init`, `HAL_ADC_MspInit` (adc.c); `MX_DMA_Init` |
| **protections** | `DPC_FLT_Faulterror_Check` 08005670 (316, DPC_Faulterror.c); `DPC_MISC_DCLoad_Init` 08003EF8 (threshold set-up); the actions in `DPC_FSM_ERROR_Func` / `DPC_FSM_FAULT_Func` and `DPC_ACT_OutDisable`; `DPC_MISC_OB_Init` 0800415A (option bytes). See 3.9: the software fault chain is inert in this build. |
| **communication / HMI / debug** | none for control: `MX_USART2_UART_Init` (init only, no transmit or receive function is linked), no CAN. HMI: `DPC_Display_*`, `SSD1306_*` (I2C OLED, DPC_MISC_Display.c), `DPC_MISC_BLED_Set` (LED PWM on TIM1 CH3/CH3N), `DPC_MISC_RELAY_Cntl` (fan relay, GPIOC pin 12). Debug: `Debug_DATA_DAC`, `DPC_DAC_Init`, `MX_DAC1/2_Init` (the compiler removed the signal selection, so the DAC outputs are a constant), GPIO timing marks in the ISRs |
| **start-up / platform glue** | `SystemClock_Config`, `MX_GPIO_Init`, `MX_TIM1/2/3/6/7_Init`, `HAL_TIM_Base_MspInit`, `HAL_TIM_MspPostInit`, `MX_DAC*`, `MX_I2C1_Init`, `HAL_MspInit`, `Error_Handler` (returns immediately), `SystemInit`, 19 handlers in stm32g4xx_it.c |

### 2.3 Start-up and run-time structure (decoded from `main`, 0x08001F34)

1. `HAL_Init` (NVIC priority grouping 4 bits preempt / 0 sub; SysTick 1 kHz at priority 0), `SystemClock_Config`, `MX_GPIO / DMA / TIM1 / TIM2 / TIM3 / ADC1 / ADC2 / HRTIM1 / DAC1 / DAC2 / I2C1 / USART2 / TIM6 / TIM7_Init`.
2. `DPC_Display_Init` (OLED splash "DPC", "Power up", "........"), `DPC_MISC_OB_Init` (option bytes: programs the BOOT_LOCK bit if not set, then reloads - boot always from flash).
3. `DPC_ADC_Init(&DAB.DPC_ADC_Conf, 3.901, 0, 42.67, 2048, 6.206, 0, 102.4, 2048)`.
4. `DPC_ACT_Calc_DeadTime(400 ns)` twice -> `DAB.DPC_DTG_DAB1_bin / DAB2_bin` = 136; `DPC_ACT_DAB_Conf_DeadTime`.
5. `DPC_MISC_APPL_Timer_Init` three times: **TIM2 10 kHz, TIM3 1 kHz, TIM6 3 kHz**.
6. `DPC_MISC_Analog_Start`: ADC1/ADC2 calibration (single-ended), `HAL_ADC_Start_DMA` ADC1 (1 word) and ADC2 (5 words), DAC start.
7. `DPC_ACT_Init(20000, 100000, PWM_Armed, &DAB.tDPC_PWM)`: sets the HRTIM period, starts the master and the four timer counters and enables all 8 outputs, then immediately `DPC_ACT_HRTIM_OutDisable` (outputs off).
8. `DPC_PI_Init` (voltage PI), `DPC_MISC_DCLoad_Init(550 V, 1000 V, 0.5 A, 1.2 A, 100 A)`, `DPC_LPCNTRL_DAB_Init(&DAB.pDAB_CTRL, DAB_OPEN_LOOP, 50 V, &conf)` (this is where f_sw, T_sw, L and n are loaded), `DAB.PC_State = FSM_Idle` unless it is `FSM_Error`, `DPC_DAC_Init`.
9. `DPC_FSM_State_Init(DPC_FSM_WAIT)`, `DPC_TO_Init`, `DPC_ACT_TpzPWM_PS_Init` (master compares MCMP1..3 and timer CMP1 to their start values), `DPC_ADC_TrigSet(40800)`, **`DPC_ACT_OutEnable`** (all 8 PWM outputs on), `DPC_MISC_Appl_Timer_Start` (starts TIM2, TIM3, TIM6 interrupts and the LED PWM).
10. Endless loop `DPC_FSM_Application()`.

Facts that follow from this order: the **PWM outputs are enabled in step 9 before the 10 kHz control interrupt is started and independent of the FSM state** (the FSM only ever disables them);
the start values written by `DPC_ACT_TpzPWM_PS_Init` (MCMP1 = T/2, MCMP2 = T/4, MCMP3 = 0.75 T, with T = 54,400 counts) are not the values that `DPC_ACT_DAB_TpzPWM_PS_Update` writes for zero phase shift
in SPWM mode (MCMP1 = T/2, MCMP2 = 40, MCMP3 = MCMP4 = T/2); how long they act depends on when the first TIM2 interrupt arrives (up to one 100 us tick) and the resulting waveform was not derived here.
There is **no inrush / pre-charge or burst sequence**: the `INRUSH_*` / `BURST_*` types exist in the library but no instance is linked; the only soft-start-like element is the 10 V/s reference ramp (3.6).

**Finite state machine** (`DPC_FSM_State_t`: WAIT 0, IDLE 1, INIT 2, START 3, RUN 4, STOP 5, ERROR 6, FAULT 7; the state names are also OLED strings "FSM_WAIT" ... "FSM_ERROR"):

| state | what the code does | next |
|---|---|---|
| WAIT | LED state `BLED_Wait`, arms timeout #4 = **3000** ticks (1 tick = 1 ms, TIM3) | IDLE; STOP if the timeout could not be armed |
| IDLE | waits until timeout #4 has elapsed, then arms #5 = **1000** and releases #4 | INIT |
| INIT | waits until #5 elapsed, arms #6 = **1000** (never checked), closes the "RELAY_FAN" (GPIOC pin 12 high), releases #5 | START |
| START | sets `DAB.PC_State = FSM_Run` | RUN |
| RUN | `DPC_FLT_Faulterror_Check()`; non-zero -> STOP, otherwise stay (the converter control itself runs in the TIM2 interrupt, not here) | RUN / STOP |
| STOP | LED, display | ERROR (always) |
| ERROR | `DPC_FLT_Faulterror_Check()`; non-zero -> `DPC_ACT_OutDisable()` (8 outputs off) | FAULT (always) |
| FAULT | `DPC_ACT_OutDisable()`, `DPC_ACT_SetFault` (status `PWM_Fault`), `DAB.PC_State = FSM_Fault` | stays |

About **4 s** pass between reset and RUN (3 s + 1 s).
The FSM contains no voltage, current or temperature conditions; the only exit conditions are the timeouts and the fault vector (3.9).

### 2.4 Interrupts and the control call graph

Vectors in use (decoded from the table; NVIC priority = preempt priority, grouping 4 bits):

| vector (IRQ) | handler | prio | what it calls / does |
|---|---|---|---|
| TIM2 (28) | `TIM2_IRQHandler` 08009844 | **0** | `HAL_TIM_IRQHandler(&htim2)` -> `HAL_TIM_PeriodElapsedCallback` -> **the DAB control loop, every 100 us** (below) |
| TIM3 (29) | `TIM3_IRQHandler` | 1 | same callback -> `TimeoutMng` (1 kHz tick of the FSM timeouts) |
| TIM6_DAC (54) | `TIM6_DAC_IRQHandler` | 2 | same callback -> `DPC_LPCNTRL_DAB_Vramp` + `DPC_LPCNTRL_DAB_Vset` (reference ramp, 3 kHz); then `HAL_DAC_IRQHandler(&hdac1)` |
| TIM7_DAC (55) | `TIM7_DAC_IRQHandler` | 4 | -> `Debug_DATA_DAC`; **TIM7 is never started**, so this never runs |
| ADC1_2 (18) | `ADC1_2_IRQHandler` | 3 | `HAL_ADC_IRQHandler(&hadc1)`, `(&hadc2)` |
| DMA1 ch 3 / ch 4 (13, 14) | `DMA1_Channel3/4_IRQHandler` | 0 | `HAL_DMA_IRQHandler(&hdma_adc1 / &hdma_adc2)` -> `ADC_DMAConvCplt` -> `HAL_ADC_ConvCpltCallback`, which only pulses GPIOC pin 14 (ADC1) or 15 (ADC2) for scope timing |
| DMA1 ch 1, 2, 5 (11, 12, 15) | `DMA1_Channel1/2/5_IRQHandler` | 0 | DAC DMA handles (debug DAC) |
| SysTick | `SysTick_Handler` | 0 | `HAL_IncTick` |
| NMI, SVC, DebugMon, PendSV | return | - | empty |
| HardFault, MemManage, BusFault, UsageFault | `b .` (endless loop) | - | **do not disable the PWM outputs** |

The control interrupt (TIM2, 10 kHz) executes, in this order (decoded from `HAL_TIM_PeriodElapsedCallback`, 0x08002320):

```
GPIOC pin 11 high (scope marker)
DATA_Acquisition_from_DMA(p_ADC1_Data, p_ADC2_Data)        copy the latest DMA words into DAB_ADC
ADC2Phy_DAB_ProcessData(&DAB.DPC_ADC_Conf, DAB_ADC, &DAB.DAB_ADC_PHY)   counts -> V / A (float)
ADC2RAW_DAB_ProcessData(DAB_ADC, &DAB.DAB_ADC_RAW)          counts -> int32
DPC_LPCNTRL_DAB_Mode(&pDAB_CTRL, &DAB_ADC_RAW)              open loop / voltage PI -> PhSh_PowerCTRL, PhSh_PowerCTRL_rad
DPC_LPCNTRL_DAB_MaxPowerCalc(&pDAB_CTRL, VDAB1, VDAB2, n, f_sw, L)     Pmax_sps, Pmax_tpz (informational, nothing reads them)
DPC_LPCNTRL_DAB_TpzPWM_DutyTime_Calc(&pDAB_CTRL, VDAB1, VDAB2, n, PhSh_PowerCTRL_rad, f_sw)    trapezoidal inner shifts
DPC_LPCNTRL_DAB_ModulatorSelector(&pDAB_CTRL)               pick (Duty1, Duty2, PhSh) for SPWM / TPZ / MAN
DPC_ACT_DAB_TpzPWM_PS_Update(&tDPC_PWM, Duty1_CTRL, Duty2_CTRL, PhSh_CTRL)    write HRTIM master compares MCMP1..4
GPIOC pin 11 low
```

`DPC_LPCNTRL_DAB_Mode` calls `DPC_PI`; `DPC_LPCNTRL_DAB_Mode` and `..._DutyTime_Calc` use the IAR software double-precision routines (`__aeabi_dmul`, `__aeabi_ddiv`, `__aeabi_dadd`, `__aeabi_dsub`, `__aeabi_f2d`, `__aeabi_d2f`) because the constants pi and pi/2 are doubles; the Cortex-M4F FPU is single precision only.
The execution time of the interrupt was not estimated.

Other calls: `main` -> `DPC_FSM_Application` -> the eight `DPC_FSM_*_Func` -> `DPC_TO_*`, `DPC_MISC_BLED_Set`, `DPC_Display_Write`, `DPC_FLT_Faulterror_Check`, `DPC_ACT_OutDisable`, `DPC_ACT_SetFault`, `DPC_MISC_RELAY_Cntl`.

### 2.5 The `DAB` data structure (type `DPC_DAB_t`, 432 B at 0x20000440, `main.c` line 70)

DWARF gives every member name, type and offset. Initial values are the result of the start-up code of 2.3; `BSS` = zero-initialised and not set by any linked code, i.e. the value 0 is what a running unit has until a debugger or monitor writes it.
All offsets are from the start of `DAB`.

| offset | member | type | initial value | note |
|---|---|---|---|---|
| +0 | `InitMode` | enum HV2LV 0 / LV2HV 1 / BIDI 2 | 0 (BSS) | never read |
| +4 | `tDPC_PWM.DPC_PWM_Status` | enum Safe 0 / Armed 1 / Fault 2 | 1 | read by `DPC_ACT_OutEnable` |
| +8, +12 | `tDPC_PWM.dutyMaxLim / dutyMinLim` | u32 | 54,304 / 96 | never read |
| +16 | `tDPC_PWM.PWM_Period` | u32 | 54,400 | counts of the 5.44 GHz HRTIM clock |
| +20 | `tDPC_PWM.BURST_PWM_Period` | u32 | 272,000 | never read |
| +24 .. +36 | `pDAB_CTRL.swFreq_Hz, swPeriod_s, Inductance_H, trafoTurnRatio` | float | 100000, 1e-5, 2.8e-5, 1.78 | `DPC_LPCNTRL_DAB_Init` |
| +40 | `pDAB_CTRL.ModTecnique` (sic) | enum SPWM 0 / TPZ 1 / TRG 2 / MAN 3 | 0 = SPWM (BSS) | user-selected |
| +41 | `pDAB_CTRL.VdcCTRL_Reset` | flag | 0 | becomes the PI `resetPI` in voltage-loop mode |
| +42 | `pDAB_CTRL.DAB_CTRL_State` | enum DAB_OPEN_LOOP 0 / DAB_VOLTAGE_LOOP 1 | 0 (Init argument) | user-selected |
| +44 | `pDAB_CTRL.PhSh_CTRL_MAN` | float | 0 (BSS) | manual phase shift (open loop and MAN) |
| +48 / +52 | `fDAB_VDC_Ref_V` / `DAB_VDC_Ref_BITs` | float / u16 | 50.0 / 310 | ramped by TIM6 |
| +56 / +60 | `fDAB_VDC_RefPrev_V` / `_BITs` | float / u16 | 0 | previous value, bits never read |
| +64 / +68 | `fDAB_VDC_RefNext_V` / `_BITs` | float / u16 | 0 (BSS) | the ramp target; **nothing in the image sets it** |
| +72 .. +83 | `VOLTAGECTRL.Vdc_ref, Vdc_feed, Id_ctrl` | float | - | copies of the PI reference and feedback (counts) and of its saturated output; only written |
| +84 .. +219 | `VOLTAGECTRL.pPI_VDC_CTRL` (`PI_STRUCT_t`, 136 B: Ref, Feed, Kp, Ki, Ts, Integral, PIout, PIout_sat, PIsat_up/down, error, Integralout, resetPI, k0, k1, Antiwindup_Term, satPI_toggle, antiwindPI_toggle, Antiwindup_Gain, resetValue, `PI_Q_FxP` and 14 Q16 `_s32` mirrors) | | see 3.5 | the `_s32` fields are initialised partly but never used by `DPC_PI` |
| +220 / +224 | `PhSh_CTRL` / `PhSh_CTRL_rad` | float | - | phase shift handed to the actuator (+224 is neither written nor read) |
| +228 / +232 | `PhSh_PowerCTRL` / `_rad` | float | - | PI output (or manual) and its value x 3.14159 |
| +236 / +240 | `Duty1_CTRL`, `Duty2_CTRL` | float | - | to the actuator |
| +244 .. +272 | `TpzDuty1/2`, `TpzD1_time/D2_time`, `TpzOmega_1/2_rad`, `phi_rad_max_trian`, `phi_rad_max_trap` | float | - | trapezoidal results; the two `phi_rad_max_*` are written only |
| +276 / +280 | `TpzDuty1_CTRL_MAN`, `TpzDuty2_CTRL_MAN` | float | 0 (BSS) | manual duties (mode MAN) |
| +284 / +288 | `Pmax_sps`, `Pmax_tpz` | float | - | written only |
| +292 / +296 | `DPC_DTG_DAB1_bin`, `DPC_DTG_DAB2_bin` | u32 | 136, 136 | dead-time counts |
| +300 .. +318 | `DPC_Load`, `DC_Load_Limit` (nine u16 thresholds) | | see 3.9 | never read |
| +320 .. +359 | `DAB_ADC_PHY` (VDAB1, VDAB2, IDAB1, IDAB2, ILK; float) and `DAB_ADC_RAW` (int32) | | - | |
| +360 .. +411 | `DPC_ADC_Conf` (G/invG/B for Vac, Iac, Vdc, Idc + complete flag) | | see 3.4 | |
| +412 / +413 | `PC_State` / `FSM_Run_State` | enum | 0 (FSM_Idle) | `FSM_START` writes 3 (FSM_Run), `FSM_FAULT` writes 4 (FSM_Fault) |
| +416 .. +431 | `DAC_CH` (channel selects 12, 3, 4; gain 2048 and bias 2048 each) | | | debug DAC |

### 2.6 Application globals: type, size, address, initial value

From the DWARF variable entries (type, size, address) and the initialisers: the `P2 rw` section is filled by an IAR packbits table (entry at 0x08009980: 27 packed bytes at 0x08009B78 -> 56 bytes at 0x20000000), the `P2 zi` section by a zero-fill entry (0x20000038, 0xC5C bytes); both were decoded.
No control constant lives in `.rodata`: the read-only data are the font and three HAL / CMSIS tables (below).

| name | type | size | address | section | initial value |
|---|---|---|---|---|---|
| `DAB` | `DPC_DAB_t` | 432 | 0x20000440 | zi | zeros; filled by the start-up code (2.3, 2.5) |
| `p_ADC1_Data`, `p_ADC2_Data` | `uint32_t[1]`, `uint32_t[5]` | 4, 20 | 0x200005F0, 0x200005F4 | zi | circular DMA targets of ADC1 and ADC2 |
| `DAB_ADC` | `DAB_ADC_Value_Struct` | 20 | 0x20000C50 | zi | copy of the newest DMA words (VDAB1, VDAB2, IDAB1, IDAB2, ILK) |
| `T_ext` | `uint16_t` | 2 | 0x20000C64 | zi | 0, never read |
| `DPC_FSM_State`, `DPC_FSM_NEW_State` | `volatile DPC_FSM_State_t` | 1 + 1 | 0x20000C66, 0x20000C67 | zi | 0 (WAIT) after `DPC_FSM_State_Init(0)` |
| `taTimeoutList` | `DPC_TO_TIMEOUTDATA_t[7]` | 56 | 0x20000C18 | zi | `DPC_TO_Init`: count 0, state `DPC_TO_OFF` (1) in all 7 entries |
| `uwFaultErrorVector` | `uint32_t` | 4 | 0x20000C90 | zi | 0; never written (3.9) |
| `SSD1306`, `SSD1306_Buffer` | struct, `uint8_t[1024]` | 6, 1024 | 0x20000038, 0x20000040 | zi | OLED state and frame buffer |
| `hhrtim1`, `hadc1`, `hadc2`, `huart2`, `hi2c1`, `htim1/2/3/6/7`, `hdac1/2`, `hdma_adc1/2`, `hdma_dac1_ch1/ch2`, `hdma_dac2_ch1` | HAL handles | 252, 108, 108, 144, 76, 76 each, 20 each, 96 each | 0x20000728 ... 0x20000C7C | zi | set by the `MX_*_Init` functions |
| `Font_11x18` | `FontDef_t` | 8 | 0x20000000 | rw | width 11, height 18, data pointer 0x080001D8 |
| `uwTick`, `uwTickPrio`, `uwTickFreq` | `uint32_t` | 4 each | 0x20000008, 0x2000000C, 0x20000010 | rw | 0, 16, 1 |
| `pFlash` | `FLASH_ProcessTypeDef` | 32 | 0x20000014 | rw | zeros except one word = 1 at +16 (HAL default) |
| `SystemCoreClock` | `uint32_t` | 4 | 0x20000034 | rw | 0x00F42400 = 16,000,000 (HSI); `HAL_RCC_ClockConfig` updates it to 170 MHz |
| `Font11x18` | `const uint16_t[1710]` | 3,420 | 0x080001D8 | rodata | 11 x 18 font bitmap for the OLED |
| `UARTPrescTable`, `UARTEx_SetNbDataToProcess::numerator / denominator`, `AHBPrescTable`, `APBPrescTable` | `const uint16_t[12]`, `const uint8_t[8]` x2, `const uint8_t[16]`, `const uint8_t[8]` | 24, 8, 8, 16, 8 | 0x080083FC, 0x08008738, 0x08008740, 0x08009914, 0x08009A04 | rodata | HAL / CMSIS prescaler tables |

---

## 3. Control parameters

### 3.1 Notation

Each row: parameter, **raw** value as found, **decoded** value, **where used** (function @ address of the code that sets or consumes it), confidence.
Floats are IEEE-754 single precision: the raw hex word is given and the decoded value is the nearest short decimal (e.g. 0x4079A9FC = 3.901).
The same rows, machine-readable, are in `sim/data/st_dab_firmware_params.csv`. All values are from the `.hex`/`.out` build; the `.sim` differences are in 1.5.
"Not read" means: no instruction in the image loads that variable (checked by following every reference to the `DAB` base address 0x20000440 and to the function arguments that point into it).

### 3.2 Clocks, timers and interrupt timing

| parameter | raw | decoded | where used | conf |
|---|---|---|---|---|
| CPU and bus clocks | HSI16, PLLM /4, PLLN x85, PLLR /2, AHB /1, APB1 /1, APB2 /1, flash latency 4, boost regulator | **170 MHz** everywhere | `SystemClock_Config` 0x080020EA; `HAL_RCC_GetPCLK2Freq` is called by `DPC_ACT_Init` and `DPC_MISC_APPL_Timer_Init` | high |
| control timer TIM2 | PSC 0, ARR 16,999 = 170e6 / (PSC+1) / 10,000 - 1 | **10.000 kHz = 100 us**, NVIC priority 0 | argument 10000 of `DPC_MISC_APPL_Timer_Init` (`main` 0x08001FD8); body `HAL_TIM_PeriodElapsedCallback` | high |
| timeout timer TIM3 | PSC 4, ARR 33,999 | **1.000 kHz = 1 ms**, priority 1 | argument 1000 (0x08001FF2); `TimeoutMng` | high |
| reference-ramp timer TIM6 | PSC 0, ARR 56,665 | **3000.04 Hz = 333.3 us**, priority 2 | argument 3000 (0x0800200C); `DPC_LPCNTRL_DAB_Vramp` / `_Vset` | high |
| TIM7 | PSC 0, ARR 0, never started | unused (debug DAC trigger) | `MX_TIM7_Init`, `Debug_DATA_DAC` | high |
| TIM1 | PSC 0, ARR 0xFFFF, PWM on CH3 / CH3N (channel code 8) | LED brightness only | `DPC_MISC_Appl_Timer_Start`, `DPC_MISC_BLED_Set` | high |
| NVIC | priority grouping 3 (4 bits preempt); TIM2 0, TIM3 1, TIM6_DAC 2, ADC1_2 3, TIM7_DAC 4, DMA1 ch1-5 0, SysTick 0 | TIM2 shares priority 0 with SysTick and the DMA1 ch1-5 interrupts (no preemption among them); TIM3, TIM6, ADC1_2 and TIM7 are lower and are preempted by it | `HAL_Init`, `HAL_TIM_Base_MspInit`, `HAL_ADC_MspInit`, `MX_DMA_Init`, `HAL_InitTick` | high |
| HAL tick | SysTick reload = SystemCoreClock / (1000 / uwTickFreq); `uwTickFreq` initial value 1 (decoded from the packed init data, 2.6) | **1 kHz**, priority 0 | `HAL_InitTick` 0x080068D8 | high |

### 3.3 PWM generation (HRTIM1), dead time

| parameter | raw | decoded | where used | conf |
|---|---|---|---|---|
| HRTIM counter clock | `PrescalerRatio` = 0 (CKPSC x32) for the master and timers A-D; `DPC_ACT_Init` maps the field back to 32 | f_HRCK = 32 x 170 MHz = **5.44 GHz**, 1 count = 183.8 ps (assumes the default HRTIM kernel clock = PCLK2; no HRTIM clock selection is programmed) | `MX_HRTIM1_Init` 0x08003050; `DPC_ACT_Init` 0x08004424 | high |
| requested PWM frequency | 100000 (0x186A0) | **100 kHz** | argument of `DPC_ACT_Init` (`main` 0x0800202E) | high |
| HRTIM period (MPER and TAPER..TDPER) | float32(5,440,000 kHz / 100,000 Hz x 1000) = 54,400; up-counting (`UpDownMode` = 0), continuous | **54,400 counts = 10.000 us**; 1 count = 0.00662 deg of the switching period | `DPC_ACT_Init` | high |
| requested burst PWM frequency | 20000 (0x4E20) | 20 kHz -> 272,000 counts stored in `tDPC_PWM.BURST_PWM_Period`; never read | `DPC_ACT_Init` | value high, role medium |
| `dutyMaxLim`, `dutyMinLim` | 54,304, 96 (= period - 96, 96 for CKPSC x32) | stored, never read | `DPC_ACT_Init` | high |
| actuator compare limits | 40 and MPER | `DPC_ACT_DAB_TpzPWM_PS_Update` limits MCMP1..4 to [40, MPER] (7.35 ns minimum) | 0x08004624 | high |
| actuator input clamp | 0x3F800001 (1.0000001) and -1.0 | the three arguments (D1, D2, PS) are clamped to [-1, +1] | same | high |
| actuator scaling | `HALF` = MPER / 2 = 27,200 | compare tick = HALF x argument; negative results get +MPER | same | high |
| master compare roles | MCMP1 = D1 ticks; MCMP2 = PS ticks; MCMP3 = MCMP4 = (D2 + PS) ticks, each wrapped into the period and clamped | the control interrupt rewrites all four every 100 us | same | high |
| set / reset / reset-trigger crossbar | timers A, B, C, D are reset by master CMP1, CMP2, CMP3, CMP4; TA1, TB1, TC1, TD1 are set by master PER, CMP1, CMP2, CMP3 and reset by the timer's own CMP1; outputs 2 are the complements with dead-time insertion (DTEN) | register values only - the leg waveforms they produce were not derived | `MX_HRTIM1_Init` | high (values) |
| start values | MCMP1 = 27,200 (T/2), MCMP2 = 13,600 (T/4), MCMP3 = 40,800 (0.75 T), timer CMP1 = 27,200 (T/2); MCMP4 = 40,800 | see the remark in 2.3 | `DPC_ACT_TpzPWM_PS_Init` 0x0800459E, `DPC_ADC_TrigSet` | high |
| dead time requested | 0x34D6BF95 | 4.0e-7 s = **400 ns** for both bridges (.sim: 6.0e-7 s) | `main` 0x08001FB2 -> `DPC_ACT_Calc_DeadTime` twice | high |
| dead-time tick | 0x314A1DB9; `DTPRSC` field written 0x800 (= 2) | 2.941176 ns per count = t_HRTIM / 2 = 16 / 5.44 GHz | `DPC_ACT_Calc_DeadTime` 0x080042B4; `MX_HRTIM1_Init` | tick high; the meaning of DTPRSC = 2 is from the RM (medium) - it agrees with the firmware constant |
| dead-time counts | uint8(4e-7 / 2.941176e-9) = 136 (.sim: 204) | **136 counts = 399.99999 ns**; written as rising and falling time; TIMA and TIMB get `DT_Dab1`, TIMC and TIMD get `DT_Dab2`; both are 136 | `DPC_ACT_DAB_Conf_DeadTime` 0x080042C8; `DAB.DPC_DTG_DAB1/2_bin` | high |
| dead time as phase | 360 deg x f_sw x t_d | 14.4 deg (400 ns); 21.6 deg (.sim, 600 ns) | calculated | - |
| output pins | TA1, TA2, TB1, TB2 = PA8 ... PA11; TC1, TC2, TD1, TD2 = PB12 ... PB15, AF13 | | `HAL_HRTIM_MspPostInit` 0x0800342A | medium |
| output idle / polarity | polarity high, idle mode none, idle level 0, fault level 0, chopper off | outputs are low when disabled | `MX_HRTIM1_Init` | high |
| HRTIM fault inputs | `FaultEnable` = 0 for timers A-D; `HAL_HRTIM_FaultConfig` is not linked | **no hardware fault input is configured in the HRTIM** | `MX_HRTIM1_Init` | high |
| DLL calibration | calibration-rate bits 0xC, poll timeout 10 | periodic calibration, rate 3 | `MX_HRTIM1_Init` | medium |

### 3.4 ADC chain and scaling constants

| parameter | raw | decoded | where used | conf |
|---|---|---|---|---|
| ADC clock | ADC12 kernel clock = SYSCLK, `ClockPrescaler` 0x30000 (synchronous HCLK / 4) | 42.5 MHz | `HAL_ADC_MspInit`, `MX_ADC1_Init` | high |
| format | 12 bit, right aligned, single-ended (0x7F), no oversampling, no gain compensation | | `MX_ADC1/2_Init`, `DPC_MISC_Analog_Start` | high |
| trigger | external, rising edge, EXTSEL field 0x15; HRTIM ADC trigger 1 = master CMP4 (0x8), postscaler 0 | one trigger per master period if MCMP4 matches once per period (the EXTSEL-to-HRTIM-trigger-number mapping is in the RM, not in the binary) | `MX_ADC1/2_Init`, `MX_HRTIM1_Init` | high (fields), medium (mapping) |
| ADC1 | 1 conversion, DMA1 ch3 (request 5), circular, word | `ILK` = IN8 (PC2) | `MX_ADC1_Init`, `HAL_ADC_MspInit` | high |
| ADC2 | scan of 5 ranks, discontinuous mode with 1 conversion per trigger, DMA1 ch4, circular, word | each ADC2 channel is refreshed once per 5 triggers = 50 us at one trigger per 10 us period (calculated); the 10 kHz control reads the newest value | `MX_ADC2_Init` | high / medium |
| ADC2 ranks | rank 1 IN4 (PA7), 2 IN5 (PC4), 3 IN6 (PC0), 4 IN7 (PC1), 5 IN12 (PB2) | -> `IDAB2`, `IDAB1`, `VDAB2`, `VDAB1`, and rank 5 (not used, see below) | `DATA_Acquisition_from_DMA` 0x080057B0 | channel numbers high; pin names medium |
| `T_ext` | read from `p_ADC1_Data[4]` = 0x20000600 = `p_ADC2_Data[3]` | index beyond the 1-word ADC1 buffer: it duplicates VDAB1 rather than rank 5 (IN12); `T_ext` is never read | same | high (address), role low |
| sampling time | ADC1 IN8 47.5 cycles; ADC2 IN4-IN7 92.5 cycles; IN12 2.5 cycles | conversion = (S + 12.5) / 42.5 MHz = 1.41 / 2.47 / 0.35 us | `MX_ADC1/2_Init` | high |
| hardware offset | ADC2 rank 3 (IN6) and rank 4 (IN7): offset 1 / 2 = 100, sign negative, saturation off | 100 counts are subtracted in hardware on VDAB2 and VDAB1; the software offsets B_Vac, B_Vdc are 0 | `MX_ADC2_Init` | values high, sign semantics medium |
| `G_Vac`, `B_Vac` | 0x4079A9FC, 0 | VDAB1 [V] = (count - B_Vac) / 3.901: 0.2563 V per count, 4096 counts = **1,050 V** | `DPC_ADC_Init` 0x0800424E (args from `main` 0x08001FA6); `ADC2Phy_DAB_ProcessData` 0x0800419C | high |
| `G_Vdc`, `B_Vdc` | 0x40C6978D, 0 | VDAB2 [V] = (count - B_Vdc) / 6.206: 0.1611 V per count, 4096 counts = **660 V** | same | high |
| `G_Idc`, `B_Idc` | 0x42CCCCCD, 0x45000000 (2048.0) | IDAB1, IDAB2, ILK [A] = (count - 2048) / 102.4: 9.77 mA per count, **+/-20.0 A** | same | high |
| `G_Iac`, `B_Iac` | 0x422AAE14, 0x45000000 | 42.67 counts/A, +/-48 A: stored but **no DAB conversion uses them** | `DPC_ADC_Init` | high |
| derived by the library | invG = 1 / G stored next to G | used by the conversions (multiply instead of divide) | `DPC_ADC_Init` | high |
| quantities the controller really uses | raw VDAB2 (voltage PI feedback), VDAB1 and VDAB2 in volts (`MaxPowerCalc`, `DutyTime_Calc`) | **no current is used by any control, limit or protection code** (IDAB1, IDAB2, ILK are converted and stored only) | `DPC_LPCNTRL_DAB_Mode`, `HAL_TIM_PeriodElapsedCallback` | high |

### 3.5 Voltage loop (the only closed loop)

`DPC_PI` is a parallel PI in float, evaluated at 10 kHz; error in ADC counts, output a normalised phase shift.

| parameter | raw | decoded | where used | conf |
|---|---|---|---|---|
| structure | `DPC_PI_Init(&pPI_VDC_CTRL, Kp, Ki, Ts, up, down, sat = 1, antiwindup = 1, Kb, reset)` | PIout = Integral + Kp x e; Integral(k) = Integral(k-1) + Ki x Ts x e(k) + Antiwindup_Term(k-1); e = Ref - Feed | `main` 0x0800205E; `DPC_PI` 0x0800485C | high |
| Kp (`k0`) | 0x3A83126F | **0.001** per ADC count | `DPC_PI_Init` | high |
| Ki | 0x3DCCCCCD | **0.1** per count and second | same | high |
| Ts | 0x38D1B717 | **1.0e-4 s** (equals the TIM2 period) | same | high |
| k1 = Ki x Ts | computed at start-up | 1.0e-5 per count per sample | same | high |
| output limits | `PIsat_up` 0x3ECCCCCD, `PIsat_down` 0, `satPI_toggle` = 1 (SET) | clamp to **[0, 0.4]**; x 3.14159 = 0 ... 1.2566 rad = **0 ... 72 deg** (0.2 T) | `DPC_PI` | high |
| anti-windup | `antiwindPI_toggle` = 1, `Antiwindup_Gain` 0x3DCCCCCD | back-calculation: Antiwindup_Term = 0.1 x (PIout_sat - PIout), fed to the integrator on the next sample | same | high |
| reset value | 0 | integral value while `resetPI` is set (open loop, or `VdcCTRL_Reset`) | same | high |
| fixed point | `PI_Q_FxP` = 16, `_s32` mirrors initialised | not used by `DPC_PI` (float only) | `DPC_PI_Init` | high |
| input | Ref = `DAB_VDC_Ref_BITs` (u16 count of the reference); Feed = raw `VDAB2` (int32 count) | **the regulated variable is the port-2 (VLV) voltage**, unfiltered | `DPC_LPCNTRL_DAB_Mode` 0x0800499C | high |
| output use | `PhSh_PowerCTRL` = PIout_sat (VOLTAGE_LOOP) or `PhSh_CTRL_MAN` (OPEN_LOOP) or 0 (other states); `PhSh_PowerCTRL_rad` = x 3.14159 (double) | the phase shift is zero or positive in the loop; a negative phase shift can only come from a manual value | same | high |
| mode switch | `DAB_CTRL_State`: 0 OPEN_LOOP (start value), 1 VOLTAGE_LOOP | in OPEN_LOOP `resetPI` is held at 1, so the integrator restarts from 0 on entering VOLTAGE_LOOP (no bumpless transfer in the code) | same | high |
| gains in engineering units | Kp x G_Vdc, Ki x G_Vdc | 6.206e-3 per V (1.117 deg/V), 0.6206 per V per s (111.7 deg/(V s)); PI zero Ki/Kp = 100 rad/s = 15.9 Hz; **calculated** | - | calculated |
| loop bandwidth | - | **not derivable**: needs the port-2 capacitance, the load and the power-stage gain, none of which is in the binary | - | - |

### 3.6 Reference, ramp and start values

| parameter | raw | decoded | where used | conf |
|---|---|---|---|---|
| boot reference | `DPC_LPCNTRL_DAB_Init` argument 50 (0x32) | `fDAB_VDC_Ref_V` = 50.0 V, `DAB_VDC_Ref_BITs` = (uint16)(50 x 6.206 + 0) = 310 | `main` 0x08002084; 0x08004900 | high |
| ramp step | 0x3B5A740E up, 0xBB5A740E down | +/-0.0033333 V per TIM6 tick x 3000 Hz = **10 V/s**, the same up and down; direction from comparing the reference with `fDAB_VDC_RefNext_V`; no other limit | `DPC_LPCNTRL_DAB_Vramp` 0x08004958 | high |
| ramp target `fDAB_VDC_RefNext_V` | 0 (BSS) | **nothing in the image writes it**: unmodified, the 50 V boot reference decays to 0 V in about 5 s (calculated); 0 -> 449 V would take 45 s | `Vramp` | high |
| V -> counts | bits = (uint16)(V x `G_Vdc` + `B_Vdc`) | truncating, 0.161 V per count | `DPC_LPCNTRL_DAB_Vset` 0x0800493C | high |
| start state | `DAB_CTRL_State` 0, `ModTecnique` 0, `PhSh_CTRL_MAN` 0, `TpzDuty*_CTRL_MAN` 0, `VdcCTRL_Reset` 0 | the unit starts open loop, SPWM, zero phase shift; closing the loop, selecting a mode, a target or a manual phase needs a write to RAM | BSS / `DPC_LPCNTRL_DAB_Init` | high |
| control interface | no UART transmit / receive, no CAN, no EXTI, `HAL_GPIO_ReadPin` not linked | the only inputs left are the RAM variables above; the delivered ELF with symbols suggests a debugger or STM32CubeMonitor (inferred) | whole image | medium |

### 3.7 Modulation and phase-shift limits

| parameter | raw | decoded | where used | conf |
|---|---|---|---|---|
| mode variable | `DABModTecnique_TypeDef`: SPWM 0, TPZ 1, TRG 2, MAN 3; start value 0 | chosen by writing the variable; **no automatic selection by operating point** | `DPC_LPCNTRL_DAB_ModulatorSelector` 0x08004C5C | high |
| SPWM (single phase shift) | Duty1 = Duty2 = 1.0, PS = `PhSh_PowerCTRL` | classical SPS with 50 % legs; PS = PI output | same | high |
| TPZ (trapezoidal) | Duty1 = `TpzDuty1`, Duty2 = `TpzDuty2`, PS = 0 | two inner shifts, computed each cycle (below) | same | high |
| MAN | Duty1 = `TpzDuty1_CTRL_MAN`, Duty2 = `TpzDuty2_CTRL_MAN`, PS = `PhSh_CTRL_MAN` | fully manual duty and phase | same | high |
| TRG (triangular), and any value > 3 | Duty1 = Duty2 = 0, PS = 0 | **declared but not implemented**: selecting it commands zero | same | high |
| TPZ formulas | constants 3.14159 and 1.57079 (doubles), M = n x VLV / VHV, phi = `PhSh_PowerCTRL_rad` | `phi_rad_max_trian` = 1.57079 (1 - M); `phi_rad_max_trap` = 1.57079 (1 - nVHVVLV / (nVHVVLV + VHV^2 + (nVLV)^2)); if VHV >= nVLV: Omega2 = (2 VHV phi + (nVLV - VHV) pi) / (2 (VHV + nVLV)), Omega1 = phi - Omega2; else Omega1 = (2 nVLV phi + (VHV - nVLV) pi) / (2 (VHV + nVLV)), Omega2 = phi - Omega1; `TpzD1_time` = (Tsw/2)(1 - 2 Omega1 / pi), `TpzD2_time` likewise with Omega2; `TpzDuty1` = +TpzD1_time / (Tsw/2), `TpzDuty2` = -TpzD2_time / (Tsw/2) | `DPC_LPCNTRL_DAB_TpzPWM_DutyTime_Calc` 0x08004A0C | high (formulas read from the code); they are closed-form, not tabulated or optimised |
| phase limit actually applied | PI saturation 0.4 | 72 deg in SPWM and as phi in TPZ; `phi_rad_max_trian/trap` are computed but **written only, never used as limits** | `DPC_PI`; `DutyTime_Calc` | high |
| `Pmax_sps` | n x VHV x VLV / (8 f_sw L) | 28.57 kW at 800 V / 449.4 V with n = 1.78, 100 kHz, 28 uH (calculated); written only | `DPC_LPCNTRL_DAB_MaxPowerCalc` 0x08004C10 | formula high |
| `Pmax_tpz` | (n VHV VLV)^2 / (4 f_sw L (n VHV VLV + VHV^2 + (n VLV)^2)) | written only | same | high |
| rate limiting between PI and PWM | none found | no phase-slew limiter, no split (volt-second-balanced) update, no soft step: the PI output passes the clamps of 3.3 and is written to the compare registers every 100 us | `HAL_TIM_PeriodElapsedCallback`, `DPC_ACT_DAB_TpzPWM_PS_Update` | high |
| float format | single precision, with double-precision soft-float in `DPC_LPCNTRL_DAB_Mode` and `..._DutyTime_Calc` | | callgraph in 2.4 | high |

### 3.8 Converter model constants (loaded by `DPC_LPCNTRL_DAB_Init`, 0x08004900)

| parameter | raw | decoded | where used | conf |
|---|---|---|---|---|
| `swFreq_Hz` | 0x47C35000 | **100,000 Hz** | `MaxPowerCalc` (passed to `DutyTime_Calc`, which ignores it) | high |
| `swPeriod_s` | 0x3727C5AC | **1.0e-5 s** | `DutyTime_Calc` (Tsw, half period) | high |
| `Inductance_H` | 0x37EAE18B | **28 uH** (2.8e-5 H) | `MaxPowerCalc` only (the control path does not use L) | high |
| `trafoTurnRatio` | 0x3FE3D70A | **1.78**; the code multiplies VLV by it, M = n VLV / VHV, so n = 1.78 matches 800 V to 449.4 V (calculated) | `MaxPowerCalc`, `DutyTime_Calc` | high; orientation high |

The switching frequency exists in **three independent copies** (the argument 100000 of `DPC_ACT_Init`, `swFreq_Hz`, `swPeriod_s`); nothing ties them together.

### 3.9 Protections and thresholds

**The software fault chain is inert in this image** (decoded): `DPC_FLT_Faulterror_Check` returns the lowest set bit of `uwFaultErrorVector`, and its only reader is that function; **no instruction in the image writes the vector** (the library's fault-set function is not linked - inferred from its absence), so it stays 0 and RUN never leaves on a fault.
`DPC_FSM_ERROR_Func` / `DPC_FSM_FAULT_Func` would call `DPC_ACT_OutDisable()` (8 outputs off) and `DPC_ACT_SetFault`, but only if a debugger sets the vector. The HRTIM has no fault input configured (3.3), no comparator, watchdog or ADC analog-watchdog is configured (none of the HAL set-up functions is linked), and the hard-fault handlers loop without touching the PWM.
The fault list type shows what the library supports: `FAULT_OCL` 0x1, `FAULT_OVL` 0x2, `FAULT_OVC` 0x4, `FAULT_OCS` 0x8, `FAULT_OVS` 0x10, `FAULT_INR` 0x20, `FAULT_BRS` 0x40, `FAULT_PLL_OR` 0x80, `FAULT_PFC_UVLO` 0x100, `FAULT_IDLE` 0x200, `FAULT_GEN` 0x400, `FAULT_MAN` 0x8000, `ERROR_*` bits 16-31 (names read from DWARF; expansions such as "over-current load" are inferred).

Thresholds that are **set but never read** (`DPC_MISC_DCLoad_Init`, 0x08003EF8, called from `main` 0x0800207C; stored at `DAB.DC_Load_Limit`, +302):

| field | input | decoded counts | note |
|---|---|---|---|
| `V_dc_Limit` | 550 V | 3,413 (550 x 6.206 + 0) | 550 V on the 660 V full-scale channel (VDAB2 scale) |
| `V_cap_Limit` | 1000 V | 6,206 | above the 12-bit range (4,095): could never trigger |
| `I_No_load_Threshold` / Max / Min | 0.5 A | 2,099 / 2,109 / 2,089 | 2048 + 0.5 x 102.4; +/-20 % of (count - 2048) hysteresis (10 counts) |
| `I_Low_load_Threshold` / Max / Min | 1.2 A | 2,170 / 2,206 / 2,134 | +/-30 % hysteresis (36 counts) |
| `I_Over_load_Threshold` | 100 A | 12,288 | beyond 4,095 counts and beyond the +/-20 A scale |

Not present at all: over-temperature (`T_ext` is not compared with anything), under-voltage, transformer DC-bias, short-circuit, ZVS or soft-switching supervision.
The only hardware-related action in the code is `DPC_MISC_OB_Init`: it makes sure the BOOT_LOCK option bit is set (boot from flash only) and reloads the option bytes (0x10000 compared and written).

### 3.10 Burst / light-load mode

The library's burst machinery (`BURST_STRUCT`, `BURST_StatusTypeDef` states Start / Complete / Error / Progress / TIMEOUT / Disable / Run, `Run_Burst_Mode`, `FSM_StartUp_burst`) is declared but **no burst code or instance is in the image**. Residual parameters: the 20 kHz burst frequency (3.3) and the no-load / low-load thresholds 0.5 A and 1.2 A (3.9), all unused. No light-load or ZVS-extension logic exists other than the manually selectable TPZ mode.

### 3.11 Communication and other settings

| item | raw | decoded | where used | conf |
|---|---|---|---|---|
| CAN / FDCAN | - | **absent** (no FDCAN HAL, no handler) | - | high |
| USART2 | baud 57,600 (0xE100), 8 bit, 1 stop, no parity, mode TX+RX, no flow control, 16x oversampling, FIFO off | initialised only: no transmit or receive function is linked; the library's telemetry module was removed | `MX_USART2_UART_Init` 0x080037EC | high |
| I2C1 | timing word 0x10802D9B, 7-bit | drives the SSD1306 OLED (state names, "Power up") | `MX_I2C1_Init`, `DPC_Display_*` | high / medium |
| GPIO | PC10, PC11, PC12, PC14, PC15 push-pull outputs, low at start; PC13 input (never read) | PC11 = control-interrupt marker; PC12 = fan relay (`RELAY_FAN`, set at the end of INIT); PC14 / PC15 = ADC1 / ADC2 DMA-complete markers; PC10 not driven | `MX_GPIO_Init`, `HAL_TIM_PeriodElapsedCallback`, `HAL_ADC_ConvCpltCallback` | high |
| debug DAC | `DPC_DAC_Init(12, 3, 4, gain 2048 x3, bias 2048 x3)` | the signal selection was optimised away, the DAC outputs a constant; TIM7 (its trigger) is never started | `Debug_DATA_DAC` | high |
| option bytes | BOOT_LOCK = 0x10000 | see 3.9 | `DPC_MISC_OB_Init` | medium |
| fan | `RELAY_FAN` = 0x80 -> GPIOC pin 12 | on after about 4 s | `DPC_FSM_INIT_Func` | high |

### 3.12 Named in the code but not decodable from the image

| name | why |
|---|---|
| `fDAB_VDC_RefNext_V`, `DAB_CTRL_State` (after start-up), `ModTecnique`, `PhSh_CTRL_MAN`, `TpzDuty1/2_CTRL_MAN`, `VdcCTRL_Reset` | RAM variables written at run time by a debugger / monitor; the image only holds their zero start values. The operating targets ST used (output voltage, power) are therefore **unknown** |
| `InitMode` (HV2LV / LV2HV / BIDI) | zero-initialised and never read |
| `DPC_Application_Conf.h`, `DPC_Lib_Conf.h` constants | the macro names are not in the DWARF; only the values that reached the code were decoded |
| `phi_rad_max_trian`, `phi_rad_max_trap`, `Pmax_sps`, `Pmax_tpz`, `PhSh_CTRL_rad` | computed (or declared) but never consumed; they have no stored value, only formulas (3.7) |
| ADC offsets of the current channels, sensor gains of the board | only the firmware's assumed counts-per-unit are present; the real sensors are not |

---

## 4. What cannot be known from the binary

* **Operating points and tests.** The voltage target, the power levels, the sequence ST used to start the converter and the results (efficiency, ZVS range, waveforms) are not in the image: the targets are RAM variables that the image leaves at zero (3.6, 3.12). The 25 kW, 800 V in / 400 V out, 98.4 % figures in `README.md` come from web-search summaries and are not confirmed by the binary; the constants fit 800 V : 449 V (n = 1.78), the range of port 2 is not stated anywhere in the image.
* **Intent and rationale.** No source, comments, macro names or configuration headers (`DPC_Application_Conf.h`, `DPC_Lib_Conf.h`). Why n = 1.78, L = 28 uH, the 0.4 phase limit, 10 V/s, 400 ns or the PI gains were chosen is not recorded; the same holds for whether the 600 ns -> 400 ns change between the `.sim` and the `.hex` build was driven by a measurement.
* **Hardware.** Gate drivers, DESAT / over-current / over-voltage comparators, sensor types, divider ratios and the schematic are not visible. The firmware assumes 3.901 / 6.206 counts per volt and 102.4 counts per ampere; whether the board matches is unknown (for scale: 25 kW at 800 V is 31 A, calculated, which is above the +/-20 A scale of the firmware constants). Hardware protections on the board cannot be excluded: only the absence of any software protection and of any HRTIM fault configuration is established.
* **Waveforms.** The HRTIM register programming is decoded (3.3), but the resulting leg waveforms (what exactly D1, D2 and PS mean in time, and the effect of rewriting MCMP4 that also triggers the ADC) were not derived or simulated; ST's own definitions (UM3198) are needed.
* **Dynamics.** Loop bandwidth, phase margin and the plant (port-2 capacitance, load) are unknown; ISR execution time, jitter and CPU load were not estimated (software double precision inside the 10 kHz interrupt).
* **Pin and channel naming** (PA7, PC4, PC0, PC1, PB2, PC2, the HRTIM pins) comes from the G474 alternate-function table, not from the binary; the ADC EXTSEL value is decoded but its HRTIM-trigger number is in the reference manual.
* **Identity of the release.** No version string and no build time stamp (1.3); the ST page was not readable. Whether this image is the one UM3198 describes, and which of the `.hex` / `.sim` ST considers current, is not established.
* **Missing code.** Anything in `DPC_Math`, `DPC_PLL`, `DPC_Telemetry`, `DPC_Transforms` and the library's fault-set / burst / inrush code is absent from the image, so ST's complete library behaviour (and how a fault is normally raised) cannot be reconstructed.
* **Runtime behaviour that depends on writes** (closing the loop, mode, manual values, `VdcCTRL_Reset`) - see 3.12.

---

## 5. Against our DAB control

Basis. ST column: decoded values of this notes (section 3, `.hex`/`.out` build). Our column: `sim/out/dab_control/report.md` and `metrics.json` (calculated, not bench-validated), plus the design values they point to in `sim/out/dab_design/dab_spec.json` and `report.md`, and `sim/dab_control.py` for the PI structure that the report does not spell out (marked *script*).
Ours: **60 kW, 100 kHz**, port 1 590-950 V, port 2 400-900 V, n = 11:12 = 0.9167, L = 6.5 uH (primary referred), C2000 F28388D. ST: **25 kW** (README), 100 kHz, n = 1.78, L = 28 uH, STM32G474 + HRTIM.
Scaling used where ratings differ (all **calculated** with the SPS equation P = n V1 V2 d (1 - d) / (2 f L), d = phi / pi, from each side's own constants): power x 2.4 (60/25); SPS maximum power / rated power: ST 28.6 kW / 25 kW = 1.14 at 800 V / 449 V, ours 112.8 kW / 60 kW = 1.88 at 800 V / 800 V; phase needed for the rated power: ST 58.2 deg (800 V / 449 V), ours 28.4 deg (800 V / 800 V; our report gives 25.6 deg at 800 V / 873 V, which matches this formula).
Verdicts: **SAME APPROACH** / **DIFFERENT** (how) / **ST HAS, WE DO NOT** / **WE HAVE, ST DOES NOT** / **UNKNOWN**. Facts only; no recommendation is made here.

| # | item | ST (decoded) | ours (source) | verdict and how it differs |
|---|---|---|---|---|
| 1 | rated power, ports | 25 kW; constants fit 800 V : 449 V, ADC full scale 1,050 V / 660 V | 60 kW; P1 590-950 V, P2 400-900 V; sensing 0-1,200 V / 0-1,100 V (spec) | **DIFFERENT** (ratings). Full scale over the port maximum: ST HV 1.31 x nominal, LV 1.47 x the 449 V match; ours 1.26 x (1,200/950) and 1.22 x (1,100/900) |
| 2 | switching frequency | 100 kHz fixed (3 independent copies of the number) | 100 kHz fixed | **SAME APPROACH** (fixed frequency, no frequency modulation) |
| 3 | series L and turns ratio | L = 28 uH, n = 1.78 (multiplies the port-2 voltage; M = n VLV / VHV = 1.00 at 800 V / 449 V) | L = 6.5 uH, n = 11:12 | **DIFFERENT** (ratings; see normalised numbers above). In ST's controller L only feeds the informational Pmax; n enters the modulation formulas |
| 4 | regulated variable, outer loop | PI on the port-2 voltage (raw count of VDAB2) -> phase shift | CV loop on V2 (port 2) -> current reference (*script*, report) | **SAME APPROACH** (outer voltage loop on port 2 acting through the phase shift) |
| 5 | inner current loop | none; no current is used by any control, limit or protection code | per-branch inner current loop on the branch's own I2 sensor: SPS feed-forward + integral term (clamped +/-0.3 rad), crossover f_ci = 2 kHz (*script*, report) | **WE HAVE, ST DOES NOT** |
| 6 | feed-forward | none | SPS feed-forward in the current loop (inverse of P = n V1 V2 phi (pi - phi) / (2 pi^2 f L)) and load-current feed-forward in the CV loop (*script*) | **WE HAVE, ST DOES NOT** |
| 7 | PI form and anti-windup | float parallel PI; output clamp [0, 0.4]; back-calculation anti-windup Kb = 0.1 | CV-loop PI whose integrator stops while the CC limit is active (conditional integration), current-loop integrator clamped +/-0.3 rad (*script*); phase command clamp +/-60 deg (report assumption) | **DIFFERENT** (back-calculation vs conditional integration; clamp 72 deg one-sided vs 60 deg two-sided) |
| 8 | PI gains / bandwidth | Kp = 0.001 per count, Ki = 0.1 per count per s, Ts = 100 us; zero 15.9 Hz; 6.2e-3 per V (1.12 deg/V) and 0.62 per V per s (calculated) | designed from crossover: voltage loop f_cv = 400 Hz with kp = 2 pi f_cv C2 and PI zero at f_cv / 5 = 80 Hz; current loop f_ci = 2 kHz (*script*) | **DIFFERENT** numbers; the crossover of ST's loop is **UNKNOWN** (needs port-2 capacitance and load) |
| 9 | control update rate | 10 kHz (TIM2); newest samples, ADC2 channels up to about 50 us old | every switching period (100 kHz) with a one-period delay (model assumption) | **DIFFERENT** (ST updates every 10th period) |
| 10 | measurement timing | 12-bit ADC triggered by HRTIM master CMP4, whose value is rewritten every cycle with the bridge-2 compare (D2 + PS); ADC2: 5 ranks, 1 conversion per trigger, circular DMA | not described in the control report (model samples once per period); sensing parts in the spec | **UNKNOWN** for our sampling instant; ST's moves with the commanded phase shift |
| 11 | modulation | SPWM (SPS), TPZ (closed-form trapezoidal: two inner shifts, sum = phi), MAN, TRG declared but not implemented; **selected by writing a variable** | SPS near V1 = nV2 and at heavy load; EPS / DPS / TPS from an offline LUT (a1, a2, phi vs V1, V2, P), selected by the operating point (the control simulation itself models SPS only) | **DIFFERENT** (closed-form vs LUT; manual vs automatic) |
| 12 | trapezoidal inner shifts computed in firmware | formulas of 3.7 from VHV, VLV, n, phi every 100 us | LUT (no closed form in firmware) | **ST HAS, WE DO NOT** (the closed-form calculation) |
| 13 | on-line maximum-power calculation | `Pmax_sps`, `Pmax_tpz` computed every cycle, never used | per-branch limit P_branch,max / V2 from the offline derating map, used as a clamp | **DIFFERENT** (ST informational and unused; ours a hard limit); **ST HAS, WE DO NOT** the on-line formula |
| 14 | phase limit | 0.4 pi = 72 deg, positive only in loop mode; 27.4 kW at the limit at 800/449 V = 1.10 x rated (calculated) | +/-60 deg; 100.3 kW at the limit at 800/800 V = 1.67 x rated (calculated) | **DIFFERENT** |
| 15 | phase slew limit | none between PI and compare registers | 3 deg per switching period | **WE HAVE, ST DOES NOT** |
| 16 | split (volt-second-balanced) phase update | none; the four master compares are rewritten at once | split update; naive step 46.9 A vs 4.3 A DC offset in the simulation (metrics) | **WE HAVE, ST DOES NOT** |
| 17 | transformer DC-bias / flux balance | none | firmware flux-balance loop on the averaged transformer current, hardware trip at 5 A / 1 ms (spec) | **WE HAVE, ST DOES NOT** |
| 18 | dead time | fixed 400 ns (136 x 2.941 ns), same for both bridges, HRTIM insertion, complementary outputs; 600 ns in the `.sim` build; = 14.4 deg of the period | adaptive LUT = predicted ZVS transition + 20 ns, 50-300 ns (1.8-10.8 deg), fallback fixed 100 ns (3.6 deg), set in the C2000 dead-band / HRPWM (spec) | **DIFFERENT** (fixed vs adaptive; 4 x our fallback) |
| 19 | dead-time phase-drift handling | none | absorbed by the current loop; the LUT sets the starting point (design report) | **WE HAVE, ST DOES NOT** |
| 20 | PWM time resolution | HRTIM: 183.8 ps per count = 0.0066 deg | C2000 ePWM / HRPWM (resolution not stated in the files read) | **UNKNOWN** |
| 21 | soft start / reference ramp | voltage reference ramp +/-10 V/s (3.33 mV per 3 kHz tick), from a 50 V boot value; 0 to 449 V takes 45 s | current reference ramp 20 kA/s, 0 -> 75 A in 5 ms in the test (report) | **DIFFERENT** (slow voltage ramp vs fast current ramp) |
| 22 | pre-charge / inrush | none in firmware; the only actions are a fan relay after 4 s and the PWM enabled in `main` | 220 ohm resistor + contactor pre-charge, main contactor at dV <= 10 V, DAB enabled only after the port block reports it (spec, report) | **WE HAVE, ST DOES NOT** |
| 23 | start-up sequence | fixed timeouts 3 s (WAIT/IDLE) + 1 s (INIT) + 1 s unused; states WAIT, IDLE, INIT, START, RUN, STOP, ERROR, FAULT | event-based: precharge complete and contactor closed, then enable (spec) | **DIFFERENT** |
| 24 | power reversal | closed loop only toward port 2 (`PIsat_down` = 0); a negative phase only by manual value in open loop | +70 A -> -70 A in 1 ms through phi = 0 with no loss of control (report) | **WE HAVE, ST DOES NOT** (closed-loop reversal) |
| 25 | parallel branches | single module | 1-4 branches (+1), common reference over CAN-B + per-branch current loop, free-running carriers | **WE HAVE, ST DOES NOT** |
| 26 | communication for control | none (no CAN, UART2 initialised but unused) | CAN-B module bus, CAN-A (BMS), CAN-FD, Ethernet (REQUIREMENTS ECO-02) | **WE HAVE, ST DOES NOT** |
| 27 | over-voltage | none active; stored limits 550 V (3,413 counts) and 1,000 V (6,206 counts, above the ADC range), never read | firmware 960 V (P1) / 920 V (P2); hardware 1,000 V / 950 V (spec) | **WE HAVE, ST DOES NOT** |
| 28 | over-current | none active; stored 100 A limit = 12,288 counts (beyond the 4,095-count range and the +/-20 A scale) | transformer trip 210 A (200-220 A band), port 115 A, DESAT 7 V / 300 ns blanking (spec) | **WE HAVE, ST DOES NOT** |
| 29 | under-voltage | none | P1 580 V, P2 380 V (spec) | **WE HAVE, ST DOES NOT** |
| 30 | over-temperature, cooling | none (`T_ext` stored, never compared; it duplicates VDAB1) | module NTC trip 110 degC, coolant inlet derating 50 degC, flow trip 6 L/min (spec) | **WE HAVE, ST DOES NOT** |
| 31 | fault action and path | FSM ERROR -> FAULT disables the 8 outputs, but only if `uwFaultErrorVector` is set, which nothing in the image does; no HRTIM fault input; hard-fault handlers loop with the PWM running | firmware OV sets phi to zero within one switching period, cycle-by-cycle via CMPSS / trip zone, hardware backup 63 us (spec) | **WE HAVE, ST DOES NOT** |
| 32 | burst / light-load mode | types and parameters (20 kHz, 0.5 A, 1.2 A) exist, no code | not in the files read | **UNKNOWN** (ours); ST's is inactive |
| 33 | manual / open-loop operating modes | OPEN_LOOP with `PhSh_CTRL_MAN`; MAN with manual duties and phase | not in the files read | **UNKNOWN** (ours) |
| 34 | bring-up aids | OLED status text, LED PWM patterns, GPIO timing marks for the control and DMA interrupts, a debug-DAC stub | not in the files read | **UNKNOWN** (ours) |

Where ST's numbers could not be put on a common scale: the PI gains (no plant), the HRTIM leg timing (waveforms not derived) and the sensor scales (hardware unknown) - see section 4.
