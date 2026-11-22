import type { ChatResult, Mode, Reference } from "../types";

type Scalar = string | number | boolean;
type Picked = Record<string, Scalar>;
type Part = Record<string, unknown>;

export interface TraceContext { question: string; label: string; mode: Mode; knowledge_base?: string }
export interface TraceSnapshot { messages: { role: string; name?: Scalar; parts: Part[] }[] }
export interface TraceCollector {
  begin(context: TraceContext): number;
  finish(id: number, result: ChatResult | null, error?: Error): void;
  update(id: number, result: ChatResult): void;
  clear(): void;
  snapshot(): TraceSnapshot;
}

interface RequestEntry {
  id: number;
  context: Picked;
  started: string;
  completed?: string;
  status: "in_progress" | "completed" | "failed";
  parts: Part[];
}

// Project only response fields; never transfer config, headers or claims. Free text is redacted.
export function safeText(value: unknown): string {
  return String(value ?? "")
    .replace(/\bBearer\s+\S+/gi, "Bearer [omesso]")
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, "[token omesso]")
    .replace(/((?:access_token|refresh_token|id_token|api[_-]?key|authorization|secret|password|sig)\s*["']?\s*[:=]\s*["']?)[^\s"',;&}]+/gi, "$1[omesso]")
    .replace(/https?:\/\/[^\s<>"']+/gi, (text) => {
      try {
        const url = new URL(text);
        url.username = "";
        url.password = "";
        url.search = "";
        url.hash = "";
        return url.href;
      } catch { return "[URL non valido]"; }
    });
}

function pick(value: unknown, keys: string[]): Picked {
  const record = (value !== null && typeof value === "object" ? value : {}) as Record<string, unknown>;
  return Object.fromEntries(keys
    .filter((key) => ["string", "number", "boolean"].includes(typeof record[key]))
    .map((key) => [key, typeof record[key] === "string" ? safeText(record[key]) : record[key] as Scalar]));
}

function traceReference(ref: Reference, missingId: string) {
  return {
    marker: ref.marker ? safeText(ref.marker) : ref.id == null ? missingId : `Rif. ${safeText(ref.id)}`, document: safeText(ref.title || ref.id || "Riferimento"),
    url: safeText(ref.url || ref.path), uid: null, page: null, snippet: "",
    extra: pick(ref, ["type", "knowledge_source", "knowledge_source_type", "activity_source", "tool_call", "score", "path"]),
  };
}

function phaseTimings(timeline: NonNullable<ChatResult["timeline"]>) {
  const phases = new Map<string, { phase: string; start_ms: number; end_ms: number; events: number }>();
  for (const item of timeline) {
    const entry = phases.get(item.phase);
    if (!entry) phases.set(item.phase, { phase: item.phase, start_ms: item.at_ms, end_ms: item.at_ms, events: 1 });
    else { entry.start_ms = Math.min(entry.start_ms, item.at_ms); entry.end_ms = Math.max(entry.end_ms, item.at_ms); entry.events += 1; }
  }
  return [...phases.values()].map((item) => ({ ...pick(item, ["phase", "start_ms", "end_ms", "events"]), duration_ms: item.end_ms - item.start_ms }));
}

function milestones(result: ChatResult) {
  const timeline = result.timeline || [];
  const at = (match: (title: string) => boolean) => timeline.find((item) => match(item.title))?.at_ms;
  const values = {
    first_event_ms: timeline[0]?.at_ms,
    first_tool_ms: at((title) => title.startsWith("Chiamata tool avviata")),
    retrieval_completed_ms: at((title) => title === "Retrieval completato"),
    first_token_ms: at((title) => title === "Primo token della risposta"),
    total_ms: result.elapsed_ms ?? timeline[timeline.length - 1]?.at_ms,
  };
  return Object.fromEntries(Object.entries(values).filter(([, value]) => typeof value === "number")) as Record<string, number>;
}

export function createCollector(): TraceCollector {
  let sequence = 0;
  let requests: RequestEntry[] = [];
  return {
    begin(context) {
      const id = ++sequence;
      requests.push({ id, context: pick(context, ["question", "label", "mode", "knowledge_base"]),
        started: new Date().toISOString(), status: "in_progress", parts: [] });
      return id;
    },
    finish(id, result, error) {
      const request = requests.find((item) => item.id === id);
      if (!request) return; // A new chat invalidates outstanding requests.
      request.status = error || !result ? "failed" : "completed";
      request.completed = new Date().toISOString();
      if (error || !result) {
        request.parts = [...request.parts, { type: "response_metadata", error: safeText(error?.message ?? "Risposta assente") }];
        return;
      }
      const metadata = {
        type: "response_metadata",
        ...pick(result, ["agent", "knowledge_base", "elapsed_ms"]),
        usage: pick(result.usage, ["input_tokens", "output_tokens", "total_tokens"]),
        retrieval_usage: pick(result.retrieval_usage, ["input_tokens", "output_tokens", "total_tokens"]),
        streaming: pick(result.streaming, ["mode", "warning", "usage_scope"]),
        execution: pick(result.execution, ["identity", "user_token_forwarded", "acl_requested",
          "remote_sharepoint", "synthesis_performed", "chat_deployment"]),
        activity: (result.activity || []).map((item) => ({
          ...pick(item, ["id", "type", "knowledgeSourceName", "count", "elapsedMs", "query", "status",
            "startedAt", "completedAt", "inputTokens", "outputTokens"]),
          ...(item.error ? { error: "Errore restituito dal servizio" } : {}),
        })),
        sources: (result.sources || []).map((item) => pick(item, ["name", "type", "queries", "results"])),
        timeline: (result.timeline || []).map((item) => ({
          ...pick(item, ["at_ms", "phase", "title", "level"]),
          ...(item.detail ? { detail: safeText(item.detail) } : {}),
          ...(item.private ? { sensitive: true } : {}),
        })),
        milestones: milestones(result),
        phases: phaseTimings(result.timeline || []),
        note: "Dati restituiti dalla risposta e dallo stream, non trace interna completa. Query e argomenti tool inclusi con credenziali, token e query string degli URL oscurati; decisioni ACL per documento non esposte. Durate tool misurate dall'app sugli eventi di streaming.",
      };
      request.parts = [
        metadata,
        ...(result.tool_calls || []).map((call) => ({ type: "tool_call",
          ...pick(call, ["id", "name", "server", "status", "started_ms", "completed_ms", "duration_ms"]), tool_type: safeText(call.type),
          ...(call.arguments ? { arguments: safeText(call.arguments) } : {}),
          ...(call.error ? { error: safeText(call.error) } : {}) })),
        { type: "retrieved_sources", sources: [
          ...(result.references || []).map((ref) => traceReference(ref, "Riferimento senza ID")),
          ...(result.citations || []).map((ref) => traceReference(ref, "Citazione")),
        ] },
        { type: "text", text: safeText(result.answer) },
      ];
    },
    update(id, result) {
      this.finish(id, result);
      const request = requests.find(item => item.id === id);
      if (request) { request.status = "in_progress"; request.completed = undefined; }
    },
    clear() { requests = []; },
    snapshot() {
      return { messages: requests.flatMap((request) => [
        { role: "user", name: request.context.label, parts: [{ type: "text", text: request.context.question }] },
        { role: "assistant", name: request.context.label, parts: [
          { type: "request_event", request_id: request.id, ...request.context, question: undefined,
            started: request.started, completed: request.completed, status: request.status },
          ...request.parts,
        ] },
      ]) };
    },
  };
}
