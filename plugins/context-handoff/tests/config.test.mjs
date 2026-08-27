import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  describeError,
  findRepositoryRoot,
  loadContextHandoffConfig,
  parseThresholdConfig,
} from "../extensions/context-handoff/config.mjs";

function withRepository(fn) {
  const root = mkdtempSync(join(tmpdir(), "context-handoff-"));
  try {
    writeFileSync(join(root, ".git"), "gitdir: elsewhere\n");
    fn(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test("parses configured threshold percentages", () => {
  assert.deepEqual(
    parseThresholdConfig(
      "thresholds:\n  soft_percent: 65\n  hard_percent: 75\n",
    ),
    { softPercent: 65, hardPercent: 75 },
  );
});

test("partial configuration inherits portable defaults", () => {
  assert.deepEqual(
    parseThresholdConfig("thresholds:\n  soft_percent: 60\n"),
    { softPercent: 60, hardPercent: 70 },
  );
});

test("rejects unknown and unsafe configuration", () => {
  assert.throws(
    () => parseThresholdConfig("thresholds:\n  soft_tokens: 200000\n"),
    /expected thresholds\.soft_percent/,
  );
  assert.throws(
    () => parseThresholdConfig(
      "thresholds:\n  soft_percent: 70\n  hard_percent: 70\n",
    ),
    /softPercent must be less than hardPercent/,
  );
});

test("describes non-Error throw values safely", () => {
  assert.equal(describeError("read failed"), "read failed");
  assert.equal(describeError(new Error("parse failed")), "parse failed");
});

test("discovers config from a nested directory in a git worktree", () => {
  withRepository((root) => {
    const nested = join(root, "src", "feature");
    mkdirSync(nested, { recursive: true });
    mkdirSync(join(root, ".context-handoff"));
    writeFileSync(
      join(root, ".context-handoff", "config.yaml"),
      "thresholds:\n  soft_percent: 65\n  hard_percent: 75\n",
    );

    assert.equal(findRepositoryRoot(nested), root);
    const loaded = loadContextHandoffConfig(nested);
    assert.deepEqual(
      loaded.thresholds,
      { softPercent: 65, hardPercent: 75 },
    );
    assert.equal(loaded.warning, null);
  });
});

test("invalid repository config warns and uses defaults", () => {
  withRepository((root) => {
    mkdirSync(join(root, ".context-handoff"));
    writeFileSync(
      join(root, ".context-handoff", "config.yaml"),
      "thresholds:\n  hard_percent: 80\n",
    );

    const loaded = loadContextHandoffConfig(root);
    assert.deepEqual(
      loaded.thresholds,
      { softPercent: 55, hardPercent: 70 },
    );
    assert.match(loaded.warning, /using defaults/);
  });
});

test("symlinked repository config is rejected", { skip: process.platform === "win32" }, () => {
  withRepository((root) => {
    const target = join(root, "outside.yaml");
    writeFileSync(target, "thresholds:\n  soft_percent: 65\n");
    mkdirSync(join(root, ".context-handoff"));
    symlinkSync(target, join(root, ".context-handoff", "config.yaml"));

    const loaded = loadContextHandoffConfig(root);
    assert.match(loaded.warning, /non-symlink/);
  });
});

test("symlinked config directory is rejected", { skip: process.platform === "win32" }, () => {
  withRepository((root) => {
    const target = join(root, "redirected-config");
    mkdirSync(target);
    writeFileSync(
      join(target, "config.yaml"),
      "thresholds:\n  soft_percent: 65\n",
    );
    symlinkSync(target, join(root, ".context-handoff"), "dir");

    const loaded = loadContextHandoffConfig(root);
    assert.match(loaded.warning, /non-symlink directory/);
  });
});
