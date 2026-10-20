"""Validate model-written SQL before it touches the database.

The model may only run a single SELECT over an allow-list of tables. We parse the query
with sqlglot rather than pattern-matching strings, so comments, CTEs and subqueries can't
smuggle in a write or a read of another table. A row limit is always enforced.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = frozenset({"annual_financials", "companies", "filings"})
MAX_ROWS = 200

_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.Command, exp.Copy, exp.Pragma, exp.Attach, exp.Detach, exp.Set, exp.Use,
)
# Table-valued functions that read files or URLs (read_csv, read_parquet, glob, ...).
_FORBIDDEN_FUNCTION_PREFIXES = ("read_", "glob", "parquet_", "sniff_", "query", "duckdb_")


class UnsafeSQLError(ValueError):
    pass


def validate_select(sql: str) -> str:
    """Return a safe, row-limited version of `sql` or raise UnsafeSQLError."""
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQLError(f"Could not parse SQL: {e}") from None
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise UnsafeSQLError("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise UnsafeSQLError("Only SELECT queries are allowed.")
    if any(tree.find_all(*_FORBIDDEN)):
        raise UnsafeSQLError("Only read-only queries are allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeSQLError("Qualified table names are not allowed.")
        if not name and table.this is not None:
            raise UnsafeSQLError("Table functions are not allowed.")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise UnsafeSQLError(
                f"Table '{table.name}' is not available. Use: {', '.join(sorted(ALLOWED_TABLES))}."
            )
    for func in tree.find_all(exp.Func):
        if func.sql_name().lower().startswith(_FORBIDDEN_FUNCTION_PREFIXES):
            raise UnsafeSQLError(f"Function '{func.sql_name()}' is not allowed.")
    for anonymous in tree.find_all(exp.Anonymous):
        if anonymous.name.lower().startswith(_FORBIDDEN_FUNCTION_PREFIXES):
            raise UnsafeSQLError(f"Function '{anonymous.name}' is not allowed.")

    limited = tree.limit(MAX_ROWS) if tree.args.get("limit") is None else tree
    return limited.sql(dialect="duckdb")
