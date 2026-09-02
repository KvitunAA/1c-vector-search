"""
Синхронизация MCP-конфигурации для всех профилей в projects/.

Примеры:
    python scripts/sync_mcp_config.py
    python scripts/sync_mcp_config.py --cursor
    python scripts/sync_mcp_config.py --list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from mcp_profiles import build_mcp_config, discover_profiles, merge_into_cursor_mcp, write_mcp_config


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сгенерировать MCP-записи для всех профилей конфигураций 1С",
    )
    parser.add_argument(
        "--cursor",
        action="store_true",
        help="Обновить ~/.cursor/mcp.json (добавить/перезаписать записи профилей)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Показать найденные профили без записи файлов",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Не сливать с существующим mcp_config.json",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Путь к выходному JSON (по умолчанию: mcp_config.json в корне репозитория)",
    )
    args = parser.parse_args()

    profiles = discover_profiles()
    if not profiles:
        logger.warning("Профили не найдены. Создайте projects/<имя>/<имя>.env через init_project.py")
        return 1

    if args.list:
        for p in profiles:
            logger.info(f"  {p.mcp_server_name:30} profile={p.profile_name}  {p.description}")
        return 0

    out = write_mcp_config(
        output_path=Path(args.output) if args.output else None,
        merge_existing=not args.no_merge,
    )
    logger.success(f"Записано {len(profiles)} профилей → {out}")

    if args.cursor:
        cursor_path = Path.home() / ".cursor" / "mcp.json"
        count = merge_into_cursor_mcp(cursor_path)
        logger.success(f"Обновлено записей в {cursor_path}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
