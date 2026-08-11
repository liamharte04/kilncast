"""Verify the kiln-fault result before claiming it.

The fault run (kiln dead days 10-13) lost only ~0.5% of monthly output vs the
no-fault run. Plausible mechanism: the CaCO3 silo carries ~48h of feedstock
and the kiln is ~2.7x oversized, so the plant coasts through the outage and
catches up after repair. This script replays both series hour by hour across
the fault window and reports exactly what buffered the loss - or falsifies
the story.

Usage: uv run python scripts/verify_kiln_fault.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.common import RESULTS_DIR, series_frame

FAULT_D0, FAULT_D1 = 10, 13


def main() -> None:
    fault = series_frame(RESULTS_DIR / "series" / "fault_kiln_heater__mpc.json.gz")
    clean = series_frame(RESULTS_DIR / "series" / "fault_none__mpc.json.gz")

    for name, df in [("clean", clean), ("fault", fault)]:
        df["day"] = (df.index - df.index[0]).days

    print("Daily methane (kg), clean vs fault run:")
    daily_c = clean.groupby("day")["methane_kg"].sum()
    daily_f = fault.groupby("day")["methane_kg"].sum()
    print(f"{'day':>4}{'clean':>8}{'fault':>8}{'delta':>8}")
    for d in range(28):
        mark = " <- FAULT" if FAULT_D0 <= d < FAULT_D1 else (" <- repair" if d == FAULT_D1 else "")
        print(f"{d:>4}{daily_c.get(d, 0):>8.0f}{daily_f.get(d, 0):>8.0f}"
              f"{daily_f.get(d, 0) - daily_c.get(d, 0):>+8.0f}{mark}")

    window = slice(FAULT_D0 - 1, FAULT_D1 + 3)
    print("\nFault-window detail (fault run): silo, co2 buffer, kiln temp, methane")
    sub = fault[(fault["day"] >= FAULT_D0 - 1) & (fault["day"] <= FAULT_D1 + 2)]
    daily = sub.groupby("day").agg(
        methane=("methane_kg", "sum"),
        silo_start=("silo_kg", "first"),
        silo_end=("silo_kg", "last"),
        co2_end=("co2_kg", "last"),
        kiln_T_max=("kiln_temp", "max"),
        kiln_on_h=("mode_kiln", lambda s: (s == "on").sum()),
    )
    print(daily.round(1).to_string())

    lost = daily_c.loc[FAULT_D0:FAULT_D1 - 1].sum() - daily_f.loc[FAULT_D0:FAULT_D1 - 1].sum()
    caught_up = (daily_f.loc[FAULT_D1:].sum() - daily_c.loc[FAULT_D1:].sum())
    print(f"\nProduction lost during fault window: {lost:.0f} kg")
    print(f"Production made up after repair (fault minus clean): {caught_up:+.0f} kg")
    print(f"Net monthly impact: {daily_f.sum() - daily_c.sum():+.0f} kg "
          f"({100 * (daily_f.sum() - daily_c.sum()) / daily_c.sum():+.1f}%)")

    verdict = "CATCH-UP STORY CONFIRMED" if lost > 20 and caught_up > 0.5 * lost else (
        "WINDOW LOSS WAS SMALL - check weather during fault days" if lost <= 20
        else "STORY NOT CONFIRMED - net loss without catch-up")
    print("\nVERDICT:", verdict)


if __name__ == "__main__":
    main()
