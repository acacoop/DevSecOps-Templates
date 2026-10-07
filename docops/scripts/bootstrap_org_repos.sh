#!/usr/bin/env bash
# bootstrap_org_repos.sh
# =========================
# Recorre todos los repos de una organización de GitHub y, para los que
# TODAVÍA NO tienen el pipeline DocOps instalado, abre un Pull Request que
# agrega:
#   - .github/workflows/docops-sync.yml  (consumer workflow fino)
#   - docs/manifest/*.yml, docs/generated/*.yml  (scaffolding)
#
# Es la pieza central de la "aplicación global": en vez de depender de que
# cada persona recuerde copiar 2 carpetas al crear un repo nuevo, este
# script corre periódicamente (o a demanda) y lo hace por todos.
#
# NO hace push directo a main de ningún repo ajeno: siempre abre un PR,
# para que el equipo dueño del repo decida cuándo mergearlo (puede haber
# conflictos con un docs/ ya existente, estructura de infra distinta, etc).
#
# Requiere:
#   - gh CLI autenticado con un token que tenga, en TODOS los repos de la
#     organización: Contents (write), Pull requests (write), Workflows
#     (write) y Metadata (read). Ver GH_TOKEN más abajo.
#
# Uso:
#   GH_TOKEN=<token> ./bootstrap_org_repos.sh <org> <templates_repo> [--dry-run]
#
# Ejemplo:
#   GH_TOKEN=*** ./bootstrap_org_repos.sh acacoop acacoop/DevSecOps-Templates --dry-run
set -euo pipefail

ORG="${1:?Uso: bootstrap_org_repos.sh <org> <templates_repo> [--dry-run]}"
TEMPLATES_REPO="${2:?falta <templates_repo>, ej. acacoop/DevSecOps-Templates}"
DRY_RUN="${3:-}"

# Lista opcional de repos a saltear SIEMPRE (no se les abre PR), separados
# por coma, en formato "owner/repo". Ej:
#   EXCLUDE_REPOS="acacoop/.github,acacoop/legacy-poc" ./bootstrap_org_repos.sh ...
IFS=',' read -ra EXCLUDE_LIST <<< "${EXCLUDE_REPOS:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="$SCRIPT_DIR/../docs-template"
WORKDIR="$(mktemp -d)"
BRANCH_NAME="docops/bootstrap-auto"
PR_TITLE="chore(docops): habilitar sincronización automática de documentación a Confluence"

echo "== DocOps bootstrap para la organización '$ORG' =="
echo "Modo: $([ "$DRY_RUN" = "--dry-run" ] && echo 'DRY RUN (no escribe nada)' || echo 'EJECUCIÓN REAL (abre PRs)')"
echo

mapfile -t REPOS < <(
  gh repo list "$ORG" --source --no-archived --limit 1000 \
    --json nameWithOwner,isFork,isTemplate,defaultBranchRef \
    --jq '.[] | select(.isFork == false and .isTemplate == false and .defaultBranchRef != null) | .nameWithOwner'
)

echo "Repos candidatos encontrados: ${#REPOS[@]}"
echo

FAILED_REPOS=()

for REPO in "${REPOS[@]}"; do
  if [ "$REPO" = "$TEMPLATES_REPO" ]; then
    echo "SKIP  $REPO (es el propio repo de templates)"
    continue
  fi

  SKIP_EXCLUDED=false
  for EXCLUDED in "${EXCLUDE_LIST[@]}"; do
    if [ "$REPO" = "$EXCLUDED" ]; then
      SKIP_EXCLUDED=true
      break
    fi
  done
  if [ "$SKIP_EXCLUDED" = true ]; then
    echo "SKIP  $REPO (excluido explícitamente vía EXCLUDE_REPOS)"
    continue
  fi

  # ¿Ya tiene el workflow instalado?
  if gh api "repos/$REPO/contents/.github/workflows/docops-sync.yml" >/dev/null 2>&1; then
    echo "SKIP  $REPO (ya tiene docops-sync.yml)"
    continue
  fi

  if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "WOULD BOOTSTRAP  $REPO"
    continue
  fi

  echo "BOOTSTRAP  $REPO"
  REPO_DIR="$WORKDIR/$(basename "$REPO")"
  # La rama por defecto real del repo (puede no ser "main": master, Dev, etc.).
  # Los templates traen "branches: [main]" hardcodeado; si no se ajusta acá,
  # el trigger nunca dispara en repos cuyo default branch es distinto.
  DEFAULT_BRANCH="$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)"
  if ! (
    gh repo clone "$REPO" "$REPO_DIR" -- --quiet
    cd "$REPO_DIR"
    git checkout -b "$BRANCH_NAME"

    mkdir -p .github/workflows docs/manifest docs/generated
    cp "$TEMPLATE_DIR/consumer-workflow-example.yml" .github/workflows/docops-sync.yml
    cp "$TEMPLATE_DIR/consumer-preview-workflow-example.yml" .github/workflows/docops-preview.yml
    if [ "$DEFAULT_BRANCH" != "main" ]; then
      sed -i "s/branches: \[main\]/branches: [$DEFAULT_BRANCH]/" \
        .github/workflows/docops-sync.yml .github/workflows/docops-preview.yml
    fi
    cp "$TEMPLATE_DIR"/manifest/*.yml docs/manifest/ 2>/dev/null || true
    cp "$TEMPLATE_DIR"/generated/*.yml docs/generated/ 2>/dev/null || true
    cp "$TEMPLATE_DIR/README.md" docs/README.md

    # -f: algunos repos tienen "docs" en su .gitignore (ej. para no versionar
    # docs generadas por otra herramienta). Acá queremos versionar docs/
    # SIEMPRE, es scaffolding intencional del pipeline DocOps.
    git add .github/workflows/docops-sync.yml .github/workflows/docops-preview.yml
    git add -f docs/
    if git diff --cached --quiet; then
      echo "  (sin cambios reales para $REPO, se omite)"
      exit 0
    fi

    git -c user.name="docops-bootstrap-bot" -c user.email="docops-bootstrap@users.noreply.github.com" \
      commit -q -m "$PR_TITLE"
    git push -u origin "$BRANCH_NAME" --quiet

    gh pr create \
      --repo "$REPO" \
      --title "$PR_TITLE" \
      --body "Este PR fue abierto automáticamente por el bootstrap de DocOps.

Agrega:
- \`.github/workflows/docops-sync.yml\`: invoca el pipeline reusable de [$TEMPLATES_REPO](https://github.com/$TEMPLATES_REPO) en cada push a main (publica en Confluence).
- \`.github/workflows/docops-preview.yml\`: comenta en cada PR contra main qué páginas cambiarían, sin publicar nada todavía.
- \`docs/manifest/\` y \`docs/generated/\`: estructura estándar de documentación técnica versionada en Confluence.

Antes de mergear, el equipo dueño de este repo debe:
1. Revisar que \`docs/\` no choque con documentación propia ya existente.
2. Cargar los 4 secrets de Confluence en este repo (o confirmar que ya existen a nivel organización):
   \`CONFLUENCE_BASE_URL\`, \`CONFLUENCE_EMAIL\`, \`CONFLUENCE_API_TOKEN\`, \`CONFLUENCE_SPACE_KEY\`.
3. Completar \`docs/manifest/*.yml\` con los campos de negocio (o esperar a que lo haga la integración con Jira).

El workflow incluye el permiso \`models: read\`, usado para completar con IA (GitHub Models) los campos narrativos que el escaneo no puede determinar solo (siempre citando un archivo real como evidencia; nunca inventa datos). Es opcional: se puede desactivar poniendo \`ai_enrichment: false\` en el \`with:\` del workflow, y si falla por cualquier motivo no interrumpe la publicación.

Ver [$TEMPLATES_REPO/docops/README.md](https://github.com/$TEMPLATES_REPO/blob/main/docops/README.md) para más detalle." \
      --head "$BRANCH_NAME" \
      --base "$DEFAULT_BRANCH"
  ); then
    echo "  !! FALLÓ el bootstrap de $REPO (ver log arriba), continuando con el resto..."
    FAILED_REPOS+=("$REPO")
  fi
done

echo
echo "== Listo =="
if [ "${#FAILED_REPOS[@]}" -gt 0 ]; then
  echo
  echo "Repos que fallaron (revisar manualmente):"
  for FAILED in "${FAILED_REPOS[@]}"; do
    echo "  - $FAILED"
  done
fi
