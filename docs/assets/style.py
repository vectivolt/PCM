"""Shared Matplotlib style for the documentation figures (see STYLE.md). Usage: from style import *"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
IMG = os.path.join(REPO, "docs", "assets", "img")
NAVY, TEAL, AMBER, CORAL, SLATE, MIST = "#0B1F33", "#00A99D", "#F2A007", "#E4572E", "#5B6B7A", "#EEF3F7"
SERIES = [TEAL, NAVY, AMBER, CORAL, SLATE, "#7FD4CE", "#8FA3B5"]

plt.rcParams.update({
    "figure.dpi": 200, "savefig.dpi": 200, "figure.facecolor": "white", "axes.facecolor": "white",
    "font.family": "DejaVu Sans", "font.size": 10, "text.color": NAVY, "axes.labelcolor": NAVY,
    "axes.edgecolor": SLATE, "xtick.color": SLATE, "ytick.color": SLATE, "axes.titleweight": "bold",
    "axes.titlesize": 12, "axes.titlelocation": "left", "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": MIST, "grid.linewidth": 1.0, "axes.axisbelow": True, "legend.frameon": False,
    "axes.prop_cycle": plt.cycler(color=SERIES)})


def save(fig, name, source, note="calculated or estimated - not measured"):
    """Write docs/assets/img/<name>.png with a footer naming the data source."""
    os.makedirs(IMG, exist_ok=True)
    fig.text(0.01, 0.005, "Source: %s  ·  %s" % (source, note), fontsize=6.5, color=SLATE, ha="left", va="bottom")
    fig.savefig(os.path.join(IMG, name + ".png"), bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return "docs/assets/img/%s.png" % name
