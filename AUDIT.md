# Auditoría de rendimiento frontend

Fecha de ejecución: 2026-11-18. Rutas auditadas: Talent Pipeline Tracker `/`,
backoffice `/login` y backoffice `/inventory/products` (autenticada).
`uis/website` no existe en el monorepo.

Línea base: commit `763db31` (`main`). Los resultados, informes y capturas están
en [`audit/`](audit/); el antes/después y su interpretación, en [`REPORT.md`](REPORT.md).

## Entorno y protocolo

| Elemento | Valor |
| --- | --- |
| Equipo | Apple M5, 24 GB, macOS 26.6.2, Node 26.5.0 |
| Navegador | Microsoft Edge 156 (Chromium), headless |
| Herramienta | Lighthouse 12.8.2, solo categoría Performance |
| Modo de las apps | **Producción**: `npm run build` + `next start` (tracker en :3000, backoffice en :3001), nunca `next dev` |
| Next.js | 16.4.6 en ambas versiones. `main` fija 16.4.4 en el tracker, que aborta `next build` por la advisory GHSA-mfr8-8qww-v76h; el mismo parche de versión se aplicó a la línea base y a la versión optimizada |
| Datos del tracker | API simulada local (`audit/tools/mock-tracker-api.mjs`): **1000 candidaturas sintéticas** deterministas (el máximo que pide la app, `limit=1000`), mismo formato que la API real (la real devuelve 100 registros, 70 KB) y **150 ms** de latencia fija. Sin datos de personas reales |
| Datos del backoffice | API FastAPI local + PostgreSQL 16 (Docker) con el seed del BRIEFING (6 suministros, 4 entregas, 3 consumos); usuario de prueba y cookie de sesión para la ruta protegida. El runner aborta si la URL final es `/login` |
| Ejecuciones | **5 por ruta y formato** (móvil y escritorio); se reporta la **mediana** y se guarda el informe HTML de la ejecución mediana |
| Limitación | Por defecto la de Lighthouse (`simulate`). Para el LCP móvil del tracker, además, **limitación real** (`--throttling-method=devtools`), ver diagnóstico 3 |
| Interacción (base de INP) | `audit/tools/measure-inp.mjs`: Event Timing API con CPU a 4×, cambiando el filtro de estado y escribiendo en el buscador; mediana de la peor interacción en 5 ejecuciones |

Reproducción: `node audit/tools/mock-tracker-api.mjs`, levantar API y apps en
producción y `CHROME_PATH=… SESSION_COOKIE=… node audit/tools/run-lighthouse.mjs <etiqueta> 5`
(ver cabecera de cada script).

## Línea base (mediana de 5, `simulate`)

| Ruta | Formato | Performance | LCP | CLS | FCP | TBT | Speed Index |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Tracker `/` | móvil | 96 | 2165 ms | 0 | 1625 ms | 156 ms | 1625 ms |
| Tracker `/` | escritorio | **76** | 524 ms | **0,908** | 524 ms | 0 ms | 524 ms |
| Backoffice `/login` | móvil | 99 | 2005 ms | 0 | 753 ms | 3 ms | 753 ms |
| Backoffice `/login` | escritorio | 100 | 456 ms | 0 | 203 ms | 0 ms | 203 ms |
| Backoffice `/inventory/products` | móvil | 99 | 2009 ms | 0 | 754 ms | 4 ms | 754 ms |
| Backoffice `/inventory/products` | escritorio | 100 | 458 ms | 0 | 204 ms | 0 ms | 204 ms |

Con limitación real, el tracker en móvil: Performance 89, **LCP 2203 ms**, FCP 2048 ms, TBT 363 ms.
Latencia de interacción del tracker: **48 ms** (peor interacción, mediana).

Capturas: `audit/screenshots/baseline--*.png`. Informes: `audit/baseline/*.report.html`
y `audit/baseline-devtools/`.

Objetivos: LCP < 2,5 s, CLS < 0,1, INP < 200 ms (TBT como señal de laboratorio).
**Incumple: el CLS de escritorio del tracker (0,908, 9× el límite).** El LCP móvil del
tracker cumple por poco (2,2 s; 2,9 s en la primera ejecución en frío).

## Diagnóstico: causas raíz (con evidencia)

### 1. CLS 0,908 del tracker en escritorio: la llegada de las 1000 filas

- Lighthouse atribuye el 0,9075 a `div.page-content`. Lo reproduje fuera de
  Lighthouse con un `PerformanceObserver` de `layout-shift` y las **barras de
  desplazamiento visibles** (Playwright las oculta por defecto, por eso una
  primera traza no lo mostraba): un único desplazamiento de **0,9075 a los 426 ms**,
  justo cuando llega el listado.
- Fuentes del desplazamiento en ese evento:
  - `page-footer`: y 672 → fuera de la ventana. El estado de carga
    («Cargando candidaturas…») mide 205 px; la tabla hace crecer la página de
    654 px a **58 990 px** y empuja el pie.
  - `page-content`, `topbar-context`, `team-avatar`: x 25 → 18, x 680 → 673,
    x 1291 → 1276. Al crecer la página **aparece la barra de desplazamiento
    vertical (15 px)** y todo el contenido centrado se recoloca. Como
    `page-content` ocupa casi toda la ventana, la fracción de impacto es enorme.
- **Hipótesis descartada (experimento de control):** Lighthouse lista «Web font
  loaded» como posible causa. Repetí la medición bloqueando `fonts.googleapis.com`
  y `fonts.gstatic.com`: **el CLS siguió en 0,908**. Las fuentes no son la causa del CLS.

### 2. El `<h1>` (elemento LCP) no estaba en el HTML

- `CandidateList` usa `useSearchParams`, así que `/` (ruta estática) prerenderiza
  solo el *fallback* de `Suspense`: un *spinner* «Cargando el pipeline…». El HTML
  inicial no contenía la topbar, la cabecera ni el `<h1>`: todo dependía de
  descargar y ejecutar el JavaScript. La tira de fotogramas de la línea base lo
  muestra (primer fotograma: solo el *spinner*).
- Fases del LCP móvil: TTFB 452 ms, **render delay 1717 ms**, sin carga de recurso.

### 3. Hoja de Google Fonts bloqueante del renderizado

- `globals.css` empezaba con `@import url("https://fonts.googleapis.com/…")`:
  cadena crítica CSS propio → CSS de Google (otro origen) → `.woff2` de
  `fonts.gstatic.com`. Lighthouse la marca como **bloqueante (≈ 905–998 ms
  desperdiciados en móvil)**.
- Nota de método: en móvil con `simulate`, el LCP observado de la línea base
  fue 301 ms y el simulado 2165 ms. Lighthouse modela la red lenta sobre una carga
  que contra un servidor local termina en milisegundos, y su modelo (Lantern)
  incluye el JavaScript como dependencia del pintado. Por eso el LCP móvil del
  tracker se verificó **también con limitación real** (`devtools`), que ralentiza
  de verdad la red y la CPU durante la carga.

### Observaciones sin problema demostrado (no se optimizan)

- **INP:** la peor interacción del tracker (filtro y búsqueda, CPU 4×) es 48 ms;
  los filtros ya usan `startTransition`. Sin evidencia de problema.
- **Backoffice:** 99–100 en todas las rutas, CLS 0, TBT ≤ 9 ms. La hipótesis previa
  sobre el TTFB por la verificación de sesión no se confirma con los datos de
  laboratorio: el tiempo de respuesta del servidor observado en
  `/inventory/products`, que incluye esa verificación, es ≤ 7 ms en local. Se mantiene sin cambios y sirve como
  **control**: sus cifras antes/después coinciden (±3 ms), así que el entorno es estable.
- **Tamaño del DOM:** 1000 filas generan un DOM grande (insight «Optimize DOM
  size»). Hoy no se traduce en TBT/INP problemáticos; queda como siguiente paso
  (paginación o virtualización) si el volumen real crece.

## Optimizaciones decididas (cada una ligada a una causa)

| Causa | Cambio | Métrica objetivo |
| --- | --- | --- |
| 1. Filas que empujan el pie | Estado de carga con altura reservada (`.list-loading`, `min-height: max(205px, 100vh)`): la tabla crece por debajo del pliegue | CLS |
| 1. Barra de desplazamiento | `html { scrollbar-gutter: stable; }`: el hueco de la barra está reservado desde el primer pintado | CLS |
| 2. `<h1>` solo tras el JS | Topbar y cabecera fuera del `Suspense`, renderizadas en el servidor una sola vez; el `Suspense` envuelve solo resumen y listado, con un *fallback* de la misma maqueta (`components/pipeline-shell.tsx`). El botón «Nueva candidatura» de la cabecera avisa al listado mediante un contexto (`components/pipeline-events.tsx`) | LCP, FCP, CLS |
| 3. Google Fonts bloqueante | `next/font/google` (DM Sans y Space Grotesk): fuentes servidas desde la propia app, precargadas por cabecera `Link` y con *fallback* de métricas ajustadas | FCP, LCP |

La primera iteración dejaba el `<h1>` dentro del *fallback*. Eso mejoraba el FCP,
pero en la hidratación React **reemplazaba** el nodo y el LCP volvía a fijarse al
terminar el JavaScript (`audit/iteration-1/`). La versión final lo saca del
`Suspense`; se verificó que el `<h1>` es el mismo nodo desde el HTML inicial,
tras hidratar y tras recargar la lista.
