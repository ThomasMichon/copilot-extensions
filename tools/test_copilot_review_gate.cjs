const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const workflow = fs.readFileSync(
  path.join(__dirname, "../.github/workflows/copilot-review-gate.yml"),
  "utf8",
);
const requestStep = workflow.split(
  "      - name: Request Copilot review as a licensed Maintainer account\n",
)[1];
assert.ok(requestStep);
const script = requestStep.split("          script: |\n")[1]
  .split("\n")
  .filter((line) => line.trim())
  .map((line) => {
    assert.ok(line.startsWith("            "));
    return line.slice(12);
  })
  .join("\n");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const runScript = new AsyncFunction(
  "require", "process", "context", "github", "core", "Date", "setTimeout", script,
);
const NOW = Date.parse("2026-10-11T03:00:00Z");
const BOT = "copilot-pull-request-reviewer[bot]";

async function run(options = {}) {
  let clock = NOW;
  const waits = [];
  const writes = [];
  const failures = [];
  const warnings = [];
  const reads = [];
  let reviewReads = 0;
  let pendingReadsBeforeWrite = 0;
  const reader = {
    rest: {
      issues: { listEventsForTimeline: "timeline" },
      pulls: {
        listReviews: "reviews",
        get: async () => {
          reads.push("get");
          return {
            data: {
              state: options.state ?? "open",
              draft: options.draft ?? false,
              base: { ref: options.base ?? "dev" },
              head: { sha: "latest-head" },
            },
          };
        },
        listRequestedReviewers: async () => {
          reads.push("requested");
          if (writes.length === 0) {
            pendingReadsBeforeWrite += 1;
            const pending = options.pendingForever ||
              pendingReadsBeforeWrite <= (options.pendingReadsBeforeWrite ?? 0);
            return { data: { users: pending ? [{ login: BOT }] : [] } };
          }
          return { data: { users: options.pending === false ? [] : [{ login: BOT }] } };
        },
      },
    },
    paginate: async (method) => {
      reads.push(method);
      if (method === "timeline") return options.events ?? [];
      assert.equal(method, "reviews");
      reviewReads += 1;
      if (reviewReads === 1) return options.existingReviews ?? [];
      return writes.length === 0
        ? options.reviewsAfterPending ?? [] : options.newReviews ?? [];
    },
  };
  const writer = {
    rest: {
      pulls: {
        requestReviewers: async (params) => {
          writes.push(params);
          if (options.writeError) throw options.writeError;
        },
      },
    },
  };
  await runScript(
    (name) => {
      assert.equal(name, "@actions/github");
      return {
        getOctokit: (token) => {
          assert.equal(token, "repository-read-token");
          return reader;
        },
      };
    },
    { env: { REVIEW_READ_TOKEN: "repository-read-token" } },
    {
      repo: { owner: "owner", repo: "repo" },
      payload: { pull_request: { user: { login: "maintainer" }, number: 7,
        head: { sha: "old-event-head" } } },
    },
    writer,
    {
      info: () => {},
      setFailed: (message) => failures.push(message),
      warning: (message) => warnings.push(message),
    },
    { now: () => clock, parse: Date.parse },
    (callback, delay) => {
      waits.push(delay);
      clock += delay;
      callback();
    },
  );
  return { waits, writes, failures, warnings, reads };
}

test("requests latest head with repository-token reads and only one owner write", async () => {
  const result = await run({
    pending: false,
    newReviews: [{ user: { login: BOT }, commit_id: "latest-head", id: 1,
      state: "COMMENTED", submitted_at: new Date(NOW).toISOString() }],
  });
  assert.equal(result.writes.length, 1);
  assert.equal(result.failures.length, 0);
  assert.ok(result.reads.includes("requested"));
});

test("coalesces at the five-minute boundary using native request time", async () => {
  const result = await run({
    events: [{ event: "review_requested", requested_reviewer: { login: BOT },
      created_at: new Date(NOW - 120_000).toISOString() }],
  });
  assert.deepEqual(result.waits, [180_000]);
  assert.equal(result.writes.length, 1);
});

test("ignores unrelated reviewer events and expired cooldowns", async () => {
  const result = await run({
    events: [
      { event: "review_requested", requested_reviewer: { login: "human" },
        created_at: new Date(NOW).toISOString() },
      { event: "review_requested", requested_reviewer: { login: BOT },
        created_at: new Date(NOW - 300_000).toISOString() },
    ],
  });
  assert.deepEqual(result.waits, []);
  assert.equal(result.writes.length, 1);
});

test("skips duplicate completed reviews of live head", async () => {
  const result = await run({
    existingReviews: [{ user: { login: BOT }, commit_id: "latest-head", id: 1,
      state: "APPROVED", submitted_at: new Date(NOW).toISOString() }],
  });
  assert.equal(result.writes.length, 0);
});

test("pending or dismissed reviews do not suppress an automatic request", async () => {
  for (const state of ["PENDING", "DISMISSED"]) {
    const result = await run({
      existingReviews: [{ user: { login: BOT }, commit_id: "latest-head", id: 1,
        state, submitted_at: state === "PENDING" ? null : new Date(NOW).toISOString() }],
    });
    assert.equal(result.writes.length, 1);
  }
});

for (const options of [{ state: "closed" }, { draft: true }, { base: "main" }]) {
  test(`skips no-longer-eligible PR ${JSON.stringify(options)}`, async () => {
    assert.equal((await run(options)).writes.length, 0);
  });
}

test("pending review completes at latest head without duplicate write", async () => {
  const result = await run({
    pendingReadsBeforeWrite: 2,
    reviewsAfterPending: [{ user: { login: BOT }, commit_id: "latest-head", id: 2,
      state: "APPROVED", submitted_at: new Date(NOW).toISOString() }],
  });
  assert.equal(result.writes.length, 0);
  assert.deepEqual(result.waits, [30_000, 30_000]);
});

test("old-head pending review completes then requests live head", async () => {
  const result = await run({
    pendingReadsBeforeWrite: 1,
    reviewsAfterPending: [{ user: { login: BOT }, commit_id: "old-event-head", id: 2,
      state: "APPROVED", submitted_at: new Date(NOW).toISOString() }],
  });
  assert.equal(result.writes.length, 1);
  assert.equal(result.reads.filter((item) => item === "get").length, 2);
});

test("long-pending review defers explicitly without any owner write", async () => {
  const result = await run({ pendingForever: true });
  assert.equal(result.writes.length, 0);
  assert.deepEqual(result.waits, Array(10).fill(30_000));
  assert.match(result.failures[0], /deferred without a duplicate request/);
});

test("confirmation remains bounded at 20 polls spaced 30 seconds apart", async () => {
  const result = await run({ pending: false });
  assert.equal(result.writes.length, 1);
  assert.equal(result.reads.filter((item) => item === "requested").length, 21);
  assert.deepEqual(result.waits, Array(19).fill(30_000));
  assert.equal(result.failures.length, 1);
});

test("rate-limit write failure reports provider backoff without retry", async () => {
  const result = await run({
    writeError: { message: "rate limit exceeded", status: 403,
      response: { headers: { "x-ratelimit-remaining": "0",
        "x-ratelimit-reset": "1791687600", "retry-after": "60" } } },
  });
  assert.equal(result.writes.length, 1);
  assert.equal(result.failures.length, 1);
  assert.match(result.warnings[0], /retry-after=60; reset-epoch=1791687600/);
});

test("invalid or future cooldown timestamps fail before the write", async () => {
  for (const created_at of ["invalid", new Date(NOW + 60_000).toISOString()]) {
    await assert.rejects(run({
      events: [{ event: "review_requested", requested_reviewer: { login: BOT }, created_at }],
    }));
  }
});

test("per-PR workflow concurrency retains active confirmation", () => {
  assert.match(workflow, /group: copilot-review-request-.*pull_request\.number/);
  assert.match(workflow, /cancel-in-progress: false/);
});
