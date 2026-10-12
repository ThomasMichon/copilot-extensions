const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { spawnSync } = require("node:child_process");

const workflow = fs.readFileSync(
  path.join(__dirname, "../.github/workflows/workflow-lockdown-guard.yml"), "utf8",
).replace(/\r\n/g, "\n");
const scriptLines = workflow.split(
  "      - name: Request Copilot review as a licensed Maintainer account\n",
)[1].split("          script: |\n")[1]
  .split("\n");
const scriptEnd = scriptLines.findIndex(
  line => line.trim() && !line.startsWith("            "),
);
const script = scriptLines.slice(0, scriptEnd === -1 ? undefined : scriptEnd)
  .filter(line => line.trim()).map(line => {
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
  let clock = NOW + (options.queueMs ?? 0);
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
          return { data: { users: options.pendingUsers ??
            (options.pending ? [{ login: BOT, type: "Bot" }] : []) } };
        },
        listReviews: "reviews",
      },
    },
    paginate: async (method) => {
      reads.push(method);
      return method === "events" ? options.events ?? [] : options.reviews ?? [];
    },
  };
  const constructions = [];
  const writer = { constructor: class {
    constructor(clientOptions) {
      constructions.push(clientOptions);
      assert.equal(clientOptions.auth, "repository-token");
      assert.deepEqual(clientOptions.retry, { enabled: false });
      return reader;
    }
  }, rest: { pulls: { requestReviewers: async params => {
    writes.push(params);
    if (options.error) throw options.error;
  } } } };
  await execute(
    name => { throw new Error(`Bundled action does not expose module: ${name}`); },
    { env: { REVIEW_READ_TOKEN: "repository-token" } },
    { repo: { owner: "owner", repo: "repo" },
      payload: { pull_request: {
        number: 7, head: { sha: "event-head" },
        updated_at: options.updatedAt ?? new Date(NOW).toISOString(),
      } } },
    writer,
    { info: msg => infos.push(msg), setFailed: msg => failures.push(msg),
      warning: msg => warnings.push(msg) },
    { now: () => clock, parse: Date.parse },
    (callback, ms) => { waits.push(ms); clock += ms; callback(); },
  );
  return { writes, reads, waits, failures, warnings, infos, constructions };
}

function review(overrides = {}) {
  return { id: 1, user: { login: BOT, type: "Bot" }, commit_id: "event-head",
    state: "COMMENTED", submitted_at: new Date(NOW).toISOString(), ...overrides };
}

test("one five-minute sleep, one snapshot and one nudge, no post-write polling", async () => {
  const result = await run();
  assert.deepEqual(result.waits, [300_000]);
  assert.deepEqual(result.reads, ["pr", "pending", "events", "reviews"]);
  assert.deepEqual(result.constructions, [{
    auth: "repository-token", retry: { enabled: false },
  }]);
  assert.equal(result.writes.length, 1);
  assert.match(result.infos.at(-1), /not verified/);
});

test("pending review needs only PR and pending reads and no nudge", async () => {
  const result = await run({ pending: true });
  assert.deepEqual(result.reads, ["pr", "pending"]);
  assert.equal(result.writes.length, 0);
});

test("queue time satisfies the event grace without holding a runner", async () => {
  const result = await run({ queueMs: 900_000 });
  assert.deepEqual(result.waits, []);
  assert.equal(result.writes.length, 1);
});

test("only the unelapsed event grace holds the runner", async () => {
  const result = await run({ queueMs: 120_000 });
  assert.deepEqual(result.waits, [180_000]);
  assert.equal(result.writes.length, 1);
});

test("the exact grace boundary needs no additional delay", async () => {
  assert.deepEqual((await run({ queueMs: 300_000 })).waits, []);
});

test("invalid or future PR event time fails before any write", async () => {
  for (const updatedAt of ["invalid", new Date(NOW + 1).toISOString()]) {
    await assert.rejects(run({ updatedAt }));
  }
});

test("a recent request still suppresses nudging after a long queue", async () => {
  const result = await run({
    queueMs: 900_000,
    events: [{
      event: "review_requested", requested_reviewer: { login: BOT, type: "Bot" },
      created_at: new Date(NOW + 840_000).toISOString(),
    }],
  });
  assert.deepEqual(result.waits, []);
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
    events: [{ event: "review_requested", requested_reviewer: { login: BOT, type: "Bot" },
      created_at: new Date(NOW + 240_000).toISOString() }],
  });
  assert.equal(result.writes.length, 0);
});

test("five-minute request boundary allows nudge when still no review", async () => {
  const result = await run({
    events: [{ event: "review_requested", requested_reviewer: { login: BOT, type: "Bot" },
      created_at: new Date(NOW).toISOString() }],
  });
  assert.equal(result.writes.length, 1);
});

test("Copilot Bot alias suppresses pending, recent-request and completed-review nudges", async () => {
  const user = { login: "Copilot", type: "Bot" };
  const pending = await run({ pendingUsers: [user] });
  assert.equal(pending.writes.length, 0);
  assert.deepEqual(pending.reads, ["pr", "pending"]);
  assert.equal((await run({ events: [{
    event: "review_requested", requested_reviewer: user,
    created_at: new Date(NOW + 240_000).toISOString(),
  }] })).writes.length, 0);
  assert.equal((await run({ reviews: [review({ user })] })).writes.length, 0);
});

test("human and unrelated Bot identities cannot suppress a review request", async () => {
  for (const user of [
    { login: "Copilot", type: "User" },
    { login: BOT, type: "User" },
    { login: "other-bot", type: "Bot" },
    { login: "Copilot" },
  ]) {
    assert.equal((await run({ pendingUsers: [user], reviews: [review({ user })],
      events: [{ event: "review_requested", requested_reviewer: user,
        created_at: new Date(NOW + 240_000).toISOString() }],
    })).writes.length, 1);
  }
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
  assert.match(workflow, /REVIEW_READ_TOKEN: \$\{\{ github\.token \}\}/);
  assert.match(workflow, /github-token: \$\{\{ secrets\.MAINTAINER_REVIEW_PAT \}\}/);
  assert.doesNotMatch(script, /require\s*\(/);
});

test("pinned github-script bundle constructs independently authenticated clients", {
  skip: !process.env.COPILOT_REVIEW_ACTION_BUNDLE,
}, () => {
  const bootstrap = `
    const assert = require("node:assert/strict");
    const calls = [];
    const fakeFetch = async (url, init) => {
      const endpoint = new URL(url).pathname;
      const write = init.method === "POST";
      const token = new Headers(init.headers).get("authorization")?.split(" ")[1];
      assert.equal(token, write ? "licensed-token" : "repository-token");
      calls.push([init.method, endpoint]);
      let data;
      if (write) {
        assert.deepEqual(JSON.parse(init.body).reviewers, ["${BOT}"]);
        data = {};
      } else if (endpoint.endsWith("/requested_reviewers")) {
        data = { users: [] };
      } else if (endpoint.endsWith("/pulls/7")) {
        data = { state: "open", draft: false, base: { ref: "dev" },
          head: { sha: "event-head" } };
      } else if (endpoint.endsWith("/timeline") || endpoint.endsWith("/reviews")) {
        data = [];
      } else {
        throw new Error("Unexpected endpoint: " + endpoint);
      }
      return new Response(JSON.stringify(data), { status: 200,
        headers: { "content-type": "application/json" } });
    };
    const Client = github.constructor.defaults({ request: { fetch: fakeFetch } });
    const writer = new Client({ auth: "licensed-token", retry: { enabled: false } });
    context.payload = { pull_request: {
      number: 7, head: { sha: "event-head" },
      updated_at: new Date(${NOW + 300_000}).toISOString(),
    } };
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const run = new AsyncFunction("require", "github", "context", "core", "process",
      "Date", "setTimeout", ${JSON.stringify(script)});
    await run(require, writer, context, core, { env: { REVIEW_READ_TOKEN: "repository-token" } },
      { now: () => ${NOW + 300_000}, parse: Date.parse },
      (callback, ms) => { assert.equal(ms, 300_000); callback(); });
    assert.deepEqual(calls, [
      ["GET", "/repos/owner/repo/pulls/7"],
      ["GET", "/repos/owner/repo/pulls/7/requested_reviewers"],
      ["GET", "/repos/owner/repo/issues/7/timeline"],
      ["GET", "/repos/owner/repo/pulls/7/reviews"],
      ["POST", "/repos/owner/repo/pulls/7/requested_reviewers"],
    ]);
  `;
  const result = spawnSync(process.execPath, [
    path.resolve(process.env.COPILOT_REVIEW_ACTION_BUNDLE),
  ], {
    encoding: "utf8",
    windowsHide: true,
    timeout: 15_000,
    env: {
      PATH: process.env.PATH,
      "INPUT_GITHUB-TOKEN": "licensed-token",
      INPUT_DEBUG: "false",
      INPUT_SCRIPT: bootstrap,
      INPUT_RETRIES: "0",
      GITHUB_REPOSITORY: "owner/repo",
      GITHUB_API_URL: "https://api.github.com",
    },
  });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
