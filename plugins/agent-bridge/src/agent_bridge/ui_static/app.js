// Agent Bridge control surface: the task board, task detail with the session
// viewer, a sessions table, and the ACP surfaces. Talks only to this bridge's
// own token-protected /api/v1 routes.

import { h, clear, replaceChildren, copyText } from "./dom.js";
import { buildTasks, filterTasks, BUCKETS, ago, sessionBucket, parseStamp } from "./model.js";
import { SessionViewer } from "./viewer.js";

const TOKEN_KEY = "agentBridgeToken";
const CACHE_KEY = "agentBridgeBoard";
const THEME_KEY = "agentBridgeTheme";
const PENDING_KEY = "agentBridgePending";
const PROJECT_KEY = "agentBridgeProject";
const EARLIER_SHOWN = 8;
const $ = (s) => document.querySelector(s);

let token = localStorage.getItem(TOKEN_KEY) || "";
const state = {
  live: [], workspaces: [], projects: [], errors: {}, tasks: [], loaded: false, lastOk: 0, inflight: false,
  wsLoaded: false, wsInflight: false, showAllEarlier: false,
  pending: JSON.parse(sessionStorage.getItem(PENDING_KEY) || "{}"),
  route: { view: "tasks", key: null, sid: null, q: "", venue: "all", repo: "all" },
};

// -- auth + requests ------------------------------------------------------------

class AuthError extends Error {}

async function request(path, init = {}) {
  const headers = { ...(init.headers || {}), Authorization: "Bearer " + token };
  const r = await fetch(path, { ...init, headers });
  if (r.status === 401 || r.status === 403) {
    if (!path.startsWith("/ui/")) { signOut("Your sign-in expired. Run `agent-bridge ui` again."); }
    throw new AuthError("not signed in");
  }
  return r;
}

async function api(path) {
  const r = await request(path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`.trim());
  return r.json();
}

// `agent-bridge ui` opens this page with a one-time login code (single use,
// short-lived) -- never the token itself -- and the page trades it for the token.
async function adoptLoginCode() {
  const m = location.hash.match(/(?:^#|&)code=([^&]+)/);
  if (!m) return;
  history.replaceState(null, "", location.pathname + location.search + "#/");
  try {
    const r = await fetch("/ui/exchange", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: decodeURIComponent(m[1]) }),
    });
    if (!r.ok) { showSignIn("That sign-in link expired. Run `agent-bridge ui` again."); return; }
    setToken((await r.json()).token || "");
  } catch (e) {
    showSignIn("Sign-in failed: " + e.message);
  }
}

function setToken(t) {
  token = t;
  if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY);
}

function signOut(message) {
  setToken("");
  sessionStorage.removeItem(CACHE_KEY);
  showSignIn(message || "");
}

function showSignIn(message) {
  $("#app").hidden = true;
  $("#signin").hidden = false;
  $("#signin-msg").textContent = message || "";
  $("#signin-msg").hidden = !message;
}

function showApp() {
  $("#signin").hidden = true;
  $("#app").hidden = false;
}

// -- routing ------------------------------------------------------------------

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(query);
  const route = {
    view: "tasks", key: null, sid: null, q: params.get("q") || "",
    venue: params.get("venue") || "all", repo: params.get("repo") || "all",
  };
  if (parts[0] === "task" && parts[1]) { route.key = parts[1]; route.sid = parts[2] || null; }
  else if (parts[0] === "sessions") route.view = "sessions";
  else if (parts[0] === "acp") route.view = "acp";
  return route;
}

function routeHash(r) {
  let path = r.view === "sessions" ? "sessions" : r.view === "acp" ? "acp"
    : r.key ? "task/" + encodeURIComponent(r.key) + (r.sid ? "/" + encodeURIComponent(r.sid) : "") : "";
  const params = new URLSearchParams();
  if (r.q) params.set("q", r.q);
  if (r.venue && r.venue !== "all") params.set("venue", r.venue);
  if (r.repo && r.repo !== "all") params.set("repo", r.repo);
  const qs = params.toString();
  return "#/" + path + (qs ? "?" + qs : "");
}

function navigate(patch, { replace = false } = {}) {
  const next = { ...state.route, ...patch };
  const hash = routeHash(next);
  if (hash === location.hash) return;
  if (replace) history.replaceState(null, "", hash); else history.pushState(null, "", hash);
  onRoute();
}

function onRoute() {
  const prev = state.route;
  state.route = parseRoute();
  if ($("#q").value !== state.route.q) $("#q").value = state.route.q;
  for (const tab of document.querySelectorAll(".tabs a")) {
    tab.classList.toggle("on", tab.dataset.view === state.route.view);
  }
  $("#tasks-view").hidden = state.route.view !== "tasks";
  $("#venues").hidden = state.route.view !== "tasks";
  $("#repos").hidden = state.route.view !== "tasks";
  $("#sessions-view").hidden = state.route.view !== "sessions";
  $("#acp-view").hidden = state.route.view !== "acp";
  if (state.route.view === "acp" && prev.view !== "acp") loadAcp();
  render();
}

// -- data -----------------------------------------------------------------------

async function refresh() {
  if (!token || state.inflight) return;
  state.inflight = true;
  let live;
  try { live = await api("/api/v1/live-sessions"); } catch (e) { live = e; }
  state.inflight = false;
  if (live instanceof AuthError) return;
  if (live instanceof Error) state.errors.live = live.message;
  else {
    delete state.errors.live;
    state.live = live.live_sessions || [];
    state.loaded = true;
    state.lastOk = Date.now();
    prunePending();
    saveCache();
  }
  render();
}

// Workspaces (every worktree, including old ones) come from a slower,
// server-cached listing; the board never waits for it.
async function refreshWorkspaces(force = false) {
  if (!token || state.wsInflight) return;
  state.wsInflight = true;
  try {
    const d = await api("/api/v1/ui/workspaces" + (force ? "?refresh=true" : ""));
    state.workspaces = d.workspaces || [];
    state.projects = d.projects || [];
    state.wsLoaded = true;
    const errs = Object.entries(d.errors || {});
    if (errs.length) state.errors.workspaces = errs.map(([k, v]) => `${k}: ${v}`).join("; ");
    else delete state.errors.workspaces;
    saveCache();
  } catch (e) {
    if (!(e instanceof AuthError)) state.errors.workspaces = e.message;
  } finally {
    state.wsInflight = false;
  }
  render();
}

function saveCache() {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({
      live: state.live, workspaces: state.workspaces, projects: state.projects, at: state.lastOk,
    }));
  } catch (e) { /* storage full; the cache is only a paint accelerator */ }
}

function savePending() {
  sessionStorage.setItem(PENDING_KEY, JSON.stringify(state.pending));
}

// A launch shows as "Starting" until its session registers (or 10 minutes pass).
function prunePending() {
  const live = new Set(state.live.map((s) => s.worktree_id));
  const now = Date.now() / 1000;
  let changed = false;
  for (const [key, p] of Object.entries(state.pending)) {
    if (live.has(key) || now - p.at > 600) { delete state.pending[key]; changed = true; }
  }
  if (changed) savePending();
}
function paintFromCache() {
  try {
    const c = JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null");
    if (!c) return;
    state.live = c.live || [];
    state.workspaces = c.workspaces || [];
    state.projects = c.projects || [];
    state.wsLoaded = state.workspaces.length > 0;
    state.loaded = true;
    state.lastOk = c.at || 0;
    state.stale = true;
    render();
  } catch (e) { /* ignore a corrupt cache */ }
}

let pollTimer = null;
function schedulePoll() {
  clearTimeout(pollTimer);
  const every = document.hidden ? 30000 : 5000;
  pollTimer = setTimeout(async () => { await refresh(); state.stale = false; schedulePoll(); }, every);
}

let wsTimer = null;
function scheduleWorkspaces() {
  clearTimeout(wsTimer);
  wsTimer = setTimeout(async () => { await refreshWorkspaces(); scheduleWorkspaces(); }, document.hidden ? 120000 : 30000);
}

// -- rendering ----------------------------------------------------------------------

const VENUE_LABEL = { codespace: "CodeSpace", container: "Container", ssh: "SSH", local: "Local" };
const ROLE_LABEL = { worker: "Worker", orchestrator: "Orchestrator", session: "Session", previous: "Earlier session" };
const BUCKET_LABEL = Object.fromEntries(BUCKETS);

function render() {
  state.tasks = buildTasks(state.live, state.workspaces, state.pending);
  renderStatus();
  renderSummary();
  if (state.route.view === "tasks") { renderVenueChips(); renderBoard(); renderDetail(); }
  if (state.route.view === "sessions") renderSessions();
}

function renderStatus() {
  const el = $("#net");
  const errs = Object.entries(state.errors);
  if (state.errors.live) {
    el.className = "net bad";
    el.textContent = "Can't reach the bridge — showing " + (state.lastOk ? "data from " + ago(state.lastOk / 1000) : "nothing yet");
  } else if (state.stale) {
    el.className = "net muted";
    el.textContent = "Refreshing…";
  } else {
    el.className = "net muted";
    el.textContent = state.lastOk ? "Updated " + new Date(state.lastOk).toLocaleTimeString() : "Loading…";
  }
  el.title = errs.map(([k, v]) => `${k}: ${v}`).join("\n");
}

function renderSummary() {
  const counts = {};
  for (const t of state.tasks) counts[t.bucket] = (counts[t.bucket] || 0) + 1;
  const parts = BUCKETS.filter(([k]) => counts[k] && k !== "gone" && k !== "earlier").map(([k, label]) =>
    h("button", {
      class: "sum sum-" + k, title: "Show " + label,
      onclick: () => {
        navigate({ view: "tasks", key: null, sid: null });
        const g = document.getElementById("group-" + k);
        if (g) g.scrollIntoView({ behavior: "smooth", block: "start" });
      },
    }, h("span", { class: "dot" }), `${counts[k]} ${label.toLowerCase()}`));
  replaceChildren($("#summary"), parts);
}

function renderVenueChips() {
  const present = new Set(["all"]);
  const repos = new Set();
  for (const t of state.tasks) {
    for (const r of t.repos || [t.repo]) if (r) repos.add(r);
    if (!t.live) continue;
    if (!t.venues.length) present.add("local");
    for (const v of t.venues) present.add(v);
  }
  if (state.route.venue !== "all") present.add(state.route.venue);
  const order = ["all", "codespace", "container", "ssh", "local"];
  replaceChildren($("#venues"), order.filter((v) => present.has(v)).map((v) =>
    h("button", {
      class: "fchip" + (state.route.venue === v ? " on" : ""), "aria-pressed": state.route.venue === v ? "true" : "false",
      onclick: () => navigate({ venue: v }, { replace: true }),
    }, v === "all" ? "All" : VENUE_LABEL[v] || v)));
  const repoChips = repos.size > 1 ? [h("span", { class: "fsep" }), ["all", ...[...repos].sort()].map((r) =>
    h("button", {
      class: "fchip" + (state.route.repo === r ? " on" : ""), "aria-pressed": state.route.repo === r ? "true" : "false",
      onclick: () => navigate({ repo: r }, { replace: true }),
    }, r === "all" ? "All repos" : r))] : [];
  replaceChildren($("#repos"), repoChips);
}

// Board cards are keyed and only rebuilt when their visible content changes,
// so polling never flickers, loses scroll, or steals focus.
const cards = new Map();  // key -> {el, sig}
const groups = new Map(); // bucket -> {el, list, count}

function groupEl(bucket) {
  if (!groups.has(bucket)) {
    const count = h("span", { class: "g-count" });
    const list = h("div", { class: "g-list" });
    const more = h("button", { class: "ghost g-more", hidden: true,
      onclick: () => { state.showAllEarlier = !state.showAllEarlier; render(); } });
    const el = h("section", { class: "group g-" + bucket, id: "group-" + bucket },
      h("h2", null, h("span", { class: "dot" }), BUCKET_LABEL[bucket], count), list, more);
    groups.set(bucket, { el, list, count, more });
  }
  return groups.get(bucket);
}

function cardSig(t, selected) {
  return JSON.stringify([t.title, t.bucket, t.progress && [t.progress.summary, t.progress.blocker, t.progress.phase],
    t.progress && t.progress.stale, t.worktree && [t.worktree.follow_up, t.worktree.summary], t.pr, t.venues,
    t.quiet, t.repos, t.sessions.length, ago(t.updated), selected, t.worktree && t.worktree.status,
    t.fullTitle, t.codespaces]);
}

function renderCard(t, selected) {
  const p = t.progress || {};
  const venue = !t.live ? [] : t.venues.length ? t.venues.map((v) => VENUE_LABEL[v] || v) : ["Local"];
  const wt = t.worktree || {};
  const ask = t.live && wt.follow_up && wt.summary ? wt.summary : "";
  const blurb = t.bucket === "starting" ? "Starting a Copilot session…"
    : p.stale ? `Last milestone ${ago(p.ts)}: ${p.blocker || p.summary || p.phase || ""}`
    : p.summary || (t.live ? "No progress reported yet" : "No summary recorded");
  const targets = (t.repos || []).filter((r) => r !== t.repo);
  const el = h("article", {
    class: "card c-" + t.bucket + (selected ? " selected" : "") + (t.unused ? " unused" : ""), tabindex: "0",
    dataset: { key: t.key }, "aria-label": t.title, role: "button", title: t.fullTitle || t.title,
  },
    h("div", { class: "c-top" },
      t.bucket === "starting" ? h("span", { class: "spin" }) : h("span", { class: "dot", title: BUCKET_LABEL[t.bucket] }),
      h("h3", { text: t.title }),
      h("time", { class: "muted", text: ago(t.updated) })),
    ask ? h("p", { class: "c-blocker", title: ask, text: "Waiting on you: " + ask })
      : p.blocker && !p.stale ? h("p", { class: "c-blocker", text: p.blocker })
      : h("p", { class: "c-progress" + (p.summary && t.bucket !== "starting" && !p.stale ? "" : " muted"), text: blurb }),
    h("div", { class: "c-meta" },
      venue.map((v) => h("span", { class: "chip", text: v })),
      targets.map((r) => h("span", { class: "chip target", title: "The worker's repo", text: "→ " + r })),
      (t.codespaces || []).slice(0, 1).map((cs) => h("span", { class: "chip", title: cs, text: "CodeSpace" })),
      t.bucket === "monitoring" ? h("span", { class: "chip mon", title: "The worker is waiting on its PR's builds",
                                                   text: "Monitoring PR" }) : null,
      p.phase && !p.stale && t.bucket !== "monitoring" ? h("span", { class: "chip", text: p.phase }) : null,
      wt.status && wt.status !== "active" ? h("span", { class: "chip", text: wt.status }) : null,
      !t.live && wt.follow_up ? h("span", { class: "chip warn", title: "Marked as having follow-ups", text: "follow-up" }) : null,
      t.pr ? prChip(t.pr) : null,
      t.quiet ? h("span", { class: "chip warn", title: "Mid-turn with no events for a while", text: "quiet" }) : null,
      h("span", { class: "grow" }),
      h("span", { class: "muted small", text: t.sessions.length > 1 ? t.sessions.length + " sessions" : t.repo })));
  return el;
}

function prChip(pr, link = false) {
  const text = "PR " + pr.number + (pr.state ? " · " + pr.state : pr.build ? " · " + pr.build : "") + (link ? " ↗" : "");
  const cls = "chip pr" + (pr.state ? " pr-" + pr.state : "");
  const tip = pr.title || "";
  return link && pr.url
    ? h("a", { class: cls, href: pr.url, target: "_blank", rel: "noopener noreferrer", text, title: tip })
    : h("span", { class: cls, text, title: tip });
}

function renderBoard() {
  const board = $("#board");
  const shown = filterTasks(state.tasks, state.route);
  const selected = state.route.key;
  board.classList.toggle("compact", !!selected);
  $("#layout").classList.toggle("split", !!selected);
  if (!state.loaded) {
    if (!board.querySelector(".skeleton")) {
      replaceChildren(board, h("div", { class: "skeleton" }, [1, 2, 3].map(() => h("div", { class: "card sk" }))));
    }
    return;
  }
  const sk = board.querySelector(".skeleton");
  if (sk) sk.remove();
  const byBucket = new Map();
  for (const t of shown) {
    if (!byBucket.has(t.bucket)) byBucket.set(t.bucket, []);
    byBucket.get(t.bucket).push(t);
  }
  const seen = new Set();
  for (const [bucket] of BUCKETS) {
    const g = groupEl(bucket);
    const all = byBucket.get(bucket) || [];
    if (!all.length) { g.el.remove(); continue; }
    g.count.textContent = String(all.length);
    const capped = bucket === "earlier" && !state.showAllEarlier && !state.route.q && all.length > EARLIER_SHOWN;
    const list = capped ? all.slice(0, EARLIER_SHOWN) : all;
    g.more.hidden = !(bucket === "earlier" && all.length > EARLIER_SHOWN && !state.route.q);
    g.more.textContent = state.showAllEarlier ? "Show fewer" : `Show all ${all.length}`;
    board.appendChild(g.el);
    list.forEach((t, i) => {
      seen.add(t.key);
      const sig = cardSig(t, t.key === selected);
      let c = cards.get(t.key);
      if (!c || c.sig !== sig) {
        const el = renderCard(t, t.key === selected);
        if (c) {
          const hadFocus = document.activeElement === c.el;
          c.el.replaceWith(el);
          if (hadFocus) el.focus();
        }
        c = { el, sig };
        cards.set(t.key, c);
      }
      if (g.list.children[i] !== c.el) g.list.insertBefore(c.el, g.list.children[i] || null);
    });
  }
  for (const [key, c] of cards) if (!seen.has(key)) { c.el.remove(); cards.delete(key); }
  for (const g of groups.values()) {
    while (g.list.children.length && !seen.has(g.list.lastChild.dataset.key)) g.list.lastChild.remove();
  }
  let empty = board.querySelector(".empty");
  if (!shown.length) {
    if (!empty) {
      empty = h("div", { class: "empty" });
      board.appendChild(empty);
    }
    replaceChildren(empty, state.tasks.length
      ? [h("p", { text: "No tasks match this filter." }),
         h("button", { class: "ghost", onclick: () => navigate({ q: "", venue: "all", repo: "all" }, { replace: true }) }, "Clear filter")]
      : [h("h3", { text: "No tasks yet" }),
         h("p", { class: "muted", text: "Start one here, or from the Picker. A task is a worktree with its Copilot " +
           "session, plus any workers it runs in CodeSpaces, containers, or SSH hosts." }),
         h("button", { class: "primary", onclick: () => openNewTask() }, "New task")]);
  } else if (empty) empty.remove();
}

// -- task detail --------------------------------------------------------------

const viewer = new SessionViewer({ api, request });
let detailKey = null;
let detailSig = "";
const live = { slots: null, sigs: {} };  // the live-task pane's in-place parts

function renderDetail() {
  const pane = $("#detail");
  const key = state.route.key;
  if (!key) {
    pane.hidden = true;
    viewer.close();
    detailKey = null;
    live.slots = null;
    return;
  }
  pane.hidden = false;
  const task = state.tasks.find((t) => t.key === key);
  if (!task) {
    if (!state.loaded || !state.wsLoaded) {
      if (detailKey !== key) live.slots = null;
      if (detailKey !== key) replaceChildren(pane, h("div", { class: "d-missing muted" }, h("span", { class: "spin" }), " Loading…"));
      detailKey = key;
      return;
    }
    viewer.close();
    detailKey = key;
    detailSig = "";
    live.slots = null;
    replaceChildren(pane, h("div", { class: "d-missing" },
      h("h2", { text: "Task not found" }),
      h("p", { class: "muted", text: "Its worktree may have been finalized and cleaned up." }),
      h("button", { onclick: () => navigate({ key: null, sid: null }) }, "Back to tasks")));
    return;
  }
  if (!task.sessions.length) {
    viewer.close();
    const sig = JSON.stringify([task.key, task.title, task.bucket, task.progress, task.pr, task.worktree]);
    if (detailKey !== key || sig !== detailSig) {
      detailKey = key;
      detailSig = sig;
      live.slots = null;
      replaceChildren(pane, detailHead(task), task.pending ? detailStarting(task) : detailEarlier(task));
    }
    return;
  }
  const entry = task.sessions.find((x) => x.s.session_id === state.route.sid) || task.sessions[0];
  // The viewer element stays attached for as long as a live task is open --
  // re-inserting it would reset its scroll position and steal the composer's
  // focus. Only the parts around it are rebuilt, and only when they change.
  if (!live.slots || live.slots.pane !== pane || !pane.contains(viewer.el)) {
    live.slots = {
      pane, head: h("div", { class: "d-slot-head" }), progress: h("div", { class: "d-slot-progress" }),
      tabs: h("div"), info: h("div", { class: "d-slot-info" }),
    };
    replaceChildren(pane, live.slots.head, live.slots.progress,
      h("div", { class: "d-main" }, live.slots.tabs, viewer.el), live.slots.info);
    live.sigs = {};
  }
  detailKey = key;
  detailSig = "";
  const parts = {
    head: [() => detailHead(task), [task.key, task.title, task.bucket, task.pr, task.repo, task.machine, ago(task.updated)]],
    progress: [() => detailProgress(task), [task.bucket, task.worktree && [task.worktree.follow_up,
      task.worktree.summary, task.worktree.status_note_at], task.progress && [task.progress.summary, task.progress.blocker,
      task.progress.phase, task.progress.stale, ago(task.progress.ts || 0)]]],
    tabs: [() => detailTabs(task, entry), [entry.s.session_id,
      task.sessions.map((x) => [x.s.session_id, x.role, sessionBucket(x.s)])]],
    info: [() => detailInfo(task, entry), [entry.s.session_id, entry.s.status, entry.s.liveness, entry.s.turn_state]],
  };
  for (const [name, [build, sigParts]] of Object.entries(parts)) {
    const sig = JSON.stringify(sigParts);
    if (live.sigs[name] === sig) continue;
    live.sigs[name] = sig;
    const slot = live.slots[name];
    const prev = slot.querySelector("details");
    const wasOpen = prev ? prev.open : window.matchMedia("(min-width: 1700px)").matches;
    replaceChildren(slot, build());
    if (wasOpen) { const d = slot.querySelector("details"); if (d) d.open = true; }
  }
  viewer.open(entry.s);
}

function detailHead(task) {
  const titleEl = h("h2", { text: task.title, title: task.fullTitle || task.title });
  const editable = !!task.worktree;
  const edit = () => {
    const input = h("input", { class: "title-edit", type: "text", maxlength: "30", value: task.title,
                               "aria-label": "Task title" });
    const hint = h("span", { class: "muted small", text: "Enter to save · Esc to cancel · 30 characters (the Picker's limit)" });
    const wrap = h("div", { class: "title-edit-wrap" }, input, hint);
    const done = () => { wrap.replaceWith(titleEl); if (editBtn) editBtn.hidden = false; };
    input.addEventListener("keydown", async (e) => {
      if (e.key === "Escape") { e.stopPropagation(); done(); return; }
      if (e.key !== "Enter") return;
      const title = input.value.trim();
      if (!title || title === task.title) { done(); return; }
      input.disabled = true;
      hint.textContent = "Saving…";
      try {
        const r = await request(`/api/v1/ui/tasks/${encodeURIComponent(task.key)}/title`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || r.status);
        const row = state.workspaces.find((w) => w.id === task.key);
        if (row) row.title = d.title;
        titleEl.textContent = d.title;
        done();
        render();
      } catch (err) {
        input.disabled = false;
        hint.textContent = "Couldn\u2019t save: " + err.message;
      }
    });
    titleEl.replaceWith(wrap);
    if (editBtn) editBtn.hidden = true;
    input.focus();
    input.select();
  };
  const editBtn = editable ? h("button", { class: "ghost icon small edit", title: "Rename this task", "aria-label": "Rename",
                                           onclick: edit }, "✎") : null;
  if (editable) titleEl.addEventListener("dblclick", edit);
  return h("header", { class: "d-head" },
    h("button", { class: "ghost icon", title: "Back to tasks (Esc)", "aria-label": "Back",
                  onclick: () => navigate({ key: null, sid: null }) }, "←"),
    h("div", { class: "d-title" },
      h("div", { class: "d-title-row" }, titleEl, editBtn),
      task.fullTitle ? h("p", { class: "d-full muted", text: task.fullTitle }) : null,
      h("div", { class: "d-sub muted small" },
        h("span", { class: "chip c-" + task.bucket }, h("span", { class: "dot" }), BUCKET_LABEL[task.bucket]),
        task.pr ? prChip(task.pr, true) : null,
        task.pr && task.pr.build ? h("span", { class: "chip", text: "build " + task.pr.build }) : null,
        (task.repos || []).filter((r) => r !== task.repo).map((r) => h("span", { class: "chip target", text: "→ " + r })),
        h("span", { text: [task.repo, task.machine].filter(Boolean).join(" · ") }),
        h("span", { text: "updated " + ago(task.updated) }))));
}
function detailAsk(task) {
  const w = task.worktree || {};
  if (!task.live || !w.follow_up || !w.summary) return null;
  return h("div", { class: "d-progress blocked ask" },
    h("p", null, h("strong", { text: "Waiting on you" })),
    h("p", { text: w.summary }),
    w.status_note_at ? h("p", { class: "muted small", text: "noted " + ago(parseStamp(w.status_note_at)) +
      " by the task's session (the same note the Picker shows)" }) : null);
}

function detailProgress(task) {
  const ask = detailAsk(task);
  const p = task.progress;
  if (!p) return ask;
  const milestone = detailMilestone(task, p);
  return ask ? h("div", null, ask, milestone) : milestone;
}

function detailMilestone(task, p) {
  if (p.stale) {
    return h("div", { class: "d-progress stale" },
      h("p", { class: "muted small", text: `Last milestone, reported ${ago(p.ts)} — the session has kept working since:` }),
      p.blocker ? h("p", { class: "muted", text: p.blocker }) : null,
      p.summary ? h("p", { class: "mono small muted", text: p.summary }) : null);
  }
  const monitoring = task.bucket === "monitoring";
  return h("div", { class: "d-progress" + (p.blocker && !monitoring ? " blocked" : monitoring ? " monitoring" : "") },
    monitoring ? h("p", null, h("strong", { text: "Monitoring its PR. " }),
      "The PR's builds are still running; nothing needs you yet.") : null,
    p.blocker ? h("p", null, h("strong", { text: monitoring ? "Latest note: " : "Blocked: " }), p.blocker) : null,
    p.summary ? h("p", { class: "mono small", text: p.summary }) : null,
    h("p", { class: "muted small", text: [p.phase && "phase " + p.phase, p.ts && "reported " + ago(p.ts)]
      .filter(Boolean).join(" · ") }));
}
function detailTabs(task, entry) {
  return h("nav", { class: "d-tabs", "aria-label": "Sessions in this task" }, task.sessions.map((x) => {
    const s = x.s;
    const where = s.venue ? (VENUE_LABEL[s.venue.kind] || s.venue.kind) : "local";
    const on = x === entry;
    return h("button", {
      class: "d-tab b-" + sessionBucket(s) + (on ? " on" : ""), "aria-current": on ? "true" : null,
      title: s.session_id, onclick: () => navigate({ sid: s.session_id }, { replace: true }),
    }, h("span", { class: "dot" }), h("span", { text: ROLE_LABEL[x.role] || x.role }),
    h("span", { class: "muted small", text: where + " · " + String(s.session_id).slice(0, 8) }));
  }));
}

function detailStarting(task) {
  return h("div", { class: "d-body starting" },
    h("div", { class: "d-progress" },
      h("p", null, h("span", { class: "spin" }), h("strong", { text: "  Starting a Copilot session" })),
      h("p", { class: "muted small", text: "The worktree is ready; Copilot is loading its plugins. " +
        "The session appears here as soon as it registers — usually within a minute." })),
    h("h3", { class: "d-h", text: "Prompt" }),
    h("pre", { class: "d-prompt", text: task.pending.prompt || "" }));
}

function detailEarlier(task) {
  const w = task.worktree || {};
  const active = w.status === "active";
  const box = h("textarea", { rows: "3", placeholder: "Optional: what should the new session do first?",
                              "aria-label": "First message" });
  const out = h("span", { class: "muted small", role: "status" });
  const btn = h("button", { class: "primary" }, "Resume session");
  const go = async () => {
    btn.disabled = true;
    out.textContent = "Starting a session in this worktree…";
    try {
      const r = await request(`/api/v1/ui/tasks/${encodeURIComponent(task.key)}/resume`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: box.value.trim() }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || r.status);
      state.pending[task.key] = { title: task.title, project: task.repo, prompt: box.value.trim() ||
        "(resumed without a new prompt)", at: Date.now() / 1000 };
      savePending();
      render();
      refresh();
    } catch (e) {
      out.textContent = "Couldn\u2019t resume: " + e.message;
      btn.disabled = false;
    }
  };
  btn.addEventListener("click", go);
  box.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) go(); });
  return h("div", { class: "d-body" },
    w.summary ? h("div", { class: "d-progress" }, h("p", { text: w.summary }),
      w.status_note_at ? h("p", { class: "muted small", text: "noted " + ago(parseStamp(w.status_note_at)) }) : null) : null,
    active ? h("section", { class: "d-resume" },
      h("h3", { class: "d-h", text: "Pick this task back up" }),
      h("p", { class: "muted small", text: "Starts a new Copilot session in this worktree. It runs in the " +
        "background; watch it here, or attach from the Picker." }),
      box, h("div", { class: "row" }, btn, out))
      : h("p", { class: "muted", text: `This worktree is ${w.status}; it can\u2019t be resumed.` }),
    h("h3", { class: "d-h", text: "Details" }),
    h("table", { class: "facts" }, h("tbody", null,
      row("Worktree", task.key, true),
      row("Repo", task.repo),
      row("Branch", w.branch, true),
      row("Directory", w.path, true),
      row("Status", [w.status, w.follow_up ? "has follow-ups" : ""].filter(Boolean).join(" · ")),
      row("Started", w.started_at ? new Date(parseStamp(w.started_at) * 1000).toLocaleString() : ""),
      row("Last resumed", w.last_resumed_at ? new Date(parseStamp(w.last_resumed_at) * 1000).toLocaleString() : ""),
      row("Activity", [w.session_count && w.session_count + " sessions", w.turn_count && w.turn_count + " turns"]
        .filter(Boolean).join(" · ")),
      row("Codename", w.codename),
      (task.paired || []).map((p) => row("Paired " + (p.pair_role || "worktree"), p.path, true)))));
}

function row(label, value, copy) {
  if (!value) return null;
  return h("tr", null, h("th", { text: label }),
    h("td", null, h("code", { text: value }),
      copy ? h("button", { class: "ghost small", onclick: (e) => copyText(e.currentTarget, value) }, "Copy") : null));
}

function detailInfo(task, entry) {
  const s = entry.s;
  const v = s.venue || {};
  const dead = s.status === "expired" || s.status === "wedged";
  return h("details", { class: "d-info" },
    h("summary", null, "Session details"),
    h("table", null, h("tbody", null,
      row("Session", s.session_id, true),
      row("Task", task.key, true),
      row("Worktree", s.worktree_id),
      row("Machine", s.machine),
      row("Venue", v.kind ? `${v.kind}: ${v.target}` : ""),
      row("Supervisor", v.supervisor_ref),
      (task.paired || []).map((p) => row("Paired " + (p.pair_role || "worktree"), p.path, true)),
      row("Repo / branch", [s.repo, s.branch].filter(Boolean).join(" @ ")),
      row("Directory", s.cwd),
      row("State", [s.status, s.liveness, s.turn_state, s.cli_mode ? "cli-mode" : ""].filter(Boolean).join(" · ")),
      row("Driven by", s.driven_by),
      row("Message from a terminal", `agent-bridge send ${s.session_id} "…" --no-wait --steer`, true))),
    dead ? h("button", { class: "danger", onclick: (e) => removeLive(e.currentTarget, s.session_id) },
      "Remove this ended session") : null);
}

async function removeLive(btn, id) {
  btn.disabled = true;
  try {
    const r = await request(`/api/v1/live-sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
    btn.textContent = r.ok ? "Removed" : "Remove failed: " + r.status;
    refresh();
  } catch (e) { btn.textContent = "Remove failed"; }
}

// -- sessions table -------------------------------------------------------------

function renderSessions() {
  const root = $("#sessions-view");
  const q = state.route.q.trim().toLowerCase();
  const rows = [];
  for (const t of state.tasks) {
    for (const x of t.sessions) {
      const s = x.s;
      const hay = [s.session_id, t.title, s.worktree_id, s.machine, s.venue && s.venue.target, s.branch]
        .filter(Boolean).join(" ").toLowerCase();
      if (q && !q.split(/\s+/).every((w) => hay.includes(w))) continue;
      const p = s.latest_progress || {};
      rows.push(h("tr", {
        class: "clickable", tabindex: "0",
        onclick: () => navigate({ view: "tasks", key: t.key, sid: s.session_id }),
        onkeydown: (e) => { if (e.key === "Enter") navigate({ view: "tasks", key: t.key, sid: s.session_id }); },
      },
        h("td", null, h("code", { text: String(s.session_id).slice(0, 8), title: s.session_id })),
        h("td", { text: ROLE_LABEL[x.role] || x.role }),
        h("td", { text: t.title }),
        h("td", { text: s.venue ? `${VENUE_LABEL[s.venue.kind] || s.venue.kind} ${s.venue.target}` : s.machine || "" }),
        h("td", null, h("span", { class: "chip c-" + sessionBucket(s) }, h("span", { class: "dot" }),
          [s.liveness, s.turn_state].filter(Boolean).join(" / ") || s.status)),
        h("td", { class: "clip", text: p.blocker || p.summary || "" }),
        h("td", { class: "muted", text: ago(s.updated_at) })));
    }
  }
  replaceChildren(root, h("table", { class: "grid" },
    h("thead", null, h("tr", null, ["Session", "Role", "Task", "Where", "State", "Progress", "Updated"]
      .map((c) => h("th", { text: c })))),
    h("tbody", null, rows.length ? rows : h("tr", null, h("td", { colspan: "7", class: "muted", text: "No live sessions." })))));
}

// -- ACP -------------------------------------------------------------------------

const wsBase = (location.protocol === "https:" ? "wss://" : "ws://") + location.host;

async function loadAcp() {
  const root = $("#acp-view");
  const agentsBox = h("div", null, h("p", { class: "muted", text: "Loading agents… (this can take a few seconds)" }));
  const sessionsBox = h("div", null, h("p", { class: "muted", text: "Loading sessions…" }));
  replaceChildren(root,
    h("p", { class: "muted" }, "Connect an external ACP client (for example ",
      h("a", { href: "https://acp-ui.github.io/", target: "_blank", rel: "noopener noreferrer" }, "acp-ui"),
      ") to a URL below with transport ", h("b", null, "websocket"), " and an ",
      h("code", null, "Authorization: Bearer <token>"), " header (", h("code", null, "agent-bridge token"), ")."),
    h("h2", { text: "Agents" }), agentsBox, h("h2", { text: "ACP sessions" }), sessionsBox);
  api("/api/v1/sessions").then((d) => replaceChildren(sessionsBox, acpSessions(d.sessions || [])))
    .catch((e) => replaceChildren(sessionsBox, h("p", { class: "bad", text: "Couldn't load sessions: " + e.message })));
  api("/api/v1/agents").then((d) => replaceChildren(agentsBox, acpAgents(d.agents || [])))
    .catch((e) => replaceChildren(agentsBox, h("p", { class: "bad", text: "Couldn't load agents: " + e.message })));
}

function urlCell(url) {
  return h("td", { class: "nowrap" }, h("code", { text: url }),
    h("button", { class: "ghost small", onclick: (e) => copyText(e.currentTarget, url) }, "Copy"));
}

function acpAgents(list) {
  if (!list.length) return h("p", { class: "muted", text: "No agents registered." });
  return h("table", { class: "grid" },
    h("thead", null, h("tr", null, ["Agent", "Description", "Target", "ACP WebSocket URL"].map((c) => h("th", { text: c })))),
    h("tbody", null, list.map((a) => {
      const name = a.name || a.display_name || "";
      return h("tr", null,
        h("td", null, h("b", { text: a.display_name || name }), h("div", { class: "muted small", text: name })),
        h("td", { text: a.description || "" }),
        h("td", { text: a.host ? "ssh:" + a.host : (a.target_type || "local") }),
        urlCell(wsBase + "/acp/" + encodeURIComponent(name)));
    })));
}

function acpSessions(list) {
  if (!list.length) return h("p", { class: "muted", text: "No ACP sessions." });
  return h("table", { class: "grid" },
    h("thead", null, h("tr", null, ["Session", "Agent", "Status", "Turns", "Context", "Adopt URL", ""]
      .map((c) => h("th", { text: c })))),
    h("tbody", null, list.map((s) => {
      const out = h("span", { class: "muted small" });
      const input = h("input", { type: "text", placeholder: "Queue a follow-up turn", "aria-label": "Follow-up turn" });
      const send = async () => {
        const prompt = input.value.trim();
        if (!prompt) return;
        try {
          const r = await request(`/api/v1/sessions/${encodeURIComponent(s.session_id)}/turns`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt, queue: true }),
          });
          const text = await r.text();
          if (!r.ok) throw new Error(r.status + " " + text.slice(0, 120));
          input.value = "";
          out.textContent = JSON.parse(text).queued ? "queued" : "sent";
        } catch (e) { out.textContent = "failed: " + e.message; }
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
      return h("tr", null,
        h("td", null, h("code", { text: s.session_id }), h("div", { class: "muted small", text: s.name || "" })),
        h("td", { text: s.agent_name || "" }),
        h("td", { text: s.status || "" }),
        h("td", { text: s.turn_count != null ? String(s.turn_count) : "" }),
        h("td", { text: s.context_pct != null ? s.context_pct.toFixed(0) + "%" : "" }),
        urlCell(wsBase + "/acp/session/" + encodeURIComponent(s.session_id)),
        h("td", { class: "nowrap" }, input, h("button", { class: "small", onclick: send }, "Queue"), out));
    })));
}

// -- new task --------------------------------------------------------------------

function defaultProject() {
  const saved = localStorage.getItem(PROJECT_KEY);
  if (saved && state.projects.includes(saved)) return saved;
  const recent = state.tasks.filter((t) => state.projects.includes(t.repo)).sort((a, b) => b.updated - a.updated)[0];
  return (recent && recent.repo) || state.projects[0] || "";
}

function fillProjects() {
  const sel = $("#nt-project");
  const cur = sel.value || defaultProject();
  replaceChildren(sel, state.projects.length ? state.projects.map((p) => h("option", { value: p }, p))
    : [h("option", { value: "" }, state.wsLoaded ? "No repos registered" : "Loading repos…")]);
  sel.value = state.projects.includes(cur) ? cur : defaultProject();
  sel.disabled = !state.projects.length;
}

function openNewTask() {
  if (!state.wsLoaded) refreshWorkspaces();
  fillProjects();
  $("#nt-status").textContent = "";
  $("#nt-start").disabled = false;
  $("#newtask").showModal();
  $("#nt-prompt").focus();
}

async function startTask() {
  const project = $("#nt-project").value;
  const prompt = $("#nt-prompt").value.trim();
  const title = $("#nt-title").value.trim();
  const status = $("#nt-status");
  if (!project) { status.textContent = "Choose a repo."; return; }
  if (!prompt) { status.textContent = "Describe the task first."; $("#nt-prompt").focus(); return; }
  $("#nt-start").disabled = true;
  status.textContent = "Creating a worktree and starting Copilot — this takes about 20 seconds…";
  try {
    const r = await request("/api/v1/ui/tasks", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, prompt, title }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status} ${r.statusText}`);
    localStorage.setItem(PROJECT_KEY, project);
    state.pending[d.worktree_id] = { title, project, prompt, at: Date.now() / 1000 };
    savePending();
    $("#nt-prompt").value = "";
    $("#nt-title").value = "";
    $("#newtask").close();
    navigate({ view: "tasks", key: d.worktree_id, sid: null });
    refresh();
  } catch (e) {
    status.textContent = "Couldn\u2019t start the task: " + e.message;
    $("#nt-start").disabled = false;
  }
}

// -- theme, keyboard, wiring ------------------------------------------------------------

function applyTheme(mode) {
  if (mode) document.documentElement.dataset.theme = mode; else delete document.documentElement.dataset.theme;
  $("#theme").textContent = mode === "dark" ? "Dark" : mode === "light" ? "Light" : "Auto";
}

function visibleCards() {
  return [...document.querySelectorAll("#board .card:not(.sk)")];
}

function onKey(e) {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName);
  if (e.key === "Escape") {
    if (typing) { document.activeElement.blur(); return; }
    if (state.route.key) navigate({ key: null, sid: null });
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); $("#q").focus(); return; }
  if (e.key === "n") { e.preventDefault(); openNewTask(); return; }
  if (state.route.view !== "tasks") return;
  const list = visibleCards();
  if (!list.length) return;
  const at = list.indexOf(document.activeElement);
  if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); list[Math.min(list.length - 1, at + 1)].focus(); }
  else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); list[Math.max(0, at - 1)].focus(); }
}

function wire() {
  $("#board").addEventListener("click", (e) => {
    const card = e.target.closest(".card[data-key]");
    if (card) navigate({ key: card.dataset.key, sid: null });
  });
  $("#board").addEventListener("keydown", (e) => {
    const card = e.target.closest(".card[data-key]");
    if (card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); navigate({ key: card.dataset.key, sid: null }); }
  });
  $("#q").addEventListener("input", (e) => navigate({ q: e.target.value }, { replace: true }));
  $("#theme").addEventListener("click", () => {
    const cur = localStorage.getItem(THEME_KEY) || "";
    const next = cur === "" ? "dark" : cur === "dark" ? "light" : "";
    if (next) localStorage.setItem(THEME_KEY, next); else localStorage.removeItem(THEME_KEY);
    applyTheme(next);
  });
  $("#signout").addEventListener("click", () => signOut("Signed out."));
  $("#new").addEventListener("click", openNewTask);
  $("#nt-cancel").addEventListener("click", () => $("#newtask").close());
  $("#nt-start").addEventListener("click", startTask);
  $("#nt-prompt").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); startTask(); }
  });
  $("#signin-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const t = $("#signin-token").value.trim();
    if (!t) return;
    setToken(t);
    $("#signin-token").value = "";
    start();
  });
  window.addEventListener("popstate", onRoute);
  window.addEventListener("hashchange", onRoute);
  document.addEventListener("keydown", onKey);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); schedulePoll(); });
}

async function start() {
  if (!token) { if ($("#signin").hidden) showSignIn(""); return; }
  showApp();
  onRoute();
  refreshWorkspaces().then(() => { if ($("#newtask").open) fillProjects(); scheduleWorkspaces(); });
  await refresh();
  schedulePoll();
}

applyTheme(localStorage.getItem(THEME_KEY) || "");
wire();
adoptLoginCode().then(() => {
  if (token) paintFromCache();
  start();
});
