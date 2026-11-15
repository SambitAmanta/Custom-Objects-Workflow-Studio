.PHONY: test test-fe test-all lint format migrate run up down build load-test demo workers

PYTHON = apps/api/.venv/Scripts/python.exe
PYTEST = apps/api/.venv/Scripts/pytest.exe
RUFF = apps/api/.venv/Scripts/ruff.exe

up:
	docker compose -f infra/docker/docker-compose.yml up -d

down:
	docker compose -f infra/docker/docker-compose.yml down -v

test:
	$(PYTEST)

test-fe:
	npm test --workspace=@forge/schema-renderer

test-all: test test-fe

lint:
	$(RUFF) check apps tests scripts

format:
	$(RUFF) format apps tests scripts

build:
	npm run build

load-test:
	k6 run tests/load/k6_records_benchmark.js

migrate:
	$(PYTHON) apps/api/manage.py migrate

seed:
	$(PYTHON) scripts/seed_demo.py

demo: migrate seed
	@echo "Demo environment seeded successfully."

workers:
	$(PYTHON) apps/workers/run_worker.py --service all

run:
	$(PYTHON) apps/api/manage.py runserver 0.0.0.0:8000
