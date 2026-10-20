"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import styles from "@/components/copilot.module.css";
import { EvidencePanel, type EvidenceTab } from "@/components/evidence-panel";
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
  }
}

export default function Home() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tab, setTab] = useState<EvidenceTab>("trace");
  const [activeSource, setActiveSource] = useState<string | null>(null);
  const [companies, setCompanies] = useState<Company[] | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    fetchCompanies()
      .then(setCompanies)
      .catch(() => setApiError("The research API is not reachable right now."));
    return () => abortRef.current?.abort();
  }, []);

  const busy = runs.some((r) => r.status === "running");
  const activeRun = runs.find((r) => r.id === activeId) ?? null;

  const updateRun = useCallback((id: string, fn: (run: Run) => Run) => {
    setRuns((prev) => prev.map((r) => (r.id === id ? fn(r) : r)));
  }, []);

  const ask = useCallback(async (question: string) => {
    const id = crypto.randomUUID();
    setRuns((prev) => [{ id, question, status: "running", steps: [] }, ...prev]);
    setActiveId(id);
    setTab("trace");
    setActiveSource(null);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamAnswer(question, (event) => {
        updateRun(id, (run) => applyEvent(run, event));
        if (event.type === "answer" && event.data.citations.length) setTab("sources");
      }, controller.signal);
      // A stream that closes without an answer or error event still has to end the run.
      updateRun(id, (run) => run.status === "running"
        ? { ...run, status: "error", error: "The connection closed before an answer arrived." }
        : run);
    } catch (e) {
      if (controller.signal.aborted) return;
      const message = e instanceof Error ? e.message : String(e);
      updateRun(id, (run) => ({ ...run, status: "error", error: message }));
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

  const cite = useCallback((chunkId: string) => {
    setTab("sources");
    setActiveSource(chunkId);
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
                  <RunCard
                    run={run}
                    active={run.id === activeId}
                    onSelect={() => { if (run.id !== activeId) { setActiveId(run.id); setActiveSource(null); } }}
                    onCite={cite}
                  />
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
