"""Тесты разбора корневого Configuration.xml (основная конфигурация / расширение)."""
from pathlib import Path

from config_dump import read_configuration_dump_info


def test_no_configuration_xml(tmp_path: Path):
    info = read_configuration_dump_info(tmp_path)
    assert info["valid"] is False
    assert info["is_extension"] is False


def test_minimal_main_configuration(tmp_path: Path):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">
  <Configuration uuid="00000000-0000-0000-0000-000000000001">
    <Properties>
      <Name>ТестоваяКонфигурация</Name>
    </Properties>
  </Configuration>
</MetaDataObject>
"""
    (tmp_path / "Configuration.xml").write_text(xml, encoding="utf-8")
    info = read_configuration_dump_info(tmp_path)
    assert info["valid"] is True
    assert info["is_extension"] is False
    assert info["configuration_name"] == "ТестоваяКонфигурация"


def test_extension_configuration(tmp_path: Path):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">
  <ConfigurationExtension uuid="00000000-0000-0000-0000-000000000002">
    <Properties>
      <Name>МоеРасширение</Name>
    </Properties>
  </ConfigurationExtension>
</MetaDataObject>
"""
    (tmp_path / "Configuration.xml").write_text(xml, encoding="utf-8")
    info = read_configuration_dump_info(tmp_path)
    assert info["valid"] is True
    assert info["is_extension"] is True
    assert info["configuration_name"] == "МоеРасширение"
