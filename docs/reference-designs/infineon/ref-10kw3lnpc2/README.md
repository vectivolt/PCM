# REF-10KW3LNPC2 - 10 kW three-phase three-level NPC2 inverter board (Infineon)
Modular: NPC2 main board with IGBT7 H7 (IKZA40N120CH7 1200 V + IKZA40N65EH7 650 V), 1ED3240MC12H EiceDRIVER on three 4-channel boards (ISODRV-3240C4P15N05-1), isolated aux PB-APS-24V-5V ISO; XMC7200 control board REF-CLBXMC7PEC sold separately.
- **Native ratings** (UG-2024-09, section 1.2, typical/maximum columns): input 650 V typ / 850 V max DC (board warns DC-link potential up to 1000 VDC); output 380 V typ / 400 V max L-L rms, 15.2 A, 10 kW; 24 kHz; 50-65 Hz.
- **Informs:** GDRV alternate (EiceDRIVER, DESAT concepts; ECO-05) and IGBT/NPC2 cost benchmark.
- **Limitation (roadmap):** scale to 100-230 A class, 950 V, production firmware; 24 kHz IGBT AC-side stage, out of scope here.
- **Files:** user manual only. A REF-10KW3LNPC2Q (QDPAK SiC variant) manual exists on infineon.com, not fetched.
