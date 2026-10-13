"""SQL Server-specific rewrites and checks layered on top of sqlglot.

sqlglot's T-SQL generator passes some constructs through unchanged even when
SQL Server has no equivalent (ILIKE, EXPLODE, ROWNUM, ...). Rewrites fix the
cases with a faithful T-SQL equivalent; checks flag the rest so the caller can
refuse the statement instead of running something wrong.
"""

from __future__ import annotations

from sqlglot import exp

# Expression types SQL Server has no direct equivalent for.
UNSUPPORTED_NODES: dict[type[exp.Expression], str] = {
    exp.Explode: "EXPLODE/UNNEST has no T-SQL equivalent; rewrite with OPENJSON or STRING_SPLIT",
    exp.Unnest: "UNNEST has no T-SQL equivalent; rewrite with OPENJSON or STRING_SPLIT",
    exp.ArrayAgg: "ARRAY_AGG has no T-SQL equivalent; use STRING_AGG or JSON_ARRAYAGG",
    exp.Array: "array values are not supported by SQL Server",
    exp.Struct: "struct values are not supported by SQL Server",
    exp.Lambda: "lambda expressions are not supported by SQL Server",
    exp.Interval: "INTERVAL values are not supported by SQL Server; use DATEADD/DATEDIFF",
}

UNSUPPORTED_TYPES = {
    exp.DataType.Type.ARRAY: "ARRAY",
    exp.DataType.Type.STRUCT: "STRUCT",
    exp.DataType.Type.MAP: "MAP",
    exp.DataType.Type.INTERVAL: "INTERVAL",
}

# Oracle pseudo-columns that would silently become ordinary column references.
PSEUDO_COLUMNS = {"ROWNUM", "ROWID", "LEVEL"}

ROUTINE_KINDS = {"FUNCTION", "PROCEDURE", "TRIGGER"}


def rewrite(tree: exp.Expression, source: str) -> exp.Expression:
    """Apply rewrites that have an exact T-SQL equivalent."""
    tree = tree.transform(_ilike_to_lower_like)
    tree = tree.transform(_interval_arithmetic_to_dateadd)
    _qualify_returning(tree)
    if source == "oracle":
        _drop_from_dual(tree)
    return tree


def problems(tree: exp.Expression, source: str) -> list[str]:
    """Return reasons this statement cannot be translated faithfully."""
    found: list[str] = []

    if isinstance(tree, exp.Command):
        found.append(f"'{tree.name}' statements could not be parsed and would pass through untranslated")

    if isinstance(tree, exp.Create) and str(tree.args.get("kind", "")).upper() in ROUTINE_KINDS:
        found.append(
            f"{tree.args['kind'].upper()} bodies are procedural code in the {source} dialect "
            "and must be rewritten by hand in T-SQL"
        )

    for node in tree.walk():
        for node_type, reason in UNSUPPORTED_NODES.items():
            if type(node) is node_type:
                found.append(reason)
        if isinstance(node, exp.DataType) and node.this in UNSUPPORTED_TYPES:
            found.append(f"{UNSUPPORTED_TYPES[node.this]} data type is not supported by SQL Server")
        if (
            source == "oracle"
            and isinstance(node, exp.Column)
            and not node.table
            and node.name.upper() in PSEUDO_COLUMNS
        ):
            found.append(f"Oracle pseudo-column {node.name.upper()} has no T-SQL equivalent")
        if isinstance(node, exp.Anonymous) and not isinstance(node.parent, exp.Dot):
            found.append(
                f"unknown function {node.name.upper()}(); if it is a user-defined function, "
                "schema-qualify it (e.g. dbo." + node.name + ")"
            )

    # De-duplicate while keeping order.
    return list(dict.fromkeys(found))


def _ilike_to_lower_like(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.ILike):
        return exp.Like(
            this=exp.Lower(this=node.this),
            expression=exp.Lower(this=node.expression),
        )
    return node


INTERVAL_UNITS = {
    "YEAR", "QUARTER", "MONTH", "WEEK", "DAY", "HOUR", "MINUTE", "SECOND", "MILLISECOND", "MICROSECOND",
}


def _interval_arithmetic_to_dateadd(node: exp.Expression) -> exp.Expression:
    """ts +/- INTERVAL 'n' UNIT -> DATEADD(UNIT, +/-n, ts) when n is a whole number."""
    if not isinstance(node, (exp.Add, exp.Sub)) or not isinstance(node.expression, exp.Interval):
        return node
    interval = node.expression
    unit = interval.text("unit").upper().rstrip("S")
    amount = interval.this
    if unit not in INTERVAL_UNITS or not isinstance(amount, exp.Literal):
        return node
    try:
        n = int(amount.name)
    except ValueError:
        return node
    if isinstance(node, exp.Sub):
        n = -n
    return exp.DateAdd(this=node.this, expression=exp.Literal.number(n), unit=exp.var(unit))


def _qualify_returning(tree: exp.Expression) -> None:
    """RETURNING id -> OUTPUT INSERTED.id (DELETED for DELETE statements)."""
    returning = tree.args.get("returning")
    if not isinstance(returning, exp.Returning):
        return
    prefix = "DELETED" if isinstance(tree, exp.Delete) else "INSERTED"
    qualified = []
    for item in returning.expressions:
        if isinstance(item, exp.Star):
            qualified.append(exp.Column(this=exp.Star(), table=exp.to_identifier(prefix)))
            continue
        for col in item.find_all(exp.Column):
            if not col.table:
                col.set("table", exp.to_identifier(prefix))
        qualified.append(item)
    returning.set("expressions", qualified)


def _drop_from_dual(tree: exp.Expression) -> None:
    for select in tree.find_all(exp.Select):
        key = "from_" if "from_" in select.arg_types else "from"
        from_ = select.args.get(key)
        if from_ and isinstance(from_.this, exp.Table) and from_.this.name.upper() == "DUAL" and not select.args.get("joins"):
            select.set(key, None)
