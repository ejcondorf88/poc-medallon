"""Orquestador: construye staging, valida la calidad y publica solo una versión aprobada."""
from __future__ import annotations

import logging
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import replace

import pandas as pd

from .almacen import AlmacenCsv
from .bronce import CapaBronce
from .calidad import CapaCalidad, ContextoCalidad
from .config import Settings
from .excepciones import ErrorCalidad, ErrorConcurrencia, ErrorPublicacion
from .oro import CapaOro
from .plata import CapaPlata


class PipelineMedallon:
    """Punto único de entrada del proceso. Inyecta las mismas dependencias a todas las capas."""

    CAPAS_PUBLICADAS = ("calidad", "plata", "oro")

    def __init__(self, settings: Settings, lock_timeout: float = 300.0, lock_poll: float = 0.1) -> None:
        self.settings = settings
        self.lock_timeout = lock_timeout
        self.lock_poll = lock_poll
        self.staging_settings = replace(settings, output_dir=settings.staging_dir)
        almacen = AlmacenCsv()
        self.bronce = CapaBronce(self.staging_settings, almacen)
        self.plata = CapaPlata(self.staging_settings, almacen)
        self.oro = CapaOro(self.staging_settings, almacen)
        self.calidad = CapaCalidad(self.staging_settings, almacen)
        self.log = logging.getLogger(self.__class__.__name__)

    @staticmethod
    def _mostrar(mensaje: str) -> None:
        print(mensaje, flush=True)

    def ejecutar(self) -> pd.DataFrame:
        with self._lock_ejecucion():
            return self._ejecutar_bloqueado()

    def _ejecutar_bloqueado(self) -> pd.DataFrame:
        inicio = time.perf_counter()
        self._preparar_staging()
        self._mostrar("\n=== FLUJO ETL MEDALLÓN ===")
        self._mostrar(
            "Cada etapa escribe primero en staging; calidad debe aprobar antes de "
            "reemplazar lo publicado. Así un fallo no deja una salida parcial."
        )
        self._mostrar(f"[1/5] BRONCE  -> lectura desde {self.settings.bronze_dir.resolve()}")

        datos_bronce = self.bronce.ejecutar()
        self._mostrar(f"      {len(datos_bronce)} tablas fuente leídas")

        self._mostrar(f"[2/5] PLATA   -> limpieza y trazabilidad en {self.staging_settings.silver_dir.resolve()}")
        resultados_plata = self.plata.ejecutar(datos_bronce)
        limpias = {t: r.limpio for t, r in resultados_plata.items()}
        self._mostrar(f"      {sum(len(df) for df in limpias.values()):,} filas válidas en staging")

        self._mostrar(f"[3/5] ORO     -> modelo en estrella en {self.staging_settings.gold_dir.resolve()}")
        datos_oro = self.oro.ejecutar(limpias)
        self._mostrar(f"      {len(datos_oro)} tablas generadas; hecho principal: {len(datos_oro['fact_ventas']):,} filas")

        self._mostrar(f"[4/5] CALIDAD -> controles en {self.staging_settings.quality_dir.resolve()}")
        reporte = self.calidad.ejecutar(ContextoCalidad(datos_bronce, resultados_plata, datos_oro))
        if (reporte["estado"] == "FALLO").any():
            self._mostrar(
                "[5/5] PUBLICACIÓN CANCELADA: un FALLO significa que no se concilian "
                "filas, importes o relaciones; la versión anterior permanece intacta. "
                f"Revise {self.staging_settings.quality_dir / 'reporte_calidad.csv'}."
            )
            raise ErrorCalidad(
                "Fallaron controles de calidad críticos; revise "
                f"{self.staging_settings.quality_dir / 'reporte_calidad.csv'}"
            )

        self._mostrar(
            "[5/5] PUBLICACIÓN -> todos los controles bloqueantes aprobaron; "
            "promoción atómica de plata, oro y calidad."
        )
        self._promover_staging()
        self._mostrar(f"      Publicadas: plata, oro y calidad en {self.settings.destination_dir.resolve()}")
        self.log.info("Pipeline terminado en %.1f s", time.perf_counter() - inicio)
        self._mostrar(f"=== FLUJO COMPLETADO CORRECTAMENTE EN {time.perf_counter() - inicio:.1f} s ===\n")
        return reporte

    @contextmanager
    def _lock_ejecucion(self):
        bloqueo = self.settings.data_dir / ".pipeline.lock"
        bloqueo.parent.mkdir(parents=True, exist_ok=True)
        limite = time.monotonic() + self.lock_timeout
        while True:
            try:
                bloqueo.mkdir()
                break
            except FileExistsError:
                if time.monotonic() >= limite:
                    raise ErrorConcurrencia(
                        f"El pipeline no pudo obtener el bloqueo {bloqueo.resolve()} "
                        f"dentro de {self.lock_timeout:.1f} segundos"
                    )
                time.sleep(self.lock_poll)
        try:
            (bloqueo / "owner.txt").write_text(str(os.getpid()), encoding="utf-8")
            yield
        finally:
            shutil.rmtree(bloqueo, ignore_errors=True)

    def _preparar_staging(self) -> None:
        """Elimina restos de una corrida anterior y prepara una zona temporal limpia."""
        staging = self.settings.staging_dir
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        self.log.info("Staging preparado | %s", staging.resolve())

    def _promover_staging(self) -> None:
        """Promueve las capas aprobadas con reemplazo de directorios y rollback si falla."""
        staging = self.settings.staging_dir
        destino = self.settings.destination_dir
        backup = staging / "_backup"
        backup.mkdir(parents=True, exist_ok=True)
        instaladas: list[str] = []
        respaldadas: list[str] = []

        try:
            for capa in self.CAPAS_PUBLICADAS:
                origen = staging / capa
                objetivo = destino / capa
                if not origen.exists():
                    raise ErrorPublicacion(f"No existe la capa aprobada en staging: {origen}")

                if objetivo.exists():
                    os.replace(objetivo, backup / capa)
                    respaldadas.append(capa)

                os.replace(origen, objetivo)
                instaladas.append(capa)
        except Exception as error:
            for capa in reversed(instaladas):
                objetivo = destino / capa
                if objetivo.exists():
                    shutil.rmtree(objetivo)

            for capa in reversed(respaldadas):
                respaldo = backup / capa
                if respaldo.exists():
                    os.replace(respaldo, destino / capa)

            raise ErrorPublicacion(
                f"No se pudo publicar la versión aprobada; se restauró la publicación anterior: {error}"
            ) from error

        shutil.rmtree(staging)
