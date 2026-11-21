"""Azure SQL Database backend for the spare parts catalog.

Loads the same structure as ``data/inventory.json`` (``{"warehouses": [...], "parts": [...]}``)
so the search logic in ``inventory.py`` is identical for both sources. Authentication is
Microsoft Entra only: the App Service managed identity in Azure (``ActiveDirectoryMSI``, user-assigned
when ``SQL_CLIENT_ID`` is set) and the developer identity locally (``ActiveDirectoryDefault``, e.g. Azure CLI login).

With ``SQL_AUTO_SEED=true`` the server loads ``data/inventory.json`` into the database itself; this is used
when the SQL server only has a private endpoint and the azd hooks cannot reach it from the developer machine.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DATA_FILE = Path(__file__).parent / "data" / "inventory.json"
_seed_checked = False

SCHEMA = """
IF OBJECT_ID('dbo.warehouses') IS NULL
CREATE TABLE dbo.warehouses (
    code NVARCHAR(20) NOT NULL PRIMARY KEY,
    name NVARCHAR(200) NOT NULL,
    site NVARCHAR(200) NOT NULL,
    type NVARCHAR(50) NOT NULL
);
IF OBJECT_ID('dbo.parts') IS NULL
CREATE TABLE dbo.parts (
    part_number NVARCHAR(40) NOT NULL PRIMARY KEY,
    seq INT NOT NULL,
    name NVARCHAR(200) NOT NULL,
    machine NVARCHAR(200) NOT NULL,
    category NVARCHAR(50) NOT NULL,
    standard NVARCHAR(200) NULL,
    description NVARCHAR(MAX) NOT NULL,
    maintenance_note NVARCHAR(MAX) NULL,
    unit_cost_eur DECIMAL(12, 2) NOT NULL,
    supplier NVARCHAR(200) NOT NULL,
    lead_time_days INT NOT NULL,
    reorder_point INT NOT NULL,
    criticality NVARCHAR(10) NOT NULL
);
IF OBJECT_ID('dbo.part_stock') IS NULL
CREATE TABLE dbo.part_stock (
    part_number NVARCHAR(40) NOT NULL REFERENCES dbo.parts(part_number),
    warehouse NVARCHAR(20) NOT NULL REFERENCES dbo.warehouses(code),
    bin NVARCHAR(40) NOT NULL,
    seq INT NOT NULL,
    on_hand INT NOT NULL,
    reserved INT NOT NULL,
    CONSTRAINT PK_part_stock PRIMARY KEY (part_number, warehouse, bin)
);
IF OBJECT_ID('dbo.purchase_orders') IS NULL
CREATE TABLE dbo.purchase_orders (
    po NVARCHAR(40) NOT NULL,
    part_number NVARCHAR(40) NOT NULL REFERENCES dbo.parts(part_number),
    seq INT NOT NULL,
    quantity INT NOT NULL,
    eta DATE NOT NULL,
    status NVARCHAR(40) NOT NULL,
    CONSTRAINT PK_purchase_orders PRIMARY KEY (po, part_number)
);
IF OBJECT_ID('dbo.part_alternates') IS NULL
CREATE TABLE dbo.part_alternates (
    part_number NVARCHAR(40) NOT NULL REFERENCES dbo.parts(part_number),
    alternate_part_number NVARCHAR(40) NOT NULL,
    seq INT NOT NULL,
    note NVARCHAR(400) NOT NULL,
    CONSTRAINT PK_part_alternates PRIMARY KEY (part_number, alternate_part_number)
);
"""


def connection_string(
    server: str | None = None, database: str | None = None, authentication: str | None = None
) -> str:
    server = server or os.environ["SQL_SERVER"]
    database = database or os.environ["SQL_DATABASE"]
    authentication = authentication or os.environ.get("SQL_AUTHENTICATION", "ActiveDirectoryDefault")
    conn_str = (
        f"Server=tcp:{server},1433;Database={database};Encrypt=yes;TrustServerCertificate=no;"
        f"Authentication={authentication}"
    )
    client_id = os.environ.get("SQL_CLIENT_ID", "").strip()
    if client_id and authentication.lower() == "activedirectorymsi":
        conn_str += f";UID={client_id}"
    return conn_str


def connect(conn_str: str | None = None, attempts: int = 6) -> Any:
    """Open a connection, retrying while a serverless (free offer) database resumes from auto-pause."""
    import mssql_python

    conn_str = conn_str or connection_string()
    for attempt in range(1, attempts + 1):
        try:
            return mssql_python.connect(conn_str)
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(min(10 * attempt, 30))
    raise RuntimeError("unreachable")


def _rows(cursor: Any, sql: str) -> list[dict[str, Any]]:
    cursor.execute(sql)
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def ensure_seeded(conn: Any, data_file: Path = DATA_FILE) -> dict[str, int] | None:
    """Seed the database when its content does not match ``data_file`` (hash stored in dbo.seed_meta).

    An application lock serializes concurrent workers; returns the row counts when data was (re)loaded.
    """
    raw = data_file.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    cur = conn.cursor()
    cur.execute("EXEC sp_getapplock @Resource = N'ricambi-seed', @LockMode = 'Exclusive', @LockOwner = 'Session', @LockTimeout = 120000")
    try:
        cur.execute(
            "IF OBJECT_ID('dbo.seed_meta') IS NULL "
            "CREATE TABLE dbo.seed_meta (id INT NOT NULL PRIMARY KEY, data_hash NVARCHAR(64) NOT NULL, seeded_at DATETIME2 NOT NULL)"
        )
        conn.commit()
        cur.execute("SELECT data_hash FROM dbo.seed_meta WHERE id = 1")
        row = cur.fetchone()
        if row and row[0] == digest:
            return None
        counts = seed(conn, json.loads(raw.decode("utf-8")))
        cur.execute(
            "MERGE dbo.seed_meta AS t USING (SELECT 1 AS id) AS s ON t.id = s.id "
            "WHEN MATCHED THEN UPDATE SET data_hash = ?, seeded_at = SYSUTCDATETIME() "
            "WHEN NOT MATCHED THEN INSERT (id, data_hash, seeded_at) VALUES (1, ?, SYSUTCDATETIME());",
            (digest, digest),
        )
        conn.commit()
        return counts
    finally:
        cur.execute("EXEC sp_releaseapplock @Resource = N'ricambi-seed', @LockOwner = 'Session'")


def load() -> dict[str, Any]:
    global _seed_checked
    conn = connect(attempts=int(os.environ.get("SQL_CONNECT_ATTEMPTS", "3")))
    try:
        if not _seed_checked and os.environ.get("SQL_AUTO_SEED", "false").strip().lower() in {"1", "true", "yes"}:
            counts = ensure_seeded(conn)
            if counts:
                logger.info("Database seeded from %s: %s", DATA_FILE.name, counts)
            _seed_checked = True
        cur = conn.cursor()
        warehouses = _rows(cur, "SELECT code, name, site, type FROM dbo.warehouses ORDER BY code")
        parts = _rows(cur, """
            SELECT part_number, name, machine, category, standard, description, maintenance_note,
                   unit_cost_eur, supplier, lead_time_days, reorder_point, criticality
            FROM dbo.parts ORDER BY seq""")
        stock = _rows(cur, "SELECT part_number, warehouse, bin, on_hand, reserved FROM dbo.part_stock ORDER BY part_number, seq")
        orders = _rows(cur, "SELECT part_number, po, quantity, eta, status FROM dbo.purchase_orders ORDER BY part_number, seq")
        alternates = _rows(cur, "SELECT part_number, alternate_part_number, note FROM dbo.part_alternates ORDER BY part_number, seq")
    finally:
        conn.close()

    by_part: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for s in stock:
        pn = s.pop("part_number")
        by_part[pn]["stock"].append(s)
    for o in orders:
        pn = o.pop("part_number")
        o["eta"] = o["eta"].isoformat() if hasattr(o["eta"], "isoformat") else str(o["eta"])
        by_part[pn]["open_orders"].append(o)
    for a in alternates:
        by_part[a["part_number"]]["alternates"].append({"part_number": a["alternate_part_number"], "note": a["note"]})

    for p in parts:
        p["unit_cost_eur"] = float(p["unit_cost_eur"])
        children = by_part.get(p["part_number"], {})
        p["stock"] = list(children.get("stock", []))
        p["open_orders"] = list(children.get("open_orders", []))
        p["alternates"] = list(children.get("alternates", []))
    return {"warehouses": warehouses, "parts": parts}


def seed(conn: Any, data: dict[str, Any]) -> dict[str, int]:
    """Create the schema if needed and replace all rows with ``data`` in a single transaction."""
    cur = conn.cursor()
    for statement in SCHEMA.strip().split(";"):
        if statement.strip():
            cur.execute(statement)
    conn.commit()

    for table in ("part_alternates", "purchase_orders", "part_stock", "parts", "warehouses"):
        cur.execute(f"DELETE FROM dbo.{table}")
    cur.executemany(
        "INSERT INTO dbo.warehouses (code, name, site, type) VALUES (?, ?, ?, ?)",
        [(w["code"], w["name"], w["site"], w["type"]) for w in data["warehouses"]],
    )
    cur.executemany(
        """INSERT INTO dbo.parts (part_number, seq, name, machine, category, standard, description, maintenance_note,
               unit_cost_eur, supplier, lead_time_days, reorder_point, criticality)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (p["part_number"], i, p["name"], p["machine"], p["category"], p.get("standard"), p["description"],
             p.get("maintenance_note"), p["unit_cost_eur"], p["supplier"], p["lead_time_days"], p["reorder_point"],
             p["criticality"])
            for i, p in enumerate(data["parts"])
        ],
    )
    stock = [(p["part_number"], s["warehouse"], s["bin"], i, s["on_hand"], s["reserved"])
             for p in data["parts"] for i, s in enumerate(p["stock"])]
    orders = [(o["po"], p["part_number"], i, o["quantity"], o["eta"], o["status"])
              for p in data["parts"] for i, o in enumerate(p["open_orders"])]
    alternates = [(p["part_number"], a["part_number"], i, a["note"])
                  for p in data["parts"] for i, a in enumerate(p["alternates"])]
    if stock:
        cur.executemany(
            "INSERT INTO dbo.part_stock (part_number, warehouse, bin, seq, on_hand, reserved) VALUES (?, ?, ?, ?, ?, ?)", stock)
    if orders:
        cur.executemany(
            "INSERT INTO dbo.purchase_orders (po, part_number, seq, quantity, eta, status) VALUES (?, ?, ?, ?, ?, ?)", orders)
    if alternates:
        cur.executemany(
            "INSERT INTO dbo.part_alternates (part_number, alternate_part_number, seq, note) VALUES (?, ?, ?, ?)", alternates)
    conn.commit()
    return {"warehouses": len(data["warehouses"]), "parts": len(data["parts"]), "stock": len(stock),
            "purchase_orders": len(orders), "alternates": len(alternates)}
