# Talent Pipeline Tracker

Frontend interno de People & Talent para revisar candidaturas, gestionar estado y etapa y mantener notas de selección.

## Desarrollo local

```bash
npm install
npm run dev
```

La app se abre en <http://localhost:3000>. Configura `NEXT_PUBLIC_API_URL` en `.env.local` con `https://playground.4geeks.com/tracker/api/v1`.

## Validación

```bash
npm run lint
npm run build
```

## Estructura

- `app/`: listado `/` y detalle `/candidatures/[id]`.
- `components/`: listado, detalle y formulario de candidaturas.
- `lib/api.ts`: cliente asíncrono para Talent Tracker API.
- `types/tracker.ts`: tipos de registros, notas y valores del pipeline.
