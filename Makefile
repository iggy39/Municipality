PYTHON ?= $(shell if [ -x .venv/bin/python ]; then printf .venv/bin/python; elif command -v python3 >/dev/null 2>&1; then printf python3; else printf python; fi)
COMPOSE ?= docker compose
DATABASE_URL ?= postgresql+psycopg://municipality:municipality@localhost:5432/municipality
SOURCE_REGISTRY_SEED ?= config/gis/source_registry.seed.yaml

.PHONY: up migrate seed-sources download-gis-poc-sources verify-gis-demo verify-gis-poc test

up:
	$(COMPOSE) up --build -d

migrate:
	$(COMPOSE) exec -T backend alembic upgrade head

seed-sources:
	$(COMPOSE) exec -T backend python -m municipality.gis_source_seed "$(SOURCE_REGISTRY_SEED)"

download-gis-poc-sources:
	$(PYTHON) scripts/download_gis_poc_sources.py

verify-gis-demo:
	DATABASE_URL="$(DATABASE_URL)" $(PYTHON) scripts/verify_gis_demo.py

verify-gis-poc:
	DATABASE_URL="$(DATABASE_URL)" $(PYTHON) scripts/verify_gis_demo.py --skip-boundaries --strict

test:
	$(PYTHON) -m pytest
