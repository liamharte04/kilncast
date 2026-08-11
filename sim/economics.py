"""Plant economics: capex breakdown, amortisation, and the objective.

The objective is ECONOMIC, mirroring Rivan's capex-first philosophy: kg of
grid-spec methane per pound of capital, never thermodynamic efficiency.
"""

from __future__ import annotations

from sim.plant import EpisodeLog
from sim.subsystems import v


def capex_breakdown_gbp(curves: dict) -> dict[str, float]:
    s = curves["plant_sizing"]
    return {
        "solar": v(s, "solar_capacity_kwp") * v(curves, "solar", "capex_gbp_per_kwp"),
        "electrolyser": v(s, "electrolyser_kw") * v(curves, "electrolyser", "capex_gbp_per_kw"),
        "dac": (v(s, "dac_kiln_kw") + v(s, "dac_absorber_kw")) * v(curves, "dac", "capex_gbp_per_kw"),
        "sabatier": v(s, "sabatier_capacity_kg_ch4_per_h") * v(curves, "sabatier", "capex_gbp_per_kg_h"),
        "h2_buffer": v(s, "h2_buffer_kg") * v(curves, "h2_buffer", "capex_gbp_per_kg"),
        "battery": v(s, "battery_kwh") * v(curves, "battery", "capex_gbp_per_kwh"),
    }


def total_capex_gbp(curves: dict) -> float:
    return sum(capex_breakdown_gbp(curves).values())


def cost_gbp_per_hour(curves: dict) -> float:
    """Straight-line amortisation plus O&M, per operating hour."""
    capex = total_capex_gbp(curves)
    years = v(curves, "economics", "plant_lifetime_years")
    opex_frac = v(curves, "economics", "opex_frac_per_year")
    return capex / (years * 8760.0) + capex * opex_frac / 8760.0


def summarise(log: EpisodeLog, curves: dict, hours: float) -> dict:
    """Aggregate an episode into the metrics the brief asks for."""
    cost = cost_gbp_per_hour(curves) * hours
    kg = log.methane_kg
    value = kg * v(curves, "economics", "methane_value_gbp_per_kg")
    return {
        "methane_kg": round(kg, 1),
        "gbp_per_kg_ch4": round(cost / kg, 3) if kg else None,  # never Infinity - not valid JSON
        "methane_value_gbp": round(value, 0),
        "amortised_cost_gbp": round(cost, 0),
        "margin_gbp": round(value - cost, 0),
        "plant_utilisation": round(log.utilisation, 3),
        # utilisation = energy used / energy available. A controller can score
        # high while making little product (the baseline does) - so we also
        # report productivity of the energy actually consumed:
        "kg_ch4_per_mwh_used": round(kg / (log.used_kwh / 1000.0), 2) if log.used_kwh else 0.0,
        "curtailed_kwh": round(log.curtailed_kwh, 0),
        "curtailed_frac": round(log.curtailed_kwh / log.solar_kwh, 3) if log.solar_kwh else 0.0,
        "water_consumed_kg": round(log.water_kg, 0),
        "limiting_subsystem_hours": dict(sorted(log.limiting_counts.items(), key=lambda kv: -kv[1])),
    }
