"""The README figure: every 2026 event's abnormal return, by direction, against the noise band.

Usage:
    python -m src.figures      -> docs/figures/h1_results.png
"""

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src import config  # noqa: E402

SURFACE, INK, QUIET = "#fcfcfb", "#0b0b0b", "#52514e"
DOT, BAND, ACCENT, AXIS = "#9a998f", "#ecebe6", "#2a78d6", "#b9b8b0"
OUT = config.ROOT / "docs" / "figures" / "h1_results.png"


def panel(ax, df, band, lim, step, heading, band_label):
    ax.axvspan(*band, color=BAND, zorder=0, lw=0)
    ax.axvline(0, color=AXIS, lw=1, zorder=1)
    for y, d in ((1, "escalating"), (0, "de-escalating")):
        g = df[df["direction"] == d]["ar_bps"].to_numpy()
        jitter = (np.arange(len(g)) % 3 - 1) * 0.09
        ax.scatter(g, y + jitter, s=34, color=DOT, edgecolor=SURFACE, linewidth=0.8, zorder=2)
        m = g.mean()
        ax.plot([m, m], [y - 0.25, y + 0.25], color=ACCENT, lw=3, solid_capstyle="round", zorder=3)
        ax.annotate(f"mean {m:+.1f}", (m, y + 0.27), xytext=(-4 if m < 0 else 4, 0),
                    textcoords="offset points", ha="right" if m < 0 else "left", va="bottom",
                    fontsize=9, fontweight="bold", color=INK)
        ax.text(-lim * 1.02, y, f"{d.capitalize()}  (n {len(g)})", ha="right", va="center",
                fontsize=10, color=INK)
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-0.6, 1.75)
    ax.set_xticks(np.arange(-lim, lim + 1, step))
    ax.set_xticklabels([f"{t:+.0f}" if t else "0" for t in ax.get_xticks()], fontsize=9, color=QUIET)
    ax.set_yticks([])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(axis="x", length=0, pad=6)
    ax.set_title(heading, loc="left", fontsize=10.5, fontweight="bold", color=INK, x=-0.3)
    ax.text(lim, 1.72, band_label, ha="right", va="top", fontsize=8.5, color=QUIET)
    ax.set_facecolor(SURFACE)


def main() -> None:
    ev = pd.read_csv(config.RESULTS_DIR / "iran_war_2026_events.csv")
    ov = pd.read_csv(config.RESULTS_DIR / "iran_war_2026_offhours_events.csv")
    plc = pd.read_csv(config.RESULTS_DIR / "iran_war_2026_placebo.csv").set_index("period")
    rob = pd.read_csv(config.RESULTS_DIR / "iran_war_2026_summary.csv")
    night_sd = rob.loc[rob["test"].str.startswith("pre-war nights"), "sd_bps"].iloc[0]

    fig, axes = plt.subplots(2, 1, figsize=(9, 5.6), facecolor=SURFACE)
    fig.subplots_adjust(left=0.27, right=0.97, top=0.84, bottom=0.07, hspace=0.75)
    panel(axes[0], ev, (plc.loc["phase2", "p05_bps"], plc.loc["phase2", "p95_bps"]), 90, 30,
          "Market hours: 30 minutes from the post (bp)", "shaded: placebo 5th to 95th percentile")
    panel(axes[1], ov, (-night_sd, night_sd), 250, 125,
          "Overnight: last price before the post to 10:00 next day (bp)", "shaded: pre-war nights, ±1 SD")
    fig.text(0.02, 0.955, "In market hours, ITA's moves after Iran posts sit in the noise band",
             fontsize=13, fontweight="bold", color=INK)
    fig.text(0.02, 0.91, "Each dot is one 2026 event: ITA's abnormal return vs SPY. "
             "H1 predicts escalating dots right of 0, de-escalating left. Blue tick = mean.",
             fontsize=9, color=QUIET)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200, facecolor=SURFACE)
    print(f"wrote {OUT.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
