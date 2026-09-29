import { test } from "node:test";
import assert from "node:assert/strict";
import { join, dirname } from "node:path";
import {
  mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, unlinkSync,
  existsSync, utimesSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { execFileSync, spawnSync } from "node:child_process";

import {
  HANDOFF_META_PREFIX,
  addHandoffClaim,
  agentWorktreesGetResult,
  attemptWorktreeSync,
  buildResumePrompt,
  buildSeedForStored,
  checkHeadAlignment,
  consumeDispatchHandoffTask,
  consumeFileHandoff,
  decodeHandoffPayload,
  encodeHandoffPayload,
  formatConsumeResult,
  handoffDedupKey,
  isolatedPythonArgs,
  logHandoffPromptReceived,
  manualFallbackInstructions,
  normalizeHandoffTitle,
  plainGitSync,
  redactGitDiagnostics,
  processStartTimeMs,
  releaseHandoffClaimIfTerminal,
  resolveRuntimePython,
  retryStoredHandoffCutover,
  resolveSystemCli,
  runtimeEnvironment,
  safePathSegment,
  sanitizedGitEnv,
  sessionBindingForSession,
  triggerHandoff,
  recoverStoredHandoff,
  writeJsonAtomic,
  writeSessionStateHandoff,
  readSessionStateHandoff,
  markSessionStateHandoffConsumed,
  dispatchTaskConsumed,
  pickupSignals,
  noteHandoffInRecord,
  promoteSuccessorHead,
  listWorktreeSessions,
  getPreviousSession,
  abortHandoffTask,
  abortFileHandoff,
} from "../extensions/context-handoff/handoff-core.mjs";
import { extractRecoveryLocatorFromPrompt } from "../extensions/context-handoff/cutover-seed.mjs";

function withTempHome(fn) {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-home-"));
  const oldHome = process.env.HOME;
  const oldUserProfile = process.env.USERPROFILE;
  process.env.HOME = dir;
  process.env.USERPROFILE = dir;
  try {
    fn(dir);
  } finally {
    if (oldHome === undefined) delete process.env.HOME;
    else process.env.HOME = oldHome;
    if (oldUserProfile === undefined) delete process.env.USERPROFILE;
    else process.env.USERPROFILE = oldUserProfile;
    rmSync(dir, { recursive: true, force: true });
  }
}

function makeLocatorLookupSeams({
  anchorPath = null,
  worktrees = [],
  stateDirs = {},
  repoPaths = null,
}) {
  return {
    get: (key, cwd) => {
      if (key !== "worktree-state-dir") return null;
      return stateDirs[cwd] || null;
    },
    execute: (_bin, argv, opts = {}) => {
      if (
        argv[0] === "get"
        && argv[1] === "worktree-state-dir"
      ) {
        return stateDirs[opts.cwd] || "";
      }
      if (
        argv[0] === "repos"
        && argv[1] === "list"
        && argv.includes("--class")
        && argv.includes("worktree")
        && argv.includes("--json")
      ) {
        return JSON.stringify({
          repos: [{
            name: "wt-repo",
            paths: repoPaths || (anchorPath ? { windows: anchorPath } : {}),
          }],
        });
      }
      if (
        argv[0] === "list"
        && argv.includes("--all")
        && argv.includes("--json")
        && opts.cwd === anchorPath
      ) {
        return JSON.stringify({ worktrees });
      }
      throw new Error(`unexpected command: ${argv.join(" ")} @ ${opts.cwd || ""}`);
    },
  };
}

test("encode/decode round-trips metadata + text", () => {
  const meta = { kind: "context-handoff", id: "handoff-sid1", title: "Fix X" };
  const body = "## Session Continuation\nline two\nline three";
  const encoded = encodeHandoffPayload(body, meta);
  assert.ok(encoded.startsWith(HANDOFF_META_PREFIX));
  const { metadata, text } = decodeHandoffPayload(encoded);
  assert.deepEqual(metadata, meta);
  assert.equal(text, body);
});

test("buildSeedForStored keeps the bounded three-part locator", () => {
  const stored = {
    storage: "agent-dispatch",
    id: "task-42",
    metadata: { title: "Ship the thing" },
  };
  const seed = buildSeedForStored(stored);
  assert.match(seed, /^Task: Ship the thing \| Resume: \/consume-handoff to take over \| Recovery: context-handoff task:task-42$/);
  assert.equal(seed.split(" | ").length, 3);
  assert.ok(seed.length <= 200);
});

test("manual fallback instructions stay manual and seed-focused", () => {
  const seed =
    "Task: Continue | Resume: /consume-handoff to take over | " +
    "Recovery: context-handoff file:handoff-1";
  const text = manualFallbackInstructions(
    { storage: "file", id: "handoff-1" },
    seed,
  );
  assert.match(text, /No control system acknowledged the request/);
  assert.match(text, /run `\/consume-handoff`/);
  assert.match(text, /consume --locator/);
  assert.match(text, /VERBATIM/);
  assert.match(text, new RegExp(seed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("manual fallback instructions distinguish an in-flight spawn from silence", () => {
  const seed =
    "Task: Continue | Resume: /consume-handoff to take over | " +
    "Recovery: context-handoff file:handoff-1";
  const text = manualFallbackInstructions(
    { storage: "file", id: "handoff-1" },
    seed,
    { spawnInFlight: true },
  );
  assert.match(text, /already been.*spawned and is starting up/s);
  assert.match(text, /expected in-progress state, not a failure/);
  assert.doesNotMatch(text, /No control system acknowledged the request/);
  assert.match(text, new RegExp(seed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("automaticCutoverDisabled takes priority over a stale spawnInFlight marker", () => {
  // Copilot review finding on PR #3041: a disabled-mode caller never
  // requested a spawn, but the pickup probe can still observe a stale or
  // unrelated spawnInFlight marker -- claiming "the automatic cutover
  // should complete" in that case would contradict the disabled-mode
  // truth. automaticCutoverDisabled must win regardless of spawnInFlight.
  const seed =
    "Task: Continue | Resume: /consume-handoff to take over | " +
    "Recovery: context-handoff file:handoff-1";
  const text = manualFallbackInstructions(
    { storage: "file", id: "handoff-1" },
    seed,
    { spawnInFlight: true, automaticCutoverDisabled: true },
  );
  assert.match(text, /Automatic cutover is disabled/);
  assert.doesNotMatch(text, /already been.*spawned and is starting up/s);
  assert.doesNotMatch(text, /automatic cutover should complete/);
});

test("runtime invocation isolates imports and forces UTF-8", () => {
  assert.deepEqual(
    isolatedPythonArgs("agent_worktrees", ["get", "worktree-dir"]),
    [
      "-I", "-X", "utf8", "-m", "agent_worktrees",
      "get", "worktree-dir",
    ],
  );
  assert.deepEqual(
    runtimeEnvironment({
      KEEP: "yes",
      PYTHONHOME: "unsafe-home",
      PYTHONPATH: "unsafe-path",
      PYTHONUTF8: "0",
    }, "C:\\plugins\\agent-worktrees"),
    {
      KEEP: "yes",
      COPILOT_PLUGIN_ROOT: "C:\\plugins\\agent-worktrees",
      PYTHONUTF8: "1",
    },
  );
});

test("resolveSystemCli resolves agent-bridge via its sibling payload, not bare PATH", () => {
  // Regression test: trigger_handoff's best-effort agent-bridge ping used to
  // fall through to the bare command name (execFileSync("agent-bridge", ...))
  // because resolveSystemCliDescriptor's layout map only covered
  // agent-worktrees and agent-dispatch. On Windows, "agent-bridge" is only
  // reachable via .cmd/.ps1 shims, so the bare-name spawn failed with ENOENT
  // even when the agent-bridge plugin was genuinely installed as a sibling.
  const resolved = resolveSystemCli("agent-bridge");
  assert.ok(
    resolved.endsWith(
      process.platform === "win32"
        ? join("bin", "agent-bridge.ps1")
        : join("bin", "agent-bridge"),
    ),
    `expected a sibling-payload path, got: ${resolved}`,
  );
  assert.notEqual(resolved, "agent-bridge");
});

test("checkHeadAlignment flags pending handoffs missing from the monitor registry", () => {
  withTempHome((home) => {
    const repoRoot = join(home, "repo");
    mkdirSync(repoRoot, { recursive: true });
    mkdirSync(join(repoRoot, "wt-active"), { recursive: true });
    mkdirSync(join(repoRoot, "wt-dormant"), { recursive: true });
    const registryDir = join(home, ".agent-worktrees", "status-monitor.d");
    mkdirSync(registryDir, { recursive: true });
    writeFileSync(join(registryDir, "wt-active"), join(repoRoot, "wt-active"), "utf8");

    const execute = (_bin, argv, opts = {}) => {
      if (
        argv[0] === "repos"
        && argv[1] === "list"
        && argv.includes("--class")
        && argv.includes("worktree")
        && argv.includes("--json")
      ) {
        return JSON.stringify({
          repos: [{
            name: "copilot-extensions",
            paths: { windows: repoRoot },
          }],
        });
      }
      if (
        argv[0] === "list"
        && argv.includes("--all")
        && argv.includes("--json")
        && opts.cwd === repoRoot
      ) {
        return JSON.stringify({
          worktrees: [
            {
              id: "active",
              path: join(repoRoot, "wt-active"),
              repo: "copilot-extensions",
              machine: "host-a",
              status: "active",
            },
            {
              id: "dormant",
              path: join(repoRoot, "wt-dormant"),
              repo: "copilot-extensions",
              machine: "host-a",
              status: "active",
            },
          ],
        });
      }
      if (
        argv[0] === "head-session"
        && argv[1] === "--worktree"
        && argv[2] === "active"
      ) {
        return JSON.stringify({
          tracked: true,
          head_session: "session-active",
          active: true,
          occupied: true,
          pending_handoffs: [],
        });
      }
      if (
        argv[0] === "head-session"
        && argv[1] === "--worktree"
        && argv[2] === "dormant"
      ) {
        return JSON.stringify({
          tracked: true,
          head_session: null,
          active: false,
          occupied: true,
          pending_handoffs: [{ token: "handoff-1" }],
        });
      }
      throw new Error(`unexpected command: ${argv.join(" ")} @ ${opts.cwd || ""}`);
    };

    const result = checkHeadAlignment(repoRoot, execute);
    assert.equal(result.checked, 2);
    assert.equal(result.findings.length, 1);
    assert.equal(result.findings[0].reason, "pending-handoff-unregistered");
    assert.equal(result.findings[0].worktree.id, "dormant");
    assert.equal(result.findings[0].monitorSession, "wt-dormant");
  });
});

test("retryStoredHandoffCutover shells to agent-worktrees handoff-cutover --retry", () => {
  const calls = [];
  const result = retryStoredHandoffCutover(
    "C:\\repo",
    "predecessor-1",
    (bin, argv, opts) => {
      calls.push({ bin, argv, opts });
      return JSON.stringify({
        ok: true,
        outcome: "refocused",
        session: "wt-demo",
        successor_session: "successor-1",
        successor_pane: "%5",
      });
    },
  );
  assert.equal(result.ok, true);
  assert.equal(result.outcome, "refocused");
  assert.deepEqual(calls, [{
    bin: "agent-worktrees",
    argv: ["handoff-cutover", "--retry", "--session-id", "predecessor-1", "--json"],
    opts: { cwd: "C:\\repo", timeout: 30000 },
  }]);
});

test("addHandoffClaim journals a task-kind claim on the target worktree, never a self-assign", () => {
  const calls = [];
  addHandoffClaim("C:\\repo", "task-123", "wt-9", (bin, argv, opts) => {
    calls.push({ bin, argv, opts });
    return "{}";
  });
  assert.deepEqual(calls, [{
    bin: "agent-worktrees",
    argv: [
      "claims", "add", "task", "task-123", "--note", "context handoff",
      "--worktree", "wt-9", "--json",
    ],
    opts: { cwd: "C:\\repo", timeout: 15000 },
  }]);
  // Deliberately NOT "agent-dispatch claim" (which would self-assign/claim
  // the task itself) -- only ever "agent-worktrees claims add task", a
  // bookkeeping claim on the worktree's own resource ledger.
  assert.ok(!calls.some((c) => c.bin === "agent-dispatch"));
});

test("addHandoffClaim is best-effort: a throwing executor never propagates", () => {
  assert.doesNotThrow(() => {
    addHandoffClaim("C:\\repo", "task-123", "wt-9", () => {
      throw new Error("agent-worktrees CLI not found");
    });
  });
});

// #3248 review comment 9: dispatchHandoff publishes the task before
// addHandoffClaim journals its claim -- a fast successor can claim/complete
// it in that window before the claim exists, so the async terminal-
// transition release hook fires too early and finds nothing to release.
// releaseHandoffClaimIfTerminal is the post-claim reconciliation: re-read
// the task right after journaling and release inline if it's already
// terminal by then.
test("releaseHandoffClaimIfTerminal releases the claim when the task is already terminal", () => {
  const calls = [];
  releaseHandoffClaimIfTerminal("C:\\repo", "task-123", "wt-9", (bin, argv, opts) => {
    calls.push({ bin, argv, opts });
    if (bin === "agent-dispatch") return JSON.stringify({ id: "task-123", status: "completed" });
    return "{}";
  });
  assert.deepEqual(calls, [
    {
      bin: "agent-dispatch",
      argv: ["show", "task-123"],
      opts: { cwd: "C:\\repo", timeout: 15000 },
    },
    {
      bin: "agent-worktrees",
      argv: ["claims", "release", "task-123", "--worktree", "wt-9", "--json"],
      opts: { cwd: "C:\\repo", timeout: 15000 },
    },
  ]);
});

test("releaseHandoffClaimIfTerminal does nothing when the task is still nonterminal", () => {
  const calls = [];
  releaseHandoffClaimIfTerminal("C:\\repo", "task-123", "wt-9", (bin, argv, opts) => {
    calls.push({ bin, argv, opts });
    return JSON.stringify({ id: "task-123", status: "proposed" });
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].bin, "agent-dispatch");
  assert.ok(!calls.some((c) => c.bin === "agent-worktrees"));
});

test("releaseHandoffClaimIfTerminal treats each terminal status (completed/abandoned/dead_letter) as releasable", () => {
  for (const status of ["completed", "abandoned", "dead_letter"]) {
    const calls = [];
    releaseHandoffClaimIfTerminal("C:\\repo", "task-123", "wt-9", (bin, argv, opts) => {
      calls.push({ bin, argv, opts });
      if (bin === "agent-dispatch") return JSON.stringify({ id: "task-123", status });
      return "{}";
    });
    assert.ok(
      calls.some((c) => c.bin === "agent-worktrees"),
      `expected a release call for status ${status}`,
    );
  }
});

test("releaseHandoffClaimIfTerminal is best-effort: a throwing executor never propagates", () => {
  assert.doesNotThrow(() => {
    releaseHandoffClaimIfTerminal("C:\\repo", "task-123", "wt-9", () => {
      throw new Error("agent-dispatch CLI not found");
    });
  });
});

test("session-state handoff records can be written and marked consumed", () => {
  withTempHome(() => {
    const stored = {
      storage: "file",
      id: "handoff-session-1",
      path: "C:\\state\\handoff-session-1.json",
      metadata: {
        worktree: "wt-example",
        worktreeDir: "C:\\repo\\wt-example",
        stateDir: "C:\\state",
        title: "Continue parser fix",
      },
    };
    const written = writeSessionStateHandoff({
      sid: "session-1",
      promptText: "stored markdown",
      stored,
      seed: "Task: Continue | Resume: /consume-handoff to take over | Recovery: context-handoff file:handoff-session-1",
    });
    assert.equal(written.ok, true);
    const readBack = readSessionStateHandoff("session-1");
    assert.equal(readBack.record.promptText, "stored markdown");
    assert.equal(readBack.record.handoffId, "handoff-session-1");
    const consumed = markSessionStateHandoffConsumed(
      "session-1",
      { consumedBySession: "successor-1", handoffId: "handoff-session-1" },
    );
    assert.equal(consumed.consumed, true);
    assert.equal(consumed.consumedBySession, "successor-1");
  });
});

test("file-backed consume marks the predecessor session-state request consumed", () => {
  withTempHome((home) => {
    const stateDir = join(home, "wt-state");
    const handoffDir = join(stateDir, "handoff");
    mkdirSync(handoffDir, { recursive: true });
    writeSessionStateHandoff({
      sid: "predecessor-1",
      promptText: "stored markdown",
      stored: {
        storage: "file",
        id: "handoff-predecessor-1",
        path: join(handoffDir, "handoff-predecessor-1.json"),
        metadata: { stateDir },
      },
      seed: "Task: Continue | Resume: /consume-handoff to take over | Recovery: context-handoff file:handoff-predecessor-1",
    });
    writeJsonAtomic(join(handoffDir, "handoff-predecessor-1.json"), {
      kind: "context-handoff",
      version: 2,
      id: "handoff-predecessor-1",
      storage: "file",
      sessionId: "predecessor-1",
      cwd: "C:\\repo",
      title: "Continue",
      stateDir,
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });

    const consumed = consumeFileHandoff(
      "C:\\repo",
      "successor-1",
      "handoff-predecessor-1",
      join(handoffDir, "handoff-predecessor-1.json"),
    );
    assert.equal(consumed.ok, true);
    assert.equal(consumed.predecessorSession, "predecessor-1");
    const stateRecord = readSessionStateHandoff("predecessor-1");
    assert.equal(stateRecord.record.consumed, true);
    assert.equal(stateRecord.record.consumedBySession, "successor-1");
  });
});

test("file-backed locator consume uses registered repo paths even when the key does not match the local platform guess", () => {
  withTempHome((home) => {
    const anchorPath = join(home, "wt-repo");
    const foreignPath = process.platform === "win32"
      ? "/tmp/context-handoff-foreign-repo"
      : "Z:\\context-handoff-foreign-repo";
    const resumeCwd = join(home, "resume-home");
    const stateDir = join(home, "wt-state");
    const handoffPath = join(stateDir, "handoff", "handoff-predecessor-wsl.json");
    mkdirSync(anchorPath, { recursive: true });
    mkdirSync(resumeCwd, { recursive: true });
    mkdirSync(join(stateDir, "handoff"), { recursive: true });
    writeJsonAtomic(handoffPath, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-predecessor-wsl",
      storage: "file",
      sessionId: "predecessor-wsl",
      cwd: anchorPath,
      title: "Continue",
      stateDir,
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });

    const consumed = consumeFileHandoff(
      resumeCwd,
      "successor-wsl",
      "handoff-predecessor-wsl",
      null,
      makeLocatorLookupSeams({
        anchorPath,
        repoPaths: process.platform === "win32"
          ? { linux: foreignPath, wsl: anchorPath }
          : { windows: foreignPath, wsl: anchorPath },
        stateDirs: { [anchorPath]: stateDir },
      }),
    );
    assert.equal(consumed.ok, true);
    assert.equal(consumed.path, handoffPath);
  });
});

test("file-backed locator consume succeeds when cwd is outside the adopted project", () => {
  withTempHome((home) => {
    const anchorPath = join(home, "wt-repo");
    const resumeCwd = join(home, "resume-home");
    const stateDir = join(home, "wt-state");
    const handoffPath = join(stateDir, "handoff", "handoff-predecessor-1.json");
    mkdirSync(anchorPath, { recursive: true });
    mkdirSync(resumeCwd, { recursive: true });
    mkdirSync(join(stateDir, "handoff"), { recursive: true });
    writeJsonAtomic(handoffPath, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-predecessor-1",
      storage: "file",
      sessionId: "predecessor-1",
      cwd: anchorPath,
      title: "Continue",
      stateDir,
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });

    const seams = makeLocatorLookupSeams({
      anchorPath,
      stateDirs: { [anchorPath]: stateDir },
    });
    const consumed = consumeFileHandoff(
      resumeCwd,
      "successor-1",
      "handoff-predecessor-1",
      null,
      seams,
    );
    assert.equal(consumed.ok, true);
    assert.equal(consumed.path, handoffPath);
    assert.equal(consumed.payload, "stored markdown");
  });
});

test("file-backed locator consume still succeeds when cwd is the adopted project", () => {
  withTempHome((home) => {
    const anchorPath = join(home, "wt-repo");
    const stateDir = join(home, "wt-state");
    const handoffPath = join(stateDir, "handoff", "handoff-predecessor-2.json");
    mkdirSync(anchorPath, { recursive: true });
    mkdirSync(join(stateDir, "handoff"), { recursive: true });
    writeJsonAtomic(handoffPath, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-predecessor-2",
      storage: "file",
      sessionId: "predecessor-2",
      cwd: anchorPath,
      title: "Continue",
      stateDir,
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });

    const consumed = consumeFileHandoff(
      anchorPath,
      "successor-2",
      "handoff-predecessor-2",
      null,
      {
        get: (key, cwd) => (
          key === "worktree-state-dir" && cwd === anchorPath ? stateDir : null
        ),
        execute: () => {
          throw new Error("locator fallback should not run when cwd already resolves");
        },
      },
    );
    assert.equal(consumed.ok, true);
    assert.equal(consumed.path, handoffPath);
  });
});

test("file-backed locator still fails honestly when no matching handoff exists", () => {
  withTempHome((home) => {
    const anchorPath = join(home, "wt-repo");
    const resumeCwd = join(home, "resume-home");
    const stateDir = join(home, "wt-state");
    mkdirSync(anchorPath, { recursive: true });
    mkdirSync(resumeCwd, { recursive: true });
    mkdirSync(join(stateDir, "handoff"), { recursive: true });

    const seams = makeLocatorLookupSeams({
      anchorPath,
      stateDirs: { [anchorPath]: stateDir },
    });
    const consumed = consumeFileHandoff(
      resumeCwd,
      "successor-1",
      "handoff-missing",
      null,
      seams,
    );
    assert.equal(consumed.ok, false);
    assert.equal(consumed.message, "File-backed handoff was not found.");
  });
});

test("file-backed locator preserves consume-once semantics across cwd-independent discovery", () => {
  withTempHome((home) => {
    const anchorPath = join(home, "wt-repo");
    const resumeCwd = join(home, "resume-home");
    const stateDir = join(home, "wt-state");
    const handoffPath = join(stateDir, "handoff", "handoff-predecessor-3.json");
    mkdirSync(anchorPath, { recursive: true });
    mkdirSync(resumeCwd, { recursive: true });
    mkdirSync(join(stateDir, "handoff"), { recursive: true });
    writeJsonAtomic(handoffPath, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-predecessor-3",
      storage: "file",
      sessionId: "predecessor-3",
      cwd: anchorPath,
      title: "Continue",
      stateDir,
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });

    const seams = makeLocatorLookupSeams({
      anchorPath,
      stateDirs: { [anchorPath]: stateDir },
    });
    const first = consumeFileHandoff(
      resumeCwd,
      "successor-3",
      "handoff-predecessor-3",
      null,
      seams,
    );
    assert.equal(first.ok, true);

    const second = consumeFileHandoff(
      resumeCwd,
      "replay-3",
      "handoff-predecessor-3",
      null,
      seams,
    );
    assert.equal(second.ok, false);
    assert.equal(second.alreadyConsumed, true);
    assert.equal(second.claimedBySession, "successor-3");
    assert.match(second.message, /already consumed/);
    assert.match(second.message, /successor-3/);
  });
});

test("a second consume racing a live in-progress lock reports the lock owner's session id", () => {
  withTempHome((home) => {
    const anchorPath = join(home, "wt-repo");
    const stateDir = join(home, "wt-state");
    const handoffPath = join(stateDir, "handoff", "handoff-predecessor-lock.json");
    mkdirSync(join(stateDir, "handoff"), { recursive: true });
    writeJsonAtomic(handoffPath, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-predecessor-lock",
      storage: "file",
      sessionId: "predecessor-lock",
      cwd: anchorPath,
      title: "Continue",
      stateDir,
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });
    // Simulate another session's in-progress consume by holding the lock file
    // with a live pid (this test process) and its session id, exactly as
    // consumeFileHandoffOnce itself writes it.
    writeFileSync(`${handoffPath}.consume.lock`, JSON.stringify({
      pid: process.pid,
      sessionId: "in-progress-consumer",
      createdAt: new Date().toISOString(),
    }), "utf-8");
    try {
      const result = consumeFileHandoff(
        anchorPath,
        "racing-session",
        "handoff-predecessor-lock",
        handoffPath,
      );
      assert.equal(result.ok, false);
      assert.equal(result.busy, true);
      assert.equal(result.claimedBySession, "in-progress-consumer");
      assert.match(result.message, /in-progress-consumer/);
    } finally {
      try { unlinkSync(`${handoffPath}.consume.lock`); } catch { /* best-effort */ }
    }
  });
});

test("formatConsumeResult surfaces the claimant session id and offers to file a bug", () => {
  const text = formatConsumeResult({
    ok: false,
    alreadyConsumed: true,
    claimedBySession: "some-other-session",
    message: "Handoff X was already consumed by session `some-other-session`.",
  });
  assert.match(text, /some-other-session/);
  assert.match(text, /offer to file a bug/i);
});

test("formatConsumeResult without a known claimant does not fabricate a bug offer", () => {
  const text = formatConsumeResult({
    ok: false,
    message: "File-backed handoff was not found.",
  });
  assert.doesNotMatch(text, /offer to file a bug/i);
});

test("noteHandoffInRecord returns the CLI's confirmed outcome, not the request", () => {
  const execute = (bin, argv) => {
    assert.equal(bin, "agent-worktrees");
    assert.deepEqual(argv, [
      "note-handoff", "--task", "handoff-1", "--title", "Fix the widget",
      "--session-id", "predecessor-1", "--live-cutover",
    ]);
    return JSON.stringify({
      noted: true, worktree_id: "wt-1", session: "predecessor-1",
      task: "handoff-1", handoff_ordinal: 1, live_cutover: true,
    });
  };
  const result = noteHandoffInRecord(
    "C:\\repo", "predecessor-1", "handoff-1", "Fix the widget", true, execute,
  );
  assert.equal(result.noted, true);
  assert.equal(result.liveCutover, true);
  assert.equal(result.error, null);
  assert.equal(result.raw.worktree_id, "wt-1");
});

test("noteHandoffInRecord reports noted:false for an untracked worktree instead of assuming success", () => {
  // Copilot review finding on PR #4493: the CLI itself reports noted:false
  // for an untracked worktree (still exit 0) -- a caller that only checks
  // "did the subprocess throw" would wrongly conclude the handoff was
  // recorded.
  const execute = () => JSON.stringify({ noted: false, reason: "not a tracked worktree" });
  const result = noteHandoffInRecord("C:\\repo", "sid", "handoff-1", "t", true, execute);
  assert.equal(result.noted, false);
  assert.equal(result.liveCutover, false);
  assert.equal(result.raw.reason, "not a tracked worktree");
});

test("noteHandoffInRecord degrades to noted:false with an error instead of throwing on a CLI failure", () => {
  const execute = () => { throw new Error("agent-worktrees unreachable"); };
  assert.doesNotThrow(() => {
    const result = noteHandoffInRecord("C:\\repo", "sid", "handoff-1", "t", false, execute);
    assert.equal(result.noted, false);
    assert.equal(result.liveCutover, false);
    assert.match(result.error, /agent-worktrees unreachable/);
  });
});

test("promoteSuccessorHead links succession over the expected predecessor head", () => {
  const calls = [];
  const execute = (bin, argv, opts) => {
    calls.push({ bin, argv, opts });
    if (argv[0] === "head-session") {
      return JSON.stringify({
        tracked: true,
        head_session: "predecessor-x",
      });
    }
    if (argv[0] === "link-succession") return JSON.stringify({ ok: true });
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
  const result = promoteSuccessorHead(
    "C:\\repo", "wt-1", "predecessor-x", "successor-x", execute,
  );
  assert.deepEqual(result, {
    promoted: true,
    predecessor: "predecessor-x",
    successor: "successor-x",
  });
  const linkCall = calls.find((c) => c.argv[0] === "link-succession");
  assert.ok(linkCall, "expected a link-succession call");
  assert.deepEqual(linkCall.argv, [
    "link-succession",
    "--worktree", "wt-1",
    "--predecessor", "predecessor-x",
    "--successor", "successor-x",
    "--predecessor-state", "handed-off",
    "--json",
  ]);
});

test("promoteSuccessorHead is a no-op when this session is already the recorded head", () => {
  const calls = [];
  const execute = (bin, argv) => {
    calls.push(argv[0]);
    return JSON.stringify({ tracked: true, head_session: "successor-x" });
  };
  const result = promoteSuccessorHead(
    "C:\\repo", "wt-1", "predecessor-x", "successor-x", execute,
  );
  assert.deepEqual(result, { promoted: false, reason: "already-head" });
  assert.deepEqual(calls, ["head-session"]);
});

test("promoteSuccessorHead never guesses a substitute predecessor when the live head has diverged", () => {
  // The handoff's own recorded author is "predecessor-x", but something
  // else already made "someone-else" the live head (e.g. an unrelated
  // succession, or a race with another consumer). Superseding that would
  // wrongly conclude an unrelated session's lineage -- must be a no-op.
  const execute = (bin, argv) => {
    if (argv[0] === "head-session") {
      return JSON.stringify({ tracked: true, head_session: "someone-else" });
    }
    throw new Error(`unexpected CLI call: ${argv.join(" ")}`);
  };
  const result = promoteSuccessorHead(
    "C:\\repo", "wt-1", "predecessor-x", "successor-x", execute,
  );
  assert.deepEqual(result, {
    promoted: false,
    reason: "head-diverged",
    currentHead: "someone-else",
    expectedPredecessor: "predecessor-x",
  });
});

test("promoteSuccessorHead is a no-op when the worktree has no recorded head at all", () => {
  const execute = () => JSON.stringify({ tracked: true, head_session: null });
  const result = promoteSuccessorHead(
    "C:\\repo", "wt-1", "predecessor-x", "successor-x", execute,
  );
  assert.deepEqual(result, {
    promoted: false,
    reason: "head-diverged",
    currentHead: null,
    expectedPredecessor: "predecessor-x",
  });
});

test("promoteSuccessorHead is a no-op when the worktree is untracked", () => {
  const execute = () => JSON.stringify({ tracked: false });
  const result = promoteSuccessorHead(
    "C:\\repo", "wt-1", "predecessor-x", "successor-x", execute,
  );
  assert.deepEqual(result, { promoted: false, reason: "untracked" });
});

test("promoteSuccessorHead is a no-op without a known predecessor to supersede", () => {
  const execute = () => {
    throw new Error("must not call the CLI without a predecessor id");
  };
  const result = promoteSuccessorHead(
    "C:\\repo", "wt-1", null, "successor-x", execute,
  );
  assert.deepEqual(result, { promoted: false, reason: "missing-ids" });
});

test("promoteSuccessorHead degrades to link-failed instead of throwing on a CLI error", () => {
  const execute = (bin, argv) => {
    if (argv[0] === "head-session") {
      return JSON.stringify({ tracked: true, head_session: "predecessor-x" });
    }
    throw new Error("agent-worktrees unreachable");
  };
  const result = promoteSuccessorHead(
    "C:\\repo", "wt-1", "predecessor-x", "successor-x", execute,
  );
  assert.equal(result.promoted, false);
  assert.equal(result.reason, "link-failed");
  assert.match(result.error, /agent-worktrees unreachable/);
});

test("file-backed consume promotes the successor session to worktree head (best-effort backstop)", () => {
  withTempHome((home) => {
    const stateDir = join(home, "wt-state");
    const handoffDir = join(stateDir, "handoff");
    mkdirSync(handoffDir, { recursive: true });
    const handoffPath = join(handoffDir, "handoff-promo-1.json");
    writeJsonAtomic(handoffPath, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-promo-1",
      storage: "file",
      sessionId: "predecessor-promo",
      cwd: "C:\\repo",
      title: "Continue",
      stateDir,
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
      worktree: "wt-promo-1",
    });

    const promoteCalls = [];
    const consumed = consumeFileHandoff(
      "C:\\repo",
      "successor-promo",
      "handoff-promo-1",
      handoffPath,
      { promoteHead: (...args) => { promoteCalls.push(args); } },
    );
    assert.equal(consumed.ok, true);
    assert.deepEqual(
      promoteCalls,
      [["C:\\repo", "wt-promo-1", "predecessor-promo", "successor-promo"]],
    );
  });
});

test("file-backed consume never lets a throwing head-promotion backstop fail the consume result", () => {
  withTempHome((home) => {
    const stateDir = join(home, "wt-state");
    const handoffDir = join(stateDir, "handoff");
    mkdirSync(handoffDir, { recursive: true });
    const handoffPath = join(handoffDir, "handoff-promo-2.json");
    writeJsonAtomic(handoffPath, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-promo-2",
      storage: "file",
      sessionId: "predecessor-promo-2",
      cwd: "C:\\repo",
      title: "Continue",
      stateDir,
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
      worktree: "wt-promo-2",
    });

    assert.doesNotThrow(() => {
      const consumed = consumeFileHandoff(
        "C:\\repo",
        "successor-promo-2",
        "handoff-promo-2",
        handoffPath,
        {
          promoteHead: () => { throw new Error("agent-worktrees unreachable"); },
        },
      );
      assert.equal(consumed.ok, true);
    });
  });
});

test("task-backed consume promotes the successor session to worktree head using the task's own metadata", () => {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-promo-task-"));
  const metadata = {
    stateDir: dir,
    sessionId: "predecessor-task-promo",
    worktree: "wt-task-promo",
    title: "Continue parser fix",
  };
  const payload = encodeHandoffPayload("full brief", metadata);
  const promoteCalls = [];
  try {
    const result = consumeDispatchHandoffTask(
      "C:\\repo",
      "task-promo-1",
      "successor-task-promo",
      true,
      {
        readPayload: () => payload,
        consumeTask: () => payload,
        stateDirResolver: () => dir,
        promoteHead: (...args) => { promoteCalls.push(args); },
      },
    );
    assert.equal(result.ok, true);
    assert.equal(result.predecessorSession, "predecessor-task-promo");
    assert.equal(result.worktree, "wt-task-promo");
    assert.deepEqual(
      promoteCalls,
      [[
        "C:\\repo", "wt-task-promo", "predecessor-task-promo",
        "successor-task-promo",
      ]],
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("task-backed consume checkpoints payload before one-time consume and survives same-session retry", () => {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-delivery-"));
  const metadata = {
    stateDir: dir,
    sessionId: "predecessor-1",
    worktree: "wt-example",
    title: "Continue parser fix",
  };
  const payload = encodeHandoffPayload("full brief", metadata);
  let consumeCalls = 0;
  try {
    const first = consumeDispatchHandoffTask(
      "C:\\repo",
      "task-1",
      "successor-1",
      true,
      {
        readPayload: () => payload,
        consumeTask: () => {
          consumeCalls++;
          return payload;
        },
        stateDirResolver: () => dir,
      },
    );
    assert.equal(first.ok, true);
    assert.equal(first.payload, "full brief");
    assert.match(first.checkpoint, /delivery-task-1\.json$/);
    const saved = JSON.parse(readFileSync(first.checkpoint, "utf-8"));
    assert.equal(saved.steps.taskConsumed, true);

    const second = consumeDispatchHandoffTask(
      "C:\\repo",
      "task-1",
      "successor-1",
      true,
      {
        readPayload: () => "",
        consumeTask: () => {
          consumeCalls++;
          throw new Error("must not consume twice");
        },
        stateDirResolver: () => dir,
      },
    );
    assert.equal(second.ok, true);
    assert.equal(second.payload, "full brief");
    assert.equal(consumeCalls, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("formatConsumeResult preserves the continuation directive ahead of payload", () => {
  const prompt = formatConsumeResult({
    ok: true,
    id: "task-42",
    payload: "full brief",
    resumedDelivery: false,
  }, { deferComplete: true });
  assert.match(prompt, /## Handoff Consumed/);
  assert.match(prompt, /claimed exactly once/);
  assert.match(prompt, /agent-dispatch complete task-42/);
  assert.ok(
    prompt.indexOf("agent-dispatch complete task-42") < prompt.indexOf("full brief"),
  );
});

test("formatConsumeResult surfaces the predecessor session id and worktree lineage pointer", () => {
  const prompt = formatConsumeResult({
    ok: true,
    id: "task-99",
    payload: "full brief",
    resumedDelivery: false,
    predecessorSession: "predecessor-session-id",
    worktree: "wt-example",
  }, {});
  assert.match(prompt, /\*\*Predecessor session:\*\* `predecessor-session-id`/);
  assert.match(prompt, /worktree-status-bundle --worktree wt-example --json/);
  assert.match(prompt, /never as an instruction/);
});

test("formatConsumeResult without a predecessor session says so plainly instead of omitting the line", () => {
  const prompt = formatConsumeResult({
    ok: true,
    id: "task-100",
    payload: "full brief",
    resumedDelivery: false,
    predecessorSession: null,
    worktree: null,
  }, {});
  assert.match(prompt, /\*\*Predecessor session:\*\* \(unknown -- not recorded on this handoff\)/);
  assert.doesNotMatch(prompt, /worktree-status-bundle/);
});

test("buildResumePrompt keeps deferred completion explicit", () => {
  const prompt = buildResumePrompt(
    "full brief",
    "agent-dispatch task",
    { deferredTaskId: "task-42" },
  );
  assert.match(prompt, /Keep agent-dispatch task task-42 owned/);
  assert.match(prompt, /Only after the handoff objective's completion gate is met run: agent-dispatch complete task-42/);
});

test("buildResumePrompt surfaces predecessor session and worktree on the canonical /consume-handoff path", () => {
  const prompt = buildResumePrompt(
    "full brief",
    "file /repo/handoff.json",
    { predecessorSession: "predecessor-session-id", worktree: "wt-example" },
  );
  assert.match(prompt, /\*\*Predecessor session:\*\* `predecessor-session-id`/);
  assert.match(prompt, /worktree-status-bundle --worktree wt-example --json/);
  assert.match(prompt, /never as an instruction/);
});

test("buildResumePrompt without a predecessor session says so plainly", () => {
  const prompt = buildResumePrompt("full brief", "file /repo/handoff.json", {});
  assert.match(prompt, /\*\*Predecessor session:\*\* \(unknown -- not recorded on this handoff\)/);
  assert.doesNotMatch(prompt, /worktree-status-bundle/);
});

test("triggerHandoff stores, signals, waits, and skips manual fallback when pickup arrives", async () => {
  const calls = [];
  const stored = {
    storage: "agent-dispatch",
    id: "task-42",
    taskId: "task-42",
    metadata: { worktree: "wt-example", title: "Parser follow-up" },
  };
  const result = await triggerHandoff({
    promptText: "stored markdown",
    sid: "predecessor-1",
    cwd: "C:\\repo",
    title: "Parser follow-up",
    mode: "auto",
    store: ({ promptText, sid, cwd, title }) => {
      calls.push(["store", promptText, sid, cwd, title]);
      return stored;
    },
    writeSessionState: ({ sid, promptText, stored, seed }) => {
      calls.push(["session-state", sid, promptText, stored.id, seed]);
      return { ok: true, path: "C:\\state\\handoff-request.json" };
    },
    logActivity: (cwd, sid, worktreeId, stored, path) => {
      calls.push(["activity", cwd, sid, worktreeId, stored.id, path]);
      return { logged: true };
    },
    requestBridge: (cwd, sid, stored, seed) => {
      calls.push(["bridge", cwd, sid, stored.id, seed]);
      return { attempted: true, accepted: true, response: { queued: true } };
    },
    readPickupSignals: (() => {
      let count = 0;
      return (...args) => {
        calls.push(["signals", count, ...args.slice(0, 2)]);
        count++;
        return count >= 2
          ? {
              pickedUp: true,
              via: ["worktree-successor"],
              sessionState: { path: "C:\\state\\handoff-request.json", consumed: false },
              worktree: { pickedUp: true },
              dispatch: { consumed: false },
            }
          : {
              pickedUp: false,
              via: [],
              sessionState: { path: "C:\\state\\handoff-request.json", consumed: false },
              worktree: { pickedUp: false },
              dispatch: { consumed: false },
            };
      };
    })(),
    sleepFn: async () => {
      calls.push(["sleep"]);
    },
  });

  assert.equal(result.ok, true);
  assert.equal(result.stored.id, "task-42");
  assert.equal(result.manualInstructions, null);
  assert.deepEqual(result.pickup.via, ["worktree-successor"]);
  assert.equal(calls[0][0], "store");
  assert.equal(calls[1][0], "session-state");
  assert.equal(calls[2][0], "activity");
  assert.equal(calls[3][0], "bridge");
  assert.ok(calls.some(([name]) => name === "sleep"));
  assert.match(result.seed, /task:task-42$/);
});

test("triggerHandoff calls afterStore once the baton is durably stored, and beforeArmPickup only in auto mode before arming pickup", async () => {
  // Real regression this guards: a caller's side task (e.g. a worktree
  // sync) must never start before the store is confirmed, and must only
  // gate the live-pickup signal in auto mode (manual-only never arms
  // pickup, so there is nothing to gate and no reason to pay that latency).
  const calls = [];
  const stored = {
    storage: "agent-dispatch",
    id: "task-77",
    taskId: "task-77",
    metadata: { worktree: "wt-example", title: "t" },
  };
  await triggerHandoff({
    promptText: "markdown",
    sid: "sess-1",
    cwd: "C:\\repo",
    title: "t",
    mode: "auto",
    store: () => { calls.push("store"); return stored; },
    writeSessionState: () => { calls.push("session-state"); return { ok: true, path: "p" }; },
    logActivity: () => { calls.push("activity"); return { logged: true }; },
    requestBridge: () => { calls.push("bridge"); return { attempted: false, accepted: false }; },
    readPickupSignals: () => ({ pickedUp: true, via: ["x"], sessionState: {}, worktree: {}, dispatch: {} }),
    sleepFn: async () => {},
    afterStore: () => { calls.push("afterStore"); },
    beforeArmPickup: async () => { calls.push("beforeArmPickup"); },
  });
  const order = ["store", "session-state", "afterStore", "beforeArmPickup", "activity"];
  assert.deepEqual(calls.slice(0, order.length), order);
});

test("triggerHandoff never calls afterStore/beforeArmPickup when the store itself fails", async () => {
  const calls = [];
  const result = await triggerHandoff({
    promptText: "markdown",
    sid: "sess-1",
    cwd: "C:\\repo",
    title: "t",
    mode: "auto",
    store: () => ({ storage: null, error: "no safe store" }),
    afterStore: () => { calls.push("afterStore"); },
    beforeArmPickup: async () => { calls.push("beforeArmPickup"); },
  });
  assert.equal(result.ok, false);
  assert.deepEqual(calls, []);
});

test("triggerHandoff calls afterStore but never beforeArmPickup under manual-only mode", async () => {
  const calls = [];
  const stored = {
    storage: "agent-dispatch",
    id: "task-78",
    taskId: "task-78",
    metadata: { worktree: "wt-example", title: "t" },
  };
  await triggerHandoff({
    promptText: "markdown",
    sid: "sess-1",
    cwd: "C:\\repo",
    title: "t",
    mode: "manual-only",
    store: () => stored,
    writeSessionState: () => ({ ok: true, path: "p" }),
    afterStore: () => { calls.push("afterStore"); },
    beforeArmPickup: async () => { calls.push("beforeArmPickup"); },
  });
  assert.deepEqual(calls, ["afterStore"]);
});

test("triggerHandoff logs the predecessor pid in the handoff_requested activity", async () => {
  const execCalls = [];
  await triggerHandoff({
    promptText: "stored markdown",
    sid: "predecessor-1",
    cwd: "C:\\repo",
    title: "Parser follow-up",
    mode: "auto",
    execute: (bin, argv, opts) => {
      execCalls.push({ bin, argv, opts });
      return "";
    },
    store: () => ({
      storage: "file",
      id: "handoff-predecessor-1",
      path: "C:\\state\\handoff-predecessor-1.json",
      metadata: { worktree: "wt-example", title: "Parser follow-up" },
    }),
    writeSessionState: () => ({ ok: true, path: "C:\\state\\handoff-request.json" }),
    noteHandoff: () => {},
    requestBridge: () => ({ attempted: false, accepted: false }),
    readPickupSignals: () => ({
      pickedUp: false,
      via: [],
      sessionState: { path: "C:\\state\\handoff-request.json", consumed: false },
      worktree: { pickedUp: false },
      dispatch: { consumed: false },
    }),
    sleepFn: async () => {},
    waitMs: 0,
  });

  const activityCall = execCalls.find(
    ({ bin, argv }) => bin === "agent-worktrees" && argv[0] === "activity-log",
  );
  assert.ok(activityCall, "expected triggerHandoff to emit activity-log");
  assert.ok(
    activityCall.argv.includes(`predecessor_pid=${process.ppid}`),
    `expected predecessor_pid field in ${JSON.stringify(activityCall.argv)}`,
  );
});

test("triggerHandoff always returns the final seed and manual fallback when nothing picks it up", async () => {
  const result = await triggerHandoff({
    promptText: "stored markdown",
    sid: "predecessor-1",
    cwd: "C:\\repo",
    title: "Parser follow-up",
    mode: "auto",
    store: () => ({
      storage: "file",
      id: "handoff-predecessor-1",
      path: "C:\\state\\handoff-predecessor-1.json",
      metadata: { worktree: "wt-example", title: "Parser follow-up" },
    }),
    writeSessionState: ({ seed }) => ({ ok: true, path: "C:\\state\\handoff-request.json", seed }),
    noteHandoff: () => {},
    logActivity: () => ({ logged: true }),
    requestBridge: () => ({ attempted: true, accepted: false, error: "unsupported" }),
    readPickupSignals: () => ({
      pickedUp: false,
      spawnInFlight: false,
      via: [],
      sessionState: { path: "C:\\state\\handoff-request.json", consumed: false },
      worktree: { pickedUp: false },
      dispatch: { consumed: false },
    }),
    sleepFn: async () => {},
    waitMs: 0,
  });
  assert.equal(result.ok, true);
  assert.match(result.manualInstructions, /No control system acknowledged the request/);
  assert.match(result.manualInstructions, new RegExp(result.seed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("triggerHandoff exits early and reports progress once a spawn is merely in flight", async () => {
  const calls = [];
  const result = await triggerHandoff({
    promptText: "stored markdown",
    sid: "predecessor-1",
    cwd: "C:\\repo",
    title: "Parser follow-up",
    mode: "auto",
    store: () => ({
      storage: "file",
      id: "handoff-predecessor-1",
      path: "C:\\state\\handoff-predecessor-1.json",
      metadata: { worktree: "wt-example", title: "Parser follow-up" },
    }),
    writeSessionState: ({ seed }) => ({ ok: true, path: "C:\\state\\handoff-request.json", seed }),
    noteHandoff: () => {},
    logActivity: () => ({ logged: true }),
    requestBridge: () => ({ attempted: false, accepted: false }),
    readPickupSignals: (() => {
      let count = 0;
      return () => {
        count++;
        calls.push(count);
        return {
          pickedUp: false,
          spawnInFlight: count >= 2,
          via: [],
          sessionState: { path: "C:\\state\\handoff-request.json", consumed: false },
          worktree: { pickedUp: false },
          dispatch: { consumed: false },
        };
      };
    })(),
    sleepFn: async () => {},
    // A ceiling far longer than this test should ever actually wait -- it
    // must exit as soon as spawnInFlight flips true, not run out the clock.
    waitMs: 120000,
  });
  assert.equal(result.ok, true);
  assert.equal(result.pickup.pickedUp, false);
  assert.equal(result.pickup.spawnInFlight, true);
  // Exactly two reads: the first (not yet in flight) then the one that
  // flipped spawnInFlight true -- proves the loop didn't keep polling.
  assert.equal(calls.length, 2);
  assert.match(result.manualInstructions, /already been.*spawned and is starting up/s);
  assert.match(result.manualInstructions, /expected in-progress state, not a failure/);
  assert.doesNotMatch(result.manualInstructions, /No control system acknowledged the request/);
});

test("triggerHandoff under the default (manual-only) mode never wires up automatic pickup", async () => {
  const calls = [];
  const result = await triggerHandoff({
    promptText: "stored markdown",
    sid: "predecessor-1",
    cwd: "C:\\repo",
    title: "Parser follow-up",
    // mode omitted deliberately -- proves the safe default gates this, not
    // an explicitly-passed value.
    store: () => ({
      storage: "file",
      id: "handoff-predecessor-1",
      path: "C:\\state\\handoff-predecessor-1.json",
      metadata: { worktree: "wt-example", title: "Parser follow-up" },
    }),
    writeSessionState: ({ seed }) => ({ ok: true, path: "C:\\state\\handoff-request.json", seed }),
    noteHandoff: (...args) => {
      calls.push(["note-handoff", ...args]);
      return { noted: true, liveCutover: args.at(-1), raw: {}, error: null };
    },
    logActivity: (...args) => {
      calls.push(["activity", ...args]);
      return { logged: true };
    },
    requestBridge: (...args) => {
      calls.push(["bridge", ...args]);
      return { attempted: true, accepted: true, response: { queued: true } };
    },
    readPickupSignals: () => {
      calls.push(["signals"]);
      return {
        pickedUp: false,
        spawnInFlight: false,
        via: [],
        sessionState: { path: "C:\\state\\handoff-request.json", consumed: false },
        worktree: { pickedUp: false },
        dispatch: { consumed: false },
      };
    },
    sleepFn: async () => {
      calls.push(["sleep"]);
    },
    waitMs: 120000,
  });
  assert.equal(result.ok, true);
  // `noteHandoff` (agent-worktrees `note-handoff`) now always fires -- this
  // is head-tracking/lineage state ("manual" mode must not withhold it, per
  // the mode redesign), not a live-cutover trigger. What manual mode still
  // withholds is the actual cutover machinery: neither the activity event
  // agent-worktrees' resident monitor primarily watches for, nor the
  // agent-bridge ping, ever fire. And the note-handoff call itself carries
  // `liveCutover: false` (see the 5th positional arg below), which is what
  // now keeps the monitor's own spawn-eligibility gate closed for this
  // entry -- not the entry's mere absence (the pre-this-change gap a
  // Copilot review caught on PR #3041, before that field existed).
  assert.deepEqual(calls.map(([name]) => name), ["note-handoff", "signals"]);
  assert.deepEqual(calls[0], [
    "note-handoff",
    "C:\\repo",
    "predecessor-1",
    "handoff-predecessor-1",
    "Parser follow-up",
    false,
  ]);
  assert.equal(result.worktreeSignal.noted, true);
  assert.equal(result.worktreeSignal.activity.logged, false);
  assert.equal(result.bridge.attempted, false);
  assert.match(
    result.manualInstructions,
    /Automatic cutover is disabled.*mode.*is not `auto`/s,
  );
});

test("triggerHandoff force=true arms live-cutover signaling even under manual-only mode", async () => {
  // #mux-companion-manual-cutover-diagnostics: an explicit, single-call
  // opt-in for a human-gated diagnostic trigger (never set by an ordinary
  // agent-invoked trigger_handoff call) -- arms the SAME noteHandoff/
  // activity/bridge signals `mode: auto` would, without changing the
  // configured mode itself.
  const calls = [];
  const result = await triggerHandoff({
    promptText: "stored markdown",
    sid: "predecessor-1",
    cwd: "C:\\repo",
    title: "Parser follow-up",
    mode: "manual-only",
    force: true,
    store: () => ({
      storage: "file",
      id: "handoff-predecessor-1",
      path: "C:\\state\\handoff-predecessor-1.json",
      metadata: { worktree: "wt-example", title: "Parser follow-up" },
    }),
    writeSessionState: ({ seed }) => ({ ok: true, path: "C:\\state\\handoff-request.json", seed }),
    noteHandoff: (...args) => {
      calls.push(["note-handoff", ...args]);
      return { noted: true, liveCutover: args.at(-1), raw: {}, error: null };
    },
    logActivity: (...args) => {
      calls.push(["activity", ...args]);
      return { logged: true };
    },
    requestBridge: (...args) => {
      calls.push(["bridge", ...args]);
      return { attempted: true, accepted: true, response: { queued: true } };
    },
    readPickupSignals: () => ({
      pickedUp: false, spawnInFlight: false, via: [],
      sessionState: { path: "C:\\state\\handoff-request.json", consumed: false },
      worktree: { pickedUp: false }, dispatch: { consumed: false },
    }),
    sleepFn: async () => { calls.push(["sleep"]); },
    waitMs: 0,
  });
  assert.equal(result.ok, true);
  assert.equal(result.automaticCutoverDisabled, false);
  assert.equal(result.worktreeSignal.noted, true);
  assert.equal(result.worktreeSignal.activity.logged, true);
  assert.equal(result.bridge.attempted, true);
  const noteCall = calls.find(([name]) => name === "note-handoff");
  assert.ok(noteCall, "expected a note-handoff call");
  assert.equal(noteCall.at(-1), true, "expected liveCutover armed (last arg true)");
  assert.ok(calls.some(([name]) => name === "activity"));
  assert.ok(calls.some(([name]) => name === "bridge"));
});

test("triggerHandoff omitting force still notes the handoff (lineage tracking), but never arms live-cutover", async () => {
  // Regression guard for the PR #3041 fix this effort builds on top of --
  // force defaults to false, so activity/bridge (the actual spawn+retire
  // signals) must behave identically to before force existed. `noteHandoff`
  // itself is now intentionally NOT part of that guarantee: it always
  // fires regardless of mode (mode governs live-cutover arming, not whether
  // handoff state gets recorded at all -- see the mode redesign this test
  // was updated for), but always with `liveCutover: false` here, which is
  // what keeps the monitor's own gate closed.
  const calls = [];
  const result = await triggerHandoff({
    promptText: "stored markdown",
    sid: "predecessor-1",
    cwd: "C:\\repo",
    title: "Parser follow-up",
    mode: "manual-only",
    store: () => ({
      storage: "file",
      id: "handoff-predecessor-1",
      path: "C:\\state\\handoff-predecessor-1.json",
      metadata: { worktree: "wt-example", title: "Parser follow-up" },
    }),
    writeSessionState: ({ seed }) => ({ ok: true, path: "C:\\state\\handoff-request.json", seed }),
    noteHandoff: (...args) => {
      calls.push(["note-handoff", ...args]);
      return { noted: true, liveCutover: args.at(-1), raw: {}, error: null };
    },
    logActivity: (...args) => { calls.push(["activity", ...args]); return { logged: true }; },
    requestBridge: (...args) => { calls.push(["bridge", ...args]); return { attempted: true, accepted: true }; },
    readPickupSignals: () => ({
      pickedUp: false, spawnInFlight: false, via: [],
      sessionState: { path: "C:\\state\\handoff-request.json", consumed: false },
      worktree: { pickedUp: false }, dispatch: { consumed: false },
    }),
  });
  assert.equal(result.ok, true);
  assert.equal(result.automaticCutoverDisabled, true);
  assert.equal(result.worktreeSignal.noted, true);
  assert.equal(result.worktreeSignal.activity.logged, false);
  assert.equal(result.bridge.attempted, false);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "note-handoff");
  assert.equal(calls[0].at(-1), false, "expected liveCutover NOT armed (last arg false)");
});

test("storeHandoff never notes the worktree record itself (save_handoff_prompt must never arm pickup)", () => {
  // Root-cause fix for the same High-severity gap: `storeHandoff()` backs
  // BOTH `save_handoff_prompt` (documented as never arming pickup) and
  // `trigger_handoff`. It used to call `noteHandoffInRecord()` internally
  // regardless of caller or mode, so even `save_handoff_prompt` alone --
  // with no trigger_handoff call at all -- created a `pending_handoffs`
  // entry agent-worktrees' resident monitor could discover and claim.
  // Structural check (storeHandoff has no dependency-injection seam for
  // its internal helpers): its source must never reference
  // `noteHandoffInRecord` -- only `triggerHandoff()` may call it, and only
  // when `mode: auto` is configured.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const storeHandoffBody = source.slice(
    source.indexOf("export function storeHandoff("),
    source.indexOf("export function buildSeedForStored("),
  );
  assert.ok(storeHandoffBody.length > 0, "could not locate storeHandoff's body");
  assert.doesNotMatch(storeHandoffBody, /noteHandoffInRecord/);
});

test("triggerHandoff reports when an explicit stored baton cannot be recovered", async () => {
  const result = await triggerHandoff({
    sid: "predecessor-1",
    cwd: "C:\\repo",
    handoffToken: "handoff-predecessor-1",
    sleepFn: async () => {},
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "not-found");
});

test("triggerHandoff refuses to re-arm a handoff aborted between recovery and arming", async () => {
  // Real regression this guards (PR #4570 review round 16): the status/
  // consumed check only protects the instant recovery runs -- without a
  // fresh re-check immediately before arming, a concurrent abort landing in
  // that gap would still let this trigger re-arm the token abort just
  // retired. This narrows (does not fully eliminate) the race.
  let showCalls = 0;
  const execute = (bin, argv) => {
    if (argv[0] === "payload") {
      return encodeHandoffPayload("body", { sessionId: "predecessor-1" });
    }
    if (argv[0] === "show") {
      showCalls += 1;
      const status = showCalls === 1 ? "queued" : "abandoned";
      return JSON.stringify({ id: "task-race", status });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
  const result = await triggerHandoff({
    sid: "predecessor-1",
    cwd: "C:\\repo",
    handoffToken: "task-race",
    execute,
    sleepFn: async () => {},
    writeSessionState: () => { throw new Error("must not arm a retired handoff"); },
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "not-found");
  assert.equal(showCalls, 2);
});

// -- Phase 3 item 3: the deterministic "did my launch actually land" signal --

test("logHandoffPromptReceived shells the exact activity-log invocation", () => {
  const calls = [];
  const execute = (bin, argv, opts) => {
    calls.push({ bin, argv, opts });
    return "";
  };
  const result = logHandoffPromptReceived(
    "C:\\repo", "wt-example", "successor-1", "task:handoff-1", execute,
  );
  assert.equal(result.logged, true);
  assert.equal(calls.length, 1);
  const { bin, argv } = calls[0];
  assert.equal(bin, "agent-worktrees");
  assert.deepEqual(argv, [
    "activity-log", "context_handoff_prompt_received",
    "--worktree-id", "wt-example",
    "--session-id", "successor-1",
    "--source", "context-handoff",
    "--field", "locator=task:handoff-1",
  ]);
});

test("logHandoffPromptReceived is a no-op without a worktree id or locator", () => {
  const execute = () => { throw new Error("must not be called"); };
  assert.deepEqual(
    logHandoffPromptReceived("C:\\repo", null, "sid", "task:handoff-1", execute),
    { logged: false },
  );
  assert.deepEqual(
    logHandoffPromptReceived("C:\\repo", "wt-example", "sid", null, execute),
    { logged: false },
  );
});

test("logHandoffPromptReceived degrades to logged:false on a CLI failure", () => {
  const execute = () => { throw new Error("agent-worktrees not found"); };
  const result = logHandoffPromptReceived(
    "C:\\repo", "wt-example", "sid", "task:handoff-1", execute,
  );
  assert.equal(result.logged, false);
  assert.match(result.error, /agent-worktrees not found/);
});

test("triggerHandoff's real pickupSignals path surfaces prompt-received deterministically", async () => {
  const execute = (bin, argv) => {
    if (bin === "agent-worktrees" && argv[0] === "head-session") {
      return JSON.stringify({ tracked: false });
    }
    if (
      bin === "agent-worktrees"
      && argv[0] === "activity"
      && argv.includes("context_handoff_prompt_received")
    ) {
      return `${JSON.stringify({ locator: "file:handoff-predecessor-1" })}\n`;
    }
    return "";
  };

  const result = await triggerHandoff({
    promptText: "stored markdown",
    sid: "predecessor-1",
    cwd: "C:\\repo",
    title: "Parser follow-up",
    store: () => ({
      storage: "file",
      id: "handoff-predecessor-1",
      path: "C:\\state\\handoff-predecessor-1.json",
      metadata: { worktree: "wt-example", title: "Parser follow-up" },
    }),
    writeSessionState: ({ seed }) => (
      { ok: true, path: "C:\\state\\handoff-request.json", seed }
    ),
    noteHandoff: () => {},
    logActivity: () => ({ logged: true }),
    requestBridge: () => ({ attempted: false, accepted: false }),
    execute,
    sleepFn: async () => {},
    waitMs: 0,
  });
  assert.equal(result.pickup.pickedUp, true);
  assert.ok(result.pickup.via.includes("prompt-received"));
  assert.equal(result.pickup.promptReceived.received, true);
});

test("utility helpers preserve safe normalization and bounded CLI diagnostics", () => {
  assert.equal(normalizeHandoffTitle("  Fix %PATH%\r\nthen\0 validate\t now  "), "Fix %PATH% then validate now");
  assert.equal(safePathSegment("a/b c:d"), "a_b_c_d");
  assert.equal(safePathSegment("").length > 0, true);
  const result = agentWorktreesGetResult(
    "worktree-dir",
    "C:\\repo",
    "session-1",
    () => "",
  );
  assert.match(result.error, /returned an empty result/);
  const binding = sessionBindingForSession(
    "session-1",
    "C:\\repo",
    (_bin, _args) => JSON.stringify({ found: true, session_id: "session-1" }),
  );
  assert.equal(binding.found, true);
});

// --- handoffDedupKey (round-9 review fix: a repeat save_handoff_prompt call
// after this session's own worktree sync must actually refresh a
// task-backed baton, not silently keep serving the pre-sync payload) -------

test("handoffDedupKey is stable for identical content (true accidental duplicates still dedupe)", () => {
  const a = handoffDedupKey("sess-1", "same prompt text");
  const b = handoffDedupKey("sess-1", "same prompt text");
  assert.equal(a, b);
});

test("handoffDedupKey changes when the prompt content changes, even for the same session", () => {
  // Real regression this guards: agent-dispatch's `create --dedup-key K` is
  // idempotent per K -- a repeat call with the SAME key returns the existing
  // nonterminal task UNCHANGED, even with different --payload-file content.
  // Guidance tells the agent to always re-save after a post-approval sync;
  // without the content folded into the key, that re-save would be a no-op
  // against agent-dispatch and the successor would still get the stale
  // pre-sync payload.
  const before = handoffDedupKey("sess-1", "pre-sync content");
  const after = handoffDedupKey("sess-1", "post-sync content, rebased on latest main");
  assert.notEqual(before, after);
});

test("handoffDedupKey scopes different sessions to different keys even with identical content", () => {
  const a = handoffDedupKey("sess-1", "identical text");
  const b = handoffDedupKey("sess-2", "identical text");
  assert.notEqual(a, b);
});

test("handoffDedupKey embeds the session id verbatim for debuggability", () => {
  const key = handoffDedupKey("my-session-42", "content");
  assert.match(key, /^handoff-my-session-42-[0-9a-f]{12}$/);
});

// --- sanitizedGitEnv (round-10 review fix: inherited GIT_DIR/GIT_WORK_TREE/
// etc. could override `cwd` and make a safety check inspect, or the
// delegated sync mutate, a different repository than the one intended) ----

test("sanitizedGitEnv strips repository-identity variables inherited from the ambient environment", () => {
  const env = sanitizedGitEnv({
    GIT_DIR: "/somewhere/else/.git",
    GIT_WORK_TREE: "/somewhere/else",
    GIT_INDEX_FILE: "/tmp/other-index",
    GIT_COMMON_DIR: "/somewhere/else/.git-common",
    GIT_CONFIG_KEY_0: "foo",
    GIT_CONFIG_VALUE_0: "bar",
    UNRELATED_VAR: "keep-me",
  });
  assert.equal(env.GIT_DIR, undefined);
  assert.equal(env.GIT_WORK_TREE, undefined);
  assert.equal(env.GIT_INDEX_FILE, undefined);
  assert.equal(env.GIT_COMMON_DIR, undefined);
  assert.equal(env.GIT_CONFIG_KEY_0, undefined);
  assert.equal(env.GIT_CONFIG_VALUE_0, undefined);
  assert.equal(env.UNRELATED_VAR, "keep-me");
});

test("sanitizedGitEnv disables the terminal credential prompt so an unattended sync never hangs on it", () => {
  const env = sanitizedGitEnv({});
  assert.equal(env.GIT_TERMINAL_PROMPT, "0");
});

test("sanitizedGitEnv is case-insensitive to a lowercase-spelled repository-identity variable", () => {
  const env = sanitizedGitEnv({ git_dir: "/somewhere/else/.git" });
  assert.equal(env.git_dir, undefined);
});

test("listWorktreeSessions returns sessions/handoffs when agent-worktrees resolves the worktree", () => {
  const execute = (bin, argv) => {
    if (bin === "agent-worktrees" && argv[0] === "get") {
      return "wt-example";
    }
    if (bin === "agent-worktrees" && argv[0] === "list-sessions") {
      assert.deepEqual(argv, ["list-sessions", "--worktree", "wt-example", "--all-projects", "--json"]);
      return JSON.stringify({
        sessions: [{ id: "s1", is_head: true, state: "active", name: "Fix parser", created_at: "t1" }],
        handoffs: [{ predecessor: "s0", successor: "s1", token: "manual-1", linked_at: "t2" }],
      });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
  const result = listWorktreeSessions("C:\\repo", null, null, execute);
  assert.equal(result.available, true);
  assert.equal(result.worktree, "wt-example");
  assert.equal(result.sessions.length, 1);
  assert.equal(result.handoffs[0].successor, "s1");
});

test("listWorktreeSessions resolves the worktree via a known session id even when cwd has no binding", () => {
  // Real regression this guards: a project anchor (or any cwd that isn't
  // literally the session's worktree) has no cwd-inferable worktree id, but
  // agentWorktreesGet supports binding-first resolution by session id.
  const execute = (bin, argv, opts) => {
    if (bin === "agent-worktrees" && argv[0] === "get") {
      assert.equal(argv.includes("--session-id"), true);
      assert.equal(argv[argv.indexOf("--session-id") + 1], "s1");
      return "wt-example";
    }
    if (bin === "agent-worktrees" && argv[0] === "list-sessions") {
      return JSON.stringify({ sessions: [{ id: "s1" }], handoffs: [] });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")} ${JSON.stringify(opts)}`);
  };
  const result = listWorktreeSessions("C:\\anchor", null, "s1", execute);
  assert.equal(result.available, true);
  assert.equal(result.worktree, "wt-example");
});

test("listWorktreeSessions reports unavailable without guessing when no worktree id resolves", () => {
  const execute = () => "";
  const result = listWorktreeSessions("C:\\repo", null, null, execute);
  assert.equal(result.available, false);
  assert.match(result.reason, /no worktree id was resolvable/);
});

test("listWorktreeSessions reports unavailable when agent-worktrees itself fails", () => {
  const execute = (bin, argv) => {
    if (argv[0] === "get") return "wt-example";
    throw new Error("agent-worktrees not installed");
  };
  const result = listWorktreeSessions("C:\\repo", null, null, execute);
  assert.equal(result.available, false);
  assert.equal(result.worktree, "wt-example");
});

test("getPreviousSession resolves the predecessor from the recorded handoff chain", () => {
  const execute = (bin, argv) => {
    if (argv[0] === "get") return "wt-example";
    if (argv[0] === "list-sessions") {
      return JSON.stringify({
        sessions: [
          { id: "s0", is_head: false, state: "handed-off" },
          { id: "s1", is_head: true, state: "active" },
        ],
        handoffs: [{ predecessor: "s0", successor: "s1", token: "manual-1", linked_at: "t2" }],
      });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
  const result = getPreviousSession("C:\\repo", "s1", null, execute);
  assert.equal(result.available, true);
  assert.equal(result.predecessorSession, "s0");
  assert.equal(result.handoff.token, "manual-1");
});

test("getPreviousSession reports no predecessor honestly instead of guessing", () => {
  const execute = (bin, argv) => {
    if (argv[0] === "get") return "wt-example";
    if (argv[0] === "list-sessions") {
      return JSON.stringify({ sessions: [{ id: "s0" }], handoffs: [] });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
  const result = getPreviousSession("C:\\repo", "s0", null, execute);
  assert.equal(result.available, true);
  assert.equal(result.predecessorSession, null);
  assert.match(result.reason, /no recorded handoff names this session/);
});

test("getPreviousSession reports unavailable for a session not recorded on the worktree", () => {
  // Real regression this guards (PR #4570 review round 14): a mistyped id
  // or a mismatched explicit --worktree must never be conflated with a
  // genuinely-first session -- that would falsely say "it may be the
  // worktree's first session" for a session that isn't even on it.
  const execute = (bin, argv) => {
    if (argv[0] === "get") return "wt-example";
    if (argv[0] === "list-sessions") {
      return JSON.stringify({ sessions: [{ id: "s0" }], handoffs: [] });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
  const result = getPreviousSession("C:\\repo", "sess-does-not-exist", null, execute);
  assert.equal(result.available, false);
  assert.match(result.reason, /not recorded/);
});

test("getPreviousSession requires a session id", () => {
  const result = getPreviousSession("C:\\repo", null, null, () => "");
  assert.equal(result.available, false);
  assert.match(result.reason, /session id is required/);
});

// Stub for the ledger-cancellation calls (`agent-worktrees get worktree-id`
// then `agent-worktrees cancel-handoff`) abortHandoffTask/abortFileHandoff
// now make after retiring the backing handoff record.
function ledgerExecuteStub({ worktreeId = "wt-1", cancelled = true } = {}) {
  return (bin, argv) => {
    if (bin === "agent-worktrees" && argv[0] === "get") return worktreeId;
    if (bin === "agent-worktrees" && argv[0] === "cancel-handoff") {
      const token = argv[argv.indexOf("--token") + 1];
      return JSON.stringify({ cancelled, worktree_id: worktreeId, token });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
}

test("abortHandoffTask abandons the agent-dispatch task with the given reason", () => {
  const calls = [];
  const ledger = ledgerExecuteStub();
  const execute = (bin, argv) => {
    calls.push({ bin, argv });
    if (argv[0] === "show") {
      return JSON.stringify({ id: "task-42", labels: ["handoff"], source: "context-handoff", status: "queued" });
    }
    if (argv[0] === "payload") return "";
    if (bin === "agent-worktrees") return ledger(bin, argv);
    return "{}";
  };
  const result = abortHandoffTask("C:\\repo", "task-42", "no longer needed", execute);
  assert.equal(result.ok, true);
  assert.equal(result.id, "task-42");
  assert.equal(result.kind, "task");
  assert.equal(result.ledgerCancelled, true);
  assert.equal(result.ledgerNote, undefined);
  assert.deepEqual(calls, [
    { bin: "agent-dispatch", argv: ["show", "task-42"] },
    { bin: "agent-dispatch", argv: ["payload", "task-42", "--raw"] },
    // Peek: read-only, --dry-run, BEFORE the destructive abandon.
    { bin: "agent-worktrees", argv: ["get", "worktree-id"] },
    { bin: "agent-worktrees", argv: ["cancel-handoff", "--worktree-id", "wt-1", "--token", "task-42", "--dry-run"] },
    {
      bin: "agent-dispatch",
      argv: [
        "abandon", "task-42", "--permit", "--reason", "no longer needed",
        "--expected-status", "queued",
      ],
    },
    // Commit: the real cancellation, only after abandon succeeded.
    { bin: "agent-worktrees", argv: ["get", "worktree-id"] },
    { bin: "agent-worktrees", argv: ["cancel-handoff", "--worktree-id", "wt-1", "--token", "task-42"] },
  ]);
});

test("abortHandoffTask reports honestly when the ledger cancellation itself can't be confirmed", () => {
  const execute = (bin, argv) => {
    if (argv[0] === "show") {
      return JSON.stringify({ id: "task-42", labels: ["handoff"], source: "context-handoff", status: "queued" });
    }
    if (argv[0] === "payload") return "";
    if (bin === "agent-worktrees" && argv[0] === "get") return "wt-1";
    if (bin === "agent-worktrees" && argv[0] === "cancel-handoff") {
      return JSON.stringify({ cancelled: false, reason: "unknown token" });
    }
    return "{}";
  };
  const result = abortHandoffTask("C:\\repo", "task-42", null, execute);
  assert.equal(result.ok, true);
  assert.equal(result.ledgerCancelled, false);
  // Must recommend retrying cancel-handoff directly, never handoffs-check
  // --execute -- that command cannot reconcile a pre-consumption abort (PR
  // #4570 review round 5 and round 11: it only acts once a successor/
  // candidate and a recorded spawn already exist).
  assert.match(result.ledgerNote, /cancel-handoff/);
  assert.doesNotMatch(result.ledgerNote, /handoffs-check/);
  // Real regression this guards (PR #4570 review round 12): cancel-handoff's
  // own argparser registers only --token/--worktree-dir/--worktree-id, so a
  // suggested --json flag would make argparse exit before the retry could
  // even run. Check the actual backtick-quoted invocation, not the whole
  // message (which may mention "--json" only in passing prose).
  const suggestedCommand = result.ledgerNote.match(/`([^`]+)`/)[1];
  assert.doesNotMatch(suggestedCommand, /--json/);
});

test("abortHandoffTask decodes the payload for its sessionId BEFORE abandoning, since abandon can redact it", () => {
  // Real regression this guards (PR #4570 review round 9): a real
  // agent-dispatch may redact/archive a task's payload once it goes
  // terminal. Reading the payload AFTER abandon (the original ordering)
  // passed every mocked test yet would silently lose the predecessor
  // sessionId in production, making both the session-state-mark and
  // ledger-cancel follow-ups no-ops despite the task itself being retired.
  let abandoned = false;
  const marked = [];
  const execute = (bin, argv) => {
    if (argv[0] === "show") {
      return JSON.stringify({ id: "task-42", labels: ["handoff"], source: "context-handoff", status: "queued" });
    }
    if (argv[0] === "abandon") {
      abandoned = true;
      return "{}";
    }
    if (argv[0] === "payload") {
      return abandoned ? "" : encodeHandoffPayload("body", { sessionId: "predecessor-1" });
    }
    if (bin === "agent-worktrees" && argv[0] === "get") return "wt-1";
    if (bin === "agent-worktrees" && argv[0] === "cancel-handoff") {
      marked.push(argv);
      return JSON.stringify({ cancelled: true });
    }
    return "{}";
  };
  const result = abortHandoffTask("C:\\repo", "task-42", null, execute);
  assert.equal(result.ledgerCancelled, true);
  assert.deepEqual(marked, [
    // Peek (dry-run) BEFORE abandon, then the real commit AFTER it succeeds.
    ["cancel-handoff", "--worktree-id", "wt-1", "--token", "task-42", "--dry-run"],
    ["cancel-handoff", "--worktree-id", "wt-1", "--token", "task-42"],
  ]);
});

test("abortHandoffTask refuses to abandon when the ledger already has an associated candidate", () => {
  // Real regression this guards (PR #4570 review round 17): the candidate
  // guard in cancel_handoff runs too late to protect this task-backed abort
  // if fenced only after abandon -- a successor can already be associated
  // in the ledger while the dispatch task is still queued. Fencing must
  // happen BEFORE the destructive abandon, and refuse the whole abort, not
  // just the ledger half of it.
  let abandonCalled = false;
  const execute = (bin, argv) => {
    if (argv[0] === "show") {
      return JSON.stringify({ id: "task-42", labels: ["handoff"], source: "context-handoff", status: "queued" });
    }
    if (argv[0] === "payload") return "";
    if (argv[0] === "abandon") { abandonCalled = true; return "{}"; }
    if (bin === "agent-worktrees" && argv[0] === "get") return "wt-1";
    if (bin === "agent-worktrees" && argv[0] === "cancel-handoff") {
      return JSON.stringify({ cancelled: false, candidate: "successor-1" });
    }
    return "{}";
  };
  const result = abortHandoffTask("C:\\repo", "task-42", null, execute);
  assert.equal(result.ok, false);
  assert.match(result.error, /successor-1/);
  assert.equal(abandonCalled, false);
});

test("abortHandoffTask never commits the ledger cancellation when abandon itself fails", () => {
  // Real regression this guards (PR #4570 review round 18): the peek is
  // read-only, so a clean peek does not by itself prove the abandon that
  // follows will succeed -- a race consumer can still claim the task in
  // between (rejected here by --expected-status). The ledger must be left
  // untouched (no non-dry-run cancel-handoff call at all) rather than
  // committed before abandon, which would otherwise show "cancelled" (and
  // possibly restore the predecessor to head) over a task that's still
  // alive under a real successor.
  const commitCalls = [];
  const execute = (bin, argv) => {
    if (argv[0] === "show") {
      return JSON.stringify({ id: "task-42", labels: ["handoff"], source: "context-handoff", status: "queued" });
    }
    if (argv[0] === "payload") return "";
    if (argv[0] === "abandon") {
      throw new Error("agent-dispatch: expected-status mismatch (task is now claimed)");
    }
    if (bin === "agent-worktrees" && argv[0] === "get") return "wt-1";
    if (bin === "agent-worktrees" && argv[0] === "cancel-handoff") {
      if (!argv.includes("--dry-run")) commitCalls.push(argv);
      return JSON.stringify({ cancelled: false, candidate: null });
    }
    return "{}";
  };
  const result = abortHandoffTask("C:\\repo", "task-42", null, execute);
  assert.equal(result.ok, false);
  assert.match(result.error, /expected-status mismatch/);
  assert.deepEqual(commitCalls, []);
});

test("abortHandoffTask refuses to abandon a task that isn't a context-handoff handoff", () => {
  const execute = (bin, argv) => {
    if (argv[0] === "show") {
      return JSON.stringify({ id: "task-99", labels: ["unrelated"], source: "some-other-producer", status: "queued" });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
  const result = abortHandoffTask("C:\\repo", "task-99", "typo'd id", execute);
  assert.equal(result.ok, false);
  assert.match(result.error, /is not a context-handoff task/);
});

test("abortHandoffTask refuses to abandon an already-terminal task", () => {
  const execute = (bin, argv) => {
    if (argv[0] === "show") {
      return JSON.stringify({ id: "task-1", labels: ["handoff"], source: "context-handoff", status: "completed" });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
  const result = abortHandoffTask("C:\\repo", "task-1", null, execute);
  assert.equal(result.ok, false);
  assert.match(result.error, /not proposed\/queued/);
});

test("abortHandoffTask refuses to abandon a task a successor already claimed/started", () => {
  // Real regression this guards: a deferred `agent-dispatch consume
  // --defer-complete` leaves the task `started`, not terminal -- "cancel
  // before consumption" must not abandon a handoff a successor may already
  // be actively working.
  for (const status of ["claimed", "started", "suspended"]) {
    const execute = (bin, argv) => {
      if (argv[0] === "show") {
        return JSON.stringify({ id: "task-2", labels: ["handoff"], source: "context-handoff", status });
      }
      throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
    };
    const result = abortHandoffTask("C:\\repo", "task-2", null, execute);
    assert.equal(result.ok, false, `expected refusal for status ${status}`);
    assert.match(result.error, /not proposed\/queued/);
  }
});

test("abortHandoffTask degrades safe on a CLI failure", () => {
  const execute = () => { throw new Error("agent-dispatch not installed"); };
  const result = abortHandoffTask("C:\\repo", "task-42", null, execute);
  assert.equal(result.ok, false);
  assert.equal(result.kind, "task");
});

test("abortFileHandoff marks an unconsumed file handoff aborted, never claiming a fake consumer", () => {
  withTempHome(() => {
    const dir = mkdtempSync(join(tmpdir(), "context-handoff-abort-"));
    try {
      const path = join(dir, "handoff-to-abort.json");
      writeJsonAtomic(path, {
        kind: "context-handoff",
        version: 2,
        id: "handoff-to-abort",
        storage: "file",
        sessionId: "predecessor-1",
        cwd: "C:\\repo",
        title: "Continue",
        promptText: "stored markdown",
        consumed: false,
        consumedAt: null,
      });
      writeSessionStateHandoff({
        sid: "predecessor-1",
        promptText: "stored markdown",
        stored: { storage: "file", id: "handoff-to-abort" },
        seed: "seed text",
      });
      const result = abortFileHandoff(
        "C:\\repo", "aborting-session", "handoff-to-abort", path, "changed my mind",
        { execute: ledgerExecuteStub() },
      );
      assert.equal(result.ok, true);
      assert.equal(result.record.consumed, true);
      assert.equal(result.record.consumedBySession, null);
      assert.equal(result.record.aborted, true);
      assert.equal(result.record.abortedBySession, "aborting-session");
      assert.equal(result.record.abortReason, "changed my mind");
      assert.equal(result.ledgerCancelled, true);
      const onDisk = JSON.parse(readFileSync(path, "utf-8"));
      assert.equal(onDisk.aborted, true);
      // Best-effort side effect: the predecessor's own session-state marker
      // is also retired, so it stops reporting `consumed: false` forever.
      const sessionState = readSessionStateHandoff("predecessor-1");
      assert.equal(sessionState.record.consumed, true);
      assert.equal(sessionState.record.aborted, true);
      assert.equal(sessionState.record.abortReason, "changed my mind");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

test("abortFileHandoff refuses to abort when the ledger already has an associated candidate", () => {
  // Real regression this guards (PR #4570 review round 17): fencing only
  // after the destructive write would still let a successor's in-flight
  // pickup baton be destroyed. Fencing must happen BEFORE writeJsonAtomic.
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-abort-candidate-"));
  try {
    const path = join(dir, "handoff-candidate.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-candidate",
      storage: "file",
      sessionId: "predecessor-1",
      cwd: "C:\\repo",
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });
    const execute = (bin, argv) => {
      if (bin === "agent-worktrees" && argv[0] === "get") return "wt-1";
      if (bin === "agent-worktrees" && argv[0] === "cancel-handoff") {
        return JSON.stringify({ cancelled: false, candidate: "successor-1" });
      }
      throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
    };
    const result = abortFileHandoff(
      "C:\\repo", "aborting-session", "handoff-candidate", path, "changed my mind",
      { execute },
    );
    assert.equal(result.ok, false);
    assert.match(result.message, /successor-1/);
    const onDisk = JSON.parse(readFileSync(path, "utf-8"));
    assert.equal(onDisk.consumed, false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("abortFileHandoff reclaims a stale lock left by a crashed consumer instead of failing forever", () => {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-abort-stale-lock-"));
  try {
    const path = join(dir, "handoff-stale-lock.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-stale-lock",
      storage: "file",
      sessionId: "predecessor-1",
      cwd: "C:\\repo",
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });
    // A lock naming a PID that cannot possibly still be running (crashed
    // holder), the same shape consumeFileHandoffOnce's own stale-reclaim
    // test uses.
    writeFileSync(`${path}.consume.lock`, JSON.stringify({
      pid: 999999999, sessionId: "crashed-consumer", createdAt: new Date().toISOString(),
    }), "utf-8");
    const result = abortFileHandoff(
      "C:\\repo", "aborting-session", "handoff-stale-lock", path, "reclaim test",
      { execute: ledgerExecuteStub() },
    );
    assert.equal(result.ok, true);
    assert.equal(result.record.aborted, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("abortFileHandoff refuses to re-abort an already-consumed handoff", () => {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-abort-consumed-"));
  try {
    const path = join(dir, "handoff-already-consumed.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-already-consumed",
      storage: "file",
      sessionId: "predecessor-1",
      cwd: "C:\\repo",
      promptText: "stored markdown",
      consumed: true,
      consumedAt: "2026-01-01T00:00:00Z",
      consumedBySession: "successor-1",
    });
    const result = abortFileHandoff(
      "C:\\repo", "aborting-session", "handoff-already-consumed", path, "too late",
    );
    assert.equal(result.ok, false);
    assert.match(result.message, /already consumed by session `successor-1`/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("abortFileHandoff reports not-found honestly for a nonexistent handoff", () => {
  const result = abortFileHandoff(
    "C:\\repo", "aborting-session", "handoff-does-not-exist", null, null,
  );
  assert.equal(result.ok, false);
  assert.match(result.message, /was not found/);
});

test("abortFileHandoff refuses to abort while a real consume holds the lock (never races it)", () => {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-abort-race-"));
  try {
    const path = join(dir, "handoff-in-flight.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-in-flight",
      storage: "file",
      sessionId: "predecessor-1",
      cwd: "C:\\repo",
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });
    // Simulate a real consumeFileHandoffOnce actively holding the lock.
    writeFileSync(`${path}.consume.lock`, JSON.stringify({
      pid: process.pid, sessionId: "real-consumer", createdAt: new Date().toISOString(),
    }), "utf-8");
    const result = abortFileHandoff(
      "C:\\repo", "aborting-session", "handoff-in-flight", path, "too slow",
    );
    assert.equal(result.ok, false);
    assert.match(result.message, /already locked/);
    // The abort attempt must not have deleted the real consumer's lock.
    assert.ok(existsSync(`${path}.consume.lock`));
    const onDisk = JSON.parse(readFileSync(path, "utf-8"));
    assert.equal(onDisk.consumed, false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("an aborted file handoff can never be recovered/retriggered again", () => {
  // Real regression this guards (PR #4570 review round 13): marking a file
  // handoff consumed/aborted did not stop recoverStoredHandoff's explicit-
  // token path from selecting it again -- loadStoredFileHandoff returned any
  // readable record with no consumed/aborted check, so `trigger
  // --handoff-token <aborted-id>` would rewrite a fresh unconsumed session
  // request for a token abort was supposed to make permanently non-reusable.
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-abort-reuse-"));
  try {
    const path = join(dir, "handoff-reuse.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      version: 2,
      id: "handoff-reuse",
      storage: "file",
      sessionId: "predecessor-1",
      cwd: "C:\\repo",
      promptText: "stored markdown",
      consumed: false,
      consumedAt: null,
    });
    const aborted = abortFileHandoff(
      "C:\\repo", "aborting-session", "handoff-reuse", path, "changed my mind",
      { execute: ledgerExecuteStub() },
    );
    assert.equal(aborted.ok, true);

    const recovered = recoverStoredHandoff("C:\\repo", "predecessor-1", "handoff-reuse", path);
    assert.equal(recovered, null);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("an abandoned task-backed handoff can never be recovered/retriggered again", () => {
  // Real regression this guards (PR #4570 review round 14): abandoning a
  // task does not make its payload unreadable -- agent-dispatch's payload
  // endpoint resolves payloads without checking task status -- so
  // loadStoredTaskHandoff must check the task's own status itself, or
  // `trigger --handoff-token <aborted-task>` could recreate an unconsumed
  // session-state request and pending ledger entry for a task abort just
  // retired.
  const execute = (bin, argv) => {
    if (argv[0] === "payload") {
      return encodeHandoffPayload("body", { sessionId: "predecessor-1" });
    }
    if (argv[0] === "show") {
      return JSON.stringify({ id: "task-reuse", status: "abandoned" });
    }
    throw new Error(`unexpected CLI call: ${bin} ${argv.join(" ")}`);
  };
  const recovered = recoverStoredHandoff(
    "C:\\repo", "predecessor-1", "task-reuse", null, {}, execute,
  );
  assert.equal(recovered, null);
});

test("dispatchTaskConsumed does not treat an abandoned (aborted) task as picked up", () => {
  // Real regression this guards (PR #4570 review round 7): abandoning a task
  // via abort is indistinguishable from a genuine successor pickup unless
  // "abandoned" is excluded -- otherwise triggerHandoff()'s bounded wait
  // could observe an in-flight abort mid-race and report pickup
  // acknowledged, suppressing manual fallback even though nobody consumed
  // anything.
  const execute = () => JSON.stringify({ id: "task-1", status: "abandoned" });
  const result = dispatchTaskConsumed("C:\\repo", "task-1", execute);
  assert.equal(result.consumed, false);
});

test("dispatchTaskConsumed still reports a genuine pickup as consumed", () => {
  const execute = () => JSON.stringify({ id: "task-1", status: "claimed" });
  const result = dispatchTaskConsumed("C:\\repo", "task-1", execute);
  assert.equal(result.consumed, true);
});

test("pickupSignals excludes a session-state record an abort marked aborted", () => {
  // Real regression this guards (PR #4570 review round 7): abortHandoffTask/
  // abortFileHandoff both set consumed:true on the session-state marker
  // (markSessionStateHandoffConsumed) purely to stop a later lookup
  // reporting a stale consumed:false -- pickupSignals must not read that as
  // a genuine successor pickup.
  withTempHome(() => {
    writeSessionStateHandoff({
      sid: "predecessor-1",
      promptText: "stored markdown",
      stored: { storage: "agent-dispatch", id: "task-1" },
      seed: "seed text",
    });
    markSessionStateHandoffConsumed("predecessor-1", {
      handoffId: "task-1", aborted: true, abortReason: "changed my mind",
    });
    const execute = () => JSON.stringify({ id: "task-1", status: "abandoned" });
    const stored = { storage: "agent-dispatch", id: "task-1", metadata: {} };
    const result = pickupSignals("C:\\repo", "predecessor-1", stored, null, execute);
    assert.equal(result.pickedUp, false);
    assert.equal(result.sessionState.consumed, false);
    assert.deepEqual(result.via, []);
  });
});
