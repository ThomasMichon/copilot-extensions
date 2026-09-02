// Pure, SDK-free successor seed builders.

export const MAX_CUTOVER_SEED_LENGTH = 1024;
const CANONICAL_REPOSITORY =
  "https://github.com/ThomasMichon/copilot-extensions";
const PAYLOAD_CLI_RESOLVER = [
  "const f=require('fs'),p=require('path'),c=require('child_process'),",
  "r=p.join(require('os').homedir(),'.copilot','installed-plugins',",
  "'copilot-extensions','context-handoff'),",
  "m=JSON.parse(f.readFileSync(p.join(r,'plugin.json'),'utf8'));",
  `if(m.name!=='context-handoff'||m.repository!=='${CANONICAL_REPOSITORY}')process.exit(2);`,
  "const x=p.join(r,'extensions','context-handoff','handoff-cli.mjs'),",
  "q=c.spawnSync(process.execPath,[x,...process.argv.slice(1)],{stdio:'inherit'});",
  "process.exit(q.status??1)",
].join("");

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
    throw new Error("handoff recovery command must be non-empty single-line ASCII");
  }
  return text;
}

function commandArg(value) {
  const text = exactAsciiSingleLine(value);
  if (/^[A-Za-z0-9._:@%+\/\\=-]+$/.test(text)) return text;
  return `"${text.replace(/"/g, '\\"')}"`;
}

export function leadFrom(title) {
  const normalized = asciiSingleLine(title)
    .replace(/^(?:continue|task)\s*:\s*/i, "")
    .slice(0, 180)
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

export function recoveryCommandFor(
  kind,
  id,
  _options = {},
) {
  const prefix = `node -e ${commandArg(PAYLOAD_CLI_RESOLVER)} consume`;
  if (kind === "task") {
    return `${prefix} --task-id ${commandArg(id)} --defer-complete`;
  }
  return `${prefix} --handoff-id ${commandArg(id)}`;
}

// Stable three-part contract:
//   1. task/title lead
//   2. one recommendation to use the extension command
//   3. one exact raw recovery command
export function buildCutoverSeed(
  kind,
  id,
  lead,
  { recoveryCommand = null } = {},
) {
  const command = exactAsciiSingleLine(
    recoveryCommand || recoveryCommandFor(kind, id),
  );
  const recommendation =
    "Recommendation: after startup invoke `/consume-handoff` " +
    "(the `consume_handoff` tool) to acknowledge and take over";
  let taskLead = asciiSingleLine(lead) || leadFrom("");
  let seed = `${taskLead} | ${recommendation} | Recovery: ${command}`;
  if (seed.length > MAX_CUTOVER_SEED_LENGTH) {
    taskLead = leadFrom("");
    seed = `${taskLead} | ${recommendation} | Recovery: ${command}`;
  }
  if (seed.length > MAX_CUTOVER_SEED_LENGTH) {
    throw new Error(
      `handoff recovery command exceeds ${MAX_CUTOVER_SEED_LENGTH} characters`,
    );
  }
  return seed;
}
