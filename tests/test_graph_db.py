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


class TestAddNodesBatch:
    """Пакетное добавление узлов."""

    def test_empty_batch_noop(self, graph):
        graph.add_nodes_batch([])
        assert graph.get_stats()["nodes_count"] == 0

    def test_batch_insert(self, graph):
        graph.add_nodes_batch(
            [
                {
                    "node_id": "metadata:Catalogs:Номенклатура",
                    "node_type": "Metadata",
                    "name": "Номенклатура",
                    "object_type": "Catalogs",
                    "object_name": "Номенклатура",
                    "synonym": "Товары",
                },
                {
                    "node_id": "method:Catalogs:Номенклатура:Module:Тест",
                    "node_type": "Method",
                    "name": "Тест",
                    "object_type": "Catalogs",
                    "object_name": "Номенклатура",
                },
            ]
        )
        stats = graph.get_stats()
        assert stats["nodes_count"] == 2
        assert _node_field(graph, "metadata:Catalogs:Номенклатура", "synonym") == "Товары"

    def test_batch_upsert(self, graph):
        graph.add_nodes_batch(
            [{"node_id": "n1", "node_type": "Metadata", "name": "Old"}]
        )
        graph.add_nodes_batch(
            [{"node_id": "n1", "node_type": "Metadata", "name": "New"}]
        )
        assert _node_field(graph, "n1", "name") == "New"
        assert graph.get_stats()["nodes_count"] == 1

    def test_batch_dedupe_by_id(self, graph):
        graph.add_nodes_batch(
            [
                {"node_id": "n1", "node_type": "Metadata", "name": "First"},
                {"node_id": "n1", "node_type": "Metadata", "name": "Second"},
            ]
        )
        assert _node_field(graph, "n1", "name") == "Second"
        assert graph.get_stats()["nodes_count"] == 1

    def test_invalid_node_type_raises(self, graph):
        with pytest.raises(ValueError, match="Неизвестный тип узла"):
            graph.add_nodes_batch(
                [{"node_id": "n1", "node_type": "InvalidType", "name": "X"}]
            )


class TestAddEdgesBatch:
    """Пакетное добавление рёбер."""

    def test_empty_batch_noop(self, graph):
        graph.add_nodes_batch([{"node_id": "n1", "node_type": "Metadata", "name": "A"}])
        graph.add_edges_batch([])
        assert graph.get_stats()["edges_count"] == 0

    def test_batch_insert(self, graph):
        graph.add_nodes_batch(
            [
                {"node_id": "s1", "node_type": "Metadata", "name": "Source"},
                {"node_id": "t1", "node_type": "Metadata", "name": "Target"},
            ]
        )
        graph.add_edges_batch(
            [{"source": "s1", "target": "t1", "edge_type": "REFERENCES"}]
        )
        assert graph.get_stats()["edges_count"] == 1

    def test_batch_dedupe_same_type(self, graph):
        graph.add_nodes_batch(
            [
                {"node_id": "s1", "node_type": "Metadata", "name": "Source"},
                {"node_id": "t1", "node_type": "Metadata", "name": "Target"},
            ]
        )
        graph.add_edges_batch(
            [
                {"source": "s1", "target": "t1", "edge_type": "REFERENCES"},
                {"source": "s1", "target": "t1", "edge_type": "REFERENCES"},
            ]
        )
        assert graph.get_stats()["edges_count"] == 1

    def test_batch_different_edge_types(self, graph):
        graph.add_nodes_batch(
            [
                {"node_id": "s1", "node_type": "Metadata", "name": "Source"},
                {"node_id": "t1", "node_type": "Metadata", "name": "Target"},
            ]
        )
        graph.add_edges_batch(
            [
                {"source": "s1", "target": "t1", "edge_type": "REFERENCES"},
                {"source": "s1", "target": "t1", "edge_type": "HAS_METHOD"},
            ]
        )
        assert graph.get_stats()["edges_count"] == 2

    def test_invalid_edge_type_raises(self, graph):
        graph.add_nodes_batch(
            [
                {"node_id": "s1", "node_type": "Metadata", "name": "Source"},
                {"node_id": "t1", "node_type": "Metadata", "name": "Target"},
            ]
        )
        with pytest.raises(ValueError, match="Неизвестный тип ребра"):
            graph.add_edges_batch(
                [{"source": "s1", "target": "t1", "edge_type": "INVALID_EDGE"}]
            )

    def test_batch_with_extra(self, graph):
        graph.add_nodes_batch(
            [
                {"node_id": "s1", "node_type": "Metadata", "name": "Source"},
                {"node_id": "t1", "node_type": "Metadata", "name": "Target"},
            ]
        )
        graph.add_edges_batch(
            [
                {
                    "source": "s1",
                    "target": "t1",
                    "edge_type": "REFERENCES",
                    "extra": {"context": "batch"},
                }
            ]
        )
        extra = json.loads(_edge_field(graph, "extra"))
        assert extra["context"] == "batch"


class TestCsvUnsafeRowsGoRowwise:
    """Kuzu LOAD FROM ломается на запятых и кавычках — такие строки пишутся MERGE поштучно."""

    def test_role_synonym_with_commas(self, graph):
        synonym = "Роль на чтение, в базе, документов"
        graph.add_nodes_batch(
            [
                {
                    "node_id": "metadata:Roles:ЧтениеДокументов",
                    "node_type": "Metadata",
                    "name": "ЧтениеДокументов",
                    "object_type": "Roles",
                    "object_name": "ЧтениеДокументов",
                    "synonym": synonym,
                },
                {
                    "node_id": "metadata:Catalogs:Номенклатура",
                    "node_type": "Metadata",
                    "name": "Номенклатура",
                    "object_type": "Catalogs",
                    "object_name": "Номенклатура",
                    "synonym": "Товары",
                },
            ]
        )
        assert _node_field(graph, "metadata:Roles:ЧтениеДокументов", "synonym") == synonym
        assert _node_field(graph, "metadata:Catalogs:Номенклатура", "synonym") == "Товары"
        assert graph.get_stats()["nodes_count"] == 2

    def test_quoted_name_and_extra_still_work(self, graph):
        graph.add_nodes_batch(
            [
                {
                    "node_id": "n1",
                    "node_type": "Metadata",
                    "name": 'Роль "Базовые права"',
                    "synonym": 'Чтение, запись',
                    "extra": {"rights": ["Read", "View"]},
                }
            ]
        )
        assert _node_field(graph, "n1", "name") == 'Роль "Базовые права"'
        assert _node_field(graph, "n1", "synonym") == "Чтение, запись"
        extra = json.loads(_node_field(graph, "n1", "extra"))
        assert extra["rights"] == ["Read", "View"]

    def test_row_needs_rowwise_detects_comma_and_quote(self):
        assert GraphDBManager._row_needs_rowwise({"synonym": "Роль на чтение, в базе"})
        assert GraphDBManager._row_needs_rowwise({"name": 'Роль "Чтение"'})
        assert not GraphDBManager._row_needs_rowwise(
            {"id": "n1", "name": "Номенклатура", "synonym": "Товары", "extra": ""}
        )


class TestGraphLockAndReadOnly:
    """Lock graph.db и read-only для MCP."""

    def test_second_writer_reports_lock(self, graph_db_path):
        first = GraphDBManager(db_path=graph_db_path, lock_retries=1)
        try:
            with pytest.raises(RuntimeError, match="файл занят"):
                GraphDBManager(db_path=graph_db_path, lock_retries=1)
        finally:
            first.close()

    def test_read_only_after_writer_closed(self, graph_db_path):
        writer = GraphDBManager(db_path=graph_db_path)
        writer.add_node("n1", "Metadata", "A")
        writer.close()
        reader = GraphDBManager(db_path=graph_db_path, read_only=True, lock_retries=1)
        try:
            assert reader.get_stats()["nodes_count"] == 1
        finally:
            reader.close()

    def test_read_only_missing_db_empty_stats(self, tmp_path):
        missing = str(tmp_path / "no_graph.db")
        reader = GraphDBManager(db_path=missing, read_only=True, lock_retries=1)
        try:
            stats = reader.get_stats()
            assert stats["nodes_count"] == 0
            assert reader.get_dependencies("X") == []
            assert reader.get_references("X") == []
        finally:
            reader.close()


class TestBatchIntegration:
    """Смешанный сценарий batch + запросы графа."""

    def test_batch_nodes_edges_and_references(self, graph):
        graph.add_nodes_batch(
            [
                {
                    "node_id": "metadata:Documents:Заказ",
                    "node_type": "Metadata",
                    "name": "Заказ",
                    "object_type": "Documents",
                    "object_name": "Заказ",
                },
                {
                    "node_id": "metadata:Catalogs:Номенклатура",
                    "node_type": "Metadata",
                    "name": "Номенклатура",
                    "object_type": "Catalogs",
                    "object_name": "Номенклатура",
                },
            ]
        )
        graph.add_edges_batch(
            [
                {
                    "source": "metadata:Documents:Заказ",
                    "target": "metadata:Catalogs:Номенклатура",
                    "edge_type": "REFERENCES",
                }
            ]
        )
        refs = graph.get_references("Заказ")
        assert len(refs) >= 1
        assert any("Номенклатура" in item["object"] for item in refs)


class TestNewEdgeTypes:
    """Рёбра прав и макетов СКД."""

    def test_has_right_and_has_template(self, graph):
        graph.add_nodes_batch(
            [
                {"node_id": "metadata:Roles:Чтение", "node_type": "Metadata", "name": "Чтение"},
                {"node_id": "metadata:Catalogs:Номенклатура", "node_type": "Metadata", "name": "Номенклатура"},
                {"node_id": "metadata:Reports:Остатки", "node_type": "Metadata", "name": "Остатки"},
                {
                    "node_id": "metadata:Templates:Reports.Остатки.ОсновнаяСхема",
                    "node_type": "Metadata",
                    "name": "Reports.Остатки.ОсновнаяСхема",
                },
            ]
        )
        graph.add_edges_batch(
            [
                {
                    "source": "metadata:Roles:Чтение",
                    "target": "metadata:Catalogs:Номенклатура",
                    "edge_type": "HAS_RIGHT",
                },
                {
                    "source": "metadata:Reports:Остатки",
                    "target": "metadata:Templates:Reports.Остатки.ОсновнаяСхема",
                    "edge_type": "HAS_TEMPLATE",
                },
            ]
        )
        stats = graph.get_stats()
        assert stats["edges_by_type"]["HAS_RIGHT"] == 1
        assert stats["edges_by_type"]["HAS_TEMPLATE"] == 1
