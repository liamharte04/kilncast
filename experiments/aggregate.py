"""Aggregate sweep results into the headline table and figure.

Published scope: the 4 pilot sites x 4 seasonal months (complete controller
quads only; partial extra-site episodes are listed, never silently mixed in).
The headline figure uses ABSOLUTE production dumbbells, not percent uplift -
a 0 kg baseline month makes percent undefined and would hide the two best
results (Wiltshire and Leipzig January).

Usage: uv run python experiments/aggregate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from experiments.common import RESULTS_DIR

FIG_DIR = Path(__file__).resolve().parent.parent / "docs" / "figures"
PILOT_SITES = ["Heraklion GR", "Leipzig DE", "Seville ES", "Wiltshire UK"]
MONTH_LABEL = {"2025-01-01": "Jan", "2025-04-01": "Apr", "2025-07-01": "Jul", "2025-10-01": "Oct"}


def main() -> None:
    rows = [json.loads(p.read_text()) for p in (RESULTS_DIR / "sweep").glob("*.json")]
    df = pd.DataFrame(rows)

    pivot = df.pivot_table(index=["site", "month"], columns="controller",
                           values="methane_kg").reset_index()
    complete = pivot.dropna(subset=["baseline", "heuristic", "mpc", "oracle"])
    pilot = complete[complete["site"].isin(PILOT_SITES)].copy()
    extras = sorted(set(complete["site"]) - set(PILOT_SITES))
    if extras or len(complete) != len(pivot):
        print(f"NOTE: published scope = pilot sites {PILOT_SITES}; "
              f"complete extra sites present: {extras}; "
              f"partial (dropped) rows: {len(pivot) - len(complete)}")

    # percent uplift is undefined on a 0 kg baseline - report NaN, show kg
    pilot["mpc_vs_baseline_pct"] = pilot.apply(
        lambda r: 100 * (r["mpc"] - r["baseline"]) / r["baseline"] if r["baseline"] > 1 else float("nan"),
        axis=1,
    )
    pilot["gap_closed_pct"] = 100 * (pilot["mpc"] - pilot["baseline"]) / (
        pilot["oracle"] - pilot["baseline"]
    )
    print(pilot.round(1).to_string(index=False))

    overall = pilot[["baseline", "heuristic", "mpc", "oracle"]].sum()
    uplift = 100 * (overall["mpc"] - overall["baseline"]) / overall["baseline"]
    gap = 100 * (overall["mpc"] - overall["baseline"]) / (overall["oracle"] - overall["baseline"])
    print(f"\nHEADLINE (pooled kg, {len(pilot)} site-months): MPC {overall['mpc']:.0f} kg vs "
          f"baseline {overall['baseline']:.0f} kg = +{uplift:.1f}%, closing {gap:.0f}% of oracle gap")
    dist = pilot["gap_closed_pct"]
    print(f"gap-closed distribution across site-months: min {dist.min():.0f}%, "
          f"median {dist.median():.0f}%, max {dist.max():.0f}%")

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    pilot.to_csv(FIG_DIR / "sweep_table.csv", index=False)

    # dumbbell figure: absolute kg, baseline -> mpc, oracle tick
    plot = pilot.sort_values(["site", "month"]).reset_index(drop=True)
    labels = [f"{r.site.split(' ')[0]} {MONTH_LABEL.get(r.month, r.month)}" for r in plot.itertuples()]
    y = range(len(plot))
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.hlines(y, plot["baseline"], plot["mpc"], color="#b0b0b0", lw=2, zorder=1)
    ax.scatter(plot["baseline"], y, s=45, color="#c44e52", label="run-when-sunny baseline", zorder=2)
    ax.scatter(plot["mpc"], y, s=45, color="#2a7e43", label="forecast MPC", zorder=3)
    ax.scatter(plot["oracle"], y, s=60, color="#444444", marker="|", label="perfect-forecast oracle", zorder=2)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("methane produced in 28 days (kg)")
    ax.set_title("Same plant, same weather: scheduled vs naive, 16 real site-months (2025)")
    for i, r in enumerate(plot.itertuples()):
        if r.baseline < 1:
            ax.annotate(f"baseline {r.baseline:.0f} kg -> MPC {r.mpc:.0f} kg", (r.mpc + 60, i),
                        va="center", fontsize=8, color="#2a7e43")
    ax.legend(loc="lower right")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "sweep_dumbbell.png", dpi=120)
    print(f"figure saved: {FIG_DIR / 'sweep_dumbbell.png'}")


if __name__ == "__main__":
    main()
