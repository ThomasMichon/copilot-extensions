import { AUTHORIZATION_EVIDENCE_DIRECTIVE } from "./cutover-seed.mjs";

export const MAX_AUTHORIZATION_EVIDENCE_BYTES = 8192;

export function validateAuthorizationEvidence(value) {
  if (typeof value !== "string") {
    throw new TypeError("authorization_evidence must be a string");
  }
  if (Buffer.byteLength(value, "utf8") > MAX_AUTHORIZATION_EVIDENCE_BYTES) {
    throw new RangeError("authorization_evidence exceeds the 8192-byte limit");
  }
  return value.trim() ? value : null;
}

export function formatAuthorizationEvidence(evidence) {
  if (evidence !== null && evidence !== undefined) {
    evidence = validateAuthorizationEvidence(evidence);
  }
  return [
    "## Authorization Evidence & Limits",
    evidence ||
      "Not captured -- recover the original operator/charter approval and " +
      "current limits/holds from the existing checkpoint, canonical effort " +
      "or bounded source history before the affected effects. Do not infer " +
      "permission or ask for blanket reapproval; continue other authorized work.",
    AUTHORIZATION_EVIDENCE_DIRECTIVE,
  ].join("\n\n");
}

// SDK-free so both known-evidence and emergency drafts can be exercised directly.
export function formatHandoffMarkdown(handoffData, scope) {
  const lines = [
    "# Session Handoff",
    "",
    `**Session:** ${handoffData.sessionId}`,
    `**CWD:** ${handoffData.cwd}`,
    `**Branch:** ${
      handoffData.branch === null
        ? "(unavailable)"
        : handoffData.branch || "(detached)"
    }`,
    `**Turn count:** ${handoffData.turnCount}`,
    `**Context utilization:** ${handoffData.contextUtilization}`,
    `**Generated:** ${handoffData.generatedAt}`,
    "",
  ];
  if (scope) lines.push("## Continuation Scope", `> ${scope}`, "");
  if (handoffData.firstUserPrompt) {
    lines.push("## Original Request", `> ${handoffData.firstUserPrompt.slice(0, 500)}`, "");
  }
  const files = Object.entries(handoffData.filesModified || {});
  if (files.length > 0) {
    lines.push("## Files Modified");
    for (const [path, info] of files) {
      lines.push(`- \`${path}\` (${info.tool}, turn ${info.turnIndex})`);
    }
    lines.push("");
  }
  if (handoffData.gitStatus) {
    lines.push("## Git Status", "```", handoffData.gitStatus, "```", "");
  }
  if (handoffData.agentSummary) lines.push("## Summary", handoffData.agentSummary, "");
  if (handoffData.agentNextSteps) lines.push("## Next Steps", handoffData.agentNextSteps, "");
  lines.push(formatAuthorizationEvidence(handoffData.authorizationEvidence), "");
  lines.push(
    "## Outstanding Background Flows & External State",
    "Not captured -- this handoff was auto-drafted by the force tier without " +
      "agent composition. The successor MUST check the " +
      "worktree/dispatch/PR state directly (or ask only for genuinely missing " +
      "information) for any active watches, polls, " +
      "scheduled prompts, open PRs, held claims/leases, or peer-agent " +
      "coordination this session may have owned before assuming there are none.",
    "",
  );
  return lines.join("\n");
}
