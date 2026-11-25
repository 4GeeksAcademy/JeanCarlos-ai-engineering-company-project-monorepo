# Evidencia: notificaciones SSE de RFP en vivo (2026-11-25)

**Montaje.** El backoffice local (Next, `:3001`) contra la API local (`:8000`) y PostgreSQL, con Playwright y dos sesiones. **Dashboard A** se deja abierto sin tocarlo; **dashboard B** es otra persona que sube RFPs.

> **Modelo simulado en el clasificador y el extractor.** El gateway del modelo agotó el presupuesto del equipo (`Budget has been exceeded … Max budget: 200.0`). Por eso, en esta verificación la API respondió al clasificador y al extractor con el modelo guionizado de los tests (`tests/pipelines/rfp_fixtures.py`). Todo lo demás es real: subida, PostgreSQL, pipeline, tabla `rfp_event`, stream SSE, ruta Next, navegador, caída de la API y reposición. Esta parte es una capa de comunicación y no llama a ningún modelo.

## 1. Llegada sin recargar

[`sse-1-live.png`](sse-1-live.png) → [`sse-2-arrival.png`](sse-2-arrival.png)

- A conecta su stream (`● En vivo`).
- B sube `healthcore-request-1.pdf` y se crea el ticket `RFP-20261125-AA9CD0`.
- **1,4 s después** aparece en A el aviso «Nueva RFP · requiere procesamiento» (Meridian Manufacturing, Inc., US, occupational_health, Analizando), y la fila se inserta con la etiqueta «Nueva».
- A no recargó la página ni pidió la lista por el evento.

## 2. Corte, backoff, reposición y deduplicación

[`sse-3-reconnecting.png`](sse-3-reconnecting.png) → [`sse-4-recovered.png`](sse-4-recovered.png)

Extracto de [`live-run.log`](live-run.log) y [`live-run-driver.log`](live-run-driver.log):

```
[driver] 09:52:26 API down
[9.2 s]  badge A: Sin conexión en tiempo real · reintento 1 en 1 s
[10.0 s] dashboard A → GET /api/rfp/events Last-Event-ID=10
[10.2 s] badge A: Sin conexión en tiempo real · reintento 2 en 3 s
[12.1 s] dashboard A → GET /api/rfp/events Last-Event-ID=10
[12.2 s] badge A: Sin conexión en tiempo real · reintento 3 en 5 s
[driver] 09:52:34 CLI registers healthcore-request-2.pdf while the dashboard is offline → RFP-20261125-80FEE5
[driver] 09:52:35 CLI registers healthcore-request-1.pdf while the dashboard is offline → RFP-20261125-EBB003
[16.8 s] dashboard A → GET /api/rfp/events Last-Event-ID=10
[16.8 s] badge A: Sin conexión en tiempo real · reintento 4 en 8 s
[driver] 09:52:38 API back
[24.2 s] dashboard A → GET /api/rfp/events Last-Event-ID=10
[24.5 s] dashboard A reconectado: ● En vivo
[26.0 s] aviso: … recuperada tras reconexión | RFP-20261125-EBB003 · Meridian Manufacturing, Inc. (US) …
[26.0 s] aviso: … recuperada tras reconexión | RFP-20261125-80FEE5 · Thames Valley University (UK) …
[26.0 s] filas: 24, únicas: 24; avisos: 3, únicos: 3
```

**Qué muestra:**
- **Backoff progresivo:** los reintentos esperan ~1 s, 2 s, 4 s y 8 s (con jitter).
- **Reposición:** cada intento envía `Last-Event-ID: 10`. Al volver la API, el servidor reenvió los 2 tickets registrados durante la desconexión (`replayed: 2`, ids 11 y 12) y la UI los marca «recuperada tras reconexión».
- **Sin duplicados:** 24 filas, 24 únicas; 3 avisos, 3 únicos.
- Las peticiones al stream con su `Last-Event-ID` están en [`sse-stream-requests.json`](sse-stream-requests.json).

## 2 bis. Corte antes del primer evento (tras la revisión del PR #31)

[`sse-5-cut-before-first-event.png`](sse-5-cut-before-first-event.png) · [`live-run-cut-before-first-event.log`](live-run-cut-before-first-event.log)

1. El dashboard conecta y **no recibe ningún** `rfp_ticket_created`.
2. La API cae y se registran 2 RFP por la CLI.
3. El cliente reintenta con **`Last-Event-ID=12`**, el cursor que trajo `stream_ready` en su `id:`. Antes de la corrección no enviaba cursor.
4. Al volver la API, las 2 RFP aparecen marcadas «recuperada tras reconexión».
5. Sin recargar la página y sin duplicados: 26 filas, 26 únicas.

## 3. El texto en la red

[`wire-replay.txt`](wire-replay.txt) contiene dos llamadas con `curl` a la API:
- **Sin JWT:** `401` y ningún evento.
- **Con el JWT del backoffice y `Last-Event-ID: 10`:** `content-type: text/event-stream`, `retry`, `stream_ready` con `replayed: 2`, y los dos eventos `rfp_ticket_created` con su `id:` y el payload JSON del BRIEFING.

Todos los archivos se revisaron con `find_phi` (sin PHI) y no contienen tokens.
