"""
compute_fingerprint.py
========================
CLI fina sobre fingerprint_utils: imprime a stdout la huella (sha256) de un
conjunto de archivos, dado por patrones glob relativos a la raíz del repo.

Uso:
    python scripts/compute_fingerprint.py --include "azure-vote/**" "Dockerfile*" --exclude "**/__pycache__/**"
    python scripts/compute_fingerprint.py --include "infra/*.tf" "infra/**/*.tf"

NOTA sobre patrones: se matchean con fnmatch (shell-style), NO con glob
recursivo real. "**/*.tf" no matchea archivos directamente dentro de la
carpeta (solo anidados en subcarpetas) porque "/" es literal. Por eso,
para cubrir "archivos sueltos en la carpeta" + "archivos en subcarpetas",
hay que pasar AMBOS patrones: "infra/*.tf" Y "infra/**/*.tf".
"""

from __future__ import annotations

import argparse

from fingerprint_utils import compute_fingerprint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Raíz del repo (default: directorio actual)")
    parser.add_argument(
        "--include",
        nargs="+",
        required=True,
        help="Uno o más patrones glob a incluir (ej. 'infra/**/*.tf')",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=[],
        help="Patrones glob a excluir",
    )
    args = parser.parse_args()

    digest, files = compute_fingerprint(args.root, args.include, args.exclude)
    print(digest)


if __name__ == "__main__":
    main()
