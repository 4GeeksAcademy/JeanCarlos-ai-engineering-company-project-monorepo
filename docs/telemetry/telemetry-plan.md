# Plan de telemetría — HealthCore

**Estado:** diseño previo a instrumentación. No añade ni afirma eventos ya
instrumentados.  
**Ámbito:** sistema de inventario de insumos clínicos de HealthCore (BRIEFING
de telemetría, §1–§6) y superficies existentes del backoffice/API.  
**Versión del contrato:** `2.1.0` (aditiva sobre `2.0.0`, ver [«Cambio aditivo 2.1.0»](#cambio-aditivo-210)). `2.0.0` fue un cambio incompatible respecto a `1.0.0`:
renombrado de eventos obligatorios y nuevos campos mínimos; ver
[Migración desde 1.0.0](#migración-desde-100)).

## Alcance y fuentes de verdad

La fuente de las métricas obligatorias es el **BRIEFING de telemetría de
HealthCore**: cinco eventos de inventario (§3), sus campos mínimos y las
restricciones de negocio (§6). Nada más se clasifica como obligatorio.

Las métricas de claims, citas (no-show) y CME/licencias pertenecen al
`CONTEXT` de Hito 2 (utilidades TypeScript de reporting), no a este briefing.
Quedan **fuera** de este catálogo; ver [Exclusiones](#dry-gaps-y-exclusiones).

Entidades del briefing y su estado en la API actual:

| Entidad del briefing | API actual | Estado |
| --- | --- | --- |
| `Product` con `category` ∈ `medication`/`ppe`/`consumable`/`equipment` y fecha de vencimiento | `product_category` (obligatorio al crear) y `expiry_date` en `medical_supply`; se mantiene la `category` operativa | Resuelto. |
| `InboundOrder` (proveedor → clínica) | `POST /inventory/orders/inbound` con `clinic_id` y `vendor_name` libre | Pendiente: `supplier_id` referenciado al directorio. |
| `OutboundOrder` (consumo por departamento) | `POST /inventory/orders/outbound` exige `department` controlado | Resuelto. |
| `clinic` (país + estado/región) | `clinic_id` 1–12; `country` es del producto | Pendiente: tabla de clínicas con país/región. |
| Stock y mínimo por clínica | Salidas validadas contra el stock de su clínica; mínimos en `clinic_stock_minimum` | Resuelto. El catálogo sigue mostrando el total global. |

Clasificación:

- **Métrica del BRIEFING (obligatoria):** exactamente los cinco `event_type`
  de §3, con sus nombres literales.
- **Oportunidad identificada (propuesta propia):** instrumentación adicional
  sobre la aplicación actual. No sustituye a ninguna métrica obligatoria.

El inventario tiene **5 tipos obligatorios** y **20 propuestas propias** (25 en
total).

## Event Envelope

Todo evento usa los ocho campos requeridos:

| Campo | Regla |
| --- | --- |
| `eventId` | UUID único generado por productor confiable; idempotencia en destino. |
| `timestamp` | ISO 8601 UTC, RFC 3339, por ejemplo `2026-11-15T12:00:00.000Z`. |
| `sessionId` | ID aleatorio de sesión pseudónimo; `null` para procesos sin sesión (p. ej. el job de vencimientos). Nunca cookie/token. |
| `userId` | Seudónimo estable con HMAC y rotación de clave documentada; `null` si no existe contexto autenticado. Nunca username, email ni UUID crudo de cuenta. Es el rastro al **staff**, nunca a un paciente (§6). |
| `event_type` | Nombre estable snake_case. Los obligatorios usan el nombre literal del briefing. |
| `schemaVersion` | SemVer del contrato; `2.0.0`. |
| `requestId` | ID de correlación creado/saneado por proxy confiable. En jobs, ID de ejecución; `null` solo si no existe petición ni job padre. |
| `properties` | Objeto cerrado a su allowlist por tipo de evento; `additionalProperties: false`. |

`sessionId`, `userId` y `requestId` están siempre presentes, aunque su valor
pueda ser `null`. El navegador no puede declarar `userId`: el servidor lo
deriva de la sesión autenticada. El backend limita tamaño, valida versión y
timestamp, descarta claves desconocidas y no acepta `requestId` arbitrario.
Cada propiedad en `event-schemas.json` define tipo, obligatoriedad, propósito y
sensibilidad; la `allowlist` es exhaustiva.

### Campos mínimos de inventario

Los cinco eventos obligatorios llevan en `properties`, como mínimo:

| Campo | Tipo | Regla |
| --- | --- | --- |
| `clinic_id` | entero 1–12 | Clínica donde ocurre el hecho. |
| `country` | `US` \| `UK` | País de la **clínica**, para agregar por país. |
| `product_id` | entero | ID interno del insumo; nunca SKU, nombre ni texto libre. |
| `product_category` | `medication` \| `ppe` \| `consumable` \| `equipment` | Categoría del briefing, leída de `Product`. |
| `quantity` | entero | Significado propio por evento (recibido, consumido, stock resultante, intentado, por vencer). |
| `department` | `primary_care` \| `general_consultation` \| `specialty_care` \| `chronic_care` | Solo cuando aplique: obligatorio en `outbound_order_created`, opcional (`null`) en `stock_threshold_triggered`, ausente en el resto. Área clínica, nunca una persona. Catálogo pendiente de confirmar con Operaciones Clínicas. |

## Flujo y puntos de instrumentación obligatorios

Flujo: sesión autenticada → catálogo `GET /inventory/products` → selección de
insumo → validación → `POST /inventory/orders/inbound` o `POST
/inventory/orders/outbound` → commit transaccional (actualiza stock por
clínica) → evaluación del mínimo → refresco del catálogo. El panel de
vencimientos del catálogo consulta los insumos que vencen en 30 días.

En la Fase 2 todos los eventos se emiten desde el backoffice con `track()`,
después de la respuesta autoritativa de la API; la API decide los hechos
(commit, cruce de mínimo, rechazo, vencimiento) y el navegador solo los
reporta. El objetivo de producción es emitir los de negocio desde un outbox en
backend.

1. **`inbound_order_created`**: tras el commit 201 de la entrada.
   Incluye `supplier_id` para segmentar por proveedor.
2. **`outbound_order_created`**: tras el commit 201 de la salida, con
   `department` obligatorio.
3. **`stock_threshold_triggered`**: la API detecta, en la misma transacción de
   la salida, que el stock de `clinic_id + product_id` queda **por debajo del
   mínimo configurado para esa clínica** (`quantity < min_stock_threshold`) y lo
   devuelve en `stock_threshold`; el backoffice emite el evento con esa
   respuesta. Solo en el flanco de bajada (antes `>=` mínimo, ahora `<`); se
   rearma cuando una entrada devuelve el stock a `>=` mínimo. El badge UI `<=10`
   sobre stock global **no** es este umbral y no emite este evento.
4. **`direct_stock_edit_rejected`**: al rechazar la API explícitamente (403)
   un intento de fijar stock fuera de una orden en la ruta de control
   `/inventory/clinics/{clinic_id}/products/{product_id}/stock`; el backoffice
   lo emite desde el panel de ajuste directo.
   Se emiten la plantilla de ruta, el método, `clinic_id`, `product_id`,
   categoría y cantidad intentada (`null` si no es válida); nunca el path
   literal ni el cuerpo. Un 404 genérico no prueba intención y no cuenta.
5. **`supply_expiry_flagged`**: `GET /inventory/expiring` devuelve los
   insumos con `Product.expiry_date` y stock `> 0` por clínica cuando
   `days_until_expiry <= 30` (`expiry_window_days = 30`). Fase 2: el backoffice
   lo emite al cargar el panel de vencimientos, una vez por
   `clinic_id + product_id + expiry_date` y navegador. Objetivo: un job diario
   (UTC) con deduplicación global por esa clave.

## Catálogo de eventos

La justificación de cada evento sigue la frase
«Capturamos `[event_type]` porque necesitamos saber `[hipótesis]`, lo que nos
permite tomar la decisión `[decisión concreta]`».

### Métricas del BRIEFING (obligatorias)

| `event_type` | Se dispara cuando… | Hipótesis | Decisión | Campos (`properties`) | Entrega |
| --- | --- | --- | --- | --- | --- |
| `inbound_order_created` | Una clínica registra la recepción de insumos de un proveedor. | Necesitamos saber cuánto y qué insumo se compra, por clínica y proveedor. | Consolidar compras entre clínicas y negociar mejores condiciones con proveedores. | `clinic_id`, `country`, `product_id`, `product_category`, `quantity`, `supplier_id` | Stream tras commit; agregado diario. |
| `outbound_order_created` | Una clínica registra el consumo de un insumo en la atención de un departamento. | Necesitamos saber qué insumos se consumen más, y a qué ritmo, por clínica y departamento. | Ajustar la reposición automática de insumos críticos por clínica. | `clinic_id`, `country`, `product_id`, `product_category`, `quantity`, `department` | Stream tras commit; agregado diario. |
| `stock_threshold_triggered` | El stock de un insumo cae por debajo del mínimo configurado para esa clínica. | Necesitamos saber con qué frecuencia una clínica se queda corta de un insumo crítico (EPP, medicamento). | Priorizar el reabastecimiento urgente y escalar a Operaciones Clínicas. | `clinic_id`, `country`, `product_id`, `product_category`, `quantity`, `min_stock_threshold`, `department` | Stream por cruce; deduplicado hasta recuperar el mínimo. |
| `direct_stock_edit_rejected` | Un usuario intenta modificar el stock fuera de una orden y el sistema lo rechaza. | Necesitamos saber si el personal intenta saltarse el control de trazabilidad de insumos. | Reforzar formación o permisos en la clínica donde ocurre con más frecuencia. | `clinic_id`, `country`, `product_id`, `product_category`, `quantity`, `attempted_method`, `route_template`, `response_status` | Stream por intento; alerta en segundos. |
| `supply_expiry_flagged` | Un insumo con stock en una clínica entra en los 30 días previos a su vencimiento. | Necesitamos saber qué insumos están por vencer antes de que se conviertan en pérdida o riesgo de cumplimiento. | Priorizar el uso o la baja controlada de ese lote antes del vencimiento. | `clinic_id`, `country`, `product_id`, `product_category`, `quantity`, `expiry_date`, `days_until_expiry`, `expiry_window_days` | Batch diario; un evento por insumo/clínica/fecha. |

Todas se agregan por clínica y por país (US/UK) para el futuro dashboard
operativo de red y las alertas de cumplimiento.

### Oportunidades identificadas (propuestas propias)

| `event_type` | Hipótesis y decisión | Entrega |
| --- | --- | --- |
| `inventory_order_validation_failed` | Necesitamos saber qué validaciones 422 fallan repetidamente para corregir formularios o datos maestros. | Stream con agrupación de 5 s; agregado diario. |
| `inventory_outbound_insufficient_stock_rejected` | Necesitamos detectar salidas que superan el stock disponible por clínica/insumo para ajustar mínimos y reposición. | Stream por rechazo; agregado diario. |
| `auth_login_succeeded` | Necesitamos adopción de acceso válido por rol para planificar soporte y capacidad. | Batch diario; eliminar user-level al agregar. |
| `auth_login_failed` | Necesitamos tendencia de fallos genéricos para detectar errores de UX o abuso y decidir controles de acceso. | Stream por umbral de seguridad; agregación diaria. |
| `auth_session_rejected` | Necesitamos distinguir expiración/token revocado para reducir interrupciones y diagnosticar configuración de sesión. | Batch diario; stream si aumenta la tasa. |
| `auth_password_recovery_requested` | Necesitamos volumen de recuperación para dimensionar soporte, sin descubrir si existe una cuenta. | Batch diario; resultado siempre neutral. |
| `user_admin_action_completed` | Necesitamos recuento por tipo/resultado de gestión de usuarios para auditar el flujo administrativo. | Batch diario. |
| `profile_access_denied` | Necesitamos detectar fallos de autorización para corregir permisos o investigar accesos no permitidos. | Stream ante pico; resumen diario. |
| `supplier_created` | Necesitamos mezcla de altas por país/categoría para orientar cobertura de compras. | Batch semanal. |
| `supplier_rate_updated` | Necesitamos frecuencia y dirección de cambios de tarifa para planificar revisiones sin filtrar montos exactos. | Batch semanal. |
| `supplier_status_changed` | Necesitamos frecuencia de activación/suspensión por país para conocer cambios de disponibilidad. | Batch diario. |
| `inventory_product_created` | Necesitamos altas por `product_category` y la proporción con `expiry_date` para vigilar calidad del catálogo. | Batch diario. |
| `api_latency_aggregated` | Necesitamos p50/p95 por ruta normalizada para escoger optimizaciones con evidencia. | Batch cada minuto; alerta por latencia sostenida. |
| `api_error_aggregated` | Necesitamos tasas por clase de status/error y ruta para priorizar fiabilidad y correcciones. | Batch por minuto; stream para 5xx sostenidos. |
| `frontend_route_viewed` | Necesitamos uso por sección para priorizar navegación y soporte. | Batch por minuto; deduplicar sesión/ruta. |
| `frontend_unhandled_error` | Necesitamos errores cliente por release/ruta para priorizar regresiones sin recoger stack con datos. | Stream para nueva regresión; resumen por release. |
| `form_flow_started` | Necesitamos inicio de flujos de alta/orden para calcular conversión y priorizar simplificación. | Batch diario. |
| `form_flow_abandoned` | Necesitamos etapa y demora del abandono para localizar fricción de formularios. | Batch diario desde señal `pagehide` best-effort. |
| `supplier_filter_applied` | Necesitamos patrones de filtro y resultados para decidir organización del directorio. | Batch diario; solo valores allowlisted. |
| `inventory_orders_viewed` | Necesitamos frecuencia de consulta de historial para dimensionar capacidad y mejorar revisión operativa. | Batch diario. |

## Entrega, frecuencia y debounce

- **Stream** si la decisión necesita reacción inmediata: entradas/salidas
  confirmadas (alimentan reposición), cruce de mínimo, rechazo de edición
  directa, fallos de acceso anómalos y 5xx. “Stream” significa objetivo de
  segundos con outbox durable y reintento, sin bloquear la transacción de
  negocio.
- **Batch** para `supply_expiry_flagged` (job diario), volúmenes
  administrativos y navegación.
- **Mínimo por clínica:** el cruce se calcula en backend con el stock por
  `clinic_id + product_id` dentro de la transacción de la orden. Estado de
  rearme durable por par clínica/insumo para evitar duplicados entre
  instancias. Si el mínimo se reconfigura por encima del stock actual, no se
  emite hasta la siguiente salida que confirme el cruce.
- **Vencimiento:** dedupe por `clinic_id + product_id + expiry_date` (hoy por
  navegador; la Fase 3 debe deduplicar en almacenamiento); si el stock llega a
  0 antes del vencimiento no se emite. Ventana fija de 30 días.
- **Latencia:** ventanas de 60 s por `route_template`, método y clase de
  status; alerta cuando p95 > 200 ms durante 5 ventanas o 5xx > 2 % durante
  5 min. Ajustar tras línea base.
- **Vistas:** deduplicar `sessionId + route_template` en 30 s; sin query
  string, fragmento ni IDs.
- **Validación:** agrupar mismo formulario + conjunto de códigos en 5 s.
- **Idempotencia:** destino deduplica por `eventId`. La telemetría nunca es
  fuente de verdad para autorizar salidas ni contabilizar stock.

## Privacidad, retención y control (HIPAA / UK GDPR)

- **Sin PHI:** ningún campo contiene nombre de paciente, identificador de
  paciente o de historia clínica, diagnóstico, motivo clínico ni dato real o
  simulado interpretable como información de salud. Aplica también a datos de
  prueba y seed (§6).
- `department` describe un área clínica de un catálogo cerrado; nunca se
  admite texto libre ni nada que identifique a una persona atendida.
- El rastro de cada movimiento es al **staff** mediante `userId` seudónimo;
  nunca a un paciente.
- Excluir además emails, teléfonos, username, contraseñas, hashes, tokens,
  cookies, IP, user-agent sin reducir, `vendor_name` libre, notas y cuerpos de
  request/response.
- `clinic_id`, `product_id`, `supplier_id` y cantidades son datos operativos:
  acceso RBAC, finalidad limitada y retención definida por Compliance.
- Seudónimos de sesión/usuario no equivalen a anonimización: HMAC con clave
  rotada y acceso limitado. Retención propuesta: eventos crudos 30 días;
  agregados por clínica/país hasta 13 meses, sujeto a validación de
  Compliance y requisitos US/UK.
- Limitar payload por evento, validar versión, ACL, TLS, cifrado en reposo,
  auditoría de accesos y redacción previa a la cola.

## Dry-gaps y exclusiones

1. **Resueltos en la Fase 2:** stock y mínimo por clínica
   (`clinic_stock_minimum`), `Product.expiry_date`, `product_category` del
   briefing (obligatorio al crear; migración de los existentes:
   `medications→medication`, `ppe→ppe`, `consumables`/`wound_care→consumable`;
   `diagnostics` se asigna por producto), `department` en salidas y ruta de
   rechazo explícito de edición directa.
2. **Departamento:** catálogo de valores pendiente de confirmar con
   Operaciones Clínicas.
3. **Proveedor en entradas:** `vendor_name` es texto libre y no se envía.
   `supplier_id` va `null` hasta que la entrada referencie el directorio.
4. **País de la clínica:** hoy `country` sale del producto. Debe derivarse de
   la clínica (tabla de clínicas con país y estado/región).
5. **Vencimientos:** el productor actual es el backoffice (por navegador); un
   job diario debe sustituirlo para garantizar una sola emisión por clave.
6. **Fuera de alcance:** métricas de claims (tasa de denegación 8 %), citas
   (no-show 20 %, coste) y personal (CME, licencias 90/30 días) son del
   `CONTEXT` de Hito 2, no de este briefing. No se catalogan aquí.
7. **Cuerpos fallidos, credenciales, latencia por usuario, IP, replay, heatmaps
   y resultado “cuenta encontrada” de recuperación:** excluidos por riesgo de
   privacidad o falta de decisión concreta.

## Datos semilla para validar los eventos

Según §5 del briefing, el seed de validación incluye: 8–10 insumos que cubren
las 4 categorías (al menos uno con `expiry_date` a ≤ 30 días); 3 clínicas, al
menos una en US y una en UK; 15–20 entradas y 15–20 salidas, estas últimas
solo con `department`; mínimos configurados para que al menos 2 salidas
disparen `stock_threshold_triggered` y el job emita 1
`supply_expiry_flagged`. Sin ningún dato de paciente, ni siquiera ficticio.

## Lista de endpoints y cobertura

| Operación | Cobertura de diseño |
| --- | --- |
| `POST /login` | `auth_login_succeeded`, `auth_login_failed`, métricas agregadas |
| `GET /me`, `GET /auth/me` | `auth_session_rejected`, métricas agregadas |
| `POST /auth/forgot-password` | `auth_password_recovery_requested` (neutro) |
| `POST /auth/reset-password`, `POST /auth/change-password` | Métricas agregadas de status/latencia |
| Rutas `/users` | `user_admin_action_completed` |
| Rutas `/profiles` | `profile_access_denied`, métricas agregadas |
| `GET /suppliers` | `supplier_filter_applied`, `api_latency_aggregated` |
| `POST /suppliers` | `supplier_created` |
| `PUT /suppliers/{supplier_id}`, `PATCH .../rate`, `PATCH .../status` | `supplier_rate_updated`, `supplier_status_changed` |
| `GET /inventory/products`, `GET /inventory/products/{supply_id}` | Métricas agregadas (el badge `<=10` no emite eventos) |
| `POST /inventory/products` | `inventory_product_created` |
| `POST /inventory/orders/inbound` | **`inbound_order_created`**, `inventory_order_validation_failed` |
| `POST /inventory/orders/outbound` | **`outbound_order_created`**, **`stock_threshold_triggered`** (si cruza el mínimo), `inventory_outbound_insufficient_stock_rejected`, `inventory_order_validation_failed` |
| `GET /inventory/orders` | `inventory_orders_viewed` |
| `PUT`/`PATCH /inventory/clinics/{clinic_id}/products/{product_id}/stock` (siempre 403) | **`direct_stock_edit_rejected`** |
| `GET /inventory/expiring` | **`supply_expiry_flagged`** |
| Otras 4xx/5xx y errores cliente | `api_error_aggregated`, `frontend_unhandled_error` |

Las rutas se registran como plantilla, nunca con identificadores ni query
string.

## Migración desde 1.0.0

| 1.0.0 | 2.0.0 |
| --- | --- |
| `inventory_inbound_order_created` | `inbound_order_created` (+ `product_category`, `supplier_id`; − `order_id`) |
| `inventory_outbound_order_created` | `outbound_order_created` (+ `product_category`, `department`; − `order_id`, `consumption_type`) |
| `inventory_stock_threshold_triggered` (badge UI `<=10`, global) | `stock_threshold_triggered` (mínimo por clínica, backend) |
| `inventory_direct_stock_edit_rejected` | `direct_stock_edit_rejected` (+ campos mínimos) |
| — | `supply_expiry_flagged` (nuevo) |
| `supply_id` | `product_id` |
| `inventory_order_validation_failed`, `inventory_outbound_insufficient_stock_rejected` | Reclasificados como propuesta propia |
| `claim_*`, `appointment_*`, `clinician_*` | Eliminados (fuera del briefing) |

La captura del backoffice, el contrato generado y la validación de la API
(`schemaVersion: "2.0.0"`) ya están migrados. El estado de cada evento
obligatorio está en [event-capture.md](./event-capture.md#obligatorios-del-briefing).

## Cambio aditivo 2.1.0

| Cambio | Detalle |
| --- | --- |
| `inbound_order_created.total_cost` | `number`, `minimum 0`, opcional. Es `quantity × unit_cost` en la moneda de la clínica, calculado por la API. Alimenta el KPI de costo de insumos del pipeline de negocio ([`data/pipelines/PIPELINE_DESIGN.md`](../../data/pipelines/PIPELINE_DESIGN.md)). |
| `schemaVersion` | El backoffice envía `2.1.0`. La API acepta `2.0.0` y `2.1.0`, para no rechazar las pestañas abiertas antes del cambio. |
| Eventos `2.0.0` ya guardados | No tienen `total_cost`. El pipeline los cuenta como `inbound_without_cost` y nunca los toma como costo 0. |

