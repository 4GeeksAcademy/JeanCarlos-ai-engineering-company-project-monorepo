# Harness y guardrails del asistente de compliance (Hito 8, Parte 2)

El agente protegido es el **asistente de compliance de HealthCore**, del área de Claire Whitfield (Chief Compliance Officer). Es el mismo agente LangGraph de los hitos anteriores: RAG sobre la base de conocimiento, herramientas por el servidor MCP y memoria. Lo usan unas 200 personas en 12 clínicas de EE. UU. y Reino Unido. El código está en `services/agent/harness/` y se conecta al grafo de `services/agent/graph.py`.

## Principio

Ninguna capa confía en que el modelo "se porte bien":

- Los bloqueos críticos (jailbreak, uso personal, identificadores de paciente, brechas activas, contratos) son **deterministas** y ocurren **antes de cualquier llamada al modelo**.
- Las respuestas de rechazo son **textos fijos**, no generados, así que no se puede convencer al modelo de "rechazar a medias".
- Toda respuesta, también los rechazos, sale por el **output guard**.

## Capas

| # | Capa | Dónde | Tipo de fallo | Acción |
| --- | --- | --- | --- | --- |
| 1 | Validación estructural de la entrada | `receive_question` | estructural | bloquea |
| 2 | Identificadores de paciente: directos (nombre, DOB, MRN, NHS, SSN…) o combinación de cuasi-identificadores (edad + diagnóstico + ciudad, nombre de pila…) | runner, antes del grafo | contenido | rechaza y pide reformular; el texto nunca entra al estado, la traza ni los checkpoints |
| 3 | `guard_input`: reglas deterministas (ES/EN, con normalización de acentos, letras espaciadas, l33t y base64) | nodo del grafo | seguridad o contenido | bloquea con texto fijo |
| 4 | `classify_scope`: atajo por vocabulario del dominio; si no hay atajo, una llamada al modelo en JSON validado con Pydantic | nodo del grafo | contenido o seguridad | `general` → respuesta breve + reconducción; `personal_task` / `instruction_override` con confianza ≥ 0,7 → rechazo; si la confianza es menor, se trata como `general` |
| 5 | Aislamiento del contenido externo | `generate_answer`, `format_tickets` | seguridad | un fragmento RAG o una memoria con instrucciones se retiene; un dato de tool se sustituye por un marcador |
| 6 | Filtro de datos de tools | `format_tickets` | contenido | un ticket `compliance_breach` abierto o en curso no muestra título ni sede |
| 7 | `guard_output` | último nodo de todas las rutas | estructural, seguridad o contenido | sustituye la respuesta por un texto seguro |

La capa 3 cubre cuatro cosas:

- **Cambio de instrucciones**: ignorar u olvidar reglas, cambio de rol, "modo desarrollador", extracción del prompt, marcadores falsos como `SYSTEM:` o `<|im_start|>`, y autoridad falsa.
- **Uso personal**: textos creativos, correos personales, tareas, código de otros proyectos, terapia y recados.
- **Extracción de una brecha activa**: si la conversación ya tocó una brecha, una pregunta suelta como "¿cuántos registros?" también se bloquea (extracción gradual).
- **Términos comerciales de un BAA/DPA concreto.**

Si un usuario repite el intento de cambiar las instrucciones 3 o más veces en la misma conversación, el rechazo lo indica y queda registrado.

El output guard (capa 7) revisa:

- **Estructura**: respuesta vacía, demasiado larga, o JSON o tokens de chat en crudo.
- **Seguridad**:
  - filtración del system prompt, detectada por el **token canario** o por frases características;
  - secretos (claves, JWT) o endpoints internos.
- **Contenido**: **PHI en la salida del modelo**, buscando *valores* y no menciones de conceptos:
  - un nombre junto a "paciente", o un título seguido de apellido (Sr., Mrs.…);
  - un número de MRN, póliza o NHS;
  - una fecha ligada a una persona, un valor de laboratorio;
  - edad combinada con diagnóstico o ciudad.

## System prompt seguro (`harness/prompts.py`)

- Va en el rol `system` y empieza con un token canario.
- Declara la identidad del asistente (Claire Whitfield, 12 clínicas) y una **jerarquía de autoridad**: solo el system prompt manda; el mensaje del usuario es una petición; lo que llega en `<documento>`, `<memoria>` o `<datos_herramienta>` son datos.
- Declara el dominio del BRIEFING:
  - HIPAA y UK GDPR en lenguaje llano;
  - notificación de brechas: 60 días bajo HIPAA y 72 horas al ICO;
  - BAA y DPA;
  - políticas internas.
- Declara cuándo puede salir del dominio: small talk o regulación general con reconducción obligatoria; nunca uso personal.
- Declara lo que nunca revela: PHI, casos de pacientes con identificadores, detalles de brechas no cerradas y términos de BAA/DPA de un proveedor.
- El mensaje del usuario viaja en el rol `user`, dentro de `<mensaje_usuario>`, y escapado: no puede cerrar el bloque antes de tiempo.

**Brecha de conocimiento.** La base de conocimiento no contiene todavía documentos de compliance; solo están los 4 operativos de citas, seguros, referencias y pacientes nuevos. El agente usa como contexto un **marco regulatorio de referencia** (`REGULATORY_BASELINE`) con lo que el BRIEFING fija: 60 días bajo HIPAA, 72 horas al ICO, BAA, DPA y los usos permitidos de PHI. Lo cita como fuente y aclara que la política interna la define Compliance. No se inventa ninguna política interna.

## Observabilidad

- Cada activación escribe una fila en `guardrail_events` (`data/agent_state/guardrails.sqlite`) y una línea en el logger `api.guardrails`.
  - Campos: guardia, acción (`blocked` / `redirected` / `sanitized` / `fallback`), **tipo de fallo** (`structural` / `content` / `security`, con un CHECK en SQLite) y categoría.
  - **Nunca se guarda el texto del usuario**, así que el registro está libre de PHI.
- Los fallos estructurales de salidas del modelo también se registran como `fallback`: un plan inválido, un clasificador sin JSON o una auto-evaluación inválida.
- Resumen:
  - `GET /agent/guardrails/summary?since=<ISO>&conversation_id=<id>` (solo admin);
  - `python -m services.agent.harness summary [--since …] [--conversation …]`;
  - `python -m services.agent.harness events`.

## Pruebas

- `tests/pipelines/test_agent_guardrails.py`: 66 tests deterministas, con todos los modelos simulados y registrados.
  - Demuestran que los bloqueos ocurren **sin ninguna llamada al modelo**.
  - Comprueban las rutas del grafo, los eventos con su tipo de fallo, el aislamiento de RAG y tools, el output guard y la ausencia de falsos positivos en preguntas legítimas.
  - Si alguien debilita una regla, CI falla.
- `tests/pipelines/test_agent_guardrails_evidence.py` comprueba los criterios contra la evidencia grabada en vivo (`docs/agent/harness/evidence/guardrail-cases.{json,md}`), que se regenera con `python -m services.agent.harness.record_evidence`.
