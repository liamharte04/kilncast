"""Characterise real forecast error by lead day across representative sites.

Produces docs/figures/forecast_error.png - the writeup figure showing why
scheduling against forecasts is hard: irradiance error grows with lead time,
and grows differently in different climates.

Usage: uv run python scripts/forecast_error_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sim.weather import MAX_LEAD_DAYS, load_actuals, load_issued

SITES = {
    "Seville ES": (37.39, -5.99),
    "Wiltshire UK": (51.20, -1.80),
    "Leipzig DE": (51.34, 12.37),
    "Heraklion GR": (35.34, 25.14),
}
START, END = "2025-01-01", "2025-12-31"
OUT = Path(__file__).resolve().parent.parent / "docs" / "figures" / "forecast_error.png"


def main() -> None:
    fig, (ax_mae, ax_rel) = plt.subplots(1, 2, figsize=(12, 4.5))
    rows = []
    for site, (lat, lon) in SITES.items():
        actual = load_actuals(lat, lon, START, END)["shortwave_radiation"]
        issued = load_issued(lat, lon, START, END)
        daylight = actual > 10
        maes, mean_irr = [], actual[daylight].mean()
        for lead in range(1, MAX_LEAD_DAYS + 1):
            fc = issued[f"shortwave_radiation_lead{lead}"]
            joined = pd.concat([actual, fc], axis=1).dropna()
            joined = joined[daylight.reindex(joined.index).fillna(False)]
            maes.append((joined.iloc[:, 0] - joined.iloc[:, 1]).abs().mean())
        leads = range(1, MAX_LEAD_DAYS + 1)
        ax_mae.plot(leads, maes, marker="o", label=site)
        ax_rel.plot(leads, [100 * m / mean_irr for m in maes], marker="o", label=site)
        rows.append({"site": site, "mean_daylight_irr": round(mean_irr, 1)}
                    | {f"mae_d{i + 1}": round(m, 1) for i, m in enumerate(maes)})

    ax_mae.set_xlabel("forecast lead (days)")
    ax_mae.set_ylabel("MAE, daylight hours (W/m2)")
    ax_mae.set_title("Irradiance forecast error by lead day (2025)")
    ax_rel.set_xlabel("forecast lead (days)")
    ax_rel.set_ylabel("MAE / mean daylight irradiance (%)")
    ax_rel.set_title("Relative forecast error")
    for ax in (ax_mae, ax_rel):
        ax.legend()
        ax.grid(alpha=0.3)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=120)

    table = pd.DataFrame(rows)
    table.to_csv(OUT.parent / "forecast_error.csv", index=False)
    print(table.to_string(index=False))
    print(f"\nfigure saved: {OUT}")


if __name__ == "__main__":
    main()
