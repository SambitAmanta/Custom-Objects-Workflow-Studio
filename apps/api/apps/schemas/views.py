from apps.authentication.models import RoleChoices
from apps.schemas.compat_checker import CompatibilityChecker
from apps.schemas.compiler import compile_object_type_to_json_schema
from apps.schemas.services import SchemaRegistryService
from core.repository import TenantScopedRepository
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


def _get_service(request) -> SchemaRegistryService:
    principal = getattr(request, "auth", None)
    if not principal or not hasattr(principal, "tenant_id"):
        raise PermissionError("Authenticated tenant context is required")
    repo = TenantScopedRepository(tenant_id=principal.tenant_id)
    return SchemaRegistryService(repo)


def _is_admin(request) -> bool:
    principal = getattr(request, "auth", None)
    return bool(principal and principal.role == RoleChoices.ADMIN)


def _serialize_doc(doc: dict | None) -> dict | None:
    if not doc:
        return None
    res = dict(doc)
    if "_id" in res:
        res["_id"] = str(res["_id"])
    return res


@api_view(["GET", "POST"])
def list_or_create_types(request):
    """GET: List all published object types.
    POST: Create or update draft object type (Admin only).
    """
    service = _get_service(request)

    if request.method == "POST":
        if not _is_admin(request):
            return Response(
                {"detail": "Admin role required to define object schemas"},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            draft = service.save_draft(request.data)
            return Response(_serialize_doc(draft), status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # GET: return list of latest published types
    cursor = service.repo.aggregate(
        service.COLLECTION,
        [
            {"$match": {"status": "published"}},
            {"$sort": {"version": -1}},
            {
                "$group": {
                    "_id": "$slug",
                    "doc": {"$first": "$$ROOT"},
                }
            },
        ],
    )
    types = [_serialize_doc(item["doc"]) for item in cursor]
    return Response({"types": types})


@api_view(["GET"])
def get_type_detail(request, slug: str):
    """Retrieve schema definition for an object type.
    Add ?draft=true to retrieve draft if available.
    """
    service = _get_service(request)
    include_draft = request.query_params.get("draft", "").lower() in ("true", "1")

    if include_draft:
        draft = service.get_draft(slug)
        if draft:
            return Response(_serialize_doc(draft))

    latest = service.get_latest_published(slug)
    if not latest:
        # Cross-tenant or non-existent returns 404 to eliminate enumeration
        return Response(
            {"detail": f"Object type '{slug}' not found"}, status=status.HTTP_404_NOT_FOUND
        )

    return Response(_serialize_doc(latest))


@api_view(["GET"])
def get_compiled_json_schema(request, slug: str):
    """Returns the compiled Draft-07 JSON Schema for client-side forms and validation."""
    service = _get_service(request)
    version = request.query_params.get("version")

    if version:
        doc = service.get_version(slug, int(version))
    else:
        doc = service.get_latest_published(slug)

    if not doc:
        return Response(
            {"detail": f"Schema for '{slug}' not found"}, status=status.HTTP_404_NOT_FOUND
        )

    compiled = compile_object_type_to_json_schema(doc)
    return Response(compiled)


@api_view(["GET"])
def list_type_versions(request, slug: str):
    """List all published versions for an object type."""
    service = _get_service(request)
    versions = service.list_versions(slug)
    if not versions:
        return Response(
            {"detail": f"No published versions for '{slug}'"}, status=status.HTTP_404_NOT_FOUND
        )

    return Response(
        {
            "slug": slug,
            "versions": [_serialize_doc(v) for v in versions],
        }
    )


@api_view(["POST"])
def publish_type(request, slug: str):
    """Freezes draft schema into an immutable published version (Admin only)."""
    if not _is_admin(request):
        return Response(
            {"detail": "Admin role required to publish schemas"},
            status=status.HTTP_403_FORBIDDEN,
        )

    service = _get_service(request)
    raw_allow_breaking = request.data.get("allow_breaking", False)
    if isinstance(raw_allow_breaking, str):
        allow_breaking = raw_allow_breaking.lower() in ("true", "1")
    else:
        allow_breaking = bool(raw_allow_breaking)

    try:
        published = service.publish_draft(slug, allow_breaking=allow_breaking)
        return Response(_serialize_doc(published), status=status.HTTP_200_OK)
    except ValueError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
def check_compatibility(request, slug: str):
    """Compares proposed schema with active published version to detect breaking changes."""
    service = _get_service(request)
    latest = service.get_latest_published(slug)
    if not latest:
        return Response(
            {
                "is_compatible": True,
                "breaking_changes": [],
                "non_breaking_changes": ["Initial schema creation (no prior versions)"],
            }
        )

    proposed_schema = request.data
    compat = CompatibilityChecker.check(latest, proposed_schema)
    return Response(compat.model_dump())
