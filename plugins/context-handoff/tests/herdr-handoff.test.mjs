import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  consumeFileHandoff, currentPaneId, formatConsumeResult,
  runHandoffCutover, storeHandoff,
} from "../extensions/context-handoff/handoff-core.mjs";

test("Herdr store and consume retire only the identity-matched predecessor", () => {
  const home = mkdtempSync(join(tmpdir(), "handoff-herdr-"));
  const previous = { ...process.env };
  const calls = [];
  let terminal = "terminal-predecessor";
  let targetSession = "predecessor";
  let name = null;
  const execute = (bin, args) => {
    calls.push([bin, ...args]);
    if (args[0] === "pane") {
      return JSON.stringify({ result: { pane: {
        cwd: home,
        foreground_cwd: "/deleted/plugin-payload",
      } } });
    }
    if (args[0] === "agent") {
      const predecessor = args[2] === "w1:p1";
      return JSON.stringify({ result: { agent: {
        agent: "copilot",
        name,
        pane_id: args[2],
        terminal_id: predecessor ? terminal : "terminal-successor",
        agent_session: { value: predecessor ? targetSession : "successor" },
      } } });
    }
    assert.ok(bin.endsWith("/copilot-pane"));
    assert.deepEqual(args, ["stop", "--pane", "w1:p1"]);
    return "";
  };
  try {
    Object.assign(process.env, {
      HOME: home, HERDR_ENV: "1", HERDR_PANE_ID: "w1:p1", TMUX_PANE: "%9",
    });
    assert.equal(currentPaneId(), null);
    const stored = storeHandoff({
      promptText: "continue the original objective", sid: "predecessor",
      cwd: "/stale/plugin-cwd", title: "handoff", execute,
    });
    assert.equal(stored.storage, "file");
    assert.equal(stored.metadata.cwd, home);
    assert.equal(stored.metadata.worktree, null);
    assert.equal(stored.metadata.predecessor.transport, "herdr");
    assert.equal(stored.metadata.predecessor.sessionId, "predecessor");
    assert.equal(stored.metadata.predecessor.terminalId, terminal);
    assert.equal(calls.some(([bin]) => bin.includes("agent-worktrees")), false);
    assert.equal(calls.some(([, verb]) => verb === "stop"), false);

    process.env.HERDR_PANE_ID = "w1:p2";
    const consume = () => consumeFileHandoff(home, "successor", stored.id, stored.path, { execute });
    targetSession = "new-owner";
    let result = consume();
    assert.equal(result.ok, true);
    assert.equal(result.retire.retired, false);
    assert.equal(calls.some(([, verb]) => verb === "stop"), false);
    targetSession = "predecessor";
    terminal = "reused-terminal";
    result = consume();
    assert.equal(result.retire.retired, false);
    assert.equal(calls.some(([, verb]) => verb === "stop"), false);
    terminal = "terminal-predecessor";
    result = consume();
    assert.equal(result.retire.retired, true);
    assert.match(formatConsumeResult(result), /Herdr successor verified/);
    assert.doesNotMatch(formatConsumeResult(result), /Successor lifecycle:\*\* incomplete/);
    assert.equal(JSON.parse(readFileSync(stored.path)).consumedBySession, "successor");
    assert.equal(calls.filter(([, verb]) => verb === "stop").length, 1);
    consume();
    assert.equal(calls.filter(([, verb]) => verb === "stop").length, 1);
    const other = consumeFileHandoff(home, "other-session", stored.id, stored.path, { execute });
    assert.equal(other.ok, false);
    assert.equal(calls.filter(([, verb]) => verb === "stop").length, 1);

    process.env.HERDR_PANE_ID = "w1:p1";
    const second = storeHandoff({
      promptText: "another objective", sid: "predecessor-2", cwd: home,
      title: "second", execute: (bin, args) => {
        if (args[0] === "agent") {
          targetSession = "predecessor-2";
          name = "named-copilot";
        }
        return execute(bin, args);
      },
    });
    const samePane = consumeFileHandoff(home, "successor", second.id, second.path, { execute });
    assert.equal(samePane.retire.retired, false);
    assert.equal(calls.filter(([, verb]) => verb === "stop").length, 1);
  } finally {
    for (const key of Object.keys(process.env)) {
      if (!(key in previous)) delete process.env[key];
    }
    Object.assign(process.env, previous);
    rmSync(home, { recursive: true, force: true });
  }
});

test("Herdr launch uses one seeded launcher call and explicit permission mode", () => {
  const home = mkdtempSync(join(tmpdir(), "handoff-herdr-launch-"));
  const previous = { ...process.env };
  const calls = [];
  const execute = (bin, args) => {
    calls.push([bin, ...args]);
    if (args[0] === "pane") {
      return JSON.stringify({ result: { pane: { cwd: home } } });
    }
    assert.ok(bin.endsWith("/copilot-pane"));
    assert.equal(readFileSync(args[8], "utf8"), "exact seed");
    assert.deepEqual(args.slice(9), ["--permission-mode", "allow-all"]);
    return "pane_handle=w1:p2\ncopilot_session_id=successor\n";
  };
  try {
    Object.assign(process.env, { HOME: home, HERDR_ENV: "1", HERDR_PANE_ID: "w1:p1" });
    const refused = runHandoffCutover(home, "exact seed", "predecessor", execute);
    assert.equal(refused.ok, false);
    assert.equal(calls.length, 0);
    const launched = runHandoffCutover(home, "exact seed", "predecessor", execute, {
      permissionMode: "allow-all",
    });
    assert.equal(launched.ok, true);
    assert.equal(launched.host, "herdr");
    assert.equal(calls.filter(([, verb]) => verb === "launch").length, 1);
    assert.equal(calls.some(([, verb]) => verb === "stop"), false);
  } finally {
    for (const key of Object.keys(process.env)) {
      if (!(key in previous)) delete process.env[key];
    }
    Object.assign(process.env, previous);
    rmSync(home, { recursive: true, force: true });
  }
});
