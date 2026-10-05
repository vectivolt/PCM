<!-- breadcrumb --> [Home](../../README.md) › [Documentation](../README.md) › Glossary

# 📚 Glossary

> The abbreviations and project terms used across the documentation, each in one or two lines.

---

| Term | Meaning |
|---|---|
| **A/kW** | Current per kilowatt of rated power. A low-voltage product carries more amperes per kW, and most of a BOM scales with current — the basis of the benchmark's current adjustment. |
| **AEC-Q100 / AEC-Q101** | Automotive qualification standards for integrated circuits / discrete semiconductors; used here as evidence that a part is qualified. |
| **aR fuse** | Semiconductor-protection fuse that protects against short circuits only (partial range). The cost-first battery port has one per pole. |
| **ASSUMED (5,000-unit basis)** | A volume price obtained by multiplying the catalogue price by a factor per part class (`gen/data/volume_factors.csv`), not by a published price. |
| **B1 / B2** | The cost-first module's two insulation barriers: **B1** reinforced, between the live DC side and everything touchable (communication, stop input, fans); **B2** basic, between the DC side and the earthed heatsink and chassis. |
| **Basic / reinforced / functional insulation** | Insulation levels of IEC 60664-1: *basic* protects against shock with one layer; *reinforced* equals two layers (double); *functional* only lets the circuit work. |
| **Benchmark** | The owner's price reference: Megarevo's 1500 V string PCS, 228 kW, selling for 1,500 USD = 6.6 USD/kW. |
| **Benchmark-equivalent BOM** | The benchmark converted to a BOM for a given module: 60 % BOM share of the selling price, then adjusted for the module's current per kW (`gen/cost.py`). |
| **BOM** | Bill of materials. Here: board BOMs, rolled up into module BOMs, then priced into *costed* BOMs. |
| **Build checks** | The checks every board generator runs: ERC, netlist identity, isolation domains, part stress, PDF, BOM completeness, plus the board's own design check. |
| **BUS−** | The negative DC rail, common to both ports of the non-isolated PV module; the cost-first controller is referenced to it. |
| **Catalogue price** | The price break at the highest quantity up to 1,000 pieces from the best documented source, LCSC first. |
| **CM / DM** | Common mode / differential mode (noise, chokes, filters). |
| **CMPSS** | Comparator subsystem of the TI C2000 controller, used for fast hardware trips. |
| **CMRR** | Common-mode rejection ratio of a comparator or amplifier; at a trip node away from 0 V it adds an offset to the comparator band ([risk C9](12-risks-and-open-items.md)). |
| **CMTI** | Common-mode transient immunity of an isolator or gate driver, in V/ns. |
| **Contactor** | Electromechanical switch for the DC port; it must never be asked to open a current above its breaking capacity. |
| **Cost-first architecture** | The re-architecture of 2026-10-04 ([D-044](../requirements/DECISIONS.md)): one power board and one control board, control on the negative rail, one reinforced barrier, lean port protection. |
| **CUSTOM / RFQ / GENERIC / NOPART** | BOM sourcing classes: a part made to our specification; a part to be quoted; a jellybean part specified by value; not a purchased part (test point, net tie). |
| **DAB** | Dual active bridge: an isolated bidirectional DC/DC with two full bridges and a transformer; power is set by the phase shift between the bridges. |
| **Dead time** | The interval when both switches of a leg are off, to prevent shoot-through. |
| **DESAT** | Desaturation detection: the gate driver senses a high on-state voltage (a short circuit) and turns the switch off. |
| **Design check** | A board script's own calculations (`design_check()`): trip thresholds, timing, ratings, loads; written to `<board>_design_check.txt`. |
| **DNP** | Do not populate: a drawn part that is not fitted in a given build. |
| **DPT** | Double-pulse test: the standard bench test of a switching cell's turn-on and turn-off. |
| **Earlier platform** | The roadmap's full-featured implementation (eight boards for PV-P75, modules ending in `-FULL`), kept as reference and not developed further. |
| **ePWM** | Enhanced PWM module of the TI C2000 controller. |
| **ERC** | Electrical rules check of the schematic (`kicad-cli`). |
| **FIT** | Failures in time: failures per 10⁹ device-hours; used here for the cosmic-ray failure rate of high-voltage devices. |
| **FSBB** | Four-switch buck-boost: a non-inverting converter that steps up or down, with a half bridge on each port. |
| **Gate 0** | The roadmap's first development gate, architecture closure: specifications, topology trade-off, source review. |
| **gBat / gPV** | Fuse classes for battery and photovoltaic circuits (full-range breaking). |
| **GFL / GFM** | Grid-following / grid-forming control of an inverter: injecting a current in step with the grid's voltage, or forming the voltage itself (off grid or islanded). |
| **IGBT / SiC MOSFET** | Silicon insulated-gate bipolar transistor / silicon-carbide MOSFET, the two power-switch technologies compared in this project. |
| **IMD** | Insulation monitoring device: measures the insulation resistance of the floating DC system to earth. |
| **Interleaving** | Running parallel phases with shifted switching instants (120° for three, 90° for four) so their ripple currents partly cancel. |
| **LCL filter** | Inverter-side inductor, capacitor, grid-side inductor: the grid filter of PCS-P125. |
| **LCSC** | Chinese electronic-component distributor, the first price source of the cost model. |
| **LUT** | Look-up table; here the DAB's offline table of phase shifts per operating point. |
| **Miller clamp / false turn-on** | A fast voltage rise on a switch's drain couples charge into the gate of the off device through its Miller capacitance; a clamp holds the gate low. |
| **Module** | In this documentation, a product such as PV-P75. A *power module* is a semiconductor package with several switches inside. |
| **MOV / varistor** | Metal-oxide varistor: the surge-protection element of the port network. |
| **MPPT** | Maximum power point tracking: the algorithm that keeps a PV array at its highest-power operating point. |
| **NPC / T-type / two-level** | Inverter leg topologies: neutral-point clamped and T-type are three-level; two-level is the plain half bridge. PCS-P125 is two-level ([D-053](../requirements/DECISIONS.md)). |
| **OpenMagnetics** | Open-source magnetics design and verification tool, used as one of three views in `sim/magnetics.py`. |
| **OVC** | Overvoltage category (IEC 60664-1): the impulse level a circuit must withstand. |
| **PCS** | Power conversion system: here a bidirectional battery inverter (DC to three-phase AC). |
| **PD** | Pollution degree (insulation coordination) — or partial discharge in transformer testing; the text says which. |
| **PE** | Protective earth. |
| **PELV / SELV** | Protective / safety extra-low voltage: circuits a person may touch. The earlier platform put the controller on a PELV island; the cost-first design keeps only communication and fans on the SELV side. |
| **PLL** | Phase-locked loop: tracks the angle and frequency of the grid voltage. |
| **PR controller** | Proportional-resonant current controller in the stationary frame, with resonant terms at the fundamental and selected harmonics (PCS-P125: h5, h7). |
| **Precharge** | Charging the DC-link capacitors through a resistor before the main contactor closes. |
| **REAL (5,000-unit basis)** | A volume price taken from a published break of at least 1,000 pieces. |
| **SCR** | Short-circuit ratio of the grid at the connection point: "stiff" is very large, SCR 5 is a weak grid. |
| **SOA** | Safe operating area of a power device. |
| **SPS / TPS** | Single / triple phase-shift modulation of a DAB. |
| **t<sub>SC</sub> / E<sub>SC</sub>** | Short-circuit withstand time / energy of a device. No Chinese SiC maker publishes them; each gate-drive preset prints the values a maker must confirm before release. |
| **THDi** | Total harmonic distortion of the current. |
| **TMR sensor** | Tunnel-magnetoresistance current sensor: an open-loop, low-cost alternative to closed-loop transducers. |
| **Trip latch** | The single hardware latch of the cost-first control board: any trip source turns all gates off until it is cleared. |
| **UVLO** | Under-voltage lock-out: a driver or supply turns its output off below a threshold. |
| **VDE 0884-17** | Standard for the insulation of digital isolators and isolated gate drivers; "granted" means a certificate exists. |
| **VIOSM / VIOWM** | An isolator's surge (impulse) rating / working-voltage rating across its barrier. |
| **ZVS** | Zero-voltage switching: a switch turns on with no voltage across it; essential for the DAB's efficiency. |

---

<!-- footer --> ← [Repository guide](13-repository-guide.md) · [Documentation index](../README.md) · [Decisions](decisions.md) →
