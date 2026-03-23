import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

# Определяем корневую директорию проекта
PROJECT_ROOT = Path(__file__).parent


def load_profile(profile_name: str = None):
    """
    Загружает конфигурацию профиля

    Args:
        profile_name: Имя профиля (например, 'your_project', 'gisu', 'erp')
                     Если None, используется переменная окружения PROJECT_PROFILE
                     или дефолтный профиль
    """
    if profile_name is None:
        profile_name = os.getenv("PROJECT_PROFILE", "default")

    # Поддержка вложенных путей: esty\osn → projects/esty/osn/osn.env
    profile_path = PROJECT_ROOT / "projects" / profile_name / f"{Path(profile_name).name}.env"

    if not profile_path.exists():
        logger.warning(f"Профиль '{profile_name}' не найден по пути {profile_path}")
        logger.info("Используются переменные окружения по умолчанию")
    else:
        logger.info(f"Загружен профиль: {profile_name} из {profile_path}")
        load_dotenv(profile_path, override=True)

    profile_local = profile_path.parent / f"{Path(profile_name).name}.env.local"
    if profile_local.exists():
        load_dotenv(profile_local, override=True)
        logger.info(f"Применены переопределения из {profile_local}")

    return profile_name


current_profile = load_profile()


KNOWN_MODELS = {
    "nomic-ai/nomic-embed-text-v1.5":              {"dim": 768,  "max_tokens": 8192},
    "nomic-ai/nomic-embed-text-v1":                {"dim": 768,  "max_tokens": 8192},
    "nomic-embed-text-v1.5":                       {"dim": 768,  "max_tokens": 8192},
    "nomic-embed-text-v1":                         {"dim": 768,  "max_tokens": 8192},
    "nomic-embed-text-v2-moe":                     {"dim": 768,  "max_tokens": 512},
    "sentence-transformers/paraphrase-multilingual-minilm-l12-v2": {"dim": 384, "max_tokens": 512},
    "paraphrase-multilingual-minilm-l12-v2":       {"dim": 384,  "max_tokens": 512},
    "sentence-transformers/all-minilm-l6-v2":      {"dim": 384,  "max_tokens": 256},
    "all-minilm-l6-v2":                            {"dim": 384,  "max_tokens": 256},
    "baai/bge-m3":                                 {"dim": 1024, "max_tokens": 8192},
    "bge-m3":                                      {"dim": 1024, "max_tokens": 8192},
    "baai/bge-small-en-v1.5":                      {"dim": 384,  "max_tokens": 512},
    "baai/bge-base-en-v1.5":                       {"dim": 768,  "max_tokens": 512},
    "baai/bge-large-en-v1.5":                      {"dim": 1024, "max_tokens": 512},
    "intfloat/multilingual-e5-large":              {"dim": 1024, "max_tokens": 512},
    "intfloat/multilingual-e5-base":               {"dim": 768,  "max_tokens": 512},
    "intfloat/multilingual-e5-small":              {"dim": 384,  "max_tokens": 512},
    "intfloat/e5-mistral-7b-instruct":             {"dim": 4096, "max_tokens": 32768},
    "jinaai/jina-embeddings-v3":                   {"dim": 1024, "max_tokens": 8192},
    "jinaai/jina-embeddings-v2-base-en":           {"dim": 768,  "max_tokens": 8192},
    "cohere/embed-multilingual-v3.0":              {"dim": 1024, "max_tokens": 512},
    "cohere/embed-english-v3.0":                   {"dim": 1024, "max_tokens": 512},
    "text-embedding-ada-002":                      {"dim": 1536, "max_tokens": 8191},
    "text-embedding-3-small":                      {"dim": 1536, "max_tokens": 8191},
    "text-embedding-3-large":                      {"dim": 3072, "max_tokens": 8191},
    "ibm-granite/granite-embedding-125m-english":  {"dim": 768,  "max_tokens": 512},
    "ibm-granite/granite-embedding-30m-english":   {"dim": 384,  "max_tokens": 512},
}

QWEN3_DIMENSIONS = {
    "0.6b": 1024,
    "1.7b": 2048,
    "4b":   2560,
    "8b":   4096,
}


def _resolve_embedding_dimension(model_name: str, env_dim: str) -> int:
    """Определяет размерность эмбеддингов: env > таблица > Qwen3-эвристика > 768."""
    if env_dim and env_dim.isdigit() and int(env_dim) > 0:
        return int(env_dim)

    model_lower = model_name.lower().strip()

    for pattern, info in KNOWN_MODELS.items():
        if pattern in model_lower or model_lower.endswith(pattern):
            return info["dim"]

    if "qwen3" in model_lower:
        for size_key, dim in QWEN3_DIMENSIONS.items():
            if size_key in model_lower:
                return dim
        return 2560

    return 768


def _resolve_max_tokens(model_name: str) -> int:
    """Определяет макс. токенов модели из таблицы (0 = не определено)."""
    model_lower = model_name.lower().strip()
    for pattern, info in KNOWN_MODELS.items():
        if pattern in model_lower or model_lower.endswith(pattern):
            return info["max_tokens"]
    if "qwen3" in model_lower:
        return 32768
    return 0


class Config:
    """Настройки приложения"""

    PROFILE_NAME = current_profile
    PROFILE_DIR = PROJECT_ROOT / "projects" / current_profile

    CONFIG_PATH = os.getenv("CONFIG_PATH", "")
    VECTORDB_PATH = os.getenv(
        "VECTORDB_PATH",
        str(PROFILE_DIR / "vectordb")
    )
    GRAPHDB_PATH = os.getenv(
        "GRAPHDB_PATH",
        str(PROFILE_DIR / "graphdb" / "graph.db")
    )

    EMBEDDING_MODEL = os.getenv(
        "EMBEDDING_MODEL",
        "your-embedding-model-name"
    )
    EMBEDDING_DIMENSION = _resolve_embedding_dimension(
        EMBEDDING_MODEL, os.getenv("EMBEDDING_DIMENSION", "")
    )
    EMBEDDING_API_BASE = os.getenv("EMBEDDING_API_BASE", "")
    EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "dummy")
    EMBEDDING_ADD_EOS_MANUAL = os.getenv("EMBEDDING_ADD_EOS_MANUAL", "false").lower() in ("true", "1", "yes")
    # Эвристика «символов на токен» для перевода лимитов токенов в обрезку по длине строки.
    # 2.0 близко к латинице; для русского и длинных идентификаторов BSL токенов на строку больше — ставьте 1.5–1.7,
    # иначе движок (LM Studio / llama.cpp) тихо обрежет после лимита модели.
    _chars_per_token_env = os.getenv("CHARS_PER_TOKEN", "1.65").strip().replace(",", ".")
    try:
        CHARS_PER_TOKEN = float(_chars_per_token_env)
    except ValueError:
        CHARS_PER_TOKEN = 1.65
    _chunk_max_tokens = os.getenv("CHUNK_MAX_TOKENS", "")
    _chunk_max_chars_env = os.getenv("CHUNK_MAX_CHARS", "0")
    CHUNK_MAX_TOKENS = int(_chunk_max_tokens) if _chunk_max_tokens and _chunk_max_tokens.isdigit() else 0
    _chunk_max_chars_parsed = (
        int(_chunk_max_chars_env)
        if _chunk_max_chars_env and _chunk_max_chars_env.isdigit()
        else 0
    )
    CHUNK_MAX_CHARS = (
        int(CHUNK_MAX_TOKENS * CHARS_PER_TOKEN) if CHUNK_MAX_TOKENS
        else _chunk_max_chars_parsed if _chunk_max_chars_parsed > 0
        else int(512 * CHARS_PER_TOKEN)
    )
    CHUNK_OVERLAP_TOKENS = int(os.getenv("CHUNK_OVERLAP_TOKENS", "100"))
    CHUNK_OVERLAP_CHARS = int(CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN)
    _max_tokens_str = os.getenv("EMBEDDING_MAX_TOKENS", "")
    _max_chars_env = os.getenv("EMBEDDING_MAX_CHARS", "0")
    EMBEDDING_MAX_TOKENS = int(_max_tokens_str) if _max_tokens_str and _max_tokens_str.isdigit() else 0
    EMBEDDING_MAX_CHARS = (
        int(EMBEDDING_MAX_TOKENS * CHARS_PER_TOKEN) if EMBEDDING_MAX_TOKENS
        else int(_max_chars_env)
    )

    COLLECTION_CODE = "1c_code"
    COLLECTION_METADATA = "1c_metadata"
    COLLECTION_FORMS = "1c_forms"

    COLLECTIONS = {
        "code": COLLECTION_CODE,
        "metadata": COLLECTION_METADATA,
        "forms": COLLECTION_FORMS
    }

    METADATA_TYPES = {
        "Справочник": "Catalogs",
        "Документ": "Documents",
        "РегистрСведений": "InformationRegisters",
        "РегистрНакопления": "AccumulationRegisters",
        "РегистрБухгалтерии": "AccountingRegisters",
        "Обработка": "DataProcessors",
        "Отчет": "Reports",
        "ОбщийМодуль": "CommonModules",
        "Перечисление": "Enums",
        "ПланСчетов": "ChartsOfAccounts"
    }

    BATCH_SIZE_CODE = int(os.getenv("BATCH_SIZE_CODE", "100"))
    BATCH_SIZE_METADATA = int(os.getenv("BATCH_SIZE_METADATA", "50"))
    BATCH_SIZE_FORMS = int(os.getenv("BATCH_SIZE_FORMS", "50"))

    DEFAULT_SEARCH_LIMIT = int(os.getenv("DEFAULT_SEARCH_LIMIT", "5"))
    MAX_SEARCH_LIMIT = int(os.getenv("MAX_SEARCH_LIMIT", "20"))

    VECTOR_DISTANCE_METRIC = os.getenv("VECTOR_DISTANCE_METRIC", "cosine")
    HYBRID_SEARCH_ALPHA = float(os.getenv("HYBRID_SEARCH_ALPHA", "0.7"))
    SEARCH_USE_MMR = os.getenv("SEARCH_USE_MMR", "true").lower() in ("true", "1", "yes")
    MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.7"))
    SEARCH_FETCH_K = int(os.getenv("SEARCH_FETCH_K", "50"))

    LOG_LEVEL = (os.getenv("LOG_LEVEL", "INFO") or "INFO").upper()

    @classmethod
    def validate(cls):
        """Валидация конфигурации"""
        errors = []

        if not cls.CONFIG_PATH:
            errors.append("CONFIG_PATH не установлен")
        elif not Path(cls.CONFIG_PATH).exists():
            errors.append(f"Путь к конфигурации не существует: {cls.CONFIG_PATH}")

        if errors:
            logger.error("Ошибки конфигурации:")
            for error in errors:
                logger.error(f"  - {error}")
            return False

        logger.info(f"Конфигурация валидна для профиля '{cls.PROFILE_NAME}'")
        return True

    @classmethod
    def show(cls):
        """Показать текущую конфигурацию"""
        logger.info("=" * 60)
        logger.info(f"ПРОФИЛЬ: {cls.PROFILE_NAME}")
        logger.info("=" * 60)
        logger.info(f"Директория профиля: {cls.PROFILE_DIR}")
        logger.info(f"Путь к конфигурации 1С: {cls.CONFIG_PATH}")
        logger.info(f"Путь к векторной БД: {cls.VECTORDB_PATH}")
        logger.info(f"Путь к графовой БД: {cls.GRAPHDB_PATH}")
        logger.info(f"Модель эмбеддингов: {cls.EMBEDDING_MODEL}")
        dim_source = "env" if os.getenv("EMBEDDING_DIMENSION", "").strip().isdigit() else "auto"
        logger.info(f"Размерность эмбеддингов: {cls.EMBEDDING_DIMENSION} ({dim_source})")
        if cls.EMBEDDING_API_BASE:
            logger.info(f"API эмбеддингов: {cls.EMBEDDING_API_BASE}")
        if "qwen3" in cls.EMBEDDING_MODEL.lower():
            if cls.EMBEDDING_ADD_EOS_MANUAL:
                logger.warning(
                    f"EMBEDDING_ADD_EOS_MANUAL=true: llama.cpp уже добавляет EOS автоматически, "
                    f"возможен двойной EOS. Рекомендуется установить false."
                )
            else:
                logger.info("EMBEDDING_ADD_EOS_MANUAL: false (EOS добавляется llama.cpp автоматически)")
        logger.info(f"CHARS_PER_TOKEN (эвристика для лимитов): {cls.CHARS_PER_TOKEN}")
        if cls.EMBEDDING_MAX_TOKENS:
            logger.info(f"EMBEDDING_MAX_TOKENS: {cls.EMBEDDING_MAX_TOKENS} → EMBEDDING_MAX_CHARS: {cls.EMBEDDING_MAX_CHARS}")
        elif cls.EMBEDDING_MAX_CHARS > 0:
            logger.info(f"EMBEDDING_MAX_CHARS (обрезание чанков): {cls.EMBEDDING_MAX_CHARS}")
        chunk_info = f"макс. {cls.CHUNK_MAX_CHARS} символов"
        if cls.CHUNK_MAX_TOKENS:
            chunk_info = f"макс. {cls.CHUNK_MAX_TOKENS} токенов (~{cls.CHUNK_MAX_CHARS} символов)"
        logger.info(f"Чанки: {chunk_info}, нахлёст {cls.CHUNK_OVERLAP_TOKENS} токенов (~{cls.CHUNK_OVERLAP_CHARS} символов)")
        logger.info(f"Лимит поиска по умолчанию: {cls.DEFAULT_SEARCH_LIMIT}")
        logger.info(f"Максимальный лимит поиска: {cls.MAX_SEARCH_LIMIT}")
        logger.info(f"Батчи индексации: код={cls.BATCH_SIZE_CODE}, метаданные={cls.BATCH_SIZE_METADATA}, формы={cls.BATCH_SIZE_FORMS}")
        logger.info(f"Метрика расстояния: {cls.VECTOR_DISTANCE_METRIC}")
        logger.info(f"Гибридный поиск alpha: {cls.HYBRID_SEARCH_ALPHA}, MMR: {cls.SEARCH_USE_MMR}")
        logger.info(f"Уровень логирования: {cls.LOG_LEVEL}")
        logger.info("=" * 60)


logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level=Config.LOG_LEVEL
)


if __name__ == "__main__":
    Config.show()
    Config.validate()
