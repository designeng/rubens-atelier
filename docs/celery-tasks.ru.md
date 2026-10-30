# Celery-задачи

Все задачи объявлены в [app/celery_app.py](../app/celery_app.py).

| Задача | Запуск | Что делает |
|---|---|---|
| `harness.run_agent` | API при создании задачи (`service.py`) | Выполняет одну agent-задачу (`execute_task`): запускает Claude Agent SDK в рабочей директории, ведёт lease, пишет события и итог в Redis. Таймаут: `task_timeout_seconds + 300`. |
| `harness.step` | движок/планировщик, когда шаг получил слот | Один agent- или action-шаг work item'а workflow (`run_step`). Результат (вердикт) отдаёт обратно в движок. Возобновляется с существующего run'а, поэтому повтор безопасен. |
| `harness.exec` | доска: терминал в workspace | Выполняет shell-команду, запущенную из терминала на доске (`app.terminal`); вывод стримится через Redis. |
| `harness.dispatch` | beat, каждые 15 с; также при освобождении слота | Планировщик: переносит готовые шаги из очереди READY в свободные слоты с учётом общего лимита и лимита на проект и отправляет их воркерам (`harness.step`). Подбирает слоты, освобождённые упавшими шагами, и окна лимитов, которые сбросились. |
| `harness.reconcile` | beat, каждые 60 с | Перезапускает «застрявшие» шаги воркера: потерян enqueue или воркер умер после завершения agent-задачи, но до отчёта о вердикте. Не чаще одного раза за `step_stale_seconds` на шаг. |
| `harness.reap_orphans` | beat, каждые `max(lease_ttl_seconds, 30)` с | Помечает `failed` (`worker_lost`) задачи в статусе `running`, у которых истёк lease (воркер убит, OOM, потерян хост). |
| `harness.poll_checks` | beat (`checks_poll_seconds`; при включённых вебхуках — `checks_fallback_poll_seconds`); GitHub-вебхук — с `item_ids` | Проверяет items в check-состояниях (CI, PR и т. п.) и в human-состояниях с `watch`. Выдаёт вердикт, когда исход известен, иначе спросит на следующем проходе. С вебхуками beat остаётся запасным вариантом на случай потерянных доставок и для вердиктов по времени (`no_checks`, `timeout`). |
| `harness.poll_issues` | beat, каждые `issue_poll_seconds`, только если задан `issue_repos` | Берёт открытые GitHub issues с меткой `issue_label` в `issue_repos`, создаёт для каждого задачу и сразу меняет метку, чтобы следующий опрос его не подхватил. |
| `harness.cleanup_workspace` | после мерджа истории | Удаляет workspace одной истории, если она смерджена и всё ещё в успешном состоянии. Возвращает `True`, если удалил. |
| `harness.remove_workspaces` | API (ручное удаление с доски) | Удаляет перечисленные workspaces с тома воркера (API том только читает). `include_failed` разрешает удалять и упавшие. Таймаут 30 мин. |
| `harness.prune_workspaces` | beat, раз в час, только если `workspace_ttl_hours` > 0 | Автоочистка: удаляет завершённые и «осиротевшие» workspaces старше TTL. Таймаут 30 мин. |

## Расписание beat

| Задача | Период |
|---|---|
| `harness.dispatch` | 15 с |
| `harness.reconcile` | 60 с |
| `harness.reap_orphans` | `max(lease_ttl_seconds, 30)` с |
| `harness.poll_checks` | `checks_poll_seconds` (с вебхуками — `checks_fallback_poll_seconds`) |
| `harness.poll_issues` | `issue_poll_seconds` (если задан `issue_repos`) |
| `harness.prune_workspaces` | 3600 с (если задан `workspace_ttl_hours`) |

Остальные задачи (`run_agent`, `step`, `exec`, `cleanup_workspace`, `remove_workspaces`) по расписанию не запускаются.
