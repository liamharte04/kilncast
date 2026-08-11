"""Discriminator: is the 500 kWh battery dip solver quality or real fragility?

Re-runs the 500 kWh forecast-MPC episode with a 120 s solver time limit and a
tighter MIP gap. If output recovers to ~1850 kg, the dip was incumbent
quality; if it stays ~1580 kg, deterministic-MPC-plus-battery fragility under
forecast error is real and goes in the writeup as a finding.
"""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control.mpc import ForecastMPC
from experiments.common import RESULTS_DIR, run_episode
from sim.env import PlantEnv
from sim.subsystems import load_curves

curves = copy.deepcopy(load_curves())
curves["plant_sizing"]["battery_kwh"]["value"] = 500
env = PlantEnv.from_site(37.39, -5.99, "2024-12-01", "2025-12-31", curves=curves)
mpc = ForecastMPC(curves, time_limit_s=120.0)
summary = run_episode(mpc, env, "2025-03-01", 28)
out = RESULTS_DIR / "battery" / "battery_mpc_500_longsolve.json"
out.write_text(json.dumps(summary, indent=1))
print(json.dumps({k: summary[k] for k in ("methane_kg", "wall_seconds", "solver_failures")}, indent=1))
