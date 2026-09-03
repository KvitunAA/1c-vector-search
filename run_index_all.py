"""
Последовательная индексация основной конфигурации и расширения.

Порядок:
  1) векторная БД основной конфигурации;
  2) граф основной конфигурации;
  3) векторная БД расширения (если задан EXTENSION_CONFIG_PATH в профиле);
  4) граф расширения.

Требуется переменная окружения PROJECT_PROFILE и пути в projects/<профиль>/*.env.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _run(description: str, script: str, args: list) -> None:
    root = _project_root()
    cmd = [sys.executable, str(root / script)] + args
    print()
    print("=" * 60)
    print(description)
    print("=" * 60)
    subprocess.run(cmd, cwd=str(root), check=True)


def _graph_index_args(*, extension: bool = False) -> List[str]:
    """Аргументы index_graph_mp.py из профиля (workers, staging)."""
    sys.path.insert(0, str(_project_root()))
    import importlib

    import config

    importlib.reload(config)
    from config import Config

    args: List[str] = []
    if extension:
        args.append("--extension")
    args.append("--clear")
    if Config.INDEX_GRAPH_WORKERS > 0:
        args.extend(["--workers", str(Config.INDEX_GRAPH_WORKERS)])
    if Config.GRAPH_USE_STAGING:
        args.append("--staging")
    return args


def main() -> None:
    if not os.getenv("PROJECT_PROFILE"):
        print(
            "Ошибка: задайте переменную окружения PROJECT_PROFILE "
            "(см. run_index_all_<профиль>.cmd).",
            file=sys.stderr,
        )
        sys.exit(1)

    graph_args = _graph_index_args()

    _run(
        "[1/4] Основная конфигурация — векторная БД (sqlite-vec)",
        "run_indexer.py",
        ["--clear", "--vector-only"],
    )
    _run(
        "[2/4] Основная конфигурация — граф зависимостей",
        "index_graph_mp.py",
        graph_args,
    )

    sys.path.insert(0, str(_project_root()))
    import importlib

    import config

    importlib.reload(config)
    from config import Config

    ext_path = (Config.EXTENSION_CONFIG_PATH or "").strip()
    if not ext_path:
        print()
        print(
            "Расширение: EXTENSION_CONFIG_PATH не задан в профиле — "
            "шаги 3–4 пропущены (основная конфигурация уже проиндексирована)."
        )
        print()
        return

    _run(
        "[3/4] Расширение — векторная БД",
        "run_indexer.py",
        ["--extension", "--clear", "--vector-only"],
    )
    _run(
        "[4/4] Расширение — граф зависимостей",
        "index_graph_mp.py",
        _graph_index_args(extension=True),
    )
    print()
    print("Готово: основная конфигурация и расширение проиндексированы.")
    print()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"\nОшибка на шаге индексации (код {exc.returncode}).", file=sys.stderr)
        sys.exit(exc.returncode or 1)
