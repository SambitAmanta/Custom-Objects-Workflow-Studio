import hashlib

import jwt
from apps.authentication.models import ApiKey, TenantMembership
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions

User = get_user_model()


class ForgeAuthPrincipal:
    """Represents the authenticated principal with active tenant and role context."""

    def __init__(
        self, user, tenant_id: str, role: str, is_api_key: bool = False, api_key_instance=None
    ):
        self.user = user
        self.tenant_id = str(tenant_id)
        self.role = role
        self.is_api_key = is_api_key
        self.api_key_instance = api_key_instance

    @property
    def id(self) -> str:
        if self.is_api_key:
            return f"apikey_{self.api_key_instance.id}"
        return str(self.user.id)

    @property
    def is_authenticated(self) -> bool:
        return True

    def __str__(self):
        return f"Principal({self.id}, tenant={self.tenant_id}, role={self.role})"


class ForgeJWTAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None

        raw_token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(
                raw_token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("Token has expired")
        except jwt.PyJWTError as e:
            raise exceptions.AuthenticationFailed(f"Invalid token: {str(e)}")

        user_id = payload.get("sub") or payload.get("user_id")
        if not user_id:
            raise exceptions.AuthenticationFailed("Token missing user identifier")

        try:
            user = User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed("User not found or inactive")

        # Determine target tenant:
        # 1. Explicit X-Tenant-Id header
        # 2. Token payload claim 'tenant_id'
        # 3. User's default membership
        target_tenant_id = request.headers.get("X-Tenant-Id") or payload.get("tenant_id")

        membership_query = TenantMembership.objects.filter(user=user, tenant__is_active=True)
        if target_tenant_id:
            membership = (
                membership_query.filter(tenant_id=target_tenant_id).select_related("tenant").first()
            )
            if not membership:
                raise exceptions.PermissionDenied(
                    "User does not have access to the specified tenant"
                )
        else:
            membership = (
                membership_query.filter(is_default=True).select_related("tenant").first()
                or membership_query.select_related("tenant").first()
            )
            if not membership:
                raise exceptions.PermissionDenied("User does not belong to any active tenant")

        principal = ForgeAuthPrincipal(
            user=user,
            tenant_id=str(membership.tenant.id),
            role=membership.role,
            is_api_key=False,
        )
        return (user, principal)


class ApiKeyAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        raw_key = request.headers.get("X-Api-Key")
        if not raw_key:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("ApiKey "):
                raw_key = auth_header.split(" ", 1)[1].strip()

        if not raw_key:
            return None

        # Format: fg_xxxxxxxx_secret
        parts = raw_key.split("_", 2)
        if len(parts) < 3 or parts[0] != "fg":
            raise exceptions.AuthenticationFailed("Invalid API key format")

        prefix = f"fg_{parts[1]}"
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

        try:
            api_key = ApiKey.objects.select_related("tenant").get(
                key_prefix=prefix,
                key_hash=key_hash,
            )
        except ApiKey.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid API key")

        if not api_key.is_valid:
            raise exceptions.AuthenticationFailed(
                "API key is expired, revoked, or tenant is inactive"
            )

        principal = ForgeAuthPrincipal(
            user=AnonymousUser(),
            tenant_id=str(api_key.tenant.id),
            role=api_key.role,
            is_api_key=True,
            api_key_instance=api_key,
        )
        return (AnonymousUser(), principal)
