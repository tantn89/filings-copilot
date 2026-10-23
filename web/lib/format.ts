import type { AnswerEvent, Citation, Company } from "./agent-types";

export const TOOL_LABELS: Record<string, string> = {
  query_financials: "SQL over XBRL financials",
  search_filings: "Search 10-K text",
  list_companies: "Check available data",
};

export function formatSeconds(s: number): string {
  return s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(1)} s`;
}

export function formatTokens(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

export function formatCost(usd: number | null): string | null {
  if (usd === null) return null;
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`;
}

/** Put each major SQL clause on its own line so model-written queries are scannable. */
export function formatSql(sql: string): string {
  return sql
    .replace(/\s+/g, " ")
    .replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|LIMIT|JOIN|LEFT JOIN|WITH|UNION|HAVING)\s+/gi,
      (_, clause: string) => `\n${clause.toUpperCase()} `)
    .trim();
}

export function sourceLabel(c: Citation): string {
  return `${c.ticker} · FY${c.fiscal_year} · Item ${c.section_item}`;
}

export function coverageSummary(companies: Company[]): string {
  const years = companies.flatMap((c) => c.fiscal_years_with_10k_text);
  if (years.length === 0) return "";
  const lo = Math.min(...years);
  const hi = Math.max(...years);
  const span = lo === hi ? `FY${lo}` : `FY${lo}–FY${hi}`;
  return `${companies.map((c) => c.ticker).join(", ")} · 10-K text ${span}`;
}

// Same id format and grouping rules as the backend's citation parser (research_agent.py).
const CHUNK_ID = /^[A-Z.\-]+-\d{4}-\d{1,2}[A-C]?-\d{4}$/;

export const UNVERIFIED_HREF_PREFIX = "#unverified-";

export type LinkedAnswer = { text: string; ordered: Citation[] };

/**
 * Number citations in order of first appearance and rewrite "[NVDA-2025-7-0012]" (or a
 * group such as "[NVDA-2025-7-0012, NVDA-2025-1A-0003]") into markdown links
 * "[1](#src-NVDA-2025-7-0012)" that the answer renderer turns into chips. Ids in a valid
 * format that were never retrieved become "#unverified-<id>" markers rather than vanishing.
 * Brackets that are not purely ids ("[1]", "[note]") are left as written.
 */
export function linkCitations(answer: string, citations: Citation[]): LinkedAnswer {
  const byId = new Map(citations.map((c) => [c.chunk_id, c]));
  const order: string[] = [];
  const text = answer.replace(/\[([^\]]+)\]/g, (match, inner: string) => {
    const ids = inner.split(/[,;\s]+/).filter(Boolean);
    if (ids.length === 0 || !ids.every((id) => CHUNK_ID.test(id))) return match;
    return ids
      .map((id) => {
        if (!byId.has(id)) return `[unverified](${UNVERIFIED_HREF_PREFIX}${id})`;
        if (!order.includes(id)) order.push(id);
        return `[${order.indexOf(id) + 1}](#src-${id})`;
      })
      .join("");
  });
  const ordered = order.map((id) => byId.get(id)!);
  return { text, ordered };
}

// Answers are immutable once streamed, so the run card and the evidence panel share one result.
const linkedAnswers = new WeakMap<AnswerEvent, LinkedAnswer>();

export function linkAnswer(answer: AnswerEvent): LinkedAnswer {
  let linked = linkedAnswers.get(answer);
  if (!linked) {
    linked = linkCitations(answer.answer, answer.citations);
    linkedAnswers.set(answer, linked);
  }
  return linked;
}
