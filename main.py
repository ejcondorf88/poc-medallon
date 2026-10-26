"""Punto de entrada del ETL medallón ejecutable por etapas o en modo completo."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from andinos_etl.config import Settings
from andinos_etl.excepciones import ErrorETL
from andinos_etl.pipeline import PipelineMedallon
from andinos_etl.registro import configurar_logging
from andinos_etl.stages import (
    APPROVED_BUNDLE_NAME,
    ejecutar_bronce,
    ejecutar_oro,
    ejecutar_plata,
    verificar_artifact,
)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_QUALITY_GATE = 3
EXIT_CHECKSUM = 4


def leer_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ETL medallón de Supermercados Andinos (bronce -> plata -> oro)")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="carpeta raíz del modo completo")
    parser.add_argument("--stage", choices=("completo", "bronce", "plata", "oro", "verificar"), default="completo")
    parser.add_argument("--bronze-dir", type=Path, default=Path("data/bronce"), help="directorio Bronze de entrada")
    parser.add_argument("--plata-dir", type=Path, default=Path("data/plata-validado"), help="directorio Plata de entrada o salida")
    parser.add_argument("--output-dir", type=Path, default=None, help="directorio de salida del stage")
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = leer_argumentos()
    configurar_logging(args.data_dir / "logs")
    try:
        if args.stage == "completo":
            settings = Settings(data_dir=args.data_dir)
            PipelineMedallon(settings).ejecutar()
            return EXIT_OK
        if args.stage == "bronce":
            salida = args.output_dir or args.data_dir / "bronce-validado"
            resultado = ejecutar_bronce(args.bronze_dir, salida)
            print(f"BRONCE OK: {salida}")
            return EXIT_OK
        if args.stage == "plata":
            salida = args.output_dir or args.data_dir / "plata-validado"
            resultado = ejecutar_plata(args.bronze_dir, salida)
            print(f"PLATA OK: {salida}")
            return EXIT_OK
        if args.stage == "oro":
            salida = args.output_dir or args.data_dir
            resultado = ejecutar_oro(args.bronze_dir, args.plata_dir, salida)
            if resultado.status == "REJECTED":
                logging.getLogger("main").error("Quality gate REJECTED: %s", salida / "diagnostico" / "gate.json")
                return EXIT_QUALITY_GATE
            print(f"ORO APPROVED: {resultado.output_dir}")
            return EXIT_OK
        artifact = args.output_dir or args.data_dir / APPROVED_BUNDLE_NAME
        resultado = verificar_artifact(artifact)
        if not resultado.ok:
            for error in resultado.errors:
                logging.getLogger("main").error("Verificación fallida: %s", error)
            return EXIT_CHECKSUM
        print(f"VERIFICACION OK: {artifact}")
        return EXIT_OK
    except (ErrorETL, FileNotFoundError, ValueError) as error:
        logging.getLogger("main").error("El pipeline se detuvo: %s", error)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
