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
  openSync, closeSync, statSync,
} from "node:fs";
import { join, basename, parse, relative, resolve } from "node:path";
import { homedir } from "node:os";
import { execSync, execFileSync } from "node:child_process";
import { leadFrom, buildCutoverSeed } from "./cutover-seed.mjs";
import { supersededHandoffIds } from "./handoff-tasks.mjs";
import { AGENT_WORKTREES_QUERY_TIMEOUT_MS } from "./cli-timeouts.mjs";

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
export function agentWorktreesGet(key, cwd, sessionId, execute = runCli) {
  return agentWorktreesGetResult(key, cwd, sessionId, execute).value;
}

export function agentWorktreesGetResult(
  key, cwd, sessionId, execute = runCli,
) {
  const argv = ["get", key];
  if (sessionId) argv.push("--session-id", sessionId);
  try {
    const out = execute("agent-worktrees", argv, {
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

export function safePathSegment(value) {
  return String(value || "unknown")
    .replace(/[^A-Za-z0-9._-]/g, "_")
    .slice(0, 160) || "unknown";
}

export function isHerdrPane(env = process.env) {
  return env.HERDR_ENV === "1" && Boolean(env.HERDR_PANE_ID);
}

function commandErrorDetail(error) {
  for (const value of [error?.stderr, error?.stdout, error?.message]) {
    const detail = value?.toString().trim();
    if (detail) return detail;
  }
  return String(error).trim();
}

export function herdrHandoffDir(
  cwd, env = process.env, home = homedir(),
) {
  if (!isHerdrPane(env)) return null;
  const absoluteCwd = resolve(cwd || process.cwd());
  const checkoutPath = relative(parse(absoluteCwd).root, absoluteCwd);
  return join(
    home,
    ".copilot",
    "context-handoff",
    "checkouts",
    checkoutPath || "_root",
    "handoff",
  );
}

export function resolveHandoffCwd(
  cwd,
  {
    execute = runCli,
    env = process.env,
    home = homedir(),
  } = {},
) {
  if (!isHerdrPane(env)) {
    return { cwd: resolve(cwd || process.cwd()), error: null };
  }
  try {
    const output = execute(
      join(home, ".local", "bin", "herdr"),
      ["pane", "current", "--current"],
      { timeout: 5000 },
    );
    const paneCwd = JSON.parse(output)?.result?.pane?.cwd;
    if (typeof paneCwd !== "string" || !paneCwd) {
      return {
        cwd: null,
        error: "Herdr did not report the current pane working directory.",
      };
    }
    return { cwd: resolve(paneCwd), error: null };
  } catch (error) {
    const detail = commandErrorDetail(error);
    return {
      cwd: null,
      error: detail || "Unable to resolve the current Herdr pane working directory.",
    };
  }
}

export function currentPaneId(env = process.env) {
  if (isHerdrPane(env)) return null;
  return env.TMUX_PANE || env.PSMUX_PANE || null;
}

export function currentMuxSession(pane = currentPaneId(), execute = runCli) {
  if (!pane) return null;
  try {
    return execute(
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

export function worktreeInfo(
  cwd, sid, get = agentWorktreesGet, env = process.env,
) {
  if (isHerdrPane(env)) {
    return { wtDir: null, worktree: null, stateDir: null };
  }
  const wtDir = get("worktree-dir", cwd, sid);
  const worktree = wtDir ? basename(wtDir) : null;
  const stateDir = get("worktree-state-dir", cwd, sid);
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
    muxSession: currentMuxSession(),
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

export function handoffDirFor(
  cwd, sid, get = agentWorktreesGet, env = process.env, home = homedir(),
) {
  const herdrDir = herdrHandoffDir(cwd, env, home);
  if (herdrDir) return herdrDir;
  const stateDir = get("worktree-state-dir", cwd, sid);
  return stateDir ? join(stateDir, "handoff") : null;
}

export function writeJsonAtomic(path, value) {
  const tmp = `${path}.${process.pid}.tmp`;
  try {
    writeFileSync(tmp, JSON.stringify(value, null, 2), "utf-8");
    renameSync(tmp, path);
  } finally {
    try { unlinkSync(tmp); } catch { /* renamed or never created */ }
  }
}

// --- file-backed store ----------------------------------------------------
export function saveFileHandoff(promptText, sid, cwd, title) {
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
        ...metadata, consumed: false, consumedAt: null, promptText,
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

export function consumeFileHandoffOnce(
  cwd, sid, handoffId, explicitPath = null,
) {
  const found = readFileHandoff(cwd, sid, handoffId, explicitPath);
  if (!found) {
    return { ok: false, message: "File-backed handoff was not found." };
  }
  const lockPath = `${found.path}.consume.lock`;
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
            busy: true,
            message:
              `Handoff ${found.record.id || found.path} recovery is already active.`,
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
        busy: true,
        message:
          `Handoff ${found.record.id || found.path} is already being consumed. ` +
          "Do not replay it; retry only after the active consumer finishes.",
      };
    }
  }
  try {
    const current = readFileHandoff(cwd, sid, handoffId, found.path);
    if (!current) {
      return { ok: false, message: "File-backed handoff disappeared before consumption." };
    }
    if (current.record.consumed) {
      if (sid && current.record.consumedBySession === sid) {
        return {
          ok: true,
          resumedDelivery: true,
          path: current.path,
          record: current.record,
        };
      }
      return {
        ok: false,
        alreadyConsumed: true,
        id: current.record.id,
        message:
          `Handoff ${current.record.id || current.path} was already consumed at ` +
          `${current.record.consumedAt || "an unknown time"}. Do not replay it.`,
      };
    }
    const record = markFileHandoffConsumed(
      current.path, current.record, sid,
    );
    return { ok: true, path: current.path, record };
  } finally {
    if (lockFd !== null) {
      try { closeSync(lockFd); } catch { /* best-effort */ }
    }
    try { unlinkSync(lockPath); } catch { /* already gone */ }
  }
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
  const dir = metadata.stateDir ? join(metadata.stateDir, "handoff") : null;
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
  if (isHerdrPane()) return;
  try {
    const argv = ["note-handoff"];
    if (ref) argv.push("--task", ref);
    if (title) argv.push("--title", title);
    if (sid) argv.push("--session-id", sid);
    runCli("agent-worktrees", argv, { cwd, timeout: 5000 });
  } catch { /* history is advisory */ }
}

// --- live-cutover trigger -------------------------------------------------
// Herdr is selected only when the current session carries Herdr's pane identity.
// It owns pane mechanics only: context-handoff keeps the durable baton, launches
// one seeded sibling through the installed copilot-pane helper, and deliberately
// retains the predecessor as the recovery point. Other sessions keep the
// existing agent-worktrees mux choreography.
export function parseHerdrLaunchOutput(output) {
  const values = {};
  for (const line of String(output || "").split(/\r?\n/)) {
    const separator = line.indexOf("=");
    if (separator <= 0) continue;
    values[line.slice(0, separator)] = line.slice(separator + 1);
  }
  return {
    pane: values.pane_handle || null,
    sessionId: values.copilot_session_id || null,
  };
}

export function runHerdrHandoffCutover(
  cwd,
  seed,
  sessionId,
  {
    execute = runCli,
    env = process.env,
    home = homedir(),
    now = Date.now,
    launcherPath = join(home, ".local", "bin", "copilot-pane"),
  } = {},
) {
  const cwdResult = resolveHandoffCwd(cwd, { execute, env, home });
  if (!cwdResult.cwd) {
    return {
      ok: false,
      host: "herdr",
      reason: "error",
      error: cwdResult.error,
    };
  }
  const launchCwd = cwdResult.cwd;
  const dir = herdrHandoffDir(launchCwd, env, home);
  if (!dir) {
    return {
      ok: false,
      host: "herdr",
      reason: "error",
      error: "Herdr pane identity is unavailable.",
    };
  }
  const taskFile = join(
    dir,
    `launch-${safePathSegment(sessionId)}-${process.pid}-${now()}.txt`,
  );
  try {
    mkdirSync(dir, { recursive: true });
    writeFileSync(taskFile, seed, { encoding: "utf-8", mode: 0o600 });
    const output = execute(
      launcherPath,
      [
        "launch",
        "--role", "coordinator",
        "--cwd", launchCwd,
        "--host", "local",
        "--task-file", taskFile,
      ],
      { cwd: launchCwd, timeout: 180_000 },
    );
    const launched = parseHerdrLaunchOutput(output);
    if (!launched.pane || !launched.sessionId) {
      return {
        ok: false,
        host: "herdr",
        reason: "error",
        error: "copilot-pane did not report the successor pane and session.",
      };
    }
    return {
      ok: true,
      host: "herdr",
      old_pane: env.HERDR_PANE_ID,
      new_pane: launched.pane,
      new_session: launched.sessionId,
      predecessor_retained: true,
    };
  } catch (error) {
    const detail = commandErrorDetail(error);
    return {
      ok: false,
      host: "herdr",
      reason: "error",
      error: detail || "copilot-pane launch failed.",
    };
  } finally {
    try { unlinkSync(taskFile); } catch { /* consumed or never written */ }
  }
}

export function runHandoffCutover(
  cwd, seed, sessionId, options = {},
) {
  const env = options.env || process.env;
  const execute = options.execute || runCli;
  if (isHerdrPane(env)) {
    return runHerdrHandoffCutover(cwd, seed, sessionId, {
      ...options,
      env,
      execute,
    });
  }
  const argv = ["handoff-cutover", "--seed", seed];
  const ownPane = env.TMUX_PANE || env.PSMUX_PANE || "";
  if (ownPane) argv.push("--old-pane", ownPane);
  if (sessionId) argv.push("--session-id", sessionId);
  try {
    const result = JSON.parse(execute(
      "agent-worktrees", argv, { cwd, timeout: 20000 },
    ));
    return result?.ok
      ? { ...result, host: "mux" }
      : { ok: false, host: "mux", reason: "error", error: null };
  } catch (e) {
    const status = typeof e?.status === "number" ? e.status : null;
    let error = null;
    try {
      const stdout = (e?.stdout || "").toString();
      const parsed = stdout ? JSON.parse(stdout) : null;
      error = parsed?.error || null;
    } catch { /* stdout was not JSON */ }
    const reason = status === 2 ? "no-worktree" : status === 3 ? "no-mux" : "error";
    return { ok: false, host: "mux", reason, error };
  }
}

// --- high-level orchestration (what the CLI + extension both want) ---------

// Store a handoff. Herdr sessions always use context-handoff's checkout-scoped
// machine-local state without probing agent-worktrees. Other sessions prefer an
// agent-dispatch task (durable/browsable) and fall back to a one-time local file.
// Mirrors the extension's save_handoff_prompt store selection. Returns:
//   { storage: "agent-dispatch"|"file", id, taskId?, path?, metadata }
// On failure returns a storage:null result with the resolver/write diagnostic.
export function storeHandoff({ promptText, sid, cwd, title, preferTask = true }) {
  const cwdResult = resolveHandoffCwd(cwd);
  if (!cwdResult.cwd) {
    return {
      storage: null,
      id: null,
      metadata: null,
      error: cwdResult.error,
    };
  }
  cwd = cwdResult.cwd;
  if (preferTask && !isHerdrPane() && agentDispatchAvailable()) {
    const task = dispatchHandoff(promptText, sid, cwd, title);
    if (task) {
      noteHandoffInRecord(cwd, sid, task.id, title);
      return { storage: "agent-dispatch", id: task.id, taskId: task.id, metadata: task.metadata };
    }
  }
  const file = saveFileHandoff(promptText, sid, cwd, title);
  if (!file?.path) {
    return { storage: null, id: null, metadata: null, error: file?.error || "unknown file-store failure" };
  }
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
    worktreeDir: md.worktreeDir || null,
    sessionId: md.sessionId || null,
    path: stored.path || null,
    muxSession: md.muxSession || null,
  });
}

// Store + build seed + (optionally) trigger the live cutover in one call --
// the standalone equivalent of save_handoff_prompt followed by continue_handoff.
// Returns { stored, seed, pastePrompt, cutover? }.
export function saveAndCutover({ promptText, sid, cwd, title, preferTask = true, cutover = true }) {
  const stored = storeHandoff({ promptText, sid, cwd, title, preferTask });
  if (!stored?.storage) {
    return {
      stored: null,
      seed: null,
      pastePrompt: null,
      error: stored?.error || "No safe handoff store resolved.",
    };
  }
  const seed = buildSeedForStored(stored, { retry: true });
  const pastePrompt = buildSeedForStored(stored, { retry: false });
  const result = { stored, seed, pastePrompt };
  if (cutover) result.cutover = runHandoffCutover(cwd, seed, sid);
  return result;
}
