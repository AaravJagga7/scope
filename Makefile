SHELL := /bin/bash
VENV ?= .venv
PYTHON := $(shell if [ -f $(VENV)/bin/python ]; then echo $(VENV)/bin/python; else echo python3; fi)
RUFF := $(shell if [ -f $(VENV)/bin/ruff ]; then echo $(VENV)/bin/ruff; else echo ruff; fi)
PYTEST := $(shell if [ -f $(VENV)/bin/pytest ]; then echo $(VENV)/bin/pytest; else echo pytest; fi)

.PHONY: all up down test lint e2e setup clean store

all: lint test

setup:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment..."; \
		python3.11 -m venv $(VENV) || python3 -m venv $(VENV); \
	fi
	@$(PYTHON) -m pip install --upgrade pip
	@$(PYTHON) -m pip install -e ".[dev]" || $(PYTHON) -m pip install ruff pytest httpx

up:
	@if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		echo "Starting SCOPE-X stack via docker-compose..."; \
		docker compose up -d; \
	else \
		echo "Docker daemon not running; launching local service processes..."; \
		$(PYTHON) scripts/local_dev_server.py start; \
	fi

down:
	@if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		echo "Stopping SCOPE-X stack via docker-compose..."; \
		docker compose down; \
	else \
		echo "Stopping local service processes..."; \
		$(PYTHON) scripts/local_dev_server.py stop; \
	fi

lint:
	@echo "Running linting checks..."
	@if command -v $(RUFF) >/dev/null 2>&1 || [ -x "$(RUFF)" ]; then \
		$(RUFF) check . && $(RUFF) format --check .; \
	else \
		echo "ruff not found, running basic syntax validation..."; \
		$(PYTHON) -m compileall scopex tests services; \
	fi

test:
	@echo "Running test suite..."
	@if command -v $(PYTEST) >/dev/null 2>&1 || [ -x "$(PYTEST)" ]; then \
		$(PYTEST); \
	else \
		$(PYTHON) -m unittest discover -s tests; \
	fi

e2e:
	@echo "Running end-to-end checkout test..."
	@./scripts/e2e_checkout.sh

store:
	@echo "Starting Bazaaro storefront + Constraint Lab on http://127.0.0.1:8010 ..."
	@$(PYTHON) -m storefront --port 8010

clean:
	@rm -rf .pytest_cache .ruff_cache build dist *.egg-info scopex_dev.db .services.pid .storefront
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
