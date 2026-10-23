"use client";

import { useEffect } from "react";
import styles from "@/components/copilot.module.css";

/** Route-level error boundary: a render fault shows a recoverable notice, not a blank page. */
export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className={styles.shell}>
      <main className={styles.errorPage}>
        <p className={styles.notice} role="alert">
          <strong>Something went wrong.</strong> The page hit an unexpected error while showing
          a result.
        </p>
        <button type="button" className={styles.primaryButton} onClick={reset}>
          Try again
        </button>
      </main>
    </div>
  );
}
