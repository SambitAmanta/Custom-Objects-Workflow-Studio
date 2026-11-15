export type FieldType =
  | "text"
  | "number"
  | "boolean"
  | "date"
  | "enum"
  | "reference"
  | "list";

export type UserRole = "admin" | "editor" | "viewer";

export type FieldPermission = "write" | "read" | "masked" | "hidden";

export interface FieldValidationRules {
  min_length?: number;
  max_length?: number;
  pattern?: string;
  minimum?: number;
  maximum?: number;
  min_items?: number;
  max_items?: number;
  options?: string[];
}

export interface FieldDefinition {
  name: string;
  label?: string;
  type: FieldType;
  required?: boolean;
  unique?: boolean;
  default?: any;
  item_type?: "text" | "number" | "reference" | "enum";
  options?: string[];
  target_type?: string;
  validation_rules?: FieldValidationRules;
  ui_hints?: {
    placeholder?: string;
    help_text?: string;
    widget?: "input" | "textarea" | "select" | "radio" | "checkbox";
  };
}

export interface WorkflowTransition {
  name: string;
  from_state: string;
  to_state: string;
  roles?: string[];
  conditions?: Record<string, any>;
  has_conditions?: boolean;
}

export interface WorkflowDefinition {
  initial_state: string;
  states: string[];
  transitions: WorkflowTransition[];
}

export interface ObjectSchema {
  slug: string;
  name: string;
  description?: string;
  version?: number;
  status?: "draft" | "published";
  fields: FieldDefinition[];
  workflow?: WorkflowDefinition;
  permissions?: {
    field_permissions?: Record<string, Record<string, FieldPermission>>;
  };
}

export interface RecordItem {
  id: string;
  tenant_id?: string;
  type: string;
  version: number;
  state: string;
  data: Record<string, any>;
  created_at: string;
  updated_at: string;
  [key: string]: any;
}

