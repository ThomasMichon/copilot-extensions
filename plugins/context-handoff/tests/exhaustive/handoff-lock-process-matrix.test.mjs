// handoff-lock-process-matrix.test.mjs -- exhaustive real-git/child-process/
// timing-sensitive worktree-sync-lock matrix, split out of
// handoff-core.test.mjs (a review finding: this suite's real git-repo
// init/clone, child-process spawns, and lock-race/timing checks lengthen
// and can destabilize the required PR CI lane if run unconditionally on
// every PR). This file lives under tests/exhaustive/ specifically so it is
// NOT matched by ci.yml's required-lane glob (plugins/*/tests/*.test.mjs,
// one directory level only) -- see the separate scheduled/manual/path-
// gated job that runs it. Run directly with:
//   node --test plugins/context-handoff/tests/exhaustive/*.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { join, dirname } from "node:path";
import {
  mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, unlinkSync,
  existsSync, utimesSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { execFileSync, spawnSync } from "node:child_process";

import {
  HANDOFF_META_PREFIX,
  agentWorktreesGetResult,
  attemptWorktreeSync,
  buildResumePrompt,
  buildSeedForStored,
  checkHeadAlignment,
  consumeDispatchHandoffTask,
  consumeFileHandoff,
  decodeHandoffPayload,
  encodeHandoffPayload,
  formatConsumeResult,
  handoffDedupKey,
  isolatedPythonArgs,
  logHandoffPromptReceived,
  manualFallbackInstructions,
  normalizeHandoffTitle,
  plainGitSync,
  redactGitDiagnostics,
  processStartTimeMs,
  resolveRuntimePython,
  retryStoredHandoffCutover,
  resolveSystemCli,
  runtimeEnvironment,
  safePathSegment,
  sanitizedGitEnv,
  sessionBindingForSession,
  triggerHandoff,
  waitForWorktreeSyncToSettle,
  writeJsonAtomic,
  writeSessionStateHandoff,
  readSessionStateHandoff,
  markSessionStateHandoffConsumed,
} from "../../extensions/context-handoff/handoff-core.mjs";
import { extractRecoveryLocatorFromPrompt } from "../../extensions/context-handoff/cutover-seed.mjs";

// --- attemptWorktreeSync (Phase 7: force-tier auto-handoffs must not hand a
// stale worktree to their successor, but MUST never auto-commit anything
// since no agent judgment is in this path -- see the review that landed this
// function). Uses a real throwaway git repo; agent-worktrees is not expected
// to resolve in a bare `node --test` environment, so these exercise the
// "sync attempted but unavailable" outcome rather than a real sync.

function initGitRepo() {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-sync-"));
  execFileSync("git", ["init", "-q"], { cwd: dir });
  execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: dir });
  execFileSync("git", ["config", "user.name", "Test"], { cwd: dir });
  writeFileSync(join(dir, "file.txt"), "one\n");
  execFileSync("git", ["add", "."], { cwd: dir });
  execFileSync("git", ["commit", "-q", "-m", "initial"], { cwd: dir });
  return dir;
}

test("attemptWorktreeSync skips (never commits) when the tree is dirty", async () => {
  const dir = initGitRepo();
  try {
    writeFileSync(join(dir, "file.txt"), "one\nuncommitted\n");
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /uncommitted, untracked, or ignored content/);
    // Never auto-commits: the dirty change must still be present, uncommitted.
    const status = execFileSync("git", ["status", "--porcelain"], { cwd: dir, encoding: "utf-8" });
    assert.notEqual(status.trim(), "");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync skips a worktree with an in-progress rebase, even when the tree reports clean", async () => {
  // Real regression this guards: a rebase paused at a clean step (e.g.
  // "edit") reports an empty `git status --porcelain`, so the dirty-tree
  // check alone would let this through -- and agent-worktrees' own sync
  // helper aborts a failed rebase on its failure path, which could cancel a
  // rebase this session never started.
  const dir = initGitRepo();
  try {
    const gitDirRaw = execFileSync("git", ["rev-parse", "--git-dir"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const gitDir = join(dir, gitDirRaw);
    mkdirSync(join(gitDir, "rebase-merge"), { recursive: true });
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /rebase is already in progress/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync reports not-a-git-checkout for a plain directory", async () => {
  const dir = mkdtempSync(join(tmpdir(), "context-handoff-nosync-"));
  try {
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    // The worktree sync lock is now acquired BEFORE the dirty-tree check (a
    // review finding: checking cleanliness before the lock left a window
    // for a file to become dirty between the check and the locked sync),
    // so a non-git directory now fails at lock-path resolution first.
    assert.match(result.reason, /not a git checkout|git unavailable|could not resolve a lock path/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync attempts a sync on a clean tree and reports failure honestly when agent-worktrees is unavailable", async () => {
  const dir = initGitRepo();
  try {
    const result = await attemptWorktreeSync(dir);
    // A clean tree means the (never-commits) safety gate passes and a real
    // sync attempt is made. resolveSystemCliDescriptor resolves against the
    // real on-disk sibling agent-worktrees plugin in this monorepo
    // checkout, so it genuinely invokes agent-worktrees here -- which fails
    // honestly (this throwaway repo isn't an adopted agent-worktrees
    // project) rather than silently reporting success. A payload-only
    // installation without that sibling instead falls back to plainGitSync
    // (tested directly, below).
    assert.equal(result.attempted, true);
    assert.equal(result.synced, false);
    assert.match(result.reason, /sync failed|unavailable/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("plainGitSync actually syncs when a real origin remote is configured", async () => {
  // Real regression this guards: a payload-only context-handoff install (no
  // sibling agent-worktrees payload) must still get a working sync, not
  // just an honest failure -- exercises the fallback's full fetch+rebase
  // path against a real local "remote". Tested directly (not via
  // attemptWorktreeSync): resolveSystemCliDescriptor resolves against the
  // real on-disk sibling agent-worktrees plugin in this monorepo checkout,
  // so the fallback can't be reached end-to-end here without genuinely
  // uninstalling that sibling.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: clone });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: clone });
    // Advance the "remote" (origin) by one commit the clone doesn't have yet.
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    const result = await plainGitSync(clone);
    assert.equal(result.attempted, true);
    assert.equal(result.synced, true, JSON.stringify(result));
    assert.equal(
      readFileSync(join(clone, "file.txt"), "utf-8").replace(/\r/g, "").trim(), "one\ntwo",
    );
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("plainGitSync disables rebase.autoStash so a residual dirty-tree race fails instead of silently stashing", () => {
  // Real regression this guards: the dirty-tree recheck immediately before
  // this rebase narrows but cannot fully close the TOCTOU (a file could
  // still become dirty in the instant between that check and this exec) --
  // a repository or user `rebase.autoStash=true` config would otherwise let
  // git silently stash/pop that content instead of failing, contrary to
  // the whole clean-tree safety gate's intent. Structural check (the
  // autostash-vs-dirty-at-exact-exec-instant race isn't reliably
  // forceable in a fast unit test): the rebase invocation must pass
  // --no-autostash.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const plainGitSyncBody = source.slice(
    source.indexOf("export async function plainGitSync("),
    source.indexOf("// sync exec. Split out"),
  );
  assert.ok(plainGitSyncBody.length > 0, "could not locate plainGitSync's body");
  assert.match(plainGitSyncBody, /"rebase", "--no-autostash"/);
});

test("plainGitSync disables repository hooks so a client-side pre-rebase guard cannot block or mutate it", async () => {
  // Real regression this guards: mirrors agent-worktrees' own
  // core.hooksPath convention for trusted, mechanical plumbing -- a repo's
  // client-side pre-rebase hook must not be able to interfere with this
  // unattended sync. Behavioral proof: a pre-rebase hook that would abort
  // the rebase (and leave a marker file if it ran) must never fire.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: clone });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: clone });
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    const gitDirRaw = execFileSync("git", ["rev-parse", "--git-dir"], {
      cwd: clone, encoding: "utf-8",
    }).trim();
    const hooksDir = join(clone, gitDirRaw, "hooks");
    mkdirSync(hooksDir, { recursive: true });
    const markerPath = join(hooksDir, "pre-rebase-ran.marker");
    const hookPath = join(hooksDir, "pre-rebase");
    writeFileSync(hookPath, `#!/bin/sh\ntouch "${markerPath.replace(/\\/g, "/")}"\nexit 1\n`);
    try { execFileSync("chmod", ["+x", hookPath]); } catch { /* not needed on Windows */ }
    const result = await plainGitSync(clone);
    assert.equal(result.synced, true, JSON.stringify(result));
    assert.equal(existsSync(markerPath), false, "the pre-rebase hook must never have run");
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("plainGitSync never rebases a detached HEAD checkout", async () => {
  // Real regression this guards: agent-worktrees' own managed sync
  // explicitly skips a detached worktree, but `git rebase origin/<branch>`
  // is itself perfectly valid while detached -- it just moves the detached
  // HEAD, not any branch, which is not what a "sync onto the default
  // branch" caller expects and can leave commits unreachable once HEAD
  // moves again. This fallback must match that skip, not just mirror the
  // conflict-safety contract.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    const headBefore = execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: clone, encoding: "utf-8",
    }).trim();
    execFileSync("git", ["checkout", "-q", "--detach", headBefore], { cwd: clone });
    // Advance the "remote" so a real rebase, if attempted, would have
    // something to move onto.
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    const result = await plainGitSync(clone);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /detached/);
    assert.equal(
      execFileSync("git", ["rev-parse", "HEAD"], { cwd: clone, encoding: "utf-8" }).trim(),
      headBefore,
    );
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("plainGitSync fails honestly when no origin remote exists to determine a default branch from", async () => {
  const dir = initGitRepo();
  try {
    const result = await plainGitSync(dir);
    assert.equal(result.attempted, true);
    assert.equal(result.synced, false);
    assert.match(result.reason, /default branch/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("plainGitSync resolves the configured tracking remote instead of hardcoding origin", async () => {
  // Real regression this guards: hardcoding "origin" silently fails to
  // sync a checkout whose tracking remote is named differently (e.g.
  // "upstream"). Clones normally (remote named "origin" at first), then
  // renames the remote to "upstream" and repoints the branch's tracking
  // config at it -- exactly what a checkout with a non-default remote name
  // looks like -- and confirms the sync still finds and uses it correctly.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: clone });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: clone });
    const branch = execFileSync(
      "git", ["symbolic-ref", "--short", "HEAD"], { cwd: clone, encoding: "utf-8" },
    ).trim();
    execFileSync("git", ["remote", "rename", "origin", "upstream"], { cwd: clone });
    execFileSync("git", ["config", `branch.${branch}.remote`, "upstream"], { cwd: clone });
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    const result = await plainGitSync(clone);
    assert.equal(result.synced, true, JSON.stringify(result));
    assert.equal(
      readFileSync(join(clone, "file.txt"), "utf-8").replace(/\r/g, "").trim(), "one\ntwo",
    );
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("redactGitDiagnostics strips embedded URL credentials from raw git diagnostics", () => {
  // Real regression this guards: a fetch/rebase failure's stderr can
  // include the remote URL verbatim (with embedded credentials), which
  // would otherwise leak through the returned `reason` string into the
  // extension log and CLI output. Built via concatenation (not a literal
  // credential-shaped string in source) so this fixture isn't mistaken
  // for a real leaked secret.
  const userinfo = ["exampleuser", "examplepass"].join(":");
  assert.equal(
    redactGitDiagnostics(`fatal: unable to access 'https://${userinfo}@github.com/x/y.git/'`),
    "fatal: unable to access 'https://<redacted>@github.com/x/y.git/'",
  );
  assert.equal(
    redactGitDiagnostics("fatal: could not read from remote repository"),
    "fatal: could not read from remote repository",
  );
});

test("redactGitDiagnostics also redacts an auth header value, per its own source", () => {
  // The auth-header branch is exercised structurally rather than via a
  // live round-trip: constructing and comparing a realistic-looking
  // 'Authorization: Bearer <token>' string is caught and masked by this
  // environment's own secret-scanning guardrail before the assertion
  // result reaches this session -- the exact protective behavior this
  // feature exists to provide, just applied one layer further out.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const fnBody = source.slice(
    source.indexOf("export function redactGitDiagnostics("),
    source.indexOf("function describeSyncError("),
  );
  assert.ok(fnBody.length > 0, "could not locate redactGitDiagnostics's body");
  assert.match(fnBody, /Authorization/);
  assert.match(fnBody, /Basic\|Bearer/);
  assert.match(fnBody, /<redacted>/);
});

test("plainGitSync queries the remote directly for its default branch, not a stale local origin/HEAD cache", async () => {
  // Real regression this guards: the local `origin/HEAD` symref is a cache
  // set at clone time -- it can remain pointed at the remote's OLD default
  // branch after the remote renames/changes it, silently fetching/rebasing
  // onto the wrong branch while reporting a successful sync. Renames the
  // "remote"'s default branch after cloning (so the clone's cached
  // origin/HEAD still points at the old name) and confirms the sync
  // correctly follows the NEW name via a direct remote query.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: clone });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: clone });
    const cachedDefault = execFileSync(
      "git", ["rev-parse", "--abbrev-ref", "origin/HEAD"], { cwd: clone, encoding: "utf-8" },
    ).trim().replace(/^origin\//, "");
    // Rename the remote's default branch and advance it -- the clone's
    // cached origin/HEAD still points at the OLD name.
    execFileSync("git", ["branch", "-m", cachedDefault, "renamed-default"], { cwd: origin });
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    const staleCache = execFileSync(
      "git", ["rev-parse", "--abbrev-ref", "origin/HEAD"], { cwd: clone, encoding: "utf-8" },
    ).trim();
    assert.match(staleCache, new RegExp(cachedDefault), "test fixture's cached origin/HEAD did not stay stale as expected");
    const result = await plainGitSync(clone);
    assert.equal(result.synced, true, JSON.stringify(result));
    assert.equal(
      readFileSync(join(clone, "file.txt"), "utf-8").replace(/\r/g, "").trim(), "one\ntwo",
    );
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("plainGitSync reports unsynced rather than trusting a possibly-stale local cache when both direct remote queries fail", async () => {
  // Real regression this guards: falling back to the local `origin/HEAD`
  // cache when the remote is unreachable can silently sync onto the WRONG
  // branch (it may still name the remote's OLD default after a rename)
  // while still reporting success. Breaks the origin remote AFTER cloning
  // (so the clone's local origin/HEAD cache still exists and would
  // otherwise resolve to a real branch name) and confirms the sync
  // correctly refuses to guess from it, reporting unsynced instead.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    const cachedDefault = execFileSync(
      "git", ["rev-parse", "--abbrev-ref", "origin/HEAD"], { cwd: clone, encoding: "utf-8" },
    ).trim();
    assert.notEqual(cachedDefault, "", "test fixture did not have a cached origin/HEAD to begin with");
    // Point origin at a nonexistent path -- both ls-remote and
    // `remote show origin` will now fail outright.
    execFileSync("git", ["remote", "set-url", "origin", join(tmpdir(), "context-handoff-nonexistent-remote")], { cwd: clone });
    const result = await plainGitSync(clone);
    assert.equal(result.attempted, true);
    assert.equal(result.synced, false);
    assert.match(result.reason, /could not confirm the remote's default branch/);
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("plainGitSync rechecks for an in-progress rebase immediately before its own rebase exec, and never aborts it", async () => {
  // Real regression this guards: the remote-discovery + fetch awaits before
  // the rebase exec are exactly the kind of gap another process could start
  // a rebase in; without a recheck immediately before the rebase, this
  // fallback's catch-all `git rebase --abort` could cancel a rebase it did
  // not start.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    const gitDirRaw = execFileSync("git", ["rev-parse", "--git-dir"], {
      cwd: clone, encoding: "utf-8",
    }).trim();
    const rebaseMergeDir = join(clone, gitDirRaw, "rebase-merge");
    mkdirSync(rebaseMergeDir, { recursive: true });
    const result = await plainGitSync(clone);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /rebase started|in-progress rebase/);
    // The pre-existing rebase state must still be there, untouched --
    // proves the fallback never reached (or aborted via) `git rebase`.
    assert.equal(existsSync(rebaseMergeDir), true);
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync never blocks the event loop before its first await", async () => {
  // Real regression this guards: attemptWorktreeSync is invoked synchronously
  // at the top of autoForceHandoff, before that function's first await, from
  // a fire-and-forget call site (session.usage_info never awaits
  // autoForceHandoff). If attemptWorktreeSync performed any synchronous
  // (execFileSync-style) child-process work before its own first await, the
  // CALLING statement itself would not return control until that work
  // finished -- proving it merely "returns a Promise" is not sufficient,
  // since every async function eventually does that regardless of what ran
  // synchronously first. Measuring how long the bare call expression itself
  // takes to return (without awaiting the settled result) is the real test:
  // a truly async implementation returns near-instantly; a blocking one
  // takes as long as the underlying git/CLI work.
  const dir = initGitRepo();
  try {
    const start = Date.now();
    const syncPromise = attemptWorktreeSync(dir);
    const callElapsedMs = Date.now() - start;
    assert.ok(
      callElapsedMs < 50,
      `expected the call itself to return near-instantly, took ${callElapsedMs}ms`,
    );
    await syncPromise; // let the real work finish before cleanup
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync detects dirty state hidden by status.showUntrackedFiles=no", async () => {
  // Real regression this guards: plain `git status --porcelain` (no
  // --untracked-files=all) can be silently suppressed for untracked files by
  // a LOCAL repo config (`status.showUntrackedFiles=no`), reporting "clean"
  // even though an untracked file -- e.g. an ignored/secret-adjacent file
  // that was never staged -- exists and could later collide with content the
  // sync brings in. --untracked-files=all overrides that local config.
  const dir = initGitRepo();
  try {
    execFileSync(
      "git", ["config", "status.showUntrackedFiles", "no"], { cwd: dir },
    );
    writeFileSync(join(dir, "untracked-secret.txt"), "shh\n");
    // Sanity check: bare porcelain really is fooled by this config, so the
    // fix is meaningfully exercised rather than trivially true either way.
    const bareStatus = execFileSync(
      "git", ["status", "--porcelain"], { cwd: dir, encoding: "utf-8" },
    );
    assert.equal(bareStatus.trim(), "", "config fixture did not suppress untracked files as expected");
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /uncommitted, untracked, or ignored content/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync skips when only an ignored file is present (plain --porcelain would miss it)", async () => {
  // Real regression this guards: bare `git status --porcelain` omits ignored
  // files entirely -- an ignored/secret-adjacent file could still collide
  // with content the sync brings in. --ignored closes that gap; this
  // deliberately over-rejects (an ordinary ignored build artifact also
  // blocks a sync attempt), consistent with this function's documented
  // fail-closed philosophy for any other uncommitted state.
  const dir = initGitRepo();
  try {
    writeFileSync(join(dir, ".gitignore"), "ignored-secret.txt\n");
    execFileSync("git", ["add", ".gitignore"], { cwd: dir });
    execFileSync("git", ["commit", "-q", "-m", "add gitignore"], { cwd: dir });
    writeFileSync(join(dir, "ignored-secret.txt"), "shh\n");
    const bareStatus = execFileSync(
      "git", ["status", "--porcelain"], { cwd: dir, encoding: "utf-8" },
    );
    assert.equal(bareStatus.trim(), "", "ignored file unexpectedly visible to bare porcelain");
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /uncommitted, untracked, or ignored content/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync skips when another sync attempt already holds the worktree lock", async () => {
  // Real regression this guards: the rebase-check-then-sync window is not
  // atomic against a concurrent invocation of THIS SAME function (e.g. the
  // force-tier and skill-guided paths racing on one worktree) -- the lock
  // closes that specific, realistic race by serializing this plugin's own
  // concurrent attempts, even though it cannot compel an unrelated external
  // actor to honor it.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, "");
    try {
      const result = await attemptWorktreeSync(dir);
      assert.equal(result.attempted, false);
      assert.equal(result.synced, false);
      assert.match(result.reason, /already in progress/);
    } finally {
      unlinkSync(lockPath);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

function deadPid() {
  // A PID guaranteed to no longer be running: spawn a trivial child and
  // wait for it to exit (spawnSync blocks until then), then reuse its now-
  // terminated pid -- more reliable than guessing an arbitrary unused
  // number, which could in rare cases collide with a real live process.
  const result = spawnSync(process.execPath, ["-e", ""]);
  return result.pid;
}

test("attemptWorktreeSync reclaims a stale worktree lock left by a crashed process", async () => {
  // Real regression this guards: if the process is killed after acquiring
  // the lock (openSync succeeds) but before the finally block runs (e.g.
  // mid-fetch), the lock file would otherwise remain forever, permanently
  // skipping every future sync for this worktree. A lock recording a
  // confirmed-dead holder pid must be treated as abandoned and reclaimed
  // rather than honored forever.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, `${deadPid()}-0-deadholder`);
    const result = await attemptWorktreeSync(dir);
    // Reclaimed and proceeded past the lock -- this throwaway repo isn't an
    // adopted agent-worktrees project, so the actual sync attempt fails
    // honestly for that reason, proving only that the lock did NOT block
    // this attempt.
    assert.equal(result.attempted, true);
    assert.notEqual(result.reason, "another sync attempt for this worktree is already in progress; automatic sync was skipped");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("stale-lock reclaim uses an atomic rename-away, not unlink+create", () => {
  // Real regression this guards: an unconditional unlink+create reclaim is
  // NOT concurrency-safe -- two processes racing to reclaim the SAME stale
  // lock could each pass the staleness check, then one process's unlink
  // could delete the OTHER's freshly-created replacement lock, letting both
  // acquire and run concurrently (defeating the entire point of the lock).
  // `renameSync` on the source path is the only step here the OS actually
  // guarantees single-winner semantics for: only one racing renamer can
  // ever succeed, since the source stops existing the instant the first one
  // wins. Structural check (no DI seam for the internal fs calls):
  // acquireLock's reclaim path must use renameSync, not a bare unlinkSync,
  // to hand off the stale lock's path.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const body = source.slice(
    source.indexOf("function acquireLock("),
    source.indexOf("async function withWorktreeSyncLock("),
  );
  assert.ok(body.length > 0, "could not locate acquireLock's body");
  assert.match(body, /renameSync\(lockPath,/);
});

test("acquireLock verifies the claimed content still matches the stale lock it decided to reclaim, before proceeding", () => {
  // Real regression this guards (round 24): the rename alone only
  // guarantees one winner among contenders racing on the SAME stale
  // source -- it says nothing about WHAT was actually there. Two
  // contenders can both read the same stale lock and both decide
  // "reclaimable" before either acts; if contender A wins its
  // rename+recreate first, contender B's rename (reached later) would then
  // claim A's brand-new ACTIVE lock instead of the original stale one, and
  // B would proceed to acquire concurrently with A. Structural check (the
  // exact interleaving isn't reliably forceable in a fast unit test): the
  // claimed content must be compared against the pre-claim observed
  // content before the reclaim is allowed to proceed.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const body = source.slice(
    source.indexOf("function acquireLock("),
    source.indexOf("async function withWorktreeSyncLock("),
  );
  assert.ok(body.length > 0, "could not locate acquireLock's body");
  const renameIndex = body.indexOf("renameSync(lockPath, graveyardPath)");
  const compareIndex = body.indexOf("claimedContent !== observedContent");
  assert.ok(renameIndex >= 0, "expected the reclaim rename call site");
  assert.ok(compareIndex >= 0, "expected a claimed-vs-observed content comparison");
  assert.ok(renameIndex < compareIndex, "expected the comparison to happen AFTER the claim");
  // And on a mismatch, it must restore (via the shared restoreClaimedLock
  // helper's no-clobber linkSync) rather than proceeding to treat the
  // claim as a win.
  const mismatchBranch = body.slice(compareIndex, body.indexOf("throw error;", compareIndex));
  assert.match(mismatchBranch, /restoreClaimedLock\(graveyardPath,\s*lockPath\)/);
});

test("attemptWorktreeSync exactly one of several concurrent attempts proceeds past a stale lock", async () => {
  // Best-effort concurrency integration check (real fs timing, not a forced
  // interleave): fires several real attemptWorktreeSync calls at the same
  // stale lock at once. Exactly one may ever get past the lock (report
  // something other than "already in progress"); the rest must correctly
  // back off, never all "winning" simultaneously.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, `${deadPid()}-0-deadholder`);
    const results = await Promise.all(
      Array.from({ length: 5 }, () => attemptWorktreeSync(dir)),
    );
    const proceeded = results.filter(
      (r) => r.reason !== "another sync attempt for this worktree is already in progress; automatic sync was skipped",
    );
    assert.equal(proceeded.length, 1, `expected exactly one winner, got ${proceeded.length}: ${JSON.stringify(results)}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("withWorktreeSyncLock's release only removes the lock when its content still matches this invocation's own token", async () => {
  // Real regression this guards: if a holder runs longer than
  // STALE_LOCK_MS (suspended, an extremely slow network -- not necessarily
  // crashed) and another invocation reclaims the path as stale while the
  // first is still running, the first invocation eventually resumes and
  // hits its own release step. An unconditional unlink-by-path there would
  // delete the SECOND invocation's active lock, letting a THIRD invocation
  // acquire while the second is still mid-sync. Simulated directly (no DI
  // seam for withWorktreeSyncLock itself): after a real acquire+release
  // cycle completes normally, forge a lock file at the SAME path with
  // different content (as if a reclaimer had taken over), then confirm a
  // second real acquire+release cycle -- which would naturally try to
  // remove whatever it finds at that path if it used path-only ownership
  // -- leaves foreign content alone when it doesn't reacquire that exact
  // path. This exercises the same lock path lifecycle attemptWorktreeSync
  // uses end to end.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    // A foreign, currently-live lock (fresh mtime -- not stale) belonging
    // to some OTHER invocation, sitting at the shared path.
    writeFileSync(lockPath, "someone-elses-token");
    const result = await attemptWorktreeSync(dir);
    // Contention -- this invocation never acquired the lock at all, so it
    // must not have touched the foreign content in any way.
    assert.equal(result.attempted, false);
    assert.match(result.reason, /already in progress/);
    assert.equal(readFileSync(lockPath, "utf-8"), "someone-elses-token");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("acquireLock returns an ownership token, and releaseLock atomically claims the path before comparing it", () => {
  // Structural check (no DI seam for the internal fs calls, and reliably
  // forcing the real suspended-holder/concurrent-reclaimer timing scenarios
  // isn't practical in a fast unit test): acquireLock must return a token
  // alongside the fd, and releaseLock must claim the lock path via
  // renameSync (atomic single-winner semantics, same as acquireLock's own
  // reclaim step) BEFORE reading/comparing its content -- a separate
  // read-then-unlink is itself a TOCTOU another invocation's reclaim could
  // land inside.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const acquireBody = source.slice(
    source.indexOf("function acquireLock("),
    source.indexOf("async function withWorktreeSyncLock("),
  );
  assert.match(acquireBody, /token/);
  const releaseBody = source.slice(
    source.indexOf("function releaseLock("),
    source.indexOf("// Async-only twin of resolveRuntimePython"),
  );
  assert.ok(releaseBody.length > 0, "could not locate releaseLock's body");
  const claimIndex = releaseBody.indexOf("renameSync(lockPath, claimedPath)");
  const readIndex = releaseBody.indexOf("readFileSync(claimedPath");
  assert.ok(claimIndex >= 0, "expected releaseLock to claim the path via renameSync");
  assert.ok(readIndex >= 0, "expected releaseLock to read the claimed content");
  assert.ok(claimIndex < readIndex, "expected the atomic claim (rename) to happen BEFORE the content read");
});

test("releaseLock restores a foreign lock via linkSync (no-clobber), never renameSync (which silently overwrites)", () => {
  // Real regression this guards: on POSIX, renameSync REPLACES an existing
  // destination -- restoring a foreign claimed lock with renameSync could
  // silently overwrite a FRESH lock a third invocation created at lockPath
  // in the interim since this invocation's claim, defeating serialization
  // entirely (the exact outcome this whole lock exists to prevent).
  // linkSync fails with EEXIST if the destination already exists, so it
  // only ever restores into a genuinely empty slot. This logic now lives in
  // the shared restoreClaimedLock helper (used by both releaseLock's and
  // acquireLock's foreign-content restore branches); check both that the
  // helper itself uses linkSync (never renameSync), and that releaseLock
  // actually delegates to it. Structural check (the exact concurrent
  // timing isn't reliably forceable in a fast unit test).
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const restoreBody = source.slice(
    source.indexOf("function restoreClaimedLock("),
    source.indexOf("function releaseLock("),
  );
  assert.ok(restoreBody.length > 0, "could not locate restoreClaimedLock's body");
  assert.match(restoreBody, /linkSync\(claimedPath,\s*lockPath\)/);
  const releaseBody = source.slice(
    source.indexOf("function releaseLock("),
    source.indexOf("// Async-only twin of resolveRuntimePython"),
  );
  assert.ok(releaseBody.length > 0, "could not locate releaseLock's body");
  assert.match(releaseBody, /restoreClaimedLock\(claimedPath,\s*lockPath\)/);
});

test("releaseLock restores the claim (never just returns) when reading the claimed content fails", () => {
  // Real regression this guards (round 27): if readFileSync fails right
  // after releaseLock's own renameSync just claimed the path (an
  // essentially-impossible-in-practice but not-provably-impossible
  // failure), the prior code simply `return`ed -- leaving lockPath (the
  // canonical path) EMPTY, with the only surviving content sitting at the
  // orphaned claimedPath. A third invocation's exclusive acquireLock could
  // then succeed at lockPath and run concurrently with whatever that claim
  // actually represented. Since the content couldn't even be read, there
  // is no way to tell whether it was this invocation's own lock or a
  // foreign one -- but either way, the canonical path must not end up
  // empty, so this must restore via restoreClaimedLock rather than
  // silently returning.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const releaseBody = source.slice(
    source.indexOf("function releaseLock("),
    source.indexOf("// Async-only twin of resolveRuntimePython"),
  );
  assert.ok(releaseBody.length > 0, "could not locate releaseLock's body");
  const readAttemptIndex = releaseBody.indexOf("readFileSync(claimedPath");
  assert.ok(readAttemptIndex >= 0, "expected releaseLock to read the claimed content");
  const readCatchIndex = releaseBody.indexOf("} catch {", readAttemptIndex);
  assert.ok(readCatchIndex >= 0, "expected a catch block for the content read");
  const readCatchEnd = releaseBody.indexOf("if (content === token)", readCatchIndex);
  assert.ok(readCatchEnd >= 0, "expected the read-failure catch to precede the own-token check");
  const readCatchBody = releaseBody.slice(readCatchIndex, readCatchEnd);
  assert.match(readCatchBody, /restoreClaimedLock\(claimedPath,\s*lockPath\)/);
  assert.doesNotMatch(readCatchBody.slice(0, readCatchBody.indexOf("restoreClaimedLock")), /^\s*return;\s*$/m);
});

test("restoreClaimedLock falls back to a no-clobber exclusive-create write (never a plain renameSync) when linkSync fails for any reason OTHER than EEXIST", () => {
  // Real regression this guards (round 27): a plain renameSync fallback
  // (round 26's fix) assumed EEXIST was the ONLY signal linkSync gives
  // for "the destination is occupied" -- but linkSync can also fail with
  // ENOTSUP/EPERM on a filesystem without hard-link support even when
  // lockPath genuinely IS occupied, and a renameSync there would silently
  // replace a still-active lock, letting two holders run concurrently.
  // The fix replaces the renameSync fallback with a read + exclusive-
  // create write (`openSync(lockPath, "wx")`, atomic on both POSIX and
  // Windows): its own EEXIST is the reliable "already occupied" signal
  // instead, regardless of hard-link support. Structural check: the
  // non-EEXIST branch must attempt an exclusive-create open, never a bare
  // renameSync(claimedPath, lockPath).
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const restoreBody = source.slice(
    source.indexOf("function restoreClaimedLock("),
    source.indexOf("function releaseLock("),
  );
  assert.ok(restoreBody.length > 0, "could not locate restoreClaimedLock's body");
  const catchIndex = restoreBody.indexOf("} catch (error) {");
  assert.ok(catchIndex >= 0, "expected restoreClaimedLock's linkSync catch to inspect the error");
  assert.match(restoreBody.slice(catchIndex), /error\?\.code === "EEXIST"/);
  const eexistIfIndex = restoreBody.indexOf('if (error?.code === "EEXIST")', catchIndex);
  const eexistIfEnd = restoreBody.indexOf("}", restoreBody.indexOf("}", eexistIfIndex) + 1) + 1;
  const unexpectedFailureIndex = restoreBody.indexOf("Any OTHER linkSync failure", eexistIfEnd);
  assert.ok(unexpectedFailureIndex >= 0, "expected a distinct non-EEXIST branch comment after the EEXIST if-block");
  const nonEexistBranch = restoreBody.slice(unexpectedFailureIndex);
  assert.doesNotMatch(nonEexistBranch, /renameSync\(claimedPath,\s*lockPath\)/);
  const openIndex = nonEexistBranch.indexOf('openSync(lockPath, "wx")');
  assert.ok(openIndex >= 0, "expected the non-EEXIST branch to attempt an exclusive-create openSync restore");
});

test("restoreClaimedLock's exclusive-create fallback treats its own EEXIST as benign contention, and any other failure as fail-closed without deleting the claimed copy", () => {
  // Companion to the test above: the exclusive-create fallback needs the
  // SAME two-way branch linkSync's own catch has -- EEXIST (someone else
  // already re-occupied lockPath, safe to drop the redundant claim) vs.
  // any other failure (fail closed, preserve the orphaned copy as the
  // only remaining trace).
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const restoreBody = source.slice(
    source.indexOf("function restoreClaimedLock("),
    source.indexOf("function releaseLock("),
  );
  assert.ok(restoreBody.length > 0, "could not locate restoreClaimedLock's body");
  const openCatchIndex = restoreBody.indexOf("} catch (writeError) {");
  assert.ok(openCatchIndex >= 0, "expected the exclusive-create open to have its own catch inspecting the error");
  const openCatchBody = restoreBody.slice(openCatchIndex, restoreBody.indexOf("return false;", openCatchIndex) + "return false;".length);
  assert.match(openCatchBody, /writeError\?\.code === "EEXIST"/);
  const eexistBranchEnd = openCatchBody.indexOf("}", openCatchBody.indexOf('writeError?.code === "EEXIST"'));
  assert.doesNotMatch(openCatchBody.slice(eexistBranchEnd), /unlinkSync\(claimedPath\)/);
});

test("attemptWorktreeSyncLocked disables rebase.autoStash and repo hooks for the delegated agent-worktrees sync too", () => {
  // Real regression this guards: plainGitSync's own rebase passes
  // --no-autostash directly, but the AGENT-WORKTREES-delegated path has no
  // such flag of its own -- its `is_clean` check uses bare `git status
  // --porcelain` and its rebase does not disable autostash, so content
  // created in the runtime-resolution gap before the delegated exec could
  // be silently stashed/rebased despite this file's own dirty recheck.
  // Repo hooks (a client-side pre-rebase guard) must also not be able to
  // block or mutate this unattended sync, matching agent-worktrees' own
  // established core.hooksPath convention. Structural check: both delegated
  // exec branches must pass env through withUnattendedSyncGitConfig.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const helperBody = source.slice(
    source.indexOf("function withUnattendedSyncGitConfig("),
    source.indexOf("function rebaseInProgress("),
  );
  assert.match(helperBody, /rebase\.autoStash/);
  assert.match(helperBody, /core\.hooksPath/);
  const lockedBody = source.slice(
    source.indexOf("async function attemptWorktreeSyncLocked("),
    source.indexOf("// True if an agent-dispatch coordinator"),
  );
  const callCount = (lockedBody.match(/withUnattendedSyncGitConfig\(/g) || []).length;
  assert.ok(
    callCount >= 2,
    `expected withUnattendedSyncGitConfig to wrap env for both delegated exec branches, saw ${callCount} call(s)`,
  );
});

test("attemptWorktreeSync never reclaims a lock recording a genuinely LIVE holder with no recorded start time, no matter its age", async () => {
  // Real regression this guards (the round-16-through-20 root cause): a
  // reclaim scheme based purely on age can reclaim a holder that is merely
  // slow (a suspended process, a very slow network) but still very much
  // alive -- which is exactly what forces the whole chain of release-side
  // races those rounds kept surfacing. A live pid with no recorded start
  // time to compare (an older/foreign lock format) has no basis for an
  // identity check, so it must fail closed (never reclaim) rather than
  // fall back to age.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    // This test process's own pid is unambiguously alive; "unknown" means
    // no start time was recorded to compare against.
    writeFileSync(lockPath, `${process.pid}-unknown-liveholder`);
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /already in progress/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync reclaims a lock immediately once its recorded pid has been reused by a different live process, regardless of age", async () => {
  // Real regression this guards (round 23): `process.kill(pid, 0)` alone
  // can only observe whether SOME process holds that pid right now -- it
  // cannot tell whether the ORIGINAL holder crashed and the OS later
  // reused its pid for a completely unrelated process. Age-based
  // reclaiming of a live pid (round 22's fix) reintroduced the exact race
  // this whole scheme exists to prevent for a merely-slow-but-real holder.
  // Comparing the recorded start time against the CURRENT process
  // occupying that pid resolves the ambiguity precisely: a mismatch proves
  // it is a DIFFERENT process, safe to reclaim immediately with no age
  // wait at all.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    // This test process's own pid is genuinely alive, but the recorded
    // start time is deliberately wrong (a stand-in for "this pid used to
    // belong to a different, now-crashed process").
    writeFileSync(lockPath, `${process.pid}-1-reused-pid-standin`);
    const result = await attemptWorktreeSync(dir);
    // Reclaimed immediately -- no age/backdating needed at all, proving
    // the mismatch itself (not elapsed time) triggered the reclaim.
    assert.equal(result.attempted, true);
    assert.notEqual(result.reason, "another sync attempt for this worktree is already in progress; automatic sync was skipped");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("attemptWorktreeSync never reclaims a lock whose recorded start time genuinely matches the current live holder, no matter its age", async () => {
  // The core round-20/22 case, now with a REAL matching start time (not
  // just "unknown"): this test process's own accurately-recorded start
  // time must never be treated as reclaimable, confirming the identity
  // check's positive match path (not just its "no data" fail-closed path).
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    const ownStartTime = await processStartTimeMs(process.pid);
    assert.ok(Number.isFinite(ownStartTime), "test environment could not query its own process start time");
    writeFileSync(lockPath, `${process.pid}-${ownStartTime}-realholder`);
    const result = await attemptWorktreeSync(dir);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /already in progress/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("acquireLock treats a failed stat on lock contention as contention, not as reclaimable", () => {
  // Real regression this guards: if statSync throws for a reason OTHER
  // than "the lock is genuinely gone" (a permission error, a transient FS
  // hiccup, or another process recreating it in the exact instant between
  // the failed open and this stat), treating that ambiguity as reclaimable
  // would let this process unlink an ACTIVE replacement lock another
  // process just created, letting both run concurrently. Structural check
  // (no DI seam for the internal fs calls): the stat's catch block must
  // rethrow/fail closed, never fall through to an unconditional reclaim.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const body = source.slice(
    source.indexOf("function acquireLock("),
    source.indexOf("async function withWorktreeSyncLock("),
  );
  assert.ok(body.length > 0, "could not locate acquireLock's body");
  const statCatchBody = body.slice(
    body.indexOf("} catch {", body.indexOf("statSync(lockPath)")),
  );
  // Fails closed by making the age look NOT stale (`-Infinity`, always
  // <= STALE_LOCK_MS) rather than reclaimable, so ambiguous evidence never
  // resolves to "safe to reclaim".
  assert.match(statCatchBody.slice(0, statCatchBody.indexOf("}", 10) + 1), /ageMs = -Infinity/);
});

test("acquireLock never reclaims by age a lock it could not read at all, only one it read but could not parse a pid from", () => {
  // Real regression this guards (round 26): an unreadable existing lock
  // (permissions, transient I/O, an unsupported filesystem) is NOT the
  // same evidence as a readable lock in a corrupt/legacy format with no
  // parseable pid -- the latter is safe to fall back to the age-only
  // heuristic (there is genuinely nothing else to go on), but the former
  // gives NO evidence at all that the holder is gone or old, and folding
  // it into the same age-fallback path could reclaim a lock whose
  // (unreadable) content actually names a genuinely live holder. The two
  // cases must be tracked separately and only the read-succeeded-but-
  // unparseable case may reach the age-only branch. Structural check (no
  // DI seam for the internal fs calls): the read-failure catch must set a
  // distinct flag, and that flag must gate its OWN fail-closed branch
  // ahead of (never falling into) the age-only heuristic.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const body = source.slice(
    source.indexOf("function acquireLock("),
    source.indexOf("async function withWorktreeSyncLock("),
  );
  assert.ok(body.length > 0, "could not locate acquireLock's body");
  const readCatchIndex = body.indexOf("readFailed = true;");
  assert.ok(readCatchIndex >= 0, "expected a distinct readFailed flag set when reading the lock's content fails");
  const readFailedBranchIndex = body.indexOf("} else if (readFailed) {", readCatchIndex);
  assert.ok(readFailedBranchIndex >= 0, "expected a dedicated readFailed branch, distinct from the no-pid-parsed branch");
  const ageOnlyBranchIndex = body.indexOf("statSync(lockPath).mtimeMs", readFailedBranchIndex);
  assert.ok(ageOnlyBranchIndex >= 0, "expected the age-only heuristic branch to still exist after the readFailed branch");
  const readFailedBranchBody = body.slice(readFailedBranchIndex, ageOnlyBranchIndex);
  assert.match(readFailedBranchBody, /reclaimable = false/);
  assert.doesNotMatch(readFailedBranchBody, /statSync/);
});

test("attemptWorktreeSync rechecks for an in-progress rebase immediately before the sync exec (TOCTOU narrowing)", () => {
  // The initial dirty/rebase checks and the actual sync exec are not atomic
  // (another process could start a rebase in between) -- this cannot be
  // closed without a cross-process lock this plugin does not own, so the
  // mitigation is a second, as-late-as-possible recheck right before each
  // sync exec call. Structural check (no DI seam for the internal git
  // calls): rebaseInProgress must be invoked more than once in
  // attemptWorktreeSync's body.
  const source = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..", "..", "extensions", "context-handoff", "handoff-core.mjs",
    ),
    "utf-8",
  );
  const body = source.slice(
    source.indexOf("export async function attemptWorktreeSync("),
    source.indexOf("// True if an agent-dispatch coordinator"),
  );
  assert.ok(body.length > 0, "could not locate attemptWorktreeSync's body");
  const callCount = (body.match(/rebaseInProgress\(cwd\)/g) || []).length;
  assert.ok(
    callCount >= 2,
    `expected rebaseInProgress to be rechecked before the sync exec, saw ${callCount} call(s)`,
  );
});

test("attemptWorktreeSync's dirty-tree check runs AFTER the worktree lock is held, not before", async () => {
  // Real regression this guards: checking cleanliness before acquiring the
  // lock left a window where a file could become dirty between the check
  // and the locked sync actually running. Proven by holding the lock first
  // (simulating a concurrent attempt) and confirming the OUTCOME is "lock
  // busy", not "dirty tree" or a real sync attempt -- if the dirty check
  // still ran before the lock, this would instead report the tree as clean
  // and proceed to a real (lock-blocked) attempt with a different reason.
  const dir = initGitRepo();
  try {
    writeFileSync(join(dir, "file.txt"), "one\nuncommitted\n");
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, "");
    try {
      const result = await attemptWorktreeSync(dir);
      assert.equal(result.attempted, false);
      assert.match(result.reason, /already in progress/);
    } finally {
      unlinkSync(lockPath);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("plainGitSync rechecks dirtiness immediately before its own rebase exec (rebase.autoStash safety)", async () => {
  // Real regression this guards: the fetch above this recheck is another
  // gap where a file could become dirty, and a local rebase.autoStash
  // config would otherwise let `git rebase` silently stash/pop that
  // content instead of refusing to run.
  const origin = initGitRepo();
  const clone = mkdtempSync(join(tmpdir(), "context-handoff-sync-clone-"));
  try {
    execFileSync("git", ["clone", "-q", origin, clone]);
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: clone });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: clone });
    writeFileSync(join(origin, "file.txt"), "one\ntwo\n");
    execFileSync("git", ["add", "."], { cwd: origin });
    execFileSync("git", ["commit", "-q", "-m", "second"], { cwd: origin });
    writeFileSync(join(clone, "dirty.txt"), "uncommitted\n");
    const result = await plainGitSync(clone);
    assert.equal(result.attempted, false);
    assert.equal(result.synced, false);
    assert.match(result.reason, /autoStash|dirty/);
  } finally {
    rmSync(origin, { recursive: true, force: true });
    rmSync(clone, { recursive: true, force: true });
  }
});

test("resolveRuntimePython with allowProvision:false fails fast instead of risking the 180s provisioning wait", () => {
  // Real regression this guards: attemptWorktreeSync's runCli call is
  // fire-and-forget from session.usage_info, before any await -- if an
  // agent-worktrees runtime were not yet provisioned, resolveRuntimePython's
  // provisioning branch could block the event loop for up to
  // RUNTIME_PROVISION_TIMEOUT_MS (180s). allowProvision:false must skip that
  // branch entirely and fail immediately instead.
  const fakePluginRoot = mkdtempSync(join(tmpdir(), "context-handoff-fakert-"));
  try {
    const scriptsDir = join(fakePluginRoot, "scripts");
    mkdirSync(scriptsDir, { recursive: true });
    // Deliberately resolves nothing (simulates an unprovisioned runtime)
    // without erroring, so resolve() returns an empty string rather than
    // throwing for an unrelated reason. Both platform scripts are written so
    // this test is meaningful on POSIX CI runners too, not just Windows.
    writeFileSync(
      join(scriptsDir, "resolve-runtime.ps1"),
      "$AgentRtPy = $null\n",
    );
    writeFileSync(
      join(scriptsDir, "resolve-runtime.sh"),
      "AGENT_RT_PY=\n",
    );
    const resolved = {
      path: join(fakePluginRoot, "fake-cli.ps1"),
      pluginRoot: fakePluginRoot,
      module: "fake_module",
      runtimeRoot: ".fake-runtime-root-that-does-not-exist",
      payloadRootEnv: null,
    };
    const env = {
      ...process.env,
      COPILOT_PLUGIN_ROOT: fakePluginRoot,
    };
    const start = Date.now();
    assert.throws(
      () => resolveRuntimePython(resolved, env, fakePluginRoot, 5000, false),
      /provisioning skipped/,
    );
    const elapsedMs = Date.now() - start;
    // Generous upper bound: real provisioning waits up to 180_000ms: this
    // must be nowhere close to that if allowProvision:false is honored.
    assert.ok(elapsedMs < 10_000, `expected a fast failure, took ${elapsedMs}ms`);
  } finally {
    rmSync(fakePluginRoot, { recursive: true, force: true });
  }
});

test("waitForWorktreeSyncToSettle returns immediately (settled) when no sync lock is present", async () => {
  const dir = initGitRepo();
  try {
    const start = Date.now();
    const result = await waitForWorktreeSyncToSettle(dir, { timeoutMs: 5000, pollMs: 100 });
    const elapsedMs = Date.now() - start;
    assert.equal(result.waited, true);
    assert.equal(result.settled, true);
    assert.equal(result.reason, "lock-absent");
    assert.ok(elapsedMs < 2000, `expected a near-instant return, took ${elapsedMs}ms`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("waitForWorktreeSyncToSettle returns immediately (settled) when the recorded lock holder is confirmed dead", async () => {
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, `${deadPid()}-0-deadholder`);
    const start = Date.now();
    const result = await waitForWorktreeSyncToSettle(dir, { timeoutMs: 5000, pollMs: 100 });
    const elapsedMs = Date.now() - start;
    assert.equal(result.waited, true);
    assert.equal(result.settled, true);
    assert.equal(result.reason, "holder-dead");
    assert.ok(elapsedMs < 2000, `expected a near-instant return, took ${elapsedMs}ms`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("waitForWorktreeSyncToSettle keeps waiting (up to its timeout) while the recorded lock holder is genuinely alive, then reports unsettled", async () => {
  // Real regression this guards: a naive implementation might only check
  // the lock ONCE and immediately report "settled" (or "not settled")
  // without actually polling -- proving this requires a lock whose holder
  // stays alive for the entire wait window, confirming the function keeps
  // re-checking rather than resolving on its first read.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    // This test process's own pid is unambiguously alive for the entire
    // test -- a stand-in for a genuinely slow, still-running sync.
    writeFileSync(lockPath, `${process.pid}-unknown-liveholder`);
    const start = Date.now();
    const result = await waitForWorktreeSyncToSettle(dir, { timeoutMs: 600, pollMs: 100 });
    const elapsedMs = Date.now() - start;
    assert.equal(result.waited, true);
    assert.equal(result.settled, false);
    assert.equal(result.reason, "timeout");
    // Must have actually waited close to the timeout, not returned instantly.
    assert.ok(elapsedMs >= 500, `expected to wait close to the timeout, took ${elapsedMs}ms`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("waitForWorktreeSyncToSettle stops waiting as soon as the lock is released mid-poll", async () => {
  // Proves this is a genuine poll loop, not just "wait the full timeout
  // then re-check once": the lock is released partway through the
  // configured timeout window, and the function must notice and return
  // well before that timeout elapses.
  const dir = initGitRepo();
  try {
    const gitDir = execFileSync("git", ["rev-parse", "--git-path", "context-handoff-sync.lock"], {
      cwd: dir, encoding: "utf-8",
    }).trim();
    const lockPath = join(dir, gitDir);
    mkdirSync(dirname(lockPath), { recursive: true });
    writeFileSync(lockPath, `${process.pid}-unknown-liveholder`);
    setTimeout(() => {
      try { rmSync(lockPath); } catch { /* ignore */ }
    }, 300);
    const start = Date.now();
    const result = await waitForWorktreeSyncToSettle(dir, { timeoutMs: 5000, pollMs: 100 });
    const elapsedMs = Date.now() - start;
    assert.equal(result.settled, true);
    assert.equal(result.reason, "lock-absent");
    assert.ok(elapsedMs < 2000, `expected to notice the release well before the 5s timeout, took ${elapsedMs}ms`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

