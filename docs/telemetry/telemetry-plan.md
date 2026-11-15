# Plan de telemetría — HealthCore

**Estado:** diseño previo a instrumentación. No añade ni afirma eventos ya
instrumentados.  
**Ámbito:** RFI de inventario, contexto HealthCore y superficies existentes del
backoffice/API.  
**Versión del contrato:** `1.0.0`.

## Alcance y fuentes de verdad

El contexto HealthCore define las entidades `Claim`, `Appointment`,
`Clinician` y `Location`, además de los cálculos operativos descritos abajo.
Estas entidades y funciones son una asignación de utilidades TypeScript; no
están expuestas por la API FastAPI actual. Sus eventos obligatorios se
producirán únicamente cuando el futuro proceso/dashboard calcule los informes.
No se deben simular como eventos de órdenes ni presentar como datos actuales.

La API actual expone autenticación, usuarios, perfiles, proveedores e
inventario. El inventario relacional tiene productos, entradas y salidas; el
stock es global y se calcula como entregas menos consumos. Solo las órdenes
modifican stock; salidas mayores que el disponible se rechazan con HTTP 400.
El catálogo del backoffice muestra un badge informativo de stock bajo con
umbral de 10 unidades. No existe hoy un endpoint de edición directa de stock
ni una alerta de stock mínimo en backend.

Clasificación obligatoria:

- **Métrica del BRIEFING (obligatoria):** incluye los umbrales de negocio de
  `CONTEXT-healthcore.es .md` y las métricas de inventario explícitamente
  solicitadas por el RFI del encargo.
- **Oportunidad identificada (propuesta propia):** instrumentación adicional
  para responder preguntas operativas o técnicas sobre la aplicación actual.

El inventario tiene 14 tipos obligatorios diseñados y 18 propuestas propias
(32 tipos en total). Los identificadores y umbrales del contexto se mantienen
exactos: claim `8%`; no-show `20%`; Physician `40 horas/año`; Nurse
Practitioner `30 horas/año`; rezago CME `15` puntos porcentuales; licencia
`90` días (primera alerta) y `30` días (urgente). El contexto no asigna horas
CME a `nurse` ni `medical_assistant`: no inferirlas.

## Event Envelope

Todo evento usa los ocho campos requeridos:

| Campo | Regla |
| --- | --- |
| `eventId` | UUID único generado por productor confiable; idempotencia en destino. |
| `timestamp` | ISO 8601 UTC, RFC 3339, por ejemplo `2026-11-15T12:00:00.000Z`. |
| `sessionId` | ID aleatorio de sesión pseudónimo; `null` para procesos sin sesión. Nunca cookie/token. |
| `userId` | Seudónimo estable con HMAC y rotación de clave documentada; `null` si no existe contexto autenticado. Nunca username, email ni UUID crudo de cuenta. |
| `event_type` | Taxonomía estable `entidad_acción`, snake_case. |
| `schemaVersion` | SemVer del contrato; inicialmente `1.0.0`. |
| `requestId` | ID de correlación creado/saneado por proxy confiable. En jobs, ID de ejecución; `null` solo si no existe petición ni job padre. |
| `properties` | Objeto cerrado a su allowlist por tipo de evento; `additionalProperties: false`. |

`sessionId`, `userId` y `requestId` son siempre presentes, aunque su valor
pueda ser `null` en eventos agregados. El navegador no puede declarar
`userId`: el servidor lo deriva de la sesión autenticada. El backend debe
limitar tamaño, validar versiones y timestamp, descartar claves desconocidas y
no tomar `requestId` arbitrario sin validación. Cada registro de `properties`
en `event-schemas.json` define tipo, obligatoriedad y propósito; la lista
`allowlist` es exhaustiva.

## Flujo y puntos de instrumentación obligatorios

Flujo: sesión autenticada → lectura de catálogo `GET /inventory/products` →
selección de producto → validación de formulario → `POST
/inventory/orders/inbound` o `POST /inventory/orders/outbound` → commit
transaccional → refresco del catálogo → badge de mínimo.

1. **`inventory_inbound_order_created`**: después del commit 201; cuenta
   entradas y cantidades por día, clínica y producto.
2. **`inventory_outbound_order_created`**: después del commit 201; misma
   segmentación para salidas.
3. **`inventory_order_validation_failed`**: rechazos 422 con campo/código
   allowlisted, sin incluir valores enviados ni mensaje de Pydantic completo.
4. **`inventory_outbound_insufficient_stock_rejected`**: respuesta 400;
   identifica intentos de retirar más de lo disponible sin cambiar stock.
5. **`inventory_direct_stock_edit_rejected`**: el producto no expone una ruta
   de edición directa. Detectar en gateway un intento que coincida con rutas
   prohibidas `/inventory/products/{supply_id}/stock` y rechazarlo; emitir solo
   plantilla de ruta/método/status, nunca el path con ID. Esta instrumentación
   requiere una regla explícita en gateway; un 404 genérico no basta para
   distinguir el intento.
6. **`inventory_stock_threshold_triggered`**: cruce del umbral UI actual
   (`current_stock <= 10` unidades, global por producto). Emitir solo en el
   flanco de entrada desde >10 y deduplicar hasta que el stock vuelva a superar
   10. Es una señal de la UI, no una alerta clínica/backend existente.
7. **`claim_denial_rate_calculated`** y
   **`claim_high_denial_rate_flagged`**: tasa por payer y por sede, umbral
   obligatorio 8%.
8. **`appointment_no_show_cost_calculated`**,
   **`appointment_no_show_rate_calculated`** y
   **`appointment_high_no_show_rate_flagged`**: pérdida semanal por sede y
   umbral 20%.
9. **`clinician_cme_report_generated`**,
   **`clinician_cme_at_risk_detected`** y
   **`clinician_license_alert_triggered`**: horas/cumplimiento agregado,
   rezago 15 puntos porcentuales, y ventanas de 90/30 días.

### Contratos actuales que deben cubrirse

| Área | Método y ruta | Oportunidades principales |
| --- | --- | --- |
| Autenticación | `POST /login` | Éxito y fallo genérico de credenciales; nunca username, email ni contraseña. |
| Autenticación | `GET /me`, `GET /auth/me` | Sesión expirada/rechazada y latencia; no emitir email o claims del token. |
| Contraseñas | `POST /auth/forgot-password`, `/auth/reset-password`, `/auth/change-password` | Solicitud/fallo operacional y latencia; recuperación debe seguir siendo neutra y nunca registrar token/email. |
| Usuarios | `GET /users`, `POST /users`, `GET /users/{user_id}`, `PATCH /users/{user_id}`, `DELETE /users/{user_id}` | Acción administrativa y resultado; no IDs de usuario directos ni atributos de cuenta. |
| Perfiles | `GET /profiles`, `POST /profiles`, `GET /profiles/{profile_id}`, `PATCH /profiles/{profile_id}`, `DELETE /profiles/{profile_id}` | Acceso denegado y acciones agregadas; evitar perfil/usuario identificable. |
| Proveedores | `GET /suppliers`, `POST /suppliers`, `GET /suppliers/{supplier_id}`, `PUT /suppliers/{supplier_id}`, `PATCH /suppliers/{supplier_id}/rate`, `PATCH /suppliers/{supplier_id}/status` | Consultas/filtros y mutaciones agregadas; no contacto email ni notas. |
| Inventario | `GET /inventory/products`, `POST /inventory/products`, `GET /inventory/products/{supply_id}` | Lecturas, producto creado, errores de validación y medición técnica. |
| Órdenes | `POST /inventory/orders/inbound`, `POST /inventory/orders/outbound`, `GET /inventory/orders` | Entrada/salida, insuficiencia de stock, validación y consultas de historial. |

Son 28 operaciones HTTP actuales (incluidas las dos rutas `/me`). Errores
transversales 401/403/404/409/422/500/503 también quedan cubiertos por eventos
propuestos de autenticación, autorización, validación y latencia. No enviar
cuerpos de respuesta o petición a la telemetría.

## Catálogo de eventos

La frase de justificación de cada evento es
«Capturamos `[event_type]` porque necesitamos saber `[hipótesis]`, lo que nos
permite tomar la decisión `[decisión concreta]`».

### Métricas del BRIEFING (obligatorias)

Todos los eventos de informes de claims, citas y personal son agregados. El
contexto especifica datos de pacientes (`patientId`), importes, razones de
denegación libres/operativas, identificadores/nombres de clínicos y fechas de
licencia; **no** se deben enviar esos identificadores o datos de nivel
individual a un destino analítico general.

| `event_type` | Hipótesis y decisión | Entrega |
| --- | --- | --- |
| `inventory_inbound_order_created` | Necesitamos conocer entradas diarias por producto/sede para decidir reposición y planificación de recepción. | Stream tras commit; informe diario. |
| `inventory_outbound_order_created` | Necesitamos conocer salidas diarias para decidir planificación de stock y consumo por sede. | Stream tras commit; informe diario. |
| `inventory_order_validation_failed` | Necesitamos saber qué validaciones fallan repetidamente para corregir formularios o datos maestros. | Stream para identificar errores sistémicos; agregación diaria. |
| `inventory_direct_stock_edit_rejected` | Necesitamos saber si se intenta eludir el rastro de órdenes para reforzar controles y UX autorizada. | Stream por señal de control; alerta de seguridad. |
| `inventory_stock_threshold_triggered` | Necesitamos contar productos que cruzan el límite bajo del badge para planificar reposición. | Stream por cruce único; consolidación diaria. |
| `inventory_outbound_insufficient_stock_rejected` | Necesitamos detectar solicitudes superiores al stock disponible y sus patrones por producto para ajustar reposición/solicitudes. | Stream para error operativo; agregación diaria. |
| `claim_denial_rate_calculated` | Necesitamos tasas por `payerId` y `locationId` para orientar revisión de codificación/contratos con el benchmark exacto de 8%. | Batch semanal del reporte operativo. |
| `claim_high_denial_rate_flagged` | Necesitamos saber qué payer supera 8% para priorizar revisión de Revenue Cycle. | Batch semanal al calcular el reporte. |
| `appointment_no_show_cost_calculated` | Necesitamos estimar USD perdidos por no-show en los 7 días calendario inclusivos por sede para priorizar acciones clínicas. | Batch semanal; reporte del lunes. |
| `appointment_no_show_rate_calculated` | Necesitamos la tasa por `locationId` frente al umbral interno exacto de 20% para decidir intervenciones locales. | Batch semanal. |
| `appointment_high_no_show_rate_flagged` | Necesitamos identificar sedes que superan 20% para escalar revisión operativa. | Batch semanal al calcular el reporte. |
| `clinician_cme_report_generated` | Necesitamos distribución de cumplimiento y horas para decidir seguimiento del equipo People; Physician=40 y Nurse Practitioner=30 horas/año. | Batch semanal; métricas agregadas. |
| `clinician_cme_at_risk_detected` | Necesitamos contar riesgo cuando el avance CME está >15 puntos porcentuales por detrás del ciclo para priorizar seguimiento preventivo. | Batch semanal; sin alertas analíticas por persona. |
| `clinician_license_alert_triggered` | Necesitamos conteo de licencias en ventanas exactas 90 días (primera) y 30 días (urgente) para priorizar revisión de credenciales. | Stream para alerta urgente y primera advertencia; conteo semanal. |

Para CME/licencias, el servicio operativo autorizado puede notificar al dueño
del proceso fuera del lake analítico. Este plan no cambia las políticas de
RR. HH. ni concede acceso a identidades individuales.

### Oportunidades identificadas (propuestas propias)

| `event_type` | Hipótesis y decisión | Entrega |
| --- | --- | --- |
| `auth_login_succeeded` | Necesitamos adopción de acceso válido por rol para planificar soporte y capacidad. | Batch diario; eliminar user-level al agregar. |
| `auth_login_failed` | Necesitamos tendencia de fallos genéricos para detectar errores de UX o abuso y decidir controles de acceso. | Stream por umbral de seguridad; agregación diaria. |
| `auth_session_rejected` | Necesitamos distinguir expiración/token revocado para reducir interrupciones y diagnosticar configuración de sesión. | Batch diario; stream si aumenta la tasa. |
| `auth_password_recovery_requested` | Necesitamos volumen y resultado operacional de recuperación para dimensionar soporte, sin descubrir si existe una cuenta. | Batch diario; resultado siempre neutral. |
| `user_admin_action_completed` | Necesitamos recuento por tipo/resultado de gestión de usuarios para auditar el flujo administrativo. | Batch diario. |
| `profile_access_denied` | Necesitamos detectar fallos de autorización para corregir permisos o investigar accesos no permitidos. | Stream ante pico; resumen diario. |
| `supplier_created` | Necesitamos mezcla de altas por país/categoría para orientar cobertura de compras. | Batch semanal. |
| `supplier_rate_updated` | Necesitamos frecuencia y distribución de actualizaciones por banda para planificar revisión de tarifas sin filtrar montos exactos. | Batch semanal. |
| `supplier_status_changed` | Necesitamos frecuencia de activación/suspensión por país para conocer cambios de disponibilidad. | Batch diario. |
| `inventory_product_created` | Necesitamos altas por categoría/país/unidad para vigilar calidad del catálogo. | Batch diario. |
| `api_latency_aggregated` | Necesitamos p50/p95 y errores por ruta normalizada para escoger optimizaciones con evidencia. | Batch cada minuto; latencia sostenida alta dispara alerta. |
| `api_error_aggregated` | Necesitamos tasas por clase de status/error y ruta para priorizar fiabilidad y correcciones. | Batch por minuto; stream para 5xx sostenidos. |
| `frontend_route_viewed` | Necesitamos uso por sección para priorizar navegación y soporte. | Batch por minuto; deduplicar sesión/ruta. |
| `frontend_unhandled_error` | Necesitamos errores cliente por release/ruta para priorizar regresiones sin recoger stack con datos. | Stream para nueva regresión; resumen por release. |
| `form_flow_started` | Necesitamos inicio de flujos de alta/orden para calcular conversión y priorizar simplificación. | Batch diario. |
| `form_flow_abandoned` | Necesitamos conocer etapa y demora del abandono para localizar fricción de formularios. | Batch diario desde señal `pagehide` best-effort. |
| `supplier_filter_applied` | Necesitamos patrones de filtro y resultados para decidir organización del directorio. | Batch diario; valores solo categorías allowlisted. |
| `inventory_orders_viewed` | Necesitamos frecuencia de consulta de historial para dimensionar capacidad y mejorar revisión operativa. | Batch diario. |

## Entrega, frecuencia y debounce

- **Stream** si la decisión necesita reacción inmediata: rechazo de edición
  directa, salidas rechazadas, fallos de acceso anómalos, errores 5xx y
  alertas urgentes de licencia. “Stream” significa objetivo de segundos, con
  cola duradera/reintento; no bloqueo de la transacción de negocio.
- **Batch** para informes semanales de denegaciones, no-shows y CME; volúmenes
  administrativos y navegación. Los eventos críticos de orden se emiten tras
  commit a outbox duradera y se procesan en stream; el informe diario/semanal
  agrega eventos, no reconsulta ni replica PII.
- **Latencia:** no emitir una fila por request de alta frecuencia. Agregar
  durante ventanas de 60 s por `route_template`, método y clase de status.
  Publicar `requestCount`, histograma/p50/p95 y fallos; stream de alerta solo
  cuando una ruta supere 200 ms p95 durante 5 ventanas consecutivas o error
  5xx >2% por 5 min. Ajustar límites tras línea base medida.
- **Vistas:** deduplicar `sessionId + route_template` en 30 s; nunca incluir
  query string, fragmento, IDs ni enlaces de reset.
- **Validación:** agrupar mismo formulario + conjunto de error codes en 5 s;
  enviar `occurrenceCount`, no texto ni valores.
- **Stock bajo:** evento solo al cruce de >10 a <=10 unidades globales por
  producto; un nuevo evento se permite al recuperar >10 y volver a cruzar.
  Para evitar eventos duplicados entre instancias, calcular cruce con una
  transición de estado durable o deduplicar por `supply_id` pseudónimo +
  versión de stock. Hoy el badge UI no tiene tal transición de backend.
- **Idempotencia:** destino deduplica por `eventId`; outbox y consumidores
  reintentan sin duplicar decisiones de negocio. La telemetría nunca es fuente
  de verdad para autorizar salidas o contabilizar stock.

## Privacidad, retención y control

- Excluir `patientId`, datos clínicos, motivo textual de no-show, texto de
  notas, emails, teléfono, nombre/username, CV/LinkedIn, contraseñas, hashes,
  tokens, cookies, dirección IP, user-agent sin reducir y cuerpo de request.
- `payerId`, `locationId`, `supply_id`, `clinic_id`, cantidades y métricas
  financieras requieren acceso RBAC, finalidad limitada, agregación por
  cohortes y retención definida por Compliance. `claimAmount` y coste de
  no-show solo agregados USD; no claims individuales.
- CME/licencia: dato laboral de riesgo regulatorio. Solo conteos agregados,
  supresión de cohortes <10, acceso restringido a responsables autorizados y
  revisión de Compliance/RR. HH. previa a producción. No telemetry con
  `clinicianId`, `fullName`, `licenceExpiryDate` individual o licencia/state.
- Seudónimos de sesión/usuario no equivalen a anonimización. HMAC con clave
  rotada, acceso limitado y política documentada. Retención propuesta:
  telemetría de eventos crudos 30 días; agregados no identificables hasta
  13 meses, sujetos a validación legal/Compliance y requisitos locales
  US/UK. Menor retención si no es necesaria.
- Limitar payload/evento, schema version, ACL, TLS, cifrado en reposo,
  auditoría de accesos y redacción previa a la cola. Prohibido que el cliente
  envíe campos no allowlisted.

## Dry-gaps y exclusiones

1. **Claims, appointments, clinicians y locations:** están en el contexto de
   la asignación TypeScript, no en los routers FastAPI actuales. Para los
   eventos obligatorios de métricas se requiere un job/dashboard que realmente
   calcule los reportes. Hasta entonces, reportar “no disponible”; no generar
   ceros sintéticos.
2. **Umbral de inventario:** el único umbral verificable es el badge UI `<=10`;
   no inventar cantidades mínimas por clínica/producto ni denominarlo política
   clínica. Confirmar con Operations antes de crear alertas accionables.
3. **Edición directa:** no hay endpoint actualmente. Un path 404 no prueba
   intención de modificar stock; instrumentar en gateway con patrón estricto,
   o no emitir el evento hasta implementar un rechazo explícito.
4. **Cuerpos fallidos y credenciales:** excluidos aunque ayuden a reproducir
   errores: contienen datos sensibles. Solo códigos allowlisted y nombres de
   campos no sus valores.
5. **Latencia por usuario:** excluida del pipeline analítico; métricas
   agregadas por ruta evitan claves de alta cardinalidad y exposición de
   actividad individual.
6. **IP geográfica, replay de sesión, teclas/clickstream detallado y heatmaps:**
   excluidos por riesgo/coste y falta de hipótesis de decisión concreta.
7. **Email de recuperación:** nunca medir “cuenta encontrada/no encontrada”,
   porque revela existencia de cuentas y contradice la respuesta neutra.
8. **Caché o eventos de disponibilidad de stock:** un badge cacheado no prueba
   stock real. No usar eventos analíticos para decidir transacciones.

## Lista de endpoints y cobertura

La evaluación de cobertura se basa en los routers actuales. Errores son
cross-cutting; los eventos no registran mensajes libres ni payloads.

| Operación actual | Cobertura de diseño |
| --- | --- |
| `POST /login` | `auth_login_succeeded`, `auth_login_failed`, `api_latency_aggregated`, `api_error_aggregated` |
| `GET /me`, `GET /auth/me` | `auth_session_rejected`, métricas agregadas; no email |
| `POST /auth/forgot-password` | `auth_password_recovery_requested`, métricas agregadas; neutro |
| `POST /auth/reset-password`, `POST /auth/change-password` | métricas agregadas de status/latencia, sin token/PII |
| `GET /users`, `POST /users`, `GET /users/{user_id}`, `PATCH /users/{user_id}`, `DELETE /users/{user_id}` | `user_admin_action_completed` |
| `GET /profiles`, `POST /profiles`, `GET /profiles/{profile_id}`, `PATCH /profiles/{profile_id}`, `DELETE /profiles/{profile_id}` | `profile_access_denied`, métricas agregadas |
| `GET /suppliers` | `supplier_filter_applied`, `api_latency_aggregated` |
| `POST /suppliers` | `supplier_created`, `user_admin_action_completed` |
| `GET /suppliers/{supplier_id}` | `api_latency_aggregated` |
| `PUT /suppliers/{supplier_id}` | `supplier_rate_updated`/`supplier_status_changed` solo para campos cambiados allowlisted |
| `PATCH /suppliers/{supplier_id}/rate` | `supplier_rate_updated` |
| `PATCH /suppliers/{supplier_id}/status` | `supplier_status_changed` |
| `GET /inventory/products` | `inventory_stock_threshold_triggered` en consumidor de UI al cruce; telemetría de latencia |
| `POST /inventory/products` | `inventory_product_created` |
| `GET /inventory/products/{supply_id}` | Telemetría agregada de latencia |
| `POST /inventory/orders/inbound` | `inventory_inbound_order_created`, `inventory_order_validation_failed` |
| `POST /inventory/orders/outbound` | `inventory_outbound_order_created`, `inventory_outbound_insufficient_stock_rejected`, `inventory_order_validation_failed` |
| `GET /inventory/orders` | `inventory_orders_viewed`, telemetría agregada |
| Ruta prohibida `/inventory/products/{supply_id}/stock` (no existe) | `inventory_direct_stock_edit_rejected`, solo cuando gateway reconoce método/path y rechaza explícitamente |
| Otras 4xx/5xx y errores cliente | `api_error_aggregated`, `frontend_unhandled_error` con taxonomy allowlisted |

Las rutas no deben usarse como etiqueta literal si contienen identificadores:
resolver a plantilla registrada. Los events de navegación y solicitudes no
incluyen query strings.
