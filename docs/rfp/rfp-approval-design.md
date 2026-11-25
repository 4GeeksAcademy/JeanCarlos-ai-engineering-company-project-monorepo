# Flujo agéntico de RFPs: aprobación humana y cierre (Hito 9, Parte 3)

La Parte 3 toma los borradores evaluados de la Parte 2 y los somete al visto bueno del responsable nombrado de cada departamento (BRIEFING §2.1). Cuando todos aprueban, genera sola el documento final para Revenue Cycle. Es el mismo ticket y la misma fila desde la Parte 1.

## Layout

| Pieza | Ruta |
| --- | --- |
| Grafos LangGraph con checkpointer: rama por departamento + coordinador del ticket | `data/pipelines/rfp_intake/approval_graph.py` |
| Reglas: aprobadores, límites, validación de la respuesta humana, paquete de aprobación, triggers §7 | `data/pipelines/rfp_intake/approval.py` |
| Checkpointer (PostgresSaver / SqliteSaver), `thread_id` y locks por hilo | `data/pipelines/rfp_intake/checkpoint.py` |
| Ultimate document synthesizer (determinista) | `data/pipelines/rfp_intake/final_document.py` |
| Runner: abrir, reanudar un departamento, reanudar al árbitro, estado de los hilos | `data/pipelines/rfp_intake/approval_runner.py` |
| Trazabilidad de **todos** los nodos de las Partes 1, 2 y 3 | `data/pipelines/rfp_intake/tracing.py` → tabla `rfp_trace` |
| E2E reproducible con aprobaciones simuladas | `data/pipelines/rfp_intake/simulation.py`, `scripts/rfp_e2e.py`, `tests/pipelines/test_rfp_e2e.py` |
| Tablas y columnas nuevas | `models.py`, `supabase/migrations/20261125010000_rfp_approval_completion.sql` |
| HTTP en el **mismo backend** | `services/api/routes/rfp.py` |
| UI: aprobar / solicitar cambios / rechazar, arbitraje, documento final, traza | `uis/backoffice/src/app/rfp/page.tsx` |
| CLI | `scripts/rfp_intake.py approval-open | approval | decide | arbitrate | document | trace` |

## Entrada: la cola de la Parte 2

```sql
status IN ('en_evaluación', 'needs_human_review') AND ready_for_approval   -- contrato rfp_response.handoff/v1
```

Se toma con un `UPDATE` condicional (`esperando_aprobación`, `ready_for_approval = false`), así que el flujo de aprobación se abre una sola vez. La API la abre sola al terminar la Parte 2 (`RFP_AUTO_APPROVAL=0` lo desactiva; entonces `POST /api/rfp/tickets/{id}/approval`). Las secciones que agotaron el límite de la Parte 2 llegan igual, marcadas `needs_human_review` en el paquete de aprobación.

## Grafos y checkpointer

Dos grafos, un hilo con checkpoint cada uno:

| Hilo (`thread_id`) | Grafo | Qué hace |
| --- | --- | --- |
| `rfp-{ticket_id}` | coordinador | arbitraje, despacho de ramas, espera, sintetizador, cierre |
| `rfp-{ticket_id}:revenue` / `:clinical` / `:compliance` | rama de departamento | paquete → **interrupt** de aprobación → registrar → (revisar) → esperar el cierre |

```
Rama de departamento (rfp-{ticket}:{dept})
START ─┬─ directive ─→ revise ─┐
       └──────────────────────→ prepare_packet → await_approval ⏸ → record_decision ─┬ approve → await_closure ⏸ ─┬ close → END
                                     ↑                                              ├ request_changes → revise  └ reopen (árbitro) → revise
                                     └──────────────── revise ←────────────────────┘ reject → END

Coordinador (rfp-{ticket})
open_approval → arbitrate ─┬ trigger bloqueante, quedan rondas → dispatch (request_changes forzado por regla)
                           ├ trigger con árbitro humano / rondas agotadas → arbiter_decision ⏸
                           ├ sin trigger, faltan aprobaciones → dispatch → await_departments ⏸ ─┬ todas aprobadas → arbitrate
                           │                                                                  └ un rechazo → close_branches → close
                           └ sin trigger, todas aprobadas → synthesize → close_branches → close → END
```

- **Interrupt real antes de la acción irreversible.** `await_approval` llama a `interrupt()` con el paquete de aprobación. El checkpointer ya guardó la rama antes de pausar (el test lo comprueba con `get_tuple` antes de que nadie conteste).
- **Pausa solo de esa rama.** Cada departamento tiene su propio hilo; `dispatch` arranca las tres ramas en paralelo (`ThreadPoolExecutor`, cada una en un contexto limpio para que no se confunda con un subgrafo del coordinador). Aprobar Clinical mientras Revenue sigue interrumpido es reanudar un hilo distinto: no toca el checkpoint de los otros. Dos decisiones simultáneas de departamentos distintos entran a la vez (test con `Barrier`). Dos sobre el mismo hilo se serializan con `checkpoint.thread_lock` (lock en proceso + `pg_advisory_lock` en Postgres).
- **Resume como punto de entrada explícito.** `approval_runner.submit_decision` hace `graph.invoke(Command(resume=decision), config={"thread_id": "rfp-{ticket}:{dept}"})`. El grafo continúa en `await_approval`; nada anterior se vuelve a ejecutar (el test simula un reinicio de la API borrando los grafos compilados y el checkpointer en memoria, y comprueba que no hay llamadas nuevas al modelo ni un `prepare_packet` nuevo).
- **Checkpointer.** Postgres en producción: `PostgresSaver` sobre un pool de psycopg contra la misma base de los tickets (tablas `checkpoints*`, creadas por la migración y por `setup()`). En tests y con un engine SQLite: `SqliteSaver` sobre un archivo. No se usa el checkpointer en memoria.
- **Una ejecución nueva no hereda checkpoints.** Al abrir la aprobación se borran (`delete_thread`) los hilos del ticket y de sus departamentos.

## Respuesta humana validada antes de entrar al grafo

`ApprovalDecision` (Pydantic, `extra="forbid"`) + `approval.validate_decision(packet, raw)`, en la API (409 si nadie está esperando esa decisión, 422 si es inválida) **y** otra vez dentro de `await_approval`:

- `action` ∈ `approve | request_changes | reject`, y debe estar en `allowed_actions` del paquete.
- `approver` debe ser el responsable nombrado del departamento: Tom Callahan, Dr. Marcus Reid o Claire Whitfield (sin jerarquía extra).
- `comment` obligatorio (≥ 10 caracteres) para `request_changes` y `reject`; si coincide con las reglas de PHI, se rechaza: el comentario va al generador.
- `confirmed_capacity` (entero) obligatorio cuando Clinical aprueba y se conoce la población cubierta: es el dato estructurado del trigger `capacity-vs-population`.

## Límites de iteración (verificables en el código)

| Límite | Valor | Dónde se aplica |
| --- | --- | --- |
| `MAX_REVISION_ROUNDS` (`RFP_MAX_REVISION_ROUNDS`) | 2 `request_changes` por departamento | `build_packet` quita la acción de `allowed_actions`; `validate_decision` la rechaza; `record_decision` lanza error si llegara igual |
| `MAX_ARBITRATION_ROUNDS` (`RFP_MAX_ARBITRATION_ROUNDS`) | 2 rondas en que el arbitraje devuelve secciones | `arbitrate` deja de reabrir ramas; el árbitro nombrado da una resolución final (`binding`) sin otra ronda |
| Ciclo generador ↔ evaluadores de cada revisión | 3 (`RFP_MAX_ITERATIONS`, Parte 2) | el mismo subgrafo de la Parte 2 |

## Arbitraje: triggers del BRIEFING §7 en estado estructurado

`approval.detect_triggers(ticket, metadata, sections)` lee solo campos estructurados; ningún agente vota.

| Trigger | Detección | Árbitro fijo | Resolución |
| --- | --- | --- | --- |
| `phi-detected` | `evaluation_results.contains_phi` de la sección, o las reglas de PHI sobre su `draft_content` | Claire Whitfield | Regla: parada dura. La sección vuelve a su generador, su aprobación queda bloqueada y el sintetizador no corre. Con las rondas agotadas, Claire elige `discard` (→ `descartado`) o `request_new_run` |
| `baa-dpa-mismatch` | los mismos patrones que las reglas HC-BAA-US / HC-DPA-UK: compliance sin BAA+HIPAA (US) o DPA+UK GDPR (UK), o cualquier sección con el instrumento del otro país | Claire Whitfield | Regla: `request_changes` forzado en compliance y en las secciones con la cláusula incorrecta, con el problema exacto como feedback |
| `capacity-vs-population` | `confirmed_capacity` de Clinical < `rfp_metadata.covered_population` (o la población acordada), solo cuando Compliance ya aprobó y no hay `phi-detected` | Tom Callahan | Humano: `reduce_population` (Revenue revisa sus términos para N personas) o `add_sites` (Clinical revisa y vuelve a confirmar capacidad), o `request_new_run` |
| `capacity-vs-population` (fallo aplicado) | tras `reduce_population`, una sección cuyo borrador todavía contiene la cifra original (`agents.numbers_in`) | Tom Callahan (su decisión ya tomada) | Regla: la sección vuelve a su generador y su aprobación queda bloqueada hasta que la cifra desaparezca |

Cada resolución queda en `rfp_ticket.arbitration` (ronda, trigger, árbitro, regla o humano, resolución, evidencia) y en el documento final.

**Cómo se aplica `reduce_population`.**
- Se reabren Revenue y toda sección que cite la población original.
- La revisión recibe la población acordada en los metadatos y la decisión como conflicto resuelto.
- El evaluador de compliance de la Parte 2 trata la cifra original como violación HC-FIG-01 («3000 no longer applies…»). Así, el propio ciclo generador ↔ evaluadores la corrige.

## Documento final

`synthesize` corre **solo** cuando todas las secciones requeridas están `approved` (compliance siempre) y no hay trigger abierto. Consolida el texto aprobado tal cual: el sintetizador es determinista, ningún modelo reescribe lo que firmaron los aprobadores. Añade la portada (cliente, país, programa, población, moneda, BAA/DPA), la línea «Approved by…» de cada sección, la tabla de aprobaciones y el registro de arbitraje. Se niega a correr si falta una aprobación, si alguna sección tiene PHI o si la moneda no corresponde al país. Se guarda en `rfp_final_document` (`ticket_id`, `sections`, `currency`, `generated_at`, + `approvals`, `arbitration`, `markdown`) y el ticket pasa a `terminado`. Se lee en `GET /api/rfp/tickets/{id}/document` (JSON o `?format=markdown`) y en la UI (con descarga `.md`).

## Estados del ticket

| Momento | `status` | `stage` |
| --- | --- | --- |
| Entrada (de la Parte 2) | `en_evaluación` / `needs_human_review` | `generated` |
| Aprobaciones pendientes | `esperando_aprobación` | `awaiting_approval` / `arbitration` / `finalizing` |
| Documento final guardado | `terminado` | `completed` |
| Un departamento rechazó | `needs_human_review` | `approval_rejected` (Revenue relanza la Parte 2) |
| Claire descarta por PHI | `descartado` | `discarded_by_compliance` |
| Error inesperado | se conserva | `approval_failed` |

No hay estados nuevos: solo los del BRIEFING. La etapa (`stage`) dice dónde está dentro del estado.

## Trazabilidad

`tracing.traced(agent, fn)` envuelve **cada nodo de los tres grafos** (Parte 1 `:intake`, Parte 2 `:generation`, Parte 3 por hilo). Cada ejecución guarda en `rfp_trace`: nodo, agente (`agent:classifier`, `agent:generator:revenue`, `arbiter:rules (BRIEFING §7)`, `human:Tom Callahan`…), `thread_id`, input, output, estado (`ok` / `interrupted` / `error`) y timestamps de inicio y fin. En los grafos de la Parte 3 también va al estado (`trace`). Sin PHI: los textos largos se guardan como longitud + hash, los cortos pasan por las reglas de PHI, y los dos primeros nodos de la Parte 1 (que ven el PDF antes del cribado) solo registran la forma del input. Se consulta en `GET /api/rfp/tickets/{id}/trace`, en la UI y con `scripts/rfp_intake.py trace <id>`.

## Revisión de punta a punta: qué se encontró y se corrigió

Las RFP de Meridian y Thames Valley se corrieron de punta a punta por la UI y con `scripts/rfp_e2e.py`, con el modelo real. Aparecieron tres problemas de transición entre partes:

1. **Población del árbitro sin aplicar.** Tom redujo la población de 3000 a 2000. La sección de Revenue revisada seguía diciendo «3,000 students», y Clinical también, mientras la portada decía 2000. El generador había ignorado el feedback porque los extractos de la RFP dicen 3,000. Corregido en tres lugares:
   - la revisión recibe la población acordada;
   - el evaluador de compliance rechaza la cifra superada;
   - el arbitraje bloquea y devuelve cualquier sección que la cite.

   Con eso, la repetición en vivo terminó con 2,000 en todo el documento. Un ticket que había quedado a medias (RFP-20261124-2F4945) se cerró con un `request_changes` real: el ciclo corrigió la cifra.
2. **Orden de las secciones.** Las filas se guardan en el orden en que terminan los workers paralelos de la Parte 1. Por eso el handoff de la Parte 2 y la UI mostraban, por ejemplo, Compliance → Revenue → Clinical, mientras el documento final usa el orden del BRIEFING. Ahora `ticket_view` y `build_response_handoff` ordenan siempre revenue → clinical → compliance. Lo detectó el test E2E de forma intermitente.
3. **Indicador de progreso.** La UI mostraba «Aprobación por departamento…» como si fuera un paso en curso de la Parte 2. Ahora ese indicador solo aparece en `generando_borrador` / `en_evaluación`.

## Preguntas de diseño

**¿Qué pasa si un departamento rechaza su sección después de la interrupción? ¿Vuelve al generador de la Parte 2 o requiere una nueva ejecución?**
Depende de la decisión, y las dos existen:
- `request_changes` vuelve al **generador de la Parte 2 de esa sección**, dentro del mismo hilo: el nodo `revise` ejecuta el mismo subgrafo generador → 3 evaluadores en paralelo → juez de la Parte 2, con el comentario del aprobador como feedback y el borrador anterior. Después vuelve a pedir la aprobación. Los demás departamentos no se enteran ni esperan. Está limitado a 2 rondas por departamento.
- `reject` es definitivo para esta ejecución: la rama termina, las ramas pendientes se cancelan, el sintetizador no corre y el ticket queda en `needs_human_review` / `approval_rejected`. Requiere **una nueva ejecución**: Revenue (dueño del ticket) relanza la Parte 2 (`POST /generate?retry=true` o el botón «Nueva ejecución»), que vuelve a abrir la Parte 3 con hilos nuevos.

**¿Qué información mínima necesita ver un humano en el punto de aprobación para decidir con confianza, sin releer todo el documento?**
El paquete de aprobación (`approval.build_packet`, guardado en `approval_packet`) contiene:
- su sección (≈ 250–550 palabras);
- el veredicto de los tres evaluadores: si pasó, qué aspectos clave faltan, qué reglas falló;
- si agotó el límite de la Parte 2 (`needs_human_review`) y cuántas iteraciones usó;
- los datos que su sección debe respetar: país, moneda, BAA/DPA, población;
- las preguntas abiertas de la sección;
- los triggers que la afectan, con su árbitro;
- cuántas revisiones lleva de cuántas;
- qué acciones puede tomar y, si alguna está bloqueada, por qué.

No necesita la RFP completa ni las secciones de los otros departamentos.

**¿Cómo detectas los triggers del BRIEFING en estado estructurado, para que el arbitraje sea real?**
Cada trigger es una comparación de campos, no una lectura de texto libre de un agente:
- `phi-detected`: `contains_phi` del `EvaluationResult` + las reglas de PHI sobre el borrador guardado.
- `baa-dpa-mismatch`: `client_country` de los metadatos contra los patrones de BAA/HIPAA/DPA/UK GDPR, los mismos de las reglas de compliance de la Parte 2.
- `capacity-vs-population`: la capacidad que Dr. Marcus Reid **introduce como número al aprobar** contra `covered_population`.

El nodo `arbitrate` corre al abrir (antes de pedir aprobaciones) y cuando todas están dadas (antes del sintetizador). Su salida cambia el grafo: reabre ramas con feedback, bloquea la aprobación, interrumpe para el árbitro o impide el documento. Los tests prueban cada trigger y que el árbitro equivocado es rechazado.

## Pruebas

- `tests/pipelines/test_rfp_approval.py` (24):
  - interrupt + checkpoint persistido;
  - resume tras «reinicio» sin re-ejecutar nada;
  - aprobar B con A interrumpido, con aserciones sobre la traza;
  - dos decisiones simultáneas;
  - `thread_id` por ticket y departamento;
  - documento solo con todas las aprobaciones;
  - validación de respuestas (6 casos);
  - `request_changes` → generador de la Parte 2;
  - límite de revisiones;
  - rechazo;
  - `baa-dpa-mismatch` por regla;
  - `phi-detected` como parada dura;
  - capacidad solo tras Compliance;
  - `reduce_population`;
  - sección que ignora la población del árbitro;
  - detección de población obsoleta en estado estructurado;
  - límite de arbitraje;
  - traza con agente, input, output y timestamp.
- `tests/pipelines/test_rfp_e2e.py`: Partes 1 → 2 → 3 con aprobaciones simuladas. El plan `standard` (Meridian, US) incluye un `request_changes`; el plan `capacity` (Thames Valley, UK) incluye el arbitraje de Tom Callahan. Comprueba la secuencia de estados sin saltos y la consistencia de datos (moneda, secciones, texto aprobado = texto del documento).
- `services/api/tests/test_rfp.py`: endpoints de aprobación, arbitraje, documento y traza.
- `scripts/rfp_e2e.py <pdf> --plan standard|capacity --out <dir>`: la misma ruta E2E contra el modelo real y la base del backend.
