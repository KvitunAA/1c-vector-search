@echo off
REM Web-приложение: чат по конфигурации 1С через vector MCP + LM Studio.
REM Открывается в браузере по адресу http://127.0.0.1:8765

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Определяем путь к Python (как в остальных скриптах репозитория)
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

set "HOST=127.0.0.1"
set "PORT=8765"

echo Запуск web-приложения на http://%HOST%:%PORT%
"%PYTHON%" -m uvicorn app.main:app --host %HOST% --port %PORT%

pause
