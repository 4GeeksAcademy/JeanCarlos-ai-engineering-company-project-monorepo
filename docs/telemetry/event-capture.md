# Telemetría: captura en backoffice

## Alcance implementado

Contrato: [event-schemas.json](./event-schemas.json), versión `1.0.0`.
Diseño: [telemetry-plan.md](./telemetry-plan.md).

Se instrumentaron los flujos existentes, como se acordó: **19 de 32 eventos**,
incluidos **5 de los 14 obligatorios** del catálogo. No se inventan productores
clínicos ni eventos de un gateway inexistente. Esto no equivale a cubrir todo el
BRIEFING ni a implementar almacenamiento o auditoría de seguridad confiable.

Todos los eventos pasan por `track()` en
[`telemetry.ts`](../../uis/backoffice/src/services/telemetry.ts). Los componentes
no construyen envelopes ni envían lotes directamente.

| Evento | Clasificación | Punto de captura |
| --- | --- | --- |
| `inventory_inbound_order_created` | Obligatorio, RFI | Cliente inventory, respuesta exitosa del alta |
| `inventory_outbound_order_created` | Obligatorio, RFI | Cliente inventory, respuesta exitosa del alta |
| `inventory_order_validation_failed` | Obligatorio, RFI | Cliente inventory, HTTP 422; campos/códigos permitidos, agrupación de 5 s |
| `inventory_outbound_insufficient_stock_rejected` | Obligatorio, RFI | Cliente inventory, HTTP 400 y stock autoritativo en `X-Available-Stock` |
| `inventory_stock_threshold_triggered` | Obligatorio, RFI | Comparación de lecturas válidas del catálogo: cruce observado de >10 a <=10 |
| `inventory_orders_viewed` | Propuesta | Historial cargado correctamente; deduplicación de 30 s |
| `supplier_created` | Propuesta | Cliente suppliers, alta confirmada |
| `supplier_rate_updated` | Propuesta | Cambio confirmado; dirección respecto a la tarifa mostrada previamente, sin monto |
| `supplier_status_changed` | Propuesta | Cambio confirmado; estado previo mostrado y nuevo, sin ID |
| `supplier_filter_applied` | Propuesta | Listado filtrado válido; debounce de 500 ms y tamaño agrupado |
| `form_flow_started` | Propuesta | Primer cambio en formulario de entrada/salida |
| `form_flow_abandoned` | Propuesta | Desmontaje/pagehide de formulario iniciado sin éxito; no se atribuye abandono durante un envío pendiente |
| `frontend_route_viewed` | Propuesta | Provider de navegación; solo rutas canónicas, deduplicación de 30 s |
| `frontend_unhandled_error` | Propuesta | `error`, `unhandledrejection` y Error Boundary; código fijo, deduplicación de 30 s |
| `api_latency_aggregated` | Propuesta | `telemetryFetch`, ventanas de 60 s; p50/p95 y volumen de respuestas HTTP |
| `auth_login_succeeded` | Propuesta | Helper de autenticación; tras recuperar rol e identidad seudónima |
| `auth_login_failed` | Propuesta | Helper de autenticación; razón normalizada, sin credenciales |
| `auth_session_rejected` | Propuesta | HTTP 401 de inventario/proveedores; razón genérica `invalid`, sin inferir expiración |
| `auth_password_recovery_requested` | Propuesta | Respuesta neutral exitosa de recuperación |

La latencia se mide desde el navegador hasta el proxy de Next.js y su API, no
como duración exclusiva de FastAPI. Se conservan hasta 1000 muestras por grupo
ruta/método/clase HTTP por ventana; no incluye solicitudes abortadas o sin
respuesta HTTP. La detección de stock bajo es observacional por pestaña, no un
evento global garantizado después de cada commit. Los cambios de proveedor
usan el valor previamente mostrado, que puede quedar obsoleto entre operadores.

### Eventos pendientes

Los ocho obligatorios de claims, citas/no-shows y CME/licencias no tienen
productor en este backoffice. `inventory_direct_stock_edit_rejected` requiere
un gateway confiable que todavía no existe: un 404 del navegador no prueba un
intento de evadir controles. Permanecen documentados, pero no se emiten.

Cuatro propuestas siguen pendientes: `user_admin_action_completed`,
`profile_access_denied`, `inventory_product_created` y
`api_error_aggregated`. Los formularios de proveedor no capturan aún el funnel
start/abandon; sí se capturan sus operaciones confirmadas. No se añadieron
Web Vitals, una actividad opcional que requiere ampliar el contrato aprobado.

## Configuración

En local, exportar antes de iniciar Next.js, sin incluir secretos en variables
`NEXT_PUBLIC_*`:

```bash
export NEXT_PUBLIC_TELEMETRY_ENDPOINT=/api/telemetry/events
export TELEMETRY_ENDPOINT=http://127.0.0.1:8000/telemetry/events
export SUPPLIERS_API_URL=http://127.0.0.1:8000
cd uis/backoffice
npm run dev
```

FastAPI lee `TELEMETRY_ENDPOINT` al iniciar y lo conserva en
`app.state.telemetry_endpoint`; por ahora no redirige tráfico ni persiste nada.
Su valor predeterminado es `http://localhost:8000/telemetry/events`. Exportar la
variable también en la terminal que inicia la API. Sus variables existentes de
JWT, almacenamiento y correo siguen siendo necesarias.

Compose ya proporciona los valores de telemetría a UI y API, con
`http://api:8000/telemetry/events` como destino interno predeterminado.

```bash
docker compose up --build
```

En Next.js de producción, `NEXT_PUBLIC_TELEMETRY_ENDPOINT` debe estar definido
**al ejecutar `npm run build`**, no únicamente al arrancar `npm run start`.
Si falta o apunta fuera del origen, se avisa por consola y se descarta el evento.
Los archivos de entorno existentes restringidos no se modificaron; los exports
y la configuración de Compose permiten habilitarlo sin acceder a ellos.

Se usa un proxy del mismo origen, `/api/telemetry/events`, en vez de enviar desde
el navegador directamente al puerto 8000: evita CORS, permite beacon y mantiene
el Bearer en el servidor. El destino real del proxy se configura con
`TELEMETRY_ENDPOINT`. Solo se aceptan destinos públicos del mismo origen en la
variable del navegador. La ruta proxy comprueba Origin y limita el body a
60000 bytes; no permite cambiar el destino desde el payload.

## Contrato compartido y contenedores

El catálogo canónico sigue en `docs/telemetry`. La UI importa una proyección
generada y versionada, sin descripciones editoriales, dentro de su contexto
Docker. Así no depende de archivos externos a `uis/` al compilar.

Tras modificar el catálogo, desde la raíz:

```bash
node scripts/generate-telemetry-contract.mjs
node scripts/generate-telemetry-contract.mjs --check
```

Versionar también
[`telemetry-contract.json`](../../uis/backoffice/src/services/telemetry-contract.json).
`--check` falla si no coincide con la proyección canónica; no modifica archivos.

## Entrega, identidad y privacidad

- Envelope automático: UUID de evento al capturar, timestamp ISO, UUID de
  sesión en `sessionStorage` (memoria si no está disponible), versión compartida,
  usuario seudónimo o `null`, y request UUID. El ID de sesión rota al login.
- `GET /telemetry/session` requiere la cookie autenticada a través del proxy.
  Devuelve un HMAC SHA-256 del ID interno, con dominio separado y secreto de
  firma de la API; nunca el email ni el ID de cuenta crudo. La rotación del
  secreto cambia los seudónimos. No son datos anónimos: siguen requiriendo
  políticas de acceso, retención y privacidad.
- La identidad se obtiene de forma asíncrona; los primeros eventos pueden ser
  anónimos. Si falla la consulta, se informa en consola y no se bloquea la UI.
- Request IDs de las llamadas instrumentadas se propagan por Next.js/FastAPI y
  se recuperan del header de respuesta para eventos asociados. Los eventos
  locales generan su propio UUID de correlación.
- Allowlist de nombres y propiedades, validación de tipos/enums y límites
  numéricos antes de encolar. Se rechazan rutas no canónicas. No se envían
  nombres, emails, contraseñas, tokens, notas, mensajes de error, stacks,
  términos de búsqueda ni valores inválidos de formularios.
- Cada lote contiene entre 1 y 20 eventos; flush cada **10 s** o al alcanzar
  **20 eventos**. Se sigue el paso detallado del reto; su rúbrica menciona 20 s.
- Una solicitud inicial y hasta tres reintentos, esperando 1, 2 y 4 s; timeout
  de 10 s por intento. Tras agotar reintentos se informa y descarta el lote.
  Los 4xx definitivos se descartan; 429 sí se reintenta. Se comprueba
  `{ "received": N }`, no solo el status.
- Cola principal limitada a 200 eventos, descartando el más antiguo si se
  llena; hasta 200 grupos pendientes para debounce/validaciones. Límite de
  2500 bytes por evento; sin almacenamiento persistente.
- Al ocultar la página se intentan enviar todos los lotes mediante beacon.
  Si el navegador rechaza uno por cuota, se conserva lo restante en memoria.
  Al añadir eventos durante el cierre también se intenta beacon. No hay
  garantía de entrega: aceptar un beacon no confirma el procesamiento HTTP.
- Puede duplicarse un lote que estaba en vuelo al ocultarse la pestaña.
  Se mantienen los mismos `eventId`; la Fase 3 deberá deduplicarlos.
  La cola se pierde si el proceso/pestaña se destruye sin entrega.

El stub público valida el **envelope**, límites de lote y tipos JSON;
`POST /telemetry/events` devuelve `{ "received": N }`. Solo registra cantidad y
nombres de eventos, no sus propiedades. Los errores 422 no reflejan la entrada.
No escribe en TinyDB, SQL ni Supabase. Todavía no valida el catálogo completo
del lado servidor, ni autentica los identificadores enviados por clientes:
no debe tratarse como fuente de auditoría confiable. Antes de exposición pública
en producción se necesitan límites de tráfico/body en el servidor/gateway,
validación del catálogo en servidor y vinculación de identidad confiable.

## Verificación realizada

- Backoffice y Talent Tracker: `npm run lint` y `npm run build`, correctos.
- Regresión backend focalizada: 12 pruebas de autenticación/cache, correctas.
- API con almacenamiento temporal: lotes de 1/20, rechazo de lote vacío/21,
  UUID/timestamp/extra inválidos, ausencia de persistencia, request ID,
  identidad HMAC autenticada y logs/errores sin datos enviados.
- Servicio ejecutado en entorno de navegador simulado: 10 s/20 eventos,
  metadatos, allowlists, deduplicación, tres reintentos y descarte, beacon de
  45 eventos en lotes 20/20/5, agrupación 422 y debounce de filtros.
- Integración HTTP real con Next.js y FastAPI temporales: proxy 200 y
  `{ "received": 1 }`; lotes inválidos 422, origen externo 403, body grande 413
  y sesión anónima 401. Se detuvieron los servidores y eliminó el almacenamiento
  temporal después de la comprobación.

No se realizó verificación visual en DevTools ni se generó captura de pantalla.
El entorno de navegador preparado anteriormente no dispone de todas sus
bibliotecas de sistema; las comprobaciones anteriores no sustituyen esa captura.

### Comprobación manual pendiente

1. Iniciar UI/API con las variables anteriores.
2. Abrir Network en DevTools y filtrar `telemetry/events`.
3. Navegar por catálogo/historial, registrar una orden con datos de desarrollo
   y esperar 10 s; también probar un rechazo por stock insuficiente.
4. Verificar `events: []`, metadatos automáticos, allowlists y respuesta
   `200 { "received": N }`; ocultar la pestaña para comprobar beacon.
5. Capturar únicamente el lote sintético y su respuesta, sin cookies, tokens
   ni datos reales. Adjuntar la captura al PR `feat: telemetry event-capture`.
