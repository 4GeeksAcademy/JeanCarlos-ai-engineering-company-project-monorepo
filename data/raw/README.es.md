# Carpeta `data/raw`

Esta carpeta está pensada para almacenar **datos en bruto** (raw) relacionados con la compañía: dumps, exports, archivos de ejemplo, muestras de eventos, o datasets sin transformar.

- **Propósito principal**: servir como zona de aterrizaje o referencia de datos originales antes de ser procesados por pipelines.
- **Recomendación**: documenta el origen de cada dataset, formato, tamaño esperado, consideraciones de privacidad/PII y cómo se versiona (idealmente evitando subir datos sensibles al repositorio).

## `healthcore_sales.csv`

- **Origen:** dataset de referencia de predicción de ventas de HealthCore (`content/contexts/sales-forecasting/healthcore/healthcore_sales.csv` del repositorio del syllabus de AI Engineering), copiado sin alteraciones.
- **Formato:** 120 filas mensuales, de 2016-01 a 2025-12, región `consolidated`; columnas `month`, `revenue_usd`, `visits_count`, `avg_revenue_per_visit_usd` y `region`.
- **Privacidad:** solo cifras agregadas mensuales, sin datos de pacientes (fuera del alcance de HIPAA y UK GDPR).
- **Lo usa:** `scripts/train_sales_forecast.py`.
