import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";

// The module reads AGENT_WORKTREES_QUERY_TIMEOUT_MS at import time (a plain
// top-level const), so the override case needs a fresh process -- importing
// it twice in one test process would just hit Node's module cache.
const MODULE_URL = new URL(
  "../extensions/context-handoff/cli-timeouts.mjs", import.meta.url,
).href;

function readTimeoutInSubprocess(env) {
  const script =
    `import("${MODULE_URL}").then(` +
    `(m) => console.log(m.AGENT_WORKTREES_QUERY_TIMEOUT_MS));`;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    encoding: "utf8",
    env: { ...process.env, ...env },
  });
  assert.equal(result.status, 0, result.stderr);
  return Number(result.stdout.trim());
}

test("defaults to 45s (real margin above observed 24-27s under load, #6724)", () => {
  const env = { ...process.env };
  delete env.AGENT_WORKTREES_QUERY_TIMEOUT_MS;
  assert.equal(readTimeoutInSubprocess(env), 45_000);
});

test("AGENT_WORKTREES_QUERY_TIMEOUT_MS overrides the default", () => {
  assert.equal(
    readTimeoutInSubprocess({ AGENT_WORKTREES_QUERY_TIMEOUT_MS: "60000" }),
    60_000,
  );
});

test("a non-numeric or non-positive override falls back to the default", () => {
  assert.equal(
    readTimeoutInSubprocess({ AGENT_WORKTREES_QUERY_TIMEOUT_MS: "not-a-number" }),
    45_000,
  );
  assert.equal(
    readTimeoutInSubprocess({ AGENT_WORKTREES_QUERY_TIMEOUT_MS: "0" }),
    45_000,
  );
  assert.equal(
    readTimeoutInSubprocess({ AGENT_WORKTREES_QUERY_TIMEOUT_MS: "-5" }),
    45_000,
  );
});
