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
  openSync, closeSync, statSync, readdirSync, linkSync, ftruncateSync, writeSync,
} from "node:fs";
import { join, basename, dirname, resolve } from "node:path";
import { execFileSync, execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import {
  CONTINUATION_DIRECTIVE,
  HANDOFF_MECHANISM_AWARENESS,
  leadFrom,
  buildCutoverSeed,
} from "./cutover-seed.mjs";
import { supersededHandoffIds } from "./handoff-tasks.mjs";
import { AGENT_WORKTREES_QUERY_TIMEOUT_MS } from "./cli-timeouts.mjs";
import { DEFAULT_HANDOFF_MODE, automaticHandoffEnabled } from "./mode.mjs";

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
const execFileAsync = promisify(execFile);

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
    "agent-bridge": {
      relative:
        process.platform === "win32"
          ? join("bin", "agent-bridge.ps1")
          : join("bin", "agent-bridge"),
      module: "agent_bridge",
      runtimeRoot: ".agent-bridge",
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

export function resolveRuntimePython(resolved, env, cwd, timeout, allowProvision = true) {
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
    if (!allowProvision) {
      // Best-effort/fire-and-forget callers (e.g. the force-tier auto-sync
      // path, which has no agent judgment in the loop and must never risk
      // blocking the event loop for the up-to-RUNTIME_PROVISION_TIMEOUT_MS
      // self-provisioning step below) opt out of provisioning entirely: an
      // unprovisioned runtime is reported as unavailable, not waited on.
      throw new Error("payload runtime is not yet provisioned (provisioning skipped)");
    }
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
  const { cwd, timeout = 15000, allowProvision = true, ...extra } = opts;
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
      resolved, env, cwd, timeout, allowProvision,
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

// Mirrors agent-worktrees' own URL-userinfo/auth-header redaction
// (`repos.py`'s `_redact`) -- a fetch/rebase failure's stderr can include
// the remote URL verbatim, and a URL with embedded credentials
// (`https://user:pass@host/...`) or a bearer/basic auth header would
// otherwise leak through this error text into the extension log and CLI
// output.
export function redactGitDiagnostics(text) {
  return text
    .replace(/(https?:\/\/)[^/@\s]+@/gi, "$1<redacted>@")
    .replace(/(Authorization:\s*(?:Basic|Bearer)\s+)\S+/gi, "$1<redacted>");
}

function describeSyncError(error) {
  const message = error instanceof Error ? error.message : String(error);
  return redactGitDiagnostics(message);
}

// Mirrors agent-worktrees' own `repository_identity_env()`
// (plugins/agent-worktrees/src/agent_worktrees/git_ops.py) for the same
// reason: these probes and the delegated sync always supply their checkout
// explicitly via `cwd`, but an inherited GIT_DIR/GIT_WORK_TREE/
// GIT_INDEX_FILE/etc. can silently override that selection and make the
// safety check inspect (or the sync mutate) a different repository than the
// one this function was asked to sync. GIT_TERMINAL_PROMPT=0 additionally
// keeps an unattended sync from ever blocking on a credential prompt.
const REPOSITORY_CONTEXT_ENV = new Set([
  "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES",
  "GIT_COMMON_DIR", "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
  "GIT_DIR", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_GRAFT_FILE",
  "GIT_IMPLICIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_INTERNAL_SUPER_PREFIX",
  "GIT_NAMESPACE", "GIT_NO_REPLACE_OBJECTS", "GIT_OBJECT_DIRECTORY",
  "GIT_PREFIX", "GIT_QUARANTINE_PATH", "GIT_REPLACE_REF_BASE",
  "GIT_SHALLOW_FILE", "GIT_WORK_TREE",
]);
export function sanitizedGitEnv(baseEnv = process.env) {
  const env = { ...baseEnv };
  for (const name of Object.keys(env)) {
    const upper = name.toUpperCase();
    if (
      REPOSITORY_CONTEXT_ENV.has(upper)
      || upper.startsWith("GIT_CONFIG_KEY_")
      || upper.startsWith("GIT_CONFIG_VALUE_")
    ) {
      delete env[name];
    }
  }
  env.GIT_TERMINAL_PROMPT = "0";
  return env;
}

// Ephemeral git config overrides via the GIT_CONFIG_* env protocol
// (equivalent to `-c key=value` on every git invocation in this
// environment, including ones a delegated CLI spawns internally):
// - `rebase.autoStash=false`: for the AGENT-WORKTREES-delegated sync path,
//   which (unlike plainGitSync's own rebase, which ALSO passes
//   `--no-autostash` directly as a redundant belt-and-braces measure) has
//   no CLI flag of its own to ask for this.
// - `core.hooksPath=/dev/null`: mirrors agent-worktrees' own established
//   convention (`git_ops.py`'s `no_hooks` option) for trusted, mechanical
//   plumbing -- a repo's client-side guard hooks (`pre-rebase` etc.) must
//   not be able to block or mutate this unattended sync. Git for Windows'
//   POSIX layer resolves this path the same way agent-worktrees already
//   relies on cross-platform.
// Call AFTER sanitizedGitEnv(), which strips any inherited
// GIT_CONFIG_KEY_*/VALUE_* first, so these are the only overrides present.
function withUnattendedSyncGitConfig(env) {
  return {
    ...env,
    GIT_CONFIG_COUNT: "2",
    GIT_CONFIG_KEY_0: "rebase.autoStash",
    GIT_CONFIG_VALUE_0: "false",
    GIT_CONFIG_KEY_1: "core.hooksPath",
    GIT_CONFIG_VALUE_1: "/dev/null",
  };
}

// `--git-path` resolves the correct per-worktree location for either rebase
// strategy (merge-based `rebase -i` vs. the older apply-based path).
// Returns { inProgress, error } rather than throwing so callers can
// distinguish "definitely mid-rebase" from "couldn't tell, fail closed" with
// one shape. Deliberately cheap/side-effect-free so it is safe to call twice
// (see attemptWorktreeSync's TOCTOU note on its two call sites).
async function rebaseInProgress(cwd) {
  const env = sanitizedGitEnv();
  try {
    const [mergeDir, applyDir] = await Promise.all([
      execFileAsync("git", ["rev-parse", "--git-path", "rebase-merge"], {
        cwd, encoding: "utf-8", timeout: 5000, env,
      }),
      execFileAsync("git", ["rev-parse", "--git-path", "rebase-apply"], {
        cwd, encoding: "utf-8", timeout: 5000, env,
      }),
    ]);
    return {
      inProgress: existsSync(resolve(cwd, mergeDir.stdout.trim()))
        || existsSync(resolve(cwd, applyDir.stdout.trim())),
      error: false,
    };
  } catch {
    // If we can't even ask git where its rebase state would live, treat that
    // the same as "not a git checkout" would have -- fail closed by
    // reporting in-progress rather than guessing.
    return { inProgress: true, error: true };
  }
}

// Best-effort, same-process-family serialization for attemptWorktreeSync's
// own check-then-sync window: a filesystem exclusive-create lock scoped to
// this worktree's git dir. This closes the realistic TOCTOU risk (this
// plugin's own force-tier and skill-guided sync paths racing each other
// across concurrent tool calls/sessions on the SAME worktree) but is
// deliberately honest about its limit -- it cannot and does not claim to
// serialize against an operator (or unrelated tool) running `git rebase` by
// hand at the exact wrong instant; no lock this plugin creates can compel an
// external actor to honor it. That residual, unavoidable-without-owning-
// agent-worktrees-itself gap is why rebaseInProgress() is still rechecked
// immediately before the sync exec even while this lock is held.
//
// STALE_LOCK_MS: an ultimate, last-resort fallback threshold ONLY for when
// the recorded holder's identity genuinely cannot be determined (its PID
// can't be parsed from the lock, its start time can't be queried, or
// querying process liveness itself errors) -- see `isProcessAlive` and
// `processStartTimeMs` below. The PRIMARY reclaim criterion is PROCESS
// IDENTITY (pid + start time), not age: an aged-but-still-running holder (a
// slow network, a suspended process -- not crashed) must never be reclaimed
// out from under itself, because that is exactly what forced the whole
// chain of release-side races rounds 16-20 kept surfacing (a resumed live
// holder finding its lock already replaced). Bare pid liveness alone is not
// enough either (round 23 finding): `process.kill(pid, 0)` only proves SOME
// process holds that pid right now, not that it is the SAME process that
// created the lock -- the OS can and does reuse pids. Comparing the
// holder's recorded START TIME against the CURRENT process occupying that
// pid distinguishes "still the same process, just slow" (never reclaim, no
// matter how old) from "a different process now recycling that pid" (safe
// to reclaim immediately, regardless of age).
const STALE_LOCK_MS = 60 * 60 * 1000;

// True if `pid` is still running. `process.kill(pid, 0)` sends no signal
// (POSIX) / merely checks accessibility (Windows via Node's libuv layer) --
// it only tests existence. Fails OPEN (reports alive) on any inability to
// tell (e.g. EPERM for a pid owned by another user), matching this whole
// lock's fail-closed philosophy: never reclaim on ambiguous evidence.
// Exported for waitForWorktreeSyncToSettle below, which needs the same
// liveness check to decide whether a still-present sync lock is worth
// continuing to wait on.
export function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code !== "ESRCH";
  }
}

// Queries the OS's own record of when `pid` started, normalized to epoch
// milliseconds -- used as a process-identity fingerprint alongside the pid
// itself (a bare pid, once its original holder exits, can be reused by a
// completely unrelated live process; comparing start times distinguishes
// that from "still the same process"). Both the write side (this
// invocation queries its OWN pid at acquire time) and the read side (a
// later invocation queries the RECORDED holder's pid) go through this SAME
// function, so both sides use identical measurement precision -- comparing
// Node's own `process.uptime()`-derived estimate against the OS's official
// answer for someone else's pid would not reliably match even for the
// exact same process. Returns null if it cannot be determined (unsupported
// platform tool, permissions, the pid no longer exists).
export async function processStartTimeMs(pid) {
  try {
    if (process.platform === "win32") {
      const { stdout } = await execFileAsync(
        windowsPowerShell(),
        [
          "-NoProfile", "-Command",
          `(Get-Process -Id ${pid} -ErrorAction Stop).StartTime.ToFileTimeUtc()`,
        ],
        { timeout: 5000, encoding: "utf-8" },
      );
      const fileTimeTicks = Number(stdout.trim());
      if (!Number.isFinite(fileTimeTicks)) return null;
      // FILETIME: 100ns intervals since 1601-01-01; 11644473600000 is the
      // epoch offset in ms between that and the Unix epoch.
      return Math.round(fileTimeTicks / 10000 - 11644473600000);
    }
    const { stdout } = await execFileAsync(
      "ps", ["-o", "lstart=", "-p", String(pid)],
      { timeout: 5000, encoding: "utf-8" },
    );
    const parsed = Date.parse(stdout.trim());
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

// Writes this process's own identity token into an already-open,
// freshly-exclusively-created lock fd: a synchronous placeholder FIRST (so
// the file exists and is non-empty the INSTANT the exclusive create
// succeeds, before any async work), then the full identity token (which
// needs an async OS query -- processStartTimeMs, itself a subprocess spawn
// on both platforms) via an in-place truncate+rewrite. Round-31 finding:
// computing the full token BEFORE the exclusive create meant the lock file
// did not exist at all for that query's entire duration -- an outside
// observer (waitForWorktreeSyncToSettle) reading "absent" during that
// window is indistinguishable from "no sync ever started".
//
// Round-32 finding: the placeholder must still be PARSEABLE as
// `pid-startTime-` -- an unparseable placeholder (e.g. bare
// `${pid}-pending`) falls into acquireLock's own age-only reclaim
// fallback, so a process merely SUSPENDED here (not dead) for longer than
// STALE_LOCK_MS could have its own in-progress lock reclaimed out from
// under it, then still finish writing its "final" token on resume,
// running concurrently with the replacement holder. Using
// `${pid}-unknown-pending` instead is parseable (pid, but a deliberately
// unknown start time) -- acquireLock's contention logic treats a live pid
// with no recorded start time as having no basis for identity comparison
// and fails closed (never reclaims), exactly the protection a
// genuinely-alive-but-slow placeholder holder needs; a truly DEAD holder
// (this process crashed before finishing) is still reclaimed immediately
// via the ordinary `!isProcessAlive` check, unaffected by this format
// change.
//
// Shared by BOTH the fresh-create path and the reclaim-success path
// below -- each is an independent act of becoming the new holder and gets
// its own token.
async function writeLockToken(fd) {
  writeFileSync(fd, `${process.pid}-unknown-pending`);
  const ownStartTime = await processStartTimeMs(process.pid);
  const token = `${process.pid}-${ownStartTime ?? "unknown"}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  ftruncateSync(fd, 0);
  writeSync(fd, token, 0);
  return token;
}

// Exclusively creates lockPath and writes this process's full identity
// token into it (via writeLockToken), cleaning up (closing the fd,
// removing the lock) if anything after the exclusive create fails --
// round-32 finding, and its own "previously missed" follow-up: BOTH the
// fresh-create path AND the reclaim-success path in acquireLock below
// perform this exact sequence (each an independent act of becoming the
// new holder), and each needs the SAME cleanup-on-failure guarantee, not
// just the first one. Propagates the ORIGINAL openSync error (e.g.
// EEXIST) uncaught -- only a failure AFTER a successful create triggers
// cleanup here.
async function createLockWithToken(lockPath) {
  const fd = openSync(lockPath, "wx");
  try {
    const token = await writeLockToken(fd);
    return { fd, token };
  } catch (writeError) {
    // The exclusive create itself succeeded (this process now genuinely
    // OWNS lockPath), but finishing its token initialization failed --
    // without cleanup here, the caller never gets a `{fd, token}` to
    // release, leaking the fd and leaving a permanently-stuck lock behind
    // for every future sync attempt on this worktree.
    try { closeSync(fd); } catch { /* already closed */ }
    try { unlinkSync(lockPath); } catch { /* best-effort */ }
    throw writeError;
  }
}

async function acquireLock(lockPath) {
  // Ownership token written into the lock file's content, not just its
  // presence at a path -- see the release-time check in
  // withWorktreeSyncLock's finally block for why path-only ownership isn't
  // enough. See writeLockToken's and createLockWithToken's own doc
  // comments for the placeholder-first write sequence and the round-31/32
  // findings they close.
  try {
    return await createLockWithToken(lockPath);
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
    let holderPid = null;
    let holderStartTime = null;
    let observedContent = null;
    let readFailed = false;
    try {
      observedContent = readFileSync(lockPath, "utf-8");
      const match = observedContent.match(/^(\d+)-(\d+|unknown)-/);
      if (match) {
        holderPid = Number(match[1]);
        holderStartTime = match[2] === "unknown" ? null : Number(match[2]);
      }
    } catch {
      // Couldn't read the existing lock's content at all (permissions, a
      // transient I/O error, an unsupported filesystem) -- this is
      // DIFFERENT from a successful read that simply didn't parse a pid
      // (a corrupt/legacy lock format, handled by the age-only fallback
      // below). An unreadable lock provides no evidence the holder is
      // gone or old, so it must fail closed as contention, never fall
      // through to reclaim-by-age.
      readFailed = true;
    }
    let reclaimable = false;
    if (holderPid !== null) {
      if (!isProcessAlive(holderPid)) {
        // Confirmed dead -- always reclaimable, no age check needed.
        reclaimable = true;
      } else if (holderStartTime !== null) {
        // A live pid: compare start times to tell "still the same process"
        // from "a different process now recycling that pid". Query through
        // the SAME function used to record it, for measurement parity.
        const currentStartTime = await processStartTimeMs(holderPid);
        if (currentStartTime !== null && currentStartTime !== holderStartTime) {
          // Confirmed different process -- safe to reclaim regardless of
          // age; no ambiguity to wait out.
          reclaimable = true;
        }
        // Either the start times match (genuinely still the same process --
        // NEVER reclaim, no matter its age) or the current start time
        // couldn't be queried (ambiguous -- fail closed, do not reclaim).
      }
      // A live pid with no recorded start time (an older/foreign lock
      // format) has no basis for identity comparison -- fail closed
      // (never reclaim) rather than guessing from age alone (the exact
      // round-23 finding: age-based reclaim of a live-but-slow holder
      // reintroduces the very race this whole scheme exists to prevent).
    } else if (readFailed) {
      // Unreadable, not merely unparseable -- fail closed (see the comment
      // at the catch site above). Do NOT fall through to the age-only
      // heuristic: an unreadable lock is exactly as likely to belong to a
      // genuinely live holder as a stale one, and age alone cannot tell
      // them apart.
      reclaimable = false;
    } else {
      // Read succeeded but no parseable pid (a corrupt/legacy lock format)
      // -- fall back to the age-only heuristic as a last resort so a
      // genuinely unreadable-as-in-unparseable abandoned lock can still
      // self-heal eventually.
      let ageMs;
      try {
        ageMs = Date.now() - statSync(lockPath).mtimeMs;
      } catch {
        // Could not stat the lock we just failed to create exclusively --
        // NOT necessarily gone (a permission error, a transient FS issue,
        // or it vanishing between the failed open and this stat all look
        // the same here). Treating that ambiguity as reclaimable would
        // risk unlinking a lock another process re-created a moment ago.
        // Fail closed as contention instead -- a lock that is genuinely
        // gone will simply succeed to acquire on the NEXT attempt.
        ageMs = -Infinity;
      }
      reclaimable = ageMs > STALE_LOCK_MS;
    }
    if (!reclaimable) throw error;
    // Confirmed dead, confirmed a different process, or unparseable-and-old
    // -- reclaim it, but atomically: renaming lockPath AWAY (not unlinking
    // it) is the only step here the OS actually guarantees single-winner
    // semantics for. If two processes race to reclaim the SAME stale lock,
    // only one `renameSync` can succeed (the source path stops existing
    // the instant the first one wins); the loser's rename throws ENOENT and
    // it must NOT proceed to acquire -- an unconditional unlink+open here
    // (an earlier approach) let a second reclaimer delete the FIRST
    // reclaimer's fresh lock and acquire its own, letting both run
    // concurrently and defeating the whole point of the lock.
    //
    // The rename alone is not sufficient, though: it only guarantees ONE
    // winner among contenders racing on the SAME (still-stale) source --
    // it says nothing about WHAT was actually at that path when this
    // contender's rename ran. Two contenders can both read the same stale
    // lock and both decide "reclaimable" before either acts; if contender A
    // wins its rename+recreate first, contender B's rename (reached later)
    // would then claim A's brand-new ACTIVE lock instead of the original
    // stale one, and B would proceed to acquire concurrently with A. Verify
    // the claimed content still matches what this decision was based on
    // before proceeding -- a mismatch means someone else already won and
    // replaced it, so this contender must restore it (no-clobber) and
    // report contention rather than destroying an active lock.
    const graveyardPath = `${lockPath}.stale-${process.pid}-${Date.now()}`;
    try {
      renameSync(lockPath, graveyardPath);
    } catch {
      // Lost the race to reclaim (someone else's rename won first, or the
      // lock is already gone/held again) -- this is genuine contention,
      // not something to retry past.
      throw error;
    }
    let claimedContent;
    try {
      claimedContent = readFileSync(graveyardPath, "utf-8");
    } catch {
      claimedContent = null;
    }
    // Only verify when the initial read actually succeeded -- if it didn't
    // (a rare transient failure, distinct from "couldn't parse"), there is
    // no real baseline to compare against, and the age-based fallback
    // decision above already accounted for that case on its own terms.
    if (observedContent !== null && claimedContent !== observedContent) {
      // Claimed something OTHER than the stale lock this decision was
      // based on -- restore it (see restoreClaimedLock's own doc comment)
      // rather than destroying whatever is actually there now, then
      // report contention.
      restoreClaimedLock(graveyardPath, lockPath);
      throw error;
    }
    try { unlinkSync(graveyardPath); } catch { /* best-effort cleanup */ }
    // Only the single winning reclaimer reaches here, so this create is
    // guaranteed fresh -- no residual race with another reclaimer. Uses
    // the SAME cleanup-on-failure create+token sequence as the
    // fresh-create path above (createLockWithToken) -- this is an
    // independent act of becoming the new holder, with its own freshly-
    // computed token, and needs the same guarantee against leaking a
    // descriptor/placeholder if token initialization fails here too
    // ("previously missed" round-33 follow-up to round 32's fix, which
    // only covered the fresh-create path).
    return await createLockWithToken(lockPath);
  }
}

// Resolves the shared per-worktree sync-lock path (the same lock
// attemptWorktreeSync's own withWorktreeSyncLock uses), so any OTHER
// caller that needs to observe (not acquire) that lock -- e.g.
// waitForWorktreeSyncToSettle below -- resolves it identically rather than
// duplicating the git invocation.
async function resolveWorktreeSyncLockPath(cwd) {
  const gitPath = await execFileAsync(
    "git", ["rev-parse", "--git-path", "context-handoff-sync.lock"],
    { cwd, encoding: "utf-8", timeout: 5000, env: sanitizedGitEnv() },
  );
  return resolve(cwd, gitPath.stdout.trim());
}

async function withWorktreeSyncLock(cwd, fn) {
  let lockPath;
  try {
    lockPath = await resolveWorktreeSyncLockPath(cwd);
  } catch {
    // Can't even resolve the lock location -- fail closed the same way the
    // rebase check does, rather than proceeding unserialized.
    return {
      attempted: false,
      synced: false,
      reason: "could not resolve a lock path for this worktree; automatic sync was skipped to be safe",
    };
  }
  let lock;
  try {
    lock = await acquireLock(lockPath);
  } catch {
    return {
      attempted: false,
      synced: false,
      reason: "another sync attempt for this worktree is already in progress; automatic sync was skipped",
    };
  }
  try {
    return await fn();
  } finally {
    try { closeSync(lock.fd); } catch { /* best-effort */ }
    releaseLock(lockPath, lock.token);
  }
}

// Polls the SAME shared worktree-sync lock attemptWorktreeSync itself
// acquires, WITHOUT ever trying to acquire it -- a read-only observer for a
// caller on either side of a handoff cutover that needs to know "is a sync
// still genuinely in flight for this worktree right now?" before it reads
// or trusts the working tree's contents:
//   - the predecessor's force-tier trigger (extension.mjs's
//     autoForceHandoff) uses this to decide when it is safe to arm live
//     pickup, instead of a blind fixed-duration wait;
//   - a successor's consume_handoff call uses this defensively at startup,
//     in case pickup was armed (or a manual /consume-handoff was run)
//     while the predecessor's sync was still settling.
// Bounded by `timeoutMs` regardless of outcome -- a slow/unreachable
// remote must never turn this into an unbounded wait on EITHER side. Never
// reclaims or otherwise mutates the lock; that remains attemptWorktreeSync's
// own job alone.
//
// Start-barrier note (`startGraceMs`): attemptWorktreeSync's own path to
// actually creating the lock file has several async steps of its own
// (resolving the lock path, querying the holder's own start time) -- a
// caller that KNOWS it just dispatched a sync attempt a moment ago (the
// force-tier's own beforeArmPickup, the only caller that can make that
// claim) could otherwise observe "no lock file yet" and wrongly conclude
// "already settled" before the sync even had a chance to register itself.
// Passing a nonzero `startGraceMs` makes that caller's early
// absent/unparseable readings NOT trusted as settled until either genuine
// evidence of a live holder is observed (no further grace needed from
// then on) or the grace window itself elapses. Defaults to 0 (trust the
// very first reading immediately) for a caller with NO such certainty --
// e.g. consume_handoff's defensive startup check, which has no reason to
// believe a sync is in flight at all and must stay fast in the overwhelmingly
// common case where none is.
//
// Fail-closed on read errors: readFileSync failing with anything OTHER
// than ENOENT (permissions, a sharing violation, transient I/O) proves
// NOTHING about whether a sync is in flight -- it is silently skipped as
// an inconclusive reading (never counted as "absent") rather than treated
// as evidence of settlement, so a genuinely-still-active lock this
// process merely failed to read once cannot be mistaken for "done".
// Present-but-unparseable content is treated the SAME way -- ALWAYS
// inconclusive, never settled, regardless of any grace window -- since
// the lock briefly holds non-final content between its own exclusive
// create and its full identity token being written (see acquireLock's
// placeholder-write comment); only a genuinely ABSENT lock (ENOENT) can
// ever be trusted quickly.
// A confirmed-dead holder only proves no process currently holds the
// lock -- it does NOT prove the worktree itself is consistent (a process
// can crash mid-rebase and leave .git/rebase-merge or a half-applied
// conflict behind), so that case additionally checks for an in-progress
// rebase and reports it via `needsInspection` rather than implying the
// tree is trustworthy.
export async function waitForWorktreeSyncToSettle(cwd, { timeoutMs = 15000, pollMs = 500, startGraceMs = 0 } = {}) {
  let lockPath;
  try {
    lockPath = await resolveWorktreeSyncLockPath(cwd);
  } catch {
    // Not a git worktree, or git itself unavailable -- nothing to wait on.
    return { waited: false, settled: true, reason: "could not resolve a lock path for this worktree" };
  }
  const deadline = Date.now() + Math.max(0, timeoutMs);
  const startGraceDeadline = Date.now() + Math.min(Math.max(0, startGraceMs), Math.max(0, timeoutMs));
  const gracePollMs = Math.min(pollMs, 100);
  let everObservedLive = false;
  for (;;) {
    let content = null;
    let inconclusive = false;
    try {
      content = readFileSync(lockPath, "utf-8");
    } catch (error) {
      if (error?.code !== "ENOENT") {
        // Any read failure OTHER than "genuinely does not exist"
        // (permissions, a sharing violation, transient I/O) proves
        // NOTHING about whether a sync is in flight -- treating it the
        // same as "absent" could conclude settled while an active lock
        // is actually still there, just unreadable to THIS read attempt.
        inconclusive = true;
      }
      // ENOENT: content stays null, meaning genuinely absent (below).
    }
    if (!inconclusive && content !== null) {
      const match = content.match(/^(\d+)-(\d+|unknown)-/);
      const holderPid = match ? Number(match[1]) : null;
      if (holderPid !== null && isProcessAlive(holderPid)) {
        everObservedLive = true;
        // Fall through to poll again -- waiting for it to clear.
      } else if (holderPid !== null) {
        // Present, parseable, confirmed dead -- see this function's own
        // doc comment for why that alone is not proof of a consistent
        // worktree. Treat an inability to even ask git the same way as
        // rebaseState.inProgress itself does elsewhere in this file --
        // fail closed (needsInspection: true) rather than assume clean.
        const rebaseState = await rebaseInProgress(cwd);
        // Round-32 finding: that await gave another contender time to
        // reclaim this EXACT dead lock and start a genuinely NEW, live
        // sync -- re-read before trusting the "settled" conclusion this
        // decision was based on. A mismatch means someone else has since
        // acted on it; fall through to the poll loop instead of returning
        // stale information, so the NEXT iteration re-evaluates the
        // CURRENT state (which may immediately show live, correctly
        // setting everObservedLive and waiting for it to clear).
        let recheckContent = null;
        try {
          recheckContent = readFileSync(lockPath, "utf-8");
        } catch { /* absent now -- also handled by falling through */ }
        if (recheckContent === content) {
          return {
            waited: true,
            settled: true,
            reason: "holder-dead",
            needsInspection: rebaseState.inProgress || rebaseState.error,
          };
        }
        inconclusive = true;
      } else {
        // Present but UNPARSEABLE -- ALWAYS inconclusive (see doc
        // comment), never settled from this reading alone, regardless of
        // any grace window already elapsed.
        inconclusive = true;
      }
    }
    if (!inconclusive && content === null) {
      if (everObservedLive) {
        // Was genuinely observed live at least once, and is now
        // genuinely absent -- this really is settled (released cleanly
        // since we last saw it in flight), not a startup artifact.
        return { waited: true, settled: true, reason: "lock-absent-after-live" };
      } else if (Date.now() >= startGraceDeadline) {
        // Never once observed live, and the startup grace window (zero
        // by default) has fully elapsed -- genuinely nothing in flight
        // (never started, or genuinely absent from before this wait
        // began).
        return { waited: true, settled: true, reason: "lock-absent" };
      }
    }
    // Still within an opted-in startup grace window with no live evidence
    // yet, an inconclusive/unparseable/read-error reading this iteration,
    // or observed live and still waiting for it to clear -- keep polling.
    if (Date.now() >= deadline) {
      return { waited: true, settled: false, reason: "timeout" };
    }
    const nextPollMs = everObservedLive ? pollMs : gracePollMs;
    await sleep(Math.min(nextPollMs, Math.max(0, deadline - Date.now())));
  }
}

// Releases a worktree sync lock atomically against a concurrent reclaimer,
// rather than a separate read-then-unlink (itself a TOCTOU: another
// invocation could reclaim and replace the path after the read returns this
// invocation's own token but before the unlink runs, letting this invocation
// delete the new holder's active lock). `renameSync` atomically CLAIMS
// whatever is at `lockPath` right now -- the same single-winner guarantee
// `acquireLock`'s reclaim step relies on -- so the content check below only
// ever inspects content this invocation has already exclusively taken
// possession of; nothing else can be racing over the SAME bytes by the time
// it runs.
//
// The "claimed content isn't ours" branch below is now structurally rare:
// acquireLock only ever reclaims a CONFIRMED-DEAD holder (by pid liveness),
// or an unreadable/corrupt lock as a last resort -- a genuinely live holder
// (however long it has been running) is never reclaimed out from under
// itself, so this invocation reaching its own release after being displaced
// should no longer happen in normal operation. It remains as defense in
// depth for the corrupt-lock fallback path.
// Restores a claimed (renamed-away) lock's content back to its original
// path via a no-clobber `linkSync` (never `renameSync`, which silently
// overwrites an existing destination on POSIX and could clobber a fresh
// lock a THIRD actor created at `lockPath` in the interim). Shared by
// `acquireLock`'s reclaim-verification and `releaseLock`'s foreign-content
// restore, which both hit this exact situation. Returns true if the
// restore succeeded, false otherwise -- the caller decides in EITHER case
// whether it is safe to remove `claimedPath` (only when the restore
// succeeded, or the failure was the expected "already re-occupied" EEXIST
// case; any OTHER `linkSync` failure falls back to a read + no-clobber
// EXCLUSIVE-create write, since `linkSync` can fail for reasons OTHER than
// "already occupied" too -- e.g. `ENOTSUP`/`EPERM` on a filesystem without
// hard-link support -- so its failure alone is never proof `lockPath` is
// actually empty; only an exclusive-create's own `EEXIST` is. A plain
// `renameSync` fallback here would silently replace a genuinely active
// lock in exactly that case, which is the bug this now avoids).
function restoreClaimedLock(claimedPath, lockPath) {
  try {
    linkSync(claimedPath, lockPath);
  } catch (error) {
    if (error?.code === "EEXIST") {
      // Expected/benign: someone else already re-acquired that path since
      // the claim -- the claimed copy is now a redundant duplicate, safe
      // to drop by the caller.
      try { unlinkSync(claimedPath); } catch { /* already gone */ }
      return true;
    }
    // Any OTHER linkSync failure (permissions, an unsupported filesystem,
    // cross-device link, I/O) does NOT prove lockPath is empty -- fall
    // back to a read + exclusive-create write (`wx`, atomic on both POSIX
    // and Windows: fails with EEXIST if the destination already exists,
    // exactly like linkSync's own EEXIST signal, but without requiring
    // hard-link support).
    let content;
    try {
      content = readFileSync(claimedPath, "utf-8");
    } catch {
      // Can't even read our own claimed copy -- nothing left to restore
      // FROM. Fail closed.
      return false;
    }
    let fd;
    try {
      fd = openSync(lockPath, "wx");
    } catch (writeError) {
      if (writeError?.code === "EEXIST") {
        // Same benign case as linkSync's EEXIST above: someone else
        // already re-occupied lockPath since the claim.
        try { unlinkSync(claimedPath); } catch { /* already gone */ }
        return true;
      }
      // Truly could not restore either way -- fail closed, leaving the
      // orphaned copy at claimedPath as the only remaining trace rather
      // than losing it outright.
      return false;
    }
    try {
      writeFileSync(fd, content);
    } catch {
      // Exclusive create succeeded but the write itself failed --
      // lockPath now exists (possibly empty or partially written) while
      // claimedPath still holds the real content. Fail closed rather
      // than let this escape uncaught (violating this function's
      // documented true/false contract) and preserve claimedPath as the
      // only trustworthy remaining copy, matching every other failure
      // branch in this function.
      try { closeSync(fd); } catch { /* already closed */ }
      return false;
    }
    try { closeSync(fd); } catch { /* already closed */ }
    try { unlinkSync(claimedPath); } catch { /* already gone */ }
    return true;
  }
  // linkSync succeeded: lockPath now has a second name for the SAME
  // content via the hard link -- safe to drop the claimedPath name.
  try { unlinkSync(claimedPath); } catch { /* already gone */ }
  return true;
}

function releaseLock(lockPath, token) {
  const claimedPath = `${lockPath}.release-${process.pid}-${Date.now()}`;
  try {
    renameSync(lockPath, claimedPath);
  } catch {
    // Nothing at that path to claim -- already released, or reclaimed and
    // released again by someone else. Either way, not this invocation's to
    // touch.
    return;
  }
  let content;
  try {
    content = readFileSync(claimedPath, "utf-8");
  } catch {
    // Unexpected: we just renamed it into existence ourselves. We cannot
    // tell whether this was our own lock or a foreign one already
    // reclaimed since -- but either way, leaving lockPath (the canonical
    // path) empty here would let a third invocation acquire concurrently
    // with whatever this claim actually represented. Restore it rather
    // than guess.
    restoreClaimedLock(claimedPath, lockPath);
    return;
  }
  if (content === token) {
    // Genuinely our own lock, being released as intended -- lockPath is
    // MEANT to end up empty here (that is the whole point of a release),
    // so unlike every other catch in this pair of functions, this one is
    // NOT a "must restore" situation: a failed cleanup below only leaves
    // harmless orphaned garbage at claimedPath, never a live/ambiguous
    // lock, since lockPath is already correctly vacated by the rename
    // above regardless of what happens to the orphan copy's name.
    try { unlinkSync(claimedPath); } catch { /* already gone */ }
    return;
  }
  // Claimed content that is NOT this invocation's own token -- this
  // invocation ran long enough that another one reclaimed the path as
  // stale and is (or was) actively using it. Restore it (see
  // restoreClaimedLock's own doc comment for why linkSync, not renameSync).
  restoreClaimedLock(claimedPath, lockPath);
}

// Async-only twin of resolveRuntimePython's `resolve()` step, for a caller
// that must never block the event loop (see attemptWorktreeSync below).
// Always behaves as allowProvision:false -- an unprovisioned runtime is
// reported as not-found, never waited on with a blocking provision step.
async function resolveRuntimePythonAsyncNoProvision(resolved, env, cwd, timeout) {
  let python;
  if (process.platform === "win32") {
    const script = [
      "$ErrorActionPreference='Stop'",
      `$env:AGENT_RT_ROOT=Join-Path $env:USERPROFILE '${resolved.runtimeRoot}'`,
      ". (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts\\resolve-runtime.ps1')",
      "if ($AgentRtPy) {[Console]::Out.Write($AgentRtPy)}",
    ].join("; ");
    const { stdout } = await execFileAsync(
      windowsPowerShell(),
      ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
      { cwd, timeout, env, encoding: "utf-8" },
    );
    python = stdout.trim();
  } else {
    const { stdout } = await execFileAsync("sh", ["-c", [
      `AGENT_RT_ROOT="$HOME/${resolved.runtimeRoot}"`,
      "export AGENT_RT_ROOT",
      '. "$COPILOT_PLUGIN_ROOT/scripts/resolve-runtime.sh"',
      'printf %s "${AGENT_RT_PY:-}"',
    ].join("; ")], { cwd, timeout, env, encoding: "utf-8" });
    python = stdout.trim();
  }
  if (!python || !existsSync(python)) {
    throw new Error("payload runtime is not yet provisioned (provisioning skipped)");
  }
  return python;
}

// Best-effort worktree sync for the fully-automated force-tier path (Phase 7:
// a successor inherits the SAME on-disk worktree, so an un-synced predecessor
// hands the same already-fixed-upstream bugs to its own successor). This path
// has NO agent judgment in the loop, so it is deliberately more conservative
// than the agent-guided skill flow: it NEVER commits anything. If the tree is
// dirty, syncing is skipped entirely rather than risking an unsupervised
// commit of unrelated edits or untracked secrets (the same risk an agent-guided
// blanket commit could hit, but here there is no agent to scope it). Only a
// worktree that is already clean gets synced.
//
// --ignored --untracked-files=all matches this repo's own established
// dirty-check safety pattern
// (customizing-copilot/skills/reviewing-customizations/scripts/
// scan_plugin_sources.py's _payload_is_clean): plain `--porcelain` omits
// ignored files entirely and collapses an untracked directory to one
// summary line, and can also be suppressed entirely for untracked files
// by a local `status.showUntrackedFiles=no` config -- any of which would
// let real content (including an ignored/untracked secret-adjacent file
// that could collide with what the sync brings in) slip through as
// "clean". This deliberately over-rejects (e.g. an ordinary ignored
// build/dependency directory also blocks a sync attempt) in favor of the
// same fail-closed philosophy this whole function already follows for any
// other uncommitted state. Extracted so it can run INSIDE the worktree
// sync lock (see attemptWorktreeSync below) rather than before it -- a
// review finding: checking cleanliness before acquiring the lock left a
// window where a file could become dirty between the check and the
// locked sync actually running.
async function worktreeIsDirty(cwd) {
  const result = await execFileAsync(
    "git", ["status", "--porcelain", "--ignored", "--untracked-files=all"],
    { cwd, encoding: "utf-8", timeout: 5000, env: sanitizedGitEnv() },
  );
  return Boolean(result.stdout.trim());
}

// Fully async (execFile, not execFileSync/runCli) end to end: this is invoked
// synchronously at the top of a fire-and-forget async function
// (autoForceHandoff) before its first await, so any blocking child-process
// call here would freeze the SDK event loop for the length of that call
// regardless of the caller's own fire-and-forget intent.
export async function attemptWorktreeSync(cwd) {
  return withWorktreeSyncLock(cwd, () => attemptWorktreeSyncLocked(cwd));
}

// The locked portion of attemptWorktreeSync: rebase check through the actual
// Fallback for a payload-only context-handoff installation with no sibling
// agent-worktrees payload available (or an unresolvable one): a plain
// sanitized-env fetch + rebase onto the remote's default branch, aborting
// cleanly on conflict. Mirrors the same conflict-safety contract
// agent-worktrees' own `git sync` provides (never leaves a conflicted
// rebase-merge state behind), just without agent-worktrees' own richer
// diagnostics. Exported for direct testability: resolveSystemCliDescriptor
// resolves against the real on-disk sibling `agent-worktrees` plugin, which
// always exists in this monorepo checkout, so this path can't be reached
// end-to-end from attemptWorktreeSync in-repo without genuinely uninstalling
// that sibling -- test this function directly instead.
export async function plainGitSync(cwd) {
  const env = withUnattendedSyncGitConfig(sanitizedGitEnv());
  const timeout = 20000;
  // `agent-worktrees`' own managed sync explicitly skips a detached
  // worktree; this fallback must match that, not just mirror its conflict
  // safety. `git rebase origin/<branch>` is valid while detached (it moves
  // the detached HEAD, not any branch), which is not what a "sync onto the
  // default branch" caller expects and could leave commits unreachable
  // once HEAD moves again. `symbolic-ref -q` exits nonzero (throws here)
  // exactly when HEAD is detached, with no output parsing needed.
  let currentBranch;
  try {
    const { stdout } = await execFileAsync(
      "git", ["symbolic-ref", "-q", "--short", "HEAD"],
      { cwd, encoding: "utf-8", timeout: 5000, env },
    );
    currentBranch = stdout.trim();
  } catch {
    return {
      attempted: false,
      synced: false,
      reason: "worktree HEAD is detached; the plain-git sync fallback never rebases a detached checkout",
    };
  }
  // Resolve the CONFIGURED remote for the current branch (a review finding:
  // hardcoding "origin" silently fails to sync a checkout whose tracking
  // remote is named differently, e.g. "upstream") -- falls back to
  // "origin" only when no tracking remote is configured, matching the
  // managed path's own `RepoConfig.remote` default.
  let remote = "origin";
  try {
    const { stdout } = await execFileAsync(
      "git", ["config", `branch.${currentBranch}.remote`],
      { cwd, encoding: "utf-8", timeout: 5000, env },
    );
    const configured = stdout.trim();
    if (configured) remote = configured;
  } catch {
    // No configured tracking remote -- "origin" default stands.
  }
  let defaultBranch;
  try {
    // Authoritative: ask the REMOTE directly via `ls-remote --symref` (a
    // review finding: the local `origin/HEAD` symref below is a CACHE --
    // it can remain pointed at the remote's OLD default branch after the
    // remote changes it, silently fetching/rebasing onto the wrong branch
    // while reporting success). This is already a network round-trip, so
    // it costs nothing extra over the fetch this function performs anyway,
    // and matches the authoritative resolver's own ordering
    // (agent-worktrees' `status_bar_cli.py`'s `_resolve_remote_default_branch`).
    const { stdout } = await execFileAsync(
      "git", ["ls-remote", "--symref", remote, "HEAD"],
      { cwd, encoding: "utf-8", timeout, env },
    );
    const match = stdout.match(/^ref:\s*refs\/heads\/(\S+)\s+HEAD/m);
    defaultBranch = match ? match[1] : null;
  } catch {
    defaultBranch = null;
  }
  if (!defaultBranch) {
    try {
      // The `ls-remote --symref` query above failed outright (network
      // issue, or a remote that doesn't answer symref queries) -- try
      // `git remote show <remote>` next: it ALSO asks the remote directly
      // (still authoritative, not a local cache), so prefer it over the
      // possibly-stale `origin/HEAD` symref below.
      const { stdout } = await execFileAsync(
        "git", ["remote", "show", remote],
        { cwd, encoding: "utf-8", timeout, env },
      );
      const match = stdout.match(/HEAD branch:\s*(\S+)/);
      defaultBranch = match && match[1] !== "(unknown)" ? match[1] : null;
    } catch {
      defaultBranch = null;
    }
  }
  if (!defaultBranch) {
    // Both direct remote queries failed outright (e.g. genuinely
    // unreachable network) -- a review finding: falling back to the local
    // `origin/HEAD` cache here can silently sync onto the WRONG branch (it
    // may still name the remote's OLD default after a rename) while still
    // reporting success. Report unsynced instead of guessing from a value
    // that cannot be confirmed authoritative.
    return {
      attempted: true,
      synced: false,
      reason: "could not confirm the remote's default branch (both direct remote queries failed); the plain-git sync fallback does not guess from a possibly-stale local cache",
    };
  }
  try {
    await execFileAsync(
      "git", ["fetch", remote, defaultBranch],
      { cwd, encoding: "utf-8", timeout, env },
    );
    // Recheck immediately before the rebase exec -- this fallback is
    // reached only after attemptWorktreeSync's own earlier rebase check,
    // and the remote-discovery + fetch awaits above are exactly the kind of
    // gap another process could start a rebase in. Skipping here (rather
    // than proceeding into the catch's unconditional `git rebase --abort`)
    // avoids cancelling a rebase this call did not start.
    const recheck = await rebaseInProgress(cwd);
    if (recheck.inProgress || recheck.error) {
      return {
        attempted: false,
        synced: false,
        reason: "a rebase started in this worktree just before the plain-git sync; it was skipped rather than touching an in-progress rebase",
      };
    }
    // Same recheck-immediately-before-exec pattern, for dirtiness: the
    // fetch above is another gap where a file could become dirty, and a
    // local `rebase.autoStash` config would otherwise let `git rebase`
    // silently stash/pop unreviewed content instead of refusing to run.
    if (await worktreeIsDirty(cwd)) {
      return {
        attempted: false,
        synced: false,
        reason: "the worktree became dirty just before the plain-git sync; it was skipped rather than risking rebase.autoStash moving unreviewed content",
      };
    }
    await execFileAsync(
      // --no-autostash: the recheck immediately above narrows but cannot
      // fully close the dirty-tree TOCTOU (a file could still become dirty
      // in the instant between that check and this exec) -- a repository
      // or user `rebase.autoStash=true` config would otherwise let this
      // automatic path silently stash/pop that content instead of failing,
      // contrary to the whole clean-tree safety gate's intent. Explicitly
      // disabling it makes a race fail loudly instead of moving unreviewed
      // local content.
      "git", ["rebase", "--no-autostash", `${remote}/${defaultBranch}`],
      { cwd, encoding: "utf-8", timeout, env },
    );
    return { attempted: true, synced: true, reason: null };
  } catch (error) {
    // Never leave a conflicted rebase-merge state for the successor to
    // inherit -- same conflict-safety contract as agent-worktrees' own
    // sync helper. Only reachable once the recheck above has already
    // confirmed no pre-existing rebase, so any abort here can only ever be
    // cancelling the rebase this same call just started (a real conflict),
    // never one it did not start.
    try {
      await execFileAsync("git", ["rebase", "--abort"], { cwd, encoding: "utf-8", timeout: 5000, env });
    } catch { /* nothing to abort, or abort itself failed -- best-effort */ }
    return {
      attempted: true,
      synced: false,
      reason: `plain-git sync failed: ${describeSyncError(error)}`,
    };
  }
}

// sync exec. Split out so the lock (withWorktreeSyncLock) wraps this
// function's entire body -- including the dirty-tree check, which now runs
// AFTER the lock is held rather than before (a review finding: checking
// before acquiring the lock left a window for a file to become dirty
// between the check and the locked sync actually running).
async function attemptWorktreeSyncLocked(cwd) {
  let dirty;
  try {
    dirty = await worktreeIsDirty(cwd);
  } catch (error) {
    return {
      attempted: false,
      synced: false,
      reason: `not a git checkout or git unavailable: ${describeSyncError(error)}`,
    };
  }
  if (dirty) {
    return {
      attempted: false,
      synced: false,
      reason: "worktree has uncommitted, untracked, or ignored content; automatic sync never commits, so it was skipped",
    };
  }
  // A rebase can be paused at a clean step (e.g. `edit`), which reports a
  // clean `git status --porcelain` even though a rebase is genuinely in
  // progress. Proceeding to sync in that state would let the sync helper's
  // own failure-path `git rebase --abort` cancel a rebase this session
  // never started -- never our call to make. `--git-path` resolves the
  // correct per-worktree location for either rebase strategy.
  //
  // The worktree sync lock serializes this plugin's OWN concurrent sync
  // attempts (e.g. the force-tier and skill-guided paths racing on the same
  // worktree), but it cannot compel an external actor (a human, or an
  // unrelated tool) to honor it -- that residual gap is why
  // `rebaseInProgress` is still rechecked immediately before the sync exec.
  const rebaseState = await rebaseInProgress(cwd);
  if (rebaseState.inProgress || rebaseState.error) {
    return {
      attempted: false,
      synced: false,
      reason: rebaseState.error
        ? "could not determine rebase state; automatic sync was skipped to be safe"
        : "a rebase is already in progress in this worktree; automatic sync never touches an in-progress rebase, so it was skipped",
    };
  }
  let resolved;
  try {
    resolved = resolveSystemCliDescriptor("agent-worktrees");
  } catch {
    // No sibling agent-worktrees payload resolves (e.g. a payload-only
    // context-handoff installation) -- fall back to a plain sanitized-env
    // git fetch/rebase rather than reporting the sync unavailable outright.
    return plainGitSync(cwd);
  }
  const timeout = 20000;
  try {
    if (resolved.pluginRoot) {
      const env = withUnattendedSyncGitConfig(sanitizedGitEnv(
        runtimeEnvironment({ ...process.env }, resolved.pluginRoot),
      ));
      // allowProvision: false, structurally -- this async resolver never
      // takes the (execFileSync-based, up-to-RUNTIME_PROVISION_TIMEOUT_MS)
      // provisioning path at all; an unprovisioned runtime throws
      // immediately instead.
      const python = await resolveRuntimePythonAsyncNoProvision(
        resolved, env, cwd, timeout,
      );
      if (resolved.payloadRootEnv) {
        env[resolved.payloadRootEnv] = resolved.pluginRoot;
      }
      // Recheck immediately before the actual sync subprocess launch --
      // narrows (never fully closes) the TOCTOU window noted above, since
      // this is the last point before the exec that could abort a rebase.
      // Also recheck dirtiness here: the delegated `agent-worktrees git
      // sync`'s own clean-tree check uses bare `git status --porcelain`
      // (not this file's stricter --ignored/--untracked-files=all check),
      // and its rebase does not disable rebase.autoStash on its own --
      // content created in the runtime-resolution gap above could
      // otherwise be silently stashed/rebased by the delegated call,
      // bypassing this path's own fail-closed safety. GIT_CONFIG_* above
      // covers the autostash half; this recheck covers the dirty half.
      const recheck = await rebaseInProgress(cwd);
      if (recheck.inProgress || recheck.error) {
        return {
          attempted: false,
          synced: false,
          reason: "a rebase started in this worktree just before sync; automatic sync never touches an in-progress rebase, so it was skipped",
        };
      }
      if (await worktreeIsDirty(cwd)) {
        return {
          attempted: false,
          synced: false,
          reason: "the worktree became dirty just before sync; it was skipped rather than letting the delegated sync touch it",
        };
      }
      await execFileAsync(
        python,
        isolatedPythonArgs(resolved.module, ["git", "sync"]),
        { cwd, timeout, env, encoding: "utf-8" },
      );
    } else {
      const recheck = await rebaseInProgress(cwd);
      if (recheck.inProgress || recheck.error) {
        return {
          attempted: false,
          synced: false,
          reason: "a rebase started in this worktree just before sync; automatic sync never touches an in-progress rebase, so it was skipped",
        };
      }
      if (await worktreeIsDirty(cwd)) {
        return {
          attempted: false,
          synced: false,
          reason: "the worktree became dirty just before sync; it was skipped rather than letting the delegated sync touch it",
        };
      }
      await execFileAsync(resolved.path, ["git", "sync"], {
        cwd, timeout, encoding: "utf-8", env: withUnattendedSyncGitConfig(sanitizedGitEnv()),
      });
    }
    return { attempted: true, synced: true, reason: null };
  } catch (error) {
    return {
      attempted: true,
      synced: false,
      reason: `sync failed or agent-worktrees unavailable: ${describeSyncError(error)}`,
    };
  }
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

function normalizeRegisteredRepoPath(raw) {
  if (!raw || typeof raw !== "string") return null;
  if (!raw.startsWith("~")) return raw;
  const suffix = raw.slice(1).replace(/^[\\/]+/, "");
  return suffix ? join(homedir(), suffix) : homedir();
}

function registeredRepoPaths(paths = {}) {
  const candidates = [];
  for (const raw of Object.values(paths || {})) {
    const candidate = normalizeRegisteredRepoPath(raw);
    if (candidate && existsSync(candidate)) candidates.push(candidate);
  }
  return candidates;
}

function knownHandoffSearchRoots(cwd, execute = runCli) {
  let reposJson;
  try {
    reposJson = cliJson(
      "agent-worktrees",
      ["repos", "list", "--class", "worktree", "--json"],
      cwd,
      AGENT_WORKTREES_QUERY_TIMEOUT_MS,
      execute,
    );
  } catch {
    return [];
  }
  const roots = new Set();
  for (const repo of reposJson?.repos || []) {
    for (const anchorPath of registeredRepoPaths(repo?.paths)) {
      roots.add(anchorPath);
      try {
        const listed = cliJson(
          "agent-worktrees",
          ["list", "--all", "--json"],
          anchorPath,
          AGENT_WORKTREES_QUERY_TIMEOUT_MS,
          execute,
        );
        for (const worktree of listed?.worktrees || []) {
          if (worktree?.path) roots.add(worktree.path);
        }
      } catch {
        // Best-effort enumeration: the anchor namespace still covers adopted
        // anchors even when the per-project worktree listing is unavailable.
      }
    }
  }
  return [...roots];
}

function discoverFileHandoffPath(
  cwd,
  handoffId,
  execute = runCli,
) {
  const filename = `${safePathSegment(handoffId)}.json`;
  const visitedStateDirs = new Set();
  for (const root of knownHandoffSearchRoots(cwd, execute)) {
    const resolution = agentWorktreesGetResult(
      "worktree-state-dir",
      root,
      null,
      execute,
    );
    const stateDir = resolution.value;
    if (!stateDir || visitedStateDirs.has(stateDir)) continue;
    visitedStateDirs.add(stateDir);
    const candidate = join(stateDir, "handoff", filename);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function knownWorktreeRows(cwd, execute = runCli) {
  let reposJson;
  try {
    reposJson = cliJson(
      "agent-worktrees",
      ["repos", "list", "--class", "worktree", "--json"],
      cwd,
      AGENT_WORKTREES_QUERY_TIMEOUT_MS,
      execute,
    );
  } catch {
    return [];
  }
  const rows = new Map();
  for (const repo of reposJson?.repos || []) {
    for (const anchorPath of registeredRepoPaths(repo?.paths)) {
      let listed;
      try {
        listed = cliJson(
          "agent-worktrees",
          ["list", "--all", "--json"],
          anchorPath,
          AGENT_WORKTREES_QUERY_TIMEOUT_MS,
          execute,
        );
      } catch {
        continue;
      }
      for (const worktree of listed?.worktrees || []) {
        if (!worktree?.id || !worktree?.path) continue;
        const key = [
          worktree.machine || "",
          worktree.repo || repo?.name || "",
          worktree.id,
        ].join(":");
        if (!rows.has(key)) rows.set(key, worktree);
      }
    }
  }
  return [...rows.values()];
}

function normalizeComparePath(value) {
  if (!value) return "";
  let normalized = String(value).trim().replace(/[\\/]+/g, "/");
  normalized = normalized.replace(/\/+$/, "");
  return process.platform === "win32"
    ? normalized.toLowerCase()
    : normalized;
}

export function readStatusMonitorRegistry(home = homedir()) {
  const dir = join(home, ".agent-worktrees", "status-monitor.d");
  const entries = {};
  if (!existsSync(dir)) return entries;
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    try {
      entries[name] = readFileSync(path, "utf-8").trim();
    } catch {
      // Best-effort diagnostic only.
    }
  }
  return entries;
}

export function checkHeadAlignment(cwd, execute = runCli) {
  const worktrees = knownWorktreeRows(cwd, execute);
  const registry = readStatusMonitorRegistry();
  const findings = [];
  for (const worktree of worktrees) {
    let head;
    try {
      head = cliJson(
        "agent-worktrees",
        ["head-session", "--worktree", worktree.id, "--json"],
        cwd,
        AGENT_WORKTREES_QUERY_TIMEOUT_MS,
        execute,
      );
    } catch (error) {
      findings.push({
        reason: "head-query-failed",
        worktree,
        error: describeCliError(error),
      });
      continue;
    }
    const pending = Array.isArray(head?.pending_handoffs) ? head.pending_handoffs : [];
    if (!pending.length) continue;
    const monitorSession = `wt-${worktree.id}`;
    const registeredPath = registry[monitorSession] || null;
    const expectedPath = worktree.path || null;
    const pathMatches = registeredPath
      ? normalizeComparePath(registeredPath) === normalizeComparePath(expectedPath)
      : null;
    if (!registeredPath) {
      findings.push({
        reason: "pending-handoff-unregistered",
        worktree,
        head,
        monitorSession,
        monitorPath: null,
      });
      continue;
    }
    if (!pathMatches) {
      findings.push({
        reason: "pending-handoff-registry-path-mismatch",
        worktree,
        head,
        monitorSession,
        monitorPath: registeredPath,
      });
    }
  }
  return {
    ok: true,
    checked: worktrees.length,
    findings,
  };
}

export function retryStoredHandoffCutover(cwd, sid, execute = runCli) {
  if (!sid) {
    return {
      ok: false,
      error: "retry-cutover requires --session-id or COPILOT_AGENT_SESSION_ID",
    };
  }
  try {
    return cliJson(
      "agent-worktrees",
      ["handoff-cutover", "--retry", "--session-id", sid, "--json"],
      cwd,
      30000,
      execute,
    );
  } catch (error) {
    return {
      ok: false,
      error: describeCliError(error) || "Could not retry handoff cutover.",
    };
  }
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

export function readFileHandoff(
  cwd,
  sid,
  handoffId,
  explicitPath = null,
  { get = agentWorktreesGet, execute = runCli } = {},
) {
  const filename = `${safePathSegment(handoffId)}.json`;
  let path = explicitPath || (() => {
    const dir = handoffDirFor(cwd, sid, get);
    return dir ? join(dir, filename) : null;
  })();
  if ((!path || !existsSync(path)) && !explicitPath) {
    // A recovery locator must remain usable from a bare/HOME resume or any
    // other cwd that is outside the adopted project that stored the handoff.
    path = discoverFileHandoffPath(cwd, handoffId, execute) || path;
  }
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
  options = {},
) {
  const found = readFileHandoff(cwd, sid, handoffId, explicitPath, options);
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
      let ownerSessionId = null;
      let lockAgeMs = 0;
      try {
        const lockInfo = JSON.parse(readFileSync(lockPath, "utf-8"));
        ownerPid = lockInfo.pid;
        ownerSessionId = lockInfo.sessionId || null;
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
        claimedBySession: ownerSessionId,
        message:
          `Handoff ${found.record.id || found.path} is already being consumed ` +
          `by session \`${ownerSessionId || "unknown"}\`. ` +
          "Do not replay it; retry only after the active consumer finishes.",
      };
    }
  }
  try {
    const current = readFileHandoff(
      cwd, sid, handoffId, found.path, options,
    );
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
        claimedBySession: current.record.consumedBySession || null,
        message:
          `Handoff ${current.record.id || current.path} was already consumed by ` +
          `session \`${current.record.consumedBySession || "unknown"}\` at ` +
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

// Exported for testability (pure function, no I/O): the dedup key folds in a
// short content hash rather than just the session id. agent-dispatch's
// `create` is idempotent per dedup-key -- a repeat call with the SAME key
// returns the existing nonterminal task unchanged, even with different
// --payload-file content. Without the hash, a deliberate re-save after this
// session's own worktree sync (guidance tells the agent to always re-save
// post-sync) would silently keep the stale pre-sync payload live. Identical
// content still maps to the same key, so a true accidental duplicate call
// still dedupes as before; only genuinely different content earns a fresh
// task, and `abandonSupersededHandoffs` retires whatever it superseded.
export function handoffDedupKey(sid, promptText) {
  const contentHash = createHash("sha256").update(promptText).digest("hex").slice(0, 12);
  return `handoff-${sid}-${contentHash}`;
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
      "--dedup-key", handoffDedupKey(sid, promptText), "--payload-file", tmp,
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
      const predecessorSessionId = decoded.metadata?.sessionId || checkpoint.predecessorSession || null;
      const priorState = predecessorSessionId
        ? readSessionStateHandoff(predecessorSessionId)
        : null;
      const claimedBySession = priorState?.record?.consumedBySession || null;
      const cliMessage = describeCliError(error) || "Could not consume handoff task.";
      return {
        ok: false,
        id: taskId,
        claimedBySession,
        message: claimedBySession
          ? `${cliMessage}\n\nAlready consumed by session \`${claimedBySession}\`.`
          : cliMessage,
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
  options = {},
) {
  const consumed = consumeFileHandoffOnce(
    cwd, sid, handoffId, explicitPath, options,
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
    const claimant = result?.claimedBySession;
    return (
      `${result?.message || "Handoff could not be consumed."}\n\n` +
      "Handoff consumption is blocked. Do not treat the missing brief as " +
      "completion or reconstruct a different objective from session history." +
      (claimant
        ? `\n\nClaimant session: \`${claimant}\`. If this handoff was not ` +
          "expected to already be claimed (e.g. it looks like a duplicate " +
          "or racing consumption attempt), tell the user and offer to file " +
          "a bug referencing this session id -- do not file one " +
          "automatically without asking."
        : "")
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
    HANDOFF_MECHANISM_AWARENESS,
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
    HANDOFF_MECHANISM_AWARENESS,
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
      // `process.ppid`, NOT `process.pid`: this MCP-extension host runs as a
      // direct child of the actual `copilot` CLI process, so its own pid is
      // never the process the status-monitor daemon needs to identify and
      // eventually retire -- recording `process.pid` here fed a wrong "expected
      // copilot pid" into the daemon's spawn/retire identity check, which
      // (harmlessly for spawning, but permanently for retiring, since a failed
      // attempt was recorded as "handled") silently stranded every predecessor
      // pane past the first handoff on a worktree. The daemon now resolves the
      // authoritative pid itself via a live mux binding lookup and no longer
      // gates on this value, but it stays correct here as defense in depth and
      // for any future consumer that reads it verbatim.
      "--field", `predecessor_pid=${process.ppid}`,
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

// A weaker, earlier signal than `worktreeSuccessorRecorded`: the resident
// status-monitor logs `handoff_cutover_spawn` as soon as it claims the token
// and opens the successor pane -- well before that successor's own Copilot
// process has cold-started far enough to run its `sessionStart` hook and
// flip the worktree's head session (which is what `worktreeSuccessorRecorded`
// actually waits for). Real cold-starts routinely take 40-90+ seconds, so
// surfacing this earlier "spawn acknowledged" state lets the predecessor stop
// waiting/report progress sooner instead of looking like nothing happened.
function worktreeSpawnInFlight(cwd, stored, execute = runCli) {
  const worktreeId = stored.metadata?.worktree || null;
  if (!worktreeId) return { inFlight: false, worktreeId };
  let raw;
  try {
    raw = execute(
      "agent-worktrees",
      ["activity", "--worktree-id", worktreeId, "--event", "handoff_cutover_spawn", "--json"],
      { cwd, timeout: 10000 },
    );
  } catch (error) {
    return { inFlight: false, worktreeId, error: describeCliError(error) };
  }
  const lines = String(raw || "").split("\n").map((line) => line.trim()).filter(Boolean);
  for (const line of lines) {
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      continue;
    }
    if (String(event?.handoff_token || "") === stored.id) {
      return { inFlight: true, worktreeId, event };
    }
  }
  return { inFlight: false, worktreeId };
}

// The deterministic "did my launch actually land" signal (Phase 3 item 3):
// the successor's own extension.mjs greps its first submitted prompt for the
// exact recovery locator (see cutover-seed.mjs's extractRecoveryLocatorFromPrompt)
// and best-effort logs a `context_handoff_prompt_received` activity event --
// distinct from agent-worktrees' own numbered-handoff `handoff_token` identity
// space (used by `handoff_cutover_spawn`), so this uses its own `locator`
// field carrying context-handoff's own task/file id, unambiguously.
export function logHandoffPromptReceived(
  cwd,
  worktreeId,
  sid,
  locator,
  execute = runCli,
) {
  if (!worktreeId || !locator) return { logged: false };
  try {
    execute("agent-worktrees", [
      "activity-log",
      "context_handoff_prompt_received",
      "--worktree-id", worktreeId,
      "--session-id", sid || "",
      "--source", "context-handoff",
      "--field", `locator=${locator}`,
    ], { cwd, timeout: 5000, stdio: "ignore" });
    return { logged: true };
  } catch (error) {
    return { logged: false, error: describeCliError(error) };
  }
}

// Predecessor-side read: has the successor's own extension already confirmed
// receipt of exactly this handoff's recovery locator as a real submitted
// prompt? A stronger, more direct signal than `worktreeSpawnInFlight` (which
// only proves a pane/process was created, not that Copilot itself received
// the seed) -- feeds `pickupSignals` below.
function worktreePromptReceived(cwd, stored, execute = runCli) {
  const worktreeId = stored.metadata?.worktree || null;
  if (!worktreeId) return { received: false, worktreeId };
  const expected = `${stored.storage === "agent-dispatch" ? "task" : "file"}:${stored.id}`;
  let raw;
  try {
    raw = execute(
      "agent-worktrees",
      [
        "activity", "--worktree-id", worktreeId,
        "--event", "context_handoff_prompt_received", "--json",
      ],
      { cwd, timeout: 10000 },
    );
  } catch (error) {
    return { received: false, worktreeId, error: describeCliError(error) };
  }
  const lines = String(raw || "").split("\n").map((line) => line.trim()).filter(Boolean);
  for (const line of lines) {
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      continue;
    }
    if (String(event?.locator || "") === expected) {
      return { received: true, worktreeId, event };
    }
  }
  return { received: false, worktreeId };
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
  let promptReceived = { received: false, worktreeId: worktree.worktreeId };
  if (via.length === 0) {
    // Only worth a separate CLI round-trip when full pickup isn't already
    // confirmed some other way.
    promptReceived = worktreePromptReceived(cwd, stored, execute);
    if (promptReceived.received) via.push("prompt-received");
  }
  const pickedUp = via.length > 0;
  // Only worth a separate CLI round-trip when full pickup hasn't already
  // been confirmed -- the spawn marker is strictly implied by pickedUp.
  const spawn = pickedUp
    ? { inFlight: true, worktreeId: worktree.worktreeId }
    : worktreeSpawnInFlight(cwd, stored, execute);
  return {
    pickedUp,
    spawnInFlight: spawn.inFlight,
    via,
    sessionState: {
      path: sessionStatePath,
      consumed: sessionConsumed,
    },
    worktree,
    dispatch,
    promptReceived,
    spawn,
  };
}

// Store a handoff, preferring an agent-dispatch task (durable/browsable) when a
// coordinator is reachable, else a one-time worktree-state file. Mirrors the
// extension's save_handoff_prompt store selection. Returns:
//   { storage: "agent-dispatch"|"file", id, taskId?, path?, metadata }
// On failure returns a storage:null result with the resolver/write diagnostic.
export function storeHandoff({ promptText, sid, cwd, title, preferTask = true }) {
  // Deliberately does NOT touch the worktree's own record: `storeHandoff`
  // backs BOTH `save_handoff_prompt` (documented as the "safe,
  // non-committal" step that must never arm pickup) and `trigger_handoff`
  // (the "arm pickup" step). Recording a handoff there creates a
  // `pending_handoffs` entry agent-worktrees' resident monitor can
  // discover and claim on its own -- a live-cutover trigger, not merely
  // advisory history (Copilot review finding on PR #3041: this used to
  // run unconditionally here, so even `save_handoff_prompt` -- and a
  // manual-only-mode `trigger_handoff` -- could still get auto-launched by
  // the monitor through this exact path). Only `triggerHandoff()` records
  // it now, and only when `mode: auto` is configured.
  if (preferTask && agentDispatchAvailable()) {
    const task = dispatchHandoff(promptText, sid, cwd, title);
    if (task) {
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
  return { storage: "file", id: file.id, path: file.path, metadata: file.metadata };
}

// Compose the same short locator seed for manual recovery and external pickup.
export function buildSeedForStored(stored) {
  const md = stored.metadata || {};
  const kind = stored.storage === "agent-dispatch" ? "task" : "file";
  const lead = leadFrom(md.title);
  return buildCutoverSeed(kind, stored.id, lead);
}

export function manualFallbackInstructions(
  stored,
  seed,
  { spawnInFlight = false, automaticCutoverDisabled = false } = {},
) {
  const location = stored.storage === "agent-dispatch"
    ? `agent-dispatch task ${stored.id}`
    : `file handoff ${stored.id}`;
  // automaticCutoverDisabled takes priority over spawnInFlight: mode being
  // disabled means THIS call never requested an automatic spawn, but the
  // pickup probe can still observe a stale/unrelated spawn-in-flight marker
  // (e.g. a manual successor's own registration, or a leftover marker from
  // an earlier auto-mode attempt) -- claiming "the automatic cutover should
  // complete" in that case would directly contradict the disabled-mode
  // truth (Copilot review finding on PR #3041).
  if (automaticCutoverDisabled) {
    return (
      `Handoff stored as ${location}. Automatic cutover is disabled ` +
      "(`.context-handoff/config.yaml`'s `mode` is not `auto`), so no " +
      "successor pane was requested and none will be spawned automatically -- " +
      "this is expected, not a failure. Open (or ask the operator to open) " +
      "the successor session yourself, then run `/consume-handoff`. If that " +
      "command is unavailable, use the context-handoff payload-local CLI and " +
      "pass only the trailing `task:<id>` or `file:<id>` token to " +
      "`consume --locator`.\n\n" +
      "Copy only the following short handoff prompt/seed:\n\n" +
      "```text\n" +
      `${seed}\n` +
      "```"
    );
  }
  if (spawnInFlight) {
    return (
      `Handoff stored as ${location}. A successor session has already been ` +
      "spawned and is starting up -- real Copilot cold-start (loading MCP " +
      "servers/skills before it can run its own sessionStart hook) routinely " +
      "takes longer than this tool's short wait, so this is the expected " +
      "in-progress state, not a failure. No action is usually needed; the " +
      "automatic cutover should complete once the successor finishes " +
      "starting. If you'd rather not wait, or want a backup, run " +
      "`/consume-handoff` in the successor session yourself, or use the " +
      "context-handoff payload-local CLI and pass only the trailing " +
      "`task:<id>` or `file:<id>` token to `consume --locator`.\n\n" +
      "Copy only the following short handoff prompt/seed:\n\n" +
      "```text\n" +
      `${seed}\n` +
      "```"
    );
  }
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
    // Waits only long enough for the CUTOVER to start (the resident
    // status-monitor's `handoff_cutover_spawn` marker, typically 10-25s),
    // not for the successor to actually finish cold-starting and consume the
    // handoff -- that part legitimately takes 40-90s+ and blocking on it
    // would make every real handoff feel hung. 30s used to be the ceiling
    // AND the only signal checked (full pickup), so a real cold-start meant
    // this almost always timed out reporting "no pickup" even when a cutover
    // was already under way; now the loop exits the moment a spawn is
    // observed, so 30s comfortably covers that without needing to grow.
    waitMs = 30000,
    // Live-cutover opt-in gate (`.context-handoff/config.yaml`'s `mode`):
    // only "auto" ever emits the `handoff_requested` activity event
    // agent-worktrees' resident status-monitor watches for, or pings
    // agent-bridge -- the two things that can cause an automatic successor
    // pane to spawn. Any other mode (the default, "manual-only") still
    // stores/seeds the handoff normally; it just never wires up automatic
    // pickup, so the operator/agent must consume it manually.
    mode = DEFAULT_HANDOFF_MODE,
    execute = runCli,
    store = storeHandoff,
    writeSessionState = writeSessionStateHandoff,
    noteHandoff = noteHandoffInRecord,
    logActivity = logHandoffActivity,
    requestBridge = requestAgentBridgeHandoff,
    readPickupSignals = pickupSignals,
    sleepFn = sleep,
    // Called ONCE the baton is durably stored (right after store()/
    // writeSessionState() both succeed) -- the right place for a caller to
    // KICK OFF a best-effort side task (e.g. a worktree sync) that must
    // never run before storage is confirmed. Defaults to a no-op. Paired
    // with beforeArmPickup below (round-13 review finding: starting that
    // side task before the store was even attempted meant a store failure
    // could still leave it running, contradicting the "post-capture only"
    // contract).
    afterStore = () => {},
    // Awaited AFTER the baton is safely stored (`store()` above) but BEFORE
    // any live-pickup signal is armed (`noteHandoff`/activity log/bridge
    // request below) -- lets a caller defer live pickup until a best-effort
    // worktree sync settles (or times out), without ever delaying the
    // store itself. Defaults to a no-op so every other caller is
    // unaffected. Added for the force-tier path (round-12 review finding:
    // starting the sync merely CONCURRENTLY with the live trigger was not
    // enough -- a fast monitor could still launch a successor before the
    // sync had even begun; this closes that by construction rather than
    // narrowing it).
    beforeArmPickup = async () => {},
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
  // The baton is now durably stored -- the earliest point a caller may
  // start a best-effort side task without risking it running ahead of (or
  // despite) a store failure above.
  afterStore();

  const autoEnabled = automaticHandoffEnabled(mode);
  // Runs after the baton above is durably stored, but strictly before any
  // live-pickup signal below is armed -- see beforeArmPickup's own comment.
  // Only worth awaiting when a live signal can actually fire (manual-only
  // mode, the default, never arms pickup at all, so there is nothing to
  // gate and no reason to add the sync's latency to this call).
  if (autoEnabled) await beforeArmPickup();
  // `noteHandoff` (agent-worktrees `note-handoff`) calls `open_handoff()`,
  // which creates a `pending_handoffs` entry agent-worktrees' resident
  // monitor scans independently of the `handoff_requested` activity event
  // below (a session-state-file fallback path lets it discover and claim a
  // pending handoff with NO activity event at all) -- so this call is a
  // live-cutover trigger point too, not merely advisory, and must be gated
  // the same way (Copilot review finding on PR #3041: a "manual-only"
  // handoff could otherwise still get auto-launched by the monitor through
  // this exact path, defeating the entire opt-in gate). Now runs
  // regardless of `justStored` -- `storeHandoff()` itself never notes it
  // (see its own docstring), so this is the ONE place that ever does,
  // whether the handoff was just stored fresh or recovered from a prior
  // save.
  if (autoEnabled) {
    noteHandoff(cwd, sid, stored.id, stored.metadata?.title || title);
  }
  // Only "auto" mode emits the `handoff_requested` activity event
  // agent-worktrees' resident status-monitor watches for, or pings
  // agent-bridge -- both are how an automatic successor pane gets spawned.
  // Any other mode (the default, "manual-only") skips both: the handoff is
  // still fully stored/seeded, but nothing wires up automatic pickup.
  const activity = autoEnabled
    ? logActivity(
      cwd,
      sid,
      stored.metadata?.worktree || null,
      stored,
      sessionState.path,
      execute,
    )
    : { logged: false, reason: "automatic-cutover-disabled" };
  const bridge = autoEnabled
    ? requestBridge(cwd, sid, stored, seed, execute)
    : { attempted: false, accepted: false, reason: "automatic-cutover-disabled" };

  const start = Date.now();
  let status = readPickupSignals(cwd, sid, stored, sessionState.path, execute);
  // Exit as soon as EITHER full pickup is confirmed OR a spawn is merely in
  // flight -- the latter means the automatic cutover is already under way,
  // so there is nothing more useful to learn by continuing to block; report
  // that progress instead of waiting out the full ceiling. When automatic
  // cutover is disabled, nothing will spawn during this window, so don't
  // block on it -- check once (a manually-launched successor may already
  // have consumed it) and move straight to manual instructions.
  while (
    autoEnabled
    && !status.pickedUp
    && !status.spawnInFlight
    && (Date.now() - start) < waitMs
  ) {
    await sleepFn(1000);
    status = readPickupSignals(cwd, sid, stored, sessionState.path, execute);
  }

  return {
    ok: true,
    stored,
    seed,
    sessionState,
    // Explicit, top-level flag so callers can branch on it directly instead
    // of re-deriving it from nested activity/bridge reason strings -- a
    // real, distinct outcome from "signaled but no pickup arrived" (Copilot
    // extension review finding on PR #3041: caller-facing text must not say
    // "signaled" when neither live-cutover trigger point ever ran).
    automaticCutoverDisabled: !autoEnabled,
    worktreeSignal: {
      noted: autoEnabled,
      activity,
    },
    bridge,
    pickup: {
      ...status,
      waitedMs: Date.now() - start,
    },
    manualInstructions: status.pickedUp
      ? null
      : manualFallbackInstructions(stored, seed, {
        spawnInFlight: status.spawnInFlight,
        automaticCutoverDisabled: !autoEnabled,
      }),
  };
}
