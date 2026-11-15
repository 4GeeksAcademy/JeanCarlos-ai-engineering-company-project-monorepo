# Carpeta `uis`

Esta carpeta contiene **todos los proyectos con interfaz de usuario** para el proyecto transversal de AI Engineering de la compañía — por ejemplo: un sitio web público, un frontend de panel de administración, una interfaz de ecommerce, portales para clientes, aplicaciones Streamlit/Gradio u otras herramientas sólo-frontend.

Los dos proyectos principales que se almacenan aquí son:

- **`website`** — la presencia web pública de la compañía.
- **`backoffice`** — la aplicación interna de administración. Es el lugar ideal para desarrollar múltiples soluciones dentro de un mismo proyecto: autenticación, gestión de personas, gestión de operaciones, comunicación interna y otras capacidades de back-office.

Organiza `uis/` por **distintas áreas de la compañía** — cada subcarpeta agrupa un ámbito diferente (por ejemplo, web pública frente a operaciones internas) e incluye su propia documentación técnica y funcional.

- **Propósito principal**: centralizar en un único lugar todas las aplicaciones frontend que dan soporte a los casos de uso de la compañía.
- **Recomendación**: documenta en este archivo (o en sub-READMEs) las aplicaciones que vayas añadiendo, su objetivo, tecnología usada y cómo ejecutarlas.

> _These instructions are also available in [English](./README.md)._

## HealthCore Backoffice — Supplier Directory

Aplicación independiente en `backoffice/`, sin cambios en Talent Pipeline Tracker.
Requiere Node.js 20.9 o posterior y la API de proveedores en ejecución
(ver [instrucciones de backend](../services/README.es.md#healthcore-supplier-directory)).

```bash
cd uis/backoffice
npm install
cp .env.example .env.local
npm run dev
```

Abrir <http://localhost:3001/suppliers>. Permite filtrar por país/categoría,
registrar proveedores, editar tarifas y activar/suspender cada fila sin recargar
la página. Los estados de carga, éxito y error son visibles.

`SUPPLIERS_API_URL` configura el destino del proxy Next.js (por defecto
`http://127.0.0.1:8000`); no es una variable pública del navegador.
Las peticiones usan `/api/suppliers` en el mismo origen, sin necesitar CORS.
Configurar la variable antes de `npm run build` y reconstruir si cambia el destino.

Validación desde `uis/backoffice/`:

```bash
npm run lint
npm run build
```
