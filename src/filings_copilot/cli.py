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
    cases_path: Annotated[Path, typer.Option("--cases")] = Path("evals/golden_questions.jsonl"),
    retrieval_only: Annotated[
        bool, typer.Option(help="Score retrieval only: offline, no LLM calls, free.")
    ] = False,
    out_dir: Annotated[Path, typer.Option("--out")] = Path("evals/results"),
    provider: Annotated[str | None, typer.Option(help="Override: anthropic | openrouter")] = None,
    model: Annotated[str | None, typer.Option(help="Override the model id")] = None,
) -> None:
    """Score the system against the golden question set and save a JSON report."""
    import json
    from datetime import datetime

    from filings_copilot.embeddings import FastEmbedEmbedder
    from filings_copilot.evaluation import evaluate_agent, evaluate_retrieval, load_cases
    from filings_copilot.filings_store import FilingsStore
    from filings_copilot.hybrid_retriever import HybridRetriever

    overrides = {k: v for k, v in (("llm_provider", provider), ("llm_model", model)) if v}
    settings = get_settings().model_copy(update=overrides)
    cases = load_cases(cases_path)
    report: dict[str, object] = {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "effort": settings.llm_effort,
    }

    if retrieval_only:
        embedder = FastEmbedEmbedder(settings.embedding_model)
        store = FilingsStore(settings.db_path, embedder.dimension, read_only=True)
        report["retrieval"] = evaluate_retrieval(HybridRetriever(store, embedder), cases)
    else:
        from filings_copilot.app_factory import build_runtime

        runtime = build_runtime(settings)
        report["retrieval"] = evaluate_retrieval(runtime.agent.tools.retriever, cases)
        report["agent"] = evaluate_agent(runtime.agent, cases)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for section in ("retrieval", "agent"):
        if section in report:
            summary = {k: v for k, v in report[section].items() if k != "per_case"}  # type: ignore[attr-defined]
            typer.echo(f"{section}: {json.dumps(summary, indent=2)}")
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
