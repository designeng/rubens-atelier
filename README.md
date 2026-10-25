# agent-harness

HTTP-сервис для запуска Claude Code ([Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk)) как долгих фоновых задач: FastAPI, Celery и Redis.

Главный сценарий: агент берёт issue из GitHub, реализует его, прогоняет проверки, пушит ветку и открывает PR.

```
POST /tasks ─► Redis (Celery broker) ─► worker ─► Claude Agent SDK (Claude Code)
                  ▲                         │
GET /tasks/{id} ──┤◄── task hash ◄──────────┤  status, session_id, result, cost
GET …/stream ─────┘◄── Redis Stream ◄───────┘  события агента (SSE, с replay)
```

## Запуск

```bash
cp .env.example .env          # ANTHROPIC_API_KEY, HARNESS_API_TOKEN, HARNESS_GITHUB_TOKEN
docker compose up -d --build  # redis, api :8000, worker, beat
```

Агенту в контейнере нужен `ANTHROPIC_API_KEY`. Вместо него можно указать `CLAUDE_CODE_OAUTH_TOKEN`, его выдаёт `claude setup-token`. Логин из keychain хоста в контейнер не попадает.

Локально без Docker, только с Redis:

```bash
uv sync
uv run uvicorn app.api:app --reload
uv run celery -A app.celery_app worker -l info --pool threads   # на macOS prefork+spawn не работает
uv run celery -A app.celery_app beat -l info
uv run pytest
```

## API

| Метод | Путь | |
|---|---|---|
| `POST` | `/tasks` | Произвольная задача (`prompt`, опционально `repo_url`, `verify_command`, `model`, `effort`, `allowed_tools`, `max_budget_usd`, …) |
| `POST` | `/issues/run` | `{repo, number, verify_command?, prompt?}`: работа над issue от начала до PR |
| `GET` | `/tasks/{id}` | Статус, результат, стоимость, `session_id`, `pr_url` |
| `GET` | `/tasks/{id}/stream` | SSE: история и живые события до завершения задачи, поддерживает `Last-Event-ID` |
| `GET` | `/tasks/{id}/events?after=` | Те же события страницами |
| `POST` | `/tasks/{id}/cancel` | Отмена: задача из очереди снимается сразу, работающая останавливается в течение примерно 2 секунд |
| `GET` | `/tasks?status=` | Список задач |

Follow-up: `POST /tasks {"prompt": "...", "parent_task_id": "<id>"}` продолжает ту же Claude-сессию в том же workspace. Для issue-задачи он пушит в ту же ветку, и PR обновляется.

```bash
curl -X POST localhost:8000/issues/run -H "Authorization: Bearer $HARNESS_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"repo": "acme/app", "number": 42, "verify_command": "make test"}'
```

## Как работает issue-флоу

1. **Триггер.** Задачу ставит `POST /issues/run` или beat, который раз в `HARNESS_ISSUE_POLL_SECONDS` опрашивает `HARNESS_ISSUE_REPOS` и забирает issues с меткой `agent`. Ставить метки могут только участники с правами triage, поэтому любой посторонний не сможет натравить агента на свой текст.
2. **Подготовка.** Harness клонирует репозиторий и создаёт ветку `agent/issue-N`. Метка меняется на `agent:in-progress`.
3. **Агент.** Получает текст issue, комментарии к нему и «definition of done»: тесты, линтеры, документация, коммиты. Делать push агенту запрещено.
4. **Verify.** Harness сам запускает `verify_command`. Если команда упала, сессия возобновляется с её выводом, до `verify_attempts` раз.
5. **Публикация.** Harness пушит ветку (`--force-with-lease`), открывает PR с `Closes #N`, оставляет комментарий в issue и ставит метку `agent:pr-open`. При ошибке ставится `agent:failed` и пишется комментарий с причиной.

Push идёт в ветку с PR, а не в `main`: production-готовность подтверждают CI и ревью.

## Надёжность

- `acks_late` + `prefetch=1`: если воркер упал, сообщение доставляется повторно. Повторная доставка не перезапускает агента: атомарный claim (Lua) переводит такую задачу в `failed: worker lost`.
- Lease с heartbeat и reaper в beat: задачи упавших воркеров помечаются failed примерно через 30 секунд, а не через `visibility_timeout`.
- `session_id` сохраняется сразу при старте. Отменённую, упавшую или зависшую по таймауту задачу можно продолжить через `parent_task_id`.
- Готовые задачи и их события хранятся в Redis `HARNESS_RESULT_TTL_SECONDS` (по умолчанию 7 дней).

## Безопасность

У агента есть Bash, а текст issue может прийти от постороннего человека. Учитывайте следующее:
- воркер работает в контейнере под непривилегированным пользователем;
- GitHub-токен используется только harness'ом: он передаётся через `-c http.extraheader`, а не хранится в `.git/config`, и удаляется из env воркер-процессов;
- `allowed_tools`, `max_turns`, `max_budget_usd` и timeout ограничивают каждую задачу;
- для продакшена стоит изолировать каждую задачу отдельно: свой контейнер или microVM, egress-фильтр. Можно также включить `sandbox` в `ClaudeAgentOptions`.
