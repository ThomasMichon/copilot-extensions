import { test, mock } from "node:test";
import assert from "node:assert/strict";
import childProcess from "node:child_process";
import { EventEmitter } from "node:events";
import { syncBuiltinESMExports } from "node:module";
import { PassThrough } from "node:stream";
import { worktreeIsDirty } from "../extensions/context-handoff/handoff-core.mjs";

async function withChild(run, check) {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.kills = [];
  child.kill = signal => {
    child.kills.push(signal);
    child.emit("close", 0, null);
    return true;
  };
  const stub = mock.method(childProcess, "spawn", () => {
    queueMicrotask(() => run(child));
    return child;
  });
  syncBuiltinESMExports();
  try {
    await check(child);
  } finally {
    child.stdout.destroy();
    child.stderr.destroy();
    stub.mock.restore();
    syncBuiltinESMExports();
  }
}

test("status deadline cannot accept a zero exit caused by timeout cleanup", async () => {
  await withChild(() => {}, async child => {
    await assert.rejects(worktreeIsDirty("/unused", { timeoutMs: 20 }), /timed out/);
    assert.deepEqual(child.kills, ["SIGKILL"]);
    assert.equal(child.stdout.destroyed, true);
    assert.equal(child.stderr.destroyed, true);
  });
});

test("status deadline settles when an exited child's inherited pipes stay open", async () => {
  await withChild(child => child.emit("exit", 0, null), async child => {
    await assert.rejects(worktreeIsDirty("/unused", { timeoutMs: 20 }), /timed out/);
    assert.equal(child.stdout.destroyed, true);
    assert.equal(child.stderr.destroyed, true);
  });
});

test("status output is not accepted before a successful close", async () => {
  await withChild(child => {
    child.stdout.write("?? pending.txt\n");
    child.stderr.write("x".repeat(10000));
    child.emit("close", 128, null);
  }, async () => {
    await assert.rejects(worktreeIsDirty("/unused"), error =>
      error.message.startsWith("git status exited 128: ") && error.message.length < 4200,
    );
  });
});

test("status close with empty output reports clean", async () => {
  await withChild(child => child.emit("close", 0, null), async () => {
    assert.equal(await worktreeIsDirty("/unused"), false);
  });
});
