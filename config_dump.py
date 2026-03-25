"""
Сведения о выгрузке конфигурации или расширения 1С по корневому Configuration.xml.

Выгрузка расширения (DumpConfigToFiles с -Extension) использует тот же иерархический
формат файлов, что и основная конфигурация: в корне лежит Configuration.xml.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict


def _local_name(tag: str) -> str:
    if not tag:
        return ""
    return tag.split("}")[-1] if "}" in tag else tag


def _name_from_properties(properties_elem: Any) -> str:
    """Ищет дочерний Name внутри Properties."""
    for child in properties_elem:
        if _local_name(child.tag) == "Name" and child.text and str(child.text).strip():
            return str(child.text).strip()
    return ""


def read_configuration_dump_info(config_root: Path) -> Dict[str, Any]:
    """
    Читает корневой Configuration.xml и определяет тип выгрузки и имя.

    Returns:
        Словарь с ключами:
        - valid: bool — удалось ли прочитать XML
        - is_extension: bool — признаки расширения конфигурации
        - configuration_name: str — имя из Properties/Name, если найдено
        - root_element: str — локальное имя корневого элемента
        - config_root: str — абсолютный путь к корню выгрузки
        - error: str — текст ошибки разбора (при необходимости)
    """
    root_path = Path(config_root)
    cfg_xml = root_path / "Configuration.xml"
    result: Dict[str, Any] = {
        "valid": False,
        "config_root": str(root_path.resolve()),
        "is_extension": False,
        "configuration_name": "",
        "root_element": "",
    }
    if not cfg_xml.is_file():
        return result
    try:
        tree = ET.parse(cfg_xml)
        root = tree.getroot()
    except ET.ParseError as exc:
        result["error"] = str(exc)
        return result

    result["valid"] = True
    result["root_element"] = _local_name(root.tag)

    # Вариант 1: MetaDataObject → Configuration | ConfigurationExtension
    for child in root:
        ln = _local_name(child.tag)
        if ln == "ConfigurationExtension":
            result["is_extension"] = True
            for sub in child:
                if _local_name(sub.tag) == "Properties":
                    name = _name_from_properties(sub)
                    if name:
                        result["configuration_name"] = name
                    break
            break
        if ln == "Configuration":
            for sub in child:
                if _local_name(sub.tag) == "Properties":
                    name = _name_from_properties(sub)
                    if name:
                        result["configuration_name"] = name
                    break
            break

    # Вариант 2: корень — сразу Configuration / ConfigurationExtension (редкие выгрузки)
    root_ln = _local_name(root.tag)
    if root_ln == "ConfigurationExtension":
        result["is_extension"] = True
    if root_ln in ("Configuration", "ConfigurationExtension"):
        for sub in root:
            if _local_name(sub.tag) == "Properties":
                name = _name_from_properties(sub)
                if name:
                    result["configuration_name"] = name
                break

    # Доп. эвристика: в XML расширения иногда встречается узел с локальным именем ConfigurationExtension
    if not result["is_extension"]:
        for elem in root.iter():
            if _local_name(elem.tag) == "ConfigurationExtension":
                result["is_extension"] = True
                break

    return result
