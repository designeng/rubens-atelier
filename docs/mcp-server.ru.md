# MCP-сервер

Сервер объявлен в [app/mcp_server.py](../app/mcp_server.py). Через него любой MCP-клиент (Claude Code, Claude Desktop, …) управляет фабрикой: проектами, историями, inbox, scheduler'ом и workspaces.

Это клиент HTTP API, а не второй бэкенд. Каждый инструмент вызывает эндпоинт API, поэтому проходит те же проверки, что и доска (compare-and-set по версии, блокеры, бюджеты), и одинаково работает с `make dev`, compose и удалённым развёртыванием. Транспорт — stdio.

## Подключение

API должен быть запущен (`make dev` или `docker compose up`).

```bash
claude mcp add factory -- uv run --directory /path/to/agent-harness python -m app.mcp_server
```

Удалённый harness:

```bash
claude mcp add factory \
  -e HARNESS_API_URL=https://factory.example -e HARNESS_API_TOKEN=… \
  -- uv run --directory /path/to/agent-harness python -m app.mcp_server
```

`make mcp` запускает то же самое вручную; сервер ждёт MCP-клиента на stdin, в терминале он ничего не показывает.

`.mcp.json` в репозитории нет намеренно: агенты клонируют этот репозиторий в свои workspaces и подхватили бы сервер оттуда.

## Настройки

| Переменная | По умолчанию | |
|---|---|---|
| `HARNESS_API_URL` | `http://localhost:8000` | Адрес HTTP API |
| `HARNESS_API_TOKEN` | пусто | Bearer-токен; пустой — запросы без авторизации |

Обе читаются из окружения или `.env` в рабочей директории, как и остальные настройки (`app/config.py`).

## Инструменты

R — только чтение, W — меняет состояние, D — необратимо (MCP-аннотации `readOnlyHint` / `destructiveHint`).

### Проекты

| Инструмент | | Эндпоинт | Что делает |
|---|---|---|---|
| `list_projects` | R | `GET /projects` | Проекты с репозиторием, verify-командой, auto-merge, бюджетом и тратами |
| `create_project` | W | `POST /projects` | `slug`, `repo` (`owner/name`), опционально `base_branch`, `verify_command`, `auto_merge`, `budget_usd`, `installation_id` |
| `update_project` | W | `PATCH /projects/{slug}` | Переданные поля меняются, остальные остаются; `clear` (`base_branch`, `verify_command`, `budget_usd`) сбрасывает значения |
| `import_issues` | W | `POST /projects/{slug}/import-issues` | Открытые issues репозитория — в backlog историями; ничего не запускается |

### Доска и истории

| Инструмент | | Эндпоинт | Что делает |
|---|---|---|---|
| `get_board` | R | `GET /projects/{slug}/board` | Колонки backlog, working, needs_human, done, failed с сокращёнными карточками; `done_limit` (по умолчанию 20) и `done_total` |
| `get_inbox` | R | `GET /projects/{slug}/inbox` | Открытые запросы к человеку, старые первыми |
| `get_item` | R | `GET /items/{id}` | История целиком: тело, переходы, runs (с `task_id`), артефакты, открытый запрос. Артефакты — только последняя версия каждого, все — с `all_artifacts=true` |
| `create_item` | W | `POST /projects/{slug}/items` | `title`, `body`, `kind`, `priority` (-100..100), `parent_id`, `issue`, `model`, `lean_context`, `budget_usd`, `blocked_by`; `start=true` сразу запускает |
| `update_item` | W | `PATCH /items/{id}` | Переданные поля меняются; `clear` (`model`, `lean_context`, `budget_usd`) возвращает к умолчанию. Пустой вызов — ошибка «nothing to change» |
| `item_action` | W | `POST /items/{id}/{action}` | `start`, `run-now`, `cancel`, `reopen`, `open-pr` |
| `delete_item` | D | `DELETE /items/{id}` | Удаляет упавшую или отменённую историю со всей историей |
| `answer_request` | W | `POST /requests/{id}/answer` | `option` из списка запроса и `comment`, который станет артефактом для следующего агента |
| `get_item_git` | R | `GET /items/{id}/git` | `git status`, коммиты и изменённые файлы относительно базовой ветки; `diff=true` — полный diff |
| `get_run_log` | R | `GET /tasks/{id}/events` + `GET /tasks/{id}` | События сессии агента (текст, вызовы инструментов, verify), статус, причина падения, стоимость. Строки обрезаются до 2000 символов; `next_after` — для следующей страницы |

### Scheduler и модели

| Инструмент | | Эндпоинт | Что делает |
|---|---|---|---|
| `scheduler_status` | R | `GET /scheduler` | Занятые слоты, очередь, capacity и её причина, окна подписки |
| `pause_scheduler` | W | `POST /scheduler/pause` | Новые шаги не выдаются, текущие доработают; `reason` |
| `resume_scheduler` | W | `POST /scheduler/resume` | |
| `set_scheduler_limits` | W | `PATCH` / `DELETE /scheduler/limits` | `max_agents`, `max_agents_per_project`, `force`; `reset=true` сбрасывает все переопределения |
| `list_models` | R | `GET /models` | Модели для историй, модель по умолчанию, lean context по умолчанию |

### Workspaces

| Инструмент | | Эндпоинт | Что делает |
|---|---|---|---|
| `list_workspaces` | R | `GET /workspaces` | Workspaces на томе: владелец, размер, фаза, можно ли удалить; фильтры `project`, `phase`, `size=false` пропускает подсчёт размера |
| `prune_workspaces` | D | `POST /workspaces/prune` | Удаляет workspaces завершённых и удалённых историй; `include_failed` добавляет упавшие (теряются незапушенные коммиты). По умолчанию `dry_run=true` — сначала смотрите `would_remove` |

## Что не выставлено

- Терминал доски (`POST /items/{id}/exec`) — это выполнение команд на сервере.
- Удаление одного workspace (`DELETE /workspaces/{id}`) — есть `prune_workspaces` с dry run.
- Разовые задачи (`POST /tasks`, `/issues/run`) и SSE-стрим — для фабрики есть истории, лог run'а читается постранично через `get_run_log`.
- GitHub App (`/github/app`, installations).

## Ошибки

Ответ API с кодом 4xx/5xx превращается в ошибку инструмента с методом, путём, кодом и `detail`, например `GET /projects/missing/board failed with 404: project not found`. Недоступный API — `factory API at … is unreachable`. Клиент видит это как `isError` и может исправить вызов.

## Тесты

[tests/test_mcp_server.py](../tests/test_mcp_server.py) вызывает инструменты через `mcp.call_tool`, а их HTTP-запросы через `httpx.MockTransport` уходят в приложение in-process (фикстура `client`). Подменить транспорт можно атрибутом `mcp_server.transport`.
