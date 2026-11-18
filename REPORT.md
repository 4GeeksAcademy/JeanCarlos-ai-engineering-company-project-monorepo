# Informe de rendimiento frontend

Antes: `main` (`763db31`). Después: rama `feature/perf-audit`. Mismo equipo,
navegador, Lighthouse 12.8.2, datos, APIs y builds de producción (`next build` +
`next start`); 5 ejecuciones por ruta y formato, mediana. Protocolo y diagnóstico
en [`AUDIT.md`](AUDIT.md).

## Resultado

| Objetivo | Antes | Después |
| --- | --- | --- |
| CLS < 0,1 (tracker, escritorio) | ❌ **0,908** | ✅ **0** |
| LCP < 2,5 s (tracker, móvil, limitación real) | ⚠️ 2,20 s (2,40 s en frío) | ✅ **1,46 s** |
| INP < 200 ms (tracker, interacción con CPU 4×) | ✅ 48 ms | ✅ 48 ms |
| Backoffice (todas las rutas) | ✅ 99–100 | ✅ 99–100 (sin cambios, control) |

## Comparativa antes / después

### Lighthouse por defecto (`simulate`), mediana de 5

| Ruta | Formato | Performance | LCP | CLS | FCP | TBT | Speed Index |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Tracker `/` | escritorio | 76 → **100** (+24) | 524 → 498 ms | **0,908 → 0** | 524 → **204 ms** | 0 → 0 ms | 524 → 204 ms |
| Tracker `/` | móvil | 96 → 95 (−1) | 2165 → 2308 ms (+143) ¹ | 0 → 0 | 1625 → **753 ms** (−54 %) | 156 → 199 ms (+43) ² | 1625 → **753 ms** |
| Backoffice `/login` | móvil | 99 → 99 | 2005 → 2005 ms | 0 → 0 | 753 → 753 ms | 3 → 2 ms | 753 → 753 ms |
| Backoffice `/login` | escritorio | 100 → 100 | 456 → 456 ms | 0 → 0 | 203 → 203 ms | 0 → 0 ms | 203 → 203 ms |
| Backoffice inventario | móvil | 99 → 99 | 2009 → 2012 ms | 0 → 0 | 754 → 755 ms | 4 → 7 ms | 754 → 755 ms |
| Backoffice inventario | escritorio | 100 → 100 | 458 → 458 ms | 0 → 0 | 204 → 204 ms | 0 → 0 ms | 204 → 204 ms |

### Tracker en móvil con limitación real (`devtools`), mediana de 5

| Métrica | Antes | Después | Cambio |
| --- | --- | --- | --- |
| Performance | 89 | **92** | +3 |
| LCP | 2203 ms | **1459 ms** | **−744 ms (−34 %)** |
| FCP | 2048 ms | **1459 ms** | −589 ms (−29 %) |
| Speed Index | 2303 ms | **1674 ms** | −629 ms (−27 %) |
| TBT | 363 ms | 354 ms | −9 ms |
| CLS | 0 | 0 | = |

LCP observado (lo que ocurrió en la carga real de cada ejecución `simulate`): móvil
**301 → 60 ms**, escritorio **224 → 55 ms**. Ahora el `<h1>` se pinta con el
primer fotograma y no vuelve a cambiar.

¹ **Lectura honesta del LCP móvil simulado.** Lantern, el modelo de Lighthouse,
simula red y CPU lentas sobre una carga que contra el servidor local termina en
~50 ms; incluye el JavaScript entre las dependencias del pintado y lo estima en
~2,3 s en ambas versiones. La limitación real, que ralentiza de verdad la
carga, muestra la mejora (−744 ms), igual que el LCP observado. Se reporta el
valor simulado tal cual para no ocultarlo.
² En el modelo simulado, el *bootup* del chunk principal pasa de 434 a 495 ms
(el HTML inicial ahora contiene más marcado que hidratar). Con limitación real el
TBT no empeora (363 → 354 ms). Sigue por debajo de 200 ms en `simulate` y queda
anotado para vigilarlo.

Las cifras del backoffice antes y después coinciden (±3 ms): no se modificó y
confirma que las diferencias del tracker no son ruido del entorno.

## Cambios realizados y su impacto

| Cambio | Archivos | Efecto medido |
| --- | --- | --- |
| Altura reservada del estado de carga + `scrollbar-gutter: stable` | `app/globals.css`, `components/pipeline-shell.tsx` | CLS escritorio **0,908 → 0** (reproducción: 0,9075 → 0,000) |
| Topbar y cabecera fuera del `Suspense`, *fallback* con la misma maqueta | `app/page.tsx`, `components/pipeline-shell.tsx`, `components/pipeline-events.tsx`, `components/candidate-list.tsx` | `<h1>` en el HTML inicial; FCP −54 % (simulado) / −29 % (real); LCP real −34 % |
| Fuentes con `next/font` en lugar del `@import` de Google Fonts | `app/layout.tsx`, `app/globals.css` | Desaparece el recurso de terceros bloqueante (≈ 0,9–1 s desperdiciados en móvil); 0 peticiones a Google |
| Next 16.4.6 en el tracker (prerrequisito para compilar) | `package.json` | Aplicado también a la línea base; no forma parte de la mejora |

Comportamiento verificado tras el cambio: listado, filtros y «Nueva candidatura»
(al guardar aparece el aviso y la lista se recarga, total 1000 → 1001). `npm run
lint` y `npm run build` OK. La refactorización anterior (`useInventoryResource`)
se mantiene como mejora de mantenibilidad; no se le atribuye ningún efecto en
las métricas.

**Iteración descartada** (`audit/iteration-1/`): dejar el `<h1>` dentro del
*fallback* llevaba el escritorio a 100 y el FCP a 754 ms, pero el LCP móvil no
mejoraba, porque la hidratación reemplazaba el nodo. Se corrigió sacando la
cabecera del `Suspense`.

## Evidencia

- Capturas de cada informe (ejecución mediana): `audit/screenshots/`
  - `baseline--tracker-home-desktop.png` / `after--tracker-home-desktop.png` (76 → 100, CLS 0,908 → 0)
  - `baseline-devtools--tracker-home-mobile.png` / `after-devtools--tracker-home-mobile.png` (LCP 2,2 s → 1,5 s)
  - el resto de rutas y formatos con el mismo patrón de nombre
- Informes HTML completos: `audit/{baseline,after,baseline-devtools,after-devtools,iteration-1}/*.report.html`
- Medianas y las 5 ejecuciones de cada caso: `audit/*/summary.json`
- Herramientas: `audit/tools/` (API simulada, runner de Lighthouse, medición de interacción)

## Límites y siguientes pasos

- Son mediciones de laboratorio en un equipo local con datos sintéticos; los Core
  Web Vitals deben confirmarse con datos de campo (RUM anonimizado o CrUX) en el
  percentil 75, separados por móvil y escritorio. INP solo se puede medir de
  verdad con usuarios reales; aquí se midió la latencia de interacción en laboratorio.
- 1000 filas renderizadas a la vez producen un DOM grande. Con el volumen actual no
  afecta a TBT ni a la interacción, pero si los datos crecen conviene paginar o
  virtualizar el listado.
- Vigilar el *bootup* de JavaScript del tracker en móvil (nota ²).
