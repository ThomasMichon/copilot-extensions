import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync, chmodSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  METADATA_REFRESH_CLI_TIMEOUT_MS, metadataRefresher, readSessionBinding, registrationMetadata,
  resolveMetadataAsync, runCliAsync,
} from "../extensions/agent-bridge/metadata.mjs";

test("selected receiving metadata keeps its provider scope without inventing guest ownership", async () => {
  const { metadataRefresher } = await import("../extensions/agent-bridge/metadata.mjs");
  const meta = await resolveMetadataAsync({
    cwd: process.cwd(), env: {
      AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT: fileURLToPath(new URL("../", import.meta.url)),
      AGENT_BRIDGE_SCOPE_ID: "provider-workspace@selected-venue",
      AGENT_BRIDGE_NATIVE_SESSION_ID: "11111111-1111-4111-8111-111111111111",
    },
  });
  const wire = registrationMetadata(meta);
  assert.equal(wire.worktree_id, "provider-workspace@selected-venue");
  assert.equal(wire.machine, null);
  assert.equal(wire.repo, null);
  assert.equal(wire.clear_targeting_metadata, undefined);
  let queried = false;
  const refresh = metadataRefresher({ meta }, async () => { queried = true; });
  assert.equal(await refresh(), false);
  assert.equal(queried, false);
});

test("receiving metadata refuses another plugin root rather than querying guest Worktrees", async () => {
  await assert.rejects(resolveMetadataAsync({
    env: {
      AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT: fileURLToPath(new URL("../../agent-remote-driver/", import.meta.url)),
      AGENT_BRIDGE_SCOPE_ID: "scope",
      AGENT_BRIDGE_NATIVE_SESSION_ID: "11111111-1111-4111-8111-111111111111",
    },
  }), /differs from its selected launch/);
});

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
// slowest call. A real-process barrier proves overlap without a wall-time
// threshold that also measures host load and shell startup.
test("resolveMetadataAsync runs its four spawns concurrently, not sequentially", async (t) => {
  const { dir } = makeFakeBinDir();
  const gate = join(dir, "gate.cjs");
  writeFileSync(gate, `
const { writeFileSync, readdirSync } = require("node:fs");
const { join } = require("node:path");
const dir = ${JSON.stringify(dir)};
writeFileSync(join(dir, process.pid + ".started"), "");
const deadline = Date.now() + 5000;
function poll() {
  if (readdirSync(dir).filter(name => name.endsWith(".started")).length === 4) {
    console.log(process.argv[2]);
  } else if (Date.now() >= deadline) {
    process.exitCode = 1;
  } else {
    setTimeout(poll, 10);
  }
}
poll();
`);
  for (const [name, value] of [["git", "main"], ["agent-worktrees", "some-worktree"]]) {
    const isWin = process.platform === "win32";
    const script = join(dir, isWin ? `${name}.cmd` : name);
    writeFileSync(script, isWin
      ? `@echo off\r\n"${process.execPath}" "${gate}" "${value}"\r\nexit /b %errorlevel%\r\n`
      : `#!/bin/sh\nexec "${process.execPath}" "${gate}" "${value}"\n`);
    if (!isWin) chmodSync(script, 0o755);
  }
  const originalPath = process.env.PATH;
  process.env.PATH = `${dir}${process.platform === "win32" ? ";" : ":"}${originalPath}`;
  t.after(() => { process.env.PATH = originalPath; });

  const meta = await resolveMetadataAsync({ cwd: dir, env: {} });
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
  write("agent-worktrees", 0, "anchor-example-web");

  const originalPath = process.env.PATH;
  process.env.PATH = `${dir}${process.platform === "win32" ? ";" : ":"}${originalPath}`;
  t.after(() => { process.env.PATH = originalPath; });

  const meta = await resolveMetadataAsync({ cwd: dir, env: {} });

  assert.equal(meta.worktree_id, "anchor-example-web");
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

// The process start time is what lets the bridge tell this process apart from
// an unrelated one that later reuses its pid, so it must never change.
test("resolveMetadataAsync reports a fixed process start time", async (t) => {
  const { dir, write } = makeFakeBinDir();
  write("git", 0, "main");
  write("agent-worktrees", 0, "anchor-example-web");

  const originalPath = process.env.PATH;
  process.env.PATH = `${dir}${process.platform === "win32" ? ";" : ":"}${originalPath}`;
  t.after(() => { process.env.PATH = originalPath; });

  const first = await resolveMetadataAsync({ cwd: dir, env: {} });
  const second = await resolveMetadataAsync({ cwd: dir, env: {} });

  assert.equal(typeof first.process_started_at, "number");
  assert.equal(first.process_started_at, second.process_started_at);
  const expected = Date.now() / 1000 - process.uptime();
  assert.ok(Math.abs(first.process_started_at - expected) < 2);
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

test("resolveMetadataAsync does not invent a logical machine from a physical hostname", async (t) => {
  const { dir, write } = makeFakeBinDir();
  write("git", 0, "main");
  const isWin = process.platform === "win32";
  const failing = join(dir, isWin ? "agent-worktrees.cmd" : "agent-worktrees");
  writeFileSync(failing, isWin ? "@echo off\r\nexit /b 1\r\n" : "#!/bin/sh\nexit 1\n");
  if (!isWin) chmodSync(failing, 0o755);

  const originalPath = process.env.PATH;
  process.env.PATH = `${dir}${isWin ? ";" : ":"}${originalPath}`;
  t.after(() => { process.env.PATH = originalPath; });

  const meta = await resolveMetadataAsync({ cwd: dir, env: {} });

  assert.equal(meta.machine, null);
});

// An extension reload in the same process is a new module instance: it must
// still report the bit-identical start time, so the bridge can compare start
// times exactly (no drift window a reused pid could slip through).
test("a reloaded metadata module reports the same process start time exactly", async () => {
  const first = (await import("../extensions/agent-bridge/metadata.mjs")).processIdentity();
  const reloaded = await import(`../extensions/agent-bridge/metadata.mjs?reload=${Date.now()}`);
  assert.equal(reloaded.processIdentity().process_started_at, first.process_started_at);
  assert.equal(reloaded.processIdentity().pid, process.pid);
});


test("metadataRefresher fills in a worktree the load-time lookup missed, with backoff", async () => {
  const { metadataRefresher, METADATA_RETRY_MIN_MS } = await import("../extensions/agent-bridge/metadata.mjs");
  const state = { meta: { machine: "m1", cwd: "/w", worktree_id: null, repo: null, branch: "b" } };
  let t = 0;
  const answers = [null, { worktree_id: null, repo: null }, { worktree_id: "wt-1", repo: "proj", machine: "other" }];
  let calls = 0;
  const refresh = metadataRefresher(state, async () => { calls += 1; return answers.shift(); }, () => t);
  assert.equal(await refresh(), false); // the lookup failed again: back off
  assert.equal(await refresh(), false); // within the backoff: not asked
  assert.equal(calls, 1);
  t += METADATA_RETRY_MIN_MS;
  assert.equal(await refresh(), false); // asked, still missing: back off longer
  t += METADATA_RETRY_MIN_MS;
  assert.equal(await refresh(), false); // the doubled backoff hasn't passed
  assert.equal(calls, 2);
  t += METADATA_RETRY_MIN_MS;
  assert.equal(await refresh(), true);
  assert.deepEqual(state.meta, { machine: "m1", cwd: "/w", worktree_id: "wt-1", repo: "proj", branch: "b" });
  assert.equal(await refresh(), false); // nothing missing any more: never asked again
  assert.equal(calls, 3);
});

test("metadataRefresher never replaces a known identity", async () => {
  const { metadataRefresher } = await import("../extensions/agent-bridge/metadata.mjs");
  const state = { meta: { machine: "m1", worktree_id: "pinned@cs", repo: "proj" } };
  let calls = 0;
  const refresh = metadataRefresher(state, async () => { calls += 1; return { worktree_id: "other" }; }, () => 0);
  assert.equal(await refresh(), false);
  assert.equal(calls, 0);
  assert.equal(state.meta.worktree_id, "pinned@cs");
});

test("metadataRefresher recovers the logical machine even when worktree and project are known", async () => {
  const { metadataRefresher } = await import("../extensions/agent-bridge/metadata.mjs");
  const state = { meta: { machine: null, worktree_id: "wt-1", repo: "proj" } };
  let calls = 0;
  const refresh = metadataRefresher(state, async () => {
    calls += 1;
    return { machine: "host-wsl", worktree_id: "other", repo: "other" };
  }, () => 0);
  assert.equal(await refresh(), true);
  assert.deepEqual(state.meta, { machine: "host-wsl", worktree_id: "wt-1", repo: "proj" });
  assert.equal(await refresh(), false);
  assert.equal(calls, 1);
});

test("an unresolved machine cannot register an unqualified worktree lineage", () => {
  const meta = { machine: null, worktree_id: "shared-name", repo: "proj", cwd: "/fixture" };
  assert.deepEqual(registrationMetadata(meta), {
    machine: null, worktree_id: null, repo: null, cwd: "/fixture",
    clear_targeting_metadata: true,
  });
  assert.equal(meta.worktree_id, "shared-name");
  assert.deepEqual(registrationMetadata(null), {
    machine: null, worktree_id: null, repo: null, clear_targeting_metadata: true,
  });
  const resolved = { ...meta, machine: "host-wsl" };
  assert.equal(registrationMetadata(resolved), resolved);
});

test("incomplete project or worktree metadata also clears the old lineage", () => {
  for (const key of ["repo", "worktree_id"]) {
    const meta = { machine: "host-wsl", repo: "proj", worktree_id: "wt", [key]: null };
    assert.deepEqual(registrationMetadata(meta), {
      machine: null, repo: null, worktree_id: null, clear_targeting_metadata: true,
    });
  }
});

// --- sessionStart worktree binding (the identity agent-worktrees' hook
// already stamped into this session's state folder) ---

// A disposable Copilot home (COPILOT_HOME) and user home, so the binding
// lookup never touches the real profile.
function makeBindingHomes(t) {
  const root = mkdtempSync(join(tmpdir(), "agent-bridge-binding-test-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const copilotHome = join(root, "copilot-home");
  const userHome = join(root, "user-home");
  const env = { COPILOT_HOME: copilotHome, HOME: userHome, USERPROFILE: userHome };
  const write = (sessionId, content, { underUserHome = false } = {}) => {
    const dir = underUserHome
      ? join(userHome, ".copilot", "session-state", sessionId)
      : join(copilotHome, "session-state", sessionId);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "worktree-binding.json"), typeof content === "string" ? content : JSON.stringify(content));
  };
  return { env, write };
}

const bindingFor = (sessionId, overrides = {}) => ({
  sessionId, worktreeId: "wt-bound", worktreeDir: "/fixture/wt-bound", machine: "host-bound",
  registeredAt: "2026-01-01T00:00:00", project: "proj-bound", creationNonce: "abc123", ...overrides,
});

// `git` answers at once; `agent-worktrees` records each call and then hangs
// far past the lookup timeout, like a CLI starved on a contended host.
function useHangingAgentWorktrees(t) {
  const dir = mkdtempSync(join(tmpdir(), "agent-bridge-metadata-hang-"));
  const marker = join(dir, "agent-worktrees.calls");
  const isWin = process.platform === "win32";
  const script = (name, body) => {
    const path = join(dir, isWin ? `${name}.cmd` : name);
    writeFileSync(path, isWin ? `@echo off\r\n${body.join("\r\n")}\r\n` : `#!/bin/sh\n${body.join("\n")}\n`);
    if (!isWin) chmodSync(path, 0o755);
  };
  script("git", ["echo main"]);
  script("agent-worktrees", isWin
    ? [`echo %*>>"${marker}"`, `"${process.execPath}" -e "setTimeout(() => {}, 10000)"`, "echo late"]
    : [`echo "$*" >> "${marker}"`, "exec sleep 10"]);
  const originalPath = process.env.PATH;
  process.env.PATH = `${dir}${isWin ? ";" : ":"}${originalPath}`;
  t.after(() => { process.env.PATH = originalPath; });
  return { cwd: dir, called: () => existsSync(marker) };
}

test("a timed-out CLI lookup no longer leaves the session untargetable when its binding exists", async (t) => {
  const { env, write } = makeBindingHomes(t);
  const cli = useHangingAgentWorktrees(t);
  const sessionId = randomUUID();

  // Without a binding, every `agent-worktrees get` times out: no identity.
  const before = await resolveMetadataAsync({ cwd: cli.cwd, env, sessionId, timeoutMs: 1_000 });
  assert.equal(cli.called(), true);
  assert.deepEqual([before.machine, before.worktree_id, before.repo], [null, null, null]);
  assert.equal(registrationMetadata(before).clear_targeting_metadata, true);

  write(sessionId, bindingFor(sessionId));
  const after = await resolveMetadataAsync({ cwd: cli.cwd, env, sessionId, timeoutMs: 1_000 });
  assert.equal(after.worktree_id, "wt-bound");
  assert.equal(after.machine, "host-bound");
  assert.equal(after.repo, "proj-bound");
  assert.equal(after.branch, "main");
  const wire = registrationMetadata(after);
  assert.equal(wire.clear_targeting_metadata, undefined);
  assert.equal(wire.worktree_id, "wt-bound");
});

test("a fully bound session spawns no agent-worktrees lookup at all", async (t) => {
  const { env, write } = makeBindingHomes(t);
  const cli = useHangingAgentWorktrees(t);
  const sessionId = randomUUID();
  write(sessionId, bindingFor(sessionId), { underUserHome: true });

  const meta = await resolveMetadataAsync({ cwd: cli.cwd, env, sessionId });

  assert.deepEqual([meta.machine, meta.worktree_id, meta.repo], ["host-bound", "wt-bound", "proj-bound"]);
  assert.equal(cli.called(), false);
});

test("a binding that lands after registration is picked up by the refresher within its backoff", async (t) => {
  const { env, write } = makeBindingHomes(t);
  const lateId = randomUUID();
  const state = {
    sessionId: randomUUID(),
    meta: { machine: null, cwd: "/w", worktree_id: null, repo: null, branch: "b" },
  };
  const lookups = [];
  const refresh = metadataRefresher(state, async (options) => {
    lookups.push(options);
    return { machine: null, worktree_id: null, repo: null }; // the CLI keeps timing out
  }, () => 0, { env });

  assert.equal(await refresh(), false); // no binding yet, and the CLI found nothing: back off
  assert.equal(lookups.length, 1);
  assert.equal(lookups[0].timeoutMs, METADATA_REFRESH_CLI_TIMEOUT_MS);
  assert.equal(lookups[0].sessionId, state.sessionId);
  assert.equal(registrationMetadata(state.meta).clear_targeting_metadata, true);

  // The hook writes the binding for the id the session is resumed under; it
  // only counts once the extension follows that id.
  write(lateId, bindingFor(lateId), { underUserHome: true });
  assert.equal(await refresh(), false);
  state.sessionId = lateId;
  assert.equal(await refresh(), true);

  assert.equal(lookups.length, 1); // filled from the file, inside the CLI backoff
  assert.deepEqual(state.meta, {
    machine: "host-bound", cwd: "/w", worktree_id: "wt-bound", repo: "proj-bound", branch: "b",
  });
  assert.equal(registrationMetadata(state.meta), state.meta);
  assert.equal(await refresh(), false);
});

test("a binding for another session, or a malformed one, is ignored", async (t) => {
  const { env, write } = makeBindingHomes(t);
  const sessionId = randomUUID();

  write(sessionId, bindingFor(randomUUID()));
  assert.equal(await readSessionBinding(sessionId, { env }), null);

  for (const malformed of ["{not json", "[]", "null", `"${sessionId}"`, " ".repeat(17 * 1024)]) {
    write(sessionId, malformed);
    assert.equal(await readSessionBinding(sessionId, { env }), null);
  }

  // Unusable fields are dropped one by one; an anchor's identity stays the CLI's.
  write(sessionId, bindingFor(sessionId, { worktreeId: "@anchor", project: "../escape" }));
  assert.deepEqual(await readSessionBinding(sessionId, { env }),
    { worktree_id: null, machine: "host-bound", repo: null });

  assert.equal(await readSessionBinding("../outside", { env }), null);
  assert.equal(await readSessionBinding(null, { env }), null);

  const state = { sessionId, meta: { machine: null, worktree_id: null, repo: null } };
  write(sessionId, bindingFor("someone-else"));
  const refresh = metadataRefresher(state, async () => null, () => 0, { env });
  assert.equal(await refresh(), false);
  assert.deepEqual(state.meta, { machine: null, worktree_id: null, repo: null });
});

test("AGENT_BRIDGE_SCOPE_ID still outranks the binding's worktree", async (t) => {
  const { env: homes, write } = makeBindingHomes(t);
  const cli = useHangingAgentWorktrees(t);
  const env = { ...homes, AGENT_BRIDGE_SCOPE_ID: "anchor-example-web@cs-1" };
  const sessionId = randomUUID();
  write(sessionId, bindingFor(sessionId));

  const meta = await resolveMetadataAsync({ cwd: cli.cwd, env, sessionId });
  assert.equal(meta.worktree_id, "anchor-example-web@cs-1");
  assert.equal(meta.machine, "host-bound");
  assert.equal(meta.repo, "proj-bound");
  assert.equal(cli.called(), false);

  const state = { sessionId, meta: { machine: null, worktree_id: null, repo: null } };
  const refresh = metadataRefresher(state, async () => null, () => 0, { env });
  assert.equal(await refresh(), true);
  assert.equal(state.meta.worktree_id, "anchor-example-web@cs-1");
});

test("an identity still incomplete after a few lookups is logged once", async () => {
  const state = { sessionId: null, meta: { machine: "m1", worktree_id: null, repo: null } };
  let clock = 0;
  const logs = [];
  const refresh = metadataRefresher(state, async () => null, () => clock, {
    env: {}, log: (line) => logs.push(line), warnAfter: 3,
  });
  for (let attempt = 0; attempt < 6; attempt += 1) {
    assert.equal(await refresh(), false);
    clock += 60 * 60_000; // past any backoff
  }
  assert.equal(logs.length, 1);
  assert.match(logs[0], /after 3 lookups \(missing: worktree_id, repo\)/);
});
