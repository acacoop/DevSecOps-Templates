"""
scan_code.py
=============
Escanea el código fuente del repositorio y genera un YAML con la forma de
docs/generated/desarrollo.yml, completando SOLO lo que puede evidenciar con
archivos concretos. Lo que no puede determinar queda como `null` (que
yaml_to_confluence.py renderiza como "No determinado desde el repositorio").

No es un parser exhaustivo de todos los lenguajes del mundo: cubre
heurísticas concretas y documentadas, y es seguro extenderlo agregando más
reglas en `detect_language_and_tooling()`.

Uso:
    python scripts/scan_code.py --root . --out /tmp/desarrollo-scanned.yml \
        --fingerprint <hash> --commit <sha>
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import os
import re

import yaml

# Carpetas que nunca son "código de aplicación" para este escaneo.
EXCLUDED_DIRS = {".git", "docs", "infra", ".github", "scripts", "__pycache__", "node_modules", "jenkins-tutorial"}

FRAMEWORK_SIGNATURES = {
    "Flask": re.compile(r"^\s*(from|import)\s+flask\b", re.MULTILINE | re.IGNORECASE),
    "Django": re.compile(r"^\s*(from|import)\s+django\b", re.MULTILINE | re.IGNORECASE),
    "FastAPI": re.compile(r"^\s*(from|import)\s+fastapi\b", re.MULTILINE | re.IGNORECASE),
    "Express": re.compile(r"require\(['\"]express['\"]\)|from ['\"]express['\"]", re.IGNORECASE),
}

LANGUAGE_BY_EXTENSION = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".java": "Java",
    ".cs": "C#",
    ".go": "Go",
}


def iter_source_files(root: str):
    for current_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for filename in filenames:
            yield os.path.relpath(os.path.join(current_dir, filename), root).replace(os.sep, "/")


def detect_language(files: list[str]) -> tuple[str | None, str | None]:
    """Devuelve (lenguaje_dominante, archivo_de_evidencia) según la
    extensión más frecuente entre los archivos escaneados."""
    counts: dict[str, int] = {}
    first_file_by_lang: dict[str, str] = {}

    for rel_path in files:
        _, ext = os.path.splitext(rel_path)
        lang = LANGUAGE_BY_EXTENSION.get(ext.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
            first_file_by_lang.setdefault(lang, rel_path)

    if not counts:
        return None, None

    dominant = max(counts, key=counts.get)
    return dominant, first_file_by_lang[dominant]


def detect_framework(root: str, files: list[str]) -> tuple[str | None, str | None]:
    """Busca firmas de imports de frameworks conocidos dentro de los
    archivos de código fuente (no de test/docs)."""
    for rel_path in files:
        if not rel_path.endswith((".py", ".js", ".ts")):
            continue
        full_path = os.path.join(root, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            continue

        for framework, pattern in FRAMEWORK_SIGNATURES.items():
            if pattern.search(content):
                return framework, rel_path

    return None, None


def find_dockerfiles(root: str, files: list[str]) -> list[str]:
    return [f for f in files if os.path.basename(f).lower().startswith("dockerfile")]


def detect_runtime(root: str, dockerfiles: list[str]) -> tuple[str | None, str | None]:
    """Extrae la primera línea `FROM` del primer Dockerfile encontrado."""
    if not dockerfiles:
        return None, None

    dockerfile = sorted(dockerfiles)[0]
    with open(os.path.join(root, dockerfile), "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            match = re.match(r"^\s*FROM\s+(\S+)", line, re.IGNORECASE)
            if match:
                return match.group(1), dockerfile

    return None, dockerfile


def detect_package_manager(root: str, files: list[str], dockerfiles: list[str]) -> tuple[str | None, str | None]:
    manifest_by_manager = {
        "pip (requirements.txt)": "requirements.txt",
        "pip (pyproject.toml)": "pyproject.toml",
        "npm (package.json)": "package.json",
        "maven (pom.xml)": "pom.xml",
    }
    for manager, manifest_name in manifest_by_manager.items():
        for rel_path in files:
            if os.path.basename(rel_path) == manifest_name:
                return manager, rel_path

    # Fallback: dependencias declaradas directamente en el Dockerfile
    # (ej. `RUN pip install redis`), sin un manifiesto de dependencias.
    for dockerfile in dockerfiles:
        with open(os.path.join(root, dockerfile), "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if re.search(r"RUN\s+pip\s+install", content, re.IGNORECASE):
            return "pip (declarado directamente en Dockerfile, sin requirements.txt)", dockerfile

    return None, None


def detect_app_type(root: str, files: list[str], framework: str | None) -> tuple[str | None, str | None]:
    has_templates = any("/templates/" in f or f.startswith("templates/") for f in files)
    has_static = any("/static/" in f or f.startswith("static/") for f in files)

    if framework in {"Flask", "Django"} and (has_templates or has_static):
        evidence_file = next(
            (f for f in files if "/templates/" in f or f.startswith("templates/")),
            None,
        )
        return "Portal web (server-side rendered)", evidence_file

    if framework:
        return f"Servicio/API ({framework})", None

    return None, None


def scan(root: str) -> dict:
    files = list(iter_source_files(root))

    language, language_evidence = detect_language(files)
    framework, framework_evidence = detect_framework(root, files)
    dockerfiles = find_dockerfiles(root, files)
    runtime, runtime_evidence = detect_runtime(root, dockerfiles)
    package_manager, package_manager_evidence = detect_package_manager(root, files, dockerfiles)
    app_type, app_type_evidence = detect_app_type(root, files, framework)

    return {
        "tipos_de_aplicaciones": [
            {
                "aplicacion": app_type or None,
                "tipo": app_type,
                "evidencia_en_repositorio": app_type_evidence,
            }
        ],
        "tecnologias_de_desarrollo": {
            "capa_lenguaje": {
                "tecnologia": language,
                "version": None,  # requiere inspección más profunda (ej. parsear FROM python:X.Y)
                "donde_se_detecto": language_evidence,
            },
            "capa_framework": {
                "tecnologia": framework,
                "version": None,
                "donde_se_detecto": framework_evidence,
            },
            "capa_runtime": {
                "tecnologia": runtime,
                "version": None,
                "donde_se_detecto": runtime_evidence,
            },
            "capa_gestor_de_paquetes": {
                "tecnologia": package_manager,
                "version": None,
                "donde_se_detecto": package_manager_evidence,
            },
        },
    }


def deep_merge(base: dict, overrides: dict) -> dict:
    """Combina `overrides` sobre `base` recursivamente (solo para dicts).
    Preserva todas las claves de `base` que `overrides` no toca, para no
    perder secciones del schema documentado que el scanner todavía no
    completa automáticamente.
    """
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Raíz del repo a escanear")
    parser.add_argument(
        "--template",
        default="docs/generated/desarrollo.yml",
        help="Schema completo sobre el que se mezcla lo escaneado (ruta relativa a --root)",
    )
    parser.add_argument("--out", required=True, help="Archivo YAML de salida")
    parser.add_argument("--fingerprint", required=True, help="Huella calculada para este escaneo")
    parser.add_argument("--commit", required=True, help="SHA del commit relevado")
    args = parser.parse_args()

    template_path = os.path.join(args.root, args.template)
    with open(template_path, "r", encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}

    scanned = scan(args.root)
    scanned["trazabilidad"] = {
        "ultimo_commit_relevado": args.commit,
        "huella_codigo": args.fingerprint,
        "fecha_relevamiento": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    result = deep_merge(base, scanned)

    with open(args.out, "w", encoding="utf-8") as f:
        yaml.safe_dump(result, f, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    main()
