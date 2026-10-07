# Arquitectura de DocOps — Documentación técnica viva, global y versionada

Este documento explica **cómo funciona de punta a punta** el sistema que mantiene
actualizada en Confluence la documentación de todos los repositorios de la
organización (`acacoop`), y cierra con un **paso a paso reproducible** usando
un repo real como ejemplo.

Para el detalle de archivos y convenciones de carpetas, ver `docops/README.md`.
Este documento se enfoca en la arquitectura y el flujo completo, no en cada
script en particular.

## 1. Objetivo

Mantener, para **cada repositorio** de la organización, una página de
Confluence con 5 bloques de documentación que se actualizan solos cuando
corresponde, combinando dos orígenes de datos distintos:

| Origen del dato | Qué contiene | Quién lo escribe | Qué lo actualiza |
|---|---|---|---|
| **Escaneo real del repo** | Lenguaje, framework, runtime, gestor de paquetes, bases de datos, Terraform | `scan_code.py` / `scan_terraform.py` (este pipeline) | Push a `main` que toca código o `*.tf` |
| **Negocio e infraestructura declarada** | Información que no se puede inferir del código (dueños, criticidad, ambientes, tickets de infra) | Equipo de negocio / integración con Jira | Al completar `docs/manifest/*.yml` |

La regla de oro del diseño: **el agente nunca sobreescribe lo que no generó
él mismo**. Lee `docs/manifest/` para publicarlo, pero jamás escribe ahí.

## 2. Arquitectura general

```
┌─────────────────────────────┐        ┌──────────────────────────────────┐
│  Repo de la organización     │        │  acacoop/DevSecOps-Templates      │
│  (ej. "alta-de-cuenta")      │        │  (este repo — fuente única)       │
│                               │        │                                    │
│  docs/manifest/*.yml  ◄───── │ Jira   │  docops/scripts/*.py              │
│  docs/generated/*.yml        │        │   - scan_code.py                  │
│  infra/**/*.tf                │        │   - scan_terraform.py             │
│  .github/workflows/           │        │   - yaml_to_confluence.py         │
│    docops-sync.yml   ────────┼───────►│   - confluence_publish.py         │
│    docops-preview.yml ───────┼──uses──│   - confluence_fingerprint.py     │
└───────────────┬───────────────┘        │                                    │
                │ push a main             │  .github/workflows/                │
                ▼                         │    docops-sync.yml (reusable)      │
        GitHub Actions                    │    docops-preview.yml (reusable)   │
        (checkout doble:                 │    org-bootstrap-docops.yml        │
         repo + templates@main)          └──────────────────────────────────┘
                │
                ▼
        Confluence (1 página raíz + 5 subpáginas versionadas por repo)
```

Puntos clave de este diseño:

- **Un solo lugar con la lógica**: ningún repo consumidor tiene sus propios
  scripts. El workflow hace un segundo `checkout` de `DevSecOps-Templates`
  en tiempo de ejecución, así que corregir un script acá se propaga
  automáticamente a los ~59 repos en su próxima corrida, sin tocarlos.
- **Workflows reusables (`workflow_call`)**: cada repo solo tiene un archivo
  fino que invoca `acacoop/DevSecOps-Templates/.github/workflows/docops-sync.yml@main`
  pasando sus propios secrets de Confluence.
- **Versionado inteligente por huella (fingerprint)**: antes de publicar
  cualquier bloque, se calcula un hash del contenido fuente relevante y se
  compara contra el último hash publicado (guardado como *content property*
  de la página en Confluence). Si no cambió, se omite la publicación — así
  no se generan versiones vacías en Confluence por cada push.
- **Dos momentos separados: preview vs. publish**. Un PR nunca escribe en
  Confluence (podría cerrarse sin mergear); solo el push a `main` publica de
  verdad.
- **Rollout global sin intervención manual**: un workflow de reconciliación
  (`org-bootstrap-docops.yml`) detecta repos nuevos y les abre un PR con el
  scaffolding, en vez de depender de que alguien lo instale a mano.

## 3. Los 5 bloques de documentación

Cada repo termina con una página raíz (nombre del repo) y estas 5 subpáginas,
cada una con su propio versionado independiente en Confluence:

| # | Página | Origen | Dispara con |
|---|---|---|---|
| 1 | `1. Negocio` | `docs/manifest/negocio.yml` (Jira) | Cambios en ese YAML |
| 2 | `2. Desarrollo` | Escaneo real (`scan_code.py`) | Push a `main` con cambios de código (excluye `docs/`, `infra/`, `.github/`) |
| 3 | `3. Infraestructura declarada` | `docs/manifest/infraestructura-declarada.yml` (Jira) | Cambios en ese YAML |
| 4 | `4. Infraestructura relevada` | Escaneo real (`scan_terraform.py`) | Push a `main` con cambios en `**/*.tf` |
| 5 | `5. Modelado de amenaza (borrador)` | `docs/generated/amenazas.yml` | Cambios en ese YAML |

El título real en Confluence lleva el prefijo del repo (ej.
`alta-de-cuenta - 2. Desarrollo`) para que no colisionen entre repos distintos
dentro del mismo espacio de Confluence.

## 4. Flujo paso a paso (motor interno de `docops-sync.yml`)

Por cada uno de los 5 bloques, el workflow reusable repite siempre la misma
secuencia:

1. **Calcular huella local**: `compute_fingerprint.py` hashea (sha256) los
   archivos relevantes para ese bloque específico (ej. solo `*.tf` para el
   bloque 4).
2. **Leer huella remota**: `confluence_fingerprint.py --get` consulta la
   *content property* guardada en la página de Confluence la última vez que
   se publicó.
3. **Comparar**:
   - Si son iguales → se loguea "sin cambios" y se corta ahí (no se gasta
     una versión nueva de Confluence por nada).
   - Si difieren (o no hay huella remota todavía) → se continúa.
4. **Generar contenido**:
   - Bloques 2 y 4: corren el escaneo real (`scan_code.py` /
     `scan_terraform.py`) sobre el checkout del repo, producen un YAML.
   - Bloques 1, 3 y 5: toman el YAML ya existente en `docs/manifest/` o
     `docs/generated/` tal cual.
5. **Convertir a HTML**: `yaml_to_confluence.py` transforma ese YAML en el
   formato *storage* de Confluence (tablas, encabezados), siguiendo el
   template de esa sección.
6. **Publicar**: `confluence_publish.py` crea la página si no existe, o la
   actualiza (nueva versión) si ya existía, siempre debajo de la página raíz
   del repo.
7. **Guardar la nueva huella**: `confluence_fingerprint.py --set` graba el
   hash recién publicado, para que la próxima corrida pueda compararse
   contra este estado.

`docops-preview.yml` ejecuta los pasos 1 a 3 (en modo solo lectura) para
cada bloque y deja un comentario en el PR del tipo "esto cambiaría si se
mergea" — nunca llega a los pasos 4-7.

## 5. Rollout global automático

Para que esto sea "automático para cada repo que se cree" sin depender de
que alguien recuerde copiar archivos, existe `org-bootstrap-docops.yml`:

1. Corre `bootstrap_org_repos.sh`, que lista todos los repos de la
   organización (excluyendo forks, templates y el propio
   `DevSecOps-Templates`).
2. Para cada repo que **no tiene todavía** `docops-sync.yml`, clona el repo,
   agrega el workflow fino + el scaffolding de `docs/manifest` y
   `docs/generated`, y **abre un Pull Request** (nunca hace push directo a
   `main` de un repo ajeno).
3. Es idempotente: los repos que ya tienen el workflow se saltean.
4. Corre semanalmente por `schedule` (además de poder dispararse manual con
   `dry_run`), así los repos creados después de la fecha de rollout inicial
   también reciben su PR de bootstrap sin intervención manual.

Requiere un secret de organización `ORG_BOOTSTRAP_TOKEN` (PAT fine-grained
con `Contents`, `Pull requests` y `Workflows: write`), porque el
`GITHUB_TOKEN` automático de Actions solo tiene permisos sobre el repo donde
corre, no sobre el resto de la organización.

## 6. Paso a paso con ejemplo real: `alta-de-cuenta`

Esta sección muestra el flujo completo de punta a punta usando un repo real
ya onboardeado como ejemplo.

### Paso 1 — Estado inicial del repo

`alta-de-cuenta` es una colección de scripts de PowerShell
(`Correo.ps1`, `UsuarioAD.ps1`, etc.). Tiene instalado, vía el PR de
bootstrap ya mergeado:

```
alta-de-cuenta/
├── .github/workflows/docops-sync.yml   # invoca el workflow reusable
├── docs/manifest/negocio.yml
├── docs/manifest/infraestructura-declarada.yml
├── docs/generated/amenazas.yml
└── Correo.ps1, UsuarioAD.ps1, ...
```

### Paso 2 — Disparador: push a `main`

Un cambio se mergea a `main` (en este ejemplo, hasta un commit vacío sirve
para forzar la corrida):

```powershell
git commit --allow-empty -m "actualizo scripts de automatización"
git push origin main
```

### Paso 3 — GitHub Actions ejecuta `docops-sync.yml`

El workflow fino del repo invoca al reusable de `DevSecOps-Templates@main`,
que hace el doble checkout (repo + scripts) y corre los 5 bloques descritos
en la sección 4.

### Paso 4 — Bloque "2. Desarrollo": escaneo real

Como no cambió nada en `docs/manifest` ni en `*.tf`, los bloques 1, 3, 4 y 5
se omiten ("sin cambios"). El bloque 2 sí corre porque cambió código:

- `scan_code.py` recorre el repo y detecta por extensión que el lenguaje
  dominante es **PowerShell** (`.ps1`), con evidencia en `Correo.ps1`.
- Como no hay ningún framework de servicio/API, pero sí un lenguaje de
  scripting dominante, clasifica el tipo de aplicación como
  **"Script de automatización"**.
- Genera `desarrollo.yml` con esos campos completos (en vez de "No
  determinado desde el repositorio").

### Paso 5 — Conversión y publicación

- `yaml_to_confluence.py` convierte ese YAML a HTML con el formato de la
  página.
- `confluence_publish.py` detecta que la página
  `alta-de-cuenta - 2. Desarrollo` ya existe y la actualiza (ej. v1 → v2),
  en vez de crear una duplicada.
- `confluence_fingerprint.py --set` guarda la nueva huella para la próxima
  comparación.

### Paso 6 — Resultado visible en Confluence

```
docops-sandbox (o alta-de-cuenta)      <- página raíz
├── alta-de-cuenta - 1. Negocio
├── alta-de-cuenta - 2. Desarrollo     <- v2, con PowerShell / Script de automatización
├── alta-de-cuenta - 3. Infraestructura declarada
├── alta-de-cuenta - 4. Infraestructura relevada
└── alta-de-cuenta - 5. Modelado de amenaza (borrador)
```

### Paso 7 — Verificación (cómo se comprobó)

```powershell
gh run list --repo acacoop/alta-de-cuenta --workflow=docops-sync.yml --limit 1
gh run view <run-id> --repo acacoop/alta-de-cuenta --log | Select-String "Actualizando página"
```

Confirmando en el log la línea:

```
Actualizando página existente: 'alta-de-cuenta - 2. Desarrollo' (v1 -> v2)
```

## 7. Cómo se extiende (agregar un stack nuevo)

Cuando `scan_code.py` no reconoce un stack (ej. .NET, Java/Maven, Go), el
campo correspondiente queda `null` por diseño ("no inventar valores"), lo
que se renderiza como "No determinado desde el repositorio". Para cubrirlo:

1. Agregar la extensión de archivo a `LANGUAGE_BY_EXTENSION`.
2. Si corresponde, agregar detección de versión (ej. vía el manifiesto de
   dependencias propio del stack: `.csproj`, `pom.xml`, `go.mod`).
3. Si corresponde, ampliar `detect_app_type()` con la heurística de
   clasificación (servicio/API, script, librería, etc.).
4. Validar localmente clonando un repo real con ese stack y corriendo
   `scan_code.py --root <clon> --out <tmp.yml> --template <ruta absoluta a desarrollo.yml>`.
5. Commitear y pushear a `main` de `DevSecOps-Templates` — se propaga solo
   a todos los repos en su próxima corrida.

## 8. Resumen de garantías de diseño

- **No rompe repos existentes**: el bootstrap solo abre PRs, nunca hace
  push directo a `main` de un repo ajeno.
- **No genera ruido en Confluence**: el versionado por huella evita publicar
  cuando no hubo cambios reales.
- **No mezcla orígenes de datos**: el agente nunca escribe en
  `docs/manifest/` (eso es territorio de Jira/negocio).
- **Un solo punto de mantenimiento**: toda mejora al escaneo vive en
  `DevSecOps-Templates` y se hereda automáticamente por los ~59 repos.
- **Honesto sobre lo que no sabe**: un campo sin evidencia concreta en el
  repo se marca explícitamente como "No determinado", nunca se infiere o
  inventa.
