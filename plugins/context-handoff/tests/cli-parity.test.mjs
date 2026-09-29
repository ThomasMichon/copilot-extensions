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

test("trigger --force is parsed as a boolean flag and reaches triggerHandoff", () => {
  // #mux-companion-manual-cutover-diagnostics: --force must never shell out
  // to real provisioning as a side effect of merely being parsed -- the
  // actual arming behavior is covered by handoff-core.test.mjs's fully
  // mocked (noteHandoff/logActivity/requestBridge injected) tests. This is
  // a structural check only: the CLI passes `force: Boolean(args.force)`
  // through to triggerHandoff, and "force" is registered as a boolean flag
  // (not `--force <value>`, which would swallow the next argv token).
  const source = readFileSync(cli, "utf-8");
  assert.match(source, /"no-task", "json", "defer-complete", "force"/);
  assert.match(source, /force: Boolean\(args\.force\)/);
});

test("CLI handoff commands are never refused for mode=off -- only for unrelated reasons", () => {
  // Revised design decision: `mode: off` disables only automatic/unprompted
  // behavior (pressure nudges, the force tier), never the manual entry
  // points a session/operator explicitly invokes. These commands may still
  // fail here for OTHER reasons this sandbox doesn't set up (no adopted
  // agent-worktrees state, no stored handoff to find/retry) -- the only
  // thing under test is that "disabled" is never the reason.
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
        assert.doesNotMatch(
          result.stderr,
          /context handoff is disabled/i,
          `${args[0]} was refused for mode:off, but manual entry points must always work`,
        );
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
