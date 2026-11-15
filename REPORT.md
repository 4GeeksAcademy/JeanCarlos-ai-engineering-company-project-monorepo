# Informe de rendimiento frontend

## Comparativa antes y después

Referencia: commit `0887253`. Versión posterior: refactorización de esta
auditoría. Ambas deben medirse en un build de producción local, con la misma
API de prueba, sesión, datos, dispositivo y throttling. Las cifras están
pendientes: no se dispone de una línea base Lighthouse válida.

| Ruta / métrica | Antes | Después | Variación | Resultado |
| --- | --- | --- | --- | --- |
| Tracker `/` — Performance | Pendiente | Pendiente | Pendiente | Sin evaluar |
| Tracker `/` — LCP / CLS / TTFB | Pendiente | Pendiente | Pendiente | Sin evaluar |
| Backoffice `/login` — Performance | Pendiente | Pendiente | Pendiente | Sin evaluar |
| Backoffice `/login` — LCP / CLS / TTFB | Pendiente | Pendiente | Pendiente | Sin evaluar |
| Backoffice `/inventory/products` — Performance | Pendiente | Pendiente | Pendiente | Sin evaluar |
| Backoffice `/inventory/products` — LCP / CLS / TTFB | Pendiente | Pendiente | Pendiente | Sin evaluar |
| INP real por ruta y dispositivo | Pendiente | Pendiente | Pendiente | Requiere datos de campo |

Para cada fila, añadir móvil y escritorio por separado, mediana de cinco
ejecuciones de laboratorio, fecha, versiones, condiciones y enlaces a los
informes. INP debe obtenerse de interacciones reales, no del TBT de Lighthouse.
Comparar los percentiles 75 de campo con LCP < 2,5 s, CLS < 0,1 e INP < 200 ms;
usar TTFB < 0,8 s como referencia operativa.

## Cambios realizados e impacto

- Se extrajo `useInventoryResource` para reutilizar la carga cancelable,
  errores y reintento de catálogo e historial, conservando la redirección de
  sesión existente.
- Impacto esperado: menos duplicación y mantenimiento más consistente. **No
  hay evidencia de mejora en Core Web Vitals**, ni debe atribuirse una reducción
  de latencia o JavaScript a la extracción sin medirla.
- Validación ejecutada: `npm run lint` y `npm run build` en ambos frontends.
  La interacción en navegador y la comparación Lighthouse quedan pendientes
  por las dependencias de Chromium indicadas en `AUDIT.md`.

## Conclusión y siguientes pasos

Estado de los objetivos: **no evaluado**. Completar la línea base y la medición
posterior con el protocolo de `AUDIT.md`; investigar las métricas que no
cumplan el objetivo y registrar las optimizaciones y sus resultados antes de
declarar una mejora.
