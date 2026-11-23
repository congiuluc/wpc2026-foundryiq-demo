// Isolated browser smoke test: no Azure calls or existing browser profile are used.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn } = require("node:child_process");
const { setTimeout: sleep } = require("node:timers/promises");

const root = path.resolve(__dirname, "..");
const chrome = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const examples = Array.from({ length: 6 }, (_, index) => ({
  label: `Esempio ${index + 1}`,
  question: `Domanda esempio ${index + 1}`,
}));
const config = {
  user: "Demo User", user_source: "test", user_token_available: true, running_on_app_service: false,
  user_profile: { display_name: "Demo User", object_id: "user-1", tenant_id: "tenant-1", groups: ["group-1", "group-2"], groups_status: "available", roles: [] },
  knowledge_bases: [
    { id: "demo", name: "demo", label: "Demo", examples, tests: examples },
    { id: "acl", name: "acl", label: "ACL", examples: [], tests: [] },
  ],
  agents: [{ name: "one", label: "Agente uno", kind: "foundryiq", description: "Test" }, { name: "two", label: "Agente due", kind: "classic", description: "Test" }],
};
const received = [];
const groupNameRequests = [];
const groupClaims = { object_id: "user-1", tenant_id: "tenant-1", groups: ["group-1", "group-2"], groups_status: "available" };
let groupNamesResponse = { ...groupClaims, names: { "group-1": "Tecnici", "group-2": "Direzione" }, status: "available", message: null };
let groupNamesHttpStatus = 200;
function reply(req, res, body) {
  if (req.url === "/api/config") return res.end(JSON.stringify(config));
  if (req.url === "/api/profile/group-names") {
    groupNameRequests.push(req.url);
    res.statusCode = groupNamesHttpStatus;
    return res.end(JSON.stringify(groupNamesResponse));
  }
  if (req.url === "/api/chat" || req.url === "/api/agent-chat") {
    received.push(body);
    const agent = req.url === "/api/agent-chat";
    const question = body.messages.at(-1).content;
    if (question === "Errore trace") {
      res.statusCode = 502;
      return setTimeout(() => res.end(JSON.stringify({ detail: "Errore simulato" })), 300);
    }
    const agentAnswer = `Risposta ${body.agent}: guida [0] e [ref_id:0] e 【6:3†source】`;
    const response = agent
      ? {
          agent: body.agent, answer: agentAnswer,
          tool_calls: [{ id: "tool-1", type: "mcp_call", name: "tool-demo", server: "mcp", status: "completed",
            started_ms: 150, completed_ms: 450, duration_ms: 300 }],
          citations: [{ title: `Guida web ${body.agent}`, url: `https://example.test/${body.agent}`, type: "url_citation" }],
          citation_spans: [{ start: agentAnswer.indexOf("guida"), end: agentAnswer.indexOf("guida") + 5, citation_index: 0 }],
          references: [
            { id: 0, title: `Manuale MCP ${body.agent}`, marker: "【6:3†source】", tool_call: 1 },
            { id: 1, title: "Fonte aggiuntiva 1" }, { id: 2, title: "Fonte aggiuntiva 2" }, { id: 3, title: "Fonte aggiuntiva 3" },
          ],
          elapsed_ms: 1500, usage: { total_tokens: 40 }, execution: { identity: "Identità del progetto" },
        }
      : { answer: `Risposta ${body.use_acl ? "ACL" : "pubblica"} [0]`, references: [{ id: 0, title: body.use_acl ? "Manuale ACL" : "Manuale pubblico", url: "https://example.test/manual" }], activity: [{ id: 1, type: "modelQueryPlanning", status: "completed", started_ms: 20, completed_ms: 80, duration_ms: 60 }], sources: [{ name: "Manuali", type: "azureBlob", queries: 1, results: 1 }], elapsed_ms: 120, knowledge_base: body.knowledge_base, execution: { identity: "Credenziale Azure locale", acl_requested: body.use_acl, user_token_forwarded: body.use_acl, synthesis_performed: true, chat_deployment: "test" } };
    const delay = question.startsWith("Attendi") ? 1600 : 80;
    if (agent && question === "Retry con attività precedente") {
      res.setHeader("Content-Type", "text/event-stream");
      const event = (name, data) => res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
      event("activity", { id: 7, type: "mcpServer", tool_call_id: "kb", tool_call: 1, mcp_tool: "stale_tool" });
      event("status", { stage: "Retry", result: { answer: "", streaming: { mode: "json_retry" } } });
      event("completed", { ...response, answer: "Retry completato", citations: [], citation_spans: [],
        tool_calls: [{ id: "kb", type: "mcp_call", name: "knowledge_base_retrieve" }],
        references: [{ id: 0, title: "Nuovo riferimento", type: "mcpServer", activity_source: 7,
          tool_call_id: "kb", tool_call: 1 }], activity: [] });
      return res.end();
    }
    if (agent && question === "ID attività ambigui") {
      res.setHeader("Content-Type", "text/event-stream");
      const event = (name, data) => res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
      const reference = { id: 0, title: "Riferimento ambiguo", type: "mcpServer", activity_source: 7,
        tool_call_id: "kb", tool_call: 1 };
      event("activity", { id: 7, type: "mcpServer", tool_call_id: "kb", tool_call: 1, mcp_tool: "first_call" });
      event("activity", { id: "7", type: "mcpServer", tool_call_id: "kb", tool_call: 1, mcp_tool: "second_call" });
      event("references", { items: [reference] });
      event("completed", { ...response, answer: "Attività ambigue", citations: [], citation_spans: [],
        tool_calls: [{ id: "kb", type: "mcp_call", name: "knowledge_base_retrieve" }],
        references: [reference], activity: [] });
      return res.end();
    }
    if (["Streaming live", "Streaming errore", "Streaming interrotto", "Tool solo nello stream"].includes(question)) {
      assert.equal(body.stream, true);
      res.setHeader("Content-Type", "text/event-stream");
      const event = (name, data) => res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
      event("status", { stage: "Retrieval Foundry IQ", result: { answer: "" } });
      event(agent ? "tool" : "activity", agent
        ? { id: "tool-1", type: "mcp_call", name: "tool-demo", status: "in_progress", started_ms: 150 }
        : { id: 1, type: "modelQueryPlanning", status: "in_progress", started_ms: 20 });
      event("log", { at_ms: agent ? 150 : 20, phase: agent ? "tool" : "retrieval", title: "Attività avviata" });
      event("delta", { text: response.answer.slice(0, 8) });
      if (question === "Streaming errore" || question === "Streaming interrotto") {
        if (question === "Streaming errore") event("error", { message: "Errore upstream nello stream" });
        return res.end();
      }
      return setTimeout(() => {
        event(agent ? "tool" : "activity", agent ? response.tool_calls[0] : response.activity[0]);
        event("completed", agent && question === "Tool solo nello stream" ? { ...response, tool_calls: [] } : response);
        res.end();
      }, 1200);
    }
    return setTimeout(() => res.end(JSON.stringify(response)), delay);
  }
  const relative = req.url.split("?")[0];
  const base = path.join(root, "frontend", "dist");
  const name = relative === "/" || relative === "/trace" || ["/demo/multi-fonte", "/demo/acl", "/demo/agenti"].includes(relative) ? "index.html" : relative.slice(1);
  const file = path.resolve(base, name);
  if (!file.startsWith(base + path.sep) || !fs.existsSync(file)) { res.statusCode = 404; return res.end(); }
  const mime = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
  res.setHeader("Content-Type", mime[path.extname(file)] || "application/octet-stream");
  fs.createReadStream(file).pipe(res);
}
function client(url) {
  const ws = new WebSocket(url), pending = new Map(); let seq = 0;
  const ready = new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  ws.onmessage = ({ data }) => {
    const msg = JSON.parse(data); const item = pending.get(msg.id);
    if (item) { pending.delete(msg.id); msg.error ? item.reject(new Error(msg.error.message)) : item.resolve(msg.result); }
  };
  return {
    ready,
    async call(method, params = {}) {
      await ready;
      return new Promise((resolve, reject) => { const id = ++seq; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
    },
    close() { ws.close(); },
  };
}
async function evaluate(c, expression) {
  const result = await c.call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function wait(c, expression, timeout = 5000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await evaluate(c, expression)) return; await sleep(50); }
  throw new Error(`Timed out: ${expression}`);
}
async function setValue(c, selector, value) {
  await evaluate(c, `(() => { const e = document.querySelector(${JSON.stringify(selector)}); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(e, ${JSON.stringify(value)}); e.dispatchEvent(new Event('input', { bubbles: true })); })()`);
}

test("React chat, current and manual trace, and presentation controls", { timeout: 45000 }, async () => {
  assert.ok(fs.existsSync(chrome), `Chrome non trovato: imposta CHROME_PATH (${chrome})`);
  assert.ok(fs.existsSync(path.join(root, "frontend", "dist", "index.html")), "Esegui prima npm run build");
  const server = http.createServer((req, res) => {
    let data = ""; req.on("data", (chunk) => data += chunk);
    req.on("end", () => { if (req.url.startsWith("/api/")) res.setHeader("Content-Type", "application/json"); reply(req, res, data ? JSON.parse(data) : null); });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "wpc-react-test-"));
  const browser = spawn(chrome, ["--headless=new", "--disable-gpu", "--disable-background-timer-throttling", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  const clients = [];
  try {
    const portFile = path.join(profile, "DevToolsActivePort");
    let port;
    for (let i = 0; i < 100 && !port; i++) {
      try { port = fs.readFileSync(portFile, "utf8").split("\n")[0]; }
      catch (error) { if (!["ENOENT", "EBUSY"].includes(error.code)) throw error; }
      if (!port) await sleep(100);
    }
    assert.ok(port, "Chrome non ha avviato DevTools");
    const endpoint = `http://127.0.0.1:${port}`;
    const target = await (await fetch(`${endpoint}/json/new?${encodeURIComponent(origin)}`, { method: "PUT" })).json();
    const page = client(target.webSocketDebuggerUrl); clients.push(page);
    await page.call("Emulation.setDeviceMetricsOverride", { width: 1365, height: 768, deviceScaleFactor: 1, mobile: false });
    const openTrace = async () => {
      await evaluate(page, "document.querySelector('[aria-label=\"Visualizza trace corrente\"]').click()");
      await wait(page, "document.querySelector('.trace-dialog')?.open");
    };
    const closeTrace = async () => {
      await evaluate(page, "document.querySelector('.trace-dialog .dialog-actions button').click()");
      await wait(page, "!document.querySelector('.trace-dialog').open");
      await sleep(100);
    };
    const openProfile = async () => {
      await evaluate(page, "document.querySelector('.user').click()");
      await wait(page, "document.querySelector('.profile-dialog').open");
    };
    const closeProfile = async () => {
      await evaluate(page, "document.querySelector('.profile-dialog .dialog-actions button').click()");
      await wait(page, "!document.querySelector('.profile-dialog').open");
      await sleep(100);
    };
    await wait(page, "!!document.querySelector('#question') && !!document.querySelector('#kb option')");
    await wait(page, "!document.querySelector('[href^=\"/static/\"], [src^=\"/static/\"]') && [...document.styleSheets].some(sheet => sheet.href?.includes('/assets/') && sheet.cssRules.length > 0)");
    await wait(page, "document.querySelectorAll('.starter-card').length === 6");
    const starterLayout = await evaluate(page, `(() => {
      const cards = [...document.querySelectorAll('.starter-card')].map(card => {
        const { left, top, bottom } = card.getBoundingClientRect();
        return { left, top, bottom };
      });
      return { cards, composerTop: document.querySelector('.composer-wrap').getBoundingClientRect().top };
    })()`);
    assert.equal(starterLayout.cards.length, 6);
    assert.ok(starterLayout.cards.slice(0, 3).every(card => card.top === starterLayout.cards[0].top));
    assert.ok(starterLayout.cards.slice(3).every(card => card.top === starterLayout.cards[3].top));
    assert.ok(starterLayout.cards[3].top > starterLayout.cards[0].top);
    assert.ok(starterLayout.cards.slice(0, 3).every((card, index) => card.left === starterLayout.cards[index + 3].left));
    assert.ok(starterLayout.cards[5].bottom <= starterLayout.composerTop);
    await evaluate(page, "document.querySelectorAll('.starter-card')[4].click()");
    await wait(page, "document.querySelector('#question').value === 'Domanda esempio 5'");
    await page.call("Input.dispatchKeyEvent", { type: "keyDown", key: "6", code: "Digit6", modifiers: 1, windowsVirtualKeyCode: 54 });
    await page.call("Input.dispatchKeyEvent", { type: "keyUp", key: "6", code: "Digit6", modifiers: 1, windowsVirtualKeyCode: 54 });
    await wait(page, "document.querySelector('#question').value === 'Domanda esempio 6'");
    await page.call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
    assert.equal(await evaluate(page, "document.documentElement.scrollWidth <= innerWidth + 1 && document.querySelectorAll('.starter-card').length === 6"), true);
    await page.call("Emulation.setDeviceMetricsOverride", { width: 1365, height: 768, deviceScaleFactor: 1, mobile: false });
    assert.equal(await evaluate(page, "document.querySelector('.tests-wrap').classList.contains('collapsed') && document.querySelector('#tests').getAttribute('aria-hidden') === 'true' && !!document.querySelector('.tests-edge-tab')"), true);
    await evaluate(page, "document.querySelector('.tests-edge-tab').click()");
    await wait(page, "document.querySelector('.tests-wrap').classList.contains('open') && document.querySelector('#tests').getAttribute('aria-hidden') === 'false'");
    await evaluate(page, "document.querySelector('.tests-hide-btn').click()");
    await wait(page, "document.querySelector('.tests-wrap').classList.contains('collapsed') && !!document.querySelector('.tests-edge-tab')");
    assert.equal(groupNameRequests.length, 0);
    await openProfile();
    await wait(page, "document.querySelector('.profile-dialog').textContent.includes('Tecnici')");
    assert.equal(await evaluate(page, `(() => {
      const groups = document.querySelector('.profile-groups').textContent;
      return groups.includes('Tecnici') && groups.includes('Direzione') && groups.includes('group-1') && groups.includes('group-2');
    })()`), true);
    await closeProfile();
    groupNamesResponse = { ...groupClaims, names: { "group-1": "Tecnici" }, status: "partial", message: "Graph: Group.ReadBasic.All richiesto." };
    await openProfile();
    await wait(page, "document.querySelector('.profile-dialog [role=status]')?.textContent.includes('Group.ReadBasic.All')");
    assert.equal(await evaluate(page, `(() => {
      const groups = document.querySelector('.profile-groups').textContent;
      return groups.includes('Tecnici') && groups.includes('group-1') && groups.includes('group-2') && !groups.includes('Direzione');
    })()`), true);
    await closeProfile();
    groupNamesResponse = { ...groupClaims, names: {}, status: "unavailable", message: "Accesso a Graph negato." };
    await openProfile();
    await wait(page, "document.querySelector('.profile-dialog [role=status]')?.textContent.includes('Accesso a Graph negato')");
    assert.equal(await evaluate(page, "document.querySelector('.profile-groups').textContent.includes('group-1') && document.querySelector('.profile-groups').textContent.includes('group-2')"), true);
    await closeProfile();
    groupNamesHttpStatus = 503;
    await openProfile();
    await wait(page, "document.querySelector('.profile-dialog [role=status]')?.textContent.includes('HTTP 503')");
    assert.equal(await evaluate(page, "document.querySelector('.profile-groups').textContent.includes('group-1')"), true);
    await closeProfile();
    groupNamesHttpStatus = 200;
    groupNamesResponse = { ...groupClaims, names: null, status: "available", message: null };
    await openProfile();
    await wait(page, "document.querySelector('.profile-dialog [role=status]')?.textContent.includes('risposta non valida')");
    await closeProfile();
    groupNamesResponse = { ...groupClaims, groups: ["group-2", "group-3"], groups_status: "overage",
      names: { "group-2": "Direzione", "group-3": "Amministrazione" }, status: "available", message: null };
    await openProfile();
    await wait(page, "document.querySelector('.profile-dialog').textContent.includes('Amministrazione')");
    assert.equal(await evaluate(page, `(() => {
      const dialog = document.querySelector('.profile-dialog');
      const groups = dialog.querySelector('.profile-groups').textContent;
      return dialog.textContent.includes('Elenco incompleto') && groups.includes('group-2') && groups.includes('group-3') && !groups.includes('group-1');
    })()`), true);
    await closeProfile();
    groupNamesResponse = { ...groupClaims, object_id: "user-2", names: { "group-1": "Altro utente" }, status: "available", message: null };
    await openProfile();
    await wait(page, "document.querySelector('.profile-dialog [role=status]')?.textContent.includes('sessione è cambiata')");
    assert.equal(await evaluate(page, "!document.querySelector('.profile-dialog').textContent.includes('Altro utente')"), true);
    await closeProfile();
    groupNamesResponse = { ...groupClaims, groups: [], groups_status: "unavailable", names: {}, status: "available", message: null };
    await openProfile();
    await wait(page, "document.querySelector('.profile-dialog').textContent.includes('Gruppi non disponibili nei claim')");
    assert.equal(await evaluate(page, "!document.querySelector('.profile-groups')"), true);
    await closeProfile();
    const longGroup = "11111111-1111-4111-8111-111111111111";
    groupNamesResponse = { ...groupClaims, groups: [longGroup], names: { [longGroup]: `Gruppo-${"A".repeat(120)}` }, status: "available", message: null };
    await page.call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
    await openProfile();
    await wait(page, "document.querySelector('.profile-dialog').textContent.includes('Gruppo-AAAA')");
    assert.equal(await evaluate(page, `(() => {
      const dialog = document.querySelector('.profile-dialog');
      const list = dialog.querySelector('.profile-groups').getBoundingClientRect();
      const item = dialog.querySelector('.profile-groups li').getBoundingClientRect();
      return dialog.scrollWidth <= dialog.clientWidth + 1 && item.right <= list.right + 1;
    })()`), true);
    await closeProfile();
    await page.call("Emulation.setDeviceMetricsOverride", { width: 1365, height: 768, deviceScaleFactor: 1, mobile: false });
    groupNamesResponse = { ...groupClaims, names: { "group-1": "Tecnici", "group-2": "Direzione" }, status: "available", message: null };
    await page.call("Page.bringToFront");
    await evaluate(page, "window.__nativeOpen = window.open; window.__openCalls = 0; window.open = () => { window.__openCalls++; return null; }; document.querySelector('[aria-label=\"Visualizza trace corrente\"]').focus()");
    await openTrace();
    assert.equal(await evaluate(page, "document.querySelector('.trace-dialog').matches(':modal') && document.querySelector('.trace-dialog').contains(document.activeElement)"), true);
    assert.equal(await evaluate(page, "document.querySelector('.trace-dialog [role=status]').textContent.includes('non contiene ancora eventi')"), true);
    assert.equal(await evaluate(page, "window.__openCalls === 0 && location.pathname === '/'"), true);
    await page.call("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
    await page.call("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
    await wait(page, "!document.querySelector('.trace-dialog').open && document.activeElement?.getAttribute('aria-label') === 'Visualizza trace corrente'");
    for (const mode of ["noacl", "compare", "agents"]) {
      await evaluate(page, `document.querySelector('input[value=${mode}]').click()`);
      await setValue(page, "#question", "Streaming live");
      await evaluate(page, "document.querySelector('#form').requestSubmit()");
      await wait(page, "!!document.querySelector('.answer .text') && !!document.querySelector('.elapsed')");
      assert.equal(await evaluate(page, "document.querySelector('.answer .text').textContent.trim()"), "Risposta");
      if (mode !== "agents") {
      const consoleLayout = await evaluate(page, `(() => {
        const answer = document.querySelector('.answer');
        const output = answer.querySelector('.answer-output').getBoundingClientRect();
        const console = answer.querySelector('.timeline-console');
        const rect = console.getBoundingClientRect();
        return { open: console.open, events: console.querySelector('[role=log]').textContent,
          columns: getComputedStyle(answer.querySelector('.answer-processing')).gridTemplateColumns.split(' ').length,
          beside: rect.left >= output.right, below: rect.top >= output.bottom };
      })()`);
      assert.equal(consoleLayout.open, true);
      assert.match(consoleLayout.events, /Attività avviata/);
      assert.equal(consoleLayout.columns === 2 ? consoleLayout.beside : consoleLayout.below, true);
      if (mode === "noacl") {
        assert.equal(consoleLayout.columns, 2);
        await page.call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
        assert.equal(await evaluate(page, `(() => {
          const output = document.querySelector('.answer-output').getBoundingClientRect();
          const console = document.querySelector('.timeline-console').getBoundingClientRect();
          return console.top >= output.bottom && document.documentElement.scrollWidth <= innerWidth + 1;
        })()`), true);
        await page.call("Emulation.setDeviceMetricsOverride", { width: 1365, height: 768, deviceScaleFactor: 1, mobile: false });
      }
      } else {
        assert.equal(await evaluate(page, `Array.from(document.querySelectorAll('.answer')).every(answer => {
          const timeline = answer.querySelector('.timeline');
          return timeline.open && !answer.querySelector('.timeline-console')
            && !!(timeline.compareDocumentPosition(answer.querySelector('.agent-metrics')) & Node.DOCUMENT_POSITION_FOLLOWING);
        })`), true);
      }
      await openTrace();
      assert.equal(await evaluate(page, "document.querySelector('.trace-dialog #trace-view').textContent.includes('Risposta')"), true);
      assert.equal(await evaluate(page, "document.querySelector('.trace-waterfall')?.textContent.includes('osservati')"), true);
      assert.equal(await evaluate(page, "document.querySelector('.waterfall-request .waterfall-state')?.textContent"), "In corso");
      if (mode === "agents") {
        assert.match(await evaluate(page, "document.querySelector('.waterfall-tool .waterfall-state')?.textContent"), /ultimo evento/);
      }
      await closeTrace();
      await wait(page, "!document.querySelector('.elapsed')");
      assert.equal(await evaluate(page, "document.querySelectorAll('.answer').length"), mode === "noacl" ? 1 : 2);
      assert.equal(await evaluate(page, `Array.from(document.querySelectorAll('.answer')).every(answer => {
        const timeline = answer.querySelector('.timeline');
        return !timeline.open && !timeline.classList.contains('timeline-console')
          && (${mode === "agents"} || (!!(answer.querySelector('.text').compareDocumentPosition(timeline) & Node.DOCUMENT_POSITION_FOLLOWING)
          && !!(timeline.compareDocumentPosition(answer.querySelector('.references-details')) & Node.DOCUMENT_POSITION_FOLLOWING)));
      })`), true);
      if (mode === "agents") {
        await openTrace();
        const bars = await evaluate(page, `Array.from(document.querySelectorAll('.waterfall-tool')).map(row => {
          const track = row.querySelector('.waterfall-track').getBoundingClientRect();
          const bar = row.querySelector('.waterfall-bar').getBoundingClientRect();
          return { left: (bar.left - track.left) / track.width, width: bar.width / track.width, text: row.textContent };
        })`);
        assert.equal(bars.length, 2);
        for (const bar of bars) {
          assert.ok(Math.abs(bar.left - 0.1) < 0.01 && Math.abs(bar.width - 0.2) < 0.01, JSON.stringify(bar));
          assert.match(bar.text, /300 ms/);
        }
        await closeTrace();
      }
      await evaluate(page, "document.querySelector('#reset').click()");
    }
    await evaluate(page, "document.querySelector('input[value=noacl]').click()");
    for (const question of ["Streaming errore", "Streaming interrotto"]) {
      await setValue(page, "#question", question);
      await evaluate(page, "document.querySelector('#form').requestSubmit()");
      await wait(page, "!!document.querySelector('.answer-error-msg')");
      assert.equal(await evaluate(page, "document.querySelector('.answer .text').textContent.trim()"), "Risposta");
      assert.equal(await evaluate(page, "!document.querySelector('.elapsed')"), true);
      assert.equal(await evaluate(page, "!!document.querySelector('.timeline') && !document.querySelector('.timeline').open && !document.querySelector('.timeline-console')"), true);
      await evaluate(page, "document.querySelector('#reset').click()");
    }
    await setValue(page, "#question", "Domanda prova api_key=secret-demo-value");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "!!document.querySelector('.citation-link')");
    await wait(page, "document.querySelectorAll('img.src-icon').length > 0 && [...document.querySelectorAll('img.src-icon')].every(img => img.complete && img.naturalWidth > 0)");
    await page.call("Page.bringToFront");
    await evaluate(page, "document.querySelector('.citation-link').focus()");
    assert.equal(await evaluate(page, "document.activeElement.classList.contains('citation-link')"), true);
    await page.call("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", text: "\r", unmodifiedText: "\r", windowsVirtualKeyCode: 13 });
    await page.call("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
    await wait(page, "document.activeElement.classList.contains('reference-card')");
    assert.equal(await evaluate(page, "document.activeElement.classList.contains('reference-card') && document.querySelector('.references-details').open"), true);
    await evaluate(page, "document.querySelector('.infographic-open').click()");
    await wait(page, "document.querySelector('.infographic-dialog').open");
    assert.equal(await evaluate(page, "document.querySelector('.infographic-dialog svg').getAttribute('viewBox')"), "0 0 1600 900");
    await evaluate(page, "document.querySelector('.infographic-dialog .dialog-actions button:last-child').click()");
    await wait(page, "!document.querySelector('.infographic-dialog').open");
    await openTrace();
    await wait(page, "document.querySelector('.trace-dialog #trace-view')?.textContent.includes('Risposta pubblica')");
    assert.equal(await evaluate(page, "document.querySelector('.trace-dialog').textContent.includes('api_key=[omesso]') && !document.querySelector('.trace-dialog').textContent.includes('secret-demo-value')"), true);
    assert.equal(await evaluate(page, "document.querySelector('.trace-json pre').textContent.includes('Manuale pubblico') && window.__openCalls === 0"), true);
    await evaluate(page, "document.querySelector('.trace-dialog input[value=documents]').click()");
    await wait(page, "document.querySelector('.trace-dialog #trace-view')?.textContent.includes('Manuale pubblico')");
    await evaluate(page, "(() => { const e = document.querySelector('.trace-dialog input[type=search]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(e, 'Manuale'); e.dispatchEvent(new Event('input', { bubbles: true })); })()");
    await wait(page, "document.querySelector('.trace-dialog mark')?.textContent === 'Manuale'");
    await closeTrace();
    await evaluate(page, "document.querySelector('input[value=compare]').click()");
    await setValue(page, "#question", "Confronto");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "document.querySelectorAll('.answer .citation-link').length === 2");
    assert.equal(received.slice(-2).filter((r) => r.use_acl).length, 1);
    await openTrace();
    assert.equal(await evaluate(page, "(() => { const text = document.querySelector('.trace-dialog #trace-view').textContent; return text.includes('Risposta ACL') && text.includes('Risposta pubblica') && text.includes('Con ACL') && text.includes('Senza ACL'); })()"), true);
    await closeTrace();
    await evaluate(page, "document.querySelectorAll('.answer .citation-link')[0].click()");
    await wait(page, "document.activeElement.closest('.answer') === document.querySelectorAll('.answer')[0] && document.activeElement.textContent.includes('Manuale ACL')");
    await evaluate(page, "document.querySelectorAll('.answer .citation-link')[1].click()");
    await wait(page, "document.activeElement.closest('.answer') === document.querySelectorAll('.answer')[1] && document.activeElement.textContent.includes('Manuale pubblico')");
    await evaluate(page, "document.querySelector('input[value=agents]').click()");
    await setValue(page, "#question", "Agenti");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "document.querySelectorAll('.agent-metrics').length === 2");
    assert.equal(await evaluate(page, "document.querySelector('.agent-tool-summary').textContent.includes('tool-demo')"), true);
    assert.equal(await evaluate(page, `(() => {
      const cards = [...document.querySelectorAll('.answer')];
      return cards.every(card => card.querySelectorAll('.text .citation-link').length === 3
        && card.querySelector('.text').textContent.includes('[0]')
        && !card.querySelector('.references-details').open);
    })()`), true);
    await evaluate(page, `document.querySelectorAll('.answer')[0].querySelector('[aria-label^="Vai alla citazione C1"]').click()`);
    await wait(page, `document.activeElement.closest('.answer') === document.querySelectorAll('.answer')[0]
      && document.activeElement.textContent.includes('Guida web one')`);
    assert.equal(await evaluate(page, "!document.querySelectorAll('.answer')[1].querySelector('.references-details').open"), true);
    await evaluate(page, `document.querySelectorAll('.answer')[1].querySelector('[aria-label^="Vai al riferimento 0:"]').click()`);
    await wait(page, `document.activeElement.closest('.answer') === document.querySelectorAll('.answer')[1]
      && document.activeElement.textContent.includes('Manuale MCP two')`);
    await evaluate(page, `document.querySelectorAll('.answer')[0].querySelector('[aria-label^="Vai al riferimento 【6:3"]').click()`);
    await wait(page, `document.activeElement.closest('.answer') === document.querySelectorAll('.answer')[0]
      && document.activeElement.textContent.includes('Manuale MCP one')`);
    await openTrace();
    assert.equal(await evaluate(page, "(() => { const text = document.querySelector('.trace-dialog #trace-view').textContent; return text.includes('Risposta one') && text.includes('Risposta two') && text.includes('tool-demo'); })()"), true);
    await closeTrace();
    await setValue(page, "#question", "Tool solo nello stream");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "!document.querySelector('.elapsed') && document.querySelectorAll('.answer').length === 4");
    await openTrace();
    assert.equal(await evaluate(page, `(() => {
      const users = [...document.querySelectorAll('.trace-dialog .trace-message.role-user')]
        .filter(x => x.textContent.includes('Tool solo nello stream'));
      return users.length === 2 && users.every(x => x.nextElementSibling.querySelector('.waterfall-tool .waterfall-bar'));
    })()`), true);
    await closeTrace();
    await setValue(page, "#question", "Retry con attività precedente");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "!document.querySelector('.elapsed') && document.querySelectorAll('.answer').length === 6");
    assert.equal(await evaluate(page, `(() => {
      const cards = [...document.querySelectorAll('.answer')].slice(-2);
      return cards.every(card => card.textContent.includes('Nuovo riferimento') && !card.textContent.includes('stale_tool'));
    })()`), true);
    await setValue(page, "#question", "ID attività ambigui");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "!document.querySelector('.elapsed') && document.querySelectorAll('.answer').length === 8");
    assert.equal(await evaluate(page, `(() => {
      const cards = [...document.querySelectorAll('.answer')].slice(-2);
      return cards.every(card => {
        const reference = card.querySelector('.reference-card')?.textContent || '';
        const unlinked = card.querySelector('.reference-mcp-unlinked')?.textContent || '';
        return reference.includes('Riferimento ambiguo') && !reference.includes('first_call') && !reference.includes('second_call')
          && unlinked.includes('first_call') && unlinked.includes('second_call');
      });
    })()`), true);
    await setValue(page, "#question", "Attendi live");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "!!document.querySelector('.elapsed')");
    await sleep(250);
    assert.notEqual(await evaluate(page, "document.querySelector('.elapsed').textContent"), "0.0 s");
    await openTrace();
    await wait(page, `[...document.querySelectorAll('.trace-dialog .trace-message.role-user')].filter(x => x.textContent.includes('Attendi live')).some(x => x.nextElementSibling.textContent.includes('"status": "in_progress"'))`);
    await wait(page, `[...document.querySelectorAll('.trace-dialog .trace-message.role-user')].filter(x => x.textContent.includes('Attendi live')).length === 2 && [...document.querySelectorAll('.trace-dialog .trace-message.role-user')].filter(x => x.textContent.includes('Attendi live')).every(x => x.nextElementSibling.textContent.includes('"status": "completed"'))`);
    await closeTrace();
    await setValue(page, "#question", "Errore trace");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await openTrace();
    await wait(page, "document.querySelector('.trace-dialog #trace-view')?.textContent.includes('Errore simulato')");
    assert.equal(await evaluate(page, `document.querySelector('.trace-dialog #trace-view').textContent.includes('"status": "failed"')`), true);
    await closeTrace();
    await wait(page, "!document.querySelector('.elapsed')");
    await setValue(page, "#question", "Attendi reset");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "!!document.querySelector('.elapsed')");
    await evaluate(page, "document.querySelector('#reset').click()");
    await openTrace();
    await wait(page, "document.querySelector('.trace-dialog [role=status]')?.textContent.includes('non contiene ancora eventi')");
    await sleep(1800);
    assert.equal(await evaluate(page, "document.querySelectorAll('.answer').length"), 0);
    assert.equal(await evaluate(page, "document.querySelector('.trace-dialog [role=status]').textContent.includes('non contiene ancora eventi') && !document.querySelector('.trace-dialog #trace-view')"), true);
    await closeTrace();

    await evaluate(page, "window.open = window.__nativeOpen; window.__legacyTrace = window.open('/trace?current=1', '_blank'); true");
    let traceTarget;
    for (let i = 0; i < 100 && !traceTarget; i++) {
      traceTarget = (await (await fetch(`${endpoint}/json`)).json()).find((t) => t.url.startsWith(`${origin}/trace?current=1`));
      if (!traceTarget) await sleep(50);
    }
    assert.ok(traceTarget, "Pagina Trace di compatibilità non aperta");
    const trace = client(traceTarget.webSocketDebuggerUrl); clients.push(trace);
    await wait(trace, "!!document.querySelector('#trace-text') && !!window.opener");
    const historicalTrace = { type: "wpc-current-trace", trace: { messages: [{ role: "assistant", parts: [{ type: "text", text: "Snapshot storica" }] }] } };
    await evaluate(page, `window.__legacyTrace.postMessage(${JSON.stringify(historicalTrace)}, location.origin)`);
    await wait(trace, "document.querySelector('#trace-view')?.textContent.includes('Snapshot storica')");
    await evaluate(trace, "window.dispatchEvent(new MessageEvent('message', { origin: 'https://other.example', source: window.opener, data: { type: 'wpc-current-trace', trace: { messages: [] } } }))");
    await evaluate(trace, "window.dispatchEvent(new MessageEvent('message', { origin: location.origin, source: window, data: { type: 'wpc-current-trace', trace: { messages: [] } } }))");
    assert.equal(await evaluate(trace, "document.querySelector('#trace-view').textContent.includes('Snapshot storica')"), true);
    await evaluate(trace, "[...document.querySelectorAll('button')].find(x => x.textContent === 'Pulisci').click()");
    await wait(trace, "document.querySelector('#trace-text').value === '' && !document.querySelector('#trace-view')");
    await evaluate(trace, "window.dispatchEvent(new MessageEvent('message', { origin: location.origin, source: window.opener, data: { type: 'wpc-current-trace', trace: { messages: [{ role: 'assistant', parts: [{ type: 'text', text: 'Da ignorare' }] }] } } }))");
    await sleep(100);
    assert.equal(await evaluate(trace, "document.querySelector('#trace-text').value === '' && !document.querySelector('#trace-view')"), true);
    await setValue(trace, "#trace-text", JSON.stringify({ role: "assistant", parts: [
      { type: "response_metadata", elapsed_ms: 1000 },
      { type: "tool_call", name: "search", started_ms: 200, duration_ms: 300 },
      { type: "tool_call_response", response: '【1:0†source】{"title":"Documento","snippet":"testo ricercabile"}' },
    ] }));
    await evaluate(trace, "[...document.querySelectorAll('button')].find(x => x.textContent === 'Visualizza').click()");
    await wait(trace, "document.querySelector('#trace-view')?.textContent.includes('Documento')");
    assert.deepEqual(await evaluate(trace, `(() => {
      const bar = document.querySelector('.waterfall-tool .waterfall-bar');
      return [parseFloat(bar.style.left), parseFloat(bar.style.width)];
    })()`), [20, 30]);
    await evaluate(trace, "document.querySelector('details.source summary').click()");
    await wait(trace, "document.querySelector('details.source').open");
    await evaluate(trace, "[...document.querySelectorAll('button')].find(x => x.textContent === 'Espandi tutto').click()");
    await wait(trace, "!document.querySelector('details.source').open");
    await evaluate(trace, "document.querySelector('input[value=documents]').click()");
    await wait(trace, "document.querySelector('#trace-view')?.textContent.includes('Pagina')");
    await evaluate(trace, "(() => { const e = document.querySelector('input[type=search]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(e, 'ricercabile'); e.dispatchEvent(new Event('input', { bubbles: true })); })()");
    await wait(trace, "document.querySelector('mark')?.textContent === 'ricercabile'");
    await evaluate(trace, "document.querySelector('input[type=checkbox]').click()");
    assert.equal(await evaluate(trace, "document.querySelector('#trace-view pre')?.textContent.includes('ricercabile')"), true);
    await evaluate(trace, "[...document.querySelectorAll('button')].find(x => x.textContent === 'Pulisci').click()");
    await wait(trace, "document.querySelector('#trace-text').value === '' && !document.querySelector('#trace-view')");
    await evaluate(page, "document.querySelector('[aria-label=\"Tema scuro\"]').click()");
    assert.equal(await evaluate(page, "document.documentElement.dataset.theme"), "dark");
    await setValue(page, "#question", "Vista mobile");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "document.querySelectorAll('.agent-metrics').length === 2");
    await page.call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
    for (let i = 0; i < 4; i++) await evaluate(page, "document.querySelector('[aria-label=\"Ingrandisci testo\"]').click()");
    await sleep(200);
    assert.equal(await evaluate(page, "document.querySelector('.scale-label').textContent"), "160%");
    assert.equal(await evaluate(page, "document.documentElement.scrollWidth <= innerWidth + 1"), true);
    await openTrace();
    const mobileTrace = await evaluate(page, "(() => { const dialog = document.querySelector('.trace-dialog'), body = dialog.querySelector('.trace-dialog-body'), right = body.getBoundingClientRect().right; return { right: dialog.getBoundingClientRect().right, width: innerWidth, dialogScroll: dialog.scrollWidth, dialogClient: dialog.clientWidth, bodyScroll: body.scrollWidth, bodyClient: body.clientWidth, contentHeight: body.scrollHeight, bodyHeight: body.clientHeight, overflowing: [...body.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > right + 1).slice(0, 10).map(e => [e.tagName, e.getAttribute('class'), Math.round(e.getBoundingClientRect().right)]) }; })()");
    assert.ok(mobileTrace.right <= mobileTrace.width + 1 && mobileTrace.dialogScroll <= mobileTrace.dialogClient + 1 && mobileTrace.bodyScroll <= mobileTrace.bodyClient + 1 && mobileTrace.contentHeight > mobileTrace.bodyHeight, JSON.stringify(mobileTrace));
    await evaluate(page, "document.querySelector('.trace-dialog-body').scrollTop = document.querySelector('.trace-dialog-body').scrollHeight");
    assert.equal(await evaluate(page, "(() => { const dialog = document.querySelector('.trace-dialog'), body = dialog.querySelector('.trace-dialog-body'), close = dialog.querySelector('.dialog-actions button').getBoundingClientRect(); return body.scrollTop > 0 && close.top >= dialog.getBoundingClientRect().top && close.bottom <= dialog.getBoundingClientRect().bottom; })()"), true);
    await page.call("Emulation.setDeviceMetricsOverride", { width: 320, height: 844, deviceScaleFactor: 1, mobile: false });
    assert.equal(await evaluate(page, "(() => { const dialog = document.querySelector('.trace-dialog'), body = dialog.querySelector('.trace-dialog-body'); return dialog.getBoundingClientRect().right <= innerWidth + 1 && body.scrollWidth <= body.clientWidth + 1; })()"), true);
    await closeTrace();

    await page.call("Page.navigate", { url: `${origin}/demo/acl?kb=demo&mode=agents` });
    await wait(page, "location.pathname === '/demo/acl' && document.querySelector('#send') && !document.querySelector('#send').disabled");
    assert.equal(await evaluate(page, "document.querySelector('input[value=compare]').checked && !document.querySelector('#kb') && !document.querySelector('input[value=agents]')"), true);
    assert.equal(await evaluate(page, "document.querySelector('.demo-nav [aria-current=page]').textContent"), "ACL");
    await setValue(page, "#question", "ACL scenario");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "document.querySelectorAll('.answer .citation-link').length === 2");
    assert.deepEqual(received.slice(-2).map((r) => [r.knowledge_base, r.use_acl]), [["acl", true], ["acl", false]]);
    await page.call("Page.reload");
    await sleep(250);
    await wait(page, "location.pathname === '/demo/acl' && document.querySelector('input[value=compare]')?.checked");

    await evaluate(page, "document.querySelector('.demo-nav a[href=\"/demo/multi-fonte\"]').click()");
    await wait(page, "location.pathname === '/demo/multi-fonte' && document.querySelector('#send') && !document.querySelector('#send').disabled");
    assert.equal(await evaluate(page, "document.documentElement.scrollWidth <= innerWidth + 1"), true);
    assert.equal(await evaluate(page, "document.querySelector('input[value=noacl]').checked && !document.querySelector('#kb')"), true);
    await setValue(page, "#question", "Multi scenario");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "!!document.querySelector('.citation-link')");
    assert.equal(received.at(-1).knowledge_base, "demo");
    assert.equal(received.at(-1).use_acl, false);

    await page.call("Page.navigate", { url: `${origin}/demo/agenti?mode=noacl` });
    await wait(page, "location.pathname === '/demo/agenti' && !!document.querySelector('#agents-picker input')");
    assert.equal(await evaluate(page, "!document.querySelector('input[name=mode]') && !document.querySelector('#kb')"), true);
    await setValue(page, "#question", "Agent scenario");
    await evaluate(page, "document.querySelector('#form').requestSubmit()");
    await wait(page, "document.querySelectorAll('.agent-metrics').length === 2");
    assert.deepEqual(received.slice(-2).map((r) => r.agent).sort(), ["one", "two"]);

    const bases = config.knowledge_bases;
    try {
      config.knowledge_bases = bases.filter((kb) => kb.id !== "acl");
      await page.call("Page.navigate", { url: `${origin}/demo/acl?kb=demo` });
      await wait(page, "location.pathname === '/demo/acl' && document.querySelector('.warning')?.textContent.includes('Knowledge base della demo non disponibile')");
      assert.equal(await evaluate(page, "document.querySelector('#send').disabled && !document.querySelector('#kb')"), true);
    } finally { config.knowledge_bases = bases; }
  } finally {
    for (const c of clients) c.close();
    browser.kill();
    await new Promise((resolve) => { if (browser.exitCode !== null) resolve(); else browser.once("exit", resolve); });
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(profile, { recursive: true, force: true });
  }
});
