"""Contratos de transporte para artefactos Bronce y Plata."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .config import COLUMNAS_ESPERADAS, TABLAS
from .integridad import CHECKSUMS_FILENAME, MANIFEST_FILENAME, ErrorIntegridadArtifact
from .plata import RegistroRegla, ResultadoLimpieza

PLATA_INDEX_FILENAME = "plata_index.json"
BRONCE_INDEX_FILENAME = "bronce_index.json"
ARCHIVOS_ESTANDAR = {"manifest.json", "checksums.sha256", PLATA_INDEX_FILENAME}


def copiar_bronce(origen: Path, destino: Path, tablas: tuple[str, ...] = TABLAS) -> dict[str, str]:
    destino.mkdir(parents=True, exist_ok=True)
    archivos: dict[str, str] = {}
    for tabla in tablas:
        fuente = origen / f"{tabla}.csv"
        if not fuente.is_file():
            raise FileNotFoundError(f"No existe el archivo Bronze requerido: {fuente}")
        shutil.copy2(fuente, destino / f"{tabla}.csv")
        archivos[f"{tabla}.csv"] = str(fuente)
    (destino / BRONCE_INDEX_FILENAME).write_text(
        json.dumps({"schema_version": 1, "tables": list(tablas)}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return archivos


def escribir_plata(destino: Path, resultados: Mapping[str, ResultadoLimpieza]) -> Path:
    destino.mkdir(parents=True, exist_ok=True)
    tablas: dict[str, dict[str, Any]] = {}
    for tabla in TABLAS:
        if tabla not in resultados:
            raise ErrorIntegridadArtifact(f"Falta resultado de limpieza para la tabla {tabla}")
        resultado = resultados[tabla]
        limpio = destino / f"{tabla}.csv"
        rechazado = destino / "rechazados" / f"{tabla}_rechazados.csv"
        limpio.parent.mkdir(parents=True, exist_ok=True)
        rechazado.parent.mkdir(parents=True, exist_ok=True)
        resultado.limpio.to_csv(limpio, index=False, encoding="utf-8-sig", date_format="%Y-%m-%dT%H:%M:%S", lineterminator="\n")
        resultado.rechazado.to_csv(rechazado, index=False, encoding="utf-8-sig", date_format="%Y-%m-%dT%H:%M:%S", lineterminator="\n")
        tablas[tabla] = {
            "limpio": limpio.relative_to(destino).as_posix(),
            "rechazados": rechazado.relative_to(destino).as_posix(),
            "tipos": _tipos_plata(tabla),
        }
    reglas = [asdict(regla) for resultado in resultados.values() for regla in resultado.reglas]
    reglas_path = destino / "reglas_limpieza.csv"
    pd.DataFrame(reglas, columns=["tabla", "regla", "filas_afectadas", "detalle"]).to_csv(
        reglas_path, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    indice = {
        "schema_version": 1,
        "tables": tablas,
        "reglas": reglas_path.relative_to(destino).as_posix(),
    }
    ruta_indice = destino / PLATA_INDEX_FILENAME
    ruta_indice.write_text(json.dumps(indice, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ruta_indice


def _tipos_plata(tabla: str) -> dict[str, str]:
    if tabla == "ventas":
        return {
            "fecha_hora": "datetime",
            "fecha": "date",
            "cantidad": "int64",
            "precio_unitario": "float64",
            "descuento_pct": "float64",
            "costo_unitario": "float64",
            "costo_imputado": "bool",
            "_fila_origen": "int64",
        }
    tipos = {"_fila_origen": "int64"}
    if tabla == "tiendas":
        tipos.update({"metros2": "float64", "fecha_apertura": "date"})
    elif tabla == "productos":
        tipos.update({"costo_base": "float64", "precio_lista": "float64"})
    elif tabla == "clientes":
        tipos.update({"anio_nacimiento": "Int64", "fecha_alta": "date"})
    elif tabla == "promociones":
        tipos.update({"descuento_objetivo": "float64", "fecha_inicio": "date", "fecha_fin": "date"})
    return tipos


def _convertir_tipos(
    df: pd.DataFrame,
    tipos: Mapping[str, str],
    normalizar_vacios: bool = False,
) -> pd.DataFrame:
    resultado = df.copy()
    if normalizar_vacios:
        for columna in resultado.select_dtypes(include=["object", "string"]).columns:
            resultado[columna] = resultado[columna].replace("", pd.NA)
    for columna, tipo in tipos.items():
        if columna not in resultado.columns:
            continue
        if tipo == "datetime":
            resultado[columna] = pd.to_datetime(resultado[columna], errors="coerce")
        elif tipo == "date":
            resultado[columna] = pd.to_datetime(resultado[columna], errors="coerce").dt.date
        elif tipo == "int64":
            resultado[columna] = pd.to_numeric(resultado[columna], errors="coerce").astype("int64")
        elif tipo == "Int64":
            resultado[columna] = pd.to_numeric(resultado[columna], errors="coerce").astype("Int64")
        elif tipo == "float64":
            resultado[columna] = pd.to_numeric(resultado[columna], errors="coerce").astype("float64")
        elif tipo == "bool":
            resultado[columna] = resultado[columna].astype("bool")
    return resultado


def leer_plata(origen: Path) -> dict[str, ResultadoLimpieza]:
    ruta_indice = origen / PLATA_INDEX_FILENAME
    if not ruta_indice.is_file():
        raise ErrorIntegridadArtifact(f"No existe el índice Plata: {ruta_indice}")
    indice = json.loads(ruta_indice.read_text(encoding="utf-8"))
    if indice.get("schema_version") != 1:
        raise ErrorIntegridadArtifact("schema_version de plata_index.json no soportado")
    tablas = indice.get("tables", {})
    faltantes = [tabla for tabla in TABLAS if tabla not in tablas]
    if faltantes:
        raise ErrorIntegridadArtifact(f"Faltan tablas en el artefacto Plata: {', '.join(faltantes)}")
    permitidos = set(ARCHIVOS_ESTANDAR)
    permitidos.add(indice.get("reglas", ""))
    for entrada in tablas.values():
        permitidos.update((entrada.get("limpio", ""), entrada.get("rechazados", "")))
    reales = {ruta.relative_to(origen).as_posix() for ruta in origen.rglob("*") if ruta.is_file()}
    inesperados = sorted(reales - permitidos)
    if inesperados:
        raise ErrorIntegridadArtifact(f"Archivos inesperados en Plata: {', '.join(inesperados)}")
    resultados: dict[str, ResultadoLimpieza] = {}
    for tabla in TABLAS:
        entrada = tablas[tabla]
        limpio_path = origen / entrada["limpio"]
        rechazado_path = origen / entrada["rechazados"]
        if not limpio_path.is_file() or not rechazado_path.is_file():
            raise ErrorIntegridadArtifact(f"Archivos faltantes de la tabla Plata {tabla}")
        limpio = pd.read_csv(limpio_path, encoding="utf-8-sig", keep_default_na=False)
        rechazado = pd.read_csv(rechazado_path, encoding="utf-8-sig", keep_default_na=False)
        tipos = entrada.get("tipos", {})
        limpio = _convertir_tipos(limpio, tipos, normalizar_vacios=True)
        rechazado = _convertir_tipos(rechazado, tipos)
        if "motivo_rechazo" in rechazado.columns:
            rechazado["motivo_rechazo"] = rechazado["motivo_rechazo"].astype("string")
        resultados[tabla] = ResultadoLimpieza(tabla, limpio, rechazado, [])
    reglas_path = origen / indice["reglas"]
    if not reglas_path.is_file():
        raise ErrorIntegridadArtifact(f"Falta el archivo de reglas: {reglas_path}")
    reglas_df = pd.read_csv(reglas_path, encoding="utf-8-sig", keep_default_na=False)
    reglas = [
        RegistroRegla(str(fila["tabla"]), str(fila["regla"]), int(fila["filas_afectadas"]), str(fila["detalle"]))
        for fila in reglas_df.to_dict("records")
    ]
    for tabla, resultado in resultados.items():
        resultado.reglas = [regla for regla in reglas if regla.tabla == tabla]
    return resultados


def leer_bronze(origen: Path) -> dict[str, pd.DataFrame]:
    permitidos = {f"{tabla}.csv" for tabla in TABLAS} | {
        MANIFEST_FILENAME, CHECKSUMS_FILENAME, BRONCE_INDEX_FILENAME, "stage.json"
    }
    reales = {ruta.relative_to(origen).as_posix() for ruta in origen.rglob("*") if ruta.is_file()}
    inesperados = sorted(reales - permitidos)
    if inesperados:
        raise ErrorIntegridadArtifact(f"Archivos inesperados en Bronce: {', '.join(inesperados)}")
    datos: dict[str, pd.DataFrame] = {}
    for tabla in TABLAS:
        ruta = origen / f"{tabla}.csv"
        if not ruta.is_file():
            raise ErrorIntegridadArtifact(f"Falta el archivo Bronze requerido: {ruta}")
        df = pd.read_csv(ruta, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        faltantes = [columna for columna in COLUMNAS_ESPERADAS[tabla] if columna not in df.columns]
        if faltantes:
            raise ErrorIntegridadArtifact(f"{tabla}: faltan columnas {faltantes}")
        df = df.assign(_fila_origen=range(2, len(df) + 2))
        datos[tabla] = df
    return datos
