# Cost review — what is over-engineered, and is the topology right?

**Date:** 2026-10-04 · **Trigger:** owner: "cost is too high compared with Megarevo … check what we are
over-engineering … is our topology correct and proper … we must produce it within the budget", with the benchmark
"Megarevo 1500 V string PCS, 228 kW version sells for 1,500 USD".

Every cost figure below comes from `gen/cost.py` (`bom/COST.md`), run on 2026-10-04. About 45 % of the total is
estimates; nothing has been quoted or bought.

## 1. The benchmark

| | Power | Price | Per kW |
|---|---|---|---|
| Megarevo MPHV 1500 V string PCS (complete IP66 AC/DC unit; price stated by the owner) | 228 kW | 1,500 USD selling price | **6.6 USD/kW** |
| Chinese DC/DC module listings ≥ 30 kW (17 marketplace listings, export list prices) | 30–125 kW | — | 22–74 USD/kW, median 34 |
| **Our PV-P75 today — BOM only, no PCB, assembly, enclosure, margin** | 75 kW | 2,789 USD | **37 USD/kW** |
| Our DAB-D60 today — BOM only | 60 kW | 3,606 USD | 60 USD/kW |

Megarevo publishes no price for the PMD-75-G3 itself. A 75 kW unit costs more per kW than a 228 kW one, so the
working assumption here is a selling price of roughly 8–11 USD/kW (600–800 USD) for a 75 kW MPPT DC/DC of that class.
**Our BOM alone is three to five times that selling price.** This is not a matter of trimming.

## 2. Is the topology right?

**The power-stage topology is right. The system architecture around it is what costs the money.**

- A two-level four-switch buck-boost with interleaved phases and Chinese 1700 V SiC devices is the cheapest way to
  cover 250–1000 V on both ports: the re-run trade study (`sim/out/pv_tradeoff/gate0b.md`) puts the best three-level
  alternative 29 % higher per cell, with twice the gate-drive channels.
- What that power stage needs for 75 kW costs about **650 USD** at catalogue prices: 24 SiC MOSFETs 98, gate drive
  124, three inductors 240, film capacitors about 190. That is 9 USD/kW, and the inductors and capacitors still have
  room.
- The other **2,100 USD** is everything built around the power stage. That is where the over-engineering is.

## 3. What is over-engineered

| # | What we built | What a product of this price class does | Cost today (PV-P75) | Cost if done lean |
|---|---|---|---|---|
| 1 | **Cabinet-level protection inside every module:** three TDK contactors (two in parallel on the battery port so a fuse always clears before a contact opens), four NH industrial fuses, active discharge with thermostats, proof-test readbacks, terminal-side voltage sensing on both ports, six 1700 V SiC switches for the insulation monitor | One fuse pair and one relay / contactor with precharge on the battery side; the PV side is a current-limited source and needs neither precharge nor fuses; fault currents above the contactor's rating are left to the battery rack's own protection; varistors on the board | PV-PORT **1,358** | 150–250 |
| 2 | **Control on a protected low-voltage (PELV) island, with a reinforced isolation barrier at every driver and sensor:** 12 isolated bias modules, isolated amplifiers on every voltage and current, closed-loop current transducers with their own ±15 V supplies, RS-422 links between boards. 132 barrier parts had to be audited | Control referenced to the negative DC rail; one reinforced barrier, at the communication port and the fans; voltages measured with resistor dividers, currents with cheap sensors or shunts | about 300 spread over the boards | about 60 |
| 3 | **Eight boards, 3,345 parts:** a 599-part controller card and a 565-part system board with two CAN, CAN FD, two RS-485, Ethernet, 8 + 8 digital I/O, 12 temperature channels, 4 fan channels, redundant 24 V feeds, a dual-channel safety chain with fault analysis; three separate cell boards each with its own logic and supplies; separate auxiliary supply and gateway boards | One power board and one small control board, roughly 1,000 parts; CAN + RS-485; a single hardware trip latch with a watchdog | CTRL + SYS + AUX + BMU **261**, plus PCB and assembly outside the BOM | 60–80 |
| 4 | **A dual-core F28388D** (19.8 USD) | A single-core TI C2000 (F280039C class, about 5–6 USD) has the 16 PWM outputs and the ADCs needed | 20 | 6 |
| 5 | **Fans chosen for −40 °C at a single-piece Western distributor price** (88–113 USD each) | Asian fans bought in volume | **264–339** | 30–60 |
| 6 | **Terminal-short survival:** 48 clamp rectifiers so that a bolted short at the terminals does not damage the SiC devices | Not fitted: the module fails safe (the fuse clears) and is repaired | 128 | 0 |
| 7 | **Inductors and chokes designed without a cost ceiling:** profiled litz on stacked toroids (80 USD each), port chokes sized for 40 dB at 16 kHz (100 USD each), a requirement no emission standard sets | Inductors at 20–30 USD; chokes sized for the 150 kHz–30 MHz limits | 440 | 130–160 |
| 8 | **Efficiency 0.5 point above the competitor** (99.5 % against 99 %) | — | not a cost item now: the Chinese devices made it nearly free | — |

Items 1–3 are one decision, not three: we built an industrial "safe low-voltage control + full protective
separation + protection that needs nothing upstream" platform, as the roadmap described it, and that platform cannot
be made cheap. A Megarevo-class product is built the other way round.

## 4. Two ways forward

| | A. Trim the present platform | B. Cost-first architecture |
|---|---|---|
| What changes | Lean port, cheaper fans, no clamp banks, cheaper inductors; boards and isolation concept stay | Control on the negative rail, one power board + one control board, lean protection, self-powered from the DC side, small TI C2000 |
| PV-P75 BOM at catalogue prices | about **1,700–1,900 USD** (23–25 USD/kW) | about **700–850 USD** (9–11 USD/kW); perhaps 550–600 at Chinese volume prices |
| Against the benchmark | still about three times too high | in the same range |
| What is kept | everything drawn so far | the power stage, its devices, the gate-drive channel, the magnetics designs, all simulations and methods |
| What is given up | — | the PELV control island and its 132 barriers (service and debugging then need isolated tools — normal for this product class); the common controller card shared with other products; Ethernet, CAN FD, most of the spare I/O; redundant 24 V feeds and the dual-channel safety chain; terminal-short survival |

## 5. Decision

**Path B.** Path A cannot reach the budget whatever is trimmed. Recorded as D-044 in `DECISIONS.md`.

- **Working budget (my assumption — the owner gave the PCS price, not a DC/DC price):** PV-P75 BOM ≤ **800 USD** at
  catalogue prices (about 10.7 USD/kW), DAB-D60 ≤ 950 USD. If the real price target for the 75 kW module is known, it
  replaces this number.
- **Kept from the work so far:** two-level interleaved four-switch buck-boost at 32 kHz; 2 × Sichain SG2M040170HJ per
  position with the Microchip part as the qualified fallback; the NSI6651 gate-drive channel with its clamp and
  short-circuit booster; the inductor and its verification; the control and MPPT design; the varistor surge network;
  the build checks, the pin audit, the stress check and the cost model.
- **Replaced:** CTRL-C2000, SYS-IO-AUX, PV-PORT, three PVCELL-25 boards, AUX-HV and BMU-GW give way to one power
  board and one control board for the PV module. The existing boards stay in the repository as the roadmap's
  full-featured implementation; no further cost is sunk into them.
- **Not negotiable in the lean design:** hardware over-current, over-voltage and short-circuit trips that need no
  firmware; a contactor that is never opened above its breaking capacity; reinforced insulation between the DC side
  and anything a person or another device can touch (communication, fans, enable input); insulation monitoring.

## 6. What is still unknown

- The real selling price of the PMD-75-G3, and so the real budget.
- Chinese volume prices: every figure here is a catalogue or marketplace price, or an estimate.
- Fuses: no Asian link with a complete public datasheet was found (the makers' documents are behind forms or blocked
  hosts); the lean design will specify the fuse by its required characteristics and name candidates for a quotation.
- The DAB-D60: its isolated topology with a liquid cold plate and a 60 kW transformer will stay well above the PV
  module per kW even when built lean.
