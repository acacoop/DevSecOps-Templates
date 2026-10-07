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
import json
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

# Paquete npm o pip cuya versión declarada se usa como "version" de cada
# tecnología de desarrollo, cuando el lenguaje/framework coincide.
NPM_PACKAGE_BY_FRAMEWORK = {"Express": "express"}
PY_PACKAGE_BY_FRAMEWORK = {"Flask": "flask", "Django": "django", "FastAPI": "fastapi"}

# Firmas de dependencias de bases de datos conocidas: nombre del paquete ->
# (motor, tipo de gestor). Cubre los drivers/ORMs más comunes en npm y pip.
DB_NPM_SIGNATURES = {
    "pg": ("PostgreSQL", "Relacional"),
    "mysql": ("MySQL", "Relacional"),
    "mysql2": ("MySQL", "Relacional"),
    "sqlite3": ("SQLite", "Relacional"),
    "better-sqlite3": ("SQLite", "Relacional"),
    "mongoose": ("MongoDB", "Documental"),
    "mongodb": ("MongoDB", "Documental"),
    "redis": ("Redis", "Clave-valor"),
    "ioredis": ("Redis", "Clave-valor"),
}
DB_PY_SIGNATURES = {
    "psycopg2": ("PostgreSQL", "Relacional"),
    "psycopg2-binary": ("PostgreSQL", "Relacional"),
    "pymysql": ("MySQL", "Relacional"),
    "mysqlclient": ("MySQL", "Relacional"),
    "pymongo": ("MongoDB", "Documental"),
    "redis": ("Redis", "Clave-valor"),
}


def iter_source_files(root: str):
    for current_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for filename in filenames:
            yield os.path.relpath(os.path.join(current_dir, filename), root).replace(os.sep, "/")


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _clean_version(raw: str | None) -> str | None:
    """Convierte un rango de versión declarado (ej. '^5.9.2', '>=2.3.0')
    en el número de versión concreto que lo acompaña."""
    if not raw:
        return None
    match = re.search(r"\d+(\.\d+){0,3}", raw)
    return match.group(0) if match else raw


def _find_upwards(root: str, start_file: str | None, filename: str) -> str | None:
    """Busca `filename` empezando en el directorio de `start_file` y subiendo
    hacia la raíz del repo (soporta monorepos donde el manifiesto relevante
    vive en un subdirectorio como server/ o client/, no en la raíz)."""
    root_abs = os.path.abspath(root)
    current = os.path.abspath(
        os.path.join(root, os.path.dirname(start_file)) if start_file else root
    )
    while True:
        candidate = os.path.join(current, filename)
        if os.path.isfile(candidate):
            return os.path.relpath(candidate, root_abs).replace(os.sep, "/")
        if current == root_abs:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def _parse_requirements_version(path: str, package: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return None
    pattern = re.compile(rf"^\s*{re.escape(package)}\s*[=<>!~]+\s*([\w.*]+)", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(content)
    return match.group(1) if match else None


def detect_app_name(root: str, framework_evidence: str | None) -> str | None:
    """Nombre de la aplicación: el 'name' del manifiesto npm más cercano al
    código donde se detectó el framework (soporta monorepos), o el nombre
    de la carpeta del repo como último recurso."""
    manifest = _find_upwards(root, framework_evidence, "package.json")
    if manifest:
        name = _load_json(os.path.join(root, manifest)).get("name")
        if name:
            return name
    return os.path.basename(os.path.abspath(root)) or None


def detect_language_version(root: str, language: str | None, language_evidence: str | None) -> tuple[str | None, str | None]:
    if language == "TypeScript":
        manifest = _find_upwards(root, language_evidence, "package.json")
        if manifest:
            data = _load_json(os.path.join(root, manifest))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            raw = deps.get("typescript")
            if raw:
                return _clean_version(raw), manifest
    return None, None


def detect_framework_version(root: str, framework: str | None, framework_evidence: str | None) -> tuple[str | None, str | None]:
    npm_package = NPM_PACKAGE_BY_FRAMEWORK.get(framework)
    if npm_package:
        manifest = _find_upwards(root, framework_evidence, "package.json")
        if manifest:
            data = _load_json(os.path.join(root, manifest))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            raw = deps.get(npm_package)
            if raw:
                return _clean_version(raw), manifest

    py_package = PY_PACKAGE_BY_FRAMEWORK.get(framework)
    if py_package:
        manifest = _find_upwards(root, framework_evidence, "requirements.txt")
        if manifest:
            version = _parse_requirements_version(os.path.join(root, manifest), py_package)
            if version:
                return version, manifest

    return None, None


def detect_runtime_fallback(root: str, files: list[str]) -> tuple[str | None, str | None]:
    """Cuando no hay Dockerfile, intenta inferir el runtime Node.js desde
    .nvmrc o el campo engines.node de algún package.json del repo."""
    nvmrc = next((f for f in files if os.path.basename(f) == ".nvmrc"), None)
    if nvmrc:
        with open(os.path.join(root, nvmrc), "r", encoding="utf-8", errors="ignore") as f:
            version = f.read().strip()
        if version:
            return f"Node.js {version}", nvmrc

    for rel_path in files:
        if os.path.basename(rel_path) == "package.json":
            engine = _load_json(os.path.join(root, rel_path)).get("engines", {}).get("node")
            if engine:
                return f"Node.js {_clean_version(engine)}", rel_path

    return None, None


def detect_databases(root: str, files: list[str]) -> list[dict]:
    """Detecta motores de base de datos a partir de dependencias declaradas
    (drivers/ORMs conocidos) en package.json y requirements.txt."""
    found: dict[str, dict] = {}

    for rel_path in files:
        if os.path.basename(rel_path) == "package.json":
            data = _load_json(os.path.join(root, rel_path))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for package, (motor, tipo_de_gestor) in DB_NPM_SIGNATURES.items():
                if motor not in found and package in deps:
                    found[motor] = {
                        "motor": motor,
                        "tipo_de_gestor": tipo_de_gestor,
                        "version": _clean_version(deps[package]),
                        "evidencia": rel_path,
                    }

        if os.path.basename(rel_path) == "requirements.txt":
            try:
                with open(os.path.join(root, rel_path), "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read().lower()
            except OSError:
                continue
            for package, (motor, tipo_de_gestor) in DB_PY_SIGNATURES.items():
                if motor not in found and package in content:
                    version = _parse_requirements_version(os.path.join(root, rel_path), package)
                    found[motor] = {
                        "motor": motor,
                        "tipo_de_gestor": tipo_de_gestor,
                        "version": version,
                        "evidencia": rel_path,
                    }

    return list(found.values())


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


def detect_app_type(files: list[str], framework: str | None, framework_evidence: str | None) -> tuple[str | None, str | None]:
    has_templates = any("/templates/" in f or f.startswith("templates/") for f in files)
    has_static = any("/static/" in f or f.startswith("static/") for f in files)

    if framework in {"Flask", "Django"} and (has_templates or has_static):
        evidence_file = next(
            (f for f in files if "/templates/" in f or f.startswith("templates/")),
            None,
        )
        return "Portal web (server-side rendered)", evidence_file

    if framework:
        # Antes esto quedaba en None aunque sí teníamos evidencia concreta
        # (el archivo donde se detectó el framework).
        return f"Servicio/API ({framework})", framework_evidence

    return None, None


def scan(root: str) -> dict:
    files = list(iter_source_files(root))

    language, language_evidence = detect_language(files)
    framework, framework_evidence = detect_framework(root, files)
    dockerfiles = find_dockerfiles(root, files)
    runtime, runtime_evidence = detect_runtime(root, dockerfiles)
    if not runtime:
        runtime, runtime_evidence = detect_runtime_fallback(root, files)
    package_manager, package_manager_evidence = detect_package_manager(root, files, dockerfiles)
    app_type, app_type_evidence = detect_app_type(files, framework, framework_evidence)
    app_name = detect_app_name(root, framework_evidence)

    language_version, language_version_evidence = detect_language_version(root, language, language_evidence)
    framework_version, framework_version_evidence = detect_framework_version(root, framework, framework_evidence)
    databases = detect_databases(root, files)

    result = {
        "tipos_de_aplicaciones": [
            {
                "aplicacion": app_name,
                "tipo": app_type,
                "evidencia_en_repositorio": app_type_evidence,
            }
        ],
        "tecnologias_de_desarrollo": {
            "capa_lenguaje": {
                "tecnologia": language,
                "version": language_version,
                "donde_se_detecto": language_version_evidence or language_evidence,
            },
            "capa_framework": {
                "tecnologia": framework,
                "version": framework_version,
                "donde_se_detecto": framework_version_evidence or framework_evidence,
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

    if databases:
        result["mapa_de_bases_de_datos"] = {
            "tipos_de_bases_de_datos": [
                {"base": None, "tipo_de_gestor": db["tipo_de_gestor"], "evidencia": db["evidencia"]}
                for db in databases
            ],
            "tecnologias_empleadas": [
                {"motor": db["motor"], "version": db["version"], "donde_se_detecto": db["evidencia"]}
                for db in databases
            ],
        }

    return result


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
