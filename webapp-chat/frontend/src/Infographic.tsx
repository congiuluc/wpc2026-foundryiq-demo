import { useRef } from "react";
import { Dialog } from "./Layout";
import type { GraphicData } from "./types";

function wrap(value: string | undefined, width: number, max = 2) {
  const words = String(value ?? "").replace(/\s+/g, " ").trim().split(" ");
  const lines = []; let line = "";
  for (const word of words) {
    if (line && `${line} ${word}`.length > width) { lines.push(line); line = ""; }
    if (word.length > width) {
      if (line) { lines.push(line); line = ""; }
      for (let i = 0; i < word.length; i += width) lines.push(word.slice(i, i + width));
    } else line += (line ? " " : "") + word;
  }
  if (line) lines.push(line);
  if (lines.length > max) { lines.length = max; lines[max - 1] = `${lines[max - 1].slice(0, width - 3)}...`; }
  return lines;
}

function Text({ x, y, value, width = 28, size = 22, max = 2, color = "#384060", bold = false }: { x: number; y: number; value?: string; width?: number; size?: number; max?: number; color?: string; bold?: boolean }) {
  return <text fill={color} fontSize={size} fontWeight={bold ? 700 : 400}>{wrap(value, width, max).map((line, i) => <tspan key={i} x={x} y={y + i * (size + 7)}>{line}</tspan>)}</text>;
}
function Glyph({ kind, color }: { kind: "identity" | "acl" | "iq" | "source" | "tool" | "output"; color: string }) {
  return <g transform="translate(24 20)">
    <rect width="38" height="38" rx="11" fill={color} opacity=".13" />
    <g transform="translate(9 9)" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {kind === "identity" && <><circle cx="10" cy="6.5" r="3.2" /><path d="M3.8 17c.9-3 3.3-4.6 6.2-4.6s5.3 1.6 6.2 4.6" /></>}
      {kind === "acl" && <><path d="M10 2 16 4.5v4.6c0 4-2.5 6.8-6 8.1-3.5-1.3-6-4.1-6-8.1V4.5Z" /><path d="m7.6 10 1.7 1.7 3.2-3.3" /></>}
      {kind === "iq" && <path d="M10 2 12 7.2 17.2 9.2 12 11.2 10 16.4 8 11.2 2.8 9.2 8 7.2Z" />}
      {kind === "source" && <><ellipse cx="10" cy="5" rx="6.2" ry="2.6" /><path d="M3.8 5v5c0 1.4 2.8 2.6 6.2 2.6s6.2-1.2 6.2-2.6V5M3.8 10v5c0 1.4 2.8 2.6 6.2 2.6s6.2-1.2 6.2-2.6v-5" /></>}
      {kind === "tool" && <><rect x="3" y="5" width="14" height="11" rx="2.5" /><path d="M10 2.5V5M7.5 10h.01M12.5 10h.01M7.5 13h5" /></>}
      {kind === "output" && <><rect x="3.5" y="2.5" width="13" height="15" rx="2.2" /><path d="M7 7h6M7 10.5h6M7 14h3.8" /></>}
    </g>
  </g>;
}

function Box({ x, y, w, h = 166, label, title, detail, color = "#4338ca", glyph = "iq" }: { x: number; y: number; w: number; h?: number; label: string; title?: string; detail?: string; color?: string; glyph?: "identity" | "acl" | "iq" | "source" | "tool" | "output" }) {
  return <g transform={`translate(${x} ${y})`}>
    <rect width={w} height={h} rx="22" fill="#ffffff" stroke="#d5dcf0" strokeWidth="1.5" filter="url(#shadow)" />
    <rect x="1" y="14" width="6" height={h - 28} rx="3" fill={color} />
    <Glyph kind={glyph} color={color} />
    <Text x={74} y={44} value={label} size={15} width={Math.max(12, Math.floor((w - 90) / 9.5))} max={1} color={color} bold />
    <Text x={24} y={86} value={title} size={24} width={Math.max(12, Math.floor((w - 44) / 13.5))} max={1} color="#0f172a" bold />
    <Text x={24} y={120} value={detail} size={18} width={Math.max(16, Math.floor((w - 44) / 10.5))} max={2} color="#3b4468" />
  </g>;
}

function buildModel(data: GraphicData) {
  const { config, mode, kb, agents, question, result, agent } = data;
  const architecture = !result;
  const agentMode = architecture ? mode === "agents" : !!agent;
  const isAcl = !agentMode && (mode === "compare" || kb?.id === "acl");
  const steps: [string, string][] = [];
  if (!result) {
    steps.push(["Autenticazione", config?.running_on_app_service ? "Entra ID ed Easy Auth: il backend riceve il token Search." : "In locale le credenziali Azure CLI forniscono il token utente Search."],
      ["Identità", agentMode ? "Gli agenti usano l'identità del progetto senza ACL dell'utente." : "L'identità dell'app autorizza la chiamata alla knowledge base."],
      ["Controlli ACL", "Con ACL il backend inoltra il token Search dell'utente. SharePoint remoto usa il token e i propri permessi anche in modalità senza ACL."],
      ["Fonti previste", kb?.id === "acl" && !agentMode ? "Documenti ADLS con ACL di cartella per gruppi Tecnici e Direzione." : "Manuali Blob/indice, MCP ricambi, SharePoint se configurato e web filtrato."],
      ["Risposta", "L'architettura descrive il percorso previsto, non una traccia osservata. Nessun risultato non prova un diniego ACL."]);
  } else {
    const execution = result.execution || {};
    steps.push(["Domanda", question || ""], ["Identità", `${execution.identity || "Identità non esposta"}. Token utente ${execution.user_token_forwarded ? "inoltrato" : "non inoltrato"}.`],
      ["Controlli ACL", agent ? "Identità del progetto: ACL utente non applicate." : execution.acl_requested ? "Filtro ACL richiesto con il token Search; non sono esposte decisioni per documento." : execution.user_token_forwarded && execution.remote_sharepoint ? "Nessun filtro ACL documentale richiesto; SharePoint remoto applica comunque i permessi utente." : "Nessun filtro ACL utente richiesto; le fonti possono imporre altri controlli."],
      [agent ? "Tool osservati" : "Fonti osservate", agent ? (result.tool_calls?.map((t) => `${t.server ? `${t.server} / ` : ""}${t.name}: ${t.error || t.status || "stato non esposto"}`).join("; ") || "Nessun tool osservato.") : (result.sources?.map((s) => `${s.name}: ${s.queries} query, ${s.results} risultati${s.error ? `, errore ${s.error}` : ""}`).join("; ") || "Nomi delle fonti non esposti.")],
      ["Riferimenti", `${(result.references || []).length + (result.citations || []).length} riferimenti o citazioni. Zero risultati non prova un diniego ACL.`],
      ["Sintesi", agent ? "Modello dell'agente; l'attività interna della KB può non essere disponibile." : execution.synthesis_performed ? `Sintesi con ${execution.chat_deployment} dai contenuti estratti.` : "Nessuna chiamata al modello di sintesi."],
      ["Risposta", result.answer || "Nessuna risposta testuale."]);
  }
  const title = architecture ? agentMode ? "Architettura · confronto agenti" : isAcl ? "Architettura · demo ACL" : "Architettura · demo multi-fonte"
    : agent ? `Esecuzione · ${agent.label}` : `Percorso osservato · ${isAcl ? "demo ACL" : "demo multi-fonte"}`;
  let lanes: { title?: string; label: string; detail: string; extra?: string; acl?: boolean; identity?: string; control?: string }[] = [], nodes: { title: string; detail: string }[] = [];
  if (agentMode) {
    lanes = architecture ? agents.map((a) => ({ title: a.label, label: a.kind === "foundryiq" ? "FOUNDRY IQ" : "RAG CLASSICO", detail: "Identità del progetto", extra: "Tool e fonti dipendono dall'agente" }))
      : [{ title: agent?.label, label: agent?.kind === "foundryiq" ? "FOUNDRY IQ" : "RAG CLASSICO", detail: "Identità del progetto", extra: result?.tool_calls?.map((t) => t.name || t.type).join(" · ") || "Nessun tool osservato" }];
  } else if (isAcl) {
    const lane = (acl: boolean, observed: boolean) => ({ label: acl ? "CON ACL" : "SENZA ACL", acl,
      identity: observed ? result?.execution?.user_token_forwarded ? "App + token utente" : "Identità app" : acl ? "App + token utente" : "Identità app",
      control: acl ? "Security trimming" : "Nessun filtro ACL utente",
      detail: observed ? `${(result?.references || []).length} riferimenti · ${result?.elapsed_ms ?? "—"} ms` : "Esito dipende da query, dati e permessi" });
    lanes = architecture && mode === "compare" ? [lane(false, false), lane(true, false)] : [lane(architecture ? mode === "acl" : !!result?.execution?.acl_requested, !architecture)];
  } else {
    nodes = architecture ? [{ title: "Manuali e indice", detail: "Documentazione tecnica" }, { title: "Ricambi", detail: "MCP e SQL" }, { title: "SharePoint", detail: "Policy e permessi utente" }, { title: "Ricerca web", detail: "Ricerca filtrata" }]
      : result?.sources?.length ? result.sources.slice(0, 4).map((s) => ({ title: s.name, detail: `${s.queries} query · ${s.results} risultati${s.error ? " · errore" : ""}` })) : [{ title: "Fonti non esposte", detail: "Nessuna fonte dedotta" }];
  }
  return { title, architecture, agentMode, isAcl, lanes, nodes, steps };
}

export function Infographic({ data, onClose }: { data: GraphicData | null; onClose: () => void }) {
  const svg = useRef<SVGSVGElement | null>(null);
  const model = data ? buildModel(data) : null;
  const note = "Le decisioni ACL per documento non sono esposte. Zero risultati non prova un diniego.";
  const download = () => {
    if (!svg.current) return;
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg.current)], { type: "image/svg+xml;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = "foundry-iq-infografica.svg"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <Dialog open={!!data} onClose={onClose} className="infographic-dialog" title={model?.title || "Architettura e ACL"} actions={<button type="button" className="secondary" onClick={download}>Scarica SVG 16:9</button>}>
    {model && data && <><figure className="infographic-frame">
      <svg ref={svg} xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900" role="img" aria-labelledby="graphic-title graphic-desc" fontFamily="Segoe UI Variable Text, Segoe UI, system-ui, sans-serif">
        <title id="graphic-title">{model.title}</title><desc id="graphic-desc">{note} Descrizione completa sotto il diagramma.</desc>
        <defs>
          <linearGradient id="bg-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#f7f8ff" />
            <stop offset="55%" stopColor="#f3f5fe" />
            <stop offset="100%" stopColor="#f9f5ff" />
          </linearGradient>
          <linearGradient id="accent-bar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#22d3ee" />
            <stop offset="50%" stopColor="#4f46e5" />
            <stop offset="100%" stopColor="#a855f7" />
          </linearGradient>
          <filter id="shadow" x="-12%" y="-12%" width="124%" height="132%">
            <feDropShadow dx="0" dy="8" stdDeviation="10" floodColor="#1e2963" floodOpacity=".08" />
          </filter>
          <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 1.5 9 5 0 8.5z" fill="#64729c" />
          </marker>
        </defs>
        <rect width="1600" height="900" fill="url(#bg-grad)" />
        <circle cx="1480" cy="90" r="240" fill="#6366f1" opacity=".05" />
        <circle cx="140" cy="830" r="220" fill="#0ea5e9" opacity=".05" />
        <rect x="58" y="36" width="8" height="86" rx="4" fill="url(#accent-bar)" />
        <rect x="1220" y="42" width="322" height="40" rx="20" fill="#eef2fc" stroke="#d5dcf0" />
        <Text x={1246} y={68} value={model.architecture ? "WPC 2026 · ARCHITETTURA" : "WPC 2026 · FLUSSO OSSERVATO"} width={32} size={15} max={1} color="#4338ca" bold />
        <Text x={84} y={74} value={model.title} width={60} size={38} max={1} color="#0f172a" bold />
        <Text x={84} y={114} value={model.architecture ? "Architettura prevista · non è una traccia di esecuzione" : "Esecuzione osservata · dati restituiti dalla risposta"} width={96} size={22} max={1} color="#3b4468" />
        <rect x="58" y="144" width="1484" height="76" rx="18" fill="#ffffff" stroke="#dde3f4" />
        <Text x={82} y={176} value={data.question || "Percorso dei dati e confini di identità"} width={114} size={21} max={2} color="#1e293b" />
        {model.agentMode ? model.lanes.slice(0, 3).map((lane, i) => {
          const w = Math.min(900, (1420 - (model.lanes.length - 1) * 40) / Math.max(model.lanes.length, 1));
          const x = 90 + i * (w + 40);
          const isIq = lane.label === "FOUNDRY IQ";
          return <g key={i}>
            <rect x={x} y="252" width={w} height="542" rx="28" fill="#eef1fc" stroke="#d5dcf0" />
            <Box x={x + 22} y={278} w={w - 44} label={lane.label} title={lane.title} detail={lane.detail} color={isIq ? "#4338ca" : "#9d174d"} glyph={isIq ? "iq" : "tool"} />
            <path d={`M${x + w / 2} 446v42`} stroke="#64729c" strokeWidth="3.5" markerEnd="url(#arrow)" />
            <Box x={x + 22} y={498} w={w - 44} label="TOOL" title="Orchestrazione" detail={lane.extra} color="#0369a1" glyph="source" />
            <rect x={x + 22} y="692" width={w - 44} height="76" rx="18" fill="#ffffff" stroke="#cce8de" />
            <Text x={x + 44} y={737} value="Risposta dell'agente · senza ACL utente" width={Math.floor(w / 12)} size={19} color="#046c4e" bold />
          </g>;
        }) : model.isAcl ? model.lanes.map((lane, i) => {
          const y = model.lanes.length > 1 ? 305 + i * 245 : 430;
          return <g key={i}>
            <rect x="74" y={y - 42} width="1452" height="222" rx="26" fill={lane.acl ? "#edf8f4" : "#eef1fc"} stroke={lane.acl ? "#bde3d4" : "#d5dcf0"} />
            <Text x={98} y={y - 14} value={lane.label} color={lane.acl ? "#046c4e" : "#4338ca"} size={17} bold />
            <Box x={96} y={y} w={320} h={162} label="IDENTITÀ" title={lane.identity} detail="Autorizzazione della richiesta" color="#4338ca" glyph="identity" />
            <Box x={474} y={y} w={368} h={162} label="CONTROLLO" title={lane.control} detail={lane.acl ? "Permessi dell'utente richiesti" : "Le fonti possono applicare controlli"} color={lane.acl ? "#046c4e" : "#9d174d"} glyph="acl" />
            <Box x={900} y={y} w={530} h={162} label="RISULTATO" title="Riferimenti" detail={lane.detail} color="#0369a1" glyph="output" />
            <path d={`M418 ${y + 81}h44 M844 ${y + 81}h44`} stroke="#64729c" strokeWidth="3.5" fill="none" markerEnd="url(#arrow)" />
          </g>;
        }) : <g>
          {model.nodes.map((node, i) => {
            const coords = [[72, 262], [72, 548], [1188, 262], [1188, 548]][i];
            const left = i < 2, y = coords[1] + 83;
            return <g key={i}>
              <path d={`M${left ? 590 : 1010} ${y < 440 ? 385 : 475} H${left ? 485 : 1115} V${y} H${left ? 422 : 1178}`} stroke="#64729c" strokeWidth="3.5" fill="none" markerEnd="url(#arrow)" />
              <Box x={coords[0]} y={coords[1]} w={340} label={model.architecture ? "FONTE PREVISTA" : "FONTE OSSERVATA"} title={node.title} detail={node.detail} color="#0369a1" glyph="source" />
            </g>;
          })}
          <Box x={590} y={332} w={420} h={184} label="ORCHESTRAZIONE" title="Foundry IQ" detail="Pianifica query e interroga le fonti" color="#4338ca" glyph="iq" />
          <path d="M800 518v104" stroke="#64729c" strokeWidth="3.5" markerEnd="url(#arrow)" />
          <Box x={550} y={634} w={500} h={162} label="OUTPUT" title="Risposta e riferimenti" detail={model.architecture ? "Fonti previste dalla demo" : `${(data.result?.references || []).length} riferimenti restituiti`} color="#046c4e" glyph="output" />
        </g>}
        <Text x={58} y={862} value={note} width={120} size={19} max={1} color="#475569" />
      </svg><figcaption>{note}</figcaption></figure>
      <details className="infographic-description" open={matchMedia("(max-width: 860px)").matches}><summary>Descrizione accessibile e dettagli completi</summary><ol>{model.steps.map(([title, detail], i) => <li key={i}><strong>{title}</strong>: {detail}</li>)}</ol>
        {!!data.result?.activity?.length && <><h3>Attività osservata</h3><ul>{data.result.activity.map((a, i) => <li key={i}>{a.knowledgeSourceName || a.type || "Attività"}{a.query ? ` · Query: ${a.query}` : ""}{a.count != null ? ` · Risultati: ${a.count}` : ""}{a.error ? ` · Errore: ${JSON.stringify(a.error)}` : ""}</li>)}</ul></>}</details></>}
  </Dialog>;
}
