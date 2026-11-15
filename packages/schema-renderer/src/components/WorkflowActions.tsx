import React from "react";
import { WorkflowTransition } from "../types";

export interface WorkflowActionsProps {
  currentState: string;
  availableTransitions: WorkflowTransition[];
  onTransition: (transitionName: string) => void | Promise<void>;
  isTransitioning?: boolean;
  disabled?: boolean;
}

export const WorkflowActions: React.FC<WorkflowActionsProps> = ({
  currentState,
  availableTransitions = [],
  onTransition,
  isTransitioning = false,
  disabled = false,
}) => {
  const getButtonColor = (name: string): { bg: string; hover: string } => {
    const n = name.toLowerCase();
    if (n.includes("approve") || n.includes("accept") || n.includes("publish")) {
      return { bg: "#16a34a", hover: "#15803d" };
    }
    if (n.includes("reject") || n.includes("cancel") || n.includes("deny")) {
      return { bg: "#dc2626", hover: "#b91c1c" };
    }
    return { bg: "#2563eb", hover: "#1d4ed8" };
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0.75rem 1rem",
        backgroundColor: "#f9fafb",
        borderRadius: "8px",
        border: "1px solid #e5e7eb",
        flexWrap: "wrap",
        gap: "0.75rem",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
        <span style={{ fontSize: "0.875rem", fontWeight: 600, color: "#4b5563" }}>
          Current State:
        </span>
        <span
          style={{
            padding: "0.2rem 0.6rem",
            borderRadius: "9999px",
            fontSize: "0.8rem",
            fontWeight: 600,
            textTransform: "capitalize",
            backgroundColor: "#e0e7ff",
            color: "#3730a3",
          }}
        >
          {currentState || "draft"}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
        {availableTransitions.length === 0 ? (
          <span style={{ fontSize: "0.8rem", color: "#9ca3af", fontStyle: "italic" }}>
            No transitions available for your role in this state.
          </span>
        ) : (
          availableTransitions.map((t) => {
            const colors = getButtonColor(t.name);
            return (
              <button
                key={t.name}
                type="button"
                disabled={isTransitioning || disabled}
                onClick={() => onTransition(t.name)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.35rem",
                  padding: "0.4rem 0.85rem",
                  borderRadius: "6px",
                  border: "none",
                  backgroundColor: colors.bg,
                  color: "#ffffff",
                  fontSize: "0.8rem",
                  fontWeight: 500,
                  cursor: isTransitioning || disabled ? "not-allowed" : "pointer",
                  opacity: isTransitioning || disabled ? 0.6 : 1,
                  transition: "background-color 0.15s ease",
                }}
                onMouseEnter={(e) => {
                  if (!isTransitioning && !disabled) {
                    e.currentTarget.style.backgroundColor = colors.hover;
                  }
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = colors.bg;
                }}
              >
                <span>{t.name}</span>
                <span style={{ fontSize: "0.7rem", opacity: 0.8 }}>→ {t.to_state}</span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
};
