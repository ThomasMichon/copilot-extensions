import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { gitStatusHasOutput } from "../extensions/context-handoff/git-status.mjs";

function fixture(run) {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  const launch = (command, args, options) => {
    assert.equal(command, "git");
    assert.deepEqual(args, ["status", "--porcelain", "--ignored", "--untracked-files=all"]);
    assert.equal(options.windowsHide, true);
    assert.equal(options.env.GIT_OPTIONAL_LOCKS, "0");
    assert.deepEqual(options.stdio, ["ignore", "pipe", "pipe"]);
    queueMicrotask(() => run(child));
    return child;
  };
  const env = { GIT_OPTIONAL_LOCKS: "1" };
  return gitStatusHasOutput("fixture", env, launch).finally(() => {
    assert.equal(env.GIT_OPTIONAL_LOCKS, "1", "the read-only setting is child-scoped");
  });
}

test("status drains output beyond the buffered exec limit and waits for exit", async () => {
  assert.equal(await fixture((child) => {
    const chunk = Buffer.alloc(65536, 120);
    for (let index = 0; index < 32; index++) child.stdout.emit("data", chunk);
    child.emit("close", 0, null);
  }), true);
});

test("empty successful status is clean", async () => {
  assert.equal(await fixture((child) => child.emit("close", 0, null)), false);
});

test("output before failed inspection is not accepted as a successful dirty check", async () => {
  await assert.rejects(fixture((child) => {
    child.stdout.emit("data", Buffer.from("?? partial\n"));
    child.emit("close", 128, null);
  }), /inspection failed \(exit 128\)/);
});

test("spawn failure and timeout refuse inspection", async () => {
  await assert.rejects(fixture((child) => {
    child.emit("error", new Error("spawn git ENOENT"));
    child.emit("close", -2, null);
  }), /ENOENT/);
  await assert.rejects(fixture((child) => {
    child.emit("close", null, "SIGTERM");
  }), /inspection failed \(signal SIGTERM\)/);
});
