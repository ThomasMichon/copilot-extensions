import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { contextHandoffConfigPathsAsync } from "../extensions/context-handoff/config.mjs";
import {
  ACTOR_AGENT,
  ACTOR_OPERATOR,
  OVERRIDE_FILE_NAME,
  applyOverrideRequest,
  createConfigReloader,
  effectiveThresholds,
  emptyOverride,
  parseOverrideArgs,
  readOverride,
  sessionOverridePath,
  writeOverride,
} from "../extensions/context-handoff/live-control.mjs";
import { DEFAULT_THRESHOLDS } from "../extensions/context-handoff/thresholds.mjs";

function fakeFs() {
  const stats = new Map();
  return {
    set(path, mtimeMs, size = 10) { stats.set(path, { mtimeMs, size }); },
    remove(path) { stats.delete(path); },
    async statFn(path) {
      if (!stats.has(path)) throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
      return stats.get(path);
    },
  };
}

test("config reloader reloads only when a layer's fingerprint changes", async () => {
  const fs = fakeFs();
  let loads = 0;
  let next = { mode: "auto", thresholds: DEFAULT_THRESHOLDS, warning: null };
  const reloader = createConfigReloader({
    paths: ["user.yaml", "repo.yaml"],
    load: async () => { loads++; return next; },
    statFn: fs.statFn,
  });
  fs.set("repo.yaml", 1);
  await reloader.prime();

  assert.deepEqual(await reloader.refresh(), { changed: false });
  assert.equal(loads, 0);

  fs.set("repo.yaml", 2);
  next = { mode: "off", thresholds: DEFAULT_THRESHOLDS, warning: null };
  const changed = await reloader.refresh();
  assert.equal(changed.changed, true);
  assert.equal(changed.config.mode, "off");

  // A newly created user layer counts as a change too.
  fs.set("user.yaml", 1);
  assert.equal((await reloader.refresh()).changed, true);
  assert.equal(loads, 2);
});

test("config reloader keeps the previous config on an invalid edit and reports it once", async () => {
  const fs = fakeFs();
  fs.set("repo.yaml", 1);
  const reloader = createConfigReloader({
    paths: ["repo.yaml"],
    load: async () => ({ mode: "manual-only", warning: "Invalid context-handoff config; bad" }),
    statFn: fs.statFn,
  });
  await reloader.prime();
  fs.set("repo.yaml", 2);
  const first = await reloader.refresh();
  assert.equal(first.changed, false);
  assert.match(first.warning, /Invalid context-handoff config/);
  assert.deepEqual(await reloader.refresh(), { changed: false });
});

test("config layer paths list user then repo candidates even when absent", async () => {
  const root = mkdtempSync(join(tmpdir(), "ch-live-"));
  try {
    mkdirSync(join(root, "repo", ".git"), { recursive: true });
    const paths = await contextHandoffConfigPathsAsync(join(root, "repo"), { homeDir: join(root, "home") });
    assert.deepEqual(paths, [
      join(root, "home", ".context-handoff", "config.yaml"),
      join(root, "repo", ".context-handoff", "config.yaml"),
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("slash-command arguments parse actions and threshold units", () => {
  assert.deepEqual(parseOverrideArgs(""), { action: "status", thresholds: {} });
  assert.deepEqual(parseOverrideArgs("PAUSE"), { action: "pause", thresholds: {} });
  assert.deepEqual(parseOverrideArgs("set soft=60 hard=150k force=78%"), {
    action: "set",
    thresholds: { softPercent: 60, hardTokens: 150_000, forcePercent: 78 },
  });
  assert.deepEqual(parseOverrideArgs("set hard=160000t"), {
    action: "set",
    thresholds: { hardTokens: 160_000 },
  });
  assert.throws(() => parseOverrideArgs("set soft=lots"), /soft: expected a percent/);
  assert.throws(() => parseOverrideArgs("set medium=60"), /unrecognized argument/);
});

test("operator can set, pause, resume, and clear; set keeps unnamed tiers on config", () => {
  const configThresholds = { softPercent: 50, hardPercent: 65, forcePercent: 75 };
  const opts = { actor: ACTOR_OPERATOR, configThresholds, now: () => "t" };

  const set = applyOverrideRequest(emptyOverride(), { action: "set", thresholds: { softPercent: 40 } }, opts);
  assert.equal(set.ok, true);
  assert.deepEqual(set.override.thresholds, { softPercent: 40 });
  assert.deepEqual(effectiveThresholds(configThresholds, set.override).thresholds, {
    softPercent: 40, hardPercent: 65, forcePercent: 75,
  });

  const paused = applyOverrideRequest(set.override, { action: "pause" }, opts);
  assert.equal(paused.override.paused, true);
  assert.equal(paused.override.updatedBy, ACTOR_OPERATOR);

  const resumed = applyOverrideRequest(paused.override, { action: "resume" }, opts);
  assert.equal(resumed.override.paused, false);

  const cleared = applyOverrideRequest(resumed.override, { action: "clear" }, opts);
  assert.deepEqual(cleared.override, emptyOverride());

  const invalid = applyOverrideRequest(emptyOverride(), { action: "set", thresholds: { softPercent: 70 } }, opts);
  assert.equal(invalid.ok, false);
  assert.match(invalid.message, /softPercent must be less than hardPercent/);
});

test("agent may only pause or raise thresholds", () => {
  const configThresholds = { softPercent: 55, hardPercent: 70, forcePercent: 79 };
  const opts = { actor: ACTOR_AGENT, configThresholds, now: () => "t" };

  assert.equal(applyOverrideRequest(emptyOverride(), { action: "pause" }, opts).ok, true);
  for (const action of ["resume", "clear"]) {
    const result = applyOverrideRequest({ ...emptyOverride(), paused: true }, { action }, opts);
    assert.equal(result.ok, false);
    assert.match(result.message, /only the operator/);
    assert.equal(result.override.paused, true);
  }

  const raised = applyOverrideRequest(emptyOverride(), { action: "set", thresholds: { softPercent: 65 } }, opts);
  assert.equal(raised.ok, true);
  assert.equal(raised.override.updatedBy, ACTOR_AGENT);

  const lowered = applyOverrideRequest(raised.override, { action: "set", thresholds: { softPercent: 60 } }, opts);
  assert.equal(lowered.ok, false);
  assert.match(lowered.message, /may only raise thresholds; soft would drop from 65% to 60%/);

  // Mixed units are compared at the known token limit.
  const tokensOk = applyOverrideRequest(emptyOverride(), {
    action: "set", thresholds: { softTokens: 120_000 },
  }, { ...opts, tokenLimit: 200_000 });
  assert.equal(tokensOk.ok, true);
  const tokensLower = applyOverrideRequest(emptyOverride(), {
    action: "set", thresholds: { softTokens: 100_000 },
  }, { ...opts, tokenLimit: 200_000 });
  assert.equal(tokensLower.ok, false);

  // Without a token limit, a cross-unit change cannot be proven safe.
  const unknown = applyOverrideRequest(emptyOverride(), {
    action: "set", thresholds: { softTokens: 120_000 },
  }, opts);
  assert.equal(unknown.ok, false);
  assert.match(unknown.message, /Cannot verify/);
});

test("an override that no longer fits a reloaded config falls back to config thresholds", () => {
  const configThresholds = { softPercent: 30, hardPercent: 40, forcePercent: 50 };
  const override = { ...emptyOverride(), thresholds: { softPercent: 60 } };
  const effective = effectiveThresholds(configThresholds, override);
  assert.deepEqual(effective.thresholds, configThresholds);
  assert.match(effective.warning, /Session threshold override ignored/);
  assert.deepEqual(effectiveThresholds(configThresholds, emptyOverride()), {
    thresholds: configThresholds, warning: null,
  });
});

test("override sidecar round-trips only inside an existing session folder", () => {
  const root = mkdtempSync(join(tmpdir(), "ch-override-"));
  try {
    assert.equal(sessionOverridePath(root, "missing"), null);
    assert.equal(sessionOverridePath(root, "unknown"), null);
    mkdirSync(join(root, "sid"));
    const path = sessionOverridePath(root, "sid");
    assert.equal(path, join(root, "sid", OVERRIDE_FILE_NAME));
    assert.deepEqual(readOverride(path), emptyOverride());

    const override = { paused: true, thresholds: { hardTokens: 150_000, bogus: 1 }, updatedBy: "agent", updatedAt: "t" };
    assert.equal(writeOverride(path, override), true);
    assert.deepEqual(readOverride(path), {
      paused: true, thresholds: { hardTokens: 150_000 }, updatedBy: "agent", updatedAt: "t",
    });

    writeFileSync(path, "{not json", "utf-8");
    assert.deepEqual(readOverride(path), emptyOverride());
    assert.equal(writeOverride(null, override), false);
    assert.ok(readFileSync(path, "utf-8").length > 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
