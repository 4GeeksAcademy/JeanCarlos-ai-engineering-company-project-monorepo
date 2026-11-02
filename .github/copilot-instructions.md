# Copilot Instructions — HealthCore Talent Pipeline Tracker

## Fuente de verdad

- Usa `CONTEXT.md` y `SPEC.md` como fuente funcional del hito.
- La app está en `uis/talent-pipeline-tracker/` y usa Next.js App Router, React y TypeScript.
- La API publicada es `https://playground.4geeks.com/tracker/api/v1`; su contrato está en `https://playground.4geeks.com/tracker/api/v1/docs`.

## Reglas de implementación

- Conserva exactamente los nombres `snake_case` y los valores del API (`status`, `stage`, `full_name`, `experience_years`, etc.).
- Usa componentes React, hooks locales y el App Router. No añadas librerías externas de estado.
- Mantén tipos en `types/`, acceso a API en `lib/`, UI en `components/` y páginas en `app/`.
- Gestiona todas las peticiones con `async`/`await`; informa carga, éxito y error y actualiza la UI sin recarga completa.
- No inventes datos, puestos, reglas o etapas. Escapa/valida entradas y restringe enlaces externos a HTTP(S).
- Mantén navegación por teclado, etiquetas accesibles y diseño responsive.

## Validación

Desde `uis/talent-pipeline-tracker/`, ejecuta `npm run lint` y `npm run build`.