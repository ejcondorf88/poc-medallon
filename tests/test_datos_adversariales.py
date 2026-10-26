import pandas as pd

from andinos_etl.calidad import ContextoCalidad, ConciliacionReferenciasHuerfanas
from andinos_etl.plata import LimpiadorVentas, ResultadoLimpieza


def _resultado_tabla(limpio: pd.DataFrame, rechazado: pd.DataFrame | None = None) -> ResultadoLimpieza:
    return ResultadoLimpieza(
        tabla="ventas",
        limpio=limpio,
        rechazado=rechazado if rechazado is not None else pd.DataFrame(),
    )


def _contexto(bronze: pd.DataFrame, validas: pd.DataFrame, rechazados: pd.DataFrame,
              hechos: pd.DataFrame) -> ContextoCalidad:
    return ContextoCalidad(
        bronce={"ventas": bronze},
        plata={
            "ventas": _resultado_tabla(validas, rechazados),
            "clientes": _resultado_tabla(pd.DataFrame({"cliente_id": ["C001"]})),
            "promociones": _resultado_tabla(pd.DataFrame({"promocion_id": ["P001"]})),
        },
        oro={
            "fact_ventas": hechos,
            "dim_cliente": pd.DataFrame({"cliente_id": ["C001"], "cliente_key": [1]}),
            "dim_promocion": pd.DataFrame({"promocion_id": ["P001"], "promocion_key": [1]}),
        },
    )


def _bronze(cliente: str, promo: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "cliente_id": cliente,
        "promocion_id": promo,
        "_fila_origen": 2,
    }])


def _hechos(cliente_key: int, promo_key: int) -> pd.DataFrame:
    return pd.DataFrame([{
        "venta_id": "V001",
        "cliente_key": cliente_key,
        "promocion_key": promo_key,
    }])


def test_control_aprueba_desconocido_legitimo() -> None:
    ctx = _contexto(
        _bronze("", ""),
        pd.DataFrame({"venta_id": ["V001"], "cliente_id": [None], "promocion_id": [None]}),
        pd.DataFrame(),
        _hechos(0, 0),
    )

    resultado = ConciliacionReferenciasHuerfanas().evaluar(ctx)

    assert resultado.estado == "OK"


def test_control_aprueba_huerfano_rechazado_con_linaje() -> None:
    rechazados = pd.DataFrame([{
        "cliente_id": "FANTASMA",
        "promocion_id": None,
        "_fila_origen": 2,
        "motivo_rechazo": "cliente_id_huerfano",
    }])
    ctx = _contexto(
        _bronze("FANTASMA", ""),
        pd.DataFrame(columns=["venta_id", "cliente_id", "promocion_id"]),
        rechazados,
        _hechos(0, 0).iloc[0:0],
    )

    resultado = ConciliacionReferenciasHuerfanas().evaluar(ctx)

    assert resultado.estado == "OK"
    assert "huérfanos detectados: 1 | rechazados: 1" in resultado.obtenido


def test_control_falla_si_huerfano_no_fue_rechazado() -> None:
    ctx = _contexto(
        _bronze("FANTASMA", ""),
        pd.DataFrame(columns=["venta_id", "cliente_id", "promocion_id"]),
        pd.DataFrame(),
        _hechos(0, 0).iloc[0:0],
    )

    resultado = ConciliacionReferenciasHuerfanas().evaluar(ctx)

    assert resultado.estado == "FALLO"
    assert "sin rechazo: 1" in resultado.obtenido


def test_control_falla_si_huerfano_pierde_su_fila_de_origen() -> None:
    rechazados = pd.DataFrame([{
        "cliente_id": "FANTASMA",
        "promocion_id": None,
        "_fila_origen": None,
        "motivo_rechazo": "cliente_id_huerfano",
    }])
    ctx = _contexto(
        _bronze("FANTASMA", ""),
        pd.DataFrame(columns=["venta_id", "cliente_id", "promocion_id"]),
        rechazados,
        _hechos(0, 0).iloc[0:0],
    )

    resultado = ConciliacionReferenciasHuerfanas().evaluar(ctx)

    assert resultado.estado == "FALLO"
    assert "sin linaje: 1" in resultado.obtenido


def test_control_falla_si_id_conocido_usa_clave_cero() -> None:
    ctx = _contexto(
        _bronze("C001", ""),
        pd.DataFrame({"venta_id": ["V001"], "cliente_id": ["C001"], "promocion_id": [None]}),
        pd.DataFrame(),
        _hechos(0, 0),
    )

    resultado = ConciliacionReferenciasHuerfanas().evaluar(ctx)

    assert resultado.estado == "FALLO"
    assert "claves de cliente incorrectas: 1" in resultado.obtenido


def _venta_adversarial(**cambios) -> pd.DataFrame:
    fila = {
        "venta_id": "V001",
        "fecha_hora": "2026-01-15 10:00:00",
        "tienda_id": "T001",
        "cliente_id": "C001",
        "producto_id": "P001",
        "cantidad": "1",
        "precio_unitario": "5.00",
        "descuento_pct": "0.00",
        "costo_unitario": "2.00",
        "promocion_id": "PR001",
        "medio_pago": "Tarjeta",
        "tipo_movimiento": "Venta",
        "_fila_origen": "2",
    }
    fila.update({clave: str(valor) for clave, valor in cambios.items()})
    return pd.DataFrame([fila])


def _limpiar_ventas(ventas: pd.DataFrame):
    return LimpiadorVentas(
        pd.Series({"P001": 2.0}), {"C001"}, {"PR001"}
    ).limpiar(ventas)


def test_rechaza_fecha_hora_invalida() -> None:
    resultado = _limpiar_ventas(_venta_adversarial(fecha_hora="2026-99-99 10:00:00"))

    assert resultado.rechazado["motivo_rechazo"].tolist() == ["fecha_hora_invalida"]


def test_rechaza_tipo_movimiento_desconocido() -> None:
    resultado = _limpiar_ventas(_venta_adversarial(tipo_movimiento="Ajuste"))

    assert resultado.rechazado["motivo_rechazo"].tolist() == ["tipo_movimiento_invalido"]


def test_rechaza_signo_incoherente() -> None:
    resultado = _limpiar_ventas(_venta_adversarial(cantidad=-1, tipo_movimiento="Venta"))

    assert resultado.rechazado["motivo_rechazo"].tolist() == ["signo_cantidad_incoherente"]


def test_rechaza_descuento_fuera_de_rango() -> None:
    resultado = _limpiar_ventas(_venta_adversarial(descuento_pct=1.5))

    assert resultado.rechazado["motivo_rechazo"].tolist() == ["precio_o_descuento_fuera_de_rango"]


def test_rechaza_venta_id_duplicado_con_contenido_conflictivo() -> None:
    ventas = pd.concat([
        _venta_adversarial(),
        _venta_adversarial(cliente_id="", precio_unitario="6.00"),
    ], ignore_index=True)

    resultado = _limpiar_ventas(ventas)

    assert resultado.rechazado["motivo_rechazo"].tolist() == ["venta_id_duplicado_conflicto"]
