# Codebase summary

Navigation only. Read the module docstrings for behaviour; the README `## Project layout`
lists the modules.

## Entry points

| Surface | Start here |
|---|---|
| CLI (`ingest`, `ask`, `eval`, `eval-report`, `serve`) | `src/filings_copilot/cli.py` |
| HTTP API | `src/filings_copilot/http_api.py` (`create_app`, an app factory) |
| Wiring of store, retriever, tools, LLM client and agent | `src/filings_copilot/app_factory.py` (`build_runtime`) |
| Settings and every `FILINGS_*` variable | `src/filings_copilot/config.py` (`Settings`); example values in `.env.example` |
| Web UI | `web/app/page.tsx` |
| Cloud Run deploy | `scripts/deploy_cloud_run.py` |
| Golden questions | `evals/golden_questions.jsonl` |

## Where each concern lives

| Concern | Source | Guarding tests |
|---|---|---|
| SEC access, paging, retries | `edgar_client.py` | `test_edgar_client.py`, `test_edgar_paging_retries_and_fiscal_years.py` |
| Fiscal-year naming | `fiscal_calendar.py` | `test_edgar_paging_retries_and_fiscal_years.py` |
| HTML to sections and chunks | `tenk_section_parser.py`, `text_chunker.py` | `test_text_processing.py` |
| XBRL to annual metrics | `financial_facts_loader.py` | `test_financial_facts_loader.py` |
| Ingest orchestration and failure handling | `ingest_pipeline.py` | `test_ingest_pipeline.py` |
| Storage, FTS index, read-only lockdown | `filings_store.py` | `test_serving_store_lockdown.py` |
| Hybrid search | `hybrid_retriever.py`, `embeddings.py` | `test_retrieval_and_tools.py` |
| SQL safety | `sql_guard.py` | `test_sql_guard.py` |
| Tool schemas, execution limits | `agent_tools.py` | `test_retrieval_and_tools.py` |
| Agent loop, citations, stop reasons | `research_agent.py` | `test_research_agent.py` |
| OpenRouter adapter | `openai_compatible_client.py` | `test_openai_compatible_client.py` |
| Demo limits, client identity, error redaction | `usage_limiter.py`, `http_api.py` | `test_usage_limiter.py`, `test_http_api.py` |
| Grading and cost | `evaluation.py` | `test_evaluation.py` |
| SSE client and event normalisation | `web/lib/agent-api-client.ts`, `web/lib/agent-types.ts` | `npm run lint` (type check) and `npm run build` |
| Answer formatting and citation links | `web/lib/format.ts` | same |

Shared test doubles (hashing embedder, in-memory store, scripted Claude client) are in
`tests/conftest.py`.

## Local state that is not in git

- `data/filings.duckdb`: built by `filings-copilot ingest`, shipped in the image.
- `evals/results/`: one JSON report per eval run, read by `eval-report`.
- `.env`: local credentials.
- `plans/`: working plans and review reports.
