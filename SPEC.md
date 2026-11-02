# Especificación: Talent Pipeline Tracker

## Objetivo

Construir una herramienta interna de People & Talent para gestionar candidaturas desde una sola interfaz. La aplicación consume la API REST ya desplegada; no implementa ni simula un backend.

## Entidad de la API

Los nombres del contrato se conservan exactamente en `snake_case`.

```ts
interface Candidate {
  id: string;
  full_name: string;
  email: string;
  phone: string;
  position: string;
  linkedin_url: string | null;
  cv_url: string | null;
  status: "received" | "in_progress" | "selected" | "discarded";
  stage: "pending" | "review" | "personal_interview" | "technical_interview" | "offer_presented";
  experience_years: number;
  notes_count: number;
  applied_at: string;
  updated_at: string;
}

interface CandidateNote {
  id: string;
  record_id: string;
  content: string;
  created_at: string;
}
```

El recurso del backend se denomina `Record`. El alta y la edición requieren `full_name`, `email`, `phone`, `position` y `experience_years`; `linkedin_url` y `cv_url` son opcionales y aceptan `null`.

## API

- Base URL: `https://playground.4geeks.com/tracker/api/v1`
- Documentación: <https://playground.4geeks.com/tracker/api/v1/docs>
- `GET /records`: query params `status`, `stage`, `search`, `page` y `limit`.
- `POST /records`: crear un registro con `CandidateInput`.
- `GET /records/{id}`: recuperar el detalle.
- `PUT /records/{id}`: reemplazar los campos de creación.
- `PATCH /records/{id}`: actualizar `status` y/o `stage`.
- `GET /records/{id}/notes`, `POST /records/{id}/notes` (`{ "content": string }`) y `DELETE /records/{id}/notes/{note_id}`.
- El listado responde `{ total, page, limit, data }`; notas responden `{ data, meta }`.

## Requisitos funcionales

1. Mostrar nombre, puesto, estado y etapa en el listado.
2. Filtrar por estado y etapa mediante query params y `useSearchParams`.
3. Buscar por nombre o email sin recargar la página.
4. Navegar mediante App Router a `/candidatures/[id]` y mostrar todos los campos de `Candidate`.
5. Cambiar estado y etapa con PATCH.
6. Listar, añadir y eliminar notas internas.
7. Crear registros con POST y editar sus campos con PUT.
8. Presentar feedback de éxito, error y carga para las operaciones asíncronas.

## Criterios de calidad y aceptación

- Next.js App Router, React y TypeScript; sin gestores de estado externos.
- Tipos de API y cliente de datos organizados en `/types` y `/lib`; UI en `/components` y rutas en `/app`.
- Cada lectura expone estados visibles de carga, éxito y error; fallos de red/API no son silenciosos.
- Tras POST, PUT o PATCH se actualiza la UI sin recarga completa de página.
- Validar campos requeridos, formato email, experiencia no negativa y URLs opcionales antes de guardar.
- Interfaz en español, usable por teclado y adaptable a móvil/escritorio.
- Ejecutar `npm run lint` y `npm run build` en `uis/talent-pipeline-tracker` sin errores.
- La URL de la API se configura en `.env.local` como `NEXT_PUBLIC_API_URL`.