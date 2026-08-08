.DEFAULT_GOAL := help
.PHONY: help install dev test test-cov lint format typecheck check migrate migration downgrade seed backtest analyze scan worker docker-up docker-down docker-logs docker-rebuild clean

PYTHON ?= python
VENV   := .venv
BIN    := $(VENV)/bin
ifeq ($(OS),Windows_NT)
	BIN := $(VENV)/Scripts
endif

help: ## Verfuegbare Befehle anzeigen
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Virtuelles Environment anlegen und Abhaengigkeiten installieren
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e ".[dev]"
	$(BIN)/pre-commit install

dev: ## API mit Auto-Reload starten
	$(BIN)/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

worker: ## Telegram-Bot und Scheduler starten
	$(BIN)/python -m app.cli worker

test: ## Tests ausfuehren
	$(BIN)/pytest

test-cov: ## Tests mit Abdeckungsbericht
	$(BIN)/pytest --cov=app --cov-report=term-missing --cov-report=html

lint: ## Ruff-Pruefung
	$(BIN)/ruff check app tests scripts
	$(BIN)/ruff format --check app tests scripts

format: ## Code formatieren und Autofixes anwenden
	$(BIN)/ruff format app tests scripts
	$(BIN)/ruff check --fix app tests scripts

typecheck: ## mypy ausfuehren
	$(BIN)/mypy app

check: lint typecheck test ## Lint, Typen und Tests in einem Schritt

migrate: ## Migrationen auf den neuesten Stand bringen
	$(BIN)/alembic upgrade head

migration: ## Neue Migration erzeugen: make migration m="beschreibung"
	@if [ -z "$(m)" ]; then echo "Bitte m=\"beschreibung\" angeben."; exit 1; fi
	$(BIN)/alembic revision --autogenerate -m "$(m)"

downgrade: ## Letzte Migration zuruecknehmen
	$(BIN)/alembic downgrade -1

seed: ## Grunddaten anlegen (Symbole, Strategie, Gewichtung)
	$(BIN)/python -m app.cli seed

analyze: ## Analyse ausfuehren: make analyze s=BTCUSDT
	$(BIN)/python -m app.cli analyze $(or $(s),BTCUSDT)

scan: ## Marktscan ohne Versand ausfuehren
	$(BIN)/python -m app.cli scan

backtest: ## DB-Backtest: make backtest s=BTCUSDT tf=1h from=2026-02-01 to=2026-07-31
	$(BIN)/python -m app.cli backtest \
		--symbol $(or $(s),BTCUSDT) \
		--timeframe $(or $(tf),1h) \
		--start $(or $(from),2026-02-01) \
		--end $(or $(to),2026-07-31) \
		--prefer-db \
		--no-persist

docker-up: ## Alle Container bauen und starten
	docker compose up --build -d

docker-down: ## Container stoppen (Volumes bleiben erhalten)
	docker compose down

docker-logs: ## Logs verfolgen
	docker compose logs -f app worker

docker-rebuild: ## Container ohne Cache neu bauen
	docker compose build --no-cache

clean: ## Caches und Artefakte entfernen
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
