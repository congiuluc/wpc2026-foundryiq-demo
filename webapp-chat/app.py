"""Chat web app to try Foundry IQ knowledge bases with and without document-level ACL enforcement.

Each question is sent to the knowledge base retrieve API. The app identity (managed identity on App
Service, Azure CLI locally) authorizes the call; when ACLs are enabled the signed-in user's token is
forwarded in `x-ms-query-source-authorization`, so Foundry IQ returns only the documents the user can
read. The extracted content is then turned into an answer by the chat model deployment.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

import requests
from azure.identity import AzureCliCredential, DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import AzureOpenAI
from pydantic import BaseModel, Field

SEARCH_SCOPE = "https://search.azure.com/.default"
SEARCH_AUDIENCES = ("https://search.azure.com", "880da380-985e-4198-81b9-e05b1cc53158")
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
STATIC_DIR = Path(__file__).parent / "static"
RUNNING_ON_APP_SERVICE = bool(os.getenv("WEBSITE_SITE_NAME"))


def _load_azd_env() -> None:
    """For local runs, read missing settings from the current azd environment."""
    if RUNNING_ON_APP_SERVICE or os.getenv("FOUNDRYIQ_DEMO_SEARCH_ENDPOINT"):
        return
    azd = shutil.which("azd")
    if not azd:
        return
    try:
        result = subprocess.run(
            [azd, "env", "get-values", "--output", "json"],
            capture_output=True, text=True, check=True, timeout=30,
            cwd=Path(__file__).resolve().parent.parent,
        )
        for key, value in json.loads(result.stdout).items():
            os.environ.setdefault(key, str(value))
    except (subprocess.SubprocessError, json.JSONDecodeError):
        pass


_load_azd_env()

SEARCH_ENDPOINT = os.getenv("FOUNDRYIQ_DEMO_SEARCH_ENDPOINT", "").rstrip("/")
OPENAI_ENDPOINT = os.getenv("FOUNDRYIQ_DEMO_OPENAI_ENDPOINT", "").rstrip("/")
CHAT_DEPLOYMENT = os.getenv("FOUNDRYIQ_DEMO_CHAT_DEPLOYMENT", "gpt-5-mini")
SEARCH_API_VERSION = os.getenv("FOUNDRYIQ_SEARCH_API_VERSION", "2026-11-01-preview")
OPENAI_API_VERSION = os.getenv("FOUNDRYIQ_OPENAI_API_VERSION", "2025-04-01-preview")
PROJECT_ENDPOINT = os.getenv("FOUNDRYIQ_DEMO_PROJECT_ENDPOINT", "").rstrip("/")

# Agents compared in the "Agenti" mode: classic RAG agents with independent tools vs the Foundry IQ agent.
# Deployed by scripts/setup_foundry_agents.py from agents/*.yaml.
AGENTS: dict[str, dict[str, str]] = {
    "wpc-rag-classico-rapido": {
        "label": "RAG classico · rapido",
        "kind": "classic",
        "description": "Tool indipendenti: indice manuali keyword top 3, MCP ricambi (2 tool), web filtrato.",
    },
    "wpc-rag-classico-accurato": {
        "label": "RAG classico · accurato",
        "kind": "classic",
        "description": "Stesse sorgenti del RAG rapido: indice manuali semantic top 10, MCP ricambi (7 tool), web filtrato, prompt di orchestrazione.",
    },
    "wpc-technician-assistant": {
        "label": "Foundry IQ · knowledge base",
        "kind": "foundryiq",
        "description": "Un solo tool: la knowledge base Foundry IQ (manuali, MCP, web filtrato e policy SharePoint) pianifica le query.",
    },
}

KNOWLEDGE_BASES: dict[str, dict[str, Any]] = {
    "demo": {
        "name": os.getenv("FOUNDRYIQ_DEMO_KB_NAME", "wpc-foundryiq-kb"),
        "label": "Demo WPC (manuali, ricambi, policy, web)",
        # Low reasoning effort: the knowledge base plans queries from the conversation.
        "query": "messages",
        # policies-sharepoint-ks is remoteSharePoint: it always queries SharePoint as the signed-in user,
        # so the user token is forwarded in x-ms-query-source-authorization even without ACL mode.
        "remote_sharepoint": True,
        "examples": [
            # Each example needs more than one knowledge source (manuals, MCP inventory, SharePoint, Bing).
            {
                "label": "Rx1 · inceppamenti e motore di tensione",
                "question": "Il Rx1 Digital Weave Integration Station ha inceppamenti del tessuto: cosa controllo "
                "secondo il manuale, abbiamo un Weave Tension Motor disponibile e le policy dicono di ripararlo "
                "o sostituirlo?",
                "sources": ["Manuali", "Ricambi", "SharePoint"],
            },
            {
                "label": "Reclaim Tank · membrane filtranti",
                "question": "Devo cambiare le membrane filtranti della Graphene Vapor Reclaim Tank: ogni quanto va "
                "fatto, quante GRT-002 abbiamo a magazzino e come vanno smaltite quelle esauste?",
                "sources": ["Manuali", "Ricambi", "SharePoint"],
            },
            {
                "label": "Edge Node · alimentatore AD-60W-24",
                "question": "L'Edge Control Node P4311 non si accende: come verifico l'alimentatore AD-60W-24, ne "
                "abbiamo a magazzino o un'alternativa, e quali modelli equivalenti vendono i distributori online?",
                "sources": ["Manuali", "Ricambi", "Web", "SharePoint"],
            },
            {
                "label": "OQTR · cartucce esaurite",
                "question": "Le cartucce d'inchiostro dell'OQTR Digital Printing and Lacing Stand sono esaurite: "
                "ogni quanto vanno sostituite, c'è un ricambio alternativo disponibile e come segnalo il fermo "
                "al responsabile?",
                "sources": ["Manuali", "Ricambi", "SharePoint"],
            },
        ],
        # Additional multi-source test queries, shown only in the test panel.
        "extra_tests": [
            {
                "label": "Cutting Blade CGC-001 · manutenzione e sicurezza",
                "question": "Per la Cutting Blade Assembly CGC-001 del Contoso Robotic Garment Cut-Sew: frequenza di "
                "manutenzione, disponibilità e alternative, e regole di sicurezza nel maneggiarla.",
                "sources": ["Manuali", "Ricambi", "SharePoint"],
            },
            {
                "label": "Ricambi critici sotto scorta · Delta Printer",
                "question": "Quali ricambi ad alta criticità sono sotto scorta e, per quelli della Delta Nano Circuit "
                "Board 3D Printer, cosa prevede il manuale per la manutenzione?",
                "sources": ["Ricambi", "Manuali"],
            },
            {
                "label": "Print Bed Heater DS-PBH-004 · alternative",
                "question": "Il Print Bed Heater DS-PBH-004 ha 45 giorni di lead time: ci sono alternative a magazzino "
                "o prodotti equivalenti sui cataloghi dei distributori?",
                "sources": ["Ricambi", "Web"],
            },
        ],
    },
    "acl": {
        "name": os.getenv("FOUNDRYIQ_ACL_KB_NAME", "wpc-acl-kb"),
        "label": "Demo ACL (documenti con permessi)",
        # Minimal reasoning effort: the knowledge base accepts only explicit search intents.
        "query": "intents",
        "examples": [
            {
                "label": "Procedura richiesta ricambi · SLA P1",
                "question": "Qual è il codice della procedura di richiesta ricambi e lo SLA per la priorità P1?",
                "sources": ["Tutti i tecnici"],
                "expected_acl": ["WPC FoundryIQ ACL - Tecnici", "WPC FoundryIQ ACL - Direzione"],
            },
            {
                "label": "Ven Model 12 · modalità service (riservato)",
                "question": "Qual è il codice della modalità service del Ven Systems Model 12?",
                "sources": ["Solo gruppo autorizzato"],
                "expected_acl": ["WPC FoundryIQ ACL - Tecnici"],
            },
            {
                "label": "Accordi quadro 2027 · Delta (riservato)",
                "question": "Qual è lo sconto concordato con Delta Systems negli accordi quadro 2027?",
                "sources": ["Solo gruppo autorizzato"],
                "expected_acl": ["WPC FoundryIQ ACL - Direzione"],
            },
        ],
    },
}

SYSTEM_PROMPT = (
    "Sei l'assistente dei tecnici WPC. Rispondi in italiano usando solo le informazioni nei "
    "contenuti recuperati da Foundry IQ. Se i contenuti non bastano, dillo chiaramente senza "
    "inventare. Cita le fonti con il loro ref_id tra parentesi quadre, ad esempio [0]."
)
NO_CONTENT_ANSWER = "Nessun contenuto accessibile trovato nella knowledge base per questa domanda."

app_credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
local_user_credential = None if RUNNING_ON_APP_SERVICE else AzureCliCredential()


class _TokenCache:
    """Caches tokens per credential and scope: AzureCliCredential starts a process on every call."""

    def __init__(self) -> None:
        self._tokens: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, credential: Any, scope: str) -> str:
        key = f"{id(credential)}:{scope}"
        with self._lock:
            token = self._tokens.get(key)
            if token is None or token.expires_on - time.time() < 300:
                token = credential.get_token(scope)
                self._tokens[key] = token
            return token.token


tokens = _TokenCache()
_openai_client: AzureOpenAI | None = None
_agents_client: Any = None
_agents_lock = threading.Lock()


def agents_client() -> Any:
    """OpenAI client of the Foundry project, used to call agents through the Responses API."""
    global _agents_client
    with _agents_lock:
        if _agents_client is None:
            from azure.ai.projects import AIProjectClient

            _agents_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=app_credential).get_openai_client()
        return _agents_client


def openai_client() -> AzureOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AzureOpenAI(
            azure_endpoint=OPENAI_ENDPOINT,
            api_version=OPENAI_API_VERSION,
            azure_ad_token_provider=get_bearer_token_provider(app_credential, COGNITIVE_SCOPE),
        )
    return _openai_client


def jwt_claims(token: str) -> dict[str, Any]:
    """Reads claims for display and expiry checks only; Foundry IQ validates the token."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def current_user(request: Request) -> dict[str, Any]:
    """Returns the signed-in user's Search token: Easy Auth on App Service, Azure CLI locally."""
    if RUNNING_ON_APP_SERVICE:
        token = request.headers.get("x-ms-token-aad-access-token")
        name = request.headers.get("x-ms-client-principal-name")
        source = "App Service authentication"
    else:
        token = tokens.get(local_user_credential, SEARCH_SCOPE)
        name = None
        source = "Azure CLI (esecuzione locale)"
    claims = jwt_claims(token) if token else {}
    if claims.get("aud") not in SEARCH_AUDIENCES:
        # Easy Auth returns a Graph token when the Search scope is missing from the login parameters.
        token = None
    return {
        "token": token,
        "name": name or claims.get("upn") or claims.get("preferred_username") or claims.get("name"),
        "expires_on": claims.get("exp"),
        "source": source,
    }


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    knowledge_base: str = "demo"
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    use_acl: bool = True


class AgentChatRequest(BaseModel):
    agent: str
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)


def retrieve_body(messages: list[ChatMessage], mode: str) -> dict[str, Any]:
    if mode == "intents":
        question = next(m.content for m in reversed(messages) if m.role == "user")
        return {"intents": [{"type": "semantic", "search": question}], "includeActivity": True}
    return {
        "messages": [{"role": m.role, "content": [{"type": "text", "text": m.content}]} for m in messages],
        "includeActivity": True,
    }


def retrieve(kb: dict[str, Any], messages: list[ChatMessage], user_token: str | None) -> dict[str, Any]:
    url = f"{SEARCH_ENDPOINT}/knowledgebases/{kb['name']}/retrieve?api-version={SEARCH_API_VERSION}"
    headers = {"Authorization": f"Bearer {tokens.get(app_credential, SEARCH_SCOPE)}"}
    if user_token:
        headers["x-ms-query-source-authorization"] = user_token
    mode = kb["query"]
    response = requests.post(url, headers=headers, json=retrieve_body(messages, mode), timeout=120)
    if response.status_code == 400 and mode == "messages":
        # Knowledge bases with minimal reasoning effort reject conversation messages.
        response = requests.post(url, headers=headers, json=retrieve_body(messages, "intents"), timeout=120)
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"Retrieve {response.status_code}: {response.text[:800]}")
    return response.json()


def extracted_text(result: dict[str, Any]) -> str:
    parts = []
    for message in result.get("response") or []:
        for content in message.get("content") or []:
            if content.get("type") == "text" and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts)


def summarize_references(result: dict[str, Any]) -> list[dict[str, Any]]:
    references = []
    activity_by_id = {
        str(item["id"]): item
        for item in result.get("activity") or []
        if item.get("id") is not None
    }
    for ref in result.get("references") or []:
        activity_id = ref.get("activitySource")
        activity = activity_by_id.get(str(activity_id), {}) if activity_id is not None else {}
        source = ref.get("sourceData") or {}
        location = ref.get("blobUrl") or ref.get("url") or ref.get("webUrl") or source.get("url")
        title = ref.get("title") or source.get("title") or source.get("name")
        if not title and location:
            title = unquote(location.rstrip("/").split("/")[-1])
        # Storage URLs are not readable from the browser; only web and SharePoint links are clickable.
        is_storage = bool(location) and (".blob.core." in location or ".dfs.core." in location)
        references.append({
            "id": ref.get("id"),
            "type": ref.get("type"),
            "activity_source": activity_id,
            "knowledge_source": activity.get("knowledgeSourceName"),
            "knowledge_source_type": activity.get("type") if activity.get("knowledgeSourceName") else None,
            "title": title or ref.get("docKey") or str(ref.get("id")),
            "url": location if location and location.startswith("http") and not is_storage else None,
            "path": location,
            "score": ref.get("rerankerScore"),
        })
    return references


def summarize_activity(result: dict[str, Any]) -> list[dict[str, Any]]:
    activity = []
    for item in result.get("activity") or []:
        entry = {k: item[k] for k in ("id", "type", "knowledgeSourceName", "count", "elapsedMs") if item.get(k) is not None}
        for value in item.values():
            if isinstance(value, dict) and isinstance(value.get("search"), str):
                entry["query"] = value["search"]
        if item.get("error"):
            entry["error"] = item["error"]
        activity.append(entry)
    return activity


def summarize_sources(activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate retrieval activity per knowledge source (planning/synthesis steps have no source name)."""
    sources: dict[str, dict[str, Any]] = {}
    for item in activity:
        name = item.get("knowledgeSourceName")
        if not name:
            continue
        source = sources.setdefault(name, {"name": name, "type": item.get("type"), "queries": 0, "results": 0, "error": None})
        source["queries"] += 1
        source["results"] += item.get("count") or 0
        if item.get("error"):
            source["error"] = item["error"] if isinstance(item["error"], str) else json.dumps(item["error"])
    return list(sources.values())


def synthesize(messages: list[ChatMessage], extracted: str) -> str:
    if extracted.strip() in ("", "[]", "{}"):
        return NO_CONTENT_ANSWER
    history = [{"role": m.role, "content": m.content} for m in messages[:-1]]
    completion = openai_client().chat.completions.create(
        model=CHAT_DEPLOYMENT,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {
                "role": "user",
                "content": f"Contenuti recuperati da Foundry IQ:\n{extracted[:60000]}\n\nDomanda: {messages[-1].content}",
            },
        ],
        max_completion_tokens=4000,
    )
    return completion.choices[0].message.content or ""


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    return dump() if callable(dump) else {}


def _short(value: Any, limit: int = 300) -> str | None:
    if value in (None, "", [], {}):
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def summarize_agent_output(response: Any) -> dict[str, Any]:
    """Extracts answer, tool calls and citations from a Responses API result (any tool type)."""
    data = _as_dict(response)
    answer_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    citations: dict[str, dict[str, Any]] = {}
    for item in data.get("output") or []:
        item = _as_dict(item)
        kind = item.get("type") or ""
        if kind == "message":
            for content in item.get("content") or []:
                content = _as_dict(content)
                if content.get("text"):
                    answer_parts.append(content["text"])
                for note in content.get("annotations") or []:
                    note = _as_dict(note)
                    key = note.get("url") or note.get("title") or note.get("file_id")
                    if key and key not in citations:
                        citations[key] = {"title": note.get("title") or key, "url": note.get("url"), "type": note.get("type")}
        elif kind.endswith("_call") or kind == "mcp_list_tools":
            action = _as_dict(item.get("action"))
            tool_calls.append({
                "type": kind,
                "name": item.get("name") or item.get("server_label") or kind.removesuffix("_call"),
                "server": item.get("server_label"),
                "arguments": _short(item.get("arguments") or action.get("query") or action.get("queries")),
                "status": item.get("status"),
                "error": _short(item.get("error")),
            })
    usage = _as_dict(data.get("usage"))
    return {
        "answer": "\n".join(answer_parts) or data.get("output_text") or "",
        "tool_calls": [t for t in tool_calls if t["type"] != "mcp_list_tools"],
        "citations": list(citations.values()),
        "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens", "total_tokens") if usage.get(k) is not None},
    }


app = FastAPI(title="WPC Foundry IQ chat")


@app.middleware("http")
async def require_app_service_login(request: Request, call_next):
    """Fail-closed on App Service: without Easy Auth the identity headers could be forged by the client."""
    if RUNNING_ON_APP_SERVICE and request.url.path != "/healthz":
        if os.getenv("WEBSITE_AUTH_ENABLED", "").lower() != "true":
            return JSONResponse(
                status_code=503,
                content={"detail": "Autenticazione App Service non configurata: eseguire scripts/setup_chat_auth.ps1."},
            )
        if not request.headers.get("x-ms-client-principal-id"):
            return JSONResponse(status_code=401, content={"detail": "Accesso richiesto."})
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config(request: Request) -> dict[str, Any]:
    try:
        user = current_user(request)
    except Exception as exc:  # noqa: BLE001 - shown in the UI, e.g. missing az login
        user = {"token": None, "name": None, "source": f"errore: {exc}"}
    return {
        "user": user["name"],
        "user_source": user["source"],
        "user_token_available": bool(user["token"]),
        "running_on_app_service": RUNNING_ON_APP_SERVICE,
        "knowledge_bases": [
            {
                "id": key,
                "name": kb["name"],
                "label": kb["label"],
                "examples": kb["examples"],
                "tests": kb["examples"] + kb.get("extra_tests", []),
            }
            for key, kb in KNOWLEDGE_BASES.items()
        ],
        "agents": [{"name": name, **info} for name, info in AGENTS.items()],
        "agents_available": bool(PROJECT_ENDPOINT),
    }


@app.post("/api/agent-chat")
def agent_chat(body: AgentChatRequest) -> dict[str, Any]:
    """Runs the conversation on a Foundry prompt agent (project identity: no per-user ACL)."""
    if body.agent not in AGENTS:
        raise HTTPException(status_code=400, detail="Agente sconosciuto.")
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="L'ultimo messaggio deve essere dell'utente.")
    if not PROJECT_ENDPOINT:
        raise HTTPException(status_code=500, detail="Configurazione mancante: FOUNDRYIQ_DEMO_PROJECT_ENDPOINT.")
    started = time.perf_counter()
    try:
        response = agents_client().responses.create(
            input=[{"role": m.role, "content": m.content} for m in body.messages],
            extra_body={"agent_reference": {"name": body.agent, "type": "agent_reference"}},
        )
    except Exception as exc:  # noqa: BLE001 - surfaced in the comparison card
        raise HTTPException(status_code=502, detail=f"Agente {body.agent}: {str(exc)[:800]}") from exc
    return {
        "agent": body.agent,
        **AGENTS[body.agent],
        **summarize_agent_output(response),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


@app.post("/api/chat")
def chat(body: ChatRequest, request: Request) -> dict[str, Any]:
    kb = KNOWLEDGE_BASES.get(body.knowledge_base)
    if kb is None:
        raise HTTPException(status_code=400, detail="Knowledge base sconosciuta.")
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="L'ultimo messaggio deve essere dell'utente.")
    if not SEARCH_ENDPOINT or not OPENAI_ENDPOINT:
        raise HTTPException(
            status_code=500,
            detail="Configurazione mancante: FOUNDRYIQ_DEMO_SEARCH_ENDPOINT / FOUNDRYIQ_DEMO_OPENAI_ENDPOINT.",
        )

    user_token = None
    if body.use_acl:
        try:
            user = current_user(request)
        except Exception as exc:  # noqa: BLE001 - e.g. az login missing in local runs
            raise HTTPException(status_code=401, detail=f"Token utente non disponibile: {exc}") from exc
        if not user["token"]:
            raise HTTPException(status_code=401, detail="Token utente per Azure AI Search non disponibile: accedi di nuovo.")
        if user["expires_on"] and user["expires_on"] - time.time() < 60:
            raise HTTPException(status_code=401, detail="token_expired")
        user_token = user["token"]
    elif kb.get("remote_sharepoint"):
        # Best effort: without a user token only the SharePoint source fails, the others still answer.
        try:
            user = current_user(request)
        except Exception:  # noqa: BLE001
            user = {"token": None}
        if user["token"] and not (user.get("expires_on") and user["expires_on"] - time.time() < 60):
            user_token = user["token"]

    started = time.perf_counter()
    result = retrieve(kb, body.messages, user_token)
    answer = synthesize(body.messages, extracted_text(result))
    activity = summarize_activity(result)
    return {
        "answer": answer,
        "knowledge_base": kb["name"],
        "acl_applied": body.use_acl and bool(user_token),
        "references": summarize_references(result),
        "sources": summarize_sources(activity),
        "activity": activity,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/trace")
def trace() -> FileResponse:
    return FileResponse(STATIC_DIR / "trace.html")
