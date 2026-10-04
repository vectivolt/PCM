# Schematic generators

One Python script per board. Running it writes a real KiCad 10 project, verifies it and writes the BOM:

```bash
.venv/bin/python gen/bmu_gw.py        # -> hardware/BMU-GW/ (schematic, PDF, netlist, ERC, report) + bom/BMU-GW_BOM.csv
```

`gen/bmu_gw.py` is the pattern to copy. `gen/dcdclib.py` (docstring) defines the catalog-entry format and the
`Builder` API. `schgen.py`, `symlib.py`, `sexpr.py` are the KiCad writer copied from the Meter project — do not edit them.

## How a board is written

```python
B = L.Builder(PROJECT, "title", REV, DATE, CATALOG, rails=["+24V", "3V3"], returns=["GND"], ...)
B.new_sheet("01_name", "Sheet title", "two-line description")     # key must start NN_
B.block("Block title", "note printed under the block")
B.part("UCC21710", {"1": "NET_A", "2": None, ...})                # catalog key, {pin number: net}; None = no-connect
B.R("10k", "NET_A", "GND")                                        # jellybean resistor  (pkg=, tol=, note=)
B.C("100n", "3V3", "GND")                                         # jellybean MLCC      (pkg=, volt=, diel=, tol=)
B.flag("+24V")                                                    # PWR_FLAG where a rail enters via a connector/passive
sys.exit(L.build(B, domain_of=domain_of, isolators=iso, crossings=xing, waivers={...}))
```

- A **net is just its name**. The same name on two sheets is the same net. `rails`/`returns` are drawn as power symbols.
- **Every pin of every symbol must be given** a net or `None`. A net with a single pin is an ERC error — connect it,
  give it a test point (`B.TP(net)`), or make the pin `None`.
- Layout is automatic (blocks are packed on A3→A1). Keep a sheet to one function; fewer than 100 parts per reference
  prefix per sheet. Title "root title - sheet title" must stay ≤ 62 characters; cover-box description lines ≤ 85.
- **Catalog entries** live in the board script (`CATALOG = dict(catalog.PARTS, **{...})`). `gen/catalog.py` holds
  parts already verified and shared — use them, do not edit that file. `gen/interfaces.py` holds the inter-board
  connector pin maps — use `interfaces.pins(...)` for those connectors, do not edit that file.
- **Pin tables come from the datasheet PDF**, never from memory: `pdftotext -layout docs/datasheets/<cat>/<MPN>.pdf -`.
  If the PDF is not in `docs/datasheets/<category>/` yet, download it from the manufacturer's own site (PDF only, no
  login) to `docs/datasheets/<category>/<MPN>.pdf`. Put the document number and page/figure of the pin table in a
  comment next to the entry. KiCad stock symbols: `.venv/bin/python gen/dcdclib.py Device:D_Schottky` prints their pins.
- **MPN must be a real orderable part number**, `ds` an existing repo path or an https URL on the maker's site.
  Custom magnetics / busbars / heatsinks: `sourcing="CUSTOM"` with the full spec in `desc`.
- **Isolation domains:** `domain_of(net)` names the domain of every net; only parts listed in `isolators` (barrier
  components) or `crossings` (deliberate non-barrier bridges such as a Y capacitor or a sense divider) may touch two.
- **Part stress:** `vrange(net)` returns the DC range `(vmin, vmax)` of a net against its own domain's reference, or
  `None` for a net without one (switch node, gate, bus line). `build(..., vrange=...)` then checks every jellybean
  capacitor (applied voltage <= 80 % of rated) and resistor (dissipation <= 60 % of the package rating, voltage <= the
  package working voltage) between two ranged nets. Pulse load, ripple current and catalogue parts stay in the
  board's own `design_check()`. Pattern: `VDC` in `gen/bmu_gw.py`.
- ERC must be clean. `waivers={"violation_type": "reason"}` may silence a *warning* type with a written reason;
  errors can never be waived.

## What `build()` checks

ERC via kicad-cli (all severities) · exported netlist identical to the Python table · isolation domains ·
jellybean part stress (when `vrange` is given) · PDF rendered · BOM completeness (manufacturer + MPN + datasheet on file, or GENERIC/RFQ/CUSTOM/NOPART).
