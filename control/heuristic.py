"""Rule-based scheduler: hand-written operator wisdom, forecast-aware.

Three rules beyond the baseline, each targeting a failure the baseline's
limiting-subsystem histogram exposes:

1. PACE THE REACTOR overnight: instead of draining the H2 buffer flat out
   after sunset, spread it across the dark hours so the reactor never
   cold-starves (H2 was limiting 95% of baseline hours).
2. MATCH LOADS TO THE SKY: command load fractions proportional to measured
   irradiance so the plant's shed-priority clipping isn't doing the control.
3. KILN THERMAL STRATEGY from the forecast: hold temperature overnight only
   when tomorrow looks sunny (reheating from cold costs more than holding),
   let it cool before multi-day dark spells.
"""

from __future__ import annotations

import pandas as pd

from sim.plant import Action
from sim.subsystems import v

SUN_THRESHOLD_WM2 = 50.0
GOOD_SUN_WM2 = 300.0


class RuleBased:
    name = "heuristic-rules"

    def __init__(self, curves: dict):
        self.curves = curves
        self.sab_cap = v(curves, "plant_sizing", "sabatier_capacity_kg_ch4_per_h")
        self.h2_per_ch4 = v(curves, "stoichiometry", "kg_h2_per_kg_ch4")
        self.h2_buffer_kg = v(curves, "plant_sizing", "h2_buffer_kg")

    def _hours_to_next_sun(self, obs: dict, forecast: pd.DataFrame) -> float:
        future = forecast[forecast.index > obs["time"]]
        sunny = future[future["shortwave_radiation"] > GOOD_SUN_WM2]
        if sunny.empty:
            return 24.0
        return max(1.0, (sunny.index[0] - obs["time"]).total_seconds() / 3600.0)

    def act(self, obs: dict, forecast: pd.DataFrame) -> Action:
        ghi = obs["ghi_now"]
        sunny = ghi > SUN_THRESHOLD_WM2
        h2 = obs["stores"]["h2_kg"]

        # rule 1: pace the reactor across the gap to the next generation window
        gap_h = self._hours_to_next_sun(obs, forecast)
        paced = h2 / (gap_h * self.sab_cap * self.h2_per_ch4)
        sab_load = min(1.0, max(0.15, paced)) if h2 > 0.05 * self.h2_buffer_kg else 0.0

        # rule 2: loads follow the sky
        elec_load = min(1.0, max(0.25, ghi / 900.0)) if sunny else 0.0
        kiln_load = 1.0 if ghi > GOOD_SUN_WM2 else (0.4 if sunny else 0.0)

        # rule 3: kiln holds heat overnight only if tomorrow is worth it
        next24 = forecast[forecast.index > obs["time"]].head(24)
        tomorrow_sunny = next24["shortwave_radiation"].max() > GOOD_SUN_WM2
        kiln_mode = "on" if sunny else ("standby" if tomorrow_sunny else "off")

        return Action(
            electrolyser_mode="on" if sunny else "standby",
            electrolyser_load=elec_load,
            kiln_mode=kiln_mode,
            kiln_load=kiln_load,
            absorber_load=1.0 if sunny else 0.0,
            sabatier_mode="on" if sab_load > 0 else "standby",
            sabatier_load=sab_load,
            battery_charge_kw=150.0 if ghi > 600 else -1000.0,
        )
