import pytest

from sql2tsql import TranslationError, translate


@pytest.mark.parametrize(
    "dialect, sql, expected",
    [
        ("mysql", "SELECT `a`, IFNULL(b, 0) FROM t LIMIT 3", "SELECT TOP 3 [a], COALESCE(b, 0) FROM t;"),
        ("postgres", "SELECT 'a' || 'b', NOW()::date", "SELECT 'a' + 'b', CAST(GETDATE() AS DATE);"),
        (
            "postgres",
            "SELECT name FROM users WHERE name ILIKE 'a%'",
            "SELECT name FROM users WHERE LOWER(name) LIKE LOWER('a%');",
        ),
        (
            "postgres",
            "INSERT INTO t (a) VALUES (1) RETURNING id, a",
            "INSERT INTO t (a) OUTPUT INSERTED.id, INSERTED.a VALUES (1);",
        ),
        ("postgres", "DELETE FROM t WHERE a = 1 RETURNING *", "DELETE OUTPUT DELETED.* FROM t WHERE a = 1;"),
        ("oracle", "SELECT SYSDATE FROM dual", "SELECT GETDATE();"),
        (
            "postgres",
            "SELECT a FROM t WHERE ts > now() - interval '1 day'",
            "SELECT a FROM t WHERE ts > DATEADD(DAY, -1, GETDATE());",
        ),
        ("mysql", "SELECT ts + INTERVAL 3 HOUR FROM t", "SELECT DATEADD(HOUR, 3, ts) FROM t;"),
        ("postgres", "SELECT a FROM t WHERE a ~ 'x+'", "SELECT a FROM t WHERE REGEXP_LIKE(a, 'x+');"),
        ("postgres", "SELECT dbo.my_func(a) FROM t", "SELECT dbo.my_func(a) FROM t;"),
    ],
)
def test_translates(dialect, sql, expected):
    assert translate(sql, dialect).tsql == expected


def test_multiple_statements_are_split():
    result = translate("SELECT 1; SELECT `a` FROM t LIMIT 2", "mysql")
    assert result.statements == ["SELECT 1", "SELECT TOP 2 [a] FROM t"]


def test_snowflake_qualify_becomes_subquery():
    sql = "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1"
    tsql = translate(sql, "snowflake").tsql
    assert "QUALIFY" not in tsql
    assert "ROW_NUMBER() OVER" in tsql


@pytest.mark.parametrize(
    "dialect, sql, reason",
    [
        ("oracle", "SELECT * FROM t WHERE ROWNUM <= 5", "ROWNUM"),
        (
            "postgres",
            "CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END $$ LANGUAGE plpgsql",
            "procedural code",
        ),
        ("postgres", "SELECT my_func(a) FROM t", "unknown function MY_FUNC"),
        ("postgres", "SELECT x::int[] FROM t", "ARRAY data type"),
        ("bigquery", "SELECT ARRAY_AGG(x) FROM t", "ARRAY_AGG"),
        ("postgres", "SELECT unnest(x) FROM t", "UNNEST"),
        ("postgres", "SELECT interval '1 day' * n FROM t", "INTERVAL"),
        ("postgres", "VACUUM t", "untranslated"),
    ],
)
def test_strict_mode_rejects_unfaithful_translations(dialect, sql, reason):
    with pytest.raises(TranslationError, match=reason):
        translate(sql, dialect)


def test_lenient_mode_returns_warnings():
    result = translate("SELECT * FROM t WHERE ROWNUM <= 5", "oracle", strict=False)
    assert result.statements == ["SELECT * FROM t WHERE ROWNUM <= 5"]
    assert any("ROWNUM" in w for w in result.warnings)


def test_tsql_passes_through():
    assert translate("SELECT TOP 1 * FROM t", "tsql").tsql == "SELECT TOP 1 * FROM t;"


def test_unknown_dialect():
    with pytest.raises(TranslationError, match="unsupported dialect"):
        translate("SELECT 1", "cobol")


def test_parse_error():
    with pytest.raises(TranslationError, match="could not parse"):
        translate("SELECT FROM WHERE (", "postgres")
