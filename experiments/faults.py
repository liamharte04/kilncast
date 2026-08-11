"""Fault scenarios: inject mid-episode faults, measure impact and recovery.

Runs each fault against the relevant controllers on the same site-month,
plus no-fault references. The kiln scenario exercises the full autonomy loop
the brief asks for: detect (commanded vs actual mode mismatch), isolate (MPC
replans with the kiln masked out), recover (repair notification re-enables
it).

Scenario-controller pairing is deliberate: the irradiance sensor fault
victimises controllers that READ the sensor (baseline, heuristic) while the
MPC is architecturally immune (it plans from forecasts) - that contrast is
the result, not an accident.

Default window is July (peak production - faults have something to break).
The March runs bound the lucky-timing case; see scripts/verify_kiln_fault.py.

Honest limitation, stated here and in the writeup: recovery relies on a
repair NOTIFICATION (a human event), not autonomous re-probing.

Usage: uv run python experiments/faults.py [start_date]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control.baseline import SunFollower
from control.heuristic import RuleBased
from control.mpc import ForecastMPC
from experiments.common import RESULTS_DIR, run_episode
from sim.env import PlantEnv
from sim.subsystems import load_curves

SITE = ("Seville ES", 37.39, -5.99)
DAYS = 28
FAULT_START_H, FAULT_END_H = 10 * 24, 13 * 24  # days 10-13

CONTROLLERS = {"baseline": SunFollower, "heuristic": RuleBased, "mpc": ForecastMPC}

# scenario -> (fault attr, on value, off value, controllers to test)
SCENARIOS: dict[str, tuple | None] = {
    "none": None,
    "kiln_heater": ("kiln_heater_failed", True, False),
    "electrolyser_degraded_30pct": ("electrolyser_capacity_frac", 0.7, 1.0),
    "irradiance_sensor_bias": ("irradiance_sensor_bias", 250.0, 0.0),
    "h2_valve_stuck": ("h2_valve_stuck", True, False),
    "panel_soiling_15pct": ("solar_soiling_frac", 0.85, 1.0),
}
PAIRINGS = {
    "none": ["baseline", "heuristic", "mpc"],
    "kiln_heater": ["baseline", "mpc"],
    "electrolyser_degraded_30pct": ["baseline", "mpc"],
    "irradiance_sensor_bias": ["baseline", "heuristic", "mpc"],
    "h2_valve_stuck": ["baseline", "mpc"],
    "panel_soiling_15pct": ["baseline", "mpc"],
}


def fault_setters(name: str):
    attr, on_value, off_value = SCENARIOS[name]
    return (lambda env: setattr(env.plant.faults, attr, on_value),
            lambda env: setattr(env.plant.faults, attr, off_value))


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
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-07-01"
    curves = load_curves()
    out_dir = RESULTS_DIR / "faults" / start
    out_dir.mkdir(parents=True, exist_ok=True)
    _, lat, lon = SITE

    results: dict = {}
    for scenario, controllers in PAIRINGS.items():
        for cname in controllers:
            key = f"{scenario}__{cname}"
            out = out_dir / f"{key}.json"
            if out.exists():
                results[key] = json.loads(out.read_text())
                print(f"{key}: cached")
                continue
            env = PlantEnv.from_site(lat, lon, "2024-12-01", "2025-12-31", curves=curves)
            controller = CONTROLLERS[cname](curves)
            monitor = KilnFaultMonitor() if (cname == "mpc" and scenario == "kiln_heater") else None
            hook = None
            if scenario != "none":
                apply_fault, clear_fault = fault_setters(scenario)
                hook = make_hook(apply_fault, clear_fault, monitor)
            summary = run_episode(
                controller, env, start, DAYS, hook=hook,
                series_path=RESULTS_DIR / "series" / f"fault_{start}_{key}.json.gz",
            )
            summary |= {"scenario": scenario, "controller": cname, "month": start,
                        "fault_window_days": [10, 13]}
            if monitor is not None:
                summary["detection_events"] = monitor.events
            out.write_text(json.dumps(summary, indent=1))
            results[key] = summary
            print(f"{key}: {summary['methane_kg']:.0f} kg")

    print(f"\n{'scenario':<32}" + "".join(f"{c:>12}" for c in CONTROLLERS))
    for scenario, controllers in PAIRINGS.items():
        cells = "".join(
            f"{results[f'{scenario}__{c}']['methane_kg']:>12.0f}" if c in controllers else f"{'-':>12}"
            for c in CONTROLLERS
        )
        print(f"{scenario:<32}{cells}")
    ev = results.get("kiln_heater__mpc", {}).get("detection_events", [])
    if ev:
        print("\nkiln detection log:", *ev, sep="\n  ")


if __name__ == "__main__":
    main()
