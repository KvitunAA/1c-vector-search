"""
RAG-оркестрация: поиск через MCP -> компактный контекст -> ответ LM Studio.

Шаги:
  1. Семантический поиск кода / метаданных / форм через инструменты MCP-сервера.
  2. При намёке на анализ влияния — дополнительно граф зависимостей и ссылок
     по наиболее релевантному объекту метаданных.
  3. Сборка компактного контекста с ограничением по длине (чтобы не переполнять
     контекст чат-модели большими фрагментами BSL).
  4. Формирование сообщений для LM Studio и возврат ответа вместе с источниками.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import lmstudio, mcp_client
from .registry import McpServer

# Ограничения, чтобы не переполнять контекстное окно чат-модели.
MAX_CODE_SNIPPETS = 6
MAX_METADATA_ITEMS = 8
MAX_FORM_ITEMS = 5
CODE_SNIPPET_CHARS = 1200
CONTEXT_CHAR_BUDGET = 14000

# Слова-маркеры запроса на анализ влияния / связей объекта.
_GRAPH_INTENT_MARKERS = (
    "завис",
    "ссыл",
    "влия",
    "использу",
    "связ",
    "затрон",
    "impact",
    "depend",
    "where used",
)

SYSTEM_PROMPT = (
    "Ты — ассистент-аналитик по конфигурации 1С. Отвечай на русском языке. "
    "Используй ТОЛЬКО предоставленный ниже контекст из векторной базы и графа "
    "зависимостей конфигурации. Если данных в контексте недостаточно для ответа, "
    "прямо сообщи об этом и не выдумывай. Указывай конкретные объекты метаданных, "
    "модули и пути к файлам, на которые опираешься. Фрагменты кода оформляй в "
    "блоках ```bsl```."
)


def _wants_graph(question: str) -> bool:
    lowered = question.lower()
    return any(marker in lowered for marker in _GRAPH_INTENT_MARKERS)


def _safe_results(payload: Any, key: str) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _truncate(text: str, limit: int) -> str:
    if text and len(text) > limit:
        return text[: limit - 3] + "..."
    return text or ""


def _pick_graph_object(metadata_results: List[Dict[str, Any]]) -> Optional[str]:
    for item in metadata_results:
        name = (item.get("name") or "").strip()
        if name:
            return name
    return None


def _build_context(
    code: List[Dict[str, Any]],
    metadata: List[Dict[str, Any]],
    forms: List[Dict[str, Any]],
    graph: Optional[Dict[str, Any]],
) -> str:
    parts: List[str] = []

    if metadata:
        lines = ["== ОБЪЕКТЫ МЕТАДАННЫХ =="]
        for item in metadata[:MAX_METADATA_ITEMS]:
            synonym = item.get("synonym") or ""
            description = item.get("description") or ""
            head = f"- {item.get('type', '')}.{item.get('name', '')}"
            if synonym:
                head += f" ({synonym})"
            lines.append(head)
            if description:
                lines.append(f"  Описание: {_truncate(description, 300)}")
            if item.get("file_path"):
                lines.append(f"  Файл: {item['file_path']}")
        parts.append("\n".join(lines))

    if code:
        lines = ["== ФРАГМЕНТЫ КОДА =="]
        for item in code[:MAX_CODE_SNIPPETS]:
            obj = item.get("object", "")
            module = item.get("module", "")
            method = item.get("method", "")
            signature = item.get("signature", "")
            header = f"- {obj} / {module} / {method}".rstrip(" /")
            lines.append(header)
            if signature:
                lines.append(f"  Сигнатура: {signature}")
            if item.get("file_path"):
                lines.append(f"  Файл: {item['file_path']}")
            snippet = _truncate(item.get("code", ""), CODE_SNIPPET_CHARS)
            if snippet:
                lines.append("  ```bsl")
                lines.append(snippet)
                lines.append("  ```")
        parts.append("\n".join(lines))

    if forms:
        lines = ["== ФОРМЫ =="]
        for item in forms[:MAX_FORM_ITEMS]:
            lines.append(
                f"- {item.get('form_name', '')} ({item.get('object', '')}), "
                f"элементов: {item.get('elements_count', 0)}"
            )
        parts.append("\n".join(lines))

    if graph and (graph.get("dependencies") or graph.get("references")):
        lines = [f"== ГРАФ ЗАВИСИМОСТЕЙ для '{graph.get('object', '')}' =="]
        deps = graph.get("dependencies") or []
        refs = graph.get("references") or []
        if deps:
            lines.append("На объект ссылаются (зависят от него):")
            for dep in deps[:25]:
                lines.append(f"  - {dep.get('object', '')} [{dep.get('edge_type', '')}]")
        if refs:
            lines.append("Объект ссылается на (использует):")
            for ref in refs[:25]:
                lines.append(f"  - {ref.get('object', '')} [{ref.get('edge_type', '')}]")
        parts.append("\n".join(lines))

    context = "\n\n".join(parts)
    return _truncate(context, CONTEXT_CHAR_BUDGET)


async def _gather_sources(
    server: McpServer, question: str, limit: int, use_graph: bool
) -> Dict[str, Any]:
    """Вызывает поисковые (и, при необходимости, графовые) инструменты MCP."""
    base_calls: List[Tuple[str, Dict[str, Any]]] = [
        ("search_1c_code", {"query": question, "limit": limit}),
        ("search_1c_metadata", {"query": question, "limit": limit}),
        ("search_1c_forms", {"query": question, "limit": max(3, limit // 2)}),
    ]

    code_payload, metadata_payload, forms_payload = await mcp_client.call_batch(
        server, base_calls
    )

    code = _safe_results(code_payload, "results")
    metadata = _safe_results(metadata_payload, "results")
    forms = _safe_results(forms_payload, "results")

    graph: Optional[Dict[str, Any]] = None
    if use_graph:
        graph_object = _pick_graph_object(metadata)
        if graph_object:
            dep_payload, ref_payload = await mcp_client.call_batch(
                server,
                [
                    ("graph_dependencies", {"object_name": graph_object, "limit": 100}),
                    ("graph_references", {"object_name": graph_object, "limit": 100}),
                ],
            )
            graph = {
                "object": graph_object,
                "dependencies": _safe_results(dep_payload, "dependencies"),
                "references": _safe_results(ref_payload, "references"),
            }

    return {"code": code, "metadata": metadata, "forms": forms, "graph": graph}


async def answer_question(
    server: McpServer,
    question: str,
    *,
    limit: int = 5,
    use_graph: Optional[bool] = None,
    lm_base_url: str = lmstudio.DEFAULT_BASE_URL,
    lm_model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> Dict[str, Any]:
    """Полный цикл: поиск -> контекст -> ответ LM Studio -> ответ + источники."""
    if use_graph is None:
        use_graph = _wants_graph(question)

    sources = await _gather_sources(server, question, limit, use_graph)

    context = _build_context(
        sources["code"], sources["metadata"], sources["forms"], sources["graph"]
    )

    has_context = bool(context.strip())
    if not has_context:
        context = "(По запросу ничего не найдено в векторной базе и графе.)"

    user_content = (
        f"Вопрос пользователя:\n{question}\n\n"
        f"Контекст из конфигурации 1С (сервер '{server.name}'):\n{context}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    answer = await lmstudio.chat(
        messages,
        base_url=lm_base_url,
        model=lm_model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    return {
        "answer": answer,
        "sources": {
            "code": sources["code"],
            "metadata": sources["metadata"],
            "forms": sources["forms"],
        },
        "graph": sources["graph"],
        "used_graph": use_graph,
        "has_context": has_context,
    }
