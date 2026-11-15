import React from "react";
import { SchemaForm } from "../components/SchemaForm";
import { ObjectSchema } from "../types";

export default {
  title: "Components/SchemaForm",
  component: SchemaForm,
};

const invoiceSchema: ObjectSchema = {
  slug: "invoice",
  name: "Invoice",
  description: "Enterprise billing invoice schema",
  version: 1,
  fields: [
    {
      name: "invoice_number",
      label: "Invoice Number",
      type: "text",
      required: true,
      unique: true,
      ui_hints: { placeholder: "e.g. INV-2026-001" },
    },
    {
      name: "customer_name",
      label: "Customer Name",
      type: "text",
      required: true,
    },
    {
      name: "amount",
      label: "Amount (USD)",
      type: "number",
      required: true,
      validation_rules: { minimum: 0 },
    },
    {
      name: "status",
      label: "Workflow Status",
      type: "enum",
      options: ["draft", "submitted", "approved", "rejected", "paid"],
      default: "draft",
    },
    {
      name: "is_urgent",
      label: "Urgent Processing",
      type: "boolean",
      default: false,
    },
    {
      name: "due_date",
      label: "Due Date",
      type: "date",
    },
    {
      name: "customer_ref",
      label: "Customer Reference",
      type: "reference",
      target_type: "customer",
    },
    {
      name: "tax_id",
      label: "Confidential Tax ID",
      type: "text",
    },
  ],
  permissions: {
    field_permissions: {
      tax_id: {
        admin: "write",
        editor: "masked",
        viewer: "hidden",
      },
      amount: {
        admin: "write",
        editor: "write",
        viewer: "read",
      },
    },
  },
};

export const AdminCreate = () => (
  <div style={{ maxWidth: "600px", padding: "2rem" }}>
    <h3>Admin Role (Full Write Access)</h3>
    <SchemaForm
      schema={invoiceSchema}
      role="admin"
      onSubmit={(data) => alert(JSON.stringify(data, null, 2))}
    />
  </div>
);

export const EditorEdit = () => (
  <div style={{ maxWidth: "600px", padding: "2rem" }}>
    <h3>Editor Role (Tax ID Masked)</h3>
    <SchemaForm
      schema={invoiceSchema}
      role="editor"
      initialData={{
        invoice_number: "INV-2026-001",
        customer_name: "Stark Logistics",
        amount: 2500,
        status: "draft",
        is_urgent: true,
        tax_id: "US-99182746",
      }}
      onSubmit={(data) => alert(JSON.stringify(data, null, 2))}
    />
  </div>
);

export const ViewerReadOnly = () => (
  <div style={{ maxWidth: "600px", padding: "2rem" }}>
    <h3>Viewer Role (Tax ID Hidden, Amount Read-Only)</h3>
    <SchemaForm
      schema={invoiceSchema}
      role="viewer"
      initialData={{
        invoice_number: "INV-2026-001",
        customer_name: "Stark Logistics",
        amount: 2500,
        status: "draft",
        is_urgent: false,
        tax_id: "US-99182746",
      }}
      onSubmit={(data) => alert(JSON.stringify(data, null, 2))}
    />
  </div>
);

