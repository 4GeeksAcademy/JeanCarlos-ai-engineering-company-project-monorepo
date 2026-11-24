# Evidencia: verificación en vivo (2026-11-24)

Las 4 RFP se subieron **por la UI** del backoffice (`/rfp`) con Playwright, contra la API local y el modelo real del gateway (`deepseek-v4-flash`). La subida respondió en menos de 1 s (202) y la UI hizo polling hasta el estado final.

| # | PDF | Ticket | Resultado | Tiempo | Captura | JSON |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Meridian Manufacturing (formal, US) | RFP-20261124-5412E0 | `analisis_completo`, USD · HIPAA · BAA, 800 empleados → `proposal_generation` | 43 s | [rfp-1.png](rfp-1.png) | [ticket-1-formal-meridian.json](ticket-1-formal-meridian.json) |
| 2 | Thames Valley University (informal, UK) | RFP-20261124-0964DA | `analisis_completo`, GBP · UK GDPR · DPA, 3000 estudiantes → `proposal_generation` | 60 s | [rfp-2.png](rfp-2.png) | [ticket-2-informal-thames-valley.json](ticket-2-informal-thames-valley.json) |
| 3 | MedFlow (pitch de un proveedor de EHR) | RFP-20261124-FDBE05 | `descartado` (clasificador: no es RFP), sin workers | 6 s | [rfp-3.png](rfp-3.png) | [ticket-3-invalid-medflow.json](ticket-3-invalid-medflow.json) |
| 4 | Gulfline Logistics (sintética, con PHI) | RFP-20261124-B45C96 | `analisis_completo`, PHI redactada antes de cualquier agente → `compliance_review`, `ready_for_generation = false` | 48 s | [rfp-4.png](rfp-4.png) | [ticket-4-phi-gulfline.json](ticket-4-phi-gulfline.json) |

En el caso 4 solo se registran las categorías de PHI (`clinical_summary`, `date_of_birth`, `diagnosis_field`, `medical_record_number`, `patient_name`). Los JSON se revisaron con `find_phi` y no contienen ningún valor del adjunto clínico.

Los JSON se generaron con `python scripts/rfp_intake.py show <ticket_id>`, que muestra la misma vista que `GET /api/rfp/tickets/{id}`.

## Parte 2: generación y evaluación (2026-11-24)

Las RFP 1 y 2 se volvieron a subir **por la UI**. La Parte 1 terminó en `analisis_completo` y la Parte 2 arrancó sola desde el handoff (`ready_for_generation`), sin volver a leer el PDF. La UI hizo polling durante todo el ciclo:

| Ticket | Recorrido del ticket | Resultado por sección |
| --- | --- | --- |
| RFP-20261124-F97B58 (Meridian, US) | `analizando` → `analisis_completo` → `generando_borrador` → `en_evaluación` (127 s) | `compliance` y `clinical` pasan en la iteración 1. `revenue` falla en pertinencia en la iteración 1 (falta «Pricing structure»), recibe el feedback y pasa en la iteración 2. |
| RFP-20261124-890FBB (Thames Valley, UK) | `analizando` → `analisis_completo` → `generando_borrador` → `en_evaluación` → **`needs_human_review`** (87 s) | `compliance` pasa en la iteración 1. `revenue` falla dos veces por HC-FIG-01 (una cifra inventada: «50») y pasa en la iteración 3. `clinical` agota las 3 iteraciones sin cubrir «Timeline» ni «Scheduling and location», así que conserva su último borrador y su `EvaluationResult`, y pasa igual a la Parte 3. |

- Capturas: [rfp-part2-1.png](rfp-part2-1.png) (Meridian) y [rfp-part2-2.png](rfp-part2-2.png) (Thames Valley).
- JSON de cada ticket, con los borradores, el `EvaluationResult` y el historial de iteraciones: [ticket-part2-1-meridian.json](ticket-part2-1-meridian.json) y [ticket-part2-2-thames-valley.json](ticket-part2-2-thames-valley.json).
- Los JSON se revisaron con `find_phi` y no contienen PHI.

## Parte 3: aprobación humana, arbitraje y cierre (2026-11-24/25)

Recorrido completo **Parte 1 → 2 → 3 por la UI** (Playwright contra la API local y el modelo real). Cada departamento aprueba en su propia tarjeta: cada clic reanuda el hilo `rfp-{ticket}:{departamento}` en su `interrupt`.

| Ticket | Plan | Recorrido del ticket | Qué pasó en la Parte 3 |
| --- | --- | --- | --- |
| RFP-20261124-13CA0D (Meridian, US) | standard | `analizando` → `analisis_completo` → `generando_borrador` → `en_evaluación` → `esperando_aprobación` → **`terminado`** (67 s) | 1. Dr. Marcus Reid aprueba Clinical (capacidad 800) mientras Revenue y Compliance siguen interrumpidas.<br>2. Tom Callahan pide cambios: Revenue vuelve al generador de la Parte 2 (nodos 60–66 de la traza) y se le vuelve a preguntar.<br>3. Tom aprueba.<br>4. Claire Whitfield aprueba.<br>5. El documento final se genera solo. |
| RFP-20261124-3BAE79 (Thames Valley, UK) | capacity | mismo recorrido → **`terminado`** (97 s) | 1. Clinical confirma capacidad para 2000 de 3000 estudiantes.<br>2. Cuando Compliance aprueba, el arbitraje dispara `capacity-vs-population` y pausa para Tom Callahan.<br>3. Tom reduce la población a 2000.<br>4. Se reabren Revenue y Clinical (las dos citaban 3000), se revisan en paralelo y se vuelven a aprobar.<br>5. El documento dice 2,000 en todas partes. |
| RFP-20261124-A72820 (Meridian, US) | standard, **`scripts/rfp_e2e.py`** (sin UI) | `analizando` → … → `terminado` (69 s) | Las mismas decisiones, como `resume` programático. Informe: [e2e-standard-RFP-20261124-A72820.json](e2e-standard-RFP-20261124-A72820.json) (línea de tiempo de estados, hilos pausados tras cada paso, traza) y [documento final](e2e-standard-RFP-20261124-A72820-final-document.md). |
| RFP-20261124-2F4945 (Thames Valley, UK) | capacity (ejecución previa a la corrección) | → `terminado` | Clinical seguía citando 3000 después de la decisión de Tom. Su aprobación quedó bloqueada por el guardrail; el `request_changes` de Dr. Reid, ya con la corrección, la dejó en 2,000 y el ticket se cerró. |

**Capturas:**
- Meridian:
  - [todas las ramas pausadas](rfp-part3-standard-1-paused.png)
  - [Clinical aprobada con Revenue y Compliance interrumpidas](rfp-part3-standard-2-b-approved-a-paused.png)
  - [documento final](rfp-part3-standard-3-final-document.png)
  - [traza](rfp-part3-standard-4-trace.png)
- Thames Valley:
  - [ramas pausadas](rfp-part3-capacity-1-paused.png)
  - [arbitraje de Tom Callahan](rfp-part3-capacity-2-arbitration.png)
  - [secciones reabiertas](rfp-part3-capacity-3-reopened.png)
  - [documento final](rfp-part3-capacity-4-final-document.png)
  - [traza](rfp-part3-capacity-5-trace.png)

**Datos:**
- Documentos finales: [final-document-RFP-20261124-13CA0D.md](final-document-RFP-20261124-13CA0D.md) y [final-document-RFP-20261124-3BAE79.md](final-document-RFP-20261124-3BAE79.md).
- Tickets: [ticket-part3-RFP-20261124-13CA0D.json](ticket-part3-RFP-20261124-13CA0D.json) y [ticket-part3-RFP-20261124-3BAE79.json](ticket-part3-RFP-20261124-3BAE79.json).
- Trazas (`scripts/rfp_intake.py trace`): [trace-RFP-20261124-13CA0D.txt](trace-RFP-20261124-13CA0D.txt) y [trace-RFP-20261124-3BAE79.txt](trace-RFP-20261124-3BAE79.txt).

Todos los archivos se revisaron con `find_phi` y no contienen PHI.
