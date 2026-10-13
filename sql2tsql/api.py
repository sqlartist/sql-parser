"""HTTP API for the translator, called by dbo.run_sql through sp_invoke_external_rest_endpoint.

Run with:  uvicorn sql2tsql.api:app --host 0.0.0.0 --port 8000
SQL Server only calls HTTPS endpoints, so put it behind a TLS-terminating proxy
or run uvicorn with --ssl-keyfile/--ssl-certfile.

If the SQL2TSQL_API_KEY environment variable is set, every request must send it
in the X-Api-Key header.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .core import SUPPORTED_DIALECTS, TranslationError, translate

app = FastAPI(title="sql2tsql", description="Translate any SQL dialect to T-SQL")


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("SQL2TSQL_API_KEY")
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="invalid or missing X-Api-Key")


class TranslateRequest(BaseModel):
    sql: str = Field(description="SQL text in the source dialect; may contain several statements")
    dialect: str = Field(description="Source dialect, e.g. postgres, mysql, oracle, snowflake")
    strict: bool = Field(default=True, description="Refuse anything that cannot be translated faithfully")
    pretty: bool = False


class TranslateResponse(BaseModel):
    dialect: str
    tsql: str
    statements: list[str]
    warnings: list[str]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/dialects", dependencies=[Depends(require_api_key)])
def dialects() -> dict[str, list[str]]:
    return {"dialects": list(SUPPORTED_DIALECTS)}


@app.post("/translate", response_model=TranslateResponse, dependencies=[Depends(require_api_key)])
def translate_endpoint(req: TranslateRequest):
    try:
        result = translate(req.sql, req.dialect, strict=req.strict, pretty=req.pretty)
    except TranslationError as e:
        return JSONResponse(status_code=422, content={"error": str(e), "problems": e.problems})
    return TranslateResponse(
        dialect=result.source_dialect,
        tsql=result.tsql,
        statements=result.statements,
        warnings=result.warnings,
    )
