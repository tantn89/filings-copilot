import type { AgentEvent, Company } from "./agent-types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // Body was not JSON; fall through to the status text.
  }
  return `Request failed (${response.status} ${response.statusText})`;
}

export async function fetchCompanies(): Promise<Company[]> {
  const response = await fetch(`${API_URL}/companies`);
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

/** Parse one server-sent-event block ("event: x\ndata: {...}") into an AgentEvent. */
function parseBlock(block: string): AgentEvent | null {
  let type = "";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith(":")) continue; // keep-alive comment
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!type || data.length === 0) return null;
  return { type, data: JSON.parse(data.join("\n")) } as AgentEvent;
}

/**
 * POST the question and call `onEvent` for every streamed agent event.
 * EventSource only supports GET, so the stream is read from fetch() directly.
 */
export async function streamAnswer(
  question: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!response.ok || !response.body) throw new Error(await errorMessage(response));

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = parseBlock(block);
      if (event) onEvent(event);
    }
  }
  const last = parseBlock(buffer);
  if (last) onEvent(last);
}
