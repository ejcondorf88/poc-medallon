"""CAPA PLATA: datos limpios, tipados y validados. Lo que no pasa las reglas se aparta, no se borra."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from .base import CapaMedallon
from .config import (ATRIBUTOS_OBLIGATORIOS, COLUMNAS_ESPERADAS, LLAVES_NATURALES,
                     MEDIO_PAGO_CANONICO, MEDIO_PAGO_DESCONOCIDO, TIPOS_MOVIMIENTO)

EXPLICACIONES_REGLA = {
    "normalizar_texto": "unifica espacios y vacíos para que las validaciones de nulos sean consistentes",
    "rechazar:llave_nula": "impide publicar dimensiones sin identificador natural",
    "rechazar:atributo_obligatorio_nulo": "evita dimensiones incompletas que no se pueden analizar correctamente",
    "rechazar:numero_invalido": "evita cálculos con valores numéricos que no se pudieron interpretar",
    "rechazar:entero_invalido": "protege campos que requieren números enteros",
    "rechazar:fecha_invalida": "evita fechas que no se pueden ordenar ni relacionar con el calendario",
    "rechazar:llave_duplicada": "garantiza una sola fila por identificador de dimensión",
    "rechazar:metros2_no_positivo": "evita superficies inválidas y divisiones sin sentido en ventas por m²",
    "rechazar:precio_o_costo_no_positivo": "evita precios o costos de producto que distorsionen márgenes",
    "rechazar:descuento_fuera_de_rango": "mantiene el descuento de promoción como una fracción entre 0 y 1",
    "rechazar:fechas_invertidas": "asegura que una promoción no termine antes de comenzar",
    "rechazar:duplicado_exacto": "evita contar dos veces una misma fila repetida en la fuente",
    "rechazar:campo_obligatorio_nulo": "protege los campos necesarios para identificar y calcular una línea de venta",
    "rechazar:cliente_id_huerfano": "impide asignar una venta a un cliente informado que no existe",
    "rechazar:promocion_id_huerfano": "impide asignar una venta a una promoción informada que no existe",
    "rechazar:fecha_hora_invalida": "garantiza que cada venta pueda ubicarse en el tiempo",
    "rechazar:cantidad_invalida": "evita cantidades no interpretables",
    "rechazar:cantidad_no_entera": "mantiene unidades vendidas como unidades enteras",
    "rechazar:costo_unitario_negativo": "evita costos negativos que falsearían el margen",
    "unificar_medio_pago": "agrupa distintas escrituras del POS bajo categorías comparables",
    "rechazar:tipo_movimiento_invalido": "limita los movimientos a Venta o Devolución",
    "rechazar:signo_cantidad_incoherente": "mantiene ventas positivas y devoluciones negativas",
    "rechazar:precio_o_descuento_fuera_de_rango": "evita precios no positivos y descuentos fuera del rango 0–1",
    "imputar_costo_con_costo_base": "completa costos ausentes con referencia de producto y marca la imputación",
    "rechazar:sin_costo_disponible": "impide publicar líneas sin costo real ni costo de referencia",
    "rechazar:venta_id_duplicado_conflicto": "garantiza un ID único por línea y aparta duplicados no idénticos",
}


# ----------------------------------------------------------------------------
# Estructuras de resultado
# ----------------------------------------------------------------------------
@dataclass
class RegistroRegla:
    """Evidencia de una regla de limpieza: qué se hizo y a cuántas filas afectó."""
    tabla: str
    regla: str
    filas_afectadas: int
    detalle: str = ""


@dataclass
class ResultadoLimpieza:
    """Salida de limpiar una tabla: filas válidas, filas rechazadas y bitácora de reglas."""
    tabla: str
    limpio: pd.DataFrame
    rechazado: pd.DataFrame
    reglas: list[RegistroRegla] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Limpiadores (uno por tabla, todos con la misma estructura)
# ----------------------------------------------------------------------------
class LimpiadorTabla(ABC):
    """Plantilla de limpieza. El método `limpiar` fija el orden; las subclases aportan las reglas."""

    tabla: str

    def __init__(self) -> None:
        self._reglas: list[RegistroRegla] = []
        self._rechazados: list[pd.DataFrame] = []

    # ---- método plantilla ---------------------------------------------------
    def limpiar(self, bronce: pd.DataFrame) -> ResultadoLimpieza:
        self._reglas, self._rechazados = [], []              # estado limpio en cada llamada

        # Paso 1: quedarse solo con las columnas del contrato (más la fila de origen).
        df = bronce[COLUMNAS_ESPERADAS[self.tabla] + ["_fila_origen"]].copy()

        # Paso 2: normalizar el texto (quitar espacios y convertir vacíos en nulos reales).
        df = self._normalizar_texto(df)

        # Paso 3: reglas propias de cada tabla.
        df = self._transformar(df)

        rechazado = (pd.concat(self._rechazados, ignore_index=True)
                     if self._rechazados else df.iloc[0:0].assign(motivo_rechazo=pd.Series(dtype="object")))
        return ResultadoLimpieza(self.tabla, df.reset_index(drop=True), rechazado, self._reglas)

    @abstractmethod
    def _transformar(self, df: pd.DataFrame) -> pd.DataFrame:
        """Reglas específicas de la tabla (tipos, validaciones, derivadas)."""

    # ---- utilidades compartidas --------------------------------------------
    def _registrar(self, regla: str, filas: int, detalle: str = "") -> None:
        """Deja constancia de una regla aplicada (aunque haya afectado 0 filas)."""
        self._reglas.append(RegistroRegla(self.tabla, regla, int(filas), detalle))

    def _normalizar_texto(self, df: pd.DataFrame) -> pd.DataFrame:
        """Recorta espacios y transforma '' en nulo, para que `isna()` funcione de verdad."""
        filas_modificadas = pd.Series(False, index=df.index)
        for col in df.columns:
            if col == "_fila_origen":
                continue
            original = df[col]
            normalizado = original.str.strip().replace("", pd.NA)
            cambio = (
                original.fillna("").astype(str).ne(normalizado.fillna("").astype(str))
                | original.isna().ne(normalizado.isna())
            )
            filas_modificadas |= cambio
            df[col] = normalizado
        self._registrar(
            "normalizar_texto",
            int(filas_modificadas.sum()),
            "filas con espacios recortados o cadenas vacías convertidas a nulo",
        )
        return df

    def _rechazar(self, df: pd.DataFrame, mascara: pd.Series, motivo: str) -> pd.DataFrame:
        """Aparta las filas que cumplen `mascara` con su motivo y devuelve el resto."""
        mascara = mascara.fillna(False).astype(bool)
        if mascara.any():
            self._rechazados.append(df[mascara].assign(motivo_rechazo=motivo))
        self._registrar(f"rechazar:{motivo}", int(mascara.sum()))
        return df[~mascara].copy()

    def _castear(self, df: pd.DataFrame, columnas: list[str],
                 funcion: Callable[[pd.Series], pd.Series], motivo: str) -> pd.DataFrame:
        """Convierte columnas de tipo; si una conversión falla, la fila se rechaza (no se inventa dato)."""
        invalida = pd.Series(False, index=df.index)
        for col in columnas:
            tenia_valor = df[col].notna()
            df[col] = funcion(df[col])
            invalida |= tenia_valor & df[col].isna()           # tenía texto y no se pudo convertir
        return self._rechazar(df, invalida, motivo)

    @staticmethod
    def _a_numero(serie: pd.Series) -> pd.Series:
        return pd.to_numeric(serie, errors="coerce")

    @staticmethod
    def _a_fecha(serie: pd.Series) -> pd.Series:
        return pd.to_datetime(serie, format="%Y-%m-%d", errors="coerce").dt.date

    @staticmethod
    def _a_fecha_hora(serie: pd.Series) -> pd.Series:
        return pd.to_datetime(serie, format="%Y-%m-%d %H:%M:%S", errors="coerce")


class LimpiadorDimension(LimpiadorTabla):
    """Reglas comunes a las dimensiones: llave obligatoria y única, tipos y rangos."""

    columnas_numericas: list[str] = []
    columnas_enteras: list[str] = []
    columnas_fecha: list[str] = []

    def _transformar(self, df: pd.DataFrame) -> pd.DataFrame:
        llave = LLAVES_NATURALES[self.tabla]

        # Regla 1: la llave natural no puede ser nula.
        df = self._rechazar(df, df[llave].isna(), "llave_nula")

        # Regla 1b: los atributos requeridos por el modelo dimensional deben existir.
        atributos = ATRIBUTOS_OBLIGATORIOS[self.tabla]
        faltantes = df[atributos].isna().any(axis=1)
        df = self._rechazar(df, faltantes, "atributo_obligatorio_nulo")

        # Regla 2: conversión de tipos (número, entero, fecha).
        df = self._castear(df, self.columnas_numericas, self._a_numero, "numero_invalido")
        df = self._castear(df, self.columnas_enteras, self._a_numero, "entero_invalido")
        for col in self.columnas_enteras:
            df[col] = df[col].astype("Int64")
        df = self._castear(df, self.columnas_fecha, self._a_fecha, "fecha_invalida")

        # Regla 3: reglas de rango propias de cada dimensión.
        df = self._validar_negocio(df)

        # Regla 4: la llave debe ser única (se conserva la primera aparición).
        df = self._rechazar(df, df.duplicated(subset=[llave], keep="first"), "llave_duplicada")
        return df

    def _validar_negocio(self, df: pd.DataFrame) -> pd.DataFrame:
        """Gancho para validaciones específicas; por defecto no hace nada."""
        return df


class LimpiadorTiendas(LimpiadorDimension):
    tabla = "tiendas"
    columnas_numericas = ["metros2"]
    columnas_fecha = ["fecha_apertura"]

    def _validar_negocio(self, df: pd.DataFrame) -> pd.DataFrame:
        # Una tienda debe tener superficie positiva.
        return self._rechazar(df, df["metros2"] <= 0, "metros2_no_positivo")


class LimpiadorProductos(LimpiadorDimension):
    tabla = "productos"
    columnas_numericas = ["costo_base", "precio_lista"]

    def _validar_negocio(self, df: pd.DataFrame) -> pd.DataFrame:
        # Costos y precios deben ser positivos.
        return self._rechazar(df, (df["costo_base"] <= 0) | (df["precio_lista"] <= 0), "precio_o_costo_no_positivo")


class LimpiadorClientes(LimpiadorDimension):
    tabla = "clientes"
    columnas_enteras = ["anio_nacimiento"]
    columnas_fecha = ["fecha_alta"]


class LimpiadorPromociones(LimpiadorDimension):
    tabla = "promociones"
    columnas_numericas = ["descuento_objetivo"]
    columnas_fecha = ["fecha_inicio", "fecha_fin"]

    def _validar_negocio(self, df: pd.DataFrame) -> pd.DataFrame:
        # El descuento es una fracción entre 0 y 1, y la campaña no puede terminar antes de empezar.
        df = self._rechazar(df, ~df["descuento_objetivo"].between(0, 1), "descuento_fuera_de_rango")
        return self._rechazar(df, df["fecha_fin"] < df["fecha_inicio"], "fechas_invertidas")


class LimpiadorVentas(LimpiadorTabla):
    """Tabla de hechos: es donde viven las reglas más importantes del negocio."""

    tabla = "ventas"

    def __init__(self, costo_base: pd.Series, clientes_validos: set[str],
                 promociones_validas: set[str]) -> None:
        super().__init__()
        # Costo de referencia por producto, usado para completar costos vacíos.
        self._costo_base = costo_base
        self._clientes_validos = clientes_validos
        self._promociones_validas = promociones_validas

    def _transformar(self, df: pd.DataFrame) -> pd.DataFrame:
        columnas = COLUMNAS_ESPERADAS["ventas"]

        # Regla 1: eliminar filas duplicadas exactas (el origen trae 55 repetidas al final).
        df = self._rechazar(df, df.duplicated(subset=columnas, keep="first"), "duplicado_exacto")

        # Regla 1b: los campos que definen la transacción no pueden venir vacíos.
        obligatorios = ["venta_id", "fecha_hora", "tienda_id", "producto_id", "cantidad",
                        "precio_unitario", "descuento_pct", "tipo_movimiento"]
        df = self._rechazar(df, df[obligatorios].isna().any(axis=1), "campo_obligatorio_nulo")

        # Regla 1c: un ID informado debe existir; el vacío sí representa desconocido.
        cliente_huerfano = df["cliente_id"].notna() & ~df["cliente_id"].isin(self._clientes_validos)
        df = self._rechazar(df, cliente_huerfano, "cliente_id_huerfano")
        promocion_huerfana = df["promocion_id"].notna() & ~df["promocion_id"].isin(self._promociones_validas)
        df = self._rechazar(df, promocion_huerfana, "promocion_id_huerfano")

        # Regla 2: conversión de tipos; si no se puede convertir, la fila se aparta.
        df = self._castear(df, ["fecha_hora"], self._a_fecha_hora, "fecha_hora_invalida")
        df = self._castear(df, ["cantidad"], self._a_numero, "cantidad_invalida")
        fraccion = df["cantidad"].notna() & (df["cantidad"] % 1 != 0)
        df = self._rechazar(df, fraccion, "cantidad_no_entera")
        df["cantidad"] = df["cantidad"].astype("int64")
        df = self._castear(df, ["precio_unitario", "descuento_pct", "costo_unitario"],
                           self._a_numero, "numero_invalido")
        costo_negativo = df["costo_unitario"].notna() & (df["costo_unitario"] < 0)
        df = self._rechazar(df, costo_negativo, "costo_unitario_negativo")

        # Regla 3: unificar el medio de pago (Tarjeta/TARJETA/TC -> Tarjeta, etc.).
        original = df["medio_pago"].copy()
        df["medio_pago"] = (df["medio_pago"].str.lower().map(MEDIO_PAGO_CANONICO)
                            .fillna(MEDIO_PAGO_DESCONOCIDO))
        self._registrar("unificar_medio_pago", int((original != df["medio_pago"]).sum()),
                        "variantes de escritura llevadas a un valor canónico")

        # Regla 4: el tipo de movimiento solo puede ser Venta o Devolución.
        df = self._rechazar(df, ~df["tipo_movimiento"].isin(TIPOS_MOVIMIENTO), "tipo_movimiento_invalido")

        # Regla 5: coherencia de signos (venta positiva, devolución negativa).
        es_devolucion = df["tipo_movimiento"] == "Devolución"
        incoherente = (es_devolucion & (df["cantidad"] >= 0)) | (~es_devolucion & (df["cantidad"] <= 0))
        df = self._rechazar(df, incoherente, "signo_cantidad_incoherente")

        # Regla 6: rangos válidos de precio y descuento.
        df = self._rechazar(df, (df["precio_unitario"] <= 0) | ~df["descuento_pct"].between(0, 1),
                            "precio_o_descuento_fuera_de_rango")

        # Regla 7: completar costos vacíos con el costo base del producto y marcarlos.
        df["costo_imputado"] = df["costo_unitario"].isna()
        if df["costo_imputado"].any():
            df.loc[df["costo_imputado"], "costo_unitario"] = df.loc[
                df["costo_imputado"], "producto_id"
            ].map(self._costo_base)
        self._registrar("imputar_costo_con_costo_base", int(df["costo_imputado"].sum()),
                        "costo_unitario vacío reemplazado por productos.costo_base (marcado en costo_imputado)")
        df = self._rechazar(df, df["costo_unitario"].isna(), "sin_costo_disponible")

        # Regla 8: campo derivado de solo fecha, necesario para relacionar con el calendario.
        df["fecha"] = df["fecha_hora"].dt.date

        # Regla 9: tras limpiar, cada línea debe tener un venta_id único.
        df = self._rechazar(df, df.duplicated(subset=["venta_id"], keep="first"), "venta_id_duplicado_conflicto")
        return df


# ----------------------------------------------------------------------------
# Capa
# ----------------------------------------------------------------------------
class CapaPlata(CapaMedallon):
    """Orquesta la limpieza de todas las tablas y guarda limpio, rechazado y bitácora."""

    def ejecutar(self, bronce: dict[str, pd.DataFrame]):
        print(
            "[PLATA] Limpia cada tabla sin borrar evidencia: normaliza texto, convierte tipos, "
            "aplica reglas de negocio y separa rechazados con motivo y linaje.",
            flush=True,
        )
        print(
            "[PLATA] Primero valida dimensiones; después usa sus catálogos para verificar "
            "referencias de ventas e imputar costos faltantes desde productos.",
            flush=True,
        )
        resultados: dict[str, ResultadoLimpieza] = {}

        # Paso 1: primero las dimensiones (la tabla de ventas necesita el costo base de productos).
        for limpiador in (LimpiadorTiendas(), LimpiadorProductos(), LimpiadorClientes(), LimpiadorPromociones()):
            resultados[limpiador.tabla] = limpiador.limpiar(bronce[limpiador.tabla])

        # Paso 2: luego los hechos, entregándole los catálogos y el costo base por producto.
        costo_base = resultados["productos"].limpio.set_index("producto_id")["costo_base"]
        clientes_validos = set(resultados["clientes"].limpio["cliente_id"])
        promociones_validas = set(resultados["promociones"].limpio["promocion_id"])
        resultados["ventas"] = LimpiadorVentas(
            costo_base, clientes_validos, promociones_validas
        ).limpiar(bronce["ventas"])

        # Paso 3: persistir cada resultado y la bitácora de reglas.
        bitacora: list[RegistroRegla] = []
        for tabla, res in resultados.items():
            self.almacen.escribir(res.limpio, self.settings.silver_dir / f"{tabla}.csv")
            self.almacen.escribir(res.rechazado, self.settings.rejected_dir / f"{tabla}_rechazados.csv")
            bitacora.extend(res.reglas)
            print(
                f"[PLATA] {tabla}: {len(res.limpio):,} válidas; "
                f"{len(res.rechazado):,} rechazadas. Detalle de reglas:",
                flush=True,
            )
            for regla in res.reglas:
                motivo = EXPLICACIONES_REGLA.get(regla.regla, "regla aplicada para proteger la calidad del dato")
                detalle = f" | {regla.detalle}" if regla.detalle else ""
                print(
                    f"[PLATA]   {regla.regla}: {regla.filas_afectadas:,} fila(s) afectada(s) "
                    f"— {motivo}{detalle}",
                    flush=True,
                )
            self.log.info("Plata  | %-11s | %6d válidas | %4d rechazadas", tabla, len(res.limpio), len(res.rechazado))

        reglas = pd.DataFrame([vars(r) for r in bitacora])
        self.almacen.escribir(reglas, self.settings.quality_dir / "reglas_limpieza.csv")
        return resultados
