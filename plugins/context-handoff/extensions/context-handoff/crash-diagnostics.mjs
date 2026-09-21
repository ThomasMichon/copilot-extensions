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
// exit code -- and the presence/absence of the `exit` line it always writes
// pinpoints whether Node itself decided to terminate (uncaught exception,
// rejected top-level await, natural event-loop drain) or something external
// force-killed the process before Node's own exit handling could run (no
// `exit` line is ever written in that case, since SIGKILL and an external
// process-tree kill bypass it entirely).

import { constants as fsConstants, openSync, writeSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const DEFAULT_CRASH_LOG = join(tmpdir(), "context-handoff-extension-crash.log");

// `os.tmpdir()` is commonly a shared, world-writable directory on POSIX
// (`/tmp`), so a predictable filename there is both readable by other local
// users and open to a pre-created-symlink attack redirecting the write
// somewhere else entirely. `O_NOFOLLOW` makes the kernel itself refuse to
// open through an existing symlink (atomic, not a check-then-open race);
// mode 0o600 keeps a freshly created file private to this user. `O_NOFOLLOW`
// does not exist on Windows (no POSIX symlink-attack surface there in the
// same shape) -- fall back to a plain create/append flag set on that
// platform, since the flag is simply absent from `fs.constants`, not merely
// disabled.
const OPEN_FLAGS =
  fsConstants.O_CREAT |
  fsConstants.O_WRONLY |
  fsConstants.O_APPEND |
  (fsConstants.O_NOFOLLOW ?? 0);

/**
 * Builds an `emergencyLog(label, detail)` function bound to `logPath`
 * (default: `DEFAULT_CRASH_LOG`). Opens one private (0600), append-mode file
 * descriptor lazily on first use and reuses it -- synchronous I/O only (must
 * survive a process already mid-shutdown), and every failure (a pre-existing
 * symlink tripping `O_NOFOLLOW`, a permissions error, a full disk, ...) is
 * swallowed so diagnostic logging can never itself become a new crash cause.
 */
export function createEmergencyLog(logPath = DEFAULT_CRASH_LOG) {
  let fd;
  let openFailed = false;
  return function emergencyLog(label, detail) {
    try {
      if (fd === undefined && !openFailed) {
        try {
          fd = openSync(logPath, OPEN_FLAGS, 0o600);
        } catch {
          openFailed = true;
        }
      }
      if (fd === undefined) return;
      writeSync(
        fd,
        `${new Date().toISOString()} pid=${process.pid} ${label}: ${detail}\n`,
        null,
        "utf-8",
      );
    } catch {
      // Diagnostic logging must never become a second crash cause.
    }
  };
}

const SIGNALS = ["SIGTERM", "SIGINT", "SIGHUP"];

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
  const onUncaughtException = (err) => {
    emergencyLog("uncaughtException", err && err.stack ? err.stack : String(err));
    process.exit(1);
  };
  const onUnhandledRejection = (reason) => {
    emergencyLog(
      "unhandledRejection",
      reason instanceof Error ? reason.stack : String(reason),
    );
    process.exit(1);
  };
  const onExit = (code) => {
    emergencyLog("exit", `code=${code}`);
  };
  const signalHandlers = new Map();
  for (const signal of SIGNALS) {
    const handler = () => {
      emergencyLog("signal", signal);
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

  return function uninstall() {
    process.off("uncaughtException", onUncaughtException);
    process.off("unhandledRejection", onUnhandledRejection);
    process.off("exit", onExit);
    for (const [signal, handler] of signalHandlers) {
      process.off(signal, handler);
    }
  };
}
