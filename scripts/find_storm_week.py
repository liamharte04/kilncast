"""Find the best storm week for the demo - by data, not by eye.

Scans every sweep series (site x seasonal month) for the 7-day window that
maximises demo value: a real multi-day irradiance collapse (drama), during
which the MPC's cumulative production pulls furthest ahead of the baseline
(divergence). The winner becomes the dashboard replay and the README GIF.

Usage: uv run python scripts/find_storm_week.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from experiments.common import RESULTS_DIR, series_frame


def daily(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("D").agg(solar_kwh=("solar_kw", "sum"), ch4=("methane_kg", "sum"))


def main() -> None:
    series_dir = RESULTS_DIR / "series"
    candidates = []
    for mpc_path in sorted(series_dir.glob("*_mpc.json.gz")):
        if mpc_path.name.startswith(("fault_", "battery_")):
            continue
        base_path = series_dir / mpc_path.name.replace("_mpc", "_baseline")
        if not base_path.exists():
            continue
        key = mpc_path.name.replace("_mpc.json.gz", "")
        mpc_d, base_d = daily(series_frame(mpc_path)), daily(series_frame(base_path))

        for start in range(len(mpc_d) - 7):
            w_m, w_b = mpc_d.iloc[start : start + 7], base_d.iloc[start : start + 7]
            sunny = w_m["solar_kwh"].max()
            dark = w_m["solar_kwh"].min()
            if sunny <= 0:
                continue
            drama = 1 - dark / sunny  # 1.0 = a fully dark day follows a bright one
            dark_days = int((w_m["solar_kwh"] < 0.35 * sunny).sum())
            divergence = w_m["ch4"].sum() - w_b["ch4"].sum()
            base_kg = w_b["ch4"].sum()
            score = divergence * drama * (1 + dark_days / 7)
            candidates.append({
                "key": key, "week_start": str(w_m.index[0].date()),
                "drama": round(drama, 2), "dark_days": dark_days,
                "mpc_kg": round(w_m["ch4"].sum(), 0), "baseline_kg": round(base_kg, 0),
                "divergence_kg": round(divergence, 0), "score": round(score, 0),
            })

    all_df = pd.DataFrame(candidates)
    top = all_df.sort_values("score", ascending=False).head(10)
    print(top.to_string(index=False))

    print("\nBest week per site:")
    all_df["site"] = all_df["key"].str.rsplit("_", n=1).str[0]
    per_site = all_df.sort_values("score", ascending=False).groupby("site").head(1)
    print(per_site.to_string(index=False))

    # The demo week: published pilot scope only, must contain a real storm
    # (drama >= 0.75), then maximise divergence x ratio - the GIF's counters
    # diverging is the emotional beat, and ratio is what the eye reads.
    pilot = all_df[all_df["site"].isin(
        ["Leipzig_DE", "Heraklion_GR", "Seville_ES", "Wiltshire_UK"])]
    stormy = pilot[(pilot["drama"] >= 0.75) & (pilot["baseline_kg"] > 0)].copy()
    stormy["ratio"] = stormy["mpc_kg"] / stormy["baseline_kg"]
    stormy["demo_score"] = stormy["divergence_kg"] * stormy["ratio"]
    best = stormy.sort_values("demo_score", ascending=False).iloc[0]
    print(f"\nWINNER: {best['key']} week of {best['week_start']} - "
          f"MPC {best['mpc_kg']:.0f} kg vs baseline {best['baseline_kg']:.0f} kg "
          f"through {best['dark_days']} dark day(s)")
    (RESULTS_DIR / "storm_week.txt").write_text(f"{best['key']},{best['week_start']}\n")


if __name__ == "__main__":
    main()
