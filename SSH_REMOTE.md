# Удалённый доступ к БД на другом сервере (SSH)

Как подключиться к векторной и графовой БД, лежащим на **SERVER_HOST**, с рабочей машины.

## Почему именно так

`sqlite-vec` и `Kuzu` — **встраиваемые** движки, а не серверы БД:

- `vectordb_manager.py` открывает файл через `sqlite3.connect(vectordb.sqlite)`;
- `graph_db.py` открывает каталог через `kuzu.Database(path)`.

У них **нет сетевого протокола и порта**. Подключиться к ним «по IP» нельзя ни при какой
настройке — это свойство движков, а не проекта. Сканирование SERVER_HOST подтверждает:
слушают только 1234 (LM Studio), 22 (SSH), 445 (SMB), 3389 (RDP); ни одного сервиса БД нет.

Решение: **по сети ходит не БД, а сам MCP-сервер**. Он запускается на SERVER_HOST, открывает
файлы БД локально (быстро и безопасно), а его stdio-канал JSON-RPC пробрасывается на рабочую
машину поверх SSH. Для клиента (Cursor) это выглядит как обычный локальный MCP-сервер.

```
Cursor (рабочая машина)
   │  stdio (JSON-RPC)
   ▼
 ssh -T user@SERVER_HOST  ──────► run_server_ssh.cmd
                                          │
                                          ├── run_server.py → server.py
                                          │        ├── sqlite-vec: projects/<профиль>/vectordb/vectordb.sqlite
                                          │        └── Kuzu:      projects/<профиль>/graphdb/graph.db
                                          └── эмбеддинги → http://SERVER_HOST:1234/v1 (LM Studio, локально)
```

Бонус: эмбеддинги считаются на той же машине, где стоит LM Studio, — запросы к модели
не выходят в сеть вообще.

## Что нужно на сервере SERVER_HOST

1. Проект `1c-vector-search` (эта же папка) в известном каталоге, например
   `C:\Cursor\1c-vector-search`.
2. Python 3.10+ и зависимости:
   ```cmd
   pip install -r requirements.txt
   ```
3. Проиндексированные БД профиля: `projects\<профиль>\vectordb\vectordb.sqlite` и
   `projects\<профиль>\graphdb\graph.db`.
4. Файл `local.env` с путём к Python (если он не в PATH):
   ```env
   VECTOR_PYTHON_PATH=C:\Python311\python.exe
   ```
5. Профиль `projects\<профиль>\<профиль>.env` с эмбеддингами через локальный LM Studio:
   ```env
   EMBEDDING_API_BASE=http://127.0.0.1:1234/v1
   EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5
   ```
   Размерность (768) определяется автоматически по имени модели — задавать не нужно.

## Настройка доступа по ключу

Ключ для этого подключения уже создан на рабочей машине:

- приватный: `C:\Users\USERNAME\.ssh\id_ed25519_mcp`
- публичный: `C:\Users\USERNAME\.ssh\id_ed25519_mcp.pub`

```
ssh-ed25519 AAAA... комментарий
```

Ключ обязателен: MCP-клиент запускает `ssh` неинтерактивно и не сможет ввести пароль.

### Установка ключа (на сервере, под нужной учётной записью)

Для **обычного** пользователя:

```cmd
mkdir %USERPROFILE%\.ssh 2>nul
echo ssh-ed25519 AAAA... комментарий>> %USERPROFILE%\.ssh\authorized_keys
```

Для пользователя **из группы «Администраторы»** OpenSSH for Windows читает не
`%USERPROFILE%\.ssh\authorized_keys`, а общий файл — это стандартная и самая частая причина
«Permission denied» при верной настройке:

```cmd
echo ssh-ed25519 AAAA... комментарий>> C:\ProgramData\ssh\administrators_authorized_keys
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
```

### Проверка с рабочей машины

```powershell
ssh -i "$env:USERPROFILE\.ssh\id_ed25519_mcp" -o BatchMode=yes REMOTE_USER@SERVER_HOST "echo OK"
```

Должно вывести `OK` без запроса пароля.

## Подключение в Cursor

`Ctrl+Shift+P` → **MCP: Edit Config File**, добавьте блок из
[`mcp_config_ssh_template.json`](mcp_config_ssh_template.json), подставив `REMOTE_USER`, путь к
проекту на сервере и имя профиля:

```json
"1c-vector-search-ERP": {
  "command": "ssh",
  "args": [
    "-T",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-i", "C:\\Users\\USERNAME\\.ssh\\id_ed25519_mcp",
    "mcp-user@SERVER_HOST",
    "D:\\MCP\\Vector_mcp\\ERP\\run_server_ssh.cmd",
    "ERP"
  ]
}
```

**Не оборачивайте вызов в `cmd /c`.** sshd на Windows и так запускает команду через `cmd.exe`;
вложенность добавляет лишнюю кавычку в аргумент — профиль приезжает как `ERP"`, и сервер идёт
за базой по пути `projects\ERP"\vectordb`. Вызывайте `.cmd` напрямую.

Если путь к проекту на сервере содержит пробелы — заключите его в кавычки внутри строки
аргумента: `"\"C:\\Program Files\\1c-vector-search\\run_server_ssh.cmd\""`.

## Почему отдельный `run_server_ssh.cmd`, а не `run_server_your_project.cmd`

stdout — это канал MCP, в него нельзя писать ничего постороннего. В обычном
`run_server_your_project.cmd` последней строкой стоит **`pause`**: после завершения Python он
пишет в stdout «Press any key to continue» и блокирует процесс. По SSH это ломает сессию.
[`run_server_ssh.cmd`](run_server_ssh.cmd) — без `pause`, без `echo`, с `python -u`
(небуферизованный ввод-вывод обязателен для stdio через SSH-пайп) и с корректным кодом возврата.

## Ключевые параметры ssh

| Параметр | Зачем |
|---|---|
| `-T` | без псевдотерминала — иначе терминальная обработка портит JSON-RPC |
| `-o BatchMode=yes` | никаких интерактивных запросов: при проблеме с ключом падаем сразу, а не висим |
| `-o StrictHostKeyChecking=accept-new` | первое подключение не требует подтверждения вручную |
| `-o ServerAliveInterval=30` `-o ServerAliveCountMax=3` | обрыв сети обнаруживается за ~90 с, а не зависает |
| `-i <ключ>` | явный ключ; не зависит от ssh-agent |

## Диагностика

**`Permission denied (publickey,password,keyboard-interactive)`** — ключ не установлен или лежит
не в том файле. Для админской учётки см. `administrators_authorized_keys` выше. Проверьте права:
`authorized_keys` не должен быть доступен на запись другим пользователям.

**Сервер стартует, но инструменты не отвечают** — проверьте канал вручную, отправив MCP-хендшейк:
```powershell
$k = "$env:USERPROFILE\.ssh\id_ed25519_mcp"
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}' |
  ssh -T -i $k -o BatchMode=yes mcp-user@SERVER_HOST "C:\path\to\1c-vector-search\run_server_ssh.cmd ERP"
```
Ожидаемый ответ — одна строка JSON с `"serverInfo":{"name":"1c-vector-search"}`. Логи идут в
stderr, в stdout не должно быть ничего постороннего: строка вроде «Press any key» или баннер —
причина поломки. Баннер SSH убирается созданием пустого `%USERPROFILE%\.ssh\rc` или отключением
MOTD на сервере.

**`OSError: [WinError 123]` и путь вида `projects\ERP"\vectordb`** — в конфиге осталась обёртка
`cmd /c`. Уберите её, вызывайте `.cmd` напрямую.

**`RuntimeError: IO exception: Could not set lock on file ... graph.db`** — граф уже открыт другим
процессом на запись. Убедитесь, что `GRAPHDB_READ_ONLY` не выставлен в `false`, и что на сервере
не идёт индексация графа (она открывает БД на запись эксклюзивно).

**Поиск отвечает `total_results: 0` при живой БД** — почти всегда недоступны эмбеддинги.
`_query_collection` в `vectordb_manager.py` перехватывает любую ошибку и возвращает пустой
список, поэтому причина видна только в stderr сервера. Проверьте LM Studio:
```powershell
Invoke-RestMethod http://SERVER_HOST:1234/v1/models | Select-Object -ExpandProperty data
```
Идентификатор модели должен совпадать с `EMBEDDING_MODEL` в профиле **буква в букву**. LM Studio
меняет его между сеансами: суффикс квантизации (`@f16`, `@q4_k_m`) может исчезнуть, и тогда
запрос падает с `400 No models loaded`. Надёжнее указывать имя без суффикса.

**`⚠️ Векторная БД пуста` / `Граф пуст`** (в stderr) — на сервере не проиндексирован профиль или
`VECTORDB_PATH` / `GRAPHDB_PATH` указывают не туда. Проверьте:
```cmd
set PROJECT_PROFILE=your_project && python config.py
```

**Медленный первый ответ** — Python и импорт `sentence-transformers` стартуют несколько секунд.
Если `EMBEDDING_API_BASE` задан, локальная модель не грузится и старт заметно быстрее.

## Несколько клиентов одновременно

Каждое подключение поднимает свой процесс сервера. Kuzu даёт **пишущему** процессу эксклюзивную
блокировку каталога БД, поэтому раньше второй экземпляр падал с
`Could not set lock on file ... graph.db`.

Сервер открывает граф **только на чтение** (`GRAPHDB_READ_ONLY`, по умолчанию `true`) — читатели
уживаются друг с другом, и локальный экземпляр на сервере спокойно работает рядом с
подключёнными по SSH. Проверено: две одновременные сессии получают статистику графа независимо.

Отключать режим (`GRAPHDB_READ_ONLY=false`) нужно только если вы намеренно хотите писать в граф
из процесса сервера. На индексацию настройка не влияет: индексаторы всегда открывают БД на запись.

## Ограничения

- **Не запускайте индексацию, пока работает сервер** — индексатор берёт эксклюзивную блокировку
  на запись, и все читатели перестанут стартовать.
- Индексация выполняется **на сервере** (`run_index_all_<профиль>.cmd`), а не по SSH с клиента.
- Профили и пути к БД живут на сервере, в его `projects/<профиль>/`. Рабочая машина о них
  ничего не знает и локальная копия проекта ей для работы MCP не нужна.
- Эмбеддинги считает LM Studio на самом сервере (`EMBEDDING_API_BASE`). Если его локальный сервер
  выключен или выгружена модель, поиск по коду/метаданным вернёт пустой результат, а граф
  продолжит работать — он эмбеддингов не требует.
