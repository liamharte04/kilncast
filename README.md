# plant-twin

An open digital twin of a solar-powered synthetic methane plant (solar ->
electrolysis -> H2 + air-captured CO2 -> Sabatier methanation), with autonomous
scheduling strategies benchmarked against real archived weather forecasts.

Built for SoTA Commission II: Intermittent Abundance.

Status: early development. Quickstart, results, and documentation land as the
build progresses.

## Layout

- `curves/` - subsystem operating curves (yaml, every value source-tagged)
- `sim/` - weather data, subsystem models, plant simulation, economics
- `control/` - baseline, heuristic, and MPC schedulers
- `experiments/` - multi-site evaluation sweeps
- `dashboard/` - visualisation of live plant state
- `scripts/` - one-off utilities
