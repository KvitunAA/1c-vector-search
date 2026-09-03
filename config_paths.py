"""
Сбор путей к выгрузкам основной конфигурации и расширений из профиля.

Поддерживаются:
- CONFIG_PATH — основная конфигурация;
- EXTENSION_CONFIG_PATH — одно расширение (обратная совместимость);
- EXTENSION_CONFIG_PATHS — явный список путей через ``;`` или перевод строки;
- EXTENSIONS_ROOT — каталог, в котором каждая подпапка с Configuration.xml — расширение.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, List

from config_dump import read_configuration_dump_info

_PATH_SEPARATORS = re.compile(r"[;\n\r]+")


def split_config_paths(raw: str) -> List[str]:
    """Разбивает строку с путями на список уникальных существующих каталогов."""
    if not raw or not str(raw).strip():
        return []
    parts = [part.strip().strip('"').strip("'") for part in _PATH_SEPARATORS.split(str(raw))]
    return _dedupe_paths([part for part in parts if part])


def _dedupe_paths(paths: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for path in paths:
        key = str(Path(path).resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(str(Path(path)))
    return result


def discover_extensions_in_root(extensions_root: str) -> List[str]:
    """
    Находит выгрузки расширений в подкаталогах extensions_root.

    Берутся только непосредственные дочерние папки, где есть Configuration.xml.
    """
    root = Path(extensions_root)
    if not root.is_dir():
        return []

    discovered: List[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if (child / "Configuration.xml").is_file():
            discovered.append(str(child.resolve()))
    return discovered


def collect_extension_config_paths(
    extension_config_path: str = "",
    extension_config_paths: str = "",
    extensions_root: str = "",
    *,
    exclude_paths: Iterable[str] | None = None,
) -> List[str]:
    """Собирает все пути расширений из переменных профиля."""
    paths: List[str] = []
    single = (extension_config_path or "").strip()
    if single:
        paths.append(single)
    paths.extend(split_config_paths(extension_config_paths or ""))
    paths.extend(discover_extensions_in_root((extensions_root or "").strip()))

    exclude = {str(Path(p).resolve()).lower() for p in (exclude_paths or []) if p}
    filtered: List[str] = []
    for path in _dedupe_paths(paths):
        resolved = str(Path(path).resolve()).lower()
        if resolved in exclude:
            continue
        filtered.append(path)
    return filtered


def validate_config_root(config_root: str | Path, *, label: str = "выгрузка") -> Path:
    """Проверяет, что каталог существует и содержит Configuration.xml."""
    root = Path(config_root)
    if not root.is_dir():
        raise FileNotFoundError(f"{label}: каталог не найден: {root}")
    if not (root / "Configuration.xml").is_file():
        raise FileNotFoundError(f"{label}: не найден Configuration.xml в {root}")
    return root.resolve()


def make_source_id(config_root: Path, source_kind: str) -> str:
    """Стабильный идентификатор источника для item_id и метаданных."""
    dump = read_configuration_dump_info(config_root)
    folder_id = re.sub(r"[^\w\-]+", "_", config_root.name).strip("_") or "source"
    if source_kind == "main":
        return "main"

    config_name = (dump.get("configuration_name") or "").strip()
    if config_name:
        name_id = re.sub(r"[^\w\-]+", "_", config_name).strip("_")
        if name_id and name_id.lower() != folder_id.lower():
            return f"ext_{name_id}_{folder_id}"[:64]
    return f"ext_{folder_id}"[:64]


def resolve_index_source(config_root: Path, source_kind: str) -> dict:
    """Метаданные источника для индексации и поиска."""
    dump = read_configuration_dump_info(config_root)
    configuration_name = (dump.get("configuration_name") or config_root.name).strip()
    return {
        "index_source": source_kind,
        "source_id": make_source_id(config_root, source_kind),
        "configuration_name": configuration_name,
        "config_root": str(config_root.resolve()),
        "is_extension": bool(dump.get("is_extension")) if dump.get("valid") else source_kind == "extension",
    }


def build_unified_index_plan(main_root: Path, extension_roots: List[Path]) -> List[dict]:
    """План шагов unified-индексации."""
    plan = [{"kind": "main", "path": main_root, "clear": True}]
    for ext_root in extension_roots:
        plan.append({"kind": "extension", "path": ext_root, "clear": False})
    return plan
