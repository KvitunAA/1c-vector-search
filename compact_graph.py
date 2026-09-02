"""
Сборка компактной Kuzu БД из staging CSV одним COPY (без MERGE-bloat).
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import kuzu

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from graph_staging import GraphStagingWriter

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _posix(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _create_schema(conn: kuzu.Connection) -> None:
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS Node(
            id STRING,
            node_type STRING,
            name STRING,
            object_type STRING,
            object_name STRING,
            synonym STRING,
            extra STRING,
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS REL(FROM Node TO Node, edge_type STRING, extra STRING)"
    )


def compact_staging_to_kuzu(
    staging_dir: Path,
    graph_db_path: Path,
    *,
    buffer_pool_size: int = 0,
    backup_existing: bool = True,
) -> dict:
    """
    COPY nodes.csv + edges.csv → новая Kuzu БД.
    Возвращает stats после загрузки.
    """
    nodes_csv = staging_dir / "nodes.csv"
    edges_csv = staging_dir / "edges.csv"
    if not nodes_csv.is_file() or not edges_csv.is_file():
        writer = GraphStagingWriter(str(staging_dir))
        if not writer._nodes and not writer._edges:
            raise FileNotFoundError(
                f"Staging пуст: {staging_dir}. Сначала index_graph_mp.py --staging --clear"
            )
        writer.write_csv_files()

    graph_db_path.parent.mkdir(parents=True, exist_ok=True)
    if graph_db_path.exists() and backup_existing:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = graph_db_path.with_name(f"{graph_db_path.name}.bak_{stamp}")
        shutil.move(str(graph_db_path), str(backup_path))
        logger.info("Старый graph.db перемещён в %s", backup_path)
        for suffix in (".wal", ".shadow"):
            sidecar = graph_db_path.with_name(graph_db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()

    if graph_db_path.exists():
        shutil.rmtree(graph_db_path) if graph_db_path.is_dir() else graph_db_path.unlink()

    started = time.time()
    db = kuzu.Database(str(graph_db_path), buffer_pool_size=max(0, buffer_pool_size))
    conn = kuzu.Connection(db)
    _create_schema(conn)

    nodes_posix = _posix(nodes_csv)
    edges_posix = _posix(edges_csv)

    logger.info("COPY Node FROM %s", nodes_csv)
    conn.execute(f"COPY Node FROM '{nodes_posix}' (HEADER=true)")

    logger.info("COPY REL FROM %s", edges_csv)
    conn.execute(f"COPY REL FROM '{edges_posix}' (HEADER=true)")

    try:
        conn.execute("CHECKPOINT")
        logger.info("CHECKPOINT выполнен")
    except RuntimeError as exc:
        logger.warning("CHECKPOINT пропущен: %s", exc)

    node_count = conn.execute("MATCH (n:Node) RETURN count(n)").get_next()[0]
    edge_count = conn.execute("MATCH ()-[r:REL]->() RETURN count(r)").get_next()[0]
    db.close()

    elapsed = time.time() - started
    size_mb = graph_db_path.stat().st_size / (1024 * 1024) if graph_db_path.exists() else 0
    result = {
        "nodes_count": node_count,
        "edges_count": edge_count,
        "graph_db_path": str(graph_db_path),
        "size_mb": round(size_mb, 1),
        "elapsed_sec": round(elapsed, 1),
    }
    logger.info(
        "Compact завершён: %s узлов, %s рёбер, %.1f MB, %.1f сек",
        node_count,
        edge_count,
        size_mb,
        elapsed,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="COPY staging CSV → компактная Kuzu graph.db")
    parser.add_argument("--staging-path", default=None, help="Каталог graph_staging")
    parser.add_argument("--graph-path", default=None, help="Путь к graph.db")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Не сохранять backup старого graph.db",
    )
    args = parser.parse_args()

    staging_dir = Path(args.staging_path or Config.GRAPH_STAGING_PATH)
    graph_db_path = Path(args.graph_path or Config.GRAPHDB_PATH)

    try:
        compact_staging_to_kuzu(
            staging_dir,
            graph_db_path,
            buffer_pool_size=getattr(Config, "KUZU_BUFFER_POOL_SIZE", 0),
            backup_existing=not args.no_backup,
        )
    except Exception as exc:
        logger.error("Compact failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
