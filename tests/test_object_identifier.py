"""Тесты parse_object_identifier."""
import pytest

from object_identifier import parse_object_identifier


class TestParseObjectIdentifier:
    def test_short_name(self):
        parsed = parse_object_identifier("НачислениеЗарплаты")
        assert parsed.object_name == "НачислениеЗарплаты"
        assert parsed.object_type is None
        assert parsed.node_id is None

    def test_qualified_name(self):
        parsed = parse_object_identifier("Documents.НачислениеЗарплаты")
        assert parsed.object_type == "Documents"
        assert parsed.object_name == "НачислениеЗарплаты"
        assert parsed.node_id == "metadata:Documents:НачислениеЗарплаты"
        assert parsed.qualified_name == "Documents.НачислениеЗарплаты"

    def test_metadata_node_id(self):
        parsed = parse_object_identifier("metadata:Catalogs:Сотрудники")
        assert parsed.object_type == "Catalogs"
        assert parsed.object_name == "Сотрудники"
        assert parsed.node_id == "metadata:Catalogs:Сотрудники"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_object_identifier("  ")
