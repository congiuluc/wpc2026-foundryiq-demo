// Parses Foundry agent traces (messages with parts: text, tool_call, tool_call_response).
// Tool responses from Foundry IQ / file search contain "【turn:index†source】" markers followed by a JSON
// object with uid, blob_url and snippet. Runs in the browser and in Node (for tests).
(function (root) {
  "use strict";

  const SOURCE_MARKER = /【(\d+):(\d+)†([^】]*)】/g;

  function normalizeNewlines(text) {
    return String(text ?? "").replace(/\r\n/g, "\n");
  }

  function decodeBase64Url(value) {
    const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const binary = typeof atob === "function" ? atob(padded) : Buffer.from(padded, "base64").toString("binary");
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  }

  // Azure AI Search document keys: "<prefix>_<base64url(url)><padding digit>_pages_<n>".
  function parseUid(uid) {
    const result = { uid, url: null, page: null };
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

  function documentName(url) {
    if (!url) return null;
    try {
      return decodeURIComponent(url.split("?")[0].replace(/\/+$/, "").split("/").pop());
    } catch {
      return url.split("/").pop();
    }
  }

  // Parses the JSON object that follows a source marker; any text after it is returned as trailer.
  function parseSourceBody(body) {
    const start = body.indexOf("{");
    const end = body.lastIndexOf("}");
    if (start >= 0 && end > start) {
      try {
        return { data: JSON.parse(body.slice(start, end + 1)), trailer: body.slice(end + 1).trim() };
      } catch {
        // fall through: show raw text
      }
    }
    return { data: null, trailer: "", raw: body.trim() };
  }

  function parseToolResponse(response) {
    if (response && typeof response === "object") {
      return { header: "", sources: [], trailer: "", json: response };
    }
    const text = normalizeNewlines(response);
    const markers = [...text.matchAll(SOURCE_MARKER)];
    if (!markers.length) {
      try {
        return { header: "", sources: [], trailer: "", json: JSON.parse(text) };
      } catch {
        return { header: text.trim(), sources: [], trailer: "" };
      }
    }
    const sources = [];
    let trailer = "";
    markers.forEach((marker, i) => {
      const bodyEnd = i + 1 < markers.length ? markers[i + 1].index : text.length;
      const parsed = parseSourceBody(text.slice(marker.index + marker[0].length, bodyEnd));
      const data = parsed.data || {};
      const uidInfo = parseUid(data.uid);
      const url = data.blob_url || data.url || data.doc_url || uidInfo.url;
      sources.push({
        marker: marker[0],
        turn: Number(marker[1]),
        index: Number(marker[2]),
        label: marker[3],
        uid: data.uid || null,
        url,
        document: data.title || documentName(url) || data.uid || marker[0],
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

  function parseArguments(value) {
    if (typeof value !== "string") return value;
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }

  function normalizePart(part) {
    if (typeof part === "string") return { type: "text", text: part };
    const type = part.type || "unknown";
    if (type === "tool_call_response" || type === "function_call_output" || type === "tool_result") {
      const response = part.response ?? part.output ?? part.content;
      return { type: "tool_call_response", id: part.id || part.call_id || part.tool_call_id, ...parseToolResponse(response) };
    }
    if (type === "tool_call" || type === "function_call") {
      return {
        type: "tool_call",
        id: part.id || part.call_id,
        name: part.name || part.function?.name || part.tool_name || "tool",
        arguments: parseArguments(part.arguments ?? part.function?.arguments ?? part.input),
      };
    }
    if (type === "text" || type === "input_text" || type === "output_text") {
      return { type: "text", text: normalizeNewlines(part.text ?? part.content ?? "") };
    }
    return { type, raw: part };
  }

  function normalizeMessage(message, position) {
    const parts = message.parts ?? message.content ?? [];
    const list = Array.isArray(parts) ? parts : [parts];
    return {
      position,
      role: message.role || message.type || "unknown",
      name: message.name || null,
      parts: list.map(normalizePart),
    };
  }

  function messagesFrom(value) {
    if (Array.isArray(value)) return value;
    if (value && typeof value === "object") {
      for (const key of ["messages", "data", "items", "trace", "conversation"]) {
        if (Array.isArray(value[key])) return value[key];
      }
      return [value];
    }
    throw new Error("La traccia deve essere un oggetto JSON o un array di messaggi.");
  }

  // Accepts a JSON message, an array of messages, an object with a messages array, or JSON Lines.
  function parseTrace(input) {
    const text = String(input ?? "").trim();
    if (!text) throw new Error("Traccia vuota.");
    let value;
    try {
      value = JSON.parse(text);
    } catch (error) {
      const lines = text.split(/\r?\n/).filter((l) => l.trim());
      try {
        value = lines.map((l) => JSON.parse(l));
      } catch {
        throw new Error(`JSON non valido: ${error.message}`);
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

  const api = { parseTrace, parseToolResponse, parseUid };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TraceParser = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
