// Renders traces parsed by TraceParser. All content is inserted as text nodes (never as HTML).
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const input = $("trace-text");
  const view = $("trace-view");
  const errorBox = $("trace-error");
  const toolbar = $("trace-toolbar");
  const filterInput = $("filter");
  const rawToggle = $("raw");
  let trace = null;

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  // Text with the filter term wrapped in <mark>.
  function highlighted(text, term) {
    const value = String(text ?? "");
    if (!term) return [value];
    const out = [];
    const lower = value.toLowerCase();
    let from = 0;
    for (let i = lower.indexOf(term); i >= 0; i = lower.indexOf(term, from)) {
      out.push(value.slice(from, i), el("mark", {}, value.slice(i, i + term.length)));
      from = i + term.length;
    }
    out.push(value.slice(from));
    return out;
  }

  function tableCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  }

  // Minimal markdown: headings, tables, bullet lists, paragraphs, horizontal rules.
  function renderMarkdown(text, term) {
    const container = el("div", { class: "snippet" });
    const lines = text.split("\n");
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line.trim()) continue;
      if (line.trim().startsWith("|")) {
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++]);
        i--;
        const body = rows.filter((r) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(r)).map(tableCells);
        const hasHeader = rows.length > 1 && /^\s*\|?[\s:|-]+\|?\s*$/.test(rows[1]);
        const table = el("table");
        body.forEach((cells, index) => {
          const tag = hasHeader && index === 0 ? "th" : "td";
          table.append(el("tr", {}, cells.map((c) => el(tag, {}, highlighted(c, term)))));
        });
        container.append(el("div", { class: "table-wrap" }, table));
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        container.append(el(`h${Math.min(heading[1].length + 2, 6)}`, {}, highlighted(heading[2], term)));
      } else if (/^\s*[-*]\s+/.test(line)) {
        const list = el("ul");
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          list.append(el("li", {}, highlighted(lines[i++].replace(/^\s*[-*]\s+/, ""), term)));
        }
        i--;
        container.append(list);
      } else if (/^\s*-{3,}\s*$/.test(line)) {
        container.append(el("hr"));
      } else {
        container.append(el("p", {}, highlighted(line, term)));
      }
    }
    return container;
  }

  function snippetNode(text, term) {
    return rawToggle.checked ? el("pre", {}, highlighted(text, term)) : renderMarkdown(text, term);
  }

  function matches(source, term) {
    if (!term) return true;
    return [source.snippet, source.document, source.url, source.uid].some((v) => String(v ?? "").toLowerCase().includes(term));
  }

  function sourceCard(source, term, showDocument = true) {
    const title = showDocument ? source.document : `Pagina ${source.page ?? "?"}`;
    return el(
      "details",
      { class: "source", open: term ? true : null },
      el(
        "summary",
        {},
        el("span", { class: "type" }, source.marker.replace(/†.*】/, "】")),
        el("strong", {}, highlighted(title, term)),
        showDocument && source.page !== null ? el("span", { class: "meta" }, ` · pagina ${source.page}`) : null,
        el("span", { class: "meta" }, ` · ${source.snippet.length.toLocaleString("it-IT")} caratteri`),
      ),
      el(
        "div",
        { class: "source-meta" },
        source.url ? el("div", {}, "URL: ", el("code", {}, source.url)) : null,
        source.uid ? el("div", {}, "uid: ", el("code", {}, source.uid)) : null,
        Object.keys(source.extra).length ? el("pre", {}, JSON.stringify(source.extra, null, 2)) : null,
      ),
      snippetNode(source.snippet, term),
    );
  }

  function jsonBlock(value) {
    return el("pre", {}, typeof value === "string" ? value : JSON.stringify(value, null, 2));
  }

  function partNode(part, term) {
    if (part.type === "text") {
      return el("div", { class: "part" }, el("span", { class: "type" }, "text"), snippetNode(part.text, term));
    }
    if (part.type === "tool_call") {
      return el(
        "div",
        { class: "part tool-call" },
        el("div", {}, el("span", { class: "type" }, "tool_call"), el("strong", {}, part.name), part.id ? el("span", { class: "meta" }, ` · ${part.id}`) : null),
        part.arguments !== undefined ? jsonBlock(part.arguments) : null,
      );
    }
    if (part.type === "tool_call_response") {
      const visible = part.sources.filter((s) => matches(s, term));
      return el(
        "div",
        { class: "part tool-response" },
        el(
          "div",
          {},
          el("span", { class: "type" }, "tool_call_response"),
          part.id ? el("span", { class: "meta" }, part.id) : null,
          part.sources.length ? el("span", { class: "meta" }, ` · ${visible.length}/${part.sources.length} fonti`) : null,
        ),
        part.header ? el("p", { class: "meta" }, part.header) : null,
        visible.map((s) => sourceCard(s, term)),
        part.json !== undefined ? jsonBlock(part.json) : null,
        part.trailer ? el("p", { class: "meta" }, part.trailer) : null,
      );
    }
    return el("div", { class: "part" }, el("span", { class: "type" }, part.type), jsonBlock(part.raw));
  }

  function renderTimeline(term) {
    return trace.messages.map((message) =>
      el(
        "article",
        { class: `trace-message role-${message.role}` },
        el("div", { class: "message-head" }, el("span", { class: "badge role" }, message.role), message.name ? el("span", { class: "meta" }, message.name) : null, el("span", { class: "meta" }, `#${message.position + 1}`)),
        message.parts.map((p) => partNode(p, term)),
      ),
    );
  }

  function renderDocuments(term) {
    const groups = new Map();
    for (const message of trace.messages) {
      for (const part of message.parts) {
        for (const source of part.sources || []) {
          if (!matches(source, term)) continue;
          const key = source.url || source.document;
          if (!groups.has(key)) groups.set(key, { document: source.document, url: source.url, sources: [] });
          groups.get(key).sources.push(source);
        }
      }
    }
    if (!groups.size) return [el("p", { class: "meta" }, "Nessuna fonte corrisponde al filtro.")];
    return [...groups.values()]
      .sort((a, b) => b.sources.length - a.sources.length)
      .map((group) =>
        el(
          "article",
          { class: "trace-message" },
          el("div", { class: "message-head" }, el("strong", {}, highlighted(group.document, term)), el("span", { class: "meta" }, ` · ${group.sources.length} chunk`)),
          group.url ? el("div", { class: "meta" }, el("code", {}, group.url)) : null,
          group.sources.sort((a, b) => (a.page ?? 0) - (b.page ?? 0)).map((s) => sourceCard(s, term, false)),
        ),
      );
  }

  function render() {
    if (!trace) return;
    const term = filterInput.value.trim().toLowerCase();
    const mode = document.querySelector("input[name=view]:checked").value;
    const s = trace.stats;
    $("stats").replaceChildren(
      ...[["messaggi", s.messages], ["tool call", s.toolCalls], ["risposte tool", s.toolResponses], ["fonti", s.sources], ["documenti", s.documents]].map(([label, value]) =>
        el("span", { class: "stat" }, el("strong", {}, value), ` ${label}`),
      ),
    );
    view.replaceChildren(...(mode === "documents" ? renderDocuments(term) : renderTimeline(term)));
  }

  function load(text) {
    errorBox.hidden = true;
    try {
      trace = TraceParser.parseTrace(text);
      toolbar.hidden = false;
      render();
    } catch (error) {
      trace = null;
      toolbar.hidden = true;
      view.replaceChildren();
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    }
  }

  async function loadFile(file) {
    input.value = await file.text();
    load(input.value);
  }

  $("parse").addEventListener("click", () => load(input.value));
  $("clear").addEventListener("click", () => {
    input.value = "";
    trace = null;
    toolbar.hidden = true;
    errorBox.hidden = true;
    view.replaceChildren();
  });
  $("file").addEventListener("change", (e) => e.target.files[0] && loadFile(e.target.files[0]));
  input.addEventListener("paste", () => setTimeout(() => load(input.value), 0));
  const dropZone = input.closest(".trace-input");
  input.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
  input.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  input.addEventListener("drop", (e) => {
    dropZone.classList.remove("dragover");
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    loadFile(e.dataTransfer.files[0]);
  });
  filterInput.addEventListener("input", render);
  rawToggle.addEventListener("change", render);
  document.querySelectorAll("input[name=view]").forEach((r) => r.addEventListener("change", render));
  $("toggle-all").addEventListener("click", (e) => {
    const cards = view.querySelectorAll("details.source");
    const open = [...cards].some((d) => !d.open);
    cards.forEach((d) => (d.open = open));
    e.target.textContent = open ? "Comprimi tutto" : "Espandi tutto";
  });
})();
