@echo off
chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set "PYTHON=%SCRIPT_DIR%.tools\python312\python.exe"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

if not exist "%PYTHON%" (
  echo Python 3.12 для проекта не найден: %PYTHON%
  echo Запустите: powershell -ExecutionPolicy Bypass -File scripts\bootstrap_python.ps1
  exit /b 1
)

"%PYTHON%" -m pytest tests\test_object_identifier.py tests\test_graph_staging.py tests\test_graph_db.py tests\test_mcp_profiles.py %*
