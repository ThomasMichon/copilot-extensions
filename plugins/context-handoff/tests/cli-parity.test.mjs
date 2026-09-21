import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

const plugin = join(dirname(fileURLToPath(import.meta.url)), "..");
const cli = join(
  plugin, "extensions", "context-handoff", "handoff-cli.mjs",
);

function withRepository(fn) {
  const root = mkdtempSync(join(tmpdir(), "context-handoff-cli-"));
  try {
    writeFileSync(join(root, ".git"), "gitdir: elsewhere\n");
    fn(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function withIsolatedHome(fn) {
  const home = mkdtempSync(join(tmpdir(), "context-handoff-cli-home-"));
  try {
    const homeDrive = home.slice(0, 2);
    const homePath = home.slice(2);
    fn({
      HOME: home,
      USERPROFILE: home,
      HOMEDRIVE: homeDrive,
      HOMEPATH: homePath,
    });
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

test("payload-local CLI exposes the extension fallback flow", () => {
  const result = spawnSync(process.execPath, [cli, "help"], {
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  for (const command of ["facts", "save", "trigger", "consume", "check-heads", "retry-cutover", "sync-worktree"]) {
    assert.match(result.stdout, new RegExp(`\\b${command}\\b`));
  }
  assert.match(result.stdout, /--locator/);
  assert.match(result.stdout, /"task:<id>"/);
  assert.match(result.stdout, /"file:<id>"/);
  assert.match(result.stdout, /--task-id/);
  assert.match(result.stdout, /--handoff-token/);
  assert.doesNotMatch(result.stdout, /\bcontinue\b/);
  assert.match(result.stdout, /\bretry-cutover\b/);
});

test("trigger requires either markdown input or a stored handoff token", () => {
  withRepository((root) => withIsolatedHome((homeEnv) => {
    const missing = spawnSync(
      process.execPath,
      [cli, "trigger", "--session-id", "predecessor-session"],
      { cwd: root, encoding: "utf8", env: { ...process.env, ...homeEnv } },
    );
    assert.equal(missing.status, 2);
    assert.match(missing.stderr, /pass --prompt-file\/--prompt\/stdin or --handoff-token/);
  }));
});

test("CLI handoff commands refuse mode=off repositories", () => {
  withRepository((root) => {
    mkdirSync(join(root, ".context-handoff"));
    writeFileSync(
      join(root, ".context-handoff", "config.yaml"),
      "mode: off\n",
    );

    withIsolatedHome((homeEnv) => {
      for (const args of [
        ["save", "--session-id", "session-1", "--prompt", "handoff body"],
        ["trigger", "--session-id", "session-1", "--prompt", "handoff body"],
        ["consume", "--session-id", "session-1", "--task-id", "task-1"],
        ["retry-cutover", "--session-id", "session-1"],
      ]) {
        const result = spawnSync(process.execPath, [cli, ...args], {
          cwd: root,
          encoding: "utf8",
          env: { ...process.env, ...homeEnv },
        });
        assert.equal(result.status, 1, `${args[0]}: ${result.stderr}`);
        assert.match(result.stderr, /context handoff is disabled/i);
      }
    });
  });
});

test("consume requires exactly one recovery target", () => {
  withRepository((root) => withIsolatedHome((homeEnv) => {
    const common = { cwd: root, encoding: "utf8", env: { ...process.env, ...homeEnv } };
    const missing = spawnSync(
      process.execPath,
      [cli, "consume", "--session-id", "successor-session"],
      common,
    );
    assert.equal(missing.status, 2);
    assert.match(missing.stderr, /exactly one of --locator/);

    const ambiguous = spawnSync(
      process.execPath,
      [
        cli,
        "consume",
        "--session-id",
        "successor-session",
        "--task-id",
        "task-1",
        "--handoff-id",
        "handoff-1",
      ],
      common,
    );
    assert.equal(ambiguous.status, 2);
    assert.match(ambiguous.stderr, /exactly one of --locator/);

    const deferredFile = spawnSync(
      process.execPath,
      [
        cli,
        "consume",
        "--session-id",
        "successor-session",
        "--locator",
        "file:handoff-1",
        "--defer-complete",
      ],
      common,
    );
    assert.equal(deferredFile.status, 2);
    assert.match(deferredFile.stderr, /only valid with a task target/);
  }));
});

test("extension and CLI delegate storage, signaling, and consumption to the same core", () => {
  const source = readFileSync(cli, "utf8");
  for (const shared of [
    "checkHeadAlignment",
    "retryStoredHandoffCutover",
    "storeHandoff",
    "buildSeedForStored",
    "triggerHandoff",
    "consumeFileHandoff",
    "consumeDispatchHandoffTask",
    "formatConsumeResult",
    "attemptWorktreeSync",
  ]) {
    assert.match(source, new RegExp(`\\b${shared}\\b`));
  }
  assert.doesNotMatch(source, /runHandoffCutover/);
});

test("sync-worktree shares the same lock/rebase-safe helper the force-tier path uses", async () => {
  // Real regression this guards: without a shared entry point, the
  // skill-guided flow invoked `agent-worktrees git sync` directly, bypassing
  // attemptWorktreeSync's lock and rebase check entirely -- a force-tier
  // sync and a skill-guided sync could then race on the same worktree.
  const root = mkdtempSync(join(tmpdir(), "context-handoff-cli-sync-"));
  try {
    spawnSync("git", ["init", "-q"], { cwd: root });
    spawnSync("git", ["config", "user.email", "test@example.com"], { cwd: root });
    spawnSync("git", ["config", "user.name", "Test"], { cwd: root });
    writeFileSync(join(root, "file.txt"), "one\n");
    spawnSync("git", ["add", "."], { cwd: root });
    spawnSync("git", ["commit", "-q", "-m", "initial"], { cwd: root });
    const result = spawnSync(
      process.execPath, [cli, "sync-worktree", "--json", "--cwd", root],
      { encoding: "utf8" },
    );
    const parsed = JSON.parse(result.stdout);
    // No reachable agent-worktrees catalog in this bare test environment,
    // so it fails honestly rather than fabricating success -- proves the
    // command actually reached attemptWorktreeSync's real logic.
    assert.equal(parsed.attempted, true);
    assert.equal(parsed.synced, false);
    assert.match(parsed.reason, /sync failed|unavailable/);
    // A real sync failure must exit nonzero -- a caller using this command
    // as a gate must see a failure exit status, not a silent 0. Also
    // confirms the JSON write was NOT truncated by an immediate
    // process.exit(): stdout parsed above as complete, valid JSON.
    assert.equal(result.status, 1);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("fallback remains payload-only with no installed runtime", () => {
  const manifest = JSON.parse(
    readFileSync(join(plugin, "plugin.json"), "utf8"),
  );
  assert.equal(manifest.runtimeScope, "none");
  assert.equal(manifest.extensions, undefined);
  assert.equal(existsSync(join(plugin, "pyproject.toml")), false);
  assert.equal(existsSync(join(plugin, "scripts", "install.sh")), false);
  assert.equal(existsSync(join(plugin, "scripts", "install.ps1")), false);
  assert.equal(
    readFileSync(join(plugin, "README.md"), "utf8").includes(
      "There is **no** installed runtime, venv, binstub",
    ),
    true,
  );
});
