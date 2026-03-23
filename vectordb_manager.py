"""
Менеджер векторной базы данных для хранения информации о конфигурации 1С
"""
import re
import logging
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

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


class VectorDBManager:
    """Управление векторной БД"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or Config.VECTORDB_PATH
        Path(self.db_path).mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=self.db_path,
            settings=Settings(anonymized_telemetry=False)
        )

        if Config.EMBEDDING_API_BASE:
            base_ef = embedding_functions.OpenAIEmbeddingFunction(
                api_base=Config.EMBEDDING_API_BASE,
                api_key=Config.EMBEDDING_API_KEY,
                model_name=Config.EMBEDDING_MODEL
            )
            if "qwen3" in Config.EMBEDDING_MODEL.lower() and Config.EMBEDDING_ADD_EOS_MANUAL:
                self.embedding_function = QwenEOSEmbeddingWrapper(base_ef)
                logger.warning(
                    f"EMBEDDING_ADD_EOS_MANUAL=true для модели {Config.EMBEDDING_MODEL}. "
                    f"llama.cpp (LM Studio) уже добавляет EOS автоматически — возможен двойной EOS. "
                    f"Рекомендуется установить EMBEDDING_ADD_EOS_MANUAL=false."
                )
            else:
                self.embedding_function = base_ef
                logger.info(f"Эмбеддинги через API: {Config.EMBEDDING_API_BASE}, модель: {Config.EMBEDDING_MODEL}")
        else:
            self.embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=Config.EMBEDDING_MODEL,
                trust_remote_code=True
            )
            logger.info(f"Эмбеддинги локально: {Config.EMBEDDING_MODEL}")

        self.collections = {}
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
        logger.info(f"Векторная БД инициализирована: {self.db_path}, поиск: {' + '.join(search_mode)}")

    def _init_collections(self):
        distance = getattr(Config, "VECTOR_DISTANCE_METRIC", "cosine")
        if distance not in ("cosine", "l2", "ip"):
            distance = "cosine"
        collection_metadata = {"hnsw:space": distance}
        for key, name in Config.COLLECTIONS.items():
            try:
                self.collections[key] = self.client.get_or_create_collection(
                    name=name,
                    embedding_function=self.embedding_function,
                    metadata={**collection_metadata, "description": f"Коллекция для {key} из конфигурации 1С"}
                )
                logger.info(f"Коллекция '{name}' готова (метрика: {distance})")
            except Exception as e:
                logger.error(f"Ошибка создания коллекции {name}: {e}")
                raise

    def clear_all_collections(self):
        for name in Config.COLLECTIONS.values():
            try:
                self.client.delete_collection(name=name)
                logger.info(f"Коллекция '{name}' удалена")
            except Exception as e:
                logger.warning(f"Не удалось удалить коллекцию {name}: {e}")
        self._init_collections()

    def add_code_chunks(self, chunks: List[Dict], batch_size: int = None):
        if batch_size is None:
            batch_size = getattr(Config, "BATCH_SIZE_CODE", 100)
        collection = self.collections["code"]
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            documents = []
            metadatas = []
            ids = []
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
                document = "\n".join(text_parts)
                if Config.EMBEDDING_MAX_CHARS > 0 and len(document) > Config.EMBEDDING_MAX_CHARS:
                    document = document[: Config.EMBEDDING_MAX_CHARS - 3] + "..."
                documents.append(document)
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
                metadatas.append(metadata)
                chunk_idx = chunk.get("chunk_index", 0)
                chunk_id = f"code_{i + j}_{chunk.get('method_name', 'unknown')}_{chunk_idx}"
                ids.append(chunk_id)
            try:
                collection.add(documents=documents, metadatas=metadatas, ids=ids)
                logger.info(f"Добавлено {len(batch)} чанков кода (батч {i // batch_size + 1})")
            except Exception as e:
                logger.error(f"Ошибка добавления кода в БД: {e}")

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
        return "\n".join(text_parts)

    def add_metadata_objects(self, metadata_objects: List[Dict], batch_size: int = None):
        if batch_size is None:
            batch_size = getattr(Config, "BATCH_SIZE_METADATA", 50)
        collection = self.collections["metadata"]
        for i in range(0, len(metadata_objects), batch_size):
            batch = metadata_objects[i:i + batch_size]
            documents = []
            metadatas = []
            ids = []
            for j, obj in enumerate(batch):
                document = self._build_metadata_document(obj)
                if Config.EMBEDDING_MAX_CHARS > 0 and len(document) > Config.EMBEDDING_MAX_CHARS:
                    document = document[: Config.EMBEDDING_MAX_CHARS - 3] + "..."
                documents.append(document)
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
                metadatas.append(metadata)
                obj_id = f"metadata_{obj_type}_{obj.get('name', 'unknown')}_{i + j}"
                ids.append(obj_id)
            try:
                collection.add(documents=documents, metadatas=metadatas, ids=ids)
                logger.info(f"Добавлено {len(batch)} объектов метаданных (батч {i // batch_size + 1})")
            except Exception as e:
                logger.error(f"Ошибка добавления метаданных в БД: {e}")

    def add_forms(self, forms: List[Dict], batch_size: int = None):
        if batch_size is None:
            batch_size = getattr(Config, "BATCH_SIZE_FORMS", 50)
        collection = self.collections["forms"]
        for i in range(0, len(forms), batch_size):
            batch = forms[i:i + batch_size]
            documents = []
            metadatas = []
            ids = []
            for j, form in enumerate(batch):
                text_parts = [
                    f"Форма: {form.get('form_name', '')}",
                    f"Объект: {form.get('object_type', '')} {form.get('object_name', '')}"
                ]
                if form.get('elements'):
                    text_parts.append(f"Элементы: {', '.join(form['elements'][:20])}")
                document = "\n".join(text_parts)
                if Config.EMBEDDING_MAX_CHARS > 0 and len(document) > Config.EMBEDDING_MAX_CHARS:
                    document = document[: Config.EMBEDDING_MAX_CHARS - 3] + "..."
                documents.append(document)
                metadata = {
                    "form_name": form.get('form_name', ''),
                    "object_name": form.get('object_name', ''),
                    "object_type": form.get('object_type', ''),
                    "elements_count": form.get('elements_count', 0),
                    "file_path": form.get('file_path', '')
                }
                metadatas.append(metadata)
                form_id = f"form_{form.get('object_name', 'unknown')}_{form.get('form_name', 'unknown')}_{i + j}"
                ids.append(form_id)
            try:
                collection.add(documents=documents, metadatas=metadatas, ids=ids)
                logger.info(f"Добавлено {len(batch)} форм (батч {i // batch_size + 1})")
            except Exception as e:
                logger.error(f"Ошибка добавления форм в БД: {e}")

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

    def _query_collection(
        self,
        collection_key: str,
        query: str,
        limit: int,
        where: Optional[Dict] = None,
        error_label: str = "поиск",
    ) -> List[Dict]:
        """Универсальный семантический поиск с гибридным re-ranking и MMR."""
        collection = self.collections[collection_key]
        fetch_k = max(limit, min(getattr(Config, "SEARCH_FETCH_K", 50), 100))
        alpha = getattr(Config, "HYBRID_SEARCH_ALPHA", 1.0)
        use_mmr = getattr(Config, "SEARCH_USE_MMR", False)
        mmr_lambda = getattr(Config, "MMR_LAMBDA", 0.5)
        try:
            results = collection.query(
                query_texts=[query],
                n_results=fetch_k,
                where=where,
            )
            items = list(zip(
                results["documents"][0] or [],
                results["metadatas"][0] or [],
                results["distances"][0] or [],
            ))
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
        collection = self.collections["code"]
        where_filter = {"object_name": {"$eq": object_name}}
        try:
            if not query or not query.strip():
                get_result = collection.get(
                    where=where_filter,
                    limit=limit,
                    include=["documents", "metadatas"]
                )
                if not get_result or not get_result.get("documents"):
                    return []
                items = [
                    (doc, meta, 0.0)
                    for doc, meta in zip(
                        get_result["documents"],
                        get_result["metadatas"]
                    )
                ]
                return self._format_results_from_items(items)
            results = collection.query(
                query_texts=[query],
                n_results=limit,
                where=where_filter
            )
            return self._format_results(results)
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

    def _format_results(self, results) -> List[Dict]:
        formatted = []
        if not results.get("documents") or not results["documents"][0]:
            return formatted
        for i, (doc, metadata, distance) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        )):
            formatted.append({
                "rank": i + 1,
                "relevance": round(1 - distance, 3),
                "document": doc,
                "metadata": metadata
            })
        return formatted

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
        for key, collection in self.collections.items():
            try:
                stats[key] = collection.count()
            except Exception:
                stats[key] = 0
        return stats
