"""The named baseline every headline number is measured against.

SunFollower is the plausible naive operator: run every power-consuming unit
flat out whenever the sun is up, drop them to hot standby at night, and keep
the reactor on around the clock (it is exothermic once running). No forecast,
no pacing, no thermal strategy - exactly what you would wire up first.
"""

from __future__ import annotations

from sim.plant import Action

SUN_THRESHOLD_WM2 = 50.0


class SunFollower:
    name = "baseline-sun-follower"

    def __init__(self, curves: dict):
        self.curves = curves

    def act(self, obs: dict, forecast=None) -> Action:
        sunny = obs["ghi_now"] > SUN_THRESHOLD_WM2
        return Action(
            electrolyser_mode="on" if sunny else "standby",
            electrolyser_load=1.0 if sunny else 0.0,
            kiln_mode="on" if sunny else "standby",
            kiln_load=1.0 if sunny else 0.0,
            absorber_load=1.0 if sunny else 0.0,
            sabatier_mode="on",
            sabatier_load=1.0,
            battery_charge_kw=100.0 if sunny else -1000.0,
        )
