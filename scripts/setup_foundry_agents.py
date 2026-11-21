"""Deploys the Foundry prompt agents declared in agents/*.yaml (agents-as-code).

Called by the azd postdeploy hook after setup_foundry_iq.ps1 has created the knowledge base and the
project connections. Values like ${VAR} or ${VAR=default} are resolved from the process environment
(azd exports the env to hooks) and, when missing, from `azd env get-values`.

A new agent version is created only when the definition changes: its sha256 is stored in the version
metadata (definition_sha256), so re-running `azd deploy` is idempotent.

Usage: python scripts/setup_foundry_agents.py [--dry-run] [--agents-dir agents]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:=([^}]*))?\}")
KB_TOOL_NAME = "knowledge_base_retrieve"


def load_azd_env() -> dict[str, str]:
    if os.environ.get("WPC_NO_AZD") == "true":
        return {}
    try:
        out = subprocess.run(
            ["azd", "env", "get-values", "--output", "json"],
            capture_output=True, text=True, check=True, cwd=ROOT, shell=os.name == "nt",
        ).stdout
        return {k: str(v) for k, v in json.loads(out or "{}").items()}
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def make_resolver(azd_env: Callable[[], Mapping[str, str]]) -> Callable[[str], str | None]:
    cache: dict[str, Mapping[str, str]] = {}

    def resolve(name: str) -> str | None:
        if os.environ.get(name):
            return os.environ[name]
        if "azd" not in cache:
            cache["azd"] = azd_env()
        return cache["azd"].get(name) or None

    return resolve


def substitute(value: Any, resolve: Callable[[str], str | None], missing: set[str]) -> Any:
    if isinstance(value, dict):
        return {k: substitute(v, resolve, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, resolve, missing) for v in value]
    if not isinstance(value, str):
        return value

    def repl(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        resolved = resolve(name) or default
        if resolved is None:
            missing.add(name)
            return match.group(0)
        return resolved

    return PLACEHOLDER.sub(repl, value)


def load_specs(agents_dir: Path, resolve: Callable[[str], str | None]) -> list[dict[str, Any]]:
    specs = []
    for path in sorted(agents_dir.glob("*.y*ml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        missing: set[str] = set()
        spec = substitute(raw, resolve, missing)
        if missing:
            raise ValueError(
                f"{path.name}: variabili mancanti {', '.join(sorted(missing))}. Le crea scripts/setup_foundry_iq.ps1: "
                "eseguilo (o 'azd hooks run postdeploy'); senza azd carica prima i valori con ./scripts/load_env.ps1"
            )
        for key in ("name", "model", "instructions"):
            if not spec.get(key):
                raise ValueError(f"{path.name}: campo '{key}' obbligatorio")
        specs.append(spec)
    return specs


def split_list(value: Any) -> list[str]:
    """Accepts a YAML list or a comma/semicolon separated string (handy for ${VAR} placeholders)."""
    items = value if isinstance(value, list) else re.split(r"[,;]", str(value or ""))
    return [str(item).strip() for item in items if str(item).strip()]


def build_tool(tool: Mapping[str, Any]) -> dict[str, Any]:
    """Returns the tool payload of the agent definition (plain dict, SDK independent)."""
    kind = tool.get("type")
    if kind == "azure_ai_search":
        # Classic RAG: the agent queries one index directly, with its own query type and top_k.
        index: dict[str, Any] = {
            "project_connection_id": tool["connection"],
            "index_name": tool["index_name"],
            "query_type": tool.get("query_type", "simple"),
        }
        if tool.get("top_k") is not None:
            index["top_k"] = int(tool["top_k"])
        if tool.get("filter"):
            index["filter"] = tool["filter"]
        return {"type": "azure_ai_search", "azure_ai_search": {"indexes": [index]}}
    if kind == "web_search":
        payload: dict[str, Any] = {"type": "web_search"}
        domains = split_list(tool.get("allowed_domains"))
        if domains:
            payload["filters"] = {"allowed_domains": domains}
        if tool.get("search_context_size"):
            payload["search_context_size"] = tool["search_context_size"]
        return payload
    if kind == "knowledge_base":
        payload = {
            "type": "mcp",
            "server_label": tool.get("server_label", "knowledge-base"),
            "server_url": tool["server_url"],
            "require_approval": "never",
            "allowed_tools": [KB_TOOL_NAME],
            "project_connection_id": tool["connection"],
        }
    elif kind == "mcp":
        payload = {
            "type": "mcp",
            "server_label": tool["server_label"],
            "server_url": tool["server_url"],
            "require_approval": tool.get("require_approval", "never"),
        }
        if tool.get("allowed_tools"):
            payload["allowed_tools"] = list(tool["allowed_tools"])
        if tool.get("connection"):
            payload["project_connection_id"] = tool["connection"]
    else:
        raise ValueError(
            f"tipo di tool non supportato: {kind!r} (usa knowledge_base, mcp, azure_ai_search o web_search)"
        )
    if tool.get("description"):
        payload["server_description"] = tool["description"]
    return payload


def build_definition(spec: Mapping[str, Any]) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "kind": "prompt",
        "model": spec["model"],
        "instructions": spec["instructions"].strip(),
        "tools": [build_tool(t) for t in spec.get("tools") or []],
    }
    if spec.get("temperature") is not None:
        definition["temperature"] = spec["temperature"]
    return definition


def definition_hash(spec: Mapping[str, Any], definition: Mapping[str, Any]) -> str:
    payload = {"definition": definition, "description": spec.get("description", "")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def latest_hash(client: Any, name: str) -> str | None:
    from azure.core.exceptions import ResourceNotFoundError

    try:
        details = client.agents.get(name)
    except ResourceNotFoundError:
        return None
    versions = getattr(details, "versions", None) or {}
    latest = versions.get("latest") if isinstance(versions, Mapping) else None
    metadata = (latest or {}).get("metadata") if isinstance(latest, Mapping) else getattr(latest, "metadata", None)
    return (metadata or {}).get("definition_sha256")


def sdk_tool(tool: Mapping[str, Any]) -> Any:
    from azure.ai.projects.models import (
        AISearchIndexResource,
        AzureAISearchTool,
        AzureAISearchToolResource,
        MCPTool,
        WebSearchTool,
        WebSearchToolFilters,
    )

    kind = tool["type"]
    if kind == "azure_ai_search":
        indexes = [AISearchIndexResource(**i) for i in tool["azure_ai_search"]["indexes"]]
        return AzureAISearchTool(azure_ai_search=AzureAISearchToolResource(indexes=indexes))
    if kind == "web_search":
        args = {k: v for k, v in tool.items() if k not in ("type", "filters")}
        if tool.get("filters"):
            args["filters"] = WebSearchToolFilters(**tool["filters"])
        return WebSearchTool(**args)
    return MCPTool(**{k: v for k, v in tool.items() if k != "type"})


def deploy(specs: list[dict[str, Any]], endpoint: str, tenant_id: str | None) -> None:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition
    from azure.identity import AzureCliCredential, AzureDeveloperCliCredential, ChainedTokenCredential

    credential = ChainedTokenCredential(
        AzureDeveloperCliCredential(tenant_id=tenant_id), AzureCliCredential(tenant_id=tenant_id)
    )
    with AIProjectClient(endpoint=endpoint, credential=credential) as client:
        for spec in specs:
            name = spec["name"]
            definition = build_definition(spec)
            digest = definition_hash(spec, definition)
            if latest_hash(client, name) == digest:
                print(f"  invariato: {name}")
                continue
            sdk_definition = PromptAgentDefinition(
                model=definition["model"],
                instructions=definition["instructions"],
                tools=[sdk_tool(t) for t in definition["tools"]],
                **({"temperature": definition["temperature"]} if "temperature" in definition else {}),
            )
            version = client.agents.create_version(
                agent_name=name,
                definition=sdk_definition,
                description=spec.get("description"),
                metadata={"managed_by": "azd", "definition_sha256": digest},
            )
            print(f"  ok: {name} versione {version.version}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agents-dir", type=Path, default=ROOT / "agents")
    parser.add_argument("--dry-run", action="store_true", help="mostra le definizioni senza chiamare Foundry")
    args = parser.parse_args(argv)

    resolve = make_resolver(load_azd_env)
    try:
        specs = load_specs(args.agents_dir, resolve)
    except ValueError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    if not specs:
        print(f"Nessun agente in {args.agents_dir}.")
        return 0
    if args.dry_run:
        for spec in specs:
            print(json.dumps({"name": spec["name"], "definition": build_definition(spec)}, indent=2, ensure_ascii=False))
        return 0

    endpoint = resolve("FOUNDRYIQ_DEMO_PROJECT_ENDPOINT")
    if not endpoint:
        raise SystemExit("FOUNDRYIQ_DEMO_PROJECT_ENDPOINT mancante: esegui azd provision.")
    tenant = resolve("FOUNDRYIQ_DEMO_TENANT_ID") or resolve("AZURE_TENANT_ID")
    deploy(specs, endpoint, tenant)
    print(f"Agenti Foundry pronti: {', '.join(s['name'] for s in specs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
