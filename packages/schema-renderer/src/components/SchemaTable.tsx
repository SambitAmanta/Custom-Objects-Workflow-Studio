import React from "react";
import { ObjectSchema, RecordItem, UserRole } from "../types";
import { getFieldPermission, isFieldVisible } from "../utils/permissions";

export interface SchemaTableProps {
  schema: ObjectSchema;
  records: RecordItem[];
  role?: UserRole;
  sortField?: string;
  sortDirection?: "asc" | "desc";
  onSort?: (field: string) => void;
  onRowClick?: (record: RecordItem) => void;
  onNextPage?: () => void;
  onPrevPage?: () => void;
  hasNextPage?: boolean;
  hasPrevPage?: boolean;
  isLoading?: boolean;
}

export const SchemaTable: React.FC<SchemaTableProps> = ({
  schema,
  records,
  role = "viewer",
  sortField,
  sortDirection,
  onSort,
  onRowClick,
  onNextPage,
  onPrevPage,
  hasNextPage = false,
  hasPrevPage = false,
  isLoading = false,
}) => {
  const visibleFields = (schema.fields || []).filter((f) =>
    isFieldVisible(schema, f.name, role)
  );

  const getStateBadgeStyle = (state: string): React.CSSProperties => {
    switch (state?.toLowerCase()) {
      case "approved":
      case "active":
      case "completed":
        return { backgroundColor: "#dcfce7", color: "#15803d" };
      case "submitted":
      case "in_progress":
        return { backgroundColor: "#fef9c3", color: "#a16207" };
      case "rejected":
      case "failed":
        return { backgroundColor: "#fee2e2", color: "#b91c1c" };
      default:
        return { backgroundColor: "#f3f4f6", color: "#4b5563" };
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", width: "100%" }}>
      <div style={{ overflowX: "auto", border: "1px solid #e5e7eb", borderRadius: "8px" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.875rem" }}>
          <thead style={{ backgroundColor: "#f9fafb", borderBottom: "1px solid #e5e7eb" }}>
            <tr>
              <th style={{ padding: "0.75rem 1rem", fontWeight: 600, color: "#374151" }}>ID</th>
              <th style={{ padding: "0.75rem 1rem", fontWeight: 600, color: "#374151" }}>State</th>
              <th style={{ padding: "0.75rem 1rem", fontWeight: 600, color: "#374151" }}>Version</th>
              {visibleFields.map((field) => (
                <th
                  key={field.name}
                  onClick={() => onSort && onSort(field.name)}
                  style={{
                    padding: "0.75rem 1rem",
                    fontWeight: 600,
                    color: "#374151",
                    cursor: onSort ? "pointer" : "default",
                    userSelect: "none",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
                    <span>{field.label || field.name}</span>
                    {sortField === field.name && (
                      <span>{sortDirection === "asc" ? "▲" : "▼"}</span>
                    )}
                  </div>
                </th>
              ))}
              <th style={{ padding: "0.75rem 1rem", fontWeight: 600, color: "#374151" }}>Updated</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td
                  colSpan={visibleFields.length + 4}
                  style={{ padding: "2rem", textAlign: "center", color: "#6b7280" }}
                >
                  Loading records...
                </td>
              </tr>
            ) : records.length === 0 ? (
              <tr>
                <td
                  colSpan={visibleFields.length + 4}
                  style={{ padding: "2rem", textAlign: "center", color: "#6b7280" }}
                >
                  No records found.
                </td>
              </tr>
            ) : (
              records.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => onRowClick && onRowClick(r)}
                  style={{
                    borderBottom: "1px solid #e5e7eb",
                    cursor: onRowClick ? "pointer" : "default",
                    transition: "background-color 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    if (onRowClick) e.currentTarget.style.backgroundColor = "#f9fafb";
                  }}
                  onMouseLeave={(e) => {
                    if (onRowClick) e.currentTarget.style.backgroundColor = "transparent";
                  }}
                >
                  <td style={{ padding: "0.75rem 1rem", fontFamily: "monospace", fontSize: "0.75rem", color: "#6b7280" }}>
                    {r.id.slice(0, 8)}...
                  </td>
                  <td style={{ padding: "0.75rem 1rem" }}>
                    <span
                      style={{
                        padding: "0.2rem 0.5rem",
                        borderRadius: "9999px",
                        fontSize: "0.75rem",
                        fontWeight: 500,
                        ...getStateBadgeStyle(r.state),
                      }}
                    >
                      {r.state || "draft"}
                    </span>
                  </td>
                  <td style={{ padding: "0.75rem 1rem", fontSize: "0.8rem", color: "#4b5563" }}>
                    v{r.version}
                  </td>
                  {visibleFields.map((field) => {
                    const perm = getFieldPermission(schema, field.name, role);
                    const val = r.data?.[field.name];

                    if (perm === "masked") {
                      return (
                        <td key={field.name} style={{ padding: "0.75rem 1rem" }}>
                          <span style={{ color: "#9ca3af", fontStyle: "italic", fontSize: "0.8rem" }}>
                            ***MASKED***
                          </span>
                        </td>
                      );
                    }

                    return (
                      <td key={field.name} style={{ padding: "0.75rem 1rem", color: "#1f2937" }}>
                        {field.type === "boolean"
                          ? val
                            ? "✓"
                            : "✗"
                          : Array.isArray(val)
                          ? val.join(", ")
                          : typeof val === "object" && val !== null
                          ? JSON.stringify(val)
                          : val !== undefined && val !== null
                          ? String(val)
                          : "—"}
                      </td>
                    );
                  })}
                  <td style={{ padding: "0.75rem 1rem", fontSize: "0.75rem", color: "#6b7280" }}>
                    {r.updated_at ? new Date(r.updated_at).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination controls */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0.5rem 0" }}>
        <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
          Showing {records.length} records
        </span>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            type="button"
            onClick={onPrevPage}
            disabled={!hasPrevPage || isLoading}
            style={{
              padding: "0.35rem 0.75rem",
              borderRadius: "4px",
              border: "1px solid #d1d5db",
              backgroundColor: hasPrevPage ? "#ffffff" : "#f3f4f6",
              cursor: hasPrevPage ? "pointer" : "not-allowed",
              fontSize: "0.8rem",
            }}
          >
            Previous
          </button>
          <button
            type="button"
            onClick={onNextPage}
            disabled={!hasNextPage || isLoading}
            style={{
              padding: "0.35rem 0.75rem",
              borderRadius: "4px",
              border: "1px solid #d1d5db",
              backgroundColor: hasNextPage ? "#ffffff" : "#f3f4f6",
              cursor: hasNextPage ? "pointer" : "not-allowed",
              fontSize: "0.8rem",
            }}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
};

