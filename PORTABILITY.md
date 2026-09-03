# Перенос на другой компьютер

Руководство по переносу проекта на новую машину.

## Быстрый старт после копирования

1. **Скопируйте папку** (Git, архив) на новый компьютер.

2. **Установите зависимости:**
   ```cmd
   cd 1c-vector-search
   pip install -r requirements.txt
   ```

3. **Запустите настройку машины:**
   ```cmd
   python setup_machine.py
   ```
   Или укажите путь к Python явно:
   ```cmd
   python setup_machine.py --python "C:\path\to\python.exe"
   ```
   Скрипт создаст `local.env`, обновит пути в `mcp_config.json` (если есть) и приведёт `.cmd` к переносимому формату.

4. **Укажите путь к выгрузке 1С** — для каждого проекта создайте `projects/<имя>/<имя>.env.local`:
   ```env
   CONFIG_PATH=F:\Configuration\Files
   ```
   Файлы `*.env.local` не коммитятся в Git.

5. **Запустите индексацию:**
   ```cmd
   run_index_your_project.cmd
   ```

6. **Скопируйте содержимое `mcp_config.json`** в настройки Cursor:  
   `Ctrl+Shift+P` → «MCP: Edit Config File» → вставьте блок `mcpServers`.

## Структура путей

| Файл | Переносимый? | Примечание |
|------|--------------|------------|
| `run_server_*.cmd` | ✅ | Используют `%~dp0`, `VECTOR_PYTHON_PATH` |
| `run_index_*.cmd` | ✅ | Пути из профиля `.env` |
| `projects/*/name.env` | ⚠️ | `CONFIG_PATH` — абсолютный, при переносе переопределите в `*.env.local` |
| `projects/*/vectordb/` | ✅ | Относительно корня, можно переиндексировать |
| `mcp_config.json` | 🔄 | Обновляется через `setup_machine.py` |
| `local.env` | ❌ | Не коммитить, создаётся на каждой машине |

## Добавление нового проекта

```cmd
python init_project.py -n my_project -c "F:\Configuration\Files" -m "1c-vector-search-my" -d "Описание" --add-mcp --index -y
```

Для крупных конфигураций (БУХ, ERP, >15k объектов) добавьте `--large-config` — в профиль попадут `GRAPH_USE_STAGING=1`, `INDEX_GRAPH_WORKERS=1` и уменьшенные батчи графа. Либо задайте в `projects/<имя>/<имя>.env`:

```env
GRAPH_USE_STAGING=1
INDEX_GRAPH_WORKERS=1
```

При `GRAPH_USE_STAGING=1` скрипт `run_index_all.py` автоматически передаёт `--staging` в `index_graph_mp.py` (сбор CSV + compact COPY вместо MERGE в Kuzu). Это предотвращает ошибку Kuzu «buffer pool is full» на больших конфигурациях.

Для ручного запуска только графа: `set STAGING=1` и `set CLEAR_GRAPH=1`, затем `run_index_graph_<профиль>.cmd`.

Рекомендуется задать путь к Python:

```cmd
set VECTOR_PYTHON_PATH=C:\path\to\python.exe
python init_project.py -n my_project -c "путь\к\выгрузке" --add-mcp --index -y
```
