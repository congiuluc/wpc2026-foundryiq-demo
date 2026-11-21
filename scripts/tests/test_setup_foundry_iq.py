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
    ("url_configured", "skip_sharepoint", "with_sharepoint"),
    [(False, False, False), (True, False, True), (True, True, False)],
)
def test_knowledge_base_retrieval_instructions_follow_available_sources(
    tmp_path, url_configured, skip_sharepoint, with_sharepoint
):
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
    assert result.returncode == 0, result.stderr or result.stdout
    requests = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    kb = next(item["body"] for item in requests if "/knowledgebases/demo-test?" in item["url"])
    instructions = kb["retrievalInstructions"]

    assert isinstance(instructions, str)
    assert all(name in instructions for name in ("manuals-test", "parts-test", "web-test"))
    assert all(tool in instructions for tool in ("search_parts", "check_stock", "low_stock_report"))
    assert ("policies-test" in instructions) is with_sharepoint
    assert ("policies-test" in [source["name"] for source in kb["knowledgeSources"]]) is with_sharepoint
    assert kb["retrievalReasoningEffort"] == {"kind": "low"}
    assert kb["outputMode"] == "extractiveData"

    acl_kb = next(item["body"] for item in requests if "/knowledgebases/wpc-acl-kb?" in item["url"])
    assert "retrievalInstructions" not in acl_kb
    assert acl_kb["retrievalReasoningEffort"] == {"kind": "minimal"}
