# HealthCore MCP server

Servidor MCP que expone dos sistemas de HealthCore a cualquier cliente MCP autorizado:
- los **tickets del Incidents Manager**, para consultar, crear y cambiar su estado;
- el **inventario**, solo para consultar.

Se apoya en los servicios existentes (`/api/incidents` y `/inventory/products` de la API) sin
reemplazarlos, y está protegido con **OAuth 2.1 mediante MCP Auth** (`mcpauth`).

```text
Cliente MCP (agente LangGraph, MCP Playground, otro equipo)
  │  1. token OAuth (client credentials)      ┌─────────────────────────────┐
  ├──────────────────────────────────────────▶│ Keycloak (realm healthcore) │
  │  2. POST /mcp  Authorization: Bearer …    └─────────────────────────────┘
  ▼
HealthCore MCP server  (mcps/healthcore_mcp, Streamable HTTP :8765/mcp)
  │  MCP Auth: JWT (firma JWKS, iss, exp, aud) → scopes por tool → auditoría
  │  3. cuenta de servicio `mcp-service` (rol user)
  ▼
API HealthCore (:8000)  GET/POST /api/incidents · PATCH /api/incidents/{id}/status · GET /inventory/products
```

## Transporte: Streamable HTTP

Elegí **Streamable HTTP** (un endpoint, `/mcp`, en modo *stateless*) y no stdio, por tres
razones:
- **Varios clientes remotos.** El servidor lo consumen el agente (dentro de la API), otros
  equipos y partners. stdio solo sirve a un proceso local que lanza el servidor como
  subproceso, y no se puede compartir.
- **OAuth vive en HTTP.** La especificación de autorización de MCP define el token bearer en
  la cabecera `Authorization` y el discovery del servidor de autorización (Protected Resource
  Metadata, RFC 9728). Con stdio no hay cabeceras: la credencial tendría que pasar por
  variables de entorno del proceso, sin scopes por cliente ni revocación.
- **Stateless.** Cada petición se procesa con su propio contexto de autenticación, sin sesión
  en memoria entre peticiones. Escala horizontalmente y no mezcla la identidad de dos
  clientes.

## Autenticación y autorización (MCP Auth)

**Validación del token** (`auth.py` y `create_app()` en `server.py`):
- **Modo resource server.** `MCPAuth(protected_resources=ResourceServerConfig(...))` publica
  **Protected Resource Metadata** en `/.well-known/oauth-protected-resource/mcp`: recurso,
  servidor de autorización y `scopes_supported`. No uso la auth integrada de FastMCP.
- **Middleware bearer.** `bearer_auth_middleware("jwt", audience="healthcore-mcp", resource=...)`
  valida **cada** petición a `/mcp`:
  - firma contra el JWKS del emisor;
  - emisor;
  - expiración;
  - audiencia.

  Sin un token válido no se puede ni listar tools: la respuesta es `401` con
  `WWW-Authenticate: Bearer error=…, resource_metadata="…"`.
- **Cualquier proveedor OIDC.** El servidor de autorización se configura con
  `MCP_OAUTH_ISSUER`. En local es Keycloak (servicio `keycloak` del `docker-compose.yml`), con
  el realm versionado en `keycloak/healthcore-realm.json`.

**Mínimo privilegio, en dos niveles:**

1. **En el servidor de autorización.** Cada cliente OAuth solo puede obtener los scopes de su
   función. Si pide más, Keycloak responde `invalid_scope`.

   | Cliente | Scopes | Para qué |
   | --- | --- | --- |
   | `healthcore-agent` | `incidents:read`, `inventory:read` | El agente LangGraph solo consulta |
   | `healthcore-ops` | `incidents:read`, `incidents:write`, `inventory:read` | Consola de operaciones: gestiona tickets |
   | `healthcore-inventory-reader` | `inventory:read` | Partner o dashboard de stock: no ve tickets |

2. **En el servidor MCP.** Cada tool exige su scope (`TOOL_SCOPES`, comprobado por
   `GovernanceMiddleware` antes de ejecutarla). No existe ningún scope de escritura de
   inventario.

**Cuenta de servicio hacia la API.** El servidor llama a la API de HealthCore con la cuenta
`mcp-service` (rol `user`, no admin), creada con `scripts/create_api_service_account.py`.
Los scopes OAuth deciden qué puede hacer cada cliente MCP; la cuenta de servicio solo da
acceso de red a la API.

## Tools (discovery)

`tools/list` devuelve, para cada tool:
- nombre, título y descripción, que incluye el scope requerido, el endpoint real y los
  errores posibles;
- `inputSchema`, con los valores del catálogo como `enum`, y `outputSchema`, con los mismos
  campos que la API;
- anotaciones `readOnlyHint`, `destructiveHint` e `idempotentHint`.

`initialize` devuelve `instructions` con el resumen del servidor, los scopes y los códigos de
error. El recurso `healthcore://incidents/catalog` contiene los valores permitidos, el ciclo
de estados, qué scope pide cada tool y el significado de cada código de error.

| Tool | Scope | Llama a | Lectura |
| --- | --- | --- | --- |
| `get_incident(incident_id)` | `incidents:read` | `GET /api/incidents/{id}` | sí |
| `list_incidents(status?, category?, origin?, branch?, limit=20)` | `incidents:read` | `GET /api/incidents?…` | sí |
| `create_incident(title, description, category, origin, branch)` | `incidents:write` | `POST /api/incidents` (nace en `open`) | no |
| `update_incident_status(incident_id, status)` | `incidents:write` | **`PATCH /api/incidents/{id}/status`**, el endpoint del ciclo de vida: `open → in_progress / discarded`, `in_progress → resolved / discarded` | no |
| `query_inventory(operation="list"\|"get", sku?, product?)` | `inventory:read` | `GET /inventory/products` | sí, siempre |

Los campos y valores (`status`, `category`, `origin`, `branch`, `sku`, `current_stock`…) son
los de las APIs existentes y salen del mismo catálogo (`packages/shared/incidents`).

### Inventario de solo lectura por diseño

No basta con no implementar la escritura. Hay cuatro capas, y cualquier intento se rechaza
explícitamente:

1. **Scope.** No existe `inventory:write`: ningún cliente puede obtener permiso de escritura
   sobre el inventario.
2. **Tools.** No hay ninguna tool de escritura de inventario. Llamar a una que no existe, como
   `update_inventory`, devuelve `UNKNOWN_TOOL`.
3. **Tool de consulta.** `query_inventory` solo acepta `operation="list"` o `"get"`. Cualquier
   operación de escritura (`update`, `adjust`, `consume`, `delete`, `create`, …) devuelve
   **`INVENTORY_READ_ONLY`** y no llega a la API.
4. **Cliente HTTP.** `api_client.py` no tiene ningún método que escriba en el inventario.

```json
{"code": "INVENTORY_READ_ONLY",
 "message": "The inventory is read-only through the HealthCore MCP server; no stock was changed.",
 "details": {"operation": "update", "allowed_operations": ["list", "get"]}}
```

## Códigos de error

Hay dos capas, con códigos distintos entre sí:

| Capa | Código | Cuándo |
| --- | --- | --- |
| HTTP (MCP Auth) | **401** `missing_auth_header` / `invalid_token` / `invalid_audience` | Sin token, token falso, caducado, de otro emisor o para otra audiencia. Lleva `WWW-Authenticate` con `resource_metadata` |
| HTTP (MCP Auth) | **403** `insufficient_scope` | Reservado para scopes exigidos a todas las peticiones (hoy ninguno) |
| Tool (`isError: true`, JSON `{"code","message","details"}`) | `INSUFFICIENT_SCOPE` | El token es válido pero no incluye el scope de la tool (**autorización**) |
| Tool | `VALIDATION_ERROR` | Argumento ausente, de tipo incorrecto o fuera del catálogo, o rechazado por la API con 400 (**validación**), con la lista de campos |
| Tool | `UNKNOWN_TOOL` | La tool no existe |
| Tool | `NOT_FOUND` | El ticket no existe |
| Tool | `INVALID_STATUS_TRANSITION` | El ciclo de vida no permite ese cambio de estado |
| Tool | `INVENTORY_READ_ONLY` | Intento de escritura sobre el inventario |
| Tool | `UPSTREAM_TIMEOUT` | La API no respondió en el timeout de 4 s (`MCP_API_TIMEOUT_SECONDS`) |
| Tool | `UPSTREAM_UNAVAILABLE` | La API está caída, devolvió un 5xx o hubo un error inesperado (sin detalles internos) |
| Tool | `UPSTREAM_AUTH_FAILED` | La API rechazó la cuenta de servicio del servidor MCP |

## Logs de auditoría

`GovernanceMiddleware` escribe **una línea por invocación**, también para las rechazadas, las
inválidas y las de tools inexistentes. Va al logger `mcp.audit` (stdout) y se añade a
`MCP_AUDIT_LOG` (por defecto `mcps/healthcore_mcp/logs/tool-invocations.jsonl`, que no se
versiona):

```json
{"ts": "2026-11-23T09:51:57.043+00:00", "tool": "get_incident", "client_id": "healthcore-agent",
 "subject": "…", "scopes": ["incidents:read", "inventory:read"], "arguments": {"incident_id": 2},
 "argument_names": ["incident_id"], "result": "ok", "error_code": null, "duration_ms": 268.0}
```

De los argumentos solo se registran identificadores y valores del catálogo. El texto libre
(`title`, `description`), que podría contener datos de pacientes, nunca se escribe en el log.

## Puesta en marcha (local)

```bash
python mcps/healthcore_mcp/scripts/init_dev_secrets.py          # crea mcps/healthcore_mcp/.env (no se versiona)
docker compose up -d keycloak                                    # :8081, importa el realm healthcore
HEALTHCORE_ADMIN_USERNAME=… HEALTHCORE_ADMIN_PASSWORD=… \
  python mcps/healthcore_mcp/scripts/create_api_service_account.py   # usuario mcp-service en la API
python -m mcps.healthcore_mcp                                    # http://127.0.0.1:8765/mcp
python mcps/healthcore_mcp/scripts/get_token.py healthcore-ops   # token para probar desde un cliente MCP
```

**Variables de entorno:**

| Variable | Valor por defecto |
| --- | --- |
| `MCP_OAUTH_ISSUER` | `http://localhost:8081/realms/healthcore` |
| `MCP_RESOURCE_URL` | `http://localhost:8765/mcp`; la URL pública si se expone |
| `MCP_AUDIENCE` | `healthcore-mcp` |
| `HEALTHCORE_API_URL` | `http://127.0.0.1:8000` |
| `MCP_API_USERNAME`, `MCP_API_PASSWORD` | La cuenta de servicio |
| `MCP_API_TIMEOUT_SECONDS` | `4` |
| `MCP_ALLOWED_HOSTS` | Opcional: limita los `Host` aceptados (protección contra DNS rebinding). Por defecto se acepta cualquiera |
| `MCP_HOST`, `MCP_PORT` | `127.0.0.1`, `8765` |

### Probar desde MCP Playground (Codespaces)

1. En Codespaces, reenvía el puerto 8765 con visibilidad **Public**.
2. Arranca el servidor con `MCP_RESOURCE_URL=https://<codespace>-8765.app.github.dev/mcp`, para
   que los metadatos anuncien la URL pública. Si quieres limitar los hosts aceptados, añade
   `MCP_ALLOWED_HOSTS=<codespace>-8765.app.github.dev`.
3. En MCP Playground, pega la URL reenviada (`…/mcp`), con transporte Streamable HTTP, y la
   cabecera `Authorization: Bearer <token de get_token.py>`.
4. Prueba un flujo completo por tool, más un intento de escritura de inventario, que debe dar
   `INVENTORY_READ_ONLY`.

Sin la cabecera, Playground recibe `401` con la URL de los metadatos del recurso.

## Migración del agente

El agente LangGraph (`services/agent`) ya **no llama al Incidents Manager ni al inventario
directamente**:
- **Cliente MCP.** `services/agent/tools.py` carga las tools de este servidor con
  `langchain-mcp-adapters` (`MultiServerMCPClient`, `streamable_http`) y las llama como
  cliente MCP: `get_incident`, `list_incidents` y `query_inventory`.
- **Credencial propia.** Se autentica como el cliente OAuth `healthcore-agent`, con solo
  lectura.
- **Sin código antiguo.** La implementación HTTP directa de la parte 2 se eliminó, y hay un
  test que falla si un módulo del agente vuelve a usar `/api/incidents`.
- **Mismo enrutamiento y fallback.** El enrutamiento RAG/tools y los fallbacks son los mismos
  de antes; lo demuestran los 16 casos de eval, con rutas idénticas a las de la parte 2.

Detalle en [`docs/agent/agent-design.md`](../../docs/agent/agent-design.md) §9.

## Pruebas

- `tests/mcps/test_healthcore_mcp.py` (25): servidor real con uvicorn, un servidor OAuth de
  prueba (discovery, JWKS, `/token`) y una API sustituta que registra cada petición. Cubren:
  - 401 sin token, con token falso, firmado con otra clave, caducado, de otra audiencia o de
    otro emisor;
  - Protected Resource Metadata y discovery;
  - el ciclo de vida de un ticket por `PATCH …/status`;
  - los códigos de error y las escrituras de inventario rechazadas sin tocar la API;
  - los scopes por tool, timeout y caída de la API;
  - la auditoría.
- `tests/pipelines/test_agent_tools.py` (12): el agente como cliente MCP de ese mismo
  servidor.
