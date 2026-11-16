# Carpeta `services`

Esta carpeta contiene **todos los servicios backend** (APIs y workers en segundo plano) relacionados con la compañía para el proyecto transversal de AI Engineering.

Cada subcarpeta dentro de `services/` debe corresponder a **un servicio concreto** (por ejemplo `admin-api`, `data-processor-worker`) e incluir su propia documentación técnica y funcional.

- **Propósito principal**: centralizar toda la lógica backend, APIs y consumidores de colas que dan soporte a los casos de uso de la compañía.
- **Recomendación**: documenta en este archivo (o en sub-READMEs) los servicios que vayas añadiendo, su objetivo, tecnología usada y cómo ejecutarlos.

## HealthCore Supplier Directory

`api/` contiene la API FastAPI, Pydantic 2 y TinyDB del directorio.
Antes de arrancar la API, configura las variables JWT según
[la guía de autenticación](api/README.md). Desde la raíz del repositorio:

```bash
python -m venv services/api/.venv
services/api/.venv/bin/python -m pip install -r services/api/requirements.txt
services/api/.venv/bin/python -m services.api.seed
services/api/.venv/bin/python -m services.api.bootstrap_admin
services/api/.venv/bin/python -m uvicorn services.api.main:app --host 127.0.0.1 --port 8000
```

Documentación interactiva: <http://127.0.0.1:8000/docs>.
Las rutas de proveedores, inventario, usuarios y perfiles requieren un token JWT obtenido en `POST /auth/login` (ver `services/api/README.md`).
`SUPPLIERS_DB_PATH` permite cambiar el archivo persistente; por defecto se usa
`services/api/data/suppliers.json`. El seed inserta los 15 proveedores del briefing,
identificados por nombre y país; repetirlo no duplica ni sobrescribe registros.

Ejecutar el seed con la API detenida y usar **un solo proceso/worker**.
El bloqueo interno protege peticiones concurrentes dentro de ese proceso;
TinyDB no soporta múltiples procesos escribiendo el mismo archivo.
No usar `--workers` ni compartir el archivo con otra instancia.

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/suppliers?country=USA&category=medical_supplies` | Lista (ambos filtros opcionales, combinados con AND) |
| GET | `/suppliers/{id}` | Detalle |
| POST | `/suppliers` | Alta, 201 |
| PUT | `/suppliers/{id}` | Sustitución de todos los campos editables |
| PATCH | `/suppliers/{id}/rate` | Cambio de `monthly_rate` |
| PATCH | `/suppliers/{id}/status` | Cambio de `status` |

Las lecturas y actualizaciones devuelven 200, los IDs inexistentes 404 y las
entradas inválidas 422. Los IDs son enteros positivos generados por TinyDB.
No hay DELETE: se conserva el proveedor con `status=suspended`.
La respuesta incluye `id` y `updated_at` UTC; enviar estos campos en una escritura
se rechaza. El timestamp se genera al crear y al actualizar la tarifa; un cambio
de estado no lo modifica. PUT lo renueva solo si cambia la tarifa.
No se rechazan fechas de renovación pasadas, presentes en el briefing.
Los acuerdos de cumplimiento son opcionales, incluso para proveedores tecnológicos.

Este servicio es un ejercicio local, sin autenticación. No exponerlo públicamente
ni usarlo con información sensible sin controles de acceso, respaldo y una base
de datos adecuada para producción.
