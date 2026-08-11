"""Run all four controllers on a real site and month; print the comparison.

Usage: uv run python scripts/run_controllers.py [lat lon start_date days]
Defaults: Seville, March 2025, 28 days (weather already cached).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control.baseline import SunFollower
from control.heuristic import RuleBased, RuleBasedTuned
from control.mpc import ForecastMPC, OracleMPC
from sim.economics import summarise
from sim.env import PlantEnv
from sim.plant import EpisodeLog
from sim.subsystems import load_curves

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def run(controller, env: PlantEnv, start: str, days: int) -> dict:
    if getattr(controller, "uses_actuals", False):
        controller.attach_env(env)
    env.reset(start, days=days)
    log = EpisodeLog()
    t0 = time.time()
    while not env.done:
        action = controller.act(env._obs(), env.forecast_now)
        _, result = env.step(action)
        log.add(result)
    summary = summarise(log, env.plant.curves, hours=days * 24)
    summary["wall_seconds"] = round(time.time() - t0, 1)
    if hasattr(controller, "solve_failures"):
        summary["solver_failures"] = controller.solve_failures
    return summary


def main() -> None:
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 37.39
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else -5.99
    start = sys.argv[3] if len(sys.argv) > 3 else "2025-03-01"
    days = int(sys.argv[4]) if len(sys.argv) > 4 else 28

    curves = load_curves()
    controllers = [
        SunFollower(curves),
        RuleBased(curves),
        RuleBasedTuned(curves),
        ForecastMPC(curves),
        OracleMPC(curves),
    ]
    results = {}
    for controller in controllers:
        env = PlantEnv.from_site(lat, lon, "2024-12-01", "2025-12-31")
        print(f"\n=== {controller.name} ===")
        results[controller.name] = run(controller, env, start, days)
        print(json.dumps(results[controller.name], indent=1))

    base = results["baseline-sun-follower"]["methane_kg"]
    oracle = results["oracle-perfect-forecast"]["methane_kg"]
    print(f"\n{'controller':<28}{'kg CH4':>9}{'vs baseline':>13}{'oracle gap closed':>19}")
    for name, r in results.items():
        kg = r["methane_kg"]
        vs = 100 * (kg - base) / base if base else 0
        gap = 100 * (kg - base) / (oracle - base) if oracle > base else 0
        print(f"{name:<28}{kg:>9.0f}{vs:>+12.1f}%{gap:>18.0f}%")

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"controllers_{lat}_{lon}_{start}_{days}d.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
