# Carpeta `scripts`

Esta carpeta contiene **scripts auxiliares** del monorepo: automatizaciones de desarrollo, utilidades de mantenimiento, tareas repetitivas (setup, lint, migraciones, generación de datos, etc.) y tooling interno.

- **Propósito principal**: agrupar herramientas de soporte que no pertenecen a una app/agente/pipeline específico, pero facilitan el trabajo del equipo.
- **Recomendación**: documenta cada script (qué hace, parámetros, requisitos, ejemplos de uso) y procura que sean reproducibles (y seguros) en distintos entornos.

## Scripts

- `seed_incidents.py` — carga el CSV histórico de incidencias (`data/raw/incidents-healthcore.csv`) en el gestor usando las reglas compartidas de `packages/shared/incidents`. Idempotente; las filas inválidas se reportan y no se insertan. Ver `services/api/README.md`.
- `generate-incident-catalog.mjs` — copia el catálogo compartido de incidencias al backoffice (`--check` comprueba que está al día).
- `generate-telemetry-contract.mjs` — genera el contrato de telemetría del backoffice.
