.PHONY: test lint format migrate run up down build load-test

PYTHON = apps/api/.venv/Scripts/python.exe
PYTEST = apps/api/.venv/Scripts/pytest.exe
RUFF = apps/api/.venv/Scripts/ruff.exe

up:
	docker compose -f infra/docker/docker-compose.yml up -d

down:
	docker compose -f infra/docker/docker-compose.yml down -v

test:
	$(PYTEST)

lint:
	$(RUFF) check apps tests

format:
	$(RUFF) format apps tests

build:
	npm run build

load-test:
	k6 run tests/load/k6_records_benchmark.js

migrate:
	$(PYTHON) apps/api/manage.py migrate

run:
	$(PYTHON) apps/api/manage.py runserver 0.0.0.0:8000
