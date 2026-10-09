# Tripwire — canonical commands. This Makefile and the empty folder layout were set up
# during pre-event prep (tooling only, no project code); all implementation was written during the event.
# Until a target's code exists, it no-ops with a friendly message instead of failing,
# so `make check` is safe to run from the very first commit.

.DEFAULT_GOAL := help
PY := uv run

.PHONY: help setup up db down dev check lint test e2e seed load demo reset clean

help:  ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup:  ## install python deps (uv sync, incl. dev) and web deps (npm) if present
	uv sync
	@[ -f .env ] || (cp .env.example .env && echo "created .env from .env.example — fill in keys")
	@[ -f web/package.json ] && (cd web && npm install) || echo "web/ not scaffolded yet"

up:  ## start local ClickHouse (docker) and apply the schema
	docker compose up -d clickhouse
	@echo "waiting for ClickHouse..."; for i in $$(seq 1 30); do curl -sf localhost:8123/ping >/dev/null && break; sleep 1; done
	$(MAKE) db

db:  ## apply data/schema.sql + read-only user
	$(PY) python -m tripwire.ch init

down:  ## stop local services
	@[ -f docker-compose.yml ] && docker compose down || true

dev:  ## run checkpoint + detector + web via honcho (needs Procfile)
	@[ -f Procfile ] && $(PY) honcho start || echo "Procfile not created yet (CP0)"

lint:  ## ruff over the whole repo (fails the gate on any lint error)
	$(PY) ruff check .

# The pre-merge gate always tests against LOCAL ClickHouse (Docker or the standalone binary on
# :8123 with the compose credentials), even when .env points at ClickHouse Cloud: fast, and it
# never writes test rows into the shared demo database. Cloud is verified by live rehearsals.
LOCAL_CH := CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8123 CLICKHOUSE_SECURE=0 CLICKHOUSE_USER=default \
	CLICKHOUSE_PASSWORD=tripwire CLICKHOUSE_RO_USER=tripwire_ro CLICKHOUSE_RO_PASSWORD=tripwire_ro_pw

test:  ## unit + integration tests against LOCAL ClickHouse (skips CH tests if it's not running)
	@if ls tests/unit/*.py tests/integration/*.py >/dev/null 2>&1; then \
		$(LOCAL_CH) $(PY) pytest tests/unit tests/integration -q ; \
	else echo "no unit/integration tests yet — skipping"; fi

check: lint test  ## PRE-MERGE GATE: lint + unit/integration must be green
	@echo "make check: OK"

e2e:  ## end-to-end acceptance against LOCAL ClickHouse (run after the MVP)
	@if ls tests/e2e/*.py >/dev/null 2>&1; then $(LOCAL_CH) $(PY) pytest -m e2e -q ; \
	else echo "no e2e tests yet — skipping"; fi

e2e-cloud:  ## the same e2e suite against whatever .env points at (e.g. ClickHouse Cloud)
	$(PY) pytest -m e2e -q

seed:  ## load a few hundred historical rows for the live agents
	$(PY) python -m tripwire.loader seed

ROWS ?= 1000000
load:  ## load synthetic background rows in 5M chunks (make load ROWS=30000000)
	$(PY) python -m tripwire.loader load --rows $(ROWS) --chunk 5000000

demo:  ## reset to green, ready to present
	@echo "POST /demo/reset once the checkpoint is running"

reset:  ## clear local runtime state
	@rm -f var/state.json 2>/dev/null; echo "cleared var/state.json"

clean:  ## remove caches
	@rm -rf .pytest_cache .ruff_cache web/dist 2>/dev/null; echo "cleaned"
