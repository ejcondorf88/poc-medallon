"""Configuración del registro de eventos (logging): consola y archivo."""
from __future__ import annotations

import logging
from pathlib import Path

FORMATO = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configurar_logging(log_dir: Path, nivel: int = logging.INFO) -> None:
    """Escribe los mensajes en consola y en `logs/etl.log` para tener evidencia de cada corrida."""
    log_dir.mkdir(parents=True, exist_ok=True)
    raiz = logging.getLogger()
    raiz.handlers.clear()                      # evita mensajes duplicados si se llama dos veces
    raiz.setLevel(nivel)
    for handler in (logging.StreamHandler(), logging.FileHandler(log_dir / "etl.log", encoding="utf-8")):
        handler.setFormatter(logging.Formatter(FORMATO))
        raiz.addHandler(handler)
