import json
from types import SimpleNamespace

import pytest

pytest.importorskip("imageio_ffmpeg", reason="Install scripts/media-requirements.txt for media checks.")
pytest.importorskip("playwright.sync_api", reason="Install scripts/media-requirements.txt for media checks.")

from scripts.capture_demo_media import collect_response


def test_media_manifest_records_observations_without_response_content_or_secrets():
    results = []
    response = SimpleNamespace(
        url="http://localhost/api/chat", ok=True, status=200, headers={},
        json=lambda: {
            "answer": "Personal or private response text",
            "api_key": "secret", "user": "person@example.test",
            "references": [{"url": "https://example.test/?token=secret"}],
            "sources": [{"name": "machinespecs-ks", "type": "azureBlob", "results": 3,
                         "error": "token=secret"}],
            "elapsed_ms": 1200, "usage": {"total_tokens": 500},
            "execution": {"acl_requested": True, "user_token_forwarded": True},
        },
    )
    collect_response(response, results)
    assert results[0]["references"] == 1
    assert results[0]["sources"] == [{"name": "machinespecs-ks", "type": "azureBlob", "results": 3, "error": True}]
    assert results[0]["acl_requested"] is True and results[0]["user_token_forwarded"] is True
    assert all(value not in json.dumps(results) for value in ("secret", "person@", "private response"))


def test_media_manifest_does_not_turn_http_errors_into_success():
    results = []
    collect_response(SimpleNamespace(url="http://localhost/api/agent-chat", ok=False, status=502), results)
    assert results == [{"ok": False, "http_status": 502}]
    collect_response(SimpleNamespace(url="http://localhost/api/config", ok=True), results)
    assert results == [{"ok": False, "http_status": 502}]


def test_media_manifest_uses_terminal_stream_event_and_rejects_incomplete_streams():
    response = SimpleNamespace(
        url="http://localhost/api/chat", ok=True, status=200,
        headers={"content-type": "text/event-stream"},
        text=lambda: 'event: delta\ndata: {"text":"private"}\n\nevent: completed\ndata: {"answer":"private","references":[{}]}\n\n',
    )
    results = []
    collect_response(response, results)
    assert results[0]["references"] == 1
    assert "private" not in json.dumps(results)
    response.text = lambda: 'event: delta\ndata: {"text":"parziale"}\n\n'
    with pytest.raises(RuntimeError, match="senza evento finale"):
        collect_response(response, [])
