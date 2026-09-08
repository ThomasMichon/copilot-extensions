// handoff-core.mjs -- SDK-free store/trigger core for context handoffs.
//
// Everything the handoff store + pending-pickup trigger needs, WITHOUT
// importing the Copilot SDK or the session extension. This is the same
// rationale that extracted `cutover-seed.mjs` (the seed builders): keep the
// load-bearing logic importable from anywhere -- a unit test, the clean-room,
// and (the reason this module exists) a standalone CLI (`handoff-cli.mjs`) an
// agent can invoke DIRECTLY when the context-handoff extension does not resolve
// or fails to load (e.g. a Bare-resumed session where no extensions load at
// all).
//
// The functions here are ported faithfully from extension.mjs's store/trigger
// helpers. They shell out to the SAME `agent-worktrees` / `agent-dispatch`
// binstubs and write the SAME on-disk handoff format, so a handoff stored via
// this core is byte-compatible with the extension's `consume_handoff` /
// `/resume-handoff` path.

import {
  writeFileSync, readFileSync, existsSync, mkdirSync, renameSync, unlinkSync,
  openSync, closeSync, statSync, readdirSync,
} from "node:fs";
import { join, basename, dirname } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import {
  CONTINUATION_DIRECTIVE,
  leadFrom,
  buildCutoverSeed,
} from "./cutover-seed.mjs";
import { supersededHandoffIds } from "./handoff-tasks.mjs";
import { AGENT_WORKTREES_QUERY_TIMEOUT_MS } from "./cli-timeouts.mjs";

export const HANDOFF_META_PREFIX = "<!-- context-handoff:";
export const HANDOFF_META_SUFFIX = "-->";
export const HANDOFF_CLI_PATH = join(
  dirname(fileURLToPath(import.meta.url)),
  "handoff-cli.mjs",
);
const PLUGIN_ROOT = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const INSTALLATION_ROOT = dirname(PLUGIN_ROOT);
const CANONICAL_REPOSITORY =
  "https://github.com/ThomasMichon/copilot-extensions";
const RUNTIME_PROVISION_TIMEOUT_MS = 180000;

// --- cross-platform system-CLI invocation ---------------------------------
// Resolve sibling payload/runtime ownership before invoking exact isolated
// Python argv. User-controlled handoff text never becomes shell source.

function resolveSystemCliDescriptor(bin) {
  const layouts = {
    "agent-worktrees": {
      relative:
        process.platform === "win32"
          ? join("bin", "payload", "agent-worktrees.ps1")
          : join("bin", "payload", "agent-worktrees"),
      module: "agent_worktrees",
      runtimeRoot: ".agent-worktrees",
      payloadRootEnv: "AGENT_WORKTREES_PAYLOAD_ROOT",
    },
    "agent-dispatch": {
      relative:
        process.platform === "win32"
          ? join("bin", "agent-dispatch.ps1")
          : join("bin", "agent-dispatch"),
      module: "agent_dispatch",
      runtimeRoot: ".agent-dispatch",
      payloadRootEnv: null,
    },
  };
  const layout = layouts[bin];
  if (!layout) return { path: bin, pluginRoot: null };

  const siblingRoot = join(INSTALLATION_ROOT, bin);
  const manifestPath = join(siblingRoot, "plugin.json");
  const commandPath = join(siblingRoot, layout.relative);
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
  } catch {
    throw new Error(`${bin} payload is unavailable in this installation`);
  }
  if (
    manifest?.name !== bin
    || manifest?.repository !== CANONICAL_REPOSITORY
    || !existsSync(commandPath)
  ) {
    throw new Error(`${bin} payload provenance or command path is invalid`);
  }
  return {
    path: commandPath,
    pluginRoot: siblingRoot,
    module: layout.module,
    runtimeRoot: layout.runtimeRoot,
    payloadRootEnv: layout.payloadRootEnv,
  };
}

export function resolveSystemCli(bin) {
  return resolveSystemCliDescriptor(bin).path;
}

function windowsPowerShell() {
  return join(
    process.env.SystemRoot || "C:\\Windows",
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
  );
}

function resolveRuntimePython(resolved, env, cwd, timeout) {
  const resolve = process.platform === "win32"
    ? () => {
      const script = [
        "$ErrorActionPreference='Stop'",
        `$env:AGENT_RT_ROOT=Join-Path $env:USERPROFILE '${resolved.runtimeRoot}'`,
        ". (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts\\resolve-runtime.ps1')",
        "if ($AgentRtPy) {[Console]::Out.Write($AgentRtPy)}",
      ].join("; ");
      return execFileSync(
        windowsPowerShell(),
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        { cwd, timeout, env, encoding: "utf-8" },
      ).trim();
    }
    : () => execFileSync("sh", ["-c", [
      `AGENT_RT_ROOT="$HOME/${resolved.runtimeRoot}"`,
      "export AGENT_RT_ROOT",
      '. "$COPILOT_PLUGIN_ROOT/scripts/resolve-runtime.sh"',
      'printf %s "${AGENT_RT_PY:-}"',
    ].join("; ")], {
      cwd, timeout, env, encoding: "utf-8",
    }).trim();
  let python = resolve();
  if (!python) {
    const provisionBin = process.platform === "win32"
      ? windowsPowerShell()
      : resolved.path;
    const provisionArgs = process.platform === "win32"
      ? [
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", resolved.path,
        "--help",
      ]
      : ["--help"];
    execFileSync(provisionBin, provisionArgs, {
      cwd,
      timeout: Math.max(timeout, RUNTIME_PROVISION_TIMEOUT_MS),
      env,
      encoding: "utf-8",
      stdio: "ignore",
    });
    python = resolve();
  }
  if (!python || !existsSync(python)) {
    throw new Error("payload runtime provisioning did not produce Python");
  }
  return python;
}

export function isolatedPythonArgs(module, args) {
  return ["-I", "-X", "utf8", "-m", module, ...args];
}

export function runtimeEnvironment(baseEnv, pluginRoot) {
  const env = {
    ...baseEnv,
    COPILOT_PLUGIN_ROOT: pluginRoot,
    PYTHONUTF8: "1",
  };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  return env;
}

export function runCli(bin, args, opts = {}) {
  const { cwd, timeout = 15000, ...extra } = opts;
  const resolved = resolveSystemCliDescriptor(bin);
  const resolvedBin = resolved.path;
  const env = resolved.pluginRoot
    ? runtimeEnvironment(
      { ...process.env, ...extra.env },
      resolved.pluginRoot,
    )
    : extra.env;
  const childOptions = { ...extra, env };
  if (resolved.pluginRoot) {
    const python = resolveRuntimePython(
      resolved, env, cwd, timeout,
    );
    if (resolved.payloadRootEnv) {
      env[resolved.payloadRootEnv] = resolved.pluginRoot;
    }
    return execFileSync(
      python,
      isolatedPythonArgs(resolved.module, args),
      {
      ...childOptions, cwd, timeout, encoding: "utf-8",
      },
    );
  }
  return execFileSync(resolvedBin, args, {
    ...childOptions, cwd, timeout, encoding: "utf-8",
  });
}

// True if an agent-dispatch coordinator answers a health probe.
export function agentDispatchAvailable() {
  try {
    runCli("agent-dispatch", ["health"], {
      timeout: 5000,
      stdio: "ignore",
    });
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

export function normalizeHandoffTitle(value) {
  return String(value || "")
    .replace(/\0/g, "")
    .replace(/[\r\n\t]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function sessionBindingForSession(
  sessionId, cwd, execute = runCli,
) {
  if (!sessionId) return { found: false, session_id: null };
  try {
    const raw = execute(
      "agent-worktrees",
      ["session-binding", "--session-id", sessionId, "--json"],
      { cwd, timeout: AGENT_WORKTREES_QUERY_TIMEOUT_MS },
    );
    const parsed = JSON.parse(raw);
    return parsed?.found
      ? parsed
      : { found: false, session_id: sessionId };
  } catch {
    return { found: false, session_id: sessionId };
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

export function worktreeInfo(cwd, sid, get = agentWorktreesGet) {
  const wtDir = get("worktree-dir", cwd, sid);
  const worktree = wtDir ? basename(wtDir) : null;
  const stateDir = get("worktree-state-dir", cwd, sid);
  return { wtDir, worktree, stateDir };
}

export function collectAdvisoryGitFacts(
  cwd = process.cwd(),
  execute = execFileSync,
) {
  const git = (args) => {
    try {
      return execute("git", args, {
        cwd,
        timeout: 5000,
        encoding: "utf-8",
      }).trim() || null;
    } catch {
      return null;
    }
  };
  return {
    branch: git(["rev-parse", "--abbrev-ref", "HEAD"]),
    repo: git(["remote", "get-url", "origin"]),
    status: git(["status", "--short"]),
  };
}

export function collectCliHandoffFacts(cwd, sid) {
  const info = worktreeInfo(cwd, sid);
  const git = collectAdvisoryGitFacts(cwd);
  return {
    sessionId: sid || null,
    cwd,
    worktree: info.worktree,
    worktreeDir: info.wtDir,
    stateDir: info.stateDir,
    branch: git.branch,
    repo: git.repo,
    gitStatus: git.status,
    sessionBinding: sessionBindingForSession(sid, cwd),
    generatedAt: new Date().toISOString(),
  };
}

export function makeHandoffMetadata(
  { sid, cwd, title, storage, taskId = null },
  get = agentWorktreesGet,
) {
  const { wtDir, worktree, stateDir } = worktreeInfo(cwd, sid, get);
  const id = `handoff-${safePathSegment(sid)}`;
  return {
    kind: "context-handoff",
    version: 2,
    id,
    storage,
    taskId,
    sessionId: sid,
    cwd,
    title: title || "",
    worktree,
    worktreeDir: wtDir,
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
  const firstLine = (
    firstNewline >= 0 ? text.slice(0, firstNewline) : text
  ).replace(/\r$/, "");
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

export function handoffDirFor(cwd, sid, get = agentWorktreesGet) {
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
  const dir = metadata.stateDir ? join(metadata.stateDir, "handoff") : null;
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

export function findHandoffTask(cwd, worktree) {
  const tasks = agentDispatchJson(
    ["list", "--status", "proposed,queued", "--label", "handoff"], cwd,
  );
  if (!Array.isArray(tasks)) return null;
  const mine = tasks.filter((task) => task?.target_worktree === worktree);
  mine.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  return mine[0] || null;
}

export function readTaskPayloadRaw(cwd, taskId) {
  try {
    return runCli(
      "agent-dispatch", ["payload", taskId, "--raw"],
      { cwd, timeout: 15000 },
    );
  } catch {
    return "";
  }
}

export function runAgentDispatchConsume(cwd, taskId, deferComplete) {
  const argv = ["consume", taskId];
  if (deferComplete) argv.push("--defer-complete");
  return runCli("agent-dispatch", argv, { cwd, timeout: 20000 });
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

function describeCliError(error) {
  return (
    error?.stderr
    || error?.stdout
    || error?.message
    || String(error)
  ).toString().trim();
}

function cliJson(bin, argv, cwd, timeout = 15000, execute = runCli) {
  try {
    return JSON.parse(execute(bin, argv, { cwd, timeout }));
  } catch (error) {
    const stdout = error?.stdout?.toString().trim();
    if (stdout) {
      try {
        return JSON.parse(stdout);
      } catch {
        // Preserve the original command failure when stdout is not JSON.
      }
    }
    throw error;
  }
}

function sessionStateDirFor(sessionId) {
  const safe = safePathSegment(sessionId);
  return join(homedir(), ".copilot", "session-state", safe);
}

export function sessionStateHandoffPath(sessionId) {
  return join(sessionStateDirFor(sessionId), "handoff-request.json");
}

export function readSessionStateHandoff(sessionId) {
  const path = sessionStateHandoffPath(sessionId);
  if (!existsSync(path)) return null;
  try {
    return { path, record: JSON.parse(readFileSync(path, "utf-8")) };
  } catch {
    return null;
  }
}

export function writeSessionStateHandoff(
  { sid, promptText, stored, seed },
) {
  if (!sid) return { ok: false, path: null, error: "session id is unavailable" };
  const path = sessionStateHandoffPath(sid);
  const record = {
    kind: "context-handoff-session-request",
    version: 1,
    sessionId: sid,
    storage: stored.storage,
    handoffId: stored.id,
    taskId: stored.taskId || null,
    handoffPath: stored.path || null,
    worktree: stored.metadata?.worktree || null,
    worktreeDir: stored.metadata?.worktreeDir || null,
    stateDir: stored.metadata?.stateDir || null,
    title: stored.metadata?.title || "",
    seed,
    promptText,
    consumed: false,
    consumedAt: null,
    consumedBySession: null,
    createdAt: new Date().toISOString(),
  };
  try {
    mkdirSync(dirname(path), { recursive: true });
    writeJsonAtomic(path, record);
    return { ok: true, path, record };
  } catch (error) {
    return {
      ok: false,
      path,
      error:
        `resolved session-state directory ${dirname(path)}, but the handoff ` +
        `request write failed: ${error?.message || String(error)}`,
    };
  }
}

export function markSessionStateHandoffConsumed(
  predecessorSessionId,
  { consumedBySession = null, handoffId = null } = {},
) {
  if (!predecessorSessionId) return null;
  const found = readSessionStateHandoff(predecessorSessionId);
  if (!found?.record) return null;
  const current = found.record;
  if (handoffId && current.handoffId && current.handoffId !== handoffId) {
    return current;
  }
  const consumed = {
    ...current,
    consumed: true,
    consumedAt: current.consumedAt || new Date().toISOString(),
    consumedBySession: consumedBySession || current.consumedBySession || null,
  };
  writeJsonAtomic(found.path, consumed);
  return consumed;
}

function deliveryCheckpointPath(metadata, token) {
  const stateDir = metadata?.stateDir;
  if (!stateDir || !token) return null;
  return join(
    stateDir,
    "handoff",
    `delivery-${safePathSegment(token)}.json`,
  );
}

function readDeliveryCheckpoint(path) {
  if (!path || !existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

function writeDeliveryCheckpoint(checkpoint) {
  if (!checkpoint?.path) return checkpoint;
  mkdirSync(dirname(checkpoint.path), { recursive: true });
  checkpoint.updatedAt = new Date().toISOString();
  writeJsonAtomic(checkpoint.path, checkpoint);
  return checkpoint;
}

function checkpointStep(checkpoint, name, detail = null) {
  if (!checkpoint) return;
  checkpoint.steps = checkpoint.steps || {};
  checkpoint.steps[name] = true;
  checkpoint.stepTimes = checkpoint.stepTimes || {};
  checkpoint.stepTimes[name] = new Date().toISOString();
  if (detail !== null) {
    checkpoint.details = checkpoint.details || {};
    checkpoint.details[name] = detail;
  }
  writeDeliveryCheckpoint(checkpoint);
}

function prepareTaskDeliveryCheckpoint(
  cwd, taskId, sid, decoded = null, stateDirResolver = agentWorktreesGet,
) {
  const source = decoded || decodeHandoffPayload(readTaskPayloadRaw(cwd, taskId));
  const metadata = source.metadata || {};
  if (!metadata.stateDir) {
    metadata.stateDir = stateDirResolver(
      "worktree-state-dir", cwd, sid,
    );
  }
  const path = deliveryCheckpointPath(metadata, taskId);
  const existing = readDeliveryCheckpoint(path);
  if (existing) {
    if (existing.consumerSession && existing.consumerSession !== sid) {
      return {
        ok: false,
        message:
          `Handoff ${taskId} is already being delivered to ` +
          `${existing.consumerSession}; refusing replay in ${sid || "unknown"}.`,
      };
    }
    return { ok: true, checkpoint: existing };
  }
  if (!path) {
    return {
      ok: false,
      message: "Task-backed handoff metadata has no durable worktree state path.",
    };
  }
  const checkpoint = {
    kind: "context-handoff-delivery",
    version: 1,
    path,
    handoffToken: taskId,
    predecessorSession: metadata.sessionId || null,
    consumerSession: sid || null,
    metadata,
    payload: source.text || "",
    steps: {
      payloadStored: true,
      consumeAttempted: false,
      taskConsumed: false,
      promptInjected: false,
    },
    createdAt: new Date().toISOString(),
  };
  writeDeliveryCheckpoint(checkpoint);
  return { ok: true, checkpoint };
}

export function findTaskDeliveryCheckpoint(cwd, sid) {
  const root = handoffDirFor(cwd, sid);
  if (!root || !existsSync(root)) return null;
  let newest = null;
  let newestMtime = 0;
  let files;
  try {
    files = readdirSync(root);
  } catch {
    return null;
  }
  for (const file of files) {
    if (!file.startsWith("delivery-") || !file.endsWith(".json")) continue;
    const path = join(root, file);
    try {
      const checkpoint = JSON.parse(readFileSync(path, "utf-8"));
      if (
        checkpoint.kind !== "context-handoff-delivery"
        || checkpoint.consumerSession !== sid
        || checkpoint.steps?.promptInjected
      ) continue;
      const mtime = statSync(path).mtimeMs;
      if (mtime > newestMtime) {
        newestMtime = mtime;
        newest = checkpoint;
      }
    } catch {
      // Ignore malformed or concurrently replaced checkpoints.
    }
  }
  return newest;
}

export function markDeliveryPromptInjected(pathOrCheckpoint) {
  const checkpoint = typeof pathOrCheckpoint === "string"
    ? readDeliveryCheckpoint(pathOrCheckpoint)
    : pathOrCheckpoint;
  if (!checkpoint?.path) return null;
  checkpointStep(checkpoint, "promptInjected");
  return checkpoint;
}

export function findHandoffFile(cwd, sid) {
  const root = handoffDirFor(cwd, sid);
  if (!root || !existsSync(root)) return null;
  let best = null;
  let bestScore = 0;
  let files;
  try {
    files = readdirSync(root);
  } catch {
    return null;
  }

  for (const file of files) {
    if (
      !file.endsWith(".json")
      || file.startsWith("delivery-")
    ) continue;
    const path = join(root, file);
    try {
      const record = JSON.parse(readFileSync(path, "utf-8"));
      if (record.consumed && record.consumedBySession !== sid) continue;
      const score = statSync(path).mtimeMs
        + ((record.cwd === cwd) ? 1e15 : 0);
      if (score > bestScore) {
        bestScore = score;
        best = { path, record };
      }
    } catch {
      // Ignore malformed or concurrently replaced records.
    }
  }
  return best;
}

function loadStoredTaskHandoff(cwd, taskId) {
  const raw = readTaskPayloadRaw(cwd, taskId);
  const decoded = decodeHandoffPayload(raw);
  if (!decoded.text && !decoded.metadata) return null;
  return {
    storage: "agent-dispatch",
    id: taskId,
    taskId,
    metadata: decoded.metadata || null,
    promptText: decoded.text || "",
  };
}

function loadStoredFileHandoff(cwd, sid, handoffId) {
  const found = readFileHandoff(cwd, sid, handoffId);
  if (!found?.record) return null;
  return {
    storage: "file",
    id: found.record.id,
    path: found.path,
    metadata: found.record,
    promptText: String(found.record.promptText || ""),
  };
}

export function recoverStoredHandoff(cwd, sid, handoffToken = null) {
  const explicitKinds = handoffToken && handoffToken.startsWith("handoff-")
    ? ["file", "agent-dispatch"]
    : ["agent-dispatch", "file"];

  if (handoffToken) {
    for (const kind of explicitKinds) {
      const loaded = kind === "agent-dispatch"
        ? loadStoredTaskHandoff(cwd, handoffToken)
        : loadStoredFileHandoff(cwd, sid, handoffToken);
      if (loaded) return loaded;
    }
    return null;
  }

  const { wtDir, worktree } = worktreeInfo(cwd, sid);
  if (worktree) {
    const task = findHandoffTask(cwd, worktree);
    if (task?.id) {
      const loaded = loadStoredTaskHandoff(cwd, task.id);
      if (loaded) {
        loaded.metadata = loaded.metadata || {
          title: task.title || task.name || "",
          worktree,
          worktreeDir: wtDir,
          sessionId: sid,
        };
        return loaded;
      }
    }
  }
  const file = findHandoffFile(cwd, sid);
  if (!file?.record?.id) return null;
  return {
    storage: "file",
    id: file.record.id,
    path: file.path,
    metadata: file.record,
    promptText: String(file.record.promptText || ""),
  };
}

export function consumeDispatchHandoffTask(
  cwd,
  taskId,
  sid,
  deferComplete = false,
  {
    readPayload = readTaskPayloadRaw,
    consumeTask = runAgentDispatchConsume,
    stateDirResolver = agentWorktreesGet,
  } = {},
) {
  const before = decodeHandoffPayload(readPayload(cwd, taskId));
  const prepared = prepareTaskDeliveryCheckpoint(
    cwd, taskId, sid, before, stateDirResolver,
  );
  if (!prepared.ok) return { ok: false, id: taskId, message: prepared.message };
  const checkpoint = prepared.checkpoint;
  const checkpointPayload = decodeHandoffPayload(checkpoint.payload || "");
  let decoded = {
    metadata: {
      ...(before.metadata || {}),
      ...(checkpointPayload.metadata || {}),
      ...(checkpoint.metadata || {}),
    },
    text: checkpointPayload.metadata
      ? checkpointPayload.text
      : checkpoint.payload || before.text || "",
  };
  if (!checkpoint.steps?.taskConsumed) {
    checkpointStep(checkpoint, "consumeAttempted");
    try {
      const consumed = consumeTask(cwd, taskId, deferComplete);
      const fromConsume = decodeHandoffPayload(consumed);
      if (fromConsume.text) decoded.text = fromConsume.text;
      if (fromConsume.metadata) decoded.metadata = fromConsume.metadata;
      checkpoint.payload = decoded.text;
      checkpoint.metadata = decoded.metadata;
      checkpointStep(checkpoint, "taskConsumed");
    } catch (error) {
      return {
        ok: false,
        id: taskId,
        message: describeCliError(error) || "Could not consume handoff task.",
      };
    }
  }
  markSessionStateHandoffConsumed(
    decoded.metadata?.sessionId || checkpoint.predecessorSession || null,
    { consumedBySession: sid, handoffId: taskId },
  );
  return {
    ok: true,
    id: taskId,
    payload: String(decoded.text || "").trim(),
    metadata: decoded.metadata,
    checkpoint: checkpoint.path,
    checkpointState: checkpoint,
    resumedDelivery: Boolean(checkpoint.steps?.promptInjected),
  };
}

export function consumeFileHandoff(
  cwd,
  sid,
  handoffId,
  explicitPath = null,
) {
  const consumed = consumeFileHandoffOnce(
    cwd, sid, handoffId, explicitPath,
  );
  if (!consumed.ok) return consumed;
  const record = consumed.record;
  markSessionStateHandoffConsumed(
    record.sessionId || null,
    { consumedBySession: sid, handoffId: record.id },
  );
  return {
    ok: true,
    id: record.id,
    path: consumed.path,
    payload: String(record.promptText || "").trim(),
    metadata: record,
    resumedDelivery: consumed.resumedDelivery,
  };
}

export function formatConsumeResult(
  result, { deferComplete = false } = {},
) {
  if (!result?.ok) {
    return (
      `${result?.message || "Handoff could not be consumed."}\n\n` +
      "Handoff consumption is blocked. Do not treat the missing brief as " +
      "completion or reconstruct a different objective from session history."
    );
  }
  return [
    "## Handoff Consumed",
    "",
    result.id ? `**Handoff:** ${result.id}` : null,
    result.resumedDelivery
      ? "**Delivery:** resumed after a prior same-session pickup"
      : "**Delivery:** claimed exactly once",
    deferComplete && result.id
      ? `**Completion:** when the handoff goal is reached, run \`agent-dispatch complete ${result.id}\`.`
      : null,
    "",
    CONTINUATION_DIRECTIVE,
    "",
    "---",
    "",
    result.payload || "(The handoff payload was empty.)",
  ].filter(Boolean).join("\n");
}

export function buildResumePrompt(
  handoffText,
  source,
  { deferredTaskId = null } = {},
) {
  return [
    `You are resuming a handoff (${source}). Continue in place from the stored brief.`,
    deferredTaskId
      ? `Keep agent-dispatch task ${deferredTaskId} owned. Only after the handoff objective's completion gate is met run: agent-dispatch complete ${deferredTaskId}`
      : null,
    CONTINUATION_DIRECTIVE,
    "",
    "---",
    "",
    handoffText,
  ].filter((line) => line !== null).join("\n");
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

function logHandoffActivity(
  cwd,
  sid,
  worktreeId,
  stored,
  sessionStatePath,
  execute = runCli,
) {
  if (!worktreeId) return { logged: false };
  try {
    execute("agent-worktrees", [
      "activity-log",
      "handoff_requested",
      "--worktree-id", worktreeId,
      "--session-id", sid || "",
      "--source", "context-handoff",
      "--field", `handoff_id=${stored.id}`,
      "--field", `storage=${stored.storage}`,
      "--field", `session_state=${sessionStatePath}`,
    ], {
      cwd,
      timeout: 5000,
      stdio: "ignore",
    });
    return { logged: true };
  } catch (error) {
    return { logged: false, error: describeCliError(error) };
  }
}

function worktreeHeadState(cwd, worktreeId, execute = runCli) {
  if (!worktreeId) return null;
  try {
    return cliJson(
      "agent-worktrees",
      ["head-session", "--worktree", worktreeId, "--json"],
      cwd,
      10000,
      execute,
    );
  } catch {
    return null;
  }
}

function worktreeSuccessorRecorded(cwd, stored, predecessorSessionId, execute = runCli) {
  const worktreeId = stored.metadata?.worktree || null;
  const head = worktreeHeadState(cwd, worktreeId, execute);
  if (!head || !head.tracked) return { pickedUp: false, worktreeId, head };
  const pending = Array.isArray(head.pending_handoffs) ? head.pending_handoffs : [];
  const matching = pending.find((item) => item?.token === stored.id) || null;
  const headSession = head.head_session || null;
  const candidate = matching?.candidate || null;
  return {
    pickedUp:
      Boolean(candidate)
      || Boolean(headSession && headSession !== predecessorSessionId),
    worktreeId,
    head,
    candidate,
    headSession,
  };
}

function dispatchTaskConsumed(cwd, taskId) {
  if (!taskId) return { consumed: false, task: null };
  const task = agentDispatchJson(["show", taskId], cwd);
  const status = String(task?.status || "");
  return {
    consumed: Boolean(task && !["proposed", "queued"].includes(status)),
    task,
  };
}

function requestAgentBridgeHandoff(
  cwd,
  sid,
  stored,
  seed,
  execute = runCli,
) {
  const worktreeId = stored.metadata?.worktree || null;
  if (!worktreeId) {
    return { attempted: false, accepted: false, reason: "no-worktree" };
  }
  try {
    const response = cliJson(
      "agent-bridge",
      [
        "handoff-request",
        "--worktree-id", worktreeId,
        "--session-id", sid || "",
        "--handoff-token", stored.id,
        "--seed", seed,
        "--json",
      ],
      cwd,
      5000,
      execute,
    );
    return { attempted: true, accepted: true, response };
  } catch (error) {
    return {
      attempted: true,
      accepted: false,
      error: describeCliError(error),
    };
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pickupSignals(cwd, sid, stored, sessionStatePath, execute = runCli) {
  const found = readSessionStateHandoff(sid);
  const sessionConsumed = Boolean(found?.record?.consumed);
  const worktree = worktreeSuccessorRecorded(cwd, stored, sid, execute);
  const dispatch = stored.storage === "agent-dispatch"
    ? dispatchTaskConsumed(cwd, stored.id)
    : { consumed: false, task: null };
  const via = [];
  if (sessionConsumed) via.push("session-state-consumed");
  if (worktree.pickedUp) via.push("worktree-successor");
  if (dispatch.consumed) via.push("dispatch-consumed");
  return {
    pickedUp: via.length > 0,
    via,
    sessionState: {
      path: sessionStatePath,
      consumed: sessionConsumed,
    },
    worktree,
    dispatch,
  };
}

// Store a handoff, preferring an agent-dispatch task (durable/browsable) when a
// coordinator is reachable, else a one-time worktree-state file. Mirrors the
// extension's save_handoff_prompt store selection. Returns:
//   { storage: "agent-dispatch"|"file", id, taskId?, path?, metadata }
// On failure returns a storage:null result with the resolver/write diagnostic.
export function storeHandoff({ promptText, sid, cwd, title, preferTask = true }) {
  if (preferTask && agentDispatchAvailable()) {
    const task = dispatchHandoff(promptText, sid, cwd, title);
    if (task) {
      noteHandoffInRecord(cwd, sid, task.id, title);
      return {
        storage: "agent-dispatch",
        id: task.id,
        taskId: task.id,
        metadata: task.metadata,
      };
    }
  }
  const file = saveFileHandoff(promptText, sid, cwd, title);
  if (!file?.path) {
    return {
      storage: null,
      id: null,
      metadata: null,
      error: file?.error || "unknown file-store failure",
    };
  }
  noteHandoffInRecord(cwd, sid, file.id, title);
  return { storage: "file", id: file.id, path: file.path, metadata: file.metadata };
}

// Compose the same short locator seed for manual recovery and external pickup.
export function buildSeedForStored(stored) {
  const md = stored.metadata || {};
  const kind = stored.storage === "agent-dispatch" ? "task" : "file";
  const lead = leadFrom(md.title);
  return buildCutoverSeed(kind, stored.id, lead);
}

export function manualFallbackInstructions(stored, seed) {
  const location = stored.storage === "agent-dispatch"
    ? `agent-dispatch task ${stored.id}`
    : `file handoff ${stored.id}`;
  return (
    `Handoff stored as ${location}. No control system acknowledged the request ` +
    "during the grace window, so continue manually: open the successor session " +
    "in this worktree (or ask your control plane to pick up the pending handoff), " +
    "then run `/consume-handoff`. If that command is unavailable, use the " +
    "context-handoff payload-local CLI and pass only the trailing `task:<id>` " +
    "or `file:<id>` token to `consume --locator`.\n\n" +
    "Copy only the following short handoff prompt/seed:\n\n" +
    "```text\n" +
    `${seed}\n` +
    "```"
  );
}

export async function triggerHandoff(
  {
    promptText = null,
    sid,
    cwd,
    title = "",
    preferTask = true,
    handoffToken = null,
    waitMs = 30000,
    execute = runCli,
    store = storeHandoff,
    writeSessionState = writeSessionStateHandoff,
    noteHandoff = noteHandoffInRecord,
    logActivity = logHandoffActivity,
    requestBridge = requestAgentBridgeHandoff,
    readPickupSignals = pickupSignals,
    sleepFn = sleep,
  },
) {
  const normalizedPrompt = String(promptText || "").trim();
  let stored;
  let handoffBody;
  let justStored = false;

  if (normalizedPrompt) {
    stored = store({
      promptText: normalizedPrompt,
      sid,
      cwd,
      title,
      preferTask,
    });
    if (!stored?.storage) {
      return {
        ok: false,
        reason: "store-failed",
        error: stored?.error || "No safe handoff store resolved.",
      };
    }
    handoffBody = normalizedPrompt;
    justStored = true;
  } else {
    const loaded = recoverStoredHandoff(cwd, sid, handoffToken);
    if (!loaded) {
      return {
        ok: false,
        reason: "not-found",
        error: "No saved handoff was found for this worktree.",
      };
    }
    stored = {
      storage: loaded.storage,
      id: loaded.id,
      taskId: loaded.taskId || null,
      path: loaded.path || null,
      metadata: loaded.metadata || null,
    };
    handoffBody = String(loaded.promptText || "").trim();
  }

  const seed = buildSeedForStored(stored);
  const sessionState = writeSessionState({
    sid,
    promptText: handoffBody,
    stored,
    seed,
  });
  if (!sessionState.ok) {
    return {
      ok: false,
      reason: "session-state-write-failed",
      stored,
      seed,
      error: sessionState.error,
    };
  }

  if (!justStored) {
    noteHandoff(cwd, sid, stored.id, stored.metadata?.title || title);
  }
  const activity = logActivity(
    cwd,
    sid,
    stored.metadata?.worktree || null,
    stored,
    sessionState.path,
    execute,
  );
  const bridge = requestBridge(
    cwd, sid, stored, seed, execute,
  );

  const start = Date.now();
  let status = readPickupSignals(cwd, sid, stored, sessionState.path, execute);
  while (!status.pickedUp && (Date.now() - start) < waitMs) {
    await sleepFn(1000);
    status = readPickupSignals(cwd, sid, stored, sessionState.path, execute);
  }

  return {
    ok: true,
    stored,
    seed,
    sessionState,
    worktreeSignal: {
      noted: true,
      activity,
    },
    bridge,
    pickup: {
      ...status,
      waitedMs: Date.now() - start,
    },
    manualInstructions: status.pickedUp
      ? null
      : manualFallbackInstructions(stored, seed),
  };
}
