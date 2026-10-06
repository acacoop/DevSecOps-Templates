"""
confluence_publish.py
======================
Crea o actualiza (de forma versionada) una página de Confluence Cloud a partir
de contenido HTML en "storage format".

Es el componente central del pipeline DocOps: tanto el uso manual (esta
prueba) como el workflow de GitHub Actions llaman a este mismo script, con la
misma lógica de "buscar por título -> crear o actualizar".

Autenticación: Confluence Cloud usa Basic Auth con (email, API token).
Variables de entorno requeridas:
    CONFLUENCE_BASE_URL    ej. https://acacoop-sandbox.atlassian.net/wiki
    CONFLUENCE_EMAIL       email de la cuenta dueña del API token
    CONFLUENCE_API_TOKEN   token generado en id.atlassian.com
    CONFLUENCE_SPACE_KEY   ej. DOCSTEC

Uso:
    python confluence_publish.py --title "docops-sandbox" --body-file paginas/raiz.html
    python confluence_publish.py --title "1. Negocio" --parent-title "docops-sandbox" --body-file paginas/negocio.html
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

REQUIRED_ENV_VARS = [
    "CONFLUENCE_BASE_URL",
    "CONFLUENCE_EMAIL",
    "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_SPACE_KEY",
]


def load_config() -> dict:
    """Lee y valida las variables de entorno necesarias para autenticar."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        print(
            f"ERROR: faltan variables de entorno: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    return {
        "base_url": os.environ["CONFLUENCE_BASE_URL"].rstrip("/"),
        "email": os.environ["CONFLUENCE_EMAIL"],
        "api_token": os.environ["CONFLUENCE_API_TOKEN"],
        "space_key": os.environ["CONFLUENCE_SPACE_KEY"],
    }


def _raise_with_context(response: requests.Response) -> None:
    """Como raise_for_status(), pero imprime la URL real y el cuerpo de la
    respuesta antes de fallar. Sin esto, un error de configuración (ej. una
    CONFLUENCE_BASE_URL mal armada) se ve como un JSONDecodeError confuso en
    vez de mostrar directamente qué devolvió el servidor.
    """
    if response.ok:
        return
    print(f"ERROR {response.status_code} en {response.url}", file=sys.stderr)
    print(response.text[:1000], file=sys.stderr)
    response.raise_for_status()


def _session(config: dict) -> requests.Session:
    session = requests.Session()
    session.auth = (config["email"], config["api_token"])
    session.headers.update({"Content-Type": "application/json"})
    return session


def find_page(session: requests.Session, config: dict, title: str) -> dict | None:
    """Busca una página por título exacto dentro del espacio configurado.

    Devuelve el dict de la página (incluyendo su número de versión) o None
    si no existe todavía.
    """
    url = f"{config['base_url']}/rest/api/content"
    params = {
        "spaceKey": config["space_key"],
        "title": title,
        "expand": "version",
    }
    response = session.get(url, params=params, timeout=30)
    _raise_with_context(response)
    results = response.json().get("results", [])
    return results[0] if results else None


def create_page(
    session: requests.Session,
    config: dict,
    title: str,
    html_body: str,
    parent_id: str | None,
) -> dict:
    """Crea una página nueva. Si parent_id está presente, queda anidada."""
    url = f"{config['base_url']}/rest/api/content"
    payload = {
        "type": "page",
        "title": title,
        "space": {"key": config["space_key"]},
        "body": {"storage": {"value": html_body, "representation": "storage"}},
    }
    if parent_id:
        payload["ancestors"] = [{"id": parent_id}]

    response = session.post(url, json=payload, timeout=30)
    _raise_with_context(response)
    return response.json()


def update_page(
    session: requests.Session,
    config: dict,
    page: dict,
    html_body: str,
) -> dict:
    """Actualiza una página existente. Confluence versiona automáticamente:
    cada PUT con version.number incrementado queda en el historial nativo
    de la página, visible en "Page history" dentro de Confluence.
    """
    page_id = page["id"]
    next_version = page["version"]["number"] + 1

    url = f"{config['base_url']}/rest/api/content/{page_id}"
    payload = {
        "id": page_id,
        "type": "page",
        "title": page["title"],
        "space": {"key": config["space_key"]},
        "body": {"storage": {"value": html_body, "representation": "storage"}},
        "version": {"number": next_version},
    }

    response = session.put(url, json=payload, timeout=30)
    _raise_with_context(response)
    return response.json()


def ensure_page(
    config: dict,
    title: str,
    html_body: str,
    parent_title: str | None = None,
) -> dict:
    """Punto de entrada principal: crea o actualiza la página `title`.

    Si se pasa `parent_title`, primero resuelve (o crea) esa página padre,
    para poder anidar la página hija debajo.
    """
    session = _session(config)

    parent_id = None
    if parent_title:
        parent_page = find_page(session, config, parent_title)
        if parent_page is None:
            print(f"Página padre '{parent_title}' no existe. Creándola vacía...")
            parent_page = create_page(
                session,
                config,
                parent_title,
                html_body="<p>Página raíz generada automáticamente por el pipeline DocOps.</p>",
                parent_id=None,
            )
        parent_id = parent_page["id"]

    existing_page = find_page(session, config, title)
    if existing_page is None:
        print(f"Creando página nueva: '{title}'")
        result = create_page(session, config, title, html_body, parent_id)
    else:
        print(
            f"Actualizando página existente: '{title}' "
            f"(v{existing_page['version']['number']} -> v{existing_page['version']['number'] + 1})"
        )
        result = update_page(session, config, existing_page, html_body)

    page_url = f"{config['base_url']}{result['_links']['webui']}"
    print(f"OK -> {page_url}")
    return result


def get_page_property(
    session: requests.Session, config: dict, page_id: str, key: str
) -> str | None:
    """Lee una content property de una página (ej. la huella guardada).
    Devuelve None si la página no tiene esa property todavía.
    """
    url = f"{config['base_url']}/rest/api/content/{page_id}/property/{key}"
    response = session.get(url, timeout=30)
    if response.status_code == 404:
        return None
    _raise_with_context(response)
    return response.json().get("value")


def set_page_property(
    session: requests.Session, config: dict, page_id: str, key: str, value: str
) -> None:
    """Crea o actualiza (versionando) una content property de una página.
    Se usa para guardar la huella (fingerprint) de la última vez que se
    generó contenido para esta página.
    """
    url = f"{config['base_url']}/rest/api/content/{page_id}/property/{key}"
    get_response = session.get(url, timeout=30)

    if get_response.status_code == 404:
        create_url = f"{config['base_url']}/rest/api/content/{page_id}/property"
        payload = {"key": key, "value": value}
        response = session.post(create_url, json=payload, timeout=30)
        _raise_with_context(response)
        return

    _raise_with_context(get_response)
    current_version = get_response.json()["version"]["number"]
    payload = {"key": key, "value": value, "version": {"number": current_version + 1}}
    response = session.put(url, json=payload, timeout=30)
    _raise_with_context(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True, help="Título exacto de la página")
    parser.add_argument(
        "--parent-title",
        default=None,
        help="Título de la página padre (se crea vacía si no existe)",
    )
    parser.add_argument(
        "--body-file",
        required=True,
        help="Archivo con el contenido en formato storage (HTML) de Confluence",
    )
    args = parser.parse_args()

    config = load_config()

    with open(args.body_file, "r", encoding="utf-8") as f:
        html_body = f.read()

    ensure_page(config, args.title, html_body, parent_title=args.parent_title)


if __name__ == "__main__":
    main()
