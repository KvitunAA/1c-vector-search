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
        "Роли": "Roles",
    }

    METADATA_COLLECTION_MAP_CF = {
        k.casefold(): v for k, v in METADATA_COLLECTION_MAP.items()
    }

    @staticmethod
    def extract_metadata_references_from_code(code: str) -> List[Tuple[str, str]]:
        """Извлечение ссылок на объекты метаданных из BSL кода."""
        refs = []
        pattern = re.compile(
            r"\b(Документы|Справочники|РегистрыСведений|РегистрыНакопления|"
            r"РегистрыБухгалтерии|ПланыСчетов|Перечисления|ОбщиеМодули|"
            r"Обработки|Отчеты|Роли)\.(\w+)",
            re.IGNORECASE,
        )
        seen = set()
        for match in pattern.finditer(code):
            collection_ru = match.group(1)
            obj_name = match.group(2)
            obj_type = BSLParser.METADATA_COLLECTION_MAP_CF.get(collection_ru.casefold())
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

    TEMPLATE_KIND_LABELS = {
        "DataCompositionSchema": "Макет СКД (схема компоновки данных)",
        "SpreadsheetDocument": "Табличный документ (MXL, печатная форма)",
        "HTMLDocument": "HTML-документ",
        "TextDocument": "Текстовый документ",
        "BinaryData": "Двоичные данные",
        "ActiveDocument": "ActiveDocument",
        "GeographicalSchema": "Географическая схема",
        "GraphicalSchema": "Графическая схема",
        "Template": "Макет",
    }

    RIGHTS_TYPE_MAP = {
        "Catalog": "Catalogs",
        "CatalogObject": "Catalogs",
        "Document": "Documents",
        "DocumentObject": "Documents",
        "InformationRegister": "InformationRegisters",
        "AccumulationRegister": "AccumulationRegisters",
        "AccountingRegister": "AccountingRegisters",
        "DataProcessor": "DataProcessors",
        "Report": "Reports",
        "CommonModule": "CommonModules",
        "Enum": "Enums",
        "ChartOfAccounts": "ChartsOfAccounts",
        "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes",
        "BusinessProcess": "BusinessProcesses",
        "Task": "Tasks",
        "ExchangePlan": "ExchangePlans",
        "Constant": "Constants",
        "FilterCriterion": "FilterCriteria",
        "HTTPService": "HTTPServices",
        "WebService": "WebServices",
        "CommonForm": "CommonForms",
        "CommonCommand": "CommonCommands",
        "Subsystem": "Subsystems",
    }

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.split("}")[-1] if tag else ""

    @classmethod
    def _synonym_text(cls, synonym_elem) -> str:
        if synonym_elem is None:
            return ""
        for child in synonym_elem.iter():
            local = cls._local_name(child.tag)
            if local in ("content", "presentation") and child.text and child.text.strip():
                return child.text.strip()
        return (synonym_elem.text or "").strip()

    @classmethod
    def _properties_fields(cls, root) -> Tuple[str, str, str]:
        """Имя, синоним, комментарий из Properties (выгрузка конфигуратора) или v8:name."""
        name, synonym, comment = "", "", ""
        for elem in root.iter():
            if cls._local_name(elem.tag) != "Properties":
                continue
            for child in list(elem):
                local = cls._local_name(child.tag)
                if local == "Name" and child.text and child.text.strip():
                    name = child.text.strip()
                elif local == "Comment" and child.text and child.text.strip():
                    comment = child.text.strip()
                elif local == "Synonym":
                    synonym = cls._synonym_text(child)
            if name:
                break
        if not name:
            name_elem = root.find(".//v8:name", cls.NS)
            if name_elem is not None and name_elem.text:
                name = name_elem.text.strip()
        if not synonym:
            synonym_elem = root.find(".//v8:synonym", cls.NS)
            if synonym_elem is not None:
                synonym = cls._synonym_text(synonym_elem)
        return name, synonym, comment

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

    @classmethod
    def parse_rights_xml(cls, xml_path: Path) -> List[Dict]:
        """Разбор Ext/Rights.xml: объекты и выданные права (value=true)."""
        if not xml_path.exists():
            return []
        try:
            root = ET.parse(xml_path).getroot()
        except Exception as e:
            logger.error("Ошибка парсинга прав %s: %s", xml_path, e)
            return []

        granted = []
        for obj_elem in root.iter():
            if cls._local_name(obj_elem.tag).lower() != "object":
                continue
            object_name = ""
            rights = []
            for child in list(obj_elem):
                local = cls._local_name(child.tag).lower()
                if local == "name" and child.text:
                    object_name = child.text.strip()
                elif local == "right":
                    right_name, allowed = "", False
                    for right_child in list(child):
                        rlocal = cls._local_name(right_child.tag).lower()
                        if rlocal == "name" and right_child.text:
                            right_name = right_child.text.strip()
                        elif rlocal == "value" and right_child.text:
                            allowed = right_child.text.strip().lower() in ("true", "1", "истина")
                    if right_name and allowed:
                        rights.append(right_name)
            if object_name and rights:
                granted.append({"name": object_name, "rights": rights})
        return granted

    @classmethod
    def parse_role_metadata(cls, xml_path: Path, rights_path: Optional[Path] = None) -> Optional[Dict]:
        """Роль или шаблон прав: свойства + список выданных прав."""
        try:
            root = ET.parse(xml_path).getroot()
        except Exception as e:
            logger.error("Ошибка парсинга роли %s: %s", xml_path, e)
            return None

        name, synonym, comment = cls._properties_fields(root)
        if not name:
            name = xml_path.stem
        granted = cls.parse_rights_xml(rights_path) if rights_path else []
        return {
            "name": name,
            "type": cls._local_name(root.tag) or "Role",
            "synonym": synonym,
            "comment": comment,
            "file_path": str(xml_path),
            "granted_objects": granted,
            "granted_count": len(granted),
            "attributes": [],
            "attributes_count": 0,
            "tabular_sections": [],
            "has_modules": [],
        }

    @classmethod
    def _empty_template_record(cls, xml_path: Path, kind: str) -> Dict:
        name = xml_path.parent.parent.name if xml_path.parent.name == "Ext" else xml_path.stem
        return {
            "name": name,
            "type": kind,
            "synonym": "",
            "comment": "",
            "file_path": str(xml_path),
            "kind": kind,
            "queries": [],
            "data_sets": [],
            "fields": [],
            "parameters": [],
            "named_areas": [],
            "cell_texts": [],
            "body_text": "",
            "attributes": [],
            "attributes_count": 0,
            "tabular_sections": [],
            "has_modules": [],
        }

    @classmethod
    def _unique_texts(cls, values: List[str], limit: int) -> List[str]:
        seen = []
        for raw in values:
            text = " ".join((raw or "").split())
            if len(text) < 2:
                continue
            if text in seen:
                continue
            seen.append(text)
            if len(seen) >= limit:
                break
        return seen

    @classmethod
    def _extract_spreadsheet(cls, root) -> Tuple[List[str], List[str], List[str]]:
        cell_texts = []
        parameters = []
        named_areas = []
        for elem in root.iter():
            local = cls._local_name(elem.tag)
            lower = local.lower()
            text = (elem.text or "").strip()
            if lower in ("content", "presentation") and text:
                cell_texts.append(text)
            elif lower == "parameter" and text and "\n" not in text and len(text) < 200:
                parameters.append(text)
            elif lower in ("nameditem", "nameditemcells"):
                for child in list(elem):
                    if cls._local_name(child.tag).lower() == "name" and child.text:
                        named_areas.append(child.text.strip())
        return cell_texts, parameters, named_areas

    @classmethod
    def _extract_plain_xml_text(cls, root) -> str:
        chunks = []
        for elem in root.iter():
            text = (elem.text or "").strip()
            if len(text) >= 2:
                chunks.append(text)
        return "\n".join(cls._unique_texts(chunks, 400))

    @classmethod
    def _strip_html(cls, html: str) -> str:
        without_tags = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
        without_tags = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", without_tags)
        without_tags = re.sub(r"(?s)<[^>]+>", " ", without_tags)
        return " ".join(without_tags.split())

    @classmethod
    def _detect_template_kind(cls, head: str, root_tag: str, declared: str = "") -> str:
        declared_cf = (declared or "").replace(" ", "")
        for key in cls.TEMPLATE_KIND_LABELS:
            if declared_cf.lower() == key.lower():
                return key
        head_l = head.lower()
        root_l = root_tag.lower()
        if "datacompositionschema" in head_l or "data-composition-system" in head_l:
            return "DataCompositionSchema"
        if "spreadsheet" in head_l or root_l == "document":
            return "SpreadsheetDocument"
        if "<html" in head_l or "textdocument" in head_l and "<html" in head_l:
            return "HTMLDocument"
        if "htmldocument" in head_l or "text/html" in head_l:
            return "HTMLDocument"
        if "geographicalschema" in head_l:
            return "GeographicalSchema"
        if "graphicalschema" in head_l:
            return "GraphicalSchema"
        if "activedocument" in head_l:
            return "ActiveDocument"
        return "Template"

    @classmethod
    def read_template_descriptor(cls, template_dir: Path) -> Dict[str, str]:
        """Properties из Templates/Имя.xml рядом с Ext/Template.xml."""
        candidates = [
            template_dir.parent / f"{template_dir.name}.xml",
            template_dir / f"{template_dir.name}.xml",
        ]
        for xml_path in candidates:
            if not xml_path.exists():
                continue
            try:
                root = ET.parse(xml_path).getroot()
            except Exception:
                continue
            name, synonym, comment = cls._properties_fields(root)
            template_type = ""
            for elem in root.iter():
                if cls._local_name(elem.tag) == "TemplateType" and elem.text:
                    template_type = elem.text.strip()
                    break
            return {
                "name": name,
                "synonym": synonym,
                "comment": comment,
                "template_type": template_type,
                "descriptor_path": str(xml_path),
            }
        return {}

    @classmethod
    def parse_dcs_template(cls, xml_path: Path) -> Optional[Dict]:
        """Обратная совместимость: только СКД, иначе None."""
        parsed = cls.parse_template_file(xml_path)
        if not parsed or parsed.get("kind") != "DataCompositionSchema":
            return None
        return parsed

    @classmethod
    def parse_template_file(cls, xml_path: Path, declared_type: str = "") -> Optional[Dict]:
        """Любой макет из Ext/Template.xml: СКД, MXL, HTML, текст."""
        try:
            head = xml_path.read_text(encoding="utf-8-sig", errors="ignore")
        except Exception as e:
            logger.error("Ошибка чтения макета %s: %s", xml_path, e)
            return None
        if not head.strip():
            return None

        kind = cls._detect_template_kind(head[:8000], "", declared_type)
        if kind == "DataCompositionSchema" or (
            "DataCompositionSchema" in head[:4000] or "data-composition-system" in head[:4000]
        ):
            try:
                root = ET.parse(xml_path).getroot()
            except Exception as e:
                logger.error("Ошибка парсинга макета СКД %s: %s", xml_path, e)
                return None
            record = cls._empty_template_record(xml_path, "DataCompositionSchema")
            queries, data_sets, fields, parameters = [], [], [], []
            for elem in root.iter():
                local = cls._local_name(elem.tag)
                lower = local.lower()
                text = (elem.text or "").strip()
                if lower == "query" and text:
                    queries.append(text)
                elif lower == "dataset":
                    ds_name = ""
                    for child in list(elem):
                        if cls._local_name(child.tag).lower() == "name" and child.text:
                            ds_name = child.text.strip()
                            break
                    if ds_name:
                        data_sets.append(ds_name)
                elif lower == "field" or local.endswith("Field"):
                    for child in list(elem):
                        child_local = cls._local_name(child.tag).lower()
                        if child_local in ("datapath", "name") and child.text and child.text.strip():
                            fields.append(child.text.strip())
                elif lower == "parameter" or local.endswith("Parameter"):
                    for child in list(elem):
                        if cls._local_name(child.tag).lower() == "name" and child.text and child.text.strip():
                            parameters.append(child.text.strip())
            record["queries"] = queries
            record["data_sets"] = list(dict.fromkeys(data_sets))
            record["fields"] = list(dict.fromkeys(fields))[:80]
            record["parameters"] = list(dict.fromkeys(parameters))[:40]
            return record

        try:
            root = ET.parse(xml_path).getroot()
            root_tag = cls._local_name(root.tag)
        except Exception:
            if "<html" in head.lower():
                record = cls._empty_template_record(xml_path, "HTMLDocument")
                record["body_text"] = cls._strip_html(head)[:8000]
                return record
            record = cls._empty_template_record(xml_path, declared_type or "TextDocument")
            record["body_text"] = head[:8000]
            return record

        kind = cls._detect_template_kind(head[:8000], root_tag, declared_type)
        record = cls._empty_template_record(xml_path, kind)

        if kind == "SpreadsheetDocument":
            cell_texts, parameters, named_areas = cls._extract_spreadsheet(root)
            record["cell_texts"] = cls._unique_texts(cell_texts, 250)
            record["parameters"] = cls._unique_texts(parameters, 80)
            record["named_areas"] = cls._unique_texts(named_areas, 60)
            return record

        if kind == "HTMLDocument":
            record["body_text"] = cls._strip_html(head)[:8000]
            return record

        record["body_text"] = cls._extract_plain_xml_text(root)[:8000]
        return record

    @classmethod
    def parse_binary_template(cls, bin_path: Path, declared_type: str = "") -> Dict:
        """Макет только как Template.bin (без XML содержимого)."""
        record = cls._empty_template_record(bin_path, declared_type or "BinaryData")
        record["kind"] = declared_type or "BinaryData"
        record["type"] = record["kind"]
        return record

    @classmethod
    def rights_target(cls, object_ref: str) -> Optional[Tuple[str, str]]:
        """Catalog.Номенклатура -> (Catalogs, Номенклатура)."""
        if not object_ref or "." not in object_ref:
            return None
        type_key, obj_name = object_ref.split(".", 1)
        obj_name = obj_name.split(".")[0]
        mapped = cls.RIGHTS_TYPE_MAP.get(type_key)
        if not mapped or not obj_name:
            return None
        return mapped, obj_name


class ConfigurationScanner:
    """Сканер структуры конфигурации 1С"""

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.bsl_parser = BSLParser()
        self.metadata_parser = MetadataParser()

    def list_module_files(self) -> List[Tuple[Path, str]]:
        """Список BSL-файлов без парсинга (для параллельной обработки в индексаторе графа)."""
        results = []
        for bsl_file in self.config_path.rglob("*.bsl"):
            relative_path = bsl_file.relative_to(self.config_path)
            parts = relative_path.parts
            object_type = parts[0] if len(parts) > 0 else "Unknown"
            object_name = parts[1] if len(parts) > 1 else bsl_file.stem
            results.append((bsl_file, f"{object_type}.{object_name}"))
        return results

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
                    logger.debug("Найдено %s методов в %s", len(methods), relative_path)
            except Exception as e:
                logger.error(
                    "Ошибка при сканировании модуля %s: %s",
                    bsl_file,
                    e,
                    exc_info=True,
                )
        logger.info("Сканирование модулей завершено: %s файлов с методами", len(results))
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

        results.extend(self.scan_all_roles("Roles"))
        results.extend(self.scan_all_roles("RoleTemplates"))
        results.extend(self.scan_all_templates())
        logger.info("Сканирование метаданных завершено: %s объектов", len(results))
        return results

    def _iter_named_xml(self, dir_name: str):
        dir_path = self.config_path / dir_name
        if not dir_path.exists():
            return
        seen = set()
        for xml_file in dir_path.glob("*.xml"):
            if xml_file.stem not in seen:
                seen.add(xml_file.stem)
                yield xml_file, xml_file.stem
        for xml_file in dir_path.glob("*/*.xml"):
            if xml_file.parent.name != xml_file.stem:
                continue
            if xml_file.stem in seen:
                continue
            seen.add(xml_file.stem)
            yield xml_file, xml_file.stem

    def scan_all_roles(self, directory: str = "Roles") -> List[Dict]:
        """Роли или шаблоны прав (RoleTemplates)."""
        results = []
        kind = "RoleTemplate" if directory == "RoleTemplates" else "Role"
        for xml_file, stem in self._iter_named_xml(directory):
            if xml_file.parent.name == stem:
                rights_path = xml_file.parent / "Ext" / "Rights.xml"
            else:
                rights_path = xml_file.parent / stem / "Ext" / "Rights.xml"
            parsed = self.metadata_parser.parse_role_metadata(xml_file, rights_path)
            if not parsed:
                continue
            parsed["object_type_dir"] = directory
            parsed["kind"] = kind
            parsed["type"] = kind
            results.append(parsed)
            logger.debug(
                "Роль/шаблон: %s (%s), объектов прав: %s",
                parsed["name"],
                directory,
                parsed.get("granted_count", 0),
            )
        logger.info("Сканирование %s: %s объектов", directory, len(results))
        return results

    def _annotate_template(self, parsed: Dict, content_path: Path) -> Optional[Dict]:
        try:
            relative = content_path.relative_to(self.config_path)
        except ValueError:
            return None
        parts = relative.parts
        template_name = parsed.get("name") or content_path.parent.parent.name
        owner_type, owner_name = "Unknown", ""
        if parts and parts[0] == "CommonTemplates" and len(parts) >= 2:
            owner_type = "CommonTemplates"
            owner_name = parts[1]
            template_name = parts[1]
        elif "Templates" in parts:
            idx = parts.index("Templates")
            owner_type = parts[0] if parts else "Unknown"
            owner_name = parts[1] if len(parts) > 1 else ""
            if idx + 1 < len(parts):
                template_name = parts[idx + 1]
        if owner_type == "CommonTemplates":
            unique_name = f"CommonTemplates.{template_name}"
        else:
            unique_name = f"{owner_type}.{owner_name}.{template_name}"

        template_dir = content_path.parent.parent if content_path.parent.name == "Ext" else content_path.parent
        descriptor = self.metadata_parser.read_template_descriptor(template_dir)
        if descriptor.get("template_type") and parsed.get("kind") in ("Template", "BinaryData", ""):
            parsed["kind"] = descriptor["template_type"]
            parsed["type"] = descriptor["template_type"]
        if descriptor.get("synonym"):
            parsed["synonym"] = descriptor["synonym"]
        elif not parsed.get("synonym"):
            parsed["synonym"] = template_name
        if descriptor.get("comment"):
            parsed["comment"] = descriptor["comment"]
        if descriptor.get("name"):
            template_name = descriptor["name"]
            if owner_type == "CommonTemplates":
                unique_name = f"CommonTemplates.{template_name}"
            else:
                unique_name = f"{owner_type}.{owner_name}.{template_name}"

        parsed["name"] = unique_name
        parsed["template_name"] = template_name
        parsed["owner_type"] = owner_type
        parsed["owner_name"] = owner_name
        parsed["object_type_dir"] = "Templates"
        parsed["kind"] = parsed.get("kind") or "Template"
        parsed["type"] = parsed.get("type") or parsed["kind"]
        return parsed

    def scan_all_templates(self) -> List[Dict]:
        """Все макеты: СКД, MXL, HTML, текст, двоичные (Template.xml / Template.bin)."""
        results = []
        seen = set()
        for xml_file in self.config_path.rglob("Ext/Template.xml"):
            descriptor_dir = xml_file.parent.parent
            declared = self.metadata_parser.read_template_descriptor(descriptor_dir).get("template_type", "")
            parsed = self.metadata_parser.parse_template_file(xml_file, declared)
            if not parsed:
                continue
            annotated = self._annotate_template(parsed, xml_file)
            if not annotated or annotated["name"] in seen:
                continue
            seen.add(annotated["name"])
            results.append(annotated)
            logger.debug("Макет %s: %s", annotated.get("kind"), annotated["name"])

        for bin_file in self.config_path.rglob("Ext/Template.bin"):
            xml_sibling = bin_file.with_suffix(".xml")
            if xml_sibling.exists():
                continue
            descriptor_dir = bin_file.parent.parent
            declared = self.metadata_parser.read_template_descriptor(descriptor_dir).get("template_type", "")
            parsed = self.metadata_parser.parse_binary_template(bin_file, declared)
            annotated = self._annotate_template(parsed, bin_file)
            if not annotated or annotated["name"] in seen:
                continue
            seen.add(annotated["name"])
            results.append(annotated)
            logger.debug("Макет (bin) %s: %s", annotated.get("kind"), annotated["name"])

        logger.info("Сканирование макетов: %s", len(results))
        return results

    def scan_all_dcs_templates(self) -> List[Dict]:
        """Обратная совместимость: все макеты, не только СКД."""
        return self.scan_all_templates()

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
