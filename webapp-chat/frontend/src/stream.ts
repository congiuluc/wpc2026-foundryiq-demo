import { isRecord } from "./types";
import type { ChatResult, Reference } from "./types";

export interface StreamEvent { event: string; data: Record<string, unknown> }

export function mergeAvailable<T extends object>(earlier: T, later: T): T {
  const result = { ...earlier };
  for (const key of Object.keys(later) as (keyof T)[]) {
    const value = later[key];
    if (value !== null && value !== undefined && value !== "") result[key] = value;
  }
  return result;
}

function mergeItems<T>(
  observed: T[] | undefined, final: T[] | undefined, identity: (item: T) => string | null,
  merge: (earlier: T, later: T) => T,
): T[] | undefined {
  if (!final) return observed;
  if (!observed?.length) return final;
  const result = [...final];
  for (const item of observed) {
    const key = identity(item);
    if (key === null) {
      if (!final.length) result.push(item);
      continue;
    }
    const matches = final.flatMap((candidate, index) => identity(candidate) === key ? [index] : []);
    if (matches.length === 1 && observed.filter(candidate => identity(candidate) === key).length === 1) {
      result[matches[0]] = merge(item, result[matches[0]]);
    } else if (!matches.length) result.push(item);
  }
  return result;
}

function callScope(agent: boolean, callId: string | number | null | undefined, callNumber: number | null | undefined): unknown {
  if (callId != null && callId !== "") return ["call", callId];
  if (typeof callNumber === "number" && Number.isInteger(callNumber) && callNumber > 0) return ["number", callNumber];
  return agent ? null : ["retrieve"];
}

export function activityKey(
  id: string | number | null | undefined, callId: string | number | null | undefined,
  callNumber: number | null | undefined, agent: boolean,
): string | null {
  const scope = callScope(agent, callId, callNumber);
  return id == null || id === "" || scope == null ? null : JSON.stringify([scope, String(id)]);
}

function referenceId(reference: Reference, agent: boolean): string | null {
  const scope = callScope(agent, reference.tool_call_id, reference.tool_call);
  const referencePart = reference.id != null && reference.id !== "" ? ["id", reference.id] : reference.marker ? ["marker", reference.marker] : null;
  return referencePart && scope != null ? JSON.stringify([scope, reference.activity_source ?? null, referencePart]) : null;
}

export function mergeStreamResult(observed: ChatResult, final: ChatResult): ChatResult {
  const agent = !!(final.agent || observed.agent);
  return {
    ...observed, ...final,
    tool_calls: mergeItems(observed.tool_calls, final.tool_calls,
      item => item.id != null && item.id !== "" ? JSON.stringify(item.id) : null, mergeAvailable),
    activity: mergeItems(observed.activity, final.activity,
      item => activityKey(item.id, item.tool_call_id, item.tool_call, agent), mergeAvailable),
    references: mergeItems(observed.references, final.references, item => referenceId(item, agent), (earlier, later) => {
      const merged = mergeAvailable(earlier, later);
      if (earlier.mcp_call && later.mcp_call) merged.mcp_call = mergeAvailable(earlier.mcp_call, later.mcp_call);
      return merged;
    }),
    citations: mergeItems(observed.citations, final.citations,
      item => item.url || item.title ? JSON.stringify([item.type ?? null, item.url || item.title]) : null, mergeAvailable),
  };
}

export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = body.getReader(), decoder = new TextDecoder();
  let buffer = "";
  const parse = (frame: string): StreamEvent | null => {
    let event = "message";
    const lines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) lines.push(line.slice(5).replace(/^ /, ""));
    }
    if (!lines.length) return null;
    const data: unknown = JSON.parse(lines.join("\n"));
    if (!isRecord(data)) throw new Error("Evento streaming non valido.");
    return { event, data };
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let index;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const event = parse(buffer.slice(0, index));
        buffer = buffer.slice(index + 2);
        if (event) yield event;
      }
      if (done) {
        if (buffer.trim()) {
          const event = parse(buffer);
          if (event) yield event;
        }
        break;
      }
    }
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}
