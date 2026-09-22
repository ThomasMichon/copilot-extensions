// Emergency crash diagnostics: an isolated module with no
// `@github/copilot-sdk` dependency, so it can be exercised directly by
// subprocess tests without mocking `joinSession()`.
//
// `extension.mjs` has been observed, on at least one host, reaching the
// harness's readiness state (successful tool registration over the
// `joinSession` IPC channel) and then terminating with exit code 1 and NO
// captured stderr -- repeatedly, across many sessions over an extended
// period. No process-level `uncaughtException`/`unhandledRejection`/`exit`
// handlers existed anywhere in that file, so whatever Node would normally
// print for a crash was never captured.
//
// `installEmergencyDiagnostics` writes a synchronous, durable diagnostic
// line to a fixed `os.tmpdir()` path the instant anything goes wrong, so the
// next crash leaves an actual error message/stack behind instead of a bare
// exit code -- and the presence/absence of a logged non-zero `exit` line
// (routine `code=0` exits are never logged; see the rationale beside
// `onExit` below) pinpoints whether Node itself decided to terminate
// (uncaught exception, rejected top-level await) or something external
// force-killed the process before Node's own exit handling could run (no
// line is ever written in that case, since SIGKILL and an external
// process-tree kill bypass it entirely).

import {
  closeSync,
  constants as fsConstants,
  fstatSync,
  openSync,
  writeSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const DEFAULT_CRASH_LOG = join(tmpdir(), "context-handoff-extension-crash.log");

// Bounds this fixed, unrotated, machine-global file's growth. SIGTERM is the
// Copilot CLI's own routine mechanism for `/clear` and foreground-session
// replacement (see the "signal" rationale below), so -- unlike the
// suppressed `code=0` exit path -- ordinary, expected lifecycle churn alone
// still appends a line every time on a long-lived, handoff-heavy host, with
// no natural ceiling. Rather than never recording a legitimate signal (which
// would defeat the entire "distinguish a routine stop from a crash"
// diagnostic this module exists to provide), the file is truncated back to
// empty once it crosses this size, checked once per process at the lazy
// first-open below -- cheap, and sufficient to bound growth across the many
// separate short-lived processes that are the actual growth vector, even
// though it does not bound a single pathological process logging in a tight
// loop (not a real shape for this module: at most a handful of entries are
// ever logged per process lifetime).
const MAX_LOG_BYTES = 1_048_576; // 1 MiB

// `os.tmpdir()` is commonly a shared, world-writable directory on POSIX
// (`/tmp`), so a predictable filename there is both readable by other local
// users and open to several pre-creation attacks redirecting or blocking
// the write. `O_NOFOLLOW` makes the kernel itself refuse to open through an
// existing symlink (atomic, not a check-then-open race); `O_NONBLOCK` keeps
// an open against a pre-created FIFO from blocking indefinitely waiting for
// a reader (an `O_WRONLY` open of a FIFO with no reader would otherwise
// hang forever -- fatal for code that runs from a crash/signal handler,
// since the process would never get to exit or record anything) and instead
// fails immediately (`ENXIO`) so the regular-file check below still gets a
// chance to run and reject it through the ordinary open-failure path; mode
// 0o600 keeps a freshly created file private to this user. `O_NONBLOCK` has
// no effect on a genuine regular file's later reads/writes, so leaving it
// set on the fd for the writes below is harmless once the check confirms
// it really is one. Neither flag exists on Windows (no POSIX FIFO/symlink
// attack surface there in the same shape) -- fall back to a plain
// create/append flag set on that platform, since both are simply absent
// from `fs.constants`, not merely disabled.
const OPEN_FLAGS =
  fsConstants.O_CREAT |
  fsConstants.O_WRONLY |
  fsConstants.O_APPEND |
  (fsConstants.O_NOFOLLOW ?? 0) |
  (fsConstants.O_NONBLOCK ?? 0);

/**
 * True only if the already-opened `fd` is a regular file, owned by this
 * process's user, with no group/other permission bits set. Guards against a
 * different local user pre-creating an ordinary (non-symlink) file at this
 * predictable path with permissive mode before this process ever runs --
 * `O_NOFOLLOW` alone does not protect against that, since it only rejects a
 * *symlink*, and `O_CREAT`'s mode argument has no effect when the file
 * already existed.
 */
function isPrivateRegularFile(fd) {
  const stats = fstatSync(fd);
  return (
    stats.isFile() &&
    stats.uid === process.getuid() &&
    (stats.mode & 0o077) === 0
  );
}

/**
 * Builds an `emergencyLog(label, detail)` function bound to `logPath`
 * (default: `DEFAULT_CRASH_LOG`). Opens one private (0600), append-mode file
 * descriptor lazily on first use and reuses it -- synchronous I/O only (must
 * survive a process already mid-shutdown), and every failure (a pre-existing
 * symlink tripping `O_NOFOLLOW`, an untrusted pre-existing file failing the
 * ownership/mode check below, a permissions error, a full disk, ...) is
 * swallowed so diagnostic logging can never itself become a new crash cause.
 */
export function createEmergencyLog(logPath = DEFAULT_CRASH_LOG) {
  let fd;
  let openFailed = false;
  // Returns true if this call actually appended the entry, false if it was
  // silently swallowed (see the module doc comment) -- callers that need to
  // know whether the diagnostic actually landed anywhere (so they can fall
  // back to something else) check this return value; nothing here changes
  // behavior for a caller that ignores it.
  return function emergencyLog(label, detail) {
    try {
      if (fd === undefined && !openFailed) {
        try {
          const candidate = openSync(logPath, OPEN_FLAGS, 0o600);
          // The `0o600` mode above applies only when O_CREAT actually
          // creates the file. O_NOFOLLOW alone only rejects a symlink at
          // this path -- it does nothing about another local user having
          // pre-created an ordinary, world-readable (or -writable) regular
          // file here first: a predictable path in a shared os.tmpdir()
          // makes that trivial to set up before this process ever runs, and
          // this open() call would then happily append sensitive stack
          // traces into that untrusted, already-permissioned file. Refuse to
          // use a pre-existing file that this user does not own or that
          // grants group/other any access, rather than trying to tighten it
          // in place (which would itself require already trusting it enough
          // to touch). Not meaningful on Windows (no POSIX uid/mode model,
          // and no `process.getuid`) -- skip there.
          if (process.platform === "win32" || isPrivateRegularFile(candidate)) {
            fd = candidate;
            if (fstatSync(fd).size >= MAX_LOG_BYTES) {
              // See MAX_LOG_BYTES above: bound growth from routine,
              // expected signal churn (not just crashes) by resetting an
              // overgrown file back to empty before this process's first
              // write. A plain ftruncateSync() on this already-open
              // O_APPEND descriptor fails with EPERM on Windows
              // (empirically confirmed: Windows refuses to truncate a
              // handle opened in append mode) -- so instead, close it,
              // reopen the same path to reset it, then reopen again in the
              // normal append mode this function keeps for every later
              // write. Both reset-path opens keep the exact same
              // O_NOFOLLOW/O_NONBLOCK protective flags as the very first
              // open above (closing the original fd reopens a TOCTOU
              // window: `logPath` could have been replaced with a symlink
              // or FIFO in between), and each new fd is independently
              // revalidated with `isPrivateRegularFile()` -- a reset must
              // not silently downgrade to writing through an
              // attacker-substituted path just because the *original*
              // open already proved trustworthy. `fd` is cleared first so
              // that if any step here throws (propagating to the catch
              // below, which marks this process as given up on
              // diagnostics), a later call never risks reusing an
              // already-closed descriptor.
              closeSync(fd);
              fd = undefined;
              const resetFlags =
                fsConstants.O_CREAT |
                fsConstants.O_WRONLY |
                fsConstants.O_TRUNC |
                (fsConstants.O_NOFOLLOW ?? 0) |
                (fsConstants.O_NONBLOCK ?? 0);
              const truncater = openSync(logPath, resetFlags, 0o600);
              if (process.platform !== "win32" && !isPrivateRegularFile(truncater)) {
                closeSync(truncater);
                throw new Error("crash log path is no longer trustworthy after reset");
              }
              closeSync(truncater);
              const reopened = openSync(logPath, OPEN_FLAGS, 0o600);
              if (process.platform !== "win32" && !isPrivateRegularFile(reopened)) {
                closeSync(reopened);
                throw new Error("crash log path is no longer trustworthy after reopen");
              }
              fd = reopened;
            }
          } else {
            closeSync(candidate);
            openFailed = true;
          }
        } catch {
          openFailed = true;
        }
      }
      if (fd === undefined) return false;
      writeSync(
        fd,
        `${new Date().toISOString()} pid=${process.pid} ${label}: ${detail}\n`,
        null,
        "utf-8",
      );
      return true;
    } catch {
      // Diagnostic logging must never become a second crash cause.
      return false;
    }
  };
}

const SIGNALS = ["SIGTERM", "SIGINT", "SIGHUP"];

/**
 * Best-effort, never-throwing description of an arbitrary thrown/rejected
 * value. A thrown value is not required to be an `Error` -- reading
 * `.stack` or coercing with `String()` can run a hostile/buggy user-defined
 * getter, `toString()`, or `Symbol.toPrimitive` that itself throws. Those
 * expressions would otherwise run directly inside `uncaughtException`/
 * `unhandledRejection` handler bodies, outside `emergencyLog`'s own
 * try/catch (which only guards its own file I/O) -- a second exception
 * thrown while Node is already dispatching `uncaughtException` is fatal and
 * bypasses further JS, losing the very diagnostic this module exists to
 * capture. Every failure path here falls back to a fixed, allocation-free
 * string.
 */
function describeFailure(value) {
  try {
    if (value && typeof value === "object" && typeof value.stack === "string") {
      return value.stack;
    }
    return String(value);
  } catch {
    return "<failure detail unavailable: describing it threw>";
  }
}

/**
 * Registers process-level crash diagnostics using `emergencyLog` (from
 * {@link createEmergencyLog}). Returns an `uninstall()` function that
 * removes exactly the listeners this call added, so a test (or any future
 * caller needing to swap diagnostics) can clean up without disturbing
 * listeners installed elsewhere.
 *
 * Registering `uncaughtException`/`unhandledRejection` listeners suppresses
 * Node's own default auto-exit-on-crash behavior; each handler re-exits
 * explicitly (status 1, matching what Node's default handling already did)
 * to preserve the process's prior termination behavior exactly -- this is
 * visibility, not a behavior change.
 *
 * Signal handlers do NOT call `process.exit()`: on POSIX, a parent process
 * distinguishes a signal-terminated child (`code=null, signal="SIGTERM"`)
 * from a normal exit (`code=<n>, signal=null`), and a host that inspects
 * this may classify or retry the two differently. Converting a signal into
 * `process.exit(128 + signum)` would report a normal exit and silently
 * change that contract. Instead, each handler logs, removes its own
 * listener for that one signal (so the re-raise below cannot recurse into
 * this same handler), and re-sends the identical signal to this process.
 * With no listener left for it, the OS's default disposition applies and
 * genuinely terminates the process by that signal -- Node then reports the
 * same `(code=null, signal=<signal>)` shape a parent would have seen with no
 * diagnostics installed at all.
 */
export function installEmergencyDiagnostics(emergencyLog) {
  // Tracks whether joinSession() has already resolved, purely in memory --
  // NOT written to the durable log on its own. Every successful extension
  // instance (this module is re-imported many times per machine lifetime:
  // once per discovery pass, plus once per reconnect/resume) would otherwise
  // append an unconditional "reached readiness" line to this fixed,
  // unrotated, machine-global file even when nothing ever goes wrong --
  // exactly the unbounded-growth failure mode the onExit code=0 skip below
  // already guards against. Instead, this flag is folded into whichever
  // failure entry (if any) actually gets logged, so a reader can still tell
  // whether a captured crash happened before or after readiness, without
  // paying for a write on the overwhelmingly common all-is-well path.
  let ready = false;
  // emergencyLog() deliberately swallows every open/write failure (missing
  // directory, full disk, an untrusted pre-existing path, ...) so that
  // diagnostic logging itself can never become a second crash cause -- but
  // installing these listeners at all suppresses Node's own default
  // uncaught-exception report to stderr. Put those two together and a
  // logging failure on an uncaughtException/unhandledRejection would leave
  // NEITHER a file entry NOR any stderr output: the exact silent-exit
  // symptom this module exists to diagnose, just moved one layer down. This
  // wrapper checks emergencyLog()'s own success/failure return and, only on
  // failure, falls back to a best-effort synchronous stderr write (itself
  // wrapped in try/catch, so a failing stderr write -- e.g. a closed/broken
  // fd 2 -- still cannot crash the handler that is already mid-failure).
  const logOrFallback = (label, detail) => {
    if (emergencyLog(label, detail)) return;
    try {
      process.stderr.write(
        `context-handoff emergency diagnostics (log write failed): ${label}: ${detail}\n`,
      );
    } catch {
      // Nothing further can be done; see the module doc comment.
    }
  };
  const onUncaughtException = (err) => {
    logOrFallback("uncaughtException", `ready=${ready} ${describeFailure(err)}`);
    process.exit(1);
  };
  const onUnhandledRejection = (reason) => {
    logOrFallback("unhandledRejection", `ready=${ready} ${describeFailure(reason)}`);
    process.exit(1);
  };
  // Only a non-zero exit is logged. This module's own extension.mjs is
  // dynamically re-imported many times over a machine's lifetime -- once
  // per discovery pass, plus once per reconnect/resume -- and the ordinary
  // outcome of nearly all of those forks is a routine `process.exit(0)`.
  // Logging every one of them would make this fixed, unrotated,
  // machine-global scratch file grow without bound over a long-lived host
  // (the existing lifecycle-logging pattern -- see
  // docs/patterns/lifecycle-activity-logging.md -- deliberately bounds or
  // reboot-volatilizes every tier it defines; this file has neither
  // property, so it must not record routine, uninteresting events at all).
  // A normal exit carries no diagnostic value on its own here: the
  // interesting signal is the *presence* of an uncaughtException/
  // unhandledRejection/signal line, not the routine absence of one. Not
  // itself routed through logOrFallback(): a routine, uninteresting event
  // has no fallback message worth ever printing to stderr, unlike an actual
  // failure above.
  const onExit = (code) => {
    if (code !== 0) emergencyLog("exit", `code=${code} ready=${ready}`);
  };
  const signalHandlers = new Map();
  for (const signal of SIGNALS) {
    const handler = () => {
      logOrFallback("signal", `${signal} ready=${ready}`);
      process.off(signal, handler);
      process.kill(process.pid, signal);
    };
    signalHandlers.set(signal, handler);
  }

  process.on("uncaughtException", onUncaughtException);
  process.on("unhandledRejection", onUnhandledRejection);
  process.on("exit", onExit);
  for (const [signal, handler] of signalHandlers) {
    process.on(signal, handler);
  }

  return {
    // Called once joinSession() actually resolves; purely an in-memory
    // flag, see the `ready` rationale above -- never itself a durable write.
    markReady() {
      ready = true;
    },
    uninstall() {
      process.off("uncaughtException", onUncaughtException);
      process.off("unhandledRejection", onUnhandledRejection);
      process.off("exit", onExit);
      for (const [signal, handler] of signalHandlers) {
        process.off(signal, handler);
      }
    },
  };
}
