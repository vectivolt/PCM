# Transistor and diode positions: LCSC orderability and Chinese alternatives (2026-10-05)

Status: this round is finished; gaps are listed in section 6 (owner request 2026-10-05: "check other Switches and Diodes alternatives from reputed chinese OEMs, from lcsc ... for easier ordering"). Scope: every transistor and diode position of the three module BOMs (PV-P75, PV-P100-110, PCS-P125 cost-first boards, `bom/*_module_BOM.csv`) plus the DAB study's device set (`sim/out/dab_design/dab_spec.json`). 42 positions, 180 position x candidate rows in `sim/data/lcsc_semis.csv` (one row per candidate, incumbent rows included). Nothing is bought; nothing here is bench-validated.

Where the figures come from: LCSC product pages read 2026-10-05 between 14:50 and 15:30 UTC (the page embeds its stock and price ladder; every candidate row names the page, its brand and MPN as printed there, and the read time); the JLCPCB part page for the library tier and its stock; manufacturer or LCSC-hosted datasheets read with `pdftotext` (page references in the CSV); `https://jlcsearch.tscircuit.com` only to discover candidates. Prices are USD. LCSC's first price break is often 5, 10 or 50 pieces: `price_1` is that first break (the minimum order), the note column says so. `price_100` and `price_1k` are the price of the highest break at or below 100 / 1000 pieces; where the ladder ends earlier (many SiC parts stop at 90 pieces) the column repeats the last break. Stock moves daily: re-read before ordering.

Verdicts: DROP-IN = same package, same pin order, equal or better on every parameter the design check uses; FUNCTIONAL = fits but needs the stated re-check; REJECT = reason in the CSV. Per the project rule a main-switch alternate is FUNCTIONAL at best (D-041: the Miller and short-circuit analyses are device-specific).

## 1. Findings that change earlier numbers (read first)

1. **The main switch is orderable on LCSC today.** Sichain SG2M040170HJ (24 / 32 / 36 per module) is LCSC **C42456100: 418 pcs in stock, USD 6.7294 @1 / 5.7836 @10 / 5.5553 @30 / 5.0709 @90+** (page read 2026-10-05 14:55 UTC). The 2026-10-04 audit and `gen/data/prices.csv` / `cost_estimates.csv` call it "RFQ, not on LCSC" because the jlcsearch mirror holds only the JLCPCB parts library: this part is LCSC-only (its JLCPCB page is empty), so it has no JLCPCB tier and cannot be taken from the JLCPCB library for PCBA. Stock covers 17 / 13 / 11 modules. The cost model's 4.07 USD estimate is 1.00 USD under the live 90+ price (24 / 32 / 36 USD per module more).
2. **The flyback switch IV2Q171R0D7Z is listed (C5806853) but has 0 pieces.** LCSC ladder USD 5.9556 @1, 5.137 @10, 4.5897 @50, 4.0979 @100, 3.8713 @500, 3.7695 @1000 (model estimate 2.50). No LCSC part from a reputed maker meets the bar: WeEn WNSC2M1K0170B7 (new, Rev.02 July 2026, TO263-7L, same pins, equal or better on the conduction, charge, thermal and rating rows - C_rss 2.8 vs 2.2 pF is the exception - but no qualification statement) is not on LCSC (TME 4,000 pcs and element14 APAC 528 pcs per findchips.com, not verified on the distributor pages); Sichain SG2M1K0170J2J is not on LCSC; Bestirpower BCBF170N1000P1 (494 pcs, USD 1.08 @1) is rejected for an incomplete datasheet (no pin table, R_DS(on) only at 20 V). Correction to the audit: Bestirpower is a traceable maker (www.bestirpower.com: fab-lite, offices in Shanghai, Shenzhen, Ningbo, Xi'an).
3. **A 1600 V clamp diode at one third of the price exists in volume.** Yangjie 60EPS16 (C698648, 2,192 pcs, USD 0.8967 @360+, 0.8655 @1080+) against the incumbent VS-60EPS16-M3 (C506457, **36 pcs**, USD 2.674 @1000): same 1600 V / 60 A / 950 A / 4525 A2s numbers on paper, but surge and I2t with V_RRM reapplied are not stated, the lead polarity is not tabulated and the datasheet figures equal Vishay's no-voltage rows (FUNCTIONAL). 20 devices per DAB module: about 36 USD less per module at the 1000 level. WeEn WND60P16WQ (C602729) is listed with 0 pcs (the 2026-10-04 audit lists only the W6-suffix WeEn parts, as not on LCSC, and does not mention this listing). The 600 A clamp modules (DD600N16K, Techsem MDC600-16-416F3, Sirectifier SDD600N16BT) are not on LCSC at all.
4. **DAB 1200 V switch:** IV3Q12013T4Z has no LCSC listing. Sichain SG2M014120LJ is on LCSC (C52109935) with only **14 pcs** (a DAB module needs 16). In stock and FUNCTIONAL: Sichain S1M014120H (first generation, 75 pcs), CR Micro CRXQF17M120G2Z (640 pcs, R_DS(on) +35 %, V_th min 1.8 V), Bestirpower BCZ120N16M1 (31 pcs, preliminary Rev. 0.5, Q_gd 117 nC).
5. **Incumbents that cannot be ordered as specified (17 of 42 positions):** P02 IV2Q171R0D7Z, P03 IV3Q12013T4Z, P04b DD600N16K, P06 US1MH, P09 VS-8ETU04S-M3, P15 BZX84-A18, P17 BZX84-B20, P24 SMBJ33A, P25 SMBJ36A, P26 SMBJ58CA, P27 SMCJ33A, P32 PMV30ENEAR, P34 IRFR9214TRPbF, P38 PBSS5540X, P39 ZXTN25040DFHTA, P40 ZXTP25040DFHTA, P42 CA-IS3417WT-Q1. The other 25 incumbents are in stock on LCSC; these are shallow (stock below 100 modules): P01 SG2M040170HJ (418 pcs = 11 modules); P04a VS-60EPS16-M3 (36 pcs = 1 module); P07 S2M-13-F (270 pcs = 67 modules); P11 PMEG4010CEJ (2,825 pcs = 29 modules); P19 BZX84-B4V7 (1,570 pcs = 43 modules); P21 BZX84-B6V8 (5 pcs = 5 modules).
6. **Maker labels in `gen/data/prices.csv` are wrong for eight rows** (LCSC page brand read today): BAS16 C181107 is Guangdong Hottech; BAT54 C545546 is UMW; BAT54S C408389 is MDD; BSS138BK C5224267 is ElecSuper (not Nexperia: the genuine part is C282529); SMBJ33A C173526 is MDD; SMBJ36A C224020, SMBJ58CA C151267 and SMCJ33A C224049 are Littelfuse. No Bourns TVS was found on LCSC. The comma-suffixed Nexperia listings (BAS16,215 C79997; BAT54,215 C85084; BAT54S,215 C47546) are genuine.
7. **BZX84 pin order:** SOT-23 pin 1 = anode, 2 = n.c., 3 = cathode in the Nexperia datasheet (Rev 7, Table 2) and in every Zener candidate read; the repository's generators already wire it so. Nexperia -QR parts carry the same table with AEC-Q101 and are DROP-IN where stocked. Only Yangjie lists a genuine +-2 % B-series on LCSC (no temperature coefficient, no reverse-surge rating: FUNCTIONAL).
8. **Process notes:** the jlcsearch mirror (and the JLCPCB part pages) cover the JLCPCB library only, so "no mirror hit" never meant "not on LCSC" (finding 1); LCSC returned HTTP 403 for about 15 minutes (20:27-20:42 IST) after a burst of page reads - not worked around, the reader was rate-limited afterwards (one request per 2.5 s). One sub-agent first sent a browser User-Agent for some datasheet downloads; those files were discarded and fetched again with plain requests.

## 2. Recommended orderable part per position

Rule: the incumbent if it is in stock on LCSC; otherwise the best DROP-IN; otherwise the best FUNCTIONAL with the re-check it needs. "Modules" = how many modules the live LCSC stock of the recommended part covers (PV-P75 / PV-P100-110 / PCS-P125). Quantities per module are P75 / P100-110 / P125. Tier = JLCPCB parts-library tier read on the JLCPCB page (Basic / Extended; "Preferred" would be shown only from the mirror's flag).

### A. SiC power switches and 1600 V clamp diodes (cost drivers)

| Pos | Qty | Incumbent and LCSC status | Recommended part | LCSC | Stock | USD @1 / @100 / @1000 | Tier | Verdict | Modules |
|---|---|---|---|---|---|---|---|---|---|
| P01 | 24 / 32 / 36 | Sichain SG2M040170HJ: C42456100, 418 pcs | Sichain SG2M040170HJ | C42456100 | 418 | 6.73 / 5.07 / 5.07 | not in JLCPCB library | INCUMBENT | 17 / 13 / 11 |
| P02 | 1 / 1 / 1 | InventChip IV2Q171R0D7Z: C5806853 listed, 0 pcs | **none orderable on LCSC** (see section 4) | - | - | - | - | - | - |
| P03 | 16 per DAB module | InventChip IV3Q12013T4Z: not on LCSC | Sichain SG2M014120LJ | C52109935 | 14 | 10.47 / 7.42 / 7.42 | Extended | FUNCTIONAL | 0 DAB module(s) (16 needed each) |
| P04a | 20 per DAB module (8 per bank on the full-featured PV boards) | Vishay VS-60EPS16-M3: C506457, 36 pcs | Vishay VS-60EPS16-M3 | C506457 | 36 | 4.98 / 2.95 / 2.67 | Extended | INCUMBENT | 1 DAB module(s) (20 needed each) |
| P04b | 2 per DAB module (1 arm per port) | Infineon DD600N16K: not on LCSC | **none orderable on LCSC** (see section 4) | - | - | - | - | - | - |

### B. HV and power rectifiers

| Pos | Qty | Incumbent and LCSC status | Recommended part | LCSC | Stock | USD @1 / @100 / @1000 | Tier | Verdict | Modules |
|---|---|---|---|---|---|---|---|---|---|
| P05 | 3 / 3 / 3 | Vishay BYG10Y-E3/TR: C97584, 31,405 pcs | Vishay BYG10Y-E3/TR | C97584 | 31,405 | 0.2003 / 0.1589 / 0.1189 | Extended | INCUMBENT | 10,468 / 10,468 / 10,468 |
| P06 | 36 / 48 / 18 | Taiwan Semiconductor US1MH: C17442259 listed, 0 pcs | Vishay US1MHE3_A/H | C413446 | 38,586 | 0.1038 / 0.0626 / 0.0527 | Extended | FUNCTIONAL | 1,071 / 803 / 2,143 |
| P07 | 3 / 3 / 4 | Diodes Inc S2M-13-F: C156322, 270 pcs | Diodes Inc S2M-13-F | C156322 | 270 | 0.1046 / 0.0870 / 0.0716 | Extended | INCUMBENT | 90 / 90 / 67 |
| P08 | 1 / 1 / 1 | Diodes Inc US1G-13-F: C110531, 16,135 pcs | Diodes Inc US1G-13-F | C110531 | 16,135 | 0.1101 / 0.0858 / 0.0646 | Extended | INCUMBENT | 16,135 / 16,135 / 16,135 |
| P09 | 2 / 2 / 2 | Vishay VS-8ETU04S-M3: not on LCSC | ST STTH8L06G-TR | C2924785 | 426 | 0.8964 / 0.5470 / 0.4670 | Extended | FUNCTIONAL | 213 / 213 / 213 |
| P10 | 2 / 2 / 2 | MDD SS56: C65009, 230,910 pcs | MDD SS56 | C65009 | 230,910 | 0.0531 / 0.0439 / 0.0353 | Extended | INCUMBENT | 115,455 / 115,455 / 115,455 |

### C. Schottky and switching diodes

| Pos | Qty | Incumbent and LCSC status | Recommended part | LCSC | Stock | USD @1 / @100 / @1000 | Tier | Verdict | Modules |
|---|---|---|---|---|---|---|---|---|---|
| P11 | 72 / 96 / 60 | Nexperia PMEG4010CEJ,115: C50704, 2,825 pcs | Nexperia PMEG4010CEJ,115 | C50704 | 2,825 | 0.3826 / 0.3053 / 0.2307 | Extended | INCUMBENT | 39 / 29 / 47 |
| P12 | 40 / 52 / 22 | Nexperia BAT54,215: C85084, 110,740 pcs | Nexperia BAT54,215 | C85084 | 110,740 | 0.0220 / 0.0220 / 0.0143 | Extended | INCUMBENT | 2,768 / 2,129 / 5,033 |
| P13 | 13 / 17 / 7 | Nexperia BAT54S,215: C47546, 277,760 pcs | Nexperia BAT54S,215 | C47546 | 277,760 | 0.0311 / 0.0311 / 0.0199 | Extended | INCUMBENT | 21,366 / 16,338 / 39,680 |
| P14 | 3 / 3 / 2 | Nexperia BAS16,215: C79997, 265,750 pcs | Nexperia BAS16,215 | C79997 | 265,750 | 0.0101 / 0.0101 / 0.0078 | Extended | INCUMBENT | 88,583 / 88,583 / 132,875 |

### D. Zener diodes

| Pos | Qty | Incumbent and LCSC status | Recommended part | LCSC | Stock | USD @1 / @100 / @1000 | Tier | Verdict | Modules |
|---|---|---|---|---|---|---|---|---|---|
| P15 | 1 / 1 / 1 | Nexperia BZX84-A18,215: C406046 listed, 0 pcs | Nexperia BZX84-B18-QR | C7508205 | 2,990 | 0.0423 / 0.0332 / 0.0287 | Extended | FUNCTIONAL | 2,990 / 2,990 / 2,990 |
| P16 | 25 / 33 / 13 | Nexperia BZX84-B10,215: C454956, 66,850 pcs | Nexperia BZX84-B10,215 | C454956 | 66,850 | 0.0379 / 0.0297 / 0.0257 | Extended | INCUMBENT | 2,674 / 2,025 / 5,142 |
| P17 | 25 / 33 / 37 | Nexperia BZX84-B20,215: not on LCSC | Yangjie BZX84B20 | C968219 | 1,100 | 0.0327 / 0.0327 / 0.0226 | Extended | FUNCTIONAL | 44 / 33 / 29 |
| P18 | 25 / 33 / 13 | Nexperia BZX84-B3V3,215: C182377, 4,740 pcs | Nexperia BZX84-B3V3,215 | C182377 | 4,740 | 0.0825 / 0.0653 / 0.0502 | Extended | INCUMBENT | 189 / 143 / 364 |
| P19 | 24 / 32 / 36 | Nexperia BZX84-B4V7,215: C426829, 1,570 pcs | Nexperia BZX84-B4V7,215 | C426829 | 1,570 | 0.0516 / 0.0407 / 0.0352 | Extended | INCUMBENT | 65 / 49 / 43 |
| P20 | 12 / 16 / 6 | Nexperia BZX84-B5V6,215: C183269, 17,820 pcs | Nexperia BZX84-B5V6,215 | C183269 | 17,820 | 0.0358 / 0.0358 / 0.0233 | Extended | INCUMBENT | 1,485 / 1,113 / 2,970 |
| P21 | 1 / 1 / 1 | Nexperia BZX84-B6V8,215: C282544, 5 pcs | Yangjie BZX84B6V8 | C968233 | 580 | 0.0252 / 0.0252 / 0.0170 | Extended | FUNCTIONAL | 580 / 580 / 580 |
| P22 | 2 / 2 / 1 | Nexperia BZX84-C15,215: C143972, 59,660 pcs | Nexperia BZX84-C15,215 | C143972 | 59,660 | 0.0471 / 0.0367 / 0.0316 | Extended | INCUMBENT | 29,830 / 29,830 / 59,660 |
| P23 | 1 / 1 / 1 | Nexperia BZX84-C5V1,215: C294621, 28,710 pcs | Nexperia BZX84-C5V1,215 | C294621 | 28,710 | 0.0562 / 0.0420 / 0.0350 | Extended | INCUMBENT | 28,710 / 28,710 / 28,710 |

### E. TVS / ESD diodes

| Pos | Qty | Incumbent and LCSC status | Recommended part | LCSC | Stock | USD @1 / @100 / @1000 | Tier | Verdict | Modules |
|---|---|---|---|---|---|---|---|---|---|
| P24 | 3 / 3 / 4 | Bourns SMBJ33A: not on LCSC | MDD SMBJ33A | C173526 | 7,190 | 0.0599 / 0.0500 / 0.0451 | Extended | DROP-IN | 2,396 / 2,396 / 1,797 |
| P25 | 1 / 1 / 1 | Bourns SMBJ36A: not on LCSC | MDD SMBJ36A | C114001 | 94,680 | 0.0580 / 0.0481 / 0.0432 | Extended | DROP-IN | 94,680 / 94,680 / 94,680 |
| P26 | 1 / 1 / 1 | Bourns SMBJ58CA: not on LCSC | MDD SMBJ58CA | C114007 | 15,930 | 0.0579 / 0.0481 / 0.0432 | Extended | DROP-IN | 15,930 / 15,930 / 15,930 |
| P27 | 6 / 6 / 6 | Bourns SMCJ33A: not on LCSC | Brightking SMCJ33A/TR13 | C310047 | 14,945 | 0.1214 / 0.0971 / 0.0758 | Extended | FUNCTIONAL | 2,490 / 2,490 / 2,490 |
| P28 | 1 / 1 / 1 | MDD SM712: C502564, 195,540 pcs | MDD SM712 | C502564 | 195,540 | 0.0719 / 0.0719 / 0.0542 | Extended | INCUMBENT | 195,540 / 195,540 / 195,540 |
| P29 | 1 / 1 / 1 | Texas Instruments ESD2CAN24DBZRQ1: C5736151, 9,630 pcs | Texas Instruments ESD2CAN24DBZRQ1 | C5736151 | 9,630 | 0.3026 / 0.2349 / 0.1697 | Extended | INCUMBENT | 9,630 / 9,630 / 9,630 |

### F. Small-signal and HV MOSFETs

| Pos | Qty | Incumbent and LCSC status | Recommended part | LCSC | Stock | USD @1 / @100 / @1000 | Tier | Verdict | Modules |
|---|---|---|---|---|---|---|---|---|---|
| P30 | 6 / 6 / 6 | Nexperia 2N7002BK,215: C282405, 172,680 pcs | Nexperia 2N7002BK,215 | C282405 | 172,680 | 0.0392 / 0.0307 / 0.0265 | Extended | INCUMBENT | 28,780 / 28,780 / 28,780 |
| P31 | 9 / 9 / 8 | Nexperia BSS138BK,215: C282529, 28,080 pcs | Nexperia BSS138BK,215 | C282529 | 28,080 | 0.0565 / 0.0443 / 0.0383 | Extended | INCUMBENT | 3,120 / 3,120 / 3,510 |
| P32 | 39 / 51 / 44 | Nexperia PMV30ENEAR: C553092 listed, 0 pcs | Vishay SQ2318AES-T1_BE3 | C3280170 | 7,940 | 0.2490 / 0.2158 / 0.1837 | Extended | FUNCTIONAL | 203 / 155 / 180 |
| P33 | 3 / 3 / 3 | Infineon BSS126H6327XTSA2: C534596, 24,060 pcs | Infineon BSS126H6327XTSA2 | C534596 | 24,060 | 0.1808 / 0.1436 / 0.1078 | Extended | INCUMBENT | 8,020 / 8,020 / 8,020 |
| P34 | 3 / 3 / 4 | Vishay IRFR9214TRPbF: not on LCSC | UTC 2P50G-TN3-R | C484516 | 65 | 0.2522 / 0.1994 / 0.1485 | Extended | FUNCTIONAL | 21 / 21 / 16 |

### G. Bipolar transistors

| Pos | Qty | Incumbent and LCSC status | Recommended part | LCSC | Stock | USD @1 / @100 / @1000 | Tier | Verdict | Modules |
|---|---|---|---|---|---|---|---|---|---|
| P35 | 36 / 48 / 12 | Nexperia MMBT3904,215: C426835, 126,220 pcs | Nexperia MMBT3904,215 | C426835 | 126,220 | 0.0281 / 0.0281 / 0.0185 | Extended | INCUMBENT | 3,506 / 2,629 / 10,518 |
| P36 | 24 / 32 / 6 | Nexperia MMBT3906,215: C75549, 108,820 pcs | Nexperia MMBT3906,215 | C75549 | 108,820 | 0.0363 / 0.0283 / 0.0243 | Extended | INCUMBENT | 4,534 / 3,400 / 18,136 |
| P37 | 1 / 1 / 1 | Nexperia BC817-25,215: C39828, 41,660 pcs | Nexperia BC817-25,215 | C39828 | 41,660 | 0.0245 / 0.0245 / 0.0158 | Extended | INCUMBENT | 41,660 / 41,660 / 41,660 |
| P38 | 12 / 16 / 6 | Nexperia PBSS5540X,135: C455078, 1 pcs | Nexperia PBSS4041PX,115 | C426840 | 9,351 | 0.5639 / 0.3469 / 0.2976 | Extended | FUNCTIONAL | 779 / 584 / 1,558 |
| P39 | 0 / 0 / 6 | Diodes Incorporated ZXTN25040DFHTA: not on LCSC | JSCJ FMMT619 | C53071 | 14,990 | 0.0527 / 0.0427 / 0.0377 | Extended | FUNCTIONAL | 2,498 |
| P40 | 0 / 0 / 18 | Diodes Incorporated ZXTP25040DFHTA: not on LCSC | **none orderable on LCSC** (see section 4) | - | - | - | - | - | - |

### H. Adjacent power semiconductor on the BOM

| Pos | Qty | Incumbent and LCSC status | Recommended part | LCSC | Stock | USD @1 / @100 / @1000 | Tier | Verdict | Modules |
|---|---|---|---|---|---|---|---|---|---|
| P42 | 2 / 2 / 2 | Chipanalog CA-IS3417WT-Q1: C42420726 listed, 0 pcs | **none orderable on LCSC** (see section 4) | - | - | - | - | - | - |

### Re-checks and caveats for every recommendation that is not the incumbent

- **P03 Sichain SG2M014120LJ (FUNCTIONAL)**: none beyond the DAB study, which already uses the worst case of both devices (dab_spec.json device_design_basis); V_th binning for the 2 parallel devices; no short-circuit rating; no qualification
- **P06 Vishay US1MHE3_A/H (FUNCTIONAL)**: I_R max 10 uA at 25 C (incumbent 5 uA): confirm the DESAT-string leakage into the NSI6651 DESAT pin while off stays negligible (inferred: uA vs internal pull-down)
- **P09 ST STTH8L06G-TR (FUNCTIONAL)**: confirm the D2PAK lead numbering (NC/A) against the KiCad footprint; t_rr 75-105 ns: re-check the secondary RC snubber and SiC turn-on stress if the aux flyback runs CCM; V_F(150 C) 1.05 vs 1.0 V: re-check diode dissipation
- **P15 Nexperia BZX84-B18-QR (FUNCTIONAL)**: tolerance +-2 % instead of +-1 %: re-check the VDD-follower reference = SiC gate-drive voltage band (aux_hv: 18 V reference, follower to VDD): +-0.40 V instead of +-0.18 V at 25 C
- **P17 Yangjie BZX84B20 (FUNCTIONAL)**: no temperature coefficient: VDD_MAX (aux_hv.py, 20.4 V + 18 mV/K x 60 K) relies on Nexperia S_Z max; no reverse surge rating: re-check gate-clamp transient energy; I_R 2x higher (negligible on a gate rail)
- **P21 Yangjie BZX84B6V8 (FUNCTIONAL)**: no temperature coefficient: re-check the frequency-foldback threshold drift over temperature (aux_hv AX-03) with an assumed S_Z; surge not relevant here
- **P24 MDD SMBJ33A (DROP-IN)**: none stated (same table values as the incumbent, details in the CSV)
- **P25 MDD SMBJ36A (DROP-IN)**: none stated (same table values as the incumbent, details in the CSV)
- **P26 MDD SMBJ58CA (DROP-IN)**: none stated (same table values as the incumbent, details in the CSV)
- **P27 Brightking SMCJ33A/TR13 (FUNCTIONAL)**: steady-state dissipation stated as 6.5 W @T_A 50 C with R_thJL 15 C/W (= 5 W @T_L 75 C, inferred): re-check AUX75 clamp heat; 8/20 us clamping not stated
- **P32 Vishay SQ2318AES-T1_BE3 (FUNCTIONAL)**: Qg +12 % and Ciss max 553 pF: re-run gdrv design_check clamp-engage/release timing (AO q_on, ciss); VGS(th) min 1.5 V vs 1.0 V: re-run the clamp threshold check with vth (1.5, 2.0, 2.5); no ESD rating stated: ESD-safe handling
- **P34 UTC 2P50G-TN3-R (FUNCTIONAL)**: RDSon 8.5 ohm vs 3.0 ohm: re-run sim/port_design.py hold-current check (HS_RDS 3.0 -> 8.5, x1.8 hot) and the coil pull-in/hold margins
- **P38 Nexperia PBSS4041PX,115 (FUNCTIONAL)**: hFE min 200 vs 250 at 0.5 A (180 vs 200 at 1 A): re-check the ATL431 cathode/base-drive budget of the bias regulator at max load; VEBO 5 V vs 6 V
- **P39 JSCJ FMMT619 (FUNCTIONAL)**: hFE 200 vs 300 at 1 A: re-run gdrv design_check release timing (10 mA base x 200 = 2 A >= 1 A assumed, inferred); IC 2 A / no ICM: confirm the simulated release peak current; lower PC

Full parameter comparison (candidate against incumbent, with datasheet page), pin mapping, qualification statement, datasheet URL, every other candidate and every REJECT reason are in `sim/data/lcsc_semis.csv`. Alternatives for positions whose incumbent is in stock are listed there as well (cost-down or second source); none of them is a DROP-IN for the small-signal MOSFETs and BJTs (lower V_DS, P_tot or ESD rating, V_th window, unconfirmed pin order), which is why the incumbents stay recommended.

## 3. Cost per module (LCSC unit prices times quantity; unit price at the 1 / 100 / 1000-piece break)

`@1` is the first break (minimum order) of the part, so a one-module order of 24 pieces would actually be priced at the 10+ tier; the three columns are the unit-price levels the CSV carries, not order sizes. Each cell is USD per module @1 / @100 / @1000.

### 3.1 Main switch and flyback switch (PV-P75 24, PV-P100-110 32, PCS-P125 36 main switches; 1 flyback switch each)

| Position and part | LCSC | Stock | Unit USD @1 / @100 / @1000 | PV-P75 | PV-P100-110 | PCS-P125 | Note |
|---|---|---|---|---|---|---|---|
| P01 Sichain SG2M040170HJ | C42456100 | 418 | 6.73 / 5.07 / 5.07 | 161.51 / 121.70 / 121.70 | 215.34 / 162.27 / 162.27 | 242.26 / 182.55 / 182.55 | incumbent; model estimate 4.07 per device |
| P01 Sichain SG2M020170HJ | C42456099 | 313 | 12.10 / 9.74 / 9.74 | 290.50 / 233.87 / 233.87 | 387.34 / 311.82 / 311.82 | 435.75 / 350.80 / 350.80 | FUNCTIONAL 1:1 swap (2x die, x2.7 gate charge) |
| P02 InventChip IV2Q171R0D7Z | C5806853 | 0 | 5.96 / 4.10 / 3.77 | 5.96 / 4.10 / 3.77 | 5.96 / 4.10 / 3.77 | 5.96 / 4.10 / 3.77 | listed, 0 stock; model estimate 2.50 |

Delta of the 1:1 SG2M020170HJ swap against the incumbent, USD per module @1 / @100 / @1000: PV-P75 +129.00 / 112.16 / 112.16, PV-P100-110 +172.00 / 149.55 / 149.55, PCS-P125 +193.50 / 168.25 / 168.25.

The WeEn alternative for the flyback switch is not on LCSC; findchips.com (2026-10-05) shows TME 4,000 pcs at 1.63 and element14 APAC 528 pcs at 1.47-2.52 USD (not read on the distributor pages): indicative only.

### 3.2 DAB study positions (per DAB module: 16 switches, 20 discrete clamp diodes, 2 clamp-module arms)

| Position and part | LCSC | Stock | Unit USD @1 / @100 / @1000 | Per DAB module @1 / @100 / @1000 | Note |
|---|---|---|---|---|---|
| P03 Sichain SG2M014120LJ | C52109935 | 14 | 10.47 / 7.42 / 7.42 | 167.47 / 118.73 / 118.73 | study alternate; 14 pcs only; study basis 7.3119 per device (mirror 90+ price) = 116.99 per module, live 90+ price 7.4206 |
| P03 Sichain S1M014120H | C22363603 | 75 | 12.41 / 8.80 / 8.80 | 198.59 / 140.80 / 140.80 | FUNCTIONAL, first generation |
| P03 CR Micro CRXQF17M120G2Z | C41507149 | 640 | 9.86 / 6.81 / 6.81 | 157.83 / 108.98 / 108.98 | FUNCTIONAL, 640 pcs, R_DS(on) +35 % |
| P03 Bestirpower BCZ120N16M1 | C53152746 | 31 | 6.45 / 4.21 / 3.80 | 103.21 / 67.41 / 60.83 | FUNCTIONAL, preliminary datasheet, 31 pcs |
| P04a Vishay VS-60EPS16-M3 | C506457 | 36 | 4.98 / 2.95 / 2.67 | 99.57 / 58.97 / 53.48 | incumbent; study uses 2.674 (1000+) = 53.48 per module; 36 pcs |
| P04a Yangjie 60EPS16 | C698648 | 2,192 | 1.61 / 0.9674 / 0.8967 | 32.12 / 19.35 / 17.93 | FUNCTIONAL; 2,192 pcs |
| P04a WeEn Semiconductors WND60P16WQ | C602729 | 0 | 5.77 / 3.59 / 3.31 | 115.48 / 71.87 / 66.29 | FUNCTIONAL; 0 pcs |

Yangjie against Vishay for the 20-device clamp, USD per DAB module @1 / @100 / @1000: -67.44 / -39.62 / -35.55.

### 3.3 All transistor and diode lines of the recommended parts (LCSC unit prices at the three levels)

Sum over the positions that have an LCSC price for the recommended part (the incumbent's ladder where nothing is recommended); lines without any LCSC price are listed below the table. These are LCSC catalogue prices at MOQ-level quantities: they say what ordering from LCSC costs, not what a 5,000-unit build would pay.

| Module | all priced lines @1 / @100 / @1000 | of which main switch | other transistor and diode lines |
|---|---|---|---|
| PV-P75 | 235.93 / 178.73 / 167.58 | 161.51 / 121.70 / 121.70 | 74.42 / 57.03 / 45.88 |
| PV-P100-110 | 308.23 / 233.79 / 219.54 | 215.34 / 162.27 / 162.27 | 92.89 / 71.52 / 57.27 |
| PCS-P125 | 306.14 / 232.05 / 222.64 | 242.26 / 182.55 / 182.55 | 63.88 / 49.49 / 40.09 |

Lines without an LCSC price: P40 ZXTP25040DFHTA. Lines priced from an incumbent that has 0 stock (the listed ladder): P02 IV2Q171R0D7Z, P42 CA-IS3417WT-Q1.

## 4. Positions with no LCSC-orderable part (distributor or RFQ needed)

| Position | Part the BOM names | Status on LCSC | Where to get it |
|---|---|---|---|
| P02 flyback switch | InventChip IV2Q171R0D7Z (AEC-Q101) | C5806853 listed, 0 pcs | LCSC back-order / InventChip RFQ; or WeEn WNSC2M1K0170B7 (FUNCTIONAL) from TME / element14; or Sichain SG2M1K0170J2J by RFQ |
| P03 DAB switch (study) | InventChip IV3Q12013T4Z | no LCSC or JLCPCB listing found | InventChip RFQ; alternates in stock are shallow (SG2M014120LJ 14 pcs) |
| P04b clamp modules (study) | Infineon DD600N16K; Techsem MDC600-16-416F3; Sirectifier SDD600N16BT | none listed (LCSC's diode modules stop at SOT-227 / ISOTOP) | brokers / maker quote |
| P09 output rectifier | Vishay VS-8ETU04S-M3 | not listed | ST STTH8L06G-TR on LCSC (FUNCTIONAL, three re-checks); Vishay direct for the exact part |
| P17 gate clamp 20 V +-2 % | Nexperia BZX84-B20,215 | not listed (B20,235 / -QR / -QVL listed with 0 pcs) | Yangjie BZX84B20 (C968219, FUNCTIONAL) or a Nexperia distributor |
| P24-P27 TVS | Bourns SMBJ33A / SMBJ36A / SMBJ58CA / SMCJ33A | Bourns not found on LCSC | MDD SMBJ (DROP-IN), Littelfuse SMBJ / SMCJ, Brightking SMCJ33A |
| P34 hold-closed switch | Vishay IRFR9214TRPbF | not listed (VBsemi relabels rejected) | UTC 2P50G-TN3-R is FUNCTIONAL (65 pcs, R_DS(on) 8.5 vs 3.0 ohm); genuine IRFR9214 from Vishay distributors |
| P39 / P40 low-V_CEsat pair | Diodes ZXTN25040DFHTA / ZXTP25040DFHTA | not listed | P39: JSCJ FMMT619 FUNCTIONAL; P40: no LCSC part carries the 6.6 A peak (onsemi NSS40200 and JSCJ FMMT720 are 4 A parts): Diodes distributors |
| Listed but 0 pcs today | TSC US1MH (P06, C17442259), Nexperia PMV30ENEAR (P32, C553092), BZX84-A18,215 (P15, C406046), CA-IS3417WT-Q1 (P42, C42420726), PBSS5540X (P38, 1 pc), BZX84-B6V8,215 (P21, 5 pcs) | see section 2 for the in-stock substitutes | LCSC back-order or the substitute |

## 5. What the datasheets do not give

- **Short-circuit withstand:** none of the SiC datasheets (Sichain SG2M040170HJ, SG2M020170HJ, SG2M014120LJ, S1M014120H; InventChip; WeEn; Bestirpower; CR Micro) states a short-circuit rating; no avalanche rating for the Sichain parts. The DESAT timing in the gate-drive design therefore rests on an assumed value (D-041 open item). WeEn WNSC2M1K0170B7 states E_AS 24.5 mJ and 100 % UIS test only.
- **Qualification:** AEC-Q101 is stated in the datasheet of the exact listed part for: InventChip IV2Q171R0D7Z, IV2Q17040T4Z and IV3Q12013T4Z; Taiwan Semiconductor US1MH; the Vishay HE3-suffix parts (US1MHE3_A/H, BYG10YHE3_A/H, SMCJ33AHE3_A/H) and SQ2318AES; Nexperia 2N7002BK, BSS138BK, PMV30ENEA, PBSS5540X, PBSS4041PX, BAS16 and the -QR Zener series; PANJIT BC817-25-AU; Yangjie BC817-25Q; Diodes BC817-25Q; Littelfuse SM712-02HTG; TI ESD2CAN24-Q1; onsemi NSV / SZ-prefix parts. Not stated (the rows say 'none stated'): Sichain (all SG2M / S1M / S2M parts), CR Micro CRXQF17M120G2Z, Bestirpower, WeEn (WNSC2M1K0170B7, WND60P16WQ, WND75P16W6), Yangjie 60EPS16 / BZX84B / MMBT3904 / MMBT3906 / BSS138, MDD, JSCJ, Hottech, UMW, Slkor, Goodwork, LRC, UTC. The incumbent Vishay BYG10Y-E3/TR and S2M-type 1000 V parts are commercial grade (AEC-Q101 only for the HE3 suffix). No maker publishes a reliability report for the parts compared.
- **Cosmic-ray (FIT versus DC voltage):** no 1700 V or 1200 V candidate publishes a curve; the D-007 open risk is unchanged by any of these alternatives. Sichain V_th spans 2.5-4.0 V (binning needed for paralleling, D-057).
- **Clamp surge with the bus voltage reapplied:** Vishay states 800 A / 3200 A2s with V_RRM reapplied; Yangjie 60EPS16 and WeEn WND60P16WQ state only the no-voltage rows (950 A / 4525 or 4513 A2s). The DAB clamp event reapplies the bus.
- **Zeners:** Yangjie BZX84B states no temperature coefficient and no reverse-surge rating (Nexperia: 12-18 mV/K and 40 W); the aux supply's V_DD,max uses the Nexperia coefficient.
- **Pin order:** confirmed in a datasheet table or drawing for every DROP-IN. Not confirmed (printed as such in the CSV): Bestirpower BCBF170N1000P1 (no pin table), Yangjie 60EPS16 (polarity 'as marked'), ARK DMZ6012E, several Yangjie / Hottech small-signal parts. These cannot be DROP-IN until a sample or the maker confirms the pins.
- **Temperature behaviour at 175 C:** every Chinese SiC maker gives V_th at 175 C as a typical value only; the false-turn-on margin cannot be recomputed on a guaranteed number.

## 6. Method and gaps

- Position list: every reference starting with Q or D in the per-board BOMs plus the power semiconductors, quantities summed from the three module BOMs (CSV columns `boards` and `qty_per_module`). The DAB study set comes from `dab_spec.json` (16 switches, 20 discrete clamp diodes of the 10-per-port design, 1 clamp-module arm per port).
- Candidates came from the LCSC pages' own alternate lists, the mirror's package listings (it returns at most 100 parts per package, ranked by stock: TO-247-4L, TO-247-4, TO-263-7, TO-263-7L, TO-247-3L and TO-247-2L were listed; LCSC-only parts and zero-stock tails can be missing, which is how finding 1 was missed), neighbouring C-numbers of the same maker (Sichain C42456085-C42456100 and C52109930-C52109940, InventChip C22368182-C22368209), web search, and the makers' sites. LCSC's search page and `wmsc.lcsc.com` refuse scripted use (HTTP 403 / client-rendered) and were not worked around. Maker sites that refused or did not serve a PDF to a plain request: www.21yangjie.com (403), assets.nexperia.com (403), semtech.com (HTML), protekdevices.com (404), st.com (no answer), Vishay (HTML viewer for some parts); the LCSC-hosted copy of the same maker datasheet was read instead and is labelled as such in `docs/SOURCES.csv`.
- Not finished: no exhaustive search for Chinese 1600 V SMA/SMB/SMC rectifiers (P05) or 8 A / 400 V D2PAK ultrafast diodes (P09); Goodwork S2M and MCC S2M (P07) not rated; the Bestirpower product list is script-loaded (only its first page was read); Siliup / HL / HXY / HTCSEMI SiC listings (P03) were rejected on traceability or price without reading their datasheets; Yangjie BZX84B10 / B3V3 and onsemi BZX84B4V7 / B6V8 had no C-number found; the P41 TL431-type shunt reference and the isolated-amplifier / driver ICs are outside this list.
- A second pass over the LCSC pages is cheap (`stock` and the ladder are on every product page); the cached page JSON of this run is not kept in the repository.

## 7. Datasheets filed

New files are under `docs/datasheets/power-semiconductors/` (transistors, rectifiers, SiC, clamp diodes) and `docs/datasheets/protection/` (Zeners, TVS, Schottky and switching diodes), registered in `docs/SOURCES.csv` with URL and SHA-256 (`python docs/fetch.py --check` passes). The CSV column `datasheet_path_if_downloaded` gives the path of each candidate's file. Several files are LCSC-hosted copies of the maker's datasheet; the maker's own revision may differ (a hash mismatch in `fetch.py` means the file was revised).

