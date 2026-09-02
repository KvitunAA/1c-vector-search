"""
Промежуточное хранилище графа (CSV + in-memory dedupe) перед bulk COPY в Kuzu.
"""
from __future__ import annotations

import csv
import json
import logging
import pickle
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import Config
from graph_db import GraphDBManager, _EDGE_COLUMNS, _NODE_COLUMNS

logger = logging.getLogger(__name__)

NODE_COLUMNS = _NODE_COLUMNS
EDGE_COLUMNS = _EDGE_COLUMNS


class GraphStagingWriter:
    """Собирает узлы и рёбра без записи в Kuzu (dedupe в памяти)."""

    STATE_FILE = "staging_state.pkl"

    def __init__(self, staging_dir: Optional[str] = None):
        self.staging_dir = Path(staging_dir or Config.GRAPH_STAGING_PATH)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._nodes: Dict[str, Dict[str, str]] = {}
        self._edges: Dict[Tuple[str, str, str], Dict[str, str]] = {}
        self._load_state_if_exists()
        logger.info("Graph staging: %s (узлов %s, рёбер %s)", self.staging_dir, len(self._nodes), len(self._edges))

    def _state_path(self) -> Path:
        return self.staging_dir / self.STATE_FILE

    def _load_state_if_exists(self) -> None:
        path = self._state_path()
        if not path.is_file():
            return
        try:
            with path.open("rb") as handle:
                payload = pickle.load(handle)
            self._nodes = payload.get("nodes", {})
            self._edges = payload.get("edges", {})
            logger.info(
                "Загружен staging state: %s узлов, %s рёбер",
                len(self._nodes),
                len(self._edges),
            )
        except Exception as exc:
            logger.warning("Не удалось загрузить staging state: %s", exc)

    def save_state(self) -> None:
        path = self._state_path()
        with path.open("wb") as handle:
            pickle.dump({"nodes": self._nodes, "edges": self._edges}, handle, protocol=pickle.HIGHEST_PROTOCOL)

    def clear(self) -> None:
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._nodes.clear()
        self._edges.clear()
        logger.info("Staging очищен: %s", self.staging_dir)

    def close(self) -> None:
        """Совместимость с GraphDBManager — периодически сохраняем state."""
        self.save_state()

    def add_node(self, **kwargs: Any) -> None:
        record = GraphDBManager._normalize_node_record(
            {
                "node_id": kwargs.get("node_id") or kwargs.get("id"),
                "node_type": kwargs.get("node_type"),
                "name": kwargs.get("name"),
                "object_type": kwargs.get("object_type"),
                "object_name": kwargs.get("object_name"),
                "synonym": kwargs.get("synonym"),
                "extra": kwargs.get("extra"),
            }
        )
        self._nodes[record["id"]] = record

    def add_nodes_batch(self, nodes: List[Dict[str, Any]]) -> None:
        if not nodes:
            return
        for record in GraphDBManager._dedupe_nodes(nodes):
            self._nodes[record["id"]] = record

    def add_edges_batch(self, edges: List[Dict[str, Any]]) -> None:
        if not edges:
            return
        for record in GraphDBManager._dedupe_edges(edges):
            key = (record["source"], record["target"], record["edge_type"])
            self._edges[key] = record

    def get_stats(self) -> Dict[str, Any]:
        by_type: Dict[str, int] = {}
        for node in self._nodes.values():
            by_type[node["node_type"]] = by_type.get(node["node_type"], 0) + 1

        edge_by_type: Dict[str, int] = {}
        for edge in self._edges.values():
            edge_by_type[edge["edge_type"]] = edge_by_type.get(edge["edge_type"], 0) + 1

        return {
            "nodes_count": len(self._nodes),
            "edges_count": len(self._edges),
            "nodes_by_type": by_type,
            "edges_by_type": edge_by_type,
        }

    def write_csv_files(self) -> Tuple[Path, Path]:
        """Записывает nodes.csv и edges.csv для COPY INTO Kuzu."""
        nodes_path = self.staging_dir / "nodes.csv"
        edges_path = self.staging_dir / "edges.csv"

        with nodes_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(NODE_COLUMNS),
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()
            for node in self._nodes.values():
                writer.writerow({column: node.get(column, "") for column in NODE_COLUMNS})

        with edges_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(EDGE_COLUMNS),
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()
            for edge in self._edges.values():
                writer.writerow({column: edge.get(column, "") for column in EDGE_COLUMNS})

        meta = {
            "nodes_count": len(self._nodes),
            "edges_count": len(self._edges),
            "stats": self.get_stats(),
        }
        (self.staging_dir / "staging_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "Staging CSV: %s (%s узлов), %s (%s рёбер)",
            nodes_path,
            len(self._nodes),
            edges_path,
            len(self._edges),
        )
        return nodes_path, edges_path
