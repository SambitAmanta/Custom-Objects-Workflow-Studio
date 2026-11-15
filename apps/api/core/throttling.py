from rest_framework.throttling import SimpleRateThrottle


class TenantRateThrottle(SimpleRateThrottle):
    """Throttles requests per tenant across all users/keys within that organization."""

    scope = "tenant"

    def get_cache_key(self, request, view):
        principal = getattr(request, "auth", None)
        if principal and hasattr(principal, "tenant_id") and principal.tenant_id:
            return f"throttle_tenant_{principal.tenant_id}"
        # Fall back to client IP for unauthenticated requests
        return self.get_ident(request)
