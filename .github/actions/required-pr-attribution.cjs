const fs = require("node:fs");
const crypto = require("node:crypto");
const path = require("node:path");
const { Parser, HtmlRenderer } = require("commonmark");
const { parseFragment } = require("parse5");
const { RefreshBudget, RefreshLimitError, invoke, paginate } = require("./source-refresh-budget.cjs");

const HANDLE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const WORKFLOW = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/;
const EXEMPTION = "attribution-exempt";
const CONTEXT = "source-attribution";
const DEFINITION = crypto.createHash("sha256").update(fs.readFileSync(__filename))
  .update(fs.readFileSync(path.join(__dirname, "source-refresh-budget.cjs")))
  .update(fs.readFileSync(path.join(__dirname, "package-lock.json"))).digest("hex");
const REPAIR = [
  "Every pull request targeting dev must carry one source attribution comment in its description.",
  "",
  "Managed source format:",
  "```text",
  "<!-- agent-worktrees:source codename=<assigned-public-handle> -->",
  "```",
  "Optional fields: root=<assigned-root-handle> enc=<opaque-AWI1-base64-token>.",
  "Repository automation format:",
  "```text",
  "<!-- agent-worktrees:source workflow=<workflow-file-slug> run=<run-id> -->",
  "```",
  "gh-aw automation may instead use its compiler-provided native origin:",
  "```text",
  "<!-- gh-aw-workflow-id: <workflow-file-slug> -->",
  "```",
  "The run field is optional. Raw machine, worktree, and session fields are not accepted.",
  "Place the actual comment on its own line, outside quoted examples or Markdown code fences.",
  "",
  "Repair from the managed source worktree:",
  "1. Save the existing authored PR description to a UTF-8 file.",
  "2. Use your session-catalog agent-worktrees command: attribution-prepare --body-file description.md.",
  "3. Save the prepared output and update this PR description with your repository-scoped gh pr edit --body-file invocation.",
  "If preparation is not installed yet, append the codename comment using the actual assigned public codename shown by worktree creation/listing. Do not invent a source or copy raw identifiers.",
  "If the source intentionally disables attribution or has no assigned public source, ask the operator rather than changing privacy policy.",
  "See CONTRIBUTING.md for the full manual-repair procedure.",
].join("\n");

function metadataText(body) {
  const lines = body.split(/\r?\n/);
  const comments = [];
  const quotes = [];
  const tree = new Parser().parse(body);
  const walker = tree.walker();
  let event;
  while ((event = walker.next())) {
    if (!event.entering) continue;
    const node = event.node;
    if (node.type === "code_block" || node.type === "block_quote") {
      for (let line = node.sourcepos[0][0] - 1; line < node.sourcepos[1][0]; line++) lines[line] = "";
      if (node.type === "block_quote") quotes.push(node);
      continue;
    }
    if (node.type === "html_block" && node.literal.trimStart().startsWith("<!--")) comments.push(node);
  }
  // Also exclude source-looking comments between multiline example backticks.
  const visible = withoutInlineCode(lines.join("\n")).split("\n");
  for (const node of comments) {
    node.literal = visible.slice(node.sourcepos[0][0] - 1, node.sourcepos[1][0]).join("\n") + "\n";
  }
  for (const node of quotes) node.unlink();
  return htmlSourceComments(new HtmlRenderer().render(tree)).join("\n");
}

function htmlSourceComments(html) {
  const pending = [parseFragment(html, { sourceCodeLocationInfo: true })];
  const comments = [];
  while (pending.length) {
    const node = pending.pop();
    if (["blockquote", "code", "pre"].includes(node.tagName)) continue;
    if (node.nodeName === "#comment" && /^\s*(?:agent-worktrees:source\b|gh-aw-workflow-id:)/.test(node.data)) {
      const { startOffset, endOffset } = node.sourceCodeLocation;
      const before = html.slice(0, startOffset).split("\n").at(-1);
      const after = html.slice(endOffset).split("\n")[0];
      const standalone = /^ {0,3}$/.test(before) && /^[ \t]*$/.test(after);
      comments.push((standalone ? "" : "not-standalone ") + html.slice(startOffset, endOffset));
    }
    pending.push(...(node.childNodes || []));
    if (node.content) pending.push(node.content);
  }
  return comments;
}

function withoutInlineCode(text) {
  let result = "";
  for (let cursor = 0; cursor < text.length;) {
    if (text.startsWith("<!--", cursor)) {
      const close = text.indexOf("-->", cursor + 4);
      const after = close === -1 ? text.length : close + 3;
      const comment = text.slice(cursor, after);
      result += /^<!--\s*(?:agent-worktrees:source\b|gh-aw-workflow-id:)/.test(comment) ?
        comment : comment.replace(/[^\r\n]/g, " ");
      cursor = after;
      continue;
    }
    if (text[cursor] !== "`") { result += text[cursor++]; continue; }
    let end = cursor;
    while (text[end] === "`") end++;
    const width = end - cursor;
    const boundary = text.slice(end).search(/\n[ \t]*\n/);
    const limit = boundary === -1 ? text.length : end + boundary;
    let close = text.indexOf("`", end);
    let after = -1;
    while (close !== -1 && close < limit) {
      let runEnd = close;
      while (text[runEnd] === "`") runEnd++;
      if (runEnd - close === width) { after = runEnd; break; }
      close = text.indexOf("`", runEnd);
    }
    if (after !== -1) {
      result += text.slice(cursor, after).replace(/[^\r\n]/g, " ");
      cursor = after;
    } else { result += text.slice(cursor, end); cursor = end; }
  }
  return result;
}

function validate(body) {
  if (body == null) body = "";
  if (typeof body !== "string") return "PR description must be text before source attribution can be validated.";
  if (body.length > 65536) return "PR description exceeds the supported 65,536-character limit; shorten it before retrying.";
  const text = metadataText(body);
  const candidates = text.match(/<!--\s*agent-worktrees:source\b/g) || [];
  const nativeCount = (text.match(/<!--\s*gh-aw-workflow-id:/g) || []).length;
  const markers = [...text.matchAll(/^ {0,3}<!--\s*agent-worktrees:source\s+([^\r\n]*?)\s*-->[ \t]*$/gm)];
  if (candidates.length && nativeCount) return "Conflicting source attribution origins.";
  if (!candidates.length) {
    const native = [...text.matchAll(/^ {0,3}<!--\s*gh-aw-workflow-id:\s*(\S+)\s*-->[ \t]*$/gm)];
    if (!nativeCount) return "Missing source attribution marker.";
    if (nativeCount !== 1 || native.length !== 1 ||
        !WORKFLOW.test(native[0][1]) || native[0][1].length > 64) return "Invalid native workflow-origin attribution.";
    return null;
  }
  if (candidates.length !== 1 || markers.length !== 1) return "Expected exactly one standalone, well-formed source attribution comment.";
  const fields = {};
  for (const token of markers[0][1].trim().split(/\s+/)) {
    const pair = token.match(/^([a-z]+)=(\S+)$/);
    if (!pair || Object.hasOwn(fields, pair[1])) return "Malformed or duplicate source attribution fields.";
    fields[pair[1]] = pair[2];
  }
  if (Object.hasOwn(fields, "codename")) {
    if (Object.keys(fields).some((key) => !["codename", "root", "enc"].includes(key))) return "Unrecognized or raw source attribution fields.";
    if (!HANDLE.test(fields.codename) || fields.codename.length > 64) return "Invalid public source codename.";
    if (fields.root && (!HANDLE.test(fields.root) || fields.root.length > 64)) return "Invalid public root codename.";
    if (fields.enc) {
      if (fields.enc.length > 16384 || !/^[A-Za-z0-9+/]+={0,2}$/.test(fields.enc)) return "Invalid encrypted attribution envelope.";
      const token = Buffer.from(fields.enc, "base64");
      if (token.toString("base64") !== fields.enc || token.length < 32 || token.subarray(0, 4).toString() !== "AWI1") return "Invalid encrypted attribution envelope.";
    }
  } else if (Object.hasOwn(fields, "workflow")) {
    if (Object.keys(fields).some((key) => !["workflow", "run"].includes(key))) return "Unrecognized workflow-origin attribution fields.";
    if (!WORKFLOW.test(fields.workflow) || fields.workflow.length > 64) return "Invalid public workflow origin.";
    if (fields.run && !/^[0-9]{1,20}$/.test(fields.run)) return "Invalid workflow run identifier.";
  } else return "Source attribution requires a public codename or workflow origin.";
  return null;
}

function decision(pr) {
  if (pr.base.ref !== "dev") return null;
  if ((pr.labels || []).some((label) => (typeof label === "string" ? label : label.name).toLowerCase() === EXEMPTION)) return null;
  return validate(pr.body);
}

async function currentPR(github, context, number, budget) {
  if (!Number.isSafeInteger(number) || number < 1) throw new Error("Invalid pull request metadata.");
  return { ...(await invoke(budget, () => github.rest.pulls.get({
    ...context.repo, pull_number: number,
  }))).data, number };
}

async function headPolicy(github, context, number, expectedHead, budget) {
  let pr = expectedHead ? null : await currentPR(github, context, number, budget);
  const head = expectedHead || pr.head.sha;
  let listed;
  let census = false;
  try {
    listed = await paginate(budget, github, github.rest.repos.listPullRequestsAssociatedWithCommit, {
      ...context.repo, commit_sha: head, per_page: 100,
    });
  } catch (error) {
    if (![500, 502, 503, 504].includes(error?.status)) throw error;
    console.warn("Commit association service failed; verifying the complete open-dev pull request census.");
    listed = await paginate(budget, github, github.rest.pulls.list, {
      ...context.repo, state: "open", base: "dev", per_page: 100,
    });
    census = true;
  }
  const associated = listed.find((item) => item.number === number &&
    item.state === "open" && item.base.ref === "dev" && item.head.sha === head);
  if (associated) pr = associated;
  else {
    // Commits already on the default branch can omit open PRs from the association API.
    if (!census) listed = await paginate(budget, github, github.rest.pulls.list, {
      ...context.repo, state: "open", base: "dev", per_page: 100,
    });
    pr = listed.find((item) => item.number === number) || await currentPR(github, context, number, budget);
  }
  const peersForHead = (items) => items.filter((item) =>
    item.state === "open" && item.base.ref === "dev" && item.head.sha === pr.head.sha,
  ).map((item) => item.number).sort((a, b) => a - b);
  const peers = peersForHead(listed);
  if (pr.state === "open" && pr.base.ref === "dev" && !peers.includes(number)) {
    throw new Error("Current dev pull request membership could not be verified.");
  }
  return {
    pr,
    error: peers.length > 1 ?
      "Multiple open dev pull requests share this head commit. Publish a distinct head commit for each PR, then retry the check." :
      decision(pr),
    snapshot: snapshot(pr, peers),
  };
}

async function enforce({ github, context, core }) {
  const number = context.payload.pull_request.number;
  const observed = await headPolicy(github, context, number);
  let error = observed.error;
  if (observed.pr.state !== "open" || observed.pr.base.ref !== "dev" ||
      observed.pr.head.sha !== context.payload.pull_request.head.sha ||
      (await headPolicy(github, context, number)).snapshot !== observed.snapshot) {
    error = "Pull request metadata changed or could not be verified; retry the current check.";
  }
  if (error) {
    core.setFailed(`${error}\n\n${REPAIR}`);
    await core.summary.addHeading("Required source attribution").addRaw(`${error}\n\n${REPAIR}`).write();
  }
}

function snapshot(pr, peers) {
  return "source-metadata:" + crypto.createHash("sha256").update(JSON.stringify({
    definition: DEFINITION, number: pr.number, body: pr.body,
    head: pr.head.sha, base: pr.base.ref, state: pr.state,
    labels: (pr.labels || []).map((label) => typeof label === "string" ? label : label.name).sort(),
    peers,
  })).digest("hex");
}

async function backfill({ github, context, core, head, requestLimit = 950,
  pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) }) {
  if (head !== undefined && (typeof head !== "string" || !/^[a-f0-9]{40}$/.test(head))) {
    throw new Error("Invalid departed head metadata.");
  }
  const budget = new RefreshBudget(requestLimit);
  const prs = await paginate(budget, github, github.rest.pulls.list, {
    ...context.repo, state: "open", base: "dev", per_page: 100,
  });
  const failed = [];
  for (const listed of prs.filter((pr) => head === undefined || pr.head.sha === head)) {
    try {
      await refreshPR(github, context, listed, pause, budget, head);
    } catch (error) {
      if (error instanceof RefreshLimitError) throw error;
      const number = Number.isSafeInteger(listed.number) && listed.number > 0 ? listed.number : "unknown";
      failed.push(number);
      core.warning(`Source metadata refresh failed for PR ${number}; continuing other PRs.`);
    }
  }
  if (failed.length) throw new Error(`Source metadata refresh failed for ${failed.length} PR(s) (${failed.join(", ")}); retry the check.`);
  core.info(head ? "Current dev pull requests on the departed head refreshed." :
    "Current dev pull request metadata checks refreshed.");
}

async function departed({ github, context, core, ...options }) {
  const { action, pull_request: pr, changes, before } = context.payload;
  let head;
  if (action === "synchronize" && pr.base.ref === "dev") head = before;
  else if (action === "closed" && pr.base.ref === "dev") head = pr.head.sha;
  else if (action === "edited" && changes?.base?.ref?.from === "dev" && pr.base.ref !== "dev") {
    head = pr.head.sha;
  } else return;
  if (head === undefined) throw new Error("Missing departed head metadata.");
  await backfill({ github, context, core, ...options, head });
}

async function refreshPR(github, context, listed, pause, budget, head) {
  let stable = false;
  for (let attempt = 0; attempt < 3; attempt++) {
    budget.protectPending();
    let pr = listed;
    let externalId = "source-metadata:" + DEFINITION + ":unverified";
    let created;
    try {
      const observed = await headPolicy(github, context, listed.number, listed.head.sha, budget);
      pr = observed.pr;
      externalId = observed.snapshot;
      if (pr.state !== "open" || pr.base.ref !== "dev" ||
          (head !== undefined && pr.head.sha !== head)) { stable = true; break; }
      const { data } = await invoke(budget, () => github.rest.checks.listForRef({
        ...context.repo, ref: pr.head.sha, check_name: CONTEXT, per_page: 100,
      }));
      const latest = data.check_runs.filter((check) => check.app?.slug === "github-actions")
        .sort((a, b) => b.id - a.id)[0];
      if (latest?.external_id === externalId && latest.status === "completed" &&
          latest.conclusion === (observed.error ? "failure" : "success")) {
        if ((await headPolicy(github, context, listed.number, pr.head.sha, budget)).snapshot === externalId) { stable = true; break; }
        continue;
      }
      await pause(1000);
      created = await invoke(budget, () => github.rest.checks.create({
        ...context.repo, name: CONTEXT, head_sha: pr.head.sha,
        external_id: externalId, status: "in_progress",
        details_url: `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`,
        output: {
          title: "Required source attribution",
          summary: "Validating current source metadata.",
        },
      }));
      if ((await headPolicy(github, context, listed.number, pr.head.sha, budget)).snapshot === externalId) {
        await invoke(budget, () => github.rest.checks.update({
          ...context.repo, check_run_id: created.data.id, status: "completed",
          conclusion: observed.error ? "failure" : "success",
          output: {
            title: "Required source attribution",
            summary: observed.error ? `${observed.error}\n\n${REPAIR}` : "Required source metadata check completed.",
          },
        }));
        stable = (await headPolicy(github, context, listed.number, pr.head.sha, budget)).snapshot === externalId;
      }
    } finally {
      try {
        if (!stable) {
          const invalidated = {
            ...context.repo, name: CONTEXT, head_sha: pr.head.sha,
            external_id: externalId + ":superseded",
            status: "completed", conclusion: "failure",
            details_url: `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`,
            output: {
              title: "Required source attribution",
              summary: "Source metadata changed or could not be verified; this result is superseded.",
            },
          };
          await invoke(budget, () => github.rest.checks.create(invalidated), true);
          if (created) {
            const original = await invoke(budget, () => github.rest.checks.get({
              ...context.repo, check_run_id: created.data.id,
            }), true);
            if (original.data.status !== "completed") {
              await invoke(budget, () => github.rest.checks.update({
                ...context.repo, check_run_id: created.data.id, status: "completed", conclusion: "failure",
                output: invalidated.output,
              }), true);
            }
          }
        }
      } finally { budget.release(); }
    }
    if (stable) break;
  }
  if (!stable) throw new Error("Source metadata changed too often to validate; retry the check.");
}

module.exports = { validate, decision, enforce, backfill, departed, REPAIR };
