import uuid
from typing import Any

from apps.authentication.models import RoleChoices
from apps.schemas.services import SchemaRegistryService
from apps.workflows.evaluator import RuleEvaluationError, SafeJSONLogicEvaluator
from core.permissions import FieldPermissionManager
from core.repository import TenantScopedRepository
from core.unit_of_work import UnitOfWork
from django.utils import timezone


class TransitionStateMismatchError(Exception):
    """Raised when record state or version does not match expected transition from_state (HTTP 409)."""

    pass


class WorkflowEngineService:
    def __init__(self, repo: TenantScopedRepository):
        self.repo = repo
        self.uow = UnitOfWork(repo)
        self.schema_service = SchemaRegistryService(repo)

    def get_available_transitions(
        self,
        type_slug: str,
        current_state: str,
        role: str = "viewer",
    ) -> list[dict[str, Any]]:
        schema = self.schema_service.get_latest_published(type_slug)
        if not schema:
            return []

        transitions = schema.get("workflow", {}).get("transitions", [])
        role_lower = str(role).lower()
        available = []

        for t in transitions:
            if t.get("from_state") == current_state:
                allowed_roles = [r.lower() for r in t.get("roles", [])]
                if role_lower == RoleChoices.ADMIN or role_lower in allowed_roles:
                    available.append(
                        {
                            "name": t.get("name"),
                            "from_state": t.get("from_state"),
                            "to_state": t.get("to_state"),
                            "has_conditions": bool(t.get("conditions")),
                        }
                    )
        return available

    def execute_transition(
        self,
        type_slug: str,
        record_id: str,
        transition_name: str,
        principal_id: str = "anonymous",
        role: str = "viewer",
    ) -> dict[str, Any]:
        """Atomically executes state machine transition inside multi-document transaction."""
        schema = self.schema_service.get_latest_published(type_slug)
        if not schema:
            raise ValueError(f"No published schema found for type '{type_slug}'")

        transitions = schema.get("workflow", {}).get("transitions", [])
        target_transition = next((t for t in transitions if t.get("name") == transition_name), None)
        if not target_transition:
            raise ValueError(
                f"Transition '{transition_name}' is not defined for type '{type_slug}'"
            )

        # 1. Fetch current record
        record = self.repo.find_one(
            UnitOfWork.RECORDS_COLLECTION,
            {"_id": record_id, "type": type_slug},
        )
        if not record:
            return None

        from_state = target_transition.get("from_state")
        to_state = target_transition.get("to_state")
        current_state = record.get("state")
        current_version = record.get("version", 1)

        # 2. State pre-check
        if current_state != from_state:
            raise TransitionStateMismatchError(
                f"Cannot execute '{transition_name}': record is in state '{current_state}', expected '{from_state}'"
            )

        # 3. Role Guard Check
        role_lower = str(role).lower()
        allowed_roles = [r.lower() for r in target_transition.get("roles", [])]
        if role_lower != RoleChoices.ADMIN and role_lower not in allowed_roles:
            raise PermissionError(
                f"Role '{role}' is not authorized to execute transition '{transition_name}'"
            )

        # 4. JSONLogic Rule Condition Evaluation (Safe AST)
        conditions = target_transition.get("conditions")
        if conditions:
            eval_context = {
                "data": record.get("data", {}),
                "user": {
                    "id": principal_id,
                    "roles": [role_lower],
                },
            }
            try:
                condition_passed = SafeJSONLogicEvaluator.evaluate(conditions, eval_context)
            except RuleEvaluationError as e:
                raise ValueError(f"Workflow condition evaluation failed: {e}") from e

            if not condition_passed:
                raise ValueError(
                    f"Transition condition guard not satisfied for '{transition_name}'"
                )

        now = timezone.now().isoformat()
        new_version = current_version + 1
        event_id = uuid.uuid4().hex

        # 5. Atomic Compare-and-Set Transaction
        def _transaction_work(session):
            updated = self.repo.find_one_and_update(
                UnitOfWork.RECORDS_COLLECTION,
                {
                    "_id": record_id,
                    "type": type_slug,
                    "state": from_state,
                    "version": current_version,
                },
                {
                    "$set": {
                        "state": to_state,
                        "updated_at": now,
                    },
                    "$inc": {"version": 1},
                },
                session=session,
                return_document=True,
            )
            # If state or version mismatch, concurrent winner already transitioned it
            if not updated:
                raise TransitionStateMismatchError(
                    f"Lost state transition race: record '{record_id}' is no longer in state '{from_state}'"
                )

            # In-transaction audit entry
            self.repo.insert_one(
                UnitOfWork.AUDIT_COLLECTION,
                {
                    "event_id": event_id,
                    "tenant_id": self.repo.tenant_id,
                    "type": type_slug,
                    "record_id": record_id,
                    "version": new_version,
                    "action": "transitioned",
                    "transition": transition_name,
                    "from_state": from_state,
                    "to_state": to_state,
                    "principal_id": principal_id,
                    "changed_fields": ["state"],
                    "timestamp": now,
                },
                session=session,
            )

            # Thin Outbox Event
            self.repo.insert_one(
                UnitOfWork.OUTBOX_COLLECTION,
                {
                    "event_id": event_id,
                    "tenant_id": self.repo.tenant_id,
                    "topic": f"records.{type_slug}.transitioned",
                    "action": "transitioned",
                    "record_id": record_id,
                    "version": new_version,
                    "changed_fields": ["state"],
                    "transition": transition_name,
                    "from_state": from_state,
                    "to_state": to_state,
                    "tombstone": None,
                    "status": "pending",
                    "locked_until": None,
                    "created_at": now,
                },
                session=session,
            )
            return updated

        updated_record = self.uow.run_transaction(_transaction_work)
        if updated_record:
            updated_record["data"] = FieldPermissionManager.mask_record_data(
                schema, updated_record.get("data", {}), role
            )
        return updated_record
