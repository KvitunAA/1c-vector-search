@echo off
setlocal
REM ============================================================================
REM MCP-сервер для запуска ЧЕРЕЗ SSH (stdio поверх SSH-канала).
REM
REM ВАЖНО: stdout этого скрипта — транспорт MCP (JSON-RPC).
REM Ничего нельзя выводить в stdout: ни echo, ни pause, ни заголовки.
REM Логи сервера идут в stderr (loguru) — это допустимо, SSH их пробрасывает.
REM
REM Использование (на удалённой машине запускается автоматически клиентом):
REM   run_server_ssh.cmd [имя_профиля]
REM Если профиль не задан аргументом — берётся PROJECT_PROFILE, иначе your_project.
REM ============================================================================

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PROFILE=%~1"
if not defined PROFILE set "PROFILE=%PROJECT_PROFILE%"
if not defined PROFILE set "PROFILE=your_project"

REM Путь к Python из local.env (создаётся setup_machine.py на каждой машине)
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do if "%%a"=="VECTOR_PYTHON_PATH" set "VECTOR_PYTHON_PATH=%%b"

set "PROJECT_PROFILE=%PROFILE%"
if not defined VECTORDB_PATH set "VECTORDB_PATH=%SCRIPT_DIR%projects\%PROFILE%\vectordb"
if not defined GRAPHDB_PATH set "GRAPHDB_PATH=%SCRIPT_DIR%projects\%PROFILE%\graphdb\graph.db"

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

REM -u: небуферизованный ввод-вывод, обязателен для stdio через SSH-пайп
"%PYTHON%" -u "%SCRIPT_DIR%run_server.py"
exit /b %ERRORLEVEL%
