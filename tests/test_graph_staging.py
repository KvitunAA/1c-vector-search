"""Тесты graph_staging и compact_graph."""
import csv
from pathlib import Path

import kuzu
import pytest

from compact_graph import compact_staging_to_kuzu
from graph_staging import GraphStagingWriter


def test_staging_dedupe_and_csv(tmp_path):
    staging = GraphStagingWriter(str(tmp_path / "staging"))
    staging.clear()

    staging.add_nodes_batch(
        [
            {"node_id": "metadata:Catalogs:A", "node_type": "Metadata", "name": "A"},
            {"node_id": "metadata:Catalogs:A", "node_type": "Metadata", "name": "A"},
            {
                "node_id": "method:Catalogs:A:Mod:Foo",
                "node_type": "Method",
                "name": "Foo",
                "extra": {"module": "Mod"},
            },
        ]
    )
    staging.add_edges_batch(
        [
            {
                "source": "metadata:Catalogs:A",
                "target": "method:Catalogs:A:Mod:Foo",
                "edge_type": "HAS_METHOD",
            },
            {
                "source": "metadata:Catalogs:A",
                "target": "method:Catalogs:A:Mod:Foo",
                "edge_type": "HAS_METHOD",
            },
        ]
    )

    stats = staging.get_stats()
    assert stats["nodes_count"] == 2
    assert stats["edges_count"] == 1

    nodes_csv, edges_csv = staging.write_csv_files()
    assert nodes_csv.is_file()
    assert edges_csv.is_file()

    with nodes_csv.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert any("Synonym" in row.get("synonym", "") or row["name"] == "A" for row in rows)


def test_compact_staging_to_kuzu(tmp_path):
    staging_dir = tmp_path / "staging"
    graph_path = tmp_path / "graph.db"
    staging = GraphStagingWriter(str(staging_dir))
    staging.clear()
    staging.add_nodes_batch(
        [
            {"node_id": "metadata:Catalogs:A", "node_type": "Metadata", "name": "A"},
            {"node_id": "metadata:Catalogs:B", "node_type": "Metadata", "name": "B"},
        ]
    )
    staging.add_edges_batch(
        [
            {
                "source": "metadata:Catalogs:A",
                "target": "metadata:Catalogs:B",
                "edge_type": "USES_IN_CODE",
            }
        ]
    )
    staging.write_csv_files()

    result = compact_staging_to_kuzu(staging_dir, graph_path, backup_existing=False)
    assert result["nodes_count"] == 2
    assert result["edges_count"] == 1
    assert graph_path.exists()

    db = kuzu.Database(str(graph_path), buffer_pool_size=0)
    conn = kuzu.Connection(db)
    assert conn.execute("MATCH (n:Node) RETURN count(n)").get_next()[0] == 2
    db.close()
