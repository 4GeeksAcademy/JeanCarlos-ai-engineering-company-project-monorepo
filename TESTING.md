# Pruebas de autenticación y usuarios

## Plan

La suite comprueba las reglas de negocio de los endpoints de autenticación,
usuarios y contraseñas: credenciales, roles, unicidad, persistencia, revocación
y recuperación de un solo uso. Cada endpoint tiene un caso normal, uno límite
y uno de fallo. Se usa `TestClient` para ejercitar las rutas y se comprueban
resultados de negocio y efectos en TinyDB, no internals de FastAPI ni detalles
de serialización.

Cada prueba usa una base TinyDB en `tmp_path` y una clave JWT de prueba.
Las fixtures no leen `.env`, limpian la configuración en caché y sustituyen el
envío de correo por una bandeja en memoria. Una prueba simula un fallo de
Resend; no se realizan envíos ni peticiones externas. La concurrencia del
reseteo se comprueba con un solo proceso, tal como requiere TinyDB.

## Ejecución

Desde la raíz, en un entorno virtual Python:

```bash
python -m pip install -r services/api/requirements-dev.txt
python -m pytest services/api/tests \
  --cov=services.api.routes.auth \
  --cov=services.api.routes.users \
  --cov=services.api.routes.auth_password \
  --cov=services.api.core.dependencies \
  --cov=services.api.core.security \
  --cov-report=term-missing --cov-fail-under=70
```

`--cov-fail-under` comprueba el total. Para comprobar también el 70% en cada
módulo del alcance, después de ejecutar ese comando:

```bash
python - <<'PY'
from coverage import Coverage

modules = [
    "services/api/routes/auth.py",
    "services/api/routes/users.py",
    "services/api/routes/auth_password.py",
    "services/api/core/dependencies.py",
    "services/api/core/security.py",
]
coverage = Coverage()
coverage.load()
failed = False
for module in modules:
    _, statements, _, missing, _ = coverage.analysis2(module)
    percent = 100 * (len(statements) - len(missing)) / len(statements)
    print(f"{module}: {percent:.1f}%")
    failed |= percent < 70
raise SystemExit(1 if failed else 0)
PY
```

## Matriz de casos

| Endpoint | Caso normal | Caso límite | Fallo |
| --- | --- | --- | --- |
| `POST /login` | Credenciales válidas | Usuario sin distinción de mayúsculas | Contraseña incorrecta, usuario inexistente o bcrypt >72 bytes |
| `GET /me` | Usuario autenticado | Rol actualizado después de emitir el token | Token caducado, inválido, de reseteo o usuario eliminado |
| `GET /users` | Admin lista usuarios | Solo existe el admin inicial | User y manager sin permiso |
| `POST /users` | Admin crea usuario con hash | Contraseña de 72 bytes y correo opcional | Duplicados y usuario sin permiso |
| `GET /users/{id}` | Admin consulta usuario | ID inexistente | Usuario sin permiso |
| `PATCH /users/{id}` | Cambia contraseña y revoca sesión | Correo sin cambios; último admin | Duplicado, ID inexistente y usuario sin permiso |
| `DELETE /users/{id}` | Elimina usuario y perfil; revoca sesión | Último admin protegido | ID inexistente y usuario sin permiso |
| `POST /auth/forgot-password` | Correo registrado recibe token simulado | Respuesta neutral para correo desconocido o inválido | Fallos de TinyDB o Resend sin revelar detalles |
| `POST /auth/reset-password` | Cambia hash y revoca sesión | Contraseña de 72 bytes; uso único concurrente | Token caducado, manipulado, de acceso o reutilizado |
| `POST /auth/change-password` | Valida contraseña actual y revoca sesión | Límite bcrypt y revocación de enlaces previos | Contraseña incorrecta, falta de sesión o >72 bytes |

El envío real de Resend, los proveedores, los perfiles como recurso independiente
y las interfaces no forman parte del alcance de esta suite.

## Utilidades TypeScript con Jest (raíz del monorepo)

Desde la raíz, independientemente de las aplicaciones Next.js:

```bash
npm install
npm run typecheck
npm test -- --runInBand
npm run test:coverage -- --runInBand
```

`jest.config.cjs` usa `ts-jest` y el entorno Node, busca las pruebas en
`tests/utils` e incluye las cuatro utilidades de `src/utils` en la medición de cobertura.
`tsconfig.json` comprueba utilidades, modelos y tests en modo estricto.
No se modifican las dependencias ni los comandos de las aplicaciones Next.js.
Esta entrega no incluye las actualizaciones locales de `package-lock.json`
ni `uv.lock`, restringidas por la política de acceso de la organización.
Usa `npm install` para sincronizar las dependencias de Jest declaradas en
`package.json`; `npm ci` requiere un archivo de bloqueo actualizado.
El informe navegable para herramientas compatibles con LCOV queda en
`coverage/lcov.info`; los resultados generados no se versionan.

Las pruebas usan datos sintéticos, sin peticiones externas ni información real
de pacientes:

| Suite | Comportamientos verificados |
| --- | --- |
| `search.test.ts` | Búsqueda lineal y binaria, primero/centro/último, ausentes y listas vacías |
| `collections.test.ts` | Filtros combinados, selección vacía, orden ascendente/descendente sin mutación y agrupación |
| `validations.test.ts` | Importes, fechas, clínicas, motivo de denegación, ID de paciente, horas CME y umbrales estrictos |
| `transformations.test.ts` | Tasas y redondeo, costes semanales, alertas, progreso CME y vencimientos de licencias |

Las validaciones dependientes de la fecha fijan el reloj al 15 de junio de 2026
y restauran los temporizadores tras cada caso.

## Caso límite identificado con ayuda de IA

Al revisar `calculateNoShowCost` con IA se identificó un caso que faltaba probar:
una semana inclusiva de siete días puede acabar contando ocho si el inicio se
calcula restando siete días en lugar de seis.

Se añadió el test
`counts only no-shows in the inclusive seven-day window at the requested clinic`
en `tests/utils/transformations.test.ts`:

- Semana con cierre el `2026-06-15`: incluye el `2026-06-09` y el `2026-06-15`.
- Excluye el `2026-06-08` y el `2026-06-16`, otra clínica, citas canceladas y
  citas completadas.
- Las dos ausencias válidas cuestan `100.25 + 200 = 300.25`.

El test pasa con la implementación existente; es un riesgo de regresión
identificado con IA, no un bug de producción confirmado ni una corrección
inventada. Restar siete días incluiría el día 8 y haría fallar esta prueba.

## Resultados de Jest

| Comprobación | Resultado |
| --- | --- |
| `npm run typecheck` | Sin errores |
| Jest de utilidades | 4 suites y 40/40 pruebas correctas |
| Cobertura Jest | 99.49% sentencias, 96.77% ramas, 100% funciones y 99.48% líneas |
