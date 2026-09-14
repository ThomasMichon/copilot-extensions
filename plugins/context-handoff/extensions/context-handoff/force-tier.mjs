// Force-tier tool-call classification (Phase 1, Goal 1 of the
// context-handoff-overhaul effort).
//
// Once the force threshold fires, the extension auto-drafts/stores/triggers
// a handoff and then must deny further *mutating* tool calls for the rest of
// the session -- but read-only inspection should still be allowed so the
// agent (or the operator) can observe what happened. This module holds the
// pure classification logic in isolation so it is unit-testable without the
// live @github/copilot-sdk connection extension.mjs needs.
//
// Uses the SDK's own per-request read-only signals (the permission request
// `kind`, or a request-specific `readOnly` flag) rather than a hand-
// maintained tool-name allowlist, so it stays correct as new tool kinds are
// added upstream.

// Whether a permission request represents a read-only operation.
export function isReadOnlyPermissionRequest(request) {
  switch (request?.kind) {
    case "read":
    case "url":
      return true;
    case "shell":
      return Array.isArray(request.commands) &&
        request.commands.length > 0 &&
        request.commands.every((c) => c.readOnly);
    case "mcp":
      return Boolean(request.readOnly);
    default:
      return false;
  }
}

export const FORCE_TIER_DENY_FEEDBACK =
  "[Context Handoff] The force threshold was reached this turn; a handoff " +
  "was already auto-drafted, stored, and triggered. Further mutating tool " +
  "calls are denied until the successor session picks up -- read-only " +
  "inspection is still allowed.";
