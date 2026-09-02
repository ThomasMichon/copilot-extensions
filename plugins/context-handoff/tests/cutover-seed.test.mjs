import { test } from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import {
  existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync,
} from "node:fs";
import { join } from "node:path";
import {
  MAX_CUTOVER_SEED_LENGTH,
  buildCutoverSeed,
  leadFrom,
  recoveryCommandFor,
} from "../extensions/context-handoff/cutover-seed.mjs";

test("leadFrom preserves a stable task-first title lead", () => {
  assert.equal(leadFrom("Fix the widget"), "Task: Fix the widget");
  assert.equal(leadFrom("Continue: Fix it"), "Task: Fix it");
  assert.equal(leadFrom(""), "Task: Continue the current work");
});

test("task seed is a bounded ASCII three-part locator", () => {
  const seed = buildCutoverSeed(
    "task", "task-42", leadFrom("Fix the widget"),
  );
  assert.match(seed, /^Task: Fix the widget \| Recommendation:/);
  assert.match(seed, /Recovery: node -e /);
  assert.match(seed, /copilot-extensions/);
  assert.match(seed, /handoff-cli\.mjs/);
  assert.match(seed, /consume --task-id task-42 --defer-complete$/);
  assert.doesNotMatch(seed, /Recovery: agent-dispatch consume/);
  assert.equal(seed.split(" | ").length, 3);
  assert.ok(seed.length <= MAX_CUTOVER_SEED_LENGTH);
  assert.ok(!seed.includes("\n"));
  assert.ok(!/[^\x00-\x7F]/.test(seed));
});

test("file seed carries one ASCII-safe payload CLI recovery command", () => {
  const command = recoveryCommandFor("file", "handoff-1", {
    handoffCliPath: "C:\\Users\\Jos\u00e9\\handoff-cli.mjs",
  });

  const seed = buildCutoverSeed("file", "handoff-1", leadFrom("Continue"), {
    handoffCliPath: "C:\\Users\\Jos\u00e9\\handoff-cli.mjs",
  });
  assert.match(command, /^node -e /);
  assert.match(command, /require\('os'\)\.homedir\(\)/);
  assert.match(command, /copilot-extensions/);
  assert.match(command, /handoff-cli\.mjs/);
  assert.match(command, /consume --handoff-id handoff-1$/);
  assert.ok(!/[^\x00-\x7F]/.test(command));
  assert.doesNotMatch(command, /Jos/);
  assert.ok(seed.endsWith(`Recovery: ${command}`));
  assert.ok(!seed.includes("## Session Continuation"));
  assert.ok(seed.length <= MAX_CUTOVER_SEED_LENGTH);
});

test("ASCII-safe resolver executes from a Unicode home path", () => {
  const base = mkdtempSync(join(process.cwd(), ".test-unicode-home-"));
  const home = join(base, "\u7528\u6237");
  const plugin = join(
    home, ".copilot", "installed-plugins", "copilot-extensions",
    "context-handoff",
  );
  const cli = join(
    plugin, "extensions", "context-handoff", "handoff-cli.mjs",
  );
  const output = join(base, "args.json");
  try {
    mkdirSync(join(plugin, "extensions", "context-handoff"), {
      recursive: true,
    });

    writeFileSync(join(plugin, "plugin.json"), JSON.stringify({
      name: "context-handoff",
      repository: "https://github.com/ThomasMichon/copilot-extensions",
    }));
    writeFileSync(
      cli,
      "import { writeFileSync } from 'node:fs';" +
        "writeFileSync(process.env.RECOVERY_ARGS_OUT," +
        "JSON.stringify(process.argv.slice(2)));",
    );
    execSync(recoveryCommandFor("file", "handoff-unicode"), {
      shell: true,
      stdio: "pipe",
      env: {
        ...process.env,
        HOME: home,
        USERPROFILE: home,
        RECOVERY_ARGS_OUT: output,
      },
    });
    assert.deepEqual(JSON.parse(readFileSync(output, "utf8")), [
      "consume", "--handoff-id", "handoff-unicode",
    ]);
  } finally {
    rmSync(base, { recursive: true, force: true });
  }
});

test("payload resolver rejects a same-named plugin with wrong provenance", () => {
  const base = mkdtempSync(join(process.cwd(), ".test-wrong-marketplace-"));
  const plugin = join(
    base, ".copilot", "installed-plugins", "copilot-extensions",
    "context-handoff",
  );
  const cli = join(
    plugin, "extensions", "context-handoff", "handoff-cli.mjs",
  );
  const output = join(base, "should-not-run");
  try {
    mkdirSync(join(plugin, "extensions", "context-handoff"), {
      recursive: true,
    });
    writeFileSync(join(plugin, "plugin.json"), JSON.stringify({
      name: "context-handoff",
      repository: "https://example.invalid/other/context-handoff",
    }));
    writeFileSync(
      cli,
      "import { writeFileSync } from 'node:fs';" +
        "writeFileSync(process.env.RECOVERY_ARGS_OUT,'ran');",
    );
    assert.throws(
      () => execSync(recoveryCommandFor("file", "handoff-wrong"), {
        shell: true,
        stdio: "pipe",
        env: {
          ...process.env,
          HOME: base,
          USERPROFILE: base,
          RECOVERY_ARGS_OUT: output,
        },
      }),
    );
    assert.equal(existsSync(output), false);
  } finally {
    rmSync(base, { recursive: true, force: true });
  }
});

test("long titles are compacted without changing the recovery command", () => {
  const command = "agent-dispatch consume task-99 --defer-complete";
  const seed = buildCutoverSeed(
    "task",
    "task-99",
    leadFrom("x".repeat(3000)),
    { recoveryCommand: command },
  );
  assert.ok(seed.length <= MAX_CUTOVER_SEED_LENGTH);
  assert.ok(seed.endsWith(`Recovery: ${command}`));
});

test("an impossible recovery command fails instead of truncating it", () => {
  assert.throws(
    () => buildCutoverSeed(
      "task", "task-1", leadFrom("x"),
      { recoveryCommand: "x".repeat(MAX_CUTOVER_SEED_LENGTH) },
    ),
    /exceeds 1024/,
  );
});

test("recovery commands reject non-ASCII instead of corrupting it", () => {
  assert.throws(
    () => buildCutoverSeed(
      "task", "task-1", leadFrom("x"),
      { recoveryCommand: "echo caf\u00e9" },
    ),
    /single-line ASCII/,
  );
});
