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
    └── consumer-workflow-example.yml
```

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

## Aplicación global (para cuando salgamos del sandbox)

Hay tres formas de "activar esto para cada repo nuevo que se cree",
de menor a mayor automatización:

1. **Repository template de la organización.** Se marca este repo (o uno
   dedicado) como "template" en GitHub, y cuando alguien crea un repo nuevo
   eligiendo ese template, ya viene con `.github/workflows/docops-sync.yml`
   y `docs/` scaffolded. Requiere que la persona elija el template al
   crear el repo (no es 100% automático, pero es el más simple de activar).

2. **GitHub App que escucha `repository.created`.** Una GitHub App (o un
   workflow en un repo "controlador" disparado por un webhook de
   organización) que, cada vez que se crea un repo nuevo en `acacoop`,
   hace un commit inicial agregando `.github/workflows/docops-sync.yml` +
   `docs/` automáticamente, y llama a la API de GitHub para cargar los 4
   secrets de Confluence (si se gestionan de forma centralizada, ver punto
   siguiente). Esto sí es 100% automático, pero requiere desplegar y
   mantener la App.

3. **Secrets a nivel organización + required workflow (GHE/Enterprise).**
   En vez de cargar los 4 secrets en cada repo, se cargan una sola vez como
   "Organization secret" (con política de acceso a los repos que correspondan),
   y si la cuenta de GitHub lo soporta (GitHub Enterprise Cloud), se puede
   forzar que TODOS los repos ejecuten un "required workflow" sin que cada
   uno necesite tener el archivo `.github/workflows/docops-sync.yml` propio.

Para el sandbox actual alcanza con la opción manual (copiar 2 archivos +
cargar 4 secrets por repo), que es exactamente lo que se hizo en
`acacoop/docops-sandbox`. Las opciones 1-3 son el Paso 10 del roadmap.
