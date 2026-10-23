from __future__ import annotations

import pytest

from filings_copilot.sql_guard import MAX_ROWS, UnsafeSQLError, validate_select


def test_adds_row_limit_to_plain_select() -> None:
    sql = validate_select("SELECT * FROM annual_financials WHERE ticker = 'NVDA'")
    assert sql.endswith(f"LIMIT {MAX_ROWS}")


def test_keeps_a_smaller_limit() -> None:
    assert validate_select("SELECT 1 AS x FROM companies LIMIT 3").endswith("LIMIT 3")


def test_lowers_a_larger_limit_to_the_cap() -> None:
    sql = validate_select("SELECT * FROM annual_financials LIMIT 100000")
    assert sql.endswith(f"LIMIT {MAX_ROWS}")


def test_cte_may_reuse_an_allowed_table_name() -> None:
    validate_select("WITH companies AS (SELECT * FROM companies) SELECT * FROM companies")


def test_allows_ctes_window_functions_and_joins() -> None:
    validate_select("""
        WITH r AS (SELECT ticker, fiscal_year, value FROM annual_financials WHERE metric='revenue')
        SELECT r.ticker, c.name, fiscal_year,
               value / lag(value) OVER (PARTITION BY r.ticker ORDER BY fiscal_year) - 1 AS growth
        FROM r JOIN companies c USING (ticker)
    """)


@pytest.mark.parametrize("sql", [
    "DELETE FROM annual_financials",
    "DROP TABLE companies",
    "UPDATE companies SET name = 'x'",
    "INSERT INTO companies VALUES ('X', 1, 'x')",
    "SELECT 1; DROP TABLE companies",
    "CREATE TABLE t AS SELECT * FROM companies",
    "COPY companies TO 'out.csv'",
    "ATTACH 'other.db' AS other",
    "PRAGMA database_list",
    "SET enable_external_access = true",
    "SELECT * FROM read_csv('C:/secrets.csv')",
    "SELECT * FROM 'C:/secrets.csv'",
    "SELECT * FROM chunks",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM other.main.companies",
    "SELECT * FROM (SELECT * FROM read_parquet('s3://bucket/x.parquet'))",
    "WITH x AS (SELECT * FROM glob('*')) SELECT * FROM x",
    # A CTE named after a hidden table must not unlock that table.
    "WITH chunks AS (SELECT * FROM chunks) SELECT count(*) FROM chunks",
    "WITH duckdb_tables AS (SELECT * FROM duckdb_tables) SELECT * FROM duckdb_tables",
    "SELECT (SELECT max(text) FROM chunks) AS t FROM companies",
    # Resource abuse: unbounded recursion, row explosions, non-literal limits.
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r",
    "SELECT 1 FROM annual_financials a, annual_financials b, annual_financials c, "
    "annual_financials d, annual_financials e",
    "SELECT * FROM companies LIMIT (SELECT 1000000)",
    # Server configuration and catalog metadata.
    "SELECT current_setting('temp_directory') FROM companies",
    "SELECT getvariable('x') FROM companies",
    "not sql at all (((",
])
def test_rejects_unsafe_or_out_of_scope_sql(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(sql)
