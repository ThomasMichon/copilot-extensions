// Pure data model for the bridge UI: no DOM access, so it runs (and is tested)
// under node as well as in the page.

// -- time ---------------------------------------------------------------------

export function ago(ts, now = Date.now() / 1000) {
  if (!ts) return "";
  const s = Math.max(0, Math.round(now - ts));
  if (s < 45) return "just now";
  if (s < 90) return "1m ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400 * 2) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

export function duration(seconds) {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return "";
  const s = Math.round(seconds);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s";
  return Math.floor(s / 3600) + "h " + String(Math.floor((s % 3600) / 60)).padStart(2, "0") + "m";
}

// -- SSE ------------------------------------------------------------------------

/** Parse one SSE block into {id, type, data, ts}; data is null for comments/empty frames. */
export function parseSseBlock(block) {
  let id = null, type = "message", raw = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("id:")) id = parseInt(line.slice(3).trim(), 10);
    else if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) raw += line.slice(5).trim();
  }
  if (Number.isNaN(id)) id = null;
  if (!raw) return { id, type, data: null, ts: null };
  let frame;
  try { frame = JSON.parse(raw); } catch (e) { return { id, type, data: null, ts: null }; }
  const data = frame && typeof frame.data === "object" && frame.data ? frame.data : (frame || {});
  const ts = frame && typeof frame.timestamp === "number" ? frame.timestamp : null;
  return { id, type, data, ts };
}

// -- tool summaries -----------------------------------------------------------------

const VERBS = {
  bash: ["Ran", "command", "commands"], powershell: ["Ran", "command", "commands"],
  shell: ["Ran", "command", "commands"],
  read_bash: ["Checked", "shell", "shells"], read_powershell: ["Checked", "shell", "shells"],
  write_bash: ["Typed into", "shell", "shells"], write_powershell: ["Typed into", "shell", "shells"],
  stop_bash: ["Stopped", "shell", "shells"], stop_powershell: ["Stopped", "shell", "shells"],
  view: ["Read", "file", "files"], read: ["Read", "file", "files"], read_file: ["Read", "file", "files"],
  create: ["Created", "file", "files"], edit: ["Edited", "file", "files"],
  str_replace_editor: ["Edited", "file", "files"], apply_patch: ["Patched", "file", "files"],
  grep: ["Searched", "time", "times"], rg: ["Searched", "time", "times"],
  glob: ["Listed", "pattern", "patterns"],
  web_fetch: ["Fetched", "page", "pages"], web_search: ["Searched the web", "time", "times"],
  task: ["Delegated", "task", "tasks"], read_agent: ["Checked", "agent", "agents"],
  skill: ["Loaded", "skill", "skills"], sql: ["Queried", "table", "tables"],
};

function basename(p) {
  const s = String(p || "").replace(/[\\/]+$/, "");
  const i = Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\"));
  return i >= 0 ? s.slice(i + 1) : s;
}

function firstLine(s, n = 90) {
  const line = String(s || "").split("\n").find((l) => l.trim()) || "";
  return line.length > n ? line.slice(0, n - 1) + "…" : line;
}

function firstString(obj) {
  if (!obj || typeof obj !== "object") return typeof obj === "string" ? obj : "";
  for (const v of Object.values(obj)) if (typeof v === "string" && v.trim()) return v;
  return "";
}

/** A one-line, human summary of a tool call: {verb, noun, label}. */
export function summarizeTool(name, input) {
  const kind = String(name || "tool");
  const a = input && typeof input === "object" ? input : {};
  const [verb, noun] = VERBS[kind] || [kind, "call", "calls"];
  let label;
  switch (kind) {
    case "bash": case "powershell": case "shell":
      label = a.description || firstLine(a.command); break;
    case "read_bash": case "read_powershell": case "write_bash": case "write_powershell":
    case "stop_bash": case "stop_powershell":
      label = a.shellId || ""; break;
    case "view": case "read": case "read_file": case "create": case "edit":
    case "str_replace_editor":
      label = basename(a.path || a.file_path); break;
    case "grep": case "rg":
      label = (a.pattern ? `"${firstLine(a.pattern, 60)}"` : "") + (a.glob ? " in " + a.glob : ""); break;
    case "glob": label = a.pattern || ""; break;
    case "web_fetch": label = String(a.url || "").replace(/^https?:\/\//, ""); break;
    case "web_search": label = a.query || ""; break;
    case "task": label = a.description || a.name || a.agent_type || ""; break;
    case "read_agent": label = a.agent_id || ""; break;
    case "skill": label = a.skill || ""; break;
    case "sql": label = a.description || firstLine(a.query); break;
    default: label = firstLine(firstString(a));
  }
  return { verb, noun, label: firstLine(label, 120) };
}

/** ["Read 7 files", "Ran 3 commands"] for a list of work steps, most frequent first. */
export function summarizeSteps(steps) {
  const counts = new Map();
  for (const st of steps) {
    if (st.kind !== "tool") continue;
    const [verb, one, many] = VERBS[st.name] || [st.name, "call", "calls"];
    const key = verb + "|" + one + "|" + many;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((x, y) => y[1] - x[1])
    .map(([key, n]) => {
      const [verb, one, many] = key.split("|");
      return `${verb} ${n} ${n === 1 ? one : many}`;
    });
}

// -- session event model ------------------------------------------------------

const OUTPUT_KEEP = 20000;

function outputText(content) {
  const s = Array.isArray(content) ? content.join("\n") : (content == null ? "" : String(content));
  return s.length > OUTPUT_KEEP ? s.slice(0, OUTPUT_KEEP) + "\n… (clipped)" : s;
}

/**
 * Folds the live event stream into display blocks: user messages, agent
 * messages, and one collapsed "work" block per run of tools/thoughts between
 * them. apply() returns the indices of blocks it changed so a renderer can
 * update only those.
 */
export class SessionModel {
  constructor() {
    this.blocks = [];
    this.toolIndex = new Map();  // tool_call_id -> [blockIndex, step]
    this.usage = { model: null, used: null, size: null };
    this.lastTs = null;
    this.lastId = 0;
    this.events = 0;
  }

  _work(ts) {
    const last = this.blocks[this.blocks.length - 1];
    if (last && last.type === "work" && !last.closed) return this.blocks.length - 1;
    this.blocks.push({ type: "work", steps: [], start: ts, end: ts, closed: false, intent: null });
    return this.blocks.length - 1;
  }

  _push(block) {
    const last = this.blocks[this.blocks.length - 1];
    if (last && last.type === "work") last.closed = true;
    this.blocks.push(block);
    return this.blocks.length - 1;
  }

  apply(type, data, ts, id) {
    const d = data || {};
    if (id != null) this.lastId = Math.max(this.lastId, id);
    if (ts != null) this.lastTs = ts;
    this.events += 1;
    const changed = [];
    switch (type) {
      case "user_message": {
        const text = String(d.content || "");
        // A message delivered through the bridge inbox is represented only by
        // its attribution header; show it as a quiet relay marker.
        const relay = /^Message from .* \(via agent-bridge\)\s*$/.test(text.trim());
        changed.push(this._push({ type: "user", text, relay, ts }));
        break;
      }
      case "local_sent":
        changed.push(this._push({ type: "sent", text: String(d.text || ""), delivery: d.delivery, ts }));
        break;
      case "agent_message":
        changed.push(this._push({ type: "agent", text: String(d.text || ""), ts, agentId: d.agent_id || null }));
        break;
      case "agent_thought": {
        const i = this._work(ts);
        this.blocks[i].steps.push({ kind: "thought", text: String(d.text || ""), ts });
        this.blocks[i].end = ts;
        changed.push(i);
        break;
      }
      case "tool_call_start": {
        const i = this._work(ts);
        const name = d.kind || d.title || "tool";
        const input = d.raw_input;
        if (name === "report_intent") {
          this.blocks[i].intent = String((input && input.intent) || "");
          changed.push(i);
          break;
        }
        const step = {
          kind: "tool", id: d.tool_call_id, name, input, ...summarizeTool(name, input),
          status: "running", start: ts, end: null, output: "", agentId: d.agent_id || null,
        };
        this.blocks[i].steps.push(step);
        this.blocks[i].end = ts;
        if (d.tool_call_id) this.toolIndex.set(d.tool_call_id, [i, step]);
        changed.push(i);
        break;
      }
      case "tool_call_update": {
        const hit = this.toolIndex.get(d.tool_call_id);
        if (!hit) break;
        const [i, step] = hit;
        step.status = d.status === "failed" ? "failed" : "completed";
        step.end = ts;
        step.output = outputText(d.content);
        if (ts != null && (this.blocks[i].end == null || ts > this.blocks[i].end)) this.blocks[i].end = ts;
        changed.push(i);
        break;
      }
      case "ask_user_request":
        changed.push(this._push({ type: "ask", text: String(d.message || ""), ts }));
        break;
      case "compaction_start":
        changed.push(this._push({ type: "note", text: "Compacting the conversation…", ts }));
        break;
      case "compaction_complete": {
        const freed = Number(d.tokens_removed);
        const text = !d.success ? "Context compaction failed"
          : "Context compacted" + (Number.isFinite(freed) && freed > 0
            ? `: ${freed.toLocaleString()} tokens freed` : "");
        const last = this.blocks[this.blocks.length - 1];
        if (last && last.type === "note" && /^Compacting/.test(last.text)) {
          last.text = text;
          changed.push(this.blocks.length - 1);
        } else changed.push(this._push({ type: "note", text, ts }));
        break;
      }
      case "permission_request":
        changed.push(this._push({
          type: "permission", ts,
          text: [d.intention, d.fullCommandText].filter(Boolean).join(" — ") || String(d.kind || ""),
        }));
        break;
      case "turn_complete": {
        const n = this.blocks.length - 1;
        if (n >= 0 && this.blocks[n].type === "work") {
          this.blocks[n].end = ts || this.blocks[n].end;
          changed.push(n);
        }
        break;
      }
      case "usage_update":
        if (d.model) this.usage.model = d.model;
        if (d.context_used != null) this.usage.used = d.context_used;
        if (d.context_size != null) this.usage.size = d.context_size;
        break;
      default:
        break;
    }
    return changed;
  }

  /** The newest tool call still open, if any. */
  runningTool() {
    for (let i = this.blocks.length - 1; i >= 0 && i >= this.blocks.length - 3; i--) {
      const b = this.blocks[i];
      if (b.type !== "work") continue;
      for (let j = b.steps.length - 1; j >= 0; j--) {
        const st = b.steps[j];
        if (st.kind === "tool" && st.status === "running") return st;
      }
    }
    return null;
  }

  contextPct() {
    const { used, size } = this.usage;
    return used != null && size ? Math.round((100 * used) / size) : null;
  }
}

// -- markdown-lite ------------------------------------------------------------

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s<>)\]]+)/g;

/** Inline tokens: text / b / code / link (http(s) only). */
export function parseInline(text) {
  const out = [];
  let last = 0;
  const s = String(text || "");
  for (const m of s.matchAll(INLINE)) {
    if (m.index > last) out.push({ t: "text", text: s.slice(last, m.index) });
    const tok = m[0];
    if (tok.startsWith("**")) out.push({ t: "b", text: tok.slice(2, -2) });
    else if (tok.startsWith("`")) out.push({ t: "code", text: tok.slice(1, -1) });
    else if (tok.startsWith("[")) {
      const k = tok.indexOf("](");
      out.push({ t: "link", text: tok.slice(1, k), href: tok.slice(k + 2, -1) });
    } else {
      const href = tok.replace(/[.,;:]+$/, "");
      out.push({ t: "link", text: href, href });
      if (href.length < tok.length) out.push({ t: "text", text: tok.slice(href.length) });
    }
    last = m.index + tok.length;
  }
  if (last < s.length) out.push({ t: "text", text: s.slice(last) });
  return out;
}

/** Block tokens: p / h / li / code / quote. Never produces markup strings. */
export function parseMarkdown(text) {
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let para = [];
  const flush = () => {
    if (para.length) blocks.push({ t: "p", inl: parseInline(para.join(" ")) });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      const body = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) body.push(lines[i]);
      blocks.push({ t: "code", text: body.join("\n") });
      continue;
    }
    let m;
    if (!line.trim()) { flush(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flush(); blocks.push({ t: "h", inl: parseInline(m[2]) }); continue; }
    if ((m = line.match(/^\s*(?:[-*]|\d+[.)])\s+(.*)$/))) {
      flush();
      blocks.push({ t: "li", inl: parseInline(m[1]), indent: line.match(/^\s*/)[0].length >= 2 });
      continue;
    }
    if ((m = line.match(/^>\s?(.*)$/))) { flush(); blocks.push({ t: "quote", inl: parseInline(m[1]) }); continue; }
    para.push(line.trim());
  }
  flush();
  return blocks;
}

// -- tasks (the board) ------------------------------------------------------------

const DONE_PHASES = new Set(["done", "complete", "completed", "merged", "finished"]);

/** A session reads as working until it has been quiet this long (seconds). A
 *  turn ends after every tool round, so the raw turn state flickers. */
export const WORKING_GRACE = 120;

/** A PR build that hasn't finished: the worker is waiting on CI, not on a person. */
const BUILD_PENDING = /\b(running|queued|pending|progress(ing)?|in[-_ ]?progress|not[-_ ]?started)\b/i;
/** A blocker that asks the operator for something (vs. "builds are still running"). */
const ASKS_OPERATOR = /\b(you|your|operator|user|host must|requeue|re-queue|approv\w*|decid\w*|confirm\w*|permission|credential\w*|auth\w*|log ?in|sign ?in|token|access)\b/i;

/** True when a session's latest milestone says it is waiting on its PR's builds. */
export function monitoringPr(p) {
  if (!p || !p.pr) return false;
  const build = String((p.markers && p.markers["pr-build"]) || "");
  return BUILD_PENDING.test(build) && !(p.blocker && ASKS_OPERATOR.test(p.blocker));
}

export function isWorking(s, now = Date.now() / 1000) {
  return s.turn_state === "running" || s.liveness === "active" || s.liveness === "stalled" ||
    !!(s.last_activity_at && now - s.last_activity_at < WORKING_GRACE);
}

/** A milestone is stale once the session has gone on working well past it: a
 *  "blocked" reported an hour ago doesn't describe a session that is busy now.
 *  An idle session's last milestone is its last word, so it stays current. */
export function milestoneStale(s, now = Date.now() / 1000) {
  const p = s.latest_progress;
  if (!p || !p.ts || !s.last_activity_at) return false;
  return isWorking(s, now) && s.last_activity_at - p.ts > WORKING_GRACE;
}

/** needs | monitoring | working | done | idle | gone for one live session. */
export function sessionBucket(s, now = Date.now() / 1000) {
  const p = s.latest_progress || {};
  if (s.status === "expired") return "gone";
  if (s.status === "wedged") return "needs";
  if (!milestoneStale(s, now)) {
    if (monitoringPr(p)) return "monitoring";
    if (p.phase === "blocked" || p.blocker) return "needs";
    if (DONE_PHASES.has(String(p.phase || "").toLowerCase()) || /^DONE\b/.test(p.summary || "")) return "done";
  }
  return isWorking(s, now) ? "working" : "idle";
}

export const BUCKETS = [
  ["starting", "Starting"], ["needs", "Needs you"], ["working", "Working"], ["monitoring", "Monitoring PR"],
  ["done", "Done"],
  ["idle", "Idle"], ["gone", "Ended"], ["earlier", "Earlier"],
];
const BUCKET_RANK = Object.fromEntries(BUCKETS.map(([k], i) => [k, i]));

/** "ceo-list-sw-blank-x6pr995j4gwc6vxp" -> "ceo list sw blank" (CodeSpace names). */
export function humanizeVenueName(name) {
  const s = String(name || "").replace(/-[a-z0-9]{12,}$/i, "");
  return s.replace(/[-_]+/g, " ").trim();
}

/** "nakanaki-5-win-20260925-114307-f0c7" -> {date, time, short}; null if not a worktree id. */
export function parseWorktreeId(id) {
  const m = String(id || "").match(/-(\d{8})-(\d{6})-([0-9a-f]{4})$/);
  return m ? { date: m[1], time: m[2], short: m[3] } : null;
}

/** "pr/bridge-ui-control-surface" -> "bridge ui control surface"; "" for generated branch names. */
export function humanizeBranch(branch) {
  const s = String(branch || "").replace(/^(refs\/heads\/)?(pr|feature|fix|user\/[^/]+|users\/[^/]+)\//, "");
  if (!s || /^worktree\//.test(s) || /(^|[-/])\d{8}-\d{6}-[0-9a-f]{4}$/.test(s)) return "";
  return s.replace(/[-_/]+/g, " ").trim();
}

function firstSentence(s, n = 70) {
  const t = String(s || "").trim().split(/(?<=[.;])\s|\n/)[0] || "";
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}

/** ISO-ish local timestamp ("2026-09-17 19:38:36" / "...T...") -> epoch seconds, or 0. */
export function parseStamp(v) {
  if (typeof v === "number") return v;
  const t = Date.parse(String(v || "").replace(" ", "T"));
  return Number.isNaN(t) ? 0 : t / 1000;
}

export function prOf(progress) {
  const p = progress || {};
  const raw = p.pr || (p.markers && p.markers.pr) || null;
  if (!raw) return null;
  const s = String(raw);
  const m = s.match(/(\d+)\/?$/);
  return {
    number: m ? m[1] : s, url: /^https:\/\//.test(s) ? s : null,
    build: (p.markers && p.markers["pr-build"]) || null,
  };
}

function prOfWorktree(w) {
  const pr = w && w.pr;
  if (!pr || typeof pr !== "object" || !pr.number) return null;
  return {
    number: String(pr.number), url: /^https:\/\//.test(pr.url || "") ? pr.url : null, build: null,
    // Only a state the server just looked up is shown; the recorded one goes stale.
    state: pr.live ? pr.state || null : null, title: pr.title || null,
  };
}

/** CodeSpace names a task holds, from agent-worktrees' claims summary. */
export function claimedCodespaces(w) {
  const s = String((w && w.claims_summary) || "");
  return [...s.matchAll(/codespace\s+([A-Za-z0-9-]+)/g)].map((m) => m[1]);
}

/** One line saying what an earlier task was, from the best source available. */
export function describeWorktree(w, title) {
  if (!w) return "";
  const pr = prOfWorktree(w);
  const cands = [w.summary, pr && pr.title, w.subject, humanizeBranch(w.branch)];
  const hit = cands.find((x) => x && x !== title);
  if (hit) return hit;
  const cs = claimedCodespaces(w);
  if (cs.length) return "Supervised CodeSpace " + humanizeVenueName(cs[0]);
  if (pr) return "Opened PR " + pr.number;
  if (!w.session_count && !w.turn_count) return "No sessions yet";
  return "";
}

/** A longer line to show under a title: the PR title, or the summary when the title was cut short. */
function fullTitleOf(title, pr, wt) {
  if (pr && pr.title && pr.title !== title) return pr.title;
  if (/…$/.test(title || "") && wt && wt.summary) return firstSentence(wt.summary, 160);
  return "";
}

/** The best human title for a task. */
export function taskTitle(wt, worker, repo, key) {
  const parsed = parseWorktreeId(key);
  const pr = prOfWorktree(wt);
  return (wt && wt.title) ||
    (worker && worker.venue ? humanizeVenueName(worker.venue.target) : "") ||
    (pr && pr.title) || (wt && wt.subject) ||
    (wt && humanizeBranch(wt.branch)) ||
    (wt && firstSentence(wt.summary)) ||
    (repo ? repo + (parsed ? " · " + parsed.short : "") : "") || key;
}

/**
 * Join live sessions and workspaces into tasks. A venue worker attaches to the
 * worktree that supervises it (venue.supervisor_ref = machine/project/worktree_id);
 * every local session on a worktree belongs to that worktree's task; a
 * worktree with no live session is an "earlier" task; a launch still starting
 * shows from `pending` until its session registers.
 */
export function buildTasks(liveSessions, workspaces = [], pending = {}, now = Date.now() / 1000) {
  // A paired sibling (e.g. a knowledge worktree opened alongside a harness one)
  // belongs to its lead worktree's task, not a task of its own.
  const siblings = new Map();
  const leads = new Set((workspaces || []).filter((w) => w && w.pair_id && w.pair_role !== "knowledge")
    .map((w) => w.pair_id));
  const wts = new Map();
  for (const w of workspaces || []) {
    if (!w || !w.id) continue;
    if (w.pair_id && w.pair_role === "knowledge" && leads.has(w.pair_id)) {
      if (!siblings.has(w.pair_id)) siblings.set(w.pair_id, []);
      siblings.get(w.pair_id).push(w);
    } else wts.set(w.id, w);
  }
  const paired = (wt) => (wt && wt.pair_id && siblings.get(wt.pair_id)) || [];
  const tasks = new Map();
  const get = (key) => {
    if (!tasks.has(key)) tasks.set(key, { key, sessions: [] });
    return tasks.get(key);
  };
  for (const s of liveSessions || []) {
    const v = s.venue || null;
    if (v && v.supervisor_ref) {
      get(String(v.supervisor_ref).split("/").pop()).sessions.push({ s, role: "worker" });
    } else if (v) {
      get(`venue:${v.kind}:${v.target}`).sessions.push({ s, role: "worker" });
    } else {
      get(s.worktree_id || `session:${s.session_id}`).sessions.push({ s, role: "local" });
    }
  }
  const out = [];
  for (const t of tasks.values()) {
    const hasWorker = t.sessions.some((x) => x.role === "worker");
    // The newest session with a turn state leads; older ones on the same worktree are "previous".
    const locals = t.sessions.filter((x) => x.role === "local")
      .sort((a, b) => (b.s.turn_state ? 1 : 0) - (a.s.turn_state ? 1 : 0) ||
                      (b.s.updated_at || 0) - (a.s.updated_at || 0));
    locals.forEach((x, i) => { x.role = i === 0 ? (hasWorker ? "orchestrator" : "session") : "previous"; });
    const workers = t.sessions.filter((x) => x.role === "worker")
      .sort((a, b) => (b.s.updated_at || 0) - (a.s.updated_at || 0));
    t.sessions = [...workers, ...locals];

    const wt = wts.get(t.key) || null;
    const primary = t.sessions[0].s;
    const worker = workers[0] ? workers[0].s : null;
    const withProgress = t.sessions.map((x) => x.s).filter((s) => s.latest_progress)
      .sort((a, b) => (b.latest_progress.ts || 0) - (a.latest_progress.ts || 0));
    const source = (worker && worker.latest_progress) ? worker : withProgress[0] || null;
    const progress = source ? { ...source.latest_progress, stale: milestoneStale(source, now) } : null;
    const counted = t.sessions.filter((x) => x.role !== "previous");
    let bucket = counted.map((x) => sessionBucket(x.s, now))
      .sort((a, b) => BUCKET_RANK[a] - BUCKET_RANK[b])[0] || "idle";
    // The task's own session flagged follow-ups for the operator and went quiet.
    if (wt && wt.follow_up && wt.summary && bucket === "idle") bucket = "needs";
    const repo = (wt && (wt.project || wt.repo)) || (locals[0] && locals[0].s.repo) || primary.repo || "";
    const title = taskTitle(wt, worker, repo, t.key);
    const wpr = prOfWorktree(wt);
    out.push({
      key: t.key, title, sessions: t.sessions, bucket, progress,
      pr: prOf(progress) || wpr, worktree: wt, repo,
      repos: [...new Set([repo, ...workers.map((x) => x.s.repo)].filter(Boolean))],
      fullTitle: fullTitleOf(title, wpr, wt), paired: paired(wt),
      machine: (locals[0] && locals[0].s.machine) || primary.machine || "",
      venues: [...new Set(workers.map((x) => x.s.venue.kind))],
      quiet: counted.some((x) => x.s.liveness === "stalled"),
      updated: Math.max(...t.sessions.map((x) => x.s.updated_at || x.s.last_activity_at || 0)),
      // Stable within a group: when the task began, never a heartbeat-driven time.
      since: parseStamp(wt && wt.started_at) ||
        Math.min(...t.sessions.map((x) => x.s.registered_at || x.s.updated_at || 0)),
      live: true,
    });
  }
  for (const [key, p] of Object.entries(pending || {})) {
    if (tasks.has(key)) continue;
    const wt = wts.get(key) || null;
    out.push({
      key, title: p.title || firstSentence(p.prompt) || taskTitle(wt, null, p.project, key), sessions: [],
      bucket: "starting", progress: { summary: p.prompt }, pr: null, worktree: wt, repo: p.project,
      repos: [p.project], fullTitle: "",
      machine: "", venues: [], quiet: false, updated: p.at, since: p.at, live: false, pending: p,
    });
  }
  for (const wt of wts.values()) {
    if (tasks.has(wt.id) || (pending && pending[wt.id]) || wt.picker_hidden || wt.origin === "system") continue;
    const repo = wt.project || wt.repo || "";
    const title = taskTitle(wt, null, repo, wt.id);
    const pr = prOfWorktree(wt);
    const about = describeWorktree(wt, title);
    out.push({
      key: wt.id, title, sessions: [], bucket: "earlier",
      progress: about ? { summary: about } : null, pr, worktree: wt, repo, repos: [repo],
      fullTitle: fullTitleOf(title, pr, wt),
      codespaces: claimedCodespaces(wt), unused: about === "No sessions yet", paired: paired(wt),
      machine: wt.machine || "", venues: [], quiet: false, live: false,
      updated: Math.max(parseStamp(wt.status_note_at), parseStamp(wt.last_resumed_at), parseStamp(wt.started_at)),
      since: 0,
    });
  }
  // Live groups keep a stable order (newest task first); Earlier is by last activity.
  out.sort((a, b) => BUCKET_RANK[a.bucket] - BUCKET_RANK[b.bucket] ||
    (a.bucket === "earlier" ? b.updated - a.updated : (b.since || 0) - (a.since || 0)) ||
    (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  return out;
}

/** Text, venue-type, and repo filtering for the board. */
export function filterTasks(tasks, { q = "", venue = "all", repo = "all" } = {}) {
  const needle = q.trim().toLowerCase();
  return tasks.filter((t) => {
    if (venue !== "all") {
      if (venue === "local" ? t.venues.length > 0 : !t.venues.includes(venue)) return false;
    }
    if (repo !== "all" && !(t.repos || [t.repo]).includes(repo)) return false;
    if (!needle) return true;
    const w = t.worktree || {};
    const hay = [t.title, t.fullTitle, t.key, ...(t.repos || [t.repo]), t.machine, t.pr && t.pr.number,
      t.progress && t.progress.summary, w.branch, w.summary, w.codename, w.subject, w.claims_summary,
      ...t.sessions.flatMap((x) => [x.s.session_id, x.s.branch, x.s.venue && x.s.venue.target])]
      .filter(Boolean).join(" ").toLowerCase();
    return needle.split(/\s+/).every((word) => hay.includes(word));
  });
}