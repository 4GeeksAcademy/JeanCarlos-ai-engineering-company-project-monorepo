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
