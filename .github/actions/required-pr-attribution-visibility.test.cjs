const { test } = require("node:test");
const assert = require("node:assert/strict");
const gate = require("./required-pr-attribution.cjs");
const { marker, context, pr, core, headFixture, countRequests } =
  require("./source-attribution-test-fixture.cjs");

function fixture() {
  const values = [{ ...pr(marker), number: 1 }];
  const result = headFixture(values);
  result.created.push({
    name: "source-attribution", head_sha: "head", status: "completed", conclusion: "failure",
    external_id: "source-metadata:" + "a".repeat(64) + ":superseded",
    details_url: "https://github.com/owner/repo/runs/1",
  });
  return { ...result, values };
}

test("scoped confirmation waits for known pre-write states without replaying writes", async () => {
  for (const state of ["old-result", "missing-created", "pending-created"]) {
    const { github, created } = fixture();
    const list = github.rest.checks.listForRef;
    const before = structuredClone((await list({ ref: "head" })).data.check_runs);
    let stale = 2;
    let writes = 0;
    let pauses = 0;
    const update = github.rest.checks.update;
    github.rest.checks.update = async (check) => { writes++; return update(check); };
    github.rest.checks.listForRef = async (options) => {
      const result = await list(options);
      if (created.at(-1)?.conclusion === "success" && stale-- > 0) {
        if (state === "old-result") result.data.check_runs[0] = before[0];
        if (state === "missing-created") result.data.check_runs.pop();
        if (state === "pending-created") Object.assign(result.data.check_runs.at(-1),
          { status: "in_progress", conclusion: null });
      }
      return result;
    };
    await gate.backfill({
      github, context, core: core(), pullRequest: 1,
      pause: async (milliseconds) => { assert.equal(milliseconds, 1000); pauses++; },
    });
    assert.equal(pauses, 2, state);
    assert.equal(writes, 2, "visibility confirmation must not repeat accepted writes");
    assert.equal(created.length, 2);
    assert.ok(created.every((check) => check.conclusion === "success"));
  }
});

test("a known creation can complete after a not-found response without retrying unknown writes", async () => {
  const { github, created } = fixture();
  const update = github.rest.checks.update;
  let refused = false;
  let pauses = 0;
  github.rest.checks.update = async (check) => {
    if (check.check_run_id === 2 && check.conclusion === "success" && !refused) {
      refused = true;
      throw Object.assign(new Error("new check is not visible yet"), { status: 404 });
    }
    return update(check);
  };
  await gate.backfill({
    github, context, core: core(), pullRequest: 1, pause: async () => { pauses++; },
  });
  assert.ok(refused);
  assert.equal(pauses, 1);
  assert.equal(created.length, 2);
  assert.ok(created.every((check) => check.conclusion === "success"));
});

test("persistent completion refusal stays bounded and retains failure fencing", async () => {
  const { github, created } = fixture();
  const update = github.rest.checks.update;
  let completions = 0;
  let pauses = 0;
  github.rest.checks.update = async (check) => {
    if (check.check_run_id === 2 && check.conclusion === "success") {
      completions++;
      throw Object.assign(new Error("new check is not visible yet"), { status: 404 });
    }
    return update(check);
  };
  const output = core();
  await assert.rejects(gate.backfill({
    github, context, core: output, pullRequest: 1, pause: async () => { pauses++; },
  }), /not visible yet/);
  assert.equal(completions, 3);
  assert.equal(pauses, 2);
  assert.ok(created.every((check) => check.status === "completed" && check.conclusion === "failure"));
  assert.deepEqual(output.messages, []);
});

test("lost or unclassified completion responses never replay the write", async () => {
  for (const status of [undefined, 403, 500]) {
    const { github, created } = fixture();
    const update = github.rest.checks.update;
    let completions = 0;
    github.rest.checks.update = async (check) => {
      const result = await update(check);
      if (check.check_run_id === 2 && check.conclusion === "success") {
        completions++;
        throw Object.assign(new Error("completion outcome is unknown"), { status });
      }
      return result;
    };
    const output = core();
    await assert.rejects(gate.backfill({
      github, context, core: output, pullRequest: 1,
      pause: async () => { assert.fail("unknown writes must not be paced into replay"); },
    }), /outcome is unknown/);
    assert.equal(completions, 1);
    assert.ok(created.every((check) => check.conclusion === "failure"));
    assert.deepEqual(output.messages, []);
  }
});

test("a persistent old view exhausts bounded observation and fences every attempted result", async () => {
  const { github, created } = fixture();
  const list = github.rest.checks.listForRef;
  const before = structuredClone((await list({ ref: "head" })).data.check_runs);
  let pauses = 0;
  github.rest.checks.listForRef = async (options) => {
    const result = await list(options);
    if (created.at(-1)?.conclusion === "success") result.data.check_runs[0] = before[0];
    return result;
  };
  const output = core();
  await assert.rejects(gate.backfill({
    github, context, core: output, pullRequest: 1, pause: async () => { pauses++; },
  }), /Owned source check series/);
  assert.equal(pauses, 2);
  assert.ok(created.every((check) => check.status === "completed" && check.conclusion === "failure"));
  assert.deepEqual(output.messages, []);
});

test("a new owned result or changed known identity is not treated as propagation delay", async () => {
  for (const change of ["new-result", "different-snapshot", "different-conclusion"]) {
    const { github, created } = fixture();
    const list = github.rest.checks.listForRef;
    let changed = false;
    github.rest.checks.listForRef = async (options) => {
      if (!changed && created.at(-1)?.conclusion === "success") {
        changed = true;
        if (change === "new-result") created.push({
          ...created[0], conclusion: "failure",
          details_url: "https://github.com/owner/repo/actions/runs/456",
        });
        if (change === "different-snapshot") created[0].external_id = "source-metadata:" + "b".repeat(64);
        if (change === "different-conclusion") created[0].conclusion = "cancelled";
      }
      return list(options);
    };
    const output = core();
    await assert.rejects(gate.backfill({
      github, context, core: output, pullRequest: 1,
      pause: async () => { assert.fail("concurrent changes are not a visibility retry"); },
    }), /Owned source check series/);
    assert.ok(changed);
    assert.equal(created.at(-1).conclusion, "failure");
    assert.deepEqual(output.messages, []);
  }
});

test("source changes during visibility pacing invalidate the attempted success", async () => {
  for (const stage of ["confirmation", "completion"]) {
    const { github, created, values } = fixture();
    const list = github.rest.checks.listForRef;
    const update = github.rest.checks.update;
    if (stage === "confirmation") {
      github.rest.checks.listForRef = async (options) => {
        const result = await list(options);
        if (created.at(-1)?.conclusion === "success") result.data.check_runs.pop();
        return result;
      };
    } else {
      github.rest.checks.update = async (check) => {
        if (check.check_run_id === 2 && check.conclusion === "success") {
          throw Object.assign(new Error("new check is not visible yet"), { status: 404 });
        }
        return update(check);
      };
    }
    const output = core();
    await assert.rejects(gate.backfill({
      github, context, core: output, pullRequest: 1, pause: async () => { values[0].body = ""; },
    }), /metadata changed/);
    assert.ok(created.every((check) => check.conclusion === "failure"));
    assert.deepEqual(output.messages, []);
  }
});

test("confirmation reads share the original request budget and reserved cleanup", async () => {
  const { github, created } = fixture();
  const list = github.rest.checks.listForRef;
  github.rest.checks.listForRef = async (options) => {
    const result = await list(options);
    if (created.at(-1)?.conclusion === "success") result.data.check_runs.pop();
    return result;
  };
  const requests = countRequests(github);
  const output = core();
  await assert.rejects(gate.backfill({
    github, context, core: output, pullRequest: 1, requestLimit: 16, pause: async () => {},
  }), /request budget/);
  assert.ok(requests() <= 16);
  assert.ok(created.every((check) => check.conclusion === "failure"));
  assert.deepEqual(output.messages, []);
});
