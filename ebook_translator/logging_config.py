from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(name: str, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter(
        '{"time":"%(asctime)s","level":"%(levelname)s","component":"%(name)s","msg":"%(message)s"}'
    )
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    file_handler = RotatingFileHandler(log_dir / f"{name}.log", maxBytes=10_000_000, backupCount=5)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger

