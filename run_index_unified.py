"""
Объединённая индексация: основная конфигурация + все расширения в одну векторную БД и один граф.

Используются переменные профиля:
  CONFIG_PATH            — основная конфигурация;
  EXTENSIONS_ROOT        — каталог с подпапками расширений;
  EXTENSION_CONFIG_PATHS — явный список путей расширений через ``;``;
  EXTENSION_CONFIG_PATH  — одно расширение (обратная совместимость).

Результат: один MCP (VECTORDB_PATH + GRAPHDB_PATH) ищет по всем источникам.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

from config import Config
from config_dump import read_configuration_dump_info
from config_paths import (
    build_unified_index_plan,
    collect_extension_config_paths,
    resolve_index_source,
    validate_config_root,
)
from index_config import ConfigIndexer
from index_graph_mp import GraphIndexer
from logging_setup import setup_index_logging

import logging

setup_index_logging(Config.LOG_LEVEL)
logger = logging.getLogger(__name__)


def _require_profile() -> None:
    if not os.getenv("PROJECT_PROFILE"):
        logger.error(
            "Задайте переменную окружения PROJECT_PROFILE "
            "(см. run_index_unified_<профиль>.cmd)."
        )
        sys.exit(1)


def _extension_paths() -> List[Path]:
    raw_paths = collect_extension_config_paths(
        Config.EXTENSION_CONFIG_PATH,
        Config.EXTENSION_CONFIG_PATHS,
        Config.EXTENSIONS_ROOT,
        exclude_paths=[Config.CONFIG_PATH] if Config.CONFIG_PATH else None,
    )
    validated: List[Path] = []
    for path in raw_paths:
        try:
            validated.append(validate_config_root(path, label="Расширение"))
        except FileNotFoundError as exc:
            logger.error(str(exc))
            sys.exit(1)
    return validated


def _graph_workers() -> int | None:
    if Config.INDEX_GRAPH_WORKERS > 0:
        return Config.INDEX_GRAPH_WORKERS
    return None


def _index_vector(config_root: Path, *, clear_existing: bool, source_kind: str) -> None:
    source = resolve_index_source(config_root, source_kind)
    logger.info(
        "Векторная БД: %s (%s, source_id=%s)",
        config_root,
        source["configuration_name"],
        source["source_id"],
    )
    indexer = ConfigIndexer(
        config_path=str(config_root),
        db_path=Config.VECTORDB_PATH,
        clear_existing=clear_existing,
        graph_db_path=Config.GRAPHDB_PATH,
        index_source=source,
    )
    indexer.index_all(vector_only=True)


def _index_graph(config_root: Path, *, clear_existing: bool) -> None:
    dump = read_configuration_dump_info(config_root)
    kind = "расширение" if dump.get("is_extension") else "основная конфигурация"
    logger.info("Граф: %s (%s)", config_root, kind)
    graph_indexer = GraphIndexer(
        config_path=str(config_root),
        db_path=Config.GRAPHDB_PATH,
        clear_existing=clear_existing,
        use_cache=not clear_existing,
        workers=_graph_workers(),
        staging=Config.GRAPH_USE_STAGING,
    )
    graph_indexer.index_all()


def main() -> None:
    _require_profile()

    if not Config.CONFIG_PATH.strip():
        logger.error("CONFIG_PATH не задан в профиле.")
        sys.exit(1)

    try:
        main_root = validate_config_root(Config.CONFIG_PATH, label="Основная конфигурация")
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)

    extension_roots = _extension_paths()
    plan = build_unified_index_plan(main_root, extension_roots)
    total_steps = len(plan) * 2
    step = 0

    logger.info("=" * 60)
    logger.info("Объединённая индексация в один MCP")
    logger.info("Векторная БД: %s", Config.VECTORDB_PATH)
    logger.info("Граф: %s", Config.GRAPHDB_PATH)
    logger.info("Основная конфигурация: %s", main_root)
    if extension_roots:
        logger.info("Расширений: %s", len(extension_roots))
        for ext in extension_roots:
            logger.info("  - %s", ext)
    else:
        logger.warning(
            "Расширения не заданы (EXTENSIONS_ROOT / EXTENSION_CONFIG_PATHS / EXTENSION_CONFIG_PATH). "
            "Будет проиндексирована только основная конфигурация."
        )
    logger.info("=" * 60)

    for entry in plan:
        source_kind = entry["kind"]
        config_root = entry["path"]
        clear_flag = entry["clear"]

        step += 1
        logger.info("[%s/%s] %s — векторная БД: %s", step, total_steps, source_kind, config_root.name)
        _index_vector(config_root, clear_existing=clear_flag, source_kind=source_kind)

        step += 1
        logger.info("[%s/%s] %s — граф: %s", step, total_steps, source_kind, config_root.name)
        _index_graph(config_root, clear_existing=clear_flag)

    logger.info("")
    logger.info("Готово: основная конфигурация и %s расширений в одной БД для MCP.", len(extension_roots))
    logger.info("MCP-сервер: %s", Config.MCP_SERVER_NAME)
    logger.info("")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("Индексация прервана пользователем")
        sys.exit(1)
