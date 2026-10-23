"""The read-only serving connection is the last line of defence behind sql_guard."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from filings_copilot.edgar_client import Company
from filings_copilot.filings_store import FilingsStore


@pytest.fixture
def served(tmp_path: Path) -> FilingsStore:
    path = tmp_path / "filings.duckdb"
    writer = FilingsStore(path, 8)
    writer.upsert_company(Company(1, "NVDA", "NVIDIA CORP"))
    writer.close()
    return FilingsStore(path, 8, read_only=True)


@pytest.mark.parametrize("sql", [
    "INSERT INTO companies VALUES ('X', 2, 'x')",
    "SELECT * FROM read_csv('pyproject.toml')",
    "COPY companies TO 'leak.csv'",
    "ATTACH ':memory:' AS other",
    "SET memory_limit = '8GB'",
    "SET enable_external_access = true",
])
def test_serving_connection_refuses_writes_files_and_config_changes(
    served: FilingsStore, sql: str
) -> None:
    with pytest.raises(duckdb.Error):
        served.cursor().execute(sql)


def test_serving_connection_is_resource_capped(served: FilingsStore) -> None:
    memory, threads = served.cursor().execute(
        "SELECT current_setting('memory_limit'), current_setting('threads')"
    ).fetchone()  # type: ignore[misc]
    assert threads == 1
    assert memory.endswith("MiB") and float(memory.split()[0]) <= 256


def test_dimension_mismatch_is_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "filings.duckdb"
    FilingsStore(path, 8).close()
    with pytest.raises(ValueError, match=r"FLOAT\[8\].*FLOAT\[16\]"):
        FilingsStore(path, 16, read_only=True)
