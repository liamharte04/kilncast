"""Offline tests for forecast assembly - no network required."""

import numpy as np
import pandas as pd
import pytest

from sim.weather import FORECAST_VARS, assemble_forecast, trailing_climatology


@pytest.fixture
def synthetic():
    """60 days of actuals (constant 100) and issued forecasts where the value
    in each lead column equals the lead number - so assembly is verifiable."""
    idx = pd.date_range("2025-01-01", periods=60 * 24, freq="h")
    actuals = pd.DataFrame({var: 100.0 for var in FORECAST_VARS}, index=idx)
    issued = pd.DataFrame(index=idx)
    for var in FORECAST_VARS:
        for lead in range(1, 8):
            issued[f"{var}_lead{lead}"] = float(lead)
    return issued, actuals


def test_lead_selection(synthetic):
    issued, actuals = synthetic
    issue = pd.Timestamp("2025-02-10")
    fc = assemble_forecast(issued, actuals, issue, horizon_days=10)

    assert len(fc) == 240
    # issue-day hour uses lead 1; hour on day+3 uses lead 4 column
    assert fc.iloc[5]["shortwave_radiation"] == 1.0
    assert fc.loc[issue + pd.Timedelta(days=3, hours=12), "shortwave_radiation"] == 4.0
    # beyond day 7: climatology of constant actuals = 100
    tail = fc[fc["lead_days"] > 7]
    assert (tail["source"] == "climatology").all()
    assert np.allclose(tail["shortwave_radiation"], 100.0)
    # within archive: marked issued
    assert (fc[fc["lead_days"] <= 7]["source"] == "issued").all()


def test_climatology_no_lookahead(synthetic):
    _, actuals = synthetic
    # poison the future: if climatology used post-issue data the mean would move
    issue = pd.Timestamp("2025-02-10")
    actuals.loc[issue:, "shortwave_radiation"] = 9999.0
    climo = trailing_climatology(actuals, issue, "shortwave_radiation")
    assert climo.max() <= 101.0


def test_issue_time_must_be_midnight(synthetic):
    issued, actuals = synthetic
    with pytest.raises(ValueError):
        assemble_forecast(issued, actuals, pd.Timestamp("2025-02-10 06:00"))
