"""
Скрипт индексации графа связей конфигурации 1С.
Поддерживает кеширование сканирования и чекпоинты для продолжения с места остановки.
Поддерживает многопроцессорность для ускорения индексации.
"""
import sys
import json
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple
from multiprocessing import Pool, cpu_count

# Добавляем текущую директорию в путь поиска модулей
sys.path.insert(0, str(Path(__file__).parent))

import logging
from tqdm import tqdm
from config import Config
from graph_db import GraphDBManager
from graph_staging import GraphStagingWriter
from logging_setup import setup_index_logging
from parser_1c import BSLParser, ConfigurationScanner, MetadataParser

setup_index_logging(Config.LOG_LEVEL)
logger = logging.getLogger(__name__)

# Файлы для управления состоянием
SCAN_CACHE_FILE = "graph_scan_cache.json"
CHECKPOINT_FILE = "graph_checkpoint.json"
POOL_CHUNKSIZE = 16

_REF_TYPES_FALLBACK = frozenset(
    (
        "Catalogs",
        "Documents",
        "InformationRegisters",
        "AccumulationRegisters",
        "CommonModules",
        "Enums",
        "DataProcessors",
        "Reports",
        "Roles",
    )
)


def _build_module_graph(
    file_path: Path,
    object_full_name: str,
    methods: List[Dict],
    known_objects: FrozenSet[Tuple[str, str]],
) -> Dict[str, Any]:
    """Строит узлы методов и рёбра для одного BSL-модуля."""
    parts = object_full_name.split(".")
    obj_type = parts[0] if len(parts) > 0 else "Unknown"
    obj_name = parts[1] if len(parts) > 1 else file_path.stem

    source_id = f"metadata:{obj_type}:{obj_name}"
    method_nodes = []
    edges = []

    for method in methods:
        method_name = method.get("method_name", "")
        module_name = file_path.stem
        method_id = f"method:{obj_type}:{obj_name}:{module_name}:{method_name}"

        method_nodes.append(
            {
                "node_id": method_id,
                "node_type": "Method",
                "name": method_name,
                "object_type": obj_type,
                "object_name": obj_name,
                "extra": {"module": module_name, "signature": method.get("signature", "")},
            }
        )
        edges.append({"source": source_id, "target": method_id, "edge_type": "HAS_METHOD"})

        refs = BSLParser.extract_metadata_references_from_code(method.get("code", ""))
        for ref_type, ref_name in refs:
            if (ref_type, ref_name) in known_objects or ref_type in _REF_TYPES_FALLBACK:
                target_id = f"metadata:{ref_type}:{ref_name}"
                edges.append({"source": source_id, "target": target_id, "edge_type": "USES_IN_CODE"})

    return {
        "file_path": str(file_path),
        "object_full_name": object_full_name,
        "methods": methods,
        "source_id": source_id,
        "obj_type": obj_type,
        "obj_name": obj_name,
        "method_count": len(methods),
        "method_nodes": method_nodes,
        "edges": edges,
    }


def _process_bsl_file(
    args: Tuple[str, str, FrozenSet[Tuple[str, str]]],
) -> Optional[Dict[str, Any]]:
    """Парсинг BSL и извлечение связей в отдельном процессе."""
    file_path_str, object_full_name, known_objects = args
    file_path = Path(file_path_str)
    try:
        methods = BSLParser().parse_module(file_path)
        if not methods:
            return None
        return _build_module_graph(file_path, object_full_name, methods, known_objects)
    except Exception as exc:
        logger.error("Ошибка при обработке модуля %s: %s", file_path, exc, exc_info=True)
        return None


def _process_module_from_cache(
    args: Tuple[Path, str, List[Dict], FrozenSet[Tuple[str, str]]],
) -> Dict[str, Any]:
    """Извлечение связей из уже разобранных методов (кеш сканирования)."""
    file_path, object_full_name, methods, known_objects = args
    return _build_module_graph(file_path, object_full_name, methods, known_objects)


def _metadata_node_record(
    object_type: str,
    object_name: str,
    synonym: str = "",
) -> Dict[str, Any]:
    node_id = f"metadata:{object_type}:{object_name}"
    return {
        "node_id": node_id,
        "node_type": "Metadata",
        "name": object_name,
        "object_type": object_type,
        "object_name": object_name,
        "synonym": synonym,
    }


def _collect_module_graph_records(results: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Собирает уникальные узлы и рёбра из результатов обработки модулей."""
    nodes_by_id: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    edge_keys = set()

    for result in results:
        nodes_by_id[result["source_id"]] = _metadata_node_record(
            result["obj_type"],
            result["obj_name"],
        )
        for method_node in result["method_nodes"]:
            nodes_by_id[method_node["node_id"]] = method_node

        for edge in result["edges"]:
            edge_key = (edge["source"], edge["target"], edge["edge_type"])
            if edge_key in edge_keys:
                continue
            edge_keys.add(edge_key)

            target_id = edge["target"]
            if target_id.startswith("metadata:") and target_id.count(":") >= 2:
                _, ref_type, ref_name = target_id.split(":", 2)
                nodes_by_id[target_id] = _metadata_node_record(ref_type, ref_name)

            edges.append(edge)

    return list(nodes_by_id.values()), edges


def _role_and_template_graph_records(
    metadata_list: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Рёбра роль→объект (HAS_RIGHT) и владелец→макет (HAS_TEMPLATE)."""
    extra_nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    edge_keys = set()

    for item in metadata_list:
        obj_type = item.get("object_type_dir", "")
        obj_name = item.get("name", "")
        source_id = f"metadata:{obj_type}:{obj_name}"

        if obj_type in ("Roles", "RoleTemplates"):
            for granted in item.get("granted_objects") or []:
                target = MetadataParser.rights_target(granted.get("name", ""))
                if not target:
                    continue
                t_type, t_name = target
                target_id = f"metadata:{t_type}:{t_name}"
                extra_nodes[target_id] = _metadata_node_record(t_type, t_name)
                edge_key = (source_id, target_id, "HAS_RIGHT")
                if edge_key in edge_keys:
                    continue
                edge_keys.add(edge_key)
                edges.append(
                    {
                        "source": source_id,
                        "target": target_id,
                        "edge_type": "HAS_RIGHT",
                        "extra": {
                            "rights": ",".join(granted.get("rights") or []),
                            "ref": granted.get("name", ""),
                        },
                    }
                )

        if obj_type == "Templates":
            owner_type = item.get("owner_type") or ""
            owner_name = item.get("owner_name") or ""
            if not owner_type or not owner_name:
                continue
            owner_id = f"metadata:{owner_type}:{owner_name}"
            extra_nodes[owner_id] = _metadata_node_record(owner_type, owner_name)
            edge_key = (owner_id, source_id, "HAS_TEMPLATE")
            if edge_key in edge_keys:
                continue
            edge_keys.add(edge_key)
            edges.append(
                {
                    "source": owner_id,
                    "target": source_id,
                    "edge_type": "HAS_TEMPLATE",
                    "extra": {"template": item.get("template_name", "")},
                }
            )

    return list(extra_nodes.values()), edges


def _write_module_results_batch(
    graph: GraphDBManager,
    results: List[Dict[str, Any]],
    chunk_size: Optional[int] = None,
    on_chunk_complete: Optional[Callable[[int], None]] = None,
    chunk_index_offset: int = 0,
) -> None:
    """Пакетная запись узлов и рёбер модулей в граф порциями."""
    if not results:
        return

    if chunk_size is None:
        chunk_size = max(1, getattr(Config, "GRAPH_MODULE_CHUNK_SIZE", 100))

    total_nodes = 0
    total_edges = 0
    for start in range(0, len(results), chunk_size):
        chunk = results[start:start + chunk_size]
        nodes, edges = _collect_module_graph_records(chunk)
        total_nodes += len(nodes)
        total_edges += len(edges)
        global_start = chunk_index_offset + start + 1
        global_end = chunk_index_offset + start + len(chunk)
        logger.info(
            "Запись модулей в граф (порция %s-%s): %s узлов, %s рёбер",
            global_start,
            global_end,
            len(nodes),
            len(edges),
        )
        graph.close()
        graph.add_nodes_batch(nodes)
        graph.close()
        graph.add_edges_batch(edges)
        graph.close()
        if on_chunk_complete:
            on_chunk_complete(chunk_index_offset + start + len(chunk))

    logger.info("Запись модулей завершена: всего %s узлов, %s рёбер", total_nodes, total_edges)


class GraphIndexer:
    """Индексатор графа конфигурации 1С с поддержкой многопроцессорности"""

    def __init__(
        self,
        config_path: str,
        db_path: str = None,
        clear_existing: bool = False,
        use_cache: bool = True,
        workers: int = None,
        staging: bool = False,
    ):
        self.config_path = Path(config_path)
        self.scanner = ConfigurationScanner(self.config_path)
        self.db_path = db_path or Config.GRAPHDB_PATH
        self.staging_mode = staging
        if staging:
            logger.info("[staging] Режим staging: сбор в CSV, Kuzu — только на финальном compact")
            self.graph = GraphStagingWriter(Config.GRAPH_STAGING_PATH)
        else:
            self.graph = GraphDBManager(db_path)
        self.use_cache = use_cache
        self.workers = workers or max(1, cpu_count() - 1)
        self._fresh_graph_module_results: Optional[List[Dict[str, Any]]] = None

        if clear_existing:
            logger.info("Очистка существующего графа / staging...")
            self.graph.clear()
            self._clear_checkpoint()

    def _clear_checkpoint(self):
        """Удаляет файл чекпоинта"""
        if Path(CHECKPOINT_FILE).exists():
            Path(CHECKPOINT_FILE).unlink()
            logger.info("Чекпоинт сброшен")

    def _save_checkpoint(self, stage: str, index: int):
        """Сохраняет текущий прогресс"""
        try:
            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as handle:
                json.dump({"stage": stage, "index": index}, handle)
        except Exception as exc:
            logger.warning("Не удалось сохранить чекпоинт: %s", exc)

    def _load_checkpoint(self):
        """Загружает прогресс из файла"""
        if Path(CHECKPOINT_FILE).exists():
            try:
                with open(CHECKPOINT_FILE, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception as exc:
                logger.warning("Ошибка чтения чекпоинта: %s", exc)
        return None

    def _load_scan_cache(self):
        """Загружает данные из кеша сканирования"""
        cache_path = Path(SCAN_CACHE_FILE)
        if self.use_cache and cache_path.exists():
            logger.info("Загрузка данных из кеша сканирования: %s", cache_path)
            try:
                with open(cache_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                cached_config = data.get("config_path", "")
                if str(self.config_path.resolve()) != str(Path(cached_config).resolve()):
                    logger.warning(
                        "Кеш создан для другого пути конфигурации (%s), пересканирование",
                        cached_config,
                    )
                    return None
                return data["metadata"], data["modules"], data["forms"]
            except Exception as exc:
                logger.warning("Ошибка чтения кеша, будет выполнено сканирование: %s", exc)
        return None

    def _save_scan_cache(self, metadata_list, modules_data, forms_list):
        """Сохраняет результаты сканирования"""
        if not self.use_cache:
            return
        logger.info("Сохранение данных в кеш сканирования: %s", SCAN_CACHE_FILE)
        try:
            serializable_modules = []
            for file_path, object_full_name, methods in modules_data:
                serializable_modules.append(
                    {
                        "file_path": str(file_path),
                        "object_full_name": object_full_name,
                        "methods": methods,
                    }
                )
            data = {
                "config_path": str(self.config_path.resolve()),
                "metadata": metadata_list,
                "modules": serializable_modules,
                "forms": forms_list,
            }
            with open(SCAN_CACHE_FILE, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Не удалось сохранить кеш сканирования: %s", exc)


    def _process_modules_parallel(
        self,
        modules_data: List[Tuple[Path, str, List[Dict]]],
        known_objects: FrozenSet[Tuple[str, str]],
    ) -> List[Dict[str, Any]]:
        pool_args = [
            (file_path, object_full_name, methods, known_objects)
            for file_path, object_full_name, methods in modules_data
        ]
        results: List[Dict[str, Any]] = []
        with Pool(processes=self.workers) as pool:
            for result in tqdm(
                pool.imap_unordered(
                    _process_module_from_cache,
                    pool_args,
                    chunksize=POOL_CHUNKSIZE,
                ),
                total=len(pool_args),
                desc="Modules (extract from cache)",
            ):
                results.append(result)
        return results

    def index_all(self):
        """Полная индексация графа"""
        logger.info("=" * 60)
        logger.info("Начало индексации графа конфигурации 1С")
        logger.info("Путь к конфигурации: %s", self.config_path)
        logger.info("Количество процессов: %s", self.workers)
        logger.info("Модель эмбеддингов: %s", Config.EMBEDDING_MODEL)
        logger.info("API Базовый URL: %s", Config.EMBEDDING_API_BASE)
        logger.info("=" * 60)

        cached_data = self._load_scan_cache()
        if cached_data:
            metadata_list, modules_data_serialized, forms_list = cached_data
            modules_data = [
                (Path(module["file_path"]), module["object_full_name"], module["methods"])
                for module in modules_data_serialized
            ]
            self._fresh_graph_module_results = None
        else:
            metadata_list = self.scanner.scan_all_metadata()
            known_objects_for_scan = frozenset(
                (item.get("object_type_dir", ""), item.get("name", "")) for item in metadata_list
            )
            forms_list = self.scanner.scan_all_forms()
            module_files = self.scanner.list_module_files()

            logger.info(
                "Параллельный парсинг %s BSL-модулей (%s процессов)...",
                len(module_files),
                self.workers,
            )
            pool_args = [
                (str(file_path), object_full_name, known_objects_for_scan)
                for file_path, object_full_name in module_files
            ]
            graph_results: List[Dict[str, Any]] = []
            with Pool(processes=self.workers) as pool:
                for result in tqdm(
                    pool.imap_unordered(_process_bsl_file, pool_args, chunksize=POOL_CHUNKSIZE),
                    total=len(pool_args),
                    desc="Modules (parse+extract)",
                ):
                    if result:
                        graph_results.append(result)

            self._fresh_graph_module_results = graph_results
            modules_data = [
                (Path(item["file_path"]), item["object_full_name"], item["methods"])
                for item in graph_results
            ]
            logger.info(
                "Сканирование завершено: метаданные=%s, модули=%s, формы=%s",
                len(metadata_list),
                len(modules_data),
                len(forms_list),
            )
            self._save_scan_cache(metadata_list, modules_data, forms_list)

        cp = self._load_checkpoint()
        start_meta_idx, start_mod_idx, start_form_idx = 0, 0, 0
        if cp:
            logger.info("Обнаружен чекпоинт: Этап '%s', Индекс %s", cp["stage"], cp["index"])
            if cp["stage"] == "metadata":
                start_meta_idx = cp["index"]
            if cp["stage"] == "modules":
                start_mod_idx = cp["index"]
            if cp["stage"] == "forms":
                start_form_idx = cp["index"]
            if cp["stage"] in ["modules", "forms"]:
                logger.info("Этап метаданных пропущен (уже выполнен)")
                start_meta_idx = len(metadata_list)
            if cp["stage"] == "forms":
                logger.info("Этап модулей пропущен (уже выполнен)")
                start_mod_idx = len(modules_data)

        logger.info("Построение графа...")
        known_objects = frozenset(
            (item.get("object_type_dir", ""), item.get("name", "")) for item in metadata_list
        )

        if start_meta_idx < len(metadata_list):
            logger.info(
                " [1/3] Добавление узлов метаданных (с %s из %s)...",
                start_meta_idx,
                len(metadata_list),
            )
            metadata_nodes = [
                _metadata_node_record(
                    item.get("object_type_dir", "Unknown"),
                    item.get("name", ""),
                    item.get("synonym", ""),
                )
                for item in metadata_list[start_meta_idx:]
            ]
            self.graph.add_nodes_batch(metadata_nodes)
            extra_nodes, extra_edges = _role_and_template_graph_records(metadata_list[start_meta_idx:])
            if extra_nodes:
                self.graph.add_nodes_batch(extra_nodes)
            if extra_edges:
                logger.info("Рёбра прав и макетов СКД: %s", len(extra_edges))
                self.graph.add_edges_batch(extra_edges)
            self._save_checkpoint("metadata", len(metadata_list))
        else:
            logger.info(" [1/3] Метаданные уже обработаны")

        if start_mod_idx < len(modules_data):
            logger.info(
                " [2/3] Добавление методов и связей (с %s из %s)...",
                start_mod_idx,
                len(modules_data),
            )
            if self._fresh_graph_module_results is not None:
                module_results = self._fresh_graph_module_results[start_mod_idx:]
            else:
                modules_slice = modules_data[start_mod_idx:]
                module_results = self._process_modules_parallel(modules_slice, known_objects)

            logger.info("Пакетная запись модулей в графовую БД...")
            _write_module_results_batch(self.graph, module_results)
            self._save_checkpoint("modules", len(modules_data))
        else:
            logger.info(" [2/3] Модули уже обработаны")

        if start_form_idx < len(forms_list):
            logger.info(
                " [3/3] Добавление форм (с %s из %s)...",
                start_form_idx,
                len(forms_list),
            )
            form_nodes: List[Dict[str, Any]] = []
            form_edges: List[Dict[str, Any]] = []
            metadata_for_forms: Dict[str, Dict[str, Any]] = {}

            for form in forms_list[start_form_idx:]:
                obj_type = form.get("object_type", "Unknown")
                obj_name = form.get("object_name", "")
                form_name = form.get("form_name", "")
                source_id = f"metadata:{obj_type}:{obj_name}"
                metadata_for_forms[source_id] = _metadata_node_record(obj_type, obj_name)
                form_id = f"form:{obj_type}:{obj_name}:{form_name}"
                form_nodes.append(
                    {
                        "node_id": form_id,
                        "node_type": "Form",
                        "name": form_name,
                        "object_type": obj_type,
                        "object_name": obj_name,
                        "extra": {"elements_count": form.get("elements_count", 0)},
                    }
                )
                form_edges.append({"source": source_id, "target": form_id, "edge_type": "HAS_FORM"})

            self.graph.add_nodes_batch(list(metadata_for_forms.values()) + form_nodes)
            self.graph.add_edges_batch(form_edges)
            self._save_checkpoint("forms", len(forms_list))
        else:
            logger.info(" [3/3] Формы уже обработаны")

        if self.staging_mode:
            from compact_graph import compact_staging_to_kuzu

            logger.info("[staging] Финальный compact: staging CSV -> Kuzu COPY...")
            self.graph.write_csv_files()
            compact_result = compact_staging_to_kuzu(
                Path(Config.GRAPH_STAGING_PATH),
                Path(self.db_path),
                buffer_pool_size=getattr(Config, "KUZU_BUFFER_POOL_SIZE", 0),
            )
            logger.info(
                "Kuzu graph.db: %.1f MB (%s узлов, %s рёбер)",
                compact_result["size_mb"],
                compact_result["nodes_count"],
                compact_result["edges_count"],
            )

        self._clear_checkpoint()
        stats = self.graph.get_stats()
        logger.info("=" * 60)
        logger.info("Индексация графа завершена успешно!")
        logger.info("=" * 60)
        logger.info("Узлов: %s, рёбер: %s", stats["nodes_count"], stats["edges_count"])
        logger.info("По типам узлов: %s", stats["nodes_by_type"])
        logger.info("По типам рёбер: %s", stats["edges_by_type"])


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Индексация графа конфигурации 1С")
    parser.add_argument(
        "--config-path",
        type=str,
        default=None,
        help="Путь к выгрузке 1С. Если не задан — CONFIG_PATH или EXTENSION_CONFIG_PATH (при --extension).",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Каталог графовой БД Kuzu. Если не задан — GRAPHDB_PATH или EXTENSION_GRAPHDB_PATH (при --extension).",
    )
    parser.add_argument(
        "--extension",
        action="store_true",
        help="Использовать выгрузку и граф расширения (EXTENSION_*), не трогая БД основной конфигурации.",
    )
    parser.add_argument(
        "--staging",
        action="store_true",
        help="Сбор в staging CSV (без MERGE в Kuzu), затем compact через COPY",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Очистить граф перед индексацией (сбрасывает чекпоинт)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Игнорировать кеш сканирования и пересканировать файлы",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Количество процессов для многопроцессорной обработки (по умолчанию: cpu_count - 1)",
    )
    args = parser.parse_args()

    if args.extension:
        effective_config = args.config_path or (
            Config.EXTENSION_CONFIG_PATH.strip() if Config.EXTENSION_CONFIG_PATH else ""
        )
        effective_graph = args.db_path or Config.EXTENSION_GRAPHDB_PATH
        logger.info("Режим --extension: граф пишется в отдельный файл (EXTENSION_GRAPHDB_PATH).")
    else:
        effective_config = args.config_path or Config.CONFIG_PATH
        effective_graph = args.db_path or Config.GRAPHDB_PATH

    if not effective_config:
        logger.error(
            "Не задан путь к выгрузке. Укажите --config-path или CONFIG_PATH / EXTENSION_CONFIG_PATH (для --extension)."
        )
        sys.exit(1)

    config_path = Path(effective_config)
    if not config_path.exists():
        logger.error("Путь к конфигурации не найден: %s", effective_config)
        sys.exit(1)
    try:
        indexer = GraphIndexer(
            config_path=str(config_path),
            db_path=effective_graph,
            clear_existing=args.clear,
            use_cache=not args.no_cache,
            workers=args.workers,
            staging=args.staging,
        )
        indexer.index_all()
    except Exception as exc:
        if "buffer pool" in str(exc).lower() or "buffer manager" in str(exc).lower():
            logger.error(
                "Ошибка Kuzu (нехватка памяти buffer pool): %s. "
                "Для крупных конфигураций включите GRAPH_USE_STAGING=1 в профиле "
                "или запустите index_graph_mp.py с флагом --staging.",
                exc,
            )
        else:
            logger.error("Ошибка при индексации графа: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
