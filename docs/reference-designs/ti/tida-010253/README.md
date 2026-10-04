# TIDA-010253 - battery control unit (BCU) for HV battery racks (TI)
Rack controller board: isolated CAN, RS-485, Ethernet (DP83826E), HV relay-coil drivers, daisy chain, humidity sensor, RTC, watchdog, HV ADC, current-sensor interface, reverse-polarity protection.
- **Native ratings:** no electrical power ratings; it is a controller board for a **Sitara controlCARD (TMDSCNCD263), not a C2000 card**.
- **Informs:** SYS-IO-AUX (ECO-03: isolated CAN/RS485, Ethernet, relay drive, watchdog) and BMU-GW (ECO-08).
- **Limitation (roadmap):** BMS-interface reference only; PCS control should not duplicate battery-pack electronics.
- **Files:** TIDUF55 design guide, TIDMC33 schematic (8 pages), TIDMC34 BOM (7 pages). Not fetched (PCB layout is out of scope): assembly drawing TIDMC35, PCB layout TIDMC36, CAD symbols TIDMC37, Gerbers TIDCGI9 (all listed on TI's tool page).
