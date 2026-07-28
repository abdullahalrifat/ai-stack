import type { RunEvent } from "./api";

export function answerAfterEvent(current: string, event: RunEvent): string {
  if (event.event_type === "output_delta") {
    return current + String(event.payload.content || "");
  }
  if (
    event.event_type === "run_completed" &&
    typeof event.payload.answer === "string"
  ) {
    return event.payload.answer;
  }
  return current;
}

export function diffAfterEvent(current: string, event: RunEvent): string {
  return event.event_type === "diff_ready"
    ? String(event.payload.diff || "")
    : current;
}

export function sourceUrls(text: string): string[] {
  const matches = text.match(/https?:\/\/[^\s)\]}>,]+/g) || [];
  return [...new Set(matches.map((url) => url.replace(/[.!?:;]+$/, "")))];
}
