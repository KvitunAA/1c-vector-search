"""
Клиент к vector MCP-серверу через stdio.

Запускает выбранный MCP-сервер тем же способом, что и Cursor (команда + env
профиля), и вызывает его инструменты по протоколу MCP. На одну операцию
(вопрос / ручной вызов / проверка) открывается одна сессия: внутри сессии можно
сделать несколько вызовов инструментов, после чего процесс корректно завершается.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .registry import McpServer, build_subprocess_env


class McpError(RuntimeError):
    """Ошибка взаимодействия с MCP-сервером."""


def _server_parameters(server: McpServer) -> StdioServerParameters:
    return StdioServerParameters(
        command=server.command,
        args=list(server.args),
        env=build_subprocess_env(server),
        cwd=server.cwd or None,
    )


def _parse_tool_payload(result: Any) -> Any:
    """Извлекает полезную нагрузку из CallToolResult.

    Инструменты сервера возвращают JSON-строку в TextContent. Если разобрать как
    JSON не удалось (например, инструкция в markdown) — возвращаем сырой текст.
    """
    contents = getattr(result, "content", None) or []
    texts: List[str] = []
    for item in contents:
        text = getattr(item, "text", None)
        if text is not None:
            texts.append(text)

    if not texts:
        return {}

    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, ValueError):
        return {"text": joined}


@asynccontextmanager
async def open_session(server: McpServer):
    """Контекстный менеджер: поднимает MCP-сервер и инициализирует сессию."""
    params = _server_parameters(server)
    try:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
    except McpError:
        raise
    except Exception as exc:  # noqa: BLE001 — нормализуем любую транспортную ошибку
        raise McpError(
            f"Не удалось подключиться к MCP-серверу '{server.name}': {exc}"
        ) from exc


async def _call(session: ClientSession, name: str, arguments: Dict[str, Any]) -> Any:
    result = await session.call_tool(name, arguments or {})
    return _parse_tool_payload(result)


async def call_tool(server: McpServer, name: str, arguments: Dict[str, Any]) -> Any:
    """Одиночный вызов инструмента в отдельной сессии."""
    async with open_session(server) as session:
        return await _call(session, name, arguments)


async def call_batch(
    server: McpServer, calls: List[Tuple[str, Dict[str, Any]]]
) -> List[Any]:
    """Несколько вызовов инструментов в рамках одной сессии (один запуск процесса)."""
    results: List[Any] = []
    async with open_session(server) as session:
        for name, arguments in calls:
            results.append(await _call(session, name, arguments))
    return results


async def list_tools(server: McpServer) -> List[Dict[str, Any]]:
    """Список инструментов сервера (имя + описание)."""
    async with open_session(server) as session:
        result = await session.list_tools()
        tools = []
        for tool in result.tools:
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                }
            )
        return tools


async def server_stats(server: McpServer) -> Dict[str, Any]:
    """Статистика векторной и графовой БД выбранного сервера."""
    async with open_session(server) as session:
        vector = await _call(session, "get_vectordb_stats", {})
        graph = await _call(session, "graph_stats", {})
    return {"vector": vector, "graph": graph}
