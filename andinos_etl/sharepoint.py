"""Publicación idempotente de un artefacto aprobado en SharePoint mediante Microsoft Graph."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"


class ErrorPublicacionSharePoint(RuntimeError):
    """SharePoint rechazó la publicación o no se pudo verificar el resultado."""


@dataclass(frozen=True)
class ConfiguracionSharePoint:
    site_id: str
    folder: str
    tenant_id: str
    client_id: str
    client_secret: str

    @classmethod
    def desde_entorno(cls) -> "ConfiguracionSharePoint":
        campos = {
            "site_id": "SHAREPOINT_SITE_ID",
            "folder": "SHAREPOINT_FOLDER",
            "tenant_id": "AZURE_TENANT_ID",
            "client_id": "AZURE_CLIENT_ID",
            "client_secret": "AZURE_CLIENT_SECRET",
        }
        valores = {campo: os.environ.get(variable) for campo, variable in campos.items()}
        faltantes = [variable for campo, variable in campos.items() if not valores[campo]]
        if faltantes:
            raise ErrorPublicacionSharePoint(f"Faltan credenciales o configuración: {', '.join(faltantes)}")
        return cls(**{campo: str(valor) for campo, valor in valores.items()})


def _pedir_json(metodo: str, url: str, token: str, cuerpo: dict[str, Any] | None = None) -> dict[str, Any]:
    datos = None
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode("utf-8")
    solicitud = urllib.request.Request(url, data=datos, method=metodo)
    solicitud.add_header("Authorization", f"Bearer {token}")
    solicitud.add_header("Accept", "application/json")
    if datos is not None:
        solicitud.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(solicitud, timeout=60) as respuesta:
            contenido = respuesta.read()
            return json.loads(contenido) if contenido else {}
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", errors="replace")
        raise ErrorPublicacionSharePoint(f"Microsoft Graph respondió {error.code}: {detalle}") from error
    except urllib.error.URLError as error:
        raise ErrorPublicacionSharePoint(f"No se pudo conectar con Microsoft Graph: {error.reason}") from error


def _pedir_bytes(url: str, token: str) -> bytes:
    solicitud = urllib.request.Request(url, method="GET")
    solicitud.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(solicitud, timeout=60) as respuesta:
            return respuesta.read()
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", errors="replace")
        raise ErrorPublicacionSharePoint(f"Microsoft Graph respondió {error.code}: {detalle}") from error


def obtener_token(configuracion: ConfiguracionSharePoint) -> str:
    url = f"{LOGIN_BASE}/{configuracion.tenant_id}/oauth2/v2.0/token"
    cuerpo = urllib.parse.urlencode({
        "client_id": configuracion.client_id,
        "client_secret": configuracion.client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode("utf-8")
    solicitud = urllib.request.Request(url, data=cuerpo, method="POST")
    solicitud.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(solicitud, timeout=60) as respuesta:
            return str(json.loads(respuesta.read())["access_token"])
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", errors="replace")
        raise ErrorPublicacionSharePoint(f"Entra ID rechazó la autenticación: {detalle}") from error
    except (KeyError, urllib.error.URLError) as error:
        raise ErrorPublicacionSharePoint(f"No se pudo obtener el token de Microsoft Graph: {error}") from error


def _ruta_graph(carpeta: str) -> str:
    return "/".join(urllib.parse.quote(segmento, safe="") for segmento in carpeta.strip("/").split("/") if segmento)


def _item_carpeta(configuracion: ConfiguracionSharePoint, token: str, carpeta: str) -> dict[str, Any]:
    return _pedir_json(
        "GET",
        f"{GRAPH_BASE}/sites/{configuracion.site_id}/drive/root:/{_ruta_graph(carpeta)}",
        token,
    )


def _asegurar_carpeta(configuracion: ConfiguracionSharePoint, token: str, carpeta: str) -> dict[str, Any]:
    segmentos = [segmento for segmento in carpeta.strip("/").split("/") if segmento]
    padre = _pedir_json("GET", f"{GRAPH_BASE}/sites/{configuracion.site_id}/drive/root", token)
    actual = ""
    for segmento in segmentos:
        actual = f"{actual}/{segmento}" if actual else segmento
        try:
            padre = _item_carpeta(configuracion, token, actual)
            continue
        except ErrorPublicacionSharePoint as error:
            if "respondió 404" not in str(error):
                raise
        cuerpo = {
            "name": segmento,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail",
        }
        padre = _pedir_json(
            "POST",
            f"{GRAPH_BASE}/sites/{configuracion.site_id}/drive/items/{padre['id']}/children",
            token,
            cuerpo,
        )
    return padre



def _subir_archivo(
    configuracion: ConfiguracionSharePoint,
    token: str,
    carpeta: str,
    archivo: Path,
) -> None:
    url = (
        f"{GRAPH_BASE}/sites/{configuracion.site_id}/drive/root:"
        f"/{_ruta_graph(carpeta)}/{urllib.parse.quote(archivo.name, safe='')}:/content"
    )
    solicitud = urllib.request.Request(url, data=archivo.read_bytes(), method="PUT")
    solicitud.add_header("Authorization", f"Bearer {token}")
    solicitud.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(solicitud, timeout=120) as respuesta:
            respuesta.read()
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", errors="replace")
        raise ErrorPublicacionSharePoint(f"No se pudo subir {archivo.name}: {detalle}") from error


def _hash_remoto(configuracion: ConfiguracionSharePoint, token: str, carpeta: str, archivo: Path) -> str:
    url = (
        f"{GRAPH_BASE}/sites/{configuracion.site_id}/drive/root:"
        f"/{_ruta_graph(carpeta)}/{urllib.parse.quote(archivo.name, safe='')}/content"
    )
    return hashlib.sha256(_pedir_bytes(url, token)).hexdigest()


LIBRO_ESTABLE = "modelo_estrella.xlsx"


def _validar_publicable(artifact_dir: Path) -> dict[str, Any]:
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    gate = json.loads((artifact_dir / "gate.json").read_text(encoding="utf-8"))
    if gate.get("status") != "APPROVED":
        raise ErrorPublicacionSharePoint("El artifact no tiene un quality gate APPROVED")
    commit_esperado = os.environ.get("GITHUB_SHA")
    if commit_esperado and manifest.get("git_commit") != commit_esperado:
        raise ErrorPublicacionSharePoint("El manifest no corresponde al commit de esta ejecución")
    return manifest


def publicar_artifact(
    artifact_dir: Path,
    configuracion: ConfiguracionSharePoint,
    token: str | None = None,
) -> dict[str, Any]:
    manifest = _validar_publicable(artifact_dir)
    token = token or obtener_token(configuracion)
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    version = f"{configuracion.folder.strip('/')}/run-{run_id}-attempt-{run_attempt}"
    _asegurar_carpeta(configuracion, token, version)
    archivos = [ruta for ruta in artifact_dir.rglob("*") if ruta.is_file()]
    prioridad = {"checksums.sha256": 4, "manifest.json": 3, "gate.json": 2}
    archivos.sort(key=lambda ruta: (prioridad.get(ruta.name, 1), ruta.relative_to(artifact_dir).as_posix()))
    for archivo in archivos:
        _subir_archivo(configuracion, token, version, archivo)
    for archivo in archivos:
        hash_local = hashlib.sha256(archivo.read_bytes()).hexdigest()
        hash_remoto = _hash_remoto(configuracion, token, version, archivo)
        if hash_local != hash_remoto:
            raise ErrorPublicacionSharePoint(f"El hash remoto no coincide para {archivo.name}")
    return {
        "status": "PUBLISHED",
        "site_id": configuracion.site_id,
        "folder": version,
        "files": len(archivos),
        "commit": manifest.get("git_commit"),
    }


def publicar_copia_estable(
    artifact_dir: Path,
    configuracion: ConfiguracionSharePoint,
    token: str | None = None,
    carpeta: str | None = None,
) -> dict[str, Any]:
    manifest = _validar_publicable(artifact_dir)
    candidatos = [ruta for ruta in artifact_dir.rglob(LIBRO_ESTABLE) if ruta.is_file()]
    if len(candidatos) != 1:
        raise ErrorPublicacionSharePoint(
            f"Se esperaba un único {LIBRO_ESTABLE} en el artifact y hay {len(candidatos)}"
        )
    libro = candidatos[0]
    destino = (carpeta or configuracion.folder).strip("/")
    if not destino:
        raise ErrorPublicacionSharePoint("La carpeta destino de la copia estable está vacía")
    token = token or obtener_token(configuracion)
    _asegurar_carpeta(configuracion, token, destino)
    _subir_archivo(configuracion, token, destino, libro)
    hash_local = hashlib.sha256(libro.read_bytes()).hexdigest()
    if hash_local != _hash_remoto(configuracion, token, destino, libro):
        raise ErrorPublicacionSharePoint(
            f"El hash remoto no coincide para {libro.name} en la copia estable"
        )
    return {
        "status": "STABLE_PUBLISHED",
        "site_id": configuracion.site_id,
        "folder": destino,
        "file": libro.name,
        "sha256": hash_local,
        "commit": manifest.get("git_commit"),
    }


def main() -> int:
    artifact_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("andinos-model-approved")
    try:
        configuracion = ConfiguracionSharePoint.desde_entorno()
        resultado = publicar_artifact(artifact_dir, configuracion)
        resultado["copia_estable"] = publicar_copia_estable(artifact_dir, configuracion)
        print(json.dumps(resultado, ensure_ascii=False, sort_keys=True))
        return 0
    except (ErrorPublicacionSharePoint, OSError, ValueError) as error:
        print(f"ERROR SHAREPOINT: {error}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
