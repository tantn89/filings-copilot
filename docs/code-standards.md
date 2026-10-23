# Code standards

The quality gates are whatever `.github/workflows/ci.yml` runs; that file is the owner.
Lint and type rules live in `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]`).

## Environment

- Python 3.12 only, matching the container image. Install with the pinned set:
  `pip install -c constraints.txt -e ".[dev]"`.
- Change a dependency by editing `pyproject.toml`, then regenerating `constraints.txt`
  from a clean 3.12 environment, so CI and the image keep using the same versions.
- Node 22 for `web/`; `npm ci` against the committed lockfile.

## Testing policy

- **Tests never touch the network or spend credits.** EDGAR is mocked (respx), embeddings
  use a hashing embedder, Claude is a scripted fake, DuckDB is in memory
  (`tests/conftest.py`). Live checks belong to `filings-copilot eval`, which is run on
  purpose, not in CI.
- **Every real-world data quirk gets a regression test** with a minimal fixture
  reproducing it: running page headers, wrapped "Item" lines, merged table cells,
  52/53-week fiscal years, alternative XBRL concepts. Add the fixture before the fix.
- **Security fixes keep their attack as a test.** The guard bypasses and cost-amplification
  queries are in `test_sql_guard.py`, `test_retrieval_and_tools.py` and
  `test_serving_store_lockdown.py`. Never weaken one to get a green build.
- **Grader changes are tested both ways**: a case that must pass and one that must fail
  (`test_evaluation.py`), so a looser grader cannot hide a regression.

## Code conventions

- Module names describe their single concern (`hybrid_retriever.py`,
  `financial_facts_loader.py`); keep modules near 200 lines and split on real boundaries.
- Validate model-produced input at the boundary where it is used, never assume the model
  followed the schema.
- Return tool errors to the model as text so it can retry; raise only for bugs.
- No numeric literals for limits inside logic: keep them as named module constants or
  `Settings` fields.

## Commits and contributions

- Conventional commits (`feat`, `fix`, `refactor`, `build`, `docs`, `test`) with a scope
  matching the area (`api`, `agent`, `ingest`, `web`, `deploy`, `security`, `eval`).
- Describe the behaviour in commit messages, not plan or ticket labels.
- Never commit `.env`, keys, the DuckDB file, or eval results; `.gitignore` covers them.
- A change to the SQL guard, limits, or deploy permissions also updates the matching
  claim in the README.
