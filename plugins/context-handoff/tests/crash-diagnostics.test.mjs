// Subprocess tests for crash-diagnostics.mjs: exercises the real
// process.on('uncaughtException'/'unhandledRejection'/'exit'/'SIGTERM'/
// 'SIGINT'/'SIGHUP') lifecycle handlers and asserts both the durable log
// content and the actual process exit status. Each scenario runs in its own
// child process (tests/fixtures/crash-diagnostics-harness.mjs) -- never in
// the shared `node --test` runner process, since these handlers call
// process.exit() by design.
//
// This file has two tiers, both run by the required `guards + lint` CI
// lane's unconditional `plugins/*/tests/*.test.mjs` glob (`ci.yml`) for
// every PR in the whole monorepo, not just ones that touch
// context-handoff:
//
// - A small, always-run smoke contract: the cheap subprocess cases (clean
//   exit, uncaught exception, unhandled rejection, a hostile-getter thrown
//   value, a failure after markReady(), a non-zero exit with no preceding
//   failure, SIGTERM) plus the always-run, non-subprocess production
//   wiring contract test. None of these need a POSIX toolchain beyond
//   spawning `node` itself.
// - A stress-gated set behind `CONTEXT_HANDOFF_CRASH_DIAGNOSTICS_STRESS`:
//   the world-readable-file/symlink/FIFO attack simulations (the FIFO case
//   needs a real `mkfifo` binary) and the 1 MiB rotation-fixture case.
//   These are either meaningfully more expensive or add a POSIX-toolchain
//   dependency no other required-CI test here needs, so they are reserved
//   for the scheduled/manual `crash-diagnostics-stress.yml` workflow
//   (mirroring `INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS`'s existing
//   scheduled/manual-only pattern -- see
//   `.github/workflows/installation-context-full.yml`) rather than ever
//   running in required PR CI, regardless of what a given PR touches (an
//   earlier revision of this PR instead had `ci.yml` set the stress env var
//   whenever a PR's diff touched `plugins/context-handoff/`, giving this
//   plugin's own PRs the full stress set in required CI -- per review,
//   that still put a subprocess-heavy matrix in the required lane instead
//   of using a focused/scheduled split, so it was removed in favor of this
//   simpler two-tier design).
import { spawn, spawnSync } from "node:child_process";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
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
import { createEmergencyLog, sidecarDirFor } from "../extensions/context-handoff/crash-diagnostics.mjs";

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

// Finds the index of the "(" that closes the call whose opening "(" is at
// `openParenIndex`, counting depth but skipping over any "("/")" that fall
// inside a single/double-quoted string, a template literal, or a //
// line/`/* */` block comment -- this file's own extension.mjs has a huge
// options-object argument full of prompt/description prose that can
// contain unbalanced parens as plain English, which a naive depth counter
// would misread. Deliberately minimal (no escape-sequence handling beyond
// a basic backslash skip, no nested-template-literal `${}` awareness) --
// sufficient for locating one specific, known call in one specific file,
// not a general-purpose JS parser.
function findMatchingCloseParen(source, openParenIndex) {
  let depth = 0;
  let quote = null; // one of "'", '"', "`", or null
  let inLineComment = false;
  let inBlockComment = false;
  for (let i = openParenIndex; i < source.length; i += 1) {
    const ch = source[i];
    if (inLineComment) {
      if (ch === "\n") inLineComment = false;
      continue;
    }
    if (inBlockComment) {
      if (ch === "*" && source[i + 1] === "/") {
        inBlockComment = false;
        i += 1;
      }
      continue;
    }
    if (quote) {
      if (ch === "\\") {
        i += 1; // skip the escaped character
      } else if (ch === quote) {
        quote = null;
      }
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      quote = ch;
      continue;
    }
    if (ch === "/" && source[i + 1] === "/") {
      inLineComment = true;
      continue;
    }
    if (ch === "/" && source[i + 1] === "*") {
      inBlockComment = true;
      continue;
    }
    if (ch === "(") {
      depth += 1;
    } else if (ch === ")") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

test("clean exit records nothing at all -- routine exits are not logged", async () => {
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
});

test("an uncaught exception is logged with its stack, then exits 1", async () => {
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
});

// A thrown value is not required to be an Error. Reading a hostile/buggy
// .stack getter runs arbitrary user code that can itself throw -- that must
// not crash describeFailure() (and lose the diagnostic) while it is already
// handling the original failure.
test("a hostile throwing .stack getter does not itself crash the handler", async () => {
  await withCrashLog(async (logPath) => {
    const result = spawnSync(process.execPath, [harness, "throw-hostile-getter", logPath], {
      encoding: "utf-8",
    });
    assert.equal(result.status, 1);
    const log = readLogSafe(logPath);
    assert.match(log, /uncaughtException: ready=false <failure detail unavailable: describing it threw>/);
    assert.match(log, /\bexit: code=1\b/);
  });
});

// describeFailure()'s OTHER branch: a thrown value with no .stack property
// at all falls through to String(value), not Error.stack. The
// hostile-.stack-getter test above never exercises this path (it always
// has a .stack property, just a throwing one).
test("a thrown non-Error value with no .stack is logged via String(value)", async () => {
  await withCrashLog(async (logPath) => {
    const result = spawnSync(process.execPath, [harness, "throw-non-error-string", logPath], {
      encoding: "utf-8",
    });
    assert.equal(result.status, 1);
    const log = readLogSafe(logPath);
    assert.match(log, /uncaughtException: ready=false boom-plain-string/);
    assert.match(log, /\bexit: code=1\b/);
  });
});

// A distinct hostile-value shape from the .stack-getter test: no .stack
// property at all (so describeFailure() falls through past that branch),
// but a Symbol.toPrimitive/toString that itself throws when String(value)
// tries to coerce it -- describeFailure()'s own outer try/catch must still
// produce the fixed fallback string, not crash the handler.
test("a hostile throwing Symbol.toPrimitive/toString does not itself crash the handler", async () => {
  await withCrashLog(async (logPath) => {
    const result = spawnSync(process.execPath, [harness, "throw-hostile-tostring", logPath], {
      encoding: "utf-8",
    });
    assert.equal(result.status, 1);
    const log = readLogSafe(logPath);
    assert.match(log, /uncaughtException: ready=false <failure detail unavailable: describing it threw>/);
    assert.match(log, /\bexit: code=1\b/);
  });
});

// markReady() is purely in-memory (see installEmergencyDiagnostics() in
// crash-diagnostics.mjs) -- it must never write a durable line on its own,
// but a failure captured *after* it was called must reflect ready=true, not
// the ready=false default asserted by the other cases above.
test("a failure after markReady() logs ready=true", async () => {
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
});

// createEmergencyLog()'s own truncation guard, exercised in-process
// (no subprocess needed -- this is pure file I/O, not a
// process.on(...)-installing call) rather than via the harness: SIGTERM is
// the CLI's own routine mechanism for /clear and foreground-session
// replacement, so this file can otherwise grow without bound purely from
// expected lifecycle churn, never a crash. Gated alongside the rest of this
// file's cases (see the header comment) since it deliberately writes a
// multi-megabyte fixture.
test(
  "an overgrown crash log is rotated (not destroyed) before the next entry",
  { skip: !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const oneMiB = 1_048_576;
      const oldContent = "x".repeat(oneMiB + 10);
      writeFileSync(logPath, oldContent);
      // Must pass isPrivateRegularFile()'s ownership/mode check the same
      // way the real crash-log file itself would (private, 0600) -- a
      // plain writeFileSync()'s mode is filtered through this process's
      // umask, which can otherwise leave permissive group/other bits that
      // make createEmergencyLog() treat this fixture as an untrusted
      // pre-existing file and refuse to write to it at all, rather than
      // rotating it (the behavior actually under test here).
      chmodSync(logPath, 0o600);
      const emergencyLog = createEmergencyLog(logPath);
      emergencyLog("signal", "SIGTERM ready=true");
      const content = readFileSync(logPath, "utf-8");
      assert.ok(
        content.length < oneMiB,
        `expected the live path's oversized content to be rotated away, got ${content.length} bytes`,
      );
      assert.match(content, /signal: SIGTERM ready=true/);
      // The rotated-away content must survive under a sidecar, not be
      // destroyed -- see the "never delete" rationale in crash-diagnostics.mjs.
      const dir = sidecarDirFor(logPath);
      const base = logPath.slice(dirname(logPath).length + 1);
      const sidecars = readdirSync(dir).filter((name) => name.startsWith(`${base}.stale-`));
      assert.equal(sidecars.length, 1, `expected exactly one sidecar, found: ${JSON.stringify(sidecars)}`);
      assert.equal(readFileSync(join(dir, sidecars[0]), "utf-8"), oldContent);
    });
  },
);

// purgeOldStaleSidecars() bounds the *cumulative* disk usage many rotations
// over a long-lived host would otherwise leave unbounded, without risking
// the destructive-delete race an unconditional cleanup would reintroduce:
// each sidecar's own filename embeds its rotation timestamp atomically
// (baked in by the same renameSync() call that creates it), so age-gating
// on that embedded timestamp is safe even across concurrent processes --
// see STALE_SIDECAR_NAME_PATTERN's own doc comment for why the filesystem
// mtime was tried first and had exactly the cross-process race this design
// avoids. This test creates a fake old sidecar with an old timestamp baked
// into its *name* (rather than waiting 24h, or relying on mtime at all)
// and confirms it is purged by the next rotation, while a fresh one from
// that same rotation survives.
test(
  "a rotation purges old sidecars (by embedded name timestamp) but keeps its own fresh one",
  { skip: !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const oneMiB = 1_048_576;
      writeFileSync(logPath, "x".repeat(oneMiB + 10));
      chmodSync(logPath, 0o600);
      // A pre-existing sidecar whose *name* claims a rotation timestamp
      // well past the 24h purge threshold -- simulates one left behind by
      // a rotation long ago. Its actual mtime (whatever writeFileSync()
      // gives it, i.e. right now) is deliberately left alone/untouched, to
      // prove the purge decision is made from the name, not the mtime.
      // Sidecars live in their own dedicated subdirectory (see
      // sidecarDirFor()); mkdirSync() it first since no real rotation has
      // happened yet in this test to create it.
      const dir = sidecarDirFor(logPath);
      mkdirSync(dir, { recursive: true });
      const base = logPath.slice(dirname(logPath).length + 1);
      const twoDaysAgoMs = Date.now() - 2 * 24 * 60 * 60 * 1000;
      const oldSidecarName = `${base}.stale-99999-${twoDaysAgoMs}-00000000-0000-0000-0000-000000000000`;
      writeFileSync(join(dir, oldSidecarName), "ancient rotated content\n");

      const emergencyLog = createEmergencyLog(logPath);
      emergencyLog("signal", "SIGTERM ready=true");

      const sidecars = readdirSync(dir).filter((name) => name.startsWith(`${base}.stale-`));
      assert.equal(
        sidecars.length,
        1,
        `expected the ancient sidecar purged and exactly one fresh one left, found: ${JSON.stringify(sidecars)}`,
      );
      assert.notEqual(sidecars[0], oldSidecarName, "the ancient sidecar should have been purged");
    });
  },
);

// Regression for the cross-process race the mtime-based design had: mtime
// is observable (and, from another process's perspective, indistinguishable
// from "old") the instant a rename completes, before this process could
// ever separately re-stamp it -- a concurrent process's own purge call
// could delete a sidecar this process just created, using its own,
// unrelated mtime-based judgment. Proves the current, name-based design is
// immune: this sidecar's *actual* mtime is deliberately made to look very
// recent (freshly written, same as any real rotation would produce) while
// its *name* claims an old rotation timestamp -- if the purge were still
// reading mtime at all, this sidecar would survive; since it reads the
// name instead, it must still be purged.
test(
  "purge decides by the embedded name timestamp even when the sidecar's actual mtime looks fresh",
  { skip: !RUN_STRESS_CASES },
  async () => {
    await withCrashLog(async (logPath) => {
      const dir = sidecarDirFor(logPath);
      mkdirSync(dir, { recursive: true });
      const base = logPath.slice(dirname(logPath).length + 1);
      const twoDaysAgoMs = Date.now() - 2 * 24 * 60 * 60 * 1000;
      const oldByNameSidecarName = `${base}.stale-12345-${twoDaysAgoMs}-11111111-1111-1111-1111-111111111111`;
      // writeFileSync() gives this file a real, current mtime -- freshly
      // written, same as any genuine rotation's sidecar would have.
      writeFileSync(join(dir, oldByNameSidecarName), "old-by-name, fresh-by-mtime\n");

      const oneMiB = 1_048_576;
      writeFileSync(logPath, "x".repeat(oneMiB + 10));
      chmodSync(logPath, 0o600);
      const emergencyLog = createEmergencyLog(logPath);
      emergencyLog("signal", "SIGTERM ready=true");

      const sidecars = readdirSync(dir).filter((name) => name.startsWith(`${base}.stale-`));
      assert.equal(
        sidecars.length,
        1,
        `expected the name-old sidecar purged despite its fresh mtime, found: ${JSON.stringify(sidecars)}`,
      );
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
      // handling (log-then-exit) is otherwise unaffected -- and, since the
      // log write failed, logOrFallback() must have printed a best-effort
      // stderr message instead of leaving this failure completely silent
      // (the exact symptom this module exists to diagnose).
      assert.equal(result.status, 1);
      const after = statSync(logPath);
      assert.equal(readFileSync(logPath, "utf-8"), "not ours\n");
      assert.equal(after.mode, before.mode);
      assert.match(result.stderr, /emergency diagnostics \(log write failed\): uncaughtException/);
      // The 'exit' event still fires after uncaughtException's own
      // process.exit(1) call, and its own emergencyLog("exit", ...) would
      // also fail here (the file write is refused for this whole process,
      // not just once) -- alreadyFellBack must suppress a second, redundant
      // fallback line for the same underlying failure.
      const fallbackLines = result.stderr
        .split("\n")
        .filter((line) => line.includes("emergency diagnostics (log write failed)"));
      assert.equal(fallbackLines.length, 1, `expected exactly one fallback line, got: ${JSON.stringify(fallbackLines)}`);
    });
  },
);

// A plain non-zero process.exit(1) with no preceding exception/rejection/
// signal is a real path onExit() must still cover: nothing else logs or
// falls back for this process before onExit() itself runs, so if the log
// write also fails here, the failure would otherwise be completely silent
// (neither a file entry nor stderr) -- the exact symptom this module exists
// to diagnose. Reuses the same untrusted-pre-existing-file setup as the
// case above to force the log write to fail.
test(
  "a non-zero exit with no preceding failure still falls back to stderr when the log write fails",
  { skip: process.platform === "win32" },
  async () => {
    await withCrashLog(async (logPath) => {
      writeFileSync(logPath, "not ours\n", { mode: 0o644 });
      chmodSync(logPath, 0o644);
      const result = spawnSync(process.execPath, [harness, "exit-1-directly", logPath], {
        encoding: "utf-8",
      });
      assert.equal(result.status, 1);
      assert.equal(readFileSync(logPath, "utf-8"), "not ours\n");
      assert.match(result.stderr, /emergency diagnostics \(log write failed\): exit: code=1/);
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

test("an unhandled rejection is logged with its stack, then exits 1", async () => {
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
        // Sent only after the listeners above are already attached: sending
        // the signal first and registering the 'exit' listener afterward
        // would risk missing an 'exit' that fires in between, intermittently
        // waiting out the full 5s timeout above and failing even when the
        // signal re-raise itself is completely correct.
        child.kill("SIGTERM");
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

// Every case above exercises installEmergencyDiagnostics()'s markReady()
// entirely through this test file's own harness -- none of them proves the
// real production extension.mjs actually calls it, in the right place,
// after the real joinSession() resolves. If that wiring were ever moved or
// deleted, every crash captured after real readiness would be silently
// mis-tagged ready=false while every test above kept passing. A full
// integration test would need a real joinSession()/SDK harness this plugin
// does not have; this source-contract check (reading extension.mjs as text,
// the same technique tests/guidance.test.mjs already uses for other
// production wiring in this same file) is the pragmatic alternative: it
// fails if the markReady() call is ever removed, or reordered to sit before
// the joinSession() call instead of after it. Always runs (no subprocess).
test("production extension.mjs calls markReady() only after joinSession() resolves", () => {
  const extensionPath = join(here, "..", "extensions", "context-handoff", "extension.mjs");
  const extension = readFileSync(extensionPath, "utf-8");
  const installIndex = extension.indexOf(
    "const emergencyDiagnostics = installEmergencyDiagnostics(emergencyLog);",
  );
  assert.notEqual(
    installIndex,
    -1,
    "extension.mjs must capture installEmergencyDiagnostics()'s return value",
  );
  const callStart = extension.indexOf("const session = await joinSession(");
  assert.notEqual(callStart, -1, "expected to find the joinSession() call");
  // Diagnostics must be installed *before* joinSession() is even called,
  // not merely present somewhere in the file: joinSession() itself can
  // fail or throw, and the whole point of installing these handlers first
  // is to capture exactly that class of pre-readiness failure (the
  // ready=false cases this instrumentation exists for). Installing it
  // after the awaited call would still satisfy every other check in this
  // test while leaving a failure during the join itself uninstrumented.
  assert.ok(
    installIndex < callStart,
    "installEmergencyDiagnostics() must be called before joinSession(), not after it",
  );
  // Merely checking markReady() appears somewhere after the *opening* text
  // of the joinSession() call (or that some "});" anywhere in the file
  // precedes it) is not enough: a regression that moved the call to inside
  // the (very large) options object literal -- before the awaited call
  // actually resolves -- would still satisfy that ordering, and an
  // unrelated "});" from some other block could produce a false-positive
  // adjacency match, both mis-tagging pre-readiness failures as
  // ready=true while this test kept reporting success. So the joinSession()
  // call's *own* matching closing paren is located with a small
  // string/comment-aware scanner that counts parenthesis depth from its
  // opening "(" but skips over any "("/")" found inside string/template
  // literals or comments (this file's huge options-object argument is full
  // of prompt/description prose that can contain unbalanced parens), and
  // markReady() must be the very next statement after the line containing
  // that specific, correctly matched closing paren.
  const closeParenIndex = findMatchingCloseParen(extension, extension.indexOf("(", callStart));
  assert.notEqual(closeParenIndex, -1, "expected to find joinSession()'s own matching closing paren");
  const closeLineIndex = extension.slice(0, closeParenIndex).split("\n").length - 1;
  const lines = extension.split("\n");
  assert.equal(
    lines[closeLineIndex].trim(),
    "});",
    `expected joinSession()'s own closing line to be "});", got: ${JSON.stringify(lines[closeLineIndex])}`,
  );
  // markReady() must be the very next non-blank statement after that
  // specific closing line -- not merely somewhere later in the file.
  let nextLineIndex = closeLineIndex + 1;
  while (nextLineIndex < lines.length && lines[nextLineIndex].trim() === "") {
    nextLineIndex += 1;
  }
  assert.equal(
    lines[nextLineIndex]?.trim(),
    "emergencyDiagnostics.markReady();",
    `expected markReady() to immediately follow joinSession()'s own closing "});", but the next line was: ${JSON.stringify(lines[nextLineIndex])}`,
  );
});
