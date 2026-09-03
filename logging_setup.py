"""Единая настройка logging для скриптов индексации (UTF-8 файл + консоль)."""
from __future__ import annotations

import logging
import sys


def setup_index_logging(level: str = "INFO", log_file: str = "indexing.log") -> None:
    """Настраивает root logger: indexing.log (UTF-8) и stdout (UTF-8 при поддержке Python)."""
    log_level = getattr(logging, (level or "INFO").upper(), logging.INFO)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    try:
        handlers.append(logging.StreamHandler(sys.stdout, encoding="utf-8"))
    except TypeError:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )
