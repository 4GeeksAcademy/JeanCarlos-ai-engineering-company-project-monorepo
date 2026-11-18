# Pruebas — HealthCore

Batería de pruebas del ticket **AUTH-088** (API de autenticación) y de las
actividades extra **API-042** (endpoints del backoffice) y **FE-019** (utilidades
del frontend).

| Suite | Herramienta | Dónde | Tests | Cobertura |
| --- | --- | --- | --- | --- |
| Autenticación (AUTH-088) | pytest | `services/api/tests/` | 142 | **98 %** en los módulos de auth |
| Backoffice (API-042) | pytest | `services/api/tests/` | 33 | **99 %** en suppliers + inventario |
| Guardas transversales | pytest | `services/api/tests/` | 19 | — |
| Frontend (FE-019 + auth TS) | Jest | `uis/backoffice/__tests__/` | 51 | **85 %** de líneas |

## Cómo ejecutarlas

### Backend (FastAPI) — pytest

Desde la **raíz del repositorio**:

```bash
uv run pytest           # 194 tests
uv run pytest --cov     # con informe de cobertura por archivo de services/api
```

`uv run` crea el entorno e instala lo necesario la primera vez (equivale a
`uv sync`): las dependencias de la API desde `services/api/requirements.txt`
(una sola fuente) y el grupo `dev` (pytest, pytest-cov, httpx). El
`pyproject.toml` de la raíz configura pytest (`testpaths = ["services/api/tests"]`,
`pythonpath = ["."]` para que los tests importen `services.api...`) y la
cobertura (`source = ["services/api"]`, sin tests ni scripts de seed).

También funciona desde el propio proyecto FastAPI, que tiene su
`pyproject.toml` equivalente:

```bash
cd services/api && uv run pytest
```

Las pruebas no leen `.env`, no usan red ni disco: TinyDB en memoria, SQLite en
memoria para inventario y una clave JWT de prueba.

Cobertura de cada suite por separado (desde la raíz; el comando falla si no se
alcanza el mínimo):

```bash
# AUTH-088: solo las pruebas de lógica de auth sobre los módulos de auth (mínimo 70 %)
uv run pytest services/api/tests \
  --ignore=services/api/tests/test_route_protection.py --ignore=services/api/tests/test_cache.py \
  --ignore=services/api/tests/test_suppliers.py --ignore=services/api/tests/test_inventory.py \
  --cov=services.api.core.security --cov=services.api.core.dependencies \
  --cov=services.api.routes.auth --cov=services.api.routes.users \
  --cov=services.api.routes.profiles --cov=services.api.routes.auth_password \
  --cov=services.api.services.users --cov=services.api.models.auth --cov-fail-under=70

# API-042: suppliers + inventario (mínimo 60 %)
uv run pytest services/api/tests/test_suppliers.py services/api/tests/test_inventory.py \
  --cov=services.api.routes.suppliers --cov=services.api.routers.inventory \
  --cov=services.api.services.inventory --cov-fail-under=60
```

### Frontend (Next.js / TypeScript) — Jest

Independiente del backend, desde el proyecto del backoffice:

```bash
cd uis/backoffice
npm install
npx jest --coverage     # o: npm test / npm run test:coverage
```

Configuración en `uis/backoffice/jest.config.js` (ts-jest, entorno Node, alias `@/`).

## Enfoque: lógica, no HTTP

Las pruebas llaman directamente a las **funciones de los endpoints, servicios y
dependencias** (`create_user`, `login`, `get_current_user`, `update_user`…) con
una base en memoria, y afirman **lo que el endpoint decide**: qué excepción
lanza y con qué código (401/403/404/409), qué queda en la base, qué tokens
dejan de valer. No se comprueba cómo FastAPI serializa la respuesta.

Dos excepciones deliberadas usan `TestClient`, porque lo que protegen es el
cableado de la aplicación y no se puede comprobar de otra forma:

- `test_route_protection.py`: recorre **todas** las rutas del esquema y exige
  401 sin token o con token inválido, salvo las públicas. Evita que una ruta
  nueva quede abierta por olvido.
- `test_cache.py`: caché por usuario y logs de tiempos sin datos sensibles.

En el frontend se sustituye `fetch` y los módulos de Next.js (`next/headers`,
`next/navigation`, `server-only`) y la telemetría, para probar el parseo de
respuestas, los mensajes de error y las decisiones de sesión.

## Plan de pruebas (AUTH-088)

Casos decididos **antes** de escribir el código de los tests. Cada endpoint
tiene camino feliz (✅), caso límite (◐) y modo de fallo (✖).

| Endpoint / módulo | ✅ Camino feliz | ◐ Caso límite | ✖ Modo de fallo |
| --- | --- | --- | --- |
| `POST /users` — `test_register.py` | Guarda solo hash bcrypt, rol `user` por defecto, crea el `Profile` con los datos de contacto | Sin campos de perfil → perfil vacío; email duplicado con otra capitalización (409); contraseña de 72 bytes exactos | Anónimo/usuario intenta registrarse como `admin`/`manager` (403); email inválido o vacío, rol desconocido, campo `username` heredado; contraseña vacía, corta o >72 bytes |
| `POST /auth/login` — `test_login.py` | Token firmado con `sub` = id de TinyDB; cuerpo JSON y formulario OAuth2 de `/docs` | Email sin distinguir mayúsculas; contraseña sensible a mayúsculas y espacios; cuenta desactivada; cuerpo vacío o incompleto | Contraseña errónea / email inexistente / >72 bytes → mismo 401 genérico (sin enumerar cuentas); JSON mal formado; campos extra; el login no modifica el usuario |
| Token — `test_token.py` (generación + `get_current_user`) | Claims `sub`, `iat`, `token_version`, `purpose`; el rol se lee de la BD, no del token | **Duración = `ACCESS_TOKEN_EXPIRE_MINUTES` exactos** (1, 30, 1440); variable heredada; por defecto 30; variable vacía → 30; válido 30 s antes de expirar | **Token expirado**; token emitido ya caducado (regresión del incidente); mal formado; firmado con otra clave; payload manipulado; sin `sub`/`exp`/`iat`/`token_version`; token de reseteo usado como acceso; revocado por cambio de credenciales; usuario desactivado o borrado; configuración insegura (clave corta, `none`, expiración ≤0) |
| `GET /auth/me` — `test_auth_me.py` | Email, rol y perfil vinculado | Usuario sin perfil → `profile: null`; cada uno ve solo el suyo | Token inválido (401) |
| `GET /users`, `GET /users/{id}` — `test_users_read.py` | Staff lista todo; el usuario lee su cuenta | BD vacía; nunca expone `hashed_password` | Usuario normal lista (403); leer otra cuenta → 403 también si el id no existe (no se pueden sondear ids) |
| `PUT /users/{id}` — `test_users_update.py` | El dueño cambia email/contraseña con la actual y se revocan sus tokens; admin cambia rol/estado; admin resetea contraseña | Mismo email con otra capitalización mantiene la sesión; actualización vacía; último admin activo; un admin puede dejar de serlo si hay otro | Editar otra cuenta (403); auto-asignarse rol/estado (403); **cambiar credenciales sin la contraseña actual (403)**; email ya usado (409); id inexistente (404); payload inválido |
| `DELETE /users/{id}` — `test_users_delete.py` | Admin borra usuario, perfil y sesiones; el usuario borra su cuenta | Último admin activo (409); un admin inactivo no cuenta como "otro admin" | Borrar otra cuenta (403); id inexistente (404) |
| `/profiles` — `test_profiles.py` | `GET/PUT /profiles/me` solo del llamante; PUT parcial | Usuario sin perfil: GET 404 y PUT lo crea; limpiar un campo con `null` | Cambiar dueño o credenciales desde el perfil; leer perfil ajeno (403); **solo el dueño modifica, ni el admin** (403); id inexistente (404) |
| `POST /auth/forgot-password`, `/reset-password` — `test_password_recovery.py` | Encola un email con token de propósito `password_reset`; reset cambia el hash y revoca sesiones | Email desconocido no encola nada; respuesta neutral para entradas desconocidas, inválidas o mal formadas; contraseña de 72 bytes; uso único | Token caducado, manipulado o de acceso (400); usuario borrado; contraseña nueva vacía/corta/>72 bytes |
| `POST /auth/change-password` — `test_change_password.py` | Con la contraseña actual cambia y revoca sesiones | Invalida enlaces de reseteo pendientes; 72 bytes | Contraseña actual incorrecta (400, sin cambios); campos vacíos o ausentes |
| Hash — `test_password_hashing.py` | bcrypt con sal aleatoria verifica | Unicode a 72 bytes | Contraseñas parecidas no verifican; >72 bytes no se trunca hasta coincidir; UTF-8 inválido no lanza |

## Backoffice (API-042)

| Grupo | ✅ | ◐ | ✖ |
| --- | --- | --- | --- |
| Proveedores — `test_suppliers.py` | Alta con id y `updated_at`; filtros país+categoría combinados; cambio de tarifa y estado | Directorio vacío / filtros sin resultado; `updated_at` solo cambia si cambia la tarifa; caché por usuario invalidada al escribir; categorías vacías, tarifa 0, nombre vacío | Moneda incoherente con el país (UK→GBP, USA→USD); fecha imposible (`2024-02-30`); `NaN`; categoría desconocida; campos extra; 404 en todas las operaciones |
| Inventario — `test_inventory.py` | Producto nuevo con stock 0; stock = entradas − salidas; cada movimiento guarda el `user_uuid` del token | Consumir exactamente el stock disponible; inventario vacío; caché refrescada tras un movimiento; cantidad 0, clínica 0/13, tipos laxos (`"5"`) | Consumir más de lo disponible → 400 con `X-Available-Stock` y sin registrar nada; SKU duplicado (409); producto inexistente (404); producto inválido |

## Frontend (FE-019 y auth en TypeScript)

| Función (`uis/backoffice/src/lib`) | ✅ | ✖ (y límites) |
| --- | --- | --- |
| `passwordError` (`auth.ts`) | Contraseña válida y confirmada; 8 caracteres y 72 bytes exactos | Vacía, 7 caracteres, 7 emoji (cuenta caracteres, no unidades UTF-16), >72 bytes, confirmación distinta |
| `authRequest` (`auth.ts`) | POST JSON con `X-Request-ID` y telemetría; devuelve los 401 al formulario | Red caída: registra `auth_login_failed` solo en login y relanza |
| `apiUrl`, `sameOrigin` (`server-api.ts`) | Une base y ruta sin doble barra; origen igual al host; proxy TLS | Otro sitio, otro puerto, *downgrade* de protocolo, sin Origin, Origin mal formado o con ruta (protección CSRF) |
| `requireSession` (`server-api.ts`) | Verifica contra `GET /auth/me` con el token | Sin cookie → `/login` sin llamar a la API; 401 → `/login`; API caída o 5xx → error genérico sin registrar el token |
| `listSuppliers`, `createSupplier`, `updateSupplierRate` (`suppliers.ts`) | Lista parseada con solo los filtros activos; alta; directorio vacío | Respuesta con forma inválida; 401 en lenguaje claro; 5xx sin filtrar detalles; red caída; 200 no JSON; errores 422 unidos; 404 |
| `listProducts`, `listOrders`, `createOutbound` (`inventory.ts`) | Catálogo con stock; historial; alta de salida | Stock fraccionario, unidad desconocida; evento de stock bajo solo al cruzar el umbral; 401 con estado para redirigir; tipo de consumo o fecha inválidos; stock insuficiente con cantidad disponible; red caída |

## Resultados de cobertura

`uv run pytest --cov` desde la raíz (194 tests):

| Módulo | Cobertura | | Módulo | Cobertura |
| --- | --- | --- | --- | --- |
| `core/security.py` | 100 % | | `routes/suppliers.py` | 100 % |
| `core/dependencies.py` | 96 % | | `routers/inventory.py` | 99 % |
| `routes/auth.py` | 100 % | | `services/inventory.py` | 100 % |
| `routes/users.py` | 100 % | | | |
| `routes/profiles.py` | 100 % | | | |
| `routes/auth_password.py` | 96 % | | | |
| `services/users.py` | 98 % | | | |
| `models/auth.py` | 98 % | | **Total API** | **94 %** |

Solo con las pruebas de lógica de auth (sin `TestClient`), los módulos de auth
quedan en **98 %** (objetivo 70 %); solo con `test_suppliers.py` +
`test_inventory.py`, el backoffice queda en **99 %** (objetivo 60 %).

Jest (`npx jest --coverage`, 51 tests): `auth.ts` 100 %, `server-api.ts` 100 %,
`suppliers.ts` 94 %, `inventory.ts` 73 % de líneas; **85 %** en total. Lo no
cubierto de `inventory.ts` es telemetría de validación 422 y el hook de React
`useInventoryError`.

## Bugs detectados por la batería (y corregidos)

1. **Una variable de expiración vacía tumbaba la API** — `core/security.py`.
   `ACCESS_TOKEN_EXPIRE_MINUTES=` (lo habitual al copiar una plantilla `.env`)
   lanzaba `RuntimeError` en el arranque en lugar de usar el valor por defecto.
   Es la misma familia que el incidente del ticket: configuración de expiración
   que deja a todos fuera. Ahora un valor en blanco equivale a "no definido"
   (30 min); `0`, negativos o texto siguen fallando de forma explícita.
   Test: `test_token.py::test_blank_expiry_in_env_file_falls_back_to_the_default`.

2. **Se podía secuestrar una cuenta con solo el token** — `routes/users.py`.
   `PUT /users/{id}` dejaba al propio usuario cambiar email o contraseña sin la
   contraseña actual: con un token robado se cambia el email, se pide
   recuperación y el dueño pierde la cuenta (`/auth/change-password` sí la pedía,
   pero el PUT la saltaba). Ahora el cambio de credenciales propio exige
   `current_password` (403 si falta o es incorrecta); un admin que gestiona a
   otro usuario no la necesita. `current_password` nunca se guarda.
   Tests: `test_users_update.py::test_self_service_credential_change_requires_the_current_password`.

3. **El backoffice verificaba la sesión contra una ruta que ya no existe** —
   `uis/backoffice/src/lib/server-api.ts`. `requireSession` llamaba a `GET /me`,
   que AUTH-01 movió a `GET /auth/me`; la API respondía 404 y cada página
   protegida mostraba "No se pudo verificar la sesión". Ahora usa `/auth/me`.
   Test: `__tests__/server-api.test.ts` › "verifies the session token against GET /auth/me".

En los tres casos el test se escribió primero, falló por la causa esperada
(`RuntimeError`, `DID NOT RAISE HTTPException`, `Received: ".../me"`) y pasó
tras la corrección.

## Flujo asistido por IA

Se usó un agente de IA (Claude Code) con el código de cada endpoint como
contexto para pedirle casos límite que faltaran en el plan inicial. Casos que
aportó y se incorporaron tras revisarlos:

- Entradas vacías o en blanco en la **configuración**, no solo en los formularios
  (→ bug 1).
- "¿Qué puede hacer alguien que solo tiene el token?" sobre cada ruta de
  escritura (→ bug 2).
- Contrato entre frontend y API: rutas que el frontend llama y que el backend
  renombró (→ bug 3).
- bcrypt solo usa 72 bytes: una contraseña más larga no debe "coincidir" por
  truncado; contar caracteres frente a unidades UTF-16 (emoji) en el frontend.
- Token con el payload de otro usuario y la firma original; token sin cada claim
  obligatorio; token de reseteo usado como acceso.
- Un admin **inactivo** no debe contar como "otro admin" al borrar al último.
- Leer una cuenta ajena devuelve 403 aunque el id no exista (no revela ids).

La IA también generó el esqueleto de los tests; cada uno se revisó, se
simplificó y se comentó donde la aserción no es evidente. Las decisiones de qué
probar (y qué no) están en las tablas anteriores.

## Fuera de alcance

- Envío real de emails (Resend): se sustituye por una bandeja en memoria.
- PostgreSQL real: inventario usa SQLite en memoria con los mismos modelos.
- Concurrencia entre procesos: TinyDB solo admite un proceso.
- Componentes React de las páginas (no son utilidades).
