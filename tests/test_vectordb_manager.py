"""Тесты для vectordb_manager: VectorDBManager, _tokenize, _hybrid_rerank, _apply_mmr."""
from unittest.mock import MagicMock, patch
from typing import List

import pytest


class DummyEmbeddingFn:
    """Фейковая функция эмбеддинга для тестов (возвращает нулевые вектора)."""

    def __call__(self, input: List[str]) -> List[List[float]]:
        return [[0.0] * 10 for _ in input]

    def name(self) -> str:
        return "DummyEmbedding"


@pytest.fixture
def vdb(tmp_path, monkeypatch):
    """Создаёт VectorDBManager с in-memory-подобным хранением."""
    monkeypatch.setenv("CONFIG_PATH", "")
    monkeypatch.setenv("PROJECT_PROFILE", "default")
    monkeypatch.setenv("EMBEDDING_API_BASE", "")
    monkeypatch.setenv("EMBEDDING_MODEL", "dummy-model")
    monkeypatch.setenv("HYBRID_SEARCH_ALPHA", "1.0")
    monkeypatch.setenv("SEARCH_USE_MMR", "false")

    import importlib
    import config
    importlib.reload(config)

    with patch(
        "vectordb_manager.embedding_functions.SentenceTransformerEmbeddingFunction",
        return_value=DummyEmbeddingFn(),
    ):
        from vectordb_manager import VectorDBManager
        manager = VectorDBManager(db_path=str(tmp_path / "test_vdb"))

    return manager


class TestTokenize:
    """Токенизация для BM25."""

    def test_basic_tokenization(self, vdb):
        tokens = vdb._tokenize("Привет мир тест")
        assert "привет" in tokens
        assert "мир" in tokens
        assert "тест" in tokens

    def test_removes_punctuation(self, vdb):
        tokens = vdb._tokenize("Функция(); // комментарий")
        assert "функция" in tokens
        assert "()" not in tokens

    def test_filters_single_char(self, vdb):
        tokens = vdb._tokenize("а б в слово")
        assert "а" not in tokens
        assert "б" not in tokens
        assert "слово" in tokens

    def test_empty_string(self, vdb):
        assert vdb._tokenize("") == []

    def test_only_punctuation(self, vdb):
        assert vdb._tokenize("!@#$%^&*()") == []


class TestHybridRerank:
    """Гибридный re-ranking (вектор + BM25)."""

    def test_alpha_1_returns_unchanged(self, vdb):
        items = [("doc1", {}, 0.1), ("doc2", {}, 0.2)]
        result = vdb._hybrid_rerank("query", items, alpha=1.0)
        assert result == items

    def test_empty_items_returns_empty(self, vdb):
        result = vdb._hybrid_rerank("query", [], alpha=0.5)
        assert result == []

    def test_reranks_with_bm25(self, vdb):
        items = [
            ("номенклатура товар продукт", {}, 0.3),
            ("совершенно другой текст", {}, 0.2),
        ]
        result = vdb._hybrid_rerank("номенклатура товар", items, alpha=0.5)
        assert len(result) == 2

    def test_result_is_sorted_by_distance(self, vdb):
        items = [
            ("тест функция процедура", {"a": 1}, 0.5),
            ("тест функция код", {"a": 2}, 0.6),
            ("другой текст вовсе", {"a": 3}, 0.1),
        ]
        result = vdb._hybrid_rerank("тест функция", items, alpha=0.5)
        distances = [r[2] for r in result]
        assert distances == sorted(distances)


class TestApplyMmr:
    """Maximal Marginal Relevance."""

    def test_limits_results(self, vdb):
        items = [(f"doc{i}", {}, 0.1 * i) for i in range(10)]
        result = vdb._apply_mmr(items, "query", limit=3, lambda_param=0.7)
        assert len(result) == 3

    def test_full_lambda_returns_by_relevance(self, vdb):
        items = [
            ("doc a b c", {}, 0.1),
            ("doc d e f", {}, 0.2),
            ("doc g h i", {}, 0.3),
        ]
        result = vdb._apply_mmr(items, "query", limit=2, lambda_param=1.0)
        assert len(result) == 2
        assert result[0][2] <= result[1][2]

    def test_empty_items(self, vdb):
        result = vdb._apply_mmr([], "query", limit=5, lambda_param=0.5)
        assert result == []

    def test_limit_greater_than_items(self, vdb):
        items = [("doc1", {}, 0.1)]
        result = vdb._apply_mmr(items, "query", limit=10, lambda_param=0.5)
        assert len(result) == 1


class TestAddCodeChunks:
    """Добавление чанков кода."""

    def test_add_single_chunk(self, vdb):
        chunks = [{
            "method_name": "Тест",
            "method_type": "Функция",
            "params": "",
            "signature": "Функция Тест()",
            "is_export": False,
            "code": "Функция Тест()\n    Возврат 1;\nКонецФункции",
            "body": "    Возврат 1;",
            "comments": [],
            "file_path": "/test.bsl",
            "object_type": "Catalogs",
            "object_name": "Номенклатура",
        }]
        vdb.add_code_chunks(chunks)
        stats = vdb.get_stats()
        assert stats["code"] == 1

    def test_add_multiple_chunks(self, vdb):
        chunks = [
            {
                "method_name": f"Метод{i}",
                "method_type": "Функция",
                "params": "",
                "signature": f"Функция Метод{i}()",
                "is_export": False,
                "code": f"Функция Метод{i}()\nКонецФункции",
                "body": "",
                "comments": [],
                "file_path": "/test.bsl",
            }
            for i in range(5)
        ]
        vdb.add_code_chunks(chunks)
        assert vdb.get_stats()["code"] == 5

    def test_continuation_chunk_omits_comments_in_document(self, vdb):
        """Во 2+ чанке метода комментарии не дублируются в тексте для эмбеддинга."""
        shared_comments = ["Параметры: Регистратор", "Документ основание"]
        chunks = [
            {
                "method_name": "ДлиннаяПроцедура",
                "method_type": "Процедура",
                "signature": "Процедура ДлиннаяПроцедура()",
                "is_export": False,
                "code": "Процедура ДлиннаяПроцедура()\n    А = 1;\nКонецПроцедуры",
                "comments": shared_comments,
                "file_path": "/test.bsl",
                "object_type": "Catalogs",
                "object_name": "Тест",
                "chunk_index": 0,
                "total_chunks": 2,
            },
            {
                "method_name": "ДлиннаяПроцедура",
                "method_type": "Процедура",
                "signature": "Процедура ДлиннаяПроцедура()",
                "is_export": False,
                "code": "Процедура ДлиннаяПроцедура()\n    Б = 2;\nКонецПроцедуры",
                "comments": shared_comments,
                "file_path": "/test.bsl",
                "object_type": "Catalogs",
                "object_name": "Тест",
                "chunk_index": 1,
                "total_chunks": 2,
            },
        ]
        vdb.add_code_chunks(chunks)
        coll = vdb.collections["code"]
        first = coll.get(ids=["code_0_ДлиннаяПроцедура_0"], include=["documents"])["documents"][0]
        second = coll.get(ids=["code_1_ДлиннаяПроцедура_1"], include=["documents"])["documents"][0]
        assert "// Параметры: Регистратор" in first
        assert "// Параметры: Регистратор" not in second
        assert "Процедура ДлиннаяПроцедура()" in second

    def test_add_empty_list(self, vdb):
        vdb.add_code_chunks([])
        assert vdb.get_stats()["code"] == 0


class TestAddMetadataObjects:
    """Добавление метаданных."""

    def test_add_metadata(self, vdb):
        objs = [{
            "name": "Номенклатура",
            "type": "Catalog",
            "synonym": "Товары",
            "comment": "Справочник товаров",
            "attributes": [{"name": "Артикул", "type": "Строка"}],
            "attributes_count": 1,
            "tabular_sections": [],
            "has_modules": ["ObjectModule"],
            "file_path": "/test.xml",
            "object_type_dir": "Catalogs",
        }]
        vdb.add_metadata_objects(objs)
        assert vdb.get_stats()["metadata"] == 1


class TestAddForms:
    """Добавление форм."""

    def test_add_form(self, vdb):
        forms = [{
            "form_name": "ФормаЭлемента",
            "object_type": "Catalogs",
            "object_name": "Номенклатура",
            "elements": ["Наименование", "Артикул"],
            "elements_count": 2,
            "file_path": "/Form.xml",
        }]
        vdb.add_forms(forms)
        assert vdb.get_stats()["forms"] == 1


class TestSearchCode:
    """Поиск по коду."""

    def test_search_returns_results(self, vdb):
        chunks = [{
            "method_name": "ПолучитьЦену",
            "method_type": "Функция",
            "params": "Номенклатура",
            "signature": "Функция ПолучитьЦену(Номенклатура)",
            "is_export": True,
            "code": "Функция ПолучитьЦену(Номенклатура)\n    Возврат 100;\nКонецФункции",
            "body": "    Возврат 100;",
            "comments": ["Получить цену номенклатуры"],
            "file_path": "/price.bsl",
        }]
        vdb.add_code_chunks(chunks)
        results = vdb.search_code("цена номенклатура", limit=5)
        assert len(results) >= 1
        assert "rank" in results[0]
        assert "relevance" in results[0]
        assert "document" in results[0]
        assert "metadata" in results[0]


class TestSearchMetadata:
    """Поиск по метаданным."""

    def test_search_metadata_returns_results(self, vdb):
        objs = [{
            "name": "Контрагенты",
            "type": "Catalog",
            "synonym": "Контрагенты",
            "comment": "Справочник контрагентов",
            "attributes": [],
            "attributes_count": 0,
            "tabular_sections": [],
            "has_modules": [],
            "file_path": "/test.xml",
            "object_type_dir": "Catalogs",
        }]
        vdb.add_metadata_objects(objs)
        results = vdb.search_metadata("контрагент", limit=5)
        assert len(results) >= 1


class TestSearchForms:
    """Поиск по формам."""

    def test_search_forms_returns_results(self, vdb):
        forms = [{
            "form_name": "ФормаСписка",
            "object_type": "Documents",
            "object_name": "Заказ",
            "elements": ["Дата", "Номер", "Контрагент"],
            "elements_count": 3,
            "file_path": "/Form.xml",
        }]
        vdb.add_forms(forms)
        results = vdb.search_forms("форма заказ", limit=5)
        assert len(results) >= 1


class TestClearCollections:
    """Очистка коллекций."""

    def test_clear_removes_data(self, vdb):
        vdb.add_code_chunks([{
            "method_name": "Тест",
            "method_type": "Функция",
            "params": "",
            "signature": "Функция Тест()",
            "is_export": False,
            "code": "Функция Тест()\nКонецФункции",
            "body": "",
            "comments": [],
            "file_path": "/test.bsl",
        }])
        assert vdb.get_stats()["code"] == 1
        vdb.clear_all_collections()
        assert vdb.get_stats()["code"] == 0


class TestGetStats:
    """Статистика."""

    def test_initial_stats_zero(self, vdb):
        stats = vdb.get_stats()
        assert stats["code"] == 0
        assert stats["metadata"] == 0
        assert stats["forms"] == 0


class TestFormatResults:
    """Форматирование результатов."""

    def test_format_results_from_items(self, vdb):
        items = [
            ("doc1", {"key": "val1"}, 0.1),
            ("doc2", {"key": "val2"}, 0.3),
        ]
        formatted = vdb._format_results_from_items(items)
        assert len(formatted) == 2
        assert formatted[0]["rank"] == 1
        assert formatted[0]["relevance"] == 0.9
        assert formatted[1]["rank"] == 2
        assert formatted[1]["relevance"] == 0.7

    def test_format_empty_items(self, vdb):
        assert vdb._format_results_from_items([]) == []


class TestQwenEOSWrapper:
    """QwenEOSEmbeddingWrapper."""

    def test_adds_eos_suffix(self):
        from vectordb_manager import QwenEOSEmbeddingWrapper, QWEN3_EOS_SUFFIX
        base = DummyEmbeddingFn()
        wrapper = QwenEOSEmbeddingWrapper(base)

        called_with = []
        original_call = base.__call__

        def capturing_call(input):
            called_with.extend(input)
            return original_call(input)

        wrapper._base = MagicMock(side_effect=capturing_call)
        wrapper(["text1", "text2"])
        assert all(t.endswith(QWEN3_EOS_SUFFIX) for t in called_with)

    def test_no_double_eos(self):
        from vectordb_manager import QwenEOSEmbeddingWrapper, QWEN3_EOS_SUFFIX
        base = DummyEmbeddingFn()
        wrapper = QwenEOSEmbeddingWrapper(base)

        called_with = []

        def capturing_call(input):
            called_with.extend(input)
            return [[0.0] * 10 for _ in input]

        wrapper._base = MagicMock(side_effect=capturing_call)
        text_with_eos = f"already has eos{QWEN3_EOS_SUFFIX}"
        wrapper([text_with_eos])
        assert called_with[0].count(QWEN3_EOS_SUFFIX) == 1

    def test_name_returns_string(self):
        from vectordb_manager import QwenEOSEmbeddingWrapper
        wrapper = QwenEOSEmbeddingWrapper(DummyEmbeddingFn())
        assert isinstance(wrapper.name(), str)
