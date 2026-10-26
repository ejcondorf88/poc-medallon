from pathlib import Path

import pandas as pd

from andinos_etl.config import Settings
from andinos_etl.plata import CapaPlata


def _ventas(cantidad: str = "1", costo: str = "2.00", cliente: str = "C001",
            promocion: str = "") -> pd.DataFrame:
    return pd.DataFrame([{
        "venta_id": "V001",
        "fecha_hora": "2026-01-15 10:00:00",
        "tienda_id": "T001",
        "cliente_id": cliente,
        "producto_id": "P001",
        "cantidad": cantidad,
        "precio_unitario": "5.00",
        "descuento_pct": "0.00",
        "costo_unitario": costo,
        "promocion_id": promocion,
        "medio_pago": "Efectivo",
        "tipo_movimiento": "Venta",
        "_fila_origen": 2,
    }])


def _bronce(ventas: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "tiendas": pd.DataFrame([{
            "tienda_id": "T001", "nombre_tienda": "Sucursal Central", "ciudad": "Quito",
            "provincia": "Pichincha", "formato": "Urbano", "fecha_apertura": "2020-01-01",
            "metros2": "1000", "_fila_origen": 2,
        }]),
        "productos": pd.DataFrame([{
            "producto_id": "P001", "sku": "SKU001", "producto": "Producto", "categoria": "A",
            "subcategoria": "B", "marca": "Marca", "proveedor": "Proveedor", "costo_base": "2.00",
            "precio_lista": "5.00", "_fila_origen": 2,
        }]),
        "clientes": pd.DataFrame([{
            "cliente_id": "C001", "segmento": "Premium", "ciudad_residencia": "Quito",
            "anio_nacimiento": "1990", "fecha_alta": "2021-01-01",
            "canal_preferido": "Web", "_fila_origen": 2,
        }]),
        "promociones": pd.DataFrame([{
            "promocion_id": "PR001", "nombre_promocion": "Campaña", "fecha_inicio": "2026-01-01",
            "fecha_fin": "2026-01-31", "tipo_promocion": "Porcentaje",
            "descuento_objetivo": "0.10", "categoria_objetivo": "A", "_fila_origen": 2,
        }]),
        "ventas": ventas,
    }


def _ejecutar(tmp_path: Path, ventas: pd.DataFrame):
    settings = Settings(data_dir=tmp_path / "data")
    return CapaPlata(settings).ejecutar(_bronce(ventas))


def test_rechaza_cantidad_fraccionaria(tmp_path: Path) -> None:
    resultado = _ejecutar(tmp_path, _ventas(cantidad="1.5"))["ventas"]

    assert len(resultado.limpio) == 0
    assert resultado.rechazado["motivo_rechazo"].tolist() == ["cantidad_no_entera"]
    assert resultado.rechazado["_fila_origen"].tolist() == [2]


def test_rechaza_costo_unitario_negativo(tmp_path: Path) -> None:
    resultado = _ejecutar(tmp_path, _ventas(costo="-1.00"))["ventas"]

    assert len(resultado.limpio) == 0
    assert resultado.rechazado["motivo_rechazo"].tolist() == ["costo_unitario_negativo"]


def test_rechaza_atributo_obligatorio_nulo_en_dimension(tmp_path: Path) -> None:
    bronce = _bronce(_ventas())
    bronce["productos"].loc[0, "categoria"] = ""
    settings = Settings(data_dir=tmp_path / "data")

    resultado = CapaPlata(settings).ejecutar(bronce)["productos"]

    assert len(resultado.limpio) == 0
    assert resultado.rechazado["motivo_rechazo"].tolist() == ["atributo_obligatorio_nulo"]


def test_rechaza_cliente_huerfano_y_lo_diferencia_de_desconocido(tmp_path: Path) -> None:
    resultado = _ejecutar(tmp_path, _ventas(cliente="CLIENTE_FANTASMA"))["ventas"]

    assert len(resultado.limpio) == 0
    assert resultado.rechazado["motivo_rechazo"].tolist() == ["cliente_id_huerfano"]
    assert resultado.rechazado["cliente_id"].tolist() == ["CLIENTE_FANTASMA"]


def test_rechaza_promocion_huerfana(tmp_path: Path) -> None:
    resultado = _ejecutar(tmp_path, _ventas(promocion="PROMO_FANTASMA"))["ventas"]

    assert len(resultado.limpio) == 0
    assert resultado.rechazado["motivo_rechazo"].tolist() == ["promocion_id_huerfano"]


def test_cliente_vacio_permanece_como_desconocido_valido(tmp_path: Path) -> None:
    resultado = _ejecutar(tmp_path, _ventas(cliente=""))["ventas"]

    assert len(resultado.limpio) == 1
    assert len(resultado.rechazado) == 0


def test_rechaza_los_55_duplicados_exactos(tmp_path: Path) -> None:
    original = _ventas()
    ventas = pd.concat([original] * 56, ignore_index=True)
    ventas["_fila_origen"] = range(2, 58)

    resultado = _ejecutar(tmp_path, ventas)["ventas"]

    assert len(resultado.limpio) == 1
    assert len(resultado.rechazado) == 55
    assert resultado.rechazado["motivo_rechazo"].eq("duplicado_exacto").all()
    assert resultado.rechazado["_fila_origen"].tolist() == list(range(3, 58))
