import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MAX_AUTHORIZATION_EVIDENCE_BYTES,
  formatAuthorizationEvidence,
  formatHandoffMarkdown,
  validateAuthorizationEvidence,
} from "../extensions/context-handoff/handoff-markdown.mjs";
import {
  decodeHandoffPayload,
  encodeHandoffPayload,
} from "../extensions/context-handoff/handoff-core.mjs";

const evidence = [
  "Source: original operator decision, canonical effort Scope Approvals",
  "Allowed: reviewed tooling update on the operator-owned target selector",
  "Excluded: personal archives, mail, third-party resources, new privileges",
  "Repetition: bounded repeating scope; no explicit revision pin",
  "Current changes: rollout paused by the later operator decision",
].join("\n");

test("known evidence and limits survive formatting and the actual payload codec", () => {
  const body = formatHandoffMarkdown({
    sessionId: "synthetic-source", cwd: "/source", branch: "feature",
    turnCount: 2, contextUtilization: "79%", generatedAt: "synthetic",
    firstUserPrompt: "Deliver the tooling fix", filesModified: {},
    authorizationEvidence: evidence,
  }, "same approved objective");
  assert.match(body, /## Authorization Evidence & Limits/);
  assert.ok(body.includes(evidence));
  assert.match(body, /Read current canonical authority before effects/);
  assert.match(body, /not a grant/);
  const metadata = { kind: "context-handoff", id: "synthetic-baton", title: "Continue" };
  assert.deepEqual(decodeHandoffPayload(encodeHandoffPayload(body, metadata)),
    { metadata, text: body });
});

test("an emergency draft does not convert missing evidence into permission", () => {
  const body = formatHandoffMarkdown({
    sessionId: "synthetic-source", cwd: "/source", branch: null,
    filesModified: {}, firstUserPrompt: "A request excerpt is not an approval inventory",
  });
  assert.match(body, /## Authorization Evidence & Limits/);
  assert.match(body, /Not captured -- recover the original operator\/charter approval/);
  assert.match(body, /Do not infer permission or ask for blanket reapproval/);
  assert.match(body, /reconcile unknown prior effects/);
  assert.match(body, /current limits\/holds/);
});

test("carrier validation rejects invalid or oversized input rather than dropping limits", () => {
  assert.equal(validateAuthorizationEvidence(" \n"), null);
  assert.equal(validateAuthorizationEvidence(evidence), evidence);
  assert.equal(validateAuthorizationEvidence("x".repeat(MAX_AUTHORIZATION_EVIDENCE_BYTES)).length,
    MAX_AUTHORIZATION_EVIDENCE_BYTES);
  assert.throws(() => validateAuthorizationEvidence({ approved: true }), TypeError);
  assert.throws(() => formatAuthorizationEvidence(false), TypeError);
  assert.throws(() => validateAuthorizationEvidence("x".repeat(MAX_AUTHORIZATION_EVIDENCE_BYTES + 1)),
    /8192-byte limit/);
  assert.throws(() => validateAuthorizationEvidence("\u{1F600}".repeat(2049)),
    /8192-byte limit/);
});
