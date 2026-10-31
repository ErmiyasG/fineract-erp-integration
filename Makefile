.PHONY: help build up down restart logs test test-java test-python lint format typecheck init-fineract seed-erp demo-gl-sync simulate-lifecycle simulate-duplicates simulate-concurrency simulate-failures simulate-reconciliation clean

SHELL := /bin/bash
VENV ?= $(PWD)/venv
PYTHON := $(shell if [ -x $(VENV)/bin/python3 ]; then echo $(VENV)/bin/python3; else which python3; fi)
PYTEST := $(shell if [ -x $(VENV)/bin/pytest ]; then echo $(VENV)/bin/pytest; else which pytest; fi)
RUFF := $(shell if [ -x $(VENV)/bin/ruff ]; then echo $(VENV)/bin/ruff; else which ruff; fi)
MYPY := $(shell if [ -x $(VENV)/bin/mypy ]; then echo $(VENV)/bin/mypy; else which mypy; fi)

help: ## Display available commands
	@echo "=================================================================="
	@echo " Fintech Integration Assessment Management Commands"
	@echo "=================================================================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

build: ## Build all docker containers
	docker compose build

up: ## Start all services in the background
	docker compose up -d

down: ## Stop all services and tear down containers
	docker compose down

restart: down up ## Restart all services

logs: ## Tail logs across all services
	docker compose logs -f

logs-integration: ## Tail logs for integration service
	docker compose logs -f integration-service

logs-fineract: ## Tail logs for fineract core
	docker compose logs -f fineract

test-java: ## Run JUnit tests for the Fineract Java customization
	@echo "Running Fineract Java tests..."
	cd fineract-customization && ./gradlew test

test-python: ## Run pytest across Python components
	@echo "Running Integration Service and Mock ERP tests..."
	cd integration-service && $(PYTEST) -v
	cd mock-erp && $(PYTEST) -v

test: test-java test-python ## Run all tests across both languages

lint: ## Lint Python services using ruff
	$(RUFF) check .

format: ## Format Python code
	$(RUFF) format .

typecheck: ## Run mypy type checking
	cd integration-service && $(MYPY) app
	cd mock-erp && $(MYPY) app

init-fineract: ## Run automated configuration for Fineract (office, currency, product, user)
	$(PYTHON) scripts/init_fineract.py

seed-erp: ## Seed chart of accounts into Mock ERP
	$(PYTHON) scripts/seed_erp.py

demo-gl-sync: ## Run Part A GL account sync demonstration (Behaviors 1-4)
	$(PYTHON) scripts/demo_gl_sync.py

simulate-lifecycle: ## Run end-to-end loan lifecycle simulation (Part D)
	$(PYTHON) scripts/simulate_lifecycle.py

simulate-duplicates: ## Run sequential duplicate disbursement simulation (Part I1)
	$(PYTHON) scripts/simulate_duplicates.py

simulate-concurrency: ## Run concurrent disbursements and repayments simulation (Part I2, I3)
	$(PYTHON) scripts/simulate_concurrency.py

simulate-failures: ## Run failure injection and recovery scenarios (Part J)
	$(PYTHON) scripts/simulate_failures.py

simulate-reconciliation: ## Run reconciliation simulation showing all 4 outcomes (Part K)
	$(PYTHON) scripts/simulate_reconciliation.py

clean: ## Stop containers and remove volumes
	docker compose down -v

