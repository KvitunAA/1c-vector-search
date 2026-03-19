"""
Парсер конфигурации 1С для извлечения кода и метаданных
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class BSLParser:
    """Парсер BSL модулей"""

    _DIRECTIVE_RE = re.compile(
        r"&(НаКлиенте|НаСервере|НаСервереБезКонтекста|НаКлиентеНаСервереБезКонтекста"
        r"|AtClient|AtServer|AtServerNoContext|AtClientAtServerNoContext)",
        re.IGNORECASE,
    )

    _METHOD_RE = re.compile(
        r"(?P<directives>(?:&[^\n]*\n)*)"
        r"\s*(?P<type>Процедура|Функция|Procedure|Function)"
        r"\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)"
        r"\s*(?P<export>Экспорт|Export)?"
        r"\s*\n(?P<body>.*?)"
        r"\n\s*(?:Конец(?:Процедуры|Функции)|EndProcedure|EndFunction)",
        re.IGNORECASE | re.DOTALL,
    )

    _VAR_RE = re.compile(
        r"^\s*Перем\s+(\w+)", re.IGNORECASE | re.MULTILINE
    )

    @classmethod
    def parse_module(cls, file_path: Path) -> List[Dict]:
        """Парсинг BSL модуля на процедуры/функции с директивами компиляции."""
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Ошибка чтения файла {file_path}: {e}")
            return []

        module_vars = cls._VAR_RE.findall(content)

        content_clean = re.sub(
            r'#(?:Если|ИначеЕсли|Иначе|КонецЕсли|If|ElsIf|Else|EndIf)[^\n]*\n',
            '\n', content, flags=re.IGNORECASE,
        )

        chunks = []
        for match in cls._METHOD_RE.finditer(content_clean):
            try:
                method_type_raw = match.group("type")
                if method_type_raw is None:
                    logger.warning(
                        "Пропуск совпадения: группа 'type' пуста в %s, фрагмент: %s",
                        file_path,
                        repr(match.group(0)[:200]) if match.group(0) else "пусто",
                    )
                    continue

                directives_block = match.group("directives") or ""
                method_type = method_type_raw.capitalize()
                if method_type in ("Procedure", "Function"):
                    method_type = "Процедура" if method_type == "Procedure" else "Функция"
                method_name = match.group("name")
                if method_name is None:
                    logger.warning(
                        "Пропуск совпадения: группа 'name' пуста в %s, фрагмент: %s",
                        file_path,
                        repr(match.group(0)[:200]) if match.group(0) else "пусто",
                    )
                    continue
                params = (match.group("params") or "").strip()
                is_export = match.group("export") is not None
                body = match.group("body") or ""

                directive = ""
                dir_match = cls._DIRECTIVE_RE.search(directives_block)
                if dir_match:
                    directive = dir_match.group(1)

                start_pos = match.start()
                lines_before = content_clean[:start_pos].split('\n')
                comments = []
                for line in reversed(lines_before[-10:]):
                    line = line.strip()
                    if line.startswith('//'):
                        comments.insert(0, line[2:].strip())
                    elif line and not line.startswith('&'):
                        break

                end_keyword = "КонецПроцедуры" if "процедур" in method_type.lower() else "КонецФункции"
                full_code = match.group(0) + '\n' + end_keyword

                chunks.append({
                    "method_type": method_type,
                    "method_name": method_name,
                    "params": params,
                    "signature": f"{method_type} {method_name}({params})",
                    "is_export": is_export,
                    "directive": directive,
                    "code": full_code,
                    "body": body,
                    "comments": comments,
                    "file_path": str(file_path),
                })
            except (AttributeError, TypeError) as e:
                logger.warning(
                    "Пропуск совпадения при парсинге %s: %s, фрагмент: %s",
                    file_path,
                    e,
                    repr(match.group(0)[:200]) if match.group(0) else "пусто",
                )
                continue

        if not chunks and content.strip():
            chunks.append({
                "method_type": "Module",
                "method_name": file_path.stem,
                "params": "",
                "signature": f"Модуль {file_path.stem}",
                "is_export": False,
                "directive": "",
                "code": content,
                "body": content,
                "comments": [],
                "file_path": str(file_path),
                "module_variables": module_vars,
            })

        if module_vars and chunks and chunks[0]["method_type"] != "Module":
            chunks[0]["module_variables"] = module_vars

        return chunks

    METADATA_COLLECTION_MAP = {
        "Документы": "Documents",
        "Справочники": "Catalogs",
        "РегистрыСведений": "InformationRegisters",
        "РегистрыНакопления": "AccumulationRegisters",
        "РегистрыБухгалтерии": "AccountingRegisters",
        "ПланыСчетов": "ChartsOfAccounts",
        "Перечисления": "Enums",
        "ОбщиеМодули": "CommonModules",
        "Обработки": "DataProcessors",
        "Отчеты": "Reports",
    }

    @staticmethod
    def extract_metadata_references_from_code(code: str) -> List[Tuple[str, str]]:
        """Извлечение ссылок на объекты метаданных из BSL кода."""
        refs = []
        pattern = re.compile(
            r"\b(Документы|Справочники|РегистрыСведений|РегистрыНакопления|"
            r"РегистрыБухгалтерии|ПланыСчетов|Перечисления|ОбщиеМодули|"
            r"Обработки|Отчеты)\.(\w+)",
            re.IGNORECASE,
        )
        seen = set()
        for match in pattern.finditer(code):
            collection_ru = match.group(1)
            obj_name = match.group(2)
            obj_type = BSLParser.METADATA_COLLECTION_MAP.get(collection_ru)
            if obj_type and (obj_type, obj_name) not in seen:
                seen.add((obj_type, obj_name))
                refs.append((obj_type, obj_name))
        return refs

    @staticmethod
    def extract_module_info(file_path: Path) -> Dict:
        """Извлечение общей информации о модуле"""
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
            directives = re.findall(r'&([^\n]+)', content)
            variables_section = ""
            var_match = re.search(r'#Область\s+ОбластьПеременных(.*?)#КонецОбласти', content, re.DOTALL | re.IGNORECASE)
            if var_match:
                variables_section = var_match.group(1).strip()
            return {
                "file_path": str(file_path),
                "directives": directives,
                "has_variables": bool(variables_section),
                "size": len(content),
                "lines": content.count('\n')
            }
        except Exception as e:
            logger.error(f"Ошибка извлечения информации из модуля {file_path}: {e}")
            return {}


class MetadataParser:
    """Парсер XML метаданных 1С"""

    NS = {'v8': 'http://v8.1c.ru/8.3/MDClasses'}

    @staticmethod
    def parse_object_metadata(xml_path: Path) -> Optional[Dict]:
        """Парсинг XML файла объекта метаданных"""
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            object_type = root.tag.split('}')[-1] if '}' in root.tag else root.tag

            name_elem = root.find('.//v8:name', MetadataParser.NS)
            if name_elem is None:
                name_elem = root.find('.//{http://v8.1c.ru/8.1/data/core}name')

            synonym_elem = root.find('.//v8:synonym', MetadataParser.NS)
            comment_elem = root.find('.//v8:comment', MetadataParser.NS)

            name = name_elem.text if name_elem is not None and name_elem.text else xml_path.stem
            synonym = ''
            if synonym_elem is not None:
                rep_elem = synonym_elem.find('.//v8:item/v8:presentation', MetadataParser.NS)
                if rep_elem is not None:
                    synonym = rep_elem.text or ''

            comment = comment_elem.text if comment_elem is not None else ''

            metadata = {
                "name": name,
                "type": object_type,
                "synonym": synonym,
                "comment": comment,
                "file_path": str(xml_path)
            }

            attributes = []
            for attr_elem in root.findall('.//v8:attributes', MetadataParser.NS):
                attr_name = attr_elem.find('v8:name', MetadataParser.NS)
                attr_type = attr_elem.find('.//v8:type', MetadataParser.NS)
                if attr_name is not None:
                    attributes.append({
                        "name": attr_name.text,
                        "type": MetadataParser._extract_type(attr_type) if attr_type is not None else "Неопределено"
                    })

            metadata["attributes"] = attributes
            metadata["attributes_count"] = len(attributes)

            tabular_sections = []
            for tab_elem in root.findall('.//v8:tabularSections', MetadataParser.NS):
                tab_name = tab_elem.find('v8:name', MetadataParser.NS)
                if tab_name is not None:
                    ts_attrs = []
                    for ts_attr in tab_elem.findall('.//v8:attributes', MetadataParser.NS):
                        ts_attr_name = ts_attr.find('v8:name', MetadataParser.NS)
                        ts_attr_type = ts_attr.find('.//v8:type', MetadataParser.NS)
                        if ts_attr_name is not None:
                            ts_attrs.append({
                                "name": ts_attr_name.text,
                                "type": MetadataParser._extract_type(ts_attr_type) if ts_attr_type is not None else "Неопределено"
                            })
                    tabular_sections.append({
                        "name": tab_name.text,
                        "attributes": ts_attrs,
                    })

            metadata["tabular_sections"] = tabular_sections

            dimensions = []
            for dim_elem in root.findall('.//v8:dimensions', MetadataParser.NS):
                dim_name = dim_elem.find('v8:name', MetadataParser.NS)
                dim_type = dim_elem.find('.//v8:type', MetadataParser.NS)
                if dim_name is not None:
                    dimensions.append({
                        "name": dim_name.text,
                        "type": MetadataParser._extract_type(dim_type) if dim_type is not None else "Неопределено"
                    })
            if dimensions:
                metadata["dimensions"] = dimensions

            resources = []
            for res_elem in root.findall('.//v8:resources', MetadataParser.NS):
                res_name = res_elem.find('v8:name', MetadataParser.NS)
                res_type = res_elem.find('.//v8:type', MetadataParser.NS)
                if res_name is not None:
                    resources.append({
                        "name": res_name.text,
                        "type": MetadataParser._extract_type(res_type) if res_type is not None else "Неопределено"
                    })
            if resources:
                metadata["resources"] = resources

            commands = []
            for cmd_elem in root.findall('.//v8:commands', MetadataParser.NS):
                cmd_name = cmd_elem.find('v8:name', MetadataParser.NS)
                if cmd_name is not None:
                    commands.append(cmd_name.text)
            if commands:
                metadata["commands"] = commands

            parent_dir = xml_path.parent
            has_modules = []
            module_files = {
                "МодульОбъекта.bsl": "ObjectModule",
                "МодульМенеджера.bsl": "ManagerModule",
                "МодульНабораЗаписей.bsl": "RecordSetModule",
                "МодульКоманды.bsl": "CommandModule",
                "Module.bsl": "Module"
            }
            for module_file, module_type in module_files.items():
                if (parent_dir / module_file).exists():
                    has_modules.append(module_type)

            metadata["has_modules"] = has_modules

            return metadata

        except Exception as e:
            logger.error(f"Ошибка парсинга XML {xml_path}: {e}")
            return None

    @staticmethod
    def _extract_type(type_elem) -> str:
        """Извлечение типа из XML элемента"""
        try:
            type_def = type_elem.find('.//v8:TypeId', MetadataParser.NS)
            if type_def is not None and type_def.text:
                return type_def.text
            type_str = type_elem.find('.//v8:string', MetadataParser.NS)
            if type_str is not None:
                return "Строка"
            type_num = type_elem.find('.//v8:number', MetadataParser.NS)
            if type_num is not None:
                return "Число"
            type_date = type_elem.find('.//v8:date', MetadataParser.NS)
            if type_date is not None:
                return "Дата"
            type_bool = type_elem.find('.//v8:boolean', MetadataParser.NS)
            if type_bool is not None:
                return "Булево"
            return "Составной тип"
        except Exception:
            return "Неопределено"

    @staticmethod
    def parse_form_metadata(xml_path: Path) -> Optional[Dict]:
        """Парсинг формы 1С"""
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            elements = []
            for item in root.findall('.//{http://v8.1c.ru/8.3/xcf/logform}Item'):
                name_attr = item.get('name')
                if name_attr:
                    elements.append(name_attr)
            form_name = xml_path.stem
            if form_name == "Form":
                form_name = xml_path.parent.parent.name if xml_path.parent.name == "Ext" else xml_path.parent.name
            return {
                "file_path": str(xml_path),
                "form_name": form_name,
                "elements": elements,
                "elements_count": len(elements)
            }
        except Exception as e:
            logger.error(f"Ошибка парсинга формы {xml_path}: {e}")
            return None


class ConfigurationScanner:
    """Сканер структуры конфигурации 1С"""

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.bsl_parser = BSLParser()
        self.metadata_parser = MetadataParser()

    def scan_all_modules(self) -> List[Tuple[Path, str, List[Dict]]]:
        """Сканирование всех BSL модулей в конфигурации"""
        results = []
        for bsl_file in self.config_path.rglob("*.bsl"):
            try:
                relative_path = bsl_file.relative_to(self.config_path)
                parts = relative_path.parts
                object_type = parts[0] if len(parts) > 0 else "Unknown"
                object_name = parts[1] if len(parts) > 1 else bsl_file.stem
                methods = self.bsl_parser.parse_module(bsl_file)
                if methods:
                    results.append((bsl_file, f"{object_type}.{object_name}", methods))
                    logger.info(f"Найдено {len(methods)} методов в {relative_path}")
            except Exception as e:
                logger.error(
                    "Ошибка при сканировании модуля %s: %s",
                    bsl_file,
                    e,
                    exc_info=True,
                )
        return results

    def scan_all_metadata(self) -> List[Dict]:
        """Сканирование всех XML файлов метаданных."""
        results = []
        seen_names = set()
        metadata_dirs = [
            "Catalogs", "Documents", "InformationRegisters",
            "AccumulationRegisters", "AccountingRegisters",
            "DataProcessors", "Reports", "CommonModules",
            "Enums", "ChartsOfAccounts"
        ]

        for dir_name in metadata_dirs:
            dir_path = self.config_path / dir_name
            if not dir_path.exists():
                continue

            for xml_file in dir_path.glob("*.xml"):
                unique_key = (dir_name, xml_file.stem)
                if unique_key in seen_names:
                    continue
                metadata = self.metadata_parser.parse_object_metadata(xml_file)
                if metadata:
                    metadata["object_type_dir"] = dir_name
                    seen_names.add(unique_key)
                    results.append(metadata)
                    logger.info(f"Извлечены метаданные: {metadata['name']} ({dir_name})")

            for xml_file in dir_path.glob("*/*.xml"):
                if "Forms" in str(xml_file) or "Commands" in str(xml_file) or "Ext" in str(xml_file):
                    continue
                if xml_file.parent.name == xml_file.stem:
                    unique_key = (dir_name, xml_file.stem)
                    if unique_key in seen_names:
                        continue
                    metadata = self.metadata_parser.parse_object_metadata(xml_file)
                    if metadata:
                        metadata["object_type_dir"] = dir_name
                        seen_names.add(unique_key)
                        results.append(metadata)
                        logger.info(f"Извлечены метаданные: {metadata['name']} ({dir_name})")

        return results

    def scan_all_forms(self) -> List[Dict]:
        """Сканирование всех форм."""
        results = []
        for pattern in ("Forms/*/Form.xml", "Forms/*/Ext/Form.xml"):
            for xml_file in self.config_path.rglob(pattern):
                form_metadata = self.metadata_parser.parse_form_metadata(xml_file)
                if form_metadata:
                    relative_path = xml_file.relative_to(self.config_path)
                    parts = relative_path.parts
                    if len(parts) >= 3:
                        form_metadata["object_type"] = parts[0]
                        form_metadata["object_name"] = parts[1]
                    results.append(form_metadata)
                    logger.info(f"Найдена форма: {form_metadata['form_name']}")
        return results
