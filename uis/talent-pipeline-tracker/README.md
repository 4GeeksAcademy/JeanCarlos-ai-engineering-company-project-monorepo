# Talent Pipeline Tracker

Frontend interno de People & Talent para revisar candidaturas, gestionar estado y etapa y mantener notas de selección.

## Desarrollo local

```bash
npm install
npm run dev
```

La app se abre en <http://localhost:3000>. Configura `NEXT_PUBLIC_API_URL` en `.env.local` con `https://playground.4geeks.com/tracker/api/v1`.

## Autenticación

Arranca también la API de HealthCore. `AUTH_API_URL` configura su URL solo en el
servidor (por defecto `http://127.0.0.1:8000`, o `SUPPLIERS_API_URL` si existe).
`/login` y `/register` son públicos; el listado, los detalles de candidaturas y
`/account/profile` requieren una sesión válida. El website público no se modifica.

El paquete local `packages/auth` comparte los formularios, perfil y guard cliente
con backoffice. El registro no permite elegir roles. Se guarda el JWT en
`localStorage` como `healthcore_access_token`; las llamadas protegidas lo envían
como Bearer al proxy del mismo origen. El proxy comprueba la sesión con HealthCore
y no envía ese JWT a la API pública de candidaturas. Un `401` o cerrar sesión
limpian el token y redirigen al login. No hay middleware que intente leer localStorage.
Al usar puertos diferentes, backoffice y tracker mantienen sesiones independientes.

Tras editar el paquete compartido, ejecuta `npm install` en esta aplicación y
reinicia el servidor para actualizar su copia local.

## Validación

```bash
npm run lint
npm run build
```

## Estructura

- `app/`: listado `/` y detalle `/candidatures/[id]`.
- `app/api/`: proxies de autenticación y candidaturas.
- `components/`: listado, detalle y formulario de candidaturas.
- `lib/api.ts`: cliente asíncrono para Talent Tracker API.
- `types/tracker.ts`: tipos de registros, notas y valores del pipeline.
