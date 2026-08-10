"""
Графовая база данных для хранения связей между объектами конфигурации 1С.
Использует встраиваемую графовую СУБД Kuzu (Cypher) для персистентного
хранения узлов и рёбер.
"""
import gc
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import kuzu

from config import Config

logger = logging.getLogger(__name__)


class GraphDBManager:
    """Менеджер графовой БД (Kuzu) для конфигурации 1С"""

    NODE_TYPES = ("Metadata", "Method", "Form")
    EDGE_TYPES = ("REFERENCES", "HAS_METHOD", "HAS_FORM", "ATTRIBUTE_TYPE", "USES_IN_CODE")

    def __init__(self, db_path: Optional[str] = None, read_only: bool = False):
        """
        Args:
            db_path: каталог БД Kuzu
            read_only: открыть БД только на чтение. Kuzu даёт пишущему процессу
                ЭКСКЛЮЗИВНУЮ блокировку каталога, поэтому одновременно работать
                могут только читатели. Для MCP-сервера (он лишь читает) это
                позволяет держать несколько экземпляров сразу — например,
                локальный на сервере и подключённый по SSH с другой машины.
        """
        self.db_path = Path(db_path or Config.GRAPHDB_PATH)
        self.read_only = read_only
        if not read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        elif not self.db_path.exists():
            raise FileNotFoundError(
                f"Графовая БД не найдена: {self.db_path}. "
                f"В режиме только-чтения она не создаётся — выполните индексацию графа."
            )
        self._db: Optional[kuzu.Database] = None
        self._conn: Optional[kuzu.Connection] = None
        if not read_only:
            self._init_db()
        mode = "только чтение" if read_only else "чтение и запись"
        logger.info(f"Графовая БД (Kuzu) инициализирована: {self.db_path} ({mode})")

    def _get_conn(self) -> kuzu.Connection:
        if self._conn is None:
            self._db = kuzu.Database(str(self.db_path), read_only=self.read_only)
            self._conn = kuzu.Connection(self._db)
        return self._conn

    def _require_writable(self, action: str):
        if self.read_only:
            raise RuntimeError(
                f"{action}: графовая БД открыта только на чтение (read_only=True)."
            )

    def _init_db(self):
        self._require_writable("Создание схемы")
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
        self._require_writable("Очистка графа")
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
        self._require_writable("Добавление узла")
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
        self._require_writable("Добавление ребра")
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
