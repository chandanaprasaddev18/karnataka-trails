# Single entry point for every operation. `make` with no target lists them.
#
# COMPOSE_PROJECT_NAME isolates our containers, networks and volumes from the
# other Docker work on this machine (legalrag-*).

export COMPOSE_PROJECT_NAME := tripplan

# An unrelated venv may be active in the shell (e.g. legal-rag's). uv ignores it
# anyway; unexporting keeps it from warning on every single command.
unexport VIRTUAL_ENV

SHELL := /bin/bash
COMPOSE := docker compose
UV := uv --project api

# Read .env if present so the psql targets know the port.
ifneq (,$(wildcard .env))
include .env
export
endif

POSTGRES_HOST_PORT ?= 5434
POSTGRES_USER ?= tripplan
POSTGRES_DB ?= tripplan

# Defaults for `make plan`. Override on the command line:
#   make plan INTERESTS=trekking DAYS=2
INTERESTS ?= trekking,spiritual
DAYS ?= 3
PEOPLE ?= 4
BUDGET ?= 3
ORIGIN ?= Bengaluru

.DEFAULT_GOAL := help
.PHONY: help env install install-api up up-llm down down-v ps logs wait migrate db-info \
        config-show psql seed seed-taxonomy seed-pois publish fetch-photos plan api worker \
        test test-unit lint fmt typecheck check check-all clean \
        web-install web-dev web-check web-build

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- setup -----------------------------------------------------------------

env: ## Create .env from .env.example if missing
	@test -f .env && echo ".env already exists" || (cp .env.example .env && echo "created .env")

install: ## Create the venv and install deps (Python 3.12)
	$(UV) sync --python 3.12 --group dev
	@echo "installed. run 'make up && make migrate'"

install-api: ## Add the FastAPI/uvicorn extra (step 7)
	$(UV) sync --python 3.12 --group dev --extra api

# --- docker ----------------------------------------------------------------

up: ## Start Postgres+pgvector and wait until healthy
	$(COMPOSE) up -d db
	@$(MAKE) --no-print-directory wait

up-llm: ## Also start Ollama (step 6, offline dev only)
	$(COMPOSE) --profile llm up -d ollama

wait: ## Block until Postgres reports healthy
	@printf "waiting for postgres"
	@for i in $$(seq 1 60); do \
	  status=$$(docker inspect -f '{{.State.Health.Status}}' tripplan-db 2>/dev/null || echo missing); \
	  if [ "$$status" = "healthy" ]; then echo " ok"; exit 0; fi; \
	  printf "."; sleep 1; \
	done; echo " TIMEOUT"; docker logs --tail 30 tripplan-db; exit 1

down: ## Stop containers (data volumes preserved)
	$(COMPOSE) --profile llm down

down-v: ## Stop containers AND delete volumes (destroys all data)
	$(COMPOSE) --profile llm down -v

ps: ## Show this project's containers
	$(COMPOSE) --profile llm ps

logs: ## Tail Postgres logs
	$(COMPOSE) logs -f db

# --- database --------------------------------------------------------------

migrate: ## Apply pending migrations
	$(UV) run tripplan migrate

db-info: ## Show DB versions and row counts
	$(UV) run tripplan db-info

config-show: ## Print effective config (secrets masked)
	$(UV) run tripplan config-show

psql: ## Open a psql shell inside the container
	$(COMPOSE) exec db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

# --- seed data -------------------------------------------------------------

seed: seed-taxonomy seed-pois ## Load all seed data (idempotent)

seed-taxonomy: ## Load interest tags and regions
	$(UV) run tripplan seed-taxonomy

seed-pois: ## Load POIs and guides (loads as status=draft)
	$(UV) run tripplan seed-pois

publish: ## Promote fact-checked POIs to status=published (see step 4 gate)
	$(UV) run tripplan publish

fetch-photos: ## Source CC photos from Wikimedia Commons into web/public/photos
	$(UV) run tripplan fetch-photos $(if $(OVERWRITE),--overwrite,)

# --- engine ----------------------------------------------------------------

plan: ## Generate an itinerary on the CLI (INTERESTS= DAYS= PEOPLE= BUDGET= ORIGIN= MONTH=)
	$(UV) run tripplan plan --interests $(INTERESTS) --days $(DAYS) \
	  --people $(PEOPLE) --budget $(BUDGET) --origin $(ORIGIN) \
	  $(if $(MONTH),--month $(MONTH),) $(if $(JSON),--json,)

api: ## Run the FastAPI server on :8000
	$(UV) run uvicorn tripplan.api.app:app --reload --port 8000

worker: ## Run the itinerary job worker
	$(UV) run tripplan worker

# --- frontend --------------------------------------------------------------

web-install: ## Install frontend deps
	cd web && npm install

web-dev: ## Run the Next.js dev server on :3000
	cd web && npm run dev

web-check: ## Type-check the frontend
	cd web && npx next typegen && npx tsc --noEmit

web-build: ## Production build of the frontend
	cd web && npm run build

# --- quality ---------------------------------------------------------------

# The quality tools all read their config from api/pyproject.toml, and pytest
# and mypy only discover it when the project directory is the working directory.
# Run them from root and you get a silently unconfigured run: no asyncio_mode,
# no --strict, unknown markers.
test: ## Run all tests (integration tests skip if the DB is down)
	cd api && uv run pytest

test-unit: ## Run only tests that need no Docker
	cd api && uv run pytest -m "not integration"

lint: ## Lint with ruff
	cd api && uv run ruff check src tests

fmt: ## Auto-format and auto-fix
	cd api && uv run ruff format src tests
	cd api && uv run ruff check --fix src tests

typecheck: ## Type-check with mypy --strict
	cd api && uv run mypy src tests

check: lint typecheck test ## Everything CI runs (backend)

check-all: check web-check ## Backend + frontend (needs `make web-install` first)

clean: ## Remove caches
	rm -rf api/.pytest_cache api/.mypy_cache api/.ruff_cache api/htmlcov api/.coverage
	find api -type d -name __pycache__ -prune -exec rm -rf {} +
