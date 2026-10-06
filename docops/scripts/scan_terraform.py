"""
scan_terraform.py
===================
Escanea los archivos *.tf del repositorio y genera un YAML con la forma de
docs/generated/infraestructura-relevada.yml.

Usa un parser liviano basado en expresiones regulares sobre los bloques de
nivel superior de HCL (resource/provider/output). No es un parser HCL
completo (no evalúa expresiones, variables interpoladas, ni módulos
remotos) pero es suficiente para documentar automáticamente QUÉ recursos y
providers existen y EN QUÉ ARCHIVO, que es el objetivo de este bloque.

Uso:
    python scripts/scan_terraform.py --root . --out /tmp/infra-relevada-scanned.yml \
        --fingerprint <hash> --commit <sha>
"""

from __future__ import annotations

import argparse
import datetime
import os
import re

import yaml

EXCLUDED_DIRS = {".git", ".terraform"}

RESOURCE_RE = re.compile(r'resource\s+"(?P<type>\w+)"\s+"(?P<name>\w+)"\s*\{')
PROVIDER_BLOCK_RE = re.compile(
    r'(?P<name>\w+)\s*=\s*\{\s*source\s*=\s*"(?P<source>[^"]+)"\s*version\s*=\s*"(?P<version>[^"]+)"\s*\}',
    re.DOTALL,
)
OUTPUT_RE = re.compile(r'output\s+"(?P<name>\w+)"\s*\{')

# Mapeo best-effort de tipo de recurso Terraform -> rol en la tabla de
# "tipología de servidores y servicios". Si un tipo no está en este mapa,
# se publica igual con un rol genérico (no se descarta el recurso).
RESOURCE_ROLE_HINTS = {
    "azurerm_resource_group": "Agrupación lógica de recursos (Resource Group)",
    "azurerm_container_group": "Cómputo - contenedores (Azure Container Instances)",
    "azurerm_app_service": "Cómputo - App Service",
    "azurerm_kubernetes_cluster": "Cómputo - Kubernetes (AKS)",
    "azurerm_storage_account": "Almacenamiento",
    "azurerm_sql_server": "Base de datos - SQL Server",
    "azurerm_postgresql_server": "Base de datos - PostgreSQL",
    "azurerm_virtual_network": "Red - Virtual Network",
    "azurerm_key_vault": "Seguridad - Key Vault",
}


def iter_tf_files(root: str) -> list[str]:
    matches = []
    for current_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for filename in filenames:
            if filename.endswith(".tf"):
                rel_path = os.path.relpath(os.path.join(current_dir, filename), root).replace(os.sep, "/")
                matches.append(rel_path)
    return sorted(matches)


def parse_resources(root: str, tf_files: list[str]) -> list[dict]:
    resources = []
    for rel_path in tf_files:
        with open(os.path.join(root, rel_path), "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for match in RESOURCE_RE.finditer(content):
            resource_type = match.group("type")
            resources.append(
                {
                    "servicio": match.group("name"),
                    "tipo_y_rol": RESOURCE_ROLE_HINTS.get(
                        resource_type, f"Recurso Terraform ({resource_type})"
                    ),
                    "recurso_terraform": f"{resource_type}.{match.group('name')}",
                    "archivo": rel_path,
                }
            )
    return resources


def parse_providers(root: str, tf_files: list[str]) -> list[dict]:
    providers = []
    for rel_path in tf_files:
        with open(os.path.join(root, rel_path), "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for match in PROVIDER_BLOCK_RE.finditer(content):
            providers.append(
                {
                    "capa": "Provider de infraestructura (Terraform)",
                    "tecnologia_detectada": f"{match.group('name')} ({match.group('source')})",
                    "version": match.group("version"),
                    "evidencia": rel_path,
                }
            )
    return providers


def parse_outputs(root: str, tf_files: list[str]) -> list[str]:
    names = []
    for rel_path in tf_files:
        with open(os.path.join(root, rel_path), "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        names.extend(match.group("name") for match in OUTPUT_RE.finditer(content))
    return names


def scan(root: str) -> dict:
    tf_files = iter_tf_files(root)

    if not tf_files:
        return {}

    resources = parse_resources(root, tf_files)
    providers = parse_providers(root, tf_files)

    return {
        "tipologia_de_servidores_y_servicios": resources or None,
        "tecnologias_empleadas": providers or None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Raíz del repo a escanear")
    parser.add_argument(
        "--template",
        default="docs/generated/infraestructura-relevada.yml",
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
        "huella_terraform": args.fingerprint,
        "fecha_relevamiento": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    result = dict(base)
    for key, value in scanned.items():
        if value is not None:
            result[key] = value

    with open(args.out, "w", encoding="utf-8") as f:
        yaml.safe_dump(result, f, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    main()
