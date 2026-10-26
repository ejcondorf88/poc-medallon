"""Integridad de artefactos mediante hashes SHA-256 y manifiestos deterministas."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

SCHEMA_VERSION = 1
CHECKSUMS_FILENAME = "checksums.sha256"
MANIFEST_FILENAME = "manifest.json"


class ErrorIntegridadArtifact(ValueError):
    """La estructura o el contenido de un artefacto no cumple el contrato de integridad."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def ruta_relativa_segura(ruta: str | Path) -> Path:
    texto = str(ruta).replace("\\", "/")
    relativa = PurePosixPath(texto)
    windows = PureWindowsPath(str(ruta))
    if not texto or not relativa.parts or relativa.is_absolute() or windows.is_absolute() or windows.drive or ".." in relativa.parts:
        raise ErrorIntegridadArtifact(f"Ruta relativa insegura: {ruta}")
    if any(parte in ("", ".") for parte in relativa.parts[:-1]):
        raise ErrorIntegridadArtifact(f"Ruta relativa insegura: {ruta}")
    return Path(*relativa.parts)


def construir_manifest(
    stage: str,
    hashes: Mapping[str, str] | None = None,
    *,
    git_version: str | None = None,
    git_commit: str | None = None,
    created_at_utc: str | None = None,
    rows_processed: int | None = None,
    ventas_netas: float | None = None,
    costo_total: float | None = None,
    margen: float | None = None,
    quality_gate: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "git_version": git_version,
        "git_commit": git_commit,
        "created_at_utc": created_at_utc or utc_now_iso(),
        "rows_processed": rows_processed,
        "hashes": {ruta_relativa_segura(ruta).as_posix(): digest for ruta, digest in (hashes or {}).items()},
    }
    for nombre, valor in (
        ("ventas_netas", ventas_netas),
        ("costo_total", costo_total),
        ("margen", margen),
    ):
        if valor is not None:
            manifest[nombre] = valor
    if quality_gate is not None:
        manifest["quality_gate"] = dict(quality_gate)
    if extra:
        manifest.update(dict(extra))
    return manifest


def escribir_manifest(manifest: Mapping[str, Any], ruta: Path) -> Path:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ErrorIntegridadArtifact("schema_version de manifest no soportado")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ruta


def leer_manifest(ruta: Path) -> dict[str, Any]:
    manifest = json.loads(ruta.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ErrorIntegridadArtifact(f"schema_version de manifest no soportado: {manifest.get('schema_version')}")
    for valor in manifest.get("hashes", {}):
        ruta_relativa_segura(valor)
    return manifest


def archivos_payload(raiz: Path, excluir: set[str] | None = None) -> list[Path]:
    excluidos = excluir or set()
    return sorted(
        (ruta for ruta in raiz.rglob("*") if ruta.is_file() and ruta.relative_to(raiz).as_posix() not in excluidos),
        key=lambda ruta: ruta.relative_to(raiz).as_posix(),
    )


def generar_checksums(raiz: Path, ruta_salida: Path | None = None) -> Path:
    raiz = raiz.resolve()
    salida = (ruta_salida or raiz / CHECKSUMS_FILENAME).resolve()
    try:
        salida.relative_to(raiz)
    except ValueError as error:
        raise ErrorIntegridadArtifact("checksums.sha256 debe quedar dentro del artefacto") from error
    lineas = []
    for ruta in archivos_payload(raiz, {salida.relative_to(raiz).as_posix()}):
        relativa = ruta.relative_to(raiz).as_posix()
        ruta_relativa_segura(relativa)
        lineas.append(f"{sha256_file(ruta)}  {relativa}")
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("\n".join(lineas) + ("\n" if lineas else ""), encoding="utf-8")
    return salida


def leer_checksums(ruta: Path) -> dict[str, str]:
    if not ruta.exists():
        raise ErrorIntegridadArtifact(f"No existe el archivo de checksums: {ruta}")
    hashes: dict[str, str] = {}
    for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        if not linea.strip():
            continue
        try:
            digest, relativa = linea.split("  ", 1)
        except ValueError as error:
            raise ErrorIntegridadArtifact(f"Línea de checksum inválida: {numero}") from error
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ErrorIntegridadArtifact(f"Hash SHA-256 inválido: {numero}")
        hashes[ruta_relativa_segura(relativa).as_posix()] = digest
    return hashes


@dataclass(frozen=True)
class ResultadoVerificacion:
    ok: bool
    errores: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return self.ok


def verificar_checksums(raiz: Path) -> ResultadoVerificacion:
    raiz = raiz.resolve()
    ruta_checksums = raiz / CHECKSUMS_FILENAME
    try:
        esperados = leer_checksums(ruta_checksums)
    except (OSError, ErrorIntegridadArtifact) as error:
        return ResultadoVerificacion(False, (str(error),))
    errores: list[str] = []
    reales = {ruta.relative_to(raiz).as_posix() for ruta in archivos_payload(raiz, {CHECKSUMS_FILENAME})}
    for relativa, esperado in esperados.items():
        ruta = raiz / Path(*PurePosixPath(relativa).parts)
        if not ruta.is_file():
            errores.append(f"Falta archivo: {relativa}")
            continue
        obtenido = sha256_file(ruta)
        if obtenido != esperado:
            errores.append(f"Archivo alterado: {relativa}")
    for relativa in sorted(reales - set(esperados)):
        errores.append(f"Archivo extra: {relativa}")
    return ResultadoVerificacion(not errores, tuple(errores))
