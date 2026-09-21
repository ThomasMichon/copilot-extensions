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

import { appendFileSync } from "node:fs";
import { constants as osConstants, tmpdir } from "node:os";
import { join } from "node:path";

export const DEFAULT_CRASH_LOG = join(tmpdir(), "context-handoff-extension-crash.log");

/**
 * Builds an `emergencyLog(label, detail)` function bound to `logPath`
 * (default: `DEFAULT_CRASH_LOG`). Synchronous I/O only -- must survive a
 * process already mid-shutdown -- and every failure is swallowed so
 * diagnostic logging can never itself become a new crash cause.
 */
export function createEmergencyLog(logPath = DEFAULT_CRASH_LOG) {
  return function emergencyLog(label, detail) {
    try {
      appendFileSync(
        logPath,
        `${new Date().toISOString()} pid=${process.pid} ${label}: ${detail}\n`,
        { encoding: "utf-8" },
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
 * Registering `uncaughtException`/`unhandledRejection`/signal listeners
 * suppresses Node's own default auto-exit-on-crash/terminate-on-signal
 * behavior; each handler below re-exits explicitly to preserve the process's
 * prior termination behavior exactly -- this is visibility, not a behavior
 * change. Signal handlers use the conventional 128+signum exit status
 * (143/130/129 for SIGTERM/SIGINT/SIGHUP) rather than a flat 0, so a host
 * inspecting the exit code still sees "terminated by signal", not "exited
 * successfully".
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
      process.exit(128 + osConstants.signals[signal]);
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
