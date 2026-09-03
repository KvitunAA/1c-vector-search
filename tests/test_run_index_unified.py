"""Тесты плана unified-индексации."""
from pathlib import Path

from config_paths import build_unified_index_plan


def test_build_unified_index_plan_main_and_extensions(tmp_path: Path):
    main = tmp_path / "main"
    ext1 = tmp_path / "ext1"
    ext2 = tmp_path / "ext2"
    plan = build_unified_index_plan(main, [ext1, ext2])

    assert len(plan) == 3
    assert plan[0] == {"kind": "main", "path": main, "clear": True}
    assert plan[1]["kind"] == "extension"
    assert plan[1]["clear"] is False
    assert plan[2]["path"] == ext2


def test_build_unified_index_plan_main_only(tmp_path: Path):
    main = tmp_path / "main"
    plan = build_unified_index_plan(main, [])
    assert len(plan) == 1
    assert plan[0]["kind"] == "main"
