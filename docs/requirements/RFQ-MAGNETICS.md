<!-- breadcrumb --> [Home](../../README.md) › [Documentation](../README.md) › RFQ index for the magnetics

# 🧲 RFQ index: the custom magnetic parts

> One request-for-quotation (RFQ) index for every custom magnetic part of the drawn modules: where each is used and how many per module, the requirement rows a quotation must hold, the tests, and the estimate we hold.

![as of](https://img.shields.io/badge/as%20of-2026--10--07-5B6B7A?style=flat-square)
![numbers](https://img.shields.io/badge/numbers-calculated%20%C2%B7%20estimated-F2A007?style=flat-square)
![bench](https://img.shields.io/badge/bench--validated-nothing-E4572E?style=flat-square)
![quotes](https://img.shields.io/badge/supplier%20quotes-none%20yet-E4572E?style=flat-square)
![parts](https://img.shields.io/badge/RFQ%20parts-10-0B1F33?style=flat-square)
![review](https://img.shields.io/badge/review%20R4-item%20E12%20open-F2A007?style=flat-square)

---

> [!IMPORTANT]
> **What this index is, and what it is not.** For each custom magnetic part it names the rows a quotation must state it
> holds — inductance **at current**, losses, insulation levels and temperature limits, not the nominal inductance alone —
> so that prices can be compared without changing the design. Nothing here is bench-validated and nothing has been
> quoted: every figure is **calculated** or **estimated** in the file it is quoted from, and the file's own labels
> (ASSUMED, ESTIMATE, FROM MEMORY, RFQ) are kept. The winding sheet of each part is the drawing (the MAG-2 deliverable of
> [REQUIREMENTS.md](REQUIREMENTS.md) §7); this page says what to ask for and what is still open. It is the documentation
> pass for item E12 of the independent review R4 ([review_r4.csv][r4]; closed, *Improvement Recommended*, D-081): an RFQ index with
> the acceptance criteria fixed, the three- and four-wire builds kept as separate assemblies, and the 48 kHz
> weight-sensitive variant noted ([Appendix A](#appendix-a--the-48-khz-weight-sensitive-variant-information-not-a-substitution)).
> It records no new decision.

## 📐 How to use it

1. **One RFQ per part.** Ten parts, ten sections (RFQ-01 to RFQ-10); each stands alone. The cover facts are in the
   tables below, the rows in the sections.
2. **Ask for compliance row by row.** For every row of a part's table the supplier states COMPLY or DEVIATE, with the
   value and its evidence (maker's data, calculation, or the test that will show it). A quotation that gives a price and
   a nominal inductance only is incomplete.
3. **The rows are the contract, the construction is not.** Each section gives the construction as designed; the supplier
   may propose equivalents (another core grade, conductor, former or potting) that meet the requirement rows. Design
   losses are the **higher** of our own model and OpenMagnetics (copper and core separately), so a supplier's loss figure
   below a loss row is not accepted until a first article confirms it.
4. **State the quantity per build.** The three-wire and four-wire inverters are separate assemblies (matrix below): a
   quotation says how many pieces go into each, and the four-wire parts are quoted as their own lines.
5. **Prices.** Per piece at 1,000 pieces (the cost model's catalogue basis) and at 5,000 modules per year of the
   product (SRC-6); one-off tooling as its own line; packed mass per piece; lead time for first articles and for
   production. Programme targets, which are our objective and not supplier availability: AC inductors prototype ≤ 8 weeks,
   production ≤ 12 weeks; DAB transformer samples before PCB release ([roadmap][road], "Critical BOM and supplier risk").
6. **Two sources, one drawing.** Two vendors are quoted from the same controlled drawing and the same acceptance test, not
   two independent designs ([roadmap][road], "Magnetics sourcing"). Cores and parts come from Asian, preferably Chinese,
   makers (SRC-1, [D-031][dec], [D-048][dec]) that are "proper, verified and reliable" (SRC-2): a complete datasheet, a
   documented qualification, an established maker. Each section lists the material data to attach.
7. **Tests.** Production tests on every part (roadmap: "L, resistance, hipot/insulation, turns ratio where applicable");
   type tests on the first articles. Section 10 of each sheet adds a generic first-article list — inductances (open
   circuit, leakage short-circuit from each side, unit-to-unit match), R<sub>dc</sub>, R<sub>ac</sub> 100–500 kHz, partial
   discharge (PD) and AC withstand at the sheet's levels, impulse, thermal run, core loss at the stated B<sub>pk</sub>. Where
   that generic text conflicts with a sheet's §5 and §7 (a "cold plate at 65 °C" run for an air-cooled part), §5 and §7
   govern.

## What a nominal-only quote would miss

| Part | The nominal figure a quote leads with | What the design depends on instead |
|---|---|---|
| RFQ-01 PV inductor | 224 µH | L at 45 A DC ≥ 207.8 µH on every part; 83.8 W loss against a 10 K margin to the 155 °C hot-spot limit |
| RFQ-02 AUX-T1 | L<sub>p</sub> 485 µH | flux ≤ 348.5 mT at the 4.07 A limit; leakage and switched capacitance (drain 1,318 V of 1,360 V); a PD-free reinforced barrier |
| RFQ-03 T_BIAS4 | ratio 1 : 6 | ≤ 5 pF between windings and PD-free ≥ 1.75 kV pk; a catalogue 1 : 1.3 transformer has 12.5 pF |
| RFQ-04 port ring | a nanocrystalline ring for a 160 / 200 A port | a **flat-µ** grade: the catalogue high-µ ring saturates under the 150 Hz common-mode current (about 3 T against 1.2 T) |
| RFQ-05 L1 | 120 µH ±10 % | L(I) above the envelope to 566 A (108 → 96 µH); 285 W at 198 A / 950 V; the 140 °C hot-spot limit reached at 60 °C inlet |
| RFQ-06 L2 | 6 µH ±10 % | L(I) ≥ 5.4 / 4.8 / 3.6 µH at 0 / 305 / 367 A |
| RFQ-07 AC CM choke | ≥ 126 µH | that value at the −25 % µ corner, with flux ≤ 0.96 T under the 150 Hz common-mode current |
| RFQ-08 DAB transformer | 11 : 12, L<sub>m</sub> 150 µH | leakage 0.94 µH ±20 % (units matched to ±5 %) as part of the 6.50 µH power-transfer inductance; the 182 W loss budget; the thermal path to the cold plate |
| RFQ-09 DAB series inductor | 5.56 µH | no saturation to 700 A at 100 °C; the litz kept out of the gap's fringing field |
| RFQ-10 AUX-HV T1 | L<sub>p</sub> 1.148 mH | leakage ≤ 34.0 µH, switched capacitance ≤ 38.7 pF, a PD-free reinforced barrier |

<sub>Sources: the sections below. A quotation to "120 µH ±10 % at 0 A" would accept a core that saturates near 450 A; the
inverter's protection chain is built on the L(I) rows ([D-078][dec], review R2-03).</sub>

## Quantity per module, and what is at stake

| RFQ | Part | PV-P75 | PV-P100/110 | PCS-P125 (3-wire) | PCS-P125-4W | DAB-D60 | Pieces per year at 5,000 modules of one product |
|---|---|---:|---:|---:|---:|---:|---|
| 01 | PV inductor L_CELL | 3 | 4 | – | – | – | 15,000 / 20,000 |
| 02 | AUX-T1 75 W | 1 | 1 | 1 | 1 | – | 5,000 per product |
| 03 | T_BIAS4 gate-bias transformer | 3 | 4 | 3 | 4 | – | 15,000 / 20,000 / 15,000 / 20,000 |
| 04 | Port CM ring (CMC160 / CMC200) | 2 | 2 | 1 | 1 | 2 (earlier CMC160) | 10,000 / 10,000 / 5,000 / 5,000 / 10,000 |
| 05 | L1 120 µH (and L<sub>N</sub>) | – | – | 3 | 4 (3 + L<sub>N</sub>) | – | 15,000 / 20,000 |
| 06 | L2 6 µH | – | – | 3 | 3 | – | 15,000 each |
| 07 | AC CM choke | – | – | 1 (3 bars) | 1 (4 bars) | – | 5,000 each |
| 08 | DAB transformer 11 : 12 | – | – | – | – | 1 matched pair (2 units) | 5,000 pairs |
| 09 | DAB series inductor | – | – | – | – | 1 | 5,000 |
| 10 | AUX-HV T1 30 W | – | – | – | – | 1 | 5,000 |

<sub>Quantities are the CUSTOM lines of [bom/PV-P75][bom-pv75], [PV-P100-110][bom-pv100], [PCS-P125][bom-pcs],
[PCS-P125-4W][bom-pcs4] and [DAB-D60-FULL][bom-dab] `_costed_BOM.csv` (column Qty). The annual quantity of a line is 5,000
× its quantity per module ([COST.md][cost]); the cost model does not credit combined volumes across products. The
DAB-D60 BOM is the roadmap's earlier platform, kept and not developed further.</sub>

Custom magnetics per module — only the lines of this index, summed from the costed BOMs (USD, catalogue / 5,000 units):

| Module | Custom magnetics | Share of the module BOM | Module BOM |
|---|---:|---:|---:|
| PV-P75 | 158.6 / 132.1 | 16 % | 979.6 / 820.4 |
| PV-P100/110 | 201.3 / 168.0 | 17 % | 1,171.3 / 980.7 |
| PCS-P125 | 546.6 / 461.9 | 34 % | 1,588.9 / 1,349.1 |
| PCS-P125-4W | 676.4 / 571.8 | 35 % | 1,925.8 / 1,636.0 |
| DAB-D60 (earlier platform) | 407.9 / 345.7 | 11 % | 3,614.7 / 2,927.3 |

- The three L1, three L2 and the AC choke of the three-wire inverter are 442 USD at 5,000 units (3 × 107.61 + 3 × 32.81 +
  21.08); the filter of the trade study of [D-060][dec] was 443 USD. **10 % of that is 44 USD per module**, which is why
  a quotation has to be comparable row by row.
- The four-wire build adds 110 USD of custom magnetics at 5,000 units — L<sub>N</sub> 107.6, the fourth bias transformer
  1.5, the four-bar choke 0.9 — of the 287 USD that separates it from the three-wire build (1,634.7 − 1,348.2). That is the
  reason to quote the two builds separately.

## Common ground

- **Environment.** Ambient −30…+60 °C, full power to 45 °C inlet then derated, forced air (PV-20, AC-02 in
  [REQUIREMENTS.md][req]); altitude declared 2,000 m (INST-46 in [INSTALLATION.md][inst]); storage −40…+70 °C (ASSUMED
  there); pollution degree 3 external, 2 internal; DC overvoltage category II, AC category III (ECO-10; INST-26).
- **Insulation basis.** The levels printed in each sheet come from [insulation_spec.json][insj]. The standard tables
  behind them were written down from memory and are marked "verify" in the [insulation report §6][ins]; the PD behaviour of
  every custom magnetic part and the comparative tracking index (CTI) of custom bobbins are listed there as unverified.
  Every supplier is asked for the CTI of former and case, the potting data and, where triple-insulated wire (TIW) is used,
  its certificate. The PD acceptance level is ≤ 10 pC throughout.
- **Loss convention.** Calculated with the winding at 90 °C and the core at 100 °C (§6 of every winding sheet);
  R<sub>dc</sub> at 110 °C where the sheet says so.
- **Estimates.** All prices are engineering estimates without a quote (confidence *low*). The 5,000-unit price in the
  BOM is the catalogue price times a class factor — 0.85 for the inductors, chokes and DAB magnetics, 0.67 for the small
  transformers (AUX-T1, T_BIAS4, AUX-HV T1) — which is ASSUMED, not a published break ([COST.md][cost], "The 5000-unit
  view").
- **As of.** The winding sheets are those generated on 2026-10-07 at 05:27; the costed BOMs those of 2026-10-07 15:57.
  [pcs_spec.json][pcs] was rewritten later (commit 5cc40a4, the R3 response); its L1 and L2 requirement rows and selected
  parts were compared with the sheets while this page was written and are unchanged. No generator was run. Re-read this
  page after the next magnetics or BOM rebuild.

## 🧲 The parts

### RFQ-01 · PV inductor L_CELL, 224 µH (rev M2)

**Used on.** One per phase, chassis-mounted in the cell air stream: PV-P75 (power board PV-PWR) **3**; PV-P100/110
(PV-PWR-4) **4**; not on the inverter or the DAB. BOM line `L_CELL 224uH (CUSTOM)`, item 6 of [PV-P75][bom-pv75] and
[PV-P100-110][bom-pv100]. The earlier-platform PV modules carry the same part (3 / 4).
**Sources.** [winding sheet][s-pv] · [design file][j-pv] · requirement [cell_spec.json][cell] `inductor.requirement`.

| Requirement row | Value | Source |
|---|---|---|
| **Inductance at current** | | |
| L at 45 A DC, every part, A<sub>L</sub> tolerance −8 % included | **≥ 207.8 µH** (nominal 225.9 µH) | sheet §2; cell_spec |
| L at zero current (L0) | nominal 243.7 µH (A<sub>L</sub> 178 nH ±8 % for the stack of two, N = 37); no acceptance limit stated (O-01) | sheet §2, §3 |
| L at the trip current 72.4 A | ≥ 0.35 × L0 (design 0.81 × L0: a powder core saturates softly, L falls to 50 % of L0 at 129 A) | sheet §2; cell_spec |
| **Currents and flux** | | |
| I<sub>dc</sub> max · I<sub>rms</sub> max · I<sub>pk</sub> normal · trip | 45 A · 47.7 A · 62.3 A · 72.4 A | cell_spec |
| Ripple · voltage across · volt-seconds | ≤ 34.6 A pk-pk at 32 kHz · ≤ 1,000 V · ≤ 7,812 V·µs | cell_spec |
| Flux | B<sub>sat</sub> 1.25 T (NPC 26, 25 °C); 0.288 T at 45 A DC, 0.445 T at the trip; ripple swing 0.209 T pk-pk | sheet §2; cell_spec |
| **Losses and temperature** | | |
| Total loss at the worst point (1,000 V, duty 0.5, 45 A DC) | **≤ 83.8 W** = core 25.4 W + copper 58.3 W; hot-spot-limited ceiling 93.2 W | sheet §2, §6 |
| R<sub>dc</sub> | ≤ 22.2 mΩ at 110 °C | sheet §4 |
| Hot spot | **≤ 155 °C** (class H 180 °C less 25 K); 144.8 °C calculated at 55 °C local air and 150 m³/h; NTC trip 149.9 °C (band 146.1–153.9 °C) | sheet §7; cell_spec; [guide 04][g04] |
| Per build: thermal limit, margin | PV-P75 87.6 W at 134 m³/h per phase, +4.4 K · PV-P100/110 79.5 W at 114 m³/h, **−5.3 K** (MG-17 open; the module derates) | sheet §7; [review][rev] |
| Mass | 1.745 kg | sheet §9 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). Two POCO NPC290026
toroids (NPC 26 powder core, 74/45/35 mm, coated 75.2/44.07/36.27 mm; A<sub>e</sub> 1,008 mm², l<sub>e</sub> 183.8 mm)
stacked with a 0.5 mm spacer; 37 turns of solid round enamelled copper 3.15 mm, grade 2 (wire class 200), one layer
close-wound at the bore, first and last turn ≥ 10 mm apart behind an insulating separator; core wrapped 2 × 0.13 mm
Nomex 410 + 1 × 0.05 mm polyimide over the epoxy coating; vacuum pressure impregnation (VPI) with class H varnish; a
negative-temperature-coefficient thermistor (NTC) at the bore-side winding centre in a reinforced pocket (R/T curve of the
BOM's NTC probe, item 7: 10 kΩ, B25/100 3,988 K, TDK 8016 equivalent); insulating base with a centre clamp, core and clamp
bonded to protective earth (PE). Studied and not chosen ([sheet §9][s-pv]):
profiled litz (79.9 USD, 52 W), coarse bunched litz (57.2 USD, 66 W), edgewise flat wire (44.6 USD, 85.6 W), NPC 40 core
with 32 turns (41.7 USD, 80.5 W, L<sub>trip</sub>/L0 0.68); rejected: POCO NPF 26 (core loss about 70 W), KDM KS 26 (42
turns do not fit one layer), POCO GPC 26 (no 74 mm toroid), one toroid (hot spot > 170 °C), NPX / NPA (1.6–1.8 × core
price).

**Insulation and safety.**
- Winding–core, basic (HV–PE, switch node): working voltage 1,000 V, recurring peak 1,273 V; impulse 6,000 V; AC 2,200 V
  rms; PD ≤ 10 pC at 1,910 V pk (extinction ≥ 1,528 V pk); creepage 3.2 / 5.0 mm (pollution degree 1 / 2); clearance
  5.5 mm at 2,000 m, 6.3 mm at 3,000 m (sheet §5).
- NTC pocket–winding, reinforced (HV–PELV, PELV = protective extra-low voltage): impulse 8,000 V; AC 4,400 V rms; PD
  ≤ 10 pC at 2,387 V pk (extinction ≥ 1,910 V pk); creepage 6.4 / 10 mm; clearance 8.0 mm at 2,000 m, 9.2 mm at 3,000 m
  (sheet §5; IC-16 in cell_spec). Pocket: 3 layers polyimide + silicone sleeve.
- Altitude: declared 2,000 m; the sheet gives the 3,000 m clearances above and specifies no further option for this part.

**Environment.** Cell air stream, forced air: design flow 150 m³/h at full load; 134 / 114 m³/h per phase in the PV-P75 /
PV-P100/110 module models; 45 °C inlet, local air 55 °C (ASSUMED); h ≈ 25 W/m²K on the wound toroid; ambient −30…+60 °C.

**Tests.** *Production (every part):* PD ≤ 10 pC at 1,910 V pk winding–core; AC 2,200 V rms 1 s winding–core; AC 4,400 V rms
1 s NTC–winding. *Type (first articles):* AC 2,200 V rms 1 min winding–core and 4,400 V rms 1 min NTC–winding; impulse
6 kV and 8 kV; L(I) to 72.4 A against the inductance rows; thermal run at 83.8 W in 150 m³/h; the generic first-article
list, with core loss taken at the part's 32 kHz and 0.105 T pk rather than the generic 100 kHz.

**Cost basis.** **40.5 USD** each at the catalogue basis, **34.4 USD** at 5,000 units (BOM factor 0.85); ESTIMATE, no
quote: core 2 × 8.0 USD (13.3 USD/kg Asian powder), solid round wire 0.54 kg at 18.0 USD/kg = 9.8, insulation 4.0
(Nomex wrap, VPI batch, base), winding 5.0 + test 2.0, +10 % ([sheet §9][s-pv]). Rev M1 (litz) was 80 USD.
Annual quantity: 15,000 (PV-P75) or 20,000 (PV-P100/110).
**Evidence to attach.** The loss and DC-bias curves of the 26µ powder core (POCO's 2026 catalogue prints the loss fit for
60µ and 75µ only; DMEGC's DE8038 E-core, the Asian E-core option, has no retrievable FeSiAl curves); the magnet-wire
datasheet (class 200, grade 2).
**Open.** O-01, O-02, O-03.

### RFQ-02 · AUX-T1, 75 W auxiliary flyback transformer (rev M1; auxiliary block AUX-75 rev A3)

**Used on.** The auxiliary flyback of every power board, one design for all four ([D-076][dec]): PV-P75 (PV-PWR) 1 ·
PV-P100/110 (PV-PWR-4) 1 · PCS-P125 (PCS-PWR) 1 · PCS-P125-4W (PCS-PWR-4W) 1. BOM line `AUX-T1 75 W (CUSTOM)`, items 12 / 12 /
16 / 18. Board-mounted.
**Sources.** [winding sheet][s-aux75] · [design file][j-aux75] · requirement [aux75_spec.json][aux75]
`transformer_requirement`, `ratings`, `temperatures`.

| Requirement row | Value | Source |
|---|---|---|
| **Inductance, flux, leakage** | | |
| Turns P : live 24 V : SELV (SELV = separated extra-low voltage) | **50 : 9 : 10**; the ratios 5.556 and 1.111 are the requirement, the absolute turns may change with the core if N<sub>p</sub> × A<sub>min</sub> ≥ 6.07 × 10⁻³ m² (design 6.435 × 10⁻³ m²) | aux75_spec; sheet §2 |
| L<sub>p</sub> (both primary halves in series) | 485 µH ±7 % at 10 kHz | sheet §2 |
| Frequency, mode | 64.9 kHz (58.3–74.2 kHz); discontinuous conduction (DCM) from 250 V at 94.2 W and from 200 V at the gates-off load | aux75_spec |
| Peak primary current | 2.56 A at full load; current-limit band 3.04–4.07 A | aux75_spec; [D-076][dec] |
| Flux at the highest limit (4.07 A, L<sub>p</sub> +7 %) | **≤ 348.5 mT on A<sub>min</sub>** (0.85 × B<sub>sat</sub> of DMR95 at 100 °C); design 328.5 mT, margin 5.7 % | sheet §2; aux75_spec |
| Leakage, primary with SELV and live shorted, 100 kHz | acceptance **≤ 12.0 µH** (sheet); the converter design runs at ≤ 18.3 µH; design estimate 15.2 µH (the 1-D value × 2); OpenMagnetics 3.4 µH (O-06) | sheet §2; aux75_spec |
| Leakage live–SELV | ≤ 0.62 µH (estimate 0.41 µH) | sheet §2; aux75_spec |
| Switched capacitance, P1 start to shield, 100 kHz | acceptance **≤ 45 pF** (sheet); the converter limit is 62.7 pF on its model's basis, 46.6 pF on the sheet's; design 41.5 pF (O-06) | sheet §2; aux75_spec |
| RMS currents | primary 0.78 A, live 2.15 A, SELV 3.88 A | aux75_spec |
| Ratings | 94.2 W continuous (live 30 W continuous, 48 W peak; SELV 42.2 W with three fans, 64.2 W with four); 112.2 W for 1 s; full rating from 250 V DC input to 1,000 V | aux75_spec |
| **Losses and temperature** | | |
| Winding loss | ≤ 1.95 W (design 1.30 W own model, 1.78 W OpenMagnetics) | sheet §2, §6 |
| Total transformer loss, rise | ≤ 2.5 W at 94.2 W (copper 2.0 + core 0.5 W); rise ≤ 40 K; hot spot ≤ 130 °C (112.5 °C calculated at the rating in the 60 °C module-internal ambient) (O-08) | aux75_spec; sheet §7 |
| Mass | 0.141 kg | sheet §9 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). DMEGC EC39A core
set in DMR95 (ETD 39 class: A<sub>e</sub> 133 mm², A<sub>min</sub> 128.7 mm², l<sub>e</sub> 103 mm, V<sub>e</sub> 13.7 cm³),
centre-leg gap 1.17 mm ground to L<sub>p</sub>, copper set back 2.0 mm from the gapped leg. Windings, inside out: P1 25
turns litz 30 × 0.10 mm (start = drain) · shield 1 turn copper foil 0.05 mm with open overlap, to BUS− · SELV 10 turns
TIW-litz 200 × 0.10 mm (three-layer extruded, certified reinforced), one layer · LIVE 9 turns litz 100 × 0.10 mm, one
layer, to the live 24 V rectifier · P2 25 turns litz 30 × 0.10 mm (finish = bulk). Single-layer build 7.97 mm of the
8.20 mm window; vacuum-potted case on a 14-pin former.

**Insulation and safety.**
- Reinforced barrier, primary + live + shield to SELV: recurring peak 1,318 V; AC 4,400 V rms 60 s; impulse 8,000 V; PD
  ≤ 10 pC at 2,472 V pk on every unit (extinction ≥ 1,977 V pk; the construction is designed for a 2,505 V pk test).
  Barrier = TIW-litz insulation + 4 × 0.06 mm tape each side of the SELV layer, minimum solid insulation 0.34 mm; SELV
  pins on their own row; creepage ≥ 20.0 mm (pollution degree 2) / 6.4 mm inside the potting; clearance ≥ 10.4 mm
  (levels 8.0 / 9.2 / 10.4 mm at 2,000 / 3,000 / 4,000 m).
- Primary ↔ live winding (both on BUS−): functional, PD-free at ≥ 1,750 V pk. Vacuum potting is required for the PD test.
- **Altitude option** (not fitted; declared 2,000 m): 3,000 m needs clearance ≥ 9.2 mm and a sea-level impulse type test
  of **10.4 kV**; 4,000 m needs 10.4 mm and 11.4 kV; 5,000 m 11.9 mm ([D-075][dec]; [PV-CTL design check][pvchk],
  "Altitude"; [insulation report §3.2][ins]; INST-46 in [INSTALLATION.md][inst] for 9.2 / 10.4 / 11.9 mm). The pin row as
  drawn already gives ≥ 10.4 mm; price the 10.4 kV test as an option (O-18).

**Environment.** Board-mounted potted ETD39-class case (about 45 × 40 × 35 mm), natural convection, about 12 K/W
(estimate); module-internal ambient 60 °C; ambient range −30…+60 °C.

**Tests.** *Production:* PD ≤ 10 pC at 2,472 V pk on every unit; the acceptance-limit note of the sheet adds leakage at
100 kHz with SELV and live shorted, switched capacitance P1 start to shield at 100 kHz, and L<sub>p</sub> at 10 kHz.
*Type:* AC 4,400 V rms 60 s; impulse 8,000 V; a thermal run (the sheet now reads the aux rating, 94.2 W continuous, fixed at the root on 2026-10-07; ask also for the
112.2 W 1 s peak, O-05); a measured L<sub>p</sub>(I) to the 4.07 A limit with the core hot, reported (O-04).

**Cost basis.** **8.8 USD** at the catalogue basis, **5.9 USD** at 5,000 units (BOM 5.896 = ×0.67; the sheet's own 5k
figure is 5.9); ESTIMATE: EC39A set 70 g 0.85, former 0.45, TIW-litz 200 × 0.1 mm about 1.0 m 1.9, litz for P and live 0.6,
foil and tape 0.2, case and vacuum potting 1.6, winding 1.6, PD routine test 0.8, +10 % ([sheet §9][s-aux75]). Annual
quantity 5,000 per product (20,000 over the four).
**Evidence to attach.** The EC39A datasheet and bobbin outline (not on file: the sheet's core data are those of the ETD
39/20/13 class and say "confirm the bobbin"); the TIW certificate (IEC 61558-2-16 / IEC 62368-1 Annex J); CTI of former
and case.
**Open.** O-04 to O-08, O-18.

### RFQ-03 · T_BIAS4, gate-bias transformer (rev M1)

**Used on.** One per gate-drive phase, each feeding four gate-drive channels from a TI SN6505B push-pull driver: PV-P75
(PV-PWR) **3** · PV-P100/110 (PV-PWR-4) **4** · PCS-P125 (PCS-PWR) **3** · PCS-P125-4W (PCS-PWR-4W) **4**; not on the DAB.
BOM line `Gate-bias transformer per phase …`, items 5 / 5 / 7 / 8.
**Sources.** [winding sheet][s-bias] · [design file][j-bias] · requirement `sim/out/gdrv_miller/bias_transformer_req.json`
(copied into the design file's `basis.requirement`).

| Requirement row | Value | Source |
|---|---|---|
| **Electrical** | | |
| Turns | 4 + 4 (bifilar, centre-tapped) : 24 × 4 secondaries; ratio 6.0 per secondary to half primary (5.9 was requested; it would need 10 : 59, which does not fit) | sheet §2 |
| Drive | 5 V (4.75–5.25 V), f<sub>min</sub> 350 kHz (420 kHz typical), 7.5 µV·s per half primary | design file `basis` |
| L of one primary half | design 36.8 µH, −25 % corner 27.6 µH (A<sub>L</sub> 2.3 µH ±25 %, EP17 DMR44 ungapped) | sheet §2, §3 |
| Magnetising current | ≤ 0.15 A pk at −25 % A<sub>L</sub> and f<sub>min</sub> (design 0.136 A); equivalent to L<sub>half</sub> ≥ 25.0 µH (derived: 7.5 µV·s / (2 × 0.15 A)) | sheet §2 |
| Winding resistance at 110 °C | half primary ≤ 0.1 Ω (design 0.020 Ω); secondary ≤ 2.0 Ω (design 0.754 Ω) | design file `basis` |
| Loads | 24.2 mA DC per secondary (31.4 mA rms); primary average 0.679 A per phase | design file `basis` |
| Flux | B<sub>pk</sub> 27.7 mT (36.8 mT on A<sub>min</sub>) at 350 kHz against ≥ 315 mT B<sub>sat</sub> at 100 °C; Curie > 215 °C | sheet §2, §7 |
| **Capacitance and leakage** | | |
| Capacitance at 100 kHz | **primary to each secondary ≤ 5 pF and secondary to secondary ≤ 5 pF**; design 3.43 pF adjacent sections, 1.77 pF one apart or high side to primary | sheet §2, §5 |
| Leakage | referred to the primary 0.29 µH (adjacent) / 0.67 µH (one apart); the criterion is rectifier commutation ≤ 5 % of the half period (design +1 %); no limit in µH stated (O-09) | sheet §2; report check `bias_tc` |
| **Losses and temperature** | | |
| Loss, rise | 0.028 W at 420 kHz with 4 × 24 mA; rise 3.3 K at about 120 K/W (estimate) | sheet §6, §7 |
| Mass | 17.4 g | sheet §9 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). DMEGC EP17 set in
DMR44, ungapped, core floating; a custom five-section bobbin (liquid-crystal polymer) with 0.6 mm flanges, sections in
the order S1 (high side A) · S2 (low side A) · P (2 × 4 turns bifilar, enamelled copper 0.40 mm grade 1, centre tap) ·
S3 (low side B) · S4 (high side B); each secondary 24 turns of TIW 0.16 mm (three-layer, IEC 61558-2-16 / IEC 62368-1
Annex J), 6 layers × 4 turns; vacuum varnish dip (no air voids at the TIW). Studied: four single-secondary parts per phase
on an EP10 DMR44 with a standard two-section bobbin and no tooling (4.40 USD catalogue, 2.40 at 5,000 units per phase,
four times the board area) — the four-secondary part is cheaper once the bobbin tooling is paid. The BOM's package field
still reads "EP10-class"; the sheet's core is the EP17.

**Insulation and safety.** Functional insulation (class B3 of ARCHITECTURE-COSTFIRST): each secondary to P and to the
other secondaries — working voltage 1,000 V DC, recurring peak 1,401 V, PD ≤ 10 pC at **≥ 1,750 V pk**, surge 3,790 V
1.2/50 µs. Pins of different windings ≥ 5.0 mm apart (skip pin positions at 2.5 mm pitch; 3.2 mm if the board is coated).
No altitude option is specified for this functional barrier.

**Environment.** Board-mounted, natural convection; tested at 85 °C ambient.

**Tests.** *Production:* L<sub>half</sub>, ratio, resistance; PD ≤ 10 pC at 1,750 V pk between windings (sample) **or**
2.5 kV DC 1 s between each pair (routine); capacitance primary–secondary and secondary–secondary ≤ 5 pF at 100 kHz
(sample). *Type:* impulse 3,790 V 1.2/50 between windings; thermal run at 85 °C ambient. The sample size and frequency
are not stated (O-09, O-17).

**Cost basis.** **2.18 USD** at the catalogue basis; at 5,000 units **1.22 USD in the sheet plus about 4,000 USD of
one-off bobbin tooling** (0.80 USD per part if spread over 5,000 parts), and 1.46 USD in the BOM (×0.67, tooling not
included). ESTIMATE: EP17 set 13.4 g, custom bobbin, TIW 0.16 mm about 0.12 USD/m (1k) / 0.08 (5k) for 2.6 m, automated
sectional winding, routine test ([sheet §9][s-bias]). Annual quantity 15,000 or 20,000 per product.
**Evidence to attach.** Capacitance and PD results on samples (ARCHITECTURE-COSTFIRST risk R-06); the EP17 DMR44 curves;
the TIW certificate.
**Open.** O-09, O-17.

### RFQ-04 · Port common-mode ring (CMC160 / CMC200; BOM line N-C-644025), rev M2

**Used on.** One ring per DC port, around both bars: PV-P75 (PV-PWR) **2** · PV-P100/110 (PV-PWR-4) **2** · PCS-P125
(PCS-PWR) **1** · PCS-P125-4W (PCS-PWR-4W) **1**. DAB-D60 (earlier platform) carries **2** × `CM choke 160 A (CUSTOM)` instead
(see below). BOM line `N-C-644025`, items 252 / 250 / 269 / 269.
**Sources.** [winding sheet][s-port] · [design file][j-port] · requirement `sim/out/port_design/port_spec.json`
(via the sheet's `basis`).

> [!WARNING]
> **Grade fixed at the root (2026-10-07).** The BOM line used to name Qingdao Yunlu's catalogue ring in its standard high-µ grade (µ<sub>i</sub> 70,000 / 24,000 ±30 %), which the sheet rejects because it saturates under the 150 Hz common-mode current. The catalog entry now specifies the flat-permeability grade (µ ≈ 30,000 at 150 Hz, Shincore SC-1K107-LP class or the Yunlu / AT&M flat grade) as an RFQ item keyed by the size code `N-C-644025`; the regenerated BOM text carries the requirement. Quote the flat grade only.

| Requirement row | Value | Source |
|---|---|---|
| Ring | one nanocrystalline flat-loop ring 63/38/25 mm, cased (Shincore SC-1K107-LP class, µ ≈ 30,000); A<sub>e</sub> 234 mm², l<sub>e</sub> 158.7 mm; µ(150 Hz) 30,320 and µ(150 kHz) 17,620 from the OpenMagnetics material data; B<sub>sat</sub> 1.2 T | sheet §2, §3 |
| Passage | single pass, + and − bars side by side: CMC160 10 × 5 mm tin-plated Cu (50 mm²), CMC200 10 × 7 mm (70 mm²); hole fill 0.147 / 0.192 (limit 0.45) | sheet §4 |
| **Flux** | **B ≤ 0.92 T** under the 150 Hz common-mode current + differential leakage + HF with µ +25 % (case B): design 0.832 T (CMC160), 0.859 T (CMC200); case C 0.946 T, below B<sub>sat</sub>; B<sub>DM</sub> 0.109 T, B<sub>HF</sub> 0.010 T | sheet §2, §6 |
| Common-mode bias | 1.7 A rms at 150 Hz with rated DC (the type-test condition) | sheet §5 |
| Rated DC through the bars | 160 A (CMC160) and 200 A (CMC200); rise ≤ 40 K at rated current (the ring has no winding: core loss about 1 mW) | sheet §1, §6; `basis.ratings_from` |
| Attenuation the board achieves with the ring | 17.2 / 18.3 / 18.3 dB at 150 kHz / 500 kHz / 2 MHz against ≥ 16 / 16 / 6 dB; the ring's own L<sub>cm</sub> (one turn) is 53.3 µH nominal (frequency not stated); the routine test is at 10 kHz with no pass band (O-10) | sheet §2, §5 |
| Mass | 0.321 kg | sheet §9 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). The ring above in a
clip-on case on the sleeved bars; no copper of its own. Asian sources: Shincore SC-1K107-LP, or a Yunlu / AT&M flat-µ
grade (field-annealed, µ ≈ 30,000); the earlier M1 construction (four stacked 80/50/25 toroids, bifilar 3-turn bars,
100 / 109 USD) was replaced by the ring (10.9 USD).

**Insulation and safety.** The ring is not an insulation part; the bars' own insulation is the basic insulation (IC-09).
Sleeve on the bars ≥ 0.75 mm, ≥ 1.5 kV DC; case CTI ≥ 600. Basic HV–PE, DC pole: working voltage 1,000 V; impulse
6,000 V (the type test takes no surge credit); AC 2,200 V rms; creepage 16 mm at the terminals, 5 mm potted; clearance
7.1 mm (the 4,000 m value; 5.5 / 6.3 mm at 2,000 / 3,000 m).

**Environment.** Chassis-mounted on the port bars, in the module air; no heat source of its own.

**Tests.** *Production:* AC 2,200 V rms 1 s (or 2,640 V 1 s) pole–pole and poles–core/case; L<sub>cm</sub> at 10 kHz.
*Type:* AC 2,200 V rms 60 s; impulse 6 kV 1.2/50; L<sub>cm</sub> at 16 and 100 kHz with 1.7 A rms 150 Hz common-mode bias
and rated DC; thermal run at rated DC (IC-09).

**Cost basis.** **10.9 USD** each at the catalogue basis, **9.27 USD** at 5,000 units (BOM ×0.85); ESTIMATE: flat
nanocrystalline 20 USD/kg for 0.27 kg plus case 1.5, insulation 2, labour 1, +10 % ([sheet §9][s-port]). Annual quantity
10,000 (PV modules) or 5,000 (inverters).
**Evidence to attach.** B–H and µ(f, T) of the flat grade at 150 Hz, 10 kHz and 150 kHz–2 MHz (the µ at 150 Hz and the grade
itself are assumptions: [report §7][rep]); the case and sleeve data.
**DAB-D60.** The earlier BOM line `CM choke 160 A (CUSTOM)` (2 per module, 65 USD each; 55.25 at 5,000 units) reads: two
windings of 160 A DC, 1,000 V DC working, L<sub>cm</sub> ≥ 0.5 mH at 10 kHz and |Z<sub>cm</sub>| ≥ 50 Ω at 16–100 kHz, no
saturation with the 150 Hz common-mode current and the differential flux (core to be set by the magnetics design, MG-10),
impulse ≥ 6 kV, 2,200 V rms 60 s, creepage ≥ 5.0 mm, rise ≤ 40 K at 160 A. MG-10 found that text incomplete; the ring
above is the construction on file for it, but the ring's attenuation needs the ≥ 127 nF to PE that is the port engineer's
BOM, and no file read for this page shows that capacitance on the DAB60 board; the 65 USD matches neither the M1 reference
(100 USD) nor the ring (10.9 USD).
**Open.** O-10, O-11 (PCS 250 A port), O-17.

### RFQ-05 · PCS-P125 converter-side filter inductor L1, 120 µH (rev M1) — and the four-wire neutral inductor L_N

**Used on.** PCS-P125 (PCS-PWR): **3**, one per phase · PCS-P125-4W (PCS-PWR-4W): **4**, the three phases plus the neutral
inductor L<sub>N</sub>, which is the same part by decision ([guide 09][g09]; see the L<sub>N</sub> row). BOM line
`L1 120u (CUSTOM)`, items 8 / 9. Chassis-mounted in the fan duct downstream of the heatsink.
**Sources.** [winding sheet][s-l1] · [design file][j-l1] · requirement [pcs_spec.json][pcs] `inductors.L1`.

| Requirement row | Value | Source |
|---|---|---|
| **Inductance at current** | | |
| L at 0 A | **120 µH ±10 %** (gap ground to ±5 %) | sheet §2 |
| L(I) minima, every part | 108 µH at 0 A · 102 at 311 A · 96 at 336 A · 78 at 397 A · **60 at 450 A (the hardware trip)**; the −10 % part delivers ≥ 107.3 µH at 450 A | pcs_spec `inductors.L1`; sheet §2 |
| **Acceptance envelope**, L<sub>inc</sub> ≥ (µH), with the core at ≥ 100 °C and at 25 °C | 0 A 108.0 · 300 A 107.8 · 450 A 107.2 · 500 A 106.5 · 520 A 105.8 · 550 A 103.0 · 566 A ≈ 96; the 141-point grid is `electrical.L_trajectory.hot_100C.envelope_H` | sheet §2a; design file |
| Flux-rule current (the acceptance ceiling) | **566 A** (+5 % part, B ≤ 0.98 B<sub>sat</sub> at 100 °C); every gates-off current of the protection chain stays below it: 520.5 A (local window) and 533.6 A (CMPSS backup at its required band top) | sheet §2a; pcs_spec `protection_chain` |
| Knee (L = 50 % of L0), recorded not accepted | hot +5 % part 577.8 A · hot nominal 606.6 A · 25 °C +5 % part 643.9 A · 25 °C nominal 676 A; OpenMagnetics' saturation figure 553 A | sheet §2a |
| Saturation flux assumed | 1.40 T at 100 °C (ESTIMATE from 1.56 T at 25 °C, data sheet); the tool's value is 1.35 T | sheet §2a |
| **Currents and ripple** | | |
| Fundamental rms | 180 A rated · 198 A continuous · 216 A for 2 min · 259.2 A for 200 ms; peaks 311 A (110 %), 336 A (120 %), 397 A (200 ms); trip 450 A | pcs_spec |
| HF ripple | 9.4 A rms (750 V) / 13.9 A rms (950 V), of which 13.0 A is common mode; ≤ 61.8 A pk-pk; carrier 32 kHz with sidebands, 64 kHz; 7.42 mV·s per half carrier period at 950 V; switch-node steps ≤ 1,050 V at up to 60 V/ns | pcs_spec; sheet §5 |
| **Losses and temperature** | | |
| Loss | **169.4 W** at 180 A / 750 V (core 55.8 + copper 113.6); **285.1 W** at 198 A / 950 V (core 107.2 + copper 178.0); 243.2 W at 216 A / 950 V; the 57 W budget printed in pcs_spec is superseded by the efficiency rules | sheet §6 |
| R<sub>dc</sub> | 1.12 mΩ per coil (two coils) at 110 °C | sheet §4 |
| Hot spot | **≤ 140 °C at 60 °C inlet** (ASSUMED limit; class F 155 °C system): 139.6 °C calculated at 198 A / 950 V, so no margin at 60 °C; 124.6 °C at 45 °C inlet; internal gradient 3.3 K | sheet §7 |
| Mass | 11.56 kg (core 8.9 kg) | sheet §3, §9 |
| **L<sub>N</sub> (four-wire only)** | the same electrical requirement; 216 A rms at 100 % unbalance, 311 A peak with half-wave loads (the L1 continuous limit); the phase-leg ripple (61.8 A pk-pk) holds only while L<sub>N</sub> ends on a node quiet at 32 kHz (C<sub>fN</sub> tied to the DC midpoint). If the phases' common-mode ripple returned through L<sub>N</sub> it would carry about 3 × 13 A rms at 32 kHz and need its own design (O-11) | pcs_spec `LN_four_wire`; [report][rep] notes; guide 09 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). Fe-based amorphous
C-core pair (Yunlu A-R series, 1K101 / 2605SA1 class), leg 30 × 100 mm, window 160 × 40 mm, A<sub>e</sub> 2,580 mm²,
l<sub>e</sub> 480.7 mm; 20 turns in two coils (10 per leg) of aluminium foil 0.80 × 144 mm, 10 layers per coil; 12 gaps of
1.17 mm (6 per leg, 14.0 mm in all, quasi-distributed with spacers); 2.5 mm glass-filled PPS formers, VPI class F/H,
banded. Studied (sheet, "Constructions compared"): nanocrystalline "recommended" 227 / 188.6 USD, 166 W worst, hot spot
109 °C; "within budget" 394.7 / 331.5 USD, 61.5 W; "lowest loss" 691.8 / 584.1 USD. A core holding the flux rule to 650 A
would cost +10.8 / +9.1 USD per inductor and still needs a slower turn-off ([D-078][dec]); powder cores were not searched
([D-060][dec]).

**Insulation and safety.** Winding–core/PE, basic, DC-side system (1,000 V DC working, overvoltage category II): impulse
6 kV 1.2/50; AC 2.2 kV rms (1 s routine, 60 s type); PD ≤ 10 pC at 1.5 kV pk (1.5 × the 1.0 kV peak working voltage).
Turn-to-turn for 1,050 V steps at 60 V/ns: 0.13 mm polyimide / Nomex interlayer between foil layers (or served litz +
0.05 mm film); the switch-node end of each coil on the outer layer with one extra wrap; 5 mm creepage margins at the coil
ends; void-free VPI. Creepage and clearance beyond the margins are not stated in the L1 sheet; the insulation row "Basic
HV–PE, switch node (… magnetic cores)" gives 3.2 / 5.0 mm creepage and 5.5 / 6.3 mm clearance at 2,000 / 3,000 m
([RFQ-01](#rfq-01--pv-inductor-l_cell-224-µh-rev-m2)). The boards draw a winding NTC in each L1 (NTC5–7; NTC8 on L<sub>N</sub>,
[PCS-PWR design check][pcschk]); the sheet lists no pocket, probe or insulation level for it (O-11).

**Environment.** Fan duct, three Delta AFB1224SHE-F00: 149 m³/h per heatsink section, the inductors downstream of the
heatsink so the air is pre-heated by the section's 469 W (+10.1 K); inlet 45 °C and 60 °C; h = 25 W/m²K on the 0.172 m² of
exposed coil and core surface (about 4 m/s past a 0.1 m body, ASSUMED — the duct around the inductors is not drawn).

**Tests.** *Production:* winding–core AC 2.2 kV rms 1 s; PD ≤ 10 pC at 1.5 kV pk; L at 0 A and at 300 A DC bias; R<sub>dc</sub>.
*Type:* impulse 6 kV 1.2/50; AC 2.2 kV rms 60 s; **L(I) to 600 A pulsed** (a single half-sine or triangular pulse, I²t
inside the winding's adiabatic rating) **at 25 °C and with the core at ≥ 100 °C**, accepted when L<sub>inc</sub> is at or
above the envelope up to 566 A, the curve recorded beyond; thermal run at 198 A + 32 kHz ripple in the duct air; the
generic first-article list. (Until [D-078][dec] the L(I) test stopped at 450 A.)

**Cost basis.** **126.6 USD** each at the catalogue basis; at 5,000 units **107.6 USD** in the BOM (×0.85) and 103.1 USD in
the sheet's own model (materials ×0.85, labour ×0.7); ESTIMATE, no quote: amorphous C-core 7.5 USD/kg for 8.9 kg = 66.8,
aluminium foil 5.5 USD/kg = 11.6, insulation 9.7, labour 27 (10 + 0.5 per turn + core assembly 4 + test 3), +10 %
([sheet §9][s-l1]). Annual quantity 15,000 (three-wire) or 20,000 (four-wire).
**Evidence to attach.** The amorphous alloy's B–H, B<sub>sat</sub> against temperature and loss at 20–50 kHz (Yunlu's
amorphous C-core curve is an image; the loss fit used is quoted from memory from Metglas data), the gap-spacer tolerance,
the foil and interlayer data.
**Register.** MG-18 (open) belongs to the withdrawn 24 kHz L1; the present L1 passes its checks `pcs_l1_*`
([report][rep]).
**Open.** O-01, O-11, O-12.

### RFQ-06 · PCS-P125 grid-side filter inductor L2, 6 µH (rev M1)

**Used on.** PCS-P125 **3** · PCS-P125-4W **3** (there is no neutral L2). BOM line `L2 6u (CUSTOM)`, items 9 / 10.
**Sources.** [winding sheet][s-l2] · [design file][j-l2] · requirement [pcs_spec.json][pcs] `inductors.L2`.

| Requirement row | Value | Source |
|---|---|---|
| L at 0 A | **6 µH ±10 %** (gap ground to ±5 %) | sheet §2 |
| L(I) minima, every part | 5.4 µH at 0 A · 4.8 at 305 A · **3.6 at 367 A**; the −10 % part delivers 5.53 / 5.47 / 5.41 µH | pcs_spec; sheet §2 |
| Flux | 1.11 T at the 367 A trip against B<sub>sat</sub> 1.40 T (100 °C) / 1.56 T (25 °C); rule B ≤ 0.98 B<sub>sat</sub> | sheet §2 |
| Currents | 180 A rated · 198 A continuous · 216 A (2 min) · 259.2 A (200 ms); HF < 0.5 % of rated (0.9 A rms taken); ripple 3.1 A pk-pk | pcs_spec; sheet §2 |
| Loss | **11.4 W** at 180 A / 750 V (budget 12 W); 13.7 W at 198 A / 950 V; 16.3 W at 216 A | sheet §6 |
| Hot spot | ≤ 140 °C at 60 °C inlet; 86.3 °C calculated (71.3 °C at 45 °C inlet) | sheet §7 |
| Mass · R<sub>dc</sub> | 1.172 kg · 0.17 mΩ per coil at 110 °C | sheet §4, §9 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). Fe-based amorphous
C-core pair, leg 15 × 40 mm, window 80 × 20 mm, A<sub>e</sub> 516 mm², l<sub>e</sub> 240.4 mm; 4 turns in two coils (2 per
leg) of aluminium foil 1.50 × 64 mm, 2 layers per coil; 2 gaps of 1.20 mm (1 per leg, 2.4 mm in all); 2.5 mm glass-filled
PPS formers, VPI class F/H, banded.

**Insulation and safety.** Winding–core/PE, basic, mains side (overvoltage category III): working voltage 375.6 V; impulse
4 kV (6 kV if the AC surge protective device, SPD, is not credited — the sheet tests 6 kV); AC 1.5 kV rms; 2.5 mm
formers, 5 mm margins, VPI. Creepage and clearance values are not stated in the sheet (O-11).

**Environment.** As RFQ-05 (same duct, same air).

**Tests.** *Production:* winding–core AC 1.5 kV rms 1 s; L at 0 A and 300 A DC; R<sub>dc</sub>. *Type:* impulse 6 kV; L(I) to
367 A; thermal run at 198 A.

**Cost basis.** **38.6 USD** each at the catalogue basis; 5,000 units **32.8 USD** in the BOM (×0.85) and 29.7 USD in the
sheet's model; ESTIMATE: C-core 6.7, foil 1.2, insulation 8.2, labour 19, +10 % ([sheet §9][s-l2]). Annual quantity
15,000 per product.
**Evidence to attach.** As RFQ-05.
**Open.** O-01, O-11, O-12.

### RFQ-07 · PCS-P125 AC common-mode choke (rev M1)

**Used on.** PCS-P125 **1** (three bars 25 × 3 mm flat-stacked; BOM `CM choke (CUSTOM)`, item 4) · PCS-P125-4W **1** (four
bars 16 × 4.5 mm on edge through the same cores; BOM `CM choke 4W (CUSTOM)`, item 5). Chassis-mounted over the phase
(and neutral) busbars. The Y1 capacitors are separate BOM lines.
**Sources.** [winding sheet][s-pcm] · [design file][j-pcm] · requirement [pcs_spec.json][pcs] `inductors.AC_CM_choke`,
`ports_and_common_mode`.

| Requirement row | Value | Source |
|---|---|---|
| L<sub>cm</sub> | **≥ 126.1 µH at 32 kHz, 1 V, on every part**, i.e. across the µ tolerance; design nominal 193.5 µH, −25 % µ corner 145 µH, OpenMagnetics 174.7 µH | sheet §2 |
| Turns, cores | N = 1 (the bars pass once); 2 cores | sheet §2 |
| Flux | B ≤ 0.96 T in total; design 0.89 T = 150 Hz common-mode 1.25 A rms → 0.620 T, differential leakage at 198 A → 0.236 T (0.334 T at 405 A), HF 0.036 T (3.5 V rms); the differential leakage is taken as 0.3 % of L<sub>cm</sub> (ASSUMED) | sheet §2; report check `pcs_cm_B` |
| Loss | core ≤ 1 W (0.5 W); no winding, no rise | sheet §6, §7 |
| Mass | 0.777 kg | sheet §9 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). Two Yunlu
N-R-564440 rectangular nanocrystalline cores (56 × 44.6 × 40 mm, window 33 × 21.6 mm, A<sub>e</sub> 345 mm², l<sub>e</sub>
134.4 mm) in a **flat-µ grade (µ 30,000 ±25 %, RFQ)**, stacked over the busbars; bars sleeved ≥ 1 mm (rated 1.5 kV rms)
inside cased cores. Three-wire stack 19 mm; four-wire bars 16 × 4.5 mm on edge, 30.5 × 18 mm in the 33 × 21.6 mm window.
The MAS reference material is Shincore SC-1K107-LP.

**Insulation and safety.** Mains side, basic (overvoltage category III), as L2: working voltage 375.6 V; impulse 6 kV;
AC 1.5 kV rms. No altitude option is specified.

**Environment.** Fan duct downstream of the heatsink (as RFQ-05); no winding loss.

**Tests.** *Production:* L<sub>cm</sub> at 32 kHz, 1 V; bar–core 1.5 kV rms 1 s. *Type:* impulse 6 kV; L<sub>cm</sub> with 1.25 A
rms 150 Hz common-mode bias.

**Cost basis.** Three-wire **24.8 USD**, four-wire **25.8 USD** (+1.0 for the fourth bar) at the catalogue basis; 5,000 units
**21.08 / 21.93 USD** in the BOM (×0.85), 20.6 USD in the sheet; ESTIMATE: flat nanocrystalline 20 USD/kg plus 1.5 USD case
per core, insulation 3, labour 2 + 0.5 per core, +10 % ([sheet §9][s-pcm]). Annual quantity 5,000 of each variant.
**Evidence to attach.** The flat grade's µ(f, T) and B–H (150 Hz, 32 kHz); whether the two Yunlu cores are catalogue parts
in that grade.
**Open.** O-12, O-13.

### RFQ-08 · DAB-D60 power transformer 11 : 12, matched pair (rev M1)

**Used on.** DAB-D60 (DAB60): **1** assembly = two identical potted units on one cold plate; the BOM counts the pair as one
line `Transformer 11:12 (CUSTOM)`, item 8. Not used on the PV modules or the inverter.
**Sources.** [winding sheet][s-dabx] · [design file][j-dabx] · requirement [dab_spec.json][dab] `transformer`; RFQ table in
the [DAB report §9][dabrep].

**Known shortfall, calculated (MG-16, open).** At the 137.3 A worst corner the pair loses 188.6 W against a 182 W budget and
the hot spot is 133 °C (with the DMEGC core-set loss limit) against 130 °C at 65 °C coolant; the window is full. Options on
file: coolant ≤ 62 °C at that corner or a derated current above it; three units in parallel; settle it in the DAB cost
round. The DAB magnetics are the part of this index most likely to change.

| Requirement row | Value | Source |
|---|---|---|
| Turns, arrangement | 11 : 12 (n = 0.9167); primaries in parallel, secondaries in parallel; P–S–P interleaved (primary 6 + 5 turns around the 12-turn secondary) | sheet §1, §4 |
| L<sub>m</sub> (100 kHz, open secondary) | **150 µH ±10 %** for the pair (300 µH per unit); non-magnetic spacer gap ground to A<sub>L</sub> | sheet §2, §3 |
| Leakage (referred to N1, secondary shorted, 100 kHz) | **0.94 µH ±20 %** for the pair (1.88 µH per unit); the two units matched within ±5 % (current sharing) | sheet §2 |
| Total series inductance | **6.50 µH ±5 %** = transformer leakage + the series inductor of RFQ-09, **measured as a matched set**; only this total is an electrical requirement, the split 0.94 + 5.56 µH is the design's | dab_spec `L_split_note`; sheet §2 |
| Volt-seconds, flux, frequency | 4.75 mV·s per half period (950 V × 5 µs); B<sub>pk</sub> 0.135 T at 950 V; 100 kHz | sheet §2 |
| Currents (pair) | I1 rms max 137.3 A (68.65 A per unit), I1 pk 169.3 A; I2 rms 128.6 A, pk 154.8 A | sheet §2; dab_spec |
| Loss | **≤ 182 W at the worst corner** (137.3 A with the v2low current shape + 950 V flux); calculated 188.6 W (core 45.4 + copper 143.2), 92.2 W at 60 kW matched, 31.0 W at 6 kW light | sheet §6; report check `dab_m1_loss` |
| Hot spot | **≤ 130 °C** at the coolant below; 125.7 °C with typical core loss, 133.2 °C with the core-set loss limit; R<sub>th</sub> hot spot to coolant 0.644 K/W per unit; class F (155 °C) or better | sheet §7 |
| Winding resistance at 90 °C, per unit | primary part 1 2.66 mΩ · secondary 6.89 mΩ · primary part 2 3.03 mΩ | sheet §4 |
| Interwinding capacitance | **≤ 300 pF** (target; measure) | DAB report §9 |
| Embedded NTC | 10 kΩ ±2 %, R/T 8016 (B25/100 3,988 K) at the winding hot spot, leads reinforced to PELV; the hardware trip for magnetics NTCs is 140 °C | BOM text; [DAB guide][g10] |
| Mass | 5.55 kg (pair) | sheet §9 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). Per unit: two DMEGC
EE80 sets in DMR95 stacked (A<sub>e</sub> 1,600 mm², l<sub>e</sub> 183.6 mm), a 0.392 mm non-magnetic spacer in all three legs
ground to A<sub>L</sub>; windings inside out: primary part 1, 6 turns profiled litz 5,858 × 0.05 mm · secondary 12 turns
profiled litz 5,369 × 0.05 mm · primary part 2, 5 turns profiled litz 5,858 × 0.05 mm, each served + 0.25 mm polyimide
wrap; one-piece 2 mm glass-filled PBT/PPS former with flanges; P–S barriers 2.0 mm (Nomex 410 2 × 0.25 mm + polyimide film
4 × 0.05 mm + impregnation), each overlapping the winding edges by ≥ 4 mm; **vacuum-potted** (thermally conductive epoxy
≥ 1.5 W/mK, void-free) in an aluminium housing (base ≥ 6 mm) bolted to the cold plate with a gap pad ≥ 3 W/mK, contact
area ≥ 0.013 m² per unit (housing about 115 × 115 mm). Alternatives: TDG TP4A or DMEGC DMR96A in the same EE80 set (RFQ).
Litz grade is the one cost lever: 0.071 mm saves about 20 USD per pair for +25 W at 60 kW and pushes the hot spot past
130 °C ([report][rep], "DAB magnetics").

**Insulation and safety.**

| Barrier | Working voltage | Type of insulation | AC (rms) | Impulse | PD ≤ 10 pC at | Creepage · clearance (2,000 / 3,000 / 4,000 m) |
|---|---|---|---|---|---|---|
| Port 1 ↔ port 2 (simple separation) | 1,850 V DC, recurring peak 2,100 V | basic | 3,323 V 60 s (or 4,700 V DC) | 6,000 V | 3,150 V pk (extinction ≥ 2,520 V) | creepage ≥ 7.5 mm (pollution degree 1; 10 mm achieved in the potting), 20 mm along the housing (pollution degree 2, group IIIa) or 10 mm with CTI ≥ 600; clearance 5.5 / 6.3 / 7.1 mm |
| Windings ↔ core | 950 V DC (port 1), 900 V (port 2) | basic | 2,200 V | 6,000 V | 1,800 V pk (1,724 V port 2) | creepage 5 mm margins at both winding ends; clearance 5.5 / 6.3 / 7.1 mm |
| NTC ↔ winding | 950 V | reinforced | 4,400 V | 8,000 V | 2,250 V pk | creepage 6.4 / 10 mm; clearance 8.0 / 9.2 mm |

<sub>Sheet §5; dab_spec `transformer.isolation`. Terminations on opposite sides (port 1 / port 2).</sub>

**Environment.** Liquid cold plate. The sheet's design point is **65 °C coolant at the magnetics**; the DAB study's worst-case
inlet is 50 °C at 9 L/min and the magnetics are last in the flow order after the two bridges ([dab_spec.json][dab]
`thermal`). The dab_spec hot-spot target "≤ 130 °C at 50 °C coolant" is the earlier statement.

**Tests.** *Production:* port 1–port 2 AC 3,323 V rms 60 s (or 4,700 V DC); windings–core AC 2,200 V rms; PD ≤ 10 pC at
3,149 V pk (P–S) and 1,799 V pk (windings–core); NTC–winding 4,400 V rms; L<sub>m</sub>, L<sub>sigma</sub> and L<sub>total</sub> of
the matched set at 100 kHz. *Type:* impulse 6 kV 1.2/50 P–S and windings–core; PD at 1.2 ×; thermal run on the cold plate with
the worst-case loss, with a **measured R<sub>th</sub>** (MG-02); R<sub>ac</sub> at 100 / 300 / 500 kHz; loss at 800 V / 60 kW in
a DAB or calorimeter, and the **core-set loss at 0.126 T, 100 kHz, 100 °C with the real waveform** (MG-03). Second source on
the same drawing and acceptance test.

**Cost basis.** **186 USD** per pair at the catalogue basis, **158.1 USD** at 5,000 units (BOM ×0.85); ESTIMATE, no quote:
four EE80 DMR95 sets 2.96 kg at 6.0 USD/kg, litz 0.05 mm 1.39 kg at 60 USD/kg of copper, aluminium housings, potting and
former 32, labour and test 36, +10 % ([sheet §9][s-dabx]). Annual quantity 5,000 pairs (10,000 units).
**Evidence to attach.** The DMR95 core-set loss curve at 100 kHz and 100 °C (the sheet's R34-toroid data are not the
core-set limit); litz strand-insulation grade; potting conductivity; housing drawing.
**Records disagree.** The BOM text of this line is the earlier hand-off (L<sub>sigma</sub> 4.38 µH, loss ≤ 159 W, hot spot ≤ 130 °C at
50 °C coolant, 118.5 A, PM 114/93 reference core) — **use the sheet**. The drawn DAB60 rev B0 is frozen on the earlier split
(4.38 + 2.12 µH); the total 6.50 µH is the same.
**Open.** O-14, O-17.

### RFQ-09 · DAB-D60 series inductor L_SER (rev M1)

**Used on.** DAB-D60 **1**, primary (port-1) side in series with the transformer, cold-plate mounted. BOM line
`2.12 uH series L (CUSTOM)`, item 1 — **the value in that line is superseded: it becomes 5.56 µH** (the line's own estimate note
says so).
**Sources.** [winding sheet][s-dabl] · [design file][j-dabl] · requirement [dab_spec.json][dab] `series_inductor`.

**Known shortfall, calculated (MG-15, open).** The requirement rose from 275 A to 700 A (1.1 × the 632 A trip fault peak at
100 °C, [D-068][dec]); the three-turn construction reaches 0.405 T = 0.99 of B<sub>sat</sub> (0.41 T, typical, 100 °C),
against the 0.8 check limit. Four turns on the same cores would give 0.74 B<sub>sat</sub> at 106 W worst; or accept three
turns on a measured L(I) at 100 °C.

| Requirement row | Value | Source |
|---|---|---|
| Inductance | **5.56 µH** nominal, trim range 5.05–6.07 µH by gap shims, so that transformer leakage + L = 6.50 µH ±5 % as a matched set | sheet §2 |
| **Saturation** | **no saturation to I<sub>sat,min</sub> = 700 A at 100 °C**; B at 700 A calculated 0.405 T (0.99 B<sub>sat</sub>, MG-15); B at the 169.3 A peak 0.098 T | sheet §2; [review][rev] |
| Currents | I<sub>rms</sub> max 137.3 A · I<sub>pk</sub> max 169.3 A · frequency 100 kHz | sheet §2 |
| Loss | 8.3 W (6 kW light) · 44.1 W (60 kW matched) · 77.0 W (v2low 94 A) · **90.1 W at the 137.3 A worst corner** (core 40.3 + copper 49.8); the maps carry ≤ 80.3 W (O-15) | sheet §6; dab_spec |
| Hot spot | ≤ 130 °C; 108.9 °C calculated at 65 °C coolant (R<sub>th</sub> winding 0.627 K/W) | sheet §7 |
| Mass | 3.69 kg | sheet §9 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). Four DMEGC EE80 sets in
DMR95 stacked (A<sub>e</sub> 3,200 mm², V<sub>e</sub> 587.5 cm³); 3 turns of profiled litz 12,732 × 0.05 mm (13.7 × 3.4 mm,
25 mm² of copper, MLT 0.426 m); quasi-distributed centre-leg gap of six 1.11 mm ferrite I-segments (6.65 mm in all, final
by trim shims), outer legs ungapped; winding on a former set back 7 mm from the gapped leg; potted, alone in an aluminium
housing or with the transformer pair on the cold plate (gap pad ≥ 3 W/mK). Powder cores were rejected (core loss at 100 kHz
and full AC flux). Alternative core: TDG TP4A in the same E80-class set (RFQ).

**Insulation and safety.** Winding–core, basic, 950 V DC working (recurring peak 1,200 V; the winding sits at the bridge-1
switch node, 57 V/ns, core and bracket on the earthed cold plate): AC 2,200 V rms 60 s; impulse 6 kV; PD ≤ 10 pC at
1,799 V pk (extinction ≥ 1,440 V); creepage 5 mm margins (pollution degree 2); clearance 5.5 / 6.3 / 7.1 mm at 2,000 / 3,000 /
4,000 m; former wall 7 mm + 5 mm margins.

**Environment.** Liquid cold plate, 65 °C coolant at the magnetics (design point; see RFQ-08).

**Tests.** *Production:* winding–core AC 2,200 V rms 60 s; PD ≤ 10 pC at 1,799 V pk; L (matched set with the transformer).
*Type:* impulse 6 kV; **measured L(I) to 700 A at 100 °C** (O-15); thermal run on the cold plate with the worst-case loss;
the generic first-article list.

**Cost basis.** **86 USD** at the catalogue basis, **73.1 USD** at 5,000 units (BOM ×0.85); ESTIMATE, no quote: cores 27.8,
litz 0.05 mm 19.8, housing and potting 16, labour and test 14.4, +10 % ([sheet §9][s-dabl]). Annual quantity 5,000.
**Evidence to attach.** DMR95 data at 100 °C including B<sub>sat</sub> at 100 °C (the sheet uses 0.41 T, typical); the gap-segment
drawing and shim range.
**Records disagree.** The BOM text still reads 2.12 µH, trim 1.24–2.99 µH, 118.5 A, no saturation to 275 A, loss ≤ 11.2 W and a
PM 114/93 reference — **use the sheet**; the magnetics report's cost section and MG-15 still quote 635 A, the sheet and the DAB
guide use 700 A.
**Open.** O-14, O-15.

### RFQ-10 · AUX-HV T1, 30 W flyback transformer (rev M1) — DAB-D60 and the earlier platform

**Used on.** DAB-D60 (board AUX-HV_DAB) **1**, BOM item 13; the earlier PV-P75-FULL and PV-P100-110-FULL modules carry one
each on AUX-HV. The cost-first modules use RFQ-02 instead.
**Sources.** [winding sheet][s-auxhv] · [design file][j-auxhv] · requirement `sim/out/aux_hv_design/aux_hv_spec.json` rev C1.

| Requirement row | Value | Source |
|---|---|---|
| Turns | 90 : 15 : 16 (P : S : AUX), n = 6 | sheet §2 |
| L<sub>p</sub> | **1.148 mH ±7 % at 10 kHz** (gap 1.156 mm ground to A<sub>L</sub> 141.8 nH; outer legs ungapped) | sheet §2 |
| Frequency, mode | 64.9 kHz, DCM | sheet §2 |
| Current, flux | 0.95 A pk at full load, limit 1.74 A; B<sub>pk</sub> 0.131 T; limit 0.259 T on A<sub>min</sub> (rule 0.3485 T = 0.85 B<sub>sat</sub> at 100 °C); no L(I) test is written (O-16) | sheet §2 |
| Leakage (S shorted) | estimate 31.6 µH (1-D 15.8 µH × 2); **≤ 34.0 µH** | sheet §2; BOM text |
| Switched capacitance P1–shield | design 24.5 pF; **≤ 38.7 pF** (scaled 32 pF against that limit) | sheet §2 |
| Losses, rise | winding ≤ 1.52 W (0.69 W own, 1.04 W OpenMagnetics); rise 13 K at about 15 K/W (potted case 38 × 30 × 30 mm, natural convection) | sheet §2, §6, §7 |
| Mass | 77 g | sheet §9 |

**Construction as designed** (the supplier may propose equivalents that meet the requirement rows). DMEGC EC34A set in
DMR95 (ETD 34/17/11 class, A<sub>e</sub> 97.5 mm², A<sub>min</sub> 91.6 mm²), windings inside out: P1 45 turns enamelled copper
0.30 mm grade 2 (start = drain) · shield 1 turn copper foil 0.05 mm to PGND · S 15 turns TIW-litz 64 × 0.10 mm (three-layer
extruded, certified for reinforced insulation, IEC 61558-2-16 / IEC 62368-1 Annex J), one layer, unbroken to the pin and
out in a PTFE sleeve · AUX 16 turns enamelled copper 0.25 mm spaced over the breadth · P2 45 turns 0.30 mm; copper set back
2.0 mm from the gapped leg (1.0 mm former wall + 1.0 mm spacer); vacuum-potted case, 14-pin former.

**Insulation and safety.** Reinforced HV–PELV: working voltage 1,000 V DC, recurring peak 1,323 V at 65 kHz (1,428 V at the
1,100 V trip); AC 4,400 V rms 60 s; impulse 8,000 V 1.2/50; PD ≤ 10 pC at 2,461 V pk (extinction ≥ 1,968 V pk), primary +
aux + shield to secondary. Barrier = TIW three-layer extruded insulation + 4 layers of 0.06 mm polyester tape each side of S,
minimum solid insulation 0.34 mm; primary pins on one row, secondary pins on the opposite row, the core a primary-side
conductive part. Creepage **20 mm** along the case / former between the pin rows (pollution degree 2, group IIIa as the CTI is
unknown), **10 mm if former and case have CTI ≥ 600**, 6.4 mm inside the potting; clearance ≥ 10.4 mm (8.0 / 9.2 / 10.4 mm at
2,000 / 3,000 / 4,000 m). **Vacuum potting (epoxy, class F, void-free) is required** for the PD test.

**Environment.** Board-mounted, no heatsink; module-internal ambient 60 °C.

**Tests.** *Production:* PD ≤ 10 pC at 2,461 V pk (extinction ≥ 1,968 V pk). *Type:* AC 4,400 V rms 60 s; impulse 8,000 V
1.2/50; thermal run at 30 W; the generic first-article list. MG-13's RFQ items: TIW certificate for reinforced insulation at
the working voltage, PD extinction above 1.5 × the recurring peak, creepage drawing of the former.

**Cost basis.** **5.9 USD** at the catalogue basis, **3.95 USD** at 5,000 units (BOM ×0.67); ESTIMATE, no quote: EC34A set
0.40 (39 g), ETD34 14-pin former (CTI ≥ 600 grade) 0.35, TIW-litz 64 × 0.10 mm about 1.3 m at about 0.9 USD/m, wire, foil, tape
and spacer 0.40, case and vacuum potting 1.20, winding 1.20, PD and hipot routine test 0.60, +10 % ([sheet §9][s-auxhv]).
Annual quantity 5,000.
**Evidence to attach.** TIW certificate; CTI of former and case; the EC34A drawing (the DMEGC datasheet is on file, its
outline against ETD34 formers is the sheet's note).
**Open.** O-16.

## ⚠️ Open rows and conflicts between records

Rows for which no number was found in the files, and places where two records disagree. Nothing is invented here: a
"derived" value says how it follows from stated figures. The RFQ rule for each is in the last column.

| ID | Part | Row or conflict | What the files give | RFQ rule |
|---|---|---|---|---|
| O-01 | 01, 05, 06 | L0 limit; frequency and signal level at which L(I) is measured | PV inductor: no L0 limit; L1/L2: "L at 0 A and 300 A DC bias", no frequency | ask the supplier to state the method and report both |
| O-02 | 01 | embedded NTC: tolerance and upper temperature rating | the lug probe of the same R/T curve is 1 %, −40…150 °C; the winding runs to 155 °C | ask for a part rated ≥ 155 °C and its tolerance |
| O-03 | 01 | NTC–winding PD at 2,387 V pk | in the sheet's level table and IC-16, not in its routine-test line | quote it as type test and as routine test |
| O-04 | 02 | L<sub>p</sub>(I) acceptance up to the 4.07 A limit, hot | only the flux limit (348.5 mT) and N<sub>p</sub> × A<sub>min</sub> are stated; no test, no pass level | report the measured curve; the owner sets the pass level |
| O-05 | 02 | thermal type-test power | resolved 2026-10-07: the sheet reads the aux rating (94.2 W continuous); the 112.2 W 1 s peak run is still to be priced | price the 112.2 W run |
| O-06 | 02 | leakage and capacitance limits | sheet: ≤ 12.0 µH, ≤ 45 pF (drain budget "1,336 V vs 1,360 V"); aux75_spec: ≤ 18.3 µH, ≤ 62.7 pF (46.6 pF on the sheet's basis), drain 1,318 V | quote to the sheet's stricter acceptance limits; a result between them is a deviation request |
| O-07 | 02 | EC39A datasheet and bobbin | cited by the sheet, not on file or in the manifest; "confirm the bobbin" | supplier supplies both |
| O-08 | 02 | transformer loss | requirement ≤ 2.5 W at 94.2 W; sheet 1.52 W at 112.2 W; the aux model's own estimate 3.3 W at 200 V (its winding resistances, A-46) | measured loss at 94.2 W decides |
| O-09 | 03 | leakage limit in µH; L<sub>half</sub> pass band; sample size | criterion "commutation ≤ 5 % of the half period"; L<sub>half</sub> design minimum 27.6 µH, derived 25.0 µH from the 0.15 A limit | quote sample sizes (O-17) |
| O-10 | 04 | L<sub>cm</sub> pass band at 10 kHz; µ(150 Hz) and the flat grade | ring L<sub>cm</sub> 53.3 µH nominal (derived −25 % corner 40 µH if the case-B µ tolerance holds); µ(150 Hz) and the grade are assumptions (report §7) | supplier states a guaranteed band and the grade |
| O-11 | 04, 05, 06 | PCS DC-port ring at up to 250 A; L1 creepage and clearance; L1 winding NTC pocket and its insulation level; winding–core capacitance; L<sub>N</sub> HF current if common mode returns | sheet rows cover 160 / 200 A; L1 sheet gives 5 mm margins only; boards draw NTC5–8 "in the L1 windings"; none for capacitance | supplier proposes; the magnetics designer rules before the order |
| O-12 | 05, 06, 07 | acoustic limit; allowable stray capacitance | both are on the roadmap's list of items to freeze; no number in any sheet | supplier states what the construction gives |
| O-13 | 07 | µ(T) of the flat grade; differential-leakage fraction | 0.3 % of L<sub>cm</sub> ASSUMED | measure on first articles |
| O-14 | 08, 09 | R<sub>ac</sub> targets at 100 / 300 / 500 kHz; loss limit in the 800 V / 60 kW test | calculated R<sub>dc</sub> and 92.2 W (pair) only | report; the owner sets the pass level |
| O-15 | 09 | pass level of L(I) at 700 A hot; loss limit | 0.99 B<sub>sat</sub> typical (MG-15); 80.3 W in dab_spec against 90.1 W worst corner in the sheet | report the curve at 100 °C; decide with the DAB cost round |
| O-16 | 10 | L(I) test to the current limit | MG-12's recommendation "vendor to measure L(I) to 1.86 A at 120 °C" is not in the sheet's test list | add it as a type test |
| O-17 | all | number of first articles; sample plan; life and thermal-cycling qualification; tooling other than the T_BIAS4 bobbin; supplier lead times | none stated (only the 4,000 USD bobbin and the internal programme targets) | supplier proposes; purchasing records |
| O-18 | 01, 02, 08, 10 | impulse type-test level at altitude | sheets: 8,000 V for the declared 2,000 m; [insulation report §3.2][ins]: for clearances dimensioned for altitude the sea-level test is 9.5 kV (2,000 m), 10.4 kV (3,000 m), 11.4 kV (4,000 m) on the 8 kV row (tables "from memory — verify") | price 8.0 kV and 9.5 kV; 10.4 kV is the 3,000 m option |
| C-1 | 04 | port ring grade | resolved at the root 2026-10-07: the catalog entry (gen/pv_power.py CM_RING) now specifies the flat-µ grade (≈ 30,000, Shincore SC-1K107-LP class or Yunlu / AT&M flat grade) as an RFQ item keyed by the size code N-C-644025 | **sheet and BOM agree** |
| C-2 | 08, 09 | DAB leakage / series-L split and loss, coolant | BOM text and drawn DAB60 rev B0: 4.38 + 2.12 µH, ≤ 159 W, 50 °C; sheets: 0.94 + 5.56 µH, 188.6 W, 65 °C; total 6.50 µH both | **sheets govern**; total ±5 % is the contract |
| C-3 | 04 | DAB CM choke 160 A price | BOM 65 USD (earlier estimate); M1 reference 100 USD; M2 ring 10.9 USD | open until the DAB60 port is reviewed |
| C-4 | 03 | 5,000-unit price and package | sheet 1.22 USD + 4,000 USD tooling; BOM 1.46 USD (×0.67), "EP10-class" | quote both options; core is EP17 |
| C-5 | 09 | saturation requirement | 700 A (sheet, DAB guide, risk D9); 635 A (report cost section, MG-15 text) | 700 A |
| C-6 | 05 | 5,000-unit L1 price | BOM 107.6 USD (×0.85); sheet 103.1 USD (materials ×0.85, labour ×0.7) | both shown; neither is a quote |

## 💰 Summary table

| Part | Used on | Qty per module | Must-hold requirements | Type tests | Our estimate (USD) | Source |
|---|---|---|---|---|---|---|
| **RFQ-01** PV inductor L_CELL 224 µH, rev M2 | PV-P75, PV-P100/110 | 3 · 4 | L(45 A) ≥ 207.8 µH; L(72.4 A) ≥ 0.35 L0; loss ≤ 83.8 W at 1,000 V / D 0.5 / 45 A; hot spot ≤ 155 °C at 55 °C air, 150 m³/h; winding–core PD ≤ 10 pC at 1,910 V pk; NTC pocket reinforced 4.4 kV rms | AC 2.2 / 4.4 kV 1 min; impulse 6 / 8 kV; L(I) to 72.4 A; thermal run 83.8 W in 150 m³/h | 40.5 / 34.4 | [sheet][s-pv] §2, §5–§9; [BOM item 6][bom-pv75] |
| **RFQ-02** AUX-T1 75 W, rev M1 | PV-P75, PV-P100/110, PCS-P125, PCS-P125-4W | 1 · 1 · 1 · 1 | 50 : 9 : 10; L<sub>p</sub> 485 µH ±7 %; flux ≤ 348.5 mT at 4.07 A; leakage ≤ 12.0 µH (converter limit 18.3), C ≤ 45 pF; loss ≤ 2.5 W at 94.2 W; reinforced PD ≤ 10 pC at 2,472 V pk, clearance ≥ 10.4 mm | AC 4.4 kV 60 s; impulse 8 kV (10.4 kV option at 3,000 m); thermal run at 94.2 W; L<sub>p</sub>(I) reported | 8.8 / 5.9 | [sheet][s-aux75] §2, §5–§9; [aux75_spec][aux75]; BOM items 12 / 12 / 16 / 18 |
| **RFQ-03** T_BIAS4, rev M1 | PV-P75, PV-P100/110, PCS-P125, PCS-P125-4W | 3 · 4 · 3 · 4 | 4+4 : 24 × 4, ratio 6.0; L<sub>half</sub> ≥ 27.6 µH (I<sub>mag</sub> ≤ 0.15 A); C ≤ 5 pF winding to winding; PD-free ≥ 1.75 kV pk; surge 3.79 kV; R ≤ 0.1 / 2.0 Ω | impulse 3,790 V; thermal at 85 °C ambient; PD and C on samples | 2.18 / 1.22 + 4,000 tooling (BOM 1.46) | [sheet][s-bias] §2, §5, §9; BOM items 5 / 5 / 7 / 8 |
| **RFQ-04** Port CM ring, rev M2 (BOM N-C-644025) | PV-P75, PV-P100/110, PCS-P125, PCS-P125-4W; DAB-D60 (earlier CMC160) | 2 · 2 · 1 · 1 · (2) | flat-µ ≈ 30,000 ring 63/38/25 (**not** the catalogue high-µ grade); B ≤ 0.92 T at 1.7 A rms 150 Hz + rated DC (160 / 200 A); basic 1,000 V DC: 2.2 kV rms, 6 kV | AC 2.2 kV 60 s; impulse 6 kV; L<sub>cm</sub> at 16 / 100 kHz with CM bias and rated DC; thermal run | 10.9 / 9.27 (DAB: 65 / 55.25) | [sheet][s-port] §2–§9; BOM items 252 / 250 / 269 / 269 |
| **RFQ-05** L1 120 µH (and L<sub>N</sub>), rev M1 | PCS-P125, PCS-P125-4W | 3 · 4 | 120 µH ±10 %; L<sub>inc</sub> ≥ envelope 108 → 96 µH to 566 A; loss ≤ 169 W at 180 A / 750 V and ≤ 285 W at 198 A / 950 V; hot spot ≤ 140 °C at 60 °C inlet; basic 1 kV DC: 2.2 kV rms, 6 kV, PD ≤ 10 pC at 1.5 kV pk | L(I) to 600 A pulsed at 25 °C and ≥ 100 °C; impulse 6 kV; thermal run 198 A + 32 kHz ripple | 126.6 / 107.6 (sheet 103.1) | [sheet][s-l1] §2a, §5–§9; BOM items 8 / 9 |
| **RFQ-06** L2 6 µH, rev M1 | PCS-P125, PCS-P125-4W | 3 · 3 | 6 µH ±10 %; L<sub>inc</sub> ≥ 5.4 / 4.8 / 3.6 µH at 0 / 305 / 367 A; loss ≤ 13.7 W at 198 A; hot spot ≤ 140 °C; mains basic: 1.5 kV rms, 6 kV | L(I) to 367 A; impulse 6 kV; thermal run 198 A | 38.6 / 32.8 (sheet 29.7) | [sheet][s-l2] §2, §5–§9; BOM items 9 / 10 |
| **RFQ-07** AC CM choke, rev M1 | PCS-P125 (3 bars), PCS-P125-4W (4 bars) | 1 · 1 | L<sub>cm</sub> ≥ 126.1 µH at 32 kHz at the −25 % µ corner; B ≤ 0.96 T (150 Hz CM 1.25 A rms + DM + HF); flat-µ ≈ 30,000; 1.5 kV rms, 6 kV | impulse 6 kV; L<sub>cm</sub> with 1.25 A rms 150 Hz bias | 24.8 / 21.1 (4W: 25.8 / 21.9) | [sheet][s-pcm] §2, §5, §9; BOM items 4 / 5 |
| **RFQ-08** DAB transformer 11 : 12, matched pair, rev M1 | DAB-D60 | 1 pair | 11 : 12; L<sub>m</sub> 150 µH ±10 %; leakage 0.94 µH ±20 % (units ±5 %); total series L 6.50 µH ±5 %; I1 137.3 A rms; loss ≤ 182 W (calculated 188.6 W); hot spot ≤ 130 °C at 65 °C coolant; C ≤ 300 pF; P–S 3.32 kV rms, PD ≤ 10 pC at 3.15 kV pk, 6 kV | impulse 6 kV; PD at 1.2 ×; thermal run with measured R<sub>th</sub>; loss at 800 V / 60 kW; R<sub>ac</sub> 100–500 kHz | 186 / 158.1 | [sheet][s-dabx] §2, §5–§9; [DAB report §9][dabrep]; BOM item 8 |
| **RFQ-09** DAB series inductor, rev M1 | DAB-D60 | 1 | 5.56 µH (trim 5.05–6.07); no saturation to 700 A at 100 °C; 137.3 A rms; ≤ 90 W; hot spot ≤ 130 °C; winding–core 2.2 kV rms, PD ≤ 10 pC at 1.8 kV pk, 6 kV | impulse 6 kV; L(I) to 700 A at 100 °C; thermal run | 86 / 73.1 | [sheet][s-dabl] §2, §5–§9; BOM item 1 |
| **RFQ-10** AUX-HV T1 30 W, rev M1 | DAB-D60 (earlier platform) | 1 | 90 : 15 : 16; L<sub>p</sub> 1.148 mH ±7 %; leakage ≤ 34.0 µH; C ≤ 38.7 pF; reinforced PD ≤ 10 pC at 2,461 V pk; clearance ≥ 10.4 mm; creepage 20 mm (10 mm at CTI ≥ 600) | AC 4.4 kV 60 s; impulse 8 kV 1.2/50; thermal run 30 W | 5.9 / 3.95 | [sheet][s-auxhv] §2, §5–§9; BOM item 13 |

<sub>Estimate = USD per piece, catalogue basis / 5,000 units, from the costed BOM lines; every figure is an ESTIMATE without
a quote, confidence low. The sheets' own 5,000-unit figures are in the sections.</sub>

## Appendix A · The 48 kHz weight-sensitive variant (information, not a substitution)

Review item E12 asks that the transport-sensitive higher-frequency candidate be noted. In the trade study of [D-060][dec]
the two-level stage at 48 kHz is the lightest two-level candidate that meets every acceptance limit (the T-types are
22 kg but 148 USD dearer). Calculated, at 5,000 units ([tradeoff.md][trade]; unrounded values from
[tradeoff.json][tradej]):

| | Chosen: 32 kHz, L1 120 µH (A1) | 48 kHz candidate (A2) | Difference |
|---|---|---|---|
| SiC devices | 36 (6 per switch) | 42 (7 per switch) | +6 |
| L1 / C<sub>f</sub> / L2 | 120 µH / 50 µF / 6 µH | 64.8 µH / 30 µF / 6 µH | |
| L1 construction | amorphous C-core, leg 30 × 100 mm, window 160 × 40 mm, 20 turns Al foil 0.8 mm | **nanocrystalline** C-core, leg 30 × 80 mm, window 120 × 30 mm, 16 turns Al foil 0.8 mm | |
| L1 mass each | 11.56 kg | 6.34 kg | −5.2 kg |
| L1 loss at 180 A / 750 V · 198 A / 950 V | 169.7 · 284.8 W | 109.1 · 169.2 W | |
| L1 hot spot at 60 °C inlet | 138.5 °C | 134.8 °C | |
| L1 price each, 5,000 units (study) | 103.1 USD | 103.0 USD | |
| AC CM choke L<sub>cm</sub> | 126 µH | 104 µH | |
| Filter, 5,000 units | 443 USD | 437 USD | −6 USD |
| **Filter mass** | 39.0 kg | 23.3 kg | **−15.7 kg** |
| **Module, 5,000 units (catalogue)** | 1,131 (1,409) USD | 1,156 (1,438) USD | **+25.7 (+29.1) USD** |
| Peak · full-load efficiency (125 kW, 750 V) | 98.99 · 98.30 % | 98.92 · 98.36 % | |
| Worst junction at 110 %, 45 °C | 123 °C | 121 °C | |

**Freight break-even.** The candidate costs about 25 USD more at 5,000 units and carries about 16 kg less filter mass: **1.56
USD per kg** from the rounded table figures of the study (1,156 − 1,131 = 25 USD; 39 − 23 = 16 kg), **1.64 USD/kg** from the
unrounded values (25.7 USD, 15.7 kg), 1.86 USD/kg at catalogue prices. If shipping the filter inductors costs more than about 1.6 USD per
kg, the lighter candidate is cheaper delivered; below that it is not. Only purchasing's freight quotation can say which.

**Why it is not a substitution.**
- No winding sheet exists for its L1. The sheets of this index describe the 32 kHz point only; the 48 kHz L1 is a selection of the
  trade study (nanocrystalline C-core, 16 turns), with no L(I) trajectory to the flux-rule current, no fault-chain
  coordination ([D-078][dec], R2-03) and no acceptance envelope.
- The drawn power boards carry six devices per switch; the candidate needs seven. The study prices devices, pads, films,
  decoupling, gate channels, bias, L1, L2, C<sub>f</sub>, damping and the choke; board and heatsink changes are not in it.
- The sweep's resolution is about ±20 USD and the study itself says the exact frequency and inductance are "not a firm
  result" — +25.7 USD is about one step of that resolution. Powder cores were not searched at either point.
- Everything is calculated; the amorphous loss fit is quoted from memory (Metglas data) and the design takes the higher of
  it and OpenMagnetics' ([report][rep], "Where the two loss models disagree").

**What the RFQ does with it.** Nothing binding. At most the L1 suppliers of RFQ-05 are asked for an indicative price and
packed mass for the 64.8 µH nanocrystalline L1 (leg 30 × 80 mm, window 120 × 30 mm, 16 turns, 6.3 kg), clearly marked as
information. The decision belongs after the gates close, as E12 states; the three-wire and four-wire builds stay separate
whatever it is.

---

<!-- footer -->
← [Magnetics](../guide/06-magnetics.md) · [Documentation index](../README.md) · [Sourcing and cost](../guide/07-sourcing-and-cost.md) →

<!-- Link targets. -->
[r4]: ../../gen/data/review_r4.csv
[rev]: ../../gen/data/review_magnetics.csv
[dec]: DECISIONS.md
[req]: REQUIREMENTS.md
[inst]: INSTALLATION.md
[road]: 00-roadmap-source.md
[cost]: ../../bom/COST.md
[bom-pv75]: ../../bom/PV-P75_costed_BOM.csv
[bom-pv100]: ../../bom/PV-P100-110_costed_BOM.csv
[bom-pcs]: ../../bom/PCS-P125_costed_BOM.csv
[bom-pcs4]: ../../bom/PCS-P125-4W_costed_BOM.csv
[bom-dab]: ../../bom/DAB-D60-FULL_costed_BOM.csv
[rep]: ../../sim/out/magnetics/report.md
[ins]: ../../sim/out/insulation/report.md
[insj]: ../../sim/out/insulation/insulation_spec.json
[pcs]: ../../sim/out/pcs_design/pcs_spec.json
[trade]: ../../sim/out/pcs_design/tradeoff.md
[tradej]: ../../sim/out/pcs_design/tradeoff.json
[cell]: ../../sim/out/pv_design/cell_spec.json
[aux75]: ../../sim/out/aux_hv_design/aux75_spec.json
[dab]: ../../sim/out/dab_design/dab_spec.json
[dabrep]: ../../sim/out/dab_design/report.md
[pvchk]: ../../hardware/PV-CTL/outputs/PV-CTL_design_check.txt
[pcschk]: ../../hardware/PCS-PWR/outputs/PCS-PWR_design_check.txt
[s-pv]: ../../sim/out/magnetics/spec_pv_inductor.md
[j-pv]: ../../sim/out/magnetics/design_pv_inductor.json
[s-aux75]: ../../sim/out/magnetics/spec_aux75_transformer.md
[j-aux75]: ../../sim/out/magnetics/design_aux75_transformer.json
[s-bias]: ../../sim/out/magnetics/spec_gdrv_bias_transformer.md
[j-bias]: ../../sim/out/magnetics/design_gdrv_bias_transformer.json
[s-port]: ../../sim/out/magnetics/spec_port_cm_choke.md
[j-port]: ../../sim/out/magnetics/design_port_cm_choke.json
[s-l1]: ../../sim/out/magnetics/spec_pcs_l1.md
[j-l1]: ../../sim/out/magnetics/design_pcs_l1.json
[s-l2]: ../../sim/out/magnetics/spec_pcs_l2.md
[j-l2]: ../../sim/out/magnetics/design_pcs_l2.json
[s-pcm]: ../../sim/out/magnetics/spec_pcs_cm_choke.md
[j-pcm]: ../../sim/out/magnetics/design_pcs_cm_choke.json
[s-dabx]: ../../sim/out/magnetics/spec_dab_transformer.md
[j-dabx]: ../../sim/out/magnetics/design_dab_transformer.json
[s-dabl]: ../../sim/out/magnetics/spec_dab_series_inductor.md
[j-dabl]: ../../sim/out/magnetics/design_dab_series_inductor.json
[s-auxhv]: ../../sim/out/magnetics/spec_aux_hv_transformer.md
[j-auxhv]: ../../sim/out/magnetics/design_aux_hv_transformer.json
[g04]: ../guide/04-protection-and-safety.md
[g09]: ../guide/09-pcs-p125.md
[g10]: ../guide/10-dab-d60.md
