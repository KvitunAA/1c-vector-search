"""Тесты для code_grep: grep_method_usage, _find_enclosing_method, _extract_object_info_from_path."""
from pathlib import Path

import pytest

from code_grep import grep_method_usage, _find_enclosing_method, _extract_object_info_from_path


class TestExtractObjectInfoFromPath:
    """Извлечение типа/имени объекта из пути к файлу."""

    def test_standard_path(self, tmp_path):
        config = tmp_path / "config"
        bsl = config / "Catalogs" / "Номенклатура" / "МодульОбъекта.bsl"
        bsl.parent.mkdir(parents=True)
        bsl.touch()
        info = _extract_object_info_from_path(bsl, config)
        assert info["object_type"] == "Catalogs"
        assert info["object_name"] == "Номенклатура"
        assert info["module_name"] == "МодульОбъекта"

    def test_root_level_file(self, tmp_path):
        config = tmp_path / "config"
        bsl = config / "Module.bsl"
        bsl.parent.mkdir(parents=True)
        bsl.touch()
        info = _extract_object_info_from_path(bsl, config)
        assert info["object_type"] == "Module.bsl"

    def test_unrelated_path_fallback(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir()
        other = tmp_path / "other" / "module.bsl"
        other.parent.mkdir(parents=True)
        other.touch()
        info = _extract_object_info_from_path(other, config)
        assert info["object_type"] == "Unknown"


class TestFindEnclosingMethod:
    """Поиск охватывающего метода по номеру строки."""

    SAMPLE_CODE = """\
Переменная = 1;

Процедура Первая()
    Действие1();
КонецПроцедуры

Функция Вторая(Параметр)
    Возврат Параметр;
КонецФункции
"""

    def test_line_inside_first_procedure(self):
        result = _find_enclosing_method(self.SAMPLE_CODE, 4)
        assert result == "Первая"

    def test_line_inside_second_function(self):
        result = _find_enclosing_method(self.SAMPLE_CODE, 8)
        assert result == "Вторая"

    def test_line_before_any_method(self):
        result = _find_enclosing_method(self.SAMPLE_CODE, 1)
        assert result is None

    def test_line_out_of_range(self):
        result = _find_enclosing_method(self.SAMPLE_CODE, 999)
        assert result is None

    def test_zero_line(self):
        result = _find_enclosing_method(self.SAMPLE_CODE, 0)
        assert result is None

    def test_negative_line(self):
        result = _find_enclosing_method(self.SAMPLE_CODE, -1)
        assert result is None


class TestGrepMethodUsage:
    """Grep по BSL-файлам."""

    def test_finds_method_call(self, tmp_path):
        config = tmp_path / "config" / "Catalogs" / "Test"
        config.mkdir(parents=True)
        bsl = config / "Module.bsl"
        bsl.write_text(
            "Процедура Тест()\n    МояПроцедура();\nКонецПроцедуры\n",
            encoding="utf-8-sig",
        )
        results = grep_method_usage("МояПроцедура", config_path=tmp_path / "config")
        assert len(results) == 1
        assert results[0]["line_number"] == 2
        assert "МояПроцедура" in results[0]["line_content"]
        assert results[0]["in_method"] == "Тест"

    def test_no_results_for_absent_method(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir()
        bsl = config / "Module.bsl"
        bsl.write_text("Процедура А()\nКонецПроцедуры\n", encoding="utf-8-sig")
        results = grep_method_usage("НесуществующийМетод", config_path=config)
        assert results == []

    def test_respects_limit(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir()
        bsl = config / "Module.bsl"
        lines = "\n".join(f"МойМетод();  // строка {i}" for i in range(20))
        bsl.write_text(lines, encoding="utf-8-sig")
        results = grep_method_usage("МойМетод", config_path=config, limit=5)
        assert len(results) == 5

    def test_nonexistent_config_path(self, tmp_path):
        results = grep_method_usage("Тест", config_path=tmp_path / "не_существует")
        assert results == []


class TestGrepTimeBudget:
    """Ограничение времени обхода: неполный результат должен быть помечен."""

    @staticmethod
    def _make_config(tmp_path, files=40):
        config = tmp_path / "config"
        config.mkdir()
        for i in range(files):
            d = config / "CommonModules" / f"Модуль{i}" / "Ext"
            d.mkdir(parents=True)
            # Объёмный файл без искомого имени: заставляет обход реально работать
            body = "\n".join(f"    ПрочаяПроцедура{j}();" for j in range(400))
            (d / "Module.bsl").write_text(
                f"Процедура Тест{i}()\n{body}\nКонецПроцедуры\n", encoding="utf-8-sig"
            )
        return config

    def test_not_truncated_when_budget_enough(self, tmp_path):
        config = self._make_config(tmp_path, files=5)
        results = grep_method_usage("ПрочаяПроцедура1", config_path=config, time_budget_sec=0)
        assert results.truncated is False
        assert len(results) > 0

    def test_truncated_flag_on_exhausted_budget(self, tmp_path):
        config = self._make_config(tmp_path, files=40)
        # Бюджет заведомо меньше времени обхода — обход обязан прерваться
        results = grep_method_usage(
            "ЗаведомоОтсутствующийМетод", config_path=config, time_budget_sec=0.001
        )
        assert results.truncated is True
        assert results.files_scanned < 40

    def test_zero_budget_means_unlimited(self, tmp_path):
        config = self._make_config(tmp_path, files=5)
        results = grep_method_usage(
            "ЗаведомоОтсутствующийМетод", config_path=config, time_budget_sec=0
        )
        assert results.truncated is False
        assert results.files_scanned == 5
        assert results == []

    def test_behaves_like_list(self, tmp_path):
        """Вызывающий код не должен заметить смены типа возврата."""
        config = tmp_path / "config"
        config.mkdir()
        (config / "Module.bsl").write_text(
            "Процедура Тест()\n    МояПроцедура();\nКонецПроцедуры\n", encoding="utf-8-sig"
        )
        results = grep_method_usage("МояПроцедура", config_path=config)
        assert isinstance(results, list)
        assert len(results) == 1
        assert [r["in_method"] for r in results] == ["Тест"]

    def test_case_insensitive_search(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir()
        bsl = config / "Module.bsl"
        bsl.write_text("мояфункция();\nМояФункция();\n", encoding="utf-8-sig")
        results = grep_method_usage("МояФункция", config_path=config)
        assert len(results) == 2

    def test_word_boundary_matching(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir()
        bsl = config / "Module.bsl"
        bsl.write_text(
            "МояФункцияДополнительная();\nМояФункция();\n",
            encoding="utf-8-sig",
        )
        results = grep_method_usage("МояФункция", config_path=config)
        found_lines = [r["line_content"] for r in results]
        assert any("МояФункция()" in l for l in found_lines)

    def test_enclosing_method_tracked(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir()
        bsl = config / "Module.bsl"
        bsl.write_text(
            """\
Процедура Внешняя()
    Цель();
КонецПроцедуры

Функция Другая()
    Цель();
КонецФункции
""",
            encoding="utf-8-sig",
        )
        results = grep_method_usage("Цель", config_path=config)
        assert len(results) == 2
        methods = [r["in_method"] for r in results]
        assert "Внешняя" in methods
        assert "Другая" in methods

    def test_binary_file_skipped(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir()
        bsl = config / "Binary.bsl"
        bsl.write_bytes(b"\x00\x01\x02 \xd0\xa2\xd0\xb5\xd1\x81\xd1\x82()")
        results = grep_method_usage("Тест", config_path=config)
        assert isinstance(results, list)
