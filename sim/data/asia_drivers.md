# Gate-drive channel: Asian sourcing study (driver + isolated bias)

Date 2026-10-04. Scope: REQUIREMENTS.md section 7 (SRC-1...4), decision D-031. Research and a prepared design only -
`gen/gdrv.py` and the boards are NOT edited in this step. Nothing here is bench-validated. Labels: **[DS pN]** =
datasheet value with page, **[calc]** = calculated here, **[assumed]** = engineering assumption, **[src]** = price or
stock source with date. Per-candidate data: `sim/data/asia_drivers.csv`.

Design range given by the orchestrator: on-rail +15...+20 V, off-rail 0...-5 V, 1-4 devices in parallel per channel,
32-100 kHz, switch-node dv/dt up to 100 V/ns, 1000 V DC system (1100 V trip), reinforced isolation to PELV.

## 1. Recommendation

| Function | Recommended | Runner-up | Incumbent |
|---|---|---|---|
| Isolated smart gate driver | **NOVOSENSE NSI6651ASC-Q1SWR** (AEC-Q100 Grade 1; industrial twin NSI6651ASC-DSWR) | Sillumin SiLM5992SHCG-DG | TI UCC21710QDWRQ1 |
| Isolated bias, per channel | **MORNSUN QA243CT-xxxxR4** (R4), *conditional on Mornsun's insulation report* | MORNSUN QA243C-xxxxR3G (R3; fallback) | TI UCC14241QDWNRQ1 |

The driver swap is clean: certified, AEC-Q100, cheaper. The bias swap is **not yet clean**. No Asian gate-drive module found
states a reinforced working voltage for a 1000-1100 V DC barrier, and the Mornsun modules are unregulated with no
power-good output. Until Mornsun's insulation report is on file, the UCC14241-Q1 stays the documented fallback.

## 2. Ranked shortlist per function

### 2.1 Isolated smart gate driver

1. **NOVOSENSE NSI6651ASC (-Q1SWR / -DSWR)**. A UCC21750-class part, pin-compatible with the UCC21750 footprint except
   pin 1 (AIN -> VEE2) and pin 16 (APWM -> TEST, to GND1) [DS p3; UCC21750 pinout checked on its DS p3].
   - Isolation and certification are equal to the UCC21710: VIOWM 1500 Vrms / 2121 VDC from TDDB, VIORM 2121 Vpk,
     VIOTM 8000 Vpk, VIOSM 10 kVpk, CIO 0.8 pF, CPG/CLR 8.0 mm, CTI > 600 V group I [DS p14-15].
     Certificates granted: UL 1577 E500602, VDE 0884-17 reinforced 40052820, CQC20001264939, CSA CN5A [DS p17].
   - Qualification: -Q1 is AEC-Q100 Grade 1 [Q1 DS p1].
   - Timing: delay 70/80/110 ns, PWD <= 30 ns, minimum pulse 20/40/70 ns, CMTI >= 150 kV/us [DS p9].
   - Output and clamp: 11/12 A peak, ROH 2.2 ohm / ROL 0.3 ohm, internal Miller clamp with threshold 1.5/2.0/2.5 V
     (same as UCC21710) at VEE2 + 0.8 V for 1 A [DS p7].
   - DESAT: threshold 8.5/9.26/9.8 V; ICHG 430-600 uA (industrial) / 350-650 uA (Q1); LEB 200 ns typ; filter
     150-265 ns; DESAT to OUT 150-300 ns; to FLT 400-750 ns [DS p8, Q1 p9].
   - Soft turn-off ISTO: 250/400/570 mA (industrial, -40..125 C) / **100**/400/570 mA (Q1, -40..150 C) [DS p8, Q1 p9].
   - Supervision: VCC2 UVLO on 10.5/11.2/12.8 V. RDY reports VCC1/VCC2 UVLO; FLT reports DESAT; RST/EN low resets a
     fault after tRST_FIL <= 800 ns [DS p6-8].
   - Availability: LCSC C33959952 (-Q1SWR), 2.98 USD at qty 1, 168 in stock [src mirror]. The -DSWR is not on LCSC.
2. **Sillumin SiLM5992SHCG-DG**: 12 A, DESAT (ICHG 0.38-0.58 mA), 550 mA soft turn-off, delay 90/130 ns, PWD 35 ns,
   tsk-pp 35 ns, CMTI 150 kV/us [DS p1, p10-11].
   - It falls behind on isolation and qualification: VIOWM only 1060 Vrms / 1500 VDC and VIORM 1500 Vpk [DS p7].
     VDE 0884-17 is **pending** (UL 1577 5 kVrms and CQC granted) [DS p1, Rev 1.0 Aug 2023], and there is no AEC-Q
     statement.
   - LCSC C22466050, 2.28 USD, 1425 in stock [src mirror].
3. Screened out (CSV rows):
   - Chipanalog CA-IS3214x: 8 pins, so no DESAT, FLT or RDY; all certifications "申请中" (pending) [DS V1.00 p1-2].
   - NOVOSENSE NSI68515: opto-emulator input, PWD up to 100 ns.
   - Toshiba TLP5214A: CMR 35 kV/us, below 100 V/ns.
   - ROHM BM6112FV-C: 3.75 kVrms basic class, external clamp FET needed.
   - Not evaluated, because no public DESAT-driver datasheet was found in this pass: 2Pai, BYD, Kiwi, Fortior, Renesas,
     Mitsubishi/Fuji.

### 2.2 Isolated bias supply (one per channel)

1. **MORNSUN QA243CT-xxxxR4** (fourth generation, Chinese datasheet, Aug 2026).
   - Models: +15/-5 V 100 mA, +18/-3 V 95 mA, +20/-5 V 80 mA (2.0 W); VIN 21.6-26.4 V; efficiency 83-86 % [DS p1-2].
   - Isolation: 5 kVAC 1 min, partial discharge 2000 V (**typ only**), CIO 3 pF typ, CMTI > 200 kV/us [DS p1, p4].
   - Unregulated: -Vo load regulation 10/14 % from 10 to 100 % load [DS p3]. -40..105 C [DS p4].
   - No safety listing, no PG. Price not checked.
2. **MORNSUN QA243C-1504R3G / -1803R3G / -2005R3G** (R3; fallback, best documented). PD >= 1700 V (< 10 pC, IEC
   61800-5-1), 5 kVAC, CIO 3.5/5 pF, CMTI +/-200 kV/us, MTBF 3.5 Mh, EN 62368-1 *test report* (not a certificate)
   [DS p4].
   - Full-load outputs at 24 V: +14.18..15.68 / -3.74..-4.14 V; +17.01..18.81 / -2.84..-3.14 V;
     +18.8..20.8 / -4.6..-5.1 V [DS p3].
   - Load regulation 6-8 % typ, **15 % max** (10-100 % load), unspecified below 10 % [DS p3].
   - LCSC C20615423 (QA243C-2005R3), price not verifiable (see section 4).
3. MORNSUN QA243CT-xxxxR3S: PD 2.5 kV, CIO 2.5/4 pF, load regulation 8/12 %, no safety listing ("--") [DS p1-3].
4. Reference only: Murata MGJ2 is "reinforced to UL 60950" only at a **200 Vrms / 280 Vpk working voltage** [DS p3]. The
   whole module class leaves the 1000 V DC case to the user.

Alternative not worked out: an Asian push-pull transformer driver with a reinforced transformer. The transformer, a
custom part with its own insulation evidence, carries the same open question as the modules, and the regulation and
PG would have to be built.

### 2.3 Companion parts (brief, as asked)

- Nexperia discretes already in the channel (PMEG4010CEJ, BAT54/BAT54S, BZX84, 2N7002BK) stay; Nexperia is
  Asian-owned (Wingtech).
- SN74LVC1G17: UMW clone on LCSC C7394021 at 0.039 USD (TI C7836 0.082 USD) [src mirror]. Prefer Nexperia 74LVC1G17
  (not priced).
- ZXTP25040DFH (Diodes Inc.) stays. Its datasheet was already filed by the orchestrator (identical sha256).
- Not researched in this pass: an Asian second source for US1M (DESAT diodes) and for the AM26LV31E/32E RS-422 pair.

## 3. Requirement comparison

| Requirement | UCC21710 / UCC14241 | NSI6651ASC-Q1 / Mornsun R4 (best) | SiLM5992SH / Mornsun R3G (runner-up) |
|---|---|---|---|
| Barrier working voltage >= 1100 V DC, reinforced | 2121 VDC (VDE 40040142 granted) / 1414 VDC, VDE planned | 2121 VDC (VDE 40052820 granted) / **not stated** | 1500 VDC (VDE pending) / **not stated**; PD >= 1700 V |
| CMTI >= 100 V/ns | 150 / > 150 | 150 (test VCM not stated) / > 200 | 150 / +/-200 |
| Short-circuit protection | OC input + external DESAT network, tOCOFF 150-400 ns | DESAT 8.5-9.8 V, ICHG 350-650 uA, to OUT 150-300 ns | DESAT ~9 V, ICHG 380-580 uA, to 90 % 0.35-0.6 us |
| Soft turn-off current | 250-570 mA | Q1 **100**-570 mA (ind. 250-570) | 550 mA typ |
| Delay / skew (dead-time budget) | tsk-pp 30 ns (gdrv budget 60 ns) | 70-110 ns, PWD 30, no tsk-pp spec (<= 40 from the spread) -> 70 ns budget [calc] | tsk-pp 35 + PWD 35 = 70 ns |
| Miller clamp | CLMPI 1.5-2.5 V, VEE + 0.5 V at 1 A | CLAMP 1.5-2.5 V, VEE2 + 0.8 V at 1 A | 1.8-2.2 V, 2/4 A |
| UVLO, RDY, FLT, EN | yes, plus PG of the bias module on RDY | yes / no PG (driver UVLO only) | yes / no PG |
| Isolated temperature (AIN/APWM) | yes | **no** | no |
| Rail regulation | +/-1.3 % both rails, adjustable | unregulated, -Vo +10/14 % at light load | unregulated, up to 15 % (10-100 % load) |
| Rails available (24 V in) | any within 15-25 V total | +15/-5, +18/-3, +20/-5 | +15/-4, +18/-3, +20/-5 |
| Bias power | 2 W to 85 C, 1.5 W at 105 C | 2 W, derate >= 85 C, to 105 C | ~2.2 W, derate >= 85 C |
| Barrier capacitance | < 3.5 pF (0.35 A at 100 V/ns) | 3 pF typ (0.30 A) [calc] | 3.5 typ / 5 max pF (0.50 A) [calc] |
| Qualification | AEC-Q100 both | AEC-Q100 Gr. 1 / maker MTBF only | none stated / maker MTBF 3.5 Mh |
| Price qty 1 (USD) | 4.42 (TI store via aggregator) / 12.83 (LCSC) | 2.98 (LCSC) / not verified | 2.28 (LCSC) / not verified |

## 4. Price and availability (all recorded in gen/data/prices.csv)

- TI pair per channel: 4.42 (UCC21710QDWRQ1, TI via electronicsdatasheets.com) + 12.83 (UCC14241QDWNRQ1, LCSC qty 1)
  = **17.25 USD** [calc]. The UCC21710 is not on LCSC (DigiKey 6.43 USD).
- NSI6651ASC-Q1SWR: 2.98 USD (LCSC, 168 pcs). It saves 1.44 USD per channel against the TI store, 3.45 USD against
  DigiKey [calc].
- Mornsun QA243C-2005R3 is listed on LCSC (C20615423), but the price is JS-rendered and the code is not in the
  jlcsearch mirror. **Not verified.** The real saving sits here (the UCC14241 is 12.83 USD), so a Mornsun quote or a
  price from another documented source is the first open item.
- Method: the mirror's MPN search is unreliable for these parts. Prices came from LCSC codes read off item.szlcsc.com
  pages, then the mirror's code lookup.

## 5. Residual risks

1. **Bias insulation at 1000-1100 V DC is unproven for every Asian module.** Mornsun gives 5 kVAC 1 min and PD
   1.7-2.5 kV but no working voltage, and the safety listing is a 62368 test report (R3) or nothing (R3S, R4). Request
   Mornsun's insulation/PD report and an IEC 61800-5-1 / 62477-1 statement at 1100 V DC before switching.
2. **Unregulated rails.** The +20 V model can reach about 21.0 x 1.15 = 24 V at 10 % load [calc]; below 10 % load is
   unspecified. Many Asian SiC parts allow +22 V. Each rail needs a preload of about 10 % of rated current, plus the
   existing gate Zeners.
3. **No PG.** The UCC14241 PG to RDY path disappears, and the negative rail is no longer monitored.
4. **NSI-Q1 soft turn-off.** ISTO min 100 mA over -40..150 C gives 356 nC / 0.1 A = 3.56 us for 2 x MSC035 [calc]. That
   is above its 3.1 us typical SCWT (industrial grade or UCC21710: 1.42 us). Use the device set's QG and SCWT; if it
   fails, use the -DSWR (250 mA min, not on LCSC) or reduce QG per channel.
5. **DESAT blanking spread.** ICHG +/-30 %, VTH +/-8 %, LEB given only as typical, and parasitic C dominates. For
   3 x US1M and no external C the window is 211-474 ns (spread 2.25) [calc, LEB 150-250 ns and pin 2-4 pF assumed],
   against 261-463 ns (1.77) with the present OC scheme. A BAT54S on the DESAT pin adds up to 2 x 10 pF and roughly
   doubles the window. Confirm by DPT.
6. **AIN/APWM is lost.** The DAB module NTC (L1 driver -> AN2) and the GDRV-HB NTC need another isolated path.
7. **Supply chain.** NSI-Q1 has only 168 pcs on LCSC. EVISUN (易成) and YLPTEC (易川) sell look-alike "QA" modules,
   EVISUN even under the series name "QAxx3C-R3", so the BOM must say MORNSUN and come from an authorised source.
8. **Rail gaps.** No 24 V-input Mornsun model offers +18/-4, +18/-5 or +20/-4. Those rails would keep the UCC14241.

## 6. What could not be verified

- Mornsun price; Mornsun reinforced working voltage; R4 PD minimum (only typical given).
- NSI CMTI test conditions; NSI part-to-part skew; NSI TDDB lifetime figure (VIOWM is TDDB-based, but no
  years-at-VIOWM statement was found in the text).
- NSI production history and design-ins beyond AEC-Q100 Grade 1 and the granted certificates. No reliability report
  was downloaded (none found openly).
- Sillumin DESAT threshold row (not text-extractable; ~9 V from the block diagram).
- Companions: US1M and RS-422 second sources, Nexperia 74LVC1G17 price.

## 7. Changes to gen/gdrv.py for the chosen pair (NSI6651ASC + Mornsun QA, to apply when devices and rails are fixed)

### 7.1 PARTS

- `UCC21710` -> `NSI6651A`:
  - mfr NOVOSENSE, mpn NSI6651ASC-Q1SWR (or -DSWR), SOW16, ds `gate-drivers/NSI66x1A-Q1.pdf`.
  - pins left `15 VCC1 pi, 10 IN+ i, 11 IN- i, 14 RST/EN i, -, 13 ~{FLT} oc, 12 RDY oc, 16 TEST i, -, 9 GND1 pi`.
  - pins right `5 VCC2 pi, 4 OUTH o, 6 OUTL o, 7 CLAMP o, -, 2 DESAT i, -, 3 GND2 pi, 1 VEE2 pi, 8 VEE2 pi`.
- `UCC14241` -> one entry per Mornsun model, e.g. `QA243C-1504R3G`, `QA243CT-1505R4` (SIP or module).
  - Pins come from the model's pin drawing (not text-extractable). The R3S moves +Vo/0V/-Vo to different pins per
    voltage variant [DS p2 test conditions], so each model needs its own symbol map.

### 7.2 channel()

- **Driver pin map:** VCC1 = vcc, IN+ = INP, IN- = INN or GND, RST/EN = en, FLT = flt_n, RDY = rdy, GND1 = gnd,
  TEST (16) = gnd [DS p3].
- **Secondary side:** VCC2 = vdd, OUTH/OUTL as now, CLAMP = clmp (gate or the PNP node), DESAT = DESAT net,
  GND2 = com, VEE2 (pins 1 and 8) = vee.
- **APWM / AIN:** delete the `apwm`/`ntc` handling (and the 1k/10n APWM RC); raise ValueError if `ntc` is passed.
- **Bias block:** delete UCC14241 and all its support parts:
  - FBVDD/FBVEE dividers and their 330p caps.
  - RLIM1, RLIM2 and the BAT54 DLIM.
  - The ENA tie, the PG 10k pull-up and the 2N7002BK PG-to-RDY transistor.

  Add:
  - The Mornsun module: Vin = vin, GND = gnd, +Vo = vdd, 0V = com, -Vo = vee.
  - Input C per datasheet; output C within the maximum capacitive load (680-2200 uF per output).
  - Preloads: R(+Vo-COM) and R(COM-(-Vo)) sized for 10 % of rated current. Example for +20/-5 V at 80 mA: 2.4 k and
    680 R; 0.17 W and 0.04 W [calc].
- **Unipolar off-rail (+18/0 etc.):** VEE2 = COM, the module -Vo carries only its preload.
- **GATE_V table:** keys become the Mornsun models. (15, 4) R3G 1504; (15, 5) R4 1505; (18, 3) R3G/R4 1803;
  (20, 5) R3G 2005 or R4 2005; (x, 0) as above. (18, 4), (18, 5) and (20, 4) are not available from Mornsun at 24 V.
  REFUSED stays only for the UCC14241 fallback.
- **DESAT network** (replaces VDD-R1-DX-R2-OC-R3/Cblk and the OC pin):
  - Topology: DESAT - Rs - n x US1M - drain. Cblk (C0G) from DESAT to GND2, normally DNP with parasitics only. A single
    BAT54 from GND2 (anode) to DESAT for negative spikes; drop the BAT54S or count its capacitance.
  - Trip values [calc, Q1 limits, US1M VF 0.5-1.0 V assumed]:
    - 1700 V: n = 3, Rs = 100 R, trip 5.43 / 6.96 / 8.27 V.
    - 1200 V: n = 2, Rs = 1.5 k, trip 5.52 / 7.01 / 8.28 V.
    - 650-750 V: n = 1, Rs = 3.0 k, trip 5.53 / 7.01 / 8.26 V.
  - The `desat` DNP option fits a 0 R from DESAT to GND2 instead of the string.
- **Miller hold (rev 4):** unchanged. CLAMP has the same threshold window and the same VCC2 + 0.8 V high-side clamp. The
  PNP follower base node stays on pin 7.
- **Dead-time stretcher:** same circuit. DEADTIME values are re-derived with the 70 ns delay-mismatch budget
  (UCC21710: 60 ns), and the minimum-pulse filter must exceed tPWmin 70 ns.

### 7.3 design_check()

- **NSI dict replaces the UCC21710 dict:**
  - UVLO: vcc2_on (10.5/11.2/12.8; Q1 9.8-12.8), vcc2_off (9.8-11.8; Q1 9.0-11.8).
  - Output: roh 2.2 (no ROH_EFF), rol 0.3, i_pk 10 (rating).
  - Clamp: vclmpth (1.5, 2.0, 2.5), vclamp_1a 0.8.
  - DESAT: vdesat (8.5, 9.26, 9.8), ichg (350, 500, 650) uA, t_leb 200 ns typ (assumed +/-25 %), t_fil (150, 200,
    265), t_off (150, 250, 300), t_flt (400, 650, 750) ns.
  - i_sto (0.10 Q1 / 0.25 ind, 0.40, 0.57) A.
  - Timing: tprop (70, 80, 110) ns, pwd 30, tpw_min 70.
  - Isolation: cmti 150; VIOWM 1500 Vrms / 2121 VDC, VIORM 2121, VIOTM 8000, CIO 0.8 pF.
- **Mornsun dict per model:** +Vo/-Vo (min/typ/max at full load), load regulation max (15 % R3, 14 % R4, 12 % R3S),
  line regulation, rated mA, cap-load maximum, CIO maximum, P = 2 W with derating >= 85 C.
- **Delete:** the UCC14241 dict, `rails()` divider maths, RDR/RLIM and p_rlim, the AIN/NTC divider checks, and the PG
  timing.
- **New or changed asserts:**
  - VCC2 at the lowest rail (+Vo min) >= vcc2_on max.
  - Gate voltages at the highest rail ((+Vo max) x (1 + load reg)) <= device VGS max, and the same on the negative side.
  - Gate power n x QG x dV x f + driver + preloads <= 0.8 x module power at the board temperature.
  - DESAT trip window vs device V_DS at the turn-off maximum (>= 2x margin as now).
  - Blanking window from t_leb + C_total x VTH / ICHG, with C_total = Cblk + string (CT 10 pF / n) + clamp diode + pin.
  - SC time = blank + t_fil + t_off + soft turn-off (n x QG / i_sto min) <= device SCWT and <= t_resp.
  - Dead time with the 70 ns budget.
  - Miller hold with vclamp_1a = 0.8 (the rev 4 estimate rises by <= 0.3 V: 0.66 -> <= 0.96 V for 2 x MSC035 at
    2.75 A, limit 0.98 V [calc]).
  - Common-mode current CIO x dv/dt (<= 0.5 A at 100 V/ns).
  - The bias working voltage stays an explicit UNVERIFIED flag until Mornsun's report is on file.

### 7.4 Boards

- **gen/pvcell.py:**
  - Call signature unchanged.
  - Set GATE_V to the new devices' rails and pick the DESAT preset by device voltage class (n, Rs).
  - Firmware holds RST/EN low >= 0.8 us to clear a fault (tRST_FIL max).
  - A 3-level cell doubles to 8 channels. Keep <= 2 channels per sheet, because one sheet holds <= 99 R and a
    paralleled channel now has about 26 R.
- **gen/dab60.py:**
  - The module NTC must leave the L1 driver: AN2 (APWM -> RC -> ADC) needs a new isolated temperature path, not
    designed here.
  - RG_off 0.27 ohm stays valid (ROL 0.3 ohm is the same).
  - RG_on must be re-derived: ROH 2.2 ohm vs UCC21710 ROH_EFF 0.7 ohm gives slower turn-on and lower peak source
    current; re-check E_on and dv/dt.
- **GDRV-HB (gdrv.py main):** same channel changes. J_GATE_L loses its NTC/AIN function; the 2 x 8 logic header is
  unchanged (RDY and FLT keep their meaning).

## 8. Files

- Datasheets added (each registered in both manifests):
  - `docs/datasheets/gate-drivers/NSI66x1A.pdf` and `NSI66x1A-Q1.pdf` (novosns.com).
  - `SiLM5992SH.pdf` (LCSC datasheet host; the part is stocked as C22466050).
  - `Mornsun_QAxx3C-R3.pdf` and `Mornsun_QAxx3CT-R3S.pdf` (mornsun-power.com).
  - `Mornsun_QAxx3CT-xxxxR4.pdf` (mornsun.cn).
- Prices: 6 lines appended to `gen/data/prices.csv`.
