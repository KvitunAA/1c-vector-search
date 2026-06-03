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


def main() -> None:
    if not os.getenv("PROJECT_PROFILE"):
        print(
            "Ошибка: задайте переменную окружения PROJECT_PROFILE "
            "(см. run_index_all_<профиль>.cmd).",
            file=sys.stderr,
        )
        sys.exit(1)

    workers = os.getenv("INDEX_GRAPH_WORKERS", "8").strip() or "8"

    _run(
        "[1/4] Основная конфигурация — векторная БД (sqlite-vec)",
        "run_indexer.py",
        ["--clear", "--vector-only"],
    )
    _run(
        "[2/4] Основная конфигурация — граф зависимостей",
        "index_graph_mp.py",
        ["--clear", "--workers", workers],
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
        ["--extension", "--clear", "--workers", workers],
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
