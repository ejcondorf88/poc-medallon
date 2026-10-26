"""Configuración central: rutas, contrato de datos y reglas de negocio.

Todo lo que pueda cambiar (nombres de columnas, catálogos, rutas) vive aquí,
para no dejar "números mágicos" ni textos repetidos dentro de la lógica.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Tablas de origen que el pipeline espera encontrar en la carpeta raw.
TABLAS: tuple[str, ...] = ("ventas", "tiendas", "productos", "clientes", "promociones")

# Contrato de datos: columnas exactas que debe traer cada archivo (según el diccionario).
COLUMNAS_ESPERADAS: dict[str, list[str]] = {
    "ventas": ["venta_id", "fecha_hora", "tienda_id", "cliente_id", "producto_id",
               "cantidad", "precio_unitario", "descuento_pct", "costo_unitario",
               "promocion_id", "medio_pago", "tipo_movimiento"],
    "tiendas": ["tienda_id", "nombre_tienda", "ciudad", "provincia", "formato",
                "fecha_apertura", "metros2"],
    "productos": ["producto_id", "sku", "producto", "categoria", "subcategoria",
                  "marca", "proveedor", "costo_base", "precio_lista"],
    "clientes": ["cliente_id", "segmento", "ciudad_residencia", "anio_nacimiento",
                 "fecha_alta", "canal_preferido"],
    "promociones": ["promocion_id", "nombre_promocion", "fecha_inicio", "fecha_fin",
                    "tipo_promocion", "descuento_objetivo", "categoria_objetivo"],
}

# Llave natural (identificador del sistema de origen) de cada tabla.
LLAVES_NATURALES: dict[str, str] = {
    "ventas": "venta_id", "tiendas": "tienda_id", "productos": "producto_id",
    "clientes": "cliente_id", "promociones": "promocion_id",
}

# Atributos que no pueden faltar en cada dimensión publicada.
ATRIBUTOS_OBLIGATORIOS: dict[str, list[str]] = {
    "tiendas": ["nombre_tienda", "ciudad", "provincia", "formato", "fecha_apertura", "metros2"],
    "productos": ["sku", "producto", "categoria", "subcategoria", "marca", "proveedor",
                  "costo_base", "precio_lista"],
    "clientes": ["segmento", "ciudad_residencia", "anio_nacimiento", "fecha_alta",
                 "canal_preferido"],
    "promociones": ["nombre_promocion", "fecha_inicio", "fecha_fin", "tipo_promocion",
                    "descuento_objetivo", "categoria_objetivo"],
}

# Reglas de negocio ---------------------------------------------------------
TIPOS_MOVIMIENTO: tuple[str, str] = ("Venta", "Devolución")

# Unifica las distintas formas en que el POS escribe el medio de pago.
# La clave va en minúsculas para comparar sin importar mayúsculas.
MEDIO_PAGO_CANONICO: dict[str, str] = {
    "tarjeta": "Tarjeta", "tc": "Tarjeta",
    "efectivo": "Efectivo",
    "transferencia": "Transferencia",
    "billetera móvil": "Billetera móvil",
}
MEDIO_PAGO_DESCONOCIDO = "Otro"

# Miembro "desconocido" de las dimensiones: evita llaves nulas en la tabla de hechos
# (por ejemplo, ventas sin cliente identificado o sin promoción).
CLAVE_DESCONOCIDA = 0

# Si más de este porcentaje de costos debe imputarse, el control de calidad avisa.
UMBRAL_COSTO_IMPUTADO = 0.01

# Nombres en español para la dimensión calendario (no dependen del idioma del sistema).
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


@dataclass(frozen=True)
class Settings:
    """Rutas del pipeline. Es inmutable (frozen) para que nadie la cambie a mitad de ejecución.

    Estructura esperada (la carpeta bronce es el PUNTO DE PARTIDA y trae los CSV originales):
        data/bronce/   <- datos originales, no se modifican nunca
        data/_staging/ <- resultados temporales que aún no están aprobados
        data/plata/    <- los genera main.py
        data/oro/      <- los genera main.py (esto es lo que se sube a Power BI)
    """

    data_dir: Path = Path("data")
    output_dir: Path | None = None
    bronze_source: Path | None = None
    silver_source: Path | None = None
    gold_source: Path | None = None
    quality_source: Path | None = None

    @property
    def destination_dir(self) -> Path:
        """Carpeta donde se escriben las capas publicadas o la zona temporal de staging."""
        return self.output_dir or self.data_dir

    @property
    def staging_dir(self) -> Path:
        """Zona temporal aislada para construir y validar antes de publicar."""
        return self.data_dir / "_staging"

    # Cada capa tiene su propia carpeta, así el linaje de los datos es visible.
    @property
    def bronze_dir(self) -> Path:
        return self.bronze_source or self.data_dir / "bronce"

    @property
    def silver_dir(self) -> Path:
        return self.silver_source or self.destination_dir / "plata"

    @property
    def rejected_dir(self) -> Path:
        return self.silver_dir / "rechazados"

    @property
    def gold_dir(self) -> Path:
        return self.gold_source or self.destination_dir / "oro"

    @property
    def quality_dir(self) -> Path:
        return self.quality_source or self.destination_dir / "calidad"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def archivo_bronce(self, tabla: str) -> Path:
        """Ruta del CSV original de una tabla dentro de la capa bronce."""
        return self.bronze_dir / f"{tabla}.csv"
