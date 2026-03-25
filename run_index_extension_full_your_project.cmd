@echo off
chcp 65001 >nul
REM Полная индексация выгрузки расширения: векторная БД + граф (оба в отдельных путях EXTENSION_*).

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set PROJECT_PROFILE=your_project

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

"%PYTHON%" "%SCRIPT_DIR%run_indexer.py" --extension --clear

pause
