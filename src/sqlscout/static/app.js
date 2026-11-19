(() => {
  const $ = (id) => document.getElementById(id);
  const nf = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
  let apiKey = null;

  function el(tag, props = {}, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const kid of kids) if (kid) node.append(kid);
    return node;
  }

  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json" };
    if (apiKey) headers["X-API-Key"] = apiKey;
    const res = await fetch(path, { ...options, headers });
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON error body */ }
    return { ok: res.ok, status: res.status, data };
  }

  // ---------- rendering
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);

  function table(columns, rows) {
    const numeric = columns.map((_, i) => rows.length > 0 && rows.every((r) => r[i] === null || isNum(r[i])));
    const head = el("tr", {}, ...columns.map((c, i) => el("th", { text: c, class: numeric[i] ? "num" : "" })));
    const body = rows.map((r) =>
      el("tr", {}, ...r.map((v, i) => el("td", { class: numeric[i] ? "num" : "", text: v === null ? "null" : isNum(v) ? nf.format(v) : String(v) })))
    );
    return el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, head), el("tbody", {}, ...body)));
  }

  function chart(columns, rows) {
    if (columns.length !== 2 || rows.length < 2 || rows.length > 30) return null;
    if (!rows.every((r) => isNum(r[1]))) return null;
    const NS = "http://www.w3.org/2000/svg";
    const rowH = 22, left = 150, width = 900, pad = 110;
    const height = rows.length * rowH + 8;
    const max = Math.max(...rows.map((r) => r[1]), 1);
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `Bar chart of ${columns[1]} by ${columns[0]}`);
    rows.forEach((r, i) => {
      const y = i * rowH + 4;
      const w = Math.max(1, ((width - left - pad) * Math.max(r[1], 0)) / max);
      const label = document.createElementNS(NS, "text");
      label.setAttribute("x", left - 8); label.setAttribute("y", y + 14); label.setAttribute("text-anchor", "end");
      label.textContent = String(r[0]).slice(0, 22);
      const bar = document.createElementNS(NS, "rect");
      bar.setAttribute("x", left); bar.setAttribute("y", y + 2); bar.setAttribute("width", w);
      bar.setAttribute("height", rowH - 7); bar.setAttribute("rx", 3);
      const value = document.createElementNS(NS, "text");
      value.setAttribute("x", left + w + 6); value.setAttribute("y", y + 14);
      value.textContent = nf.format(r[1]);
      svg.append(label, bar, value);
    });
    return el("div", { class: "chart" }, svg);
  }

  function resultBlock(d) {
    const frag = document.createDocumentFragment();
    frag.append(table(d.columns, d.rows));
    const c = chart(d.columns, d.rows);
    if (c) frag.append(c);
    const bits = [`${d.rows.length} row${d.rows.length === 1 ? "" : "s"}`];
    if (d.truncated) bits.push("truncated at the row cap");
    if (d.elapsed_ms !== undefined) bits.push(`${d.elapsed_ms} ms`);
    frag.append(el("p", { class: "meta", text: bits.join(" · ") }));
    return frag;
  }

  function errorCard(kind, message) {
    const label = { unsafe: "Blocked by the guard", policy: "Blocked by policy", invalid: "Invalid query", runtime: "Query failed" }[kind] || "Error";
    return el("div", { class: "card error" }, el("h2", { text: label }), el("div", { text: message }));
  }

  // ---------- tabs
  document.querySelectorAll("nav button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("nav button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      for (const name of ["ask", "sql", "schema"]) $("tab-" + name).hidden = name !== b.dataset.tab;
    })
  );

  // ---------- SQL console
  async function runSql() {
    const out = $("sql-out"); const btn = $("sql-btn");
    btn.disabled = true; out.replaceChildren();
    const { data, status } = await api("/api/sql", { method: "POST", body: JSON.stringify({ query: $("sql").value }) });
    btn.disabled = false;
    if (status === 401) { out.append(errorCard("policy", "This server needs an API key.")); return; }
    if (status === 429) { out.append(errorCard("policy", "Too many requests. Try again in a minute.")); return; }
    if (data && data.error) { out.append(errorCard(data.error.kind, data.error.message)); return; }
    if (!data || !data.columns) { out.append(errorCard("runtime", "Unexpected response.")); return; }
    const card = el("div", { class: "card" }, el("h2", { text: "Query that ran" }), el("pre", { text: data.sql }));
    if (data.notes.length) card.append(el("p", { class: "meta", text: "Guard: " + data.notes.join("; ") }));
    out.append(card, el("div", { class: "card" }, resultBlock(data)));
  }
  $("sql-form").addEventListener("submit", (e) => { e.preventDefault(); runSql(); });

  // ---------- Ask
  async function ask() {
    const q = $("question").value.trim();
    if (q.length < 3) return;
    const out = $("ask-out"); const btn = $("ask-btn");
    btn.disabled = true; out.replaceChildren(el("p", { class: "meta", text: "Working…" }));
    const { ok, data, status } = await api("/api/ask", { method: "POST", body: JSON.stringify({ question: q }) });
    btn.disabled = false; out.replaceChildren();
    if (!ok) {
      const detail = (data && (data.detail && (typeof data.detail === "string" ? data.detail : "Invalid request"))) || "Request failed.";
      out.append(errorCard(status === 503 ? "runtime" : "policy", detail));
      return;
    }
    out.append(el("div", { class: "card answer", text: data.answer || "(no answer)" }));
    if (data.sql) out.append(el("div", { class: "card" }, el("h2", { text: "Query that produced it" }), el("pre", { text: data.sql }), data.columns ? resultBlock(data) : null));
    const trace = el("details", {}, el("summary", { text: `Steps (${data.steps.length}) · ${data.usage.input_tokens + data.usage.output_tokens} tokens · ${data.status}` }));
    data.steps.forEach((s) => {
      const text = s.tool === "run_sql" ? s.arguments.query : JSON.stringify(s.arguments);
      trace.append(el("div", { class: "step" + (s.is_error ? " bad" : "") }, el("div", {}, el("code", { text: `${s.index}. ${s.tool}` })), el("pre", { text }), el("p", { class: "meta", text: s.output.slice(0, 220) })));
    });
    out.append(el("div", { class: "card" }, trace));
  }
  $("ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask(); });

  // ---------- chips and schema
  function chips(host, items, target, attack) {
    items.forEach((it) => {
      const text = typeof it === "string" ? it : it.label;
      const b = el("button", { type: "button", text, class: attack ? "attack" : "" });
      b.addEventListener("click", () => { target.value = typeof it === "string" ? it : it.sql; if (attack || typeof it !== "string") { document.querySelector('[data-tab="sql"]').click(); runSql(); } else { target.focus(); } });
      host.append(b);
    });
  }

  async function init() {
    const health = await api("/api/health");
    const info = health.data || {};
    if (info.model) { $("model").textContent = info.model; $("model").hidden = false; }
    else {
      $("ask-banner").textContent = "No model is configured on this server, so Ask is disabled. The SQL console and schema work without one.";
      $("ask-banner").hidden = false;
    }
    const ex = (await api("/api/examples")).data || { questions: [], queries: [], attacks: [] };
    chips($("ask-chips"), ex.questions, $("question"), false);
    chips($("sql-chips"), ex.queries, $("sql"), false);
    chips($("attack-chips"), ex.attacks, $("sql"), true);
    $("sql").value = (ex.queries[0] || {}).sql || "SELECT COUNT(*) FROM orders";

    const schema = (await api("/api/schema")).data;
    if (schema) {
      const hidden = Object.entries(schema.restricted).map(([t, c]) => `${t}.${c.join(", ")}`).join("; ");
      $("schema-note").textContent = hidden ? `Hidden from queries: ${hidden}.` : "";
      $("schema-out").replaceChildren(...schema.tables.map((t) =>
        el("div", { class: "card tbl" },
          el("h3", { text: t.name }, el("small", { text: `${nf.format(t.rows)} rows` })),
          t.description ? el("p", { class: "meta", text: t.description }) : null,
          ...t.columns.map((c) => el("div", { class: "col" }, el("b", { text: c.name }), el("span", { text: ` ${c.type}${c.description ? " · " + c.description : ""}` }))))));
    }
  }
  init();
})();
