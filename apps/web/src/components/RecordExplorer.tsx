import React, { useState } from "react";
import {
  ObjectSchema,
  RecordItem,
  SchemaForm,
  SchemaTable,
  WorkflowActions,
  WorkflowTransition,
  UserRole,
} from "@forge/schema-renderer";

interface RecordExplorerProps {
  schema: ObjectSchema;
  records: RecordItem[];
  role: UserRole;
  onRoleChange: (newRole: UserRole) => void;
  onCreateRecord: (data: Record<string, any>) => Promise<void>;
  onUpdateRecord: (id: string, version: number, data: Record<string, any>) => Promise<void>;
  onExecuteTransition: (recordId: string, transitionName: string) => Promise<void>;
  availableTransitionsForRecord?: WorkflowTransition[];
  onFetchTransitions?: (recordId: string) => Promise<WorkflowTransition[]>;
  isLoading?: boolean;
}

export const RecordExplorer: React.FC<RecordExplorerProps> = ({
  schema,
  records,
  role,
  onRoleChange,
  onCreateRecord,
  onUpdateRecord,
  onExecuteTransition,
  onFetchTransitions,
  isLoading = false,
}) => {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState<RecordItem | null>(null);
  const [currentTransitions, setCurrentTransitions] = useState<WorkflowTransition[]>([]);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const handleSelectRecord = async (record: RecordItem) => {
    setSelectedRecord(record);
    if (onFetchTransitions) {
      const transitions = await onFetchTransitions(record.id);
      setCurrentTransitions(transitions);
    } else {
      // Derive from schema transitions matching current state
      const matching = (schema.workflow?.transitions || []).filter(
        (t) => t.from_state === record.state
      );
      setCurrentTransitions(matching);
    }
  };

  const handleTransitionClick = async (transitionName: string) => {
    if (!selectedRecord) return;
    setIsTransitioning(true);
    try {
      await onExecuteTransition(selectedRecord.id, transitionName);
      setSelectedRecord(null);
    } finally {
      setIsTransitioning(false);
    }
  };

  const handleRecordUpdate = async (data: Record<string, any>) => {
    if (!selectedRecord) return;
    setIsSaving(true);
    try {
      await onUpdateRecord(selectedRecord.id, selectedRecord.version, data);
      setSelectedRecord(null);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      {/* Explorer Controls & Role Switcher */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          backgroundColor: "#ffffff",
          padding: "1rem 1.25rem",
          borderRadius: "8px",
          border: "1px solid #e2e8f0",
          flexWrap: "wrap",
          gap: "1rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "#475569" }}>
            Viewing as Role:
          </span>
          <div style={{ display: "flex", backgroundColor: "#f1f5f9", padding: "3px", borderRadius: "6px" }}>
            {(["admin", "editor", "viewer"] as UserRole[]).map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => onRoleChange(r)}
                style={{
                  padding: "0.35rem 0.85rem",
                  fontSize: "0.8rem",
                  fontWeight: 600,
                  textTransform: "capitalize",
                  borderRadius: "4px",
                  border: "none",
                  backgroundColor: role === r ? "#ffffff" : "transparent",
                  color: role === r ? "#0f172a" : "#64748b",
                  boxShadow: role === r ? "0 1px 2px rgba(0,0,0,0.05)" : "none",
                  cursor: "pointer",
                }}
              >
                {r}
              </button>
            ))}
          </div>
          <span style={{ fontSize: "0.75rem", color: "#64748b" }}>
            {role === "viewer"
              ? "Read-only access, sensitive fields masked/hidden"
              : role === "editor"
              ? "Can create & edit non-restricted fields"
              : "Full admin permissions"}
          </span>
        </div>

        <button
          type="button"
          onClick={() => setShowCreateModal(true)}
          disabled={role === "viewer"}
          style={{
            padding: "0.5rem 1rem",
            backgroundColor: role === "viewer" ? "#94a3b8" : "#2563eb",
            color: "#ffffff",
            fontSize: "0.85rem",
            fontWeight: 600,
            borderRadius: "6px",
            border: "none",
            cursor: role === "viewer" ? "not-allowed" : "pointer",
          }}
        >
          + New {schema.name || schema.slug}
        </button>
      </div>

      {/* Records Table */}
      <div
        style={{
          backgroundColor: "#ffffff",
          padding: "1.25rem",
          borderRadius: "8px",
          border: "1px solid #e2e8f0",
        }}
      >
        <SchemaTable
          schema={schema}
          records={records}
          role={role}
          isLoading={isLoading}
          onRowClick={handleSelectRecord}
        />
      </div>

      {/* Create Record Modal */}
      {showCreateModal && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(15, 23, 42, 0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
        >
          <div
            style={{
              backgroundColor: "#ffffff",
              padding: "1.75rem",
              borderRadius: "10px",
              width: "100%",
              maxWidth: "540px",
              maxHeight: "90vh",
              overflowY: "auto",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)",
            }}
          >
            <h3 style={{ fontSize: "1.15rem", fontWeight: 700, marginBottom: "1rem", color: "#0f172a" }}>
              Create {schema.name || schema.slug}
            </h3>
            <SchemaForm
              schema={schema}
              role={role}
              onCancel={() => setShowCreateModal(false)}
              onSubmit={async (data) => {
                await onCreateRecord(data);
                setShowCreateModal(false);
              }}
            />
          </div>
        </div>
      )}

      {/* Record Details & Workflow Actions Modal */}
      {selectedRecord && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(15, 23, 42, 0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
        >
          <div
            style={{
              backgroundColor: "#ffffff",
              padding: "1.75rem",
              borderRadius: "10px",
              width: "100%",
              maxWidth: "600px",
              maxHeight: "90vh",
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: "1.25rem",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <h3 style={{ fontSize: "1.1rem", fontWeight: 700, color: "#0f172a" }}>
                  Record Detail
                </h3>
                <span style={{ fontSize: "0.75rem", fontFamily: "monospace", color: "#64748b" }}>
                  ID: {selectedRecord.id} | ETag: W/"{selectedRecord.version}"
                </span>
              </div>
              <button
                type="button"
                onClick={() => setSelectedRecord(null)}
                style={{
                  border: "none",
                  backgroundColor: "transparent",
                  fontSize: "1.25rem",
                  cursor: "pointer",
                  color: "#64748b",
                }}
              >
                ✕
              </button>
            </div>

            {/* Workflow Action Buttons */}
            <WorkflowActions
              currentState={selectedRecord.state}
              availableTransitions={currentTransitions}
              onTransition={handleTransitionClick}
              isTransitioning={isTransitioning}
            />

            {/* Editable Form */}
            <SchemaForm
              schema={schema}
              initialData={selectedRecord.data}
              role={role}
              isSubmitting={isSaving}
              onCancel={() => setSelectedRecord(null)}
              onSubmit={handleRecordUpdate}
            />
          </div>
        </div>
      )}
    </div>
  );
};
