# Carpeta `scripts`

Esta carpeta contiene **scripts auxiliares** del monorepo: automatizaciones de desarrollo, utilidades de mantenimiento, tareas repetitivas (setup, lint, migraciones, generación de datos, etc.) y tooling interno.

- **Propósito principal**: agrupar herramientas de soporte que no pertenecen a una app/agente/pipeline específico, pero facilitan el trabajo del equipo.
- **Recomendación**: documenta cada script (qué hace, parámetros, requisitos, ejemplos de uso) y procura que sean reproducibles (y seguros) en distintos entornos.

## `train_sales_forecast.py`

Predicción de ventas para Finanzas (Random Forest). Carga `data/raw/healthcore_sales.csv`, entrena con 2016–2023, pronostica 2024–2025 de forma recursiva sin verlos y escribe MSE, PSI, Gini y K2 Score (R²) junto con una gráfica con banda P10–P90 en `data/eval/sales_forecast/`. Detalle: [`docs/ml/sales-forecast.md`](../docs/ml/sales-forecast.md).

```bash
uv sync
uv run python scripts/train_sales_forecast.py
uv run pytest tests/pipelines/test_sales_forecast.py
```
