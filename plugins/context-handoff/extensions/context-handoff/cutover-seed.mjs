// Pure, SDK-free successor seed builders.

export const MAX_CUTOVER_SEED_LENGTH = 200;

function asciiSingleLine(value) {
  return String(value || "")
    .replace(/[\r\n\t]+/g, " ")
    .replace(/[^\x20-\x7E]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function exactAsciiSingleLine(value) {
  const text = String(value || "").trim();
  if (!text || /[^\x20-\x7E]/.test(text)) {
    throw new Error("handoff recovery locator must be non-empty single-line ASCII");
  }
  return text;
}

function seedField(value) {
  return asciiSingleLine(value)
    .replace(/\|/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function leadFrom(title) {
  const normalized = seedField(title)
    .replace(/^(?:continue|task)\s*:\s*/i, "")
    .slice(0, 72)
    .trim();
  return `Task: ${normalized || "Continue the current work"}`;
}

export const CONTINUATION_DIRECTIVE =
  "Treat the handoff as active responsibility within the authority it assigns, " +
  "not as proof that the predecessor's latest phase finished the work. Objective " +
  "owners continue the original objective; bounded delegates continue only their " +
  "inherited scope and return or re-handoff at that boundary. After loading the " +
  "brief, keep driving every actionable next phase the original request permits, " +
  "without waiting for another user nudge. Consuming the handoff is setup, not " +
  "completion: begin substantive work immediately after pickup. If the inherited " +
  "plan is incomplete, finish the planning needed to act and then execute it, " +
  "subject to any required safety, review, approval, or confirmation gate. If " +
  "context pressure returns first, hand off again with the same parent objective. " +
  "When the handoff cites an active effort, load that effort before reconstructing " +
  "intent from session history. Treat the effort -- not the handoff task, latest " +
  "phase, or pull request -- as the source of truth and completion gate. Objective " +
  "owners focus on driving it to `Done`: select and execute the next authorized " +
  "Plan or Validation Plan item, and do not finalize the worktree while any item " +
  "remains unresolved unless responsibility is explicitly transferred to a named " +
  "tracked objective.";

export function recoveryLocatorFor(kind, id) {
  if (kind !== "task" && kind !== "file") {
    throw new Error(`unsupported handoff recovery kind: ${kind}`);
  }
  const token = exactAsciiSingleLine(id);
  if (!/^[A-Za-z0-9._-]+$/.test(token)) {
    throw new Error("handoff recovery id contains unsafe characters");
  }
  return `${kind}:${token}`;
}

export function parseRecoveryLocator(value) {
  const locator = exactAsciiSingleLine(value);
  const separator = locator.indexOf(":");
  if (separator <= 0) {
    throw new Error("invalid handoff recovery locator");
  }
  const kind = locator.slice(0, separator);
  const id = locator.slice(separator + 1);
  const canonical = recoveryLocatorFor(kind, id);
  if (canonical !== locator) {
    throw new Error("invalid handoff recovery locator");
  }
  return { kind, id };
}

// Stable three-part contract:
//   1. task/title lead
//   2. one recommendation to use the extension command
//   3. one short opaque recovery locator
export function buildCutoverSeed(
  kind,
  id,
  lead,
) {
  const locator = recoveryLocatorFor(kind, id);
  const recommendation = "Resume: /consume-handoff to take over";
  let taskLead = seedField(lead) || leadFrom("");
  let seed =
    `${taskLead} | ${recommendation} | Recovery: context-handoff ${locator}`;
  if (seed.length > MAX_CUTOVER_SEED_LENGTH) {
    taskLead = leadFrom("");
    seed =
      `${taskLead} | ${recommendation} | Recovery: context-handoff ${locator}`;
  }
  if (seed.length > MAX_CUTOVER_SEED_LENGTH) {
    throw new Error(
      `handoff recovery locator exceeds ${MAX_CUTOVER_SEED_LENGTH} characters`,
    );
  }
  return seed;
}
