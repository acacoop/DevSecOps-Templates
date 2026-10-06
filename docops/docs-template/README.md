# Documentación técnica automatizada (DocOps)

Esta carpeta alimenta la página de Confluence de este proyecto. Se divide por
**origen del dato**, no por tipo de contenido, porque eso es lo que determina
quién puede escribir cada archivo y qué dispara su actualización.

| Carpeta | Quién escribe | Disparador |
|---|---|---|
| `manifest/` | Integración con Jira (pipeline aparte) | Al aprobar iniciativa / CHANGE / despliegue de infra |
| `generated/` | Agente de documentación (este pipeline) | Push a `main` (código o `*.tf`) |

**Regla de oro:** el agente de documentación (`docs-sync.yml`) **nunca escribe
en `manifest/`**. Solo lee esos archivos para inyectarlos en Confluence.

Ver `confluence-mapping.yml` para el detalle de qué archivo va a qué página.
