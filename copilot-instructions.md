# Instrucciones del proyecto — HealthCore Talent Pipeline Tracker

Para el hito de gestión de candidaturas, usa `CONTEXT.md` y `SPEC.md` como fuente de verdad. La aplicación está en `uis/talent-pipeline-tracker/` y usa Next.js App Router, React y TypeScript.

- Conserva exactamente los nombres de campos y valores del API documentados en el contexto.
- Usa componentes/hooks locales; no añadas un gestor de estado externo.
- Organiza tipos en `types/`, cliente REST en `lib/`, UI en `components/` y rutas en `app/`.
- Toda petición debe exponer carga, éxito y error y actualizar la interfaz sin recarga completa.
- No inventes reglas o datos de selección que no estén en el contexto o contrato del API.
- Mantén accesibilidad de teclado y diseño responsive.
- Verifica con `npm run lint` y `npm run build` desde la carpeta de la app.