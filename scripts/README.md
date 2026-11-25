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

## `evaluate_sales_model.py`

Formal evaluation of the sales forecast model on the 8 training years only: temporal cross-validation (`TimeSeriesSplit`, 5 folds of 12 months, no shuffle, no lag leakage across folds), MAE and RMSE for training and validation (mean ± std), and the learning curve. Writes `data/eval/learning_curve.png`, `data/eval/cv_folds.png` and `data/eval/sales_model_evaluation.json`; the diagnosis is in [`data/eval/evaluation_report.md`](../data/eval/evaluation_report.md).

```bash
uv run python scripts/evaluate_sales_model.py
uv run pytest tests/pipelines/test_temporal_cv.py
```

> _Spanish version: [README.es.md](./README.es.md)._

## Scripts

- `seed_incidents.py` — loads the legacy incident CSV (`data/raw/incidents-healthcore.csv`) into the incident manager using the shared rules in `packages/shared/incidents`. Idempotent; invalid rows are reported, never inserted. See `services/api/README.md`.
- `generate-incident-catalog.mjs` — copies the shared incident catalog into the backoffice (`--check` verifies it is up to date).
- `generate-telemetry-contract.mjs` — generates the backoffice telemetry contract.
