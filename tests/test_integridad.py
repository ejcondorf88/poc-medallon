import json
from pathlib import Path

import pytest

from andinos_etl.integridad import (
    CHECKSUMS_FILENAME,
    ErrorIntegridadArtifact,
    construir_manifest,
    escribir_manifest,
    generar_checksums,
    leer_checksums,
    ruta_relativa_segura,
    sha256_file,
    verificar_checksums,
)


def test_hash_manifest_y_checksums_son_estables(tmp_path: Path) -> None:
    payload = tmp_path / "payload.csv"
    payload.write_text("id,valor\n1,10\n", encoding="utf-8")
    manifest = construir_manifest(
        "plata",
        {"payload.csv": sha256_file(payload)},
        git_version="v1",
        git_commit="abc123",
        created_at_utc="2026-01-01T00:00:00Z",
        rows_processed=1,
    )
    ruta_manifest = escribir_manifest(manifest, tmp_path / "manifest.json")
    generar_checksums(tmp_path)

    assert manifest["schema_version"] == 1
    assert json.loads(ruta_manifest.read_text(encoding="utf-8")) == manifest
    checksums = leer_checksums(tmp_path / CHECKSUMS_FILENAME)
    assert set(checksums) == {"manifest.json", "payload.csv"}
    assert verificar_checksums(tmp_path).ok


@pytest.mark.parametrize("mutacion", ["alterado", "faltante", "extra"])
def test_verificacion_detecta_tampering_missing_y_extra(tmp_path: Path, mutacion: str) -> None:
    payload = tmp_path / "payload.csv"
    payload.write_text("id,valor\n1,10\n", encoding="utf-8")
    manifest = construir_manifest(
        "plata", {"payload.csv": sha256_file(payload)}, created_at_utc="2026-01-01T00:00:00Z"
    )
    escribir_manifest(manifest, tmp_path / "manifest.json")
    generar_checksums(tmp_path)
    if mutacion == "alterado":
        payload.write_text("id,valor\n1,11\n", encoding="utf-8")
    elif mutacion == "faltante":
        payload.unlink()
    else:
        (tmp_path / "extra.csv").write_text("id\n2\n", encoding="utf-8")

    resultado = verificar_checksums(tmp_path)

    marcadores = {"alterado": "alterado", "faltante": "Falta", "extra": "extra"}
    assert not resultado.ok
    assert any(marcadores[mutacion] in error for error in resultado.errores)


@pytest.mark.parametrize("ruta", ["/tmp/archivo.csv", "../archivo.csv", "carpeta/../../archivo.csv"])
def test_rechaza_rutas_relativas_inseguras(ruta: str) -> None:
    with pytest.raises(ErrorIntegridadArtifact):
        ruta_relativa_segura(ruta)
