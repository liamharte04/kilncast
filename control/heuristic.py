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


class RuleBasedTuned:
    """The strongest hand-rulebook we could write - the fair fight for MPC.

    The naive baseline's dominant loss is misallocation: a 220 kW kiln run
    flat-out calcines ~183 kg/h CO2 into a 150 kg buffer feeding a reactor
    that needs ~63 kg/h, venting the rest, while the electrolyser is starved
    of the power the kiln wasted. This rulebook fixes exactly that:

    1. Kiln load matches stoichiometric demand (plus a silo top-up term),
       freeing power for the electrolyser.
    2. Electrolyser takes the remainder of the sky.
    3. Reactor paced across the night like RuleBased.

    Published so the MPC uplift is reported against BOTH a naive operator and
    a well-tuned rulebook - not a strawman.
    """

    name = "heuristic-tuned"

    def __init__(self, curves: dict):
        self.inner = RuleBased(curves)
        sab_cap = v(curves, "plant_sizing", "sabatier_capacity_kg_ch4_per_h")
        co2_per = v(curves, "stoichiometry", "kg_co2_per_kg_ch4")
        e_kiln = v(curves, "dac", "kiln_energy_kwh_per_t") / 1000.0
        kiln_kw = v(curves, "plant_sizing", "dac_kiln_kw")
        # steady-state kiln load that exactly feeds the reactor
        self.kiln_match = (sab_cap * co2_per * e_kiln) / kiln_kw
        self.silo_cap = v(curves, "plant_sizing", "silo_hours_cao") * sab_cap * co2_per

    def act(self, obs: dict, forecast) -> Action:
        action = self.inner.act(obs, forecast)
        ghi = obs["ghi_now"]
        if ghi > SUN_THRESHOLD_WM2:
            if obs["kiln_temp_frac"] < 0.9:
                # a demand-matched load never reaches calcination temperature
                # from cold - heat flat out first, throttle once hot
                action.kiln_load = 1.0
            else:
                silo_frac = obs["stores"]["silo_kg"] / self.silo_cap
                top_up = 0.25 if silo_frac < 0.5 else 0.0
                # never below the hold power or the kiln cools mid-day
                action.kiln_load = min(1.0, max(self.kiln_match + top_up, 0.35))
            action.electrolyser_load = 1.0  # remainder of the sky, plant clips
        return action


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
