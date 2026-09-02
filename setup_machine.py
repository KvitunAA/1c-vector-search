"""
Первичная настройка на новой машине.

Обновляет абсолютные пути в mcp_config.json, создаёт local.env с путями к Python,
переводит .cmd-скрипты на переносимый формат. Запускайте после копирования
репозитория на другой компьютер.

Использование:
    python setup_machine.py
    python setup_machine.py --python "C:\\path\\to\\python.exe"
"""
import json
import os
import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).parent


def make_run_server_cmd(name: str, vector_root: Path) -> str:
    """Генерирует переносимый run_server_<name>.cmd"""
    return f'''@echo off
REM MCP-сервер {name}: переносимый скрипт (пути относительно расположения)

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE={name}
set VECTORDB_PATH=%SCRIPT_DIR%projects\\{name}\\vectordb
set GRAPHDB_PATH=%SCRIPT_DIR%projects\\{name}\\graphdb\\graph.db

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

"%PYTHON%" "%SCRIPT_DIR%run_server.py"
'''


def make_run_index_cmd(name: str, vector_root: Path) -> str:
    """Генерирует переносимый run_index_<name>.cmd"""
    return f'''@echo off
chcp 65001 >nul
REM Индексация {name}: пути из projects/{name}/*.env или *.env.local

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE={name}
set VECTORDB_PATH=%SCRIPT_DIR%projects\\{name}\\vectordb
set GRAPHDB_PATH=%SCRIPT_DIR%projects\\{name}\\graphdb\\graph.db

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

"%PYTHON%" "%SCRIPT_DIR%run_indexer.py" --clear
'''


def make_run_index_graph_cmd(name: str, vector_root: Path) -> str:
    """Генерирует переносимый run_index_graph_<name>.cmd"""
    return f'''@echo off
chcp 65001 >nul
REM Индексация только графа {name}

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE={name}
set VECTORDB_PATH=%SCRIPT_DIR%projects\\{name}\\vectordb
set GRAPHDB_PATH=%SCRIPT_DIR%projects\\{name}\\graphdb\\graph.db

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

"%PYTHON%" "%SCRIPT_DIR%index_graph_mp.py" --clear
'''


def migrate_cmd_files(vector_root: Path) -> int:
    """Переводит run_server_*.cmd, run_index_*.cmd, run_index_graph_*.cmd в переносимый формат."""
    count = 0
    for f in vector_root.glob("run_server_*.cmd"):
        name = f.stem.replace("run_server_", "")
        if (vector_root / "projects" / name).exists():
            f.write_text(make_run_server_cmd(name, vector_root), encoding="utf-8")
            count += 1
    for f in vector_root.glob("run_index_*.cmd"):
        if "run_index_graph" in f.stem:
            name = f.stem.replace("run_index_graph_", "")
        else:
            name = f.stem.replace("run_index_", "")
        if (vector_root / "projects" / name).exists():
            if "run_index_graph" in f.stem:
                f.write_text(make_run_index_graph_cmd(name, vector_root), encoding="utf-8")
            else:
                f.write_text(make_run_index_cmd(name, vector_root), encoding="utf-8")
            count += 1
    return count


def detect_python() -> str:
    """Определяет путь к Python."""
    local_tools = PROJECT_ROOT / ".tools" / "python312" / "python.exe"
    if local_tools.exists():
        return str(local_tools)

    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c", "import sys; print(sys.executable)"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return sys.executable


def fix_mcp_paths(data: dict, vector_root: Path, python_path: str) -> int:
    """Заменяет старые пути в mcp_config на актуальные для текущей машины."""
    updated = 0
    servers = data.get("mcpServers", {})

    for cfg in servers.values():
        if cfg.get("command") == "cmd":
            args = cfg.get("args", [])
            if len(args) >= 2 and args[0] == "/c":
                old_path = args[1]
                if isinstance(old_path, str) and old_path.endswith(".cmd"):
                    cmd_name = Path(old_path).name
                    new_path = str(vector_root / cmd_name)
                    if (vector_root / cmd_name).exists():
                        cfg["args"] = ["/c", new_path]
                        updated += 1
        else:
            if "python" in str(cfg.get("command", "")).lower():
                cfg["command"] = python_path
                updated += 1
            args = cfg.get("args", [])
            for i, a in enumerate(args):
                if isinstance(a, str) and "run_server.py" in a:
                    args[i] = str(vector_root / "run_server.py")
                    updated += 1
                    break
            cfg["args"] = args

        env = cfg.get("env", {})
        vdb = env.get("VECTORDB_PATH", "")
        if vdb and "projects" in vdb.replace("\\", "/"):
            parts = vdb.replace("\\", "/").split("/")
            try:
                idx = parts.index("projects")
                if idx + 2 <= len(parts):
                    proj = parts[idx + 1]
                    env["VECTORDB_PATH"] = str(vector_root / "projects" / proj / "vectordb")
                    env["GRAPHDB_PATH"] = str(vector_root / "projects" / proj / "graphdb" / "graph.db")
                    updated += 1
            except (ValueError, IndexError):
                pass

    return updated


def main():
    parser = __import__("argparse").ArgumentParser(
        description="Настройка на новой машине (пути, Python, MCP)",
    )
    parser.add_argument("--python", "-p", help="Путь к python.exe")
    parser.add_argument("--dry-run", action="store_true", help="Показать изменения без записи")
    args = parser.parse_args()

    vector_root = PROJECT_ROOT.resolve()
    python_path = args.python or os.getenv("VECTOR_PYTHON_PATH") or detect_python()

    logger.info("=" * 60)
    logger.info("Настройка")
    logger.info("=" * 60)
    logger.info(f"Корень проекта: {vector_root}")
    logger.info(f"Python:         {python_path}")

    local_env = vector_root / "local.env"
    local_content = f"""# Пути для текущей машины (не коммитить)
VECTOR_PYTHON_PATH={python_path}
"""
    if not args.dry_run:
        local_env.write_text(local_content, encoding="utf-8")
        logger.success(f"Создан/обновлён: {local_env}")

    migrated = migrate_cmd_files(vector_root)
    if migrated > 0 and not args.dry_run:
        logger.success(f"Обновлено .cmd-скриптов: {migrated}")

    mcp_path = vector_root / "mcp_config.json"
    if mcp_path.exists():
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        n = fix_mcp_paths(data, vector_root, python_path)
        if not args.dry_run and n > 0:
            mcp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.success(f"mcp_config.json: обновлено {n} путей")
        elif args.dry_run and n > 0:
            logger.info(f"[dry-run] Будет обновлено {n} путей в mcp_config.json")
    else:
        logger.warning("mcp_config.json отсутствует — создайте проекты через init_project.py --add-mcp")

    logger.info("=" * 60)
    logger.info("Следующие шаги:")
    logger.info("1. Создайте projects/<имя>/*.env.local для CONFIG_PATH (путь к выгрузке 1С)")
    logger.info("2. Запустите run_index_<имя>.cmd для индексации")
    logger.info("3. Добавьте local.env в .gitignore (уже есть)")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
