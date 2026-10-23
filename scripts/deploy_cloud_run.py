"""Deploy the API to Google Cloud Run from source (Cloud Build builds the Dockerfile).

    python scripts/deploy_cloud_run.py --project <gcp-project-id> [--region us-central1]

What it does, idempotently:
1. Enables the Run, Cloud Build, Artifact Registry and Secret Manager APIs.
2. Stores LLM keys from .env in Secret Manager (values are piped, never printed).
3. Deploys with scale-to-zero, one instance at most and the demo question caps. The
   caps live in process memory and reset when the instance restarts, so the hard bound
   on LLM spend is the provider-side limit (Anthropic workspace / OpenRouter key).

Requires `gcloud auth login` and a project with billing enabled.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from filings_copilot.config import Settings

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "filings-copilot-api"
APIS = ["run.googleapis.com", "cloudbuild.googleapis.com",
        "artifactregistry.googleapis.com", "secretmanager.googleapis.com"]


def gcloud(
    *args: str, stdin: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run gcloud. Passing `stdin` (even "") captures output instead of streaming it."""
    exe = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if exe is None:
        raise SystemExit("gcloud not found on PATH; install the Google Cloud CLI first.")
    return subprocess.run([exe, *args], input=stdin, text=True, check=check,
                          capture_output=stdin is not None)


def put_secret(project: str, name: str, value: str) -> None:
    exists = gcloud("secrets", "describe", name, f"--project={project}",
                    stdin="", check=False).returncode == 0
    if not exists:
        gcloud("secrets", "create", name, f"--project={project}",
               "--replication-policy=automatic", stdin="")
    else:
        # Free tier covers 6 active versions: only add one when the value changed.
        latest = gcloud("secrets", "versions", "access", "latest", f"--secret={name}",
                        f"--project={project}", stdin="", check=False)
        if latest.returncode == 0 and latest.stdout == value:
            print(f"secret {name}: unchanged")
            return
    gcloud("secrets", "versions", "add", name, f"--project={project}", "--data-file=-",
           stdin=value)
    print(f"secret {name}: new version stored")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    args = parser.parse_args()

    settings = Settings()
    if not (ROOT / settings.db_path).exists():
        raise SystemExit("data/filings.duckdb not found; run `filings-copilot ingest` first.")
    if not settings.demo_daily_question_cap:
        raise SystemExit("Refusing to deploy a public demo without a daily question cap "
                         "(FILINGS_DEMO_DAILY_QUESTION_CAP).")

    gcloud("services", "enable", *APIS, f"--project={args.project}")

    secrets = {"ANTHROPIC_API_KEY": settings.anthropic_api_key,
               "OPENROUTER_API_KEY": settings.openrouter_api_key}
    # In new projects the default compute service account both builds the image (Cloud
    # Build) and runs the service: it gets the builder role on the project, and read
    # access only to the secrets this service uses.
    number = gcloud("projects", "describe", args.project, "--format=value(projectNumber)",
                    stdin="").stdout.strip()
    service_account = f"serviceAccount:{number}-compute@developer.gserviceaccount.com"
    gcloud("projects", "add-iam-policy-binding", args.project, f"--member={service_account}",
           "--role=roles/run.builder", "--condition=None", "--quiet", stdin="")

    bindings = []
    for env_name, value in secrets.items():
        if value:
            secret_name = f"filings-copilot-{env_name.lower().replace('_', '-')}"
            put_secret(args.project, secret_name, value)
            gcloud("secrets", "add-iam-policy-binding", secret_name, f"--project={args.project}",
                   f"--member={service_account}", "--role=roles/secretmanager.secretAccessor",
                   "--quiet", stdin="")
            bindings.append(f"{env_name}={secret_name}:latest")
    if not bindings:
        raise SystemExit("No LLM key found in .env (ANTHROPIC_API_KEY or OPENROUTER_API_KEY).")

    env_vars = {
        "FILINGS_LLM_PROVIDER": settings.llm_provider,
        "FILINGS_LLM_MODEL": settings.llm_model,
        "FILINGS_LLM_EFFORT": settings.llm_effort,
        "FILINGS_CORS_ORIGINS": settings.cors_origins,
        "FILINGS_DEMO_QUESTIONS_PER_HOUR": str(settings.demo_questions_per_hour),
        "FILINGS_DEMO_DAILY_QUESTION_CAP": str(settings.demo_daily_question_cap),
        "FILINGS_TRUSTED_PROXY_HOPS": str(settings.trusted_proxy_hops),
    }
    # An env-vars file, not --set-env-vars: on Windows gcloud.cmd runs through cmd.exe,
    # which eats the "^@^" custom-delimiter syntax and merges every variable into one.
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        json.dump(env_vars, f)  # a JSON object is valid YAML
        env_file = f.name
    try:
        gcloud(
            "run", "deploy", SERVICE, "--source", str(ROOT),
            f"--project={args.project}", f"--region={args.region}",
            "--allow-unauthenticated",
            "--memory=1Gi", "--cpu=1",
            # One instance keeps the in-memory question caps consistent; zero minimum
            # instances means no cost while idle (and a cap reset after each restart).
            "--min-instances=0", "--max-instances=1", "--concurrency=20",
            "--timeout=300",
            f"--env-vars-file={env_file}",
            "--set-secrets=" + ",".join(bindings),
            "--quiet",
        )
    finally:
        Path(env_file).unlink()
    url = gcloud("run", "services", "describe", SERVICE, f"--project={args.project}",
                 f"--region={args.region}", "--format=value(status.url)", stdin="").stdout.strip()
    print(f"Deployed: {url}  (try {url}/health)")


if __name__ == "__main__":
    main()
