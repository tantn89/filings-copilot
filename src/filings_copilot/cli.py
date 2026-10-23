"""Command line: `filings-copilot ingest | ask | serve`."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from filings_copilot.config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _parse_years(spec: str) -> set[int]:
    """'2023-2025' or '2023,2025' -> {2023, 2024, 2025} / {2023, 2025}."""
    years: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-"))
            years.update(range(lo, hi + 1))
        else:
            years.add(int(part))
    return years


@app.command()
def ingest(
    tickers: Annotated[str, typer.Option(help="Comma-separated, e.g. NVDA,MSFT")],
    years: Annotated[str, typer.Option(help="Fiscal years, e.g. 2023-2025")],
    force: Annotated[bool, typer.Option(help="Re-embed filings already ingested")] = False,
) -> None:
    """Download 10-K text and XBRL financials from SEC EDGAR into the local database."""
    from filings_copilot.edgar_client import EdgarClient
    from filings_copilot.embeddings import FastEmbedEmbedder
    from filings_copilot.filings_store import FilingsStore
    from filings_copilot.ingest_pipeline import ingest as run_ingest

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    embedder = FastEmbedEmbedder(settings.embedding_model)
    store = FilingsStore(settings.db_path, embedder.dimension)
    with EdgarClient(
        settings.sec_user_agent,
        settings.sec_max_requests_per_second,
        force_ipv4=settings.sec_force_ipv4,
    ) as edgar:
        report = run_ingest(
            [t.strip().upper() for t in tickers.split(",") if t.strip()],
            _parse_years(years),
            edgar,
            store,
            embedder,
            settings.chunk_size_words,
            settings.chunk_overlap_words,
            force=force,
        )
    store.close()
    typer.echo(
        f"Ingested {report.filings} filings ({report.skipped_filings} already present), "
        f"{report.chunks} chunks, {report.metric_rows} metric rows."
    )
    for warning in report.warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW)


@app.command()
def ask(question: str) -> None:
    """Ask one question and print the answer with its tool trace."""
    from filings_copilot.app_factory import build_runtime

    runtime = build_runtime(get_settings())
    for event in runtime.agent.stream(question):
        if event.type == "tool_call":
            typer.secho(f"-> {event.data['name']}({event.data['input']})", fg=typer.colors.CYAN)
        elif event.type == "tool_result" and event.data["is_error"]:
            typer.secho(f"   error: {event.data['preview']}", fg=typer.colors.RED)
        elif event.type == "answer":
            d = event.data
            typer.echo("\n" + d["answer"] + "\n")
            for c in d["citations"]:
                typer.echo(f"[{c['chunk_id']}] {c['url']}")
            typer.secho(
                f"\n{d['latency_s']}s · {d['input_tokens']} in / {d['output_tokens']} out tokens",
                dim=True,
            )
        elif event.type == "error":
            typer.secho(event.data["message"], fg=typer.colors.RED)
            raise typer.Exit(1)


@app.command("eval")
def run_eval(
    cases_path: Annotated[Path, typer.Option("--cases")] = Path("evals/golden_cases.jsonl"),
    retrieval_only: Annotated[
        bool, typer.Option(help="Score retrieval only: offline, no LLM calls, free.")
    ] = False,
    repeats: Annotated[
        int, typer.Option(min=1, help="Trials per question; 3+ lets regressgate separate noise.")
    ] = 1,
    out_dir: Annotated[Path, typer.Option("--out")] = Path("evals/results"),
    provider: Annotated[str | None, typer.Option(help="Override: anthropic | openrouter")] = None,
    model: Annotated[str | None, typer.Option(help="Override the model id")] = None,
) -> None:
    """Score the system against the golden cases and save the reports.

    Agent runs are regressgate reports: gate two of them with `regressgate compare`.
    """
    import json
    from datetime import datetime

    from regressgate import load_cases
    from regressgate.markdown_report import render_run

    from filings_copilot.embeddings import FastEmbedEmbedder
    from filings_copilot.evaluation import evaluate_agent, evaluate_retrieval
    from filings_copilot.filings_store import FilingsStore
    from filings_copilot.hybrid_retriever import HybridRetriever

    overrides = {k: v for k, v in (("llm_provider", provider), ("llm_model", model)) if v}
    settings = get_settings().model_copy(update=overrides)
    cases = load_cases(cases_path)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if retrieval_only:
        embedder = FastEmbedEmbedder(settings.embedding_model)
        store = FilingsStore(settings.db_path, embedder.dimension, read_only=True)
        retrieval = evaluate_retrieval(HybridRetriever(store, embedder), cases)
        out = out_dir / f"retrieval-{stamp}.json"
        out.write_text(json.dumps(retrieval, indent=2), encoding="utf-8")
        summary = {k: v for k, v in retrieval.items() if k != "per_case"}
        typer.echo(f"retrieval: {json.dumps(summary, indent=2)}")
        typer.echo(f"Report: {out}")
        return

    from filings_copilot.app_factory import build_runtime

    runtime = build_runtime(settings)
    retrieval = evaluate_retrieval(runtime.agent.tools.retriever, cases)
    metadata = {
        "provider": settings.llm_provider, "model": settings.llm_model,
        "effort": settings.llm_effort,
        "retrieval": {k: v for k, v in retrieval.items() if k != "per_case"},
    }
    report = evaluate_agent(runtime.agent, cases, repeats=repeats, metadata=metadata)
    out = report.save(out_dir / f"eval-{stamp}.json")
    typer.echo(render_run(report))
    typer.echo(f"\nretrieval: {json.dumps(metadata['retrieval'])}")
    typer.echo(f"Report: {out}")


@app.command("eval-report")
def eval_report(
    results_dir: Annotated[Path, typer.Option("--results")] = Path("evals/results"),
) -> None:
    """Print a markdown table comparing the latest agent eval of each model."""
    from filings_copilot.evaluation import comparison_table, load_latest_agent_reports

    typer.echo(comparison_table(load_latest_agent_reports(results_dir)))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the HTTP API."""
    import uvicorn

    uvicorn.run("filings_copilot.http_api:create_app", host=host, port=port, factory=True)


if __name__ == "__main__":
    app()
