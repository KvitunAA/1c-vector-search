"""
Поиск по исходникам 1С (grep) для точного поиска вызовов методов.
Используется в find_1c_method_usage вместо семантического поиска.
"""
import re
import logging
from pathlib import Path
from typing import List, Dict, Optional

from config import Config

logger = logging.getLogger(__name__)


def _extract_object_info_from_path(file_path: Path, config_path: Path) -> Dict[str, str]:
    """Извлекает object_type, object_name, module_name из пути к файлу."""
    try:
        rel = file_path.relative_to(config_path)
        parts = rel.parts
        object_type = parts[0] if len(parts) > 0 else "Unknown"
        object_name = parts[1] if len(parts) > 1 else file_path.stem
        module_name = file_path.stem
        return {
            "object_type": object_type,
            "object_name": object_name,
            "module_name": module_name,
        }
    except ValueError:
        return {"object_type": "Unknown", "object_name": file_path.stem, "module_name": file_path.stem}


def _find_enclosing_method(content: str, line_number: int) -> Optional[str]:
    """Находит имя процедуры/функции, содержащей указанную строку."""
    lines = content.split("\n")
    if line_number < 1 or line_number > len(lines):
        return None
    pattern = re.compile(
        r"(?:&[^\n]*\n)*\s*(?:Процедура|Функция)\s+(\w+)\s*\(",
        re.IGNORECASE
    )
    current_method = None
    for i, line in enumerate(lines[:line_number], start=1):
        match = pattern.search(line)
        if match:
            current_method = match.group(1)
    return current_method


def grep_method_usage(
    method_name: str,
    config_path: Optional[Path] = None,
    limit: int = 50,
) -> List[Dict]:
    """
    Ищет вхождения имени метода в BSL-файлах конфигурации.

    Args:
        method_name: Имя процедуры или функции для поиска
        config_path: Путь к конфигурации (по умолчанию Config.CONFIG_PATH)
        limit: Максимальное количество результатов

    Returns:
        Список словарей с file_path, line_number, line_content, object_type,
        object_name, module_name, in_method
    """
    config_path = Path(config_path or Config.CONFIG_PATH)
    if not config_path.exists():
        logger.warning(f"Путь к конфигурации не найден: {config_path}")
        return []

    pattern = re.compile(
        r"\b" + re.escape(method_name) + r"\b",
        re.IGNORECASE
    )
    results = []

    method_header_re = re.compile(
        r"^\s*(?:Процедура|Функция)\s+(\w+)\s*\(",
        re.IGNORECASE
    )

    for bsl_file in config_path.rglob("*.bsl"):
        try:
            content = bsl_file.read_text(encoding="utf-8-sig")
        except Exception as e:
            logger.debug(f"Ошибка чтения {bsl_file}: {e}")
            continue

        obj_info = _extract_object_info_from_path(bsl_file, config_path)
        current_method = None

        for line_num, line in enumerate(content.split("\n"), start=1):
            header_match = method_header_re.search(line)
            if header_match:
                current_method = header_match.group(1)

            if pattern.search(line):
                results.append({
                    "file_path": str(bsl_file),
                    "line_number": line_num,
                    "line_content": line.strip()[:200],
                    "object_type": obj_info["object_type"],
                    "object_name": obj_info["object_name"],
                    "module_name": obj_info["module_name"],
                    "in_method": current_method or "",
                })
                if len(results) >= limit:
                    return results

    return results


def grep_method_usage_many(
    method_name: str,
    config_paths: Optional[List[Path]] = None,
    limit: int = 50,
) -> List[Dict]:
    """Ищет вхождения метода во всех указанных выгрузках конфигурации."""
    paths = config_paths or [Path(p) for p in Config.get_all_config_paths() if p]
    if not paths:
        single = Config.CONFIG_PATH
        if single:
            paths = [Path(single)]

    combined: List[Dict] = []
    per_path_limit = max(limit, 1)

    for config_path in paths:
        if len(combined) >= limit:
            break
        remaining = limit - len(combined)
        for item in grep_method_usage(method_name, config_path=config_path, limit=min(per_path_limit, remaining)):
            enriched = dict(item)
            enriched["config_root"] = str(config_path)
            try:
                from config_dump import read_configuration_dump_info

                dump = read_configuration_dump_info(config_path)
                if dump.get("valid"):
                    enriched["index_source"] = "extension" if dump.get("is_extension") else "main"
                    enriched["configuration_name"] = dump.get("configuration_name", "")
            except Exception:
                pass
            combined.append(enriched)

    return combined[:limit]
