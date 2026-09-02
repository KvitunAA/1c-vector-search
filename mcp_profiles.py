"""
Генерация записей MCP для нескольких конфигураций 1С из каталога projects/.

Один репозиторий — несколько профилей (ЗУП, ERP, КА, БУХ и т.д.), каждый со своим
именем MCP-сервера в Cursor (как tip / tip_zup в mcp.json).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = REPO_ROOT / "projects"

_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


@dataclass
class ProfileMcp:
    """Профиль конфигурации 1С и соответствующая запись MCP."""

    profile_name: str
    mcp_server_name: str
    description: str
    env_path: Path
    vectordb_path: Path
    graphdb_path: Path
    run_server_cmd: Path

    def to_mcp_entry(self, repo_root: Optional[Path] = None) -> Dict:
        root = (repo_root or REPO_ROOT).resolve()
        cmd = self.run_server_cmd if self.run_server_cmd.exists() else root / "run_server.py"
        entry: Dict = {
            "command": "cmd",
            "args": ["/c", str(cmd)],
            "env": {
                "PROJECT_PROFILE": self.profile_name,
                "VECTORDB_PATH": str(self.vectordb_path),
                "GRAPHDB_PATH": str(self.graphdb_path),
            },
            "description": self.description,
        }
        if cmd.suffix.lower() == ".py":
            python = os.getenv("VECTOR_PYTHON_PATH") or "python"
            entry = {
                "command": python,
                "args": [str(cmd)],
                "cwd": str(root),
                "env": entry["env"],
                "description": self.description,
            }
        return entry


def _read_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if match:
            key, raw = match.group(1), match.group(2).strip()
            if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                raw = raw[1:-1]
            values[key] = raw
    return values


def _profile_env_paths(profile_dir: Path) -> List[Path]:
    name = profile_dir.name
    paths = [profile_dir / f"{name}.env"]
    paths.extend(sorted(profile_dir.glob("*.env")))
    seen: List[Path] = []
    for p in paths:
        if p.exists() and p not in seen:
            seen.append(p)
    return seen


def _merge_profile_env(profile_dir: Path) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for env_path in _profile_env_paths(profile_dir):
        merged.update(_read_env_file(env_path))
    local_name = f"{profile_dir.name}.env.local"
    local_path = profile_dir / local_name
    if local_path.exists():
        merged.update(_read_env_file(local_path))
    return merged


def _default_mcp_name(profile_name: str) -> str:
    return profile_name.replace("/", "_").replace("\\", "_")


def _default_description(profile_name: str, env: Dict[str, str]) -> str:
    if env.get("CONFIG_DESCRIPTION"):
        return env["CONFIG_DESCRIPTION"]
    config_path = env.get("CONFIG_PATH", "")
    if config_path:
        return f"MCP: семантический поиск по конфигурации 1С ({profile_name})"
    return f"MCP: профиль {profile_name} (укажите CONFIG_PATH в .env)"


def discover_profiles(projects_dir: Optional[Path] = None) -> List[ProfileMcp]:
    """Находит профили в projects/<имя>/ с файлом .env или проиндексированной БД."""
    base = projects_dir or PROJECTS_DIR
    profiles: List[ProfileMcp] = []

    if not base.exists():
        return profiles

    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        profile_name = entry.name
        env_files = _profile_env_paths(entry)
        has_env = bool(env_files)
        has_index = (entry / "vectordb" / "vectordb.sqlite").exists()
        if not (has_env or has_index):
            continue

        env = _merge_profile_env(entry) if has_env else {}
        mcp_name = env.get("MCP_SERVER_NAME") or _default_mcp_name(profile_name)
        description = _default_description(profile_name, env)

        vectordb = Path(env["VECTORDB_PATH"]) if env.get("VECTORDB_PATH") else entry / "vectordb"
        graphdb = Path(env["GRAPHDB_PATH"]) if env.get("GRAPHDB_PATH") else entry / "graphdb" / "graph.db"
        run_cmd = REPO_ROOT / f"run_server_{profile_name}.cmd"

        profiles.append(
            ProfileMcp(
                profile_name=profile_name,
                mcp_server_name=mcp_name,
                description=description,
                env_path=env_files[0] if env_files else entry / f"{profile_name}.env",
                vectordb_path=vectordb,
                graphdb_path=graphdb,
                run_server_cmd=run_cmd,
            )
        )
    return profiles


def build_mcp_config(
    repo_root: Optional[Path] = None,
    profiles: Optional[Iterable[ProfileMcp]] = None,
) -> Dict:
    """Собирает mcp_config.json для всех профилей."""
    root = (repo_root or REPO_ROOT).resolve()
    items = list(profiles) if profiles is not None else discover_profiles(root / "projects")
    servers: Dict[str, Dict] = {}
    for profile in items:
        servers[profile.mcp_server_name] = profile.to_mcp_entry(root)
    return {"mcpServers": servers}


def write_mcp_config(
    output_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
    merge_existing: bool = True,
) -> Path:
    """Записывает mcp_config.json в корень репозитория."""
    root = (repo_root or REPO_ROOT).resolve()
    target = output_path or (root / "mcp_config.json")
    new_data = build_mcp_config(root)

    if merge_existing and target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            merged = existing.get("mcpServers", {})
            merged.update(new_data.get("mcpServers", {}))
            new_data = {"mcpServers": merged}
        except (json.JSONDecodeError, OSError):
            pass

    target.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def merge_into_cursor_mcp(
    cursor_mcp_path: Path,
    repo_root: Optional[Path] = None,
    replace_prefixes: Optional[List[str]] = None,
) -> int:
    """
    Добавляет/обновляет записи профилей в ~/.cursor/mcp.json.
    Возвращает число обновлённых записей.
    """
    root = (repo_root or REPO_ROOT).resolve()
    profiles = discover_profiles(root / "projects")
    if not profiles:
        return 0

    if cursor_mcp_path.exists():
        data = json.loads(cursor_mcp_path.read_text(encoding="utf-8"))
    else:
        data = {}

    servers = data.setdefault("mcpServers", {})
    prefixes = replace_prefixes or [p.mcp_server_name for p in profiles]

    to_remove = [
        key for key in list(servers.keys())
        if any(key == p or key.startswith(f"{p}_") for p in prefixes)
    ]
    for key in to_remove:
        if key in servers:
            del servers[key]

    config = build_mcp_config(root, profiles)
    servers.update(config.get("mcpServers", {}))
    cursor_mcp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(profiles)
