"""Subsystem models: state machines driven by the operating-curves yaml.

Fidelity is operating-curve level (the brief's "simplified operating curves"),
not CFD: each unit has discrete modes (off / starting / on / standby), a load
range, start-up behaviour, and standby costs. The kiln adds a continuous
thermal state - its inertia is the plant's cheapest energy store.

All parameters flow from curves/rivan-v1.yaml; nothing numeric lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CURVES_PATH = Path(__file__).resolve().parent.parent / "curves" / "rivan-v1.yaml"

OFF, STARTING, ON, STANDBY = "off", "starting", "on", "standby"


def load_curves(path: Path = CURVES_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text())


def v(node: dict, *path: str) -> float:
    """Dig out a parameter value: v(curves, 'dac', 'kiln_temp_c')."""
    for key in path:
        node = node[key]
    return node["value"]


@dataclass
class Store:
    """A buffer: H2 tank, CO2 gas holder, CaCO3 silo, or battery energy."""

    capacity: float
    level: float = 0.0

    def add(self, amount: float) -> float:
        """Add up to capacity; returns the amount that did NOT fit."""
        space = self.capacity - self.level
        accepted = min(amount, space)
        self.level += accepted
        return amount - accepted

    def draw(self, amount: float) -> float:
        """Withdraw up to level; returns the amount actually delivered."""
        taken = min(amount, self.level)
        self.level -= taken
        return taken


@dataclass
class Unit:
    """Generic mode/state machine with cold-start delay."""

    name: str
    rated_kw: float
    min_load_frac: float
    cold_start_h: float
    standby_power_frac: float
    start_draw_frac: float
    mode: str = OFF
    start_progress_h: float = 0.0
    fault_capacity_frac: float = 1.0  # faults scale effective capacity

    def command(self, target: str) -> None:
        if target == self.mode or (target == ON and self.mode == STARTING):
            return
        if target == ON:
            if self.mode == STANDBY:
                self.mode = ON  # hot start: instant at hourly resolution
            else:
                self.mode, self.start_progress_h = STARTING, 0.0
        elif target in (STANDBY, OFF):
            if target == STANDBY and self.mode == OFF:
                return  # cannot standby from cold - stay off
            self.mode = target

    def tick_start(self, dt: float) -> None:
        if self.mode == STARTING:
            self.start_progress_h += dt
            if self.start_progress_h >= self.cold_start_h:
                self.mode = ON

    def overhead_kw(self) -> float:
        """Power the unit draws in non-producing modes."""
        if self.mode == STARTING:
            return self.rated_kw * self.start_draw_frac
        if self.mode == STANDBY:
            return self.rated_kw * self.standby_power_frac
        return 0.0

    def usable_kw(self, load_frac: float) -> float:
        """Producing power draw for a commanded load, honouring min load."""
        if self.mode != ON or load_frac <= 0:
            return 0.0
        load = max(self.min_load_frac, min(1.0, load_frac))
        return self.rated_kw * load * self.fault_capacity_frac


@dataclass
class Kiln(Unit):
    """Calciner with continuous thermal state (0 = cold, 1 = 900C).

    Heating uses full commanded power; processing CaCO3 requires temp >= hot
    threshold. Unpowered, temperature decays with time constant tau - that
    decay is what pre-charging before a storm is fighting.
    """

    temp_frac: float = 0.0
    tau_h: float = 6.0
    heat_loss_frac: float = 0.05
    hot_threshold: float = 0.9

    def thermal_step(self, power_kw: float, dt: float) -> None:
        if power_kw > 0:
            heat_in = power_kw / self.rated_kw if self.rated_kw else 0.0
            delta = dt * (heat_in - self.heat_loss_frac * self.temp_frac) / self.cold_start_h
        else:
            delta = -dt * self.temp_frac / self.tau_h
        self.temp_frac = max(0.0, min(1.0, self.temp_frac + delta))

    @property
    def is_hot(self) -> bool:
        return self.temp_frac >= self.hot_threshold


def build_units(curves: dict) -> dict:
    """Instantiate all units from the curves file."""
    sizing = curves["plant_sizing"]
    elec = curves["electrolyser"]
    dac = curves["dac"]
    sab = curves["sabatier"]

    return {
        "electrolyser": Unit(
            name="electrolyser",
            rated_kw=v(sizing, "electrolyser_kw"),
            min_load_frac=v(elec, "min_load_frac"),
            cold_start_h=v(elec, "cold_start_h"),
            standby_power_frac=v(elec, "hot_standby_power_frac"),
            start_draw_frac=v(elec, "start_draw_frac"),
        ),
        "absorber": Unit(
            name="absorber",
            rated_kw=v(sizing, "dac_absorber_kw"),
            min_load_frac=0.0,
            cold_start_h=0.0,
            standby_power_frac=0.0,
            start_draw_frac=0.0,
            mode=ON,
        ),
        "kiln": Kiln(
            name="kiln",
            rated_kw=v(sizing, "dac_kiln_kw"),
            min_load_frac=v(dac, "min_load_frac"),
            cold_start_h=v(dac, "kiln_cold_start_h"),
            standby_power_frac=v(dac, "kiln_heat_loss_frac"),
            start_draw_frac=1.0,
            tau_h=v(dac, "kiln_thermal_tau_h"),
            heat_loss_frac=v(dac, "kiln_heat_loss_frac"),
        ),
        "sabatier": Unit(
            name="sabatier",
            rated_kw=v(sab, "heater_kw"),
            min_load_frac=v(sab, "turndown_min_frac"),
            cold_start_h=v(sab, "cold_start_h"),
            standby_power_frac=v(sab, "hot_standby_power_frac"),
            start_draw_frac=1.0,
        ),
    }


@dataclass
class Stores:
    h2: Store
    co2: Store
    silo: Store  # CaCO3 measured in embodied kg CO2
    battery_kwh: Store

    @classmethod
    def from_curves(cls, curves: dict) -> Stores:
        sizing = curves["plant_sizing"]
        stoich = curves["stoichiometry"]
        steady_co2_kg_h = v(sizing, "sabatier_capacity_kg_ch4_per_h") * v(stoich, "kg_co2_per_kg_ch4")
        return cls(
            h2=Store(v(sizing, "h2_buffer_kg")),
            co2=Store(v(sizing, "co2_buffer_kg")),
            silo=Store(v(sizing, "silo_hours_cao") * steady_co2_kg_h),
            battery_kwh=Store(v(sizing, "battery_kwh")),
        )


@dataclass
class FaultState:
    """Active faults, applied by Plant before each step. Phase 4 populates."""

    kiln_heater_failed: bool = False
    electrolyser_capacity_frac: float = 1.0
    irradiance_sensor_bias: float = 0.0
    h2_valve_stuck: bool = False
    solar_soiling_frac: float = 1.0
    log: list = field(default_factory=list)
