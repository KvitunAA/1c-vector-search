"""
FastAPI-приложение: витрина для работы с vector MCP-серверами 1С через LM Studio.

Запуск:
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
или скриптом run_app.cmd в корне репозитория.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import lmstudio, mcp_client, rag, registry

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="1C Vector MCP Chat", version="1.0.0")


class ChatRequest(BaseModel):
    server: str
    question: str = Field(min_length=1)
    lm_base_url: str = lmstudio.DEFAULT_BASE_URL
    lm_model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = Field(default=1024, ge=64, le=8192)
    limit: int = Field(default=5, ge=1, le=20)
    use_graph: Optional[bool] = None


class ToolRequest(BaseModel):
    server: str
    tool: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@app.get("/api/mcp")
async def get_mcp_servers() -> Dict[str, Any]:
    servers = registry.load_servers()
    return {"servers": [s.to_public_dict() for s in servers]}


@app.get("/api/mcp/{name}/stats")
async def get_mcp_stats(name: str):
    server = registry.get_server(name)
    if server is None:
        return _error(f"MCP-сервер '{name}' не найден в реестре.", 404)
    try:
        stats = await mcp_client.server_stats(server)
    except mcp_client.McpError as exc:
        return _error(str(exc), 502)
    return stats


@app.get("/api/mcp/{name}/tools")
async def get_mcp_tools(name: str):
    server = registry.get_server(name)
    if server is None:
        return _error(f"MCP-сервер '{name}' не найден в реестре.", 404)
    try:
        tools = await mcp_client.list_tools(server)
    except mcp_client.McpError as exc:
        return _error(str(exc), 502)
    return {"tools": tools}


@app.get("/api/health")
async def health(server: Optional[str] = None, lm_base_url: str = lmstudio.DEFAULT_BASE_URL):
    result: Dict[str, Any] = {"lmstudio": {}, "mcp": {}}

    try:
        models = await lmstudio.list_models(lm_base_url)
        result["lmstudio"] = {"ok": True, "models": models}
    except lmstudio.LmStudioError as exc:
        result["lmstudio"] = {"ok": False, "error": str(exc)}

    if server:
        srv = registry.get_server(server)
        if srv is None:
            result["mcp"] = {"ok": False, "error": f"Сервер '{server}' не найден."}
        else:
            try:
                stats = await mcp_client.server_stats(srv)
                vector_total = sum(
                    v for v in (stats.get("vector", {}).get("collections", {}) or {}).values()
                    if isinstance(v, int)
                )
                result["mcp"] = {
                    "ok": True,
                    "vector_records": vector_total,
                    "graph_nodes": stats.get("graph", {}).get("nodes_count", 0),
                    "graph_edges": stats.get("graph", {}).get("edges_count", 0),
                }
            except mcp_client.McpError as exc:
                result["mcp"] = {"ok": False, "error": str(exc)}

    return result


@app.post("/api/tool")
async def call_tool(req: ToolRequest):
    server = registry.get_server(req.server)
    if server is None:
        return _error(f"MCP-сервер '{req.server}' не найден в реестре.", 404)
    try:
        payload = await mcp_client.call_tool(server, req.tool, req.arguments)
    except mcp_client.McpError as exc:
        return _error(str(exc), 502)
    return {"tool": req.tool, "result": payload}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    server = registry.get_server(req.server)
    if server is None:
        return _error(f"MCP-сервер '{req.server}' не найден в реестре.", 404)
    if not server.is_available():
        return _error(
            f"Векторная база сервера '{req.server}' не найдена. "
            f"Сначала выполните индексацию (run_index_*.cmd).",
            409,
        )

    try:
        result = await rag.answer_question(
            server,
            req.question,
            limit=req.limit,
            use_graph=req.use_graph,
            lm_base_url=req.lm_base_url,
            lm_model=req.lm_model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except mcp_client.McpError as exc:
        return _error(str(exc), 502)
    except lmstudio.LmStudioError as exc:
        return _error(str(exc), 502)

    return result


# Статика (UI). Монтируется последней, чтобы не перехватывать /api/*.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
