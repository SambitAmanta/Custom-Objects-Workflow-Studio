import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  validateRecordData,
  getFieldPermission,
  isFieldVisible,
  isFieldEditable,
  SchemaForm,
  SchemaTable,
  WorkflowActions,
} = require("../dist/index.js");

const TEST_SCHEMA = {
  slug: "invoice",
  name: "Invoice",
  version: 1,
  fields: [
    {
      name: "title",
      label: "Invoice Title",
      type: "text",
      required: true,
      validation_rules: { min_length: 3, max_length: 50 },
    },
    {
      name: "amount",
      label: "Total Amount",
      type: "number",
      required: true,
      validation_rules: { minimum: 10, maximum: 10000 },
    },
    {
      name: "department",
      type: "enum",
      options: ["Engineering", "Sales"],
    },
    {
      name: "tax_id",
      type: "text",
      required: false,
    },
  ],
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

describe("Schema Validation Utilities", () => {
  test("detects missing required fields", () => {
    const errors = validateRecordData(TEST_SCHEMA.fields, {});
    assert.ok(errors.title, "Expected title error");
    assert.ok(errors.amount, "Expected amount error");
  });

  test("validates text length constraints", () => {
    const errors = validateRecordData(TEST_SCHEMA.fields, {
      title: "AB", // min_length is 3
      amount: 100,
    });
    assert.match(errors.title, /at least 3 characters/);
  });

  test("validates numeric range constraints", () => {
    const errors = validateRecordData(TEST_SCHEMA.fields, {
      title: "Valid Title",
      amount: 5, // minimum is 10
    });
    assert.match(errors.amount, /greater than or equal to 10/);
  });

  test("validates enum option membership", () => {
    const errors = validateRecordData(TEST_SCHEMA.fields, {
      title: "Valid Title",
      amount: 50,
      department: "Marketing", // Not in ["Engineering", "Sales"]
    });
    assert.match(errors.department, /Must be one of/);
  });
});

describe("Field-Level Permission RBAC", () => {
  test("hides restricted fields for viewer", () => {
    assert.equal(getFieldPermission(TEST_SCHEMA, "tax_id", "viewer"), "hidden");
    assert.equal(isFieldVisible(TEST_SCHEMA, "tax_id", "viewer"), false);
  });

  test("masks restricted fields for editor", () => {
    assert.equal(getFieldPermission(TEST_SCHEMA, "tax_id", "editor"), "masked");
    assert.equal(isFieldVisible(TEST_SCHEMA, "tax_id", "editor"), true);
    assert.equal(isFieldEditable(TEST_SCHEMA, "tax_id", "editor"), false);
  });

  test("allows full write access for admin", () => {
    assert.equal(getFieldPermission(TEST_SCHEMA, "tax_id", "admin"), "write");
    assert.equal(isFieldEditable(TEST_SCHEMA, "tax_id", "admin"), true);
  });
});

describe("Component Functionality & Exports", () => {
  test("exports valid React component definitions", () => {
    assert.equal(typeof SchemaForm, "function");
    assert.equal(typeof SchemaTable, "function");
    assert.equal(typeof WorkflowActions, "function");
  });
});
