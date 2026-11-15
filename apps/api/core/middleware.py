import json
import logging
import time
import uuid

from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger("forge.structured")


class RequestIDMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.request_id = request_id

    def process_response(self, request, response):
        if hasattr(request, "request_id"):
            response["X-Request-ID"] = request.request_id
        return response


class TenantContextMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.tenant_id = None
        request.principal_id = None
        # Note: DRF authentication runs during view dispatch, but if request.auth is already present:
        principal = getattr(request, "auth", None)
        if principal and hasattr(principal, "tenant_id"):
            request.tenant_id = principal.tenant_id
            request.principal_id = principal.id

    def process_view(self, request, view_func, view_args, view_kwargs):
        # In DRF, view dispatch performs authentication; update context if available
        principal = getattr(request, "auth", None)
        if principal and hasattr(principal, "tenant_id"):
            request.tenant_id = principal.tenant_id
            request.principal_id = principal.id
        return None


class StructuredLoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request._start_time = time.monotonic()

    def process_response(self, request, response):
        duration_ms = round(
            (time.monotonic() - getattr(request, "_start_time", time.monotonic())) * 1000, 2
        )
        request_id = getattr(request, "request_id", "none")
        principal = getattr(request, "auth", None)
        tenant_id = getattr(principal, "tenant_id", getattr(request, "tenant_id", "none"))
        principal_id = getattr(principal, "id", getattr(request, "principal_id", "anonymous"))

        log_data = {
            "type": "http_access",
            "request_id": request_id,
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "method": request.method,
            "path": request.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }
        # In production this prints standard JSON line
        logger.info(json.dumps(log_data))
        return response
