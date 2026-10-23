"use client";

import { useEffect, useRef, type KeyboardEvent } from "react";
import type { Citation, Run, TraceStep } from "@/lib/agent-types";
import { TOOL_LABELS, formatSeconds, formatSql, linkAnswer, sourceLabel } from "@/lib/format";
import styles from "./copilot.module.css";

export type EvidenceTab = "trace" | "sources";

/** The source to scroll to; `seq` changes on every chip click so a repeat click re-scrolls. */
export type SourceFocus = { id: string; seq: number };

const TABS: readonly EvidenceTab[] = ["trace", "sources"];
const PANEL_ID = "evidence-tabpanel";

type Props = {
  run: Run | null;
  tab: EvidenceTab;
  onTab: (tab: EvidenceTab) => void;
  activeSource: SourceFocus | null;
};

/** Tool input comes straight from the model, unvalidated: coerce any value to a list of strings. */
function toList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  return value == null ? [] : [String(value)];
}

function StepDetail({ step }: { step: TraceStep }) {
  const input = step.input;
  if (step.name === "query_financials") {
    return <pre className={styles.code}>{formatSql(String(input.sql ?? ""))}</pre>;
  }
  if (step.name === "search_filings") {
    const filters = [
      ...toList(input.tickers),
      ...toList(input.fiscal_years).map((y) => `FY${y}`),
      ...toList(input.section_items).map((s) => `Item ${s}`),
    ];
    return (
      <>
        <pre className={styles.code}>{String(input.query ?? "")}</pre>
        {filters.length > 0 && (
          <div className={styles.filters}>
            {filters.map((f, i) => <span key={`${i}-${f}`} className={styles.filterChip}>{f}</span>)}
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

function Sources({ cited, activeSource }: { cited: Citation[]; activeSource: SourceFocus | null }) {
  const refs = useRef<Record<string, HTMLLIElement | null>>({});
  const activeId = activeSource?.id ?? null;

  useEffect(() => {
    if (!activeSource) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    refs.current[activeSource.id]?.scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
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
          className={`${styles.source} ${activeId === c.chunk_id ? styles.sourceActive : ""}`}
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
  const cited = run?.answer ? linkAnswer(run.answer).ordered : [];

  // Roving tabindex: only the selected tab is in the Tab order; arrow keys move between tabs.
  function onTabKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = TABS[(TABS.indexOf(tab) + step + TABS.length) % TABS.length];
    onTab(next);
    document.getElementById(`tab-${next}`)?.focus();
  }

  return (
    <section className={styles.panel} aria-labelledby="evidence-title">
      <header className={styles.panelHeader}>
        <h2 id="evidence-title" className={styles.panelTitle}>Evidence</h2>
        {run && (
          <div className={styles.tabs} role="tablist" aria-label="Evidence view" onKeyDown={onTabKeyDown}>
            {TABS.map((t) => (
              <button
                key={t}
                id={`tab-${t}`}
                type="button"
                role="tab"
                aria-selected={tab === t}
                aria-controls={PANEL_ID}
                tabIndex={tab === t ? 0 : -1}
                className={`${styles.tab} ${tab === t ? styles.tabActive : ""}`}
                onClick={() => onTab(t)}
              >
                {t === "trace" ? `Trace ${run.steps.length}` : `Sources ${cited.length}`}
              </button>
            ))}
          </div>
        )}
      </header>
      <div
        className={styles.panelBody}
        id={run ? PANEL_ID : undefined}
        role={run ? "tabpanel" : undefined}
        aria-labelledby={run ? `tab-${tab}` : undefined}
        tabIndex={run ? 0 : undefined}
      >
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
