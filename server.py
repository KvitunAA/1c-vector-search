"""
MCP Сервер для работы с конфигурацией 1С через векторную БД
"""
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

from config import Config
from vectordb_manager import VectorDBManager
from graph_db import GraphDBManager
from code_grep import grep_method_usage

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mcp_server.log', encoding='utf-8'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

app = Server("1c-vector-search")
db_manager = VectorDBManager()
graph_manager = GraphDBManager()

logger.info("MCP Сервер запущен")
logger.info(f"Векторная БД: {Config.VECTORDB_PATH}")


@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Список доступных инструментов MCP"""
    return [
        types.Tool(
            name="search_1c_code",
            description=(
                "Семантический поиск по коду модулей 1С. "
                "Найдет процедуры, функции и фрагменты кода по описанию на естественном языке. "
                "Например: 'проведение документа', 'расчет НДС', 'работа с файлами'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Описание искомого кода или функциональности"},
                    "limit": {"type": "integer", "description": "Количество результатов (по умолчанию 5)", "default": 5, "minimum": 1, "maximum": 20},
                    "only_export": {"type": "boolean", "description": "Искать только экспортные методы", "default": False}
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="search_1c_metadata",
            description=(
                "Поиск объектов метаданных 1С (справочники, документы, регистры, роли, "
                "шаблоны прав, макеты СКД и печатные формы MXL). Ищет по названию, синониму, описанию, правам, тексту запроса или тексту макета."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Название или описание объекта метаданных"},
                    "object_type": {"type": "string", "description": "Тип объекта метаданных для фильтрации", "enum": list(Config.METADATA_TYPES.values()) + ["Все"], "default": "Все"},
                    "limit": {"type": "integer", "description": "Количество результатов", "default": 5, "minimum": 1, "maximum": 20}
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="search_1c_forms",
            description="Поиск форм 1С по описанию или названию",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Описание или название формы"},
                    "limit": {"type": "integer", "description": "Количество результатов", "default": 5, "minimum": 1, "maximum": 20}
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="find_1c_method_usage",
            description=(
                "Найти где используется конкретный метод (процедура/функция) в конфигурации. "
                "Поиск по имени метода в коде других модулей."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "method_name": {"type": "string", "description": "Имя процедуры или функции для поиска"},
                    "limit": {"type": "integer", "description": "Максимальное количество результатов", "default": 10, "minimum": 1, "maximum": 50}
                },
                "required": ["method_name"]
            }
        ),
        types.Tool(
            name="get_vectordb_stats",
            description="Получить статистику по векторной БД (количество проиндексированных объектов)",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="search_by_object_name",
            description=(
                "Получить всю информацию о конкретном объекте конфигурации по его имени. "
                "Вернет метаданные и весь код, связанный с этим объектом."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string", "description": "Точное имя объекта (например: 'Номенклатура', 'РеализацияТоваровУслуг')"},
                    "include_code": {"type": "boolean", "description": "Включить код модулей объекта", "default": True}
                },
                "required": ["object_name"]
            }
        ),
        types.Tool(
            name="graph_dependencies",
            description=(
                "Анализ зависимостей: какие объекты ссылаются на указанный объект. "
                "Полезно для оценки влияния изменений (что затронет изменение объекта X)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string", "description": "Имя объекта метаданных (например: 'Номенклатура', 'Сотрудники')"},
                    "limit": {"type": "integer", "description": "Максимум результатов", "default": 100}
                },
                "required": ["object_name"]
            }
        ),
        types.Tool(
            name="graph_references",
            description=(
                "Анализ ссылок: на какие объекты ссылается указанный объект. "
                "Показывает зависимости объекта (что он использует)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string", "description": "Имя объекта метаданных"},
                    "limit": {"type": "integer", "description": "Максимум результатов", "default": 100}
                },
                "required": ["object_name"]
            }
        ),
        types.Tool(
            name="graph_stats",
            description="Статистика графовой базы: количество узлов, рёбер, распределение по типам.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="get_analyst_instructions",
            description=(
                "Получить инструкцию для аналитика и пользователя по использованию инструментов MCP. "
                "Возвращает описание всех инструментов, параметры, примеры запросов и рекомендации."
            ),
            inputSchema={"type": "object", "properties": {}}
        )
    ]


@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Обработка вызовов инструментов"""
    arguments = arguments or {}

    try:
        if name == "search_1c_code":
            query = (arguments.get("query") or "").strip()
            if not query:
                return [types.TextContent(type="text", text=json.dumps({"error": "query обязателен и не может быть пустым"}, ensure_ascii=False))]
            limit = arguments.get("limit", 5)
            only_export = arguments.get("only_export", False)
            logger.info(f"Поиск кода: '{query}' (limit={limit}, only_export={only_export})")
            filters = {"is_export": True} if only_export else None
            results = db_manager.search_code(query, limit=limit, filters=filters)
            formatted_results = []
            for result in results:
                metadata = result["metadata"]
                item = {
                    "rank": result["rank"],
                    "relevance": result["relevance"],
                    "object": f"{metadata.get('object_type', '')}.{metadata.get('object_name', '')}",
                    "module": metadata.get("module_name", ""),
                    "method": metadata.get("method_name", ""),
                    "signature": metadata.get("signature", ""),
                    "is_export": metadata.get("is_export", False),
                    "code": result["document"],
                    "file_path": metadata.get("file_path", ""),
                }
                directive = metadata.get("directive", "")
                if directive:
                    item["directive"] = directive
                formatted_results.append(item)
            response = {"query": query, "total_results": len(formatted_results), "results": formatted_results}
            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

        elif name == "search_1c_metadata":
            query = (arguments.get("query") or "").strip()
            if not query:
                return [types.TextContent(type="text", text=json.dumps({"error": "query обязателен и не может быть пустым"}, ensure_ascii=False))]
            object_type = arguments.get("object_type", "Все")
            limit = arguments.get("limit", 5)
            logger.info(f"Поиск метаданных: '{query}' (type={object_type}, limit={limit})")
            type_filter = None
            if object_type != "Все":
                type_filter = Config.METADATA_TYPES.get(object_type, object_type)
            results = db_manager.search_metadata(query, limit=limit, object_type=type_filter)
            formatted_results = []
            for result in results:
                metadata = result["metadata"]
                item = {
                    "rank": result["rank"],
                    "relevance": result["relevance"],
                    "type": metadata.get("object_type", ""),
                    "name": metadata.get("object_name", ""),
                    "synonym": metadata.get("synonym", ""),
                    "description": metadata.get("description", ""),
                    "has_modules": [x for x in (metadata.get("has_modules") or "").split(",") if x.strip()],
                    "attributes_count": metadata.get("attributes_count", 0),
                    "file_path": metadata.get("file_path", ""),
                }
                ts_raw = metadata.get("tabular_sections", "")
                if ts_raw:
                    item["tabular_sections"] = [x.strip() for x in ts_raw.split(",") if x.strip()]
                if metadata.get("has_dimensions"):
                    item["has_dimensions"] = True
                if metadata.get("has_resources"):
                    item["has_resources"] = True
                if metadata.get("commands_count", 0) > 0:
                    item["commands_count"] = metadata["commands_count"]
                formatted_results.append(item)
            response = {"query": query, "object_type_filter": object_type, "total_results": len(formatted_results), "results": formatted_results}
            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

        elif name == "search_1c_forms":
            query = (arguments.get("query") or "").strip()
            if not query:
                return [types.TextContent(type="text", text=json.dumps({"error": "query обязателен и не может быть пустым"}, ensure_ascii=False))]
            limit = arguments.get("limit", 5)
            logger.info(f"Поиск форм: '{query}' (limit={limit})")
            results = db_manager.search_forms(query, limit=limit)
            formatted_results = []
            for result in results:
                metadata = result["metadata"]
                formatted_results.append({
                    "rank": result["rank"],
                    "relevance": result["relevance"],
                    "form_name": metadata.get("form_name", ""),
                    "object": f"{metadata.get('object_type', '')}.{metadata.get('object_name', '')}",
                    "elements_count": metadata.get("elements_count", 0),
                    "file_path": metadata.get("file_path", "")
                })
            response = {"query": query, "total_results": len(formatted_results), "results": formatted_results}
            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

        elif name == "find_1c_method_usage":
            method_name = (arguments.get("method_name") or "").strip()
            if not method_name:
                return [types.TextContent(type="text", text=json.dumps({"error": "method_name обязателен и не может быть пустым"}, ensure_ascii=False))]
            limit = arguments.get("limit", 10)
            logger.info(f"Поиск использования метода: '{method_name}' (limit={limit})")
            config_path = Path(Config.CONFIG_PATH) if Config.CONFIG_PATH else None
            if config_path and config_path.exists():
                grep_results = grep_method_usage(method_name, config_path=config_path, limit=limit)
                usages = [
                    {
                        "object": f"{r['object_type']}.{r['object_name']}",
                        "module": r["module_name"],
                        "in_method": r["in_method"],
                        "line_number": r["line_number"],
                        "line_content": r["line_content"],
                        "file_path": r["file_path"],
                        "source": "grep",
                    }
                    for r in grep_results
                ]
            else:
                results = db_manager.search_code(f"вызов {method_name}", limit=limit * 2)
                usages = []
                for result in results:
                    if method_name.lower() in result["document"].lower():
                        metadata = result["metadata"]
                        usages.append({
                            "object": f"{metadata.get('object_type', '')}.{metadata.get('object_name', '')}",
                            "module": metadata.get("module_name", ""),
                            "in_method": metadata.get("method_name", ""),
                            "code_context": result["document"][:500] + "..." if len(result["document"]) > 500 else result["document"],
                            "file_path": metadata.get("file_path", ""),
                            "source": "vector",
                        })
                        if len(usages) >= limit:
                            break

            graph_deps = graph_manager.get_dependencies(method_name, limit=20)
            caller_objects = set()
            for u in usages:
                caller_objects.add(u["object"])
            graph_extra = [
                {
                    "object": dep["object"],
                    "edge_type": dep["edge_type"],
                    "node_id": dep["node_id"],
                    "source": "graph",
                }
                for dep in graph_deps
                if dep["object"] not in caller_objects
            ]

            response = {
                "method_name": method_name,
                "total_usages": len(usages),
                "usages": usages,
            }
            if graph_extra:
                response["graph_related"] = graph_extra
                response["graph_related_count"] = len(graph_extra)
            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

        elif name == "get_vectordb_stats":
            logger.info("Получение статистики векторной БД")
            stats = db_manager.get_stats()
            response = {
                "database_path": Config.VECTORDB_PATH,
                "configuration_path": Config.CONFIG_PATH,
                "collections": stats,
                "total_records": sum(stats.values())
            }
            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

        elif name == "search_by_object_name":
            object_name = (arguments.get("object_name") or "").strip()
            include_code = arguments.get("include_code", True)
            if not object_name:
                return [types.TextContent(type="text", text=json.dumps({"error": "object_name обязателен и не может быть пустым"}, ensure_ascii=False))]
            logger.info(f"Поиск объекта по имени: '{object_name}'")
            metadata_results = db_manager.search_metadata(object_name, limit=1)
            if not metadata_results:
                return [types.TextContent(type="text", text=json.dumps({"error": f"Объект '{object_name}' не найден", "object_name": object_name}, ensure_ascii=False, indent=2))]
            metadata_info = metadata_results[0]["metadata"]
            response = {
                "object_name": object_name,
                "type": metadata_info.get("object_type", ""),
                "synonym": metadata_info.get("synonym", ""),
                "description": metadata_info.get("description", ""),
                "has_modules": [x for x in (metadata_info.get("has_modules") or "").split(",") if x.strip()],
                "attributes_count": metadata_info.get("attributes_count", 0),
            }
            ts_raw = metadata_info.get("tabular_sections", "")
            if ts_raw:
                response["tabular_sections"] = [x.strip() for x in ts_raw.split(",") if x.strip()]
            if metadata_info.get("has_dimensions"):
                response["has_dimensions"] = True
            if metadata_info.get("has_resources"):
                response["has_resources"] = True
            if metadata_info.get("commands_count", 0) > 0:
                response["commands_count"] = metadata_info["commands_count"]
            if include_code:
                code_results = db_manager.search_code_by_object(object_name=object_name, limit=200)
                if not code_results:
                    code_results = db_manager.search_code(object_name, limit=100)
                    code_results = [r for r in code_results if r["metadata"].get("object_name") == object_name]
                object_code = []
                for result in code_results:
                    code_item = {
                        "module": result["metadata"].get("module_name", ""),
                        "method": result["metadata"].get("method_name", ""),
                        "signature": result["metadata"].get("signature", ""),
                        "is_export": result["metadata"].get("is_export", False),
                        "code": result["document"],
                    }
                    directive = result["metadata"].get("directive", "")
                    if directive:
                        code_item["directive"] = directive
                    object_code.append(code_item)
                response["code"] = object_code
                response["methods_count"] = len(object_code)
            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

        elif name == "graph_dependencies":
            object_name = (arguments.get("object_name") or "").strip()
            try:
                limit = min(max(1, int(arguments.get("limit", 100))), 500)
            except (TypeError, ValueError):
                limit = 100
            if not object_name:
                return [types.TextContent(type="text", text=json.dumps({"error": "object_name обязателен и не может быть пустым"}, ensure_ascii=False))]
            logger.info(f"Граф: зависимости объекта '{object_name}' (limit={limit})")
            deps = graph_manager.get_dependencies(object_name, limit=limit)
            response = {"object_name": object_name, "description": "Объекты, которые ссылаются на указанный объект", "total_count": len(deps), "dependencies": deps}
            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

        elif name == "graph_references":
            object_name = (arguments.get("object_name") or "").strip()
            try:
                limit = min(max(1, int(arguments.get("limit", 100))), 500)
            except (TypeError, ValueError):
                limit = 100
            if not object_name:
                return [types.TextContent(type="text", text=json.dumps({"error": "object_name обязателен и не может быть пустым"}, ensure_ascii=False))]
            logger.info(f"Граф: ссылки объекта '{object_name}' (limit={limit})")
            refs = graph_manager.get_references(object_name, limit=limit)
            response = {"object_name": object_name, "description": "Объекты, на которые ссылается указанный объект", "total_count": len(refs), "references": refs}
            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

        elif name == "graph_stats":
            logger.info("Граф: статистика")
            stats = graph_manager.get_stats()
            response = {"database_path": str(Config.GRAPHDB_PATH), **stats}
            return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

        elif name == "get_analyst_instructions":
            logger.info("Инструкция для аналитика")
            instructions_path = Path(Config.PROFILE_DIR) / "ИнструкцияПоИспользованиюMCP.md"
            if instructions_path.exists():
                text = instructions_path.read_text(encoding="utf-8")
                return [types.TextContent(type="text", text=text)]
            return [types.TextContent(type="text", text=f"Файл инструкции не найден: {instructions_path}")]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.error(f"Ошибка при выполнении инструмента {name}: {e}", exc_info=True)
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"error": str(e), "tool": name, "arguments": arguments}, ensure_ascii=False, indent=2)
            )
        ]


async def main():
    """Главная функция сервера"""
    logger.info("Запуск MCP сервера...")

    stats = db_manager.get_stats()
    if sum(stats.values()) == 0:
        logger.warning("⚠️  Векторная БД пуста! Запустите индексацию: python run_indexer.py")
    else:
        logger.info(f"✅ Векторная БД готова: {sum(stats.values())} записей")

    graph_stats = graph_manager.get_stats()
    if graph_stats["nodes_count"] == 0:
        logger.warning("⚠️  Граф пуст! Запустите индексацию: run_index_your_project.cmd или run_index_graph_your_project.cmd")
    else:
        logger.info(f"✅ Граф готов: {graph_stats['nodes_count']} узлов, {graph_stats['edges_count']} рёбер")

    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="1c-vector-search",
                    server_version="0.1.0",
                    capabilities=app.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        graph_manager.close()
        logger.info("Графовая БД закрыта")


if __name__ == "__main__":
    asyncio.run(main())
