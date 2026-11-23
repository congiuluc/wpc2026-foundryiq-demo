"""Normalize returned citations and MCP call metadata without exposing tool output."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    dump = getattr(item, "model_dump", None)
    return dump() if callable(dump) else {}


def _short(value: Any, limit: int = 300) -> str | None:
    if value in (None, "", [], {}):
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def summarize_references(result: dict[str, Any]) -> list[dict[str, Any]]:
    references = []
    activity_by_id: dict[str, dict[str, Any] | None] = {}
    for item in result.get("activity") or []:
        if item.get("id") not in (None, ""):
            identity = str(item["id"])
            activity_by_id[identity] = item if identity not in activity_by_id else None
    for ref in result.get("references") or []:
        activity_id = ref.get("activitySource")
        activity = (activity_by_id.get(str(activity_id)) or {}) if activity_id not in (None, "") else {}
        source = ref.get("sourceData") or {}
        location = ref.get("blobUrl") or ref.get("url") or ref.get("webUrl") or source.get("url")
        doc_url = ref.get("docUrl") or source.get("doc_url")
        if not location and isinstance(doc_url, str):
            # Indexed SharePoint returns a Graph drive path.
            location = unquote(doc_url.split("root:/", 1)[-1]) if "root:/" in doc_url else doc_url
        title = ref.get("title") or source.get("title") or source.get("name")
        if not title and location:
            title = unquote(location.rstrip("/").split("/")[-1])
        is_storage = bool(location) and (".blob.core." in location or ".dfs.core." in location)
        reference = {
            "id": ref.get("id"),
            "type": ref.get("type"),
            "activity_source": activity_id,
            "knowledge_source": activity.get("knowledgeSourceName"),
            "knowledge_source_type": activity.get("type") if activity.get("knowledgeSourceName") else None,
            "title": title or ref.get("docKey") or str(ref.get("id")),
            "url": location if location and location.startswith("http") and not is_storage else None,
            "path": location,
            "score": ref.get("rerankerScore"),
        }
        mcp = activity.get("mcpServerArguments") if isinstance(activity.get("mcpServerArguments"), dict) else {}
        tool = ref.get("toolName") or mcp.get("toolName")
        if ref.get("type") == "mcpServer" or activity.get("type") == "mcpServer" or tool:
            if tool:
                reference["mcp_tool"] = tool
            if isinstance(mcp.get("toolArguments"), dict):
                reference["mcp_arguments"] = mcp["toolArguments"]
            for source_key, target_key in (
                ("elapsedMs", "elapsed_ms"), ("count", "result_count"),
                ("startedAt", "mcp_started_at"), ("completedAt", "mcp_completed_at"),
                ("status", "mcp_status"), ("error", "mcp_error"), ("warning", "mcp_warning"),
            ):
                if activity.get(source_key) is not None:
                    reference[target_key] = activity[source_key]
        references.append(reference)
    return references


def notebook_reference_rows(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "ref_id": ref.get("id"), "marcatore": ref.get("marker"), "fonte": ref.get("knowledge_source"),
        "tipo": ref.get("type"), "tipo fonte": ref.get("knowledge_source_type"),
        "titolo": ref.get("title"), "documento": ref.get("path"), "score": ref.get("score"),
        "id attività": ref.get("activity_source"), "chiamata": ref.get("tool_call"), "id chiamata": ref.get("tool_call_id"),
        "chiamata MCP": ref.get("mcp_call"), "tool MCP": ref.get("mcp_tool"),
        "argomenti MCP": ref.get("mcp_arguments"), "risultati MCP": ref.get("result_count"),
        "ms MCP": ref.get("elapsed_ms"), "inizio MCP": ref.get("mcp_started_at"),
        "fine MCP": ref.get("mcp_completed_at"), "stato MCP": ref.get("mcp_status"), "errore MCP": ref.get("mcp_error"),
        "avviso MCP": ref.get("mcp_warning"),
    } for ref in references]


def _mcp_result_count(output: Any) -> int | None:
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            return None
    if not isinstance(output, Mapping):
        return None
    count = output.get("count")
    if type(count) is int and count >= 0:
        return count
    structured = output.get("structuredContent")
    if isinstance(structured, Mapping):
        return _mcp_result_count(structured)
    content = output.get("content")
    if isinstance(content, list) and len(content) == 1 and isinstance(content[0], Mapping):
        if content[0].get("type") == "text":
            return _mcp_result_count(content[0].get("text"))
    return None


def _tool_event(item: dict[str, Any], arguments: str | None = None) -> dict[str, Any]:
    action = _as_dict(item.get("action"))
    raw_arguments = item.get("arguments")
    if raw_arguments is None:
        raw_arguments = action.get("query") or action.get("queries")
    if item["type"] == "mcp_call":
        formatted = (raw_arguments if isinstance(raw_arguments, str) else
                     json.dumps(raw_arguments, ensure_ascii=False, default=str) if raw_arguments is not None else None)
    else:
        formatted = _short(raw_arguments)
    error = item.get("error")
    if item["type"] == "mcp_call" and error not in (None, "", [], {}):
        error = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False, default=str)
    else:
        error = _short(error)
    result = {
        "id": item.get("id"), "type": item["type"],
        "name": item.get("name") or (None if item["type"] == "mcp_call"
                                     else item.get("server_label") or item["type"].removesuffix("_call")),
        "server": item.get("server_label"), "status": item.get("status"),
        "arguments": formatted if formatted not in (None, "") else arguments,
        "error": error,
    }
    if item["type"] == "mcp_call":
        count = _mcp_result_count(item.get("output"))
        if count is not None:
            result["result_count"] = count
    return result


_SOURCE_MARKER = re.compile(r"【\d+:\d+†[^】]+】")


def _knowledge_text_values(value: str, marker: str | None = None):
    decoder = json.JSONDecoder()
    position = 0
    while position < len(value):
        while position < len(value) and value[position].isspace():
            position += 1
        found = _SOURCE_MARKER.match(value, position)
        if found:
            marker = found.group()
            position = found.end()
            continue
        if position == len(value):
            break
        try:
            parsed, end = decoder.raw_decode(value, position)
        except json.JSONDecodeError:
            found = _SOURCE_MARKER.search(value, position)
            if not found:
                return
            marker = found.group()
            position = found.end()
            continue
        if not isinstance(parsed, str) or parsed != value[position:end]:
            yield parsed, marker
        marker = None
        position = end


def _knowledge_reference_records(value: Any, marker: str | None = None, activity: list | None = None):
    if isinstance(value, str):
        for parsed, block_marker in _knowledge_text_values(value, marker):
            yield from _knowledge_reference_records(parsed, block_marker, activity)
    elif isinstance(value, list):
        for item in value:
            yield from _knowledge_reference_records(item, activity=activity)
    elif isinstance(value, Mapping):
        records = value.get("references")
        if isinstance(records, list) and records:
            entries = value.get("activity")
            linked_activity = entries if isinstance(entries, list) else activity
            for record in records:
                if isinstance(record, Mapping):
                    yield record, None, linked_activity
            return
        source_fields = ("sourceData", "uri", "url", "web_url", "blob_url", "blobUrl", "uid", "document_id")
        has_source_data = any(key in value for key in source_fields)
        has_reference = value.get("ref_id") is not None and has_source_data
        has_marked_document = marker and has_source_data
        if has_reference or has_marked_document:
            yield value, marker, activity
        elif isinstance(value.get("documents"), list):
            for document in value["documents"]:
                if isinstance(document, Mapping) and any(key in document for key in source_fields):
                    yield document, marker, activity
        elif isinstance(value.get("content"), list):
            yield from _knowledge_reference_records(value["content"], marker, activity)
        elif value.get("type") == "text" and isinstance(value.get("text"), str):
            yield from _knowledge_reference_records(value["text"], marker, activity)
        elif isinstance(value.get("result"), (Mapping, list, str)):
            yield from _knowledge_reference_records(value["result"], marker, activity)


def _knowledge_mcp_activity(value: Any, call_number: int | None, call: dict[str, Any]):
    if isinstance(value, str):
        for parsed, _ in _knowledge_text_values(value):
            yield from _knowledge_mcp_activity(parsed, call_number, call)
    elif isinstance(value, list):
        for item in value:
            yield from _knowledge_mcp_activity(item, call_number, call)
    elif isinstance(value, Mapping):
        entries = value.get("activity")
        if isinstance(entries, list):
            for item in entries:
                if not isinstance(item, Mapping) or item.get("type") != "mcpServer":
                    continue
                entry = {key: item[key] for key in (
                    "id", "type", "knowledgeSourceName", "count", "elapsedMs", "startedAt", "completedAt",
                    "status", "error", "warning",
                ) if item.get(key) is not None}
                arguments = item.get("mcpServerArguments")
                if isinstance(arguments, Mapping):
                    if arguments.get("toolName"):
                        entry["mcp_tool"] = arguments["toolName"]
                    if isinstance(arguments.get("toolArguments"), Mapping):
                        entry["mcp_arguments"] = dict(arguments["toolArguments"])
                entry["tool_call"] = call_number
                if call.get("id") not in (None, ""):
                    entry["tool_call_id"] = call["id"]
                yield entry
        for key in ("content", "result", "structuredContent"):
            nested = value.get(key)
            if isinstance(nested, (Mapping, list, str)):
                yield from _knowledge_mcp_activity(nested, call_number, call)
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            yield from _knowledge_mcp_activity(value["text"], call_number, call)


def _document_source(record: Mapping[str, Any]) -> dict[str, Any]:
    source = _as_dict(record.get("sourceData")) or dict(record)
    content = source.get("content")
    if isinstance(content, str) and content.lstrip().startswith("{"):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, Mapping):
            source = {**parsed, **{key: value for key, value in source.items() if key != "content"}}
    if str(source.get("title") or "").startswith("http"):
        source.pop("title")
    return source


def _knowledge_references(output: Any, call_number: int | None, call: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    references = []
    for record, marker, activity in _knowledge_reference_records(output):
        source = _document_source(record)
        raw = {
            "id": record.get("ref_id") if record.get("ref_id") is not None else record.get("id"),
            "type": record.get("kind") or record.get("type"),
            "activitySource": record.get("activitySource"),
            "toolName": record.get("toolName") or source.get("toolName"),
            "title": record.get("title") if not str(record.get("title") or "").startswith("http") else None,
            "blobUrl": source.get("blob_url") or source.get("blobUrl"),
            "url": source.get("url") or source.get("web_url") or record.get("uri"),
            "sourceData": source,
            "docKey": record.get("docKey") or source.get("uid") or source.get("document_id") or marker,
            "rerankerScore": record.get("rerankerScore"),
        }
        reference = summarize_references({"references": [raw], "activity": activity or []})[0]
        reference["knowledge_source"] = (
            reference["knowledge_source"] or record.get("knowledgeSourceName") or source.get("knowledgeSourceName")
        )
        reference["tool_call"] = call_number
        if call is not None:
            reference["mcp_call"] = call.copy()
            if call.get("id") not in (None, ""):
                reference["tool_call_id"] = call["id"]
        if marker:
            reference["marker"] = marker
        references.append(reference)
    return references


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def summarize_agent_output(response: Any) -> dict[str, Any]:
    """Extract answer, observed tool calls, citations and reference metadata."""
    data = _as_dict(response)
    answer_parts: list[str] = []
    answer_length = 0
    tool_calls: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    citation_indices: dict[tuple[str, str], int] = {}
    citation_spans: list[dict[str, int]] = []
    references: list[dict[str, Any]] = []
    activity: list[dict[str, Any]] = []
    for item in data.get("output") or []:
        item = _as_dict(item)
        kind = item.get("type") or ""
        if kind == "message":
            for content in item.get("content") or []:
                content = _as_dict(content)
                text = content.get("text")
                offset = None
                if isinstance(text, str) and text:
                    offset = answer_length + (1 if answer_parts else 0)
                    answer_parts.append(text)
                    answer_length = offset + _utf16_length(text)
                for note in content.get("annotations") or []:
                    note = _as_dict(note)
                    key = note.get("url") or note.get("file_id") or note.get("filename") or note.get("title")
                    if not isinstance(key, str) or not key:
                        continue
                    identity = (str(note.get("type") or ""), key)
                    if identity not in citation_indices:
                        citation_indices[identity] = len(citations)
                        citations.append({"title": note.get("title") or note.get("filename") or key,
                                          "url": note.get("url"), "type": note.get("type")})
                    start, end = note.get("start_index"), note.get("end_index")
                    if offset is not None and type(start) is int and type(end) is int and 0 <= start < end <= len(text):
                        citation_spans.append({
                            "start": offset + _utf16_length(text[:start]),
                            "end": offset + _utf16_length(text[:end]),
                            "citation_index": citation_indices[identity],
                        })
                    elif offset is not None and note.get("type") == "file_citation":
                        index = note.get("index")
                        if type(index) is int and 0 <= index <= len(text):
                            position = offset + _utf16_length(text[:index])
                            citation_spans.append({
                                "start": position, "end": position, "citation_index": citation_indices[identity],
                            })
        elif kind.endswith("_call"):
            call = _tool_event(item)
            tool_calls.append(call)
            if kind == "mcp_call" and item.get("name") == "knowledge_base_retrieve":
                references.extend(_knowledge_references(item.get("output"), len(tool_calls), call))
                activity.extend(_knowledge_mcp_activity(item.get("output"), len(tool_calls), call))
    usage = _as_dict(data.get("usage"))
    return {
        "answer": "\n".join(answer_parts) or data.get("output_text") or "",
        "tool_calls": tool_calls,
        "citations": citations,
        "citation_spans": citation_spans,
        "references": references,
        "activity": activity,
        "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens", "total_tokens") if usage.get(k) is not None},
    }
