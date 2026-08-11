"""Aggregate sweep results into the headline table and figure.

Usage: uv run python experiments/aggregate.py
Reads results/sweep/*.json, writes docs/figures/sweep_table.csv and
docs/figures/sweep_uplift.png, prints the summary.
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


def main() -> None:
    rows = [json.loads(p.read_text()) for p in (RESULTS_DIR / "sweep").glob("*.json")]
    df = pd.DataFrame(rows)
    pivot = df.pivot_table(index=["site", "month"], columns="controller",
                           values="methane_kg").reset_index()
    pivot["mpc_vs_baseline_pct"] = 100 * (pivot["mpc"] - pivot["baseline"]) / pivot["baseline"]
    pivot["gap_closed_pct"] = 100 * (pivot["mpc"] - pivot["baseline"]) / (
        pivot["oracle"] - pivot["baseline"]
    )

    print(pivot.round(1).to_string(index=False))

    by_site = pivot.groupby("site")[["baseline", "heuristic", "mpc", "oracle"]].sum()
    by_site["mpc_vs_baseline_pct"] = (
        100 * (by_site["mpc"] - by_site["baseline"]) / by_site["baseline"]
    )
    by_site["gap_closed_pct"] = 100 * (by_site["mpc"] - by_site["baseline"]) / (
        by_site["oracle"] - by_site["baseline"]
    )
    print("\nPer site (4 seasonal months summed):")
    print(by_site.round(1).to_string())

    overall = pivot[["baseline", "heuristic", "mpc", "oracle"]].sum()
    uplift = 100 * (overall["mpc"] - overall["baseline"]) / overall["baseline"]
    gap = 100 * (overall["mpc"] - overall["baseline"]) / (overall["oracle"] - overall["baseline"])
    print(f"\nOVERALL: MPC {overall['mpc']:.0f} kg vs baseline {overall['baseline']:.0f} kg "
          f"= +{uplift:.1f}%, closing {gap:.0f}% of the oracle gap "
          f"({len(pivot)} site-months, real as-issued forecasts)")

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    pivot.to_csv(FIG_DIR / "sweep_table.csv", index=False)

    fig, ax = plt.subplots(figsize=(11, 5))
    months = ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01"]
    labels = ["Jan", "Apr", "Jul", "Oct"]
    sites = sorted(pivot["site"].unique())
    width = 0.2
    for i, site in enumerate(sites):
        sub = pivot[pivot["site"] == site].set_index("month").reindex(months)
        ax.bar([x + i * width for x in range(len(months))],
               sub["mpc_vs_baseline_pct"], width, label=site)
    ax.set_xticks([x + 1.5 * width for x in range(len(months))])
    ax.set_xticklabels(labels)
    ax.set_ylabel("MPC uplift vs baseline (%)")
    ax.set_title("Forecast-MPC methane uplift by site and season (28-day months, 2025)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "sweep_uplift.png", dpi=120)
    print(f"figure saved: {FIG_DIR / 'sweep_uplift.png'}")


if __name__ == "__main__":
    main()
