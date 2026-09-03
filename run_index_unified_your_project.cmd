@echo off
chcp 65001 >nul
REM Объединённая индексация: основная конфигурация + все расширения в одну БД для одного MCP.
REM В projects\your_project\your_project.env задайте:
REM   CONFIG_PATH=C:\path\to\main\config
REM   EXTENSIONS_ROOT=C:\path\to\extensions   (подпапки с Configuration.xml)
REM или EXTENSION_CONFIG_PATHS=C:\ext1;C:\ext2

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE=your_project
set VECTORDB_PATH=%SCRIPT_DIR%projects\your_project\vectordb
set GRAPHDB_PATH=%SCRIPT_DIR%projects\your_project\graphdb\graph.db

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

REM set INDEX_GRAPH_WORKERS=4

"%PYTHON%" "%SCRIPT_DIR%run_index_unified.py"

pause
