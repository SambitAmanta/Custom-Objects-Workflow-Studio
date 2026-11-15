import React, { useState } from "react";
import { ObjectSchema, RecordItem, UserRole } from "@forge/schema-renderer";
import { SchemaEditor } from "./components/SchemaEditor";
import { WorkflowEditor } from "./components/WorkflowEditor";
import { RecordExplorer } from "./components/RecordExplorer";

const INITIAL_INVOICE_SCHEMA: ObjectSchema = {
  slug: "invoice",
  name: "Invoice",
  description: "Enterprise multi-state invoice lifecycle",
  version: 1,
  status: "published",
  fields: [
    {
      name: "title",
      label: "Invoice Title",
      type: "text",
      required: true,
      validation_rules: { min_length: 3, max_length: 100 },
      ui_hints: { placeholder: "e.g. Cloud Infrastructure Q4" },
    },
    {
      name: "amount",
      label: "Total Amount ($)",
      type: "number",
      required: true,
      validation_rules: { minimum: 0 },
      ui_hints: { placeholder: "e.g. 1500" },
    },
    {
      name: "department",
      label: "Billing Department",
      type: "enum",
      required: true,
      options: ["Engineering", "Product", "Operations", "Sales"],
    },
    {
      name: "tax_id",
      label: "Tax Identification Number",
      type: "text",
      required: false,
      ui_hints: { placeholder: "XX-XXXXXXX" },
    },
  ],
  workflow: {
    initial_state: "draft",
    states: ["draft", "submitted", "approved", "rejected"],
    transitions: [
      {
        name: "submit",
        from_state: "draft",
        to_state: "submitted",
        roles: ["editor", "admin"],
      },
      {
        name: "approve",
        from_state: "submitted",
        to_state: "approved",
        roles: ["admin"],
        conditions: { "<=": [{ var: "data.amount" }, 1000] },
      },
      {
        name: "reject",
        from_state: "submitted",
        to_state: "rejected",
        roles: ["admin"],
      },
    ],
  },
  permissions: {
    field_permissions: {
      tax_id: {
        viewer: "hidden",
        editor: "masked",
        admin: "write",
      },
    },
  },
};

const INITIAL_RECORDS: RecordItem[] = [
  {
    id: "rec-8f921ab0",
    type: "invoice",
    version: 1,
    state: "draft",
    data: {
      title: "DataDog Monitoring Subscription",
      amount: 450,
      department: "Engineering",
      tax_id: "US-8829103",
    },
    created_at: "2026-11-15T18:00:00Z",
    updated_at: "2026-11-15T18:00:00Z",
  },
  {
    id: "rec-41c09bb2",
    type: "invoice",
    version: 2,
    state: "submitted",
    data: {
      title: "Annual SOC2 Security Audit",
      amount: 12000,
      department: "Operations",
      tax_id: "US-1192834",
    },
    created_at: "2026-11-15T19:00:00Z",
    updated_at: "2026-11-15T19:30:00Z",
  },
  {
    id: "rec-6b19fe01",
    type: "invoice",
    version: 3,
    state: "approved",
    data: {
      title: "GitHub Enterprise Seats",
      amount: 800,
      department: "Engineering",
      tax_id: "US-9912831",
    },
    created_at: "2026-11-15T17:00:00Z",
    updated_at: "2026-11-15T20:15:00Z",
  },
];

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"schema" | "workflow" | "records">("records");
  const [schema, setSchema] = useState<ObjectSchema>(INITIAL_INVOICE_SCHEMA);
  const [records, setRecords] = useState<RecordItem[]>(INITIAL_RECORDS);
  const [currentRole, setCurrentRole] = useState<UserRole>("admin");
  const [notification, setNotification] = useState<{ message: string; type: "success" | "error" | "info" } | null>(null);

  const notify = (message: string, type: "success" | "error" | "info" = "info") => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 4000);
  };

  const handleCreateRecord = async (data: Record<string, any>) => {
    const newRecord: RecordItem = {
      id: `rec-${Math.random().toString(36).substring(2, 10)}`,
      type: schema.slug,
      version: 1,
      state: schema.workflow?.initial_state || "draft",
      data,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    setRecords([newRecord, ...records]);
    notify(`Created record '${newRecord.id}' (v1) in '${newRecord.state}' state`, "success");
  };

  const handleUpdateRecord = async (id: string, version: number, data: Record<string, any>) => {
    setRecords((prev) =>
      prev.map((r) => {
        if (r.id === id) {
          // Optimistic Concurrency check
          if (r.version !== version) {
            notify(`HTTP 412 Precondition Failed: Version mismatch (current v${r.version})`, "error");
            return r;
          }
          const updated = {
            ...r,
            version: r.version + 1,
            data: { ...r.data, ...data },
            updated_at: new Date().toISOString(),
          };
          notify(`Updated record '${id}' to v${updated.version}`, "success");
          return updated;
        }
        return r;
      })
    );
  };

  const handleExecuteTransition = async (recordId: string, transitionName: string) => {
    const record = records.find((r) => r.id === recordId);
    if (!record) return;

    const transition = schema.workflow?.transitions.find((t) => t.name === transitionName);
    if (!transition) {
      notify(`Transition '${transitionName}' not found`, "error");
      return;
    }

    // Role Guard Check
    const allowedRoles = transition.roles || ["editor", "admin"];
    if (currentRole !== "admin" && !allowedRoles.includes(currentRole)) {
      notify(`HTTP 403 Forbidden: Role '${currentRole}' is not allowed to execute '${transitionName}'`, "error");
      return;
    }

    // Condition Guard Check
    if (transition.conditions) {
      const amount = Number(record.data.amount || 0);
      if (transitionName === "approve" && amount > 1000) {
        notify(`HTTP 400 Bad Request: Transition condition guard violated (amount ${amount} > 1000 threshold)`, "error");
        return;
      }
    }

    // Transition execution (atomic compare-and-set)
    setRecords((prev) =>
      prev.map((r) => {
        if (r.id === recordId) {
          return {
            ...r,
            state: transition.to_state,
            version: r.version + 1,
            updated_at: new Date().toISOString(),
          };
        }
        return r;
      })
    );
    notify(`Transitioned record to '${transition.to_state}' (v${record.version + 1})`, "success");
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      {/* Top Header */}
      <header
        style={{
          backgroundColor: "#0f172a",
          color: "#ffffff",
          padding: "1rem 2rem",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: "1px solid #1e293b",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <div
            style={{
              width: "32px",
              height: "32px",
              backgroundColor: "#3b82f6",
              borderRadius: "6px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 800,
              fontSize: "1.1rem",
            }}
          >
            F
          </div>
          <div>
            <h1 style={{ fontSize: "1.15rem", fontWeight: 700, letterSpacing: "-0.01em" }}>
              Forge
            </h1>
            <p style={{ fontSize: "0.75rem", color: "#94a3b8" }}>
              Multi-tenant Custom Objects & Workflow Studio
            </p>
          </div>
        </div>

        {/* Global Navigation Tabs */}
        <div style={{ display: "flex", gap: "0.5rem", backgroundColor: "#1e293b", padding: "4px", borderRadius: "8px" }}>
          <button
            type="button"
            onClick={() => setActiveTab("records")}
            style={{
              padding: "0.4rem 1rem",
              borderRadius: "6px",
              border: "none",
              backgroundColor: activeTab === "records" ? "#3b82f6" : "transparent",
              color: activeTab === "records" ? "#ffffff" : "#94a3b8",
              fontSize: "0.85rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            📋 Records Explorer
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("schema")}
            style={{
              padding: "0.4rem 1rem",
              borderRadius: "6px",
              border: "none",
              backgroundColor: activeTab === "schema" ? "#3b82f6" : "transparent",
              color: activeTab === "schema" ? "#ffffff" : "#94a3b8",
              fontSize: "0.85rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            📐 Schema Builder
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("workflow")}
            style={{
              padding: "0.4rem 1rem",
              borderRadius: "6px",
              border: "none",
              backgroundColor: activeTab === "workflow" ? "#3b82f6" : "transparent",
              color: activeTab === "workflow" ? "#ffffff" : "#94a3b8",
              fontSize: "0.85rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            🔄 Workflow Studio
          </button>
        </div>
      </header>

      {/* Toast Notification Bar */}
      {notification && (
        <div
          style={{
            padding: "0.75rem 2rem",
            backgroundColor:
              notification.type === "success"
                ? "#ecfdf5"
                : notification.type === "error"
                ? "#fef2f2"
                : "#eff6ff",
            color:
              notification.type === "success"
                ? "#065f46"
                : notification.type === "error"
                ? "#991b1b"
                : "#1e40af",
            borderBottom: `1px solid ${
              notification.type === "success"
                ? "#a7f3d0"
                : notification.type === "error"
                ? "#fecaca"
                : "#bfdbfe"
            }`,
            fontSize: "0.85rem",
            fontWeight: 500,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>{notification.message}</span>
          <button
            type="button"
            onClick={() => setNotification(null)}
            style={{ background: "none", border: "none", cursor: "pointer", fontSize: "1rem", color: "inherit" }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Main Content Area */}
      <main style={{ flex: 1, padding: "2rem", maxWidth: "1280px", margin: "0 auto", width: "100%" }}>
        {activeTab === "records" && (
          <RecordExplorer
            schema={schema}
            records={records}
            role={currentRole}
            onRoleChange={setCurrentRole}
            onCreateRecord={handleCreateRecord}
            onUpdateRecord={handleUpdateRecord}
            onExecuteTransition={handleExecuteTransition}
          />
        )}

        {activeTab === "schema" && (
          <SchemaEditor
            schema={schema}
            onChange={setSchema}
            onSaveDraft={async () => notify("Draft schema saved successfully", "success")}
            onPublish={async () => {
              setSchema({ ...schema, status: "published", version: (schema.version || 1) + 1 });
              notify(`Published immutable schema v${(schema.version || 1) + 1}`, "success");
            }}
          />
        )}

        {activeTab === "workflow" && (
          <WorkflowEditor
            schema={schema}
            onChange={setSchema}
            onSave={async () => notify("Workflow state machine updated", "success")}
          />
        )}
      </main>
    </div>
  );
};

export default App;
