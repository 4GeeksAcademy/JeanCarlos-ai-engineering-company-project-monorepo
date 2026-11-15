# Auditoría de serialización de la API

## Alcance y criterios

Se revisaron las rutas de negocio registradas en `services.api.main`. La tabla
clasifica el estado **anterior → posterior**: ✅ esquema público y modelo
materializado; ⚠️ filtrado por FastAPI pero retorno interno crudo o esquema
compartido con escritura; ❌ sin esquema explícito de respuesta. `204` no
contiene cuerpo. Las rutas generadas por FastAPI para OpenAPI y documentación
no representan registros de negocio y no se incluyen.

Todos los modelos de salida son listas explícitas de campos. Los handlers
devuelven instancias Pydantic o listas de estas, nunca registros TinyDB ni
objetos ORM. Las respuestas de error de FastAPI y los handlers globales se
tratan por separado de las respuestas de éxito.

## Contratos públicos

| Código | Campos de respuesta |
| --- | --- |
| Token | `access_token`, `token_type` |
| Usuario | `id`, `username`, `role` |
| Usuario actual | Usuario + `email` |
| Perfil | `id`, `user_id` |
| Mensaje | `message` |
| Proveedor | `id`, `name`, `country`, `categories`, `monthly_rate`, `currency`, `status`, `compliance_agreement`, `contract_renewal_date`, `contact_email`, `notes`, `updated_at` |
| Producto | `id`, `name`, `sku`, `category`, `unit`, `country`, `current_stock` |
| Insumo | `id`, `name`, `sku`, `category`, `unit`, `country` |
| Entrada | `id`, `supply_id`, `quantity`, `clinic_id`, `created_at`, `user_uuid`, `supply` (Insumo), `vendor_name` |
| Salida | `id`, `supply_id`, `quantity`, `clinic_id`, `created_at`, `user_uuid`, `supply` (Insumo), `consumption_type` |

| Método y ruta | Antes → después | Cuerpo de éxito |
| --- | --- | --- |
| POST `/login` | ✅ → ✅ | Token |
| GET `/me` | ⚠️ → ✅ | Usuario |
| GET `/auth/me` | Nueva → ✅ | Usuario actual |
| POST `/auth/forgot-password` | ❌ → ✅ | Mensaje neutro |
| POST `/auth/reset-password` | ❌ → ✅ | Mensaje |
| POST `/auth/change-password` | ❌ → ✅ | Mensaje |
| GET `/users` | ⚠️ → ✅ | Lista de Usuario |
| POST `/users` | ⚠️ → ✅ | Usuario |
| GET `/users/{user_id}` | ⚠️ → ✅ | Usuario |
| PATCH `/users/{user_id}` | ⚠️ → ✅ | Usuario |
| DELETE `/users/{user_id}` | ✅ → ✅ | Sin cuerpo, 204 |
| GET `/profiles` | ⚠️ → ✅ | Lista de Perfil |
| POST `/profiles` | ⚠️ → ✅ | Perfil |
| GET `/profiles/{profile_id}` | ⚠️ → ✅ | Perfil |
| PATCH `/profiles/{profile_id}` | ⚠️ → ✅ | Perfil |
| DELETE `/profiles/{profile_id}` | ✅ → ✅ | Sin cuerpo, 204 |
| GET `/suppliers` | ⚠️ → ✅ | Lista de Proveedor |
| POST `/suppliers` | ⚠️ → ✅ | Proveedor |
| GET `/suppliers/{supplier_id}` | ⚠️ → ✅ | Proveedor |
| PUT `/suppliers/{supplier_id}` | ⚠️ → ✅ | Proveedor |
| PATCH `/suppliers/{supplier_id}/rate` | ⚠️ → ✅ | Proveedor |
| PATCH `/suppliers/{supplier_id}/status` | ⚠️ → ✅ | Proveedor |
| GET `/inventory/products` | ⚠️ → ✅ | Lista de Producto |
| POST `/inventory/products` | ⚠️ → ✅ | Producto |
| GET `/inventory/products/{supply_id}` | ⚠️ → ✅ | Producto |
| POST `/inventory/orders/inbound` | ⚠️ → ✅ | Entrada |
| POST `/inventory/orders/outbound` | ⚠️ → ✅ | Salida |
| GET `/inventory/orders` | ⚠️ → ✅ | `inbound`: lista de Entrada; `outbound`: lista de Salida |

En inventario, las salidas ya se materializaban antes; se clasifican como ⚠️
porque el resumen de insumo heredaba del modelo de entrada. Los modelos
`ListItem` son independientes de los modelos de escritura. Los de proveedor y
producto conservan deliberadamente todos los campos anteriores para no romper
los clientes existentes; no se afirma una reducción cuantificada de payload
para esas listas.

## Seguridad y compatibilidad

- `hashed_password`, `token_version`, `email_key` y `username_key` nunca forman
  parte de los esquemas públicos. El email de cuenta se devuelve únicamente en
  `GET /auth/me`, autenticado, para el usuario de la sesión. `/me` se conserva
  como alias compatible para verificar la sesión, pero ya no devuelve email;
  las respuestas de `/users` tampoco lo devuelven. Esto reduce esos payloads y
  supone un cambio de contrato para consumidores que leían ese campo.
- `contact_email` es un contacto de proveedor, no un email de cuenta; se
  conserva por decisión explícita. Los campos de email siguen disponibles como
  **entradas** para creación/actualización y recuperación de contraseña.
- La recuperación devuelve el mismo mensaje para email conocido, desconocido o
  inválido. Los errores de validación de auth no autenticada son genéricos y
  no reflejan campos ni valores enviados. Las respuestas de error de negocio
  usan `detail`; los errores de validación usan una lista de `loc`, `msg` y
  `type`, sin incluir `input` ni contexto.
- El listado de órdenes mantiene `user_uuid` y los datos del insumo que utiliza
  el backoffice. Reducirlos o paginar requiere un cambio de contrato separado.

Los modelos `Create`/`Update`, `Response` y `ListItem` son explícitos y no
comparten campos sensibles mediante herencia de modelos de entrada.
