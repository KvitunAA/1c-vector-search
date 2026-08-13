@echo off
setlocal
REM ============================================================================
REM MCP-сервер по HTTP (Streamable HTTP) для Secure MCP Tunnel.
REM
REM В отличие от run_server_ssh.cmd, здесь stdout НЕ является транспортом:
REM обмен идёт по HTTP, а вывод можно писать свободно.
REM
REM По умолчанию слушаем ТОЛЬКО петлевой адрес. Настройки для конкретной
REM машины (в том числе токен) задаются в local.env — он не коммитится:
REM
REM   MCP_HTTP_HOST=0.0.0.0
REM   MCP_HTTP_PORT=8181
REM   MCP_HTTP_BEARER_TOKEN=...
REM   MCP_HTTP_ALLOWED_HOSTS=host:port,host
REM
REM ВАЖНО: открывая сервер за пределы 127.0.0.1, обязательно задайте
REM MCP_HTTP_BEARER_TOKEN — иначе к базе конфигурации сможет обратиться
REM любой, кто дотянется до порта.
REM
REM Использование:
REM   run_server_http.cmd [имя_профиля]
REM ============================================================================

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PROFILE=%~1"
if not defined PROFILE set "PROFILE=%PROJECT_PROFILE%"
if not defined PROFILE set "PROFILE=your_project"

REM Настройки текущей машины из local.env (создаётся setup_machine.py).
REM Читаем все пары КЛЮЧ=ЗНАЧЕНИЕ, а не только путь к Python: так машинные
REM параметры и секреты не попадают ни в скрипт, ни в репозиторий.
if exist "%SCRIPT_DIR%local.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%local.env") do set "%%a=%%b"

set "PROJECT_PROFILE=%PROFILE%"
if not defined VECTORDB_PATH set "VECTORDB_PATH=%SCRIPT_DIR%projects\%PROFILE%\vectordb"
if not defined GRAPHDB_PATH set "GRAPHDB_PATH=%SCRIPT_DIR%projects\%PROFILE%\graphdb\graph.db"

if not defined MCP_HTTP_HOST set "MCP_HTTP_HOST=127.0.0.1"
if not defined MCP_HTTP_PORT set "MCP_HTTP_PORT=8181"

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

"%PYTHON%" -u "%SCRIPT_DIR%server_http.py"
exit /b %ERRORLEVEL%
