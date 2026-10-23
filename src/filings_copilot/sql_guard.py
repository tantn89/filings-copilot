"""Validate model-written SQL before it touches the database.

The model may only run a single SELECT over an allow-list of tables. We parse the query
with sqlglot rather than pattern-matching strings, so comments, CTEs and subqueries can't
smuggle in a write or a read of another table. Table references are resolved per scope,
so a CTE named after a hidden table (`WITH chunks AS (SELECT * FROM chunks)`) still
counts as a read of that table. Row count and query shape are bounded; time and memory
are bounded by the serving connection (see filings_store and agent_tools).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.scope import Scope, traverse_scope

ALLOWED_TABLES = frozenset({"annual_financials", "companies", "filings"})
MAX_ROWS = 200
MAX_TABLE_REFERENCES = 4  # keeps self cross joins from exploding row counts

_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.Command, exp.Copy, exp.Pragma, exp.Attach, exp.Detach, exp.Set, exp.Use,
)
# Functions that read files/URLs or expose server configuration and catalog metadata.
_FORBIDDEN_FUNCTION_PREFIXES = (
    "read_", "glob", "parquet_", "sniff_", "query", "duckdb_", "pragma_",
    "current_setting", "getvariable", "getenv", "which_secret",
)


class UnsafeSQLError(ValueError):
    pass


def _cte_references(tree: exp.Query) -> set[int]:
    """ids of Table nodes that point at a CTE in their own scope (not at a real table)."""
    try:
        scopes = traverse_scope(tree)
    except OptimizeError:
        raise UnsafeSQLError("Query structure is too complex to validate.") from None
    refs: set[int] = set()
    for scope in scopes:
        for table in scope.tables:
            if isinstance(scope.sources.get(table.alias_or_name), Scope):
                refs.add(id(table))
    return refs


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
    if any(w.args.get("recursive") for w in tree.find_all(exp.With)):
        raise UnsafeSQLError("Recursive CTEs are not allowed.")

    tables = list(tree.find_all(exp.Table))
    if len(tables) > MAX_TABLE_REFERENCES:
        raise UnsafeSQLError(f"At most {MAX_TABLE_REFERENCES} table references are allowed.")
    cte_refs = _cte_references(tree)
    for table in tables:
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeSQLError("Qualified table names are not allowed.")
        if not table.name and table.this is not None:
            raise UnsafeSQLError("Table functions are not allowed.")
        if id(table) not in cte_refs and table.name.lower() not in ALLOWED_TABLES:
            raise UnsafeSQLError(
                f"Table '{table.name}' is not available. Use: {', '.join(sorted(ALLOWED_TABLES))}."
            )
    for func in tree.find_all(exp.Func):
        if func.sql_name().lower().startswith(_FORBIDDEN_FUNCTION_PREFIXES):
            raise UnsafeSQLError(f"Function '{func.sql_name()}' is not allowed.")
    for anonymous in tree.find_all(exp.Anonymous):
        if anonymous.name.lower().startswith(_FORBIDDEN_FUNCTION_PREFIXES):
            raise UnsafeSQLError(f"Function '{anonymous.name}' is not allowed.")

    return _with_row_cap(tree).sql(dialect="duckdb")


def _with_row_cap(tree: exp.Query) -> exp.Query:
    """Add LIMIT MAX_ROWS, or lower a larger literal LIMIT to it."""
    limit = tree.args.get("limit")
    if limit is None:
        return tree.limit(MAX_ROWS)
    value = limit.expression
    if not (isinstance(value, exp.Literal) and value.is_int):
        raise UnsafeSQLError("LIMIT must be a whole number.")
    if int(value.name) > MAX_ROWS:
        return tree.limit(MAX_ROWS)
    return tree
