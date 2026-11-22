# `scripts` folder

This folder contains **helper scripts** for the monorepo: development automation, maintenance utilities, repetitive tasks (setup, lint, migrations, data generation, etc.), and internal tooling.

- **Main purpose**: group support tools that do not belong to a specific app, agent, or pipeline but make the team’s work easier.
- **Recommendation**: document each script (what it does, parameters, requirements, usage examples) and keep them reproducible (and safe) across environments.

## `train_sales_forecast.py`

Sales forecast for Finance (Random Forest). Loads `data/raw/healthcore_sales.csv`, trains on 2016–2023, forecasts 2024–2025 recursively without seeing them, and writes MSE, PSI, Gini and K2 Score (R²) plus a P10–P90 band chart to `data/eval/sales_forecast/`. Details: [`docs/ml/sales-forecast.md`](../docs/ml/sales-forecast.md).

```bash
uv sync
uv run python scripts/train_sales_forecast.py
uv run pytest tests/pipelines/test_sales_forecast.py
```

> _Spanish version: [README.es.md](./README.es.md)._
