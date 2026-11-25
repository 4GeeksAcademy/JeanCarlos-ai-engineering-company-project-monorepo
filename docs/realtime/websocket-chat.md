# Tiempo real: chat del Compliance assistant por WebSocket (Parte 2)

El Compliance assistant del área de Claire Whitfield responde al personal interno (nunca a pacientes) qué está permitido bajo HIPAA y UK GDPR. Antes funcionaba con una petición y una respuesta completa: si el asistente se iba por el régimen equivocado, el usuario no podía corregirlo hasta que terminaba.

Ahora:
- habla por un **WebSocket por sesión de chat**;
- la respuesta llega **token por token** mientras el modelo la genera;
- el usuario puede **interrumpirla y redirigirla** a mitad de respuesta;
- si se cae la conexión, **se reengancha al mismo hilo**.

El agente es el mismo: el mismo grafo, nodos, herramientas, memoria y guardrails. Solo cambia el canal.

## Piezas

| Pieza | Ruta |
| --- | --- |
| Tokens desde la generación (modo `custom` de LangGraph) y abort real del stream del modelo | `data/pipelines/rag.py` (`_chat`, `GenerationCancelled`) |
| Corrida en streaming del mismo agente | `services/agent/runner.py` (`AgentRuntime.stream_run`) |
| `ChatSession` y mensajes (historial para rehidratar) | `services/agent/chat/store.py` (base de estado del agente) |
| Pub/sub en memoria: un canal `chat.<session_id>` por sesión | `services/agent/chat/bus.py` |
| Productor (turno del agente en un hilo), interrupción, snapshot y guard de salida en streaming | `services/agent/chat/manager.py` |
| REST de sesiones + `WS /agent/chat/ws` (JWT, `session_id`, eventos) | `services/agent/chat/router.py` (bajo `/agent`, en el mismo backend) |
| Cliente: `ChatSocket` (auth en el primer frame, backoff, rehidratación) y reducer de eventos | `uis/backoffice/src/lib/chat-socket.ts` |
| Chat del backoffice (el «Asistente», antes una sola pregunta y respuesta) | `uis/backoffice/src/app/knowledge/page.tsx` |
| Pruebas | `tests/realtime/test_chat_ws.py` (16, servidor) y `tests/realtime/chat-client.test.mts` (4, cliente) |

## Sesión (BRIEFING §3)

`ChatSession`: `session_id` (`chat_…`), `agent_id` (`compliance_assistant`), `user_id` (el miembro del personal autenticado), `status` (`active` / `interrupted` / `closed`) y `created_at`.

- `session_id` es también el `conversation_id` del agente, de modo que las propuestas de memoria siguen la conversación.
- Cada turno es una corrida del agente con su propio `run_id`, que es el `thread_id` de sus checkpoints LangGraph. Así no se mezcla el estado de un turno con el siguiente, que es como el agente funciona por diseño.
- La conversación se rehidrata desde el **historial de mensajes** guardado.

Cada mensaje tiene `message_id` (`msg_…`), `role`, `text` y un `status`:
- `complete`;
- `streaming`;
- `interrupted`: se conserva tal como se mostró, nunca se borra ni se sobrescribe;
- `failed`.

## Conexión

`WS /agent/chat/ws?session_id=chat_…`:
- Queda **ligada a una sesión existente** por `session_id` en la URL. Sin él responde 4400; una sesión desconocida, 4404.
- **Mismo JWT del backoffice:**
  - por `?token=` (si es inválido, se rechaza en el handshake); o
  - por un **primer frame `{"event":"auth","data":{"token":…}}`** en menos de 5 s. Es lo que usa el backoffice: así el token no aparece en URLs ni en logs de acceso.
- No se envía ningún evento del chat antes de autenticar (4401) ni a quien no tiene acceso a la sesión (4403).
- Solo puede escribir el dueño. Un admin puede unirse como **observador de solo lectura** (el supervisor de la pregunta de diseño 2).
- Next no puede hacer proxy de un upgrade de WebSocket, así que el navegador conecta directamente a la API. La ruta `GET /api/agent/chat/socket` (mismo origen, sin caché) le da la URL y el mismo JWT, que la página guarda solo en memoria para el frame de auth.

## Eventos

Todos tienen la forma `{"event", "data"}`, llevan `session_id` y no llevan PHI.

| Dirección | Evento | `data` |
| --- | --- | --- |
| cliente → servidor | `auth` | `token` |
| cliente → servidor | `user_message` | `session_id`, `text` |
| cliente → servidor | `interrupt_requested` | `session_id`, `new_input` |
| servidor → clientes | `session_snapshot` | `session_id`, `status`, `agent_id`, `messages[]`, `streaming` ({`message_id`, `sequence`} o null), `role` |
| servidor → clientes | `user_message` | `session_id`, `message_id`, `text` (ya cribado de PHI) |
| servidor → clientes | `generation_started` | `session_id`, `message_id` |
| servidor → clientes | `token_chunk` | `session_id`, `message_id`, `token`, `sequence` (1, 2, 3… por mensaje) |
| servidor → clientes | `interrupt_requested` | `session_id`, `new_input` (eco a todos los suscriptores) |
| servidor → clientes | `generation_interrupted` | `session_id`, `message_id`, `status: "interrupted"`, `sequence` |
| servidor → clientes | `generation_completed` | `session_id`, `message_id`, `status: "complete"`, `text` (respuesta final tras el guard), `sequence` |
| servidor → clientes | `generation_failed` / `session_closed` / `chat_error` | `detail` / `status` / `code`, `detail` |

## Cómo salen los tokens

- `AgentRuntime.stream_run` recorre `graph.stream(..., stream_mode=["custom", "values"])`.
- En el nodo `generate_answer`, `rag._chat` llama al modelo con `stream=True` y emite cada delta con `get_stream_writer()` (modo **`custom`** de LangGraph), solo cuando la configuración de la corrida trae `stream_tokens`. `POST /agent/query` y el resto de usos siguen sin streaming.
- No se usa el modo `messages` porque el agente llama al gateway con el cliente OpenAI, no con un chat model de LangChain.
- Los tokens pasan por el **guard de salida del agente en streaming** (`StreamGuard`): el texto se libera cuando lleva 120 caracteres de retraso respecto al último token (más que cualquier patrón de PHI o secreto) y el texto completo pasa el guard. Si aparece PHI, no se libera nada más y la respuesta final es la que decide `guard_output`.
- La respuesta final llega en `generation_completed.text`. Normalmente es lo mismo que se transmitió. Puede incluir partes deterministas del agente que no salen del modelo (datos de tickets o stock, la propuesta de memoria, una negativa), o la respuesta del guard.

## Interrupción: abort del stream, no `interrupt()` HITL

1. El cliente envía `interrupt_requested` con `new_input`.
2. El gestor activa el `cancel` (un `threading.Event`) del turno. Desde ese momento:
   - no se publica ningún `token_chunk` de esa generación;
   - el bucle de tokens de `rag._chat` lanza `GenerationCancelled` y **cierra el stream HTTP del modelo**, así que el gateway deja de generar;
   - `stream_run` ya no programa más nodos.
3. El mensaje parcial se guarda con **exactamente lo que se mostró**, con `status: interrupted`, y se publica `generation_interrupted`.
4. `new_input` se guarda como un `user_message` nuevo y arranca **un turno nuevo**: otro `message_id` y otra corrida del agente.

En la verificación en vivo, el modelo registró `ABORTED after 64/99 words`. El test lo comprueba con un modelo falso: el stream se cerró antes del final y no llega ningún `token_chunk` del mensaje interrumpido después de `generation_interrupted`.

## Pub/sub

- **Productor:** el turno del agente, en un hilo. Publica en `chat.<session_id>` y no sabe quién escucha.
- **Consumidores:** cada WebSocket de esa sesión, con su propia cola acotada.
- Un consumidor lento se desconecta (4408) en vez de frenar al productor, y al volver se rehidrata con el snapshot.
- Es un mecanismo en memoria: suficiente para un proceso, según el BRIEFING §5. Con varios procesos, la misma interfaz iría sobre Redis pub/sub o `LISTEN/NOTIFY`.

## Reconexión y rehidratación

**Cliente (`ChatSocket`):**
- Backoff progresivo: 1, 2, 4, 8 s… hasta 30 s, con jitter. Vuelve a 0 con el snapshot.
- Pide credenciales nuevas en cada intento y se conecta al **mismo `session_id`**.
- Los códigos 4401, 4403 y 4404 detienen los reintentos («Sesión expirada» / «No tienes acceso»).

**Servidor, al conectar:**
1. Suscribe la conexión al canal.
2. Envía `session_snapshot` con el historial guardado. Si hay un mensaje en curso, incluye **el texto ya mostrado y su último `sequence`**.
3. Reenvía los eventos en vivo, saltándose los `token_chunk` con `sequence` menor o igual que el del snapshot.

El reducer del cliente **no acepta tokens hasta aplicar el snapshot**, ignora los duplicados por `sequence` y, si detecta un hueco, pide un snapshot nuevo. Si la API se reinicia en mitad de una respuesta, ese mensaje queda `interrupted` con lo que se mostró.

## Preguntas de diseño

**¿Por qué WebSockets y no lo de la Parte 1? ¿Qué parte del requisito obliga a un canal bidireccional?**

**La interrupción.** El usuario tiene que mandar datos (`interrupt_requested` con `new_input`) **mientras** el servidor sigue enviando tokens de la misma respuesta, y los dos flujos deben convivir en el mismo canal y sin pisarse:
- el servidor deja de emitir los tokens viejos;
- el cliente recibe en orden `generation_interrupted` y el turno nuevo.

SSE es solo de servidor a cliente. Con SSE más un `POST` aparte habría dos canales sin orden entre sí: el `POST` de la interrupción podría llegar a otro proceso que no tiene la generación, y el cliente tendría que reconciliar la orden de los eventos de dos conexiones.

Un WebSocket ligado a la sesión lleva las dos direcciones, en orden, sobre la misma conexión autenticada.

**Si varios clientes están suscritos a la misma sesión (un supervisor observando), ¿cómo reciben todos lo mismo sin duplicar llamadas al agente?**

- **Un solo productor por sesión.** El gestor solo arranca un turno si no hay otro en curso, y rechaza un segundo `user_message` con `generation_in_progress`. Así, el agente corre una vez por turno.
- El productor publica en `chat.<session_id>` y **cada conexión es un consumidor** con su propia cola: todas reciben los mismos eventos en el mismo orden.
- Quien se conecta tarde se pone al día con el snapshot, que incluye el texto en curso y su `sequence`, en vez de volver a pedir la respuesta.
- El test `test_a_supervisor_observes_the_same_events_without_a_second_agent_call` comprueba listas de eventos idénticas y una sola llamada al modelo. En la verificación en vivo, la supervisora y el analista recibieron los mismos 97 `token_chunk`.

**¿Cómo separaste el abort del stream de `interrupt()` HITL? ¿Qué pasa con el mensaje parcial y con el siguiente turno?**

**No uso `interrupt()` para esto.** `interrupt()` pausa el grafo para que un humano decida y luego **reanuda el mismo** turno desde su checkpoint; lo uso así en la Parte 3 del flujo de RFPs. Aquí el usuario no quiere reanudar, quiere **cancelar** la generación y empezar otra. Por eso:
- el abort es un flag de cancelación que corta el stream del modelo y el recorrido del grafo;
- la corrida termina como `interrupted` en la traza del agente;
- no queda nada pendiente de reanudar.

**El mensaje parcial** se conserva con el texto que el usuario vio y `status: interrupted`: no se borra ni se sobrescribe. **El siguiente turno** es un mensaje de usuario nuevo con `new_input` y un mensaje de asistente nuevo (otro `message_id`, otra corrida), que responde a `new_input` (en el test y en vivo, UK GDPR en lugar de HIPAA).

## Cómo probar

```bash
uv run python -m pytest tests/realtime/test_chat_ws.py -v        # 16: contrato, JWT, sesión, abort medible, rehidratación, pub/sub, PHI
node --test tests/realtime/chat-client.test.mts                   # 4: reducer + reconexión del cliente
```

En la UI, abre «Asistente» (`/knowledge`) y pregunta por HIPAA. Mientras responde, escribe «espera, mi pregunta era sobre UK GDPR, no HIPAA» y pulsa «Interrumpir y enviar». Para observar como supervisor, abre la misma página como admin con `?session=chat_…`.

La verificación en vivo, con capturas, logs y los frames recibidos, está en [`evidence-chat/README.md`](evidence-chat/README.md).
