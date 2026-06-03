# Release v0.4.0 (03.06.2026)

## Кратко

Версия **0.4.0** — крупное обновление хранилищ и индексации расширений:

- **Векторная БД:** ChromaDB → **sqlite-vec** (SQLite + KNN по эмбеддингам).
- **Граф зависимостей:** SQLite-таблицы → **Kuzu** (Cypher, встраиваемая графовая СУБД).
- **Расширения конфигурации:** отдельные каталоги БД, флаг `--extension`, сводный `run_index_all.py`.
- **Материалы презентации:** `assets/pptx-plan/` (SVG-схемы и генератор).

> **Важно:** формат данных несовместим с v0.3.x. После обновления нужна **полная переиндексация** (`--clear`).

---

## Новое и изменённое

### Хранилища (breaking change)

| Компонент | Было (≤ v0.3.x) | Стало (v0.4.0) |
|-----------|-----------------|----------------|
| Семантический поиск | ChromaDB | **sqlite-vec** (`vectordb.sqlite` в `VECTORDB_PATH`) |
| Граф зависимостей | SQLite (таблицы Node/Edge) | **Kuzu** (каталог `GRAPHDB_PATH`) |
| Зависимости Python | `chromadb` | `sqlite-vec>=0.1.6`, `kuzu>=0.6.0` |

- `vectordb_manager.py` — переписан под sqlite-vec; гибридный поиск (BM25) и MMR сохранены.
- `graph_db.py` — API `GraphDBManager` сохранён; внутри — Kuzu, запросы на Cypher.
- `config.py` — уточнены подписи в логах (`sqlite-vec`, `Kuzu`); комментарий, что `GRAPHDB_PATH` — каталог БД Kuzu.

### Индексация расширений (v0.4.0, коммит a5d4842)

- Переменные профиля: `EXTENSION_CONFIG_PATH`, `EXTENSION_VECTORDB_PATH`, `EXTENSION_GRAPHDB_PATH`.
- `index_config.py`, `index_graph_mp.py` — флаг **`--extension`**.
- `config_dump.py` — определение типа выгрузки по корневому `Configuration.xml`.
- Скрипты: `run_index_extension_*`, **`run_index_all.py`** / `run_index_all_<профиль>.cmd`.
- `init_project.py` — шаблоны скриптов и блок `EXTENSION_*` в `.env`.
- `parser_1c.py` — `casefold()` при поиске коллекции в `METADATA_COLLECTION_MAP`.

### Индексация и конфиг (с v0.3.1)

- Настраиваемые батчи: `BATCH_SIZE_CODE`, `BATCH_SIZE_METADATA`, `BATCH_SIZE_FORMS`.
- `CHARS_PER_TOKEN` (по умолчанию 1.65), исправление `CHUNK_MAX_CHARS=0`.
- Чанки кода: комментарии только в **первом** фрагменте метода (`chunk_index > 0` без дублирования шапки).
- Устойчивость BSL-парсера при нестандартных конструкциях (Issue #1).

### Документация и презентация

- `assets/pptx-plan/build_pptx_plan.py` — генерация SVG и черновика Markdown-плана слайдов.
- SVG: обзор системы, вектор+граф, расширения, сценарий MCP-поиска, анализ влияния, roadmap.

### Тесты

- `tests/test_graph_db.py` — адаптация под Kuzu.
- `tests/test_vectordb_manager.py` — адаптация под sqlite-vec.

---

## Миграция с v0.3.x

1. Обновить код: `git pull` (тег `v0.4.0`).
2. Переустановить зависимости:
   ```cmd
   pip install -r requirements.txt
   ```
3. **Удалить или переименовать** старые каталоги индекса (Chroma / старый SQLite-граф), например:
   - `projects/<профиль>/vectordb/`
   - `projects/<профиль>/graphdb/`
   - при использовании расширения — `extension_vectordb/`, `extension_graphdb/`
4. Выполнить полную индексацию:
   ```cmd
   run_index_all_your_project.cmd
   ```
   или:
   ```cmd
   set PROJECT_PROFILE=your_project
   python run_index_all.py
   ```
   с очисткой через `--clear` в соответствующих скриптах / `run_indexer.py --clear`.

5. Проверить MCP в Cursor: перезапустить MCP-сервер после переиндексации.

---

## Web-приложение «1С Vector MCP Chat» (v1.0.0 компонента)

Новый компонент в том же теге **v0.4.0** (подробности: [`RELEASE_v1.0.0.md`](RELEASE_v1.0.0.md)):

- **`app/`** — FastAPI: чат с RAG через stdio MCP + LM Studio, панель источников, SVG-граф.
- **`run_app.cmd`**, **`requirements-app.txt`**, **`app/mcp_registry.example.json`**.
- MCP-сервер для Cursor (`server.py`) **не меняется** — приложение отдельный клиент.

```cmd
pip install -r requirements-app.txt
run_app.cmd
```

Браузер: **http://127.0.0.1:8765**

---

## Коммиты релиза

Диапазон: после документации v0.3.0 (`e9931b4`) → `786f0e0` (HEAD на момент релиза).

Ключевые коммиты:

- `182aad3` — настраиваемые батчи индексации
- `0dd34ed` — устойчивость BSL-парсера
- `2916c7b`, `e2888cd` — CHARS_PER_TOKEN, чанки без дублирования комментариев
- `a5d4842` — индексация расширений, `run_index_all`
- `786f0e0` — sqlite-vec + Kuzu

---

## Публикация на GitHub

```cmd
git tag -a v0.4.0 -m "v0.4.0: sqlite-vec + Kuzu, расширения, pptx-plan"
git push origin v0.4.0
```

На странице репозитория: **Releases → Draft a new release** → выбрать тег `v0.4.0`, вставить текст из этого файла (разделы «Кратко», «Новое», «Миграция»).
