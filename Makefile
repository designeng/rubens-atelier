# Local development: infra (redis, postgres) in Docker, app processes on the host.
# Defaults of HARNESS_REDIS_URL / HARNESS_DATABASE_URL already point at the compose ports.
RUN = uv run --env-file .env
PORT ?= 8000

.PHONY: help infra api worker beat dev stop test web-build web-dev web-test

help:
	@echo "make dev     redis + postgres, then api, worker and beat together (Ctrl-C stops them)"
	@echo "make infra   start redis and postgres only"
	@echo "make api     uvicorn on :$(PORT) with reload (prints the board URL once started)"
	@echo "make worker  celery worker (threads pool: prefork+spawn does not work on macOS)"
	@echo "make beat    celery beat (orphan reaper, CI poller, issue poller)"
	@echo "make stop    stop redis and postgres"
	@echo "make test    run the test suite"
	@echo "make web-build  build the board (web/, Vue + Vite) into app/static/dist (needs Node 22 and pnpm)"
	@echo "make web-dev    Vite dev server for the board on :5173/board/, API proxied to :$(PORT) (run make api too)"
	@echo "make web-test   type check and unit tests of the board"

infra:
	docker compose up -d --wait redis postgres

# The filter repeats the board address after every (re)start; --use-colors because stdout is a pipe.
api:
	$(RUN) uvicorn app.api:app --reload --port $(PORT) --use-colors 2>&1 | awk '{ print; if ($$0 ~ /Application startup complete/) print "INFO:     Board: http://localhost:$(PORT)/board"; fflush() }'

# Concurrency must be at least HARNESS_MAX_AGENTS, plus room for actions (push, PR).
worker:
	$(RUN) celery -A app.celery_app worker -l info --pool threads --concurrency 6

beat:
	$(RUN) celery -A app.celery_app beat -l info -s /tmp/celerybeat-schedule

# The board is built only when missing; after changing web/ run make web-build (or use make web-dev).
dev: infra app/static/dist/index.html
	$(MAKE) -j3 api worker beat

app/static/dist/index.html:
	$(MAKE) web-build

stop:
	docker compose stop redis postgres

test:
	uv run pytest

web-build:
	cd web && pnpm install --frozen-lockfile && pnpm build

web-dev:
	cd web && pnpm install --frozen-lockfile && pnpm dev

web-test:
	cd web && pnpm install --frozen-lockfile && pnpm typecheck && pnpm test
