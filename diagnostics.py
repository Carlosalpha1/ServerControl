"""Registro de diagnóstico con rotación para Windows y Linux."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core import config_directory


def configure_logging(debug: bool = False) -> Path | None:
    """Configura el registro sin impedir el arranque si falla la escritura."""
    logger = logging.getLogger("servercontrol")
    logger.setLevel(
        logging.DEBUG if debug else logging.INFO
    )
    logger.propagate = False

    # Evita duplicar handlers si se configura varias veces.
    for handler in tuple(logger.handlers):
        if getattr(handler, "_servercontrol_handler", False):
            logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s "
        "[%(threadName)s] %(name)s: %(message)s"
    )

    path = config_directory() / "logs" / "servercontrol.log"

    try:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        handler = RotatingFileHandler(
            path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )

    except OSError:
        handler = (
            logging.StreamHandler()
            if sys.stderr
            else logging.NullHandler()
        )
        path = None

    handler._servercontrol_handler = True
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if path is None:
        logger.warning(
            "No se pudo abrir el registro; "
            "se usará la consola disponible."
        )

    return path