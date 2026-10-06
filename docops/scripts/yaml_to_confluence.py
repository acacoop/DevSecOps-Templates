"""
yaml_to_confluence.py
======================
Convierte un archivo YAML de docs/ (manifest/ o generated/) en HTML
"storage format", listo para publicarse con confluence_publish.py.

Es un conversor GENÉRICO: no sabe nada de "Negocio" ni "Desarrollo"
específicamente. Recorre la estructura del YAML y aplica estas reglas:

  - dict               -> una sección con <h2>/<h3> por cada clave
  - list de dicts       -> una tabla (columnas = claves del primer elemento)
  - valor None          -> texto de "faltante" (parametrizable)
  - "{{jira.algo}}"     -> se interpreta como placeholder aún no
                           sincronizado con Jira, y se muestra como tal

Uso:
    python scripts/yaml_to_confluence.py --yaml docs/generated/desarrollo.yml --title "2. Desarrollo"
    python scripts/yaml_to_confluence.py --yaml docs/manifest/negocio.yml --title "1. Negocio" --out /tmp/negocio.html
"""

from __future__ import annotations

import argparse
import html
import re
import sys

import yaml

JIRA_PLACEHOLDER_RE = re.compile(r"^\{\{\s*jira[.\-][\w.]+\s*\}\}$")

MISSING_FROM_AGENT = "<em>No determinado desde el repositorio</em>"
MISSING_FROM_JIRA = "<em>Pendiente de sincronización con Jira</em>"


def humanize(key: str) -> str:
    """Convierte 'nombre_iniciativa' en 'Nombre iniciativa'."""
    text = key.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else text


def render_scalar(value) -> str:
    if value is None:
        return MISSING_FROM_AGENT
    text = str(value)
    if JIRA_PLACEHOLDER_RE.match(text.strip()):
        return MISSING_FROM_JIRA
    return html.escape(text)


def render_table(rows: list[dict]) -> str:
    """Renderiza una lista de dicts como tabla HTML (storage format)."""
    if not rows:
        return f"<p>{MISSING_FROM_AGENT}</p>"

    columns = list(rows[0].keys())
    header = "".join(f"<th>{html.escape(humanize(c))}</th>" for c in columns)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{render_scalar(row.get(c))}</td>" for c in columns)
        body_rows.append(f"<tr>{cells}</tr>")

    return (
        "<table><tbody>"
        f"<tr>{header}</tr>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


def render_section(key: str, value, level: int) -> str:
    heading_tag = f"h{min(level, 6)}"
    title_html = f"<{heading_tag}>{html.escape(humanize(key))}</{heading_tag}>"

    if isinstance(value, dict):
        return title_html + render_dict(value, level + 1)
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return title_html + render_table(value)
        if not value:
            return title_html + f"<p>{MISSING_FROM_AGENT}</p>"
        items = "".join(f"<li>{render_scalar(v)}</li>" for v in value)
        return title_html + f"<ul>{items}</ul>"
    return title_html + f"<p>{render_scalar(value)}</p>"


def render_dict(data: dict, level: int = 2) -> str:
    parts = [render_section(key, value, level) for key, value in data.items()]
    return "".join(parts)


def render_yaml_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(f"{path}: se esperaba un mapeo YAML en la raíz")

    return render_dict(data, level=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yaml", required=True, help="Ruta al archivo YAML de docs/")
    parser.add_argument(
        "--title",
        required=True,
        help="Título de la sección (se antepone como <h1> al contenido)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Archivo de salida. Si se omite, imprime a stdout.",
    )
    args = parser.parse_args()

    body = render_yaml_file(args.yaml)
    full_html = f"<h1>{html.escape(args.title)}</h1>" + body

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(full_html)
        print(f"Generado: {args.out}", file=sys.stderr)
    else:
        print(full_html)


if __name__ == "__main__":
    main()
