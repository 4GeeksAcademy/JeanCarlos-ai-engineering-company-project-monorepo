# Carpeta `uis`

Esta carpeta contiene **todos los proyectos con interfaz de usuario** para el proyecto transversal de AI Engineering de la compañía — por ejemplo: un sitio web público, un frontend de panel de administración, una interfaz de ecommerce, portales para clientes, aplicaciones Streamlit/Gradio u otras herramientas sólo-frontend.

Los dos proyectos principales que se almacenan aquí son:

- **`website`** — la presencia web pública de la compañía.
- **`backoffice`** — la aplicación interna de administración. Es el lugar ideal para desarrollar múltiples soluciones dentro de un mismo proyecto: autenticación, gestión de personas, gestión de operaciones, comunicación interna y otras capacidades de back-office.

Organiza `uis/` por **distintas áreas de la compañía** — cada subcarpeta agrupa un ámbito diferente (por ejemplo, web pública frente a operaciones internas) e incluye su propia documentación técnica y funcional.

- **Propósito principal**: centralizar en un único lugar todas las aplicaciones frontend que dan soporte a los casos de uso de la compañía.
- **Recomendación**: documenta en este archivo (o en sub-READMEs) las aplicaciones que vayas añadiendo, su objetivo, tecnología usada y cómo ejecutarlas.

> _These instructions are also available in [English](./README.md)._

## HealthCore Backoffice — Supplier Directory

Aplicación independiente en `backoffice/`, sin cambios en Talent Pipeline Tracker.
Requiere Node.js 20.9 o posterior y la API de proveedores en ejecución
(ver [instrucciones de backend](../services/README.es.md#healthcore-supplier-directory)).

```bash
cd uis/backoffice
npm install
cp .env.example .env.local
npm run dev
```

Abrir <http://localhost:3001/login>. Permite filtrar por país/categoría,
registrar proveedores, editar tarifas y activar/suspender cada fila sin recargar
la página. Los estados de carga, éxito y error son visibles.

`SUPPLIERS_API_URL` configura el destino del proxy Next.js (por defecto
`http://127.0.0.1:8000`); no es una variable pública del navegador.
Las peticiones pasan por rutas servidoras del mismo origen, sin necesitar CORS.
El token se almacena en `localStorage` con la clave `healthcore_access_token`.
Cada petición protegida lo lee y lo envía en `Authorization: Bearer <token>`;
el proxy lo reenvía únicamente a la API de HealthCore. En producción se requiere
HTTPS y deben evitarse scripts no confiables: un XSS puede acceder a localStorage.
El proxy de despliegue debe validar el host público y sobrescribir las cabeceras
`Host` y `X-Forwarded-Proto` con los valores confiables de la petición.
El enlace de recuperación usa Resend y requiere la configuración descrita en la
[guía de autenticación](../services/api/README.md#password-recovery).
Configurar la variable antes de `npm run build` y reconstruir si cambia el destino.

### Autenticación frontend (AUTH-02)

Backoffice y Talent Pipeline Tracker comparten formularios y gestión de sesión
mediante el paquete local `packages/auth`; no es una aplicación independiente.
Ejecuta `npm install` en cada aplicación tras actualizar esta rama. Sus archivos
`.npmrc` hacen que npm copie el paquete local; vuelve a instalarlo después de
editar el código compartido y reinicia el servidor de desarrollo.

Ambas aplicaciones ofrecen `/login`, `/register` y `/account/profile`. El login
usa email y contraseña. El registro crea una cuenta de rol `user` y su perfil,
inicia sesión y guarda el token. Los errores de validación se muestran junto a
los campos. Los administradores existentes necesitan tener email asignado para
usar el login frontend; el login por usuario sigue disponible en Swagger.

Un guard cliente valida la sesión con `/auth/me` antes de mostrar las vistas
internas. En backoffice protege proveedores, inventario y cuenta; en el tracker,
el listado, los detalles de candidaturas y cuenta. Login y registro son públicos;
recuperar y restablecer contraseña siguen siendo públicos en backoffice.
Logout y cualquier respuesta `401` de una llamada protegida borran el token y
redirigen al login. Cada origen/puerto mantiene su propio localStorage, por lo
que hay que iniciar sesión en cada aplicación.

`AUTH_API_URL` permite definir la API de autenticación en el servidor de ambas
aplicaciones. Si no está definida, se usa `SUPPLIERS_API_URL` o
`http://127.0.0.1:8000`. El tracker valida el token en HealthCore antes de consultar
la API externa de candidaturas y nunca reenvía el JWT a ese proveedor.
El website público no tiene guards ni cambios de autenticación.

Comprobación manual: abre una ruta interna sin token, registra una cuenta,
comprueba el token en las herramientas del navegador, edita y recarga el perfil,
cierra sesión y prueba de nuevo una ruta interna. Para comprobar un `401`,
sustituye el token por un valor inválido y navega a una vista protegida.
No compartas capturas que incluyan el token.

Validación desde `uis/backoffice/`:

```bash
npm run lint
npm run build
```

## Desarrollo con Docker Compose

El Compose de la raíz ejecuta Talent Pipeline Tracker en el puerto 3000, el
backoffice en el 3001 y la API en el 8000. `uis/website` aún no existe. Es una
configuración de desarrollo con recarga automática, no una imagen de producción.

1. Instala Docker y Docker Compose. En la raíz, crea `.env` con
   `JWT_SECRET_KEY` (mínimo 32 bytes aleatorios), `JWT_ALGORITHM=HS256` y
   `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (entero positivo). Consulta las variables
   adicionales de recuperación de contraseña y PostgreSQL en la
   [guía de la API](../services/api/README.md). No compartas ni subas `.env`.
   Opcionalmente, `NEXT_PUBLIC_API_URL` cambia el destino del tracker; por
   defecto usa la API pública del tracker, no esta API de proveedores.
2. Desde la raíz:

   ```bash
   docker compose up --build -d
   docker compose logs -f
   ```

3. Abre <http://localhost:3000>, <http://localhost:3001/login> o
   <http://localhost:8000/docs>. El backoffice usa `http://api:8000` desde la
   red interna; el navegador no necesita resolver ese nombre. Para crear el
   primer administrador, detén la API y ejecuta el asistente:

   ```bash
   docker compose stop api
   docker compose run --rm --no-deps api python -m services.api.bootstrap_admin
   docker compose start api
   ```

Los cambios en código se reflejan mediante bind mounts. Si cambian las
dependencias, reconstruye y actualiza los volúmenes de `node_modules`:

```bash
docker compose build
docker compose run --rm --no-deps ui sh -c 'cd /app/talent-pipeline-tracker && npm ci && cd /app/backoffice && npm ci'
docker compose up -d
```

`docker compose down` detiene los servicios sin borrar datos. El volumen
`api_data` conserva TinyDB; no uses `down -v` sin respaldarlo. TinyDB requiere
un único proceso de API y copias de seguridad; el inventario requiere una
instancia PostgreSQL externa configurada con `DATABASE_URL`.

### VPS por SSH

Instala Docker y el plugin Compose en el VPS, clona el repositorio y crea su
`.env` directamente en el servidor. Desde la carpeta del repositorio ejecuta
`docker compose up --build -d`. Los puertos solo escuchan en loopback; desde tu
equipo crea túneles para acceder sin publicarlos:

```bash
ssh -N -L 3000:127.0.0.1:3000 -L 3001:127.0.0.1:3001 -L 8000:127.0.0.1:8000 usuario@vps
```

Abre las mismas URL locales indicadas arriba. Para actualizar, entra por SSH,
actualiza el repositorio y vuelve a ejecutar `docker compose up --build -d`;
si cambiaron dependencias de Node, ejecuta también el comando de actualización
de `node_modules` indicado arriba. No expongas estos servidores de desarrollo
a Internet ni uses datos sensibles: un despliegue de producción necesita
imágenes y configuración de producción, HTTPS, controles de acceso, proxy
confiable, persistencia y respaldos apropiados.
