"""Multi-site x multi-month x controller sweep - the headline table's data.

Resumable: each (site, month, controller) episode saves its own JSON and is
skipped on re-run, so the sweep can be stopped and restarted freely.

Usage:
  uv run python experiments/sweep.py                    # cached 4-site pilot
  uv run python experiments/sweep.py --all-sites        # full 20-site sweep
  uv run python experiments/sweep.py --months 2025-03-01 --sites "Seville ES"

COVERAGE IS LOGGED, NEVER SILENT: the pilot covers 4 sites x 4 seasonal
months; the full run covers every site in sites.yaml x 4 seasonal months.
Extending months costs ~6 min of solver time per additional site-month.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control.baseline import SunFollower
from control.heuristic import RuleBased
from control.mpc import ForecastMPC, OracleMPC
from experiments.common import RESULTS_DIR, load_sites, run_episode
from sim.env import PlantEnv
from sim.subsystems import load_curves

PILOT_SITES = ["Seville ES", "Wiltshire UK", "Leipzig DE", "Heraklion GR"]
SEASONAL_MONTHS = ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01"]
CONTROLLERS = {
    "baseline": SunFollower,
    "heuristic": RuleBased,
    "mpc": ForecastMPC,
    "oracle": OracleMPC,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(PILOT_SITES))
    ap.add_argument("--all-sites", action="store_true")
    ap.add_argument("--months", default=",".join(SEASONAL_MONTHS))
    ap.add_argument("--controllers", default="baseline,heuristic,mpc,oracle")
    ap.add_argument("--days", type=int, default=28)
    args = ap.parse_args()

    all_sites = load_sites()
    names = list(all_sites) if args.all_sites else [s.strip() for s in args.sites.split(",")]
    months = [m.strip() for m in args.months.split(",")]
    controllers = [c.strip() for c in args.controllers.split(",")]
    out_dir = RESULTS_DIR / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)

    total = len(names) * len(months) * len(controllers)
    done = 0
    print(f"sweep: {len(names)} sites x {len(months)} months x {len(controllers)} controllers "
          f"= {total} episodes ({args.days} days each)")

    curves = load_curves()
    for site in names:
        lat, lon = all_sites[site]
        env = None  # lazy - skip fetch entirely if all episodes exist
        for month in months:
            for cname in controllers:
                done += 1
                key = f"{site.replace(' ', '_')}_{month}_{cname}"
                out = out_dir / f"{key}.json"
                if out.exists():
                    print(f"[{done}/{total}] {key}: cached, skip")
                    continue
                if env is None:
                    print(f"  fetching weather for {site} ({lat}, {lon})...")
                    env = PlantEnv.from_site(lat, lon, "2024-12-01", "2025-12-31")
                controller = CONTROLLERS[cname](curves)
                summary = run_episode(
                    controller, env, month, args.days,
                    series_path=RESULTS_DIR / "series" / f"{key}.json.gz",
                )
                summary |= {"site": site, "lat": lat, "lon": lon, "month": month,
                            "controller": cname, "days": args.days}
                out.write_text(json.dumps(summary, indent=1))
                print(f"[{done}/{total}] {key}: {summary['methane_kg']:.0f} kg "
                      f"({summary['wall_seconds']}s)")

    print("\nsweep complete")


if __name__ == "__main__":
    main()
