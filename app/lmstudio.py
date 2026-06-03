"""
Клиент к LM Studio (и любому OpenAI-совместимому серверу) для чат-ответа.

Используется только для генерации финального ответа модели. Эмбеддинги для
поиска вычисляет сам MCP-сервер по настройкам своего профиля — это разные модели
и разные endpoint'ы, поэтому здесь работаем исключительно с chat/completions.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import httpx

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_TIMEOUT = 180.0


class LmStudioError(RuntimeError):
    """Ошибка обращения к LM Studio."""


def _completions_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def _models_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/models"


async def list_models(base_url: str = DEFAULT_BASE_URL) -> List[str]:
    """Список загруженных в LM Studio моделей (для выбора чат-модели в UI)."""
    url = _models_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise LmStudioError(f"LM Studio недоступен по адресу {base_url}: {exc}") from exc

    models = data.get("data", []) if isinstance(data, dict) else []
    return [m.get("id", "") for m in models if m.get("id")]


async def chat(
    messages: List[Dict[str, str]],
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> str:
    """Запрашивает ответ чат-модели. Возвращает текст ответа."""
    payload: Dict[str, object] = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if model:
        payload["model"] = model

    url = _completions_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        raise LmStudioError(f"LM Studio вернул ошибку: {detail}") from exc
    except httpx.HTTPError as exc:
        raise LmStudioError(f"LM Studio недоступен по адресу {base_url}: {exc}") from exc

    choices = data.get("choices", []) if isinstance(data, dict) else []
    if not choices:
        raise LmStudioError("LM Studio вернул пустой ответ (нет choices).")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if not content:
        raise LmStudioError("LM Studio вернул ответ без содержимого.")
    return content
