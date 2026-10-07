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
│   ├── ai_enrich.py              # enriquecimiento opcional con IA (ver abajo)
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

## Enriquecimiento con IA (opcional, activado por default)

El escaneo determinístico (`scan_code.py` / `scan_terraform.py`) solo
completa un campo cuando encuentra evidencia inequívoca en un archivo del
repo (versión en un manifiesto, motor de base de datos en un `docker-compose`,
recurso en un `.tf`, etc.). Hay secciones enteras que son, por naturaleza,
narrativas — "Funciones esenciales", "Roles y responsabilidades",
"Integración y dependencias", "Procesos tecnológicos clave" — que un regex
no puede completar con confianza y que históricamente quedaban siempre en
*"No determinado desde el repositorio"*.

`ai_enrich.py` agrega una capa de IA (**GitHub Copilot CLI**, invocado como
subproceso vía `copilot -p`) que corre DESPUÉS del escaneo determinístico,
tanto para "2. Desarrollo" como para "4. Infraestructura relevada", con
reglas estrictas que preservan la filosofía de "nunca inventar":

- Solo mira los campos que **siguen en `null`** tras el escaneo. Nunca
  pisa un valor que ya vino del regex/manifiesto.
- Al modelo se le manda como única evidencia el **contenido real** de
  archivos del repo (README, manifiestos, `*.tf`, etc.), con instrucciones
  explícitas de responder `null` si no hay evidencia.
- Todo campo completado que necesita una referencia a evidencia (`donde`,
  `archivo`, `evidencia`, `origen`, etc.) se **valida contra la lista real
  de archivos del repo**: si el modelo cita un path que no existe, se
  descarta ese campo (o ese registro completo), no se publica.
- Es **best-effort y no bloqueante**: si falla por cualquier motivo (sin
  token, CLI no instalada, rate limit, timeout), el paso se omite y el
  resto del pipeline sigue igual — los campos sin evidencia simplemente
  quedan en null, como siempre funcionó.
- Se puede desactivar por repo con `ai_enrichment: false` en el `with:`
  del `docops-sync.yml` del repo consumidor.

> **Nota histórica:** la primera versión de este mecanismo usaba GitHub
> Models (API HTTP separada de Copilot). GitHub Models fue retirado por
> completo el 30 de julio de 2026 y dejó de responder JSON válido para
> cualquier cliente, por lo que se reemplazó por Copilot CLI.

### Cómo configurar el token de Copilot CLI (una sola vez, a nivel organización)

Copilot CLI necesita un token propio para autenticarse de forma no
interactiva; el `GITHUB_TOKEN` automático de Actions **no sirve** (no es
un formato de token soportado por la CLI). Hace falta:

1. Generar un **Personal Access Token de grano fino** desde una **cuenta
   personal** que tenga asiento de Copilot asignado:
   GitHub → Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token.
   - Resource owner: la cuenta personal (NO la organización).
   - Permisos: habilitar **"Copilot Requests"**.
   - Expiration: definir una fecha y calendarizar su renovación (el token
     vence y hay que rotarlo manualmente).
2. Guardarlo como secret **a nivel organización** (así escala a los ~59
   repos sin repetir el paso en cada uno):
   ```
   gh secret set COPILOT_GITHUB_TOKEN --org acacoop --visibility all --body "<el token>"
   ```
3. Cada workflow consumidor (`consumer-workflow-example.yml`) ya pasa
   `COPILOT_GITHUB_TOKEN: ${{ secrets.COPILOT_GITHUB_TOKEN }}` al workflow
   reusable — al ser secret de organización, no hace falta declararlo de
   nuevo en cada repo individual.

**Costo operacional:** cada invocación de `copilot -p` consume una
"premium request" de la cuota mensual de Copilot asociada al dueño del
PAT. El pipeline hace hasta 2 invocaciones por repo por push a `main` con
cambios (una para "Desarrollo", otra para "Infraestructura relevada"). Al
escalar a toda la organización, estimar el volumen esperado antes de
activar `ai_enrichment: true` de forma masiva, y considerar fijar un
modelo económico vía la env var `AI_ENRICHMENT_MODEL` (default:
`gpt-5-mini`) en vez del modelo por default de Copilot CLI.

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
5. El `schedule:` del workflow (`cron: "0 13 * * 1"`, lunes 13:00 UTC) ya
   está **activo** — corre solo, una vez por semana, sin intervención
   manual. Esto es lo que hace que un repo creado en cualquier momento
   termine recibiendo su PR de bootstrap sin que nadie tenga que acordarse
   de correrlo a mano.

### Estado del rollout (última corrida real, Oct/2026)

- **80 repos candidatos** detectados en la organización (se excluyen
  forks, templates y repos sin `defaultBranchRef`).
- **35 repos** ya tenían el pipeline instalado (bootstrap de corridas
  previas).
- **45 repos** recibieron un Pull Request `docops/bootstrap-auto` nuevo.
  De esos, **41 se revisaron y mergearon** (CI verde, sin conflictos); los
  4 restantes quedaron abiertos para que cada equipo dueño los revise y
  mergee cuando le resulte conveniente — no es bloqueante: en cuanto se
  mergeen, el primer push a `main` dispara el relevamiento inicial
  (Fase 1) igual que en el resto.
- El script es **idempotente**: si se vuelve a correr (manual o por el
  schedule semanal) sobre un repo que ya tiene un PR de bootstrap abierto,
  lo detecta (`gh pr list --head docops/bootstrap-auto`) y lo saltea en
  vez de reintentar el push, evitando duplicados o errores de
  `non-fast-forward`.
- **Fix aplicado**: `gh repo clone` autentica el clone en sí mismo pero no
  deja un credential helper persistente, por lo que los `git push`
  posteriores (git puro) fallaban. Se agregó `gh auth setup-git` al inicio
  del script para que todo push subsiguiente se autentique con el mismo
  token (`GH_TOKEN` / `ORG_BOOTSTRAP_TOKEN`).

### Dos fases de funcionamiento, por repo

- **Fase 1 — Relevamiento inicial**: ocurre una única vez, en el momento
  en que se mergea el PR de bootstrap a `main` de ese repo. Como todavía
  no existe una huella guardada en Confluence, el pipeline escanea y
  publica las 5 páginas completas sin importar el fingerprint.
- **Fase 2 — Mantenimiento incremental**: de ahí en adelante, cada push a
  `main` de ese repo compara la huella actual contra la guardada
  (`docops:fingerprint`, content property de Confluence). Si no cambió
  nada relevante (código o Terraform), no se toca Confluence; si cambió,
  se regenera solo el bloque afectado y se publica una nueva versión.

### Recomendación adicional: secrets de Confluence a nivel organización

Para que un repo nuevo no tenga que cargar los 4 secrets de Confluence a
mano, conviene cargarlos UNA sola vez como **organization secrets**
(Settings → Secrets and variables → Actions → "New organization secret"),
con política de acceso "All repositories" o una lista explícita. Así, el
único paso manual que le queda a un equipo nuevo es completar
`docs/manifest/*.yml` (o esperar a la integración con Jira) — todo lo demás
ya está resuelto por el PR de bootstrap + los secrets heredados de la
organización.
