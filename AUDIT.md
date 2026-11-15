# Auditoría de rendimiento frontend

## Alcance y estado

Fecha: 2026-11-15. Se auditan `uis/talent-pipeline-tracker` y
`uis/backoffice`; `uis/website` no existe. La referencia previa a la
refactorización es el commit `0887253`.

**No se han obtenido puntuaciones iniciales.** Lighthouse se instaló en un
directorio temporal, pero Chromium no arrancó por bibliotecas del sistema
ausentes (`libavahi-common.so.3`, `libavahi-client.so.3`, `libdrm.so.2` y
`libXi.so.6`). Los valores siguientes son marcadores, no mediciones ni evidencia
de cumplimiento. El entorno acordado es local con datos sintéticos; no
representa la red, carga ni datos de producción.

| Ruta | Performance Lighthouse | LCP | CLS | INP real | TTFB |
| --- | --- | --- | --- | --- | --- |
| Tracker `/` | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente |
| Backoffice `/login` | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente |
| Backoffice `/inventory/products` autenticado | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente |

Objetivos solicitados: LCP < 2,5 s, CLS < 0,1 e INP < 200 ms. Para TTFB se
propone como referencia < 0,8 s; hay que interpretarlo en el contexto de la
infraestructura. Evaluar los Core Web Vitals en el percentil 75 de visitas
reales, separado por móvil y escritorio. Los resultados locales no demuestran
que se alcancen esos objetivos.

## Diagnóstico: observaciones e hipótesis

No hay trazas suficientes para atribuir una causa raíz a una métrica. Las
siguientes observaciones deben verificarse antes de optimizar:

1. **TTFB del backoffice:** el layout protegido verifica la sesión contra
   `/me` antes de renderizar. Hipótesis: la latencia o disponibilidad de la
   API puede incrementar el TTFB. Medir la duración de `/me` y el desglose de
   red; no eliminar la comprobación de autenticación.
2. **LCP y CLS:** el catálogo carga productos en el cliente después del
   renderizado inicial y sustituye el estado de carga por una tabla. Hipótesis:
   red, hidratación y cambio de altura pueden retrasar el contenido o provocar
   desplazamientos. Identificar el elemento LCP y las fuentes de CLS en una
   traza antes de reservar espacio o cambiar el momento de carga.
3. **INP del tracker:** el listado solicita hasta 1.000 candidaturas y filtra
   resúmenes en el navegador. Hipótesis: grandes volúmenes pueden aumentar el
   trabajo del hilo principal durante filtros y renderizado. Medir con un
   conjunto sintético representativo; valorar paginación o procesamiento
   eficiente solo si la traza lo justifica.
4. **Código duplicado:** catálogo e historial repetían la carga cancelable,
   gestión de errores y reintento. Esto es un problema de mantenibilidad
   observado, no una causa demostrada de una mala puntuación.

## Plan de medición y solución

1. Preparar una API local de prueba y una sesión de prueba para las rutas
   protegidas; no utilizar credenciales ni datos personales reales. Configurar
   `NEXT_PUBLIC_API_URL` del tracker y `SUPPLIERS_API_URL` del backoffice para
   apuntar a ese entorno. Registrar el tamaño del conjunto de datos y la
   latencia simulada.
2. Compilar y ejecutar cada frontend en modo producción (`npm run build` y
   `npm run start`), no con `next dev`. Medir la referencia `0887253` y la
   versión modificada en copias aisladas, con idénticos datos y ajustes.
3. En Chrome DevTools > Lighthouse, ejecutar navegación móvil y escritorio,
   cinco veces por ruta y versión, con la misma máquina, navegador y
   throttling; registrar la mediana y adjuntar los informes. En el inventario,
   iniciar sesión en el entorno de prueba y conservar el almacenamiento al
   auditar. Verificar que la URL final no sea `/login`.
4. Registrar Performance, LCP, CLS, FCP, TBT y TTFB de laboratorio. Lighthouse
   en navegación no proporciona INP real: TBT es una señal de laboratorio, no
   un sustituto ni una medición de INP. Para INP, observar interacciones
   representativas en DevTools y, en un despliegue autorizado, recoger RUM
   anonimizado o CrUX cuando exista suficiente tráfico.
5. Priorizar según las trazas: reducir latencia de dependencias del servidor,
   evitar trabajo innecesario en el cliente, reservar espacio para contenido
   asíncrono y optimizar recursos LCP cuando proceda. Volver a medir con el
   mismo protocolo y registrar diferencias y regresiones en `REPORT.md`.

Plantilla de ejecución: fecha y commit; URL final; dispositivo y versión de
Chrome/Lighthouse; condiciones de red y CPU; estado de sesión; cantidad de
datos; cinco valores individuales, mediana, traza y enlace al informe.
