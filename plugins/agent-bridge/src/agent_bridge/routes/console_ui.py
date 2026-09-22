"""Full multi-panel console UX -- ``GET /ui/console``.

A single dependency-light HTML page realizing the harness vision: a localhost
browser GUI with a panel for the MAIN local Copilot CLI (live-session: SSE event
stream + prompt) and a panel per Codespace native session, each STREAMING that
codespace's full Copilot terminal (xterm.js over the native.v1 WebSocket) with
the ability to type into it.

Isolation is architectural: the browser talks to the bridge's
``/api/v1/native-executions`` + ``/api/v1/live-sessions`` surfaces DIRECTLY, so a
codespace's streamed chat never flows through the main CLI's context window.

Auth: the page is public (like /ui); it collects the bridge token locally and
attaches ``Authorization: Bearer`` to HTTP and the ``bearer.<token>``
subprotocol to WebSockets. xterm.js is loaded from a CDN (localhost dev tool);
if offline, terminals fall back to a plain <pre> renderer.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Agent Bridge - Console</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css" />
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font: 13px/1.5 system-ui, sans-serif; margin: 0; height: 100vh;
         display: flex; flex-direction: column; }
  header { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap;
           padding: .5rem .75rem; border-bottom: 1px solid #8884; }
  header h1 { font-size: 1rem; margin: 0 .75rem 0 0; }
  input[type=password], input[type=text] { padding: .35rem .5rem; border-radius: 6px;
         border: 1px solid #8884; font: inherit; }
  button { padding: .35rem .7rem; border-radius: 6px; border: 1px solid #8884;
           background: #8882; font: inherit; cursor: pointer; }
  button:hover { background: #8883; }
  button.on { background: #2a83; border-color: #2a8; }
  .muted { opacity: .65; } .err { color: #e44; }
  main { flex: 1; display: grid; grid-template-columns: 300px 1fr; min-height: 0; }
  #side { border-right: 1px solid #8884; overflow: auto; padding: .5rem; }
  #side h2 { font-size: .8rem; text-transform: uppercase; opacity: .6;
             margin: .75rem .25rem .25rem; }
  .item { padding: .35rem .5rem; border-radius: 6px; cursor: pointer;
          border: 1px solid transparent; }
  .item:hover { background: #8882; } .item.sel { background: #2a83; border-color: #2a8; }
  .item .id { font-family: ui-monospace, monospace; font-size: .72rem; opacity: .7;
              word-break: break-all; }
  .pill { display: inline-block; padding: 0 .4rem; border-radius: 999px;
          font-size: .72rem; border: 1px solid #8884; }
  .s-ready, .s-live, .s-active, .s-running { background: #2a83; }
  .s-starting, .s-idle { background: #fa03; }
  .s-stopped, .s-unreachable, .s-failed, .s-err, .s-stale { background: #f443; }
  .rm { float: right; margin-left: .35rem; border: 0; background: transparent; color: inherit;
        opacity: .45; cursor: pointer; font-size: .95rem; line-height: 1; padding: 0 .2rem; }
  .rm:hover { opacity: 1; color: #f66; }
  #stage { display: flex; flex-direction: column; min-height: 0; }
  #tabs { display: flex; gap: .25rem; padding: .4rem .5rem; border-bottom: 1px solid #8884;
          align-items: center; flex-wrap: wrap; }
  #panel { flex: 1; min-height: 0; position: relative; }
  .view { position: absolute; inset: 0; display: none; flex-direction: column; }
  .view.show { display: flex; }
  #term { flex: 1; min-height: 0; padding: .25rem; }
  pre.fallback { flex: 1; margin: 0; padding: .5rem; overflow: auto; white-space: pre-wrap;
                 font-family: ui-monospace, monospace; font-size: 12px; }
  .compose { display: flex; gap: .4rem; padding: .5rem; border-top: 1px solid #8884; }
  .compose input { flex: 1; }
  #mainlog { flex: 1; margin: 0; padding: .5rem; overflow: auto; white-space: pre-wrap;
             font-family: ui-monospace, monospace; font-size: 12px; }
  .status { font-size: .78rem; }
</style>
</head>
<body>
<header>
  <h1>Agent Bridge Console</h1>
  <button id="refresh">Refresh</button>
  <button id="showall" title="Also show stopped/unreachable sessions">show stopped</button>
  <span id="status" class="muted status"></span>
  <details style="margin-left:.5rem"><summary class="muted status" style="cursor:pointer">token (optional)</summary>
    <input id="token" type="password" placeholder="only needed for non-localhost access" size="34" />
    <button id="save">Save</button>
  </details>
  <span style="flex:1"></span>
  <span class="muted status">streams go browser&#8596;bridge&#8596;codespace (not the main context)</span>
</header>
<main>
  <div id="side">
    <h2>Main session</h2>
    <div id="live"></div>
    <h2>Codespaces</h2>
    <div id="cat-codespace"></div>
    <h2>Containers</h2>
    <div id="cat-container"></div>
    <h2>Worktrees</h2>
    <div id="cat-worktree"></div>
  </div>
  <div id="stage">
    <div id="tabs">
      <span id="sel-label" class="muted">Select a session</span>
      <span style="flex:1"></span>
      <span id="term-controls" style="display:none">
        <button data-role="writer" class="on">writer</button>
        <button data-role="observer">observer</button>
        <label><input type="checkbox" id="takeover" /> takeover</label>
        <button id="reattach">reattach</button>
        <button id="stopexec" title="retire the native execution">stop</button>
      </span>
    </div>
    <div id="panel">
      <div id="view-term" class="view">
        <div id="term"></div>
      </div>
      <div id="view-main" class="view">
        <pre id="mainlog"></pre>
        <div class="compose">
          <select id="mainvenue" title="Where should a new task be solved?">
            <option value="">venue: auto</option>
            <option value="codespace">codespace</option>
            <option value="container">container</option>
            <option value="worktree">worktree</option>
          </select>
          <input id="mainprompt" type="text" placeholder="Give the main session a task..." />
          <button id="mainsend">Send</button>
        </div>
      </div>
    </div>
  </div>
</main>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js"></script>
<script>
const $ = (s) => document.querySelector(s);
const TOKEN_KEY = "agentBridgeToken";
let token = localStorage.getItem(TOKEN_KEY) || "";
$("#token").value = token;
const httpBase = "";
const wsBase = (location.protocol === "https:" ? "wss://" : "ws://") + location.host;
let sel = null;              // {kind:'native'|'main', ...}
let termConn = null;         // current native terminal connection
let mainConn = null;         // current live-session SSE
let role = "writer";
let showAll = false;              // hide stopped/unreachable execs by default
const execCache = {};             // execId -> exec (so selecting never re-fetches)

function setStatus(m, e) { const el = $("#status"); el.textContent = m; el.className = "status " + (e ? "err" : "muted"); }
function esc(s){ return String(s==null?"":s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
async function api(path, opts) {
  const o = opts || {};
  o.headers = Object.assign({}, o.headers || {});
  if (token) o.headers["Authorization"] = "Bearer " + token; // optional on localhost
  const r = await fetch(httpBase + path, o);
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.status === 204 ? null : r.json();
}

// ---- sidebar listing ----
async function refresh() {
  try {
    const [live, native, worktrees] = await Promise.all([
      api("/api/v1/live-sessions").catch(() => ({ live_sessions: [] })),
      api("/api/v1/native-executions").catch(() => ({ executions: [] })),
      api("/api/v1/worktrees").catch(() => ({ worktrees: [] })),
    ]);
    renderLive(live.live_sessions || []);
    const execs = native.executions || [];
    for (const k in execCache) delete execCache[k];
    for (const e of execs) execCache[e.executionId] = e;
    renderNative("#cat-codespace", execs.filter(e => (e.provider || "codespace") === "codespace"), "codespace");
    renderNative("#cat-container", execs.filter(e => e.provider === "container"), "container");
    renderWorktrees(worktrees.worktrees || worktrees.list || []);
    setStatus("Updated " + new Date().toLocaleTimeString());
  } catch (e) { setStatus(e.message, true); }
}
function itemHtml(id, title, sub, statusClass, statusText, selKey) {
  const on = sel && sel.key === selKey ? " sel" : "";
  return `<div class="item${on}" data-key="${esc(selKey)}">
    <div>${esc(title)} <span class="pill s-${esc(statusClass)}">${esc(statusText)}</span></div>
    <div class="id">${esc(sub)}</div></div>`;
}
function fmtAge(sec){
  if(sec==null||isNaN(sec))return"";
  sec=Math.max(0,Math.floor(sec));
  if(sec<60)return sec+"s";
  if(sec<3600)return Math.floor(sec/60)+"m";
  return Math.floor(sec/3600)+"h";
}
function renderLive(list) {
  const now = Date.now()/1000;
  $("#live").innerHTML = list.map(s => {
    const age = (s.updated_at!=null) ? (now - s.updated_at) : null;
    const stale = (age!=null) && age > 90;            // no heartbeat for 90s => likely dead
    const label = stale ? "stale" : (s.turn_state || s.status || "live");
    const cls = stale ? "err" : (s.turn_state || s.status || "live").toLowerCase();
    const selKey = "main:" + s.session_id;
    const on = sel && sel.key === selKey ? " sel" : "";
    const title = s.repo || s.cwd || "main session";
    const sub = (s.role ? s.role + " " : "") + (s.session_id||"").slice(0,8)
      + ((age!=null) ? (" \u00b7 " + fmtAge(age) + " ago") : "");
    const rmTitle = "Deregister from the console (a live process may re-register)";
    return `<div class="item${on}" data-key="${esc(selKey)}">
      <div>${esc(title)} <span class="pill s-${esc(cls)}">${esc(label)}</span>
        <button class="rm" data-rm="${esc(s.session_id)}" title="${rmTitle}">\u00d7</button></div>
      <div class="id">${esc(sub)}</div></div>`;
  }).join("") || `<div class="muted" style="padding:.35rem">No main session registered yet.</div>`;
}
async function removeLive(sid){
  const msg = "Remove this session from the console list?\n\nThis only deregisters it. "
    + "If its process is alive it re-registers in seconds (so it is live, not a ghost).";
  if(!confirm(msg))return;
  try{ await api("/api/v1/live-sessions/"+encodeURIComponent(sid),{method:"DELETE"}); setTimeout(refresh, 300); }
  catch(e){ setStatus(e.message, true); }
}
const DEAD_STATES = new Set(["stopped","failed","rejected","stopping","retired","gone","unreachable"]);
function renderNative(target, list, kind) {
  const shown = showAll ? list : list.filter(e => !DEAD_STATES.has((e.state||"").toLowerCase()));
  const hidden = list.length - shown.length;
  const body = shown.map(e => itemHtml(
    e.executionId, e.codespace || (e.target||"").split(":")[1] || kind,
    (e.sessionId||"").slice(0,8) + " g:" + (e.generation||"").slice(0,6),
    (e.state||"").toLowerCase(), e.state || "?",
    "native:" + e.executionId
  )).join("");
  const note = hidden > 0
    ? `<div class="muted" style="padding:.35rem;font-size:.72rem">${hidden} stopped/unreachable hidden \u2014 use "show stopped"</div>`
    : "";
  $(target).innerHTML = (body || `<div class="muted" style="padding:.35rem">No ${kind} sessions.</div>`) + note;
}
function renderWorktrees(list) {
  $("#cat-worktree").innerHTML = (Array.isArray(list) ? list : []).map(w => itemHtml(
    w.worktree_id || w.id, w.repo || w.worktree_id || w.id || "worktree",
    (w.branch ? w.branch + " " : "") + String(w.worktree_id || w.id || "").slice(0,16),
    (w.status || "").toLowerCase(), w.status || "-",
    "worktree:" + (w.worktree_id || w.id)
  )).join("") || `<div class="muted" style="padding:.35rem">No worktrees.</div>`;
}

// ---- selection ----
document.addEventListener("click", (e) => {
  const rm = e.target.closest("[data-rm]");
  if (rm) { e.stopPropagation(); removeLive(rm.dataset.rm); return; }
  const it = e.target.closest(".item[data-key]");
  if (!it) return;
  const key = it.dataset.key;
  if (key.startsWith("native:")) selectNative(key.slice(7));
  else if (key.startsWith("main:")) selectMain(key.slice(5));
  else if (key.startsWith("worktree:")) selectWorktree(key.slice(9));
});

function selectWorktree(id) {
  sel = { kind: "worktree", key: "worktree:" + id, id };
  $("#sel-label").textContent = "worktree " + id;
  markSelection();
  showView("main");
  const log = $("#mainlog");
  log.textContent = "Worktree " + id + "\n(Local worktree — drive it by tasking the main session.)\n";
}

function showView(which) {
  $("#view-term").classList.toggle("show", which === "term");
  $("#view-main").classList.toggle("show", which === "main");
  $("#term-controls").style.display = which === "term" ? "" : "none";
}

// Highlight the selected item WITHOUT re-fetching/re-rendering the sidebar --
// selection must be instant even while the (slow) native-executions list probe
// is in flight.
function markSelection() {
  document.querySelectorAll("#side .item").forEach(it => {
    it.classList.toggle("sel", !!sel && it.dataset.key === sel.key);
  });
}

// ---- xterm helper (with graceful fallback) ----
let term = null, fitAddon = null;
function ensureTerm() {
  if (window.Terminal) {
    if (!term) {
      term = new window.Terminal({ convertEol: true, fontSize: 12, scrollback: 5000 });
      if (window.FitAddon && window.FitAddon.FitAddon) {
        fitAddon = new window.FitAddon.FitAddon();
        term.loadAddon(fitAddon);
      }
      term.open($("#term"));
      term.onData(d => { if (termConn) termConn.sendInput(d); });
    }
    return term;
  }
  // fallback: plain <pre>
  if (!$("#term pre")) $("#term").innerHTML = '<pre class="fallback"></pre>';
  return { write: (s) => { const p = $("#term pre"); p.textContent += s; p.scrollTop = p.scrollHeight; },
           reset: () => { const p = $("#term pre"); if (p) p.textContent = ""; } };
}
// Fit the terminal to the panel AND push the real cols/rows to the remote PTY,
// so output isn't stuck wrapping at the default 80x24 (the "busted" look).
function fitTerm() {
  if (fitAddon) { try { fitAddon.fit(); } catch (e) {} }
  if (term && term.rows && termConn && termConn.resize) termConn.resize(term.rows, term.cols);
}
let fitTimer = null;
window.addEventListener("resize", () => { clearTimeout(fitTimer); fitTimer = setTimeout(fitTerm, 120); });

function selectNative(execId) {
  // Use the cached exec from the last refresh -- never re-fetch the slow
  // native-executions list on a click (that was the source of the "hang").
  const ex = execCache[execId];
  if (!ex) { setStatus("execution not in the current list; press Refresh", true); return; }
  sel = { kind: "native", key: "native:" + execId, exec: ex };
  $("#sel-label").textContent = (ex.codespace || execId) + "  [" + (ex.state||"") + "]";
  markSelection();
  showView("term");
  openTerminal(ex);
}
function openTerminal(ex) {
  if (termConn) { termConn.close(); termConn = null; }
  const t = ensureTerm(); t.reset && t.reset();
  let lastAck = 0, ws = null, closed = false;
  const url = `${wsBase}/api/v1/native-executions/${encodeURIComponent(ex.executionId)}/terminal`
    + `?generation=${encodeURIComponent(ex.generation)}&role=${role}`
    + `&takeover=${$("#takeover").checked ? "true" : "false"}&after=${lastAck}`;
  ws = new WebSocket(url, token ? ["native.v1", "bearer." + token] : ["native.v1"]);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { setStatus("terminal connected (" + role + ")"); setTimeout(fitTerm, 40); };
  ws.onclose = (ev) => { if (closed) return;
    const m = {4409:"writer busy (someone else is writing)",4410:"writer revoked (taken over)",
               4403:"read only (observer)",1008:"auth failed"}[ev.code];
    setStatus("terminal closed" + (m ? ": " + m : " (" + ev.code + ")"), ev.code >= 4000); };
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      const c = JSON.parse(ev.data);
      if (c.type === "exit") { t.write("\r\n[child exited: " + c.exitCode + "]\r\n"); }
      return;
    }
    const b = new Uint8Array(ev.data);
    const seq = Number(new DataView(b.buffer, b.byteOffset, 8).getBigUint64(0, false));
    t.write(new TextDecoder().decode(b.subarray(8)));
    lastAck = seq;
    try { ws.send(JSON.stringify({ type: "ack", sequence: seq })); } catch (e) {}
  };
  termConn = {
    close: () => { closed = true; try { ws.send(JSON.stringify({type:"detach"})); } catch(e){} try { ws.close(); } catch(e){} },
    sendInput: (d) => { try { ws.send(new TextEncoder().encode(d)); } catch(e){} },
    resize: (rows, cols) => { try { ws.send(JSON.stringify({ type: "resize", rows: rows, columns: cols })); } catch(e){} },
  };
}

async function selectMain(sid) {
  sel = { kind: "main", key: "main:" + sid, sid };
  $("#sel-label").textContent = "main CLI  " + sid.slice(0,12);
  markSelection();
  showView("main");
  openMain(sid);
}
function openMain(sid) {
  if (mainConn) { mainConn.close(); mainConn = null; }
  const log = $("#mainlog"); log.textContent = "";
  const append = (s) => { log.textContent += s + "\n"; log.scrollTop = log.scrollHeight; };
  // The represented-event feed is an SSE stream (text/event-stream), NOT a JSON
  // poll. Loopback is token-free, so EventSource (which cannot set an auth
  // header) connects directly. Each represented event arrives as a NAMED SSE
  // event with a JSON data payload (see live_representation.translate_sdk_event).
  const url = `/api/v1/live-sessions/${encodeURIComponent(sid)}/events?after=0`;
  let es;
  try { es = new EventSource(url); }
  catch (e) { append("(could not open the session stream: " + e.message + ")"); return; }
  append("(streaming the main session -- its full reasoning appears here)");
  const render = (name) => (ev) => {
    let outer = {}; try { outer = JSON.parse(ev.data); } catch { outer = {}; }
    const d = outer.data || outer; // represented payload is nested under .data
    if (name === "user_message") append("\n\u00bb " + (d.content || ""));
    else if (name === "agent_message") append(d.text || "");
    else if (name === "agent_thought") append("\u00b7 " + (d.text || ""));
    else if (name === "tool_call_start") {
      append("\u23f5 " + (d.kind || d.name || "tool") + (d.fullCommandText ? ": " + d.fullCommandText : ""));
    } else if (name === "permission_request") {
      append("\u26a0 permission requested (answer in the CLI or here): " + (d.kind || d.intention || ""));
    } else if (name === "turn_complete") append("\u2014 turn complete \u2014");
  };
  const evNames = ["user_message", "agent_message", "agent_thought",
    "tool_call_start", "permission_request", "turn_complete"];
  for (const n of evNames) { es.addEventListener(n, render(n)); }
  es.onerror = () => { /* EventSource auto-reconnects; stay quiet to avoid noise */ };
  mainConn = { close: () => { try { es.close(); } catch (e) {} } };
}
function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }

async function sendMain() {
  if (!sel || sel.kind !== "main") return;
  const task = $("#mainprompt").value.trim(); if (!task) return;
  const venue = $("#mainvenue").value;
  // An optional venue hint; the main session still does the real venue
  // resolution (agent-worktrees related resolve + odsp-web-harness / dispatching-work)
  // and may explain if the hinted venue isn't viable.
  const body = venue ? ("[venue: " + venue + "] " + task) : task;
  $("#mainprompt").value = "";
  try {
    await api(`/api/v1/live-sessions/${encodeURIComponent(sel.sid)}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sender: "browser-gui", body }),
    });
    const log = $("#mainlog"); log.textContent += "> " + body + "\n"; log.scrollTop = log.scrollHeight;
  } catch (e) { setStatus(e.message, true); }
}

// ---- controls ----
$("#save").onclick = () => { token = $("#token").value.trim(); localStorage.setItem(TOKEN_KEY, token); refresh(); };
$("#refresh").onclick = refresh;
$("#showall").onclick = () => { showAll = !showAll; $("#showall").classList.toggle("on", showAll); refresh(); };
$("#mainsend").onclick = sendMain;
$("#mainprompt").addEventListener("keydown", e => { if (e.key === "Enter") sendMain(); });
document.querySelectorAll("#term-controls button[data-role]").forEach(b => {
  b.onclick = () => { role = b.dataset.role;
    document.querySelectorAll("#term-controls button[data-role]").forEach(x => x.classList.toggle("on", x === b));
    if (sel && sel.kind === "native") openTerminal(sel.exec); };
});
$("#reattach").onclick = () => { if (sel && sel.kind === "native") openTerminal(sel.exec); };
$("#stopexec").onclick = async () => {
  if (!sel || sel.kind !== "native") return;
  if (!confirm("Retire this native execution?")) return;
  try { await api(`/api/v1/native-executions/${encodeURIComponent(sel.exec.executionId)}/stop`,
    { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ generation: sel.exec.generation, force: true }) });
    setStatus("stop requested"); refresh();
  } catch (e) { setStatus(e.message, true); }
};

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


@router.get("/ui/console", response_class=HTMLResponse, include_in_schema=False)
async def console_ui() -> str:
    """Serve the full multi-panel console UX page."""
    return _PAGE
