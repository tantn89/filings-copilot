"use client";

import { useEffect, useRef } from "react";
import type { Citation, Run, TraceStep } from "@/lib/agent-types";
import { TOOL_LABELS, formatSeconds, formatSql, linkCitations, sourceLabel } from "@/lib/format";
import styles from "./copilot.module.css";

export type EvidenceTab = "trace" | "sources";

type Props = {
  run: Run | null;
  tab: EvidenceTab;
  onTab: (tab: EvidenceTab) => void;
  activeSource: string | null;
};

function StepDetail({ step }: { step: TraceStep }) {
  const input = step.input;
  if (step.name === "query_financials") {
    return <pre className={styles.code}>{formatSql(String(input.sql ?? ""))}</pre>;
  }
  if (step.name === "search_filings") {
    const filters = [
      ...((input.tickers as string[]) ?? []),
      ...((input.fiscal_years as number[]) ?? []).map((y) => `FY${y}`),
      ...((input.section_items as string[]) ?? []).map((s) => `Item ${s}`),
    ];
    return (
      <>
        <pre className={styles.code}>{String(input.query ?? "")}</pre>
        {filters.length > 0 && (
          <div className={styles.filters}>
            {filters.map((f) => <span key={f} className={styles.filterChip}>{f}</span>)}
          </div>
        )}
      </>
    );
  }
  return null;
}

function Trace({ steps }: { steps: TraceStep[] }) {
  return (
    <ol className={styles.trace} aria-label="Tool calls in order">
      {steps.map((step) => {
        const state = !step.result ? "" : step.result.is_error ? styles.stepFailed : styles.stepDone;
        return (
          <li key={step.id} className={`${styles.step} ${state}`}>
            <span className={styles.stepDot} aria-hidden />
            <div className={styles.stepHead}>
              <span className={styles.stepName}>{TOOL_LABELS[step.name] ?? step.name}</span>
              <span className={`${styles.stepMeta} num`}>
                {step.result ? formatSeconds(step.result.latency_s) : "running"}
              </span>
            </div>
            <StepDetail step={step} />
            {step.result?.is_error && <p className={styles.stepError}>{step.result.preview}</p>}
          </li>
        );
      })}
    </ol>
  );
}

function Sources({ cited, activeSource }: { cited: Citation[]; activeSource: string | null }) {
  const refs = useRef<Record<string, HTMLLIElement | null>>({});

  useEffect(() => {
    if (activeSource) refs.current[activeSource]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeSource]);

  if (cited.length === 0) {
    return <p className={styles.panelEmpty}>The answer did not cite any filing passages.</p>;
  }
  return (
    <ol className={styles.sources}>
      {cited.map((c, i) => (
        <li
          key={c.chunk_id}
          id={`src-${c.chunk_id}`}
          ref={(el) => { refs.current[c.chunk_id] = el; }}
          className={`${styles.source} ${activeSource === c.chunk_id ? styles.sourceActive : ""}`}
        >
          <div className={styles.sourceHead}>
            <span className={styles.sourceIndex}>{i + 1}</span>
            <span>{sourceLabel(c)}</span>
          </div>
          <span className={styles.sourceSection}>{c.section_title}</span>
          <p className={styles.sourceText}>{c.text}</p>
          <a className={styles.sourceLink} href={c.url} target="_blank" rel="noreferrer">
            Open filing on SEC.gov ↗
          </a>
        </li>
      ))}
    </ol>
  );
}

function HowItWorks() {
  return (
    <div className={styles.panelEmpty}>
      <p>Every answer shows its work here.</p>
      <ol className={styles.howList}>
        <li className={styles.howItem}>
          <span>
            <strong>Numbers come from SQL</strong>
            Audited XBRL facts in DuckDB. The model writes the query; a parser-based guard
            only lets read-only SELECTs through.
          </span>
        </li>
        <li className={styles.howItem}>
          <span>
            <strong>Explanations come from the filing</strong>
            Hybrid keyword and semantic search over Items 1, 1A, 7 and 7A of each 10-K.
          </span>
        </li>
        <li className={styles.howItem}>
          <span>
            <strong>Claims are cited</strong>
            Each citation links to the passage and the original filing on SEC.gov.
          </span>
        </li>
      </ol>
    </div>
  );
}

export function EvidencePanel({ run, tab, onTab, activeSource }: Props) {
  const cited = run?.answer ? linkCitations(run.answer.answer, run.answer.citations).ordered : [];

  return (
    <section className={styles.panel} aria-labelledby="evidence-title">
      <header className={styles.panelHeader}>
        <h2 id="evidence-title" className={styles.panelTitle}>Evidence</h2>
        {run && (
          <div className={styles.tabs} role="tablist" aria-label="Evidence view">
            {(["trace", "sources"] as const).map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={tab === t}
                className={`${styles.tab} ${tab === t ? styles.tabActive : ""}`}
                onClick={() => onTab(t)}
              >
                {t === "trace" ? `Trace ${run.steps.length}` : `Sources ${cited.length}`}
              </button>
            ))}
          </div>
        )}
      </header>
      <div className={styles.panelBody} role={run ? "tabpanel" : undefined}>
        {!run && <HowItWorks />}
        {run && tab === "trace" && (
          run.steps.length ? <Trace steps={run.steps} />
            : <p className={styles.panelEmpty}>{run.status === "running" ? "Waiting for the first tool call…" : "No tools were called."}</p>
        )}
        {run && tab === "sources" && <Sources cited={cited} activeSource={activeSource} />}
      </div>
    </section>
  );
}
