import pytest
from fastapi.testclient import TestClient

from sql2tsql.api import app

client = TestClient(app)


def test_translate():
    r = client.post("/translate", json={"sql": "SELECT a FROM t LIMIT 5", "dialect": "postgres"})
    assert r.status_code == 200
    body = r.json()
    assert body["statements"] == ["SELECT TOP 5 a FROM t"]
    assert body["tsql"] == "SELECT TOP 5 a FROM t;"
    assert body["warnings"] == []


def test_untranslatable_sql_is_422_with_reason():
    r = client.post("/translate", json={"sql": "SELECT ARRAY_AGG(x) FROM t", "dialect": "bigquery"})
    assert r.status_code == 422
    assert "ARRAY_AGG" in r.json()["error"]


def test_dialects():
    assert "postgres" in client.get("/dialects").json()["dialects"]


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("SQL2TSQL_API_KEY", "secret")


def test_api_key_required_when_configured(api_key):
    payload = {"sql": "SELECT 1", "dialect": "mysql"}
    assert client.post("/translate", json=payload).status_code == 401
    assert client.post("/translate", json=payload, headers={"X-Api-Key": "wrong"}).status_code == 401
    assert client.post("/translate", json=payload, headers={"X-Api-Key": "secret"}).status_code == 200
    assert client.get("/health").status_code == 200
