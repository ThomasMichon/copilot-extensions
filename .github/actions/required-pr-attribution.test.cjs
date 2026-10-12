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
  return { messages, summary, setFailed: (value) => messages.push(value), info: (value) => messages.push(value) };
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
        create: async (check) => created.push(check),
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
        create: async (check) => created.push(check),
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

test("the existing compiler-owned native workflow origin needs no artifact rewrite", () => {
  const native = "<!-- gh-aw-workflow-id: ci-failure-fix-attempt -->";
  assert.equal(gate.validate("Authored body\n\n" + native), null);
  assert.ok(gate.validate("```html\n" + native + "\n```"));
  assert.ok(gate.validate(native + "\n" + native));
  assert.ok(gate.validate(native.replace("ci-failure-fix-attempt", "bad/private")));
  assert.ok(gate.validate("<!-- agent-worktrees:source machine=raw -->\n" + native));
  assert.equal(gate.validate(marker + "\n" + native), null);
});
