# docs/ - reference library of the DC-DC project

Everything the schematics, BOMs and simulations cite as a source: manufacturers' reference designs, component datasheets and the requirements. Nothing here is bench data; reference designs remove unknowns, they do not set our ratings (REQUIREMENTS.md section 6, rule 5).

## Layout (fixed)

```
docs/
  README.md                  this file
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

## What is in it (counts from SOURCES.csv, 2026-10-04)

| | rows | OK (file on disk) | MANUAL (to fetch by hand) | MB of OK files |
|---|---:|---:|---:|---:|
| reference designs | 62 | 46 | 16 | 227 |
| datasheets | 229 | 213 | 16 | 422 |
| **total** | **291** | **259** | **32** | **649** |

Plus 28 small unpacked documents under `design-files/` (12.3 MB, covered by their archive's row) and the per-design READMEs.

### Reference designs per vendor

| vendor | designs | OK | MANUAL | MB |
|---|---:|---:|---:|---:|
| infineon | 1 | 1 | 0 | 5.8 |
| megarevo | 2 | 8 | 0 | 23.3 |
| onsemi | 1 | 0 | 8 | 0.0 |
| st | 2 | 0 | 6 | 0.0 |
| ti | 9 | 28 | 1 | 42.4 |
| wolfspeed | 5 | 9 | 1 | 155.7 |

| design | what it is | OK | MANUAL |
|---|---|---:|---:|
| `infineon/ref-10kw3lnpc2` | 10 kW 3-level NPC2 inverter (user manual) | 1 | 0 |
| `megarevo/pma` | PMA modular PCS, AC side (competitor datasheets, manual) | 7 | 0 |
| `megarevo/pmd-75-g3` | 75 kW non-isolated DC/DC MPPT module = PV-P75 target | 1 | 0 |
| `onsemi/tnd6401-6408-25kw-dcfc` | 25 kW SiC DC fast-charger design series (8 parts) | 0 | 8 |
| `st/stdes-dabbidir` | 25 kW DAB (data brief, manual, schematic, BOM, test report) | 0 | 5 |
| `st/stdes-pfcbidir` | 15 kW 3-level bidirectional AFE (data brief) | 0 | 1 |
| `ti/tida-010054` | 10 kW DAB, 100 kHz, F280039 | 3 | 0 |
| `ti/tida-010247` | BMU for HV storage (BQ769x2, CAN stacking) | 3 | 0 |
| `ti/tida-010253` | battery control unit (BCU) for HV racks | 3 | 0 |
| `ti/tida-01606` | 11 kW bidirectional T-type inverter/PFC | 3 | 0 |
| `ti/tida-050063` | active precharge, TPSI3052 | 4 | 0 |
| `ti/tida-050082` | positive-rail active precharge, TPSI31P1 | 3 | 0 |
| `ti/tidm-buckboost-bidir` | C2000 non-isolated bidirectional buck-boost | 4 | 0 |
| `ti/tidm-solar-dcdc` | C2000 MPPT boost + LLC (500 W) | 4 | 0 |
| `ti/tmdscncd28388d` | F28388D controlCARD (our controller) | 1 | 1 |
| `wolfspeed/crd-020dd17p-j` | 20 W 60-1000 V aux flyback (25 W sibling guide + Altium files) | 2 | 0 |
| `wolfspeed/crd-25bda6512n-k` | 25 kW T-type inverter/PFC (guide only) | 1 | 1 |
| `wolfspeed/crd-60dd12n` | 60 kW 4-phase interleaved boost (guide, schematics, BOMs) | 2 | 0 |
| `wolfspeed/crd250da12e-xm3` | XM3 three-phase liquid-cooled SiC inverter stack (guide, schematics, BOMs) | 2 | 0 |
| `wolfspeed/crd60dd12n-gmb` | 60 kW isolated DAB = DAB-D60 basis (guide, schematics, BOMs, cold plate) | 2 | 0 |

### Datasheets per category

| category | datasheets | OK | MANUAL | MB |
|---|---:|---:|---:|---:|
| connectors | 11 | 11 | 0 | 8.0 |
| controllers | 7 | 6 | 1 | 61.2 |
| gate-drivers | 10 | 8 | 2 | 17.2 |
| isolation-interface | 25 | 25 | 0 | 55.6 |
| magnetics | 14 | 14 | 0 | 24.6 |
| passives-capacitors | 16 | 16 | 0 | 12.9 |
| power-semiconductors | 37 | 34 | 3 | 41.5 |
| power-supply | 26 | 26 | 0 | 75.9 |
| protection | 46 | 36 | 10 | 28.5 |
| sensing | 30 | 30 | 0 | 59.1 |
| thermal | 6 | 6 | 0 | 36.7 |
| timing | 1 | 1 | 0 | 0.6 |

By manufacturer: Texas Instruments 79, Wurth Elektronik 18, Vishay 14, Wolfspeed 13, TDK Electronics 9, Nexperia 8, Bourns 8, Infineon 6, LEM 5, onsemi 4, Magnetics (Spang & Company) 4, Diodes Inc 4, Littelfuse 4, Samtec 3, Microchip 3, Diodes Incorporated 3, DEHN 3, CITEL 3, Mersen 3, Raycap 3, STMicroelectronics 2, RECOM 2, Phoenix Contact 2, TE Connectivity 2, ebm-papst 2, Honeywell 2, Murata 1, Vishay Draloric 1, KEMET 1, Rubycon 1, Infineon Technologies Bipolar 1, OBO Bettermann 1, Altran Magnetics 1, Schurter 1, ABB (Furse) 1, Omron 1, TDK 1, Sensata 1, Miba Resistors 1, Eaton 1, SCHURTER 1, Allegro MicroSystems 1, SIKA 1, Sanyo Denki 1, Delta Electronics 1, Abracon 1.

## Manifests

`docs/SOURCES.csv` is the master. Columns: `path,kind,vendor,id,title,url,retrieved,bytes,sha256,status,note`.

- `path` relative to the repository root; `kind` = `reference-design` or `datasheet`; `vendor` = vendor folder (reference designs) or manufacturer (datasheets); `id` = design id or MPN.
- `status` = `OK` (the file is on disk; `bytes` and `sha256` are those of the file) or `MANUAL` (not downloaded: `url` is the direct link when one is known, otherwise the product page; `bytes` 0, empty `sha256`; `note` says why).
- Every file under `reference-designs/` and `datasheets/` has exactly one row, except `README.md`, `SOURCES.csv`, `spec.md` and the unpacked `design-files/` contents (covered by the row of their archive).
- The two sub-manifests carry the same rows (reference-designs: `path,vendor,design,title,url,retrieved,bytes,sha256,status,note`; datasheets: `path,category,manufacturer,mpn,title,url,retrieved,bytes,sha256,status,note`). Edit all three together.

Check (run from the repository root; prints the problems, or `none`):

```bash
python3 - <<'EOF'
import csv, hashlib, os, collections
rows = list(csv.DictReader(open("docs/SOURCES.csv", newline="")))
bad, seen = [], collections.Counter(r["path"] for r in rows)
for r in rows:
    p = r["path"]
    if r["status"] == "OK":
        if not os.path.isfile(p): bad.append(("missing", p)); continue
        b = open(p, "rb").read()
        if len(b) != int(r["bytes"]) or hashlib.sha256(b).hexdigest() != r["sha256"]: bad.append(("mismatch", p))
    elif os.path.exists(p): bad.append(("MANUAL but present", p))
for tree in ("docs/reference-designs", "docs/datasheets"):
    for dp, _, fn in os.walk(tree):
        if "design-files" in dp.split(os.sep): continue
        for f in fn:
            if f in ("README.md", "SOURCES.csv", "spec.md", ".DS_Store"): continue
            if seen[os.path.join(dp, f)] != 1: bad.append(("no/duplicate row", os.path.join(dp, f)))
print(len(rows), "rows; problems:", bad or "none")
EOF
```

## How to add a document

1. **Place it.** Reference design: `docs/reference-designs/<vendor>/<design>/` (lower-case vendor and design id; one folder per design, with a short `README.md`: what it is, native ratings confirmed from the document, which of our boards it informs, its limitation for us). Datasheet: `docs/datasheets/<category>/<MPN-or-family>.pdf` in one of the categories above. Name files `<design>_<kind>_<doc-id>.<ext>` for reference designs (kind = design-guide, schematic, bom, test-report, user-guide, ...).
2. **Download from the manufacturer's own domain only** (no mirrors, distributors or aggregators): `curl -L --fail -A "Mozilla/5.0" -o <file> <url>`. Check with `file` that it really is a PDF (or ZIP), skip anything over 100 MB, never execute anything. Compare `shasum -a 256` with the library first: if the bytes already exist under another name, do not add a second copy; put a sentence in the existing row's `note` instead.
3. **Archives.** Keep the ZIP in the design folder as `<design-id>_design-files.zip` and unpack only what a person can read (PDF schematics, BOM xlsx/csv/pdf, drawings) into `design-files/`. CAD, Gerber and STEP files stay inside the ZIP.
4. **Add the row** to `docs/SOURCES.csv` and to the sub-manifest of its tree: `bytes` = `stat -f%z <file>`, `sha256` = `shasum -a 256 <file>`, `retrieved` = date, `url` = the exact link used, `note` = anything a reader needs (revision, family datasheet, what it was used for).
5. **If the site refuses** (403, connection reset, login, licence click-through, CAPTCHA) do not work around it: add the row with `status` = `MANUAL`, the direct link in `url`, the reason in `note`, and list it under "Still to download by hand" below.
6. Run the check above. Do not edit `gen/`, `sim/`, `hardware/`, `bom/` or `requirements/` to make a document fit.

## Still to download by hand

Every item below was re-checked on 2026-10-04: each link was tried once more with a plain `curl -L --fail -A "Mozilla/5.0"` and refused (the TI and Wolfspeed items have no direct link, their pages were inspected); nothing was circumvented. Open the link in a browser, save the file at the path shown (relative to `docs/`), then change its row to `OK` with `bytes` and `sha256` (see "How to add a document").

### onsemi (12)

Reason: HTTP 403 from onsemi's Akamai edge for every onsemi.com PDF path.

| item | save as | link |
|---|---|---|
| NCD57090 | `datasheets/gate-drivers/NCD57090.pdf` | https://www.onsemi.com/pdf/datasheet/ncd57090a-d.pdf |
| NTH4L014N120M3P | `datasheets/power-semiconductors/NTH4L014N120M3P.pdf` | https://www.onsemi.com/download/data-sheet/pdf/nth4l014n120m3p-d.pdf |
| NTH4L022N120M3S | `datasheets/power-semiconductors/NTH4L022N120M3S.pdf` | https://www.onsemi.com/pdf/datasheet/nth4l022n120m3s-d.pdf |
| NTH4L028N170M1 | `datasheets/power-semiconductors/NTH4L028N170M1.pdf` | https://www.onsemi.com/download/data-sheet/pdf/nth4l028n170m1-d.pdf |
| onsemi TND6401/D part 1: structure of a fast EV charger and key electrical specifications | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6401_part1-structure-and-key-specs.pdf` | https://www.onsemi.com/download/reference-designs/pdf/tnd6401-d.pdf |
| onsemi TND6402/D part 2: solution overview | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6402_part2-solution-overview.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK  (direct link not verified, pattern: https://www.onsemi.com/pub/collateral/tnd6402-d.pdf) |
| onsemi TND6403/D part 3: PFC simulation and development | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6403_part3-pfc-simulation-and-development.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK  (direct link not verified, pattern: https://www.onsemi.com/pub/collateral/tnd6403-d.pdf) |
| onsemi TND6404/D part 4: DC-DC (DAB) simulation and design considerations | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6404_part4-dc-dc-dab-simulation-and-design.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK  (direct link not verified, pattern: https://www.onsemi.com/pub/collateral/tnd6404-d.pdf) |
| onsemi TND6405/D part 5: control algorithms, modulation schemes and feedback | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6405_part5-control-algorithms-modulation-feedback.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK  (direct link not verified, pattern: https://www.onsemi.com/pub/collateral/tnd6405-d.pdf) |
| onsemi TND6406/D part 6: gate drive system for power modules | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6406_part6-gate-drive-system.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK  (direct link not verified, pattern: https://www.onsemi.com/pub/collateral/tnd6406-d.pdf) |
| onsemi TND6407/D part 7: auxiliary power supply units for 800 V EV chargers | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6407_part7-auxiliary-power-800v.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK  (direct link not verified, pattern: https://www.onsemi.com/pub/collateral/tnd6407-d.pdf) |
| onsemi TND6408/D part 8: thermal management | `reference-designs/onsemi/tnd6401-6408-25kw-dcfc/tnd6408_part8-thermal-management.pdf` | https://www.onsemi.com/design/evaluation-board/SEC-25KW-SIC-PIM-GEVK  (direct link not verified, pattern: https://www.onsemi.com/pub/collateral/tnd6408-d.pdf) |

### STMicroelectronics (8)

Reason: curl exit 92 (HTTP/2 stream INTERNAL_ERROR): st.com closes the connection for non-browser clients.

| item | save as | link |
|---|---|---|
| M24C64 | `datasheets/controllers/M24C64.pdf` | https://www.st.com/resource/en/datasheet/m24c64-f.pdf |
| STGAP2SICS | `datasheets/gate-drivers/STGAP2SICS.pdf` | https://www.st.com/resource/en/datasheet/stgap2sics.pdf |
| STDES-DABBIDIR bill of materials v2.0 | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_bom_v2-0.pdf` | https://www.st.com/content/ccc/resource/technical/document/bill_of_materials/group2/63/8e/bf/15/c6/c5/4a/74/STDES-DABBIDIR_BOM/files/stdes-dabbidir-bom.pdf/jcr:content/translations/en.stdes-dabbidir-bom.pdf |
| STDES-DABBIDIR data brief DB4879 | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_data-brief_db4879.pdf` | https://www.st.com/resource/en/data_brief/stdes-dabbidir.pdf |
| STDES-DABBIDIR schematic v2.0 (Rev 2, 13 Dec 2023) | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_schematic_v2-0.pdf` | https://www.st.com/resource/en/schematic_pack/stdes-dabbidir-schematic.pdf |
| STDES-DABBIDIR test report TN1435 [extra] | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_test-report_tn1435.pdf` | https://www.st.com/resource/en/technical_note/tn1435-stdesdabbidir--test-report-stmicroelectronics.pdf |
| STDES-DABBIDIR user manual UM3198 | `reference-designs/st/stdes-dabbidir/stdes-dabbidir_user-manual_um3198.pdf` | https://www.st.com/resource/en/user_manual/um3198-25-kw-dual-active-bridge-bidirectional-power-converter-for-ev-charging-and-battery-energy-storage-systems-stmicroelectronics.pdf |
| STDES-PFCBIDIR data brief | `reference-designs/st/stdes-pfcbidir/stdes-pfcbidir_data-brief.pdf` | https://www.st.com/resource/en/data_brief/stdes-pfcbidir.pdf |

### Littelfuse (4)

Reason: HTTP 403.

| item | save as | link |
|---|---|---|
| 5.0SMDJ | `datasheets/protection/5.0SMDJ.pdf` | https://www.littelfuse.com/assetdocs/tvs-diode-5?assetguid=d3fdc005-98d5-4584-977b-a6ee20c5178d |
| SMBJ | `datasheets/protection/SMBJ.pdf` | https://www.littelfuse.com/assetdocs/tvs-diodes-smbj-series-datasheet?assetguid=ba555e99-a12d-4f72-a0b6-86b06c67171e |
| SMCJ | `datasheets/protection/SMCJ.pdf` | https://www.littelfuse.com/assetdocs/tvs-diodes-smcj-datasheet?assetguid=37388813-0d6d-4329-969b-1aa8b7614ac1 |
| SPD2 PV series | `datasheets/protection/SPD2-PV.pdf` | https://www.littelfuse.com/assetdocs/spd2-pv-series-datasheet?assetguid=da42ceb5-ac40-4ee0-95c3-d14c85ecd0e9 |

### TE Connectivity (2)

Reason: HTTP 403.

| item | save as | link |
|---|---|---|
| EV200 | `datasheets/protection/EV200.pdf` | https://www.te.com/commerce/DocumentDelivery/DDEController?Action=srchrtrv&DocNm=5-1773450-5_sec7_EV200A&DocType=CS&DocLang=English&DocFilename=ENG_CS_5-1773450-5_sec7_EV200A_0313_._5-1773450-5_Sec7_EV200A.pdf |
| EVC 250 | `datasheets/protection/EVC250.pdf` | https://www.te.com/content/dam/te-com/documents/automotive/global/evc-250-datasheet.pdf |

### Sensata (Gigavac) (1)

Reason: HTTP 403.

| item | save as | link |
|---|---|---|
| HX360 | `datasheets/protection/HX360.pdf` | https://www.sensata.com/sites/default/files/a/sensata-gigavac-hx360-contactor-datasheet.pdf |

### Eaton (1)

Reason: curl exit 92 (HTTP/2 stream INTERNAL_ERROR).

| item | save as | link |
|---|---|---|
| PV-NH | `datasheets/protection/PV-NH.pdf` | https://www.eaton.com/content/dam/eaton/products/electrical-circuit-protection/fuses/data-sheets/bus-iec-ds-720133-nhpvfuselinks.pdf |

### Phoenix Contact (2)

Reason: HTTP 403 (Akamai "Access Denied").

| item | save as | link |
|---|---|---|
| VAL-MS 1000DC/2+V (2805091) | `datasheets/protection/2805091.pdf` | https://www.phoenixcontact.com/en-us/products/pv-arrester-val-ms-1000dc2v-2805091?type=pdf |
| VAL-MB-T2 1000DC-PV/2+V (2905645) | `datasheets/protection/2905645.pdf` | https://www.phoenixcontact.com/en-us/products/pv-arrester-val-mb-t2-1000dc-pv2v-2905645?type=pdf |

### Texas Instruments (1)

Reason: No separate download: the hardware files ship only inside the C2000Ware installers (.exe / .run / .app.zip, ~325 MB) which sit behind a TI login (lock icon, dr-download.ti.com/secure/). Installers are not downloaded or run.

| item | save as | link |
|---|---|---|
| TMDSCNCD28388D controlCARD hardware files (schematic, BOM, Gerbers): shipped only inside C... | `reference-designs/ti/tmdscncd28388d/tmdscncd28388d_schematic.pdf` | https://www.ti.com/tool/C2000WARE |

### Wolfspeed (1)

Reason: The product page has no download link for the design files (the CRD-60DD12N and XM3 pages do); ask Wolfspeed or check the page in a browser.

| item | save as | link |
|---|---|---|
| CRD-25BDA6512N-K design files (main board) | `reference-designs/wolfspeed/crd-25bda6512n-k/crd-25bda6512n-k_design-files.zip` | https://www.wolfspeed.com/products/power/reference-designs/crd-25bda6512n-k/ |

## Housekeeping record (2026-10-04)

- Removed unreferenced byte-identical duplicates (the surviving copy is the one cited by `gen/`): `datasheets/isolation-interface/ISO1212.pdf` (= `ISO1211.pdf`, one TI ISO121x datasheet), `datasheets/power-supply/750315371.pdf` (= `magnetics/WE-750315371.pdf`), `datasheets/sensing/REF5030.pdf` (= `REF5025E.pdf`, one TI REF50xx datasheet). The notes of the surviving rows say which MPNs they also cover.
- Two files whose rows said OK were missing from disk and were downloaded again from the manufacturer link: `sensing/OPA320.pdf` (sha256 identical to the recorded value) and `sensing/CSS2H-3920.pdf` (cited by `gen/data/pin_audit_parts_B.csv`; its file and row had disappeared during the reconcile; sha256 identical to the copy hashed before).
- Provenance: every library file has a URL in the manifest. `reference-designs/megarevo/pmd-75-g3/spec.md` is a hand-transcribed web table and carries its source page, retrieval date and hash in its own header.
- The design-file archives' CAD, Gerber and STEP content stays inside the ZIPs. Four small Altium files (`wolfspeed/crd-020dd17p-j`, the only schematic form of that design) and two small cold-plate STEP files (`wolfspeed/crd60dd12n-gmb`) were unpacked into `design-files/` earlier in the day and are kept as found; they are copies of ZIP content.

