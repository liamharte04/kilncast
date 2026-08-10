"""Phase 0 de-risk: can Open-Meteo give us real archived forecasts AND actuals?

The entry's core differentiator is backtesting schedulers against forecasts
as they were actually issued (with their real errors), not synthetic noise.
This script probes three Open-Meteo endpoints:

1. Archive API (ERA5)              -> ground-truth actuals for 2025
2. Previous Runs API               -> forecasts at lead times of 1..7+ days,
                                      exactly as issued on the day
3. Historical Forecast API         -> the continuous short-lead forecast archive

It reports hourly coverage per variable, forecast error growth by lead day
(the sanity check that these really are as-issued forecasts: error must grow
with lead time), and saves a forecast-vs-actual plot for one week.

Usage: uv run python scripts/derisk_weather.py
"""

from __future__ import annotations

import sys
import time

import matplotlib
import pandas as pd
import requests

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SITES = {
    "Seville ES": (37.39, -5.99),
    "Wiltshire UK": (51.20, -1.80),
}
YEAR_START, YEAR_END = "2025-01-01", "2025-12-31"
PLOT_WEEK = ("2025-03-10", "2025-03-16")
ACTUAL_VARS = ["shortwave_radiation", "direct_normal_irradiance", "temperature_2m", "cloud_cover"]


def fetch(url: str, params: dict, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt + 1} after error: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("unreachable")


def to_frame(payload: dict) -> pd.DataFrame:
    df = pd.DataFrame(payload["hourly"])
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")


def coverage(df: pd.DataFrame) -> dict[str, float]:
    return {c: round(100 * df[c].notna().mean(), 1) for c in df.columns}


def main() -> int:
    ok = True
    frames: dict[str, dict[str, pd.DataFrame]] = {}

    for site, (lat, lon) in SITES.items():
        print(f"\n=== {site} ({lat}, {lon}) ===")
        common = {"latitude": lat, "longitude": lon, "timezone": "UTC"}

        print("[1/3] Archive API (ERA5 actuals, full 2025)...")
        actual = to_frame(
            fetch(
                "https://archive-api.open-meteo.com/v1/archive",
                common
                | {
                    "start_date": YEAR_START,
                    "end_date": YEAR_END,
                    "hourly": ",".join(ACTUAL_VARS),
                },
            )
        )
        print(f"  rows: {len(actual)}  coverage %: {coverage(actual)}")
        if len(actual) < 8700 or min(coverage(actual).values()) < 99:
            print("  !! actuals coverage below threshold")
            ok = False

        print("[2/3] Previous Runs API (as-issued forecasts, lead day 1..10 probe)...")
        lead_frames: dict[int, pd.Series] = {}
        for lead in range(1, 11):
            var = f"shortwave_radiation_previous_day{lead}"
            try:
                df = to_frame(
                    fetch(
                        "https://previous-runs-api.open-meteo.com/v1/forecast",
                        common
                        | {
                            "start_date": YEAR_START,
                            "end_date": YEAR_END,
                            "hourly": var,
                        },
                    )
                )
                cov = df[var].notna().mean() * 100
                print(f"  lead day {lead}: {cov:.1f}% coverage")
                if cov > 90:
                    lead_frames[lead] = df[var]
            except Exception as e:  # noqa: BLE001
                print(f"  lead day {lead}: UNAVAILABLE ({type(e).__name__})")

        max_lead = max(lead_frames) if lead_frames else 0
        print(f"  -> usable as-issued lead days: 1..{max_lead}")
        if max_lead < 5:
            print("  !! fewer than 5 lead days available")
            ok = False

        print("  error growth by lead day (MAE W/m2 vs ERA5, daylight hours):")
        sun = actual["shortwave_radiation"] > 10
        maes = {}
        for lead, series in sorted(lead_frames.items()):
            joined = pd.concat([actual["shortwave_radiation"], series], axis=1).dropna()
            joined = joined[sun.reindex(joined.index).fillna(False)]
            mae = (joined.iloc[:, 0] - joined.iloc[:, 1]).abs().mean()
            maes[lead] = mae
            print(f"    day {lead}: {mae:.0f}")
        if maes and not (maes[max(maes)] > maes[min(maes)]):
            print("  !! error does not grow with lead time - data may not be as-issued")
            ok = False

        print("[3/3] Historical Forecast API (short-lead continuous archive)...")
        hist = to_frame(
            fetch(
                "https://historical-forecast-api.open-meteo.com/v1/forecast",
                common
                | {
                    "start_date": PLOT_WEEK[0],
                    "end_date": PLOT_WEEK[1],
                    "hourly": "shortwave_radiation",
                },
            )
        )
        print(f"  rows: {len(hist)}  coverage %: {coverage(hist)}")

        frames[site] = {"actual": actual, "leads": lead_frames}

    # Exit-criterion plot: one week, actual vs short and long lead forecasts
    fig, axes = plt.subplots(len(SITES), 1, figsize=(12, 4 * len(SITES)), sharex=True)
    for ax, (site, data) in zip(axes, frames.items()):
        week = slice(PLOT_WEEK[0], PLOT_WEEK[1])
        ax.plot(data["actual"].loc[week].index, data["actual"].loc[week, "shortwave_radiation"],
                label="actual (ERA5)", lw=2, color="black")
        leads = sorted(data["leads"])
        for lead, style in [(leads[0], "tab:green"), (leads[-1], "tab:red")]:
            s = data["leads"][lead].loc[week]
            ax.plot(s.index, s.values, label=f"forecast, issued {lead}d ahead", lw=1, color=style)
        ax.set_title(f"{site} - irradiance, forecast vs actual ({PLOT_WEEK[0]} week)")
        ax.set_ylabel("W/m2")
        ax.legend()
    fig.tight_layout()
    out = "derisk_weather.png"
    fig.savefig(out, dpi=120)
    print(f"\nplot saved: {out}")

    print("\nVERDICT:", "PASS - weather source is viable" if ok else "FAIL - see !! lines above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
