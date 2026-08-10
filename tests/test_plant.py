"""Plant conservation and constraint tests - the physics can't be cheated."""

import math
import random

import pytest

from sim.economics import summarise, total_capex_gbp
from sim.plant import Action, EpisodeLog, Plant
from sim.subsystems import load_curves


def sine_day(hour: int, peak: float = 800.0) -> float:
    """Synthetic irradiance: daylight 6..18, sine-shaped."""
    h = hour % 24
    return peak * math.sin(math.pi * (h - 6) / 12) if 6 <= h <= 18 else 0.0


ALL_ON = Action(
    electrolyser_mode="on", electrolyser_load=1.0,
    kiln_mode="on", kiln_load=1.0,
    absorber_load=1.0,
    sabatier_mode="on", sabatier_load=1.0,
)


@pytest.fixture
def plant():
    return Plant(load_curves())


def run_hours(plant, hours, action_fn, start_hour=0):
    results = []
    for t in range(hours):
        ghi = sine_day(start_hour + t)
        results.append(plant.step(action_fn(t), ghi=ghi, t_amb=15.0))
    return results


def test_energy_balance_under_random_commands(plant):
    rng = random.Random(42)
    modes = ["on", "standby", "off"]

    def rand_action(_):
        return Action(
            electrolyser_mode=rng.choice(modes), electrolyser_load=rng.random(),
            kiln_mode=rng.choice(modes), kiln_load=rng.random(),
            absorber_load=rng.random(),
            sabatier_mode=rng.choice(modes), sabatier_load=rng.random(),
            battery_charge_kw=rng.uniform(-100, 100),
        )

    for r in run_hours(plant, 300, rand_action):
        assert r.energy_balance_error_kwh < 1e-6, r
        for name, level in r.stores.items():
            assert level >= -1e-9, f"{name} went negative"


def test_mass_conservation_h2_and_co2(plant):
    initial_h2 = plant.stores.h2.level
    initial_silo = plant.stores.silo.level
    initial_co2 = plant.stores.co2.level
    results = run_hours(plant, 72, lambda _: ALL_ON)

    made = sum(r.flows["h2_made"] for r in results)
    wasted = sum(r.flows["h2_wasted"] for r in results)
    consumed = sum(r.flows["h2_consumed"] for r in results)
    assert abs(initial_h2 + made - wasted - consumed - plant.stores.h2.level) < 1e-6

    captured = sum(r.flows["co2_captured"] for r in results)
    cap_wasted = sum(r.flows["co2_capture_wasted"] for r in results)
    calcined = sum(r.flows["co2_calcined"] for r in results)
    assert abs(initial_silo + captured - cap_wasted - calcined - plant.stores.silo.level) < 1e-6

    vented = sum(r.flows["co2_vented"] for r in results)
    co2_used = sum(r.flows["co2_consumed"] for r in results)
    assert abs(initial_co2 + calcined - vented - co2_used - plant.stores.co2.level) < 1e-6


def test_sunny_days_produce_methane(plant):
    results = run_hours(plant, 96, lambda _: ALL_ON)
    log = EpisodeLog()
    for r in results:
        log.add(r)
    assert log.methane_kg > 100, "four sunny days should make real methane"
    summary = summarise(log, plant.curves, hours=96)
    assert summary["gbp_per_kg_ch4"] < 100


def test_island_constraint_no_battery(plant):
    for r in run_hours(plant, 96, lambda _: ALL_ON):
        assert r.used_kw <= r.solar_kw + 1e-6, "no grid: usage cannot exceed solar"


def test_cold_starts_delay_production(plant):
    r1 = plant.step(ALL_ON, ghi=800, t_amb=15)
    assert r1.modes["electrolyser"] == "starting"
    assert r1.flows["h2_made"] == 0.0
    r2 = plant.step(ALL_ON, ghi=800, t_amb=15)
    assert r2.modes["electrolyser"] == "on"
    assert r2.flows["h2_made"] > 0
    assert r2.kiln_temp_frac < 0.9, "kiln is thermally massive - hours to heat"


def test_kiln_cools_when_unpowered(plant):
    for _ in range(12):
        plant.step(ALL_ON, ghi=800, t_amb=15)
    hot = plant.units["kiln"].temp_frac
    assert hot > 0.8
    off = Action()
    for _ in range(6):
        plant.step(off, ghi=0, t_amb=5)
    assert plant.units["kiln"].temp_frac < hot - 0.3, "unpowered kiln must decay"


def test_kiln_fault_forces_shutdown(plant):
    for _ in range(12):
        plant.step(ALL_ON, ghi=800, t_amb=15)
    plant.faults.kiln_heater_failed = True
    r = plant.step(ALL_ON, ghi=800, t_amb=15)
    assert r.modes["kiln"] == "off"


def test_battery_discharge_not_gated_by_charge_command():
    """A forecast bust during a planned-charge hour must still be covered by
    the battery - discharge is automatic, never blocked by the action sign."""
    import copy

    from sim.subsystems import load_curves as lc

    curves = copy.deepcopy(lc())
    curves["plant_sizing"]["battery_kwh"]["value"] = 500
    plant = Plant(curves)
    plant.stores.battery_kwh.level = 400.0
    # night (no solar), electrolyser warm and commanded on, action REQUESTS charge
    plant.units["electrolyser"].mode = "on"
    act = Action(electrolyser_mode="on", electrolyser_load=0.5, battery_charge_kw=50.0)
    r = plant.step(act, ghi=0.0, t_amb=10.0)
    assert r.flows["h2_made"] > 0, "battery should power the electrolyser despite charge request"
    assert plant.stores.battery_kwh.level < 400.0


def test_capex_in_plausible_range(plant):
    total = total_capex_gbp(plant.curves)
    assert 300_000 < total < 900_000, f"1MW plant capex {total} looks wrong"
