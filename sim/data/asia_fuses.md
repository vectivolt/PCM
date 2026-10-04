# Asian fuses for the port protection board: what is documented, what it costs

Research of 2026-10-04 (plain web requests only). Nothing here is bench-validated. Curve points are READ from
figures (method and accuracy per row in `sim/data/asia_fuses.csv`, column `note`); prices are looked-up
prices, not quotes. Conversions use the ECB reference rates of 2026-10-02: 1 EUR = 1.1225 USD,
1 GBP = 1.320 USD, 1 CHF = 1.210 USD.

## 1 Verdict

* **Two Asian main links come close to what the coordination needs, and each has a hole.**
  * **Hollyland (Xiamen) NH2XLPV, 1500 V DC, 160 / 200 / 250 A**: a real maker specification (HLD-PSI-1253) with
    pre-arc and total I2t, maximum loss, a readable time-current curve and a derating curve; UL file E345479,
    TUV R 50540085. But it is an NH2XL body (191 mm), 1500 V base, and has four open points (section 3).
  * **Zhejiang Aite ATPV-NH1, 1000 V DC, 32-250 A, all in NH1 size**: the maker's web page prints pre-arc I2t,
    total I2t and loss at In for 160 / 200 / 250 A, 50 kA, gPV, IEC 60269-6 (UL 248-19 and TUV Rheinland are claimed).
    The curve is only a low-resolution image and the datasheet PDF is behind an e-mail request form.
* **No Asian gBat / aBat link of 200-250 A at 1000 V or more has curve AND I2t data.** Aite ATES-NH1B (aBat,
  DC 1000 V, 50 kA, 200 / 225 / 250 A) and Hollyland NH1XLaBat / NH2XLaBat (1500 V) give ratings only;
  Hollyland NH3LgBat (1500 V, 150 kA, NH3L body 75 x 192 mm) has a curve only. Sinofuse lists aBat/gPV series
  (RS309-MM-EV, PV312-2XL-T, RSZ307) with voltage / current range / breaking capacity only.
* **Sinofuse has full specifications (copies at Maritex and LCSC), but for the aR semiconductor link RS306**
  (I2t, losses, curves, Kt). It is partial-range ("minimum protection range >= 8 In"), so it leaves 450 A to
  about 8 In uncovered: **not usable as the port fuse**. Aite's bolted ATES-EG (1250 V AC / 1000 V DC) looks like the
  same class (its page does not state the class).
* **Small links**: Hollyland HC10gPV 1000 V, 1-20 A is fully documented (I2t, loss, curve, derating, 30 kA at
  L/R 1-3 ms). 25 / 30 A (HC10PV, UL 248-19) and 35 A (HC10LgPV, 1500 V, 85 mm body) have a curve only.
* **Prices**: the Chinese documented links have one price point (Hollyland 400 A on LCSC: 60.2 USD); Aite and
  the 160-250 A Hollyland links are quote-only. Undocumented marketplace NH1/NH2 links cost 7.7-12 USD
  (indicative, no datasheet).

## 2 Documented well enough for a coordination study

| link | what the document contains | what is missing |
|---|---|---|
| Hollyland NH2XLPV-C/D 160 / 200 / 250 A (`Hollyland-NH2XLPV-C-D-1500VDC.pdf`) | 1500 V DC, 50 kA @1500 V, pre-arc / total I2t, max loss at In, I-T curve 160-400 A (vector), derating curve -40..120 C, UL 248-19 / IEC 60269-6 | L/R, test voltage of the I2t, curve tolerance; class printed as `PV` |
| Aite ATPV-NH1 160 / 200 / 250 A (`Aite-ATPV-NH1-1000VDC-webpage.html`, curve image) | 1000 V DC, 50 kA, gPV, pre-arc / total I2t, loss at In, conventional times, IEC / UL / GB standards listed | L/R, test conditions, readable curve, derating, any PDF or certificate |
| Hollyland HC10gPV 1-20 A, 10.3x38 (`Hollyland-HC10gPV-1000VDC.pdf` + catalogue sheet) | 1000 V DC, 30 kA @ L/R 1-3 ms (catalogue sheet), I2t, loss at 0.8 and 1.0 In, I-T curve, derating, altitude, TUV | test voltage of the I2t |
| Hollyland HC10PV 25 / 30 A, HC10LgPV 35 A, NH3LgBat 200 / 250 A (one-page sheets) | voltage, breaking capacity, average I-T curve | I2t, loss, L/R, derating |
| Sinofuse RS306-01-T5Z 160 / 200 / 250 A (aR) | DC 1000 V (UL) / AC 1250 V, DC 50 kA at L/R 10 ms, I2t, loss, curves, Kt | partial-range; I2t are AC (1390 V) values; curve ends at 2.4 s |

Brochure level only (maker web pages: voltage, current range, breaking capacity; no PDF retrievable): Sinofuse
PV312-2XL-T (1500 V, 160-450 A, 50 kA, gPV), PV312-XL3-T (250-630 A), RSZ307-000-L2N / -01-T5Z / -1 (DC 1000 V
aR, 50 kA), RS309-MM (DC 1000 V, 5-800 A, 50 kA, aR), RS309-MM-EV (aBat, 50-800 A, 25 kA), EVEELB-JP (aBat
10-60 A, 20 kA), EVEGL (32-100 A, 25 kA), RS308-PV-3E (gPV 1-30 A, only 10 kA); Hollyland NH1XLaBat (50-450 A,
50 kA @1500 V), NH2XLaBat (50-630 A, 250 kA), NH2XLaR (aR, curve, 250 kA, time constant <= 4 ms); PEC EVFC-phi10.3
(DC 1000 V 10 / 20 / 30 A) and EVFP-phi51 (DC 850 V 150-500 A, DC 1000 V only at 700 A): PEC's catalogue is behind
a request form and 850 V is below the port voltage.

## 3 Open points before using the Hollyland NH2XLPV (ask the maker)

1. Breaking capacity: **50 kA** in the spec, **30 kA** in the family's one-page sheet. Use 30 kA until clarified.
2. **Curve and I2t disagree**: at 10 ms the curve gives 250 / 351 / 524 kA2s (5.0 / 5.9 / 7.2 kA) against stated
   pre-arc 24 / 44 / 80 kA2s (for HC10gPV 20 A they agree: 0.25 vs 0.208 kA2s). The coordination must use the
   curve, not the table.
3. **Loss**: "max watts loss" 55 / 70 / 80 W at In, i.e. 2.4 / 2.6 / 2.6 times Mersen NH1/NH2 (23 / 27 / 31 W).
   Four 160 A links at 135 A would dissipate about 4 x 39 W = 157 W (I2 scaling of the maximum figure) against
   4 x 13.9 W = 56 W for the Mersen links in the port report.
4. Derating (factor on In, 1.00 at 20 C): 0.842 at 60 C, 0.819 at 65 C. A 160 A link then carries 135 A at 60 C
   and 131 A at 65 C; 200 A: 168 / 164 A; 250 A: 210 / 205 A.
5. Body NH2XL (191 mm, 59 mm wide) needs a 1500 V NH2XL / NH3L base (Hollyland lists NH3LBU15); base data and
   price not obtained. Spec cover contact: sdxm@hollyfuse.com. Aite: sales@aitefuse.com (datasheet and TUV
   certificates "on request"), Sinofuse: zrchina@sinofuse.com.

## 4 Against the links used today

| | Mersen HP10NH1GPV160 | Mersen HP10NH2GPV250 | ETI 004110342 NH1 gPV 200 A (gBat 004110760 has the same numbers) | Hollyland NH2XLPV 160 / 200 / 250 | Aite ATPV-NH1 160 / 200 / 250 | Sinofuse RS306-01-T5Z 160 / 200 / 250 | Hollyland HC10gPV 20 A |
|---|---|---|---|---|---|---|---|
| class / size | gPV / NH1 | gPV / NH2 | gPV / NH1 | PV / NH2XL | gPV / NH1 | aR / bolted 01 | gPV / 10x38 |
| rated DC voltage | 1000 V | 1000 V | 1000 V | 1500 V | 1000 V | 1000 V (UL) | 1000 V |
| DC breaking | 50 kA, L/R 1 ms (UL) | same | 30 kA, L/R <= 1 ms | 30 or 50 kA, L/R not given | 50 kA, L/R not given | 50 kA, L/R 10 ms | 30 kA, L/R 1-3 ms |
| pre-arc I2t | 19 kA2s | 80 kA2s | 4.4 kA2s | 24 / 44 / 80 kA2s | 4.2 / 5.6 / 7.3 kA2s | 4.2 / 6.8 / 12.5 kA2s | 0.208 kA2s |
| total I2t | not in file | not in file | 29 kA2s | 54 / 99 / 176 kA2s (V not given) | 23.9 / 31.4 / 41.3 kA2s | 25.6 / 42 / 76.5 kA2s (AC 1390 V) | 0.462 kA2s |
| loss at In | 23 W | 31 W | 27 W | 55 / 70 / 80 W (max) | 31 / 36 / 44 W | 40 / 50 / 60 W | 3.2 W |
| derating, 65 C | 124 A of 160 A (Mersen LV formula, unconfirmed) | 194 A of 250 A | 136 A of 200 A | 0.819 | not stated | 0.80 | 0.873 |
| price (USD) | 343-381 (RS Americas) | none found | none public (RFQ) | 60.2 for the 400 A (LCSC) | quote only | none found | 6.8 (15 A, LCSC) |

Sources of the Western columns: `NH-gPV-1000VDC.pdf` (losses, 50 kA), `ETI-Green-Protect.pdf` and the ETI product
pages 004110342 / 004110379 / 004110646, `sim/out/port_design/report.md` 6.1 / 6.6 (Mersen I2t, derated currents).
ETI's NH1C gPV 160 A page (004110379) gives 20 kA, 32 W, 5.95 / 20.75 kA2s; its NH1XL gPV 250 A (004110646)
30 kA, 19 / 180 kA2s.

## 5 What the parts cost (prices in `gen/data/prices.csv`, date 2026-10-04)

| group | today in cost model | sourced range (USD) | sources behind it |
|---|---|---|---|
| 1 NH1/NH2 gPV 1000 V DC links, 160-250 A | 123 each | Western **123-381**: Siemens 3NE1224-4 110.0 EUR = **123.5 USD** (equals the 123 USD placeholder at today's rate, probably its origin; VAT status unclear), Jean Muller N1644900 146 EUR = 164 USD (net, 12-pack), Mersen HP10NH1GPV160 343-381. Chinese documented: 60.2 (Hollyland 400 A, 1500 V, LCSC). Undocumented marketplace NH1/NH2 160-250 A: 7.7-12 | 3 Western makers / 3 shops, 1 Chinese documented price, 5 marketplace listings |
| 2 battery-class links, 200-250 A, >= 1000 V | 123 (same as 1) | **no price found**: ETI 004110760, Mersen 10NH1/2GBAT200-250 (request-a-quote), Aite ATES-NH1B, Hollyland aBat, Sinofuse aBat ("inquire") | 0 prices from 5 makers; the gPV prices above are the only proxy |
| 3 contactors 1000 V DC, 250-300 A | 127 | TDK HVC43-250A-24MC at 100 pcs **82-91** (Mouser / DigiKey, search-result figures), at 1 pc 105-115; HVC43 without mirror contact at 100 pcs 127 (Cytech, the cost-model figure), DigiKey CH 89.28 CHF = 108 at 1 pc. Asian: Hongfa HFE82V-300C 750 V variant 70.7 (63 EUR, Melchioni); 300 A / 1000 V web-shop / marketplace contactors 31.5-69.9 (no datasheet). Hongfa 1000 V variants: no price | 4 TDK sources (only Cytech read on the page), 1 Hongfa distributor, 3 Chinese listings |
| 4 fan 9GT1224P1S001, quantity | 113 | **113-138** at 10-21 pcs: DigiKey 21+ 112.92, RS UK 10+ 104.14 GBP = 137.5, RS IE 10+ 121.33 EUR = 136.2; one dealer sells single pieces at 88 | 3 shops (DigiKey and RS are search-result figures; only the dealer page was read) |
| 5 1000 V DC DIN-rail arresters | 140-194 | Western **194-340**: DEHN 952 515 172.75 EUR = 194 (the cost-model top), 204 EUR net = 229 (elektro4000), 302.5 EUR = 340 (maxsel); OBO V20-C 3-PH-1000 263 EUR = 295 at 1 pc, 192 EUR = 216 at 16 pcs; Phoenix VAL-MS 260.7 EUR = 293 (not verified). Chinese marketplace 1000 V SPDs 4.5-30 (uncertified, indicative) | 3 Western makers / 5 shops (OBO and the two German DEHN shops read on the page), 3 marketplace listings |

Reading: the contactor is overstated against Mouser / DigiKey at 100 pieces (82-91 vs 127); the fan and the
arresters sit inside the Western range; the main fuse placeholder is one shop price at the cheap end of the
Western range, and no Asian link with complete data can replace it yet.

## 6 Not obtained (to download or ask by hand)

* Sinofuse download centre: selection guide (12.1 MB), application manual CN (54.1 MB) and EN (55.1 MB), e.g.
  `https://omo-oss-file110.thefastfile.com/portal-saas/pg2025110714081371491/cms/file/sinofuse electric solutions 6.10.pdf`
  and, in the same folder, `产品解决方案应用手册6.11.pdf` and `中熔电气产品选型指南 5.22.pdf`: the CDN answers plain
  requests with HTTP 403 "Forbidden Access"; its product-page images likewise. Per-series specifications for PV312,
  RSZ307 and RS309 (like the RS306 one, ZR/YC-0018-02) are not on the public site.
* Aite datasheet and TUV certificates (`https://www.aitefuse.com/download-center` asks for an e-mail address; not
  submitted). DirectIndustry viewer of Hollyland (Cloudflare challenge); PEC / iPROS catalogue (request form).
* CHINT (chintglobal.com download centre is script-rendered; only a 10x38 15 A 1000 V DC fuse seen at a Spanish
  shop), Mingrong, Galaxy, GRL, DELIXI, Sanbo, Fuji, Daito, Hinode (only AC 660 V bolted links seen), SOC, Kyosan,
  Deoksan, Kiswel: no 1000 V DC NH or bolt-on 160-250 A document found with the US-only web search or a plain
  request (sites unreachable or 403); a Chinese-language search by hand is the open task.
* DigiKey, Mouser, RS, Farnell / Newark, TME, Buerklin, Automa refuse plain requests: figures marked
  "web-search result" in `prices.csv` come from search results, not from the pages.
* Price of any gBat / aBat 200-250 A link; Hollyland and Aite 160-250 A prices; Hongfa HFE82V-300C/1000 and
  HFE85V-300M/1000 prices; price and data of the NH2XL / NH3L bases; a readable Aite curve.

## 7 Files

`sim/data/asia_fuses.csv` (15 rows: Hollyland NH2XLPV x3, HC10gPV 20 A, HC10PV x2, HC10LgPV, NH3LgBat x2, Aite
ATPV-NH1 x3, Sinofuse RS306 x3). In `docs/datasheets/protection/` (all registered in both `SOURCES.csv`):
`Hollyland-NH2XLPV-C-D-1500VDC.pdf`, `Hollyland-NH2XLPV-1500VDC.pdf`, `Hollyland-HC10gPV-1000VDC.pdf`,
`Hollyland-HC10gPV-1000VDC-catalogue.pdf`, `Hollyland-HC10PV-1000VDC.pdf`, `Hollyland-HC10LgPV-1500VDC.pdf`,
`Hollyland-NH3LgBat-1500VDC.pdf`, `Sinofuse-RS306-01-1250V.pdf`, `Aite-ATPV-NH1-1000VDC-webpage.html`,
`Aite-ATPV-NH1-1000VDC-melting-curve.jpg`, `Aite-ATES-NH1B-aBat-1000VDC-webpage.html`; 48 lines appended to
`gen/data/prices.csv`.
