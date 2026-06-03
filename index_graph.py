"""
[DEPRECATED] Однопроцессорная версия индексатора графа.
Используйте index_graph_mp.py вместо этого файла.

index_graph_mp.py поддерживает тот же функционал + многопроцессорность:
  python index_graph_mp.py --workers 1   # эквивалент этого скрипта
  python index_graph_mp.py --workers 8   # ускоренная параллельная индексация

Этот файл оставлен для обратной совместимости и будет удалён в будущем.
"""
import warnings
import sys
import json
from pathlib import Path

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


class GraphIndexer:
    """Индексатор графа конфигурации 1С"""

    def __init__(self, config_path: str, db_path: str = None, clear_existing: bool = False, use_cache: bool = True):
        self.config_path = Path(config_path)
        self.scanner = ConfigurationScanner(self.config_path)
        self.graph = GraphDBManager(db_path)
        self.use_cache = use_cache

        if clear_existing:
            logger.info("Очистка существующего графа...")
            self.graph.clear()
            self._clear_checkpoint() # При очистке БД сбрасываем и чекпоинт

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
        
        # Логирование модели
        logger.info(f"🤖 Модель эмбеддингов: {Config.EMBEDDING_MODEL}")
        logger.info(f"🌐 API Базовый URL: {Config.EMBEDDING_API_BASE}")
        
        logger.info("=" * 60)

        # 1. Загрузка данных (сканирование или кеш)
        cached_data = self._load_scan_cache()
        
        if cached_data:
            metadata_list, modules_data_serialized, forms_list = cached_data
            modules_data = [
                (Path(m['file_path']), m['object_full_name'], m['methods']) 
                for m in modules_data_serialized
            ]
        else:
            logger.info("🔍 Сканирование файлов конфигурации...")
            metadata_list = self.scanner.scan_all_metadata()
            modules_data = self.scanner.scan_all_modules()
            forms_list = self.scanner.scan_all_forms()
            logger.info("✅ Сканирование завершено")
            self._save_scan_cache(metadata_list, modules_data, forms_list)

        # 2. Загрузка чекпоинта
        cp = self._load_checkpoint()
        start_meta_idx = 0
        start_mod_idx = 0
        start_form_idx = 0
        
        if cp:
            logger.info(f"🚀 Обнаружен чекпоинт: Этап '{cp['stage']}', Индекс {cp['index']}")
            if cp['stage'] == 'metadata': start_meta_idx = cp['index']
            if cp['stage'] == 'modules': start_mod_idx = cp['index']
            if cp['stage'] == 'forms': start_form_idx = cp['index']
            
            # Если мы уже на этапе модулей, метаданные можно пропустить (они быстрые, но пропустим для чистоты)
            if cp['stage'] in ['modules', 'forms']:
                logger.info("⏭️ Этап метаданных пропущен (уже выполнен)")
                start_meta_idx = len(metadata_list) # Указываем, что всё готово

            # Если мы на этапе форм, модули пропускаем
            if cp['stage'] == 'forms':
                logger.info("⏭️ Этап модулей пропущен (уже выполнен)")
                start_mod_idx = len(modules_data)

        # 3. Построение графа
        logger.info("🕸️ Построение графа...")
        known_objects = {(m.get("object_type_dir", ""), m.get("name", "")) for m in metadata_list}

        # ЭТАП 1: Метаданные
        if start_meta_idx < len(metadata_list):
            logger.info(f" [1/3] Добавление узлов метаданных (с {start_meta_idx} из {len(metadata_list)})...")
            for i in range(start_meta_idx, len(metadata_list)):
                m = metadata_list[i]
                obj_type = m.get("object_type_dir", "Unknown")
                obj_name = m.get("name", "")
                self.graph.ensure_metadata_node(
                    object_type=obj_type,
                    object_name=obj_name,
                    synonym=m.get("synonym", ""),
                )
                # Сохраняем чекпоинт каждые 50 элементов
                if i % 50 == 0:
                    self._save_checkpoint('metadata', i + 1)
            self._save_checkpoint('metadata', len(metadata_list))
        else:
            logger.info(" [1/3] Метаданные уже обработаны")

        # ЭТАП 2: Модули
        if start_mod_idx < len(modules_data):
            logger.info(f" [2/3] Добавление методов и связей (с {start_mod_idx} из {len(modules_data)})...")
            
            modules_slice = modules_data[start_mod_idx:]
            
            for i, (file_path, object_full_name, methods) in enumerate(tqdm(modules_slice, initial=start_mod_idx, total=len(modules_data), desc="Modules")):
                real_index = start_mod_idx + i
                
                parts = object_full_name.split(".")
                obj_type = parts[0] if len(parts) > 0 else "Unknown"
                obj_name = parts[1] if len(parts) > 1 else file_path.stem
                
                logger.info(f"   Модуль [{real_index}]: {obj_type}.{obj_name} ({len(methods)} методов)")
                
                source_id = self.graph.ensure_metadata_node(obj_type, obj_name, "")

                for method in methods:
                    method_name = method.get("method_name", "")
                    module_name = file_path.stem
                    method_id = f"method:{obj_type}:{obj_name}:{module_name}:{method_name}"
                    
                    self.graph.add_node(
                        node_id=method_id,
                        node_type="Method",
                        name=method_name,
                        object_type=obj_type,
                        object_name=obj_name,
                        extra={"module": module_name, "signature": method.get("signature", "")},
                    )
                    self.graph.add_edge(source_id, method_id, "HAS_METHOD")

                    refs = BSLParser.extract_metadata_references_from_code(method.get("code", ""))
                    for ref_type, ref_name in refs:
                        if (ref_type, ref_name) in known_objects or ref_type in (
                            "Catalogs", "Documents", "InformationRegisters",
                            "AccumulationRegisters", "CommonModules", "Enums",
                            "DataProcessors", "Reports",
                        ):
                            target_id = self.graph.ensure_metadata_node(ref_type, ref_name, "")
                            self.graph.add_edge(source_id, target_id, "USES_IN_CODE")

                # Сохраняем чекпоинт после каждого модуля
                self._save_checkpoint('modules', real_index + 1)
            
            self._save_checkpoint('modules', len(modules_data))
        else:
            logger.info(" [2/3] Модули уже обработаны")

        # ЭТАП 3: Формы
        if start_form_idx < len(forms_list):
            logger.info(f" [3/3] Добавление форм (с {start_form_idx} из {len(forms_list)})...")
            
            forms_slice = forms_list[start_form_idx:]
            
            for i, form in enumerate(tqdm(forms_slice, initial=start_form_idx, total=len(forms_list), desc="Forms")):
                real_index = start_form_idx + i
                
                obj_type = form.get("object_type", "Unknown")
                obj_name = form.get("object_name", "")
                form_name = form.get("form_name", "")
                
                logger.info(f"   Форма [{real_index}]: {form_name} ({obj_type}.{obj_name})")

                source_id = self.graph.ensure_metadata_node(obj_type, obj_name, "")
                form_id = f"form:{obj_type}:{obj_name}:{form_name}"
                self.graph.add_node(
                    node_id=form_id,
                    node_type="Form",
                    name=form_name,
                    object_type=obj_type,
                    object_name=obj_name,
                    extra={"elements_count": form.get("elements_count", 0)},
                )
                self.graph.add_edge(source_id, form_id, "HAS_FORM")
                
                self._save_checkpoint('forms', real_index + 1)
                
            self._save_checkpoint('forms', len(forms_list))
        else:
            logger.info(" [3/3] Формы уже обработаны")

        # Успешное завершение - удаляем чекпоинт
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
        default=Config.CONFIG_PATH,
        help="Путь к выгрузке конфигурации 1С",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Каталог графовой БД Kuzu (по умолчанию из конфига)",
    )
    parser.add_argument("--clear", action="store_true", help="Очистить граф перед индексацией (сбрасывает чекпоинт)")
    parser.add_argument("--no-cache", action="store_true", help="Игнорировать кеш сканирования и пересканировать файлы")

    args = parser.parse_args()

    config_path = Path(args.config_path)
    if not config_path.exists():
        logger.error(f"Путь к конфигурации не найден: {args.config_path}")
        sys.exit(1)

    try:
        indexer = GraphIndexer(
            config_path=str(config_path),
            db_path=args.db_path,
            clear_existing=args.clear,
            use_cache=not args.no_cache
        )
        indexer.index_all()
    except Exception as e:
        logger.error(f"❌ Ошибка при индексации графа: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    warnings.warn(
        "index_graph.py устарел. Используйте index_graph_mp.py --workers 1 "
        "для однопроцессорного режима.",
        DeprecationWarning,
        stacklevel=1,
    )
    main()