"""
Скрипт индексации графа связей конфигурации 1С.
Поддерживает кеширование сканирования и чекпоинты для продолжения с места остановки.
Поддерживает многопроцессорность для ускорения индексации.
"""
import sys
import json
from pathlib import Path
from typing import List, Tuple, Dict, Any, FrozenSet
from multiprocessing import Pool, cpu_count

# Добавляем текущую директорию в путь поиска модулей
sys.path.insert(0, str(Path(__file__).parent))

import logging
from tqdm import tqdm
from config import Config
from graph_db import GraphDBManager
from parser_1c import BSLParser, ConfigurationScanner

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("indexing.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Файлы для управления состоянием
SCAN_CACHE_FILE = "graph_scan_cache.json"
CHECKPOINT_FILE = "graph_checkpoint.json"


def _process_module(args: Tuple[Path, str, List[Dict], FrozenSet[Tuple[str, str]]]) -> Dict[str, Any]:
    """Обработка одного модуля в отдельном процессе."""
    file_path, object_full_name, methods, known_objects = args
    
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
        
        method_nodes.append({
            "node_id": method_id,
            "node_type": "Method",
            "name": method_name,
            "object_type": obj_type,
            "object_name": obj_name,
            "extra": {"module": module_name, "signature": method.get("signature", "")}
        })
        
        edges.append({"source": source_id, "target": method_id, "edge_type": "HAS_METHOD"})
        
        refs = BSLParser.extract_metadata_references_from_code(method.get("code", ""))
        for ref_type, ref_name in refs:
            if (ref_type, ref_name) in known_objects or ref_type in (
                "Catalogs", "Documents", "InformationRegisters",
                "AccumulationRegisters", "CommonModules", "Enums",
                "DataProcessors", "Reports",
            ):
                target_id = f"metadata:{ref_type}:{ref_name}"
                edges.append({"source": source_id, "target": target_id, "edge_type": "USES_IN_CODE"})
    
    return {
        "source_id": source_id,
        "obj_type": obj_type,
        "obj_name": obj_name,
        "method_count": len(methods),
        "method_nodes": method_nodes,
        "edges": edges
    }


class GraphIndexer:
    """Индексатор графа конфигурации 1С с поддержкой многопроцессорности"""

    def __init__(self, config_path: str, db_path: str = None, clear_existing: bool = False, use_cache: bool = True, workers: int = None):
        self.config_path = Path(config_path)
        self.scanner = ConfigurationScanner(self.config_path)
        self.graph = GraphDBManager(db_path)
        self.use_cache = use_cache
        self.workers = workers or max(1, cpu_count() - 1)
        
        if clear_existing:
            logger.info("Очистка существующего графа...")
            self.graph.clear()
            self._clear_checkpoint()

    def _clear_checkpoint(self):
        """Удаляет файл чекпоинта"""
        if Path(CHECKPOINT_FILE).exists():
            Path(CHECKPOINT_FILE).unlink()
            logger.info("🗑️ Чекпоинт сброшен")

    def _save_checkpoint(self, stage: str, index: int):
        """Сохраняет текущий прогресс"""
        try:
            with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
                json.dump({"stage": stage, "index": index}, f)
        except Exception as e:
            logger.warning(f"Не удалось сохранить чекпоинт: {e}")

    def _load_checkpoint(self):
        """Загружает прогресс из файла"""
        if Path(CHECKPOINT_FILE).exists():
            try:
                with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Ошибка чтения чекпоинта: {e}")
        return None

    def _load_scan_cache(self):
        """Загружает данные из кеша сканирования"""
        cache_path = Path(SCAN_CACHE_FILE)
        if self.use_cache and cache_path.exists():
            logger.info(f"📦 Загрузка данных из кеша сканирования: {cache_path}")
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                cached_config = data.get("config_path", "")
                if str(self.config_path.resolve()) != str(Path(cached_config).resolve()):
                    logger.warning(f"Кеш создан для другого пути конфигурации ({cached_config}), пересканирование")
                    return None
                return data['metadata'], data['modules'], data['forms']
            except Exception as e:
                logger.warning(f"Ошибка чтения кеша, будет выполнено сканирование: {e}")
        return None

    def _save_scan_cache(self, metadata_list, modules_data, forms_list):
        """Сохраняет результаты сканирования"""
        if not self.use_cache:
            return
        logger.info(f"💾 Сохранение данных в кеш сканирования: {SCAN_CACHE_FILE}")
        try:
            serializable_modules = []
            for file_path, object_full_name, methods in modules_data:
                serializable_modules.append({
                    "file_path": str(file_path),
                    "object_full_name": object_full_name,
                    "methods": methods
                })
            data = {
                "config_path": str(self.config_path.resolve()),
                "metadata": metadata_list,
                "modules": serializable_modules,
                "forms": forms_list
            }
            with open(SCAN_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Не удалось сохранить кеш сканирования: {e}")

    def index_all(self):
        """Полная индексация графа"""
        logger.info("=" * 60)
        logger.info("Начало индексации графа конфигурации 1С")
        logger.info(f"Путь к конфигурации: {self.config_path}")
        logger.info(f"⚙️ Количество процессов: {self.workers}")
        logger.info(f"🤖 Модель эмбеддингов: {Config.EMBEDDING_MODEL}")
        logger.info(f"🌐 API Базовый URL: {Config.EMBEDDING_API_BASE}")
        logger.info("=" * 60)

        cached_data = self._load_scan_cache()
        if cached_data:
            metadata_list, modules_data_serialized, forms_list = cached_data
            modules_data = [(Path(m['file_path']), m['object_full_name'], m['methods']) for m in modules_data_serialized]
        else:
            logger.info("🔍 Сканирование файлов конфигурации...")
            metadata_list = self.scanner.scan_all_metadata()
            modules_data = self.scanner.scan_all_modules()
            forms_list = self.scanner.scan_all_forms()
            logger.info("✅ Сканирование завершено")
            self._save_scan_cache(metadata_list, modules_data, forms_list)

        cp = self._load_checkpoint()
        start_meta_idx, start_mod_idx, start_form_idx = 0, 0, 0
        if cp:
            logger.info(f"🚀 Обнаружен чекпоинт: Этап '{cp['stage']}', Индекс {cp['index']}")
            if cp['stage'] == 'metadata': start_meta_idx = cp['index']
            if cp['stage'] == 'modules': start_mod_idx = cp['index']
            if cp['stage'] == 'forms': start_form_idx = cp['index']
            if cp['stage'] in ['modules', 'forms']:
                logger.info("⏭️ Этап метаданных пропущен (уже выполнен)")
                start_meta_idx = len(metadata_list)
            if cp['stage'] == 'forms':
                logger.info("⏭️ Этап модулей пропущен (уже выполнен)")
                start_mod_idx = len(modules_data)

        logger.info("🕸️ Построение графа...")
        known_objects = frozenset((m.get("object_type_dir", ""), m.get("name", "")) for m in metadata_list)

        if start_meta_idx < len(metadata_list):
            logger.info(f" [1/3] Добавление узлов метаданных (с {start_meta_idx} из {len(metadata_list)})...")
            for i in range(start_meta_idx, len(metadata_list)):
                m = metadata_list[i]
                obj_type = m.get("object_type_dir", "Unknown")
                obj_name = m.get("name", "")
                self.graph.ensure_metadata_node(object_type=obj_type, object_name=obj_name, synonym=m.get("synonym", ""))
                if i % 50 == 0:
                    self._save_checkpoint('metadata', i + 1)
            self._save_checkpoint('metadata', len(metadata_list))
        else:
            logger.info(" [1/3] Метаданные уже обработаны")

        if start_mod_idx < len(modules_data):
            logger.info(f" [2/3] Добавление методов и связей (с {start_mod_idx} из {len(modules_data)})...")
            modules_slice = modules_data[start_mod_idx:]
            args = [(file_path, object_full_name, methods, known_objects) for file_path, object_full_name, methods in modules_slice]
            
            with Pool(processes=self.workers) as pool:
                results = list(tqdm(pool.imap(_process_module, args), total=len(modules_data), initial=start_mod_idx, desc="Modules (multiprocessing)"))
            
            logger.info("💾 Запись результатов в графовую БД...")
            for i, result in enumerate(tqdm(results, desc="Saving to DB")):
                real_index = start_mod_idx + i
                self.graph.ensure_metadata_node(object_type=result["obj_type"], object_name=result["obj_name"], synonym="")
                for method_node in result["method_nodes"]:
                    self.graph.add_node(node_id=method_node["node_id"], node_type=method_node["node_type"], name=method_node["name"], object_type=method_node["object_type"], object_name=method_node["object_name"], extra=method_node["extra"])
                for edge in result["edges"]:
                    target_id = edge["target"]
                    if target_id.startswith("metadata:") and target_id.count(":") >= 2:
                        _, t_type, t_name = target_id.split(":", 2)
                        self.graph.ensure_metadata_node(object_type=t_type, object_name=t_name, synonym="")
                    self.graph.add_edge(edge["source"], edge["target"], edge["edge_type"])
                self._save_checkpoint('modules', real_index + 1)
            self._save_checkpoint('modules', len(modules_data))
        else:
            logger.info(" [2/3] Модули уже обработаны")

        if start_form_idx < len(forms_list):
            logger.info(f" [3/3] Добавление форм (с {start_form_idx} из {len(forms_list)})...")
            for i, form in enumerate(tqdm(forms_list[start_form_idx:], initial=start_form_idx, total=len(forms_list), desc="Forms")):
                real_index = start_form_idx + i
                obj_type = form.get("object_type", "Unknown")
                obj_name = form.get("object_name", "")
                form_name = form.get("form_name", "")
                source_id = self.graph.ensure_metadata_node(obj_type, obj_name, "")
                form_id = f"form:{obj_type}:{obj_name}:{form_name}"
                self.graph.add_node(node_id=form_id, node_type="Form", name=form_name, object_type=obj_type, object_name=obj_name, extra={"elements_count": form.get("elements_count", 0)})
                self.graph.add_edge(source_id, form_id, "HAS_FORM")
                self._save_checkpoint('forms', real_index + 1)
            self._save_checkpoint('forms', len(forms_list))
        else:
            logger.info(" [3/3] Формы уже обработаны")

        self._clear_checkpoint()
        stats = self.graph.get_stats()
        logger.info("=" * 60)
        logger.info("✅ Индексация графа завершена успешно!")
        logger.info("=" * 60)
        logger.info(f"Узлов: {stats['nodes_count']}, рёбер: {stats['edges_count']}")
        logger.info(f"По типам узлов: {stats['nodes_by_type']}")
        logger.info(f"По типам рёбер: {stats['edges_by_type']}")


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
        help="Файл SQLite графовой БД. Если не задан — GRAPHDB_PATH или EXTENSION_GRAPHDB_PATH (при --extension).",
    )
    parser.add_argument(
        "--extension",
        action="store_true",
        help="Использовать выгрузку и граф расширения (EXTENSION_*), не трогая БД основной конфигурации.",
    )
    parser.add_argument("--clear", action="store_true", help="Очистить граф перед индексацией (сбрасывает чекпоинт)")
    parser.add_argument("--no-cache", action="store_true", help="Игнорировать кеш сканирования и пересканировать файлы")
    parser.add_argument("--workers", type=int, default=None, help="Количество процессов для многопроцессорной обработки (по умолчанию: cpu_count - 1)")
    args = parser.parse_args()

    if args.extension:
        effective_config = args.config_path or (Config.EXTENSION_CONFIG_PATH.strip() if Config.EXTENSION_CONFIG_PATH else "")
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
        logger.error(f"Путь к конфигурации не найден: {effective_config}")
        sys.exit(1)
    try:
        indexer = GraphIndexer(
            config_path=str(config_path),
            db_path=effective_graph,
            clear_existing=args.clear,
            use_cache=not args.no_cache,
            workers=args.workers,
        )
        indexer.index_all()
    except Exception as e:
        logger.error(f"❌ Ошибка при индексации графа: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
