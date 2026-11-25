# Evidencia: chat del Compliance assistant por WebSocket en vivo (2026-11-25)

**Montaje.** El backoffice local (Next, `:3001`) contra la API local (`:8000`), con Playwright y dos navegadores:
- **Analista de Compliance** (`analista.compliance`, rol `user`): chatea en «Asistente».
- **Supervisora** (`demo`, admin): observa la misma sesión en solo lectura (`?session=chat_…`).

> **Modelo de demostración.** El gateway del modelo sigue sin presupuesto (`Budget has been exceeded … Max budget: 200.0`). Por eso, la API se arrancó con un modelo que **emite palabra a palabra** con `stream=True`, como el gateway, y registra cuándo su stream se cierra antes del final ([`demo-model.log`](demo-model.log)). También se sustituyeron el recuperador y el clasificador de alcance. Todo lo demás es real: el grafo del agente, sus guardrails, la API, el WebSocket, el pub/sub, el almacén de sesiones y los navegadores. El lanzador es local y no está en el repositorio.

## 1. Tokens conforme se generan

[`ws-1-streaming.png`](ws-1-streaming.png) · supervisora: [`ws-1-streaming-supervisor.png`](ws-1-streaming-supervisor.png)

La longitud del mensaje en pantalla, medida cada 100 ms, crece sin parar: `1 → 6 → 18 → 31 → 39 → … → 256 → 267` caracteres ([`live-run.log`](live-run.log)).

## 2. Interrupción a mitad de respuesta

[`ws-2-interrupted.png`](ws-2-interrupted.png) · supervisora: [`ws-2-interrupted-supervisor.png`](ws-2-interrupted-supervisor.png)

- Mientras responde sobre HIPAA, el analista envía «espera, mi pregunta era sobre UK GDPR, no HIPAA» con «Interrumpir y enviar».
- **El modelo dejó de generar:** `[demo model] stream closed: ABORTED after 64/99 words`.
- El mensaje parcial quedó con **277 caracteres**, marcado «Interrumpida», **sin cambios** 1,5 s después.
- Debajo aparecen un mensaje nuevo del usuario con `new_input` y **una respuesta nueva** sobre UK GDPR: `user · assistant(interrupted) · user · assistant(complete)`.
- En [`ws-frames.json`](ws-frames.json), los eventos del analista están en orden: `session_snapshot, user_message, generation_started, token_chunk…, interrupt_requested, generation_interrupted, user_message, generation_started, token_chunk…, generation_completed`. No hay ningún `token_chunk` del mensaje interrumpido después de `generation_interrupted`.

## 3. Pub/sub: la supervisora ve lo mismo sin otra llamada al agente

- La supervisora recibió **los mismos 97 `token_chunk`** que el analista y las mismas 4 burbujas.
- El modelo se llamó una vez por turno: 2 corridas en total, la interrumpida y la nueva.

## 4. Caída de la API, reconexión y rehidratación

[`ws-3-reconnecting.png`](ws-3-reconnecting.png) → [`ws-4-rehydrated.png`](ws-4-rehydrated.png)

- Con la API caída 11 s, el indicador mostró «Sin conexión · reintento 1 en 2 s → 2 en 2 s → 3 en 4 s → 4 en 9 s».
- Al volver la API, el cliente reconectó con el mismo `session_id` y recibió el `session_snapshot`.
- Después, **una recarga completa de la página** restauró desde el servidor la conversación entera: los 4 mensajes con sus estados, incluido el interrumpido. No quedó un chat vacío.

Todos los archivos se revisaron con `find_phi` (sin PHI) y no contienen tokens: en `ws-frames.json` solo se guardan los nombres de evento, ids, `sequence` y longitudes.
