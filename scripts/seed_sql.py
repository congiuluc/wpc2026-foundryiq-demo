"""Create the spare parts schema in Azure SQL, load data/inventory.json and grant the MCP App Service
managed identity read access. Idempotent: safe to rerun on every `azd provision`.

Usage:
  python scripts/seed_sql.py --server <fqdn> --database ricambi [--app-name <webapp> --app-client-id <guid>]
Authentication uses the signed-in Entra identity (Azure CLI / azd) through ActiveDirectoryDefault.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-ricambi"))

import sql_source  # noqa: E402


def grant_reader(conn, app_name: str, client_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9-]+", app_name):
        raise ValueError(f"Nome app non valido: {app_name}")
    sid = "0x" + uuid.UUID(client_id).bytes_le.hex().upper()
    cur = conn.cursor()
    # SID-based creation avoids requiring Microsoft Graph permissions on the SQL server identity.
    cur.execute(
        f"IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'{app_name}') "
        f"CREATE USER [{app_name}] WITH SID = {sid}, TYPE = E;"
    )
    cur.execute(
        "IF NOT EXISTS (SELECT 1 FROM sys.database_role_members rm "
        "JOIN sys.database_principals r ON r.principal_id = rm.role_principal_id "
        "JOIN sys.database_principals m ON m.principal_id = rm.member_principal_id "
        f"WHERE r.name = N'db_datareader' AND m.name = N'{app_name}') "
        f"ALTER ROLE db_datareader ADD MEMBER [{app_name}];"
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--data", default=str(ROOT / "mcp-ricambi" / "data" / "inventory.json"))
    parser.add_argument("--app-name")
    parser.add_argument("--app-client-id")
    args = parser.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    conn = sql_source.connect(sql_source.connection_string(args.server, args.database, "ActiveDirectoryDefault"))
    try:
        counts = sql_source.seed(conn, data)
        print("Dati caricati:", ", ".join(f"{k}={v}" for k, v in counts.items()))
        if args.app_name and args.app_client_id:
            grant_reader(conn, args.app_name, args.app_client_id)
            print(f"Identita gestita '{args.app_name}' abilitata in lettura (db_datareader).")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
