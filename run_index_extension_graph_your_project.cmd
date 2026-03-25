@echo off
chcp 65001 >nul
REM Индексация только графа по выгрузке РАСШИРЕНИЯ (отдельный файл extension_graphdb\graph.db).
REM Граф основной конфигурации не изменяется.

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE=your_project

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

set "CLEAR_OPT="
if defined CLEAR_GRAPH set "CLEAR_OPT=--clear"
set "CACHE_OPT="
if defined NO_CACHE set "CACHE_OPT=--no-cache"

"%PYTHON%" "%SCRIPT_DIR%index_graph_mp.py" --extension --workers 8 %CLEAR_OPT% %CACHE_OPT%

pause
