"""Chat web app to try Foundry IQ knowledge bases with and without document-level ACL enforcement.

Each question is sent to the knowledge base retrieve API. The app identity (managed identity on App
Service, Azure CLI locally) authorizes the call; when ACLs are enabled the signed-in user's token is
forwarded in `x-ms-query-source-authorization`, so Foundry IQ returns only the documents the user can
read. The extracted content is then turned into an answer by the chat model deployment.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote
from uuid import UUID

import requests
from azure.core.exceptions import AzureError
from azure.identity import AzureCliCredential, DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AzureOpenAI
from pydantic import BaseModel, Field

from mcp_references import (
    _as_dict, _knowledge_mcp_activity, _knowledge_references, _short, _tool_event,
    summarize_agent_output, summarize_references,
)

SEARCH_SCOPE = "https://search.azure.com/.default"
SEARCH_AUDIENCES = ("https://search.azure.com", "880da380-985e-4198-81b9-e05b1cc53158")
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BATCH_URL = "https://graph.microsoft.com/v1.0/$batch"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
RUNNING_ON_APP_SERVICE = bool(os.getenv("WEBSITE_SITE_NAME"))
logger = logging.getLogger(__name__)


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
                "label": "Compressore CMP-4324 · spia CTL11 e approvazione",
                "question": "Il compressore CMP-4324 non parte e la spia CTL11 è rossa: è l'alimentatore? "
                "Il cavo ha l'etichetta UL 817, cosa significa? Posso sostituire il pezzo da solo o serve "
                "un'approvazione?",
                "sources": ["Manuali", "Ricambi", "Web", "SharePoint"],
            },
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
        ],
        # Additional multi-source test queries, shown only in the test panel.
        "extra_tests": [
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
graph_managed_credential = ManagedIdentityCredential() if RUNNING_ON_APP_SERVICE else None


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
graph_tokens = _TokenCache()
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
    profile = user_profile(request, claims, source)
    if claims.get("aud") not in SEARCH_AUDIENCES:
        # Easy Auth returns a Graph token when the Search scope is missing from the login parameters.
        token = None
    return {
        "token": token,
        "name": name or claims.get("upn") or claims.get("preferred_username") or claims.get("name"),
        "expires_on": claims.get("exp"),
        "source": source,
        "profile": profile,
    }


def user_profile(request: Request, access_claims: dict[str, Any], source: str) -> dict[str, Any]:
    """Display-only allowlist; never expose tokens or use these claims to authorize retrieval."""
    identity_claims = access_claims
    principal_claims: dict[str, list[str]] = {}
    if RUNNING_ON_APP_SERVICE:
        id_token = request.headers.get("x-ms-token-aad-id-token")
        if id_token:
            identity_claims = jwt_claims(id_token)
        encoded = request.headers.get("x-ms-client-principal")
        if encoded:
            try:
                principal = json.loads(base64.b64decode(encoded, validate=True))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("Claim di identità Easy Auth non leggibili: accedi di nuovo.") from exc
            if not isinstance(principal, dict) or not isinstance(principal.get("claims"), list):
                raise ValueError("Claim di identità Easy Auth non validi: accedi di nuovo.")
            for claim in principal["claims"]:
                if isinstance(claim, dict) and isinstance(claim.get("typ"), str) and isinstance(claim.get("val"), str):
                    principal_claims.setdefault(claim["typ"], []).append(claim["val"])

    def values(key: str, mapped: str | None = None) -> list[str]:
        found = principal_claims.get(key, []) + principal_claims.get(mapped or "", [])
        if found:
            return found
        value = identity_claims.get(key)
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str)]
        return []

    def first(key: str, mapped: str | None = None) -> str | None:
        return next(iter(values(key, mapped)), None)

    groups = values("groups", "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups")
    claim_names = identity_claims.get("_claim_names")
    principal_overage = any(
        key.rsplit("/", 1)[-1] == "groups.link"
        or (key.rsplit("/", 1)[-1] == "hasgroups" and any(value.lower() == "true" for value in vals))
        or (key == "_claim_names" and any('"groups"' in value for value in vals))
        for key, vals in principal_claims.items()
    )
    overage = bool(identity_claims.get("hasgroups")) or (
        isinstance(claim_names, dict) and "groups" in claim_names
    ) or principal_overage
    groups_present = "groups" in identity_claims or any(key.rsplit("/", 1)[-1] == "groups" for key in principal_claims)
    return {
        "display_name": first("name", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"),
        "username": request.headers.get("x-ms-client-principal-name") if RUNNING_ON_APP_SERVICE else (
            first("preferred_username") or first("upn")
        ),
        "object_id": first("oid", "http://schemas.microsoft.com/identity/claims/objectidentifier")
        or (request.headers.get("x-ms-client-principal-id") if RUNNING_ON_APP_SERVICE else None),
        "tenant_id": first("tid", "http://schemas.microsoft.com/identity/claims/tenantid"),
        "source": source,
        "groups": sorted(set(groups)),
        "groups_status": "overage" if overage else "available" if groups_present else "unavailable",
        "roles": sorted(set(values("roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"))),
        "token_expires_on": access_claims.get("exp"),
    }


def _graph_status_message(status: int) -> str:
    if status in (401, 403):
        return f"Accesso a Microsoft Graph negato (HTTP {status}): verifica Group.ReadBasic.All e il consenso amministrativo."
    if status == 429:
        return "Microsoft Graph ha limitato le richieste: riprova."
    if status == 404:
        return "Uno o più gruppi non sono visibili o non esistono in Microsoft Graph."
    return f"Microsoft Graph ha restituito un errore HTTP {status}."


def resolve_group_names(groups: list[str]) -> dict[str, Any]:
    """Resolve only group IDs in the current user's claims, never group memberships."""
    names: dict[str, str] = {}
    problems: set[str] = set()
    valid_groups: list[tuple[str, str]] = []
    for group in groups:
        try:
            valid_groups.append((group, str(UUID(group))))
        except ValueError:
            problems.add("Alcuni valori nei claim non sono Object ID di gruppi validi.")

    if valid_groups:
        credential = graph_managed_credential if RUNNING_ON_APP_SERVICE else local_user_credential
        try:
            token = graph_tokens.get(credential, GRAPH_SCOPE)
        except AzureError as exc:
            logger.warning("Acquisizione token Microsoft Graph fallita: %s", type(exc).__name__)
            problems.add("Impossibile ottenere un token Microsoft Graph: verifica l'identità e le autorizzazioni.")
        else:
            for offset in range(0, len(valid_groups), 20):
                group_batch = valid_groups[offset:offset + 20]
                by_request_id = {str(index): group for index, group in enumerate(group_batch, 1)}
                body = {"requests": [
                    {"id": request_id, "method": "GET", "url": f"/groups/{group_id}?$select=id,displayName"}
                    for request_id, (_, group_id) in by_request_id.items()
                ]}
                try:
                    response = requests.post(
                        GRAPH_BATCH_URL,
                        headers={"Authorization": f"Bearer {token}"},
                        json=body,
                        timeout=(3.05, 5),
                        allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    logger.warning("Richiesta Microsoft Graph fallita: %s", type(exc).__name__)
                    problems.add("Microsoft Graph non raggiungibile: riprova.")
                    break
                if response.status_code != 200:
                    problems.add(_graph_status_message(response.status_code))
                    break
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                responses = payload.get("responses") if isinstance(payload, dict) else None
                if not isinstance(responses, list):
                    problems.add("Microsoft Graph ha restituito una risposta incompleta o non valida.")
                    break
                seen: set[str] = set()
                for item in responses:
                    if not isinstance(item, dict):
                        problems.add("Microsoft Graph ha restituito una risposta incompleta o non valida.")
                        continue
                    request_id = item.get("id")
                    if not isinstance(request_id, str) or request_id not in by_request_id or request_id in seen:
                        problems.add("Microsoft Graph ha restituito una risposta incompleta o non valida.")
                        continue
                    seen.add(request_id)
                    status = item.get("status")
                    if type(status) is not int:
                        problems.add("Microsoft Graph ha restituito una risposta incompleta o non valida.")
                        continue
                    if status != 200:
                        problems.add(_graph_status_message(status))
                        continue
                    group, expected_id = by_request_id[request_id]
                    result = item.get("body")
                    if (
                        isinstance(result, dict)
                        and isinstance(result.get("id"), str)
                        and result["id"].lower() == expected_id
                        and isinstance(result.get("displayName"), str)
                        and result["displayName"].strip()
                    ):
                        names[group] = result["displayName"]
                    else:
                        problems.add("Microsoft Graph ha restituito una risposta incompleta o non valida.")
                if len(seen) != len(by_request_id):
                    problems.add("Microsoft Graph ha restituito una risposta incompleta o non valida.")

    if not problems and len(names) == len(groups):
        return {"names": names, "status": "available", "message": None}
    message = " ".join(sorted(problems)) or "Microsoft Graph non ha restituito tutti i nomi."
    logger.warning("Risoluzione nomi gruppi incompleta: %s", message)
    return {"names": names, "status": "partial" if names else "unavailable", "message": message}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    knowledge_base: str = "demo"
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    use_acl: bool = True
    stream: bool = False


class AgentChatRequest(BaseModel):
    agent: str
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    stream: bool = False


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


def summarize_activity(result: dict[str, Any]) -> list[dict[str, Any]]:
    activity = []
    for item in result.get("activity") or []:
        entry = {k: item[k] for k in (
            "id", "type", "knowledgeSourceName", "count", "elapsedMs", "startedAt", "completedAt",
            "inputTokens", "outputTokens", "warning", "status",
        ) if item.get(k) is not None}
        for value in item.values():
            if isinstance(value, dict) and isinstance(value.get("search"), str):
                entry["query"] = value["search"]
        mcp = item.get("mcpServerArguments")
        if isinstance(mcp, dict):
            if mcp.get("toolName"):
                entry["mcp_tool"] = mcp["toolName"]
            if isinstance(mcp.get("toolArguments"), dict):
                entry["mcp_arguments"] = mcp["toolArguments"]
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
    completion = openai_client().chat.completions.create(
        model=CHAT_DEPLOYMENT,
        messages=synthesis_messages(messages, extracted),
        max_completion_tokens=4000,
    )
    return completion.choices[0].message.content or ""


def synthesis_messages(messages: list[ChatMessage], extracted: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[{"role": m.role, "content": m.content} for m in messages[:-1]],
        {"role": "user", "content": (
            f"Contenuti recuperati da Foundry IQ:\n{extracted[:60000]}\n\nDomanda: {messages[-1].content}"
        )},
    ]


def sse_event(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def log_event(
    started: float, phase: str, title: str, detail: str | None = None,
    level: str = "info", private: bool = False,
) -> str:
    """Live timeline entry; `private` flags queries/tool arguments (shown in the Trace after redaction)."""
    data: dict[str, Any] = {
        "at_ms": round((time.perf_counter() - started) * 1000), "phase": phase, "title": title, "level": level,
    }
    if detail:
        data["detail"] = detail
    if private:
        data["private"] = True
    return sse_event("log", data)


_ACTIVITY_NAMES = {
    "modelQueryPlanning": "Pianificazione query (LLM)",
    "modelAnswerSynthesis": "Sintesi risposta (LLM)",
    "agenticReasoning": "Ragionamento agentico",
    "semanticReranker": "Reranking semantico",
}


def _activity_label(item: dict[str, Any]) -> str:
    kind = str(item.get("type") or "attività")
    name = item.get("knowledgeSourceName")
    return f"{name} ({kind})" if name else _ACTIVITY_NAMES.get(kind, kind)


def _activity_detail(item: dict[str, Any]) -> str | None:
    parts = []
    mcp = item.get("mcpServerArguments")
    if isinstance(mcp, dict) and mcp.get("toolName"):
        arguments = mcp.get("toolArguments")
        parts.append(f"tool MCP {mcp['toolName']}"
                     + (f" {_short(json.dumps(arguments, ensure_ascii=False), 160)}" if arguments else ""))
    if item.get("count") is not None:
        parts.append(f"{item['count']} risultati")
    if item.get("elapsedMs") is not None:
        parts.append(f"{item['elapsedMs']} ms")
    if item.get("inputTokens") is not None or item.get("outputTokens") is not None:
        parts.append(f"token {item.get('inputTokens', '?')} in / {item.get('outputTokens', '?')} out")
    if item.get("error"):
        parts.append(f"errore: {_short(item['error'], 200)}")
    return " · ".join(parts) or None


def _reference_titles(references: list[dict[str, Any]], limit: int = 5) -> str | None:
    titles = []
    for ref in references[:limit]:
        title = str(ref.get("title") or ref.get("id") or "riferimento")
        if ref.get("knowledge_source"):
            title += f" [{ref['knowledge_source']}]"
        if isinstance(ref.get("score"), (int, float)):
            title += f" ({ref['score']:.2f})"
        titles.append(title)
    if len(references) > limit:
        titles.append(f"+{len(references) - limit} altri")
    return " · ".join(titles) or None


def read_sse(lines: Iterable[str | bytes]) -> Iterator[tuple[str, Any]]:
    """Parse named SSE events, including comments and multiline JSON data."""
    name, data = "message", []
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if not line:
            if data:
                yield name, json.loads("\n".join(data))
            name, data = "message", []
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield name, json.loads("\n".join(data))


def retrieval_usage(activity: list[dict[str, Any]]) -> dict[str, int]:
    usage = {}
    for source, target in (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens")):
        values = [item[source] for item in activity if type(item.get(source)) is int]
        if values:
            usage[target] = sum(values)
    if "input_tokens" in usage and "output_tokens" in usage:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage


def _merge_available(earlier: dict[str, Any], later: dict[str, Any]) -> dict[str, Any]:
    merged = earlier.copy()
    for key, value in later.items():
        if value is None or value == "":
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_available(merged[key], value)
        else:
            merged[key] = value
    return merged


def _item_id(item: dict[str, Any]) -> tuple[type, str | int] | None:
    value = item.get("id")
    return (type(value), value) if type(value) in (str, int) and value != "" else None


def _merge_observed(final: list[dict[str, Any]], observed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconcile only unique IDs; retain unidentifiable observations if no final items exist."""
    merged = [item.copy() for item in final]
    for item in observed:
        identity = _item_id(item)
        if identity is None:
            if not final:
                merged.append(item.copy())
            continue
        final_matches = [i for i, current in enumerate(final) if _item_id(current) == identity]
        observed_matches = sum(_item_id(current) == identity for current in observed)
        if len(final_matches) == 1 and observed_matches == 1:
            index = final_matches[0]
            merged[index] = _merge_available(item, merged[index])
        elif not final_matches:
            merged.append(item.copy())
    return merged


def _observe_item(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    identity = _item_id(item)
    matches = [i for i, current in enumerate(items) if identity is not None and _item_id(current) == identity]
    if len(matches) == 1:
        items[matches[0]] = _merge_available(items[matches[0]], item)
    else:
        items.append(item.copy())


def stream_chat(body: ChatRequest, kb: dict[str, Any], user_token: str | None, started: float) -> Iterator[str]:
    result_data = {
        "answer": "", "knowledge_base": kb["name"], "acl_applied": body.use_acl and bool(user_token),
        "execution": {
            "identity": "Managed identity della web app" if RUNNING_ON_APP_SERVICE else "Credenziale Azure locale",
            "user_token_forwarded": bool(user_token), "acl_requested": body.use_acl,
            "remote_sharepoint": bool(kb.get("remote_sharepoint")), "chat_deployment": CHAT_DEPLOYMENT,
        },
    }
    response = None
    try:
        yield sse_event("status", {"stage": "Retrieval Foundry IQ", "result": result_data})
        headers = {
            "Authorization": f"Bearer {tokens.get(app_credential, SEARCH_SCOPE)}",
            "Accept": "text/event-stream",
        }
        if user_token:
            headers["x-ms-query-source-authorization"] = user_token
        url = f"{SEARCH_ENDPOINT}/knowledgebases/{kb['name']}/retrieve?api-version={SEARCH_API_VERSION}"
        yield log_event(started, "retrieval", f"Richiesta retrieve alla knowledge base {kb['name']}", " · ".join([
            f"input: {kb['query']}",
            "token utente inoltrato (ACL)" if user_token else "identità dell'app, nessun token utente",
        ]))
        response = requests.post(url, headers=headers, json=retrieve_body(body.messages, kb["query"]), timeout=120, stream=True)
        if response.status_code == 400 and kb["query"] == "messages":
            response.close()
            yield log_event(started, "retrieval", "Formato messages rifiutato (400): nuovo tentativo con intents", level="warning")
            response = requests.post(url, headers=headers, json=retrieve_body(body.messages, "intents"), timeout=120, stream=True)
        if not response.ok:
            raise HTTPException(status_code=502, detail=f"Retrieve {response.status_code}: {response.text[:800]}")
        if "text/event-stream" not in response.headers.get("content-type", "").lower():
            raise RuntimeError("Il servizio retrieve non ha restituito uno stream SSE.")
        response.encoding = "utf-8"
        completed = None
        raw_activity: list[dict[str, Any]] = []
        raw_references: list[dict[str, Any]] = []
        activity_started: dict[str | int, int] = {}
        activity_timings: dict[str | int, dict[str, Any]] = {}
        for name, data in read_sse(response.iter_lines(chunk_size=1, decode_unicode=True)):
            if name == "retrieval.started":
                yield log_event(started, "retrieval", "Retrieval avviato da Foundry IQ", " · ".join(
                    f"{label}: {data[key]}" for key, label in (
                        ("outputMode", "output"), ("reasoningEffort", "reasoning effort"), ("requestId", "request id"),
                    ) if data.get(key) is not None
                ) or None)
            elif name in ("activity.started", "activity.completed"):
                _observe_item(raw_activity, data)
                activity = summarize_activity({"activity": [data]})[0]
                activity["status"] = "in_progress" if name.endswith("started") else "completed"
                activity_id = activity.get("id")
                if type(activity_id) in (str, int) and activity_id != "":
                    now_ms = round((time.perf_counter() - started) * 1000)
                    if name.endswith("started"):
                        activity["started_ms"] = activity_started.setdefault(activity_id, now_ms)
                    else:
                        activity["completed_ms"] = now_ms
                        if activity_id in activity_started:
                            activity["started_ms"] = activity_started[activity_id]
                            activity["duration_ms"] = now_ms - activity["started_ms"]
                    activity_timings[activity_id] = {
                        key: activity[key] for key in ("status", "started_ms", "completed_ms", "duration_ms")
                        if key in activity
                    }
                yield sse_event("activity", activity)
                if name.endswith("started"):
                    yield log_event(started, "retrieval", f"Avviata: {_activity_label(data)}")
                else:
                    yield log_event(
                        started, "retrieval", f"Completata: {_activity_label(data)}", _activity_detail(data),
                        level="error" if data.get("error") else "info",
                    )
                    if activity.get("query"):
                        yield log_event(started, "retrieval", f"Query eseguita su {_activity_label(data)}",
                                        activity["query"], private=True)
            elif name == "references.completed" and isinstance(data, list):
                raw_references = data
                references = summarize_references({"references": data, "activity": raw_activity})
                result_data["references"] = references
                yield sse_event("references", {"items": references})
                yield log_event(started, "retrieval", f"Riferimenti risolti: {len(references)}", _reference_titles(references))
            elif name == "response.completed":
                completed = data["response"]
                if data.get("statusCode") == 206:
                    yield log_event(started, "retrieval", "Risultato parziale (206): alcune fonti non hanno risposto", level="warning")
                break
            elif name == "error":
                raise RuntimeError(f"Retrieve: {json.dumps(data.get('error', data))[:800]}")
        if completed is None:
            raise RuntimeError("Stream retrieve interrotto prima del completamento.")
        response.close()
        completed = {
            **completed,
            "activity": _merge_observed(completed.get("activity") or [], raw_activity),
            "references": _merge_observed(completed.get("references") or [], raw_references),
        }
        activity = summarize_activity(completed)
        for item in activity:
            activity_id = item.get("id")
            if type(activity_id) in (str, int) and activity_id in activity_timings:
                item.update(activity_timings[activity_id])
        result_data.update(
            references=summarize_references(completed), sources=summarize_sources(activity),
            activity=activity, retrieval_usage=retrieval_usage(activity),
        )
        usage_text = result_data["retrieval_usage"].get("total_tokens")
        yield log_event(started, "retrieval", "Retrieval completato", " · ".join([
            f"{len(result_data['references'])} riferimenti",
            f"{len(result_data['sources'])} fonti interrogate",
            f"token retrieval: {usage_text}" if usage_text is not None else "token retrieval non restituiti",
        ]))
        extracted = extracted_text(completed)
        has_content = extracted.strip() not in ("", "[]", "{}")
        result_data["execution"]["synthesis_performed"] = has_content
        yield sse_event("status", {"stage": "Generazione risposta", "result": result_data})
        if has_content:
            yield log_event(started, "generation", f"Sintesi con il modello {CHAT_DEPLOYMENT}",
                            f"{len(extracted)} caratteri di contenuto recuperato")
            with openai_client().chat.completions.create(
                model=CHAT_DEPLOYMENT, messages=synthesis_messages(body.messages, extracted),
                max_completion_tokens=4000, stream=True, stream_options={"include_usage": True},
            ) as stream:
                finished = False
                for chunk in stream:
                    if chunk.usage is not None:
                        usage = chunk.usage
                        result_data["usage"] = {
                            "input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens,
                            "total_tokens": usage.total_tokens,
                        }
                        yield sse_event("usage", result_data["usage"])
                        yield log_event(started, "generation", "Token generazione ricevuti",
                                        f"{usage.prompt_tokens} in / {usage.completion_tokens} out / {usage.total_tokens} totali")
                    for choice in chunk.choices:
                        if choice.delta.content:
                            if not result_data["answer"]:
                                yield log_event(started, "generation", "Primo token della risposta")
                            result_data["answer"] += choice.delta.content
                            yield sse_event("delta", {"text": choice.delta.content})
                        if choice.finish_reason is not None:
                            if choice.finish_reason != "stop":
                                raise RuntimeError(f"Generazione incompleta: {choice.finish_reason}.")
                            finished = True
                            yield log_event(started, "generation", "Generazione terminata", f"{len(result_data['answer'])} caratteri")
                if not finished:
                    raise RuntimeError("Stream del modello interrotto prima del completamento.")
        else:
            yield log_event(started, "generation", "Nessun contenuto recuperato: sintesi saltata", level="warning")
            result_data["answer"] = NO_CONTENT_ANSWER
            yield sse_event("delta", {"text": NO_CONTENT_ANSWER})
        result_data["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        yield log_event(started, "done", "Risposta completata", f"{result_data['elapsed_ms']} ms totali")
        yield sse_event("completed", result_data)
    except Exception as exc:  # Stream errors must remain visible after HTTP headers have been sent.
        message = str(exc.detail if isinstance(exc, HTTPException) else exc)[:800]
        yield log_event(started, "done", "Errore", message, level="error")
        yield sse_event("error", {"message": message})
    finally:
        if response is not None:
            response.close()


def _tool_result_detail(item: dict[str, Any]) -> str | None:
    results = item.get("results")
    if isinstance(results, list):
        names = [str(_as_dict(r).get("filename") or _as_dict(r).get("title") or "") for r in results]
        names = [n for n in names if n]
        return " · ".join([f"{len(results)} risultati", *names[:5]])
    output = item.get("output")
    if output not in (None, ""):
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
        return f"output di {len(text)} caratteri"
    return None


def stream_agent(body: AgentChatRequest, started: float) -> Iterator[str]:
    text_emitted = False
    try:
        yield sse_event("status", {"stage": "Esecuzione agente e tool"})
        yield log_event(started, "agent", f"Richiesta inviata all'agente {body.agent}", "identità del progetto, nessun token utente")
        tool_numbers: dict[tuple[type, str | int], int] = {}
        tool_started: dict[str | int, int] = {}
        arguments: dict[tuple[type, str | int], str] = {}
        observed_items: list[dict[str, Any]] = []
        observed_tools: dict[tuple[type, str | int], dict[str, Any]] = {}
        observed_citations: list[dict[str, Any]] = []
        with agents_client().responses.create(
            input=[{"role": m.role, "content": m.content} for m in body.messages],
            extra_body={"agent_reference": {"name": body.agent, "type": "agent_reference"}}, stream=True,
        ) as stream:
            for event in stream:
                if event.type == "response.output_text.delta":
                    if not text_emitted and event.delta:
                        yield log_event(started, "generation", "Primo token della risposta")
                    text_emitted = text_emitted or bool(event.delta)
                    yield sse_event("delta", {"text": event.delta})
                elif event.type == "response.output_text.done":
                    yield log_event(started, "generation", "Testo della risposta terminato")
                elif event.type == "response.created":
                    model = _as_dict(getattr(event, "response", None)).get("model")
                    yield log_event(started, "agent", "Esecuzione avviata dal servizio", f"modello: {model}" if model else None)
                elif event.type in ("response.output_item.added", "response.output_item.done"):
                    item = _as_dict(event.item)
                    kind = item.get("type") or ""
                    done = event.type.endswith("done")
                    if kind.endswith("_call"):
                        key = _item_id(item)
                        if key is not None:
                            tool_numbers.setdefault(key, len(tool_numbers) + 1)
                            _observe_item(observed_items, item)
                        elif done:
                            observed_items.append(item.copy())
                        tool = _tool_event(item, arguments.get(key) if key is not None else None)
                        now_ms = round((time.perf_counter() - started) * 1000)
                        tool_id = item.get("id")
                        if type(tool_id) in (str, int) and tool_id != "":
                            if not done:
                                tool["started_ms"] = tool_started.setdefault(tool_id, now_ms)
                            else:
                                tool["completed_ms"] = now_ms
                                if tool_id in tool_started:
                                    tool["started_ms"] = tool_started[tool_id]
                                    tool["duration_ms"] = now_ms - tool["started_ms"]
                        if key is not None:
                            observed_tools[key] = _merge_available(observed_tools.get(key, {}), tool)
                        yield sse_event("tool", tool)
                        label = str(tool["name"] or "tool") + (f" ({tool['server']})" if tool["server"] else "")
                        if not done:
                            yield log_event(started, "tool", f"Chiamata tool avviata: {label}", kind)
                            continue
                        failed = bool(tool["error"]) or tool["status"] == "failed"
                        yield log_event(
                            started, "tool", f"Tool {'in errore' if failed else 'completato'}: {label}",
                            tool["error"] or _tool_result_detail(item), level="error" if failed else "info",
                        )
                        if tool["arguments"] and key not in arguments:
                            yield log_event(started, "tool", f"Argomenti di {label}", tool["arguments"], private=True)
                        if kind == "mcp_call" and item.get("name") == "knowledge_base_retrieve":
                            number = tool_numbers.get(key)
                            references = _knowledge_references(item.get("output"), number, tool)
                            yield sse_event("references", {"items": references, "append": True})
                            for activity in _knowledge_mcp_activity(item.get("output"), number, tool):
                                yield sse_event("activity", activity)
                            yield log_event(started, "tool", f"Riferimenti Foundry IQ ricevuti: {len(references)}",
                                            _reference_titles(references))
                    elif kind == "mcp_list_tools":
                        if done:
                            tools = [str(_as_dict(t).get("name")) for t in item.get("tools") or [] if _as_dict(t).get("name")]
                            yield log_event(started, "tool", f"Tool disponibili da {item.get('server_label') or 'server MCP'}: {len(tools)}",
                                            ", ".join(tools[:10]) or None)
                        else:
                            yield log_event(started, "tool", f"Elenco tool dal server MCP {item.get('server_label') or ''}".strip())
                    elif kind == "reasoning":
                        summary = " ".join(str(_as_dict(s).get("text") or "") for s in item.get("summary") or []).strip()
                        yield log_event(started, "agent", "Ragionamento completato" if done else "Ragionamento del modello",
                                        _short(summary, 400) if done else None)
                    elif kind == "message":
                        yield log_event(started, "generation",
                                        "Scrittura della risposta terminata" if done else "Scrittura della risposta")
                    elif kind.endswith("_output") and done:
                        yield log_event(started, "tool", f"Risultato ricevuto: {kind}", _tool_result_detail(item))
                elif event.type in ("response.mcp_call_arguments.done", "response.function_call_arguments.done"):
                    key = _item_id({"id": getattr(event, "item_id", None)})
                    args = getattr(event, "arguments", None)
                    if not isinstance(args, str):
                        args = None
                    if args:
                        if key is not None:
                            arguments[key] = args
                        yield log_event(started, "tool", "Argomenti inviati al tool", args, private=True)
                elif event.type == "response.output_text.annotation.added":
                    note = _as_dict(getattr(event, "annotation", None))
                    title = note.get("title") or note.get("filename") or note.get("url") or note.get("file_id")
                    if title:
                        citation = {"title": title, "url": note.get("url"), "type": note.get("type")}
                        observed_citations.append(citation)
                        yield sse_event("citation", citation)
                        yield log_event(started, "generation", f"Citazione: {_citation_title(str(title))}", note.get("type"))
                elif event.type == "response.completed":
                    response_data = _as_dict(event.response)
                    output = [_as_dict(item) for item in response_data.get("output") or []]
                    final_calls = [item for item in output if str(item.get("type") or "").endswith("_call")]
                    merged_calls = _merge_observed(final_calls, observed_items)
                    for item in merged_calls:
                        key = _item_id(item)
                        if not item.get("arguments") and key in arguments:
                            item["arguments"] = arguments[key]
                    calls = iter(merged_calls)
                    response_data["output"] = [
                        next(calls) if str(item.get("type") or "").endswith("_call") else item for item in output
                    ] + list(calls)
                    result = agent_result(body, response_data, started)
                    for call in result["tool_calls"]:
                        key = _item_id(call)
                        if key in observed_tools:
                            call.update(_merge_available(observed_tools[key], call))
                    for reference in result["references"]:
                        key = _item_id({"id": reference.get("tool_call_id")})
                        matches = [call for call in result["tool_calls"] if key is not None and _item_id(call) == key]
                        if len(matches) == 1:
                            reference["mcp_call"] = matches[0].copy()
                    for citation in observed_citations:
                        identity = (citation.get("type"), citation.get("url") or citation.get("title"))
                        if not any((item.get("type"), item.get("url") or item.get("title")) == identity
                                   for item in result["citations"]):
                            result["citations"].append(citation)
                    usage = result.get("usage") or {}
                    yield log_event(started, "done", "Risposta completata", " · ".join([
                        f"{len(result.get('tool_calls') or [])} chiamate tool",
                        f"{len(result.get('citations') or []) + len(result.get('references') or [])} riferimenti",
                        f"token: {usage['total_tokens']}" if usage.get("total_tokens") is not None else "token non restituiti",
                        f"{result['elapsed_ms']} ms",
                    ]))
                    yield sse_event("completed", result)
                    return
                elif event.type in ("error", "response.failed", "response.incomplete"):
                    details = _as_dict(event)
                    error = _as_dict(_as_dict(details.get("response")).get("error"))
                    raise RuntimeError(f"Stream agente non completato: {details.get('message') or error.get('message') or event.type}.")
        raise RuntimeError("Stream agente interrotto prima del completamento.")
    except Exception as exc:  # Report terminal failures to the comparison card.
        if body.agent == "wpc-rag-classico-accurato" and not text_emitted and "A user error occurred while executing a tool call" in str(exc):
            compatibility = {
                "mode": "json_retry",
                "warning": "Il tool ha interrotto lo streaming: query ripetuta senza streaming.",
                "usage_scope": "Tool e token mostrati si riferiscono al retry. Il consumo del tentativo interrotto non è disponibile.",
            }
            yield log_event(started, "agent", "Stream interrotto dal tool: retry senza streaming", str(exc)[:400], level="warning")
            yield sse_event("status", {
                "stage": "Retry senza streaming del risultato",
                "result": {"answer": "", "streaming": compatibility},
            })
            try:
                response = agents_client().responses.create(
                    input=[{"role": m.role, "content": m.content} for m in body.messages],
                    extra_body={"agent_reference": {"name": body.agent, "type": "agent_reference"}},
                )
                status = response.get("status") if isinstance(response, Mapping) else getattr(response, "status", None)
                if status != "completed":
                    raise RuntimeError(f"Risposta del retry non completata: {status}.")
                result = agent_result(body, response, started)
                yield log_event(started, "done", "Retry completato", f"{len(result.get('tool_calls') or [])} chiamate tool")
                yield sse_event("completed", {**result, "streaming": compatibility})
            except Exception as retry_error:
                message = f"Retry agente {body.agent}: {str(retry_error)[:800]}"
                yield log_event(started, "done", "Retry fallito", message, level="error")
                yield sse_event("error", {"message": message})
            return
        message = f"Agente {body.agent}: {str(exc)[:800]}"
        yield log_event(started, "done", "Errore", message, level="error")
        yield sse_event("error", {"message": message})


def agent_result(body: AgentChatRequest, response: Any, started: float) -> dict[str, Any]:
    return {
        "agent": body.agent, **AGENTS[body.agent], **summarize_agent_output(response),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "execution": {"identity": "Identità del progetto", "user_token_forwarded": False, "acl_requested": False},
    }


def streaming_response(events: Iterable[str]) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


def _citation_title(title: str) -> str:
    return unquote(title.rstrip("/").split("/")[-1]) if title.startswith("http") else title


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
    response = await call_next(request)
    if request.url.path in ("/api/config", "/api/profile/group-names"):
        response.headers["Cache-Control"] = "no-store"
    return response


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
        "user_profile": user.get("profile"),
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
        "chat_deployment": CHAT_DEPLOYMENT,
    }


@app.get("/api/profile/group-names")
def profile_group_names(request: Request) -> dict[str, Any]:
    try:
        profile = current_user(request)["profile"]
    except (AzureError, ValueError) as exc:
        logger.warning("Profilo utente non disponibile per Microsoft Graph: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="Profilo utente non disponibile: accedi di nuovo.") from exc
    return {
        "object_id": profile["object_id"],
        "tenant_id": profile["tenant_id"],
        "groups": profile["groups"],
        "groups_status": profile["groups_status"],
        **resolve_group_names(profile["groups"]),
    }


@app.post("/api/agent-chat")
def agent_chat(body: AgentChatRequest):
    """Runs the conversation on a Foundry prompt agent (project identity: no per-user ACL)."""
    if body.agent not in AGENTS:
        raise HTTPException(status_code=400, detail="Agente sconosciuto.")
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="L'ultimo messaggio deve essere dell'utente.")
    if not PROJECT_ENDPOINT:
        raise HTTPException(status_code=500, detail="Configurazione mancante: FOUNDRYIQ_DEMO_PROJECT_ENDPOINT.")
    started = time.perf_counter()
    if body.stream:
        return streaming_response(stream_agent(body, started))
    try:
        response = agents_client().responses.create(
            input=[{"role": m.role, "content": m.content} for m in body.messages],
            extra_body={"agent_reference": {"name": body.agent, "type": "agent_reference"}},
        )
    except Exception as exc:  # noqa: BLE001 - surfaced in the comparison card
        raise HTTPException(status_code=502, detail=f"Agente {body.agent}: {str(exc)[:800]}") from exc
    return agent_result(body, response, started)


@app.post("/api/chat")
def chat(body: ChatRequest, request: Request):
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
    if body.stream:
        return streaming_response(stream_chat(body, kb, user_token, started))
    result = retrieve(kb, body.messages, user_token)
    extracted = extracted_text(result)
    answer = synthesize(body.messages, extracted)
    activity = summarize_activity(result)
    return {
        "answer": answer,
        "knowledge_base": kb["name"],
        "acl_applied": body.use_acl and bool(user_token),
        "references": summarize_references(result),
        "sources": summarize_sources(activity),
        "activity": activity,
        "retrieval_usage": retrieval_usage(activity),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "execution": {
            "identity": "Managed identity della web app" if RUNNING_ON_APP_SERVICE else "Credenziale Azure locale",
            "user_token_forwarded": bool(user_token),
            "acl_requested": body.use_acl,
            "remote_sharepoint": bool(kb.get("remote_sharepoint")),
            "synthesis_performed": extracted.strip() not in ("", "[]", "{}"),
            "chat_deployment": CHAT_DEPLOYMENT,
        },
    }


app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets", check_dir=False), name="assets")


@app.get("/")
def index() -> FileResponse:
    if not (FRONTEND_DIR / "index.html").is_file():
        raise HTTPException(status_code=503, detail="Frontend non compilato: esegui npm ci e npm run build in webapp-chat/frontend.")
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/trace")
def trace() -> FileResponse:
    return index()


@app.get("/demo/multi-fonte")
@app.get("/demo/acl")
@app.get("/demo/agenti")
def demo_page() -> FileResponse:
    return index()
