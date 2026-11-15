import { FieldDefinition } from "../types";

export function validateRecordData(
  fields: FieldDefinition[],
  data: Record<string, any>
): Record<string, string> {
  const errors: Record<string, string> = {};

  for (const field of fields) {
    const val = data[field.name];
    const rules = field.validation_rules || {};

    // 1. Required Check
    if (field.required) {
      if (val === undefined || val === null || val === "" || (Array.isArray(val) && val.length === 0)) {
        errors[field.name] = `${field.label || field.name} is required`;
        continue;
      }
    }

    if (val === undefined || val === null || val === "") {
      continue;
    }

    // 2. Type-specific validations
    if (field.type === "text") {
      const strVal = String(val);
      if (rules.min_length !== undefined && strVal.length < rules.min_length) {
        errors[field.name] = `Must be at least ${rules.min_length} characters`;
      }
      if (rules.max_length !== undefined && strVal.length > rules.max_length) {
        errors[field.name] = `Must not exceed ${rules.max_length} characters`;
      }
      if (rules.pattern) {
        try {
          const regex = new RegExp(rules.pattern);
          if (!regex.test(strVal)) {
            errors[field.name] = `Value does not match required pattern`;
          }
        } catch {
          // Ignore invalid regex
        }
      }
    } else if (field.type === "number") {
      const numVal = Number(val);
      if (isNaN(numVal)) {
        errors[field.name] = `Must be a valid number`;
      } else {
        if (rules.minimum !== undefined && numVal < rules.minimum) {
          errors[field.name] = `Must be greater than or equal to ${rules.minimum}`;
        }
        if (rules.maximum !== undefined && numVal > rules.maximum) {
          errors[field.name] = `Must be less than or equal to ${rules.maximum}`;
        }
      }
    } else if (field.type === "enum") {
      const options = field.options || rules.options || [];
      if (options.length > 0 && !options.includes(String(val))) {
        errors[field.name] = `Must be one of: ${options.join(", ")}`;
      }
    } else if (field.type === "list") {
      if (Array.isArray(val)) {
        if (rules.min_items !== undefined && val.length < rules.min_items) {
          errors[field.name] = `Must contain at least ${rules.min_items} items`;
        }
        if (rules.max_items !== undefined && val.length > rules.max_items) {
          errors[field.name] = `Must not contain more than ${rules.max_items} items`;
        }
      }
    }
  }

  return errors;
}
