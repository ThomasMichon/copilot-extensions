const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const workflow = fs.readFileSync(
  path.join(__dirname, "../.github/workflows/copilot-review-gate.yml"), "utf8",
);
const script = workflow.split(
  "      - name: Request Copilot review as a licensed Maintainer account\n",
)[1].split("          script: |\n")[1]
  .split("\n").filter(line => line.trim()).map(line => {
    assert.ok(line.startsWith("            "));
    return line.slice(12);
  }).join("\n");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const execute = new AsyncFunction(
  "require", "process", "context", "github", "core", "Date", "setTimeout", script,
);
const NOW = Date.parse("2026-10-11T03:00:00Z");
const BOT = "copilot-pull-request-reviewer[bot]";

async function run(options = {}) {
  const writes = [], reads = [], waits = [], failures = [], warnings = [], infos = [];
  let clock = NOW;
  const reader = {
    rest: {
      issues: { listEventsForTimeline: "events" },
      pulls: {
        get: async () => {
          reads.push("pr");
          return { data: {
            state: options.state ?? "open",
            draft: options.draft ?? false,
            base: { ref: options.base ?? "dev" },
            head: { sha: options.head ?? "event-head" },
          } };
        },
        listRequestedReviewers: async () => {
          reads.push("pending");
          return { data: { users: options.pending ? [{ login: BOT }] : [] } };
        },
        listReviews: "reviews",
      },
    },
    paginate: async (method) => {
      reads.push(method);
      return method === "events" ? options.events ?? [] : options.reviews ?? [];
    },
  };
  const writer = { rest: { pulls: { requestReviewers: async params => {
    writes.push(params);
    if (options.error) throw options.error;
  } } } };
  await execute(
    name => {
      assert.equal(name, "@actions/github");
      return { getOctokit: token => {
        assert.equal(token, "repository-token");
        return reader;
      } };
    },
    { env: { REVIEW_READ_TOKEN: "repository-token" } },
    { repo: { owner: "owner", repo: "repo" },
      payload: { pull_request: { number: 7, head: { sha: "event-head" } } } },
    writer,
    { info: msg => infos.push(msg), setFailed: msg => failures.push(msg),
      warning: msg => warnings.push(msg) },
    { now: () => clock, parse: Date.parse },
    (callback, ms) => { waits.push(ms); clock += ms; callback(); },
  );
  return { writes, reads, waits, failures, warnings, infos };
}

function review(overrides = {}) {
  return { id: 1, user: { login: BOT }, commit_id: "event-head",
    state: "COMMENTED", submitted_at: new Date(NOW).toISOString(), ...overrides };
}

test("one five-minute sleep, one snapshot and one nudge, no post-write polling", async () => {
  const result = await run();
  assert.deepEqual(result.waits, [300_000]);
  assert.deepEqual(result.reads, ["pr", "pending", "events", "reviews"]);
  assert.equal(result.writes.length, 1);
  assert.match(result.infos.at(-1), /not verified/);
});

test("pending review needs only PR and pending reads and no nudge", async () => {
  const result = await run({ pending: true });
  assert.deepEqual(result.reads, ["pr", "pending"]);
  assert.equal(result.writes.length, 0);
});

test("completed current-head review suppresses nudge", async () => {
  assert.equal((await run({ reviews: [review()] })).writes.length, 0);
});

for (const options of [{ state: "closed" }, { draft: true }, { base: "main" }]) {
  test(`ineligible PR ${JSON.stringify(options)} never nudged`, async () => {
    const result = await run(options);
    assert.deepEqual(result.reads, ["pr"]);
    assert.equal(result.writes.length, 0);
  });
}

test("newer push leaves full grace period to latest queued run", async () => {
  assert.equal((await run({ head: "newer-head" })).writes.length, 0);
});

for (const state of ["PENDING", "DISMISSED"]) {
  test(`${state} review cannot masquerade as completion`, async () => {
    assert.equal((await run({ reviews: [review({ state })] })).writes.length, 1);
  });
}

for (const event of ["reopened", "base_ref_changed"]) {
  test(`${event} invalidates old same-head review`, async () => {
    const result = await run({
      events: [{ event, created_at: new Date(NOW).toISOString() }],
      reviews: [review({ submitted_at: new Date(NOW - 60_000).toISOString() })],
    });
    assert.equal(result.writes.length, 1);
  });
}

test("post-retarget review suppresses new nudge", async () => {
  assert.equal((await run({
    events: [{ event: "base_ref_changed", created_at: new Date(NOW - 60_000).toISOString() }],
    reviews: [review()],
  })).writes.length, 0);
});

test("recent native/manual request suppresses duplicate nudge", async () => {
  const result = await run({
    events: [{ event: "review_requested", requested_reviewer: { login: BOT },
      created_at: new Date(NOW + 240_000).toISOString() }],
  });
  assert.equal(result.writes.length, 0);
});

test("five-minute request boundary allows nudge when still no review", async () => {
  const result = await run({
    events: [{ event: "review_requested", requested_reviewer: { login: BOT },
      created_at: new Date(NOW).toISOString() }],
  });
  assert.equal(result.writes.length, 1);
});

test("rate-limit failure reports headers and performs no retry", async () => {
  const result = await run({
    error: { message: "quota exhausted", response: { headers: {
      "retry-after": "60", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1791687600",
    } } },
  });
  assert.equal(result.writes.length, 1);
  assert.equal(result.failures.length, 1);
  assert.match(result.warnings[0], /retry-after=60/);
});

test("invalid or future timeline state fails before write", async () => {
  for (const created_at of ["invalid", new Date(NOW + 360_000).toISOString()]) {
    await assert.rejects(run({
      events: [{ event: "base_ref_changed", created_at }],
    }));
  }
});

test("workflow preserves author trust and per-PR serialization", () => {
  assert.match(workflow, /repos\.checkCollaborator/);
  assert.match(workflow, /CODEOWNERS/);
  assert.match(workflow, /steps\.status\.outputs\.is_maintainer == 'true'/);
  assert.match(workflow, /cancel-in-progress: false/);
  assert.match(workflow, /group: copilot-review-request-.*pull_request\.number/);
});
