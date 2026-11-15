import re

from apps.records.services import (
    OptimisticLockError,
    RecordService,
    UniqueConstraintError,
)
from core.idempotency import IdempotencyManager, IdempotencyPayloadMismatchError
from core.repository import TenantScopedRepository
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


def _get_service(request) -> RecordService:
    principal = getattr(request, "auth", None)
    if not principal or not hasattr(principal, "tenant_id"):
        raise PermissionError("Authenticated tenant context is required")
    repo = TenantScopedRepository(tenant_id=principal.tenant_id)
    return RecordService(repo)


def _get_principal_info(request) -> tuple[str, str]:
    principal = getattr(request, "auth", None)
    principal_id = getattr(principal, "id", "anonymous")
    role = getattr(principal, "role", "viewer")
    return principal_id, role


def _get_principal_info(request) -> tuple[str, str]:
    principal = getattr(request, "auth", None)
    principal_id = getattr(principal, "id", "anonymous")
    role = getattr(principal, "role", "viewer")
    return principal_id, role


def _parse_if_match_header(request) -> int:
    header = request.headers.get("If-Match")
    if not header:
        raise KeyError("If-Match header is required for updates")

    match = re.search(r"(\d+)", header)
    if not match:
        raise ValueError('Invalid If-Match format. Expected integer version e.g. W/"1"')
    return int(match.group(1))


def _serialize_record(record: dict | None) -> dict | None:
    if not record:
        return None
    res = dict(record)
    if "_id" in res:
        res["id"] = str(res.pop("_id"))
    return res


@api_view(["GET", "POST"])
def list_or_create_records(request, type_slug: str):
    """GET /api/v1/o/{type}/: Filter, sort, and paginate records with field masking.
    POST /api/v1/o/{type}/: Create record with JSON Schema validation and atomic Idempotency-Key.
    """
    service = _get_service(request)
    principal_id, role = _get_principal_info(request)

    if request.method == "POST":
        idempotency_key = request.headers.get("Idempotency-Key")
        request_hash = (
            IdempotencyManager.compute_request_hash(request.method, request.path, request.data)
            if idempotency_key
            else None
        )

        try:
            record, is_replayed = service.create_record(
                type_slug=type_slug,
                data=request.data,
                principal_id=principal_id,
                role=role,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            response = Response(_serialize_record(record), status=status.HTTP_201_CREATED)
            response["ETag"] = f'W/"{record.get("version", 1)}"'
            if is_replayed:
                response["X-Idempotent-Replay"] = "true"
            return response
        except IdempotencyPayloadMismatchError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except PermissionError as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except UniqueConstraintError as e:
            return Response(
                {"detail": str(e), "field": e.field_name}, status=status.HTTP_409_CONFLICT
            )

    # GET: List records
    filters = {k: v for k, v in request.query_params.items() if k.startswith("data.")}
    sort_param = request.query_params.get("sort", "_id")
    sort_dir = -1 if sort_param.startswith("-") else 1
    sort_field = sort_param.lstrip("+-")
    cursor = request.query_params.get("cursor")
    limit = min(int(request.query_params.get("limit", 50)), 100)

    try:
        result = service.list_records(
            type_slug=type_slug,
            filters=filters,
            sort_field=sort_field,
            sort_dir=sort_dir,
            cursor=cursor,
            limit=limit,
            role=role,
        )
        return Response(
            {
                "type": type_slug,
                "records": [_serialize_record(r) for r in result["records"]],
                "next_cursor": result["next_cursor"],
                "has_next": result["has_next"],
            }
        )
    except ValueError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "PATCH", "DELETE"])
def record_detail(request, type_slug: str, record_id: str):
    """GET /api/v1/o/{type}/{id}/: Retrieve record with ETag and field masking.
    PATCH /api/v1/o/{type}/{id}/: Partial update with optimistic concurrency (If-Match) and field RBAC.
    DELETE /api/v1/o/{type}/{id}/: Soft-delete record and free unique keys.
    """
    service = _get_service(request)
    principal_id, role = _get_principal_info(request)

    if request.method == "GET":
        expand_param = request.query_params.get("expand")
        expand_list = [f.strip() for f in expand_param.split(",")] if expand_param else None

        record = service.get_record(
            type_slug=type_slug, record_id=record_id, role=role, expand=expand_list
        )
        if not record:
            return Response(
                {"detail": f"Record '{record_id}' not found"}, status=status.HTTP_404_NOT_FOUND
            )

        response = Response(_serialize_record(record))
        response["ETag"] = f'W/"{record.get("version", 1)}"'
        return response

    elif request.method == "PATCH":
        # 1. Require If-Match header -> 428 Precondition Required
        try:
            if_match_version = _parse_if_match_header(request)
        except KeyError:
            return Response(
                {"detail": "If-Match header is required for updates (RFC 6585)"},
                status=status.HTTP_428_PRECONDITION_REQUIRED,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Update with Optimistic Concurrency Control and Field-Level RBAC
        try:
            updated = service.update_record(
                type_slug=type_slug,
                record_id=record_id,
                patch_data=request.data,
                if_match_version=if_match_version,
                principal_id=principal_id,
                role=role,
            )
            if not updated:
                return Response(
                    {"detail": f"Record '{record_id}' not found"}, status=status.HTTP_404_NOT_FOUND
                )

            response = Response(_serialize_record(updated), status=status.HTTP_200_OK)
            response["ETag"] = f'W/"{updated.get("version", 1)}"'
            return response
        except PermissionError as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except OptimisticLockError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_412_PRECONDITION_FAILED,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except UniqueConstraintError as e:
            return Response(
                {"detail": str(e), "field": e.field_name}, status=status.HTTP_409_CONFLICT
            )

    elif request.method == "DELETE":
        try:
            if_match_version = (
                _parse_if_match_header(request) if "If-Match" in request.headers else None
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            deleted = service.delete_record(
                type_slug=type_slug,
                record_id=record_id,
                if_match_version=if_match_version,
                principal_id=principal_id,
            )
            if not deleted:
                return Response(
                    {"detail": f"Record '{record_id}' not found"}, status=status.HTTP_404_NOT_FOUND
                )

            return Response(status=status.HTTP_204_NO_CONTENT)
        except OptimisticLockError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_412_PRECONDITION_FAILED,
            )
