import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("extension fallback port matches the canonical Python client default", () => {
  const source = readFileSync(join(here, "../extensions/agent-bridge/extension.mjs"), "utf-8");
  assert.match(source, /return `http:\/\/\$\{host\}:9280`;/);
  assert.doesNotMatch(source, /9281/);
});
