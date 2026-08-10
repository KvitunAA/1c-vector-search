"""
HTTP-транспорт MCP-сервера (Streamable HTTP) поверх тех же инструментов, что и stdio.

Зачем: клиенты вроде коннекторов ChatGPT не умеют stdio — им нужен HTTP-эндпоинт
на стабильном URL. Обработчики инструментов не дублируются: отсюда переиспользуется
объект `app` из server.py, поэтому stdio-режим и HTTP-режим всегда ведут себя одинаково.

Запуск:
    set PROJECT_PROFILE=ERP
    python server_http.py

Переменные окружения:
    MCP_HTTP_HOST        адрес прослушивания (по умолчанию 127.0.0.1)
    MCP_HTTP_PORT        порт (по умолчанию 8181)
    MCP_HTTP_PATH        путь эндпоинта (по умолчанию /mcp)
    MCP_HTTP_ALLOWED_HOSTS    список Host через запятую (нужен при работе за туннелем/прокси)
    MCP_HTTP_ALLOWED_ORIGINS  список Origin через запятую
    MCP_HTTP_BEARER_TOKEN     если задан, требовать заголовок Authorization: Bearer <токен>

ВНИМАНИЕ про MCP_HTTP_BEARER_TOKEN: это временная мера для проверок внутри локальной
сети, а НЕ замена OAuth. Общий статический токен не даёт ни разграничения по людям,
ни отзыва доступа у одного из них. Для эндпоинта, смотрящего в интернет, нужен OAuth
(mcp.server.auth): см. SCALING.md, раздел про коннектор ChatGPT.
"""
import os
import sys
import hmac
import contextlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from config import Config
# Импорт server поднимает векторную БД и граф и регистрирует все обработчики инструментов.
from server import app as mcp_app, db_manager, graph_manager


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


HOST = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_HTTP_PORT", "8181"))
PATH = "/" + os.getenv("MCP_HTTP_PATH", "/mcp").strip("/")
BEARER_TOKEN = os.getenv("MCP_HTTP_BEARER_TOKEN", "").strip()

ALLOWED_HOSTS = _csv_env("MCP_HTTP_ALLOWED_HOSTS")
ALLOWED_ORIGINS = _csv_env("MCP_HTTP_ALLOWED_ORIGINS")

# Защита от DNS-rebinding сверяет заголовок Host со списком разрешённых. За туннелем или
# обратным прокси в Host приходит внешний домен, а не адрес прослушивания, поэтому без
# явного списка все запросы получат 421 - и причина будет совершенно неочевидной.
security_settings = TransportSecuritySettings(
    enable_dns_rebinding_protection=bool(ALLOWED_HOSTS or ALLOWED_ORIGINS),
    allowed_hosts=ALLOWED_HOSTS,
    allowed_origins=ALLOWED_ORIGINS,
)

session_manager = StreamableHTTPSessionManager(
    app=mcp_app,
    event_store=None,      # без возобновления потока: истории событий не держим
    json_response=False,   # SSE-ответы, как ожидают клиенты MCP
    stateless=False,
    security_settings=security_settings,
)


async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
    if BEARER_TOKEN:
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {BEARER_TOKEN}"
        # Сравнение постоянного времени: обычное == по строкам утекает длину совпавшего префикса.
        if not hmac.compare_digest(auth, expected):
            logger.warning("Отклонён запрос без корректного Bearer-токена")
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
    await session_manager.handle_request(scope, receive, send)


async def healthz(request: Request) -> Response:
    """Проверка живости: не требует токена и не раскрывает содержимое баз."""
    return JSONResponse(
        {
            "status": "ok",
            "profile": Config.PROFILE_NAME,
            "vector_records": sum(db_manager.get_stats().values()),
            "graph_nodes": graph_manager.get_stats()["nodes_count"],
        }
    )


@contextlib.asynccontextmanager
async def lifespan(_: Starlette):
    async with session_manager.run():
        logger.info(f"MCP HTTP-транспорт готов: http://{HOST}:{PORT}{PATH}")
        if not BEARER_TOKEN:
            logger.warning(
                "MCP_HTTP_BEARER_TOKEN не задан - эндпоинт открыт всем, кто до него дотянется. "
                "Допустимо только для проверок внутри локальной сети."
            )
        yield
        logger.info("MCP HTTP-транспорт остановлен")


starlette_app = Starlette(
    debug=False,
    routes=[
        Route("/healthz", endpoint=healthz, methods=["GET"]),
        Mount(PATH, app=handle_mcp),
    ],
    lifespan=lifespan,
)


def main():
    logger.info(f"Профиль: {Config.PROFILE_NAME}")
    logger.info(f"Прослушивание: {HOST}:{PORT}, эндпоинт {PATH}")
    if ALLOWED_HOSTS:
        logger.info(f"Разрешённые Host: {', '.join(ALLOWED_HOSTS)}")
    else:
        logger.info("Проверка Host отключена (MCP_HTTP_ALLOWED_HOSTS не задан)")
    uvicorn.run(starlette_app, host=HOST, port=PORT, log_level=Config.LOG_LEVEL.lower())


if __name__ == "__main__":
    main()
