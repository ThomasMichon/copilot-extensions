import { test } from "node:test";
import assert from "node:assert/strict";
import { join, dirname } from "node:path";
import {
  mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, unlinkSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { execFileSync, spawnSync } from "node:child_process";

import {
  HANDOFF_META_PREFIX,
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
  resolveRuntimePython,
  retryStoredHandoffCutover,
  resolveSystemCli,
  runtimeEnvironment,
  safePathSegment,
  sanitizedGitEnv,
  sessionBindingForSession,
  triggerHandoff,
  writeJsonAtomic,
  writeSessionStateHandoff,
  readSessionStateHandoff,
  markSessionStateHandoffConsumed,
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
  assert.match(text, /Copy only the following short handoff prompt\/seed/);
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

test("buildResumePrompt keeps deferred completion explicit", () => {
  const prompt = buildResumePrompt(
    "full brief",
    "agent-dispatch task",
    { deferredTaskId: "task-42" },
  );
  assert.match(prompt, /Keep agent-dispatch task task-42 owned/);
  assert.match(prompt, /Only after the handoff objective's completion gate is met run: agent-dispatch complete task-42/);
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
  // Neither live-cutover trigger point was ever invoked -- not the activity
  // event agent-worktrees' resident monitor primarily watches for, not the
  // agent-bridge ping, and -- critically -- not `noteHandoff` either: it
  // shells to `agent-worktrees note-handoff`, which calls `open_handoff()`
  // and creates a `pending_handoffs` entry the monitor can independently
  // discover and claim via its OWN session-state-file fallback path, with
  // no activity event required at all (the High-severity gap a Copilot
  // review caught on PR #3041 -- gating only the activity event/bridge
  // ping left this second path wide open). Only a single pickup-status
  // check, no polling loop, no sleep.
  assert.deepEqual(calls.map(([name]) => name), ["signals"]);
  assert.equal(result.worktreeSignal.noted, false);
  assert.equal(result.worktreeSignal.activity.logged, false);
  assert.equal(result.bridge.attempted, false);
  assert.match(
    result.manualInstructions,
    /Automatic cutover is disabled.*mode.*is not `auto`/s,
  );
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

// --- attemptWorktreeSync (Phase 7: force-tier auto-handoffs must not hand a
// stale worktree to their successor, but MUST never auto-commit anything
// since no agent judgment is in this path -- see the review that landed this
// function). Uses a real throwaway git repo; agent-worktrees is not expected
// to resolve in a bare `node --test` environment, so these exercise the
// "sync attempted but unavailable" outcome rather than a real sync.

function initGitRepo() {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-sync-"));
  execFileSync("git", ["init", "-q"], { cwd: dir });
  execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: dir });
  execFileSync("git", ["config", "user.name", "Test"], { cwd: dir });
  writeFileSync(join(dir, "file.txt"), "one\n");
  execFileSync("git", ["add", "."], { cwd: dir });
  execFileSync("git", ["commit", "-q", "-m", "initial"], { cwd: dir });
  return dir;
}

test("attemptWorktreeSync skips (never commits) when the tree is dirty", async () => {
  const dir = initGitRepo();
  try {
    writeFileSync(join(dir, "file.txt"), "one\nuncommitted\n");
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /uncommitted, untracked, or ignored content/);
    // Never auto-commits: the dirty change must still be present, uncommitted.
    const status = execFileSync("git", ["status", "--porcelain"], { cwd: dir, encoding: "utf-8" });
    assert.notEqual(status.trim(), "");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync skips a worktree with an in-progress rebase, even when the tree reports clean", async () => {
  // Real regression this guards: a rebase paused at a clean step (e.g.
  // "edit") reports an empty `git status --porcelain`, so the dirty-tree
  // check alone would let this through -- and agent-worktrees' own sync
  // helper aborts a failed rebase on its failure path, which could cancel a
  // rebase this session never started.
  const dir = initGitRepo();
  try {
    const gitDirRaw = execFileSync("git", ["rev-parse", "--git-dir"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const gitDir = join(dir, gitDirRaw);
    mkdirSync(join(gitDir, "rebase-merge"), { recursive: true });
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /rebase is already in progress/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync reports not-a-git-checkout for a plain directory", async () => {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-nosync-"));
  try {
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    // The worktree sync lock is now acquired BEFORE the dirty-tree check (a
    // review finding: checking cleanliness before the lock left a window
    // for a file to become dirty between the check and the locked sync),
    // so a non-git directory now fails at lock-path resolution first.
    assert.match(result.reason, /not a git checkout|git unavailable|could not resolve a lock path/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync attempts a sync on a clean tree and reports failure honestly when agent-worktrees is unavailable", async () => {
  const dir = initGitRepo();
  try {
    const result = await attemptWorktreeSync(dir);
    // A clean tree means the (never-commits) safety gate passes and a real
    // sync attempt is made. resolveSystemCliDescriptor resolves against the
    // real on-disk sibling agent-worktrees plugin in this monorepo
    // checkout, so it genuinely invokes agent-worktrees here -- which fails
    // honestly (this throwaway repo isn't an adopted agent-worktrees
    // project) rather than silently reporting success. A payload-only
    // installation without that sibling instead falls back to plainGitSync
    // (tested directly, below).
    assert.equal(result.attempted, true);
    assert.equal(result.synced, false);
    assert.match(result.reason, /sync failed|unavailable/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("plainGitSync actually syncs when a real origin remote is configured", async () => {
  // Real regression this guards: a payload-only context-handoff install (no
  // sibling agent-worktrees payload) must still get a working sync, not
  // just an honest failure -- exercises the fallback's full fetch+rebase
  // path against a real local "remote". Tested directly (not via
  // attemptWorktreeSync): resolveSystemCliDescriptor resolves against the
  // real on-disk sibling agent-worktrees plugin in this monorepo checkout,
  // so the fallback can't be reached end-to-end here without genuinely
  // uninstalling that sibling.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: clone });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: clone });
    // Advance the "remote" (origin) by one commit the clone doesn't have yet.
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    const result = await plainGitSync(clone);
    assert.equal(result.attempted, true);
    assert.equal(result.synced, true, JSON.stringify(result));
    assert.equal(
      readFileSync(join(clone, "file.txt"), "utf-8").replace(/\r/g, "").trim(), "one\ntwo",
    );
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("plainGitSync disables rebase.autoStash so a residual dirty-tree race fails instead of silently stashing", () => {
  // Real regression this guards: the dirty-tree recheck immediately before
  // this rebase narrows but cannot fully close the TOCTOU (a file could
  // still become dirty in the instant between that check and this exec) --
  // a repository or user `rebase.autoStash=true` config would otherwise let
  // git silently stash/pop that content instead of failing, contrary to
  // the whole clean-tree safety gate's intent. Structural check (the
  // autostash-vs-dirty-at-exact-exec-instant race isn't reliably
  // forceable in a fast unit test): the rebase invocation must pass
  // --no-autostash.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const plainGitSyncBody = source.slice(
    source.indexOf("export async function plainGitSync("),
    source.indexOf("// sync exec. Split out"),
  );
  assert.ok(plainGitSyncBody.length > 0, "could not locate plainGitSync's body");
  assert.match(plainGitSyncBody, /"rebase", "--no-autostash"/);
});

test("plainGitSync disables repository hooks so a client-side pre-rebase guard cannot block or mutate it", async () => {
  // Real regression this guards: mirrors agent-worktrees' own
  // core.hooksPath convention for trusted, mechanical plumbing -- a repo's
  // client-side pre-rebase hook must not be able to interfere with this
  // unattended sync. Behavioral proof: a pre-rebase hook that would abort
  // the rebase (and leave a marker file if it ran) must never fire.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: clone });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: clone });
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    const gitDirRaw = execFileSync("git", ["rev-parse", "--git-dir"], {
      cwd: clone, encoding: "utf-8",
    }).trim();
    const hooksDir = join(clone, gitDirRaw, "hooks");
    mkdirSync(hooksDir, { recursive: true });
    const markerPath = join(hooksDir, "pre-rebase-ran.marker");
    const hookPath = join(hooksDir, "pre-rebase");
    writeFileSync(hookPath, `#!/bin/sh\ntouch "${markerPath.replace(/\\/g, "/")}"\nexit 1\n`);
    try { execFileSync("chmod", ["+x", hookPath]); } catch { /* not needed on Windows */ }
    const result = await plainGitSync(clone);
    assert.equal(result.synced, true, JSON.stringify(result));
    assert.equal(existsSync(markerPath), false, "the pre-rebase hook must never have run");
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("plainGitSync never rebases a detached HEAD checkout", async () => {
  // Real regression this guards: agent-worktrees' own managed sync
  // explicitly skips a detached worktree, but `git rebase origin/<branch>`
  // is itself perfectly valid while detached -- it just moves the detached
  // HEAD, not any branch, which is not what a "sync onto the default
  // branch" caller expects and can leave commits unreachable once HEAD
  // moves again. This fallback must match that skip, not just mirror the
  // conflict-safety contract.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    const headBefore = execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: clone, encoding: "utf-8",
    }).trim();
    execFileSync("git", ["checkout", "-q", "--detach", headBefore], { cwd: clone });
    // Advance the "remote" so a real rebase, if attempted, would have
    // something to move onto.
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    const result = await plainGitSync(clone);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /detached/);
    assert.equal(
      execFileSync("git", ["rev-parse", "HEAD"], { cwd: clone, encoding: "utf-8" }).trim(),
      headBefore,
    );
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("plainGitSync fails honestly when no origin remote exists to determine a default branch from", async () => {
  const dir = initGitRepo();
  try {
    const result = await plainGitSync(dir);
    assert.equal(result.attempted, true);
    assert.equal(result.synced, false);
    assert.match(result.reason, /default branch/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("plainGitSync queries the remote directly for its default branch, not a stale local origin/HEAD cache", async () => {
  // Real regression this guards: the local `origin/HEAD` symref is a cache
  // set at clone time -- it can remain pointed at the remote's OLD default
  // branch after the remote renames/changes it, silently fetching/rebasing
  // onto the wrong branch while reporting a successful sync. Renames the
  // "remote"'s default branch after cloning (so the clone's cached
  // origin/HEAD still points at the old name) and confirms the sync
  // correctly follows the NEW name via a direct remote query.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: clone });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: clone });
    const cachedDefault = execFileSync(
      "git", ["rev-parse", "--abbrev-ref", "origin/HEAD"], { cwd: clone, encoding: "utf-8" },
    ).trim().replace(/^origin\//, "");
    // Rename the remote's default branch and advance it -- the clone's
    // cached origin/HEAD still points at the OLD name.
    execFileSync("git", ["branch", "-m", cachedDefault, "renamed-default"], { cwd: origin });
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    const staleCache = execFileSync(
      "git", ["rev-parse", "--abbrev-ref", "origin/HEAD"], { cwd: clone, encoding: "utf-8" },
    ).trim();
    assert.match(staleCache, new RegExp(cachedDefault), "test fixture's cached origin/HEAD did not stay stale as expected");
    const result = await plainGitSync(clone);
    assert.equal(result.synced, true, JSON.stringify(result));
    assert.equal(
      readFileSync(join(clone, "file.txt"), "utf-8").replace(/\r/g, "").trim(), "one\ntwo",
    );
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("plainGitSync rechecks for an in-progress rebase immediately before its own rebase exec, and never aborts it", async () => {
  // Real regression this guards: the remote-discovery + fetch awaits before
  // the rebase exec are exactly the kind of gap another process could start
  // a rebase in; without a recheck immediately before the rebase, this
  // fallback's catch-all `git rebase --abort` could cancel a rebase it did
  // not start.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    const gitDirRaw = execFileSync("git", ["rev-parse", "--git-dir"], {
      cwd: clone, encoding: "utf-8",
    }).trim();
    const rebaseMergeDir = join(clone, gitDirRaw, "rebase-merge");
    mkdirSync(rebaseMergeDir, { recursive: true });
    const result = await plainGitSync(clone);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /rebase started|in-progress rebase/);
    // The pre-existing rebase state must still be there, untouched --
    // proves the fallback never reached (or aborted via) `git rebase`.
    assert.equal(existsSync(rebaseMergeDir), true);
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync never blocks the event loop before its first await", async () => {
  // Real regression this guards: attemptWorktreeSync is invoked synchronously
  // at the top of autoForceHandoff, before that function's first await, from
  // a fire-and-forget call site (session.usage_info never awaits
  // autoForceHandoff). If attemptWorktreeSync performed any synchronous
  // (execFileSync-style) child-process work before its own first await, the
  // CALLING statement itself would not return control until that work
  // finished -- proving it merely "returns a Promise" is not sufficient,
  // since every async function eventually does that regardless of what ran
  // synchronously first. Measuring how long the bare call expression itself
  // takes to return (without awaiting the settled result) is the real test:
  // a truly async implementation returns near-instantly; a blocking one
  // takes as long as the underlying git/CLI work.
  const dir = initGitRepo();
  try {
    const start = Date.now();
    const syncPromise = attemptWorktreeSync(dir);
    const callElapsedMs = Date.now() - start;
    assert.ok(
      callElapsedMs < 50,
      `expected the call itself to return near-instantly, took ${callElapsedMs}ms`,
    );
    await syncPromise; // let the real work finish before cleanup
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync detects dirty state hidden by status.showUntrackedFiles=no", async () => {
  // Real regression this guards: plain `git status --porcelain` (no
  // --untracked-files=all) can be silently suppressed for untracked files by
  // a LOCAL repo config (`status.showUntrackedFiles=no`), reporting "clean"
  // even though an untracked file -- e.g. an ignored/secret-adjacent file
  // that was never staged -- exists and could later collide with content the
  // sync brings in. --untracked-files=all overrides that local config.
  const dir = initGitRepo();
  try {
    execFileSync(
      "git", ["config", "status.showUntrackedFiles", "no"], { cwd: dir },
    );
    writeFileSync(join(dir, "untracked-secret.txt"), "shh\n");
    // Sanity check: bare porcelain really is fooled by this config, so the
    // fix is meaningfully exercised rather than trivially true either way.
    const bareStatus = execFileSync(
      "git", ["status", "--porcelain"], { cwd: dir, encoding: "utf-8" },
    );
    assert.equal(bareStatus.trim(), "", "config fixture did not suppress untracked files as expected");
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /uncommitted, untracked, or ignored content/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync skips when only an ignored file is present (plain --porcelain would miss it)", async () => {
  // Real regression this guards: bare `git status --porcelain` omits ignored
  // files entirely -- an ignored/secret-adjacent file could still collide
  // with content the sync brings in. --ignored closes that gap; this
  // deliberately over-rejects (an ordinary ignored build artifact also
  // blocks a sync attempt), consistent with this function's documented
  // fail-closed philosophy for any other uncommitted state.
  const dir = initGitRepo();
  try {
    writeFileSync(join(dir, ".gitignore"), "ignored-secret.txt\n");
    execFileSync("git", ["add", ".gitignore"], { cwd: dir });
    execFileSync("git", ["commit", "-q", "-m", "add gitignore"], { cwd: dir });
    writeFileSync(join(dir, "ignored-secret.txt"), "shh\n");
    const bareStatus = execFileSync(
      "git", ["status", "--porcelain"], { cwd: dir, encoding: "utf-8" },
    );
    assert.equal(bareStatus.trim(), "", "ignored file unexpectedly visible to bare porcelain");
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /uncommitted, untracked, or ignored content/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync skips when another sync attempt already holds the worktree lock", async () => {
  // Real regression this guards: the rebase-check-then-sync window is not
  // atomic against a concurrent invocation of THIS SAME function (e.g. the
  // force-tier and skill-guided paths racing on one worktree) -- the lock
  // closes that specific, realistic race by serializing this plugin's own
  // concurrent attempts, even though it cannot compel an unrelated external
  // actor to honor it.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, "");
    try {
      const result = await attemptWorktreeSync(dir);
      assert.equal(result.attempted, false);
      assert.equal(result.synced, false);
      assert.match(result.reason, /already in progress/);
    } finally {
      unlinkSync(lockPath);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

function deadPid() {
  // A PID guaranteed to no longer be running: spawn a trivial child and
  // wait for it to exit (spawnSync blocks until then), then reuse its now-
  // terminated pid -- more reliable than guessing an arbitrary unused
  // number, which could in rare cases collide with a real live process.
  const result = spawnSync(process.execPath, ["-e", ""]);
  return result.pid;
}

test("attemptWorktreeSync reclaims a stale worktree lock left by a crashed process", async () => {
  // Real regression this guards: if the process is killed after acquiring
  // the lock (openSync succeeds) but before the finally block runs (e.g.
  // mid-fetch), the lock file would otherwise remain forever, permanently
  // skipping every future sync for this worktree. A lock recording a
  // confirmed-dead holder pid must be treated as abandoned and reclaimed
  // rather than honored forever.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, `${deadPid()}-0-deadholder`);
    const result = await attemptWorktreeSync(dir);
    // Reclaimed and proceeded past the lock -- this throwaway repo isn't an
    // adopted agent-worktrees project, so the actual sync attempt fails
    // honestly for that reason, proving only that the lock did NOT block
    // this attempt.
    assert.equal(result.attempted, true);
    assert.notEqual(result.reason, "another sync attempt for this worktree is already in progress; automatic sync was skipped");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("stale-lock reclaim uses an atomic rename-away, not unlink+create", () => {
  // Real regression this guards: an unconditional unlink+create reclaim is
  // NOT concurrency-safe -- two processes racing to reclaim the SAME stale
  // lock could each pass the staleness check, then one process's unlink
  // could delete the OTHER's freshly-created replacement lock, letting both
  // acquire and run concurrently (defeating the entire point of the lock).
  // `renameSync` on the source path is the only step here the OS actually
  // guarantees single-winner semantics for: only one racing renamer can
  // ever succeed, since the source stops existing the instant the first one
  // wins. Structural check (no DI seam for the internal fs calls):
  // acquireLock's reclaim path must use renameSync, not a bare unlinkSync,
  // to hand off the stale lock's path.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const body = source.slice(
    source.indexOf("function acquireLock("),
    source.indexOf("async function withWorktreeSyncLock("),
  );
  assert.ok(body.length > 0, "could not locate acquireLock's body");
  assert.match(body, /renameSync\(lockPath,/);
});

test("attemptWorktreeSync exactly one of several concurrent attempts proceeds past a stale lock", async () => {
  // Best-effort concurrency integration check (real fs timing, not a forced
  // interleave): fires several real attemptWorktreeSync calls at the same
  // stale lock at once. Exactly one may ever get past the lock (report
  // something other than "already in progress"); the rest must correctly
  // back off, never all "winning" simultaneously.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, `${deadPid()}-0-deadholder`);
    const results = await Promise.all(
      Array.from({ length: 5 }, () => attemptWorktreeSync(dir)),
    );
    const proceeded = results.filter(
      (r) => r.reason !== "another sync attempt for this worktree is already in progress; automatic sync was skipped",
    );
    assert.equal(proceeded.length, 1, `expected exactly one winner, got ${proceeded.length}: ${JSON.stringify(results)}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("withWorktreeSyncLock's release only removes the lock when its content still matches this invocation's own token", async () => {
  // Real regression this guards: if a holder runs longer than
  // STALE_LOCK_MS (suspended, an extremely slow network -- not necessarily
  // crashed) and another invocation reclaims the path as stale while the
  // first is still running, the first invocation eventually resumes and
  // hits its own release step. An unconditional unlink-by-path there would
  // delete the SECOND invocation's active lock, letting a THIRD invocation
  // acquire while the second is still mid-sync. Simulated directly (no DI
  // seam for withWorktreeSyncLock itself): after a real acquire+release
  // cycle completes normally, forge a lock file at the SAME path with
  // different content (as if a reclaimer had taken over), then confirm a
  // second real acquire+release cycle -- which would naturally try to
  // remove whatever it finds at that path if it used path-only ownership
  // -- leaves foreign content alone when it doesn't reacquire that exact
  // path. This exercises the same lock path lifecycle attemptWorktreeSync
  // uses end to end.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    // A foreign, currently-live lock (fresh mtime -- not stale) belonging
    // to some OTHER invocation, sitting at the shared path.
    writeFileSync(lockPath, "someone-elses-token");
    const result = await attemptWorktreeSync(dir);
    // Contention -- this invocation never acquired the lock at all, so it
    // must not have touched the foreign content in any way.
    assert.equal(result.attempted, false);
    assert.match(result.reason, /already in progress/);
    assert.equal(readFileSync(lockPath, "utf-8"), "someone-elses-token");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("acquireLock returns an ownership token, and releaseLock atomically claims the path before comparing it", () => {
  // Structural check (no DI seam for the internal fs calls, and reliably
  // forcing the real suspended-holder/concurrent-reclaimer timing scenarios
  // isn't practical in a fast unit test): acquireLock must return a token
  // alongside the fd, and releaseLock must claim the lock path via
  // renameSync (atomic single-winner semantics, same as acquireLock's own
  // reclaim step) BEFORE reading/comparing its content -- a separate
  // read-then-unlink is itself a TOCTOU another invocation's reclaim could
  // land inside.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const acquireBody = source.slice(
    source.indexOf("function acquireLock("),
    source.indexOf("async function withWorktreeSyncLock("),
  );
  assert.match(acquireBody, /token/);
  const releaseBody = source.slice(
    source.indexOf("function releaseLock("),
    source.indexOf("// Async-only twin of resolveRuntimePython"),
  );
  assert.ok(releaseBody.length > 0, "could not locate releaseLock's body");
  const claimIndex = releaseBody.indexOf("renameSync(lockPath, claimedPath)");
  const readIndex = releaseBody.indexOf("readFileSync(claimedPath");
  assert.ok(claimIndex >= 0, "expected releaseLock to claim the path via renameSync");
  assert.ok(readIndex >= 0, "expected releaseLock to read the claimed content");
  assert.ok(claimIndex < readIndex, "expected the atomic claim (rename) to happen BEFORE the content read");
});

test("releaseLock restores a foreign lock via linkSync (no-clobber), never renameSync (which silently overwrites)", () => {
  // Real regression this guards: on POSIX, renameSync REPLACES an existing
  // destination -- restoring a foreign claimed lock with renameSync could
  // silently overwrite a FRESH lock a third invocation created at lockPath
  // in the interim since this invocation's claim, defeating serialization
  // entirely (the exact outcome this whole lock exists to prevent).
  // linkSync fails with EEXIST if the destination already exists, so it
  // only ever restores into a genuinely empty slot. Structural check (the
  // exact concurrent timing isn't reliably forceable in a fast unit test).
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const releaseBody = source.slice(
    source.indexOf("function releaseLock("),
    source.indexOf("// Async-only twin of resolveRuntimePython"),
  );
  assert.ok(releaseBody.length > 0, "could not locate releaseLock's body");
  assert.match(releaseBody, /linkSync\(claimedPath,\s*lockPath\)/);
  assert.doesNotMatch(releaseBody, /renameSync\(claimedPath,\s*lockPath\)/);
});

test("attemptWorktreeSyncLocked disables rebase.autoStash and repo hooks for the delegated agent-worktrees sync too", () => {
  // Real regression this guards: plainGitSync's own rebase passes
  // --no-autostash directly, but the AGENT-WORKTREES-delegated path has no
  // such flag of its own -- its `is_clean` check uses bare `git status
  // --porcelain` and its rebase does not disable autostash, so content
  // created in the runtime-resolution gap before the delegated exec could
  // be silently stashed/rebased despite this file's own dirty recheck.
  // Repo hooks (a client-side pre-rebase guard) must also not be able to
  // block or mutate this unattended sync, matching agent-worktrees' own
  // established core.hooksPath convention. Structural check: both delegated
  // exec branches must pass env through withUnattendedSyncGitConfig.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const helperBody = source.slice(
    source.indexOf("function withUnattendedSyncGitConfig("),
    source.indexOf("function rebaseInProgress("),
  );
  assert.match(helperBody, /rebase\.autoStash/);
  assert.match(helperBody, /core\.hooksPath/);
  const lockedBody = source.slice(
    source.indexOf("async function attemptWorktreeSyncLocked("),
    source.indexOf("// True if an agent-dispatch coordinator"),
  );
  const callCount = (lockedBody.match(/withUnattendedSyncGitConfig\(/g) || []).length;
  assert.ok(
    callCount >= 2,
    `expected withUnattendedSyncGitConfig to wrap env for both delegated exec branches, saw ${callCount} call(s)`,
  );
});

test("attemptWorktreeSync never reclaims a lock recording a genuinely LIVE holder, no matter its age", async () => {
  // Real regression this guards (the round-16-through-20 root cause): a
  // reclaim scheme based purely on age can reclaim a holder that is merely
  // slow (a suspended process, a very slow network) but still very much
  // alive -- which is exactly what forces the whole chain of release-side
  // races those rounds kept surfacing. Gating reclaim on confirmed
  // liveness instead means a live holder is NEVER reclaimed, however old
  // its lock file is.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    // This test process's own pid is unambiguously alive.
    writeFileSync(lockPath, `${process.pid}-0-liveholder`);
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /already in progress/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("acquireLock treats a failed stat on lock contention as contention, not as reclaimable", () => {
  // Real regression this guards: if statSync throws for a reason OTHER
  // than "the lock is genuinely gone" (a permission error, a transient FS
  // hiccup, or another process recreating it in the exact instant between
  // the failed open and this stat), treating that ambiguity as reclaimable
  // would let this process unlink an ACTIVE replacement lock another
  // process just created, letting both run concurrently. Structural check
  // (no DI seam for the internal fs calls): the stat's catch block must
  // rethrow/fail closed, never fall through to an unconditional reclaim.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const body = source.slice(
    source.indexOf("function acquireLock("),
    source.indexOf("async function withWorktreeSyncLock("),
  );
  assert.ok(body.length > 0, "could not locate acquireLock's body");
  const statCatchBody = body.slice(
    body.indexOf("} catch {", body.indexOf("statSync(lockPath)")),
  );
  assert.match(statCatchBody.slice(0, statCatchBody.indexOf("}", 10) + 1), /throw error/);
});

test("attemptWorktreeSync rechecks for an in-progress rebase immediately before the sync exec (TOCTOU narrowing)", () => {
  // The initial dirty/rebase checks and the actual sync exec are not atomic
  // (another process could start a rebase in between) -- this cannot be
  // closed without a cross-process lock this plugin does not own, so the
  // mitigation is a second, as-late-as-possible recheck right before each
  // sync exec call. Structural check (no DI seam for the internal git
  // calls): rebaseInProgress must be invoked more than once in
  // attemptWorktreeSync's body.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const body = source.slice(
    source.indexOf("export async function attemptWorktreeSync("),
    source.indexOf("// True if an agent-dispatch coordinator"),
  );
  assert.ok(body.length > 0, "could not locate attemptWorktreeSync's body");
  const callCount = (body.match(/rebaseInProgress\(cwd\)/g) || []).length;
  assert.ok(
    callCount >= 2,
    `expected rebaseInProgress to be rechecked before the sync exec, saw ${callCount} call(s)`,
  );
});

test("attemptWorktreeSync's dirty-tree check runs AFTER the worktree lock is held, not before", async () => {
  // Real regression this guards: checking cleanliness before acquiring the
  // lock left a window where a file could become dirty between the check
  // and the locked sync actually running. Proven by holding the lock first
  // (simulating a concurrent attempt) and confirming the OUTCOME is "lock
  // busy", not "dirty tree" or a real sync attempt -- if the dirty check
  // still ran before the lock, this would instead report the tree as clean
  // and proceed to a real (lock-blocked) attempt with a different reason.
  const dir = initGitRepo();
  try {
    writeFileSync(join(dir, "file.txt"), "one\nuncommitted\n");
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, "");
    try {
      const result = await attemptWorktreeSync(dir);
      assert.equal(result.attempted, false);
      assert.match(result.reason, /already in progress/);
    } finally {
      unlinkSync(lockPath);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("plainGitSync rechecks dirtiness immediately before its own rebase exec (rebase.autoStash safety)", async () => {
  // Real regression this guards: the fetch above this recheck is another
  // gap where a file could become dirty, and a local rebase.autoStash
  // config would otherwise let `git rebase` silently stash/pop that
  // content instead of refusing to run.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: clone });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: clone });
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    writeFileSync(join(clone, "dirty.txt"), "uncommitted\n");
    const result = await plainGitSync(clone);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /autoStash|dirty/);
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("resolveRuntimePython with allowProvision:false fails fast instead of risking the 180s provisioning wait", () => {
  // Real regression this guards: attemptWorktreeSync's runCli call is
  // fire-and-forget from session.usage_info, before any await -- if an
  // agent-worktrees runtime were not yet provisioned, resolveRuntimePython's
  // provisioning branch could block the event loop for up to
  // RUNTIME_PROVISION_TIMEOUT_MS (180s). allowProvision:false must skip that
  // branch entirely and fail immediately instead.
  const fakePluginRoot = mkdtempSync(join(tmpdir(), "context-handoff-fakert-"));
  try {
    const scriptsDir = join(fakePluginRoot, "scripts");
    mkdirSync(scriptsDir, { recursive: true });
    // Deliberately resolves nothing (simulates an unprovisioned runtime)
    // without erroring, so resolve() returns an empty string rather than
    // throwing for an unrelated reason. Both platform scripts are written so
    // this test is meaningful on POSIX CI runners too, not just Windows.
    writeFileSync(
      join(scriptsDir, "resolve-runtime.ps1"),
      "$AgentRtPy = $null\n",
    );
    writeFileSync(
      join(scriptsDir, "resolve-runtime.sh"),
      "AGENT_RT_PY=\n",
    );
    const resolved = {
      path: join(fakePluginRoot, "fake-cli.ps1"),
      pluginRoot: fakePluginRoot,
      module: "fake_module",
      runtimeRoot: ".fake-runtime-root-that-does-not-exist",
      payloadRootEnv: null,
    };
    const env = {
      ...process.env,
      COPILOT_PLUGIN_ROOT: fakePluginRoot,
    };
    const start = Date.now();
    assert.throws(
      () => resolveRuntimePython(resolved, env, fakePluginRoot, 5000, false),
      /provisioning skipped/,
    );
    const elapsedMs = Date.now() - start;
    // Generous upper bound: real provisioning waits up to 180_000ms: this
    // must be nowhere close to that if allowProvision:false is honored.
    assert.ok(elapsedMs < 10_000, `expected a fast failure, took ${elapsedMs}ms`);
  } finally {
    rmSync(fakePluginRoot, { recursive: true, force: true });
  }
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
