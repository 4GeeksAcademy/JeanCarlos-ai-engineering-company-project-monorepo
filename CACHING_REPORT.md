# Informe de optimización de caching

## Alcance y evidencia

Fecha: 2026-11-15. Revisión de las 28 operaciones FastAPI definidas en
`services/api`; frontend analizado: `uis/talent-pipeline-tracker`. Las cifras
de coste y frecuencia a continuación son hipótesis cualitativas basadas en la
implementación actual y el recorrido de la UI, **no métricas de producción**.
No se ejecutó carga de seed ni se midió una línea base con datos voluminosos.

Se añadió middleware que registra método, ruta FastAPI normalizada, status y
duración en milisegundos. Excluye query strings, IDs concretos, cabeceras,
payload y usuario; evita exponer filtros sensibles y tokens. Para validar una
decisión, recoger p50/p95 y tasa de repetición por ruta antes/después, con
volumen y distribución realistas, sin registrar valores de query.

## Decisiones de frontend

- **`CandidateFormDialog`** se carga con `next/dynamic` solo al pulsar crear o
  editar. El formulario es un diálogo extenso que no participa en el render
  inicial del listado ni del detalle. El beneficio esperado es retirar ese
  código del camino inicial; no se ha medido el tamaño de chunk ni LCP.
- **`CandidateNotes`** se carga dinámicamente al desplegar el seguimiento de
  notas. La vista obtiene texto interno y la interacción de añadir/eliminar,
  por lo que no es necesaria para leer la información principal de la
  candidatura. El fetch de notas también espera a la interacción. Beneficio
  estimado: menos JavaScript y una solicitud menos en la entrada al detalle;
  pendiente medir Network y UX.
- **`useMemo` en `CandidateList`** agrega en una sola pasada las candidaturas
  visibles por estado (hasta 1.000 resultados, según `lib/api.ts`). Antes había
  tres recorridos `filter` en cada render. El agregado depende de `records` y
  `loadState`, así que cambios del campo de búsqueda no vuelven a recorrer los
  registros. Ganancia estimada: de tres pasadas a una cuando cambian los datos
  y ninguna durante renders que solo cambian búsqueda/transición. Requiere
  React Profiler para cuantificar el beneficio real.

## Decisiones de backend

| Endpoint | Coste esperado antes | Frecuencia estimada | Estabilidad | Caché y vencimiento | Invalidación |
| --- | --- | --- | --- | --- | --- |
| GET `/suppliers` | Escaneo TinyDB O(n) y filtros | Repetido al abrir directorio/cambiar filtros | Media; cambia con operaciones administrativas | Sí, TTL 30 s; clave `(user_id, country, category)` | Alta, edición, tarifa y estado incrementan versión y vacían namespace |
| GET `/inventory/products` | Agregación de dos tablas de movimientos + join/orden por producto | Repetido al abrir catálogo/formularios y actualizar stock | Baja a media durante entradas/salidas | Sí, TTL 10 s; clave `user_id` | Alta de producto, entrada y salida invalidan tras commit |

Las claves incluyen el usuario autenticado aunque el catálogo sea compartido,
para que la caché nunca cruce contextos de autenticación. La caché está en
proceso, limitada a 1.024 claves, expira con reloj monotónico y copia los
valores al almacenar/leer. El control de versión evita que una lectura que
empezó antes de una invalidación vuelva a insertar un valor antiguo.

El middleware nuevo permitirá comprobar estos supuestos con p50/p95. Hasta
recoger esa evidencia, el ahorro esperado es evitar scans TinyDB repetidos y
agregaciones repetidas de stock, no una mejora medida. Si la telemetría muestra
lecturas ya baratas o poco repetidas, reconsiderar el cache.

## Inventario de endpoints y criterio de cache

Coste, frecuencia y estabilidad se expresan como estimaciones a validar con
timing y tráfico. Los TTL solo se aplican a los dos `GET` indicados arriba.

| Operación | Coste estimado | Frecuencia / cambio esperado | Decisión |
| --- | --- | --- | --- |
| POST `/login` | Hash bcrypt deliberadamente costoso | Solo al iniciar sesión; credenciales/sesión cambian | No cachear autenticación |
| GET `/me` | Lectura TinyDB por petición | Cada verificación de sesión; el rol puede cambiar | No cachear sesión ni identidad |
| GET `/auth/me` | Lectura TinyDB por petición | Perfil propio; email puede cambiar | No cachear datos personales |
| POST `/auth/forgot-password` | Búsqueda TinyDB + proveedor de email | Poco frecuente; respuesta debe ser neutral | No cachear efecto ni resultado por email |
| POST `/auth/reset-password` | Validar token + actualización/hash | Poco frecuente, de un solo uso | No cachear |
| POST `/auth/change-password` | Verificar y actualizar hash | Poco frecuente, revoca tokens | No cachear |
| GET `/users` | Escaneo TinyDB | Solo administración; cuentas cambian con gestión | No cachear lista administrativa |
| POST `/users` | Hash bcrypt + escritura | Poco frecuente; mutación | No cachear |
| GET `/users/{user_id}` | Lectura TinyDB | Búsqueda administrativa; cuenta puede cambiar | No cachear |
| PATCH `/users/{user_id}` | Lectura, validación y a veces hash/escritura | Solo administración; mutación | No cachear |
| DELETE `/users/{user_id}` | Elimina usuario y perfil | Poco frecuente; mutación | No cachear |
| GET `/profiles` | Escaneo/filtrado TinyDB | Gestión de perfiles; algunos resultados son propios | No cachear datos ligados a usuario |
| POST `/profiles` | Escritura y control de relación | Poco frecuente; mutación | No cachear |
| GET `/profiles/{profile_id}` | Lectura y autorización | Consultado por ID; resultado privado | No cachear |
| PATCH `/profiles/{profile_id}` | Lectura, autorización y escritura | Poco frecuente; mutación | No cachear |
| DELETE `/profiles/{profile_id}` | Lectura, autorización y eliminación | Poco frecuente; mutación | No cachear |
| GET `/suppliers` | Scan y filtros TinyDB | Frecuente en el directorio; cambia por administración | Caché TTL 30 s, acotada al usuario y filtros |
| POST `/suppliers` | Validación y escritura TinyDB | Solo al registrar; mutación | No cachear; invalida listado |
| GET `/suppliers/{supplier_id}` | Lectura TinyDB por ID | Navegación puntual; ficha cambia con edición | No cachear hasta medir repetición |
| PUT `/suppliers/{supplier_id}` | Lectura, validación y escritura | Solo edición; mutación | No cachear; invalida listado |
| PATCH `/suppliers/{supplier_id}/rate` | Lectura y escritura | Acción administrativa; mutación | No cachear; invalida listado |
| PATCH `/suppliers/{supplier_id}/status` | Lectura y escritura | Acción administrativa; mutación | No cachear; invalida listado |
| GET `/inventory/products` | Agrega movimientos y calcula stock | Frecuente entre catálogo/formularios; stock cambia en movimientos | Caché TTL 10 s, clave por usuario |
| POST `/inventory/products` | Escritura PostgreSQL | Solo al alta; mutación | No cachear; invalida catálogo |
| GET `/inventory/products/{supply_id}` | Stock agregado de un producto | Consulta puntual y sensible a movimientos concurrentes | No cachear saldo individual |
| POST `/inventory/orders/inbound` | Lock + escritura PostgreSQL | Operación de negocio; cambia stock | No cachear; invalida catálogo tras commit |
| POST `/inventory/orders/outbound` | Lock + consulta de stock + escritura | Operación de negocio; cambia stock | No cachear; invalida catálogo tras commit |
| GET `/inventory/orders` | Lee y une ambos historiales | Menos frecuente; contiene usuario y movimientos recientes | No cachear por volumen y datos de auditoría |

## Intercambio frescura / rendimiento y límites

El stock es una lectura agregada y la API es la autoridad para aceptar salidas.
Un TTL de 10 s evita repetir agregaciones al navegar, mientras limita la
desactualización incidental a una ventana breve; las escrituras locales
invalidan inmediatamente tras commit. Una operación en otro proceso puede
dejar una respuesta obsoleta hasta 10 s. En instalaciones de varias instancias
de API, la caché en memoria no comparte invalidaciones: sustituir por Redis con
versiones/pub-sub o eliminar ese cache antes de escalar. La vista del cliente
no debe usar el resultado cacheado para autorizar el consumo; PostgreSQL vuelve
a validar el stock bajo lock.

El directorio cambia menos que las órdenes; 30 s reduce escaneos y filtros
repetidos, con invalidación inmediata en escrituras del proceso actual. La
persistencia TinyDB ya requiere un único proceso que escriba el archivo. Si
otra herramienta modifica su base mientras el API sigue levantado, esa
modificación no notifica la caché; el TTL limita la obsolescencia a 30 s.

## Validación posterior

Ejecutar con datos sintéticos variados y autorizados: registrar filas iniciales
y finales, latencias p50/p95, repeticiones por ruta, hit ratio, carga de
proceso y respuestas justo después de cada escritura. Comparar misma máquina,
datos y perfil de tráfico antes/después. No se crearon ni se insertaron datos
en una base compartida como parte de este cambio.
