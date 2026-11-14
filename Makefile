COMPOSE = docker compose
RUN_WEB = $(COMPOSE) run --rm web

.PHONY: up down logs migrate seed demo-reset test extract-pages retention-cleanup check-secrets deploy estimate-costs

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
	sh scripts/extract_pages.sh

retention-cleanup:
	$(RUN_WEB) sh scripts/retention_cleanup.sh

check-secrets:
	sh scripts/check_secrets.sh

deploy: check-secrets
	sh scripts/deploy.sh

estimate-costs:
	python scripts/estimate_costs.py
