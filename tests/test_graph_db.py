"""Тесты для graph_db: GraphDBManager на Kuzu."""
import json

import pytest

from graph_db import GraphDBManager


@pytest.fixture
def graph(graph_db_path):
    """Создаёт экземпляр GraphDBManager с временной БД."""
    gm = GraphDBManager(db_path=graph_db_path)
    yield gm
    gm.close()


def _node_field(graph, node_id, field):
    """Возвращает значение свойства узла по id через Cypher."""
    result = graph._get_conn().execute(
        f"MATCH (n:Node {{id: $id}}) RETURN n.{field}", {"id": node_id}
    )
    if result.has_next():
        return result.get_next()[0]
    return None


def _edge_field(graph, field):
    """Возвращает значение свойства первого ребра через Cypher."""
    result = graph._get_conn().execute(f"MATCH ()-[r:REL]->() RETURN r.{field}")
    if result.has_next():
        return result.get_next()[0]
    return None


class TestGraphDBManagerInit:
    """Инициализация и подключение."""

    def test_creates_db(self, graph_db_path):
        gm = GraphDBManager(db_path=graph_db_path)
        from pathlib import Path
        assert Path(graph_db_path).exists()
        gm.close()

    def test_empty_after_init(self, graph):
        stats = graph.get_stats()
        assert stats["nodes_count"] == 0
        assert stats["edges_count"] == 0

    def test_close_and_reconnect(self, graph_db_path):
        gm = GraphDBManager(db_path=graph_db_path)
        gm.add_node("test:1", "Metadata", "Test")
        gm.close()

        gm2 = GraphDBManager(db_path=graph_db_path)
        stats = gm2.get_stats()
        assert stats["nodes_count"] == 1
        gm2.close()


class TestAddNode:
    """Добавление узлов."""

    def test_add_node(self, graph):
        graph.add_node("n1", "Metadata", "TestNode")
        stats = graph.get_stats()
        assert stats["nodes_count"] == 1

    def test_add_node_with_all_fields(self, graph):
        graph.add_node(
            "n1", "Metadata", "TestNode",
            object_type="Catalogs", object_name="Номенклатура",
            synonym="Товары", extra={"custom": "data"},
        )
        assert _node_field(graph, "n1", "synonym") == "Товары"
        extra = json.loads(_node_field(graph, "n1", "extra"))
        assert extra["custom"] == "data"

    def test_upsert_replaces_existing(self, graph):
        graph.add_node("n1", "Metadata", "Old")
        graph.add_node("n1", "Metadata", "New")
        assert _node_field(graph, "n1", "name") == "New"
        assert graph.get_stats()["nodes_count"] == 1

    def test_invalid_node_type_raises(self, graph):
        with pytest.raises(ValueError, match="Неизвестный тип узла"):
            graph.add_node("n1", "InvalidType", "Name")

    def test_all_valid_node_types(self, graph):
        for i, nt in enumerate(GraphDBManager.NODE_TYPES):
            graph.add_node(f"n{i}", nt, f"Name{i}")
        assert graph.get_stats()["nodes_count"] == len(GraphDBManager.NODE_TYPES)


class TestAddEdge:
    """Добавление рёбер."""

    def test_add_edge(self, graph):
        graph.add_node("s1", "Metadata", "Source")
        graph.add_node("t1", "Metadata", "Target")
        graph.add_edge("s1", "t1", "REFERENCES")
        stats = graph.get_stats()
        assert stats["edges_count"] == 1

    def test_no_duplicate_edges(self, graph):
        graph.add_node("s1", "Metadata", "Source")
        graph.add_node("t1", "Metadata", "Target")
        graph.add_edge("s1", "t1", "REFERENCES")
        graph.add_edge("s1", "t1", "REFERENCES")
        assert graph.get_stats()["edges_count"] == 1

    def test_different_edge_types_not_deduplicated(self, graph):
        graph.add_node("s1", "Metadata", "Source")
        graph.add_node("t1", "Metadata", "Target")
        graph.add_edge("s1", "t1", "REFERENCES")
        graph.add_edge("s1", "t1", "HAS_METHOD")
        assert graph.get_stats()["edges_count"] == 2

    def test_invalid_edge_type_raises(self, graph):
        graph.add_node("s1", "Metadata", "Source")
        graph.add_node("t1", "Metadata", "Target")
        with pytest.raises(ValueError, match="Неизвестный тип ребра"):
            graph.add_edge("s1", "t1", "INVALID_EDGE")

    def test_edge_with_extra(self, graph):
        graph.add_node("s1", "Metadata", "Source")
        graph.add_node("t1", "Metadata", "Target")
        graph.add_edge("s1", "t1", "REFERENCES", extra={"context": "test"})
        extra = json.loads(_edge_field(graph, "extra"))
        assert extra["context"] == "test"


class TestEnsureMetadataNode:
    """Создание метаданных через ensure_metadata_node."""

    def test_creates_node_with_correct_id(self, graph):
        node_id = graph.ensure_metadata_node("Catalogs", "Номенклатура", "Товары")
        assert node_id == "metadata:Catalogs:Номенклатура"

    def test_idempotent(self, graph):
        graph.ensure_metadata_node("Catalogs", "Номенклатура")
        graph.ensure_metadata_node("Catalogs", "Номенклатура")
        assert graph.get_stats()["nodes_count"] == 1

    def test_node_has_correct_type(self, graph):
        graph.ensure_metadata_node("Documents", "Заказ")
        assert _node_field(graph, "metadata:Documents:Заказ", "node_type") == "Metadata"


class TestClear:
    """Очистка графа."""

    def test_clear_removes_all(self, graph):
        graph.add_node("n1", "Metadata", "A")
        graph.add_node("n2", "Metadata", "B")
        graph.add_edge("n1", "n2", "REFERENCES")
        graph.clear()
        stats = graph.get_stats()
        assert stats["nodes_count"] == 0
        assert stats["edges_count"] == 0


class TestGetDependencies:
    """Поиск зависимостей (кто ссылается на объект)."""

    def test_finds_dependencies(self, graph):
        src = graph.ensure_metadata_node("Catalogs", "Контрагенты")
        tgt = graph.ensure_metadata_node("Documents", "Заказ")
        graph.add_edge(tgt, src, "REFERENCES")
        deps = graph.get_dependencies("Контрагенты")
        assert len(deps) >= 1
        assert any("Заказ" in d["object"] for d in deps)

    def test_empty_when_no_deps(self, graph):
        graph.ensure_metadata_node("Catalogs", "Одинокий")
        deps = graph.get_dependencies("Одинокий")
        assert deps == []

    def test_limit_applied(self, graph):
        target = graph.ensure_metadata_node("Catalogs", "Цель")
        for i in range(10):
            src = graph.ensure_metadata_node("Documents", f"Док{i}")
            graph.add_edge(src, target, "REFERENCES")
        deps = graph.get_dependencies("Цель", limit=3)
        assert len(deps) <= 3

    def test_limit_clamped_to_min(self, graph):
        target = graph.ensure_metadata_node("Catalogs", "X")
        src = graph.ensure_metadata_node("Documents", "Y")
        graph.add_edge(src, target, "REFERENCES")
        deps = graph.get_dependencies("X", limit=-5)
        assert len(deps) >= 1


class TestGetReferences:
    """Поиск ссылок (на что ссылается объект)."""

    def test_finds_references(self, graph):
        src = graph.ensure_metadata_node("Documents", "Заказ")
        tgt = graph.ensure_metadata_node("Catalogs", "Номенклатура")
        graph.add_edge(src, tgt, "REFERENCES")
        refs = graph.get_references("Заказ")
        assert len(refs) >= 1
        assert any("Номенклатура" in r["object"] for r in refs)

    def test_empty_when_no_refs(self, graph):
        graph.ensure_metadata_node("Catalogs", "БезСсылок")
        refs = graph.get_references("БезСсылок")
        assert refs == []


class TestGetStats:
    """Статистика графа."""

    def test_empty_stats(self, graph):
        stats = graph.get_stats()
        assert stats["nodes_count"] == 0
        assert stats["edges_count"] == 0
        assert stats["nodes_by_type"] == {}
        assert stats["edges_by_type"] == {}

    def test_stats_counts(self, graph):
        graph.add_node("m1", "Metadata", "A")
        graph.add_node("m2", "Method", "B")
        graph.add_edge("m1", "m2", "HAS_METHOD")
        stats = graph.get_stats()
        assert stats["nodes_count"] == 2
        assert stats["edges_count"] == 1
        assert stats["nodes_by_type"]["Metadata"] == 1
        assert stats["nodes_by_type"]["Method"] == 1
        assert stats["edges_by_type"]["HAS_METHOD"] == 1
