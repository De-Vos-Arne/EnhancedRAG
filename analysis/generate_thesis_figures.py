"""
Print-ready figures for the thesis document itself — not the interactive
HTML report (that's a separate exploratory artifact). Reads
results/analysis_payload.json (written by the analysis pass) and saves
PNG (300dpi) + SVG into results/figures/.

    python analysis/generate_thesis_figures.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "results" / "analysis_payload.json"
OUT = ROOT / "results" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

data = json.loads(PAYLOAD.read_text(encoding="utf-8"))

# Single-hue sequential blue ramp (light -> dark), used consistently across
# every figure so "more blue" always means "higher score."
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6",
       "#256abf", "#1c5cab", "#184f95", "#104281"]
INK = "#211d17"
INK_2 = "#5b5344"
GRID = "#e4ddd0"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "Arial", "DejaVu Sans"],
    "text.color": INK, "axes.labelcolor": INK, "xtick.color": INK_2,
    "ytick.color": INK_2, "axes.edgecolor": GRID,
    "figure.facecolor": "white", "axes.facecolor": "white",
    "font.size": 11,
})


def save(fig, name):
    fig.savefig(OUT / f"{name}.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {name}.png / .svg")


# ── Figure 1: overall quality by regime, with 95% CI ────────────────────
rs = data["regime_stats"]
labels = [r["label"] for r in rs]
means = [r["mean"] for r in rs]
los = [r["mean"] - r["lo"] for r in rs]
his = [r["hi"] - r["mean"] for r in rs]
colors = [SEQ[i] for i in range(len(rs))]

fig, ax = plt.subplots(figsize=(8, 4.8))
y = np.arange(len(rs))
ax.barh(y, means, color=colors, height=0.62, zorder=3,
        edgecolor="white", linewidth=0.6)
ax.errorbar(means, y, xerr=[los, his], fmt="none", ecolor=INK,
            elinewidth=1.1, capsize=3, capthick=1.1, alpha=0.55, zorder=4)
for yi, m, r in zip(y, means, rs):
    ax.text(r["hi"] + 2.5, yi, f"{m:.1f}", va="center", fontsize=10, color=INK,
             fontweight="bold", zorder=5)
ax.set_yticks(y, labels)
ax.invert_yaxis()
ax.set_xlim(0, 112)
ax.set_xticks([0, 20, 40, 60, 80, 100])
ax.set_xlabel("Mean overall quality (0–100, blind-rated)")
ax.set_title("Answer quality by retrieval regime", fontsize=13, pad=14,
              loc="left", color=INK, fontweight="bold")
ax.spines[["top", "right", "left"]].set_visible(False)
ax.xaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
ax.set_axisbelow(True)
fig.text(0.01, -0.02, "Error bars: 95% CI. n = 27 questions per regime.",
          fontsize=8.5, color=INK_2)
save(fig, "fig1_overall_by_regime")

# ── Figure 2: heatmap, group x regime ───────────────────────────────────
groups = [g for g in ["A", "B", "C", "D", "E", "F", "X"] if g in data["group_table"]]
regimes = data["regimes"]
short = data["short"]
mat = np.array([[data["group_table"][g].get(r, np.nan) for r in regimes] for g in groups])

from matplotlib.colors import LinearSegmentedColormap
cmap = LinearSegmentedColormap.from_list("seq_blue", SEQ)

fig, ax = plt.subplots(figsize=(9, 4.2))
im = ax.imshow(mat, cmap=cmap, vmin=0, vmax=100, aspect="auto")
ax.set_xticks(range(len(regimes)), [short[r] for r in regimes])
group_labels = [f"{g} — {data['group_names'][g]}" for g in groups]
ax.set_yticks(range(len(groups)), group_labels)
for i in range(len(groups)):
    for j in range(len(regimes)):
        v = mat[i, j]
        if np.isnan(v):
            continue
        txt_color = "white" if v > 55 else INK
        ax.text(j, i, f"{v:.0f}", ha="center", va="center",
                 fontsize=9.5, color=txt_color)
ax.set_xticks(np.arange(-.5, len(regimes), 1), minor=True)
ax.set_yticks(np.arange(-.5, len(groups), 1), minor=True)
ax.grid(which="minor", color="white", linewidth=2)
ax.tick_params(which="minor", bottom=False, left=False)
ax.set_title("Mean overall quality by question group and regime", fontsize=13,
              pad=14, loc="left", color=INK, fontweight="bold")
cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
cbar.set_label("Mean overall quality", fontsize=9.5, color=INK_2)
save(fig, "fig2_group_regime_heatmap")

# ── Figure 3: distribution of scores per regime (spread, not just mean) ──
fig, ax = plt.subplots(figsize=(8, 4.6))
qtable = data["question_table"]
box_data = []
for r in regimes:
    vals = [q["scores"][r] for q in qtable if r in q["scores"]]
    box_data.append(vals)

bp = ax.boxplot(box_data, vert=False, patch_artist=True, widths=0.55,
                 medianprops=dict(color=INK, linewidth=1.4),
                 whiskerprops=dict(color=INK_2), capprops=dict(color=INK_2),
                 flierprops=dict(marker="o", markersize=3.5, markerfacecolor=INK_2,
                                  markeredgecolor="none", alpha=0.6))
for patch, color in zip(bp["boxes"], colors):
    patch.set_facecolor(color)
    patch.set_edgecolor("white")
    patch.set_alpha(0.9)
ax.set_yticks(range(1, len(regimes) + 1), labels)
ax.invert_yaxis()
ax.set_xlim(-2, 102)
ax.set_xlabel("Overall quality (0–100), distribution across all 27 questions")
ax.set_title("Spread of answer quality by regime", fontsize=13, pad=14,
              loc="left", color=INK, fontweight="bold")
ax.spines[["top", "right", "left"]].set_visible(False)
ax.xaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
ax.set_axisbelow(True)
save(fig, "fig3_score_spread_by_regime")

# ── Figure 4: every question x regime ───────────────────────────────────
qmat = np.array([[q["scores"].get(r, np.nan) for r in regimes] for q in qtable])
qids = [q["id"] for q in qtable]
qgroups = [q["group"] for q in qtable]

fig, ax = plt.subplots(figsize=(9, 0.32 * len(qtable) + 1.6))
im = ax.imshow(qmat, cmap=cmap, vmin=0, vmax=100, aspect="auto")
ax.set_xticks(range(len(regimes)), [short[r] for r in regimes])
row_labels = [f"{qid}" for qid in qids]
ax.set_yticks(range(len(qtable)), row_labels, fontsize=9)
for i in range(len(qtable)):
    for j in range(len(regimes)):
        v = qmat[i, j]
        if np.isnan(v):
            continue
        txt_color = "white" if v > 55 else INK
        ax.text(j, i, f"{v:.0f}", ha="center", va="center",
                 fontsize=8, color=txt_color)
ax.set_xticks(np.arange(-.5, len(regimes), 1), minor=True)
ax.set_yticks(np.arange(-.5, len(qtable), 1), minor=True)
ax.grid(which="minor", color="white", linewidth=1.4)
ax.tick_params(which="minor", bottom=False, left=False)

# group divider lines + labels on the right
last_g = None
for i, g in enumerate(qgroups):
    if g != last_g:
        if last_g is not None:
            ax.axhline(i - 0.5, color=INK, linewidth=0.9, xmin=-0.06, xmax=1,
                       clip_on=False)
        last_g = g
group_starts = {}
for i, g in enumerate(qgroups):
    group_starts.setdefault(g, i)
for g, i0 in group_starts.items():
    span = qgroups.count(g)
    ax.text(-1.35, i0 + span / 2 - 0.5, f"{g} — {data['group_names'][g]}",
             fontsize=8.5, color=INK_2, ha="right", va="center", rotation=0)

ax.set_title("Overall quality, every rated question x regime", fontsize=13,
              pad=14, loc="left", color=INK, fontweight="bold")
cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.03)
cbar.set_label("Overall quality", fontsize=9.5, color=INK_2)
save(fig, "fig4_every_question_heatmap")

# ── Figure 5: context volume (fragment count) vs answer quality ─────────
# The data is NOT continuous across fragment count — regimes cluster into
# three discrete bands (R0-R5 fixed at 12; R6/R8 variable ~18-60; R7 fixed
# ~230) — so a continuous regression line implies a relationship the data
# doesn't actually have samples to support in between. A grouped
# distribution by band is the honest version of this comparison.
CV_PATH = ROOT / "results" / "context_volume.json"
if CV_PATH.exists():
    cv = json.loads(CV_PATH.read_text(encoding="utf-8"))
    cv = [r for r in cv if r["n_fragments"] is not None]

    BANDS = [("12 fragments\n(R0–R5, fixed pool)", lambda n: n <= 15),
             ("18–60 fragments\n(R6, R8 — expansion/pooling)", lambda n: 15 < n < 100),
             ("~230 fragments\n(R7 — long-context control)", lambda n: n >= 100)]
    band_data, band_labels, band_n = [], [], []
    for label, pred in BANDS:
        vals = [r["overall"] for r in cv if pred(r["n_fragments"])]
        if not vals:
            continue
        band_data.append(vals)
        band_labels.append(label)
        band_n.append(len(vals))

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    positions = range(1, len(band_data) + 1)
    bp = ax.boxplot(band_data, positions=positions, patch_artist=True, widths=0.5,
                     medianprops=dict(color=INK, linewidth=1.4),
                     whiskerprops=dict(color=INK_2), capprops=dict(color=INK_2),
                     flierprops=dict(marker="o", markersize=3.5, markerfacecolor=INK_2,
                                      markeredgecolor="none", alpha=0.5))
    band_colors = [SEQ[1], SEQ[5], SEQ[8]][:len(band_data)]
    for patch, color in zip(bp["boxes"], band_colors):
        patch.set_facecolor(color)
        patch.set_edgecolor("white")
        patch.set_alpha(0.9)
    # jittered raw points on top, so the underlying sample sizes are visible
    rng = np.random.default_rng(0)
    for i, vals in enumerate(band_data, start=1):
        jitter = rng.uniform(-0.16, 0.16, size=len(vals))
        ax.scatter(np.full(len(vals), i) + jitter, vals, s=12, color=INK,
                   alpha=0.35, zorder=3, linewidth=0)
    ax.set_xticks(list(positions), [f"{l}\n(n={n})" for l, n in zip(band_labels, band_n)],
                  fontsize=9.5)
    ax.set_ylabel("Overall quality (0–100)")
    ax.set_ylim(-5, 100)
    ax.set_title("Context volume vs. answer quality, grouped by band", fontsize=13,
                  pad=14, loc="left", color=INK, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    fig.text(0.01, -0.03,
              "Each point is one rated run (n=243 total). Fragment counts cluster into three bands rather "
              "than varying continuously, so grouped distributions are shown rather than a fitted trend line.",
              fontsize=8.5, color=INK_2)
    save(fig, "fig5_context_volume_vs_quality")

print(f"\nAll figures written to {OUT}")
