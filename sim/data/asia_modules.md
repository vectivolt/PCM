# Chinese power modules, contactors, fuses and arresters - survey for PCS-P125, DAB-D60, PV-P75

Run: 2026-10-04 23:59 to 2026-10-05 00:55 IST (the calendar rolled over during the run; `retrieved` in the two
SOURCES.csv manifests carries the true file date 2026-10-05, the price rows keep 2026-10-04 as instructed and say
"read 2026-10-05" in the note). Plain requests only. Companion table: `sim/data/asia_modules.csv` (24 rows: A 10, B 9, C 5;
every value there is transcribed from the PDF page named in `pages`, empty = the document does not state it).
Nothing here is bench-validated and nothing was bought; "calculated" marks my own arithmetic from the figures quoted.

## 0. Headline

- **No module of any vendor has a public price.** HIITIO pages say "Get A Quote"; StarPower, Macmic, Leapers, BASiC, AccoPower
  publish none; none of the 23 candidate MPNs is on LCSC (mirror searched for each MPN, the maker names and "IGBT module");
  RS Online and Digi-Key HK refused plain requests. So "price per ampere" cannot be computed; section 4 gives the **break-even price** a module must beat
  instead, from the project's own discrete costs.
- **Nobody claims pin-compatibility with a named Western module, except AccoPower** (MINI PACK SiC half-bridge, "pin-to-pin
  compatible with mainstream Easy-1B/-2B", no part number, data sheet only on request). HIITIO's PDFs say "size similar to Easy 3
  with Cu baseplate" at most. One HIITIO PDF (HCG600FL100E3T1) carries "Infineon Technologies AG" in its file metadata: the
  document was edited from an Infineon file; that is lineage, not a compatibility statement. The "clone" idea therefore rests on
  the owner's premise, not on any maker's document.
- **A (PCS-P125):** real three-level modules exist: HIITIO T-type 1200 V/650 V 500 A (ICDC 324 A at 80 C) and I-type NPC
  at 650 / 1000 / 1200 V; StarPower 650 V I-NPC 300-400 A and Macmic 650 V I-NPC 300 A with short-circuit ratings in the
  sheet. Printed "200 A" or "450 A" labels are often 25 C figures: use the Tc column in the CSV.
- **B (DAB-D60):** E1/E2 SiC half-bridges of 5.5-8.5 mOhm exist from BASiC (on file), Leapers, HIITIO, StarPower.
  BASiC has the best documents; StarPower's SiC sheet has no switching data at all.
- **C (PV-P75):** the requested class (1700 V SiC half-bridge or boost module, 20-60 mOhm, 45 A) **does not exist** in any
  Chinese catalogue I could read. Smallest 1700 V SiC module = 62 mm / ED3 at 300 A, 6.4-6.7 mOhm (HIITIO = Leapers OEM).
- **Documents differ between HIITIO and its OEM** for the same-looking part (1700 V 62 mm: HIITIO prints Eon 20.1 mJ, Leapers
  prints "T.B.D"; creepage to heat-sink 14.5 vs 29 mm; isolation test f = 0 Hz vs 50 Hz). Treat a HIITIO module sheet as
  unverified until the maker confirms.
- **D/E/F:** HIITIO's contactor catalogue (pp. 15-16) and fuse sheets are better than its module sheets: make/break tables,
  8 kA short-circuit withstand, economised coil data, I2t, L/R and a UL Recognized certificate (HCHVF1000, file E533379).

## 1. Use A - PCS-P125 three-level (590-950 V DC, 216 A rms max phase = 305 A peak)

Project baseline (ARCHITECTURE-PCS.md): T-type, 1200 V outer / 650 V inner discretes, about 116 USD of IGBTs (about 39 USD per
phase with pads); the file's module estimate is 60-80 USD per phase "no public price - RFQ". Device stress at 950 V DC:
T-type outer 0.79 x V_CES; I-type NPC half the bus: 0.73 (650 V), 0.475 (1000 V), 0.40 (1200 V). Semikron AN17-003 (on file,
p.9-10) puts 650 V dies in 3L-NPC and 1200 V/650 V in TNPC in the 1000 V DC class (<1 FIT/cm2 for Semikron dies); none of
these makers publishes FIT-versus-V_CE data - ask for it before release.

Ranked (fit to 305 A peak / 950 V, then documentation; price unknown for all):

| # | Part | Circuit | Rating at stated Tc | Reason |
|---|---|---|---|---|
| 1 | HIITIO **HCG500FL120E3TM** | T-type, 1200 V outer / 650 V inner, optional DC cap, Easy 3B-size | ICDC 324 A at TH 80 C; VCEsat 1.81 V (500 A, 25 C); RthJH 0.175 | Same topology as the baseline, one module per phase, 3 kV/60 s. Missing: short-circuit data; Rth is junction-to-heatsink |
| 2 | HIITIO **HCG400FL120E3RA** | I-NPC 1200 V | ICDC 298 A at TH 80 C; 1.64 V (400 A) | 0.40 x V_CES: no cosmic-ray worry; two series devices in every path (project note: +330 W for NPC1); no SC data |
| 3 | StarPower **GD300MLX65B3ST** | I-NPC (NPC1) 650 V, B3 | 354 A at 25 C, **300 A at 60 C**; 1.45 V (300 A) | Best document: SC 6 us 1500 A, Zth, E at 25/125/150 C, RBSOA. Preliminary 2020, 2.5 kV isolation, 650 V at 0.73 |
| 4 | Macmic **MMG300B065PD6TC** | I-NPC 650 V, GB | 350 A at 25 C, 300 A at 60 C; 1.55 V | Terse 6-page sheet but SC 6 us 1400 A and Rth 0.17; CTI only 200 |
| 5 | HIITIO **HCG450FL100E3T1** | 6-IGBT leg, 1000 V (ANPC-like) | 310 A at Tc 65 C (outer), 300 A inner | 0.475 x V_CES; isolation tested 1 s only (4 kV); no SC data |
| 6 | HIITIO **HCG375FL065E3RC** | I-NPC 650 V, DC cap inside | ICDC 285 A at TC 80 C | 650 V silicon (price unknown); 285 A DC rating is marginal against 305 A peak |
| 7 | Leapers **DFH08TL12EZE2A** | SiC/Si hybrid T-type, E2, 150 nF cap | SiC 160 A, IGBT 190 A at Ts 80 C | Needs 2 modules per phase; body-diode data "chip: Target"; no SC |
| 8 | Macmic **MMG300WB120TLB6TC** | half of an I-NPC leg (2 series IGBT + clamp diode) | 300 A at Tc 95 C, 1200 V | 2 x 62 mm-class modules per phase (6 total); SC 10 us 1500 A |
| 9 | HIITIO **HCG300FH120A2E1** | 1200 V half-bridge, 62 mm | 300 A (no Tc printed) | Building block for a home-built outer leg; Rth, Cies, Qg printed "TBD" - unusable for thermal design |
| 10 | StarPower **GD200TLQ120L3S** | T-type 1200/650 V, baseplate-less | **100 A at 100 C** (label 200 A); diode 75 A | Shows the label trap; no larger StarPower T-type except P2-case GD900TLY120P2S |

Half-bridge IGBT building blocks also exist (HIITIO 62 mm 200-450 A, StarPower C2/C6 cases, Macmic 62 mm); only one price
signal exists: marketplace listings of StarPower GD450HFX120C6SA at 49.6-56 and 67.58-78 USD (reseller, **indicative**).
One 1200 V half-bridge for the outer pair alone would therefore already be 1.3-2 times the 38.7 USD that all discretes of a
phase cost (calculated from an unverified reseller listing).

## 2. Use B - DAB-D60 (1200 V SiC half-bridge, 6-17 mOhm per switch, 100 kHz)

| # | Part | Package | R_DS(on) typ 25 C / hot | I_D at stated Tc | Switching data | Reason / gap |
|---|---|---|---|---|---|---|
| 1 | BASiC **BMF008MR12E2G3** (on file) | E2B press-fit, SiC SBD | 8.1 / 13.5 mOhm (175 C) | 160 A, TH 80 C | Eon 2.3, Eoff 0.6 mJ at 175 C; Zth | Best documents; Rev 1.0 still "Preliminary"; no SC data |
| 2 | Leapers **DFS06HF12EZA2** | E2 | 6.0 / 9.4 mOhm (175 C), VGS 15 V | 200 A, Tc 80 C | Eon 6.15, Eoff 1.03 mJ (800 V 200 A 2.2 ohm, 25 C) | Lowest R in range; no Zth, no SC, Rth "typ only" 0.10 |
| 3 | HIITIO **HCS06FH120E1C1** | Easy 1B-class, 26 g | 6.2 / 10.3 mOhm (175 C) | 150 A, Tf 95 C | Eon 1.52, Eoff 0.93 mJ (600 V 150 A 3.3 ohm) | Smallest; no Zth, no SC |
| 4 | Leapers **DFS08HF12EZA2** (on file) | E2, 63x58 mm | 7.5 / 11.8 mOhm | 160 A, Tf 90 C | curves 25/150 C | Second source; weaker data |
| 5 | BASiC **BMF240R12E2G3** (on file) | E2B | 5.5 / 10.0 mOhm | 240 A, TH 80 C | Eon 7.4, Eoff 1.8 mJ (800 V 240 A) | Upgrade path; Coss 0.9 nF costs light-load ZVS; UL 1557 E550494 |
| 6 | StarPower **MD85HFS120L2S_B11** | L2 | 8.5 / 14.3 mOhm | 230 A at **25 C** only | **none printed** | Cannot be simulated; automotive-targeted; B3 version "not for new design" |
| 7 | Leapers **DFS08HF12DEA1** | 34 mm, 170 g | 8 / 14 mOhm | 162 A, Tc 100 C | Qrr only | Bulky for 100 kHz; body diode "Target" |
| 8 | Leapers **DFS04HF12EZA2** | E2 | 3.23 / 6.02 mOhm | 240 A, Tc 125 C | Eon 13.5, Eoff 3.85 mJ at 175 C | Below range; Rth(j-c) "T.B.D"; Coss 4.37 nF is quoted at 25 V |
| 9 | AccoPower **MINI PACK** | Easy 1B/2B pin-to-pin (maker's words) | 6 / 11 mOhm | - | - | Only written compatibility claim; sheet on request only |

HIITIO HCS09FC120E1Q1 (Easy 1B, 9 mOhm) is a **chopper**, not a half-bridge; full-bridge modules were not found. Two
half-bridges per bridge are needed either way (4 modules for the whole DAB).

## 3. Use C - PV-P75 (1000 V ports, 1100 V trip, 45 A per phase)

| Candidate | Class | Why it does not fit / what it is good for |
|---|---|---|
| HIITIO **HCS300FH170A2C1**, Leapers **DFS300HF17DFC1** (62 mm), DFS300HF17I4C1 (ED3) | 1700 V SiC half-bridge, 300 A, 6.4-6.7 mOhm | 5-10 x oversized: one module per half-bridge, 6 per PV-P75 (3 phases x 2 legs) against 24 discretes; only HIITIO's sheet prints Eon/Eoff (20.1 / 7.4 mJ at 900 V 300 A, 25 C); Leapers prints "T.B.D" |
| StarPower **GD75HFX170C1S** (also GD50/100HFX170C1S, GD150HFX170C2S) | 1700 V **IGBT** half-bridge, 75 A at 100 C | Right size, full sheet with SC 10 us. But Eon + Eoff = 25.6 + 29.9 mJ at 150 C (900 V, 75 A): about 33 mJ at 45 A, i.e. about 0.65 kW per switch at 20 kHz (calculated, linear in current) - IGBT is only an option at a few kHz |
| HIITIO **HCS05FH230E2B2** | 2300 V SiC, Easy 2B, 5 mOhm, 240 A | 0.48 x V_DSS at the 1100 V trip, but 5 x the current needed; p2 and p3 disagree on Tj max (175 vs 150 C) |
| Leapers **DFS40CU12F0Q1** | 1200 V SiC **dual boost**, F0, 40 mOhm, 50 A | Only boost-type module found; 1200 V is too low for the 2-level cell, fits only the 3-level flying-capacitor alternative that D-041 rejected on cost |

Conclusion: keep the 8 TO-247-4 discretes per phase; no module route exists at this size.

## 4. Price against the discrete solution (break-even, calculated)

| Use | Discrete cost (project figure) | Positions replaced by one module | **Module must cost less than** | Candidate price known? |
|---|---|---|---|---|
| A | 116 USD / 3 phases = 38.7 USD per phase (about 0.93 USD/kW) | 1 module per phase (HCG500, HCG400, GD300, MMG300B) | 38.7 USD, i.e. 0.12-0.13 USD per ampere of the DC rating at 80/60 C | no |
| A | same | 2 modules per phase (DFH08TL12, MMG300WB TLA+TLB) | 19.4 USD each | no |
| B | 72 USD per bridge (8 TO-247-4; if the 13.5 mOhm class, two in parallel = about 6.75 mOhm per switch at 25 C - my assumption) | 2 half-bridge modules per bridge | 36 USD each, for 5.5-8.5 mOhm per switch | no |
| C | 33 USD per phase (8 TO-247-4) | 2 half-bridge modules per phase | 16.5 USD each | no |

Marketplace anchors (all **indicative**, appended to `gen/data/prices.csv` with their URLs): StarPower 1200 V 450 A
half-bridge 49.6-78 USD; HV DC contactors 250 A 22.64 and 400 A 33.58 USD (MOQ 30); 1000 V fuses 16.7-25 USD. For
comparison with section 1: a single half-bridge module is already above the 38.7 USD a phase of discretes costs.
Verdict by arithmetic: modules would have to cost under about one third of what multi-chip SiC or three-level IGBT
modules of this size normally cost; the discrete design stays the cost leader unless an RFQ says otherwise. RFQ contacts:
sales@hiitio.com, Sales@leapers-power.com, sales@macmicst.com, inquiry@basicsemi.com, salesmarketing@accopower.com.

## 5. What each vendor's documents are worth

| Vendor | Documents found | Verdict for design work |
|---|---|---|
| **StarPower** (starpowereurope.com) | Product page with topology/package/status + PDF per part; IGBT sheets 9-15 pp with SC, Zth, RBSOA, E at 3 temperatures | **Good for IGBT** (but "preliminary" 2019-2021, never revised); SiC sheet (MD85...) is brochure-grade |
| **Macmic** (macmicst.com, product pages /en/products/power-module/igbt/<id>) | 6-page sheets with parametrics, SC, Rth | **Usable**, terse; CTI 200-225; 3-level parts are half-legs or full NPC |
| **HIITIO** (hiitio.com, hiitio.b-cdn.net PDFs, no login) | 80 module PDFs screened; 8-20 pp each | **Partial**: full curve sets but no SC data on NPC/T-type, Rth often junction-to-heatsink, "TBD" entries, isolation tests of 1 s; disagrees with OEM sheets; no equivalence claims, no prices. Contactor/fuse documents much better |
| **Leapers** (leapers-power.com, /action/download?file= links) | 8-22 pp, mostly "Preliminary Ver.A/B" | **Partial**: E2 SiC tables good, no SC/Zth, Rth sometimes "T.B.D", Coss sometimes at 25 V |
| **BASiC** (on file) | E2B sheets 11 pp, Rev 1.0/1.1 | **Best SiC module documents**; still no SC, no reliability report |
| **AccoPower** | web page only | brochure; datasheet by contact form |
| CRRC Times, Silan, Sirectifier, Techsem, Sanan | home pages and category pages only; Sirectifier IGBT-module pages returned empty shells; Silan SiC-module list not retrievable; Techsem IGBT list = MPS/MGC 1200 V families with no three-level wording; CRRC home page not examined further; Sanan SiC discretes only | nothing relevant to A/B/C found - not exhaustive |
| BYD Semiconductor, Yangjie | bydsemi.com is a parked domain (links to spaceship.com); 21yangjie.com answered 403 | not reachable |

None of the module sheets states a qualification standard (AEC-Q101, AQG324, IEC 60747); BASiC BMF240 lists UL 1557.

## 6. D - high-voltage DC contactors, 1000 V / 250-400 A

HIITIO epoxy HCZ series (separate sheets, 4 pp each) and ceramic HCF series (catalogue 2025, image-only PDF, pp. 15-16 read
by eye). All values are the maker's; resistive load; "breaking only" in the life entries means no making at that
voltage/current - precharge still needed (HCZ sheets: keep the voltage across the open contact within 20 V).

| Part | V / I rated | Contact drop | Limiting short-time current | Life at about 1000 V | Max breaking (1 op) | Short-circuit withstand | Coil / economiser | Aux contact | Dielectric coil-contacts / open contacts | Operate / release |
|---|---|---|---|---|---|---|---|---|---|---|
| HCZ01-250F-A | 1000 V / 250 A (75 mm2) | 0.06 V | 350 A 900 s; 500 A 180 s | 250 A: 500 ops | 2000 A at **320 V** | not stated | 12-36 V or 48-72 V; 40 W 0.1 s then 2.2 W | opt. 30 V 2 A | 3500 VAC 1 min | 30 / 10 ms |
| HCZ01-300F-A | 1000 V / 300 A | 0.05 V | 450 A 480 s; 600 A 30 s; 900 A 6 s | 300 A: 100 ops | 2000 A at 320 V | not stated | same | same | 3500 VAC | 30 / 10 ms |
| HCZ01-350 | 1000 V / 350 A (120 mm2) | contact resistance <= 0.4 mOhm | 450 A 480 s; 600 A 30 s; 900 A 6 s | 300 A: 100 ops | 2000 A at 320 V | not stated | same | same | 3500 VAC | 30 / 10 ms |
| HCZ03-400F-A | 1500 V / 400 A (150 mm2) | 0.03 V | 500 A 300 s; 1000 A 25 s | 300 A: 1000 ops | 3000 A at 320 V | not stated | 24 V / 12-36 V; 44 W 150 ms then 4 W | optional | 4500 VAC | 50 / 30 ms |
| HCF250E | 1500 V / 250 A | 0.05 V | 400 A 2500 s; 1000 A 100 s; 2000 A 1.5 s | 250 A 1500 V: 1000 ops (break only) | 2500 A at 800 V; 1000 A at 1500 V | **8000 A, 5 ms, no smoke or fire** | 12/24/48 V; about 60 W start, 5.4 W hold | opt. 8 V 100 mA to 30 V 2 A | 4000 / 3000 VAC | 50 / 30 ms |
| HCF300 | 1500 V / 300 A | 0.15 V | 450 A 5 min; 1000 A 25 s | 300 A 1000 V: 200 ops (break only) | 2000 A at 450 and 800 V | 8000 A, 5 ms | 12/24/48 V; about 6 W continuous | optional | 4000 / 3000 VAC | 30 / 10 ms |
| HCF350 | 1500 V / 350 A | 0.07 V | 400 A 10 min; 1000 A 30 s; 2000 A 1 s | 350 A 1500 V: 1000 ops (break only) | 2500 A at 800 V; 1000 A at 1500 V | 8000 A, 5 ms | 12/24/48 V; 50 W then 5 W | optional | 5000 / 5000 VAC | 50 / 30 ms |
| HCF400 | 1500 V / 400 A | 0.2 V | 600 A 15 min; 1200 A 30 s; 3000 A 0.6 s | 400 A 1000 V: 200 ops (break only) | 2500 A at 800 V; **2000 A at 1000 V** | 8000 A, 10 ms | 12/24/48 V; 50 W then 5 W | optional | 4000 / 3000 VAC | 30 / 10 ms |

Reading: only the ceramic HCF family states a short-circuit withstand and a break rating at 1000 V; the epoxy HCZ family
breaks 2-3 kA at 320 V only, so for 1000 V a fuse must clear first. Coil economisers are built in (2.2-5.4 W hold, 40-60 W
for 0.1-0.15 s), so a 24 V rail sees an inrush of about 1.7-2.5 A per contactor (calculated: 40-60 W / 24 V). Certificates: HCF lists
UL/CE/CB/SEMKO/CQC/CCC; HCZ01 lists REACH/RoHS/CE/UL without file numbers. Prices: none public ("Get A Quote");
marketplace EVK250 / EVK400 22.64 / 33.58 USD (MOQ 30) are for a different maker and no data sheet was read.
Sources, all under `docs/datasheets/protection/`: `HIITIO-HCZ01-250.pdf`, `-HCZ01-300`, `-HCZ01-350`, `-HCZ03-400`,
`-HCF200` (not tabulated: 200 A) and `-HVDC-Contactor-catalog-2025.pdf` (HCF250E/300 on the page labelled Page[15] = PDF page 16,
HCF350/400 on Page[16] = PDF page 17).

## 7. E - DC fuses for 1000 V battery and PV duty, 160-250 A

I2t in A2s, melting / clearing at the stated test voltage; "tau" is the test time constant L/R. Every HIITIO fuse sheet
listed has a time-current curve and a derating section (36E: curve accuracy +-15 % of current); none prints a pre-arcing
time table.

| Part | Class / size | Breaking capacity and tau | 160 A | 200 A | 225 A | 250 A | Power loss (250 A) | Approvals |
|---|---|---|---|---|---|---|---|---|
| **HCHVF1000-xxxA-38R** | aR, 38 mm cartridge, 125-400 A | 50 kA at 1000 Vdc, tau 2.5 +-0.5 ms | (150 A: 6615 / 41891) | 12285 / 74472 | 14320 / 84199 | 15624 / 103219 | 77 W | **UL Recognized E533379** (certificate on file), CE |
| HCHVT1000-xxxA-36E 000# | aR, square body, 25-250 A | 50 kA at 1000 Vdc, tau <= 2 +-1 ms, min 3 In | 4100 / 18750 | 9560 / 39700 | 11780 / 54700 | 14500 / 70590 | 45 W | UL 248-13 / IEC 60269-4 "refer to" (no certificate) |
| HCHVT1000-xxxA-43E-A 01# | aR, 50-400 A | 50 kA, min 3 In (tau not printed) | 2535 / 19850 | 5070 / 38200 | - | 8970 / 68550 | 51 W | as above |
| HCHVT1000-xxxA-38R | aR, BS88-style, 250-400 A | 50 kA, tau <= 5 ms, min 3 In; test 1.12 kVdc 51.2 kA 4.78 ms, peak arc voltage 1.49 kV | - | - | - | 10560 / 48900 | 30 W | IEC 60269-4 / UL 248-13 / JASO, UL E533379 mentioned |
| HCPVT1500-xxxB-59ES | **gPV** (full range), 1500 V, 50-630 A | 50 kA at 1500 Vdc, tau <= 2 +-0.5 ms | 8400 / 32000 | 13500 / 55000 | - | 26500 / 105000 | 82 W (45 W at 70 % load) | UL 248-19 "Pending"; conventional-time table (1.05 In >= 60 min, 1.35 In <= 120 min, 2 In <= 10-12 min) |

Reading: the aR types protect against short circuits only (they do not clear below 3 In), as the project's own HPE501
aR fuse; the gPV type covers low overload too and, being rated 1500 V, is a candidate for the PV array side at 1000 V. Fuse
choice against the module's short-circuit let-through (ARCHITECTURE-COSTFIRST.md: 50 kA at L/R <= 3 ms) is not made here;
the only HIITIO fuse with a UL file and a test tau inside 3 ms is HCHVF1000 (2.5 ms). Marketplace anchors (indicative):
1000 V 350 A semiconductor fuse 25 USD, 32-250 A PV fuse link 20 USD, NH gPV 250/315 A 16.7 USD (MOQ 100); HIITIO has no public price.
Other Chinese fuses (Hollyland NH2XLPV 160-250 A with UL E345479, Aite, Sinofuse and others) are assessed in
`sim/data/asia_fuses.md` by an earlier agent; HIITIO's fuses are not covered there. Contactors from Hongfa (HFE82V, HPE501) are on file in
`docs/datasheets/protection`, not re-assessed here.

## 8. F - residual-current transducers and DC surge arresters

| Part | Data in the document | Fit |
|---|---|---|
| HIITIO **HLB6-A1PV** (6 pp) | Fluxgate, 5 V supply 30 mA; trips at 6 mA DC / 30 mA AC (IEC 62752 / 62955 Mode 2/3); rated current **80 A single-phase / 40 A three-phase**; response times per waveform (e.g. 6 mA smooth DC 300-600 ms, 60 mA 25-60 ms); 5 kVac primary-secondary; OVC III; 3000 A surge; EMC table | Made for EV chargers: 40 A three-phase window cannot carry the PCS's 216 A rms phases. For the PV/DAB modules there is no residual-current requirement in REQUIREMENTS.md. HLB6A-EP1-PVA (Type B) PDF is image-only and was not read. No price |
| HIITIO **HCDSP2-1000/3 (R)** (3 pp) | Type 2, 1000 Vdc continuous, In 20 kA, Imax 40 kA (8/20), **Up <= 4000 V**, 25 ns, SCCR 2000 A, thermal disconnect, remote contact option, TUV/CE | Up <= 4000 V at 1000 Vdc is the maker's figure; compare it with the arresters already on file (Dehn, Phoenix Contact, Raycap, ABB, CITEL in `docs/datasheets/protection`) and with the impulse level from `sim/insulation.py` (not checked here). No price |

## 9. What I could not obtain

- Any module price (all vendors); RFQs needed. RS Online (403), Digi-Key HK (Cloudflare challenge), Digi-Key, TME, Octopart (403), Mouser/Newark/Verical (timeout), Farnell datasheet host (timeout), Alibaba (anti-bot page), CSDN article on 1700 V SiC modules (empty reply): **to read by hand**.
- AccoPower MINI PACK data sheet (contact form); Silan SiC-module list (script-loaded page); Macmic SiC modules (not scanned); Macmic TLA sibling sheet; BASiC 62 mm/ED3 sheets; HIITIO Type-B RCT sheet and the 74-page fuse catalogue (image-only, OCR'd but not read); HCF250B/C and HCF400B/C catalogue pages.
- Qualification or reliability reports for any module; FIT-versus-V_CE data; short-circuit ratings for HIITIO NPC/T-type and for all SiC modules; Rth(j-c) for DFS04HF12EZA2 and HCG300FH120A2E1.
- Verification of any pin-out against a Western outline: no outline drawing was compared, so "pin-compatible" is **unconfirmed everywhere**.
- BYD Semiconductor (parked domain), Yangjie (403), CRRC Times (not examined beyond the home page).
- A thermal or loss check of any module at 216 A rms / 16 kHz: the DC ratings quoted are not that check (sim/pcs_design.py would do it once prices justify it).
