# Подключение профилей к MCP в Cursor

Один репозиторий **ProectVectorSearch** обслуживает **несколько конфигураций 1С** (ЗУП, ERP, КА, БУХ и др.). Для каждой конфигурации — отдельный профиль в `projects/<имя>/` и **отдельное имя MCP** в Cursor (как `tip` и `tip_zup`).

## Быстрый старт: новая конфигурация

```cmd
python init_project.py -n tip_zup -c "D:\1C\ZUP\Config" -m tip_zup -d "MCP: ЗУП" --add-mcp -y
python init_project.py -n tip_erp -c "D:\1C\ERP\Config" -m tip_erp -d "MCP: ERP" --add-mcp -y
```

После индексации синхронизируйте MCP:

```cmd
python scripts\sync_mcp_config.py --cursor
```

Или только `mcp_config.json` в корне репозитория:

```cmd
python scripts\sync_mcp_config.py
```

## Параметры профиля (.env)

| Переменная | Назначение |
|------------|------------|
| `CONFIG_PATH` | Путь к выгрузке 1С |
| `MCP_SERVER_NAME` | Имя MCP в Cursor (`tip_zup`, `tip_erp`, …) |
| `CONFIG_DESCRIPTION` | Описание в mcp.json |

## Подключение вручную (один профиль)

`Ctrl+Shift+P` → **"MCP: Edit Config File"**

```json
"tip_zup": {
  "command": "cmd",
  "args": ["/c", "C:\\Cursor\\ProectVectorSearch\\run_server_tip_zup.cmd"],
  "env": {
    "PROJECT_PROFILE": "tip_zup",
    "VECTORDB_PATH": "C:\\Cursor\\ProectVectorSearch\\projects\\tip_zup\\vectordb",
    "GRAPHDB_PATH": "C:\\Cursor\\ProectVectorSearch\\projects\\tip_zup\\graphdb\\graph.db"
  },
  "description": "MCP: Зарплата и управление персоналом"
}
```

В чате Cursor выбирайте нужный сервер: `@tip_zup`, `@tip_erp` и т.д.

## Индексация

```cmd
run_index_tip_zup.cmd
run_index_graph_tip_zup.cmd
```

Для компактной сборки графа: `set STAGING=1` перед `run_index_graph_*.cmd`.

## Список профилей

```cmd
python scripts\sync_mcp_config.py --list
```
