"use strict";

const els = {
  serverSelect: document.getElementById("serverSelect"),
  serverInfo: document.getElementById("serverInfo"),
  lmBase: document.getElementById("lmBase"),
  lmModel: document.getElementById("lmModel"),
  lmModels: document.getElementById("lmModels"),
  temperature: document.getElementById("temperature"),
  maxTokens: document.getElementById("maxTokens"),
  limit: document.getElementById("limit"),
  useGraph: document.getElementById("useGraph"),
  healthBtn: document.getElementById("healthBtn"),
  healthStatus: document.getElementById("healthStatus"),
  chat: document.getElementById("chat"),
  composer: document.getElementById("composer"),
  question: document.getElementById("question"),
  sendBtn: document.getElementById("sendBtn"),
};

let servers = [];

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

// Минимальный рендер ответа: блоки ```...``` -> <pre>, остальное с переносами строк.
function renderAnswer(text) {
  const parts = String(text).split(/```/);
  let html = "";
  parts.forEach((part, index) => {
    if (index % 2 === 1) {
      const cleaned = part.replace(/^bsl\n?/i, "");
      html += `<pre><code>${escapeHtml(cleaned)}</code></pre>`;
    } else {
      html += escapeHtml(part).replace(/\n/g, "<br />");
    }
  });
  return html;
}

function addMessage(role, html, metaText) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = html;
  if (metaText) {
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = metaText;
    div.appendChild(meta);
  }
  els.chat.appendChild(div);
  els.chat.scrollTop = els.chat.scrollHeight;
  return div;
}

async function api(path, options) {
  const response = await fetch(path, options);
  let data;
  try {
    data = await response.json();
  } catch (e) {
    throw new Error(`Некорректный ответ сервера (${response.status})`);
  }
  if (!response.ok || data.error) {
    throw new Error(data.error || `Ошибка ${response.status}`);
  }
  return data;
}

async function loadServers() {
  try {
    const data = await api("/api/mcp");
    servers = data.servers || [];
    els.serverSelect.innerHTML = "";
    if (servers.length === 0) {
      const opt = document.createElement("option");
      opt.textContent = "Нет доступных серверов";
      els.serverSelect.appendChild(opt);
      els.serverSelect.disabled = true;
      return;
    }
    servers.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = s.available ? s.name : `${s.name} (нет индекса)`;
      els.serverSelect.appendChild(opt);
    });
    updateServerInfo();
  } catch (e) {
    els.serverInfo.textContent = e.message;
  }
}

function currentServer() {
  return servers.find((s) => s.name === els.serverSelect.value);
}

function updateServerInfo() {
  const s = currentServer();
  if (!s) {
    els.serverInfo.textContent = "";
    return;
  }
  const status = s.available ? "база готова" : "база не проиндексирована";
  els.serverInfo.innerHTML =
    `${escapeHtml(s.description || "")}<br/>${escapeHtml(status)}`;
}

async function checkHealth() {
  const s = currentServer();
  els.healthStatus.innerHTML = '<span class="muted small">Проверка…</span>';
  try {
    const params = new URLSearchParams({ lm_base_url: els.lmBase.value.trim() });
    if (s) params.set("server", s.name);
    const data = await api(`/api/health?${params.toString()}`);

    const items = [];
    const lm = data.lmstudio || {};
    items.push(badge(lm.ok, lm.ok ? `LM Studio: ${(lm.models || []).length} моделей` : `LM Studio: ${lm.error || "недоступен"}`));

    if (lm.ok && lm.models) {
      els.lmModels.innerHTML = "";
      lm.models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m;
        els.lmModels.appendChild(opt);
      });
    }

    const mcp = data.mcp || {};
    if (Object.keys(mcp).length) {
      const text = mcp.ok
        ? `MCP: ${mcp.vector_records} записей, граф ${mcp.graph_nodes} узлов`
        : `MCP: ${mcp.error || "ошибка"}`;
      items.push(badge(mcp.ok, text));
    }

    els.healthStatus.innerHTML = items.join("");
  } catch (e) {
    els.healthStatus.innerHTML = badge(false, e.message);
  }
}

function badge(ok, text) {
  const cls = ok ? "ok" : "err";
  return `<span class="badge ${cls}"><span class="dot"></span>${escapeHtml(text)}</span>`;
}

// ---- Источники и граф ----

function setTabBadge(tab, count) {
  const btn = document.querySelector(`.tab[data-tab="${tab}"]`);
  if (!btn) return;
  const base = { code: "Код", metadata: "Метаданные", forms: "Формы", graph: "Граф" }[tab];
  btn.textContent = count ? `${base} (${count})` : base;
}

function renderSources(sources, graph) {
  renderCode(sources.code || []);
  renderMetadata(sources.metadata || []);
  renderForms(sources.forms || []);
  renderGraph(graph);
}

function emptyHtml(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function renderCode(code) {
  setTabBadge("code", code.length);
  const box = document.getElementById("tab-code");
  if (!code.length) { box.innerHTML = emptyHtml("Нет фрагментов кода"); return; }
  box.innerHTML = code.map((c) => `
    <div class="source-card">
      <span class="relevance">${escapeHtml((c.relevance ?? "").toString())}</span>
      <div class="title">${escapeHtml(c.method || c.module || c.object)}</div>
      <div class="sub">${escapeHtml(c.object || "")} ${c.is_export ? "· экспорт" : ""}</div>
      ${c.signature ? `<div class="sub">${escapeHtml(c.signature)}</div>` : ""}
      <pre><code>${escapeHtml(c.code || "")}</code></pre>
      <div class="path">${escapeHtml(c.file_path || "")}</div>
    </div>`).join("");
}

function renderMetadata(metadata) {
  setTabBadge("metadata", metadata.length);
  const box = document.getElementById("tab-metadata");
  if (!metadata.length) { box.innerHTML = emptyHtml("Нет объектов метаданных"); return; }
  box.innerHTML = metadata.map((m) => `
    <div class="source-card">
      <span class="relevance">${escapeHtml((m.relevance ?? "").toString())}</span>
      <div class="title">${escapeHtml(m.type || "")}.${escapeHtml(m.name || "")}</div>
      ${m.synonym ? `<div class="sub">${escapeHtml(m.synonym)}</div>` : ""}
      ${m.description ? `<div class="sub">${escapeHtml(m.description)}</div>` : ""}
      <div class="path">${escapeHtml(m.file_path || "")}</div>
    </div>`).join("");
}

function renderForms(forms) {
  setTabBadge("forms", forms.length);
  const box = document.getElementById("tab-forms");
  if (!forms.length) { box.innerHTML = emptyHtml("Нет форм"); return; }
  box.innerHTML = forms.map((f) => `
    <div class="source-card">
      <span class="relevance">${escapeHtml((f.relevance ?? "").toString())}</span>
      <div class="title">${escapeHtml(f.form_name || "")}</div>
      <div class="sub">${escapeHtml(f.object || "")} · элементов: ${escapeHtml((f.elements_count ?? 0).toString())}</div>
      <div class="path">${escapeHtml(f.file_path || "")}</div>
    </div>`).join("");
}

function renderGraph(graph) {
  const box = document.getElementById("tab-graph");
  if (!graph || (!(graph.dependencies || []).length && !(graph.references || []).length)) {
    setTabBadge("graph", 0);
    box.innerHTML = emptyHtml("Нет данных графа. Включите «Граф связей» или задайте вопрос о зависимостях объекта.");
    return;
  }
  const deps = graph.dependencies || [];
  const refs = graph.references || [];
  setTabBadge("graph", deps.length + refs.length);
  box.innerHTML = `
    <div class="graph-legend">
      <span><span class="legend-dot" style="background:#ff8a5d"></span>Зависят от объекта (${deps.length})</span>
      <span><span class="legend-dot" style="background:#4f8cff"></span>Объект использует (${refs.length})</span>
    </div>
    ${buildGraphSvg(graph.object, deps, refs)}`;
}

// Самодостаточная SVG-визуализация: центральный объект + кольца связей.
function buildGraphSvg(centerName, deps, refs) {
  const width = 340;
  const height = 360;
  const cx = width / 2;
  const cy = height / 2;
  const maxPer = 12;
  const d = deps.slice(0, maxPer);
  const r = refs.slice(0, maxPer);

  let svg = `<svg id="graphSvg" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`;
  const place = (list, color, radius, startAngle, sweep) => {
    let out = "";
    const n = Math.max(list.length, 1);
    list.forEach((node, i) => {
      const angle = startAngle + (sweep * (i + 1)) / (n + 1);
      const x = cx + radius * Math.cos(angle);
      const y = cy + radius * Math.sin(angle);
      out += `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="${color}" stroke-opacity="0.4" />`;
      out += `<circle cx="${x}" cy="${y}" r="5" fill="${color}" />`;
      const label = node.object || "";
      const anchor = x < cx ? "end" : "start";
      const dx = x < cx ? -8 : 8;
      out += `<text x="${x + dx}" y="${y + 3}" fill="#cfd5e1" font-size="9" text-anchor="${anchor}">${escapeHtml(label.slice(0, 22))}</text>`;
    });
    return out;
  };

  // Зависимые слева (от -135° до -225°/135°), используемые справа.
  svg += place(d, "#ff8a5d", 120, Math.PI * 0.6, Math.PI * 0.8);
  svg += place(r, "#4f8cff", 120, -Math.PI * 0.4, Math.PI * 0.8);
  svg += `<circle cx="${cx}" cy="${cy}" r="26" fill="#1e222b" stroke="#36c08a" stroke-width="2" />`;
  svg += `<text x="${cx}" y="${cy + 3}" fill="#e6e8ee" font-size="10" text-anchor="middle">${escapeHtml((centerName || "").slice(0, 16))}</text>`;
  svg += `</svg>`;
  return svg;
}

function clearSources() {
  ["code", "metadata", "forms", "graph"].forEach((t) => {
    setTabBadge(t, 0);
    document.getElementById(`tab-${t}`).innerHTML = emptyHtml("…");
  });
}

// ---- Чат ----

async function sendQuestion(event) {
  event.preventDefault();
  const question = els.question.value.trim();
  const server = currentServer();
  if (!question) return;
  if (!server) {
    addMessage("error", "Не выбран MCP-сервер.");
    return;
  }

  addMessage("user", escapeHtml(question));
  els.question.value = "";
  els.sendBtn.disabled = true;
  const pending = addMessage("assistant", '<span class="spinner"></span> Поиск в базе и запрос к LM Studio…');

  try {
    const data = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        server: server.name,
        question,
        lm_base_url: els.lmBase.value.trim(),
        lm_model: els.lmModel.value.trim() || null,
        temperature: parseFloat(els.temperature.value) || 0.2,
        max_tokens: parseInt(els.maxTokens.value, 10) || 1024,
        limit: parseInt(els.limit.value, 10) || 5,
        use_graph: els.useGraph.checked ? true : null,
      }),
    });

    const counts = data.sources || {};
    const meta = `Источники: код ${(counts.code || []).length}, метаданные ${(counts.metadata || []).length}, формы ${(counts.forms || []).length}` +
      (data.used_graph ? " · граф включён" : "");
    pending.innerHTML = renderAnswer(data.answer);
    const metaDiv = document.createElement("div");
    metaDiv.className = "meta";
    metaDiv.textContent = meta + (data.has_context ? "" : " · контекст пуст");
    pending.appendChild(metaDiv);

    renderSources(data.sources || {}, data.graph);
  } catch (e) {
    pending.className = "msg error";
    pending.innerHTML = escapeHtml(e.message);
  } finally {
    els.sendBtn.disabled = false;
    els.question.focus();
  }
}

// ---- Вкладки ----
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-body").forEach((b) => b.classList.add("hidden"));
    tab.classList.add("active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.remove("hidden");
  });
});

els.serverSelect.addEventListener("change", updateServerInfo);
els.healthBtn.addEventListener("click", checkHealth);
els.composer.addEventListener("submit", sendQuestion);
els.question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    els.composer.requestSubmit();
  }
});

clearSources();
addMessage("system", "Выберите MCP-сервер, проверьте подключение и задайте вопрос. Ctrl+Enter — отправить.");
loadServers();
