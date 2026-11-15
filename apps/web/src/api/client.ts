import { ObjectSchema, RecordItem, WorkflowTransition } from "@forge/schema-renderer";

const BASE_URL = "/api/v1";

export class ForgeApiClient {
  private token: string | null = null;
  private tenantId: string = "default";

  setAuth(token: string, tenantId: string) {
    this.token = token;
    this.tenantId = tenantId;
  }

  private getHeaders(): HeadersInit {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-Tenant-Id": this.tenantId,
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }
    return headers;
  }

  async getSchema(slug: string): Promise<ObjectSchema> {
    const res = await fetch(`${BASE_URL}/schemas/types/${slug}/`, {
      headers: this.getHeaders(),
    });
    if (!res.ok) throw new Error(`Failed to fetch schema: ${res.statusText}`);
    return res.json();
  }

  async saveDraftSchema(schema: Partial<ObjectSchema>): Promise<ObjectSchema> {
    const res = await fetch(`${BASE_URL}/schemas/types/`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(schema),
    });
    if (!res.ok) throw new Error(`Failed to save draft schema: ${res.statusText}`);
    return res.json();
  }

  async publishDraftSchema(slug: string, allowBreaking: boolean = false): Promise<ObjectSchema> {
    const res = await fetch(`${BASE_URL}/schemas/types/${slug}/publish/`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify({ allow_breaking: allowBreaking }),
    });
    if (!res.ok) throw new Error(`Failed to publish schema: ${res.statusText}`);
    return res.json();
  }

  async listRecords(typeSlug: string, cursor?: string): Promise<{ records: RecordItem[]; next_cursor?: string; has_next: boolean }> {
    const url = new URL(`${window.location.origin}${BASE_URL}/o/${typeSlug}/`);
    if (cursor) url.searchParams.set("cursor", cursor);

    const res = await fetch(url.toString(), {
      headers: this.getHeaders(),
    });
    if (!res.ok) throw new Error(`Failed to list records: ${res.statusText}`);
    return res.json();
  }

  async createRecord(typeSlug: string, data: Record<string, any>, idempotencyKey?: string): Promise<RecordItem> {
    const headers = { ...this.getHeaders() } as Record<string, string>;
    if (idempotencyKey) {
      headers["Idempotency-Key"] = idempotencyKey;
    }

    const res = await fetch(`${BASE_URL}/o/${typeSlug}/`, {
      method: "POST",
      headers,
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to create record: ${res.statusText}`);
    }
    return res.json();
  }

  async updateRecord(typeSlug: string, recordId: string, version: number, patchData: Record<string, any>): Promise<RecordItem> {
    const headers = { ...this.getHeaders() } as Record<string, string>;
    headers["If-Match"] = `W/"${version}"`;

    const res = await fetch(`${BASE_URL}/o/${typeSlug}/${recordId}/`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(patchData),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to update record: ${res.statusText}`);
    }
    return res.json();
  }

  async listAvailableTransitions(typeSlug: string, recordId: string): Promise<WorkflowTransition[]> {
    const res = await fetch(`${BASE_URL}/o/${typeSlug}/${recordId}/transitions/`, {
      headers: this.getHeaders(),
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.available_transitions || [];
  }

  async executeTransition(typeSlug: string, recordId: string, transitionName: string): Promise<RecordItem> {
    const res = await fetch(`${BASE_URL}/o/${typeSlug}/${recordId}/transitions/${transitionName}/`, {
      method: "POST",
      headers: this.getHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to execute transition: ${res.statusText}`);
    }
    return res.json();
  }
}

export const api = new ForgeApiClient();

