"""
Скрипт индексации конфигурации 1С в векторную БД
"""
import logging
import sys
from pathlib import Path
from typing import List, Dict, Optional
import argparse
from tqdm import tqdm

from config import Config
from config_dump import read_configuration_dump_info
from parser_1c import ConfigurationScanner, BSLParser
from vectordb_manager import VectorDBManager

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('indexing.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class ConfigIndexer:
    """Индексатор конфигурации 1С"""

    def __init__(
        self,
        config_path: str,
        db_path: str = None,
        clear_existing: bool = False,
        graph_db_path: Optional[str] = None,
    ):
        self.config_path = Path(config_path)
        self.scanner = ConfigurationScanner(self.config_path)
        self.db_manager = VectorDBManager(db_path)
        self.graph_db_path = graph_db_path if graph_db_path is not None else Config.GRAPHDB_PATH

        if clear_existing:
            logger.info("Очистка существующей векторной БД...")
            self.db_manager.clear_all_collections()

    def index_all(self, vector_only: bool = False):
        """Полная индексация конфигурации"""
        logger.info("=" * 80)
        logger.info("Начало индексации конфигурации 1С")
        logger.info(f"Путь к конфигурации: {self.config_path}")
        dump_info = read_configuration_dump_info(self.config_path)
        if dump_info.get("valid"):
            kind = "расширение конфигурации" if dump_info.get("is_extension") else "основная конфигурация"
            logger.info(f"Тип выгрузки: {kind}")
            if dump_info.get("configuration_name"):
                logger.info(f"Имя в выгрузке: {dump_info['configuration_name']}")
        if vector_only:
            logger.info("Режим: только векторная БД (граф пропускается)")
        logger.info("=" * 80)

        logger.info("\n[1/4] Индексация модулей и кода...")
        code_chunks = self._index_code()

        logger.info("\n[2/4] Индексация метаданных объектов...")
        metadata_count = self._index_metadata()

        logger.info("\n[3/4] Индексация форм...")
        forms_count = self._index_forms()

        graph_stats = {"nodes_count": 0, "edges_count": 0}
        if not vector_only:
            logger.info("\n[4/4] Индексация графа связей...")
            graph_stats = self._index_graph()
        else:
            logger.info("\n[4/4] Граф пропущен (--vector-only)")

        logger.info("\n" + "=" * 80)
        logger.info("Индексация завершена!")
        logger.info("=" * 80)
        logger.info(f"Проиндексировано кода: {code_chunks} чанков")
        logger.info(f"Проиндексировано метаданных: {metadata_count} объектов")
        logger.info(f"Проиндексировано форм: {forms_count}")
        logger.info(f"Граф: {graph_stats.get('nodes_count', 0)} узлов, {graph_stats.get('edges_count', 0)} рёбер")

        stats = self.db_manager.get_stats()
        logger.info("\nСтатистика векторной БД:")
        for collection, count in stats.items():
            logger.info(f"  {collection}: {count} записей")

        logger.info("\n✅ Конфигурация готова к использованию!")

    def _index_code(self) -> int:
        """Индексация кода модулей"""
        logger.info("Сканирование BSL модулей...")
        modules_data = self.scanner.scan_all_modules()

        if not modules_data:
            logger.warning("Не найдено ни одного BSL модуля!")
            return 0

        logger.info(f"Найдено {len(modules_data)} файлов с кодом")

        all_chunks = []
        max_chars = Config.CHUNK_MAX_CHARS if Config.CHUNK_MAX_CHARS > 0 else 2048
        overlap_chars = Config.CHUNK_OVERLAP_CHARS

        for file_path, object_full_name, methods in tqdm(modules_data, desc="Обработка модулей"):
            parts = object_full_name.split('.')
            object_type = parts[0] if len(parts) > 0 else "Unknown"
            object_name = parts[1] if len(parts) > 1 else "Unknown"
            module_name = file_path.stem

            for method in methods:
                base_chunk = {
                    "object_name": object_name,
                    "object_type": object_type,
                    "module_name": module_name,
                    "method_name": method["method_name"],
                    "method_type": method["method_type"],
                    "signature": method["signature"],
                    "is_export": method["is_export"],
                    "code": method["code"],
                    "comments": method.get("comments", []),
                    "file_path": str(file_path)
                }
                sub_chunks = self._split_method_if_needed(base_chunk, max_chars, overlap_chars)
                all_chunks.extend(sub_chunks)

        logger.info(f"Добавление {len(all_chunks)} чанков кода в векторную БД...")
        self.db_manager.add_code_chunks(all_chunks)

        return len(all_chunks)

    def _split_method_if_needed(
        self,
        chunk: Dict,
        max_chars: int,
        overlap_chars: int
    ) -> List[Dict]:
        """Разбивает длинный метод на чанки с нахлёстом."""
        code = chunk["code"]
        if len(code) <= max_chars:
            chunk["chunk_index"] = 0
            chunk["total_chunks"] = 1
            return [chunk]

        lines = code.split("\n")
        if len(lines) <= 1:
            chunk["chunk_index"] = 0
            chunk["total_chunks"] = 1
            return [chunk]

        header = lines[0]
        footer = lines[-1]
        body_lines = lines[1:-1]
        body_text = "\n".join(body_lines)
        if len(body_text) <= max_chars:
            chunk["chunk_index"] = 0
            chunk["total_chunks"] = 1
            return [chunk]

        overlap_lines = max(1, overlap_chars // 40)
        chunk_bodies = []
        current = []
        current_len = 0

        for line in body_lines:
            line_len = len(line) + 1
            if current_len + line_len > max_chars and current:
                chunk_bodies.append("\n".join(current))
                overlap = current[-overlap_lines:] if len(current) >= overlap_lines else current
                current = overlap
                current_len = sum(len(ln) + 1 for ln in overlap)
            current.append(line)
            current_len += line_len

        if current:
            chunk_bodies.append("\n".join(current))

        result = []
        for i, body_part in enumerate(chunk_bodies):
            full_code = header + "\n" + body_part + "\n" + footer
            sub = {
                **chunk,
                "code": full_code,
                "chunk_index": i,
                "total_chunks": len(chunk_bodies),
            }
            result.append(sub)
        return result

    def _index_metadata(self) -> int:
        """Индексация метаданных"""
        logger.info("Сканирование метаданных объектов...")
        metadata_objects = self.scanner.scan_all_metadata()

        if not metadata_objects:
            logger.warning("Не найдено объектов метаданных!")
            return 0

        logger.info(f"Найдено {len(metadata_objects)} объектов метаданных")
        logger.info(f"Добавление {len(metadata_objects)} объектов в векторную БД...")
        self.db_manager.add_metadata_objects(metadata_objects)

        return len(metadata_objects)

    def _index_forms(self) -> int:
        """Индексация форм"""
        logger.info("Сканирование форм...")
        forms = self.scanner.scan_all_forms()

        if not forms:
            logger.warning("Не найдено форм!")
            return 0

        logger.info(f"Найдено {len(forms)} форм")
        logger.info(f"Добавление {len(forms)} форм в векторную БД...")
        self.db_manager.add_forms(forms)

        return len(forms)

    def _index_graph(self) -> Dict:
        """Индексация графа связей между объектами"""
        try:
            from index_graph import GraphIndexer
            graph_path = Path(self.graph_db_path).parent
            graph_path.mkdir(parents=True, exist_ok=True)
            graph_indexer = GraphIndexer(
                config_path=str(self.config_path),
                db_path=self.graph_db_path,
                clear_existing=True,
            )
            graph_indexer.index_all()
            return graph_indexer.graph.get_stats()
        except Exception as e:
            logger.warning(f"Ошибка индексации графа (продолжаем): {e}")
            return {"nodes_count": 0, "edges_count": 0}


def main():
    """Главная функция"""
    parser = argparse.ArgumentParser(
        description='Индексация конфигурации 1С в векторную БД для MCP сервера'
    )
    parser.add_argument(
        '--config-path',
        type=str,
        default=None,
        help='Путь к выгрузке 1С (основная конфигурация или расширение). Если не задан — из профиля.',
    )
    parser.add_argument(
        '--db-path',
        type=str,
        default=None,
        help='Каталог векторной БД (Chroma). Если не задан — из профиля.',
    )
    parser.add_argument(
        '--graph-db-path',
        type=str,
        default=None,
        help='Файл SQLite графовой БД. Если не задан — GRAPHDB_PATH или EXTENSION_GRAPHDB_PATH (при --extension).',
    )
    parser.add_argument(
        '--extension',
        action='store_true',
        help=(
            'Индексировать выгрузку расширения в отдельные БД: EXTENSION_CONFIG_PATH, '
            'EXTENSION_VECTORDB_PATH, EXTENSION_GRAPHDB_PATH (не затрагивает основную конфигурацию).'
        ),
    )
    parser.add_argument(
        '--clear',
        action='store_true',
        help='Очистить существующую БД перед индексацией'
    )
    parser.add_argument(
        '--vector-only',
        action='store_true',
        help='Индексировать только векторную БД (код, метаданные, формы); граф пропускается'
    )

    args = parser.parse_args()

    if args.extension:
        effective_config = args.config_path or (Config.EXTENSION_CONFIG_PATH.strip() if Config.EXTENSION_CONFIG_PATH else "")
        effective_vector = args.db_path or Config.EXTENSION_VECTORDB_PATH
        effective_graph = args.graph_db_path or Config.EXTENSION_GRAPHDB_PATH
        logger.info("Режим --extension: используются отдельные БД расширения (основная конфигурация не изменяется).")
    else:
        effective_config = args.config_path or Config.CONFIG_PATH
        effective_vector = args.db_path or Config.VECTORDB_PATH
        effective_graph = args.graph_db_path or Config.GRAPHDB_PATH

    if not effective_config:
        logger.error(
            "Не задан путь к выгрузке. Укажите --config-path или в профиле "
            "CONFIG_PATH (основная конфигурация) / EXTENSION_CONFIG_PATH (режим --extension)."
        )
        sys.exit(1)

    config_path = Path(effective_config)
    if not config_path.exists():
        logger.error(f"Путь к конфигурации не найден: {effective_config}")
        logger.error("Укажите правильный путь через --config-path или переменные окружения профиля.")
        sys.exit(1)

    if not (config_path / "Configuration.xml").exists():
        logger.error(f"Не найден Configuration.xml в {effective_config}")
        logger.error(
            "Укажите корень выгрузки 1С: основная конфигурация или расширение "
            "(оба варианта содержат Configuration.xml в корне)."
        )
        sys.exit(1)

    if args.extension:
        ext_dump = read_configuration_dump_info(config_path)
        if ext_dump.get("valid") and not ext_dump.get("is_extension"):
            logger.warning(
                "Корневой Configuration.xml не распознан как расширение. "
                "Проверьте путь; индексация в БД расширения будет продолжена."
            )

    try:
        indexer = ConfigIndexer(
            config_path=effective_config,
            db_path=effective_vector,
            clear_existing=args.clear,
            graph_db_path=effective_graph,
        )
        indexer.index_all(vector_only=args.vector_only)
    except KeyboardInterrupt:
        logger.warning("\nИндексация прервана пользователем")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Ошибка при индексации: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
