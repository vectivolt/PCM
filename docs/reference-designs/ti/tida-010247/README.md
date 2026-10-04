# TIDA-010247 - high-accuracy battery management unit for 48-1500 V storage (TI)
Stacked BQ769x2 (BQ76972) monitor, up to 32 series cells per BMU, high-side N-MOSFET control, CAN stacking, MSPM0G3519 MCU.
- **Native ratings** (TIDUF20 Rev B): +/-1.8 mV LFP cell-voltage accuracy at 25 degC; 7 uA ship-mode current; stacked architecture over CAN up to 1500 V.
- **Informs:** BMU-GW (ECO-08) - what the battery side of the CAN/RS485 interface looks like. We do not build a cell monitor.
- **Limitation (roadmap):** inform the BMS interface only; PCS control should not duplicate cell-monitoring electronics.
- **Files:** TIDUF20 Rev B design guide, TIDMB58 Rev A schematic (11 pages), TIDMB59 Rev A BOM (10 pages). Not fetched (PCB layout is out of scope): assembly drawing TIDMB60, PCB layout TIDMB61, CAD symbols TIDMB62, Gerbers TIDCGC2, calculator snvr520 (all listed on TI's tool page).
