"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";
import styles from "./copilot.module.css";

type Props = { busy: boolean; onAsk: (question: string) => void };

export function QuestionComposer({ busy, onAsk }: Props) {
  const [value, setValue] = useState("");
  const question = value.trim();
  const canSubmit = !busy && question.length >= 3;

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!canSubmit) return;
    onAsk(question);
    setValue("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) submit(event);
  }

  return (
    <form className={styles.composer} onSubmit={submit}>
      <label htmlFor="question" className="visually-hidden">
        Question about the filings
      </label>
      <textarea
        id="question"
        className={styles.composerInput}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask about revenue, margins, risks or strategy…"
        rows={2}
        maxLength={1000}
      />
      <div className={styles.composerFooter}>
        <span className={styles.hint}>Enter to ask · Shift+Enter for a new line</span>
        <button type="submit" className={styles.primaryButton} disabled={!canSubmit}>
          {busy ? "Researching…" : "Ask"}
        </button>
      </div>
    </form>
  );
}
