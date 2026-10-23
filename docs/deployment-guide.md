# Deployment guide

Two deploy targets: the API on Google Cloud Run and the UI on Vercel. The public URLs are
in the root README. Account and project identifiers are kept by the maintainer, not in
this public repository; commands below take them as placeholders.

## API on Cloud Run

| Field | Route |
|---|---|
| When to use | Shipping API code, a new DuckDB file, a model change, or new limits |
| Scope | The GCP project passed explicitly as `--project`; region `us-central1` by default; service `filings-copilot-api` |
| Source of truth | `scripts/deploy_cloud_run.py` (its docstring lists every step); `Dockerfile` for the image |
| How to run | Build the data first (`filings-copilot ingest ...`), set the deploy values in `.env` (provider, model, `FILINGS_CORS_ORIGINS`, both demo caps), then `python scripts/deploy_cloud_run.py --project <gcp-project>` |
| Access | `gcloud auth login` as a project owner or editor; billing enabled on the project |
| Limits | One instance max and scale to zero by design (see [system-architecture.md](system-architecture.md)). The script refuses to deploy without a daily cap |
| Verify | `GET /health` returns the model and both limits that are actually active. A limit of 0 there means the caps are off |

## UI on Vercel

| Field | Route |
|---|---|
| When to use | Shipping changes under `web/` or pointing the UI at a new API URL |
| Scope | The Vercel project linked in `web/.vercel/` (local, git-ignored); production environment |
| Source of truth | Vercel project settings; the only app variable is `NEXT_PUBLIC_API_URL` |
| How to run | From `web/`: `npx vercel --prod`. The project is not connected to GitHub, so pushes do not deploy |
| Access | `npx vercel login` with the account that owns the project |
| Limits | Set `NEXT_PUBLIC_API_URL` from a shell with `printf` (no trailing newline or BOM); an invisible character there broke every request once. The value is read at build time, so redeploy after changing it |
| Verify | Open the site, ask a question, and check the evidence panel fills. If the API URL changed, update `FILINGS_CORS_ORIGINS` and redeploy the API |

## Secrets

- LLM keys are read from the local `.env` by the deploy script and stored in Google Secret
  Manager. Secret names and IAM bindings are defined in `scripts/deploy_cloud_run.py`
  (`put_secret`, `main`). Values are piped, never printed.
- Rotating a key: change it in `.env` and rerun the deploy script. It adds a secret
  version only when the value changed.
- The runtime service account has `secretAccessor` on those secrets only.

## Logs

- Application and request logs:
  `gcloud run services logs read filings-copilot-api --project=<gcp-project> --region=us-central1 --limit=50`.
  Each question logs `question client=... forwarded_entries=...`; agent failures log
  `agent failed ref=<id>`, the same id the user sees in the UI error.
- Build logs: the Cloud Build link printed by the deploy command.
- Logs contain client IP addresses. Keep excerpts out of public issues.

## Spend limits

The in-process caps reset on every instance restart. The real bound is the provider:

- **Anthropic**: a spend limit in the Claude Console (organisation billing, or a dedicated
  workspace for the demo key).
- **OpenRouter**: a credit limit on the key, if the OpenRouter provider is used.
- **GCP**: a billing budget alert on the project covers Cloud Run and Cloud Build.

## Rollback

- API: list revisions with
  `gcloud run revisions list --service=filings-copilot-api --project=<gcp-project> --region=us-central1`,
  then route traffic back with
  `gcloud run services update-traffic filings-copilot-api --to-revisions=<revision>=100 --project=<gcp-project> --region=us-central1`.
- UI: promote an earlier deployment in the Vercel dashboard (Deployments, then Promote to
  Production).
