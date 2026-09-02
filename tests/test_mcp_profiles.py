"""Тесты генерации MCP-конфигурации для нескольких профилей."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_profiles import ProfileMcp, build_mcp_config, discover_profiles


@pytest.fixture
def projects_tree(tmp_path: Path) -> Path:
    for name, mcp_name, desc in [
        ("zup", "tip_zup", "MCP: ЗУП"),
        ("erp", "tip_erp", "MCP: ERP"),
    ]:
        prof = tmp_path / name
        prof.mkdir()
        (prof / f"{name}.env").write_text(
            f"CONFIG_PATH=C:\\1C\\{name}\n"
            f"MCP_SERVER_NAME={mcp_name}\n"
            f"CONFIG_DESCRIPTION={desc}\n",
            encoding="utf-8",
        )
    return tmp_path


def test_discover_profiles_reads_mcp_names(projects_tree: Path):
    profiles = discover_profiles(projects_tree)
    names = {p.profile_name: p.mcp_server_name for p in profiles}
    assert names == {"zup": "tip_zup", "erp": "tip_erp"}


def test_build_mcp_config_multiple_servers(projects_tree: Path, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    profiles = discover_profiles(projects_tree)
    config = build_mcp_config(repo, profiles)

    assert "tip_zup" in config["mcpServers"]
    assert "tip_erp" in config["mcpServers"]
    assert config["mcpServers"]["tip_zup"]["env"]["PROJECT_PROFILE"] == "zup"
    assert config["mcpServers"]["tip_zup"]["description"] == "MCP: ЗУП"


def test_profile_mcp_entry_uses_run_server_cmd(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    cmd = repo / "run_server_my.cmd"
    cmd.write_text("@echo off\n", encoding="utf-8")

    profile = ProfileMcp(
        profile_name="my",
        mcp_server_name="my_mcp",
        description="test",
        env_path=tmp_path / "my.env",
        vectordb_path=tmp_path / "vectordb",
        graphdb_path=tmp_path / "graph.db",
        run_server_cmd=cmd,
    )
    entry = profile.to_mcp_entry(repo)
    assert entry["args"] == ["/c", str(cmd)]
