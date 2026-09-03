"""Тесты run_index_all: аргументы графа из профиля."""
import pytest


class TestGraphIndexArgs:
    def test_staging_and_workers_from_env(self, monkeypatch):
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        monkeypatch.setenv("GRAPH_USE_STAGING", "1")
        monkeypatch.setenv("INDEX_GRAPH_WORKERS", "1")

        import importlib
        import run_index_all

        importlib.reload(run_index_all)

        args = run_index_all._graph_index_args()
        assert "--staging" in args
        assert "--workers" in args
        assert args[args.index("--workers") + 1] == "1"
        assert "--clear" in args

    def test_no_staging_when_disabled(self, monkeypatch):
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        monkeypatch.setenv("GRAPH_USE_STAGING", "0")
        monkeypatch.delenv("INDEX_GRAPH_WORKERS", raising=False)

        import importlib
        import run_index_all

        importlib.reload(run_index_all)

        args = run_index_all._graph_index_args()
        assert "--staging" not in args

    def test_main_passes_staging_to_subprocess(self, monkeypatch):
        monkeypatch.setenv("PROJECT_PROFILE", "default")
        monkeypatch.setenv("GRAPH_USE_STAGING", "1")
        monkeypatch.setenv("INDEX_GRAPH_WORKERS", "2")

        captured = []

        def fake_run(cmd, cwd=None, check=None):
            captured.append(list(cmd))

        import importlib
        import run_index_all

        importlib.reload(run_index_all)
        monkeypatch.setattr(run_index_all.subprocess, "run", fake_run)

        run_index_all.main()

        graph_calls = [c for c in captured if "index_graph_mp.py" in str(c)]
        assert len(graph_calls) == 1
        graph_cmd = graph_calls[0]
        assert "--staging" in graph_cmd
        assert "--workers" in graph_cmd
        assert "2" in graph_cmd
