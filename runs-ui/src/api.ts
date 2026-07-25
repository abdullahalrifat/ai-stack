export type Run = { id: string; status: string; task: string; model: string; requested_workspace: string; allow_write: boolean; conversation_id?: string | null; answer?: string; error?: string; created_at: string; completed_at?: string };
export type RunEvent = { id?: number; event_type: string; payload: Record<string, unknown>; created_at?: string; status?: string };

const base = "/api";
const headers = (key: string) => ({ "Content-Type": "application/json", Authorization: `Bearer ${key}` });
export async function api<T>(key: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, { ...init, headers: { ...headers(key), ...(init?.headers || {}) } });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}
export async function streamEvents(key: string, runId: string, after: number, onEvent: (event: RunEvent) => void): Promise<number> {
  const response = await fetch(`${base}/runs/${runId}/events?after=${after}`, { headers: headers(key) });
  if (!response.ok || !response.body) throw new Error(await response.text());
  let cursor = after, buffer = ""; const reader = response.body.getReader(), decoder = new TextDecoder();
  while (true) {
    const { value, done } = await reader.read(); if (done) return cursor;
    buffer += decoder.decode(value, { stream: true }); const frames = buffer.split("\n\n"); buffer = frames.pop() || "";
    for (const frame of frames) {
      const raw = frame.split("\n").filter((line) => line.startsWith("data: ")).map((line) => line.slice(6)).join("\n");
      if (!raw) continue; const event = JSON.parse(raw) as RunEvent; if (event.id) cursor = Math.max(cursor, event.id); onEvent(event);
    }
  }
}
