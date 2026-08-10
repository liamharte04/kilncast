"""Episode environment tests on synthetic weather - offline."""

import math

import pandas as pd
import pytest

from sim.env import PlantEnv
from sim.plant import Action
from sim.weather import FORECAST_VARS

ALL_ON = Action(
    electrolyser_mode="on", electrolyser_load=1.0,
    kiln_mode="on", kiln_load=1.0,
    absorber_load=1.0,
    sabatier_mode="on", sabatier_load=1.0,
)


@pytest.fixture
def env():
    idx = pd.date_range("2025-01-01", periods=90 * 24, freq="h")
    ghi = [max(0.0, 700 * math.sin(math.pi * (t.hour - 6) / 12)) if 6 <= t.hour <= 18 else 0.0
           for t in idx]
    actuals = pd.DataFrame(
        {"shortwave_radiation": ghi, "temperature_2m": 15.0, "cloud_cover": 20.0,
         "direct_normal_irradiance": ghi},
        index=idx,
    )
    issued = pd.DataFrame(index=idx)
    for var in FORECAST_VARS:
        for lead in range(1, 8):
            issued[f"{var}_lead{lead}"] = actuals[var] * (1 - 0.02 * lead)
    return PlantEnv(actuals, issued)


def test_episode_runs_and_summarises(env):
    env.reset("2025-02-01", days=3)
    steps = 0
    while not env.done:
        _, result = env.step(ALL_ON)
        assert result.energy_balance_error_kwh < 1e-6
        steps += 1
    assert steps == 72
    s = env.summary()
    assert s["methane_kg"] > 0
    assert "limiting_subsystem_hours" in s


def test_forecast_reissued_at_midnight(env):
    env.reset("2025-02-01", days=2)
    first_issue = env.forecast_now.index[0]
    for _ in range(24):
        env.step(ALL_ON)
    assert env.forecast_now.index[0] == first_issue + pd.Timedelta(days=1)
    assert set(env.forecast_now["source"].unique()) == {"issued", "climatology"}


def test_controller_cannot_see_future(env):
    env.reset("2025-02-01", days=2)
    obs = env._obs()
    assert "actuals" not in obs
    fc = env.forecast_now
    assert len(fc) == 240, "10-day horizon per the brief"
    assert (fc[fc["lead_days"] <= 7]["source"] == "issued").all()
