"""Bound the kiln-fault impact: same fault, deliberately sunny window.

The day 10-13 fault overlapped cloudy days (lucky timing -> -0.5%). This run
injects the identical 3-day fault over days 23-26 (productive days in the
clean run) to bound the unlucky case honestly.

Usage: uv run python scripts/kiln_fault_sunny_window.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import experiments.faults as faults_mod
from control.mpc import ForecastMPC
from experiments.common import RESULTS_DIR, run_episode
from experiments.faults import KilnFaultMonitor, fault_setters, make_hook
from sim.env import PlantEnv
from sim.subsystems import load_curves

faults_mod.FAULT_START_H, faults_mod.FAULT_END_H = 23 * 24, 26 * 24

curves = load_curves()
env = PlantEnv.from_site(37.39, -5.99, "2024-12-01", "2025-12-31", curves=curves)
monitor = KilnFaultMonitor()
apply_fault, clear_fault = fault_setters("kiln_heater")
hook = make_hook(apply_fault, clear_fault, monitor)
summary = run_episode(
    ForecastMPC(curves), env, "2025-03-01", 28, hook=hook,
    series_path=RESULTS_DIR / "series" / "fault_kiln_sunny__mpc.json.gz",
)
summary["detection_events"] = monitor.events
summary["fault_window_days"] = [23, 26]
out = RESULTS_DIR / "faults" / "kiln_heater_sunny_window__mpc.json"
out.write_text(json.dumps(summary, indent=1))
ref = json.loads((RESULTS_DIR / "faults" / "none__mpc.json").read_text())["methane_kg"]
print(f"sunny-window kiln fault: {summary['methane_kg']:.0f} kg vs clean {ref:.0f} "
      f"({100 * (summary['methane_kg'] - ref) / ref:+.1f}%)")
print("detection:", *summary["detection_events"], sep="\n  ")
