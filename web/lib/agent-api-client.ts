import type { AgentEvent, Citation, Company } from "./agent-types";

// trim() also strips invisible characters (e.g. a byte-order mark) that some shells add
// when the value is piped into `vercel env add`; one turned the URL into a relative path.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000")
  .trim()
  .replace(/\/$/, "");

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

type JsonObject = Record<string, unknown>;

const isObject = (v: unknown): v is JsonObject =>
  typeof v === "object" && v !== null && !Array.isArray(v);
const asString = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
const asNumber = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

function toCitation(v: unknown): Citation | null {
  if (!isObject(v) || typeof v.chunk_id !== "string") return null;
  return {
    chunk_id: v.chunk_id,
    ticker: asString(v.ticker),
    fiscal_year: asNumber(v.fiscal_year),
    section_item: asString(v.section_item),
    section_title: asString(v.section_title),
    text: asString(v.text),
    url: asString(v.url),
    score: asNumber(v.score),
  };
}

/**
 * Coerce a decoded event payload into the typed shape the UI renders. The stream is an
 * external boundary (the API deploys separately), so unknown event types are dropped and
 * missing or mistyped fields fall back to safe defaults instead of crashing a render.
 */
function toEvent(type: string, d: JsonObject): AgentEvent | null {
  switch (type) {
    case "tool_call":
      return {
        type,
        data: { id: asString(d.id), name: asString(d.name), input: isObject(d.input) ? d.input : {} },
      };
    case "tool_result":
      return {
        type,
        data: {
          id: asString(d.id),
          name: asString(d.name),
          is_error: d.is_error === true,
          latency_s: asNumber(d.latency_s),
          preview: asString(d.preview),
        },
      };
    case "answer":
      return {
        type,
        data: {
          answer: asString(d.answer),
          citations: Array.isArray(d.citations)
            ? d.citations.map(toCitation).filter((c): c is Citation => c !== null)
            : [],
          stop_reason: asString(d.stop_reason),
          latency_s: asNumber(d.latency_s),
          cost_usd: typeof d.cost_usd === "number" && Number.isFinite(d.cost_usd) ? d.cost_usd : null,
          input_tokens: asNumber(d.input_tokens),
          output_tokens: asNumber(d.output_tokens),
        },
      };
    case "error":
      return { type, data: { message: asString(d.message) || "The research agent failed." } };
    default:
      return null;
  }
}

/** Parse one server-sent-event block ("event: x\ndata: {...}"); null for anything unusable. */
function parseBlock(block: string): AgentEvent | null {
  let type = "";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith(":")) continue; // keep-alive comment
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!type || data.length === 0) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    return null; // one malformed block should not end the whole stream
  }
  return isObject(payload) ? toEvent(type, payload) : null;
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
  try {
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
  } finally {
    // On an early exit (throw or abort) this stops the download so the backend stops the
    // agent; after a clean end it is a no-op. A cancel failure must not mask the original error.
    reader.cancel().catch(() => {});
  }
}
