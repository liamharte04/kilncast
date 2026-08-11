"""Render the storm-week GIF: two plants, one weather, diverging counters.

Design (the bear-GIF lesson - one legible, personable image):
- split screen: baseline left, forecast-MPC right, shared sky strip on top
  with the storm front visible as an approaching dark band
- stores drawn as filling vessels (H2 tank, CaO silo) and a kiln that glows;
  no line charts
- big ticking kg counters; a STALLED badge when the baseline flatlines
- freeze-frame ending that works as a standalone still
- persistent caption: real forecasts, real error; date stamp for time passing

Usage: uv run python scripts/make_storm_gif.py
Output: docs/figures/storm_week.gif (also used by the README).
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from experiments.common import RESULTS_DIR, series_frame

OUT = Path(__file__).resolve().parent.parent / "docs" / "figures" / "storm_week.gif"
DAYS = 7
FPS = 14
HOLD_FRAMES = 24  # ~1.7 s freeze on the final frame

SKY_TOP, SKY_H = 0.86, 0.12
PANEL = {"baseline": (0.02, 0.47), "mpc": (0.51, 0.96)}
COL = {"h2": "#3b7dd8", "silo": "#8a6d3b", "kiln_cold": "#9aa0a6", "kiln_hot": "#e8710a",
       "sky_sun": "#ffd54f", "sky_dark": "#37474f", "ch4": "#2a7e43", "stall": "#c62828"}


def load_week():
    key, week_start = (RESULTS_DIR / "storm_week.txt").read_text().strip().split(",")
    w0 = pd.Timestamp(week_start)
    frames = {}
    for ctrl in ("baseline", "mpc"):
        df = series_frame(RESULTS_DIR / "series" / f"{key.rsplit('_', 1)[0]}_{key.rsplit('_', 1)[1]}_{ctrl}.json.gz")
        frames[ctrl] = df.loc[w0 : w0 + pd.Timedelta(days=DAYS)]
    return key, frames


def vessel(ax, x, y, w, h, frac, color, label):
    ax.add_patch(Rectangle((x, y), w, h, fill=False, lw=1.2, edgecolor="#444"))
    ax.add_patch(Rectangle((x, y), w, h * max(0.0, min(1.0, frac)), color=color, alpha=0.85))
    ax.text(x + w / 2, y - 0.045, label, ha="center", va="top", fontsize=9)


def kiln_icon(ax, x, y, r, temp_frac):
    hot = temp_frac >= 0.9
    color = COL["kiln_hot"] if hot else COL["kiln_cold"]
    glow = Circle((x, y), r * (1.15 + 0.25 * temp_frac), color=COL["kiln_hot"],
                  alpha=0.25 * temp_frac)
    ax.add_patch(glow)
    ax.add_patch(Circle((x, y), r, color=color))
    ax.text(x, y - r - 0.05, f"kiln {int(temp_frac * 900)}C", ha="center", va="top", fontsize=9)


def draw_frame(frames, hour, key, caption: str | None = None):
    fig, ax = plt.subplots(figsize=(9, 5.4), dpi=90)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    base = frames["baseline"]
    mpc = frames["mpc"]
    n = len(base)
    hour = min(hour, n - 1)
    t = base.index[hour]

    # sky strip: hourly irradiance as coloured band, storm = dark
    ghi = base["ghi"].to_numpy()
    gmax = max(ghi.max(), 1.0)
    for i in range(n):
        shade = ghi[i] / gmax
        color = tuple(np.array(matplotlib.colors.to_rgb(COL["sky_dark"])) * (1 - shade)
                      + np.array(matplotlib.colors.to_rgb(COL["sky_sun"])) * shade)
        ax.add_patch(Rectangle((0.02 + 0.96 * i / n, SKY_TOP), 0.96 / n, SKY_H, color=color))
    cursor_x = 0.02 + 0.96 * hour / n
    ax.plot([cursor_x, cursor_x], [SKY_TOP - 0.01, SKY_TOP + SKY_H + 0.01], color="black", lw=1.5)
    ax.text(0.02, SKY_TOP + SKY_H + 0.015, "the sky (actual irradiance)", fontsize=9, va="bottom")
    ax.text(0.98, SKY_TOP + SKY_H + 0.015, t.strftime("%a %d %b %Y  %H:00"), fontsize=10,
            va="bottom", ha="right", fontweight="bold")

    titles = {"baseline": "run-when-sunny", "mpc": "reads the forecast"}
    for ctrl, (x0, x1) in PANEL.items():
        df = frames[ctrl]
        row = df.iloc[hour]
        cum = df["methane_kg"].iloc[: hour + 1].sum()
        mid = (x0 + x1) / 2
        ax.add_patch(FancyBboxPatch((x0, 0.16), x1 - x0, 0.62, boxstyle="round,pad=0.01",
                                    fill=False, lw=1.0, edgecolor="#888"))
        ax.text(mid, 0.80, titles[ctrl], ha="center", fontsize=12, fontweight="bold")

        vessel(ax, x0 + 0.05, 0.30, 0.08, 0.30, row["h2_kg"] / 100.0, COL["h2"], "H2 tank")
        vessel(ax, x0 + 0.18, 0.30, 0.08, 0.30, row["silo_kg"] / 3292.0, COL["silo"], "CaO silo")
        kiln_icon(ax, x0 + 0.36, 0.45, 0.055, row["kiln_temp"])

        ax.text(mid, 0.085, f"{cum:,.0f} kg CH4", ha="center", fontsize=17,
                fontweight="bold", color=COL["ch4"])

        recent = df["methane_kg"].iloc[max(0, hour - 11) : hour + 1].sum()
        if ctrl == "baseline" and hour > 24 and recent < 0.5:
            ax.text(mid, 0.70, "STALLED", ha="center", fontsize=13, color="white",
                    fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.25", facecolor=COL["stall"], edgecolor="none"))

    site = key.split("_2025")[0].replace("_", " ")
    ax.text(0.5, 0.005, f"{site} - real 2025 weather, real as-issued forecasts (with real error)",
            ha="center", fontsize=9, color="#555")
    if caption:
        ax.text(0.5, 0.865, caption, ha="center", va="center", fontsize=15,
                fontweight="bold", color="#222",
                bbox=dict(boxstyle="round,pad=0.45", facecolor="#ffffff",
                          edgecolor="#222", alpha=0.95))
    fig.tight_layout(pad=0.4)
    fig.canvas.draw()
    img = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
    plt.close(fig)
    return Image.fromarray(img)


def main() -> None:
    key, frames = load_week()
    n = len(frames["baseline"])
    step = 1  # every hour; 168 frames at 14 fps = 12 s loop
    images = [draw_frame(frames, h, key) for h in range(0, n, step)]

    # freeze-frame ending: final state + caption line
    final = draw_frame(frames, n - 1, key,
                       caption="Same plant. Same weather. It read the forecast.")
    images += [final] * HOLD_FRAMES

    OUT.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(OUT, save_all=True, append_images=images[1:],
                   duration=int(1000 / FPS), loop=0, optimize=True)
    size_mb = OUT.stat().st_size / 1e6
    print(f"saved {OUT} ({len(images)} frames, {size_mb:.1f} MB)")
    final.save(OUT.with_suffix(".png"))
    print(f"freeze frame: {OUT.with_suffix('.png')}")


if __name__ == "__main__":
    main()
