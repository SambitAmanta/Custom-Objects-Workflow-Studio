import datetime

import jwt
from apps.authentication.models import ApiKey, RoleChoices, TenantMembership
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

User = get_user_model()


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def obtain_token(request):
    """Authenticate with email and password to receive a short-lived JWT token."""
    email = request.data.get("email")
    password = request.data.get("password")
    requested_tenant_id = request.data.get("tenant_id")

    if not email or not password:
        return Response(
            {"detail": "Email and password are required"}, status=status.HTTP_400_BAD_REQUEST
        )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({"detail": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

    if not user.check_password(password) or not user.is_active:
        return Response({"detail": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

    memberships = TenantMembership.objects.filter(user=user, tenant__is_active=True).select_related(
        "tenant"
    )
    if not memberships.exists():
        return Response(
            {"detail": "User has no active tenant memberships"}, status=status.HTTP_403_FORBIDDEN
        )

    if requested_tenant_id:
        active_membership = memberships.filter(tenant_id=requested_tenant_id).first()
        if not active_membership:
            return Response(
                {"detail": "User does not belong to specified tenant"},
                status=status.HTTP_403_FORBIDDEN,
            )
    else:
        active_membership = memberships.filter(is_default=True).first() or memberships.first()

    now = datetime.datetime.now(datetime.UTC)
    expires_at = now + datetime.timedelta(seconds=settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS)

    payload = {
        "sub": str(user.id),
        "user_id": str(user.id),
        "email": user.email,
        "tenant_id": str(active_membership.tenant.id),
        "tenant_slug": active_membership.tenant.slug,
        "role": active_membership.role,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    return Response(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS,
            "tenant": {
                "id": str(active_membership.tenant.id),
                "name": active_membership.tenant.name,
                "slug": active_membership.tenant.slug,
                "role": active_membership.role,
            },
        }
    )


@api_view(["GET"])
def list_user_tenants(request):
    """List all tenants the authenticated user belongs to."""
    if not request.user or not request.user.is_authenticated:
        return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

    memberships = TenantMembership.objects.filter(
        user=request.user, tenant__is_active=True
    ).select_related("tenant")
    results = [
        {
            "tenant_id": str(m.tenant.id),
            "tenant_name": m.tenant.name,
            "tenant_slug": m.tenant.slug,
            "role": m.role,
            "is_default": m.is_default,
        }
        for m in memberships
    ]
    return Response({"tenants": results})


@api_view(["POST"])
def create_api_key(request):
    """Create a new machine-to-machine API key for the active tenant (Admin only)."""
    principal = getattr(request, "auth", None)
    if not principal or principal.role != RoleChoices.ADMIN:
        return Response(
            {"detail": "Admin role required to create API keys"}, status=status.HTTP_403_FORBIDDEN
        )

    name = request.data.get("name")
    role = request.data.get("role", RoleChoices.EDITOR)
    if not name:
        return Response({"detail": "Key name is required"}, status=status.HTTP_400_BAD_REQUEST)

    if role not in RoleChoices.values:
        return Response(
            {"detail": f"Invalid role. Choices: {RoleChoices.values}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from apps.authentication.models import Tenant

    tenant = Tenant.objects.get(id=principal.tenant_id)
    api_key, raw_secret = ApiKey.generate(tenant=tenant, name=name, role=role)

    return Response(
        {
            "id": str(api_key.id),
            "name": api_key.name,
            "key_prefix": api_key.key_prefix,
            "role": api_key.role,
            "api_key": raw_secret,  # Shown only once!
            "created_at": api_key.created_at.isoformat(),
        },
        status=status.HTTP_201_CREATED,
    )
