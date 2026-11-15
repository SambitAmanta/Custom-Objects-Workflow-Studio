import logging

from core.repository import get_mongo_db
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path

logger = logging.getLogger(__name__)


def healthz(request):
    """Liveness probe: verifies process is alive and responding."""
    return JsonResponse({"status": "ok", "service": "forge-api"}, status=200)


def readyz(request):
    """Readiness probe: verifies dependencies (Postgres & MongoDB) are reachable."""
    checks = {"database": "unknown", "mongodb": "unknown"}
    healthy = True

    # Check relational DB
    try:
        connection.ensure_connection()
        checks["database"] = "connected"
    except Exception as e:
        logger.error(f"Readiness check failed for relational DB: {e}")
        checks["database"] = f"error: {str(e)}"
        healthy = False

    # Check MongoDB
    try:
        db = get_mongo_db()
        db.command("ping")
        checks["mongodb"] = "connected"
    except Exception as e:
        logger.error(f"Readiness check failed for MongoDB: {e}")
        checks["mongodb"] = f"error: {str(e)}"
        healthy = False

    status_code = 200 if healthy else 503
    return JsonResponse(
        {"status": "ready" if healthy else "degraded", "checks": checks}, status=status_code
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("api/v1/auth/", include("apps.authentication.urls")),
    path("api/v1/schemas/", include("apps.schemas.urls")),
    path("api/v1/o/", include("apps.records.urls")),
]
