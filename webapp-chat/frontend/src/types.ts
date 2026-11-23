export type Mode = "noacl" | "acl" | "compare" | "agents";
export type Demo = "multi-fonte" | "acl" | "agenti";
export interface Example {
  label: string;
  question: string;
  sources?: string[];
  expected_acl?: string[];
}
export interface KnowledgeBase {
  id: string;
  name: string;
  label: string;
  examples: (Example | string)[];
  tests?: Example[];
}
export interface Agent {
  name: string;
  label: string;
  kind: string;
  description: string;
}
export interface UserProfile {
  display_name?: string | null;
  username?: string | null;
  object_id?: string | null;
  tenant_id?: string | null;
  source?: string | null;
  groups: string[];
  groups_status: "available" | "overage" | "unavailable";
  roles: string[];
  token_expires_on?: number | null;
}
export interface GroupNamesResult {
  object_id: string | null;
  tenant_id: string | null;
  groups: string[];
  groups_status: UserProfile["groups_status"];
  names: Record<string, string>;
  status: "available" | "partial" | "unavailable";
  message: string | null;
}
export interface ChatConfig {
  user?: string | null;
  user_source?: string;
  user_token_available: boolean;
  running_on_app_service: boolean;
  user_profile?: UserProfile | null;
  knowledge_bases: KnowledgeBase[];
  agents: Agent[];
}
export interface Reference {
  id?: string | number | null;
  marker?: string | null;
  title?: string | null;
  url?: string | null;
  path?: string | null;
  type?: string | null;
  knowledge_source?: string | null;
  knowledge_source_type?: string | null;
  activity_source?: string | number | null;
  tool_call?: number | null;
  tool_call_id?: string | number | null;
  mcp_call?: ToolCall | null;
  score?: number | null;
  mcp_tool?: string | null;
  mcp_arguments?: Record<string, unknown> | null;
  elapsed_ms?: number | null;
  result_count?: number | null;
  mcp_started_at?: string | null;
  mcp_completed_at?: string | null;
  mcp_status?: string | null;
  mcp_error?: unknown;
  mcp_warning?: string | null;
}
export interface CitationSpan {
  start: number;
  end: number;
  citation_index: number;
}
export interface ToolCall {
  id?: string | number | null;
  type: string;
  name?: string | null;
  server?: string | null;
  status?: string | null;
  arguments?: string | null;
  error?: string | null;
  result_count?: number | null;
  started_ms?: number;
  completed_ms?: number;
  duration_ms?: number;
}
export interface SourceSummary {
  name: string;
  type?: string | null;
  queries: number;
  results: number;
  error?: string | null;
}
export interface Activity {
  id?: string | number;
  status?: string;
  elapsedMs?: number;
  inputTokens?: number;
  outputTokens?: number;
  startedAt?: string;
  completedAt?: string;
  started_ms?: number;
  completed_ms?: number;
  duration_ms?: number;
  knowledgeSourceName?: string;
  type?: string;
  query?: string;
  count?: number;
  error?: unknown;
  warning?: string;
  mcp_tool?: string;
  mcp_arguments?: Record<string, unknown>;
  tool_call_id?: string | number | null;
  tool_call?: number | null;
}
export interface TimelineEvent {
  at_ms: number;
  phase: string;
  title: string;
  detail?: string;
  level?: "info" | "warning" | "error";
  private?: boolean;
}
export interface ChatResult {
  streaming?: { mode?: string; warning?: string; usage_scope?: string };
  timeline?: TimelineEvent[];
  answer: string;
  agent?: string;
  knowledge_base?: string;
  references?: Reference[];
  citations?: Reference[];
  citation_spans?: CitationSpan[];
  tool_calls?: ToolCall[];
  sources?: SourceSummary[];
  activity?: Activity[];
  elapsed_ms?: number;
  usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number };
  retrieval_usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number };
  execution?: {
    identity?: string;
    user_token_forwarded?: boolean;
    acl_requested?: boolean;
    remote_sharepoint?: boolean;
    synthesis_performed?: boolean;
    chat_deployment?: string;
  };
}
export interface ChatMessage { role: "user" | "assistant"; content: string }
export interface Entry {
  agent?: Agent;
  useAcl?: boolean;
  loading: boolean;
  result?: ChatResult;
  error?: string;
  status?: string;
}
export interface Turn { id: number; question: string; entries: Entry[] }
export interface GraphicData {
  config: ChatConfig;
  mode: Mode;
  kb?: KnowledgeBase;
  agents: Agent[];
  question?: string;
  result?: ChatResult;
  agent?: Agent;
}
export interface TraceSource {
  marker: string;
  document: string;
  snippet: string;
  url?: string | null;
  uid?: string | null;
  page?: number | null;
  extra: Record<string, unknown>;
}
export interface TracePart {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  arguments?: unknown;
  status?: string;
  server?: string;
  tool_type?: string;
  error?: string;
  started_ms?: number;
  completed_ms?: number;
  duration_ms?: number;
  sources?: TraceSource[];
  header?: string;
  json?: unknown;
  trailer?: string;
  raw?: unknown;
}
export interface ParsedTrace {
  messages: { position: number; role: string; name: string | null; parts: TracePart[] }[];
  stats: { messages: number; toolCalls: number; toolResponses: number; sources: number; documents: number };
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
