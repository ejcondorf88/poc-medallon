"""Acceso a disco. Separar la lectura/escritura de la lógica facilita cambiar de CSV a Parquet."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


class AlmacenCsv:
    """Lee y escribe CSV con una convención única para todo el proyecto."""

    ENCODING_LECTURA = "utf-8-sig"   # quita el BOM si el archivo lo trae
    ENCODING_ESCRITURA = "utf-8-sig"  # agrega el BOM para que Excel lo abra bien

    def leer(self, ruta: Path) -> pd.DataFrame:
        """Lee un CSV completo como texto, sin interpretar nulos ni tipos (fidelidad al origen)."""
        if not ruta.exists():
            raise FileNotFoundError(f"No se encontró el archivo de origen: {ruta}")
        return pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding=self.ENCODING_LECTURA)

    def escribir(self, df: pd.DataFrame, ruta: Path) -> None:
        """Escribe un CSV creando las carpetas necesarias. Fechas en formato ISO."""
        ruta.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(ruta, index=False, encoding=self.ENCODING_ESCRITURA,
                  date_format="%Y-%m-%dT%H:%M:%S", lineterminator="\n")
