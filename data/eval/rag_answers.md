# Respuestas generadas sobre test-queries.json

Generadas con `python data/eval/evaluate_rag_retrieval.py --with-answers` (min_score=0.3).

### ins-01 — ¿Aceptan Medicaid en la clínica de Georgia?

_Chunks sobre el umbral: insurance-coverage#0 (0.55)_

No, actualmente Medicaid no se acepta en las clínicas de Georgia. Solo está disponible en las clínicas de Texas y Florida. Si el paciente tiene Medicaid, puede agendar una cita en Texas o Florida, o bien considerar la tarifa de "self-pay" con 20% de descuento si paga el mismo día de la consulta.

Fuente: Cobertura de Seguros Aceptados — Estados Unidos (Texas, Florida, Georgia)

### ins-02 — Un paciente sin seguro, ¿tiene algún descuento si paga el mismo día?

_Chunks sobre el umbral: appointment-policy#1 (0.41), insurance-coverage#1 (0.36), appointment-policy#0 (0.35), new-patient-checklist#1 (0.34), insurance-coverage#0 (0.34)_

Sí, los pacientes sin seguro en Estados Unidos pueden pagar la tarifa de "self-pay" con un 20% de descuento si el pago se realiza el mismo día de la consulta. Este descuento aplica en las clínicas de Texas, Florida y Georgia.

Fuente: Cobertura de Seguros Aceptados — Estados Unidos (Texas, Florida, Georgia)

### ins-03 — ¿Atienden pacientes del NHS en Londres?

_Chunks sobre el umbral: insurance-coverage#1 (0.60), new-patient-checklist#0 (0.32)_

Sí, en Londres atendemos pacientes del NHS, pero con un cupo limitado de citas mensuales. Si el cupo ya está completo, ofrecemos la opción de pago privado o referimos al paciente de vuelta al sistema NHS estándar. Es importante que el paciente confirme su cupo con antelación.

Fuente: Cobertura de Seguros Aceptados — Reino Unido (Londres y Manchester)

### ins-04 — ¿Qué hago si el cupo mensual del NHS ya está lleno?

_Chunks sobre el umbral: insurance-coverage#1 (0.50)_

Si el cupo mensual del NHS ya está lleno, debes ofrecer al paciente la opción de pagar de forma privada o referirlo de vuelta al sistema NHS estándar. No hay otra alternativa documentada para reservar una cita con cargo al NHS en ese mes.

Fuente: Cobertura de Seguros Aceptados — Reino Unido (Londres y Manchester)

### ins-05 — El paciente tiene un seguro que no conozco, ¿puedo confirmarle que lo cubrimos?

_Chunks sobre el umbral: insurance-coverage#2 (0.55), new-patient-checklist#0 (0.52), new-patient-checklist#2 (0.44), insurance-coverage#1 (0.44), insurance-coverage#0 (0.42)_

No debe confirmar cobertura de ningún seguro que no esté listado en nuestra base de conocimiento. Primero debe verificar con el equipo de facturación (Tom Callahan) antes de dar cualquier respuesta al paciente.

Fuente: Cobertura de Seguros Aceptados — Cobertura de Seguros Aceptados

### ins-06 — ¿Qué seguros privados aceptan?

_Chunks sobre el umbral: insurance-coverage#0 (0.47), insurance-coverage#1 (0.44), insurance-coverage#2 (0.39)_

En Estados Unidos (Texas, Florida, Georgia) aceptamos seguros comerciales como Blue Cross Blue Shield, Aetna, Cigna y UnitedHealthcare. En Reino Unido (Londres y Manchester) trabajamos con seguros privados como Bupa y AXA Health. Si el seguro del paciente no está en esta lista, debe verificarse con el equipo de facturación (Tom Callahan) antes de confirmar nada.

Fuente: Cobertura de Seguros Aceptados — Estados Unidos (Texas, Florida, Georgia); Cobertura de Seguros Aceptados — Reino Unido (Londres y Manchester); Cobertura de Seguros Aceptados — Cobertura de Seguros Aceptados

### app-01 — ¿Cobran cargo por cancelación con 12 horas de anticipación?

_Chunks sobre el umbral: appointment-policy#1 (0.61), appointment-policy#2 (0.40), appointment-policy#3 (0.38), appointment-policy#0 (0.31)_

Sí, se cobra cargo por cancelación con 12 horas de anticipación, ya que la política establece que si se cancela con menos de 24 horas antes de la cita, aplica un cargo de 50 USD (o 40 GBP en Reino Unido) para pacientes de pago privado. Para pacientes con Medicare o Medicaid no se cobra, pero se registra el incidente en su historial.

Fuente: [Política de Citas y Cancelaciones — Política de cancelación]

### app-02 — ¿A un paciente de Medicare se le cobra si no se presenta a la cita?

_Chunks sobre el umbral: appointment-policy#1 (0.58), appointment-policy#3 (0.52), appointment-policy#2 (0.42), appointment-policy#0 (0.38), new-patient-checklist#2 (0.37)_

No, a los pacientes de Medicare no se les cobra el cargo por no-show. Según la política, está prohibido cobrarles, pero se debe registrar el incidente en su historial.

Fuente: Política de Citas y Cancelaciones — Política de cancelación

### app-03 — ¿Cuántos días hay que esperar para una cita de rutina?

_Chunks sobre el umbral: appointment-policy#0 (0.61), appointment-policy#3 (0.50), referral-process#1 (0.45), appointment-policy#2 (0.44), referral-process#2 (0.42)_

La disponibilidad promedio para una cita de rutina es de 3 a 5 días. Si el paciente necesita atención más urgente, puede consultar por una cita del mismo día, sujeta a disponibilidad del médico de turno.

Fuente: Política de Citas y Cancelaciones — Reserva de citas

### app-04 — ¿Hasta cuándo puede un paciente cambiar su cita desde el recordatorio?

_Chunks sobre el umbral: appointment-policy#2 (0.69), appointment-policy#3 (0.49), appointment-policy#1 (0.46), appointment-policy#0 (0.43), new-patient-checklist#0 (0.33)_

El paciente puede reprogramar su cita directamente desde el enlace del recordatorio hasta 4 horas antes de la hora programada. Fuente: Política de Citas y Cancelaciones — Recordatorios automáticos

### app-05 — Un paciente ha faltado tres veces este semestre, ¿le puedo agendar otra cita normal?

_Chunks sobre el umbral: appointment-policy#3 (0.55), appointment-policy#0 (0.44), appointment-policy#2 (0.40), new-patient-checklist#2 (0.37), appointment-policy#1 (0.35)_

Si el paciente ha tenido 3 no-shows en los últimos 6 meses, antes de agendar una nueva cita de rutina debe ser marcado para revisión por el coordinador. Esto no aplica a citas urgentes. Por favor, revisa su historial y procede según la política.

Fuente: Política de Citas y Cancelaciones — Política de Citas y Cancelaciones

### ref-01 — ¿Cómo funciona una referencia a un especialista dentro de la red?

_Chunks sobre el umbral: referral-process#3 (0.59), referral-process#0 (0.58), referral-process#2 (0.41), referral-process#1 (0.33)_

Cuando un médico de atención primaria refiere a un paciente a un especialista dentro de HealthCore, el proceso es: el médico crea la referencia en el sistema, el sistema la enruta a la clínica con especialista disponible más cercana a la ubicación preferida del paciente, el paciente recibe notificación con opciones de cita, y al confirmar, la clínica receptora recibe automáticamente un resumen del historial relevante. Si el paciente no recibe opciones en 5 días hábiles, el coordinador debe escalar el caso a Marcus Reid (Director de Operaciones Clínicas).

Fuente: Proceso de Referencia Interna — Proceso de Referencia Interna

### ref-02 — ¿Cuánto tarda en promedio una referencia interna hasta tener la cita?

_Chunks sobre el umbral: referral-process#1 (0.77), referral-process#2 (0.71), referral-process#3 (0.59), referral-process#0 (0.53), appointment-policy#0 (0.46)_

El tiempo promedio actual para completar una referencia interna, desde su creación hasta que la cita queda confirmada, es de 11 días. Este es un promedio que buscamos reducir, no un plazo de servicio garantizado. Si el paciente no recibe opciones de cita en 5 días hábiles, debe escalarse el caso a Marcus Reid, Director de Operaciones Clínicas.

Fuente: Proceso de Referencia Interna — Tiempo objetivo de referencia completada; Proceso de Referencia Interna — Proceso de Referencia Interna

### ref-03 — Pasaron 6 días hábiles y el paciente no tiene opciones de cita con el especialista, ¿qué hago?

_Chunks sobre el umbral: referral-process#2 (0.50), appointment-policy#0 (0.49), appointment-policy#3 (0.48), appointment-policy#1 (0.41), appointment-policy#2 (0.38)_

Debes escalar el caso directamente a Marcus Reid, Director de Operaciones Clínicas, ya que han pasado más de 5 días hábiles sin que el paciente reciba opciones de cita.

Fuente: Proceso de Referencia Interna — Proceso de Referencia Interna

### ref-04 — ¿Cómo se gestiona una referencia a un especialista que no es de HealthCore?

_Chunks sobre el umbral: referral-process#3 (0.69), referral-process#0 (0.67), referral-process#2 (0.45), insurance-coverage#1 (0.41), insurance-coverage#0 (0.36)_

Las referencias a especialistas fuera de la red HealthCore no siguen el proceso automatizado; requieren gestión manual y pueden tardar significativamente más. No hay pasos documentados adicionales en la base de conocimiento para este tipo de derivación.

Fuente: Proceso de Referencia Interna — Proceso de Referencia Interna

### new-01 — ¿Qué debo traer a mi primera cita?

_Chunks sobre el umbral: new-patient-checklist#1 (0.57), new-patient-checklist#0 (0.51), new-patient-checklist#2 (0.37)_

Para su primera cita, debe traer un documento de identidad válido, su tarjeta de seguro (si aplica) y, si el motivo de consulta lo amerita, una lista de sus medicamentos actuales. Además, recuerde que debe completar el formulario de historial médico antes de la cita, ya sea en el portal o en papel en recepción; si no lo hace, el tiempo de consulta se reducirá porque el médico lo completará durante la visita.

Fuente: Checklist para Pacientes Nuevos — Documentos que el paciente debe traer el día de la cita; Checklist para Pacientes Nuevos — Todo paciente nuevo debe completar antes de su primera cita; Checklist para Pacientes Nuevos — Checklist para Pacientes Nuevos

### new-02 — ¿Qué tiene que completar un paciente nuevo antes de su primera consulta?

_Chunks sobre el umbral: new-patient-checklist#0 (0.76), new-patient-checklist#2 (0.70), new-patient-checklist#1 (0.59), appointment-policy#3 (0.36), referral-process#0 (0.35)_

Antes de la primera consulta, el paciente nuevo debe completar cuatro pasos: el formulario de historial médico (en el portal o en papel), la verificación de identidad con documento oficial, la verificación de cobertura de seguro o confirmación de pago self-pay, y firmar el consentimiento informado de tratamiento de datos (usando el formulario correspondiente al país de la clínica: HIPAA en EE. UU. o UK GDPR en Reino Unido). Si no completa el historial antes, igual puede ser atendido, pero el tiempo de consulta se reducirá.

Fuente: Checklist para Pacientes Nuevos — Todo paciente nuevo debe completar antes de su primera cita; Checklist para Pacientes Nuevos — Checklist para Pacientes Nuevos

### new-03 — ¿Qué formulario de consentimiento firma un paciente nuevo en Manchester?

_Chunks sobre el umbral: new-patient-checklist#0 (0.52), insurance-coverage#1 (0.47), new-patient-checklist#2 (0.38), new-patient-checklist#1 (0.33)_

En el Reino Unido, el consentimiento informado que debe firmar un paciente nuevo es el formulario de consentimiento de tratamiento de datos bajo la normativa UK GDPR. El coordinador debe asegurarse de usar el formulario correspondiente al país de la clínica.

Fuente: Checklist para Pacientes Nuevos — Todo paciente nuevo debe completar antes de su primera cita

### new-04 — Si un paciente nuevo no llenó el historial médico, ¿lo pueden atender igual?

_Chunks sobre el umbral: new-patient-checklist#2 (0.74), new-patient-checklist#0 (0.60), new-patient-checklist#1 (0.46), appointment-policy#3 (0.37), referral-process#0 (0.35)_

Sí, un paciente nuevo que no haya completado el formulario de historial médico puede ser atendido igualmente, pero el tiempo de consulta se verá reducido porque el médico deberá llenarlo durante la cita.

Fuente: Checklist para Pacientes Nuevos — Checklist para Pacientes Nuevos

### una-01 — ¿Cuánto cuesta una resonancia magnética?

_Chunks sobre el umbral: ninguno_

La base de conocimiento no tiene información sobre el costo de una resonancia magnética. Para obtener una cotización precisa, por favor consulta con el equipo de facturación (Tom Callahan), quien podrá orientarte según la ubicación y el tipo de seguro del paciente.

Fuente: ninguna

### una-02 — ¿Aceptan el seguro Humana en Texas?

_Chunks sobre el umbral: insurance-coverage#0 (0.54), insurance-coverage#2 (0.31), insurance-coverage#1 (0.31)_

Humana no aparece en la lista de seguros comerciales aceptados en Estados Unidos. Para Texas, los seguros documentados son Blue Cross Blue Shield, Aetna, Cigna y UnitedHealthcare, además de Medicare y Medicaid (este último solo en Texas y Florida). Antes de confirmar cualquier cobertura, debe verificarlo con el equipo de facturación (Tom Callahan).

Fuente: Cobertura de Seguros Aceptados — Estados Unidos (Texas, Florida, Georgia); Cobertura de Seguros Aceptados — Cobertura de Seguros Aceptados

### una-03 — ¿Las clínicas tienen estacionamiento gratuito?

_Chunks sobre el umbral: appointment-policy#0 (0.35), insurance-coverage#1 (0.32)_

La base de conocimiento no tiene información sobre estacionamiento gratuito en las clínicas. Para resolver esta duda, sugiero consultar directamente con la supervisión de Patient Experience (Priya Nair).

Fuente: ninguna

### off-01 — ¿Cuál es la receta de la paella valenciana?

_Chunks sobre el umbral: ninguno_

La base de conocimiento no tiene información sobre la receta de la paella valenciana. Para consultas culinarias o de otro tipo no relacionadas con nuestros servicios clínicos, no podemos proporcionar orientación. Si necesita ayuda con algún tema de HealthCore, por favor indíquelo con gusto lo asistiré.

Fuente: ninguna

### off-02 — ¿Quién ganó el último mundial de fútbol?

_Chunks sobre el umbral: ninguno_

La base de conocimiento no tiene información sobre el ganador del último mundial de fútbol. Para este tipo de consultas, por favor dirígete a la supervisión de Patient Experience (Priya Nair), quien podrá orientarte sobre cómo proceder.

Fuente: ninguna

### off-03 — ¿Cómo configuro la impresora de la oficina?

_Chunks sobre el umbral: ninguno_

La base de conocimiento no tiene información sobre cómo configurar la impresora de la oficina. Para resolver este tipo de temas técnicos, sugiero consultar con la supervisión de Patient Experience, Priya Nair, quien podrá orientarlos o derivarlos al equipo adecuado.

Fuente: ninguna

### off-04 — Recomiéndame una película para el fin de semana.

_Chunks sobre el umbral: ninguno_

La base de conocimiento no tiene información sobre recomendaciones de películas. Para consultas de entretenimiento o temas no relacionados con los servicios de HealthCore, no contamos con datos documentados. Si necesitas ayuda con algún proceso de la clínica, puedo orientarte; de lo contrario, te sugiero consultar con tu equipo de supervisión para cualquier otra necesidad.

Fuente: ninguna
