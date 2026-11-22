# Job nocturno de telemetría (`nightly_export`, ticket DEV-53)

Cada noche, sin intervención manual:

1. Exporta a CSV los `telemetry_events` del día anterior, si todavía no existe el
   archivo.
2. Lanza el pipeline de negocio.
3. Registra en `reporting.job_runs` qué pasó y cuándo.

| Pieza | Archivo |
| --- | --- |
| Script (proceso independiente) | [`scripts/nightly_export.py`](../../scripts/nightly_export.py) |
| Control de estado | [`services/job_runner.py`](../../services/job_runner.py) |
| Tabla | [`supabase/migrations/20261122160000_reporting_job_runs.sql`](../../supabase/migrations/20261122160000_reporting_job_runs.sql) |
| Disparador | [`scripts/nightly_export.crontab`](../../scripts/nightly_export.crontab), contenedor `scheduler` ([`services/scheduler.Dockerfile`](../../services/scheduler.Dockerfile), `docker-compose.yml`) |
| Tests | [`tests/scripts/test_nightly_export.py`](../../tests/scripts/test_nightly_export.py) |

## Ejecución

```bash
python scripts/nightly_export.py                         # ayer (día calendario anterior en UTC)
TARGET_DATE=2026-11-21 python scripts/nightly_export.py  # cualquier día cerrado, sin tocar código

docker compose up -d scheduler                           # disparador nocturno
docker compose run --rm -e TARGET_DATE=2026-11-21 scheduler python scripts/nightly_export.py
```

| Variable | Uso |
| --- | --- |
| `REPORTING_PIPELINE_DATABASE_URL` | Conexión del rol `reporting_pipeline`: lee `telemetry_events` y escribe en `reporting.*`. |
| `TARGET_DATE` | Opcional, `YYYY-MM-DD`. Tiene que ser un día ya cerrado (anterior a hoy en UTC). |
| `NIGHTLY_PIPELINE_TIMEOUT_SECONDS` | Opcional, por defecto `1800`. Pasado ese tiempo, el subproceso del pipeline se mata y el job queda `failed`. |
| `NIGHTLY_STALE_AFTER_MINUTES` | Opcional, por defecto `120`. Una fila `processing` más antigua que esto viene de un proceso matado con `SIGKILL` y se marca `failed`. |

Códigos de salida:

- `0`: el job terminó `completed`, o se omitió en silencio (lock o día ya hecho).
- `1`: terminó `failed`.
- `2`: `TARGET_DATE` no es válido.

## Máquina de estados

```text
pending ──► processing ──► completed
                      └──► failed
(skipped: registro terminal de una ejecución que abortó en silencio; nunca pasa por processing)
```

| Paso | Qué hace |
| --- | --- |
| `pending` | Se crea la fila antes de cualquier trabajo. |
| `processing` | Se marca antes de exportar. **Es el lock distribuido:** el índice único parcial `job_runs_one_processing_idx` admite una sola fila `processing` por `job_name`, así que la transición es atómica entre procesos y máquinas. No hay otra tabla, columna ni flag de bloqueo. |
| `completed` | Solo si el CSV y el pipeline terminaron bien. Guarda en `details` la ruta del CSV, las filas exportadas, el código de salida y el `pipeline_run_id`. |
| `failed` | Cualquier excepción, `SIGTERM` o `Ctrl-C` lleva la fila a `failed` con el mensaje (`try/except/finally`). El `finally` comprueba además que la fila no quedó `pending` ni `processing`. |

Una ejecución termina como `skipped`, y no hace nada más, en dos casos:

- **Lock:** ya hay una fila `processing` de `nightly_export`, o otra instancia
  ganó la carrera hacia `processing`. Queda `skipped` con
  `skip_reason = locked`.
- **Idempotencia:** ya existe una fila `completed` para
  `(nightly_export, target_date)`. Queda `skipped` con
  `skip_reason = already_completed`: no reexporta el CSV ni relanza el
  pipeline. El índice único parcial `job_runs_one_completed_per_day_idx`
  garantiza un solo `completed` por día.

Un día `failed` no cuenta como hecho, así que la siguiente ejecución lo reintenta:

- reutiliza el CSV si ya existía;
- vuelve a lanzar el pipeline, que es idempotente por su upsert.

### `job_runs` frente a `pipeline_runs`

| Tabla | Capa | Registra |
| --- | --- | --- |
| `reporting.job_runs` | Orquestación nocturna | Export a CSV, lanzamiento del subproceso, lock e idempotencia por día. |
| `reporting.pipeline_runs` | ETL (Hito 6) | Lo escribe el propio pipeline: fases, eventos leídos, filas cambiadas. |

`job_runs.details.pipeline_run_id` enlaza las dos tablas sin duplicar métricas.

## CSV de respaldo

- **Ruta:** `data/raw/telemetry_YYYY-MM-DD.csv`.
- **Contenido:** los eventos con `timestamp` en `[target_date, target_date + 1 día)`
  UTC, ordenados por `timestamp` e `id`.
- **Columnas:** `id`, `timestamp`, `event_type` y `tags` (JSON). Los
  identificadores seudónimos (`session_id`, `user_id`, `request_id`) no se
  escriben en disco; el rol del pipeline tampoco puede leerlos.
- **Escritura atómica:** se escribe en un archivo `.partial` y se renombra, así
  que una caída nunca deja un CSV a medias que la siguiente corrida tome por
  terminado.
- **No se sobrescribe nunca.**
- **Es respaldo y auditoría, no entrada:** el pipeline lee `telemetry_events` de
  la base de datos.
- **Fuera de git:** los CSV se ignoran, salvo
  `data/raw/telemetry_2026-11-21.csv`, que se versiona como ejemplo del formato.

## Disparador

- **Expresión:** `0 5 * * *`, todos los días a las 05:00 UTC, en el contenedor
  dedicado `scheduler` con [supercronic](https://github.com/aptible/supercronic)
  v0.2.50 (binario verificado con SHA1).
- **Por qué un contenedor dedicado y no el crontab del sistema:**
  - viaja con el repositorio (`docker compose up -d scheduler`) y no depende de
    configurar cada servidor;
  - corre en UTC fijo, igual que `target_date`;
  - pasa las variables de entorno al job, cosa que `cron` no hace;
  - escribe los logs en stdout (`docker compose logs scheduler`);
  - reenvía `SIGTERM` al job al parar el contenedor, y el job lo convierte en
    `failed`.
- **Por qué no el scheduler del framework:** ni APScheduler ni hooks en el
  `lifespan` de FastAPI. El job nunca comparte proceso ni hilo con la API; el
  test `test_script_is_independent_of_the_api` comprueba que el script no carga
  `fastapi`.
- **Por qué a las 05:00 UTC:**
  - «ayer» ya está cerrado en UTC, y los beacons y reintentos de la noche
    llevan 5 h de margen para llegar;
  - es la misma franja que el pipeline tenía prevista
    (`data/pipelines/PIPELINE_DESIGN.md` §7);
  - el reporte del mes está al día antes de la jornada en Reino Unido.

## Logs

Cada línea lleva el timestamp UTC, el job, la fecha objetivo y el estado
resultante. Los eventos normales (inicio, fin, omisiones) van en `INFO` y las
excepciones en `ERROR`, con traza:

```text
2026-11-22T14:47:03Z INFO job=nightly_export target_date=2026-11-21 status=processing started (job_run=1)
2026-11-22T14:47:50Z ERROR job=nightly_export target_date=2026-11-19 status=failed PipelineFailed: pipeline did not finish within 2 s (job_run=5)
```
