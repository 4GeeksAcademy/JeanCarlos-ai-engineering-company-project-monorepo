# Flujo agéntico de RFPs: recepción y enrutamiento (Hito 9, Parte 1)

En HealthCore, el papel de "Ventas" lo cumple **Revenue Cycle**, el equipo de Tom Callahan. Ellos suben la RFP institucional en PDF desde el backoffice y reciben el análisis repartido entre los tres departamentos del BRIEFING:

| `department_id` | Departamento | Responsable |
| --- | --- | --- |
| `revenue` | Revenue Cycle | Tom Callahan |
| `clinical` | Clinical Operations | Dr. Marcus Reid |
| `compliance` | Compliance and Data Governance | Claire Whitfield |

Compliance participa **siempre**.

## Layout

| Pieza | Ruta |
| --- | --- |
| Grafo LangGraph dedicado `rfp_intake` (separado del agente CX / knowledge) | `data/pipelines/rfp_intake/graph.py` |
| Agentes: clasificador, extractor, orquestador, workers, synthesizer | `data/pipelines/rfp_intake/agents.py` |
| PDF → Markdown (MarkItDown) y legibilidad (py-readability-metrics) | `data/pipelines/rfp_intake/document.py` |
| Detección y redacción de PHI | `data/pipelines/rfp_intake/phi.py` |
| Tablas SQLModel (PostgreSQL / Supabase) | `data/pipelines/rfp_intake/models.py`, `supabase/migrations/20261123230000_rfp_intake.sql` |
| Contrato de handoff a la Parte 2 | `data/pipelines/rfp_intake/handoff.py` |
| Rutas HTTP en el **backend existente** (solo disparan y consultan) | `services/api/routes/rfp.py` |
| UI en modo ticket | `uis/backoffice/src/app/rfp/page.tsx` |
| CLI de reproceso y smoke runs | `scripts/rfp_intake.py` |
| PDF subidos (artefacto de runtime, ignorado por git) | `data/raw/rfp/<ticket_id>.pdf` |

## Flujo

```
POST /api/rfp/tickets (PDF) ─► guarda data/raw/rfp/<ticket_id>.pdf, ticket «analizando» ─► 202 {ticket_id}
                                └─ BackgroundTasks ─► rfp_intake
convert_document (MarkItDown) → screen_phi (redacta PHI) → classify ─ no es RFP → discard («descartado»)
                                                                  └ RFP → extract_metadata (+ legibilidad)
   → orchestrate ─ Send ─► worker revenue ┐
                 ├ Send ─► worker clinical ├─► synthesize → «analisis_completo» + handoff
                 └ Send ─► worker compliance ┘
GET /api/rfp/tickets[/{id}] ◄─ la UI hace polling cada 3 s
```

**Etapas.** Cada nodo guarda su etapa (`stage`) y su resultado en PostgreSQL antes de que empiece el siguiente. El estado del ticket solo toma los tres valores de la Parte 1: `analizando`, `descartado` y `analisis_completo`. El CHECK de la tabla ya admite el vocabulario completo de las partes 2 y 3.

**Workers en paralelo.** Corren con `Send`. Cada uno recibe los **metadatos compartidos y solo sus párrafos**, nunca el documento completo.

**Validación de cada agente.** La salida de cada agente pasa por un contrato Pydantic. Si el JSON no es válido, se reintenta una vez. Además se comprueba contra el texto de la RFP:

- Una cifra que no aparece en la RFP o en los metadatos se descarta y se convierte en pregunta abierta. Nunca se inventa una población, un precio o una fecha.
- El contacto de cada departamento lo pone el código a partir de la tabla del BRIEFING, no el modelo.

**El documento es un dato, no instrucciones.** Viaja siempre dentro de `<documento_rfp>`, y una instrucción incrustada en la RFP no se sigue.

## Persistencia (PostgreSQL / Supabase, vía SQLModel)

- **`rfp_ticket`**: `ticket_id`, `rfp_id`, `status`, `stage`, `raw_pdf_path`, `error`, `classification`, `phi_detected`, `phi_categories`, `compliance_review_required` y `document_markdown` (ya redactado; es la entrada de la Parte 2). También `synthesis`, `handoff`, `ready_for_generation`, `created_at`, `updated_at` y `completed_at`.
- **`rfp_metadata`**:
  - datos de la RFP: `client_name`, `client_country` (US/UK), `program_type`, `covered_population`, `deadline`, `budget_range`, `currency`, `regulation`, `agreement_type`, `departments_needed` y `open_questions`;
  - **métricas de costo de procesamiento**: `word_count`, `estimated_tokens`, `pdf_pages`, `flesch_kincaid_grade`, `flesch_reading_ease`, `gunning_fog`, `cost_band` y `readability` (el JSON completo).
- **`rfp_department_section`**: `department_id`, `owner`, `key_aspects`, `requests` (qué pedirle al departamento), `open_questions` y `excerpt_ids`. También `draft_content`, `evaluation_results` (con `contains_phi: false`), `approval_status`, `approver` y `approved_at`, que usan las partes 2 y 3.

**Sin PHI en ninguna columna:**

- El nombre del archivo subido no se guarda: el PDF se nombra con el `ticket_id`.
- La PHI se registra solo como categorías.
- El texto del documento se guarda únicamente después de redactarlo.

## Handoff a la Parte 2 (`rfp_intake.handoff/v1`)

El handoff se guarda en `rfp_ticket.handoff` junto con la bandera de cola `ready_for_generation`. La Parte 2 consulta:

```sql
select ticket_id, handoff, document_markdown from rfp_ticket
where status = 'analisis_completo' and ready_for_generation;
```

El handoff contiene:

- `ticket_id`
- `route`: `proposal_generation`, o `compliance_review` si se detectó PHI
- los metadatos y la `currency`
- `required_clauses`: BAA para EE. UU. o DPA (UK GDPR) para Reino Unido
- `workstreams`: los `key_aspects`, `requests` y `open_questions` de cada departamento
- `conflicts`, `open_questions` y `phi`
- `document_ref`: el Markdown ya redactado. La Parte 2 **no vuelve a parsear el PDF**.

## RFP con PHI (caso crítico del BRIEFING)

1. `screen_phi` se ejecuta **antes de cualquier agente**. Detecta:
   - nombre de paciente;
   - DOB, MRN, NHS o SSN con su valor;
   - un campo de diagnóstico;
   - un "clinical summary" adjunto;
   - una edad combinada con un diagnóstico.
2. Los párrafos afectados se sustituyen por `[REDACTADO: posible PHI — retenido para revisión humana de Compliance]`. Si el texto es un resumen clínico adjunto, se redacta hasta la siguiente sección.
3. Si después de redactar queda PHI, el flujo **falla cerrado**.
4. El ticket se marca con `phi_detected`, `compliance_review_required` y `phi_categories`.
5. El handoff va a `compliance_review` con `ready_for_generation = false`, porque el trigger `phi-detected` es una parada dura.
6. Las salidas de los workers y del synthesizer se vuelven a revisar.
7. El vocabulario sanitario genérico de una RFP real **no** cuenta como PHI. Por ejemplo, "chronic condition management (diabetes, hypertension)" o "student health data".

## Preguntas de diseño

**¿Qué pasa si una RFP menciona un departamento que no existe en el BRIEFING?**
No se crea ningún departamento:

- El orquestador pone la mención (IT, legal, compras…) en `unmapped`, con el departamento válido más cercano o `null`.
- Un `department_id` desconocido en el plan del modelo también se mueve a `unmapped`.
- La tabla tiene un CHECK que solo admite `revenue`, `clinical` y `compliance`.
- El synthesizer lo muestra a Revenue Cycle como paso siguiente («canalizar vía X»), y Revenue, como dueño del ticket, decide.

**¿Qué necesita cada worker del estado compartido? ¿Qué pasa si falta una cifra?**

- Cada worker recibe solo los metadatos públicos (cliente, país, programa, población, fecha límite, presupuesto, moneda, regulación) y **los párrafos que el orquestador le asignó**, no el documento completo. Así gasta menos tokens y no se contamina con lo que corresponde a otro departamento.
- Si falta una cifra (población, presupuesto, año de la fecha límite), se registra como **pregunta abierta** del departamento responsable.
- Una cifra que el modelo incluya y que no esté en la RFP se descarta.

**¿Cómo se decide que un documento "no es una RFP"? ¿Qué pasa con un falso negativo?**

- **Criterio.** El clasificador aplica el criterio del BRIEFING: es una RFP si un cliente institucional (empleador o universidad de EE. UU. o Reino Unido) **pide** una propuesta para un servicio de HealthCore. Cuentan tanto la formal como la informal. Un proveedor que **vende** algo (como el pitch de EHR), una factura o un documento clínico sin petición no lo son.
- **Umbral.** Solo se descarta si el modelo dice «no es RFP» con confianza ≥ 0,5. Una negativa dudosa sigue adelante como `accept_low_confidence`: un falso positivo cuesta un análisis, mientras que un falso negativo pierde una oportunidad.
- **Falso negativo.** El ticket descartado conserva el PDF y la razón (`classification.reasons`), así que Revenue lo ve en la UI. Si fue un error, lo reprocesa con `python scripts/rfp_intake.py reprocess <ticket_id>`.

**¿Qué pasa si dos workers devuelven información contradictoria sobre la misma sección?**

- Los workers no negocian entre sí. Cada uno declara `facts` estructurados (población, moneda, instrumento BAA/DPA), y el synthesizer detecta contradicciones de forma **determinista** comparándolos entre sí y con los metadatos verificados.
- **Regla de resolución:** gana el valor verificado contra el texto de la RFP. En lo regulatorio el árbitro es Compliance (Claire Whitfield), y en el resto Revenue (Tom Callahan).
- El conflicto queda registrado en `synthesis.conflicts` y en el handoff. Es la base de los triggers de arbitraje de la Parte 3.

**¿Dónde corre el trabajo async y cómo sigue siendo verdad el ticket si el job falla a mitad?**

- **Dónde corre.** El POST guarda el PDF, crea el ticket en `analizando` y responde 202. El pipeline corre con `BackgroundTasks` de FastAPI, en un hilo del mismo proceso de la API.
- **Persistencia.** Cada nodo persiste su etapa y su resultado.
- **Si algo falla:**
  - el runner registra `stage = failed` y `error = "<etapa>: <tipo de error>"`. El estado sigue en `analizando`, porque el vocabulario de la Parte 1 no tiene un estado de error, y la UI lo muestra como «Error (reintentar)»;
  - un fallo nunca es silencioso y no afecta a otros tickets, porque cada ejecución es independiente;
  - si falla un worker, solo ese departamento queda en modo `fallback`, con una sección determinista que pide revisión manual.
- **Reproceso.** `scripts/rfp_intake.py reprocess` vuelve a ejecutar el ticket desde el PDF guardado.
- **Mejora futura.** Si el volumen crece, el mismo `run_intake` puede ejecutarse en el worker Celery que ya existe, sin cambiar el contrato.

## Pruebas

- `tests/pipelines/test_rfp_intake.py`: 29 tests. El modelo está simulado y la conversión de los PDF de muestra es real.
  - **Clasificador**: aceptar, descartar, negativa dudosa, documento vacío, reintento y error explícito.
  - **Extractor**: cifras verificadas contra el texto, país y moneda.
  - **Orquestador**: departamentos desconocidos y fallback.
  - **Workers**: extractos aislados, cifras inventadas, PHI y fallback.
  - **Synthesizer**: contradicciones.
  - **PHI**: detección y redacción.
  - **Grafo completo** con las 4 RFP: formal, informal, inválida y con PHI.
  - **Fallos**: a mitad del pipeline y de un solo worker.
- `services/api/tests/test_rfp.py`: subida con respuesta 202 y procesamiento en background, un documento rechazado que no detiene otros tickets, y validación.
