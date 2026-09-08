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
  assert.match(CONTINUATION_DIRECTIVE, /load that effort before reconstructing intent/);
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

test("payload-local fallback documents trigger and consume, not cutover", () => {
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
  ]) {
    assert.ok(skill.includes(command), `fallback must document ${command}`);
  }
  assert.match(setup, /No installed runtime, venv, binstub/);
  assert.match(setup, /invoke it with `node`/);
  assert.doesNotMatch(skill, /\bcontinue --seed\b/);
  assert.doesNotMatch(skill, /\bretry --session-id\b/);
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
