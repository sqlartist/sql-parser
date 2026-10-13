"""Translate SQL from any sqlglot-supported dialect into T-SQL."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot.dialects.dialect import Dialects
from sqlglot.errors import ErrorLevel, ParseError, UnsupportedError

from . import rules

SUPPORTED_DIALECTS: tuple[str, ...] = tuple(sorted(d.value for d in Dialects if d.value))


class TranslationError(ValueError):
    """Raised when SQL cannot be translated faithfully to T-SQL."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or [message]


@dataclass
class Translation:
    source_dialect: str
    statements: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def tsql(self) -> str:
        return ";\n".join(self.statements) + (";" if self.statements else "")


class _WarningCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def translate(sql: str, dialect: str, *, strict: bool = True, pretty: bool = False) -> Translation:
    """Translate ``sql`` written in ``dialect`` into a list of T-SQL statements.

    With ``strict`` (the default) any construct that cannot be translated
    faithfully raises :class:`TranslationError`. Without it, those problems are
    returned as warnings and the best-effort translation is still produced.
    """
    dialect = (dialect or "").strip().lower()
    if dialect not in SUPPORTED_DIALECTS:
        raise TranslationError(
            f"unsupported dialect '{dialect}'; expected one of: {', '.join(SUPPORTED_DIALECTS)}"
        )
    if not sql or not sql.strip():
        raise TranslationError("no SQL supplied")

    collector = _WarningCollector()
    sqlglot_logger = logging.getLogger("sqlglot")
    sqlglot_logger.addHandler(collector)
    try:
        try:
            trees = [t for t in sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.RAISE) if t is not None]
        except ParseError as e:
            raise TranslationError(f"could not parse {dialect} SQL: {e}") from e

        found: list[str] = []
        statements: list[str] = []
        for tree in trees:
            if dialect != "tsql":
                tree = rules.rewrite(tree, dialect)
                found.extend(rules.problems(tree, dialect))
            try:
                statements.append(
                    tree.sql(
                        dialect="tsql",
                        pretty=pretty,
                        unsupported_level=ErrorLevel.RAISE if strict else ErrorLevel.WARN,
                    )
                )
            except UnsupportedError as e:
                found.append(str(e))
    finally:
        sqlglot_logger.removeHandler(collector)

    # sqlglot also logs its Command fallback, which rules.problems already reports.
    logged = [m for m in collector.messages if "Falling back to parsing as a 'Command'" not in m]
    found = list(dict.fromkeys(found + logged))
    if found and strict:
        raise TranslationError("SQL cannot be translated faithfully to T-SQL: " + "; ".join(found), found)
    return Translation(source_dialect=dialect, statements=statements, warnings=found)
