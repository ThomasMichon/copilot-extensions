import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { ANNOUNCED_MARKER, firstLoadThisSession } from "../extensions/agent-bridge/announce.mjs";

function root() {
  return mkdtempSync(join(tmpdir(), "ab-announce-"));
}

test("announces once per session across extension-host restarts", () => {
  const stateRoot = root();
  try {
    mkdirSync(join(stateRoot, "sid-1"));
    const now = () => new Date("2026-09-26T06:00:00Z");
    assert.equal(firstLoadThisSession("sid-1", { stateRoot, now }), true);
    assert.equal(firstLoadThisSession("sid-1", { stateRoot, now }), false);
    assert.equal(firstLoadThisSession("sid-1", { stateRoot, now }), false);
    assert.equal(readFileSync(join(stateRoot, "sid-1", ANNOUNCED_MARKER), "utf-8"),
      "2026-09-26T06:00:00.000Z");
    // A different session announces for itself.
    mkdirSync(join(stateRoot, "sid-2"));
    assert.equal(firstLoadThisSession("sid-2", { stateRoot }), true);
  } finally {
    rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("announces when it cannot remember", () => {
  const stateRoot = root();
  try {
    assert.equal(firstLoadThisSession(null, { stateRoot }), true);
    // No session-state dir: never creates one, and keeps announcing.
    assert.equal(firstLoadThisSession("missing", { stateRoot }), true);
    assert.equal(firstLoadThisSession("missing", { stateRoot }), true);
  } finally {
    rmSync(stateRoot, { recursive: true, force: true });
  }
});
