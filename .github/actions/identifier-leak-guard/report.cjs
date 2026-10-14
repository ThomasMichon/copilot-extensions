const fs = require("node:fs");

module.exports = async function report({github, context, core}) {
  const {owner, repo} = context.repo;
  const prNumber = Number(process.env.PR_NUMBER);
  const headSha = process.env.PR_HEAD_SHA;
  if (process.env.STALE === "true") {
    core.notice(`Skipping stale reporting for PR #${prNumber}.`);
    return;
  }
  const {data: current} = await github.rest.pulls.get({
    owner, repo, pull_number: prNumber,
  });
  if (current.head.sha !== headSha) {
    core.notice(`Skipping stale reporting for PR #${prNumber}.`);
    return;
  }
  const readJson = (path) => {
    const value = JSON.parse(fs.readFileSync(path, "utf8"));
    if (!Array.isArray(value)) throw new Error("Identifier report must be an array.");
    return value;
  };
  const findings = readJson(process.env.FINDINGS_PATH);
  const details = readJson(process.env.DETAILS_PATH);
  const scannedPaths = fs.readFileSync(process.env.PATHS_PATH, "utf8")
    .split("\n").filter(Boolean).map((line) => JSON.parse(line));
  if (scannedPaths.some((path) => typeof path !== "string" || !path || path.includes("\0"))) {
    throw new Error("Identifier paths must be nonempty Git filename strings.");
  }
  const configured = process.env.DENYLIST_CONFIGURED === "true";
  const scanOutcome = process.env.SCAN_OUTCOME || "skipped";
  const failingSteps = [
    ["python setup", process.env.SETUP_PYTHON_OUTCOME],
    ["PR metadata resolution", process.env.PR_OUTCOME],
    ["report-file initialization", process.env.INIT_FILES_OUTCOME],
    ["PR data fetch", process.env.FETCH_PR_DATA_OUTCOME],
    ["denylist load", process.env.DENYLIST_OUTCOME],
  ].filter(([, outcome]) => outcome !== "success").map(([label]) => label);
  let conclusion = "success";
  let title = "Identifier leak guard passed";
  let summary = `Scanned ${scannedPaths.length} changed file(s) on PR head ${headSha}.`;
  let text = "";
  if (failingSteps.length > 0) {
    conclusion = "failure";
    title = "Identifier leak guard errored";
    summary = "The trusted scan failed before it could produce reliable findings. Inspect this workflow run's logs.";
    text = `Failing step(s): ${failingSteps.join(", ")}.`;
  } else if (!configured && scanOutcome === "success") {
    // The existing feature gate stays neutral only when its prerequisites worked.
    conclusion = "neutral";
    title = "Identifier leak guard not yet configured (in development)";
    summary = "FORBIDDEN_IDS_FACILITY / FORBIDDEN_IDS_WORK repository secrets are not configured yet (Phase 4 of ci-identifier-leak-guard is pending), so this check is reported neutral instead of blocking PRs.";
  } else if (details.length > 0) {
    conclusion = "failure";
    title = `${details.length} forbidden identifier(s) found`;
    summary = "Trusted scan found forbidden identifiers in the PR head's changed file contents.";
    // Leave space below the Checks API's 65535-character cap for the remainder count.
    const rendered = [];
    let used = 0;
    for (const item of details) {
      const reason = item.reason ? ` \u2014 ${item.reason}` : "";
      const line = `- ${item.file}:${item.line}:${item.col} \u2014 \`${item.identifier}\`${reason}`;
      if (used + line.length + 1 > 60000) break;
      rendered.push(line);
      used += line.length + 1;
    }
    if (rendered.length < details.length) {
      rendered.push(`- ...and ${details.length - rendered.length} more.`);
    }
    text = rendered.join("\n");
  } else if (scanOutcome !== "success") {
    conclusion = "failure";
    title = "Identifier leak guard errored";
    summary = "The trusted scan step failed before it could produce findings. Inspect this workflow run's logs.";
    text = "Failing step(s): trusted scanner.";
  } else {
    text = findings.length
      ? `Redacted findings artifact contains ${findings.length} hashed entr${findings.length === 1 ? "y" : "ies"}.`
      : "No forbidden identifiers were found.";
  }
  await github.rest.checks.create({
    owner, repo, name: "identifier leak guard", head_sha: headSha,
    status: "completed", conclusion, output: {title, summary, text},
  });
  if (conclusion !== "failure" || details.length === 0 || failingSteps.length > 0) return;
  const marker = `<!-- identifier-leak-guard:${headSha} -->`;
  const lines = details.map((item) => {
    const reason = item.reason ? ` \u2014 ${item.reason}` : "";
    return `- \`${item.file}:${item.line}:${item.col}\` \u2014 \`${item.identifier}\`${reason}`;
  });
  const body = [
    marker, "Identifier leak guard found forbidden identifiers in this PR head:", "",
    ...lines, "", "Please replace each matched value with a generic/public-safe equivalent.",
  ].join("\n");
  // workflow_run tokens can reject comments; the already-published check is authoritative.
  try {
    const comments = await github.paginate(github.rest.issues.listComments, {
      owner, repo, issue_number: prNumber, per_page: 100,
    });
    const existing = comments.find((comment) =>
      comment.user?.login === "github-actions[bot]" &&
      typeof comment.body === "string" && comment.body.includes(marker));
    if (existing) {
      await github.rest.issues.updateComment({owner, repo, comment_id: existing.id, body});
    } else {
      await github.rest.issues.createComment({owner, repo, issue_number: prNumber, body});
    }
  } catch (error) {
    core.warning(
      `identifier-leak-guard: could not post PR feedback comment (${error.message}). ` +
      "The Check Run's own output already carries the full finding detail.",
    );
  }
};
