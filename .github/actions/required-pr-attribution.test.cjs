const { test } = require("node:test");
const assert = require("node:assert/strict");
const gate = require("./required-pr-attribution.cjs");

const marker = "<!-- agent-worktrees:source codename=clear-compass -->";
const context = {
  repo: { owner: "owner", repo: "repo" }, payload: { pull_request: { number: 1 } },
  serverUrl: "https://github.com", runId: 123,
};
function pr(body, labels = []) {
  return { body, labels, base: { ref: "dev" }, state: "open", head: { sha: "head" } };
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

test("non-dev and silent approvals emit no attribution diagnostic", async () => {
  for (const value of [
    { ...pr(""), base: { ref: "main" } },
    pr("", [{ name: "attribution-exempt" }]),
    pr(marker),
  ]) {
    const output = core();
    await gate.enforce({
      context, core: output,
      github: { rest: { pulls: { get: async () => ({ data: value }) } } },
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
    "> - ```html\n  SOURCE\n>   ```",
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

test("indented or container-prefixed fence content cannot close another fence", () => {
  for (const body of [
    "```html\n    ```\n" + marker + "\n```",
    "~~~html\n    ~~~\n" + marker + "\n~~~",
    "```html\n> ```\n" + marker + "\n```",
    "```html\n- ```\n" + marker + "\n```",
    "- ```html\n      ```\n  " + marker + "\n  ```",
    "> ```html\n>     ```\n" + marker + "\n> ```",
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
  const github = { rest: { pulls: { get: async () => ({ data: current }) } } };
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

test("existing PR heads receive distinct current metadata results", async () => {
  const created = [];
  const github = {
    paginate: async () => [{ number: 1 }, { number: 2 }],
    rest: {
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
    paginate: async () => [{ number: 1 }],
    rest: {
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
      paginate: async () => [{ number: 1 }],
      rest: {
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
    paginate: async () => [{ number: 1 }],
    rest: {
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
      paginate: async () => [{ number: 1 }, { number: 2 }],
      rest: {
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
