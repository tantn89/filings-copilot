# System architecture

The component diagram and the headline decisions (DuckDB as the only store, RRF fusion,
normalised metrics, hand-rolled agent loop) are in the root README under `## Architecture`
and `## Design decisions`. This file adds boundaries, trust boundaries and the decisions
that the README does not record.

## Boundaries

| Boundary | Runs | Writes | Reads |
|---|---|---|---|
| Ingest | Offline, `filings-copilot ingest` | The DuckDB file | SEC EDGAR APIs |
| Serve | FastAPI (`filings-copilot serve` or the container) | Nothing | DuckDB file opened read-only; LLM provider |
| Eval | `filings-copilot eval` | `evals/results/*.json` (git-ignored) | Golden set, the same agent code path as Serve |
| Web | Next.js in `web/` | Nothing | The API over HTTP and SSE |

Run ingest with the API stopped: DuckDB allows one writing process per file. The serving
image ships a pre-built database, so production has no write path at all.

## Trust boundaries

1. **Internet to API.** Anonymous callers. The per-client limit keys on the address the
   platform's proxy appended to `X-Forwarded-For` (`FILINGS_TRUSTED_PROXY_HOPS`), because
   entries further left are client-supplied. CORS is locked to the UI origin. Agent
   errors reach the client only as a reference id; details stay in server logs.
2. **Model to database.** Model-written SQL passes `sql_guard.validate_select` (AST
   allow-list, per-scope table resolution, row cap), then runs on a connection that is
   read-only, has external access disabled, a memory and thread limit, and locked
   configuration, under a query watchdog. Each layer assumes the one above it can fail.
3. **Model to UI.** Tool inputs and SSE events are normalised at runtime before rendering
   (`web/lib/agent-api-client.ts`, `web/lib/format.ts`); markdown renders without raw
   HTML. The Anthropic path uses strict tool schemas, but the OpenRouter path does not, so
   the UI cannot rely on them.

## Decision ledger

| Decision | Chosen | Rejected and why |
|---|---|---|
| API host | Google Cloud Run, scale to zero, max one instance | Hugging Face Docker Spaces: requires a paid plan for this use. Always-on VM: pays while idle |
| Demo limits | In-memory `UsageLimiter`, one instance | Redis or a database counter: extra service and cost for a demo. Accepted trade-off: counters reset on restart, so the provider spend limit is the real bound |
| Client identity | Rightmost trusted `X-Forwarded-For` entry | Leftmost entry: spoofable, verified in production logs |
| Secret access | Per-secret `secretAccessor` for the runtime service account | Project-wide binding: grants every secret in the project |
| Production model | Claude Sonnet 5.5 | Opus 5.5: same eval accuracy at roughly twice the cost and latency; stays the local default |
| Other models | OpenRouter adapter behind the Anthropic-shaped loop (`openai_compatible_client.py`) | A second agent loop per provider: tools, guard and eval would diverge |
| Grading | Deterministic checks against XBRL, citations and phrases | LLM-as-judge: a score change could come from the judge, not the system |
| Dependency versions | Exact pins in `constraints.txt`, Python 3.12 only | Floating ranges: CI and the image drifted apart |
| Database at scale | Brute-force cosine | HNSW (`vss`) now: unnecessary at ~1k chunks; revisit past ~1M |

## Known limits

- Counters and the daily cap reset whenever the Cloud Run instance restarts.
- The runtime service account still holds Google's default `roles/editor`. It also builds
  the image; narrowing it needs a dedicated build service account first.
- Multi-part questions rely on the agent to split searches; retrieval alone misses one
  golden case (see README, Failure analysis).
