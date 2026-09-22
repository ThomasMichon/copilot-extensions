// Subprocess tests for crash-diagnostics.mjs: exercises the real
// process.on('uncaughtException'/'unhandledRejection'/'exit'/'SIGTERM'/
// 'SIGINT'/'SIGHUP') lifecycle handlers and asserts both the durable log
// content and the actual process exit status. Each scenario runs in its own
// child process (tests/fixtures/crash-diagnostics-harness.mjs) -- never in
// the shared `node --test` runner process, since these handlers call
// process.exit() by design.
//
// EVERY case in this file (except one pure-file-I/O truncation check) --
// not only the world-readable-file/symlink/FIFO attack simulations --
// spawns at least one real subprocess, and the required `guards + lint` CI
// lane runs every `plugins/*/tests/*.test.mjs` file unconditionally, for
// every PR in this whole monorepo (`.github/workflows/ci.yml`), not just
// ones that touch context-handoff. Paying that subprocess-spawn cost (and,
// for the FIFO case, a real `mkfifo` dependency) on every unrelated PR's
// required CI would be a blast-radius mismatch for a single plugin's
// regression suite. So this entire file gates on
// `CONTEXT_HANDOFF_CRASH_DIAGNOSTICS_STRESS`, which `ci.yml` sets
// automatically whenever a PR's own diff touches
// `plugins/context-handoff/` (so this plugin's own PRs still get full
// coverage in required CI) and which the scheduled/manual
// `crash-diagnostics-stress.yml` workflow also sets unconditionally
// (mirroring `INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS`'s existing
// scheduled/manual-only pattern -- see
// `.github/workflows/installation-context-full.yml`), so the full suite
// still runs periodically no matter what any given PR touches.
import { spawn, spawnSync } from "node:child_process";
import {
  chmodSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";
import { createEmergencyLog } from "../extensions/context-handoff/crash-diagnostics.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const harness = join(here, "fixtures", "crash-diagnostics-harness.mjs");
const RUN_STRESS_CASES = Boolean(process.env.CONTEXT_HANDOFF_CRASH_DIAGNOSTICS_STRESS);

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

// Each real log entry starts a fresh line stamped `<ISO timestamp>
// pid=<pid> <label>: ...` (see emergencyLog() in crash-diagnostics.mjs);
// a multi-line Error.stack's trailing `at ...` lines are NOT themselves
// separately stamped, so counting stamped lines counts entries, not raw
// lines. Used to assert an exact entry count -- e.g. that no extra,
// unconditional marker entry (such as the old per-instance
// `joinSession-resolved` write this PR removed) has been reintroduced.
function logEntryLabels(log) {
  return [...log.matchAll(/^\S+ pid=\d+ (\S+):/gm)].map((m) => m[1]);
}

test(
  "clean exit records nothing at all -- routine exits are not logged",
  { skip: !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const result = spawnSync(process.execPath, [harness, "clean-exit", logPath], {
        encoding: "utf-8",
      });
      assert.equal(result.status, 0);
      // A routine code=0 exit is deliberately never logged (see the rationale
      // beside onExit in crash-diagnostics.mjs): this extension is
      // re-imported many times per machine lifetime, and logging every
      // uneventful fork would make this fixed, unrotated file grow without
      // bound. readLogSafe() returning "" also covers the file never having
      // been created at all, since createEmergencyLog() only opens it lazily
      // on first actual write.
      assert.equal(readLogSafe(logPath), "");
    });
  },
);

test(
  "an uncaught exception is logged with its stack, then exits 1",
  { skip: !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const result = spawnSync(process.execPath, [harness, "uncaught-exception", logPath], {
        encoding: "utf-8",
      });
      assert.equal(result.status, 1);
      const log = readLogSafe(logPath);
      assert.match(log, /uncaughtException: ready=false Error: boom-uncaught/);
      // The stack trace is multi-line; confirm more than just the message
      // survived (i.e. `err.stack`, not `String(err)`, was actually logged).
      assert.match(log, /at /);
      assert.match(log, /\bexit: code=1\b/);
    });
  },
);

// A thrown value is not required to be an Error. Reading a hostile/buggy
// .stack getter runs arbitrary user code that can itself throw -- that must
// not crash describeFailure() (and lose the diagnostic) while it is already
// handling the original failure.
test(
  "a hostile throwing .stack getter does not itself crash the handler",
  { skip: !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const result = spawnSync(process.execPath, [harness, "throw-hostile-getter", logPath], {
        encoding: "utf-8",
      });
      assert.equal(result.status, 1);
      const log = readLogSafe(logPath);
      assert.match(log, /uncaughtException: ready=false <failure detail unavailable: describing it threw>/);
      assert.match(log, /\bexit: code=1\b/);
    });
  },
);

// markReady() is purely in-memory (see installEmergencyDiagnostics() in
// crash-diagnostics.mjs) -- it must never write a durable line on its own,
// but a failure captured *after* it was called must reflect ready=true, not
// the ready=false default asserted by the other cases above.
test(
  "a failure after markReady() logs ready=true",
  { skip: !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const result = spawnSync(
        process.execPath,
        [harness, "uncaught-exception-after-ready", logPath],
        { encoding: "utf-8" },
      );
      assert.equal(result.status, 1);
      const log = readLogSafe(logPath);
      assert.match(log, /uncaughtException: ready=true Error: boom-after-ready/);
      assert.match(log, /\bexit: code=1\b/);
      // Guard against a regression to the old per-instance
      // `joinSession-resolved` write (or any other unconditional marker):
      // markReady() itself must never append a durable line, so exactly
      // these two entries -- the failure and the resulting exit -- may
      // exist, nothing else.
      assert.deepEqual(logEntryLabels(log), ["uncaughtException", "exit"]);
    });
  },
);

// createEmergencyLog()'s own truncation guard, exercised in-process
// (no subprocess needed -- this is pure file I/O, not a
// process.on(...)-installing call) rather than via the harness: SIGTERM is
// the CLI's own routine mechanism for /clear and foreground-session
// replacement, so this file can otherwise grow without bound purely from
// expected lifecycle churn, never a crash. Gated alongside the rest of this
// file's cases (see the header comment) since it deliberately writes a
// multi-megabyte fixture.
test(
  "an overgrown crash log is truncated before the next entry, not left to grow forever",
  { skip: !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const oneMiB = 1_048_576;
      writeFileSync(logPath, "x".repeat(oneMiB + 10));
      // Must pass isPrivateRegularFile()'s ownership/mode check the same
      // way the real crash-log file itself would (private, 0600) -- a
      // plain writeFileSync()'s mode is filtered through this process's
      // umask, which can otherwise leave permissive group/other bits that
      // make createEmergencyLog() treat this fixture as an untrusted
      // pre-existing file and refuse to write to it at all, rather than
      // truncating it (the behavior actually under test here).
      chmodSync(logPath, 0o600);
      const emergencyLog = createEmergencyLog(logPath);
      emergencyLog("signal", "SIGTERM ready=true");
      const content = readFileSync(logPath, "utf-8");
      assert.ok(
        content.length < oneMiB,
        `expected the oversized pre-existing content to be truncated away, got ${content.length} bytes`,
      );
      assert.match(content, /signal: SIGTERM ready=true/);
    });
  },
);

// A predictable path in a shared os.tmpdir() means another local user could
// have pre-created an ordinary, world-readable regular file there before
// this process ever runs. O_NOFOLLOW alone does not protect against this
// (it only rejects a symlink), and O_CREAT's mode argument has no effect
// once the file already exists -- appending straight into it would leak
// sensitive stack traces at whatever permissions that pre-existing file
// happened to carry. Not meaningful on Windows (no POSIX uid/mode model);
// gated to the scheduled/manual stress lane -- see the file header comment.
test(
  "a pre-existing world-readable file at the log path is never written to",
  { skip: process.platform === "win32" || !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      writeFileSync(logPath, "not ours\n", { mode: 0o644 });
      // writeFileSync()'s mode is filtered through this process's umask
      // (e.g. a umask of 0o077 would silently produce 0o600, not 0o644),
      // which would make isPrivateRegularFile() accept this file as private
      // and defeat the entire point of this simulation. chmodSync() sets
      // the mode directly, bypassing the umask, so the file is
      // deterministically world-readable regardless of the host's umask.
      chmodSync(logPath, 0o644);
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

// A pre-created symlink at the predictable log path could otherwise
// redirect this process's own stack traces to an attacker-chosen file
// elsewhere on disk. O_NOFOLLOW is what is supposed to prevent this (the
// kernel refuses to open through an existing symlink at all); this test
// actually exercises that guarantee rather than merely asserting the flag
// is present in source. Gated to the scheduled/manual stress lane -- see
// the file header comment.
test(
  "a pre-existing symlink at the log path is never followed or written through",
  { skip: process.platform === "win32" || !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const dir = dirname(logPath);
      const target = join(dir, "attacker-target.log");
      writeFileSync(target, "untouched\n");
      symlinkSync(target, logPath);
      const result = spawnSync(process.execPath, [harness, "uncaught-exception", logPath], {
        encoding: "utf-8",
      });
      // The crash-log write is refused (O_NOFOLLOW makes the open() call
      // itself fail), but the process's own crash handling is otherwise
      // unaffected.
      assert.equal(result.status, 1);
      assert.equal(readFileSync(target, "utf-8"), "untouched\n");
    });
  },
);

// A different local user could pre-create the predictable log path as a
// FIFO (named pipe) instead of a regular file or symlink. Opening a FIFO
// for writing with no reader attached blocks indefinitely under a plain
// O_WRONLY open -- fatal here, since this code runs from a crash/signal
// handler: the process would hang forever instead of exiting and recording
// anything. O_NONBLOCK is what is supposed to make that open fail
// immediately instead; this test actually opens a real FIFO to prove it,
// bounded by node:test's own default per-test timeout so a regression hangs
// this test rather than the whole suite indefinitely. Requires the external
// `mkfifo` binary, so this stays out of the always-run required lane
// regardless of platform -- gated to the scheduled/manual stress lane, see
// the file header comment.
test(
  "a pre-existing FIFO at the log path does not block the crash handler",
  { skip: process.platform === "win32" || !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const mkfifo = spawnSync("mkfifo", [logPath]);
      assert.equal(mkfifo.status, 0, "mkfifo must be available on this POSIX host for this test");
      const result = spawnSync(process.execPath, [harness, "uncaught-exception", logPath], {
        encoding: "utf-8",
        timeout: 10_000,
      });
      assert.notEqual(result.status, null, "the harness must exit, not hang, on a FIFO");
      assert.equal(result.status, 1);
    });
  },
);

test(
  "an unhandled rejection is logged with its stack, then exits 1",
  { skip: !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const result = spawnSync(process.execPath, [harness, "unhandled-rejection", logPath], {
        encoding: "utf-8",
      });
      assert.equal(result.status, 1);
      const log = readLogSafe(logPath);
      assert.match(log, /unhandledRejection: ready=false Error: boom-rejected/);
      assert.match(log, /at /);
      assert.match(log, /\bexit: code=1\b/);
    });
  },
);

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
  { skip: process.platform === "win32" || !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const child = spawn(process.execPath, [harness, "wait-for-signal", logPath], {
        stdio: ["ignore", "pipe", "inherit"],
      });
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          child.kill("SIGKILL");
          reject(new Error("harness did not print READY within 5s"));
        }, 5_000);
        let buffered = "";
        child.stdout.on("data", (chunk) => {
          buffered += chunk;
          if (buffered.includes("READY\n")) {
            clearTimeout(timer);
            resolve();
          }
        });
        child.on("error", (err) => {
          clearTimeout(timer);
          reject(err);
        });
      });
      child.kill("SIGTERM");
      const [code, signal] = await new Promise((resolve, reject) => {
        // Bounded: if the signal re-raise regresses (e.g. a stray listener
        // survives and swallows the re-sent signal instead of letting the
        // OS's default disposition terminate the process), this must fail
        // loudly within a fixed window rather than hang the required CI job
        // indefinitely waiting for an 'exit' that will never come.
        const timer = setTimeout(() => {
          child.kill("SIGKILL");
          reject(new Error("harness did not exit within 5s of receiving SIGTERM"));
        }, 5_000);
        child.on("error", (err) => {
          clearTimeout(timer);
          reject(err);
        });
        child.on("exit", (exitCode, exitSignal) => {
          clearTimeout(timer);
          resolve([exitCode, exitSignal]);
        });
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
