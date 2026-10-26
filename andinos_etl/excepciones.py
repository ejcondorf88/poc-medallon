"""Excepciones propias del pipeline: permiten distinguir fallos de datos de fallos de código."""


class ErrorETL(Exception):
    """Clase base de todos los errores controlados del pipeline."""


class ErrorEsquema(ErrorETL):
    """El archivo de origen no tiene las columnas que el contrato de datos exige."""


class ErrorIntegridad(ErrorETL):
    """Se rompió una relación entre tablas (por ejemplo, una venta sin tienda)."""


class ErrorCalidad(ErrorETL):
    """Al menos un control de calidad falló y no se debe publicar la capa oro."""


class ErrorPublicacion(ErrorETL):
    """La promoción de la zona temporal falló y se restauró la publicación anterior."""


class ErrorConcurrencia(ErrorETL):
    """Otra ejecución del pipeline mantiene el bloqueo y no pudo serializarse."""
