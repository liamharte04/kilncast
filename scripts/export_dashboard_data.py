"""Export simulation results into dashboard/data/ as plain JSON.

Produces:
- manifest.json: episodes (site/month/controller) with summary numbers, the
  storm-week winner, fault scenarios, and the sweep table
- ep_{key}.json: hourly arrays per episode (compact, column-oriented)
- forecast overlays for the storm episode (lead-1 and lead-7 as-issued)

Usage: uv run python scripts/export_dashboard_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from experiments.common import RESULTS_DIR, load_sites, series_frame
from sim.weather import load_issued

DASH_DATA = Path(__file__).resolve().parent.parent / "dashboard" / "data"

COLS = ["ghi", "solar_kw", "used_kw", "methane_kg", "kiln_temp", "limiting",
        "h2_kg", "co2_kg", "silo_kg", "battery_kwh", "mode_kiln", "mode_electrolyser",
        "mode_sabatier", "curtailed_kwh"]


def export_series(path: Path, key: str) -> dict | None:
    if not path.exists():
        return None
    df = series_frame(path)
    out = {"time": [t.isoformat() for t in df.index]}
    for c in COLS:
        if c in df.columns:
            out[c] = df[c].tolist()
    out["ch4_cum"] = df["methane_kg"].cumsum().round(1).tolist()
    (DASH_DATA / f"ep_{key}.json").write_text(json.dumps(out))
    return {"key": key, "hours": len(df)}


def main() -> None:
    DASH_DATA.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"episodes": [], "faults": [], "sweep": [], "storm": None}

    # sweep episodes (mpc + baseline pairs)
    for p in sorted((RESULTS_DIR / "sweep").glob("*.json")):
        s = json.loads(p.read_text())
        manifest["sweep"].append(s)
        key = f"{s['site'].replace(' ', '_')}_{s['month']}_{s['controller']}"
        info = export_series(RESULTS_DIR / "series" / f"{key}.json.gz", key)
        if info:
            manifest["episodes"].append(info | {
                "site": s["site"], "month": s["month"], "controller": s["controller"],
                "methane_kg": s["methane_kg"],
            })

    # fault episodes (results/faults/<month>/<scenario>__<controller>.json,
    # plus legacy March files and the sunny-window bound run)
    for p in sorted((RESULTS_DIR / "faults").rglob("*.json")):
        s = json.loads(p.read_text())
        month = s.get("month", p.parent.name if p.parent.name != "faults" else "2025-03-01")
        key = f"fault_{month}_{p.stem}"
        candidates = [
            RESULTS_DIR / "series" / f"fault_{month}_{p.stem}.json.gz",
            RESULTS_DIR / "series" / f"fault_{p.stem}.json.gz",
            RESULTS_DIR / "series" / "fault_kiln_sunny__mpc.json.gz"
            if "sunny" in p.stem else Path("nonexistent"),
        ]
        src = next((c for c in candidates if c.exists()), None)
        info = export_series(src, key) if src else None
        if info:
            manifest["faults"].append(info | {
                "scenario": s.get("scenario", p.stem), "controller": s.get("controller", "mpc"),
                "month": month, "methane_kg": s["methane_kg"],
                "fault_window_days": s.get("fault_window_days"),
                "detection_events": s.get("detection_events", []),
            })

    # storm winner + forecast overlay
    storm_file = RESULTS_DIR / "storm_week.txt"
    if storm_file.exists():
        key, week_start = storm_file.read_text().strip().split(",")
        site_name = " ".join(key.split("_")[:2]).replace("_", " ")
        month = key.split("_")[-1]
        sites = load_sites()
        site_label = next((s for s in sites if s.replace(" ", "_") in key), None)
        manifest["storm"] = {"key": key, "week_start": week_start, "site": site_label}
        if site_label:
            lat, lon = sites[site_label]
            issued = load_issued(lat, lon, "2024-12-01", "2025-12-31")
            w0 = pd.Timestamp(week_start)
            window = issued.loc[w0 : w0 + pd.Timedelta(days=9)]
            manifest["storm"]["forecast"] = {
                "time": [t.isoformat() for t in window.index],
                "lead1": window["shortwave_radiation_lead1"].round(1).tolist(),
                "lead7": window["shortwave_radiation_lead7"].round(1).tolist(),
            }

    (DASH_DATA / "manifest.json").write_text(json.dumps(manifest))
    n = len(list(DASH_DATA.glob("ep_*.json")))
    print(f"exported {n} episode files + manifest "
          f"({len(manifest['sweep'])} sweep rows, {len(manifest['faults'])} fault runs)")
    print("storm:", manifest["storm"] and {k: manifest['storm'][k] for k in ('key', 'week_start', 'site')})


if __name__ == "__main__":
    main()
