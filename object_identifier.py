"""Разбор идентификаторов объектов 1С (короткое и полное имя)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ObjectIdentifier:
    """Нормализованный идентификатор объекта метаданных."""

    object_name: str
    object_type: Optional[str] = None
    node_id: Optional[str] = None

    @property
    def qualified_name(self) -> str:
        if self.object_type:
            return f"{self.object_type}.{self.object_name}"
        return self.object_name


def parse_object_identifier(name: str) -> ObjectIdentifier:
    """
    Разбирает имя объекта в форматах:
    - Documents.НачислениеЗарплаты
    - metadata:Documents:НачислениеЗарплаты
    - НачислениеЗарплаты (короткое имя)
    """
    raw = (name or "").strip()
    if not raw:
        raise ValueError("object_name не может быть пустым")

    if raw.startswith("metadata:"):
        parts = raw.split(":", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            return ObjectIdentifier(
                object_name=parts[2],
                object_type=parts[1],
                node_id=raw,
            )

    if "." in raw:
        object_type, object_name = raw.split(".", 1)
        if object_type and object_name:
            return ObjectIdentifier(
                object_name=object_name,
                object_type=object_type,
                node_id=f"metadata:{object_type}:{object_name}",
            )

    return ObjectIdentifier(object_name=raw)
