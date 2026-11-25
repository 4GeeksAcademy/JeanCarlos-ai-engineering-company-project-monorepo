# Tiempo real: notificación de RFP nuevas por SSE (Parte 1)

Hasta ahora, el equipo de Revenue Cycle (Tom Callahan) solo se enteraba de una RFP nueva recargando el dashboard `/rfp`. Ahora el backend **empuja** un evento `rfp_ticket_created` en cuanto un ticket nuevo entra al flujo, y el dashboard lo muestra solo.

Es una capa de comunicación: ninguna parte de este flujo llama a un modelo ni a un agente.

## Piezas

| Pieza | Ruta |
| --- | --- |
| Publicación del evento (una fila por evento, sin PHI) | `data/pipelines/rfp_intake/events.py`, tabla `rfp_event` (`models.py`, `supabase/migrations/20261125020000_rfp_realtime_events.sql`) |
| Dónde se dispara | `data/pipelines/rfp_intake/graph.py`, nodo `extract_metadata` |
| Hub por proceso + generador del stream SSE | `services/api/realtime.py` |
| Endpoint `GET /api/rfp/events` (mismo JWT que el backoffice) | `services/api/routes/rfp.py` |
| Ruta Next que pasa el stream tal cual llega (BFF: cookie → `Authorization: Bearer`) | `uis/backoffice/src/app/api/rfp/events/route.ts` |
| Cliente `fetch` + `ReadableStream`: parser, backoff, watchdog, `Last-Event-ID`, deduplicación | `uis/backoffice/src/lib/sse.ts` |
| Dashboard: panel «Nuevas RFP», fila «Nueva», indicador «En vivo / reintento» | `uis/backoffice/src/app/rfp/page.tsx` |
| Pruebas | `tests/realtime/test_rfp_sse.py` (servidor real, texto en la red) y `tests/realtime/sse-client.test.mts` (cliente) |

## El evento `rfp_ticket_created`

**Cuándo se dispara:**
- **Una sola vez por ticket, justo cuando el ticket entra al flujo como RFP válida.** El clasificador ya aceptó el documento, el extractor ya tiene los datos del cliente y el ticket sigue en `analizando`, el primer estado del sistema de RFPs. El evento sale unos segundos después de la subida, antes de que el orquestador y los workers empiecen.
- **Un documento que el clasificador descarta nunca notifica**, porque no es una RFP.
- **Reprocesar un ticket no vuelve a notificar:** `(event, ticket_id)` es único.

```text
id: 11
event: rfp_ticket_created
data: {"ticket_id":"80fee564-05da-4b89-a4e7-7c59cfc33944","rfp_id":"RFP-20261125-80FEE5","client_name":"Thames Valley University","client_country":"UK","program_type":"referral_network","status":"analizando","created_at":"2026-11-25T08:52:34.719569Z"}
```

(capturado de la API local: [`evidence/wire-replay.txt`](evidence/wire-replay.txt))

**El payload:**
- Lleva **solo campos del ticket**, con los nombres del sistema de RFPs (BRIEFING §2–§3): `ticket_id`, `rfp_id`, `client_name`, `client_country`, `program_type`, `status`, `created_at`. No hay sobre `{"event","data"}`.
- `status` es el valor que guarda el sistema de RFPs para ese estado, `analizando`. Es el mismo valor que el BRIEFING de esta parte llama `analyzing`: el BRIEFING pide reutilizar exactamente los nombres que ya usa el sistema, así que no se inventa un valor nuevo para el mismo estado.
- **Sin PHI:** son contratos institucionales y el payload no contiene ningún dato de paciente. Además, cualquier valor que coincida con las reglas de PHI se retiene (fail closed), y un test publica la RFP sintética con resumen clínico y comprueba que el evento queda limpio.

Además de `rfp_ticket_created`, el stream usa otros eventos con nombre y un comentario de keep-alive:

| Evento | Para qué |
| --- | --- |
| `stream_ready` | Confirma la conexión e indica desde qué id se reanuda y cuántos eventos se van a reenviar (`replayed`). Lleva `id:` con ese cursor |
| `resync_required` | El hueco es demasiado grande para reenviarlo (más de 100) o el `Last-Event-ID` es desconocido: el cliente vuelve a leer la lista una vez. Lleva `id:` con el evento más reciente, para que no se repita |
| `session_expired` | El JWT caducó: el servidor cierra el stream |
| `: keep-alive` (comentario) | Cada 15 s, para que proxies y el watchdog del cliente vean la conexión viva |

## Servidor

**Endpoint y protección:**
- `GET /api/rfp/events` responde con `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no` y `retry: 3000`.
- Exige el **mismo JWT del backoffice** (`AuthenticatedUser`, `Authorization: Bearer`). Sin él responde 401 y no envía ningún evento.
- El stream termina cuando el JWT expira.
- Admite como máximo 5 streams simultáneos por usuario (429).

**Hub por proceso.** Hay un `EventHub` por proceso de la API: es el único lector de `rfp_event` para todas las conexiones de ese proceso y reparte cada evento a la cola de cada conexión.
- Se despierta al instante cuando el intake del mismo proceso publica.
- Consulta cada 2 s si hay eventos de otros procesos (otro worker, la CLI).
- Un cliente lento cuya cola se llena se desconecta en vez de acumular memoria; vuelve con su `Last-Event-ID` y recibe la reposición.

**Apagado sin colgar los reinicios.** El apagado ordenado de uvicorn espera a que terminen las respuestas abiertas, y un stream SSE nunca termina por sí solo. Durante la verificación en vivo, un reinicio dejó vivo el proceso viejo sirviendo los dashboards.
- `realtime.install_shutdown_hook()` (al arrancar la app) envuelve el manejador de SIGTERM/SIGINT que uvicorn ya instaló: cierra todos los streams al instante y después deja seguir el apagado de uvicorn.
- Mientras se apaga, el endpoint responde 503.
- La ruta Next propaga el fin o el error del stream de la API al navegador.

Con eso, el cliente se entera de la caída en menos de 1 s y reconecta al proceso nuevo.

**Cada conexión:**
1. Se suscribe al hub.
2. Reenvía desde la base los eventos posteriores a su `Last-Event-ID`.
3. Sigue en vivo y salta los ids que ya envió (el solape entre los pasos 1 y 2).

## Cliente

**Por qué no `EventSource`.** `EventSource` no permite enviar `Authorization` ni controlar la reconexión. En su lugar, `src/lib/sse.ts` usa `fetch` + `ReadableStream` con un parser propio del formato `text/event-stream`.

**El JWT nunca llega al JavaScript de la página.** El navegador llama a la ruta del mismo origen `/api/rfp/events` con su cookie de sesión (httpOnly). La ruta Next la reenvía a la API con el mismo JWT del backoffice como `Authorization: Bearer …` y pasa el stream byte a byte, sin buffer. Así, un XSS no puede robar el token.

**Reconexión con backoff progresivo:**
- Espera 1 s, 2 s, 4 s, 8 s, 16 s y como máximo 30 s, con un ±20 % de jitter para que muchos dashboards no reconecten a la vez.
- El contador vuelve a 0 cuando el servidor confirma el stream (`stream_ready`).
- Un watchdog aborta la conexión si pasan 45 s sin recibir ni un byte (tres keep-alives). Una conexión TCP medio abierta tras un corte de red nunca da error por sí sola.
- El evento `online` del navegador salta el resto de la espera.
- Un 401 detiene el ciclo y muestra «Sesión expirada».
- Mientras está desconectado, el dashboard **lo dice**: «Sin conexión en tiempo real · reintento 3 en 4 s». No deja de notificar en silencio.

**En la UI:**
- Cada RFP nueva aparece en un panel propio, **«Nuevas RFP en tiempo real»** (borde azul, «Nueva RFP · requiere procesamiento», con `rfp_id`, cliente, país, programa y estado).
- Se inserta en la tabla desde el payload, con la etiqueta «Nueva». No se recarga la página ni se vuelve a pedir la lista por cada evento.
- Si llegó por reposición después de una caída, lleva la etiqueta «recuperada tras reconexión».

## Recuperación tras una desconexión y deduplicación

La estrategia es **`Last-Event-ID` con reposición en el servidor**, y **lectura de la lista justo después de conectar** la primera vez.

**Por qué funciona:**
- Cada evento está en `rfp_event` y su `id` es el `id:` del SSE. El cliente guarda el último `id:` recibido y lo manda en cada reconexión, incluso después de intentos fallidos.
- El servidor reenvía los eventos posteriores (hasta 100) antes de seguir en vivo. Como están en PostgreSQL, sobreviven a un reinicio de la API.

**El cursor existe desde el primer frame** (corrección tras la revisión del PR #31). `stream_ready` y `resync_required` llevan `id:` con el cursor del servidor, y el cliente también lo toma de `data.last_event_id` como respaldo.

Antes, un dashboard que se desconectaba sin haber recibido ningún `rfp_ticket_created` no tenía `Last-Event-ID`. Al reconectar arrancaba «desde ahora» y las RFP registradas durante el corte no aparecían hasta recargar. Además, después de un `resync_required` el cursor no avanzaba y el resync se podía repetir. Ahora:
- un corte justo después de conectar reconecta con el cursor de `stream_ready`;
- un resync deja el cursor en el evento más reciente.

Lo cubren:
- `test_a_cut_before_the_first_event_still_recovers_what_arrived_during_the_cut` y `test_resync_moves_the_cursor_so_it_is_not_repeated` (servidor);
- tres tests del cliente en `sse-client.test.mts`;
- una verificación en vivo ([`evidence/live-run-cut-before-first-event.log`](evidence/live-run-cut-before-first-event.log)): reconexión con `Last-Event-ID=12` sin haber recibido ningún evento, las 2 RFP del corte recuperadas, sin recargar y sin duplicados.

**Los dos huecos que no cubre la reposición:**
- **Primera conexión:** el stream arranca en el evento más reciente y el dashboard vuelve a leer la lista al recibir `stream_ready`. Así no se pierde lo que se registró entre la carga de la página y la apertura del stream.
- **`resync_required`:** el dashboard lee la lista una vez.

**Deduplicación, en dos niveles:**
- **Servidor:** ids estrictamente crecientes por conexión, saltando el solape entre reposición y vivo.
- **Cliente:** `TicketDeduper` por `ticket_id` en los avisos. La tabla se fusiona por `ticket_id`, de modo que un ticket que ya llegó por la lista y luego por el evento, o que se reenvía dos veces, aparece **una sola vez**.

## Preguntas de diseño

**Si dos personas abren el dashboard a la vez, ¿conexiones independientes o una capa intermedia? ¿Y con 50?**

Las dos cosas, en capas distintas:
- Cada pestaña tiene **su propia conexión SSE**, porque cada una tiene su propio cursor (`Last-Event-ID`) y su propia sesión (JWT).
- Por detrás **comparten una capa intermedia por proceso**: el `EventHub` es el único lector de la tabla de eventos y reparte a todas las conexiones.

Con 50 personas hay 50 tareas asyncio en reposo con una cola cada una, una consulta a la base por cada despertar (no 50) y un keep-alive cada 15 s por conexión. Para una API async eso es poco (lo prueba el test con 3 conexiones y un solo hub).

Lo que sí hay que vigilar con 50:
- **La ruta Next** mantiene un socket hacia la API por pestaña.
- **El límite de conexiones del navegador** en HTTP/1.1 es de 6 por origen. Por eso hay un solo stream por página.
- **Con varias instancias de la API,** cada una tiene su hub. La base reparte entre ellas, porque todas leen `rfp_event`. A mayor escala, el siguiente paso sería un bus (`LISTEN/NOTIFY` de Postgres o Redis pub/sub) en lugar de consultar cada 2 s.

**¿Qué estrategia de recuperación elegiste y cómo evitas duplicados?**

`Last-Event-ID` con reposición corta en el servidor, más lectura de la lista después de conectar; los detalles están en la sección anterior. Para evitar duplicados:
- el servidor no repite ids dentro de una conexión;
- el cliente deduplica por `ticket_id`, tanto en los avisos como en la tabla;
- la base garantiza un solo evento por ticket.

Lo prueban `test_reconnecting_with_last_event_id_replays_what_was_missed_without_duplicates` y el test del cliente «after a network cut…».

**¿Por qué SSE y no WebSockets? ¿Cuándo dejaría de serlo?**

**El caso es unidireccional:** el servidor avisa y el dashboard escucha. SSE es HTTP normal, y eso trae varias ventajas:
- pasa por la misma autenticación, el mismo BFF y los mismos proxies;
- tiene ids y reposición estándar (`Last-Event-ID`);
- se reconecta sin un protocolo propio;
- no hace falta mantener estado de sesión bidireccional ni ningún servidor nuevo.

**Dejaría de ser la herramienta correcta en cuanto el mismo canal tenga que llevar mensajes del cliente al servidor con baja latencia**, por ejemplo:
- reaccionar al ticket desde la notificación («lo tomo yo», asignarlo, aprobar);
- presencia (quién está mirando el ticket);
- un chat entre Revenue y los departamentos.

Con una acción ocasional, un `POST` normal junto a SSE sigue bastando: las aprobaciones de la Parte 3 ya funcionan así. Con interacción continua o conversacional, como el chat de la Parte 2, WebSocket evita abrir una petición por mensaje y da un canal bidireccional con estado.

## Verificación en vivo

Ver [`evidence/README.md`](evidence/README.md), con capturas, logs y el texto capturado en la red:
- Dos sesiones del backoffice: la que no hace nada muestra la RFP que sube la otra **1,4 s** después, sin recargar.
- Con la API caída, la UI dice «Sin conexión en tiempo real · reintento N en X s», reintenta a ~1, 2, 4 y 8 s, y envía `Last-Event-ID` en cada intento.
- Las 2 RFP registradas durante la caída se reponen al volver la API, marcadas «recuperada tras reconexión», sin duplicar filas ni avisos.

## Cómo probar

```bash
uv run python -m pytest tests/realtime -v                    # servidor real, texto en la red (13)
node --test tests/realtime/sse-client.test.mts                 # cliente: parser, backoff, Last-Event-ID, dedup (8)

# a mano contra la API local (sin token: 401)
curl -N http://127.0.0.1:8000/api/rfp/events
curl -N -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/rfp/events
curl -N -H "Authorization: Bearer $TOKEN" -H "Last-Event-ID: 3" http://127.0.0.1:8000/api/rfp/events   # reposición
```

En la UI, abre `/rfp` en dos ventanas y sube una RFP desde una de ellas. La otra muestra el aviso «Nueva RFP» sin recargar.
