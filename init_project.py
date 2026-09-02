"""
Универсальная инициализация проекта 1С для векторного поиска

Создаёт профиль, .cmd-скрипты, MCP-конфигурацию по указанным параметрам.

Использование:
    python init_project.py --name my_project --config "D:\\Path\\To\\1C\\Config"
    python init_project.py --name my_project --config "D:\\Path\\To\\1C" --mcp-name "1c-vector-search-my" --add-mcp
    python init_project.py --name my_project --config "D:\\Path\\To\\1C" --index --add-mcp
"""

import sys
import os
import json
import argparse
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))


def _normalize_name(name: str) -> str:
    return name.replace("-", "_").replace(" ", "_")


def create_project(
    name: str,
    config_path: str,
    mcp_name: str = None,
    description: str = None,
    auto_index: bool = False,
    add_mcp: bool = False,
    python_path: str = None,
    overwrite: bool = False,
):
    """Создаёт проект и все необходимые артефакты."""
    name = _normalize_name(name)
    if not name.replace("_", "").isalnum():
        logger.error(f"Некорректное имя проекта: {name}. Используйте буквы, цифры, '_'.")
        return False

    config_path_obj = Path(config_path)
    if not config_path_obj.exists():
        logger.error(f"Путь к конфигурации не существует: {config_path}")
        return False

    if not (config_path_obj / "Configuration.xml").exists():
        logger.warning(f"Не найден Configuration.xml в {config_path}. Убедитесь, что указан корень выгрузки 1С.")

    project_root = Path(__file__).parent
    project_dir = project_root / "projects" / name
    vectordb_path = project_dir / "vectordb"

    if project_dir.exists() and not overwrite:
        logger.warning(f"Проект '{name}' уже существует: {project_dir}")
        response = input("Перезаписать конфигурацию? (y/N): ").strip().lower()
        if response != "y":
            return False

    project_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Создана директория: {project_dir}")

    mcp_name = mcp_name or f"1c-vector-search-{name}"
    description = description or f"MCP сервер для конфигурации 1С {name}"
    python_path = python_path or os.getenv("VECTOR_PYTHON_PATH", "python")

    env_file = project_dir / f"{name}.env"
    env_content = f"""# Конфигурация профиля {name}
# Для переопределения путей на другой машине создайте {name}.env.local (см. PORTABILITY.md)

# === ОБЯЗАТЕЛЬНЫЕ ПАРАМЕТРЫ ===
CONFIG_PATH={config_path_obj.resolve()}

# === MCP (имя сервера в Cursor; для нескольких конфигураций — разные имена) ===
MCP_SERVER_NAME={mcp_name}
CONFIG_DESCRIPTION={description}

# === РАСШИРЕНИЕ КОНФИГУРАЦИИ (отдельные БД; см. run_index_extension_*) ===
# Корень выгрузки расширения (Configuration.xml). Пусто — задайте при индексации --config-path.
# EXTENSION_CONFIG_PATH=
# EXTENSION_VECTORDB_PATH по умолчанию: projects/{name}/extension_vectordb
# EXTENSION_GRAPHDB_PATH по умолчанию: projects/{name}/extension_graphdb/graph.db

# === ОПЦИОНАЛЬНЫЕ (VECTORDB_PATH по умолчанию: projects/{name}/vectordb) ===
EMBEDDING_MODEL=your-embedding-model-name
EMBEDDING_DIMENSION=768
DEFAULT_SEARCH_LIMIT=5
MAX_SEARCH_LIMIT=20
LOG_LEVEL=INFO
"""
    env_file.write_text(env_content, encoding="utf-8")
    logger.success(f"Создан: {env_file}")

    run_server_cmd = project_root / f"run_server_{name}.cmd"
    run_server_content = f'''@echo off
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
    run_server_cmd.write_text(run_server_content, encoding="utf-8")
    logger.success(f"Создан: {run_server_cmd}")

    run_index_cmd = project_root / f"run_index_{name}.cmd"
    run_index_content = f'''@echo off
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
    run_index_cmd.write_text(run_index_content, encoding="utf-8")
    logger.success(f"Создан: {run_index_cmd}")

    run_index_graph_cmd = project_root / f"run_index_graph_{name}.cmd"
    run_index_graph_content = f'''@echo off
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
    run_index_graph_cmd.write_text(run_index_graph_content, encoding="utf-8")
    logger.success(f"Создан: {run_index_graph_cmd}")

    run_ext_vec = project_root / f"run_index_extension_vector_{name}.cmd"
    run_ext_vec.write_text(
        f'''@echo off
chcp 65001 >nul
REM Векторная БД только для выгрузки расширения (EXTENSION_* из projects/{name}/)

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE={name}

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

"%PYTHON%" "%SCRIPT_DIR%run_indexer.py" --extension --clear --vector-only
''',
        encoding="utf-8",
    )
    logger.success(f"Создан: {run_ext_vec}")

    run_ext_graph = project_root / f"run_index_extension_graph_{name}.cmd"
    run_ext_graph.write_text(
        f'''@echo off
chcp 65001 >nul
REM Граф только для выгрузки расширения (EXTENSION_GRAPHDB_PATH)

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE={name}

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

set "CLEAR_OPT="
if defined CLEAR_GRAPH set "CLEAR_OPT=--clear"
set "CACHE_OPT="
if defined NO_CACHE set "CACHE_OPT=--no-cache"

set "WORKERS_OPT="
if defined INDEX_GRAPH_WORKERS set "WORKERS_OPT=--workers %INDEX_GRAPH_WORKERS%"

"%PYTHON%" "%SCRIPT_DIR%index_graph_mp.py" --extension %WORKERS_OPT% %CLEAR_OPT% %CACHE_OPT%

pause
''',
        encoding="utf-8",
    )
    logger.success(f"Создан: {run_ext_graph}")

    run_ext_full = project_root / f"run_index_extension_full_{name}.cmd"
    run_ext_full.write_text(
        f'''@echo off
chcp 65001 >nul
REM Векторная БД + граф для выгрузки расширения (EXTENSION_*)

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE={name}

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

"%PYTHON%" "%SCRIPT_DIR%run_indexer.py" --extension --clear

pause
''',
        encoding="utf-8",
    )
    logger.success(f"Создан: {run_ext_full}")

    run_index_all_cmd = project_root / f"run_index_all_{name}.cmd"
    run_index_all_cmd.write_text(
        f'''@echo off
chcp 65001 >nul
REM Основная конфигурация (вектор + граф) и расширение (вектор + граф), если задан EXTENSION_CONFIG_PATH

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE={name}
set VECTORDB_PATH=%SCRIPT_DIR%projects\\{name}\\vectordb
set GRAPHDB_PATH=%SCRIPT_DIR%projects\\{name}\\graphdb\\graph.db

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

"%PYTHON%" "%SCRIPT_DIR%run_index_all.py"

pause
''',
        encoding="utf-8",
    )
    logger.success(f"Создан: {run_index_all_cmd}")

    mcp_setup = project_dir / "MCP_SETUP.md"
    mcp_setup_content = f"""# Подключение {name} к MCP в Cursor

## Статус

- **Векторная БД**: `{vectordb_path}`
- **Конфигурация 1С**: `{config_path_obj.resolve()}`

## Подключение в Cursor

`Ctrl+Shift+P` → **"MCP: Edit Config File"**

В секцию `mcpServers` добавьте:

```json
"{mcp_name}": {{
  "command": "cmd",
  "args": ["/c", "{str(run_server_cmd).replace(chr(92), chr(92)+chr(92))}"],
  "env": {{
    "PROJECT_PROFILE": "{name}",
    "VECTORDB_PATH": "{vectordb_path}",
    "GRAPHDB_PATH": "{project_dir / 'graphdb' / 'graph.db'}"
  }},
  "description": "{description}"
}}
```

## Индексация

```cmd
{project_root}\\run_index_{name}.cmd
```

## Использование

Префикс @ при выборе MCP: `@{mcp_name}`
"""
    mcp_setup.write_text(mcp_setup_content, encoding="utf-8")
    logger.success(f"Создан: {mcp_setup}")

    instructions_src = project_root / "projects" / "your_project" / "ИнструкцияПоИспользованиюMCP.md"
    instructions_dst = project_dir / "ИнструкцияПоИспользованиюMCP.md"
    if instructions_src.exists():
        instr_text = instructions_src.read_text(encoding="utf-8")
        instr_text = instr_text.replace("1c-vector-search", mcp_name)
        instructions_dst.write_text(instr_text, encoding="utf-8")
        logger.success(f"Скопирован: {instructions_dst}")

    readme = project_dir / "README.md"
    readme.write_text(
        f"""# Проект {name}

Конфигурация 1С: `{config_path_obj.resolve()}`

## Индексация

```cmd
{project_root}\\run_index_{name}.cmd
```

## MCP

См. [MCP_SETUP.md](MCP_SETUP.md)
""",
        encoding="utf-8",
    )
    logger.success(f"Создан: {readme}")

    if add_mcp:
        mcp_config_path = project_root / "mcp_config.json"
        mcp_entry = {
            mcp_name: {
                "command": "cmd",
                "args": ["/c", str(run_server_cmd)],
                "env": {
                    "PROJECT_PROFILE": name,
                    "VECTORDB_PATH": str(vectordb_path),
                    "GRAPHDB_PATH": str(project_dir / "graphdb" / "graph.db"),
                },
                "description": description,
            }
        }
        if mcp_config_path.exists():
            try:
                data = json.loads(mcp_config_path.read_text(encoding="utf-8"))
                data.setdefault("mcpServers", {})
                data["mcpServers"].update(mcp_entry)
                mcp_config_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                logger.success(f"Добавлено в mcp_config.json: {mcp_name}")
            except Exception as e:
                logger.error(f"Ошибка обновления mcp_config.json: {e}")
        else:
            mcp_config_path.write_text(
                json.dumps({"mcpServers": mcp_entry}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.success(f"Создан mcp_config.json с записью {mcp_name}")

    logger.info("=" * 70)
    logger.success(f"Проект '{name}' создан")
    logger.info("=" * 70)
    logger.info(f"Конфигурация: {config_path_obj.resolve()}")
    logger.info(f"Проект:       {project_dir}")
    logger.info(f"MCP имя:      {mcp_name}")
    logger.info("=" * 70)

    if auto_index:
        logger.info("Запуск индексации...")
        os.environ["PROJECT_PROFILE"] = name
        os.environ["VECTORDB_PATH"] = str(vectordb_path)
        os.environ["GRAPHDB_PATH"] = str(project_dir / "graphdb" / "graph.db")
        try:
            saved_argv = sys.argv
            sys.argv = ["index_config.py", "--config-path", str(config_path_obj.resolve()),
                        "--db-path", str(vectordb_path), "--clear"]
            try:
                from index_config import main as index_main
                index_main()
            finally:
                sys.argv = saved_argv
            logger.success("Индексация завершена")
        except Exception as e:
            logger.error(f"Ошибка индексации: {e}")
            logger.info(f"Запустите вручную: {run_index_cmd}")
            return False
    else:
        logger.info(f"Индексация: {run_index_cmd}")
        logger.info(f"MCP: скопируйте блок из projects/{name}/MCP_SETUP.md в настройки Cursor")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Универсальная инициализация проекта 1С для векторного поиска",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:

  python init_project.py --name my_project --config "D:\\1C\\Config"
  python init_project.py --name upp --config "D:\\1C\\UPP" --mcp-name "1c-vector-search-upp" --add-mcp --index
        """,
    )
    parser.add_argument("-n", "--name", help="Имя проекта (профиль)")
    parser.add_argument("-c", "--config", help="Путь к выгруженной конфигурации 1С")
    parser.add_argument("-m", "--mcp-name", help="Имя MCP в Cursor (по умолчанию: 1c-vector-search-<name>)")
    parser.add_argument("-d", "--description", help="Описание MCP")
    parser.add_argument("--add-mcp", action="store_true", help="Добавить в mcp_config.json")
    parser.add_argument("--index", action="store_true", help="Запустить индексацию")
    parser.add_argument("--python-path", help="Путь к python.exe (или env VECTOR_PYTHON_PATH)")
    parser.add_argument("-y", "--yes", action="store_true", help="Перезаписать без подтверждения")

    args = parser.parse_args()

    if not args.name or not args.config:
        logger.info("=== Инициализация проекта 1С ===")
        args.name = args.name or input("Имя проекта (напр. my_project): ").strip()
        args.config = args.config or input("Путь к конфигурации 1С: ").strip()
        if not args.name or not args.config:
            logger.error("Укажите имя и путь к конфигурации")
            sys.exit(1)
        if not args.index:
            args.index = input("Запустить индексацию? (y/N): ").strip().lower() == "y"
        if not args.add_mcp:
            args.add_mcp = input("Добавить в mcp_config.json? (y/N): ").strip().lower() == "y"

    success = create_project(
        name=args.name,
        config_path=args.config,
        mcp_name=args.mcp_name,
        description=args.description,
        auto_index=args.index,
        add_mcp=args.add_mcp,
        python_path=args.python_path,
        overwrite=args.yes,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
