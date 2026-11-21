import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inventory  # noqa: E402


def test_italian_query_finds_english_parts():
    titles = [r["part_number"] for r in inventory.search_parts("filtro membrana")["results"]]
    assert set(titles[:2]) == {"DSE-007", "GRT-002"}


def test_typo_and_machine_filter():
    results = inventory.search_parts("interlok swich", machine="Rx1")["results"]
    assert [r["part_number"] for r in results] == ["RX1-SIS-006"]


def test_results_have_foundry_iq_fields():
    result = inventory.search_parts("GRT-002")["results"][0]
    assert result["part_number"] == "GRT-002"
    assert {"id", "title", "content"} <= result.keys()
    assert "Graphene Filter Membrane" in result["content"]


def test_check_stock_reports_unknown_parts_and_warehouses():
    out = inventory.check_stock(["GRT-002", "NOPE-1"], "wh-main")
    assert out["count"] == 1 and "NOPE-1" in out["message"]
    assert inventory.check_stock(["GRT-002"], "XX")["count"] == 0


def test_alternates_are_directional():
    assert [r["part_number"] for r in inventory.find_alternatives("AD-60W-24")["results"]] == ["AD-90W-24"]
    assert inventory.find_alternatives("AD-90W-24")["count"] == 0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "test-key")
    import server

    with TestClient(server.create_app()) as c:
        yield c


def _rpc(client, method, params, headers):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"accept": "application/json, text/event-stream", **headers},
    )


def test_http_requires_api_key_and_serves_tools(client):
    assert _rpc(client, "tools/list", {}, {}).status_code == 401
    resp = _rpc(client, "tools/call", {"name": "search_parts", "arguments": {"query": "ugello", "top": 3}},
                {"x-api-key": "test-key"})
    assert resp.status_code == 200
    data = json.loads(resp.json()["result"]["content"][0]["text"])
    assert data["count"] == 3 and all(r["category"] == "nozzle" for r in data["results"])


def test_search_parts_accepts_foundry_iq_queries_list(client):
    # Shape sent by Foundry IQ query planning for mcpServer knowledge sources.
    args = {"queries": ["Rx1 Digital Weave Integration Station ricambi sotto scorta", "Rx1 spare parts backorder arrival"]}
    resp = _rpc(client, "tools/call", {"name": "search_parts", "arguments": args}, {"x-api-key": "test-key"})
    result = resp.json()["result"]
    assert not result.get("isError"), result
    data = json.loads(result["content"][0]["text"])
    assert data["count"] > 0 and all(r["machine"].startswith("Rx1") for r in data["results"][:3])


class _FakeCursor:
    """Serves the rows sql_source.load() selects, derived from the bundled JSON."""

    def __init__(self, data):
        parts = data["parts"]
        self.tables = {
            "warehouses": [(w["code"], w["name"], w["site"], w["type"]) for w in data["warehouses"]],
            "parts": [tuple(p[k] for k in ("part_number", "name", "machine", "category", "standard", "description",
                                           "maintenance_note", "unit_cost_eur", "supplier", "lead_time_days",
                                           "reorder_point", "criticality")) for p in parts],
            "part_stock": [(p["part_number"], s["warehouse"], s["bin"], s["on_hand"], s["reserved"])
                           for p in parts for s in p["stock"]],
            "purchase_orders": [(p["part_number"], o["po"], o["quantity"], date.fromisoformat(o["eta"]), o["status"])
                                for p in parts for o in p["open_orders"]],
            "part_alternates": [(p["part_number"], a["part_number"], a["note"]) for p in parts for a in p["alternates"]],
        }

    def execute(self, sql):
        table = re.search(r"FROM dbo\.(\w+)", sql).group(1)
        columns = re.search(r"SELECT (.*?) FROM", sql, re.S).group(1).split(",")
        self.description = [(c.strip(),) for c in columns]
        self.rows = self.tables[table]

    def fetchall(self):
        return self.rows


class _FakeConnection:
    def __init__(self, data):
        self._cursor = _FakeCursor(data)

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def test_sql_source_rebuilds_json_shape(monkeypatch):
    import sql_source

    data = json.loads(inventory.DATA_FILE.read_text(encoding="utf-8"))
    monkeypatch.setattr(sql_source, "connect", lambda *a, **k: _FakeConnection(data))
    assert sql_source.load() == data


def test_connection_string_uses_user_assigned_identity(monkeypatch):
    import sql_source

    monkeypatch.setenv("SQL_CLIENT_ID", "11111111-2222-3333-4444-555555555555")
    msi = sql_source.connection_string("srv", "db", "ActiveDirectoryMSI")
    assert msi.endswith(";UID=11111111-2222-3333-4444-555555555555")
    assert "UID=" not in sql_source.connection_string("srv", "db", "ActiveDirectoryDefault")


class _SeedConnection:
    def __init__(self, stored_hash):
        self.stored_hash = stored_hash
        self.statements = []

    def cursor(self):
        conn = self

        class _Cursor:
            def execute(self, sql, params=None):
                conn.statements.append(sql)

            def fetchone(self):
                return (conn.stored_hash,) if conn.stored_hash else None

        return _Cursor()

    def commit(self):
        pass


def test_ensure_seeded_reseeds_only_when_data_changes(monkeypatch):
    import hashlib

    import sql_source

    seeded = []
    monkeypatch.setattr(sql_source, "seed", lambda conn, data: seeded.append(data) or {"parts": len(data["parts"])})
    digest = hashlib.sha256(sql_source.DATA_FILE.read_bytes()).hexdigest()

    current = _SeedConnection(digest)
    assert sql_source.ensure_seeded(current) is None and not seeded
    assert "sp_releaseapplock" in current.statements[-1]

    stale = _SeedConnection("old-hash")
    assert sql_source.ensure_seeded(stale)["parts"] > 0 and len(seeded) == 1
    assert any(s.startswith("MERGE dbo.seed_meta") for s in stale.statements)


def test_sql_failure_falls_back_to_json(monkeypatch):
    import sql_source

    def boom():
        raise ConnectionError("database paused")

    monkeypatch.setattr(sql_source, "load", boom)
    monkeypatch.setattr(inventory, "SOURCE", "sql")
    monkeypatch.setattr(inventory, "FALLBACK_TO_JSON", True)
    monkeypatch.setattr(inventory, "_state", {"data": None, "expires": 0.0, "source": None, "error": None})
    status = inventory.status()
    assert status["source"] == "json-fallback" and status["parts"] > 0
    assert "database paused" in status["error"]
    assert inventory.search_parts("filtro membrana")["count"] > 0
