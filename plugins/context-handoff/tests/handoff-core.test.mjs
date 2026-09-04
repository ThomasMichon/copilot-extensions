// handoff-core.test.mjs -- unit tests for the SDK-free handoff store/seed core.
//
// Run: node --test  (from plugins/context-handoff/, or point at this file)
//
// Covers the pure, I/O-free surface of handoff-core.mjs -- the payload
// encode/decode round-trip (the on-disk + task-payload format the extension's
// consume path reads), the seed composition for a stored handoff (delegating to
// the issue-#853 bash-first invariant in cutover-seed.mjs), and the path/arg
// sanitizers. The store/trigger functions that shell out to agent-worktrees /
// agent-dispatch are exercised by the clean-room context-handoff-cutover
// scenario, not here.

import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import {
  encodeHandoffPayload, decodeHandoffPayload, buildSeedForStored,
  safePathSegment, agentWorktreesGet, handoffDirFor,
  runCli,
  agentWorktreesGetResult, consumeFileHandoffOnce, writeJsonAtomic,
  collectAdvisoryGitFacts, currentMuxSession, resolveSystemCli,
  sessionBindingForSession, manualFallbackInstructions,
  prepareTaskCutoverCheckpoint, completeHandoffLifecycle,
  consumeDispatchHandoffTask,
  makeHandoffMetadata,
  runHandoffCutover,
  buildResumePrompt,
  normalizeHandoffTitle,
  isolatedPythonArgs,
  runtimeEnvironment,
  HANDOFF_META_PREFIX,
} from "../extensions/context-handoff/handoff-core.mjs";
import {
  existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync,
  utimesSync,
} from "node:fs";
import {
  AGENT_WORKTREES_QUERY_TIMEOUT_MS,
} from "../extensions/context-handoff/cli-timeouts.mjs";
import {
  buildCutoverSeed,
} from "../extensions/context-handoff/cutover-seed.mjs";

test("encode/decode round-trips metadata + text", () => {
  const meta = { kind: "context-handoff", id: "handoff-sid1", title: "Fix X", oldPane: "%3" };
  const body = "## Session Continuation\nline two\nline three";
  const encoded = encodeHandoffPayload(body, meta);
  assert.ok(encoded.startsWith(HANDOFF_META_PREFIX));
  const { metadata, text } = decodeHandoffPayload(encoded);
  assert.deepEqual(metadata, meta);
  assert.equal(text, body);
});

test("decode accepts CRLF-delimited task payloads", () => {
  const meta = { kind: "context-handoff", worktree: "wt-example" };
  const encoded = encodeHandoffPayload("continue", meta).replace(/\n/g, "\r\n");
  const { metadata, text } = decodeHandoffPayload(encoded);
  assert.deepEqual(metadata, meta);
  assert.equal(text, "continue");
});

test("decode of a plain (metadata-less) string returns null metadata + full text", () => {
  const raw = "no metadata here\njust text";
  const { metadata, text } = decodeHandoffPayload(raw);
  assert.equal(metadata, null);
  assert.equal(text, raw);
});

test("decode tolerates malformed metadata JSON (falls back to raw)", () => {
  const raw = `${HANDOFF_META_PREFIX} {not valid json} -->\nbody`;
  const { metadata, text } = decodeHandoffPayload(raw);
  assert.equal(metadata, null);
  assert.equal(text, raw);
});

test("buildSeedForStored: task-backed -> compact recovery locator", () => {
  const stored = {
    storage: "agent-dispatch",
    id: "task-42",
    metadata: {
      title: "Ship the thing",
      oldPane: "%7",
      worktree: "wt-abc",
      worktreeDir: "/tmp/src/wt-abc",
      sessionId: "sid-9",
    },
  };
  const seed = buildSeedForStored(stored, { retry: true });
  assert.match(seed, /Recovery: context-handoff task:task-42$/);
  assert.doesNotMatch(seed, /node -e|handoff-cli/);
  assert.match(seed, /^Task: Ship the thing/);
  assert.match(
    seed,
    /Resume: \/consume-handoff to take over/,
  );
  assert.ok(seed.length <= 200);
});

test("buildSeedForStored: file-backed -> exact file CLI recovery", () => {
  const stored = {
    storage: "file",
    id: "handoff-sid1",
    path: "C:\\state\\handoff-sid1.json",
    metadata: { title: "Fix the parser", oldPane: null, worktree: null, sessionId: "sid1" },
  };
  const seed = buildSeedForStored(stored, { retry: true });
  assert.match(seed, /Recovery: context-handoff file:handoff-sid1$/);
  assert.doesNotMatch(seed, /C:\\\\state\\\\handoff-sid1\.json/);
});

test("buildSeedForStored is identical for launch and manual fallback", () => {
  const stored = {
    storage: "file", id: "h1",
    metadata: { title: "x", oldPane: null, worktree: null, sessionId: "s" },
  };
  assert.equal(
    buildSeedForStored(stored, { retry: false }),
    buildSeedForStored(stored, { retry: true }),
  );
});

test("safePathSegment sanitizes and caps", () => {
  assert.equal(safePathSegment("a/b c:d"), "a_b_c_d");
  assert.equal(safePathSegment(""), "unknown");
  assert.equal(safePathSegment(null), "unknown");
  assert.equal(safePathSegment("x".repeat(300)).length, 160);
});

test("system CLI resolution stays inside the owning marketplace installation", () => {
  const worktrees = resolveSystemCli("agent-worktrees");
  const dispatch = resolveSystemCli("agent-dispatch");
  const pluginsRoot = join(import.meta.dirname, "..", "..");
  assert.ok(worktrees.startsWith(join(pluginsRoot, "agent-worktrees")));
  assert.ok(dispatch.startsWith(join(pluginsRoot, "agent-dispatch")));
  assert.doesNotMatch(worktrees, /odsp-web-harness/);
  assert.doesNotMatch(dispatch, /odsp-web-harness/);
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

test("advisory Git facts do not require an ambient git command", () => {
  assert.deepEqual(collectAdvisoryGitFacts(), {
    branch: null,
    repo: null,
    status: null,
  });
});

test("handoff titles are normalized to one line at entry", () => {
  assert.equal(
    normalizeHandoffTitle("  Fix %PATH%\r\nthen\0 validate\t now  "),
    "Fix %PATH% then validate now",
  );
});

test("Windows runCli preserves quotes and batch metacharacters", {
  skip: process.platform !== "win32",
}, () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-win-args-"));
  try {
    const capture = join(dir, "capture.mjs");
    const output = join(dir, "args.json");
    const seed = buildCutoverSeed(
      "task",
      "task-1",
      'Task: preserve %PATH% "title" & echo not-a-command',
    );
    writeFileSync(
      capture,
      "import { writeFileSync } from 'node:fs';" +
        "writeFileSync(process.env.ARGS_OUT," +
        "JSON.stringify(process.argv.slice(2)));",
    );
    runCli(process.execPath, [
      capture,
      'Task: preserve %PATH% "title" & echo not-a-command',
      seed,
    ], {
      cwd: dir,
      timeout: 5000,
      env: { ...process.env, ARGS_OUT: output },
    });

    assert.deepEqual(JSON.parse(readFileSync(output, "utf8")), [
      'Task: preserve %PATH% "title" & echo not-a-command',
      seed,
    ]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("currentMuxSession resolves the predecessor pane identity", () => {
  let invocation = null;
  const result = currentMuxSession("%7", (bin, args, options) => {
    invocation = { bin, args, options };
    return "caller-session\n";
  });
  assert.equal(result, "caller-session");
  assert.deepEqual(invocation.args, [
    "display-message", "-p", "-t", "%7", "#{session_name}",
  ]);
});

test("sessionBindingForSession uses the public bounded JSON query", () => {
  let invocation = null;
  const result = sessionBindingForSession(
    "session-1",
    "/repo",
    (bin, args, options) => {
      invocation = { bin, args, options };
      return JSON.stringify({
        found: true,
        session_id: "session-1",
        pane_id: "%7",
        copilot_pid: 42,
      });
    },
  );
  assert.equal(result.found, true);
  assert.deepEqual(invocation, {
    bin: "agent-worktrees",
    args: ["session-binding", "--session-id", "session-1", "--json"],
    options: { cwd: "/repo", timeout: AGENT_WORKTREES_QUERY_TIMEOUT_MS },
  });
});

test("metadata discovers mux and process identity without pane environment", () => {
  const oldTmux = process.env.TMUX_PANE;
  const oldPsmux = process.env.PSMUX_PANE;
  delete process.env.TMUX_PANE;
  delete process.env.PSMUX_PANE;
  try {
    const metadata = makeHandoffMetadata(
      {
        sid: "session-1",
        cwd: "/repo",
        title: "Continue",
        storage: "file",
      },
      (_bin, args) => {
        assert.equal(args[0], "session-binding");
        return JSON.stringify({
          found: true,
          session_id: "session-1",
          worktree_id: "wt-example",
          mux_session: "wt-wt-example",
          pane_id: "%7",
          pane_pid: 70,
          pane_start_time: "pane-created",
          copilot_pid: 80,
          copilot_start_time: "copilot-created",
        });
      },
      (key) => key === "worktree-dir"
        ? "/repo/wt-example"
        : "/state/wt-example",
    );
    assert.equal(metadata.oldPane, "%7");
    assert.equal(metadata.muxSession, "wt-wt-example");
    assert.equal(metadata.predecessor.source, "session-binding");
    assert.equal(metadata.predecessor.copilotPid, 80);
    assert.equal(metadata.predecessor.copilotStartTime, "copilot-created");
  } finally {
    if (oldTmux === undefined) delete process.env.TMUX_PANE;
    else process.env.TMUX_PANE = oldTmux;
    if (oldPsmux === undefined) delete process.env.PSMUX_PANE;
    else process.env.PSMUX_PANE = oldPsmux;
  }
});

test("cutover passes startup association after predecessor discovery", () => {
  const calls = [];
  const result = runHandoffCutover(
    "/repo",
    "Task: Continue | Resume: /consume-handoff to take over | Recovery: context-handoff task:task-1",
    "session-1",
    (_bin, args) => {
      calls.push(args);
      if (args[0] === "session-binding") {
        return JSON.stringify({
          found: true,
          session_id: "session-1",
          pane_id: "%9",
        });
      }
      return JSON.stringify({
        ok: true, old_pane: "%9", new_pane: "%10",
      });
    },
    { handoffToken: "task-1", worktreeId: "wt-example" },
  );
  assert.equal(result.ok, true);
  assert.deepEqual(calls[1].slice(-8), [
    "--old-pane", "%9",
    "--session-id", "session-1",
    "--handoff-token", "task-1",
    "--worktree-id", "wt-example",
  ]);
});

test("cutover preserves prompt receipt failure details", () => {
  const result = runHandoffCutover(
    "/repo",
    "continue",
    "session-1",
    (_bin, args) => {
      if (args[0] === "session-binding") {
        return JSON.stringify({ found: false, session_id: "session-1" });
      }
      return JSON.stringify({
        ok: false,
        prompt_received: false,
        prompt_status: "failed:pane-exited",
        error: "successor did not confirm prompt launch",
      });
    },
  );

  assert.deepEqual(result, {
    ok: false,
    prompt_received: false,
    prompt_status: "failed:pane-exited",
    error: "successor did not confirm prompt launch",
    reason: "error",
  });
});

test("cutover handles a null JSON result without masking the failure", () => {
  const result = runHandoffCutover(
    "/repo",
    "continue",
    "session-1",
    (_bin, args) => args[0] === "session-binding"
      ? JSON.stringify({ found: false, session_id: "session-1" })
      : "null",
  );

  assert.deepEqual(result, {
    ok: false,
    reason: "error",
    error: null,
  });
});

test("manual fallback clearly delimits the exact copyable seed", () => {
    const seed =
      "Task: Continue | Resume: /consume-handoff to take over | " +
      "Recovery: context-handoff file:handoff-1";
    const text = manualFallbackInstructions(
      { storage: "file", id: "handoff-1" },
      seed,
    );
    assert.match(text, /Copy only the following short locator prompt/);
    assert.match(text, /pass only the trailing `task:<id>` or `file:<id>` token/);
    assert.match(text, /`consume --locator`/);
    assert.match(text, /```text/);
    assert.match(text, new RegExp(seed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("deferred resume prompt returns explicit completion gate command", () => {
  const prompt = buildResumePrompt(
    "full brief",
    "agent-dispatch task",
    { deferredTaskId: "task-42" },
  );
  assert.match(prompt, /Keep agent-dispatch task task-42 owned/);
  assert.match(
    prompt,
    /Only after the handoff objective's completion gate is met run: agent-dispatch complete task-42/,
  );
  assert.ok(prompt.indexOf("agent-dispatch complete") < prompt.indexOf("full brief"));
});

test("task checkpoint persists payload before one-time consume", () => {
    const dir = mkdtempSync(join(process.cwd(), ".test-handoff-checkpoint-"));
    try {
      const decoded = {
        metadata: {
          stateDir: dir,
          sessionId: "predecessor",
          worktree: "wt-example",
        },
        text: "durable brief",
      };
      const result = prepareTaskCutoverCheckpoint(
        "/repo", "task-1", "successor", decoded,
      );
      assert.equal(result.ok, true);
      const saved = JSON.parse(readFileSync(result.checkpoint.path, "utf-8"));
      assert.equal(saved.payload, "durable brief");
      assert.equal(saved.steps.payloadStored, true);
      assert.equal(saved.steps.taskConsumed, false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
});

test("task retry recovers metadata embedded in an affected checkpoint payload", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-crlf-retry-"));
  const metadata = {
    stateDir: dir,
    worktree: "wt-example",
    worktreeDir: "/repo",
    title: "Recovered handoff",
    predecessor: {
      sessionId: "predecessor",
      paneId: "%3",
      muxSession: "wt-wt-example",
      copilotPid: 33,
      copilotStartTime: "created-33",
    },
  };
  const payload = encodeHandoffPayload("continue", metadata);
  const calls = [];
  try {
    const prepared = prepareTaskCutoverCheckpoint(
      "/repo",
      "task-crlf-retry",
      "successor",
      { metadata: { stateDir: dir }, text: payload },
      () => dir,
    );
    prepared.checkpoint.steps.consumeAttempted = true;
    prepared.checkpoint.steps.taskConsumed = true;
    writeJsonAtomic(prepared.checkpoint.path, prepared.checkpoint);

    const result = consumeDispatchHandoffTask(
      "/repo",
      "task-crlf-retry",
      "successor",
      true,
      {
        readPayload: () => "",
        stateDirResolver: () => dir,
        execute: (_bin, args) => {
          calls.push(args);
          if (args[0] === "bind-session") {
            return JSON.stringify({
              bound: true,
              head_session: "successor",
            });
          }
          if (args[0] === "status") return JSON.stringify({});
          if (args[0] === "handoff-cutover") {
            return JSON.stringify({ ok: true });
          }
          throw new Error(args.join(" "));
        },
      },
    );

    assert.equal(result.ok, true);
    assert.equal(result.metadata.worktree, "wt-example");
    assert.equal(result.payload, "continue");
    assert.equal(result.retire.retired, true);
    assert.deepEqual(calls[0].slice(0, 7), [
      "bind-session",
      "--session-id", "successor",
      "--worktree-id", "wt-example",
      "--handoff-token", "task-crlf-retry",
    ]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("task retry requires structured ownership by this successor", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-owner-retry-"));
  const metadata = {
    stateDir: dir,
    worktree: "wt-example",
    worktreeDir: "/repo",
    title: "Structured retry",
    predecessor: {
      sessionId: "predecessor",
      paneId: "%3",
      muxSession: "wt-wt-example",
      copilotPid: 33,
      copilotStartTime: "created-33",
    },
  };
  const payload = encodeHandoffPayload("continue", metadata);
  try {
    const prepared = prepareTaskCutoverCheckpoint(
      "/repo", "task-owner", "successor", decodeHandoffPayload(payload),
      () => dir,
    );
    prepared.checkpoint.steps.consumeAttempted = true;
    writeJsonAtomic(prepared.checkpoint.path, prepared.checkpoint);
    const result = consumeDispatchHandoffTask(
      "/repo",
      "task-owner",
      "successor",
      true,
      {
        readPayload: () => "",
        consumeTask: () => {
          throw new Error("coordinator says task-owner cannot be consumed");
        },
        taskStateReader: () => ({
          id: "task-owner",
          status: "started",
          owner: "machine/wt-example",
          owner_session_id: "successor",
        }),
        worktreeGet: () => "machine",
        stateDirResolver: () => dir,
        execute: (_bin, args) => {
          if (args[0] === "bind-session") {
            return JSON.stringify({
              bound: true, head_session: "successor",
            });
          }
          if (args[0] === "status") return JSON.stringify({});
          if (args[0] === "handoff-cutover") {
            return JSON.stringify({ ok: true });
          }
          throw new Error(args.join(" "));
        },
      },
    );
    assert.equal(result.ok, true);
    assert.equal(result.retire.retired, true);
    assert.equal(
      result.checkpointState.details.taskConsumed.authoritativeTask.owner_session_id,
      "successor",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("free-form consume error cannot trigger takeover during outage", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-outage-"));
  const metadata = {
    stateDir: dir,
    worktree: "wt-example",
    sessionId: "predecessor",
  };
  const payload = encodeHandoffPayload("continue", metadata);
  let lifecycleCalls = 0;
  try {
    const prepared = prepareTaskCutoverCheckpoint(
      "/repo", "task-outage", "successor", decodeHandoffPayload(payload),
      () => dir,
    );
    prepared.checkpoint.steps.consumeAttempted = true;
    writeJsonAtomic(prepared.checkpoint.path, prepared.checkpoint);
    const result = consumeDispatchHandoffTask(
      "/repo",
      "task-outage",
      "successor",
      true,
      {
        readPayload: () => "",
        consumeTask: () => {
          throw new Error(
            "timeout while running agent-dispatch consume task-outage",
          );
        },
        taskStateReader: () => null,
        worktreeGet: () => "machine",
        stateDirResolver: () => dir,
        execute: () => {
          lifecycleCalls++;
          throw new Error("lifecycle must stay stopped");
        },
      },
    );
    assert.equal(result.ok, false);
    assert.equal(result.ownershipConfirmed, false);
    assert.equal(lifecycleCalls, 0);
    assert.equal(
      JSON.parse(readFileSync(prepared.checkpoint.path, "utf-8"))
        .steps.predecessorRetired,
      false,
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("takeover uses atomic bind/head ack, title, then verified retire", () => {
    const calls = [];
    const execute = (bin, args) => {
      calls.push(args);
      if (args[0] === "bind-session") {
        return JSON.stringify({ bound: true, head_session: "successor" });
      }
      if (args[0] === "status") return "[OK] Worktree title updated";
      if (args[0] === "handoff-cutover") return JSON.stringify({ ok: true });
      throw new Error(`${bin} ${args.join(" ")}`);
    };
    const result = completeHandoffLifecycle(
      "/repo",
      {
        worktree: "wt-example",
        worktreeDir: "/repo",
        title: "Continue parser fix",
        predecessor: {
          sessionId: "predecessor",
          paneId: "%3",
          muxSession: "wt-wt-example",
          copilotPid: 33,
          copilotStartTime: "created-33",
        },
      },
      "successor",
      "task-1",
      {
        execute,
      },
    );
    assert.deepEqual(calls.map((args) => args[0]), [
      "bind-session",
      "status",
      "handoff-cutover",
    ]);
    const retireArgs = calls.at(-1);
    assert.ok(retireArgs.includes("--expected-copilot-pid"));
    assert.ok(retireArgs.includes("--expected-copilot-start-time"));
    assert.equal(result.retired, true);
});

test("old metadata without process creation identity preserves predecessor", () => {
    const calls = [];
    const execute = (_bin, args) => {
      calls.push(args[0]);
      if (args[0] === "bind-session") {
        return JSON.stringify({ bound: true, head_session: "successor" });
      }
      if (args[0] === "link-succession") {
        return JSON.stringify({ head_session: "successor" });
      }
      if (args[0] === "head-session") {
        return JSON.stringify({ head_session: "successor" });
      }
      if (args[0] === "status") return JSON.stringify({});
      throw new Error("retire must not be called");
    };
    const result = completeHandoffLifecycle(
      "/repo",
      {
        worktree: "wt-example",
        worktreeDir: "/repo",
        title: "Old handoff",
        sessionId: "predecessor",
        oldPane: "%3",
        muxSession: "wt-wt-example",
      },
      "successor",
      "task-old",
      { execute },
    );
    assert.equal(result.retired, false);
    assert.match(result.manualCleanup, /creation identity is unavailable/);
    assert.ok(!calls.includes("handoff-cutover"));
});

test("structured retire failure is preserved instead of thrown", () => {
  const error = new Error("command failed");
  error.stdout = JSON.stringify({
    ok: false,
    gone: false,
    method: "failed",
  });
  const result = completeHandoffLifecycle(
    "/repo",
    {
      worktree: "wt-example",
      worktreeDir: "/repo",
      predecessor: {
        sessionId: "predecessor",
        paneId: "%3",
        muxSession: "wt-wt-example",
        copilotPid: 33,
        copilotStartTime: "created-33",
      },
    },
    "successor",
    "task-structured-failure",
    {
      execute: (_bin, args) => {
        if (args[0] === "bind-session") {
          return JSON.stringify({ bound: true, head_session: "successor" });
        }
        if (args[0] === "handoff-cutover") throw error;
        throw new Error(args.join(" "));
      },
    },
  );
  assert.equal(result.retired, false);
  assert.equal(result.retireResult.method, "failed");
  assert.match(result.manualCleanup, /manual cleanup/);
});

test("same successor retry resumes from task checkpoint without replaying payload", () => {
    const dir = mkdtempSync(join(process.cwd(), ".test-handoff-retry-"));
    let consumeCalls = 0;
    let failTitle = true;
    const metadata = {
      stateDir: dir,
      worktree: "wt-example",
      worktreeDir: "/repo",
      title: "Retry cutover",
      predecessor: {
        sessionId: "predecessor",
        paneId: "%3",
        muxSession: "wt-wt-example",
        copilotPid: 33,
        copilotStartTime: "created-33",
      },
    };
    const payload = encodeHandoffPayload("continue", metadata);
    const execute = (_bin, args) => {
      if (args[0] === "bind-session") {
        return JSON.stringify({ bound: true, head_session: "successor" });
      }
      if (args[0] === "status") {
        if (failTitle) {
          failTitle = false;
          throw new Error("injected title failure");
        }
        return JSON.stringify({});
      }
      if (args[0] === "handoff-cutover") return JSON.stringify({ ok: true });
      throw new Error(args.join(" "));
    };
    try {
      assert.throws(
        () => consumeDispatchHandoffTask(
          "/repo", "task-1", "successor", true,
          {
            readPayload: () => payload,
            consumeTask: () => {
              consumeCalls++;
              return payload;
            },
            execute,
            stateDirResolver: () => dir,
          },
        ),
        /injected title failure/,
      );
      const retry = consumeDispatchHandoffTask(
        "/repo", "task-1", "successor", true,
        {
          readPayload: () => "",
          consumeTask: () => {
            consumeCalls++;
            throw new Error("task already consumed by this successor");
          },
          execute,
          stateDirResolver: () => dir,
        },
      );
      assert.equal(retry.ok, true);
      assert.equal(retry.retire.retired, true);
      assert.equal(consumeCalls, 1);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
});

for (const failedStep of ["bind-session", "status", "handoff-cutover"]) {
    test(`checkpoint retry converges after ${failedStep} failure`, () => {
      const dir = mkdtempSync(join(
        process.cwd(), `.test-handoff-step-${failedStep}-`,
      ));
      let consumeCalls = 0;
      let failureBudget = failedStep === "bind-session" ? 2 : 1;
      const metadata = {
        stateDir: dir,
        worktree: "wt-example",
        worktreeDir: "/repo",
        title: "Retry every step",
        predecessor: {
          sessionId: "predecessor",
          paneId: "%3",
          muxSession: "wt-wt-example",
          copilotPid: 33,
          copilotStartTime: "created-33",
        },
      };
      const payload = encodeHandoffPayload("continue", metadata);
      const execute = (_bin, args) => {
        const step = args[0];
        if (step === failedStep && failureBudget > 0) {
          failureBudget--;
          if (step === "handoff-cutover") {
            return JSON.stringify({ ok: false });
          }
          throw new Error(`injected ${step} failure`);
        }
        if (step === "bind-session") {
          return JSON.stringify({ bound: true, head_session: "successor" });
        }
        if (step === "link-succession") {
          return JSON.stringify({ head_session: "successor" });
        }
        if (step === "head-session") {
          return JSON.stringify({ head_session: "successor" });
        }
        if (step === "status") return JSON.stringify({});
        if (step === "handoff-cutover") return JSON.stringify({ ok: true });
        throw new Error(args.join(" "));
      };
      const options = {
        readPayload: () => payload,
        consumeTask: () => {
          consumeCalls++;
          return payload;
        },
        execute,
        stateDirResolver: () => dir,
      };
      try {
        try {
          consumeDispatchHandoffTask(
            "/repo", "task-step", "successor", true, options,
          );
        } catch {
          // A command failure may abort the first attempt after its prior
          // checkpoint has already been persisted.
        }
        const retry = consumeDispatchHandoffTask(
          "/repo",
          "task-step",
          "successor",
          true,
          {
            ...options,
            readPayload: () => "",
            consumeTask: () => {
              consumeCalls++;
              throw new Error("task already consumed by this successor");
            },
          },
        );
        assert.equal(retry.retire.retired, true);
        assert.equal(consumeCalls, 1);
      } finally {
        rmSync(dir, { recursive: true, force: true });
      }
    });
}

test("agentWorktreesGet allows slow startup-time identity queries", () => {
  let invocation = null;
  const value = agentWorktreesGet(
    "worktree-state-dir",
    "/repo",
    "session-1",
    (bin, args, options) => {
      invocation = { bin, args, options };
      return "/state/worktree\n";
    },
  );

  assert.equal(value, "/state/worktree");
  assert.deepEqual(invocation, {
    bin: "agent-worktrees",
    args: ["get", "worktree-state-dir", "--session-id", "session-1"],
    options: {
      cwd: "/repo",
      timeout: AGENT_WORKTREES_QUERY_TIMEOUT_MS,
    },
  });
  assert.equal(AGENT_WORKTREES_QUERY_TIMEOUT_MS, 15_000);
});

test("agentWorktreesGetResult explains an empty resolver result", () => {
  const result = agentWorktreesGetResult(
    "worktree-state-dir",
    "/repo",
    "session-1",
    () => "\n",
  );
  assert.equal(result.value, null);
  assert.match(result.error, /returned an empty result/);
  assert.match(result.error, /session-1/);
});

test("atomic write cleans its temporary file", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-atomic-"));
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, { ok: true });
    assert.deepEqual(JSON.parse(readFileSync(path, "utf-8")), { ok: true });
    assert.deepEqual(readdirSync(dir), ["handoff.json"]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption is exactly once", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-consume-"));
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-once",
      consumed: false,
      consumedAt: null,
      promptText: "continue",
    });
    const first = consumeFileHandoffOnce("/repo", "successor", null, path);
    const retry = consumeFileHandoffOnce("/repo", "successor", null, path);
    const second = consumeFileHandoffOnce("/repo", "other", null, path);
    assert.equal(first.ok, true);
    assert.equal(first.record.consumedBySession, "successor");
    assert.equal(retry.ok, true);
    assert.equal(retry.resumedDelivery, true);
    assert.equal(second.ok, false);
    assert.equal(second.alreadyConsumed, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption recovers a dead-owner lock", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-lock-"));
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-lock",
      consumed: false,
      consumedAt: null,
      promptText: "continue",
    });
    writeFileSync(`${path}.consume.lock`, JSON.stringify({ pid: 2147483647 }));
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption does not steal a fresh incomplete lock", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-fresh-lock-"));
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-fresh-lock",
      consumed: false,
      promptText: "continue",
    });
    writeFileSync(`${path}.consume.lock`, "");
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, false);
    assert.equal(consumed.busy, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption treats EPERM lock owners as alive", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-eperm-lock-"));
  const originalKill = process.kill;
  try {
    const path = join(dir, "handoff.json");
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-eperm-lock",
      consumed: false,
      promptText: "continue",
    });
    writeFileSync(`${path}.consume.lock`, JSON.stringify({ pid: 12345 }));
    process.kill = () => {
      const error = new Error("not permitted");
      error.code = "EPERM";
      throw error;
    };
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, false);
    assert.equal(consumed.busy, true);
  } finally {
    process.kill = originalKill;
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption recovers a stale incomplete lock", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-stale-lock-"));
  try {
    const path = join(dir, "handoff.json");
    const lock = `${path}.consume.lock`;
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-stale-lock",
      consumed: false,
      promptText: "continue",
    });
    writeFileSync(lock, "");
    const stale = new Date(Date.now() - 60_000);
    utimesSync(lock, stale, stale);
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("file handoff consumption recovers a dead recovery lock", () => {
  const dir = mkdtempSync(join(process.cwd(), ".test-handoff-recovery-lock-"));
  try {
    const path = join(dir, "handoff.json");
    const lock = `${path}.consume.lock`;
    writeJsonAtomic(path, {
      kind: "context-handoff",
      id: "handoff-recovery-lock",
      consumed: false,
      promptText: "continue",
    });
    writeFileSync(lock, JSON.stringify({ pid: 2147483647 }));
    writeFileSync(`${lock}.recover`, JSON.stringify({ pid: 2147483647 }));
    const consumed = consumeFileHandoffOnce("/repo", "successor", null, path);
    assert.equal(consumed.ok, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("handoffDirFor resolves only the state directory", () => {
  const keys = [];
  const dir = handoffDirFor("/repo", "session-1", (key, cwd, sid) => {
    keys.push({ key, cwd, sid });
    return "/state/worktree";
  });

  assert.equal(dir, join("/state/worktree", "handoff"));
  assert.deepEqual(keys, [{
    key: "worktree-state-dir",
    cwd: "/repo",
    sid: "session-1",
  }]);
});
