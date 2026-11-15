from typing import Any

from apps.authentication.models import RoleChoices


class FieldPermissionManager:
    """Evaluates field-level RBAC and masks or strips sensitive fields.

    Supported permission levels per role:
    - write: full read and write access.
    - read: read-only access (write/patch attempts rejected).
    - masked: value replaced with '***MASKED***'.
    - hidden: field is completely removed from all serialization payloads.
    """

    @staticmethod
    def get_field_permission(schema: dict[str, Any], field_name: str, role: str) -> str:
        """Returns the permission level ('write', 'read', 'masked', 'hidden') for a field."""
        # Admin always has full write access unless explicitly configured
        role_lower = str(role).lower()
        if role_lower == RoleChoices.ADMIN:
            return "write"

        field_permissions = schema.get("permissions", {}).get("field_permissions", {})
        perms_for_field = field_permissions.get(field_name, {})

        # Default permissions: Editor has write, Viewer has read
        default_perm = "write" if role_lower == RoleChoices.EDITOR else "read"
        return perms_for_field.get(role_lower, default_perm)

    @classmethod
    def mask_record_data(
        cls, schema: dict[str, Any], data: dict[str, Any], role: str
    ) -> dict[str, Any]:
        """Filters and masks record data dictionary according to role permissions."""
        if not data or not isinstance(data, dict):
            return {}

        masked = {}
        for k, v in data.items():
            # Pass through expanded relation fields if the parent field is readable
            base_field = k[:-9] if k.endswith("_expanded") else k
            perm = cls.get_field_permission(schema, base_field, role)

            if perm == "hidden":
                continue
            elif perm == "masked":
                masked[k] = "***MASKED***"
            else:  # 'write' or 'read'
                masked[k] = v

        return masked

    @classmethod
    def validate_write_permissions(
        cls, schema: dict[str, Any], field_names: list[str], role: str
    ) -> None:
        """Validates that the role has write permission on all specified fields."""
        role_lower = str(role).lower()
        if role_lower == RoleChoices.ADMIN:
            return

        for field_name in field_names:
            perm = cls.get_field_permission(schema, field_name, role)
            if perm != "write":
                raise PermissionError(
                    f"Permission denied: role '{role}' does not have write access to field '{field_name}'"
                )
