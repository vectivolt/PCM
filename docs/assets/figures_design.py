"""Charts for the technical design pages docs/guide/02-06, 08-10 (STYLE.md). Every number is read at run time from a
data file in the repository; a missing file or a changed format stops the script with a message naming the file.

Usage: .venv/bin/python docs/assets/figures_design.py      -> docs/assets/img/design_*.png
"""
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter

from style import *  # noqa: F401,F403  (REPO, IMG, palette, save, plt)


def need(rel):
    path = os.path.join(REPO, rel)
    if not os.path.isfile(path):
        sys.exit("figures_design: missing data file %s" % rel)
    return path


def rows(rel):
    return list(csv.DictReader(open(need(rel), newline="", encoding="utf-8")))


def js(rel):
    return json.load(open(need(rel), encoding="utf-8"))


def text(rel):
    return open(need(rel), encoding="utf-8").read()


def grab(pattern, s, rel):
    m = re.search(pattern, s)
    if not m:
        sys.exit("figures_design: pattern %r not found in %s" % (pattern, rel))
    return m


def out(fig, name, source, note="calculated or estimated - not measured", bottom=None, left=None, top=0.9):
    """save() after making room for its source footer (tight layout, or fixed margins when a legend sits below)."""
    if bottom is None:
        fig.tight_layout(rect=(0, 0.06, 1, 1))
    else:
        fig.subplots_adjust(bottom=bottom, left=left or 0.15, top=top)
    return save(fig, name, source, note)


def hbar_labels(ax, labels):
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)


# ------------------------------------------------------------------------------------------------ 03 power stage
def fig_gate0b():
    src = "sim/out/pv_tradeoff/gate0b.csv"
    R = rows(src)
    floor = 98.6
    fig, ax = plt.subplots(figsize=(8, 4.3))
    for three, col, lab in ((False, TEAL, "two-level, 1700 V SiC"), (True, NAVY, "three-level flying capacitor, 1200 V SiC")):
        for ok in (True, False):
            pts = [r for r in R if r["topo"].startswith("3") == three and (r["meets"] == "True") == ok and r["key"] != "REF"]
            ax.scatter([float(r["cost_usd"]) for r in pts], [max(float(r["eta_corner_min_pct"]), floor) for r in pts],
                       s=48, marker="o" if ok else "v", facecolor=col if ok else "white", edgecolor=col, linewidth=1.4,
                       zorder=3, label=lab + ("" if ok else " - fails a rule (< %.1f %% drawn at %.1f %%)" % (floor, floor)))
    ref = next(r for r in R if r["key"] == "REF")
    ax.scatter(float(ref["cost_usd"]), float(ref["eta_corner_min_pct"]), s=60, marker="s", color=SLATE, zorder=3,
               label="reference: 2 x Microchip MSC035SMA170B4")
    best3 = min((r for r in R if r["topo"].startswith("3") and r["meets"] == "True"), key=lambda r: float(r["cost_usd"]))
    for key, txt, dx, dy in (("SC40x2", "chosen: 2 x SG2M040170HJ, 32 kHz", 62, 0.04),
                             (best3["key"], "best three-level", 10, -0.16)):
        r = next(r for r in R if r["key"] == key)
        x, y = float(r["cost_usd"]), float(r["eta_corner_min_pct"])
        ax.annotate("%s\n%.0f USD, %.2f %%, Tj %.0f C" % (txt, x, y, float(r["tj_max_C"])), (x, y), (x + dx, y + dy),
                    fontsize=7.5, color=NAVY, arrowprops=dict(arrowstyle="-", color=SLATE, lw=0.8))
    ax.axhline(99.0, color=AMBER, ls="--", lw=1)
    ax.text(ax.get_xlim()[1], 99.0, "rule: >= 99.0 % at every full-power corner ", ha="right", va="bottom", fontsize=7,
            color=AMBER)
    ax.set_xlabel("power-stage cost per phase (cell) [USD, catalogue prices and estimates]")
    ax.set_ylabel("lowest full-power corner efficiency [%]")
    ax.set_title("Gate-0b trade study: cost against worst-corner efficiency (%d device sets)" % len(R))
    ax.legend(fontsize=6.8, ncol=2, loc="upper center", bbox_to_anchor=(0.45, -0.17))
    return out(fig, "design_gate0b_tradeoff", src, "calculated; device prices partly estimates", bottom=0.36, left=0.11)


def fig_phase_losses():
    src = "sim/out/pv_design/envelope.csv"
    R = [r for r in rows(src) if float(r["load"]) == 1.0]
    pts = [(550, 950), (950, 550), (550, 550), (950, 950), (1000, 500), (500, 1000)]
    sel = []
    for va, vb in pts:
        m = [r for r in R if float(r["va"]) == va and float(r["vb"]) == vb]
        if not m:
            sys.exit("figures_design: %s has no full-load row %d -> %d V" % (src, va, vb))
        sel.append(m[0])
    groups = [("conduction", ["cond"]), ("switching", ["sw"]), ("dead time (body diode)", ["dead"]),
              ("inductor core", ["core"]), ("inductor copper", ["cu"]),
              ("capacitors, gate, damper, bias, misc.", ["cap", "gate", "damp", "aux", "misc"])]
    fig, ax = plt.subplots(figsize=(8, 3.9))
    left = [0.0] * len(sel)
    for (name, keys), col in zip(groups, SERIES):
        w = [sum(float(r[k]) for k in keys) for r in sel]
        ax.barh(range(len(sel)), w, left=left, color=col, height=0.62, label=name)
        left = [a + b for a, b in zip(left, w)]
    for i, r in enumerate(sel):
        ax.text(left[i] + 3, i, "%.2f %%  %s, %.1f kW" % (100 * float(r["eff"]), r["mode"], float(r["p_W"]) / 1e3),
                va="center", fontsize=8, color=NAVY)
    hbar_labels(ax, ["%d -> %d V" % (float(r["va"]), float(r["vb"])) for r in sel])
    ax.set_xlim(0, max(left) * 1.5)
    ax.set_xlabel("loss of one phase at full power, 45 C inlet [W]")
    ax.set_title("Where one phase loses its power (primary device, per-phase heatsink model)")
    ax.legend(fontsize=7, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.2))
    return out(fig, "design_phase_losses", src, "calculated by sim/pv_design.py - not measured", bottom=0.36, left=0.14)


# ------------------------------------------------------------------------------------------------ 02 architecture
def fig_supply_budget():
    srcs = [("PV-P75", "hardware/PV-PWR/outputs/PV-PWR_design_check.txt"),
            ("PV-P100/110", "hardware/PV-PWR-4/outputs/PV-PWR-4_design_check.txt")]
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.0), sharey=True)
    for ax, (name, rel) in zip(axes, srcs):
        s = text(rel)
        conv = float(grab(r"5 V / 3\.3 V converters ([\d.]+) W", s, rel)[1])
        n, coil = grab(r"(\d) contactors held on the economiser at ([\d.]+) W max each", s, rel).groups()
        live, live_r = map(float, grab(r"= ([\d.]+) W <= aux live rating ([\d.]+) W", s, rel).groups())
        selv, selv_r = map(float, grab(r"SELV fans \+ logic ([\d.]+) W <= ([\d.]+) W", s, rel).groups())
        tot, tot_r = map(float, grab(r"total ([\d.]+) W <= ([\d.]+) W", s, rel).groups())
        coils = int(n) * float(coil)
        ax.barh(0, conv, color=NAVY, height=0.5, label="5 V / 3.3 V converters (gate bias, logic)")
        ax.barh(0, coils, left=conv, color=AMBER, height=0.5, label="contactor coils held (economiser)")
        ax.barh(1, selv, color=TEAL, height=0.5, label="fans + SELV logic")
        for y, used, rating in ((0, live, live_r), (1, selv, selv_r)):
            ax.plot([rating, rating], [y - 0.34, y + 0.34], color=CORAL, lw=2)
            ax.text(rating + 0.8, y, "%.1f of %.1f W" % (used, rating), va="center", fontsize=7.5, color=NAVY)
        ax.set_title("%s: %.1f of %.1f W in total" % (name, tot, tot_r), fontsize=9.5)
        ax.set_xlim(0, max(selv_r, live_r) * 1.45)
        ax.set_xlabel("continuous load, maxima [W]")
    hbar_labels(axes[0], ["live 24 V\n(BUS- side)", "SELV 24 V\n(reinforced winding)"])
    axes[0].legend(fontsize=7, loc="upper center", bbox_to_anchor=(1.05, -0.32), ncol=3)
    fig.suptitle("Auxiliary flyback: load against the winding ratings (red mark = rating)", x=0.01, ha="left",
                 fontweight="bold", fontsize=11, color=NAVY)
    return out(fig, "design_supply_budget", srcs[0][1] + ", PV-PWR-4_design_check.txt", "calculated maxima - not measured",
               bottom=0.38, left=0.15, top=0.78)


# ------------------------------------------------------------------------------------------------ 04 protection
def fig_trip_timing():
    rel = "hardware/PV-CTL/outputs/PV-CTL_design_check.txt"
    s = text(rel)
    if "Trip table (as built):" not in s:
        sys.exit("figures_design: no trip table in %s" % rel)
    block = s.split("Trip table (as built):")[1].split("Firmware on top")[0]
    unit = {"ns": 1e-3, "us": 1.0, "ms": 1e3}
    data = []
    for line in block.strip().splitlines():
        cols = re.split(r"\s{2,}", line.strip())
        c = {k: next((x for x in cols if x.startswith(k)), "") for k in ("band", "response", "requirement")}
        m1 = re.search(r"response ([\d.]+) (ns|us|ms)", c["response"])
        m2 = re.search(r"<= ([\d.]+) (ns|us|ms)", c["requirement"])
        if m1 and m2:
            data.append((re.sub(r" low$", "", cols[0]), float(m1[1]) * unit[m1[2]], float(m2[1]) * unit[m2[2]],
                         c["band"].replace("band ", "")))
    if len(data) < 5:
        sys.exit("figures_design: trip table format changed in %s" % rel)
    fig, ax = plt.subplots(figsize=(8, 3.6))
    y = range(len(data))
    ax.barh(y, [d[1] for d in data], color=TEAL, height=0.55, label="as built (worst case)")
    ax.scatter([d[2] for d in data], y, marker="|", s=420, color=CORAL, linewidth=2.5, zorder=3, label="requirement")
    for i, d in enumerate(data):
        ax.text(d[2] * 1.25, i, "x%.1f margin   band %s" % (d[2] / d[1], d[3]), va="center", fontsize=7.5, color=NAVY)
    hbar_labels(ax, [d[0] for d in data])
    ax.set_xscale("log")
    ax.set_xlim(min(d[1] for d in data) / 2, max(d[2] for d in data) * 60)
    ax.set_xlabel("time from threshold crossing to gates off / latch set [us, log scale]")
    ax.set_title("Hardware trips on PV-CTL: response against requirement")
    ax.legend(fontsize=7.5, loc="upper right")
    return out(fig, "design_trip_timing", rel, "calculated worst case - not measured")


# ------------------------------------------------------------------------------------------------ 05 control
def fig_controller_resources():
    rel = "hardware/PV-CTL/outputs/PV-CTL_design_check.txt"
    s = text(rel)
    pwm = re.findall(r"PWM1-(\d+) on GPIO0-\d+ = ePWM1-(\d+) A/B", s)
    if len(pwm) < 2:
        sys.exit("figures_design: PWM pin lines not found in %s" % rel)
    pwm_total = 2 * max(int(m[1]) for m in pwm)
    items = [("ePWM outputs, PV-P75", int(pwm[0][0]), pwm_total), ("ePWM outputs, PV-P100/110", int(pwm[1][0]), pwm_total)]
    items.append(("ADC analog pins",) + tuple(map(int, grab(r"(\d+) of the (\d+) analog pins", s, rel).groups())))
    items.append(("CMPSS windows", int(grab(r"CMPSS1-(\d)", s, rel)[1]), int(grab(r"CMPSS1-(\d)", s, rel)[1])))
    items.append(("GPIO pins",) + tuple(map(int, grab(r"(\d+) of (\d+) GPIO pins used", s, rel).groups())))
    items.append(("eCAP (fan PWM, 2 tachs)",) + tuple(map(int, grab(r"eCAP2/3 capture \((\d+) of (\d+)\)", s, rel).groups())))
    items.append(("eQEP (third tach)",) + tuple(map(int, grab(r"eQEP1 \((\d+) of (\d+)\)", s, rel).groups())))
    items.append(("ADC conversion time", int(grab(r"= (\d+) % \(sampling plan", s, rel)[1]), 100))
    fig, ax = plt.subplots(figsize=(8, 3.4))
    frac = [100.0 * u / t for _, u, t in items]
    ax.barh(range(len(items)), [100] * len(items), color=MIST, height=0.6)
    ax.barh(range(len(items)), frac, color=[CORAL if f >= 99.9 else TEAL for f in frac], height=0.6)
    for i, (name, u, t) in enumerate(items):
        ax.text(101, i, ("%d %%" % u) if name.startswith("ADC conv") else "%d of %d" % (u, t), va="center", fontsize=8,
                color=NAVY)
    hbar_labels(ax, [it[0] for it in items])
    ax.set_xlim(0, 118)
    ax.set_xlabel("share of the F280039C resource in use [%]  (red = fully used)")
    ax.set_title("Controller resource use as drawn (TI F280039C, 100-pin)")
    return out(fig, "design_controller_resources", rel, "from the drawn pin plan")


# ------------------------------------------------------------------------------------------------ 06 magnetics
PART_LABEL = {"pv_inductor": "PV inductor\n(Kool Mu E-core hand-off)", "dab_transformer": "DAB transformer\n(PM 114/93 hand-off)",
              "dab_series_inductor": "DAB series inductor\n(hand-off)", "aux_hv_transformer": "AUX-HV flyback\n(ETD 29)",
              "port_cm_choke": "Port CM choke\n(spec only)"}


def fig_magnetics_threeway():
    rel, rep = "sim/out/magnetics/magnetics_check.json", "sim/out/magnetics/report.md"
    C = js(rel)["comparison"]
    b = grab(r"loss (\d+) %, L and B (\d+) %, leakage (\d+) %", text(rep), rep).groups()
    classes = [("L", "L, ripple, R", float(b[1])), ("B", "flux", float(b[1])),
               ("loss", "loss, temp.", float(b[0])), ("leak", "leakage", float(b[2]))]
    src = {"d_des": "designer", "d_om": "om"}   # a figure the designer or the tool did not state (0 / None) is skipped
    has = lambda r, key: r[key] is not None and r[src[key]] not in (None, 0, 0.0)
    parts = [p for p in PART_LABEL if p in C and any(has(r, "d_des") or has(r, "d_om") for r in C[p] if r["band"] != "spec")]
    if len(parts) < 4:
        sys.exit("figures_design: magnetics comparison incomplete in %s" % rel)
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.4), sharey=True)
    for ax, key, title in ((axes[0], "d_des", "designer against own calculation"),
                           (axes[1], "d_om", "OpenMagnetics against own calculation")):
        for i, p in enumerate(parts):
            for j, (cls, _, band) in enumerate(classes):
                d = [r[key] for r in C[p] if r["band"] == cls and has(r, key)]
                if not d:
                    col, txt = MIST, "-"
                else:
                    w = max(d, key=abs) * 100
                    col, txt = (TEAL if abs(w) <= band else CORAL), "%+.0f %%" % w
                ax.add_patch(plt.Rectangle((j, i), 0.96, 0.92, color=col))
                ax.text(j + 0.48, i + 0.46, txt, ha="center", va="center", fontsize=8,
                        color="white" if col != MIST else SLATE, fontweight="bold")
        ax.set_xlim(0, len(classes))
        ax.set_ylim(len(parts), 0)
        ax.set_xticks([j + 0.48 for j in range(len(classes))])
        ax.set_xticklabels(["%s\n+/-%.0f %%" % (c[1], c[2]) for c in classes], fontsize=7.5)
        ax.set_yticks([i + 0.46 for i in range(len(parts))])
        ax.set_yticklabels([PART_LABEL[p] for p in parts], fontsize=7.5)
        ax.grid(False)
        ax.set_title(title, fontsize=9.5)
        for sp in ax.spines.values():
            sp.set_visible(False)
    fig.suptitle("Magnetics, first verification: worst deviation per quantity class (teal inside band, coral outside)",
                 x=0.01, ha="left", fontweight="bold", fontsize=10.5, color=NAVY)
    return out(fig, "design_magnetics_threeway", rel, "calculated - nothing measured")


def fig_magnetics_checks():
    rel = "sim/out/magnetics/magnetics_check.json"
    checks = js(rel)["checks"]
    names = {"pv": "PV inductor", "dab": "DAB transformer", "lser": "DAB series inductor", "aux": "AUX-HV transformer (30 W)",
             "aux75": "AUX-T1 transformer (75 W)", "bias": "gate-bias transformer", "cmc": "port CM ring / choke",
             "model": "model self-tests", "om": "OpenMagnetics ran"}
    cnt = Counter((c["id"].split("_")[0], c["ok"]) for c in checks)
    groups = [g for g in names if cnt[(g, True)] + cnt[(g, False)]]
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ok = [cnt[(g, True)] for g in groups]
    bad = [cnt[(g, False)] for g in groups]
    ax.barh(range(len(groups)), ok, color=TEAL, height=0.6, label="pass")
    ax.barh(range(len(groups)), bad, left=ok, color=CORAL, height=0.6, label="fail (known, tracked as MG-15 / MG-16)")
    for i, (a, f) in enumerate(zip(ok, bad)):
        ax.text(a + f + 0.2, i, "%d / %d" % (a, a + f), va="center", fontsize=8, color=NAVY)
    hbar_labels(ax, [names[g] for g in groups])
    ax.set_xlabel("number of checks")
    ax.set_title("sim/magnetics.py self-checks: %d of %d pass" % (sum(ok), len(checks)))
    ax.legend(fontsize=7.5, loc="lower right")
    return out(fig, "design_magnetics_checks", rel, "calculated checks - nothing measured")


def fig_pv_inductor_options():
    rel = "sim/out/magnetics/design_pv_inductor.json"
    d = js(rel)
    opts = d["options"]
    chosen = [o for o in opts if o["conductor"].split(",")[0] in d["construction"]]
    if len(chosen) != 1:
        sys.exit("figures_design: cannot tell the chosen PV inductor option in %s" % rel)
    lim = d["thermal"]["T_hotspot_max_C"]
    fig, ax = plt.subplots(figsize=(8, 3.8))
    cheap = sorted((o for o in opts if o["cost_usd"] < 50), key=lambda o: -o["P_total_W"])   # crowded corner: fan out
    for o in opts:
        c = TEAL if o is chosen[0] else (CORAL if o["T_hotspot_C"] > lim else NAVY)
        ax.scatter(o["cost_usd"], o["P_total_W"], s=70, color=c, zorder=3)
        txt = "%s: %.1f W, %.0f C, %.1f USD%s" % (o["option"], o["P_total_W"], o["T_hotspot_C"], o["cost_usd"],
                                                  "  (design)" if o is chosen[0] else "")
        if o in cheap:
            xy = (55, cheap[0]["P_total_W"] + 1 - 4.5 * cheap.index(o))
            ax.annotate(txt, (o["cost_usd"], o["P_total_W"]), xy, fontsize=7.5, color=c, va="center",
                        arrowprops=dict(arrowstyle="-", color=SLATE, lw=0.7))
        else:
            ax.annotate(txt, (o["cost_usd"], o["P_total_W"]), (6, -4), textcoords="offset points", fontsize=7.5, color=c)
    ax.set_xlim(min(o["cost_usd"] for o in opts) - 5, max(o["cost_usd"] for o in opts) + 45)
    ax.set_xlabel("cost per inductor [USD, estimate - no quote]")
    ax.set_ylabel("worst-point loss [W]")
    ax.set_title("PV inductor rev %s: five constructions on the same core stack (hot-spot limit %.0f C)"
                 % (d["revision"], lim))
    return out(fig, "design_pv_inductor_options", rel, "calculated; costs are estimates")


# ------------------------------------------------------------------------------------------------ 08 verification
REVIEWS = [("review_ctrl_sys.csv", "Control card + system I/O (CSR)"), ("review_pvcell_gdrv.csv", "PV cell + gate drive (PVR)"),
           ("review_port_auxhv.csv", "Port + AUX-HV (PA)"), ("integration_findings.csv", "Integration (INT)"),
           ("review_dab60.csv", "DAB60 board (DR)"), ("review_insulation.csv", "Insulation (IC)"),
           ("review_magnetics.csv", "Magnetics (MG)"), ("review_pcm.csv", "Independent PCM review of 033d8d8 (PCM)")]


def fig_reviews():
    sev = ["critical", "major", "minor"]
    data = []
    for f, name in REVIEWS:
        R = rows("gen/data/" + f)
        c = Counter(r["severity"] for r in R)
        st = Counter(r["status"] for r in R) if "status" in R[0] else None
        data.append((name, [c[s] for s in sev], st))
    fig, ax = plt.subplots(figsize=(8, 3.6))
    left = [0] * len(data)
    for s, col in zip(sev, (CORAL, AMBER, SLATE)):
        w = [d[1][sev.index(s)] for d in data]
        ax.barh(range(len(data)), w, left=left, color=col, height=0.6, label=s)
        left = [a + b for a, b in zip(left, w)]
    for i, d in enumerate(data):
        note = "%d findings" % sum(d[1])
        if d[2]:
            note += "  - %d closed by calculation, %d open" % (sum(v for k, v in d[2].items() if k.startswith("closed")),
                                                             d[2]["open"])
        ax.text(left[i] + 0.4, i, note, va="center", fontsize=7.5, color=NAVY)
    hbar_labels(ax, [d[0] for d in data])
    ax.set_xlim(0, max(left) * 2.0)
    ax.set_xlabel("findings")
    ax.set_title("Independent design reviews: %d findings by severity" % sum(left))
    ax.legend(fontsize=7.5, loc="lower right")
    return out(fig, "design_review_findings", "gen/data/review_*.csv, integration_findings.csv", "review records")


def fig_audits():
    pin_rel, bar_rel = "gen/data/pin_audit_report.csv", "sim/out/insulation/barrier_audit.csv"
    P = rows(pin_rel)
    pc = Counter(r["result"] for r in P)
    need("gen/data/temp_audit.csv")
    log = subprocess.run([sys.executable, need("gen/temp_audit.py")], capture_output=True, text=True, cwd=REPO).stdout
    t = grab(r"temp audit: (\d+) parts; (\d+) ok; (\d+) cold-limited; (\d+) hot-limited; (\d+) unknown; (\d+) not audited",
             log, "gen/temp_audit.py output").groups()
    B = rows(bar_rel)
    fig, axes = plt.subplots(1, 3, figsize=(8, 3.0), gridspec_kw=dict(width_ratios=[1, 1.2, 1.5]))
    ax = axes[0]
    keys = sorted(pc, key=lambda k: -pc[k])
    ax.bar(range(len(keys)), [pc[k] for k in keys], color=[TEAL if k == "PASS" else AMBER for k in keys], width=0.6)
    for i, k in enumerate(keys):
        ax.text(i, pc[k] + 2, str(pc[k]), ha="center", fontsize=8, color=NAVY)
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels([k.lower().replace("not auditable", "not\nauditable") for k in keys], fontsize=7.5)
    ax.set_title("Pin audit: %d parts,\n%d pins" % (len(P), sum(int(r["pins"]) for r in P)), fontsize=9)
    ax = axes[1]
    lab = ["ok", "cold-\nlimited", "hot-\nlimited", "unknown", "not\naudited"]
    val = list(map(int, t[1:]))
    ax.bar(range(5), val, color=[TEAL, CORAL, CORAL, AMBER, SLATE], width=0.6)
    for i, v in enumerate(val):
        ax.text(i, v + 2, str(v), ha="center", fontsize=8, color=NAVY)
    ax.set_xticks(range(5))
    ax.set_xticklabels(lab, fontsize=7)
    ax.set_title("Temperature audit (-30 / +85 C):\n%s orderable parts" % t[0], fontsize=9)
    ax = axes[2]
    verdicts = ["PASS", "PASS WITH CONDITION", "FAIL"]
    alts = ["2000m", "3000m", "4000m"]
    bottom = [0] * 3
    for v, col in zip(verdicts, (TEAL, AMBER, CORAL)):
        h = [sum(1 for r in B if r["verdict_" + a] == v) for a in alts]
        ax.bar(range(3), h, bottom=bottom, color=col, width=0.6, label=v.lower())
        bottom = [x + y for x, y in zip(bottom, h)]
    ax.set_xticks(range(3))
    ax.set_xticklabels([a.replace("m", " m") for a in alts], fontsize=7.5)
    ax.set_title("Insulation barrier audit,\nearlier platform: %d rows" % len(B), fontsize=9)
    ax.set_ylim(0, len(B) * 1.35)
    ax.legend(fontsize=6.3, loc="upper center", ncol=3, columnspacing=0.8, handlelength=1.2)
    return out(fig, "design_audits", "%s, gen/temp_audit.py, %s" % (pin_rel, bar_rel), "audit scripts - not measured")


# ------------------------------------------------------------------------------------------------ 09 PCS, 10 DAB
def fig_pcs_screen():
    rel = "sim/out/pcs_design/pcs_spec.json"
    S = js(rel)["topology_screen"]
    C, choice, rule = S["candidates"], S["choice"], 0.67
    rep = text("sim/out/pcs_design/report.md")
    infeasible = {c["name"] for c in C if re.search(re.escape(c["name"]) + r"[^|\n]*NOT FEASIBLE", rep)}
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for c in C:
        ok = c["max_V_over_rating_950V"] <= rule
        mk = "*" if c["name"] == choice else ("x" if c["name"] in infeasible else "o")
        ax.scatter(c["usd_5k"], c["loss_125kW_900V_W"], s=150 if mk == "*" else 46, marker=mk,
                   color=TEAL if ok else CORAL, zorder=3)
        ax.annotate(c["name"].split(" - ")[0].replace(" (", "\n("), (c["usd_5k"], c["loss_125kW_900V_W"]), (5, 3),
                    textcoords="offset points", fontsize=6.3, color=NAVY if c["name"] != choice else TEAL)
    ax.scatter([], [], color=TEAL, label="every device <= %.2f of its rating at 950 V" % rule)
    ax.scatter([], [], color=CORAL, label="above %.2f of rating at 950 V (cosmic-ray rule fails)" % rule)
    ax.scatter([], [], marker="x", color=SLATE, label="one module per phase: overheats (not feasible)")
    ax.set_xlabel("screen-scope cost at 5,000 units [USD: devices, pads, gate drive, LCL, DC link]")
    ax.set_ylabel("device loss at 125 kW, 900 V DC [W]")
    ax.set_title("PCS-P125 topology screen (%d candidates); star = choice" % len(C))
    ax.legend(fontsize=7, loc="center right", bbox_to_anchor=(1.0, 0.36))
    return out(fig, "design_pcs_screen", rel, "calculated; prices mostly assumed factors")


def fig_dab_devices():
    rel = "sim/out/dab_design/device_options.csv"
    R = rows(rel)
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.8), sharey=True)
    names = ["%s (%s)" % (r["option"], r["devices_per_bridge"] + " per bridge") for r in R]
    cols = [TEAL if r["option"] == "D2W" else (CORAL if r["option"] == "GM4" else (NAVY if r["option"].endswith("W")
            else "#8FA3B5")) for r in R]
    eta = [100 * float(r["eta_800_800_60kW"]) for r in R]
    axes[0].barh(range(len(R)), eta, color=cols, height=0.62)
    axes[0].axvline(98.8, color=AMBER, ls="--", lw=1)
    axes[0].text(98.8, -0.75, " 98.8 % (DAB-04 target above 20 kW)", fontsize=6.5, color=AMBER, va="center")
    for i, (r, e) in enumerate(zip(R, eta)):
        axes[0].text(e + 0.02, i, "%.2f %%  %s/%s pts" % (e, r["full_power_points_ok"], r["full_power_points"]),
                     va="center", fontsize=6.8, color=NAVY)
    axes[0].set_xlim(98.2, 99.75)
    axes[0].set_xticks([98.4, 98.8, 99.2, 99.6])
    axes[0].set_xlabel("efficiency at 800 / 800 V, 60 kW [%] (model)")
    tj = [float(r["Tj_max_full_C"]) for r in R]
    axes[1].barh(range(len(R)), tj, color=cols, height=0.62)
    axes[1].axvline(140, color=CORAL, ls="--", lw=1)
    for i, (r, v) in enumerate(zip(R, tj)):
        usd = r["cost_usd_per_bridge"]
        axes[1].text(v + 2, i, "%.0f C  %s" % (v, ("%.0f USD/bridge" % float(usd)) if usd else "RFQ"), va="center",
                     fontsize=6.8, color=NAVY)
    axes[1].set_xlim(0, 215)
    axes[1].set_xlabel("hottest junction at 60 kW [C] (limit 140 C)")
    hbar_labels(axes[0], names)
    axes[0].tick_params(axis="y", labelsize=7)
    fig.suptitle("DAB-D60 device options (teal = chosen D2W, navy = worst-of-two design bases, coral = incumbent module)",
                 x=0.01, ha="left", fontweight="bold", fontsize=9.5, color=NAVY)
    return out(fig, "design_dab_devices", rel, "calculated by sim/dab_design.py - model, not calibrated")


FIGURES = [fig_gate0b, fig_phase_losses, fig_supply_budget, fig_trip_timing, fig_controller_resources,
           fig_magnetics_threeway, fig_magnetics_checks, fig_pv_inductor_options, fig_reviews, fig_audits,
           fig_pcs_screen, fig_dab_devices]

if __name__ == "__main__":
    written = [f() for f in FIGURES]
    missing = [w for w in written if not os.path.isfile(os.path.join(REPO, w)) or os.path.getsize(os.path.join(REPO, w)) < 5000]
    assert not missing, "figures_design: not written: %s" % missing
    print("figures_design: %d charts written to docs/assets/img/ (design_*.png)" % len(written))
