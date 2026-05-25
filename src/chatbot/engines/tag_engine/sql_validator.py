"""SQL safety validation for the TAG executor.

Uses sqlglot to parse the LLM's output into an AST. Reject anything that isn't
a single read-only SELECT/WITH at top level. Inject `LIMIT N` if the query
has no explicit LIMIT (so a stray cross-join can't blow up the row cap).

Why an AST walk instead of a regex:
  • Regex catches `DROP TABLE` but misses `INSERT … SELECT`, `CREATE TABLE
    AS …`, mutations hidden inside CTE bodies, and `PRAGMA` toggles.
  • An AST walk identifies the *kind* of every statement node and refuses
    anything in the deny list, regardless of where it appears.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass
class ValidationResult:
    ok: bool
    sql: str                    # possibly modified (e.g. LIMIT injected)
    reason: str | None = None   # populated when ok=False


# Top-level statement nodes that are read-only and acceptable.
_ALLOWED_TOP_LEVEL = (exp.Select, exp.Union, exp.With)

# Any node of these kinds anywhere in the AST is a hard reject.
_DENIED_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Command,        # generic SQL command nodes (PRAGMA fallback, etc.)
)


def validate_and_prepare(sql: str, *, dialect: str = "sqlite", row_limit: int = 100) -> ValidationResult:
    """Parse the SQL with sqlglot; return a ValidationResult.

    On success, `sql` is the possibly-modified statement (with `LIMIT` injected
    if the top-level SELECT had none). On failure, `reason` describes why.
    """
    stripped = (sql or "").strip().rstrip(";").strip()
    if not stripped:
        return ValidationResult(ok=False, sql=sql, reason="empty SQL")

    try:
        statements = sqlglot.parse(stripped, dialect=dialect)
    except sqlglot.errors.ParseError as exc:
        return ValidationResult(ok=False, sql=sql, reason=f"parse error: {exc}")

    # parse() returns one node per top-level statement. We allow exactly one.
    statements = [s for s in statements if s is not None]
    if len(statements) == 0:
        return ValidationResult(ok=False, sql=sql, reason="no statements parsed")
    if len(statements) > 1:
        return ValidationResult(ok=False, sql=sql, reason="multiple statements not allowed")

    root = statements[0]
    if not isinstance(root, _ALLOWED_TOP_LEVEL):
        return ValidationResult(
            ok=False,
            sql=sql,
            reason=f"top-level statement must be SELECT/WITH; got {type(root).__name__}",
        )

    # Walk the whole AST: any mutating / non-read-only node anywhere fails.
    for node in root.walk():
        if isinstance(node, _DENIED_NODES):
            return ValidationResult(
                ok=False,
                sql=sql,
                reason=f"disallowed construct: {type(node).__name__}",
            )

    # Inject LIMIT if missing. For UNION queries, attach LIMIT to the whole
    # union (top-level). For WITH, attach to the outer SELECT.
    inject_target: exp.Expression | None = None
    if isinstance(root, exp.Select):
        if root.args.get("limit") is None:
            inject_target = root
    elif isinstance(root, exp.Union):
        if root.args.get("limit") is None:
            inject_target = root
    elif isinstance(root, exp.With):
        outer = root.expression
        if isinstance(outer, exp.Select) and outer.args.get("limit") is None:
            inject_target = outer

    if inject_target is not None:
        inject_target.set("limit", exp.Limit(expression=exp.Literal.number(row_limit)))

    return ValidationResult(ok=True, sql=root.sql(dialect=dialect))
