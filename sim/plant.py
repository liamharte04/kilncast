"""The coupled plant: solar -> electrolyser -> H2 -> Sabatier <- CO2 <- DAC.

The controller PROPOSES (modes, loads, battery power); the plant ENFORCES.
Hard constraints - islanded power balance, min loads, buffer capacities, kiln
temperature, reactor feed stoichiometry - are applied here by clipping the
proposal, and every clip is logged. A controller can therefore never cheat
physics, which keeps all strategies comparable.

Step semantics (dt = 1 h): power is allocated in shed-last -> shed-first
order (overheads -> absorber -> kiln -> electrolyser -> battery charge);
battery discharge covers any granted load beyond solar. H2 compression energy
is treated as included in the electrolyser specific energy (see curves note).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sim.subsystems import (
    ON,
    STANDBY,
    STARTING,
    FaultState,
    Kiln,
    Stores,
    build_units,
    load_curves,
    v,
)


@dataclass
class Action:
    """Controller proposal for one step. Loads are fractions of rated."""

    electrolyser_mode: str = "off"
    electrolyser_load: float = 0.0
    kiln_mode: str = "off"
    kiln_load: float = 0.0
    absorber_load: float = 0.0
    sabatier_mode: str = "off"
    sabatier_load: float = 0.0
    battery_charge_kw: float = 0.0  # positive = charge cap from surplus; discharge is automatic


@dataclass
class StepResult:
    methane_kg: float
    solar_kw: float
    used_kw: float
    curtailed_kwh: float
    limiting_subsystem: str
    stores: dict
    modes: dict
    kiln_temp_frac: float
    clips: list
    energy_balance_error_kwh: float
    water_kg: float
    co2_vented_kg: float
    flows: dict = field(default_factory=dict)  # per-step mass flows for audits


class Plant:
    def __init__(self, curves: dict | None = None):
        self.curves = curves or load_curves()
        self.units = build_units(self.curves)
        self.stores = Stores.from_curves(self.curves)
        self.faults = FaultState()
        c = self.curves
        self.solar_kwp = v(c, "plant_sizing", "solar_capacity_kwp")
        self.derate = v(c, "solar", "system_derate")
        self.temp_coeff = v(c, "solar", "temp_coeff_per_c")
        self.e_h2 = v(c, "electrolyser", "specific_energy_kwh_per_kg_h2")
        self.e_absorb = v(c, "dac", "absorber_energy_kwh_per_t") / 1000.0  # kWh per kg CO2
        self.e_kiln = v(c, "dac", "kiln_energy_kwh_per_t") / 1000.0
        self.sab_cap = v(c, "plant_sizing", "sabatier_capacity_kg_ch4_per_h")
        self.conv = v(c, "sabatier", "conversion_frac")
        self.h2_per_ch4 = v(c, "stoichiometry", "kg_h2_per_kg_ch4")
        self.co2_per_ch4 = v(c, "stoichiometry", "kg_co2_per_kg_ch4")
        self.h2o_per_h2 = v(c, "stoichiometry", "kg_h2o_per_kg_h2")
        self.batt_eff = v(c, "battery", "round_trip_eff") ** 0.5  # per direction

    # ------------------------------------------------------------------ solar
    def solar_power_kw(self, ghi: float, t_amb: float) -> float:
        t_cell = t_amb + ghi / 800.0 * 25.0
        p = self.solar_kwp * (ghi / 1000.0) * self.derate * (1 + self.temp_coeff * (t_cell - 25.0))
        return max(0.0, p * self.faults.solar_soiling_frac)

    # ------------------------------------------------------------------- step
    def step(self, action: Action, ghi: float, t_amb: float, dt: float = 1.0) -> StepResult:
        clips: list[str] = []
        stores = self.stores
        elec = self.units["electrolyser"]
        kiln = self.units["kiln"]
        absorber = self.units["absorber"]
        sab = self.units["sabatier"]
        assert isinstance(kiln, Kiln)

        # -- faults modify effective behaviour before anything else
        elec.fault_capacity_frac = self.faults.electrolyser_capacity_frac
        kiln_available = not self.faults.kiln_heater_failed

        # -- mode transitions
        elec.command(action.electrolyser_mode)
        kiln.command(action.kiln_mode if kiln_available else "off")
        sab.command(action.sabatier_mode)
        for u in (elec, kiln, sab):
            u.tick_start(dt)

        solar_kw = self.solar_power_kw(ghi, t_amb)
        batt = stores.battery_kwh

        # -- power requests, highest priority first
        kiln_hold_req = kiln.overhead_kw() if (kiln_available and kiln.mode in (STARTING, STANDBY)) else 0.0
        overhead_req = elec.overhead_kw() + sab.overhead_kw()  # startup draws only
        absorber_req = absorber.rated_kw * max(0.0, min(1.0, action.absorber_load))
        kiln_req = kiln.usable_kw(action.kiln_load) if (kiln_available and kiln.mode == ON) else 0.0
        elec_req = elec.usable_kw(action.electrolyser_load)

        # discharge is ALWAYS available to cover deficits: charging draws only
        # from post-load surplus, so charge and discharge are mutually
        # exclusive per hour by construction and no controller knob is needed.
        # (Gating discharge on the action's sign meant a forecast bust during
        # a planned-charge hour shed loads while a full battery sat idle.)
        max_batt_delivery_kw = batt.level * self.batt_eff / dt
        available = solar_kw + max_batt_delivery_kw

        def take(req: float, label: str) -> float:
            nonlocal available
            grant = min(req, available)
            if grant < req - 1e-9:
                clips.append(f"{label}: shed {req - grant:.1f} kW (power shortfall)")
            available -= grant
            return grant

        overhead_kw = take(overhead_req, "overheads")
        kiln_hold_kw = take(kiln_hold_req, "kiln-hold")  # granted heat only - no free warmth
        absorber_kw = take(absorber_req, "absorber")
        kiln_kw = take(kiln_req, "kiln")
        elec_kw = take(elec_req, "electrolyser")
        if elec.mode == ON and 0 < elec_kw < elec.rated_kw * elec.min_load_frac - 1e-9:
            clips.append("electrolyser: below min load after shed - idled this step")
            available += elec_kw
            elec_kw = 0.0

        # -- battery charge from whatever solar remains (never charged from itself)
        solar_after_loads = max(0.0, solar_kw - (overhead_kw + absorber_kw + kiln_kw + elec_kw))
        charge_kw = min(max(0.0, action.battery_charge_kw), solar_after_loads)
        rejected_kwh = batt.add(charge_kw * self.batt_eff * dt)
        if rejected_kwh > 0:
            clips.append(f"battery: full, rejected {rejected_kwh / dt:.1f} kW")
            charge_kw -= rejected_kwh / (self.batt_eff * dt)

        consumption_kw = overhead_kw + kiln_hold_kw + absorber_kw + kiln_kw + elec_kw
        used_kw = consumption_kw + charge_kw

        # -- battery discharge covers granted load beyond solar
        delivered_kwh = 0.0
        if consumption_kw * dt > solar_kw * dt + 1e-9:
            delivered_kwh = consumption_kw * dt - solar_kw * dt
            batt.draw(delivered_kwh / self.batt_eff)

        curtailed_kwh = max(0.0, (solar_kw - used_kw) * dt)

        # -- island energy audit: sources - sinks must be ~0
        balance = solar_kw * dt + delivered_kwh - used_kw * dt - curtailed_kwh

        # -- kiln thermal state: only power actually GRANTED heats the kiln
        kiln.thermal_step(kiln_kw + kiln_hold_kw, dt)

        # -- chemistry chain
        co2_captured = absorber_kw * dt / self.e_absorb if self.e_absorb else 0.0
        wasted_capture = stores.silo.add(co2_captured)
        if wasted_capture > 0:
            clips.append(f"silo: full, absorber wasted {wasted_capture:.0f} kg CO2")

        co2_calcined = 0.0
        if kiln_kw > 0 and kiln.is_hot:
            co2_calcined = stores.silo.draw(kiln_kw * dt / self.e_kiln)
        elif kiln_kw > 0:
            clips.append(f"kiln: heating only, temp {kiln.temp_frac:.2f} below threshold")
        co2_vented = stores.co2.add(co2_calcined)
        if co2_vented > 0:
            clips.append(f"co2 buffer: full, vented {co2_vented:.0f} kg")

        h2_made = elec_kw * dt / self.e_h2
        h2_lost = stores.h2.add(h2_made)
        if h2_lost > 0:
            clips.append(f"h2 buffer: full, electrolyser output wasted {h2_lost:.1f} kg")

        # -- sabatier: feed-limited by both stores, capacity-limited by command
        ch4 = 0.0
        limiting = "solar" if solar_kw <= 1e-6 else "none"
        if sab.mode == ON and action.sabatier_load > 0:
            want_ch4 = self.sab_cap * max(sab.min_load_frac, min(1.0, action.sabatier_load)) * dt
            h2_available = 0.0 if self.faults.h2_valve_stuck else stores.h2.level
            bounds = {
                "sabatier": want_ch4,
                "h2": h2_available / self.h2_per_ch4,
                "co2": stores.co2.level / self.co2_per_ch4,
            }
            limiting = min(bounds, key=bounds.get)
            ch4 = bounds[limiting] * self.conv
            stores.h2.draw(ch4 / self.conv * self.h2_per_ch4)
            stores.co2.draw(ch4 / self.conv * self.co2_per_ch4)
        elif sab.mode != ON:
            limiting = "sabatier-offline"

        return StepResult(
            methane_kg=ch4,
            solar_kw=solar_kw,
            used_kw=used_kw,
            curtailed_kwh=curtailed_kwh,
            limiting_subsystem=limiting,
            stores={
                "h2_kg": stores.h2.level,
                "co2_kg": stores.co2.level,
                "silo_kg": stores.silo.level,
                "battery_kwh": batt.level,
            },
            modes={u.name: u.mode for u in (elec, kiln, sab)},
            kiln_temp_frac=kiln.temp_frac,
            clips=clips,
            energy_balance_error_kwh=abs(balance),
            water_kg=h2_made * self.h2o_per_h2,
            co2_vented_kg=co2_vented,
            flows={
                "h2_made": h2_made,
                "h2_wasted": h2_lost,
                "h2_consumed": ch4 / self.conv * self.h2_per_ch4 if ch4 else 0.0,
                "co2_captured": co2_captured,
                "co2_capture_wasted": wasted_capture,
                "co2_calcined": co2_calcined,
                "co2_consumed": ch4 / self.conv * self.co2_per_ch4 if ch4 else 0.0,
                "co2_vented": co2_vented,
            },
        )


@dataclass
class EpisodeLog:
    """Accumulates StepResults into the aggregates the brief requires."""

    methane_kg: float = 0.0
    curtailed_kwh: float = 0.0
    solar_kwh: float = 0.0
    used_kwh: float = 0.0
    water_kg: float = 0.0
    limiting_counts: dict = field(default_factory=dict)

    def add(self, r: StepResult, dt: float = 1.0) -> None:
        self.methane_kg += r.methane_kg
        self.curtailed_kwh += r.curtailed_kwh
        self.solar_kwh += r.solar_kw * dt
        self.used_kwh += r.used_kw * dt
        self.water_kg += r.water_kg
        self.limiting_counts[r.limiting_subsystem] = (
            self.limiting_counts.get(r.limiting_subsystem, 0) + 1
        )

    @property
    def utilisation(self) -> float:
        return self.used_kwh / self.solar_kwh if self.solar_kwh else 0.0
