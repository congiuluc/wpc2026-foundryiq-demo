"use strict";

const state = { config: null, history: [], agentHistory: {}, busy: false, testsDone: new Set() };
const $ = (id) => document.getElementById(id);

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Minimal markdown: headings, paragraphs, bold, inline code, links, bullet/numbered lists and citation markers.
function inlineMarkdown(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\s*\[ref_id:(\d+)\]/g, '<sup class="cite">$1</sup>');
}

function renderMarkdown(text) {
  const html = [];
  let list = null;
  let paragraph = [];
  const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };
  const flush = () => { if (paragraph.length) { html.push(`<p>${paragraph.join("<br>")}</p>`); paragraph = []; } };
  for (const raw of escapeHtml(text).split("\n")) {
    if (!raw.trim()) { flush(); closeList(); continue; }
    const line = inlineMarkdown(raw);
    const heading = /^\s*#{1,6}\s+(.*)$/.exec(line);
    if (heading) { flush(); closeList(); html.push(`<h3>${heading[1]}</h3>`); continue; }
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const item = bullet || numbered;
    if (item) {
      flush();
      const tag = bullet ? "ul" : "ol";
      if (list !== tag) { closeList(); html.push(`<${tag}>`); list = tag; }
      html.push(`<li>${item[1]}</li>`);
      continue;
    }
    closeList();
    paragraph.push(line);
  }
  flush();
  closeList();
  return html.join("");
}

// Live elapsed-time indicator for loading cards, useful while presenting.
function startTimer(card) {
  const started = performance.now();
  const id = setInterval(() => {
    const el = card.querySelector(".elapsed");
    if (!el || !card.classList.contains("loading")) { clearInterval(id); return; }
    el.textContent = `${((performance.now() - started) / 1000).toFixed(1)} s`;
  }, 100);
}

function currentMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

const MODE_HINTS = {
  noacl: "🌐 Interroga la knowledge base con l'identità dell'app: tutte le fonti, senza filtro per utente.",
  acl: "🔐 Usa il token dell'utente: Foundry IQ restituisce solo i documenti consentiti ai suoi gruppi Entra.",
  compare: "⚖️ Stessa domanda, due risposte affiancate: con il filtro ACL dell'utente e senza.",
  agents: "🧩 Agenti Foundry a confronto: RAG classico (indice + MCP + web) contro l'agente con la knowledge base Foundry IQ.",
};

function exampleItems() {
  // Agents use the demo sources, so they share the demo knowledge base examples.
  const kbId = currentMode() === "agents" ? "demo" : $("kb").value;
  const kb = state.config.knowledge_bases.find((k) => k.id === kbId) || state.config.knowledge_bases[0];
  return kb.examples.map((e) => (typeof e === "string" ? { label: e, question: e } : e));
}

function renderExamples() {
  const items = exampleItems();
  const chips = $("examples");
  const starter = $("starter");
  chips.replaceChildren();
  starter.replaceChildren();
  items.forEach((example, index) => {
    const key = index < 9 ? `<span class="key">${index + 1}</span>` : "";
    const chip = document.createElement("button");
    chip.type = "button";
    chip.title = `${example.question}${index < 9 ? ` (Alt+${index + 1})` : ""}`;
    chip.innerHTML = `${key}${escapeHtml(example.label)}`;
    chip.addEventListener("click", () => useExample(example.question));
    chips.appendChild(chip);

    const card = document.createElement("button");
    card.type = "button";
    card.className = "starter-card";
    card.innerHTML = `<span class="starter-title">${key}${escapeHtml(example.label)}</span><span class="starter-q">${escapeHtml(example.question)}</span>`;
    card.addEventListener("click", () => useExample(example.question));
    starter.appendChild(card);
  });
  renderTests();
}

const SOURCE_ICONS = { Manuali: "📄", Ricambi: "🧩", SharePoint: "🟦", Web: "🌐" };

function testItems() {
  const kbId = currentMode() === "agents" ? "demo" : $("kb").value;
  const kb = state.config.knowledge_bases.find((k) => k.id === kbId) || state.config.knowledge_bases[0];
  return { kb, items: kb.tests || exampleItems() };
}

function renderTests() {
  const { kb, items } = testItems();
  $("tests-kb").textContent = kb.label;
  $("tests-kb").title = kb.name;
  const list = $("tests-list");
  list.replaceChildren();
  items.forEach((test) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "test-item";
    button.title = test.question;
    button.classList.toggle("done", state.testsDone.has(test.question));
    const tags = (test.sources || [])
      .map((s) => `<span class="test-src">${SOURCE_ICONS[s] || "🔐"} ${escapeHtml(s)}</span>`)
      .join("");
    const aclGroups = test.expected_acl;
    const aclTag = Array.isArray(aclGroups)
      ? `<span class="test-acl" title="${escapeHtml(aclGroups.join(", "))}">🔐 ACL attesa: ${aclGroups.length > 1 ? "Tecnici + Direzione" : `Solo ${aclGroups[0] === "WPC FoundryIQ ACL - Tecnici" ? "Tecnici" : "Direzione"}`}</span>`
      : "";
    button.innerHTML = `<span class="test-label">${escapeHtml(test.label)}</span>`
      + `<span class="test-q">${escapeHtml(test.question)}</span>`
      + (tags || aclTag ? `<span class="test-srcs">${tags}${aclTag}</span>` : "");
    button.addEventListener("click", () => {
      if (state.busy) return;
      $("question").value = test.question;
      $("form").requestSubmit();
      if (window.matchMedia("(max-width: 1100px)").matches) setTestsOpen(false);
    });
    li.appendChild(button);
    list.appendChild(li);
  });
}

function setTestsOpen(open) {
  document.body.classList.toggle("tests-closed", !open);
  $("tests-toggle").setAttribute("aria-pressed", String(open));
  try { localStorage.setItem("wpc-tests-open", open ? "1" : "0"); } catch { /* storage unavailable */ }
}

function useExample(example) {
  $("question").value = example;
  autoGrow();
  $("question").focus();
}

function resetChat() {
  state.history = [];
  state.agentHistory = {};
  $("log").replaceChildren();
}

function renderAgents() {
  const list = $("agents-list");
  list.replaceChildren();
  for (const agent of state.config.agents || []) {
    const label = document.createElement("label");
    label.title = agent.description;
    label.innerHTML = `<input type="checkbox" name="agent" value="${escapeHtml(agent.name)}" checked> ${escapeHtml(agent.label)}`;
    list.appendChild(label);
  }
}

function selectedAgents() {
  const names = [...document.querySelectorAll('input[name="agent"]:checked')].map((i) => i.value);
  return (state.config.agents || []).filter((a) => names.includes(a.name));
}

function applyMode() {
  const mode = currentMode();
  const agentsMode = mode === "agents";
  $("agents-picker").hidden = !agentsMode;
  $("kb-picker").hidden = agentsMode;
  $("mode-hint").textContent = MODE_HINTS[mode] || "";
  resetChat();
  renderExamples();
}

async function loadConfig() {
  const response = await fetch("/api/config");
  state.config = await response.json();
  const select = $("kb");
  for (const kb of state.config.knowledge_bases) {
    const option = document.createElement("option");
    option.value = kb.id;
    option.textContent = kb.label;
    option.title = kb.name;
    select.appendChild(option);
  }
  const params = new URLSearchParams(location.search);
  if (params.get("kb")) select.value = params.get("kb");
  const modeRadio = document.querySelector(`input[name="mode"][value="${CSS.escape(params.get("mode") || "")}"]`);
  if (modeRadio) modeRadio.checked = true;
  renderAgents();
  applyMode();

  const name = state.config.user || "";
  const user = name ? `<strong>${escapeHtml(name)}</strong>` : "utente non identificato";
  const initial = escapeHtml((name.trim()[0] || "?").toUpperCase());
  $("user").innerHTML = `<span class="who">${user}<br>${escapeHtml(state.config.user_source || "")}</span><span class="avatar" aria-hidden="true">${initial}</span>`;
  if (!state.config.user_token_available) {
    const warning = $("warning");
    warning.hidden = false;
    warning.textContent = state.config.running_on_app_service
      ? "Token utente per Azure AI Search non disponibile: la modalità con ACL non funziona. Riesegui l'accesso da /.auth/login/aad oppure controlla la configurazione dell'autenticazione (scripts/setup_chat_auth.ps1)."
      : "Token utente non disponibile: esegui 'az login' sullo stesso tenant delle risorse e ricarica la pagina.";
  }
}

async function callChat(useAcl, messages, retried = false) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ knowledge_base: $("kb").value, messages, use_acl: useAcl }),
  });
  const payload = await response.json().catch(() => ({ detail: response.statusText }));
  if (response.status === 401 && payload.detail === "token_expired" && !retried) {
    // Easy Auth refreshes the stored tokens with the refresh token (offline_access).
    await fetch("/.auth/refresh");
    return callChat(useAcl, messages, true);
  }
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail));
  return payload;
}

function answerCard(useAcl) {
  const card = document.createElement("div");
  card.className = "answer loading";
  card.innerHTML = `<div class="head">${badge(useAcl)}<span>Ricerca in corso…</span><span class="meta elapsed">0.0 s</span></div>`;
  startTimer(card);
  return card;
}

function badge(useAcl) {
  return useAcl
    ? '<span class="badge acl">Con ACL</span>'
    : '<span class="badge noacl">Senza ACL</span>';
}

const SOURCE_KINDS = {
  azureBlob: ["blob", "Blob"],
  searchIndex: ["search", "Indice"],
  indexedSharePoint: ["sharepoint", "SharePoint"],
  remoteSharePoint: ["sharepoint", "SharePoint"],
  indexedOneLake: ["onelake", "OneLake"],
  web: ["web", "Web"],
  mcpServer: ["mcp", "MCP"],
};

function sourceIcon(type, name) {
  const [icon] = SOURCE_KINDS[type] || ["source"];
  const label = escapeHtml(name);
  return `<img class="src-icon" src="/static/icons/${icon}.svg" alt="${label}" title="${label}">`;
}

function renderSources(sources, unavailable = "Nomi delle knowledge source non disponibili nella risposta.") {
  if (!sources || !sources.length) {
    return `<div class="sources"><span class="sources-label">Fonti interrogate</span><span class="sources-unavailable">${escapeHtml(unavailable)}</span></div>`;
  }
  const chips = sources.map((s) => {
    const [, kind] = SOURCE_KINDS[s.type] || ["source", s.type || "?"];
    const state = s.error ? "err" : s.results ? "" : "empty";
    const detail = s.error ? "errore" : `${s.results} risultat${s.results === 1 ? "o" : "i"}`;
    const tip = `${s.name} · ${kind} · ${s.queries} query${s.error ? " · " + s.error : ""}`;
    return `<li class="src ${state}" title="${escapeHtml(tip)}">${sourceIcon(s.type, s.name)}<span class="src-name">${escapeHtml(s.name)}</span><span class="src-count">${escapeHtml(detail)}</span></li>`;
  }).join("");
  return `<div class="sources"><span class="sources-label">Fonti interrogate</span><ul>${chips}</ul></div>`;
}

function fillCard(card, result, useAcl) {
  card.className = "answer";
  const refs = result.references.map((r) => {
    const label = escapeHtml(r.title || r.id || "");
    const link = r.url ? `<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${label}</a>` : label;
    const path = r.path && !r.url ? ` <span class="meta">${escapeHtml(r.path)}</span>` : "";
    const source = r.knowledge_source
      ? `<span class="ref-source">${sourceIcon(r.knowledge_source_type || r.type, r.knowledge_source)}<span>${escapeHtml(r.knowledge_source)}</span></span>`
      : '<span class="ref-source unknown">Fonte non disponibile</span>';
    return `<li>${source} <span class="type">${escapeHtml(r.type || "?")}</span>[${escapeHtml(String(r.id))}] ${link}${path}</li>`;
  }).join("");
  card.innerHTML = `
    <div class="head">${badge(useAcl)}<span class="meta">${escapeHtml(result.knowledge_base)} · ${result.references.length} riferimenti · ${result.elapsed_ms} ms</span></div>
    <div class="text">${renderMarkdown(result.answer)}</div>
    ${renderSources(result.sources)}
    <details><summary>Riferimenti (${result.references.length})</summary>${refs ? `<ul class="refs">${refs}</ul>` : "<p>Nessun documento restituito.</p>"}</details>
    <details><summary>Attività Foundry IQ</summary><pre>${escapeHtml(JSON.stringify(result.activity, null, 2))}</pre></details>`;
}

function failCard(card, error, useAcl) {
  card.className = "answer error";
  card.innerHTML = `${badge(useAcl)} ${escapeHtml(error.message)}`;
}

async function callAgent(agent, messages) {
  const response = await fetch("/api/agent-chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent, messages }),
  });
  const payload = await response.json().catch(() => ({ detail: response.statusText }));
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail));
  return payload;
}

function agentBadge(agent) {
  const text = agent.kind === "foundryiq" ? "Foundry IQ" : "RAG classico";
  return `<span class="badge ${agent.kind === "foundryiq" ? "foundryiq" : "classic"}">${text}</span>`;
}

function agentHeader(agent) {
  return `<div class="head">${agentBadge(agent)}<strong>${escapeHtml(agent.label)}</strong></div>`;
}

function fillAgentCard(card, agent, result) {
  card.className = "answer";
  const tools = result.tool_calls.map((t) => {
    const name = [t.server, t.name].filter(Boolean).join(" · ");
    const args = t.arguments ? ` <span class="meta">${escapeHtml(t.arguments)}</span>` : "";
    const error = t.error ? ` <span class="meta">⚠ ${escapeHtml(t.error)}</span>` : "";
    return `<li class="${t.error ? "failed" : ""}"><span class="type">${escapeHtml(t.type)}</span>${escapeHtml(name)}${args}${error}</li>`;
  }).join("");
  const cites = result.citations.map((c) => {
    const label = escapeHtml(c.title || c.url || "");
    return `<li>${c.url ? `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${label}</a>` : label}</li>`;
  }).join("");
  const tokens = result.usage && result.usage.total_tokens ? ` · ${result.usage.total_tokens} token` : "";
  card.innerHTML = `
    ${agentHeader(agent)}
    <p class="agent-desc">${escapeHtml(agent.description)}</p>
    <div><span class="meta">${escapeHtml(result.agent)} · ${result.tool_calls.length} chiamate tool · ${result.elapsed_ms} ms${tokens}</span></div>
    <div class="text">${renderMarkdown(result.answer || "(nessuna risposta)")}</div>
    ${agent.kind === "foundryiq" ? renderSources(result.sources, "Nomi delle knowledge source non disponibili nella risposta dell'agente.") : ""}
    <details><summary>Chiamate tool (${result.tool_calls.length})</summary>${tools ? `<ul class="tools">${tools}</ul>` : "<p>Nessun tool chiamato: risposta dal solo modello.</p>"}</details>
    <details><summary>Citazioni (${result.citations.length})</summary>${cites ? `<ul class="refs">${cites}</ul>` : "<p>Nessuna citazione.</p>"}</details>`;
}

async function askAgents(question, assistantTurn) {
  const agents = selectedAgents();
  if (!agents.length) throw new Error("Seleziona almeno un agente.");
  if (agents.length > 1) assistantTurn.classList.add("compare");
  if (agents.length > 2) assistantTurn.classList.add("cols-3");
  await Promise.all(agents.map(async (agent) => {
    const card = document.createElement("div");
    card.className = "answer loading";
    card.innerHTML = `${agentHeader(agent)}<p class="meta">Esecuzione agente… <span class="elapsed">0.0 s</span></p>`;
    assistantTurn.appendChild(card);
    startTimer(card);
    // Each agent keeps its own conversation, as if the user were talking to it alone.
    const messages = [...(state.agentHistory[agent.name] || []), { role: "user", content: question }];
    try {
      const result = await callAgent(agent.name, messages);
      fillAgentCard(card, agent, result);
      state.agentHistory[agent.name] = [...messages, { role: "assistant", content: result.answer }].slice(-10);
    } catch (error) {
      card.className = "answer error";
      card.innerHTML = `${agentHeader(agent)} ${escapeHtml(error.message)}`;
    }
  }));
}

async function ask(question) {
  if (state.busy) return;
  state.busy = true;
  $("send").disabled = true;

  const userTurn = document.createElement("div");
  userTurn.className = "turn-user";
  userTurn.textContent = question;
  const assistantTurn = document.createElement("div");
  assistantTurn.className = "turn-assistant";
  $("log").append(userTurn, assistantTurn);
  // Keep the question at the top so the audience reads answers from the start.
  userTurn.scrollIntoView({ behavior: "smooth", block: "start" });

  const mode = currentMode();
  if (mode === "agents") {
    try {
      await askAgents(question, assistantTurn);
    } catch (error) {
      assistantTurn.innerHTML = `<div class="answer error">${escapeHtml(error.message)}</div>`;
    }
    state.busy = false;
    $("send").disabled = false;
    userTurn.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  const messages = [...state.history, { role: "user", content: question }];
  const variants = mode === "compare" ? [true, false] : [mode === "acl"];
  if (variants.length > 1) assistantTurn.classList.add("compare");

  const results = await Promise.all(variants.map(async (useAcl) => {
    const card = answerCard(useAcl);
    assistantTurn.appendChild(card);
    try {
      const result = await callChat(useAcl, messages);
      fillCard(card, result, useAcl);
      return result;
    } catch (error) {
      failCard(card, error, useAcl);
      return null;
    }
  }));

  // The conversation continues with the first answer (the ACL-filtered one in comparison mode).
  if (results[0]) {
    state.history = [...messages, { role: "assistant", content: results[0].answer }].slice(-10);
  }
  state.busy = false;
  $("send").disabled = false;
  userTurn.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $("question").value.trim();
  if (!question) return;
  $("question").value = "";
  autoGrow();
  state.testsDone.add(question);
  for (const item of document.querySelectorAll(".test-item")) {
    if (item.title === question) item.classList.add("done");
  }
  ask(question);
});
function autoGrow() {
  const box = $("question");
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight, 200)}px`;
}
$("question").addEventListener("input", autoGrow);
$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("form").requestSubmit();
  }
});
// Alt+1..9 loads the matching example question (presenter shortcut).
document.addEventListener("keydown", (event) => {
  if (!event.altKey || event.ctrlKey || event.metaKey || !/^Digit[1-9]$/.test(event.code)) return;
  const item = exampleItems()[Number(event.code.slice(5)) - 1];
  if (!item) return;
  event.preventDefault();
  useExample(item.question);
});
$("kb").addEventListener("change", () => { resetChat(); renderExamples(); });
$("reset").addEventListener("click", resetChat);
for (const radio of document.querySelectorAll('input[name="mode"]')) radio.addEventListener("change", applyMode);

// Test panel: open by default on wide screens, remembered across reloads; Alt+T toggles it.
{
  let saved = null;
  try { saved = localStorage.getItem("wpc-tests-open"); } catch { /* storage unavailable */ }
  const open = saved === null ? !window.matchMedia("(max-width: 1100px)").matches : saved === "1";
  setTestsOpen(open);
  const toggle = () => setTestsOpen(document.body.classList.contains("tests-closed"));
  $("tests-toggle").addEventListener("click", toggle);
  $("tests-close").addEventListener("click", () => setTestsOpen(false));
  document.addEventListener("keydown", (event) => {
    if (event.altKey && !event.ctrlKey && !event.metaKey && event.code === "KeyT") {
      event.preventDefault();
      toggle();
    }
  });
  // Keeps the sticky panel just below the header, whose height changes with zoom and stage mode.
  const header = document.querySelector("header");
  new ResizeObserver(() => document.documentElement.style.setProperty("--header-h", `${header.offsetHeight}px`)).observe(header);
}

loadConfig().catch((error) => {
  $("warning").hidden = false;
  $("warning").textContent = `Errore di configurazione: ${error.message}`;
});
