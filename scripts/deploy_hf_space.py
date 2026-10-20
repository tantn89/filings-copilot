"""Deploy the API to a Hugging Face Docker Space.

Uploads the Dockerfile, package source and the pre-built DuckDB file, writes the Space
card, and sets secrets/variables from the local environment (.env is read too). The Space
rebuilds the image on every upload.

    python scripts/deploy_hf_space.py --space <user>/filings-copilot-api

Requires a Hugging Face write token (HF_TOKEN or `hf auth login`). LLM keys are stored
as Space secrets, never uploaded.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

from filings_copilot.config import Settings

ROOT = Path(__file__).resolve().parents[1]

SPACE_CARD = """---
title: filings-copilot API
emoji: 📑
colorFrom: green
colorTo: gray
sdk: docker
app_port: 8080
pinned: false
license: mit
short_description: Research agent API for SEC 10-K filings
---

API for [filings-copilot](https://github.com/tantn89/filings-copilot): `POST /ask/stream`,
`GET /companies`, `GET /health`. Interactive docs at `/docs`.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--space", required=True, help="e.g. tantn89/filings-copilot-api")
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    db = ROOT / settings.db_path
    if not db.exists():
        raise SystemExit(f"{db} not found; run `filings-copilot ingest` first.")

    if not settings.demo_daily_question_cap:
        raise SystemExit("Refusing to deploy a public demo without a daily question cap "
                         "(FILINGS_DEMO_DAILY_QUESTION_CAP).")

    api = HfApi()  # token from HF_TOKEN or `hf auth login`
    api.create_repo(args.space, repo_type="space", space_sdk="docker",
                    private=args.private, exist_ok=True)

    # Non-secret runtime configuration, copied from the local settings.
    variables = {
        "FILINGS_LLM_PROVIDER": settings.llm_provider,
        "FILINGS_LLM_MODEL": settings.llm_model,
        "FILINGS_LLM_EFFORT": settings.llm_effort,
        "FILINGS_CORS_ORIGINS": settings.cors_origins,
        "FILINGS_DEMO_QUESTIONS_PER_HOUR": str(settings.demo_questions_per_hour),
        "FILINGS_DEMO_DAILY_QUESTION_CAP": str(settings.demo_daily_question_cap),
    }
    for key, value in variables.items():
        api.add_space_variable(args.space, key, value)

    secrets = {"ANTHROPIC_API_KEY": settings.anthropic_api_key,
               "OPENROUTER_API_KEY": settings.openrouter_api_key}
    for key, value in secrets.items():
        if value:
            api.add_space_secret(args.space, key, value)

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        shutil.copy(ROOT / "Dockerfile", stage)
        shutil.copy(ROOT / "pyproject.toml", stage)
        shutil.copytree(ROOT / "src", stage / "src",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (stage / "data").mkdir()
        shutil.copy(db, stage / "data" / "filings.duckdb")
        (stage / "README.md").write_text(SPACE_CARD, encoding="utf-8")
        api.upload_folder(repo_id=args.space, repo_type="space", folder_path=stage,
                          commit_message="Deploy filings-copilot API")

    print(f"Deployed. Build logs: https://huggingface.co/spaces/{args.space}")
    print(f"API base URL: https://{args.space.replace('/', '-').lower()}.hf.space")


if __name__ == "__main__":
    main()
