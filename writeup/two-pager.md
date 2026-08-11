# Kilncast: buffer atoms, not electrons

*SoTA Commission II entry - Liam Harte*

## Motivation

In a simulated October week in Wiltshire - Rivan's own county - a
solar-methane plant run the obvious way made 9 kg of methane. The identical
plant, driven by a scheduler reading real weather forecasts, made 260 kg
from the same sky: it banked heat, calcined feedstock and hydrogen before
the storm arrived. Intermittency is not an efficiency problem; it is a
scheduling problem, and a remote plant is only economical if it schedules
itself. This entry is an open digital twin of Rivan's process built to
measure exactly how much autonomy is worth - under real weather, real
forecast error, equipment faults and no grid.

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
produced **+95.6% more methane**, closing **75%** of the gap to perfect
hindsight (per site-month: 0-98%, median 75%). The controlled comparison:
a tuned rulebook that fixes the baseline's dominant misallocation gains
+55.7%; the MPC adds +25.6% on top. The geography is the finding: northern
sites gain the most from scheduling and close the least of the gap, and the
extreme cell - Wiltshire in January - is the sharpest measurement of what
forecast error costs: **0 kg** from every controller we built (the forecast
MPC spent 144 hours heating a kiln that never crossed calcination
temperature) against 194 kg for the perfect-forecast oracle. Wiltshire in
October: 41 kg naive vs 726 kg scheduled. Faults: a 3-day kiln heater
failure is detected autonomously within 2 hours from a commanded-vs-actual
mode mismatch, the MILP replans without the kiln, and monthly impact ranges
from -4% (outage coinciding with cloud; the silo and a 2.7x-oversized kiln
catch up) to -9.5% (outage in peak July sun). Solver noise is measured, not
assumed (`experiments/noise_band.py`, 8.2% band); claims inside the band
are reported as indistinguishable from zero.

## What didn't work

- **Batteries.** Offered storage at every 2026 price (50-250 GBP/kWh), the
  scheduler never earned the capex back - and 0-2 MWh changed output by
  less than the measured solver-noise band (8.2%) under both real and
  perfect forecasts. The atom buffers already capture the arbitrage. We
  initially measured something more dramatic - batteries actively hurting a
  deterministic planner - but our own review traced it to a stale-replan
  bug; the corrected result is the quieter, better-supported claim, and the
  original lives in git history as a cautionary tale about planning cadence.
- **Hand rules.** Our first heuristic gained under 1% over the baseline
  across the sweep; the tuned rulebook captures +55.7% but still leaves a
  quarter of the MPC's production on the table. (Its own first version made
  62 kg - a demand-matched kiln load never reaches calcination temperature
  from cold. Even writing good rules requires the insight the optimiser
  finds automatically.)
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
