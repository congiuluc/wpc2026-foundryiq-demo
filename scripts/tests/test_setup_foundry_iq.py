import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")


@pytest.mark.skipif(PWSH is None, reason="PowerShell is required")
@pytest.mark.parametrize(
    ("url_configured", "skip_sharepoint", "indexed_configured", "indexed_query", "valid"),
    [
        (False, False, False, "", True),
        (True, False, False, "", True),
        (True, True, False, "", True),
        (True, False, True, "", True),
        (True, True, True, "", True),
        (False, False, True, "includeFolder=https://example.sharepoint.com/custom", True),
        (True, False, True, "includeFolder=https://example.sharepoint.com/custom", True),
        (False, False, True, "", False),
    ],
)
def test_knowledge_base_retrieval_instructions_follow_available_sources(
    tmp_path, url_configured, skip_sharepoint, indexed_configured, indexed_query, valid
):
    with_sharepoint = url_configured and not skip_sharepoint
    with_indexed_sharepoint = indexed_configured and not skip_sharepoint
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("FOUNDRYIQ_", "SHAREPOINT_", "MCP_")):
            env.pop(key)
    capture = tmp_path / "requests.jsonl"
    env.update(
        {
            "WPC_NO_AZD": "true",
            "AZURE_ENV_NAME": "",
            "TEST_CAPTURE": str(capture),
            "FOUNDRYIQ_DEMO_SEARCH_ENDPOINT": "https://search.example",
            "FOUNDRYIQ_DEMO_OPENAI_ENDPOINT": "https://openai.example",
            "FOUNDRYIQ_DEMO_STORAGE_RESOURCE_ID": "/subscriptions/test/storage",
            "FOUNDRYIQ_BING_ALLOWED_DOMAINS": "example.com",
            "MCP_SERVER_URL": "https://mcp.example/mcp",
            "MCP_API_KEY": "test-key",
            "FOUNDRY_PROJECT_ID": "/subscriptions/test/projects/demo",
            "FOUNDRYIQ_ACL_GROUP_TECNICI_ID": "test-group",
            "FOUNDRYIQ_ACL_STORAGE_RESOURCE_ID": "/subscriptions/test/acl-storage",
            "SKIP_ACL_DEMO_SETUP": "false",
            "SKIP_SHAREPOINT_SETUP": "true" if skip_sharepoint else "false",
            "SHAREPOINT_POLICIES_URL": "https://example.sharepoint.com/policies" if url_configured else "",
            "SHAREPOINT_INDEXED_CONNECTION_STRING": (
                "SharePointOnlineEndpoint=https://example.sharepoint.com;"
                "ApplicationId=11111111-1111-1111-1111-111111111111;ApplicationSecret=test-secret"
                if indexed_configured else ""
            ),
            "SHAREPOINT_INDEXED_QUERY": indexed_query,
        }
    )
    script = str(ROOT / "scripts" / "setup_foundry_iq.ps1").replace("'", "''")
    runner = rf"""
$ErrorActionPreference = 'Stop'
function global:az {{
    $parts = @($args)
    if ($parts[0] -ne 'rest') {{ throw 'Unexpected Azure CLI command' }}
    $global:LASTEXITCODE = 0
    $method = $parts[[array]::IndexOf($parts, '--method') + 1]
    if ($method -eq 'get') {{
        return '{{"azureBlobParameters":{{"createdResources":{{"index":"manuals-index"}}}}}}'
    }}
    if ($method -ne 'put') {{ throw "Unexpected REST method: $method" }}
    $url = $parts[[array]::IndexOf($parts, '--url') + 1]
    $bodyPath = $parts[[array]::IndexOf($parts, '--body') + 1].Substring(1)
    $body = Get-Content -Raw -LiteralPath $bodyPath | ConvertFrom-Json
    [pscustomobject]@{{ url = $url; body = $body }} | ConvertTo-Json -Compress -Depth 30 |
        Add-Content -LiteralPath $env:TEST_CAPTURE -Encoding utf8NoBOM
}}
& '{script}' -KnowledgeBaseName 'demo-test' -BlobKnowledgeSourceName 'manuals-test' `
    -McpKnowledgeSourceName 'parts-test' -SharePointKnowledgeSourceName 'policies-test' `
    -IndexedSharePointKnowledgeSourceName 'policies-indexed-test' `
    -BingKnowledgeSourceName 'web-test'
"""
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", runner],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert "test-secret" not in result.stdout + result.stderr
    if not valid:
        assert result.returncode != 0
        assert "SHAREPOINT_POLICIES_URL" in result.stderr
        assert "SHAREPOINT_INDEXED_QUERY" in result.stderr
        assert not capture.exists()
        return
    assert result.returncode == 0, result.stderr or result.stdout
    requests = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    kb = next(item["body"] for item in requests if "/knowledgebases/demo-test?" in item["url"])
    instructions = kb["retrievalInstructions"]

    assert isinstance(instructions, str)
    assert all(name in instructions for name in ("manuals-test", "parts-test", "web-test"))
    assert all(tool in instructions for tool in ("search_parts", "check_stock", "low_stock_report"))
    kb_sources = [source["name"] for source in kb["knowledgeSources"]]
    assert "policies-test" not in kb_sources
    assert "'policies-test'" not in instructions
    assert ("policies-indexed-test" in instructions) is with_indexed_sharepoint
    assert ("policies-indexed-test" in kb_sources) is with_indexed_sharepoint
    assert any("/knowledgesources/policies-test?" in item["url"] for item in requests) is with_sharepoint
    indexed_requests = [
        item["body"] for item in requests
        if "/knowledgesources/policies-indexed-test?" in item["url"]
    ]
    assert bool(indexed_requests) is with_indexed_sharepoint
    if with_indexed_sharepoint:
        source = indexed_requests[0]
        assert source["kind"] == "indexedSharePoint"
        parameters = source["indexedSharePointParameters"]
        assert parameters["connectionString"] == env["SHAREPOINT_INDEXED_CONNECTION_STRING"]
        assert parameters["containerName"] == "useQuery"
        assert parameters["query"] == (indexed_query or "includeFolder=https://example.sharepoint.com/policies")
        ingestion = parameters["ingestionParameters"]
        assert ingestion["contentExtractionMode"] == "minimal"
        assert ingestion["ingestionPermissionOptions"] == ["userIds", "groupIds"]
        assert ingestion["embeddingModel"]["azureOpenAIParameters"] == {
            "resourceUri": "https://openai.example",
            "deploymentId": "text-embedding-3-large",
            "modelName": "text-embedding-3-large",
        }
        assert "chatCompletionModel" not in ingestion
    assert kb["models"] == [
        {
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": "https://openai.example",
                "deploymentId": "gpt-5.2",
                "modelName": "gpt-5.2",
            },
        }
    ]
    assert kb["retrievalReasoningEffort"] == {"kind": "low"}
    assert kb["outputMode"] == "extractiveData"

    acl_kb = next(item["body"] for item in requests if "/knowledgebases/wpc-acl-kb?" in item["url"])
    assert "retrievalInstructions" not in acl_kb
    assert acl_kb["models"][0]["azureOpenAIParameters"]["modelName"] == "gpt-5-mini"
    assert acl_kb["retrievalReasoningEffort"] == {"kind": "minimal"}
