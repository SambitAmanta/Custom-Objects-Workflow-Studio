import os
import sys
from pathlib import Path

import mongomock
import pytest
from rest_framework.test import APIClient

# Ensure apps/api is on the Python path
APPS_DIR = Path(__file__).resolve().parent.parent / "apps" / "api"
sys.path.insert(0, str(APPS_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from core.repository import set_mongo_client  # noqa: E402


@pytest.fixture(autouse=True)
def mock_mongo():
    """Sets up an isolated in-memory Mongo client for each test."""
    client = mongomock.MongoClient(uuidRepresentation="standard")
    set_mongo_client(client)
    yield client
    set_mongo_client(None)


@pytest.fixture
def api_client():
    return APIClient()
