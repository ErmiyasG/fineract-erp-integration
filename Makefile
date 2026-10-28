.PHONY: help build up down restart logs test test-java test-python lint format typecheck init-fineract seed-erp simulate-lifecycle simulate-duplicates simulate-concurrency simulate-failures simulate-reconciliation clean

SHELL := /bin/bash

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
	cd integration-service && pytest -v
	cd mock-erp && pytest -v

test: test-java test-python ## Run all tests across both languages

lint: ## Lint Python services using ruff
	cd integration-service && ruff check .
	cd mock-erp && ruff check .

format: ## Format Python code
	cd integration-service && ruff format .
	cd mock-erp && ruff format .

typecheck: ## Run mypy type checking
	cd integration-service && mypy app
	cd mock-erp && mypy app

init-fineract: ## Run automated configuration for Fineract (office, currency, product, user)
	python3 scripts/init_fineract.py

seed-erp: ## Seed chart of accounts into Mock ERP
	python3 scripts/seed_erp.py

simulate-lifecycle: ## Run end-to-end loan lifecycle simulation (Part D)
	python3 scripts/simulate_lifecycle.py

simulate-duplicates: ## Run sequential duplicate disbursement simulation (Part I1)
	python3 scripts/simulate_duplicates.py

simulate-concurrency: ## Run concurrent disbursements and repayments simulation (Part I2, I3)
	python3 scripts/simulate_concurrency.py

simulate-failures: ## Run failure injection and recovery scenarios (Part J)
	python3 scripts/simulate_failures.py

simulate-reconciliation: ## Run reconciliation simulation showing all 4 outcomes (Part K)
	python3 scripts/simulate_reconciliation.py

clean: ## Stop containers and remove volumes
	docker compose down -v

