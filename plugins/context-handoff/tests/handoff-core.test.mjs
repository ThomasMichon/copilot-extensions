import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import {
  mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";

import {
  HANDOFF_META_PREFIX,
  agentWorktreesGetResult,
  buildResumePrompt,
  buildSeedForStored,
  consumeDispatchHandoffTask,
  consumeFileHandoff,
  decodeHandoffPayload,
  encodeHandoffPayload,
  formatConsumeResult,
  isolatedPythonArgs,
  manualFallbackInstructions,
  normalizeHandoffTitle,
  runtimeEnvironment,
  safePathSegment,
  sessionBindingForSession,
  triggerHandoff,
  writeJsonAtomic,
  writeSessionStateHandoff,
  readSessionStateHandoff,
  markSessionStateHandoffConsumed,
} from "../extensions/context-handoff/handoff-core.mjs";

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
        repoPaths: { wsl: anchorPath },
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
    assert.match(second.message, /already consumed/);
  });
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

test("triggerHandoff always returns the final seed and manual fallback when nothing picks it up", async () => {
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
    writeSessionState: ({ seed }) => ({ ok: true, path: "C:\\state\\handoff-request.json", seed }),
    noteHandoff: () => {},
    logActivity: () => ({ logged: true }),
    requestBridge: () => ({ attempted: true, accepted: false, error: "unsupported" }),
    readPickupSignals: () => ({
      pickedUp: false,
      via: [],
      sessionState: { path: "C:\\state\\handoff-request.json", consumed: false },
      worktree: { pickedUp: false },
      dispatch: { consumed: false },
    }),
    sleepFn: async () => {},
  });
  assert.equal(result.ok, true);
  assert.match(result.manualInstructions, /No control system acknowledged the request/);
  assert.match(result.manualInstructions, new RegExp(result.seed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
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
