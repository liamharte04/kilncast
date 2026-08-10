"""Battery sweep: the "buffer atoms, not electrons" thesis on trial.

Runs the MPC on the same site-month with battery capacities from zero to
large. The plant already buffers energy as heat (kiln), chemical state
(silo), and gas (H2) - the question is how much ADDITIONAL methane a battery
buys, and at what battery price that beats spending the same capital on...
nothing (the plant is fixed here; capital comparison is against the battery's
own cost). We publish the flip point wherever it lands - if batteries win at
realistic 2026 prices, that is a finding, not a failure.

Assumption stated: no C-rate limit on charge/discharge (documented in the
writeup; typical LFP C-rates would not bind at these sizes).

Usage: uv run python experiments/battery.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control.mpc import ForecastMPC
from experiments.common import RESULTS_DIR, run_episode
from sim.env import PlantEnv
from sim.subsystems import load_curves, v

SITE = ("Seville ES", 37.39, -5.99)
START, DAYS = "2025-03-01", 28
SIZES_KWH = [0, 250, 500, 1000, 2000]
CAPEX_GRID = [50, 100, 150, 250]  # GBP/kWh scenarios


def main() -> None:
    out_dir = RESULTS_DIR / "battery"
    out_dir.mkdir(parents=True, exist_ok=True)
    name, lat, lon = SITE

    runs = {}
    for size in SIZES_KWH:
        out = out_dir / f"battery_{size}.json"
        if out.exists():
            runs[size] = json.loads(out.read_text())
            print(f"battery {size} kWh: cached")
            continue
        curves = copy.deepcopy(load_curves())
        curves["plant_sizing"]["battery_kwh"]["value"] = size
        env = PlantEnv.from_site(lat, lon, "2025-01-01", "2025-12-31", curves=curves)
        summary = run_episode(
            ForecastMPC(curves), env, START, DAYS,
            series_path=RESULTS_DIR / "series" / f"battery_{size}.json.gz",
        )
        summary |= {"battery_kwh": size, "site": name, "month": START}
        out.write_text(json.dumps(summary, indent=1))
        runs[size] = summary
        print(f"battery {size} kWh: {summary['methane_kg']:.0f} kg")

    # flip-point analysis: annualised value of extra methane vs annualised
    # battery cost, across capex scenarios
    curves = load_curves()
    ch4_value = v(curves, "economics", "methane_value_gbp_per_kg")
    years = v(curves, "economics", "plant_lifetime_years")
    base_kg = runs[0]["methane_kg"]
    annual_factor = 365.0 / DAYS

    table = []
    header = "".join(f"{str(c) + ' GBP/kWh':>15}" for c in CAPEX_GRID)
    print(f"\n{'kWh':>6}{'extra kg CH4/mo':>17}{'value GBP/yr':>14}{header}")
    for size in SIZES_KWH[1:]:
        extra_kg = runs[size]["methane_kg"] - base_kg
        value_yr = extra_kg * annual_factor * ch4_value
        row = {"battery_kwh": size, "extra_kg_month": round(extra_kg, 1),
               "extra_value_gbp_yr": round(value_yr, 0), "beats_capex": {}}
        cells = ""
        for capex in CAPEX_GRID:
            cost_yr = size * capex / years
            wins = value_yr > cost_yr
            row["beats_capex"][str(capex)] = bool(wins)
            cells += f"{'WINS' if wins else 'loses':>15}"
        table.append(row)
        print(f"{size:>6}{extra_kg:>17.1f}{value_yr:>14.0f}{cells}")

    (out_dir / "flip_point.json").write_text(json.dumps(table, indent=1))
    print("\nsaved flip-point table")


if __name__ == "__main__":
    main()
