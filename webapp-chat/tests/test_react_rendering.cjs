const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { createRequire } = require("node:module");
const { pathToFileURL } = require("node:url");

const frontend = path.resolve(__dirname, "..", "frontend");
const frontendRequire = createRequire(path.join(frontend, "package.json"));
const React = frontendRequire("react");
const { renderToStaticMarkup } = frontendRequire("react-dom/server");
const originalMatchMedia = globalThis.matchMedia;
let server, AnswerCard, Infographic;

before(async () => {
  const { createServer } = await import(pathToFileURL(frontendRequire.resolve("vite")).href);
  server = await createServer({ root: frontend, logLevel: "silent", server: { middlewareMode: true }, appType: "custom" });
  ({ AnswerCard } = await server.ssrLoadModule(path.join(frontend, "src", "Responses.tsx")));
  ({ Infographic } = await server.ssrLoadModule(path.join(frontend, "src", "Infographic.tsx")));
  globalThis.matchMedia = () => ({ matches: true });
});

after(async () => {
  if (server) await server.close();
  if (originalMatchMedia === undefined) delete globalThis.matchMedia;
  else globalThis.matchMedia = originalMatchMedia;
});

const agent = { name: "test-agent", label: "Agente di test", kind: "classic", description: "Test" };
const result = (overrides = {}) => ({ answer: "Risposta", ...overrides });

function answer(data, options = {}) {
  return renderToStaticMarkup(React.createElement(AnswerCard, {
    entry: { loading: false, result: data, ...options },
    question: "Domanda",
    onInfographic() {},
  }));
}

function diagram(overrides = {}) {
  const data = {
    config: { running_on_app_service: true, user_token_available: true, knowledge_bases: [], agents: [] },
    mode: "noacl",
    kb: { id: "demo", name: "demo-kb", label: "Demo", examples: [] },
    agents: [],
    ...overrides,
  };
  return renderToStaticMarkup(React.createElement(Infographic, { data, onClose() {} }));
}

test("retry warning remains visible with its usage scope while loading and after completion", () => {
  for (const loading of [true, false]) {
    const html = answer(result({ streaming: {
      mode: "json_retry", warning: "Retry senza streaming", usage_scope: "Token del retry, primo tentativo non disponibile",
    } }), { agent, loading });
    assert.match(html, /role="status">Retry senza streaming/);
    assert.match(html, /primo tentativo non disponibile/);
  }
});

test("agent metrics distinguish time, calls and input/output tokens", () => {
  const html = answer(result({
    elapsed_ms: 8189,
    tool_calls: [{ type: "azure_ai_search_call", name: "azure_ai_search" }],
    usage: { total_tokens: 7937, input_tokens: 7600, output_tokens: 337 },
  }), { agent });
  assert.match(html, /8,19 s/);
  assert.match(html, /8\.189 ms/);
  assert.match(html, /Token totali<\/dt><dd>7\.937<\/dd>/);
  assert.match(html, /<dt>Input<\/dt><dd>7\.600<\/dd>/);
  assert.match(html, /<dt>Output<\/dt><dd>337<\/dd>/);
  assert.match(html, /Azure AI Search/);
});

test("zero telemetry is displayed while missing telemetry remains unavailable", () => {
  const zero = answer(result({ elapsed_ms: 0, tool_calls: [], usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 } }), { agent });
  assert.match(zero, /0 s/);
  assert.match(zero, /Token totali<\/dt><dd>0<\/dd>/);
  assert.match(zero, /Nessun tool invocato/);
  assert.doesNotMatch(zero, /Non disponibili/);
  const missing = answer(result(), { agent });
  assert.match(missing, /Tempo non restituito/);
  assert.match(missing, /provider non ha restituito/);
  assert.match(missing, /chiamate tool non disponibili/);
  assert.doesNotMatch(missing, /<dd>0<\/dd>/);
});

test("observed tool calls are grouped with call and error counts", () => {
  const call = { type: "mcp_call", server: "ricambi", name: "check_stock" };
  const html = answer(result({ tool_calls: [
    { ...call, status: "completed" },
    { ...call, status: "failed" },
    { ...call, server: "altro", status: "completed" },
  ] }), { agent });
  assert.match(html, /Chiamate tool<\/dt><dd>3<\/dd>/);
  assert.equal((html.match(/class="agent-tool-name"/g) || []).length, 2);
  assert.match(html, /2 chiamate/);
  assert.match(html, /1 errore/);
  assert.match(html, /MCP · altro · check_stock/);
});

test("tool details preserve unknown statuses and escape provider text", () => {
  const html = answer(result({ tool_calls: [
    { type: "mcp_call", name: "tool", arguments: "<script>alert(1)</script>" },
    { type: "mcp_call", name: "tool", status: "failed", error: "Errore <provider>" },
    { type: "function_call", name: "custom", status: "custom_status" },
  ] }), { agent });
  assert.match(html, /Stato non esposto/);
  assert.match(html, /custom_status/);
  assert.match(html, /class="failed"/);
  assert.match(html, /Errore &lt;provider&gt;/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|Completata/);
});

test("total tokens are not fabricated from partial usage", () => {
  const html = answer(result({ tool_calls: [], usage: { input_tokens: 1234 } }), { agent });
  assert.match(html, /Token totali<\/dt><dd>Non disponibili<\/dd>/);
  assert.match(html, /<dt>Input<\/dt><dd>1\.234<\/dd>/);
  assert.match(html, /<dt>Output<\/dt><dd>Non disponibili<\/dd>/);
});

test("retrieval markers resolve by exact ID within each response", () => {
  const first = answer(result({
    answer: "Vedi [0] e [ref_id:8], poi ancora [0].",
    references: [{ id: "8", title: "Manuale" }, { id: 0, title: "Ricambi" }],
  }));
  const other = answer(result({ answer: "[0]", references: [{ id: 0, title: "Policy" }] }));
  assert.equal((first.match(/class="citation-link"/g) || []).length, 3);
  assert.equal((first.match(/Vai al riferimento 0: Ricambi/g) || []).length, 2);
  assert.match(first, /Vai al riferimento 8: Manuale/);
  assert.match(other, /Vai al riferimento 0: Policy/);
});

test("missing and duplicate citation IDs cannot become guessed links", () => {
  const html = answer(result({ answer: "[1] [2]", references: [{ id: "1", title: "Uno" }, { id: "1", title: "Due" }] }));
  assert.doesNotMatch(html, /class="citation-link"/);
  assert.equal((html.match(/class="citation-unavailable"/g) || []).length, 2);
  const agentHtml = answer(result({ answer: "[0]", citations: [{ id: 0, title: "Fonte" }] }), { agent });
  assert.doesNotMatch(agentHtml, /class="citation-link"/);
  assert.match(agentHtml, /posizion/);
});

test("agent annotations and explicit knowledge-base references link to their own cards", () => {
  const text = "😀 Consulta **la guida** e [ref_id:7].";
  const html = answer(result({
    answer: text,
    citations: [{ title: "Guida web", url: "https://example.com/guide", type: "url_citation" },
      { title: "allegato.pdf", type: "file_citation" }],
    citation_spans: [{ start: text.indexOf("la guida"), end: text.indexOf("la guida") + "la guida".length, citation_index: 0 }],
    references: [{ id: 7, title: "Manuale tecnico", tool_call: 1 }],
  }), { agent });
  assert.match(html, /<strong>la guida<\/strong><button[^>]*>\[C1\]<\/button>/);
  assert.match(html, /Vai alla citazione C1: Guida web/);
  assert.match(html, /Vai al riferimento 7: Manuale tecnico/);
  assert.match(html, /Citazione C1/);
  assert.match(html, /Rif\. 7/);
  assert.match(html, /allegato\.pdf/);
  assert.match(html, /chiamata 1/);
});

test("agent numeric markers do not resolve to unrelated MCP references", () => {
  const text = "Web [0] e manuale [ref_id:0].";
  const html = answer(result({
    answer: text,
    citations: [{ title: "Fonte web", url: "https://example.com/web", type: "url_citation" }],
    citation_spans: [{ start: text.indexOf("Web"), end: text.indexOf("Web") + 3, citation_index: 0 }],
    references: [{ id: 0, title: "Manuale MCP", tool_call: 1 }],
  }), { agent });
  assert.equal((html.match(/class="citation-link"/g) || []).length, 2);
  assert.match(html, /\[C1\]<\/button> \[0\]/);
  assert.match(html, /Vai al riferimento 0: Manuale MCP/);
  assert.match(html, /\[ref_id:0\]<\/button>/);
});

test("file citation positions insert a link at the given text index", () => {
  const html = answer(result({
    answer: "😀 Manuale allegato",
    citations: [{ title: "manuale.pdf", type: "file_citation" }],
    citation_spans: [{ start: 10, end: 10, citation_index: 0 }],
  }), { agent });
  assert.match(html, /😀 Manuale<button[^>]*>\[C1\]<\/button> allegato/);
  assert.doesNotMatch(html, /senza posizioni valide/);
});

test("file citations at line starts preserve headings and lists", () => {
  const text = "# Manuale\n- Punto";
  const html = answer(result({
    answer: text,
    citations: [{ title: "manuale.pdf", type: "file_citation" }],
    citation_spans: [
      { start: 0, end: 0, citation_index: 0 },
      { start: text.indexOf("- Punto"), end: text.indexOf("- Punto"), citation_index: 0 },
    ],
  }), { agent });
  assert.match(html, /<h3><button[^>]*>\[C1\]<\/button>Manuale<\/h3>/);
  assert.match(html, /<ul><li><button[^>]*>\[C1\]<\/button>Punto<\/li><\/ul>/);
});

test("agent source markers resolve only the exact marker on an unambiguous reference", () => {
  const html = answer(result({
    answer: "Fonte 【6:3†source】, alternativa [6:3|source], ignota 【6:0†source】.",
    references: [{ id: 0, marker: "【6:3†source】", title: "Manuale" }],
  }), { agent });
  assert.equal((html.match(/class="citation-link"/g) || []).length, 2);
  assert.match(html, /Vai al riferimento 【6:3†source】: Manuale/);
  assert.match(html, /Vai al riferimento \[6:3\|source\]: Manuale/);
  assert.match(html, /class="citation-unavailable"[^>]*>【6:0†source】/);

  const ambiguous = answer(result({
    answer: "【6:3†source】",
    references: [
      { id: 0, marker: "【6:3†source】", title: "Primo", tool_call: 1 },
      { id: 1, marker: "【6:3†source】", title: "Secondo", tool_call: 2 },
    ],
  }), { agent });
  assert.doesNotMatch(ambiguous, /class="citation-link"/);
  assert.match(ambiguous, /class="citation-unavailable"/);
});

test("agent references with repeated IDs are not linked by guesswork", () => {
  const html = answer(result({
    answer: "Un risultato [ref_id:0] e un marcatore 【3:0†source】.",
    references: [{ id: 0, title: "Primo", tool_call: 1 }, { id: 0, title: "Secondo", tool_call: 2 }],
    citations: [],
  }), { agent });
  assert.doesNotMatch(html, /class="citation-link"/);
  assert.match(html, /Riferimento 0 non disponibile o ambiguo/);
  assert.match(html, /【3:0†source】/);
  assert.match(html, /Primo/);
  assert.match(html, /Secondo/);
});

test("missing knowledge-base tool references are reported as unavailable", () => {
  const html = answer(result({
    answer: "Testo",
    citations: [{ title: "Fonte", url: "https://example.com" }],
    tool_calls: [{ type: "mcp_call", name: "knowledge_base_retrieve" }],
  }), { agent });
  assert.match(html, /Il tool non ha esposto riferimenti utilizzabili/);
});

test("invalid or absent agent annotation positions do not create inline links", () => {
  const html = answer(result({
    answer: "Testo senza riferimenti",
    citations: [{ title: "Fonte", url: "https://example.com" }],
    citation_spans: [{ start: 0, end: 1000, citation_index: 0 }],
  }), { agent });
  assert.doesNotMatch(html, /class="citation-link"/);
  assert.match(html, /Fonte/);
  assert.match(html, /posizion/);
});

test("citations leave inline code and links intact and escape provider text", () => {
  const html = answer(result({
    answer: "`[0]` [example](https://example.com/[0]) **test [0]** <img onerror=x>",
    references: [{ id: 0, title: '<script>"unsafe"</script>' }],
  }));
  assert.match(html, /<code>\[0\]<\/code>/);
  assert.match(html, /href="https:\/\/example.com\/\[0\]"/);
  assert.equal((html.match(/class="citation-link"/g) || []).length, 1);
  assert.match(html, /<strong>test <button/);
  assert.match(html, /&lt;img onerror=x&gt;/);
  assert.doesNotMatch(html, /<img onerror|<script>/);
});

test("reference cards show metadata and allow only HTTP links", () => {
  const html = answer(result({ references: [
    { id: 0, title: "Manuale", type: "azureBlob", knowledge_source: "docs", path: "https://example.com/a" },
    { id: 1, title: "<img src=x>", url: "javascript:alert(1)" },
    { id: 2, title: "Guida", url: "https://example.com/guide" },
  ] }));
  assert.match(html, /Rif\. 0/);
  assert.match(html, /docs/);
  assert.match(html, /Percorso:/);
  assert.match(html, /https:\/\/example.com\/a/);
  assert.match(html, /href="https:\/\/example.com\/guide"/);
  assert.doesNotMatch(html, /href="javascript:|<img src=x>/);
  assert.match(html, /&lt;img src=x&gt;/);
});

test("query infographic has 16:9 output and a complete escaped description", () => {
  const question = '<script>alert("x")</script> ' + "domanda lunga ".repeat(40);
  const html = diagram({ question, result: result({ references: [], sources: [], activity: [] }) });
  assert.match(html, /width="1600" height="900" viewBox="0 0 1600 900"/);
  assert.ok(html.includes(question.replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")));
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /class="infographic-description" open=""/);
});

test("ACL architecture shows security trimming and both comparison lanes", () => {
  const html = diagram({ mode: "compare", kb: { id: "acl", name: "acl-kb", label: "Demo ACL", examples: [] } });
  assert.match(html, /Architettura · demo ACL/);
  assert.match(html, /Documenti ADLS con ACL/);
  assert.match(html, /SENZA ACL/);
  assert.match(html, /CON ACL/);
  assert.match(html, /Security trimming/);
});

test("multi-source query shows observed sources rather than planned ones", () => {
  const architecture = diagram();
  assert.match(architecture, /Architettura · demo multi-fonte/);
  assert.match(architecture, /Ricambi/);
  assert.match(architecture, /MCP/);
  const html = diagram({ question: "Policy?", result: result({
    references: [],
    sources: [{ name: "SharePoint policy", type: "indexedSharePoint", queries: 1, results: 2 }],
    activity: [{ knowledgeSourceName: "SharePoint policy" }],
  }) });
  assert.match(html, /Percorso osservato · demo multi-fonte/);
  assert.match(html, /SharePoint policy/);
  assert.doesNotMatch(html, /Manuali e indice/);
});

test("agent diagrams separate lanes and include only returned tool calls", () => {
  const architecture = diagram({ mode: "agents", agents: [
    { name: "classic", label: "RAG rapido", kind: "classic" },
    { name: "iq", label: "Foundry IQ", kind: "foundryiq" },
  ] });
  assert.match(architecture, /Architettura · confronto agenti/);
  assert.match(architecture, /RAG rapido/);
  assert.match(architecture, /Foundry IQ/);
  const html = diagram({ mode: "agents", agent, question: "Stock?", result: result({
    tool_calls: [{ type: "mcp_call", name: "inventory", server: "parts", status: "completed" }],
  }) });
  assert.match(html, /parts \/ inventory: completed/);
  assert.doesNotMatch(html, /Manuali e indice/);
});

test("missing activity and zero results do not imply an ACL denial", () => {
  const html = diagram({ mode: "acl", kb: { id: "acl" }, question: "Codice service?", result: result({
    references: [], sources: [], execution: { acl_requested: true, user_token_forwarded: true },
  }) });
  assert.match(html, /Filtro ACL richiesto/);
  assert.match(html, /Nomi delle fonti non esposti/);
  assert.match(html, /non prova un diniego ACL/);
  assert.match(html, /Nessuna chiamata al modello di sintesi/);
  assert.doesNotMatch(html, /Accesso negato/);
});

test("SharePoint permissions remain explicit without document ACL", () => {
  const html = diagram({ question: "Policy?", result: result({
    execution: { acl_requested: false, user_token_forwarded: true, remote_sharepoint: true, synthesis_performed: true, chat_deployment: "test-model" },
    sources: [{ name: "policy-ks", queries: 2, results: 3 }],
    activity: [{ knowledgeSourceName: "policy-ks", query: "policy <2027>", count: 3 }],
  }) });
  assert.match(html, /SharePoint remoto applica comunque i permessi utente/);
  assert.match(html, /policy-ks: 2 query, 3 risultati/);
  assert.match(html, /Query: policy &lt;2027&gt;/);
  assert.match(html, /Sintesi con test-model/);
});

test("agent flow shows project identity and observed tools without inferred sources", () => {
  const html = diagram({ mode: "agents", agent, question: "Scorte?", result: result({
    tool_calls: [{ type: "mcp_call", name: "stock", error: "Timeout" }],
    references: [],
    citations: [{ title: "Guida", url: "https://example.com" }],
  }) });
  assert.match(html, /Identità del progetto: ACL utente non applicate/);
  assert.match(html, /stock: Timeout/);
  assert.match(html, /1 riferimenti o citazioni/);
  assert.doesNotMatch(html, /Manuali e indice/);
});
