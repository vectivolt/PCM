"""Overview charts and generated tables for the landing pages (style: docs/assets/STYLE.md).

Run:  .venv/bin/python docs/assets/figures_overview.py
In :  bom/<module>_costed_BOM.csv, bom/<module>_module_BOM.csv, bom/COST.md      (gen/cost.py, gen/build_all.py)
      gen/data/costfirst_pcs_bom.csv, gen/data/market_prices.csv, sim/out/pcs_design/pcs_spec.json
      sim/out/compare_megarevo/comparison.csv + report.md, hardware/*/outputs/*_report.json
      docs/requirements/DECISIONS.md (the working budget of D-044)
Out:  docs/assets/img/*.png, and the blocks between <!-- BEGIN:name --> and <!-- END:name --> in the pages of PAGES.
Every number is read from those files at run time; the benchmark arithmetic is gen/cost.py's own (imported).
Exit code 1 if a data file, a page or a marker is missing, or if the self-check fails.
"""
import collections
import csv
import glob
import json
import os
import re
import sys

sys.dont_write_bytecode = True          # leave no __pycache__ in gen/ or docs/assets/
from style import *  # noqa: E402,F401,F403  (REPO, IMG, palette, plt, save)
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Patch  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "gen"))
import build_all as BA  # noqa: E402  board and module lists
import cost as C  # noqa: E402        group_of(), benchmark constants, load_market()

PAGES = {"README.md": ["cost-summary"],
         "docs/guide/01-overview.md": ["board-status"],
         "docs/guide/07-sourcing-and-cost.md": ["cost-summary"],
         "docs/guide/11-megarevo-comparison.md": ["megarevo-score", "megarevo-table"]}
LIGHT_TEAL, PALE = "#7FD4CE", "#C5CED6"


def need(path):
    p = os.path.join(REPO, path)
    if not os.path.exists(p):
        sys.exit("MISSING data file: %s" % path)
    return p


def rows(path):
    return list(csv.DictReader(open(need(path), encoding="utf-8")))


def usd(x):
    return "{:,.0f}".format(x)


def subtitle(ax, title, sub):
    ax.set_title(title, pad=24)
    ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=8.5, color=SLATE, va="bottom")


# ---------------------------------------------------------------------------------------------- data
def module_cost(m):
    """Totals of bom/<m>_costed_BOM.csv: catalogue, 5,000 units, by evidence basis and by function group."""
    t = {"cat": 0.0, "k5": 0.0, "real5": 0.0, "unpriced": 0, "basis": collections.Counter(),
         "groups": collections.defaultdict(collections.Counter)}
    for r in rows("bom/%s_costed_BOM.csv" % m):
        if r["ext_cost_usd"] == "":                 # UNPRICED: listed in COST.md, never counted as zero
            t["unpriced"] += 1
            continue
        ext, ext5, src = float(r["ext_cost_usd"]), float(r["unit_cost_5k_usd"] or 0) * int(r["Qty"]), r["price_source"]
        basis = ("class rule" if src.startswith("estimate class:") else "estimate" if src.startswith("estimate ")
                 else "price")
        g = t["groups"][C.group_of(r)]
        g[basis] += ext
        g["k5"] += ext5
        t["basis"][basis] += ext
        t["cat"] += ext
        t["k5"] += ext5
        t["real5"] += ext5 if r["basis_5k"].startswith("REAL") else 0.0
    return t


def check_against_cost_md(costs):
    """The costed CSVs and bom/COST.md come from the same gen/cost.py run; a mismatch means one of them is stale."""
    md = open(need("bom/COST.md"), encoding="utf-8").read()
    for m, t in costs.items():
        hit = re.search(r"^\| %s(?: \(old platform, for contrast\))? \| [\d.]+ \| ([\d.]+) \| [\d.]+ \| ([\d.]+) \|" % re.escape(m), md, re.M)
        if not hit or abs(float(hit.group(1)) - t["cat"]) > 0.02 or abs(float(hit.group(2)) - t["k5"]) > 0.02:
            sys.exit("bom/COST.md and bom/%s_costed_BOM.csv disagree - re-run gen/cost.py" % m)


def benchmark():
    """gen/cost.py's benchmark arithmetic: BOM share of the selling price and the equivalent BOM of a module."""
    need("gen/data/market_prices.csv")
    bench = next((r for r in C.load_market() if r["cat"] == "benchmark"), None)
    if not bench:
        sys.exit("no benchmark row (string PCS) in gen/data/market_prices.csv")
    share = (1 - C.BENCH_MARGIN) * C.BOM_SHARE_OF_COGS
    bom_kw = bench["usd"] * share / bench["kw"]

    def equivalent(m, kw):
        return bom_kw * ((1 - C.CURRENT_SHARE) + C.CURRENT_SHARE * C.MODULE_AMPS[m] / kw / C.BENCH_DC_A_PER_KW) * kw
    return {"price_kw": bench["usd"] / bench["kw"], "bom_kw": bom_kw, "share": share, "eq": equivalent}


def pcs_costs():
    """PCS-P125: the design study of D-053 (pcs_spec.json, the current figure) and the three-level T-type estimate of the
    architect's list that D-053 withdrew (TOTAL row of costfirst_pcs_bom.csv, still printed in bom/COST.md)."""
    spec = json.load(open(need("sim/out/pcs_design/pcs_spec.json"), encoding="utf-8"))
    c = spec["cost_usd"]
    out = {"3-wire": {"cat": c["three_wire"]["catalogue"], "k5": c["three_wire"]["5k"], "real": 100 * c["evidence_share_5k"]},
           "4-wire": {"cat": c["four_wire"]["catalogue"], "k5": c["four_wire"]["5k"], "real": None},
           "topology": spec["design"]["topology"], "devices": spec["design"]["devices"]}
    row = next((r for r in rows("gen/data/costfirst_pcs_bom.csv") if r["block"] == "TOTAL" and "3-wire" in r["function"]), None)
    if not row:
        sys.exit("no 3-wire TOTAL row in gen/data/costfirst_pcs_bom.csv")
    out["t-type"] = {"cat": float(row["ext_price_usd"]), "k5": float(row["unit_price_5k_usd"])}
    return out


def board_reports():
    reps = {}
    for p in sorted(glob.glob(os.path.join(REPO, "hardware", "*", "outputs", "*_report.json"))):
        r = json.load(open(p, encoding="utf-8"))
        reps[r["project"]] = r
    if not reps:
        sys.exit("MISSING data file: hardware/*/outputs/*_report.json")
    return reps


def parts_by_board(m):
    """Fitted parts per board of a module, from the 'Used on' column of bom/<m>_module_BOM.csv."""
    n = collections.Counter()
    for r in rows("bom/%s_module_BOM.csv" % m):
        for hit in re.finditer(r"([A-Za-z0-9_\-]+): (\d+)", r["Used on"]):
            n[hit.group(1)] += int(hit.group(2))
    return n


def budget():
    hit = re.search(r"PV-P75 BOM ≤ ([\d,]+) USD at catalogue prices", open(need("docs/requirements/DECISIONS.md"), encoding="utf-8").read())
    if not hit:
        sys.exit("working budget of D-044 not found in docs/requirements/DECISIONS.md")
    return float(hit.group(1).replace(",", ""))


# ---------------------------------------------------------------------------------------------- charts
def fig_cost_journey(pv, full, eq, bud, n_full):
    vals = [full["cat"], pv["cat"], pv["k5"], eq]
    labels = ["Earlier platform\n%d boards · catalogue" % n_full, "Cost-first\ncatalogue prices", "Cost-first\n5,000-unit build",
              "Benchmark-equivalent\nBOM (current-adjusted)"]
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    ax.bar(range(4), vals, color=[SLATE, TEAL, NAVY, CORAL], width=0.6)
    ax.grid(axis="x", visible=False)
    top = max(vals)
    for i, v in enumerate(vals):
        ax.text(i, v + top * 0.015, "%s USD\n%.1f USD/kW" % (usd(v), v / 75), ha="center", va="bottom", fontsize=9, color=NAVY)
        if i < 3:
            ax.text(i, v / 2 if v > top * 0.12 else v + top * 0.13, "%.1f×\nbenchmark" % (v / eq), ha="center", va="center",
                    fontsize=8.5, color="white" if v > top * 0.12 else NAVY, fontweight="bold")
    ax.axhline(bud, color=AMBER, ls="--", lw=1.3)
    ax.text(3.42, bud + top * 0.015, "working budget %s USD\nat catalogue prices (D-044)" % usd(bud), ha="right", va="bottom", fontsize=8, color="#9A6500")
    ax.annotate("", xy=(1, pv["cat"] + top * 0.16), xytext=(0, full["cat"] * 0.80),
                arrowprops=dict(arrowstyle="->", color=SLATE, lw=1.2, connectionstyle="arc3,rad=-0.25"))
    ax.text(0.62, full["cat"] * 0.72, ("%+.0f %%" % (100 * (pv["cat"] / full["cat"] - 1))).replace("-", "−"), fontsize=10, color=NAVY, fontweight="bold")
    ax.set_xticks(range(4), labels, fontsize=9)
    ax.set_ylabel("USD per 75 kW module (BOM only)")
    ax.set_ylim(0, top * 1.22)
    fig.subplots_adjust(bottom=0.2)
    subtitle(ax, "PV-P75 bill of materials: earlier platform → cost-first → benchmark",
             "%.0f %% of the cost-first catalogue total is engineering estimate; nothing is quoted (bom/COST.md)" % (100 * pv["basis"]["estimate"] / pv["cat"]))
    return save(fig, "cost_journey", "bom/PV-P75*_costed_BOM.csv, gen/cost.py benchmark constants, DECISIONS.md D-044", "estimated - not quoted")


def fig_cost_blocks(pv):
    groups = sorted(pv["groups"].items(), key=lambda kv: kv[1]["price"] + kv[1]["class rule"] + kv[1]["estimate"])
    fig, ax = plt.subplots(figsize=(8.2, 4.9))
    left = [0.0] * len(groups)
    for basis, color, label in (("price", TEAL, "looked-up price"), ("class rule", LIGHT_TEAL, "chip-passive class rule"),
                                ("estimate", AMBER, "engineering estimate (no quote)")):
        vals = [g[basis] for _, g in groups]
        ax.barh(range(len(groups)), vals, left=left, color=color, height=0.62, label=label)
        left = [a + b for a, b in zip(left, vals)]
    for i, (_, g) in enumerate(groups):
        ax.plot([g["k5"]], [i], marker="|", ls="none", markersize=13, mew=2.2, color=NAVY, label="at 5,000 units" if i == 0 else None)
        ax.text(left[i] + pv["cat"] * 0.006, i, "  %s USD · %.0f %%" % (usd(left[i]), 100 * left[i] / pv["cat"]), va="center", fontsize=8.5, color=NAVY)
    ax.set_yticks(range(len(groups)), [n for n, _ in groups], fontsize=8.8)
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, max(left) * 1.28)
    ax.set_xlabel("USD at catalogue prices (tick: the same block at a 5,000-unit build)")
    ax.legend(loc="lower right", fontsize=8.5)
    fig.subplots_adjust(bottom=0.14)
    subtitle(ax, "PV-P75 cost by function block - %s USD at catalogue prices" % usd(pv["cat"]),
             "Groups assigned by rule in gen/cost.py (group_of); amber = engineering estimate without a quote")
    return save(fig, "cost_by_block_pv_p75", "bom/PV-P75_costed_BOM.csv (gen/cost.py)", "estimated - not quoted")


def fig_benchmark(costs, pcs, bm):
    def eqkw(m, kw):
        return bm["eq"](m, kw) / kw
    data = [  # label, catalogue USD/kW, 5k USD/kW, current-adjusted equivalent USD/kW or None, colour
        ("PV-P75 · cost-first, drawn boards", costs["PV-P75"]["cat"] / 75, costs["PV-P75"]["k5"] / 75, eqkw("PV-P75", 75), TEAL),
        ("PV-P100/110 · cost-first, at 100 kW", costs["PV-P100-110"]["cat"] / 100, costs["PV-P100-110"]["k5"] / 100, eqkw("PV-P100-110", 100), TEAL),
        ("PCS-P125 3-wire · design study, no boards", pcs["3-wire"]["cat"] / 125, pcs["3-wire"]["k5"] / 125, None, AMBER),
        ("PCS-P125 4-wire · design study, no boards", pcs["4-wire"]["cat"] / 125, pcs["4-wire"]["k5"] / 125, None, AMBER),
        ("DAB-D60 · earlier platform", costs["DAB-D60-FULL"]["cat"] / 60, costs["DAB-D60-FULL"]["k5"] / 60, eqkw("DAB-D60-FULL", 60), SLATE),
        ("PV-P75 · earlier platform", costs["PV-P75-FULL"]["cat"] / 75, costs["PV-P75-FULL"]["k5"] / 75, eqkw("PV-P75-FULL", 75), SLATE)][::-1]
    fig, ax = plt.subplots(figsize=(8.2, 4.9))
    h = 0.36
    for i, (label, cat, k5, eq, color) in enumerate(data):
        ax.barh(i + h / 2, cat, height=h, color=color, alpha=0.45)
        ax.barh(i - h / 2, k5, height=h, color=color)
        ax.text(cat + 0.6, i + h / 2, "%.1f" % cat, va="center", fontsize=8, color=NAVY)
        ax.text(k5 + 0.6, i - h / 2, "%.1f" % k5, va="center", fontsize=8, color=NAVY, fontweight="bold")
        if eq:
            ax.plot([eq], [i], marker="D", markersize=5.5, color=CORAL, zorder=5)
    ax.axvline(bm["price_kw"], color=CORAL, ls="--", lw=1.3)
    ax.axvline(bm["bom_kw"], color=CORAL, ls=":", lw=1.5)
    ax.set_yticks(range(len(data)), [d[0] for d in data], fontsize=8.8)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("BOM cost, USD per kW of rated power")
    handles = [Patch(facecolor="#B8C2CB", label="catalogue prices (light bar)"), Patch(facecolor=SLATE, label="5,000-unit build (dark bar)"),
               Line2D([], [], color=CORAL, ls="--", label="benchmark selling price %.2f USD/kW" % bm["price_kw"]),
               Line2D([], [], color=CORAL, ls=":", label="benchmark BOM at a %.0f %% BOM share: %.2f USD/kW" % (100 * bm["share"], bm["bom_kw"])),
               Line2D([], [], color=CORAL, marker="D", ls="", label="benchmark-equivalent BOM, adjusted for current (DC/DC)")]
    ax.legend(handles=handles, loc="upper right", fontsize=8)
    fig.subplots_adjust(bottom=0.14)
    subtitle(ax, "Cost per kW against the owner's benchmark (Megarevo 1500 V string PCS, 228 kW, 1,500 USD)",
             "Teal: drawn boards · amber: estimate without boards · slate: earlier platform. PCS current adjustment: ARCHITECTURE-PCS.md §12")
    return save(fig, "benchmark_usd_per_kw", "bom/*_costed_BOM.csv, sim/out/pcs_design/pcs_spec.json, gen/data/market_prices.csv",
                "estimated - not quoted; benchmark price stated by the owner")


def fig_parts(before, after, nb):
    fig, ax = plt.subplots(figsize=(8.2, 3.3))
    shades = {"before": ["#4C5B69", "#5B6B7A", "#6E7E8C", "#82909D", "#97A4AF", "#ABB6C0", "#C0C9D1"],
              "after": [TEAL, NAVY, "#7FD4CE"]}
    for row, (key, parts) in enumerate((("after", after), ("before", before))):
        module = {"before": "PV-P75-FULL", "after": "PV-P75"}[key]
        left, small = 0, []
        for (board, n), c in zip(sorted(parts.items(), key=lambda kv: (kv[0] == "chassis", -kv[1])), shades[key]):
            c = "#E3E8EC" if board == "chassis" else c
            ax.barh(row, n, left=left, color=c, edgecolor="white", linewidth=1.2, height=0.5)
            q = BA.MODULES[module].get(board, 1)
            name = "%s%s" % (board, " × %d" % q if q > 1 else "")
            if n >= 450:                                  # wide enough for a label inside
                ax.text(left + n / 2, row, "%s\n%s" % (name, usd(n)), ha="center", va="center", fontsize=7.4,
                        color=NAVY if c in ("#7FD4CE", "#C0C9D1", "#ABB6C0", "#E3E8EC") else "white")
            else:
                small.append("%s %s" % (name, usd(n)))
            left += n
        ax.text(left + 40, row, "%d boards · %s parts" % (nb[key], usd(left)), va="center", fontsize=9.5, fontweight="bold", color=NAVY)
        if small:
            ax.text(25, row - 0.36, "also: " + " · ".join(small), va="top", fontsize=7.4, color=SLATE)
    ax.set_yticks([0, 1], ["Cost-first\nPV-P75", "Earlier platform\nPV-P75-FULL"], fontsize=9)
    ax.grid(axis="y", visible=False)
    ax.set_ylim(-0.75, 1.4)
    ax.set_xlim(0, max(sum(before.values()), sum(after.values())) * 1.3)
    ax.set_xlabel("fitted parts per module (board parts + chassis items, DNP excluded)")
    fig.subplots_adjust(bottom=0.22)
    subtitle(ax, "Boards and parts of the 75 kW module, before and after the cost-first re-architecture",
             "Segments are boards; chassis = fans, busbars, harness and standoffs")
    return save(fig, "parts_and_boards", "bom/PV-P75_module_BOM.csv, bom/PV-P75-FULL_module_BOM.csv, gen/build_all.py MODULES", "counted from the generated BOMs")


VERDICTS = (("BETTER", TEAL, "better"), ("MEETS", NAVY, "meets"), ("BELOW", CORAL, "below"), ("PENDING", AMBER, "pending"), ("NOT ASSESSED", PALE, "not assessed"))


def megarevo():
    rr = rows("sim/out/compare_megarevo/comparison.csv")
    unknown = {r["verdict"] for r in rr} - {v for v, _, _ in VERDICTS}
    if unknown:
        sys.exit("unknown verdict(s) in comparison.csv: %s" % ", ".join(sorted(unknown)))
    rep = open(need("sim/out/compare_megarevo/report.md"), encoding="utf-8").read()
    sec = re.search(r"## Boards behind the PV-P75 column\n\n(.*?)\n\n", rep, re.S)
    boards = [ln.split("|")[1].strip() for ln in sec.group(1).splitlines()[2:]] if sec else []
    return rr, boards


def fig_scorecard(rr, boards):
    sections = list(dict.fromkeys(r["section"] for r in rr))
    count = collections.Counter((r["section"], r["verdict"]) for r in rr)
    total = collections.Counter(r["verdict"] for r in rr)
    fig, ax = plt.subplots(figsize=(8.2, 3.7))
    for i, s in enumerate(sections[::-1]):
        left = 0
        for v, color, _ in VERDICTS:
            n = count[(s, v)]
            if n:
                ax.barh(i, n, left=left, color=color, height=0.6, edgecolor="white", linewidth=1.2)
                ax.text(left + n / 2, i, str(n), ha="center", va="center", fontsize=8.5, color=NAVY if color == PALE else "white", fontweight="bold")
                left += n
    ax.set_yticks(range(len(sections)), sections[::-1], fontsize=9)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("published rows of the PMD-75-G3 table")
    ax.xaxis.get_major_locator().set_params(integer=True)
    fig.subplots_adjust(bottom=0.18)
    ax.legend(handles=[Patch(color=c, label="%s (%d)" % (lab, total[v])) for v, c, lab in VERDICTS if total[v] or v == "BELOW"],
              loc="lower right", fontsize=8.5)
    subtitle(ax, "PV-P75 against Megarevo PMD-75-G3: %d better · %d meets · %d below · %d not assessed" % (
        total["BETTER"], total["MEETS"], total["BELOW"], total["NOT ASSESSED"]),
        "Ours calculated / simulated, theirs published. Boards behind our column: %s" % (", ".join(boards) or "see report.md"))
    return save(fig, "megarevo_scorecard", "sim/out/compare_megarevo/comparison.csv (sim/compare_megarevo.py)", "calculated against published - not measured")


def fig_status(reps):
    REQ, ACF, ACP = "docs/requirements/REQUIREMENTS.md", "docs/requirements/ARCHITECTURE-COSTFIRST.md", "docs/requirements/ARCHITECTURE-PCS.md"
    stages = ["Require-\nments", "Archi-\ntecture", "Design\nstudy", "Schematic", "Build\nchecks", "Costed\nBOM", "Bench\ntest"]
    full_boards = list(BA.MODULES["PV-P75-FULL"])
    products = [  # name, boards, cells (state, label, evidence); None = from the board build reports
        ("PV-P75", ["PV-PWR", "PV-CTL"], [("done", "§2 + §7", REQ), ("done", "cost-first", ACF), ("done", "done", "sim/out/pv_design/cell_spec.json"),
                                          None, None, ("done", "drawn\nboards", "bom/PV-P75_costed_BOM.csv"), ("none", "not\nstarted", None)]),
        ("PV-P100/110", ["PV-PWR-4", "PV-CTL"], [("done", "§2 + §7", REQ), ("done", "cost-first", ACF), ("part", "thermal\nopen", "sim/out/pv_design/module_spec.json"),
                                                 None, None, ("done", "drawn\nboards", "bom/PV-P100-110_costed_BOM.csv"), ("none", "not\nstarted", None)]),
        ("PCS-P125", [], [("done", "§8", REQ), ("part", "revised\nD-053", ACP), ("part", "power\nstage only", "sim/out/pcs_design/pcs_spec.json"),
                          ("none", "not\nstarted", None), ("none", "—", None), ("part", "estimate", "sim/out/pcs_design/pcs_spec.json"), ("none", "not\nstarted", None)]),
        ("DAB-D60", ["DAB60"], [("done", "§3", REQ), ("part", "§16\noutline", ACF), ("done", "re-run,\nCN devices", "sim/out/dab_design/dab_spec.json"),
                                ("part", "rev B\nfrozen", None), None, ("part", "earlier\nplatform", "bom/DAB-D60-FULL_costed_BOM.csv"), ("none", "not\nstarted", None)]),
        ("Earlier platform", full_boards, [("done", "roadmap", "docs/requirements/00-roadmap-source.md"), ("done", "roadmap", "docs/requirements/00-roadmap-source.md"),
                                           ("done", "done", "sim/out/port_design/port_spec.json"), None, None,
                                           ("done", "done", "bom/PV-P75-FULL_costed_BOM.csv"), ("none", "not\nstarted", None)])]
    fill = {"done": (TEAL, "white"), "part": (AMBER, NAVY), "none": ("white", SLATE), "fail": (CORAL, "white")}
    fig, ax = plt.subplots(figsize=(8.2, 3.9))
    ax.axis("off")
    for i, (name, boards, cells) in enumerate(products):
        y = len(products) - 1 - i
        ax.text(-0.1, y + 0.4, name, ha="right", va="center", fontsize=9.5, fontweight="bold", color=NAVY)
        for j, cell in enumerate(cells):
            if cell is None:
                missing = [b for b in boards if b not in reps]
                if j == 3:
                    n = sum(BA.MODULES["PV-P75-FULL"].get(b, 1) for b in boards) if name == "Earlier platform" else len(boards)
                    cell = ("fail", "missing", None) if missing else ("done", "%d boards" % n, None)
                else:
                    ok = not missing and all(reps[b]["passed"] for b in boards)
                    cell = ("done", "all pass", None) if ok else ("fail", "FAILING", None)
            state, label, ev = cell
            if ev:
                need(ev)
            bg, fg = fill[state]
            ax.add_patch(FancyBboxPatch((j + 0.04, y + 0.08), 0.92, 0.64, boxstyle="round,pad=0,rounding_size=0.1",
                                        facecolor=bg, edgecolor=PALE if state == "none" else bg, linewidth=1))
            ax.text(j + 0.5, y + 0.4, label, ha="center", va="center", fontsize=7.2, color=fg, linespacing=1.15)
    for j, s in enumerate(stages):
        ax.text(j + 0.5, len(products) - 0.12, s, ha="center", va="bottom", fontsize=8, color=SLATE, fontweight="bold", linespacing=1.1)
    ax.set_xlim(-1.75, len(stages))
    ax.set_ylim(-0.6, len(products) + 0.55)
    fig.subplots_adjust(bottom=0.12)
    ax.legend(handles=[Patch(facecolor=TEAL, label="done"), Patch(facecolor=AMBER, label="partial / in study / frozen"),
                       Patch(facecolor="white", edgecolor=PALE, label="not started"), Patch(facecolor=CORAL, label="failing")],
              loc="lower center", ncol=4, fontsize=8, bbox_to_anchor=(0.5, -0.12))
    ax.set_title("Project status by product and stage", loc="left", x=0.0)
    return save(fig, "project_status", "hardware/*/outputs/*_report.json, docs/requirements/*.md, sim/out/*, bom/*_costed_BOM.csv",
                "nothing is bench-validated")


# ---------------------------------------------------------------------------------------------- generated blocks
def rel(page_dir, path):
    return os.path.relpath(os.path.join(REPO, path), page_dir).replace(os.sep, "/")


def block_cost_summary(page_dir, costs, pcs, bm):
    def ev(t):
        return "%.0f %% of catalogue on estimates · %.0f %% of 5k on published breaks" % (100 * t["basis"]["estimate"] / t["cat"], 100 * t["real5"] / t["k5"])
    p75, p100, f75, dab = costs["PV-P75"], costs["PV-P100-110"], costs["PV-P75-FULL"], costs["DAB-D60-FULL"]
    lines = ["| Product | What the figure is | kW | Catalogue USD | USD/kW | 5,000 units USD | USD/kW | Evidence | 5k ÷ benchmark-equivalent BOM |",
             "|---|---|---:|---:|---:|---:|---:|---|---:|",
             "| **PV-P75** | BOM of drawn boards (PV-PWR + PV-CTL) | 75 | **%s** | %.1f | **%s** | %.1f | %s | %.1f× (≈ %s USD) |" % (
                 usd(p75["cat"]), p75["cat"] / 75, usd(p75["k5"]), p75["k5"] / 75, ev(p75), p75["k5"] / bm["eq"]("PV-P75", 75), usd(bm["eq"]("PV-P75", 75))),
             "| **PV-P100/110** | BOM of drawn boards (PV-PWR-4 + PV-CTL) | 100 / 110 | %s | %.1f / %.1f | %s | %.1f / %.1f | %s | %.1f× / %.1f× |" % (
                 usd(p100["cat"]), p100["cat"] / 100, p100["cat"] / 110, usd(p100["k5"]), p100["k5"] / 100, p100["k5"] / 110, ev(p100),
                 p100["k5"] / bm["eq"]("PV-P100-110", 100), p100["k5"] / bm["eq"]("PV-P100-110", 110))]
    for key in ("3-wire", "4-wire"):
        t = pcs[key]
        lines.append("| **PCS-P125** | design study, %s (%s), design point D-060; the three-wire boards are drawn and cost more, see D-063 | 125 | %s | %.1f | %s | %.1f | %s | see note |" % (
            key, pcs["topology"].split(",")[0], usd(t["cat"]), t["cat"] / 125, usd(t["k5"]), t["k5"] / 125,
            "%.0f %% of 5k on published prices" % t["real"] if t["real"] is not None else "not stated for 4-wire"))
    for name, t, m, kw in (("PV-P75 earlier platform", f75, "PV-P75-FULL", 75), ("DAB-D60 earlier platform", dab, "DAB-D60-FULL", 60)):
        lines.append("| %s | BOM of drawn boards (%d boards) | %d | %s | %.1f | %s | %.1f | %s | %.1f× |" % (
            name, sum(BA.MODULES[m].values()), kw, usd(t["cat"]), t["cat"] / kw, usd(t["k5"]), t["k5"] / kw, ev(t), t["k5"] / bm["eq"](m, kw)))
    t = pcs["t-type"]
    still = " (still printed in bom/COST.md)" if "%.2f" % t["cat"] in open(need("bom/COST.md"), encoding="utf-8").read() else ""
    lines.append("| PCS-P125 | three-level T-type estimate, **withdrawn by D-053**%s | 125 | %s | %.1f | %s | %.1f | — | — |" % (
        still, usd(t["cat"]), t["cat"] / 125, usd(t["k5"]), t["k5"] / 125))
    lines += ["", "<sub>Generated by <a href=\"%s\">figures_overview.py</a> from <a href=\"%s\">bom/*_costed_BOM.csv</a>, "
              "<a href=\"%s\">pcs_spec.json</a> and <a href=\"%s\">costfirst_pcs_bom.csv</a>; benchmark arithmetic from <a href=\"%s\">gen/cost.py</a>. "
              "Do not edit between the markers.</sub>" % (rel(page_dir, "docs/assets/figures_overview.py"), rel(page_dir, "bom/COST.md"),
                                                          rel(page_dir, "sim/out/pcs_design/pcs_spec.json"), rel(page_dir, "gen/data/costfirst_pcs_bom.csv"),
                                                          rel(page_dir, "gen/cost.py"))]
    return "\n".join(lines)


def block_board_status(page_dir, reps):
    role = {}
    for m, boards in BA.MODULES.items():
        for b in boards:
            b = {"PV-CTL-P75": "PV-CTL", "AUX-HV_DAB": "AUX-HV"}.get(b, b)
            role.setdefault(b, "cost-first module" if m in BA.PHASES else "earlier platform")
    for b in BA.VARIANTS.get("PV-PORT", []):
        role.setdefault(b, "earlier platform")
    for b in BA.FROZEN:
        role[b] = "frozen: rev B outputs kept"
    order = {"cost-first module": 0, "earlier platform": 1}
    lines = ["| Board | Role | Rev | Sheets | Drawn symbols | Nets | Build checks | Schematic |", "|---|---|---|---:|---:|---:|---|---|"]
    for p, r in sorted(reps.items(), key=lambda kv: (order.get(role.get(kv[0], ""), 2 if kv[0] in BA.FROZEN else 3), kv[0])):
        checks = r["checks"]
        n_ok = sum(1 for c in checks if c.get("result") == "pass")
        pdf = "hardware/%s/outputs/%s_schematic.pdf" % (p, p)
        lines.append("| `%s` | %s | %s | %d | %s | %s | %s | %s |" % (
            p, role.get(p, "verification / reference board"), r["rev"], r["sheets"], usd(r["parts"]), usd(r["nets"]),
            "pass (%d/%d)" % (n_ok, len(checks)) if r["passed"] else "**FAILING** (%d/%d)" % (n_ok, len(checks)),
            "[PDF](%s)" % rel(page_dir, pdf) if os.path.exists(os.path.join(REPO, pdf)) else "—"))
    lines += ["", "<sub>Generated by <a href=\"%s\">figures_overview.py</a> from <code>hardware/*/outputs/*_report.json</code> and the module lists of "
              "<a href=\"%s\">gen/build_all.py</a>. Drawn symbols include parts marked DNP. Do not edit between the markers.</sub>" % (
                  rel(page_dir, "docs/assets/figures_overview.py"), rel(page_dir, "gen/build_all.py"))]
    return "\n".join(lines)


def block_megarevo_score(page_dir, rr):
    total = collections.Counter(r["verdict"] for r in rr)
    color = {"BETTER": "00A99D", "MEETS": "0B1F33", "BELOW": "E4572E", "PENDING": "F2A007", "NOT ASSESSED": "5B6B7A"}
    badges = ["![%s %d](https://img.shields.io/badge/%s-%d-%s?style=flat-square)" % (lab, total[v], lab.replace(" ", "%20"), total[v], color[v])
              for v, _, lab in VERDICTS if total[v] or v == "BELOW"]
    return " ".join(badges) + " &nbsp;of %d published rows" % len(rr)


def block_megarevo_table(page_dir, rr):
    mark = {"BETTER": "▲ better", "MEETS": "● meets", "BELOW": "▼ **below**", "PENDING": "◌ pending", "NOT ASSESSED": "○ not assessed"}

    def cell(x):
        return x.replace("|", "\\|").strip()

    def evidence(text):
        out = []
        for tok in [t.strip() for t in text.split(",") if t.strip()]:
            path, _, rest = tok.partition(" ")
            out.append("[%s](%s)%s" % (os.path.basename(path), rel(page_dir, path), " " + rest if rest else "")
                       if os.path.exists(os.path.join(REPO, path)) else "`%s`" % tok)
        return "<br/>".join(out)
    lines = ["| Parameter | Megarevo PMD-75-G3 · published | PV-P75 · calculated or simulated | Verdict | Evidence |", "|---|---|---|---|---|"]
    section = None
    for r in rr:
        if r["section"] != section:
            section = r["section"]
            lines.append("| **%s** | | | | |" % section)
        lines.append("| %s | %s | %s | %s | %s |" % (cell(r["parameter"]), cell(r["megarevo_pmd_75_g3_published"]), cell(r["pv_p75_calculated"]),
                                                    mark[r["verdict"]], evidence(r["evidence"])))
    lines += ["", "<sub>Generated by <a href=\"%s\">figures_overview.py</a> from <a href=\"%s\">comparison.csv</a> "
              "(written by <a href=\"%s\">sim/compare_megarevo.py</a>). Do not edit between the markers.</sub>" % (
                  rel(page_dir, "docs/assets/figures_overview.py"), rel(page_dir, "sim/out/compare_megarevo/comparison.csv"),
                  rel(page_dir, "sim/compare_megarevo.py"))]
    return "\n".join(lines)


def write_blocks(gens):
    for page, names in PAGES.items():
        path = need(page)
        text = open(path, encoding="utf-8").read()
        for name in names:
            pat = re.compile(r"(<!-- BEGIN:%s -->).*?(<!-- END:%s -->)" % (name, name), re.S)
            if len(pat.findall(text)) != 1:
                sys.exit("%s: expected exactly one <!-- BEGIN:%s --> ... <!-- END:%s --> pair" % (page, name, name))
            body = gens[name](os.path.dirname(path)).strip("\n")
            text = pat.sub(lambda m: m.group(1) + "\n\n" + body + "\n\n" + m.group(2), text)
        open(path, "w", encoding="utf-8").write(text)


def self_check(images):
    bad = [p for p in images if not os.path.exists(os.path.join(REPO, p)) or os.path.getsize(os.path.join(REPO, p)) < 1000]
    for page, names in PAGES.items():
        text = open(os.path.join(REPO, page), encoding="utf-8").read()
        for n in names:
            b, e = text.find("<!-- BEGIN:%s -->" % n), text.find("<!-- END:%s -->" % n)
            if b < 0 or e < b or text.count("<!-- BEGIN:%s -->" % n) != 1:
                bad.append("%s: marker pair %s" % (page, n))
    if bad:
        sys.exit("SELF-CHECK FAILED:\n  " + "\n  ".join(bad))
    print("self-check: %d images written, %d marker pairs in %d pages - OK" % (len(images), sum(map(len, PAGES.values())), len(PAGES)))


def main():
    costs = {m: module_cost(m) for m in ("PV-P75", "PV-P100-110", "PV-P75-FULL", "DAB-D60-FULL")}
    check_against_cost_md(costs)
    bm, pcs, reps = benchmark(), pcs_costs(), board_reports()
    rr, boards = megarevo()
    nb = {"before": sum(BA.MODULES["PV-P75-FULL"].values()), "after": sum(BA.MODULES["PV-P75"].values())}
    images = [fig_cost_journey(costs["PV-P75"], costs["PV-P75-FULL"], bm["eq"]("PV-P75", 75), budget(), nb["before"]),
              fig_cost_blocks(costs["PV-P75"]),
              fig_benchmark(costs, pcs, bm),
              fig_parts(parts_by_board("PV-P75-FULL"), parts_by_board("PV-P75"), nb),
              fig_scorecard(rr, boards),
              fig_status(reps)]
    write_blocks({"cost-summary": lambda d: block_cost_summary(d, costs, pcs, bm),
                  "board-status": lambda d: block_board_status(d, reps),
                  "megarevo-score": lambda d: block_megarevo_score(d, rr),
                  "megarevo-table": lambda d: block_megarevo_table(d, rr)})
    for p in images:
        print("wrote", p)
    self_check(images)


if __name__ == "__main__":
    main()
