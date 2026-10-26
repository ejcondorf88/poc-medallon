import hashlib
import json
from pathlib import Path

import pytest

from andinos_etl import sharepoint
from andinos_etl.sharepoint import ConfiguracionSharePoint, ErrorPublicacionSharePoint, publicar_artifact


def _artifact(tmp_path: Path, estado: str = "APPROVED") -> Path:
    artifact = tmp_path / "approved"
    artifact.mkdir()
    (artifact / "modelo_estrella.xlsx").write_bytes(b"modelo")
    (artifact / "gate.json").write_text(json.dumps({"status": estado}), encoding="utf-8")
    (artifact / "manifest.json").write_text(json.dumps({"git_commit": "abc123"}), encoding="utf-8")
    (artifact / "checksums.sha256").write_text("hash  modelo_estrella.xlsx\n", encoding="utf-8")
    return artifact


def _configuracion() -> ConfiguracionSharePoint:
    return ConfiguracionSharePoint(
        site_id="site",
        folder="modelo-andinos",
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
    )


def test_no_publica_si_gate_no_esta_aprobado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _artifact(tmp_path, "REJECTED")
    monkeypatch.setenv("GITHUB_SHA", "abc123")

    with pytest.raises(ErrorPublicacionSharePoint, match="APPROVED"):
        publicar_artifact(artifact, _configuracion(), token="token")


def test_no_publica_si_commit_no_corresponde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "otro-commit")

    with pytest.raises(ErrorPublicacionSharePoint, match="commit"):
        publicar_artifact(artifact, _configuracion(), token="token")


def test_publica_payload_primero_y_checksum_ultimo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    subidos: list[str] = []
    hashes: dict[str, str] = {}

    monkeypatch.setattr(sharepoint, "_asegurar_carpeta", lambda *args: {})
    def subir(*args) -> None:
        archivo = args[3]
        subidos.append(archivo.name)
        hashes[archivo.name] = hashlib.sha256(archivo.read_bytes()).hexdigest()

    def hash_remoto(*args) -> str:
        return hashes[args[3].name]

    monkeypatch.setattr(sharepoint, "_subir_archivo", subir)
    monkeypatch.setattr(sharepoint, "_hash_remoto", hash_remoto)

    resultado = publicar_artifact(artifact, _configuracion(), token="token")

    assert resultado["status"] == "PUBLISHED"
    assert subidos[-1] == "checksums.sha256"
    assert subidos[-2] == "manifest.json"
    assert subidos.index("modelo_estrella.xlsx") < subidos.index("manifest.json")


def test_asegura_carpeta_anidada_creando_cada_nivel(monkeypatch: pytest.MonkeyPatch) -> None:
    creados: list[tuple[str, str, str]] = []

    def pedir_json(metodo: str, url: str, token: str, cuerpo: dict | None = None) -> dict:
        if metodo == "POST":
            item_id = f"item-{len(creados) + 1}"
            creados.append((cuerpo["name"], item_id, url))
            return {"id": item_id}
        return {"id": "root"}

    def item_carpeta(*args) -> dict:
        raise ErrorPublicacionSharePoint("Microsoft Graph respondió 404: no existe")

    monkeypatch.setattr(sharepoint, "_pedir_json", pedir_json)
    monkeypatch.setattr(sharepoint, "_item_carpeta", item_carpeta)

    resultado = sharepoint._asegurar_carpeta(_configuracion(), "token", "modelo-andinos/run-123-attempt-1")

    assert [(nombre, item_id) for nombre, item_id, _ in creados] == [
        ("modelo-andinos", "item-1"),
        ("run-123-attempt-1", "item-2"),
    ]
    assert "/drive/items/root/children" in creados[0][2]
    assert "/drive/items/item-1/children" in creados[1][2]
    assert resultado["id"] == "item-2"


def test_desde_entorno_mapea_variables_de_entorno_a_campos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for nombre, valor in {
        "SHAREPOINT_SITE_ID": "site-1",
        "SHAREPOINT_FOLDER": "General",
        "AZURE_TENANT_ID": "tenant-1",
        "AZURE_CLIENT_ID": "client-1",
        "AZURE_CLIENT_SECRET": "secret-1",
    }.items():
        monkeypatch.setenv(nombre, valor)

    configuracion = ConfiguracionSharePoint.desde_entorno()

    assert configuracion == ConfiguracionSharePoint(
        site_id="site-1",
        folder="General",
        tenant_id="tenant-1",
        client_id="client-1",
        client_secret="secret-1",
    )


def test_desde_entorno_lista_las_variables_que_faltan(monkeypatch: pytest.MonkeyPatch) -> None:
    for nombre in (
        "SHAREPOINT_SITE_ID",
        "SHAREPOINT_FOLDER",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(nombre, raising=False)

    with pytest.raises(ErrorPublicacionSharePoint, match="SHAREPOINT_SITE_ID, SHAREPOINT_FOLDER"):
        ConfiguracionSharePoint.desde_entorno()


def test_copia_estable_no_publica_si_gate_no_esta_aprobado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path, "REJECTED")
    monkeypatch.setenv("GITHUB_SHA", "abc123")

    with pytest.raises(ErrorPublicacionSharePoint, match="APPROVED"):
        sharepoint.publicar_copia_estable(artifact, _configuracion(), token="token")


def test_copia_estable_no_publica_si_commit_no_corresponde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "otro-commit")

    with pytest.raises(ErrorPublicacionSharePoint, match="commit"):
        sharepoint.publicar_copia_estable(artifact, _configuracion(), token="token")


def test_copia_estable_sube_libro_a_la_carpeta_configurada_y_verifica_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    carpeta: list[str] = []
    subidas: list[tuple[str, str]] = []
    hashes_remotos: list[tuple[str, str]] = []

    def asegurar(*args) -> dict:
        carpeta.append(args[2])
        return {"id": "carpeta"}

    def subir(config, token, destino, archivo: Path) -> None:
        subidas.append((destino, archivo.name))

    def hash_remoto(config, token, destino, archivo: Path) -> str:
        hashes_remotos.append((destino, archivo.name))
        return hashlib.sha256(archivo.read_bytes()).hexdigest()

    monkeypatch.setattr(sharepoint, "_asegurar_carpeta", asegurar)
    monkeypatch.setattr(sharepoint, "_subir_archivo", subir)
    monkeypatch.setattr(sharepoint, "_hash_remoto", hash_remoto)

    resultado = sharepoint.publicar_copia_estable(artifact, _configuracion(), token="token")

    assert carpeta == ["modelo-andinos"]
    assert subidas == [("modelo-andinos", "modelo_estrella.xlsx")]
    assert hashes_remotos == [("modelo-andinos", "modelo_estrella.xlsx")]
    assert resultado["status"] == "STABLE_PUBLISHED"
    assert resultado["folder"] == "modelo-andinos"
    assert resultado["file"] == "modelo_estrella.xlsx"
    assert resultado["sha256"] == hashlib.sha256((artifact / "modelo_estrella.xlsx").read_bytes()).hexdigest()
    assert resultado["commit"] == "abc123"


def test_copia_estable_falla_si_hash_remoto_no_coincide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setattr(sharepoint, "_asegurar_carpeta", lambda *args: {})
    monkeypatch.setattr(sharepoint, "_subir_archivo", lambda *args: None)
    monkeypatch.setattr(sharepoint, "_hash_remoto", lambda *args: "distinto")

    with pytest.raises(ErrorPublicacionSharePoint, match="hash remoto"):
        sharepoint.publicar_copia_estable(artifact, _configuracion(), token="token")


def test_copia_estable_falla_si_falta_el_libro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _artifact(tmp_path)
    (artifact / "modelo_estrella.xlsx").unlink()
    monkeypatch.setenv("GITHUB_SHA", "abc123")

    with pytest.raises(ErrorPublicacionSharePoint, match="modelo_estrella.xlsx"):
        sharepoint.publicar_copia_estable(artifact, _configuracion(), token="token")


def test_main_publica_run_versionado_y_copia_estable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    for nombre, valor in {
        "SHAREPOINT_SITE_ID": "site",
        "SHAREPOINT_FOLDER": "modelo-andinos",
        "AZURE_TENANT_ID": "tenant",
        "AZURE_CLIENT_ID": "client",
        "AZURE_CLIENT_SECRET": "secret",
    }.items():
        monkeypatch.setenv(nombre, valor)
    subidas: list[str] = []
    hashes: dict[str, str] = {}

    def subir(config, token, destino, archivo: Path) -> None:
        subidas.append(f"{destino}/{archivo.name}")
        hashes[archivo.name] = hashlib.sha256(archivo.read_bytes()).hexdigest()

    monkeypatch.setattr(sharepoint, "_asegurar_carpeta", lambda *args: {})
    monkeypatch.setattr(sharepoint, "_subir_archivo", subir)
    monkeypatch.setattr(sharepoint, "_hash_remoto", lambda config, token, destino, archivo: hashes[archivo.name])
    monkeypatch.setattr(sharepoint, "obtener_token", lambda config: "token")
    monkeypatch.setattr("sys.argv", ["sharepoint", str(artifact)])

    assert sharepoint.main() == 0

    salida = json.loads(capsys.readouterr().out)
    assert salida["status"] == "PUBLISHED"
    assert salida["copia_estable"]["status"] == "STABLE_PUBLISHED"
    assert "modelo-andinos/run-123-attempt-1/checksums.sha256" in subidas
    assert "modelo-andinos/modelo_estrella.xlsx" in subidas
