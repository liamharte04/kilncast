"""Measure the solver-noise band instead of asserting it.

The submission leans on claims of a few percent (battery <3% under oracle,
kiln fault -0.5%, the optimizer's-curse delta). None of those are honest
without a measured per-episode noise floor. HiGHS is deterministic per
config, so the band is probed by varying solver settings that should not
change the true optimum: time limit, MIP gap, and salvage scaling.

Runs (Seville, March 2025, 28 days):
  A. forecast MPC, no battery, at 4 solver configs -> the noise band
  B. salvage scale 0.5x / 1.5x                     -> salvage sensitivity
  C. oracle with 0/1000/2000 kWh battery at a tighter gap -> monotonicity
  D. 500 kWh forecast MPC at the tightest config   -> optimizer's-curse check

Usage: uv run python experiments/noise_band.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control.mpc import ForecastMPC, OracleMPC
from experiments.common import RESULTS_DIR, run_episode
from sim.env import PlantEnv
from sim.subsystems import load_curves

SITE = (37.39, -5.99)
START, DAYS = "2025-03-01", 28
OUT_DIR = RESULTS_DIR / "noise_band"


def episode(tag: str, factory, battery_kwh: int = 0, **mpc_kwargs) -> float:
    out = OUT_DIR / f"{tag}.json"
    if out.exists():
        return json.loads(out.read_text())["methane_kg"]
    curves = copy.deepcopy(load_curves())
    curves["plant_sizing"]["battery_kwh"]["value"] = battery_kwh
    env = PlantEnv.from_site(*SITE, "2024-12-01", "2025-12-31", curves=curves)
    summary = run_episode(factory(curves, **mpc_kwargs), env, START, DAYS)
    summary |= {"tag": tag, "battery_kwh": battery_kwh, "mpc_kwargs": mpc_kwargs}
    out.write_text(json.dumps(summary, indent=1))
    print(f"{tag}: {summary['methane_kg']:.0f} kg ({summary['wall_seconds']}s)")
    return summary["methane_kg"]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("A. noise band - same problem, four solver configs")
    band = {
        "t30_g02": episode("A_t30_g02", ForecastMPC, time_limit_s=30, mip_rel_gap=0.02),
        "t60_g01": episode("A_t60_g01", ForecastMPC, time_limit_s=60, mip_rel_gap=0.01),
        "t90_g005": episode("A_t90_g005", ForecastMPC, time_limit_s=90, mip_rel_gap=0.005),
        "t120_g02": episode("A_t120_g02", ForecastMPC, time_limit_s=120, mip_rel_gap=0.02),
    }
    lo, hi = min(band.values()), max(band.values())
    band_pct = 100 * (hi - lo) / hi
    print(f"   band: {lo:.0f}..{hi:.0f} kg = {band_pct:.1f}% spread")

    print("B. salvage sensitivity (0.5x / 1.5x at reference config)")
    s_lo = episode("B_salvage_0.5", ForecastMPC, salvage_scale=0.5)
    s_hi = episode("B_salvage_1.5", ForecastMPC, salvage_scale=1.5)
    print(f"   salvage 0.5x: {s_lo:.0f} kg, 1.5x: {s_hi:.0f} kg "
          f"(ref {band['t30_g02']:.0f})")

    print("C. oracle battery monotonicity at tighter gap")
    okgs = [episode(f"C_oracle_b{b}", OracleMPC, battery_kwh=b,
                    time_limit_s=60, mip_rel_gap=0.01) for b in (0, 1000, 2000)]
    print(f"   oracle 0/1000/2000 kWh: {[round(k) for k in okgs]}")

    print("D. 500 kWh forecast MPC at tightest config (curse check)")
    d = episode("D_b500_tight", ForecastMPC, battery_kwh=500,
                time_limit_s=90, mip_rel_gap=0.005)
    print(f"   500 kWh tight-solve: {d:.0f} kg vs no-battery band {lo:.0f}..{hi:.0f}")

    summary = {
        "band_kg": band, "band_pct": round(band_pct, 2),
        "salvage": {"x0.5": s_lo, "x1.5": s_hi},
        "oracle_battery": dict(zip(["0", "1000", "2000"], okgs)),
        "b500_tight": d,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=1))
    print("\nsaved noise_band/summary.json")


if __name__ == "__main__":
    main()
