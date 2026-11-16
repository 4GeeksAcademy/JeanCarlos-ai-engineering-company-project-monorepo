# `scripts` folder

This folder contains **helper scripts** for the monorepo: development automation, maintenance utilities, repetitive tasks (setup, lint, migrations, data generation, etc.), and internal tooling.

- **Main purpose**: group support tools that do not belong to a specific app, agent, or pipeline but make the team’s work easier.
- **Recommendation**: document each script (what it does, parameters, requirements, usage examples) and keep them reproducible (and safe) across environments.

> _Spanish version: [README.es.md](./README.es.md)._

## Scripts

- `seed_incidents.py` — loads the legacy incident CSV (`data/raw/incidents-healthcore.csv`) into the incident manager using the shared rules in `packages/shared/incidents`. Idempotent; invalid rows are reported, never inserted. See `services/api/README.md`.
- `generate-incident-catalog.mjs` — copies the shared incident catalog into the backoffice (`--check` verifies it is up to date).
- `generate-telemetry-contract.mjs` — generates the backoffice telemetry contract.
