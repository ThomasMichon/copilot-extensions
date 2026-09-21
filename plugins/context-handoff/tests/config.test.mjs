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
  findRepositoryRootAsync,
  loadContextHandoffConfig,
  loadContextHandoffConfigAsync,
  parseContextHandoffConfig,
  parseThresholdConfig,
} from "../extensions/context-handoff/config.mjs";

function withRepository(fn) {
  const root = mkdtempSync(join(tmpdir(), "context-handoff-"));
  const home = mkdtempSync(join(tmpdir(), "context-handoff-home-"));
  try {
    writeFileSync(join(root, ".git"), "gitdir: elsewhere\n");
    fn(root, home);
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(home, { recursive: true, force: true });
  }
}

// Async twin of withRepository: `fn` may return a Promise, and cleanup waits
// for it before removing the fixture directories. `withRepository` above
// cleans up synchronously right after `fn` returns, which would delete the
// fixture out from under an async assertion's `.then()` microtask.
async function withRepositoryAsync(fn) {
  const root = mkdtempSync(join(tmpdir(), "context-handoff-"));
  const home = mkdtempSync(join(tmpdir(), "context-handoff-home-"));
  try {
    writeFileSync(join(root, ".git"), "gitdir: elsewhere\n");
    await fn(root, home);
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(home, { recursive: true, force: true });
  }
}

test("parses configured threshold percentages", () => {
  assert.deepEqual(
    parseThresholdConfig(
      "thresholds:\n  soft_percent: 65\n  hard_percent: 75\n  force_percent: 78\n",
    ),
    { softPercent: 65, hardPercent: 75, forcePercent: 78 },
  );
});

test("parses mode plus mixed threshold units", () => {
  assert.deepEqual(
    parseContextHandoffConfig(
      "mode: manual-only\nthresholds:\n  soft_tokens: 50000\n  hard_percent: 70\n  force_tokens: 90000\n",
    ),
    {
      mode: "manual-only",
      thresholds: {
        softTokens: 50_000,
        hardPercent: 70,
        forceTokens: 90_000,
      },
    },
  );
});

test("partial configuration inherits portable defaults", () => {
  assert.deepEqual(
    parseThresholdConfig("thresholds:\n  soft_percent: 60\n"),
    { softPercent: 60, hardPercent: 70, forcePercent: 79 },
  );
});

test("token thresholds are accepted as a full alternative", () => {
  assert.deepEqual(
    parseThresholdConfig(
      "thresholds:\n  soft_tokens: 50000\n  hard_tokens: 70000\n  force_tokens: 79000\n",
    ),
    {
      softTokens: 50_000,
      hardTokens: 70_000,
      forceTokens: 79_000,
    },
  );
});

test("rejects unknown and unsafe configuration", () => {
  assert.throws(
    () => parseContextHandoffConfig("thresholds:\n  mystery_percent: 200000\n"),
    /expected top-level mode or thresholds block entries/,
  );
  assert.throws(
    () => parseThresholdConfig(
      "thresholds:\n  soft_percent: 70\n  hard_percent: 70\n  force_percent: 79\n",
    ),
    /softPercent must be less than hardPercent/,
  );
  assert.throws(
    () => parseContextHandoffConfig("mode: sideways\n"),
    /mode must be one of/,
  );
});

test("describes non-Error throw values safely", () => {
  assert.equal(describeError("read failed"), "read failed");
  assert.equal(describeError(new Error("parse failed")), "parse failed");
});

test("discovers config from a nested directory in a git worktree", () => {
  withRepository((root, home) => {
    const nested = join(root, "src", "feature");
    mkdirSync(nested, { recursive: true });
    mkdirSync(join(root, ".context-handoff"));
    writeFileSync(
      join(root, ".context-handoff", "config.yaml"),
      "thresholds:\n  soft_percent: 65\n  hard_percent: 75\n",
    );

    assert.equal(findRepositoryRoot(nested), root);
    const loaded = loadContextHandoffConfig(nested, { homeDir: home });
    assert.equal(loaded.mode, "manual-only");
    assert.deepEqual(
      loaded.thresholds,
      { softPercent: 65, hardPercent: 75, forcePercent: 79 },
    );
    assert.equal(loaded.warning, null);
  });
});

test("invalid repository config warns and uses defaults", () => {
  withRepository((root, home) => {
    mkdirSync(join(root, ".context-handoff"));
    writeFileSync(
      join(root, ".context-handoff", "config.yaml"),
      "mode: manual-only\nthresholds:\n  hard_percent: 80\n",
    );

    const loaded = loadContextHandoffConfig(root, { homeDir: home });
    assert.equal(loaded.mode, "manual-only");
    assert.deepEqual(
      loaded.thresholds,
      { softPercent: 55, hardPercent: 70, forcePercent: 79 },
    );
    assert.match(loaded.warning, /using defaults/);
  });
});

test("user-level config is loaded when repository config is absent", () => {
  const home = mkdtempSync(join(tmpdir(), "context-handoff-home-"));
  try {
    withRepository((root) => {
      mkdirSync(join(home, ".context-handoff"), { recursive: true });
      writeFileSync(
        join(home, ".context-handoff", "config.yaml"),
        "mode: manual-only\nthresholds:\n  soft_tokens: 50000\n  hard_tokens: 75000\n  force_tokens: 90000\n",
      );

      const loaded = loadContextHandoffConfig(root, { homeDir: home });
      assert.equal(loaded.mode, "manual-only");
      assert.deepEqual(loaded.thresholds, {
        softTokens: 50_000,
        hardTokens: 75_000,
        forceTokens: 90_000,
      });
      assert.equal(loaded.warning, null);
      assert.equal(loaded.configPath, join(home, ".context-handoff", "config.yaml"));
    });
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("repository config overrides user-level config per key", () => {
  const home = mkdtempSync(join(tmpdir(), "context-handoff-home-"));
  try {
    withRepository((root) => {
      mkdirSync(join(home, ".context-handoff"), { recursive: true });
      writeFileSync(
        join(home, ".context-handoff", "config.yaml"),
        "mode: manual-only\nthresholds:\n  soft_tokens: 50000\n  hard_percent: 70\n  force_percent: 79\n",
      );
      mkdirSync(join(root, ".context-handoff"));
      writeFileSync(
        join(root, ".context-handoff", "config.yaml"),
        "mode: auto\nthresholds:\n  hard_tokens: 80000\n",
      );

      const loaded = loadContextHandoffConfig(root, { homeDir: home });
      assert.equal(loaded.mode, "auto");
      assert.deepEqual(loaded.thresholds, {
        softTokens: 50_000,
        hardTokens: 80_000,
        forcePercent: 79,
      });
      assert.equal(loaded.warning, null);
      assert.equal(loaded.configPath, join(root, ".context-handoff", "config.yaml"));
    });
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("symlinked repository config is rejected", { skip: process.platform === "win32" }, () => {
  withRepository((root, home) => {
    const target = join(root, "outside.yaml");
    writeFileSync(target, "thresholds:\n  soft_percent: 65\n");
    mkdirSync(join(root, ".context-handoff"));
    symlinkSync(target, join(root, ".context-handoff", "config.yaml"));

    const loaded = loadContextHandoffConfig(root, { homeDir: home });
    assert.match(loaded.warning, /non-symlink/);
  });
});

test("symlinked config directory is rejected", { skip: process.platform === "win32" }, () => {
  withRepository((root, home) => {
    const target = join(root, "redirected-config");
    mkdirSync(target);
    writeFileSync(
      join(target, "config.yaml"),
      "thresholds:\n  soft_percent: 65\n",
    );
    symlinkSync(target, join(root, ".context-handoff"), "dir");

    const loaded = loadContextHandoffConfig(root, { homeDir: home });
    assert.match(loaded.warning, /non-symlink directory/);
  });
});

// aperture-labs#7291: findRepositoryRootAsync / loadContextHandoffConfigAsync
// are the non-blocking twins extension.mjs's load-time init uses (run
// concurrently with joinSession() rather than blocking the event loop before
// it). They must agree with the synchronous originals on every outcome.
test("async findRepositoryRoot agrees with the synchronous original", async () => {
  await withRepositoryAsync(async (root) => {
    const nested = join(root, "src", "feature");
    mkdirSync(nested, { recursive: true });
    const asyncRoot = await findRepositoryRootAsync(nested);
    assert.equal(asyncRoot, findRepositoryRoot(nested));
    assert.equal(asyncRoot, root);
  });
});

test("async loader discovers config from a nested directory in a git worktree", async () => {
  await withRepositoryAsync(async (root, home) => {
    const nested = join(root, "src", "feature");
    mkdirSync(nested, { recursive: true });
    mkdirSync(join(root, ".context-handoff"));
    writeFileSync(
      join(root, ".context-handoff", "config.yaml"),
      "thresholds:\n  soft_percent: 65\n  hard_percent: 75\n",
    );

    const loaded = await loadContextHandoffConfigAsync(nested, { homeDir: home });
    assert.equal(loaded.mode, "manual-only");
    assert.deepEqual(
      loaded.thresholds,
      { softPercent: 65, hardPercent: 75, forcePercent: 79 },
    );
    assert.equal(loaded.warning, null);
    assert.deepEqual(loaded, loadContextHandoffConfig(nested, { homeDir: home }));
  });
});

test("async loader falls back to defaults with an invalid config, matching the sync loader's warning", async () => {
  await withRepositoryAsync(async (root, home) => {
    mkdirSync(join(root, ".context-handoff"));
    writeFileSync(
      join(root, ".context-handoff", "config.yaml"),
      "mode: manual-only\nthresholds:\n  hard_percent: 80\n",
    );

    const loaded = await loadContextHandoffConfigAsync(root, { homeDir: home });
    assert.equal(loaded.mode, "manual-only");
    assert.match(loaded.warning, /using defaults/);
    assert.deepEqual(loaded, loadContextHandoffConfig(root, { homeDir: home }));
  });
});

test("async loader returns the same default config as the sync loader outside any repository", async () => {
  const outside = mkdtempSync(join(tmpdir(), "context-handoff-none-"));
  const home = mkdtempSync(join(tmpdir(), "context-handoff-none-home-"));
  try {
    const loaded = await loadContextHandoffConfigAsync(outside, { homeDir: home });
    assert.deepEqual(loaded, loadContextHandoffConfig(outside, { homeDir: home }));
  } finally {
    rmSync(outside, { recursive: true, force: true });
    rmSync(home, { recursive: true, force: true });
  }
});
