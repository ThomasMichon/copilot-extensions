import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  CONTINUATION_DIRECTIVE,
} from "../extensions/context-handoff/cutover-seed.mjs";

const plugin = join(dirname(fileURLToPath(import.meta.url)), "..");

test("successor directive drives the parent objective across context windows", () => {
  assert.match(CONTINUATION_DIRECTIVE, /active responsibility within the authority it assigns/);
  assert.match(CONTINUATION_DIRECTIVE, /bounded delegates continue only their inherited scope/);
  assert.match(CONTINUATION_DIRECTIVE, /without waiting for another user nudge/);
  assert.match(CONTINUATION_DIRECTIVE, /Consuming the handoff is setup, not completion/);
  assert.match(CONTINUATION_DIRECTIVE, /begin substantive work immediately after pickup/);
  assert.match(CONTINUATION_DIRECTIVE, /finish the planning needed to act and then execute it/);
  assert.match(
    CONTINUATION_DIRECTIVE,
    /subject to any required safety, review, approval, or confirmation gate/,
  );
  assert.match(CONTINUATION_DIRECTIVE, /hand off again with the same parent objective/);
  assert.match(CONTINUATION_DIRECTIVE, /load that effort before reconstructing intent/);
});

test("handoff guidance requires a forward-looking successor roster", () => {
  const skill = readFileSync(
    join(plugin, "skills", "context-handoff", "SKILL.md"),
    "utf8",
  );
  const template = readFileSync(
    join(plugin, "skills", "context-handoff", "references", "handoff-template.md"),
    "utf8",
  );
  const normalizedSkill = skill.replace(/\s+/g, " ");

  assert.match(
    normalizedSkill,
    /A handoff with no actionable successor work is usually malformed/,
  );
  assert.match(normalizedSkill, /A single session may consume one handoff/);
  assert.match(
    normalizedSkill,
    /Do not wait for another user prompt merely because one phase/,
  );
  assert.match(normalizedSkill, /Consuming the handoff is setup, not completion/);
  assert.match(normalizedSkill, /finish the planning needed to act and then execute it/);
  assert.match(
    normalizedSkill,
    /subject to any required safety, review, approval, or confirmation gate/,
  );

  const headings = [
    "## Standalone Session Continuation",
    "### Original Request",
    "### Continuing Objective",
    "### Progress",
    "### Successor Work Roster",
    "### Completion Gates",
    "### Re-Handoff Instructions",
  ];
  let previous = -1;
  for (const heading of headings) {
    const current = template.indexOf(heading, previous + 1);
    assert.notEqual(current, -1, `${heading} must be present`);
    assert.ok(current > previous, `${heading} must appear in forward order`);
    previous = current;
  }
  assert.match(template, /Do not wait for the user to ask again/);
});

test("effort-backed handoffs link durable intent and carry only the relay delta", () => {
  const skill = readFileSync(
    join(plugin, "skills", "context-handoff", "SKILL.md"),
    "utf8",
  ).replace(/\s+/g, " ");
  const template = readFileSync(
    join(plugin, "skills", "context-handoff", "references", "handoff-template.md"),
    "utf8",
  );
  const extension = readFileSync(
    join(plugin, "extensions", "context-handoff", "extension.mjs"),
    "utf8",
  );

  for (const heading of [
    "## Effort-Backed Session Continuation",
    "### Active Effort",
    "### Next Slice",
    "### Immediate Session Delta",
    "### Completion Gates",
    "### Re-Handoff Instructions",
  ]) {
    assert.notEqual(template.indexOf(heading), -1, `${heading} must be present`);
  }
  assert.match(skill, /effort-focus show --json/);
  assert.match(skill, /active_effort\.active` is `true/);
  assert.match(skill, /do not duplicate its request, plan, or journal/i);
  assert.match(skill, /Use session ramp-up only when the Immediate Session Delta is missing/);
  assert.match(skill, /successfully bound successor is the rightful head/);
  assert.match(skill, /must not continue making competing worktree changes/);
  assert.match(skill, /session-role --json/);
  assert.match(skill, /scope boundary or required safety confirmation stops progress/);
  assert.match(skill, /update landed Plan\/Validation markers and the Journal/);
  assert.match(skill, /failed approaches and non-obvious gotchas in the Journal/);
  assert.match(skill, /Deferred to \\`<tracked objective>\\`/);
  assert.match(skill, /Blocked; transferred to \\`<tracked objective>\\`/);
  assert.match(template, /\*\*Gotchas \/ failed approaches:\*\*/);
  assert.match(extension, /compact effort-backed shape/);
  assert.doesNotMatch(extension, /Compose the FULL handoff markdown/);
  assert.doesNotMatch(extension, /The full continuation context/);
});

test("direct handoff consumption reinforces substantive continuation", () => {
  const core = readFileSync(
    join(plugin, "extensions", "context-handoff", "handoff-core.mjs"),
    "utf8",
  );
  const start = core.indexOf("export function formatConsumeResult");
  const end = core.indexOf("export function findHandoffFile", start);
  assert.notEqual(start, -1);
  assert.ok(end > start);
  const formatter = core.slice(start, end);

  assert.match(formatter, /CONTINUATION_DIRECTIVE/);
  assert.match(formatter, /Handoff consumption is blocked/);
  assert.match(formatter, /Do not treat the missing/);
  assert.match(formatter, /reconstruct a different objective/);
  assert.ok(
    formatter.indexOf("CONTINUATION_DIRECTIVE") < formatter.indexOf("result.payload"),
    "continuation directive must precede the consumed handoff payload",
  );
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
  for (const duplicate of [
    "function makeHandoffMetadata",
    "function saveFileHandoff",
    "function dispatchHandoff",
    "function consumeDispatchHandoffTask",
    "function consumeFileHandoff",
    "function runHandoffCutover",
  ]) {
    assert.doesNotMatch(extension, new RegExp(duplicate));
  }
  assert.match(extension, /name: "consume-handoff"/);
  assert.match(extension, /manualFallbackInstructions\(stored, cutoverSeed\)/);
  assert.match(extension, /HANDOFF_SEED:/);
  assert.match(extension, /HANDOFF_TOKEN:/);
  assert.match(extension, /AGENT_WORKTREES_HANDOFF_TOKEN|handoffToken/);
  assert.doesNotMatch(extension, /reply to the user with ONLY this short paste prompt/);
});

test("non-mux launcher contract is documented but not implemented", () => {
  const readme = readFileSync(join(plugin, "README.md"), "utf8");
  assert.match(readme, /Deferred non-mux launcher contract \(#1632\)/);
  assert.match(readme, /process creation `FILETIME`/);
  assert.match(readme, /`\/proc\/<pid>\/stat` start time/);
  assert.match(readme, /expected predecessor command shape/);
  assert.match(readme, /prompt file/);
  assert.match(
    readme,
    /does not spawn a new terminal when no mux is available/,
  );
});

test("skill carries the exact attributable extension-free CLI fallback", () => {
  const skill = readFileSync(
    join(plugin, "skills", "context-handoff", "SKILL.md"),
    "utf8",
  );
  const setup = readFileSync(
    join(plugin, "skills", "context-handoff-setup", "SKILL.md"),
    "utf8",
  );
  assert.match(skill, /CH_ROOT="\$\{COPILOT_PLUGIN_ROOT:-\}"/);
  assert.match(
    skill,
    /\.copilot\/installed-plugins\/copilot-extensions\/context-handoff/,
  );
  assert.match(
    skill,
    /context-handoff\|https:\/\/github\.com\/ThomasMichon\/copilot-extensions/,
  );
  assert.match(
    skill,
    /CH="\$CH_ROOT\/extensions\/context-handoff\/handoff-cli\.mjs"/,
  );
  for (const command of [
    "facts --json",
    "cutover --title",
    "save --json",
    "continue --seed",
    "consume --task-id",
    "consume --handoff-id",
    "retry --session-id",
  ]) {
    assert.ok(skill.includes(command), `fallback must document ${command}`);
  }
  assert.match(skill, /--handoff-token "<HANDOFF_TOKEN>"/);
  assert.match(
    skill,
    /\$ch = Join-Path \$chRoot 'extensions\\context-handoff\\handoff-cli\.mjs'/,
  );
  assert.match(skill, /node \$ch consume --task-id/);
  assert.match(skill, /node \$ch retry --session-id/);
  assert.doesNotMatch(skill, /command -v handoff-cli/);
  assert.doesNotMatch(
    skill,
    /installed-plugins["'\\\/]\*["'\\\/]context-handoff/,
  );
  assert.match(
    skill,
    /\$manifest\.repository -ne 'https:\/\/github\.com\/ThomasMichon\/copilot-extensions'/,
  );
  assert.match(setup, /No installed runtime, venv, binstub/);
  assert.match(setup, /invoke it with `node`/);
});

test("canonical deferred consume leaves task owned until objective completion", () => {
  const extension = readFileSync(
    join(plugin, "extensions", "context-handoff", "extension.mjs"),
    "utf8",
  );
  const start = extension.indexOf('name: "consume-handoff"');
  const end = extension.indexOf('name: "resume-handoff"', start);
  assert.ok(start >= 0 && end > start);
  const handler = extension.slice(start, end);
  assert.match(handler, /deferredTaskId: task\.id/);
  assert.match(handler, /task remains owned and the durable checkpoint/);
  assert.doesNotMatch(handler, /\["complete", task\.id\]/);
  assert.doesNotMatch(handler, /\["yield", task\.id/);
});
