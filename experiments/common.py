"""Shared episode runner for all experiments.

Every experiment goes through run_episode so results are comparable and every
run can dump an hourly series for the dashboard. The optional per-hour hook
is how fault scenarios inject and monitor faults without special-casing the
environment.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pandas as pd

from sim.economics import summarise
from sim.env import PlantEnv
from sim.plant import EpisodeLog

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def run_episode(
    controller,
    env: PlantEnv,
    start: str,
    days: int,
    hook=None,
    series_path: Path | None = None,
) -> dict:
    """Run one episode; returns the economics summary.

    hook(hour, obs, action, result, env, controller) runs after every step -
    fault injection, monitors, and detectors live there.
    """
    if getattr(controller, "uses_actuals", False):
        controller.attach_env(env)
    env.reset(start, days=days)
    log = EpisodeLog()
    series: list[dict] = []
    hour = 0
    t0 = time.time()
    while not env.done:
        obs = env._obs()
        action = controller.act(obs, env.forecast_now)
        _, result = env.step(action)
        log.add(result)
        if hook is not None:
            hook(hour, obs, action, result, env, controller)
        series.append(
            {
                "time": str(obs["time"]),
                "ghi": round(obs["ghi_now"], 1),
                "solar_kw": round(result.solar_kw, 1),
                "used_kw": round(result.used_kw, 1),
                "curtailed_kwh": round(result.curtailed_kwh, 1),
                "methane_kg": round(result.methane_kg, 2),
                "kiln_temp": round(result.kiln_temp_frac, 3),
                "limiting": result.limiting_subsystem,
                "clips": len(result.clips),
                **{f"mode_{k}": v for k, v in result.modes.items()},
                **{k: round(v, 1) for k, v in result.stores.items()},
            }
        )
        hour += 1

    summary = summarise(log, env.plant.curves, hours=days * 24)
    summary["wall_seconds"] = round(time.time() - t0, 1)
    if hasattr(controller, "solve_failures"):
        summary["solver_failures"] = controller.solve_failures
    if series_path is not None:
        series_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(series_path, "wt") as f:
            json.dump(series, f)
    return summary


def load_sites() -> dict[str, tuple[float, float]]:
    import yaml

    path = Path(__file__).resolve().parent.parent / "sites.yaml"
    data = yaml.safe_load(path.read_text())
    return {s["name"]: (s["lat"], s["lon"]) for s in data["sites"]}


def series_frame(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt") as f:
        df = pd.DataFrame(json.load(f))
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")
