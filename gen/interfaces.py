"""Inter-board connector contracts. Both ends of a cable import the same table, so a pin mismatch cannot be drawn.

`pins(TABLE, prefix)` returns {pin number: net name} for a KiCad connector symbol; names in SHARED keep their
name (power), every other signal gets the prefix (e.g. CTRL uses pins(CELL, "C1_") for cell port 1, the cell
board itself uses pins(CELL)).

Signal conventions
  *_P/*_N      RS-422 differential pair, 3.3 V logic at both ends (AM26LV31E driver / AM26LV32E receiver class)
  PWMn         CTRL -> power board gate command n (1 = switch on). 8 per port = 4 complementary pairs.
  FLT          power board -> CTRL, low = fault (DESAT / driver UVLO / local OC-OV). Receiver fail-safe: open cable = fault.
  EN           CTRL -> power board gate enable from the hardware fault chain; low forces every gate off and
               its rising edge clears latched driver faults.
  RDY          power board -> CTRL, 3.3 V logic, high only when all driver supplies are good. Driven push-pull
               from the power board's own 3.3 V (both power boards do) or open drain with the pull-up on the
               power board; CTRL only pulls DOWN (100 k), so an unplugged cable or a dead board reads "not ready".
  ID           resistor to GND on the power board; CTRL reads it on an ADC channel to identify the board. A port
               board also sums its proof-test readbacks onto this line as small level steps around its code.
  ANn_P/N      differential analog, low source impedance, up to +/-2 V differential on a 1.25-1.5 V common mode
               (isolated-amplifier or differential-driver output). A single-ended signal uses ANn_P against AGND.
  NTC_P/N      heatsink NTC, floating pair, biased on CTRL.
"""
SHARED = {"+24V", "+24V_GD", "GND", "AGND"}

# CTRL-C2000 <-> one power board (PVCELL-25, or a DAB60 bridge board). 2x20 2.54 mm shrouded IDC, ribbon 1:1.
CELL = ["+24V", "+24V", "GND", "GND",
        "PWM1_P", "PWM1_N", "PWM2_P", "PWM2_N", "PWM3_P", "PWM3_N", "PWM4_P", "PWM4_N", "GND", "GND",
        "PWM5_P", "PWM5_N", "PWM6_P", "PWM6_N", "PWM7_P", "PWM7_N", "PWM8_P", "PWM8_N", "GND", "GND",
        "FLT_P", "FLT_N", "EN_P", "EN_N", "RDY", "ID",
        "AN1_P", "AN1_N", "AN2_P", "AN2_N", "AN3_P", "AN3_N", "AGND", "AGND", "NTC_P", "NTC_N"]

# CTRL-C2000 <-> module port board (port voltages and currents). 2x13 2.54 mm shrouded IDC, ribbon 1:1.
#   VA / VB    port A / port B voltage on the converter side of the main contactor
#   VAX / VBX  the same ports on the terminal side (precharge-complete and welded-contact detection)
#   IA / IB    port currents;  AUX1 / AUX2  spare pairs (PV-PORT: insulation-measurement pole-to-PE voltages)
#   +24V       logic 24 V for the port board's sensors AND its hold-closed coil path: up to 0.7 A for up to 200 s
#              (a battery-type port holds two contactor coils).
#              CTRL feeds it through an electronic fuse (not a PTC) and biases every analog pair so that an open
#              conductor or an unpowered port board reads outside the trip window, not as a plausible 0 V / 0 A.
PORT = ["+24V", "+24V", "GND", "GND",
        "VA_P", "VA_N", "VAX_P", "VAX_N", "VB_P", "VB_N", "VBX_P", "VBX_N", "AGND", "AGND",
        "IA_P", "IA_N", "IB_P", "IB_N", "AUX1_P", "AUX1_N", "AUX2_P", "AUX2_N", "AGND", "AGND",
        "ID", "GND"]

# CTRL-C2000 <-> SYS-IO-AUX. 2x40 2.54 mm board-to-board. 3.3 V logic. Isolated transceivers, digital-input
# isolators, output drivers, the Ethernet magnetics and the hardware fault latch all live on SYS-IO-AUX.
#   +24V         protected / ORed 24 V from SYS-IO-AUX; CTRL makes its own low-voltage rails
#   CANA / SCIC  BMS port via the BMU-GW socket;  CANB = module bus;  MCAN = CAN FD service
#   SCIA / SCIB  isolated RS-485 ports 1 and 2 (TX, RX, driver enable)
#   ETH_*        100BASE-TX MDI pairs from the PHY on CTRL to the magnetics/RJ45 on SYS, plus two LED lines
#   DIn          isolated digital inputs, high = input energised
#   DOn          output commands; SYS gates them with the hardware chain before they reach a contactor driver
#   FLT_LATCH_N  SYS -> CTRL, hardware fault latch, low = tripped (also wired to the C2000 trip zone)
#   FLT_CLR      CTRL -> SYS, pulse to clear the latch (ineffective while a fault input is still active)
#   GATE_EN      SYS -> CTRL, high only while the hardware chain (E-stop, latch, watchdog) is healthy;
#                CTRL ANDs it into every power-board EN pair
#   WDI          CTRL -> SYS, watchdog kick;  SYS_RST_N  SYS -> CTRL, supervisor/watchdog reset, open drain
#   ESTOP_N      SYS -> CTRL, E-stop loop status, low = open
#
# The safety chain is DUAL CHANNEL, so that no single stuck part can keep the converter running:
#   channel A   E-stop A, 24 V under-voltage, spare trip loop, watchdog -> latch A -> GATE_EN (the EN pairs)
#               and the first series switch of the contactor/output supply
#   channel B   E-stop B, watchdog -> latch B (separate parts) -> CHB_OK, which switches +24V_GD (the supply of
#               every gate-driver bias converter: no bias, no gate) and the second series switch of the
#               contactor/output supply
#   +24V_GD     SYS -> CTRL, the channel-B-switched 24 V; CTRL passes it ONLY to the +24V pins of the four CELL
#               connectors, never to its own logic
#   CHB_OK      SYS -> CTRL, high = channel B healthy; CTRL also ANDs it into every EN pair and firmware trips on
#               any lasting disagreement between GATE_EN and CHB_OK (latent-fault detection)
SYS = ["+24V", "+24V", "+24V_GD", "+24V_GD", "GND", "GND", "GND", "GND",
       "CANA_TX", "CANA_RX", "CANB_TX", "CANB_RX", "MCAN_TX", "MCAN_RX", "GND", "GND",
       "SCIA_TX", "SCIA_RX", "SCIA_DE", "SCIB_TX", "SCIB_RX", "SCIB_DE", "SCIC_TX", "SCIC_RX", "SCIC_DE", "GND",
       "ETH_TXP", "ETH_TXN", "GND", "GND", "ETH_RXP", "ETH_RXN", "ETH_LED_LINK", "ETH_LED_ACT",
       "I2C_SDA", "I2C_SCL", "SPI_CLK", "SPI_SIMO", "SPI_SOMI", "SPI_CS_ADC_N", "SPI_CS_IO_N", "GND",
       "DI1", "DI2", "DI3", "DI4", "DI5", "DI6", "DI7", "DI8",
       "DO1", "DO2", "DO3", "DO4", "DO5", "DO6", "DO7", "DO8",
       "FLT_LATCH_N", "FLT_CLR", "GATE_EN", "WDI", "SYS_RST_N", "ESTOP_N", "GND", "GND",
       "FAN_PWM1", "FAN_PWM2", "FAN_PWM3", "FAN_PWM4", "FAN_TACH1", "FAN_TACH2", "FAN_TACH3", "FAN_TACH4",
       "PG_24V", "PG_HVAUX", "ETH_CT", "CHB_OK", "GND", "GND"]
# ETH_CT: the Ethernet PHY's analog supply, sent from CTRL to the centre taps of the RJ45 magnetics on SYS, so the
# two boards' 3.3 V rails never meet through the MDI terminations. SYS only decouples it; it must not load it.

# Function of each SYS digital output / input, the same on every module. These are 24 V field signals wired by
# harness between the SYS-IO-AUX terminals and the port board / chassis devices (index 0 = DO1 / DI1).
#   K_x_PRE / K_x_MAIN   precharge relay and main contactor coil of port A / B (energise = close)
#   K_DISCH              discharge relay, normally closed: de-energised = bus is being discharged (fail-safe)
#   IMD_SW_P / IMD_SW_N  insulation-measurement test resistor to PE on the + / - pole (energise = connected)
#   FB_x_MAIN            main-contactor state from the port board, 24 V level, high = contactor closed (welded-contact
#                        check). It is a module-INTERNAL signal referenced to the module's PELV GND: SYS-IO-AUX puts
#                        the field ground of these two input channels on GND, and the harness carries a GND return
#                        beside each. Every other input is an external field signal on the isolated return DIF_GND.
#   K_x_MAIN             on the port board this line is BOTH the high-side coil feed and the gate signal of a low-side
#                        coil switch, with the varistor directly across the coil as the only flyback path (>= 50 V,
#                        as the contactor maker requires); the hold-closed path has its own high-side and low-side
#                        switch, so no single shorted switch can energise a coil
#   ESTOP_A / ESTOP_B    the two channels of a dual-channel emergency stop (both closed = healthy); each trips the
#                        hardware chain on its own, and a disagreement between them is itself a fault
DO = ["K_A_PRE", "K_A_MAIN", "K_B_PRE", "K_B_MAIN", "K_DISCH", "IMD_SW_P", "IMD_SW_N", "DO_SPARE"]
DI = ["ESTOP_A", "DOOR", "SMOKE", "FB_A_MAIN", "FB_B_MAIN", "BREAKER", "IMD_ALARM", "ESTOP_B"]

# AUX-HV -> SYS-IO-AUX, 3-way. PG_HVAUX is driven ACTIVELY high by AUX-HV when its output is in regulation and
# pulled down on SYS-IO-AUX, so a dead or unplugged AUX-HV reads "not good".
# AUX-HV is a 30 W bootstrap supply (decision D-022): from AUX-HV alone the module boots, communicates, runs its
# self-test and shuts down in order - it does not run at power (fans and both main contactors need the cabinet 24 V).
# SYS-IO-AUX therefore (a) keeps the fans OFF by default, (b) gives the cabinet feeds priority and connects the
# AUX-HV feed only when no cabinet feed is healthy, and (c) disconnects an AUX-HV feed that is above the window.
# On the DAB module AUX-HV is fed from port 1 only (decision D-021). The module's 24 V system window is 21.6-26.0 V at every board (decision D-013);
# SYS-IO-AUX cuts every branch off between 26.10 and 26.45 V.
AUX = ["+24V_HVAUX", "GND", "PG_HVAUX"]

# SYS-IO-AUX <-> BMU-GW socket (2x5 2.54 mm); net names as used on BMU-GW (gen/bmu_gw.py J101).
GW = ["5V", "3V3", "GND", "GND", "CAN_TX", "CAN_RX", "RS485_D", "RS485_R", "RS485_DE", "RS485_RE_N"]

# Board identification: a 0.1 % resistor from ID to GND on the power / port board. CTRL pulls ID up to 2.5 V through
# 10.0 k and reads the divider on an ADC channel (open = no board = 2.5 V, short = 0 V; >= 0.33 V between codes).
# The cell ports and the port connector are decoded with separate tables (gen/ctrl_c2000.py): the cell table holds
# PVCELL-25 / DAB60-B1 / DAB60-B2, the port table DAB60-PORT / PV-PORT. On a port board the code is the PARALLEL value
# of the ID resistor and the proof-test readback resistors, and the readback states are steps around that code.
ID_OHM = {"DAB60-PORT": "2.49k", "PVCELL-25": "4.99k", "PV-PORT": "10.0k", "DAB60-B1": "20.0k", "DAB60-B2": "40.2k"}

# What CTRL offers each power board on a CELL connector's +24V pins (the safety-switched +24V_GD branch):
# eFuse limit 1.49 A per port, so a power board must draw <= 1.0 A continuous and present <= 600 uF at start-up.
CELL_24V_MAX_A, CELL_24V_MAX_UF = 1.0, 600

# ---------------------------------------------------------------------------------------------------------------
# Cost-first PV module (decision D-044, docs/requirements/ARCHITECTURE-COSTFIRST.md): POWER board <-> CONTROL board,
# 2x32 2.54 mm board-to-board. BOTH SIDES ARE LIVE: GND = AGND = BUS- (the controller's reference). Nothing on this
# connector may leave the enclosure; the only reinforced barrier is on the control board (communication, stop input,
# status, fans). Levels and scaling are those of the architecture document (sections 5 and 6), as built on PV-PWR.
#   PWMn       control -> power, 3.3 V logic AFTER the trip latch's gating; high = switch on. Phase p = 1..4:
#              PWM(4p-3) leg A high side, PWM(4p-2) leg A low side, PWM(4p-1) leg B high side, PWM(4p) leg B low side.
#              The power board buffers them to the 5 V level the NSI6651 inputs need and pulls each one low.
#   EN         control -> power, high = gate drivers enabled (latch not tripped); low forces every gate off.
#   FLT_N      power -> control, wired-OR of all driver faults (DESAT, UVLO), low = fault; pull-up on the control board.
#   RDY        power -> control, high = every gate-drive supply good.
#   BIAS_EN    control -> power, high = gate-bias converters running.
#   K_A K_B K_PRE  control -> power, high = close (PV-port contactor, battery-port contactor, precharge relay); the
#              power board's hardware interlocks (polarity, precharge delta-V, hold-off) sit between these and the coils.
#   HOLD       power -> control, high = a port current is above the contactor's breaking limit: contactor is held closed.
#   IMD_SWn    control -> power, high = insulation-test string n connected.
#   MOV_OK     power -> control, high = surge-varistor monitor loop intact.
#   ILn        per-phase inductor current sensor output, NOT ratiometric: 2.50 V (2.48-2.52) + 10.667 mV/A from the
#              STK-HO/A 75's own fixed reference (PV-PWR design check);  ILnR that reference (Uref, pin 4): the control
#              board's fixed trip window is set from it (rev A2 / PV-CTL rev A1; these four pins were +24V / +5V, which the
#              control board never used: it takes +3V3 only);  IA / IB port currents (measurement
#              gain), IA_H / IB_H the low-gain path used by the hold-off comparators;  VA / VB bank-side port voltages,
#              VAX / VBX terminal-side (bipolar, mid-scale offset), VPE = PE against BUS-;  NTCn = NTC to AGND.
PC = ["IL1R", "IL2R", "GND", "GND", "IL3R", "IL4R", "+3V3", "GND",
      "PWM1", "PWM2", "PWM3", "PWM4", "GND", "PWM5", "PWM6", "PWM7", "PWM8", "GND",
      "PWM9", "PWM10", "PWM11", "PWM12", "GND", "PWM13", "PWM14", "PWM15", "PWM16", "GND",
      "EN", "FLT_N", "RDY", "BIAS_EN", "K_A", "K_B", "K_PRE", "HOLD", "IMD_SW1", "IMD_SW2", "MOV_OK", "GND",
      "IL1", "IL2", "IL3", "IL4", "AGND", "IA", "IA_H", "IB", "IB_H", "AGND",
      "VA", "VAX", "VB", "VBX", "VPE", "AGND",
      "NTC1", "NTC2", "NTC3", "NTC4", "NTC5", "NTC6", "NTC7", "NTC8"]

assert len(CELL) == 40 and len(PORT) == 26 and len(SYS) == 80 and len(GW) == 10 and len(PC) == 64


def pins(table, prefix="", rename=None):
    """{pin number: net}. `rename` maps a contract name to the board's own net name (e.g. {"5V": "+5V"})."""
    rename = rename or {}
    return {str(i): rename.get(n, n if n in SHARED else prefix + n) for i, n in enumerate(table, 1)}
