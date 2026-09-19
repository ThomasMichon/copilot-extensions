import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  CONTINUATION_DIRECTIVE,
  HANDOFF_MECHANISM_AWARENESS,
} from "../extensions/context-handoff/cutover-seed.mjs";

const plugin = join(dirname(fileURLToPath(import.meta.url)), "..");

test("successor directive drives the parent objective across context windows", () => {
  assert.match(CONTINUATION_DIRECTIVE, /active responsibility within the authority it assigns/);
  assert.match(CONTINUATION_DIRECTIVE, /bounded delegates continue only their inherited scope/);
  assert.match(CONTINUATION_DIRECTIVE, /without waiting for another user nudge/);
  assert.match(CONTINUATION_DIRECTIVE, /Consuming the handoff is setup, not completion/);
  assert.match(CONTINUATION_DIRECTIVE, /begin substantive work immediately after pickup/);
  assert.match(CONTINUATION_DIRECTIVE, /finish the planning needed to act and then execute it/);
  assert.match(CONTINUATION_DIRECTIVE, /load that effort before reconstructing intent/);
});

test("handoff mechanism awareness carries perpetuation + fresh-session awareness", () => {
  assert.match(HANDOFF_MECHANISM_AWARENESS, /available from turn one/);
  assert.match(HANDOFF_MECHANISM_AWARENESS, /whether or not this session began from a handoff/);
  assert.match(HANDOFF_MECHANISM_AWARENESS, /never a reason to truncate diligence/);
  assert.match(HANDOFF_MECHANISM_AWARENESS, /forces one before auto-compaction/);
  assert.match(HANDOFF_MECHANISM_AWARENESS, /context-handoff skill/);
  assert.match(HANDOFF_MECHANISM_AWARENESS, /chain many handoffs in\s*\n?\s*succession/);
});

test("handoff-core threads the mechanism-awareness constant into every delivered brief", () => {
  const core = readFileSync(
    join(plugin, "extensions", "context-handoff", "handoff-core.mjs"),
    "utf8",
  );
  assert.match(core, /HANDOFF_MECHANISM_AWARENESS,\s*\n\s*leadFrom/);
  assert.match(core, /CONTINUATION_DIRECTIVE,\s*\n\s*""[,\s]*\n\s*HANDOFF_MECHANISM_AWARENESS/);
});

test("extension no longer re-delivers the awareness nudge from in-memory module state", () => {
  const extension = readFileSync(
    join(plugin, "extensions", "context-handoff", "extension.mjs"),
    "utf8",
  );
  // The fresh-session awareness message moved to the static, naturally
  // idempotent session-guidance file (scripts/emit-guidance.*) so a
  // mid-session extension re-fork (reconnect/reload) can never replay it --
  // see efforts/active/context-handoff-overhaul's journal for the incident
  // (a reload replayed this nudge and raced the skill registry, producing a
  // transient "Skill not found: context-handoff").
  assert.doesNotMatch(extension, /awarenessNudgeSent/);
  assert.doesNotMatch(extension, /pendingAwareness/);
  assert.doesNotMatch(extension, /HANDOFF_MECHANISM_AWARENESS/);
});

test("skill and README distinguish context-pressure auto-trigger from follow-up ask-first", () => {
  const skill = readFileSync(
    join(plugin, "skills", "context-handoff", "SKILL.md"),
    "utf8",
  ).replace(/\s+/g, " ");
  const readme = readFileSync(
    join(plugin, "README.md"),
    "utf8",
  ).replace(/\s+/g, " ");

  for (const source of [skill, readme]) {
    assert.match(source, /process-manager agnostic/i);
    assert.match(source, /save_handoff_prompt/);
    assert.match(source, /trigger_handoff/);
    assert.match(source, /context-pressure-driven handoff/i);
    assert.match(source, /trigger directly|call `trigger_handoff` directly/i);
    assert.match(source, /turn-end|follow-up/i);
    assert.match(source, /ask the user/i);
    assert.match(source, /one session own one slice|one session own one natural slice/i);
  }
  assert.match(skill, /A handoff with no actionable successor work is usually malformed/);
  assert.match(skill, /Only this turn-end follow-up path is skippable via \*\*autopilot\*\*/);
});

test("extension guidance no longer exposes live-cutover tools", () => {
  const extension = readFileSync(
    join(plugin, "extensions", "context-handoff", "extension.mjs"),
    "utf8",
  );
  assert.match(extension, /name: "save_handoff_prompt"/);
  assert.match(extension, /name: "trigger_handoff"/);
  assert.match(extension, /name: "consume_handoff"/);
  assert.doesNotMatch(extension, /name: "continue_handoff"/);
  assert.doesNotMatch(extension, /name: "retry_handoff_cutover"/);
  assert.match(extension, /It NEVER checks panes or PIDs/);
  assert.match(extension, /Final short handoff prompt\/seed/);
});

test("extension and CLI share the SDK-free handoff implementation", () => {
  const extension = readFileSync(
    join(plugin, "extensions", "context-handoff", "extension.mjs"),
    "utf8",
  );
  const cli = readFileSync(
    join(plugin, "extensions", "context-handoff", "handoff-cli.mjs"),
    "utf8",
  );
  assert.match(extension, /from "\.\/handoff-core\.mjs"/);
  assert.match(cli, /from "\.\/handoff-core\.mjs"/);
  assert.doesNotMatch(extension, /function (?:makeHandoffMetadata|dispatchHandoff|consumeDispatchHandoffTask)/);
});

test("payload-local fallback documents trigger, consume, and retry-cutover", () => {
  const skill = readFileSync(
    join(plugin, "skills", "context-handoff", "SKILL.md"),
    "utf8",
  );
  const setup = readFileSync(
    join(plugin, "skills", "context-handoff-setup", "SKILL.md"),
    "utf8",
  );
  for (const command of [
    "facts --json",
    "save --title",
    "trigger --title",
    "trigger --handoff-token",
    "consume --locator \"task:<task-id>\"",
    "consume --locator \"file:<handoff-id>\"",
    "retry-cutover --session-id",
  ]) {
    assert.ok(skill.includes(command), `fallback must document ${command}`);
  }
  assert.match(setup, /No installed runtime, venv, binstub/);
  assert.match(setup, /invoke it with `node`/);
  assert.doesNotMatch(skill, /\bcontinue --seed\b/);
  assert.doesNotMatch(skill, /\bretry --session-id\b/);
});

test("outstanding background flows/external state is a standing schema class, not just prose", () => {
  const skill = readFileSync(
    join(plugin, "skills", "context-handoff", "SKILL.md"),
    "utf8",
  );
  const template = readFileSync(
    join(plugin, "skills", "context-handoff", "references", "handoff-template.md"),
    "utf8",
  );
  const extension = readFileSync(
    join(plugin, "extensions", "context-handoff", "extension.mjs"),
    "utf8",
  );
  for (const source of [skill, template]) {
    assert.match(source, /### Outstanding Background Flows & External State/);
  }
  // Both shapes in handoff-template.md must carry the section (effort-backed
  // and standalone), not just one.
  assert.equal(
    (template.match(/### Outstanding Background Flows & External State/g) || []).length,
    2,
  );
  assert.match(skill, /never silently drop/i);
  assert.match(template, /Never silently drop/);
  // The force-tier auto-draft path (no agent composition) must still surface
  // an explicit open item rather than omit the section.
  assert.match(extension, /Outstanding Background Flows & External State/);
  assert.match(extension, /Not captured -- this handoff was auto-drafted by the force tier/);
  // generate_handoff_prompt's returned instructions require the section too.
  assert.match(extension, /section: never silently drop/);
});

test("skill states the mechanism is known from turn one, independent of handoff origin", () => {
  const skill = readFileSync(
    join(plugin, "skills", "context-handoff", "SKILL.md"),
    "utf8",
  );
  assert.match(skill, /## Every session knows this exists/);
  assert.match(skill, /whether or not it began\s*\n?\s*from a handoff/);
});

test("skill and README agree on the extension-host disconnect recovery", () => {
  const skill = readFileSync(
    join(plugin, "skills", "context-handoff", "SKILL.md"),
    "utf8",
  ).replace(/\s+/g, " ");
  const readme = readFileSync(
    join(plugin, "README.md"),
    "utf8",
  ).replace(/\s+/g, " ");

  for (const source of [skill, readme]) {
    assert.match(source, /Extension disconnected before responding to tool call/);
    assert.match(source, /transport-level failure, not a semantic answer/);
    assert.match(source, /retry the identical call once/);
    assert.match(source, /payload-local CLI/);
    assert.match(source, /does not depend on the extension host/);
    assert.match(source, /Only report "nothing pending" once/);
  }
});

test("skill and README agree on the last-resort write-the-file-yourself fallback", () => {
  const skill = readFileSync(
    join(plugin, "skills", "context-handoff", "SKILL.md"),
    "utf8",
  ).replace(/\s+/g, " ");
  const readme = readFileSync(
    join(plugin, "README.md"),
    "utf8",
  ).replace(/\s+/g, " ");

  for (const source of [skill, readme]) {
    assert.match(source, /Last-resort fallback: write the file yourself/);
    assert.match(source, /no MCP tool call, no extension, no `node`/);
    assert.match(source, /session-state\/<session-id>\//);
    assert.match(source, /\/clear\s*Read <absolute-path-to-file> and resume the objective/);
    assert.match(source, /no automatic pickup, no claim tracking, and no\s*supersession/);
  }
});

test("consume command remains the canonical resume surface", () => {
  const extension = readFileSync(
    join(plugin, "extensions", "context-handoff", "extension.mjs"),
    "utf8",
  );
  const start = extension.indexOf('name: "consume-handoff"');
  const end = extension.indexOf('name: "resume-handoff"', start);
  assert.ok(start >= 0 && end > start);
  const handler = extension.slice(start, end);
  assert.match(handler, /findTaskDeliveryCheckpoint/);
  assert.match(handler, /markDeliveryPromptInjected/);
  assert.match(handler, /task remains owned and the durable delivery checkpoint can retry it/);
  assert.doesNotMatch(handler, /completeHandoffLifecycle|handoff-cutover|retired/);
});
