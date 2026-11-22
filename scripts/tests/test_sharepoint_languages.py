import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote

import pytest


ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")


@pytest.fixture
def policy_files(tmp_path):
    files = {}
    for language, data in (("EN", b"english"), ("IT", b"italiano")):
        folder = tmp_path / language
        folder.mkdir()
        path = folder / "test file.pdf"
        path.write_bytes(data)
        files[language] = [path]
    return files


@pytest.mark.parametrize("existing", [False, True])
def test_notebook_uploads_to_separate_language_folders(policy_files, existing):
    notebook = json.loads((ROOT / "notebooks" / "deploy" / "03-foundry-iq-agenti.ipynb").read_text(encoding="utf-8"))
    cell = next("".join(c["source"]) for c in notebook["cells"]
                if c["cell_type"] == "code" and "policy_files = {}" in "".join(c["source"]))
    source = cell.split('    drive_path = ', 1)[1].split('elif not SHAREPOINT_POLICIES_URL:', 1)[0]
    source = "    drive_path = " + source
    source = "\n".join(line[4:] if line.startswith("    ") else line for line in source.splitlines())
    calls = []

    def graph(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET" and "/root:/policy%20demo?" in path:
            return {"id": "parent", "folder": {}, "webUrl": "https://example/policy%20demo"} if existing else None
        if method == "GET" and "/items/parent:/" in path:
            language = path.split(":/", 1)[1].split("?", 1)[0]
            return {"id": language, "folder": {}} if existing else None
        if method == "POST":
            name = kwargs["json"]["name"]
            return {"id": "parent" if name == "policy demo" else name, "folder": {},
                    "webUrl": "https://example/policy%20demo"}
        if method == "GET" and "/children?" in path:
            return {"value": [{"name": "test file.pdf", "size": len(b"english")}]
                    if existing and "/items/EN/" in path else []}
        if method == "PUT":
            return {}
        raise AssertionError((method, path))

    namespace = {"drive": {"id": "drive"}, "SHAREPOINT_FOLDER": "policy demo", "policy_files": policy_files,
                 "graph": graph, "quote": quote, "unquote": unquote, "site": {"webUrl": "https://example"}}
    exec(compile(source, "sharepoint-notebook-cell", "exec"), namespace)
    uploads = [(path, kwargs["data"]) for method, path, kwargs in calls if method == "PUT"]
    assert uploads == ([("v1.0/drives/drive/items/IT:/test%20file.pdf:/content", b"italiano")] if existing else [
        ("v1.0/drives/drive/items/EN:/test%20file.pdf:/content", b"english"),
        ("v1.0/drives/drive/items/IT:/test%20file.pdf:/content", b"italiano"),
    ])
    assert [kwargs["json"]["name"] for method, _, kwargs in calls if method == "POST"] == (
        [] if existing else ["policy demo", "EN", "IT"]
    )
    assert namespace["SHAREPOINT_POLICIES_URL"] == "https://example/policy demo"


@pytest.mark.skipif(PWSH is None, reason="PowerShell is required")
@pytest.mark.parametrize("existing", [False, True])
def test_powershell_uploads_to_separate_language_folders(policy_files, tmp_path, existing):
    source = (ROOT / "scripts" / "setup_sharepoint.ps1").read_text(encoding="utf-8")
    upload_block = source.split("$folder = Get-Setting 'SHAREPOINT_FOLDER' 'policies'", 1)[1].split(
        "Set-AzdValue 'SHAREPOINT_SITE_URL'", 1
    )[0]
    capture = tmp_path / "capture.json"
    env = os.environ.copy()
    env.update(TEST_CAPTURE=str(capture), TEST_ROOT=str(tmp_path), TEST_EXISTING=str(existing).lower())
    runner = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:events = [System.Collections.Generic.List[object]]::new()
$script:testExisting = $env:TEST_EXISTING -eq 'true'
$filesByLanguage = @{}
foreach ($language in @('EN', 'IT')) {
    $filesByLanguage[$language] = @(Get-ChildItem -LiteralPath (Join-Path $env:TEST_ROOT $language) -File)
}
$site = @{id='site'; webUrl='https://example'}
function Get-Setting($name, $default) { return 'policy demo' }
function Get-GraphOrNull($uri) {
    if (-not $script:testExisting) { return $null }
    if ($uri -match '/root:/policy%20demo\?') {
        return @{id='parent'; folder=@{}; webUrl='https://example/policy%20demo'}
    }
    if ($uri -match '/items/parent:/(EN|IT)\?') {
        return @{id=$Matches[1]; folder=@{}}
    }
    throw "Unexpected lookup: $uri"
}
function Invoke-Graph($method, $uri, $body = $null) {
    if ($method -eq 'POST') {
        $script:events.Add(@{method=$method; uri=$uri; name=$body.name})
        $id = if ($body.name -eq 'policy demo') { 'parent' } else { $body.name }
        return @{id=$id; folder=@{}; webUrl='https://example/policy%20demo'}
    }
    if ($uri -match '/sites/site/drive\?') { return @{id='drive'; name='test'} }
    if ($uri -match '/items/(EN|IT)/children\?') {
        if ($script:testExisting -and $Matches[1] -eq 'EN') { return @{value=@(@{name='test file.pdf'; size=7})} }
        return @{value=@()}
    }
    throw "Unexpected request: $method $uri"
}
function Send-GraphFile($uri, $path) {
    $script:events.Add(@{method='PUT'; uri=$uri; data=[IO.File]::ReadAllText($path)})
}
$folder = Get-Setting 'SHAREPOINT_FOLDER' 'policies'
"""
    runner += upload_block
    runner += "\n$script:events | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $env:TEST_CAPTURE -Encoding utf8NoBOM\n"
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", runner],
                            capture_output=True, text=True, encoding="utf-8", env=env, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    events = json.loads(capture.read_text(encoding="utf-8"))
    if isinstance(events, dict):
        events = [events]
    uploads = [(item["uri"], item["data"]) for item in events if item["method"] == "PUT"]
    assert uploads == ([("v1.0/drives/drive/items/IT:/test%20file.pdf:/content", "italiano")] if existing else [
        ("v1.0/drives/drive/items/EN:/test%20file.pdf:/content", "english"),
        ("v1.0/drives/drive/items/IT:/test%20file.pdf:/content", "italiano"),
    ])
    assert [item["name"] for item in events if item["method"] == "POST"] == (
        [] if existing else ["policy demo", "EN", "IT"]
    )


@pytest.mark.skipif(PWSH is None, reason="PowerShell is required")
def test_powershell_rejects_missing_language_folder_before_authentication(tmp_path):
    (tmp_path / "EN").mkdir()
    (tmp_path / "EN" / "test.pdf").write_bytes(b"pdf")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts" / "setup_sharepoint.ps1"),
         "-SourceFolder", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode != 0
    assert "Cartella sorgente non trovata" in result.stderr
