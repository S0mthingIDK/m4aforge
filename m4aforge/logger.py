"""Logging setup: rotating file handler + console handler."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from m4aforge.core import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_FILE,
    DEFAULT_MAX_LOG_BYTES,
    LogLevel,
)

LOGGER_NAME = "m4aforge"


def setup_logger(
    verbose: bool = False,
    log_file: Path = DEFAULT_LOG_FILE,
    level: LogLevel = LogLevel.INFO,
) -> logging.Logger:
    """Configure and return the application's root logger."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    log_file.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=DEFAULT_MAX_LOG_BYTES,
        backupCount=DEFAULT_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else getattr(logging, level.value))
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def get_logger() -> logging.Logger:
    """Return the application logger."""
    return logging.getLogger(LOGGER_NAME)