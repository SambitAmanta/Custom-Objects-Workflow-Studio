from apps.records.views import _get_principal_info, _serialize_record
from apps.workflows.services import TransitionStateMismatchError, WorkflowEngineService
from core.repository import TenantScopedRepository
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


def _get_workflow_service(request) -> WorkflowEngineService:
    principal = getattr(request, "auth", None)
    if not principal or not hasattr(principal, "tenant_id"):
        raise PermissionError("Authenticated tenant context is required")
    repo = TenantScopedRepository(tenant_id=principal.tenant_id)
    return WorkflowEngineService(repo)


@api_view(["GET"])
def list_transitions(request, type_slug: str, record_id: str):
    """GET /api/v1/o/{type}/{id}/transitions/: List transitions valid from current record state for current role."""
    try:
        service = _get_workflow_service(request)
    except PermissionError as e:
        return Response({"detail": str(e)}, status=status.HTTP_401_UNAUTHORIZED)

    _principal_id, role = _get_principal_info(request)

    record = service.repo.find_one(
        "records", {"_id": record_id, "type": type_slug, "tombstone": None}
    )
    if not record:
        return Response(
            {"detail": f"Record '{record_id}' not found"}, status=status.HTTP_404_NOT_FOUND
        )

    transitions = service.get_available_transitions(
        type_slug=type_slug,
        current_state=record.get("state"),
        role=role,
    )
    return Response(
        {
            "record_id": record_id,
            "current_state": record.get("state"),
            "available_transitions": transitions,
        }
    )


@api_view(["POST"])
def execute_transition(request, type_slug: str, record_id: str, transition_name: str):
    """POST /api/v1/o/{type}/{id}/transitions/{transition_name}/: Atomically transition state machine."""
    try:
        service = _get_workflow_service(request)
    except PermissionError as e:
        return Response({"detail": str(e)}, status=status.HTTP_401_UNAUTHORIZED)

    principal_id, role = _get_principal_info(request)

    try:
        updated = service.execute_transition(
            type_slug=type_slug,
            record_id=record_id,
            transition_name=transition_name,
            principal_id=principal_id,
            role=role,
        )
        if not updated:
            return Response(
                {"detail": f"Record '{record_id}' not found"}, status=status.HTTP_404_NOT_FOUND
            )

        res = Response(_serialize_record(updated), status=status.HTTP_200_OK)
        res["ETag"] = f'W/"{updated.get("version", 1)}"'
        return res
    except TransitionStateMismatchError as e:
        return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
    except PermissionError as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValueError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
