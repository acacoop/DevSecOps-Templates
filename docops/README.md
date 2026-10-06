# DocOps — Documentación técnica viva en Confluence

Este directorio contiene el pipeline **reusable** que escanea cada
repositorio de la organización en cada push a `main` y mantiene actualizada
(y versionada) su documentación técnica en Confluence, combinando:

- Contenido **generado automáticamente** por escaneo real del repo
  (lenguaje, framework, runtime, Terraform) — `docops/scripts/scan_*.py`.
- Contenido de **negocio e infraestructura declarada**, completado por la
  integración con Jira (ver `docs/manifest/` en cada repo consumidor) — este
  pipeline solo lo lee y publica, nunca lo sobreescribe.

Prototipado y validado end-to-end en el repo sandbox `acacoop/docops-sandbox`
antes de centralizarse acá.

## Estructura de este directorio

```
docops/
├── scripts/                      # Lógica Python del pipeline (genérica, sin
│                                  # nada hardcodeado de ningún repo puntual)
│   ├── confluence_publish.py     # crear/actualizar páginas versionadas
│   ├── confluence_fingerprint.py # leer/escribir la huella (content property)
│   ├── fingerprint_utils.py      # cálculo de huella (sha256 sobre archivos)
│   ├── compute_fingerprint.py    # CLI fina sobre fingerprint_utils
│   ├── scan_code.py              # escaneo real de código de aplicación
│   ├── scan_terraform.py         # escaneo real de infraestructura (*.tf)
│   └── yaml_to_confluence.py     # conversor genérico YAML -> HTML storage
└── docs-template/                 # scaffolding para copiar a un repo NUEVO
    ├── manifest/                  # (solo Jira escribe acá)
    ├── generated/                 # (solo el agente escribe acá)
    ├── README.md
    ├── consumer-workflow-example.yml           # publica en Confluence (push a main)
    └── consumer-preview-workflow-example.yml    # comenta en PRs (no publica)
```

## Dos workflows, dos momentos distintos

- **`docops-sync.yml`** (push a main) = la única fuente de verdad que
  escribe en Confluence. Garantiza que lo publicado siempre refleja el
  estado real de `main`.
- **`docops-preview.yml`** (cualquier PR contra main) = solo lee las
  huellas ya publicadas y compara contra el contenido del PR, dejando un
  comentario "esto cambiaría si se mergea". Nunca escribe en Confluence.

Se decidió explícitamente NO publicar directo desde un PR (por ejemplo, de
`test` a `main`): un PR puede quedar abierto días, pedir cambios, o
cerrarse sin mergear, y publicar en ese momento haría que la documentación
"mienta" sobre el estado real de producción. El merge del PR genera igual
un push a `main`, que es el que dispara la publicación real.

## Cómo lo usa un repo consumidor

El repo consumidor NO tiene sus propios scripts. Solo tiene:

1. `.github/workflows/docops-sync.yml` — una copia de
   `docops/docs-template/consumer-workflow-example.yml`, que invoca
   `acacoop/DevSecOps-Templates/.github/workflows/docops-sync.yml@main` con
   `uses:` (workflow reusable, `workflow_call`).
2. `docs/manifest/*.yml` y `docs/generated/*.yml` — una copia de
   `docops/docs-template/{manifest,generated}/`.
3. Terraform (opcional) bajo `infra/` en la raíz del repo — convención usada
   por `scan_terraform.py` y por el fingerprint de "Infraestructura
   relevada" para excluirlo del fingerprint de "Desarrollo".

El workflow reusable hace un **segundo checkout** de
`acacoop/DevSecOps-Templates` dentro del job (carpeta `.docops-templates/`)
para traer los scripts, y corre todo contra el checkout del repo que lo
invoca (`github.workspace`). Así, actualizar un script acá (ej. agregar un
framework nuevo a `scan_code.py`) se propaga automáticamente a todos los
repos la próxima vez que corran, sin tocar nada en cada repo individual.

## Aplicación global: rollout automático a toda la organización

La forma elegida para cumplir "que esto sea automático y global para cada
repo que se cree" es un **workflow de reconciliación** (no depende de que
alguien elija un template al crear el repo): `org-bootstrap-docops.yml`.

### Cómo funciona

1. Corre `docops/scripts/bootstrap_org_repos.sh`, que lista TODOS los repos
   de la organización (excluye forks, templates, y el propio
   `DevSecOps-Templates`).
2. Para cada repo que **todavía no tiene**
   `.github/workflows/docops-sync.yml`, clona el repo, agrega ese workflow
   + el scaffolding de `docs/manifest` y `docs/generated`, y **abre un Pull
   Request** (nunca hace push directo a `main` de un repo ajeno — el equipo
   dueño decide cuándo mergearlo, evitando romper un `docs/` que ya exista
   con otro propósito).
3. Los repos que ya tienen el workflow se saltean (idempotente: correrlo de
   nuevo no duplica PRs ni pisa nada).

### Cómo activarlo

1. Generar un **Personal Access Token (fine-grained)** con acceso a todos
   los repos de la organización y permisos: `Contents: write`,
   `Pull requests: write`, `Workflows: write`, `Metadata: read`.
   (El `GITHUB_TOKEN` automático de Actions NO sirve para esto: solo tiene
   permisos sobre el repo donde corre el workflow, no sobre el resto de la
   organización — por eso hace falta un PAT explícito).
2. Cargarlo como **organization secret** `ORG_BOOTSTRAP_TOKEN` (accesible
   al menos por este repo).
3. Correr el workflow manualmente (`workflow_dispatch`) con
   `dry_run: true` primero, para ver en los logs QUÉ repos se tocarían,
   sin escribir nada todavía.
4. Si la lista es la esperada, volver a correrlo con `dry_run: false` para
   abrir los PRs de verdad.
5. (Opcional, más adelante) Descomentar el `schedule:` del workflow para
   que esto corra solo, por ejemplo, una vez por semana, y así los repos
   creados después también terminen recibiendo su PR de bootstrap sin
   intervención manual.

### Recomendación adicional: secrets de Confluence a nivel organización

Para que un repo nuevo no tenga que cargar los 4 secrets de Confluence a
mano, conviene cargarlos UNA sola vez como **organization secrets**
(Settings → Secrets and variables → Actions → "New organization secret"),
con política de acceso "All repositories" o una lista explícita. Así, el
único paso manual que le queda a un equipo nuevo es completar
`docs/manifest/*.yml` (o esperar a la integración con Jira) — todo lo demás
ya está resuelto por el PR de bootstrap + los secrets heredados de la
organización.
