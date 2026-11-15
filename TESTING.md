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
