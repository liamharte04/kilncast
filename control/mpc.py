"""Rolling-horizon MILP scheduler (HiGHS via scipy.optimize.milp).

At each midnight, when the environment issues a fresh forecast, the
controller solves a mixed-integer program over the horizon and executes the
next 24 hours of the plan. The internal model is EXACTLY the plant's own
linear dynamics (same yaml parameters, same kiln thermal equation), so any
performance gap versus the oracle is attributable to forecast error alone -
never to model mismatch.

Decision variables per hour: electrolyser power (semicontinuous, binary),
kiln power (continuous - resistive heating), absorber power, methane rate
(with turndown, binary), battery charge/discharge, plus linear states for
every store and the kiln temperature. A binary `hot` gates calcination on
temperature. Terminal values on stores and heat prevent end-of-horizon
dumping.

OracleMPC is the same optimiser fed actual weather via the environment's
loudly-named UNFAIR_actual_weather() - the ceiling that prices forecast
uncertainty.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

from sim.plant import Action
from sim.subsystems import v


class ForecastMPC:
    name = "mpc-forecast"
    uses_actuals = False

    def __init__(self, curves: dict, horizon_h: int = 96, time_limit_s: float = 30.0):
        self.c = curves
        self.H = horizon_h
        self.time_limit = time_limit_s
        self.plan: pd.DataFrame | None = None
        self.solve_failures = 0
        self.env = None  # only OracleMPC uses this

        self.E = v(curves, "plant_sizing", "electrolyser_kw")
        self.K = v(curves, "plant_sizing", "dac_kiln_kw")
        self.A = v(curves, "plant_sizing", "dac_absorber_kw")
        self.CAP = v(curves, "plant_sizing", "sabatier_capacity_kg_ch4_per_h")
        self.e_h2 = v(curves, "electrolyser", "specific_energy_kwh_per_kg_h2")
        self.e_abs = v(curves, "dac", "absorber_energy_kwh_per_t") / 1000.0
        self.e_kiln = v(curves, "dac", "kiln_energy_kwh_per_t") / 1000.0
        self.elec_min = v(curves, "electrolyser", "min_load_frac")
        self.sab_min = v(curves, "sabatier", "turndown_min_frac")
        self.conv = v(curves, "sabatier", "conversion_frac")
        self.h2_per = v(curves, "stoichiometry", "kg_h2_per_kg_ch4")
        self.co2_per = v(curves, "stoichiometry", "kg_co2_per_kg_ch4")
        self.h2_cap = v(curves, "plant_sizing", "h2_buffer_kg")
        self.co2_cap = v(curves, "plant_sizing", "co2_buffer_kg")
        self.silo_cap = v(curves, "plant_sizing", "silo_hours_cao") * self.CAP * self.co2_per
        self.b_cap = v(curves, "plant_sizing", "battery_kwh")
        self.b_eff = v(curves, "battery", "round_trip_eff") ** 0.5
        self.tau = v(curves, "dac", "kiln_thermal_tau_h")
        self.heatup = v(curves, "dac", "kiln_heatup_tau_h")
        # plan to 0.92 while the plant requires 0.90 - the margin absorbs
        # floating-point drift so executed hours are never "heating only"
        self.hot_thresh = 0.92
        self.elec_standby = v(curves, "electrolyser", "hot_standby_power_frac") * self.E
        self.sab_standby = (
            v(curves, "sabatier", "hot_standby_power_frac") * v(curves, "sabatier", "heater_kw")
        )
        self.derate = v(curves, "solar", "system_derate")
        self.temp_coeff = v(curves, "solar", "temp_coeff_per_c")
        self.solar_kwp = v(curves, "plant_sizing", "solar_capacity_kwp")

    # ------------------------------------------------------------- weather in
    def _solar_kw(self, weather: pd.DataFrame) -> np.ndarray:
        ghi = weather["shortwave_radiation"].to_numpy(dtype=float)
        t_amb = weather["temperature_2m"].to_numpy(dtype=float)
        t_cell = t_amb + ghi / 800.0 * 25.0
        p = self.solar_kwp * (ghi / 1000.0) * self.derate * (1 + self.temp_coeff * (t_cell - 25.0))
        return np.maximum(0.0, p)

    def _horizon_weather(self, obs: dict, forecast: pd.DataFrame) -> pd.DataFrame:
        return forecast[forecast.index >= obs["time"]].head(self.H)

    # ------------------------------------------------------------------ solve
    def act(self, obs: dict, forecast: pd.DataFrame) -> Action:
        t = obs["time"]
        if self.plan is None or t not in self.plan.index:
            self._replan(obs, forecast)
        if self.plan is None or t not in self.plan.index:  # solver failed
            return _fallback_action(obs)
        row = self.plan.loc[t]

        # cold starts exist in the plant but not in the MILP - command "on"
        # ahead of planned production so units are warm when the plan needs
        # them (standby cannot be entered from cold, so a c=0 first hour must
        # not strand the reactor OFF)
        def upcoming(col: str, hours: int) -> bool:
            return self.plan[col].loc[t:].head(hours).sum() > 1e-6

        sab_on = row.c > 1e-6 or upcoming("c", 6)
        elec_on = row.e > 1e-6 or upcoming("e", 3)
        return Action(
            electrolyser_mode="on" if elec_on else "standby",
            electrolyser_load=row.e / self.E,
            # kiln holds heat by PLANNING hold power, never via standby mode -
            # keeps executed draw identical to the optimised plan
            kiln_mode="on" if row.k > 1e-6 else "off",
            kiln_load=row.k / self.K,
            absorber_load=row.a / self.A,
            sabatier_mode="on" if sab_on else "standby",
            sabatier_load=row.c / self.CAP,
            battery_charge_kw=row.chg - row.dis if (row.chg + row.dis) > 1e-6 else -1000.0,
        )

    def _replan(self, obs: dict, forecast: pd.DataFrame) -> None:
        weather = self._horizon_weather(obs, forecast)
        H = len(weather)
        if H < 24:
            self.plan = None
            return
        solar = self._solar_kw(weather)
        sol = self._solve_milp(solar, obs, H)
        if sol is None:
            self.solve_failures += 1
            self.plan = None
            return
        self.plan = pd.DataFrame(sol, index=weather.index)

    def _solve_milp(self, solar: np.ndarray, obs: dict, H: int) -> dict | None:
        # variable layout: [e, k, kp, a, c, chg, dis, T, h2, silo, co2, b, ue, us, hot] x H
        # kp = calcination rate (kg CO2/h) - explicit so silo drain and CO2
        # production stay consistent and both gate on kiln temperature.
        names = ["e", "k", "kp", "a", "c", "chg", "dis", "T", "h2", "silo", "co2", "b", "ue", "us", "hot"]
        off = {n: i * H for i, n in enumerate(names)}
        n_var = len(names) * H

        def idx(n: str, t: int) -> int:
            return off[n] + t

        lb = np.zeros(n_var)
        ub = np.full(n_var, np.inf)
        ub[off["e"] : off["e"] + H] = self.E
        ub[off["k"] : off["k"] + H] = self.K
        ub[off["kp"] : off["kp"] + H] = self.K / self.e_kiln
        ub[off["a"] : off["a"] + H] = self.A
        ub[off["c"] : off["c"] + H] = self.CAP
        ub[off["chg"] : off["chg"] + H] = max(self.b_cap, 1.0)
        ub[off["dis"] : off["dis"] + H] = max(self.b_cap, 1.0)
        ub[off["T"] : off["T"] + H] = 1.0
        ub[off["h2"] : off["h2"] + H] = self.h2_cap
        ub[off["silo"] : off["silo"] + H] = self.silo_cap
        ub[off["co2"] : off["co2"] + H] = self.co2_cap
        ub[off["b"] : off["b"] + H] = max(self.b_cap, 1e-6)
        for b in ("ue", "us", "hot"):
            ub[off[b] : off[b] + H] = 1.0

        integrality = np.zeros(n_var)
        for b in ("ue", "us", "hot"):
            integrality[off[b] : off[b] + H] = 1

        s0 = obs["stores"]
        # clamp initial conditions into variable bounds - a store sitting at
        # exactly its capacity plus float noise must not make hour 0 infeasible
        init = {
            "T": min(obs["kiln_temp_frac"], 1.0),
            "h2": min(s0["h2_kg"], self.h2_cap),
            "silo": min(s0["silo_kg"], self.silo_cap),
            "co2": min(s0["co2_kg"], self.co2_cap),
            "b": min(s0["battery_kwh"], max(self.b_cap, 1e-6)),
        }

        rows, lo, hi = [], [], []

        def add(coeffs: dict[int, float], lower: float, upper: float) -> None:
            row = np.zeros(n_var)
            for j, val in coeffs.items():
                row[j] = val
            rows.append(row)
            lo.append(lower)
            hi.append(upper)

        big_kp = self.K / self.e_kiln  # max calcination kg/h at full kiln power
        for t in range(H):
            prev = lambda n, t=t: ({idx(n, t - 1): -1.0} if t > 0 else {})
            const = lambda n, t=t: (0.0 if t > 0 else init[n])

            # h2[t] = h2[t-1] + e/e_h2 - c*h2_per/conv
            add({idx("h2", t): 1.0, **prev("h2"), idx("e", t): -1.0 / self.e_h2,
                 idx("c", t): self.h2_per / self.conv}, const("h2"), const("h2"))
            # silo[t] = silo[t-1] + a/e_abs - kp
            add({idx("silo", t): 1.0, **prev("silo"), idx("a", t): -1.0 / self.e_abs,
                 idx("kp", t): 1.0}, const("silo"), const("silo"))
            # co2[t] = co2[t-1] + kp - c*co2_per/conv
            add({idx("co2", t): 1.0, **prev("co2"), idx("kp", t): -1.0,
                 idx("c", t): self.co2_per / self.conv}, const("co2"), const("co2"))
            # calcination needs power AND heat: kp <= k/e_kiln, kp <= big*hot
            add({idx("kp", t): 1.0, idx("k", t): -1.0 / self.e_kiln}, -np.inf, 0.0)
            add({idx("kp", t): 1.0, idx("hot", t): -big_kp}, -np.inf, 0.0)
            # hot requires temperature: T[t] >= 0.9*hot[t]
            add({idx("T", t): 1.0, idx("hot", t): -self.hot_thresh}, 0.0, np.inf)
            # kiln thermal: T[t] = T[t-1]*(1-1/tau) + k[t]/(K*heatup)
            tprev = {idx("T", t - 1): -(1.0 - 1.0 / self.tau)} if t > 0 else {}
            tconst = 0.0 if t > 0 else init["T"] * (1.0 - 1.0 / self.tau)
            add({idx("T", t): 1.0, **tprev, idx("k", t): -1.0 / (self.K * self.heatup)},
                tconst, tconst)
            # battery: b[t] = b[t-1] + eff*chg - dis/eff
            add({idx("b", t): 1.0, **prev("b"), idx("chg", t): -self.b_eff,
                 idx("dis", t): 1.0 / self.b_eff}, const("b"), const("b"))
            # electrolyser semicontinuous: minE*ue <= e <= E*ue
            add({idx("e", t): 1.0, idx("ue", t): -self.E}, -np.inf, 0.0)
            add({idx("e", t): 1.0, idx("ue", t): -self.E * self.elec_min}, 0.0, np.inf)
            # sabatier turndown: minC*us <= c <= CAP*us
            add({idx("c", t): 1.0, idx("us", t): -self.CAP}, -np.inf, 0.0)
            add({idx("c", t): 1.0, idx("us", t): -self.CAP * self.sab_min}, 0.0, np.inf)
            # power balance: e + k + a + chg - dis <= solar minus a standby
            # reserve. Without the reserve, a perfectly-tight plan gets shed
            # by the plant's overhead priority at execution and the min-load
            # rule idles the electrolyser - the oracle then loses to forecast
            # MPC purely because pessimistic forecasts left accidental slack.
            reserve = self.elec_standby + self.sab_standby
            add({idx("e", t): 1.0, idx("k", t): 1.0, idx("a", t): 1.0,
                 idx("chg", t): 1.0, idx("dis", t): -1.0},
                -np.inf, max(0.0, float(solar[t]) - reserve))

        # objective: maximise methane + terminal store/heat value. Salvage
        # values are deliberately WELL BELOW marginal production value -
        # producing now must always beat hoarding (a 0.9 salvage on H2 made
        # the optimiser hoard buffers and never start the kiln), they exist
        # only to stop end-of-horizon dumping.
        obj = np.zeros(n_var)
        obj[off["c"] : off["c"] + H] = -1.0
        last = H - 1
        obj[idx("h2", last)] = -0.5 / self.h2_per
        obj[idx("co2", last)] = -0.15 / self.co2_per
        obj[idx("silo", last)] = -0.05 / self.co2_per
        obj[idx("b", last)] = -0.2 / (self.e_h2 * self.h2_per)
        obj[idx("T", last)] = -2.0
        obj[off["chg"] : off["chg"] + H] += 1e-4  # discourage battery churn
        obj[off["dis"] : off["dis"] + H] += 1e-4

        res = milp(
            obj,
            constraints=LinearConstraint(np.vstack(rows), lo, hi),
            bounds=Bounds(lb, ub),
            integrality=integrality,
            options={"time_limit": self.time_limit, "mip_rel_gap": 0.02},
        )
        # status 0 = proven optimal; status 1 = hit the time limit WITH a
        # feasible incumbent - use it (discarding it silently downgraded the
        # oracle to the fallback controller and it lost to forecast MPC)
        if res.x is None:
            return None
        x = res.x
        return {n: x[off[n] : off[n] + H] for n in ("e", "k", "a", "c", "chg", "dis")}


class OracleMPC(ForecastMPC):
    """Perfect hindsight: same optimiser, actual weather. The ceiling."""

    name = "oracle-perfect-forecast"
    uses_actuals = True

    def attach_env(self, env) -> None:
        self.env = env

    def _horizon_weather(self, obs: dict, forecast: pd.DataFrame) -> pd.DataFrame:
        assert self.env is not None, "OracleMPC needs attach_env(env)"
        return self.env.UNFAIR_actual_weather(obs["time"], self.H)


def _fallback_action(obs: dict) -> Action:
    """If the solver ever fails, degrade to a safe sun-follower hour."""
    sunny = obs["ghi_now"] > 50.0
    return Action(
        electrolyser_mode="on" if sunny else "standby",
        electrolyser_load=1.0 if sunny else 0.0,
        kiln_mode="on" if sunny else "standby",
        kiln_load=1.0 if sunny else 0.0,
        absorber_load=1.0 if sunny else 0.0,
        sabatier_mode="on",
        sabatier_load=0.3,
        battery_charge_kw=-1000.0,
    )
