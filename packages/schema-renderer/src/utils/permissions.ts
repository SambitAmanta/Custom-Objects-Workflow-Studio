import { FieldPermission, ObjectSchema, UserRole } from "../types";

export function getFieldPermission(
  schema: ObjectSchema,
  fieldName: string,
  role: UserRole = "viewer"
): FieldPermission {
  const roleLower = role.toLowerCase() as UserRole;
  if (roleLower === "admin") {
    return "write";
  }

  const fieldPermissions = schema.permissions?.field_permissions || {};
  const permsForField = fieldPermissions[fieldName] || {};

  if (permsForField[roleLower]) {
    return permsForField[roleLower];
  }

  // Default permissions: Editor has write, Viewer has read
  return roleLower === "editor" ? "write" : "read";
}

export function isFieldVisible(
  schema: ObjectSchema,
  fieldName: string,
  role: UserRole = "viewer"
): boolean {
  return getFieldPermission(schema, fieldName, role) !== "hidden";
}

export function isFieldEditable(
  schema: ObjectSchema,
  fieldName: string,
  role: UserRole = "viewer"
): boolean {
  return getFieldPermission(schema, fieldName, role) === "write";
}
