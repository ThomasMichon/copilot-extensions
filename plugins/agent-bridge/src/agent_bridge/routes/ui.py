"""Lightweight built-in status UX -- ``GET /ui``.

A single, dependency-free HTML page for inspecting bridge status (agents and
live sessions) and copying the ACP-over-WebSocket URLs to plug into an external
ACP client such as acp-ui. The page is auth-exempt static HTML; it reads the
bridge token from a local input (persisted to ``localStorage``) and calls the
existing token-protected ``/api/v1`` endpoints, so no new data surface is
exposed without auth.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Agent Bridge - Status</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.5 system-ui, sans-serif; margin: 0; padding: 1.25rem;
         max-width: 1100px; margin-inline: auto; }
  h1 { font-size: 1.3rem; margin: 0 0 .25rem; }
  h2 { font-size: 1rem; margin: 1.5rem 0 .5rem; }
  .sub { opacity: .7; margin: 0 0 1rem; }
  .bar { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap;
         margin-bottom: 1rem; }
  input[type=password], input[type=text] { padding: .4rem .5rem; border-radius: 6px;
         border: 1px solid #8884; min-width: 22rem; font: inherit; }
  button { padding: .4rem .8rem; border-radius: 6px; border: 1px solid #8884;
           background: #8882; font: inherit; cursor: pointer; }
  button:hover { background: #8883; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8883;
           vertical-align: top; }
  th { font-weight: 600; opacity: .8; }
  code { font-family: ui-monospace, monospace; background: #8882; padding: .1rem .3rem;
         border-radius: 4px; word-break: break-all; }
  .pill { display: inline-block; padding: .05rem .5rem; border-radius: 999px;
          font-size: .8rem; border: 1px solid #8884; }
  .s-running, .s-idle { background: #2a82; }
  .s-starting { background: #fa02; }
  .s-failed, .s-stopped, .s-disconnected { background: #f442; }
  .muted { opacity: .6; }
  .err { color: #e44; }
  .copy { font-size: .75rem; padding: .1rem .4rem; }
  .ws { white-space: nowrap; }
</style>
</head>
<body>
  <h1>Agent Bridge</h1>
  <p class="sub">Built-in status UX. Connect an external ACP client (e.g.
     <a href="https://acp-ui.github.io/" target="_blank" rel="noopener">acp-ui</a>)
     to any <code>ws://</code> URL below using transport
     <b>websocket</b> and <code>Authorization: Bearer &lt;token&gt;</code>.</p>

  <div class="bar">
    <input id="token" type="password" placeholder="Bridge token (run: agent-bridge token)" />
    <button id="save">Save token</button>
    <button id="refresh">Refresh</button>
    <span id="status" class="muted"></span>
  </div>
  <p class="sub">Token: run <code>agent-bridge token</code> (or read
     <code>~/.agent-bridge/auth.yaml</code>).</p>

  <h2>Agents</h2>
  <table id="agents"><thead>
    <tr><th>Name</th><th>Description</th><th>Target</th><th>ACP WebSocket URL</th></tr>
  </thead><tbody></tbody></table>

  <h2>Sessions</h2>
  <table id="sessions"><thead>
    <tr><th>Session</th><th>Agent</th><th>Caller</th><th>Status</th><th>Turns</th>
        <th>Context</th><th>Adopt URL</th></tr>
  </thead><tbody></tbody></table>

<script>
const $ = (s) => document.querySelector(s);
const TOKEN_KEY = "agentBridgeToken";
let token = localStorage.getItem(TOKEN_KEY) || "";
$("#token").value = token;

const wsBase = (location.protocol === "https:" ? "wss://" : "ws://") + location.host;

function setStatus(msg, isErr) {
  const el = $("#status");
  el.textContent = msg;
  el.className = isErr ? "err" : "muted";
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

function copyBtn(text) {
  return `<button class="copy" data-copy="${esc(text)}">copy</button>`;
}

async function api(path) {
  const r = await fetch(path, { headers: { Authorization: "Bearer " + token } });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

async function refresh() {
  if (!token) { setStatus("Enter the bridge token to load status.", true); return; }
  setStatus("Loading...");
  try {
    const [agents, sessions] = await Promise.all([
      api("/api/v1/agents"), api("/api/v1/sessions"),
    ]);
    renderAgents(agents.agents || []);
    renderSessions(sessions.sessions || []);
    setStatus("Updated " + new Date().toLocaleTimeString());
  } catch (e) {
    setStatus(e.message, true);
  }
}

function renderAgents(list) {
  const rows = list.map((a) => {
    const name = a.name || a.display_name || "";
    const url = wsBase + "/acp/" + encodeURIComponent(name);
    const target = a.host ? ("ssh:" + a.host) : (a.target_type || "local");
    return `<tr>
      <td><b>${esc(a.display_name || name)}</b><div class="muted">${esc(name)}</div></td>
      <td>${esc(a.description || "")}</td>
      <td>${esc(target)}</td>
      <td class="ws"><code>${esc(url)}</code> ${copyBtn(url)}</td></tr>`;
  });
  $("#agents tbody").innerHTML = rows.join("") ||
    `<tr><td colspan="4" class="muted">No agents registered.</td></tr>`;
}

function renderSessions(list) {
  const rows = list.map((s) => {
    const url = wsBase + "/acp/session/" + encodeURIComponent(s.session_id);
    const ctx = (s.context_pct != null) ? (s.context_pct.toFixed(0) + "%") : "-";
    const st = esc(s.status || "");
    return `<tr>
      <td><code>${esc(s.session_id)}</code><div class="muted">${esc(s.name || "")}</div></td>
      <td>${esc(s.agent_name || "-")}</td>
      <td>${esc(s.caller_id || "-")}</td>
      <td><span class="pill s-${st}">${st}</span></td>
      <td>${esc(s.turn_count != null ? s.turn_count : "-")}</td>
      <td>${esc(ctx)}</td>
      <td class="ws"><code>${esc(url)}</code> ${copyBtn(url)}</td></tr>`;
  });
  $("#sessions tbody").innerHTML = rows.join("") ||
    `<tr><td colspan="7" class="muted">No active sessions.</td></tr>`;
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-copy]");
  if (b) {
    navigator.clipboard.writeText(b.dataset.copy);
    b.textContent = "copied";
    setTimeout(() => (b.textContent = "copy"), 1200);
  }
});

$("#save").addEventListener("click", () => {
  token = $("#token").value.trim();
  localStorage.setItem(TOKEN_KEY, token);
  refresh();
});
$("#refresh").addEventListener("click", refresh);

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def status_ui() -> str:
    """Serve the built-in status UX page."""
    return _PAGE
