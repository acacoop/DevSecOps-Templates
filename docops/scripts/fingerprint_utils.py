"""
fingerprint_utils.py
======================
Calcula una huella (hash) determinística sobre un conjunto de archivos del
repositorio. Se usa para decidir si realmente cambió algo relevante (código
o Terraform) antes de volver a escanear y publicar en Confluence.

La huella depende únicamente del CONTENIDO de los archivos, no de fechas ni
de metadatos de git, así que dos checkouts distintos del mismo commit
producen siempre la misma huella.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def collect_files(root: str, include: list[str], exclude: list[str]) -> list[str]:
    """Recorre `root` y devuelve, ordenados, los paths (relativos, con `/`)
    que matchean algún patrón de `include` y ninguno de `exclude`.
    """
    matched = []
    for current_dir, dirnames, filenames in os.walk(root):
        # Poda de directorios ignorados ANTES de bajar a ellos (más rápido
        # y evita falsos positivos de patrones tipo "**/.git/**").
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", ".terraform", "node_modules"}]

        for filename in filenames:
            full_path = os.path.join(current_dir, filename)
            rel_path = os.path.relpath(full_path, root).replace(os.sep, "/")

            if exclude and _matches_any(rel_path, exclude):
                continue
            if _matches_any(rel_path, include):
                matched.append(rel_path)

    return sorted(matched)


def compute_fingerprint(root: str, include: list[str], exclude: list[str] | None = None) -> tuple[str, list[str]]:
    """Devuelve (hash_hexdigest, lista_de_archivos_incluidos).

    El hash se arma concatenando "path\\0contenido\\0" de cada archivo
    (ordenados por path) para que el resultado sea estable y reproducible.
    """
    exclude = exclude or []
    files = collect_files(root, include, exclude)

    digest = hashlib.sha256()
    for rel_path in files:
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        with open(os.path.join(root, rel_path), "rb") as f:
            digest.update(f.read())
        digest.update(b"\0")

    return digest.hexdigest(), files
