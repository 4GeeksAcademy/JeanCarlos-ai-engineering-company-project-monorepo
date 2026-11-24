# Proyecto de Compañía - Ingeniería de IA — Plantilla para estudiantes

[![4Geeks Academy](https://img.shields.io/badge/4Geeks-Academy-blue)](https://4geeksacademy.com)
[![AI Engineering](https://img.shields.io/badge/track-AI%20Engineering-green)](https://4geeksacademy.com/es/programas-de-carrera/ingenieria-ia)

_Plantilla base para proyectos transversales del Programa de Carrera en Ingeniería de IA — 4Geeks Academy._

_These instructions are also available in [English](./README.md)._

---

## Propósito

Este repositorio es el workspace activo de HealthCore. El hito actual es **Talent Pipeline Tracker**, una app interna de People & Talent conectada a la API de Talent Tracker.

- Aplicación: [`uis/talent-pipeline-tracker/`](./uis/talent-pipeline-tracker/)
- Contexto de empresa y API: [`CONTEXT.md`](./CONTEXT.md)
- Especificación funcional: [`SPEC.md`](./SPEC.md)

Para iniciar: `cd uis/talent-pipeline-tracker && npm install && npm run dev`. Configura `NEXT_PUBLIC_API_URL` en el `.env.local` de la app.

---

## Estructura del repositorio

```text
ai-engineering-company-project-monorepo/
├── README.md
├── README.es.md
├── CONTEXT.md                # Placeholder a reemplazar con el contexto asignado
├── agents/                   # Patrones/plantillas de agentes y documentación de tools
├── data/                     # raw, process, pipelines, eval
├── docs/                     # Documentación de proyecto y arquitectura
├── infra/                    # Docker, Terraform, configuraciones de despliegue
├── internal/                 # CLIs, scripts de migración empaquetados, utilidades internas
├── mcps/                     # Servidores Model Context Protocol (MCP)
├── packages/
│   └── shared/               # Paquete compartido (@repo/shared-types)
├── scripts/                  # Convenciones/documentación de scripts
├── services/                 # APIs y workers en segundo plano
├── shared/                   # Recursos/convenciones compartidas a nivel repo
├── skills/                   # Skills reutilizables para agentes
├── uis/                      # Interfaces de usuario (React, Next.js, Streamlit, HTML)
└── workflows/                # Documentación de automatizaciones/orquestación
```

---

## Cómo empezar

1. Abre `uis/talent-pipeline-tracker/` como aplicación activa.
2. Configura `NEXT_PUBLIC_API_URL=https://playground.4geeks.com/tracker/api/v1` en `.env.local`.
3. Ejecuta `npm install` y `npm run dev` desde la carpeta de la app.
4. Valida cambios con `npm run lint` y `npm run build`.

---

## Worker en segundo plano (Celery, ticket DEV-55)

Las operaciones pesadas corren en un **worker de Celery**, un proceso aparte de la API
FastAPI, con Redis como broker y como backend de resultados. La API solo encola y responde
`202` con un `task_id`. `GET /tasks/{task_id}` informa del estado: `pending`, `started`,
`retry`, `success` o `failure`. Detalles en
[`docs/operations/async-tasks.md`](./docs/operations/async-tasks.md).

**Levantarlo** (Redis, worker y Flower; credenciales en `services/api/.env`):

```bash
COMPOSE_ENV_FILE=services/api/.env docker compose up -d redis worker flower
open http://localhost:5555                       # Flower: colas, tareas, reintentos
```

Sin Docker (con Redis ya en `localhost:6379`):

```bash
export REDIS_URL=redis://localhost:6379/0
celery -A services.celery_app worker -Q reporting --loglevel INFO      # worker
celery -A services.celery_app flower --port=5555                       # Flower
```

La API tiene que usar el mismo `REDIS_URL`.

**Detenerlo:**

```bash
docker compose stop worker      # apagado en caliente: termina las tareas en curso (hasta 120 s)
docker compose down             # todo; los mensajes en cola quedan en el volumen redis_data
```

Sin Docker, `Ctrl-C` una vez hace un apagado en caliente y dos veces uno en frío. Las
tareas sin ACK vuelven a la cola (`task_acks_late`).

Detener la API no detiene el worker ni pierde los mensajes en cola. Detener el worker deja
los mensajes esperando en Redis hasta que vuelva a arrancar.

---

## Base de conocimiento RAG (Hito 7)

Asistente para los coordinadores de pacientes: responde preguntas sobre seguros, citas, referencias y pacientes nuevos solo con los documentos de `docs/company-knowledge-base/`. El diseño completo está en [`docs/rag/rag-design.md`](docs/rag/rag-design.md).

```bash
docker compose up -d qdrant        # Qdrant en localhost:6333
python -m data.process.rag         # indexa los documentos en la colección healthcore_knowledge
python data/eval/evaluate_rag_retrieval.py   # Recall@3
```

Necesita `LLM_API_URL`, `LLM_API_KEY`, `EMBEDDING_MODEL` y `GENERATION_MODEL` (modelos distintos, del gateway de 4Geeks) en `services/api/.env`. Endpoint: `POST /knowledge/query`; UI: «Asistente» en el backoffice (`/knowledge`).

## Agente de soporte con LangGraph (parte 1)

El flujo RAG como grafo explícito (`services/agent/`): estado mínimo, nodos `receive_question → retrieve_context → generate_answer` (o `answer_no_information` / `reject_question`), checkpoints en SQLite y una traza consultable de cada corrida. Diseño: [`docs/agent/agent-design.md`](docs/agent/agent-design.md).

```bash
python -m services.agent.record_evals              # graba una corrida real por caso de eval
python -m pytest tests/pipelines/test_agent_evals.py   # evals contra las trazas grabadas
```

Endpoints: `POST /agent/query` y `GET /agent/runs/{run_id}/trace` (administradores).

## Agente con tools fuera del RAG (parte 2)

El agente decide solo si una pregunta necesita la base de conocimiento, el gestor de incidentes (`GET /api/incidents`) o el inventario (`GET /inventory/products`): tools de solo lectura con contrato tipado, timeout de 4 s y fallback honesto. Diseño: [`docs/agent/agent-design.md`](docs/agent/agent-design.md) §8.

## Servidor MCP con OAuth (`mcps/healthcore_mcp`)

Expone los tickets del Incidents Manager (consultar, crear y cambiar de estado vía `PATCH /api/incidents/{id}/status`) y el inventario en solo lectura a cualquier cliente MCP autorizado: Streamable HTTP, OAuth 2.1 con MCP Auth (Keycloak en local), scopes por tool y auditoría de cada invocación. El agente LangGraph lo consume como cliente MCP. Detalle: [`mcps/healthcore_mcp/README.md`](mcps/healthcore_mcp/README.md).

## Memoria del agente (Hito 8, parte 1)

El agente propone en la misma conversación recordar correcciones operativas, patrones de incidentes y preferencias del staff; solo guarda lo que el usuario aprueba explícitamente, nunca PHI (HIPAA + UK GDPR), con registro auditable y limpieza automática. Diseño y evidencia: [`docs/agent/memory/memory-design.md`](docs/agent/memory/memory-design.md).

## Guía de carpetas — qué va en cada una

Lee el `README.md` enlazado dentro de cada carpeta antes de empezar a programar ahí.

### Archivos en la raíz

| Ruta                         | Propósito                                                            | Qué haces aquí                                                                                               |
| ---------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| [`CONTEXT.md`](./CONTEXT.md) | Contexto de HealthCore y del hito Talent Pipeline Tracker | Contrato del API, alcance y restricciones funcionales |
| `docker-compose.yml`         | Orquestación local de todo el stack                                  | Mantener en la raíz del repo — conecta `services/`, bases de datos y otros contenedores desde un solo lugar  |
| `README.md` / `README.es.md` | Esta guía                                                            | Orientación — estás aquí                                                                                     |

### `uis/` — interfaces de usuario

**Propósito:** Todas las aplicaciones frontend — todo lo que un humano ve y en lo que hace clic.

**Pon aquí:**

- Sitio web público (`website/`)
- Admin interno / backoffice (`backoffice/`)
- Portales de clientes, apps de fidelización, herramientas Streamlit/Gradio, dashboards con UI

**Ejemplos:** landing corporativa, backoffice de operaciones, portal de fidelización, UI de dashboard de telemetría

→ Ver [`uis/README.md`](./uis/README.md)

### `services/` — API centralizada de la empresa (FastAPI)

**Propósito:** Un **backend FastAPI centralizado** para toda la empresa — un solo punto de entrada que reduce la complejidad a medida que crece el proyecto.

**Pon aquí:**

- Una app FastAPI principal (p. ej. `api/`) con routers/módulos por dominio (ubicaciones, menús, ventas, telemetría, etc.)
- Workers en background solo cuando de verdad necesiten correr separados de la API

**Recomendación:** evita dividir en muchos microservicios al inicio. Añade endpoints a la misma app FastAPI; extrae un worker solo cuando sea necesario.

**Ejemplos:** `/locations`, `/menus`, `/sales/reports`, webhooks, jobs programados

→ Ver [`services/README.md`](./services/README.md)

### `data/` — datasets, pipelines y evaluación

**Propósito:** Todo lo relacionado con datos, desde archivos crudos hasta tablas listas para producción.

| Subcarpeta                                      | Propósito                     | Qué haces aquí                                                                  |
| ----------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------- |
| [`data/raw/`](./data/raw/README.md)             | Datos fuente sin tocar        | Guardar dumps, exports, CSV/JSON de ejemplo — documentar origen y reglas de PII |
| [`data/pipelines/`](./data/pipelines/README.md) | Jobs ETL/ELT                  | Escribir scripts de ingesta, limpieza y transformación                          |
| [`data/process/`](./data/process/README.md)     | Salidas limpias / intermedias | Guardar artefactos de pipelines (features, agregados, tablas limpias)           |
| [`data/eval/`](./data/eval/README.md)           | Medición de calidad           | Golden sets, datasets de evaluación RAG/agentes, métricas de experimentos       |

**Flujo:** `raw` → `pipelines` → `process` → consumido por `services/`, `uis/` o `agents/`. Usa `eval` para demostrar calidad.

### `agents/` — agentes de IA

**Propósito:** Asistentes de IA autónomos o semi-autónomos para la empresa.

**Pon aquí:**

- Una subcarpeta por agente (p. ej. `support-agent/`, `onboarding-agent/`)
- Config del agente, prompts, herramientas, tests
- Empieza desde [`agents/_template/`](./agents/_template/README.md) al crear un agente nuevo

**Ejemplos:** bot de soporte al cliente, copiloto de onboarding, asistente de formación

→ Ver [`agents/README.md`](./agents/README.md)

### `skills/` — capacidades reutilizables para agentes

**Propósito:** Instrucciones empaquetadas + scripts que agentes (o tú en Cursor) reutilizan en todo el repo.

**Pon aquí:**

- Skills de análisis de datos, code review, scraping, investigación, etc.
- Cada skill = carpeta con `SKILL.md`, scripts y recursos opcionales

**Ejemplo incluido:** `skills/data-analysis/` (script de limpieza pandas + referencia de métricas)

→ Ver [`skills/README.md`](./skills/README.md)

### `mcps/` — servidores Model Context Protocol

**Propósito:** Conectar modelos de IA con tus sistemas — bases de datos, APIs, GitHub, herramientas propias.

**Pon aquí:**

- Una subcarpeta por servidor MCP (p. ej. `database-mcp/`, `github-mcp/`)
- Definiciones de tools, resources y config del servidor

**Cuándo usarlo:** cuando un agente necesita acceso en vivo a datos o acciones que el código solo no puede dar

→ Ver [`mcps/README.md`](./mcps/README.md)

### `workflows/` — automatización y orquestación

**Propósito:** Conectar sistemas sin escribir apps completas — jobs programados, webhooks, notificaciones.

**Pon aquí:**

- Exports de workflows n8n, configs de Make/Zapier u orquestación documentada
- Flujos que enlazan `services/`, `data/pipelines/` y `agents/`

**Ejemplos:** nuevo pedido → alerta Slack, trigger ETL nocturno, lead → sync CRM

→ Ver [`workflows/README.md`](./workflows/README.md)

### `packages/` — librerías compartidas

**Propósito:** Código versionable reutilizado por varias apps, agentes o pipelines.

**Pon aquí:**

- Tipos TypeScript compartidos (`packages/shared/` → `@repo/shared-types`)
- Librerías de componentes UI, clientes API, SDKs de analytics

**Regla:** si `uis/` y `services/` comparten la misma interfaz → extráela aquí

→ Ver [`packages/README.md`](./packages/README.md)

### `shared/` — recursos sueltos compartidos

**Propósito:** Recursos que no son un paquete completo — esquemas, plantillas, assets estáticos, docs cortas.

**Pon aquí:**

- Esquemas JSON, plantillas de email, specs OpenAPI, design tokens
- Cualquier cosa reutilizada pero demasiado pequeña o no-código para `packages/`

→ Ver [`shared/README.md`](./shared/README.md)

### `docs/` — documentación transversal

**Propósito:** Arquitectura y decisiones que abarcan todo el proyecto de la empresa.

**Pon aquí:**

- Diagramas de arquitectura, ADRs, guías de seguridad/observabilidad
- Convenciones no atadas a una sola app o agente

→ Ver [`docs/README.md`](./docs/README.md)

### `infra/` — infraestructura y despliegue

**Propósito:** Cómo corre el proyecto en Docker, cloud o CI.

**Pon aquí:**

- Dockerfiles, Terraform, manifiestos K8s, configs Nginx, pipelines CI/CD

**Mantener en la raíz del repo:** `docker-compose.yml` — orquesta el entorno local de `services/`, bases de datos y otros contenedores desde un solo lugar.

→ Ver [`infra/README.md`](./infra/README.md)

### `scripts/` — scripts de ayuda

**Propósito:** Automatización pequeña y repetible — no apps completas.

**Pon aquí:**

- Scripts de setup, generadores de seed data, wrappers de lint, migraciones puntuales
- Documenta cada script: qué hace, argumentos y cómo ejecutarlo

**Diferencia con `internal/`:** los scripts suelen ser archivos sueltos; las tools de `internal/` son proyectos estructurados con deps y tests propios.

→ Ver [`scripts/README.md`](./scripts/README.md)

### `internal/` — herramientas internas para desarrolladores

**Propósito:** Utilidades robustas para el equipo de ingeniería.

**Pon aquí:**

- CLIs, herramientas de migración empaquetadas, evaluadores de prompts
- Tools con su propio `package.json`, tests y pasos de instalación

→ Ver [`internal/README.md`](./internal/README.md)

---

## ¿Dónde pongo esto?

Guía rápida de decisión:

```text
¿Tiene botones y pantallas?                → uis/
¿Corre en servidor / API / cola?           → services/
¿Es dato crudo o transformado?             → data/raw/ o data/process/
¿Mueve datos entre sistemas?               → data/pipelines/
¿Mides calidad de IA/pipelines?            → data/eval/
¿Es un asistente de IA con un objetivo?    → agents/
¿Es una capacidad/instrucción reutilizable?→ skills/
¿La IA necesita llamar tools/APIs externas?→ mcps/
¿Es n8n / automatización programada?       → workflows/
¿2+ carpetas importan el mismo código?     → packages/
¿Es esquema/plantilla/asset, no librería?  → shared/
¿Es arquitectura o docs de todo el equipo? → docs/
¿Es docker-compose para dev local?         → raíz del repo
¿Es Docker / deploy / config cloud?        → infra/
¿Es un script puntual?                     → scripts/
¿Es una CLI con su propio paquete?         → internal/
```

---

## Estructura del repositorio (árbol)

```text
ai-engineering-company-project-monorepo/
├── README.md / README.es.md   # Esta guía
├── CONTEXT.md                 # ← Reemplazar con el briefing de tu empresa
├── docker-compose.yml         # ← Orquestación local (raíz del repo)
├── uis/                       # Frontends (website, backoffice, dashboards)
├── services/                  # API FastAPI centralizada de la empresa
├── data/
│   ├── raw/                   # Datasets fuente
│   ├── pipelines/             # Jobs ETL/ELT
│   ├── process/               # Salidas limpias / intermedias
│   └── eval/                  # Conjuntos de evaluación y métricas
├── agents/                    # Agentes de IA (+ plantilla _template/)
├── skills/                    # Skills reutilizables para agentes
├── mcps/                      # Servidores MCP para acceso a tools
├── workflows/                 # Flujos n8n y automatizaciones
├── packages/                  # Librerías compartidas (@repo/shared-types, …)
├── shared/                    # Esquemas, plantillas, assets sueltos
├── docs/                      # Arquitectura y docs transversales
├── infra/                     # Docker, Terraform, despliegue
├── scripts/                   # Scripts de ayuda
└── internal/                  # CLIs y herramientas internas de desarrollo
```

---

## Enlaces

- [4Geeks Academy — Ingeniería de IA](https://4geeksacademy.com/es/programas-de-carrera/ingenieria-ia)
- [Cómo empezar un proyecto de código](https://4geeks.com/lesson/how-to-start-a-project)

---

## Contribuidores

Esta plantilla fue creada como parte del Programa de Carrera de Ingeniería de IA de 4Geeks Academy por [@marcogonzalo](https://www.linkedin.com/in/marcogonzalo) y [@alezanchezr](https://x.com/alesanchezr), junto a otros muchos colaboradores. Descubre más sobre nuestro [Curso de Ingeniería de IA](https://4geeksacademy.com/es/programas-de-carrera/ingenieria-ia) y sobre [otros cursos](https://4geeksacademy.com/es/comparar-programas).

Puedes encontrar otras plantillas y recursos similares en la [página de GitHub de 4Geeks Academy](https://github.com/4geeksacademy).

_Esta plantilla la mantiene 4Geeks Academy para el track de Ingeniería de IA. Uso exclusivo del programa._
