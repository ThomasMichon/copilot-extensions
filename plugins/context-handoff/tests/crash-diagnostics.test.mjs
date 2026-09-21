// Subprocess tests for crash-diagnostics.mjs: exercises the real
// process.on('uncaughtException'/'unhandledRejection'/'exit'/'SIGTERM'/
// 'SIGINT'/'SIGHUP') lifecycle handlers and asserts both the durable log
// content and the actual process exit status. Each scenario runs in its own
// child process (tests/fixtures/crash-diagnostics-harness.mjs) -- never in
// the shared `node --test` runner process, since these handlers call
// process.exit() by design.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const harness = join(here, "fixtures", "crash-diagnostics-harness.mjs");

async function withCrashLog(fn) {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-crash-diag-"));
  const logPath = join(dir, "crash.log");
  try {
    await fn(logPath);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function readLogSafe(logPath) {
  try {
    return readFileSync(logPath, "utf-8");
  } catch {
    return "";
  }
}

test("clean exit logs only the exit event, no failure entries", async () => {
  await withCrashLog(async (logPath) => {
    const result = spawnSync(process.execPath, [harness, "clean-exit", logPath], {
      encoding: "utf-8",
    });
    assert.equal(result.status, 0);
    const log = readLogSafe(logPath);
    assert.match(log, /\bexit: code=0\b/);
    assert.doesNotMatch(log, /uncaughtException|unhandledRejection|\bsignal\b/);
  });
});

test("an uncaught exception is logged with its stack, then exits 1", async () => {
  await withCrashLog(async (logPath) => {
    const result = spawnSync(process.execPath, [harness, "uncaught-exception", logPath], {
      encoding: "utf-8",
    });
    assert.equal(result.status, 1);
    const log = readLogSafe(logPath);
    assert.match(log, /uncaughtException: Error: boom-uncaught/);
    // The stack trace is multi-line; confirm more than just the message
    // survived (i.e. `err.stack`, not `String(err)`, was actually logged).
    assert.match(log, /at /);
    assert.match(log, /\bexit: code=1\b/);
  });
});

// A predictable path in a shared os.tmpdir() means another local user could
// have pre-created an ordinary, world-readable regular file there before
// this process ever runs. O_NOFOLLOW alone does not protect against this
// (it only rejects a symlink), and O_CREAT's mode argument has no effect
// once the file already exists -- appending straight into it would leak
// sensitive stack traces at whatever permissions that pre-existing file
// happened to carry. Not meaningful on Windows (no POSIX uid/mode model).
test(
  "a pre-existing world-readable file at the log path is never written to",
  { skip: process.platform === "win32" },
  async () => {
    await withCrashLog(async (logPath) => {
      writeFileSync(logPath, "not ours\n", { mode: 0o644 });
      const before = statSync(logPath);
      const result = spawnSync(process.execPath, [harness, "uncaught-exception", logPath], {
        encoding: "utf-8",
      });
      // The crash-log write is refused, but the process's own crash
      // handling (log-then-exit) is otherwise unaffected.
      assert.equal(result.status, 1);
      const after = statSync(logPath);
      assert.equal(readFileSync(logPath, "utf-8"), "not ours\n");
      assert.equal(after.mode, before.mode);
    });
  },
);

test("an unhandled rejection is logged with its stack, then exits 1", async () => {
  await withCrashLog(async (logPath) => {
    const result = spawnSync(process.execPath, [harness, "unhandled-rejection", logPath], {
      encoding: "utf-8",
    });
    assert.equal(result.status, 1);
    const log = readLogSafe(logPath);
    assert.match(log, /unhandledRejection: Error: boom-rejected/);
    assert.match(log, /at /);
    assert.match(log, /\bexit: code=1\b/);
  });
});

// Windows does not deliver a real POSIX signal to a JS handler when the
// signal is sent via `(child)process.kill()`: Node's own Windows emulation
// causes SIGTERM/SIGINT/SIGHUP sent this way to terminate the target
// process unconditionally, bypassing any registered `process.on(signal)`
// listener entirely (confirmed empirically against this repo's actual
// Node/Windows runtime -- a self-directed `process.kill(pid, "SIGTERM")`
// exits before either the handler or a competing timeout ever logs
// anything). A real interactive Ctrl+C is a different code path and *does*
// reach the handler, but that is not something a subprocess test can send.
// So this assertion is POSIX-only; the extension's own comments already
// document SIGTERM as inert on Windows for the same reason.
test(
  "SIGTERM is logged, then re-raised so the process still dies BY that signal",
  { skip: process.platform === "win32" },
  async () => {
    await withCrashLog(async (logPath) => {
      const child = spawn(process.execPath, [harness, "wait-for-signal", logPath], {
        stdio: ["ignore", "pipe", "inherit"],
      });
      await new Promise((resolve, reject) => {
        let buffered = "";
        child.stdout.on("data", (chunk) => {
          buffered += chunk;
          if (buffered.includes("READY\n")) resolve();
        });
        child.on("error", reject);
      });
      child.kill("SIGTERM");
      const [code, signal] = await new Promise((resolve) => {
        child.on("exit", (exitCode, exitSignal) => resolve([exitCode, exitSignal]));
      });
      // installEmergencyDiagnostics() deliberately does NOT call
      // process.exit() for a signal: it logs, removes its own listener for
      // that one signal, then re-sends the same signal to this process so
      // the OS's default disposition (genuine termination) applies with no
      // listener left to intercept it -- exactly the (code=null,
      // signal="SIGTERM") shape a parent would see with no diagnostics
      // installed at all. Converting this into a plain process.exit(143)
      // would instead report (code=143, signal=null), silently changing
      // what a host watching for a signal-terminated child observes.
      assert.equal(code, null);
      assert.equal(signal, "SIGTERM");
      const log = readLogSafe(logPath);
      assert.match(log, /\bsignal: SIGTERM\b/);
      // The re-raised signal kills the process before Node's own 'exit'
      // event has a chance to run (no listener remains to intercept it at
      // that point), so -- unlike every other scenario in this file -- no
      // "exit: code=..." line is expected here.
      assert.doesNotMatch(log, /\bexit: code=/);
    });
  },
);
