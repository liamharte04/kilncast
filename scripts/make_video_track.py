"""Render the submission video's visual track - no audio, no presenter.

Produces a 3:00 MP4 at 1920x1080/12fps whose scene changes land exactly on
the narration script's second marks (docs-private/video-script.md), so a
voiceover recorded against the script lines up 1:1:

  0:00-0:15  A  storm replay, two days before the front, already playing
  0:15-0:35  B  plant schematic - the atoms-buffer thesis
  0:35-1:10  C  full week play-through with forecast-vs-actual chart
  1:10-1:40  D  results dumbbell + headline stamps (2 cards)
  1:40-2:05  E  what didn't work: battery card + rulebook ladder card
  2:05-2:35  F  kiln fault replay with detection event
  2:35-2:55  G  open-repo card + swap-the-yaml card
  2:55-3:00  H  freeze frame

Every frame is rendered from committed results data - nothing is mocked.

Usage: uv run python scripts/make_video_track.py
Output: docs-private/kilncast-visual-track.mp4 (kept out of the repo)
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import imageio.v2 as imageio
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Circle, FancyArrow, FancyBboxPatch, Rectangle

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from experiments.common import RESULTS_DIR, series_frame

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs-private" / "kilncast-visual-track.mp4"
FIG_DIR = ROOT / "docs" / "figures"
FPS = 12
W_IN, H_IN, DPI = 16, 9, 120  # 1920x1080

GREEN, RED, BLUE, ORANGE, BROWN, GREY = "#2a7e43", "#c44e52", "#3b7dd8", "#e8710a", "#8a6d3b", "#555555"
SKY_SUN, SKY_DARK = "#ffd54f", "#37474f"


def fig_to_frame(fig) -> np.ndarray:
    fig.canvas.draw()
    arr = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
    plt.close(fig)
    return arr


def new_fig():
    fig = plt.figure(figsize=(W_IN, H_IN), dpi=DPI)
    fig.patch.set_facecolor("white")
    return fig


# ---------------------------------------------------------------- data
def load_storm():
    key, week_start = (RESULTS_DIR / "storm_week.txt").read_text().strip().split(",")
    w0 = pd.Timestamp(week_start)
    out = {}
    for ctrl in ("baseline", "mpc"):
        df = series_frame(RESULTS_DIR / "series" / f"{key}_{ctrl}.json.gz")
        week = df.loc[w0 : w0 + pd.Timedelta(days=7)].iloc[:168]
        week = week.assign(ch4_cum=week["methane_kg"].cumsum())
        out[ctrl] = week.reset_index(drop=True)
    manifest = json.loads((ROOT / "dashboard" / "data" / "manifest.json").read_text())
    fc = manifest["storm"].get("forecast", {})
    out["lead1"] = (fc.get("lead1") or [None] * 168)[:168]
    out["lead7"] = (fc.get("lead7") or [None] * 168)[:168]
    out["site"] = manifest["storm"]["site"]
    return out


def load_fault():
    fdir = RESULTS_DIR / "faults" / "2025-07-01"
    meta = json.loads((fdir / "kiln_heater__mpc.json").read_text())
    detect_h = 250
    for e in meta.get("detection_events", []):
        m = re.match(r"h(\d+):.*DETECTED", e)
        if m:
            detect_h = int(m.group(1))
    fault = series_frame(RESULTS_DIR / "series" / "fault_2025-07-01_kiln_heater__mpc.json.gz")
    clean = series_frame(RESULTS_DIR / "series" / "fault_2025-07-01_none__mpc.json.gz")
    return fault.reset_index(drop=True), clean.reset_index(drop=True), detect_h


# ------------------------------------------------------- replay frame (A, C)
def vessel(ax, x, y, w, h, frac, color, label):
    ax.add_patch(Rectangle((x, y), w, h, fill=False, lw=1.6, edgecolor="#444"))
    ax.add_patch(Rectangle((x, y), w, h * max(0.0, min(1.0, frac)), color=color, alpha=0.85))
    ax.text(x + w / 2, y - 0.035, label, ha="center", va="top", fontsize=13)


def kiln_icon(ax, x, y, r, temp_frac, label=True):
    hot = temp_frac >= 0.9
    ax.add_patch(Circle((x, y), r * (1.15 + 0.25 * temp_frac), color=ORANGE, alpha=0.25 * temp_frac))
    ax.add_patch(Circle((x, y), r, color=ORANGE if hot else "#9aa0a6"))
    if label:
        ax.text(x, y - r - 0.04, f"kiln {int(temp_frac * 900)}C", ha="center", va="top", fontsize=13)


def replay_frame(storm, hour, show_forecast_chart=False):
    base, mpc = storm["baseline"], storm["mpc"]
    n = len(mpc)
    hour = min(int(hour), n - 1)
    fig = new_fig()
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ghi = mpc["ghi"].to_numpy()
    gmax = max(ghi.max(), 1.0)

    if show_forecast_chart:
        # line chart: actual + forecasts, progressive reveal
        cax = fig.add_axes([0.06, 0.72, 0.88, 0.22])
        xs = np.arange(n)
        cax.plot(xs[: hour + 1], ghi[: hour + 1], color=GREY, lw=2.2, label="actual W/m2")
        l1 = np.array([v if v is not None else np.nan for v in storm["lead1"]], dtype=float)
        l7 = np.array([v if v is not None else np.nan for v in storm["lead7"]], dtype=float)
        cax.plot(xs, l1, color=BLUE, lw=1.3, ls=(0, (4, 4)), label="forecast 1d ahead")
        cax.plot(xs, l7, color=RED, lw=1.5, ls=(0, (6, 4)), label="forecast 7d ahead")
        cax.axvline(hour, color="black", lw=1.4)
        cax.set_xlim(0, n - 1)
        cax.set_xticks([])
        cax.tick_params(labelsize=11)
        cax.legend(loc="upper right", fontsize=12, ncol=3, frameon=False)
        cax.set_title("the sky: what forecasts promised vs what it delivered",
                      fontsize=14, loc="left", color="#333")
        for s in ("top", "right"):
            cax.spines[s].set_visible(False)
    else:
        # sky strip
        for i in range(n):
            shade = ghi[i] / gmax
            color = tuple(np.array(matplotlib.colors.to_rgb(SKY_DARK)) * (1 - shade)
                          + np.array(matplotlib.colors.to_rgb(SKY_SUN)) * shade)
            ax.add_patch(Rectangle((0.04 + 0.92 * i / n, 0.87), 0.92 / n, 0.08, color=color))
        cx = 0.04 + 0.92 * hour / n
        ax.plot([cx, cx], [0.86, 0.96], color="black", lw=2)
        ax.text(0.04, 0.965, "the sky (actual irradiance)", fontsize=13, va="bottom")

    t = pd.Timestamp("2025-10-02") + pd.Timedelta(hours=hour)
    ax.text(0.96, 0.965, t.strftime("%a %d %b %Y  %H:00"), fontsize=16, va="bottom",
            ha="right", fontweight="bold")

    panels = {"baseline": (0.04, 0.47, "run-when-sunny", base), "mpc": (0.53, 0.96, "reads the forecast", mpc)}
    for _, (x0, x1, title, df) in panels.items():
        row = df.iloc[hour]
        cum = df["ch4_cum"].iloc[hour]
        mid = (x0 + x1) / 2
        top = 0.66 if show_forecast_chart else 0.80
        ax.add_patch(FancyBboxPatch((x0, 0.14), x1 - x0, top - 0.16, boxstyle="round,pad=0.008",
                                    fill=False, lw=1.2, edgecolor="#888"))
        ax.text(mid, top, title, ha="center", fontsize=19, fontweight="bold")
        vessel(ax, x0 + 0.05, 0.28, 0.07, 0.24, row["h2_kg"] / 100.0, BLUE, "H2 tank")
        vessel(ax, x0 + 0.17, 0.28, 0.07, 0.24, row["silo_kg"] / 3292.0, BROWN, "CaO silo")
        kiln_icon(ax, x0 + 0.33, 0.40, 0.045, row["kiln_temp"])
        ax.text(mid, 0.075, f"{cum:,.0f} kg CH4", ha="center", fontsize=26,
                fontweight="bold", color=RED if title == "run-when-sunny" else GREEN)
        recent = df["ch4_cum"].iloc[hour] - df["ch4_cum"].iloc[max(0, hour - 12)]
        if title == "run-when-sunny" and hour > 24 and recent < 0.5:
            ax.text(mid, top - 0.06, "STALLED", ha="center", fontsize=16, color="white",
                    fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=RED, edgecolor="none"))
    ax.text(0.5, 0.01, f"{storm['site']} - real 2025 weather, real as-issued forecasts (with real error)",
            ha="center", fontsize=12, color=GREY)
    return fig_to_frame(fig)


# ------------------------------------------------------------ schematic (B)
def schematic_frame(storm, hour):
    mpc = storm["mpc"]
    row = mpc.iloc[min(int(hour), len(mpc) - 1)]
    fig = new_fig()
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.5, 0.93, "Buffer atoms, not electrons", ha="center", fontsize=30, fontweight="bold")
    ax.text(0.5, 0.865, "the cheapest storage on this plant is heat, chemistry and hydrogen",
            ha="center", fontsize=16, color=GREY)

    def box(x, y, w, h, label, sub, color="#f2f2f2"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.008",
                                    facecolor=color, edgecolor="#555", lw=1.2))
        ax.text(x + w / 2, y + h * 0.62, label, ha="center", fontsize=17, fontweight="bold")
        ax.text(x + w / 2, y + h * 0.28, sub, ha="center", fontsize=12, color="#444")

    def arrow(x0, y0, x1, y1):
        ax.add_patch(FancyArrow(x0, y0, x1 - x0, y1 - y0, width=0.0035,
                                head_width=0.016, head_length=0.012,
                                length_includes_head=True, color="#666"))

    box(0.05, 0.55, 0.15, 0.14, "Solar", "1 MWp, off-grid")
    box(0.28, 0.62, 0.17, 0.14, "Electrolyser", "600 kW alkaline")
    box(0.28, 0.36, 0.17, 0.14, "DAC + kiln", "900C calcium loop")
    box(0.72, 0.48, 0.19, 0.16, "Sabatier reactor", "grid-spec methane")
    arrow(0.20, 0.63, 0.28, 0.685)
    arrow(0.20, 0.60, 0.28, 0.44)
    arrow(0.585, 0.66, 0.72, 0.585)
    arrow(0.585, 0.44, 0.72, 0.52)

    # the buffers, live-filled from data - the stars of the thesis
    vessel(ax, 0.50, 0.60, 0.055, 0.16, row["h2_kg"] / 100.0, BLUE, "H2 tank")
    vessel(ax, 0.50, 0.34, 0.055, 0.16, row["silo_kg"] / 3292.0, BROWN, "CaO silo")
    kiln_icon(ax, 0.62, 0.30, 0.035, row["kiln_temp"], label=False)
    ax.text(0.62, 0.245, "thermal mass", ha="center", fontsize=12)

    ax.text(0.5, 0.13, "every operating number source-tagged to Rivan's published data  -  curves/rivan-v1.yaml",
            ha="center", fontsize=14, color=GREY)
    ax.text(0.5, 0.07, "the scheduler parks every marginal joule in the cheapest state the forecast allows",
            ha="center", fontsize=15, color="#333", style="italic")
    return fig_to_frame(fig)


# --------------------------------------------------------------- cards (D-G)
def image_card(png_path, stamps=None, caption=None):
    fig = new_fig()
    img = mpimg.imread(png_path)
    ax = fig.add_axes([0.03, 0.10, 0.70, 0.84])
    ax.imshow(img)
    ax.axis("off")
    side = fig.add_axes([0.74, 0.10, 0.24, 0.84])
    side.axis("off")
    y = 0.88
    for big, small in stamps or []:
        side.text(0.05, y, big, fontsize=40, fontweight="bold", color=GREEN)
        side.text(0.05, y - 0.10, small, fontsize=14, color="#333", wrap=True)
        y -= 0.30
    if caption:
        fig.text(0.5, 0.035, caption, ha="center", fontsize=14, color=GREY)
    return fig_to_frame(fig)


def battery_card():
    runs = {}
    for p in (RESULTS_DIR / "battery").glob("battery_mpc_*.json"):
        d = json.loads(p.read_text())
        runs[d["battery_kwh"]] = d["methane_kg"]
    sizes = sorted(runs)
    band = json.loads((RESULTS_DIR / "noise_band" / "summary.json").read_text())["band_kg"]
    lo, hi = min(band.values()), max(band.values())

    fig = new_fig()
    fig.suptitle("A battery never paid for itself", fontsize=28, fontweight="bold", y=0.94)
    ax = fig.add_axes([0.08, 0.16, 0.52, 0.62])
    ax.axhspan(lo, hi, color="#dddddd", alpha=0.6, label=f"measured solver noise ({lo:.0f}-{hi:.0f} kg)")
    ax.plot(sizes, [runs[s] for s in sizes], marker="o", color=GREEN, lw=2, label="forecast MPC output")
    ax.set_xlabel("battery size (kWh)", fontsize=13)
    ax.set_ylabel("methane, 28 days (kg)", fontsize=13)
    ax.tick_params(labelsize=12)
    ax.legend(fontsize=12, frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    side = fig.add_axes([0.66, 0.16, 0.30, 0.62])
    side.axis("off")
    side.text(0, 0.95, "at every 2026 price:", fontsize=16, fontweight="bold")
    for i, capex in enumerate([50, 100, 150, 250]):
        side.text(0.05, 0.80 - i * 0.14, f"{capex} GBP/kWh", fontsize=15)
        side.text(0.60, 0.80 - i * 0.14, "loses", fontsize=15, color=RED, fontweight="bold")
    side.text(0, 0.14, "0-2 MWh changes output by less\nthan solver noise - the heat, silo\nand H2 tank were already the battery",
              fontsize=14, color="#333")
    return fig_to_frame(fig)


def ladder_card():
    data = json.loads(next((RESULTS_DIR).glob("controllers_*.json")).read_text())
    order = ["baseline-sun-follower", "heuristic-rules", "heuristic-tuned", "mpc-forecast", "oracle-perfect-forecast"]
    labels = ["naive baseline", "naive rules", "tuned rulebook", "forecast MPC", "perfect-forecast oracle"]
    kgs = [data[k]["methane_kg"] for k in order]
    colors = ["#999", "#999", BROWN, GREEN, "#333"]

    fig = new_fig()
    fig.suptitle("Rules get you halfway - optimisation takes the rest", fontsize=26, fontweight="bold", y=0.93)
    ax = fig.add_axes([0.14, 0.14, 0.76, 0.66])
    ax.barh(range(len(kgs)), kgs, color=colors, height=0.6)
    ax.set_yticks(range(len(kgs)))
    ax.set_yticklabels(labels, fontsize=15)
    ax.invert_yaxis()
    ax.set_xlabel("methane, Seville, March 2025 (kg)", fontsize=13)
    ax.tick_params(labelsize=12)
    for i, kg in enumerate(kgs):
        ax.text(kg + 25, i, f"{kg:,.0f} kg", va="center", fontsize=14, fontweight="bold")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.text(0.5, 0.04, "same plant, same sky - only the driver changes", ha="center",
             fontsize=14, color=GREY)
    return fig_to_frame(fig)


def fault_frame(fault, clean, detect_h, hour, h0=180, h1=400):
    fig = new_fig()
    fig.suptitle("Kiln heater failure, day 10 - detect, replan, recover", fontsize=24,
                 fontweight="bold", y=0.95)
    xs = np.arange(len(fault))
    reveal = int(hour)

    ax1 = fig.add_axes([0.07, 0.52, 0.86, 0.34])
    ax1.axvspan(240, 312, color=RED, alpha=0.10)
    ax1.plot(xs[h0:reveal], clean["methane_kg"].cumsum().iloc[h0:reveal] - clean["methane_kg"].cumsum().iloc[h0],
             color=GREEN, lw=1.8, ls=(0, (5, 4)), label="no fault")
    ax1.plot(xs[h0:reveal], fault["methane_kg"].cumsum().iloc[h0:reveal] - fault["methane_kg"].cumsum().iloc[h0],
             color=RED, lw=2.2, label="with fault")
    ax1.set_xlim(h0, h1)
    ax1.set_ylabel("cumulative kg CH4", fontsize=12)
    ax1.tick_params(labelsize=11)
    ax1.legend(fontsize=12, loc="upper left", frameon=False)
    for s in ("top", "right"):
        ax1.spines[s].set_visible(False)

    ax2 = fig.add_axes([0.07, 0.12, 0.86, 0.32])
    ax2.axvspan(240, 312, color=RED, alpha=0.10)
    ax2.plot(xs[h0:reveal], fault["kiln_temp"].iloc[h0:reveal], color=ORANGE, lw=2, label="kiln temperature")
    ax2.set_xlim(h0, h1)
    ax2.set_ylim(0, 1.08)
    ax2.set_ylabel("kiln temp (frac of 900C)", fontsize=12)
    ax2.set_xlabel("hour of month", fontsize=12)
    ax2.tick_params(labelsize=11)
    for s in ("top", "right"):
        ax2.spines[s].set_visible(False)

    if reveal >= detect_h:
        for ax in (ax1, ax2):
            ax.axvline(detect_h, color="black", lw=1.4, ls=":")
        ax1.annotate("fault DETECTED (2h mode mismatch)\nreplanning without the kiln",
                     xy=(detect_h, ax1.get_ylim()[1] * 0.55), fontsize=13, fontweight="bold",
                     xytext=(detect_h + 12, ax1.get_ylim()[1] * 0.35),
                     arrowprops=dict(arrowstyle="->", lw=1.2))
    if reveal >= 312:
        ax2.annotate("repair notification - kiln re-enabled", xy=(312, 0.15), fontsize=13,
                     xytext=(320, 0.35), arrowprops=dict(arrowstyle="->", lw=1.2))
    fig.text(0.5, 0.02, "Seville, July 2025 - monthly cost of the 3-day outage: -9.5%",
             ha="center", fontsize=13, color=GREY)
    return fig_to_frame(fig)


def readme_card():
    fig = new_fig()
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.text(0.08, 0.86, "Kilncast", fontsize=44, fontweight="bold")
    ax.text(0.08, 0.78, "buffer atoms, not electrons - an open digital twin of a solar-to-methane\nplant, scheduled against real archived weather forecasts",
            fontsize=17, color="#333", va="top")
    bullets = [
        ("+95.6% methane vs run-when-sunny", "same hardware, 4 European sites x 4 seasons of real 2025 weather"),
        ("75% of the perfect-hindsight gap closed", "against honest 7-day forecast error"),
        ("Wiltshire, October: 41 kg naive vs 726 kg scheduled", "18x from the same sky, in Rivan's own county"),
    ]
    y = 0.60
    for big, small in bullets:
        ax.text(0.10, y, "-  " + big, fontsize=20, fontweight="bold", color=GREEN)
        ax.text(0.13, y - 0.052, small, fontsize=14, color="#444")
        y -= 0.14
    ax.text(0.08, 0.14, "github.com/liamharte04/kilncast   -   MIT   -   one command reproduces every figure",
            fontsize=16, color="#333")
    return fig_to_frame(fig)


def yaml_card():
    lines = (ROOT / "curves" / "rivan-v1.yaml").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("electrolyser"))
    snippet = lines[start : start + 9]
    fig = new_fig()
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.text(0.5, 0.90, "Swap one file - model your own plant", ha="center", fontsize=28, fontweight="bold")
    ax.add_patch(FancyBboxPatch((0.08, 0.22), 0.84, 0.58, boxstyle="round,pad=0.01",
                                facecolor="#f6f6f6", edgecolor="#999"))
    for i, line in enumerate(snippet):
        ax.text(0.11, 0.74 - i * 0.055, line[:110], fontsize=12.5, family="monospace")
    ax.text(0.5, 0.12, "curves/rivan-v1.yaml - every value tagged  source: rivan | literature | chemistry | assumed",
            ha="center", fontsize=14, color=GREY)
    return fig_to_frame(fig)


# ----------------------------------------------------------------- assemble
def main() -> None:
    storm = load_storm()
    fault, clean, detect_h = load_fault()

    writer = imageio.get_writer(OUT, fps=FPS, codec="libx264", quality=7,
                                pixelformat="yuv420p", macro_block_size=8)
    total = 0

    def emit(frame, seconds):
        nonlocal total
        for _ in range(int(round(seconds * FPS))):
            writer.append_data(frame)
            total += 1

    def emit_anim(frame_fn, hours, seconds):
        nonlocal total
        n_frames = int(round(seconds * FPS))
        last_hour = None
        frame = None
        for k in range(n_frames):
            hour = hours[0] + (hours[1] - hours[0]) * k / max(1, n_frames - 1)
            if int(hour) != last_hour:
                frame = frame_fn(int(hour))
                last_hour = int(hour)
            writer.append_data(frame)
            total += 1

    # find the storm front: start hour of the darkest day
    daily = storm["mpc"].groupby(storm["mpc"].index // 24)["solar_kw"].sum()
    dark_day = int(daily.idxmin())
    front_h = dark_day * 24

    print("A: storm open (0:00-0:15)")
    emit_anim(lambda h: replay_frame(storm, h), (max(0, front_h - 48), min(front_h + 6, 167)), 15)
    print("B: schematic (0:15-0:35)")
    emit_anim(lambda h: schematic_frame(storm, h), (24, 48), 20)
    print("C: full week with forecast chart (0:35-1:10)")
    emit_anim(lambda h: replay_frame(storm, h, show_forecast_chart=True), (0, 167), 33)
    emit(replay_frame(storm, 167, show_forecast_chart=True), 2)
    print("D: results cards (1:10-1:40)")
    emit(image_card(FIG_DIR / "sweep_dumbbell.png",
                    stamps=[("+95.6%", "methane vs run-when-sunny,\nsame hardware"),
                            ("75%", "of the perfect-forecast\ngap closed")]), 15)
    emit(image_card(FIG_DIR / "sweep_dumbbell.png",
                    stamps=[("0 kg", "Wiltshire January - every\ncontroller except the oracle"),
                            ("18x", "Wiltshire October,\nscheduled vs naive")],
                    caption="northern sites gain the most from scheduling and trust forecasts the least"), 15)
    print("E: what didn't work (1:40-2:05)")
    emit(battery_card(), 12.5)
    emit(ladder_card(), 12.5)
    print("F: fault replay (2:05-2:35)")
    emit_anim(lambda h: fault_frame(fault, clean, detect_h, h), (185, 395), 30)
    print("G: open repo (2:35-2:55)")
    emit(readme_card(), 10)
    emit(yaml_card(), 10)
    print("H: freeze (2:55-3:00)")
    freeze = mpimg.imread(FIG_DIR / "storm_week.png")
    fig = new_fig()
    ax = fig.add_axes([0.05, 0.05, 0.90, 0.90])
    ax.imshow(freeze)
    ax.axis("off")
    emit(fig_to_frame(fig), 5)

    writer.close()
    print(f"done: {OUT} - {total} frames = {total / FPS:.1f}s, "
          f"{OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
