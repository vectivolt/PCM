# Megarevo PMA series user manual — module-level statements (competitor notes)

- **Source:** `pma_user-manual_20250327-v2-00.pdf` ("PMA SERIES USER MANUAL", 45 PDF pages, 18,683,363 bytes, sha256 `5ab52833fcf519dbad693e051490d9e1e8ccf07a52f5d01d54c99e754d50ea65`; the manifest row in `docs/SOURCES.csv` carries the same hash). Origin: https://www.megarevo.com/upload/download/PMA-Standard%20Neutral-A4-20250327-EN-V2.00_A.pdf, linked from the PMA product page. The cover prints "VER: V2.10"; the site file name and our file name say V2.00 (an inconsistency inside the manual).
- **Retrieved:** 2026-10-04. **Read:** 2026-10-06 — text layer with `pdftotext -layout`, plus the page images for every figure and software screenshot (pp. 7-21 and 27-35; screenshots read at 300-450 dpi).
- **Status:** competitor's *published* statements, not measured and not verified by us. Sections 1-5 restate the manual. Sections 6-8 compare it with the datasheets and with itself and list what it does not say. Section 9 is arithmetic on the published figures and is labelled as ours. No design claim or requirement of ours appears in this note.
- **Wording:** paraphrased, not quoted. Connector labels, pin names, menu and parameter names (with the manual's own spelling) and numbers are reproduced as printed.
- **Page references:** `p.N` is the PDF page (1-45). The manual's printed page number is N − 2 for pp. 3-44. `§` is the manual's section number. *fig.* marks a statement taken from a drawing or photograph, *scr.* one taken from a software screenshot; unmarked statements are text or tables.
- **Scope:** modules only. The manual covers PMA0050, PMA0060, PMA0080B / PMA0080F and PMA0105B / PMA0105F (the cover lists six model codes). **It does not cover the 125 kW module**: the only 125 kW mention is one code in the model-name legend (p.7); the specification tables, the air-duct table, the breaker table and the cable table all stop at 105 kW. The PMA0125 / PMA0135 figures are in `spec-pma0125.md` and in the datasheet. Cabinet, rack, mechanical and touch-screen content is skipped except where it states a module interface or requirement.
- **Software screenshots:** the connected PC-tool screenshots (pp. 29-30) come from a unit whose status bar reads PMA-62.5kW, DSP version V001B001D017, protocol version V001B000D003, serial 9600-8-N-1. Parameter ranges read from them belong to that 62.5 kW unit.

---

## 1. Electrical interfaces of the module

### 1.1 What the drawing puts inside the module (Figures 3-7 and 3-8, p.10, *fig.*)

The block drawing runs from the DC terminals to the AC terminals:

| # | Block shown | Note |
|---|---|---|
| 1 | DC+ and DC- terminals, one DC fuse in each pole | |
| 2 | DC SPD (surge arrester) to PE | on the terminal side of the DC fuses |
| 3 | "Soft start" block; the label "DC contactor" sits under it | no timing or sequence is given anywhere in the manual |
| 4 | DC EMC filter | |
| 5 | "ISO" element across the two DC lines | the parameter tables list insulation-resistance detection as integrated (pp.39, 43) |
| 6 | DC link: two capacitors in series with a midpoint | |
| 7 | Three-level converter ("Three-Level PCS") | the model-name legend calls the module a DC/AC single-stage converter (p.7) |
| 8 | LCL filter, then AC EMC filter | |
| 9 | AC contactor, three poles drawn, with a "GFCI" ring around the phase lines | |
| 10 | One AC fuse in each phase, then terminals A, B, C (and N in Figure 3-8) | |
| 11 | AC SPD to PE | on the terminal side of the AC fuses |

Features text (§3.1.5, p.10): the three-level topology gives a wide charge and discharge voltage range when several units run in parallel; mid-point balancing can adjust the DC component and the low-frequency pulsating current on the "bypass bus" (as printed); SPD, ISO and GFCI monitoring are fitted and coordinated with the electrical protection; the module is a standard rack-mount design.

### 1.2 Power terminals

| Module | DC terminals | AC terminals | PE | Pages |
|---|---|---|---|---|
| PMA0050, PMA0060 | at the back; supplied terminal block type DL17Z-5 on the right side; DC+ above DC- | at the back; terminal block type DL17Z-5 on the left side; A, B, C from right to left; N is the upper position in the C column (*fig.* labels N above C) | chassis grounding point at the right-hand end of the back panel, next to the DC connector (*fig.* pp.12, 27); the text says beside the AC terminal (p.26) | pp.11-12, 24, 26-27 |
| PMA0080B, PMA0105B (back wiring) | at the back; M6 screws; DC+ then DC- from right to left | M8 screws; A, B, C, N from right to left | grounding stud next to the AC block | pp.14-15, 26-27 |
| PMA0080F, PMA0105F (front wiring) | at the front; M6 screws; DC- then DC+ from right to left | M8 screws; N, C, B, A from right to left | same | pp.15, 26-27 |

- Connector type, from the parameter tables: 50/60 kW quick connector that supports hot plug (p.40); 80/105 kW OT/DT terminal, permanently connected (p.43).
- *fig.* (pp.12, 27): the 50/60 kW power connectors are hybrid blocks with a small multi-pin block between the power contacts; the manual does not label or describe it. The 80/105 kW photographs show shrouded red (DC+) and black (DC-) lugs and a four-pole AC barrier strip (pp.9, 15).
- Ground conductor (Table 4-5, p.25): at least 16 mm² (50 kW), 25 mm² (62.5 kW), 35 mm² (80 and 105 kW).
- Where copper meets aluminium the manual asks for a dedicated copper-aluminium connector (p.26).

### 1.3 Neutral handling

| Statement | Page |
|---|---|
| Every module supports two grid types, 3W+PE and 3W+N+PE, with a separate DC window for each: 3W+PE operating 590-950 V, full load 600-900 V; 3W+N+PE operating 650-950 V, full load 680-900 V | pp.37, 38, 41 |
| The 80/105 kW AC port has four positions (A, B, C, N); on the 50/60 kW module N is the upper position in the C column | pp.9, 12, 15 |
| Single module: connect grid A, B, C and leave the N port unconnected | pp.12, 15 |
| Several modules on one common battery: join the N terminals of the modules to each other and do not connect them to the grid neutral | pp.12, 15 |
| Several modules on separate batteries: leave the module N lines unconnected from one another and from the grid neutral | pp.12, 15 |
| Figure 3-8 (3W+N+PE), *fig.*: the DC-link capacitor midpoint is wired to the N terminal, through the LCL and AC EMC filters; no contactor or fuse symbol is drawn in that line; the GFCI ring is drawn around the three phase lines and its lower tip touches the N line, so the drawing does not settle whether N passes through the ring. Figure 3-7 (3W+PE): the midpoint goes only to the converter's own neutral-point input | p.10 |
| Cable table: no N cable for a single module; for paralleled modules the N cable has the cross-section of one phase conductor (35 / 50 / 70 / 70 mm² for 50 / 62.5 / 80 / 105 kW) | p.25 |
| Off-grid ratings: L-N 220 / 230 / 240 V, L-L 380 / 400 / 415 V, 100 % three-phase unbalanced load | pp.38-39, 42 |

In every wiring case the manual describes, N is not tied to the grid neutral. The manual does not describe how N is connected to the load neutral in off-grid operation, and does not say whether the N conductor passes through the residual-current ring.

### 1.4 Control and signal connectors

| Connector or item | Modules | What the manual says | Page |
|---|---|---|---|
| READY switch (rocker, ON / OFF) | all | ON marks the module as installed and connected in its cabinet; OFF is the as-shipped position of a module not yet connected | p.13 |
| LEDs RUN (green), ALM (red) | all | RUN steady: operating normally; RUN flashing: standby or starting; ALM steady: module fault; both steady: software upgrade in progress | p.13 |
| USB | all, front panel | listed among the signal ports; function not described (the PC tool is connected through a USB-to-RS-485 converter on a COM port, not through this port) | pp.12, 16-17 |
| COM1, COM2 (two stacked RJ45) | all | BMS CAN, BMS RS-485, battery-fault signal, EMS RS-485; both sockets have the same pinout; used for the PC tool and for module chains | pp.13-14 |
| ETH1, ETH2 (stacked RJ45) | all | Ethernet; pinout below | p.14 |
| Termination DIP | 50/60 kW: one switch (EMS_485); 80/105 kW: four switches (BMS_485, BMS_CAN, EMS_485, one reserved) | matching-resistance (termination) switches; the table text for switch 1 repeats the EMS_485 wording | pp.13, 16 |
| 8-way pluggable terminal block | 80/105 kW only | *fig.* labels: top row NO, COM, NC; bottom row 5V, DI1, 5V, DI2. No text, rating or function is given for these terminals | p.17 |
| COM3 (stacked RJ45) | 80/105 kW only, parallel panel | module-to-module CAN, EPO pair, monitor CAN | p.18 |
| COM4 (stacked RJ45) | 80/105 kW only, parallel panel | synchronisation lines | p.19 |
| 9-way DIP | 80/105 kW only, parallel panel | 4 address bits and 5 termination switches | p.19 |
| Monitor power port (4-way pluggable) | 80/105 kW only, parallel panel | M_12V, M_GND, M_12V, M_GND; feeds the optional monitor and is chained from module to module; no current rating, direction not stated | pp.17, 20-21 |
| Fan, temperature sensor, debug connector | — | none described. Four fans are visible on the front-panel photographs (pp.9, 12, 16); the PC tool shows IGBT and ambient temperature readings (p.29) | — |

RJ45 pinouts (TIA/EIA-568-B wiring per the manual; the two sockets of a stacked connector are identical):

| Pin | COM1 / COM2 (p.14) | ETH (p.14) | COM3 (p.18) | COM4 (p.19) |
|---|---|---|---|---|
| 1 | BMS_CANH | NET_TX+ | POWER_CAN_H (parallel communication H) | Carrier_sync_H (high-frequency synchronisation H) |
| 2 | BMS_CANL | NET_TX- | POWER_CAN_L | Carrier_sync_L |
| 3 | BAT_FAULT (battery failure signal) | NET_RX+ | COM_GND | COM_GND |
| 4 | BMS_485A | PE | EPO+ (emergency power outage H) | Sync_H (low-frequency synchronisation H) |
| 5 | BMS_485B | PE | EPO- (emergency power outage L) | Sync_L |
| 6 | GND_COM (signal ground) | NET_RX- | COM_GND | COM_GND |
| 7 | EMS_485A | PE | MONITOR_CAN_H | SER_H (low-frequency discrete H) |
| 8 | EMS_485B | PE | MONITOR_CAN_L | SER_L |

### 1.5 Communication ports: roles and protocols

| Role | Port and pins | What the manual says | Page |
|---|---|---|---|
| BMS link | COM1 / COM2 pins 1-2 (CAN) and 4-5 (RS-485); pin 3 BAT_FAULT | BMS communication is RS-485 or CAN, optional; termination by the BMS_CAN and BMS_485 switches (80/105 kW) | pp.14, 16, 39, 42 |
| EMS link | COM1 / COM2 pins 7-8 (RS-485); ETH1 / ETH2 | EMS communication is RS-485 or Ethernet, optional. The monitor's log shows an EMS communication method setting (label in Chinese, my translation) taking the value CAN (*scr.*), which the table rows do not list | pp.13-14, 33, 39, 42 |
| PC commissioning tool (PMA.exe) | COM1 / COM2 pins 7-8 through a USB-to-RS-485 converter | bit rate 9600 (text); 8 data bits, no parity, 1 stop bit (*scr.* status bar "9600-8-N-1"); the tool lists module IDs 1 to 15 | pp.14, 28-30 |
| Modbus | serial or TCP | *scr.*: the connection dialog offers a serial port or a "remote Modbus server" (IP address, port 502, IPv4 / IPv6), HEX or ASCII mode, response time-out 5000 ms, poll delay 20 ms; the monitor's Version page lists a Modbus protocol version (V1.1) and its network page shows port 502. The text never names RTU or TCP and gives no register map | pp.29, 33 |
| CAN | COM1 / COM2 (BMS), COM3 (module bus, monitor) | no bit rate and no higher-layer protocol is named. *scr.*: the monitor's Version page lists an "Internal CAN protocol version" | pp.18, 33 |
| Ethernet | ETH1, ETH2 | RJ45 with PE on pins 4, 5, 7, 8; no speed is stated; *scr.*: the monitor's network page offers static or DHCP addressing | pp.14, 33 |
| Optional monitor (HMI) | COM3 pins 7-8 to the monitor's CAN1 H / L; monitor power port | the cable from the module's COM3 goes to the monitor; *scr.*: the monitor has a 4G modem (connection, signal, network mode) and an Ethernet interface | pp.18, 20, 33 |
| Module bus | COM3 pins 1-2 (POWER_CAN), COM4 | see 1.7 | pp.18-19 |
| Table rows | — | communication interface Ethernet / RS485 / CAN; human-machine interface 10.1 in touch screen, local web or upper computer, optional | pp.39, 42 |

### 1.6 Emergency power-off

- An EPO pair (EPO+, EPO-) is on COM3 pins 4-5 and is chained from module to module with the COM3 cable (pp.18, 20).
- The monitor lists an active EPO as an alarm (shown for two modules as word 5, bit 5) and shows "Stop by EPO" in its status bar (*scr.* p.34).
- Not stated: contact type (normally open or closed), voltage, current, isolation, and whether EPO acts on the contactors or only on the converter.

### 1.7 Parallel operation (80/105 kW unless stated)

| Topic | Statement | Page |
|---|---|---|
| Module count | up to 14 modules: four address bits in binary (bit0 least significant), codes 0000 and 1111 not used; Table 3-1 prints the codes for modules 1 to 6 | p.21 |
| Roles | one master module (drawn as "Host") and one terminal module (drawn as "Last") with modules in between; the manual does not say which address is the master | pp.20-21 |
| Cabling | COM3 to COM3 and COM4 to COM4, daisy-chained module to module with the standard network cable. In the PC-control variant, COM2 of one module goes to COM1 of the next (RS-485 chain) | pp.20-21 |
| Termination | 120 Ω resistors are drawn at the Host and the Last module (*fig.*); the DIP switches PCAN, MCAN, HSYNC, LSYNC and SER switch them in | pp.19-20 |
| Switch settings, monitor control | master: MCAN on; terminal module: PCAN and MCAN on; common battery: HSYNC, LSYNC and SER on at master and terminal module; separate batteries: those three off | p.20 |
| Switch settings, PC control | master: PCAN on; same HSYNC / LSYNC / SER rule; the EMS_485 switch (switch 3 of the signal DIP) on at every module | p.21 |
| Which cables are made | monitor control: the COM3 and COM4 chain is always made. PC control: the COM3 and COM4 chain is made only when the DC sides share a battery; with separate batteries it is not needed | pp.20-21 |
| 50/60 kW | parallel operation by cabling the COM ports of the modules to each other; no COM3, COM4 or synchronisation wiring is described or visible on the 50/60 kW front panel | pp.12-13 |
| Software | Parallel Operation Enabled (Disable / Enable) | pp.30-31 |
| Off-grid | paralleling the AC outputs of several modules in off-grid operation: consult the manufacturer | p.11 |
| Not stated | power-sharing method, synchronisation accuracy, cable length limit, behaviour on loss of the master, mixing module sizes | — |

The two procedures are worded for "more than 2 modules" (monitor control) and for "2 modules" (PC control); the intent of the difference is not explained.

---

## 2. Auxiliary power and start-up

### 2.1 Control power

**The source of the module's own control power is not stated.** The manual does not say whether it comes from the DC side, the AC side or an external supply. The related statements are:

- Start-up closes the external AC and DC breakers first, and only then connects the PC tool or the monitor and logs in (pp.28, 32). No step mentions an auxiliary supply.
- The delivery list and the optional-accessory list contain no auxiliary supply and no auxiliary cable (Tables 4-1 and 4-2, p.24).
- The module has a 12 V monitor power port for the optional monitor (p.20); current rating and direction are not stated.
- The safety text tells the reader to disconnect the converter's external connections and its internal power supply before work (p.36), and to wait at least 5 minutes after power-down because of stored energy (pp.4-6).

### 2.2 Start-up, soft start, black start

| Topic | Statement | Page |
|---|---|---|
| Soft start / pre-charge | only the "Soft start" block in the DC path of Figures 3-7 and 3-8 (*fig.*); no time, current limit or sequence is given | p.10 |
| Self-checks | AC relay automatic checking is listed as integrated; DC relay failure and AC relay failure are alarms; when the checks run is not stated | pp.37, 39, 43 |
| Standby | converter idle, a start command is accepted at any time; the RUN LED flashes in standby or while starting | pp.11, 13 |
| Automatic recovery | for grid over / under voltage, grid over / under frequency and the islanding alarm the manual says to wait for the module's own self-check to clear the alarm | pp.36-37 |
| Black start, or start from the grid with the battery disconnected | **no statement either way.** The nearest statements: the DC voltage must be inside the permitted range before starting (pp.28, 32); battery reverse connection and battery over / under voltage are fault entries (pp.36-37); the off-grid set points include a lower SOC limit and a lower voltage limit for the off-grid battery (p.30) | pp.28-37 |
| Shutdown | stop command (PC tool: Turn On/Off Command to Stop; monitor: Turn Off), then open the AC and DC breakers; the indicators go out when the converter has stopped | pp.34-35 |
| Work on the module | wait at least 5 minutes after power-down; disconnect AC and DC; measure to confirm discharge | pp.4-6 |
| Operator accounts | both software tools open with a default account printed in the manual (not reproduced here) | pp.28, 32 |

Start-up as the manual describes it:

| Step | PC tool (pp.28-30) | Monitor (pp.32-34) |
|---|---|---|
| 1 | check DC, AC and ground cables; DC voltage within the permitted range | same |
| 2 | connect the PC through a USB-to-RS-485 converter on a COM port | connect the monitor with the network and power cables of the delivery list |
| 3 | close the external AC and DC breakers | same |
| 4 | start PMA.exe, log in, select the connection, set 9600 bit/s | log in on the monitor |
| 5 | if no fault is active: set mode and parameters, set Turn On/Off Command to Run, press Write | if no fault is active: set parameters, then Setting, Turn On |
| 6 | set the power reference ACP Mode Reference: + discharges, - charges; range per model in Appendix 1 | Setting, Basic Setting, AC Power Reference, same sign convention |

---

## 3. Operating modes and settable parameters

### 3.1 Modes (§3.2, pp.10-11)

| Mode | Statement | Page |
|---|---|---|
| On-grid charge | constant voltage, constant current, constant power | p.11 |
| On-grid discharge | constant voltage, constant current, constant power | p.11 |
| Standby | converter idle, a start command is accepted at any time | p.11 |
| Off-grid | the converter supplies AC at constant voltage and frequency to the load; selected from the PC tool or the monitor | p.11 |
| Off-grid load limits | resistive load below the PCS rated power; loads of type "RCD" (expanded in the manual as resistor, capacitor, diode) below 60 % of the module apparent power, otherwise ask the maker; motor loads behind a VFD below 60 % of one module; motor loads without VFD: ask the maker; AC outputs of several modules in parallel: ask the maker | p.11 |
| On/off-grid transfer | not automatic: the series "for now" does not switch by itself; the converter is stopped and the mode is changed by hand from the PC tool or the monitor | p.11 |
| Charge-to-discharge transfer time | below 20 ms (a table row; not an on/off-grid transfer time) | pp.40, 43 |
| Grid type | the Grid Type (V) list shows China 400V; other entries are not shown. Grid-connection standards named: EN50549-1, EN50549-10, GB/T34120, GB/T34133 | pp.30, 40 |
| Battery chemistry | lithium-ion, lead-acid and DC-bus systems are supported; parameter sets exist for lithium and for VRLA | pp.10, 31 |
| Monitor "Mode Selection" | Manual / Scheduling, and Peak shaving with a setting for peak-shaving and valley-filling periods | p.31 |
| Overload (off-grid rows) | up to 110 %: continuous; 110 % to 120 %: 2 min; above 120 %: 200 ms | pp.39, 42 |

### 3.2 PC tool "Basic Parameters" page (*scr.* p.30, PMA-62.5kW unit)

Values are those shown in the screenshot; ranges are as printed, including the manual's spelling. Parameters are changed after Read and stored after Write.

| Parameter (as printed) | Shown value / range | Note |
|---|---|---|
| Parameter Setting | Basic Parameters | a selector; other groups are not shown |
| Date time(s) | 388 | unit s; meaning not explained |
| ACP Mode Reference [-1.2, 1.2] Pn (kW) | 15 | power reference, ± 1.2 × Pn; + discharge, - charge (text) |
| Battery voltage up limite [480, 960] V | 960 | |
| Battery voltage lower limite [480, 960] V | 590 | |
| Leakage Current Detection Enable | Disable | drop-down |
| DSP Reboot Command | NULL | drop-down |
| Lower SOC Limit For Off-Grid Battery [0, 100] % | 1 | |
| Battery type | NULL, Lithium | drop-down |
| CV Charging Current Limit [0, 120] V | 0 | unit printed as V |
| Battery charge current limite [0, 120] A | 120 | |
| Battery discharge current limite [0, 120] A | 120 | |
| Grid Type (V) | China 400V | drop-down |
| Turn On/Off Command | Stop, Run | |
| Battery Control Mode | AC power | drop-down; other entries not shown |
| Parallel Operation Enabled | Disable | drop-down |
| SOC up limite [0, 100] % | 100 | |
| SOC lower limite [0-100] % | 0 | |
| Parameter Download Command | Wait | |
| Lower Voltage Limit For Off-Grid Battery [480, 960] V | 590 | |

No power-factor or reactive-power reference, no voltage or frequency trip level, no reconnection delay, no ride-through setting and no derating setting appear among these parameters.

### 3.3 Monitor menu tree (*scr.* p.31)

- **Setting**
  - Mode Selection: Manual/Scheduling; Peak shaving (peak-shaving and valley-filling periods)
  - Control: Turn On; Turn Off; Standby
  - Basic Setup, Basic Parameters: Battery Type; AC Power Reference; Constant Voltage Charging Current Limit; Leakage Current Detection Enable; Parallel Operation Enabled; Parameter Download Command; Off-Grid Battery Voltage Lower Limit
  - Basic Setup, Battery settings, Lithium Battery Parameters: Battery Voltage Upper Limit; Battery Voltage Lower Limit; Battery Charging Current Upper Limit; Battery Discharging Current Upper Limit; Battery Voltage Protection Hysteresis; Battery SOC Upper Limit; Battery SOC Lower Limit; Off-Grid SOC Lower Limit
  - Basic Setup, Battery settings, Lead-acid Battery Parameters: VRLA Equalization Charging Voltage Upper Limit; VRLA Float Charging Voltage Upper Limit; VRLA On-Grid Discharging Voltage Lower Limit; VRLA Off-Grid Discharging Voltage Lower Limit; VRLA Equalization to Float Charge Current Threshold
- **Record**: energy statistics bar chart; real-time data curves; historical records; an Export Data button (p.33)
- **Log**: operation log (p.33)
- **Version**: monitor software version, Modbus protocol version, DSP version, protocol version, SN code; Upgrade; network port information; 4G information (pp.31, 33)
- **Date**: set date
- Home page: battery summary as uploaded by the BMS; per-module inverter data; grid totals. Status bar: alarm polling (alarm code and details) and inverter status

### 3.4 What the module reports (*scr.* pp.29, 32)

- PC tool, Analog page, module values: T_Charge generation and T_Discharge generation (counters; units not shown), IGBT temperature, leakage current (mA), ambient temperature, insulation resistance (kΩ), DC voltage, DC current, DC power, bus voltage, output voltage of phases A, B and C, output frequency.
- PC tool, Analog page, grid values: line voltages AB, BC, CA; line currents AB, BC, CA (as labelled); active, reactive and apparent power; grid frequency.
- PC tool, State page: PCS status text, a general fault clear button, soft alarm words 1 and 2, hardware alarm words 1 to 4, an alarm list and a history list.
- PC tool: a real-time curve view with serial-port and plot settings (labels partly in Chinese).
- Monitor home page: battery voltage, current, power and SOC; grid voltages and currents; power (p.32).

---

## 4. Protections, fault handling, records, upgrade and installation expectations

### 4.1 Alarm and protection list (Table 6-1, pp.36-37)

The manual says the PCS diagnoses abnormalities of the grid, the battery and itself, shows them on the backend and keeps them in a historical alarm log (p.36). **No trip level, delay time, ride-through time or reconnection time is given for any entry**, in this table or elsewhere. The advice column is paraphrased.

| Fault entry (as printed) | Handling advice in the manual |
|---|---|
| Output overcurrent | contact the service centre |
| Output instantaneous overcurrent | contact the service centre |
| DC bus overcurrent | contact the service centre |
| DC bus instantaneous overcurrent | contact the service centre |
| DC bus overvoltage / undervoltage / imbalance voltage | contact the service centre |
| Environment over temperature | shut down; check that the air duct is clear |
| IGBT over temperature | contact the service centre |
| Over-limit leakage current | shut down; check the grounding connection |
| Battery reverse connection | shut down; check the DC-side power cables |
| Battery overvoltage / undervoltage | shut down; check the DC voltage and the battery configuration |
| Grid overvoltage / undervoltage | wait for the module's self-check to clear the alarm; contact the service centre if it does not clear |
| Grid voltage reverse sequence | shut down; check the three-phase wiring |
| Grid over-frequency / underfrequency | as for grid voltage: wait for the self-check |
| Isolation island protection (as printed; our reading: anti-islanding) | as for grid voltage: wait for the self-check |
| DC relay failure | contact the service centre |
| AC relay failure | contact the service centre |
| Lightning arrester failure | contact the service centre |
| Module fan failure | shut down; check whether the fan is damaged |
| EMS communication failure | shut down; check the module and the communication wiring |

Also seen in a screenshot but not in the table: an EPO alarm (p.34). The table has no entry named for low insulation resistance, no entry for BMS communication loss and no entry for module overload; the PC tool does show an insulation reading in kΩ (p.29).

### 4.2 Protection rows of the parameter tables (pp.39, 43) and related statements

| Row | Value | Page |
|---|---|---|
| Protection class | Class I | pp.39, 43 |
| DC surge arrester | Type II | pp.39, 43 |
| AC surge arrester | Type II | pp.39, 43 |
| DC short-circuit protection | fuses plus DC contactor | pp.39, 43 |
| AC short-circuit protection | current control | pp.39, 43 |
| Residual current monitoring unit | integrated | pp.39, 43 |
| Insulation resistance detection | integrated | pp.39, 43 |
| AC relay automatic checking | integrated | pp.39, 43 |
| Over-voltage category | DC type II / AC type III (the 80/105 kW table swaps the labels of this row and the next) | pp.40, 43 |
| Pollution degree | external PD3, internal PD2 | pp.40, 43 |
| Protection degree | IP20 power compartment and IP5X control compartment (50/60 kW); IP20 (80/105 kW) | pp.40, 43 |
| Leakage current detection | a settable Enable parameter, shown as Disable; separately the manual leaves it to the user to decide whether to fit an external leakage-current protection device | pp.28, 30-31 |

### 4.3 Fault recorder, logs and alarm words

- **Fault-wave recorder (p.10):** local, on the module; stores waveforms for 100 ms before and 100 ms after the fault trigger; 32 recording channels; several groups are stored continuously. Not stated: sampling rate, storage size, number of records kept, trigger conditions, file format, how the records are retrieved.
- **Alarm words (*scr.* p.29):** two soft alarm words and four hardware alarm words, a general fault clear command, an alarm list and a history list.
- **Monitor alarms (*scr.* p.34):** the pop-up lists active alarms by module, word and bit (EPO appears as word 5, bit 5 on two modules); the status bar shows a stop reason ("Stop by command", "Stop by EPO", pp.32, 34).
- **Monitor log (*scr.* p.33):** columns Modify Type (Local or Cloud Platform), Modify Time and Event. The events shown are parameter changes: language, EMS and BMS communication type, battery type, SOC limit, time zone, fault clear, VRLA equalization-to-float current. It is a change log that also records whether the change came from the cloud.
- **Monitor record page (p.33):** energy statistics bar chart, real-time data curves, historical records, Export Data.

### 4.4 Firmware upgrade

- Both LEDs steady means the module is upgrading its software (p.13).
- The PC tool has a "PMA Upgrade" page in its left menu (*scr.* pp.29-30) and the monitor's Version page has an Upgrade button (*scr.* p.33).
- The manual gives no procedure, file type, tool version or interface for the upgrade and does not say whether it is local or remote. The datasheets claim remote upgrade; the manual text does not repeat the claim. The monitor has Ethernet and a 4G modem, and its log marks some parameter changes as coming from a "Cloud Platform" (p.33): a remote path exists for parameters, but a remote firmware path is not described.
- Version data the module reports: DSP version, protocol version, internal CAN protocol version, converter software version, serial number (pp.28-29, 33), written in a form such as V001B001D017.

### 4.5 What the installation must provide

Recommended breakers (Table 4-4, §4.3.1, p.25). The stated purpose is reliable cut-off from the battery and the grid in an emergency; the section is headed short-circuit protection device requirements.

| Module | DC output breaker | AC output breaker |
|---|---|---|
| 50 kW | 1000 Vdc / 200 A | 400 Vac / 200 A |
| 62.5 kW | 1000 Vdc / 200 A | 400 Vac / 200 A |
| 80 kW | 1000 Vdc / 300 A | 400 Vac / 250 A |
| 105 kW | 1000 Vdc / 300 A | 400 Vac / 250 A |

Recommended cables (Table 4-5, §4.3.2, p.25; "for reference only", to be chosen by ambient temperature, installation method and heat dissipation):

| Module | AC output, per phase | N cable, one PCS | N cable, paralleled | Ground cable | DC input, per pole |
|---|---|---|---|---|---|
| 50 kW | ≥ 35 mm² (printed "×3") | none ("/") | ≥ 35 mm² | ≥ 16 mm² | ≥ 50 mm² |
| 62.5 kW | ≥ 50 mm² (printed "×3") | none ("/") | ≥ 50 mm² | ≥ 25 mm² | ≥ 50 mm² |
| 80 kW | ≥ 70 mm² (printed "×3") | none ("/") | ≥ 70 mm² | ≥ 35 mm² | ≥ 70 mm² |
| 105 kW | ≥ 70 mm² (printed "×3") | none ("/") | ≥ 70 mm² | ≥ 35 mm² | ≥ 70 mm² |

Other installation statements:

- A battery-side disconnect is assumed: with it closed there is high DC voltage at the PCS DC port (p.22). Both an AC and a DC breaker outside the module are closed for start-up and opened for shutdown (pp.28, 32, 34-35).
- Grounding must be good and meet the local electrical code (pp.5, 22, 26).
- Install in a restricted-access area, indoors, with good ventilation, on a fire-resistant frame or wall, with escape routes clear and no flammable material nearby (pp.5, 23-24).
- Whether to install a leakage-current protection device is left to the user (p.28).
- **Not stated:** prospective short-circuit current at the AC or DC terminals, upstream fuse type or rating (only breakers are named), grounding system (TN, TT, IT), residual-current device type, cable temperature rating, short-circuit withstand of the module.

---

## 5. Environmental, cooling and derating statements

| Topic | Statement | Page |
|---|---|---|
| Operating temperature | −30 to +60 °C, derating above 45 °C; no derating curve or slope is given | pp.40, 43 |
| Altitude | up to 5000 m, derating above 3000 m; no curve is given | pp.40, 43 |
| Operating humidity | below 95 %, non-condensing | pp.40, 43 |
| Storage | −40 to +70 °C; relative humidity 0 to 100 % without condensation | pp.22, 40, 43 |
| Noise | below 70 dB; measuring distance not stated | pp.40, 43 |
| Cooling | intelligent forced air cooling; four front fans are visible on the photographs; fan count, type and life are not stated | pp.9, 12, 16, 40, 43 |
| Mounting | rack mounting, horizontal or vertical; air inlet and outlet must stay unobstructed | pp.23, 40, 43 |
| Clearance, horizontal mounting | at least 200 mm in front of the module (fan side), at least 800 mm behind it (wiring side) | p.23 |
| Clearance, vertical mounting | at least 800 mm below (fan side), at least 200 mm above (wiring side) | p.23 |
| Surface | the module surface gets hot in operation; install out of easy reach | p.23 |
| Maximum efficiency | 98.5 % | pp.40, 43 |
| Maintenance intervals | not stated. No air filter or dust filter is mentioned anywhere in the manual (the only filters named are the electrical EMC and LCL filters in the drawing). The maintenance content is the safety rules (pp.4-6) and the fan-failure alarm (p.37) | — |

Air-duct requirement for a single module (§4.1.3, p.23):

| Model | Ventilation quantity | Effective air inlet area |
|---|---|---|
| PMA0050 | ≥ 189 CFM | ≥ 0.0405 m² |
| PMA0060 | ≥ 236 CFM | ≥ 0.0405 m² |
| PMA0080 | ≥ 303 CFM | ≥ 0.054 m² |
| PMA0105 | ≥ 398 CFM | ≥ 0.054 m² |

---

## 6. Specification rows compared with the datasheets

Reference sheets: `pma-g2-80-105kw_datasheet_v1-0.pdf` (G2 80-105, V1.0) and `pma-125-135kw_datasheet_v1-0.pdf` (125-135, V1.0). To say which hardware generation the manual describes, the older sheets were also read: `pma-g1-50-60kw_datasheet_v1-2.pdf`, `pma-g2-50-60kw_datasheet_v1-0.pdf`, `pma-80-105kw_datasheet_v1-2.pdf`, `pma-105kw-us-standard_datasheet_v1-1.pdf`. Rows were compared by script (exact value strings after whitespace normalisation) and by eye.

**The manual covers the 50 to 105 kW modules. The 125 kW module is not in it** (see the header). Nothing below is extrapolated to 125 kW.

Rows with the same value in the manual and in the datasheets are not listed one by one: DC and AC power and current ratings, both DC windows, THDi, grid voltage range, off-grid voltage, accuracy and imbalance rows, overload tiers, communication rows, maximum efficiency, charge / discharge transfer time, humidity, temperature, altitude, noise, cooling, installation style. For the 80/105 kW modules these match the G2 80-105 sheet; for the 50/60 kW modules they match the G2 50-60 sheet.

### 6.1 Rows that differ, or exist only on one side

| Row | Manual | G2 80-105 V1.0 | 125-135 V1.0 | Remark |
|---|---|---|---|---|
| Grid frequency range | 50 ± 2 / 60 ± 2 Hz (pp.38, 41) | 50 ± 5 / 60 ± 5 Hz, according to local standards | same as G2 80-105 | every datasheet read prints ± 5 Hz |
| Adjustable power factor range | −1 to +1 (pp.38, 42) | > 0.99; −1 to +1 | same | the "> 0.99" part is not in the manual |
| Protection block | present: Class I; SPD Type II on DC and AC; DC short-circuit protection fuses plus DC contactor; AC short-circuit protection current control; residual current monitoring unit, insulation resistance detection and AC relay checking integrated (pp.39, 43) | no such rows; page 1 text names ground-fault monitoring, residual current monitoring and AC relay checking | same | new information in the manual |
| DC connector, 80/105 kW | OT/DT terminal, permanently connected (p.43) | quick-plug terminal (front-maintained); hot-plug quick connector (back-maintained) | quick-plug terminal (front); hot plug (back) | the manual matches the older V1.2 80-105 sheet |
| AC connector, 80/105 kW | OT/DT terminal, permanently connected (p.43) | OT/DT terminal (front-maintained); hot-plug quick connector (back-maintained) | quick-plug terminal (front); hot plug (back) | same remark |
| Dimensions W × D × H, 80/105 kW | 483 (444 without ears) × 680 × 174 mm, 4U (back wiring); × 220 mm, 5U (front wiring) (p.44) | 533.4 (491) × 650 × 220 mm, 21 in 5U | 690 (650) × 700 × 220 mm, 5U | the manual matches the older V1.2 sheet |
| Weight, 80/105 kW | 48 kg unpacked, 50 kg packed (p.44) | 55 kg | 75 kg | V1.2 and US sheets print 50 kg |
| Protection degree, 80/105 kW | IP20 only (p.43); 50/60 kW: IP20 power compartment, IP5X control compartment (p.40) | IP20 power compartment, IP5X control compartment | same | the 80/105 kW row matches the US-standard sheet |
| Over-voltage category and pollution degree | stated; in the 80/105 kW table the two labels are swapped (p.43) | OVC DC type II / AC type III; PD external 3, internal 2 | same | values agree once the swap is undone |
| DC voltage component, off-grid | no row (pp.39, 42) | no row | below 0.5 % Un of linear balance load | the G2 50-60 sheet also has the row |
| Standards | grid connection EN50549-1, EN50549-10, GB/T34120, GB/T34133; safety EN62477-1, EN62109-1, EN62109-2; EMC EN IEC61000-6-2, EN IEC61000-6-4 (pp.40, 41, 44) | none listed | none listed | the manual's list has no EN 50549-2 and no VDE 4105 |
| Storage humidity | 0 to 100 %, no condensation (p.22) | no row | no row | |
| Air-duct, breaker, cable and clearance tables | present (pp.23, 25) | absent | absent | |
| 125 kW module | not covered (code letter only, p.7) | — | the whole sheet | |

### 6.2 Which datasheet generation does the manual describe?

| Row | Manual | Older sheet | G2 sheet |
|---|---|---|---|
| 50/60 kW dimensions | 483 (444) × 550 × 133 mm, 19 in 3U (p.40) | G1 V1.2: same | G2 V1.0: 533.4 (491) × 650 × 174 mm, 21 in 4U |
| 50/60 kW weight | 35 kg unpacked, 38 kg packed (p.40) | G1 V1.2: 38 kg | G2 V1.0: 48 kg |
| 50/60 kW grid type and DC window | 3W+PE or 3W+N+PE, a window for each (pp.37-38) | G1 V1.2: 3W+PE only, 590-950 V | G2 V1.0: both, same windows as the manual |
| 50/60 kW off-grid rows | present (pp.38-39) | G1 V1.2: absent | G2 V1.0: present |
| 80/105 kW dimensions and weight | 483 × 680 × 174 / 220 mm; 48 kg unpacked, 50 kg packed (p.44) | V1.2 and US V1.1: same sizes; 50 kg | G2 V1.0: 533.4 × 650 × 220 mm; 55 kg |
| 80/105 kW connectors | OT/DT, permanently connected (p.43) | V1.2 and US V1.1: same | G2 V1.0: quick-plug or hot-plug variants |

Reading: the 50/60 kW part of the manual pairs the G1 mechanics with G2-style electrical rows; the 80/105 kW part follows the older V1.2 sheet in mechanics and connectors and the G2 sheet in electrical rows. The 480 V / 60 Hz US-standard version is not covered (the Grid Type list shows China 400V, p.30).

### 6.3 Datasheet statements with no counterpart in the manual

| Datasheet statement | In the manual |
|---|---|
| On-grid supports split-phase power control | not mentioned |
| Support for small-power diesel generator grid connection | no mention of a generator |
| Off-grid supports unbalanced and half-wave loads | the 100 % three-phase unbalanced row exists (pp.39, 42); half-wave loads and the DC voltage component have no counterpart |
| Supports remote upgrade, integrated local fault recorders | the local fault-wave recorder is described (p.10); remote upgrade is not described |
| Hot-plug connectors (80/105 kW G2, 125-135 kW) | 80/105 kW terminals are permanently connected; only the 50/60 kW module has hot-plug connectors (pp.40, 43) |
| Support for single-person installation (50-60 kW and US sheets) | the manual requires at least two qualified persons for manual transport (p.22); mechanics, not module electrics |
| Well established industrial IGBT power modules | only the IGBT over-temperature alarm and the IGBT temperature reading mention IGBTs (pp.29, 36) |

---

## 7. Not stated in the manual

The full text layer was searched for the terms below and every figure and screenshot page was read (pp.7-21, 27-35). The manual has **no statement** on:

- generator (genset) mode or its parameters (no hit for generator, genset, diesel);
- LVRT, HVRT or other ride-through settings, the anti-islanding method or its settings (only the islanding alarm entry in Table 6-1), droop or virtual-synchronous operation;
- per-phase or split-phase power control, power-factor or reactive-power commands, derating settings (only the two derating knees in the tables);
- trip thresholds and times for any protection;
- the source of the module's control power, the pre-charge sequence and its timing, black start, start from the grid with the battery disconnected;
- CAN bit rates and protocol, Modbus function codes and register map, Ethernet speed;
- electrical data of the EPO input, the NO / COM / NC output and the DI1 / DI2 inputs; the function of the USB port;
- fault-record count, sampling rate and retrieval; the firmware-upgrade procedure and tool; remote upgrade;
- grounding system (TN, TT, IT), residual-current device type, prospective short-circuit current, fuse types;
- derating curves, fan life, air or dust filters, maintenance intervals;
- the parallel power-sharing method, synchronisation accuracy, cable length limit, how the master is chosen, mixing module sizes;
- the 125 kW module and the 480 V / 60 Hz version.

---

## 8. Inconsistencies inside the manual (read the tables with care)

- The cover prints V2.10; the site file name and our file name say V2.00 (p.1).
- Figure captions on pp.8-9 name the 105 kW module PMA0150.
- p.41: the first rows of the 80/105 kW table carry a model header reading PMA0050 / PMA0060 above an EMC standard row.
- p.43: the pollution-degree and over-voltage-category rows have their labels swapped relative to p.40 and the datasheets.
- p.12 lists the 50/60 kW signal ports without ETH, although the photograph (p.12) and the pin table (p.14) show it.
- p.26 puts the ground point beside the AC terminal for all modules; Figures 3-9 and 4-2 (pp.12, 27) show it at the DC end of the 50/60 kW back panel.
- p.16: DIP switch 1 (BMS_485) is described with the EMS_485 wording; the wording for ON ("open") and the opposite position ("closed") sits oddly with the parallel procedures, which set terminations to "ON".
- p.19: the 9-way DIP table gives bit2 for both switch 3 (A2) and switch 4 (A3); its heading reads "BMS EMS communication port".
- p.21: the address rule allows 14 modules (code 1111 excluded) while the PC tool lists module IDs 1 to 15 (pp.29-30).
- p.30: the CV charging current limit carries the unit V.
- p.22: the supplier is once named Shenzhen MagneTek Technology instead of Megarevo.

---

## 9. Arithmetic on the published figures (ours; not Megarevo statements)

- **Airflow per rated kW:** 189 / 50 = 3.78, 236 / 62.5 = 3.78, 303 / 80 = 3.79 and 398 / 105 = 3.79 CFM per kW, one ratio across all four sizes (about 6.4 m³/h per kW with 1 CFM = 1.699 m³/h). 189 CFM is 321 m³/h and 398 CFM is 676 m³/h.
- **Power tiers:** maximum apparent power / rated active power is 1.20 for all four sizes (60/50, 75/62.5, 96/80, 126/105), equal to the ± 1.2 Pn range of the ACP Mode Reference; maximum continuous apparent power / rated power is 1.10, 1.10, 1.10 and 1.095.
- **Breakers against the module's maximum currents:** AC breaker / maximum AC current is 200/86 = 2.3 (50 kW), 200/110 = 1.8 (62.5 kW), 250/138 = 1.8 (80 kW) and 250/180 = 1.4 (105 kW); DC breaker / maximum DC current is 200/100 = 2.0, 200/125 = 1.6, 300/160 = 1.9 and 300/200 = 1.5.
- **Settable battery window against the stated operating window:** the settable range 480-960 V (p.30) starts 110 V below and ends 10 V above the 3W+PE operating window 590-950 V; the 3W+N+PE window starts at 650 V.
