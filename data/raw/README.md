# `data/raw` folder

This folder is intended for **raw data** related to the company: dumps, exports, sample files, event samples, or untransformed datasets.

- **Main purpose**: serve as a landing zone or reference for original data before pipelines process it.
- **Recommendation**: document each dataset’s origin, format, expected size, privacy/PII considerations, and how it is versioned (ideally avoiding sensitive data in the repository).

## `healthcore_sales.csv`

- **Origin:** HealthCore sales-forecasting reference dataset (`content/contexts/sales-forecasting/healthcore/healthcore_sales.csv` in the AI Engineering syllabus repository), copied unaltered.
- **Format:** 120 monthly rows, 2016-01 to 2025-12, region `consolidated`; columns `month`, `revenue_usd`, `visits_count`, `avg_revenue_per_visit_usd`, `region`.
- **Privacy:** aggregated monthly figures only; no patient-level data (outside HIPAA / UK GDPR scope).
- **Used by:** `scripts/train_sales_forecast.py`.

> _Spanish version: [README.es.md](./README.es.md)._
