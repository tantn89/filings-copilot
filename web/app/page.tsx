"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import styles from "@/components/copilot.module.css";
import { EvidencePanel, type EvidenceTab, type SourceFocus } from "@/components/evidence-panel";
import { QuestionComposer } from "@/components/question-composer";
import { RunCard } from "@/components/run-card";
import { fetchCompanies, streamAnswer } from "@/lib/agent-api-client";
import type { AgentEvent, Company, Run } from "@/lib/agent-types";
import { coverageSummary } from "@/lib/format";

const EXAMPLES = [
  "Why did NVIDIA's gross margin increase in fiscal 2025, and to what level?",
  "Compare the gross margins of NVIDIA, Microsoft and Apple in fiscal 2025.",
  "What risks does Microsoft identify from developing and deploying AI?",
  "How much did Apple spend on research and development in fiscal 2025?",
];

const REPO_URL = "https://github.com/tantn89/filings-copilot";

// crypto.randomUUID() exists only in secure contexts, so it throws on a LAN dev URL.
let runCounter = 0;
function nextRunId(): string {
  runCounter += 1;
  return `run-${Date.now()}-${runCounter}`;
}

/** Fold one streamed event into the run it belongs to. */
function applyEvent(run: Run, event: AgentEvent): Run {
  switch (event.type) {
    case "tool_call":
      return { ...run, steps: [...run.steps, event.data] };
    case "tool_result":
      return {
        ...run,
        steps: run.steps.map((s) => (s.id === event.data.id ? { ...s, result: event.data } : s)),
      };
    case "answer":
      return { ...run, status: "done", answer: event.data };
    case "error":
      return { ...run, status: "error", error: event.data.message };
    default:
      return run;
  }
}

export default function Home() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tab, setTab] = useState<EvidenceTab>("trace");
  const [activeSource, setActiveSource] = useState<SourceFocus | null>(null);
  const [companies, setCompanies] = useState<Company[] | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Mirrors activeId for stream callbacks, which outlive the render that created them.
  const activeIdRef = useRef<string | null>(null);
  const sourceSeq = useRef(0);

  useEffect(() => {
    fetchCompanies()
      .then(setCompanies)
      .catch(() => setApiError("The research API is not reachable right now."));
  }, []);

  // Abort the in-flight stream only on a real unmount. StrictMode (dev) runs cleanup and
  // setup back to back, so the abort is deferred and skipped once the effect is set up again.
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      setTimeout(() => {
        if (!mountedRef.current) abortRef.current?.abort();
      }, 0);
    };
  }, []);

  const busy = runs.some((r) => r.status === "running");
  const activeRun = runs.find((r) => r.id === activeId) ?? null;

  const updateRun = useCallback((id: string, fn: (run: Run) => Run) => {
    setRuns((prev) => prev.map((r) => (r.id === id ? fn(r) : r)));
  }, []);

  const ask = useCallback(async (question: string) => {
    const id = nextRunId();
    setRuns((prev) => [{ id, question, status: "running", steps: [] }, ...prev]);
    activeIdRef.current = id;
    setActiveId(id);
    setTab("trace");
    setActiveSource(null);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamAnswer(question, (event) => {
        updateRun(id, (run) => applyEvent(run, event));
        // Jump to Sources only if the user is still looking at this run.
        if (event.type === "answer" && event.data.citations?.length && activeIdRef.current === id) {
          setTab("sources");
        }
      }, controller.signal);
      // A stream that closes without an answer or error event still has to end the run.
      updateRun(id, (run) => run.status === "running"
        ? { ...run, status: "error", error: "The connection closed before an answer arrived." }
        : run);
    } catch (e) {
      const message = controller.signal.aborted
        ? "Cancelled."
        : e instanceof Error ? e.message : String(e);
      // Every run reaches a terminal state; an answer that already arrived is kept.
      updateRun(id, (run) => run.status === "running" ? { ...run, status: "error", error: message } : run);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [updateRun]);

  // Shareable links: /?q=<question> asks it on load (used by the README demo links).
  const askedFromUrl = useRef(false);
  useEffect(() => {
    if (askedFromUrl.current) return;
    askedFromUrl.current = true;
    const q = new URLSearchParams(window.location.search).get("q")?.trim();
    if (q && q.length >= 3) void ask(q.slice(0, 1000));
  }, [ask]);

  const selectRun = useCallback((runId: string) => {
    if (activeIdRef.current === runId) return;
    activeIdRef.current = runId;
    setActiveId(runId);
    setActiveSource(null);
  }, []);

  const cite = useCallback((chunkId: string) => {
    setTab("sources");
    sourceSeq.current += 1;
    setActiveSource({ id: chunkId, seq: sourceSeq.current });
  }, []);

  const coverage = companies ? coverageSummary(companies) : "";

  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <div className={styles.brand}>
          <span className={styles.wordmark}>filings-copilot</span>
          <span className={styles.tagline}>Research agent for SEC 10-K filings</span>
        </div>
        <nav className={styles.topLinks} aria-label="Project links">
          <a href={REPO_URL} target="_blank" rel="noreferrer">GitHub</a>
          <a href={`${REPO_URL}#results`} target="_blank" rel="noreferrer">Eval results</a>
        </nav>
      </header>

      <main className={styles.workspace}>
        <div className={styles.mainColumn}>
          {runs.length === 0 && (
            <section className={styles.intro}>
              <h1 className={styles.introTitle}>
                Ask a 10-K. <span className={styles.titleLine}>Get numbers you can check.</span>
              </h1>
              <p className={styles.introText}>
                Figures come from audited XBRL data through SQL. Explanations are quoted from
                the filing and cited, so every claim links back to its source.
              </p>
              {coverage && <p className={styles.coverage}>{coverage}</p>}
            </section>
          )}

          {apiError && (
            <p className={styles.notice} role="alert">
              <strong>Offline.</strong> {apiError} You can still run it locally — see the README.
            </p>
          )}

          <QuestionComposer busy={busy} onAsk={ask} />

          {runs.length === 0 ? (
            <section className={styles.examples} aria-labelledby="examples-label">
              <h2 id="examples-label" className={styles.examplesLabel}>Try a question</h2>
              <ul className={styles.exampleList}>
                {EXAMPLES.map((q) => (
                  <li key={q}>
                    <button type="button" className={styles.exampleButton} disabled={busy}
                      onClick={() => ask(q)}>
                      {q}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <ol className={styles.runList} aria-label="Questions and answers, newest first">
              {runs.map((run) => (
                <li key={run.id}>
                  <RunCard run={run} active={run.id === activeId} onSelect={selectRun} onCite={cite} />
                </li>
              ))}
            </ol>
          )}
        </div>

        <aside className={styles.panelColumn}>
          <EvidencePanel run={activeRun} tab={tab} onTab={setTab} activeSource={activeSource} />
        </aside>
      </main>
    </div>
  );
}
