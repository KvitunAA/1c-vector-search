"""
Поиск по исходникам 1С (grep) для точного поиска вызовов методов.
Используется в find_1c_method_usage вместо семантического поиска.
"""
import re
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional

from config import Config

logger = logging.getLogger(__name__)


class GrepResults(list):
    """Результаты grep со сведениями об обходе.

    Наследуется от list, поэтому для вызывающего кода ничего не меняется:
    len(), итерация, сравнение со списком работают как раньше. Дополнительные
    атрибуты нужны, чтобы отличить «совпадений действительно нет» от
    «обход прерван по времени и результат неполный».
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.truncated = False
        self.files_scanned = 0
        self.elapsed_sec = 0.0


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
    time_budget_sec: Optional[float] = None,
) -> List[Dict]:
    """
    Ищет вхождения имени метода в BSL-файлах конфигурации.

    Обход прекращается, когда набрано `limit` совпадений ЛИБО когда исчерпан
    бюджет времени. Без бюджета редкое (или отсутствующее) имя означало полный
    обход выгрузки: на ERP это минуты, за которые MCP-клиент успевает отвалиться
    по таймауту, а процесс на сервере продолжает работать.

    Args:
        method_name: Имя процедуры или функции для поиска
        config_path: Путь к конфигурации (по умолчанию Config.CONFIG_PATH)
        limit: Максимальное количество результатов
        time_budget_sec: Предел времени обхода в секундах.
            None — взять Config.GREP_TIME_BUDGET_SEC, 0 — без ограничения.

    Returns:
        GrepResults (подкласс list) со словарями: file_path, line_number,
        line_content, object_type, object_name, module_name, in_method.
        Атрибут `truncated` показывает, что обход прерван и результат неполный.
    """
    results = GrepResults()

    config_path = Path(config_path or Config.CONFIG_PATH)
    if not config_path.exists():
        logger.warning(f"Путь к конфигурации не найден: {config_path}")
        return results

    if time_budget_sec is None:
        time_budget_sec = getattr(Config, "GREP_TIME_BUDGET_SEC", 20.0)

    pattern = re.compile(
        r"\b" + re.escape(method_name) + r"\b",
        re.IGNORECASE
    )

    method_header_re = re.compile(
        r"^\s*(?:Процедура|Функция)\s+(\w+)\s*\(",
        re.IGNORECASE
    )

    started = time.monotonic()
    deadline = started + time_budget_sec if time_budget_sec > 0 else None

    for bsl_file in config_path.rglob("*.bsl"):
        if deadline is not None and time.monotonic() > deadline:
            results.truncated = True
            logger.warning(
                f"Поиск '{method_name}' прерван по времени ({time_budget_sec} с): "
                f"просмотрено файлов {results.files_scanned}, найдено {len(results)}. "
                f"Результат неполный."
            )
            break

        try:
            content = bsl_file.read_text(encoding="utf-8-sig")
        except Exception as e:
            logger.debug(f"Ошибка чтения {bsl_file}: {e}")
            continue

        results.files_scanned += 1

        # Быстрая отсечка: в подавляющем большинстве файлов имени нет, и разбирать
        # их построчно незачем. Один поиск по всему тексту дешевле, чем регулярка
        # на каждую строку.
        if not pattern.search(content):
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
                    results.elapsed_sec = time.monotonic() - started
                    return results

    results.elapsed_sec = time.monotonic() - started
    return results
