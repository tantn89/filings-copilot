"use client";

import ReactMarkdown from "react-markdown";
import type { Run } from "@/lib/agent-types";
import { TOOL_LABELS, formatCost, formatSeconds, formatTokens, linkCitations } from "@/lib/format";
import styles from "./copilot.module.css";

type Props = {
  run: Run;
  active: boolean;
  onSelect: () => void;
  onCite: (chunkId: string) => void;
};

function RunStatus({ run }: { run: Run }) {
  if (run.status !== "running") return null;
  const current = run.steps.at(-1);
  const label = current && !current.result
    ? `${TOOL_LABELS[current.name] ?? current.name}…`
    : run.steps.length ? "Writing the answer…" : "Planning…";
  return (
    <p className={styles.status} role="status">
      <span className={styles.pulse} aria-hidden />
      {label}
      {run.steps.length > 0 && <span className="num"> · step {run.steps.length}</span>}
    </p>
  );
}

export function RunCard({ run, active, onSelect, onCite }: Props) {
  const linked = run.answer ? linkCitations(run.answer.answer, run.answer.citations) : null;
  const cost = run.answer ? formatCost(run.answer.cost_usd) : null;

  return (
    <article
      className={`${styles.run} ${active ? styles.runActive : ""}`}
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
    >
      <h2 className={styles.runQuestion}>{run.question}</h2>
      <RunStatus run={run} />

      {linked && (
        <div className={styles.answer}>
          <ReactMarkdown
            components={{
              a: ({ href, children }) => {
                const id = href?.startsWith("#src-") ? href.slice(5) : null;
                if (!id) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
                return (
                  <button
                    type="button"
                    className={styles.citeChip}
                    aria-label={`Show source ${String(children)}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect();
                      onCite(id);
                    }}
                  >
                    {children}
                  </button>
                );
              },
            }}
          >
            {linked.text}
          </ReactMarkdown>
        </div>
      )}

      {run.status === "error" && <p className={styles.runError} role="alert">{run.error}</p>}

      {run.answer && (
        <p className={`${styles.stats} num`}>
          <span>{formatSeconds(run.answer.latency_s)}</span>
          <span>{run.steps.length} tool {run.steps.length === 1 ? "call" : "calls"}</span>
          <span>
            {formatTokens(run.answer.input_tokens + run.answer.output_tokens)} tokens
          </span>
          {cost && <span>{cost}</span>}
          <span>{linked?.ordered.length ?? 0} sources cited</span>
        </p>
      )}
    </article>
  );
}
