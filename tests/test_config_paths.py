"""Тесты сбора путей выгрузок и меток источника."""
from pathlib import Path

from config_paths import (
    build_unified_index_plan,
    collect_extension_config_paths,
    discover_extensions_in_root,
    make_source_id,
    resolve_index_source,
    split_config_paths,
    validate_config_root,
)


def _write_configuration_xml(path: Path, *, extension: bool = False) -> None:
    tag = "ConfigurationExtension" if extension else "Configuration"
    name = "МоеРасширение" if extension else "ОсновнаяКонфигурация"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">
  <{tag} uuid="00000000-0000-0000-0000-000000000001">
    <Properties>
      <Name>{name}</Name>
    </Properties>
  </{tag}>
</MetaDataObject>
"""
    path.write_text(xml, encoding="utf-8")


def test_split_config_paths_semicolon():
    paths = split_config_paths(r"C:\ext1;C:\ext2")
    assert paths == ["C:\\ext1", "C:\\ext2"]


def test_discover_extensions_in_root(tmp_path: Path):
    root = tmp_path / "extensions"
    ext1 = root / "ExtA"
    ext2 = root / "ExtB"
    ext1.mkdir(parents=True)
    ext2.mkdir(parents=True)
    _write_configuration_xml(ext1 / "Configuration.xml", extension=True)
    _write_configuration_xml(ext2 / "Configuration.xml", extension=True)
    (root / "readme.txt").write_text("skip", encoding="utf-8")

    discovered = discover_extensions_in_root(str(root))
    assert len(discovered) == 2
    assert discovered[0].endswith("ExtA") or discovered[1].endswith("ExtA")


def test_collect_extension_paths_merges_sources(tmp_path: Path):
    main = tmp_path / "main"
    ext_single = tmp_path / "single"
    ext_list = tmp_path / "listed"
    root = tmp_path / "root"
    ext_from_root = root / "FromRoot"
    for path in (main, ext_single, ext_list, ext_from_root):
        path.mkdir(parents=True)
        _write_configuration_xml(path / "Configuration.xml", extension=path.name != "main")

    paths = collect_extension_config_paths(
        str(ext_single),
        f"{ext_list};{ext_list}",
        str(root),
        exclude_paths=[str(main)],
    )
    assert len(paths) == 3
    resolved_names = {Path(p).name for p in paths}
    assert resolved_names == {"single", "listed", "FromRoot"}


def test_validate_config_root(tmp_path: Path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    _write_configuration_xml(cfg / "Configuration.xml")
    assert validate_config_root(cfg).name == "cfg"


def test_resolve_index_source_main(tmp_path: Path):
    cfg = tmp_path / "main"
    cfg.mkdir()
    _write_configuration_xml(cfg / "Configuration.xml")
    source = resolve_index_source(cfg, "main")
    assert source["index_source"] == "main"
    assert source["source_id"] == "main"
    assert source["configuration_name"] == "ОсновнаяКонфигурация"


def test_resolve_index_source_extension(tmp_path: Path):
    ext = tmp_path / "MyExtFolder"
    ext.mkdir()
    _write_configuration_xml(ext / "Configuration.xml", extension=True)
    source = resolve_index_source(ext, "extension")
    assert source["index_source"] == "extension"
    assert source["source_id"].startswith("ext_")
    assert source["is_extension"] is True


def test_build_unified_index_plan(tmp_path: Path):
    main = tmp_path / "main"
    ext = tmp_path / "ext"
    plan = build_unified_index_plan(main, [ext])
    assert plan[0]["clear"] is True
    assert plan[1]["clear"] is False
