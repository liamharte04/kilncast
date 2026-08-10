"""Episode environment: plant + weather + economics behind a gym-style API.

    env = PlantEnv.from_site(37.39, -5.99, "2025-01-01", "2025-12-31")
    obs = env.reset("2025-03-01", days=30)
    while not env.done:
        obs, result = env.step(controller.act(obs, env.forecast_now))
    print(env.summary())

Controllers see ONLY ``obs`` and ``forecast_now`` (the 10-day forecast as
available at the most recent midnight - real archived leads 1-7, labelled
climatology beyond). Actual future weather is never exposed; the oracle
controller in Phase 3 gets it via an explicit, loudly-named method instead.
"""

from __future__ import annotations

import pandas as pd

from sim.economics import summarise
from sim.plant import Action, EpisodeLog, Plant
from sim.weather import assemble_forecast, load_actuals, load_issued


class PlantEnv:
    def __init__(self, actuals: pd.DataFrame, issued: pd.DataFrame, curves: dict | None = None):
        self.actuals = actuals
        self.issued = issued
        self.curves_override = curves
        self.plant: Plant | None = None
        self.log = EpisodeLog()
        self.t: pd.Timestamp | None = None
        self.end: pd.Timestamp | None = None
        self.forecast_now: pd.DataFrame | None = None

    @classmethod
    def from_site(cls, lat: float, lon: float, start: str, end: str, curves: dict | None = None):
        return cls(load_actuals(lat, lon, start, end), load_issued(lat, lon, start, end), curves)

    # ------------------------------------------------------------------ api
    def reset(self, episode_start: str, days: int = 30) -> dict:
        self.plant = Plant(self.curves_override)
        self.log = EpisodeLog()
        self.t = pd.Timestamp(episode_start).normalize()
        self.log_start = self.t
        self.end = self.t + pd.Timedelta(days=days)
        self._issue_forecast()
        return self._obs()

    @property
    def done(self) -> bool:
        return self.t is None or self.t >= self.end

    def step(self, action: Action):
        assert self.plant is not None and not self.done
        row = self.actuals.loc[self.t]
        result = self.plant.step(
            action,
            ghi=float(row["shortwave_radiation"]) + self.plant.faults.irradiance_sensor_bias,
            t_amb=float(row["temperature_2m"]),
        )
        self.log.add(result)
        self.t = self.t + pd.Timedelta(hours=1)
        if self.t == self.t.normalize() and not self.done:
            self._issue_forecast()  # new forecast each midnight - daily re-plan cadence
        return self._obs(), result

    def summary(self) -> dict:
        hours = (self.t - self.log_start).total_seconds() / 3600.0
        return summarise(self.log, self.plant.curves, hours=hours)

    # -------------------------------------------------------------- internals
    def _issue_forecast(self) -> None:
        self.forecast_now = assemble_forecast(self.issued, self.actuals, self.t.normalize())

    def _obs(self) -> dict:
        p = self.plant
        return {
            "time": self.t,
            "stores": {
                "h2_kg": p.stores.h2.level,
                "co2_kg": p.stores.co2.level,
                "silo_kg": p.stores.silo.level,
                "battery_kwh": p.stores.battery_kwh.level,
            },
            "modes": {name: u.mode for name, u in p.units.items()},
            "kiln_temp_frac": p.units["kiln"].temp_frac,
        }

    # Oracle access - deliberately loud. Only control/oracle.py may call this.
    def UNFAIR_actual_weather(self, start: pd.Timestamp, hours: int) -> pd.DataFrame:
        return self.actuals.loc[start : start + pd.Timedelta(hours=hours - 1)]
