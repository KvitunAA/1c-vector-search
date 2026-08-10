@echo off
setlocal
REM ============================================================================
REM MCP-сервер по HTTP (Streamable HTTP) для Secure MCP Tunnel.
REM
REM В отличие от run_server_ssh.cmd, здесь stdout НЕ является транспортом:
REM обмен идёт по HTTP, а вывод можно писать свободно.
REM
REM Слушаем ТОЛЬКО петлевой адрес: наружу и даже в локальную сеть сервер
REM не смотрит, к нему обращается лишь tunnel-client с того же хоста.
REM Чтобы открыть его в локальной сети, задайте MCP_HTTP_HOST до запуска.
REM
REM Использование:
REM   run_server_http.cmd [имя_профиля]
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

if not defined MCP_HTTP_HOST set "MCP_HTTP_HOST=127.0.0.1"
if not defined MCP_HTTP_PORT set "MCP_HTTP_PORT=8181"

set "PYTHON=python"
if defined VECTOR_PYTHON_PATH set "PYTHON=%VECTOR_PYTHON_PATH%"

"%PYTHON%" -u "%SCRIPT_DIR%server_http.py"
exit /b %ERRORLEVEL%
