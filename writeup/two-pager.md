# Kilncast: buffer atoms, not electrons

*SoTA Commission II entry - Liam Harte*

## Motivation

In our simulated January in Wiltshire - Rivan's own county - a solar-methane
plant run the obvious way made **0 kg** of methane in 28 days. The identical
plant, driven by a scheduler that reads real weather forecasts, made 92 kg
from the same sky. <!--NUM:wiltshire_jan--> Intermittency is not an
efficiency problem; it is a scheduling problem, and a remote plant is only
economical if it schedules itself. This entry is an open digital twin of
Rivan's process built to measure exactly how much autonomy is worth - under
real weather, real forecast error, equipment faults and no grid.

## The environment

The twin models Rivan's published architecture at operating-curve level:
off-grid solar -> alkaline electrolyser -> H2 buffer; calcium-looping DAC
(a 900C kiln with real thermal inertia, a CaCO3/CaO silo) -> CO2; a Sabatier
reactor with 90% turndown -> pipeline methane. Every parameter in
`curves/rivan-v1.yaml` carries a source tag - Rivan's blog, literature,
chemistry, or an explicit `assumed` - and the parameterisation approach was
confirmed with the organisers and Rivan. Weather is real twice over: ERA5
actuals, and *as-issued* Open-Meteo forecasts (lead days 1-7, with measured
error growth: Seville irradiance MAE rises from 42 to 71 W/m2 across the
week; a strictly-trailing climatology fills days 8-10 and a test poisons the
future to prove no leakage).

The architectural rule that keeps every comparison honest: controllers
PROPOSE, the plant ENFORCES. Islanded power balance, minimum loads, buffer
capacities, kiln temperature and stoichiometry are imposed by clipping, and
every clip is logged. Energy and mass conservation are asserted on every
simulated hour.

## The thesis and the scheduler

The cheapest storage on this plant is not a battery - it is thermal mass in
a hot kiln, chemical state in the silo, and hydrogen in a low-pressure tank.
The scheduler's whole job is to park every marginal joule in the cheapest
state the forecast says it can be withdrawn from. We implement it as a
rolling-horizon MILP (HiGHS) that replans on each midnight forecast; its
internal model is the plant's exact dynamics, so its gap to a
perfect-forecast oracle prices forecast uncertainty alone (zero model
mismatch is also a limitation - field MPC has to survive mismatch this
benchmark does not yet impose).

## Results

Across 4 European sites x 4 seasons of 2025 (28-day windows), against a
named run-when-sunny baseline on identical hardware: the forecast MPC
produced **+90% more methane** <!--NUM:headline_uplift-->, closing
**~70%** <!--NUM:gap_closed--> of the gap to perfect hindsight (per
site-month the closure ranges ~30-95%; northern sites gain the most from
scheduling and close the least of the gap - scheduling matters most, and
forecasts help least, exactly where sun is scarce). Wiltshire in October:
41 kg naive vs 784 kg scheduled. <!--NUM:wiltshire_oct--> Faults: a 3-day
kiln heater failure is detected autonomously within 2 hours from a
commanded-vs-actual mode mismatch, the MILP replans without the kiln, and
monthly impact ranges from indistinguishable-from-zero (outage coinciding
with cloud; the silo and a 2.7x-oversized kiln catch up) to roughly -17%
(outage in peak sun) <!--NUM:fault_range-->. Solver noise is measured, not
assumed (`experiments/noise_band.py`); claims inside the band are reported
as indistinguishable from zero.

## What didn't work

- **Batteries.** Offered storage at every 2026 price (50-250 GBP/kWh), the
  scheduler never earned the capex back - the atom buffers already capture
  the arbitrage - and under real forecast error a deterministic MPC used a
  battery to do WORSE <!--NUM:battery-->; solving harder did not fix it,
  consistent with the optimizer's curse. The honest conclusion is a
  limitation of deterministic planning: robust/stochastic MPC is the named
  next step, and this benchmark is exactly the environment to test it in.
- **Hand rules.** Our first heuristic gained ~0.4% over the baseline across
  the sweep; even a tuned rulebook that fixes the baseline's dominant
  misallocation (kiln matched to stoichiometric demand) trails the MPC
  badly. <!--NUM:heuristics--> Intuition does not crack this problem.
- **The salvage-value trap.** Terminal store credits near methane's marginal
  value made the optimiser hoard buffers and never start the kiln - a
  cautionary result for anyone bolting an MILP onto a plant.
- **A "perfect" oracle that lost.** Until standby overheads were modelled,
  the forecast MPC beat the perfect-information oracle: razor-tight optimal
  plans were shed at execution while pessimistic forecasts left accidental
  slack. Margins matter as much as information.

Also stated plainly: the naive baseline produced MORE methane when its kiln
failed (+17%) - it misallocates that much power. That misallocation, not
weather magic, is most of what scheduling recovers.

## Where the prize goes

Three steps, in order: validate the twin against real plant telemetry
(replacing `assumed` tags with measured curves); publish the environment as
the open benchmark for renewable-driven plant autonomy - real forecast
error, faults and economics in one gym-style API that currently exists
nowhere else; and implement the stochastic MPC that finding one demands.
Swap one yaml file and it is your plant: the harness is process-agnostic.

*Repo: github.com/liamharte04/kilncast - one command reproduces every figure.*
