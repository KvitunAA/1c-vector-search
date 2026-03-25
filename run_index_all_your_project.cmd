@echo off
chcp 65001 >nul
REM Полная индексация: основная конфигурация (вектор + граф), затем расширение (вектор + граф), если задан EXTENSION_CONFIG_PATH в projects\your_project\your_project.env

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE=your_project
set VECTORDB_PATH=%SCRIPT_DIR%projects\your_project\vectordb
set GRAPHDB_PATH=%SCRIPT_DIR%projects\your_project\graphdb\graph.db

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

REM Опционально: число процессов графа (по умолчанию 8 в run_index_all.py)
REM set INDEX_GRAPH_WORKERS=4

"%PYTHON%" "%SCRIPT_DIR%run_index_all.py"

pause
