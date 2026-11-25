# Auditoría OWASP Top 10 — HealthCore (backend, frontend y sistema agéntico)

**Fecha:** 2026-11-25 · **Rama:** `feature/owasp-top10-audit` · **Referencia:** OWASP Top 10:2021 (la numeración que usa el BRIEFING §7: A01 control de acceso, A02 criptografía, A05 configuración).

**Sin PHI:** este informe y su evidencia (`docs/security/evidence/`) solo contienen IDs sintéticos y metadatos. La IP pública del VPS aparece como `VPS_HOST`.

## 1. Alcance

| Componente del BRIEFING §3 / §7 | En este fork | Dónde |
| --- | --- | --- |
| Asistente de documentación clínica (notas SOAP) | **No existe**: no hay código que genere ni envíe notas | — |
| Flujo de referidos automatizado | **No existe** como workflow ejecutable; el agente solo consulta la base de conocimiento | — |
| RAG sobre protocolos y políticas | Sí | `data/pipelines/rag.py`, `POST /knowledge/query` |
| Agente de cumplimiento (Compliance assistant) | Sí: grafo LangGraph, herramientas MCP, memoria, guardrails, chat WebSocket | `services/agent/` |
| Flujo agéntico de RFPs (3 partes, con HITL) | Sí | `data/pipelines/rfp_intake/`, `/api/rfp/*` |
| Sistema de logs de acceso y trazabilidad | Sí: traza del agente, auditoría MCP, `rfp_trace`, eventos de guardrails | `services/agent/tracing.py`, `mcps/healthcore_mcp/audit.py`, `data/pipelines/rfp_intake/tracing.py` |
| Dashboard KPI + SSE | Sí: reporting mensual y SSE de tickets RFP | `services/reporting/`, `GET /api/rfp/events` |
| Frontend de staff | Sí: backoffice Next.js | `uis/backoffice/` |
| Servidor | VPS DigitalOcean `bc-vps-278` (Ubuntu 22.04) con el despliegue `/opt/healthcore` | `infra/hardening/` |

**Método:**
- **Revisión manual** del código y de la configuración de cada carril: backend (`services/api`, `services/reporting`, `mcps`), frontend (`uis/backoffice`) y sistema agéntico (`services/agent`, `data/pipelines/rfp_intake`, RAG).
- **Escaneos automáticos** ([`evidence/scans.txt`](evidence/scans.txt)): `pip-audit`, `npm audit`, `bandit` y `gitleaks` sobre todo el historial.
- **Inspección en solo lectura del VPS** antes de tocarlo.
- **Pruebas antes/después** de cada corrección.

**Severidad:**
- **Crítica:** explotable hoy, con impacto en datos, firmas o el servidor.
- **Alta:** exposición grave con alguna barrera.
- **Media:** debilidad con mitigación parcial.
- **Baja:** endurecimiento o higiene.

## 2. Resumen de hallazgos

| ID | Cat. | Carril | Hallazgo | Severidad | Estado | Evidencia |
| --- | --- | --- | --- | --- | --- | --- |
| SRV-01 | A07/A05 | Servidor | SSH con `PermitRootLogin yes` y `PasswordAuthentication yes`, bajo ataque real: 192.555 contraseñas fallidas en `auth.log` (99.255 contra root) | **Crítica** | **Corregida** | [server-1-before](evidence/server-1-before.txt) → [server-2-after](evidence/server-2-after.txt) |
| SRV-02 | A05 | Servidor | Sin firewall (ufw inactivo, `INPUT ACCEPT`): cualquier proceso que escuche en `0.0.0.0` queda expuesto (demostrado con el puerto 9999) | **Crítica** | **Corregida** | ídem |
| APP-01 | A01 | Agéntico (HITL RFP) | Cualquier usuario autenticado podía firmar la aprobación de un departamento, incluida la de Compliance obligatoria, escribiendo el nombre del aprobador | **Crítica** | **Corregida** | [before](evidence/fix-A01-rfp-approver-before.txt) → [after](evidence/fix-A01-rfp-approver-after.txt) |
| APP-02 | A07 | Backend | `POST /login` sin protección contra fuerza bruta (intentos ilimitados) | **Crítica** | **Corregida** | [fix-A07-login-throttle](evidence/fix-A07-login-throttle.txt) |
| SRV-03 | A05/A01 | Servidor | Sin usuario operativo: todo como root; código del despliegue legible por todos (644/755) | Alta | **Corregida** | ídem SRV-01 |
| APP-03 | A05 | Backend/Frontend | Contenedores de API, worker, Flower y UI ejecutándose como root. Además, el Dockerfile de la API no construía (contexto incorrecto) | Alta | **Corregida** (en el repo) | [fix-A05-containers-non-root](evidence/fix-A05-containers-non-root.txt) |
| APP-04 | A06/A02 | Backend | `python-jose` (CVE-2026-85394, sin versión corregida) y `ecdsa` (CVE-2024-23342) en la cadena de JWT | Alta | **Corregida** | [pip-audit before](evidence/pip-audit-before.json) → [after](evidence/pip-audit-after.json) |
| APP-05 | A05 | Backend | `/docs`, `/redoc` y `/openapi.json` públicos: 49 rutas y esquemas sin autenticación | Media | **Corregida** | [fix-A05-api-docs](evidence/fix-A05-api-docs.txt) |
| APP-06 | A05 | Frontend | Sin cabeceras de seguridad (CSP, anti-clickjacking, nosniff) y con `X-Powered-By` | Media | **Corregida** | [fix-A05-security-headers](evidence/fix-A05-security-headers.txt) |
| APP-07 | A02 | Backend | Conexiones a PostgreSQL remoto (Supabase) con `sslmode=prefer`, que permite caer a texto plano | Media | **Corregida** | [fix-A02-db-tls](evidence/fix-A02-db-tls.txt) |
| AG-01 | A01 | Agéntico | El agente usa su propia identidad OAuth (solo lectura), no la del usuario: cualquier miembro del personal ve incidentes y stock de todas las clínicas a través del agente | Media | Abierta (diseño) | §4 A01 |
| AG-02 | A02 | Agéntico/Frontend | El chat WebSocket entrega el JWT de sesión al JavaScript de la página para el frame `auth` | Media | Mitigada | §4 A02 |
| APP-08 | A04 | Backend | `POST /telemetry/events` acepta eventos sin autenticar (lotes de 20 como máximo, validados contra el contrato), sin límite de frecuencia | Baja | Aceptada | §4 A04 |
| APP-09 | A09 | Todos | Los eventos de seguridad se registran, pero no hay alertas | Media | Abierta | §4 A09 |
| SRV-04 | A05 | Servidor | Reinicio de kernel pendiente; OpenClaw (gateway de modelos, `127.0.0.1:18789`) corre como root; el stack desplegado usa las imágenes antiguas (root) | Baja | Abierta (recomendación) | §3 |
| REPO-01 | A02 | Repo | 4 JWT de desarrollo en informes Lighthouse de un commit antiguo (ya caducados) | Baja | Aceptada | [scans](evidence/scans.txt) |

Las **4 vulnerabilidades críticas están corregidas** y demostradas con antes/después. Las de severidad alta y media también, salvo las tres de diseño (AG-01, AG-02 y APP-09), que se documentan con su mitigación y su plan.

## 3. Endurecimiento del servidor

**Servidor:** VPS `bc-vps-278` (DigitalOcean, Ubuntu 22.04). Corre el stack de HealthCore en Docker (`/opt/healthcore`: ui, api y Postgres) y otro servicio propio (OpenClaw).

**Scripts versionados:**
- [`infra/hardening/harden.sh`](../../infra/hardening/harden.sh): idempotente y por etapas. La etapa `ssh` se niega a cerrar root si el usuario operativo no tiene clave, y valida con `sshd -t` antes de recargar.
- [`infra/hardening/verify.sh`](../../infra/hardening/verify.sh): imprime PASS/FAIL por control.
- Procedimiento: [`infra/hardening/README.md`](../../infra/hardening/README.md).

| Control | Antes | Después | Verificación |
| --- | --- | --- | --- |
| Login SSH como root | Permitido (`PermitRootLogin yes`) | **Prohibido** | `ssh root@VPS_HOST` → `Permission denied (publickey)` |
| Autenticación por contraseña | Ofrecida (`publickey,password`) | **Solo clave**, `AuthenticationMethods publickey`, `MaxAuthTries 3`, `AllowUsers deploy`, X11 y agent forwarding desactivados | el servidor solo anuncia `publickey` |
| Usuario operativo | Solo `root` | `deploy`: no root, `sudo` con contraseña, **fuera del grupo `docker`** (que equivale a root) | `verify.sh` |
| Permisos de carpetas | `/opt/healthcore` root 755/644 | código `deploy:healthcore` 750/640; config sensible `/etc/healthcore` `root:healthcore` 750/640; `.env` 600; logs `/var/log/healthcore` `deploy:healthcore` 750 | `verify.sh`: ningún fichero legible por «otros» |
| Firewall | Inactivo | ufw `deny incoming`, solo **22/tcp con `limit`** | barrido externo: `22=ABIERTO` y cerrados 80, 443, 3000, 3001, 5432, 6379, 8000, 9999 y 18789, **aunque había un proceso escuchando en 0.0.0.0:9999** |
| Fuerza bruta | Sin respuesta | fail2ban (5 fallos en 10 min → 1 h) | 3 IPs atacantes baneadas en los primeros minutos |

**Resultado:** `verify.sh` pasó de 15 FAIL a **19/19 PASS** ([antes](evidence/server-1-before.txt), [después](evidence/server-2-after.txt)). Los contenedores siguieron en marcha durante todo el proceso y salen a Internet: las cadenas de Docker van antes que ufw.

**Puertos, según el BRIEFING §7 («bloquea acceso público a DB, colas y admin de modelos»):**
- PostgreSQL, Redis, Qdrant, Keycloak y Flower: solo en `127.0.0.1` (`docker-compose.yml:51` y siguientes).
- El gateway de modelos local: también solo en loopback.
- La aplicación se usa a través de un túnel SSH (cifrado): `ssh -L 3001:127.0.0.1:3001 deploy@VPS_HOST`. No hay HTTP en claro hacia Internet.
- Si se publica un dominio, el único puerto nuevo sería 443, detrás de un reverse proxy con TLS.

**Pendientes del servidor (SRV-04):**
- Programar el reinicio del kernel.
- Mover OpenClaw a un usuario propio.
- Redesplegar `/opt/healthcore` con las imágenes no-root de esta rama, cambiando antes el dueño del volumen `api_data` a uid 10001.
- Añadir el Cloud Firewall de DigitalOcean con la misma regla.

## 4. Evaluación por categoría (OWASP Top 10:2021 × tres carriles)

En cada celda: **Aplica / No aplica**, hallazgo y evidencia.

### A01 — Broken Access Control

| Carril | Evaluación |
| --- | --- |
| Backend | **Aplica, controlado.** Todas las rutas de negocio exigen JWT: `CurrentUser` / `AuthenticatedUser` (`services/api/core/dependencies.py:47`, con revocación por `token_version`). Las administrativas exigen rol: `require_admin` y `require_profile_manager` (`dependencies.py:67`, `services/reporting/router.py:36`). No hay CORS abierto (no se usa `CORSMiddleware`). Excepción: `POST /telemetry/events`, sin autenticar, ver A04. |
| Frontend | **Aplica, controlado.** La sesión está en una cookie `httpOnly; SameSite=Strict; Secure` en producción (`uis/backoffice/src/app/api/auth/[action]/route.ts:79`). El BFF comprueba el mismo origen en las mutaciones (`src/lib/api-proxy.ts`). La UI no decide permisos: la API devuelve 401/403. |
| **Sistema agéntico** | **Aplica. Crítica corregida (APP-01).** Los departamentos aprueban (HITL de la Parte 3) con el nombre del aprobador escrito en el cuerpo de la petición, sin comprobar la cuenta, de modo que cualquiera podía firmar como Claire Whitfield y saltarse la aprobación obligatoria de Compliance. **Corrección:** `RFP_APPROVERS` asigna cada departamento a cuentas concretas del backoffice. `require_approver` (`services/api/routes/rfp.py:218`, aplicado en `:276` y en el arbitraje) devuelve 403 a cualquier otra cuenta, falla en cerrado si no hay configuración y guarda la cuenta real en `submitted_by`, que el cliente no puede falsificar. Tests: `services/api/tests/test_rfp.py::test_only_the_mapped_account_can_sign_off_a_department` y `::test_without_a_configured_approver_nobody_can_sign_off`.<br>**Herramientas del agente:** usa su propio cliente OAuth con solo `incidents:read` e `inventory:read` (`services/agent/tools.py:11`, `:162`). El servidor MCP exige el scope de cada herramienta en cada llamada (`mcps/healthcore_mcp/server.py:147`), así que el agente no puede invocar `create_incident` ni `update_incident_status`.<br>**AG-01 (Media, abierta):** como el agente actúa con su identidad y no con la del usuario, cualquier miembro del personal ve los datos de todas las clínicas a través de él (incidentes, sin detalles de brechas activas, y stock). No hay PHI, pero tampoco segmentación por clínica: el modelo de usuarios no tiene atributo de clínica. Plan: añadir la clínica al usuario y propagarla como claim (token exchange) a los scopes MCP.<br>**Chat:** el WebSocket solo deja escribir al dueño de la sesión; un admin entra como observador de solo lectura (`services/agent/chat/router.py:77`, `:193`). Los endpoints de auditoría del agente son solo para admins (`services/agent/router.py:23`). |

### A02 — Cryptographic Failures

| Carril | Evaluación |
| --- | --- |
| Backend | **Aplica. Corregidas APP-04 y APP-07.**<br>**JWT:** HS256 con secreto de 32 bytes o más (`services/api/core/security.py:31`), algoritmo fijado al decodificar (`dependencies.py:30`). Se migró de `python-jose` (CVE sin versión corregida, más `ecdsa` vulnerable) a PyJWT: pip-audit pasó de 3 a 0.<br>**Contraseñas:** bcrypt (`security.py:40`).<br>**Base de datos:** `require_tls()` (`data/pipelines/connections.py:29`) añade `sslmode=require` a cualquier PostgreSQL remoto en los cuatro sitios que crean engines. Verificado contra Supabase: `ssl_in_use=True`. |
| Frontend | **Aplica, controlado.** No hay secretos en el bundle: las variables `NEXT_PUBLIC_*` son URLs públicas. En producción, cookie `Secure` y HSTS (ver A05). |
| **Sistema agéntico** | **Aplica.**<br>**Claves del LLM:** viven en el entorno y nunca en el repo (`services/api/.env` está ignorado; gitleaks no encuentra claves de API). El gateway LLM (4Geeks/LiteLLM) **no tiene BAA ni DPA firmado**, así que ningún dato de paciente puede llegarle: el cribado de PHI corre antes de cualquier modelo (`services/agent/runner.py:82`, `data/pipelines/rfp_intake/phi.py:63`).<br>**Trazas sin PHI:** la traza RFP resume con longitud + hash y criba los textos cortos (`data/pipelines/rfp_intake/tracing.py:35`). Los eventos SSE retienen cualquier valor que parezca PHI (`data/pipelines/rfp_intake/events.py:71`). Las del chat guardan el texto ya cribado.<br>**Checkpoints:** LangGraph con `pickle_fallback=False`.<br>**AG-02 (Media, mitigada):** el chat abre el WebSocket directamente contra la API, y la ruta `GET /api/agent/chat/socket` entrega el JWT al JS de la página para el primer frame (no va en la URL ni en los logs). Un XSS podría robarlo durante su vida (30 min). Mitigación: CSP con `connect-src` limitado (APP-06), ruta solo para el mismo origen (`socket/route.ts:13`) y token solo en memoria. Plan: un ticket de un solo uso, de 60 s y ligado a la sesión, en lugar del JWT. |

### A03 — Injection

| Carril | Evaluación |
| --- | --- |
| Backend | **Aplica, controlado.** SQL con SQLModel/SQLAlchemy parametrizado. Bandit solo marca 2 falsos positivos (`services/agent/memory/store.py:366`, `:372`: fragmentos constantes y valor como parámetro). No se usa `subprocess`, `eval` ni `pickle` en el código propio. Los PDF subidos se validan por firma y tamaño (`data/pipelines/rfp_intake/store.py:32`) y se convierten sin red. |
| Frontend | **Aplica, controlado.** React escapa todo el contenido: no hay `dangerouslySetInnerHTML` en `uis/backoffice/src`. El documento final y los borradores del modelo se muestran como texto (`<pre>`), nunca como HTML. |
| **Sistema agéntico** | **Aplica (prompt injection, BRIEFING §4). Controlado.**<br>Las instrucciones dentro de chunks del RAG o de datos de herramientas se aíslan antes de llegar al modelo (`services/agent/harness/isolation.py:43`).<br>El contenido de usuario y el recuperado van en bloques y roles separados (`data/pipelines/rag.py:128`).<br>Los intentos de «ignora las políticas…» se rechazan por reglas antes de cualquier llamada al modelo.<br>Las peticiones con identificadores de paciente se rechazan sin entrar al grafo (`services/agent/harness/rules.py:200`).<br>Lo mismo aplica en el flujo RFP: los extractos van dentro de `<documento_rfp>` y se tratan como datos.<br>Evidencia: [agentic-guardrail-tests.txt](evidence/agentic-guardrail-tests.txt), 106 tests en verde, incluidos los dos casos del BRIEFING §4: `test_instruction_change_is_rejected_by_rules_before_any_model_call` y `test_instructions_inside_a_knowledge_base_chunk_never_reach_the_model`. |

### A04 — Insecure Design

| Carril | Evaluación |
| --- | --- |
| Backend | **Aplica.** APP-08: `POST /telemetry/events` es público por diseño (telemetría del frontend antes del login), limitado a 20 eventos por lote y validado contra el contrato (`services/api/routes/telemetry.py:45`, `:49`); no tiene límite de frecuencia. Riesgo aceptado (Baja): es solo de escritura y no acepta campos fuera del contrato. Plan: límite de frecuencia por IP en el reverse proxy. |
| Frontend | **No aplica** de forma específica: no hay lógica de negocio en el cliente; toda decisión se valida en la API. |
| **Sistema agéntico** | **Aplica (excessive agency). Controlado.**<br>**Acciones irreversibles (BRIEFING §5):**<br>- Notas SOAP y notificaciones a pacientes: **no existen en este fork**, así que no hay acción que se pueda ejecutar sin médico.<br>- El documento final de una propuesta solo se genera con **todas** las aprobaciones humanas, Compliance siempre (`data/pipelines/rfp_intake/approval_graph.py:127`, `final_document.py:23`).<br>- La respuesta humana se valida antes de entrar al grafo (`approval.py:101`).<br>- El agente no tiene herramientas de escritura (A01).<br>- La memoria del agente solo se escribe tras la aprobación explícita del usuario.<br>- El chat interrumpe la generación de verdad: cierra el stream del modelo. |

### A05 — Security Misconfiguration

| Carril | Evaluación |
| --- | --- |
| Backend | **Aplica. Corregidas APP-03 y APP-05.**<br>**Documentación de la API:** Swagger, ReDoc y OpenAPI están apagados salvo con `API_DOCS_ENABLED=1` (`services/api/main.py:65`); antes 200 y 49 rutas, ahora 404.<br>**Imágenes:** la API, el worker y Flower corren como `app` (uid 10001), con el código de solo lectura para ese usuario y el estado en el volumen `/data` (`services/Dockerfile:32`). Se corrigió también el contexto de build roto.<br>**Puertos en local:** compose publica todo en `127.0.0.1` (`docker-compose.yml`). Keycloak `start-dev` y Flower sin autenticación son solo de desarrollo y en loopback (Baja). |
| Frontend | **Aplica. Corregida APP-06.** `next.config.ts` (`uis/backoffice/next.config.ts:25`) añade:<br>- CSP: `default-src 'self'`, `connect-src` limitado al origen y al WebSocket del chat, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri` y `form-action 'self'`;<br>- `X-Frame-Options DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP;<br>- HSTS en producción;<br>- sin `X-Powered-By`.<br>Verificado con Playwright: el login, el SSE de `/rfp` y el WebSocket del chat funcionan con **0 violaciones de CSP**. La UI corre como `node` (`uis/Dockerfile:19`). |
| **Sistema agéntico** | **Aplica, controlado.**<br>**Endpoints de depuración:** no hay endpoints de debug del agente en la API pública; la traza, la memoria y el resumen de guardrails son solo para admins.<br>**Envíos sin gate humano:** no hay envío automático de notas (no existen); la propuesta RFP no sale sin el gate humano (A04).<br>**Herramientas:** limitadas por scopes OAuth.<br>**Gateway de modelos en el VPS (OpenClaw):** solo en loopback y detrás del firewall (SRV-04: corre como root, pendiente). |

### A06 — Vulnerable and Outdated Components

| Carril | Evaluación |
| --- | --- |
| Backend | **Aplica. Corregida APP-04.** pip-audit sobre `uv.lock` (205 paquetes): de 3 vulnerabilidades a **0**. Las imágenes base están fijadas por versión (`python:3.12-slim`, `uv:0.9.26`, `redis:7.4-alpine`, `qdrant:v1.20.2`, `keycloak:26.8.1`). |
| Frontend | **Aplica.** `npm audit --omit=dev` en el backoffice: **0** vulnerabilidades (Next 16, React 19). |
| **Sistema agéntico** | **Aplica.** LangGraph 1.2, `langgraph-checkpoint-postgres` y `langchain-mcp-adapters` están dentro del mismo `uv.lock` auditado: 0 vulnerabilidades. |

### A07 — Identification and Authentication Failures

| Carril | Evaluación |
| --- | --- |
| Backend | **Aplica. Crítica corregida (APP-02).** `LoginThrottle` (`services/api/core/login_throttle.py:35`, aplicado en `routes/auth.py:27`): 5 fallos en 15 min por usuario → **429 con `Retry-After`**, sin comprobar siquiera la contraseña. Un login correcto limpia el contador, y el log lleva un hash del usuario, no el nombre. Antes: 8 intentos, 8 × 401; después: 401 × 5 y 429 desde el 6.º ([evidencia](evidence/fix-A07-login-throttle.txt); tests en `services/api/tests/test_auth.py`).<br>**Ya existente:** contraseñas de 8 caracteres o más con límite de bytes de bcrypt, mismo coste para usuarios inexistentes (no se pueden enumerar), JWT de 30 min, revocación por `token_version` y tokens de reset con propósito distinto. **Abierta:** MFA para cuentas de aprobadores y admins (Media). |
| Frontend | **Aplica, controlado.** El login muestra «Demasiados intentos fallidos…» ante un 429. La sesión es una cookie `httpOnly`; el token nunca va en `localStorage`. |
| **Sistema agéntico** | **Aplica, controlado.**<br>El WebSocket del chat y el SSE exigen el mismo JWT. El chat lo recibe por el primer frame o por `?token=`, y lo rechaza en el handshake si no vale (`services/agent/chat/router.py:141`). El SSE de RFP corta el stream cuando caduca el token y limita a 5 streams por usuario (`routes/rfp.py:328`).<br>Los aprobadores HITL quedan ligados a cuentas (A01).<br>**Servidor (SRV-01): corregido.** |

### A08 — Software and Data Integrity Failures

| Carril | Evaluación |
| --- | --- |
| Backend | **Aplica, controlado.** CI instala con `uv sync --locked` (lockfile con hashes). El scheduler verifica supercronic por SHA1 (`services/scheduler.Dockerfile`). **Baja:** las GitHub Actions están fijadas por etiqueta y no por SHA (plan: fijar por SHA). |
| Frontend | **Aplica, controlado.** `npm ci` con `package-lock.json`; no hay scripts de terceros por CDN (la CSP lo impide). |
| **Sistema agéntico** | **Aplica, controlado.** La integridad del contenido recuperado (RAG) se trata como dato no confiable (A03). Los checkpoints de LangGraph se serializan sin pickle (`pickle_fallback=False`). El documento final consolida **exactamente** el texto aprobado por cada humano, sin que un modelo lo reescriba después (`final_document.py`). |

### A09 — Security Logging and Monitoring Failures

| Carril | Evaluación |
| --- | --- |
| Backend | **Aplica.** Se registran los logins fallidos y bloqueados (`api.security`, con hash del usuario), cada petición con `request_id` y las aprobaciones rechazadas (`rfp_approval refused`, con el id de la cuenta). **APP-09 (Media, abierta):** no hay alertas sobre estos eventos. Plan: enviar `api.security` y fail2ban a una alerta (umbral de 429 por minuto o un baneo). |
| Frontend | **Aplica.** Telemetría de eventos de frontend sin PII, incluido el fallo de login (`auth_login_failed`). |
| **Sistema agéntico** | **Aplica, controlado.** Cada nodo de los grafos RFP queda registrado con agente, input y output resumidos y timestamps (`rfp_trace`). Cada llamada MCP queda en una línea de auditoría con herramienta, cliente, scopes y resultado (`mcps/healthcore_mcp/audit.py:28`). Los guardrails quedan con su tipo de fallo (`services/agent/harness/events.py:49`). Todo **sin contenido clínico**, como pide el BRIEFING §1: se registra *qué* ocurrió, no el texto del paciente. |

### A10 — Server-Side Request Forgery (SSRF)

| Carril | Evaluación |
| --- | --- |
| Backend | **No aplica.** Ningún endpoint descarga una URL elegida por el usuario. Los proxies y clientes tienen destinos fijos de configuración (`SUPPLIERS_API_URL`, URL de MCP, gateway LLM, Qdrant). Los PDF se procesan en local. |
| Frontend | **No aplica.** El BFF reenvía solo a `apiUrl(<prefijo fijo>)` (`src/lib/api-proxy.ts`), con `redirect: "manual"`. El usuario no controla el host. |
| **Sistema agéntico** | **No aplica.** Las herramientas del agente son un servidor MCP fijo con operaciones tipadas (`ticket_id`, criterios), no URLs. El RAG solo consulta Qdrant. El modelo no puede pedir ninguna URL. |

## 5. Correcciones críticas: antes y después (reproducible)

| # | Corrección | Antes | Después | Cómo reproducir |
| --- | --- | --- | --- | --- |
| 1 | **SSH sin root ni contraseñas + firewall** (SRV-01, SRV-02) | root entra; el servidor ofrece `password`; 192.555 intentos fallidos registrados; un proceso en `0.0.0.0:9999` es accesible desde Internet | `root` → `Permission denied (publickey)`; solo se ofrece `publickey`; solo el 22 abierto, 9999 cerrado; `verify.sh` 19/19 PASS | `sudo bash verify.sh` en el servidor y los comandos de `infra/hardening/README.md` |
| 2 | **Aprobación RFP ligada a la cuenta** (APP-01) | una cuenta cualquiera firma Compliance: `202 Accepted` | `403`; solo la cuenta asignada firma y queda en `submitted_by` | `uv run pytest services/api/tests/test_rfp.py -k "mapped_account or configured_approver"` |
| 3 | **Límite de intentos de login** (APP-02) | 8 contraseñas erróneas → 8 × `401` | 5 × `401`, después `429` con `Retry-After: 900` | `uv run pytest services/api/tests/test_auth.py -k throttled` o el bucle `curl` de la evidencia |

Las correcciones de severidad alta y media (APP-03 a APP-07) tienen su antes/después en `docs/security/evidence/`.

## 6. Checklist de PHI en la auditoría

- [x] Ningún log, evento ni output de las pruebas contiene PHI: toda la evidencia se pasó por `find_phi` y devolvió `[]`.
- [x] El log de seguridad del login guarda un hash del usuario, no el nombre ni la contraseña.
- [x] Los tests de inyección se citan por nombre; sus mensajes no se copian a la evidencia.
- [x] La IP pública del servidor y la del administrador no se publican (`VPS_HOST`, `ADMIN_IP`).

## 7. Cómo verificar todo

```bash
uv run python -m pytest tests services          # 648 passed, 18 skipped
node --test tests/realtime/*.mts
uvx pip-audit -r <(uv export --frozen --no-hashes --no-dev)   # 0 vulnerabilidades
cd uis/backoffice && npm audit --omit=dev                     # 0 vulnerabilidades
ssh deploy@VPS_HOST 'sudo bash ~/verify.sh'                   # 19/19 PASS
```
