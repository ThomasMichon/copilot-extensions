import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [seedPath, corePath, metricsPath] = process.argv.slice(2);
if (!seedPath || !corePath || !metricsPath) {
  console.error("usage: seed-probe.mjs <cutover-seed.mjs> <handoff-core.mjs> <metrics.json>");
  process.exit(2);
}

const seedMod = await import(pathToFileURL(seedPath));
const coreMod = await import(pathToFileURL(corePath));
const { buildCutoverSeed, leadFrom, MAX_CUTOVER_SEED_LENGTH } = seedMod;
const { encodeHandoffPayload, decodeHandoffPayload } = coreMod;

const taskId = "task-eval-123";
const handoffId = "handoff-eval";
const taskSeed = buildCutoverSeed(
  "task", taskId, leadFrom("Measure handoff takeover"),
);
const fileSeed = buildCutoverSeed(
  "file",
  handoffId,
  leadFrom("Measure handoff takeover"),
);
const payload = [
  "## Session Continuation",
  "Objective: preserve this high-fidelity brief.",
  "Canary: HANDOFF_FIDELITY_7f1a9c2e",
  "Next: acknowledge, take over, and continue.",
].join("\n");
const metadata = {
  kind: "context-handoff",
  version: 2,
  id: handoffId,
  title: "Measure handoff takeover",
};
const encoded = encodeHandoffPayload(payload, metadata);
const decoded = decodeHandoffPayload(encoded);
const sha = (value) => createHash("sha256").update(value).digest("hex");

const checks = [];
const check = (condition, label) => {
  checks.push({ label, pass: Boolean(condition) });
  console.log(`${condition ? "ok  " : "FAIL"}: ${label}`);
};

check(taskSeed.startsWith("Task: Measure handoff takeover | "), "stable task-first lead");
check(taskSeed.split(" | ").length === 3, "exact three-part seed");
check(
  taskSeed.includes(
    "Resume: /consume-handoff to take over",
  ),
  "explicit post-startup acknowledgement",
);
check(
  taskSeed.endsWith(`Recovery: context-handoff task:${taskId}`),
  "task recovery carries a short opaque locator",
);
check(
  fileSeed.endsWith(`Recovery: context-handoff file:${handoffId}`) &&
    !/[^\x00-\x7F]/.test(fileSeed),
  "file recovery locator is ASCII and path-independent",
);
check(
  !/[`"';&]/.test(taskSeed.split(" | ")[2]) &&
    !taskSeed.includes("node -e"),
  "seed contains no inline executable source or shell syntax",
);
check(!taskSeed.includes(payload), "full payload is not inlined");
check(!taskSeed.includes("\n"), "single-line launch transport");
check(!/[^\x00-\x7F]/.test(taskSeed), "ASCII launch transport");
check(taskSeed.length <= MAX_CUTOVER_SEED_LENGTH, "seed length budget");
check(decoded.text === payload, "payload round-trips byte-for-byte");
check(sha(decoded.text) === sha(payload), "payload SHA-256 fidelity");

const metrics = {
  schema: "copilot-extensions.context-handoff-efficiency",
  version: 1,
  initialSeed: {
    characters: taskSeed.length,
    estimatedTokens: Math.ceil(taskSeed.length / 4),
    maxCharacters: MAX_CUTOVER_SEED_LENGTH,
    parts: 3,
  },
  takeoverBudget: {
    initialSubmittedPrompts: 1,
    expectedAgentTurnsToAcknowledge: 1,
    expectedExtensionToolCallsToAcknowledge: 1,
  },
  payload: {
    characters: payload.length,
    sha256: sha(payload),
    roundTripSha256: sha(decoded.text),
    faithful: decoded.text === payload,
  },
  checks,
};
writeFileSync(metricsPath, JSON.stringify(metrics, null, 2) + "\n", "utf8");
console.log(`metrics: ${metricsPath}`);
process.exit(checks.every((item) => item.pass) ? 0 : 1);
