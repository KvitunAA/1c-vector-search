"""
Менеджер векторной базы данных для хранения информации о конфигурации 1С.
Хранилище — sqlite-vec (расширение SQLite для KNN-поиска по эмбеддингам).
Эмбеддинги вычисляются самостоятельно: через OpenAI-совместимый API (LM Studio,
LocalAI и т.п.) либо локально через sentence-transformers.
"""
import re
import json
import logging
import sqlite3
from typing import List, Dict, Optional, Tuple, Callable
from pathlib import Path

import sqlite_vec

from config import Config

logger = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False

QWEN3_EOS_SUFFIX = "<|endoftext|>"


class QwenEOSEmbeddingWrapper:
    """Обёртка для добавления EOS-токена Qwen3 при EMBEDDING_ADD_EOS_MANUAL=true.

    ВНИМАНИЕ: llama.cpp (LM Studio) уже добавляет EOS автоматически на уровне
    BPE-токенизатора. Включение EMBEDDING_ADD_EOS_MANUAL=true приведёт к двойному
    EOS и ухудшению качества эмбеддингов. Рекомендуется оставлять false (по умолчанию).
    Предупреждение "add_eos_token should be true" — косметическое.
    """

    def __init__(self, base_embedding_fn):
        self._base = base_embedding_fn

    def __call__(self, input: List[str]) -> List[List[float]]:
        texts_with_eos = [
            t if t.endswith(QWEN3_EOS_SUFFIX) else t + QWEN3_EOS_SUFFIX
            for t in input
        ]
        return self._base(texts_with_eos)

    def name(self) -> str:
        if hasattr(self._base, "name") and callable(self._base.name):
            return self._base.name()
        return "QwenEOSWrapper"


class APIEmbeddingFunction:
    """Эмбеддинги через OpenAI-совместимый API (POST {base}/embeddings)."""

    def __init__(self, api_base: str, api_key: str, model_name: str):
        import requests
        self._requests = requests
        self._url = api_base.rstrip("/") + "/embeddings"
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._model = model_name

    def __call__(self, input: List[str]) -> List[List[float]]:
        if not input:
            return []
        payload = {"model": self._model, "input": list(input)}
        response = self._requests.post(self._url, json=payload, headers=self._headers, timeout=300)
        response.raise_for_status()
        data = response.json().get("data", [])
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in ordered]

    def name(self) -> str:
        return f"API:{self._model}"


class LocalEmbeddingFunction:
    """Локальные эмбеддинги через sentence-transformers."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name, trust_remote_code=True)
        self._model_name = model_name

    def __call__(self, input: List[str]) -> List[List[float]]:
        if not input:
            return []
        vectors = self._model.encode(list(input), convert_to_numpy=True)
        return [v.tolist() for v in vectors]

    def name(self) -> str:
        return f"Local:{self._model_name}"


def build_embedding_function() -> Callable[[List[str]], List[List[float]]]:
    """Создаёт функцию эмбеддинга по настройкам Config (API или локальная модель)."""
    if Config.EMBEDDING_API_BASE:
        base_ef = APIEmbeddingFunction(
            api_base=Config.EMBEDDING_API_BASE,
            api_key=Config.EMBEDDING_API_KEY,
            model_name=Config.EMBEDDING_MODEL,
        )
        if "qwen3" in Config.EMBEDDING_MODEL.lower() and Config.EMBEDDING_ADD_EOS_MANUAL:
            logger.warning(
                f"EMBEDDING_ADD_EOS_MANUAL=true для модели {Config.EMBEDDING_MODEL}. "
                f"llama.cpp (LM Studio) уже добавляет EOS автоматически — возможен двойной EOS. "
                f"Рекомендуется установить EMBEDDING_ADD_EOS_MANUAL=false."
            )
            return QwenEOSEmbeddingWrapper(base_ef)
        logger.info(f"Эмбеддинги через API: {Config.EMBEDDING_API_BASE}, модель: {Config.EMBEDDING_MODEL}")
        return base_ef

    logger.info(f"Эмбеддинги локально: {Config.EMBEDDING_MODEL}")
    return LocalEmbeddingFunction(Config.EMBEDDING_MODEL)


class VectorDBManager:
    """Управление векторной БД на sqlite-vec."""

    # Поля метаданных, которые дублируются в отдельные колонки для фильтрации/выборки
    _FILTER_COLUMNS = ("object_name", "object_type", "is_export")

    def __init__(
        self,
        db_path: Optional[str] = None,
        embedding_function: Optional[Callable[[List[str]], List[List[float]]]] = None,
    ):
        self.db_path = db_path or Config.VECTORDB_PATH
        Path(self.db_path).mkdir(parents=True, exist_ok=True)
        self.sqlite_file = str(Path(self.db_path) / "vectordb.sqlite")

        self.embedding_function = embedding_function or build_embedding_function()

        self._conn = sqlite3.connect(self.sqlite_file)
        self._conn.row_factory = sqlite3.Row
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

        self._distance_metric = self._resolve_distance_metric()
        self._vec_dim: Dict[str, int] = {}
        self._init_collections()

        alpha = getattr(Config, "HYBRID_SEARCH_ALPHA", 1.0)
        use_mmr = getattr(Config, "SEARCH_USE_MMR", False)
        if alpha < 1.0 and not HAS_BM25:
            logger.warning(
                "Гибридный поиск включён (HYBRID_SEARCH_ALPHA < 1.0), "
                "но rank_bm25 не установлен. pip install rank_bm25"
            )
        search_mode = []
        if alpha < 1.0 and HAS_BM25:
            search_mode.append(f"гибрид (alpha={alpha})")
        else:
            search_mode.append("вектор")
        if use_mmr:
            search_mode.append(f"MMR (lambda={getattr(Config, 'MMR_LAMBDA', 0.5)})")
        logger.info(
            f"Векторная БД (sqlite-vec) инициализирована: {self.sqlite_file}, "
            f"метрика: {self._distance_metric}, поиск: {' + '.join(search_mode)}"
        )

    @staticmethod
    def _resolve_distance_metric() -> str:
        metric = getattr(Config, "VECTOR_DISTANCE_METRIC", "cosine").lower()
        if metric == "l2":
            return "L2"
        if metric == "l1":
            return "L1"
        return "cosine"

    def _items_table(self, collection_key: str) -> str:
        return f"{Config.COLLECTIONS[collection_key]}_items"

    def _vec_table(self, collection_key: str) -> str:
        return f"vec_{Config.COLLECTIONS[collection_key]}"

    def _init_collections(self):
        """Создаёт таблицы метаданных (vec0-таблицы создаются лениво — см. _ensure_vec_table)."""
        for key in Config.COLLECTIONS:
            items = self._items_table(key)
            self._conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS "{items}" (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT UNIQUE,
                    document TEXT,
                    metadata TEXT,
                    object_name TEXT,
                    object_type TEXT,
                    is_export INTEGER
                )
                """
            )
            self._conn.execute(
                f'CREATE INDEX IF NOT EXISTS "idx_{items}_object" ON "{items}"(object_name)'
            )
        self._conn.commit()

    def _ensure_vec_table(self, collection_key: str, dim: int):
        vec = self._vec_table(collection_key)
        if collection_key in self._vec_dim:
            return
        existing = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (vec,)
        ).fetchone()
        if not existing:
            self._conn.execute(
                f'CREATE VIRTUAL TABLE "{vec}" USING vec0(embedding float[{dim}] distance_metric={self._distance_metric})'
            )
            self._conn.commit()
        self._vec_dim[collection_key] = dim

    def _vec_table_exists(self, collection_key: str) -> bool:
        vec = self._vec_table(collection_key)
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (vec,)
        ).fetchone()
        return row is not None

    def clear_all_collections(self):
        for key in Config.COLLECTIONS:
            self._conn.execute(f'DROP TABLE IF EXISTS "{self._vec_table(key)}"')
            self._conn.execute(f'DROP TABLE IF EXISTS "{self._items_table(key)}"')
        self._conn.commit()
        self._vec_dim.clear()
        self._init_collections()
        logger.info("Все коллекции очищены")

    def _truncate(self, document: str) -> str:
        if Config.EMBEDDING_MAX_CHARS > 0 and len(document) > Config.EMBEDDING_MAX_CHARS:
            return document[: Config.EMBEDDING_MAX_CHARS - 3] + "..."
        return document

    def _insert_batch(
        self,
        collection_key: str,
        records: List[Dict],
    ):
        """Вставляет батч записей: документ, метаданные и эмбеддинг.

        records: список dict с ключами item_id, document, metadata (dict).
        """
        if not records:
            return
        documents = [r["document"] for r in records]
        embeddings = self.embedding_function(documents)
        if not embeddings:
            return
        self._ensure_vec_table(collection_key, len(embeddings[0]))
        items = self._items_table(collection_key)
        vec = self._vec_table(collection_key)
        try:
            for record, embedding in zip(records, embeddings):
                metadata = record["metadata"]
                cur = self._conn.execute(
                    f'INSERT INTO "{items}" (item_id, document, metadata, object_name, object_type, is_export) '
                    f"VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record["item_id"],
                        record["document"],
                        json.dumps(metadata, ensure_ascii=False),
                        metadata.get("object_name", ""),
                        metadata.get("object_type", ""),
                        1 if metadata.get("is_export") else 0,
                    ),
                )
                self._conn.execute(
                    f'INSERT INTO "{vec}" (rowid, embedding) VALUES (?, ?)',
                    (cur.lastrowid, sqlite_vec.serialize_float32(embedding)),
                )
            self._conn.commit()
        except Exception as e:
            self._conn.rollback()
            logger.error(f"Ошибка добавления записей в коллекцию '{collection_key}': {e}")

    def add_code_chunks(self, chunks: List[Dict], batch_size: int = None):
        if batch_size is None:
            batch_size = getattr(Config, "BATCH_SIZE_CODE", 100)
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            records = []
            for j, chunk in enumerate(batch):
                text_parts = []
                chunk_index = chunk.get("chunk_index", 0)
                total_chunks = chunk.get("total_chunks", 1)
                # Во 2+ чанках длинной процедуры не повторяем «шапку» из комментариев —
                # иначе эмбеддинг заполняется одним и тем же блоком // Параметры… и хуже
                # отражает уникальный код (см. README).
                include_comments = total_chunks <= 1 or chunk_index == 0
                if include_comments and chunk.get("comments"):
                    text_parts.append("// " + "\n// ".join(chunk["comments"]))
                text_parts.append(chunk["signature"])
                text_parts.append(chunk["code"])
                document = self._truncate("\n".join(text_parts))
                metadata = {
                    "object_type": chunk.get("object_type", ""),
                    "object_name": chunk.get("object_name", ""),
                    "module_name": chunk.get("module_name", ""),
                    "method_name": chunk.get("method_name", ""),
                    "method_type": chunk.get("method_type", ""),
                    "is_export": chunk.get("is_export", False),
                    "directive": chunk.get("directive", ""),
                    "signature": chunk.get("signature", ""),
                    "file_path": chunk.get("file_path", ""),
                    "chunk_index": chunk.get("chunk_index", 0),
                    "total_chunks": chunk.get("total_chunks", 1),
                }
                chunk_idx = chunk.get("chunk_index", 0)
                item_id = f"code_{i + j}_{chunk.get('method_name', 'unknown')}_{chunk_idx}"
                records.append({"item_id": item_id, "document": document, "metadata": metadata})
            self._insert_batch("code", records)
            logger.info(f"Добавлено {len(batch)} чанков кода (батч {i // batch_size + 1})")

    @staticmethod
    def _build_metadata_document(obj: Dict) -> str:
        """Формирует текстовый документ для эмбеддинга из объекта метаданных."""
        text_parts = [
            f"Тип: {obj.get('type', '')}",
            f"Имя: {obj.get('name', '')}",
            f"Синоним: {obj.get('synonym', '')}",
            f"Комментарий: {obj.get('comment', '')}"
        ]
        if obj.get('attributes'):
            attr_list = [f"{a['name']} ({a['type']})" for a in obj['attributes']]
            text_parts.append(f"Реквизиты: {', '.join(attr_list)}")
        if obj.get('dimensions'):
            dim_list = [f"{d['name']} ({d['type']})" for d in obj['dimensions']]
            text_parts.append(f"Измерения: {', '.join(dim_list)}")
        if obj.get('resources'):
            res_list = [f"{r['name']} ({r['type']})" for r in obj['resources']]
            text_parts.append(f"Ресурсы: {', '.join(res_list)}")
        if obj.get('tabular_sections'):
            ts_parts = []
            for ts in obj['tabular_sections']:
                if isinstance(ts, dict):
                    ts_name = ts['name']
                    ts_attrs = [f"{a['name']} ({a['type']})" for a in ts.get('attributes', [])]
                    ts_desc = f"{ts_name}({', '.join(ts_attrs)})" if ts_attrs else ts_name
                    ts_parts.append(ts_desc)
                else:
                    ts_parts.append(str(ts))
            text_parts.append(f"Табличные части: {'; '.join(ts_parts)}")
        if obj.get('commands'):
            text_parts.append(f"Команды: {', '.join(obj['commands'])}")
        if obj.get("kind") in ("Role", "RoleTemplate") or obj.get("granted_objects"):
            kind_label = "Шаблон прав" if obj.get("kind") == "RoleTemplate" else "Роль"
            text_parts.append(kind_label)
            granted = obj.get("granted_objects") or []
            text_parts.append(f"Объектов с правами: {len(granted)}")
            snippets = []
            for item in granted[:80]:
                rights = ", ".join(item.get("rights") or [])
                snippets.append(f"{item.get('name', '')} ({rights})" if rights else item.get("name", ""))
            if snippets:
                text_parts.append("Права: " + "; ".join(snippets))
        kind = obj.get("kind") or ""
        is_template = obj.get("object_type_dir") == "Templates" or kind in (
            "DataCompositionSchema",
            "SpreadsheetDocument",
            "HTMLDocument",
            "TextDocument",
            "BinaryData",
            "ActiveDocument",
            "GeographicalSchema",
            "GraphicalSchema",
            "Template",
        )
        if is_template or obj.get("queries") or obj.get("cell_texts") or obj.get("body_text"):
            labels = {
                "DataCompositionSchema": "Макет СКД (схема компоновки данных)",
                "SpreadsheetDocument": "Табличный документ (MXL, печатная форма)",
                "HTMLDocument": "HTML-документ",
                "TextDocument": "Текстовый документ",
                "BinaryData": "Двоичные данные",
                "ActiveDocument": "ActiveDocument",
                "GeographicalSchema": "Географическая схема",
                "GraphicalSchema": "Графическая схема",
            }
            text_parts.append(labels.get(kind, "Макет 1С"))
            if obj.get("owner_type") or obj.get("owner_name"):
                text_parts.append(
                    f"Владелец: {obj.get('owner_type', '')} {obj.get('owner_name', '')}"
                )
            if obj.get("template_name"):
                text_parts.append(f"Макет: {obj.get('template_name')}")
            if obj.get("data_sets"):
                text_parts.append("Наборы данных: " + ", ".join(obj["data_sets"][:30]))
            if obj.get("parameters"):
                text_parts.append("Параметры: " + ", ".join(obj["parameters"][:30]))
            if obj.get("fields"):
                text_parts.append("Поля: " + ", ".join(obj["fields"][:40]))
            if obj.get("named_areas"):
                text_parts.append("Области: " + ", ".join(obj["named_areas"][:40]))
            if obj.get("cell_texts"):
                text_parts.append("Текст ячеек: " + "; ".join(obj["cell_texts"][:80]))
            if obj.get("body_text"):
                text_parts.append(obj["body_text"])
            for idx, query_text in enumerate(obj.get("queries") or [], start=1):
                text_parts.append(f"Запрос {idx}: {query_text}")
        return "\n".join(text_parts)

    def add_metadata_objects(self, metadata_objects: List[Dict], batch_size: int = None):
        if batch_size is None:
            batch_size = getattr(Config, "BATCH_SIZE_METADATA", 50)
        for i in range(0, len(metadata_objects), batch_size):
            batch = metadata_objects[i:i + batch_size]
            records = []
            for j, obj in enumerate(batch):
                document = self._truncate(self._build_metadata_document(obj))
                obj_type = obj.get('object_type_dir') or obj.get('type', '')
                ts_names = []
                for ts in obj.get('tabular_sections', []):
                    ts_names.append(ts['name'] if isinstance(ts, dict) else str(ts))
                metadata = {
                    "object_name": obj.get('name', ''),
                    "object_type": obj_type,
                    "synonym": obj.get('synonym', ''),
                    "description": obj.get('comment', ''),
                    "has_modules": ','.join(obj.get('has_modules', [])),
                    "attributes_count": obj.get('attributes_count', 0),
                    "tabular_sections": ','.join(ts_names),
                    "has_dimensions": len(obj.get('dimensions', [])) > 0,
                    "has_resources": len(obj.get('resources', [])) > 0,
                    "commands_count": len(obj.get('commands', [])),
                    "file_path": obj.get('file_path', '')
                }
                item_id = f"metadata_{obj_type}_{obj.get('name', 'unknown')}_{i + j}"
                records.append({"item_id": item_id, "document": document, "metadata": metadata})
            self._insert_batch("metadata", records)
            logger.info(f"Добавлено {len(batch)} объектов метаданных (батч {i // batch_size + 1})")

    def add_forms(self, forms: List[Dict], batch_size: int = None):
        if batch_size is None:
            batch_size = getattr(Config, "BATCH_SIZE_FORMS", 50)
        for i in range(0, len(forms), batch_size):
            batch = forms[i:i + batch_size]
            records = []
            for j, form in enumerate(batch):
                text_parts = [
                    f"Форма: {form.get('form_name', '')}",
                    f"Объект: {form.get('object_type', '')} {form.get('object_name', '')}"
                ]
                if form.get('elements'):
                    text_parts.append(f"Элементы: {', '.join(form['elements'][:20])}")
                document = self._truncate("\n".join(text_parts))
                metadata = {
                    "form_name": form.get('form_name', ''),
                    "object_name": form.get('object_name', ''),
                    "object_type": form.get('object_type', ''),
                    "elements_count": form.get('elements_count', 0),
                    "file_path": form.get('file_path', '')
                }
                item_id = f"form_{form.get('object_name', 'unknown')}_{form.get('form_name', 'unknown')}_{i + j}"
                records.append({"item_id": item_id, "document": document, "metadata": metadata})
            self._insert_batch("forms", records)
            logger.info(f"Добавлено {len(batch)} форм (батч {i // batch_size + 1})")

    def _tokenize(self, text: str) -> List[str]:
        """Простая токенизация для BM25 (русский + BSL)."""
        text = re.sub(r"[^\w\s]", " ", text.lower())
        return [t for t in text.split() if len(t) > 1]

    def _hybrid_rerank(
        self,
        query: str,
        items: List[Tuple[str, Dict, float]],
        alpha: float,
    ) -> List[Tuple[str, Dict, float]]:
        """Комбинирует векторный score с BM25. alpha=1 — только вектор, alpha=0 — только BM25."""
        if not HAS_BM25 or alpha >= 1.0 or not items:
            if alpha < 1.0 and not HAS_BM25:
                logger.debug("rank_bm25 не установлен — гибридный поиск отключён, используется только векторный")
            return items
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return items
        docs = [self._tokenize(item[0]) for item in items]
        bm25 = BM25Okapi(docs)
        bm25_scores = bm25.get_scores(query_tokens)
        max_bm = float(max(bm25_scores)) if len(bm25_scores) > 0 and max(bm25_scores) > 0 else 1.0
        combined = []
        for i, (doc, meta, dist) in enumerate(items):
            vec_score = max(0.0, min(1.0, 1.0 - dist))
            bm25_norm = bm25_scores[i] / max_bm if max_bm > 0 else 0
            score = alpha * vec_score + (1 - alpha) * bm25_norm
            combined.append((doc, meta, 1.0 - score))
        combined.sort(key=lambda x: x[2])
        return combined

    def _apply_mmr(
        self,
        items: List[Tuple[str, Dict, float]],
        query: str,
        limit: int,
        lambda_param: float,
    ) -> List[Tuple[str, Dict, float]]:
        """Maximal Marginal Relevance: баланс релевантности и разнообразия."""
        if len(items) <= limit or lambda_param >= 1.0:
            return items[:limit]
        query_tokens = set(self._tokenize(query))
        selected = []
        remaining = list(items)
        while len(selected) < limit and remaining:
            best_score = -1.0
            best_idx = 0
            for i, (doc, meta, dist) in enumerate(remaining):
                rel = 1.0 - dist
                doc_tokens = set(self._tokenize(doc))
                sim_to_selected = 0.0
                if selected:
                    for sel_doc, _, _ in selected:
                        sel_tokens = set(self._tokenize(sel_doc))
                        jaccard = len(doc_tokens & sel_tokens) / max(1, len(doc_tokens | sel_tokens))
                        sim_to_selected = max(sim_to_selected, jaccard)
                mmr = lambda_param * rel - (1 - lambda_param) * sim_to_selected
                if mmr > best_score:
                    best_score = mmr
                    best_idx = i
            selected.append(remaining.pop(best_idx))
        return selected

    def _knn_items(
        self,
        collection_key: str,
        query: str,
        fetch_k: int,
    ) -> List[Tuple[str, Dict, float]]:
        """Векторный KNN-поиск: возвращает список (document, metadata, distance)."""
        if not self._vec_table_exists(collection_key):
            return []
        embeddings = self.embedding_function([query])
        if not embeddings:
            return []
        query_vector = sqlite_vec.serialize_float32(embeddings[0])
        items_table = self._items_table(collection_key)
        vec_table = self._vec_table(collection_key)
        rows = self._conn.execute(
            f'''
            SELECT i.document AS document, i.metadata AS metadata, v.distance AS distance
            FROM "{vec_table}" v
            JOIN "{items_table}" i ON i.rowid = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            ''',
            (query_vector, fetch_k),
        ).fetchall()
        return [
            (row["document"], json.loads(row["metadata"]), float(row["distance"]))
            for row in rows
        ]

    def _query_collection(
        self,
        collection_key: str,
        query: str,
        limit: int,
        where: Optional[Dict] = None,
        error_label: str = "поиск",
    ) -> List[Dict]:
        """Универсальный семантический поиск с гибридным re-ranking и MMR."""
        alpha = getattr(Config, "HYBRID_SEARCH_ALPHA", 1.0)
        use_mmr = getattr(Config, "SEARCH_USE_MMR", False)
        mmr_lambda = getattr(Config, "MMR_LAMBDA", 0.5)
        fetch_k = max(limit, min(getattr(Config, "SEARCH_FETCH_K", 50), 100))
        if where:
            # over-fetch для пост-фильтрации в Python (нативные фильтры vec0 не используем)
            fetch_k = max(fetch_k, 200)
        try:
            items = self._knn_items(collection_key, query, fetch_k)
            if where:
                items = [it for it in items if self._matches_filter(it[1], where)]
            if alpha < 1.0 and HAS_BM25:
                items = self._hybrid_rerank(query, items, alpha)
            if use_mmr:
                items = self._apply_mmr(items, query, limit, mmr_lambda)
            else:
                items = items[:limit]
            return self._format_results_from_items(items)
        except Exception as e:
            logger.error(f"Ошибка {error_label}: {e}")
            return []

    @staticmethod
    def _matches_filter(metadata: Dict, where: Dict) -> bool:
        """Пост-фильтрация по равенству значений метаданных."""
        for field, expected in where.items():
            if isinstance(expected, dict):
                # формат {"$eq": value}
                expected = expected.get("$eq", expected)
            if metadata.get(field) != expected:
                return False
        return True

    def search_code(self, query: str, limit: int = 5, filters: Optional[Dict] = None) -> List[Dict]:
        return self._query_collection(
            "code", query, limit,
            where=filters if filters else None,
            error_label="поиска в коде",
        )

    def search_code_by_object(
        self,
        object_name: str,
        query: Optional[str] = None,
        limit: int = 200
    ) -> List[Dict]:
        items_table = self._items_table("code")
        try:
            if not query or not query.strip():
                rows = self._conn.execute(
                    f'SELECT document, metadata FROM "{items_table}" WHERE object_name = ? LIMIT ?',
                    (object_name, limit),
                ).fetchall()
                items = [(row["document"], json.loads(row["metadata"]), 0.0) for row in rows]
                return self._format_results_from_items(items)
            fetch_k = max(limit, 200)
            items = self._knn_items("code", query, fetch_k)
            items = [it for it in items if it[1].get("object_name") == object_name]
            items = items[:limit]
            return self._format_results_from_items(items)
        except Exception as e:
            logger.error(f"Ошибка поиска кода по объекту '{object_name}': {e}")
            return []

    def search_metadata(self, query: str, limit: int = 5, object_type: Optional[str] = None) -> List[Dict]:
        where_filter = {"object_type": object_type} if object_type else None
        return self._query_collection(
            "metadata", query, limit,
            where=where_filter,
            error_label="поиска в метаданных",
        )

    def search_forms(self, query: str, limit: int = 5) -> List[Dict]:
        return self._query_collection(
            "forms", query, limit,
            error_label="поиска форм",
        )

    def _format_results_from_items(
        self,
        items: List[Tuple[str, Dict, float]]
    ) -> List[Dict]:
        """Форматирует список (doc, metadata, distance) в результат поиска."""
        formatted = []
        for i, (doc, metadata, distance) in enumerate(items):
            formatted.append({
                "rank": i + 1,
                "relevance": round(1 - distance, 3),
                "document": doc,
                "metadata": metadata
            })
        return formatted

    def get_stats(self) -> Dict:
        stats = {}
        for key in Config.COLLECTIONS:
            try:
                row = self._conn.execute(
                    f'SELECT COUNT(*) FROM "{self._items_table(key)}"'
                ).fetchone()
                stats[key] = row[0] if row else 0
            except Exception:
                stats[key] = 0
        return stats

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
