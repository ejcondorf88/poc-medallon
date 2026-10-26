from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from andinos_etl.almacen import AlmacenCsv
from andinos_etl.calidad import ContextoCalidad, IntegridadTemporal
from andinos_etl.config import Settings
from andinos_etl.oro import CapaOro
from andinos_etl.plata import ResultadoLimpieza


def _ventas_plata() -> pd.DataFrame:
    return pd.DataFrame([{
        "venta_id": "V001",
        "fecha_hora": pd.Timestamp("2025-02-23 17:33:00"),
        "fecha": "2025-02-23",
        "tienda_id": "T001",
        "cliente_id": "C001",
        "producto_id": "P001",
        "cantidad": 2,
        "precio_unitario": 4.41,
        "descuento_pct": 0.0,
        "costo_unitario": 2.91,
        "promocion_id": None,
        "medio_pago": "Tarjeta",
        "tipo_movimiento": "Venta",
        "costo_imputado": 0,
    }])


def _dimensiones() -> dict[str, pd.DataFrame]:
    return {
        "dim_tienda": pd.DataFrame({"tienda_id": ["T001"], "tienda_key": [1]}),
        "dim_producto": pd.DataFrame({"producto_id": ["P001"], "producto_key": [1]}),
        "dim_cliente": pd.DataFrame({"cliente_id": ["C001"], "cliente_key": [1]}),
        "dim_promocion": pd.DataFrame({"promocion_id": ["SIN_PROMO"], "promocion_key": [0]}),
    }


def _plata_completa() -> dict[str, pd.DataFrame]:
    return {
        "ventas": _ventas_plata(),
        "tiendas": pd.DataFrame({"tienda_id": ["T001"], "_fila_origen": [2]}),
        "productos": pd.DataFrame({"producto_id": ["P001"], "_fila_origen": [2]}),
        "clientes": pd.DataFrame({"cliente_id": ["C001"], "anio_nacimiento": [1990], "_fila_origen": [2]}),
        "promociones": pd.DataFrame(columns=["promocion_id", "_fila_origen"]),
    }


def test_fact_ventas_conserva_fecha_hora_y_deriva_hora(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    oro = CapaOro(settings, AlmacenCsv())
    ventas = _ventas_plata()

    dimensiones = _dimensiones()
    hechos = oro._fact_ventas(
        ventas,
        dimensiones["dim_tienda"],
        dimensiones["dim_producto"],
        dimensiones["dim_cliente"],
        dimensiones["dim_promocion"],
    )

    assert hechos["fecha_hora"].dtype == "datetime64[us]"
    assert hechos["fecha_hora"].iloc[0] == pd.Timestamp("2025-02-23 17:33:00")
    assert hechos["hora"].iloc[0] == 17
    assert hechos["fecha_key"].iloc[0] == 20250223


def test_excel_guarda_fecha_hora_como_datetime(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, output_dir=tmp_path / "salida")
    oro = CapaOro(settings, AlmacenCsv())
    ventas = _ventas_plata()

    resultado = oro.ejecutar(_plata_completa())

    ruta = settings.gold_dir / "modelo_estrella.xlsx"
    libro = load_workbook(ruta, read_only=False)
    hoja = libro["fact_ventas"]
    celda = hoja["B2"]
    assert isinstance(celda.value, datetime)
    assert celda.number_format == "YYYY-MM-DD HH:MM:SS"
    assert hoja.column_dimensions["B"].width >= 19
    assert "fecha_hora" in resultado["fact_ventas"].columns


def test_csv_oro_usa_fecha_hora_iso_para_power_bi(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, output_dir=tmp_path / "salida")
    oro = CapaOro(settings, AlmacenCsv())

    oro.ejecutar(_plata_completa())

    contenido = (settings.gold_dir / "fact_ventas.csv").read_text(encoding="utf-8-sig")
    assert "2025-02-23T17:33:00" in contenido


def test_calendario_no_tiene_huecos(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", output_dir=tmp_path / "salida")
    oro = CapaOro(settings, AlmacenCsv())

    resultado = oro.ejecutar(_plata_completa())
    fechas = pd.to_datetime(resultado["dim_fecha"]["fecha"]).sort_values()

    assert len(fechas) == 365
    assert fechas.min() == pd.Timestamp("2025-01-01")
    assert fechas.max() == pd.Timestamp("2025-12-31")
    assert (fechas.diff().dropna() == pd.Timedelta(days=1)).all()


def test_control_temporal_aprueba_concordancia() -> None:
    hechos = pd.DataFrame({
        "venta_id": ["V001"],
        "fecha_hora": [pd.Timestamp("2025-02-23 17:33:00")],
        "hora": [17],
        "fecha_key": [20250223],
    })
    ctx = ContextoCalidad(
        bronce={"ventas": pd.DataFrame()},
        plata={"ventas": ResultadoLimpieza("ventas", _ventas_plata(), pd.DataFrame())},
        oro={"fact_ventas": hechos},
    )

    resultado = IntegridadTemporal().evaluar(ctx)

    assert resultado.estado == "OK"


def test_control_temporal_falla_si_hora_no_corresponde() -> None:
    hechos = pd.DataFrame({
        "venta_id": ["V001"],
        "fecha_hora": [pd.Timestamp("2025-02-23 17:33:00")],
        "hora": [8],
        "fecha_key": [20250223],
    })
    ctx = ContextoCalidad(
        bronce={"ventas": pd.DataFrame()},
        plata={"ventas": ResultadoLimpieza("ventas", _ventas_plata(), pd.DataFrame())},
        oro={"fact_ventas": hechos},
    )

    resultado = IntegridadTemporal().evaluar(ctx)

    assert resultado.estado == "FALLO"
    assert "hora incorrecta: 1" in resultado.obtenido
