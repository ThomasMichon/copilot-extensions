import { test } from "node:test";
import assert from "node:assert/strict";
import { writeFileSync, readFileSync, unlinkSync, linkSync } from "node:fs";
import { join } from "node:path";
import childProcess from "node:child_process";
import dns from "node:dns";
import { syncBuiltinESMExports } from "node:module";
import { setTimeout as delay } from "node:timers/promises";
import { residentFixture } from "./fixtures/resident-control.mjs";

let helperCalls = 0;
let harnessStarting = false;
const lookup = dns.lookup;
for (const key of ["exec", "execFile", "spawn", "fork", "execSync", "execFileSync", "spawnSync"]) {
  childProcess[key] = () => { helperCalls++; throw new Error("helper forbidden"); };
}
dns.lookup = (...args) => {
  if (harnessStarting) return lookup(...args);
  throw new Error("DNS forbidden");
};
syncBuiltinESMExports();
const { observeHandoffPromptReceived } = await import("../extensions/context-handoff/handoff-core.mjs");
const { observeResidentReceipt, parseUniqueJson } = await import("../extensions/context-handoff/resident-receipt-client.mjs");

function input(fixture, pending = {}) {
  // Transport tests isolate the socket contract; registration tests use the real native adapter.
  return { sessionDir: fixture.sessionDir, sid: "subject", locator: "task:one", pending,
    beforeRecord: async () => ({ verified: true }) };
}
async function withFixture(body) {
  let fixture;
  harnessStarting = true;
  try { fixture = await residentFixture(); }
  finally { harnessStarting = false; }
  try { await body(fixture); }
  finally { await fixture.close(); assert.equal(helperCalls, 0); }
}

test("actual core entry uses resident sockets and zero command helpers for two cycles", async (t) => {
  await withFixture(async (f) => {
    for (let cycle = 0; cycle < 2; cycle++) {
      const started = performance.now();
      const result = await observeHandoffPromptReceived(input(f));
      assert.equal(result.status, "logged");
      assert.ok(performance.now() - started < 2000);
    }
    assert.equal(f.effects.size, 1);
    assert.deepEqual(f.requests.map((r) => r.action), [
      "health", "handoff-prompt-receipt", "health", "handoff-prompt-receipt",
    ]);
    t.diagnostic(`effects=${f.effects.size}; socket requests=${f.requests.length}; helpers=${helperCalls}`);
  });
});

for (const failure of ["missing-catalog", "missing-token", "missing-nonce", "foreign-source", "previous-only", "non-loopback", "duplicate-catalog", "duplicate-field", "hardlink"]) {
  test(`${failure} refuses before any resident effect`, async () => {
    await withFixture(async (f) => {
      if (failure === "missing-catalog") unlinkSync(f.guidance);
      if (failure === "missing-token") unlinkSync(f.tokenPath);
      if (failure === "missing-nonce") { delete f.subjectBinding.creationNonce; f.writeBinding(); }
      if (failure === "foreign-source") { f.candidate.sourceFingerprint = "foreign"; f.writeCatalog(); }
      if (failure === "previous-only") writeFileSync(f.activeRoutePath, JSON.stringify({ previous: f.route }));
      if (failure === "non-loopback") { f.route.bind = "example.test"; f.writeRoute(); }
      if (failure === "duplicate-catalog") {
        const raw = readFileSync(f.guidance, "utf-8");
        writeFileSync(f.guidance, raw + raw);
      }
      if (failure === "duplicate-field") writeFileSync(f.activeRoutePath, '{"active":{},"active":{}}');
      if (failure === "hardlink") linkSync(f.tokenPath, join(f.runtimeRoot, "token-alias"));
      assert.equal((await observeResidentReceipt(input(f))).status, "unavailable");
      assert.equal(f.effects.size, 0);
      assert.equal(f.requests.length, 0);
    });
  });
}

test("wrong credential and absent capability never record", async () => {
  await withFixture(async (f) => {
    writeFileSync(f.tokenPath, "wrong");
    assert.equal((await observeResidentReceipt(input(f))).status, "unavailable");
    writeFileSync(f.tokenPath, "fixture-token");
    f.capability = false;
    assert.equal((await observeResidentReceipt(input(f))).status, "unavailable");
    assert.equal(f.effects.size, 0);
    assert.equal(f.requests.every((r) => r.action === "health"), true);
  });
});

test("lost record reply retains exact identity and only queries on replay", async () => {
  await withFixture(async (f) => {
    const pending = {};
    f.dropRecordReply = true;
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "unknown");
    assert.equal(pending.uncertain, true);
    f.dropRecordReply = false;
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "logged");
    assert.equal(f.effects.size, 1);
    assert.deepEqual(f.requests.filter((r) => r.action === "handoff-prompt-receipt")
      .map((r) => r.payload.operation), ["record", "query"]);
  });
});

test("unknown is fenced through source and incarnation changes", async () => {
  await withFixture(async (f) => {
    const pending = {};
    f.dropRecordReply = true;
    await observeResidentReceipt(input(f, pending));
    const count = f.requests.length;
    f.subjectBinding.creationNonce = "replacement";
    f.writeBinding();
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "unknown");
    assert.equal(f.requests.length, count);
    f.subjectBinding.creationNonce = "incarnation";
    f.writeBinding();
    f.catalog.binding.source.fingerprint = "new-source";
    f.catalog.binding.installation.sourceFingerprint = "new-source";
    f.candidate.sourceFingerprint = "new-source";
    f.writeCatalog();
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "unknown");
    assert.equal(f.requests.length, count);
  });
});

test("query while prior mutation is in flight cannot admit a repeated record", async () => {
  await withFixture(async (f) => {
    const pending = { uncertain: true };
    f.inFlight = true;
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "unknown");
    assert.equal(pending.uncertain, true);
    assert.equal(f.requests.at(-1).payload.operation, "query");
    assert.equal(f.effects.size, 0);
  });
});

test("oversized post-record response remains unknown, not completed or absent", async () => {
  await withFixture(async (f) => {
    f.oversized = true;
    const pending = {};
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "unknown");
    assert.equal(pending.uncertain, true);
  });
});

test("cancellation settles local socket without claiming server rollback", async () => {
  await withFixture(async (f) => {
    f.hangHealth = true;
    const aborter = new AbortController();
    const started = performance.now();
    const operation = observeResidentReceipt({ ...input(f), signal: aborter.signal });
    while (f.requests.length === 0) await delay(5);
    aborter.abort();
    assert.equal((await operation).status, "unavailable");
    assert.ok(performance.now() - started < 2000);
    await delay(10);
    assert.equal(f.sockets.size, 0);
    assert.equal(f.effects.size, 0);
  });
});

test("a nonresponsive resident consumes one whole 2000ms observation, no retry loop", async (t) => {
  await withFixture(async (f) => {
    f.hangHealth = true;
    const started = performance.now();
    assert.equal((await observeResidentReceipt(input(f))).status, "unavailable");
    const elapsed = performance.now() - started;
    assert.ok(elapsed < 2000);
    assert.equal(f.requests.length, 1);
    assert.equal(f.effects.size, 0);
    t.diagnostic(`whole stalled observation=${elapsed.toFixed(2)}ms; helpers=${helperCalls}`);
  });
});

test("duplicate escaped JSON keys are refused", () => {
  assert.throws(() => parseUniqueJson('{"key":1,"\\u006bey":2}'));
});

test("missing native metadata admission never falls back to record transport", async () => {
  await withFixture(async (f) => {
    const options = input(f);
    delete options.beforeRecord;
    assert.equal((await observeResidentReceipt(options)).reason, "native-metadata-required");
    assert.equal(f.requests.length, 0);
    assert.equal(f.effects.size, 0);
  });
});

function setGenerations(fixture, installGeneration, activationGeneration, owner = true) {
  const values = { installGeneration, activationGeneration };
  Object.assign(fixture.candidate, values);
  Object.assign(fixture.catalog.binding.installation, values);
  if (owner) Object.assign(fixture.owner, values);
  fixture.writeCatalog();
}

test("installation generation changes reconcile an uncertain exact receipt by query", async () => {
  await withFixture(async (f) => {
    setGenerations(f, 1, 1);
    const pending = {};
    f.dropRecordReply = true;
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "unknown");
    const subject = { ...pending.subject };
    setGenerations(f, 2, 3);
    f.owner.routeGeneration = f.route.generation = 2;
    f.owner.runtimeGeneration = "runtime-two";
    f.writeRoute();
    f.dropRecordReply = false;
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "logged");
    assert.deepEqual(pending.subject, subject);
    assert.deepEqual(f.requests.filter((r) => r.action === "handoff-prompt-receipt")
      .map((r) => r.payload.operation), ["record", "query"]);
    assert.equal(f.requests.at(-1).payload.expectedOwner.installGeneration, 2);
    assert.equal(f.requests.at(-1).payload.expectedOwner.activationGeneration, 3);
    assert.equal(f.effects.size, 1);
  });
});

test("installation generation changes allow retry after unavailable pre-send health", async () => {
  await withFixture(async (f) => {
    setGenerations(f, 1, 1);
    const pending = {};
    f.capability = false;
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "unavailable");
    assert.equal(pending.uncertain, undefined);
    setGenerations(f, 2, 2);
    f.capability = true;
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "logged");
    assert.equal(f.requests.filter((r) => r.action === "handoff-prompt-receipt").length, 1);
    assert.equal(f.effects.size, 1);
  });
});

test("current generation mismatch in health still refuses the receipt", async () => {
  await withFixture(async (f) => {
    setGenerations(f, 1, 1);
    const pending = {};
    f.capability = false;
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "unavailable");
    setGenerations(f, 2, 2, false);
    f.capability = true;
    assert.equal((await observeResidentReceipt(input(f, pending))).status, "unavailable");
    assert.equal(f.effects.size, 0);
    assert.equal(f.requests.every((r) => r.action === "health"), true);
  });
});
