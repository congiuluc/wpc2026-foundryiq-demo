import base64
import importlib
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def fake_jwt(**claims):
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture
def chat_app(monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "test-app")
    monkeypatch.setenv("WEBSITE_AUTH_ENABLED", "True")
    monkeypatch.setenv("FOUNDRYIQ_DEMO_SEARCH_ENDPOINT", "https://search.example")
    monkeypatch.setenv("FOUNDRYIQ_DEMO_OPENAI_ENDPOINT", "https://openai.example")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    monkeypatch.setattr(module.tokens, "get", lambda credential, scope: "app-token")
    calls = []
    module.calls = calls
    module.synthesize_inputs = []

    def fake_synthesize(messages, extracted):
        module.synthesize_inputs.append(extracted)
        return module.NO_CONTENT_ANSWER if extracted.strip() in ("", "[]") else "risposta"

    monkeypatch.setattr(module, "synthesize", fake_synthesize)
    yield module
    sys.path.pop(0)


def signed_in_client(module):
    return TestClient(module.app, headers={"x-ms-client-principal-id": "user-1"})


def test_config_exposes_expected_acl_groups_for_acl_examples(chat_app):
    result = signed_in_client(chat_app).get("/api/config")

    assert result.status_code == 200
    acl_examples = next(kb for kb in result.json()["knowledge_bases"] if kb["id"] == "acl")["examples"]
    assert [example["expected_acl"] for example in acl_examples] == [
        ["WPC FoundryIQ ACL - Tecnici", "WPC FoundryIQ ACL - Direzione"],
        ["WPC FoundryIQ ACL - Tecnici"],
        ["WPC FoundryIQ ACL - Direzione"],
    ]


def user_headers(expires_in=3600, aud="https://search.azure.com"):
    token = fake_jwt(aud=aud, exp=int(time.time()) + expires_in, upn="tecnico@contoso.com")
    return {"x-ms-token-aad-access-token": token, "x-ms-client-principal-name": "tecnico@contoso.com"}


def kb_response(refs):
    return {
        "response": [{"role": "assistant", "content": [{"type": "text", "text": json.dumps(refs)}]}],
        "references": [
            {"type": "azureBlob", "id": "0", "activitySource": 0, "blobUrl": "https://acct.blob.core.windows.net/acl-docs/pubblico/procedura.md"},
            {"type": "web", "id": "1", "url": "https://learn.microsoft.com/page", "title": "Learn"},
        ][: len(refs)],
        "activity": [{"id": 0, "type": "azureBlob", "knowledgeSourceName": "acl-docs-ks", "count": len(refs), "azureBlobArguments": {"search": "q"}}],
    }


def test_acl_mode_forwards_user_token_and_intents(chat_app, monkeypatch):
    def fake_post(url, headers, json, timeout):
        chat_app.calls.append((url, headers, json))
        return FakeResponse(200, kb_response([{"ref_id": 0}, {"ref_id": 1}]))

    monkeypatch.setattr(chat_app.requests, "post", fake_post)
    client = signed_in_client(chat_app)
    signed_in = user_headers()
    result = client.post(
        "/api/chat",
        json={"knowledge_base": "acl", "messages": [{"role": "user", "content": "codice service?"}], "use_acl": True},
        headers=signed_in,
    )

    assert result.status_code == 200
    url, headers, body = chat_app.calls[0]
    assert "/knowledgebases/wpc-acl-kb/retrieve" in url
    assert headers["Authorization"] == "Bearer app-token"
    assert headers["x-ms-query-source-authorization"] == signed_in["x-ms-token-aad-access-token"]
    assert body["intents"] == [{"type": "semantic", "search": "codice service?"}]
    data = result.json()
    assert data["acl_applied"] is True
    # Storage URLs are not browsable, web URLs are.
    assert data["references"][0]["url"] is None
    assert data["references"][0]["title"] == "procedura.md"
    assert data["references"][0]["knowledge_source"] == "acl-docs-ks"
    assert data["references"][1]["url"] == "https://learn.microsoft.com/page"
    assert data["references"][1]["knowledge_source"] is None
    assert data["activity"][0]["query"] == "q"
    assert data["sources"] == [{"name": "acl-docs-ks", "type": "azureBlob", "queries": 1, "results": 2, "error": None}]


def test_no_acl_mode_omits_user_header(chat_app, monkeypatch):
    monkeypatch.setattr(
        chat_app.requests, "post",
        lambda url, headers, json, timeout: chat_app.calls.append(headers) or FakeResponse(200, kb_response([])),
    )
    client = signed_in_client(chat_app)
    result = client.post(
        "/api/chat",
        json={"knowledge_base": "acl", "messages": [{"role": "user", "content": "sconto?"}], "use_acl": False},
    )

    assert result.status_code == 200
    assert "x-ms-query-source-authorization" not in chat_app.calls[0]
    assert result.json()["acl_applied"] is False
    assert result.json()["answer"] == chat_app.NO_CONTENT_ANSWER


def test_remote_sharepoint_kb_forwards_user_token_without_acl(chat_app, monkeypatch):
    monkeypatch.setattr(
        chat_app.requests, "post",
        lambda url, headers, json, timeout: chat_app.calls.append(headers) or FakeResponse(200, kb_response([])),
    )
    signed_in = user_headers()
    result = signed_in_client(chat_app).post(
        "/api/chat",
        json={"knowledge_base": "demo", "messages": [{"role": "user", "content": "policy?"}], "use_acl": False},
        headers=signed_in,
    )

    assert result.status_code == 200
    assert chat_app.calls[0]["x-ms-query-source-authorization"] == signed_in["x-ms-token-aad-access-token"]
    assert result.json()["acl_applied"] is False


def test_chat_correlates_references_with_source_activity(chat_app, monkeypatch):
    payload = {
        "response": [{"role": "assistant", "content": [{"type": "text", "text": "contenuto"}]}],
        "activity": [
            {"id": 0, "type": "azureBlob", "knowledgeSourceName": "manuali-ks", "count": 1},
            {"id": 1, "type": "mcpServer", "knowledgeSourceName": "ricambi-ks", "count": 1},
            {"id": 2, "type": "modelQueryPlanning"},
        ],
        "references": [
            {"id": "0", "type": "searchIndex", "activitySource": 0, "docKey": "manuale"},
            {"id": "1", "type": "mcpServer", "activitySource": "1", "docKey": "ricambio"},
            {"id": "2", "type": "web", "activitySource": 2, "docKey": "pianificazione"},
            {"id": "3", "type": "web", "activitySource": 99, "docKey": "sconosciuta"},
            {"id": "4", "type": "web", "docKey": "senza-attivita"},
        ],
    }
    monkeypatch.setattr(chat_app.requests, "post", lambda *args, **kwargs: FakeResponse(200, payload))
    result = signed_in_client(chat_app).post(
        "/api/chat",
        json={"knowledge_base": "demo", "messages": [{"role": "user", "content": "ricambi?"}], "use_acl": False},
    )

    assert result.status_code == 200
    data = result.json()
    assert [ref["knowledge_source"] for ref in data["references"]] == [
        "manuali-ks", "ricambi-ks", None, None, None,
    ]
    assert data["references"][0]["activity_source"] == 0
    assert data["references"][0]["type"] == "searchIndex"
    assert data["references"][0]["knowledge_source_type"] == "azureBlob"
    assert data["references"][2]["knowledge_source_type"] is None
    assert data["activity"][0]["id"] == 0
    assert [source["name"] for source in data["sources"]] == ["manuali-ks", "ricambi-ks"]


def test_messages_fall_back_to_intents_on_400(chat_app, monkeypatch):
    responses = [FakeResponse(400, {"error": "messages not supported"}), FakeResponse(200, kb_response([{"ref_id": 0}]))]

    def fake_post(url, headers, json, timeout):
        chat_app.calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr(chat_app.requests, "post", fake_post)
    client = signed_in_client(chat_app)
    result = client.post(
        "/api/chat",
        json={"knowledge_base": "demo", "messages": [{"role": "user", "content": "ricambi?"}], "use_acl": False},
    )

    assert result.status_code == 200
    assert "messages" in chat_app.calls[0]
    assert chat_app.calls[1]["intents"][0]["search"] == "ricambi?"


@pytest.mark.parametrize(
    ("headers", "detail"),
    [
        ({}, "Token utente per Azure AI Search non disponibile: accedi di nuovo."),
        (user_headers(aud="https://graph.microsoft.com"), "Token utente per Azure AI Search non disponibile: accedi di nuovo."),
        (user_headers(expires_in=10), "token_expired"),
    ],
)
def test_acl_mode_requires_valid_search_token(chat_app, monkeypatch, headers, detail):
    monkeypatch.setattr(chat_app.requests, "post", lambda *a, **k: pytest.fail("retrieve must not be called"))
    client = signed_in_client(chat_app)
    result = client.post(
        "/api/chat",
        json={"knowledge_base": "acl", "messages": [{"role": "user", "content": "q"}], "use_acl": True},
        headers=headers,
    )

    assert result.status_code == 401
    assert result.json()["detail"] == detail

@pytest.mark.parametrize(
    ("auth_enabled", "headers", "status"),
    [
        ("", {"x-ms-client-principal-id": "forged"}, 503),
        ("True", {}, 401),
    ],
)
def test_app_service_rejects_requests_without_login(chat_app, monkeypatch, auth_enabled, headers, status):
    monkeypatch.setenv("WEBSITE_AUTH_ENABLED", auth_enabled)
    client = TestClient(chat_app.app)

    assert client.get("/healthz").status_code == 200
    for path in ("/", "/trace", "/api/config"):
        assert client.get(path, headers=headers).status_code == status
    result = client.post(
        "/api/chat",
        json={"knowledge_base": "demo", "messages": [{"role": "user", "content": "q"}], "use_acl": False},
        headers=headers,
    )
    assert result.status_code == status


def test_trace_page_served_to_signed_in_user(chat_app):
    result = signed_in_client(chat_app).get("/trace")

    assert result.status_code == 200
    assert "trace-parser.js" in result.text


class FakeAgentsClient:
    def __init__(self):
        self.requests = []
        outer = self

        class Responses:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                return {
                    "output": [
                        {"type": "mcp_list_tools", "server_label": "ricambi"},
                        {"type": "azure_ai_search_call", "arguments": '{"query": "pompa"}', "status": "completed"},
                        {"type": "mcp_call", "server_label": "ricambi", "name": "check_stock", "arguments": "{}", "error": None},
                        {"type": "message", "content": [{"type": "output_text", "text": "Ci sono 3 pezzi.",
                            "annotations": [{"type": "url_citation", "url": "https://a.com/p", "title": "Pompa"}]}]},
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                }

        self.responses = Responses()


def test_agent_chat_invokes_agent_reference_and_summarizes_tools(chat_app, monkeypatch):
    monkeypatch.setattr(chat_app, "PROJECT_ENDPOINT", "https://project.example")
    fake = FakeAgentsClient()
    monkeypatch.setattr(chat_app, "agents_client", lambda: fake)
    response = signed_in_client(chat_app).post(
        "/api/agent-chat",
        json={"agent": "wpc-rag-classico-rapido", "messages": [{"role": "user", "content": "scorte pompa?"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert fake.requests[0]["extra_body"] == {"agent_reference": {"name": "wpc-rag-classico-rapido", "type": "agent_reference"}}
    assert body["answer"] == "Ci sono 3 pezzi."
    assert [t["type"] for t in body["tool_calls"]] == ["azure_ai_search_call", "mcp_call"]
    assert body["tool_calls"][1]["name"] == "check_stock"
    assert body["citations"] == [{"title": "Pompa", "url": "https://a.com/p", "type": "url_citation"}]
    assert body["kind"] == "classic" and body["usage"]["total_tokens"] == 15


def test_agent_chat_rejects_unknown_agent(chat_app, monkeypatch):
    monkeypatch.setattr(chat_app, "PROJECT_ENDPOINT", "https://project.example")
    response = signed_in_client(chat_app).post(
        "/api/agent-chat", json={"agent": "altro", "messages": [{"role": "user", "content": "x"}]}
    )
    assert response.status_code == 400
