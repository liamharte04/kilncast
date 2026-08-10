"""Fault scenarios: inject mid-episode faults, measure impact and recovery.

Each fault runs against baseline and against MPC-with-monitor on the same
site-month, plus a no-fault reference. The kiln scenario exercises the full
autonomy loop the brief asks for: detect (commanded vs actual mode mismatch),
isolate (MPC replans with the kiln masked out), recover (repair notification
re-enables it).

Honest limitation, stated here and in the writeup: recovery relies on a
repair NOTIFICATION (a human event), not autonomous re-probing.

Usage: uv run python experiments/faults.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control.baseline import SunFollower
from control.mpc import ForecastMPC
from experiments.common import RESULTS_DIR, run_episode
from sim.env import PlantEnv
from sim.subsystems import load_curves

SITE = ("Seville ES", 37.39, -5.99)
START, DAYS = "2025-03-01", 28
FAULT_START_H, FAULT_END_H = 10 * 24, 13 * 24  # days 10-13


def fault_setters(name: str):
    """Returns (apply, clear) callables mutating env.plant.faults."""
    def make(attr, on_value, off_value):
        return (lambda env: setattr(env.plant.faults, attr, on_value),
                lambda env: setattr(env.plant.faults, attr, off_value))

    return {
        "kiln_heater": make("kiln_heater_failed", True, False),
        "electrolyser_degraded_30pct": make("electrolyser_capacity_frac", 0.7, 1.0),
        "irradiance_sensor_bias": make("irradiance_sensor_bias", 250.0, 0.0),
        "h2_valve_stuck": make("h2_valve_stuck", True, False),
        "panel_soiling_15pct": make("solar_soiling_frac", 0.85, 1.0),
    }[name]


class KilnFaultMonitor:
    """Detects a dead kiln by commanded-vs-actual mode mismatch, masks it out
    of the MPC, and re-enables it on the repair notification."""

    def __init__(self):
        self.mismatch_hours = 0
        self.events: list[str] = []
        self.fault_flagged = False

    def hook(self, hour, obs, action, result, env, controller):
        commanded_on = action.kiln_mode == "on" and action.kiln_load > 0
        if commanded_on and result.modes["kiln"] == "off":
            self.mismatch_hours += 1
        else:
            self.mismatch_hours = 0
        if self.mismatch_hours >= 2 and not self.fault_flagged:
            self.fault_flagged = True
            self.events.append(f"h{hour}: kiln fault DETECTED (2h mode mismatch) - replanning without kiln")
            if hasattr(controller, "set_kiln_available"):
                controller.set_kiln_available(False)
        if self.fault_flagged and not env.plant.faults.kiln_heater_failed:
            self.fault_flagged = False
            self.events.append(f"h{hour}: repair notification - kiln re-enabled, replanning")
            if hasattr(controller, "set_kiln_available"):
                controller.set_kiln_available(True)


def make_hook(apply_fault, clear_fault, monitor=None):
    def hook(hour, obs, action, result, env, controller):
        if hour == FAULT_START_H:
            apply_fault(env)
        if hour == FAULT_END_H:
            clear_fault(env)
        if monitor is not None:
            monitor.hook(hour, obs, action, result, env, controller)
    return hook


def main() -> None:
    curves = load_curves()
    out_dir = RESULTS_DIR / "faults"
    out_dir.mkdir(parents=True, exist_ok=True)
    _, lat, lon = SITE

    results: dict = {}
    for scenario in ["none", "kiln_heater", "electrolyser_degraded_30pct",
                     "irradiance_sensor_bias", "h2_valve_stuck", "panel_soiling_15pct"]:
        for cname, factory in [("baseline", SunFollower), ("mpc", ForecastMPC)]:
            key = f"{scenario}__{cname}"
            out = out_dir / f"{key}.json"
            if out.exists():
                results[key] = json.loads(out.read_text())
                print(f"{key}: cached")
                continue
            env = PlantEnv.from_site(lat, lon, "2024-12-01", "2025-12-31", curves=curves)
            controller = factory(curves)
            monitor = KilnFaultMonitor() if (cname == "mpc" and scenario == "kiln_heater") else None
            hook = None
            if scenario != "none":
                apply_fault, clear_fault = fault_setters(scenario)
                hook = make_hook(apply_fault, clear_fault, monitor)
            summary = run_episode(
                controller, env, START, DAYS, hook=hook,
                series_path=RESULTS_DIR / "series" / f"fault_{key}.json.gz",
            )
            summary |= {"scenario": scenario, "controller": cname,
                        "fault_window_days": [10, 13]}
            if monitor is not None:
                summary["detection_events"] = monitor.events
            out.write_text(json.dumps(summary, indent=1))
            results[key] = summary
            print(f"{key}: {summary['methane_kg']:.0f} kg")

    print(f"\n{'scenario':<32}{'baseline kg':>12}{'mpc kg':>9}{'mpc impact':>12}")
    ref = results["none__mpc"]["methane_kg"]
    for scenario in ["none", "kiln_heater", "electrolyser_degraded_30pct",
                     "irradiance_sensor_bias", "h2_valve_stuck", "panel_soiling_15pct"]:
        b = results[f"{scenario}__baseline"]["methane_kg"]
        m = results[f"{scenario}__mpc"]["methane_kg"]
        print(f"{scenario:<32}{b:>12.0f}{m:>9.0f}{100 * (m - ref) / ref:>+11.1f}%")
    ev = results.get("kiln_heater__mpc", {}).get("detection_events", [])
    if ev:
        print("\nkiln detection log:", *ev, sep="\n  ")


if __name__ == "__main__":
    main()
