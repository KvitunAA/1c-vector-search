"""
Реестр доступных vector MCP-серверов.

Источники (в порядке приоритета):
  1. Явный файл ``app/mcp_registry.json`` (если есть) — позволяет описать любые
     MCP-серверы, в том числе из соседних репозиториев (KA, ERP, ZUP, BUH, BSL).
  2. Автообнаружение профилей в ``projects/<имя>/`` текущего репозитория: профиль
     считается доступным, если рядом есть каталог ``vectordb`` с файлом
     ``vectordb.sqlite`` либо непустой ``<имя>.env``.

Каждая запись описывает, как запустить MCP-сервер через stdio (как это делает
Cursor): команда, аргументы, рабочий каталог и переменные окружения профиля.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_FILE = Path(__file__).resolve().parent / "mcp_registry.json"
PROJECTS_DIR = REPO_ROOT / "projects"

# Профили-шаблоны, которые не показываем как «готовые к работе» серверы,
# но оставляем доступными, если у них реально есть проиндексированная база.
_TEMPLATE_PROFILES = {"your_project"}


@dataclass
class McpServer:
    """Описание одного MCP-сервера для запуска через stdio."""

    name: str
    description: str = ""
    command: str = field(default_factory=lambda: sys.executable or "python")
    args: List[str] = field(default_factory=lambda: ["run_server.py"])
    cwd: str = str(REPO_ROOT)
    env: Dict[str, str] = field(default_factory=dict)
    source: str = "registry"

    def vectordb_path(self) -> str:
        return self.env.get("VECTORDB_PATH", "")

    def graphdb_path(self) -> str:
        return self.env.get("GRAPHDB_PATH", "")

    def is_available(self) -> bool:
        """Доступность определяется наличием проиндексированной векторной БД."""
        vdb = self.vectordb_path()
        if not vdb:
            return False
        sqlite_file = Path(vdb) / "vectordb.sqlite"
        return sqlite_file.exists()

    def to_public_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "cwd": self.cwd,
            "vectordb_path": self.vectordb_path(),
            "graphdb_path": self.graphdb_path(),
            "available": self.is_available(),
            "source": self.source,
        }


def _build_profile_server(profile_dir: Path) -> Optional[McpServer]:
    """Строит запись MCP-сервера по каталогу профиля текущего репозитория."""
    profile_name = profile_dir.name
    env_file = profile_dir / f"{profile_name}.env"
    vectordb_dir = profile_dir / "vectordb"
    has_index = (vectordb_dir / "vectordb.sqlite").exists()
    has_env = env_file.exists()

    if not (has_index or has_env):
        return None

    env = {
        "PROJECT_PROFILE": profile_name,
        "VECTORDB_PATH": str(vectordb_dir),
        "GRAPHDB_PATH": str(profile_dir / "graphdb" / "graph.db"),
    }

    description = f"Профиль '{profile_name}' (текущий репозиторий)"
    if profile_name in _TEMPLATE_PROFILES and not has_index:
        description += " — шаблон, база ещё не проиндексирована"

    return McpServer(
        name=profile_name,
        description=description,
        command=sys.executable or "python",
        args=["run_server.py"],
        cwd=str(REPO_ROOT),
        env=env,
        source="profile",
    )


def _discover_profile_servers() -> List[McpServer]:
    servers: List[McpServer] = []
    if not PROJECTS_DIR.exists():
        return servers

    for entry in sorted(PROJECTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        server = _build_profile_server(entry)
        if server is not None:
            servers.append(server)
    return servers


def _load_registry_file() -> List[McpServer]:
    if not REGISTRY_FILE.exists():
        return []

    data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    raw_servers = data.get("servers", []) if isinstance(data, dict) else data
    servers: List[McpServer] = []

    for raw in raw_servers:
        name = (raw.get("name") or "").strip()
        if not name:
            continue
        servers.append(
            McpServer(
                name=name,
                description=raw.get("description", ""),
                command=raw.get("command") or (sys.executable or "python"),
                args=list(raw.get("args") or ["run_server.py"]),
                cwd=raw.get("cwd") or str(REPO_ROOT),
                env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
                source="registry",
            )
        )
    return servers


def load_servers() -> List[McpServer]:
    """Возвращает объединённый список серверов (файл реестра + профили)."""
    by_name: Dict[str, McpServer] = {}

    for server in _discover_profile_servers():
        by_name[server.name] = server

    # Записи из файла реестра имеют приоритет и могут переопределить профили.
    for server in _load_registry_file():
        by_name[server.name] = server

    return sorted(by_name.values(), key=lambda s: s.name.lower())


def get_server(name: str) -> Optional[McpServer]:
    for server in load_servers():
        if server.name == name:
            return server
    return None


def build_subprocess_env(server: McpServer) -> Dict[str, str]:
    """Окружение для дочернего процесса: системное + переменные профиля."""
    merged = dict(os.environ)
    merged.update(server.env)
    return merged
