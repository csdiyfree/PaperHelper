import * as pdfjsLib from "./vendor/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "./vendor/pdf.worker.min.mjs";
marked.setOptions({ gfm: true, breaks: true });

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const app = $("#app");
const MODES = { zh: "中文", bilingual: "对照", original: "原文" };
const MODE_ORDER = ["zh", "bilingual", "original"];

const state = {
  lib: { root: "", categories: [], papers: [] },
  view: "library", cat: "", paper: null, mode: "zh", page: 1, scale: 1,
  tab: "notes", readerTab: "notes", editing: false,
  panelOpen: { library: false, reader: true },
  highlight: new Set(), busy: false, sel: null,
  hasKey: false, model: "deepseek-chat",
};

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDate = (d) => { const [, m, dd] = String(d || "").split("-").map(Number); return m ? `${m} 月 ${dd} 日` : ""; };
const restartAnim = (el) => { el.style.animation = "none"; void el.offsetHeight; el.style.animation = ""; };
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
async function api(path, { method = "GET", body } = {}) {
  const r = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `请求失败（${r.status}）`);
  return data;
}
const paperById = (id) => state.lib.papers.find((p) => p.id === id);

// ---------- library ----------
async function loadLibrary({ diff = false } = {}) {
  const prev = new Map(state.lib.papers.map((p) => [p.id, p.category]));
  state.lib = await api("/api/library");
  const moved = diff ? state.lib.papers.filter((p) => prev.has(p.id) && prev.get(p.id) !== p.category) : [];
  if (moved.length) state.highlight = new Set(moved.map((p) => p.id));
  if (state.paper) state.paper = paperById(state.paper.id) || state.paper;
  renderSidebar();
  if (state.view === "library") renderLibrary();
  for (const p of moved) flashNav(p.category);
}
function categories() {
  return [...state.lib.categories].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh"));
}
function renderSidebar() {
  $("#countAll").textContent = state.lib.papers.length;
  $("#catList").innerHTML = categories()
    .map((c) => `<button class="nav-item" data-cat="${esc(c.name)}">${esc(c.name)}<span class="count">${c.count}</span></button>`)
    .join("");
  $$(".nav-item").forEach((b) => b.classList.toggle("active", state.view === "library" && b.dataset.cat === state.cat));
  $("#libRoot").textContent = (state.lib.root || "~/PaperHelper/library").replace(/^\/Users\/[^/]+/, "~");
}
function flashNav(cat) {
  const nav = $(`.nav-item[data-cat="${CSS.escape(cat)}"]`);
  if (nav) { nav.classList.remove("flash"); void nav.offsetWidth; nav.classList.add("flash"); }
}
function paperRow(p, i) {
  return `<article class="paper${state.highlight.has(p.id) ? " hit" : ""}" data-id="${esc(p.id)}" style="--i:${i}">
    <div>
      <h3 class="t-zh">${esc(p.title_zh || p.title_en)}</h3>
      <div class="t-en">${esc(p.title_en)}</div>
      <div class="meta">${[p.authors, [p.venue, p.year].filter(Boolean).join(" "), p.pages ? `${p.pages} 页` : ""].filter(Boolean).map(esc).join(" · ")}</div>
    </div>
    <div class="paper-side"><time>${fmtDate(p.added)}</time>${p.has_notes ? '<span class="has-note">笔记</span>' : ""}</div>
  </article>`;
}
function renderLibrary() {
  const papers = state.lib.papers;
  const cats = categories();
  const groups = state.cat
    ? [[state.cat, papers.filter((p) => p.category === state.cat)]]
    : cats.map((c) => [c.name, papers.filter((p) => p.category === c.name)]).filter(([, ps]) => ps.length);
  $("#libTitle").textContent = state.cat || "全部论文";
  $("#libSub").textContent = state.cat
    ? `${groups[0][1].length} 篇`
    : papers.length ? `${papers.length} 篇 · ${cats.length} 个分类 · 新论文由 AI 自动归类` : "";
  if (!papers.length) {
    $("#paperList").innerHTML = `<div class="empty-state"><b>论文库还是空的</b>让 Claude 翻译一篇论文，完成后会自动归类到这里。</div>`;
    return;
  }
  let html = "", i = 0;
  for (const [c, ps] of groups) {
    if (!state.cat) html += `<div class="group-h">${esc(c)}<span>${ps.length}</span></div>`;
    for (const p of [...ps].sort((a, b) => String(b.added).localeCompare(String(a.added)))) html += paperRow(p, i++);
  }
  $("#paperList").innerHTML = html || `<div class="empty-state"><b>这个分类是空的</b></div>`;
}
function showLibrary(cat = "") {
  const fresh = state.view !== "library" || state.cat !== cat;
  state.view = "library"; state.cat = cat; state.paper = null;
  $("#readerView").hidden = true;
  app.classList.remove("reading");
  const lv = $("#libraryView"); lv.hidden = false; if (fresh) restartAnim(lv);
  hideSelPop(); renderSidebar(); renderLibrary(); setPanel();
}

// ---------- reader ----------
let pdfDoc = null, renderToken = 0, observers = [];

async function openPaper(id, mode) {
  const p = paperById(id);
  if (!p) { location.hash = "#/"; return; }
  const modes = p.modes || [];
  const m = modes.includes(mode) ? mode : MODE_ORDER.find((x) => modes.includes(x)) || "zh";
  const same = state.view === "reader" && state.paper?.id === id && state.mode === m;
  const samePaper = state.paper?.id === id && state.view === "reader";
  state.view = "reader"; state.paper = p; state.mode = m;
  state.highlight.clear();
  $("#libraryView").hidden = true;
  app.classList.add("reading");
  const rv = $("#readerView"); if (rv.hidden) { rv.hidden = false; restartAnim(rv); }
  $("#rTitleZh").textContent = p.title_zh || p.title_en; $("#rTitleEn").textContent = p.title_en;
  renderSidebar(); setSeg(); setPanel();
  if (!samePaper) loadNotes();
  if (!same) await loadPdf(0);
}
function setSeg() {
  const seg = $("#modeSeg"), modes = state.paper?.modes || [];
  $$("button", seg).forEach((b) => { b.classList.toggle("on", b.dataset.mode === state.mode); b.disabled = !modes.includes(b.dataset.mode); });
  const btn = $(`button[data-mode="${state.mode}"]`, seg), ind = $(".seg-ind", seg);
  ind.style.width = btn.offsetWidth + "px"; ind.style.transform = `translateX(${btn.offsetLeft}px)`;
}
async function loadPdf(ratio) {
  const token = ++renderToken;
  const wrap = $("#pdfPages");
  hideSelPop();
  observers.forEach((o) => o.disconnect()); observers = [];
  if (!(state.paper.modes || []).includes(state.mode)) {
    pdfDoc?.destroy(); pdfDoc = null;
    wrap.innerHTML = `<div class="empty-state"><b>没有可显示的 PDF</b>这篇论文的文件夹里缺少 PDF 文件。</div>`;
    $("#pageInd").textContent = "";
    return;
  }
  wrap.classList.add("fading");
  let doc;
  try {
    doc = await pdfjsLib.getDocument(`/files/${encodeURIComponent(state.paper.id)}/${state.mode}.pdf`).promise;
  } catch (e) {
    if (token === renderToken) wrap.innerHTML = `<div class="empty-state"><b>PDF 打不开</b>${esc(e.message)}</div>`;
    wrap.classList.remove("fading");
    return;
  }
  if (token !== renderToken) { doc.destroy(); return; }
  pdfDoc?.destroy(); pdfDoc = doc;
  await layoutPages(ratio, token);
}
async function layoutPages(ratio, token = ++renderToken) {
  const wrap = $("#pdfPages"), scroller = $("#pdfScroll");
  observers.forEach((o) => o.disconnect()); observers = [];
  const base = (await pdfDoc.getPage(1)).getViewport({ scale: 1 });
  if (token !== renderToken) return;
  // width the reader will have once the sidebar/panel transitions settle
  const panelW = state.panelOpen.reader ? 380 : 0;
  const avail = Math.min(innerWidth - panelW - 64, 880);
  state.scale = Math.max(0.5, Math.min(1.8, avail / base.width));
  wrap.innerHTML = ""; textLayers.clear(); prevRange = null;
  for (let i = 1; i <= pdfDoc.numPages; i++) {
    const d = document.createElement("div");
    d.className = "pdf-page"; d.dataset.page = i;
    d.style.width = base.width * state.scale + "px";
    d.style.height = base.height * state.scale + "px";
    d.style.setProperty("--scale-factor", state.scale);
    wrap.appendChild(d);
  }
  const n = pdfDoc.numPages;
  const lazy = new IntersectionObserver(
    (es) => es.forEach((e) => e.isIntersecting && renderPage(e.target, token)),
    { root: scroller, rootMargin: "900px 0px" });
  const seen = new Map();
  const vis = new IntersectionObserver((es) => {
    es.forEach((e) => seen.set(+e.target.dataset.page, e.intersectionRatio));
    let best = state.page, bv = -1;
    seen.forEach((v, k) => { if (v > bv) { bv = v; best = k; } });
    state.page = best; $("#pageInd").textContent = `${best} / ${n}`;
  }, { root: scroller, threshold: [0, 0.25, 0.5, 0.75, 1] });
  $$(".pdf-page", wrap).forEach((d) => { lazy.observe(d); vis.observe(d); });
  observers = [lazy, vis];
  const target = Math.min(n, Math.max(1, Math.ceil(ratio * n)));
  state.page = target; $("#pageInd").textContent = `${target} / ${n}`;
  scroller.scrollTop = target > 1 ? wrap.children[target - 1].offsetTop - 18 : 0;
  requestAnimationFrame(() => wrap.classList.remove("fading"));
}
async function renderPage(div, token) {
  if (div.dataset.done || token !== renderToken) return;
  div.dataset.done = "1";
  const page = await pdfDoc.getPage(+div.dataset.page);
  if (token !== renderToken) return;
  const vp = page.getViewport({ scale: state.scale });
  const dpr = window.devicePixelRatio || 1;
  const c = document.createElement("canvas");
  c.width = Math.floor(vp.width * dpr); c.height = Math.floor(vp.height * dpr);
  c.style.width = vp.width + "px"; c.style.height = vp.height + "px";
  div.appendChild(c);
  await page.render({ canvasContext: c.getContext("2d"), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
  if (token !== renderToken) return;
  const tl = document.createElement("div"); tl.className = "textLayer"; div.appendChild(tl);
  await new pdfjsLib.TextLayer({ textContentSource: page.streamTextContent(), container: tl, viewport: vp }).render();
  if (token !== renderToken) return;
  const end = document.createElement("div"); end.className = "endOfContent"; tl.append(end);
  tl.addEventListener("mousedown", (e) => {
    end.classList.add("active"); tl.classList.add("selecting");
    // anchor right away (selectionchange is async), so even a fast drag into
    // the margin stops at the pressed line instead of the end of the page
    const span = e.target.closest("span");
    if (span && tl.contains(span)) span.parentElement.insertBefore(end, span.nextSibling);
  });
  textLayers.set(tl, end);
}
function currentRatio() { return pdfDoc ? (state.page - 0.5) / pdfDoc.numPages : 0; }
let resizeTimer;
function rescaleSoon(delay = 200) {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.view === "reader" && pdfDoc) layoutPages(currentRatio()); }, delay);
}

// Selection anchoring, as in pdf.js's own viewer (TextLayerBuilder): the
// invisible "endOfContent" block (expanded by its .active class) follows the
// selection's moving end, so dragging through gaps between lines or into the
// margin doesn't make the browser extend the selection to the end of the page.
const textLayers = new Map(); // textLayer div -> endOfContent div
let prevRange = null;
function resetLayer(end, tl) {
  tl.append(end); end.style.width = ""; end.style.height = "";
  end.classList.remove("active"); tl.classList.remove("selecting");
}
document.addEventListener("pointerup", () => textLayers.forEach(resetLayer));
window.addEventListener("blur", () => textLayers.forEach(resetLayer));
document.addEventListener("keyup", () => textLayers.forEach(resetLayer));
document.addEventListener("selectionchange", () => {
  const sel = document.getSelection();
  if (!sel.rangeCount) { textLayers.forEach(resetLayer); prevRange = null; return; }
  const active = new Set();
  for (let i = 0; i < sel.rangeCount; i++) {
    const r = sel.getRangeAt(i);
    for (const tl of textLayers.keys()) if (!active.has(tl) && r.intersectsNode(tl)) active.add(tl);
  }
  for (const [tl, end] of textLayers) {
    if (active.has(tl)) { end.classList.add("active"); tl.classList.add("selecting"); }
    else resetLayer(end, tl);
  }
  const range = sel.getRangeAt(0);
  const modifyStart = prevRange && (
    range.compareBoundaryPoints(Range.END_TO_END, prevRange) === 0 ||
    range.compareBoundaryPoints(Range.START_TO_END, prevRange) === 0);
  let anchor = modifyStart ? range.startContainer : range.endContainer;
  if (anchor.nodeType === Node.TEXT_NODE) anchor = anchor.parentNode;
  const parentTl = anchor.parentElement?.closest(".textLayer");
  const end = parentTl && textLayers.get(parentTl);
  if (end && anchor !== end) {
    end.style.width = parentTl.style.width; end.style.height = parentTl.style.height;
    anchor.parentElement.insertBefore(end, modifyStart ? anchor : anchor.nextSibling);
  }
  prevRange = range.cloneRange();
});

// ---------- selection → excerpt / ask ----------
function hideSelPop() { $("#selPop").hidden = true; state.sel = null; }
document.addEventListener("mouseup", (e) => {
  if (state.view !== "reader" || e.target.closest("#selPop")) return;
  setTimeout(() => {
    const sel = window.getSelection();
    const text = sel.toString().trim();
    const node = sel.anchorNode;
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    if (!text || !el || !$("#pdfPages").contains(el)) return hideSelPop();
    const page = el.closest(".pdf-page")?.dataset.page || state.page;
    state.sel = { text, page };
    const r = sel.getRangeAt(0).getBoundingClientRect(), mr = $("#main").getBoundingClientRect();
    const pop = $("#selPop"); pop.hidden = false; restartAnim(pop);
    pop.style.left = Math.min(Math.max(8, r.left - mr.left + r.width / 2 - pop.offsetWidth / 2), mr.width - pop.offsetWidth - 8) + "px";
    pop.style.top = Math.max(62, r.top - mr.top - pop.offsetHeight - 10) + "px";
  }, 0);
});
$("#selPop").addEventListener("mousedown", (e) => e.preventDefault());
$("#selPop").addEventListener("click", (e) => {
  const act = e.target.closest("button")?.dataset.act;
  if (!act || !state.sel) return;
  const { text, page } = state.sel;
  const clean = text.replace(/-\n(?=[a-z])/g, "").replace(/([㐀-鿿，。；：、（）])\s*\n\s*/g, "$1").replace(/\s*\n\s*/g, " ");
  window.getSelection().removeAllRanges(); hideSelPop();
  if (act === "excerpt") excerpt(clean, page);
  else askAbout(clean, page);
});
$("#pdfScroll").addEventListener("scroll", () => { if (!$("#selPop").hidden) hideSelPop(); }, { passive: true });

function excerpt(text, page) {
  const ed = $("#mdEdit");
  const src = ed.value.replace(/\s+$/, "");
  ed.value = (src ? src + "\n\n" : "") + `> ${text}\n> — 第 ${page} 页 · ${MODES[state.mode]}\n`;
  saveNotes(true);
  openPanel(); showTab("notes"); setEditing(false);
  const q = $$("#mdView blockquote").at(-1);
  if (q) { q.classList.add("flash"); q.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
}
function askAbout(text, page) {
  openPanel(); showTab("ai");
  const ta = $("#ask");
  ta.value = `> ${text}\n（第 ${page} 页）\n\n这段是什么意思？`;
  ta.dispatchEvent(new Event("input")); ta.focus();
}

// ---------- notes (notes.md on disk) ----------
let saveTimer, notesFor = null;
async function loadNotes() {
  const id = state.paper.id; notesFor = id;
  $("#mdEdit").value = ""; setEditing(false);
  try {
    const { content } = await api(`/api/papers/${encodeURIComponent(id)}/notes`);
    if (notesFor !== id) return;
    $("#mdEdit").value = content || ""; setEditing(false);
    setSaveState("已保存");
  } catch (e) { setSaveState("读取失败", true); }
}
function renderMd() {
  const view = $("#mdView");
  view.innerHTML = marked.parse($("#mdEdit").value || "");
  $$('input[type="checkbox"]', view).forEach((cb, i) => { cb.disabled = false; cb.dataset.i = i; });
}
function setEditing(on) {
  state.editing = on;
  const ed = $("#mdEdit"), view = $("#mdView"), btn = $("#notesMode");
  ed.hidden = !on; view.hidden = on;
  btn.textContent = on ? "完成" : "编辑"; btn.classList.toggle("on", on);
  if (on) { autosize(ed); ed.focus(); } else renderMd();
}
function setSaveState(text, bad) {
  const s = $("#saveState");
  s.textContent = text; s.classList.toggle("saving", text === "保存中…"); s.classList.toggle("bad", !!bad);
}
function saveNotes(now) {
  const id = state.paper.id, content = $("#mdEdit").value;
  setSaveState("保存中…");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      await api(`/api/papers/${encodeURIComponent(id)}/notes`, { method: "PUT", body: { content } });
      const p = paperById(id); if (p) p.has_notes = !!content.trim();
      if (state.paper?.id === id) setSaveState("已保存");
    } catch (e) { setSaveState("保存失败，稍后重试", true); }
  }, now ? 150 : 600);
}
function autosize(ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; }
$("#mdEdit").addEventListener("input", (e) => { autosize(e.target); saveNotes(); });
$("#mdEdit").addEventListener("keydown", (e) => {
  if (e.key === "Escape" || (e.key === "Enter" && e.metaKey)) { e.preventDefault(); setEditing(false); }
});
$("#notesMode").addEventListener("click", () => setEditing(!state.editing));
$("#mdView").addEventListener("click", (e) => {
  const cb = e.target.closest('input[type="checkbox"]');
  if (cb) {
    let n = -1;
    const ed = $("#mdEdit");
    ed.value = ed.value.replace(/^(\s*[-*+] \[)([ xX])(\])/gm, (m, a, x, b) => (++n === +cb.dataset.i ? a + (x === " " ? "x" : " ") + b : m));
    saveNotes(true); return;
  }
  if (!e.target.closest("a")) setEditing(true);
});

// ---------- panel ----------
function setPanel() {
  const lib = state.view === "library";
  $$(".tab-btn").forEach((b) => (b.hidden = lib));
  $("#panelTitle").hidden = !lib;
  app.classList.toggle("panel-closed", !state.panelOpen[state.view]);
  $("#panelBtn").classList.toggle("on", state.panelOpen.reader);
  $("#libAiBtn").classList.toggle("on", state.panelOpen.library);
  showTab(lib ? "ai" : state.readerTab);
}
function showTab(tab) {
  state.tab = tab;
  if (state.view === "reader") state.readerTab = tab;
  $("#notesPane").hidden = tab !== "notes";
  const ai = $("#aiPane"); const wasHidden = ai.hidden; ai.hidden = tab !== "ai";
  $("#newChat").hidden = tab !== "ai";
  $$(".tab-btn").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  const ind = $("#tabInd"), b = $(`.tab-btn[data-tab="${tab}"]`);
  if (state.view === "library" || !b) ind.style.width = "0";
  else { ind.style.width = b.offsetWidth - 20 + "px"; ind.style.transform = `translateX(${b.offsetLeft + 10}px)`; }
  if (tab === "ai") { renderChat(); if (wasHidden && state.panelOpen[state.view]) setTimeout(() => $("#ask").focus(), 50); }
}
function openPanel() { if (!state.panelOpen[state.view]) togglePanel(); }
function togglePanel() {
  state.panelOpen[state.view] = !state.panelOpen[state.view];
  setPanel();
  rescaleSoon(360);
}
$$(".tab-btn").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("#panelBtn").addEventListener("click", togglePanel);
$("#libAiBtn").addEventListener("click", togglePanel);
$("#panelClose").addEventListener("click", togglePanel);

// ---------- AI (DeepSeek via /api/chat) ----------
const SUGGEST = {
  reader: ["用三句话概括这篇论文", "这篇论文的核心方法是什么？", "把我的笔记整理成提纲"],
  library: ["最近加了哪些论文？", "找出讲注意力机制的论文", "检查分类是否合理，必要时重新分类"],
};
const chats = {};
const chatKey = () => (state.view === "library" ? "library" : `paper:${state.paper.id}`);
function getChat(key) { return (chats[key] ||= store.get(`ph-chat-${key}`, [])); }
function persistChat(key) {
  store.set(`ph-chat-${key}`, getChat(key).slice(-40).map((m) => ({
    role: m.role, text: m.text, error: m.error,
    steps: (m.steps || []).map((s) => ({ t: s.t, summary: s.summary, done: true })),
  })));
}
function renderChat() {
  const lib = state.view === "library";
  if (!lib && !state.paper) return;
  const log = getChat(chatKey());
  const notice = state.hasKey ? "" :
    `<div class="notice">还没有接入 DeepSeek。<button class="link" data-open-settings>填入 API key</button> 后就能提问、查找和整理论文。</div>`;
  $("#chat").innerHTML = notice + log.map(msgHtml).join("");
  $("#suggest").innerHTML = log.length ? "" : SUGGEST[lib ? "library" : "reader"].map((s) => `<button class="chip">${esc(s)}</button>`).join("");
  $("#scope").textContent = lib ? `范围：整个论文库 · ${state.lib.papers.length} 篇` : "范围：这篇论文的全文与你的笔记";
  $("#ask").placeholder = lib ? "查找、整理或提问…" : "就这篇论文提问…";
  $("#chat").scrollTop = 1e9;
}
function stepHtml(s) {
  return `<div class="step${s.done ? " done" : ""}"><span class="ic"></span><span>${esc(s.t)}${s.summary ? `<span class="sum"> · ${esc(s.summary)}</span>` : ""}</span></div>`;
}
function msgHtml(m) {
  if (m.role === "user") return `<div class="msg user">${esc(m.text)}</div>`;
  const body = (m.text ? marked.parse(m.text) : "") + (m.streaming && m.text ? '<span class="caret"></span>' : "");
  return `<div class="msg ai${m.error ? " err" : ""}"><div class="steps">${(m.steps || []).map(stepHtml).join("")}</div><div class="md">${body}</div>${m.streaming && !m.text && !(m.steps || []).length ? '<div class="thinking"><i></i><i></i><i></i></div>' : ""}</div>`;
}
let paintQueued = false;
function updateLast(m) {
  if (paintQueued) return;
  paintQueued = true;
  requestAnimationFrame(() => {
    paintQueued = false;
    const el = $$("#chat .msg").at(-1); if (!el) return;
    el.outerHTML = msgHtml(m).replace('class="msg ai', 'class="msg ai still');
    $("#chat").scrollTop = 1e9;
  });
}
function handleEvent(raw, m) {
  let ev = "message", data = "";
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) ev = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  let d = {}; try { d = data ? JSON.parse(data) : {}; } catch {}
  if (ev === "step") m.steps.push({ id: d.id, t: d.label, done: false });
  else if (ev === "step_done") { const s = m.steps.find((x) => x.id === d.id); if (s) { s.done = true; s.summary = d.summary; } }
  else if (ev === "delta") m.text += d.text || "";
  else if (ev === "library_changed") loadLibrary({ diff: true }).catch(() => {});
  else if (ev === "notes_changed") { if (state.paper?.id === d.paper_id) loadNotes(); const p = paperById(d.paper_id); if (p) p.has_notes = true; }
  else if (ev === "error") { m.error = true; m.text += (m.text ? "\n\n" : "") + `**出错了**：${d.message || "未知错误"}`; }
  updateLast(m);
}
async function runAI(text) {
  text = text.trim();
  if (state.busy || !text) return;
  if (!state.hasKey) { openSettings(); return; }
  state.busy = true; $("#sendBtn").disabled = true;
  const key = chatKey(), log = getChat(key);
  const history = log.filter((m) => m.text && !m.error).slice(-12)
    .map((m) => ({ role: m.role === "ai" ? "assistant" : "user", content: m.text }));
  log.push({ role: "user", text });
  const m = { role: "ai", steps: [], text: "", streaming: true };
  log.push(m); renderChat();
  const body = {
    scope: state.view === "library" ? "library" : "paper",
    paper_id: state.paper?.id, page: state.page, mode: state.mode,
    messages: [...history, { role: "user", content: text }],
  };
  try {
    const res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok || !res.body) { const e = await res.json().catch(() => ({})); throw new Error(e.error || `请求失败（${res.status}）`); }
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) { handleEvent(buf.slice(0, i), m); buf = buf.slice(i + 2); }
    }
  } catch (e) {
    m.error = true; m.text += (m.text ? "\n\n" : "") + `**出错了**：${e.message}`;
  }
  m.streaming = false; m.steps.forEach((s) => (s.done = true));
  updateLast(m); persistChat(key);
  state.busy = false; $("#sendBtn").disabled = !$("#ask").value.trim();
}
$("#suggest").addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) runAI(c.textContent); });
$("#chat").addEventListener("click", (e) => { if (e.target.closest("[data-open-settings]")) openSettings(); });
const ask = $("#ask");
ask.addEventListener("input", () => { autosize(ask); $("#sendBtn").disabled = state.busy || !ask.value.trim(); });
ask.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault(); const t = ask.value; ask.value = ""; autosize(ask); runAI(t);
});
$("#newChat").addEventListener("click", () => {
  if (state.busy) return;
  const key = chatKey(); chats[key] = []; persistChat(key); renderChat(); ask.focus();
});

// ---------- settings ----------
async function loadConfig() {
  try { const c = await api("/api/config"); state.hasKey = !!c.has_key; state.model = c.model || "deepseek-chat"; } catch {}
}
function openSettings() {
  const root = $("#modalRoot");
  root.innerHTML = `<div class="modal-wrap"><form class="modal" autocomplete="off">
    <h2>设置</h2>
    <label class="field"><span>DeepSeek API key</span>
      <input type="password" name="key" placeholder="${state.hasKey ? "已设置，留空则不修改" : "sk-…"}" spellcheck="false"></label>
    <label class="field"><span>模型</span>
      <input name="model" value="${esc(state.model)}" spellcheck="false"></label>
    <p class="hint">key 只保存在本机 <code>~/PaperHelper/config.json</code>，不会出现在界面或日志里。</p>
    <div class="modal-actions"><span class="msg-line"></span><button type="button" class="pill-btn" data-cancel>取消</button><button class="pill-btn primary">保存</button></div>
  </form></div>`;
  const form = $("form", root), close = () => (root.innerHTML = "");
  $("[data-cancel]", root).addEventListener("click", close);
  $(".modal-wrap", root).addEventListener("mousedown", (e) => { if (e.target.classList.contains("modal-wrap")) close(); });
  form.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { model: form.model.value.trim() || "deepseek-chat" };
    if (form.key.value.trim()) body.deepseek_api_key = form.key.value.trim();
    try {
      await api("/api/config", { method: "PUT", body });
      await loadConfig(); close(); toast(state.hasKey ? "设置已保存" : "已保存（还没有 API key）");
      if (!$("#aiPane").hidden) renderChat();
    } catch (err) { $(".msg-line", root).textContent = err.message; }
  });
  form.key.focus();
}
$("#settingsBtn").addEventListener("click", openSettings);

// ---------- toast ----------
let toastTimer;
function toast(text) {
  const root = $("#toastRoot");
  root.innerHTML = `<div class="toast"><span>${esc(text)}</span></div>`;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (root.innerHTML = ""), 3000);
}

// ---------- command palette ----------
function openPalette() {
  const root = $("#paletteRoot");
  if (root.innerHTML) { root.innerHTML = ""; return; }
  root.innerHTML = `<div class="palette-wrap"><div class="palette"><input placeholder="搜索论文、作者、分类，或直接提问…"><div class="pal-list"></div></div></div>`;
  const input = $("input", root), list = $(".pal-list", root);
  let items = [], sel = 0;
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    items = state.lib.papers
      .filter((p) => !q || `${p.title_zh} ${p.title_en} ${p.authors} ${p.category} ${p.year}`.toLowerCase().includes(q))
      .slice(0, 8).map((p) => ({ kind: "paper", p }));
    if (q) items.push({ kind: "ask", q: input.value.trim() });
    sel = Math.min(sel, Math.max(0, items.length - 1));
    list.innerHTML = items.map((it, i) => it.kind === "paper"
      ? `<div class="pal-item${i === sel ? " sel" : ""}" data-i="${i}"><div><div class="pt">${esc(it.p.title_zh || it.p.title_en)}</div><div class="ps">${esc(it.p.title_en)}</div></div><span class="kind">${esc(it.p.category)}</span></div>`
      : `<div class="pal-item ask${i === sel ? " sel" : ""}" data-i="${i}"><div class="pt">问论文库助手：${esc(it.q)}</div><span class="kind">↵</span></div>`).join("")
      || `<div class="pal-empty">论文库还是空的</div>`;
  };
  const choose = (i) => {
    const it = items[i]; if (!it) return; root.innerHTML = "";
    if (it.kind === "paper") location.hash = `#/paper/${it.p.id}`;
    else { location.hash = "#/"; setTimeout(() => { if (!state.panelOpen.library) togglePanel(); runAI(it.q); }, 60); }
  };
  input.addEventListener("input", () => { sel = 0; draw(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(items.length - 1, sel + 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); draw(); e.preventDefault(); }
    else if (e.key === "Enter" && !e.isComposing) choose(sel);
    else if (e.key === "Escape") root.innerHTML = "";
  });
  list.addEventListener("mousemove", (e) => { const it = e.target.closest(".pal-item"); if (it && +it.dataset.i !== sel) { sel = +it.dataset.i; draw(); } });
  list.addEventListener("click", (e) => { const it = e.target.closest(".pal-item"); if (it) choose(+it.dataset.i); });
  $(".palette-wrap", root).addEventListener("mousedown", (e) => { if (e.target.classList.contains("palette-wrap")) root.innerHTML = ""; });
  draw(); input.focus();
}
$("#openPalette").addEventListener("click", openPalette);

// ---------- global keys & nav ----------
document.addEventListener("keydown", (e) => {
  const typing = e.target.closest("input, textarea");
  const k = e.key.toLowerCase();
  if (e.metaKey && k === "k") { e.preventDefault(); openPalette(); }
  else if (e.metaKey && k === "j") { e.preventDefault(); togglePanel(); }
  else if (e.metaKey && e.key === ",") { e.preventDefault(); openSettings(); }
  else if (e.key === "Escape" && !typing && !$("#modalRoot").innerHTML) {
    if (!$("#selPop").hidden) hideSelPop();
    else if (state.view === "reader") location.hash = state.cat ? `#/cat/${encodeURIComponent(state.cat)}` : "#/";
  }
});
$(".nav").addEventListener("click", (e) => {
  const b = e.target.closest(".nav-item"); if (!b) return;
  state.highlight.clear();
  location.hash = b.dataset.cat ? `#/cat/${encodeURIComponent(b.dataset.cat)}` : "#/";
});
$("#paperList").addEventListener("click", (e) => {
  const row = e.target.closest(".paper"); if (row) location.hash = `#/paper/${row.dataset.id}`;
});
$("#backBtn").addEventListener("click", () => {
  location.hash = state.cat ? `#/cat/${encodeURIComponent(state.cat)}` : "#/";
});
$("#modeSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-mode]");
  if (!b || b.disabled || b.dataset.mode === state.mode) return;
  const ratio = currentRatio();
  state.mode = b.dataset.mode;
  history.replaceState(null, "", `#/paper/${state.paper.id}/${state.mode}`);
  setSeg(); loadPdf(ratio);
});
window.addEventListener("resize", () => { rescaleSoon(); if (state.view === "reader") setSeg(); showTab(state.tab); });
// refresh when returning to the window (Claude may have added a paper meanwhile)
window.addEventListener("focus", () => loadLibrary().catch(() => {}));

function route() {
  const parts = location.hash.slice(1).split("/").filter(Boolean).map(decodeURIComponent);
  $("#paletteRoot").innerHTML = "";
  if (parts[0] === "paper") openPaper(parts[1], parts[2]);
  else showLibrary(parts[0] === "cat" ? parts[1] || "" : "");
}
window.addEventListener("hashchange", route);

(async () => {
  await Promise.all([loadLibrary().catch((e) => toast(e.message)), loadConfig()]);
  route();
})();
