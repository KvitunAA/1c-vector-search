# Release v0.4.1 (02.09.2026)

## Кратко

Версия **0.4.1** — графовый контур из tip_zup, устойчивость индексации и **несколько конфигураций 1С из одного репозитория** (ЗУП, ERP, КА, БУХ и др.).

> **Совместимость:** формат `vectordb.sqlite` и Kuzu-графа не менялся. Переиндексация нужна только если вы включаете `--staging` или хотите обновить граф после обновления кода индексатора.

---

## Новое

### Графовый контур (порт из tip_zup)

| Компонент | Описание |
|-----------|----------|
| `graph_staging.py` | Staging: dedupe в памяти → CSV |
| `compact_graph.py` | COPY CSV → компактная `graph.db` без MERGE-bloat |
| `object_identifier.py` | Разбор `Documents.Имя`, `metadata:Documents:Имя`, короткого имени |
| `graph_db.py` | `_node_match_clause` в `graph_dependencies` / `graph_references`; large-graph batching; `KUZU_BUFFER_POOL_SIZE`. Сохранены: `read_only`, lock-retry, `_CSV_UNSAFE_CHARS` |
| `index_graph_mp.py` | Флаг **`--staging`**; порционная запись модулей (`GRAPH_MODULE_CHUNK_SIZE`) |
| `config.py` | `GRAPH_STAGING_PATH`, `GRAPH_CSV_BATCH_SIZE`, `GRAPH_LARGE_GRAPH_THRESHOLD_MB`, `GRAPH_FLUSH_CLOSE_EVERY`, `GRAPH_STREAM_BATCH_SIZE`, `GRAPH_RELEASE_EVERY_N_BATCHES` |

**Компактная сборка графа (опционально):**

```cmd
set STAGING=1
run_index_graph_your_project.cmd
```

или `python index_graph_mp.py --staging --clear`.

### MCP для нескольких конфигураций

Один репозиторий — несколько профилей в `projects/<имя>/`, каждый со своим именем в Cursor (`@tip_zup`, `@tip_erp`, …):

| Компонент | Описание |
|-----------|----------|
| `mcp_profiles.py` | Автообнаружение профилей, сборка `mcp_config.json` |
| `scripts/sync_mcp_config.py` | `--list`, `--cursor` (обновление `~/.cursor/mcp.json`) |
| `MCP_SERVER_NAME` | Имя MCP-сервера в профиле `.env` |
| `CONFIG_DESCRIPTION` | Описание в mcp.json |
| `server.py` | Имя сервера берётся из `MCP_SERVER_NAME` |

**Быстрый старт:**

```cmd
python init_project.py -n tip_zup -c "D:\1C\ZUP" -m tip_zup -d "MCP: ЗУП" --add-mcp -y
python init_project.py -n tip_erp -c "D:\1C\ERP" -m tip_erp -d "MCP: ERP" --add-mcp -y
python scripts\sync_mcp_config.py --cursor
```

### Ускорение и устойчивость графа (02.09.2026)

- Индекс ролей и макетов в графе; оптимизация `_build_module_graph`.
- Индексация не падает при **lock MCP** (read-only + retry) и запятых в синонимах ролей (`_CSV_UNSAFE_CHARS`).

### Тесты

- `tests/test_object_identifier.py`
- `tests/test_graph_staging.py`
- `tests/test_graph_db.py` — lookup по qualified name (`Documents.Заказ`)
- `tests/test_mcp_profiles.py`

Запуск:

```cmd
run_tests.cmd
```

или:

```cmd
.tools\python312\python.exe -m pytest tests\test_object_identifier.py tests\test_graph_staging.py tests\test_graph_db.py tests\test_mcp_profiles.py -q
```

---

## Обновление с v0.4.0

1. `git pull`
2. При необходимости переиндексировать граф: `run_index_graph_<профиль>.cmd`
3. Для нескольких конфигураций — создать профили через `init_project.py` и `sync_mcp_config.py --cursor`
4. Перезапустить MCP в Cursor после смены `mcp.json`

---

## GitHub Release

```cmd
git tag -a v0.4.1 -m "v0.4.1: граф tip_zup, staging, мульти-конфиг MCP"
git push origin v0.4.1
```

На странице репозитория: **Releases → Draft a new release** → тег `v0.4.1`, текст из этого файла.
