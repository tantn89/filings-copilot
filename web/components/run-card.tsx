"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import type { Run } from "@/lib/agent-types";
import {
  TOOL_LABELS,
  UNVERIFIED_HREF_PREFIX,
  formatCost,
  formatSeconds,
  formatTokens,
  linkAnswer,
} from "@/lib/format";
import styles from "./copilot.module.css";

type Props = {
  run: Run;
  active: boolean;
  onSelect: (runId: string) => void;
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

function RunCardView({ run, active, onSelect, onCite }: Props) {
  const linked = run.answer ? linkAnswer(run.answer) : null;
  const cost = run.answer ? formatCost(run.answer.cost_usd) : null;

  return (
    // The article click is a mouse convenience; the question button is the keyboard route.
    <article
      className={`${styles.run} ${active ? styles.runActive : ""}`}
      onClick={() => onSelect(run.id)}
      aria-current={active ? "true" : undefined}
    >
      <h2 className={styles.runQuestion}>
        <button
          type="button"
          className={styles.runSelect}
          aria-pressed={active}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(run.id);
          }}
        >
          {run.question}
        </button>
      </h2>
      <RunStatus run={run} />

      {linked && (
        <div className={styles.answer}>
          <ReactMarkdown
            disallowedElements={["img"]}
            unwrapDisallowed
            components={{
              a: ({ href, children }) => {
                if (href?.startsWith(UNVERIFIED_HREF_PREFIX)) {
                  const chunkId = href.slice(UNVERIFIED_HREF_PREFIX.length);
                  return (
                    <span
                      className={styles.unverified}
                      title={`Cited passage ${chunkId} was not retrieved by the agent, so it cannot be checked.`}
                    >
                      unverified
                      <span className="visually-hidden"> citation: source passage not found</span>
                    </span>
                  );
                }
                const id = href?.startsWith("#src-") ? href.slice(5) : null;
                if (!id) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
                return (
                  <button
                    type="button"
                    className={styles.citeChip}
                    aria-label={`Show source ${String(children)}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(run.id);
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

// Every streamed event replaces one run; memo keeps the other cards from re-parsing markdown.
export const RunCard = memo(RunCardView);
