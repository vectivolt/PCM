# Reference library (docs/datasheets, docs/reference-designs)

Everything the schematics, BOMs and simulations cite as a source: manufacturers' reference designs, component datasheets and the requirements. The documents themselves are other companies' files and are **not kept in git**: `python docs/fetch.py` downloads them again from the manifest and verifies each SHA-256. Nothing here is bench data; reference designs remove unknowns, they do not set our ratings (REQUIREMENTS.md section 6, rule 5).

## Layout (fixed)

```
docs/
  LIBRARY.md                 this file
  fetch.py                   downloads the library again from the manifest, verifies it, regenerates this file's tables
  guide/, assets/            the written documentation and its figures (see docs/README.md)
  SOURCES.csv                MASTER manifest: one row per library file (reference designs + datasheets)
  requirements/              REQUIREMENTS.md (scope lock), DECISIONS.md, 00-roadmap-source.md (read-only)
  reference-designs/
    SOURCES.csv              the reference-design rows of the master (paths relative to this folder)
    <vendor>/<design>/       README.md + the vendor documents; <design>_design-files.zip and design-files/ where the
                             schematic / BOM only exist in an archive (design-files/ = unpacked readable documents only)
  datasheets/
    SOURCES.csv              the datasheet rows of the master (paths relative to the repository root)
    <category>/<name>.pdf    categories: connectors, controllers, gate-drivers, isolation-interface, magnetics,
                             passives-capacitors, power-semiconductors, power-supply, protection, sensing, thermal, timing
```

`gen/` and `sim/` (and the BOMs and KiCad outputs generated from them) cite datasheets by path (`DS + "sensing/AMC3302.pdf"`, `docs/datasheets/...` strings). **Never move, rename or delete a file under `docs/` without first grepping `gen/`, `sim/`, `bom/` and `hardware/` for its path.** The only files removed so far were unreferenced byte-identical duplicates (see the end of this file).

## What is in it

<!-- generated:counts -->
Counts from `docs/SOURCES.csv` (regenerate with `python docs/fetch.py --summary`).

|  | rows | on disk | to fetch by hand | MB on disk |
|---|---:|---:|---:|---:|
| reference designs | 107 | 92 | 15 | 348 |
| datasheets | 438 | 422 | 16 | 766 |
| **total** | **545** | **514** | **31** | **1113** |

### Reference designs

| design | what it is | on disk | by hand |
|---|---|---:|---:|
| `infineon/ref-10kw3lnpc2` | 10 kW three-phase three-level NPC2 inverter board (Infineon) | 1 | 0 |
| `megarevo/pma` | competitor documents | 7 | 0 |
| `megarevo/pmd-75-g3` | 75 kW non-isolated bidirectional DC/DC (MPPT) module | 1 | 0 |
| `megarevo/string-pcs-1500v` | Megarevo MPHV 1500 V string PCS (145–250 kW) | 1 | 0 |
| `onsemi/tnd6401-6408-25kw-dcfc` | onsemi 25 kW SiC DC fast-charger design series, TND6401/D ... TND6408/D | 0 | 8 |
| `st/stdes-dabbidir` | 25 kW bidirectional dual-active-bridge DC/DC (ST) | 1 | 5 |
| `st/stdes-pfcbidir` | 15 kW three-phase three-level bidirectional AFE (ST) | 0 | 1 |
| `ti/pmp22817` | automotive SPI-programmable gate driver and bias supply, UCC5870-Q1 + UCC14240-Q1 (TI) | 3 | 0 |
| `ti/pmp23223` | smart isolated gate driver with bias supply, UCC21732-Q1 + UCC14240-Q1 (TI) | 3 | 0 |
| `ti/pmp41009` | 350-1000 V input, 14 V / 56 W quasi-resonant flyback (TI) | 3 | 0 |
| `ti/pmp41031` | 350-1500 V input, 150 W isolated auxiliary supply, two-switch flyback (TI) | 3 | 0 |
| `ti/pmp41037` | 1 kW 800 V to 12 V serial half-bridge bidirectional DCX, GaN (TI) | 3 | 0 |
| `ti/tida-010054` | 10 kW bidirectional dual-active-bridge DC/DC (TI) | 3 | 0 |
| `ti/tida-010210` | 11 kW three-phase ANPC inverter/PFC, GaN (TI) | 3 | 0 |
| `ti/tida-010232` | AFE for insulation monitoring in high-voltage EV charging and solar (TI) | 3 | 0 |
| `ti/tida-010247` | high-accuracy battery management unit for 48-1500 V storage (TI) | 3 | 0 |
| `ti/tida-010253` | battery control unit (BCU) for HV battery racks (TI) | 3 | 0 |
| `ti/tida-010938` | 10 kW GaN single-phase string inverter with battery storage port (TI) | 4 | 0 |
| `ti/tida-010949` | 600 W GaN four-switch buck-boost solar power optimizer (TI) | 3 | 0 |
| `ti/tida-010955` | analog front end for machine-learning DC arc detection in solar (TI) | 3 | 0 |
| `ti/tida-010957` | 15-30 kW three-level flying-capacitor 3-phase+N converter, GaN (TI) | 4 | 0 |
| `ti/tida-010985` | resistive-bridge insulation monitor for 800 V DC with large Y capacitance (TI) | 3 | 0 |
| `ti/tida-011011` | isolated gate driver board with isolated bias supply for 3.3 kV SiC (TI) | 3 | 0 |
| `ti/tida-01606` | 11 kW bidirectional three-phase three-level (T-type) inverter/PFC (TI) | 3 | 0 |
| `ti/tida-050063` | high-voltage solid-state-relay active precharge (TI) | 4 | 0 |
| `ti/tida-050082` | high-voltage positive-rail active precharge (TI) | 3 | 0 |
| `ti/tidm-buckboost-bidir` | C2000 non-isolated bidirectional buck-boost (TI) | 4 | 0 |
| `ti/tidm-solar-dcdc` | MPPT DC/DC: two-phase interleaved boost + isolated LLC (TI) | 4 | 0 |
| `ti/tmdscncd28388d` | F28388D controlCARD (TI) | 1 | 1 |
| `wolfspeed/crd-020dd17p-j` | 20 W, 60-1000 V input auxiliary flyback (Wolfspeed) | 2 | 0 |
| `wolfspeed/crd-25bda6512n-k` | 25 kW bidirectional three-phase T-type inverter / PFC (Wolfspeed) | 2 | 0 |
| `wolfspeed/crd-60dd12n` | 60 kW four-phase interleaved boost converter (Wolfspeed) | 2 | 0 |
| `wolfspeed/crd200da23n-gma` | 200 kW three-phase two-level inverter for a 1500 V DC bus (Wolfspeed) | 2 | 0 |
| `wolfspeed/crd250da12e-xm3` | three-phase SiC inverter stack, liquid cooled (Wolfspeed) | 2 | 0 |
| `wolfspeed/crd60dd12n-gmb` | 60 kW isolated bidirectional DC/DC, dual active bridge (Wolfspeed) | 2 | 0 |

### Datasheets per category

| category | rows | on disk | by hand | MB |
|---|---:|---:|---:|---:|
| connectors | 24 | 24 | 0 | 12 |
| controllers | 10 | 9 | 1 | 70 |
| gate-drivers | 16 | 14 | 2 | 37 |
| isolation-interface | 39 | 39 | 0 | 70 |
| magnetics | 34 | 34 | 0 | 61 |
| passives-capacitors | 27 | 27 | 0 | 33 |
| power-semiconductors | 124 | 121 | 3 | 173 |
| power-supply | 28 | 28 | 0 | 79 |
| protection | 83 | 73 | 10 | 102 |
| sensing | 41 | 41 | 0 | 86 |
| thermal | 10 | 10 | 0 | 40 |
| timing | 2 | 2 | 0 | 3 |
<!-- /generated:counts -->

## Manifests

`docs/SOURCES.csv` is the master. Columns: `path,kind,vendor,id,title,url,retrieved,bytes,sha256,status,note`.

- `path` relative to the repository root; `kind` = `reference-design` or `datasheet`; `vendor` = vendor folder (reference designs) or manufacturer (datasheets); `id` = design id or MPN.
- `status` = `OK` (the file is on disk; `bytes` and `sha256` are those of the file) or `MANUAL` (not downloaded: `url` is the direct link when one is known, otherwise the product page; `bytes` 0, empty `sha256`; `note` says why).
- Every file under `reference-designs/` and `datasheets/` has exactly one row, except `README.md`, `SOURCES.csv`, `spec.md` and the unpacked `design-files/` contents (covered by the row of their archive).
- The two sub-manifests carry the same rows (reference-designs: `path,vendor,design,title,url,retrieved,bytes,sha256,status,note`; datasheets: `path,category,manufacturer,mpn,title,url,retrieved,bytes,sha256,status,note`). Edit all three together.

Check (run from the repository root; no network): `python docs/fetch.py --check` verifies every `OK` file's SHA-256, flags a `MANUAL` file that is on disk, a file without a row and a path listed twice.

## How to add a document

1. **Place it.** Reference design: `docs/reference-designs/<vendor>/<design>/` (lower-case vendor and design id; one folder per design, with a short `README.md`: what it is, native ratings confirmed from the document, which of our boards it informs, its limitation for us). Datasheet: `docs/datasheets/<category>/<MPN-or-family>.pdf` in one of the categories above. Name files `<design>_<kind>_<doc-id>.<ext>` for reference designs (kind = design-guide, schematic, bom, test-report, user-guide, ...).
2. **Download from the manufacturer's own domain only** (no mirrors, distributors or aggregators): `curl -L --fail -o <file> <url>` - a plain request, no browser or referrer headers. Check with `file` that it really is a PDF (or ZIP), skip anything over 100 MB, never execute anything. Compare `shasum -a 256` with the library first: if the bytes already exist under another name, do not add a second copy; put a sentence in the existing row's `note` instead.
3. **Archives.** Keep the ZIP in the design folder as `<design-id>_design-files.zip` and unpack only what a person can read (PDF schematics, BOM xlsx/csv/pdf, drawings) into `design-files/`. CAD, Gerber and STEP files stay inside the ZIP.
4. **Add the row** to `docs/SOURCES.csv` and to the sub-manifest of its tree: `bytes` = `stat -f%z <file>`, `sha256` = `shasum -a 256 <file>`, `retrieved` = date, `url` = the exact link used, `note` = anything a reader needs (revision, family datasheet, what it was used for).
5. **If the site refuses** (403, connection reset, login, licence click-through, CAPTCHA) do not work around it: add the row with `status` = `MANUAL`, the direct link in `url`, the reason in `note`, and regenerate the list below (`python docs/fetch.py --summary`).
6. Run `python docs/fetch.py --check`, then `python docs/fetch.py --summary`. Do not edit `gen/`, `sim/`, `hardware/`, `bom/` or `requirements/` to make a document fit.

## Still to download by hand

<!-- generated:by-hand -->
31 documents could not be fetched with a plain request; nothing was circumvented. Open the link in a browser, save the file at the path shown (relative to `docs/`), set its row to `OK` with `bytes` and `sha256`, and run `python docs/fetch.py --summary`.

### Eaton (1)

| item | save as | link | why |
|---|---|---|---|
| Eaton Bussmann series NH photovoltaic (gPV) fuse links, data sheet 720133 | `datasheets/protection/PV-NH.pdf` | https://www.eaton.com/content/dam/eaton/products/electrical-circuit-protection/fuses/data-sheets/bus-iec-ds-720133-nhpvfuselinks.pdf | eaton.com resets curl's HTTP/2 stream (exit 92) on 2 URL paths and an HTTP/1.1 retry timed out; not worked around. URL found by search: NH1-NH3, 1000 VDC, 32-400 A, gPV. Optional: the Mersen HP10NH sheet already covers the 1000 VDC gPV fuse item. Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": curl exit 92, HTTP/2 stream INTERNAL_ERROR (eaton.com). Not circumvented; open the link in a browser. |

### Littelfuse (4)

| item | save as | link | why |
|---|---|---|---|
| Littelfuse 5.0SMDJ series 5000 W TVS diodes (DO-214AB) | `datasheets/protection/5.0SMDJ.pdf` | https://www.littelfuse.com/assetdocs/tvs-diode-5?assetguid=d3fdc005-98d5-4584-977b-a6ee20c5178d | littelfuse.com returns HTTP 403 to curl; not bypassed. URL found by search (likely the 5.0SMDJ datasheet; if it is the wrong one use the TP5.0SMDJ sheet https://www.littelfuse.com/assetdocs/littelfuse-tvs-diode-tp5-0smdj-datasheet?assetguid=51815352-6C6B-4695-8109-88A4FFC2FE2B). A human/browser must download it. Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (littelfuse.com). Not circumvented; open the link in a browser. |
| Littelfuse SMBJ series 600 W TVS diodes (DO-214AA) | `datasheets/protection/SMBJ.pdf` | https://www.littelfuse.com/assetdocs/tvs-diodes-smbj-series-datasheet?assetguid=ba555e99-a12d-4f72-a0b6-86b06c67171e | littelfuse.com returns HTTP 403 to curl on every URL form tried (/media?filename=, m.littelfuse.com/~/media/, /assetdocs/); not bypassed. URL found by search. A human/browser must download it. Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (littelfuse.com). Not circumvented; open the link in a browser. |
| Littelfuse SMCJ series 1500 W TVS diodes (DO-214AB) | `datasheets/protection/SMCJ.pdf` | https://www.littelfuse.com/assetdocs/tvs-diodes-smcj-datasheet?assetguid=37388813-0d6d-4329-969b-1aa8b7614ac1 | littelfuse.com returns HTTP 403 to curl; not bypassed. URL found by search. A human/browser must download it. Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (littelfuse.com). Not circumvented; open the link in a browser. |
| Littelfuse SPD2 PV series datasheet (1100 / 1500 VDC, 3+0) | `datasheets/protection/SPD2-PV.pdf` | https://www.littelfuse.com/assetdocs/spd2-pv-series-datasheet?assetguid=da42ceb5-ac40-4ee0-95c3-d14c85ecd0e9 | Downloaded by the port-B SPD search agent (PA-06: battery-rated DC SPD for the 1000 V port). littelfuse.com returns 'Access Denied' to curl; in a browser the URL and the product page .../surge-protection-devices/spd2-pv now show 404. Not bypassed. Not read: only search snippets (PV ISCPV / SCCR ratings, no ESS / IEC 61643-41 type found). Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (littelfuse.com). Not circumvented; open the link in a browser. |

### Phoenix Contact (2)

| item | save as | link | why |
|---|---|---|---|
| VAL-MS 1000DC/2+V - Type 2 surge protection device - 2805091 | `datasheets/protection/2805091.pdf` | https://www.phoenixcontact.com/en-us/products/pv-arrester-val-ms-1000dc2v-2805091?type=pdf | Downloaded by the port-B SPD search agent (PA-06: battery-rated DC SPD for the 1000 V port). phoenixcontact.com returns 'Access Denied' (Akamai) to curl on the PDF link and to WebFetch; not bypassed. Values read from Phoenix's own product page in a browser (not from the PDF): PV arrester, UC 1000 V DC, In 15 kA, Imax 30 kA, Up <= 5 kV, 'Short-circuit current rating ISCPV 80 A', rated load current <= 80 A, -40..80 C, standards DIN EN 61643-11 / IEC 61643-1. Fails PA-06 by two orders of magnitude. Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (phoenixcontact.com, Akamai). Not circumvented; open the link in a browser. |
| VAL-MB-T2 1000DC-PV/2+V - Type 2 surge arrester - 2905645 | `datasheets/protection/2905645.pdf` | https://www.phoenixcontact.com/en-us/products/pv-arrester-val-mb-t2-1000dc-pv2v-2905645?type=pdf | Downloaded by the port-B SPD search agent (PA-06: battery-rated DC SPD for the 1000 V port). Same Akamai 'Access Denied' for curl; not bypassed. Values read from Phoenix's own product page in a browser: PV T2, EN 50539-11, Y configuration, UCPV 1000 V DC, In 20 kA, Imax 40 kA, Up <= 3.3 kV, 'Short-circuit current rating ISCPV 2000 A', rated load current 50 A, OCM, -40..80 C. PV only. No Phoenix 1000 V DC type with an ISCCR / battery rating was found. Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (phoenixcontact.com, Akamai). Not circumvented; open the link in a browser. |

### STMicroelectronics (2)

| item | save as | link | why |
|---|---|---|---|
| M24C64-R / M24C64-W / M24C64-F 64-Kbit serial I2C bus EEPROM | `datasheets/controllers/M24C64.pdf` | https://www.st.com/resource/en/datasheet/m24c64-f.pdf | Correct direct URL (found by search; -w and -r variants share the family). st.com resets curl's HTTP/2 stream (curl exit 92) on 4 attempts and an HTTP/1.1 retry timed out (curl exit 28); not worked around. A human/browser must download it. Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": curl exit 92, HTTP/2 stream INTERNAL_ERROR (st.com closes the connection for non-browser clients). Not circumvented; open the link in a browser. |
| STGAP2SICS isolated 4 A single gate driver for SiC MOSFETs | `datasheets/gate-drivers/STGAP2SICS.pdf` | https://www.st.com/resource/en/datasheet/stgap2sics.pdf | Correct direct URL (found by search). st.com resets curl's HTTP/2 stream (curl exit 92) on 4 attempts and an HTTP/1.1 retry timed out (curl exit 28); not worked around. A human/browser must download it. Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": curl exit 92, HTTP/2 stream INTERNAL_ERROR (st.com closes the connection for non-browser clients). Not circumvented; open the link in a browser. |

### Sensata (1)

| item | save as | link | why |
|---|---|---|---|
| Sensata GIGAVAC HX360 series contactor | `datasheets/protection/HX360.pdf` | https://www.sensata.com/sites/default/files/a/sensata-gigavac-hx360-contactor-datasheet.pdf | sensata.com returns HTTP 403 to curl; not bypassed. URL found by search. HX series is the >=1000 VDC family; GX series is only 800 VDC (SGX250: 250 A, 750 VDC). Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (sensata.com). Not circumvented; open the link in a browser. |

### TE Connectivity (2)

| item | save as | link | why |
|---|---|---|---|
| TE KILOVAC EV200 series high-voltage DC contactor (catalog section) | `datasheets/protection/EV200.pdf` | https://www.te.com/commerce/DocumentDelivery/DDEController?Action=srchrtrv&DocNm=5-1773450-5_sec7_EV200A&DocType=CS&DocLang=English&DocFilename=ENG_CS_5-1773450-5_sec7_EV200A_0313_._5-1773450-5_Sec7_EV200A.pdf | te.com returns HTTP 403 to curl; not bypassed. URL found by search. EV200 is listed 12-900 VDC, 500+ A (not 1000 VDC). Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (te.com). Not circumvented; open the link in a browser. |
| TE EVC 250 main contactor | `datasheets/protection/EVC250.pdf` | https://www.te.com/content/dam/te-com/documents/automotive/global/evc-250-datasheet.pdf | te.com returns HTTP 403 to curl; not bypassed. URL found by search. CAUTION: TE lists EVC 250 at 450 VDC (500 VDC max switching) - it does NOT cover a 1000 V bus; TDK HVC43 is the downloaded 1000 VDC contactor. Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (te.com). Not circumvented; open the link in a browser. |

### onsemi (12)

| item | save as | link | why |
|---|---|---|---|
| onsemi TND6401/D part 1: structure of a fast EV charger and key electrical specifications | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6401_part1-structure-and-key-specs.pdf` | https://www.onsemi.com/download/reference-designs/pdf/tnd6401-d.pdf | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. Link found by search. Product page: https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK |
| onsemi TND6402/D part 2: solution overview | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6402_part2-solution-overview.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. Direct link NOT verified: pattern https://www.onsemi.com/pub/collateral/tnd6402-d.pdf ; a search-indexed reprint of the content is https://www.onsemi.com/site/pdf/H2PToday2105_design_ONSemi.pdf (also 403). Find the document from the product page. |
| onsemi TND6403/D part 3: PFC simulation and development | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6403_part3-pfc-simulation-and-development.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. Direct link NOT verified: pattern https://www.onsemi.com/pub/collateral/tnd6403-d.pdf . Find the document from the product page. |
| onsemi TND6404/D part 4: DC-DC (DAB) simulation and design considerations | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6404_part4-dc-dc-dab-simulation-and-design.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. Direct link NOT verified: pattern https://www.onsemi.com/pub/collateral/tnd6404-d.pdf . Find the document from the product page. |
| onsemi TND6405/D part 5: control algorithms, modulation schemes and feedback | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6405_part5-control-algorithms-modulation-feedback.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. Direct link NOT verified: pattern https://www.onsemi.com/pub/collateral/tnd6405-d.pdf . Find the document from the product page. |
| onsemi TND6406/D part 6: gate drive system for power modules | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6406_part6-gate-drive-system.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. Direct link NOT verified: pattern https://www.onsemi.com/pub/collateral/tnd6406-d.pdf . Find the document from the product page. |
| onsemi TND6407/D part 7: auxiliary power supply units for 800 V EV chargers | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6407_part7-auxiliary-power-800v.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. Direct link NOT verified: pattern https://www.onsemi.com/pub/collateral/tnd6407-d.pdf . Find the document from the product page. |
| onsemi TND6408/D part 8: thermal management | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6408_part8-thermal-management.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. Direct link NOT verified: pattern https://www.onsemi.com/pub/collateral/tnd6408-d.pdf . Find the document from the product page. |
| NCx57090y, NCx57091y Isolated High Current IGBT/MOSFET Gate Driver (SOIC8 WB, 5 kVrms) | `datasheets/gate-drivers/NCD57090.pdf` | https://www.onsemi.com/pdf/datasheet/ncd57090a-d.pdf | Exact part is published (NCD57090A-F), so no substitution. onsemi.com returns HTTP 403 to the helper's curl on 3 URL forms (/pdf/datasheet/ncd57090a-d.pdf, /download/data-sheet/pdf/ncd57090a-d.pdf, product page ?pdf=Y), so recorded MANUAL per brief. A human should save the 25-page PDF (dated 2023-07-26) from a browser as docs/datasheets/gate-drivers/NCD57090.pdf (then dl.py --existing to verify/record). Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. |
| NTH4L014N120M3P EliteSiC MOSFET 1200 V 14 mOhm M3P TO247-4L | `datasheets/power-semiconductors/NTH4L014N120M3P.pdf` | https://www.onsemi.com/download/data-sheet/pdf/nth4l014n120m3p-d.pdf | onsemi server returns HTTP 403 to curl (bot protection); NOT bypassed. A human/browser session must download it from the URL given (direct PDF, MPN confirmed to exist as written). Product page: https://www.onsemi.com/products/discrete-power-modules/silicon-carbide-sic/silicon-carbide-sic-mosfets/nth4l014n120m3p Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. |
| NTH4L022N120M3S EliteSiC MOSFET 1200 V 22 mOhm M3S TO247-4L | `datasheets/power-semiconductors/NTH4L022N120M3S.pdf` | https://www.onsemi.com/pdf/datasheet/nth4l022n120m3s-d.pdf | onsemi server returns HTTP 403 to curl (bot protection); NOT bypassed. A human/browser session must download it from the URL given (direct PDF, MPN confirmed to exist as written). Product page: https://www.onsemi.com/products/discrete-power-modules/silicon-carbide-sic/silicon-carbide-sic-mosfets/nth4l022n120m3s Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. |
| NTH4L028N170M1 SiC MOSFET 1700 V 28 mOhm TO247-4L (M1) | `datasheets/power-semiconductors/NTH4L028N170M1.pdf` | https://www.onsemi.com/download/data-sheet/pdf/nth4l028n170m1-d.pdf | onsemi server returns HTTP 403 to curl (bot protection); NOT bypassed. A human/browser session must download it from the URL given (direct PDF, MPN confirmed to exist as written). Product page: https://www.onsemi.com/products/discrete-power-modules/silicon-carbide-sic/silicon-carbide-sic-mosfets/nth4l028n170m1 Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": HTTP 403 (onsemi Akamai edge). Not circumvented; open the link in a browser. |

### st (6)

| item | save as | link | why |
|---|---|---|---|
| STDES-DABBIDIR bill of materials v2.0 | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_bom_v2-0.pdf` | https://www.st.com/content/ccc/resource/technical/document/bill_of_materials/group2/63/8e/bf/15/c6/c5/4a/74/STDES-DABBIDIR_BOM/files/stdes-dabbidir-bom.pdf/jcr:content/translations/en.stdes-dabbidir-bom.pdf | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": curl exit 92, HTTP/2 stream INTERNAL_ERROR (st.com closes the connection for non-browser clients). Not circumvented; open the link in a browser. Product page: https://www.st.com/en/evaluation-tools/stdes-dabbidir.html |
| STDES-DABBIDIR data brief DB4879 | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_data-brief_db4879.pdf` | https://www.st.com/resource/en/data_brief/stdes-dabbidir.pdf | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": curl exit 92, HTTP/2 stream INTERNAL_ERROR (st.com closes the connection for non-browser clients). Not circumvented; open the link in a browser. Product page: https://www.st.com/en/evaluation-tools/stdes-dabbidir.html |
| STDES-DABBIDIR schematic v2.0 (Rev 2, 13 Dec 2023) | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_schematic_v2-0.pdf` | https://www.st.com/resource/en/schematic_pack/stdes-dabbidir-schematic.pdf | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": curl exit 92, HTTP/2 stream INTERNAL_ERROR (st.com closes the connection for non-browser clients). Not circumvented; open the link in a browser. Product page: https://www.st.com/en/evaluation-tools/stdes-dabbidir.html |
| STDES-DABBIDIR test report TN1435 [extra] | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_test-report_tn1435.pdf` | https://www.st.com/resource/en/technical_note/tn1435-stdesdabbidir--test-report-stmicroelectronics.pdf | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": curl exit 92, HTTP/2 stream INTERNAL_ERROR (st.com closes the connection for non-browser clients). Not circumvented; open the link in a browser. Product page: https://www.st.com/en/evaluation-tools/stdes-dabbidir.html |
| STDES-DABBIDIR user manual UM3198 | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_user-manual_um3198.pdf` | https://www.st.com/resource/en/user_manual/um3198-25-kw-dual-active-bridge-bidirectional-power-converter-for-ev-charging-and-battery-energy-storage-systems-stmicroelectronics.pdf | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": curl exit 92, HTTP/2 stream INTERNAL_ERROR (st.com closes the connection for non-browser clients). Not circumvented; open the link in a browser. Product page: https://www.st.com/en/evaluation-tools/stdes-dabbidir.html |
| STDES-PFCBIDIR data brief | `reference-designs/st/stdes-pfcbidir/stdes-pfcbidir_data-brief.pdf` | https://www.st.com/resource/en/data_brief/stdes-pfcbidir.pdf | Retried once on 2026-10-04 with plain curl -L --fail -A "Mozilla/5.0": curl exit 92, HTTP/2 stream INTERNAL_ERROR (st.com closes the connection for non-browser clients). Not circumvented; open the link in a browser. Product page: https://www.st.com/en/evaluation-tools/stdes-pfcbidir.html |

### ti (1)

| item | save as | link | why |
|---|---|---|---|
| TMDSCNCD28388D controlCARD hardware files (schematic, BOM, Gerbers): shipped only inside C2000Ware at boards\controlCARDs\TMDSCNCD28388D | `reference-designs/ti/tmdscncd28388d/tmdscncd28388d_schematic.pdf` | https://www.ti.com/tool/C2000WARE | TI tool page https://www.ti.com/tool/TMDSCNCD28388D says "Hardware files can be found by downloading C2000Ware". No separate hardware-file download exists. C2000Ware 26.02.00.00.LTS is offered only as installers (Windows .exe, Linux .run, macOS .app.zip, about 323-327 MB each) from dr-download.ti.com/secure/, shown with a lock icon (TI login required). Per the brief (no login, no installers) nothing was downloaded or run. By hand: install C2000Ware and copy the PDFs/BOM from that folder into this directory, then add rows. |
<!-- /generated:by-hand -->

## Housekeeping record (2026-10-04)

- Removed unreferenced byte-identical duplicates (the surviving copy is the one cited by `gen/`): `datasheets/isolation-interface/ISO1212.pdf` (= `ISO1211.pdf`, one TI ISO121x datasheet), `datasheets/power-supply/750315371.pdf` (= `magnetics/WE-750315371.pdf`), `datasheets/sensing/REF5030.pdf` (= `REF5025E.pdf`, one TI REF50xx datasheet). The notes of the surviving rows say which MPNs they also cover.
- Two files whose rows said OK were missing from disk and were downloaded again from the manufacturer link: `sensing/OPA320.pdf` (sha256 identical to the recorded value) and `sensing/CSS2H-3920.pdf` (cited by `gen/data/pin_audit_parts_B.csv`; its file and row had disappeared during the reconcile; sha256 identical to the copy hashed before).
- Provenance: every library file has a URL in the manifest. `reference-designs/megarevo/pmd-75-g3/spec.md` is a hand-transcribed web table and carries its source page, retrieval date and hash in its own header.
- The design-file archives' CAD, Gerber and STEP content stays inside the ZIPs. Four small Altium files (`wolfspeed/crd-020dd17p-j`, the only schematic form of that design) and two small cold-plate STEP files (`wolfspeed/crd60dd12n-gmb`) were unpacked into `design-files/` earlier in the day and are kept as found; they are copies of ZIP content.

