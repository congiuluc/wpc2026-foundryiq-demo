const test = require("node:test");
const { before, after } = test;
const assert = require("node:assert/strict");
const path = require("node:path");
const { createRequire } = require("node:module");
const { pathToFileURL } = require("node:url");

const frontend = path.resolve(__dirname, "..", "frontend");
const frontendRequire = createRequire(path.join(frontend, "package.json"));
let server, createCollector, safeText, parseTrace, readEvents;

before(async () => {
  const { createServer } = await import(pathToFileURL(frontendRequire.resolve("vite")).href);
  server = await createServer({ root: frontend, logLevel: "silent", server: { middlewareMode: true }, appType: "custom" });
  ({ createCollector, safeText } = await server.ssrLoadModule(path.join(frontend, "src", "trace", "currentTrace.ts")));
  ({ parseTrace } = await server.ssrLoadModule(path.join(frontend, "src", "trace", "traceParser.ts")));
  ({ readEvents } = await server.ssrLoadModule(path.join(frontend, "src", "stream.ts")));
});

after(async () => { if (server) await server.close(); });

test("stream parser preserves split UTF-8, multiline data, heartbeats and CRLF", async () => {
  const bytes = new TextEncoder().encode(': heartbeat\r\n\r\nevent: delta\r\ndata: {"text":\r\ndata: "disponibilità"}\r\n\r\nevent: completed\ndata: {"answer":"ok"}\n\n');
  const body = new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } });
  const events = [];
  for await (const event of readEvents(body)) events.push(event);
  assert.deepEqual(events, [
    { event: "delta", data: { text: "disponibilità" } },
    { event: "completed", data: { answer: "ok" } },
  ]);
});

test("partial trace stays in progress and is replaced by the completed result", () => {
  const trace = createCollector();
  const id = trace.begin({ question: "Manuale?", label: "IQ" });
  trace.update(id, { answer: "Parziale", activity: [{ id: 1, status: "in_progress" }] });
  assert.equal(trace.snapshot().messages[1].parts[0].status, "in_progress");
  assert.match(JSON.stringify(trace.snapshot()), /Parziale/);
  trace.finish(id, { answer: "Completa", retrieval_usage: { total_tokens: 13 } });
  assert.equal(trace.snapshot().messages[1].parts[0].status, "completed");
  assert.doesNotMatch(JSON.stringify(trace.snapshot()), /Parziale/);
  assert.match(JSON.stringify(trace.snapshot()), /"total_tokens":13/);
});

test("trace includes queries, tool arguments and timings with credentials redacted", () => {
  const trace = createCollector();
  const id = trace.begin({ question: "Pompa?", label: "IQ" });
  trace.finish(id, { answer: "ok", elapsed_ms: 900, timeline: [
    { at_ms: 5, phase: "agent", title: "Richiesta inviata" },
    { at_ms: 40, phase: "tool", title: "Chiamata tool avviata: retrieve" },
    { at_ms: 70, phase: "tool", title: "Argomenti inviati al tool", detail: "{\"query\":\"pompa P-200\",\"api_key\":\"k1\"}", private: true },
    { at_ms: 300, phase: "generation", title: "Primo token della risposta" },
  ], tool_calls: [{ id: "mcp_1", type: "mcp_call", name: "retrieve", status: "completed",
    arguments: "{\"query\":\"pompa P-200\"}", started_ms: 40, completed_ms: 250, duration_ms: 210 }],
  references: [{ title: "Manuale", path: "IT/manuale.pdf", score: 3.2, knowledge_source_type: "indexedSharePoint" }] });
  const [, metadata, call, sources] = trace.snapshot().messages[1].parts;
  const args = metadata.timeline.find((item) => item.sensitive);
  assert.match(args.detail, /pompa P-200/);
  assert.doesNotMatch(args.detail, /k1/);
  assert.deepEqual(metadata.milestones, { first_event_ms: 5, first_tool_ms: 40, first_token_ms: 300, total_ms: 900 });
  assert.deepEqual(metadata.phases.find((item) => item.phase === "tool"), { phase: "tool", start_ms: 40, end_ms: 70, events: 2, duration_ms: 30 });
  assert.equal(call.duration_ms, 210);
  assert.equal(call.arguments, "{\"query\":\"pompa P-200\"}");
  assert.equal(sources.sources[0].extra.score, 3.2);
  assert.equal(sources.sources[0].extra.path, "IT/manuale.pdf");
  const parsed = parseTrace(JSON.stringify(trace.snapshot())).messages[1].parts.find((part) => part.type === "tool_call");
  assert.deepEqual(parsed.arguments, { query: "pompa P-200" });
  assert.equal(parsed.duration_ms, 210);
});

test("waterfall preserves observed offsets, overlapping tools and retrieval activities", async () => {
  const { buildWaterfall } = await server.ssrLoadModule(path.join(frontend, "src", "trace", "waterfall.ts"));
  const trace = createCollector();
  const id = trace.begin({ question: "Pompa?", label: "Agente", mode: "agents" });
  trace.finish(id, {
    answer: "ok", elapsed_ms: 1000,
    timeline: [
      { at_ms: 10, phase: "agent", title: "Richiesta inviata" },
      { at_ms: 400, phase: "generation", title: "Scrittura della risposta" },
      { at_ms: 700, phase: "generation", title: "Generazione terminata" },
    ],
    activity: [{ id: 1, type: "azureBlob", knowledgeSourceName: "Manuali",
      started_ms: 80, completed_ms: 180, duration_ms: 100 }],
    tool_calls: [
      { id: "a", type: "mcp_call", name: "retrieve", status: "completed", started_ms: 40, completed_ms: 250, duration_ms: 210 },
      { id: "b", type: "mcp_call", name: "stock", status: "completed", started_ms: 100, completed_ms: 400, duration_ms: 300 },
    ],
  });
  const graph = buildWaterfall(parseTrace(JSON.stringify(trace.snapshot())).messages[1]);
  assert.equal(graph.extentMs, 1000);
  assert.equal(graph.rows[0].kind, "request");
  assert.deepEqual([graph.rows[0].startMs, graph.rows[0].endMs, graph.rows[0].durationMs], [0, 1000, 1000]);
  const retrieve = graph.rows.find((row) => row.kind === "tool" && row.label.includes("retrieve"));
  const stock = graph.rows.find((row) => row.kind === "tool" && row.label.includes("stock"));
  assert.deepEqual([retrieve.startMs, retrieve.endMs, retrieve.durationMs], [40, 250, 210]);
  assert.deepEqual([stock.startMs, stock.endMs, stock.durationMs], [100, 400, 300]);
  const activity = graph.rows.find((row) => row.kind === "activity");
  assert.deepEqual([activity.startMs, activity.endMs, activity.durationMs], [80, 180, 100]);
  const generation = graph.rows.find((row) => row.kind === "generation");
  assert.deepEqual([generation.startMs, generation.endMs, generation.durationMs, generation.timing], [400, 700, 300, "events"]);
});

test("waterfall leaves incomplete or inconsistent timings unavailable", async () => {
  const { buildWaterfall } = await server.ssrLoadModule(path.join(frontend, "src", "trace", "waterfall.ts"));
  const trace = createCollector();
  const id = trace.begin({ question: "Pompa?", label: "Agente", mode: "agents" });
  trace.update(id, {
    answer: "", timeline: [
      { at_ms: 20, phase: "tool", title: "Chiamata tool avviata" },
      { at_ms: 120, phase: "generation", title: "Primo token della risposta" },
    ],
    tool_calls: [
      { id: "pending", type: "mcp_call", name: "pending", status: "in_progress", started_ms: 20 },
      { id: "missing", type: "mcp_call", name: "missing", status: "completed", duration_ms: 15 },
      { id: "invalid", type: "mcp_call", name: "invalid", status: "completed", started_ms: 90, completed_ms: 40, duration_ms: 50 },
    ],
  });
  const graph = buildWaterfall(parseTrace(JSON.stringify(trace.snapshot())).messages[1]);
  assert.equal(graph.rows[0].timing, "observed");
  const pending = graph.rows.find((row) => row.label.includes("pending"));
  assert.equal(pending.startMs, 20);
  assert.equal(pending.endMs, undefined);
  assert.equal(pending.durationMs, undefined);
  const missing = graph.rows.find((row) => row.label.includes("missing"));
  assert.equal(missing.startMs, undefined);
  assert.equal(missing.durationMs, 15);
  const invalid = graph.rows.find((row) => row.label.includes("invalid"));
  assert.equal(invalid.timing, "invalid");
  assert.equal(invalid.endMs, undefined);
  const generation = graph.rows.find((row) => row.kind === "generation");
  assert.equal(generation.startMs, 120);
  assert.equal(generation.durationMs, undefined);
});

test("failed non-streaming retry keeps the fallback warning next to the error", () => {
  const trace = createCollector();
  const id = trace.begin({ question: "Ricambi?", label: "RAG accurato", mode: "agents" });
  trace.update(id, { answer: "", streaming: { mode: "json_retry", warning: "Retry senza streaming",
    usage_scope: "Token del retry" }, activity: [{ id: "a1", status: "in_progress" }] });
  trace.finish(id, null, new Error("Errore retry"));
  const [event, metadata, ...rest] = trace.snapshot().messages[1].parts;
  assert.equal(event.status, "failed");
  assert.equal(metadata.streaming.mode, "json_retry");
  assert.equal(metadata.streaming.warning, "Retry senza streaming");
  assert.equal(metadata.activity[0].status, "in_progress");
  assert.equal(rest.at(-1).error, "Errore retry");
});

test("parallel responses keep their branch and start order, including failures", () => {
  const trace = createCollector();
  const acl = trace.begin({ question: "Ricambi?", label: "Con ACL", mode: "compare" });
  const noacl = trace.begin({ question: "Ricambi?", label: "Senza ACL", mode: "compare" });
  trace.finish(noacl, null, new Error("Retrieve 502"));
  trace.finish(acl, { answer: "Disponibile", activity: [{ count: 1, knowledgeSourceName: "Manuali" }],
    execution: { acl_requested: true, user_token_forwarded: true } });
  const messages = trace.snapshot().messages;
  assert.equal(messages[1].name, "Con ACL");
  assert.equal(messages[1].parts[0].status, "completed");
  assert.equal(messages[3].parts[0].status, "failed");
  assert.equal(messages[1].parts[1].execution.acl_requested, true);
  assert.equal(messages[3].parts[1].error, "Retrieve 502");
});

test("new chat discards pending results without contaminating a new request", () => {
  const trace = createCollector();
  const old = trace.begin({ question: "Prima" });
  trace.clear();
  const next = trace.begin({ question: "Dopo" });
  trace.finish(old, { answer: "Vecchia" });
  assert.equal(trace.snapshot().messages.length, 2);
  assert.equal(trace.snapshot().messages[1].parts[0].status, "in_progress");
  trace.finish(next, { answer: "Nuova" });
  assert.match(JSON.stringify(trace.snapshot()), /Nuova/);
  assert.doesNotMatch(JSON.stringify(trace.snapshot()), /Vecchia/);
});

test("projects response fields only and redacts credentials and signed URLs", () => {
  const trace = createCollector();
  const jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature";
  const id = trace.begin({ question: `Bearer opaque ${jwt}`, user: { access_token: "private" } });
  trace.finish(id, { answer: 'access_token="opaque-secret" https://user:pass@example.com/a?sig=signed#token',
    access_token: "private", headers: { authorization: "private" },
    execution: { identity: "project", access_token: "private" },
    tool_calls: [{ name: "Search", type: "function_call", arguments: "{\"token\":\"Bearer private\"}", status: "completed" }],
    references: [{ title: "Manuale", url: "https://example.com/doc?sig=signed" }],
    usage: { total_tokens: 0, id_token: "private" } });
  const text = JSON.stringify(trace.snapshot());
  assert.doesNotMatch(text, /private|opaque-secret|signed|user:pass|eyJ/);
  assert.match(text, /total_tokens":0/);
  assert.match(text, /https:\/\/example.com\/doc/);
  assert.equal(safeText("api_key=secret-value"), "api_key=[omesso]");
});

test("real observed calls and references parse without inventing tool outputs", () => {
  const trace = createCollector();
  const id = trace.begin({ question: "Manuale?", label: "Agente" });
  trace.finish(id, { answer: "Vedi manuale", elapsed_ms: 42,
    tool_calls: [{ type: "mcp_call", name: "retrieve", server: "iq", status: "completed" }],
    citations: [{ title: "Manuale", url: "https://example.com/manuale" }] });
  const parsed = parseTrace(JSON.stringify(trace.snapshot()));
  assert.equal(parsed.stats.toolCalls, 1);
  assert.equal(parsed.stats.toolResponses, 0);
  assert.equal(parsed.stats.sources, 1);
  assert.equal(parsed.stats.documents, 1);
  const call = parsed.messages[1].parts.find((part) => part.type === "tool_call");
  assert.equal(call.server, "iq");
  assert.equal(call.status, "completed");
  assert.equal(call.arguments, undefined);
});

test("agent trace keeps actual reference IDs without inventing citation markers", () => {
  const trace = createCollector();
  const id = trace.begin({ question: "Manuale?", label: "Agente" });
  trace.finish(id, {
    answer: "Testo", references: [
      { id: 0, title: "Manuale", tool_call: 2, marker: "【3:2†source】" },
      { id: 1, title: "Secondo manuale" },
    ],
    citations: [{ title: "Guida", url: "https://example.com/guide" }],
  });
  const sources = parseTrace(JSON.stringify(trace.snapshot())).messages[1].parts.find((part) => part.type === "retrieved_sources").sources;
  assert.equal(sources[0].marker, "【3:2†source】");
  assert.equal(sources[0].extra.tool_call, 2);
  assert.equal(sources[1].marker, "Rif. 1");
  assert.equal(sources[2].marker, "Citazione");
});

test("Foundry tool responses expose document, page and snippet from source markers", () => {
  const body = JSON.stringify({ uid: "idx_aHR0cHM6Ly9hY2N0LmJsb2IuY29yZS53aW5kb3dzLm5ldC9kb2NzL01hbnVhbGUlMjBQb21wYS5wZGY0_pages_3", snippet: "Coppia 12 Nm" });
  const parsed = parseTrace(JSON.stringify([{ role: "tool", content: [
    { type: "function_call", call_id: "c1", function: { name: "search", arguments: "{\"q\":\"pompa\"}" } },
    { type: "function_call_output", call_id: "c1", output: `Risultati:\n【3:0†source】${body}\nfine` },
  ] }]));
  const [call, response] = parsed.messages[0].parts;
  assert.deepEqual(call.arguments, { q: "pompa" });
  assert.equal(response.header, "Risultati:");
  assert.equal(response.trailer, "fine");
  assert.equal(response.sources[0].document, "Manuale Pompa.pdf");
  assert.equal(response.sources[0].page, 3);
  assert.equal(response.sources[0].snippet, "Coppia 12 Nm");
  assert.equal(parsed.stats.documents, 1);
});
