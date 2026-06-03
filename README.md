# 1c-vector-search MCP Server

MCP-сервер для семантического поиска по коду и метаданным конфигураций 1С (ЗУП, УТ, ERP и т.п.). Работает локально через **sqlite-vec** (векторный поиск) и **Kuzu** (граф зависимостей), без Docker.

### Что нового (03–04.06.2026) — [v0.4.0](RELEASE_NOTES_v0.4.0.md)

- **Хранилища (breaking):** ChromaDB → **sqlite-vec**, граф SQLite → **Kuzu**. Нужна полная переиндексация (`--clear`). См. [миграцию](RELEASE_NOTES_v0.4.0.md#миграция-с-v03x).
- **Расширения, pptx-plan, батчи, парсер** — см. полный список в [`RELEASE_NOTES_v0.4.0.md`](RELEASE_NOTES_v0.4.0.md).

### Что нового (04.06.2026) — Web-приложение MCP Chat

- **`app/`** — локальная web-витрина: выбор vector MCP-профиля, вопрос по конфигурации, ответ **чат-модели LM Studio**, панель источников (код / метаданные / формы) и SVG-граф зависимостей.
- **`run_app.cmd`** — запуск на `http://127.0.0.1:8765`; зависимости: `pip install -r requirements-app.txt`.
- **`app/mcp_registry.example.json`** — пример реестра серверов (KA, ERP, ZUP и др.) с путями к `VECTORDB_PATH` / `GRAPHDB_PATH`.
- MCP-сервер для Cursor **не меняется**; приложение — отдельный stdio-клиент. Подробности: [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md).

### Что нового (25.03.2026)

- **Расширения конфигурации:** отдельные БД без затрагивания основной выгрузки — переменные `EXTENSION_CONFIG_PATH`, `EXTENSION_VECTORDB_PATH`, `EXTENSION_GRAPHDB_PATH` в профиле; флаг **`--extension`** в `index_config.py` и `index_graph_mp.py`; скрипты `run_index_extension_vector_*.cmd`, `run_index_extension_graph_*.cmd`, `run_index_extension_full_*.cmd`; модуль **`config_dump.py`** (тип выгрузки по корневому `Configuration.xml`); в `Config.show()` выводятся пути расширения.
- **Сводная индексация:** **`run_index_all.py`** и **`run_index_all_your_project.cmd`** — последовательно векторная БД и граф основной конфигурации, затем (если задан `EXTENSION_CONFIG_PATH`) вектор и граф расширения; опционально **`INDEX_GRAPH_WORKERS`** для шагов с графом.
- **`init_project.py`:** при создании проекта добавляются перечисленные скрипты расширения и `run_index_all_<имя>.cmd`, в шаблон `.env` — блок `EXTENSION_*`.
- **Тесты и парсер:** в **`extract_metadata_references_from_code`** поиск коллекции по ключу с **`casefold()`** (регистр вроде `справочники` / `Справочники`).

## Состав проекта

- **Python-модули** — полная реализация MCP-сервера, индексатора и графа зависимостей:
  - `server.py` — MCP-сервер (stdio-транспорт для Cursor)
  - `config.py` — загрузка конфигурации из профилей
  - `vectordb_manager.py` — векторная БД (sqlite-vec)
  - `graph_db.py` — граф зависимостей (Kuzu, Cypher)
  - `parser_1c.py` — парсер BSL и XML метаданных 1С
  - `code_grep.py` — grep по исходникам для find_1c_method_usage
  - `index_config.py` — индексация кода, метаданных, форм и графа
  - `config_dump.py` — разбор корневого `Configuration.xml` (основная конфигурация или расширение)
  - `index_graph_mp.py` — индексация только графа (с многопроцессорностью, `--workers 1` для однопроцессорного режима)
  - `index_graph.py` — **[deprecated]** однопроцессорная версия, используйте `index_graph_mp.py`
  - `run_server.py`, `run_indexer.py`, `run_index_all.py` — точки входа
- **Профили** — `projects/your_project/` — шаблон с обезличенными параметрами
- **Скрипты** — `run_server_your_project.cmd`, `run_index_your_project.cmd` (только векторная БД), `run_index_graph_your_project.cmd` (только граф)
- **Всё сразу** — `run_index_all_your_project.cmd` / `python run_index_all.py`: основная конфигурация (вектор + граф), затем расширение (вектор + граф), если задан `EXTENSION_CONFIG_PATH`
- **Расширение** — `run_index_extension_vector_your_project.cmd`, `run_index_extension_graph_your_project.cmd`, `run_index_extension_full_your_project.cmd` (отдельные БД без затрагивания основной конфигурации)
- **Схемы MCP** — `SERVER_METADATA.json`, `tools/*.json` — описание инструментов для клиентов
- **Web-приложение** — `app/` — чат по конфигурации через браузер (см. [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md))

## Быстрый старт

### 1. Установка зависимостей

```cmd
cd 1c-vector-search
pip install -r requirements.txt
pip install -r requirements-app.txt
```

### 2. Настройка профиля

1. Переименуйте `projects/your_project` в `projects/<имя_проекта>` (например, `Vector`).
2. Переименуйте `your_project.env` в `<имя>.env`.
3. Отредактируйте `.env`:
   - **CONFIG_PATH** — путь к выгрузке конфигурации 1С (корень, где лежит `Configuration.xml`)
   - **EMBEDDING_API_BASE** — URL API эмбеддингов (LM Studio, LocalAI и т.д.), или оставьте пустым для локальной модели
   - **EMBEDDING_MODEL** — имя модели эмбеддингов
   - **EMBEDDING_DIMENSION** — определяется автоматически по модели (см. `KNOWN_MODELS` в `config.py`). Задавайте явно, только если модель не распознана

### 3. Переименование скриптов (опционально)

Переименуйте `run_server_your_project.cmd` → `run_server_<имя>.cmd` и аналогично `run_index_*.cmd`, `run_index_graph_*.cmd`. Либо используйте `init_project.py` для создания нового проекта.

### 4. Индексация

**Векторная БД** (код, метаданные, формы) — семантический поиск:

```cmd
run_index_your_project.cmd
```

**Граф зависимостей** — анализ связей между объектами (отдельно):

```cmd
run_index_graph_your_project.cmd
```

Или через Python:

```cmd
set PROJECT_PROFILE=your_project
python run_indexer.py --clear --vector-only
```

### 5. Подключение в Cursor

`Ctrl+Shift+P` → **"MCP: Edit Config File"**

Добавьте в `mcpServers` (замените `C:\project` на путь к папке проекта):

```json
"1c-vector-search": {
  "command": "cmd",
  "args": ["/c", "C:\\project\\run_server_your_project.cmd"],
  "env": {
    "PROJECT_PROFILE": "your_project",
    "VECTORDB_PATH": "C:\\project\\projects\\your_project\\vectordb",
    "GRAPHDB_PATH": "C:\\project\\projects\\your_project\\graphdb\\graph.db"
  },
  "description": "MCP сервер для семантического поиска по конфигурации 1С"
}
```

### 6. Запуск MCP

Cursor запускает MCP-сервер автоматически при обращении к инструментам. Для проверки можно запустить вручную:

```cmd
run_server_your_project.cmd
```

### 7. Web-приложение (MCP Chat)

После индексации профиля можно задавать вопросы в браузере (ответ — модель чата из LM Studio):

```cmd
run_app.cmd
```

Откройте **http://127.0.0.1:8765**. В боковой панели выберите MCP-сервер, укажите URL LM Studio (`http://localhost:1234/v1` по умолчанию) и модель чата. Для нескольких конфигураций скопируйте `app/mcp_registry.example.json` → `app/mcp_registry.json` и пропишите пути к базам.

## Обезличенные параметры

В шаблоне используются плейсхолдеры:

- **CONFIG_PATH** — `C:\path\to\your\1c\config`
- **EMBEDDING_API_BASE** — `http://your-host:port/v1`
- **EMBEDDING_MODEL** — `your-embedding-model-name`
- **VECTOR_PYTHON_PATH** (в `local.env`) — `C:\path\to\python.exe`

Файлы `*.env.local` и `local.env` не коммитятся в Git.

### Расширения конфигурации и отдельные БД (не трогая основную конфигурацию)

В одном профиле можно держать **основную** выгрузку (`CONFIG_PATH` → `VECTORDB_PATH`, `GRAPHDB_PATH`) и **отдельно** выгрузку расширения в другие каталоги/файлы:

| Переменная | Назначение | Значение по умолчанию в профиле |
|------------|------------|--------------------------------|
| `EXTENSION_CONFIG_PATH` | Корень выгрузки расширения (где лежит `Configuration.xml`) | не задано |
| `EXTENSION_VECTORDB_PATH` | Каталог векторной БД только для расширения | `projects/<профиль>/extension_vectordb` |
| `EXTENSION_GRAPHDB_PATH` | Каталог графа Kuzu только для расширения | `projects/<профиль>/extension_graphdb/graph.db` |

**Одним запуском — основная конфигурация и расширение:** `run_index_all_your_project.cmd` или `python run_index_all.py` (после `set PROJECT_PROFILE=...`). Выполняется по шагам: векторная БД основной конфигурации → граф основной конфигурации → векторная БД расширения → граф расширения. Если `EXTENSION_CONFIG_PATH` не задан, выполняются только шаги 1–2. Число процессов для графа задаётся переменной `INDEX_GRAPH_WORKERS` (по умолчанию `8`).

**Индексация только расширения** (основные `vectordb` / `graphdb` не изменяются):

```cmd
REM В профиле задайте EXTENSION_CONFIG_PATH в projects\<имя>\<имя>.env
run_index_extension_vector_your_project.cmd
run_index_extension_graph_your_project.cmd
```

Или через Python: `python run_indexer.py --extension --clear --vector-only` и `python index_graph_mp.py --extension --clear`.

Флаг **`--extension`** подставляет пути из `EXTENSION_*`; при необходимости путь к выгрузке можно передать явно: `--config-path D:\ext\dump`.

**Поиск в MCP по расширению:** отдельная запись в `mcpServers` с `VECTORDB_PATH` / `GRAPHDB_PATH`, указывающими на `EXTENSION_VECTORDB_PATH` и `EXTENSION_GRAPHDB_PATH` (или второй профиль только под расширение).

**Альтернатива:** несколько профилей в `projects/<имя>/`, в каждом свои `CONFIG_PATH` и `VECTORDB_PATH` / `GRAPHDB_PATH` — как раньше.

**Резервный вариант без `EXTENSION_*`:** в корне выгрузки должен лежать `Configuration.xml`; при указании только `CONFIG_PATH` на расширение индексируются те же каталоги, что и у основной конфигурации. В логе будет «Тип выгрузки: расширение конфигурации», если XML распознан (см. `config_dump.py`).

---

## Настройки эмбеддингов: модель, URL, API

Файл конфигурации профиля: `projects/<имя>/<имя>.env` или `projects/<имя>/<имя>.env.local` (переопределяет .env).

### Вариант 1: Удалённый API (LM Studio, LocalAI, OpenAI-совместимый)

| Параметр | Где подменять | Описание |
|----------|---------------|----------|
| `EMBEDDING_API_BASE` | **Подставьте URL** вашего API | Базовый URL, например `http://192.168.0.1:1234/v1` для LM Studio |
| `EMBEDDING_MODEL` | **Подставьте имя модели** | Имя модели в API, например `text-embedding-nomic-embed-text-v2-moe` |
| `EMBEDDING_API_KEY` | **Подставьте ключ** (или оставьте `dummy`) | Для локальных серверов (LM Studio, LocalAI) обычно `dummy` или `not-needed` |
| `EMBEDDING_DIMENSION` | Автоопределяется | Размерность вектора; определяется по имени модели (30+ моделей). Задавайте явно, только если модель не в `KNOWN_MODELS` |
| `EMBEDDING_ADD_EOS_MANUAL` | Для Qwen3 | `false` (по умолчанию); `true` приводит к двойному EOS с llama.cpp |

### Вариант 2: Локальная модель (sentence-transformers)

| Параметр | Значение | Описание |
|----------|----------|----------|
| `EMBEDDING_API_BASE` | **Оставьте пустым** или не задавайте | Пустое значение переключает на локальную модель |
| `EMBEDDING_MODEL` | **Подставьте имя модели** | Имя из Hugging Face, например `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |

---

## Qwen3 через LM Studio / GGUF: предупреждение EOS

При использовании моделей Qwen3-Embedding (например, `text-embedding-qwen3-embedding-4b`) через LM Studio может появляться предупреждение:

```
[WARNING] At least one last token in strings embedded is not SEP.
'tokenizer.ggml.add_eos_token' should be set to 'true' in the GGUF header
```

**Это предупреждение косметическое** и на качество эмбеддингов не влияет. llama.cpp (бэкенд LM Studio) **уже добавляет EOS-токен автоматически** на уровне BPE-токенизатора.

**Важно:** параметр `EMBEDDING_ADD_EOS_MANUAL` должен быть **false** (или не задан). Если установить `true`, `QwenEOSEmbeddingWrapper` добавит `<|endoftext|>` в текст, а llama.cpp добавит второй EOS — получится **двойной EOS**, что ухудшит качество эмбеддингов.

```env
# Правильно (по умолчанию):
EMBEDDING_ADD_EOS_MANUAL=false
```

> Если ранее вы использовали `EMBEDDING_ADD_EOS_MANUAL=true`, переиндексируйте с `--clear`, чтобы пересоздать эмбеддинги без двойного EOS.
> Проверка имени модели выполняется case-insensitive: `Qwen3`, `qwen3`, `text-embedding-qwen3-embedding-4b` — все варианты распознаются.
> Подробнее см. [MODEL_CONFIGURATION_RECOMMENDATIONS.md](projects/your_project/MODEL_CONFIGURATION_RECOMMENDATIONS.md), раздел 5.

---

## Настройки токенов и чанков

Эвристика **символов на токен** задаётся **`CHARS_PER_TOKEN`** (по умолчанию **1.65** в `config.py`). Раньше использовалось 2.0 (ближе к латинице); для русского текста и длинных идентификаторов BSL на ту же строку приходится больше токенов, поэтому 1024 символа легко превращаются в 600+ токенов, и движок (например LM Studio) обрезает хвост после лимита модели. При предупреждениях вида «Number of tokens … exceeds … Truncating» уменьшите **`CHARS_PER_TOKEN`** (например 1.5) и/или **`EMBEDDING_MAX_TOKENS`** с запасом (например 480), либо задайте **`EMBEDDING_MAX_CHARS`** явно (например 800–900).

**GGUF / LM Studio:** сообщение `tokenizer.ggml.add_eos_token should be set to true` относится к метаданным конкретной GGUF-сборки; на качество поиска часто мало влияет. Для Nomic через OpenAI API ручной EOS обычно не нужен; **`EMBEDDING_ADD_EOS_MANUAL`** для Qwen — по документации к модели.

| Параметр | Где подменять | Описание |
|----------|---------------|----------|
| `CHARS_PER_TOKEN` | В `.env` профиля | Эвристика символов на токен для расчёта лимитов по символам. По умолчанию `1.65` |
| `EMBEDDING_MAX_TOKENS` | В `.env` профиля | Макс. токенов контекста модели. Если задан — `EMBEDDING_MAX_CHARS ≈ tokens × CHARS_PER_TOKEN`. Пример: `512` для nomic-embed-text-v2-moe |
| `CHUNK_MAX_TOKENS` | В `.env` профиля | Макс. токенов в одном чанке кода. Пример: `512` (число символов считается через `CHARS_PER_TOKEN`) |
| `CHUNK_OVERLAP_TOKENS` | В `.env` профиля | Нахлёст между чанками в токенах. По умолчанию: `100` |
| `CHUNK_MAX_CHARS` | Альтернатива | Макс. символов в чанке, если не задан `CHUNK_MAX_TOKENS` |
| `EMBEDDING_MAX_CHARS` | Альтернатива | Макс. символов для обрезки, если не задан `EMBEDDING_MAX_TOKENS` |
| `BATCH_SIZE_CODE` | В `.env` профиля | Размер батча при индексации кода. По умолчанию: `100`. Для 8 GB RAM: `25` |
| `BATCH_SIZE_METADATA` | В `.env` профиля | Размер батча при индексации метаданных. По умолчанию: `50`. Для 8 GB RAM: `20` |
| `BATCH_SIZE_FORMS` | В `.env` профиля | Размер батча при индексации форм. По умолчанию: `50`. Для 8 GB RAM: `20` |

> **Батчи и производительность:** больший батч = быстрее индексация (меньше запросов к API/модели), но выше пиковое потребление RAM. Рекомендации по конфигурациям ПК — в [MODEL_CONFIGURATION_RECOMMENDATIONS.md](projects/your_project/MODEL_CONFIGURATION_RECOMMENDATIONS.md), раздел 2.6.

### Пример для nomic-embed-text-v2-moe (Context Length 512 токенов)

```env
CHARS_PER_TOKEN=1.65
EMBEDDING_MAX_TOKENS=512
CHUNK_MAX_TOKENS=512
CHUNK_OVERLAP_TOKENS=100
```

**Подробные рекомендации** по выбору модели, чанкам и конфигурации ПК см. в [MODEL_CONFIGURATION_RECOMMENDATIONS.md](projects/your_project/MODEL_CONFIGURATION_RECOMMENDATIONS.md).

---

## Настройки поиска

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `VECTOR_DISTANCE_METRIC` | Метрика: `cosine`, `l2`, `ip` | `cosine` |
| `HYBRID_SEARCH_ALPHA` | Доля векторного поиска (1 — только вектор, 0 — только BM25) | `0.7` |
| `SEARCH_USE_MMR` | Maximal Marginal Relevance для разнообразия | `true` |
| `MMR_LAMBDA` | Баланс релевантности/разнообразия (0–1) | `0.7` |
| `SEARCH_FETCH_K` | Количество кандидатов для re-ranking | `50` |
| `DEFAULT_SEARCH_LIMIT` | Сколько результатов возвращать инструментам поиска по умолчанию (топ‑N), если лимит не задан явно | `5` |
| `MAX_SEARCH_LIMIT` | Верхняя граница лимита результатов | `20` |

> **Гибридный поиск и MMR включены по умолчанию.** При необходимости переопределите в `.env`. Если `rank_bm25` не установлен — будет fallback на чистый вектор с предупреждением.

### Как устроена индексация (простыми словами)

- **Что происходит:** код BSL режется на фрагменты (чанки). Для каждого фрагмента вызывается модель эмбеддингов (локально или через LM Studio / OpenAI‑совместимый API) — получается числовой вектор «смысла». Векторы сохраняются в ChromaDB; при запросе ищутся ближайшие по смыслу фрагменты.
- **Почему в логах «обрезано» и `...`:** у модели есть лимит токенов (часто 512). Если текст длиннее лимита, хвост отбрасывается: в Python — по `EMBEDDING_MAX_CHARS`, в движке эмбеддингов — ещё раз по токенам. Многоточие в конце — следствие обрезки, а не сбой.
- **Почему одно и то же повторяется в логах:** у длинной процедуры несколько чанков; в каждом раньше снова попадала одна и та же «шапка» из комментариев (`// Параметры…`). Плюс при обрезке разные большие куски могут сводиться к одному короткому одинаковому началу.
- **Влияние на поиск:** если в вектор попала в основном шапка, а не логика ниже — по смыслу искать по этому фрагменту хуже. Похожие дубли дают близкие векторы; MMR и гибрид с BM25 частично разнообразят выдачу, но не убирают причину.
- **Что сделано в коде:** для **второго и следующих** чанков одной процедуры (`chunk_index > 0`, `total_chunks > 1`) блок комментариев перед сигнатурой **не дублируется** в тексте для эмбеддинга — в отпечаток попадает больше уникального кода. Сигнатура и тело чанка по-прежнему включаются. Первая часть метода по-прежнему содержит полные комментарии.

> Ускорение индексации от этого обычно **умеренное** (меньше токенов на продолжения); главный выигрыш — **качество поиска**.

**Примечание:** Смена `VECTOR_DISTANCE_METRIC` требует переиндексации (`--clear`).

**find_1c_method_usage:** при заданном `CONFIG_PATH` используется grep по исходникам + обогащение из графа зависимостей; иначе — семантический поиск.

### Что сделать после обновления

1. **Установить зависимость:** `pip install rank_bm25`
2. **Переиндексировать** при смене `VECTOR_DISTANCE_METRIC`: `python run_indexer.py --clear --vector-only`
3. Гибридный поиск и MMR включены по умолчанию — ручная настройка не требуется

---

## Перенос на другую машину

См. [PORTABILITY.md](PORTABILITY.md) — использование `setup_machine.py`, переопределение путей в `*.env.local`.


---

## История изменений

### 04.06.2026 (v1.0.0) — Web-приложение MCP Chat

- Каталог **`app/`**: FastAPI backend, MCP stdio-клиент, RAG (`search_1c_*` + опционально граф), клиент LM Studio.
- UI: выбор сервера, настройки модели, чат, вкладки источников, SVG-визуализация `graph_dependencies` / `graph_references`.
- **`requirements-app.txt`**, **`run_app.cmd`**, **`app/mcp_registry.example.json`**.
- Полное описание релиза: [RELEASE_v1.0.0.md](RELEASE_v1.0.0.md).

### 23.03.2026 — эвристика символов/токен; чанки без дублирования комментариев

- **`CHARS_PER_TOKEN`** в `.env`: по умолчанию **1.65** вместо жёстких 2.0; уменьшает риск тихой обрезки в движке, когда строка по символам «влезает», а по токенам — нет.
- Документация: предупреждения truncation и `add_eos_token` в GGUF.
- **`vectordb_manager.add_code_chunks`:** для `chunk_index > 0` при `total_chunks > 1` комментарии перед сигнатурой не включаются в текст эмбеддинга (шапка остаётся только в первом чанке метода).
- **README:** раздел «Как устроена индексация», таблица поиска дополнена `DEFAULT_SEARCH_LIMIT` / `MAX_SEARCH_LIMIT`.

### 19.03.2026 — устойчивость парсера BSL (Issue #1)

#### Исправление падения при парсинге (БитФинанс и др.):
- **Проблема:** При индексации некоторых конфигураций (например, БитФинанс 292) парсер падал с `AttributeError: 'NoneType' object has no attribute 'capitalize'` — `match.group("type")` возвращал `None` для отдельных совпадений regex.
- **parser_1c.py** — внесены изменения:
  - Проверка `match.group("type")` и `match.group("name")` на `None` — проблемные совпадения пропускаются с предупреждением в лог
  - Безопасное использование `match.group("params")` и `match.group("body")` через `or ""`
  - `try/except (AttributeError, TypeError)` вокруг обработки каждого совпадения — при ошибке логируется предупреждение с путём к файлу и фрагментом, парсинг продолжается
  - Исправлен regex закрытия процедур/функций: `\n\s*(?:Конец(?:Процедуры|Функции)|EndProcedure|EndFunction)` — теперь `EndProcedure`/`EndFunction` не срабатывают внутри строк
  - В `scan_all_modules()` — `try/except` вокруг обработки каждого файла: ошибка в одном модуле не останавливает индексацию остальных

> При появлении предупреждений «Пропуск совпадения: группа 'type' пуста» или «Пропуск совпадения при парсинге» — проверьте лог; в этих случаях отдельные методы могут быть пропущены, но индексация завершится успешно.

---

### 02.03.2026 (v0.3.0) — расширение парсинга, автоопределение размерности, гибридный поиск

#### Автоопределение размерности эмбеддингов:
- **config.py** — добавлена таблица `KNOWN_MODELS` (30+ моделей: nomic, BGE, MiniLM, E5, Jina, Cohere, OpenAI, Granite) с размерностями и max_tokens. `EMBEDDING_DIMENSION` определяется автоматически по имени модели; для Qwen3 — эвристика по размеру модели (0.6B→1024, 1.7B→2048, 4B→2560, 8B→4096). Ручное задание в `.env` по-прежнему приоритетно.

#### Расширение MetadataParser:
- **parser_1c.py** — `MetadataParser.parse_object_metadata()` теперь извлекает:
  - Реквизиты табличных частей с типами (ранее — только имена ТЧ без реквизитов)
  - Измерения регистров (`dimensions`)
  - Ресурсы регистров (`resources`)
  - Команды объектов (`commands`)
- **vectordb_manager.py** — `_build_metadata_document()` формирует расширенный текст для эмбеддинга (измерения, ресурсы, реквизиты ТЧ, команды); метаданные коллекции дополнены полями `tabular_sections`, `has_dimensions`, `has_resources`, `commands_count`.

#### Улучшение BSLParser:
- **parser_1c.py** — `BSLParser` обновлён:
  - Извлечение директив компиляции (`&НаКлиенте`, `&НаСервере`, `&НаСервереБезКонтекста`, `&НаКлиентеНаСервереБезКонтекста`) — записываются в поле `directive`
  - Извлечение модульных переменных (`Перем`) — записываются в `module_variables`
  - Поддержка английских ключевых слов (`Procedure`/`Function`/`EndProcedure`/`EndFunction`)
  - Более robustный regex через `re.compile` с именованными группами
- **vectordb_manager.py** — поле `directive` добавлено в метаданные кода при индексации.

#### Интеграция grep с графом:
- **server.py** — `find_1c_method_usage` дополнен: после grep/vector-поиска результаты обогащаются данными из графа (`graph_dependencies`). Поле `source` указывает источник ("grep", "vector", "graph"); связанные объекты из графа возвращаются в отдельном поле `graph_related`.

#### Автоматизация гибридного поиска:
- **config.py** — новые дефолты: `HYBRID_SEARCH_ALPHA=0.7` (было 1.0 — только вектор), `SEARCH_USE_MMR=true` (было false), `MMR_LAMBDA=0.7` (было 0.5). Гибридный поиск (вектор + BM25) и MMR теперь включены по умолчанию.
- **vectordb_manager.py** — при инициализации выводится текущий режим поиска; если BM25 не установлен — выводится предупреждение.
- **your_project.env** — комментарии обновлены: описано автоопределение EMBEDDING_DIMENSION, дефолты гибридного поиска.

---

### 03.03.2026

#### Unit-тесты (pytest):
- Создана структура `tests/` с `conftest.py` и общими фикстурами (BSL-файлы, дерево конфигурации, временные БД).
- **test_parser_1c.py** — 20 тестов: парсинг процедур/функций (экспорт, комментарии, параметры, сигнатуры), пустые файлы, модули без процедур, препроцессорные директивы, извлечение ссылок на метаданные (все 10 типов коллекций, дедупликация, case-insensitive), XML-парсинг (реквизиты, модули, синоним, невалидный XML), ConfigurationScanner.
- **test_graph_db.py** — 22 теста: инициализация и таблицы, add_node (upsert, все типы, extra), add_edge (дедупликация, разные типы, extra), ensure_metadata_node (id-формат, идемпотентность), clear, get_dependencies/get_references (с лимитами, пустые), _escape_like (%, _, \\), get_stats.
- **test_vectordb_manager.py** — 24 теста: _tokenize (пунктуация, короткие токены, пустая строка), _hybrid_rerank (alpha=1, BM25, сортировка), _apply_mmr (лимит, lambda=1, пустые), add/search code/metadata/forms, clear_all_collections, get_stats, _format_results_from_items, QwenEOSEmbeddingWrapper (EOS-суффикс, дедупликация, name).
- **test_code_grep.py** — 11 тестов: _extract_object_info_from_path (стандартный, корневой, несвязанный путь), _find_enclosing_method (внутри процедуры/функции, до методов, за пределами, ноль, отрицательный), grep_method_usage (нахождение, отсутствие, лимит, case-insensitive, word boundary, enclosing tracking, бинарные файлы).
- **test_config.py** — 10 тестов: LOG_LEVEL (uppercase, lowercase, mixed, пустой), validate (пустой/несуществующий/валидный CONFIG_PATH), профили, коллекции, чанкинг.
- `pytest>=8.0.0` добавлен в `requirements.txt`.

#### index_graph.py → deprecated:
- **index_graph.py** — добавлен docstring `[DEPRECATED]` и `warnings.warn()` при запуске. Рекомендуется `index_graph_mp.py --workers 1` как замена.
- **README.md** — `index_graph_mp.py` указан как основной скрипт графовой индексации; `index_graph.py` помечен `[deprecated]`; таблица аргументов объединена (включая `--workers`); добавлены примеры запуска.

#### Qwen3 EOS — исправление двойного EOS:
- **Проблема:** llama.cpp (LM Studio) уже добавляет EOS-токен автоматически на уровне BPE-токенизатора. `QwenEOSEmbeddingWrapper` при `EMBEDDING_ADD_EOS_MANUAL=true` добавлял `<|endoftext|>` в текст — получался двойной EOS, ухудшающий качество эмбеддингов.
- **Решение:** `EMBEDDING_ADD_EOS_MANUAL` по умолчанию `false`. При включении — выводится `logger.warning` о двойном EOS.
- **vectordb_manager.py** — docstring `QwenEOSEmbeddingWrapper` дополнен предупреждением о двойном EOS; при активации wrapper выводится `logger.warning` вместо `logger.info`; case-insensitive проверка модели.
- **config.py** — при `EMBEDDING_ADD_EOS_MANUAL=true` для Qwen3 выводится `logger.warning` с рекомендацией отключить.
- **your_project.env** — комментарий обновлён: рекомендуется `false`, пояснение о двойном EOS.
- **README.md** — раздел «Qwen3 через LM Studio / GGUF: предупреждение EOS» переписан: предупреждение объяснено как косметическое, `EMBEDDING_ADD_EOS_MANUAL=false` как рекомендация.
- **MODEL_CONFIGURATION_RECOMMENDATIONS.md** — раздел 5 полностью переписан: объяснение механизма двойного EOS, рекомендация `false`, инструкция миграции с `--clear`.

#### Прочие улучшения (из предыдущих ревью):
- **vectordb_manager.py** — рефакторинг: общая логика поиска (fetch, hybrid re-rank, MMR) вынесена в `_query_collection()`, устранено дублирование в `search_code`, `search_metadata`, `search_forms`.
- **server.py** — добавлена валидация входных параметров MCP-инструментов (`query`, `method_name`, `object_name` — проверка на пустую строку; `limit` — `try/except` для некорректного типа и `clamp` в допустимый диапазон); `graph_manager.close()` вызывается в `finally` при завершении сервера.
- **code_grep.py** — оптимизирован `grep_method_usage`: инкрементальное отслеживание `current_method` вместо повторного вызова `_find_enclosing_method` для каждого совпадения (O(N) вместо O(N×M)).
- **index_graph_mp.py** — формат node ID исправлен на `metadata:{obj_type}:{obj_name}`; перед добавлением ребра вызывается `ensure_metadata_node` для целевого узла; кеш сканирования валидирует `config_path`.
- **index_graph.py** — кеш сканирования валидирует `config_path` (аналогично `_mp`).
- **config.py** — `LOG_LEVEL` нормализуется в верхний регистр (`(os.getenv(...) or "INFO").upper()`).
- **index_config.py** — переменная `l` переименована в `ln` для читаемости.
- **.gitignore** — добавлены `graph_scan_cache.json`, `graph_checkpoint.json`, `projects/*/graphdb/`.
- **SERVER_METADATA.json** — добавлено поле `serverVersion: "0.2.0"`.
- **setup_machine.py**, **init_project.py** — сгенерированные `.cmd` корректно вызывают `index_graph_mp.py` вместо `index_graph.py`.

---

### 02.03.2026

#### Синхронизация с D:\Vibe1c\Vector и исправления:
- **config.py** — добавлена поддержка вложенных путей профилей (например, `esty\osn` → `projects/esty/osn/osn.env`). Имя файла `.env` берётся из `Path(profile_name).name`.
- **index_graph_mp.py** — исправлена ошибка в progress bar: удалён дублирующий параметр `total` в вызове `tqdm()`. Ранее указывалось `total=len(args), initial=start_mod_idx, total=len(modules_data)`, что приводило к некорректному отображению прогресса. Теперь используется `total=len(modules_data), initial=start_mod_idx`.
- **Синхронизация** — все `.py` файлы приведены в соответствие с версией из D:\Vibe1c\Vector.

#### Структура проектов и документация:
- **projects/** — каждая подпапка `projects/<имя>/` содержит `.env`, `vectordb/`, `graphdb/` и документацию профиля.
- **MODEL_CONFIGURATION_RECOMMENDATIONS.md** — рекомендации по выбору моделей эмбеддингов (nomic, BGE-M3, Qwen3), настройке чанков и контекста в зависимости от объёма RAM (8/16/32/48 GB).
- **Раздельная индексация** — `run_index_vector_*.cmd` (только векторная БД), `run_index_graph_*.cmd` (только граф). Полная индексация — `python run_indexer.py --clear` без `--vector-only`.
- **init_project.py** — создание нового проекта: `python init_project.py -n my_project -c "D:\Path\To\1C\Config" --add-mcp --index -y`.
- **EMBEDDING_ADD_EOS_MANUAL для Qwen3** — в MCP_SETUP, README профиля, `your_project.env` и `projects/README.md` добавлены инструкции. **Обновление:** рекомендуется `false` (по умолчанию) — llama.cpp добавляет EOS автоматически; `true` приводит к двойному EOS (см. запись 03.03.2026).

---

### 01.03.2026 (Vector v1.x)

#### Новые функции:
- **Индексация только векторной БД** — `run_index_your_project.cmd` по умолчанию индексирует только векторную БД (код, метаданные, формы). Граф индексируется отдельно через `run_index_graph_your_project.cmd`. Флаг `--vector-only` для `run_indexer.py`.
- **Кеширование сканирования** — результаты сканирования метаданных, модулей и форм сохраняются в graph_scan_cache.json. При повторном запуске сканирование пропускается, данные загружаются из кэша.
- **Чекпоинты (возобновление с места остановки)** — прогресс индексации графа сохраняется в graph_checkpoint.json. При прерывании (Ctrl+C, ошибка) можно продолжить с места остановки.
- **Прогресс-бары** — добавлено отображение прогресса с использованием 	qdm для модулей и форм.

#### Использование:

```cmd
# Только векторная БД (код, метаданные, формы) — по умолчанию в run_index_your_project.cmd
python run_indexer.py --clear --vector-only

# Полная индексация (векторная БД + граф)
python run_indexer.py --clear

# Обычный запуск (с использованием кэша)
python run_indexer.py

# Запуск без кэша (полное пересканирование)
python run_indexer.py --no-cache

# Очистка + без кэша
python run_indexer.py --clear --no-cache
```

| Аргумент | Описание |
|----------|----------|
| --vector-only | Индексировать только векторную БД; граф пропускается |
| --clear | Очистить векторную БД перед индексацией |
| --no-cache | Игнорировать кэш сканирования (для графа) |

#### Как работает возобновление:
1. При прерывании чекпоинт сохраняется автоматически
2. При следующем запуске загружается чекпоинт и индексация продолжается
3. Успешное завершение удаляет чекпоинт

#### Аргументы командной строки (index_graph_mp.py):
| Аргумент | Описание |
|----------|----------|
| --config-path | Путь к конфигурации 1С |
| --db-path | Путь к файлу графа |
| --clear | Очистить граф перед индексацией (сбрасывает чекпоинт) |
| --no-cache | Игнорировать кэш сканирования и пересканировать файлы |
| --workers N | Количество процессов (по умолчанию: cpu_count - 1, `--workers 1` для однопроцессорного режима) |

> **Примечание:** `index_graph.py` (без `_mp`) оставлен для обратной совместимости и помечен как deprecated.
> Используйте `index_graph_mp.py --workers 1` как полную замену однопроцессорной версии.

#### Кеш сканирования:

Кеш (`graph_scan_cache.json`) содержит путь к конфигурации и автоматически инвалидируется при смене `CONFIG_PATH`. Если нужно принудительно пересканировать, используйте `--no-cache`.

#### Примеры запуска:

```cmd
# Однопроцессорный режим (эквивалент старого index_graph.py)
python index_graph_mp.py --workers 1 --clear

# Многопроцессорный режим (8 процессов)
python index_graph_mp.py --workers 8 --no-cache

# Автоматическое определение (cpu_count - 1)
python index_graph_mp.py --clear
```

**Ожидаемое ускорение:** 2-4x в зависимости от количества ядер CPU и размера конфигурации.

---

### Версии до 01.03.2026

- Базовая версия без кеширования и чекпоинтов



- `search_1c_code` — семантический поиск по коду 1С
- `search_1c_metadata` — поиск объектов метаданных
- `search_1c_forms` — поиск форм
- `search_by_object_name` — полная информация по объекту
- `find_1c_method_usage` — поиск мест использования метода
- `graph_dependencies`, `graph_references`, `graph_stats` — анализ графа зависимостей
- `get_vectordb_stats` — статистика векторной БД
- `get_analyst_instructions` — инструкция для аналитика

Подробное описание — в `projects/your_project/ИнструкцияПоИспользованиюMCP.md`.
