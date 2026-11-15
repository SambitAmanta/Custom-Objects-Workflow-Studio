import React, { useState } from "react";
import { ObjectSchema, WorkflowDefinition, WorkflowTransition } from "@forge/schema-renderer";
import { MermaidRenderer } from "./MermaidRenderer";

interface WorkflowEditorProps {
  schema: ObjectSchema;
  onChange: (updated: ObjectSchema) => void;
  onSave: () => Promise<void>;
  isSaving?: boolean;
}

export const WorkflowEditor: React.FC<WorkflowEditorProps> = ({
  schema,
  onChange,
  onSave,
  isSaving = false,
}) => {
  const workflow: WorkflowDefinition = schema.workflow || {
    initial_state: "draft",
    states: ["draft"],
    transitions: [],
  };

  const [showAddTransition, setShowAddTransition] = useState(false);
  const [newTransition, setNewTransition] = useState<Partial<WorkflowTransition>>({
    name: "",
    from_state: workflow.initial_state,
    to_state: "",
    roles: ["editor", "admin"],
  });

  // Generate Mermaid stateDiagram-v2 dynamically
  const generateMermaidChart = (): string => {
    const lines: string[] = ["stateDiagram-v2"];
    lines.push(`  [*] --> ${workflow.initial_state}`);

    for (const t of workflow.transitions || []) {
      lines.push(`  ${t.from_state} --> ${t.to_state}: ${t.name}`);
    }

    return lines.join("\n");
  };

  const handleAddTransition = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTransition.name || !newTransition.from_state || !newTransition.to_state) return;

    const transitionToAdd: WorkflowTransition = {
      name: newTransition.name.toLowerCase().replace(/[^a-z0-9_]/g, "_"),
      from_state: newTransition.from_state,
      to_state: newTransition.to_state,
      roles: newTransition.roles || ["editor", "admin"],
    };

    // Ensure to_state is included in states
    const states = Array.from(new Set([...workflow.states, transitionToAdd.to_state]));

    const updatedWorkflow: WorkflowDefinition = {
      ...workflow,
      states,
      transitions: [...(workflow.transitions || []), transitionToAdd],
    };

    onChange({
      ...schema,
      workflow: updatedWorkflow,
    });

    setNewTransition({
      name: "",
      from_state: workflow.initial_state,
      to_state: "",
      roles: ["editor", "admin"],
    });
    setShowAddTransition(false);
  };

  const removeTransition = (index: number) => {
    const updated = (workflow.transitions || []).filter((_, i) => i !== index);
    onChange({
      ...schema,
      workflow: { ...workflow, transitions: updated },
    });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Visual State Diagram Section */}
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
          <div>
            <h3 style={{ fontSize: "1.1rem", fontWeight: 700, color: "#0f172a" }}>
              Live State Machine Diagram
            </h3>
            <span style={{ fontSize: "0.8rem", color: "#64748b" }}>
              Interactive deterministic state graph rendered via Mermaid
            </span>
          </div>
          <button
            type="button"
            onClick={onSave}
            disabled={isSaving}
            style={{
              padding: "0.5rem 1rem",
              borderRadius: "6px",
              border: "none",
              backgroundColor: "#2563eb",
              color: "#ffffff",
              fontSize: "0.85rem",
              fontWeight: 600,
              cursor: isSaving ? "not-allowed" : "pointer",
            }}
          >
            {isSaving ? "Saving..." : "Save Workflow"}
          </button>
        </div>

        <MermaidRenderer chart={generateMermaidChart()} />
      </div>

      {/* Workflow Transitions List */}
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
          <h4 style={{ fontSize: "0.95rem", fontWeight: 600, color: "#1e293b" }}>
            Configured Transitions ({(workflow.transitions || []).length})
          </h4>
          <button
            type="button"
            onClick={() => setShowAddTransition(true)}
            style={{
              padding: "0.4rem 0.8rem",
              borderRadius: "6px",
              backgroundColor: "#f1f5f9",
              border: "1px solid #cbd5e1",
              fontSize: "0.8rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            + Add Transition
          </button>
        </div>

        {(workflow.transitions || []).length === 0 ? (
          <div style={{ padding: "2rem", textAlign: "center", color: "#94a3b8", fontSize: "0.85rem" }}>
            No transitions defined. Add your first state transition to define lifecycle rules.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            {workflow.transitions.map((t, idx) => (
              <div
                key={idx}
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
                  <span style={{ fontWeight: 600, fontSize: "0.9rem", color: "#0f172a" }}>
                    {t.name}
                  </span>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.8rem" }}>
                    <span style={{ padding: "0.1rem 0.4rem", backgroundColor: "#e2e8f0", borderRadius: "4px" }}>
                      {t.from_state}
                    </span>
                    <span>→</span>
                    <span style={{ padding: "0.1rem 0.4rem", backgroundColor: "#dbeafe", color: "#1e40af", borderRadius: "4px", fontWeight: 600 }}>
                      {t.to_state}
                    </span>
                  </div>
                  <span style={{ fontSize: "0.75rem", color: "#64748b" }}>
                    Roles: {(t.roles || []).join(", ")}
                  </span>
                </div>

                <button
                  type="button"
                  onClick={() => removeTransition(idx)}
                  style={{
                    padding: "0.2rem 0.5rem",
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

      {/* Add Transition Modal */}
      {showAddTransition && (
        <div
          style={{
            backgroundColor: "#ffffff",
            padding: "1.25rem",
            borderRadius: "8px",
            border: "1px solid #93c5fd",
            boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
          }}
        >
          <h4 style={{ fontSize: "0.95rem", fontWeight: 600, marginBottom: "1rem", color: "#1e3a8a" }}>
            Add Workflow Transition
          </h4>
          <form onSubmit={handleAddTransition} style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "1rem" }}>
            <div>
              <label style={{ fontSize: "0.8rem", fontWeight: 600, display: "block", marginBottom: "0.25rem" }}>
                Transition Name *
              </label>
              <input
                type="text"
                required
                placeholder="e.g. approve"
                value={newTransition.name}
                onChange={(e) => setNewTransition({ ...newTransition, name: e.target.value })}
                style={{ width: "100%", padding: "0.4rem 0.6rem", borderRadius: "4px", border: "1px solid #cbd5e1" }}
              />
            </div>
            <div>
              <label style={{ fontSize: "0.8rem", fontWeight: 600, display: "block", marginBottom: "0.25rem" }}>
                From State *
              </label>
              <input
                type="text"
                required
                placeholder="e.g. submitted"
                value={newTransition.from_state}
                onChange={(e) => setNewTransition({ ...newTransition, from_state: e.target.value })}
                style={{ width: "100%", padding: "0.4rem 0.6rem", borderRadius: "4px", border: "1px solid #cbd5e1" }}
              />
            </div>
            <div>
              <label style={{ fontSize: "0.8rem", fontWeight: 600, display: "block", marginBottom: "0.25rem" }}>
                To State *
              </label>
              <input
                type="text"
                required
                placeholder="e.g. approved"
                value={newTransition.to_state}
                onChange={(e) => setNewTransition({ ...newTransition, to_state: e.target.value })}
                style={{ width: "100%", padding: "0.4rem 0.6rem", borderRadius: "4px", border: "1px solid #cbd5e1" }}
              />
            </div>

            <div style={{ gridColumn: "span 3", display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
              <button
                type="button"
                onClick={() => setShowAddTransition(false)}
                style={{ padding: "0.4rem 0.8rem", borderRadius: "4px", border: "1px solid #cbd5e1", background: "#ffffff" }}
              >
                Cancel
              </button>
              <button
                type="submit"
                style={{ padding: "0.4rem 1rem", borderRadius: "4px", border: "none", backgroundColor: "#2563eb", color: "#ffffff", fontWeight: 600 }}
              >
                Add Transition
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
};
