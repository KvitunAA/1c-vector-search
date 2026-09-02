# Профили проектов

Каждая подпапка — профиль для отдельной конфигурации 1С.

## Шаблон: your_project

- `your_project.env` — конфигурация (CONFIG_PATH, EMBEDDING_* и т.д.)
- `your_project.env.local` — переопределения для текущей машины (не коммитить)
- `ИнструкцияПоИспользованиюMCP.md` — описание инструментов для аналитика
- `MCP_SETUP.md` — инструкция подключения к Cursor
- `MODEL_CONFIGURATION_RECOMMENDATIONS.md` — выбор моделей эмбеддингов (nomic, BGE-M3, Qwen3), настройка чанков и контекста по объёму RAM. **Qwen3 (LM Studio/GGUF):** `EMBEDDING_ADD_EOS_MANUAL=false` (по умолчанию — llama.cpp добавляет EOS автоматически)

## Скрипты индексации (корень репозитория)

| Скрипт | Назначение |
|--------|------------|
| `run_index_your_project.cmd` | Полная индексация: векторная БД + граф (код, метаданные, формы, граф связей) |
| `run_index_vector_your_project.cmd` | Только векторная БД: код, метаданные, формы (без графа) |
| `run_index_graph_your_project.cmd` | Только граф связей (без векторной БД) |

## Создание нового проекта (каждая конфигурация 1С — отдельный профиль и MCP)

```cmd
python init_project.py -n tip_zup -c "D:\1C\ZUP" -m tip_zup -d "MCP: ЗУП" --add-mcp -y
python init_project.py -n tip_erp -c "D:\1C\ERP" -m tip_erp -d "MCP: ERP" --add-mcp -y
python scripts\sync_mcp_config.py --cursor
```

В Cursor в чате выбирайте `@tip_zup`, `@tip_erp` и т.д. — один репозиторий, разные конфигурации.

Или скопируйте `your_project` и переименуйте, затем отредактируйте `.env` (`MCP_SERVER_NAME`, `CONFIG_PATH`).

---

## Текущие изменения (02.09.2026) — v0.4.1

- **Граф (tip_zup):** staging CSV, compact COPY, `--staging`, lookup `Documents.Имя`, `GRAPH_*` настройки.
- **Мульти-конфиг MCP:** `MCP_SERVER_NAME`, `sync_mcp_config.py` — ЗУП, ERP, КА из одного репозитория.
- См. [RELEASE_NOTES_v0.4.1.md](../RELEASE_NOTES_v0.4.1.md).

---

## Текущие изменения (02.03.2026)

- **Структура профилей** — каждая подпапка `projects/<имя>/` содержит `.env`, `vectordb/`, `graphdb/` и документацию.
- **MODEL_CONFIGURATION_RECOMMENDATIONS.md** — рекомендации по выбору моделей эмбеддингов (nomic, BGE-M3, Qwen3), настройке чанков и контекста в зависимости от объёма RAM (8/16/32/48 GB).
- **Раздельная индексация** — `run_index_vector_*.cmd` (только векторная БД), `run_index_graph_*.cmd` (только граф). Полная индексация — `run_index_*.cmd` или `python run_indexer.py --clear` без `--vector-only`.
- **Поддержка вложенных путей** — профили вида `esty/osn` → `projects/esty/osn/osn.env`.
- **init_project.py** — создание нового проекта с `-n`, `-c`, `--add-mcp`, `--index`.
