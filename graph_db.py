"""
Графовая база данных для хранения связей между объектами конфигурации 1С.
Использует встраиваемую графовую СУБД Kuzu (Cypher) для персистентного
хранения узлов и рёбер.
"""
import csv
import gc
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import kuzu

from config import Config
from object_identifier import ObjectIdentifier, parse_object_identifier

logger = logging.getLogger(__name__)

_NODE_COLUMNS = (
    "id",
    "node_type",
    "name",
    "object_type",
    "object_name",
    "synonym",
    "extra",
)
_EDGE_COLUMNS = ("source", "target", "edge_type", "extra")
# Kuzu LOAD FROM не разбирает RFC-кавычки CSV: запятая в синониме роли рвёт колонки.
_CSV_UNSAFE_CHARS = frozenset(',;"\'\n\r')
_LOCK_HINT = (
    "Не удалось открыть граф {path}: файл занят. "
    "Остановите MCP-сервер 1c-vector-search в Cursor и повторите индексацию."
)
_EMPTY_STATS = {
    "nodes_count": 0,
    "edges_count": 0,
    "nodes_by_type": {},
    "edges_by_type": {},
}


class GraphDBManager:
    """Менеджер графовой БД (Kuzu) для конфигурации 1С"""

    NODE_TYPES = ("Metadata", "Method", "Form")
    EDGE_TYPES = (
        "REFERENCES",
        "HAS_METHOD",
        "HAS_FORM",
        "ATTRIBUTE_TYPE",
        "USES_IN_CODE",
        "HAS_RIGHT",
        "HAS_TEMPLATE",
    )

    def __init__(
        self,
        db_path: Optional[str] = None,
        read_only: bool = False,
        lock_retries: int = 5,
        lock_retry_delay: float = 2.0,
    ):
        self.db_path = Path(db_path or Config.GRAPHDB_PATH)
        self.read_only = read_only
        self.lock_retries = max(1, lock_retries)
        self.lock_retry_delay = max(0.0, lock_retry_delay)
        self._db: Optional[kuzu.Database] = None
        self._conn: Optional[kuzu.Connection] = None
        self._unavailable = False
        if read_only and not self._database_exists():
            self._unavailable = True
            logger.warning("Графовая БД не найдена (read-only): %s", self.db_path)
            return
        if not read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info("Графовая БД (Kuzu) инициализирована: %s", self.db_path)

    def _database_exists(self) -> bool:
        path = self.db_path
        if not path.exists():
            return False
        if path.is_dir():
            try:
                return any(path.iterdir())
            except OSError:
                return False
        return path.stat().st_size > 0

    @staticmethod
    def _is_lock_error(exc: BaseException) -> bool:
        text = str(exc).lower()
        return "lock" in text or "could not set lock" in text

    def _open_database(self) -> None:
        last_exc: Optional[BaseException] = None
        buffer_pool_size = max(0, getattr(Config, "KUZU_BUFFER_POOL_SIZE", 0))
        for attempt in range(1, self.lock_retries + 1):
            try:
                self._db = kuzu.Database(
                    str(self.db_path),
                    buffer_pool_size=buffer_pool_size,
                    read_only=self.read_only,
                )
                if buffer_pool_size and not self.read_only:
                    logger.info("Kuzu buffer pool: %s MB", buffer_pool_size // (1024 * 1024))
                return
            except Exception as exc:
                last_exc = exc
                if not self._is_lock_error(exc) or attempt >= self.lock_retries:
                    break
                logger.warning(
                    "Граф занят (попытка %s/%s): %s",
                    attempt,
                    self.lock_retries,
                    exc,
                )
                time.sleep(self.lock_retry_delay)
        if last_exc is not None and self._is_lock_error(last_exc):
            raise RuntimeError(_LOCK_HINT.format(path=self.db_path)) from last_exc
        if last_exc is not None:
            raise last_exc

    def _get_conn(self) -> kuzu.Connection:
        if self._unavailable:
            raise RuntimeError(f"Графовая БД недоступна: {self.db_path}")
        if self._conn is None:
            self._open_database()
            self._conn = kuzu.Connection(self._db)
        return self._conn

    def _init_db(self):
        if self.read_only:
            if not self._unavailable:
                self._get_conn()
            return
        # На большой БД CREATE IF NOT EXISTS может исчерпать buffer pool Kuzu.
        if self.db_path.is_file() and self.db_path.stat().st_size > 4096:
            self._get_conn()
            return
        conn = self._get_conn()
        conn.execute(
            """
            CREATE NODE TABLE IF NOT EXISTS Node(
                id STRING,
                node_type STRING,
                name STRING,
                object_type STRING,
                object_name STRING,
                synonym STRING,
                extra STRING,
                PRIMARY KEY(id)
            )
            """
        )
        conn.execute(
            "CREATE REL TABLE IF NOT EXISTS REL(FROM Node TO Node, edge_type STRING, extra STRING)"
        )

    def clear(self):
        """Очистка графа перед переиндексацией"""
        conn = self._get_conn()
        conn.execute("MATCH (n:Node) DETACH DELETE n")
        logger.info("Граф очищен")

    def add_node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        object_type: Optional[str] = None,
        object_name: Optional[str] = None,
        synonym: Optional[str] = None,
        extra: Optional[Dict] = None,
    ):
        """Добавление узла (upsert через MERGE)"""
        if node_type not in self.NODE_TYPES:
            raise ValueError(f"Неизвестный тип узла: {node_type}")
        conn = self._get_conn()
        conn.execute(
            """
            MERGE (n:Node {id: $id})
            SET n.node_type = $node_type,
                n.name = $name,
                n.object_type = $object_type,
                n.object_name = $object_name,
                n.synonym = $synonym,
                n.extra = $extra
            """,
            {
                "id": node_id,
                "node_type": node_type,
                "name": name or "",
                "object_type": object_type or "",
                "object_name": object_name or "",
                "synonym": synonym or "",
                "extra": json.dumps(extra, ensure_ascii=False) if extra else "",
            },
        )

    def _add_nodes_individual(self, nodes: List[Dict[str, str]]) -> None:
        """Поштучная запись узлов — fallback при ошибке UNWIND/CSV."""
        close_every = max(25, getattr(Config, "GRAPH_FLUSH_CLOSE_EVERY", 100))
        for index, node in enumerate(nodes, start=1):
            extra = self._extra_dict(node.get("extra", ""))
            try:
                self.add_node(
                    node_id=node["id"],
                    node_type=node["node_type"],
                    name=node["name"],
                    object_type=node["object_type"] or None,
                    object_name=node["object_name"] or None,
                    synonym=node["synonym"] or None,
                    extra=extra,
                )
            except RuntimeError as exc:
                logger.warning(
                    "Поштучная запись узла %s не удалась (%s), release и повтор",
                    node["id"],
                    exc,
                )
                self.close()
                self.add_node(
                    node_id=node["id"],
                    node_type=node["node_type"],
                    name=node["name"],
                    object_type=node["object_type"] or None,
                    object_name=node["object_name"] or None,
                    synonym=node["synonym"] or None,
                    extra=extra,
                )
            if index % close_every == 0:
                gc.collect()
                self.close()
                logger.info("Поштучная запись узлов: %s/%s", index, len(nodes))

    def _add_edges_individual(self, edges: List[Dict[str, Any]]) -> None:
        """Поштучная запись рёбер — fallback при ошибке UNWIND/CSV."""
        close_every = max(25, getattr(Config, "GRAPH_FLUSH_CLOSE_EVERY", 100))
        for index, edge in enumerate(edges, start=1):
            extra = edge.get("extra")
            if isinstance(extra, str) and extra:
                extra = json.loads(extra)
            self.add_edge(
                source_id=str(edge["source"]),
                target_id=str(edge["target"]),
                edge_type=edge["edge_type"],
                extra=extra if extra else None,
            )
            if index % close_every == 0:
                gc.collect()
                self.close()
                logger.info("Поштучная запись рёбер: %s/%s", index, len(edges))

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        extra: Optional[Dict] = None,
    ):
        """Добавление ребра (дедупликация по источнику, цели и типу через MERGE)"""
        if edge_type not in self.EDGE_TYPES:
            raise ValueError(f"Неизвестный тип ребра: {edge_type}")
        conn = self._get_conn()
        conn.execute(
            """
            MATCH (s:Node {id: $source}), (t:Node {id: $target})
            MERGE (s)-[r:REL {edge_type: $edge_type}]->(t)
            SET r.extra = $extra
            """,
            {
                "source": source_id,
                "target": target_id,
                "edge_type": edge_type,
                "extra": json.dumps(extra, ensure_ascii=False) if extra else "",
            },
        )

    def ensure_metadata_node(self, object_type: str, object_name: str, synonym: str = "") -> str:
        """Создаёт узел метаданных, возвращает id"""
        node_id = f"metadata:{object_type}:{object_name}"
        self.add_node(
            node_id=node_id,
            node_type="Metadata",
            name=object_name,
            object_type=object_type,
            object_name=object_name,
            synonym=synonym,
        )
        return node_id

    @staticmethod
    def _batch_size() -> int:
        return max(1, getattr(Config, "GRAPH_WRITE_BATCH_SIZE", 5000))

    def _csv_batch_size(self) -> int:
        if self._is_large_graph():
            return max(1, getattr(Config, "GRAPH_CSV_BATCH_SIZE", 50))
        return self._batch_size()

    def _is_large_graph(self) -> bool:
        threshold_mb = max(0, getattr(Config, "GRAPH_LARGE_GRAPH_THRESHOLD_MB", 300))
        if threshold_mb <= 0:
            return False
        try:
            return self.db_path.stat().st_size > threshold_mb * 1024 * 1024
        except OSError:
            return False

    def _release_every_n_batches(self) -> int:
        return max(1, getattr(Config, "GRAPH_RELEASE_EVERY_N_BATCHES", 4))

    def _release_after_batch(self, chunk_index: int = 1, total_chunks: int = 1) -> None:
        if not self._is_large_graph():
            gc.collect()
            return
        release_every = self._release_every_n_batches()
        if chunk_index % release_every == 0 or chunk_index >= total_chunks:
            self.close()
        else:
            gc.collect()

    @staticmethod
    def _chunked(items: Sequence[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
        for index in range(0, len(items), size):
            yield list(items[index:index + size])

    @staticmethod
    def _normalize_node_record(node: Dict[str, Any]) -> Dict[str, str]:
        node_id = node.get("node_id") or node.get("id")
        if not node_id:
            raise ValueError("У узла должен быть node_id или id")
        node_type = node.get("node_type", "")
        if node_type not in GraphDBManager.NODE_TYPES:
            raise ValueError(f"Неизвестный тип узла: {node_type}")
        extra = node.get("extra")
        return {
            "id": str(node_id),
            "node_type": node_type,
            "name": node.get("name") or "",
            "object_type": node.get("object_type") or "",
            "object_name": node.get("object_name") or "",
            "synonym": node.get("synonym") or "",
            "extra": json.dumps(extra, ensure_ascii=False) if extra else "",
        }

    @staticmethod
    def _normalize_edge_record(edge: Dict[str, Any]) -> Dict[str, str]:
        edge_type = edge.get("edge_type", "")
        if edge_type not in GraphDBManager.EDGE_TYPES:
            raise ValueError(f"Неизвестный тип ребра: {edge_type}")
        source = edge.get("source")
        target = edge.get("target")
        if not source or not target:
            raise ValueError("У ребра должны быть source и target")
        extra = edge.get("extra")
        return {
            "source": str(source),
            "target": str(target),
            "edge_type": edge_type,
            "extra": json.dumps(extra, ensure_ascii=False) if extra else "",
        }

    @staticmethod
    def _dedupe_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        deduped: Dict[str, Dict[str, str]] = {}
        for node in nodes:
            normalized = GraphDBManager._normalize_node_record(node)
            deduped[normalized["id"]] = normalized
        return list(deduped.values())

    @staticmethod
    def _dedupe_edges(edges: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        deduped: Dict[Tuple[str, str, str], Dict[str, str]] = {}
        for edge in edges:
            normalized = GraphDBManager._normalize_edge_record(edge)
            key = (normalized["source"], normalized["target"], normalized["edge_type"])
            deduped[key] = normalized
        return list(deduped.values())

    @staticmethod
    def _value_needs_rowwise(value: str) -> bool:
        return bool(value) and any(char in _CSV_UNSAFE_CHARS for char in value)

    @staticmethod
    def _row_needs_rowwise(row: Dict[str, str]) -> bool:
        # Поле extra — JSON; csv.DictWriter экранирует запятые и кавычки.
        for key, value in row.items():
            if key == "extra":
                continue
            if GraphDBManager._value_needs_rowwise(value):
                return True
        return False

    @staticmethod
    def _partition_csv_safe(
        rows: List[Dict[str, str]],
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        csv_rows: List[Dict[str, str]] = []
        rowwise: List[Dict[str, str]] = []
        for row in rows:
            if GraphDBManager._row_needs_rowwise(row):
                rowwise.append(row)
            else:
                csv_rows.append(row)
        return csv_rows, rowwise

    def _write_rows_to_csv(self, rows: List[Dict[str, str]], columns: Sequence[str]) -> str:
        fd, path = tempfile.mkstemp(suffix=".csv", prefix="kuzu_batch_")
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in columns})
        return path.replace("\\", "/")

    def _execute_load_from_rows(
        self,
        rows: List[Dict[str, str]],
        columns: Sequence[str],
        cypher_body: str,
    ) -> None:
        if not rows:
            return

        conn = self._get_conn()
        csv_rows = [{column: row.get(column, "") for column in columns} for row in rows]
        csv_path = self._write_rows_to_csv(csv_rows, columns)
        try:
            conn.execute(f"LOAD FROM '{csv_path}' (HEADER=true) {cypher_body}")
        finally:
            try:
                os.remove(csv_path)
            except OSError:
                pass

    @staticmethod
    def _extra_dict(extra_value: str) -> Optional[Dict]:
        if not extra_value:
            return None
        return json.loads(extra_value)

    def _flush_nodes_with_extra(self, nodes: List[Dict[str, str]]) -> None:
        batch_size = self._csv_batch_size()
        try:
            self._unwind_nodes_batch(nodes, batch_size)
        except RuntimeError as exc:
            logger.warning("UNWIND узлов не удался (%s), fallback на поштучную запись", exc)
            self.close()
            self._add_nodes_individual(nodes)

    def _flush_edges_with_extra(self, edges: List[Dict[str, str]]) -> None:
        batch_size = self._csv_batch_size()
        try:
            self._unwind_edges_batch(edges, batch_size)
        except RuntimeError as exc:
            logger.warning("UNWIND рёбер не удался (%s), fallback на поштучную запись", exc)
            self.close()
            self._add_edges_individual(edges)

    def _unwind_nodes_batch(self, nodes: List[Dict[str, str]], batch_size: int) -> None:
        if not nodes:
            return
        cypher = """
            UNWIND $batch AS row
            MERGE (n:Node {id: row.id})
            SET n.node_type = row.node_type,
                n.name = row.name,
                n.object_type = row.object_type,
                n.object_name = row.object_name,
                n.synonym = row.synonym,
                n.extra = row.extra
            """
        total_chunks = (len(nodes) + batch_size - 1) // batch_size
        for chunk_index, chunk in enumerate(self._chunked(nodes, batch_size), start=1):
            conn = self._get_conn()
            conn.execute(cypher, {"batch": chunk})
            if chunk_index == 1 or chunk_index == total_chunks or chunk_index % 10 == 0:
                logger.info("UNWIND-запись узлов: чанк %s/%s (%s шт.)", chunk_index, total_chunks, len(chunk))
            self._release_after_batch(chunk_index, total_chunks)

    def _unwind_edges_batch(self, edges: List[Dict[str, str]], batch_size: int) -> None:
        if not edges:
            return
        cypher = """
            UNWIND $batch AS row
            MATCH (s:Node {id: row.source}), (t:Node {id: row.target})
            MERGE (s)-[r:REL {edge_type: row.edge_type}]->(t)
            SET r.extra = row.extra
            """
        total_chunks = (len(edges) + batch_size - 1) // batch_size
        for chunk_index, chunk in enumerate(self._chunked(edges, batch_size), start=1):
            conn = self._get_conn()
            conn.execute(cypher, {"batch": chunk})
            if chunk_index == 1 or chunk_index == total_chunks or chunk_index % 10 == 0:
                logger.info("UNWIND-запись рёбер: чанк %s/%s (%s шт.)", chunk_index, total_chunks, len(chunk))
            self._release_after_batch(chunk_index, total_chunks)

    def add_nodes_batch(self, nodes: List[Dict[str, Any]]) -> None:
        """Пакетное добавление узлов (upsert через MERGE)."""
        if not nodes:
            return

        normalized = self._dedupe_nodes(nodes)
        batch_size = self._csv_batch_size()
        plain_nodes, nodes_rowwise = self._partition_csv_safe(normalized)

        total_chunks = (len(plain_nodes) + batch_size - 1) // batch_size if plain_nodes else 0
        logger.info(
            "Пакетная запись узлов: %s уникальных (%s построчно из-за запятых/кавычек/extra), чанков по %s: %s",
            len(normalized),
            len(nodes_rowwise),
            batch_size,
            total_chunks,
        )

        if self._is_large_graph():
            logger.info(
                "UNWIND-запись всех узлов (большой граф): %s, чанков по %s",
                len(normalized),
                batch_size,
            )
            try:
                self._unwind_nodes_batch(normalized, batch_size)
            except RuntimeError as exc:
                logger.warning("UNWIND узлов не удался (%s), fallback на поштучную запись", exc)
                self.close()
                self._add_nodes_individual(normalized)
            return

        cypher_body = """
MERGE (n:Node {id: id})
SET n.node_type = node_type,
    n.name = name,
    n.object_type = object_type,
    n.object_name = object_name,
    n.synonym = synonym,
    n.extra = extra
"""
        for chunk_index, chunk in enumerate(self._chunked(plain_nodes, batch_size), start=1):
            try:
                self._execute_load_from_rows(chunk, _NODE_COLUMNS, cypher_body)
            except RuntimeError as exc:
                logger.warning("CSV-запись узлов не удалась (%s), fallback на UNWIND", exc)
                self.close()
                self._unwind_nodes_batch(chunk, batch_size)
            logger.debug("Записан чанк узлов %s/%s", chunk_index, total_chunks)
            self._release_after_batch(chunk_index, total_chunks or 1)
        if nodes_rowwise:
            self._flush_nodes_with_extra(nodes_rowwise)

    def add_edges_batch(self, edges: List[Dict[str, Any]]) -> None:
        """Пакетное добавление рёбер (дедуп по source/target/edge_type)."""
        if not edges:
            return

        normalized = self._dedupe_edges(edges)
        batch_size = self._csv_batch_size()
        if self._is_large_graph():
            logger.info(
                "UNWIND-запись всех рёбер (большой граф): %s, чанков по %s",
                len(normalized),
                batch_size,
            )
            try:
                self._unwind_edges_batch(normalized, batch_size)
            except RuntimeError as exc:
                logger.warning("UNWIND рёбер не удался (%s), fallback на поштучную запись", exc)
                self.close()
                self._add_edges_individual(normalized)
            return

        plain_edges, edges_rowwise = self._partition_csv_safe(normalized)

        total_chunks = (len(plain_edges) + batch_size - 1) // batch_size if plain_edges else 0
        logger.info(
            "Пакетная запись рёбер: %s уникальных (%s построчно из-за запятых/кавычек/extra), чанков по %s: %s",
            len(normalized),
            len(edges_rowwise),
            batch_size,
            total_chunks,
        )

        cypher_body = """
MATCH (s:Node {id: source}), (t:Node {id: target})
MERGE (s)-[r:REL {edge_type: edge_type}]->(t)
SET r.extra = extra
"""
        for chunk_index, chunk in enumerate(self._chunked(plain_edges, batch_size), start=1):
            self._execute_load_from_rows(chunk, _EDGE_COLUMNS, cypher_body)
            if chunk_index == 1 or chunk_index % 20 == 0 or chunk_index == total_chunks:
                logger.info("Записан чанк рёбер %s/%s", chunk_index, total_chunks)
            else:
                logger.debug("Записан чанк рёбер %s/%s", chunk_index, total_chunks)
            self._release_after_batch(chunk_index, total_chunks or 1)
        if edges_rowwise:
            self._flush_edges_with_extra(edges_rowwise)

    @staticmethod
    def _format_node_row(row: List) -> Dict:
        """row: [id, name, object_type, object_name, edge_type]"""
        node_id, name, object_type, object_name, edge_type = row
        return {
            "object": f"{object_type or ''}.{object_name or name}",
            "node_id": node_id,
            "edge_type": edge_type,
        }

    @staticmethod
    def _node_match_clause(alias: str, parsed: ObjectIdentifier) -> tuple[str, Dict[str, str]]:
        """Условие Cypher для сопоставления узла по полному или короткому имени."""
        conditions: List[str] = []
        params: Dict[str, str] = {"short_name": parsed.object_name}

        if parsed.node_id:
            conditions.append(f"{alias}.id = $node_id")
            params["node_id"] = parsed.node_id
        if parsed.object_type:
            conditions.append(
                f"({alias}.object_type = $object_type AND {alias}.object_name = $object_name)"
            )
            params["object_type"] = parsed.object_type
            params["object_name"] = parsed.object_name

        conditions.append(f"{alias}.object_name = $short_name")
        conditions.append(f"{alias}.name = $short_name")
        return f"({' OR '.join(conditions)})", params

    def get_dependencies(
        self,
        object_name: str,
        max_depth: int = 2,
        limit: int = 100
    ) -> List[Dict]:
        """Что зависит от объекта X (кто на него ссылается)."""
        if self._unavailable:
            return []
        limit = min(max(1, limit), 500)
        parsed = parse_object_identifier(object_name)
        target_where, params = self._node_match_clause("t", parsed)
        conn = self._get_conn()
        result = conn.execute(
            f"""
            MATCH (s:Node)-[r:REL]->(t:Node)
            WHERE {target_where}
            RETURN DISTINCT s.id, s.name, s.object_type, s.object_name, r.edge_type
            ORDER BY r.edge_type, s.name
            LIMIT {limit}
            """,
            params,
        )
        return self._collect(result)

    def get_references(self, object_name: str, limit: int = 100) -> List[Dict]:
        """На что ссылается объект X (какие объекты он использует)."""
        if self._unavailable:
            return []
        limit = min(max(1, limit), 500)
        parsed = parse_object_identifier(object_name)
        source_where, params = self._node_match_clause("s", parsed)
        conn = self._get_conn()
        result = conn.execute(
            f"""
            MATCH (s:Node)-[r:REL]->(t:Node)
            WHERE {source_where}
            RETURN DISTINCT t.id, t.name, t.object_type, t.object_name, r.edge_type
            ORDER BY r.edge_type, t.name
            LIMIT {limit}
            """,
            params,
        )
        return self._collect(result)

    @staticmethod
    def _collect(result) -> List[Dict]:
        rows = []
        while result.has_next():
            rows.append(GraphDBManager._format_node_row(result.get_next()))
        return rows

    def get_stats(self) -> Dict:
        """Статистика графа"""
        if self._unavailable:
            return dict(_EMPTY_STATS)
        conn = self._get_conn()

        by_type: Dict[str, int] = {}
        result = conn.execute("MATCH (n:Node) RETURN n.node_type, count(*)")
        while result.has_next():
            node_type, count = result.get_next()
            by_type[node_type] = count

        edge_by_type: Dict[str, int] = {}
        result = conn.execute("MATCH ()-[r:REL]->() RETURN r.edge_type, count(*)")
        while result.has_next():
            edge_type, count = result.get_next()
            edge_by_type[edge_type] = count

        return {
            "nodes_count": sum(by_type.values()),
            "edges_count": sum(edge_by_type.values()),
            "nodes_by_type": by_type,
            "edges_by_type": edge_by_type,
        }

    def close(self):
        if self._conn is not None:
            self._conn = None
        if self._db is not None:
            close = getattr(self._db, "close", None)
            if callable(close):
                close()
            self._db = None
        gc.collect()
