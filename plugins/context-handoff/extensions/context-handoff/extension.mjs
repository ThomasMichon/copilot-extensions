// Context Handoff Extension for Copilot CLI
//
// Tracks session state and provides a generate_handoff_prompt tool
// for creating continuation prompts when context is getting large.
//
// Uses the session.usage_info event for accurate context window monitoring
// (currentTokens / tokenLimit) instead of heuristic turn counting.
//
// Integration points (all via observable session events -- the native runtime
// removed SDK callback hooks, so no `hooks` object is passed to joinSession):
// 1. generate_handoff_prompt tool -- on-demand structured handoff data
// 2. save_handoff_prompt tool -- persist composed handoff to machine-local state
// 3. session.usage_info event -- real-time context utilization monitoring
// 4. tool.execution_start / _complete events -- track modified files + tools
// 5. user.message event -- tracks turn count + first prompt (topic bias)
// 6. session.idle event -- delivers the queued context-pressure nudge to the
//    agent via session.send() (replaces onPostToolUse additionalContext). The
//    nudge JUST tells the agent to invoke the context-handoff skill; it does
//    not prescribe tool calls or a "write a file" outcome -- the skill owns the
//    sequencing (a live cutover through the active Herdr or mux host).
//
// The /handoff gesture is handled as a skill invocation (context-handoff
// skill), not a slash command. The skill triggers the agent to call
// generate_handoff_prompt, compose prose, and call save_handoff_prompt. The
// PRIMARY path then performs a live cutover (continue_handoff) -- spinning up a
// successor through the current Herdr pane or mux host, hands-free -- and only
// falls back to a copy/paste reply when no live host is available.

import {
  existsSync,
  mkdirSync,
  writeFileSync,
  renameSync,
  unlinkSync,
  readFileSync,
  readdirSync,
  statSync,
  openSync,
  closeSync,
} from "node:fs";
import { execSync, execFileSync } from "node:child_process";
import { join, basename } from "node:path";
import { homedir } from "node:os";
import { approveAll } from "@github/copilot-sdk";
import { joinSession } from "@github/copilot-sdk/extension";
import {
  CONTINUATION_DIRECTIVE,
  leadFrom,
  buildCutoverSeed,
} from "./cutover-seed.mjs";
import { supersededHandoffIds } from "./handoff-tasks.mjs";
import { loadContextHandoffConfig } from "./config.mjs";
import { contextPressure, formatContextUsage } from "./thresholds.mjs";
import { AGENT_WORKTREES_QUERY_TIMEOUT_MS } from "./cli-timeouts.mjs";
import {
  herdrHandoffDir,
  isHerdrPane,
  resolveHandoffCwd,
  runHandoffCutover,
} from "./handoff-core.mjs";

// --- State ---
const handoffConfig = loadContextHandoffConfig(process.cwd());
const state = {
  turnCount: 0,
  sessionId: null,
  cwd: null,
  filesModified: new Map(),       // path → { tool, turnIndex }
  toolInvocations: [],            // { tool, turn, summary }
  softReminderSent: false,        // additionalContext injected to agent
  hardReminderSent: false,        // additionalContext injected to agent
  softLogShown: false,            // session.log shown to user
  hardLogShown: false,            // session.log shown to user
  handoffGenerated: false,
  firstUserPrompt: null,          // first user message (for topic bias)
  // Context window tracking (from session.usage_info events)
  currentTokens: 0,
  tokenLimit: 0,
  conversationTokens: 0,
  systemTokens: 0,
  toolDefinitionsTokens: 0,
  messagesLength: 0,
  lastUtilization: 0,             // currentTokens / tokenLimit
};

// --- Helpers ---

// Lazy-initialize state from invocation context if onSessionStart missed
function ensureState(invocation) {
  if (!state.sessionId && invocation?.sessionId) {
    state.sessionId = invocation.sessionId;
  }
  if (!state.cwd) {
    state.cwd = process.cwd();
  }
}

function currentHandoffCwd() {
  const result = resolveHandoffCwd(state.cwd || process.cwd());
  if (result.cwd) state.cwd = result.cwd;
  return result;
}

function getGitInfo(cwd) {
  const run = (cmd) => {
    try {
      return execSync(cmd, { cwd, timeout: 5000, encoding: "utf-8" }).trim();
    } catch { return null; }
  };
  // Cap status to first 30 lines to avoid huge diffs
  let status = run("git status --short");
  if (status) {
    const lines = status.split("\n");
    if (lines.length > 30) {
      status = lines.slice(0, 30).join("\n") + `\n... ${lines.length - 30} more files omitted`;
    }
  }
  return {
    branch: run("git rev-parse --abbrev-ref HEAD"),
    repo: run("git remote get-url origin"),
    status,
  };
}

// --- Shared Logic ---

// Collect structured handoff data from current session state.
// Used by both the generate_handoff_prompt tool and the /handoff command.
function collectHandoffData(sid, overrides = {}) {
  const cwd = currentHandoffCwd().cwd || state.cwd || process.cwd();
  const git = getGitInfo(cwd);
  const utilPct = state.tokenLimit > 0
    ? Math.round(state.lastUtilization * 100)
    : null;
  const modifiedEntries = [...state.filesModified.entries()].slice(-20);

  return {
    data: {
      sessionId: sid,
      cwd,
      branch: git.branch,
      repo: git.repo,
      turnCount: state.turnCount,
      contextUtilization: utilPct !== null ? `${utilPct}%` : "unknown",
      currentTokens: state.currentTokens,
      tokenLimit: state.tokenLimit,
      filesModified: Object.fromEntries(modifiedEntries),
      gitStatus: git.status,
      toolInvocations: state.toolInvocations.slice(-10),
      firstUserPrompt: state.firstUserPrompt || null,
      agentSummary: overrides.summary || null,
      agentNextSteps: overrides.next_steps || null,
      generatedAt: new Date().toISOString(),
    },
    modifiedEntries,
    git,
    utilPct,
  };
}

// --- agent-dispatch integration (soft dependency) ---
// When an agent-dispatch coordinator is reachable, a handoff is stored as a
// *task* (payload = the handoff markdown) instead of a machine-local file, so
// it becomes durable, browsable, and claimable. It is picked up two ways, with
// two completion models: a LIVE CUTOVER successor (the primary path) uses
// `agent-dispatch consume <id> --defer-complete` and completes the task
// explicitly when it reaches the goal (deferred); a human paste / /resume-handoff
// uses `agent-dispatch consume <id>` (baton -- completed on pickup). context-
// handoff sits *on top of* agent-dispatch when it exists, and falls back to the
// machine-local file flow when it doesn't. All best-effort: any failure returns null / a safe
// default so the caller degrades to the file path.

// True if the `agent-dispatch` CLI answers a health probe (a live coordinator).
function agentDispatchAvailable() {
  try {
    execSync("agent-dispatch health", { timeout: 5000, stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

// Run a multi-machine system CLI binary cross-platform, returning stdout (throws on error).
// On Windows the `agent-worktrees` / `agent-dispatch` binstubs are `.cmd` files,
// which Node's `execFileSync` CANNOT spawn directly (CreateProcess won't execute
// a batch file without a shell -- it fails ENOENT). So on win32 we go through the
// shell (`execSync`) with each arg quoted for cmd.exe; elsewhere `execFileSync`
// is exact and injection-safe. This is why every agent-worktrees/agent-dispatch
// call here MUST use runCli, not execFileSync (issue: live cutover + task-mode
// silently fell back to file on Windows because execFileSync could not run .cmd).
function quoteWinArg(s) {
  s = String(s);
  return /[\s"&|<>^()%!]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function runCli(bin, args, opts = {}) {
  const { cwd, timeout = 15000 } = opts;
  if (process.platform === "win32") {
    const line = [bin, ...args].map(quoteWinArg).join(" ");
    return execSync(line, { cwd, timeout, encoding: "utf-8" });
  }
  return execFileSync(bin, args, { cwd, timeout, encoding: "utf-8" });
}

// Resolve an agent-worktrees identity value (null on miss). Resolves from the
// current CWD; when a sessionId is given it is passed as a binding-first
// fallback so a bare-resumed session (cwd=HOME) still resolves its worktree from
// the session->worktree binding rather than the (HOME) cwd. See #4098.
function agentWorktreesGet(key, cwd, sessionId) {
  return agentWorktreesGetResult(key, cwd, sessionId).value;
}

function agentWorktreesGetResult(key, cwd, sessionId) {
  const argv = ["get", key];
  if (sessionId) argv.push("--session-id", sessionId);
  try {
    const out = runCli("agent-worktrees", argv, {
      cwd,
      timeout: AGENT_WORKTREES_QUERY_TIMEOUT_MS,
    }).trim();
    if (out) return { value: out, error: null };
    return {
      value: null,
      error:
        `agent-worktrees get ${key} returned an empty result for cwd ` +
        `${cwd || process.cwd()}${sessionId ? ` and session ${sessionId}` : ""}`,
    };
  } catch (error) {
    const detail = (
      error?.stderr || error?.stdout || error?.message || String(error)
    ).toString().trim();
    return {
      value: null,
      error:
        `agent-worktrees get ${key} failed` +
        (detail ? `: ${detail}` : ""),
    };
  }
}

const HANDOFF_META_PREFIX = "<!-- context-handoff:";
const HANDOFF_META_SUFFIX = "-->";

// Mirror the stored handoff into the worktree's OWN record as a terse,
// session-tagged `handoff` entry (record-first recovery): the sessionStart
// digest then surfaces "a handoff was produced here (task <id>, topic <title>)"
// even if agent-dispatch is unreachable or a live cutover never completed. The
// full brief still lives in the task/file; this is only the durable pointer.
// Best-effort -- a miss never affects the handoff itself.
function noteHandoffInRecord(cwd, sid, ref, title) {
  if (isHerdrPane()) return;
  try {
    const argv = ["note-handoff"];
    if (ref) argv.push("--task", ref);
    if (title) argv.push("--title", title);
    if (sid) argv.push("--session-id", sid);
    runCli("agent-worktrees", argv, { cwd, timeout: 5000 });
  } catch {
    /* history is advisory -- ignore */
  }
}

function safePathSegment(value) {
  return String(value || "unknown")
    .replace(/[^A-Za-z0-9._-]/g, "_")
    .slice(0, 160) || "unknown";
}

function currentPaneId() {
  if (isHerdrPane()) return null;
  return process.env.TMUX_PANE || process.env.PSMUX_PANE || null;
}

function currentMuxSession(pane = currentPaneId()) {
  if (!pane) return null;
  try {
    return runCli(
      process.platform === "win32" ? "psmux" : "tmux",
      ["display-message", "-p", "-t", pane, "#{session_name}"],
      { timeout: 5000 },
    ).trim() || null;
  } catch {
    return null;
  }
}

function processAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

function worktreeInfo(cwd, sid) {
  if (isHerdrPane()) {
    return { wtDir: null, worktree: null, stateDir: null };
  }
  const wtDir = agentWorktreesGet("worktree-dir", cwd, sid);
  const worktree = wtDir ? basename(wtDir) : null;
  const stateDir = agentWorktreesGet("worktree-state-dir", cwd, sid);
  return { wtDir, worktree, stateDir };
}

function makeHandoffMetadata({ sid, cwd, title, storage, taskId = null }) {
  const { wtDir, worktree, stateDir } = worktreeInfo(cwd, sid);
  const id = `handoff-${safePathSegment(sid)}`;
  return {
    kind: "context-handoff",
    version: 1,
    id,
    storage,
    taskId,
    sessionId: sid,
    cwd,
    title: title || "",
    worktree,
    worktreeDir: wtDir,
    oldPane: currentPaneId(),
    muxSession: currentMuxSession(),
    stateDir,
    createdAt: new Date().toISOString(),
  };
}

function encodeHandoffPayload(promptText, metadata) {
  return `${HANDOFF_META_PREFIX} ${JSON.stringify(metadata)} ${HANDOFF_META_SUFFIX}\n${promptText}`;
}

function decodeHandoffPayload(raw) {
  const text = String(raw || "");
  const firstNewline = text.indexOf("\n");
  const firstLine = firstNewline >= 0 ? text.slice(0, firstNewline) : text;
  if (!firstLine.startsWith(HANDOFF_META_PREFIX) || !firstLine.endsWith(HANDOFF_META_SUFFIX)) {
    return { metadata: null, text };
  }
  const jsonText = firstLine
    .slice(HANDOFF_META_PREFIX.length, -HANDOFF_META_SUFFIX.length)
    .trim();
  try {
    return {
      metadata: JSON.parse(jsonText),
      text: firstNewline >= 0 ? text.slice(firstNewline + 1) : "",
    };
  } catch {
    return { metadata: null, text };
  }
}

function handoffDirFor(cwd, sid) {
  const herdrDir = herdrHandoffDir(cwd);
  if (herdrDir) return herdrDir;
  const stateDir = agentWorktreesGet("worktree-state-dir", cwd, sid);
  return stateDir ? join(stateDir, "handoff") : null;
}

function writeJsonAtomic(path, value) {
  const tmp = `${path}.${process.pid}.tmp`;
  try {
    writeFileSync(tmp, JSON.stringify(value, null, 2), "utf-8");
    renameSync(tmp, path);
  } finally {
    try { unlinkSync(tmp); } catch { /* renamed or never created */ }
  }
}

function saveFileHandoff(promptText, sid, cwd, title) {
  const metadata = makeHandoffMetadata({ sid, cwd, title, storage: "file" });
  const dir = handoffDirFor(cwd, sid);
  if (!dir) {
    const resolution = agentWorktreesGetResult(
      "worktree-state-dir", cwd, sid,
    );
    return {
      error:
        `${resolution.error}. Run \`agent-worktrees get worktree-state-dir ` +
        `${sid ? `--session-id ${safePathSegment(sid)}` : ""}\` from the ` +
        "intended adopted checkout and verify it reports a machine-local path.",
    };
  }
  const path = join(dir, `${metadata.id}.json`);
  try {
    mkdirSync(dir, { recursive: true });
    writeJsonAtomic(path, {
        ...metadata,
        consumed: false,
        consumedAt: null,
        promptText,
    });
    return { path, id: metadata.id, metadata };
  } catch (error) {
    return {
        error:
          `resolved handoff state directory ${dir}, but the atomic write failed: ` +
          `${error?.message || String(error)}`,
    };
  }
}

function readFileHandoff(cwd, sid, handoffId, explicitPath = null) {
  const path = explicitPath || (() => {
    const dir = handoffDirFor(cwd, sid);
    return dir ? join(dir, `${safePathSegment(handoffId)}.json`) : null;
  })();
  if (!path || !existsSync(path)) return null;
  try {
    const record = JSON.parse(readFileSync(path, "utf-8"));
    return { path, record };
  } catch {
    return null;
  }
}

function markFileHandoffConsumed(path, record, sid) {
  const consumed = {
    ...record,
    consumed: true,
    consumedAt: record.consumedAt || new Date().toISOString(),
    consumedBySession: sid || record.consumedBySession || null,
  };
  writeJsonAtomic(path, consumed);
  return consumed;
}

// --- Live-cutover handoff (issue #2251) ---
// The seed builders `leadFrom` + `buildCutoverSeed` now live in the pure,
// SDK-free sibling module `./cutover-seed.mjs` (imported above) so the seed
// SHAPE -- notably the bash-first task-cutover invariant, GitHub issue #853 --
// is independently unit-testable and clean-room-importable without loading this
// whole session extension. See that module for the rationale.

// Best-effort, user-visible progress line to the (successor) Copilot session.
// The handoff must never block or fail on logging, so swallow everything.
function logProgress(msg, opts = { level: "info" }) {
  try {
    const p = session?.log?.(msg, opts);
    if (p && typeof p.catch === "function") p.catch(() => {});
  } catch {
    /* logging is best-effort */
  }
}

// Retire a specific pane after successor-side consume. Best-effort.
function retireCutoverPane(cwd, pane, metadata = {}) {
  try {
    const argv = [
      "handoff-cutover",
      "--retire-pane", pane,
      "--successor-verified",
      "--retire-reason", "handoff-consume",
    ];
    if (metadata.worktree) argv.push("--worktree-id", metadata.worktree);
    if (metadata.sessionId) argv.push("--session-id", metadata.sessionId);
    argv.push("--require-mux-identity");
    if (metadata.muxSession) argv.push("--mux-session", metadata.muxSession);
    const out = runCli("agent-worktrees", argv, {
      cwd,
      timeout: 30000,
    });
    return JSON.parse(out);
  } catch {
    return { ok: false, pane, gone: false, method: "error" };
  }
}

// Durably conclude the predecessor session as `handed-off` in the
// agent-worktrees ground layer after the successor consumes the handoff.
function concludeOldSessionHandedOff(cwd, sid) {
  if (!sid) return false;
  try {
    const wtDir = agentWorktreesGet("worktree-dir", cwd, sid);
    const worktree = wtDir ? basename(wtDir) : null;
    if (!worktree) return false;
    runCli("agent-worktrees", [
      "conclude-session",
      "--worktree", worktree,
      "--session", sid,
      "--state", "handed-off",
    ], { cwd, timeout: 10000 });
    return true;
  } catch {
    return false;
  }
}

function bindConsumedHandoff(cwd, token, metadata, sid) {
  if (!token || !sid) return false;
  try {
    const argv = [
      "bind-session",
      "--session-id", sid,
      "--handoff-token", token,
    ];
    if (metadata?.worktree) {
      argv.push("--worktree-id", metadata.worktree);
    }
    runCli("agent-worktrees", argv, { cwd, timeout: 10000 });
    return true;
  } catch {
    return false;
  }
}

// Store a handoff as a proposed, handoff-labeled agent-dispatch task pinned to
// the current worktree; payload = metadata + handoff markdown. Returns the task
// id, or null if anything fails (the caller then falls back to a worktree file).
function dispatchHandoff(promptText, sid, cwd, title) {
  const metadata = makeHandoffMetadata({ sid, cwd, title, storage: "agent-dispatch" });
  const dir = metadata.stateDir ? join(metadata.stateDir, "handoff") : null;
  if (!dir) return null;
  const tmp = join(dir, `${metadata.id}-payload-${process.pid}.md`);
  try {
    mkdirSync(dir, { recursive: true });
    writeFileSync(tmp, encodeHandoffPayload(promptText, metadata), "utf-8");
    const machine = agentWorktreesGet("machine", cwd, sid);
    const wtDir = agentWorktreesGet("worktree-dir", cwd, sid);
    const worktree = wtDir ? basename(wtDir) : null;
    // A handoff must land in *its own* worktree; if we can't resolve one, bail
    // to the file flow rather than file an unpinned, anyone-can-claim task.
    if (!worktree) return null;
    const argv = [
      "create",
      title || "Handoff: continue this session",
      "--proposed",
      "--label", "handoff",
      "--source", "context-handoff",
      "--dedup-key", `handoff-${sid}`,
      "--payload-file", tmp,
      "--target-worktree", worktree,
      "--affinity", `worktree=${worktree}`,
    ];
    if (machine) argv.push("--target-machine", machine);
    const out = runCli("agent-dispatch", argv, {
      cwd,
      timeout: 15000,
    });
    const task = JSON.parse(out);
    if (task?.id) {
      // Supersede: a newer handoff for THIS worktree makes any older pending
      // handoff for it moot -- abandon them so a re-handoffed worktree doesn't
      // pile up one stale task per session (dedup is per-session, so different
      // sessions on one worktree each file their own).
      abandonSupersededHandoffs(cwd, worktree, task.id);
    }
    return task?.id ? { id: task.id, metadata: { ...metadata, taskId: task.id } } : null;
  } catch {
    return null;
  } finally {
    try { unlinkSync(tmp); } catch { /* already gone -- fine */ }
  }
}

// Run an `agent-dispatch` subcommand, returning parsed JSON (or null on error).
function agentDispatchJson(argv, cwd) {
  try {
    const out = runCli("agent-dispatch", argv, {
      cwd,
      timeout: 15000,
    });
    return JSON.parse(out);
  } catch {
    return null;
  }
}

// Abandon this worktree's OTHER pending context-handoff tasks (proposed/queued),
// superseded by the just-stored handoff `keepId`. Dedup is per-session
// (`handoff-<sid>`), so different sessions on one worktree each file their own
// task; without this, a re-handoffed worktree accumulates one stale task per
// session. Best-effort: a cleanup failure must never break the handoff store (the
// coordinator's orphan reaper is the backstop for anything left behind).
function abandonSupersededHandoffs(cwd, worktree, keepId) {
  const tasks = agentDispatchJson(
    ["list", "--status", "proposed,queued", "--label", "handoff"],
    cwd,
  );
  for (const id of supersededHandoffIds(tasks, worktree, keepId)) {
    try {
      runCli(
        "agent-dispatch",
        [
          "abandon", id, "--permit",
          "--reason", "superseded by a newer handoff for this worktree",
        ],
        { cwd, timeout: 15000 },
      );
    } catch {
      /* best-effort -- anything left behind is reaped by the GC orphan pass */
    }
  }
}

// Find this worktree's newest pending handoff task (proposed + label 'handoff',
// pinned to `worktree`). Returns the task object, or null if none / no CLI.
function findHandoffTask(cwd, worktree) {
  const tasks = agentDispatchJson(
    ["list", "--status", "proposed,queued", "--label", "handoff"],
    cwd,
  );
  if (!Array.isArray(tasks)) return null;
  const mine = tasks.filter((t) => t?.target_worktree === worktree);
  if (mine.length === 0) return null;
  mine.sort((a, b) => (a.created_at < b.created_at ? 1 : -1)); // newest first
  return mine[0];
}

function readTaskPayloadRaw(cwd, taskId) {
  try {
    return runCli("agent-dispatch", ["payload", taskId, "--raw"], {
      cwd,
      timeout: 15000,
    });
  } catch {
    return "";
  }
}

function runAgentDispatchConsume(cwd, taskId, deferComplete) {
  const argv = ["consume", taskId];
  if (deferComplete) argv.push("--defer-complete");
  return runCli("agent-dispatch", argv, { cwd, timeout: 20000 });
}

function retireAfterConsume(cwd, metadata, sid, handoffToken) {
  const result = {
    retired: false,
    retireResult: null,
    concluded: false,
  };
  if (isHerdrPane()) return result;
  result.concluded = bindConsumedHandoff(cwd, handoffToken, metadata, sid);
  if (!result.concluded && metadata?.sessionId) {
    // Compatibility with a handoff written before the numbered ledger existed.
    result.concluded = concludeOldSessionHandedOff(cwd, metadata.sessionId);
  }
  if (metadata?.oldPane) {
    const oldSid = metadata.sessionId || sid || null;
    // Emit what we're waiting on BEFORE the (blocking) retire so the successor's
    // Copilot shows the pause and its reason -- the retire below blocks until
    // the OLD Copilot process actually exits (or a bounded timeout).
    logProgress(
      "[Context Handoff] Retiring the previous session" +
        (oldSid ? ` ${oldSid}` : "") +
        ` (pane ${metadata.oldPane}); waiting for its Copilot process to exit ` +
        "before continuing…",
    );
    result.retireResult = retireCutoverPane(cwd, metadata.oldPane, metadata);
    const rr = result.retireResult || {};
    const cop = rr.copilot || {};
    // Success requires BOTH the pane retired AND the old Copilot gone (the host
    // verb folds that into ``ok``); fall back to the pane ``gone`` flag when the
    // process check was not run (no session id).
    result.retired = Boolean(rr.ok ?? rr.gone);
    if (result.retired) {
      const reaped =
        cop.reaped > 0
          ? ` (reaped pid${(cop.pids || []).length > 1 ? "s" : ""} ` +
            `${(cop.pids || []).join(", ")})`
          : "";
      logProgress(
        `[Context Handoff] Previous session terminated${reaped}. Continuing.`,
      );
    } else {
      logProgress(
        "[Context Handoff] WARNING: the previous session did not confirm " +
          `termination (pane ${metadata.oldPane}` +
          (oldSid ? `, session ${oldSid}` : "") +
          "); it may reappear as a parallel session. Force it with: " +
          `agent-worktrees reclaim --session-id ${oldSid || "<id>"}`,
        { level: "warning" },
      );
    }
  }
  return result;
}

function consumeDispatchHandoffTask(
  cwd, taskId, sid, deferComplete = false, { deferRetire = false } = {},
) {
  const before = decodeHandoffPayload(readTaskPayloadRaw(cwd, taskId));
  try {
    const consumed = runAgentDispatchConsume(cwd, taskId, deferComplete);
    const decoded = decodeHandoffPayload(consumed);
    const metadata = decoded.metadata || before.metadata || {};
    const retire = deferRetire
      ? { retired: false, retireResult: null, concluded: false }
      : retireAfterConsume(cwd, metadata, sid, taskId);
    return {
      ok: true,
      id: taskId,
      payload: decoded.text.trim(),
      metadata,
      retire,
    };
  } catch (e) {
    const stdout = (e?.stdout || "").toString();
    if (stdout) {
      return {
        ok: false,
        alreadyConsumed: true,
        id: taskId,
        message: stdout.trim(),
      };
    }
    return { ok: false, id: taskId, message: "Could not consume handoff task." };
  }
}

function consumeFileHandoff(
  cwd, sid, handoffId, explicitPath = null, { deferRetire = false } = {},
) {
  const found = readFileHandoff(cwd, sid, handoffId, explicitPath);
  if (!found) {
    return { ok: false, message: "File-backed handoff was not found." };
  }
  const { path } = found;
  const lockPath = `${path}.consume.lock`;
  let lockFd = null;
  for (let attempt = 0; attempt < 2 && lockFd === null; attempt++) {
    try {
      lockFd = openSync(lockPath, "wx");
      writeFileSync(lockFd, JSON.stringify({
        pid: process.pid, sessionId: sid || null, createdAt: new Date().toISOString(),
      }), "utf-8");
    } catch (error) {
      if (error?.code !== "EEXIST") {
        if (lockFd !== null) {
          try { closeSync(lockFd); } catch { /* best-effort */ }
          lockFd = null;
        }
        try { unlinkSync(lockPath); } catch { /* nothing to clean */ }
        return {
          ok: false,
          message: `Could not lock file-backed handoff for consumption: ${error.message}`,
        };
      }
      let ownerPid = null;
      let lockAgeMs = 0;
      try {
        ownerPid = JSON.parse(readFileSync(lockPath, "utf-8")).pid;
      } catch { /* incomplete lock from a crashed writer */ }
      try {
        lockAgeMs = Date.now() - statSync(lockPath).mtimeMs;
      } catch { /* lock vanished or cannot be inspected */ }
      let ownerAlive = false;
      if (Number.isInteger(ownerPid) && ownerPid > 0) {
        ownerAlive = processAlive(ownerPid);
      }
      const safeToReclaim = Number.isInteger(ownerPid)
        ? !ownerAlive
        : lockAgeMs >= 30_000;
      if (safeToReclaim && attempt === 0) {
        const recoveryPath = `${lockPath}.recover`;
        let recoveryFd = null;
        for (let recoveryAttempt = 0; recoveryAttempt < 2 && recoveryFd === null; recoveryAttempt++) {
          try {
            recoveryFd = openSync(recoveryPath, "wx");
            writeFileSync(recoveryFd, JSON.stringify({
              pid: process.pid, createdAt: new Date().toISOString(),
            }), "utf-8");
          } catch (recoveryError) {
            if (recoveryError?.code !== "EEXIST") break;
            let recoveryPid = null;
            let recoveryAgeMs = 0;
            try {
              recoveryPid = JSON.parse(readFileSync(recoveryPath, "utf-8")).pid;
            } catch { /* incomplete recovery lock */ }
            try {
              recoveryAgeMs = Date.now() - statSync(recoveryPath).mtimeMs;
            } catch { /* vanished */ }
            let recoveryAlive = false;
            if (Number.isInteger(recoveryPid) && recoveryPid > 0) {
              recoveryAlive = processAlive(recoveryPid);
            }
            const recoveryStale = Number.isInteger(recoveryPid)
              ? !recoveryAlive
              : recoveryAgeMs >= 30_000;
            if (recoveryStale && recoveryAttempt === 0) {
              try { unlinkSync(recoveryPath); } catch { /* raced another recovery */ }
              continue;
            }
            break;
          }
        }
        if (recoveryFd === null) {
          return {
            ok: false,
            message:
              `Handoff ${found.record.id || path} recovery is already active.`,
          };
        }
        try {
          let currentPid = null;
          let currentAgeMs = 0;
          try {
            currentPid = JSON.parse(readFileSync(lockPath, "utf-8")).pid;
          } catch { /* incomplete lock */ }
          try {
            currentAgeMs = Date.now() - statSync(lockPath).mtimeMs;
          } catch { /* already gone */ }
          let currentAlive = false;
          if (Number.isInteger(currentPid) && currentPid > 0) {
            currentAlive = processAlive(currentPid);
          }
          const stillStale = Number.isInteger(currentPid)
            ? !currentAlive
            : currentAgeMs >= 30_000;
          if (stillStale) {
            try { unlinkSync(lockPath); } catch { /* already gone */ }
          }
        } finally {
          try { closeSync(recoveryFd); } catch { /* best-effort */ }
          try { unlinkSync(recoveryPath); } catch { /* already gone */ }
        }
        continue;
      }
      return {
        ok: false,
        message:
          `Handoff ${found.record.id || path} is already being consumed. ` +
          "Do not replay it; retry only after the active consumer finishes.",
      };
    }
  }
  try {
    const current = readFileHandoff(cwd, sid, handoffId, path);
    if (!current) {
      return { ok: false, message: "File-backed handoff disappeared before consumption." };
    }
    const { record } = current;
    if (record.consumed) {
      if (sid && record.consumedBySession === sid) {
        const retire = deferRetire
          ? { retired: false, retireResult: null, concluded: false }
          : retireAfterConsume(cwd, record, sid, record.id);
        return {
          ok: true,
          id: record.id,
          path,
          payload: String(record.promptText || "").trim(),
          metadata: record,
          retire,
        };
      }
      return {
        ok: false,
        alreadyConsumed: true,
        id: record.id,
        message:
          `Handoff ${record.id || path} was already consumed at ` +
          `${record.consumedAt || "an unknown time"}. Do not replay it.`,
      };
    }
    const consumed = markFileHandoffConsumed(path, record, sid);
    const retire = deferRetire
      ? { retired: false, retireResult: null, concluded: false }
      : retireAfterConsume(cwd, consumed, sid, consumed.id);
    return {
      ok: true,
      id: consumed.id,
      path,
      payload: String(consumed.promptText || "").trim(),
      metadata: consumed,
      retire,
    };
  } finally {
    if (lockFd !== null) {
      try { closeSync(lockFd); } catch { /* best-effort */ }
    }
    try { unlinkSync(lockPath); } catch { /* already gone */ }
  }
}

function formatConsumeResult(result, { deferComplete = false } = {}) {
  if (!result?.ok) {
    const message = result?.message || "Handoff could not be consumed.";
    return (
      `${message}\n\nHandoff consumption is blocked. Do not treat the missing ` +
      `brief as completion or reconstruct a different objective from session ` +
      `history; retry the documented path when appropriate or report the blocker.`
    );
  }
  const retire = result.retire || {};
  const retireResult = retire.retireResult;
  const lines = [
    "## Handoff Consumed",
    "",
    result.id ? `**Handoff:** ${result.id}` : null,
    retireResult
      ? `**Predecessor retire:** ${retireResult.method || "unknown"} ` +
        `(pane gone: ${Boolean(retireResult.gone)}` +
        (retireResult.copilot
          ? `; old Copilot: reaped ${retireResult.copilot.reaped || 0}, ` +
            `survivors ${retireResult.copilot.survivors || 0}`
          : "") +
        ")"
      : "**Predecessor retire:** no recorded predecessor pane",
    retire.concluded
      ? "**Predecessor session:** marked handed off"
      : null,
    deferComplete && result.id
      ? `**Completion:** when the handoff goal is reached, run \`agent-dispatch complete ${result.id}\`.`
      : null,
    "",
    CONTINUATION_DIRECTIVE,
    "",
    "---",
    "",
    result.payload || "(The handoff payload was empty.)",
  ].filter(Boolean);
  return lines.join("\n");
}

// Fallback (no coordinator): find the newest unconsumed handoff file for this
// worktree. Returns { path, record } or null.
function findHandoffFile(cwd, sid) {
  const root = handoffDirFor(cwd, sid);
  if (!root || !existsSync(root)) return null;
  let best = null;
  let bestMtime = 0;
  let sessions;
  try {
    sessions = readdirSync(root);
  } catch {
    return null;
  }
  for (const file of sessions) {
    if (!file.endsWith(".json")) continue;
    const path = join(root, file);
    let record;
    let mtime;
    try {
      record = JSON.parse(readFileSync(path, "utf-8"));
      if (
        record.consumed
        && (!sid || record.consumedBySession !== sid)
      ) continue;
      mtime = statSync(path).mtimeMs;
    } catch {
      continue;
    }
    const cwdMatch = record.cwd === cwd || String(record.promptText || "").includes(cwd);
    const score = mtime + (cwdMatch ? 1e15 : 0); // CWD match dominates recency
    if (score > bestMtime) {
      bestMtime = score;
      best = { path, record };
    }
  }
  return best;
}

// Compose the prompt injected into the current session on resume.
function buildResumePrompt(handoffText, source) {
  return [
    `You are resuming a handoff (${source}). The continuation context`,
    `follows -- treat it as the founding brief for this session and carry the`,
    `work forward from where the previous session left off. Do NOT start over`,
    `or spin up a fresh worktree; continue in place.`,
    CONTINUATION_DIRECTIVE,
    ``,
    `---`,
    ``,
    handoffText,
  ].join("\n");
}

// Persist a small context-usage sidecar that the agent-worktrees picker
// reads to show live context-window utilization per worktree. The exact
// token counts arrive via the session.usage_info event, which is delivered
// only to this extension (never written to events.jsonl), so this file is
// the sole on-disk source. Best-effort: never throws into the event loop.
function persistState() {
  try {
    const sid = state.sessionId;
    if (!sid) return;
    const dir = join(homedir(), ".copilot", "session-state", sid);
    // Don't create the dir -- an active session already owns it; a missing
    // dir means there's nothing meaningful to associate the sidecar with.
    if (!existsSync(dir)) return;
    const pct = state.tokenLimit > 0
      ? Math.round(state.lastUtilization * 100)
      : null;
    const payload = {
      sessionId: sid,
      currentTokens: state.currentTokens,
      tokenLimit: state.tokenLimit,
      utilizationPct: pct,
      // Numerator breakdown, straight from the session.usage_info event. Lets a
      // consumer see whether currentTokens is dominated by the fixed
      // system-prompt + tool/skill-definition overhead vs. live conversation --
      // i.e. why this utilization% can differ from a narrower conversation-only
      // display elsewhere.
      conversationTokens: state.conversationTokens,
      systemTokens: state.systemTokens,
      toolDefinitionsTokens: state.toolDefinitionsTokens,
      turnCount: state.turnCount,
      updatedAt: new Date().toISOString(),
    };
    writeFileSync(join(dir, "context.json"), JSON.stringify(payload), "utf-8");
  } catch {
    // Best-effort; the picker simply omits context% when the file is absent.
  }
}

// Format handoff data as a markdown document suitable for continuation.
function formatHandoffMarkdown(handoffData, scope) {
  const lines = [
    `# Session Handoff`,
    "",
    `**Session:** ${handoffData.sessionId}`,
    `**CWD:** ${handoffData.cwd}`,
    `**Branch:** ${handoffData.branch || "(detached)"}`,
    `**Turn count:** ${handoffData.turnCount}`,
    `**Context utilization:** ${handoffData.contextUtilization}`,
    `**Generated:** ${handoffData.generatedAt}`,
    "",
  ];

  if (scope) {
    lines.push(`## Continuation Scope`, `> ${scope}`, "");
  }

  if (handoffData.firstUserPrompt) {
    lines.push(
      `## Original Request`,
      `> ${handoffData.firstUserPrompt.slice(0, 500)}`,
      ""
    );
  }

  const files = Object.entries(handoffData.filesModified || {});
  if (files.length > 0) {
    lines.push(`## Files Modified`);
    for (const [path, info] of files) {
      lines.push(`- \`${path}\` (${info.tool}, turn ${info.turnIndex})`);
    }
    lines.push("");
  }

  if (handoffData.gitStatus) {
    lines.push(`## Git Status`, "```", handoffData.gitStatus, "```", "");
  }

  if (handoffData.agentSummary) {
    lines.push(`## Summary`, handoffData.agentSummary, "");
  }
  if (handoffData.agentNextSteps) {
    lines.push(`## Next Steps`, handoffData.agentNextSteps, "");
  }

  return lines.join("\n");
}

// --- Extension ---

const session = await joinSession({
  onPermissionRequest: approveAll,

  tools: [
    {
      name: "generate_handoff_prompt",
      description:
        "Generate structured session facts for creating a continuation " +
        "prompt. Returns session metadata, files modified, git status, " +
        "and key tool invocations. Compose the compact effort-backed shape " +
        "when a valid open active effort exists; otherwise compose the full " +
        "standalone shape. In either mode, preserve the parent completion gate " +
        "rather than treating the latest completed phase as the objective.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          summary: {
            type: "string",
            description:
              "Optional 1-2 sentence summary of what the session accomplished. " +
              "If omitted, the tool returns raw facts only.",
          },
          next_steps: {
            type: "string",
            description:
              "Optional description of what should happen next.",
          },
        },
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        const sid = state.sessionId || invocation?.sessionId || "unknown";
        const { data: handoffData, modifiedEntries } = collectHandoffData(sid, args);

        state.handoffGenerated = true;

        return {
          textResultForLlm: [
            "## Handoff Data",
            "",
            `**Session:** ${handoffData.sessionId}`,
            `**CWD:** ${handoffData.cwd}`,
            `**Branch:** ${handoffData.branch || "(detached)"}`,
            `**Turn count:** ${handoffData.turnCount}`,
            `**Context utilization:** ${handoffData.contextUtilization}`,
            "",
            handoffData.firstUserPrompt
              ? `### Original Request\n> ${handoffData.firstUserPrompt.slice(0, 300)}\n`
              : "",
            "### Files Modified",
            ...modifiedEntries.map(
              ([path, info]) => `- \`${path}\` (${info.tool}, turn ${info.turnIndex})`
            ),
            "",
            "### Git Status",
            "```",
            handoffData.gitStatus || "(clean)",
            "```",
            "",
            args.summary ? `### Agent Summary\n${args.summary}\n` : "",
            args.next_steps ? `### Agent Next Steps\n${args.next_steps}\n` : "",
            "",
            "---",
            "Now follow the context-handoff skill:",
            "1. Compose handoff markdown from this data plus your live context.",
            "   Use the compact effort-backed shape when a valid open active",
            "   effort exists; otherwise use the full standalone shape. Keep",
            "   separate completion gates for this handoff leg and the parent",
            "   objective/worktree.",
            "   A completed phase is progress, not a reason to omit later work.",
            "   If the parent objective truly has no actionable work left, do not",
            "   create a live handoff merely to report that fact; finish instead.",
            "2. Call save_handoff_prompt with the composed markdown as `prompt_text`",
            "   (and an optional short `title`). It stores the handoff — as an",
            "   checkout-scoped machine-local file in Herdr; otherwise as an",
            "   agent-dispatch task when a coordinator and worktree are reachable,",
            "   with a machine-local file fallback — and returns the short paste prompt",
            "   plus a HANDOFF_SEED for live cutover.",
            "3. In a Herdr or mux pane, call continue_handoff with the exact",
            "   HANDOFF_SEED. Herdr creates a sibling and retains this predecessor;",
            "   mux retires it only after successor pickup. Without either host,",
            "   reply with ONLY the short paste prompt.",
            "Do NOT paste the handoff contents, commit anything, or claim the",
            "handoff auto-loads on restart (it does not).",
          ].join("\n"),
          resultType: "success",
        };
      },
    },
    {
      name: "save_handoff_prompt",
      description:
        "Store the composed handoff markdown and return what short prompt to reply " +
        "with. In Herdr, the handoff is stored as a checkout-scoped one-time " +
        "machine-local file. Otherwise, when an agent-dispatch coordinator is " +
        "reachable, it is stored as a *proposed, handoff-labeled task* pinned to this worktree " +
        "(payload = the markdown, no session file) and resumed next session via " +
        "/resume-handoff; otherwise it falls back to a one-time machine-local " +
        "file outside the repo checkout. An active Herdr pane uses " +
        "context-handoff's checkout-scoped state and does not require " +
        "agent-worktrees. " +
        "Call this after composing the handoff from " +
        "generate_handoff_prompt data. Pass the markdown as `prompt_text` (the " +
        "`prompt` alias is also accepted); an optional short `title` labels the " +
        "task. Returns the short reply prompt AND, on a `HANDOFF_SEED:` line, the " +
        "exact seed string to pass to `continue_handoff` if you are performing a " +
        "LIVE cutover (e.g. from /handoff-continue). The handoff is NEVER loaded " +
        "automatically by a future session.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          prompt_text: {
            type: "string",
            description: "The composed effort-backed or standalone handoff markdown.",
          },
          title: {
            type: "string",
            description:
              "Optional short, specific title for the handoff task (e.g. " +
              "'Fix agent-dispatch producer recovery'). Used only in the " +
              "agent-dispatch task path.",
          },
          prompt: {
            type: "string",
            description: "Alias for prompt_text (accepted for convenience).",
          },
        },
        // Intentionally no `required`: the handler validates so a missing or
        // misnamed argument returns a clear message instead of a generic
        // "tool execution failed" (writeFileSync on undefined used to throw).
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        const sid = state.sessionId || invocation?.sessionId;
        if (!sid || sid === "unknown") {
          return "Cannot save handoff prompt: sessionId is unavailable.";
        }

        const text = (args?.prompt_text ?? args?.prompt ?? "").toString().trim();
        if (!text) {
          return (
            "Cannot save handoff: pass the composed handoff markdown as `prompt_text` " +
            "(the `prompt` alias is also accepted). Nothing was written."
          );
        }

        const cwdResult = currentHandoffCwd();
        if (!cwdResult.cwd) {
          return (
            "Cannot save handoff: the current Herdr pane working directory " +
            `could not be resolved. Nothing was written. [host: ${cwdResult.error}]`
          );
        }
        const cwd = cwdResult.cwd;
        const title = (args?.title ?? "").toString().trim();
        // Front-load the seed with the specific action so the successor
        // session's title-inference (biased toward the START of the prompt)
        // derives a meaningful title from the topic rather than the generic
        // handoff boilerplate that follows it.
        const lead = leadFrom(title);

        // Herdr stores locally without agent-worktrees. Other hosts prefer an
        // agent-dispatch task and fall back to the same one-time file format.
        // and derive both the short reply prompt (the baton paste-seed) and the
        // cutover seed. Storage is single-responsibility here; a live cutover is
        // a SEPARATE, explicit continue_handoff call the agent makes afterward,
        // passing the HANDOFF_SEED below (the *deferred* cutover seed).
        let seed = null;          // baton paste-seed (== the paste reply)
        let cutoverSeed = null;   // deferred cutover seed (== HANDOFF_SEED)
        let storedMsg = null;     // the instruction to reply with

        if (!isHerdrPane() && agentDispatchAvailable()) {
          const stored = dispatchHandoff(text, sid, cwd, title);
          const taskId = stored?.id;
          if (taskId) {
            // Two seeds, two completion models (see the context-handoff skill):
            //
            // - PASTE seed (baton): a human resuming in-place (/resume-handoff,
            //   or pasting into /clear) is driving, so `consume <id>` loads the
            //   brief AND marks the baton spent -- completed on pickup. The
            //   continuation *work* is tracked by its effort/issue.
            // - CUTOVER seed (deferred): a live-cutover successor is a dispatched
            //   autopilot CLI, so it uses `consume <id> --defer-complete` (load
            //   brief + take ownership, but NOT complete) and completes the task
            //   EXPLICITLY only when it reaches the handoff's goal -- so
            //   `completed` means the work is done, not the baton was handed off.
            //
            // Both are single-line ASCII so they ride `copilot -i` intact.
            // Each LEADS with the specific action (`lead`) so the successor
            // session's title-inference resolves to the topic, not the generic
            // "Agent Dispatch Task Handoff" boilerplate.
            seed =
              `${lead}. You are resuming a handoff (agent-dispatch task ` +
              `${taskId}); continue the prior session's work IN PLACE -- do not ` +
              `restart or create a new worktree. Load your full brief by ` +
              `running: agent-dispatch consume ${taskId} . ` +
              `${CONTINUATION_DIRECTIVE}`;
            cutoverSeed = buildCutoverSeed("task", taskId, lead, {
              oldPane: stored?.metadata?.oldPane,
              worktree: stored?.metadata?.worktree,
              worktreeDir: stored?.metadata?.worktreeDir,
              sessionId: sid,
              muxSession: stored?.metadata?.muxSession,
            });
            // Mirror the handoff into the worktree record (record-first recovery).
            noteHandoffInRecord(cwd, sid, taskId, title);
            storedMsg = (
              `Handoff stored as agent-dispatch task ${taskId} (proposed, label ` +
              `'handoff', pinned to this worktree). No file handoff was written.\n\n` +
              `PRIMARY PATH -- live cutover (no copy/paste): call continue_handoff ` +
              `with \`seed\` = the HANDOFF_SEED below to spin up the successor in ` +
              `place and hand off automatically. Only if that reports no live ` +
              `pane host (graceful fallback) do you reply to the user ` +
              `with ONLY this short paste prompt (they resume via /resume-handoff ` +
              `or by pasting into /clear):\n` +
              `  ${seed}\n` +
              `Do NOT paste the handoff contents -- the payload lives in the task ` +
              `and is loaded on demand by the embedded command. In a live cutover, ` +
              `the successor loads it and retires the predecessor after pickup.`
            );
          }
          // Task creation failed -- fall through to the file flow.
        }

        if (!seed) {
          const fileStored = saveFileHandoff(text, sid, cwd, title);
          if (!fileStored?.path) {
            return (
              "Cannot save handoff: the machine-local file resolver failed. " +
              "Nothing was written. " +
              (fileStored?.error || "No safe machine-local state directory resolved.")
            );
          }
          seed = buildCutoverSeed("file", fileStored.id, lead, {
            retry: false,
            path: fileStored.path,
          });
          cutoverSeed = buildCutoverSeed("file", fileStored.id, lead, {
            path: fileStored.path,
          });
          // Mirror the handoff into the worktree record (record-first recovery).
          noteHandoffInRecord(cwd, sid, fileStored.id, title);
          const fileReason = isHerdrPane()
            ? "because Herdr live handoff uses context-handoff's own checkout-scoped baton"
            : "because no reachable agent-dispatch coordinator was available";
          storedMsg = (
            `Handoff saved to ${fileStored.path}\n\n` +
            `(Stored as a checkout-scoped handoff file outside the repo checkout ` +
            `${fileReason}.) ` +
            `Reply to the user with ONLY this short paste prompt if live cutover ` +
            `is unavailable:\n` +
            `  ${seed}\n` +
            `Do NOT paste the file's contents, and do NOT claim it loads ` +
            `automatically on restart -- the consume_handoff tool marks it spent.`
          );
        }

        // The HANDOFF_SEED line is the machine-readable seed for a LIVE cutover
        // (the PRIMARY handoff path): call continue_handoff with `seed` set to
        // exactly this string. For a task-backed handoff it is the *deferred*
        // cutover seed (the successor completes explicitly at the goal).
        return (
          `${storedMsg}\n\n` +
          `HANDOFF_SEED: ${cutoverSeed}\n` +
          `(Live cutover is the PRIMARY path: call continue_handoff with \`seed\` ` +
          `set to exactly the HANDOFF_SEED string above. Only fall back to the ` +
          `short paste prompt if continue_handoff reports no live pane host.)`
        );
      },
    },
    {
      name: "consume_handoff",
      description:
        "Consume a stored context handoff exactly once. For agent-dispatch " +
        "handoffs, pass task_id; for file-backed handoffs, pass handoff_id " +
        "(or path). The tool loads the handoff, marks file-backed handoffs " +
        "consumed so they do not replay, and retires a recorded mux predecessor " +
        "after the successor is alive. A Herdr predecessor remains the recovery " +
        "pane.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          task_id: {
            type: "string",
            description: "agent-dispatch task id for a task-backed handoff.",
          },
          handoff_id: {
            type: "string",
            description: "File-backed handoff id, e.g. handoff-<session-id>.",
          },
          path: {
            type: "string",
            description: "Explicit file-backed handoff JSON path.",
          },
          defer_complete: {
            type: "boolean",
            description:
              "For task-backed handoffs, consume with --defer-complete so the " +
              "successor completes the task only when the handoff goal is reached.",
          },
        },
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        const cwdResult = currentHandoffCwd();
        if (!cwdResult.cwd) {
          return (
            "Cannot consume handoff: the current Herdr pane working directory " +
            `could not be resolved. [host: ${cwdResult.error}]`
          );
        }
        const cwd = cwdResult.cwd;
        const sid = state.sessionId || invocation?.sessionId || null;
        const taskId = (args?.task_id ?? "").toString().trim();
        const handoffId = (args?.handoff_id ?? "").toString().trim();
        const path = (args?.path ?? "").toString().trim();
        const deferComplete = Boolean(args?.defer_complete);

        let result;
        if (taskId) {
          result = consumeDispatchHandoffTask(cwd, taskId, sid, deferComplete);
        } else if (handoffId || path) {
          result = consumeFileHandoff(cwd, sid, handoffId, path || null);
        } else {
          return (
            "Cannot consume handoff: pass task_id for an agent-dispatch handoff " +
            "or handoff_id/path for a file-backed handoff."
          );
        }

        return {
          textResultForLlm: formatConsumeResult(result, { deferComplete }),
          resultType: result?.ok ? "success" : "error",
        };
      },
    },
    {
      name: "continue_handoff",
      description:
        "Live-cutover the CURRENT session to a seeded successor. Call this AFTER " +
        "save_handoff_prompt (the explicit 'kick the flow' step of a live " +
        "handoff): pass `seed` = the exact HANDOFF_SEED string save_handoff_prompt " +
        "returned. In an active Herdr pane it calls the installed copilot-pane " +
        "launcher with a task file to create one seeded sibling and keeps this " +
        "predecessor as the recovery point. Otherwise it uses the existing " +
        "worktree mux cutover, where successor pickup retires the predecessor. " +
        "If launch fails, it does nothing destructive and the handoff remains " +
        "safely stored.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          seed: {
            type: "string",
            description:
              "The successor's first interactive prompt -- pass the exact " +
              "HANDOFF_SEED string returned by save_handoff_prompt.",
          },
        },
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        const seed = (args?.seed ?? "").toString().trim();
        if (!seed) {
          return (
            "Cannot continue handoff: pass the HANDOFF_SEED string returned by " +
            "save_handoff_prompt as `seed`. Nothing was done. (Call " +
            "save_handoff_prompt first to store the handoff and get the seed.)"
          );
        }
        const cwdResult = currentHandoffCwd();
        if (!cwdResult.cwd) {
          return (
            "Cannot continue handoff: the current Herdr pane working directory " +
            `could not be resolved. Nothing was done. [host: ${cwdResult.error}]`
          );
        }
        const cwd = cwdResult.cwd;
        const sid = state.sessionId || invocation?.sessionId || null;
        const result = runHandoffCutover(cwd, seed, sid);
        if (!result || !result.ok) {
          const reason = result?.reason || "error";
          const tail =
            " Nothing destructive was done. The handoff is safely stored -- " +
            "resume it the normal way (paste the reply prompt into '/clear', or " +
            "run /resume-handoff in a fresh session in this worktree).";
          if (result?.host === "herdr") {
            return (
              "Live cutover is unavailable: copilot-pane could not launch the " +
              "seeded sibling from this Herdr pane." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          if (reason === "no-worktree") {
            // The common bare-resume case: the process IS inside the wt-<id>
            // mux, but Copilot was launched with its cwd at HOME (e.g. a "Bare
            // resume", or the #1416 HOME-cwd binding), so the host verb could
            // not resolve WHICH worktree from cwd -- not a genuine "no mux".
            return (
              "Live cutover is unavailable: could not determine which worktree " +
              "this session belongs to from its working directory (it looks like " +
              "a bare/HOME-cwd resume). The session may well be inside a mux, but " +
              "the cutover needs the worktree checkout as its cwd. `cd` into this " +
              "worktree's directory and try the handoff again, or resume it the " +
              "normal way." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          if (reason === "no-mux") {
            return (
              "Live cutover is unavailable: this session is not running under a " +
              "mux session (no live wt-<id> session to cut into)." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          return (
            "Live cutover is unavailable: the cutover verb failed." +
            tail +
            (result?.error ? ` [host: ${result.error}]` : "")
          );
        }
        if (result.host === "herdr") {
          return (
            `Live cutover initiated through Herdr. A successor Copilot was ` +
            `created in sibling pane ${result.new_pane || "?"} and its first ` +
            `prompt was submitted to consume the saved handoff. The predecessor ` +
            `pane remains available as the recovery point; Herdr retirement is ` +
            `not inferred or attempted. Do NOT start new work here; simply end ` +
            `your turn.`
          );
        }
        return (
          `Live cutover initiated. A successor Copilot was spawned in a new window ` +
          `of this worktree's mux session (pane ${result.new_pane || "?"}) and ` +
          `seeded to consume the handoff; the operator has been cut over to it. ` +
          `The predecessor pane will remain available unless and until the ` +
          `successor consumes the handoff and retires it. Do NOT start new work ` +
          `here; simply end your turn. (If the successor window comes up EMPTY -- ` +
          `no session created because its first prompt was never submitted -- ` +
          `call retry_handoff_cutover from here to re-attempt from the same saved ` +
          `handoff, without regenerating it.)`
        );
      },
    },
    {
      name: "retry_handoff_cutover",
      description:
        "Re-attempt a live cutover using the ALREADY-SAVED handoff, WITHOUT " +
        "regenerating or revising it. Use when a prior continue_handoff spawned a " +
        "successor window but no session was created -- the window came up " +
        "'empty' because Copilot never received a submitted first prompt, so no " +
        "sessionStart fired, no changeover was recorded, and the predecessor is " +
        "still live (closing the empty window drops you back here). This tool " +
        "recovers this checkout's stored handoff (agent-dispatch task, else " +
        "machine-local file), rebuilds the exact same cutover seed, and spawns a " +
        "fresh seeded successor through the active Herdr or mux host. Run it from " +
        "the predecessor (the pane you " +
        "land on after closing the empty window). Takes no arguments.",
      skipPermission: true,
      parameters: { type: "object", properties: {} },
      handler: async (args, invocation) => {
        void args;
        ensureState(invocation);
        const cwdResult = currentHandoffCwd();
        if (!cwdResult.cwd) {
          return (
            "Cannot retry the cutover: the current Herdr pane working directory " +
            `could not be resolved. Nothing was done. [host: ${cwdResult.error}]`
          );
        }
        const cwd = cwdResult.cwd;
        const sid = state.sessionId || invocation?.sessionId || null;

        // Recover the saved handoff (task preferred, else file) and rebuild the
        // EXACT cutover seed via the shared builder -- no regeneration.
        let kind = null;
        let id = null;
        let filePath = null;
        let lead = leadFrom("");
        const wtDir = isHerdrPane()
          ? null
          : agentWorktreesGet("worktree-dir", cwd, sid);
        const worktree = wtDir ? basename(wtDir) : null;
        if (worktree) {
          const task = findHandoffTask(cwd, worktree);
          if (task?.id) {
            kind = "task";
            id = task.id;
            lead = leadFrom(task.title || task.name || "");
          }
        }
        if (!id) {
          const file = findHandoffFile(cwd, sid);
          if (file?.record?.id) {
            kind = "file";
            id = file.record.id;
            filePath = file.path;
            lead = leadFrom(file.record.title || "");
          }
        }
        if (!id) {
          return (
            "Cannot retry the cutover: no saved handoff was found for this " +
            "checkout (no pending handoff task or unconsumed machine-local file). " +
            "If you have not saved " +
            "one yet, run save_handoff_prompt first -- there is nothing to " +
            "re-attempt."
          );
        }

        const seed = buildCutoverSeed(
          kind, id, lead,
          kind === "task"
            ? {
                oldPane: currentPaneId(),
                worktree,
                worktreeDir: wtDir,
                sessionId: sid,
                muxSession: currentMuxSession(),
              }
            : { path: filePath },
        );
        const result = runHandoffCutover(cwd, seed, sid);
        if (!result || !result.ok) {
          const reason = result?.reason || "error";
          const tail =
            " Nothing destructive was done; the saved handoff is untouched. " +
            "Resume it the normal way (/resume-handoff in a fresh session in this " +
            "worktree, or paste the reply prompt into /clear).";
          if (result?.host === "herdr") {
            return (
              "Cannot retry the cutover: copilot-pane could not launch a fresh " +
              "seeded sibling from this Herdr pane." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          if (reason === "no-worktree") {
            return (
              "Cannot retry the cutover: could not determine which worktree this " +
              "session belongs to from its working directory (it looks like a " +
              "bare/HOME-cwd resume). `cd` into this worktree's checkout and try " +
              "again." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          if (reason === "no-mux") {
            return (
              "Cannot retry the cutover: this session is not running under a mux " +
              "session (no live wt-<id> to cut into)." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          return (
            "Cannot retry the cutover: the cutover verb failed." +
            tail +
            (result?.error ? ` [host: ${result.error}]` : "")
          );
        }
        const src =
          kind === "task" ? `agent-dispatch task ${id}` : `handoff file ${id}`;
        if (result.host === "herdr") {
          return (
            `Cutover re-attempted through Herdr from the saved handoff (${src}). ` +
            `A fresh successor Copilot was created in sibling pane ` +
            `${result.new_pane || "?"} and its first prompt was submitted. This ` +
            `predecessor remains the recovery point; no Herdr pane retirement is ` +
            `attempted. Do NOT start new work here; end your turn.`
          );
        }
        return (
          `Cutover re-attempted from the saved handoff (${src}). A fresh ` +
          `successor Copilot was spawned in a new window of this worktree's mux ` +
          `session (pane ${result.new_pane || "?"}) and seeded to consume the ` +
          `handoff. If a previous EMPTY successor window is still open (one that ` +
          `never created a session), close it -- it holds no session and nothing ` +
          `is lost. Do NOT start new work here; end your turn -- this predecessor ` +
          `remains a recovery point until the successor consumes the handoff and ` +
          `retires it.`
        );
      },
    },
  ],

  commands: [
    {
      name: "handoff-continue",
      description:
        "Live-cutover handoff: generate a handoff for THIS session, spawn a " +
        "seeded successor Copilot through the active Herdr or mux pane host, and " +
        "preserve the predecessor until the selected host's safe lifecycle point.",
      handler: async (ctx) => {
        void ctx;
        await session.send({
          prompt:
            "Perform a LIVE-CUTOVER handoff now (the operator invoked " +
            "/handoff-continue). Steps: (1) call generate_handoff_prompt to " +
            "collect session facts; (2) compose continuation markdown per the " +
            "context-handoff skill -- use its compact effort-backed shape when " +
            "a valid open active effort exists, otherwise the full standalone " +
            "shape; (3) call save_handoff_prompt with that markdown as " +
            "`prompt_text` and a short specific `title` -- it stores the handoff " +
            "and returns a HANDOFF_SEED line; (4) call continue_handoff with " +
            "`seed` set to EXACTLY that HANDOFF_SEED string -- it spawns the " +
            "seeded successor Copilot through the active Herdr or worktree mux " +
            "host. After continue_handoff returns " +
            "its confirmation, DO NOT start new work -- just end your turn; this " +
            "session remains as a recovery point. A mux successor retires it only " +
            "after pickup; a Herdr predecessor is retained.",
          displayPrompt: "Live-cutover handoff (/handoff-continue)",
        });
      },
    },
    {
      name: "resume-handoff",
      description:
        "Dig up this checkout's pending handoff and inject its continuation " +
        "prompt into THIS session (foreground). Consumes the agent-dispatch " +
        "handoff task if present, else the newest matching machine-local file.",
      handler: async (ctx) => {
        const cwdResult = currentHandoffCwd();
        if (!cwdResult.cwd) {
          await session.log(
            `[Context Handoff] Cannot resume: ${cwdResult.error}`,
            { level: "error" },
          );
          return;
        }
        const cwd = cwdResult.cwd;
        const sid = state.sessionId || ctx?.sessionId || "unknown";

        // Prefer an agent-dispatch handoff task pinned to this worktree.
        if (!isHerdrPane() && agentDispatchAvailable()) {
          // Binding-first (#4098): pass the real session id (not the "unknown"
          // sentinel) so a bare-resumed session (cwd=HOME) still resolves its
          // worktree from the session binding.
          const bindSid = sid && sid !== "unknown" ? sid : null;
          const wtDir = agentWorktreesGet("worktree-dir", cwd, bindSid);
          const worktree = wtDir ? basename(wtDir) : null;
          if (worktree) {
            const task = findHandoffTask(cwd, worktree);
            if (task) {
              const consumed = consumeDispatchHandoffTask(
                cwd, task.id, sid, true, { deferRetire: true },
              );
              const body = consumed?.payload || "";
              if (!consumed?.ok || !body) {
                await session.log(
                  `Found handoff task ${task.id.slice(0, 8)} but could not claim ` +
                    "and load it. Nothing was injected or retired.",
                  { level: "warning" },
                );
                return;
              }
              try {
                await session.send({
                  prompt: buildResumePrompt(body, "agent-dispatch task"),
                  displayPrompt: `Resuming handoff ${task.id.slice(0, 8)} from agent-dispatch`,
                });
              } catch (error) {
                try {
                  runCli("agent-dispatch", [
                    "yield", task.id, "--note",
                    "handoff prompt injection failed; returning baton for retry",
                  ], { cwd, timeout: 15000 });
                } catch { /* task remains visibly held for recovery */ }
                await session.log(
                  `Claimed handoff task ${task.id.slice(0, 8)}, but prompt ` +
                    "injection failed. The predecessor was not retired; the " +
                    "baton was returned for retry when possible.",
                  { level: "warning" },
                );
                return;
              }
              try {
                runCli("agent-dispatch", ["complete", task.id], {
                  cwd, timeout: 15000,
                });
              } catch { /* payload was delivered; task cleanup is recoverable */ }
              retireAfterConsume(cwd, consumed.metadata, sid, task.id);
              return;
            }
          }
        }

        // Fallback: the newest checkout-scoped machine-local handoff file.
        const file = findHandoffFile(cwd, sid);
        if (file) {
          const consumed = consumeFileHandoff(
            cwd, sid, file.record.id, file.path, { deferRetire: true },
          );
          if (!consumed?.ok) {
            await session.log(
              consumed?.message || "Found a handoff file but could not consume it.",
              { level: "warning" },
            );
            return;
          }
          await session.send({
            prompt: buildResumePrompt(consumed.payload, `file ${file.path}`),
            displayPrompt: `Resuming handoff ${consumed.id || basename(file.path)}`,
          });
          retireAfterConsume(cwd, consumed.metadata, sid, consumed.id);
          return;
        }

        await session.log(
          "No pending handoff found for this worktree (no agent-dispatch task " +
            "and no matching worktree handoff file). If you have a handoff prompt, paste it directly.",
          { level: "warning" },
        );
      },
    },
  ],
});

// --- Session lifecycle reconstructed from events (SDK callback hooks removed) ---
// The native runtime dropped SDK callback hooks ("SDK hook callbacks are no
// longer supported by the native runtime"), which hard-failed joinSession when
// a `hooks` object was passed. The former onSessionStart / onUserPromptSubmitted
// / onPostToolUse behaviours are reconstructed below from observable session
// events. This top-level code runs on every import of the module -- and the
// module is (re)imported MULTIPLE times per session: the runtime forks a
// discovery pass plus the real join at startup, and re-forks on
// reconnect/resume. So session-start work here must be idempotent.
//
// NOTE: no user-visible "Session started" breadcrumb is emitted here. session.log
// surfaces to the chat UI, and a single such notification was observed being
// re-painted indefinitely by the CLI's notification renderer (dotfiles#447),
// flooding the UI. The extension's launch is already recorded per-fork in its
// own extension launch log, so nothing is lost by staying silent in the UI.
state.sessionId = session.sessionId ?? state.sessionId ?? null;
state.cwd = state.cwd || process.cwd();
state.turnCount = 0;
if (handoffConfig.warning) {
  session.log(`[Context Handoff] ${handoffConfig.warning}`, { level: "warning" });
}

// Turn counting + first-prompt capture (replaces onUserPromptSubmitted).
session.on("user.message", (event) => {
  state.turnCount++;
  if (!state.firstUserPrompt && event.data?.content) {
    state.firstUserPrompt = event.data.content;
  }
});

// File / tool-invocation tracking (replaces onPostToolUse's bookkeeping).
// tool.execution_complete carries the success flag but NOT the call
// arguments, so the args are stashed from tool.execution_start (keyed by
// toolCallId) and committed on a successful completion -- matching the old
// hook, which ran for successful tool calls only.
const pendingToolArgs = new Map();  // toolCallId -> { toolName, arguments }

session.on("tool.execution_start", (event) => {
  const d = event.data;
  if (!d?.toolCallId) return;
  pendingToolArgs.set(d.toolCallId, {
    toolName: d.toolName,
    arguments: d.arguments || {},
  });
  // Bound the map in case a completion event is ever missed.
  if (pendingToolArgs.size > 200) {
    pendingToolArgs.delete(pendingToolArgs.keys().next().value);
  }
});

session.on("tool.execution_complete", (event) => {
  const d = event.data;
  const pend = d?.toolCallId ? pendingToolArgs.get(d.toolCallId) : null;
  if (d?.toolCallId) pendingToolArgs.delete(d.toolCallId);
  if (!d?.success) return;  // old onPostToolUse fired for successes only

  const toolName = pend?.toolName || d.toolDescription?.name;
  const toolArgs = pend?.arguments || {};
  if (!toolName) return;

  // Track file modifications
  if ((toolName === "edit" || toolName === "create") && toolArgs?.path) {
    state.filesModified.set(toolArgs.path, {
      tool: toolName,
      turnIndex: state.turnCount,
    });
  }

  // Track notable tool invocations (skip high-frequency read-only tools)
  const skipTools = new Set(["view", "glob", "grep", "report_intent", "sql", "session_store_sql"]);
  if (!skipTools.has(toolName)) {
    const summary = toolName === "edit" || toolName === "create"
      ? toolArgs?.path || ""
      : toolName === "powershell" || toolName === "bash"
        ? (String(toolArgs?.description || toolArgs?.command || "")).slice(0, 80)
        : toolName === "task"
          ? `${toolArgs?.agent_type || ""}: ${(toolArgs?.description || "").slice(0, 60)}`
          : JSON.stringify(toolArgs || {}).slice(0, 80);

    state.toolInvocations.push({
      tool: toolName,
      turn: state.turnCount,
      summary,
    });

    // Cap at 50 entries to avoid unbounded growth
    if (state.toolInvocations.length > 50) {
      state.toolInvocations = state.toolInvocations.slice(-30);
    }
  }
});

// Agent-facing context-pressure nudge (replaces the onPostToolUse
// additionalContext return value, which the native runtime no longer
// supports). session.on handlers are observe-only, so the reminder is queued
// in the session.usage_info handler and delivered here as a real user-turn
// message via session.send() on the next idle boundary -- the agent sees and
// can act on it, exactly as the injected additionalContext used to allow.
// Guarded by the once-only softReminderSent / hardReminderSent flags (reset
// on compaction). session.send() inside an idle handler does not loop: the
// queue is cleared before sending and the guard flags prevent re-queueing.
let pendingNudge = null;  // null | "soft" | "hard"

session.on("session.idle", () => {
  if (!pendingNudge) return;
  const level = pendingNudge;
  pendingNudge = null;
  const usage = formatContextUsage(state.currentTokens, state.tokenLimit);
  // The nudge JUST hands the agent to the context-handoff skill -- it does NOT
  // prescribe individual tool calls (generate_handoff_prompt/save_handoff_prompt/
  // continue_handoff) or a "write a file" outcome. The skill owns the sequencing;
  // under Herdr or mux that means the autonomous live cutover (spin up a
  // successor Copilot in place, end the turn), not a paste prompt.
  const msg = level === "hard"
    ? `[Context Handoff -- automated] Context utilization is ${usage.utilization} ` +
      `(${usage.tokens}). ` +
      `The configured hard threshold was reached; auto-compaction still triggers ` +
      `at ~80%. Invoke the context-handoff skill now to ` +
      `hand off before context is lost -- under Herdr or mux it cuts over to a ` +
      `fresh successor Copilot in place, automatically (no copy/paste); otherwise ` +
      `it stores the handoff and hands you a short resume prompt.`
    : `[Context Handoff -- automated] Context utilization is ${usage.utilization} ` +
      `(${usage.tokens}). ` +
      `The configured soft threshold was reached. Invoke the context-handoff skill ` +
      `at the next clean boundary -- under Herdr or mux it cuts over to a fresh ` +
      `successor Copilot in place.`;
  session.send(msg).catch((e) =>
    session.log(`[Context Handoff] nudge send failed: ${e.message}`, { level: "warning" })
  );
});

// --- Real-time context utilization monitoring ---
// The session.usage_info event fires with exact token counts after each
// model interaction. This is the authoritative signal for context usage.

session.on("session.usage_info", (event) => {
  const d = event.data;
  state.currentTokens = d.currentTokens;
  state.tokenLimit = d.tokenLimit;
  state.conversationTokens = d.conversationTokens ?? 0;
  state.systemTokens = d.systemTokens ?? 0;
  state.toolDefinitionsTokens = d.toolDefinitionsTokens ?? 0;
  state.messagesLength = d.messagesLength;
  state.lastUtilization = d.tokenLimit > 0 ? d.currentTokens / d.tokenLimit : 0;

  const pressure = contextPressure(
    d.currentTokens,
    d.tokenLimit,
    handoffConfig.thresholds,
  );
  const usage = formatContextUsage(d.currentTokens, d.tokenLimit);

  // Queue an agent-facing nudge once per threshold, delivered on the next
  // idle via session.send() (see the session.idle handler above). This is the
  // agent-visible counterpart to the user-visible logs below.
  if (pressure.hard &&
      !state.hardReminderSent && !state.handoffGenerated) {
    state.hardReminderSent = true;
    state.softReminderSent = true;  // hard implies soft
    pendingNudge = "hard";
  } else if (pressure.soft &&
      !state.softReminderSent && !state.handoffGenerated) {
    state.softReminderSent = true;
    pendingNudge = "soft";
  }

  // Soft reminder at threshold (user-visible log only -- agent nudged via session.send on idle)
  if (pressure.soft &&
      !state.softLogShown && !state.handoffGenerated) {
    state.softLogShown = true;
    session.log(
      `[Context Handoff] Context utilization ${usage.utilization} ` +
      `(${usage.tokens}; ` +
      `conversation ${(d.conversationTokens ?? 0).toLocaleString()}, ` +
      `system ${(d.systemTokens ?? 0).toLocaleString()}, ` +
      `tool-defs ${(d.toolDefinitionsTokens ?? 0).toLocaleString()}). ` +
      `Configured ${pressure.softPercent}% threshold ` +
      `${Math.round(pressure.softThreshold).toLocaleString()} ` +
      `tokens reached. Hand off at the next clean boundary ` +
      `(invoke the context-handoff skill).`,
      { level: "warning" }
    );
  }

  // Hard reminder at threshold (user-visible log only -- agent nudged via session.send on idle)
  if (pressure.hard &&
      !state.hardLogShown && !state.handoffGenerated) {
    state.hardLogShown = true;
    state.softLogShown = true;  // hard implies soft
    session.log(
      `[Context Handoff] ⚠️ Context utilization ${usage.utilization} ` +
      `(${usage.tokens}; ` +
      `conversation ${(d.conversationTokens ?? 0).toLocaleString()}, ` +
      `system ${(d.systemTokens ?? 0).toLocaleString()}, ` +
      `tool-defs ${(d.toolDefinitionsTokens ?? 0).toLocaleString()}). ` +
      `Configured ${pressure.hardPercent}% hard threshold ` +
      `${Math.round(pressure.hardThreshold).toLocaleString()} ` +
      `tokens reached; auto-compaction still triggers at ~80%. ` +
      `Hand off NOW -- invoke the context-handoff skill.`,
      { level: "warning" }
    );
  }

  persistState();
});

// Also monitor compaction events for awareness
session.on("session.compaction_start", (event) => {
  session.log(
    `[Context Handoff] Compaction starting. ` +
    `Conversation tokens: ${event.data.conversationTokens?.toLocaleString() ?? "?"}, ` +
    `System tokens: ${event.data.systemTokens?.toLocaleString() ?? "?"}`,
    { level: "warning" }
  );
});

session.on("session.compaction_complete", (event) => {
  const d = event.data;
  if (d.success) {
    // Reset reminder state after successful compaction — utilization
    // will be much lower now, so future reminders should fire fresh
    state.softReminderSent = false;
    state.hardReminderSent = false;
    state.softLogShown = false;
    state.hardLogShown = false;
    session.log(
      `[Context Handoff] Compaction complete. ` +
      `${d.tokensRemoved?.toLocaleString() ?? "?"} tokens removed, ` +
      `${d.postCompactionTokens?.toLocaleString() ?? "?"} tokens remaining.`
    );
  }
});
