import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup_foundry_agents as sfa  # noqa: E402

AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"


def resolver(values):
    return lambda name: values.get(name)


def test_substitute_uses_value_then_default_and_reports_missing():
    missing = set()
    out = sfa.substitute(
        {"a": "${X}", "b": ["${Y=def}"], "c": "${Z}", "n": 3}, resolver({"X": "x"}), missing
    )
    assert out == {"a": "x", "b": ["def"], "c": "${Z}", "n": 3}
    assert missing == {"Z"}


def test_load_specs_fails_on_missing_variable():
    with pytest.raises(ValueError, match="MCP_SERVER_URL"):
        sfa.load_specs(AGENTS_DIR, resolver({"FOUNDRYIQ_DEMO_KB_MCP_ENDPOINT": "https://kb"}))


def test_repo_agents_build_expected_tools():
    env = {
        "FOUNDRYIQ_DEMO_KB_MCP_ENDPOINT": "https://kb/mcp",
        "MCP_SERVER_URL": "https://app/mcp",
        "FOUNDRYIQ_SEARCH_CONNECTION_ID": "/subs/x/connections/demo-search",
    }
    specs = {s["name"]: s for s in sfa.load_specs(AGENTS_DIR, resolver(env))}

    kb_tool = sfa.build_definition(specs["wpc-technician-assistant"])["tools"][0]
    assert kb_tool["allowed_tools"] == ["knowledge_base_retrieve"]
    assert kb_tool["project_connection_id"] == "wpc-foundryiq-kb-mcp"
    assert kb_tool["server_url"] == "https://kb/mcp"

    mcp_tool = sfa.build_definition(specs["wpc-magazzino-assistant"])["tools"][0]
    assert mcp_tool["project_connection_id"] == "mcp-ricambi-key"
    assert "search_parts" in mcp_tool["allowed_tools"]


def test_classic_rag_agents_share_sources_with_different_retrieval():
    env = {
        "FOUNDRYIQ_DEMO_KB_MCP_ENDPOINT": "https://kb/mcp",
        "MCP_SERVER_URL": "https://app/mcp",
        "FOUNDRYIQ_SEARCH_CONNECTION_ID": "/subs/x/connections/demo-search",
        "FOUNDRYIQ_BING_ALLOWED_DOMAINS": "a.com; b.com",
    }
    specs = {s["name"]: s for s in sfa.load_specs(AGENTS_DIR, resolver(env))}
    fast, accurate = (
        {t["type"]: t for t in sfa.build_definition(specs[name])["tools"]}
        for name in ("wpc-rag-classico-rapido", "wpc-rag-classico-accurato")
    )
    assert fast.keys() == accurate.keys() == {"azure_ai_search", "mcp", "web_search"}
    fast_index = fast["azure_ai_search"]["azure_ai_search"]["indexes"][0]
    accurate_index = accurate["azure_ai_search"]["azure_ai_search"]["indexes"][0]
    assert fast_index["index_name"] == accurate_index["index_name"] == "machinespecs-ks-index"
    assert fast_index["project_connection_id"] == "/subs/x/connections/demo-search"
    assert (fast_index["query_type"], fast_index["top_k"]) == ("simple", 3)
    assert (accurate_index["query_type"], accurate_index["top_k"]) == ("semantic", 10)
    assert fast["web_search"]["filters"] == {"allowed_domains": ["a.com", "b.com"]}
    assert len(fast["mcp"]["allowed_tools"]) < len(accurate["mcp"]["allowed_tools"])


def test_sdk_tool_builds_typed_models():
    search = sfa.sdk_tool(sfa.build_tool(
        {"type": "azure_ai_search", "connection": "c", "index_name": "i", "query_type": "semantic", "top_k": 5}
    ))
    assert search.azure_ai_search.indexes[0].index_name == "i"
    web = sfa.sdk_tool(sfa.build_tool({"type": "web_search", "allowed_domains": ["x.com"]}))
    assert web.filters.allowed_domains == ["x.com"]


def test_hash_changes_only_with_definition():
    spec = {"name": "a", "model": "m", "instructions": "hi", "description": "d"}
    first = sfa.definition_hash(spec, sfa.build_definition(spec))
    assert first == sfa.definition_hash(spec, sfa.build_definition(dict(spec)))
    changed = {**spec, "instructions": "hello"}
    assert first != sfa.definition_hash(changed, sfa.build_definition(changed))


def test_unknown_tool_type_rejected():
    with pytest.raises(ValueError):
        sfa.build_tool({"type": "code_interpreter"})
