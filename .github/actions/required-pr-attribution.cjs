const fs = require("node:fs");
const crypto = require("node:crypto");

const HANDLE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const WORKFLOW = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/;
const EXEMPTION = "attribution-exempt";
const CONTEXT = "source-attribution";
const DEFINITION = crypto.createHash("sha256").update(fs.readFileSync(__filename)).digest("hex");
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

function mapVisibleLines(body, visible, hidden = () => null) {
  let fence = null;
  return body.split(/\r?\n/).map((line) => {
    let rest = line;
    let quotes = 0;
    let prefix;
    while ((prefix = rest.match(/^ {0,3}>[ \t]?/))) {
      quotes++;
      rest = rest.slice(prefix[0].length);
    }
    if (fence) {
      const close = rest.match(/^( *)(`{3,}|~{3,})[ \t]*$/);
      if (quotes === fence.quotes && close &&
          close[1].length >= fence.indent && close[1].length <= fence.indent + 3 &&
          close[2][0] === fence.delimiter[0] && close[2].length >= fence.delimiter.length) fence = null;
      return hidden(line);
    }
    let indent = 0;
    // List prefixes apply to openers, not to arbitrary content inside a fence.
    while ((prefix = rest.match(/^ *(?:[-+*]|\d{1,9}[.)])[ \t]+/))) {
      indent += prefix[0].length;
      rest = rest.slice(prefix[0].length);
    }
    const open = rest.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (open && (open[1][0] !== "`" || !open[2].includes("`"))) {
      fence = { delimiter: open[1], quotes, indent };
      return hidden(line);
    }
    return quotes ? hidden(line) : visible(line);
  }).filter((line) => line !== null).join("\n");
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
  if (typeof body !== "string" || body.length > 65536) return "Source attribution could not be validated.";
  const text = withoutInlineCode(mapVisibleLines(body, (line) => line, () => ""));
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

async function currentPR(github, context, number) {
  if (!Number.isSafeInteger(number) || number < 1) throw new Error("Invalid pull request metadata.");
  return (await github.rest.pulls.get({ ...context.repo, pull_number: number })).data;
}

async function enforce({ github, context, core }) {
  const pr = await currentPR(github, context, context.payload.pull_request.number);
  const error = decision(pr);
  if (error) {
    core.setFailed(`${error}\n\n${REPAIR}`);
    await core.summary.addHeading("Required source attribution").addRaw(`${error}\n\n${REPAIR}`).write();
  }
}

function snapshot(pr) {
  return "source-metadata:" + crypto.createHash("sha256").update(JSON.stringify({
    definition: DEFINITION, number: pr.number, body: pr.body,
    head: pr.head.sha, base: pr.base.ref, state: pr.state,
    labels: (pr.labels || []).map((label) => typeof label === "string" ? label : label.name).sort(),
  })).digest("hex");
}

async function backfill({ github, context, core, pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) }) {
  const prs = await github.paginate(github.rest.pulls.list, {
    ...context.repo, state: "open", base: "dev", per_page: 100,
  });
  for (const listed of prs) {
    let stable = false;
    for (let attempt = 0; attempt < 3; attempt++) {
      let pr = await currentPR(github, context, listed.number);
      if (pr.state !== "open" || pr.base.ref !== "dev") { stable = true; break; }
      const { data } = await github.rest.checks.listForRef({
        ...context.repo, ref: pr.head.sha, check_name: CONTEXT, per_page: 100,
      });
      const latest = data.check_runs.filter((check) => check.app?.slug === "github-actions")
        .sort((a, b) => b.id - a.id)[0];
      const observed = snapshot(pr);
      if (latest?.external_id === observed && latest.status === "completed" &&
          latest.conclusion === (decision(pr) ? "failure" : "success")) {
        if (snapshot(await currentPR(github, context, listed.number)) === observed) { stable = true; break; }
        continue;
      }
      await pause(1000);
      pr = await currentPR(github, context, listed.number);
      if (pr.state !== "open" || pr.base.ref !== "dev") { stable = true; break; }
      const externalId = snapshot(pr);
      const error = decision(pr);
      const created = await github.rest.checks.create({
        ...context.repo, name: CONTEXT, head_sha: pr.head.sha,
        external_id: externalId,
        status: "in_progress",
        details_url: `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`,
        output: {
          title: "Required source attribution",
          summary: "Validating current source metadata.",
        },
      });
      try {
        if (snapshot(await currentPR(github, context, listed.number)) === externalId) {
          await github.rest.checks.update({
            ...context.repo, check_run_id: created.data.id, status: "completed",
            conclusion: error ? "failure" : "success",
            output: {
              title: "Required source attribution",
              summary: error ? `${error}\n\n${REPAIR}` : "Required source metadata check completed.",
            },
          });
          stable = snapshot(await currentPR(github, context, listed.number)) === externalId;
        }
      } finally {
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
          await github.rest.checks.create(invalidated);
          const original = await github.rest.checks.get({
            ...context.repo, check_run_id: created.data.id,
          });
          if (original.data.status !== "completed") {
            await github.rest.checks.update({
              ...context.repo, check_run_id: created.data.id, status: "completed", conclusion: "failure",
              output: invalidated.output,
            });
          }
        }
      }
      if (stable) break;
    }
    if (!stable) throw new Error("Source metadata changed too often to validate; retry the check.");
  }
  core.info("Current dev pull request metadata checks refreshed.");
}

module.exports = { validate, decision, enforce, backfill, REPAIR };
