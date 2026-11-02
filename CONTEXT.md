# CONTEXT — HealthCore Talent Pipeline Tracker

## Empresa y usuarios

HealthCore es la empresa seleccionada para este proyecto: una red de atención ambulatoria con equipos clínicos y administrativos en Estados Unidos y Reino Unido. Esta entrega construye una herramienta interna de People & Talent para organizar el proceso de selección. La interfaz se identifica como HealthCore People & Talent.

El equipo gestiona un volumen superior a 100 candidaturas en menos de dos semanas. El flujo anterior repartía seguimiento en hojas de cálculo, notas de entrevista en documentos y actualizaciones de estado en correo. La aplicación centraliza ese trabajo en una vista conectada a la API de Talent Tracker.

## Alcance del hito

- Listar candidaturas y filtrar por estado y etapa.
- Buscar por nombre completo o correo electrónico sin recargar la página.
- Consultar y editar el detalle de una candidatura.
- Actualizar estado y etapa, y añadir o eliminar notas internas.
- Registrar candidaturas desde un formulario.
- Comunicar estados de carga, éxito y error para todas las operaciones asíncronas.

## Contrato de la API

El backend expone el recurso `Record`. Los nombres de campos y valores del API se preservan exactamente.

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

Alta y edición requieren `full_name`, `email`, `phone`, `position` y `experience_years`; `linkedin_url` y `cv_url` son opcionales. No se inventan más campos, políticas de contratación ni etapas que las definidas por el API.

## API y entorno

- Documentación: <https://playground.4geeks.com/tracker/api/v1/docs>
- URL local: `NEXT_PUBLIC_API_URL=https://playground.4geeks.com/tracker/api/v1`
- Listado: `GET /records?status=&stage=&search=&page=&limit=`
- Detalle: `GET /records/{id}`
- Alta: `POST /records`
- Edición: `PUT /records/{id}`
- Cambios del pipeline: `PATCH /records/{id}`
- Notas: `GET/POST /records/{id}/notes`, `DELETE /records/{id}/notes/{note_id}`

La implementación del hito vive en `uis/talent-pipeline-tracker/` y usa Next.js App Router, React y TypeScript.
