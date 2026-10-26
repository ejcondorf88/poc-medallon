import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from andinos_etl.config import Settings
from andinos_etl.excepciones import ErrorCalidad, ErrorPublicacion
from andinos_etl.pipeline import PipelineMedallon


def _crear_capa(ruta: Path, marcador: str) -> None:
    ruta.mkdir(parents=True, exist_ok=True)
    (ruta / "archivo.txt").write_text(marcador, encoding="utf-8")


def test_promocion_reemplaza_capas_y_elimina_staging(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    for capa in PipelineMedallon.CAPAS_PUBLICADAS:
        _crear_capa(settings.staging_dir / capa, f"nuevo-{capa}")
        _crear_capa(tmp_path / capa, f"anterior-{capa}")

    PipelineMedallon(settings)._promover_staging()

    for capa in PipelineMedallon.CAPAS_PUBLICADAS:
        assert (tmp_path / capa / "archivo.txt").read_text(encoding="utf-8") == f"nuevo-{capa}"
    assert not settings.staging_dir.exists()


def test_promocion_fallida_restaura_publicacion_anterior(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    for capa in PipelineMedallon.CAPAS_PUBLICADAS:
        _crear_capa(settings.staging_dir / capa, f"nuevo-{capa}")
        _crear_capa(tmp_path / capa, f"anterior-{capa}")

    reemplazo_real = os.replace

    def reemplazar_con_fallo(origen: Path, destino: Path) -> None:
        if origen.parent == settings.staging_dir and destino == tmp_path / "oro":
            raise OSError("archivo bloqueado")
        reemplazo_real(origen, destino)

    with patch("andinos_etl.pipeline.os.replace", side_effect=reemplazar_con_fallo):
        with pytest.raises(ErrorPublicacion):
            PipelineMedallon(settings)._promover_staging()

    for capa in PipelineMedallon.CAPAS_PUBLICADAS:
        assert (tmp_path / capa / "archivo.txt").read_text(encoding="utf-8") == f"anterior-{capa}"


def test_fallo_de_calidad_no_modifica_la_publicacion_anterior(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(data_dir=tmp_path)
    _crear_capa(tmp_path / "oro", "publicado-anterior")
    pipeline = PipelineMedallon(settings)

    monkeypatch.setattr(pipeline.bronce, "ejecutar", lambda: {})
    monkeypatch.setattr(
        pipeline.plata,
        "ejecutar",
        lambda _: {"ventas": SimpleNamespace(limpio=pd.DataFrame())},
    )
    monkeypatch.setattr(pipeline.oro, "ejecutar", lambda _: {"fact_ventas": pd.DataFrame()})
    monkeypatch.setattr(
        pipeline.calidad,
        "ejecutar",
        lambda _: pd.DataFrame([{"estado": "FALLO"}]),
    )

    with pytest.raises(ErrorCalidad):
        pipeline.ejecutar()

    assert (tmp_path / "oro" / "archivo.txt").read_text(encoding="utf-8") == "publicado-anterior"
