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
