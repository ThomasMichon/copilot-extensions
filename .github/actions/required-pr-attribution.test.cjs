const { test } = require("node:test");
const assert = require("node:assert/strict");
const gate = require("./required-pr-attribution.cjs");

const marker = "<!-- agent-worktrees:source codename=clear-compass -->";
const context = {
  repo: { owner: "owner", repo: "repo" }, payload: { pull_request: { number: 1, head: { sha: "head" } } },
  serverUrl: "https://github.com", runId: 123,
};
function pr(body, labels = []) {
  return { body, labels, base: { ref: "dev" }, state: "open", head: { sha: "head" } };
}
function paged(invoke) {
  invoke.iterator = async function* (method, options) {
    const values = await invoke(method, options);
    for (let start = 0; start < Math.max(1, values.length); start += options.per_page) {
      const next = start + options.per_page < values.length;
      yield { data: values.slice(start, start + options.per_page),
        headers: next ? { link: '<https://api.github.com/next>; rel="next"' } : {} };
    }
  };
  return invoke;
}
function countRequests(github) {
  let requests = 0;
  for (const endpoints of Object.values(github.rest)) {
    for (const [name, invoke] of Object.entries(endpoints)) {
      endpoints[name] = async (...params) => { requests++; return invoke(...params); };
    }
  }
  const paginate = github.paginate;
  github.paginate = paged(async (method, options) => {
    const result = await paginate(method, options);
    requests += Math.max(1, Math.ceil(result.length / options.per_page));
    return result;
  });
  return () => requests;
}
function core() {
  const messages = [];
  const summary = {
    addHeading(value) { messages.push(value); return this; },
    addRaw(value) { messages.push(value); return this; },
    async write() {},
  };
  return {
    messages, summary, setFailed: (value) => messages.push(value),
    info: (value) => messages.push(value), warning: (value) => messages.push(value),
  };
}

test("public source and workflow forms use the existing envelope", () => {
  assert.equal(gate.validate(marker), null);
  const token = Buffer.concat([Buffer.from("AWI1"), Buffer.alloc(28)]).toString("base64");
  assert.equal(gate.validate(marker.replace(" -->", ` root=other-handle enc=${token} -->`)), null);
  assert.equal(gate.validate("<!-- agent-worktrees:source workflow=purge-consumed-changefiles run=123 -->"), null);
  assert.equal(gate.validate("<!-- agent-worktrees:source workflow=maintenance -->"), null);
  assert.equal(gate.validate("The agent-worktrees:source format is documented.\n" + marker), null);
  assert.equal(gate.validate("Example: `" + marker + "`\n" + marker), null);
  assert.equal(gate.validate("Example: ``" + marker + "``\n" + marker), null);
});

test("malformed, duplicate, raw and quoted forms fail without reflecting their values", () => {
  for (const body of [
    "", null, "Authored description", `${marker}\n${marker}`,
    marker.replace("codename=clear-compass", "codename=Bad-Handle"),
    marker.replace(" -->", " codename=secret-value -->"),
    marker.replace(" -->", " machine=secret-machine -->"),
    marker.replace(" -->", " workflow=mixed-origin -->"),
    marker.replace(" -->", " enc=secret-value -->"),
    marker.replace(" -->", " root=private/value -->"),
    "```text\n" + marker + "\n```", "~~~\n" + marker + "\n~~~",
    "prefix " + marker, "    " + marker,
    "<!-- agent-worktrees:source workflow=origin run=not-numeric -->",
    "<!-- agent-worktrees:source codename=clear-compass",
  ]) {
    const error = gate.validate(body);
    assert.equal(typeof error, "string");
    assert.ok(!error.includes("secret-value") && !error.includes("secret-machine"));
  }
});

test("unsupported description types and sizes have distinct actionable diagnostics", () => {
  assert.match(gate.validate({}), /description must be text/);
  assert.match(gate.validate("x".repeat(65537)), /65,536-character limit; shorten/);
  assert.equal(gate.validate(marker + "\n" + "x".repeat(65536 - marker.length - 1)), null);
});

test("silent approvals emit no attribution diagnostic", async () => {
  for (const value of [
    pr("", [{ name: "attribution-exempt" }]),
    pr(marker),
  ]) {
    const output = core();
    await gate.enforce({
      context, core: output,
      github: {
        paginate: paged(async () => [{ ...value, number: 1 }]),
        rest: { repos: { listPullRequestsAssociatedWithCommit() {} },
          pulls: { list() {}, get: async () => ({ data: value }) } },
      },
    });
    assert.deepEqual(output.messages, []);
  }
});

test("Markdown container fences cannot turn examples into source metadata", () => {
  const native = "<!-- gh-aw-workflow-id: maintenance -->";
  for (const example of [
    "- ```html\n  SOURCE\n  ```",
    "1. ~~~html\n   SOURCE\n   ~~~",
    "- outer\n  - ```html\n    SOURCE\n    ```",
    "> - ```html\n>   SOURCE\n>   ```",
    "- ```html\n  SOURCE\n  ````",
    "- ```html\n  SOURCE",
  ]) {
    for (const source of [marker, native]) {
      const body = example.replace("SOURCE", source);
      assert.ok(gate.validate(body), "an example alone must fail");
      if (example.endsWith("```") || example.endsWith("~~~") || example.endsWith("````")) {
        assert.equal(gate.validate(body + "\n\n" + source), null,
                     "a real source after a closed example must pass");
      }
    }
  }
  assert.equal(gate.validate("```not `a valid opener`\n" + marker), null);
  assert.ok(gate.validate("- ```html\n  " + marker + "\n  ```\n" + native) === null);
});

test("multiline code spans hide examples without changing actual comment fields", () => {
  const native = "<!-- gh-aw-workflow-id: maintenance -->";
  for (const source of [marker, native]) {
    for (const ticks of ["`", "``", "````"]) {
      const example = "Example: " + ticks + "\n" + source + "\nend" + ticks;
      assert.ok(gate.validate(example));
      assert.equal(gate.validate(example + "\n\n" + source), null);
    }
    assert.ok(gate.validate("Example: `\n" + source + "\n`"));
    assert.ok(gate.validate("Example: ``\n" + source + "\n``"));
    assert.equal(gate.validate("Unmatched ` example\n\n" + source + "\n\n`"), null);
  }
  assert.ok(gate.validate(marker.replace(" -->", " `machine=raw` -->")));
  assert.ok(gate.validate(marker.replace("codename=clear-compass", "codename=`clear-compass`")));
  assert.ok(gate.validate(marker + "\n<!-- gh-aw-workflow-id: `maintenance` -->"));
  assert.ok(gate.validate("<!-- documented example\n" + marker + "\n-->"));
  assert.equal(gate.validate("<!-- documented example\n" + marker + "\n-->\n\n" + marker), null);
});

test("list continuation and quoted containers retain their rendered code boundaries", () => {
  const native = "<!-- gh-aw-workflow-id: maintenance -->";
  for (const source of [marker, native]) {
    for (const body of [
      "- Example\n    ```html\n  SOURCE\n    ```",
      "1. Example\n     ~~~html\n   SOURCE\n     ~~~",
      "- Outer\n  - Example\n      ```html\n    SOURCE\n      ```",
      "> - Example\n>     ```html\n>   SOURCE\n>     ```",
    ]) {
      const example = body.replace("SOURCE", source);
      assert.ok(gate.validate(example));
      assert.equal(gate.validate(example + "\n\n" + source), null);
    }
    assert.ok(gate.validate("\\<!-- example -->\n\n\\" + source));
    assert.equal(gate.validate("\\" + source + "\n\n" + source), null);
  }
});

test("HTML containers cannot conceal duplicate, conflicting or raw source comments", () => {
  const native = "<!-- gh-aw-workflow-id: maintenance -->";
  for (const tag of ["div", "details", "template"]) {
    const wrap = (source) => `<${tag}>\n${source}\n</${tag}>`;
    assert.equal(gate.validate(wrap(marker)), null);
    assert.ok(gate.validate(wrap(marker) + "\n\n" + marker));
    assert.ok(gate.validate(wrap(native) + "\n\n" + marker));
    assert.ok(gate.validate(wrap(marker.replace(" -->", " machine=private-source -->"))));
    assert.ok(gate.validate(wrap("`\n" + marker + "\n`") + "\n\n" + marker));
  }
  for (const example of [
    `<div title="${marker}"></div>`,
    `<script>\n${marker}\n</script>`,
    `<svg>\n<![CDATA[\n${marker}\n]]>\n</svg>`,
    `<blockquote><template>\n${marker}\n</template></blockquote>`,
    `<pre>\n${marker}\n</pre>`,
    "<!-- documented example\n" + marker + "\n-->",
  ]) {
    assert.ok(gate.validate(example));
    assert.equal(gate.validate(example + "\n\n" + marker), null);
  }
});

test("indented or container-prefixed fence content cannot close another fence", () => {
  for (const body of [
    "```html\n    ```\n" + marker + "\n```",
    "~~~html\n    ~~~\n" + marker + "\n~~~",
    "```html\n> ```\n" + marker + "\n```",
    "```html\n- ```\n" + marker + "\n```",
    "- ```html\n      ```\n  " + marker + "\n  ```",
    "> ```html\n>     ```\n> " + marker + "\n> ```",
  ]) {
    assert.ok(gate.validate(body));
    assert.equal(gate.validate(body + "\n\n" + marker), null);
  }
  assert.equal(gate.validate("    ```not a fence\n\n" + marker), null);
});

test("quoted and indented examples neither satisfy nor conflict with standalone metadata", () => {
  const native = "<!-- gh-aw-workflow-id: maintenance -->";
  for (const example of [
    "> " + marker, "> > " + marker, "   > " + native,
    "    " + marker, "\t" + native,
    "> Example\n>\n> " + marker + "\n> " + native,
  ]) {
    assert.ok(gate.validate(example));
    assert.equal(gate.validate(example + "\n\n" + marker), null);
    assert.equal(gate.validate(example + "\n\n" + native), null);
  }
});

test("current description is reread and body-only repairs change the verdict", async () => {
  let current = pr("");
  const github = {
    paginate: paged(async () => [{ ...current, number: 1 }]),
    rest: { repos: { listPullRequestsAssociatedWithCommit() {} },
      pulls: { list() {}, get: async () => ({ data: current }) } },
  };
  const failed = core();
  await gate.enforce({ github, context, core: failed });
  assert.ok(failed.messages.join("\n").includes("attribution-prepare --body-file"));
  assert.ok(failed.messages.join("\n").includes("```text"));
  assert.ok(!failed.messages.join("\n").includes("attribution-exempt"));
  current = pr(marker);
  const passed = core();
  await gate.enforce({ github, context, core: passed });
  assert.deepEqual(passed.messages, []);
  current = pr("", [{ name: "attribution-exempt" }]);
  assert.equal(gate.decision(current), null);
  current.labels = [];
  assert.ok(gate.decision(current));
});

function headFixture(values) {
  const created = [];
  const github = {
    paginate: paged(async (_method, options) => structuredClone(values.filter((value) =>
      value.state === "open" && value.base.ref === "dev" &&
      (!options.commit_sha || value.head.sha === options.commit_sha),
    ))),
    rest: {
      repos: { listPullRequestsAssociatedWithCommit() {} },
      pulls: { list() {}, get: async ({ pull_number }) => ({
        data: structuredClone(values.find((value) => value.number === pull_number)),
      }) },
      checks: {
        listForRef: async ({ ref }) => ({ data: { check_runs: created
          .map((check, index) => ({ ...check, id: index + 1, app: { slug: "github-actions" } }))
          .filter((check) => check.head_sha === ref) } }),
        create: async (check) => { created.push(check); return { data: { id: created.length } }; },
        get: async ({ check_run_id }) => ({ data: created[check_run_id - 1] }),
        update: async (check) => {
          assert.notEqual(created[check.check_run_id - 1].status, "completed");
          Object.assign(created[check.check_run_id - 1], check);
        },
      },
    },
  };
  return { github, created };
}

test("native checks fail shared heads regardless of individual source decisions", async () => {
  for (const peer of [pr(marker), pr(""), pr("malformed source"), pr("", [{ name: "attribution-exempt" }])]) {
    const values = [{ ...pr(marker), number: 1 }, { ...peer, number: 2 }];
    const { github } = headFixture(values);
    for (const number of [1, 2]) {
      const output = core();
      await gate.enforce({
        github, core: output,
        context: { ...context, payload: { pull_request: { number, head: { sha: "head" } } } },
      });
      assert.ok(output.messages.join("\n").includes("Multiple open dev pull requests"));
      assert.ok(!output.messages.join("\n").includes("attribution-exempt"));
    }
    values[1].head = { sha: "unique-peer" };
    const output = core();
    await gate.enforce({ github, context, core: output });
    assert.deepEqual(output.messages, []);
  }
});

test("backfill cannot bless opposite decisions on a shared SHA in either order", async () => {
  for (const reverse of [false, true]) {
    const values = [{ ...pr(marker), number: 1 }, { ...pr(""), number: 2 }];
    if (reverse) values.reverse();
    const { github, created } = headFixture(values);
    const args = { github, context, core: core(), pause: async () => {} };
    await gate.backfill(args);
    assert.ok(created.length);
    assert.ok(created.every((check) => check.conclusion === "failure"));
    values.find((value) => value.number === 2).body = marker;
    await gate.backfill(args);
    assert.ok(created.every((check) => check.conclusion === "failure"));
    values.find((value) => value.number === 2).head = { sha: "unique-peer" };
    await gate.backfill(args);
    assert.equal(created.at(-1).conclusion, "success");
    assert.equal(created.filter((check) => check.head_sha === "head").at(-1).conclusion, "success");
  }
});

test("peer membership changes invalidate refresh publication and deduplicated results", async () => {
  for (const stage of ["pause", "create", "complete", "deduplicate"]) {
    const values = [{ ...pr(marker), number: 1 }];
    const { github, created } = headFixture(values);
    const args = { github, context, core: core(), pause: async () => {} };
    if (stage === "deduplicate") await gate.backfill(args);
    let joined = false;
    const join = () => {
      if (joined) return;
      joined = true;
      values.push({ ...pr(""), number: 2 });
    };
    const create = github.rest.checks.create;
    const update = github.rest.checks.update;
    const paginate = github.paginate;
    let lists = 0;
    github.paginate = paged(async (...params) => {
      // Initial batch listing, observed policy, then deduplication reread.
      if (stage === "deduplicate" && ++lists === 3) join();
      return paginate(...params);
    });
    github.rest.checks.create = async (check) => {
      const result = await create(check);
      if (stage === "create" && check.status === "in_progress") join();
      return result;
    };
    github.rest.checks.update = async (check) => {
      const result = await update(check);
      if (stage === "complete" && check.conclusion === "success") join();
      return result;
    };
    args.pause = async () => { if (stage === "pause") join(); };
    await gate.backfill(args);
    assert.ok(joined);
    assert.equal(created.at(-1).conclusion, "failure");
    values[1].state = "closed";
    await gate.backfill(args);
    assert.equal(created.at(-1).conclusion, "success");
  }
});

test("native enforcement refuses stale event heads and changing membership", async () => {
  for (const stale of ["head", "base", "state", "membership"]) {
    const values = [{ ...pr(marker), number: 1 }];
    const { github } = headFixture(values);
    if (stale === "head") values[0].head = { sha: "new-head" };
    if (stale === "base") values[0].base = { ref: "main" };
    if (stale === "state") values[0].state = "closed";
    if (stale === "membership") {
      const paginate = github.paginate;
      let lists = 0;
      github.paginate = paged(async (...params) => {
        if (++lists === 2) values.push({ ...pr(""), number: 2 });
        return paginate(...params);
      });
    }
    const output = core();
    await gate.enforce({ github, context, core: output });
    assert.ok(output.messages.join("\n").includes("metadata changed or could not be verified"));
  }
});

test("separate departure events recover remaining peers without an edit to their PR", async () => {
  for (const action of ["synchronize", "closed", "edited"]) {
    for (const validPeer of [false, true]) {
      const old = "a".repeat(40);
      const next = "b".repeat(40);
      const values = [
        { ...pr(validPeer ? marker : ""), number: 1, head: { sha: old } },
        { ...pr(marker), number: 2, head: { sha: old } },
      ];
      const { github, created } = headFixture(values);
      const event = { ...context, payload: {
        action, before: old,
        pull_request: { ...values[1], head: { sha: old } },
      } };
      const collision = core();
      await gate.enforce({ github, context: event, core: collision });
      assert.ok(collision.messages.join("\n").includes("Multiple open dev pull requests"));
      created.push({ name: "source-attribution", head_sha: old, status: "completed",
        conclusion: "failure", external_id: "native-collision" });
      if (action === "synchronize") {
        values[1].head = { sha: next };
        event.payload.pull_request = values[1];
      } else if (action === "closed") {
        values[1].state = "closed";
      } else {
        values[1].base = { ref: "main" };
        event.payload.pull_request = values[1];
        event.payload.changes = { base: { ref: { from: "dev" } } };
      }
      const unchangedPeer = JSON.stringify(values[0]);
      await gate.departed({ github, context: event, core: core(), pause: async () => {} });
      assert.equal(created.filter((check) => check.head_sha === old).at(-1).conclusion,
                   validPeer ? "success" : "failure");
      assert.equal(JSON.stringify(values[0]), unchangedPeer);
      assert.ok(created.every((check) => check.head_sha === old), "only the departed head is refreshed");
      if (action === "synchronize") {
        const ownResult = core();
        await gate.enforce({ github, context: event, core: ownResult });
        assert.deepEqual(ownResult.messages, []);
      }
    }
  }
});

test("non-departure events have no check writes and missing or invalid heads fail explicitly", async () => {
  for (const action of ["opened", "reopened", "labeled", "unlabeled", "edited", "closed", "synchronize"]) {
    const payload = { action, pull_request: { ...pr(marker), number: 2, base: { ref: "main" } } };
    await gate.departed({ github: {}, context: { ...context, payload }, core: core() });
  }
  const payload = { action: "synchronize", pull_request: { ...pr(marker), number: 2 } };
  await assert.rejects(gate.departed({ github: {}, context: { ...context, payload }, core: core() }),
                       /Missing departed head metadata/);
  payload.before = "unsafe/private-head";
  await assert.rejects(gate.departed({ github: {}, context: { ...context, payload }, core: core() }),
                       /Invalid departed head metadata/);
});

test("a remaining peer moving during departure refresh cannot redirect writes to its new head", async () => {
  const old = "a".repeat(40);
  const next = "b".repeat(40);
  const values = [{ ...pr(marker), number: 1, head: { sha: old } }];
  const { github, created } = headFixture(values);
  const event = { ...context, payload: {
    action: "closed", pull_request: { ...pr(marker), number: 2, head: { sha: old } },
  } };
  await gate.departed({
    github, context: event, core: core(),
    pause: async () => { values[0].head = { sha: next }; },
  });
  assert.ok(created.length);
  assert.ok(created.every((check) => check.head_sha === old));
  assert.equal(created.at(-1).conclusion, "failure");
});

test("a peer leaving during publication supersedes the old collision snapshot", async () => {
  const values = [{ ...pr(marker), number: 1 }, { ...pr(""), number: 2 }];
  const { github, created } = headFixture(values);
  const update = github.rest.checks.update;
  let left = false;
  github.rest.checks.update = async (check) => {
    const result = await update(check);
    if (!left) { left = true; values[1].state = "closed"; }
    return result;
  };
  await gate.backfill({ github, context, core: core(), pause: async () => {} });
  assert.equal(created[0].conclusion, "failure");
  assert.ok(created[1].external_id.endsWith(":superseded"));
  assert.equal(created.at(-1).conclusion, "success");
});

test("normal refreshes list all open PRs once and query only associated heads thereafter", async () => {
  const values = Array.from({ length: 150 }, (_, index) => ({
    ...pr(marker), number: index + 1, head: { sha: `head-${index}` },
  }));
  const { github, created } = headFixture(values);
  const paginate = github.paginate;
  let fullLists = 0;
  let associations = 0;
  let requests = 0;
  for (const endpoints of Object.values(github.rest)) {
    for (const [name, invoke] of Object.entries(endpoints)) {
      endpoints[name] = async (...params) => { requests++; return invoke(...params); };
    }
  }
  github.paginate = paged(async (method, options) => {
    if (method === github.rest.pulls.list) { fullLists++; assert.equal(options.commit_sha, undefined); }
    else { associations++; assert.ok(options.commit_sha); }
    const result = await paginate(method, options);
    requests += Math.max(1, Math.ceil(result.length / options.per_page));
    return result;
  });
  await gate.backfill({ github, context, core: core(), pause: async () => {} });
  assert.equal(fullLists, 1);
  assert.equal(created.length, values.length);
  assert.equal(associations, values.length * 3);
  assert.equal(requests, 902, "all read/write/page requests fit the standard 1000-request Actions quota");
  assert.ok(created.every((check) => check.conclusion === "success"));
});

test("larger refreshes stop explicitly before exhausting the Actions request quota", async () => {
  const values = Array.from({ length: 200 }, (_, index) => ({
    ...pr(marker), number: index + 1, head: { sha: `head-${index}` },
  }));
  const { github, created } = headFixture(values);
  const requests = countRequests(github);
  const output = core();
  await assert.rejects(gate.backfill({ github, context, core: output, pause: async () => {} }),
                       /request budget; retry after the rate-limit reset/);
  assert.ok(requests() <= 950);
  assert.ok(created.length < values.length + 1);
  assert.ok(created.every((check) => check.status === "completed"));
  assert.equal(created.at(-1).conclusion, "failure");
  assert.ok(!output.messages.includes("Current dev pull request metadata checks refreshed."));
});

test("budget exhaustion reserves failure fencing before and after completion", async () => {
  for (const requestLimit of [7, 8, 9]) {
    const { github, created } = headFixture([{ ...pr(marker), number: 1 }]);
    const requests = countRequests(github);
    await assert.rejects(gate.backfill({
      github, context, core: core(), requestLimit, pause: async () => {},
    }), /request budget/);
    assert.ok(requests() <= requestLimit);
    assert.ok(created.every((check) => check.status === "completed"));
    assert.equal(created.at(-1).conclusion, "failure");
    if (requestLimit === 9) assert.equal(created[0].conclusion, "success",
                                       "the completed conclusion is superseded, never rewritten");
  }
});

test("budget exhaustion fences an existing success when deduplication cannot be verified", async () => {
  const { github, created } = headFixture([{ ...pr(marker), number: 1 }]);
  await gate.backfill({ github, context, core: core(), pause: async () => {} });
  assert.equal(created[0].conclusion, "success");
  const requests = countRequests(github);
  await assert.rejects(gate.backfill({
    github, context, core: core(), requestLimit: 6, pause: async () => {},
  }), /request budget/);
  assert.ok(requests() <= 6);
  assert.equal(created[0].conclusion, "success");
  assert.equal(created.at(-1).conclusion, "failure");
  assert.ok(created.at(-1).external_id.endsWith(":superseded"));
});

test("each pagination page is admitted before its request, including oversized listings", async () => {
  const { RefreshBudget } = require("./source-refresh-budget.cjs");
  const budget = new RefreshBudget(4);
  let requested = 0;
  let closed = false;
  const github = { paginate: { iterator: () => ({
    [Symbol.asyncIterator]() { return this; },
    async next() {
      requested++;
      return { done: false, value: { data: [], headers: { link: '<https://api.github.com/next>; rel="next"' } } };
    },
    async return() { closed = true; return { done: true }; },
  }) } };
  await assert.rejects(budget.list(github, () => {}, { per_page: 100 }), /request budget/);
  assert.equal(requested, 4);
  assert.equal(budget.used, 4);
  assert.ok(closed);
});

test("association omissions use a fresh full listing without hiding same-head peers", async () => {
  for (const peer of [false, true]) {
    const values = [{ ...pr(marker), number: 1 }];
    if (peer) values.push({ ...pr(""), number: 2 });
    const { github } = headFixture(values);
    const paginate = github.paginate;
    github.paginate = paged(async (method, options) => method === github.rest.pulls.list ?
      paginate(method, options) : [{ ...pr(marker), number: 3, state: "closed" }]);
    const output = core();
    await gate.enforce({ github, context, core: output });
    if (peer) assert.ok(output.messages.join("\n").includes("Multiple open dev pull requests"));
    else assert.deepEqual(output.messages, []);
    github.paginate = paged(async () => []);
    await assert.rejects(gate.enforce({ github, context, core: core() }), /membership could not be verified/);
  }
});

test("associated ancestor commits, non-dev and closed PRs do not create head collisions", async () => {
  const values = [
    { ...pr(marker), number: 1 },
    { ...pr(""), number: 2, head: { sha: "descendant" } },
    { ...pr(""), number: 3, base: { ref: "main" } },
    { ...pr(""), number: 4, state: "closed" },
  ];
  const { github } = headFixture(values);
  github.paginate = paged(async () => values);
  const output = core();
  await gate.enforce({ github, context, core: output });
  assert.deepEqual(output.messages, []);
});

test("existing PR heads receive distinct current metadata results", async () => {
  const created = [];
  const github = {
    paginate: paged(async () => [1, 2].map((number) => ({
      ...pr(number === 1 ? marker : ""), number, head: { sha: `head-${number}` },
    }))),
    rest: {
      repos: { listPullRequestsAssociatedWithCommit() {} },
      pulls: {
        list() {},
        get: async ({ pull_number }) => ({
          data: { ...pr(pull_number === 1 ? marker : ""), head: { sha: `head-${pull_number}` } },
        }),
      },
      checks: {
        listForRef: async () => ({ data: { check_runs: [] } }),
        create: async (check) => {
          created.push(check);
          return { data: { id: created.length } };
        },
        update: async (check) => Object.assign(created[check.check_run_id - 1], check),
      },
    },
  };
  await gate.backfill({ github, context, core: core(), pause: async () => {} });
  assert.deepEqual(created.map((check) => check.name), ["source-attribution", "source-attribution"]);
  assert.deepEqual(created.map((check) => check.conclusion), ["success", "failure"]);
  assert.deepEqual(created.map((check) => check.head_sha), ["head-1", "head-2"]);
  assert.ok(!JSON.stringify(created).includes("attribution-exempt"));
});

test("unchanged decisions avoid repeated writes while body changes are reevaluated", async () => {
  let current = pr(marker);
  const created = [];
  const github = {
    paginate: paged(async () => [{ ...current, number: 1 }]),
    rest: {
      repos: { listPullRequestsAssociatedWithCommit() {} },
      pulls: { list() {}, get: async () => ({ data: current }) },
      checks: {
        listForRef: async () => ({ data: {
          check_runs: created.map((check, index) => ({ ...check, id: index + 1, app: { slug: "github-actions" } })),
        } }),
        create: async (check) => {
          created.push(check);
          return { data: { id: created.length } };
        },
        update: async (check) => Object.assign(created[check.check_run_id - 1], check),
      },
    },
  };
  const args = { github, context, core: core(), pause: async () => {} };
  await gate.backfill(args);
  await gate.backfill(args);
  assert.equal(created.length, 1);
  current = pr("");
  await gate.backfill(args);
  assert.equal(created.length, 2);
  assert.equal(created[1].conclusion, "failure");
});

test("changes during pacing and publication cannot leave a stale success", async () => {
  for (const scenario of [
    ...["pause", "create", "complete"].map((stage) => ({ stage, policy: false })),
    ...["pause", "create", "complete"].map((stage) => ({ stage, policy: true })),
  ]) {
    const changeAt = scenario.stage;
    let current = scenario.policy ? pr("", [{ name: "attribution-exempt" }]) : pr(marker);
    const created = [];
    let changed = false;
    const invalidate = () => { changed = true; current = pr(""); };
    const github = {
      paginate: paged(async () => [{ ...current, number: 1 }]),
      rest: {
        repos: { listPullRequestsAssociatedWithCommit() {} },
        pulls: { list() {}, get: async () => ({ data: current }) },
        checks: {
          listForRef: async () => ({ data: { check_runs: [] } }),
          create: async (check) => {
            if (check.status === "in_progress") assert.equal(check.conclusion, undefined);
            else assert.equal(check.conclusion, "failure");
            created.push(check);
            if (!changed && changeAt === "create" && check.status === "in_progress") invalidate();
            return { data: { id: created.length } };
          },
          get: async ({ check_run_id }) => ({ data: created[check_run_id - 1] }),
          update: async (check) => {
            assert.notEqual(created[check.check_run_id - 1].status, "completed",
                            "completed platform conclusions are immutable");
            Object.assign(created[check.check_run_id - 1], check);
            if (!changed && changeAt === "complete" && check.conclusion === "success") invalidate();
          },
        },
      },
    };
    await gate.backfill({
      github, context, core: core(),
      pause: async () => { if (!changed && changeAt === "pause") invalidate(); },
    });
    assert.ok(created.length);
    assert.equal(created.at(-1).conclusion, "failure");
    if (changeAt === "complete") {
      assert.equal(created[0].conclusion, "success");
      assert.equal(created[1].conclusion, "failure");
    } else assert.ok(created.every((check) => check.conclusion === "failure"));
    assert.ok(!JSON.stringify(created).includes("attribution-exempt"));
  }
});

test("unverifiable publication is failed closed without exposing metadata", async () => {
  let published = false;
  const created = [];
  const github = {
    paginate: paged(async () => {
      if (published) throw new Error("metadata unavailable");
      return [{ ...pr(marker), number: 1 }];
    }),
    rest: {
      repos: { listPullRequestsAssociatedWithCommit() {} },
      pulls: { list() {}, get: async () => {
        if (published) throw new Error("metadata unavailable");
        return { data: pr(marker) };
      } },
      checks: {
        listForRef: async () => ({ data: { check_runs: [] } }),
        create: async (check) => { created.push(check); published = true; return { data: { id: created.length } }; },
        get: async ({ check_run_id }) => ({ data: created[check_run_id - 1] }),
        update: async (check) => Object.assign(created[check.check_run_id - 1], check),
      },
    },
  };
  await assert.rejects(gate.backfill({ github, context, core: core(), pause: async () => {} }),
                       /Source metadata refresh failed for 1 PR/);
  assert.equal(created[0].conclusion, "failure");
  assert.ok(!JSON.stringify(created).includes("attribution-exempt"));
});

test("unverifiable or unstable PRs do not starve later current heads", async () => {
  for (const unavailable of [false, true]) {
    const created = [];
    let reads = 0;
    const output = core();
    const github = {
      paginate: paged(async (_method, options) => {
        const number = options.commit_sha === "head-1" ? 1 : 2;
        if (options.commit_sha && number === 1 && unavailable) throw new Error("private diagnostic");
        return [1, 2].map((value) => ({
          ...pr(marker), number: value, head: { sha: `head-${value}` },
          ...(options.commit_sha && value === 1 ? { body: marker + "\n" + (++reads) } : {}),
        }));
      }),
      rest: {
        repos: { listPullRequestsAssociatedWithCommit() {} },
        pulls: { list() {}, get: async ({ pull_number }) => {
          if (pull_number === 1 && unavailable) throw new Error("private diagnostic");
          return { data: {
            ...pr(marker), head: { sha: `head-${pull_number}` },
            ...(pull_number === 1 ? { body: marker + "\n" + (++reads) } : {}),
          } };
        } },
        checks: {
          listForRef: async () => ({ data: { check_runs: [] } }),
          create: async (check) => { created.push(check); return { data: { id: created.length } }; },
          get: async ({ check_run_id }) => ({ data: created[check_run_id - 1] }),
          update: async (check) => Object.assign(created[check.check_run_id - 1], check),
        },
      },
    };
    await assert.rejects(gate.backfill({ github, context, core: output, pause: async () => {} }),
                         /Source metadata refresh failed for 1 PR/);
    assert.equal(created.at(-1).head_sha, "head-2");
    assert.equal(created.at(-1).conclusion, "success");
    assert.ok(output.messages.some((message) => message.includes("continuing other PRs")));
    assert.ok(!output.messages.some((message) => message.includes("private diagnostic")));
  }
});

test("the existing compiler-owned native workflow origin needs no artifact rewrite", () => {
  const native = "<!-- gh-aw-workflow-id: ci-failure-fix-attempt -->";
  assert.equal(gate.validate("Authored body\n\n" + native), null);
  assert.ok(gate.validate("```html\n" + native + "\n```"));
  assert.ok(gate.validate(native + "\n" + native));
  assert.ok(gate.validate(native.replace("ci-failure-fix-attempt", "bad/private")));
  assert.ok(gate.validate("<!-- agent-worktrees:source machine=raw -->\n" + native));
  assert.ok(gate.validate(marker + "\n" + native));
  assert.ok(gate.validate(marker + "\n" + native.replace("ci-failure-fix-attempt", "bad/private")));
});
