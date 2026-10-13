# sql2tsql

Run SQL written in another dialect (PostgreSQL, MySQL, Oracle, Snowflake, BigQuery,
DuckDB, Spark, and about 25 more) against SQL Server 2025 by translating it to
T-SQL first.

```sql
EXEC dbo.run_sql @dialect = N'postgres',
                 @sql     = N'SELECT name FROM users WHERE name ILIKE ''a%'' ORDER BY id LIMIT 10';
-- runs: SELECT TOP 10 name FROM users WHERE LOWER(name) LIKE LOWER('a%') ORDER BY ... , id
```

## How it works

```
 SSMS / app ──EXEC dbo.run_sql──▶ SQL Server 2025
                                     │ sp_invoke_external_rest_endpoint (HTTPS)
                                     ▼
                              sql2tsql service ── sqlglot + SQL Server rules
                                     │ {"statements": [...]}
                                     ▼
                              sp_executesql, one statement at a time
```

- `sql2tsql/core.py` parses the input with [sqlglot](https://github.com/tobymao/sqlglot)
  and generates T-SQL.
- `sql2tsql/rules.py` adds rewrites sqlglot doesn't do for SQL Server
  (`ILIKE`, `RETURNING` → `OUTPUT INSERTED.*`, interval arithmetic → `DATEADD`,
  Oracle `FROM dual`) and refuses constructs with no faithful T-SQL equivalent.
- `sql2tsql/api.py` is the HTTP API (`POST /translate`, `GET /dialects`).
- `sql/run_sql.sql` is the stored procedure that calls the API and runs the result.

## Strict by default

A translation that would run but mean something different is worse than an
error, so by default the translator refuses anything it can't translate
faithfully and says why:

| Refused | Why |
|---|---|
| `CREATE FUNCTION/PROCEDURE/TRIGGER` from another dialect | PL/pgSQL, PL/SQL, etc. need a hand rewrite |
| `ARRAY`, `STRUCT`, `MAP`, standalone `INTERVAL` types/values | no SQL Server type |
| `UNNEST`, `EXPLODE`, `ARRAY_AGG`, lambdas | no direct equivalent |
| Oracle `ROWNUM`, `ROWID`, `LEVEL` | pseudo-columns would become ordinary columns |
| Unknown, unqualified functions | might not exist in SQL Server; schema-qualify UDFs (`dbo.f`) |
| Statements sqlglot can't parse (`VACUUM`, ...) | would pass through untranslated |

Pass `strict: false` (or `@strict = 0`) to get a best-effort translation with
the problems listed as warnings instead.

Some semantic differences are not detected, so review translations of
important queries with `@print_only = 1`: Oracle treating `''` as `NULL`,
string concatenation with `NULL`, integer division, collation and
case-sensitivity of comparisons, and `NULLS FIRST/LAST` ordering (handled with
a `CASE` sort key).

## Running it

```bash

pip install -e ".[test]"
pytest
SQL2TSQL_API_KEY=change-me uvicorn sql2tsql.api:app --port 8000
```

or `docker build -t sql2tsql . && docker run -p 8000:8000 -e SQL2TSQL_API_KEY=change-me sql2tsql`.

SQL Server only calls HTTPS endpoints, so put the service behind TLS
(a reverse proxy, Azure Container Apps, or uvicorn's `--ssl-*` options) with a
certificate SQL Server trusts.

Then in SQL Server 2025, follow the setup comment at the top of
[`sql/run_sql.sql`](sql/run_sql.sql) (enable `external rest endpoint enabled`,
create the credential holding the API key, grant permissions), change the URL
and credential name in the procedure, and create it.

```sql
EXEC dbo.run_sql N'mysql', N'SELECT `name` FROM users LIMIT 5', @print_only = 1;  -- show T-SQL only
EXEC dbo.run_sql N'mysql', N'SELECT `name` FROM users LIMIT 5';                   -- run it
```

Translated statements run with `sp_executesql` as the caller, so `run_sql`
grants no extra permissions.
