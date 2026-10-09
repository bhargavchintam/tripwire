"""SQL guard for the investigator's read-only ``run_sql`` tool (master plan D2, §7b).

    from ai.sqlguard import guard, SQLGuardError
    safe_sql = guard("SELECT agent_id, count() FROM events GROUP BY agent_id")   # LIMIT 200 injected
    safe_sql = guard(sql, limit=50)                                             # cap of 50 instead

guard() parses the text with sqlglot (dialect "clickhouse") and accepts a statement only when ALL
of these hold (allowlist mindset: anything not recognised as a plain read of ``events`` is refused):
  - it parses, and it is exactly one statement (a trailing ';' is fine; "SELECT 1; DROP ..." is not,
    comments cannot hide a second statement because the parser splits before comments are dropped)
  - it is a SELECT (or UNION/INTERSECT/EXCEPT of SELECTs); never INSERT/ALTER/DROP/SET/SYSTEM/KILL/
    SHOW/DESCRIBE/EXPLAIN/CREATE/..., never SELECT ... INTO, never WITH ... INSERT
  - every table reference is ``events`` or ``tripwire.events`` (also inside subqueries, CTEs, joins,
    IN (...), UNION branches); a CTE alias defined in the same statement may be referenced too
  - no table functions anywhere (numbers(), url(), file(), s3(), remote(), cluster(), mysql(),
    input(), merge(), view(), generateRandom(), ...): a FROM/JOIN source must be a plain identifier
  - every FROM / JOIN source is a table or a (SELECT ...) subquery; ARRAY JOIN takes a column or an array
    expression, never a subquery; the ClickHouse ``x IN table`` form (``IN system.users``) is refused
    (use ``IN (SELECT ...)``), and no column may be qualified with system / information_schema
  - no system.* / information_schema / INFORMATION_SCHEMA tables
  - no SETTINGS clause, no FORMAT clause, no INTO OUTFILE (ClickHouse's parser also refuses
    readonly users' settings changes, but the guard refuses them before the server sees them)
  - no dangerous functions in expressions: file(), url(), sleep(), sleepEachRow(), dictGet*(),
    remote()/cluster() and friends, executable()
The LIMIT rule: a missing LIMIT gets ``LIMIT <limit>``; a larger literal LIMIT is clamped down to
<limit>; a non-literal LIMIT is refused; ``LIMIT n BY col`` and set operations are wrapped in
``SELECT * FROM (...) LIMIT <limit>`` so the row cap always holds. Comments are stripped and the
statement is re-emitted with sqlglot (exp.sql(dialect="clickhouse")), so the receipt shows exactly
what ran. The read-only ClickHouse user (readonly=1, max_execution_time=5) stays the second line of
defence; this guard is the first.

CLI:
    uv run python -m ai.sqlguard "SELECT agent_id, count() FROM events GROUP BY agent_id"
    exit 0 and the normalized SQL on stdout, or exit 1 and the refusal reason.
"""

from __future__ import annotations

import logging
import sys

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

# sqlglot logs "unsupported syntax, falling back to Command" at WARNING for SHOW/EXPLAIN/SYSTEM;
# those statements are refused anyway, so the noise is dropped.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

DIALECT = "clickhouse"
DEFAULT_LIMIT = 200
ALLOWED_TABLE = "events"
ALLOWED_DBS = {"", "tripwire"}
FORBIDDEN_DBS = {"system", "information_schema"}

# Functions refused wherever they appear (lower-case). Table functions used as a FROM source are
# refused structurally (the source is not an identifier); this list catches them in expressions too.
FORBIDDEN_FUNCS = frozenset(
    {
        # file / network readers and table functions
        "file",
        "filecluster",
        "url",
        "urlcluster",
        "s3",
        "s3cluster",
        "gcs",
        "hdfs",
        "hdfscluster",
        "azureblobstorage",
        "azureblobstoragecluster",
        "remote",
        "remotesecure",
        "cluster",
        "clusterallreplicas",
        "mysql",
        "postgresql",
        "mongodb",
        "odbc",
        "jdbc",
        "sqlite",
        "redis",
        "input",
        "merge",
        "view",
        "viewifpermitted",
        "executable",
        "numbers",
        "numbers_mt",
        "zeros",
        "zeros_mt",
        "generaterandom",
        "generate_series",
        "generateseries",
        "format",
        "values",
        "loop",
        "fuzzjson",
        "fuzzquery",
        "iceberg",
        "deltalake",
        "hudi",
        "dictionary",
        # time wasters
        "sleep",
        "sleepeachrow",
    }
)
FORBIDDEN_FUNC_PREFIXES = ("dict",)  # dictGet, dictGetString, dictHas, ... (other data sources)


class SQLGuardError(ValueError):
    """The SQL was refused; str(exc) says why (safe to show to the model and in the report)."""


def _reject(why: str) -> SQLGuardError:
    return SQLGuardError(why)


def _parse_single(sql: str) -> exp.Expression:
    text = (sql or "").strip()
    if not text:
        raise _reject("empty SQL")
    try:
        statements = sqlglot.parse(text, read=DIALECT)
    except (ParseError, TokenError) as exc:
        raise _reject(f"cannot parse as ClickHouse SQL: {' '.join(str(exc).split())[:160]}") from None
    except Exception as exc:  # noqa: BLE001 - any parser crash is a refusal, never a pass
        raise _reject(f"cannot parse ({type(exc).__name__})") from None
    real = [s for s in statements if s is not None and not isinstance(s, exp.Semicolon)]
    if not real:
        raise _reject("empty SQL")
    if len(real) > 1:
        kinds = ", ".join(type(s).__name__ for s in real)
        raise _reject(f"exactly one statement is allowed (got {len(real)}: {kinds})")
    return real[0]


def _func_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name or "").lower()
    try:
        return str(node.sql_name()).lower()
    except Exception:  # noqa: BLE001
        return type(node).__name__.lower()


def _check_statement_kind(tree: exp.Expression) -> None:
    if isinstance(tree, exp.Select):
        return
    if isinstance(tree, exp.SetOperation):  # UNION / INTERSECT / EXCEPT of selects
        return
    raise _reject(f"only SELECT statements are allowed (got {type(tree).__name__})")


def _check_selects(tree: exp.Expression) -> None:
    for sel in tree.find_all(exp.Select):
        if sel.args.get("settings"):
            raise _reject("SETTINGS clause is not allowed")
        if sel.args.get("format"):
            raise _reject("FORMAT clause is not allowed")
        if sel.args.get("into"):
            raise _reject("SELECT ... INTO is not allowed")
    # A set operation's own SETTINGS/FORMAT (when the dialect attaches them at the top).
    if tree.args.get("settings"):
        raise _reject("SETTINGS clause is not allowed")
    if tree.args.get("format"):
        raise _reject("FORMAT clause is not allowed")
    for kind in (exp.Insert, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Set, exp.Describe):
        if tree.find(kind) is not None:
            raise _reject(f"{kind.__name__.upper()} is not allowed inside a query")


def _cte_aliases(tree: exp.Expression) -> set[str]:
    return {cte.alias for cte in tree.find_all(exp.CTE) if cte.alias}


def _check_tables(tree: exp.Expression) -> None:
    ctes = _cte_aliases(tree)
    for table in tree.find_all(exp.Table):
        source = table.this
        if not isinstance(source, exp.Identifier):
            what = source.name if isinstance(source, exp.Func) else type(source).__name__
            raise _reject(f"table functions are not allowed ({what})")
        name = source.name
        db = (table.db or "").lower()
        catalog = table.catalog or ""
        if catalog:
            raise _reject(f"three-part table names are not allowed ({catalog}.{db}.{name})")
        if db in FORBIDDEN_DBS or name.lower() in FORBIDDEN_DBS:
            raise _reject(f"{db + '.' if db else ''}{name} is not allowed (system / information_schema)")
        if name == ALLOWED_TABLE and db in ALLOWED_DBS:
            continue
        if not db and name in ctes:
            continue
        shown = f"{table.db}.{name}" if table.db else name
        raise _reject(f"table {shown} is not allowed (only events / tripwire.events)")


def _check_sources(tree: exp.Expression) -> None:
    """Structural check of every FROM / JOIN source and of the ``IN table`` form.

    sqlglot parses the FROM of a subquery inside ARRAY JOIN as a Column (not a Table), and ClickHouse's
    ``x IN system.users`` keeps the table as a Column too, so the table allowlist alone would miss them."""
    for node in tree.find_all(exp.From):
        src = node.this
        if isinstance(src, exp.Subquery):
            src = src.unnest()
        if not isinstance(src, (exp.Table, exp.Select, exp.SetOperation)):
            raise _reject(f"FROM source must be a table or a subquery (got {type(node.this).__name__})")
    for node in tree.find_all(exp.Join):
        src = node.this
        if str(node.args.get("kind") or "").upper() == "ARRAY":
            if src.find(exp.Select, exp.Subquery, exp.Table) is not None:
                raise _reject("ARRAY JOIN over a subquery or table is not allowed")
            continue
        inner = src.unnest() if isinstance(src, exp.Subquery) else src
        if not isinstance(inner, (exp.Table, exp.Select, exp.SetOperation)):
            raise _reject(f"JOIN source must be a table or a subquery (got {type(src).__name__})")
    for node in tree.find_all(exp.In):
        if node.args.get("field") is not None:
            raise _reject("the 'x IN table' form is not allowed; use IN (SELECT ...)")
    for col in tree.find_all(exp.Column):
        qualifier = (col.table or "").lower()
        if qualifier in FORBIDDEN_DBS or (col.db or "").lower() in FORBIDDEN_DBS:
            raise _reject(f"{col.sql(dialect=DIALECT)} is not allowed (system / information_schema)")


def _check_functions(tree: exp.Expression) -> None:
    for fn in tree.find_all(exp.Func):
        name = _func_name(fn)
        if name in FORBIDDEN_FUNCS or name.startswith(FORBIDDEN_FUNC_PREFIXES):
            raise _reject(f"function {fn.name or name}() is not allowed")


def _strip_comments(tree: exp.Expression) -> None:
    for node in tree.walk():
        node.comments = None


def _literal_limit(limit: exp.Limit) -> int:
    value = limit.expression
    if not isinstance(value, exp.Literal) or value.is_string or not str(value.this).isdigit():
        raise _reject("LIMIT must be a plain integer literal")
    return int(value.this)


def _apply_limit(tree: exp.Expression, cap: int) -> exp.Expression:
    """Guarantee a row cap of ``cap`` on the statement's result."""
    if isinstance(tree, exp.Select):
        limit = tree.args.get("limit")
        if limit is None:
            return tree.limit(cap)
        if limit.args.get("expressions"):  # LIMIT n BY col: per-group cap only -> wrap for a total cap
            return exp.select("*").from_(tree.subquery("_guarded")).limit(cap)
        if _literal_limit(limit) > cap:
            limit.set("expression", exp.Literal.number(cap))
        return tree
    # UNION / INTERSECT / EXCEPT: a LIMIT written after the last branch only limits that branch in
    # ClickHouse, so the whole set operation is wrapped (sqlglot does that in Query.limit()).
    return tree.limit(cap)


def guard(sql: str, *, limit: int = DEFAULT_LIMIT) -> str:
    """Validate ``sql`` and return the normalized, row-capped ClickHouse SQL; raise SQLGuardError otherwise."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be a positive int (got {limit!r})")
    tree = _parse_single(sql)
    _check_statement_kind(tree)
    _check_selects(tree)
    _check_tables(tree)
    _check_sources(tree)
    _check_functions(tree)
    _strip_comments(tree)
    tree = _apply_limit(tree, limit)
    out = tree.sql(dialect=DIALECT)
    # Belt and braces: the emitted text must itself re-parse as one SELECT.
    again = _parse_single(out)
    if not isinstance(again, (exp.Select, exp.SetOperation)):
        raise _reject("normalized SQL is not a single SELECT")
    return out


def _cli(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print('usage: uv run python -m ai.sqlguard "SELECT ... FROM events ..."', file=sys.stderr)
        return 2
    try:
        print(guard(argv[0]))
    except SQLGuardError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
