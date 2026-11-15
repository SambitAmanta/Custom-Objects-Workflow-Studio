from typing import Any

from pydantic import BaseModel, Field


class CompatibilityResult(BaseModel):
    is_compatible: bool = True
    breaking_changes: list[str] = Field(default_factory=list)
    non_breaking_changes: list[str] = Field(default_factory=list)


class CompatibilityChecker:
    """Classifies schema evolution changes into breaking vs non-breaking categories.

    Non-breaking changes:
    - Adding an optional field (or field with a default).
    - Adding an enum option.
    - Loosening validation constraints (lowering min, raising max).

    Breaking changes:
    - Removing or renaming an existing field.
    - Changing a field type.
    - Adding a required field without a default.
    - Removing an enum value.
    - Tightening validation constraints (raising min, lowering max, adding pattern).
    """

    @classmethod
    def check(
        cls, current_schema: dict[str, Any], proposed_schema: dict[str, Any]
    ) -> CompatibilityResult:
        result = CompatibilityResult()

        curr_fields: dict[str, dict[str, Any]] = {
            f["name"]: f for f in current_schema.get("fields", []) if "name" in f
        }
        prop_fields: dict[str, dict[str, Any]] = {
            f["name"]: f for f in proposed_schema.get("fields", []) if "name" in f
        }

        # 1. Check for removed fields (BREAKING)
        for name in curr_fields:
            if name not in prop_fields:
                result.breaking_changes.append(f"Field '{name}' was removed")

        # 2. Check added fields
        for name, p_field in prop_fields.items():
            if name not in curr_fields:
                is_required = p_field.get("required", False)
                has_default = p_field.get("default") is not None
                if is_required and not has_default:
                    result.breaking_changes.append(
                        f"Field '{name}' is added as required without a default value"
                    )
                else:
                    result.non_breaking_changes.append(
                        f"Field '{name}' added (optional or with default)"
                    )
            else:
                # 3. Field exists in both: check for type changes, constraint changes
                c_field = curr_fields[name]
                cls._check_field_evolution(name, c_field, p_field, result)

        result.is_compatible = len(result.breaking_changes) == 0
        return result

    @classmethod
    def _check_field_evolution(
        cls,
        field_name: str,
        curr: dict[str, Any],
        prop: dict[str, Any],
        result: CompatibilityResult,
    ) -> None:
        # Type change (BREAKING)
        if curr.get("type") != prop.get("type"):
            result.breaking_changes.append(
                f"Field '{field_name}' type changed from '{curr.get('type')}' to '{prop.get('type')}'"
            )
            return

        # Required flag changed
        if not curr.get("required", False) and prop.get("required", False):
            if prop.get("default") is None:
                result.breaking_changes.append(
                    f"Field '{field_name}' changed from optional to required without a default"
                )
            else:
                result.non_breaking_changes.append(
                    f"Field '{field_name}' changed to required with default value"
                )

        # Enum options evolution
        if curr.get("type") == "enum":
            curr_opts = set(curr.get("options") or [])
            prop_opts = set(prop.get("options") or [])
            removed_opts = curr_opts - prop_opts
            added_opts = prop_opts - curr_opts

            if removed_opts:
                result.breaking_changes.append(
                    f"Field '{field_name}' enum removed options: {sorted(removed_opts)}"
                )
            if added_opts:
                result.non_breaking_changes.append(
                    f"Field '{field_name}' enum added options: {sorted(added_opts)}"
                )

        # Validation rules evolution
        curr_rules = curr.get("validation_rules", {}) or {}
        prop_rules = prop.get("validation_rules", {}) or {}

        # Min length / Minimum: higher is breaking (tightening), lower is non-breaking (loosening)
        for key in ("min_length", "minimum"):
            c_val = curr_rules.get(key)
            p_val = prop_rules.get(key)
            if c_val is not None and p_val is not None:
                if p_val > c_val:
                    result.breaking_changes.append(
                        f"Field '{field_name}' tightened {key} from {c_val} to {p_val}"
                    )
                elif p_val < c_val:
                    result.non_breaking_changes.append(
                        f"Field '{field_name}' loosened {key} from {c_val} to {p_val}"
                    )
            elif c_val is None and p_val is not None:
                result.breaking_changes.append(
                    f"Field '{field_name}' added constraint {key}={p_val}"
                )

        # Max length / Maximum: lower is breaking (tightening), higher is non-breaking (loosening)
        for key in ("max_length", "maximum"):
            c_val = curr_rules.get(key)
            p_val = prop_rules.get(key)
            if c_val is not None and p_val is not None:
                if p_val < c_val:
                    result.breaking_changes.append(
                        f"Field '{field_name}' tightened {key} from {c_val} to {p_val}"
                    )
                elif p_val > c_val:
                    result.non_breaking_changes.append(
                        f"Field '{field_name}' loosened {key} from {c_val} to {p_val}"
                    )
            elif c_val is None and p_val is not None:
                result.breaking_changes.append(
                    f"Field '{field_name}' added constraint {key}={p_val}"
                )

        # Pattern change
        c_pat = curr_rules.get("pattern")
        p_pat = prop_rules.get("pattern")
        if c_pat != p_pat:
            if p_pat and not c_pat:
                result.breaking_changes.append(
                    f"Field '{field_name}' added regex pattern restriction"
                )
            elif not p_pat and c_pat:
                result.non_breaking_changes.append(
                    f"Field '{field_name}' removed regex pattern restriction"
                )
            else:
                result.breaking_changes.append(f"Field '{field_name}' regex pattern changed")
