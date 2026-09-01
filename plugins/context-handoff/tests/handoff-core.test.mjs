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
  safePathSegment, quoteWinArg, agentWorktreesGet, handoffDirFor,
  agentWorktreesGetResult, consumeFileHandoffOnce, writeJsonAtomic,
  currentMuxSession, currentPaneId, herdrHandoffDir, runHandoffCutover,
  makeHandoffMetadata, resolveHandoffCwd, resolveHerdrPredecessorIdentity,
  retireHerdrPredecessorAfterConsume, worktreeInfo,
  HANDOFF_META_PREFIX,
} from "../extensions/context-handoff/handoff-core.mjs";
import {
  existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync,
  utimesSync,
} from "node:fs";
import {
  AGENT_WORKTREES_QUERY_TIMEOUT_MS,
} from "../extensions/context-handoff/cli-timeouts.mjs";

test("encode/decode round-trips metadata + text", () => {
  const meta = { kind: "context-handoff", id: "handoff-sid1", title: "Fix X", oldPane: "%3" };
  const body = "## Session Continuation\nline two\nline three";
  const encoded = encodeHandoffPayload(body, meta);
  assert.ok(encoded.startsWith(HANDOFF_META_PREFIX));
  const { metadata, text } = decodeHandoffPayload(encoded);
  assert.deepEqual(metadata, meta);
  assert.equal(text, body);
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

test("buildSeedForStored: task-backed + known pane/worktree/session -> bash-first seed", () => {
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
  // Bash-first invariant (issue #853): the successor's first action is a core
  // shell chain, not the consume_handoff tool.
  assert.match(seed, /agent-dispatch consume task-42 --defer-complete/);
  assert.match(seed, /agent-worktrees handoff-cutover --retire-pane %7/);
  assert.match(seed, /^Task: Ship the thing/);
  assert.match(seed, /intended cwd "\/tmp\/src\/wt-abc"/);
  assert.match(seed, /agent-worktrees bind-session --worktree-id wt-abc/);
  assert.doesNotMatch(seed, /consume_handoff tool/);
});

test("buildSeedForStored: file-backed -> tool-based consume_handoff seed", () => {
  const stored = {
    storage: "file",
    id: "handoff-sid1",
    path: "C:\\state\\handoff-sid1.json",
    metadata: { title: "Fix the parser", oldPane: null, worktree: null, sessionId: "sid1" },
  };
  const seed = buildSeedForStored(stored, { retry: true });
  assert.match(seed, /consume_handoff tool/);
  assert.match(seed, /"path":"C:\\\\state\\\\handoff-sid1.json"/);
});

test("buildSeedForStored: paste prompt (retry:false) omits the loading-race retry clause", () => {
  const stored = {
    storage: "file", id: "h1",
    metadata: { title: "x", oldPane: null, worktree: null, sessionId: "s" },
  };
  const paste = buildSeedForStored(stored, { retry: false });
  assert.doesNotMatch(paste, /tool-not-found/);
});

test("safePathSegment sanitizes and caps", () => {
  assert.equal(safePathSegment("a/b c:d"), "a_b_c_d");
  assert.equal(safePathSegment(""), "unknown");
  assert.equal(safePathSegment(null), "unknown");
  assert.equal(safePathSegment("x".repeat(300)).length, 160);
});

test("quoteWinArg quotes only when needed", () => {
  assert.equal(quoteWinArg("plain"), "plain");
  assert.equal(quoteWinArg("has space"), '"has space"');
  assert.equal(quoteWinArg('a"b'), '"a""b"');
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

test("currentPaneId suppresses nested mux identity when Herdr owns the host", () => {
  assert.equal(currentPaneId({
    HERDR_ENV: "1",
    HERDR_PANE_ID: "w1:p2",
    TMUX_PANE: "%7",
  }), null);
  assert.equal(currentPaneId({ TMUX_PANE: "%7" }), "%7");
});

test("handoffDirFor uses checkout-scoped Herdr state without agent-worktrees", () => {
  const home = join(process.cwd(), ".test-herdr-home");
  const cwd = join(process.cwd(), "fixture-checkout");
  const env = { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p2" };
  let queriedAgentWorktrees = false;

  const dir = handoffDirFor(
    cwd,
    "session-1",
    () => {
      queriedAgentWorktrees = true;
      return "/unexpected";
    },
    env,
    home,
  );

  assert.equal(dir, herdrHandoffDir(cwd, env, home));
  assert.equal(queriedAgentWorktrees, false);
  assert.match(dir, /\.copilot[\\/]context-handoff[\\/]checkouts/);
});

test("worktreeInfo does not query agent-worktrees when Herdr owns the host", () => {
  let queriedAgentWorktrees = false;
  const info = worktreeInfo(
    "/repo",
    "session-1",
    () => {
      queriedAgentWorktrees = true;
      return "/unexpected";
    },
    { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p2" },
  );

  assert.deepEqual(info, {
    wtDir: null,
    worktree: null,
    stateDir: null,
  });
  assert.equal(queriedAgentWorktrees, false);
});

test("resolveHandoffCwd uses the current Herdr pane instead of process cwd", () => {
  const home = "/home/tester";
  const result = resolveHandoffCwd("/stale/process/cwd", {
    env: { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p2" },
    home,
    execute: (bin, args, options) => {
      assert.equal(bin, join(home, ".local", "bin", "herdr"));
      assert.deepEqual(args, ["pane", "current", "--current"]);
      assert.deepEqual(options, { timeout: 5000 });
      return JSON.stringify({
        result: { pane: { cwd: "/repo/current-checkout" } },
      });
    },
  });

  assert.deepEqual(result, {
    cwd: "/repo/current-checkout",
    error: null,
  });
});

test("resolveHandoffCwd preserves stdout diagnostics when stderr is empty", () => {
  const result = resolveHandoffCwd("/stale/process/cwd", {
    env: { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p2" },
    execute: () => {
      throw {
        stderr: Buffer.alloc(0),
        stdout: Buffer.from("pane lookup failed"),
      };
    },
  });

  assert.deepEqual(result, {
    cwd: null,
    error: "pane lookup failed",
  });
});

test("Herdr predecessor identity is persisted with pane and session", () => {
  const env = { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p2" };
  const resolved = resolveHerdrPredecessorIdentity("predecessor-session", {
    env,
    home: "/home/tester",
    execute: (bin, args) => {
      assert.equal(bin, "/home/tester/.local/bin/herdr");
      assert.deepEqual(args, ["agent", "get", "w1:p2"]);
      return JSON.stringify({
        result: {
          agent: {
            agent: "copilot",
            name: "copilot-w1-p2",
            pane_id: "w1:p2",
            terminal_id: "term-predecessor",
            agent_session: { value: "predecessor-session" },
          },
        },
      });
    },
  });
  const metadata = makeHandoffMetadata({
    sid: "predecessor-session",
    cwd: "/repo",
    title: "handoff",
    storage: "file",
    predecessor: resolved.predecessor,
  });

  assert.equal(resolved.error, null);
  assert.deepEqual(metadata.predecessor, {
    transport: "herdr",
    paneId: "w1:p2",
    sessionId: "predecessor-session",
    agentName: "copilot-w1-p2",
    terminalId: "term-predecessor",
  });
});

test("null Herdr agent name still persists and retires exact predecessor", () => {
  const resolved = resolveHerdrPredecessorIdentity("predecessor-session", {
    env: { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p2" },
    home: "/home/tester",
    execute: () => JSON.stringify({
      result: {
        agent: {
          agent: "copilot",
          name: null,
          pane_id: "w1:p2",
          terminal_id: "term-predecessor",
        },
      },
    }),
  });
  const invocations = [];
  const result = retireHerdrPredecessorAfterConsume({
    consumed: true,
    metadata: { predecessor: resolved.predecessor },
    successorSessionId: "successor-session",
  }, {
    env: { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p3" },
    home: "/home/tester",
    launcherPath: "/home/tester/.local/bin/copilot-pane",
    execute: (bin, args, options) => {
      invocations.push({ bin, args, options });
      if (bin.endsWith("/herdr")) {
        const paneId = args.at(-1);
        return JSON.stringify({
          result: {
            agent: {
              agent: "copilot",
              name: null,
              pane_id: paneId,
              terminal_id:
                paneId === "w1:p3"
                  ? "term-successor"
                  : "term-predecessor",
            },
          },
        });
      }
      return JSON.stringify({ result: { type: "ok" } });
    },
  });

  assert.equal(resolved.error, null);
  assert.deepEqual(resolved.predecessor, {
    transport: "herdr",
    paneId: "w1:p2",
    sessionId: "predecessor-session",
    terminalId: "term-predecessor",
  });
  assert.equal(
    invocations.filter(
      ({ bin }) => bin === "/home/tester/.local/bin/copilot-pane",
    ).length,
    1,
  );
  assert.equal(result.retired, true);
});

test("Herdr agent names are checked when both sides report them", () => {
  let stopCalls = 0;
  const result = retireHerdrPredecessorAfterConsume({
    consumed: true,
    metadata: {
      predecessor: {
        transport: "herdr",
        paneId: "w1:p2",
        sessionId: "predecessor-session",
        agentName: "recorded-agent",
        terminalId: "term-predecessor",
      },
    },
    successorSessionId: "successor-session",
  }, {
    env: { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p3" },
    home: "/home/tester",
    launcherPath: "/home/tester/.local/bin/copilot-pane",
    execute: (bin, args) => {
      if (!bin.endsWith("/herdr")) {
        stopCalls += 1;
        return "";
      }
      const paneId = args.at(-1);
      return JSON.stringify({
        result: {
          agent: {
            agent: "copilot",
            name:
              paneId === "w1:p3"
                ? "successor-agent"
                : "different-agent",
            pane_id: paneId,
            terminal_id:
              paneId === "w1:p3"
                ? "term-successor"
                : "term-predecessor",
          },
        },
      });
    },
  });

  assert.equal(stopCalls, 0);
  assert.equal(result.retired, false);
  assert.equal(result.status, "predecessor-session-mismatch");
});

test("failed consumption performs no Herdr stop", () => {
  let calls = 0;
  const result = retireHerdrPredecessorAfterConsume({
    consumed: false,
    metadata: {
      predecessor: {
        transport: "herdr",
        paneId: "w1:p2",
        sessionId: "predecessor-session",
        agentName: "copilot-w1-p2",
        terminalId: "term-predecessor",
      },
    },
    successorSessionId: "successor-session",
  }, {
    env: { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p3" },
    execute: () => {
      calls += 1;
      return "";
    },
  });

  assert.equal(calls, 0);
  assert.equal(result.retired, false);
  assert.equal(result.status, "consume-failed");
});

test("successful consumption stops exactly the recorded Herdr pane", () => {
  const invocations = [];
  const result = retireHerdrPredecessorAfterConsume({
    consumed: true,
    metadata: {
      predecessor: {
        transport: "herdr",
        paneId: "w1:p2",
        sessionId: "predecessor-session",
        agentName: "copilot-w1-p2",
        terminalId: "term-predecessor",
      },
    },
    successorSessionId: "successor-session",
  }, {
    env: { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p3" },
    home: "/home/tester",
    launcherPath: "/home/tester/.local/bin/copilot-pane",
    execute: (bin, args, options) => {
      invocations.push({ bin, args, options });
      if (bin.endsWith("/herdr")) {
        const paneId = args.at(-1);
        return JSON.stringify({
          result: {
            agent: {
              agent: "copilot",
              name: `copilot-${paneId.replace(":", "-")}`,
              pane_id: paneId,
              terminal_id:
                paneId === "w1:p3"
                  ? "term-successor"
                  : "term-predecessor",
              agent_session: {
                value:
                  paneId === "w1:p3"
                    ? "successor-session"
                    : "predecessor-session",
              },
            },
          },
        });
      }
      return JSON.stringify({ result: { type: "ok" } });
    },
  });

  const stops = invocations.filter(
    ({ bin }) => bin === "/home/tester/.local/bin/copilot-pane",
  );
  assert.equal(stops.length, 1);
  assert.deepEqual(stops[0], {
    bin: "/home/tester/.local/bin/copilot-pane",
    args: ["stop", "--pane", "w1:p2"],
    options: { timeout: 30_000 },
  });
  assert.equal(result.retired, true);
  assert.equal(result.status, "stopped");
});

test("Herdr predecessor session mismatch performs no stop", () => {
  const invocations = [];
  const result = retireHerdrPredecessorAfterConsume({
    consumed: true,
    metadata: {
      predecessor: {
        transport: "herdr",
        paneId: "w1:p2",
        sessionId: "predecessor-session",
        agentName: "copilot-w1-p2",
        terminalId: "term-predecessor",
      },
    },
    successorSessionId: "successor-session",
  }, {
    env: { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p3" },
    home: "/home/tester",
    launcherPath: "/home/tester/.local/bin/copilot-pane",
    execute: (bin, args) => {
      invocations.push({ bin, args });
      const paneId = args.at(-1);
      return JSON.stringify({
        result: {
          agent: {
            agent: "copilot",
            name: `copilot-${paneId.replace(":", "-")}`,
            pane_id: paneId,
            terminal_id:
              paneId === "w1:p3"
                ? "term-successor"
                : "term-predecessor",
            agent_session: {
              value:
                paneId === "w1:p3"
                  ? "successor-session"
                  : "different-predecessor",
            },
          },
        },
      });
    },
  });

  assert.equal(
    invocations.some(
      ({ bin }) => bin === "/home/tester/.local/bin/copilot-pane",
    ),
    false,
  );
  assert.equal(result.retired, false);
  assert.equal(result.status, "predecessor-session-mismatch");
});

test("Herdr retirement never stops the current successor pane", () => {
  let calls = 0;
  const result = retireHerdrPredecessorAfterConsume({
    consumed: true,
    metadata: {
      predecessor: {
        transport: "herdr",
        paneId: "w1:p3",
        sessionId: "predecessor-session",
        agentName: "copilot-w1-p3",
        terminalId: "term-successor",
      },
    },
    successorSessionId: "successor-session",
  }, {
    env: { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p3" },
    execute: () => {
      calls += 1;
      return "";
    },
  });

  assert.equal(calls, 0);
  assert.equal(result.retired, false);
  assert.equal(result.status, "current-pane");
});

test("runHandoffCutover routes Herdr through one copilot-pane task file", () => {
  const home = mkdtempSync(join(process.cwd(), ".test-herdr-cutover-"));
  const cwd = join(process.cwd(), "stale-process-cwd");
  const paneCwd = join(process.cwd(), "fixture-checkout");
  const env = {
    HERDR_ENV: "1",
    HERDR_PANE_ID: "w1:p2",
    TMUX_PANE: "%9",
  };
  const seed = "Task: continue through the saved baton";
  let invocation = null;
  let launcherCalls = 0;
  try {
    const result = runHandoffCutover(cwd, seed, "session-1", {
      env,
      home,
      now: () => 1234,
      execute: (bin, args, options) => {
        if (bin === join(home, ".local", "bin", "herdr")) {
          assert.deepEqual(args, ["pane", "current", "--current"]);
          return JSON.stringify({ result: { pane: { cwd: paneCwd } } });
        }
        launcherCalls += 1;
        invocation = { bin, args, options };
        const taskFile = args.at(-1);
        assert.equal(readFileSync(taskFile, "utf-8"), seed);
        return [
          "pane_handle=w1:p3",
          "terminal_identity=t3",
          "observed_process_kind=copilot",
          "copilot_session_id=01234567-89ab-4cde-8fab-0123456789ab",
        ].join("\n");
      },
    });

    assert.equal(launcherCalls, 1);
    assert.equal(
      invocation.bin,
      join(home, ".local", "bin", "copilot-pane"),
    );
    assert.deepEqual(invocation.args.slice(0, -1), [
      "launch",
      "--role", "coordinator",
      "--cwd", paneCwd,
      "--host", "local",
      "--task-file",
    ]);
    assert.deepEqual(invocation.options, { cwd: paneCwd, timeout: 180_000 });
    assert.equal(existsSync(invocation.args.at(-1)), false);
    assert.deepEqual(result, {
      ok: true,
      host: "herdr",
      old_pane: "w1:p2",
      new_pane: "w1:p3",
      new_session: "01234567-89ab-4cde-8fab-0123456789ab",
      predecessor_retirement: "after-consume",
    });
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("runHandoffCutover preserves launcher stdout when stderr is empty", () => {
  const home = mkdtempSync(join(process.cwd(), ".test-herdr-error-"));
  const env = { HERDR_ENV: "1", HERDR_PANE_ID: "w1:p2" };
  try {
    const result = runHandoffCutover("/stale", "seed", "session-1", {
      env,
      home,
      execute: (bin) => {
        if (bin === join(home, ".local", "bin", "herdr")) {
          return JSON.stringify({
            result: { pane: { cwd: "/repo/current-checkout" } },
          });
        }
        throw {
          stderr: Buffer.alloc(0),
          stdout: Buffer.from("launcher rejected task"),
        };
      },
    });

    assert.equal(result.ok, false);
    assert.equal(result.host, "herdr");
    assert.equal(result.error, "launcher rejected task");
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("runHandoffCutover keeps the existing mux route outside Herdr", () => {
  let invocation = null;
  const result = runHandoffCutover("/repo", "seed", "session-1", {
    env: { TMUX_PANE: "%7" },
    execute: (bin, args, options) => {
      invocation = { bin, args, options };
      return JSON.stringify({ ok: true, old_pane: "%7", new_pane: "%8" });
    },
  });

  assert.deepEqual(invocation, {
    bin: "agent-worktrees",
    args: [
      "handoff-cutover",
      "--seed", "seed",
      "--old-pane", "%7",
      "--session-id", "session-1",
    ],
    options: { cwd: "/repo", timeout: 20000 },
  });
  assert.deepEqual(result, {
    ok: true,
    old_pane: "%7",
    new_pane: "%8",
    host: "mux",
  });
});

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
  }, {}, "/unused-home");

  assert.equal(dir, join("/state/worktree", "handoff"));
  assert.deepEqual(keys, [{
    key: "worktree-state-dir",
    cwd: "/repo",
    sid: "session-1",
  }]);
});
