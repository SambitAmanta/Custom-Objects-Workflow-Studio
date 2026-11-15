import React, { useState } from "react";
import { FieldDefinition, ObjectSchema } from "@forge/schema-renderer";

interface SchemaEditorProps {
  schema: ObjectSchema;
  onChange: (updated: ObjectSchema) => void;
  onSaveDraft: () => Promise<void>;
  onPublish: (allowBreaking: boolean) => Promise<void>;
  isSaving?: boolean;
}

export const SchemaEditor: React.FC<SchemaEditorProps> = ({
  schema,
  onChange,
  onSaveDraft,
  onPublish,
  isSaving = false,
}) => {
  const [showAddField, setShowAddField] = useState(false);
  const [newField, setNewField] = useState<Partial<FieldDefinition>>({
    name: "",
    label: "",
    type: "text",
    required: false,
    unique: false,
  });

  // Reorder fields using Up / Down buttons
  const moveField = (index: number, direction: "up" | "down") => {
    const fields = [...schema.fields];
    const targetIndex = direction === "up" ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= fields.length) return;

    const temp = fields[index];
    fields[index] = fields[targetIndex];
    fields[targetIndex] = temp;

    onChange({ ...schema, fields });
  };

  const removeField = (index: number) => {
    const fields = schema.fields.filter((_, i) => i !== index);
    onChange({ ...schema, fields });
  };

  const handleAddField = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newField.name) return;

    const formattedName = newField.name.toLowerCase().replace(/[^a-z0-9_]/g, "_");
    const fieldToAdd: FieldDefinition = {
      name: formattedName,
      label: newField.label || newField.name,
      type: newField.type || "text",
      required: Boolean(newField.required),
      unique: Boolean(newField.unique),
      options: newField.type === "enum" ? newField.options || [] : undefined,
      validation_rules: newField.validation_rules,
    };

    onChange({
      ...schema,
      fields: [...schema.fields, fieldToAdd],
    });

    setNewField({
      name: "",
      label: "",
      type: "text",
      required: false,
      unique: false,
    });
    setShowAddField(false);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Schema Header Information */}
      <div
        style={{
          backgroundColor: "#ffffff",
          padding: "1.25rem",
          borderRadius: "8px",
          border: "1px solid #e2e8f0",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: "1rem",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <h2 style={{ fontSize: "1.25rem", fontWeight: 700, color: "#0f172a" }}>
              {schema.name || schema.slug}
            </h2>
            <span
              style={{
                fontSize: "0.75rem",
                padding: "0.2rem 0.5rem",
                borderRadius: "9999px",
                backgroundColor: schema.status === "published" ? "#dcfce7" : "#fef3c7",
                color: schema.status === "published" ? "#166534" : "#92400e",
                fontWeight: 600,
              }}
            >
              {schema.status === "published" ? `v${schema.version || 1} Published` : "Draft"}
            </span>
          </div>
          <span style={{ fontSize: "0.85rem", color: "#64748b" }}>
            Slug: <code style={{ backgroundColor: "#f1f5f9", padding: "0.1rem 0.3rem", borderRadius: "4px" }}>{schema.slug}</code>
          </span>
        </div>

        <div style={{ display: "flex", gap: "0.75rem" }}>
          <button
            type="button"
            onClick={onSaveDraft}
            disabled={isSaving}
            style={{
              padding: "0.5rem 1rem",
              borderRadius: "6px",
              border: "1px solid #cbd5e1",
              backgroundColor: "#ffffff",
              fontSize: "0.875rem",
              fontWeight: 500,
              cursor: isSaving ? "not-allowed" : "pointer",
            }}
          >
            Save Draft
          </button>
          <button
            type="button"
            onClick={() => onPublish(false)}
            disabled={isSaving}
            style={{
              padding: "0.5rem 1.25rem",
              borderRadius: "6px",
              border: "none",
              backgroundColor: "#2563eb",
              color: "#ffffff",
              fontSize: "0.875rem",
              fontWeight: 600,
              cursor: isSaving ? "not-allowed" : "pointer",
            }}
          >
            {isSaving ? "Publishing..." : "Publish Immutable Version"}
          </button>
        </div>
      </div>

      {/* Field Definitions List */}
      <div
        style={{
          backgroundColor: "#ffffff",
          padding: "1.25rem",
          borderRadius: "8px",
          border: "1px solid #e2e8f0",
          display: "flex",
          flexDirection: "column",
          gap: "1rem",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ fontSize: "1rem", fontWeight: 600, color: "#1e293b" }}>
            Field Definitions ({schema.fields.length})
          </h3>
          <button
            type="button"
            onClick={() => setShowAddField(true)}
            style={{
              padding: "0.4rem 0.85rem",
              borderRadius: "6px",
              backgroundColor: "#f1f5f9",
              border: "1px solid #cbd5e1",
              fontSize: "0.8rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            + Add Field
          </button>
        </div>

        {schema.fields.length === 0 ? (
          <div style={{ padding: "2rem", textAlign: "center", color: "#94a3b8", fontSize: "0.9rem" }}>
            No fields defined yet. Click "+ Add Field" to create your first field.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            {schema.fields.map((field, idx) => (
              <div
                key={field.name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "0.75rem 1rem",
                  borderRadius: "6px",
                  border: "1px solid #e2e8f0",
                  backgroundColor: "#f8fafc",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
                  {/* Up / Down Reorder Buttons */}
                  <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                    <button
                      type="button"
                      disabled={idx === 0}
                      onClick={() => moveField(idx, "up")}
                      title="Move Up"
                      style={{
                        padding: "2px 6px",
                        fontSize: "0.65rem",
                        borderRadius: "3px",
                        border: "1px solid #cbd5e1",
                        backgroundColor: idx === 0 ? "#f1f5f9" : "#ffffff",
                        cursor: idx === 0 ? "not-allowed" : "pointer",
                      }}
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      disabled={idx === schema.fields.length - 1}
                      onClick={() => moveField(idx, "down")}
                      title="Move Down"
                      style={{
                        padding: "2px 6px",
                        fontSize: "0.65rem",
                        borderRadius: "3px",
                        border: "1px solid #cbd5e1",
                        backgroundColor: idx === schema.fields.length - 1 ? "#f1f5f9" : "#ffffff",
                        cursor: idx === schema.fields.length - 1 ? "not-allowed" : "pointer",
                      }}
                    >
                      ▼
                    </button>
                  </div>

                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <span style={{ fontWeight: 600, fontSize: "0.9rem", color: "#0f172a" }}>
                        {field.label || field.name}
                      </span>
                      <code style={{ fontSize: "0.75rem", color: "#64748b" }}>({field.name})</code>
                      <span
                        style={{
                          fontSize: "0.7rem",
                          padding: "0.1rem 0.4rem",
                          borderRadius: "4px",
                          backgroundColor: "#e2e8f0",
                          color: "#334155",
                          fontWeight: 500,
                        }}
                      >
                        {field.type}
                      </span>
                      {field.required && (
                        <span style={{ fontSize: "0.7rem", color: "#dc2626", fontWeight: 600 }}>
                          Required
                        </span>
                      )}
                      {field.unique && (
                        <span style={{ fontSize: "0.7rem", color: "#2563eb", fontWeight: 600 }}>
                          Unique
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => removeField(idx)}
                  style={{
                    padding: "0.25rem 0.5rem",
                    borderRadius: "4px",
                    border: "1px solid #fecaca",
                    backgroundColor: "#fff1f2",
                    color: "#e11d48",
                    fontSize: "0.75rem",
                    cursor: "pointer",
                  }}
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add Field Modal / Form */}
      {showAddField && (
        <div
          style={{
            backgroundColor: "#ffffff",
            padding: "1.5rem",
            borderRadius: "8px",
            border: "1px solid #93c5fd",
            boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
          }}
        >
          <h4 style={{ fontSize: "1rem", fontWeight: 600, marginBottom: "1rem", color: "#1e3a8a" }}>
            Add New Field
          </h4>
          <form onSubmit={handleAddField} style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
            <div>
              <label style={{ fontSize: "0.8rem", fontWeight: 600, display: "block", marginBottom: "0.25rem" }}>
                Field Name (ID) *
              </label>
              <input
                type="text"
                required
                placeholder="e.g. invoice_total"
                value={newField.name}
                onChange={(e) => setNewField({ ...newField, name: e.target.value })}
                style={{ width: "100%", padding: "0.4rem 0.6rem", borderRadius: "4px", border: "1px solid #cbd5e1" }}
              />
            </div>
            <div>
              <label style={{ fontSize: "0.8rem", fontWeight: 600, display: "block", marginBottom: "0.25rem" }}>
                Display Label
              </label>
              <input
                type="text"
                placeholder="e.g. Invoice Total"
                value={newField.label}
                onChange={(e) => setNewField({ ...newField, label: e.target.value })}
                style={{ width: "100%", padding: "0.4rem 0.6rem", borderRadius: "4px", border: "1px solid #cbd5e1" }}
              />
            </div>
            <div>
              <label style={{ fontSize: "0.8rem", fontWeight: 600, display: "block", marginBottom: "0.25rem" }}>
                Field Type
              </label>
              <select
                value={newField.type}
                onChange={(e) => setNewField({ ...newField, type: e.target.value as any })}
                style={{ width: "100%", padding: "0.4rem 0.6rem", borderRadius: "4px", border: "1px solid #cbd5e1" }}
              >
                <option value="text">Text</option>
                <option value="number">Number</option>
                <option value="boolean">Boolean</option>
                <option value="date">Date & Time</option>
                <option value="enum">Enum (Select)</option>
                <option value="reference">Reference (Foreign Key)</option>
                <option value="list">List (Array)</option>
              </select>
            </div>
            <div style={{ display: "flex", gap: "1.5rem", alignItems: "center", paddingTop: "1.2rem" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={Boolean(newField.required)}
                  onChange={(e) => setNewField({ ...newField, required: e.target.checked })}
                />
                Required
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={Boolean(newField.unique)}
                  onChange={(e) => setNewField({ ...newField, unique: e.target.checked })}
                />
                Unique Constraint
              </label>
            </div>

            <div style={{ gridColumn: "span 2", display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
              <button
                type="button"
                onClick={() => setShowAddField(false)}
                style={{ padding: "0.4rem 0.8rem", borderRadius: "4px", border: "1px solid #cbd5e1", background: "#ffffff" }}
              >
                Cancel
              </button>
              <button
                type="submit"
                style={{ padding: "0.4rem 1rem", borderRadius: "4px", border: "none", backgroundColor: "#2563eb", color: "#ffffff", fontWeight: 600 }}
              >
                Save Field
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
};

