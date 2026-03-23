"""Тесты для config: Config class, load_profile, validate."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestConfigLogLevel:
    """LOG_LEVEL нормализуется в верхний регистр."""

    def test_default_is_upper(self):
        from config import Config
        assert Config.LOG_LEVEL == Config.LOG_LEVEL.upper()

    def test_lowercase_input_normalized(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "debug")
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.Config.LOG_LEVEL == "DEBUG"

    def test_mixed_case_normalized(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "Warning")
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.Config.LOG_LEVEL == "WARNING"

    def test_empty_falls_to_default(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "")
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.Config.LOG_LEVEL == "INFO"


class TestConfigValidate:
    """Валидация конфигурации."""

    def test_empty_config_path_fails(self, monkeypatch):
        monkeypatch.setenv("CONFIG_PATH", "")
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.Config.validate() is False

    def test_nonexistent_config_path_fails(self, monkeypatch, tmp_path):
        fake = str(tmp_path / "nonexistent_config")
        monkeypatch.setenv("CONFIG_PATH", fake)
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.Config.validate() is False

    def test_existing_config_path_passes(self, monkeypatch, tmp_path):
        config_dir = tmp_path / "real_config"
        config_dir.mkdir()
        monkeypatch.setenv("CONFIG_PATH", str(config_dir))
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.Config.validate() is True


class TestLoadProfile:
    """Загрузка профиля конфигурации."""

    def test_default_profile(self, monkeypatch):
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.current_profile == "default"

    def test_custom_profile(self, monkeypatch, tmp_path):
        profile_name = "test_profile"
        profile_dir = tmp_path / "projects" / profile_name
        profile_dir.mkdir(parents=True)
        env_file = profile_dir / f"{profile_name}.env"
        env_file.write_text("CONFIG_PATH=/tmp/test\n", encoding="utf-8")

        monkeypatch.setenv("PROJECT_PROFILE", profile_name)
        import importlib
        import config
        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        importlib.reload(config)


class TestConfigCollections:
    """Коллекции определены корректно."""

    def test_collections_keys(self):
        from config import Config
        assert "code" in Config.COLLECTIONS
        assert "metadata" in Config.COLLECTIONS
        assert "forms" in Config.COLLECTIONS

    def test_metadata_types_mapping(self):
        from config import Config
        assert Config.METADATA_TYPES["Справочник"] == "Catalogs"
        assert Config.METADATA_TYPES["Документ"] == "Documents"


class TestConfigChunking:
    """Настройки чанкинга."""

    def test_chunk_max_chars_default(self, monkeypatch):
        monkeypatch.setenv("CHUNK_MAX_TOKENS", "")
        monkeypatch.setenv("CHUNK_MAX_CHARS", "0")
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.Config.CHUNK_MAX_CHARS == int(512 * config.Config.CHARS_PER_TOKEN)

    def test_chunk_max_tokens_overrides(self, monkeypatch):
        monkeypatch.setenv("CHUNK_MAX_TOKENS", "512")
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.Config.CHUNK_MAX_TOKENS == 512
        assert config.Config.CHUNK_MAX_CHARS == int(512 * config.Config.CHARS_PER_TOKEN)

    def test_chars_per_token_env(self, monkeypatch):
        monkeypatch.setenv("CHARS_PER_TOKEN", "1.5")
        monkeypatch.setenv("CHUNK_MAX_TOKENS", "512")
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        import importlib
        import config
        importlib.reload(config)
        assert config.Config.CHARS_PER_TOKEN == 1.5
        assert config.Config.CHUNK_MAX_CHARS == int(512 * 1.5)
