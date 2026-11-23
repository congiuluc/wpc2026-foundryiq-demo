import ast
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from collections import UserDict
from collections.abc import Mapping
from contextlib import nullcontext
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "webapp-chat"))
import notebook_env  # noqa: E402
from mcp_references import notebook_reference_rows, summarize_agent_output, summarize_references  # noqa: E402

NOTEBOOKS = sorted((ROOT / "notebooks").rglob("*.ipynb"))
DEMO_NOTEBOOKS = sorted((ROOT / "notebooks" / "demo").glob("*.ipynb"))
DEPLOY_NOTEBOOKS = sorted((ROOT / "notebooks" / "deploy").glob("*.ipynb"))


def code_cells(path):
    return [cell for cell in json.loads(path.read_text(encoding="utf-8"))["cells"] if cell["cell_type"] == "code"]


def tagged_functions(path, tag, namespace):
    """Esegue solo funzioni, costanti e regex di una cella taggata, senza toccare Azure."""
    cell = next(c for c in code_cells(path) if tag in c.get("metadata", {}).get("tags", []))
    tree = ast.parse("".join(cell["source"]))
    def is_static(node):
        if isinstance(node, ast.FunctionDef):
            return True
        if not isinstance(node, ast.Assign):
            return False
        value = node.value
        if isinstance(value, ast.Call):
            return ast.unparse(value.func) == "re.compile"
        return all(isinstance(n, (ast.Constant, ast.Dict, ast.List, ast.Tuple, ast.Set, ast.expr_context))
                   for n in ast.walk(value))

    tree.body = [n for n in tree.body if is_static(n)]
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


def test_notebooks_are_clean_self_contained_python():
    names = {p.relative_to(ROOT / "notebooks").as_posix() for p in NOTEBOOKS}
    assert {"deploy/01-infrastruttura.ipynb", "deploy/02-dati-identita-app.ipynb", "deploy/03-foundry-iq-agenti.ipynb",
            "demo/demo-multi-fonte.ipynb", "demo/demo-acl.ipynb", "demo/demo-agenti.ipynb"} <= names
    for path in NOTEBOOKS:
        for cell in code_cells(path):
            source = "".join(cell["source"])
            ast.parse(source)
            assert not cell.get("outputs") and cell.get("execution_count") is None, path
            assert not re.search(r"^\s*(import|from)\s+(workflow|setup_|seed_)", source, re.M), path
            if path.parent.name == "deploy":
                assert not re.search(r"""["']azd["']|\bazd (env|up|provision|deploy)\b""", source), path


def test_notebook_chat_package_contains_mcp_reference_module(tmp_path, monkeypatch):
    path = ROOT / "notebooks" / "deploy" / "02-dati-identita-app.ipynb"
    source = next("".join(c["source"]) for c in code_cells(path) if c["id"] == "02-dati-identita-app-13")
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_zip")
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "build_zip" and any(keyword.arg == "include" for keyword in node.keywords)]
    include = ast.literal_eval(next(keyword.value for keyword in calls[0].keywords if keyword.arg == "include"))
    ns = {"Path": Path, "zipfile": zipfile, "EXCLUDED_DIRS": set(), "EXCLUDED_FILES": set()}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), ns)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda: str(tmp_path))
    archive = ns["build_zip"](ROOT / "webapp-chat", include=include)
    with zipfile.ZipFile(archive) as package:
        assert {"app.py", "mcp_references.py", "requirements.txt"} <= set(package.namelist())


@pytest.mark.parametrize("connection, folder, query", [
    ("", "https://example.sharepoint.com/policies", ""),
    ("test-connection", "https://example.sharepoint.com/policies", ""),
    ("test-connection", "", "includeFolder=https://example.sharepoint.com/custom"),
    ("test-connection", "", ""),
])
def test_deploy_notebook_adds_indexed_sharepoint_with_acl(connection, folder, query, capsys):
    path = ROOT / "notebooks" / "deploy" / "03-foundry-iq-agenti.ipynb"
    cells = {c["id"]: "".join(c["source"]) for c in code_cells(path)}
    requests = {}
    ns = {
        "os": SimpleNamespace(environ={"SHAREPOINT_INDEXED_CONNECTION_STRING": connection}),
        "re": re,
        "SHAREPOINT_INDEXED_QUERY": query,
        "SHAREPOINT_POLICIES_URL": folder,
        "BLOB_KS": "blob", "MCP_KS": "mcp", "SHAREPOINT_KS": "remote",
        "INDEXED_SHAREPOINT_KS": "indexed", "WEB_KS": "web", "KB_NAME": "kb",
        "BING_ALLOWED_DOMAINS": ["example.com"],
        "mcp_key": "test-key",
        "out": {
            "FOUNDRYIQ_DEMO_OPENAI_ENDPOINT": "https://openai.example",
            "FOUNDRYIQ_DEMO_CHAT_DEPLOYMENT": "chat",
            "FOUNDRYIQ_DEMO_CHAT_MODEL": "gpt-5-mini",
            "FOUNDRYIQ_DEMO_EMBEDDING_DEPLOYMENT": "embeddings",
            "FOUNDRYIQ_DEMO_STORAGE_RESOURCE_ID": "/storage",
            "FOUNDRYIQ_DEMO_BLOB_CONTAINER": "machinespecs",
            "MCP_SERVER_URL": "https://mcp.example",
        },
        "search_put": lambda path, body, kind: requests.update({path: body}),
        "search_request": lambda *args: {},
    }
    if connection and not folder and not query:
        with pytest.raises(ValueError, match="SHAREPOINT_INDEXED_QUERY"):
            exec(compile(cells["03-foundry-iq-agenti-06"], str(path), "exec"), ns)
        assert not requests
        return
    for cell_id in ("03-foundry-iq-agenti-06", "03-foundry-iq-agenti-08"):
        exec(compile(cells[cell_id], str(path), "exec"), ns)
    kb = requests["knowledgebases/kb"]
    assert {s["name"] for s in kb["knowledgeSources"]} == (
        {"blob", "mcp", "web"} | ({"indexed"} if connection else set())
    )
    assert ("indexed" in kb["retrievalInstructions"]) is bool(connection)
    assert "remote" not in kb["retrievalInstructions"]
    assert ("knowledgesources/remote" in requests) is bool(folder)
    if connection:
        source = requests["knowledgesources/indexed"]
        assert source["kind"] == "indexedSharePoint"
        parameters = source["indexedSharePointParameters"]
        assert parameters["connectionString"] == connection
        assert parameters["containerName"] == "useQuery"
        assert parameters["query"] == (query or f"includeFolder={folder}")
        assert parameters["ingestionParameters"]["ingestionPermissionOptions"] == ["userIds", "groupIds"]
        assert parameters["ingestionParameters"]["embeddingModel"]["azureOpenAIParameters"]["deploymentId"] == "embeddings"
        assert connection not in capsys.readouterr().out
    else:
        assert "knowledgesources/indexed" not in requests


@pytest.mark.parametrize("granted, stale_fic", [([], None), (["01d4889c-1287-42c6-ac1f-5d1e02578ef6"], "old-search-mi")])
def test_deploy_notebook_creates_secretless_sharepoint_ingestion_app(granted, stale_fic, capsys):
    path = ROOT / "notebooks" / "deploy" / "03-foundry-iq-agenti.ipynb"
    cell = next("".join(c["source"]) for c in code_cells(path) if c["id"] == "03-foundry-iq-agenti-04-indexed")
    calls = []

    def az(*args, output=True, check=True):
        calls.append(args)
        key = " ".join(args[:3])
        if key == "ad sp show" and args[4] == "search-mi":
            return "search-client"
        if key == "ad sp show" and args[4] == "00000003-0000-0000-c000-000000000000":
            return "graph-sp"
        if key == "ad sp show":
            return "ingestion-sp"
        if key == "ad app list":
            return None
        if key == "ad app create":
            return "ingestion-app"
        if args[:3] == ("rest", "--method", "get"):
            return granted
        if args[:3] == ("ad", "app", "federated-credential") and args[3] == "list":
            return stale_fic
        return "" if not output else None

    environ = {}
    ns = {"os": SimpleNamespace(environ=environ), "json": json, "time": SimpleNamespace(sleep=lambda _: None), "az": az,
          "RUN_SHAREPOINT_INDEXED": True, "SHAREPOINT_SITE_URL": "", "ENVIRONMENT_NAME": "demo", "TENANT_ID": "tenant",
          "RESOURCE_GROUP": "rg", "out": {"SEARCH_PRINCIPAL_ID": "search-mi", "SEARCH_SERVICE_NAME": "search"},
          "SHAREPOINT_POLICIES_URL": "https://contoso.sharepoint.com/sites/demo/Shared Documents/policies"}
    exec(compile(cell, str(path), "exec"), ns)

    assert environ["SHAREPOINT_INDEXED_CONNECTION_STRING"] == (
        "SharePointOnlineEndpoint=https://contoso.sharepoint.com/sites/demo;ApplicationId=ingestion-app;"
        "FederatedCredentialApplicationId=search-client;TenantId=tenant")
    grants = [json.loads(a[a.index("--body") + 1])["appRoleId"] for a in calls if a[:3] == ("rest", "--method", "post")]
    assert grants == [r for r in ("01d4889c-1287-42c6-ac1f-5d1e02578ef6", "a82116e5-55eb-4c41-a434-62fe8a61c773")
                      if r not in granted]
    fic_ops = [a[3] for a in calls if a[:3] == ("ad", "app", "federated-credential")]
    assert fic_ops == (["list", "delete", "create"] if stale_fic else ["list", "create"])
    fic = json.loads(next(a for a in calls if a[3:4] == ("create",) and a[2] == "federated-credential")[-1])
    assert fic["subject"] == "search-mi" and fic["audiences"] == ["api://AzureADTokenExchange"]
    assert "ApplicationSecret" not in environ["SHAREPOINT_INDEXED_CONNECTION_STRING"]


def demo_bootstrap(path):
    return tagged_functions(path, "demo-bootstrap", {
        "json": json,
        "shutil": SimpleNamespace(which=lambda _: "azd.exe"),
        "subprocess": SimpleNamespace(TimeoutExpired=subprocess.TimeoutExpired),
        "ROOT": ROOT,
        "Mapping": Mapping,
        "load_azd_env": notebook_env.load_azd_env,
        "load_env_file": notebook_env.load_env_file,
        "CONFIG_SOURCE": "azd",
        "AZD_ENVIRONMENT_NAME": "",
        "ENV_FILE": "",
        "SUBSCRIPTION_ID": "",
        "DEPLOYMENT_NAME": "wpc-foundryiq-demo",
    })


@pytest.mark.parametrize("path", DEMO_NOTEBOOKS, ids=lambda path: path.stem)
def test_demo_reads_azd_environment_without_retaining_secrets(path, monkeypatch, capsys):
    ns = demo_bootstrap(path)
    calls = []
    environment = {}
    monkeypatch.setattr(notebook_env.os, "environ", environment)
    monkeypatch.setattr(notebook_env.shutil, "which", lambda _: "azd.exe")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "AZURE_ENV_NAME": "demo", "AZURE_SUBSCRIPTION_ID": "sub",
            "FOUNDRYIQ_DEMO_KB_NAME": "my-kb", "MCP_API_KEY": "secret",
            "SHAREPOINT_INDEXED_CONNECTION_STRING": "secret", "PATH": "untrusted",
        }))

    monkeypatch.setattr(notebook_env.subprocess, "run", run)
    values = ns["load_azd_values"]("")
    assert values == {"AZURE_ENV_NAME": "demo", "AZURE_SUBSCRIPTION_ID": "sub", "FOUNDRYIQ_DEMO_KB_NAME": "my-kb"}
    assert calls[0][0] == ["azd.exe", "env", "get-values", "--output", "json", "--no-prompt"]
    assert calls[0][1]["cwd"] == ROOT
    assert environment == values
    assert not capsys.readouterr().out
    ns["load_azd_values"]("other-env")
    assert calls[1][0][-2:] == ["--environment", "other-env"]


@pytest.mark.parametrize("path", DEMO_NOTEBOOKS, ids=lambda path: path.stem)
@pytest.mark.parametrize("failure, message", [
    ("missing-cli", "non trovata"),
    ("bad-json", "JSON valido"),
    ("not-object", "oggetto di configurazione"),
    ("command-error", "Lettura dell'ambiente azd fallita"),
    ("timeout", "Impossibile leggere l'ambiente azd"),
])
def test_demo_reports_azd_read_errors(path, failure, message, monkeypatch):
    ns = demo_bootstrap(path)
    monkeypatch.setattr(notebook_env.shutil, "which", lambda _: None if failure == "missing-cli" else "azd.exe")

    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("azd", 60)
        return SimpleNamespace(
            returncode=1 if failure == "command-error" else 0,
            stderr="environment unavailable",
            stdout={"bad-json": "{", "not-object": "[]"}.get(failure, "{}"),
        )

    monkeypatch.setattr(notebook_env.subprocess, "run", run)
    with pytest.raises(RuntimeError, match=message):
        ns["load_azd_values"]("")


@pytest.mark.parametrize("override", [False, True])
def test_notebook_env_preserves_or_overrides_existing_variables(override, monkeypatch):
    environment = {"AZURE_ENV_NAME": "shell-env", "UNRELATED": "keep"}
    monkeypatch.setattr(notebook_env.os, "environ", environment)
    monkeypatch.setattr(notebook_env, "read_azd_values", lambda *args, **kwargs: {
        "AZURE_ENV_NAME": "selected-env", "AZURE_LOCATION": "swedencentral",
    })
    loaded = notebook_env.load_azd_env(override=override)
    assert loaded == {"AZURE_ENV_NAME": "selected-env" if override else "shell-env",
                      "AZURE_LOCATION": "swedencentral"}
    assert environment == {**loaded, "UNRELATED": "keep"}


@pytest.mark.parametrize("stdout, returncode, message", [
    ('{"AZURE_ENV_NAME": "demo", "AZURE_LOCATION": null}', 0, "AZURE_LOCATION"),
    ('{"MCP_API_KEY": "do-not-display"}', 1, "Lettura"),
])
def test_notebook_env_errors_do_not_leak_settings_or_partially_load(stdout, returncode, message, monkeypatch):
    environment = {"AZURE_ENV_NAME": "unchanged"}
    monkeypatch.setattr(notebook_env.os, "environ", environment)
    monkeypatch.setattr(notebook_env.shutil, "which", lambda _: "azd.exe")
    monkeypatch.setattr(notebook_env.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout=stdout, stderr="do-not-display", returncode=returncode,
    ))
    with pytest.raises(RuntimeError, match=message) as exc:
        notebook_env.load_azd_env(override=True)
    assert "do-not-display" not in str(exc.value)
    assert environment == {"AZURE_ENV_NAME": "unchanged"}


@pytest.mark.parametrize("path", DEPLOY_NOTEBOOKS, ids=lambda path: path.stem)
@pytest.mark.parametrize("enabled", [False, True])
def test_deploy_configuration_optionally_loads_azd_from_notebook_directory(path, enabled, monkeypatch):
    monkeypatch.chdir(path.parent)
    environment = {"AZURE_ENV_NAME": "stale", "AZURE_SUBSCRIPTION_ID": "stale"}
    monkeypatch.setattr(notebook_env.os, "environ", environment)
    calls = []
    values = {
        "AZURE_ENV_NAME": "selected", "AZURE_SUBSCRIPTION_ID": "sub", "AZURE_TENANT_ID": "tenant",
        "AZURE_LOCATION": "italynorth", "AZURE_RESOURCE_GROUP": "rg-selected",
        "AZURE_DEPLOYMENT_NAME": "custom-deployment", "FOUNDRYIQ_DEMO_KB_NAME": "selected-kb",
        "FOUNDRYIQ_ACL_KB_NAME": "selected-acl", "SHAREPOINT_POLICIES_URL": "https://example.test/policies",
    }

    def read(name="", *, root=None):
        calls.append((name, root))
        return values

    monkeypatch.setattr(notebook_env, "read_azd_values", read)
    source = "".join(code_cells(path)[0]["source"])
    if enabled:
        source = source.replace("LOAD_AZD_ENV = False", "LOAD_AZD_ENV = True")
        source = source.replace('AZD_ENVIRONMENT_NAME = ""', 'AZD_ENVIRONMENT_NAME = "selected"')
    ns = {}
    exec(compile(source, str(path), "exec"), ns)
    assert ns["ROOT"] == ROOT
    assert ns["ENVIRONMENT_NAME"] == ("selected" if enabled else "wpc-demo")
    assert ns["SUBSCRIPTION_ID"] == ("sub" if enabled else "")
    assert ns["DEPLOYMENT_NAME"] == ("custom-deployment" if enabled else "wpc-foundryiq-wpc-demo")
    assert calls == ([("selected", ROOT)] if enabled else [])
    if enabled:
        assert environment["AZURE_ENV_NAME"] == "selected"
    if path.stem.startswith("01"):
        assert ns["LOCATION"] == ("italynorth" if enabled else "swedencentral")
        assert ns["RESOURCE_GROUP_NAME"] == ("rg-selected" if enabled else "")
        assert ns["RUN_DEPLOY"] is False
    if path.stem.startswith("03"):
        assert ns["KB_NAME"] == ("selected-kb" if enabled else "wpc-foundryiq-kb")
        assert ns["ACL_KB_NAME"] == ("selected-acl" if enabled else "wpc-acl-kb")
        assert ns["SHAREPOINT_POLICIES_URL"] == (values["SHAREPOINT_POLICIES_URL"] if enabled else "")


@pytest.mark.parametrize("path", DEPLOY_NOTEBOOKS, ids=lambda path: path.stem)
def test_deploy_configuration_requires_azd_subscription(path, monkeypatch):
    monkeypatch.setattr(notebook_env, "load_azd_env", lambda *args, **kwargs: {"AZURE_ENV_NAME": "demo"})
    source = "".join(code_cells(path)[0]["source"]).replace("LOAD_AZD_ENV = False", "LOAD_AZD_ENV = True")
    with pytest.raises(RuntimeError, match="AZURE_SUBSCRIPTION_ID"):
        exec(compile(source, str(path), "exec"), {})


@pytest.mark.parametrize("path", DEPLOY_NOTEBOOKS, ids=lambda path: path.stem)
def test_deploy_bootstrap_rejects_azd_tenant_mismatch(path, monkeypatch):
    monkeypatch.setattr(notebook_env.shutil, "which", lambda _: "az.exe")
    monkeypatch.setattr(notebook_env.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps({"id": "sub", "tenantId": "other"}), stderr="",
    ))
    source = "".join(code_cells(path)[1]["source"]).split("SUBSCRIPTION_ID, TENANT_ID = account")[0]
    with pytest.raises(RuntimeError, match="tenant dell'ambiente azd"):
        exec(compile(source, str(path), "exec"), {
            "SUBSCRIPTION_ID": "sub", "azd_values": {"AZURE_TENANT_ID": "tenant"},
        })


@pytest.mark.parametrize("path", DEMO_NOTEBOOKS, ids=lambda path: path.stem)
def test_demo_loads_azd_or_explicit_arm_outputs(path):
    ns = demo_bootstrap(path)
    values = {"AZURE_ENV_NAME": "demo", "AZURE_SUBSCRIPTION_ID": "SUB", "AZURE_TENANT_ID": "TENANT",
              "AZURE_RESOURCE_GROUP": "rg", "FOUNDRYIQ_DEMO_PROJECT_ENDPOINT": "https://project"}
    calls = []

    def az(*args):
        calls.append(args)
        if args[:2] == ("account", "show"):
            return {"id": "sub", "tenantId": "tenant"}
        if args[:3] == ("deployment", "sub", "show"):
            outputs = {key: {"value": value} for key, value in values.items()}
            return {"properties": {"provisioningState": "Succeeded", "outputs": outputs}}
        raise AssertionError(args)

    ns["az"] = az
    ns["load_azd_values"] = lambda _: values
    out, account, source = ns["load_configuration"](["FOUNDRYIQ_DEMO_PROJECT_ENDPOINT"])
    assert out == values and account["id"] == "sub" and source == "ambiente azd demo"
    assert calls == [("account", "show", "--subscription", "SUB")]

    calls.clear()
    ns["CONFIG_SOURCE"] = "arm"
    ns["SUBSCRIPTION_ID"] = "sub"
    ns["load_azd_values"] = lambda _: pytest.fail("ARM mode must not read azd")
    out, _, source = ns["load_configuration"](["FOUNDRYIQ_DEMO_PROJECT_ENDPOINT"])
    assert out["AZURE_RESOURCE_GROUP"] == "rg" and source == "deployment ARM wpc-foundryiq-demo"
    assert calls == [("account", "show", "--subscription", "sub"),
                     ("deployment", "sub", "show", "--name", "wpc-foundryiq-demo", "--subscription", "sub")]


@pytest.mark.parametrize("path", DEMO_NOTEBOOKS, ids=lambda path: path.stem)
def test_demo_loads_safe_settings_from_env_file(path, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# commento\nAZURE_ENV_NAME=demo\nAZURE_SUBSCRIPTION_ID=sub\nAZURE_TENANT_ID=tenant\n"
        "AZURE_RESOURCE_GROUP=rg\nFOUNDRYIQ_DEMO_PROJECT_ENDPOINT=\"https://project\"\n"
        "MCP_API_KEY=secret\n", encoding="utf-8")
    environment = {}
    monkeypatch.setattr(notebook_env.os, "environ", environment)
    ns = demo_bootstrap(path)
    ns.update(CONFIG_SOURCE="env", ENV_FILE=str(env_file), az=lambda *args: {"id": "sub", "tenantId": "tenant"})
    ns["load_azd_values"] = lambda _: pytest.fail("env mode must not read azd")
    out, _, source = ns["load_configuration"](["FOUNDRYIQ_DEMO_PROJECT_ENDPOINT"])
    assert out["FOUNDRYIQ_DEMO_PROJECT_ENDPOINT"] == "https://project" and "MCP_API_KEY" not in out
    assert environment == out and source == f"file {env_file}"


def test_notebook_env_file_errors_do_not_leak_values(tmp_path):
    with pytest.raises(RuntimeError, match="New-NotebookEnv.ps1"):
        notebook_env.read_env_file(tmp_path / "missing.env")
    bad = tmp_path / ".env"
    bad.write_text("AZURE_ENV_NAME=demo\nnot-a-setting do-not-display\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Riga 2") as exc:
        notebook_env.read_env_file(bad)
    assert "do-not-display" not in str(exc.value)


@pytest.mark.parametrize("path", DEMO_NOTEBOOKS, ids=lambda path: path.stem)
@pytest.mark.parametrize("failure, message", [
    ("missing-subscription", "AZURE_SUBSCRIPTION_ID"),
    ("subscription-mismatch", "SUBSCRIPTION_ID non corrisponde"),
    ("account-mismatch", "subscription diversa"),
    ("tenant-mismatch", "tenant della configurazione"),
    ("missing-endpoint", "FOUNDRYIQ_DEMO_PROJECT_ENDPOINT"),
    ("invalid-source", "CONFIG_SOURCE"),
])
def test_demo_rejects_invalid_or_inconsistent_configuration(path, failure, message):
    ns = demo_bootstrap(path)
    values = {"AZURE_ENV_NAME": "demo", "AZURE_SUBSCRIPTION_ID": "sub", "AZURE_TENANT_ID": "tenant",
              "AZURE_RESOURCE_GROUP": "rg", "FOUNDRYIQ_DEMO_PROJECT_ENDPOINT": "https://project"}
    account = {"id": "sub", "tenantId": "tenant"}
    if failure == "missing-subscription":
        values.pop("AZURE_SUBSCRIPTION_ID")
    elif failure == "subscription-mismatch":
        ns["SUBSCRIPTION_ID"] = "other"
    elif failure == "account-mismatch":
        account["id"] = "other"
    elif failure == "tenant-mismatch":
        account["tenantId"] = "other"
    elif failure == "missing-endpoint":
        values.pop("FOUNDRYIQ_DEMO_PROJECT_ENDPOINT")
    elif failure == "invalid-source":
        ns["CONFIG_SOURCE"] = "other"
    ns["load_azd_values"] = lambda _: values
    ns["az"] = lambda *args: account
    with pytest.raises((RuntimeError, ValueError), match=message):
        ns["load_configuration"](["FOUNDRYIQ_DEMO_PROJECT_ENDPOINT"])


def test_acl_helper_grants_group_entries_and_defaults():
    ns = tagged_functions(ROOT / "notebooks/deploy/02-dati-identita-app.ipynb", "acl-helpers", {})
    acl = ns["build_acl"](["g1"], include_default=True).split(",")
    assert "group:g1:r-x" in acl and "default:group:g1:r-x" in acl and "other::---" in acl
    assert ns["ACL_FOLDERS"]["direzione-riservata"] == ["direzione"]


def test_agent_helpers_build_every_repository_agent():
    ns = tagged_functions(ROOT / "notebooks/deploy/03-foundry-iq-agenti.ipynb", "agent-helpers", {"re": re})
    values = {"FOUNDRYIQ_SEARCH_CONNECTION_ID": "search-conn", "MCP_SERVER_URL": "https://mcp/mcp",
              "FOUNDRYIQ_DEMO_KB_MCP_ENDPOINT": "https://search/kb/mcp"}
    kinds = set()
    for path in sorted((ROOT / "agents").glob("*.yaml")):
        missing = set()
        spec = ns["substitute"](yaml.safe_load(path.read_text(encoding="utf-8")), values, missing)
        assert not missing, (path.name, missing)
        definition = ns["build_definition"](spec)
        assert "${" not in json.dumps(definition)
        kinds |= {t["type"] for t in definition["tools"]}
        for tool in definition["tools"]:
            if tool["type"] == "web_search":
                assert "grainger.com" in tool["filters"]["allowed_domains"]
    assert kinds == {"azure_ai_search", "mcp", "web_search"}


def test_demo_summaries_group_sources_and_references():
    ns = tagged_functions(ROOT / "notebooks/demo/demo-multi-fonte.ipynb", "retrieve-helpers",
                          {"json": json, "summarize_references": summarize_references, "notebook_reference_rows": notebook_reference_rows})
    result = {"activity": [{"id": 1, "knowledgeSourceName": "ks-blob", "count": 2},
                           {"id": 2, "knowledgeSourceName": "ks-mcp", "count": 1, "error": {"code": "x"}}],
              "references": [{"id": "0", "activitySource": 1, "blobUrl": "https://s/c/manuale%20A.pdf"}]}
    rows = {r["fonte"]: r for r in ns["summarize_sources"](result, {"ks-blob": "azureBlob", "ks-mcp": "mcpServer"})}
    assert rows["ks-blob"]["categoria"] == "Manuali" and rows["ks-blob"]["risultati"] == 2
    assert rows["ks-mcp"]["categoria"] == "Ricambi" and '"code"' in rows["ks-mcp"]["errore"]
    reference = ns["references_by_source"](result)[0]
    assert reference["ref_id"] == "0" and reference["fonte"] == "ks-blob"
    assert reference["titolo"] == "manuale A.pdf" and reference["score"] is None


def test_multi_source_notebook_preserves_linked_mcp_arguments_and_zeroes():
    ns = tagged_functions(ROOT / "notebooks/demo/demo-multi-fonte.ipynb", "retrieve-helpers",
                          {"json": json, "summarize_references": summarize_references, "notebook_reference_rows": notebook_reference_rows})
    arguments = {"query": "pompa" * 100}
    result = {
        "activity": [{"id": 3, "type": "mcpServer", "knowledgeSourceName": "ricambi",
                      "mcpServerArguments": {"toolName": "check_stock", "toolArguments": arguments},
                      "count": 0, "elapsedMs": 0}],
        "references": [{"id": "0", "type": "mcpServer", "activitySource": 3, "title": "Ricambio"}],
    }
    [reference] = ns["references_by_source"](result)
    assert reference["tool MCP"] == "check_stock"
    assert reference["argomenti MCP"] == arguments
    assert reference["risultati MCP"] == 0 and reference["ms MCP"] == 0


def test_multi_source_detail_displays_all_mcp_argument_text():
    import pandas as pd

    path = ROOT / "notebooks" / "demo" / "demo-multi-fonte.ipynb"
    source = next("".join(c["source"]) for c in code_cells(path) if c["id"] == "demo-multi-fonte-09")
    argument = "x" * 500
    detail = {"activity": [{"id": 3, "type": "mcpServer", "knowledgeSourceName": "ricambi",
                            "mcpServerArguments": {"toolName": "check_stock", "toolArguments": {"part": argument}}}],
              "references": [{"id": "0", "type": "mcpServer", "activitySource": 3, "title": "Ricambio"}]}
    html = []
    ns = {"results": [detail], "SCENARIO_DETTAGLIO": 0, "SCENARI": [{"domanda": "Ricambio?"}],
          "pd": pd, "Markdown": str,
          "display": lambda value: html.append(value._repr_html_()) if hasattr(value, "_repr_html_") else None,
          "references_by_source": lambda value: notebook_reference_rows(summarize_references(value))}
    exec(compile(source, str(path), "exec"), ns)
    assert len(html) == 2 and all(argument in table for table in html)
    assert "argomenti MCP" in html[1] and "avviso MCP" in html[1]


def test_acl_demo_extracts_only_acl_container_paths():
    ns = tagged_functions(ROOT / "notebooks" / "demo" / "demo-acl.ipynb", "acl-bootstrap",
                          {"quote": quote, "unquote": unquote, "urlsplit": urlsplit})
    ns["CONTAINER"] = "acl"
    ns["out"] = {"FOUNDRYIQ_ACL_DFS_ENDPOINT": "https://s.dfs.core.windows.net"}
    result = {"references": [{"blobUrl": "https://s.blob.core.windows.net/acl/direzione-riservata/piano%201.md"},
                             {"url": "https://example.com/acl/x"},
                             {"sourceData": {"url": "https://s.dfs.core.windows.net/other/y"}}]}
    assert ns["source_paths"](result) == ["direzione-riservata/piano 1.md"]
    assert ns["reference_path"]({"url": "https://s.dfs.core.windows.net/acl/pubblico/doc.md"}) == "pubblico/doc.md"


def test_acl_demo_distinguishes_observed_access_from_inconclusive_results():
    ns = tagged_functions(ROOT / "notebooks" / "demo" / "demo-acl.ipynb", "acl-bootstrap",
                          {"quote": quote, "unquote": unquote, "urlsplit": urlsplit})
    ns["CONTAINER"] = "acl"
    ns["out"] = {"FOUNDRYIQ_ACL_DFS_ENDPOINT": "https://s.dfs.core.windows.net"}
    scenarios = [{"cartella": "pubblico", "gruppi": ["Tecnici"]},
                 {"cartella": "direzione-riservata", "gruppi": ["Direzione"]}]
    membership = {"Tecnici": True, "Direzione": False}
    evaluate = ns["evaluate_access"]
    visible = {"references": [{"blobUrl": "https://s.blob.core.windows.net/acl/pubblico/doc.md"}]}
    forbidden = {"references": [{"blobUrl": "https://s.blob.core.windows.net/acl/direzione-riservata/doc.md"}]}
    unresolvable = {"references": [{"id": "opaque"}]}
    assert evaluate(visible, scenarios[0], membership, scenarios)["status"] == "PASS"
    assert evaluate({}, scenarios[0], membership, scenarios)["status"] == "FAIL"
    assert evaluate({}, scenarios[1], membership, scenarios)["status"] == "WARN"
    assert evaluate(unresolvable, scenarios[0], membership, scenarios)["status"] == "WARN"
    assert evaluate(forbidden, scenarios[1], membership, scenarios)["status"] == "FAIL"


def test_acl_demo_uses_configured_group_ids_and_checks_graph_tenant():
    path = ROOT / "notebooks" / "demo" / "demo-acl.ipynb"
    source = "".join(next(c for c in code_cells(path) if c["id"] == "demo-acl-04")["source"])
    calls = []

    def az(*args):
        calls.append(args)
        if args == ("account", "show"):
            return {"tenantId": "tenant"}
        if args == ("ad", "signed-in-user", "show"):
            return {"id": "user", "displayName": "User", "userPrincipalName": "user@example.test"}
        if args[:3] == ("ad", "group", "show"):
            return {"id": args[-1]}
        if args[:4] == ("ad", "group", "member", "check"):
            return {"value": args[5] == "group-1"}
        raise AssertionError(args)

    ns = {"az": az, "TENANT_ID": "tenant", "GROUPS": {"Tecnici": "T", "Direzione": "D"}, "GROUP_IDS": {},
          "out": {"FOUNDRYIQ_ACL_GROUP_TECNICI_ID": "group-1", "FOUNDRYIQ_ACL_GROUP_DIREZIONE_ID": "group-2"},
          "pd": SimpleNamespace(DataFrame=lambda rows: rows), "display": lambda _: None,
          "record": lambda *args: None}
    exec(compile(source, str(path), "exec"), ns)
    assert ns["group_ids"] == {"Tecnici": "group-1", "Direzione": "group-2"}
    assert ns["membership"] == {"Tecnici": True, "Direzione": False}
    assert not any(args[:3] == ("ad", "group", "list") for args in calls)
    ns["GROUP_IDS"] = {"Tecnici": "override"}
    exec(compile(source, str(path), "exec"), ns)
    assert ns["group_ids"]["Tecnici"] == "override"
    calls.clear()
    ns["TENANT_ID"] = "other"
    with pytest.raises(RuntimeError, match="tenant predefinito"):
        exec(compile(source, str(path), "exec"), ns)
    assert calls == [("account", "show")]


def test_acl_demo_rejects_ambiguous_group_names():
    path = ROOT / "notebooks" / "demo" / "demo-acl.ipynb"
    source = "".join(next(c for c in code_cells(path) if c["id"] == "demo-acl-04")["source"])

    def az(*args):
        if args == ("account", "show"):
            return {"tenantId": "tenant"}
        if args == ("ad", "signed-in-user", "show"):
            return {"id": "user"}
        if args[:3] == ("ad", "group", "list"):
            return [{"id": "a", "displayName": "T"}, {"id": "b", "displayName": "T"}]
        raise AssertionError(args)

    ns = {"az": az, "TENANT_ID": "tenant", "GROUPS": {"Tecnici": "T"}, "GROUP_IDS": {}, "out": {}}
    with pytest.raises(RuntimeError, match="GROUP_IDS"):
        exec(compile(source, str(path), "exec"), ns)


def test_agent_inventory_handles_sdk_mappings_and_missing_versions():
    path = ROOT / "notebooks" / "demo" / "demo-agenti.ipynb"
    ns = demo_bootstrap(path)
    definition = UserDict({"model": "gpt-5-mini", "tools": [{"type": "mcp", "server_label": "ricambi"}]})
    valid = UserDict({"versions": UserDict({"latest": UserDict({"version": "1", "definition": definition})})})
    checks = []
    ns.update({
        "project": SimpleNamespace(agents=SimpleNamespace(get=lambda name: UserDict() if name == "missing" else valid)),
        "CONFRONTO": ["valid", "missing"], "MAGAZZINO": "warehouse",
        "record": lambda *args: checks.append(args),
        "pd": SimpleNamespace(DataFrame=lambda rows: rows), "display": lambda _: None,
    })
    source = "".join(next(c for c in code_cells(path) if c["id"] == "demo-agenti-05")["source"])
    exec(compile(source, str(path), "exec"), ns)
    assert [row["modello"] for row in ns["inventory"]] == ["gpt-5-mini", "gpt-5-mini"]
    assert [(check[0], check[1]) for check in checks] == [
        ("Agente presente · valid", "PASS"), ("Agente presente · missing", "FAIL"),
        ("Agente presente · warehouse", "PASS"),
    ]
    assert ns["as_dict"](SimpleNamespace(model_dump=lambda: {"output": []})) == {"output": []}
    with pytest.raises(TypeError, match="Tipo non supportato"):
        ns["as_dict"](object())


def test_agent_demo_summarizes_tools_citations_and_tokens():
    ns = tagged_functions(ROOT / "notebooks/demo/demo-agenti.ipynb", "agent-summary",
                          {"summarize_agent_output": summarize_agent_output})
    response = {"output": [{"type": "mcp_call", "server_label": "ricambi", "name": "check_stock", "status": "completed"},
                           {"type": "message", "content": [{"text": "Ok [1]", "annotations": [{"url": "u", "title": "Manuale"}]}]}],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
    summary = ns["summarize_response"](response)
    assert summary["chiamate"][0]["tipo"] == "MCP" and summary["chiamate"][0]["tool"] == "check_stock"
    assert summary["citazioni"] == ["Manuale"] and summary["token totali"] == 15 and summary["risposta"] == "Ok [1]"


def test_agent_notebook_exposes_real_mcp_calls_and_reference_associations():
    ns = tagged_functions(ROOT / "notebooks/demo/demo-agenti.ipynb", "agent-summary",
                          {"summarize_agent_output": summarize_agent_output})
    arguments = json.dumps({"query": "pompa" * 100})
    response = {"output": [
        {"id": "stock", "type": "mcp_call", "name": "check_stock", "server_label": "ricambi",
         "arguments": arguments, "output": '{"count":0,"results":[]}'},
        {"id": "kb", "type": "mcp_call", "name": "knowledge_base_retrieve", "server_label": "iq",
         "arguments": '{"query":"manuale"}',
         "output": json.dumps([{"ref_id": 0, "sourceData": {"title": "Manuale"}}])},
    ]}
    summary = ns["summarize_response"](response)
    assert summary["chiamate"][0]["argomenti"] == arguments
    assert summary["chiamate"][0]["risultati"] == 0
    assert summary["riferimenti"][0]["tool_call_id"] == "kb"
    assert summary["riferimenti"][0]["mcp_call"]["arguments"] == '{"query":"manuale"}'
    assert summary["citazioni"] == []


def test_agent_notebook_exposes_internal_mcp_activity_without_references():
    ns = tagged_functions(ROOT / "notebooks/demo/demo-agenti.ipynb", "agent-summary",
                          {"summarize_agent_output": summarize_agent_output})
    response = {"output": [{"id": "kb", "type": "mcp_call", "name": "knowledge_base_retrieve",
                            "output": json.dumps({"activity": [{"id": 1, "type": "mcpServer",
                                "mcpServerArguments": {"toolName": "check_stock", "toolArguments": {"part": "A"}},
                                "count": 0}], "references": []})}]}
    summary = ns["summarize_response"](response)
    assert summary["attività"][0]["mcp_tool"] == "check_stock"
    assert summary["attività"][0]["mcp_arguments"] == {"part": "A"}
    assert summary["attività"][0]["count"] == 0


@pytest.fixture
def sdk_helpers():
    try:
        installed = version("azure-search-documents")
    except PackageNotFoundError:
        pytest.skip("Install the notebook requirements to test the Search SDK.")
    if installed != "12.2.0b1":
        pytest.skip("The SDK notebook requires azure-search-documents==12.2.0b1.")
    models = pytest.importorskip("azure.search.documents.knowledgebases.models")
    path = ROOT / "notebooks" / "demo" / "demo-foundry-iq-sdk.ipynb"
    ns = tagged_functions(path, "sdk-helpers", {
        name: getattr(models, name) for name in (
            "KnowledgeBaseMessage", "KnowledgeBaseMessageTextContent",
            "KnowledgeBaseRetrievalRequest", "KnowledgeRetrievalSemanticIntent",
        )
    })
    return ns, models


@pytest.mark.parametrize("mode", ["messages", "intents"])
def test_sdk_notebook_builds_retrieval_requests(sdk_helpers, mode):
    ns, _ = sdk_helpers
    request = ns["build_request"]("Qual è il codice?", mode)
    body = dict(request)
    assert body["includeActivity"] is True and body["maxRuntimeInSeconds"] == 120
    if mode == "messages":
        assert body["messages"][0]["content"][0]["text"] == "Qual è il codice?"
        assert "intents" not in body
    else:
        assert body["intents"][0]["search"] == "Qual è il codice?"
        assert "messages" not in body
    with pytest.raises(ValueError):
        ns["build_request"]("  ", mode)
    with pytest.raises(ValueError):
        ns["build_request"]("question", "unknown")


def test_sdk_notebook_forwards_only_an_explicit_user_token(sdk_helpers):
    ns, _ = sdk_helpers
    calls = []

    class Credential:
        def __init__(self, **kwargs):
            calls.append(("credential", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append(("close credential",))

        def get_token(self, scope):
            calls.append(("token", scope))
            return SimpleNamespace(token="delegated-token")

    class Client:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append(("close client",))

        def retrieve(self, **kwargs):
            calls.append(("retrieve", kwargs))
            return "response"

    ns.update(AzureCliCredential=Credential, KnowledgeBaseRetrievalClient=Client)
    query = ns["retrieve_question"]
    assert query("question", "messages", True, "https://search", "kb", "sub",
                 {"user": {"type": "user"}}) == "response"
    client_options = next(c[1] for c in calls if c[0] == "client")
    sent = next(c[1] for c in calls if c[0] == "retrieve")
    assert client_options["api_version"] == "2026-11-01-preview" and client_options["knowledge_base_name"] == "kb"
    assert sent["query_source_authorization"] == "delegated-token" and "messages" in sent["retrieval_request"]
    assert ("close client",) in calls and ("close credential",) in calls

    calls.clear()
    query("question", "intents", False, "https://search", "kb", "sub", {"user": {"type": "servicePrincipal"}})
    assert next(c[1] for c in calls if c[0] == "retrieve")["query_source_authorization"] is None
    assert not any(c[0] == "token" for c in calls)
    calls.clear()
    with pytest.raises(RuntimeError, match="service principal"):
        query("question", "intents", True, "https://search", "kb", "sub", {"user": {"type": "servicePrincipal"}})
    assert calls == []


def test_sdk_notebook_displays_references_and_source_errors(sdk_helpers, capsys):
    _, models = sdk_helpers
    result = models.KnowledgeBaseRetrievalResponse({
        "response": [{"role": "assistant", "content": [{"type": "text", "text": "Testo [0]"}]}],
        "activity": [{"id": 1, "type": "azureBlob", "knowledgeSourceName": "acl-docs-ks",
                      "count": 1, "elapsedMs": 10, "error": {"code": "source-error", "message": "failed"},
                      "warning": "partial"}],
        "references": [{"id": "0", "type": "azureBlob", "activitySource": 1,
                        "blobUrl": "https://storage/acl/doc.md", "rerankerScore": 3.0}],
    })
    path = ROOT / "notebooks" / "demo" / "demo-foundry-iq-sdk.ipynb"
    cells = {c["id"]: "".join(c["source"]) for c in code_cells(path)}
    tables = []
    ns = {"result": result, "summarize_references": summarize_references, "notebook_reference_rows": notebook_reference_rows,
          "pd": SimpleNamespace(DataFrame=lambda rows: rows, option_context=lambda *args: nullcontext()), "display": tables.append}
    for cell in ("sdk-09", "sdk-10"):
        exec(compile(cells[cell], str(path), "exec"), ns)
    assert "Testo [0]" in capsys.readouterr().out
    assert tables[0][0]["fonte"] == "acl-docs-ks" and tables[0][0]["documento"].endswith("doc.md")
    assert tables[1][0]["errore"] and tables[1][0]["avviso"] == "partial"


def test_sdk_notebook_displays_mcp_reference_and_unlinked_activity(sdk_helpers):
    _, models = sdk_helpers
    arguments = {"query": "pompa" * 100}
    result = models.KnowledgeBaseRetrievalResponse({
        "activity": [
            {"id": 3, "type": "mcpServer", "knowledgeSourceName": "ricambi", "count": 0, "elapsedMs": 0,
             "mcpServerArguments": {"toolName": "check_stock", "toolArguments": arguments}},
            {"id": 4, "type": "mcpServer", "knowledgeSourceName": "ricambi", "count": 0,
             "mcpServerArguments": {"toolName": "other_tool", "toolArguments": {}}},
        ],
        "references": [{"id": "0", "type": "mcpServer", "activitySource": 3, "title": "Ricambio"}],
    })
    path = ROOT / "notebooks" / "demo" / "demo-foundry-iq-sdk.ipynb"
    source = next("".join(c["source"]) for c in code_cells(path) if c["id"] == "sdk-10")
    tables = []
    ns = {"result": result, "summarize_references": summarize_references, "notebook_reference_rows": notebook_reference_rows,
          "pd": SimpleNamespace(DataFrame=lambda rows: rows, option_context=lambda *args: nullcontext()), "display": tables.append}
    exec(compile(source, str(path), "exec"), ns)
    assert tables[0][0]["tool MCP"] == "check_stock" and tables[0][0]["argomenti MCP"] == arguments
    assert tables[0][0]["risultati MCP"] == 0 and tables[0][0]["ms MCP"] == 0
    assert tables[1][1]["tool MCP"] == "other_tool" and tables[1][1]["argomenti MCP"] == {}
