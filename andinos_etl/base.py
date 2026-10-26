"""Clase base de las capas del medallón (patrón Plantilla: todas se construyen y ejecutan igual)."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .almacen import AlmacenCsv
from .config import Settings


class CapaMedallon(ABC):
    """Contrato común: cada capa recibe la configuración y el almacén, y expone `ejecutar()`."""

    def __init__(self, settings: Settings, almacen: AlmacenCsv | None = None) -> None:
        self.settings = settings
        self.almacen = almacen or AlmacenCsv()          # inyección de dependencias: fácil de probar
        self.log = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def ejecutar(self, *args, **kwargs):
        """Ejecuta la capa. Cada subclase define qué recibe y qué devuelve."""
