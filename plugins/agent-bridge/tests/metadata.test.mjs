import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { mkdtempSync, writeFileSync, chmodSync } from "node:fs";
import { join } from "node:path";

import { resolveMetadataAsync, runCliAsync } from "../extensions/agent-bridge/metadata.mjs";

// A disposable directory with a fake "agent-worktrees" and "git" binstub so
// these tests exercise real child-process spawning (the actual regression
// class -- a mock of execFile would never have caught the sequential-blocking
// bug) without depending on either tool actually being installed.
function makeFakeBinDir() {
  const dir = mkdtempSync(join(tmpdir(), "agent-bridge-metadata-test-"));
  const isWin = process.platform === "win32";
  const write = (name, sleepMs, output) => {
    const scriptName = isWin ? `${name}.cmd` : name;
    const scriptPath = join(dir, scriptName);
    const body = isWin
      ? `@echo off\r\npowershell -NoProfile -Command "Start-Sleep -Milliseconds ${sleepMs}" >nul\r\necho ${output}\r\n`
      : `#!/bin/sh\nsleep ${sleepMs / 1000}\necho "${output}"\n`;
    writeFileSync(scriptPath, body);
    if (!isWin) chmodSync(scriptPath, 0o755);
  };
  return { dir, write };
}

test("runCliAsync resolves null (not a throw) when the binary does not exist", async () => {
  const result = await runCliAsync("definitely-not-a-real-binary-xyz", ["get", "machine"], process.cwd());
  assert.equal(result, null);
});

test("runCliAsync resolves trimmed stdout on success", async () => {
  const { dir, write } = makeFakeBinDir();
  write("probe", 0, "hello-world");
  const bin = process.platform === "win32" ? join(dir, "probe.cmd") : join(dir, "probe");
  const result = await runCliAsync(bin, [], dir);
  assert.equal(result, "hello-world");
});

// The actual regression this whole file exists to prevent: resolveMetadata's
// four external spawns (git + three `agent-worktrees get` calls) used to run
// SEQUENTIALLY via execSync/execFileSync -- total wall time was close to the
// SUM of all four durations, up to their combined ~29s timeout budget, which
// was confirmed live as the cause of a ready-timeout crash-loop under session
// contention. They must run concurrently instead, bounded by the single
// slowest call. This proves it with real (slow, disposable) child processes,
// not a timer mock -- the previous shape would fail this test by taking
// ~4x as long.
test("resolveMetadataAsync runs its four spawns concurrently, not sequentially", async (t) => {
  const { dir, write } = makeFakeBinDir();
  // Deliberately generous: on Windows each stub spawns a real powershell.exe
  // to sleep, which itself has a fixed ~250-400ms startup cost on top of the
  // sleep duration -- that fixed cost applies once if concurrent, ~4x if
  // sequential. SLEEP_MS is large enough, and the pass threshold wide enough,
  // that this stays a clean, non-flaky discriminator between the two shapes
  // even under machine load: concurrent lands around one spawn's total cost
  // (~800-1000ms here); sequential would land around 4x that (~3200ms+).
  const SLEEP_MS = 700;
  write("git", SLEEP_MS, "main");
  write("agent-worktrees", SLEEP_MS, "some-worktree");

  // resolveMetadataAsync hardcodes the binary names "git"/"agent-worktrees";
  // point PATH at our fake bin dir so those resolve to the slow stubs above,
  // without needing either real tool installed.
  const originalPath = process.env.PATH;
  process.env.PATH = `${dir}${process.platform === "win32" ? ";" : ":"}${originalPath}`;
  t.after(() => { process.env.PATH = originalPath; });

  const start = Date.now();
  const meta = await resolveMetadataAsync({ cwd: dir, env: {} });
  const elapsedMs = Date.now() - start;

  // Concurrent: ~1 spawn's total cost. Sequential (the regression this test
  // guards against): ~4 spawns' total cost. SLEEP_MS * 2 sits comfortably
  // between the two on this machine's measured per-spawn overhead, with
  // sequential landing at roughly SLEEP_MS * 4+ -- not a knife's-edge margin.
  assert.ok(
    elapsedMs < SLEEP_MS * 2,
    `expected concurrent execution (~${SLEEP_MS}ms + one spawn's overhead), took ${elapsedMs}ms -- looks sequential`,
  );
  assert.equal(meta.branch, "main");
  assert.equal(meta.worktree_id, "some-worktree");
  assert.equal(meta.machine, "some-worktree"); // stub returns the same fixed string for every `get` call
  assert.equal(typeof meta.pid, "number");
});

// agent-bridge-cli-mode-sessions Phase 4 follow-up: an anchor-mode CLI
// session (`agent-worktrees embody/copilot --anchor`) has no worktree-dir at
// all, so `session-scope-id` -- not a basename-of-worktree-dir derivation --
// is what supplies `worktree_id` for self-registration. Confirms the
// resolved identity is passed through verbatim, with no path manipulation
// applied on this side (that responsibility now lives entirely in
// `agent-worktrees get session-scope-id` itself).
test("resolveMetadataAsync passes an anchor session-scope-id through verbatim", async (t) => {
  const { dir, write } = makeFakeBinDir();
  write("git", 0, "main");
  write("agent-worktrees", 0, "anchor-odsp-web");

  const originalPath = process.env.PATH;
  process.env.PATH = `${dir}${process.platform === "win32" ? ";" : ":"}${originalPath}`;
  t.after(() => { process.env.PATH = originalPath; });

  const meta = await resolveMetadataAsync({ cwd: dir, env: {} });

  assert.equal(meta.worktree_id, "anchor-odsp-web");
});

// Venue CLI-mode detached launch: several CodeSpaces of the same repo all
// report `anchor-<repo>` from session-scope-id, so the launcher pins a
// venue-qualified identity via AGENT_BRIDGE_SCOPE_ID; it must win.
test("resolveMetadataAsync prefers an explicit AGENT_BRIDGE_SCOPE_ID", async (t) => {
  const { dir, write } = makeFakeBinDir();
  write("git", 0, "main");
  write("agent-worktrees", 0, "anchor-example-web");

  const originalPath = process.env.PATH;
  process.env.PATH = `${dir}${process.platform === "win32" ? ";" : ":"}${originalPath}`;
  t.after(() => { process.env.PATH = originalPath; });

  const meta = await resolveMetadataAsync({
    cwd: dir, env: { AGENT_BRIDGE_SCOPE_ID: "anchor-example-web@cs-1" },
  });

  assert.equal(meta.worktree_id, "anchor-example-web@cs-1");
});

// Sanity check that the fake-binary harness itself is exercising a real
// subprocess (not silently no-op-ing), so the timing assertion above is
// actually meaningful.
test("fake bin harness sanity: the stub script really does sleep", () => {
  const { dir, write } = makeFakeBinDir();
  write("probe", 0, "ok");
  const bin = process.platform === "win32" ? join(dir, "probe.cmd") : join(dir, "probe");
  const out = execFileSync(bin, [], { cwd: dir, encoding: "utf-8", shell: process.platform === "win32" }).trim();
  assert.equal(out, "ok");
});

