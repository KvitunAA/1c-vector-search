# Релиз 1.0.0 — Web-приложение «1С Vector MCP Chat»

**Дата:** 04.06.2026  
**Тип:** minor feature (новый компонент, без изменения протокола MCP-сервера)

## Кратко

Добавлено локальное web-приложение для работы с проиндексированными конфигурациями 1С **без Cursor**: выбор vector MCP-профиля, вопрос на естественном языке, семантический поиск через MCP и ответ **чат-модели из LM Studio** с панелью источников и визуализацией графа зависимостей.

Существующий MCP-сервер (`server.py`, stdio для Cursor) **не изменён** — приложение выступает отдельным клиентом поверх тех же инструментов.

## Новые файлы и каталоги

| Путь | Назначение |
|------|------------|
| `app/main.py` | FastAPI: API и раздача статики |
| `app/registry.py` | Реестр MCP-серверов (профили + `mcp_registry.json`) |
| `app/mcp_client.py` | MCP-клиент через stdio |
| `app/lmstudio.py` | Клиент LM Studio (`/v1/chat/completions`, `/v1/models`) |
| `app/rag.py` | RAG: поиск → контекст → ответ |
| `app/static/` | UI: чат, источники, SVG-граф |
| `app/mcp_registry.example.json` | Пример реестра (KA, ERP, ZUP и др.) |
| `requirements-app.txt` | Зависимости web-слоя (`fastapi`, `uvicorn`, `httpx`) |
| `run_app.cmd` | Запуск на `http://127.0.0.1:8765` |

## Возможности

- **Выбор MCP-сервера** — автообнаружение профилей в `projects/<имя>/` и переопределение через `app/mcp_registry.json`.
- **Чат с RAG** — параллельный вызов `search_1c_code`, `search_1c_metadata`, `search_1c_forms`; при запросах о зависимостях — `graph_dependencies` / `graph_references`.
- **LM Studio** — настраиваемый URL, модель чата, temperature, max tokens (отдельно от модели эмбеддингов профиля MCP).
- **Панель источников** — код, метаданные, формы с relevance и путями к файлам.
- **Граф** — SVG-схема «кто зависит / что использует» для центрального объекта.
- **Диагностика** — `GET /api/health` (LM Studio + выбранный MCP), проверка из UI.

## API (кратко)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/mcp` | Список серверов и статус индекса |
| GET | `/api/mcp/{name}/stats` | Статистика vector + graph |
| GET | `/api/mcp/{name}/tools` | Список инструментов MCP |
| GET | `/api/health` | LM Studio и опционально MCP |
| POST | `/api/chat` | Вопрос → ответ + sources + graph |
| POST | `/api/tool` | Прямой вызов инструмента |

## Установка и запуск

```cmd
cd 1c-vector-search-KA_Vector
pip install -r requirements.txt
pip install -r requirements-app.txt

REM Проиндексируйте профиль (если ещё не сделано):
run_index_your_project.cmd

REM Опционально: app\mcp_registry.json по примеру mcp_registry.example.json

run_app.cmd
```

Откройте в браузере: **http://127.0.0.1:8765**

Запустите **LM Studio** с загруженной чат-моделью. Эмбеддинги для MCP по-прежнему задаются в `projects/<имя>/<имя>.env` (`EMBEDDING_API_BASE`, `EMBEDDING_MODEL`).

### Python на Windows

Если `python` в PATH — заглушка Microsoft Store, укажите интерпретатор в `local.env`:

```env
VECTOR_PYTHON_PATH=C:\Users\...\AppData\Local\Programs\Python\Python312\python.exe
```

Либо запускайте через `py -3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765`.

## Ограничения и требования

- Нужна **проиндексированная** векторная БД (`vectordb/vectordb.sqlite` в профиле); иначе `POST /api/chat` вернёт **409** с понятным текстом.
- На каждый запрос чата поднимается **отдельная stdio-сессия** MCP (как при обращении из Cursor); для тяжёлых сценариев учитывайте время старта сервера.
- Чат и эмбеддинги — **разные модели** в LM Studio; в UI настраивается только чат.
- `test.svg` в корне репозитория к релизу не относится — в коммит не включать.

## Связь с предыдущими изменениями на `main`

На ветке `main` уже есть переход хранилища (**sqlite-vec** + **Kuzu** вместо ChromaDB и SQLite-графа, коммит `786f0e0`). Web-приложение совместимо с этой версией: вызывает те же MCP-инструменты, не зависит от внутренней реализации БД.

## Рекомендуемое сообщение коммита

```
feat(app): web-витрина MCP Chat с LM Studio (v1.0.0)

- FastAPI backend: реестр MCP, stdio-клиент, RAG, health/chat/tool API
- UI: выбор сервера, чат, источники, SVG-граф зависимостей
- requirements-app.txt, run_app.cmd, пример mcp_registry.example.json
- Документация: RELEASE_v1.0.0.md, раздел в README
```

## Тег (после коммита)

```cmd
git tag -a v1.0.0 -m "Web-приложение 1С Vector MCP Chat"
git push origin v1.0.0
```
