import React, { useState, useEffect } from "react";
import { ObjectSchema, UserRole } from "../types";
import { getFieldPermission, isFieldVisible, isFieldEditable } from "../utils/permissions";
import { validateRecordData } from "../utils/validation";

export interface SchemaFormProps {
  schema: ObjectSchema;
  initialData?: Record<string, any>;
  role?: UserRole;
  onSubmit: (data: Record<string, any>) => void | Promise<void>;
  onCancel?: () => void;
  isSubmitting?: boolean;
}

export const SchemaForm: React.FC<SchemaFormProps> = ({
  schema,
  initialData = {},
  role = "viewer",
  onSubmit,
  onCancel,
  isSubmitting = false,
}) => {
  const [formData, setFormData] = useState<Record<string, any>>(initialData);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    // Populate defaults
    const defaults: Record<string, any> = { ...initialData };
    for (const field of schema.fields || []) {
      if (defaults[field.name] === undefined && field.default !== undefined) {
        defaults[field.name] = field.default;
      }
    }
    setFormData(defaults);
  }, [schema, initialData]);

  const handleChange = (fieldName: string, value: any) => {
    setFormData((prev) => ({ ...prev, [fieldName]: value }));
    if (errors[fieldName]) {
      setErrors((prev) => {
        const next = { ...prev };
        delete next[fieldName];
        return next;
      });
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const validationErrors = validateRecordData(schema.fields, formData);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }
    onSubmit(formData);
  };

  const visibleFields = (schema.fields || []).filter((f) =>
    isFieldVisible(schema, f.name, role)
  );

  return (
    <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      {visibleFields.map((field) => {
        const perm = getFieldPermission(schema, field.name, role);
        const editable = isFieldEditable(schema, field.name, role);
        const isMasked = perm === "masked";
        const error = errors[field.name];
        const val = formData[field.name];

        return (
          <div key={field.name} style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
            <label style={{ fontWeight: 600, fontSize: "0.875rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span>{field.label || field.name}</span>
              {field.required && <span style={{ color: "#ef4444" }}>*</span>}
              {isMasked && (
                <span
                  style={{
                    fontSize: "0.7rem",
                    backgroundColor: "#fef3c7",
                    color: "#b45309",
                    padding: "0.1rem 0.4rem",
                    borderRadius: "4px",
                  }}
                >
                  Masked
                </span>
              )}
              {!editable && !isMasked && (
                <span
                  style={{
                    fontSize: "0.7rem",
                    backgroundColor: "#f3f4f6",
                    color: "#4b5563",
                    padding: "0.1rem 0.4rem",
                    borderRadius: "4px",
                  }}
                >
                  Read-Only
                </span>
              )}
            </label>

            {/* Input renderer based on type */}
            {field.type === "boolean" ? (
              <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: editable ? "pointer" : "default" }}>
                <input
                  type="checkbox"
                  checked={Boolean(val)}
                  disabled={!editable || isMasked}
                  onChange={(e) => handleChange(field.name, e.target.checked)}
                />
                <span style={{ fontSize: "0.875rem", color: "#4b5563" }}>
                  {field.ui_hints?.help_text || (val ? "Yes" : "No")}
                </span>
              </label>
            ) : field.type === "enum" ? (
              <select
                value={isMasked ? "" : (val ?? "")}
                disabled={!editable || isMasked}
                onChange={(e) => handleChange(field.name, e.target.value)}
                style={{
                  padding: "0.5rem",
                  borderRadius: "4px",
                  border: error ? "1px solid #ef4444" : "1px solid #d1d5db",
                  backgroundColor: !editable || isMasked ? "#f9fafb" : "#ffffff",
                }}
              >
                <option value="">-- Select {field.label || field.name} --</option>
                {(field.options || field.validation_rules?.options || []).map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            ) : field.type === "list" ? (
              <input
                type="text"
                value={
                  isMasked
                    ? "***MASKED***"
                    : Array.isArray(val)
                    ? val.join(", ")
                    : (val ?? "")
                }
                disabled={!editable || isMasked}
                placeholder={field.ui_hints?.placeholder || "Comma-separated values..."}
                onChange={(e) =>
                  handleChange(
                    field.name,
                    e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                  )
                }
                style={{
                  padding: "0.5rem",
                  borderRadius: "4px",
                  border: error ? "1px solid #ef4444" : "1px solid #d1d5db",
                  backgroundColor: !editable || isMasked ? "#f9fafb" : "#ffffff",
                }}
              />
            ) : (
              <input
                type={
                  field.type === "number"
                    ? "number"
                    : field.type === "date"
                    ? "datetime-local"
                    : "text"
                }
                value={isMasked ? "***MASKED***" : (val ?? "")}
                disabled={!editable || isMasked}
                placeholder={
                  isMasked
                    ? "***MASKED***"
                    : field.ui_hints?.placeholder || (field.type === "reference" ? `Reference to ${field.target_type}` : "")
                }
                onChange={(e) =>
                  handleChange(
                    field.name,
                    field.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value
                  )
                }
                style={{
                  padding: "0.5rem",
                  borderRadius: "4px",
                  border: error ? "1px solid #ef4444" : "1px solid #d1d5db",
                  backgroundColor: !editable || isMasked ? "#f9fafb" : "#ffffff",
                }}
              />
            )}

            {field.ui_hints?.help_text && (
              <span style={{ fontSize: "0.75rem", color: "#6b7280" }}>
                {field.ui_hints.help_text}
              </span>
            )}
            {error && <span style={{ fontSize: "0.75rem", color: "#ef4444" }}>{error}</span>}
          </div>
        );
      })}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem", marginTop: "1rem" }}>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            style={{
              padding: "0.5rem 1rem",
              borderRadius: "4px",
              border: "1px solid #d1d5db",
              background: "#ffffff",
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
        )}
        <button
          type="submit"
          disabled={isSubmitting || role === "viewer"}
          style={{
            padding: "0.5rem 1.25rem",
            borderRadius: "4px",
            border: "none",
            backgroundColor: role === "viewer" ? "#9ca3af" : "#2563eb",
            color: "#ffffff",
            fontWeight: 500,
            cursor: role === "viewer" || isSubmitting ? "not-allowed" : "pointer",
          }}
        >
          {isSubmitting ? "Saving..." : "Save Record"}
        </button>
      </div>
    </form>
  );
};

