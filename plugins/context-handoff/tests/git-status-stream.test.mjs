import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { gitStatusHasOutput } from "../extensions/context-handoff/git-status.mjs";

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
  const launch = () => {
    queueMicrotask(() => run(child));
    return child;
  };
  try {
    await check(child, timeoutMs => gitStatusHasOutput("/unused", {}, launch, timeoutMs));
  } finally {
    child.stdout.destroy();
    child.stderr.destroy();
  }
}

test("status deadline cannot accept a zero exit caused by timeout cleanup", async () => {
  await withChild(() => {}, async (child, inspect) => {
    await assert.rejects(inspect(20), /timed out/);
    assert.deepEqual(child.kills, ["SIGKILL"]);
    assert.equal(child.stdout.destroyed, true);
    assert.equal(child.stderr.destroyed, true);
  });
});

test("status deadline settles when an exited child's inherited pipes stay open", async () => {
  await withChild(child => child.emit("exit", 0, null), async (child, inspect) => {
    await assert.rejects(inspect(20), /timed out/);
    assert.equal(child.stdout.destroyed, true);
    assert.equal(child.stderr.destroyed, true);
  });
});

test("status output is not accepted before a successful close", async () => {
  await withChild(child => {
    child.stdout.write("?? pending.txt\n");
    child.stderr.write("x".repeat(10000));
    child.emit("close", 128, null);
  }, async (_child, inspect) => {
    await assert.rejects(inspect(), error =>
      error.message.startsWith("Git status inspection failed (exit 128): ") && error.message.length < 4200,
    );
  });
});

test("status close with empty output reports clean", async () => {
  await withChild(child => child.emit("close", 0, null), async (_child, inspect) => {
    assert.equal(await inspect(), false);
  });
});
