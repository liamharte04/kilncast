"""Weather data access: ERA5 actuals and as-issued archived forecasts.

Two Open-Meteo endpoints (verified in Phase 0):

- Archive API: hourly ERA5 actuals, 100% coverage.
- Previous Runs API: forecasts exactly as issued, lead days 1..7. Lead days
  8-10 do not exist in the archive, so ``assemble_forecast`` extends the
  horizon with a trailing climatology computed ONLY from data before the
  issue time (no lookahead leakage). This is a documented assumption of the
  benchmark: real forecast error to 7 days, climatology beyond.

All fetches cache to ``data/cache/`` as csv.gz keyed by endpoint, location
and date range, so full-year sweeps only hit the network once.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
MAX_LEAD_DAYS = 7
ACTUAL_VARS = ["shortwave_radiation", "direct_normal_irradiance", "temperature_2m", "cloud_cover"]
FORECAST_VARS = ["shortwave_radiation", "temperature_2m"]


def _fetch_json(url: str, params: dict, retries: int = 4) -> dict:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=90)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("unreachable")


def _cached(name: str, fetch) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.csv.gz"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    df = fetch()
    df.to_csv(path)
    return df


def _hourly_frame(payload: dict) -> pd.DataFrame:
    df = pd.DataFrame(payload["hourly"])
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")


def load_actuals(lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    """Hourly ERA5 actuals (UTC): irradiance, DNI, temperature, cloud cover."""

    def fetch() -> pd.DataFrame:
        return _hourly_frame(
            _fetch_json(
                "https://archive-api.open-meteo.com/v1/archive",
                {
                    "latitude": lat,
                    "longitude": lon,
                    "timezone": "UTC",
                    "start_date": start,
                    "end_date": end,
                    "hourly": ",".join(ACTUAL_VARS),
                },
            )
        )

    return _cached(f"actual_{lat}_{lon}_{start}_{end}", fetch)


def load_issued(lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    """As-issued forecasts. Columns ``{var}_lead{n}`` for n in 1..7.

    Value at row time t, column lead n = what the model predicted for hour t,
    n days before t.
    """

    def fetch() -> pd.DataFrame:
        hourly = ",".join(
            f"{var}_previous_day{lead}"
            for var in FORECAST_VARS
            for lead in range(1, MAX_LEAD_DAYS + 1)
        )
        df = _hourly_frame(
            _fetch_json(
                "https://previous-runs-api.open-meteo.com/v1/forecast",
                {
                    "latitude": lat,
                    "longitude": lon,
                    "timezone": "UTC",
                    "start_date": start,
                    "end_date": end,
                    "hourly": hourly,
                },
            )
        )
        return df.rename(
            columns={
                f"{var}_previous_day{lead}": f"{var}_lead{lead}"
                for var in FORECAST_VARS
                for lead in range(1, MAX_LEAD_DAYS + 1)
            }
        )

    return _cached(f"issued_{lat}_{lon}_{start}_{end}", fetch)


def trailing_climatology(
    actuals: pd.DataFrame, issue_time: pd.Timestamp, var: str, window_days: int = 30
) -> pd.Series:
    """Mean value per hour-of-day over the ``window_days`` before issue_time.

    Uses strictly pre-issue data - the honest stand-in for forecast horizons
    beyond the archive's 7-day lead limit.
    """
    start = issue_time - pd.Timedelta(days=window_days)
    past = actuals.loc[start : issue_time - pd.Timedelta(hours=1), var]
    return past.groupby(past.index.hour).mean()


def assemble_forecast(
    issued: pd.DataFrame,
    actuals: pd.DataFrame,
    issue_time: pd.Timestamp,
    horizon_days: int = 10,
) -> pd.DataFrame:
    """The forecast available at ``issue_time`` (midnight UTC), per the brief.

    Returns an hourly frame covering ``horizon_days`` with columns for each
    forecast variable plus ``lead_days`` and ``source`` ('issued' for lead
    1..7, 'climatology' beyond). Hours on the issue day itself use the lead-1
    column (the freshest archived run - slightly conservative).
    """
    if issue_time != issue_time.normalize():
        raise ValueError("issue_time must be midnight UTC (daily re-plan cadence)")

    hours = pd.date_range(issue_time, issue_time + pd.Timedelta(days=horizon_days), freq="h", inclusive="left")
    out = pd.DataFrame(index=hours)
    lead = ((hours - issue_time).days + 1).astype(int)  # issue-day hours -> lead 1
    out["lead_days"] = lead

    climos = {var: trailing_climatology(actuals, issue_time, var) for var in FORECAST_VARS}
    for var in FORECAST_VARS:
        vals = pd.Series(index=hours, dtype=float)
        for n in range(1, MAX_LEAD_DAYS + 1):
            sel = out["lead_days"] == n
            vals.loc[sel] = issued[f"{var}_lead{n}"].reindex(hours[sel]).values
        beyond = out["lead_days"] > MAX_LEAD_DAYS
        vals.loc[beyond] = [float(climos[var].get(h.hour, float("nan"))) for h in hours[beyond]]
        if vals.loc[beyond].isna().any():
            raise ValueError(
                "climatology tail has no history - load actuals starting at least "
                "30 days before the first episode (e.g. fetch from the prior month)"
            )
        out[var] = vals

    out["source"] = "issued"
    out.loc[out["lead_days"] > MAX_LEAD_DAYS, "source"] = "climatology"
    return out
