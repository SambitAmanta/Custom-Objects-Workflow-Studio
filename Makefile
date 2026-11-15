.PHONY: test lint format migrate run

PYTHON = apps/api/.venv/Scripts/python.exe
PYTEST = apps/api/.venv/Scripts/pytest.exe
RUFF = apps/api/.venv/Scripts/ruff.exe

test:
	$(PYTEST)

lint:
	$(RUFF) check apps tests

format:
	$(RUFF) format apps tests

migrate:
	$(PYTHON) apps/api/manage.py migrate

run:
	$(PYTHON) apps/api/manage.py runserver 0.0.0.0:8000
