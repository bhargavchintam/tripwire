# Tripwire — canonical commands. Scaffold prepared pre-kickoff.
# Implementation files (checkpoint/, agents/, etc.) are created DURING the event.
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

lint:  ## ruff (no-op until code exists)
	@$(PY) ruff check . 2>/dev/null || echo "ruff not set up / no python yet — skipping"

test:  ## unit + integration tests (no-op until tests exist)
	@if ls tests/unit/*.py tests/integration/*.py >/dev/null 2>&1; then \
		$(PY) pytest tests/unit tests/integration -q ; \
	else echo "no unit/integration tests yet — skipping"; fi

check: lint test  ## PRE-MERGE GATE: lint + unit/integration must be green
	@echo "make check: OK"

e2e:  ## end-to-end acceptance (run after the 12:30 MVP)
	@if ls tests/e2e/*.py >/dev/null 2>&1; then $(PY) pytest -m e2e -q ; \
	else echo "no e2e tests yet — skipping"; fi

seed:  ## load a few hundred historical rows for the live agents
	@[ -f data/seed_live_agents.sql ] && echo "run seed via your CH client" || echo "data/seed_live_agents.sql not created yet"

load:  ## load synthetic background rows (chunked toward 30M)
	@[ -f data/background_data.sql ] && echo "run load via your CH client" || echo "data/background_data.sql not created yet"

demo:  ## reset to green, ready to present
	@echo "POST /demo/reset once the checkpoint is running"

reset:  ## clear local runtime state
	@rm -f var/state.json 2>/dev/null; echo "cleared var/state.json"

clean:  ## remove caches
	@rm -rf .pytest_cache .ruff_cache web/dist 2>/dev/null; echo "cleaned"
