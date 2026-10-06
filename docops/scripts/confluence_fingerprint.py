"""
confluence_fingerprint.py
===========================
CLI para leer o escribir la huella (fingerprint) guardada en una página de
Confluence, usando una content property (clave "docops:fingerprint").

Se usa en el workflow ANTES de escanear, para decidir si hace falta volver
a generar contenido:

    huella_guardada = confluence_fingerprint.py --title "X" --get
    huella_actual    = compute_fingerprint.py ...

    si son iguales -> no hacer nada (no hay cambios reales)
    si son distintas -> escanear, publicar, y luego:
        confluence_fingerprint.py --title "X" --set <huella_actual>

Uso:
    python scripts/confluence_fingerprint.py --title "2. Desarrollo" --get
    python scripts/confluence_fingerprint.py --title "2. Desarrollo" --set <hash>
"""

from __future__ import annotations

import argparse
import sys

from confluence_publish import (
    find_page,
    get_page_property,
    load_config,
    set_page_property,
)
from confluence_publish import _session  # reutiliza la misma sesión autenticada

PROPERTY_KEY = "docops:fingerprint"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True, help="Título exacto de la página")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--get", action="store_true", help="Imprime la huella guardada (vacío si no hay)")
    group.add_argument("--set", metavar="VALUE", help="Guarda una nueva huella")
    args = parser.parse_args()

    config = load_config()
    session = _session(config)

    page = find_page(session, config, args.title)

    if args.get:
        if page is None:
            print("", end="")  # página no existe todavía -> sin huella previa
            return
        value = get_page_property(session, config, page["id"], PROPERTY_KEY)
        print(value or "", end="")
        return

    # --set
    if page is None:
        print(
            f"ERROR: no se puede guardar la huella, la página '{args.title}' no existe todavía "
            "(hay que publicarla primero con confluence_publish.py).",
            file=sys.stderr,
        )
        sys.exit(1)

    set_page_property(session, config, page["id"], PROPERTY_KEY, args.set)
    print(f"Huella guardada en '{args.title}': {args.set}", file=sys.stderr)


if __name__ == "__main__":
    main()
