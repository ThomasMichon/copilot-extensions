"""Lightweight built-in status UX -- ``GET /ui``.

A single, dependency-free HTML page for inspecting bridge status (agents, ACP
sessions, and live interactive sessions) and copying the ACP-over-WebSocket
URLs to plug into an external ACP client such as acp-ui. The page is
auth-exempt static HTML; it reads the bridge token from a local input,
persisted to ``localStorage``, and calls the existing
token-protected ``/api/v1`` endpoints, so no new data surface is exposed
without auth.

For a live session it can also **watch** the represented event stream
(``/api/v1/live-sessions/{id}/events``) and **message** it
(``/api/v1/live-sessions/{id}/messages`` -- with selectable delivery urgency); for an
ACP session it can queue a follow-up turn (``/turns`` with ``queue``). Event
content is rendered as text only, under a restrictive Content-Security-Policy.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Agent Bridge - Status</title>
<link rel="icon" href="data:," />
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.5 system-ui, sans-serif; margin: 0; padding: 1.25rem;
         max-width: 1100px; margin-inline: auto; }
  h1 { font-size: 1.3rem; margin: 0 0 .25rem; }
  h2 { font-size: 1rem; margin: 1.5rem 0 .5rem; }
  summary { font-weight: 600; margin: 1.5rem 0 .5rem; cursor: pointer; }
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
  #panel { margin-top: 1.5rem; border: 1px solid #8884; border-radius: 8px; padding: .75rem; }
  #feed { max-height: 60vh; overflow: auto; font: 13px/1.45 ui-monospace, monospace;
          border: 1px solid #8883; border-radius: 6px; padding: .5rem; margin: .5rem 0; }
  .ev { white-space: pre-wrap; word-break: break-word; padding: .15rem 0;
        border-bottom: 1px dashed #8882; }
  .ev .lbl { font-weight: 600; margin-right: .4rem; }
  .ev.user_message .lbl { color: #36c; }
  .ev.agent_message .lbl { color: #2a8; }
  .ev.agent_thought, .ev.tool_call_update { opacity: .75; }
  .ev.ask_user_request, .ev.permission_request { color: #d80; }
  .ev.turn_complete { opacity: .6; text-align: center; }
  .ev.local_sent .lbl { color: #a5c; }
  .composer { display: flex; gap: .5rem; align-items: flex-start; flex-wrap: wrap; }
  .composer textarea { flex: 1; min-width: 20rem; font: inherit; padding: .4rem;
                       border-radius: 6px; border: 1px solid #8884; }
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

  <h2>Live sessions</h2>
  <p class="sub">Interactive Copilot CLI sessions registered with this bridge --
     local ones and those running in a venue (e.g. a detached CodeSpace session).
     <b>Watch</b> streams their activity; the composer can interrupt the current
     turn, steer it at the next step, or queue a message for after it ends.</p>
  <table id="live"><thead>
    <tr><th>Session</th><th>Where</th><th>Driven by</th><th>State</th>
        <th>Progress</th><th>Updated</th><th></th></tr>
  </thead><tbody></tbody></table>

  <section id="panel" hidden>
    <div class="bar"><b id="panel-title"></b><span id="panel-status" class="muted"></span>
      <button id="panel-close">Close</button></div>
    <div id="feed"></div>
    <div class="composer">
      <textarea id="msg" rows="3" placeholder="Message this session"></textarea>
      <select id="msg-kind">
        <option value="prompt">prompt</option>
        <option value="notify">notify</option>
        <option value="status-check">status-check</option>
      </select>
      <select id="msg-delivery">
        <option value="interrupt">Interrupt &amp; send</option>
        <option value="steer">Steer (next step)</option>
        <option value="queue">Queue (after turn)</option>
      </select>
      <button id="send">Send</button><span id="send-status" class="muted"></span>
    </div>
  </section>

  <!-- The ACP surfaces (agents to open sessions on; bridge-run sessions to
       adopt) stay one click away, collapsed below the live sessions. -->
  <details id="acp">
  <summary>ACP agents &amp; sessions <span id="acp-count" class="muted"></span></summary>
  <h2>Agents</h2>
  <table id="agents"><thead>
    <tr><th>Name</th><th>Description</th><th>Target</th><th>ACP WebSocket URL</th></tr>
  </thead><tbody></tbody></table>

  <h2>Sessions</h2>
  <table id="sessions"><thead>
    <tr><th>Session</th><th>Agent</th><th>Caller</th><th>Status</th><th>Turns</th>
        <th>Context</th><th>Adopt URL</th><th></th></tr>
  </thead><tbody></tbody></table>
  </details>


<script>
const $ = (s) => document.querySelector(s);
const TOKEN_KEY = "agentBridgeToken";
let token = localStorage.getItem(TOKEN_KEY) || "";
$("#token").value = token;

// `agent-bridge ui` opens this page with a one-time login code (single use,
// short-lived) -- never the token itself -- and the page trades it for the token.
async function adoptLoginCode() {
  const m = location.hash.match(/(?:^#|&)code=([^&]+)/);
  if (!m) return;
  history.replaceState(null, "", location.pathname + location.search);
  try {
    const r = await fetch("/ui/exchange", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: decodeURIComponent(m[1]) }),
    });
    if (!r.ok) { setStatus("That login link expired; run `agent-bridge ui` again.", true); return; }
    token = (await r.json()).token || "";
    localStorage.setItem(TOKEN_KEY, token);
    $("#token").value = token;
  } catch (e) { setStatus("Login exchange failed: " + e.message, true); }
}

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
    const [agents, sessions, live] = await Promise.all([
      api("/api/v1/agents"), api("/api/v1/sessions"), api("/api/v1/live-sessions"),
    ]);
    renderAgents(agents.agents || []);
    renderSessions(sessions.sessions || []);
    renderLive(live.live_sessions || []);
    const nA = (agents.agents || []).length, nS = (sessions.sessions || []).length;
    $("#acp-count").textContent = `(${nA} agent${nA === 1 ? "" : "s"}, ` +
      `${nS} session${nS === 1 ? "" : "s"})`;
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
      <td class="ws"><code>${esc(url)}</code> ${copyBtn(url)}</td>
      <td><button data-acp="${esc(s.session_id)}">Message</button></td></tr>`;
  });
  $("#sessions tbody").innerHTML = rows.join("") ||
    `<tr><td colspan="8" class="muted">No active sessions.</td></tr>`;
}

function age(ts) {
  if (!ts) return "-";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 90 ? s + "s ago" : s < 5400 ? Math.round(s / 60) + "m ago" : Math.round(s / 3600) + "h ago";
}

function renderLive(list) {
  list.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  const rows = list.map((s) => {
    const where = s.venue ? `${s.venue.kind}:${s.venue.target}` : (s.machine || "-");
    const st = esc(s.status || "");
    const state = [s.liveness, s.turn_state].filter(Boolean).join(" / ");
    const p = s.latest_progress || {};
    const progress = [p.summary, p.phase && ("phase " + p.phase), p.pr && ("PR " + p.pr),
                      p.blocker && ("blocked: " + p.blocker)].filter(Boolean).join(" · ");
    const dead = s.status === "expired" || s.status === "wedged";
    return `<tr>
      <td><code title="${esc(s.session_id)}">${esc(String(s.session_id).slice(0, 8))}</code>
          <div class="muted">${esc(s.worktree_id || "")}</div></td>
      <td>${esc(where)}${s.cli_mode ? ' <span class="pill">cli-mode</span>' : ""}</td>
      <td>${esc(s.driven_by || "operator")}</td>
      <td><span class="pill s-${st}">${st}</span> <span class="muted">${esc(state)}</span></td>
      <td>${esc(progress || "-")}</td>
      <td>${esc(age(s.updated_at))}</td>
      <td class="ws"><button data-watch="${esc(s.session_id)}">Watch</button>
          ${dead ? `<button data-remove="${esc(s.session_id)}">Remove</button>` : ""}</td></tr>`;
  });
  $("#live tbody").innerHTML = rows.join("") ||
    `<tr><td colspan="7" class="muted">No live sessions.</td></tr>`;
}

// -- watch + message panel ---------------------------------------------------
let watch = null;  // {kind: "live"|"acp", id, ctrl, lastId}
const MAX_FEED = 500;

function panelStatus(msg) { $("#panel-status").textContent = msg ? "  " + msg : ""; }

function clip(v, n) {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s && s.length > n ? s.slice(0, n) + " …" : (s || "");
}

function eventLine(type, data) {
  switch (type) {
    case "user_message": return ["user", data.content];
    case "agent_message": return ["agent", data.text];
    case "agent_thought": return ["thinking", clip(data.text, 600)];
    case "tool_call_start":
      return ["tool", `${data.title || "tool"} ${clip(data.raw_input, 300)}`];
    case "tool_call_update": {
      const out = Array.isArray(data.content) ? data.content.join("\n") : data.content;
      return [data.status === "failed" ? "tool failed" : "tool done", clip(out, 600)];
    }
    case "ask_user_request": return ["asks", data.message || ""];
    case "permission_request":
      return ["permission", [data.kind, data.intention, data.fullCommandText].filter(Boolean).join(" ")];
    case "turn_complete": return ["", "— turn complete —"];
    // A delivered inbox message is represented only by its attribution header,
    // so the page echoes what it sent itself.
    case "local_sent": return ["you (sent)", data.text];
    default: return null;
  }
}

function addEvent(type, data) {
  const entry = eventLine(type, data || {});
  if (!entry) return;
  const feed = $("#feed");
  const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40;
  const row = document.createElement("div");
  row.className = "ev " + type;
  if (entry[0]) {
    const lbl = document.createElement("span");
    lbl.className = "lbl";
    lbl.textContent = entry[0];
    row.appendChild(lbl);
  }
  row.appendChild(document.createTextNode(entry[1] == null ? "" : String(entry[1])));
  feed.appendChild(row);
  while (feed.childElementCount > MAX_FEED) feed.removeChild(feed.firstChild);
  if (atBottom) feed.scrollTop = feed.scrollHeight;
}

function handleBlock(w, block) {
  let id = null, type = "message", data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("id:")) id = parseInt(line.slice(3).trim(), 10);
    else if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (id != null && !Number.isNaN(id)) w.lastId = Math.max(w.lastId, id);
  if (!data || type === "bridge_control") return;
  try {
    const frame = JSON.parse(data);  // {event, data: {...}, timestamp}
    addEvent(type, (frame && typeof frame.data === "object" && frame.data) || frame || {});
  } catch (e) { /* ignore a malformed frame */ }
}

async function streamLive(w) {
  while (watch === w) {
    try {
      const r = await fetch(`/api/v1/live-sessions/${encodeURIComponent(w.id)}/events?after=${w.lastId}`,
        { headers: { Authorization: "Bearer " + token, Accept: "text/event-stream" }, signal: w.ctrl.signal });
      if (!r.ok) throw new Error("events -> " + r.status);
      panelStatus("streaming");
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          handleBlock(w, buf.slice(0, i));
          buf = buf.slice(i + 2);
        }
      }
    } catch (e) {
      if (w.ctrl.signal.aborted) return;
      panelStatus("reconnecting (" + e.message + ")");
    }
    await new Promise((res) => setTimeout(res, 2000));
  }
}

function openPanel(kind, id) {
  if (watch) watch.ctrl.abort();
  watch = { kind, id, ctrl: new AbortController(), lastId: 0 };
  $("#panel").hidden = false;
  $("#feed").hidden = kind !== "live";
  $("#feed").textContent = "";
  $("#msg-kind").hidden = kind !== "live";
  $("#msg-delivery").hidden = kind !== "live";
  if (kind === "live") $("#msg-delivery").value = "interrupt";
  $("#panel-title").textContent = (kind === "live" ? "Live session " : "Session ") + id;
  $("#msg").placeholder = kind === "live"
    ? "Message this session (choose interrupt, steer, or queue)"
    : "Queue a follow-up turn for this session (runs when the current turn settles)";
  panelStatus(kind === "live" ? "connecting" : "");
  $("#send-status").textContent = "";
  if (kind === "live") streamLive(watch);
  $("#panel").scrollIntoView({ behavior: "smooth" });
}

async function sendMessage() {
  const body = $("#msg").value.trim();
  if (!body || !watch) return;
  const key = "ui:" + (crypto.randomUUID ? crypto.randomUUID() : Date.now() + ":" + Math.random());
  const delivery = watch.kind === "live" ? $("#msg-delivery").value : "queue";
  const [url, payload] = watch.kind === "live"
    ? [`/api/v1/live-sessions/${encodeURIComponent(watch.id)}/messages`,
       { sender: "bridge-ui", body, kind: $("#msg-kind").value, delivery, idempotency_key: key }]
    : [`/api/v1/sessions/${encodeURIComponent(watch.id)}/turns`, { prompt: body, queue: true }];
  $("#send").disabled = true;
  try {
    const r = await fetch(url, {
      method: "POST", body: JSON.stringify(payload),
      headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    });
    const text = await r.text();
    if (!r.ok) throw new Error(r.status + " " + text.slice(0, 200));
    $("#msg").value = "";
    if (watch.kind === "live") addEvent("local_sent", { text: body });
    $("#send-status").textContent = watch.kind === "live"
      ? ({
          interrupt: "interrupting the current turn",
          steer: "steering the running turn",
          queue: "queued: runs after the current turn ends",
        }[delivery] || "queued: runs after the current turn ends")
      : (JSON.parse(text).queued ? "queued" : "sent");
  } catch (e) {
    $("#send-status").textContent = "failed: " + e.message;
  } finally {
    $("#send").disabled = false;
  }
}

async function removeLive(id) {
  const r = await fetch(`/api/v1/live-sessions/${encodeURIComponent(id)}`,
    { method: "DELETE", headers: { Authorization: "Bearer " + token } });
  setStatus(r.ok ? "Removed " + id : "Remove failed: " + r.status, !r.ok);
  refresh();
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-copy]");
  if (b) {
    navigator.clipboard.writeText(b.dataset.copy);
    b.textContent = "copied";
    setTimeout(() => (b.textContent = "copy"), 1200);
    return;
  }
  const w = e.target.closest("button[data-watch]");
  if (w) { openPanel("live", w.dataset.watch); return; }
  const a = e.target.closest("button[data-acp]");
  if (a) { openPanel("acp", a.dataset.acp); return; }
  const rm = e.target.closest("button[data-remove]");
  if (rm) removeLive(rm.dataset.remove);
});

$("#send").addEventListener("click", sendMessage);
$("#msg").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendMessage();
});
$("#panel-close").addEventListener("click", () => {
  if (watch) watch.ctrl.abort();
  watch = null;
  $("#panel").hidden = true;
});

$("#save").addEventListener("click", () => {
  token = $("#token").value.trim();
  localStorage.setItem(TOKEN_KEY, token);
  refresh();
});
$("#refresh").addEventListener("click", refresh);

adoptLoginCode().then(refresh);
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


#: Inline script/style only; data calls only to this origin; no framing.
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


#: A login code is single-use and valid this long (seconds).
LOGIN_CODE_TTL = 60.0


def _login_codes(request: Request) -> dict[str, float]:
    codes = getattr(request.app.state, "ui_login_codes", None)
    if codes is None:
        codes = request.app.state.ui_login_codes = {}
    now = time.monotonic()
    for code in [c for c, expiry in codes.items() if expiry <= now]:
        codes.pop(code, None)
    return codes


@router.post("/api/v1/ui/login-codes", include_in_schema=False)
async def create_login_code(request: Request) -> dict[str, Any]:
    """Mint a one-time code that lets a browser sign in to /ui (auth required).

    The URL a browser records carries only this code, never the bearer token:
    history (which may sync) keeps a code that is already used or expired.
    """
    code = secrets.token_urlsafe(24)
    _login_codes(request)[code] = time.monotonic() + LOGIN_CODE_TTL
    return {"code": code, "expires_in": LOGIN_CODE_TTL}


@router.post("/ui/exchange", include_in_schema=False)
async def exchange_login_code(request: Request) -> JSONResponse:
    """Trade a valid, unused login code for the bearer token (auth-exempt; single use)."""
    try:
        code = str((await request.json()).get("code") or "")
    except ValueError:
        code = ""
    if not code or _login_codes(request).pop(code, None) is None:
        return JSONResponse({"detail": "invalid or expired login code"}, status_code=403)
    return JSONResponse(
        {"token": request.app.state.auth_token}, headers={"Cache-Control": "no-store"},
    )


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def status_ui() -> HTMLResponse:
    """Serve the built-in status UX page."""
    return HTMLResponse(
        _PAGE,
        headers={"Content-Security-Policy": _CSP, "Referrer-Policy": "no-referrer"},
    )
