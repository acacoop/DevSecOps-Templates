# DevSecOps-Templates

Repositorio central (`acacoop/DevSecOps-Templates`) donde vive toda la
lógica **reusable** de DevSecOps de la organización: pipelines de seguridad,
plantillas de workflows, y el proyecto **DocOps** (documentación técnica
viva, versionada en Confluence).

Ningún repo de la organización necesita copiar/pegar scripts: todos invocan
los workflows reusables de acá con `uses: acacoop/DevSecOps-Templates/...@main`,
así que actualizar algo en este repo se propaga automáticamente a los ~80
repos que lo consumen, sin tocar nada en cada uno de ellos.

## ¿Dónde vive cada cosa?

| Qué | Dónde | Qué hace |
| --- | --- | --- |
| **DocOps** (documentación viva) | [`docops/`](docops/README.md) | Escanea cada repo en cada push a `main` y mantiene su documentación actualizada y versionada en Confluence (desarrollo, infraestructura, negocio, amenazas). |
| **Rollout global** | [`.github/workflows/org-bootstrap-docops.yml`](.github/workflows/org-bootstrap-docops.yml) | Detecta repos de la organización sin DocOps instalado y les abre un Pull Request automático. Corre semanalmente (lunes 13:00 UTC) para cubrir repos nuevos sin intervención manual. |
| **Workflow reusable que publica en Confluence** | [`.github/workflows/docops-sync.yml`](.github/workflows/docops-sync.yml) | Llamado por cada repo consumidor en su propio `push` a `main`. Es la única pieza que efectivamente escribe en Confluence. |
| **Scripts del pipeline** (escaneo, IA, publicación) | [`docops/scripts/`](docops/scripts/) | Python puro, sin nada hardcodeado de ningún repo puntual. |
| **Scaffolding para un repo nuevo** | [`docops/docs-template/`](docops/docs-template/) | Lo que el bootstrap copia dentro de cada repo (workflow consumidor + carpetas `docs/manifest` y `docs/generated`). |

## Resumen del automatismo (DocOps)

1. **Instalación (una vez por repo)**: el repo recibe un PR (manual o vía
   el bootstrap automático) que agrega un workflow fino que solo invoca
   el pipeline reusable de este repo central.
2. **Fase 1 — Relevamiento inicial**: al mergear ese PR a `main`, el
   pipeline corre por primera vez, escanea el repo completo (código,
   Terraform, manifiestos de negocio) y publica las 5 páginas de
   documentación en Confluence.
3. **Fase 2 — Mantenimiento incremental**: en cada push a `main` siguiente,
   el pipeline calcula una huella (hash) del contenido relevante y la
   compara contra la última publicada. **Solo si cambió algo real**
   (código, Terraform, o los YAML de negocio/infraestructura declarada)
   regenera y publica una nueva versión en Confluence — evita ruido y
   versiones vacías.
4. **Enriquecimiento con IA**: los campos que el escaneo determinístico no
   puede completar con evidencia concreta (ej. "Roles y responsabilidades",
   "Integraciones") se intentan completar con GitHub Copilot CLI, siempre
   citando evidencia real del repo, nunca inventando.
5. **Escala a toda la organización**: no depende de que cada equipo
   recuerde instalarlo — el workflow de bootstrap corre solo cada semana y
   abre el PR en cualquier repo nuevo automáticamente.

Para el detalle técnico completo (arquitectura, scripts, cómo configurar
tokens, troubleshooting), ver **[`docops/README.md`](docops/README.md)**.
