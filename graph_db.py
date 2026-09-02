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
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import kuzu

from config import Config

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

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or Config.GRAPHDB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: Optional[kuzu.Database] = None
        self._conn: Optional[kuzu.Connection] = None
        self._init_db()
        logger.info(f"Графовая БД (Kuzu) инициализирована: {self.db_path}")

    def _get_conn(self) -> kuzu.Connection:
        if self._conn is None:
            self._db = kuzu.Database(str(self.db_path))
            self._conn = kuzu.Connection(self._db)
        return self._conn

    def _init_db(self):
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
        for node in nodes:
            self.add_node(
                node_id=node["id"],
                node_type=node["node_type"],
                name=node["name"],
                object_type=node["object_type"] or None,
                object_name=node["object_name"] or None,
                synonym=node["synonym"] or None,
                extra=self._extra_dict(node["extra"]),
            )

    def _flush_edges_with_extra(self, edges: List[Dict[str, str]]) -> None:
        for edge in edges:
            self.add_edge(
                source_id=edge["source"],
                target_id=edge["target"],
                edge_type=edge["edge_type"],
                extra=self._extra_dict(edge["extra"]),
            )

    def add_nodes_batch(self, nodes: List[Dict[str, Any]]) -> None:
        """Пакетное добавление узлов (upsert через MERGE)."""
        if not nodes:
            return

        normalized = self._dedupe_nodes(nodes)
        batch_size = self._batch_size()
        plain_nodes = []
        nodes_with_extra = []
        for node in normalized:
            if node.get("extra"):
                nodes_with_extra.append(node)
            else:
                plain_nodes.append(node)

        total_chunks = (len(plain_nodes) + batch_size - 1) // batch_size if plain_nodes else 0
        logger.info(
            "Пакетная запись узлов: %s уникальных (%s с extra отдельно), чанков по %s: %s",
            len(normalized),
            len(nodes_with_extra),
            batch_size,
            total_chunks,
        )

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
            self._execute_load_from_rows(chunk, _NODE_COLUMNS, cypher_body)
            logger.debug("Записан чанк узлов %s/%s", chunk_index, total_chunks)
        if nodes_with_extra:
            self._flush_nodes_with_extra(nodes_with_extra)

    def add_edges_batch(self, edges: List[Dict[str, Any]]) -> None:
        """Пакетное добавление рёбер (дедуп по source/target/edge_type)."""
        if not edges:
            return

        normalized = self._dedupe_edges(edges)
        batch_size = self._batch_size()
        plain_edges = []
        edges_with_extra = []
        for edge in normalized:
            if edge.get("extra"):
                edges_with_extra.append(edge)
            else:
                plain_edges.append(edge)

        total_chunks = (len(plain_edges) + batch_size - 1) // batch_size if plain_edges else 0
        logger.info(
            "Пакетная запись рёбер: %s уникальных (%s с extra отдельно), чанков по %s: %s",
            len(normalized),
            len(edges_with_extra),
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
            logger.debug("Записан чанк рёбер %s/%s", chunk_index, total_chunks)
        if edges_with_extra:
            self._flush_edges_with_extra(edges_with_extra)

    @staticmethod
    def _format_node_row(row: List) -> Dict:
        """row: [id, name, object_type, object_name, edge_type]"""
        node_id, name, object_type, object_name, edge_type = row
        return {
            "object": f"{object_type or ''}.{object_name or name}",
            "node_id": node_id,
            "edge_type": edge_type,
        }

    def get_dependencies(
        self,
        object_name: str,
        max_depth: int = 2,
        limit: int = 100
    ) -> List[Dict]:
        """Что зависит от объекта X (кто на него ссылается)."""
        limit = min(max(1, limit), 500)
        conn = self._get_conn()
        result = conn.execute(
            f"""
            MATCH (s:Node)-[r:REL]->(t:Node)
            WHERE t.object_name = $name OR t.name = $name
            RETURN DISTINCT s.id, s.name, s.object_type, s.object_name, r.edge_type
            ORDER BY r.edge_type, s.name
            LIMIT {limit}
            """,
            {"name": object_name},
        )
        return self._collect(result)

    def get_references(self, object_name: str, limit: int = 100) -> List[Dict]:
        """На что ссылается объект X (какие объекты он использует)."""
        limit = min(max(1, limit), 500)
        conn = self._get_conn()
        result = conn.execute(
            f"""
            MATCH (s:Node)-[r:REL]->(t:Node)
            WHERE s.object_name = $name OR s.name = $name
            RETURN DISTINCT t.id, t.name, t.object_type, t.object_name, r.edge_type
            ORDER BY r.edge_type, t.name
            LIMIT {limit}
            """,
            {"name": object_name},
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
