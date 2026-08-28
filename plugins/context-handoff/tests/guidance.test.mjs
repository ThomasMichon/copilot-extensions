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
  assert.match(CONTINUATION_DIRECTIVE, /active responsibility for the original objective/);
  assert.match(CONTINUATION_DIRECTIVE, /without waiting for another user nudge/);
  assert.match(CONTINUATION_DIRECTIVE, /hand off again with the same parent objective/);
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

  const headings = [
    "### Original Request",
    "### Continuing Objective",
    "### Progress",
    "### Successor Work Roster",
    "### Completion Gates",
    "### Re-Handoff Instructions",
  ];
  let previous = -1;
  for (const heading of headings) {
    const current = template.indexOf(heading);
    assert.notEqual(current, -1, `${heading} must be present`);
    assert.ok(current > previous, `${heading} must appear in forward order`);
    previous = current;
  }
  assert.match(template, /Do not wait for the user to ask again/);
});
