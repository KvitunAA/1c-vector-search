@echo off
chcp 65001 >nul
REM Индексация только графа (без векторной БД)
REM Поддержка кеширования и чекпоинтов

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Определяем путь к Python
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE=your_project
set VECTORDB_PATH=%SCRIPT_DIR%projects\your_project\vectordb
set GRAPHDB_PATH=%SCRIPT_DIR%projects\your_project\graphdb\graph.db

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

REM Опции управления индексацией:
REM   CLEAR_GRAPH=1    - очистить граф перед индексацией
REM   NO_CACHE=1       - игнорировать кэш сканирования
set "CLEAR_OPT="
set "CACHE_OPT="

if defined CLEAR_GRAPH set "CLEAR_OPT=--clear"
if defined NO_CACHE set "CACHE_OPT=--no-cache"

REM Запуск индексатора (многопроцессорный, workers = cpu_count - 1 по умолчанию)
REM Опционально: set INDEX_GRAPH_WORKERS=12
set "WORKERS_OPT="
if defined INDEX_GRAPH_WORKERS set "WORKERS_OPT=--workers %INDEX_GRAPH_WORKERS%"

"%PYTHON%" "%SCRIPT_DIR%index_graph_mp.py" %WORKERS_OPT% %CLEAR_OPT% %CACHE_OPT%

pause
