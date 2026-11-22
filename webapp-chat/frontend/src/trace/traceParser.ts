// Parses Foundry agent traces (messages with parts: text, tool_call, tool_call_response).
// Tool responses from Foundry IQ / file search contain "【turn:index†source】" markers followed by a JSON
// object with uid, blob_url and snippet.
import type { ParsedTrace, TracePart, TraceSource } from "../types";

type JsonRecord = Record<string, unknown>;

const SOURCE_MARKER = /【(\d+):(\d+)†([^】]*)】/g;

function normalizeNewlines(text: unknown): string {
  return String(text ?? "").replace(/\r\n/g, "\n");
}

function isRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function decodeBase64Url(value: string): string {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const bytes = Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

// Azure AI Search document keys: "<prefix>_<base64url(url)><padding digit>_pages_<n>".
export function parseUid(uid: unknown): { uid: unknown; url: string | null; page: number | null } {
  const result: { uid: unknown; url: string | null; page: number | null } = { uid, url: null, page: null };
  if (typeof uid !== "string") return result;
  const page = uid.match(/_pages_(\d+)$/);
  if (page) result.page = Number(page[1]);
  const core = page ? uid.slice(0, page.index) : uid;
  const encoded = core.includes("_") ? core.slice(core.indexOf("_") + 1) : core;
  for (const candidate of [encoded.slice(0, -1), encoded]) {
    try {
      const decoded = decodeBase64Url(candidate);
      if (/^https?:\/\//.test(decoded)) {
        result.url = decoded;
        break;
      }
    } catch {
      // not base64: leave url empty
    }
  }
  return result;
}

function documentName(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    return decodeURIComponent(url.split("?")[0].replace(/\/+$/, "").split("/").pop() || "");
  } catch {
    return url.split("/").pop() || null;
  }
}

// Parses the JSON object that follows a source marker; any text after it is returned as trailer.
function parseSourceBody(body: string): { data: JsonRecord | null; trailer: string; raw?: string } {
  const start = body.indexOf("{");
  const end = body.lastIndexOf("}");
  if (start >= 0 && end > start) {
    try {
      const data: unknown = JSON.parse(body.slice(start, end + 1));
      if (isRecord(data)) return { data, trailer: body.slice(end + 1).trim() };
    } catch {
      // fall through: show raw text
    }
  }
  return { data: null, trailer: "", raw: body.trim() };
}

const str = (value: unknown): string | undefined => (typeof value === "string" && value ? value : undefined);

export function parseToolResponse(response: unknown): Pick<TracePart, "header" | "sources" | "trailer" | "json"> {
  if (response && typeof response === "object") return { header: "", sources: [], trailer: "", json: response };
  const text = normalizeNewlines(response);
  const markers = [...text.matchAll(SOURCE_MARKER)];
  if (!markers.length) {
    try {
      return { header: "", sources: [], trailer: "", json: JSON.parse(text) };
    } catch {
      return { header: text.trim(), sources: [], trailer: "" };
    }
  }
  const sources: TraceSource[] = [];
  let trailer = "";
  markers.forEach((marker, i) => {
    const markerIndex = marker.index ?? 0;
    const bodyEnd = i + 1 < markers.length ? markers[i + 1].index ?? text.length : text.length;
    const parsed = parseSourceBody(text.slice(markerIndex + marker[0].length, bodyEnd));
    const data = parsed.data || {};
    const uidInfo = parseUid(data.uid);
    const url = str(data.blob_url) || str(data.url) || str(data.doc_url) || uidInfo.url;
    sources.push({
      marker: marker[0],
      uid: str(data.uid) || null,
      url,
      document: str(data.title) || documentName(url) || str(data.uid) || marker[0],
      page: uidInfo.page,
      snippet: normalizeNewlines(data.snippet ?? data.content ?? data.text ?? parsed.raw ?? ""),
      extra: Object.fromEntries(
        Object.entries(data).filter(([k]) => !["uid", "blob_url", "url", "snippet", "content", "text", "title"].includes(k)),
      ),
    });
    if (i === markers.length - 1) trailer = parsed.trailer;
  });
  return { header: text.slice(0, markers[0].index).trim(), sources, trailer };
}

function parseArguments(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function normalizePart(part: unknown): TracePart {
  if (typeof part === "string") return { type: "text", text: part };
  if (!isRecord(part)) return { type: "unknown", raw: part };
  const type = str(part.type) || "unknown";
  if (type === "retrieved_sources") return { type, sources: Array.isArray(part.sources) ? part.sources as TraceSource[] : [] };
  if (type === "tool_call_response" || type === "function_call_output" || type === "tool_result") {
    const response = part.response ?? part.output ?? part.content;
    return { type: "tool_call_response", id: str(part.id) || str(part.call_id) || str(part.tool_call_id), ...parseToolResponse(response) };
  }
  if (type === "tool_call" || type === "function_call") {
    const fn = isRecord(part.function) ? part.function : {};
    return {
      type: "tool_call",
      id: str(part.id) || str(part.call_id),
      name: str(part.name) || str(fn.name) || str(part.tool_name) || "tool",
      arguments: parseArguments(part.arguments ?? fn.arguments ?? part.input),
      status: str(part.status),
      server: str(part.server),
      tool_type: str(part.tool_type),
      error: str(part.error),
      ...(typeof part.started_ms === "number" ? { started_ms: part.started_ms } : {}),
      ...(typeof part.duration_ms === "number" ? { duration_ms: part.duration_ms } : {}),
    };
  }
  if (type === "text" || type === "input_text" || type === "output_text") {
    return { type: "text", text: normalizeNewlines(part.text ?? part.content ?? "") };
  }
  return { type, raw: part };
}

function normalizeMessage(message: unknown, position: number): ParsedTrace["messages"][number] {
  const record = isRecord(message) ? message : {};
  const parts = record.parts ?? record.content ?? [];
  const list = Array.isArray(parts) ? parts : [parts];
  return {
    position,
    role: str(record.role) || str(record.type) || "unknown",
    name: str(record.name) || null,
    parts: list.map(normalizePart),
  };
}

function messagesFrom(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (isRecord(value)) {
    for (const key of ["messages", "data", "items", "trace", "conversation"]) {
      const candidate = value[key];
      if (Array.isArray(candidate)) return candidate;
    }
    return [value];
  }
  throw new Error("La traccia deve essere un oggetto JSON o un array di messaggi.");
}

// Accepts a JSON message, an array of messages, an object with a messages array, or JSON Lines.
export function parseTrace(input: unknown): ParsedTrace {
  const text = String(input ?? "").trim();
  if (!text) throw new Error("Traccia vuota.");
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    const lines = text.split(/\r?\n/).filter((l) => l.trim());
    try {
      value = lines.map((l) => JSON.parse(l));
    } catch {
      throw new Error(`JSON non valido: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  const messages = messagesFrom(value).map(normalizeMessage);
  const parts = messages.flatMap((m) => m.parts);
  const sources = parts.flatMap((p) => p.sources || []);
  return {
    messages,
    stats: {
      messages: messages.length,
      toolCalls: parts.filter((p) => p.type === "tool_call").length,
      toolResponses: parts.filter((p) => p.type === "tool_call_response").length,
      sources: sources.length,
      documents: new Set(sources.map((s) => s.url || s.document)).size,
    },
  };
}
