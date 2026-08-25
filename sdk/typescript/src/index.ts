export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export interface RunRequest {
  task: string;
  workspace: string;
  allow_write?: boolean;
  conversation_id?: string;
  project_id?: string;
}

export interface CloudIsolation {
  mode?: "trusted-host" | "container" | "microvm";
  read_only_root?: boolean;
  no_new_privileges?: boolean;
  drop_capabilities?: boolean;
  seccomp?: boolean;
}

export interface CloudResources {
  cpu?: number;
  memory_mb?: number;
  pids?: number;
  disk_mb?: number;
}

export interface CloudTaskRequest {
  task: string;
  workspace?: string;
  repository_url?: string;
  git_ref?: string;
  git_commit?: string;
  model?: string;
  allow_write?: boolean;
  project_id?: string;
  tenant_id?: string;
  idempotency_key?: string;
  isolation?: CloudIsolation;
  resources?: CloudResources;
  egress?: { hosts?: string[]; allow_dns?: boolean };
  runtime?: string;
  bootstrap?: boolean;
  metadata?: Record<string, Json>;
}

export interface SDKOptions {
  baseUrl: string;
  apiKey: string;
  fetchImpl?: typeof fetch;
}

export class JarvisClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: SDKOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.apiKey = options.apiKey;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    let payload: unknown = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = { detail: text.slice(0, 4000) };
      }
    }
    if (!response.ok) {
      const detail = typeof payload === "object" && payload !== null && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : `HTTP ${response.status}`;
      throw new Error(`Jarvis API ${response.status}: ${detail}`);
    }
    return payload as T;
  }

  createRun(request: RunRequest): Promise<Record<string, Json>> {
    return this.request("POST", "/runs", request);
  }

  getRun(runId: string): Promise<Record<string, Json>> {
    return this.request("GET", `/runs/${encodeURIComponent(runId)}`);
  }

  approveRun(runId: string): Promise<Record<string, Json>> {
    return this.request("POST", `/runs/${encodeURIComponent(runId)}/approve`);
  }

  discardRun(runId: string): Promise<Record<string, Json>> {
    return this.request("POST", `/runs/${encodeURIComponent(runId)}/discard`);
  }

  cancelRun(runId: string): Promise<Record<string, Json>> {
    return this.request("POST", `/runs/${encodeURIComponent(runId)}/cancel`);
  }

  submitCloud(request: CloudTaskRequest): Promise<Record<string, Json>> {
    return this.request("POST", "/platform/v09/cloud/tasks", request);
  }

  cloudTask(taskId: string): Promise<Record<string, Json>> {
    return this.request("GET", `/platform/cloud/tasks/${encodeURIComponent(taskId)}`);
  }

  cancelCloud(taskId: string): Promise<Record<string, Json>> {
    return this.request("POST", `/platform/cloud/tasks/${encodeURIComponent(taskId)}/cancel`);
  }

  tenantCapacity(tenantId: string): Promise<Record<string, Json>> {
    return this.request("GET", `/platform/v09/capacity/${encodeURIComponent(tenantId)}`);
  }
}
