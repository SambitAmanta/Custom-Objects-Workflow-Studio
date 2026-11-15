import React from "react";
import { SchemaTable } from "../components/SchemaTable";
import { ObjectSchema, RecordItem } from "../types";

export default {
  title: "Components/SchemaTable",
  component: SchemaTable,
};

const invoiceSchema: ObjectSchema = {
  slug: "invoice",
  name: "Invoice",
  version: 1,
  fields: [
    { name: "invoice_number", label: "Invoice #", type: "text" },
    { name: "customer_name", label: "Customer", type: "text" },
    { name: "amount", label: "Amount ($)", type: "number" },
    { name: "tax_id", label: "Tax ID", type: "text" },
  ],
  permissions: {
    field_permissions: {
      tax_id: {
        admin: "write",
        editor: "masked",
        viewer: "hidden",
      },
    },
  },
};

const mockRecords: RecordItem[] = [
  {
    id: "rec-1",
    type: "invoice",
    version: 1,
    state: "draft",
    data: {
      invoice_number: "INV-2026-001",
      customer_name: "Stark Logistics",
      amount: 2500,
      tax_id: "US-99182746",
    },
    created_at: "2026-11-15T12:00:00Z",
    updated_at: "2026-11-15T12:00:00Z",
  },
  {
    id: "rec-2",
    type: "invoice",
    version: 2,
    state: "approved",
    data: {
      invoice_number: "INV-2026-002",
      customer_name: "Wayne Technologies",
      amount: 14500,
      tax_id: "US-88271635",
    },
    created_at: "2026-11-15T12:30:00Z",
    updated_at: "2026-11-15T13:00:00Z",
  },
  {
    id: "rec-3",
    type: "invoice",
    version: 1,
    state: "paid",
    data: {
      invoice_number: "INV-2026-003",
      customer_name: "Cyberdyne Systems",
      amount: 750,
      tax_id: "US-77362514",
    },
    created_at: "2026-11-15T14:00:00Z",
    updated_at: "2026-11-15T15:00:00Z",
  },
];

export const AdminView = () => (
  <div style={{ padding: "2rem" }}>
    <h3>Admin Table View (All Fields Visible)</h3>
    <SchemaTable
      schema={invoiceSchema}
      records={mockRecords}
      role="admin"
      sortField="amount"
      sortDirection="desc"
      hasNextPage={true}
      hasPrevPage={false}
      onRowClick={(rec) => console.log("Clicked row:", rec)}
    />
  </div>
);

export const EditorMaskedView = () => (
  <div style={{ padding: "2rem" }}>
    <h3>Editor Table View (Tax ID Masked with Lock Icon)</h3>
    <SchemaTable
      schema={invoiceSchema}
      records={mockRecords}
      role="editor"
      hasNextPage={false}
      hasPrevPage={true}
      onRowClick={(rec) => console.log("Clicked row:", rec)}
    />
  </div>
);

export const ViewerHiddenView = () => (
  <div style={{ padding: "2rem" }}>
    <h3>Viewer Table View (Tax ID Column Completely Hidden)</h3>
    <SchemaTable
      schema={invoiceSchema}
      records={mockRecords}
      role="viewer"
      hasNextPage={false}
      hasPrevPage={false}
      onRowClick={(rec) => console.log("Clicked row:", rec)}
    />
  </div>
);

