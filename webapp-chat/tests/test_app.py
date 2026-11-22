import base64
import importlib
import json
import re
import sys
import time
from collections import UserDict
from pathlib import Path
from types import SimpleNamespace

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


def test_demo_config_exposes_six_examples_without_duplicating_test_queries(chat_app):
    result = signed_in_client(chat_app).get("/api/config")

    assert result.status_code == 200
    demo = next(kb for kb in result.json()["knowledge_bases"] if kb["id"] == "demo")
    example_questions = [example["question"] for example in demo["examples"]]
    test_questions = [example["question"] for example in demo["tests"]]
    assert len(example_questions) == 6
    assert len(test_questions) == 7
    assert test_questions[:6] == example_questions
    assert len(set(test_questions)) == len(test_questions)


def test_config_exposes_expected_acl_groups_for_acl_examples(chat_app):
    result = signed_in_client(chat_app).get("/api/config")

    assert result.status_code == 200
    acl_examples = next(kb for kb in result.json()["knowledge_bases"] if kb["id"] == "acl")["examples"]
    assert [example["expected_acl"] for example in acl_examples] == [
        ["WPC FoundryIQ ACL - Tecnici", "WPC FoundryIQ ACL - Direzione"],
        ["WPC FoundryIQ ACL - Tecnici"],
        ["WPC FoundryIQ ACL - Direzione"],
    ]


def test_config_displays_identity_and_mapped_groups_without_tokens(chat_app):
    principal = base64.b64encode(json.dumps({"claims": [
        {"typ": "name", "val": "Tecnico Demo"},
        {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": "object-1"},
        {"typ": "http://schemas.microsoft.com/identity/claims/tenantid", "val": "tenant-1"},
        {"typ": "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups", "val": "group-2"},
        {"typ": "groups", "val": "group-1"},
        {"typ": "groups", "val": "group-1"},
        {"typ": "roles", "val": "Reader"},
        {"typ": "secret-claim", "val": "do-not-expose"},
    ]}).encode()).decode()
    headers = {**user_headers(), "x-ms-client-principal": principal}
    result = signed_in_client(chat_app).get("/api/config", headers=headers)

    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    profile = result.json()["user_profile"]
    assert profile["display_name"] == "Tecnico Demo"
    assert profile["object_id"] == "object-1"
    assert profile["tenant_id"] == "tenant-1"
    assert profile["username"] == "tecnico@contoso.com"
    assert profile["groups"] == ["group-1", "group-2"]
    assert profile["groups_status"] == "available"
    assert profile["roles"] == ["Reader"]
    assert headers["x-ms-token-aad-access-token"] not in result.text
    assert principal not in result.text
    assert "do-not-expose" not in result.text


@pytest.mark.parametrize(
    ("claims", "status", "groups"),
    [
        ({}, "unavailable", []),
        ({"groups": []}, "available", []),
        ({"groups": ["group-1"]}, "available", ["group-1"]),
        ({"hasgroups": True}, "overage", []),
        ({"_claim_names": {"groups": "src1"}, "groups": ["partial"]}, "overage", ["partial"]),
        ({"_claim_names": {"other": "src1"}}, "unavailable", []),
    ],
)
def test_group_status_uses_id_token_not_search_groups(chat_app, claims, status, groups):
    headers = {
        **user_headers(),
        "x-ms-token-aad-id-token": fake_jwt(name="Utente Demo", **claims),
    }
    result = signed_in_client(chat_app).get("/api/config", headers=headers)

    assert result.status_code == 200
    assert result.json()["user_profile"]["groups_status"] == status
    assert result.json()["user_profile"]["groups"] == groups
    assert headers["x-ms-token-aad-id-token"] not in result.text


def test_invalid_principal_is_reported_in_config(chat_app):
    result = signed_in_client(chat_app).get("/api/config", headers={"x-ms-client-principal": "invalid"})

    assert result.status_code == 200
    assert result.json()["user_profile"] is None
    assert "Claim di identità Easy Auth non leggibili" in result.json()["user_source"]


def test_local_profile_ignores_client_identity_headers(chat_app, monkeypatch):
    monkeypatch.setattr(chat_app, "RUNNING_ON_APP_SERVICE", False)
    monkeypatch.setattr(chat_app.tokens, "get", lambda *args: fake_jwt(
        aud="https://search.azure.com", preferred_username="locale@contoso.com",
        oid="local-user", groups=["local-group"],
    ))
    result = TestClient(chat_app.app).get("/api/config", headers={
        "x-ms-client-principal-name": "forged", "x-ms-client-principal": "invalid",
    })

    assert result.status_code == 200
    assert result.json()["user_profile"]["username"] == "locale@contoso.com"
    assert result.json()["user_profile"]["object_id"] == "local-user"
    assert result.json()["user_profile"]["groups"] == ["local-group"]


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


class StreamResponse(FakeResponse):
    headers = {"content-type": "text/event-stream"}

    def __init__(self, events):
        super().__init__(200, {})
        self.events = events
        self.closed = False

    def iter_lines(self, **kwargs):
        for name, data in self.events:
            yield f"event: {name}"
            yield f"data: {json.dumps(data)}"
            yield ""

    def close(self):
        self.closed = True


class ModelStream:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return iter(self.events)

    def __exit__(self, *args):
        return False


def test_retrieve_sse_parser_handles_comments_multiline_and_unicode(chat_app):
    assert list(chat_app.read_sse([
        b": heartbeat", b"", b"event: activity.completed", b'data: {"id": 1,',
        'data: "query": "disponibilità"}'.encode(), b"", b": heartbeat",
    ])) == [("activity.completed", {"id": 1, "query": "disponibilità"})]


def test_stream_chat_forwards_acl_and_separates_retrieval_and_model_usage(chat_app, monkeypatch):
    payload = kb_response([{"ref_id": 0}])
    payload["activity"].append({"id": 1, "type": "modelQueryPlanning", "inputTokens": 10, "outputTokens": 3})
    response = StreamResponse([
        ("retrieval.started", {"requestId": "req-1", "outputMode": "extractiveData", "reasoningEffort": "low"}),
        ("activity.started", {"id": 0, "type": "azureBlob", "knowledgeSourceName": "acl-docs-ks"}),
        ("activity.completed", payload["activity"][0]),
        ("references.completed", payload["references"]),
        ("response.completed", {"statusCode": 206, "response": payload}),
    ])

    def fake_post(url, **kwargs):
        chat_app.calls.append(kwargs)
        return response

    monkeypatch.setattr(chat_app.requests, "post", fake_post)
    chunks = [
        SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=None)])
        for text in ("Risposta ", "progressiva")
    ] + [
        SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")]),
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=20, completion_tokens=4, total_tokens=24), choices=[]),
    ]
    monkeypatch.setattr(chat_app, "openai_client", lambda: SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kwargs: ModelStream(chunks)),
    )))
    headers = user_headers()
    result = signed_in_client(chat_app).post("/api/chat", headers=headers, json={
        "knowledge_base": "acl", "messages": [{"role": "user", "content": "ricambi?"}], "stream": True,
    })
    events = list(chat_app.read_sse(result.text.splitlines()))
    assert result.headers["content-type"].startswith("text/event-stream")
    assert [data["text"] for name, data in events if name == "delta"] == ["Risposta ", "progressiva"]
    assert events[-1][0] == "completed"
    assert events[-1][1]["answer"] == "Risposta progressiva"
    assert events[-1][1]["retrieval_usage"]["total_tokens"] == 13
    assert events[-1][1]["usage"]["total_tokens"] == 24
    assert chat_app.calls[0]["headers"]["x-ms-query-source-authorization"] == headers["x-ms-token-aad-access-token"]
    assert chat_app.calls[0]["headers"]["Accept"] == "text/event-stream"
    assert chat_app.calls[0]["stream"] is True
    assert response.closed
    names = [name for name, _ in events]
    assert names.index("references") < names.index("delta")
    references = next(data for name, data in events if name == "references")["items"]
    assert references[0]["knowledge_source"] == "acl-docs-ks"
    logs = [data for name, data in events if name == "log"]
    titles = [data["title"] for data in logs]
    assert "Retrieval avviato da Foundry IQ" in titles
    assert "Completata: acl-docs-ks (azureBlob)" in titles
    assert "Riferimenti risolti: 1" in titles
    assert next(data for data in logs if data["title"].startswith("Risultato parziale"))["level"] == "warning"
    assert next(data for data in logs if data["title"].startswith("Query eseguita"))["private"] is True
    assert titles.index("Primo token della risposta") > titles.index("Retrieval completato")
    assert titles[-1] == "Risposta completata"
    assert all(type(data["at_ms"]) is int for data in logs)


@pytest.mark.parametrize("events", [
    [("activity.started", {"id": 1, "type": "modelQueryPlanning"})],
    [("error", {"error": {"message": "errore retrieval"}})],
])
def test_stream_chat_reports_incomplete_retrieval_without_success(chat_app, monkeypatch, events):
    response = StreamResponse(events)
    monkeypatch.setattr(chat_app.requests, "post", lambda *args, **kwargs: response)
    result = signed_in_client(chat_app).post("/api/chat", json={
        "knowledge_base": "acl", "messages": [{"role": "user", "content": "ricambi?"}],
        "use_acl": False, "stream": True,
    })
    output = list(chat_app.read_sse(result.text.splitlines()))
    assert output[-1][0] == "error"
    assert not any(name == "completed" for name, _ in output)
    assert response.closed


def test_stream_agent_transmits_tool_and_text_before_final_result(chat_app, monkeypatch):
    monkeypatch.setattr(chat_app, "PROJECT_ENDPOINT", "https://project.example")
    response = {"output_text": "Risposta agente", "usage": {"total_tokens": 12}, "output": []}
    events = [
        SimpleNamespace(type="response.output_item.added", item={"id": "tool-1", "type": "mcp_call", "name": "knowledge_base_retrieve", "status": "in_progress"}),
        SimpleNamespace(type="response.output_text.delta", delta="Risposta agente"),
        SimpleNamespace(type="response.completed", response=response),
    ]
    monkeypatch.setattr(chat_app, "agents_client", lambda: SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: ModelStream(events)),
    ))
    result = signed_in_client(chat_app).post("/api/agent-chat", json={
        "agent": "wpc-technician-assistant", "messages": [{"role": "user", "content": "ricambi?"}], "stream": True,
    })
    output = list(chat_app.read_sse(result.text.splitlines()))
    assert [name for name, _ in output if name != "log"] == ["status", "tool", "delta", "completed"]
    assert output[-1][1]["answer"] == "Risposta agente"
    assert output[-1][1]["usage"]["total_tokens"] == 12
    titles = [data["title"] for name, data in output if name == "log"]
    assert titles[0].startswith("Richiesta inviata all'agente")
    assert "Chiamata tool avviata: knowledge_base_retrieve" in titles
    assert "Primo token della risposta" in titles
    assert titles[-1] == "Risposta completata"


def test_stream_agent_streams_tool_arguments_references_and_citations(chat_app, monkeypatch):
    monkeypatch.setattr(chat_app, "PROJECT_ENDPOINT", "https://project.example")
    tool = {"id": "tool-1", "type": "mcp_call", "name": "knowledge_base_retrieve", "server_label": "kb"}
    output = json.dumps([{"ref_id": 0, "sourceData": {"title": "Manuale pompa", "url": "https://contoso.example/pompa"}}])
    events = [
        SimpleNamespace(type="response.output_item.added", item={**tool, "status": "in_progress"}),
        SimpleNamespace(type="response.mcp_call_arguments.done", item_id="tool-1", arguments='{"queries":["pompa"]}'),
        SimpleNamespace(type="response.output_item.done", item={**tool, "status": "completed", "output": output}),
        SimpleNamespace(type="response.output_text.annotation.added", annotation={"type": "url_citation", "title": "Manuale pompa", "url": "https://contoso.example/pompa"}),
        SimpleNamespace(type="response.completed", response={"output_text": "ok", "output": []}),
    ]
    monkeypatch.setattr(chat_app, "agents_client", lambda: SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: ModelStream(events)),
    ))
    result = signed_in_client(chat_app).post("/api/agent-chat", json={
        "agent": "wpc-technician-assistant", "messages": [{"role": "user", "content": "pompa?"}], "stream": True,
    })
    output_events = list(chat_app.read_sse(result.text.splitlines()))
    references = next(data for name, data in output_events if name == "references")
    assert references["append"] is True
    assert references["items"][0]["title"] == "Manuale pompa"
    assert references["items"][0]["tool_call"] == 1
    assert next(data for name, data in output_events if name == "citation")["title"] == "Manuale pompa"
    done_tool = [data for name, data in output_events if name == "tool"][-1]
    assert done_tool["arguments"] == '{"queries":["pompa"]}'
    first_tool = next(data for name, data in output_events if name == "tool")
    assert done_tool["started_ms"] == first_tool["started_ms"]
    assert done_tool["duration_ms"] == done_tool["completed_ms"] - done_tool["started_ms"] >= 0
    assert "completed_ms" not in first_tool
    args_log = next(data for name, data in output_events if name == "log" and data["title"] == "Argomenti inviati al tool")
    assert args_log["private"] is True
    assert any(name == "log" and data["title"] == "Riferimenti Foundry IQ ricevuti: 1" for name, data in output_events)


@pytest.mark.parametrize(("agent", "message", "text_emitted", "retry_fails", "retries"), [
    ("wpc-rag-classico-accurato", "A user error occurred while executing a tool call.", False, False, True),
    ("wpc-rag-classico-accurato", "A user error occurred while executing a tool call.", False, True, True),
    ("wpc-rag-classico-accurato", "A user error occurred while executing a tool call.", True, False, False),
    ("wpc-rag-classico-accurato", "Rate limit", False, False, False),
    ("wpc-technician-assistant", "A user error occurred while executing a tool call.", False, False, False),
])
def test_stream_agent_retry_is_bounded_explicit_and_only_for_known_tool_error(
    chat_app, monkeypatch, agent, message, text_emitted, retry_fails, retries,
):
    monkeypatch.setattr(chat_app, "PROJECT_ENDPOINT", "https://project.example")
    calls = []

    def failed_stream():
        if text_emitted:
            yield SimpleNamespace(type="response.output_text.delta", delta="Parziale")
        raise RuntimeError(message)

    def create(**kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):
            return ModelStream(failed_stream())
        if retry_fails:
            raise RuntimeError("Errore retry")
        return {"status": "completed", "output_text": "Risposta retry", "output": [], "usage": {"total_tokens": 8}}

    monkeypatch.setattr(chat_app, "agents_client", lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    result = signed_in_client(chat_app).post("/api/agent-chat", json={
        "agent": agent, "messages": [{"role": "user", "content": "ricambi?"}], "stream": True,
    })
    events = list(chat_app.read_sse(result.text.splitlines()))
    assert len(calls) == (2 if retries else 1)
    if retries:
        warning = next(data for name, data in events if name == "status" and "result" in data)
        assert warning["result"]["streaming"]["mode"] == "json_retry"
        assert "tentativo interrotto" in warning["result"]["streaming"]["usage_scope"]
    if retries and not retry_fails:
        assert events[-1][0] == "completed"
        assert events[-1][1]["streaming"]["mode"] == "json_retry"
        assert events[-1][1]["usage"]["total_tokens"] == 8
    else:
        assert events[-1][0] == "error"
        assert not any(name == "completed" for name, _ in events)


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
    assert data["execution"]["acl_requested"] is True
    assert data["execution"]["user_token_forwarded"] is True
    assert data["execution"]["synthesis_performed"] is True
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
    assert result.json()["execution"]["user_token_forwarded"] is False
    assert result.json()["execution"]["synthesis_performed"] is False


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
    assert result.json()["execution"]["user_token_forwarded"] is True
    assert result.json()["execution"]["acl_requested"] is False
    assert result.json()["execution"]["remote_sharepoint"] is True


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
    assert 'id="root"' in result.text


def test_frontend_bundle_assets_are_served(chat_app):
    client = signed_in_client(chat_app)
    html = client.get("/").text
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)

    assert "/static/" not in html
    assert any(path.endswith(".js") for path in assets)
    assert any(path.endswith(".css") for path in assets)
    for path in assets:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.content, path
    assert client.get("/static/styles.css").status_code == 404


@pytest.mark.parametrize("path", ["/demo/multi-fonte", "/demo/acl", "/demo/agenti"])
def test_demo_page_served_to_signed_in_user(chat_app, path):
    client = signed_in_client(chat_app)
    assert 'id="root"' in client.get(path).text
    assert client.get(path).status_code == 200
    assert TestClient(chat_app.app).get(path).status_code == 401


def test_unknown_demo_page_returns_not_found(chat_app):
    assert signed_in_client(chat_app).get("/demo/unknown").status_code == 404


def test_missing_frontend_build_reports_error(chat_app, monkeypatch, tmp_path):
    monkeypatch.setattr(chat_app, "FRONTEND_DIR", tmp_path)
    client = signed_in_client(chat_app)

    for path in ("/", "/trace", "/demo/acl"):
        response = client.get(path)
        assert response.status_code == 503
        assert "npm run build" in response.json()["detail"]


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
    assert body["execution"]["user_token_forwarded"] is False
    assert body["execution"]["acl_requested"] is False


def test_agent_chat_rejects_unknown_agent(chat_app, monkeypatch):
    monkeypatch.setattr(chat_app, "PROJECT_ENDPOINT", "https://project.example")
    response = signed_in_client(chat_app).post(
        "/api/agent-chat", json={"agent": "altro", "messages": [{"role": "user", "content": "x"}]}
    )
    assert response.status_code == 400


def test_agent_annotations_keep_locations_across_text_parts_and_file_names(chat_app):
    first = "😀 Consulta il manuale"
    second = "Altre informazioni"
    response = UserDict({"output": [{"type": "message", "content": [
        {"type": "output_text", "text": first, "annotations": [
            {"type": "url_citation", "title": "Manuale", "url": "https://example.com/manual",
             "start_index": 2, "end_index": len(first)},
            {"type": "file_citation", "file_id": "file-123", "filename": "guida.pdf", "index": 2},
            {"type": "url_citation", "title": "Senza posizione", "url": "https://example.com/other",
             "start_index": -1, "end_index": 500},
        ]},
        {"type": "output_text", "text": second, "annotations": [
            {"type": "url_citation", "title": "Manuale", "url": "https://example.com/manual",
             "start_index": 0, "end_index": len(second)},
        ]},
    ]}]})

    result = chat_app.summarize_agent_output(response)

    assert result["answer"] == f"{first}\n{second}"
    assert result["citations"] == [
        {"title": "Manuale", "url": "https://example.com/manual", "type": "url_citation"},
        {"title": "guida.pdf", "url": None, "type": "file_citation"},
        {"title": "Senza posizione", "url": "https://example.com/other", "type": "url_citation"},
    ]
    assert result["citation_spans"] == [
        {"start": 3, "end": len(first) + 1, "citation_index": 0},
        {"start": 3, "end": 3, "citation_index": 1},
        {"start": len(first) + 2, "end": len(first) + len(second) + 2, "citation_index": 0},
    ]


def test_agent_file_citation_accepts_text_boundaries_but_not_invalid_indices(chat_app):
    text = "😀 Fine"
    result = chat_app.summarize_agent_output({"output": [{"type": "message", "content": [{
        "text": text,
        "annotations": [
            {"type": "file_citation", "file_id": "first", "index": 0},
            {"type": "file_citation", "file_id": "last", "index": len(text)},
            {"type": "file_citation", "file_id": "invalid", "index": len(text) + 1},
            {"type": "file_path", "file_id": "path", "index": 1},
        ],
    }]}]})

    assert result["citation_spans"] == [
        {"start": 0, "end": 0, "citation_index": 0},
        {"start": len(text) + 1, "end": len(text) + 1, "citation_index": 1},
    ]


def test_agent_extracts_only_real_mcp_knowledge_references(chat_app):
    def output(blob_url, name):
        return json.dumps({"content": [
            {"type": "text", "text": json.dumps([{"ref_id": 0, "content": "private excerpt"}])},
            {"type": "text", "text": json.dumps({
                "kind": "resource_link", "ref_id": 0, "uri": "https://example.com/other",
                "sourceData": {"blob_url": blob_url, "title": name, "snippet": "private excerpt"},
            })},
        ]})

    result = chat_app.summarize_agent_output({"output": [
        {"type": "mcp_call", "name": "knowledge_base_retrieve", "id": "call-one",
         "output": output("https://storage.blob.core.windows.net/docs/a.pdf", "Manuale A")},
        {"type": "mcp_call", "name": "knowledge_base_retrieve", "id": "call-two",
         "output": output("https://storage.blob.core.windows.net/docs/b.pdf", "Manuale B")},
        {"type": "mcp_call", "name": "other_tool", "output": output("https://example.com/c", "Da ignorare")},
        {"type": "message", "content": [{"text": "Risposta [ref_id:0]"}]},
    ]})

    assert [(ref["id"], ref["title"], ref["tool_call"]) for ref in result["references"]] == [
        (0, "Manuale A", 1), (0, "Manuale B", 2),
    ]
    assert all(ref["url"] is None and ref["path"].endswith(".pdf") for ref in result["references"])
    assert "private excerpt" not in json.dumps(result)
    assert "Da ignorare" not in json.dumps(result)


def test_agent_mcp_output_accepts_concatenated_json_and_explicit_web_urls(chat_app):
    output = "\n".join([
        json.dumps([{"ref_id": 4, "content": "content"}]),
        json.dumps({"ref_id": 4, "kind": "web", "sourceData": {"title": "Guida", "url": "https://example.com/guide"}}),
        json.dumps({"ref_id": 5, "kind": "web", "title": "FAQ", "url": "https://example.com/faq"}),
    ])
    result = chat_app.summarize_agent_output({"output": [
        {"type": "mcp_call", "name": "knowledge_base_retrieve", "output": output},
    ]})

    assert [(ref["id"], ref["title"], ref["url"]) for ref in result["references"]] == [
        (4, "Guida", "https://example.com/guide"), (5, "FAQ", "https://example.com/faq"),
    ]


def test_agent_accepts_sdk_mcp_call_with_serialized_output(chat_app):
    from openai.types.responses.response_output_item import McpCall

    reference = {"ref_id": 5, "uri": "https://example.com/manual", "sourceData": {"title": "Manuale"}}
    output = json.dumps({"content": [{"type": "text", "text": json.dumps(reference)}]})
    call = McpCall(id="call", type="mcp_call", name="knowledge_base_retrieve",
                   server_label="kb", arguments="{}", status="completed", output=output)

    result = chat_app.summarize_agent_output({"output": [call]})

    assert [(ref["id"], ref["title"]) for ref in result["references"]] == [(5, "Manuale")]


def test_agent_mcp_marker_is_taken_from_the_reference_block_not_guessed(chat_app):
    output = (
        "【6:0†source】" + json.dumps([{"ref_id": 0, "content": "excerpt"}]) + "\n"
        "【6:3†source】" + json.dumps({
            "ref_id": 0, "kind": "web", "uri": "https://example.com/manual",
            "sourceData": {"title": "Manuale"},
        })
    )
    result = chat_app.summarize_agent_output({"output": [
        {"type": "mcp_call", "name": "knowledge_base_retrieve", "output": output},
    ]})

    assert len(result["references"]) == 1
    assert result["references"][0]["id"] == 0
    assert result["references"][0]["marker"] == "【6:3†source】"


def test_agent_mcp_marker_with_document_metadata_does_not_invent_ref_id(chat_app):
    output = (
        "Risultati:\n【3:2†source】"
        + json.dumps({"uid": "doc-1", "blob_url": "https://storage.blob.core.windows.net/docs/guida.pdf"})
        + "\nFine"
    )
    result = chat_app.summarize_agent_output({"output": [
        {"type": "mcp_call", "name": "knowledge_base_retrieve", "output": output},
    ]})

    reference = result["references"][0]
    assert reference["id"] is None
    assert reference["title"] == "guida.pdf"
    assert reference["marker"] == "【3:2†source】"


def test_agent_mcp_list_marker_is_not_assigned_to_an_individual_reference(chat_app):
    output = "【6:0†source】" + json.dumps([
        {"ref_id": 0, "uri": "https://example.com/manual", "sourceData": {"title": "Manuale"}}
    ])
    result = chat_app.summarize_agent_output({"output": [
        {"type": "mcp_call", "name": "knowledge_base_retrieve", "output": output},
    ]})

    assert result["references"][0]["id"] == 0
    assert "marker" not in result["references"][0]


def test_knowledge_references_reads_mcp_documents_with_json_content(chat_app):
    url = "https://acct.blob.core.windows.net/specs/Manuale%20pompa.md"
    content = json.dumps({"uid": "doc-1", "blob_url": url, "snippet": "Pressione massima 6 bar"})
    output = json.dumps({"documents": [{"id": "d1", "content": content, "title": url, "url": url, "knowledgeSourceIndex": 0}]})
    [reference] = chat_app._knowledge_references(output, 2)
    assert reference["id"] == "d1"
    assert reference["title"] == "Manuale pompa.md"
    assert reference["path"] == url
    assert reference["url"] is None
    assert reference["tool_call"] == 2


def test_indexed_sharepoint_reference_uses_document_path(chat_app):
    [reference] = chat_app.summarize_references({
        "activity": [{"id": 7, "type": "indexedSharePoint", "knowledgeSourceName": "policies-sharepoint-indexed-ks"}],
        "references": [{"type": "indexedSharePoint", "id": "4", "activitySource": 7, "rerankerScore": 2.52,
                        "docUrl": "/drives/b!abc/root:/policies/EN/5_Equipment%20Handling.pdf", "sourceData": None}],
    })
    assert reference["title"] == "5_Equipment Handling.pdf"
    assert reference["path"] == "policies/EN/5_Equipment Handling.pdf"
    assert reference["url"] is None
    assert reference["score"] == 2.52
    assert reference["knowledge_source"] == "policies-sharepoint-indexed-ks"
