COMPOSE = docker compose
RUN_WEB = $(COMPOSE) run --rm web

.PHONY: up down logs migrate seed demo-reset test extract-pages rulebook-suggest rulebook-report warm-web retention-cleanup check-secrets deploy estimate-costs

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

migrate:
	$(RUN_WEB) python -c "from app.db import run_migrations; run_migrations()"

seed:
	$(RUN_WEB) python -m app.seed

demo-reset:
	$(RUN_WEB) sh scripts/demo_reset.sh

test:
	python -m pytest -q

extract-pages:
	python scripts/extract_pages.py

# Curator commands: read only. Need DATABASE_URL (the rulebook must be synced: make seed or demo-reset).
rulebook-suggest:
	$(RUN_WEB) python -m app.rag.curator suggest $(SCHEME)

rulebook-report:
	$(RUN_WEB) python -m app.rag.curator report

# Official web pages: fetch the curated pages (rulebook/sources/<id>/web.yaml) and write snapshots. Run on a machine
# that can reach the sites; the VPS serves the committed snapshots (WEB_FETCH_ENABLED=false).
warm-web:
	python -m app.web.warm --export-only

retention-cleanup:
	$(RUN_WEB) sh scripts/retention_cleanup.sh

check-secrets:
	sh scripts/check_secrets.sh

deploy: check-secrets
	sh scripts/deploy.sh

estimate-costs:
	python scripts/estimate_costs.py
