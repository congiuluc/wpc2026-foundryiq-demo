import { isRecord } from "../types";
import type { ParsedTrace } from "../types";

type Timing = "measured" | "observed" | "events" | "service" | "unknown" | "invalid";
type Kind = "request" | "tool" | "retrieval" | "activity" | "generation";

export interface WaterfallRow {
  kind: Kind;
  label: string;
  status?: string;
  startMs?: number;
  endMs?: number;
  durationMs?: number;
  timing: Timing;
}

export interface Waterfall {
  rows: WaterfallRow[];
  extentMs: number;
}

const milliseconds = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
const text = (value: unknown): string | undefined => typeof value === "string" && value ? value : undefined;
const records = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.filter(isRecord) : [];

function interval(startValue: unknown, endValue: unknown, durationValue: unknown): Pick<WaterfallRow, "startMs" | "endMs" | "durationMs" | "timing"> {
  const startMs = milliseconds(startValue), end = milliseconds(endValue), duration = milliseconds(durationValue);
  if ([startValue, endValue, durationValue].some((value) => value != null && milliseconds(value) === undefined)
    || (startMs !== undefined && end !== undefined && end < startMs)
    || (startMs !== undefined && end !== undefined && duration !== undefined && Math.abs(end - startMs - duration) > 1)) {
    return { startMs, timing: "invalid" };
  }
  const endMs = end ?? (startMs !== undefined && duration !== undefined ? startMs + duration : undefined);
  if (endMs !== undefined && !Number.isFinite(endMs)) return { startMs, timing: "invalid" };
  return {
    startMs, endMs,
    durationMs: startMs !== undefined && endMs !== undefined ? endMs - startMs : duration,
    timing: startMs !== undefined && endMs !== undefined ? "measured" : "unknown",
  };
}

export function buildWaterfall(message: ParsedTrace["messages"][number]): Waterfall | null {
  const request = message.parts.find((part) => part.type === "request_event");
  const metadataPart = message.parts.find((part) => part.type === "response_metadata");
  const requestData = isRecord(request?.raw) ? request.raw : {};
  const metadata = isRecord(metadataPart?.raw) ? metadataPart.raw : {};
  const calls = message.parts.filter((part) => part.type === "tool_call");
  if (!request && !metadataPart && !calls.length) return null;

  const agent = text(metadata.agent) ?? (requestData.mode === "agents" ? message.name : undefined);
  const label = agent || message.name || text(metadata.knowledge_base) || "Risposta";
  const milestones = isRecord(metadata.milestones) ? metadata.milestones : {};
  const elapsed = milliseconds(metadata.elapsed_ms), observed = milliseconds(milestones.total_ms);
  const total = elapsed ?? observed;
  const rows: WaterfallRow[] = [{
    kind: "request",
    label,
    status: text(requestData.status),
    ...(total !== undefined ? { startMs: 0, endMs: total, durationMs: total } : {}),
    timing: elapsed !== undefined ? "measured" : observed !== undefined ? "observed" : "unknown",
  }];
  const children: WaterfallRow[] = [];

  for (const phase of records(metadata.phases)) {
    if (phase.phase !== "retrieval" && phase.phase !== "generation") continue;
    const start = milliseconds(phase.start_ms), end = milliseconds(phase.end_ms);
    const hasInterval = typeof phase.events === "number" ? phase.events > 1 : start !== end;
    const timing = interval(phase.start_ms, hasInterval ? phase.end_ms : undefined, undefined);
    children.push({
      kind: phase.phase,
      label: phase.phase === "retrieval" ? "Retrieval" : "Generazione",
      ...timing,
      timing: timing.timing === "invalid" ? "invalid" : "events",
    });
  }
  for (const item of records(metadata.activity)) {
    const name = text(item.knowledgeSourceName), type = text(item.type);
    const timing = interval(item.started_ms, item.completed_ms, item.duration_ms);
    const serviceDuration = milliseconds(item.elapsedMs);
    children.push({
      kind: "activity",
      label: name && type ? `${name} (${type})` : name || type || "Attività Foundry IQ",
      status: item.error ? "failed" : text(item.status),
      ...timing,
      ...(timing.timing === "unknown" && timing.durationMs === undefined && serviceDuration !== undefined
        ? { durationMs: serviceDuration, timing: "service" as const } : {}),
    });
  }
  for (const call of calls) {
    const name = call.name || "tool";
    children.push({
      kind: "tool",
      label: call.server && call.server !== name ? `${name} (${call.server})` : name,
      status: call.error ? "failed" : call.status,
      ...interval(call.started_ms, call.completed_ms, call.duration_ms),
    });
  }
  children.sort((a, b) => (a.startMs ?? a.endMs ?? Infinity) - (b.startMs ?? b.endMs ?? Infinity));
  rows.push(...children);
  return { rows, extentMs: rows.reduce((max, row) => Math.max(max, row.startMs ?? 0, row.endMs ?? 0), 1) };
}
