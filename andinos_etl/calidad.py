"""CONTROLES DE CALIDAD: conciliaciones automáticas que prueban que los números son confiables."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from .base import CapaMedallon
from .config import COLUMNAS_ESPERADAS, UMBRAL_COSTO_IMPUTADO


@dataclass
class ContextoCalidad:
    """Todo lo que un control puede necesitar mirar (se arma una vez y se comparte)."""
    bronce: dict[str, pd.DataFrame]
    plata: dict          # dict[str, ResultadoLimpieza]
    oro: dict[str, pd.DataFrame]


@dataclass
class ResultadoControl:
    control: str
    descripcion: str
    esperado: str
    obtenido: str
    estado: str          # OK, ADVERTENCIA o FALLO


class Control(ABC):
    """Un control = una pregunta con respuesta verificable."""
    nombre: str
    descripcion: str

    @abstractmethod
    def evaluar(self, ctx: ContextoCalidad) -> ResultadoControl: ...

    def _resultado(self, esperado, obtenido, ok: bool, estado_malo: str = "FALLO") -> ResultadoControl:
        return ResultadoControl(self.nombre, self.descripcion, str(esperado), str(obtenido),
                                "OK" if ok else estado_malo)


class ConciliacionFilas(Control):
    nombre = "C1 Conciliación de filas"
    descripcion = "Filas de bronce = filas de plata + rechazadas, y plata = hechos de oro"

    def evaluar(self, ctx):
        bronce = len(ctx.bronce["ventas"])
        plata = len(ctx.plata["ventas"].limpio)
        rechazadas = len(ctx.plata["ventas"].rechazado)
        oro = len(ctx.oro["fact_ventas"])
        ok = bronce == plata + rechazadas and plata == oro
        return self._resultado(f"{bronce} = {plata} + {rechazadas} = {oro} + rechazadas",
                               f"bronce {bronce} | plata {plata} | rechazadas {rechazadas} | oro {oro}", ok)


class ConciliacionMontos(Control):
    nombre = "C2 Conciliación de montos"
    descripcion = "Ventas netas del modelo = ventas netas recalculadas de forma independiente desde la bronce"

    def evaluar(self, ctx):
        # Recalculo independiente: solo desde el texto original, sin reutilizar nada de plata ni oro.
        b = ctx.bronce["ventas"].drop_duplicates(subset=COLUMNAS_ESPERADAS["ventas"])
        otras = ctx.plata["ventas"].rechazado
        if not otras.empty and "motivo_rechazo" in otras.columns:
            otras = otras[otras["motivo_rechazo"] != "duplicado_exacto"]["_fila_origen"]
        else:
            otras = pd.Series(dtype="object")
        b = b[~b["_fila_origen"].isin(otras)]
        num = lambda c: pd.to_numeric(b[c], errors="coerce")
        esperado = float((num("cantidad") * num("precio_unitario") * (1 - num("descuento_pct"))).sum())
        obtenido = float(ctx.oro["fact_ventas"]["importe_neto"].sum())
        return self._resultado(f"{esperado:,.2f}", f"{obtenido:,.2f}", abs(esperado - obtenido) < 0.01)


class IntegridadReferencial(Control):
    nombre = "C3 Integridad referencial"
    descripcion = "Toda llave del hecho existe en su dimensión (0 llaves huérfanas)"

    def evaluar(self, ctx):
        f = ctx.oro["fact_ventas"]
        relaciones = {"tienda_key": "dim_tienda", "producto_key": "dim_producto", "cliente_key": "dim_cliente",
                      "promocion_key": "dim_promocion", "fecha_key": "dim_fecha"}
        huerfanas = {llave: int((~f[llave].isin(ctx.oro[dim][llave])).sum()) for llave, dim in relaciones.items()}
        return self._resultado("0 huérfanas en las 5 relaciones", huerfanas, sum(huerfanas.values()) == 0)


class ConciliacionReferenciasHuerfanas(Control):
    nombre = "C8 Conciliación de referencias huérfanas"
    descripcion = "La clave 0 representa solo IDs vacíos y todo ID huérfano queda rechazado con linaje"

    def evaluar(self, ctx):
        bronce = ctx.bronce["ventas"]
        rechazados = ctx.plata["ventas"].rechazado
        validas = ctx.plata["ventas"].limpio
        hechos = ctx.oro["fact_ventas"]

        clientes = set(ctx.plata["clientes"].limpio["cliente_id"])
        promociones = set(ctx.plata["promociones"].limpio["promocion_id"])

        def presente(serie: pd.Series) -> pd.Series:
            return serie.notna() & serie.astype("string").str.strip().ne("")

        cliente_huerfano = presente(bronce["cliente_id"]) & ~bronce["cliente_id"].isin(clientes)
        promocion_huerfana = presente(bronce["promocion_id"]) & ~bronce["promocion_id"].isin(promociones)
        filas_huerfanas = bronce[cliente_huerfano | promocion_huerfana]

        motivos_huerfano = {"cliente_id_huerfano", "promocion_id_huerfano"}
        if "motivo_rechazo" in rechazados.columns:
            rechazos_huerfano = rechazados[rechazados["motivo_rechazo"].isin(motivos_huerfano)]
        else:
            rechazos_huerfano = pd.DataFrame()
        detectados = set(filas_huerfanas["_fila_origen"].dropna())
        if "_fila_origen" in rechazos_huerfano.columns:
            rechazados_ids = set(rechazos_huerfano["_fila_origen"].dropna())
            sin_origen = int(rechazos_huerfano["_fila_origen"].isna().sum())
        else:
            rechazados_ids = set()
            sin_origen = 0
        sin_rechazo = detectados - rechazados_ids
        rechazos_no_detectados = rechazados_ids - detectados

        motive_incorrecto = 0
        for row in rechazos_huerfano.itertuples(index=False):
            if row.motivo_rechazo == "cliente_id_huerfano" and not presente(pd.Series([row.cliente_id])).iloc[0]:
                motive_incorrecto += 1
            if row.motivo_rechazo == "promocion_id_huerfano" and not presente(pd.Series([row.promocion_id])).iloc[0]:
                motive_incorrecto += 1

        comparacion = validas[["venta_id", "cliente_id", "promocion_id"]].merge(
            hechos[["venta_id", "cliente_key", "promocion_key"]],
            on="venta_id",
            how="left",
            validate="one_to_one",
        )
        clientes_oro = ctx.oro["dim_cliente"][["cliente_id", "cliente_key"]]
        promociones_oro = ctx.oro["dim_promocion"][["promocion_id", "promocion_key"]]
        esperado_cliente = comparacion[["venta_id", "cliente_id"]].merge(
            clientes_oro, on="cliente_id", how="left", validate="many_to_one"
        )
        esperado_promocion = comparacion[["venta_id", "promocion_id"]].merge(
            promociones_oro, on="promocion_id", how="left", validate="many_to_one"
        )
        claves_cliente = comparacion.set_index("venta_id")["cliente_key"]
        claves_promocion = comparacion.set_index("venta_id")["promocion_key"]
        esperados_cliente = esperado_cliente.set_index("venta_id")["cliente_key"].fillna(0).astype(int)
        esperados_promocion = esperado_promocion.set_index("venta_id")["promocion_key"].fillna(0).astype(int)
        clientes_incorrectos = int((claves_cliente != esperados_cliente).sum())
        promociones_incorrectas = int((claves_promocion != esperados_promocion).sum())

        obtained = (
            f"huérfanos detectados: {len(detectados)} | rechazados: {len(rechazados_ids)} | "
            f"sin rechazo: {len(sin_rechazo)} | sin linaje: {sin_origen} | "
            f"claves de cliente incorrectas: {clientes_incorrectos} | "
            f"claves de promoción incorrectas: {promociones_incorrectas}"
        )
        ok = (
            not sin_rechazo
            and not rechazos_no_detectados
            and sin_origen == 0
            and motive_incorrecto == 0
            and clientes_incorrectos == 0
            and promociones_incorrectas == 0
        )
        return self._resultado("0 huérfanos sin rechazo y 0 claves desconocidas incorrectas", obtained, ok)


class IntegridadTemporal(Control):
    nombre = "C9 Integridad temporal"
    descripcion = "Fecha y hora del hecho coinciden con plata, hora y fecha_key"

    def evaluar(self, ctx):
        f = ctx.oro["fact_ventas"].copy()
        f["fecha_hora"] = pd.to_datetime(f["fecha_hora"], errors="coerce")
        plata = ctx.plata["ventas"].limpio[["venta_id", "fecha_hora"]].copy()
        plata["fecha_hora"] = pd.to_datetime(plata["fecha_hora"], errors="coerce")
        comparacion = f[["venta_id", "fecha_hora"]].merge(
            plata, on="venta_id", how="left", validate="one_to_one", suffixes=("_oro", "_plata")
        )

        nulos = int(comparacion["fecha_hora_oro"].isna().sum())
        desalineados = int((comparacion["fecha_hora_oro"] != comparacion["fecha_hora_plata"]).sum())
        hora_incorrecta = int((f["hora"] != f["fecha_hora"].dt.hour).sum())
        fecha_key_incorrecta = int((f["fecha_key"] != f["fecha_hora"].dt.strftime("%Y%m%d").astype(float)).sum())

        obtained = (
            f"nulos: {nulos} | desalineados con plata: {desalineados} | "
            f"hora incorrecta: {hora_incorrecta} | fecha_key incorrecta: {fecha_key_incorrecta}"
        )
        ok = nulos == 0 and desalineados == 0 and hora_incorrecta == 0 and fecha_key_incorrecta == 0
        return self._resultado("0 nulos, 0 desalineados y 0 claves temporales incorrectas", obtained, ok)


class UnicidadLlaves(Control):
    nombre = "C4 Unicidad de llaves"
    descripcion = "Las llaves primarias de dimensiones y hechos no se repiten (relaciones 1:*)"

    def evaluar(self, ctx):
        llaves = {"dim_tienda": "tienda_key", "dim_producto": "producto_key", "dim_cliente": "cliente_key",
                  "dim_promocion": "promocion_key", "dim_fecha": "fecha_key", "fact_ventas": "venta_id"}
        repetidas = {t: int(ctx.oro[t][k].duplicated().sum()) for t, k in llaves.items()}
        return self._resultado("0 repetidas en las 6 tablas", repetidas, sum(repetidas.values()) == 0)


class CoherenciaDevoluciones(Control):
    nombre = "C5 Coherencia de devoluciones"
    descripcion = "Toda devolución tiene cantidad negativa y toda venta cantidad positiva"

    def evaluar(self, ctx):
        f = ctx.oro["fact_ventas"]
        malas = int(((f["es_devolucion"] == 1) & (f["cantidad"] >= 0)).sum()
                    + ((f["es_devolucion"] == 0) & (f["cantidad"] <= 0)).sum())
        return self._resultado("0 incoherentes", malas, malas == 0)


class PromocionesVigentes(Control):
    nombre = "C6 Promociones vigentes"
    descripcion = "Las ventas con promoción ocurren dentro de las fechas y la categoría de esa campaña"

    def evaluar(self, ctx):
        f, dp, dprod = ctx.oro["fact_ventas"], ctx.oro["dim_promocion"], ctx.oro["dim_producto"]
        x = (f[f["tiene_promocion"] == 1]
             .merge(dp, on="promocion_key").merge(dprod[["producto_key", "categoria"]], on="producto_key"))
        # Usamos fecha_key (AAAAMMDD) en lugar de fecha_hora (ya eliminada del hecho)
        dia = pd.to_datetime(x["fecha_key"].astype(str), format="%Y%m%d").dt.date
        fuera = int(((dia < x["fecha_inicio"]) | (dia > x["fecha_fin"])).sum())
        otra_cat = int((x["categoria"] != x["categoria_objetivo"]).sum())
        return self._resultado("0 fuera de fechas y 0 de otra categoría",
                               f"{fuera} fuera de fechas, {otra_cat} de otra categoría",
                               fuera == 0 and otra_cat == 0, estado_malo="ADVERTENCIA")


class CostoImputado(Control):
    nombre = "C7 Costos imputados"
    descripcion = "Parte de las líneas cuyo costo vacío se completó con el costo base del producto"

    def evaluar(self, ctx):
        f = ctx.oro["fact_ventas"]
        n, pct = int(f["costo_imputado"].sum()), f["costo_imputado"].mean()
        return self._resultado(f"<= {UMBRAL_COSTO_IMPUTADO:.0%}", f"{n} líneas ({pct:.2%})",
                               pct <= UMBRAL_COSTO_IMPUTADO, estado_malo="ADVERTENCIA")


def _ventas_plata(ctx: ContextoCalidad) -> pd.DataFrame:
    return ctx.plata["ventas"].limpio.copy()


def _importes_plata(ventas: pd.DataFrame) -> pd.DataFrame:
    v = ventas.copy()
    for columna in ("cantidad", "precio_unitario", "descuento_pct", "costo_unitario"):
        v[columna] = pd.to_numeric(v[columna], errors="coerce")
    v["importe_bruto"] = v["cantidad"] * v["precio_unitario"]
    v["importe_descuento"] = v["importe_bruto"] * v["descuento_pct"]
    v["importe_neto"] = v["importe_bruto"] - v["importe_descuento"]
    v["costo_total"] = v["cantidad"] * v["costo_unitario"]
    return v


def _ventas_bronze_para_indicadores(ctx: ContextoCalidad) -> pd.DataFrame:
    b = ctx.bronce["ventas"].drop_duplicates(subset=COLUMNAS_ESPERADAS["ventas"]).copy()
    rechazados = ctx.plata["ventas"].rechazado
    if not rechazados.empty and "motivo_rechazo" in rechazados.columns:
        otras = rechazados[rechazados["motivo_rechazo"] != "duplicado_exacto"]["_fila_origen"]
        b = b[~b["_fila_origen"].isin(otras)]
    for columna in ("cantidad", "precio_unitario", "descuento_pct"):
        b[columna] = pd.to_numeric(b[columna], errors="coerce")
    b["importe_neto"] = b["cantidad"] * b["precio_unitario"] * (1 - b["descuento_pct"])
    b["fecha_hora"] = pd.to_datetime(b["fecha_hora"], errors="coerce")
    return b


def _periodo_comparable(fechas: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    ultima = pd.to_datetime(fechas).max()
    if pd.isna(ultima):
        raise ValueError("No hay fechas para construir el período comparable")
    actual_inicio = pd.Timestamp(year=ultima.year, month=1, day=1)
    actual_fin = pd.Timestamp(year=ultima.year, month=ultima.month, day=ultima.day) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    anterior_inicio = pd.Timestamp(year=ultima.year - 1, month=1, day=1)
    anterior_fin = pd.Timestamp(year=ultima.year - 1, month=ultima.month, day=ultima.day) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return actual_inicio, actual_fin, anterior_inicio, anterior_fin


class ConciliacionCostoTotal(Control):
    nombre = "C10 Conciliación de costo total"
    descripcion = "Costo total de Oro coincide con cantidad por costo unitario de Plata"

    def evaluar(self, ctx):
        v = _importes_plata(_ventas_plata(ctx))
        esperado = float(v["costo_total"].sum())
        obtenido = float(ctx.oro["fact_ventas"]["costo_total"].sum())
        diferencia = abs(esperado - obtenido)
        return self._resultado(f"{esperado:,.2f}", f"{obtenido:,.2f} (diferencia {diferencia:,.4f})", diferencia < 0.01)


class ConciliacionMargen(Control):
    nombre = "C11 Conciliación de margen"
    descripcion = "Margen de Oro coincide con venta neta menos costo total de Plata"

    def evaluar(self, ctx):
        v = _importes_plata(_ventas_plata(ctx))
        esperado = float((v["importe_neto"] - v["costo_total"]).sum())
        obtenido = float(ctx.oro["fact_ventas"]["margen"].sum())
        diferencia = abs(esperado - obtenido)
        return self._resultado(f"{esperado:,.2f}", f"{obtenido:,.2f} (diferencia {diferencia:,.4f})", diferencia < 0.01)


class ConciliacionDescuento(Control):
    nombre = "C12 Conciliación de descuento otorgado"
    descripcion = "Descuento de Oro coincide con importe bruto por porcentaje de Plata"

    def evaluar(self, ctx):
        v = _importes_plata(_ventas_plata(ctx))
        esperado = float(v["importe_descuento"].sum())
        obtenido = float(ctx.oro["fact_ventas"]["importe_descuento"].sum())
        diferencia = abs(esperado - obtenido)
        return self._resultado(f"{esperado:,.2f}", f"{obtenido:,.2f} (diferencia {diferencia:,.4f})", diferencia < 0.01)


class ConciliacionDevoluciones(Control):
    nombre = "C13 Conciliación de devoluciones"
    descripcion = "Número y valor absoluto de devoluciones de Oro coinciden con Plata"

    def evaluar(self, ctx):
        v = _importes_plata(_ventas_plata(ctx))
        f = ctx.oro["fact_ventas"]
        esperado_numero = int((v["tipo_movimiento"] == "Devolución").sum())
        esperado_valor = float(abs(v.loc[v["tipo_movimiento"] == "Devolución", "importe_neto"].sum()))
        obtenido_numero = int((f["es_devolucion"] == 1).sum())
        obtenido_valor = float(abs(f.loc[f["es_devolucion"] == 1, "importe_neto"].sum()))
        diferencia = abs(esperado_valor - obtenido_valor)
        ok = esperado_numero == obtenido_numero and diferencia < 0.01
        return self._resultado(
            f"{esperado_numero} líneas por {esperado_valor:,.2f}",
            f"{obtenido_numero} líneas por {obtenido_valor:,.2f}",
            ok,
        )


class ConciliacionPorcentajeDevoluciones(Control):
    nombre = "C14 Porcentaje de devoluciones"
    descripcion = "Porcentaje de líneas devueltas de Oro coincide con Plata"

    def evaluar(self, ctx):
        v = _ventas_plata(ctx)
        f = ctx.oro["fact_ventas"]
        esperado = float((v["tipo_movimiento"] == "Devolución").mean())
        obtenido = float((f["es_devolucion"] == 1).mean())
        diferencia = abs(esperado - obtenido)
        return self._resultado(f"{esperado:.4%}", f"{obtenido:.4%} (diferencia {diferencia:.8%})", diferencia < 1e-12)


class ConciliacionPorcentajePromocion(Control):
    nombre = "C15 Porcentaje de promoción"
    descripcion = "Porcentaje de líneas con promoción de Oro coincide con identificadores de Plata"

    def evaluar(self, ctx):
        v = _ventas_plata(ctx)
        f = ctx.oro["fact_ventas"]
        promocion_plata = v["promocion_id"].notna() & v["promocion_id"].astype("string").str.strip().ne("")
        esperado = float(promocion_plata.mean())
        obtenido = float((f["tiene_promocion"] == 1).mean())
        diferencia = abs(esperado - obtenido)
        return self._resultado(f"{esperado:.4%}", f"{obtenido:.4%} (diferencia {diferencia:.8%})", diferencia < 1e-12)


class ConciliacionCrecimientoComparable(Control):
    nombre = "C16 Crecimiento comparable"
    descripcion = "Crecimiento YTD de Oro coincide con el mismo período del año anterior calculado desde Bronce"

    def evaluar(self, ctx):
        b = _ventas_bronze_para_indicadores(ctx)
        f = ctx.oro["fact_ventas"].copy()
        f["fecha_hora"] = pd.to_datetime(f["fecha_hora"], errors="coerce")
        actual_inicio, actual_fin, anterior_inicio, anterior_fin = _periodo_comparable(b["fecha_hora"])
        actual_b = b[(b["fecha_hora"] >= actual_inicio) & (b["fecha_hora"] <= actual_fin)]["importe_neto"].sum()
        anterior_b = b[(b["fecha_hora"] >= anterior_inicio) & (b["fecha_hora"] <= anterior_fin)]["importe_neto"].sum()
        actual_f = f[(f["fecha_hora"] >= actual_inicio) & (f["fecha_hora"] <= actual_fin)]["importe_neto"].sum()
        anterior_f = f[(f["fecha_hora"] >= anterior_inicio) & (f["fecha_hora"] <= anterior_fin)]["importe_neto"].sum()
        esperado = float((actual_b / anterior_b) - 1) if anterior_b else float("nan")
        obtenido = float((actual_f / anterior_f) - 1) if anterior_f else float("nan")
        diferencia = abs(esperado - obtenido)
        periodo = f"{actual_inicio:%Y-%m-%d} a {actual_fin:%Y-%m-%d} vs {anterior_inicio:%Y-%m-%d} a {anterior_fin:%Y-%m-%d}"
        return self._resultado(f"{esperado:.4%} ({periodo})", f"{obtenido:.4%} (diferencia {diferencia:.8%})", diferencia < 1e-10)


class ConciliacionVentasPorMetroCuadrado(Control):
    nombre = "C17 Conciliación de ventas por m²"
    descripcion = "Ventas netas por m² de Oro coinciden con Plata y superficie de tienda"

    def evaluar(self, ctx):
        v = _importes_plata(_ventas_plata(ctx))
        f = ctx.oro["fact_ventas"]
        dtienda = ctx.oro["dim_tienda"]
        esperado_tienda = v.groupby("tienda_id")["importe_neto"].sum().rename("importe_neto")
        obtenido_tienda = f.groupby("tienda_key")["importe_neto"].sum().rename("importe_neto")
        comparacion = dtienda[["tienda_id", "tienda_key", "metros2"]].merge(
            esperado_tienda, on="tienda_id", how="left", validate="one_to_one"
        ).merge(obtenido_tienda, on="tienda_key", how="left", validate="one_to_one")
        comparacion["esperado_m2"] = comparacion["importe_neto_x"].fillna(0) / comparacion["metros2"]
        comparacion["obtenido_m2"] = comparacion["importe_neto_y"] / comparacion["metros2"]
        diferencia_maxima = float((comparacion["esperado_m2"] - comparacion["obtenido_m2"]).abs().max())
        esperado = float(comparacion["importe_neto_x"].sum() / comparacion["metros2"].sum())
        obtenido = float(comparacion["importe_neto_y"].sum() / comparacion["metros2"].sum())
        return self._resultado(
            f"{esperado:,.4f} por m² (diferencia máxima por tienda {diferencia_maxima:,.8f})",
            f"{obtenido:,.4f} por m²",
            abs(esperado - obtenido) < 0.01 and diferencia_maxima < 0.01,
        )


class ConciliacionFilasDimensiones(Control):
    nombre = "C18 Conciliación de filas de dimensiones"
    descripcion = "Bronce = Plata + rechazos y Oro conserva las filas válidas más miembros desconocidos"

    def evaluar(self, ctx):
        tablas = ("tiendas", "productos", "clientes", "promociones")
        detalles = {}
        ok = True
        dimensiones = {
            "tiendas": "dim_tienda",
            "productos": "dim_producto",
            "clientes": "dim_cliente",
            "promociones": "dim_promocion",
        }
        for tabla in tablas:
            bronce = len(ctx.bronce[tabla])
            plata = len(ctx.plata[tabla].limpio)
            rechazadas = len(ctx.plata[tabla].rechazado)
            oro = len(ctx.oro[dimensiones[tabla]])
            miembros_desconocidos = 1 if tabla in ("clientes", "promociones") else 0
            detalle_ok = bronce == plata + rechazadas and oro == plata + miembros_desconocidos
            detalles[tabla] = {
                "bronce": bronce,
                "plata": plata,
                "rechazadas": rechazadas,
                "oro": oro,
                "diferencia": bronce - plata - rechazadas,
            }
            ok = ok and detalle_ok
        fechas = pd.to_datetime(ctx.oro["dim_fecha"]["fecha"])
        fechas_esperadas = pd.date_range(f"{fechas.min().year}-01-01", f"{fechas.max().year}-12-31", freq="D")
        continuidad = bool((fechas.sort_values().diff().dropna() == pd.Timedelta(days=1)).all())
        calendario_ok = len(fechas) == len(fechas_esperadas) and continuidad
        detalles["dim_fecha"] = {"filas": len(fechas), "continuo": calendario_ok}
        return self._resultado("0 diferencias en todas las dimensiones y calendario continuo", detalles, ok and calendario_ok)


class AusenciaCostosNegativos(Control):
    nombre = "C19 Ausencia de costos negativos"
    descripcion = "Costos unitarios, base y totales de ventas no contienen valores negativos"

    def evaluar(self, ctx):
        ventas_plata = _importes_plata(_ventas_plata(ctx))
        productos_plata = ctx.plata["productos"].limpio.copy()
        productos_plata["costo_base"] = pd.to_numeric(productos_plata["costo_base"], errors="coerce")
        f = ctx.oro["fact_ventas"]
        ventas_no_devueltas = ventas_plata["tipo_movimiento"] != "Devolución"
        hechos_no_devueltos = f["tipo_movimiento"] != "Devolución"
        conteos = {
            "costo_unitario_plata": int((ventas_plata["costo_unitario"] < 0).sum()),
            "costo_total_venta_plata": int((ventas_plata.loc[ventas_no_devueltas, "costo_total"] < 0).sum()),
            "costo_base_producto": int((productos_plata["costo_base"] < 0).sum()),
            "costo_unitario_oro": int((f["costo_unitario"] < 0).sum()),
            "costo_total_venta_oro": int((f.loc[hechos_no_devueltos, "costo_total"] < 0).sum()),
        }
        return self._resultado("0 costos negativos", conteos, sum(conteos.values()) == 0)


class CapaCalidad(CapaMedallon):
    """Ejecuta todos los controles, guarda el reporte y dice si se puede publicar."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Para agregar un control nuevo basta con crear la clase y añadirla a esta lista.
        self.controles: list[Control] = [
            ConciliacionFilas(), ConciliacionMontos(), IntegridadReferencial(), UnicidadLlaves(),
            CoherenciaDevoluciones(), PromocionesVigentes(), CostoImputado(),
            ConciliacionReferenciasHuerfanas(), IntegridadTemporal(), ConciliacionCostoTotal(),
            ConciliacionMargen(), ConciliacionDescuento(), ConciliacionDevoluciones(),
            ConciliacionPorcentajeDevoluciones(), ConciliacionPorcentajePromocion(),
            ConciliacionCrecimientoComparable(), ConciliacionVentasPorMetroCuadrado(),
            ConciliacionFilasDimensiones(), AusenciaCostosNegativos(),
        ]

    def ejecutar(self, ctx: ContextoCalidad) -> pd.DataFrame:
        print(
            "[CALIDAD] Ejecuta conciliaciones entre capas y verificaciones de integridad "
            "antes de publicar; los montos se recalculan desde Bronce cuando es posible. "
            "Busca detectar pérdidas, duplicaciones, cálculos divergentes y referencias inválidas.",
            flush=True,
        )
        resultados = []
        for control in self.controles:
            resultado = control.evaluar(ctx)
            resultados.append(resultado)
            print(
                f"[CALIDAD] {resultado.control} | {resultado.estado} | "
                f"qué protege: {resultado.descripcion} | "
                f"esperado: {resultado.esperado} | obtenido: {resultado.obtenido}",
                flush=True,
            )
        reporte = pd.DataFrame([vars(r) for r in resultados])
        self.almacen.escribir(reporte, self.settings.quality_dir / "reporte_calidad.csv")
        for r in resultados:
            self.log.info("Calidad | %-30s | %-11s | %s", r.control, r.estado, r.obtenido)
        fallos = sum(r.estado == "FALLO" for r in resultados)
        advertencias = sum(r.estado == "ADVERTENCIA" for r in resultados)
        print(
            f"[CALIDAD] Resumen: {len(resultados) - fallos - advertencias} OK, "
            f"{advertencias} advertencia(s), {fallos} fallo(s). "
            "Las advertencias quedan documentadas; cualquier FALLO bloquea la publicación.",
            flush=True,
        )
        return reporte
