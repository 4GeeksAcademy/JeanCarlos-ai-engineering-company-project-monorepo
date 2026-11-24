# Flujo agéntico de RFPs: generación y evaluación de respuestas (Hito 9, Parte 2)

La Parte 2 convierte el análisis de la Parte 1 en un **primer borrador de la propuesta económica**, con una sección por departamento. Cada sección se autoevalúa antes de que la vea un humano.

## Layout

| Pieza | Ruta |
| --- | --- |
| Grafo LangGraph dedicado `rfp_response` (con un subgrafo por sección) | `data/pipelines/rfp_intake/response_graph.py` |
| Un generador por departamento: `RevenueGenerator`, `ClinicalGenerator`, `ComplianceGenerator` | `data/pipelines/rfp_intake/generators.py` |
| Evaluadores de legibilidad, pertinencia y compliance, y el contrato `EvaluationResult` | `data/pipelines/rfp_intake/evaluators.py` |
| Lineamientos del BRIEFING §5 como reglas con id, formato de cada sección y umbrales | `data/pipelines/rfp_intake/guidelines.py` |
| Runner: toma el ticket de la cola, registra fallos y permite reintentar | `data/pipelines/rfp_intake/response_runner.py` |
| Handoff a la Parte 3 (`rfp_response.handoff/v1`) | `data/pipelines/rfp_intake/handoff.py` |
| Columnas nuevas | `data/pipelines/rfp_intake/models.py`, `supabase/migrations/20261124010000_rfp_response_generation.sql` |
| HTTP en el **mismo backend** | `services/api/routes/rfp.py` (`POST /api/rfp/tickets/{id}/generate`) |
| UI: borradores, `EvaluationResult` e historial | `uis/backoffice/src/app/rfp/page.tsx` |
| CLI | `scripts/rfp_intake.py generate | generate-retry | generate-ready` |

## Entrada: el handoff de la Parte 1 (no el PDF)

La Parte 2 solo procesa tickets que la Parte 1 dejó en su cola:

```sql
status = 'analisis_completo' AND ready_for_generation   -- contrato rfp_intake.handoff/v1, route = proposal_generation
```

**Cómo se toma el ticket.** Se hace con un único `UPDATE` condicional: pasa a `generando_borrador` y `ready_for_generation` pasa a `false`. Así, dos disparos (el automático después de la Parte 1 y el botón o la CLI) nunca generan el mismo ticket dos veces.

**Qué tickets quedan fuera:**

- Un ticket con PHI tiene `route = compliance_review` y `ready_for_generation = false`, así que **nunca llega a un generador**.
- Un ticket `descartado` tampoco.

**Qué recibe cada generador.** Todo sale del handoff y del ticket; el PDF no se vuelve a leer:

- los metadatos compartidos, la moneda y las cláusulas requeridas;
- el resumen del synthesizer para Revenue;
- **su** workstream: `key_aspects`, `requests` y `open_questions`;
- los conflictos que le afectan;
- los extractos que el orquestador le asignó, tomados de `document_markdown`, que ya está redactado.

## Grafo

```
prepare ─ Send(section) × departamentos (en paralelo) → finalize

section (subgrafo, uno por departamento):
  generate ─┬→ evaluate_readability ─┐
            ├→ evaluate_relevance  ──┼→ judge ─ overall_pass ───────────────→ finish (passed)
            └→ evaluate_compliance ─┘        ├ falla, iteración < límite ─→ generate (con feedback_for_generator)
                                             └ falla, límite agotado ─────→ finish (needs_human_review)
```

- **Generadores separados.** Cada departamento tiene su clase, su prompt, su contexto de empresa y el formato de su sección (BRIEFING §2.1):
  - **Revenue:** términos financieros en la moneda del país. Sin importe ni plazo de pago inventados: pone `[amount to be confirmed by Revenue Cycle]`.
  - **Clinical:** solo las clínicas reales de HealthCore del país del cliente (`data/raw/clinics.csv`). Para una red de referidos usa además el proceso de referidos de la base de conocimiento (mejora opcional).
  - **Compliance:** BAA bajo HIPAA (EE. UU.) o DPA bajo UK GDPR (Reino Unido), reportes solo agregados y aprobación obligatoria de Claire Whitfield.
- **Idioma.** Las secciones se redactan en **inglés**: son la propuesta que recibe un cliente de EE. UU. o Reino Unido, y las fórmulas de py-readability-metrics están calibradas para inglés.
- **Límite de iteraciones.** Es de 3 ciclos generador → evaluadores por sección (`guidelines.MAX_ITERATIONS`, configurable con `RFP_MAX_ITERATIONS`).
- **Si una sección agota el límite:**
  - conserva el **último borrador y su `EvaluationResult`**;
  - pasa a `needs_human_review` y el ticket también;
  - viaja igual en el handoff a la Parte 3.

  El ticket nunca se descarta.
- **Si un generador no responde,** solo afecta a su sección, que queda en `needs_human_review`. Los otros departamentos siguen.
- **PHI en un borrador.** La salida cruda del modelo se pasa por `phi.screen` **antes** de guardarla o de reenviarla:
  - se guarda solo la versión redactada;
  - el evaluador de compliance marca `contains_phi: true` y la regla `HC-PHI-01`;
  - el handoff emite la señal `phi-detected` para el árbitro de la Parte 3.

## `EvaluationResult` (persistido en `rfp_department_section.evaluation_results`)

```text
EvaluationResult:
  section_id: "<ticket_id>:<department_id>", department_id, iteration
  readability: { pass, score (grado Flesch-Kincaid), details { words, flesch_reading_ease, gunning_fog, long_sentences[], issues[] } }
  relevance:   { pass, score, missing_aspects[], covered_aspects[], evaluator (model | fallback), details { gaps, unverified_evidence } }
  compliance:  { pass, rule_ids[], violations[{ rule_id, message }], contains_phi }
  overall_pass, contains_phi
  feedback_for_generator: string   # una línea concreta por problema, con el evaluador o regla que lo origina
  evaluated_at
```

Además se guarda `evaluation_history`, con el borrador redactado y el `EvaluationResult` de cada iteración, y `iterations`.

| Evaluador | Qué recibe | Cómo decide |
| --- | --- | --- |
| **Legibilidad** | solo el borrador | Usa py-readability-metrics. Entre 120 y 550 palabras, grado Flesch-Kincaid ≤ 14 y facilidad de lectura Flesch ≥ 30. El feedback lista las frases largas que hay que partir. |
| **Pertinencia** | el borrador y los `key_aspects` de **su** departamento | Un modelo juez decide aspecto por aspecto y debe **citar literalmente** la frase que lo cubre; la cita se verifica contra el borrador. Si el modelo no está disponible, cubre por palabras clave (`evaluator: fallback`). |
| **Compliance** | el borrador, `client_country` y las cifras permitidas | Determinista, con las reglas del BRIEFING §5 por id (tabla siguiente). Nada es opinión del agente. |

| Regla | Lineamiento (BRIEFING) |
| --- | --- |
| `HC-PHI-01` | Ningún nombre, diagnóstico ni identificador de paciente, ni siquiera de ejemplo (§5.1). |
| `HC-BAA-US` | Cliente de EE. UU.: cláusula BAA bajo HIPAA, nunca DPA ni UK GDPR (§5.2, trigger `baa-dpa-mismatch`). |
| `HC-DPA-UK` | Cliente del Reino Unido: cláusula DPA con UK GDPR, nunca BAA ni HIPAA (§5.3). |
| `HC-CUR-01` | USD para EE. UU. y GBP para el Reino Unido, según `client_country`; ninguna otra moneda (§5.4). |
| `HC-CMP-01` | La aprobación de Compliance es obligatoria: nadie puede decir que es opcional, y la sección de Compliance nombra a Claire Whitfield (§5.5, §6). |
| `HC-FIG-01` | Ninguna cifra inventada (población, precios, plazos): toda cifra está en el handoff, en los extractos o en los datos de HealthCore (§2.3). |
| `HC-FMT-01` | El formato de la sección del departamento: título y encabezados obligatorios, con «Open questions» (§2.1). |

## Estados del ticket (en tiempo real)

| Momento | `status` | `stage` | Sección (`generation_status`) |
| --- | --- | --- | --- |
| Se toma de la cola | `generando_borrador` | `generating` | `generating` (iteración 1) |
| Primer borrador listo | `en_evaluación` | `evaluating` | `evaluating` |
| Vuelve al generador | `en_evaluación` | `evaluating` | `revising` (iteración n) |
| Fin, todo aprobado | `en_evaluación` | `generated` | `passed` |
| Fin, alguna sección agotó el límite | `needs_human_review` | `generated` | `needs_human_review` |
| Error inesperado | (sin cambio) | `generation_failed` + `error` | se reintenta con `?retry=true` o `generate-retry` |

`analisis_completo` no se reescribe: la Parte 2 parte de él. `esperando_aprobación` y `terminado` los escribe la Parte 3.

## Handoff a la Parte 3 (`rfp_response.handoff/v1`)

El handoff se guarda en `rfp_ticket.response_handoff` junto con la bandera de cola `ready_for_approval`. La Parte 3 consulta `status IN ('en_evaluación','needs_human_review') AND ready_for_approval`.

El handoff contiene:

- **`sections[]`:** por cada departamento, `draft_content`, `evaluation_result`, `status` (`passed` o `needs_human_review`), `iterations`, `contains_phi` y el `approver`, que es el responsable del BRIEFING §2.1;
- `needs_human_review[]`, `currency`, `required_clauses` y `compliance_approval_required: true`;
- **`signals[]`:** los triggers del BRIEFING §7 que se detectan en el estado estructurado (`phi-detected`, `baa-dpa-mismatch`). La Parte 2 solo los **señala**; los resuelve el nodo de arbitraje de la Parte 3.

## Preguntas de diseño

**¿Qué información del estado necesita realmente cada agente evaluador? ¿Recibe solo la sección o el documento completo?**
Solo la sección y lo mínimo para juzgarla. Ningún evaluador recibe la RFP completa ni la sección de otro departamento.

- **Legibilidad:** solo el borrador.
- **Pertinencia:** el borrador y los `key_aspects` de su departamento, que son la vara de "lo que pide la RFP" que fijó la Parte 1.
- **Compliance:** el borrador, el país del cliente y el conjunto de cifras permitidas. Ese conjunto se calcula de antemano a partir del handoff, los extractos y el contexto de empresa, de modo que el evaluador no necesita ver esas fuentes.

Así cada evaluación es barata, no se contamina con texto ajeno y es reproducible.

**¿Cómo evitas que dos evaluadores en paralelo entren en conflicto al escribir en el estado compartido?**

- Cada evaluador escribe **una sola clave propia** del estado de la sección (`readability`, `relevance` o `compliance`), y ninguno toca las de los otros. LangGraph aplica las tres actualizaciones del mismo paso sin colisión porque son canales distintos.
- `judge` espera a los tres (fan-in) y es el **único escritor** del `EvaluationResult` y de la fila en la base de datos.
- Entre departamentos pasa lo mismo:
  - cada subgrafo escribe solo su fila `rfp_department_section`;
  - devuelve una entrada a un reducer de solo-anexar (`operator.add`);
  - los cambios del ticket compartido son `UPDATE` condicionales (`generando_borrador` → `en_evaluación` solo una vez).

  Por eso un departamento lento o que falla no bloquea a los demás.

## Pruebas

- **`tests/pipelines/test_rfp_response.py`** (24 tests, con el modelo simulado):
  - **Generadores:** uno por departamento, entrada desde el handoff y feedback en la revisión.
  - **Evaluadores:**
    - legibilidad que pasa y que falla;
    - pertinencia con cita no verificable y con fallback;
    - **fallos de compliance anclados al BRIEFING**: GBP para un cliente de EE. UU., BAA para un cliente del Reino Unido, PHI, aprobación de Compliance «opcional», cifras inventadas y formato.
  - **Ciclo completo:**
    - una sección que falla, vuelve con feedback y pasa;
    - el límite de iteraciones, que lleva a `needs_human_review` con el borrador conservado;
    - el límite configurable;
    - PHI redactada antes de guardarse;
    - un generador caído que no afecta a los demás;
    - la cola de la Parte 1 y el doble disparo;
    - el progreso del ticket;
    - un fallo y su reintento;
    - las ramas paralelas del grafo.
- **`services/api/tests/test_rfp.py`:** la subida encadena la Parte 2, y `POST /generate` responde 202 o 409.
