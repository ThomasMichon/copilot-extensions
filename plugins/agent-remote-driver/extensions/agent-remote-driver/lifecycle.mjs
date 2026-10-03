// agent-remote-driver -- SDK-free process lifecycle helpers.
//
// extension.mjs's bounded listen-retry, signal-cleanup-then-reraise, and
// heartbeat-timer wiring are all plain process/timer logic with no
// @github/copilot-sdk dependency -- extracted here (rather than left inline
// in the SDK-coupled top-level module) specifically so they are directly
// unit-testable without loading joinSession() or a real SDK session at all.
// Every dependency (the retried function, the process-like object, the
// timer scheduler) is injected, so a test can exercise bounded-retry
// exhaustion, the remove-then-reraise signal sequence, and heartbeat
// start/stop without ever sending a real OS signal or waiting on a real
// timer.

// Retries an async `fn` up to `maxAttempts` times, bounded -- never an
// unbounded retry loop (the anti-process-bombing requirement). Rethrows the
// last attempt's error once `maxAttempts` is exhausted. `onAttemptFailed`
// (optional) is called with (attempt, error) after each failed try, purely
// for logging -- it must never throw itself.
export async function boundedRetry(fn, maxAttempts, onAttemptFailed) {
  let lastErr;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await fn(attempt);
    } catch (e) {
      lastErr = e;
      if (onAttemptFailed) {
        try {
          onAttemptFailed(attempt, e);
        } catch {
          /* logging must never mask the real retry failure */
        }
      }
    }
  }
  throw lastErr;
}

// Installs a handler for each of `signals` that runs `cleanup()` and then
// lets the signal actually terminate the process via its OS default
// disposition -- never `process.exit()`, which would misreport a
// signal-terminated process as a normal exit to anything inspecting its
// (code, signal) pair (see the extension.mjs rationale this mirrors,
// matching context-handoff/extensions/context-handoff/crash-diagnostics.mjs's
// established pattern).
//
// Registering a listener for a signal suppresses Node's own default
// termination for it -- so a handler that only runs cleanup and returns
// would leave the process alive (with e.g. a heartbeat timer still armed,
// undoing the very cleanup it just did on its next tick). The fix: remove
// THIS listener (so the re-raise cannot recurse into itself), then re-send
// the identical signal -- with no listener left for it, the OS's default
// disposition actually applies.
//
// `proc` defaults to the real global `process` but is injectable so a test
// can exercise the exact handler sequence (cleanup -> off -> kill) without
// ever delivering a real OS signal to anything.
export function installSignalCleanup(signals, cleanup, proc = process) {
  const handlers = new Map();
  for (const signal of signals) {
    const handler = () => {
      cleanup();
      proc.off(signal, handler);
      proc.kill(proc.pid, signal);
    };
    handlers.set(signal, handler);
    proc.on(signal, handler);
  }
  return {
    // Mainly for tests -- removes every installed handler without
    // re-raising anything (used to tear down between test cases).
    uninstall() {
      for (const [signal, handler] of handlers) proc.off(signal, handler);
    },
  };
}

// A start/stop-able periodic timer wrapping `tick`. `setIntervalFn`/
// `clearIntervalFn` default to the real globals but are injectable so a
// test can drive `tick` deterministically (call it directly) instead of
// waiting on a real interval.
export function createIntervalTimer(tick, intervalMs, { setIntervalFn = setInterval, clearIntervalFn = clearInterval } = {}) {
  let handle = null;
  return {
    start() {
      if (handle !== null) return;
      handle = setIntervalFn(tick, intervalMs);
      if (handle && typeof handle.unref === "function") handle.unref();
    },
    stop() {
      if (handle === null) return;
      clearIntervalFn(handle);
      handle = null;
    },
    get running() {
      return handle !== null;
    },
  };
}
