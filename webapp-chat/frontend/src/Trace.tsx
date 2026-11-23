import { useEffect, useMemo, useRef, useState } from "react";
import { Dialog, Header, Profile } from "./Layout";
import { FolderIcon, LockIcon, SparkIcon } from "./UiIcons";
import { parseTrace } from "./trace/traceParser";
import { buildWaterfall } from "./trace/waterfall";
import { errorMessage, isRecord } from "./types";
import type { TraceSnapshot } from "./trace/currentTrace";
import type { WaterfallRow } from "./trace/waterfall";
import type { ChatConfig, ParsedTrace, TracePart, TraceSource } from "./types";
import type { ReactNode } from "react";

function Highlight({ text, term }: { text?: string | null; term: string }) {
  const value = String(text ?? "");
  if (!term) return value;
  const parts: ReactNode[] = []; const lower = value.toLowerCase(); let start = 0;
  for (let i = lower.indexOf(term); i >= 0; i = lower.indexOf(term, start)) {
    parts.push(value.slice(start, i), <mark key={i}>{value.slice(i, i + term.length)}</mark>);
    start = i + term.length;
  }
  parts.push(value.slice(start)); return parts;
}
function Snippet({ text, term, raw }: { text?: string; term: string; raw: boolean }) {
  if (raw) return <pre><Highlight text={text} term={term} /></pre>;
  const lines = String(text ?? "").split("\n"), blocks: ReactNode[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]; if (!line.trim()) continue;
    if (line.trim().startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++]);
      i--;
      const header = rows.length > 1 && /^\s*\|?[\s:|-]+\|?\s*$/.test(rows[1]);
      const body = rows.filter((r) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(r)).map((r) => r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim()));
      blocks.push(<div className="table-wrap" key={i}><table><tbody>{body.map((row, j) => <tr key={j}>{row.map((cell, k) => header && j === 0 ? <th key={k}><Highlight text={cell} term={term} /></th> : <td key={k}><Highlight text={cell} term={term} /></td>)}</tr>)}</tbody></table></div>);
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*]\s+/, ""));
      i--; blocks.push(<ul key={i}>{items.map((item, n) => <li key={n}><Highlight text={item} term={term} /></li>)}</ul>);
    } else if (/^\s*-{3,}\s*$/.test(line)) blocks.push(<hr key={i} />);
    else if (/^#{1,6}\s+/.test(line)) blocks.push(<h3 key={i}><Highlight text={line.replace(/^#{1,6}\s+/, "")} term={term} /></h3>);
    else blocks.push(<p key={i}><Highlight text={line} term={term} /></p>);
  }
  return <div className="snippet">{blocks}</div>;
}
const matches = (source: TraceSource, term: string) => !term || [source.snippet, source.document, source.url, source.uid].some((value) => String(value ?? "").toLowerCase().includes(term));
type Command = { id: number; open: boolean };
function Source({ source, term, raw, showDocument = true, command }: { source: TraceSource; term: string; raw: boolean; showDocument?: boolean; command: Command }) {
  const [open, setOpen] = useState(command.open || !!term);
  useEffect(() => { setOpen(!!term); }, [term]);
  useEffect(() => { if (command.id) setOpen(command.open); }, [command]);
  return <details className="source" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><span className="type">{source.marker.replace(/†.*】/, "】")}</span><strong><Highlight text={showDocument ? source.document : `Pagina ${source.page ?? "?"}`} term={term} /></strong>
      {showDocument && source.page != null && <span className="meta"> · pagina {source.page}</span>}<span className="meta"> · {source.snippet.length.toLocaleString("it-IT")} caratteri</span></summary>
    <div className="source-meta">{source.url && <div>URL: <code>{source.url}</code></div>}{source.uid && <div>uid: <code>{source.uid}</code></div>}{Object.keys(source.extra).length > 0 && <pre>{JSON.stringify(source.extra, null, 2)}</pre>}</div>
    <Snippet text={source.snippet} term={term} raw={raw} />
  </details>;
}
function Json({ value }: { value: unknown }) { return <pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>; }
type Row = Record<string, unknown>;
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((item): item is Row => !!item && typeof item === "object") : [];
const cell = (value: unknown) => value === undefined || value === null || value === "" ? "—" : typeof value === "number" ? value.toLocaleString("it-IT") : String(value);
const milestoneLabels: Record<string, string> = { first_event_ms: "Primo evento", first_tool_ms: "Primo tool", retrieval_completed_ms: "Retrieval completato", first_token_ms: "Primo token", total_ms: "Totale" };
function Table({ caption, columns, data }: { caption: string; columns: [string, string][]; data: Row[] }) {
  if (!data.length) return null;
  return <table className="trace-table"><caption>{caption}</caption><thead><tr>{columns.map(([, label]) => <th key={label} scope="col">{label}</th>)}</tr></thead>
    <tbody>{data.map((row, i) => <tr key={i} className={row.level ? `level-${String(row.level)}` : undefined}>{columns.map(([key]) => <td key={key}>{key === "detail" && row.sensitive ? <><span className="badge">query/argomenti</span> {cell(row[key])}</> : cell(row[key])}</td>)}</tr>)}</tbody></table>;
}
function Metadata({ data }: { data: Row }) {
  const usage = [["Generazione", data.usage], ["Retrieval", data.retrieval_usage]].filter(([, value]) => value && Object.keys(value as Row).length) as [string, Row][];
  const marks = (data.milestones && typeof data.milestones === "object" ? data.milestones : {}) as Row;
  return <div className="part trace-metadata"><span className="type">Metadati della risposta</span>
    {typeof data.error === "string" && <p className="level-error">{data.error}</p>}
    {Object.keys(marks).length > 0 && <div className="stats">{Object.entries(marks).map(([key, value]) => <span className="stat" key={key}><strong>{cell(value)} ms</strong> {milestoneLabels[key] ?? key}</span>)}</div>}
    {usage.length > 0 && <p className="meta">{usage.map(([label, value]) => `Token ${label.toLowerCase()}: ${cell(value.input_tokens)} in / ${cell(value.output_tokens)} out / ${cell(value.total_tokens)} totali`).join(" · ")}</p>}
    <Table caption="Fasi (dagli eventi di streaming)" columns={[["phase", "Fase"], ["start_ms", "Inizio ms"], ["end_ms", "Fine ms"], ["duration_ms", "Durata ms"], ["events", "Eventi"]]} data={rows(data.phases)} />
    <Table caption="Attività Foundry IQ" columns={[["knowledgeSourceName", "Fonte"], ["type", "Tipo"], ["query", "Query"], ["count", "Risultati"], ["elapsedMs", "ms"], ["inputTokens", "Token in"], ["outputTokens", "Token out"], ["error", "Errore"]]} data={rows(data.activity)} />
    <Table caption="Eventi in tempo reale" columns={[["at_ms", "ms"], ["phase", "Fase"], ["title", "Evento"], ["detail", "Dettaglio"]]} data={rows(data.timeline)} />
    {typeof data.note === "string" && <p className="meta">{data.note}</p>}
    <details><summary>JSON</summary><Json value={data} /></details></div>;
}
function Part({ part, term, raw, command }: { part: TracePart; term: string; raw: boolean; command: Command }) {
  if (part.type === "text") return <div className="part"><span className="type">text</span><Snippet text={part.text} term={term} raw={raw} /></div>;
  if (part.type === "response_metadata" && part.raw && typeof part.raw === "object") return <Metadata data={part.raw as Row} />;
  if (part.type === "tool_call") return <div className="part tool-call"><div><span className="type">tool_call</span><strong>{part.name}</strong>{part.id && <span className="meta"> · {part.id}</span>}{part.duration_ms !== undefined && <span className="meta"> · {part.duration_ms.toLocaleString("it-IT")} ms{part.started_ms !== undefined ? ` (avvio a ${part.started_ms.toLocaleString("it-IT")} ms)` : ""}</span>}</div>
    {part.arguments !== undefined && <Json value={part.arguments} />}{part.status && <p className="meta">Stato: {part.status}</p>}{part.server && <p className="meta">Server: {part.server}</p>}{part.tool_type && <p className="meta">Tipo: {part.tool_type}</p>}{part.error && <p>{part.error}</p>}</div>;
  if (part.type === "tool_call_response" || part.type === "retrieved_sources") {
    const sources = part.sources || [];
    const visible = sources.filter((s) => matches(s, term));
    return <div className="part tool-response"><div><span className="type">{part.type === "retrieved_sources" ? "Riferimenti / citazioni (snippet non esposti)" : "tool_call_response"}</span>{part.id && <span className="meta">{part.id}</span>}{sources.length > 0 && <span className="meta"> · {visible.length}/{sources.length} fonti</span>}</div>
      {part.header && <p className="meta">{part.header}</p>}{visible.map((s, i) => <Source key={i} source={s} term={term} raw={raw} command={command} />)}
      {part.json !== undefined && <Json value={part.json} />}{part.trailer && <p className="meta">{part.trailer}</p>}</div>;
  }
  return <div className="part"><span className="type">{part.type}</span><Json value={part.raw} /></div>;
}

function duration(ms: number) {
  return ms >= 1000
    ? `${(ms / 1000).toLocaleString("it-IT", { maximumFractionDigits: 2 })} s`
    : `${ms.toLocaleString("it-IT", { maximumFractionDigits: 2 })} ms`;
}
function timingLabel(row: WaterfallRow) {
  if (row.timing === "invalid") return "Tempi incoerenti";
  if (row.durationMs !== undefined) {
    const qualifier = row.timing === "events" ? " tra eventi" : row.timing === "observed" ? " osservati" : row.timing === "service" ? " (servizio)" : "";
    return `${duration(row.durationMs)}${qualifier}${row.startMs === undefined ? " · inizio non disponibile" : ""}`;
  }
  if (row.startMs !== undefined) return "Durata non disponibile";
  if (row.endMs !== undefined) return "Inizio non disponibile";
  return "Tempi non disponibili";
}
function statusLabel(status?: string) {
  if (!status) return "";
  if (["completed", "succeeded"].includes(status)) return "Completata";
  if (["failed", "error", "incomplete", "cancelled", "canceled"].includes(status)) return "Errore";
  if (["in_progress", "queued", "running"].includes(status)) return "In corso all'ultimo evento";
  return `Stato: ${status}`;
}
const waterfallKinds: Record<WaterfallRow["kind"], string> = {
  request: "Richiesta", tool: "Tool", retrieval: "Fase", activity: "Attività", generation: "Fase",
};
function TraceWaterfall({ message }: { message: ParsedTrace["messages"][number] }) {
  const graph = buildWaterfall(message);
  if (!graph) return null;
  return <section className="trace-waterfall" aria-label="Grafico temporale della risposta">
    <h3>Grafico temporale</h3>
    <p className="meta">Tempi osservati dalla chat. Le fasi mostrano l'intervallo tra eventi, non gli span interni del modello o di Foundry.</p>
    <div className="waterfall-scroll" role="region" aria-label="Grafico temporale scorrevole" tabIndex={0}>
      <div className="waterfall-chart">
        <div className="waterfall-axis" aria-hidden="true"><span>0 ms</span><span>{duration(graph.extentMs)}</span></div>
        <ol className="waterfall-rows">{graph.rows.map((row, index) => {
          const point = row.startMs ?? row.endMs;
          const bar = row.startMs !== undefined && row.endMs !== undefined && row.endMs > row.startMs && row.timing !== "invalid"
            ? { left: `${row.startMs / graph.extentMs * 100}%`, width: `${(row.endMs - row.startMs) / graph.extentMs * 100}%` }
            : null;
          const status = statusLabel(row.status);
          return <li className={`waterfall-row waterfall-${row.kind}`} data-kind={row.kind} key={index}>
            <div className="waterfall-label"><span className="waterfall-type">{waterfallKinds[row.kind]}</span><strong>{row.label}</strong>
              {status && <span className={`waterfall-state${status === "Errore" ? " is-error" : ""}`}>{status}</span>}</div>
            <div className="waterfall-track" aria-hidden="true">{bar
              ? <span className="waterfall-bar" style={bar} />
              : point !== undefined && row.timing !== "invalid" && <span className="waterfall-point" style={{ left: `${point / graph.extentMs * 100}%` }} />}</div>
            <div className="waterfall-time"><strong>{timingLabel(row)}</strong>
              {row.startMs !== undefined && <span>avvio +{duration(row.startMs)}</span>}
              {row.startMs === undefined && row.endMs !== undefined && <span>fine +{duration(row.endMs)}</span>}</div>
          </li>;
        })}</ol>
      </div>
    </div>
  </section>;
}

function TraceResults({ trace }: { trace: ParsedTrace }) {
  const [mode, setMode] = useState<"timeline" | "documents">("timeline"), [filter, setFilter] = useState(""), [raw, setRaw] = useState(false), [command, setCommand] = useState({ id: 0, open: false });
  const view = useRef<HTMLDivElement | null>(null);
  const term = filter.trim().toLowerCase(), groups = new Map<string, { document: string; url?: string | null; sources: TraceSource[] }>();
  for (const message of trace.messages) for (const part of message.parts) for (const source of part.sources || []) {
    if (!matches(source, term)) continue;
    const key = source.url || source.document;
    if (!groups.has(key)) groups.set(key, { document: source.document, url: source.url, sources: [] });
    groups.get(key)?.sources.push(source);
  }
  return <><section className="controls trace-controls"><div className="stats">{[["messaggi", trace.stats.messages], ["tool call", trace.stats.toolCalls], ["risposte tool", trace.stats.toolResponses], ["fonti", trace.stats.sources], ["documenti", trace.stats.documents]].map(([label, value]) => <span className="stat" key={label}><strong>{value}</strong> {label}</span>)}</div>
    <fieldset aria-label="Vista della traccia"><legend>Vista</legend><div className="segmented">{([["timeline", "Cronologia"], ["documents", "Per documento"]] as const).map(([value, label]) => <label key={value}><input type="radio" name="view" value={value} checked={mode === value} onChange={() => setMode(value)} /> {label}</label>)}</div></fieldset>
    <label>Filtro <input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="testo nelle fonti…" /></label><label className="check"><input type="checkbox" checked={raw} onChange={(e) => setRaw(e.target.checked)} /> Testo grezzo</label><span className="spacer" />
    <button type="button" className="secondary" onClick={() => setCommand((previous) => ({ id: previous.id + 1, open: [...(view.current?.querySelectorAll<HTMLDetailsElement>("details.source") || [])].some((item) => !item.open) }))}>{command.open ? "Comprimi tutto" : "Espandi tutto"}</button></section>
    <div id="trace-view" ref={view}>{mode === "timeline" ? trace.messages.map((message, i) => <article key={i} className={`trace-message role-${message.role}`}><div className="message-head"><span className="badge role">{message.role}</span>{message.name && <span className="meta">{message.name}</span>}<span className="meta">#{message.position + 1}</span></div><TraceWaterfall message={message} />{message.parts.map((part, j) => <Part key={j} part={part} term={term} raw={raw} command={command} />)}</article>)
      : !groups.size ? <p className="meta">Nessuna fonte corrisponde al filtro.</p> : [...groups.values()].sort((a, b) => b.sources.length - a.sources.length).map((group, i) => <article className="trace-message" key={i}><div className="message-head"><strong><Highlight text={group.document} term={term} /></strong><span className="meta"> · {group.sources.length} chunk</span></div>{group.url && <div className="meta"><code>{group.url}</code></div>}{group.sources.sort((a, b) => (a.page ?? 0) - (b.page ?? 0)).map((source, j) => <Source key={j} source={source} term={term} raw={raw} showDocument={false} command={command} />)}</article>)}</div></>;
}

export function CurrentTraceDialog({ open, onClose, snapshot }: { open: boolean; onClose: () => void; snapshot: TraceSnapshot }) {
  const json = useMemo(() => open ? JSON.stringify(snapshot, null, 2) : "", [open, snapshot]);
  const trace = useMemo(() => open && snapshot.messages.length ? parseTrace(json) : null, [open, snapshot.messages.length, json]);
  return <Dialog open={open} onClose={onClose} className="trace-dialog" title="Trace della chat corrente">
    {open && <div className="trace-dialog-body">
      <p className="trace-status" role="status"><span className="pulse-dot" aria-hidden="true" />{trace ? "Aggiornamento in tempo reale dalla chat corrente." : "La chat corrente non contiene ancora eventi. Invia una domanda nella chat."}</p>
      <p className="meta trace-dialog-note">Sono mostrati solo i dati restituiti alla chat, non la trace interna completa. L'analisi resta nel browser e le credenziali riconoscibili sono oscurate.</p>
      {trace && <><TraceResults trace={trace} /><details className="trace-json"><summary>JSON della trace</summary><pre>{json}</pre></details></>}
    </div>}
  </Dialog>;
}

export function Trace() {
  const [text, setText] = useState(""), [trace, setTrace] = useState<ParsedTrace | null>(null), [error, setError] = useState("");
  const following = useRef(new URLSearchParams(location.search).get("current") === "1");
  const [status, setStatus] = useState(() => following.current ? window.opener ? "Connessione alla chat corrente..." : "Chat non collegata. Per la trace corrente usa l'icona Trace nella Chat." : "");
  const [dragging, setDragging] = useState(false);
  const [config, setConfig] = useState<ChatConfig | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  useEffect(() => {
    let active = true;
    fetch("/api/config").then((r) => r.ok ? r.json() : null).then((data: ChatConfig | null) => { if (active && data) setConfig(data); }).catch(() => {});
    return () => { active = false; };
  }, []);
  const load = (value: string) => {
    try { setTrace(parseTrace(value)); setError(""); }
    catch (e) { setTrace(null); setError(errorMessage(e)); }
  };
  const stop = () => { following.current = false; setStatus(""); };
  useEffect(() => {
    const message = (event: MessageEvent<unknown>) => {
      if (!following.current || event.origin !== location.origin || event.source !== window.opener || !isRecord(event.data)) return;
      if (event.data.type === "wpc-trace-ended") { following.current = false; setStatus("Chat chiusa o ricaricata: resta l'ultima istantanea. Usa l'icona Trace nella Chat per vedere la sessione corrente."); return; }
      if (event.data.type !== "wpc-current-trace" || !isRecord(event.data.trace) || !Array.isArray(event.data.trace.messages)) return;
      const value = JSON.stringify(event.data.trace, null, 2); setText(value); load(value);
      setStatus(event.data.trace.messages.length ? "Trace corrente: eventi delle risposte ricevute, aggiornata mentre la chat resta aperta. Nessuna credenziale trasferita." : "La chat corrente non contiene ancora eventi. Invia una domanda nella scheda Chat.");
    };
    window.addEventListener("message", message);
    if (following.current) window.opener?.postMessage({ type: "wpc-trace-ready" }, location.origin);
    return () => window.removeEventListener("message", message);
  }, []);
  const file = async (item?: File) => { if (!item) return; stop(); try { const value = await item.text(); setText(value); load(value); } catch (e) { setError(`Impossibile leggere il file: ${errorMessage(e)}`); } };
  return <><a className="skip-link" href="#trace-text">Vai alla traccia</a><Header page="trace" user={config?.user} source={config?.user_source} tokenOk={config?.user_token_available} onProfile={() => setProfileOpen(true)} />
    <div className="page container"><section className="trace-hero"><span className="eyebrow"><span className="dot" />Analisi locale nel browser</span><h2>Cosa ha recuperato <span className="grad">l'agente</span>?</h2><p>Incolla o trascina una traccia per esplorare fonti, pagine, tool call e snippet restituiti da Foundry IQ.</p></section>
      <section className={`trace-input ${dragging ? "dragover" : ""}`} onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(e) => { e.preventDefault(); setDragging(false); file(e.dataTransfer.files[0]); }}>
        {status && <p className="trace-status" role="status"><span className="pulse-dot" aria-hidden="true" />{status}</p>}
        <textarea id="trace-text" name="trace" aria-label="Traccia JSON" autoComplete="off" rows={7} spellCheck="false" value={text} onChange={(e) => { stop(); setText(e.target.value); }} onPaste={(e) => { const field = e.currentTarget; const pasted = e.clipboardData.getData("text"); const value = field.value.slice(0, field.selectionStart) + pasted + field.value.slice(field.selectionEnd); stop(); setText(value); load(value); e.preventDefault(); }} placeholder="Incolla una traccia JSON: un messaggio {role, parts}, un array di messaggi, un oggetto con 'messages' oppure JSON Lines. Puoi anche trascinare qui un file." />
        <div className="trace-actions"><button type="button" onClick={() => { stop(); load(text); }}><SparkIcon />Visualizza</button><label className="file-button"><FolderIcon />Apri file…<input className="sr-only" type="file" accept=".json,.jsonl,.txt" onChange={(e) => file(e.target.files?.[0])} /></label>
          <button type="button" className="secondary" onClick={() => { stop(); setText(""); setTrace(null); setError(""); }}>Pulisci</button><span className="meta trace-privacy"><LockIcon />La traccia resta nel browser e non viene inviata al server.</span></div></section>
      {error && <div className="warning" role="status">{error}</div>}
      {trace && <main className="trace-results"><TraceResults trace={trace} /></main>}
    </div><Profile config={config} open={profileOpen} onClose={() => setProfileOpen(false)} /></>;
}
