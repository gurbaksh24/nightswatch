.PHONY: help install dev test test-unit test-integration lint format typecheck migrate migrate-new clean docker-up docker-down

help:
	@echo "Common targets:"
	@echo "  make install         - create venv and install deps"
	@echo "  make dev             - run API and worker locally"
	@echo "  make test            - run all tests"
	@echo "  make test-unit       - run unit tests only"
	@echo "  make test-integration- run integration tests (needs Postgres)"
	@echo "  make lint            - ruff + mypy"
	@echo "  make format          - ruff format"
	@echo "  make migrate         - alembic upgrade head"
	@echo "  make migrate-new MSG='...' - generate a new migration"
	@echo "  make docker-up       - start Postgres in docker"
	@echo "  make docker-down     - stop docker services"

install:
	uv venv
	uv pip install -e ".[dev]"

dev:
	@echo "Starting API on :8000 and worker..."
	(uvicorn ai_sre.main:app --reload --host 0.0.0.0 --port 8000 &) ; \
	python -m ai_sre.workers.investigation_worker

test:
	pytest

test-unit:
	pytest -m unit

test-integration:
	pytest -m integration

lint:
	ruff check src tests
	mypy

format:
	ruff format src tests
	ruff check --fix src tests

typecheck:
	mypy

migrate:
	alembic upgrade head

migrate-new:
	@if [ -z "$(MSG)" ]; then echo "Usage: make migrate-new MSG='your message'"; exit 1; fi
	alembic revision --autogenerate -m "$(MSG)"

docker-up:
	docker-compose up -d postgres

docker-down:
	docker-compose down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
