# Local development: infra (redis, postgres) in Docker, app processes on the host.
# Defaults of HARNESS_REDIS_URL / HARNESS_DATABASE_URL already point at the compose ports.
RUN = uv run --env-file .env
PORT ?= 8000

.PHONY: help sync infra migrate revision api worker beat dev stop test lint web-build web-dev web-test

help:
	@echo "make sync   git pull --rebase, then git push; stops and lists the files on conflict"
	@echo "make dev     redis + postgres, then api, worker and beat together (Ctrl-C stops them)"
	@echo "make infra   start redis and postgres only"
	@echo "make migrate bring the database schema to the latest migration (make dev runs it)"
	@echo "make revision m=\"...\"  autogenerate a migration from the models in app/db.py (review it!)"
	@echo "make api     uvicorn on :$(PORT) with reload (prints the board URL once started)"
	@echo "make worker  celery worker (threads pool: prefork+spawn does not work on macOS)"
	@echo "make beat    celery beat (orphan reaper, CI poller, issue poller)"
	@echo "make stop    stop redis and postgres"
	@echo "make test    run the test suite"
	@echo "make lint    ruff check and pyright (config in pyproject.toml)"
	@echo "make web-build  build the board (web/, Vue + Vite) into app/static/dist (needs Node 22 and pnpm)"
	@echo "make web-dev    Vite dev server for the board on :5173/board/, API proxied to :$(PORT) (run make api too)"
	@echo "make web-test   type check and unit tests of the board"

# Equalise local and remote: rebase onto origin, then push. A conflict stops the run and is reported explicitly.
sync:
	@if ! git pull --rebase --autostash; then \
		files=$$(git diff --name-only --diff-filter=U); \
		echo ""; \
		if [ -n "$$files" ]; then \
			echo "!!! SYNC CONFLICT: rebase onto origin stopped"; \
			echo "Conflicting files:"; echo "$$files" | sed 's/^/  - /'; \
			echo "Resolve them, then: git add <files> && git rebase --continue && make sync"; \
			echo "Or give up with:    git rebase --abort"; \
		else \
			echo "!!! SYNC FAILED: git pull did not complete (no file conflicts, see the error above)"; \
		fi; \
		exit 1; \
	fi
	@git push
	@echo "in sync: $$(git rev-parse --abbrev-ref HEAD) @ $$(git rev-parse --short HEAD)"

infra:
	docker compose up -d --wait redis postgres

# alembic upgrade head; a database from before Alembic is stamped at the initial revision first.
migrate:
	$(RUN) python -m app.migrate

# Compares app/db.py with the migrated database: run make migrate first, then edit the draft.
revision:
	@test -n "$(m)" || { echo 'usage: make revision m="add foo to bar"'; exit 1; }
	$(RUN) alembic revision --autogenerate -m "$(m)"

# Reload watches app/ only: agents edit .py files under ./workspaces, which would restart the API
# (and cut every board stream) on each save.
# The filter repeats the board address after every (re)start; --use-colors because stdout is a pipe.
api:
	$(RUN) uvicorn app.api:app --reload --reload-dir app --port $(PORT) --use-colors 2>&1 | awk '{ print; if ($$0 ~ /Application startup complete/) print "INFO:     Board: http://localhost:$(PORT)/board"; fflush() }'

# Concurrency must be at least HARNESS_MAX_AGENTS, plus room for actions (push, PR).
worker:
	$(RUN) celery -A app.celery_app worker -l info --pool threads --concurrency 6

beat:
	$(RUN) celery -A app.celery_app beat -l info -s /tmp/celerybeat-schedule

# The board is rebuilt on every start; for frontend work use make web-dev instead.
dev: infra migrate web-build
	$(MAKE) -j3 api worker beat

stop:
	docker compose stop redis postgres

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run pyright

web-build:
	cd web && pnpm install --frozen-lockfile && pnpm build

web-dev:
	cd web && pnpm install --frozen-lockfile && pnpm dev

web-test:
	cd web && pnpm install --frozen-lockfile && pnpm typecheck && pnpm test
