from pathlib import Path

import pandas as pd
import pytest

from andinos_etl.bronce import CapaBronce
from andinos_etl.config import COLUMNAS_ESPERADAS, Settings
from andinos_etl.excepciones import ErrorEsquema
from andinos_etl.plata import LimpiadorClientes
from andinos_etl.stages import ejecutar_bronce


def crear_bronze(data_dir: Path) -> None:
    bronze = data_dir / "bronce"
    bronze.mkdir(parents=True)
    for tabla, columnas in COLUMNAS_ESPERADAS.items():
        fila = {columna: "valor" for columna in columnas}
        if tabla == "ventas":
            fila["cantidad"] = "1"
            fila["precio_unitario"] = "5.00"
            fila["descuento_pct"] = "0.00"
            fila["costo_unitario"] = "2.00"
        pd.DataFrame([fila]).to_csv(bronze / f"{tabla}.csv", index=False, encoding="utf-8-sig")


def test_bronze_lee_las_cinco_tablas_y_anota_lineaje(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    crear_bronze(settings.data_dir)

    resultado = CapaBronce(settings).ejecutar()

    assert set(resultado) == set(COLUMNAS_ESPERADAS)
    assert all("_fila_origen" in df.columns for df in resultado.values())
    assert all(df["_fila_origen"].tolist() == [2] for df in resultado.values())


def test_bronze_falla_si_falta_una_columna_del_contrato(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    crear_bronze(settings.data_dir)
    ruta = settings.archivo_bronce("ventas")
    df = pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.drop(columns=["fecha_hora"]).to_csv(ruta, index=False, encoding="utf-8-sig")

    with pytest.raises(ErrorEsquema, match="fecha_hora"):
        CapaBronce(settings).ejecutar()


def test_bronce_no_modifica_los_archivos_de_origen(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    crear_bronze(settings.data_dir)
    ruta = settings.archivo_bronce("ventas")
    contenido_previo = ruta.read_bytes()

    CapaBronce(settings).ejecutar()

    assert ruta.read_bytes() == contenido_previo


def test_conjunto_base_del_caso_preserva_conteos_de_referencia() -> None:
    raiz = Path(__file__).resolve().parents[1]
    bronze_dir = raiz / "data" / "bronce"

    resultado = CapaBronce(Settings(data_dir=raiz / "data")).ejecutar()

    assert len(resultado["ventas"]) == 18_055
    assert len(resultado["tiendas"]) == 12
    assert len(resultado["productos"]) == 144
    assert len(resultado["clientes"]) == 1_800
    assert len(resultado["promociones"]) == 20
    assert not (bronze_dir / "lotes" / "lote-demo-productos-2026-10-25").exists()


def _inicializar_bronce_lotes(raiz: Path) -> tuple[Path, dict[str, pd.DataFrame]]:
    settings = Settings(data_dir=raiz / "data")
    crear_bronze(settings.data_dir)
    tablas = {}
    for tabla, columnas in COLUMNAS_ESPERADAS.items():
        datos = {columna: f"nuevo_{columna}" for columna in columnas}
        if tabla == "ventas":
            datos.update({
                "venta_id": "V002", "fecha_hora": "2025-02-01 10:00:00",
                "cantidad": "1", "precio_unitario": "5", "descuento_pct": "0",
                "costo_unitario": "2", "tipo_movimiento": "Venta",
            })
        elif tabla == "tiendas":
            datos.update({"tienda_id": "T002"})
        elif tabla == "productos":
            datos.update({"producto_id": "P002"})
        elif tabla == "clientes":
            datos.update({"cliente_id": "C002"})
        elif tabla == "promociones":
            datos.update({"promocion_id": "PR002"})
        tablas[tabla] = pd.DataFrame([datos])

    lote = settings.bronze_dir / "lotes" / "lote-2026-10"
    lote.mkdir(parents=True)
    for tabla, df in tablas.items():
        df.to_csv(lote / f"{tabla}.csv", index=False, encoding="utf-8-sig")
    return settings.bronze_dir, tablas


def test_bronce_acumula_lotes_nuevos_para_las_cinco_tablas(tmp_path: Path) -> None:
    _inicializar_bronce_lotes(tmp_path)
    lotes = {
        tabla: pd.read_csv(
            tmp_path / "data" / "bronce" / "lotes" / "lote-2026-10" / f"{tabla}.csv",
            dtype=str,
            encoding="utf-8-sig",
        )
        for tabla in COLUMNAS_ESPERADAS
    }

    resultado = CapaBronce(Settings(data_dir=tmp_path / "data")).ejecutar()

    for tabla in COLUMNAS_ESPERADAS:
        assert len(resultado[tabla]) == 2
        llave = {
            "ventas": "venta_id", "tiendas": "tienda_id", "productos": "producto_id",
            "clientes": "cliente_id", "promociones": "promocion_id",
        }[tabla]
        assert resultado[tabla][llave].iloc[-1] == lotes[tabla][llave].iloc[0]
        assert resultado[tabla]["_fila_origen"].tolist() == [2, 3]


def test_plata_recibe_ids_repetidos_en_lotes_para_aplicar_reglas_existentes(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    crear_bronze(settings.data_dir)
    originales = pd.DataFrame([{
        "cliente_id": "C001", "segmento": "Premium", "ciudad_residencia": "Quito",
        "anio_nacimiento": "1990", "fecha_alta": "2021-01-01", "canal_preferido": "Web",
    }])
    originales.to_csv(settings.archivo_bronce("clientes"), index=False, encoding="utf-8-sig")
    duplicado = originales.iloc[[0]].copy()
    lote = settings.bronze_dir / "lotes" / "lote-duplicado"
    lote.mkdir(parents=True)
    duplicado.to_csv(lote / "clientes.csv", index=False, encoding="utf-8-sig")

    resultado = CapaBronce(settings).ejecutar()

    assert len(resultado["clientes"]) == 2
    assert resultado["clientes"]["cliente_id"].duplicated().sum() == 1
    plata = LimpiadorClientes().limpiar(resultado["clientes"])
    assert plata.limpio["cliente_id"].tolist() == ["C001"]
    assert plata.rechazado["motivo_rechazo"].tolist() == ["llave_duplicada"]


def test_bronce_falla_si_el_csv_del_lote_no_corresponde_a_una_tabla(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    crear_bronze(settings.data_dir)
    lote = settings.bronze_dir / "lotes" / "lote-mal-formado"
    lote.mkdir(parents=True)
    (lote / "clientes_nuevos.csv").write_text("cliente_id\nC002\n", encoding="utf-8")

    with pytest.raises(ErrorEsquema, match="Use lotes"):
        CapaBronce(settings).ejecutar()


def test_stage_bronce_entrega_todas_las_tablas_acumuladas(tmp_path: Path) -> None:
    bronze, _ = _inicializar_bronce_lotes(tmp_path)
    bronze_artifact = tmp_path / "artifact-bronce"

    ejecutar_bronce(bronze, bronze_artifact)

    for tabla in COLUMNAS_ESPERADAS:
        acumulados = pd.read_csv(bronze_artifact / f"{tabla}.csv", dtype=str, encoding="utf-8-sig")
        assert len(acumulados) == 2
