// Async, non-blocking session-metadata resolution for the agent-bridge
// extension. The Core activation supplies the original SDK adapter's root;
// relocating this module must not change the selected plugin identity.
//
// NEVER use execSync/execFileSync here. Those suspend the ENTIRE process --
// no I/O of any kind, including the host's own readiness/liveness handshake
// with the extension -- for as long as the child takes to exit. Resolving
// session metadata used to run four external spawns (git + three
// `agent-worktrees get` calls) sequentially via execSync/execFileSync, which
// could block the whole process for their combined timeout (up to ~29s).
// Confirmed live as the cause of a ready-timeout crash-loop: under
// facility-wide session contention (many Copilot processes competing for the
// same CPU), those blocking spawns ran long enough that the extension never
// reported ready in time, and the host killed + respawned it in a loop that
// never resolved on its own.
//
// `execFile` (callback-based, wrapped in a Promise) keeps every child async:
// the event loop stays free to service everything else while a spawn is in
// flight, so a slow one degrades registration latency, never extension
// readiness. The four calls also run in parallel (`Promise.all`), bounded by
// the single slowest one instead of their sum.
//
// The session binding supplies association only. Current machine naming comes
// from live OS/configuration observations, never a saved worktree/binding field.
// Missing associations retain the asynchronous, backoff-bounded CLI fallback.
import { exec, execFile } from "node:child_process";
import { constants, realpathSync } from "node:fs";
import { lstat, open } from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";

// Epoch seconds this process started (wall clock minus uptime), fixed once
// per *process*: pinned on the process object, so an extension reload in the
// same process (a new module instance) reports the bit-identical value rather
// than a recomputation milliseconds off -- the bridge can then compare exactly.
const STARTED_AT_KEY = Symbol.for("agent-bridge.processStartedAt");
const PROCESS_STARTED_AT = (process[STARTED_AT_KEY] ??= Date.now() / 1000 - process.uptime());

// This process's identity, known at load with no subprocess, so every
// registration -- the very first one included -- lets the bridge refuse a row
// that belongs to another incarnation instead of accepting an id-only write.
export function processIdentity() {
  return { pid: process.pid, process_started_at: PROCESS_STARTED_AT };
}

// --- Async CLI runner (non-blocking; never freezes the event loop) ---
// Mirrors the platform split the old synchronous runCli used (Windows
// binstubs are .cmd -> need a shell; POSIX can exec the binary directly) --
// `exec` with a manually-quoted command line on Windows avoids Node's
// shell:true-with-argv deprecation warning (args would otherwise be
// concatenated unescaped), while POSIX uses `execFile` directly with no
// shell at all.
export const METADATA_CLI_TIMEOUT_MS = 8_000;
// The refresher runs off the registration path, so it can wait out a slow host.
export const METADATA_REFRESH_CLI_TIMEOUT_MS = 60_000;

export function runCliAsync(bin, args, cwd, { timeoutMs = METADATA_CLI_TIMEOUT_MS } = {}) {
  return new Promise((resolve) => {
    try {
      if (process.platform === "win32") {
        const line = [bin, ...args.map((a) => `"${String(a).replace(/"/g, '""')}"`)].join(" ");
        exec(line, { cwd, timeout: timeoutMs, encoding: "utf-8", windowsHide: true }, (err, stdout) => {
          resolve(err ? null : String(stdout).trim());
        });
      } else {
        execFile(bin, args, { cwd, timeout: timeoutMs, encoding: "utf-8" }, (err, stdout) => {
          resolve(err ? null : String(stdout).trim());
        });
      }
    } catch {
      resolve(null);
    }
  });
}

// --- sessionStart worktree binding ---
// agent-worktrees' sessionStart hook records which worktree this session
// registered against in `<session-state>/<session id>/worktree-binding.json`.
// It is evidence for this session only: a file naming another session, or one
// that is malformed, oversized, linked or absent, is ignored. The Copilot CLI
// honors COPILOT_HOME for its state; the hook writes under the user's home, so
// both are checked (deduplicated).
const SESSION_BINDING_MAX_BYTES = 16 * 1024;
const bindingSegment = (value) => typeof value === "string"
  && /^[A-Za-z0-9._-]{1,160}$/.test(value) && value !== "." && value !== "..";

export function sessionStateRoots(env = process.env) {
  const roots = [];
  if (env.COPILOT_HOME && isAbsolute(env.COPILOT_HOME)) roots.push(join(env.COPILOT_HOME, "session-state"));
  const home = (process.platform === "win32" ? env.USERPROFILE : env.HOME) || homedir();
  if (home && isAbsolute(home)) roots.push(join(home, ".copilot", "session-state"));
  return [...new Set(roots)];
}

async function readBindingFile(root, sessionId) {
  let handle;
  try {
    const dir = join(root, sessionId);
    if (!(await lstat(dir)).isDirectory()) return null;
    const path = join(dir, "worktree-binding.json");
    if (!(await lstat(path)).isFile()) return null;
    handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW || 0) | (constants.O_NONBLOCK || 0));
    const info = await handle.stat();
    if (!info.isFile() || info.size > SESSION_BINDING_MAX_BYTES) return null;
    const bytes = Buffer.alloc(SESSION_BINDING_MAX_BYTES + 1);
    let length = 0;
    while (length < bytes.length) {
      const { bytesRead } = await handle.read(bytes, length, bytes.length - length, null);
      if (!bytesRead) break;
      length += bytesRead;
    }
    if (length > SESSION_BINDING_MAX_BYTES) return null;
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, length)));
  } catch {
    return null;
  } finally {
    if (handle) await handle.close().catch(() => {});
  }
}

// Resolves this session's `{ worktree_id, repo }` from its binding
// (each null when the binding lacks a usable value), or null without one.
export async function readSessionBinding(sessionId, { env = process.env } = {}) {
  if (!bindingSegment(sessionId)) return null;
  for (const root of sessionStateRoots(env)) {
    const binding = await readBindingFile(root, sessionId);
    if (!binding || typeof binding !== "object" || binding.sessionId !== sessionId) continue;
    const usable = (value) => (bindingSegment(value) ? value : null);
    const identity = {
      worktree_id: usable(binding.worktreeId), repo: usable(binding.project),
    };
    if (identity.worktree_id || identity.repo) return identity;
  }
  return null;
}

// --- One-time session metadata ---
// Deliberately never awaited on the critical path to readiness by the caller
// (see extension.mjs's load-time init) -- state.meta starts unset and this
// fills it in whenever it resolves; register()'s payload spread tolerates an
// absent/partial value and self-corrects on the next heartbeat.
export async function resolveMetadataAsync({
  cwd = process.cwd(), env = process.env, sessionId = null, timeoutMs = METADATA_CLI_TIMEOUT_MS,
  machineResolver = resolveMachineAsync, extensionPluginRoot,
} = {}) {
  if (env.AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT) {
    const root = env.AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT;
    if (!isAbsolute(root) || typeof extensionPluginRoot !== "string"
        || !isAbsolute(extensionPluginRoot) || realpathSync(root) !== realpathSync(extensionPluginRoot)
        || !env.AGENT_BRIDGE_SCOPE_ID
        || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(env.AGENT_BRIDGE_NATIVE_SESSION_ID || "")) {
      throw new Error("Portable receiving metadata differs from its selected launch");
    }
    // The provider owns this qualified scope. It is not a guest Worktrees
    // allocation or a claim that the venue has a registered logical machine.
    return {
      machine: null, cwd, repo: null, branch: null, role: null, driven_by: null,
      worktree_id: env.AGENT_BRIDGE_SCOPE_ID, receiving_scope_id: env.AGENT_BRIDGE_SCOPE_ID,
      ...processIdentity(),
    };
  }
  // The binding answers without a subprocess; only what it lacks is looked up.
  // An explicit AGENT_BRIDGE_SCOPE_ID still outranks both for the worktree.
  const bound = await readSessionBinding(sessionId, { env });
  const getAsync = (key) => runCliAsync("agent-worktrees", ["get", key], cwd, { timeoutMs }); // marketplace-isolation: allow agent-worktrees-management
  const known = (value, key) => (value ? Promise.resolve(value) : getAsync(key));
  const [branch, sessionScopeId, machine, repo] = await Promise.all([
    runCliAsync("git", ["rev-parse", "--abbrev-ref", "HEAD"], cwd),
    known(env.AGENT_BRIDGE_SCOPE_ID || bound?.worktree_id, "session-scope-id"),
    machineResolver({ cwd, timeoutMs }),
    known(bound?.repo, "project"),
  ]);
  return {
    // A physical hostname cannot identify the logical Windows/WSL owner.
    // Leave failed lookup pending for the bounded identity refresher.
    machine: machine || null,
    cwd,
    // A venue launcher may pin a venue-qualified identity (e.g.
    // `anchor-<repo>@<codespace>`) via AGENT_BRIDGE_SCOPE_ID so several
    // venues of the same repo stay distinguishable on the host bridge.
    worktree_id: env.AGENT_BRIDGE_SCOPE_ID || sessionScopeId || null,
    repo,
    branch: branch || null,
    // process.pid is the extension host process -- a liveness hint, not the
    // copilot PID. The durable key is session_id; liveness is heartbeat-based.
    // With the start time, constant for this process (also across an
    // in-process resume), the bridge tells it from an unrelated process that
    // later reuses the pid.
    ...processIdentity(),
    role: null,
    // D4: who is steering this session, if an agent embodied it (set by
    // `agent-worktrees embody --driver`). Surfaces the "driven by <agent>"
    // banner so a human dropping in via Neuron Forge sees who's at the wheel.
    // Absent/null for an operator-launched session.
    driven_by: env.AGENT_BRIDGE_DRIVEN_BY || null,
  };
}

export async function resolveMachineAsync({
  cwd = process.cwd(), timeoutMs = METADATA_CLI_TIMEOUT_MS, lookup = async () => undefined,
} = {}) {
  const current = await lookup().catch(() => null);
  if (current !== undefined) return current;
  return runCliAsync("agent-worktrees", ["get", "machine"], cwd, { timeoutMs }); // marketplace-isolation: allow agent-worktrees-management
}

export async function refreshRegistrationMetadata(state, machineResolver = resolveMachineAsync) {
  if (state.meta?.receiving_scope_id === state.meta?.worktree_id && state.meta?.receiving_scope_id) {
    return registrationMetadata(state.meta);
  }
  const machine = await machineResolver({ cwd: state.meta?.cwd || process.cwd() }).catch(() => null);
  state.meta = { ...state.meta, machine: machine || null };
  return registrationMetadata(state.meta);
}

// The identity fields a registration needs for the bridge (and its clients) to
// place the session in its worktree: when the load-time lookup misses them (a
// slow or failed `agent-worktrees get` under load, as right after a reboot),
// the session would otherwise stay registered without them for its whole life.
const IDENTITY_FIELDS = ["machine", "worktree_id", "repo"];
export const METADATA_RETRY_MIN_MS = 30_000;
export const METADATA_RETRY_MAX_MS = 10 * 60_000;

export function registrationMetadata(meta) {
  if (meta?.receiving_scope_id && meta.receiving_scope_id === meta.worktree_id) {
    const { receiving_scope_id: _scope, ...wire } = meta;
    return wire;
  }
  if (IDENTITY_FIELDS.every((key) => meta?.[key])) return meta;
  return {
    ...meta, machine: null, worktree_id: null, repo: null,
    clear_targeting_metadata: true,
  };
}

// ``refresh()`` fills in an association that is still missing, and
// never replaces a known association. Every call re-reads the sessionStart
// binding (no subprocess); the CLI lookup runs at most once per backoff (30 s
// doubling to 10 min) with the longer refresh timeout, and once a few lookups
// still leave the identity incomplete it is logged, once. Resolves true when
// it filled something in (the next registration then carries it).
export const METADATA_WARN_AFTER_ATTEMPTS = 3;

export function metadataRefresher(state, resolve = resolveMetadataAsync, now = Date.now, {
  env = process.env, log = () => {}, warnAfter = METADATA_WARN_AFTER_ATTEMPTS,
} = {}) {
  let nextAt = 0;
  let delay = METADATA_RETRY_MIN_MS;
  let running = false;
  let attempts = 0;
  let warned = false;
  const missingFields = () => {
    const meta = state.meta || {};
    if (meta.receiving_scope_id && meta.receiving_scope_id === meta.worktree_id) return [];
    return ["worktree_id", "repo"].filter((k) => !meta[k]);
  };
  const fill = (fresh) => {
    const filled = {};
    for (const k of missingFields()) if (fresh && fresh[k]) filled[k] = fresh[k];
    if (!Object.keys(filled).length) return false;
    state.meta = { ...(state.meta || fresh), ...filled };
    return true;
  };
  return async () => {
    if (!missingFields().length) return false;
    const bound = await readSessionBinding(state.sessionId, { env });
    const fromBinding = fill(bound && { ...bound, worktree_id: env.AGENT_BRIDGE_SCOPE_ID || bound.worktree_id });
    if (!missingFields().length || running || now() < nextAt) return fromBinding;
    running = true;
    try {
      const fresh = await resolve({
        cwd: state.meta?.cwd, sessionId: state.sessionId, env,
        timeoutMs: METADATA_REFRESH_CLI_TIMEOUT_MS, machineResolver: async () => null,
      }).catch(() => null);
      attempts += 1;
      const fromLookup = fill(fresh);
      if (fromLookup) {
        delay = METADATA_RETRY_MIN_MS;
      } else {
        nextAt = now() + delay;
        delay = Math.min(delay * 2, METADATA_RETRY_MAX_MS);
      }
      const missing = missingFields();
      if (missing.length && attempts >= warnAfter && !warned) {
        warned = true;
        log(`session identity still incomplete after ${attempts} lookups (missing: ${missing.join(", ")}); `
          + "worktree-addressed targeting stays unavailable until it resolves; still retrying");
      }
      return fromLookup || fromBinding;
    } finally {
      running = false;
    }
  };
}
