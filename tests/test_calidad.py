import pandas as pd
import pytest

from andinos_etl.calidad import (
    AusenciaCostosNegativos,
    ConciliacionCostoTotal,
    ConciliacionCrecimientoComparable,
    ConciliacionDescuento,
    ConciliacionDevoluciones,
    ConciliacionFilasDimensiones,
    ConciliacionMargen,
    ConciliacionMontos,
    ConciliacionPorcentajeDevoluciones,
    ConciliacionPorcentajePromocion,
    ConciliacionVentasPorMetroCuadrado,
    ContextoCalidad,
)
from andinos_etl.plata import ResultadoLimpieza


def _ventas() -> pd.DataFrame:
    return pd.DataFrame([
        {"venta_id": "V001", "fecha_hora": "2025-01-15 10:00:00", "tienda_id": "T001", "cliente_id": "C001", "producto_id": "P001", "cantidad": 2, "precio_unitario": 10.0, "descuento_pct": 0.1, "costo_unitario": 4.0, "promocion_id": "PR001", "medio_pago": "Tarjeta", "tipo_movimiento": "Venta", "_fila_origen": 2},
        {"venta_id": "V002", "fecha_hora": "2025-02-15 11:00:00", "tienda_id": "T001", "cliente_id": "C001", "producto_id": "P001", "cantidad": -1, "precio_unitario": 10.0, "descuento_pct": 0.0, "costo_unitario": 4.0, "promocion_id": "", "medio_pago": "Efectivo", "tipo_movimiento": "Devolución", "_fila_origen": 3},
        {"venta_id": "V003", "fecha_hora": "2026-01-15 12:00:00", "tienda_id": "T001", "cliente_id": "C001", "producto_id": "P001", "cantidad": 2, "precio_unitario": 12.0, "descuento_pct": 0.0, "costo_unitario": 5.0, "promocion_id": "", "medio_pago": "Tarjeta", "tipo_movimiento": "Venta", "_fila_origen": 4},
        {"venta_id": "V004", "fecha_hora": "2026-02-15 13:00:00", "tienda_id": "T001", "cliente_id": "C001", "producto_id": "P001", "cantidad": 3, "precio_unitario": 12.0, "descuento_pct": 0.0, "costo_unitario": 5.0, "promocion_id": "", "medio_pago": "Tarjeta", "tipo_movimiento": "Venta", "_fila_origen": 5},
    ])


def _hechos() -> pd.DataFrame:
    filas = []
    for _, v in _ventas().iterrows():
        cantidad = float(v["cantidad"])
        precio = float(v["precio_unitario"])
        descuento = float(v["descuento_pct"])
        costo = float(v["costo_unitario"])
        bruto = cantidad * precio
        descuento_importe = bruto * descuento
        neto = bruto - descuento_importe
        costo_total = cantidad * costo
        filas.append({
            "venta_id": v["venta_id"],
            "fecha_hora": pd.Timestamp(v["fecha_hora"]),
            "hora": pd.Timestamp(v["fecha_hora"]).hour,
            "fecha_key": int(pd.Timestamp(v["fecha_hora"]).strftime("%Y%m%d")),
            "tienda_key": 1,
            "cliente_key": 1,
            "producto_key": 1,
            "promocion_key": 1 if v["promocion_id"] else 0,
            "cantidad": cantidad,
            "precio_unitario": precio,
            "descuento_pct": descuento,
            "costo_unitario": costo,
            "importe_bruto": bruto,
            "importe_descuento": descuento_importe,
            "importe_neto": neto,
            "costo_total": costo_total,
            "margen": neto - costo_total,
            "es_devolucion": int(v["tipo_movimiento"] == "Devolución"),
            "tiene_promocion": int(bool(v["promocion_id"])),
            "costo_imputado": 0,
            "medio_pago": v["medio_pago"],
            "tipo_movimiento": v["tipo_movimiento"],
        })
    return pd.DataFrame(filas)


def _resultado(tabla: str, filas: pd.DataFrame) -> ResultadoLimpieza:
    return ResultadoLimpieza(tabla, filas.copy(), pd.DataFrame())


def _contexto() -> ContextoCalidad:
    ventas = _ventas()
    fechas = pd.date_range("2025-01-01", "2026-12-31", freq="D")
    return ContextoCalidad(
        bronce={
            "ventas": ventas,
            "tiendas": pd.DataFrame({"tienda_id": ["T001"]}),
            "productos": pd.DataFrame({"producto_id": ["P001"], "costo_base": [4.0]}),
            "clientes": pd.DataFrame({"cliente_id": ["C001"]}),
            "promociones": pd.DataFrame({"promocion_id": ["PR001"]}),
        },
        plata={
            "ventas": _resultado("ventas", ventas),
            "tiendas": _resultado("tiendas", pd.DataFrame({"tienda_id": ["T001"]})),
            "productos": _resultado("productos", pd.DataFrame({"producto_id": ["P001"], "costo_base": [4.0]})),
            "clientes": _resultado("clientes", pd.DataFrame({"cliente_id": ["C001"]})),
            "promociones": _resultado("promociones", pd.DataFrame({"promocion_id": ["PR001"]})),
        },
        oro={
            "fact_ventas": _hechos(),
            "dim_tienda": pd.DataFrame({"tienda_id": ["T001"], "tienda_key": [1], "metros2": [100]}),
            "dim_producto": pd.DataFrame({"producto_id": ["P001"], "producto_key": [1]}),
            "dim_cliente": pd.DataFrame({"cliente_id": ["C001", "SIN_CLIENTE"], "cliente_key": [1, 0]}),
            "dim_promocion": pd.DataFrame({"promocion_id": ["PR001", "SIN_PROMO"], "promocion_key": [1, 0]}),
            "dim_fecha": pd.DataFrame({"fecha_key": fechas.strftime("%Y%m%d").astype(int), "fecha": fechas.date}),
        },
    )


CONTROLES_FASE_2 = [
    ConciliacionCostoTotal(),
    ConciliacionMargen(),
    ConciliacionDescuento(),
    ConciliacionDevoluciones(),
    ConciliacionPorcentajeDevoluciones(),
    ConciliacionPorcentajePromocion(),
    ConciliacionCrecimientoComparable(),
    ConciliacionVentasPorMetroCuadrado(),
    ConciliacionFilasDimensiones(),
    AusenciaCostosNegativos(),
]


def test_controles_fase_2_aprueban_contexto_consistente() -> None:
    ctx = _contexto()

    resultados = [control.evaluar(ctx) for control in CONTROLES_FASE_2]

    assert [resultado.control for resultado in resultados] == [control.nombre for control in CONTROLES_FASE_2]
    assert all(resultado.estado == "OK" for resultado in resultados), [resultado.obtenido for resultado in resultados]


def test_conciliacion_de_ventas_netas_coincide() -> None:
    ctx = _contexto()

    resultado = ConciliacionMontos().evaluar(ctx)

    assert resultado.estado == "OK"


@pytest.mark.parametrize("control,mutacion", [
    (ConciliacionCostoTotal(), lambda ctx: ctx.oro["fact_ventas"].__setitem__("costo_total", ctx.oro["fact_ventas"]["costo_total"] + 1)),
    (ConciliacionMargen(), lambda ctx: ctx.oro["fact_ventas"].__setitem__("margen", ctx.oro["fact_ventas"]["margen"] + 1)),
    (ConciliacionDescuento(), lambda ctx: ctx.oro["fact_ventas"].__setitem__("importe_descuento", ctx.oro["fact_ventas"]["importe_descuento"] + 1)),
    (ConciliacionDevoluciones(), lambda ctx: ctx.oro["fact_ventas"].__setitem__("es_devolucion", 1 - ctx.oro["fact_ventas"]["es_devolucion"])),
    (ConciliacionPorcentajeDevoluciones(), lambda ctx: ctx.oro["fact_ventas"].__setitem__("es_devolucion", 1 - ctx.oro["fact_ventas"]["es_devolucion"])),
    (ConciliacionPorcentajePromocion(), lambda ctx: ctx.oro["fact_ventas"].__setitem__("tiene_promocion", 1 - ctx.oro["fact_ventas"]["tiene_promocion"])),
    (ConciliacionCrecimientoComparable(), lambda ctx: ctx.oro["fact_ventas"].__setitem__("importe_neto", ctx.oro["fact_ventas"]["importe_neto"] + 100)),
    (ConciliacionVentasPorMetroCuadrado(), lambda ctx: ctx.oro["fact_ventas"].__setitem__("importe_neto", ctx.oro["fact_ventas"]["importe_neto"] + 100)),
    (ConciliacionFilasDimensiones(), lambda ctx: ctx.oro.__setitem__("dim_cliente", ctx.oro["dim_cliente"].iloc[:-1])),
    (AusenciaCostosNegativos(), lambda ctx: ctx.oro["fact_ventas"].__setitem__("costo_unitario", -1)),
])
def test_controles_fase_2_detectan_desviaciones(control, mutacion) -> None:
    ctx = _contexto()
    mutacion(ctx)

    resultado = control.evaluar(ctx)

    assert resultado.estado == "FALLO"
