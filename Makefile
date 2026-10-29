# Local development: infra (redis, postgres) in Docker, app processes on the host.
# Defaults of HARNESS_REDIS_URL / HARNESS_DATABASE_URL already point at the compose ports.
RUN = uv run --env-file .env
PORT ?= 8000

.PHONY: help infra api worker beat dev stop test

help:
	@echo "make dev     redis + postgres, then api, worker and beat together (Ctrl-C stops them)"
	@echo "make infra   start redis and postgres only"
	@echo "make api     uvicorn on :$(PORT) with reload (prints the board URL once started)"
	@echo "make worker  celery worker (threads pool: prefork+spawn does not work on macOS)"
	@echo "make beat    celery beat (orphan reaper, CI poller, issue poller)"
	@echo "make stop    stop redis and postgres"
	@echo "make test    run the test suite"

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

dev: infra
	$(MAKE) -j3 api worker beat

stop:
	docker compose stop redis postgres

test:
	uv run pytest
