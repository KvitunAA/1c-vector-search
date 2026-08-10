# Доступ к MCP из ChatGPT (Secure MCP Tunnel)

Как дать аналитику доступ к поиску по конфигурации ERP прямо из ChatGPT в браузере,
не выставляя сервер в интернет.

Для разработчиков в Cursor и Codex это не нужно — там проще и дешевле путь через SSH,
см. [SCALING.md](SCALING.md).

## Как это устроено

```
ChatGPT (браузер)  →  OpenAI  ←──[исходящий HTTPS]──  tunnel-client
                                                       (на SERVER_HOST)
                                                            ↓ 127.0.0.1:8181
                                                       server_http.py
                                                            ↓
                                                   sqlite-vec + Kuzu
```

`tunnel-client` запускается **внутри вашей сети**, сам открывает исходящее HTTPS-соединение
к OpenAI и забирает оттуда запросы. Входящие порты не открываются, публичный домен и
сертификат не нужны, правила межсетевого экрана менять не требуется.

MCP-сервер слушает **только петлевой адрес** `127.0.0.1` — снаружи и даже из локальной сети
он недоступен, к нему обращается лишь `tunnel-client` с того же хоста.

**Свой сервер OAuth не нужен.** Доступ ограничивают control-plane API-ключ и права
рабочего пространства OpenAI. Профиль `sample_mcp_remote_no_auth` рассчитан ровно на такой
случай — MCP-сервер без собственных OAuth-метаданных.

## Два разных места, которые легко перепутать

Настройка живёт в **двух** несвязанных на первый взгляд местах, и это главный источник путаницы:

- **Platform** (`platform.openai.com`) — организация API. Здесь создаётся туннель, здесь же
  выдаются ключи и права.
- **ChatGPT** (`chatgpt.com`) — здесь аналитик подключает коннектор.

Из документации дословно: *«туннель может существовать в Platform и всё равно не появиться
в ChatGPT, если неверно связано рабочее пространство или не хватает прав коннектора»*.
Поэтому создавайте туннель **сразу с нужным workspace scope**.

## Откуда что берётся

| Значение | Где взять | Зачем |
|---|---|---|
| `CONTROL_PLANE_TUNNEL_ID` | [Tunnels management](https://platform.openai.com/settings/organization/tunnels) или `tunnel-client admin tunnels create` | Общий якорь: один и тот же id должны использовать демон и коннектор ChatGPT |
| `CONTROL_PLANE_API_KEY` (runtime) | [Runtime API keys](https://platform.openai.com/settings/organization/api-keys) | Аутентифицирует `doctor` и `run`. Нужен всегда |
| `OPENAI_ADMIN_KEY` | [Admin API keys](https://platform.openai.com/settings/organization/admin-keys) | **Только** для создания/удаления туннелей из CLI |

Оттуда же, со страницы Tunnels, скачивается поддерживаемая сборка `tunnel-client`.

**`tunnel_id` — это не то же самое, что runtime-ключ.** В руководстве это вынесено в раздел
«что обычно стопорит операторов»: их путают чаще всего.

При создании runtime-ключа выбирайте **Restricted** и права Tunnels **Read + Use**.
Не ставьте **All** и не подсовывайте вместо него админский ключ — админский для долгоживущего
демона использовать нельзя.

## Права

Настраиваются в [Organization roles](https://platform.openai.com/settings/organization/people/roles)
и [groups](https://platform.openai.com/settings/organization/people/groups):

- запуск демона и подключение коннектора — Tunnels **Read + Use**;
- создание и правка туннелей — Tunnels **Read + Manage**;
- создание админских ключей — отдельное право Platform admin-key.

Роли удобнее назначать на группы, а не на людей по одному.

Режим разработчика и кастомные коннекторы включает администратор рабочего пространства
ChatGPT (Workspace Settings → Permissions & Roles → Connected Data). На личных тарифах
этого пункта может не быть — проверьте до начала работ.

Формат идентификатора туннеля: `tunnel_` и ровно 32 шестнадцатеричных символа в нижнем регистре.

## Шаг 1. MCP-сервер по HTTP на петлевом адресе

**Статус: развёрнуто и проверено на SERVER_HOST.**

Ручной запуск (для проверки):

```powershell
cd C:\path\to\1c-vector-search
.\run_server_http.cmd ERP
```

Лаунчер сам подставляет профиль, пути к базам, Python из `local.env` и слушает
только `127.0.0.1:8181`. Чтобы открыть сервер в локальной сети, задайте `MCP_HTTP_HOST`
до запуска — по умолчанию наружу он не смотрит.

Проверка в другом окне:

```powershell
Invoke-RestMethod http://127.0.0.1:8181/healthz
```

Результат проверки на сервере:

```
{"status":"ok","profile":"ERP","vector_records":1019760,"graph_nodes":127701}
```

Настоящий MCP-хендшейк по HTTP тоже проверен: `initialize` вернул
`serverInfo: 1c-vector-search`, выдался `mcp-session-id`, `tools/list` отдал все 10
инструментов. Сессии работают — это то, чего требует коннектор ChatGPT и чего не даёт stdio.

Зависимости уже установлены: `uvicorn` и `starlette` идут в комплекте с `mcp 1.29.0`.

### Автозапуск

Без этого сервер умрёт вместе с сеансом — ровно та же ловушка, что и с LM Studio.
Выполните **от имени администратора** на сервере:

```powershell
$action = New-ScheduledTaskAction -Execute "C:\path\to\1c-vector-search\run_server_http.cmd" `
            -Argument "ERP" -WorkingDirectory "C:\path\to\1c-vector-search"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "mcp-user" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
              -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "1c-vector-search MCP HTTP (ERP)" `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force

Start-ScheduledTask -TaskName "1c-vector-search MCP HTTP (ERP)"
Invoke-RestMethod http://127.0.0.1:8181/healthz
```

Тип входа **S4U** выбран намеренно: задача выполняется от непривилегированного `mcp-user`
и при этом **не требует хранить его пароль**. Учётной записи хватает доступа к локальным
файлам; сетевых ресурсов серверу не нужно.

Если задача не стартует с ошибкой прав, выдайте `mcp-user` право «Вход в качестве пакетного
задания» (`Log on as a batch job`) в локальной политике безопасности.

## Шаг 2. Установка tunnel-client

Ссылка на скачивание есть в Platform → Tunnels; сборки также лежат в
[github.com/openai/tunnel-client](https://github.com/openai/tunnel-client/releases).

Для вашего сервера нужен `tunnel-client-vX.Y.Z-windows-amd64.zip` (~25 МБ).
Windows-сборка официально публикуется, отдельная Linux-машина не нужна.

Берите последнюю **стабильную** версию, а не помеченную `-dev`.

Распакуйте, например, в `C:\path\to\tunnel-client\`.

## Шаг 3. Профиль и запуск

```powershell
cd C:\path\to\tunnel-client

# Профиль под HTTP-сервер без собственного OAuth
.\tunnel-client.exe init --sample sample_mcp_remote_no_auth `
    --profile erp-vector `
    --tunnel-id tunnel_ВАШ_ИДЕНТИФИКАТОР `
    --mcp-server-url http://127.0.0.1:8181/mcp

# Проверка конфигурации ДО запуска, с объяснением найденного
.\tunnel-client.exe doctor --profile erp-vector --explain

# Запуск
$env:CONTROL_PLANE_API_KEY = "sk-..."
.\tunnel-client.exe run --profile erp-vector --log.level=info
```

Полезные команды: `profiles samples list` (список готовых образцов),
`profiles samples show sample_mcp_remote_no_auth` (образец с пояснениями),
`runtimes status <alias>` (состояние). Встроенный интерфейс — на health-слушателе,
по умолчанию `http://127.0.0.1:8080/ui`.

Ключ лучше не передавать аргументом командной строки: поддерживаются ссылки
`env:ИМЯ_ПЕРЕМЕННОЙ` и `file:/путь/к/секрету`, чтобы он не попадал в argv и в YAML.

После создания туннеля подождите 25–30 секунд, прежде чем ожидать готовности.

## Шаг 4. Коннектор в ChatGPT

Когда локальный демон здоров (`/readyz` отвечает 200), откройте
[chatgpt.com/#settings/Connectors](https://chatgpt.com/#settings/Connectors), выберите
**Connection: Tunnel** и укажите туннель либо вставьте `tunnel_id`.

`tunnel-client run` должен **оставаться запущенным**: демон нужен и для обнаружения
коннектора, и для каждого последующего вызова инструмента из ChatGPT.

Если туннель не появляется в списке ChatGPT — причин обычно три:

1. туннель создан без нужного workspace scope;
2. у оператора коннектора нет права Tunnels **Use**;
3. демон не запущен или не прошёл проверку готовности.

Ещё вариант — туннель создан только что, и управляющий слой ещё не разнёс изменения:
после создания стоит подождать 25–30 секунд.

## Почему HTTP, а не stdio

`tunnel-client` умеет и stdio — тогда он сам запускает `run_server.py` и общается через
стандартный ввод-вывод, без отдельной службы. Заманчиво, но в документации клиента прямо
сказано: **stdio-транспорт не поддерживает MCP-сессии**. Коннектору ChatGPT они нужны,
поэтому основной путь — HTTP.

Если всё же понадобится stdio-вариант:

```powershell
.\tunnel-client.exe run --mcp.command "C:\path\to\1c-vector-search\.python\python.exe C:\path\to\1c-vector-search\run_server.py"
```

## Что стоит ограничить

Аналитику для работы нужны поиск и связи, а не выгрузка исходников целиком. Стоит подумать,
отдавать ли наружу тела модулей: инструменты `search_1c_metadata`, `graph_dependencies`,
`graph_references` и `graph_stats` дают структуру и зависимости без кода. Если ограничиться
ими, цена возможной утечки резко падает, а большинство аналитических вопросов всё равно
закрывается. Это настраивается фильтром в `list_tools` — скажите, если нужно.

## Диагностика

| Симптом | Причина |
|---|---|
| `main channel is required` | Не задан ни `--mcp.server-url`, ни `--mcp.command` |
| Ошибки формата `tunnel_id` | Требуется `tunnel_` и ровно 32 hex-символа в нижнем регистре |
| Запросы отлетают с 421 | Защита от DNS-rebinding: задайте `MCP_HTTP_ALLOWED_HOSTS` (см. `server_http.py`) |
| `tenant context missing`, `fallback_missing_entry` | Известная проблема привязки туннеля к рабочему пространству — вопрос в поддержку OpenAI |
| `/healthz` не отвечает | MCP-сервер не запущен или умер вместе с сеансом — оформите службой |
| `doctor --explain` жалуется на отсутствующий runtime-ключ | Не задан или неверен `CONTROL_PLANE_API_KEY` |
| Туннель есть в Platform, но не виден в ChatGPT | Workspace scope или права коннектора, а **не** демон. Проверяйте это раньше, чем лезть в логи демона |

Проверяйте в таком порядке: `doctor --profile <имя> --explain` до запуска демона, затем
`/readyz` (готовность, а не просто «процесс запустился»), затем локальный интерфейс
`/ui#overview` и `/ui#logs`.

## Источники

- [Secure MCP Tunnel — руководство](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [openai/tunnel-client — конфигурация](https://github.com/openai/tunnel-client/blob/master/docs/configuration.md)
- [openai/tunnel-client — онбординг](https://github.com/openai/tunnel-client/blob/master/docs/onboarding.md)
- [Developer mode и MCP-приложения в ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
