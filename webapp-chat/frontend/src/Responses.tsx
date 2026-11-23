import { Fragment, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import { CheckIcon, ClockIcon, CopyIcon, FlowIcon, GlobeIcon, LayersIcon, ShieldIcon, SparkIcon, TokenIcon, ToolIcon } from "./UiIcons";
import type { Agent, ChatResult, CitationSpan, Entry, Reference, SourceSummary, TimelineEvent, ToolCall } from "./types";

const sourceKinds: Record<string, [string, string]> = {
  azureBlob: ["blob", "Blob"], searchIndex: ["search", "Indice"], indexedSharePoint: ["sharepoint", "SharePoint"],
  remoteSharePoint: ["sharepoint", "SharePoint"], indexedOneLake: ["onelake", "OneLake"],
  web: ["web", "Web"], mcpServer: ["mcp", "MCP"],
};

function safeUrl(value: string) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}

const iconUrls = import.meta.glob<string>("./icons/*.svg", { eager: true, query: "?url", import: "default" });

export function SourceIcon({ type, name, decorative = false }: { type?: string | null; name?: string; decorative?: boolean }) {
  const [icon] = (type && sourceKinds[type]) || ["source"];
  return <img className="src-icon" src={iconUrls[`./icons/${icon}.svg`]} width="24" height="24" alt={decorative ? "" : name} title={decorative ? undefined : name} aria-hidden={decorative || undefined} />;
}

type ReferenceMap = Map<string, (Reference & { index: number }) | null>;
type AnnotationDisplay = { separator: string; citations: Reference[]; navigate: (index: number) => void };
const inlinePattern = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\[(?:ref_id:)?(\d+)\]|【(\d+):(\d+)†([^】]+)】|\[(\d+):(\d+)\|([^\]]+)\]/g;
const referenceKey = (id: string) => `id:${id}`;
const sourceKey = (message: string, index: string, source: string) => `source:${JSON.stringify([message, index, source])}`;

function annotateAnswer(text: string, spans: CitationSpan[], citations: Reference[]) {
  let separator = "\uE000";
  while (text.includes(separator)) separator += "\uE000";
  const tokens = [...text.matchAll(inlinePattern)];
  const placements: { position: number; index: number }[] = [];
  const seen = new Set<string>();
  const linked = new Set<number>();
  const splitsSurrogate = (position: number) => position > 0 && position < text.length
    && text.charCodeAt(position - 1) >= 0xD800 && text.charCodeAt(position - 1) <= 0xDBFF
    && text.charCodeAt(position) >= 0xDC00 && text.charCodeAt(position) <= 0xDFFF;
  for (const span of spans) {
    if (!Number.isInteger(span.start) || !Number.isInteger(span.end) || !Number.isInteger(span.citation_index)
      || span.start < 0 || span.start > span.end || span.end > text.length || splitsSurrogate(span.start) || splitsSurrogate(span.end)
      || !citations[span.citation_index] || (span.start === span.end && citations[span.citation_index].type !== "file_citation")) continue;
    let position = span.end;
    const lineStart = position ? text.lastIndexOf("\n", position - 1) + 1 : 0;
    const prefix = /^[ \t]*(?:#{1,6}[ \t]+|[-*•][ \t]+|\d+[.)][ \t]+)/.exec(text.slice(lineStart));
    if (prefix && position < lineStart + prefix[0].length) position = lineStart + prefix[0].length;
    for (const token of tokens) {
      if (position > token.index && position < token.index + token[0].length) position = token.index + token[0].length;
    }
    const key = `${position}:${span.citation_index}`;
    if (seen.has(key)) continue;
    seen.add(key); linked.add(span.citation_index);
    placements.push({ position, index: span.citation_index });
  }
  for (const { position, index } of placements.sort((a, b) => b.position - a.position || b.index - a.index)) {
    text = `${text.slice(0, position)}${separator}${index}${separator}${text.slice(position)}`;
  }
  return { text, separator, linked };
}

function inline(text: string, refs: ReferenceMap | null, navigate: (id: string) => void, annotations?: AnnotationDisplay): ReactNode[] {
  const pattern = new RegExp(`${inlinePattern.source}${annotations ? `|${annotations.separator}(\\d+)${annotations.separator}` : ""}`, "g");
  const result: ReactNode[] = []; let offset = 0;
  for (const match of text.matchAll(pattern)) {
    result.push(text.slice(offset, match.index));
    const key = match.index;
    if (match[1] !== undefined) result.push(<strong key={key}>{inline(match[1], refs, navigate, annotations)}</strong>);
    else if (match[2] !== undefined) result.push(<code key={key}>{match[2]}</code>);
    else if (match[3] !== undefined) {
      const url = safeUrl(match[4]);
      result.push(url ? <a key={key} href={url} target="_blank" rel="noopener noreferrer">{match[3]}</a> : match[0]);
    } else if (match[6] !== undefined || match[9] !== undefined) {
      const markerKey = match[6] !== undefined ? sourceKey(match[6], match[7], match[8]) : sourceKey(match[9], match[10], match[11]);
      const reference = refs?.get(markerKey);
      result.push(reference
        ? <button key={key} type="button" className="citation-link" aria-label={`Vai al riferimento ${match[0]}: ${reference.title || "documento"}`} onClick={() => navigate(markerKey)}>{match[0]}</button>
        : <span key={key} className="citation-unavailable" title="Il collegamento a un riferimento non è disponibile">{match[0]}</span>);
    } else if (match[12] !== undefined && annotations) {
      const index = Number(match[12]), citation = annotations.citations[index];
      result.push(citation && <button key={key} type="button" className="citation-link"
        aria-label={`Vai alla citazione C${index + 1}: ${citation.title || "documento"}`}
        onClick={() => annotations.navigate(index)}>[C{index + 1}]</button>);
    } else if (refs && match[5] !== undefined && (!annotations || match[0].startsWith("[ref_id:"))) {
      const refKey = referenceKey(match[5]), reference = refs.get(refKey);
      result.push(reference
        ? <button key={key} type="button" className="citation-link" aria-label={`Vai al riferimento ${match[5]}: ${reference.title || "documento"}`} onClick={() => navigate(refKey)}>{annotations ? match[0] : `[${match[5]}]`}</button>
        : <span key={key} className="citation-unavailable" title="Riferimento non disponibile o ambiguo" aria-label={`Riferimento ${match[5]} non disponibile o ambiguo`}>{annotations ? match[0] : `[${match[5]}]`}</span>);
    } else result.push(match[0]);
    offset = match.index + match[0].length;
  }
  result.push(text.slice(offset));
  return result;
}

function Markdown({ text, refs, navigate, annotations }: { text: string; refs: ReferenceMap | null; navigate: (id: string) => void; annotations?: AnnotationDisplay }) {
  const blocks: ReactNode[] = []; let paragraph: string[] = []; const list: { current: { type: "ul" | "ol"; items: string[] } | null } = { current: null };
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const lines = paragraph;
    blocks.push(<p key={`p${blocks.length}`}>{lines.map((line, i) => <Fragment key={i}>{i > 0 && <br />}{inline(line, refs, navigate, annotations)}</Fragment>)}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (!list.current) return;
    const { type, items } = list.current;
    const children = items.map((item, i) => <li key={i}>{inline(item, refs, navigate, annotations)}</li>);
    blocks.push(type === "ul" ? <ul key={`l${blocks.length}`}>{children}</ul> : <ol key={`l${blocks.length}`}>{children}</ol>);
    list.current = null;
  };
  for (const line of String(text || "").split("\n")) {
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    const heading = /^\s*#{1,6}\s+(.*)$/.exec(line);
    if (heading) { flushParagraph(); flushList(); blocks.push(<h3 key={`h${blocks.length}`}>{inline(heading[1], refs, navigate, annotations)}</h3>); continue; }
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line), number = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || number) {
      flushParagraph(); const type = bullet ? "ul" : "ol";
      if (list.current?.type !== type) { flushList(); list.current = { type, items: [] }; }
      const item = bullet?.[1] ?? number?.[1];
      if (item !== undefined) list.current?.items.push(item);
      continue;
    }
    flushList(); paragraph.push(line);
  }
  flushParagraph(); flushList();
  return <div className="text">{blocks}</div>;
}

function Sources({ sources, agent }: { sources?: SourceSummary[]; agent?: Agent }) {
  return <div className="sources"><span className="sources-label">Fonti interrogate</span>
    {!sources?.length ? <span className="sources-unavailable">{agent ? "Nomi delle knowledge source non disponibili nella risposta dell'agente." : "Nomi delle knowledge source non disponibili nella risposta."}</span>
      : <ul>{sources.map((s, i) => {
        const kind = (s.type && sourceKinds[s.type]?.[1]) || s.type || "?";
        const detail = s.error ? "errore" : `${s.results} risultat${s.results === 1 ? "o" : "i"}`;
        return <li key={i} className={`src ${s.error ? "err" : s.results ? "" : "empty"}`} title={`${s.name} · ${kind} · ${s.queries} query${s.error ? ` · ${s.error}` : ""}`}>
          <SourceIcon type={s.type} name={s.name} /><span className="src-name">{s.name}</span><span className="src-count">{detail}</span></li>;
      })}</ul>}
  </div>;
}

function References({ items, agent, citationCount, linkedCount, toolReferencesMissing, detailsRef, refsMap }: { items: Reference[]; agent: boolean; citationCount: number; linkedCount: number; toolReferencesMissing: boolean; detailsRef: RefObject<HTMLDetailsElement | null>; refsMap: RefObject<Map<number, HTMLLIElement>> }) {
  return <details className="references-details" ref={detailsRef} open={items.length > 0 && items.length <= 3}>
    <summary>{agent ? "Citazioni e riferimenti" : "Riferimenti"} ({items.length})</summary>
    {agent && linkedCount < citationCount && <p className="reference-note">Le citazioni senza posizioni valide restano nell'elenco senza collegamento al testo.</p>}
    {agent && items.length > citationCount && <p className="reference-note">I riferimenti sono restituiti dal tool: non tutti sono necessariamente citati nella risposta.</p>}
    {toolReferencesMissing && <p className="reference-note">Il tool non ha esposto riferimenti utilizzabili per questa risposta.</p>}
    {!items.length ? <p>{agent ? "Nessuna citazione o riferimento disponibile nella risposta." : "Nessun documento restituito."}</p> : <ul className="refs reference-list">
      {items.map((item, index) => {
        const url = item.url && safeUrl(item.url);
        const title = item.title || item.id || item.url || "Titolo non disponibile";
        return <li key={index} ref={(node) => { if (node) refsMap.current.set(index, node); else refsMap.current.delete(index); }} className="reference-card" tabIndex={-1}>
          <div className="reference-top"><span className="reference-id">{agent && index < citationCount ? `Citazione C${index + 1}` : item.id == null ? "ID non disponibile" : `Rif. ${item.id}`}</span>
            {item.marker && <span className="reference-kind">{item.marker}</span>}
            {item.knowledge_source ? <span className="reference-source"><SourceIcon type={item.knowledge_source_type || item.type} name={item.knowledge_source} />{item.knowledge_source}</span> : !agent && <span className="reference-source">Fonte non disponibile</span>}
            {item.tool_call != null && <span className="reference-kind">chiamata {item.tool_call}</span>}
            {item.type && <span className="reference-kind">{item.type}</span>}</div>
          <div className="reference-title">{url ? <a href={url} target="_blank" rel="noopener noreferrer">{title} <span className="reference-external">↗ <span className="sr-only">(si apre in una nuova scheda)</span></span></a> : <strong>{title}</strong>}</div>
          {item.path && !url && <div className="reference-path">{(item.knowledge_source_type || item.type || "").includes("SharePoint") ? "Raccolta SharePoint" : "Percorso"}: {item.path}</div>}
          {typeof item.score === "number" && <div className="reference-path">Pertinenza (reranker semantico): {item.score.toLocaleString("it-IT", { maximumFractionDigits: 2 })} / 4</div>}
          {item.mcp_tool && <div className="reference-path reference-mcp">Chiamata server MCP: <code>{item.mcp_tool}</code>
            {item.mcp_arguments && Object.keys(item.mcp_arguments).length > 0 && <> ({Object.entries(item.mcp_arguments).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ")})</>}
            {(typeof item.result_count === "number" || typeof item.elapsed_ms === "number") && <> · {[typeof item.result_count === "number" ? `${item.result_count} risultati` : null, typeof item.elapsed_ms === "number" ? `${item.elapsed_ms} ms` : null].filter(Boolean).join(" · ")}</>}</div>}
        </li>;
      })}
    </ul>}
  </details>;
}

const ints = new Intl.NumberFormat("it-IT", { useGrouping: true });
const seconds = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 2 });
const available = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const number = (v: unknown) => available(v) ? ints.format(v) : "Non disponibili";
const names: Record<string, string> = { azure_ai_search_call: "Azure AI Search", web_search_call: "Ricerca web", file_search_call: "Ricerca file", mcp_call: "MCP", function_call: "Funzione" };
const icons: Record<string, string> = { azure_ai_search_call: "searchIndex", web_search_call: "web", file_search_call: "searchIndex", mcp_call: "mcpServer" };
const statusNames: Record<string, string> = { completed: "Completata", failed: "Errore", cancelled: "Annullata", in_progress: "In corso", calling: "In corso", searching: "In corso", incomplete: "Incompleta" };
const failed = (call: ToolCall) => !!call.error || call.status === "failed";
function toolName(t: ToolCall) {
  return [...new Set([names[t.type] || t.type || "Tipo non esposto", t.server, t.name].filter((v, i) => v && (i === 0 || v !== t.type?.replace(/_call$/, ""))))].join(" · ");
}

function AgentMetrics({ result }: { result: ChatResult }) {
  const calls = Array.isArray(result.tool_calls) ? result.tool_calls : null, usage = result.usage || {};
  const groups = new Map<string, { call: ToolCall; count: number; errors: number }>();
  for (const call of calls || []) {
    const key = JSON.stringify([call.type, call.server, call.name]);
    const group = groups.get(key) || { call, count: 0, errors: 0 };
    group.count++; if (failed(call)) group.errors++; groups.set(key, group);
  }
  const hasTokenSplit = available(usage.input_tokens) && available(usage.output_tokens) && usage.input_tokens + usage.output_tokens > 0;
  const inputPct = hasTokenSplit ? Math.round((usage.input_tokens! / (usage.input_tokens! + usage.output_tokens!)) * 100) : 0;
  return <>
    <dl className="agent-metrics" aria-label="Metriche di questa risposta">
      <div className="agent-metric metric-calls"><dt><ToolIcon />Chiamate tool</dt><dd>{number(calls?.length)}</dd></div>
      <div className="agent-metric metric-time"><dt><ClockIcon />Tempo totale</dt><dd>{available(result.elapsed_ms) ? `${seconds.format(result.elapsed_ms / 1000)} s` : "Non disponibile"}</dd><dd className="metric-detail">{available(result.elapsed_ms) ? `${ints.format(result.elapsed_ms)} ms` : "Tempo non restituito"}</dd></div>
      <div className="agent-metric metric-tokens"><dt><TokenIcon />Token totali</dt><dd>{number(usage.total_tokens)}</dd></div>
    </dl>
    {([usage.input_tokens, usage.output_tokens, usage.total_tokens].some(available)) ? <div className="token-overview">
      <dl className="agent-token-breakdown" aria-label="Dettaglio token"><div><dt>Input</dt><dd>{number(usage.input_tokens)}</dd></div><div><dt>Output</dt><dd>{number(usage.output_tokens)}</dd></div></dl>
      {hasTokenSplit && <div className="token-meter" aria-hidden="true"><span style={{ width: `${inputPct}%` }} /></div>}
    </div> : <p className="agent-telemetry-note">Il provider non ha restituito l’uso dei token.</p>}
    {!calls ? <p className="agent-telemetry-note">Informazioni sulle chiamate tool non disponibili.</p> : !calls.length ? <p className="agent-telemetry-note">Nessun tool invocato in questa risposta.</p>
      : <div className="agent-tool-summary"><p>Tool invocati <span className="meta">in questa risposta</span></p><ul>{[...groups.values()].map(({ call, count, errors }, i) => <li key={i} className={errors ? "tool-has-error" : ""}><SourceIcon type={icons[call.type]} decorative /><span className="agent-tool-name">{toolName(call)}</span><span className="agent-tool-count">{ints.format(count)} {count === 1 ? "chiamata" : "chiamate"}</span>{errors > 0 && <span className="tool-error-count">{ints.format(errors)} {errors === 1 ? "errore" : "errori"}</span>}</li>)}</ul></div>}
  </>;
}

function AgentCalls({ calls }: { calls?: ToolCall[] }) {
  if (!calls?.length) return <p>{calls ? "Nessun tool invocato in questa risposta." : "Informazioni sulle chiamate non disponibili."}</p>;
  return <ol className="agent-tool-calls">{calls.map((tool, i) => <li key={i} className={failed(tool) ? "failed" : ""}>
    <div className="tool-call-head"><span className="tool-call-number">{ints.format(i + 1)}</span><SourceIcon type={icons[tool.type]} decorative /><strong>{toolName(tool)}</strong><span className="tool-call-status">{failed(tool) ? "Errore" : (tool.status && statusNames[tool.status]) || tool.status || "Stato non esposto"}</span></div>
    <dl className="tool-call-data">{[["Tipo", tool.type], ["Server", tool.server], ["Nome", tool.name], ["Argomenti", tool.arguments], ["Errore", tool.error]].filter(([, value]) => value != null && value !== "").map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>
  </li>)}</ol>;
}

const phaseNames: Record<string, string> = { retrieval: "Retrieval", generation: "Generazione", agent: "Agente", tool: "Tool", done: "Esito" };
function Timeline({ events, loading }: { events?: TimelineEvent[]; loading: boolean }) {
  if (!events?.length) return null;
  return <details className="timeline" open={loading}>
    <summary>Eventi in tempo reale ({ints.format(events.length)})</summary>
    <ol>{events.map((item, i) => <li key={i} className={`timeline-item level-${item.level || "info"}`}>
      <span className="timeline-time">+{seconds.format(item.at_ms / 1000)} s</span>
      <span className={`timeline-phase phase-${item.phase}`}>{phaseNames[item.phase] || item.phase}</span>
      <span className="timeline-text"><strong>{item.title}</strong>{item.detail && <span className="timeline-detail">{item.detail}</span>}</span>
    </li>)}</ol>
  </details>;
}

function Loading({ agent, status, hasText }: { agent?: Agent; status?: string; hasText?: boolean }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = performance.now();
    const timer = setInterval(() => setElapsed((performance.now() - started) / 1000), 100);
    return () => clearInterval(timer);
  }, []);
  return <div className="loading-state">
    <p className="meta loading-status"><span className="pulse-dot" aria-hidden="true" />{status || (agent ? "Esecuzione agente e tool…" : "Pianificazione query e ricerca…")} <span className="elapsed">{elapsed.toFixed(1)} s</span></p>
    {!hasText && <div className="skeleton-lines" aria-hidden="true"><span /><span /><span /></div>}
  </div>;
}

export function AnswerCard({ entry, question, onInfographic }: { entry: Entry; question: string; onInfographic: (question: string, result: ChatResult, agent?: Agent) => void }) {
  const { agent, useAcl, result, error, loading } = entry;
  const citations = result?.citations || [];
  const items = agent ? [...citations, ...(result?.references || [])] : result?.references || [];
  const detailsRef = useRef<HTMLDetailsElement | null>(null), nodes = useRef(new Map<number, HTMLLIElement>());
  const [copied, setCopied] = useState(false);
  const refs = useMemo(() => {
    if (!result || (agent && !result.references?.length)) return null;
    const groups: ReferenceMap = new Map();
    (result.references || []).forEach((item, index) => {
      const reference = { ...item, index: index + (agent ? citations.length : 0) };
      const add = (key: string) => groups.set(key, groups.has(key) ? null : reference);
      if (item.id != null) {
        add(referenceKey(String(item.id)));
      }
      const marker = item.marker?.match(/^【(\d+):(\d+)†([^】]+)】$/);
      if (marker) add(sourceKey(marker[1], marker[2], marker[3]));
    });
    return groups;
  }, [result, agent]);
  const annotated = useMemo(() => annotateAnswer(result?.answer || "(nessuna risposta)", agent ? result?.citation_spans || [] : [], citations), [result, agent]);
  const id = useId();
  const navigateIndex = (index: number) => {
    const target = nodes.current.get(index);
    if (!target || !detailsRef.current) return;
    detailsRef.current.open = true;
    target.focus({ preventScroll: true });
    target.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  };
  const navigate = (refId: string) => {
    const index = refs?.get(refId)?.index;
    if (index !== undefined) navigateIndex(index);
  };
  const copyAnswer = () => {
    if (!result?.answer || !navigator.clipboard) return;
    navigator.clipboard.writeText(result.answer).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    }).catch(() => {});
  };
  const badge = agent
    ? <span className={`badge ${agent.kind === "foundryiq" ? "foundryiq" : "classic"}`}>{agent.kind === "foundryiq" ? <SparkIcon /> : <LayersIcon />}{agent.kind === "foundryiq" ? "Foundry IQ" : "RAG classico"}</span>
    : <span className={`badge ${useAcl ? "acl" : "noacl"}`}>{useAcl ? <ShieldIcon /> : <GlobeIcon />}{useAcl ? "Con ACL" : "Senza ACL"}</span>;
  return <div className={`answer ${loading ? "loading" : error ? "error" : ""}`} aria-labelledby={`answer-${id}`}>
    <div className={`head ${agent ? "agent-head" : ""}`} id={`answer-${id}`}>
      <div className="head-main">{badge}{agent && <strong>{agent.label}</strong>}</div>
      <div className="head-side">
        {result && !agent && <span className="meta">{result.knowledge_base} · {items.length} riferimenti{result.elapsed_ms != null && ` · ${result.elapsed_ms} ms`}</span>}
        {result?.answer && <button type="button" className={`icon-button copy-btn ${copied ? "copied" : ""}`} aria-label={copied ? "Risposta copiata" : "Copia risposta"} title={copied ? "Copiato" : "Copia risposta"} onClick={copyAnswer}>{copied ? <CheckIcon /> : <CopyIcon />}</button>}
      </div>
    </div>
    {loading && <Loading agent={agent} status={result?.timeline?.at(-1)?.title || entry.status} hasText={!!result?.answer} />}
    {error && <p className="answer-error-msg">{error}</p>}
    {result && <>
      {result.streaming?.warning && <p className="warning" role="status">{result.streaming.warning} {result.streaming.usage_scope}</p>}
      <Timeline events={result.timeline} loading={loading} />
      {agent && <><AgentMetrics result={result} /><p className="agent-desc"><strong>Configurazione:</strong> {agent.description}</p></>}
      {(!loading || result.answer) && <Markdown text={annotated.text} refs={refs} navigate={navigate} annotations={agent ? { separator: annotated.separator, citations, navigate: navigateIndex } : undefined} />}
      {(!loading || result.sources) && (!agent || agent.kind === "foundryiq") && <Sources sources={result.sources} agent={agent} />}
      {agent && <details><summary>Dettaglio delle chiamate ({Array.isArray(result.tool_calls) ? result.tool_calls.length : "non disponibile"})</summary><p className="meta">ID agente: <code>{result.agent}</code></p><AgentCalls calls={result.tool_calls} /></details>}
      <References items={items} agent={!!agent} citationCount={agent ? citations.length : 0} linkedCount={annotated.linked.size}
        toolReferencesMissing={!!agent && !result.references?.length && !!result.tool_calls?.some((tool) => tool.type === "mcp_call" && tool.name === "knowledge_base_retrieve")}
        detailsRef={detailsRef} refsMap={nodes} />
      {!agent && <details open={loading}><summary>Attività Foundry IQ</summary>
        <ol>{(result.activity || []).map((activity, index) => <li key={activity.id ?? index}>
          <strong>{activity.knowledgeSourceName || activity.type}</strong>: {activity.status === "in_progress" ? "In corso" : activity.error ? "Errore" : "Completata"}
          {activity.count != null && ` · ${activity.count} risultati`}
          {activity.elapsedMs != null && ` · ${activity.elapsedMs} ms`}
          {activity.inputTokens != null && ` · ${activity.inputTokens} token input`}
          {activity.outputTokens != null && ` · ${activity.outputTokens} token output`}
        </li>)}</ol>
        <p className="meta">Token retrieval Foundry IQ: {number(result.retrieval_usage?.total_tokens)} · Token generazione risposta: {number(result.usage?.total_tokens)}. Valori comunicati dal servizio, non stime.</p>
        <pre>{JSON.stringify(result.activity, null, 2)}</pre>
      </details>}
      {agent && <p className="meta">I token dell’agente non includono necessariamente il lavoro interno della knowledge base. Le attività interne non esposte dal tool non sono ricostruite.</p>}
      <div className="answer-footer">
        <button type="button" className="secondary infographic-open" aria-haspopup="dialog" onClick={() => onInfographic(question, result, agent)}><FlowIcon />Infografica del flusso e ACL</button>
      </div>
    </>}
  </div>;
}
