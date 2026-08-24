// handoff-core.mjs -- SDK-free store/trigger core for context handoffs.
//
// Everything the handoff store + live-cutover trigger needs, WITHOUT importing
// the Copilot SDK or the session extension. This is the same rationale that
// extracted `cutover-seed.mjs` (the seed builders): keep the load-bearing logic
// importable from anywhere -- a unit test, the clean-room, and (the reason this
// module exists) a standalone CLI (`handoff-cli.mjs`) an agent can invoke
// DIRECTLY when the context-handoff extension does not resolve or fails to load
// (e.g. a Bare-resumed session where no extensions load at all).
//
// The functions here are ported faithfully from extension.mjs's store/trigger
// helpers. They shell out to the SAME `agent-worktrees` / `agent-dispatch`
// binstubs and write the SAME on-disk handoff format, so a handoff stored via
// this core is byte-compatible with the extension's `consume_handoff` /
// `/resume-handoff` path. (Follow-up: extension.mjs should import these from
// here to retire the duplicate definitions, under the node --test + clean-room
// context-handoff-cutover guardrails.)

import {
  writeFileSync, readFileSync, existsSync, mkdirSync, renameSync, unlinkSync,
} from "node:fs";
import { join, basename } from "node:path";
import { execSync, execFileSync } from "node:child_process";
import { leadFrom, buildCutoverSeed } from "./cutover-seed.mjs";
import { supersededHandoffIds } from "./handoff-tasks.mjs";

export const HANDOFF_META_PREFIX = "<!-- context-handoff:";
export const HANDOFF_META_SUFFIX = "-->";

// --- cross-platform system-CLI invocation ---------------------------------
// On Windows the agent-worktrees / agent-dispatch binstubs are `.cmd` files,
// which Node's execFileSync CANNOT spawn directly (no shell -> ENOENT). So on
// win32 go through the shell with each arg quoted for cmd.exe; elsewhere
// execFileSync is exact + injection-safe. Every agent-worktrees/agent-dispatch
// call MUST go through runCli.
export function quoteWinArg(s) {
  s = String(s);
  return /[\s"&|<>^()%!]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
export function runCli(bin, args, opts = {}) {
  const { cwd, timeout = 15000 } = opts;
  if (process.platform === "win32") {
    const line = [bin, ...args].map(quoteWinArg).join(" ");
    return execSync(line, { cwd, timeout, encoding: "utf-8" });
  }
  return execFileSync(bin, args, { cwd, timeout, encoding: "utf-8" });
}

// True if an agent-dispatch coordinator answers a health probe.
export function agentDispatchAvailable() {
  try {
    execSync("agent-dispatch health", { timeout: 5000, stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

// Resolve an agent-worktrees identity value (null on miss). A sessionId is
// passed binding-first so a bare-resumed session (cwd=HOME) still resolves its
// worktree from the session->worktree binding rather than the HOME cwd.
export function agentWorktreesGet(key, cwd, sessionId) {
  try {
    const argv = ["get", key];
    if (sessionId) argv.push("--session-id", sessionId);
    const out = runCli("agent-worktrees", argv, { cwd, timeout: 5000 }).trim();
    return out || null;
  } catch {
    return null;
  }
}

export function safePathSegment(value) {
  return String(value || "unknown")
    .replace(/[^A-Za-z0-9._-]/g, "_")
    .slice(0, 160) || "unknown";
}

export function currentPaneId() {
  return process.env.TMUX_PANE || process.env.PSMUX_PANE || null;
}

export function worktreeInfo(cwd, sid) {
  const wtDir = agentWorktreesGet("worktree-dir", cwd, sid);
  const worktree = wtDir ? basename(wtDir) : null;
  const stateDir = agentWorktreesGet("worktree-state-dir", cwd, sid);
  return { wtDir, worktree, stateDir };
}

export function makeHandoffMetadata({ sid, cwd, title, storage, taskId = null }) {
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
    stateDir,
    createdAt: new Date().toISOString(),
  };
}

export function encodeHandoffPayload(promptText, metadata) {
  return `${HANDOFF_META_PREFIX} ${JSON.stringify(metadata)} ${HANDOFF_META_SUFFIX}\n${promptText}`;
}

export function decodeHandoffPayload(raw) {
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

export function handoffDirFor(cwd, sid) {
  const { stateDir } = worktreeInfo(cwd, sid);
  return stateDir ? join(stateDir, "handoff") : null;
}

export function writeJsonAtomic(path, value) {
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify(value, null, 2), "utf-8");
  renameSync(tmp, path);
}

// --- file-backed store ----------------------------------------------------
export function saveFileHandoff(promptText, sid, cwd, title) {
  const metadata = makeHandoffMetadata({ sid, cwd, title, storage: "file" });
  const dir = handoffDirFor(cwd, sid);
  if (!dir) return null;
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${metadata.id}.json`);
  writeJsonAtomic(path, { ...metadata, consumed: false, consumedAt: null, promptText });
  return { path, id: metadata.id, metadata };
}

export function readFileHandoff(cwd, sid, handoffId, explicitPath = null) {
  const path = explicitPath || (() => {
    const dir = handoffDirFor(cwd, sid);
    return dir ? join(dir, `${safePathSegment(handoffId)}.json`) : null;
  })();
  if (!path || !existsSync(path)) return null;
  try {
    return { path, record: JSON.parse(readFileSync(path, "utf-8")) };
  } catch {
    return null;
  }
}

export function markFileHandoffConsumed(path, record, sid) {
  const consumed = {
    ...record,
    consumed: true,
    consumedAt: record.consumedAt || new Date().toISOString(),
    consumedBySession: sid || record.consumedBySession || null,
  };
  writeJsonAtomic(path, consumed);
  return consumed;
}

// --- agent-dispatch task store --------------------------------------------
export function agentDispatchJson(argv, cwd) {
  try {
    return JSON.parse(runCli("agent-dispatch", argv, { cwd, timeout: 15000 }));
  } catch {
    return null;
  }
}

export function abandonSupersededHandoffs(cwd, worktree, keepId) {
  const tasks = agentDispatchJson(
    ["list", "--status", "proposed,queued", "--label", "handoff"], cwd,
  );
  for (const id of supersededHandoffIds(tasks, worktree, keepId)) {
    try {
      runCli("agent-dispatch",
        ["abandon", id, "--permit", "--reason", "superseded by a newer handoff for this worktree"],
        { cwd, timeout: 15000 });
    } catch { /* best-effort -- GC orphan pass is the backstop */ }
  }
}

export function dispatchHandoff(promptText, sid, cwd, title) {
  const metadata = makeHandoffMetadata({ sid, cwd, title, storage: "agent-dispatch" });
  const dir = handoffDirFor(cwd, sid);
  if (!dir) return null;
  const tmp = join(dir, `${metadata.id}-payload-${process.pid}.md`);
  try {
    mkdirSync(dir, { recursive: true });
    writeFileSync(tmp, encodeHandoffPayload(promptText, metadata), "utf-8");
    const machine = agentWorktreesGet("machine", cwd, sid);
    const wtDir = agentWorktreesGet("worktree-dir", cwd, sid);
    const worktree = wtDir ? basename(wtDir) : null;
    // A handoff must land in its OWN worktree; without one, bail to the file
    // flow rather than file an unpinned, anyone-can-claim task.
    if (!worktree) return null;
    const argv = [
      "create", title || "Handoff: continue this session",
      "--proposed", "--label", "handoff", "--source", "context-handoff",
      "--dedup-key", `handoff-${sid}`, "--payload-file", tmp,
      "--target-worktree", worktree, "--affinity", `worktree=${worktree}`,
    ];
    if (machine) argv.push("--target-machine", machine);
    const task = JSON.parse(runCli("agent-dispatch", argv, { cwd, timeout: 15000 }));
    if (task?.id) abandonSupersededHandoffs(cwd, worktree, task.id);
    return task?.id ? { id: task.id, metadata: { ...metadata, taskId: task.id } } : null;
  } catch {
    return null;
  } finally {
    try { unlinkSync(tmp); } catch { /* already gone */ }
  }
}

// Mirror the stored handoff into the worktree's own record (best-effort).
export function noteHandoffInRecord(cwd, sid, ref, title) {
  try {
    const argv = ["note-handoff"];
    if (ref) argv.push("--task", ref);
    if (title) argv.push("--title", title);
    if (sid) argv.push("--session-id", sid);
    runCli("agent-worktrees", argv, { cwd, timeout: 5000 });
  } catch { /* history is advisory */ }
}

// --- live-cutover trigger -------------------------------------------------
// The mux choreography itself lives in `agent-worktrees handoff-cutover`; this
// is the thin trigger. Returns:
//   { ok: true, old_pane, new_pane }
//   { ok: false, reason: "no-worktree" | "no-mux" | "error", error }
export function runHandoffCutover(cwd, seed, sessionId) {
  const argv = ["handoff-cutover", "--seed", seed];
  const ownPane = process.env.TMUX_PANE || process.env.PSMUX_PANE || "";
  if (ownPane) argv.push("--old-pane", ownPane);
  if (sessionId) argv.push("--session-id", sessionId);
  try {
    const result = JSON.parse(runCli("agent-worktrees", argv, { cwd, timeout: 20000 }));
    return result?.ok ? result : { ok: false, reason: "error", error: null };
  } catch (e) {
    const status = typeof e?.status === "number" ? e.status : null;
    let error = null;
    try {
      const stdout = (e?.stdout || "").toString();
      const parsed = stdout ? JSON.parse(stdout) : null;
      error = parsed?.error || null;
    } catch { /* stdout was not JSON */ }
    const reason = status === 2 ? "no-worktree" : status === 3 ? "no-mux" : "error";
    return { ok: false, reason, error };
  }
}

// --- high-level orchestration (what the CLI + extension both want) ---------

// Store a handoff, preferring an agent-dispatch task (durable/browsable) when a
// coordinator is reachable, else a one-time worktree-state file. Mirrors the
// extension's save_handoff_prompt store selection. Returns:
//   { storage: "agent-dispatch"|"file", id, taskId?, path?, metadata }
// or null when neither store could be written (no resolvable worktree).
export function storeHandoff({ promptText, sid, cwd, title, preferTask = true }) {
  if (preferTask && agentDispatchAvailable()) {
    const task = dispatchHandoff(promptText, sid, cwd, title);
    if (task) {
      noteHandoffInRecord(cwd, sid, task.id, title);
      return { storage: "agent-dispatch", id: task.id, taskId: task.id, metadata: task.metadata };
    }
  }
  const file = saveFileHandoff(promptText, sid, cwd, title);
  if (!file) return null;
  noteHandoffInRecord(cwd, sid, file.id, title);
  return { storage: "file", id: file.id, path: file.path, metadata: file.metadata };
}

// Compose the cutover seed for a stored handoff. `retry` false -> the short
// human paste prompt; true -> the successor seed (bash-first when the pane /
// worktree / session are known -- GitHub issue #853).
export function buildSeedForStored(stored, { retry = true } = {}) {
  const md = stored.metadata || {};
  const kind = stored.storage === "agent-dispatch" ? "task" : "file";
  const lead = leadFrom(md.title);
  return buildCutoverSeed(kind, stored.id, lead, {
    retry,
    oldPane: md.oldPane || null,
    worktree: md.worktree || null,
    sessionId: md.sessionId || null,
  });
}

// Store + build seed + (optionally) trigger the live cutover in one call --
// the standalone equivalent of save_handoff_prompt followed by continue_handoff.
// Returns { stored, seed, pastePrompt, cutover? }.
export function saveAndCutover({ promptText, sid, cwd, title, preferTask = true, cutover = true }) {
  const stored = storeHandoff({ promptText, sid, cwd, title, preferTask });
  if (!stored) return { stored: null, seed: null, pastePrompt: null };
  const seed = buildSeedForStored(stored, { retry: true });
  const pastePrompt = buildSeedForStored(stored, { retry: false });
  const result = { stored, seed, pastePrompt };
  if (cutover) result.cutover = runHandoffCutover(cwd, seed, sid);
  return result;
}
