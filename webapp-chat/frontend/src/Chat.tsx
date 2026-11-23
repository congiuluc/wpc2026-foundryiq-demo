import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Header, Profile } from "./Layout";
import { AnswerCard, SourceIcon } from "./Responses";
import { Infographic } from "./Infographic";
import { CurrentTraceDialog } from "./Trace";
import { ActivityIcon, ArrowRightIcon, BotIcon, ChevronLeftIcon, ChevronRightIcon, CloseIcon, CompareIcon, FlaskIcon, FlowIcon, GlobeIcon, LayersIcon, SendIcon, ShieldIcon, SparkIcon } from "./UiIcons";
import { demoRoutes, isMode } from "./routes";
import { createCollector } from "./trace/currentTrace";
import { activityKey, mergeAvailable, mergeStreamResult, readEvents } from "./stream";
import { errorMessage, isRecord } from "./types";
import type { Agent, ChatConfig, ChatMessage, ChatResult, Demo, Entry, Example, GraphicData, KnowledgeBase, Mode, Turn } from "./types";

const hints: Record<Mode, string> = {
  noacl: "Identità dell'app (senza filtro ACL documentale; SharePoint remoto usa i permessi utente).",
  acl: "Token Search dell'utente attivo: il servizio filtra i documenti secondo i permessi ACL.",
  compare: "Due risposte in parallelo sulla stessa domanda: con filtro ACL utente e senza.",
  agents: "Confronto parallelo tra agenti con identità del progetto (senza ACL utente).",
};
const modeIcons: Record<Mode, ReactNode> = {
  noacl: <GlobeIcon />,
  acl: <ShieldIcon />,
  compare: <CompareIcon />,
  agents: <BotIcon />,
};
const modes: [Mode, string][] = [["noacl", "Senza ACL"], ["acl", "Con ACL"], ["compare", "Confronto ACL"], ["agents", "Agenti RAG vs IQ"]];
const sourceTypes: Record<string, string> = { Manuali: "azureBlob", Ricambi: "mcpServer", SharePoint: "remoteSharePoint", Web: "web" };

async function request(path: string, body: object, signal: AbortSignal, onUpdate: (result: ChatResult, stage?: string) => void, retried = false): Promise<ChatResult> {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, stream: true }), signal });
  if (response.ok && response.headers.get("content-type")?.includes("text/event-stream")) {
    if (!response.body) throw new Error("Stream della risposta assente.");
    let result: ChatResult = { answer: "" };
    let completed: ChatResult | undefined;
    for await (const { event, data } of readEvents(response.body)) {
      if (event === "error") throw new Error(typeof data.message === "string" ? data.message : "Errore nello stream.");
      if (event === "completed") {
        if (typeof data.answer !== "string") throw new Error("Risposta finale non valida.");
        completed = mergeStreamResult(result, { ...result, ...data, answer: data.answer });
        continue;
      }
      if (event === "status" && isRecord(data.result)) {
        if (isRecord(data.result.streaming) && data.result.streaming.mode === "json_retry") {
          result = { ...result, tool_calls: undefined, usage: undefined, references: undefined, citations: undefined,
            citation_spans: undefined, activity: undefined, sources: undefined, retrieval_usage: undefined };
        }
        result = mergeStreamResult(result, { ...result, ...data.result });
      }
      if (event === "log" && typeof data.title === "string" && typeof data.at_ms === "number") {
        result = { ...result, timeline: [...(result.timeline || []), { ...data, title: data.title, at_ms: data.at_ms, phase: String(data.phase ?? "") }] };
      }
      if (event === "references" && Array.isArray(data.items)) {
        result = { ...result, references: data.append ? [...(result.references || []), ...data.items] : data.items };
      }
      if (event === "citation" && typeof data.title === "string") {
        const citations = result.citations || [];
        if (!citations.some(item => item.title === data.title && item.url === data.url)) result = { ...result, citations: [...citations, data] };
      }
      if (event === "delta" && typeof data.text === "string") result = { ...result, answer: result.answer + data.text };
      if (event === "usage") result = { ...result, usage: data };
      if (event === "activity") {
        const activity = [...(result.activity || [])];
        const id = typeof data.id === "string" || typeof data.id === "number" ? data.id : undefined;
        const callId = typeof data.tool_call_id === "string" || typeof data.tool_call_id === "number" ? data.tool_call_id : undefined;
        const callNumber = typeof data.tool_call === "number" ? data.tool_call : undefined;
        const agent = path === "/api/agent-chat";
        const key = activityKey(id, callId, callNumber, agent);
        const index = key == null ? -1 : activity.findIndex(item => item.id === id
          && activityKey(item.id, item.tool_call_id, item.tool_call, agent) === key);
        if (index < 0) activity.push(data); else activity[index] = mergeAvailable(activity[index], data);
        result = { ...result, activity };
      }
      if (event === "tool" && typeof data.type === "string") {
        const calls = [...(result.tool_calls || [])];
        const index = data.id == null ? -1 : calls.findIndex(item => item.id === data.id);
        const tool = { ...data, type: data.type };
        if (index < 0) calls.push(tool); else calls[index] = mergeAvailable(calls[index], tool);
        result = { ...result, tool_calls: calls };
      }
      onUpdate(result, typeof data.stage === "string" ? data.stage : undefined);
    }
    if (completed) return completed;
    throw new Error("Connessione streaming interrotta prima del completamento.");
  }
  const payload = await response.json().catch(() => ({ detail: response.statusText }));
  if (path === "/api/chat" && response.status === 401 && payload.detail === "token_expired" && !retried) {
    await fetch("/.auth/refresh");
    return request(path, body, signal, onUpdate, true);
  }
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail));
  return payload;
}

function TestPanel({ kb, items, done, open, onSelect, onClose }: { kb?: KnowledgeBase; items: Example[]; done: Set<string>; open: boolean; onSelect: (question: string) => void; onClose: () => void }) {
  const completed = items.filter((item) => done.has(item.question)).length;
  const pct = items.length ? Math.round((completed / items.length) * 100) : 0;
  return <>
    {open && <div className="tests-backdrop" aria-hidden="true" onClick={onClose} />}
    <div className={`tests-wrap ${open ? "open" : "collapsed"}`} inert={!open || undefined}>
      <aside id="tests" className="tests" aria-labelledby="tests-title" aria-hidden={!open}>
        <div className="tests-head">
          <div className="tests-title-wrap"><FlaskIcon /><h2 id="tests-title">Query di test</h2></div>
          <button type="button" className="secondary tests-hide-btn" aria-label="Comprimi sidebar query di test" title="Comprimi la sidebar dei test (Alt+T)" onClick={onClose}><span>Comprimi</span><ChevronRightIcon /></button>
        </div>
        <div className="tests-meta">
          <p className="tests-kb" title={kb?.name}>{kb?.label}</p>
          {items.length > 0 && <div className="tests-progress" aria-label={`Completate ${completed} di ${items.length}`}>
            <span>{completed}/{items.length}</span>
            <span className="tests-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>
          </div>}
        </div>
        <ol className="tests-list">{items.map((item, i) => <li key={i}>
          <button type="button" className={`test-item ${done.has(item.question) ? "done" : ""}`} title={item.question} onClick={() => onSelect(item.question)}>
            <span className="test-label">{item.label}</span>
            <span className="test-q">{item.question}</span>
            <span className="test-srcs">
              {(item.sources || []).map((s) => <span className="test-src" key={s}><SourceIcon type={sourceTypes[s]} decorative /> {s}</span>)}
              {Array.isArray(item.expected_acl) && <span className="test-acl" title={item.expected_acl.join(", ")}><ShieldIcon />ACL attesa: {item.expected_acl.length > 1 ? "Tecnici + Direzione" : `Solo ${item.expected_acl[0] === "WPC FoundryIQ ACL - Tecnici" ? "Tecnici" : "Direzione"}`}</span>}
            </span>
          </button>
        </li>)}</ol>
        <p className="tests-note">Un clic invia subito la domanda. Le etichette mostrano le fonti attese.</p>
      </aside>
    </div>
  </>;
}

export function Chat({ demo }: { demo: Demo | null }) {
  const params = new URLSearchParams(location.search);
  const route = demo ? demoRoutes[demo] : null;
  const requestedMode = params.get("mode");
  const initialMode = isMode(requestedMode) && (!demo || (demo !== "agenti" && requestedMode !== "agents")) ? requestedMode : route?.mode || "noacl";
  const [config, setConfig] = useState<ChatConfig | null>(null), [warning, setWarning] = useState("");
  const [mode, setMode] = useState<Mode>(initialMode);
  const [kbId, setKbId] = useState(route?.kb || params.get("kb") || "");
  const [agents, setAgents] = useState<string[]>([]), [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]), [busy, setBusy] = useState(false), [done, setDone] = useState(() => new Set<string>());
  const [testsOpen, setTestsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false), [traceOpen, setTraceOpen] = useState(false), [graphic, setGraphic] = useState<GraphicData | null>(null);
  const history = useRef<ChatMessage[]>([]), agentHistory = useRef<Record<string, ChatMessage[]>>({}), generation = useRef(0), locked = useRef(false), nextTurn = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const [collector] = useState(createCollector);
  const [traceSnapshot, setTraceSnapshot] = useState(() => collector.snapshot());
  const input = useRef<HTMLTextAreaElement | null>(null), log = useRef<HTMLElement | null>(null), scrollTarget = useRef<number | null>(null);
  const publish = useCallback(() => setTraceSnapshot(collector.snapshot()), [collector]);
  useEffect(() => {
    let active = true;
    fetch("/api/config").then((response) => {
      if (!response.ok) throw new Error(`Configurazione non disponibile (${response.status}): ricarica la pagina o accedi di nuovo.`);
      return response.json();
    }).then((data: ChatConfig) => {
      if (!active) return;
      setConfig(data); setAgents((data.agents || []).map((a) => a.name));
      setKbId((id) => route ? route.kb : data.knowledge_bases.some((kb) => kb.id === id) ? id : data.knowledge_bases[0]?.id || "");
      if (route && demo !== "agenti" && !data.knowledge_bases.some((kb) => kb.id === route.kb)) {
        setWarning("Knowledge base della demo non disponibile nella configurazione.");
        return;
      }
      if (!data.user_token_available) setWarning(data.running_on_app_service
        ? "Token utente per Azure AI Search non disponibile: la modalità con ACL non funziona. Riesegui l'accesso oppure controlla la configurazione."
        : "Token utente non disponibile: esegui 'az login' sullo stesso tenant delle risorse e ricarica la pagina.");
    }).catch((error: unknown) => { if (active) setWarning(`Errore di configurazione: ${errorMessage(error)}`); });
    return () => { active = false; };
  }, [demo, route]);
  useEffect(() => {
    document.body.classList.toggle("tests-closed", !testsOpen);
    return () => document.body.classList.remove("tests-closed");
  }, [testsOpen]);
  const kb = config?.knowledge_bases.find((k) => k.id === (mode === "agents" ? "demo" : kbId)) || (!route ? config?.knowledge_bases[0] : undefined);
  const examples = (kb?.examples || []).map((x) => typeof x === "string" ? { label: x, question: x } : x);
  const tests = kb?.tests || examples;
  const selectedAgents = (config?.agents || []).filter((a) => agents.includes(a.name));
  const reset = () => {
    controllers.current.forEach(controller => controller.abort()); controllers.current.clear();
    generation.current++; locked.current = false; setBusy(false); history.current = []; agentHistory.current = {};
    collector.clear(); publish(); setTurns([]);
  };
  useEffect(() => () => { controllers.current.forEach(controller => controller.abort()); }, []);
  const toggleTests = useCallback(() => setTestsOpen((v) => !v), []);
  async function ask(value: string) {
    const q = value.trim();
    if (!q || locked.current || !config || (mode !== "agents" && !kb)) return;
    const chosen = mode === "agents" ? selectedAgents : null;
    if (chosen && !chosen.length) { setWarning("Seleziona almeno un agente."); return; }
    locked.current = true; setBusy(true); setQuestion("");
    if (input.current) input.current.style.height = "auto";
    setDone((current) => new Set(current).add(q));
    const generationId = generation.current, turnId = ++nextTurn.current;
    const variants: Entry[] = chosen ? chosen.map((agent) => ({ agent, loading: true })) : (mode === "compare" ? [true, false] : [mode === "acl"]).map((useAcl) => ({ useAcl, loading: true }));
    scrollTarget.current = turnId;
    setTurns((current) => [...current, { id: turnId, question: q, entries: variants.map((v) => ({ ...v, loading: true })) }]);
    const messages: ChatMessage[] = [...history.current, { role: "user", content: q }];
    const results = await Promise.all(variants.map(async (variant, index) => {
      const { agent, useAcl } = variant;
      const agentMessages: ChatMessage[] = agent ? [...(agentHistory.current[agent.name] || []), { role: "user", content: q }] : [];
      const traceId = collector.begin({ question: q, label: agent?.label || (useAcl ? "Con ACL" : "Senza ACL"), mode, knowledge_base: agent ? undefined : kbId });
      publish();
      const controller = new AbortController();
      controllers.current.add(controller);
      try {
        const result = await request(agent ? "/api/agent-chat" : "/api/chat", agent ? { agent: agent.name, messages: agentMessages } : { knowledge_base: kbId, messages, use_acl: useAcl }, controller.signal, (partial, stage) => {
          if (generation.current !== generationId) return;
          collector.update(traceId, partial); publish();
          setTurns(current => current.map(turn => turn.id !== turnId ? turn : { ...turn, entries: turn.entries.map((entry, n) => n === index ? { ...entry, result: partial, status: stage || entry.status } : entry) }));
        });
        collector.finish(traceId, result); publish();
        if (generation.current === generationId) {
          if (agent) agentHistory.current[agent.name] = agentMessages.concat({ role: "assistant", content: result.answer }).slice(-10);
          setTurns((current) => current.map((turn) => turn.id !== turnId ? turn : { ...turn, entries: turn.entries.map((entry, n) => n === index ? { ...entry, loading: false, result } : entry) }));
        }
        return result;
      } catch (error) {
        const reason = error instanceof Error ? error : new Error(errorMessage(error));
        collector.finish(traceId, null, reason); publish();
        if (generation.current === generationId) setTurns((current) => current.map((turn) => turn.id !== turnId ? turn : { ...turn, entries: turn.entries.map((entry, n) => n === index ? { ...entry, loading: false, error: reason.message } : entry) }));
        return null;
      } finally {
        controllers.current.delete(controller);
      }
    }));
    if (generation.current !== generationId) return;
    if (!chosen && results[0]) history.current = messages.concat({ role: "assistant", content: results[0].answer }).slice(-10);
    locked.current = false; setBusy(false);
    scrollToTurn(turnId);
  }
  function scrollToTurn(turnId: number) {
    log.current?.querySelector(`[data-turn="${turnId}"]`)?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }
  useEffect(() => {
    const turnId = scrollTarget.current;
    if (turnId === null) return;
    scrollTarget.current = null;
    requestAnimationFrame(() => scrollToTurn(turnId));
  }, [turns]);
  const useExample = (value: string) => { setQuestion(value); input.current?.focus(); };
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (!event.altKey || event.ctrlKey || event.metaKey || !/^Digit[1-9]$/.test(event.code)) return;
      const item = examples[Number(event.code.slice(5)) - 1];
      if (item) { event.preventDefault(); useExample(item.question); }
    };
    document.addEventListener("keydown", shortcut); return () => document.removeEventListener("keydown", shortcut);
  });
  return <><a className="skip-link" href="#question">Vai alla domanda</a>
    <Header page="chat" demo={demo} user={config?.user} source={config?.user_source} tokenOk={config?.user_token_available} onProfile={() => setProfileOpen(true)} onReset={reset} testsOpen={testsOpen} onTests={toggleTests} />
    <div className="shell"><div className="page container">
      <section className="controls">
        <div className="controls-row">
          {demo !== "agenti" && <fieldset aria-label="Modalità della chat"><legend className="sr-only">Modalità</legend><div className="segmented">
            {modes.filter(([value]) => !demo || value !== "agents").map(([value, label]) => <label key={value}><input type="radio" name="mode" value={value} checked={mode === value} onChange={() => { reset(); setMode(value); }} />{modeIcons[value]}<span>{label}</span></label>)}</div></fieldset>}
          {mode !== "agents" ? !demo ? <label id="kb-picker"><span className="sr-only">Knowledge base</span><select id="kb" aria-label="Knowledge base" value={kbId} onChange={(e) => { reset(); setKbId(e.target.value); }}>{(config?.knowledge_bases || []).map((item) => <option key={item.id} value={item.id} title={item.name}>{item.label}</option>)}</select></label> : <p className="kb-pill"><span>{kb?.label || "Non disponibile"}</span></p>
            : <fieldset id="agents-picker" aria-label="Agenti da confrontare"><legend className="sr-only">Agenti</legend><div className="segmented multi">{(config?.agents || []).map((agent) => <label key={agent.name} title={agent.description} className={`agent-chip ${agent.kind === "foundryiq" ? "iq" : "classic"}`}><input type="checkbox" checked={agents.includes(agent.name)} onChange={(e) => setAgents((current) => e.target.checked ? [...current, agent.name] : current.filter((name) => name !== agent.name))} />{agent.kind === "foundryiq" ? <SparkIcon /> : <LayersIcon />}<span>{agent.label}</span></label>)}</div></fieldset>}
          <div className="controls-actions">
            <button type="button" className="icon-button control-icon-btn" aria-haspopup="dialog" aria-label="Architettura e ACL" data-tip="Architettura e ACL" title="Architettura e ACL" disabled={!config} onClick={() => { if (config) setGraphic({ config, mode, kb, agents: selectedAgents }); }}><FlowIcon /><span className="sr-only">Architettura e ACL</span></button>
            <button type="button" className="icon-button control-icon-btn" aria-haspopup="dialog" aria-label="Visualizza trace corrente" data-tip="Trace corrente" title="Visualizza trace corrente" onClick={() => setTraceOpen(true)}><ActivityIcon /><span className="sr-only">Visualizza trace corrente</span></button>
          </div>
        </div>
        <p className="mode-hint">{hints[mode]}</p>
      </section>
      {warning && <div className="warning" role="status">{warning}</div>}
      {!turns.length && <section className="hero">
        <h2>{demo === "acl" ? <>Esplora i <span className="grad">permessi sui documenti</span></> : demo === "agenti" ? <>Confronta gli <span className="grad">agenti RAG e Foundry IQ</span></> : <>Chiedi alla tua <span className="grad">knowledge base</span></>}</h2>
        <p>{demo === "acl" ? "Verifica in tempo reale cosa cambia tra accesso con token utente e accesso senza ACL." : demo === "agenti" ? "Confronta risposte, tool invocati, token e tempi su una singola domanda." : "Manuali tecnici, magazzino ricambi, policy SharePoint e web orchestrati da Foundry IQ."}</p>
        {examples.length > 0 && <div className="starter">{examples.map((item, i) => <button key={i} type="button" className="starter-card" onClick={() => useExample(item.question)}>
          <span className="starter-title">{item.label}<ArrowRightIcon className="starter-cta" /></span>
          <span className="starter-q">{item.question}</span>
        </button>)}</div>}
      </section>}
      <main id="log" ref={log} aria-live="polite">{turns.map((turn) => <FragmentTurn key={turn.id} turn={turn} onInfographic={(q, result, agent) => { if (config) setGraphic({ config, mode, kb, agents: selectedAgents, question: q, result, agent }); }} />)}</main>
      <div className="composer-wrap">
        {turns.length > 0 && examples.length > 0 && <section className="examples" aria-label="Domande di esempio">{examples.map((item, i) => <button key={i} type="button" title={item.question} onClick={() => useExample(item.question)}>{item.label}</button>)}</section>}
        <form id="form" onSubmit={(event) => { event.preventDefault(); ask(question); }}>
          <textarea ref={input} id="question" name="question" aria-label="Domanda alla knowledge base" rows={1} required value={question} placeholder="Fai una domanda su ricambi, manuali, policy o disponibilità…" onChange={(e) => { setQuestion(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`; }} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
          {question.length > 0 && !busy && <button type="button" className="icon-button clear-input" aria-label="Cancella testo" onClick={() => { setQuestion(""); if (input.current) { input.current.style.height = "auto"; input.current.focus(); } }}><CloseIcon /></button>}
          <button id="send" type="submit" disabled={busy || !config || (mode !== "agents" && !kb)}><span>{busy ? "Invio…" : "Invia"}</span><SendIcon /></button>
        </form>
        <p className="footnote">Risposte generate da AI · Verifica sempre i riferimenti citati.</p>
      </div>
    </div>
    {!testsOpen && <button type="button" className="tests-edge-tab" aria-controls="tests" aria-expanded={false} aria-label="Espandi sidebar query di test" title="Espandi query di test (Alt+T)" onClick={() => setTestsOpen(true)}><ChevronLeftIcon /><FlaskIcon /><span>Test</span></button>}
    <TestPanel kb={kb} items={tests} done={done} open={testsOpen} onClose={() => setTestsOpen(false)} onSelect={(value) => { ask(value); if (matchMedia("(max-width: 1100px)").matches) setTestsOpen(false); }} /></div>
    <Profile config={config} open={profileOpen} onClose={() => setProfileOpen(false)} /><Infographic data={graphic} onClose={() => setGraphic(null)} />
    <CurrentTraceDialog open={traceOpen} onClose={() => setTraceOpen(false)} snapshot={traceSnapshot} />
  </>;
}

function FragmentTurn({ turn, onInfographic }: { turn: Turn; onInfographic: (question: string, result: ChatResult, agent?: Agent) => void }) {
  return <><div className="turn-user" data-turn={turn.id}>{turn.question}</div><div className={`turn-assistant ${turn.entries.length > 1 ? "compare" : ""} ${turn.entries.length > 2 ? "cols-3" : ""}`}>{turn.entries.map((entry, i) => <AnswerCard key={i} entry={entry} question={turn.question} onInfographic={onInfographic} />)}</div></>;
}
