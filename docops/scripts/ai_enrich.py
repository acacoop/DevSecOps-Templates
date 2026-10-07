"""
ai_enrich.py
=============
Enriquecimiento OPCIONAL y best-effort de los YAML generados por
scan_code.py / scan_terraform.py, usando un modelo de lenguaje (GitHub
Models) para completar SOLO los campos que el escaneo determinístico dejó
en `null`.

Principios de diseño (no negociables):
  - Nunca reemplaza un valor ya determinado por el escaneo heurístico.
    Solo mira los campos que siguen en `null` después de scan_code.py /
    scan_terraform.py.
  - Nunca inventa: al modelo se le pasa como ÚNICA evidencia el contenido
    real de archivos del repositorio, y cada campo completado que necesita
    una referencia a evidencia (ej. "donde", "archivo", "evidencia",
    "origen") se valida contra la lista real de archivos del repo. Si no
    coincide con un archivo real, se descarta ese campo/registro entero
    (queda `null`, como si el modelo no hubiera respondido nada).
  - Nunca puede romper el pipeline: cualquier error (sin token, sin
    permiso de "models: read", rate limit, respuesta no-JSON, timeout de
    red) se loguea como aviso y el YAML queda exactamente como entró.

Uso:
    python scripts/ai_enrich.py --root . --yaml /tmp/desarrollo.yml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import requests
import yaml

MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODEL = os.environ.get("AI_ENRICHMENT_MODEL", "openai/gpt-4o-mini")

# Presupuesto de contexto: cuánto texto de evidencia se le manda al modelo
# como máximo (y cuánto se lee de cada archivo individual), para no volar
# el límite de tokens ni mandar archivos gigantes irrelevantes.
MAX_EVIDENCE_CHARS = 12000
MAX_FILE_CHARS = 2000

# Nombres de archivo que priorizamos como evidencia (manifiestos, docs,
# IaC): lo más denso en información real sobre qué hace el repo.
EVIDENCE_PRIORITY_HINTS = (
    "readme",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "pom.xml",
    "dockerfile",
    "docker-compose",
    ".tf",
)

# Nombres de clave que, cuando el modelo completa un campo con ese nombre,
# DEBEN apuntar a un path real del repositorio (si no, se descarta el
# registro completo al que pertenecen).
EVIDENCE_KEY_HINTS = {
    "evidencia",
    "donde",
    "donde_se_detecto",
    "archivo",
    "origen",
    "origen_del_dato",
    "como_se_determino",
    "recurso_o_configuracion",
    "detectado_en_la_iac",
}

EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".terraform"}


def dotted(path: tuple) -> str:
    return ".".join(str(p) for p in path)


def collect_targets(node, path=()):
    """Recorre el YAML ya mergeado por el escaneo determinístico y separa
    lo que falta completar en dos grupos:

    - `leaf_paths`: hojas sueltas en `null` (ej. capa_runtime.tecnologia).
    - `list_paths`: listas que siguen siendo el placeholder íntegro del
      template (un solo registro con TODOS sus campos en null) — estas se
      le piden al modelo como "completá 0 a N registros reales".

    Una lista que ya tiene datos reales (del escaneo) o más de un
    registro NO se toca: eso ya lo resolvió el escaneo determinístico o
    ya tiene contenido real, y no queremos que el modelo lo pise.
    """
    leaf_paths: list[tuple] = []
    list_paths: list[tuple] = []

    if not isinstance(node, dict):
        return leaf_paths, list_paths

    for key, value in node.items():
        if key == "trazabilidad":
            continue
        sub_path = path + (key,)
        if value is None:
            leaf_paths.append(sub_path)
        elif isinstance(value, dict):
            sub_leafs, sub_lists = collect_targets(value, sub_path)
            leaf_paths.extend(sub_leafs)
            list_paths.extend(sub_lists)
        elif isinstance(value, list):
            if len(value) == 1 and isinstance(value[0], dict) and all(
                v is None for v in value[0].values()
            ):
                list_paths.append(sub_path)
            # Listas con datos reales o más de un registro: no se tocan.

    return leaf_paths, list_paths


def get_in(data: dict, path: tuple):
    cur = data
    for p in path:
        cur = cur[p]
    return cur


def group_leafs_by_parent(leaf_paths: list[tuple]) -> dict:
    """Agrupa hojas sueltas por su dict padre inmediato, para poder
    validar juntos los campos que comparten un mismo registro (ej.
    tecnologia + version + donde_se_detecto de capa_runtime)."""
    groups: dict[tuple, list[str]] = {}
    for path in leaf_paths:
        parent = path[:-1]
        groups.setdefault(parent, []).append(path[-1])
    return groups


def list_repo_files(root: str) -> list[str]:
    files = []
    for current_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for filename in filenames:
            rel = os.path.relpath(os.path.join(current_dir, filename), root).replace(os.sep, "/")
            files.append(rel)
    return files


def gather_evidence(root: str, files: list[str]) -> str:
    """Arma el bloque de evidencia real que se le manda al modelo:
    prioriza manifiestos/README/IaC, trunca cada archivo y el total para
    no exceder el presupuesto de contexto."""

    def priority(rel_path: str) -> int:
        base = os.path.basename(rel_path).lower()
        for i, hint in enumerate(EVIDENCE_PRIORITY_HINTS):
            if hint in base or rel_path.lower().endswith(hint):
                return i
        return len(EVIDENCE_PRIORITY_HINTS)

    ranked = sorted(files, key=priority)
    chunks = []
    total = 0
    for rel_path in ranked:
        if priority(rel_path) == len(EVIDENCE_PRIORITY_HINTS):
            continue  # fuera de la lista de prioridad: no vale la pena el costo de tokens
        full_path = os.path.join(root, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(MAX_FILE_CHARS)
        except OSError:
            continue
        chunk = f"--- {rel_path} ---\n{content}\n"
        if total + len(chunk) > MAX_EVIDENCE_CHARS:
            break
        chunks.append(chunk)
        total += len(chunk)
    return "\n".join(chunks)


def build_skeleton(leaf_groups: dict, list_paths: list[tuple], data: dict) -> dict:
    grupos = {
        dotted(parent): {key: None for key in keys}
        for parent, keys in leaf_groups.items()
    }
    listas = {}
    for path in list_paths:
        placeholder_item = get_in(data, path)[0]
        listas[dotted(path)] = {"items_schema": dict.fromkeys(placeholder_item.keys())}
    return {"grupos": grupos, "listas": listas}


def call_model(skeleton: dict, evidence: str, token: str) -> dict | None:
    system_prompt = (
        "Sos un asistente que documenta repositorios de software para un "
        "sistema de documentacion tecnica automatica. Se te da (1) un "
        "esqueleto JSON con campos en null que hay que completar, agrupados "
        "en 'grupos' (campos sueltos) y 'listas' (listas de registros), y "
        "(2) el contenido de archivos reales del repositorio como UNICA "
        "evidencia permitida.\n\n"
        "Reglas estrictas, sin excepciones:\n"
        "- Nunca inventes nombres de archivos, versiones, tecnologias ni "
        "datos que no esten literalmente en la evidencia provista.\n"
        "- Si un campo no se puede justificar con un archivo real de la "
        "evidencia, dejalo en null.\n"
        "- Todo campo cuyo nombre sea de evidencia (ej. 'donde', "
        "'donde_se_detecto', 'archivo', 'evidencia', 'origen', "
        "'origen_del_dato', 'como_se_determino') debe ser EXACTAMENTE uno "
        "de los paths que aparecen en los encabezados '--- path ---' de la "
        "evidencia. Si no podes dar ese path exacto, dejá todo el registro "
        "en null en vez de inventar un path aproximado.\n"
        "- Para cada entrada de 'listas', devolvé un array 'items' con 0 a "
        "N objetos que respeten EXACTAMENTE las claves de 'items_schema'; "
        "si no hay evidencia real para ningún registro, devolvé items: [].\n"
        "- Respondé EXCLUSIVAMENTE un JSON valido con la forma "
        '{"grupos": {...}, "listas": {"<path>": {"items": [...]}}}, sin '
        "texto adicional, sin markdown, sin comentarios."
    )
    user_prompt = (
        "ESQUELETO A COMPLETAR (JSON):\n"
        f"{json.dumps(skeleton, ensure_ascii=False, indent=2)}\n\n"
        "EVIDENCIA (contenido real de archivos del repositorio):\n"
        f"{evidence}\n"
    )

    body = {
        "model": DEFAULT_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        resp = requests.post(
            MODELS_ENDPOINT,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 - best-effort: nunca debe romper el pipeline
        print(
            f"[ai_enrich] aviso: no se pudo consultar el modelo ({exc}); "
            "se omite el enriquecimiento.",
            file=sys.stderr,
        )
        return None

    content = content.strip()
    content = re.sub(r"^```(json)?", "", content).strip()
    content = re.sub(r"```$", "", content).strip()
    try:
        return json.loads(content)
    except ValueError as exc:
        print(
            f"[ai_enrich] aviso: la respuesta del modelo no es JSON valido "
            f"({exc}); se omite el enriquecimiento.",
            file=sys.stderr,
        )
        return None


def validate_group(candidate: dict, keys: list[str], valid_files: set[str]) -> dict:
    """Valida un grupo de campos sueltos (ej. capa_runtime). Si tiene un
    campo de evidencia y no apunta a un archivo real, se descarta el grupo
    entero. Devuelve solo las claves del esqueleto original con valor no
    nulo (las que el modelo no pudo completar quedan afuera, sin tocar lo
    que ya había)."""
    if not isinstance(candidate, dict):
        return {}

    evidence_keys = [k for k in keys if k.lower() in EVIDENCE_KEY_HINTS]
    for ek in evidence_keys:
        ev_value = candidate.get(ek)
        if ev_value and ev_value not in valid_files:
            return {}  # evidencia inventada: se descarta TODO el grupo

    return {
        k: candidate[k]
        for k in keys
        if candidate.get(k) is not None
    }


def validate_record(candidate: dict, keys: list[str], valid_files: set[str]) -> dict | None:
    """Igual que validate_group pero para un registro de una lista: si no
    es válido devuelve None (se descarta el registro completo)."""
    if not isinstance(candidate, dict):
        return None

    normalized = {k: candidate.get(k) for k in keys}
    if all(v is None for v in normalized.values()):
        return None

    evidence_keys = [k for k in keys if k.lower() in EVIDENCE_KEY_HINTS]
    for ek in evidence_keys:
        ev_value = normalized.get(ek)
        if ev_value and ev_value not in valid_files:
            return None  # evidencia inventada: se descarta el registro

    return normalized


def apply_enrichment(
    data: dict,
    leaf_groups: dict,
    list_paths: list[tuple],
    enriched: dict,
    valid_files: set[str],
) -> int:
    filled = 0

    resp_grupos = enriched.get("grupos", {}) if isinstance(enriched, dict) else {}
    for parent, keys in leaf_groups.items():
        candidate = resp_grupos.get(dotted(parent))
        validated = validate_group(candidate, keys, valid_files)
        if not validated:
            continue
        target = get_in(data, parent) if parent else data
        for k, v in validated.items():
            target[k] = v
        filled += len(validated)

    resp_listas = enriched.get("listas", {}) if isinstance(enriched, dict) else {}
    for path in list_paths:
        placeholder_item = get_in(data, path)[0]
        keys = list(placeholder_item.keys())
        list_resp = resp_listas.get(dotted(path))
        items = list_resp.get("items") if isinstance(list_resp, dict) else None
        if not isinstance(items, list):
            continue

        valid_items = []
        for item in items:
            validated = validate_record(item, keys, valid_files)
            if validated:
                valid_items.append(validated)

        if valid_items:
            parent_node = get_in(data, path[:-1]) if len(path) > 1 else data
            parent_node[path[-1]] = valid_items
            filled += len(valid_items)

    return filled


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Raíz del repo escaneado")
    parser.add_argument(
        "--yaml",
        required=True,
        help="YAML ya generado por scan_code.py / scan_terraform.py (se sobreescribe in-place)",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("AI_ENRICHMENT_TOKEN")
    if not token:
        print(
            "[ai_enrich] sin token disponible (GITHUB_TOKEN/AI_ENRICHMENT_TOKEN); "
            "se omite el enriquecimiento.",
            file=sys.stderr,
        )
        return

    with open(args.yaml, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    leaf_paths, list_paths = collect_targets(data)
    leaf_groups = group_leafs_by_parent(leaf_paths)

    if not leaf_groups and not list_paths:
        print("[ai_enrich] no hay campos pendientes de enriquecer.")
        return

    skeleton = build_skeleton(leaf_groups, list_paths, data)
    files = list_repo_files(args.root)
    evidence = gather_evidence(args.root, files)
    if not evidence.strip():
        print("[ai_enrich] no se encontró evidencia de texto para enriquecer, se omite.")
        return

    enriched = call_model(skeleton, evidence, token)
    if not enriched:
        return

    filled = apply_enrichment(data, leaf_groups, list_paths, enriched, set(files))
    print(f"[ai_enrich] campos completados por IA: {filled}")

    if filled:
        with open(args.yaml, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    main()
