"""Streamable HTTP MCP server for spare parts and warehouse search (Foundry IQ knowledge source)."""

from __future__ import annotations

import hmac
import os
from typing import Annotated, Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

import inventory

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

mcp = MCPServer(
    name="ricambi-magazzino",
    title="Spare Parts & Warehouse",
    version="1.0.0",
    instructions=(
        "Read-only catalog of spare parts and warehouse stock for the Zava manufacturing machines. "
        "Use search_parts for free-text questions (Italian or English), get_part_details for a known part number, "
        "check_stock for availability, find_alternative_parts when a part is out of stock. "
        "Every tool returns {count, results[]} where each result has id, title and content."
    ),
)


@mcp.tool(annotations=READ_ONLY)
def search_parts(
    query: Annotated[str | None, Field(description="Free-text search: part name, symptom keyword, part number or machine, e.g. 'filter membrane reclaim tank' or 'sensore allineamento'.")] = None,
    queries: Annotated[list[str] | None, Field(max_length=10, description="Alternative to query: several search phrases, results are merged by relevance.")] = None,
    machine: Annotated[str | None, Field(description="Optional machine name or part-number prefix filter, e.g. 'Rx1', 'Graphene Vapor Reclaim Tank', 'VS12'.")] = None,
    category: Annotated[str | None, Field(description="Optional category: sensor, filter, nozzle, motor, actuator, roller, control, safety, thermal, fluidics, consumable, print-head, robotics, laser, feed, blade, mechanical, electrical.")] = None,
    warehouse: Annotated[str | None, Field(description="Optional warehouse code: WH-MAIN, WH-LINE-A, WH-LINE-B, VAN-SVC-01.")] = None,
    in_stock_only: Annotated[bool, Field(description="Return only parts with available quantity > 0.")] = False,
    top: Annotated[int, Field(ge=1, le=50, description="Maximum number of results.")] = 10,
) -> dict[str, Any]:
    """Search the spare parts catalog and return matching parts with stock, bin locations, supplier, lead time, open orders and alternates."""
    # Foundry IQ query planning may send {"queries": [...]} instead of {"query": "..."}: accept both.
    terms = ([query] if query else []) + list(queries or [])
    return inventory.search_parts_multi(terms, machine, category, warehouse, in_stock_only, top)


@mcp.tool(annotations=READ_ONLY)
def get_part_details(
    part_number: Annotated[str, Field(description="Exact part number, e.g. 'GRT-002' or 'RX1-SEH-001'.")],
) -> dict[str, Any]:
    """Get full details for one spare part: description, standard, maintenance note, stock per warehouse, open orders and alternates."""
    return inventory.get_part(part_number)


@mcp.tool(annotations=READ_ONLY)
def check_stock(
    part_numbers: Annotated[list[str], Field(min_length=1, max_length=25, description="One or more part numbers.")],
    warehouse: Annotated[str | None, Field(description="Optional warehouse code to restrict availability.")] = None,
) -> dict[str, Any]:
    """Check available quantity (on hand minus reserved) for parts, overall or in one warehouse, with next delivery when out of stock."""
    return inventory.check_stock(part_numbers, warehouse)


@mcp.tool(annotations=READ_ONLY)
def list_machine_parts(
    machine: Annotated[str, Field(description="Machine name or prefix, e.g. 'Ven Systems Model 12', 'OQTR', 'Delta Nano Circuit'.")],
) -> dict[str, Any]:
    """List the bill of spare parts for a machine with current stock status."""
    return inventory.list_machine_parts(machine)


@mcp.tool(annotations=READ_ONLY)
def find_alternative_parts(
    part_number: Annotated[str, Field(description="Part number that is unavailable.")],
) -> dict[str, Any]:
    """Find approved interchangeable parts for a part number, with their availability and substitution notes."""
    return inventory.find_alternatives(part_number)


@mcp.tool(annotations=READ_ONLY)
def low_stock_report(
    warehouse: Annotated[str | None, Field(description="Optional warehouse code.")] = None,
    criticality: Annotated[str | None, Field(description="Optional criticality filter: high, medium, low.")] = None,
) -> dict[str, Any]:
    """List parts that are out of stock or below reorder point, most critical first, with open purchase orders."""
    return inventory.low_stock_report(warehouse, criticality)


@mcp.tool(annotations=READ_ONLY)
def list_warehouses() -> dict[str, Any]:
    """List warehouses and service vans with codes, type, stocked part numbers and units on hand."""
    return inventory.list_warehouses()


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "inventory": await run_in_threadpool(inventory.status)})


class ApiKeyMiddleware:
    """Require the MCP_API_KEY value in the x-api-key header (Foundry IQ storedHeaders) on /mcp."""

    def __init__(self, app: Any, api_key: str | None) -> None:
        self.app = app
        self.api_key = api_key.encode() if api_key else None

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if self.api_key and scope["type"] == "http" and scope["path"].startswith("/mcp"):
            provided = dict(scope["headers"]).get(b"x-api-key", b"")
            if not hmac.compare_digest(provided, self.api_key):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app() -> Any:
    allowed_hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(allowed_hosts),
        allowed_hosts=allowed_hosts,
        allowed_origins=[],
    )
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, transport_security=security)
    return ApiKeyMiddleware(app, os.environ.get("MCP_API_KEY"))


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
