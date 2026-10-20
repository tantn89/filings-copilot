// Shapes of the events streamed by POST /ask/stream (see src/filings_copilot/research_agent.py).

export type ToolCallEvent = { id: string; name: string; input: Record<string, unknown> };

export type ToolResultEvent = {
  id: string;
  name: string;
  is_error: boolean;
  latency_s: number;
  preview: string;
};

export type Citation = {
  chunk_id: string;
  ticker: string;
  fiscal_year: number;
  section_item: string;
  section_title: string;
  text: string;
  url: string;
  score: number;
};

export type AnswerEvent = {
  answer: string;
  citations: Citation[];
  stop_reason: string;
  latency_s: number;
  cost_usd: number | null;
  input_tokens: number;
  output_tokens: number;
};

export type AgentEvent =
  | { type: "tool_call"; data: ToolCallEvent }
  | { type: "tool_result"; data: ToolResultEvent }
  | { type: "answer"; data: AnswerEvent }
  | { type: "error"; data: { message: string } };

export type TraceStep = ToolCallEvent & { result?: ToolResultEvent };

export type Run = {
  id: string;
  question: string;
  status: "running" | "done" | "error";
  steps: TraceStep[];
  answer?: AnswerEvent;
  error?: string;
};

export type Company = { ticker: string; name: string; fiscal_years_with_10k_text: number[] };
