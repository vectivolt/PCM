# Asian power semiconductors for PV-P75 and DAB-D60 (SRC-1, SRC-2)

Status: complete for this round, 2026-10-04. Classes a, b, c, e by the MOSFET search; d and f by the module and
rectifier searches run in parallel (same rules, rows in the same CSV).
Data: `sim/data/asia_devices.csv` (one row per part; every figure read from the filed datasheet, page numbers in
`pages`; empty cell = not stated). Prices: `gen/data/prices.csv` (2026-10-04). Datasheets:
`docs/datasheets/power-semiconductors/`, registered in `docs/datasheets/SOURCES.csv` and `docs/SOURCES.csv`.
Nothing here is measured or bought. "Qualified" below means the maker says so in the datasheet; no
qualification report from any Chinese maker is on file.

## 0. What is needed (today's design)

| class | use | voltage | resistance wanted | today |
|---|---|---|---|---|
| a | PV cell 2-level FSBB switch, 2 in parallel per position | 1700 V | <= 50 mOhm | Microchip MSC035SMA170B4 |
| b | PV 3-level FC switch (C3M0032120K slot); DAB paralleled discretes | 1200 V | 13-40 mOhm, TO-247-4 | Wolfspeed C3M0032120K (study only) |
| c | PV 5-level / stacked alternative | 650-750 V | 8-25 mOhm | none |
| d | DAB full bridge | 1200 V module | 6-17 mOhm per switch | Wolfspeed CBB011M12GM4T |
| e | AUX-HV flyback switch (TO-263-7, driven 15.2 V / 0 V) | 1700 V | ~1 ohm | Wolfspeed C3M0900170J |
| f | DC-bank reverse clamps; 1200 V SiC Schottky | 1600 V | surge / I2t | Vishay VS-60EPS16-M3, Infineon DD600N16K |

## 1. Short answer for class (a): does a credible Asian 1700 V low-resistance discrete exist?

**Yes, two Chinese makers publish complete datasheets for 1700 V, 20 mOhm parts in TO-247-4 with Kelvin source**,
both lower in resistance than the incumbent (MSC035SMA170B4: 35 typ / 45 max mOhm):

- **InventChip IV2Q17020T4Z** - 20 typ / 26 max mOhm, 44 mOhm at 175 C, Rth j-c 0.33 K/W, "AEC-Q101 qualified"
  on p1. Maker has its own 6-inch SiC fab (Yiwu, production since July 2022) and IATF 16949 (2023-10-17). Not on
  LCSC, no public price: RFQ.
- **Sichain SG2M020170HJ** - 20 typ / 28 max mOhm, 42 mOhm at 175 C, Rth j-c 0.22 typ / 0.28 max K/W, final
  datasheet V02_03 after five revisions since 2024-03. **Stocked on LCSC (C42456099): 314 pcs, USD 12.10 @1 /
  9.74 @90.** No qualification statement in the datasheet; fab not disclosed.

Both are new (2024-2025 releases). Neither maker publishes a reliability report, a short-circuit rating or a
cosmic-ray (FIT versus DC voltage) curve, so the D-007 open risk (no FIT curve for a 1700 V part at 1000 V
continuous) is **not** closed by switching to them - it gets wider. BASiC has no 1700 V part below 600 mOhm
(its nearest is the 1400 V B3M020140ZL, which breaks the 0.67 V_DC/V_DSS rule at 1000 V); Sanan's only 1700 V
MOSFET is 1 ohm; PN Junction's P3M17040K4 (40 mOhm) is listed on LCSC out of stock at USD 34-90, no cheaper than
Microchip. InventChip also lists a 2000 V 45 mOhm part (IV2Q20045T4) for the D-007 2000 V fallback.

## 2. Ranked shortlists

Prices are LCSC unless stated; "RFQ" = no public price found. Energies are the datasheet table values at the test
point given (25 C); hot R_DS(on) at 175 C unless the CSV says 150 C.

### (a) 1700 V, <= 50 mOhm (PV 2-level)

| rank | maker / part | R typ / max 25 C, hot (mOhm) | Rth j-c (K/W) | Eon / Eoff (uJ) @ test | price, stock | qualification evidence | main risk |
|---|---|---|---|---|---|---|---|
| 1 | InventChip IV2Q17020T4Z | 20 / 26, 44 | 0.33 | 2360 / 770 @ 1200 V 60 A 3.3 ohm | RFQ, not on LCSC | AEC-Q101 stated (p1); own fab; IATF 16949 | no price/stock; gate +15..18 / -5..-2 V (our +20/-4 V must become +18/-3.5 V); no SC, no FIT data |
| 2 | Sichain SG2M020170HJ | 20 / 28, 42 | 0.22 / 0.28 max | 2229 / 501 @ 1200 V 75 A 2.5 ohm | USD 12.10 @1 / 9.74 @90, 314 pcs | none in datasheet; IATF 16949 (SGS, site news 2026-07-28); "5 M+ units shipped" (site) | no qualification statement, fab unknown, young company (~4 years) |
| 3 | InventChip IV2Q17040T4Z | 40 / 52, 82.8 | 0.42 | 1582 / 306 @ 1200 V 50 A 3.3 ohm | RFQ | AEC-Q101 stated (p1) | as rank 1; R doubles to 175 C |
| 4 | Sichain SG2M040170HJ | 40 / 52, 88 | 0.31 / 0.39 | 722 / 147 @ 1200 V 38 A 2.5 ohm | RFQ | none | as rank 2 |
| info | InventChip IV2Q20045T4 (2000 V) | 43 / 56, 105 | 0.30 | 870 / 150 @ 1200 V 20 A | RFQ | none (industrial) | only 20 A switching data |
| info | BASiC B3M020140ZL (1400 V) | 20 / 32, 37 | 0.25 | 1745 / 635 @ 1000 V 55 A | TME, price not readable | none | 1400 V fails the 0.67 rule; Rev 0.0 draft |

### (b) 1200 V, 13-40 mOhm (DAB discrete parallel; PV 3-level slot)

DAB parallel (13-20 mOhm):

| rank | maker / part | R typ / max, hot | Rth j-c | Eon / Eoff @ test | price, stock | qualification | main risk |
|---|---|---|---|---|---|---|---|
| 1 | InventChip IV3Q12013T4Z | 13.5 / 17, 22.5 | 0.27 | 1480 / 430 @ 800 V 100 A 2 ohm | RFQ | AEC-Q101 stated | Gen3 gate: DC -5/+20 V, recommended off -3.5..-2 V |
| 2 | Sichain SG2M014120LJ | 13 / 18, 23.7 | 0.21 / 0.26 | 1103 / 537 @ 800 V 90 A 2.5 ohm | USD 10.31 @1 / 7.31 @90, 43 pcs | none | qualification and fab unknown |
| 3 | BASiC B3M013C120Z | 13.5 / 17.5, 23 | 0.20 typ | 1200 / 530 @ 800 V 60 A 8.2 ohm | RFQ (TME lists, MOQ 300) | none in datasheet; maker: own 6-inch fab, HK-listed | no qualification statement for the B-series |
| 4 | InventChip IV2Q12017T4Z | 17 / 24, 34 | 0.271 | 1280 / 334 @ 800 V 60 A 2 ohm | RFQ | AEC-Q101 stated | - |
| 5 | CR Micro CRXQF17M120G2Z | 17 / 23, 32 | 0.48 max | 1035 / 900 @ 800 V 75 A 2.5 ohm (-5/+15 V) | USD 9.86 @1 / 6.81 @100, 640 pcs | none in datasheet; E_AS 1130 mJ, 100 % avalanche tested | Rth twice the others; high Eoff |
| ref | ROHM SCT4018KR (JP) | 18 / 23.4, 36 (150 C) | 0.37 | 520 / 142 @ 800 V 42 A | GBP 25.93 @1 (RS, snippet) | ROHM; t_SC 4.0 us stated | 0 V off recommended, -4 V absolute; price |

PV 3-level slot (30-40 mOhm, 16 devices per cell):

| rank | maker / part | R typ / max, hot | Rth j-c | Eon / Eoff @ test | price, stock | qualification | main risk |
|---|---|---|---|---|---|---|---|
| 1 | ROHM SCT4036KRHR (JP) | 36 / 47, 72 (150 C) | 0.65 / 0.85 | 239 / 26 @ 800 V 21 A | USD 5.31 @1 / 3.25 @990, 525 pcs | AEC-Q101 (p1), t_SC 4.0 us | gate 0 V off (-4 V abs); high Rth |
| 2 | InventChip IV3Q12035T4Z | 35 / 43.8, 57 | 0.52 | 408 / 43 @ 800 V 40 A | RFQ | AEC-Q101 stated | newest (Aug 2026) |
| 3 | BASiC B3M040120Z | 40 / 55, 75 | 0.48 / 0.70 | 650 / 170 @ 800 V 40 A | RFQ | none | - |
| 4 | Sichain SG2M035120LJ | 33 / 48, 62 | 0.45 / 0.56 | 212 / 48 @ 800 V 32 A 2.5 ohm | USD 3.50 @1 / 2.26 @900, 31 pcs | none in datasheet | qualification unknown; cheapest documented part |
| 5 | Sanan AMS1200020M2 (20 mOhm, for the DAB list too) | 20 / 26, 32 | 0.31 | 532 / 46 @ 800 V 40 A 2.4 ohm | RFQ | AEC-Q101 stated | Rev 0.2.0 preliminary: no E curves, no Zth curve |

Rejected in class (b): NCE NCES120P013T4 (cheap, USD 6.94, but no switching energies in the datasheet); the
LCSC-stocked Bestirpower, HL, Megain, JIAENSEMI, Siliup, Tokmas, HXY parts (no traceable maker, see section 3).

### (c) 650-750 V, 8-25 mOhm

| rank | maker / part | R typ / max, hot | Rth j-c | Eon / Eoff @ test | price | qualification | main risk |
|---|---|---|---|---|---|---|---|
| 1 | Sanan AMS0750009M2 (750 V) | 9 / 12, 12 | 0.25 | 917 / 507 @ 500 V 80 A 2.4 ohm | RFQ | AEC-Q101 stated; vertically integrated maker | no price; Rg int 6.3 ohm |
| 2 | InventChip IV3Q07011T4Z (750 V) | 11.5 / 15, 16 | 0.27 | 602 / 305 @ 400 V 100 A | RFQ | AEC-Q101 stated | switching data at 400 V only |
| 3 | Sichain SG2M012075LJ (750 V) | 12 / 17, 18 | 0.22 / 0.28 | 706 / 485 @ 500 V 88 A | RFQ | none | qualification unknown |
| 4 | ROHM SCT4013DR (750 V, JP) | 13 / 16.9, 22.2 (150 C) | 0.37 | 500 / 310 @ 500 V 58 A | no price read | ROHM; t_SC 11.5 us | 0 V off gate |
| 5 | BASiC B3M010C075Z (750 V) | 10 / -, 12.5 | 0.20 | 910 / 625 @ 500 V 80 A | RFQ | none | no max R_DS(on) or Vth limits printed |
| 6 | BASiC B3M025075Z (750 V) | 25 / 40, 32 | 0.38 | 530 / 245 @ 500 V 50 A | RFQ | none | Rev 0.0 draft |

Rejected: NCE NCES075P013T (stocked on LCSC, USD 5.62, but its datasheet leaves R_DS(on), Vth, Eon/Eoff blank).

### (d) 1200 V SiC modules (DAB) - from the module search (rows in the CSV, class d)

| rank | maker / part | per switch R typ 25 C / hot | Eon+Eoff @ test | Rth j-c | price | qualification | main risk |
|---|---|---|---|---|---|---|---|
| 1 | BASiC BMF008MR12E2G3 (E2B half bridge, SiC SBD inside) | 8.1 / 13.5 (incl. terminals) | 3.1 + 0.7 mJ @ 600 V 130 A | 0.13 max | RFQ | none for the part; company page claims AQG324/JEDEC testing | preliminary Rev 1.0; no SC rating; Coss 0.6 nF vs 0.4 nF |
| 2 | Leapers DFS08HF12EZA2 (E2 half bridge) | 7.5 chip / 11.8 | 4.92 + 0.82 mJ @ 600 V 160 A | 0.13 typ | RFQ | none | preliminary; no Zth, no RBSOA; chip origin not stated |
| 3 | BASiC BMF240R12E2G3 (E2B) | 5.5 / 10.0 | 7.4 + 1.8 mJ @ 800 V 240 A | 0.09 max | RFQ | UL 1557 E550494 | below the 6-17 mOhm range; Coss 0.9 nF hurts light-load ZVS |
| ref | ROHM BSM180D12P3C007 (JP) | 10.0 / 17.2 (150 C) | ~10.6 + 4.9 mJ @ 600 V 180 A | 0.17 | USD 506-653 per half bridge | ROHM own chips, in production since 2015 | 5-6x the incumbent cost per switch |

No Chinese module has a public price; the module route cannot be costed until BASiC / Leapers quote.

### (e) 1700 V, ~1 ohm (AUX-HV)

| rank | maker / part | package | R typ / max @18 V (@15 V) | Rth j-c | price | qualification | fit to AUX-HV |
|---|---|---|---|---|---|---|---|
| 1 | InventChip IV2Q171R0D7Z | TO-263-7 | 700 / 910 (950 / 1250) mOhm | 2.05 | RFQ | AEC-Q101 stated | same pin-out as C3M0900170J (p1); recommended off -5..-2 V, we drive 0 V |
| 2 | Sichain SG2M1K0170J2J | TO-263-7L | 970 / 1400 (1190 / 1700) mOhm | 2.2 / 2.8 | RFQ | none | same pin-out (p1); 32 % higher R at 15 V than C3M0900170J |
| 3 | BASiC B2M600170H | TO-247-3 | 600 / 750 (750 typ) mOhm | 2.0 | RFQ | none; E_AS 18 mJ | footprint change (TO-247-3 or TOLT sibling) |
| 4 | Sanan SMS1701000K | TO-247-3L | 1000 / 1200 at 20 V gate | 1.25 | RFQ | none (industrial); E_AS 150 mJ | specified at Vgs 20 V; footprint change |

Flagged, not recommended: Bestirpower BCBF170N1000P1 (TO-263-7L, USD 1.08 @1 / 0.58 @800 on LCSC, 494 pcs - maker
not traceable), HXY HC3M001K170J (copies Wolfspeed numbering), Tokmas CI7N170SM (trading brand).

### (f) Rectifiers and SiC Schottky diodes (from the rectifier search; rows in the CSV, class f)

1600 V discrete clamp (PV, 8 in parallel per bank; incumbent VS-60EPS16-M3: 60 A, I_FSM 950 A, LCSC C506457
USD 4.98 @1 / 2.67 @1000, 36 pcs):

| rank | maker / part | I_F / I_FSM 10 ms / I2t | leakage at V_RRM 25 / 150 C | price | evidence | risk |
|---|---|---|---|---|---|---|
| 1 | WeEn WND75P16W6 (TO-247-2L) | 75 A / 1050 A / 5513 A2s | 50 uA / 2 mA | RFQ, not on LCSC | IATF 16949 sites, HTRB lab (ween-semi.com quality page); datasheet Rev.01 2026-09-21 | no hot surge rating; brand-new datasheet; no price |
| 2 | WeEn WND60P16W6 | 60 A / 950 A / 4513 A2s | 50 uA / 1.5 mA | RFQ | as above | numerically equal to the incumbent, same gaps |
| 3 | Sirectifier SD7016 | 70 A / 1500 A / "14000 A2s" | - / 3 mA | RFQ | ISO 9001 only; assembler without own fab | its I2t and Zth contradict its own I_FSM; 3x hot leakage - not recommended |

Rejected: PANJIT PGR6016PT (TW; 800 A / 3200 A2s, datasheet curves carry the Vishay "VS-60EPS" legend - copied),
MASPOWER ESTF60SS160US (LCSC C19724679 USD 1.09, trading brand, 20 mA leakage at 25 C).
The 8-part group cannot be reduced: a 600 A module arm has the I2t but about 5 nH terminal inductance against the
1.0 nH the bank tolerates; 6 x WND75P16W6 pass on I2t (about 4.7x) but sit at about 1.0 nH with 1.05x peak margin -
re-simulate before changing the count (calculated by the rectifier search, not by sim/pv_design.py).

1600 V module clamp (DAB, one arm per port; incumbent Infineon DD600N16K, AUD 440-489 at RS AU from search
snippets, not on LCSC):

| rank | maker / part | I_FSM 10 ms / I2t (hot) | evidence | risk |
|---|---|---|---|---|
| 1 | Techsem MDC600-16-416F3 | 19.0 kA / 1805 kA2s at 150 C, 60 % V_RRM reapplied (= DD600N16K hot rating) | maker since 1966, own wafer process, ISO 9001 / IRIS (tech-sem.com) | quote only; no 25 C value; terminal layout vs DD600N not compared |
| 2 | Sirectifier SDD600N16BT | 16.2 kA / 1319 kA2s hot (about 15 % below the incumbent, still ~14x margin on the DAB event) | ISO 9001, UL isolation file | assembler; quote only |

1200 V SiC Schottky (only if a design option needs one):

| rank | maker / part | I_F / I_FSM | price | evidence | risk |
|---|---|---|---|---|---|
| 1 | BASiC B3D20120H (TO-247) | 20 A (155 C) / 160 A (25 C), 150 A (110 C), avalanche-rated | RFQ | most complete datasheet (hot leakage max); own fab | no price |
| 2 | Yangjie YJD112040NQG2 / YJD112020NGG2 | 40 A / 280 A; 20 A / 160 A | LCSC C20605605 USD 5.82 @1 (220 pcs); C20605598 USD 5.62 @1 (265 pcs) | none in datasheet | no hot surge or hot leakage max |
| 3 | ROHM SCS220KGHR | 20 A / 79 A | not on LCSC | AEC-Q101 (p1) | TO-220AC, low surge |

## 3. Makers (evidence level in brackets: D = datasheet statement, S = maker web page, P = press/third party, - = none)

- **InventChip / Shanghai Zhanxin (CN).** Own 6-inch SiC wafer fab in Yiwu, production since July 2022; IATF 16949
  from 2023-10-17; "30 million SiC MOSFETs delivered" by September 2025 [S: inventchip.com.cn/inventchip]. Every
  "Z"-suffix datasheet states "AEC-Q101 qualified" [D, p1]; no qualification report, short-circuit rating or
  cosmic-ray data is published. Datasheets are compact (9 pages) but complete: tables at 25 / 175 C, E versus
  R_G, I and T, Zth, SOA. Only maker with a 1700 V 20 mOhm part that carries a qualification statement. Not on
  LCSC and no distributor price found - RFQ. Gen3 parts restrict the negative gate voltage (-5 V DC).
- **Sichain / SICHAIN Semiconductor (Ningbo) (CN).** About four years old; IATF 16949 by SGS (site news
  2026-07-28); "5 million+ units shipped, 70+ customers" [S: sichainsemi.com]. Fab ownership not disclosed (assume
  foundry). Datasheets are the most complete of the Chinese set (14 pages, revision history, E versus I / R_G / T,
  Zth) but carry no qualification statement [D]. Several parts are stocked on LCSC with prices - the only maker
  whose 1700 V 20 mOhm part can be bought off the shelf. Needs: reliability report, HTRB/HTGB data, body-diode
  statement, fab/foundry disclosure.
- **BASiC Semiconductor (CN).** Hong Kong listed (9971.HK); own 6-inch SiC MOSFET line in Shenzhen and a module
  plant with a reliability lab in Wuxi; IATF 16949 / ISO 9001 quality system; AEC-Q101 and PPAP claimed for the
  automotive "A" parts [S: basicsemi.com, h-col-146, h-pd-85/46]. Industrial B-series datasheets (14 pages, full
  curves) state no qualification; several are Rev 0.x drafts and one (B3M010C075Z) prints no maximum limits.
  No 1700 V part below 600 mOhm. Not on LCSC; TME lists some parts (prices not readable).
- **Sanan Semiconductor (CN).** Vertically integrated SiC line in Changsha (crystal growth to devices) and an
  8-inch SiC joint venture with ST in Chongqing [P: compoundsemiconductor.net articles 113378, 113517].
  Automotive "AMS" parts state AEC-Q101 [D]. Datasheet quality is mixed: AMS0750009M2 Rev 2.0.0 is complete;
  AMS1200020M2 Rev 0.2.0 has no switching-energy or Zth curves. No 1700 V part below 1 ohm. Not on LCSC; RFQ.
- **China Resources Microelectronics (CR Micro, CN).** Large state-owned listed group that runs its own silicon
  power fabs (CSMC, Wuxi / Chongqing) [P]; the SiC wafer source of CRXQF17M120G2Z is not stated. Datasheet
  complete (Rev 2.0, E versus R_G / T / I / V_DD, Zth, avalanche 1130 mJ) but no qualification statement and a
  copied PDF title (IRF3205 template) - document control is loose. Largest LCSC stock of any Asian 1200 V low-R
  part (640 pcs). Rth j-c 0.48 K/W is poor for its resistance.
- **Wuxi NCE Power (CN).** Established silicon MOSFET supplier; its SiC datasheets on LCSC are incomplete (no
  switching energies; the 750 V one leaves key cells blank). Its own site is behind a slider CAPTCHA, so the full
  documents could not be checked - rejected for now.
- **ROHM (JP).** Vertically integrated, own chips (stated in the BSM180D12P3C007 datasheet), 4th-generation
  discrete datasheets are the most complete of all (17 pages, t_SC 4.0 us at 1200 V class, Foster thermal model);
  automotive "HR" parts AEC-Q101 [D]. SCT4036KRHR is on LCSC at Chinese-level prices; SCT4018KR / SCT4013DR are
  not. Gate drive differs (0 V off recommended, -4 V absolute).
- **PN Junction (CN).** P3M17040K4 (1700 V 40 mOhm) is listed on LCSC out of stock at USD 90 @1 / 34 @1000 - no
  price advantage; datasheet not filed; maker site is a JavaScript app that returned no content.
- **Modules (from the module search):** BASiC E2B modules (BMF008MR12E2G3, BMF240R12E2G3) and Leapers
  DFS08HF12EZA2 are the in-range Chinese options, all preliminary and RFQ-only; Leapers is a packaging house that
  does not state its chip source. StarPower (in-range SiC half bridges "not for new design" or without switching
  data), Macmic (EV-traction modules only), Mitsubishi (>= 300 A), Fuji (hybrid IGBT + SiC diode), CRRC, Sanan,
  InventChip, Hestia: no in-range module with a public datasheet.
- **LCSC trading / untraceable brands - do not use:** HXY MOSFET (HC..., "-HXY" suffixes copy Wolfspeed /
  Infineon / ROHM numbers; one "-HXY" listing even prints a different resistance than its namesake), Tokmas
  (CI...), Bestirpower (BCZ / BCW / BCBF / BMW), HL (HLC), Megain (MGX / MGW), JIAENSEMI (JNCF), Siliup (SP...),
  and the GC.../KN3M/RSM/HT.../XRC/CMS/HSCM/ADW/CMHG/ASZM listings (not investigated). They are cheap
  (BCBF170N1000P1 1700 V 1 ohm USD 0.58-1.08; BCZ120N16M1 1200 V 16 mOhm USD 3.80-6.45) but no fab, no
  qualification and no maker web presence was found.
- **Not reached for discretes:** Yangjie, Silan, WeEn, Global Power Technology, CETC / Guoyang, Hestia, Toshiba,
  Sanken, Power Master / SK powertech - a web search found no 1700 V discrete below 50 mOhm from any of them
  (Toshiba's 1700 V SiC parts are modules); their 1200 / 750 V discretes were not filed.

## 4. Cost picture (incumbent versus candidate)

Figure of merit: price x R_DS(on),typ(25 C) in USD x mOhm (cost of conduction capability; lower is better).

| voltage class / position | incumbent | USD x mOhm | Asian candidate | USD x mOhm | per-position cost |
|---|---|---|---|---|---|
| 1700 V, PV 2-level position (2 devices) | MSC035SMA170B4, USD 41.80 @1 (DigiKey) / 31.29 low tier | 1463 / 1095 | SG2M020170HJ USD 12.10 / 9.74 | 242 / 195 | 2 x incumbent USD 63-84 (17.5 mOhm) vs 2 x SG2M020170HJ USD 19-24 (10 mOhm) or 1 x USD 10-12 (20 mOhm) |
| 1200 V, PV 3-level slot (16 per cell) | C3M0032120K USD 20.05 @1 (LCSC, 3 pcs) | 642 | SCT4036KRHR USD 5.31 / 3.25; SG2M035120LJ USD 3.50 / 2.26 | 191 / 117; 115 / 75 | per cell 16 devices: USD 321 vs USD 52-85 (ROHM) or 36-56 (Sichain) |
| 1200 V, DAB switch position | CBB011M12GM4T USD 201.46 per module (Richardson RFPD via findchips, 0 stock) = USD 50.4 per switch at 11 mOhm | 554 | 2 x SG2M014120LJ (6.5 mOhm) or 2 x CRXQF17M120G2Z (8.5 mOhm) | 134 / 95; 168 / 116 | USD 50 vs USD 15-21 per position (8 positions: USD 403 vs 109-165), before cold-plate insulation, clamps and the extra assembly |
| 1700 V, AUX-HV | C3M0900170J-TR USD 1.83 (Richardson, quantity tier not shown) | - | Asian parts RFQ; Bestirpower USD 1.08 (not recommended) | - | the AUX switch is not a cost driver |
| 1600 V clamp, PV bank (8 parts) | 8 x VS-60EPS16-M3 USD 39.84 @1 / 21.39 @1000 (LCSC) - about USD 128 per PV-P75 | - | WND75P16W6 / WND60P16W6 | RFQ | not comparable until WeEn quotes |
| 1600 V clamp, DAB port | DD600N16K AUD 440-489 (RS AU, snippet) | - | Techsem MDC600-16-416F3 | RFQ | not comparable until Techsem quotes |

Reading: at single-unit public prices the Chinese discretes cost 3-6x less per mOhm than the incumbents (ROHM's
stocked SCT4036KRHR 3.4x less than C3M0032120K). The module
route cannot be priced. All Chinese prices are distributor single-unit to 900-unit prices; production prices from
the makers will differ (lower), and none was quoted to us.

## 5. Gate-drive consequences (for whoever re-runs the losses)

- PV gate rail today is +20 / -4 V (D-007). Sichain and BASiC accept -4 V and recommend +18 V; InventChip
  recommends +15..+18 / -5..-2 V (Gen2) and only -3.5..-2 V with a -5 V DC limit (Gen3); ROHM recommends 0 V off
  with -4 V as the absolute DC limit. Every candidate needs +18 V instead of +20 V; InventChip Gen3 and ROHM need a
  different negative rail. Datasheet E_off values were measured at -3..-5 V.
- Vth at 175 C (typ, none gives a minimum): 1.9 V (BASiC), 2.0-2.2 V (InventChip), 2.0 V (Sanan), 2.2-2.3 V (Sichain); the D-028
  false-turn-on margin (1.48 V minimum assumed for MSC035SMA170B4) must be re-checked per part.

## 6. What I could not verify

- **Qualification.** No Chinese maker publishes a qualification report. "AEC-Q101 qualified" (InventChip, Sanan)
  is a datasheet sentence only; Sichain, BASiC (B-series), CR Micro and NCE state nothing. HTRB / H3TRB / HTGB /
  threshold-drift, dynamic gate stress, power cycling and body-diode bipolar-degradation results were not found
  for any candidate - they must be requested from the makers under NDA before a part is frozen.
- **Short-circuit withstand.** Only ROHM states t_SC (4.0 us at 1200 V class, 11.5 us for SCT4013DR). No Chinese
  discrete datasheet gives a short-circuit rating; the DESAT timing in the GDRV design assumes one.
- **Cosmic-ray robustness.** No FIT-versus-DC-voltage curve from any candidate maker (1700 V or 1200 V). For the
  2-level 1700 V option at 1000 V continuous this remains the main open risk of D-007.
- **Prices and volume.** No public price for any InventChip, BASiC, Sanan or Leapers part; LCSC stocks are 30-640
  pieces, not production volumes; LCSC prices were read from the product page or from the jlcsearch mirror (the
  mirror lags the page by up to tens of percent in stock). The DigiKey / RS / Microchip / TME pages refused scripted
  access (403): incumbent prices come from search-result snippets and findchips.com, with tier quantities missing.
- **Fabs.** Sichain's and CR Micro's SiC wafer sources are not disclosed; Leapers' chip source is not stated.
- **Hot threshold minimum.** Every Chinese maker gives Vth at 175 C as a typical value only; the D-028 false
  turn-on margin cannot be re-run on a guaranteed number.
- **Package outlines.** "TO-247-4L A / B / C / D" variants (Sichain, Sanan) differ in outline; pin order matches
  the TO-247-4 convention in the drawings read, but outlines were not compared (layout is out of scope).
- **Switching data comparability.** Test conditions differ (R_G 2-15 ohm, L_sigma 50-200 nH or not stated, gate
  -3..-5 V); the table energies are indicative only - the loss model must use each datasheet's curves.
- **Gate rail.** Not checked: whether UCC14241-Q1 bias can make +18 / -3.5 V (InventChip) or +18 / 0 V (ROHM).

## 7. Downloads and access

- Datasheets filed by this search: see the `datasheet` column (MOSFET classes a, b, c, e) and the module (d) and
  rectifier (f) rows. All from the makers' own sites except CR Micro and NCE (LCSC's datasheet host, parts listed
  on LCSC). BASiC files come from its site builder's file host (download.s21i.co99.net, BASiC's account), fetched
  through the same public lookup the site's download button uses, only for files marked public.
- Blocked / to do by hand: www.ncepower.com (slider CAPTCHA) - NCE SiC full datasheets and reliability data;
  TME, DigiKey, RS, Microchip product pages (HTTP 403) - distributor prices; www.pnjsemi.com (JavaScript app, no
  content) - PN Junction datasheets. Sichain and Sanan servers are slow (first downloads truncated, re-fetched
  complete).

## 8. Picks for the device / topology re-study (what to load into the loss models first)

| class | first pick | runner-up | why |
|---|---|---|---|
| a 1700 V | InventChip IV2Q17020T4Z | Sichain SG2M020170HJ | qualification statement and own fab vs. the only part with LCSC stock and price; both 20 mOhm |
| b 1200 V, DAB parallel | InventChip IV3Q12013T4Z | Sichain SG2M014120LJ (LCSC) / BASiC B3M013C120Z | 13-13.5 mOhm, Rth 0.20-0.27 K/W, switching data at 60-100 A |
| b 1200 V, PV 3-level slot | ROHM SCT4036KRHR (LCSC, AEC-Q101) | Sichain SG2M035120LJ (LCSC) / InventChip IV3Q12035T4Z | ROHM is the only fully documented part (t_SC, qualification) that is also cheap and stocked |
| c 750 V | Sanan AMS0750009M2 | InventChip IV3Q07011T4Z | 9-11.5 mOhm, AEC-Q101 stated, complete curves |
| d module | BASiC BMF008MR12E2G3 (2 per bridge) | Leapers DFS08HF12EZA2 | only in-range Chinese modules; both preliminary, quote needed |
| e AUX-HV | InventChip IV2Q171R0D7Z | Sichain SG2M1K0170J2J | both pin-compatible with C3M0900170J (TO-263-7) |
| f PV clamp | WeEn WND75P16W6 | WeEn WND60P16W6 | more surge and I2t than VS-60EPS16-M3 at the same leakage; quote needed |
| f DAB clamp | Techsem MDC600-16-416F3 | Sirectifier SDD600N16BT | hot surge equal to DD600N16K |

Before any of these is frozen (SRC-2): request from each maker the qualification report (AEC-Q101 or JEDEC set:
HTRB, H3TRB, HTGB +/-, TC, IOL/PC), threshold-drift and body-diode-degradation data, short-circuit withstand,
and for 1700 V the FIT-versus-DC-voltage curve; and a volume price.
